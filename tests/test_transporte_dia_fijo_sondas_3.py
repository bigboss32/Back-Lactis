"""TERCERA TANDA: el cruce de modos entre la RECEPCIÓN y el COMPROBANTE.

La recepción escribe su foto con la tarifa DE HOY; el recuadre del comprobante
conserva el modo DE ANTES. Cuando los dos no coinciden —porque alguien le cambió el
modo a la ruta entre una cosa y la otra— hay que mirar qué queda.

Las dos direcciones:

  · el comprobante decía DÍA FIJO y hoy la ruta es POR LITRO;
  · el comprobante decía POR LITRO y hoy la ruta es DÍA FIJO.

En las dos, el comprobante ya emitido NO se puede mover por un recuadre: el papel que
el conductor vio decía una cifra, y escribir una observación no puede cambiarla.
"""
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
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, Medida, _ok, _put

CERO = D(0)


def _poner(client, h, esc, valor, modo):
    _ok(client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(valor),
             "modo_transporte": modo},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": "litro"},
        ]},
        headers=h,
    ), "tarifa")


def _ver(client, h, db_session, esc, liq_id, paso):
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    m = Medida(db_session, esc["alex"]["id"], EL_DIA, esc["fabrica"]["id"])
    renglones = " ".join(
        f"[{d['modo_transporte']}]{d['litros']}L x ${d['precio_litro']} = ${d['valor']}"
        for d in liq["detalles"]
    )
    print(f"  {paso:<44} comprobante ${liq['valor_transporte']} ({liq['estado']})")
    print(f"  {'':<44} renglones: {renglones}")
    print(f"  {'':<44} fotos: {m}")
    return liq, m


# ---------------------------------------------------------------------------
# SONDA 9 — el comprobante decía POR LITRO y hoy la ruta es DÍA FIJO
# ---------------------------------------------------------------------------
def test_sonda_comprobante_por_litro_con_la_ruta_pasada_a_dia_fijo(
    client, base_datos, db_session
):
    """A MANO:

      · 16/07 a $242,76 el litro con 82,00 + 137,45 L = 219,45 L → $53.273,68
        (fotos $19.906,32 y $33.367,36).
      · Se le pone DÍA FIJO $150.000 a la ruta. El comprobante YA EMITIDO no se
        re-precifica solo: sigue en $53.273,68 hasta que alguien oprima Recalcular.
      · Se le corrige un litro a Aurelio (91,30 L). Eso es un RECUADRE, y un recuadre
        por litro SÍ vuelve a derivar el renglón de los litros que quedaron:
        (91,30 + 137,45) × $242,76 = 228,75 × 242,76 = $55.531,35.

    Lo que NO puede pasar es que el comprobante pierda plata porque la foto de la
    recepción corregida se escribió con el modo NUEVO (día fijo) mientras el renglón
    se sigue armando con el modo VIEJO (por litro).
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _poner(client, h, esc, NAPOLES, "litro")
    print("\n===== SONDA 9: comprobante POR LITRO, ruta pasada a DIA FIJO =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    por_litro = centavos((D("82.00") + D("137.45")) * NAPOLES)
    liq, _ = _ver(client, h, db_session, esc, liq_id, "generado por litro")
    print(f"  a mano: 219,45 L x ${NAPOLES} = ${por_litro}")
    assert D(liq["valor_transporte"]) == por_litro

    _poner(client, h, esc, FIJO, "dia_fijo")
    _ver(client, h, db_session, esc, liq_id, "ruta pasada a DIA FIJO (sin tocar)")

    _ok(_put(client, h, a["id"], cantidad_litros="91.30"), "corregir litros")
    esperado = centavos((D("91.30") + D("137.45")) * NAPOLES)
    liq, m = _ver(client, h, db_session, esc, liq_id, "corregidos los litros (RECUADRE)")
    print(f"  a mano por litro con 228,75 L: ${esperado}")
    assert D(liq["valor_transporte"]) == sum(
        (D(d["valor"]) for d in liq["detalles"]), CERO
    ), "el comprobante dejo de ser la suma de sus renglones"
    assert m.fotos == D(liq["valor_transporte"]), (
        f"el comprobante dice ${liq['valor_transporte']} y sus fotos suman ${m.fotos}"
    )
    assert D(liq["valor_transporte"]) == esperado, (
        f"el comprobante quedo en ${liq['valor_transporte']} y a mano da ${esperado}: "
        f"se perdieron ${esperado - D(liq['valor_transporte'])} por un RECUADRE"
    )

    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h), "recalcular")
    liq, m = _ver(client, h, db_session, esc, liq_id, "RECALCULAR (si re-precifica)")
    assert D(liq["valor_transporte"]) == FIJO, liq["valor_transporte"]
    assert m.fotos == FIJO


# ---------------------------------------------------------------------------
# SONDA 10 — lo mismo pero sobre un comprobante APROBADO
# ---------------------------------------------------------------------------
def test_sonda_lo_mismo_sobre_un_comprobante_aprobado(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _poner(client, h, esc, NAPOLES, "litro")
    print("\n===== SONDA 10: lo mismo, con el comprobante APROBADO =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    liq_id = _liquidar_flete(client, h)["id"]
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h), "aprobar")
    _ver(client, h, db_session, esc, liq_id, "aprobado por litro")

    _poner(client, h, esc, FIJO, "dia_fijo")
    _ok(_put(client, h, a["id"], observaciones="una observacion"), "observacion")
    litros = D("82.00") + D("137.45") + D("96.30")
    esperado = centavos(litros * NAPOLES)
    liq, m = _ver(client, h, db_session, esc, liq_id, "solo una OBSERVACION")
    print(f"  a mano por litro: {litros} L x ${NAPOLES} = ${esperado}")
    assert D(liq["valor_transporte"]) == esperado, (
        f"escribir una observacion movio el comprobante APROBADO de ${esperado} a "
        f"${liq['valor_transporte']}"
    )
    assert m.fotos == D(liq["valor_transporte"])
