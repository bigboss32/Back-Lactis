import uuid
from datetime import datetime, timezone
from typing import Any

from app.common.service import BaseService
from app.core.exceptions import BusinessError, ConflictError, NotFoundError
from app.modules.bancos.models import CuentaBancaria, MovimientoBancario
from app.modules.bancos.repository import (
    CuentaBancariaRepository,
    MovimientoBancarioRepository,
)
from app.modules.bancos.schemas import CuentaSaldoRead


class CuentaBancariaService(BaseService[CuentaBancaria]):
    repository_cls = CuentaBancariaRepository
    modulo = "bancos"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(CuentaBancaria.numero_cuenta == data["numero_cuenta"]):
            raise ConflictError(f"Ya existe la cuenta {data['numero_cuenta']}")

    def con_saldo(self, entity_id: uuid.UUID) -> CuentaSaldoRead:
        from app.modules.bancos.schemas import CuentaRead

        cuenta = self.repo.get_or_fail(entity_id)
        base = CuentaRead.model_validate(cuenta).model_dump()
        return CuentaSaldoRead(**base, saldo_actual=self.repo.saldo_de(cuenta))


class MovimientoBancarioService(BaseService[MovimientoBancario]):
    repository_cls = MovimientoBancarioRepository
    modulo = "bancos"

    def crear(self, payload: Any) -> MovimientoBancario:
        data = payload.model_dump(exclude_unset=True)
        CuentaBancariaRepository(self.db, self.ctx.empresa_id).get_or_fail(data["cuenta_id"])
        return super().crear(data)

    def validar_actualizar(self, obj: MovimientoBancario, data: dict[str, Any]) -> None:
        if obj.conciliado:
            raise BusinessError("No se puede modificar un movimiento conciliado")

    def validar_eliminar(self, obj: MovimientoBancario) -> None:
        if obj.conciliado:
            raise BusinessError("No se puede eliminar un movimiento conciliado")

    def conciliar(self, movimiento_ids: list[uuid.UUID]) -> list[MovimientoBancario]:
        movimientos = self.repo.por_ids(movimiento_ids)
        if len(movimientos) != len(set(movimiento_ids)):
            raise NotFoundError("Uno o más movimientos no existen")
        ahora = datetime.now(timezone.utc)
        for m in movimientos:
            if not m.conciliado:
                m.conciliado = True
                m.fecha_conciliacion = ahora
                m.updated_by = self.ctx.user_id
                self._audit("editar", m.id, {"conciliado": False}, {"conciliado": True})
        self.db.flush()
        return movimientos
