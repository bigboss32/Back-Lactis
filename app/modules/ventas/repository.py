from sqlalchemy import func, select

from app.common.repository import BaseRepository
from app.modules.clientes.models import Cliente
from app.modules.ventas.models import Pago, Venta, VentaDetalle


class VentaRepository(BaseRepository[Venta]):
    model = Venta
    default_order_by = "fecha"

    def siguiente_numero(self) -> int:
        stmt = select(func.coalesce(func.max(Venta.numero), 0)).where(
            Venta.empresa_id == self.empresa_id
        )
        return (self.db.scalar(stmt) or 0) + 1

    def cartera_por_cliente(self) -> list:
        stmt = (
            select(
                Venta.cliente_id,
                Cliente.nombre,
                func.count(Venta.id).label("ventas_pendientes"),
                func.sum(Venta.total).label("total_facturado"),
                func.sum(Venta.pagado).label("total_pagado"),
                func.sum(Venta.total - Venta.pagado).label("saldo"),
            )
            .join(Cliente, Cliente.id == Venta.cliente_id)
            .where(
                Venta.deleted_at.is_(None),
                Venta.empresa_id == self.empresa_id,
                Venta.estado.in_(["pendiente", "parcial"]),
            )
            .group_by(Venta.cliente_id, Cliente.nombre)
            .order_by(func.sum(Venta.total - Venta.pagado).desc())
        )
        return list(self.db.execute(stmt).all())


    def eventos_para_lotes(self) -> list[tuple]:
        """Renglones de venta de QUESO TERMINADO, en orden cronológico.

        Solo los productos que tienen tipo de queso: son los que salen de una
        producción. Los demás renglones (insumos, empaques que se revendan) no
        vienen de un lote de producción y no se pueden costear con esta cadena.

        El precio por kilo es el `precio_unitario` del renglón, que en un producto
        que se mide en kilos es el precio por kilo. Se manda tal cual y no se
        recalcula dividiendo, para que en pantalla coincida con la factura.

        Devuelve (fecha, created_at, cliente, tipo_queso_id, producto, cantidad,
        precio_unitario, total, gasto_monto de la venta, id de la venta).

        El flete viene de la VENTA (es del despacho completo, no del renglón), y se
        devuelve también el id para poder sumar los kilos de esa venta y repartirlo
        entre sus renglones a prorrata. Si se le cargara entero a cada renglón, una
        venta de tres productos multiplicaría el flete por tres.

        Los kilos totales se suman en Python y no con una subconsulta: la subconsulta
        se auto-correlacionaba contra el propio VentaDetalle del SELECT de afuera y
        quedaba sin FROM.
        """
        from app.modules.inventario.models import Producto

        return list(
            self.db.execute(
                select(
                    Venta.fecha,
                    VentaDetalle.created_at,
                    Cliente.nombre,
                    Producto.tipo_queso_id,
                    Producto.nombre,
                    VentaDetalle.cantidad,
                    VentaDetalle.precio_unitario,
                    VentaDetalle.total,
                    Venta.gasto_monto,
                    Venta.id,
                )
                .join(Venta, Venta.id == VentaDetalle.venta_id)
                .join(Cliente, Cliente.id == Venta.cliente_id)
                .join(Producto, Producto.id == VentaDetalle.producto_id)
                .where(
                    Venta.empresa_id == self.empresa_id,
                    Venta.deleted_at.is_(None),
                    Venta.estado != "anulada",
                    VentaDetalle.deleted_at.is_(None),
                    Producto.tipo_queso_id.is_not(None),
                )
                .order_by(Venta.fecha, VentaDetalle.created_at)
            ).all()
        )

class PagoRepository(BaseRepository[Pago]):
    model = Pago
    default_order_by = "fecha"
