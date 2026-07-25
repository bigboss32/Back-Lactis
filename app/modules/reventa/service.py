"""Reventa de queso: compras a productores con merma y abonos, ventas a
clientes y resumen de ganancia. Contabilidad separada del libro de la quesera.
"""
import re
import unicodedata
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, NotFoundError
from app.core.pagination import PageParams
from app.modules.empresas.repository import EmpresaRepository
from app.modules.reventa.models import (
    DESTINO_MERMA,
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
from app.modules.reventa.schemas import (
    EstadoCuentaCliente,
    EstadoCuentaPago,
    EstadoCuentaVenta,
    GananciaProducto,
    GananciaProductor,
    ResumenReventa,
    SugerenciasReventa,
)
from app.utils.export import build_estado_cuenta_pdf

CERO = Decimal("0")
DOS_DECIMALES = Decimal("0.01")

# Textos del desglose por producto (los ve el usuario final tal cual)
ETIQUETAS_PRODUCTO = {
    "queso": "Vendido como queso",
    "borona": "Vendido como borona",
    "merma": "Merma (pérdida real)",
    "pendiente": "Aún en inventario",
    # Ojo: el residuo negativo NO significa "vendido". Puede ser queso de una
    # temporada anterior que se vendió, se pasó a borona o se perdió. El texto
    # es neutro a propósito: afirmar una venta sería la misma mentira que se
    # arregló con la merma.
    "anterior": "Salió de inventario anterior",
}
NOTAS_PRODUCTO = {
    "queso": "vendido como queso entero",
    "borona": "subproducto vendido más barato",
    "merma": "se pagó y no se vendió: pérdida",
    "pendiente": "plata invertida, aún sin vender",
    "anterior": "se compró en un período anterior",
}
# Cuando unos kilos no tienen costo porque la compra cayó fuera del período, no
# se puede hablar de pérdida en pesos: se dice de dónde vienen.
NOTA_SIN_COSTO = "se compró en un período anterior: aquí no lleva costo"

# Nombre del producto listo para mostrarle al cliente en su estado de cuenta
NOMBRE_PRODUCTO = {TIPO_VENTA_QUESO: "Queso", TIPO_VENTA_BORONA: "Borona"}


def _nombre_archivo_cliente(cliente: str) -> str:
    """Nombre de archivo seguro para el estado de cuenta.

    El nombre del cliente es texto libre: si se colara una comilla o un salto de
    línea en el header Content-Disposition sería una inyección de cabecera HTTP.
    Se quitan los acentos (para que "Sebastián" siga siendo legible) y se borra
    todo lo que no sea alfanumérico, guion o guion bajo.
    """
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", cliente) if not unicodedata.combining(c)
    )
    limpio = re.sub(r"[^A-Za-z0-9_-]", "", "_".join(sin_acentos.split()))
    return f"estado_cuenta_{limpio or 'cliente'}.pdf"


def _canonizar_nombre(nombre: str, ya_usados: list[str]) -> str:
    """Si el nombre ya está registrado escrito de otra forma (mayúsculas o
    espacios de sobra), devuelve la escritura que ya está guardada. Así
    "sebastián ruiz" no se vuelve un segundo productor y no se parten sus kilos,
    su saldo ni su puesto en el ranking.

    La comparación se hace en Python a propósito: el lower() de SQLite solo baja
    letras ASCII (deja la Á como Á), mientras el de Postgres sí baja acentos. En
    Python el resultado es el mismo en las dos bases.
    """
    limpio = " ".join(nombre.split())
    clave = limpio.lower()
    for usado in ya_usados:
        if " ".join(usado.split()).lower() == clave:
            return usado
    return limpio


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

    def _canonizar(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("productor"):
            data["productor"] = _canonizar_nombre(
                data["productor"], self.repo.nombres_productores()
            )
        return data

    def crear(self, payload: Any) -> CompraQueso:
        data = self._calcular(payload.model_dump(exclude_unset=True))
        data["estado"] = ESTADO_PENDIENTE
        return super().crear(self._canonizar(data))

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> CompraQueso:
        actual = self.repo.get_or_fail(entity_id)
        if actual.estado == ESTADO_ANULADA:
            raise BusinessError("No se puede modificar una compra anulada")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        data = self._calcular(data, actual)
        # Se puede editar aunque tenga abonos (incluida una pagada): se recalcula el
        # estado con los abonos ya registrados y el saldo queda al día.
        data["estado"] = _estado_pago(data["valor_total"], actual.abonado)
        return super().actualizar(entity_id, self._canonizar(data))

    def validar_eliminar(self, obj: CompraQueso) -> None:
        if obj.abonado > CERO:
            raise BusinessError(
                "No se puede eliminar una compra con abonos; elimine primero los abonos o anúlela"
            )

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

    def eliminar_abono(self, compra_id: uuid.UUID, abono_id: uuid.UUID) -> CompraQueso:
        """Elimina un abono mal registrado: baja el abonado y recalcula el estado."""
        compra = self.repo.get_or_fail(compra_id)
        abono = next((a for a in compra.abonos if a.id == abono_id), None)
        if abono is None:
            raise NotFoundError("Abono no encontrado")
        valor = Decimal(abono.valor)
        compra.abonado = max(compra.abonado - valor, CERO)
        compra.estado = _estado_pago(compra.valor_total, compra.abonado)
        compra.updated_by = self.ctx.user_id
        self.db.delete(abono)
        self.db.flush()
        self._audit(
            "editar", compra.id, None,
            {"abono_eliminado": float(valor), "estado": compra.estado},
        )
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

    def _canonizar(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("cliente"):
            data["cliente"] = _canonizar_nombre(data["cliente"], self.repo.nombres_clientes())
        return data

    def crear(self, payload: Any) -> VentaQueso:
        data = self._canonizar(payload.model_dump(exclude_unset=True))
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
        return super().actualizar(entity_id, self._canonizar(data))

    def validar_eliminar(self, obj: VentaQueso) -> None:
        if obj.abonado > CERO:
            raise BusinessError(
                "No se puede eliminar una venta con abonos; elimine primero los abonos o anúlela"
            )

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

    def eliminar_abono(self, venta_id: uuid.UUID, abono_id: uuid.UUID) -> VentaQueso:
        """Elimina un abono mal registrado: baja el abonado y recalcula el estado."""
        venta = self.repo.get_or_fail(venta_id)
        abono = next((a for a in venta.abonos if a.id == abono_id), None)
        if abono is None:
            raise NotFoundError("Abono no encontrado")
        valor = Decimal(abono.valor)
        venta.abonado = max(venta.abonado - valor, CERO)
        venta.estado = _estado_pago(venta.valor_total, venta.abonado)
        venta.updated_by = self.ctx.user_id
        self.db.delete(abono)
        self.db.flush()
        self._audit(
            "editar", venta.id, None,
            {"abono_eliminado": float(valor), "estado": venta.estado},
        )
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
        # La merma es pérdida sin valor: no lleva precio.
        if data.get("destino") == DESTINO_MERMA:
            data["precio_kilo"] = CERO
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

    @staticmethod
    def _costo_de(kilos: Decimal, kilos_comprados: Decimal, total_compras: Decimal) -> Decimal:
        """Costo de esos kilos al precio promedio de compra del período.
        Divide sin redondear antes de multiplicar para no acumular error.
        """
        if not kilos_comprados:
            return CERO
        return (kilos * total_compras / kilos_comprados).quantize(DOS_DECIMALES)

    @classmethod
    def _fila_producto(
        cls,
        producto: str,
        kilos: Decimal,
        ingreso: Decimal,
        costo: Decimal,
        gastos: Decimal,
        costo_kilo: Decimal,
        kilos_vendidos: Decimal | None = None,
    ) -> GananciaProducto:
        """`kilos` = kilos del lote comprado que fueron a este destino.
        `kilos_vendidos` = kilos realmente vendidos (solo difiere en la borona).
        """
        vendidos = kilos if kilos_vendidos is None else kilos_vendidos
        # Sin costo pero con kilos = la compra quedó fuera del período; decirlo
        # en vez de mostrar $0 de pérdida como si no hubiera costado nada.
        nota = NOTAS_PRODUCTO[producto]
        if kilos > CERO and costo == CERO and producto in ("merma", "pendiente"):
            nota = NOTA_SIN_COSTO
        return GananciaProducto(
            producto=producto,
            etiqueta=ETIQUETAS_PRODUCTO[producto],
            nota=nota,
            kilos=kilos,
            kilos_vendidos=vendidos,
            ingreso=ingreso,
            costo=costo,
            gastos=gastos,
            ganancia=(ingreso - costo - gastos).quantize(DOS_DECIMALES),
            precio_venta_kilo=(
                (ingreso / vendidos).quantize(DOS_DECIMALES) if vendidos else CERO
            ),
            costo_kilo=costo_kilo,
        )

    @classmethod
    def _filas_por_producto(
        cls,
        *,
        kilos_comprados: Decimal,
        total_compras: Decimal,
        costo_kilo: Decimal,
        kilos_queso: Decimal,
        ventas_queso: Decimal,
        gastos_queso: Decimal,
        kilos_a_borona: Decimal,
        kilos_borona_vendidos: Decimal,
        ventas_borona: Decimal,
        gastos_borona: Decimal,
        kilos_merma: Decimal,
        kilos_pendientes: Decimal,
    ) -> list[GananciaProducto]:
        """Desglose de la ganancia del período en cuatro filas: queso, borona,
        merma y el residuo (lo que quedó en inventario, o lo que salió de un
        inventario anterior si se movió más de lo comprado en el período).

        CLAVE: el costo se reparte entre los kilos DEL LOTE COMPRADO y sus cuatro
        destinos reales (vendido como queso, pasado a borona, merma, inventario).
        La fila de borona se cuesta por los kilos CONVERTIDOS (kilos_a_borona),
        NO por los vendidos: la borona vendida sale del inventario de borona, que
        también se alimenta de la borona que llega gratis con el lote y de
        conversiones de temporadas anteriores. Costear los kilos vendidos
        inventaba costos que nunca se pagaron.

        Las tres primeras filas cargan su costo al precio promedio de compra y el
        residuo se lleva la diferencia, así la suma de los cuatro costos es
        EXACTAMENTE total_compras y la suma de las ganancias es exactamente
        ganancia_estimada (invariante del resumen).
        """
        costo_queso = cls._costo_de(kilos_queso, kilos_comprados, total_compras)
        costo_borona = cls._costo_de(kilos_a_borona, kilos_comprados, total_compras)
        costo_merma = cls._costo_de(kilos_merma, kilos_comprados, total_compras)
        costo_residuo = total_compras - (costo_queso + costo_borona + costo_merma)
        # Residuo positivo: queso comprado que todavía no se ha movido. Negativo:
        # se movió queso de una temporada anterior, así que su costo es un
        # crédito (ya se pagó antes) y por eso sale negativo.
        producto_residuo = "pendiente" if kilos_pendientes >= CERO else "anterior"

        return [
            cls._fila_producto(
                "queso", kilos_queso, ventas_queso, costo_queso, gastos_queso, costo_kilo
            ),
            cls._fila_producto(
                "borona",
                kilos_a_borona,
                ventas_borona,
                costo_borona,
                gastos_borona,
                costo_kilo,
                kilos_vendidos=kilos_borona_vendidos,
            ),
            cls._fila_producto("merma", kilos_merma, CERO, costo_merma, CERO, costo_kilo),
            cls._fila_producto(
                producto_residuo, abs(kilos_pendientes), CERO, costo_residuo, CERO, costo_kilo
            ),
        ]

    def _filas_por_productor(
        self, desde: date, hasta: date, valor_realizado_kilo: Decimal
    ) -> list[GananciaProductor]:
        """Ganancia ESTIMADA por productor (la UI debe decir que es estimación).

        Reparte el valor neto que dejó cada kilo comprado en el período
        (`valor_realizado_kilo`) entre los kilos que se le compraron a cada uno.
        Quien vendió más barato dejó más margen. Como el divisor de ese valor son
        los kilos COMPRADOS, la suma de las filas cuadra con la ganancia neta del
        período: no hay forma de que el ranking contradiga la tarjeta de arriba.
        """
        filas: list[GananciaProductor] = []
        for productor, compras, kilos, total_comprado, por_pagar in self.compras.por_productor(
            desde, hasta
        ):
            precio_promedio = (
                (total_comprado / kilos).quantize(DOS_DECIMALES) if kilos else CERO
            )
            filas.append(
                GananciaProductor(
                    productor=productor,
                    compras=compras,
                    kilos=kilos,
                    total_comprado=total_comprado,
                    precio_promedio=precio_promedio,
                    por_pagar=por_pagar,
                    margen_por_kilo=valor_realizado_kilo - precio_promedio,
                    ganancia_estimada=(
                        (kilos * valor_realizado_kilo).quantize(DOS_DECIMALES)
                        - total_comprado
                    ),
                )
            )
        filas.sort(key=lambda fila: fila.ganancia_estimada, reverse=True)
        return filas

    def resumen(self, desde: date, hasta: date) -> ResumenReventa:
        kilos_comprados, total_compras = self.compras.totales_periodo(desde, hasta)
        kilos_queso, ventas_queso = self.ventas.totales_periodo(
            desde, hasta, tipo=TIPO_VENTA_QUESO
        )
        # El total sale de la consulta SIN filtro de tipo y la borona por
        # diferencia: si algún día hay un tercer tipo de venta, o un dato viejo
        # con el tipo en blanco, su plata NO desaparece del resumen.
        kilos_todos, total_ventas = self.ventas.totales_periodo(desde, hasta)
        kilos_borona = kilos_todos - kilos_queso
        ventas_borona = total_ventas - ventas_queso
        total_gastos = self.ventas.gastos_periodo(desde, hasta)
        # Los gastos de la borona se sacan por diferencia (solo hay dos tipos de
        # venta), así queso + borona siempre suma EXACTO el total de gastos.
        gastos_queso = self.ventas.gastos_periodo(desde, hasta, tipo=TIPO_VENTA_QUESO)
        gastos_borona = total_gastos - gastos_queso
        # Ajustes del período: lo que se pasó a borona y LA MERMA REAL.
        kilos_a_borona, kilos_merma = self.conversiones.totales_periodo(desde, hasta)

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
        # Kilos que de verdad se vendieron: queso + borona. Es la base de los
        # promedios por kilo vendido (antes solo contaba el queso, y daba 0
        # cuando el período solo tuvo ventas de borona).
        kilos_vendidos_total = kilos_queso + kilos_borona
        # Residuo CON SIGNO del LOTE COMPRADO: lo que no se vendió como queso, no
        # se pasó a borona y no se perdió. Se resta la borona CONVERTIDA (que sí
        # salió del queso comprado), NO la borona vendida: esa sale del inventario
        # de borona, que además recibe la borona que llega gratis con el lote. Así
        # este residuo coincide con `kilos_disponibles` cuando el período abarca
        # todo el histórico, y las dos cifras no se contradicen en pantalla.
        kilos_pendientes = kilos_comprados - kilos_queso - kilos_a_borona - kilos_merma
        # Ganancia neta EXACTA del período = lo que se vendió − lo que se compró
        # − los gastos de venta. Al restar TODA la compra (no solo el costo de lo
        # vendido) queda contado lo que no se alcanzó a vender y la merma real.
        ganancia = (total_ventas - total_compras - total_gastos).quantize(DOS_DECIMALES)
        margen = (
            (ganancia / kilos_vendidos_total).quantize(DOS_DECIMALES)
            if kilos_vendidos_total
            else CERO
        )
        # Lo neto que dejó cada kilo COMPRADO en el período. El divisor son los
        # kilos comprados (no los vendidos) a propósito: así repartirlo entre los
        # productores suma exactamente la ganancia neta del período.
        valor_realizado_kilo = (
            ((total_ventas - total_gastos) / kilos_comprados).quantize(DOS_DECIMALES)
            if kilos_comprados
            else CERO
        )

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
            ganancia_estimada=ganancia,
            margen_por_kilo=margen,
            valor_realizado_kilo=valor_realizado_kilo,
            kilos_borona_vendidos=kilos_borona,
            total_ventas_borona=ventas_borona,
            kilos_a_borona=kilos_a_borona,
            kilos_merma=kilos_merma,
            kilos_pendientes=kilos_pendientes,
            por_producto=self._filas_por_producto(
                kilos_comprados=kilos_comprados,
                total_compras=total_compras,
                costo_kilo=precio_prom_compra,
                kilos_queso=kilos_queso,
                ventas_queso=ventas_queso,
                gastos_queso=gastos_queso,
                kilos_a_borona=kilos_a_borona,
                kilos_borona_vendidos=kilos_borona,
                ventas_borona=ventas_borona,
                gastos_borona=gastos_borona,
                kilos_merma=kilos_merma,
                kilos_pendientes=kilos_pendientes,
            ),
            por_productor=self._filas_por_productor(desde, hasta, valor_realizado_kilo),
            kilos_disponibles=kilos_hist_comprados - hist_queso_vendido - convertido,
            borona_disponible=borona_de_compras + a_borona - hist_borona_vendida,
            por_pagar_productores=por_pagar,
            por_cobrar_clientes=por_cobrar,
        )

    def sugerencias(self) -> SugerenciasReventa:
        """Nombres ya usados de productores y clientes, para autocompletar."""
        return SugerenciasReventa(
            productores=self.compras.nombres_productores(),
            clientes=self.ventas.nombres_clientes(),
        )

    # -------------------------------------------------------- estado de cuenta
    def estado_cuenta(
        self, cliente: str, desde: date | None = None, hasta: date | None = None
    ) -> EstadoCuentaCliente:
        """Cómo va la facturación de un cliente: sus compras, sus pagos y el saldo.

        CONFIDENCIALIDAD: esto se le entrega AL CLIENTE. De cada venta solo salen
        fecha, producto, kilos, precio por kilo, total, abonado y saldo, y de cada
        abono solo fecha y valor. Los gastos de venta (transporte), la "venta
        libre", los costos de compra, los márgenes y las observaciones (tanto de
        la venta como del abono) se quedan adentro: son los números de la quesera.

        Sin rango de fechas cubre todo el histórico, que es el saldo real que debe.
        """
        ventas = self.ventas.por_cliente(cliente, desde, hasta)
        if not ventas:
            # Si tiene ventas pero fuera del rango pedido, decirlo tal cual: no es
            # lo mismo que no ser cliente.
            if (desde or hasta) and self.ventas.por_cliente(cliente):
                raise NotFoundError(
                    "El cliente no tiene ventas vigentes en el período consultado "
                    "(las ventas anuladas no cuentan)"
                )
            # Se aclara lo de las anuladas porque el usuario puede estar viendo en
            # pantalla una venta anulada de ese cliente y no entender el error.
            raise NotFoundError(
                "El cliente no tiene ventas registradas (las ventas anuladas no "
                "cuentan para el estado de cuenta)"
            )

        filas: list[EstadoCuentaVenta] = []
        pagos: list[EstadoCuentaPago] = []
        total_kilos = CERO
        total_facturado = CERO
        total_abonado = CERO
        for venta in ventas:
            kilos = Decimal(venta.kilos)
            valor = Decimal(venta.valor_total)
            abonado = Decimal(venta.abonado)
            total_kilos += kilos
            total_facturado += valor
            total_abonado += abonado
            tipo = venta.tipo or TIPO_VENTA_QUESO
            filas.append(
                EstadoCuentaVenta(
                    fecha=venta.fecha,
                    tipo=tipo,
                    producto=NOMBRE_PRODUCTO.get(tipo, tipo.capitalize()),
                    kilos=kilos,
                    precio_kilo=Decimal(venta.precio_kilo),
                    valor_total=valor,
                    abonado=abonado,
                    saldo=valor - abonado,
                    estado=venta.estado,
                )
            )
            # Los pagos son TODOS los abonos de TODAS sus ventas, juntos: el
            # cliente paga "a la cuenta", no le interesa a qué venta se aplicó.
            # Del abono solo salen fecha y valor: sus `observaciones` son la nota
            # interna de la quesera y NO se copian aquí (ver EstadoCuentaPago).
            pagos += [
                EstadoCuentaPago(fecha=abono.fecha, valor=Decimal(abono.valor))
                for abono in venta.abonos
            ]
        pagos.sort(key=lambda pago: pago.fecha)

        return EstadoCuentaCliente(
            # El nombre que se muestra es el GUARDADO (el de su primera venta), no
            # el que llegó por query: así sale bien escrito aunque se haya
            # consultado en minúsculas.
            cliente=ventas[0].cliente,
            desde=desde,
            hasta=hasta,
            emitido=date.today(),
            compras=len(filas),
            total_kilos=total_kilos,
            total_facturado=total_facturado,
            total_abonado=total_abonado,
            saldo=total_facturado - total_abonado,
            ventas=filas,
            pagos=pagos,
        )

    def _auditar_exportacion(self, datos: EstadoCuentaCliente) -> None:
        """Deja registrada la SALIDA de datos: exportar la cartera histórica de un
        cliente es entregar información afuera y tiene que quedar en la bitácora
        (quién la sacó, de qué cliente y de qué rango).

        Se escribe la fila a mano porque ReventaResumenService no extiende
        BaseService (no tiene un repositorio único: cruza compras, ventas y
        conversiones), así que no hereda el helper _audit.
        """
        from app.modules.auditoria.models import Auditoria

        self.db.add(
            Auditoria(
                empresa_id=self.ctx.empresa_id,
                usuario_id=self.ctx.user_id,
                ip=self.ctx.ip,
                modulo="reventa",
                accion="exportar",
                entidad="EstadoCuentaCliente",
                entidad_id=None,
                antes=None,
                despues={
                    "documento": "estado_cuenta_pdf",
                    "cliente": datos.cliente,
                    "desde": datos.desde.isoformat() if datos.desde else None,
                    "hasta": datos.hasta.isoformat() if datos.hasta else None,
                    "compras": datos.compras,
                    "saldo": float(datos.saldo),
                },
            )
        )

    def estado_cuenta_pdf(
        self, cliente: str, desde: date | None = None, hasta: date | None = None
    ) -> tuple[bytes, str]:
        """Estado de cuenta en PDF, listo para mandárselo al cliente."""
        datos = self.estado_cuenta(cliente, desde, hasta)
        self._auditar_exportacion(datos)
        empresa = EmpresaRepository(self.db).get(self.ctx.empresa_id)
        nombre_empresa = empresa.nombre if empresa else "Quesera"
        nit = empresa.nit if empresa else None
        ubicacion = (
            (", ".join(p for p in [empresa.ciudad, empresa.departamento] if p) or None)
            if empresa
            else None
        )
        if datos.desde and datos.hasta:
            periodo = (
                f"{datos.desde.strftime('%d/%m/%Y')} al {datos.hasta.strftime('%d/%m/%Y')}"
            )
        elif datos.desde:
            periodo = f"Desde el {datos.desde.strftime('%d/%m/%Y')}"
        elif datos.hasta:
            periodo = f"Hasta el {datos.hasta.strftime('%d/%m/%Y')}"
        else:
            periodo = "Todo el histórico"

        pdf = build_estado_cuenta_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            cliente=datos.cliente,
            emitido=datos.emitido.strftime("%d/%m/%Y"),
            periodo=periodo,
            compras=datos.compras,
            # Se arman los campos uno por uno a propósito: lo que no se nombre
            # aquí NO puede llegar al PDF que ve el cliente.
            ventas=[
                {
                    "fecha": v.fecha,
                    "producto": v.producto,
                    "kilos": v.kilos,
                    "precio_kilo": v.precio_kilo,
                    "valor_total": v.valor_total,
                    "abonado": v.abonado,
                    "saldo": v.saldo,
                }
                for v in datos.ventas
            ],
            pagos=[{"fecha": p.fecha, "valor": p.valor} for p in datos.pagos],
            total_kilos=datos.total_kilos,
            total_facturado=datos.total_facturado,
            total_abonado=datos.total_abonado,
            saldo=datos.saldo,
        )
        return pdf, _nombre_archivo_cliente(datos.cliente)
