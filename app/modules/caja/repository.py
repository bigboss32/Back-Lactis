import uuid
from datetime import date

from app.common.repository import BaseRepository
from app.modules.caja.models import CajaDiaria, MovimientoCaja


class CajaRepository(BaseRepository[CajaDiaria]):
    model = CajaDiaria
    default_order_by = "fecha"

    def caja_del_dia(self, fecha: date, sucursal_id: uuid.UUID | None) -> CajaDiaria | None:
        stmt = self.base_query().where(CajaDiaria.fecha == fecha)
        if sucursal_id:
            stmt = stmt.where(CajaDiaria.sucursal_id == sucursal_id)
        else:
            stmt = stmt.where(CajaDiaria.sucursal_id.is_(None))
        return self.db.scalars(stmt).first()


class MovimientoCajaRepository(BaseRepository[MovimientoCaja]):
    model = MovimientoCaja
