"""SONDAS: sitios donde el día fijo todavía podría torcerse, medidos con cifras.

No son pruebas de que algo esté bien: son mediciones. Cada una imprime lo que el
dueño vería en la pantalla y lo que terminaría en el comprobante, para poder decidir
a mano si la cifra es la correcta.
"""
from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    LIQUIDACIONES,
    NAPOLES,
    RECEPCIONES,
    TRANSPORTADORES,
    D,
    _escenario,
    _liquidar_flete,
    _recibir,
    centavos,
)
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, Medida, _medir, _ok, _put

CERO = D(0)


def _poner_tarifa(client, h, esc, valor, modo):
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


def _foto_del_dia(db_session, esc):
    return Medida(db_session, esc["alex"]["id"], EL_DIA, esc["fabrica"]["id"])


# ---------------------------------------------------------------------------
# SONDA 1 — de FIJO a POR LITRO sin liquidar: ¿qué queda en las fotos?
# ---------------------------------------------------------------------------
def test_sonda_de_fijo_a_por_litro_sin_liquidar(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== SONDA 1: de FIJO a POR LITRO, sin comprobante de por medio =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    print(f"  en FIJO                        {_foto_del_dia(db_session, esc)}")

    _poner_tarifa(client, h, esc, NAPOLES, "litro")
    litros = D("82.00") + D("137.45") + D("96.30")
    correcto = centavos(litros * NAPOLES)
    print(f"  tarifa cambiada, sin tocar     {_foto_del_dia(db_session, esc)}")
    _ok(_put(client, h, a["id"], observaciones="tocada"), "tocar")
    m = _foto_del_dia(db_session, esc)
    print(f"  tocando SOLO a Aurelio         {m}")
    print(f"  a mano por litro: {litros} L x ${NAPOLES} = ${correcto}")
    print(f"  DIFERENCIA que queda en la grilla: ${m.fotos - correcto}")

    liq = client.get(
        f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h
    ).json()
    m = _foto_del_dia(db_session, esc)
    print(f"  y el COMPROBANTE dice          ${D(liq['valor_transporte'])}")
    print(f"  fotos tras generar             {m}")
    assert D(liq["valor_transporte"]) == correcto, (
        f"el comprobante por litro salio en ${D(liq['valor_transporte'])} y a mano "
        f"da ${correcto}"
    )
    assert m.fotos == correcto


# ---------------------------------------------------------------------------
# SONDA 2 — el mismo camino en POR LITRO puro (¿esto ya pasaba antes?)
# ---------------------------------------------------------------------------
def test_sonda_la_misma_desincronizacion_ya_existia_por_litro(
    client, base_datos, db_session
):
    """Cambiarle la tarifa POR LITRO a una ruta y tocar una sola recepción deja a
    las otras con la foto vieja. Es el comportamiento de siempre —la tarifa nueva
    llega por el comprobante— y sirve para saber si la sonda 1 es algo nuevo.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== SONDA 2: lo mismo pero POR LITRO de punta a punta =====")
    _poner_tarifa(client, h, esc, NAPOLES, "litro")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    print(f"  a ${NAPOLES} el litro          {_foto_del_dia(db_session, esc)}")
    _poner_tarifa(client, h, esc, "500", "litro")
    _ok(_put(client, h, a["id"], observaciones="tocada"), "tocar")
    m = _foto_del_dia(db_session, esc)
    litros = D("82.00") + D("137.45")
    print(f"  a $500 tocando solo a Aurelio  {m}")
    print(f"  a mano a $500: {litros} L x 500 = ${centavos(litros * D('500'))}")
    print(f"  DIFERENCIA que queda: ${m.fotos - centavos(litros * D('500'))}")


# ---------------------------------------------------------------------------
# SONDA 3 — el transportador RETIRADO (borrado en suave) y su día fijo
# ---------------------------------------------------------------------------
def test_sonda_transportador_retirado(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== SONDA 3: transportador RETIRADO con un dia fijo vivo =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _medir(db_session, esc, "antes de retirarlo")
    r = client.delete(f"{TRANSPORTADORES}/{esc['alex']['id']}", headers=h)
    print(f"  DELETE del transportador -> {r.status_code}")
    if r.status_code in (200, 204):
        _ok(_put(client, h, a["id"], cantidad_litros="91.30"), "corregir")
        _medir(db_session, esc, "corregida con el transportador retirado")


# ---------------------------------------------------------------------------
# SONDA 4 — la LECHE pagada y el FLETE fijo todavía sin cobrar
# ---------------------------------------------------------------------------
def test_sonda_leche_pagada_y_flete_fijo_pendiente(client, base_datos, db_session):
    """El candado es POR CAMPO: con la liquidación de LECHE pagada todavía se le
    puede corregir la RUTA al día, porque la ruta solo la traba el flete. Eso mueve
    la foto del flete de un día fijo. El día tiene que seguir cuadrando.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== SONDA 4: leche PAGADA, flete fijo pendiente =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    generadas = client.post(
        f"{LIQUIDACIONES}/generar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "proveedor"},
        headers=h,
    ).json()["generadas"]
    for liq in generadas:
        client.post(f"{LIQUIDACIONES}/{liq['id']}/aprobar", headers=h)
        client.post(f"{LIQUIDACIONES}/{liq['id']}/pagar", headers=h)
    print(f"  {len(generadas)} liquidaciones de LECHE pagadas")
    _medir(db_session, esc, "leche pagada, flete pendiente")
    rr = _put(client, h, a["id"], ruta_id=esc["napoles"]["id"])
    print(f"  corregirle la RUTA -> {rr.status_code}")
    if rr.status_code == 200:
        _medir(db_session, esc, "Aurelio se fue a Napoles -> fabrica")
        _medir(db_session, esc, "Aurelio se fue a Napoles -> napoles",
               vale=centavos(D("82.00") * NAPOLES), ruta="napoles")
    _liquidar_flete(client, h)
    _medir(db_session, esc, "flete liquidado")


# ---------------------------------------------------------------------------
# SONDA 5 — el avance ("¿cómo voy?") frente al comprobante de verdad
# ---------------------------------------------------------------------------
def test_sonda_el_avance_promete_lo_mismo_que_el_comprobante(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== SONDA 5: el avance y el comprobante dicen lo mismo =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, "2026-07-18", "Henri", "219.45")
    avance = client.post(
        f"{LIQUIDACIONES}/previsualizar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador", "tercero_id": esc["alex"]["id"]},
        headers=h,
    )
    assert avance.status_code in (200, 201), avance.text
    cuerpo = avance.json()
    assert len(cuerpo) == 1, cuerpo
    prometido = D(cuerpo[0]["valor_transporte"])
    print(f"  el avance promete   ${prometido}")
    for d in cuerpo[0]["detalles"]:
        print(f"    {d['fecha']} {d['ruta_nombre']:<12} {d['litros']:>8} L  "
              f"[{d.get('modo_transporte')}]  ${d['valor']}")
    # El PDF del avance también sale, y con la misma cifra.
    pdf = client.post(
        f"{LIQUIDACIONES}/previsualizar/pdf",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador", "tercero_id": esc["alex"]["id"]},
        headers=h,
    )
    print(f"  PDF del avance -> {pdf.status_code} ({len(pdf.content)} bytes)")
    assert pdf.status_code == 200, pdf.text
    liq = client.get(
        f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h
    ).json()
    print(f"  el comprobante dice ${D(liq['valor_transporte'])}")
    assert prometido == D(liq["valor_transporte"]), (
        f"el avance prometio ${prometido} y el comprobante salio en "
        f"${D(liq['valor_transporte'])}"
    )
    _medir(db_session, esc, "generado")
