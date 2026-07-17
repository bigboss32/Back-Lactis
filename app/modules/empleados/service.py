from decimal import Decimal
from typing import Any

from app.common.service import BaseService
from app.core.exceptions import BusinessError, ConflictError
from app.modules.empleados.models import Empleado, PagoEmpleado
from app.modules.empleados.repository import EmpleadoRepository, PagoEmpleadoRepository

CERO = Decimal("0")


class EmpleadoService(BaseService[Empleado]):
    repository_cls = EmpleadoRepository
    modulo = "empleados"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(Empleado.documento == data["documento"]):
            raise ConflictError(f"Ya existe un empleado con documento {data['documento']}")

    def validar_actualizar(self, obj: Empleado, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(
            Empleado.documento == data["documento"], exclude_id=obj.id
        ):
            raise ConflictError(f"Ya existe un empleado con documento {data['documento']}")


class PagoEmpleadoService(BaseService[PagoEmpleado]):
    repository_cls = PagoEmpleadoRepository
    modulo = "empleados"

    def crear(self, payload: Any) -> PagoEmpleado:
        from app.modules.liquidaciones.repository import AnticipoRepository

        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        empleado = EmpleadoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["empleado_id"])

        valor_dia = data.get("valor_dia")
        if valor_dia is None:
            valor_dia = empleado.valor_dia
        if not valor_dia or Decimal(valor_dia) <= CERO:
            raise BusinessError(
                "El empleado no tiene un valor por día. Indícalo en el pago o en la ficha del empleado."
            )

        valor_dia = Decimal(valor_dia)
        dias = Decimal(data["dias_trabajados"])
        bruto = (dias * valor_dia).quantize(Decimal("0.01"))

        # Descuenta los anticipos pendientes del empleado (los que quepan enteros
        # dentro del pago). Los que no quepan quedan para el siguiente pago.
        pendientes = AnticipoRepository(self.db, self.ctx.empresa_id).pendientes_empleado(
            data["empleado_id"], data["fecha"]
        )
        descontado = CERO
        aplicados = []
        for anticipo in pendientes:
            if descontado + anticipo.valor <= bruto:
                descontado += anticipo.valor
                aplicados.append(anticipo)

        data["valor_dia"] = valor_dia
        data["anticipos"] = descontado
        data["total"] = bruto - descontado
        pago = super().crear(data)
        for anticipo in aplicados:
            anticipo.pago_empleado_id = pago.id
        if aplicados:
            self.db.flush()
        return pago
