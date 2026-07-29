"""Ganancia POR LOTE de compra: qué dejó cada tanda de queso que se compró.

Un LOTE son todas las compras de queso de una misma FECHA. Así lo ve el usuario:
"la compra del 25" es un lote y "las compras del 18" es otro, aunque cada una
tenga varios productores.

EL PROBLEMA Y CÓMO SE RESUELVE
------------------------------
Las ventas no dicen de qué lote salió el queso: se registra "vendí 620 kg a
$21.500" y nada más. Así que hay que repartirlas, y la regla es FIFO: **el queso
se vende del lote más viejo primero**. No es una convención de contabilidad
traída de otra parte, es lo que pasa en la bodega: el queso es perecedero y se
saca el más viejo. La misma regla se aplica a los ajustes (lo que se pasa a
borona y lo que se pierde como merma).

Consecuencia importante y honesta: si se registran las ventas con fechas
desordenadas o si se vende más de lo comprado, el reparto lo dice en vez de
esconderlo (ver `kilos_sin_lote`).

CÓMO SE COSTEA
--------------
Cada lote tiene su propio costo por kilo (valor pagado / kilos comprados), que es
distinto entre lotes: en los datos reales el del 18 salió a $16.519/kg y el del
25 a $18.395/kg. Cuando una venta se lleva K kilos de un lote, a ese lote se le
carga K × su costo por kilo.

La borona tiene DOS orígenes y se costea distinto según cuál:
- La que llega con el lote y no se paga: costo CERO. Lo que se venda de ella es
  ganancia pura del lote.
- La que sale de pasar queso a borona: se lleva el costo del queso del que salió.
  Si no, pasar queso a borona haría desaparecer plata del lote.

LA CUENTA QUE TIENE QUE CUADRAR
-------------------------------
Cada peso pagado por un lote termina en exactamente uno de cuatro sitios:
costo de lo vendido, costo de lo que se perdió como merma, costo de la borona que
todavía no se ha vendido, o costo de lo que sigue en inventario. La función
`repartir_lotes` garantiza esa igualdad peso a peso: el último trozo de cada
reparto absorbe el residuo del redondeo, porque el usuario cuadra estas cifras
a mano con calculadora.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

CERO = Decimal("0")
CENTAVOS = Decimal("0.01")
KILOS = Decimal("0.01")

DESTINO_BORONA = "borona"
DESTINO_MERMA = "merma"
TIPO_QUESO = "queso"
TIPO_BORONA = "borona"


# --------------------------------------------------------------------- entrada
@dataclass(frozen=True)
class CompraEvento:
    """Una compra de queso a un productor."""

    fecha: date
    orden: int  # para desempatar dentro del mismo día (created_at)
    productor: str
    kilos: Decimal  # kilos_netos: los que se pagan
    borona_kilos: Decimal  # la que llega con el lote y no se paga
    valor_total: Decimal
    saldo: Decimal  # lo que falta pagarle por esta compra


@dataclass(frozen=True)
class VentaEvento:
    fecha: date
    orden: int
    tipo: str  # 'queso' | 'borona'
    kilos: Decimal
    valor_total: Decimal
    gasto_monto: Decimal


@dataclass(frozen=True)
class AjusteEvento:
    """Queso que sale del inventario sin venderse: a borona o a merma."""

    fecha: date
    orden: int
    kilos: Decimal
    destino: str  # 'borona' | 'merma'


# ---------------------------------------------------------------------- salida
@dataclass
class LoteCalculado:
    fecha: date
    productores: list[str] = field(default_factory=list)
    compras: int = 0
    # Lo comprado
    kilos_comprados: Decimal = CERO
    costo_total: Decimal = CERO
    por_pagar: Decimal = CERO
    borona_recibida: Decimal = CERO
    # A dónde fue el queso del lote
    kilos_vendidos: Decimal = CERO
    kilos_a_borona: Decimal = CERO
    kilos_merma: Decimal = CERO
    kilos_sin_vender: Decimal = CERO
    # Borona del lote (la recibida gratis + la que salió de su queso)
    borona_vendida: Decimal = CERO
    borona_sin_vender: Decimal = CERO
    # Plata
    ingreso_queso: Decimal = CERO
    ingreso_borona: Decimal = CERO
    gastos: Decimal = CERO
    costo_vendido: Decimal = CERO  # costo de los kilos de queso que se vendieron
    costo_borona_vendida: Decimal = CERO  # solo la borona que venía de queso
    costo_merma: Decimal = CERO
    costo_sin_vender: Decimal = CERO  # queso + borona que siguen en inventario

    @property
    def ingresos(self) -> Decimal:
        return self.ingreso_queso + self.ingreso_borona

    @property
    def costo_realizado(self) -> Decimal:
        """Lo que costó lo que ya salió del lote (vendido + perdido)."""
        return self.costo_vendido + self.costo_borona_vendida + self.costo_merma

    @property
    def ganancia(self) -> Decimal:
        """Ganancia de lo que YA se realizó del lote.

        No se le resta el costo de lo que sigue en inventario: ese queso no se ha
        vendido, no es una pérdida todavía, y restarlo haría que un lote recién
        comprado apareciera con una pérdida enorme el mismo día. Lo que sí se le
        resta es la merma, que sí es plata perdida de este lote.
        """
        return self.ingresos - self.costo_realizado - self.gastos

    @property
    def cerrado(self) -> bool:
        """No queda nada del lote por vender (ni queso ni borona)."""
        return self.kilos_sin_vender <= CERO and self.borona_sin_vender <= CERO


@dataclass
class RepartoLotes:
    lotes: list[LoteCalculado] = field(default_factory=list)
    # Kilos vendidos (o ajustados) que no encontraron lote de dónde salir: se
    # vendió más de lo comprado, o se vendió antes de la primera compra
    # registrada. NO se esconden: significan que falta cargar una compra.
    kilos_sin_lote: Decimal = CERO
    borona_sin_lote: Decimal = CERO
    ingreso_sin_lote: Decimal = CERO


# ------------------------------------------------------------ implementación
@dataclass
class _Trozo:
    """Un pedazo de inventario con su dueño y su costo por kilo."""

    lote: LoteCalculado
    kilos: Decimal
    costo_kilo: Decimal


def _q(valor: Decimal, paso: Decimal = CENTAVOS) -> Decimal:
    return valor.quantize(paso)


def _consumir(
    cola: list[_Trozo], kilos_pedidos: Decimal
) -> tuple[list[tuple[_Trozo, Decimal]], Decimal]:
    """Saca `kilos_pedidos` de la cola, del trozo más viejo al más nuevo.

    Devuelve (asignaciones, faltante). El faltante es lo que no había: no se
    inventa inventario para que cuadre.
    """
    asignados: list[tuple[_Trozo, Decimal]] = []
    restante = kilos_pedidos
    for trozo in cola:
        if restante <= CERO:
            break
        if trozo.kilos <= CERO:
            continue
        toma = min(trozo.kilos, restante)
        trozo.kilos -= toma
        restante -= toma
        asignados.append((trozo, toma))
    return asignados, restante


def _repartir_plata(
    asignados: list[tuple[_Trozo, Decimal]], total: Decimal, kilos_totales: Decimal
) -> list[tuple[_Trozo, Decimal]]:
    """Reparte `total` entre las asignaciones, en proporción a sus kilos.

    El ÚLTIMO trozo se lleva el residuo, de modo que la suma de los pedazos sea
    exactamente `total`. Sin eso, repartir $17.122.600 entre tres lotes puede dar
    un peso de diferencia, y esa diferencia hace que la columna no sume la cifra
    grande —que es justo lo que el usuario verifica a mano—.
    """
    if not asignados or kilos_totales <= CERO:
        return []
    partes: list[tuple[_Trozo, Decimal]] = []
    acumulado = CERO
    for indice, (trozo, kilos) in enumerate(asignados):
        if indice == len(asignados) - 1:
            parte = total - acumulado
        else:
            parte = _q(total * kilos / kilos_totales)
            acumulado += parte
        partes.append((trozo, parte))
    return partes


def repartir_lotes(
    compras: list[CompraEvento],
    ventas: list[VentaEvento],
    ajustes: list[AjusteEvento],
) -> RepartoLotes:
    """Reparte ventas y ajustes entre los lotes de compra, FIFO.

    Los eventos se ordenan por (fecha, orden). Dentro de un mismo día las compras
    van ANTES de las ventas: lo normal es comprar en la mañana y despachar en la
    tarde, y si la venta se procesara primero, el queso comprado ese mismo día no
    estaría disponible y la venta se iría a "sin lote" sin razón.
    """
    reparto = RepartoLotes()
    por_fecha: dict[date, LoteCalculado] = {}
    cola_queso: list[_Trozo] = []
    cola_borona: list[_Trozo] = []

    # (fecha, prioridad, orden): prioridad 0 = compra, 1 = ajuste, 2 = venta.
    # El ajuste va antes de la venta del mismo día porque un ajuste de merma suele
    # registrarse al pesar el despacho, o sea antes de darlo por vendido.
    eventos: list[tuple[date, int, int, str, object]] = []
    for c in compras:
        eventos.append((c.fecha, 0, c.orden, "compra", c))
    for a in ajustes:
        eventos.append((a.fecha, 1, a.orden, "ajuste", a))
    for v in ventas:
        eventos.append((v.fecha, 2, v.orden, "venta", v))
    eventos.sort(key=lambda e: (e[0], e[1], e[2]))

    for _, _, _, clase, evento in eventos:
        if clase == "compra":
            compra: CompraEvento = evento  # type: ignore[assignment]
            lote = por_fecha.get(compra.fecha)
            if lote is None:
                lote = LoteCalculado(fecha=compra.fecha)
                por_fecha[compra.fecha] = lote
                reparto.lotes.append(lote)
            lote.compras += 1
            if compra.productor not in lote.productores:
                lote.productores.append(compra.productor)
            lote.kilos_comprados += compra.kilos
            lote.costo_total += compra.valor_total
            lote.por_pagar += compra.saldo
            lote.borona_recibida += compra.borona_kilos
            if compra.kilos > CERO:
                cola_queso.append(
                    _Trozo(
                        lote=lote,
                        kilos=compra.kilos,
                        costo_kilo=compra.valor_total / compra.kilos,
                    )
                )
            if compra.borona_kilos > CERO:
                # Costo cero: la borona llega con el lote y no se paga
                cola_borona.append(
                    _Trozo(lote=lote, kilos=compra.borona_kilos, costo_kilo=CERO)
                )

        elif clase == "ajuste":
            ajuste: AjusteEvento = evento  # type: ignore[assignment]
            asignados, faltante = _consumir(cola_queso, ajuste.kilos)
            for trozo, kilos in asignados:
                costo = _q(kilos * trozo.costo_kilo)
                if ajuste.destino == DESTINO_MERMA:
                    trozo.lote.kilos_merma += kilos
                    trozo.lote.costo_merma += costo
                else:
                    trozo.lote.kilos_a_borona += kilos
                    # La borona que sale de queso ARRASTRA el costo de ese queso:
                    # si entrara con costo cero, la plata del lote desaparecería
                    cola_borona.append(
                        _Trozo(lote=trozo.lote, kilos=kilos, costo_kilo=trozo.costo_kilo)
                    )
            if faltante > CERO:
                reparto.kilos_sin_lote += faltante

        else:
            venta: VentaEvento = evento  # type: ignore[assignment]
            cola = cola_borona if venta.tipo == TIPO_BORONA else cola_queso
            asignados, faltante = _consumir(cola, venta.kilos)
            kilos_cubiertos = venta.kilos - faltante
            ingresos = _repartir_plata(asignados, venta.valor_total, venta.kilos)
            gastos = _repartir_plata(asignados, venta.gasto_monto, venta.kilos)
            # Si parte de la venta no tuvo lote, esa parte de la plata tampoco es
            # de ningún lote: se reparte solo lo que corresponde a lo cubierto.
            if faltante > CERO and venta.kilos > CERO:
                proporcion = kilos_cubiertos / venta.kilos
                cubierto_valor = _q(venta.valor_total * proporcion)
                cubierto_gasto = _q(venta.gasto_monto * proporcion)
                ingresos = _repartir_plata(asignados, cubierto_valor, kilos_cubiertos)
                gastos = _repartir_plata(asignados, cubierto_gasto, kilos_cubiertos)
                reparto.ingreso_sin_lote += venta.valor_total - cubierto_valor
                if venta.tipo == TIPO_BORONA:
                    reparto.borona_sin_lote += faltante
                else:
                    reparto.kilos_sin_lote += faltante

            for (trozo, kilos), (_, ingreso), (_, gasto) in zip(asignados, ingresos, gastos):
                costo = _q(kilos * trozo.costo_kilo)
                trozo.lote.gastos += gasto
                if venta.tipo == TIPO_BORONA:
                    trozo.lote.borona_vendida += kilos
                    trozo.lote.ingreso_borona += ingreso
                    trozo.lote.costo_borona_vendida += costo
                else:
                    trozo.lote.kilos_vendidos += kilos
                    trozo.lote.ingreso_queso += ingreso
                    trozo.lote.costo_vendido += costo

    # Lo que quedó en las colas es inventario sin vender, con su costo
    for trozo in cola_queso:
        if trozo.kilos > CERO:
            trozo.lote.kilos_sin_vender += trozo.kilos
            trozo.lote.costo_sin_vender += _q(trozo.kilos * trozo.costo_kilo)
    for trozo in cola_borona:
        if trozo.kilos > CERO:
            trozo.lote.borona_sin_vender += trozo.kilos
            trozo.lote.costo_sin_vender += _q(trozo.kilos * trozo.costo_kilo)

    # Cuadre peso a peso: los cuatro destinos del costo tienen que sumar
    # exactamente lo pagado por el lote. Los redondeos de arriba pueden dejar
    # centavos de diferencia; se le cargan al costo de lo que sigue en inventario,
    # que es el único que no está pegado a un documento concreto (una venta o un
    # ajuste ya impreso). Si el lote está vendido completo, al costo de lo vendido.
    for lote in reparto.lotes:
        lote.kilos_comprados = _q(lote.kilos_comprados, KILOS)
        repartido = (
            lote.costo_vendido
            + lote.costo_borona_vendida
            + lote.costo_merma
            + lote.costo_sin_vender
        )
        diferencia = _q(lote.costo_total) - repartido
        if diferencia != CERO:
            if lote.costo_sin_vender > CERO or lote.kilos_sin_vender > CERO:
                lote.costo_sin_vender += diferencia
            else:
                lote.costo_vendido += diferencia

    # De la más reciente a la más vieja: lo que interesa primero es el último lote
    reparto.lotes.sort(key=lambda l: l.fecha, reverse=True)
    return reparto
