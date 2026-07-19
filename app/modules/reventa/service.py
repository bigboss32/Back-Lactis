"""Reventa de queso: compras a productores con merma y abonos, ventas a
clientes y resumen de ganancia. Contabilidad separada del libro de la quesera.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError
from app.core.pagination import PageParams
from app.modules.reventa.models import (
    ESTADO_ANULADA,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    ESTADO_PENDIENTE,
    TIPO_VENTA_BORONA,
    TIPO_VENTA_QUESO,
    AbonoCompraQueso,
    AbonoVentaQueso,
    CompraQueso,
    ConversionBorona,
    VentaQueso,
)
from app.modules.reventa.repository import (
    CompraQuesoRepository,
    ConversionBoronaRepository,
    VentaQuesoRepository,
)
from app.modules.reventa.schemas import ResumenReventa

CERO = Decimal("0")
DOS_DECIMALES = Decimal("0.01")


def _estado_pago(valor_total: Decimal, abonado: Decimal) -> str:
    if abonado <= CERO:
        return ESTADO_PENDIENTE
    return ESTADO_PAGADA if abonado >= valor_total else ESTADO_PARCIAL


class CompraQuesoService(BaseService[CompraQueso]):
    repository_cls = CompraQuesoRepository
    modulo = "reventa"

    @staticmethod
    def _calcular(data: dict[str, Any], actual: CompraQueso | None = None) -> dict[str, Any]:
        brutos = Decimal(data.get("kilos_brutos") or (actual.kilos_brutos if actual else CERO))
        precio = Decimal(data.get("precio_kilo") or (actual.precio_kilo if actual else CERO))
        # Ya no hay merma en la compra: se paga por todo lo recibido. La merma
        # real se refleja al vender (se pesa menos). Se guarda merma 0.
        data["merma_kilos"] = CERO
        data["kilos_netos"] = brutos
        data["valor_total"] = (brutos * precio).quantize(DOS_DECIMALES)
        return data

    def crear(self, payload: Any) -> CompraQueso:
        data = self._calcular(payload.model_dump(exclude_unset=True))
        data["estado"] = ESTADO_PENDIENTE
        return super().crear(data)

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> CompraQueso:
        actual = self.repo.get_or_fail(entity_id)
        if actual.estado == ESTADO_ANULADA:
            raise BusinessError("No se puede modificar una compra anulada")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        data = self._calcular(data, actual)
        # Se puede editar aunque tenga abonos (incluida una pagada): se recalcula el
        # estado con los abonos ya registrados y el saldo queda al día.
        data["estado"] = _estado_pago(data["valor_total"], actual.abonado)
        return super().actualizar(entity_id, data)

    def validar_eliminar(self, obj: CompraQueso) -> None:
        if obj.abonado > CERO:
            raise BusinessError("No se puede eliminar una compra con abonos; anúlela")

    def registrar_abono(self, compra_id: uuid.UUID, payload: Any) -> CompraQueso:
        compra = self.repo.get_or_fail(compra_id)
        if compra.estado == ESTADO_ANULADA:
            raise BusinessError("La compra está anulada")
        valor = Decimal(payload.valor)
        if valor > compra.saldo:
            raise BusinessError(f"El abono (${valor:,.0f}) supera el saldo (${compra.saldo:,.0f})")
        self.db.add(
            AbonoCompraQueso(
                compra_id=compra.id,
                fecha=payload.fecha,
                valor=valor,
                observaciones=payload.observaciones,
                created_by=self.ctx.user_id,
            )
        )
        compra.abonado += valor
        compra.estado = _estado_pago(compra.valor_total, compra.abonado)
        compra.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", compra.id, None, {"abono": float(valor), "estado": compra.estado})
        return compra

    def anular(self, compra_id: uuid.UUID) -> CompraQueso:
        compra = self.repo.get_or_fail(compra_id)
        if compra.abonado > CERO:
            raise BusinessError(
                "No se puede anular una compra con abonos registrados"
            )
        antes = compra.estado
        compra.estado = ESTADO_ANULADA
        compra.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", compra.id, {"estado": antes}, {"estado": ESTADO_ANULADA})
        return compra

    def listar_filtrado(
        self, params: PageParams, *, search: str | None, estado: str | None,
        desde: date | None, hasta: date | None,
    ) -> tuple[list[CompraQueso], int]:
        extra = []
        if desde:
            extra.append(CompraQueso.fecha >= desde)
        if hasta:
            extra.append(CompraQueso.fecha <= hasta)
        return self.repo.list_paginated(params, search=search, estado=estado, extra_criteria=extra)


class VentaQuesoService(BaseService[VentaQueso]):
    repository_cls = VentaQuesoRepository
    modulo = "reventa"

    def crear(self, payload: Any) -> VentaQueso:
        data = payload.model_dump(exclude_unset=True)
        de_contado = data.pop("pagada_de_contado", False)
        kilos = Decimal(data["kilos"])
        # No permitir vender más queso o borona del disponible en inventario
        tipo = data.get("tipo", TIPO_VENTA_QUESO)
        if tipo == TIPO_VENTA_BORONA:
            disponible = ReventaResumenService.borona_disponible(self.db, self.ctx)
            if kilos > disponible:
                raise BusinessError(f"Solo hay {disponible} kg de borona disponibles")
        else:
            disponible = ReventaResumenService.queso_disponible(self.db, self.ctx)
            if kilos > disponible:
                raise BusinessError(f"Solo hay {disponible} kg de queso disponibles")
        data["valor_total"] = (kilos * Decimal(data["precio_kilo"])).quantize(DOS_DECIMALES)
        # Gasto de venta por kilo (ej. transporte): el total es por_kilo * kilos.
        por_kilo = Decimal(data.get("gasto_por_kilo") or CERO)
        data["gasto_monto"] = (por_kilo * kilos).quantize(DOS_DECIMALES)
        data["estado"] = ESTADO_PENDIENTE
        if de_contado:
            data["abonado"] = data["valor_total"]
            data["estado"] = ESTADO_PAGADA
        venta = super().crear(data)
        if de_contado:
            self.db.add(
                AbonoVentaQueso(
                    venta_id=venta.id, fecha=venta.fecha, valor=venta.valor_total,
                    observaciones="Pago de contado", created_by=self.ctx.user_id,
                )
            )
            self.db.flush()
        return venta

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> VentaQueso:
        actual = self.repo.get_or_fail(entity_id)
        if actual.estado == ESTADO_ANULADA:
            raise BusinessError("No se puede modificar una venta anulada")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        kilos = Decimal(data.get("kilos") or actual.kilos)
        precio = Decimal(data.get("precio_kilo") or actual.precio_kilo)
        data["valor_total"] = (kilos * precio).quantize(DOS_DECIMALES)
        # Recalcula el gasto total (por_kilo * kilos) si cambió cualquiera de los dos.
        por_kilo = Decimal(
            data["gasto_por_kilo"]
            if data.get("gasto_por_kilo") is not None
            else actual.gasto_por_kilo
        )
        data["gasto_monto"] = (por_kilo * kilos).quantize(DOS_DECIMALES)
        # Se puede editar aunque tenga abonos (incluida una pagada): se recalcula el estado.
        data["estado"] = _estado_pago(data["valor_total"], actual.abonado)
        return super().actualizar(entity_id, data)

    def validar_eliminar(self, obj: VentaQueso) -> None:
        if obj.abonado > CERO:
            raise BusinessError("No se puede eliminar una venta con abonos; anúlela")

    def registrar_abono(self, venta_id: uuid.UUID, payload: Any) -> VentaQueso:
        venta = self.repo.get_or_fail(venta_id)
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("La venta está anulada")
        valor = Decimal(payload.valor)
        if valor > venta.saldo:
            raise BusinessError(f"El abono (${valor:,.0f}) supera el saldo (${venta.saldo:,.0f})")
        self.db.add(
            AbonoVentaQueso(
                venta_id=venta.id, fecha=payload.fecha, valor=valor,
                observaciones=payload.observaciones, created_by=self.ctx.user_id,
            )
        )
        venta.abonado += valor
        venta.estado = _estado_pago(venta.valor_total, venta.abonado)
        venta.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", venta.id, None, {"abono": float(valor), "estado": venta.estado})
        return venta

    def anular(self, venta_id: uuid.UUID) -> VentaQueso:
        venta = self.repo.get_or_fail(venta_id)
        if venta.abonado > CERO:
            raise BusinessError(
                "No se puede anular una venta con abonos registrados"
            )
        antes = venta.estado
        venta.estado = ESTADO_ANULADA
        venta.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", venta.id, {"estado": antes}, {"estado": ESTADO_ANULADA})
        return venta

    def listar_filtrado(
        self, params: PageParams, *, search: str | None, estado: str | None,
        desde: date | None, hasta: date | None,
    ) -> tuple[list[VentaQueso], int]:
        extra = []
        if desde:
            extra.append(VentaQueso.fecha >= desde)
        if hasta:
            extra.append(VentaQueso.fecha <= hasta)
        return self.repo.list_paginated(params, search=search, estado=estado, extra_criteria=extra)


class ConversionBoronaService(BaseService[ConversionBorona]):
    """Pasar queso del inventario de reventa a borona."""

    repository_cls = ConversionBoronaRepository
    modulo = "reventa"

    def crear(self, payload: Any) -> ConversionBorona:
        data = payload.model_dump(exclude_unset=True)
        disponible = ReventaResumenService.queso_disponible(self.db, self.ctx)
        if Decimal(data["kilos"]) > disponible:
            raise BusinessError(f"Solo hay {disponible} kg de queso disponibles")
        return super().crear(data)


class ReventaResumenService:
    """Resumen del negocio de reventa (independiente de contabilidad)."""

    def __init__(self, db, ctx):
        self.db = db
        self.ctx = ctx
        self.compras = CompraQuesoRepository(db, ctx.empresa_id)
        self.ventas = VentaQuesoRepository(db, ctx.empresa_id)
        self.conversiones = ConversionBoronaRepository(db, ctx.empresa_id)

    @staticmethod
    def queso_disponible(db, ctx) -> Decimal:
        compras = CompraQuesoRepository(db, ctx.empresa_id)
        ventas = VentaQuesoRepository(db, ctx.empresa_id)
        conversiones = ConversionBoronaRepository(db, ctx.empresa_id)
        kilos_comprados, _, _ = compras.acumulados()
        kilos_queso_vendidos, _, _ = ventas.acumulados()
        return kilos_comprados - kilos_queso_vendidos - conversiones.total_convertido()

    @staticmethod
    def borona_disponible(db, ctx) -> Decimal:
        compras = CompraQuesoRepository(db, ctx.empresa_id)
        ventas = VentaQuesoRepository(db, ctx.empresa_id)
        conversiones = ConversionBoronaRepository(db, ctx.empresa_id)
        _, borona_de_compras, _ = compras.acumulados()
        _, borona_vendida, _ = ventas.acumulados()
        return borona_de_compras + conversiones.total_a_borona() - borona_vendida

    def resumen(self, desde: date, hasta: date) -> ResumenReventa:
        kilos_comprados, total_compras = self.compras.totales_periodo(desde, hasta)
        kilos_queso, ventas_queso = self.ventas.totales_periodo(desde, hasta, tipo="queso")
        kilos_borona, ventas_borona = self.ventas.totales_periodo(desde, hasta, tipo="borona")
        total_ventas = ventas_queso + ventas_borona
        total_gastos = self.ventas.gastos_periodo(desde, hasta)

        kilos_hist_comprados, borona_de_compras, por_pagar = self.compras.acumulados()
        hist_queso_vendido, hist_borona_vendida, por_cobrar = self.ventas.acumulados()
        # `convertido` = todo lo que salió del queso disponible (borona + merma);
        # `a_borona` = solo lo que se pasó a borona (suma al inventario de borona).
        convertido = self.conversiones.total_convertido()
        a_borona = self.conversiones.total_a_borona()

        precio_prom_compra = (
            (total_compras / kilos_comprados).quantize(DOS_DECIMALES) if kilos_comprados else CERO
        )
        precio_prom_venta = (
            (ventas_queso / kilos_queso).quantize(DOS_DECIMALES) if kilos_queso else CERO
        )
        # Ganancia neta EXACTA del período = lo que se vendió − lo que se compró
        # − los gastos de venta. Al restar TODA la compra (no solo el costo de lo
        # vendido) queda contada la merma: se pagó por el lote completo aunque al
        # vender pese menos. La borona vendida suma como bono (llega sin costo).
        ganancia = (total_ventas - total_compras - total_gastos).quantize(DOS_DECIMALES)
        # Ganancia promedio por kilo de queso vendido (ya con compra, merma y gastos).
        margen = (ganancia / kilos_queso).quantize(DOS_DECIMALES) if kilos_queso else CERO

        return ResumenReventa(
            desde=desde,
            hasta=hasta,
            kilos_comprados=kilos_comprados,
            total_compras=total_compras,
            kilos_vendidos=kilos_queso,
            total_ventas=total_ventas,
            precio_promedio_compra=precio_prom_compra,
            precio_promedio_venta=precio_prom_venta,
            total_gastos=total_gastos,
            merma_estimada=kilos_comprados - kilos_queso,
            ganancia_estimada=ganancia,
            margen_por_kilo=margen,
            kilos_borona_vendidos=kilos_borona,
            total_ventas_borona=ventas_borona,
            kilos_disponibles=kilos_hist_comprados - hist_queso_vendido - convertido,
            borona_disponible=borona_de_compras + a_borona - hist_borona_vendida,
            por_pagar_productores=por_pagar,
            por_cobrar_clientes=por_cobrar,
        )
