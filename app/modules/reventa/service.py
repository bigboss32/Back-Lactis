"""Reventa de queso: compras a productores con merma y abonos, ventas a
clientes y resumen de ganancia. Contabilidad separada del libro de la quesera.
"""
import os
import re
import unicodedata
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.nombres import canonizar_nombre, clave_de_tercero, unir_nombres
from app.common.service import BaseService, serialize_entity
from app.core.config import settings
from app.core.exceptions import BusinessError, NotFoundError
from app.core.logging_config import get_logger
from app.core.pagination import PageParams
from app.core.storage import (
    MENSAJE_NO_CONFIGURADO,
    R2Client,
    caducidad_utc,
    r2_configurado,
    texto_caducidad,
)
from app.modules.empresas.repository import EmpresaRepository
from app.modules.reventa.models import (
    DESTINO_MERMA,
    ESTADO_ANULADA,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    ESTADO_PENDIENTE,
    TIPO_SALDO_COBRAR,
    TIPO_SALDO_PAGAR,
    TIPO_VENTA_BORONA,
    TIPO_VENTA_QUESO,
    AbonoCompraQueso,
    AbonoSaldoAnterior,
    AbonoVentaQueso,
    AdjuntoReventa,
    CompraQueso,
    ConversionBorona,
    SaldoAnterior,
    Temporada,
    VentaQueso,
)
from app.modules.reventa.lotes import (
    AjusteEvento,
    CompraEvento,
    LoteCalculado,
    VentaEvento,
    repartir_lotes,
)
from app.modules.reventa.repository import (
    AdjuntoReventaRepository,
    CompraQuesoRepository,
    ConversionBoronaRepository,
    SaldoAnteriorRepository,
    TemporadaRepository,
    VentaQuesoRepository,
)
from app.modules.reventa.schemas import (
    AdjuntoRead,
    AdjuntosLista,
    EnlaceCompartido,
    EstadoCuentaCliente,
    GananciaDia,
    GananciaPorDia,
    EstadoCuentaCompra,
    EstadoCuentaPago,
    EstadoCuentaPagoProductor,
    EstadoCuentaProductor,
    EstadoCuentaSaldoAnterior,
    EstadoCuentaVenta,
    GananciaProducto,
    GananciaProductor,
    ResumenReventa,
    CompraDelLoteRead,
    LoteResumen,
    LotesPanel,
    VentaDelLoteRead,
    SugerenciasReventa,
    TemporadaResumen,
    TemporadasPanel,
)
from app.utils.export import (
    build_estado_cuenta_pdf,
    build_estado_cuenta_productor_pdf,
    pesos,
)

CERO = Decimal("0")
DOS_DECIMALES = Decimal("0.01")


def _dinero(valor: Decimal) -> Decimal:
    """Redondea a centavos. Se usa al FINAL, nunca antes de multiplicar: en el
    detalle por productor, cuantizar antes de multiplicar dejaba la columna
    cinco pesos por debajo de la cifra grande."""
    return Decimal(valor).quantize(DOS_DECIMALES)

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


def _nombre_archivo_productor(productor: str) -> str:
    """Nombre de archivo seguro para el estado de cuenta del productor.

    Mismo saneamiento que _nombre_archivo_cliente, y se deja aparte a propósito
    para no tocar el camino del cliente, que ya está desplegado y verificado. El
    nombre del productor es texto libre: si se colara una comilla o un salto de
    línea en el header Content-Disposition sería una inyección de cabecera HTTP.
    Se quitan los acentos (para que "Sebastián" siga siendo legible) y se borra
    todo lo que no sea alfanumérico, guion o guion bajo.
    """
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", productor) if not unicodedata.combining(c)
    )
    limpio = re.sub(r"[^A-Za-z0-9_-]", "", "_".join(sin_acentos.split()))
    return f"estado_cuenta_productor_{limpio or 'productor'}.pdf"


# La canonización de nombres de terceros vive ahora en app/common/nombres.py: el
# flete por tramos necesitó exactamente la misma regla para el nombre del
# CONDUCTOR, y dos copias habrían significado que el mismo señor se unifica en
# una pantalla y se parte en dos en la otra. Se conservan estos nombres locales
# con guion bajo para no tocar las llamadas de este archivo, que ya están
# probadas y desplegadas.
_canonizar_nombre = canonizar_nombre
_clave_tercero = clave_de_tercero
_unir_nombres = unir_nombres


def _agrupar_pendientes(
    filas: list[tuple[str, Decimal]],
) -> dict[str, tuple[str, Decimal]]:
    """Agrupa filas (nombre del tercero, saldo) por tercero:
    clave normalizada -> (nombre como está escrito, saldo sumado).

    Las variantes de escritura que la base pudo dejar en grupos distintos se
    SUMAN en una sola entrada (el lower() de SQLite no baja acentos y el de
    Postgres sí). Los saldos en cero se descartan: no son plata pendiente y no
    merecen una fila propia en el detalle.
    """
    agrupados: dict[str, tuple[str, Decimal]] = {}
    for nombre, saldo in filas:
        if not saldo:
            continue
        clave = _clave_tercero(nombre)
        primero, acumulado = agrupados.get(clave, (nombre, CERO))
        agrupados[clave] = (primero, acumulado + saldo)
    return agrupados


def _estado_pago(valor_total: Decimal, abonado: Decimal) -> str:
    if abonado <= CERO:
        return ESTADO_PENDIENTE
    return ESTADO_PAGADA if abonado >= valor_total else ESTADO_PARCIAL


def _bloquear(db: Session, entidad: Any) -> Any:
    """Relee la fila con FOR UPDATE antes de tocarle la plata.

    Sin esto, dos abonos a la vez sobre la misma deuda se pisan: los dos leen
    `abonado` viejo, los dos validan contra el mismo saldo y el segundo escribe
    encima del primero. Se pierde un pago —el productor reclama y en el sistema
    no está— y la cartera deja de cuadrar.

    Dos detalles que ya nos costaron caro antes en este proyecto:

    - `populate_existing`: sin él, el FOR UPDATE bloquea la fila en la base pero
      SQLAlchemy devuelve el objeto que ya tenía en memoria, CON LOS VALORES
      VIEJOS. Y es peor de lo que suena: quien llega segundo se queda esperando
      el candado justo mientras el primero escribe, así que al soltarse tiene en
      la mano exactamente los datos de antes.
    - Las relaciones de abonos son lazy="selectin", no "joined". Importa: con un
      LEFT JOIN de por medio, Postgres rechaza el FOR UPDATE con 0A000.

    SQLite descarta el FOR UPDATE en silencio, así que la suite no delata nada
    de esto. La corrección se sostiene por lectura del código, no por la prueba.
    """
    return db.execute(
        select(type(entidad))
        .where(type(entidad).id == entidad.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).scalar_one()


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

    def _nombres_para_canonizar(self) -> list[str]:
        """Contra qué lista se canoniza el nombre del productor: los que ya usan
        las compras MÁS los terceros del libro anterior de tipo 'pagar'.

        Un productor que por ahora SOLO existe en el libro anterior ya tiene una
        escritura guardada, y la primera compra que se le registre tiene que
        adoptarla: si no, su deuda vieja y la nueva quedan en dos productores
        distintos y el detalle muestra dos filas de la misma persona.

        Primero van los de las compras: si el nombre está en los dos lados manda
        la escritura del sistema, que es la que agrupa el detalle por productor.
        """
        return self.repo.nombres_productores() + SaldoAnteriorRepository(
            self.db, self.ctx.empresa_id
        ).nombres_terceros(TIPO_SALDO_PAGAR)

    def _canonizar(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("productor"):
            data["productor"] = _canonizar_nombre(
                data["productor"], self._nombres_para_canonizar()
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
        # estado con los abonos ya registrados y el saldo queda al día. Lo que NO
        # se permite es dejar el total por debajo de lo ya abonado: el saldo se
        # vuelve NEGATIVO y ese negativo RESTA de la tarjeta "Por pagar a
        # productores", que mostraría menos deuda de la que el negocio tiene.
        if data["valor_total"] < Decimal(actual.abonado):
            raise BusinessError(
                f"El total no puede quedar por debajo de lo ya abonado "
                f"({pesos(actual.abonado)}); elimine primero los abonos que sobren"
            )
        # Y tampoco se pueden quitar kilos que ya salieron vendidos. Bajar una
        # compra de 100 kg a 10 cuando ya se vendieron 80 deja el inventario en
        # -70: a partir de ahí NINGUNA venta pasa el control de existencias y el
        # dueño se queda sin poder trabajar sin entender por qué.
        nuevos = Decimal(data["kilos_netos"])
        viejos = Decimal(actual.kilos_netos)
        if nuevos < viejos:
            disponible = ReventaResumenService.queso_disponible(self.db, self.ctx)
            if (nuevos - viejos) + disponible < CERO:
                raise BusinessError(
                    f"No se pueden quitar tantos kilos: de esta compra ya salieron "
                    f"vendidos. Solo quedan {disponible} kg sin vender"
                )
        data["estado"] = _estado_pago(data["valor_total"], actual.abonado)
        return super().actualizar(entity_id, self._canonizar(data))

    def validar_eliminar(self, obj: CompraQueso) -> None:
        if obj.abonado > CERO:
            raise BusinessError(
                "No se puede eliminar una compra con abonos; elimine primero los abonos o anúlela"
            )

    def eliminar(self, entity_id: uuid.UUID) -> None:
        """Borra la compra Y se lleva sus soportes de pago.

        Se valida PRIMERO y se limpian los soportes después: si se limpiaran
        antes, una compra con abonos —que no se puede borrar— perdería sus fotos
        por un borrado que al final no ocurre. Sin esta limpieza, los archivos
        quedaban en el bucket sin ningún documento que los nombre.
        """
        self.validar_eliminar(self.repo.get_or_fail(entity_id))
        AdjuntoReventaService(self.db, self.ctx).limpiar_de_documento(compra_id=entity_id)
        super().eliminar(entity_id)

    def registrar_abono(self, compra_id: uuid.UUID, payload: Any) -> CompraQueso:
        compra = self.repo.get_or_fail(compra_id)
        compra = _bloquear(self.db, compra)
        if compra.estado == ESTADO_ANULADA:
            raise BusinessError("La compra está anulada")
        valor = Decimal(payload.valor)
        if valor > compra.saldo:
            # pesos() y no "{:,.0f}": el formato con coma es gringo y "$1,200,000"
            # en Colombia se lee como un peso con veinte centavos.
            raise BusinessError(
                f"El abono ({pesos(valor)}) supera el saldo ({pesos(compra.saldo)})"
            )
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
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(compra, ["abonos"])
        self._audit("editar", compra.id, None, {"abono": float(valor), "estado": compra.estado})
        return compra

    def eliminar_abono(self, compra_id: uuid.UUID, abono_id: uuid.UUID) -> CompraQueso:
        """Elimina un abono mal registrado: baja el abonado y recalcula el estado."""
        compra = self.repo.get_or_fail(compra_id)
        compra = _bloquear(self.db, compra)
        abono = next((a for a in compra.abonos if a.id == abono_id), None)
        if abono is None:
            raise NotFoundError("Abono no encontrado")
        valor = Decimal(abono.valor)
        compra.abonado = max(compra.abonado - valor, CERO)
        compra.estado = _estado_pago(compra.valor_total, compra.abonado)
        compra.updated_by = self.ctx.user_id
        self.db.delete(abono)
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(compra, ["abonos"])
        self._audit(
            "editar", compra.id, None,
            {"abono_eliminado": float(valor), "estado": compra.estado},
        )
        return compra

    def anular(self, compra_id: uuid.UUID) -> CompraQueso:
        compra = self.repo.get_or_fail(compra_id)
        compra = _bloquear(self.db, compra)
        if compra.abonado > CERO:
            raise BusinessError(
                "No se puede anular una compra con abonos registrados"
            )
        # Si el queso de esta compra YA SE VENDIÓ, no se anula. Anularla borraría
        # de la cuenta un queso que salió de verdad: el inventario se iría a
        # negativo y, con el inventario en negativo, ninguna venta vuelve a pasar
        # el control de existencias — el dueño se queda sin poder trabajar sin
        # entender por qué. Lo que hay que hacer en ese caso es corregir la
        # compra (editarla) o anular primero las ventas que se llevaron ese queso.
        disponible = ReventaResumenService.queso_disponible(self.db, self.ctx)
        if disponible - Decimal(compra.kilos_netos) < CERO:
            raise BusinessError(
                f"No se puede anular: el queso de esta compra ya se vendió. "
                f"Solo quedan {disponible} kg sin vender de los "
                f"{compra.kilos_netos} kg que trajo. Anule primero las ventas "
                f"que se lo llevaron, o corrija la compra en vez de anularla"
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

    def _nombres_para_canonizar(self) -> list[str]:
        """Contra qué lista se canoniza el nombre del cliente: los que ya usan
        las ventas MÁS los terceros del libro anterior de tipo 'cobrar'.

        Un cliente que por ahora SOLO existe en el libro anterior es justo el
        caso que motivó esa pantalla: su primera venta aquí tiene que adoptar la
        escritura ya guardada, o su deuda vieja y la nueva quedan partidas en dos
        clientes y el estado de cuenta no muestra todo lo que debe.

        Primero van los de las ventas: si el nombre está en los dos lados manda
        la escritura del sistema, que es la que agrupa el estado de cuenta.
        """
        return self.repo.nombres_clientes() + SaldoAnteriorRepository(
            self.db, self.ctx.empresa_id
        ).nombres_terceros(TIPO_SALDO_COBRAR)

    def _canonizar(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("cliente"):
            data["cliente"] = _canonizar_nombre(
                data["cliente"], self._nombres_para_canonizar()
            )
        return data

    def _exigir_existencias(
        self, tipo: str, kilos: Decimal, actual: VentaQueso | None = None
    ) -> None:
        """No se puede vender más queso (o borona) del que hay.

        Vive aquí, en un método, y no suelto dentro de `crear`, porque ESE fue el
        defecto: la comprobación estaba solo al crear y `actualizar` no la hacía.
        Se creaba una venta de 1 kg y se editaba a 500, y pasaba. El resumen
        quedaba con kilos negativos y con una ganancia que no era la real, y el
        desglose por lote decía otra cosa distinta que el resumen — que es
        justo lo que el dueño ve al cuadrar a mano.

        Al EDITAR hay que devolverle al inventario los kilos que esa misma venta
        ya tenía apartados, o si no editar 100 kg a 100 kg fallaría por comparar
        contra un disponible del que esos kilos ya están descontados.
        """
        if tipo == TIPO_VENTA_BORONA:
            disponible = ReventaResumenService.borona_disponible(self.db, self.ctx)
            que = "borona"
        else:
            disponible = ReventaResumenService.queso_disponible(self.db, self.ctx)
            que = "queso"
        if actual is not None and actual.estado != ESTADO_ANULADA and actual.tipo == tipo:
            disponible += Decimal(actual.kilos)
        if kilos > disponible:
            raise BusinessError(f"Solo hay {disponible} kg de {que} disponibles")

    def crear(self, payload: Any) -> VentaQueso:
        data = self._canonizar(payload.model_dump(exclude_unset=True))
        de_contado = data.pop("pagada_de_contado", False)
        kilos = Decimal(data["kilos"])
        tipo = data.get("tipo", TIPO_VENTA_QUESO)
        self._exigir_existencias(tipo, kilos)
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
        # La MISMA comprobación que al crear. Sin esto el guardia de la creación
        # es de adorno: se crea la venta con un kilo y se edita a los que sea.
        self._exigir_existencias(data.get("tipo") or actual.tipo, kilos, actual=actual)
        data["valor_total"] = (kilos * precio).quantize(DOS_DECIMALES)
        # Recalcula el gasto total (por_kilo * kilos) si cambió cualquiera de los dos.
        por_kilo = Decimal(
            data["gasto_por_kilo"]
            if data.get("gasto_por_kilo") is not None
            else actual.gasto_por_kilo
        )
        data["gasto_monto"] = (por_kilo * kilos).quantize(DOS_DECIMALES)
        # Se puede editar aunque tenga abonos (incluida una pagada): se recalcula el estado.
        # OJO: aquí NO va el guardia de "el total no puede quedar por debajo de lo
        # abonado" que sí tienen las compras y los saldos de la cuenta anterior.
        # Rebajarle una venta ya pagada deja SALDO A FAVOR del cliente, que es un
        # caso contemplado a propósito en el estado de cuenta (rótulo y signo
        # propios) y cubierto por test_estado_cuenta_saldo_a_favor.
        # Ese saldo negativo tampoco se le resta a la cartera: los agregados suman
        # el saldo de cada fila ACOTADO EN CERO (ver saldo_pendiente en el
        # repositorio), porque lo que un cliente pagó de más no reduce lo que le
        # deben los OTROS. Por eso se puede permitir la edición sin que la tarjeta
        # "Por cobrar a clientes" mienta.
        data["estado"] = _estado_pago(data["valor_total"], actual.abonado)
        return super().actualizar(entity_id, self._canonizar(data))

    def validar_eliminar(self, obj: VentaQueso) -> None:
        if obj.abonado > CERO:
            raise BusinessError(
                "No se puede eliminar una venta con abonos; elimine primero los abonos o anúlela"
            )

    def eliminar(self, entity_id: uuid.UUID) -> None:
        """Borra la venta Y se lleva sus soportes de pago. Mismo orden y misma
        razón que en la compra: ver CompraQuesoService.eliminar."""
        self.validar_eliminar(self.repo.get_or_fail(entity_id))
        AdjuntoReventaService(self.db, self.ctx).limpiar_de_documento(venta_id=entity_id)
        super().eliminar(entity_id)

    def registrar_abono(self, venta_id: uuid.UUID, payload: Any) -> VentaQueso:
        venta = self.repo.get_or_fail(venta_id)
        venta = _bloquear(self.db, venta)
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("La venta está anulada")
        valor = Decimal(payload.valor)
        if valor > venta.saldo:
            # Mismo motivo que en el abono de compra: los miles se separan con
            # punto y los decimales con coma (formato colombiano).
            raise BusinessError(
                f"El abono ({pesos(valor)}) supera el saldo ({pesos(venta.saldo)})"
            )
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
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(venta, ["abonos"])
        self._audit("editar", venta.id, None, {"abono": float(valor), "estado": venta.estado})
        return venta

    def eliminar_abono(self, venta_id: uuid.UUID, abono_id: uuid.UUID) -> VentaQueso:
        """Elimina un abono mal registrado: baja el abonado y recalcula el estado."""
        venta = self.repo.get_or_fail(venta_id)
        venta = _bloquear(self.db, venta)
        abono = next((a for a in venta.abonos if a.id == abono_id), None)
        if abono is None:
            raise NotFoundError("Abono no encontrado")
        valor = Decimal(abono.valor)
        venta.abonado = max(venta.abonado - valor, CERO)
        venta.estado = _estado_pago(venta.valor_total, venta.abonado)
        venta.updated_by = self.ctx.user_id
        self.db.delete(abono)
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(venta, ["abonos"])
        self._audit(
            "editar", venta.id, None,
            {"abono_eliminado": float(valor), "estado": venta.estado},
        )
        return venta

    def anular(self, venta_id: uuid.UUID) -> VentaQueso:
        venta = self.repo.get_or_fail(venta_id)
        venta = _bloquear(self.db, venta)
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


class SaldoAnteriorService(BaseService[SaldoAnterior]):
    """Saldos a medio pagar traídos del sistema anterior.

    Hermano de las compras y las ventas en todo lo que es plata (estado de
    pago, abonos, anulación), pero SIN kilos: no toca inventario, no mueve la
    ganancia y no aparece en el desglose por producto. Solo suma en lo que se
    debe cobrar y en lo que se debe pagar.
    """

    repository_cls = SaldoAnteriorRepository
    modulo = "reventa"

    def _nombres_del_tipo(self, tipo: str) -> list[str]:
        """Contra qué lista se canoniza el nombre del tercero: un saldo por
        'cobrar' es de un CLIENTE y uno por 'pagar' es de un PRODUCTOR.

        Primero van los nombres que ya usan las ventas o las compras, para
        adoptar esa escritura: es la que agrupa el estado de cuenta y el detalle
        por productor, y así "carlos ricaute" no queda como un tercero distinto
        de "Carlos Ricaute" y su deuda no sale partida en dos.
        """
        if tipo == TIPO_SALDO_PAGAR:
            del_modulo = CompraQuesoRepository(
                self.db, self.ctx.empresa_id
            ).nombres_productores()
        else:
            del_modulo = VentaQuesoRepository(self.db, self.ctx.empresa_id).nombres_clientes()
        return del_modulo + self.repo.nombres_terceros(tipo)

    def _canonizar(self, data: dict[str, Any], tipo: str) -> dict[str, Any]:
        if data.get("tercero"):
            data["tercero"] = _canonizar_nombre(data["tercero"], self._nombres_del_tipo(tipo))
        return data

    def crear(self, payload: Any) -> SaldoAnterior:
        data = payload.model_dump(exclude_unset=True)
        tipo = data.get("tipo") or TIPO_SALDO_COBRAR
        data["tipo"] = tipo
        valor_total = Decimal(data["valor_total"])
        abonado = Decimal(data.get("abonado") or CERO)
        if abonado > valor_total:
            raise BusinessError(
                f"Lo abonado ({pesos(abonado)}) supera el valor del saldo "
                f"({pesos(valor_total)})"
            )
        data["abonado"] = abonado
        data["estado"] = _estado_pago(valor_total, abonado)
        saldo = super().crear(self._canonizar(data, tipo))
        if abonado > CERO:
            # Lo que ya venía abonado en el libro viejo queda también como abono,
            # con la fecha del documento: si no, el historial sale vacío y el
            # detalle de abonos no cuadra con la columna "abonado".
            saldo.abonos.append(
                AbonoSaldoAnterior(
                    fecha=saldo.fecha,
                    valor=abonado,
                    observaciones="Abonado en el libro anterior",
                    created_by=self.ctx.user_id,
                )
            )
            self.db.flush()
        return saldo

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> SaldoAnterior:
        actual = self.repo.get_or_fail(entity_id)
        if actual.estado == ESTADO_ANULADA:
            raise BusinessError("No se puede modificar un saldo anulado")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        # El TIPO no se cambia después de creado: un saldo por 'cobrar' es de un
        # CLIENTE y uno por 'pagar' de un PRODUCTOR, y el nombre del tercero ya
        # quedó canonizado contra la lista de ese lado. Cambiarlo por PUT dejaba a
        # una clienta convertida en fila del detalle por productor y le sacaba su
        # deuda del estado de cuenta. Re-canonizar el nombre no alcanza: habría
        # que mover de lado también los abonos y el historial, así que se pide
        # anular y cargar de nuevo, que además queda en la bitácora. Mandar el
        # mismo tipo que ya tiene sí se acepta (el formulario envía todo el
        # objeto).
        tipo = data.get("tipo") or actual.tipo
        if tipo != actual.tipo:
            raise BusinessError(
                f"No se puede cambiar el tipo de un saldo de la cuenta anterior "
                f"(está cargado como '{actual.tipo}'): anúlelo y cargue uno nuevo "
                f"del tipo correcto"
            )
        data["tipo"] = tipo
        valor_total = Decimal(data.get("valor_total") or actual.valor_total)
        data["valor_total"] = valor_total
        # Se puede editar aunque tenga abonos: se recalcula el estado con lo ya
        # abonado y el saldo queda al día (igual que en compras y ventas). Lo que
        # NO se permite es dejar el total por debajo de lo ya abonado: el saldo se
        # vuelve NEGATIVO y ese negativo RESTA de la cartera, así que la tarjeta
        # mostraría menos plata sin cobrar de la que hay y el estado de cuenta le
        # rebajaría la deuda al cliente.
        if valor_total < Decimal(actual.abonado):
            raise BusinessError(
                f"El total no puede quedar por debajo de lo ya abonado "
                f"({pesos(actual.abonado)}); elimine primero los abonos que sobren"
            )
        data["estado"] = _estado_pago(valor_total, actual.abonado)
        return super().actualizar(entity_id, self._canonizar(data, tipo))

    def validar_eliminar(self, obj: SaldoAnterior) -> None:
        if obj.abonado > CERO:
            raise BusinessError(
                "No se puede eliminar un saldo con abonos; elimine primero los abonos o anúlelo"
            )

    def registrar_abono(self, saldo_id: uuid.UUID, payload: Any) -> SaldoAnterior:
        saldo = self.repo.get_or_fail(saldo_id)
        saldo = _bloquear(self.db, saldo)
        if saldo.estado == ESTADO_ANULADA:
            raise BusinessError("El saldo está anulado")
        valor = Decimal(payload.valor)
        if valor > saldo.saldo:
            # pesos() y no "{:,.0f}": el formato con coma es gringo y "$1,200,000"
            # en Colombia se lee como un peso con veinte centavos.
            raise BusinessError(
                f"El abono ({pesos(valor)}) supera el saldo ({pesos(saldo.saldo)})"
            )
        # Se agrega A LA RELACIÓN, no con db.add(): la colección ya viene cargada
        # (lazy="selectin") y un db.add() suelto la dejaría desactualizada, así
        # que la respuesta saldría sin el abono que se acaba de registrar.
        saldo.abonos.append(
            AbonoSaldoAnterior(
                fecha=payload.fecha, valor=valor,
                observaciones=payload.observaciones, created_by=self.ctx.user_id,
            )
        )
        saldo.abonado += valor
        saldo.estado = _estado_pago(saldo.valor_total, saldo.abonado)
        saldo.updated_by = self.ctx.user_id
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(saldo, ["abonos"])
        self._audit("editar", saldo.id, None, {"abono": float(valor), "estado": saldo.estado})
        return saldo

    def eliminar_abono(self, saldo_id: uuid.UUID, abono_id: uuid.UUID) -> SaldoAnterior:
        """Elimina un abono mal registrado: baja el abonado y recalcula el estado."""
        saldo = self.repo.get_or_fail(saldo_id)
        saldo = _bloquear(self.db, saldo)
        abono = next((a for a in saldo.abonos if a.id == abono_id), None)
        if abono is None:
            raise NotFoundError("Abono no encontrado")
        valor = Decimal(abono.valor)
        saldo.abonado = max(saldo.abonado - valor, CERO)
        saldo.estado = _estado_pago(saldo.valor_total, saldo.abonado)
        saldo.updated_by = self.ctx.user_id
        # Se saca de la relación (el cascade delete-orphan borra la fila): así la
        # colección en memoria queda igual que la base y la respuesta ya no lo trae.
        saldo.abonos.remove(abono)
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(saldo, ["abonos"])
        self._audit(
            "editar", saldo.id, None,
            {"abono_eliminado": float(valor), "estado": saldo.estado},
        )
        return saldo

    def anular(self, saldo_id: uuid.UUID) -> SaldoAnterior:
        saldo = self.repo.get_or_fail(saldo_id)
        saldo = _bloquear(self.db, saldo)
        if saldo.abonado > CERO:
            raise BusinessError("No se puede anular un saldo con abonos registrados")
        antes = saldo.estado
        saldo.estado = ESTADO_ANULADA
        saldo.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", saldo.id, {"estado": antes}, {"estado": ESTADO_ANULADA})
        return saldo

    def listar_filtrado(
        self, params: PageParams, *, tipo: str | None, search: str | None,
        estado: str | None, desde: date | None, hasta: date | None,
    ) -> tuple[list[SaldoAnterior], int]:
        extra = []
        if desde:
            extra.append(SaldoAnterior.fecha >= desde)
        if hasta:
            extra.append(SaldoAnterior.fecha <= hasta)
        return self.repo.list_paginated(
            params, search=search, estado=estado, filters={"tipo": tipo},
            extra_criteria=extra,
        )


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


# ----------------------------------- adjuntos (soportes de transferencia)
logger_adjuntos = get_logger("reventa.adjuntos")

# Qué se acepta y con qué extensión se guarda en el bucket.
#
# SE ACEPTA PDF, y es una decisión, no un descuido: los bancos colombianos
# (Bancolombia, Nequi, Davivienda) entregan el comprobante de una transferencia
# como PDF descargable, y ese PDF ES el soporte bueno — más que una foto de la
# pantalla. Rechazarlo obligaría al dueño a tomarle una foto al comprobante que
# ya tenía, que es peor soporte y trabajo de más.
#
# HEIC/HEIF entran porque es lo que produce un iPhone por defecto. El navegador
# a veces no sabe dibujar la miniatura, pero rechazar la foto de un iPhone con
# un "tipo no permitido" sería inexplicable para quien la está mandando.
#
# No entran videos ni ofimática: esto es el respaldo de que se pagó, no un
# archivador. Cada tipo que se abre es un tipo más que hay que servir con un
# enlace firmado, y un .html o un .svg firmados serían código ejecutándose en el
# navegador de quien reciba el enlace.
TIPOS_ADJUNTO_PERMITIDOS: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
}

TIPOS_EN_CRISTIANO = "fotos JPG, PNG, WEBP o HEIC, y comprobantes en PDF"

# Marcas HEIF/HEIC: los primeros bytes son el tamaño de la caja, luego 'ftyp' y
# luego la marca. Se listan las que usan las cámaras de los teléfonos.
MARCAS_HEIC = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"hevs"}
MARCAS_HEIF = {b"mif1", b"msf1"}


def _detectar_tipo(cabeza: bytes) -> str | None:
    """Qué es el archivo DE VERDAD, mirándole los primeros bytes.

    No se confía en el Content-Type que manda el navegador ni en la extensión
    del nombre: los dos los pone quien sube y los dos se cambian solos. Y aquí
    importa de verdad, porque de estos objetos se reparten enlaces firmados que
    se abren en el navegador de otra persona: un .html disfrazado de .jpg sería
    una página que corre en el dominio del almacenamiento con un enlace que el
    dueño repartió de buena fe por WhatsApp.

    Devuelve el tipo reconocido, o None si no es ninguno de los permitidos.
    """
    if cabeza.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if cabeza.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if cabeza[:4] == b"RIFF" and cabeza[8:12] == b"WEBP":
        return "image/webp"
    if cabeza[4:8] == b"ftyp":
        marca = cabeza[8:12]
        if marca in MARCAS_HEIC:
            return "image/heic"
        if marca in MARCAS_HEIF:
            return "image/heif"
    if cabeza.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _tamano_legible(bytes_: int) -> str:
    """"4,2 MB" — con coma decimal, que es como se escribe en Colombia."""
    if bytes_ < 1024:
        return f"{bytes_} bytes"
    if bytes_ < 1024 * 1024:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_ / (1024 * 1024):.1f}".replace(".", ",") + " MB"


class AdjuntoReventaService(BaseService[AdjuntoReventa]):
    """Soportes de pago de compras y ventas de reventa, guardados en R2.

    TRES CAMINOS DISTINTOS PARA MIRAR UN ARCHIVO, a propósito:

    - VER (`listar`): enlaces de minutos, para la pantalla. Se firman de nuevo
      cada vez que se abre el detalle.
    - COMPARTIR (`compartir`): un enlace de días para UNA imagen, para mandarlo
      por WhatsApp. Sale con la fecha de caducidad escrita en cristiano y queda
      registrado en la auditoría: es información de pago saliendo del sistema.
    - BORRAR (`eliminar_adjunto`): quita la fila Y el objeto en R2.

    Los tres empiezan por comprobar que la compra o la venta sea DE LA EMPRESA
    de quien pregunta. Esa comprobación no está en un `if` suelto: se hace
    buscando el documento con su propio repositorio, que ya filtra por
    `empresa_id` y `deleted_at IS NULL`. Si no es suyo, no aparece, y sale un
    404 antes de que se firme absolutamente nada.
    """

    repository_cls = AdjuntoReventaRepository
    modulo = "reventa"

    # ------------------------------------------------------------- utilidades
    @property
    def _max_bytes(self) -> int:
        return settings.ADJUNTOS_MAX_MB * 1024 * 1024

    def _documento(
        self, *, compra_id: uuid.UUID | None = None, venta_id: uuid.UUID | None = None
    ) -> CompraQueso | VentaQueso:
        """La compra o la venta, SIEMPRE por el repositorio con filtro de empresa.

        Es el candado multiempresa de todo el módulo de adjuntos: el documento de
        otra empresa no existe para esta consulta y `get_or_fail` levanta 404.
        """
        if compra_id is not None:
            return CompraQuesoRepository(self.db, self.ctx.empresa_id).get_or_fail(compra_id)
        return VentaQuesoRepository(self.db, self.ctx.empresa_id).get_or_fail(venta_id)

    def _adjunto(self, adjunto_id: uuid.UUID) -> AdjuntoReventa:
        """El adjunto, con el mismo candado: repositorio con filtro de empresa."""
        return self.repo.get_or_fail(adjunto_id)

    def _clave(self, *, carpeta: str, documento_id: uuid.UUID, extension: str) -> str:
        """`{empresa_id}/reventa/{compras|ventas}/{documento_id}/{uuid}{ext}`.

        El empresa_id va DENTRO de la llave a propósito: aunque alguien adivinara
        el resto, la llave de un archivo de otra empresa empieza por un uuid que
        no es el suyo. Y el nombre del archivo NO entra en la llave: lo escribe
        quien sube, y un nombre con `../` o con caracteres raros terminaría
        creando objetos donde no van.
        """
        return (
            f"{self.ctx.empresa_id}/reventa/{carpeta}/{documento_id}/"
            f"{uuid.uuid4().hex}{extension}"
        )

    def _leer_y_validar(self, archivo: Any) -> tuple[bytes, str, str, str]:
        """(contenido, tipo real, extension, nombre) — o BusinessError legible.

        Se mide ANTES de leer: un archivo de 400 MB no se carga en memoria solo
        para después decir que no cabe. En el campo la señal es mala y una subida
        equivocada se nota tarde; lo que no puede pasar es que tumbe el servidor.
        """
        nombre = (getattr(archivo, "filename", "") or "").strip() or "soporte"
        nombre = nombre[:255]

        origen = archivo.file
        try:
            origen.seek(0, os.SEEK_END)
            tamano = origen.tell()
            origen.seek(0)
        except (AttributeError, OSError):  # pragma: no cover - flujo no medible
            tamano = -1

        if tamano == 0:
            raise BusinessError(f"El archivo «{nombre}» está vacío")
        if tamano > self._max_bytes:
            raise BusinessError(
                f"«{nombre}» pesa {_tamano_legible(tamano)} y el máximo son "
                f"{settings.ADJUNTOS_MAX_MB} MB. Tome la foto en menor calidad "
                f"o mande el comprobante en PDF"
            )

        contenido = origen.read()
        # Segunda medición, por si la de arriba no se pudo hacer.
        if len(contenido) > self._max_bytes:
            raise BusinessError(
                f"«{nombre}» pesa {_tamano_legible(len(contenido))} y el máximo son "
                f"{settings.ADJUNTOS_MAX_MB} MB"
            )
        if not contenido:
            raise BusinessError(f"El archivo «{nombre}» está vacío")

        tipo = _detectar_tipo(contenido[:64])
        if tipo is None or tipo not in TIPOS_ADJUNTO_PERMITIDOS:
            raise BusinessError(
                f"«{nombre}» no es una imagen ni un PDF. Solo se aceptan "
                f"{TIPOS_EN_CRISTIANO}"
            )
        return contenido, tipo, TIPOS_ADJUNTO_PERMITIDOS[tipo], nombre

    def _nombre_de_quien_sube(self) -> str | None:
        usuario = getattr(self.ctx, "user", None)
        if usuario is None:
            return None
        completo = f"{getattr(usuario, 'nombre', '') or ''} {getattr(usuario, 'apellido', '') or ''}"
        return completo.strip()[:150] or None

    def _a_read(self, adjunto: AdjuntoReventa, cliente: R2Client | None) -> AdjuntoRead:
        """Fila lista para la pantalla, con enlace corto si hay almacenamiento."""
        url = None
        expira = None
        if cliente is not None:
            segundos = max(60, settings.R2_URL_VER_MINUTOS * 60)
            url = cliente.enlace_firmado(
                clave=adjunto.object_key,
                segundos=segundos,
                nombre_descarga=adjunto.nombre_archivo,
            )
            expira = caducidad_utc(segundos)
        return AdjuntoRead(
            id=adjunto.id,
            compra_id=adjunto.compra_id,
            venta_id=adjunto.venta_id,
            nombre_archivo=adjunto.nombre_archivo,
            content_type=adjunto.content_type,
            tamano_bytes=adjunto.tamano_bytes,
            es_imagen=adjunto.es_imagen,
            subido_por_nombre=adjunto.subido_por_nombre,
            created_at=adjunto.created_at,
            url=url,
            url_expira=expira,
        )

    # ------------------------------------------------------------------- ver
    def listar(
        self, *, compra_id: uuid.UUID | None = None, venta_id: uuid.UUID | None = None
    ) -> AdjuntosLista:
        """Los soportes del documento, cada uno con su enlace de CORTA duración.

        Sin R2 configurado responde 200 con `disponible: false` en vez de un
        error: no es culpa de quien pregunta y el resto de la pantalla tiene que
        poder seguir usándose. Las filas igual salen (nombre, peso, quién lo
        subió), solo que sin enlace para abrirlas.
        """
        self._documento(compra_id=compra_id, venta_id=venta_id)
        filas = self.repo.de_documento(compra_id=compra_id, venta_id=venta_id)
        cupo = max(0, settings.ADJUNTOS_MAX_POR_DOCUMENTO - len(filas))
        if not r2_configurado():
            return AdjuntosLista(
                disponible=False,
                mensaje=MENSAJE_NO_CONFIGURADO,
                cupo_restante=0,
                adjuntos=[self._a_read(f, None) for f in filas],
            )
        cliente = R2Client()
        return AdjuntosLista(
            disponible=True,
            cupo_restante=cupo,
            adjuntos=[self._a_read(f, cliente) for f in filas],
        )

    # ----------------------------------------------------------------- subir
    def subir(
        self,
        archivos: list[Any],
        *,
        compra_id: uuid.UUID | None = None,
        venta_id: uuid.UUID | None = None,
    ) -> AdjuntosLista:
        """Sube N soportes a una compra o a una venta.

        SE VALIDAN TODOS ANTES DE SUBIR NINGUNO. Si la tercera foto no sirve, no
        tiene sentido que las dos primeras ya estén en el bucket: el dueño
        corrige y vuelve a mandar las tres, y quedarían duplicadas.

        Y si R2 falla a mitad de camino, se borran los objetos que alcanzaron a
        subir. La excepción hace rollback de la sesión, así que las filas
        desaparecen; sin este barrido los archivos quedarían en el bucket sin
        ninguna fila que los nombre — invisibles, imborrables y cobrando.
        """
        documento = self._documento(compra_id=compra_id, venta_id=venta_id)
        if not archivos:
            raise BusinessError("No se recibió ningún archivo")
        if not r2_configurado():
            raise BusinessError(MENSAJE_NO_CONFIGURADO, code="r2_no_configurado")

        # Una compra o una venta anulada es un documento muerto: no se le siguen
        # colgando soportes de pago, igual que no se le registran abonos.
        if getattr(documento, "estado", "") == ESTADO_ANULADA:
            raise BusinessError(
                "El documento está anulado: no se le pueden agregar soportes"
            )

        ya_tiene = self.repo.contar_de(compra_id=compra_id, venta_id=venta_id)
        tope = settings.ADJUNTOS_MAX_POR_DOCUMENTO
        if ya_tiene + len(archivos) > tope:
            raise BusinessError(
                f"Caben máximo {tope} soportes por documento. Ya hay {ya_tiene} "
                f"y está mandando {len(archivos)}"
            )

        validados = [self._leer_y_validar(a) for a in archivos]

        carpeta = "compras" if compra_id is not None else "ventas"
        documento_id = compra_id if compra_id is not None else venta_id
        cliente = R2Client()
        subidas: list[str] = []
        quien = self._nombre_de_quien_sube()
        try:
            for contenido, tipo, extension, nombre in validados:
                clave = self._clave(
                    carpeta=carpeta, documento_id=documento_id, extension=extension
                )
                cliente.subir(clave=clave, contenido=contenido, content_type=tipo)
                subidas.append(clave)
                adjunto = self.repo.create(
                    self._prepare_create_data(
                        {
                            "compra_id": compra_id,
                            "venta_id": venta_id,
                            "object_key": clave,
                            "nombre_archivo": nombre,
                            "content_type": tipo,
                            "tamano_bytes": len(contenido),
                            "subido_por_nombre": quien,
                        }
                    )
                )
                self._audit("crear", adjunto.id, None, serialize_entity(adjunto))
        except Exception:
            for clave in subidas:
                try:
                    cliente.borrar(clave)
                except Exception:  # pragma: no cover - barrido de mejor esfuerzo
                    logger_adjuntos.warning(
                        "Quedó un objeto huérfano en R2 tras una subida fallida: %s", clave
                    )
            raise

        return self.listar(compra_id=compra_id, venta_id=venta_id)

    # ------------------------------------------------------------- compartir
    def compartir(self, adjunto_id: uuid.UUID) -> EnlaceCompartido:
        """Enlace de MÁS duración para UNA imagen, para mandarla por fuera.

        Queda en la auditoría con su caducidad: es un soporte de pago —con
        nombres, cuentas y montos— saliendo del sistema hacia un enlace que
        cualquiera que lo reciba puede reenviar. Que quede escrito quién lo
        repartió y hasta cuándo sirve.
        """
        adjunto = self._adjunto(adjunto_id)
        if not r2_configurado():
            raise BusinessError(MENSAJE_NO_CONFIGURADO, code="r2_no_configurado")

        dias = max(1, min(settings.R2_URL_COMPARTIR_DIAS, 7))
        segundos = dias * 24 * 60 * 60
        url = R2Client().enlace_firmado(
            clave=adjunto.object_key,
            segundos=segundos,
            nombre_descarga=adjunto.nombre_archivo,
        )
        expira = caducidad_utc(segundos)
        # Se audita el HECHO de compartir, nunca la URL: la URL lleva la firma
        # dentro, así que guardarla en la auditoría sería guardar el acceso.
        self._audit(
            "compartir",
            adjunto.id,
            None,
            {
                "nombre_archivo": adjunto.nombre_archivo,
                "compra_id": str(adjunto.compra_id) if adjunto.compra_id else None,
                "venta_id": str(adjunto.venta_id) if adjunto.venta_id else None,
                "expira": expira.isoformat(),
                "dias": dias,
            },
        )
        return EnlaceCompartido(
            url=url,
            nombre_archivo=adjunto.nombre_archivo,
            expira=expira,
            expira_texto=texto_caducidad(expira),
            dias=dias,
        )

    # ---------------------------------------------------------------- borrar
    def limpiar_de_documento(
        self, *, compra_id: uuid.UUID | None = None, venta_id: uuid.UUID | None = None
    ) -> int:
        """Se lleva los soportes cuando se borra la compra o la venta entera.

        Sin esto, borrar una compra dejaba sus fotos en el bucket para siempre:
        el documento ya no existe, así que nadie las puede ver ni borrar desde la
        aplicación, y el dueño sigue pagando ese almacenamiento sin saberlo.

        EN R2 ES DE MEJOR ESFUERZO, al revés que en `eliminar_adjunto`. Ahí el
        fallo tiene que detener la operación porque borrar el soporte ES la
        operación; aquí la operación es borrar la compra, y dejarla a medias
        —o negarla— porque el bucket no respondió sería peor: el dueño quedaría
        sin poder corregir un registro equivocado por un problema de red. Lo que
        no se pudo borrar queda en el log.

        Solo lo llaman los servicios de compra y venta, DESPUÉS de haber validado
        que el documento sí se puede borrar: si no, unos soportes se perderían
        por un borrado que al final no ocurre.
        """
        filas = self.repo.de_documento(compra_id=compra_id, venta_id=venta_id)
        if not filas:
            return 0
        cliente = R2Client() if r2_configurado() else None
        for fila in filas:
            if cliente is not None:
                try:
                    cliente.borrar(fila.object_key)
                except Exception:
                    logger_adjuntos.warning(
                        "Quedó un objeto en R2 al borrar el documento: %s", fila.object_key
                    )
            self.repo.soft_delete(fila, deleted_by=self.ctx.user_id)
        return len(filas)

    def eliminar_adjunto(self, adjunto_id: uuid.UUID) -> None:
        """Borra el soporte: PRIMERO el objeto en R2 y después la fila.

        En ese orden a propósito. Si R2 falla, se propaga el error y la fila
        sobrevive: el dueño ve que no se borró y vuelve a intentar. Al revés
        —fila primero— un fallo de R2 dejaría el archivo en el bucket sin nada
        que lo nombre: nadie podría verlo, nadie podría borrarlo, y se seguiría
        pagando su almacenamiento para siempre.
        """
        adjunto = self._adjunto(adjunto_id)
        if not r2_configurado():
            raise BusinessError(MENSAJE_NO_CONFIGURADO, code="r2_no_configurado")
        R2Client().borrar(adjunto.object_key)
        antes = serialize_entity(adjunto)
        self.repo.soft_delete(adjunto, deleted_by=self.ctx.user_id)
        self._audit("eliminar", adjunto.id, antes, serialize_entity(adjunto))


class ReventaResumenService:
    """Resumen del negocio de reventa (independiente de contabilidad)."""

    def __init__(self, db, ctx):
        self.db = db
        self.ctx = ctx
        self.compras = CompraQuesoRepository(db, ctx.empresa_id)
        self.ventas = VentaQuesoRepository(db, ctx.empresa_id)
        self.conversiones = ConversionBoronaRepository(db, ctx.empresa_id)
        self.saldos = SaldoAnteriorRepository(db, ctx.empresa_id)

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
        self,
        desde: date,
        hasta: date,
        *,
        valor_realizado_kilo: Decimal,
        neto_periodo: Decimal,
        kilos_comprados: Decimal,
    ) -> list[GananciaProductor]:
        """Ganancia ESTIMADA por productor (la UI debe decir que es estimación).

        Reparte el valor neto que dejaron las ventas del período (`neto_periodo`
        = ventas − gastos) entre los kilos que se le compraron a cada uno. Quien
        vendió más barato dejó más margen. Como el divisor son los kilos
        COMPRADOS, la suma de las filas cuadra con la ganancia neta del período:
        no hay forma de que el ranking contradiga la tarjeta de arriba.

        El reparto NO quantiza el valor por kilo antes de multiplicarlo (se hace
        kilos × neto / kilos_comprados y se redondea al final) y la diferencia de
        centavos se le da a la ÚLTIMA fila, igual que `costo_residuo` en
        _filas_por_producto: repartir con el `valor_realizado_kilo` ya redondeado
        a dos decimales desviaba la columna unos pesos de la ganancia del período.

        Las filas salen del conjunto HISTÓRICO de productores a los que se les
        debe, no solo de los que tuvieron compras EN EL PERÍODO: a quien se le
        compró en mayo y no se le ha pagado se le sigue debiendo en julio, y sin
        su fila la columna `por_pagar` no sumaba la tarjeta "Por pagar a
        productores", que sí es histórica. Esas filas van con kilos 0 y comprado
        0 —no tuvieron compras en el período, así que no inventan plata en el
        desglose— pero con TODA su deuda: la de las compras del sistema más la
        del libro anterior.

        La columna `por_pagar` INCLUYE el saldo del libro anterior de cada
        productor, porque la tarjeta "Por pagar a productores" también lo
        incluye: si solo lo sumara la tarjeta, la columna dejaría de cuadrar con
        ella. Los kilos, el costo y la ganancia NO se tocan: los saldos
        anteriores no son compras de este sistema.

        SIN COMPRAS EN EL PERÍODO no hay a quién repartirle y las filas se quedan
        con ganancia 0, que es lo correcto: a esa gente no se le compró nada este
        período. La ganancia salió de queso comprado ANTES (eso lo dice la fila
        "Salió de inventario anterior" del desglose por producto), así que
        repartirla entre los deudores históricos sería inventarles un negocio que
        no hicieron. La consecuencia hay que asumirla de frente: en ese caso la
        columna "Ganancia estimada" NO suma la tarjeta del período, y la pantalla
        tiene que decirlo en vez de prometer un cuadre que no existe.

        Cómo lo detecta el frontend: `kilos_comprados == 0` en el mismo resumen.
        NO se agrega un campo nuevo a propósito: sería un segundo nombre para un
        hecho que ya viaja en la respuesta y que además es EL MISMO divisor del
        reparto (`neto_periodo / kilos_comprados`), así que no puede desincronizarse
        del cálculo. Un `bool` aparte sí podría quedar en desacuerdo con los kilos
        el día que alguien toque una de las dos ramas.
        """
        # Deuda HISTÓRICA por productor, agrupada en Python con el mismo criterio
        # las dos: la de las compras de este sistema y la que quedó del libro
        # anterior. Se van sacando con pop() a medida que se emiten las filas del
        # período, así ningún saldo se reparte dos veces y lo que sobra al final
        # es exactamente lo que falta por mostrar.
        pendiente_sistema = _agrupar_pendientes(self.compras.pendiente_por_productor())
        pendiente_libro = _agrupar_pendientes(
            self.saldos.pendiente_por_tercero(TIPO_SALDO_PAGAR)
        )

        del_periodo = self.compras.por_productor(desde, hasta)
        # Valor neto realizado que le corresponde a los kilos de cada productor.
        # A la última fila se le suma la diferencia de redondeo para que el
        # reparto sume EXACTAMENTE neto_periodo (los kilos de las filas suman
        # kilos_comprados, así que sin compras en el período la lista va vacía).
        realizados = [
            (kilos * neto_periodo / kilos_comprados).quantize(DOS_DECIMALES)
            if kilos_comprados
            else CERO
            for _, _, kilos, _, _ in del_periodo
        ]
        # El ajuste del residuo solo tiene sentido si de verdad hubo kilos que
        # repartir: sin `kilos_comprados` el reparto es todo ceros y sumarle la
        # diferencia le daría TODO el neto del período a la última fila, que es
        # justo la plata que no le corresponde a nadie de la lista.
        if realizados and kilos_comprados:
            realizados[-1] += neto_periodo - sum(realizados, CERO)

        filas: list[GananciaProductor] = []
        # El 5.º campo de por_productor (su saldo por grupo de SQL) NO se usa
        # aquí: la deuda sale de `pendiente_sistema`, que agrupa las variantes de
        # escritura en Python y así ninguna queda por fuera ni contada dos veces.
        for (productor, compras, kilos, total_comprado, _), realizado in zip(
            del_periodo, realizados
        ):
            precio_promedio = (
                (total_comprado / kilos).quantize(DOS_DECIMALES) if kilos else CERO
            )
            clave = _clave_tercero(productor)
            _, del_sistema = pendiente_sistema.pop(clave, ("", CERO))
            _, del_libro = pendiente_libro.pop(clave, ("", CERO))
            filas.append(
                GananciaProductor(
                    productor=productor,
                    compras=compras,
                    kilos=kilos,
                    total_comprado=total_comprado,
                    precio_promedio=precio_promedio,
                    por_pagar=del_sistema + del_libro,
                    margen_por_kilo=valor_realizado_kilo - precio_promedio,
                    ganancia_estimada=realizado - total_comprado,
                )
            )
        # Productores a los que se les debe pero que NO tuvieron compras en el
        # período: de una compra vieja del sistema, del libro anterior, o de las
        # dos. Es el caso normal de quien acaba de migrar, y sin estas filas la
        # columna no sumaría lo que dice la tarjeta.
        sobrantes = list(pendiente_sistema) + [
            clave for clave in pendiente_libro if clave not in pendiente_sistema
        ]
        for clave in sobrantes:
            nombre_sistema, del_sistema = pendiente_sistema.get(clave, ("", CERO))
            nombre_libro, del_libro = pendiente_libro.get(clave, ("", CERO))
            filas.append(
                GananciaProductor(
                    # Manda la escritura de las compras: es la que ve el usuario
                    # en el resto del módulo.
                    productor=nombre_sistema or nombre_libro,
                    compras=0,
                    kilos=CERO,
                    total_comprado=CERO,
                    precio_promedio=CERO,
                    por_pagar=del_sistema + del_libro,
                    # Sin compras en el período no hay precio con qué comparar:
                    # poner el valor realizado sería inventarle un margen.
                    margen_por_kilo=CERO,
                    ganancia_estimada=CERO,
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
        # Cartera heredada del sistema anterior. Solo entra en lo que se debe
        # cobrar y pagar: NO tiene kilos, no se compró ni se vendió aquí, así que
        # no toca el inventario, ni los totales del período, ni la ganancia.
        por_cobrar_libro = self.saldos.pendiente(TIPO_SALDO_COBRAR)
        por_pagar_libro = self.saldos.pendiente(TIPO_SALDO_PAGAR)
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
            por_productor=self._filas_por_productor(
                desde,
                hasta,
                valor_realizado_kilo=valor_realizado_kilo,
                # El reparto se hace con el neto SIN redondear por kilo, así la
                # columna suma exacto la ganancia del período.
                neto_periodo=total_ventas - total_gastos,
                kilos_comprados=kilos_comprados,
            ),
            kilos_disponibles=kilos_hist_comprados - hist_queso_vendido - convertido,
            borona_disponible=borona_de_compras + a_borona - hist_borona_vendida,
            # Las dos tarjetas de cartera suman el sistema MÁS el libro anterior
            # (es la plata que de verdad se debe hoy), y enseguida va ese pedazo
            # por separado para poder mostrar el desglose.
            por_pagar_productores=por_pagar + por_pagar_libro,
            por_cobrar_clientes=por_cobrar + por_cobrar_libro,
            por_cobrar_libro_anterior=por_cobrar_libro,
            por_pagar_libro_anterior=por_pagar_libro,
        )

    def sugerencias(self) -> SugerenciasReventa:
        """Nombres ya usados de productores y clientes, para autocompletar.

        Incluye los terceros del LIBRO ANTERIOR, cada uno de SU lado: los de tipo
        'cobrar' son clientes y los de tipo 'pagar' productores (los dos lados no
        se mezclan). Un cliente que por ahora solo existe en el libro es justo el
        caso que motivó esa pantalla: sin él, el autocompletado no lo ofrecía, se
        volvía a teclear el nombre a mano y la deuda vieja quedaba separada de la
        nueva. Las dos listas van sin repetir el mismo tercero escrito de otra
        forma (ver _unir_nombres).
        """
        return SugerenciasReventa(
            productores=_unir_nombres(
                self.compras.nombres_productores(),
                self.saldos.nombres_terceros(TIPO_SALDO_PAGAR),
            ),
            clientes=_unir_nombres(
                self.ventas.nombres_clientes(),
                self.saldos.nombres_terceros(TIPO_SALDO_COBRAR),
            ),
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

        El `saldo` es TODO lo que el cliente debe: lo del sistema MÁS lo que
        traía del libro anterior, porque esa es la única cifra que le importa a
        él. `total_facturado` y `total_abonado` siguen siendo solo del sistema y
        lo del libro va aparte, así que el documento cuadra:
            (total_facturado - total_abonado) + libro_anterior_saldo = saldo
        """
        ventas = self.ventas.por_cliente(cliente, desde, hasta)
        # Los saldos del libro anterior se filtran por el MISMO rango que las
        # ventas, con la fecha original del documento viejo.
        saldos = self.saldos.por_tercero(TIPO_SALDO_COBRAR, cliente, desde, hasta)
        # Un cliente que solo arrastra deuda vieja SÍ tiene estado de cuenta: es
        # justo el caso de quien viene del sistema anterior y todavía no le ha
        # comprado nada aquí.
        if not ventas and not saldos:
            # Si tiene movimientos pero fuera del rango pedido, decirlo tal cual:
            # no es lo mismo que no ser cliente.
            if (desde or hasta) and (
                self.ventas.por_cliente(cliente)
                or self.saldos.por_tercero(TIPO_SALDO_COBRAR, cliente)
            ):
                raise NotFoundError(
                    "El cliente no tiene ventas ni saldos de la cuenta anterior "
                    "vigentes en el período consultado (lo anulado no cuenta)"
                )
            # Se aclara lo de las anuladas porque el usuario puede estar viendo en
            # pantalla una venta anulada de ese cliente y no entender el error.
            raise NotFoundError(
                "El cliente no tiene ventas registradas ni saldos de la cuenta "
                "anterior (lo anulado no cuenta para el estado de cuenta)"
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

        # Lo que traía debiendo del sistema anterior. Del saldo solo salen fecha,
        # concepto, valor, abonado y saldo: sus `observaciones` son la nota
        # interna de la quesera y no se copian (igual que en los abonos).
        filas_libro: list[EstadoCuentaSaldoAnterior] = []
        libro_total = CERO
        libro_abonado = CERO
        for saldo_anterior in saldos:
            valor = Decimal(saldo_anterior.valor_total)
            abonado = Decimal(saldo_anterior.abonado)
            libro_total += valor
            libro_abonado += abonado
            filas_libro.append(
                EstadoCuentaSaldoAnterior(
                    fecha=saldo_anterior.fecha,
                    concepto=saldo_anterior.concepto,
                    valor_total=valor,
                    abonado=abonado,
                    saldo=valor - abonado,
                )
            )
        libro_saldo = libro_total - libro_abonado

        return EstadoCuentaCliente(
            # El nombre que se muestra es el GUARDADO (el de su primera venta, o
            # el del saldo viejo si solo tiene deuda del libro), no el que llegó
            # por query: así sale bien escrito aunque se haya consultado en
            # minúsculas.
            cliente=ventas[0].cliente if ventas else saldos[0].tercero,
            desde=desde,
            hasta=hasta,
            emitido=date.today(),
            compras=len(filas),
            total_kilos=total_kilos,
            total_facturado=total_facturado,
            total_abonado=total_abonado,
            # TODO lo que debe: lo del sistema más lo del libro anterior.
            saldo=(total_facturado - total_abonado) + libro_saldo,
            ventas=filas,
            pagos=pagos,
            saldos_anteriores=filas_libro,
            libro_anterior_total=libro_total,
            libro_anterior_abonado=libro_abonado,
            libro_anterior_saldo=libro_saldo,
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
            # Los saldos del libro anterior, campo por campo: la observación del
            # saldo es interna y no puede llegar al documento del cliente.
            saldos_anteriores=[
                {
                    "fecha": s.fecha,
                    "concepto": s.concepto,
                    "valor_total": s.valor_total,
                    "abonado": s.abonado,
                    "saldo": s.saldo,
                }
                for s in datos.saldos_anteriores
            ],
            total_kilos=datos.total_kilos,
            total_facturado=datos.total_facturado,
            total_abonado=datos.total_abonado,
            libro_anterior_total=datos.libro_anterior_total,
            libro_anterior_abonado=datos.libro_anterior_abonado,
            libro_anterior_saldo=datos.libro_anterior_saldo,
            saldo=datos.saldo,
        )
        return pdf, _nombre_archivo_cliente(datos.cliente)

    # ------------------------------------- estado de cuenta DEL PRODUCTOR
    def estado_cuenta_productor(
        self, productor: str, desde: date | None = None, hasta: date | None = None
    ) -> EstadoCuentaProductor:
        """Cómo va la cuenta con un productor: lo que se le compró, lo que se le
        pagó y lo que se le debe. Es el espejo de `estado_cuenta`.

        CONFIDENCIALIDAD, AL CONTRARIO QUE EN EL DEL CLIENTE: esto se le entrega
        AL PRODUCTOR. De cada compra solo salen fecha, kilos netos, la borona que
        vino con el lote, precio por kilo, total, abonado y saldo, y de cada abono
        solo fecha y valor. NADA del lado de la venta (a cuánto se revendió su
        queso, el total de ventas, el precio promedio de venta, los márgenes, la
        ganancia, los gastos de venta ni los nombres de los clientes) sale por
        aquí, ni los saldos del libro de tipo 'cobrar', que son deudas de clientes.

        Sin rango de fechas cubre todo el histórico, que es lo que de verdad se le
        debe (el caso normal).

        OJO CON EL SIGNO: `saldo` positivo significa que LA QUESERA LE DEBE A ÉL.
        Es TODO lo que se le debe hoy: lo del sistema MÁS lo que se le venía
        debiendo del libro anterior. `total_comprado` y `total_pagado` siguen
        siendo solo del sistema y lo del libro va aparte, así que el documento
        cuadra:
            (total_comprado - total_pagado) + libro_anterior_saldo = saldo
        """
        compras = self.compras.del_productor(productor, desde, hasta)
        # Los saldos del libro anterior se filtran por el MISMO rango que las
        # compras, con la fecha original del documento viejo. SOLO los de tipo
        # 'pagar': los de tipo 'cobrar' son deudas de CLIENTES con la quesera y no
        # tienen nada que ver con un productor, ni siquiera si un tercero se llama
        # igual.
        saldos = self.saldos.por_tercero(TIPO_SALDO_PAGAR, productor, desde, hasta)
        # Un productor al que solo se le arrastra deuda vieja SÍ tiene estado de
        # cuenta: es justo el caso de quien viene del sistema anterior y todavía no
        # le ha vendido nada aquí.
        if not compras and not saldos:
            # Si tiene movimientos pero fuera del rango pedido, decirlo tal cual:
            # no es lo mismo que no haberle comprado nunca.
            if (desde or hasta) and (
                self.compras.del_productor(productor)
                or self.saldos.por_tercero(TIPO_SALDO_PAGAR, productor)
            ):
                raise NotFoundError(
                    "El productor no tiene compras ni saldos de la cuenta anterior "
                    "vigentes en el período consultado (lo anulado no cuenta)"
                )
            # Se aclara lo de las anuladas porque el usuario puede estar viendo en
            # pantalla una compra anulada de ese productor y no entender el error.
            raise NotFoundError(
                "El productor no tiene compras registradas ni saldos de la cuenta "
                "anterior (lo anulado no cuenta para el estado de cuenta)"
            )

        filas: list[EstadoCuentaCompra] = []
        pagos: list[EstadoCuentaPagoProductor] = []
        total_kilos = CERO
        total_comprado = CERO
        total_pagado = CERO
        for compra in compras:
            # Los kilos que salen son los NETOS: son los que se le pagan. La
            # borona va en su propio campo, sin sumar al total ni al valor.
            kilos = Decimal(compra.kilos_netos)
            valor = Decimal(compra.valor_total)
            abonado = Decimal(compra.abonado)
            total_kilos += kilos
            total_comprado += valor
            total_pagado += abonado
            filas.append(
                EstadoCuentaCompra(
                    fecha=compra.fecha,
                    kilos=kilos,
                    borona_kilos=Decimal(compra.borona_kilos or CERO),
                    precio_kilo=Decimal(compra.precio_kilo),
                    valor_total=valor,
                    abonado=abonado,
                    saldo=valor - abonado,
                    estado=compra.estado,
                )
            )
            # Los pagos son TODOS los abonos de TODAS sus compras, juntos: al
            # productor se le paga "a la cuenta", no le interesa a qué compra se
            # aplicó cada abono. Del abono solo salen fecha y valor: sus
            # `observaciones` son la nota interna de la quesera y NO se copian
            # aquí (ver EstadoCuentaPagoProductor).
            pagos += [
                EstadoCuentaPagoProductor(fecha=abono.fecha, valor=Decimal(abono.valor))
                for abono in compra.abonos
            ]
        pagos.sort(key=lambda pago: pago.fecha)

        # Lo que se le venía debiendo del sistema anterior. Del saldo solo salen
        # fecha, concepto, valor, abonado y saldo: sus `observaciones` son la nota
        # interna de la quesera y no se copian (igual que en los abonos).
        filas_libro: list[EstadoCuentaSaldoAnterior] = []
        libro_total = CERO
        libro_abonado = CERO
        for saldo_anterior in saldos:
            valor = Decimal(saldo_anterior.valor_total)
            abonado = Decimal(saldo_anterior.abonado)
            libro_total += valor
            libro_abonado += abonado
            filas_libro.append(
                EstadoCuentaSaldoAnterior(
                    fecha=saldo_anterior.fecha,
                    concepto=saldo_anterior.concepto,
                    valor_total=valor,
                    abonado=abonado,
                    saldo=valor - abonado,
                )
            )
        libro_saldo = libro_total - libro_abonado

        return EstadoCuentaProductor(
            # El nombre que se muestra es el GUARDADO (el de su primera compra, o
            # el del saldo viejo si solo tiene deuda del libro), no el que llegó
            # por query: así sale bien escrito aunque se haya consultado en
            # minúsculas.
            productor=compras[0].productor if compras else saldos[0].tercero,
            desde=desde,
            hasta=hasta,
            emitido=date.today(),
            compras=len(filas),
            total_kilos=total_kilos,
            total_comprado=total_comprado,
            total_pagado=total_pagado,
            saldos_anteriores=filas_libro,
            libro_anterior_total=libro_total,
            libro_anterior_abonado=libro_abonado,
            libro_anterior_saldo=libro_saldo,
            # TODO lo que se le debe: lo del sistema más lo del libro anterior.
            saldo=(total_comprado - total_pagado) + libro_saldo,
            compras_detalle=filas,
            pagos=pagos,
        )

    def _auditar_exportacion_productor(self, datos: EstadoCuentaProductor) -> None:
        """Deja registrada la SALIDA de datos, igual que en el del cliente:
        exportar la cuenta histórica de un productor es entregar información
        afuera y tiene que quedar en la bitácora (quién la sacó, de qué productor
        y de qué rango).
        """
        from app.modules.auditoria.models import Auditoria

        self.db.add(
            Auditoria(
                empresa_id=self.ctx.empresa_id,
                usuario_id=self.ctx.user_id,
                ip=self.ctx.ip,
                modulo="reventa",
                accion="exportar",
                entidad="EstadoCuentaProductor",
                entidad_id=None,
                antes=None,
                despues={
                    "documento": "estado_cuenta_productor_pdf",
                    "productor": datos.productor,
                    "desde": datos.desde.isoformat() if datos.desde else None,
                    "hasta": datos.hasta.isoformat() if datos.hasta else None,
                    "compras": datos.compras,
                    "saldo": float(datos.saldo),
                },
            )
        )

    def estado_cuenta_productor_pdf(
        self, productor: str, desde: date | None = None, hasta: date | None = None
    ) -> tuple[bytes, str]:
        """Estado de cuenta del productor en PDF, para entregárselo y cuadrar
        cuentas con él. Es un documento INTERNO: sin numeración consecutiva, sin
        resolución de la DIAN y sin IVA (no es una factura)."""
        datos = self.estado_cuenta_productor(productor, desde, hasta)
        self._auditar_exportacion_productor(datos)
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

        pdf = build_estado_cuenta_productor_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            productor=datos.productor,
            emitido=datos.emitido.strftime("%d/%m/%Y"),
            periodo=periodo,
            compras=datos.compras,
            # Se arman los campos uno por uno a propósito: lo que no se nombre
            # aquí NO puede llegar al PDF que ve el productor.
            compras_detalle=[
                {
                    "fecha": c.fecha,
                    "kilos": c.kilos,
                    "borona_kilos": c.borona_kilos,
                    "precio_kilo": c.precio_kilo,
                    "valor_total": c.valor_total,
                    "abonado": c.abonado,
                    "saldo": c.saldo,
                }
                for c in datos.compras_detalle
            ],
            pagos=[{"fecha": p.fecha, "valor": p.valor} for p in datos.pagos],
            # Los saldos del libro anterior, campo por campo: la observación del
            # saldo es interna y no puede llegar al documento del productor.
            saldos_anteriores=[
                {
                    "fecha": s.fecha,
                    "concepto": s.concepto,
                    "valor_total": s.valor_total,
                    "abonado": s.abonado,
                    "saldo": s.saldo,
                }
                for s in datos.saldos_anteriores
            ],
            total_kilos=datos.total_kilos,
            total_comprado=datos.total_comprado,
            total_pagado=datos.total_pagado,
            libro_anterior_total=datos.libro_anterior_total,
            libro_anterior_abonado=datos.libro_anterior_abonado,
            libro_anterior_saldo=datos.libro_anterior_saldo,
            saldo=datos.saldo,
        )
        return pdf, _nombre_archivo_productor(datos.productor)


# --------------------------------------------------------------- temporadas
class TemporadaService(BaseService[Temporada]):
    """Temporadas: ciclos de compra y reventa con nombre y fechas.

    NO guarda ninguna cifra de plata. Las cifras de cada temporada salen de
    `ReventaResumenService.resumen(fecha_inicio, fecha_fin)`, o sea del mismo
    código que pinta el Resumen. Es la decisión central de este módulo y está
    explicada en el modelo `Temporada`: así funciona hacia atrás (se registra hoy
    una temporada de marzo y sus cifras aparecen solas) y nunca queda una cifra
    guardada distinta de la que muestra el Resumen para las mismas fechas.

    Las dos reglas que se validan, y por qué las dos son de plata y no de forma:

    - NO SE SOLAPAN. Si dos temporadas se cruzaran, los mismos kilos y la misma
      plata caerían en las dos y la suma de las ganancias por temporada no daría
      la ganancia del negocio.
    - SOLO UNA ABIERTA. La abierta es la que está corriendo y se calcula hasta
      hoy; con dos abiertas las dos llegarían hasta hoy y se cruzarían por
      definición.
    """

    repository_cls = TemporadaRepository
    modulo = "reventa"

    # ------------------------------------------------------------ validación
    def _validar_rango(
        self, inicio: date, fin: date | None, excluir_id: uuid.UUID | None = None
    ) -> None:
        if fin is not None and fin < inicio:
            raise BusinessError(
                "La temporada no puede terminar antes de empezar: empieza el "
                f"{inicio.strftime('%d/%m/%Y')} y terminaría el "
                f"{fin.strftime('%d/%m/%Y')}"
            )
        if fin is None:
            otra_abierta = self.repo.abierta(excluir_id=excluir_id)
            if otra_abierta is not None:
                raise BusinessError(
                    f"Ya hay una temporada abierta: {otra_abierta.nombre}. "
                    "Ciérrela antes de abrir otra."
                )
        cruzada = self.repo.solapada(inicio, fin, excluir_id=excluir_id)
        if cruzada is not None:
            hasta = (
                cruzada.fecha_fin.strftime("%d/%m/%Y") if cruzada.fecha_fin else "sin cerrar"
            )
            raise BusinessError(
                f"Se cruza con la temporada {cruzada.nombre} "
                f"({cruzada.fecha_inicio.strftime('%d/%m/%Y')} - {hasta}). "
                "Las temporadas no se pueden solapar, porque los mismos kilos y "
                "la misma plata quedarían contados en las dos."
            )

    def validar_crear(self, data: dict[str, Any]) -> None:
        self._validar_rango(data["fecha_inicio"], data.get("fecha_fin"))

    def validar_actualizar(self, obj: Temporada, data: dict[str, Any]) -> None:
        # Con exclude_unset, lo que no venga en el payload se queda como está: hay
        # que validar el rango RESULTANTE, no solo lo que llegó. Cambiar solo el
        # inicio también puede provocar un cruce.
        inicio = data.get("fecha_inicio", obj.fecha_inicio)
        fin = data["fecha_fin"] if "fecha_fin" in data else obj.fecha_fin
        self._validar_rango(inicio, fin, excluir_id=obj.id)

    # -------------------------------------------------------- abrir y cerrar
    def cerrar(self, entity_id: uuid.UUID, fecha_fin: date | None = None) -> Temporada:
        """Le pone fecha de cierre. Cerrar NO congela las cifras: lo que se cierra
        es el ciclo del queso, no el libro (ver el modelo)."""
        temporada = self.repo.get_or_fail(entity_id)
        if temporada.fecha_fin is not None:
            raise BusinessError(
                f"La temporada {temporada.nombre} ya está cerrada "
                f"({temporada.fecha_fin.strftime('%d/%m/%Y')})"
            )
        return self.actualizar(entity_id, {"fecha_fin": fecha_fin or date.today()})

    def reabrir(self, entity_id: uuid.UUID) -> Temporada:
        """Quita la fecha de cierre, para cuando se cerró por equivocación.

        Solo se puede si no hay otra abierta y si al quedar hasta hoy no se cruza
        con ninguna posterior: eso lo comprueba la validación normal.
        """
        temporada = self.repo.get_or_fail(entity_id)
        if temporada.fecha_fin is None:
            raise BusinessError(f"La temporada {temporada.nombre} ya está abierta")
        return self.actualizar(entity_id, {"fecha_fin": None})

    # ---------------------------------------------------------------- panel
    def _resumen_de(
        self,
        temporada: Temporada,
        hoy: date,
        resumenes: "ReventaResumenService",
        compras: CompraQuesoRepository,
        ventas: VentaQuesoRepository,
    ) -> TemporadaResumen:
        # La abierta se calcula hasta HOY. Si además empezara en el futuro (se
        # puede dejar programada), el rango quedaría al revés y las consultas
        # devolverían ceros mudos: se acota al propio inicio, que da un día con
        # todo en cero, que es la verdad.
        fin = temporada.fecha_fin or max(hoy, temporada.fecha_inicio)
        r = resumenes.resumen(temporada.fecha_inicio, fin)
        por_cobrar = ventas.pendiente_periodo(temporada.fecha_inicio, fin)
        por_pagar = compras.pendiente_periodo(temporada.fecha_inicio, fin)
        return TemporadaResumen(
            id=temporada.id,
            nombre=temporada.nombre,
            fecha_inicio=temporada.fecha_inicio,
            fecha_fin=fin,
            abierta=temporada.fecha_fin is None,
            dias=(fin - temporada.fecha_inicio).days + 1,
            notas=temporada.notas,
            kilos_comprados=r.kilos_comprados,
            kilos_vendidos=r.kilos_vendidos,
            kilos_borona_vendidos=r.kilos_borona_vendidos,
            kilos_a_borona=r.kilos_a_borona,
            kilos_merma=r.kilos_merma,
            kilos_pendientes=r.kilos_pendientes,
            total_compras=r.total_compras,
            total_ventas=r.total_ventas,
            total_gastos=r.total_gastos,
            ganancia=r.ganancia_estimada,
            margen_por_kilo=r.margen_por_kilo,
            precio_promedio_compra=r.precio_promedio_compra,
            precio_promedio_venta=r.precio_promedio_venta,
            por_cobrar=por_cobrar,
            por_pagar=por_pagar,
            # Los kilos pendientes pueden salir NEGATIVOS (se vendió queso que
            # venía de una temporada anterior): eso no es queso por vender, así
            # que "cerrada de verdad" mira que no SOBRE nada, no que dé cero justo.
            cerrada_de_verdad=(
                r.kilos_pendientes <= CERO and por_cobrar <= CERO and por_pagar <= CERO
            ),
        )

    def panel(self) -> TemporadasPanel:
        hoy = date.today()
        resumenes = ReventaResumenService(self.db, self.ctx)
        compras = CompraQuesoRepository(self.db, self.ctx.empresa_id)
        ventas = VentaQuesoRepository(self.db, self.ctx.empresa_id)
        temporadas = self.repo.vigentes()
        filas = [self._resumen_de(t, hoy, resumenes, compras, ventas) for t in temporadas]

        # Los totales son la SUMA EXACTA de las filas de la lista. Se suman las
        # filas ya calculadas y no se vuelve a consultar el rango completo: si se
        # consultara aparte, con huecos entre temporadas el total daría más que la
        # suma de la lista y el desglose dejaría de cuadrar. Los huecos se avisan
        # por separado con dias_sin_temporada.
        mejor = peor = None
        if filas:
            mejor = max(filas, key=lambda f: f.ganancia).nombre
            peor = min(filas, key=lambda f: f.ganancia).nombre

        # Huecos: días con compras o ventas que no caen en ninguna temporada.
        cubiertos = [
            (t.fecha_inicio, t.fecha_fin or max(hoy, t.fecha_inicio)) for t in temporadas
        ]
        sin_temporada = sum(
            1
            for f in self.repo.fechas_con_movimiento()
            if not any(inicio <= f <= fin for inicio, fin in cubiertos)
        )

        ultimo = self.repo.ultimo_cierre()
        return TemporadasPanel(
            temporadas=filas,
            total_ganancia=sum((f.ganancia for f in filas), CERO),
            total_kilos_comprados=sum((f.kilos_comprados for f in filas), CERO),
            total_ventas=sum((f.total_ventas for f in filas), CERO),
            total_compras=sum((f.total_compras for f in filas), CERO),
            mejor=mejor,
            peor=peor,
            dias_sin_temporada=sin_temporada,
            proximo_inicio=(ultimo + timedelta(days=1)) if ultimo else None,
        )


# -------------------------------------------------------------------- lotes
class LoteService:
    """Ganancia por LOTE de compra: qué dejó cada tanda de queso que se compró.

    Un lote son todas las compras de queso de una misma fecha. Toda la mecánica
    del reparto FIFO y del costeo está en `app.modules.reventa.lotes`, que es una
    función pura sin base de datos para poder probarla con casos armados a mano.
    Aquí solo se leen los eventos, se llama al reparto y se arma la respuesta.

    OJO: el reparto se hace SIEMPRE sobre toda la historia, aunque se pidan solo
    los lotes de un mes. Para saber qué había en inventario el 25 de julio hay que
    haber procesado lo de antes; si se filtrara la consulta, el inventario inicial
    sería inventado y las ventas de los primeros días quedarían "sin lote". El
    filtro de fechas se aplica al final, a qué lotes se muestran.
    """

    modulo = "reventa"

    def __init__(self, db, ctx):
        self.db = db
        self.ctx = ctx
        self.compras = CompraQuesoRepository(db, ctx.empresa_id)
        self.ventas = VentaQuesoRepository(db, ctx.empresa_id)
        self.ajustes = ConversionBoronaRepository(db, ctx.empresa_id)

    def _reparto(self):
        """El reparto FIFO completo, que es la base de todo lo de aquí abajo.

        Se calcula SIEMPRE sobre toda la historia, nunca sobre un filtro: para
        saber qué había en bodega un día hay que haber procesado lo de antes.
        """
        compras = [
            CompraEvento(
                fecha=fila[0], orden=indice, productor=fila[2],
                kilos=Decimal(fila[3] or 0), borona_kilos=Decimal(fila[4] or 0),
                precio_kilo=Decimal(fila[7] or 0),
                valor_total=Decimal(fila[5] or 0), saldo=Decimal(fila[6] or 0),
            )
            for indice, fila in enumerate(self.compras.eventos_para_lotes())
        ]
        ventas = [
            VentaEvento(
                fecha=fila[0], orden=indice, cliente=fila[6], tipo=fila[2],
                kilos=Decimal(fila[3] or 0), precio_kilo=Decimal(fila[7] or 0),
                valor_total=Decimal(fila[4] or 0), gasto_monto=Decimal(fila[5] or 0),
            )
            for indice, fila in enumerate(self.ventas.eventos_para_lotes())
        ]
        ajustes = [
            AjusteEvento(
                fecha=fila[0], orden=indice, kilos=Decimal(fila[2] or 0), destino=fila[3]
            )
            for indice, fila in enumerate(self.ajustes.eventos_para_lotes())
        ]

        return repartir_lotes(compras, ventas, ajustes)

    def panel(self, desde: date | None = None, hasta: date | None = None) -> LotesPanel:
        reparto = self._reparto()

        # El filtro recorta lo que se MUESTRA, no lo que se calculó
        visibles = [
            lote
            for lote in reparto.lotes
            if (desde is None or lote.fecha >= desde) and (hasta is None or lote.fecha <= hasta)
        ]

        filas = [self._fila(lote) for lote in visibles]
        mejor = peor = None
        if visibles:
            mejor = max(visibles, key=lambda l: l.ganancia).fecha
            peor = min(visibles, key=lambda l: l.ganancia).fecha

        return LotesPanel(
            lotes=filas,
            total_ganancia=sum((f.ganancia for f in filas), CERO),
            total_kilos_comprados=sum((f.kilos_comprados for f in filas), CERO),
            total_costo=sum((f.costo_total for f in filas), CERO),
            total_ingresos=sum((f.ingresos for f in filas), CERO),
            total_por_pagar=sum((f.por_pagar for f in filas), CERO),
            total_kilos_sin_vender=sum((f.kilos_sin_vender for f in filas), CERO),
            total_costo_sin_vender=sum((f.costo_sin_vender for f in filas), CERO),
            mejor=mejor,
            peor=peor,
            # Estos tres son del reparto COMPLETO y no del filtro: son un aviso de
            # que falta cargar una compra, y esconderlo al cambiar de mes sería
            # justo lo contrario de lo que se busca.
            kilos_sin_lote=reparto.kilos_sin_lote,
            borona_sin_lote=reparto.borona_sin_lote,
            ingreso_sin_lote=reparto.ingreso_sin_lote,
        )

    def ganancia_por_dia(self, desde: date, hasta: date) -> GananciaPorDia:
        """Lo que se ganó DE VERDAD entre dos fechas, día por día.

        Ojo con no confundirlo con la ganancia del resumen, que hace "ventas del
        período menos compras del período". Eso mezcla dos cosas distintas: un
        mes en que se compró mucho y se vendió poco sale en pérdida aunque no se
        haya perdido nada — el queso está en la bodega, no desaparecido.

        Esto es otra cuenta: de cada venta hecha en esos días se toma lo que
        entró, lo que había costado ESE queso en concreto (el reparto FIFO ya lo
        sabe, no es un promedio) y el flete que se pagó por despacharlo. Eso es
        lo que se ganó ese día, y por eso los días suman el total sin sobrar ni
        faltar un peso.

        Las compras de esos días no restan aquí: comprar no es gastar, es
        cambiar plata por queso. Se ve aparte, en la cartera y en el inventario.
        """
        reparto = self._reparto()
        por_dia: dict[date, dict[str, Decimal]] = {}
        for lote in reparto.lotes:
            for v in lote.detalle_ventas:
                if v.fecha < desde or v.fecha > hasta:
                    continue
                d = por_dia.setdefault(
                    v.fecha,
                    {"kilos": CERO, "ingresos": CERO, "costo": CERO, "gastos": CERO},
                )
                d["kilos"] += v.kilos
                d["ingresos"] += v.ingreso
                d["costo"] += v.costo
                d["gastos"] += v.gasto

        dias = [
            GananciaDia(
                fecha=fecha,
                kilos=v["kilos"],
                ingresos=v["ingresos"],
                costo=v["costo"],
                gastos=v["gastos"],
                ganancia=v["ingresos"] - v["costo"] - v["gastos"],
            )
            for fecha, v in sorted(por_dia.items())
        ]
        return GananciaPorDia(
            desde=desde,
            hasta=hasta,
            dias=dias,
            kilos=sum((d.kilos for d in dias), CERO),
            ingresos=sum((d.ingresos for d in dias), CERO),
            costo=sum((d.costo for d in dias), CERO),
            gastos=sum((d.gastos for d in dias), CERO),
            # El total es la SUMA de los días, no una cuenta aparte: así el
            # desglose cuadra por construcción y no por casualidad.
            ganancia=sum((d.ganancia for d in dias), CERO),
        )

    @staticmethod
    def _fila(lote: LoteCalculado) -> LoteResumen:
        kilos_vendidos_totales = lote.kilos_vendidos + lote.borona_vendida
        return LoteResumen(
            fecha=lote.fecha,
            productores=lote.productores,
            compras=lote.compras,
            kilos_comprados=lote.kilos_comprados,
            costo_total=_dinero(lote.costo_total),
            costo_kilo=(
                _dinero(lote.costo_total / lote.kilos_comprados)
                if lote.kilos_comprados > CERO
                else CERO
            ),
            por_pagar=_dinero(lote.por_pagar),
            borona_recibida=lote.borona_recibida,
            kilos_vendidos=lote.kilos_vendidos,
            kilos_a_borona=lote.kilos_a_borona,
            kilos_merma=lote.kilos_merma,
            kilos_sin_vender=lote.kilos_sin_vender,
            borona_vendida=lote.borona_vendida,
            borona_sin_vender=lote.borona_sin_vender,
            ingreso_queso=_dinero(lote.ingreso_queso),
            ingreso_borona=_dinero(lote.ingreso_borona),
            ingresos=_dinero(lote.ingresos),
            gastos=_dinero(lote.gastos),
            costo_vendido=_dinero(lote.costo_vendido),
            costo_borona_vendida=_dinero(lote.costo_borona_vendida),
            costo_merma=_dinero(lote.costo_merma),
            costo_sin_vender=_dinero(lote.costo_sin_vender),
            ganancia=_dinero(lote.ganancia),
            margen_kilo=(
                _dinero(lote.ganancia / kilos_vendidos_totales)
                if kilos_vendidos_totales > CERO
                else CERO
            ),
            precio_venta_kilo=(
                _dinero(lote.ingreso_queso / lote.kilos_vendidos)
                if lote.kilos_vendidos > CERO
                else CERO
            ),
            cerrado=lote.cerrado,
            detalle_compras=[
                CompraDelLoteRead(
                    productor=c.productor,
                    kilos=c.kilos,
                    borona_recibida=c.borona_recibida,
                    precio_kilo=_dinero(c.precio_kilo),
                    valor_total=_dinero(c.valor_total),
                    saldo=_dinero(c.saldo),
                    kilos_vendidos=c.kilos_vendidos,
                    kilos_a_borona=c.kilos_a_borona,
                    kilos_merma=c.kilos_merma,
                    kilos_sin_vender=c.kilos_sin_vender,
                    borona_vendida=c.borona_vendida,
                    borona_sin_vender=c.borona_sin_vender,
                    ingresos=_dinero(c.ingresos),
                    gastos=_dinero(c.gastos),
                    costo_realizado=_dinero(c.costo_realizado),
                    costo_sin_vender=_dinero(c.costo_sin_vender),
                    ganancia=_dinero(c.ganancia),
                    margen_kilo=(
                        _dinero(c.ganancia / (c.kilos_vendidos + c.borona_vendida))
                        if (c.kilos_vendidos + c.borona_vendida) > CERO
                        else CERO
                    ),
                )
                for c in lote.detalle_compras
            ],
            detalle_ventas=[
                VentaDelLoteRead(
                    fecha=v.fecha,
                    cliente=v.cliente,
                    tipo=v.tipo,
                    kilos=v.kilos,
                    kilos_venta=v.kilos_venta,
                    precio_kilo=_dinero(v.precio_kilo),
                    ingreso=_dinero(v.ingreso),
                    gasto=_dinero(v.gasto),
                    costo=_dinero(v.costo),
                    ganancia=_dinero(v.ganancia),
                    partida=v.partida,
                )
                for v in lote.detalle_ventas
            ],
        )
