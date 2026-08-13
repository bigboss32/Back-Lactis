import uuid
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

    def generar_pdf(self, entity_id: uuid.UUID) -> tuple[bytes, str]:
        import uuid
        from datetime import datetime
        from app.modules.empresas.repository import EmpresaRepository
        from app.modules.liquidaciones.models import Anticipo
        from app.utils.export import build_recibo_empleado_pdf, pesos
        from sqlalchemy import select

        pago = self.repo.get_or_fail(entity_id)
        empresa = EmpresaRepository(self.db).get(self.ctx.empresa_id)
        nombre_empresa = empresa.nombre if empresa else "Quesera"
        nit = empresa.nit if empresa else None
        ubicacion = (
            ", ".join(p for p in [empresa.ciudad, empresa.departamento] if p) or None
            if empresa
            else None
        )

        empleado = pago.empleado
        empleado_nombre = f"{empleado.nombre} {empleado.apellido}".strip() if empleado else "Empleado"
        empleado_documento = empleado.documento if empleado else None
        empleado_cargo = empleado.cargo if empleado else None

        bruto = Decimal(pago.dias_trabajados) * Decimal(pago.valor_dia)

        # Anticipos descontados
        stmt = select(Anticipo).where(Anticipo.pago_empleado_id == pago.id, Anticipo.deleted_at.is_(None))
        anticipos_pago = list(self.db.scalars(stmt).all())
        anticipos_rows = [
            [a.fecha.strftime("%d/%m/%Y"), pesos(a.valor), a.observaciones or "—"]
            for a in anticipos_pago
        ]

        folio = str(pago.id)[:8].upper()
        emitido = datetime.now().strftime("%d/%m/%Y %H:%M")

        pdf = build_recibo_empleado_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            folio=folio,
            emitido=emitido,
            empleado_nombre=empleado_nombre,
            empleado_documento=empleado_documento,
            empleado_cargo=empleado_cargo,
            fecha=pago.fecha.strftime("%d/%m/%Y"),
            periodo=pago.periodo,
            dias_trabajados=str(pago.dias_trabajados),
            valor_dia=pesos(pago.valor_dia),
            valor_bruto=pesos(bruto),
            anticipos_monto=pesos(pago.anticipos),
            total_pagado=pesos(pago.total),
            anticipos_rows=anticipos_rows,
            observaciones=pago.observaciones,
        )
        filename = f"recibo_nomina_{empleado_nombre}_{pago.fecha.isoformat()}.pdf".replace(" ", "_")
        return pdf, filename
