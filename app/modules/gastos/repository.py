import uuid
from datetime import date

from sqlalchemy import func, select

from app.common.repository import BaseRepository
from app.modules.gastos.models import AdjuntoGasto, CategoriaGasto, Gasto


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


class AdjuntoGastoRepository(BaseRepository[AdjuntoGasto]):
    """Las facturas de los gastos.

    Todo pasa por `base_query()` del repositorio genérico, que ya mete
    `empresa_id = <la del contexto>` y `deleted_at IS NULL`. Eso es lo que impide
    que alguien firme el enlace de una factura de otra quesera: la fila
    simplemente no aparece y sale un 404, no un 403 que confirmaría que existe.
    """

    model = AdjuntoGasto
    default_order_by = "created_at"

    def de_gasto(self, gasto_id: uuid.UUID) -> list[AdjuntoGasto]:
        """Las facturas de UN gasto, de la más vieja a la más nueva.

        Ese orden es el que espera quien las subió: primero la que mandó primero.
        Con una factura de varias páginas es la diferencia entre verla en orden y
        verla al revés.
        """
        stmt = (
            self.base_query()
            .where(AdjuntoGasto.gasto_id == gasto_id)
            .order_by(AdjuntoGasto.created_at)
        )
        return list(self.db.scalars(stmt).all())

    def contar_de(self, gasto_id: uuid.UUID) -> int:
        """Cuántas facturas vigentes tiene el gasto (para el tope por documento)."""
        stmt = self.base_query().where(AdjuntoGasto.gasto_id == gasto_id)
        return int(self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
