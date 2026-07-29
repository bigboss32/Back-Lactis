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
El reparto se lleva al nivel de CADA COMPRA, no del lote: cada compra tiene su
propio costo por kilo, y cuando una venta se lleva K kilos de una compra, a esa
compra se le carga K × su costo por kilo. Las cifras del lote son la SUMA de las
de sus compras (ver `LoteCalculado`), no un acumulado aparte, y de ahí salen dos
cosas al mismo tiempo:

- El cuadre del lote queda garantizado por construcción, no por un ajuste al
  final: si cada compra cuadra, el lote cuadra.
- Sale gratis el detalle por productor DENTRO del lote, y es exacto: no es la
  ganancia del lote repartida a prorrata de los kilos, es la ganancia de los
  kilos de ese productor con el precio que se le pagó a él.

La borona tiene DOS orígenes y se costea distinto según cuál:
- La que llega con la compra y no se paga: costo CERO. Lo que se venda de ella es
  ganancia pura de esa compra.
- La que sale de pasar queso a borona: se lleva el costo del queso del que salió,
  y se le carga a la MISMA compra de la que salió ese queso. Si no, pasar queso a
  borona haría desaparecer plata.

LA CUENTA QUE TIENE QUE CUADRAR
-------------------------------
Cada peso pagado por una compra termina en exactamente uno de cuatro sitios:
costo de lo vendido, costo de la borona vendida, costo de lo que se perdió como
merma, o costo de lo que sigue en inventario. `repartir_lotes` garantiza esa
igualdad peso a peso: el último trozo de cada reparto absorbe el residuo del
redondeo, porque el usuario cuadra estas cifras a mano con calculadora.
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
    precio_kilo: Decimal
    valor_total: Decimal
    saldo: Decimal  # lo que falta pagarle por esta compra


@dataclass(frozen=True)
class VentaEvento:
    fecha: date
    orden: int
    cliente: str
    tipo: str  # 'queso' | 'borona'
    kilos: Decimal
    precio_kilo: Decimal
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
class CompraDelLote:
    """Una compra dentro del lote, con lo que dejaron SUS kilos.

    Los acumuladores se llenan durante el reparto: cada vez que una venta o un
    ajuste se lleva kilos de esta compra, se anotan aquí. La ganancia que sale es
    exacta —los kilos de este productor al precio que se le pagó a él— y no la del
    lote repartida a prorrata.
    """

    productor: str
    kilos: Decimal
    borona_recibida: Decimal
    precio_kilo: Decimal
    valor_total: Decimal
    saldo: Decimal
    # A dónde fueron SUS kilos (los cuatro suman `kilos`)
    kilos_vendidos: Decimal = CERO
    kilos_a_borona: Decimal = CERO
    kilos_merma: Decimal = CERO
    kilos_sin_vender: Decimal = CERO
    borona_vendida: Decimal = CERO
    borona_sin_vender: Decimal = CERO
    # Plata
    ingreso_queso: Decimal = CERO
    ingreso_borona: Decimal = CERO
    gastos: Decimal = CERO
    costo_vendido: Decimal = CERO
    costo_borona_vendida: Decimal = CERO
    costo_merma: Decimal = CERO
    costo_sin_vender: Decimal = CERO

    @property
    def ingresos(self) -> Decimal:
        return self.ingreso_queso + self.ingreso_borona

    @property
    def costo_realizado(self) -> Decimal:
        return self.costo_vendido + self.costo_borona_vendida + self.costo_merma

    @property
    def ganancia(self) -> Decimal:
        return self.ingresos - self.costo_realizado - self.gastos


@dataclass
class VentaDelLote:
    """Una venta que se llevó kilos de este lote.

    `kilos` son los que salieron de ESTE lote, que pueden ser menos que los de la
    venta (`kilos_venta`): una venta grande se parte entre varios lotes. Se guardan
    los dos para que en pantalla se vea cuándo se partió y no parezca que la venta
    fue más pequeña de lo que fue.
    """

    fecha: date
    cliente: str
    tipo: str
    kilos: Decimal
    kilos_venta: Decimal
    precio_kilo: Decimal
    ingreso: Decimal
    gasto: Decimal
    costo: Decimal

    @property
    def partida(self) -> bool:
        return self.kilos < self.kilos_venta

    @property
    def ganancia(self) -> Decimal:
        return self.ingreso - self.costo - self.gasto


@dataclass
class LoteCalculado:
    """Un lote. Todas sus cifras son la SUMA de las de sus compras.

    Son propiedades calculadas y no campos acumulados a propósito: con una sola
    fuente de verdad no puede pasar que el lote diga una cosa y el detalle por
    productor otra, que es exactamente el error que el usuario detecta cuando suma
    la columna con la calculadora.
    """

    fecha: date
    detalle_compras: list[CompraDelLote] = field(default_factory=list)
    detalle_ventas: list[VentaDelLote] = field(default_factory=list)

    # ---------------------------------------------------------- lo comprado
    @property
    def compras(self) -> int:
        return len(self.detalle_compras)

    @property
    def productores(self) -> list[str]:
        """En el orden en que se registraron, sin repetir: un productor puede
        tener dos compras el mismo día."""
        vistos: list[str] = []
        for compra in self.detalle_compras:
            if compra.productor not in vistos:
                vistos.append(compra.productor)
        return vistos

    def _suma(self, campo: str) -> Decimal:
        return sum((getattr(c, campo) for c in self.detalle_compras), CERO)

    @property
    def kilos_comprados(self) -> Decimal:
        return self._suma("kilos")

    @property
    def costo_total(self) -> Decimal:
        return self._suma("valor_total")

    @property
    def por_pagar(self) -> Decimal:
        return self._suma("saldo")

    @property
    def borona_recibida(self) -> Decimal:
        return self._suma("borona_recibida")

    # ------------------------------------------------- a dónde fue el queso
    @property
    def kilos_vendidos(self) -> Decimal:
        return self._suma("kilos_vendidos")

    @property
    def kilos_a_borona(self) -> Decimal:
        return self._suma("kilos_a_borona")

    @property
    def kilos_merma(self) -> Decimal:
        return self._suma("kilos_merma")

    @property
    def kilos_sin_vender(self) -> Decimal:
        return self._suma("kilos_sin_vender")

    @property
    def borona_vendida(self) -> Decimal:
        return self._suma("borona_vendida")

    @property
    def borona_sin_vender(self) -> Decimal:
        return self._suma("borona_sin_vender")

    # --------------------------------------------------------------- plata
    @property
    def ingreso_queso(self) -> Decimal:
        return self._suma("ingreso_queso")

    @property
    def ingreso_borona(self) -> Decimal:
        return self._suma("ingreso_borona")

    @property
    def gastos(self) -> Decimal:
        return self._suma("gastos")

    @property
    def costo_vendido(self) -> Decimal:
        return self._suma("costo_vendido")

    @property
    def costo_borona_vendida(self) -> Decimal:
        return self._suma("costo_borona_vendida")

    @property
    def costo_merma(self) -> Decimal:
        return self._suma("costo_merma")

    @property
    def costo_sin_vender(self) -> Decimal:
        return self._suma("costo_sin_vender")

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
    """Un pedazo de inventario con su dueño y su costo por kilo.

    Apunta a la COMPRA (donde se anota el reparto, y de cuya suma salen las cifras
    del lote) y también al LOTE, para poder colgar ahí la fila de la venta sin
    tener que buscarlo. Se guarda la referencia en vez de buscarla porque buscar
    una compra dentro de las listas compararía dataclasses campo por campo, y dos
    compras idénticas del mismo día (mismo productor, mismos kilos, mismo precio)
    son iguales entre sí: la búsqueda encontraría la primera y además sería
    cuadrática.
    """

    lote: LoteCalculado
    compra: CompraDelLote
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
) -> list[Decimal]:
    """Reparte `total` entre las asignaciones, en proporción a sus kilos.

    El ÚLTIMO trozo se lleva el residuo, de modo que la suma de los pedazos sea
    exactamente `total`. Sin eso, repartir $17.122.600 entre tres compras puede
    dar un peso de diferencia, y esa diferencia hace que la columna no sume la
    cifra grande —que es justo lo que el usuario verifica a mano—.
    """
    if not asignados or kilos_totales <= CERO:
        return [CERO for _ in asignados]
    partes: list[Decimal] = []
    acumulado = CERO
    for indice, (_, kilos) in enumerate(asignados):
        if indice == len(asignados) - 1:
            parte = total - acumulado
        else:
            parte = _q(total * kilos / kilos_totales)
            acumulado += parte
        partes.append(parte)
    return partes


def repartir_lotes(
    compras: list[CompraEvento],
    ventas: list[VentaEvento],
    ajustes: list[AjusteEvento],
) -> RepartoLotes:
    """Reparte ventas y ajustes entre las compras de cada lote, FIFO.

    Los eventos se ordenan por (fecha, orden). Dentro de un mismo día las compras
    van ANTES de las ventas: lo normal es comprar en la mañana y despachar en la
    tarde, y si la venta se procesara primero, el queso comprado ese mismo día no
    estaría disponible y la venta se iría a "sin lote" sin razón.
    """
    reparto = RepartoLotes()
    por_fecha: dict[date, LoteCalculado] = {}
    cola_queso: list[_Trozo] = []
    cola_borona: list[_Trozo] = []
    # Filas de venta ya abiertas, para no partir una venta en varias filas del
    # mismo lote cuando se lleva kilos de dos compras suyas: el usuario piensa en
    # ventas, no en trozos de inventario.
    filas_venta: dict[tuple[date, int], VentaDelLote] = {}

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
            registro = CompraDelLote(
                productor=compra.productor,
                kilos=compra.kilos,
                borona_recibida=compra.borona_kilos,
                precio_kilo=compra.precio_kilo,
                valor_total=compra.valor_total,
                saldo=compra.saldo,
            )
            lote.detalle_compras.append(registro)
            if compra.kilos > CERO:
                cola_queso.append(
                    _Trozo(
                        lote=lote,
                        compra=registro,
                        kilos=compra.kilos,
                        costo_kilo=compra.valor_total / compra.kilos,
                    )
                )
            if compra.borona_kilos > CERO:
                # Costo cero: la borona llega con el lote y no se paga
                cola_borona.append(
                    _Trozo(
                        lote=lote, compra=registro, kilos=compra.borona_kilos,
                        costo_kilo=CERO,
                    )
                )

        elif clase == "ajuste":
            ajuste: AjusteEvento = evento  # type: ignore[assignment]
            asignados, faltante = _consumir(cola_queso, ajuste.kilos)
            for trozo, kilos in asignados:
                costo = _q(kilos * trozo.costo_kilo)
                if ajuste.destino == DESTINO_MERMA:
                    trozo.compra.kilos_merma += kilos
                    trozo.compra.costo_merma += costo
                else:
                    trozo.compra.kilos_a_borona += kilos
                    # La borona que sale de queso ARRASTRA el costo de ese queso y
                    # se le anota a la MISMA compra: si entrara con costo cero, la
                    # plata de esa compra desaparecería.
                    cola_borona.append(
                        _Trozo(
                            lote=trozo.lote, compra=trozo.compra, kilos=kilos,
                            costo_kilo=trozo.costo_kilo,
                        )
                    )
            if faltante > CERO:
                reparto.kilos_sin_lote += faltante

        else:
            venta: VentaEvento = evento  # type: ignore[assignment]
            cola = cola_borona if venta.tipo == TIPO_BORONA else cola_queso
            asignados, faltante = _consumir(cola, venta.kilos)
            kilos_cubiertos = venta.kilos - faltante
            # Si parte de la venta no tuvo lote, esa parte de la plata tampoco es
            # de ningún lote: se reparte solo lo que corresponde a lo cubierto.
            if faltante > CERO and venta.kilos > CERO:
                proporcion = kilos_cubiertos / venta.kilos
                valor_reparto = _q(venta.valor_total * proporcion)
                gasto_reparto = _q(venta.gasto_monto * proporcion)
                reparto.ingreso_sin_lote += venta.valor_total - valor_reparto
                if venta.tipo == TIPO_BORONA:
                    reparto.borona_sin_lote += faltante
                else:
                    reparto.kilos_sin_lote += faltante
                base_kilos = kilos_cubiertos
            else:
                valor_reparto = venta.valor_total
                gasto_reparto = venta.gasto_monto
                base_kilos = venta.kilos

            ingresos = _repartir_plata(asignados, valor_reparto, base_kilos)
            gastos = _repartir_plata(asignados, gasto_reparto, base_kilos)

            for (trozo, kilos), ingreso, gasto in zip(asignados, ingresos, gastos):
                costo = _q(kilos * trozo.costo_kilo)
                registro = trozo.compra
                registro.gastos += gasto
                if venta.tipo == TIPO_BORONA:
                    registro.borona_vendida += kilos
                    registro.ingreso_borona += ingreso
                    registro.costo_borona_vendida += costo
                else:
                    registro.kilos_vendidos += kilos
                    registro.ingreso_queso += ingreso
                    registro.costo_vendido += costo

                # La fila de la venta, a nivel de LOTE: si la venta se llevó kilos
                # de dos compras del mismo lote, es UNA fila con los kilos sumados.
                lote_de = trozo.lote
                clave = (lote_de.fecha, venta.orden)
                fila = filas_venta.get(clave)
                if fila is None:
                    fila = VentaDelLote(
                        fecha=venta.fecha, cliente=venta.cliente, tipo=venta.tipo,
                        kilos=CERO, kilos_venta=venta.kilos,
                        precio_kilo=venta.precio_kilo,
                        ingreso=CERO, gasto=CERO, costo=CERO,
                    )
                    filas_venta[clave] = fila
                    lote_de.detalle_ventas.append(fila)
                fila.kilos += kilos
                fila.ingreso += ingreso
                fila.gasto += gasto
                fila.costo += costo

    # Lo que quedó en las colas es inventario sin vender, con su costo
    for trozo in cola_queso:
        if trozo.kilos > CERO:
            trozo.compra.kilos_sin_vender += trozo.kilos
            trozo.compra.costo_sin_vender += _q(trozo.kilos * trozo.costo_kilo)
    for trozo in cola_borona:
        if trozo.kilos > CERO:
            trozo.compra.borona_sin_vender += trozo.kilos
            trozo.compra.costo_sin_vender += _q(trozo.kilos * trozo.costo_kilo)

    # Cuadre peso a peso, POR COMPRA: los cuatro destinos del costo tienen que
    # sumar exactamente lo que se pagó por esa compra. Los redondeos de arriba
    # pueden dejar centavos de diferencia; se le cargan al costo de lo que sigue en
    # inventario, que es el único que no está pegado a un documento concreto (una
    # venta o un ajuste ya impreso). Si la compra está vendida completa, al costo
    # de lo vendido.
    #
    # Va por compra y no por lote a propósito: como las cifras del lote son la
    # suma de las de sus compras, cuadrar cada compra hace que el lote cuadre solo,
    # y además el detalle por productor cuadra también.
    for lote in reparto.lotes:
        for compra_reg in lote.detalle_compras:
            repartido = (
                compra_reg.costo_vendido
                + compra_reg.costo_borona_vendida
                + compra_reg.costo_merma
                + compra_reg.costo_sin_vender
            )
            diferencia = _q(compra_reg.valor_total) - repartido
            if diferencia != CERO:
                if compra_reg.kilos_sin_vender > CERO or compra_reg.borona_sin_vender > CERO:
                    compra_reg.costo_sin_vender += diferencia
                else:
                    compra_reg.costo_vendido += diferencia
        # Las ventas del lote, de la más reciente a la más vieja
        lote.detalle_ventas.sort(key=lambda v: v.fecha, reverse=True)

    # De la más reciente a la más vieja: lo que interesa primero es el último lote
    reparto.lotes.sort(key=lambda l: l.fecha, reverse=True)
    return reparto
