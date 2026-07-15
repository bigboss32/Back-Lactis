from datetime import date

from sqlalchemy import func, select

from app.common.repository import BaseRepository
from app.modules.gastos.models import CategoriaGasto, Gasto


class CategoriaGastoRepository(BaseRepository[CategoriaGasto]):
    model = CategoriaGasto
    search_fields = ("nombre",)


class GastoRepository(BaseRepository[Gasto]):
    model = Gasto
    search_fields = ("concepto", "proveedor", "numero_factura")
    default_order_by = "fecha"

    def total_por_categoria(self, desde: date, hasta: date) -> list:
        stmt = (
            select(
                CategoriaGasto.nombre,
                func.sum(Gasto.valor).label("total"),
                func.count(Gasto.id).label("cantidad"),
            )
            .join(CategoriaGasto, CategoriaGasto.id == Gasto.categoria_id)
            .where(
                Gasto.deleted_at.is_(None),
                Gasto.estado == "activo",
                Gasto.empresa_id == self.empresa_id,
                Gasto.fecha >= desde,
                Gasto.fecha <= hasta,
            )
            .group_by(CategoriaGasto.nombre)
            .order_by(func.sum(Gasto.valor).desc())
        )
        return list(self.db.execute(stmt).all())
