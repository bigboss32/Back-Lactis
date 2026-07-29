import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import lazyload

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, NotFoundError
from app.core.pagination import PageParams
from app.modules.clientes.repository import ClienteRepository
from app.modules.empresas.models import Empresa
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
from app.utils.export import pesos

CERO = Decimal("0")


def _gasto_de_despacho(por_kilo: Decimal, kilos: Decimal) -> Decimal:
    """Lo que cuesta llevar el despacho: el flete por kilo por los kilos que van.

    Se calcula sobre los kilos de TODOS los renglones de la venta, porque el flete
    se paga por el peso que sube al camión y no por producto. Igual que en las
    ventas de reventa.

    NO se le suma al total que paga el cliente: es un costo de la quesera, y es lo
    que hace que el kilo puesto en destino valga más que el kilo en la planta.
    """
    return (Decimal(por_kilo or 0) * Decimal(kilos or 0)).quantize(Decimal("0.01"))


class VentaService(BaseService[Venta]):
    repository_cls = VentaRepository
    modulo = "ventas"

    def crear(self, payload: Any) -> Venta:
        data = payload.model_dump(exclude_unset=True)
        detalles_data = data.pop("detalles")
        descontar_inventario = data.pop("descontar_inventario", True)

        # Serializa la numeración por empresa: evita números de venta duplicados
        # (y el error 500 del índice único) cuando dos ventas se crean a la vez.
        self.db.execute(
            select(Empresa.id).where(Empresa.id == self.ctx.empresa_id).with_for_update()
        )
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

        # El flete del despacho: sale de los kilos que van, no del total en plata.
        kilos_despachados = sum((Decimal(d["cantidad"]) for d in detalles_data), CERO)
        data["gasto_monto"] = _gasto_de_despacho(
            data.get("gasto_por_kilo") or CERO, kilos_despachados
        )

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

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Venta:
        venta = self.repo.get_or_fail(entity_id)
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("No se puede editar una venta anulada")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        antes = serialize_entity(venta)
        detalles_data = data.pop("detalles", None)

        # Cambiar productos o descuento afecta importes: solo se permite si la
        # venta aún no tiene pagos (si los tuviera, hay que anular y rehacer).
        afecta_importes = detalles_data is not None or "descuento" in data
        if afecta_importes and venta.pagado > CERO:
            raise BusinessError(
                "No se puede cambiar los productos o el descuento de una venta con pagos; "
                "anúlela y créela de nuevo"
            )

        if "cliente_id" in data:
            ClienteRepository(self.db, self.ctx.empresa_id).get_or_fail(data["cliente_id"])

        if detalles_data is not None:
            productos_repo = ProductoRepository(self.db, self.ctx.empresa_id)
            # Reintegra el inventario de las líneas actuales (entrada) para dejar el
            # stock como estaba antes de la venta y poder validar/descontar las nuevas.
            for detalle in venta.detalles:
                self.db.add(
                    MovimientoInventario(
                        empresa_id=self.ctx.empresa_id,
                        producto_id=detalle.producto_id,
                        fecha=date.today(),
                        tipo="entrada",
                        cantidad=detalle.cantidad,
                        costo_unitario=detalle.precio_unitario,
                        referencia=f"edición venta #{venta.numero}",
                        created_by=self.ctx.user_id,
                    )
                )
            self.db.flush()

            nuevos = []
            subtotal = CERO
            for d in detalles_data:
                producto = productos_repo.get_or_fail(d["producto_id"])
                total_linea = (Decimal(d["cantidad"]) * Decimal(d["precio_unitario"])).quantize(
                    Decimal("0.01")
                )
                subtotal += total_linea
                nuevos.append(
                    VentaDetalle(
                        producto_id=producto.id,
                        descripcion=d.get("descripcion") or producto.nombre,
                        cantidad=d["cantidad"],
                        precio_unitario=d["precio_unitario"],
                        total=total_linea,
                    )
                )
                stock = productos_repo.stock_de(producto.id)
                if stock < Decimal(d["cantidad"]):
                    raise BusinessError(
                        f"Stock insuficiente de '{producto.nombre}': disponible {stock}"
                    )
            venta.detalles = nuevos
            venta.subtotal = subtotal
            self.db.flush()
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

        if "descuento" in data:
            venta.descuento = Decimal(data["descuento"])

        # El flete se recalcula si cambió su valor por kilo O si cambiaron los kilos
        # despachados: si solo se mirara el campo, cambiar los renglones dejaría el
        # monto viejo y el kilo puesto en destino saldría mal.
        if "gasto_concepto" in data:
            venta.gasto_concepto = data["gasto_concepto"]
        if "gasto_por_kilo" in data:
            venta.gasto_por_kilo = Decimal(data["gasto_por_kilo"] or CERO)
        if "gasto_por_kilo" in data or detalles_data is not None:
            kilos_despachados = sum((Decimal(d.cantidad) for d in venta.detalles), CERO)
            venta.gasto_monto = _gasto_de_despacho(venta.gasto_por_kilo, kilos_despachados)

        if afecta_importes:
            if venta.descuento > venta.subtotal:
                raise BusinessError("El descuento no puede superar el subtotal")
            venta.total = (venta.subtotal - venta.descuento).quantize(Decimal("0.01"))
            # Sin pagos (validado arriba): pendiente, o pagada si el total quedó en 0.
            venta.estado = ESTADO_PAGADA if venta.total <= CERO else ESTADO_PENDIENTE

        for campo in ("tipo", "cliente_id", "fecha", "observaciones"):
            if campo in data:
                setattr(venta, campo, data[campo])

        venta.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", venta.id, antes, serialize_entity(venta))
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
        # Bloquea la fila de la venta para evitar sobrepagos en pagos concurrentes.
        venta = self.db.scalars(
            select(Venta)
            .where(
                Venta.id == data["venta_id"],
                Venta.empresa_id == self.ctx.empresa_id,
                Venta.deleted_at.is_(None),
            )
            # Sin el join del cliente (lazy="joined"): Postgres no admite FOR UPDATE
            # sobre el lado exterior de un LEFT JOIN. Aquí solo se bloquea la venta.
            .options(lazyload(Venta.cliente))
            .with_for_update()
        ).first()
        if venta is None:
            raise NotFoundError("Venta no encontrada")
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("No se pueden registrar pagos sobre una venta anulada")
        if venta.estado == ESTADO_PAGADA:
            raise BusinessError("La venta ya está totalmente pagada")
        valor = Decimal(data["valor"])
        if valor > venta.saldo:
            # Las cifras van por pesos(): este mensaje lo lee el dueño en el celular
            # y un Decimal crudo ("150000.00") no se lee como plata colombiana.
            raise BusinessError(
                f"El pago ({pesos(valor)}) supera el saldo pendiente ({pesos(venta.saldo)})"
            )

        pago = super().crear(data)
        venta.pagado += valor
        venta.estado = ESTADO_PAGADA if venta.pagado >= venta.total else ESTADO_PARCIAL
        self.db.flush()
        return pago

    def validar_eliminar(self, obj: Pago) -> None:
        raise BusinessError("Los pagos no se eliminan; anule la venta o registre un ajuste contable")
