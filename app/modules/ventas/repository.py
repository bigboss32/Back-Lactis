from datetime import date

from sqlalchemy import func, select

from app.common.repository import BaseRepository
from app.modules.clientes.models import Cliente
from app.modules.ventas.models import (
    Pago,
    PagoConductor,
    Venta,
    VentaDetalle,
    VentaTramoFlete,
)


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

    # ------------------------------------------- flete por tramos / conductores
    def _kilos_por_venta(self):
        """Subconsulta: kilos despachados de cada venta (suma de sus renglones).

        Hace falta para mostrar el "100 kg × 400 = 40.000" de cada tramo. Va como
        subconsulta agrupada y no como un JOIN directo contra los renglones: con
        el JOIN, una venta de tres productos repetiría su tramo tres veces y lo
        que se le debe al conductor saldría multiplicado por tres.
        """
        return (
            select(
                VentaDetalle.venta_id.label("venta_id"),
                func.sum(VentaDetalle.cantidad).label("kilos"),
            )
            .where(VentaDetalle.deleted_at.is_(None))
            .group_by(VentaDetalle.venta_id)
            .subquery()
        )

    def tramos_de_conductores(
        self, *, desde: date | None = None, hasta: date | None = None
    ) -> list[tuple]:
        """Los tramos con conductor, de las ventas VIVAS de esta empresa.

        Devuelve (conductor_clave, conductor, venta_id, numero, fecha, cliente,
        origen, destino, valor_por_kilo, valor_total, kilos del despacho).

        Se excluyen las ANULADAS: al anular se reintegra el inventario, o sea que
        ese despacho no salió, y no se le puede seguir debiendo el viaje a nadie.
        Es el mismo criterio que usa `eventos_para_lotes`, para que el flete que
        ve el conductor y el que resta la utilidad sean el mismo flete.

        Los tramos sin conductor (los fletes viejos que se migraron) no entran:
        no hay a quién pagarle.
        """
        kilos = self._kilos_por_venta()
        stmt = (
            select(
                VentaTramoFlete.conductor_clave,
                VentaTramoFlete.conductor,
                Venta.id,
                Venta.numero,
                Venta.fecha,
                Cliente.nombre,
                VentaTramoFlete.origen,
                VentaTramoFlete.destino,
                VentaTramoFlete.valor_por_kilo,
                VentaTramoFlete.valor_total,
                func.coalesce(kilos.c.kilos, 0),
            )
            .join(Venta, Venta.id == VentaTramoFlete.venta_id)
            .join(Cliente, Cliente.id == Venta.cliente_id)
            .outerjoin(kilos, kilos.c.venta_id == Venta.id)
            .where(
                Venta.empresa_id == self.empresa_id,
                Venta.deleted_at.is_(None),
                Venta.estado != "anulada",
                VentaTramoFlete.deleted_at.is_(None),
                VentaTramoFlete.conductor_clave.is_not(None),
            )
            .order_by(Venta.fecha, Venta.numero, VentaTramoFlete.orden)
        )
        if desde:
            stmt = stmt.where(Venta.fecha >= desde)
        if hasta:
            stmt = stmt.where(Venta.fecha <= hasta)
        return list(self.db.execute(stmt).all())

    def nombres_conductores(self) -> list[str]:
        """Nombres de conductor ya usados en tramos de esta empresa.

        Van TODOS, incluidos los de ventas anuladas: sirve para autocompletar y
        para canonizar, y si el nombre desapareciera al anular una venta, el
        próximo despacho lo volvería a escribir de otra forma y se partiría en
        dos personas.
        """
        stmt = (
            select(VentaTramoFlete.conductor)
            .join(Venta, Venta.id == VentaTramoFlete.venta_id)
            .where(
                Venta.empresa_id == self.empresa_id,
                Venta.deleted_at.is_(None),
                VentaTramoFlete.deleted_at.is_(None),
                VentaTramoFlete.conductor.is_not(None),
            )
            .distinct()
        )
        return [nombre for (nombre,) in self.db.execute(stmt).all() if nombre]


class PagoRepository(BaseRepository[Pago]):
    model = Pago
    default_order_by = "fecha"


class PagoConductorRepository(BaseRepository[PagoConductor]):
    model = PagoConductor
    default_order_by = "fecha"
    search_fields = ("conductor",)

    def pagos_de_conductores(
        self, *, desde: date | None = None, hasta: date | None = None
    ) -> list[PagoConductor]:
        """Los pagos hechos a conductores de esta empresa, opcionalmente del
        período. `base_query` ya filtra por empresa_id y deleted_at."""
        stmt = self.base_query()
        if desde:
            stmt = stmt.where(PagoConductor.fecha >= desde)
        if hasta:
            stmt = stmt.where(PagoConductor.fecha <= hasta)
        return list(self.db.scalars(stmt.order_by(PagoConductor.fecha)).all())

    def nombres_conductores(self) -> list[str]:
        """Nombres a los que ya se les ha pagado. Entran en la canonización para
        que un pago no cree una segunda escritura del mismo señor."""
        stmt = select(PagoConductor.conductor).where(
            PagoConductor.empresa_id == self.empresa_id,
            PagoConductor.deleted_at.is_(None),
        ).distinct()
        return [nombre for (nombre,) in self.db.execute(stmt).all() if nombre]
