"""Utilidad POR LOTE DE PRODUCCIÓN: qué dejó el queso que se hizo cada día.

EL PROBLEMA QUE RESUELVE
-----------------------
El estado de resultados del período resta TODA la leche que entró en el mes
contra TODO el queso que se vendió en el mes. Pero la leche del 1 de julio se
convierte en queso que puede venderse 60 días después, así que las dos cifras no
son del mismo queso: el resultado sale negativo sin que el negocio esté perdiendo,
porque la plata de la leche está ahí, convertida en queso, en la bodega.

Aquí se calcula lo que de verdad dejó cada producción: lo que le costó la leche
que usó, lo que se vendió de ella, y lo que todavía está en bodega.

LAS DOS CADENAS
---------------
Son dos repartos encadenados, y hacen falta los dos:

1. LECHE -> PRODUCCIÓN. Los litros que usó una producción salen de la leche
   recibida, de la más vieja a la más nueva. La leche es lo más perecedero de
   todo, así que "primero la más vieja" no es una convención: es obligatorio en la
   planta. Cada recepción tiene su propio precio por litro (varía por proveedor),
   así que el lote queda con su costo REAL y no con un promedio del mes.

   El transporte de esos litros va con ellos: es parte de lo que costó traer esa
   leche y no un gasto suelto del mes.

2. PRODUCCIÓN -> VENTA. Los kilos vendidos salen del lote de producción más
   viejo, también por lo perecedero. Ojo: la cola es POR TIPO DE QUESO. No se
   puede despachar queso doble crema de un lote de campesino, y una sola cola
   mezclaría los costos de dos productos con rendimientos distintos.

LO QUE CUADRA
-------------
Cada peso de leche termina en exactamente uno de estos sitios: costo del queso
vendido, costo del queso que sigue en bodega, o leche que se usó en una producción
sin registrar (`litros_sin_recepcion`, que se avisa aparte). Y cada kilo producido
está vendido o está en bodega.

De ahí sale la línea que le falta al estado de resultados: el queso que quedó en
bodega NO es pérdida del período, es inventario.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

CERO = Decimal("0")
CENTAVOS = Decimal("0.01")


# --------------------------------------------------------------------- entrada
@dataclass(frozen=True)
class RecepcionEvento:
    """Leche recibida de un proveedor."""

    fecha: date
    orden: int
    proveedor: str
    litros: Decimal
    # Lo que se le paga al proveedor por esos litros (ya con bonificaciones y
    # descuentos) y el flete de traerlos. Los dos son costo de ESA leche.
    valor_leche: Decimal
    valor_transporte: Decimal


@dataclass(frozen=True)
class ProduccionEvento:
    fecha: date
    orden: int
    tipo_queso_id: uuid.UUID
    tipo_queso: str
    litros_usados: Decimal
    kilos: Decimal
    merma: Decimal
    # El id de la producción en la base. Sirve para que la merma de un cierre de
    # ciclo pueda decir A QUÉ TANDA se le carga, en vez de irse a la más vieja de
    # la cola. Es opcional para no romper a quien arme eventos a mano.
    produccion_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ExistenciaEvento:
    """Queso que ya estaba en bodega y se cargó a mano, sin pasar por producción.

    Es el caso normal al empezar a usar el sistema: ya había queso hecho. Entra al
    inventario como una entrada de inventario suelta (no la crea una producción), y
    trae su propio `costo_unitario`, así que se puede costear de verdad.

    Se trata como un lote más, con dos diferencias: no tiene leche detrás (no se
    sabe de qué leche salió, ni hace falta) y su costo es el que se cargó a mano.
    Si se cargó sin costo, el lote queda en cero y eso se avisa aparte: sus kilos
    harían ver la utilidad mejor de lo que es.
    """

    fecha: date
    orden: int
    tipo_queso_id: uuid.UUID
    tipo_queso: str
    kilos: Decimal
    costo_unitario: Decimal
    referencia: str | None


MOTIVO_AJUSTE = "ajuste"
MOTIVO_MERMA_CICLO = "merma_ciclo"


@dataclass(frozen=True)
class BajaEvento:
    """Queso que sale de la bodega sin venderse: un ajuste de inventario hacia
    abajo (se daño, se perdió, se corrigió un sobrante).

    Hay que procesarlo o "en bodega" quedaría inflado: el stock del inventario sí
    baja con el ajuste, y si el reparto no lo baja también, las dos pantallas
    dirían cosas distintas sobre los mismos kilos.

    Se le carga al lote más viejo, como todo lo demás, y su costo se pierde: es
    plata que salió del lote sin ingreso.

    HAY DOS CLASES DE BAJA Y SE REPARTEN DISTINTO:

    - `motivo='ajuste'` (`produccion_id` en None): el ajuste suelto que alguien
      anotó a mano. Nadie sabe de qué tanda era el queso que se dañó, así que va
      FIFO, del lote más viejo al más nuevo, como todo lo demás.
    - `motivo='merma_ciclo'` (`produccion_id` con dueño): la merma de un CIERRE
      DE CICLO, que ya viene repartida entre las tandas del ciclo a prorrata de
      sus kilos. Aquí sí se sabe de quién es cada kilo, y por eso se le carga a
      SU tanda y no a la más vieja. Si fuera FIFO, la merma de todo el ciclo
      caería siempre sobre la última tanda —la única que todavía tiene kilos en
      la cola cuando se cierra—, y esa tanda se vería pésima mientras las demás
      se verían perfectas, siendo que todas se secaron por igual.

    Si la tanda dueña ya no tiene kilos en la cola (se despachó completa), los
    que falten se toman FIFO del resto: no se inventa inventario para que cuadre.
    """

    fecha: date
    orden: int
    tipo_queso_id: uuid.UUID
    kilos: Decimal
    produccion_id: uuid.UUID | None = None
    motivo: str = MOTIVO_AJUSTE


@dataclass(frozen=True)
class VentaEvento:
    """Un renglón de venta de queso terminado."""

    fecha: date
    orden: int
    cliente: str
    tipo_queso_id: uuid.UUID
    producto: str
    kilos: Decimal
    precio_kilo: Decimal
    valor_total: Decimal
    # Lo que costó LLEVAR el despacho. No lo paga el cliente: lo paga la quesera, y
    # se reparte entre los lotes que aportaron kilos a esa venta.
    gasto_monto: Decimal = CERO


# ---------------------------------------------------------------------- salida
@dataclass
class LecheDelLote:
    """De qué proveedor vino la leche que usó este lote, y cuánto costó.

    Es lo que permite decir "el queso del 1 de julio se hizo con leche de estos
    tres proveedores, a estos precios": el costo del lote no es un promedio del
    mes, son las recepciones concretas que se consumieron.
    """

    proveedor: str
    litros: Decimal
    costo_leche: Decimal
    costo_transporte: Decimal
    fecha_recepcion: date

    @property
    def costo(self) -> Decimal:
        return self.costo_leche + self.costo_transporte


@dataclass
class BajaDelLote:
    """Queso de este lote que salió sin venderse, con su fecha.

    Se guarda con fecha y no solo acumulado porque el estado de resultados
    necesita saber cuánto se dañó DENTRO de un mes: sin la fecha habría que
    repartir el total del lote a ojo entre los meses.

    `motivo` distingue el ajuste anotado a mano (queso que se dañó) de la merma
    de un cierre de ciclo (queso que se secó). Las dos bajan la bodega y las dos
    le restan a la utilidad —son plata que salió sin ingreso—, pero una es un
    problema y la otra es lo normal del oficio, y el dueño necesita verlas
    separadas para no salir a buscar un culpable que no existe.
    """

    fecha: date
    kilos: Decimal
    costo: Decimal
    motivo: str = MOTIVO_AJUSTE


@dataclass
class LecheSinUsar:
    """Leche que llegó y todavía no se ha convertido en queso.

    Lleva la fecha de RECEPCIÓN para poder decir, de la leche que se compró en un
    mes, cuánta sigue en el tanque. Es inventario de materia prima, no costo de
    ningún lote.
    """

    fecha_recepcion: date
    proveedor: str
    litros: Decimal
    costo: Decimal


@dataclass
class VentaDelLote:
    """Una venta que se llevó kilos de este lote.

    `kilos` son los que salieron de ESTE lote y `kilos_venta` los del renglón
    completo: una venta grande se reparte entre varios lotes, y mostrar solo los
    primeros haría creer que se despachó menos de lo que se despachó.
    """

    fecha: date
    cliente: str
    producto: str
    kilos: Decimal
    kilos_venta: Decimal
    precio_kilo: Decimal
    ingreso: Decimal
    costo: Decimal
    # La parte del flete de esa venta que le toca a este lote, a prorrata de los
    # kilos que aportó.
    gasto: Decimal = CERO

    @property
    def partida(self) -> bool:
        return self.kilos < self.kilos_venta

    @property
    def utilidad(self) -> Decimal:
        return self.ingreso - self.costo - self.gasto

    @property
    def costo_puesto_kilo(self) -> Decimal:
        """Cuánto costó cada kilo PUESTO en el destino: el queso más el flete.

        Es la cifra que el usuario pidió: "lo que vale el kilo puesto en Bogotá".
        """
        if self.kilos <= CERO:
            return CERO
        return (self.costo + self.gasto) / self.kilos


ORIGEN_PRODUCCION = "produccion"
ORIGEN_EXISTENCIA = "existencia"


@dataclass
class LoteProduccion:
    """Un lote de queso, con lo que costó y lo que dejó.

    `origen` dice de dónde salió:
    - 'produccion': se hizo aquí, y su costo es la leche que usó más el transporte.
    - 'existencia': ya estaba en bodega y se cargó a mano; su costo es el que se
      cargó, y no tiene leche detrás.
    """

    fecha: date
    tipo_queso_id: uuid.UUID
    tipo_queso: str
    litros_usados: Decimal
    kilos_producidos: Decimal
    merma: Decimal
    origen: str = ORIGEN_PRODUCCION
    # Solo en los de origen 'existencia': cómo se identificó esa carga
    referencia: str | None = None
    # El id de la producción en la base, para poder cargarle la merma de su ciclo
    produccion_id: uuid.UUID | None = None
    detalle_leche: list[LecheDelLote] = field(default_factory=list)
    detalle_ventas: list[VentaDelLote] = field(default_factory=list)
    detalle_bajas: list[BajaDelLote] = field(default_factory=list)
    # A dónde fueron los kilos (los tres suman kilos_producidos)
    kilos_vendidos: Decimal = CERO
    kilos_de_baja: Decimal = CERO
    kilos_en_bodega: Decimal = CERO
    # DE LOS `kilos_de_baja`, esta parte es merma de un cierre de ciclo: queso que
    # se secó entre que se pesó al hacerlo y se pesó al venderlo. El resto de la
    # baja son ajustes anotados a mano (se dañó, se perdió). Es un SUBCONJUNTO, no
    # un cuarto destino: no entra en la suma `vendidos + baja + bodega`.
    kilos_merma_ciclo: Decimal = CERO
    # Plata
    costo_leche: Decimal = CERO
    costo_transporte: Decimal = CERO
    # Lo que se cargó a mano (solo en los de origen 'existencia')
    costo_existencia: Decimal = CERO
    ingresos: Decimal = CERO
    # Fletes de los despachos, en la parte que le toca a este lote
    gastos: Decimal = CERO
    costo_vendido: Decimal = CERO  # costo de los kilos que se vendieron
    costo_de_baja: Decimal = CERO  # costo de los kilos que se dieron de baja
    # Subconjunto de `costo_de_baja`: lo que valía el queso que se secó
    costo_merma_ciclo: Decimal = CERO
    costo_en_bodega: Decimal = CERO
    # Litros que no encontraron leche registrada de dónde salir
    litros_sin_recepcion: Decimal = CERO

    @property
    def costo_total(self) -> Decimal:
        """Lo que costó el lote.

        En los de producción es la leche más su transporte; en los de existencia es
        lo que se cargó a mano. Nunca las dos cosas a la vez, pero se suman las tres
        para que el resto del cálculo (costo por kilo, cuadre, utilidad) sea el
        mismo sin importar el origen.
        """
        return self.costo_leche + self.costo_transporte + self.costo_existencia

    @property
    def sin_costo(self) -> bool:
        """Existencia cargada sin costo: sus kilos hacen ver la utilidad mejor de
        lo que es, porque salen como si hubieran costado cero."""
        return (
            self.origen == ORIGEN_EXISTENCIA
            and self.kilos_producidos > CERO
            and self.costo_total <= CERO
        )

    @property
    def costo_kilo(self) -> Decimal:
        if self.kilos_producidos <= CERO:
            return CERO
        return self.costo_total / self.kilos_producidos

    @property
    def rendimiento(self) -> Decimal:
        """Kilos de queso por litro de leche. Es el número que dice si el lote
        salió bueno antes de saber a cómo se vendió."""
        if self.litros_usados <= CERO:
            return CERO
        return self.kilos_producidos / self.litros_usados

    @property
    def utilidad(self) -> Decimal:
        """Utilidad de lo que YA salió del lote.

        NO le resta el costo de lo que sigue en bodega: ese queso no se ha
        vendido, no es una pérdida, y restárselo es justo el error que hace que el
        estado de resultados del mes salga negativo. Lo de bodega va aparte.

        Lo que SÍ le resta es lo que se dio de baja, que es plata que salió del
        lote sin ingreso, y los fletes de los despachos, que son plata que salió
        para poder vender.
        """
        return self.ingresos - self.costo_vendido - self.costo_de_baja - self.gastos

    @property
    def precio_venta_kilo(self) -> Decimal:
        if self.kilos_vendidos <= CERO:
            return CERO
        return self.ingresos / self.kilos_vendidos

    @property
    def costo_puesto_kilo(self) -> Decimal:
        """Cuánto costó cada kilo PUESTO en el destino: el queso más el flete.

        Es la cifra que pidió el usuario: "lo que vale el kilo puesto en Bogotá".
        Se calcula sobre los kilos VENDIDOS y no sobre los producidos, porque el
        flete solo se pagó por los que se despacharon: si se dividiera entre todos,
        el kilo que sigue en bodega cargaría con un flete que nadie pagó.
        """
        if self.kilos_vendidos <= CERO:
            return CERO
        return (self.costo_vendido + self.gastos) / self.kilos_vendidos

    @property
    def vendido_completo(self) -> bool:
        return self.kilos_en_bodega <= CERO


@dataclass
class RepartoProduccion:
    lotes: list[LoteProduccion] = field(default_factory=list)
    # Kilos vendidos que no salieron de ninguna producción registrada: se vendió
    # queso que nunca se cargó como producido. No se esconde.
    kilos_sin_lote: Decimal = CERO
    ingreso_sin_lote: Decimal = CERO
    # Litros usados en producciones sin leche registrada que los respalde
    litros_sin_recepcion: Decimal = CERO
    # Leche recibida que todavía no se ha usado en ninguna producción
    litros_sin_usar: Decimal = CERO
    costo_litros_sin_usar: Decimal = CERO
    # La misma leche sin usar, con la fecha en que llegó: sirve para decir, de lo
    # que se compró en un mes, cuánto sigue en el tanque.
    detalle_leche_sin_usar: list[LecheSinUsar] = field(default_factory=list)
    # Existencia cargada a mano SIN costo: sus kilos entran al inventario como si
    # hubieran costado cero, así que hacen ver la utilidad mejor de lo que es.
    kilos_existencia_sin_costo: Decimal = CERO


# ------------------------------------------------------------ implementación
@dataclass
class _Leche:
    """Litros de una recepción, con su costo por litro."""

    proveedor: str
    fecha: date
    litros: Decimal
    costo_litro: Decimal
    transporte_litro: Decimal


@dataclass
class _Queso:
    """Kilos de un lote de producción, con su costo por kilo."""

    lote: LoteProduccion
    kilos: Decimal
    costo_kilo: Decimal


def _q(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVOS)


# ------------------------------------------------- reparto de la merma del ciclo
def repartir_merma_ciclo(
    kilos_por_lote: list[Decimal], merma: Decimal, paso: Decimal = CENTAVOS
) -> list[Decimal]:
    """Reparte la merma de un ciclo entre sus tandas, a prorrata de sus kilos.

    POR QUÉ A PRORRATA Y NO FIFO. Al cerrar el ciclo, las tandas viejas ya salieron
    completas y la única que todavía tiene kilos en la cola es la última. Un
    reparto FIFO le cargaría TODA la merma del ciclo a esa última tanda: se vería
    pésima y las demás perfectas, cuando en realidad todas se secaron igual. Lo que
    pasó de verdad es que cada tanda rindió menos kilos vendibles de los que pesó,
    y eso es proporcional a lo que cada una produjo.

    EL ÚLTIMO LOTE SE LLEVA EL RESIDUO del redondeo, igual que `_repartir_plata` en
    reventa/lotes.py. Sin eso, repartir 5 kg entre tres tandas de 50, 50 y 30 daría
    1,92 + 1,92 + 1,15 = 4,99 y faltaría un gramo: el dueño suma esta columna a
    mano contra la cifra grande y no puede faltarle nada.

    Los lotes con cero kilos no reciben nada (no produjeron, no se secaron), y si
    NINGUNO tiene kilos no se reparte nada: no se le inventa merma a nadie.

    `paso` es la precisión del reparto. Por defecto centavos de kilo (0.01), que es
    lo que aguantan las columnas de kilos del sistema; se puede pedir más fina para
    comprobar el reparto con kilos de tres decimales.
    """
    total_kilos = sum((k for k in kilos_por_lote if k > CERO), CERO)
    if total_kilos <= CERO or merma <= CERO:
        return [CERO for _ in kilos_por_lote]

    # El residuo va al ÚLTIMO lote que tenga kilos, no al último de la lista: si la
    # lista terminara en un lote de cero kilos, el residuo caería sobre alguien que
    # no produjo nada y ese renglón no se podría explicar.
    ultimo_con_kilos = max(i for i, k in enumerate(kilos_por_lote) if k > CERO)
    partes: list[Decimal] = []
    acumulado = CERO
    for indice, kilos in enumerate(kilos_por_lote):
        if kilos <= CERO:
            partes.append(CERO)
        elif indice == ultimo_con_kilos:
            partes.append(merma - acumulado)
        else:
            parte = (merma * kilos / total_kilos).quantize(paso)
            acumulado += parte
            partes.append(parte)
    return partes


def repartir_produccion(
    recepciones: list[RecepcionEvento],
    producciones: list[ProduccionEvento],
    ventas: list[VentaEvento],
    existencias: list[ExistenciaEvento] | None = None,
    bajas: list[BajaEvento] | None = None,
) -> RepartoProduccion:
    """Encadena los dos repartos y devuelve un lote por producción o existencia.

    Los eventos se procesan en orden cronológico. Dentro del mismo día la leche
    entra ANTES de que se produzca, y se produce ANTES de despachar: es el orden
    real de la planta, y al revés la leche del día no estaría disponible para la
    producción del día y todo se iría a "sin respaldo" sin razón.

    `existencias` es el queso que ya estaba en bodega y se cargó a mano, sin pasar
    por una producción. Entra a la cola de su tipo como cualquier otro lote y por
    fecha, así que si es más viejo se despacha primero, que es lo correcto.
    """
    reparto = RepartoProduccion()
    cola_leche: list[_Leche] = []
    # La cola de queso va POR TIPO: no se puede despachar un tipo de queso desde
    # el lote de otro, y una sola cola mezclaría costos de productos distintos.
    cola_queso: dict[uuid.UUID, list[_Queso]] = {}
    filas_venta: dict[tuple[date, int, int], VentaDelLote] = {}

    eventos: list[tuple[date, int, int, str, object]] = []
    for r in recepciones:
        eventos.append((r.fecha, 0, r.orden, "recepcion", r))
    for p in producciones:
        eventos.append((p.fecha, 1, p.orden, "produccion", p))
    # La existencia va con la misma prioridad que la producción: las dos meten
    # queso a la bodega, y lo que las ordena entre sí es la fecha.
    for e in existencias or []:
        eventos.append((e.fecha, 1, e.orden, "existencia", e))
    # Las bajas van ANTES de las ventas del mismo día, como la merma en reventa: lo
    # que se daña se descubre al despachar, o sea antes de darlo por vendido.
    for b in bajas or []:
        eventos.append((b.fecha, 2, b.orden, "baja", b))
    for v in ventas:
        eventos.append((v.fecha, 3, v.orden, "venta", v))
    eventos.sort(key=lambda e: (e[0], e[1], e[2]))

    for _, _, _, clase, evento in eventos:
        if clase == "recepcion":
            recepcion: RecepcionEvento = evento  # type: ignore[assignment]
            if recepcion.litros <= CERO:
                continue
            cola_leche.append(
                _Leche(
                    proveedor=recepcion.proveedor,
                    fecha=recepcion.fecha,
                    litros=recepcion.litros,
                    costo_litro=recepcion.valor_leche / recepcion.litros,
                    transporte_litro=recepcion.valor_transporte / recepcion.litros,
                )
            )

        elif clase == "produccion":
            produccion: ProduccionEvento = evento  # type: ignore[assignment]
            lote = LoteProduccion(
                fecha=produccion.fecha,
                tipo_queso_id=produccion.tipo_queso_id,
                tipo_queso=produccion.tipo_queso,
                litros_usados=produccion.litros_usados,
                kilos_producidos=produccion.kilos,
                merma=produccion.merma,
                produccion_id=produccion.produccion_id,
            )
            reparto.lotes.append(lote)

            # --- Cadena 1: de qué leche salieron esos litros
            restante = produccion.litros_usados
            for leche in cola_leche:
                if restante <= CERO:
                    break
                if leche.litros <= CERO:
                    continue
                toma = min(leche.litros, restante)
                leche.litros -= toma
                restante -= toma
                costo = _q(toma * leche.costo_litro)
                transporte = _q(toma * leche.transporte_litro)
                lote.costo_leche += costo
                lote.costo_transporte += transporte
                # Se agrupa por proveedor Y fecha de recepción: si se separara por
                # cada recepción, un lote grande dejaría veinte renglones del mismo
                # proveedor del mismo día y no se podría leer.
                fila = next(
                    (
                        f
                        for f in lote.detalle_leche
                        if f.proveedor == leche.proveedor and f.fecha_recepcion == leche.fecha
                    ),
                    None,
                )
                if fila is None:
                    fila = LecheDelLote(
                        proveedor=leche.proveedor, litros=CERO, costo_leche=CERO,
                        costo_transporte=CERO, fecha_recepcion=leche.fecha,
                    )
                    lote.detalle_leche.append(fila)
                fila.litros += toma
                fila.costo_leche += costo
                fila.costo_transporte += transporte
            if restante > CERO:
                # Se produjo con leche que no está registrada: el lote queda con el
                # costo de lo que sí se pudo respaldar, y la diferencia se declara.
                lote.litros_sin_recepcion = restante
                reparto.litros_sin_recepcion += restante

            # --- El queso producido entra a la cola de su tipo
            if produccion.kilos > CERO:
                cola_queso.setdefault(produccion.tipo_queso_id, []).append(
                    _Queso(
                        lote=lote,
                        kilos=produccion.kilos,
                        costo_kilo=lote.costo_total / produccion.kilos,
                    )
                )

        elif clase == "baja":
            baja: BajaEvento = evento  # type: ignore[assignment]
            cola = cola_queso.get(baja.tipo_queso_id, [])
            # Si la baja trae dueño (es merma de un cierre de ciclo), se atiende
            # PRIMERO a su tanda y solo lo que falte se toma FIFO del resto. Un
            # ajuste suelto no trae dueño y va FIFO desde el principio.
            if baja.produccion_id is not None:
                orden_cola = sorted(
                    cola,
                    key=lambda q: 0 if q.lote.produccion_id == baja.produccion_id else 1,
                )
            else:
                orden_cola = cola
            restante = baja.kilos
            for queso in orden_cola:
                if restante <= CERO:
                    break
                if queso.kilos <= CERO:
                    continue
                toma = min(queso.kilos, restante)
                queso.kilos -= toma
                restante -= toma
                costo_baja = _q(toma * queso.costo_kilo)
                queso.lote.kilos_de_baja += toma
                queso.lote.costo_de_baja += costo_baja
                if baja.motivo == MOTIVO_MERMA_CICLO:
                    queso.lote.kilos_merma_ciclo += toma
                    queso.lote.costo_merma_ciclo += costo_baja
                queso.lote.detalle_bajas.append(
                    BajaDelLote(
                        fecha=baja.fecha, kilos=toma, costo=costo_baja,
                        motivo=baja.motivo,
                    )
                )
            if restante > CERO:
                # Se dio de baja queso que no está en ningún lote: mismo aviso que
                # una venta sin lote, porque es el mismo hueco.
                reparto.kilos_sin_lote += restante

        elif clase == "existencia":
            existencia: ExistenciaEvento = evento  # type: ignore[assignment]
            if existencia.kilos <= CERO:
                continue
            lote = LoteProduccion(
                fecha=existencia.fecha,
                tipo_queso_id=existencia.tipo_queso_id,
                tipo_queso=existencia.tipo_queso,
                litros_usados=CERO,
                kilos_producidos=existencia.kilos,
                merma=CERO,
                origen=ORIGEN_EXISTENCIA,
                referencia=existencia.referencia,
                costo_existencia=_q(existencia.kilos * existencia.costo_unitario),
            )
            reparto.lotes.append(lote)
            if lote.sin_costo:
                reparto.kilos_existencia_sin_costo += existencia.kilos
            cola_queso.setdefault(existencia.tipo_queso_id, []).append(
                _Queso(
                    lote=lote,
                    kilos=existencia.kilos,
                    costo_kilo=lote.costo_total / existencia.kilos,
                )
            )

        else:
            venta: VentaEvento = evento  # type: ignore[assignment]
            cola = cola_queso.get(venta.tipo_queso_id, [])
            restante = venta.kilos
            asignados: list[tuple[_Queso, Decimal]] = []
            for queso in cola:
                if restante <= CERO:
                    break
                if queso.kilos <= CERO:
                    continue
                toma = min(queso.kilos, restante)
                queso.kilos -= toma
                restante -= toma
                asignados.append((queso, toma))

            kilos_cubiertos = venta.kilos - restante
            if restante > CERO and venta.kilos > CERO:
                proporcion = kilos_cubiertos / venta.kilos
                valor_reparto = _q(venta.valor_total * proporcion)
                reparto.ingreso_sin_lote += venta.valor_total - valor_reparto
                reparto.kilos_sin_lote += restante
                base = kilos_cubiertos
            else:
                valor_reparto = venta.valor_total
                base = venta.kilos

            # El flete de esa venta se reparte igual que el ingreso: a prorrata de
            # los kilos, y si la venta quedó parcialmente sin lote, solo la parte que
            # sí tuvo lote (el resto de ese flete no es de ningún lote).
            if restante > CERO and venta.kilos > CERO:
                gasto_reparto = _q(venta.gasto_monto * (kilos_cubiertos / venta.kilos))
            else:
                gasto_reparto = venta.gasto_monto

            # El último se lleva el residuo del redondeo, para que la suma de los
            # pedazos sea exactamente el valor repartido.
            acumulado = CERO
            acumulado_gasto = CERO
            for indice, (queso, kilos) in enumerate(asignados):
                if indice == len(asignados) - 1:
                    ingreso = valor_reparto - acumulado
                    gasto = gasto_reparto - acumulado_gasto
                else:
                    ingreso = _q(valor_reparto * kilos / base) if base > CERO else CERO
                    gasto = _q(gasto_reparto * kilos / base) if base > CERO else CERO
                    acumulado += ingreso
                    acumulado_gasto += gasto
                costo = _q(kilos * queso.costo_kilo)
                lote = queso.lote
                lote.kilos_vendidos += kilos
                lote.ingresos += ingreso
                lote.gastos += gasto
                lote.costo_vendido += costo

                clave = (venta.fecha, venta.orden, id(lote))
                fila = filas_venta.get(clave)
                if fila is None:
                    fila = VentaDelLote(
                        fecha=venta.fecha, cliente=venta.cliente, producto=venta.producto,
                        kilos=CERO, kilos_venta=venta.kilos,
                        precio_kilo=venta.precio_kilo, ingreso=CERO, costo=CERO,
                        gasto=CERO,
                    )
                    filas_venta[clave] = fila
                    lote.detalle_ventas.append(fila)
                fila.kilos += kilos
                fila.ingreso += ingreso
                fila.costo += costo
                fila.gasto += gasto

    # Lo que quedó en las colas
    for cola in cola_queso.values():
        for queso in cola:
            if queso.kilos > CERO:
                queso.lote.kilos_en_bodega += queso.kilos
                queso.lote.costo_en_bodega += _q(queso.kilos * queso.costo_kilo)
    for leche in cola_leche:
        if leche.litros > CERO:
            costo_pendiente = _q(
                leche.litros * (leche.costo_litro + leche.transporte_litro)
            )
            reparto.litros_sin_usar += leche.litros
            reparto.costo_litros_sin_usar += costo_pendiente
            reparto.detalle_leche_sin_usar.append(
                LecheSinUsar(
                    fecha_recepcion=leche.fecha, proveedor=leche.proveedor,
                    litros=leche.litros, costo=costo_pendiente,
                )
            )

    # Cuadre peso a peso: lo vendido, lo dado de baja y lo que está en bodega tienen
    # que dar el costo del lote. El residuo del redondeo se le carga a lo que sigue
    # en bodega, que es lo único que no está pegado a un documento ya emitido; si el
    # lote ya salió completo, a lo vendido.
    for lote in reparto.lotes:
        diferencia = _q(lote.costo_total) - (
            lote.costo_vendido + lote.costo_de_baja + lote.costo_en_bodega
        )
        if diferencia != CERO:
            if lote.kilos_en_bodega > CERO:
                lote.costo_en_bodega += diferencia
            else:
                lote.costo_vendido += diferencia
        lote.detalle_ventas.sort(key=lambda v: v.fecha, reverse=True)
        lote.detalle_leche.sort(key=lambda l: l.fecha_recepcion)

    # De la más reciente a la más vieja
    reparto.lotes.sort(key=lambda l: l.fecha, reverse=True)
    return reparto
