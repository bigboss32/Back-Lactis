from sqlalchemy import func, select

from app.common.repository import BaseRepository
from app.modules.clientes.models import Cliente
from app.modules.ventas.models import Pago, Venta


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


class PagoRepository(BaseRepository[Pago]):
    model = Pago
    default_order_by = "fecha"
