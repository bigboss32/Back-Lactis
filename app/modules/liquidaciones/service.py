"""Liquidaciones por quincena: agrupa las recepciones no liquidadas del período,
calcula totales, descuenta anticipos y genera el comprobante (PDF/Excel),
replicando el proceso que la quesera llevaba en Excel.
"""
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, NotFoundError
from app.core.pagination import PageParams
from app.modules.empresas.repository import EmpresaRepository
from app.modules.liquidaciones.models import (
    ESTADO_ANULADA,
    ESTADO_APROBADA,
    ESTADO_BORRADOR,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    TIPO_PROVEEDOR,
    TIPO_TRANSPORTADOR,
    Anticipo,
    Liquidacion,
    LiquidacionDetalle,
    PagoLiquidacion,
)
from app.modules.liquidaciones.repository import AnticipoRepository, LiquidacionRepository
from app.modules.liquidaciones.schemas import (
    PagoLiquidacionCreate,
    PreLiquidacionAnticipo,
    PreLiquidacionDetalle,
    PreLiquidacionRead,
)
from app.modules.proveedores.repository import ProveedorRepository
from app.modules.recepcion.models import RecepcionLeche
from app.modules.recepcion.repository import RecepcionRepository
from app.modules.transportadores.repository import TransportadorRepository
from app.modules.transportadores.tarifas import tarifa_por_litro
from app.utils.export import build_liquidacion_pdf, litros, pesos

CERO = Decimal("0")
CENTAVOS = Decimal("0.01")


def _centavos(valor: Decimal) -> Decimal:
    """Redondea a centavos como lo haría una persona: el medio centavo sube.

    Se usa al recalcular un día corregido a mano. El resto de las cifras de la
    liquidación NO se re-redondean: se suman tal como están guardadas, para que
    el total sea exactamente la suma de los renglones que el dueño ve.
    """
    return Decimal(valor).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# LOS RENGLONES DEL COMPROBANTE DEL TRANSPORTADOR: uno por DÍA Y RUTA
# ---------------------------------------------------------------------------
# Hasta ahora el renglón era el día y la tarifa se leía del transportador
# (`del_dia[0].transportador.valor_transporte`). Con lo que pidió el dueño —"este
# tuvo que hacer las dos [rutas]... pero cada ruta puede tener un valor diferente
# de litro por leche"— eso deja de cuadrar: el día en que Alex hizo Nápoles a
# $242,76 y Mira Valle a $317,50 salía UN renglón con UNA tarifa, y litros ×
# precio no daba el valor. El conductor suma la columna a mano y esa resta que no
# le da es lo que le hace perder la confianza en el papel.
#
# LA INVARIANTE que estas funciones hacen cumplir, y que no se negocia:
#   1. en cada renglón, litros × precio_litro == valor, EXACTO al centavo;
#   2. la suma de los `valor` == liquidacion.valor_transporte;
#   3. y ese total == la suma de los `recepciones_leche.valor_transporte` que
#      entraron, que son FOTOS del momento en que se recibió la leche.
#
# EL COMPROBANTE MANDA, y esa es la decisión del dueño que ordena todo lo de
# abajo. Así cuadra él, a mano y con calculadora: junta los litros del día en esa
# ruta, los multiplica por la tarifa, y eso TIENE que ser lo que dice el renglón.
# O sea que el renglón de un (día, ruta) es UNO —litros sumados, la tarifa, y
# valor = redondear(litros × tarifa) UNA SOLA VEZ—, y la plata de ese renglón SE
# REPARTE entre las fotos de ese día y esa ruta para que las tres cosas se cumplan
# a la vez.
#
# Antes se hacía al revés: el renglón sumaba las fotos y se PARTÍA en una línea
# por recepción cuando la suma de las fotos redondeadas no daba lo mismo que
# redondear el total (44,23 L + 82,48 L en Nápoles a $242,76 dejaba el comprobante
# en $30.760,11 y el dueño, sumando 126,71 L × $242,76, obtenía $30.760,12). Un
# centavo, y encima dos líneas idénticas en fecha, ruta y tarifa que nadie sabía
# explicar. En una quincena normal le pasaba a 7 de 45 días.
#
# PARTIR el renglón queda SOLO para el caso en que las fotos de un mismo (día, ruta)
# están a tarifas distintas Y NO SE PUEDEN TOCAR, o sea cuando ese flete YA SE PAGÓ.
# Mientras no se haya pagado no puede pasar: antes de armar los renglones se vuelve a
# derivar la cifra de cada día con la tarifa VIVA (ver `_rederivar_el_flete`), así que
# el (día, ruta) siempre queda con una sola tarifa —la que el dueño ve en pantalla— y
# el día es UN renglón. Cuando sí toca partir, son varias líneas, cada una con SU
# tarifa y cada una cuadrando. Nunca se mete un promedio en `precio_litro`: un promedio
# hace que la columna deje de reproducirse a mano, que es justo lo que el dueño verifica.


def _nombre_de_ruta(recepcion: RecepcionLeche) -> str | None:
    """El nombre de la ruta de una recepción, sin ir a la base.

    `RecepcionLeche.ruta` es lazy="joined", así que viene cargada con la fila.
    """
    return recepcion.ruta.nombre if recepcion.ruta is not None else None


def _litros_de(recepcion: RecepcionLeche) -> Decimal:
    return Decimal(recepcion.cantidad_litros or 0)


def _foto_del_flete(recepcion: RecepcionLeche) -> Decimal:
    """El flete que quedó GUARDADO en la recepción el día que se recibió la leche."""
    return Decimal(recepcion.valor_transporte or 0)


def _tarifa_de_hoy(recepcion: RecepcionLeche) -> Decimal:
    """La tarifa que le aplica HOY: la de su ruta, o la general del transportador."""
    return tarifa_por_litro(recepcion.transportador, recepcion.ruta_id)


def _flete_de_hoy(recepcion: RecepcionLeche) -> Decimal:
    """Lo que ese día vale HOY: litros × tarifa de hoy, redondeado una sola vez.

    Es la MISMA cuenta que hace la recepción al guardar el día
    (`recepcion/service.py::_completar_y_calcular`). Vive acá arriba porque la usan
    los tres caminos del flete —generar, recalcular y el avance— y cuatro copias de
    una cuenta de plata son cuatro oportunidades de que se desincronicen.
    """
    return _centavos(_litros_de(recepcion) * _tarifa_de_hoy(recepcion))


def _tarifa_de_la_foto(recepcion: RecepcionLeche, tolerancia: Decimal) -> Decimal | None:
    """La tarifa por litro que explica el flete guardado de esa recepción.

    Se busca en este orden:

      1. LA TARIFA DE HOY (la de la ruta, o la general si esa ruta no tiene
         propia). Es el caso normal y la que hay que imprimir cuando cuadra:
         es la que el conductor reconoce y la que está en la pantalla.
      2. Si no cuadra, es porque la foto se tomó con OTRA tarifa y está CONGELADA
         —le corrigieron la tarifa de la ruta después de haberle pagado ese flete—.
         Entonces se saca de la foto misma: valor ÷ litros. Se prueban también los dos
         vecinos de un centavo porque la foto se guardó redondeada y la división puede
         caer justo al lado. (Sin pagar no se llega acá: la cifra se re-deriva de la
         tarifa viva antes de armar los renglones.)
      3. Si ninguna cuadra, `None`: esa foto no la explica ninguna tarifa de dos
         decimales (una columna corregida a mano en la base). Ver
         `_renglones_de_ultimo_recurso`.

    LA TOLERANCIA, que es la parte delicada. Con cero, una tarifa solo "explica" la
    foto si la reproduce EXACTA al centavo. Se llama con un centavo de tolerancia
    únicamente cuando la recepción comparte (día, ruta) con otras, porque en ese
    caso el reparto de `_un_renglon_y_su_reparto` pudo haberle movido un centavo
    —nunca más de uno— y sin la tolerancia esa misma foto ya no la explicaría
    ninguna tarifa: al volver a generar el comprobante el grupo se partiría solo y
    el papel cambiaría sin que nadie hubiera tocado nada.

    Una recepción SOLA en su (día, ruta) nunca la toca el reparto (su renglón es
    ella misma), así que ahí la tolerancia es cero y una foto rara sigue cayendo en
    `_renglones_de_ultimo_recurso` en vez de que le movamos un centavo en silencio.
    """
    litros_ = _litros_de(recepcion)
    foto = _foto_del_flete(recepcion)

    def explica(tarifa: Decimal) -> bool:
        return tarifa >= CERO and abs(_centavos(litros_ * tarifa) - foto) <= tolerancia

    de_hoy = _tarifa_de_hoy(recepcion)
    if explica(de_hoy):
        return de_hoy
    if litros_ <= CERO:
        # Sin litros no hay división posible. Con litros en cero la foto tenía que
        # ser cero (así se calculó), así que llegar acá es una fila corrupta.
        return None
    derivada = (foto / litros_).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
    for candidata in (derivada, derivada - CENTAVOS, derivada + CENTAVOS):
        if explica(candidata):
            return candidata
    return None


def _renglon_de_transporte(
    fecha: date,
    ruta_id: uuid.UUID | None,
    ruta_nombre: str | None,
    litros_: Decimal,
    precio_litro: Decimal,
    valor: Decimal,
) -> dict[str, Any]:
    """Un renglón ya cuadrado, como diccionario.

    Se devuelven diccionarios y no `LiquidacionDetalle` porque los mismos renglones
    los usan tres caminos: generar la liquidación, recalcularla y la
    PRE-liquidación, que no persiste nada. Una sola cuenta para los tres.
    """
    return {
        "fecha": fecha,
        "ruta_id": ruta_id,
        "ruta_nombre": ruta_nombre,
        "litros": litros_,
        "precio_litro": precio_litro,
        "valor": valor,
    }


def _renglones_de_ultimo_recurso(
    fecha: date,
    ruta_id: uuid.UUID | None,
    ruta_nombre: str | None,
    litros_: Decimal,
    valor: Decimal,
) -> list[dict[str, Any]]:
    """Una foto de flete que NINGUNA tarifa por litro de dos decimales explica.

    No debería existir: las fotos se calculan como `_centavos(litros × tarifa)` con
    una tarifa de dos decimales, y de ahí la tarifa siempre se puede volver a
    sacar. Queda por si alguien corrigió la columna a mano en la base, o por si un
    día se cambia la forma de calcular el flete.

    Antes que imprimir un renglón que no cuadra, se parte en dos: el grueso de los
    litros a la tarifa más cercana y una fracción de litro con el resto, al precio
    que lo cierra exacto. Se ve raro en el papel —y ese es el punto: se ve raro
    porque el dato de origen está raro—, pero la columna sigue sumando el total y
    el total sigue siendo la suma de las fotos.

    El truncado (ROUND_DOWN) no es un detalle: hace que el renglón grande nunca se
    pase del valor guardado, así que lo que queda para el de cierre no sale
    negativo.
    """
    resto = CENTAVOS  # 0,01 L: la fracción más chica que cabe en la columna
    if litros_ <= resto:
        raise BusinessError(
            f"El flete guardado del {fecha.strftime('%d/%m/%Y')} "
            f"({pesos(valor)} por {litros_} L) no corresponde a ninguna tarifa por "
            "litro: corrija la recepción de ese día antes de liquidar el flete"
        )
    principal = litros_ - resto
    tarifa = (valor / litros_).quantize(CENTAVOS, rounding=ROUND_DOWN)
    valor_principal = _centavos(principal * tarifa)
    sobra = valor - valor_principal
    return [
        _renglon_de_transporte(fecha, ruta_id, ruta_nombre, principal, tarifa, valor_principal),
        # 0,01 L × (sobra × 100) == sobra, exacto y sin redondeo de por medio.
        _renglon_de_transporte(fecha, ruta_id, ruta_nombre, resto, sobra * 100, sobra),
    ]


def _repartir_al_resto_mayor(
    exactos: list[tuple[Any, Decimal]], total: Decimal
) -> dict[Any, Decimal]:
    """Reparte `total` en centavos entre unas partes cuyo valor EXACTO ya se conoce.

    Es el reparto por RESTO MAYOR: a cada parte se le da su valor truncado al
    centavo y los centavos que faltan para llegar a `total` se entregan de a uno,
    empezando por la parte cuya fracción de centavo quedó más grande. Así ninguna
    parte se desvía más de un centavo de lo que le corresponde y la suma da EXACTO
    la cifra grande, que es la regla de la casa.

    Se prefirió al reparto de `reventa/lotes.py::_repartir_plata` —que le carga todo
    el residuo al último— porque acá las partes son PLATA YA CALCULADA de cada día:
    cargarle dos o tres centavos al último proveedor de la lista dejaría su foto sin
    poderse reproducir con su propia multiplicación, y el dueño también revisa día
    por día. Con el resto mayor cada foto queda a lo sumo un centavo de su cuenta.

    El desempate (fracción igual) va por el valor más grande y después por la clave,
    para que el mismo comprobante generado dos veces reparta los centavos igual: si
    dependiera del orden en que la base devolvió las filas, el papel podría salir
    distinto en cada impresión.
    """
    if not exactos:
        return {}
    pisos = {clave: exacto.quantize(CENTAVOS, rounding=ROUND_DOWN) for clave, exacto in exactos}
    faltan = int((total - sum(pisos.values())) / CENTAVOS)
    orden = sorted(
        exactos,
        key=lambda par: (-(par[1] - pisos[par[0]]), -par[1], str(par[0])),
    )
    asignado = dict(pisos)
    for clave, _ in orden[: max(faltan, 0)]:
        asignado[clave] += CENTAVOS
    # Cinturón: si por lo que sea la cuenta de centavos no cerró (una parte con
    # valor negativo, un total que no viene de estas mismas partes), el residuo se
    # le carga a la parte más grande. La suma tiene que dar `total` SIEMPRE; es la
    # única cosa que no se negocia.
    residuo = total - sum(asignado.values())
    if residuo != CERO:
        asignado[max(exactos, key=lambda par: par[1])[0]] += residuo
    return asignado


def _un_renglon_y_su_reparto(
    fecha: date,
    ruta_id: uuid.UUID | None,
    ruta_nombre: str | None,
    tarifa: Decimal,
    del_grupo: list[RecepcionLeche],
    congeladas: frozenset[uuid.UUID],
) -> tuple[list[dict[str, Any]], dict[uuid.UUID, Decimal]]:
    """UN renglón para todo el (día, ruta, tarifa), y las fotos que lo respaldan.

    El renglón es la cuenta que el dueño hace a mano: los litros sumados, la tarifa,
    y el valor redondeado UNA sola vez. Enseguida esa misma plata se reparte entre
    las recepciones del grupo (resto mayor) para que la suma de las fotos dé exacto
    el valor del renglón.

    LAS FOTOS CONGELADAS son las de un flete que ya se pagó: esa plata salió de la
    caja contra esa cifra y no se toca ni por un centavo. Si el reparto tuviera que
    moverle una, no se reparte nada: el renglón se PARTE en una línea por recepción,
    cada una con la tarifa que explica su propia foto, y así el papel sigue
    cuadrando aunque salga con más líneas.
    """
    litros_ = sum((_litros_de(r) for r in del_grupo), CERO)
    valor = _centavos(litros_ * tarifa)
    # El valor EXACTO de cada día (sin redondear) es la base del reparto: es lo que
    # hace que los centavos caigan donde de verdad se generaron.
    exactos = [(r.id, _litros_de(r) * tarifa) for r in del_grupo]
    asignado = _repartir_al_resto_mayor(exactos, valor)

    fotos: dict[uuid.UUID, Decimal] = {}
    for recepcion in del_grupo:
        nueva = asignado[recepcion.id]
        if nueva == _foto_del_flete(recepcion):
            continue
        if recepcion.id in congeladas:
            return _renglones_partidos_uno_por_recepcion(fecha, ruta_id, ruta_nombre, del_grupo), {}
        fotos[recepcion.id] = nueva
    return [_renglon_de_transporte(fecha, ruta_id, ruta_nombre, litros_, tarifa, valor)], fotos


def _renglones_partidos_uno_por_recepcion(
    fecha: date,
    ruta_id: uuid.UUID | None,
    ruta_nombre: str | None,
    del_grupo: list[RecepcionLeche],
) -> list[dict[str, Any]]:
    """Una línea por recepción, SIN tocar ninguna foto. La salida de emergencia.

    Se usa cuando el reparto tendría que moverle un centavo a una foto congelada
    (un flete ya pagado). Cada línea lleva la tarifa que reproduce su propia foto,
    así que cada una cuadra y la columna sigue sumando el total; lo que se pierde es
    la línea única del día, y eso es preferible a tocar plata ya entregada.
    """
    renglones: list[dict[str, Any]] = []
    for recepcion in del_grupo:
        litros_ = _litros_de(recepcion)
        foto = _foto_del_flete(recepcion)
        propia = _tarifa_de_la_foto(recepcion, CERO)
        if propia is None:
            renglones += _renglones_de_ultimo_recurso(fecha, ruta_id, ruta_nombre, litros_, foto)
        else:
            renglones.append(
                _renglon_de_transporte(fecha, ruta_id, ruta_nombre, litros_, propia, foto)
            )
    return renglones


def _renglones_del_dia_y_ruta(
    fecha: date,
    ruta_id: uuid.UUID | None,
    ruta_nombre: str | None,
    del_grupo: list[RecepcionLeche],
    congeladas: frozenset[uuid.UUID],
) -> tuple[list[dict[str, Any]], dict[uuid.UUID, Decimal]]:
    """Los renglones de un (día, ruta): UNO, salvo que haya dos tarifas mezcladas.

    Primero se pregunta lo único que importa: ¿una sola tarifa —la de hoy— explica
    todo el grupo? La pregunta se le hace AL GRUPO ENTERO y no foto por foto:

        |suma de las fotos − redondear(litros del grupo × tarifa)| ≤ un centavo
        por recepción

    Ese margen es exactamente lo que el doble redondeo (y un reparto anterior)
    pueden haber corrido, porque cada foto se desvía a lo sumo un centavo de su
    cuenta. Y no alcanza para confundir dos tarifas distintas: dos tarifas se
    separan por lo menos un centavo POR LITRO, así que con litros de verdad —decenas
    por proveedor— la diferencia es de pesos, no de centavos.

    Si la tarifa de hoy no explica el grupo, hay tarifas mezcladas y las fotos están
    CONGELADAS (a ese flete ya se le pagó y por eso nadie las volvió a derivar): se
    separan por la tarifa que cada una reproduce y cada montón se vuelve UN renglón con
    SU tarifa. En un flete sin pagar esta rama no se pisa, porque `_rederivar_el_flete`
    dejó todas las fotos a la tarifa viva antes de llegar acá.
    """
    de_hoy = _tarifa_de_hoy(del_grupo[0])
    litros_ = sum((_litros_de(r) for r in del_grupo), CERO)
    suma_de_fotos = sum((_foto_del_flete(r) for r in del_grupo), CERO)
    margen = CENTAVOS * len(del_grupo)
    if de_hoy >= CERO and abs(suma_de_fotos - _centavos(litros_ * de_hoy)) <= margen:
        return _un_renglon_y_su_reparto(
            fecha, ruta_id, ruta_nombre, de_hoy, del_grupo, congeladas
        )

    # Un centavo de tolerancia solo cuando la recepción comparte el día y la ruta
    # con otras, que es cuando un reparto pudo haberle movido la foto. Ver
    # `_tarifa_de_la_foto`.
    tolerancia = CENTAVOS if len(del_grupo) > 1 else CERO
    por_tarifa: dict[Decimal | None, list[RecepcionLeche]] = {}
    for recepcion in del_grupo:
        por_tarifa.setdefault(_tarifa_de_la_foto(recepcion, tolerancia), []).append(recepcion)

    renglones: list[dict[str, Any]] = []
    fotos: dict[uuid.UUID, Decimal] = {}
    for tarifa, del_monton in por_tarifa.items():
        if tarifa is None:
            # Fotos que NINGUNA tarifa de dos decimales explica: cada una en su
            # propia línea partida, sin tocarlas. Ver `_renglones_de_ultimo_recurso`.
            for recepcion in del_monton:
                renglones += _renglones_de_ultimo_recurso(
                    fecha, ruta_id, ruta_nombre,
                    _litros_de(recepcion), _foto_del_flete(recepcion),
                )
            continue
        del_monton_renglones, del_monton_fotos = _un_renglon_y_su_reparto(
            fecha, ruta_id, ruta_nombre, tarifa, del_monton, congeladas
        )
        renglones += del_monton_renglones
        fotos.update(del_monton_fotos)
    return renglones, fotos


def _orden_del_renglon(renglon: dict[str, Any]) -> tuple[Any, ...]:
    """El orden en que se lee el comprobante, y que no depende de la base.

    Fecha, nombre de ruta, y después precio, litros y valor. Los tres últimos son el
    desempate de las líneas partidas —un día y una ruta con dos tarifas, o con una
    foto rara—: sin ellos el orden lo decidía lo que devolviera el SELECT y el mismo
    papel impreso dos veces podía salir con las líneas al revés.
    """
    return (
        renglon["fecha"],
        (renglon["ruta_nombre"] or "").lower(),
        renglon["precio_litro"],
        renglon["litros"],
        renglon["valor"],
    )


class _RepartoDelFlete(NamedTuple):
    """Los renglones del comprobante y las fotos que hay que dejar para que cuadre.

    `fotos` trae SOLO las recepciones cuyo `valor_transporte` hay que corregir (id →
    cifra nueva), y siempre son centavos: es el reparto de la plata del renglón
    entre los días que lo componen. Quien persiste la liquidación las escribe; la
    PRE-liquidación las ignora, porque ese "¿cómo voy?" no guarda nada —pero muestra
    los MISMOS renglones, que es lo que el transportador va a firmar—.
    """

    renglones: list[dict[str, Any]]
    fotos: dict[uuid.UUID, Decimal]


def _reparto_del_flete(
    recepciones: Sequence[RecepcionLeche],
    congeladas: frozenset[uuid.UUID] = frozenset(),
) -> _RepartoDelFlete:
    """LOS RENGLONES del comprobante de un transportador, cuadrados al centavo.

    Agrupa por (fecha, ruta): un renglón por día y ruta, porque el transportador
    puede haber hecho las dos rutas el mismo día y cobrar distinto en cada una. La
    tarifa YA NO entra en la clave del grupo —antes sí— porque era eso lo que
    partía el día en cuatro líneas idénticas: la tarifa se decide adentro, mirando
    el grupo completo (ver `_renglones_del_dia_y_ruta`).

    `congeladas` son los ids de las recepciones cuya foto no se puede tocar porque
    su flete ya se pagó.
    """
    grupos: dict[tuple[Any, Any], list[RecepcionLeche]] = {}
    for recepcion in recepciones:
        grupos.setdefault((recepcion.fecha, recepcion.ruta_id), []).append(recepcion)

    renglones: list[dict[str, Any]] = []
    fotos: dict[uuid.UUID, Decimal] = {}
    for (fecha, ruta_id), del_grupo in grupos.items():
        del_grupo_renglones, del_grupo_fotos = _renglones_del_dia_y_ruta(
            fecha, ruta_id, _nombre_de_ruta(del_grupo[0]), del_grupo, congeladas
        )
        renglones += del_grupo_renglones
        fotos.update(del_grupo_fotos)
    renglones.sort(key=_orden_del_renglon)
    return _RepartoDelFlete(renglones, fotos)


class _DiaConElFleteDeHoy(NamedTuple):
    """Una recepción COPIADA, con su flete ya derivado de la tarifa de hoy.

    Existe para que la PRE-liquidación ("¿cómo voy?") pueda mostrar EXACTAMENTE los
    renglones que va a tener el comprobante sin escribir nada. El comprobante los arma
    después de re-derivar las fotos (ver `_rederivar_el_flete`); si el avance sumara
    las fotos como están hoy, corregir una tarifa y mirar el avance mostraría la
    tarifa vieja y al generar saldría la nueva: la cifra se movería sola.

    No se pueden poner los valores nuevos sobre las recepciones de verdad porque la
    sesión hace commit al final de cada petición: una consulta terminaría escribiendo
    plata. Así que se copian los campos que el reparto lee, y nada más.
    """

    id: uuid.UUID
    fecha: date
    ruta_id: uuid.UUID | None
    ruta: Any
    transportador: Any
    cantidad_litros: Decimal
    valor_transporte: Decimal


def _con_el_flete_de_hoy(recepciones: Sequence[RecepcionLeche]) -> list[_DiaConElFleteDeHoy]:
    """Las mismas recepciones, en copias de solo lectura y con el flete de hoy."""
    return [
        _DiaConElFleteDeHoy(
            id=r.id,
            fecha=r.fecha,
            ruta_id=r.ruta_id,
            ruta=r.ruta,
            transportador=r.transportador,
            cantidad_litros=Decimal(r.cantidad_litros or 0),
            valor_transporte=_flete_de_hoy(r),
        )
        for r in recepciones
    ]


def _renglones_de_transporte(recepciones: Sequence[RecepcionLeche]) -> list[dict[str, Any]]:
    """Solo los renglones, para quien no va a escribir las fotos (la PRE-liquidación).

    Deliberadamente NO devuelve el reparto: quien lo llama por acá es porque no
    persiste nada. El comprobante de verdad va por `_reparto_del_flete`, que sí
    tiene que dejar las fotos sumando el total.

    Va sobre COPIAS con el flete de hoy para que el avance y el papel digan lo mismo.
    """
    return _reparto_del_flete(_con_el_flete_de_hoy(recepciones)).renglones


# Ancho de cada columna del detalle del comprobante DEL TRANSPORTADOR, en
# centímetros. Suman 16,9 cm, que es el ancho que usan las demás tablas del
# documento (carta menos los márgenes), así que la hoja no se desborda por meterle
# la columna de la ruta. El nombre de la ruta es el que se lleva el espacio grande
# porque es texto y se envuelve; las cifras van justas y alineadas a la derecha.
_ANCHOS_DETALLE_FLETE = (2.4, 5.4, 2.7, 2.9, 3.5)


def _tabla_del_detalle(
    detalles: Sequence[Any], es_proveedor: bool
) -> tuple[list[str], list[list[Any]], tuple[float, ...] | None, tuple[int, ...]]:
    """La tabla "Detalle diario" del comprobante: encabezados, filas y anchos.

    En la del PROVEEDOR queda EXACTAMENTE como estaba (Fecha, Litros, Precio/L,
    Valor y anchos automáticos): ese documento no se toca.

    En la del TRANSPORTADOR entra la columna RUTA, y no es decoración: desde que el
    renglón es (día, ruta), un mismo día puede aparecer dos veces con litros y
    precios distintos. Sin el nombre de la ruta el conductor vería dos líneas con la
    misma fecha y cifras que no se explican, y no tendría manera de saber que una es
    Nápoles y la otra Mira Valle.

    La ruta en nulo se imprime con un guion y no con "Sin ruta" ni en blanco: es lo
    que dicen los renglones viejos —los de antes de este cambio, que eran por día—
    y los días de una recepción a la que nunca se le puso ruta. El guion es el mismo
    que ya usa la tabla de anticipos para lo que no tiene dato.

    Toda la plata y los litros salen por los formateadores colombianos (pesos /
    litros): el productor lee $18.525.000, no $18,525,000.
    """
    if es_proveedor:
        filas = [
            [d.fecha.strftime("%d/%m/%Y"), litros(d.litros), pesos(d.precio_litro), pesos(d.valor)]
            for d in detalles
        ]
        return ["Fecha", "Litros", "Precio/L", "Valor"], filas, None, ()

    # Se ordena acá también, y no solo al armar los renglones, porque el comprobante
    # se imprime muchas veces después de haberlo guardado: al releerlo de la base el
    # orden dentro del día lo da el id de la ruta, que no significa nada para quien
    # lee. Ordenado por nombre, dos impresiones del mismo documento salen iguales.
    #
    # Y con el precio, los litros y el valor de desempate: cuando un día y una ruta
    # salen PARTIDOS en dos líneas —le cambiaron la tarifa a mitad de quincena— la
    # fecha y el nombre de la ruta empatan, y sin desempate el orden lo decidía lo
    # que devolviera el SELECT: el mismo papel impreso dos veces podía salir con las
    # dos líneas al revés. Es el mismo orden de `_orden_del_renglon`.
    en_orden = sorted(
        detalles,
        key=lambda d: (
            d.fecha,
            (d.ruta_nombre or "").lower(),
            Decimal(d.precio_litro or 0),
            Decimal(d.litros or 0),
            Decimal(d.valor or 0),
        ),
    )
    filas = [
        [
            d.fecha.strftime("%d/%m/%Y"),
            d.ruta_nombre or "—",
            litros(d.litros),
            pesos(d.precio_litro),
            pesos(d.valor),
        ]
        for d in en_orden
    ]
    return (
        ["Fecha", "Ruta", "Litros", "Precio/L", "Valor"],
        filas,
        _ANCHOS_DETALLE_FLETE,
        (1,),  # la columna Ruta es texto: se envuelve y se alinea a la izquierda
    )


def _estado_pago(neto_a_pagar: Decimal, pagado: Decimal) -> str:
    """Estado de una liquidación en firme, deducido SIEMPRE de sus cifras.

    Misma idea (y mismo nombre) que en reventa: el estado no se escribe a mano en
    cada camino, se vuelve a calcular desde la plata. Así no puede quedar una
    liquidación marcada "pagada" que todavía deba, ni una "parcial" sin deber
    nada, que es como aparecen los descuadres que el dueño encuentra a mano.

    Sin pagos vuelve a APROBADA: es el estado del que salió (en borrador no se
    puede pagar), y es lo que tiene que quedar cuando se borra el último pago.
    """
    if pagado <= CERO:
        return ESTADO_APROBADA
    return ESTADO_PAGADA if pagado >= neto_a_pagar else ESTADO_PARCIAL


def _refrescar_saldo(liquidacion: Liquidacion) -> None:
    """Deja el saldo al día: lo que falta por pagar.

    Un solo sitio calcula esta resta para que la igualdad que el dueño verifica a
    mano —neto a pagar = pagado + saldo— no dependa de acordarse de repetirla
    bien en los cinco caminos que recalculan una liquidación.
    """
    liquidacion.saldo = liquidacion.neto_a_pagar - Decimal(liquidacion.pagado or 0)


def _bloquear(db: Session, liquidacion: Liquidacion) -> Liquidacion:
    """Relee la liquidación con FOR UPDATE antes de tocarle la plata.

    Sin esto, dos pagos a la vez sobre la misma liquidación se pisan: los dos
    leen el `pagado` viejo, los dos validan contra el mismo saldo y el segundo
    escribe encima del primero. Se pierde un pago —el proveedor reclama y en el
    sistema no está— y la cuenta deja de cuadrar.

    Tres detalles que ya costaron caro en reventa y que aquí también aplican:

    - `populate_existing`: sin él, el FOR UPDATE bloquea la fila en la base pero
      SQLAlchemy devuelve el objeto que ya tenía en memoria, CON LOS VALORES
      VIEJOS. Y es peor de lo que suena: quien llega segundo espera el candado
      justo mientras el primero escribe, así que al soltarse tiene en la mano
      exactamente los datos de antes.
    - `pagos` es lazy="selectin", no "joined": con un LEFT JOIN de por medio
      Postgres rechaza el FOR UPDATE con 0A000.
    - Y por eso mismo hay que apagar aquí el eager load de `proveedor` y
      `transportador`, que SÍ son lazy="joined" sobre FK anulables: sin
      `lazyload` esta consulta saldría con dos LEFT JOIN y Postgres la rechazaría
      con ese mismo 0A000. Quedan como carga diferida, así que el nombre del
      tercero se sigue leyendo igual cuando la respuesta lo pide.

    SQLite descarta el FOR UPDATE en silencio, así que la suite no delata nada de
    esto. La corrección se sostiene por lectura del código, no por la prueba.
    """
    return db.execute(
        select(Liquidacion)
        .where(Liquidacion.id == liquidacion.id)
        .options(lazyload(Liquidacion.proveedor), lazyload(Liquidacion.transportador))
        .execution_options(populate_existing=True)
        .with_for_update()
    ).scalar_one()


class LiquidacionService(BaseService[Liquidacion]):
    repository_cls = LiquidacionRepository
    modulo = "liquidaciones"

    # ------------------------------------------------------------- generación
    def generar(
        self,
        periodo_inicio: date,
        periodo_fin: date,
        tipo: str = "ambos",
        proveedor_id: uuid.UUID | None = None,
    ) -> list[Liquidacion]:
        if periodo_fin < periodo_inicio:
            raise BusinessError("El fin del período no puede ser anterior al inicio")
        recepciones_repo = RecepcionRepository(self.db, self.ctx.empresa_id)

        # EL FLETE VA PRIMERO, y el orden importa aunque las dos liquidaciones sean
        # independientes (cada una se marca en su propia columna y ninguna le quita
        # días a la otra). La razón es una sola: el reparto del flete MUEVE las fotos
        # (`recepciones.valor_transporte`) para que sumen exacto el renglón del
        # comprobante del transportador, y la liquidación del proveedor guarda la suma
        # de esas fotos en su columna informativa `valor_transporte`. Armándola antes,
        # esa columna quedaba con las fotos de ANTES del reparto —la de Marleny decía
        # $20.022,84 y su recepción $20.022,85— y se corregía sola en el siguiente
        # recuadre: una cifra que se mueve sin causa visible.
        #
        # La RESPUESTA sigue saliendo con las de proveedor primero: es el orden en que
        # la pantalla las lista, y no hay razón para moverlo.
        transportadores = (
            self._generar_transportadores(recepciones_repo, periodo_inicio, periodo_fin)
            if tipo in ("transportador", "ambos")
            else []
        )
        proveedores = (
            self._generar_proveedores(
                recepciones_repo, periodo_inicio, periodo_fin, proveedor_id
            )
            if tipo in ("proveedor", "ambos")
            else []
        )
        return proveedores + transportadores

    def _generar_proveedores(
        self,
        recepciones_repo: RecepcionRepository,
        inicio: date,
        fin: date,
        proveedor_id: uuid.UUID | None,
    ) -> list[Liquidacion]:
        pendientes = recepciones_repo.sin_liquidar(inicio, fin, proveedor_id)
        por_proveedor: dict[uuid.UUID, list[RecepcionLeche]] = defaultdict(list)
        for r in pendientes:
            por_proveedor[r.proveedor_id].append(r)

        anticipos_repo = AnticipoRepository(self.db, self.ctx.empresa_id)
        generadas = []
        for prov_id, recepciones in por_proveedor.items():
            total_litros = sum((r.cantidad_litros for r in recepciones), CERO)
            valor_bruto = sum((r.valor_bruto for r in recepciones), CERO)
            bonificaciones = sum((r.bonificaciones for r in recepciones), CERO)
            descuentos = sum((r.descuentos for r in recepciones), CERO)
            valor_total = valor_bruto + bonificaciones - descuentos

            # LOS ANTICIPOS SE APLICAN COMPLETOS, TODOS. Aunque sumen más que la
            # quincena y el saldo quede negativo: ver la nota "EL SALDO NEGATIVO ES LA
            # VERDAD" en `_aplicar_anticipos_pendientes`. Un anticipo que se soltara por
            # no caber es plata que sale dos veces.
            anticipos = anticipos_repo.pendientes_de(prov_id, fin)
            total_anticipos = sum((Decimal(a.valor) for a in anticipos), CERO)

            liquidacion = Liquidacion(
                empresa_id=self.ctx.empresa_id,
                tipo=TIPO_PROVEEDOR,
                proveedor_id=prov_id,
                periodo_inicio=inicio,
                periodo_fin=fin,
                total_litros=total_litros,
                # `_centavos` y no `.quantize(...)` pelado: sin el modo, Python
                # redondea con ROUND_HALF_EVEN y $2,505 daría $2,50, cuando toda la
                # plata de este proyecto sube el medio centavo ($2,51).
                precio_promedio=_centavos(valor_bruto / total_litros) if total_litros else CERO,
                valor_bruto=valor_bruto,
                bonificaciones=bonificaciones,
                descuentos=descuentos,
                valor_transporte=sum((r.valor_transporte for r in recepciones), CERO),
                anticipos=total_anticipos,
                valor_total=valor_total,
                saldo=valor_total - total_anticipos,
                estado=ESTADO_BORRADOR,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            liquidacion.detalles = [
                LiquidacionDetalle(
                    fecha=r.fecha, litros=r.cantidad_litros, precio_litro=r.precio_litro, valor=r.valor_neto
                )
                for r in sorted(recepciones, key=lambda x: x.fecha)
            ]
            self.db.add(liquidacion)
            self.db.flush()
            for r in recepciones:
                r.liquidacion_id = liquidacion.id
            for a in anticipos:
                a.liquidacion_id = liquidacion.id
            self.db.flush()
            self._audit("crear", liquidacion.id, None, serialize_entity(liquidacion))
            generadas.append(liquidacion)
        return generadas

    def _generar_transportadores(
        self, recepciones_repo: RecepcionRepository, inicio: date, fin: date
    ) -> list[Liquidacion]:
        stmt = recepciones_repo.base_query().where(
            RecepcionLeche.fecha >= inicio,
            RecepcionLeche.fecha <= fin,
            RecepcionLeche.liquidacion_transporte_id.is_(None),
            RecepcionLeche.transportador_id.is_not(None),
            RecepcionLeche.estado == "activo",
        )
        pendientes = list(self.db.scalars(stmt).all())
        por_transportador: dict[uuid.UUID, list[RecepcionLeche]] = defaultdict(list)
        for r in pendientes:
            por_transportador[r.transportador_id].append(r)

        anticipos_repo = AnticipoRepository(self.db, self.ctx.empresa_id)
        generadas = []
        for trans_id, recepciones in por_transportador.items():
            total_litros = sum((r.cantidad_litros for r in recepciones), CERO)
            # UN RENGLÓN POR DÍA Y RUTA, no por día: el transportador puede haber
            # hecho las dos rutas el mismo día y cobrar distinto en cada una. La
            # cuenta está en `_reparto_del_flete`, la misma que usan el recálculo y
            # la pre-liquidación, y es la que garantiza que litros × precio dé el
            # valor en cada renglón y que la columna sume el total.
            #
            # Ninguna foto está congelada acá: estas recepciones tienen
            # `liquidacion_transporte_id` en nulo, o sea que su flete no está en
            # ninguna liquidación y menos en una pagada.
            #
            # Y POR ESO MISMO SE VUELVE A DERIVAR EL FLETE CON LA TARIFA DE HOY, igual
            # que al recalcular. Por ese flete no ha salido un peso, así que la tarifa
            # que manda es la que está viva en el sistema: si el dueño tecleó $100 y
            # después lo corrigió a $242,76, el comprobante tiene que salir con
            # $242,76 —era imprimir una tarifa que ya no existe en ninguna pantalla, y
            # el conductor no podía reproducir la columna—. Además deja generar y
            # recalcular dando EL MISMO papel: sin esto, el primer recuadre le cambiaba
            # la cifra al comprobante sin que nadie hubiera tocado nada.
            #
            # PRIMERO SE MIRA SIN ESCRIBIR NADA, y este orden es el arreglo de un caso
            # destructivo: si con la tarifa de hoy no hay nada que liquidar, GENERAR no
            # puede dejar rastro. Antes se re-derivaba de entrada y después se decidía, y
            # con la tarifa en cero pasaba esto: un día de 44,23 L en una ruta a $242,76
            # (foto $10.737,27); el dueño le quita esa ruta de la lista del transportador
            # y su tarifa general está en 0 —que es el valor por omisión, o sea el caso
            # normal de quien no la llenó—; oprime Generar; la re-derivación le pone la
            # foto en $0,00 y, como el total quedó en cero, el transportador se salta sin
            # crear comprobante. Resultado: $10.737,27 borrados en silencio, el día sin
            # papel y sin cifra, y nadie a quien preguntarle.
            #
            # Con las copias de solo lectura (`_con_el_flete_de_hoy`, la misma cuenta que
            # usa el avance) se sabe ANTES si va a salir comprobante. Si no sale, no se
            # toca ni una foto: el día queda como estaba, sigue pendiente, y cuando la
            # tarifa se arregle generar otra vez lo derivará bien. Una tarifa que se
            # volvió cero no borra una foto que valía plata.
            #
            # NO SE REBOTA CON UN ERROR a propósito: "Generar" es un botón de barrida que
            # recorre TODOS los transportadores del período, y tumbar la corrida entera
            # —dejando sin comprobante a los que sí tenían tarifa— por uno al que le falta
            # llenar la suya sería peor. El transportador sin tarifa simplemente no sale
            # en la respuesta, igual que quien no tuvo recepciones.
            previstos = _reparto_del_flete(_con_el_flete_de_hoy(recepciones)).renglones
            if sum((renglon["valor"] for renglon in previstos), CERO) == CERO:
                continue
            self._rederivar_el_flete(recepciones, frozenset())
            reparto = _reparto_del_flete(recepciones)
            # EL TOTAL ES LA SUMA DE LOS RENGLONES, no la de las fotos como estaban:
            # el reparto acomoda las fotos para que las dos cifras sean la misma, y
            # tomarla de los renglones deja el papel cuadrado por definición.
            valor_transporte = sum((renglon["valor"] for renglon in reparto.renglones), CERO)
            # Igual que en la de proveedor: TODOS los anticipos pendientes, completos.
            # Ver la nota "EL SALDO NEGATIVO ES LA VERDAD" en
            # `_aplicar_anticipos_pendientes`.
            anticipos = anticipos_repo.pendientes_transportador(trans_id, fin)
            total_anticipos = sum((Decimal(a.valor) for a in anticipos), CERO)

            liquidacion = Liquidacion(
                empresa_id=self.ctx.empresa_id,
                tipo=TIPO_TRANSPORTADOR,
                transportador_id=trans_id,
                periodo_inicio=inicio,
                periodo_fin=fin,
                total_litros=total_litros,
                # `_centavos` y no `.quantize(...)` pelado: ver la nota en
                # `_generar_proveedores`. $2,505 el litro es $2,51, no $2,50.
                precio_promedio=_centavos(valor_transporte / total_litros)
                if total_litros
                else CERO,
                valor_transporte=valor_transporte,
                anticipos=total_anticipos,
                valor_total=valor_transporte,
                saldo=valor_transporte - total_anticipos,
                estado=ESTADO_BORRADOR,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            liquidacion.detalles = [
                LiquidacionDetalle(
                    fecha=renglon["fecha"],
                    ruta_id=renglon["ruta_id"],
                    litros=renglon["litros"],
                    precio_litro=renglon["precio_litro"],
                    valor=renglon["valor"],
                    created_by=self.ctx.user_id,
                    updated_by=self.ctx.user_id,
                )
                for renglon in reparto.renglones
            ]
            self.db.add(liquidacion)
            self.db.flush()
            for r in recepciones:
                r.liquidacion_transporte_id = liquidacion.id
            self._aplicar_fotos_del_flete(recepciones, reparto.fotos)
            for a in anticipos:
                a.liquidacion_id = liquidacion.id
            self.db.flush()
            self._audit("crear", liquidacion.id, None, serialize_entity(liquidacion))
            generadas.append(liquidacion)
        return generadas

    # -------------------------------------------------------- previsualización
    def previsualizar(
        self, inicio: date, fin: date, tipo: str, tercero_id: uuid.UUID
    ) -> list[PreLiquidacionRead]:
        """Calcula cómo va un tercero en el período SIN generar ni guardar nada.

        Sirve para mostrarle a un proveedor/transportador su avance ("¿cómo voy?")
        antes de la liquidación oficial. No toca recepciones ni anticipos.
        """
        if fin < inicio:
            raise BusinessError("El fin del período no puede ser anterior al inicio")
        recepciones_repo = RecepcionRepository(self.db, self.ctx.empresa_id)
        if tipo == TIPO_PROVEEDOR:
            pre = self._preview_proveedor(recepciones_repo, tercero_id, inicio, fin)
        elif tipo == TIPO_TRANSPORTADOR:
            pre = self._preview_transportador(recepciones_repo, tercero_id, inicio, fin)
        else:
            raise BusinessError("Tipo inválido para pre-liquidación")
        return [pre] if pre else []

    def _preview_proveedor(
        self, recepciones_repo: RecepcionRepository, prov_id: uuid.UUID, inicio: date, fin: date
    ) -> PreLiquidacionRead | None:
        recepciones = recepciones_repo.sin_liquidar(inicio, fin, prov_id)
        if not recepciones:
            return None
        total_litros = sum((r.cantidad_litros for r in recepciones), CERO)
        valor_bruto = sum((r.valor_bruto for r in recepciones), CERO)
        bonificaciones = sum((r.bonificaciones for r in recepciones), CERO)
        descuentos = sum((r.descuentos for r in recepciones), CERO)
        valor_transporte = sum((r.valor_transporte for r in recepciones), CERO)
        valor_total = valor_bruto + bonificaciones - descuentos
        anticipos = AnticipoRepository(self.db, self.ctx.empresa_id).pendientes_de(prov_id, fin)
        total_anticipos = sum((a.valor for a in anticipos), CERO)
        proveedor = ProveedorRepository(self.db, self.ctx.empresa_id).get(prov_id)
        return PreLiquidacionRead(
            tipo=TIPO_PROVEEDOR,
            tercero_id=prov_id,
            tercero_nombre=proveedor.nombre if proveedor else "-",
            tercero_detalle=getattr(proveedor, "vereda", None) if proveedor else None,
            periodo_inicio=inicio,
            periodo_fin=fin,
            total_litros=total_litros,
            precio_promedio=_centavos(valor_bruto / total_litros) if total_litros else CERO,
            valor_bruto=valor_bruto,
            bonificaciones=bonificaciones,
            descuentos=descuentos,
            valor_transporte=valor_transporte,
            anticipos=total_anticipos,
            valor_total=valor_total,
            saldo=valor_total - total_anticipos,
            detalles=[
                PreLiquidacionDetalle(
                    fecha=r.fecha, litros=r.cantidad_litros, precio_litro=r.precio_litro, valor=r.valor_neto
                )
                for r in sorted(recepciones, key=lambda x: x.fecha)
            ],
            anticipos_detalle=[
                PreLiquidacionAnticipo(fecha=a.fecha, valor=a.valor, observaciones=a.observaciones)
                for a in anticipos
            ],
        )

    def _preview_transportador(
        self, recepciones_repo: RecepcionRepository, trans_id: uuid.UUID, inicio: date, fin: date
    ) -> PreLiquidacionRead | None:
        stmt = recepciones_repo.base_query().where(
            RecepcionLeche.fecha >= inicio,
            RecepcionLeche.fecha <= fin,
            RecepcionLeche.liquidacion_transporte_id.is_(None),
            RecepcionLeche.transportador_id == trans_id,
            RecepcionLeche.estado == "activo",
        )
        recepciones = list(self.db.scalars(stmt).all())
        if not recepciones:
            return None
        total_litros = sum((r.cantidad_litros for r in recepciones), CERO)
        # Los MISMOS renglones que va a tener el comprobante de verdad, por la misma
        # función. El avance NO escribe nada —no reparte las fotos—, pero el total
        # que muestra sale de los renglones y no de las fotos como están hoy: si
        # sumara las fotos, el "¿cómo voy?" podría decir un centavo distinto del
        # papel que el transportador va a firmar.
        renglones = _renglones_de_transporte(recepciones)
        valor_transporte = sum((renglon["valor"] for renglon in renglones), CERO)
        anticipos = AnticipoRepository(self.db, self.ctx.empresa_id).pendientes_transportador(
            trans_id, fin
        )
        total_anticipos = sum((a.valor for a in anticipos), CERO)
        transportador = TransportadorRepository(self.db, self.ctx.empresa_id).get(trans_id)
        return PreLiquidacionRead(
            tipo=TIPO_TRANSPORTADOR,
            tercero_id=trans_id,
            tercero_nombre=transportador.nombre if transportador else "-",
            periodo_inicio=inicio,
            periodo_fin=fin,
            total_litros=total_litros,
            precio_promedio=_centavos(valor_transporte / total_litros) if total_litros else CERO,
            valor_bruto=CERO,
            bonificaciones=CERO,
            descuentos=CERO,
            valor_transporte=valor_transporte,
            anticipos=total_anticipos,
            valor_total=valor_transporte,
            saldo=valor_transporte - total_anticipos,
            detalles=[
                PreLiquidacionDetalle(
                    fecha=renglon["fecha"],
                    ruta_id=renglon["ruta_id"],
                    ruta_nombre=renglon["ruta_nombre"],
                    litros=renglon["litros"],
                    precio_litro=renglon["precio_litro"],
                    valor=renglon["valor"],
                )
                for renglon in renglones
            ],
            anticipos_detalle=[
                PreLiquidacionAnticipo(fecha=a.fecha, valor=a.valor, observaciones=a.observaciones)
                for a in anticipos
            ],
        )

    # --------------------------------------------- corrección de un día suelto
    def _recepciones_de(self, liquidacion: Liquidacion) -> list[RecepcionLeche]:
        """Las recepciones que esta liquidación tiene apartadas.

        Va por el repositorio para no saltarse el filtro por empresa ni el de
        borrados: son datos de plata de un tenant y aquí se reescriben.

        Y SOLO LAS ACTIVAS, igual que `RecepcionRepository.sin_liquidar`, que es el
        que las metió en esta liquidación. Un día apagado —`estado = 'inactivo'`— es
        un día que la quesera decidió no contar: sale de la grilla de recepciones y de
        la contabilidad, que sí filtra activo. Sin este filtro el recuadre lo
        conservaba y el productor seguía cobrando 126,71 L cuando le quedaban 82,48 L
        activos. Ver la nota de `_recepciones_transporte_de` sobre el día que se apaga
        DESPUÉS de pagado.
        """
        stmt = (
            RecepcionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(
                RecepcionLeche.liquidacion_id == liquidacion.id,
                RecepcionLeche.estado == "activo",
            )
            .order_by(RecepcionLeche.fecha)
        )
        return list(self.db.scalars(stmt).all())

    def _renglon_del_dia(self, liquidacion: Liquidacion, fecha: date) -> LiquidacionDetalle:
        """El renglón de ese día, creándolo si la liquidación todavía no lo tiene.

        Hace falta porque los renglones ya no son fijos: desde que se puede
        corregir un día que pertenece a una liquidación sin pagar, un día puede
        aparecer (una recepción que cambió de fecha) o desaparecer (una recepción
        borrada). Ver `_quitar_renglones_sin_dia`.

        SOLO PARA LA DEL PROVEEDOR, que es donde el renglón sigue siendo el día y
        la fecha lo identifica. En la del transportador el renglón es (día, ruta) y
        un día puede traer varios, así que ahí los renglones se reemplazan completos
        (ver `_recalcular_transporte_desde_recepciones`).
        """
        for detalle in liquidacion.detalles:
            if detalle.fecha == fecha:
                return detalle
        detalle = LiquidacionDetalle(
            fecha=fecha, created_by=self.ctx.user_id, updated_by=self.ctx.user_id
        )
        liquidacion.detalles.append(detalle)
        return detalle

    def _quitar_renglones_sin_dia(self, liquidacion: Liquidacion, fechas: set[date]) -> None:
        """Bota los renglones de días que ya no tienen recepción detrás.

        Si se quedaran, la columna Valor del comprobante dejaría de sumar el
        VALOR TOTAL —que se calcula desde las recepciones que quedan—, y ese
        cuadre es justo el que el dueño verifica a mano contra el cuaderno.
        Con cascade delete-orphan, sacarlo de la colección lo borra.
        """
        for sobrante in [d for d in liquidacion.detalles if d.fecha not in fechas]:
            liquidacion.detalles.remove(sobrante)

    def _recalcular_desde_recepciones(self, liquidacion: Liquidacion) -> None:
        """Rearma el detalle y los totales desde las recepciones del período.

        Se recalcula TODO en vez de "ajustar la diferencia" porque el dueño suma
        la columna Valor a mano y compara con el total: si el total se arrastrara
        de un cálculo anterior, un peso de diferencia lo mandaría a buscar un
        error que no existe.

        La clave del cuadre está en que el valor del día se arma con las MISMAS
        piezas que se suman arriba (bruto + bonificaciones - descuentos) y no
        leyendo `valor_neto`: así la suma de los días es idéntica al valor total,
        sin depender de cómo redondeó cada quien.
        """
        recepciones = self._recepciones_de(liquidacion)
        por_fecha = {r.fecha: r for r in recepciones}

        self._quitar_renglones_sin_dia(liquidacion, set(por_fecha))
        for fecha, recepcion in por_fecha.items():
            detalle = self._renglon_del_dia(liquidacion, fecha)
            detalle.litros = recepcion.cantidad_litros
            detalle.precio_litro = recepcion.precio_litro
            detalle.valor = (
                Decimal(recepcion.valor_bruto)
                + Decimal(recepcion.bonificaciones)
                - Decimal(recepcion.descuentos)
            )
            detalle.updated_by = self.ctx.user_id
        # El comprobante se lee de arriba abajo por fecha; los renglones nuevos se
        # agregaron al final de la colección, así que se vuelve a ordenar.
        liquidacion.detalles.sort(key=lambda d: d.fecha)

        total_litros = sum((Decimal(r.cantidad_litros) for r in recepciones), CERO)
        valor_bruto = sum((Decimal(r.valor_bruto) for r in recepciones), CERO)
        bonificaciones = sum((Decimal(r.bonificaciones) for r in recepciones), CERO)
        descuentos = sum((Decimal(r.descuentos) for r in recepciones), CERO)
        valor_total = valor_bruto + bonificaciones - descuentos

        liquidacion.total_litros = total_litros
        liquidacion.valor_bruto = valor_bruto
        liquidacion.bonificaciones = bonificaciones
        liquidacion.descuentos = descuentos
        liquidacion.valor_transporte = sum((Decimal(r.valor_transporte) for r in recepciones), CERO)
        # `_centavos` y no `.quantize(CENTAVOS)`: sin el modo manda el
        # ROUND_HALF_EVEN de Python y $2,505 daría $2,50. Acá el medio centavo sube.
        liquidacion.precio_promedio = (
            _centavos(valor_bruto / total_litros) if total_litros else CERO
        )
        liquidacion.valor_total = valor_total
        # Los anticipos no se tocan: ya quedaron aplicados a esta liquidación al
        # generarla y corregir un precio no cambia lo que se le adelantó.
        _refrescar_saldo(liquidacion)

    def _recepciones_transporte_de(self, liquidacion: Liquidacion) -> list[RecepcionLeche]:
        """Las recepciones cuyo FLETE tiene apartado esta liquidación.

        Ojo con la marca: la de leche va en `liquidacion_id` y la de flete en
        `liquidacion_transporte_id`. Un mismo día lleva las dos y son
        liquidaciones distintas, de dos personas distintas.

        SOLO LAS ACTIVAS, igual que `_generar_transportadores`, que es el que las
        metió acá. Un día apagado ya no se le paga a nadie: al transportador se le
        estaban pagando $10.737,27 de un día que la grilla no mostraba y que
        contabilidad —que sí filtra activo— registraba como gasto de menos.

        QUÉ PASA CON UN DÍA QUE SE APAGA DESPUÉS DE PAGADO: no pasa, y por dos
        candados independientes. `estado` está en `_CAMPOS_DEL_FLETE` y en
        `_CAMPOS_DE_LA_LECHE`, así que el PUT que lo apaga rebota con un 422 en cuanto
        una de las dos liquidaciones movió plata (apagar un día pagado es como
        borrarlo); y si por otro camino se apagara, `recuadrar` rebota antes de llegar
        acá. O sea que una liquidación pagada CONSERVA su día apagado y su cifra, que
        es lo correcto: esa plata ya salió y el comprobante que el tercero tiene en la
        mano no se puede desdecir. El día apagado se lo lleva la siguiente quincena,
        no la que ya se pagó.

        La marca (`liquidacion_transporte_id`) NO se le suelta al día apagado, a
        propósito: si se soltara, el día quedaría suelto y una generación posterior lo
        volvería a liquidar —cobrándoselo dos veces si la primera ya se había pagado—.
        Con la marca puesta, prenderlo otra vez lo devuelve a SU liquidación en el
        siguiente recuadre.
        """
        stmt = (
            RecepcionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(
                RecepcionLeche.liquidacion_transporte_id == liquidacion.id,
                RecepcionLeche.estado == "activo",
            )
            .order_by(RecepcionLeche.fecha)
        )
        return list(self.db.scalars(stmt).all())

    def _recalcular_transporte_desde_recepciones(
        self, liquidacion: Liquidacion, *, rederivar_tarifas: bool
    ) -> int:
        """Rearma la liquidación de flete desde las recepciones que recogió.

        Existe por la misma razón que la de proveedor: si se corrigen los litros
        de un día, el flete de ese día también cambia (se cobra por litro
        recogido) y el comprobante del transportador quedaría diciendo una cifra
        que ya no corresponde a sus recepciones.

        `rederivar_tarifas` ES LA LÍNEA QUE SEPARA LOS DOS TRABAJOS, y hay que leerla
        con cuidado porque los dos caminos entran por aquí:

          · RECALCULAR (el botón que el dueño oprime) y GENERAR pasan `True`: vuelven a
            derivar la cifra del flete de cada día con la TARIFA DE HOY. Es lo que pidió
            el dueño con "que se pueda recalcular la liquidación del transportador":
            sin eso una tarifa mal tecleada quedaba cobrándose para siempre, porque la
            tarifa no es un campo de la recepción.
          · EL RECUADRE automático —la cascada que se dispara al editar una recepción o
            un anticipo— pasa `False`: vuelve a SUMAR y a REPARTIR con las fotos como
            están, y no re-precifica. Su trabajo es que la liquidación siga cuadrando
            con sus días, no ponerle otra tarifa a un comprobante que nadie pidió
            re-precificar. Con `True` en los dos, escribir una observación en un día
            —un campo que no traba nada y que no le mueve la cuenta a nadie— le subía
            el comprobante APROBADO de $30.760,12 a $38.013,00 y le quitaba el visto
            bueno: $7.252,88 de cambio sin causa visible.

        EL REPARTO DE CENTAVOS CORRE EN LOS DOS CAMINOS, y no es negociable: es lo que
        hace que la suma de las fotos dé EXACTO el renglón del comprobante. Si el
        recuadre se saltara el reparto por no estar re-derivando, se reabriría el
        centavo que ya se cerró.

        Aquí el renglón es POR DÍA Y RUTA —el transportador cobra la suma de litros
        que recogió ese día EN ESA RUTA, no proveedor por proveedor, y en cada ruta
        cobra distinto—, armado por la misma `_reparto_del_flete` que usa
        `_generar_transportadores`. Recalcular y generar tienen que dar el mismo
        comprobante: si no, el papel cambiaría solo por haber corregido un dato de
        otro día.

        LOS RENGLONES SE REEMPLAZAN COMPLETOS en vez de buscar el que ya existía
        para esa fecha (como sí hace el de proveedor, que sigue siendo uno por día).
        La razón es que la fecha dejó de identificar un renglón: un día puede traer
        dos rutas, y hasta dos veces la misma ruta si le cambiaron la tarifa a mitad
        de quincena. Emparejar por fecha dejaría un renglón viejo con las cifras de
        otro reparto, que es exactamente el descuadre que esto evita. El id del
        renglón no le hace falta a nadie acá: corregir el precio de un día está
        cerrado a propósito en las de transportador (ver `actualizar_precio_detalle`).

        Devuelve CUÁNTOS DÍAS QUEDARON de verdad con otra cifra de flete, que va a la
        bitácora: esto mueve dinero y mañana alguien va a preguntar por qué el
        comprobante cambió de cifra.

        Se cuenta comparando la foto de ANTES de tocar nada contra la que queda al
        final, después del reparto, y no lo que la re-derivación creyó cambiar. La
        diferencia es real y el libro mentía por ella: el reparto le entrega un centavo
        a la recepción más grande del grupo (44,23 + 82,48 L a $242,76 dejan una foto en
        $20.022,85 cuando su propia cuenta da $20.022,84), así que la vuelta siguiente
        la re-derivación la "corrige" a $20.022,84, el reparto se la vuelve a subir a
        $20.022,85 y la foto termina idéntica a como empezó. El libro decía "le cambió
        el flete a 1 día" en un recálculo que no movió ni un peso, y el dueño se iba a
        buscar un cambio que no existe.
        """
        recepciones = self._recepciones_transporte_de(liquidacion)
        congeladas = self._fotos_congeladas(liquidacion, recepciones)
        # La foto de cada día ANTES de tocar nada: es la única referencia honesta para
        # decirle a la bitácora cuántos días cambiaron de verdad.
        antes_de_tocar = {r.id: _foto_del_flete(r) for r in recepciones}
        if rederivar_tarifas:
            self._rederivar_el_flete(recepciones, congeladas)

        reparto = _reparto_del_flete(recepciones, congeladas)
        # Se vacía y SE BAJA EL DELETE antes de insertar los nuevos. Con
        # delete-orphan, sacarlos de la colección los borra de verdad; el flush de
        # por medio es para que los INSERT no se adelanten a los DELETE, que es un
        # orden que SQLAlchemy no garantiza y que ya costó un IntegrityError en la
        # tabla de tarifas por ruta.
        liquidacion.detalles.clear()
        self.db.flush()
        for renglon in reparto.renglones:
            liquidacion.detalles.append(
                LiquidacionDetalle(
                    fecha=renglon["fecha"],
                    ruta_id=renglon["ruta_id"],
                    litros=renglon["litros"],
                    precio_litro=renglon["precio_litro"],
                    valor=renglon["valor"],
                    created_by=self.ctx.user_id,
                    updated_by=self.ctx.user_id,
                )
            )
        self._aplicar_fotos_del_flete(recepciones, reparto.fotos)
        dias_cambiados = sum(
            1 for r in recepciones if _foto_del_flete(r) != antes_de_tocar[r.id]
        )

        total_litros = sum((Decimal(r.cantidad_litros) for r in recepciones), CERO)
        # El total sale de LOS RENGLONES, que es lo mismo que la suma de las fotos
        # después del reparto. Tomarlo de los renglones es lo que hace que el papel
        # cuadre por definición y no por casualidad del redondeo.
        valor_transporte = sum((renglon["valor"] for renglon in reparto.renglones), CERO)
        liquidacion.total_litros = total_litros
        liquidacion.valor_transporte = valor_transporte
        # `_centavos` y no `.quantize(CENTAVOS)`: el medio centavo sube, como en
        # toda la plata del proyecto.
        liquidacion.precio_promedio = (
            _centavos(valor_transporte / total_litros) if total_litros else CERO
        )
        liquidacion.valor_total = valor_transporte
        _refrescar_saldo(liquidacion)
        return dias_cambiados

    # ------------------------------------------------- las fotos del flete
    def _rederivar_el_flete(
        self, recepciones: Sequence[RecepcionLeche], congeladas: frozenset[uuid.UUID]
    ) -> None:
        """Vuelve a calcular el flete de cada día con LA TARIFA DE HOY.

        ES EL ARREGLO QUE PIDIÓ EL DUEÑO. La tarifa entra en la plata pero NO es un
        campo de la recepción: vive en el transportador (o en su fila de la ruta). Así
        que una tarifa mal tecleada —$100 en vez de $242,76— quedaba cobrándose para
        siempre, porque recalcular solo volvía a SUMAR las fotos ya tomadas. Peor: el
        comprobante IMPRIMÍA esa tarifa vieja, que ya no existe en ninguna pantalla,
        porque el renglón la deriva de la foto. En un día de 44 L eso son $6.281,44.

        Después de esto todas las fotos del grupo son litros × tarifa de hoy, así que
        el reparto de `_reparto_del_flete` encuentra UNA sola tarifa por (día, ruta) y
        el papel sale con la tarifa que el conductor reconoce y que está en la
        pantalla. Los centavos del redondeo los sigue acomodando el reparto.

        SE ABSTIENE COMPLETA si hay UNA sola foto congelada. Congelada quiere decir
        que por ese flete ya salió plata, y `_fotos_congeladas` congela todas o
        ninguna; si mañana congelara unas pocas, mezclar días re-derivados con días
        pagados dejaría el comprobante con dos tarifas para la misma ruta —el descuadre
        que esto viene a quitar—. Mejor no tocar nada que tocar a medias.

        NO devuelve cuántos días cambió, y eso es a propósito: lo que la bitácora tiene
        que anotar es cuántas fotos quedaron distintas AL FINAL, después del reparto de
        centavos, y eso solo lo sabe quien llama (ver
        `_recalcular_transporte_desde_recepciones`). Contarlo aquí hacía que el libro
        anotara días cambiados en recálculos que no movieron un peso.
        """
        if congeladas:
            return
        cambiados = 0
        for recepcion in recepciones:
            nueva = _flete_de_hoy(recepcion)
            if nueva == _foto_del_flete(recepcion):
                continue
            recepcion.valor_transporte = nueva
            recepcion.updated_by = self.ctx.user_id
            cambiados += 1
        if cambiados:
            # Se baja el cambio antes de repartir: el reparto vuelve a leer estas
            # mismas filas y la sesión no hace autoflush.
            self.db.flush()

    def _fotos_congeladas(
        self, liquidacion: Liquidacion, recepciones: Sequence[RecepcionLeche]
    ) -> frozenset[uuid.UUID]:
        """Los ids de las recepciones cuya foto del flete NO se puede tocar.

        Está congelada la foto de un flete contra el que ya salió plata: la
        liquidación pagada, o con un solo abono registrado. Esa cifra es la que el
        transportador tiene en la mano y contra la que se le entregó el dinero.

        En la práctica no debería llegar acá ninguna: `recalcular` exige borrador y
        `recuadrar` rebota si hay pagos, así que las recepciones que se rearman son
        siempre de un comprobante que todavía no ha movido plata. Se calcula igual
        porque el que reparte centavos no puede depender de que el de arriba se haya
        acordado de revisar: si mañana aparece otro camino, el reparto se abstiene
        solo y el renglón sale partido en vez de moverle un centavo a un pago hecho.
        """
        if liquidacion.estado == ESTADO_PAGADA or liquidacion.tiene_pagos:
            return frozenset(r.id for r in recepciones)
        return frozenset()

    def _aplicar_fotos_del_flete(
        self, recepciones: Sequence[RecepcionLeche], fotos: dict[uuid.UUID, Decimal]
    ) -> None:
        """Deja escrito en cada recepción el trozo de la plata del renglón que le tocó.

        Es la otra mitad de la decisión "el comprobante manda": el renglón dice
        litros × tarifa redondeado una sola vez, y estas fotos son ese mismo valor
        repartido entre los días que lo componen, de modo que sumen EXACTO el
        renglón. Sin esto el comprobante diría una cifra y sus recepciones otra.

        Mueve centavos, nunca pesos: cada foto queda a lo sumo a un centavo de su
        propia multiplicación (ver `_repartir_al_resto_mayor`). Por eso no se abre una
        entrada de auditoría por recepción —serían decenas por quincena para tapar el
        libro con centavos—: el movimiento queda en la auditoría de la liquidación,
        que es donde el dueño lo va a buscar.
        """
        if not fotos:
            return
        for recepcion in recepciones:
            nueva = fotos.get(recepcion.id)
            if nueva is None:
                continue
            recepcion.valor_transporte = nueva
            recepcion.updated_by = self.ctx.user_id

    def actualizar_precio_detalle(
        self, entity_id: uuid.UUID, detalle_id: uuid.UUID, precio_litro: Decimal
    ) -> Liquidacion:
        """Corrige el precio por litro de un día sin salir del comprobante.

        Nace de un caso real: la liquidación salió a $1.800 el litro cuando no
        era ese el precio, y arreglarlo obligaba a anular la liquidación entera.

        SOLO en borrador y SOLO de proveedor:
        - Aprobada o anulada no se toca ni por la dirección del endpoint: ese
          precio ya se le pagó a alguien (o la liquidación ya se dio de baja).
        - En la de transportador el "precio" del renglón es la tarifa de flete
          del día y agrupa varias recepciones de la ruta; cambiarla ahí sería
          otra cosa, y se cruzaría con el transporte que ya lleva la liquidación
          del proveedor. Se deja por fuera a propósito.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado != ESTADO_BORRADOR:
            raise BusinessError(
                f"Esta liquidación está en '{liquidacion.estado}': solo se puede "
                "corregir el precio mientras sea un borrador"
            )
        if liquidacion.tipo != TIPO_PROVEEDOR:
            raise BusinessError(
                "Solo se puede corregir el precio por litro en liquidaciones de proveedor"
            )

        detalle = next(
            (d for d in liquidacion.detalles if d.id == detalle_id and d.deleted_at is None), None
        )
        if detalle is None:
            raise NotFoundError("Ese día no pertenece a la liquidación")

        recepcion = self.db.scalars(
            RecepcionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(
                RecepcionLeche.liquidacion_id == liquidacion.id,
                RecepcionLeche.proveedor_id == liquidacion.proveedor_id,
                RecepcionLeche.fecha == detalle.fecha,
            )
        ).first()
        if recepcion is None:
            raise BusinessError(
                "No se encontró la recepción de ese día; anule la liquidación y vuelva a generarla"
            )

        precio = _centavos(precio_litro)
        nuevo_bruto = _centavos(Decimal(recepcion.cantidad_litros) * precio)
        nuevo_neto = nuevo_bruto + Decimal(recepcion.bonificaciones) - Decimal(recepcion.descuentos)
        # Se valida ANTES de escribir nada: si el precio nuevo deja el día en rojo
        # la corrección no debe dejar a medias ni la recepción ni la liquidación.
        if nuevo_neto < CERO:
            raise BusinessError(
                "Con ese precio el valor del día queda negativo: revise los descuentos"
            )

        antes_recepcion = serialize_entity(recepcion)
        antes_liquidacion = serialize_entity(liquidacion)

        recepcion.precio_litro = precio
        recepcion.valor_bruto = nuevo_bruto
        recepcion.valor_neto = nuevo_neto
        recepcion.updated_by = self.ctx.user_id
        # La sesión no hace autoflush: sin este flush, el recálculo volvería a
        # consultar las recepciones y el día corregido podría releerse con el
        # precio viejo. Se baja el cambio antes de sumar.
        self.db.flush()

        self._recalcular_desde_recepciones(liquidacion)
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()

        # Se auditan las dos cosas: la liquidación, que es donde el dueño ve el
        # cambio, y la recepción del día, que es el dato de origen que quedó
        # corregido. La de recepción se arma a mano —y no con self._audit— para
        # que en el libro quede bajo su propio módulo y entidad; si no, saldría
        # como si alguien hubiera editado una liquidación con el id de otra cosa.
        from app.modules.auditoria.models import Auditoria

        self._audit("editar", liquidacion.id, antes_liquidacion, serialize_entity(liquidacion))
        self.db.add(
            Auditoria(
                empresa_id=self.ctx.empresa_id,
                usuario_id=self.ctx.user_id,
                ip=self.ctx.ip,
                modulo="recepcion",
                accion="editar",
                entidad="RecepcionLeche",
                entidad_id=recepcion.id,
                antes=antes_recepcion,
                despues=serialize_entity(recepcion),
            )
        )
        return liquidacion

    # ------------------------------------------------- anticipos del borrador
    def _anticipos_de(self, liquidacion: Liquidacion) -> list[Anticipo]:
        """Los anticipos que hoy están marcados contra esta liquidación.

        Va por el repositorio para no saltarse el filtro por empresa ni el de
        borrados: es plata de un tenant y con esto se reescribe el saldo.
        """
        stmt = (
            AnticipoRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(Anticipo.liquidacion_id == liquidacion.id)
            .order_by(Anticipo.fecha)
        )
        return list(self.db.scalars(stmt).all())

    def _aplicar_anticipos_pendientes(self, liquidacion: Liquidacion) -> Decimal:
        """Le marca al borrador los anticipos del tercero que todavía no se le han
        descontado a nadie, y deja el total y el saldo al día.

        Nace de un caso real: la liquidación de un proveedor se generó el mismo
        día en que después se le registró un anticipo de $500.000. Como los
        anticipos solo se aplicaban en el instante de generar, el borrador quedó
        con "Anticipos aplicados $0" y no había forma de recogerlo: volver a darle
        a "Generar" no hace nada, porque las recepciones ya están apartadas.

        Cuatro cuidados, en este orden de importancia:
        · SOLO en borrador. Aprobada o pagada esa cifra ya se le dio a alguien.
        · Un anticipo no se puede descontar dos veces: `pendientes_de` solo trae
          los que tienen `liquidacion_id` en nulo, así que el que ya se aplicó en
          otra quincena no vuelve a aparecer.
        · SE APLICAN COMPLETOS, TODOS, aunque sumen más que la quincena. Ver abajo.
        · El total NO se le suma encima al guardado: se vuelve a sumar desde los
          anticipos que hoy apuntan a esta liquidación. Así, llamar dos veces
          (el botón oprimido dos veces, un reintento del navegador) da lo mismo.

        EL SALDO NEGATIVO ES LA VERDAD, Y NO SE ESCONDE. Hubo un intento de evitarlo
        soltando el anticipo que no cupiera en la quincena (dejarlo con
        `liquidacion_id` en nulo para descontárselo a la siguiente), y ESO COSTABA
        PLATA DOS VECES. El caso medido, con las cifras: un proveedor con 100 L a
        $1.800 —$180.000 de quincena— y un anticipo de $300.000 QUE YA SE LE ENTREGÓ.
        El comprobante salía con "Anticipos aplicados $0,00" y saldo $180.000, y el
        dueño le pagaba esos $180.000 ENCIMA de los $300.000 que ya le había dado.

        Un saldo por debajo de cero no es un defecto de cuenta: es que el tercero le
        quedó debiendo al negocio, y esconderlo detrás de un anticipo "pendiente" le
        oculta al dueño justo el dato con el que tiene que ir a cobrar. Lo que se hace
        en su lugar es DECIRLO en palabras, en la pantalla y en el papel: ver
        `Liquidacion.le_queda_debiendo` y el rótulo del resumen en `generar_pdf`.
        """
        if liquidacion.estado != ESTADO_BORRADOR:
            return Decimal(liquidacion.anticipos)

        anticipos_repo = AnticipoRepository(self.db, self.ctx.empresa_id)
        if liquidacion.tipo == TIPO_PROVEEDOR:
            pendientes = (
                anticipos_repo.pendientes_de(liquidacion.proveedor_id, liquidacion.periodo_fin)
                if liquidacion.proveedor_id
                else []
            )
        else:
            pendientes = (
                anticipos_repo.pendientes_transportador(
                    liquidacion.transportador_id, liquidacion.periodo_fin
                )
                if liquidacion.transportador_id
                else []
            )
        for anticipo in pendientes:
            anticipo.liquidacion_id = liquidacion.id
            anticipo.updated_by = self.ctx.user_id
        # Se baja el cambio antes de volver a leer: sin autoflush, la consulta de
        # abajo no vería los que se acaban de marcar y el total saldría corto.
        self.db.flush()

        total = sum((Decimal(a.valor) for a in self._anticipos_de(liquidacion)), CERO)
        liquidacion.anticipos = total
        _refrescar_saldo(liquidacion)
        return total

    def recalcular(
        self, entity_id: uuid.UUID, *, rederivar_tarifas: bool = True
    ) -> Liquidacion:
        """Vuelve a armar el borrador con lo que hay hoy en el sistema.

        Es la salida para el caso de siempre: la liquidación se generó y después
        se registró un anticipo (o se corrigió una recepción). Un borrador todavía
        no es plata entregada, así que se puede volver a cuadrar; aprobada o
        pagada rebota, porque ahí ya se le pagó a alguien.

        EN LA DE TRANSPORTADOR HACE UNA COSA MÁS, y es la que pidió el dueño: vuelve a
        derivar el flete de cada día con la TARIFA DE HOY antes de rearmar los
        renglones (ver `_rederivar_el_flete`). Es el único camino que le queda a una
        tarifa mal tecleada para llegar a los días ya liquidados.

        `rederivar_tarifas=False` es lo que usa el RECUADRE en cascada (ver `recuadrar`):
        misma suma, mismo reparto de centavos, pero sin re-precificar. Por omisión es
        `True`, que es el botón "Recalcular" del comprobante: quien lo oprime está
        pidiendo exactamente eso.

        En la del PROVEEDOR no cambia nada: su plata sale del precio por litro que el
        usuario ESCRIBIÓ en el día, no de una tarifa derivada, y volver a "derivarlo"
        del precio del proveedor le reescribiría un precio que alguien puso a mano.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado != ESTADO_BORRADOR:
            raise BusinessError(
                f"Esta liquidación está en '{liquidacion.estado}': solo se puede "
                "recalcular mientras sea un borrador"
            )
        antes = serialize_entity(liquidacion)
        total_antes = Decimal(liquidacion.valor_total or 0)
        transporte_antes = Decimal(liquidacion.valor_transporte or 0)
        # Cada tipo se rearma con su propia marca: la de proveedor por
        # `liquidacion_id` (un renglón por día del proveedor) y la de
        # transportador por `liquidacion_transporte_id` (un renglón por día con
        # toda la ruta sumada). Cruzarlas dejaría la liquidación en ceros.
        dias_con_flete_nuevo = 0
        if liquidacion.tipo == TIPO_PROVEEDOR:
            self._recalcular_desde_recepciones(liquidacion)
        else:
            dias_con_flete_nuevo = self._recalcular_transporte_desde_recepciones(
                liquidacion, rederivar_tarifas=rederivar_tarifas
            )
            if dias_con_flete_nuevo:
                self._poner_al_dia_el_flete_de_la_leche(liquidacion)
        self._aplicar_anticipos_pendientes(liquidacion)
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()
        # LA BITÁCORA CON EL ANTES Y EL DESPUÉS DE LA CIFRA GRANDE, y con cuántos días
        # cambiaron de flete. `antes`/`despues` traen la fila completa, pero el dueño no
        # lee dos volcados de treinta columnas para encontrar el peso que se movió:
        # esto pone la respuesta de "¿por qué el comprobante cambió de cifra?" en el
        # mismo renglón del libro. Los `float` son porque la auditoría guarda JSON y un
        # Decimal no se serializa (es el mismo formato que usa `serialize_entity`).
        despues = serialize_entity(liquidacion)
        despues["recalculo"] = {
            "valor_total_antes": float(total_antes),
            "valor_total_despues": float(Decimal(liquidacion.valor_total or 0)),
            "valor_transporte_antes": float(transporte_antes),
            "valor_transporte_despues": float(Decimal(liquidacion.valor_transporte or 0)),
            "dias_con_flete_recalculado": dias_con_flete_nuevo,
        }
        self._audit("editar", liquidacion.id, antes, despues)
        return liquidacion

    def _poner_al_dia_el_flete_de_la_leche(self, liquidacion: Liquidacion) -> None:
        """Deja al día la columna informativa de flete de las liquidaciones de LECHE.

        Recalcular el flete le mueve la FOTO del flete a los días del comprobante, y
        cada uno de esos días está además en la liquidación del PROVEEDOR de esa leche,
        que guarda en `valor_transporte` la suma del flete de sus días. Sin esto, esa
        liquidación —incluso PAGADA— quedaba diciendo un flete que ya no era la suma de
        sus propias recepciones ($6.314,27 de diferencia en el caso medido) y la cifra se
        corregía sola, sin que nadie tocara nada, la próxima vez que se recuadrara: una
        cifra que se mueve sin causa visible es lo que hace desconfiar de todo el
        sistema.

        ES EL MISMO DEFECTO que ya se había cerrado por el lado de Recepción diaria y que
        este camino nuevo reabría; por eso se reúsa el mismo remedio
        (`refrescar_transporte_informativo`) en vez de escribir otro: dos formas de poner
        al día la misma cifra terminan contradiciéndose.

        SE HACE TAMBIÉN SOBRE LAS PAGADAS, y es correcto: esa columna es INFORMATIVA —no
        entra en el VALOR TOTAL (bruto + bonificaciones - descuentos), ni en el saldo, ni
        en el PDF—, así que ponerla al día no desdice ningún papel firmado. Lo único que
        cambia es lo que la pantalla muestra como "cuánto costó recoger esa leche", que
        es justo lo que se movió. Queda en la bitácora con su motivo.
        """
        motivo = (
            "se recalculó el flete del transportador y cambió la foto del flete de uno "
            "o más de estos días; es una cifra informativa y no mueve el valor total, "
            "el saldo ni el PDF de esta liquidación"
        )
        # Ordenado por el texto del id para que dos corridas del mismo recálculo dejen
        # las entradas de bitácora en el mismo orden.
        ids = sorted(
            {
                r.liquidacion_id
                for r in self._recepciones_transporte_de(liquidacion)
                if r.liquidacion_id is not None
            },
            key=str,
        )
        for liq_id in ids:
            self.refrescar_transporte_informativo(liq_id, motivo=motivo)

    def refrescar_transporte_informativo(
        self, entity_id: uuid.UUID, motivo: str | None = None
    ) -> None:
        """Pone al día la columna de flete de una liquidación de PROVEEDOR ya pagada.

        `liquidaciones.valor_transporte` en la del proveedor es INFORMATIVA: cuánto
        flete costó recoger la leche de esos días. NO entra en el VALOR TOTAL (que es
        bruto + bonificaciones - descuentos) ni sale en el PDF, pero sí en la pantalla
        y en la API.

        Hace falta porque un día con la leche PAGADA todavía se puede corregir por el
        lado del flete —cambiarle la ruta, por ejemplo: la ruta solo la traba el
        flete—, y eso mueve la foto del flete de ese día. Sin esto, el comprobante
        pagado quedaba diciendo un flete que ya no era la suma de sus propias
        recepciones, y se corregía solo —sin que nadie tocara nada— la próxima vez que
        se recuadrara: una cifra que se mueve sin causa visible.

        NO TOCA NI UN PESO DE LO QUE SE PAGÓ: ni los litros, ni el bruto, ni el total,
        ni el saldo, ni los renglones, ni el estado. Solo esa columna, y solo en las de
        proveedor. Queda en la bitácora porque es una liquidación pagada: cualquier
        escritura sobre una de esas se anota.

        `motivo` es POR QUÉ se movió la cifra, y viaja a la bitácora: el cambio puede
        venir de haberle corregido la ruta a un día en Recepción diaria o de haber
        recalculado el comprobante del transportador, y el libro tiene que poder
        distinguirlos. Sin él se pone el motivo genérico, que es el de siempre.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.tipo != TIPO_PROVEEDOR:
            return
        nuevo = sum(
            (Decimal(r.valor_transporte or 0) for r in self._recepciones_de(liquidacion)), CERO
        )
        if nuevo == Decimal(liquidacion.valor_transporte or 0):
            return
        antes = Decimal(liquidacion.valor_transporte or 0)
        liquidacion.valor_transporte = nuevo
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit(
            "editar",
            liquidacion.id,
            {"valor_transporte": float(antes)},
            {
                "valor_transporte": float(nuevo),
                "motivo": motivo
                or "cambió el flete de un día; es una cifra informativa y no "
                "mueve el valor total ni el saldo de esta liquidación pagada",
            },
        )

    def recuadrar(
        self,
        entity_id: uuid.UUID,
        motivo: str = "cambiaron las recepciones de la liquidación",
    ) -> bool:
        """Vuelve a cuadrar una liquidación cuyas cifras de origen acaban de cambiar.

        Es la contraparte de haber aflojado el candado de Recepción diaria: ahora
        un día se puede corregir mientras su liquidación no esté PAGADA, y esta es
        la que evita que quede un descuadre silencioso. Después se le aflojó el
        mismo candado a los ANTICIPOS, que entran por aquí igual: el `motivo` es
        lo único que cambia, y viaja a la bitácora para que mañana se pueda leer
        POR QUÉ una aprobada amaneció en borrador —si dijera siempre "cambiaron
        las recepciones", el libro estaría mintiendo la mitad de las veces—.

        - En borrador: se recalcula y ya.
        - APROBADA: se DEVUELVE A BORRADOR y se recalcula. Aprobar es un visto
          bueno sobre unas cifras; si las cifras cambian, el visto bueno ya no
          vale y hay que volver a darlo. El cambio de estado queda en auditoría
          aparte del recálculo, para que en el libro se lea qué pasó y por qué.
        - Pagada, o CON CUALQUIER PAGO REGISTRADO (parcial): no llega aquí (los
          guardias de Recepción diaria y de Anticipos rebotan antes), pero si
          llegara rebota igual: esa plata ya salió de la caja contra estas cifras.
        - Anulada: no se toca. Al anular se le sueltan las recepciones Y los
          anticipos, así que ninguno debería seguir apuntándole.

        LO QUE ESTE CAMINO NO HACE, y es la distinción que faltaba: NO RE-PRECIFICA.
        Vuelve a sumar los días que le quedan y a repartir los centavos entre sus fotos
        —eso sí, siempre, o se reabre el centavo que ya se cerró—, pero no vuelve a
        derivar la cifra del flete con la tarifa de hoy. Re-precificar es lo que hace el
        botón "Recalcular", que el dueño oprime a propósito.

        La razón, con las cifras del caso que lo destapó: dos días del 02/06 en Nápoles
        (126,71 L) con el comprobante APROBADO en $30.760,12 a $242,76. Alguien sube la
        tarifa a $300 —legítimo, para la quincena siguiente— y alguien más le escribe una
        observación a uno de los días. `observaciones` no traba nada y no le mueve la
        cuenta a nadie; pero como el recuadre re-derivaba, esa nota dejaba el comprobante
        en $38.013,00 y sin visto bueno: $7.252,88 de cambio por un campo que no tiene
        nada que ver con la plata. Lo mismo pasaba entrando por un anticipo.

        Devuelve True si hubo que devolverla a borrador, para poder avisarle al
        usuario que la tiene que revisar y aprobar otra vez.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado == ESTADO_PAGADA:
            raise BusinessError(
                "Esta liquidación ya está pagada: sus días no se pueden modificar"
            )
        # Con un abono hecho ya no vale devolverla a borrador y recalcularla: el
        # pago se registró contra un total que dejaría de existir.
        if liquidacion.tiene_pagos:
            raise BusinessError(
                "Esta liquidación ya tiene pagos registrados: sus días no se pueden modificar"
            )
        if liquidacion.estado == ESTADO_ANULADA:
            return False

        devuelta_a_borrador = liquidacion.estado == ESTADO_APROBADA
        if devuelta_a_borrador:
            liquidacion.estado = ESTADO_BORRADOR
            liquidacion.updated_by = self.ctx.user_id
            self.db.flush()
            self._audit(
                "editar",
                liquidacion.id,
                {"estado": ESTADO_APROBADA},
                {"estado": ESTADO_BORRADOR, "motivo": motivo},
            )
        # Se reúsa el recálculo de siempre —misma suma, mismo reparto de centavos, así
        # que generar, recalcular y recuadrar siguen dando el mismo papel sobre los
        # mismos datos— pero SIN re-derivar las tarifas: ver el párrafo de arriba. Exige
        # borrador, por eso el cambio de estado va primero.
        self.recalcular(entity_id, rederivar_tarifas=False)
        return devuelta_a_borrador

    # ------------------------------------------------------------ transiciones
    def _transicionar(self, entity_id: uuid.UUID, desde: tuple[str, ...], hacia: str) -> Liquidacion:
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado not in desde:
            raise BusinessError(
                f"No se puede pasar de '{liquidacion.estado}' a '{hacia}'"
            )
        antes = liquidacion.estado
        liquidacion.estado = hacia
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", liquidacion.id, {"estado": antes}, {"estado": hacia})
        return liquidacion

    def aprobar(self, entity_id: uuid.UUID) -> Liquidacion:
        """Aprobar es el último momento en que se puede corregir: enseguida se paga.

        Por eso antes de cambiar el estado se barren los anticipos pendientes del
        tercero. Si no, un anticipo registrado después de generar la liquidación
        se quedaría por fuera y se le pagaría al proveedor plata que ya se le
        había adelantado. Solo aplica sobre el borrador; el anticipo que ya se
        descontó en otra liquidación no vuelve a entrar.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado == ESTADO_BORRADOR:
            self._aplicar_anticipos_pendientes(liquidacion)
        return self._transicionar(entity_id, (ESTADO_BORRADOR,), ESTADO_APROBADA)

    # ------------------------------------------------------- pagos parciales
    def _exigir_pagable(self, liquidacion: Liquidacion) -> None:
        """Solo se le abona a una liquidación EN FIRME y que todavía deba algo.

        En borrador no, y esto no es formalismo: un borrador se recalcula solo
        cuando cambian las recepciones o entra un anticipo, así que el total
        contra el que se abonó puede cambiar debajo del pago y dejarlo
        descuadrado. Aprobar es justamente el momento en que las cifras quedan
        en firme.
        """
        if liquidacion.estado not in (ESTADO_APROBADA, ESTADO_PARCIAL):
            raise BusinessError(
                f"Esta liquidación está en '{liquidacion.estado}': solo se le puede "
                "pagar a una liquidación aprobada"
            )
        if Decimal(liquidacion.saldo) <= CERO:
            raise BusinessError("Esta liquidación no tiene saldo pendiente por pagar")

    def registrar_pago(self, entity_id: uuid.UUID, payload: Any) -> Liquidacion:
        """Registra un pago parcial (abono) contra una liquidación aprobada.

        Lo pidió el dueño: a un proveedor se le puede pagar una parte y quedarle
        debiendo el resto. Mientras deba algo la liquidación queda en PARCIAL, y
        pasa a PAGADA sola cuando el saldo llega a cero.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        # Con candado: dos pagos simultáneos sobre la misma liquidación se
        # pisarían y uno de los dos se perdería (ver `_bloquear`).
        liquidacion = _bloquear(self.db, liquidacion)
        self._exigir_pagable(liquidacion)

        valor = Decimal(payload.valor)
        pendiente = Decimal(liquidacion.saldo)
        if valor > pendiente:
            # pesos() y no "{:,.0f}": el formato con coma es gringo y "$1,200,000"
            # en Colombia se lee como un peso con veinte centavos.
            raise BusinessError(
                f"El pago ({pesos(valor)}) supera el saldo pendiente ({pesos(pendiente)})"
            )

        self.db.add(
            PagoLiquidacion(
                liquidacion_id=liquidacion.id,
                fecha=payload.fecha,
                valor=valor,
                observaciones=payload.observaciones,
                created_by=self.ctx.user_id,
            )
        )
        liquidacion.pagado = Decimal(liquidacion.pagado) + valor
        _refrescar_saldo(liquidacion)
        liquidacion.estado = _estado_pago(liquidacion.neto_a_pagar, liquidacion.pagado)
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()
        # Se refresca la lista de pagos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `pagado` nuevo pero SIN el pago en la
        # lista. La pantalla pinta las dos cosas juntas y se contradicen a la
        # vista.
        self.db.refresh(liquidacion, ["pagos"])
        self._audit(
            "editar", liquidacion.id, None,
            {"pago": float(valor), "estado": liquidacion.estado, "saldo": float(liquidacion.saldo)},
        )
        return liquidacion

    def eliminar_pago(self, entity_id: uuid.UUID, pago_id: uuid.UUID) -> Liquidacion:
        """Elimina un pago mal registrado: devuelve el saldo y recalcula el estado."""
        liquidacion = self.repo.get_or_fail(entity_id)
        liquidacion = _bloquear(self.db, liquidacion)
        pago = next((p for p in liquidacion.pagos if p.id == pago_id), None)
        if pago is None:
            raise NotFoundError("Pago no encontrado")
        valor = Decimal(pago.valor)
        liquidacion.pagado = max(Decimal(liquidacion.pagado) - valor, CERO)
        _refrescar_saldo(liquidacion)
        liquidacion.estado = _estado_pago(liquidacion.neto_a_pagar, liquidacion.pagado)
        liquidacion.updated_by = self.ctx.user_id
        self.db.delete(pago)
        self.db.flush()
        # Mismo motivo que al registrar: sin refrescar, la respuesta traería el
        # pago borrado todavía dentro de la lista.
        self.db.refresh(liquidacion, ["pagos"])
        self._audit(
            "editar", liquidacion.id, None,
            {
                "pago_eliminado": float(valor),
                "estado": liquidacion.estado,
                "saldo": float(liquidacion.saldo),
            },
        )
        return liquidacion

    def pagar(self, entity_id: uuid.UUID) -> Liquidacion:
        """El botón "Pagar" de siempre: saldar la liquidación de una vez.

        Antes solo cambiaba el estado a 'pagada' (no movía caja ni bancos, y eso
        se conserva). Ahora hace lo mismo pero DEJANDO CONSTANCIA: registra un
        pago por todo el saldo pendiente y el estado sale de `_estado_pago`, así
        que una liquidación pagada de un solo golpe y otra pagada en tres abonos
        quedan contadas igual y las dos aparecen en el historial.

        El caso raro se respeta: si los anticipos se comieron todo y no queda
        saldo, no hay pago que registrar y solo se marca pagada, como antes.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado not in (ESTADO_APROBADA, ESTADO_PARCIAL):
            raise BusinessError(
                f"No se puede pasar de '{liquidacion.estado}' a '{ESTADO_PAGADA}'"
            )
        pendiente = Decimal(liquidacion.saldo)
        if pendiente <= CERO:
            return self._transicionar(
                entity_id, (ESTADO_APROBADA, ESTADO_PARCIAL), ESTADO_PAGADA
            )
        return self.registrar_pago(
            entity_id,
            PagoLiquidacionCreate(
                fecha=date.today(),
                valor=pendiente,
                observaciones="Pago total de la liquidación",
            ),
        )

    def anular(self, entity_id: uuid.UUID) -> Liquidacion:
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado == ESTADO_PAGADA:
            raise BusinessError("No se puede anular una liquidación ya pagada")
        # Anular suelta las recepciones y los anticipos para volver a liquidar el
        # período. Con un abono hecho eso dejaría un pago colgando de un
        # documento que ya no representa nada: primero se borra el pago.
        if liquidacion.tiene_pagos:
            raise BusinessError(
                "No se puede anular una liquidación con pagos registrados: "
                "elimine primero los pagos"
            )
        # Liberar recepciones y anticipos para poder re-liquidar
        campo = (
            RecepcionLeche.liquidacion_id
            if liquidacion.tipo == TIPO_PROVEEDOR
            else RecepcionLeche.liquidacion_transporte_id
        )
        recepciones = self.db.scalars(
            RecepcionRepository(self.db, self.ctx.empresa_id).base_query().where(campo == liquidacion.id)
        ).all()
        for r in recepciones:
            if liquidacion.tipo == TIPO_PROVEEDOR:
                r.liquidacion_id = None
            else:
                r.liquidacion_transporte_id = None
        anticipos = self.db.scalars(
            AnticipoRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(Anticipo.liquidacion_id == liquidacion.id)
        ).all()
        for a in anticipos:
            a.liquidacion_id = None
        return self._transicionar(entity_id, (ESTADO_BORRADOR, ESTADO_APROBADA), ESTADO_ANULADA)

    # ------------------------------------------------------------------ listar
    def listar_filtrado(
        self,
        params: PageParams,
        *,
        tipo: str | None = None,
        estado: str | None = None,
        proveedor_id: uuid.UUID | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> tuple[list[Liquidacion], int]:
        extra = []
        if desde:
            extra.append(Liquidacion.periodo_fin >= desde)
        if hasta:
            extra.append(Liquidacion.periodo_inicio <= hasta)
        return self.repo.list_paginated(
            params,
            estado=estado,
            filters={"tipo": tipo, "proveedor_id": proveedor_id},
            extra_criteria=extra,
        )

    # ----------------------------------------------------------------- export
    def _nombre_tercero(self, liquidacion: Liquidacion) -> str:
        if liquidacion.tipo == TIPO_PROVEEDOR and liquidacion.proveedor:
            return liquidacion.proveedor.nombre
        if liquidacion.transportador:
            return liquidacion.transportador.nombre
        return "-"

    def generar_pdf(self, entity_id: uuid.UUID) -> tuple[bytes, str]:
        liquidacion = self.repo.get_or_fail(entity_id)
        empresa = EmpresaRepository(self.db).get(self.ctx.empresa_id)
        nombre_empresa = empresa.nombre if empresa else "Quesera"
        nit = empresa.nit if empresa else None
        ubicacion = (
            ", ".join(p for p in [empresa.ciudad, empresa.departamento] if p) or None
            if empresa
            else None
        )
        tercero = self._nombre_tercero(liquidacion)
        es_proveedor = liquidacion.tipo == TIPO_PROVEEDOR
        tercero_detalle = (
            getattr(liquidacion.proveedor, "vereda", None)
            if es_proveedor and liquidacion.proveedor
            else None
        )

        # Toda la plata y los litros salen por los formateadores colombianos
        # (pesos / litros): el productor lee $18.525.000, no $18,525,000.
        detalle_headers, detalle_rows, detalle_anchos, detalle_envuelven = _tabla_del_detalle(
            liquidacion.detalles, es_proveedor
        )

        # El renglón "Pagado" solo aparece cuando de verdad se abonó algo. Sin él
        # el comprobante de una liquidación a medio pagar mostraría un SALDO A
        # PAGAR más chico que VALOR TOTAL menos anticipos, sin explicar por qué:
        # el dueño cuadra estas cifras a mano y esa diferencia muda es justo lo
        # que le hace perder la confianza en el papel.
        pagado_rows = (
            [("Pagado", f"- {pesos(liquidacion.pagado)}", False)]
            if Decimal(liquidacion.pagado or 0) > CERO
            else []
        )

        # EL ÚLTIMO RENGLÓN CAMBIA DE RÓTULO CUANDO EL SALDO QUEDA POR DEBAJO DE CERO,
        # y no es cosmético: con "SALDO A PAGAR  -$120.000,00" el papel dice, en el
        # renglón destacado, que hay que pagar una cifra negativa. Lo que de verdad
        # pasó es que los anticipos que ya se le entregaron suman más que la quincena,
        # o sea que el tercero le quedó debiendo AL NEGOCIO. Se dice con esas palabras
        # y con la cifra en POSITIVO.
        #
        # Es la misma solución que ya usa el estado de cuenta de reventa cuando el
        # cliente abonó de más ("SALDO A FAVOR DEL CLIENTE", ver `app/utils/export.py`):
        # el rótulo dice de quién es la plata y el valor va sin signo, porque un menos
        # pegado a un total destacado es justo lo que se lee mal.
        #
        # No se agrega ni se mueve ningún renglón —el resumen queda con los mismos, en
        # el mismo orden—: solo este cambia de rótulo. Y el rótulo va corto a propósito;
        # la celda es de 6 cm con texto plano (no se envuelve) y meterle el nombre del
        # tercero lo desbordaría con un "María Fernanda Gutiérrez". El nombre ya está
        # arriba, en el bloque "Proveedor / Transportador" del encabezado.
        debe = Decimal(liquidacion.le_queda_debiendo or 0)
        saldo_row = (
            ("LE QUEDA DEBIENDO", pesos(debe), True)
            if debe > CERO
            else ("SALDO A PAGAR", pesos(liquidacion.saldo), True)
        )

        if es_proveedor:
            resumen_rows = [
                ("Total litros", litros(liquidacion.total_litros), False),
                ("Precio promedio", pesos(liquidacion.precio_promedio), False),
                ("Valor bruto", pesos(liquidacion.valor_bruto), False),
                ("Bonificaciones", f"+ {pesos(liquidacion.bonificaciones)}", False),
                ("Descuentos", f"- {pesos(liquidacion.descuentos)}", False),
                ("Anticipos aplicados", f"- {pesos(liquidacion.anticipos)}", False),
                *pagado_rows,
                ("VALOR TOTAL", pesos(liquidacion.valor_total), True),
                saldo_row,
            ]
        else:
            # El renglón de anticipos también va en la del transportador: sin él,
            # el comprobante mostraba VALOR TOTAL y SALDO A PAGAR distintos sin
            # explicar la diferencia, y el dueño cuadra estas cifras a mano.
            resumen_rows = [
                ("Total litros", litros(liquidacion.total_litros), False),
                ("Valor transporte", pesos(liquidacion.valor_transporte), False),
                ("Anticipos aplicados", f"- {pesos(liquidacion.anticipos)}", False),
                *pagado_rows,
                ("VALOR TOTAL", pesos(liquidacion.valor_total), True),
                saldo_row,
            ]

        anticipos = self._anticipos_de(liquidacion)
        anticipos_rows: list[list[Any]] = [
            [a.fecha.strftime("%d/%m/%Y"), pesos(a.valor), a.observaciones or "—"]
            for a in anticipos
        ]

        periodo = (
            f"{liquidacion.periodo_inicio.strftime('%d/%m/%Y')} al "
            f"{liquidacion.periodo_fin.strftime('%d/%m/%Y')}"
        )
        pdf = build_liquidacion_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            folio=str(liquidacion.id)[:8].upper(),
            estado=liquidacion.estado,
            emitido=datetime.now().strftime("%d/%m/%Y %H:%M"),
            tercero_label="Proveedor" if es_proveedor else "Transportador",
            tercero_nombre=tercero,
            tercero_detalle=tercero_detalle,
            periodo=periodo,
            detalle_headers=detalle_headers,
            detalle_rows=detalle_rows,
            detalle_col_widths=detalle_anchos,
            detalle_wrap_cols=detalle_envuelven,
            resumen_rows=resumen_rows,
            anticipos_rows=anticipos_rows,
            observaciones=liquidacion.observaciones,
        )
        filename = f"liquidacion_{tercero}_{liquidacion.periodo_inicio.isoformat()}.pdf".replace(" ", "_")
        return pdf, filename

    def previsualizar_pdf(
        self, inicio: date, fin: date, tipo: str, tercero_id: uuid.UUID
    ) -> tuple[bytes, str]:
        """PDF preliminar (marcado como no oficial) del avance de un tercero."""
        previews = self.previsualizar(inicio, fin, tipo, tercero_id)
        if not previews:
            raise BusinessError(
                "No hay recepciones sin liquidar para ese tercero en el período"
            )
        pre = previews[0]
        empresa = EmpresaRepository(self.db).get(self.ctx.empresa_id)
        nombre_empresa = empresa.nombre if empresa else "Quesera"
        nit = empresa.nit if empresa else None
        ubicacion = (
            (", ".join(p for p in [empresa.ciudad, empresa.departamento] if p) or None)
            if empresa
            else None
        )
        es_proveedor = pre.tipo == TIPO_PROVEEDOR
        # Mismo formato colombiano y MISMA TABLA que el comprobante oficial (con la
        # columna Ruta en la del transportador): el tercero recibe los dos
        # documentos y no pueden leerse distinto.
        detalle_headers, detalle_rows, detalle_anchos, detalle_envuelven = _tabla_del_detalle(
            pre.detalles, es_proveedor
        )
        # OJO: acá el rótulo NO cambia cuando el saldo queda negativo, al contrario del
        # comprobante oficial (ver `generar_pdf`). No es un olvido: la PANTALLA de la
        # pre-liquidación tampoco lo dice todavía, y este papel y esa pantalla tienen que
        # leerse igual —el dueño manda el uno mirando la otra—. Cuando se le ponga a la
        # pantalla del avance el mismo "le queda debiendo", este renglón se cambia con
        # ella, no antes.
        if es_proveedor:
            resumen_rows = [
                ("Total litros", litros(pre.total_litros), False),
                ("Precio promedio", pesos(pre.precio_promedio), False),
                ("Valor bruto", pesos(pre.valor_bruto), False),
                ("Bonificaciones", f"+ {pesos(pre.bonificaciones)}", False),
                ("Descuentos", f"- {pesos(pre.descuentos)}", False),
                ("Anticipos aplicados", f"- {pesos(pre.anticipos)}", False),
                ("VALOR TOTAL", pesos(pre.valor_total), True),
                ("SALDO ESTIMADO", pesos(pre.saldo), True),
            ]
        else:
            resumen_rows = [
                ("Total litros", litros(pre.total_litros), False),
                ("Valor transporte", pesos(pre.valor_transporte), False),
                ("Anticipos aplicados", f"- {pesos(pre.anticipos)}", False),
                ("VALOR TOTAL", pesos(pre.valor_total), True),
                ("SALDO ESTIMADO", pesos(pre.saldo), True),
            ]
        anticipos_rows = [
            [a.fecha.strftime("%d/%m/%Y"), pesos(a.valor), a.observaciones or "—"]
            for a in pre.anticipos_detalle
        ]
        periodo = f"{inicio.strftime('%d/%m/%Y')} al {fin.strftime('%d/%m/%Y')}"
        pdf = build_liquidacion_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            folio="PRELIMINAR",
            estado="preliminar",
            emitido=datetime.now().strftime("%d/%m/%Y %H:%M"),
            tercero_label="Proveedor" if es_proveedor else "Transportador",
            tercero_nombre=pre.tercero_nombre,
            tercero_detalle=pre.tercero_detalle,
            periodo=periodo,
            detalle_headers=detalle_headers,
            detalle_rows=detalle_rows,
            detalle_col_widths=detalle_anchos,
            detalle_wrap_cols=detalle_envuelven,
            resumen_rows=resumen_rows,
            anticipos_rows=anticipos_rows,
            observaciones="PRE-LIQUIDACIÓN — documento informativo del avance; no constituye pago.",
        )
        filename = (
            f"preliquidacion_{pre.tercero_nombre}_{inicio.isoformat()}.pdf".replace(" ", "_")
        )
        return pdf, filename


class AnticipoService(BaseService[Anticipo]):
    repository_cls = AnticipoRepository
    modulo = "liquidaciones"

    _CAMPO_POR_TIPO = {
        "proveedor": "proveedor_id",
        "transportador": "transportador_id",
        "empleado": "empleado_id",
    }

    def crear(self, payload: Any) -> Anticipo:
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        tipo = data.get("tipo") or TIPO_PROVEEDOR
        campo = self._CAMPO_POR_TIPO.get(tipo)
        if campo is None:
            raise BusinessError(f"Tipo de anticipo inválido: {tipo}")
        if not data.get(campo):
            raise BusinessError(f"Debe indicar el {tipo} del anticipo")
        data["tipo"] = tipo
        # Deja solo el id del beneficiario que corresponde al tipo
        for otro in self._CAMPO_POR_TIPO.values():
            if otro != campo:
                data[otro] = None
        anticipo = super().crear(data)
        # Nace suelto (sin liquidación), pero la respuesta trae los mismos campos
        # que el listado: si no se marcaran, la pantalla los vería en nulo y no
        # sabría si el recién creado se puede corregir.
        self._marcar_liquidacion([anticipo])
        return anticipo

    # ------------------------------------------- el candado: "ya se pagó", no
    #                                              "ya se liquidó"
    def _liquidacion_de(self, anticipo: Anticipo) -> Liquidacion | None:
        """La liquidación en la que este anticipo quedó descontado, si hay alguna.

        Va por el repositorio para no saltarse el filtro por empresa ni el de
        borrados: de esto depende si se deja o no tocar plata de un tenant.
        """
        if anticipo.liquidacion_id is None:
            return None
        stmt = (
            LiquidacionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(Liquidacion.id == anticipo.liquidacion_id)
        )
        return self.db.scalars(stmt).first()

    def _exigir_no_pagado(self, anticipo: Anticipo, verbo: str) -> Liquidacion | None:
        """El candado de verdad: se traba en cuanto SALIÓ PLATA contra el anticipo.

        Antes se trababa apenas el anticipo tuviera liquidación, sin mirar el
        estado, así que quedaba congelado desde que se GENERABA la quincena
        —aunque todavía no se le hubiera pagado un peso a nadie—. Es el mismo
        problema que ya se resolvió en Recepción diaria y la regla es la misma,
        copiada de `RecepcionService._exigir_no_pagada` a propósito: dos criterios
        distintos para la misma pregunta terminan contradiciéndose.

        "Ya salió plata" es TENER ALGÚN PAGO, no estar en 'pagada': si al
        proveedor se le abonó la mitad y después le cambian el anticipo, ese abono
        queda contra un neto que ya no existe.

        Devuelve la liquidación tocada, para recuadrarla después de escribir.
        """
        # NÓMINA: camino aparte y sin tocar. Un pago de empleado no tiene estados
        # (ni borrador, ni aprobada) ni pagos parciales: existe = ya se le pagó al
        # empleado con el anticipo ya descontado. No hay nada que "estar sin
        # pagar", así que ahí el candado se queda como estaba.
        if anticipo.pago_empleado_id is not None:
            raise BusinessError(
                f"No se puede {verbo} este anticipo: ya se le descontó al empleado "
                "en un pago de nómina"
            )

        if anticipo.liquidacion_id is None:
            return None

        liquidacion = self._liquidacion_de(anticipo)
        if liquidacion is None:
            # El anticipo apunta a una liquidación que esta empresa no ve. No
            # debería pasar; ante la duda se traba, que es el lado seguro cuando
            # de por medio hay plata.
            raise BusinessError(
                f"No se puede {verbo} este anticipo: está aplicado a una "
                "liquidación que no se puede consultar"
            )

        # Con candado antes de decidir: se va a mirar `pagado` y enseguida a
        # reescribir el total de la liquidación. Sin el FOR UPDATE, un pago que
        # entre en ese instante se lee como "todavía no hay pagos" y el recálculo
        # le pasa por encima. Ver `_bloquear`.
        liquidacion = _bloquear(self.db, liquidacion)

        # Dos mensajes porque son dos situaciones distintas para el usuario: de la
        # pagada no hay nada que hacer por dentro; del abono sí, se puede borrar el
        # pago, corregir el anticipo y volver a abonar.
        if liquidacion.estado == ESTADO_PAGADA:
            raise BusinessError(
                f"No se puede {verbo} este anticipo: la liquidación en la que se "
                "descontó ya se pagó. Si la cifra está mala, registre el ajuste en "
                "la quincena siguiente"
            )
        if liquidacion.tiene_pagos:
            raise BusinessError(
                f"No se puede {verbo} este anticipo: la liquidación en la que se "
                "descontó ya tiene un pago registrado. Elimine primero ese pago si "
                "de verdad hay que corregirlo, o registre el ajuste en la quincena "
                "siguiente"
            )
        return liquidacion

    def _recuadrar(self, liquidacion: Liquidacion | None, motivo: str) -> None:
        """Vuelve a cuadrar la liquidación a la que se le movió un anticipo.

        Sin esto queda el descuadre silencioso: la liquidación seguiría diciendo
        "Anticipos aplicados $500.000" cuando el anticipo se corrigió a $300.000 o
        se borró, y su neto a pagar —la cifra grande del comprobante— saldría mal
        por la diferencia.
        """
        if liquidacion is None:
            return
        LiquidacionService(self.db, self.ctx).recuadrar(liquidacion.id, motivo)

    # ------------------------------------------------------- estado para la UI
    def _marcar_liquidacion(self, anticipos: list[Anticipo]) -> None:
        """Le cuelga a cada anticipo el estado de su liquidación y si está trabado.

        No son columnas: se resuelven de un solo golpe para toda la lista (una
        consulta, no una por fila) y se exponen en `AnticipoRead` para que la
        pantalla sepa cuándo poner el candado. Con el candado viejo bastaba
        `aplicado`; ahora "aplicado" y "trabado" son cosas distintas, y si la
        pantalla siguiera mirando `aplicado` seguiría escondiendo los botones de
        anticipos que sí se pueden corregir.
        """
        ids = {a.liquidacion_id for a in anticipos if a.liquidacion_id is not None}
        estados: dict[uuid.UUID, tuple[str, bool]] = {}
        if ids:
            stmt = (
                LiquidacionRepository(self.db, self.ctx.empresa_id)
                .base_query()
                .where(Liquidacion.id.in_(ids))
            )
            estados = {
                liq.id: (liq.estado, liq.tiene_pagos or liq.estado == ESTADO_PAGADA)
                for liq in self.db.scalars(stmt).all()
            }
        for anticipo in anticipos:
            # El default cubre el anticipo suelto (None, False) y el que apunta a
            # una liquidación que no se ve, que se muestra trabado igual que lo
            # rebota el guardia.
            estado, con_pago = estados.get(
                anticipo.liquidacion_id, (None, anticipo.liquidacion_id is not None)
            )
            anticipo.liquidacion_estado = estado
            anticipo.bloqueado = con_pago or anticipo.pago_empleado_id is not None

    def obtener(self, entity_id: uuid.UUID) -> Anticipo:
        anticipo = super().obtener(entity_id)
        self._marcar_liquidacion([anticipo])
        return anticipo

    def listar(self, params: PageParams, **kwargs: Any) -> tuple[list[Anticipo], int]:
        items, total = super().listar(params, **kwargs)
        self._marcar_liquidacion(items)
        return items, total

    # ------------------------------------------------------------- correcciones
    def validar_actualizar(self, obj: Anticipo, data: dict[str, Any]) -> None:
        self._exigir_no_pagado(obj, "modificar")

    def validar_eliminar(self, obj: Anticipo) -> None:
        self._exigir_no_pagado(obj, "eliminar")

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Anticipo:
        """Corrige un anticipo y deja cuadrada la liquidación que lo tenía.

        La liquidación se apunta ANTES de escribir, porque después de guardar hay
        que volver a cuadrarla con la cifra nueva.
        """
        actual = self.repo.get_or_fail(entity_id)
        liquidacion = self._exigir_no_pagado(actual, "modificar")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)

        if liquidacion is not None:
            # Si al anticipo le corrigen la fecha y se pasa del fin del período,
            # ese adelanto ya no pertenece a esta quincena: se suelta para que lo
            # recoja la que le toca. Es el mismo criterio que con las recepciones
            # —si se quedara, el comprobante de junio descontaría un anticipo de
            # julio— y encaja con `pendientes_de`, que solo recoge los que tienen
            # fecha hasta el fin del período.
            nueva_fecha = data.get("fecha") or actual.fecha
            if nueva_fecha > liquidacion.periodo_fin:
                data["liquidacion_id"] = None

        anticipo = super().actualizar(entity_id, data)
        # Se baja el cambio antes de recuadrar: la sesión no hace autoflush y el
        # recálculo vuelve a leer los anticipos desde la base.
        self.db.flush()
        self._recuadrar(liquidacion, "se corrigió un anticipo aplicado a la liquidación")
        self._marcar_liquidacion([anticipo])
        return anticipo

    def eliminar(self, entity_id: uuid.UUID) -> None:
        """Borra el anticipo y deja cuadrada la liquidación que lo tenía.

        El borrado es lógico y `_anticipos_de` filtra por `deleted_at IS NULL`, así
        que al recuadrar la liquidación el anticipo borrado simplemente deja de
        sumar y el neto a pagar sube en ese valor, que es lo correcto: si el
        adelanto nunca existió, no hay nada que descontarle al productor.
        """
        anticipo = self.repo.get_or_fail(entity_id)
        liquidacion = self._exigir_no_pagado(anticipo, "eliminar")
        super().eliminar(entity_id)
        self.db.flush()
        self._recuadrar(liquidacion, "se eliminó un anticipo aplicado a la liquidación")
