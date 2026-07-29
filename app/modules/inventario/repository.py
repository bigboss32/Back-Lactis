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


    def movimientos_de_queso_sin_produccion(self) -> list[tuple]:
        """Movimientos de QUESO TERMINADO que NO los creó una producción.

        Son las existencias que el usuario cargó a mano: el caso normal al empezar
        a usar el sistema, cuando ya había queso hecho. Traen su `costo_unitario`,
        así que se pueden costear de verdad y no hay que inventarles un precio.

        Se reconocen por lo que NO son: la producción marca su entrada con
        "Producción #xxxxxxxx" (ver ProduccionService._entrada_inventario), y las
        ventas marcan sus salidas con "venta #N". Contar la entrada de una
        producción aquí DUPLICARÍA sus kilos, porque ya entra por su propio lado.

        Se devuelven las dos direcciones, y el signo lo pone quien llame:
        - entrada, o ajuste con cantidad positiva: suma queso a la bodega
        - ajuste con cantidad negativa: lo saca (se dañó, se corrigió un sobrante)
        La salida por venta NO se devuelve: esa ya la procesa la cadena de ventas.

        Devuelve (fecha, created_at, tipo_queso_id, tipo_queso, tipo_movimiento,
        cantidad, costo_unitario, referencia).
        """
        from app.modules.produccion.models import TipoQueso

        return list(
            self.db.execute(
                select(
                    MovimientoInventario.fecha,
                    MovimientoInventario.created_at,
                    Producto.tipo_queso_id,
                    TipoQueso.nombre,
                    MovimientoInventario.tipo,
                    MovimientoInventario.cantidad,
                    MovimientoInventario.costo_unitario,
                    MovimientoInventario.referencia,
                )
                .join(Producto, Producto.id == MovimientoInventario.producto_id)
                .join(TipoQueso, TipoQueso.id == Producto.tipo_queso_id)
                .where(
                    MovimientoInventario.empresa_id == self.empresa_id,
                    MovimientoInventario.deleted_at.is_(None),
                    MovimientoInventario.estado == "activo",
                    Producto.deleted_at.is_(None),
                    Producto.tipo_queso_id.is_not(None),
                    MovimientoInventario.tipo.in_(["entrada", "ajuste"]),
                    # Ni las entradas de producción (ya entran por su lado) ni nada
                    # que venga marcado como venta.
                    func.coalesce(MovimientoInventario.referencia, "").not_like("Producción #%"),
                    func.coalesce(MovimientoInventario.referencia, "").not_like("%venta #%"),
                )
                .order_by(MovimientoInventario.fecha, MovimientoInventario.created_at)
            ).all()
        )
class MovimientoInventarioRepository(BaseRepository[MovimientoInventario]):
    model = MovimientoInventario
    default_order_by = "fecha"
