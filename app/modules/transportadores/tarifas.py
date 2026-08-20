"""LA ÚNICA cuenta del flete: qué tarifa le aplica a un día, cuánto vale ese día y
cómo se reparte esa plata entre las recepciones que lo componen.

Está en un módulo aparte y no dentro del servicio de transportadores por dos
razones prácticas:

  · lo usan DOS hojas del sistema —recepción (que calcula y guarda el flete de
    cada día) y liquidaciones (que arma el comprobante del transportador)—, y
    hasta ahora la fórmula estaba repetida en cuatro sitios: uno en recepción y
    tres en liquidaciones. Cuatro copias de una cuenta de plata es cuatro
    oportunidades de que se desincronicen, y el día que se desincronizan el
    desglose del comprobante deja de sumar el total;
  · este módulo NO importa nada de recepción ni de liquidaciones, solo el modelo
    de transportadores, así que cualquiera de las dos lo puede importar sin
    armar un ciclo de imports.

No toca la base de datos: recibe el transportador YA CARGADO. Sus rutas vienen
con `lazy="selectin"`, así que están ahí sin una consulta por llamada.

------------------------------------------------------------------------------
LA TARIFA TIENE DOS MODOS, y esto es lo nuevo. Lo pidió el dueño así: "en el
transporte hay un nuevo requerimiento: que sea por litro o que sea por día fijo,
es decir, el transporte de leche a fábrica vale 150k independientemente de los
litros".

  · POR LITRO (`litro`), como siempre: el día vale litros × tarifa.
  · POR DÍA FIJO (`dia_fijo`): el día vale la tarifa, y punto. No se multiplica
    por nada.

EL FIJO ES POR DÍA Y POR RUTA, y es LO MÁS IMPORTANTE DE TODO EL CAMBIO: si ese
día recogió leche de CINCO proveedores en la ruta "a fábrica", el flete del día
son $150.000, NO $150.000 × 5. Por eso la cuenta del fijo no puede vivir en una
función que reciba UNA recepción: recibe EL GRUPO (las recepciones de ese día en
esa ruta) y devuelve UNA cifra. `valor_del_grupo` es esa función, y
`reparto_entre_las_fotos` es la que baja esa cifra a cada recepción de modo que
las partes sumen EXACTO la cifra grande —la regla de la casa—.

Si el mismo día hizo DOS rutas con fijo, son DOS fijos: uno por ruta. Eso sale
solo, porque el grupo es (día, ruta).

------------------------------------------------------------------------------
LA DIRECCIÓN DE LA CUENTA, que es la regla que ordena todo lo de abajo y la que
hay que leer antes de tocar una línea de este archivo:

    EN UN DÍA FIJO, EL RENGLÓN VALE LA TARIFA. Punto. NUNCA la suma de las
    fotos. Las fotos de las recepciones son SOLO el reparto de esa cifra entre
    los proveedores de ese día y esa ruta, y su única obligación es sumar
    exacto el renglón.

Se dice al revés de como estaba antes, y esa inversión es el arreglo entero. Con
la dirección vieja —el renglón se armaba SUMANDO las fotos— cada foto era una
cifra propia, así que cualquier escritura que le tocara la foto a UNA recepción
del día movía el renglón: corregirle los litros a un proveedor que ya estaba en
el comprobante dejaba el día de $150.000 en $261.045,13, y corrigiéndole a los
cinco llegaba a $554.826,77. Eso es correcto por litro (ahí la foto de cada día
SÍ se sostiene sola: litros × tarifa) y es falso para un fijo, donde ninguna
recepción tiene una cifra propia que defender —solo tiene una PARTE—.

Con la dirección invertida ninguno de esos defectos puede existir: corregir los
litros redistribuye pero el día sigue valiendo $150.000; apagar una recepción
redistribuye entre las que quedan; juntar dos días en uno da UN fijo, no dos.

POR LITRO NO CAMBIA NADA: ahí el renglón sigue siendo litros × tarifa y las
fotos se sostienen solas.
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple, Sequence

from app.common.dinero import repartir_al_resto_mayor
from app.modules.transportadores.models import (
    MODO_DIA_FIJO,
    MODO_POR_LITRO,
    MODOS_DE_TRANSPORTE,
    Transportador,
)

CERO = Decimal("0")
CENTAVOS = Decimal("0.01")

__all__ = [
    "MODO_DIA_FIJO",
    "MODO_POR_LITRO",
    "MODOS_DE_TRANSPORTE",
    "Tarifa",
    "al_centavo",
    "reparto_entre_las_fotos",
    "tarifa_de_transporte",
    "tarifa_por_litro",
    "valor_del_grupo",
]


def al_centavo(valor: Decimal) -> Decimal:
    """Redondea a centavos con el medio centavo PARA ARRIBA, como lo hace una persona.

    Es LA MISMA regla que ya usaban `recepcion/service._centavos` y
    `liquidaciones/service._centavos` (ahí están escritas las cifras del caso que la
    obligó: 227,55 L × $242,76 da tres decimales que la columna Numeric(14,2) no
    guarda). Vive también acá porque este módulo ya calcula plata y no puede
    importar ninguno de los dos servicios sin armar un ciclo; el modo es
    ROUND_HALF_UP en las tres, que es como redondea Postgres al meter el valor en la
    columna, así que lo que se devuelve y lo que queda guardado son el mismo número.
    """
    return Decimal(valor).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


class Tarifa(NamedTuple):
    """CÓMO cobra el flete y CUÁNTO. Las dos cosas juntas, nunca una sola.

    El `valor` NO se puede leer sin el `modo`: $150.000 en modo `litro` es una
    tarifa disparatada por litro, y $242,76 en modo `dia_fijo` es un día de trabajo
    por menos de trescientos pesos. Por eso esto es una tupla y no un número: quien
    reciba una `Tarifa` está obligado a mirar el modo antes de multiplicar.
    """

    modo: str
    valor: Decimal

    @property
    def es_dia_fijo(self) -> bool:
        return self.modo == MODO_DIA_FIJO


def _modo_de(fila_o_transportador: Any) -> str:
    """El modo guardado, cayendo en POR LITRO si la columna trae algo que no es un modo.

    Caer en `litro` y no reventar es deliberado: es el valor por omisión de la
    columna y el significado de TODO lo que existía antes de este cambio, así que un
    dato raro (una fila tocada a mano en la base, un enum que mañana se renombre) se
    lee como se leía siempre en vez de tumbar la liquidación de la quincena.
    """
    modo = getattr(fila_o_transportador, "modo_transporte", None)
    return modo if modo in MODOS_DE_TRANSPORTE else MODO_POR_LITRO


def tarifa_de_transporte(
    transportador: Transportador | None, ruta_id: uuid.UUID | None
) -> Tarifa:
    """Cómo y cuánto cobra `transportador` recogiendo leche en `ruta_id`.

    La regla, en el orden en que se mira, y es LA MISMA de siempre (lo único nuevo
    es que además de la cifra se trae el modo, porque los dos viven en la misma
    fila y separarlos sería poder cobrar el fijo de una ruta a la tarifa por litro
    de otra):

      1. si esa ruta le tiene tarifa propia, MANDA esa (es lo que pidió el dueño:
         Alex hace Nápoles a $242,76 el litro y "a fábrica" a $150.000 el día, y el
         mismo día puede hacer las dos);
      2. si no —porque la recepción quedó sin ruta, o porque a esa ruta no se le
         puso tarifa— la TARIFA GENERAL del transportador, con SU modo;
      3. sin transportador no hay flete que cobrar: cero por litro.

    Devuelve siempre Decimal en el valor, nunca float: esto se multiplica por los
    litros (o se reparte tal cual) y el resultado se guarda como plata.
    """
    if transportador is None:
        return Tarifa(MODO_POR_LITRO, CERO)
    general = Tarifa(_modo_de(transportador), Decimal(transportador.valor_transporte or 0))
    if ruta_id is None:
        return general
    for fila in transportador.rutas:
        # Una fila cuya ruta es de OTRA empresa no puede fijar plata en esta, ni
        # aunque el id coincidiera. Hoy no puede coincidir —la ruta de la recepción
        # siempre es de la empresa—, así que esto es un cinturón de seguridad y no
        # una corrección; pero es el mismo cinturón que la lectura, y el día que
        # alguien plante una fila cruzada la cuenta cae en la tarifa general en vez
        # de en una tarifa ajena. No cuesta consulta: `fila.ruta` viene con la
        # colección (lazy="selectin").
        if not fila.es_de_la_empresa(transportador.empresa_id):
            continue
        if fila.ruta_id == ruta_id:
            # Se lee tal cual está guardada. Un cero acá es un cero puesto a mano
            # (alguien decidió que en esa ruta no se cobra flete) y se respeta;
            # caer a la general "porque parece vacío" sería adivinar.
            return Tarifa(_modo_de(fila), Decimal(fila.valor_transporte or 0))
    return general


def tarifa_por_litro(
    transportador: Transportador | None, ruta_id: uuid.UUID | None
) -> Decimal:
    """La tarifa POR LITRO, y CERO cuando ese día no se cobra por litro.

    Sirve para lo único que es: leer la cifra por litro (imprimirla en el renglón
    del comprobante, compararla contra la que explica una foto vieja). NO sirve para
    calcular la plata de un día: en modo fijo devuelve cero, porque no existe
    ninguna tarifa por litro que reproduzca $150.000 el día —depende de los litros,
    que es justo lo que el fijo ignora—. Quien calcula plata usa `valor_del_grupo`.
    """
    tarifa = tarifa_de_transporte(transportador, ruta_id)
    return CERO if tarifa.es_dia_fijo else tarifa.valor


def valor_del_grupo(
    tarifa: Tarifa,
    litros_totales: Decimal,
    *,
    ya_cobrado: bool = False,
    ya_pactado: Decimal | None = None,
) -> Decimal:
    """LA ÚNICA CUENTA que dice CUÁNTO VALE un (transportador, día, ruta) completo.

    Es la cifra del renglón del comprobante, y de ella salen las fotos (nunca al
    revés). No hay ninguna otra cuenta en el sistema que decida esto: si mañana
    aparece una segunda, vuelve el defecto que esta función vino a cerrar —dos
    verdades sobre la misma plata—.

    LA CUENTA, que es la que el dueño hace a mano, y son dos según el modo:

      · POR LITRO: los litros del día en esa ruta, sumados, por la tarifa,
        redondeado UNA sola vez. Redondear una vez y no una por recepción es lo que
        hace que el renglón se reproduzca con calculadora (44,23 + 82,48 = 126,71 L
        × $242,76 = $30.760,12, y no los $30.760,11 de sumar dos redondeos).
      · POR DÍA FIJO: la tarifa, tal cual. No se multiplica por los litros NI se
        multiplica por cuántas recepciones tuvo el día: "el transporte de leche a
        fábrica vale 150k independientemente de los litros". Cinco proveedores ese
        día en esa ruta son $150.000, no $750.000. Y —esto es lo que hay que tener
        presente— TAMPOCO SE MIRA LO QUE LAS FOTOS SUMAN: las fotos son el reparto
        de esta cifra, no su origen. Ver el bloque "LA DIRECCIÓN DE LA CUENTA" del
        encabezado.

    LOS DOS DATOS DE ARRIBA solo mandan sobre el fijo, y cada uno tapa un agujero
    distinto:

      · `ya_cobrado` — ese (día, ruta) ya se cobró COMPLETO en OTRO comprobante, así
        que acá vale $0,00. El día costó $150.000 una vez; la leche que se anote
        después no lo vuelve a cobrar, porque recoger un proveedor más ese mismo día
        no cuesta más —que es literalmente lo que significa una tarifa fija—. Quién
        lo decide: `LiquidacionRepository.viajes_ya_cobrados`.
      · `ya_pactado` — a este (día, ruta) el comprobante YA le puso precio, y esa es
        la cifra que manda. Lo manda ÚNICAMENTE el RECUADRE (la cascada que se
        dispara al corregir una recepción o un anticipo), que no re-precifica: si
        alguien le subió el fijo a la ruta de $150.000 a $180.000, el comprobante
        aprobado tiene que seguir diciendo $150.000. Re-precificar es lo que hace el
        botón Recalcular, que el dueño oprime a propósito, y ese entra sin este dato.
        OJO: es EL VALOR DEL RENGLÓN de antes, no la suma de las fotos de antes. Son
        la misma cifra mientras todo esté cuadrado, y cuando NO lo están —que es
        justo cuando esto importa, porque alguien acaba de tocar una recepción— el
        renglón es el que dice la verdad y las fotos son las que hay que rehacer.

    Los dos se ignoran en modo POR LITRO, y no por descuido: ahí el renglón es
    litros × tarifa y siempre se puede volver a derivar de los litros que quedaron,
    así que no hay nada que "conservar" ni ningún día que se cobre una sola vez. Ese
    camino no cambió ni un centavo.

    CON LOS LITROS EN CERO EL FIJO SIGUE VALIENDO $150.000, y es a propósito: el
    camión hizo el viaje. El fijo se cobra por haber ido, no por lo que trajo; si se
    devolviera cero se estaría cobrando por litro con otro nombre. (Un día sin
    NINGUNA recepción viva no llega acá: no hay grupo, no hay renglón y no hay
    flete. Que el renglón DESAPAREZCA —y no que quede en $0,00— es lo correcto:
    $0,00 diría que ese viaje no se paga, y lo que pasó es que ese viaje ya no
    existe.)
    """
    if not tarifa.es_dia_fijo:
        return al_centavo(Decimal(litros_totales) * tarifa.valor)
    if ya_cobrado:
        return CERO
    if ya_pactado is not None:
        return al_centavo(Decimal(ya_pactado))
    return al_centavo(tarifa.valor)


def reparto_entre_las_fotos(
    tarifa: Tarifa,
    partes: Sequence[tuple[Any, Decimal]],
    total: Decimal,
) -> dict[Any, Decimal]:
    """Baja `total` a cada recepción del grupo, de modo que las partes SUMEN `total`.

    `partes` son pares (clave, litros de esa recepción); `total` es lo que vale el
    (día, ruta) completo (lo que devolvió `valor_del_grupo`, o lo que las fotos ya
    congeladas suman). Devuelve clave → plata, y la suma de las platas es EXACTO
    `total`. Esa igualdad es la regla de la casa: la foto de cada recepción
    (`recepciones_leche.valor_transporte`) la leen la contabilidad, la grilla de la
    quincena y el costeo, así que tienen que sumar el renglón al centavo.

    LO ÚNICO QUE CAMBIA ENTRE LOS DOS MODOS ES EL VALOR "EXACTO" DE CADA PARTE, o
    sea de dónde salen los centavos antes de repartirlos:

      · POR LITRO cada parte vale sus propios litros × la tarifa. Es lo que se hacía
        siempre y no cambia ni un centavo: el reparto por resto mayor solo acomoda la
        diferencia entre sumar redondeos y redondear la suma.
      · POR DÍA FIJO cada parte vale la porción del fijo QUE LE CORRESPONDE POR SUS
        LITROS: $150.000 × litros de esa recepción ÷ litros del día. Se escogió
        proporcional a los litros y no partes iguales porque es lo que la foto
        significa en el resto del sistema —cuánto costó recoger la leche de ESE
        productor ese día, que es lo que el costeo de la producción lee—: con partes
        iguales, un proveedor de 5 L cargaría el mismo flete que uno de 300 L y el
        costo del queso saldría torcido. Y cuando todos entregan lo mismo, las dos
        formas dan lo mismo.
      · SI EL DÍA NO TIENE LITROS (todas las recepciones en cero), no hay proporción
        posible y el fijo se parte en PARTES IGUALES. Es el único reparto que no
        inventa nada: sin litros, ninguna recepción puede reclamar más que otra.

    Los centavos los cierra `repartir_al_resto_mayor`, que es el mismo reparto que
    usa la reventa: cada parte queda a lo sumo un centavo de su propia cuenta y la
    suma da exacto. Ver su docstring en app/common/dinero.py.
    """
    if not partes:
        return {}
    total = Decimal(total)
    if tarifa.es_dia_fijo:
        litros_del_dia = sum((Decimal(litros) for _, litros in partes), CERO)
        if litros_del_dia > CERO:
            exactos = [
                (clave, total * Decimal(litros) / litros_del_dia) for clave, litros in partes
            ]
        else:
            cuantas = Decimal(len(partes))
            exactos = [(clave, total / cuantas) for clave, _ in partes]
    else:
        exactos = [(clave, Decimal(litros) * tarifa.valor) for clave, litros in partes]
    return repartir_al_resto_mayor(exactos, total)
