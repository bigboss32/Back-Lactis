import uuid
from decimal import Decimal

from sqlalchemy import case, func, select

from app.common.repository import BaseRepository
from app.modules.bancos.models import CuentaBancaria, MovimientoBancario


class CuentaBancariaRepository(BaseRepository[CuentaBancaria]):
    model = CuentaBancaria
    search_fields = ("banco", "numero_cuenta", "titular")

    def saldo_de(self, cuenta: CuentaBancaria) -> Decimal:
        neto = self.db.scalar(
            select(
                func.sum(
                    case(
                        (MovimientoBancario.tipo == "ingreso", MovimientoBancario.valor),
                        else_=-MovimientoBancario.valor,
                    )
                )
            ).where(
                MovimientoBancario.cuenta_id == cuenta.id,
                MovimientoBancario.deleted_at.is_(None),
                MovimientoBancario.estado == "activo",
            )
        )
        return cuenta.saldo_inicial + (neto or Decimal("0"))


class MovimientoBancarioRepository(BaseRepository[MovimientoBancario]):
    model = MovimientoBancario
    search_fields = ("concepto", "referencia")
    default_order_by = "fecha"

    def por_ids(self, ids: list[uuid.UUID]) -> list[MovimientoBancario]:
        stmt = self.base_query().where(MovimientoBancario.id.in_(ids))
        return list(self.db.scalars(stmt).all())
