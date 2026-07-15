import uuid
from decimal import Decimal
from typing import Any

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, ConflictError
from app.modules.caja.models import (
    ESTADO_ABIERTA,
    ESTADO_CERRADA,
    TIPO_INGRESO,
    CajaDiaria,
    MovimientoCaja,
)
from app.modules.caja.repository import CajaRepository, MovimientoCajaRepository

CERO = Decimal("0")


class CajaService(BaseService[CajaDiaria]):
    repository_cls = CajaRepository
    modulo = "caja"

    def abrir(self, payload: Any) -> CajaDiaria:
        data = payload.model_dump(exclude_unset=True)
        if self.repo.caja_del_dia(data["fecha"], data.get("sucursal_id")):
            raise ConflictError("Ya existe una caja para esa fecha y sucursal")
        data["estado"] = ESTADO_ABIERTA
        data["saldo_final"] = data.get("saldo_inicial", CERO)
        return super().crear(data)

    def registrar_movimiento(self, payload: Any) -> MovimientoCaja:
        data = payload.model_dump(exclude_unset=True)
        caja = self.repo.get_or_fail(data["caja_id"])
        if caja.estado != ESTADO_ABIERTA:
            raise BusinessError("La caja está cerrada: no admite más movimientos")

        movimiento = MovimientoCaja(
            **data,
            empresa_id=self.ctx.empresa_id,
            created_by=self.ctx.user_id,
            updated_by=self.ctx.user_id,
        )
        self.db.add(movimiento)

        if movimiento.tipo == TIPO_INGRESO:
            caja.total_ingresos += movimiento.valor
        else:
            caja.total_egresos += movimiento.valor
            if caja.saldo_inicial + caja.total_ingresos - caja.total_egresos < CERO:
                raise BusinessError("El egreso deja la caja en negativo")
        caja.saldo_final = caja.saldo_inicial + caja.total_ingresos - caja.total_egresos
        self.db.flush()
        self._audit("crear", movimiento.id, None, serialize_entity(movimiento))
        return movimiento

    def cerrar(self, entity_id: uuid.UUID, efectivo_contado: Decimal, observaciones: str | None) -> CajaDiaria:
        """Arqueo de caja: compara el efectivo contado contra el saldo esperado."""
        caja = self.repo.get_or_fail(entity_id)
        if caja.estado != ESTADO_ABIERTA:
            raise BusinessError("La caja ya está cerrada")
        antes = serialize_entity(caja)
        caja.efectivo_contado = efectivo_contado
        caja.diferencia = efectivo_contado - caja.saldo_final
        caja.estado = ESTADO_CERRADA
        if observaciones:
            caja.observaciones = observaciones
        caja.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", caja.id, antes, serialize_entity(caja))
        return caja
