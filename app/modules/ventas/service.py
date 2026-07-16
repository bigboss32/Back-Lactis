import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError
from app.core.pagination import PageParams
from app.modules.clientes.repository import ClienteRepository
from app.modules.inventario.repository import ProductoRepository
from app.modules.inventario.models import MovimientoInventario
from app.modules.ventas.models import (
    ESTADO_ANULADA,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    ESTADO_PENDIENTE,
    Pago,
    Venta,
    VentaDetalle,
)
from app.modules.ventas.repository import PagoRepository, VentaRepository
from app.modules.ventas.schemas import CarteraCliente

CERO = Decimal("0")


class VentaService(BaseService[Venta]):
    repository_cls = VentaRepository
    modulo = "ventas"

    def crear(self, payload: Any) -> Venta:
        data = payload.model_dump(exclude_unset=True)
        detalles_data = data.pop("detalles")
        descontar_inventario = data.pop("descontar_inventario", True)

        ClienteRepository(self.db, self.ctx.empresa_id).get_or_fail(data["cliente_id"])
        productos_repo = ProductoRepository(self.db, self.ctx.empresa_id)

        detalles = []
        subtotal = CERO
        for d in detalles_data:
            producto = productos_repo.get_or_fail(d["producto_id"])
            total_linea = (Decimal(d["cantidad"]) * Decimal(d["precio_unitario"])).quantize(
                Decimal("0.01")
            )
            subtotal += total_linea
            detalles.append(
                VentaDetalle(
                    producto_id=producto.id,
                    descripcion=d.get("descripcion") or producto.nombre,
                    cantidad=d["cantidad"],
                    precio_unitario=d["precio_unitario"],
                    total=total_linea,
                )
            )
            if descontar_inventario:
                stock = productos_repo.stock_de(producto.id)
                if stock < Decimal(d["cantidad"]):
                    raise BusinessError(
                        f"Stock insuficiente de '{producto.nombre}': disponible {stock}"
                    )

        descuento = Decimal(data.get("descuento") or CERO)
        if descuento > subtotal:
            raise BusinessError("El descuento no puede superar el subtotal")
        total = (subtotal - descuento).quantize(Decimal("0.01"))

        venta = Venta(
            **data,
            empresa_id=self.ctx.empresa_id,
            numero=self.repo.siguiente_numero(),
            subtotal=subtotal,
            total=total,
            pagado=CERO,
            # Una venta sin saldo (p.ej. descuento del 100%) nace PAGADA para no
            # quedar atrapada como pendiente en la cartera sin poder cerrarse.
            estado=ESTADO_PAGADA if total <= CERO else ESTADO_PENDIENTE,
            created_by=self.ctx.user_id,
            updated_by=self.ctx.user_id,
        )
        venta.detalles = detalles
        self.db.add(venta)
        self.db.flush()

        if descontar_inventario:
            for detalle in venta.detalles:
                self.db.add(
                    MovimientoInventario(
                        empresa_id=self.ctx.empresa_id,
                        producto_id=detalle.producto_id,
                        fecha=venta.fecha,
                        tipo="salida",
                        cantidad=detalle.cantidad,
                        costo_unitario=detalle.precio_unitario,
                        referencia=f"venta #{venta.numero}",
                        created_by=self.ctx.user_id,
                    )
                )
            self.db.flush()

        self._audit("crear", venta.id, None, serialize_entity(venta))
        return venta

    def anular(self, entity_id: uuid.UUID) -> Venta:
        venta = self.repo.get_or_fail(entity_id)
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("La venta ya está anulada")
        if venta.pagado > CERO:
            raise BusinessError("No se puede anular una venta con pagos registrados")
        antes = venta.estado
        venta.estado = ESTADO_ANULADA
        # Reintegrar el inventario descontado
        for detalle in venta.detalles:
            self.db.add(
                MovimientoInventario(
                    empresa_id=self.ctx.empresa_id,
                    producto_id=detalle.producto_id,
                    fecha=date.today(),
                    tipo="entrada",
                    cantidad=detalle.cantidad,
                    costo_unitario=detalle.precio_unitario,
                    referencia=f"anulación venta #{venta.numero}",
                    created_by=self.ctx.user_id,
                )
            )
        self.db.flush()
        self._audit("editar", venta.id, {"estado": antes}, {"estado": ESTADO_ANULADA})
        return venta

    def listar_filtrado(
        self,
        params: PageParams,
        *,
        cliente_id: uuid.UUID | None = None,
        tipo: str | None = None,
        estado: str | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> tuple[list[Venta], int]:
        extra = []
        if desde:
            extra.append(Venta.fecha >= desde)
        if hasta:
            extra.append(Venta.fecha <= hasta)
        return self.repo.list_paginated(
            params, estado=estado, filters={"cliente_id": cliente_id, "tipo": tipo}, extra_criteria=extra
        )

    def cartera(self) -> list[CarteraCliente]:
        return [
            CarteraCliente(
                cliente_id=fila.cliente_id,
                cliente_nombre=fila.nombre,
                ventas_pendientes=fila.ventas_pendientes,
                total_facturado=fila.total_facturado or CERO,
                total_pagado=fila.total_pagado or CERO,
                saldo=fila.saldo or CERO,
            )
            for fila in self.repo.cartera_por_cliente()
        ]


class PagoService(BaseService[Pago]):
    repository_cls = PagoRepository
    modulo = "ventas"

    def crear(self, payload: Any) -> Pago:
        data = payload.model_dump(exclude_unset=True)
        venta = VentaRepository(self.db, self.ctx.empresa_id).get_or_fail(data["venta_id"])
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("No se pueden registrar pagos sobre una venta anulada")
        if venta.estado == ESTADO_PAGADA:
            raise BusinessError("La venta ya está totalmente pagada")
        valor = Decimal(data["valor"])
        if valor > venta.saldo:
            raise BusinessError(f"El pago (${valor}) supera el saldo pendiente (${venta.saldo})")

        pago = super().crear(data)
        venta.pagado += valor
        venta.estado = ESTADO_PAGADA if venta.pagado >= venta.total else ESTADO_PARCIAL
        self.db.flush()
        return pago

    def validar_eliminar(self, obj: Pago) -> None:
        raise BusinessError("Los pagos no se eliminan; anule la venta o registre un ajuste contable")
