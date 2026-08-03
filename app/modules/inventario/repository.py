import uuid
from datetime import date
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
        "Producción #xxxxxxxx" (ver ProduccionService._entrada_inventario), las
        ventas marcan sus salidas con "venta #N", y el cierre de un ciclo de
        despacho marca sus ajustes de merma con "Merma ciclo #xxxxxxxx". Contar
        cualquiera de esos aquí DUPLICARÍA sus kilos, porque ya entran por su
        propio lado.

        El de la merma del ciclo merece una línea aparte: ese ajuste viene CON
        DUEÑO. La cadena lo lee de `ciclos_despacho_lotes`, que dice a qué tanda
        se le carga cada kilo, y por eso no puede volver a entrar por aquí como
        un ajuste suelto: se le restaría dos veces a la bodega y dos veces a la
        utilidad.

        Se devuelven las dos direcciones, y el signo lo pone quien llame:
        - entrada, o ajuste con cantidad positiva: suma queso a la bodega
        - ajuste con cantidad negativa: lo saca (se dañó, se corrigió un sobrante)
        La salida por venta NO se devuelve: esa ya la procesa la cadena de ventas.

        Devuelve (fecha, created_at, tipo_queso_id, tipo_queso, tipo_movimiento,
        cantidad, costo_unitario, referencia).
        """
        from app.modules.produccion.models import REFERENCIA_MERMA_CICLO, TipoQueso

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
                    # Ni las entradas de producción (ya entran por su lado), ni nada
                    # que venga marcado como venta, ni la merma de un cierre de
                    # ciclo (esa entra con dueño desde ciclos_despacho_lotes).
                    func.coalesce(MovimientoInventario.referencia, "").not_like("Producción #%"),
                    func.coalesce(MovimientoInventario.referencia, "").not_like("%venta #%"),
                    func.coalesce(MovimientoInventario.referencia, "").not_like(
                        f"{REFERENCIA_MERMA_CICLO} #%"
                    ),
                )
                .order_by(MovimientoInventario.fecha, MovimientoInventario.created_at)
            ).all()
        )
    def ajustes_de_queso_del_rango(self, desde: date, hasta: date) -> list[tuple]:
        """Ajustes de inventario de QUESO hechos a mano entre dos fechas.

        Es la consulta que EVITA COBRAR LA MERMA DOS VECES. Si el dueño ya anotó
        a mano "se perdieron 3 kg" dentro del ciclo, esos kilos ya salieron de la
        bodega y su costo ya se le restó al lote. La merma del cierre tiene que
        descontarlos, o si no el sistema volvería a dar por perdido un queso que
        ya se dio por perdido.

        No entran los ajustes que creó un cierre de ciclo (los de "Merma ciclo
        #"): esos no son una anotación del dueño, son lo que este mismo
        mecanismo escribió, y restarlos volvería a abrir el hueco que taparon.

        Devuelve (tipo_queso_id, tipo_queso, kilos_hacia_abajo, kilos_hacia_
        arriba), con los dos totales en positivo y ya agrupados por tipo.
        """
        from app.modules.produccion.models import REFERENCIA_MERMA_CICLO, TipoQueso

        # Los ajustes negativos sacan queso; los positivos y las entradas a mano lo
        # meten. Se devuelven separados porque significan cosas distintas: el de
        # abajo se RESTA de la merma (ya se contó) y el de arriba solo se AVISA
        # (es queso que entró sin ser una tanda, y puede desfigurar la cuenta).
        hacia_abajo = func.sum(
            case(
                (MovimientoInventario.cantidad < 0, -MovimientoInventario.cantidad),
                else_=0,
            )
        )
        hacia_arriba = func.sum(
            case(
                (MovimientoInventario.cantidad > 0, MovimientoInventario.cantidad),
                else_=0,
            )
        )
        return list(
            self.db.execute(
                select(
                    Producto.tipo_queso_id,
                    TipoQueso.nombre,
                    hacia_abajo,
                    hacia_arriba,
                )
                .join(Producto, Producto.id == MovimientoInventario.producto_id)
                .join(TipoQueso, TipoQueso.id == Producto.tipo_queso_id)
                .where(
                    MovimientoInventario.empresa_id == self.empresa_id,
                    MovimientoInventario.deleted_at.is_(None),
                    MovimientoInventario.estado == "activo",
                    Producto.deleted_at.is_(None),
                    Producto.tipo_queso_id.is_not(None),
                    MovimientoInventario.fecha >= desde,
                    MovimientoInventario.fecha <= hasta,
                    MovimientoInventario.tipo.in_(["entrada", "ajuste"]),
                    func.coalesce(MovimientoInventario.referencia, "").not_like("Producción #%"),
                    func.coalesce(MovimientoInventario.referencia, "").not_like("%venta #%"),
                    func.coalesce(MovimientoInventario.referencia, "").not_like(
                        f"{REFERENCIA_MERMA_CICLO} #%"
                    ),
                )
                .group_by(Producto.tipo_queso_id, TipoQueso.nombre)
            ).all()
        )


class MovimientoInventarioRepository(BaseRepository[MovimientoInventario]):
    model = MovimientoInventario
    default_order_by = "fecha"
