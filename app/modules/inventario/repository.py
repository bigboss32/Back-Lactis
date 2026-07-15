import uuid
from decimal import Decimal

from sqlalchemy import case, func, select

from app.common.repository import BaseRepository
from app.modules.inventario.models import MovimientoInventario, Producto

# entrada suma, salida resta, ajuste aplica su signo tal cual
STOCK_EXPR = func.sum(
    case(
        (MovimientoInventario.tipo == "entrada", MovimientoInventario.cantidad),
        (MovimientoInventario.tipo == "salida", -MovimientoInventario.cantidad),
        else_=MovimientoInventario.cantidad,
    )
)


class ProductoRepository(BaseRepository[Producto]):
    model = Producto
    search_fields = ("nombre", "categoria")

    def stock_de(self, producto_id: uuid.UUID) -> Decimal:
        stmt = select(STOCK_EXPR).where(
            MovimientoInventario.producto_id == producto_id,
            MovimientoInventario.deleted_at.is_(None),
            MovimientoInventario.estado == "activo",
        )
        return self.db.scalar(stmt) or Decimal("0")

    def stock_por_producto(self) -> dict[uuid.UUID, Decimal]:
        stmt = (
            select(MovimientoInventario.producto_id, STOCK_EXPR)
            .where(
                MovimientoInventario.deleted_at.is_(None),
                MovimientoInventario.estado == "activo",
                MovimientoInventario.empresa_id == self.empresa_id,
            )
            .group_by(MovimientoInventario.producto_id)
        )
        return {row[0]: row[1] or Decimal("0") for row in self.db.execute(stmt).all()}


class MovimientoInventarioRepository(BaseRepository[MovimientoInventario]):
    model = MovimientoInventario
    default_order_by = "fecha"
