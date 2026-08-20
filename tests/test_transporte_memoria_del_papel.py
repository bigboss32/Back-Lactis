"""LA MEMORIA DEL PAPEL: un comprobante emitido no puede perder CÓMO se emitió.

LO QUE ESTE ARCHIVO CIERRA, en una línea: cuando un comprobante de flete se queda SIN
NINGÚN RENGLÓN de una ruta, el modo y la tarifa con que la cobró se DEDUCÍAN de los
renglones que le sobrevivieran, y ahí ya no sobrevivía ninguno. El siguiente recuadre lo
re-precificaba con la tarifa de HOY.

LAS DOS PUERTAS. Ninguna de las dos oprime Recalcular:

  · APAGAR EL DÍA Y VOLVER A PRENDERLO (`estado`: activo → inactivo → activo);
  · CORREGIRLE LA RUTA Y DEVOLVÉRSELA (`ruta_id`: A fábrica → Nápoles → A fábrica).

LAS CIFRAS, escritas a mano y no calculadas por el código que se está probando. Un solo
día en la ruta "A fábrica", 16/07/2026, Aurelio con 82,00 L:

    emitido POR DÍA COMPLETO ..................  $ 150.000,00
    emitido POR LITRO, 82,00 L × $242,76 .....  $  19.906,32
                                                ------------
    EL SALTO, en cualquiera de los dos sentidos  $ 130.093,68

O sea: el papel que decía «Día completo $150.000» amanecía diciendo «82 L × $242,76 =
$19.906,32» —$130.093,68 de menos para el conductor— y el que decía «82 L × $242,76»
amanecía en «Día completo $150.000» —$130.093,68 de más—. En los dos sentidos, y sin que
nadie hubiera pedido re-precificar nada. El comprobante se caía a borrador, lo cual MITIGA
pero NO CIERRA: se vuelve a aprobar y se paga la cifra nueva.

CÓMO SE CIERRA: el comprobante GUARDA cómo cobró cada ruta —el modo, y la cifra o la
tarifa según el modo— en el momento en que se emite, en la tabla `liquidacion_rutas` (ver
`LiquidacionRuta` en app/modules/liquidaciones/models.py). Escrito, ninguna puerta se lo
puede borrar: no hay nada que deducir.

EL CONTROL QUE TIENE QUE SEGUIR PASANDO, y es la otra mitad de la regla: RECALCULAR SÍ
re-precifica. Es el botón que el dueño oprime a propósito, y después de oprimirlo el
comprobante cobra lo de hoy —los $150.000 se vuelven $19.906,32, o al revés—. Si esta
prueba dejara de pasar, el arreglo se habría convertido en un candado que no se puede
abrir.

EL ESCENARIO, siempre el mismo. Alex Agudelo, quincena del 16 al 31 de julio de 2026:

    16/07  Aurelio   82,00 L   ruta "A fábrica"   ← el día de las dos puertas
    20/07  Henri     50,00 L   ruta "Napoles"     × $242,76 = $12.138,00

El día de Nápoles está ahí para que el comprobante NO quede vacío cuando se apague el otro:
lo que se prueba es un papel que se queda sin renglones DE UNA RUTA, no un papel sin nada.
Su renglón tampoco se puede mover ni un peso, y también se revisa.
"""
import uuid

import pytest
from sqlalchemy import select

from app.modules.liquidaciones.models import Liquidacion, LiquidacionRuta
from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    FIJO,
    LIQUIDACIONES,
    NAPOLES,
    TRANSPORTADORES,
    D,
    _escenario,
    _liquidar_flete,
    _recibir,
    centavos,
)
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, _ok, _put

CERO = D(0)

# El día de las dos puertas y el día que mantiene vivo el comprobante.
LITROS_DE_LA_PUERTA = D("82.00")
OTRO_DIA = "2026-07-20"
LITROS_DE_NAPOLES = D("50.00")

# LAS TRES CIFRAS DEL ENUNCIADO, escritas a mano.
POR_LITRO_EMITIDO = D("19906.32")     # 82,00 L × $242,76
EL_DIA_COMPLETO = D("150000")         # la tarifa fija de la ruta "A fábrica"
EL_SALTO = D("130093.68")             # lo que se le paga de menos o de más al conductor
EL_RENGLON_DE_NAPOLES = D("12138.00")  # 50,00 L × $242,76

RUTA_DE_LA_PUERTA = "A fabrica"


def test_las_cifras_del_encabezado_son_las_de_verdad():
    """Antes de cualquier cosa: que las cifras escritas a mano arriba sean las que dan.

    Si mañana alguien le cambia la tarifa al escenario compartido, esta prueba se cae
    primero y con un mensaje claro, en vez de que se caigan las otras diciendo cosas
    raras sobre puertas.
    """
    assert centavos(LITROS_DE_LA_PUERTA * NAPOLES) == POR_LITRO_EMITIDO
    assert centavos(LITROS_DE_NAPOLES * NAPOLES) == EL_RENGLON_DE_NAPOLES
    assert FIJO == EL_DIA_COMPLETO
    assert EL_DIA_COMPLETO - POR_LITRO_EMITIDO == EL_SALTO


# ---------------------------------------------------------------------------
# El escenario y las dos puertas
# ---------------------------------------------------------------------------
def _tarifas(client, h, esc, valor_fabrica, modo_fabrica):
    """Le pone a "A fábrica" la tarifa y el modo que se pidan, y deja Nápoles por litro."""
    _ok(client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(valor_fabrica),
             "modo_transporte": modo_fabrica},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": "litro"},
        ]},
        headers=h,
    ), "tarifa")


def _emitir(client, h, esc, modo_al_emitir):
    """El comprobante EMITIDO en el modo que se pida, y después la tarifa cambiada al otro.

    Devuelve (la recepción del día de la puerta, el id del comprobante, lo que ese día
    cobró al emitirse). Lo que queda montado es exactamente el cruce de modos: el papel
    dice una cosa y la tarifa de hoy dice la otra.
    """
    if modo_al_emitir == "dia_fijo":
        _tarifas(client, h, esc, FIJO, "dia_fijo")
        emitido = EL_DIA_COMPLETO
    else:
        _tarifas(client, h, esc, NAPOLES, "litro")
        emitido = POR_LITRO_EMITIDO
    de_la_puerta = _recibir(
        client, h, esc, EL_DIA, "Aurelio", str(LITROS_DE_LA_PUERTA)
    )
    _recibir(client, h, esc, OTRO_DIA, "Henri", str(LITROS_DE_NAPOLES))
    liq_id = _liquidar_flete(client, h)["id"]

    # Y AHORA LA TARIFA DE HOY PASA AL OTRO MODO. Es legítimo: el dueño renegoció con el
    # conductor y de aquí en adelante esa ruta se cobra distinto. Lo que NO puede pasar es
    # que le cambie la cifra al papel que ya está emitido.
    if modo_al_emitir == "dia_fijo":
        _tarifas(client, h, esc, NAPOLES, "litro")
        hoy = POR_LITRO_EMITIDO
    else:
        _tarifas(client, h, esc, FIJO, "dia_fijo")
        hoy = EL_DIA_COMPLETO
    return de_la_puerta, liq_id, emitido, hoy


def _papel(client, h, liq_id):
    """El comprobante como lo devuelve el API, que es lo que la pantalla y el PDF leen."""
    r = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _de_la_ruta(papel, nombre):
    """Lo que el papel cobra por esa ruta, sumando sus renglones."""
    return sum(
        (D(d["valor"]) for d in papel["detalles"] if d["ruta_nombre"] == nombre), CERO
    )


def _pinta(papel, paso):
    print(f"  {paso}: total ${papel['valor_transporte']}  ({papel['estado']})")
    for d in sorted(papel["detalles"], key=lambda x: (x["fecha"], x["ruta_nombre"] or "")):
        print(f"      {d['fecha']}  {(d['ruta_nombre'] or '-'):<10} [{d['modo_transporte']}]"
              f"  {d['litros']} L × ${d['precio_litro']} = ${d['valor']}")


def _apagar_y_prender(client, h, recepcion, esc):
    """PUERTA 1: se apaga el día y se vuelve a prender. Devuelve las dos respuestas."""
    return (
        _put(client, h, recepcion["id"], estado="inactivo"),
        _put(client, h, recepcion["id"], estado="activo"),
    )


def _quitar_y_devolver_la_ruta(client, h, recepcion, esc):
    """PUERTA 2: se le cambia la ruta al día y se le devuelve. Las dos respuestas."""
    return (
        _put(client, h, recepcion["id"], ruta_id=esc["napoles"]["id"]),
        _put(client, h, recepcion["id"], ruta_id=esc["fabrica"]["id"]),
    )


LAS_DOS_PUERTAS = [
    pytest.param(_apagar_y_prender, id="apagar_y_prender"),
    pytest.param(_quitar_y_devolver_la_ruta, id="quitar_y_devolver_la_ruta"),
]
LOS_DOS_CRUCES = [
    pytest.param("dia_fijo", id="emitido_dia_fijo__hoy_por_litro"),
    pytest.param("litro", id="emitido_por_litro__hoy_dia_fijo"),
]


# ---------------------------------------------------------------------------
# 1. LAS DOS PUERTAS, EN LOS DOS CRUCES, SOBRE UN BORRADOR
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("puerta", LAS_DOS_PUERTAS)
@pytest.mark.parametrize("modo_al_emitir", LOS_DOS_CRUCES)
def test_ninguna_puerta_le_mueve_un_peso_al_papel_emitido(
    client, base_datos, puerta, modo_al_emitir
):
    """LA PRUEBA CENTRAL: cruzar una puerta no le mueve un peso al comprobante.

    Emitido POR DÍA COMPLETO en $150.000,00 y con la ruta pasada hoy a $242,76 el litro,
    apagar el día y prenderlo lo dejaba en $19.906,32: $130.093,68 de menos para el
    conductor. Al revés —emitido en $19.906,32 y la ruta pasada hoy a día fijo— lo dejaba
    en $150.000,00: $130.093,68 de más. Las mismas dos cifras por la otra puerta
    (corregirle la ruta y devolvérsela).

    Lo que se exige es lo más fuerte que se puede exigir: el renglón de esa ruta, el
    renglón de la OTRA ruta, el total y el MODO impreso quedan exactamente como estaban.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    de_la_puerta, liq_id, emitido, hoy = _emitir(client, h, esc, modo_al_emitir)

    print(f"\n===== LA PUERTA «{puerta.__name__}», emitido {modo_al_emitir} =====")
    antes = _papel(client, h, liq_id)
    _pinta(antes, "1. emitido")
    assert _de_la_ruta(antes, RUTA_DE_LA_PUERTA) == emitido
    assert _de_la_ruta(antes, "Napoles") == EL_RENGLON_DE_NAPOLES

    primera, segunda = puerta(client, h, de_la_puerta, esc)
    _ok(primera, "el primer paso de la puerta")
    _pinta(_papel(client, h, liq_id), "2. a mitad de camino")
    _ok(segunda, "el segundo paso de la puerta")

    despues = _papel(client, h, liq_id)
    _pinta(despues, "3. de vuelta")
    print(f"      emitido ${emitido}   |   la tarifa de HOY diria ${hoy}   |   "
          f"el salto que se evita ${EL_SALTO}")

    assert _de_la_ruta(despues, RUTA_DE_LA_PUERTA) == emitido, (
        f"el papel se emitio en ${emitido} y quedo en "
        f"${_de_la_ruta(despues, RUTA_DE_LA_PUERTA)}: la tarifa de hoy diria ${hoy}, "
        f"o sea ${EL_SALTO} de diferencia para el conductor"
    )
    assert _de_la_ruta(despues, "Napoles") == EL_RENGLON_DE_NAPOLES, (
        "la otra ruta del mismo papel tampoco se puede mover"
    )
    assert D(despues["valor_transporte"]) == D(antes["valor_transporte"])
    # Y EL PAPEL DICE LO MISMO, no solo suma lo mismo: el modo impreso de cada renglón es
    # el que el conductor lee («Día completo» contra una tarifa por litro).
    def _como_se_lee(papel):
        return sorted(
            (d["fecha"], d["ruta_nombre"], d["modo_transporte"], d["litros"],
             D(d["precio_litro"]), D(d["valor"]))
            for d in papel["detalles"]
        )
    assert _como_se_lee(despues) == _como_se_lee(antes), "el papel cambio de forma"


# ---------------------------------------------------------------------------
# 2. LAS DOS PUERTAS SOBRE UN COMPROBANTE APROBADO, PAGADO Y CON ABONO
# ---------------------------------------------------------------------------
def _poner_en(client, h, liq_id, estado):
    """Deja el comprobante en el estado que se pida. Devuelve lo que quedó debiendo."""
    if estado == "borrador":
        return
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h), "aprobar")
    if estado == "aprobada":
        return
    if estado == "pagada":
        _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h), "pagar")
        return
    # CON ABONO: se le entrega una parte y queda debiendo el resto (estado 'parcial').
    _ok(client.post(
        f"{LIQUIDACIONES}/{liq_id}/pagos",
        json={"fecha": EL_DIA, "valor": "1000.00", "observaciones": "un abono"},
        headers=h,
    ), "abonar")


@pytest.mark.parametrize("puerta", LAS_DOS_PUERTAS)
@pytest.mark.parametrize("modo_al_emitir", LOS_DOS_CRUCES)
@pytest.mark.parametrize("estado", ["borrador", "aprobada", "pagada", "abono"])
def test_las_puertas_en_los_cuatro_estados_del_comprobante(
    client, base_datos, puerta, modo_al_emitir, estado
):
    """Las dos puertas, los dos cruces y los cuatro estados: el papel no se mueve NUNCA.

    Cada estado se defiende de una forma distinta, y las tres son correctas:

      · BORRADOR: la puerta se cruza y el recuadre rearma el papel con la memoria escrita.
        La cifra queda idéntica;
      · APROBADA: igual, y además el comprobante VUELVE A BORRADOR —le cambiaron los días,
        así que el visto bueno hay que darlo otra vez—. Lo que no puede cambiar es la
        cifra: antes pasaba de $150.000,00 a $19.906,32 y se volvía a aprobar sobre la
        cifra nueva;
      · PAGADA y CON ABONO: la puerta NO SE PUEDE NI ABRIR. Por ese flete ya salió plata
        de la caja y el candado de Recepción diaria rebota la corrección. Es la defensa
        más fuerte de las tres y es la que ya existía; se prueba acá para dejar dicho que
        el arreglo no la aflojó.

    En los cuatro casos se mide lo mismo: el renglón de esa ruta, el de la otra y el total.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    de_la_puerta, liq_id, emitido, hoy = _emitir(client, h, esc, modo_al_emitir)
    _poner_en(client, h, liq_id, estado)

    print(f"\n===== «{puerta.__name__}», emitido {modo_al_emitir}, estado {estado} =====")
    antes = _papel(client, h, liq_id)
    _pinta(antes, "1. emitido")
    con_plata_afuera = estado in ("pagada", "abono")

    primera, segunda = puerta(client, h, de_la_puerta, esc)
    if con_plata_afuera:
        assert primera.status_code == 422, (
            f"el primer paso de la puerta tenia que rebotar con 422 sobre un comprobante "
            f"'{estado}': por ese flete ya salio plata. Dio {primera.status_code}"
        )
        print(f"      el primer paso: rebota 422 — {primera.text[:130]}")
        # EL SEGUNDO PASO NO REBOTA, y no tiene por qué: como el primero no cambió nada,
        # el segundo le está pidiendo a la recepción lo que ya dice —ponerle 'activo' a un
        # día que sigue activo, o su propia ruta— y eso no toca ningún comprobante. Lo que
        # importa es que el papel no se haya movido, y es lo que se mide enseguida.
        print(f"      el segundo paso: {segunda.status_code} (no le pide nada nuevo)")
    else:
        _ok(primera, "el primer paso de la puerta")
        _ok(segunda, "el segundo paso de la puerta")

    despues = _papel(client, h, liq_id)
    _pinta(despues, "2. despues de la puerta")
    print(f"      emitido ${emitido}   |   hoy diria ${hoy}   |   salto ${EL_SALTO}")

    assert _de_la_ruta(despues, RUTA_DE_LA_PUERTA) == emitido
    assert _de_la_ruta(despues, "Napoles") == EL_RENGLON_DE_NAPOLES
    assert D(despues["valor_transporte"]) == D(antes["valor_transporte"])
    if con_plata_afuera:
        # Ni el estado se movió: la plata que salió sigue respaldada por la misma cifra.
        assert despues["estado"] == antes["estado"]
        assert D(despues["pagado"]) == D(antes["pagado"])


# ---------------------------------------------------------------------------
# 3. EL CONTROL: RECALCULAR SÍ LLEVA AL MODO DE HOY
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("modo_al_emitir", LOS_DOS_CRUCES)
def test_recalcular_si_re_precifica_con_el_modo_de_hoy(client, base_datos, modo_al_emitir):
    """EL CONTROL, y es la otra mitad de la regla: Recalcular SÍ re-precifica.

    Guardar la memoria no puede convertirse en un candado que no se abra. El botón
    "Recalcular" es el que el dueño oprime a propósito cuando quiere que el comprobante
    cobre lo de hoy, y después de oprimirlo los $150.000,00 tienen que quedar en
    $19.906,32 —o al revés—, con el modo impreso cambiado y todo.

    Se mide en las dos direcciones para que no pase por casualidad.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _, liq_id, emitido, hoy = _emitir(client, h, esc, modo_al_emitir)

    print(f"\n===== EL CONTROL: RECALCULAR, emitido {modo_al_emitir} =====")
    _pinta(_papel(client, h, liq_id), "1. emitido")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h), "recalcular")
    despues = _papel(client, h, liq_id)
    _pinta(despues, "2. despues de RECALCULAR")
    print(f"      emitido ${emitido}  ->  recalculado ${_de_la_ruta(despues, RUTA_DE_LA_PUERTA)}"
          f"  (hoy vale ${hoy})")

    assert _de_la_ruta(despues, RUTA_DE_LA_PUERTA) == hoy, (
        "Recalcular tiene que llevar el comprobante al modo y la tarifa de HOY"
    )
    # Y el modo impreso también: un día fijo dice "Día completo" y uno por litro trae su
    # tarifa. Si solo cuadrara la cifra, el papel podría estar diciendo otra cosa.
    modo_esperado = "litro" if modo_al_emitir == "dia_fijo" else "dia_fijo"
    de_esa_ruta = [
        d for d in despues["detalles"] if d["ruta_nombre"] == RUTA_DE_LA_PUERTA
    ]
    assert [d["modo_transporte"] for d in de_esa_ruta] == [modo_esperado]


@pytest.mark.parametrize("puerta", LAS_DOS_PUERTAS)
def test_recalcular_y_despues_la_puerta_conserva_lo_recalculado(
    client, base_datos, puerta
):
    """Después de Recalcular, la memoria dice lo NUEVO y la puerta conserva eso.

    Es la vuelta completa y es lo que prueba que la memoria no se quedó pegada en la
    primera emisión: el comprobante sale por día completo en $150.000,00, el dueño le pone
    $242,76 el litro a la ruta y oprime Recalcular —queda en $19.906,32—, y a partir de ahí
    apagar y prender (o mover la ruta y devolverla) conserva los $19.906,32, no los
    $150.000,00 de la primera vez.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    de_la_puerta, liq_id, emitido, hoy = _emitir(client, h, esc, "dia_fijo")

    print(f"\n===== RECALCULAR Y DESPUES «{puerta.__name__}» =====")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h), "recalcular")
    recalculado = _papel(client, h, liq_id)
    _pinta(recalculado, "1. recalculado")
    assert _de_la_ruta(recalculado, RUTA_DE_LA_PUERTA) == hoy == POR_LITRO_EMITIDO

    primera, segunda = puerta(client, h, de_la_puerta, esc)
    _ok(primera, "el primer paso")
    _ok(segunda, "el segundo")
    despues = _papel(client, h, liq_id)
    _pinta(despues, "2. despues de la puerta")
    assert _de_la_ruta(despues, RUTA_DE_LA_PUERTA) == POR_LITRO_EMITIDO, (
        f"despues de Recalcular la memoria tenia que decir ${POR_LITRO_EMITIDO}, no "
        f"${emitido}"
    )


# ---------------------------------------------------------------------------
# 4. LA MEMORIA MISMA: que exista, que diga lo que el papel dice, y que se limpie
# ---------------------------------------------------------------------------
def _memoria_de(db_session, liq_id):
    """Las filas de memoria de ese comprobante, leídas de la BASE."""
    db_session.expire_all()
    return {
        (None if f.ruta_id is None else str(f.ruta_id)): (
            f.modo_transporte,
            None if f.precio_litro is None else D(f.precio_litro),
            None if f.valor_dia_fijo is None else D(f.valor_dia_fijo),
        )
        for f in db_session.scalars(
            select(LiquidacionRuta).where(
                LiquidacionRuta.liquidacion_id == uuid.UUID(liq_id)
            )
        ).all()
    }


@pytest.mark.parametrize("modo_al_emitir", LOS_DOS_CRUCES)
def test_la_memoria_queda_escrita_al_emitir_y_dice_lo_que_dice_el_papel(
    client, base_datos, db_session, modo_al_emitir
):
    """Al generar, el comprobante deja escrito cómo cobró cada una de sus dos rutas.

    Es la comprobación de que la memoria no es un adorno: se lee de la base y se compara
    contra lo que el papel imprime. En día fijo guarda LA CIFRA ($150.000,00) y en por
    litro guarda LA TARIFA ($242,76), que es la diferencia que hace que corregir un litro
    mueva el renglón por litro y no mueva el del día completo.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _, liq_id, emitido, _ = _emitir(client, h, esc, modo_al_emitir)

    memoria = _memoria_de(db_session, liq_id)
    print(f"\n===== LA MEMORIA ESCRITA, emitido {modo_al_emitir} =====")
    for ruta_id, dice in memoria.items():
        print(f"      ruta {(ruta_id or 'SIN RUTA')[:8]:<8} -> {dice}")

    # Dos rutas cobradas: la de la puerta y Nápoles.
    assert len(memoria) == 2
    assert memoria[esc["napoles"]["id"]] == ("litro", NAPOLES, None)
    if modo_al_emitir == "dia_fijo":
        assert memoria[esc["fabrica"]["id"]] == ("dia_fijo", None, EL_DIA_COMPLETO)
    else:
        assert memoria[esc["fabrica"]["id"]] == ("litro", NAPOLES, None)


def test_el_recuadre_deja_quieta_la_memoria_de_la_ruta_que_se_quedo_sin_renglones(
    client, base_datos, db_session
):
    """Con el día apagado el papel no tiene renglones de esa ruta, y su memoria SIGUE ahí.

    Es el arreglo visto por dentro: el momento en que la deducción se quedaba sin fuente.
    Mientras el día está apagado, el comprobante no cobra nada de "A fábrica" —el renglón
    desapareció, que es lo correcto— y sin embargo la memoria sigue diciendo «día completo
    $150.000». Es de ahí de donde sale la cifra cuando el día vuelve.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    de_la_puerta, liq_id, emitido, hoy = _emitir(client, h, esc, "dia_fijo")

    print("\n===== CON EL DIA APAGADO: sin renglon, pero CON memoria =====")
    _ok(_put(client, h, de_la_puerta["id"], estado="inactivo"), "apagar")
    apagado = _papel(client, h, liq_id)
    _pinta(apagado, "el papel con el dia apagado")
    memoria = _memoria_de(db_session, liq_id)
    for ruta_id, dice in memoria.items():
        print(f"      ruta {(ruta_id or 'SIN RUTA')[:8]:<8} -> {dice}")

    assert _de_la_ruta(apagado, RUTA_DE_LA_PUERTA) == CERO, (
        "con el dia apagado ese renglon tiene que DESAPARECER, no quedar en $0,00"
    )
    assert memoria[esc["fabrica"]["id"]] == ("dia_fijo", None, EL_DIA_COMPLETO), (
        "la memoria de la ruta que se quedo sin renglones NO se puede borrar: es de ahi "
        "de donde sale la cifra cuando el dia vuelve"
    )

    _ok(_put(client, h, de_la_puerta["id"], estado="activo"), "prender")
    assert _de_la_ruta(_papel(client, h, liq_id), RUTA_DE_LA_PUERTA) == EL_DIA_COMPLETO


def test_recalcular_si_le_bota_la_memoria_a_la_ruta_que_el_papel_ya_no_cobra(
    client, base_datos, db_session
):
    """Recalcular rehace la memoria completa: la ruta que ese papel ya no cobra pierde su
    fila.

    Es la contraparte de la prueba de arriba y es deliberado: Recalcular re-precifica
    porque el dueño lo pidió, así que después de oprimirlo el comprobante cobra lo de hoy y
    su memoria dice lo de hoy —ni una fila de más—. Con el día apagado, "A fábrica" ya no
    es una ruta que ese papel cobre, y prenderlo después lo trae con la tarifa de HOY
    ($19.906,32), que es exactamente lo que significa haber oprimido Recalcular.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    de_la_puerta, liq_id, emitido, hoy = _emitir(client, h, esc, "dia_fijo")

    print("\n===== RECALCULAR CON EL DIA APAGADO =====")
    _ok(_put(client, h, de_la_puerta["id"], estado="inactivo"), "apagar")
    print(f"      memoria antes de recalcular: {_memoria_de(db_session, liq_id)}")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h), "recalcular")
    memoria = _memoria_de(db_session, liq_id)
    print(f"      memoria despues:             {memoria}")

    assert esc["fabrica"]["id"] not in memoria, (
        "Recalcular rehace la memoria completa: la ruta sin renglones deja de tener fila"
    )
    assert memoria[esc["napoles"]["id"]] == ("litro", NAPOLES, None)

    _ok(_put(client, h, de_la_puerta["id"], estado="activo"), "prender")
    papel = _papel(client, h, liq_id)
    _pinta(papel, "el dia vuelve DESPUES de recalcular")
    assert _de_la_ruta(papel, RUTA_DE_LA_PUERTA) == hoy == POR_LITRO_EMITIDO, (
        "despues de Recalcular el dia que vuelve se cobra con la tarifa de HOY"
    )


def test_la_memoria_se_va_con_el_comprobante(client, base_datos, db_session):
    """Al borrar el comprobante, su memoria se va con él. Sin filas huérfanas.

    La FK va con ondelete CASCADE y la relación con delete-orphan, y esto lo comprueba de
    verdad: una tabla de memoria que sobreviva a su comprobante es basura que crece sola y
    que un día le va a contestar a otro documento.

    Se borra por el ORM y no por el API a propósito: el API no tiene un DELETE de
    liquidaciones (se anulan, no se borran), pero la configuración del cascade sí existe y
    es la que hay que probar. Los seeds, los scripts y una limpieza de la base sí pasan por
    aquí.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _, liq_id, _, _ = _emitir(client, h, esc, "dia_fijo")
    assert len(_memoria_de(db_session, liq_id)) == 2

    print("\n===== BORRAR EL COMPROBANTE =====")
    liquidacion = db_session.get(Liquidacion, uuid.UUID(liq_id))
    db_session.delete(liquidacion)
    db_session.flush()
    print(f"      memoria que quedo: {_memoria_de(db_session, liq_id)}")
    assert _memoria_de(db_session, liq_id) == {}


# ---------------------------------------------------------------------------
# 5. EL PAPEL DEL CONDUCTOR: el PDF tampoco cambia de forma
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("puerta", LAS_DOS_PUERTAS)
def test_el_pdf_del_conductor_dice_lo_mismo_antes_y_despues_de_la_puerta(
    client, base_datos, puerta
):
    """El PDF emitido decía «Día completo $150.000,00» y tenía que seguir diciéndolo.

    Se compara el texto del detalle diario antes y después de cruzar la puerta, porque es
    lo que el conductor tiene en la mano. Antes ese papel amanecía diciendo «82,00 L ×
    $242,76 = $19.906,32»: la misma línea, con otra cifra y otra forma de verificarse.
    """
    import io

    from pypdf import PdfReader

    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    de_la_puerta, liq_id, emitido, hoy = _emitir(client, h, esc, "dia_fijo")

    def _texto():
        contenido = client.get(f"{LIQUIDACIONES}/{liq_id}/pdf", headers=h).content
        crudo = "\n".join(
            p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages
        )
        junto = " ".join(crudo.split())
        return junto[junto.find("Detalle diario"):junto.find("Resumen")]

    antes = _texto()
    print(f"\n===== EL PDF, puerta «{puerta.__name__}» =====\n  ANTES:   {antes}")
    primera, segunda = puerta(client, h, de_la_puerta, esc)
    _ok(primera, "el primer paso")
    _ok(segunda, "el segundo")
    despues = _texto()
    print(f"  DESPUES: {despues}")

    assert "Día completo" in antes and "150.000" in antes
    assert despues == antes, "el papel del conductor cambio de forma o de cifra"
