"""AUDITORÍA DEL FLETE — sondas 7 a 12: la leche anotada TARDE sobre un viaje fijo
ya cobrado, la tarifa y el modo cambiados con el papel ya emitido, y qué queda
escrito cuando el comprobante que cobraba el viaje deja de cobrarlo.
"""
import pytest

from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_zzaudit_flete_transportador import (
    D,
    DIA_1,
    DIA_2,
    FIJO,
    LIQ,
    NAPOLES,
    RECEPCIONES,
    TRANSPORTADORES,
    cent,
    cuadre,
    escenario,
    fotos_del_comprobante,
    foto,
    generar,
    leer,
    pinta,
    put_recepcion,
    recibir,
    _post,
)


def todas(client, h):
    r = client.get(f"{LIQ}?size=100", headers=h)
    assert r.status_code == 200, r.text
    return [leer(client, h, x["id"]) for x in r.json()["items"]
            if x["tipo"] == "transportador"]


def cobrado_del_viaje(liqs, fecha, ruta_nombre):
    """Cuánta plata cobran ENTRE TODOS los comprobantes vivos por ese (día, ruta)."""
    total = D(0)
    for liq in liqs:
        if liq["estado"] == "anulada":
            continue
        for d in liq["detalles"]:
            if d["fecha"] == fecha and (d["ruta_nombre"] or "") == ruta_nombre:
                total += D(d["valor"])
    return total


# ===========================================================================
# S7. LECHE ANOTADA TARDE sobre un viaje fijo YA PAGADO.
# ===========================================================================
def test_zzaudit_leche_anotada_tarde_sobre_viaje_pagado(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    _post(client, h, f"{LIQ}/{liq_id}/pagar", {})
    liq = leer(client, h, liq_id)
    pinta(liq, "S7 comprobante PAGADO")
    assert D(liq["valor_transporte"]) == FIJO

    tarde = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    print(f"    foto de la leche anotada tarde: ${D(tarde['valor_transporte'])}")
    assert D(tarde["valor_transporte"]) == 0

    otra = generar(client, h)
    print(f"    Generar otra vez: generadas={len(otra['generadas'])} "
          f"omitidas={[o['motivo_codigo'] for o in otra.get('omitidas', [])]}")
    liqs = todas(client, h)
    cobrado = cobrado_del_viaje(liqs, DIA_1, "A fabrica")
    print(f"    el viaje del {DIA_1} esta cobrado, entre todos los papeles, "
          f"en ${cobrado}")
    assert cobrado == FIJO, f"el viaje fijo quedo cobrado ${cobrado}, no ${FIJO}"
    assert D(leer(client, h, liq_id)["valor_transporte"]) == FIJO


# ===========================================================================
# S8. El renglón "$0 — Ya cobrado" que queda escrito en un SEGUNDO comprobante,
#     y qué pasa con él cuando el comprobante que sí cobraba el viaje se anula.
#
#     Camino: se paga por error → se borra el pago → se anula (que es EL flujo
#     de corrección del sistema).
# ===========================================================================
@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_el_renglon_ya_cobrado_cuando_se_anula_el_que_cobraba(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    nap = esc["napoles"]["id"]

    # 1) el viaje del 16/07 se cobra y SE PAGA en el comprobante A
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_a = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_a}/aprobar", {})
    _post(client, h, f"{LIQ}/{liq_a}/pagar", {})
    pinta(leer(client, h, liq_a), "S8 comprobante A (pagado)")

    # 2) leche anotada tarde de ESE MISMO dia: entra en $0 (el viaje ya se cobro)
    tarde = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    assert D(tarde["valor_transporte"]) == 0

    # 3) un dia POR LITRO despues: sale el comprobante B, que se lleva la leche
    #    anotada tarde en un renglon de $0 rotulado "Ya cobrado"
    recibir(client, h, esc, DIA_2, "Ramiro", "96.31", ruta_id=nap)
    gen = generar(client, h)
    assert gen["generadas"], gen
    liq_b = gen["generadas"][0]["id"]
    b = leer(client, h, liq_b)
    pinta(b, "S8 comprobante B")
    marcados = [d for d in b["detalles"] if d.get("dia_fijo_ya_cobrado")]
    assert marcados, "el dia ya cobrado tenia que salir rotulado"

    liqs = todas(client, h)
    antes = cobrado_del_viaje(liqs, DIA_1, "A fabrica")
    print(f"    ANTES: el viaje del {DIA_1} esta cobrado en ${antes}")
    assert antes == FIJO

    # 4) el pago de A estaba mal: se borra y se anula A
    pagos = leer(client, h, liq_a).get("pagos") or []
    print(f"    pagos de A: {len(pagos)}")
    if pagos:
        r = client.delete(f"{LIQ}/{liq_a}/pagos/{pagos[0]['id']}", headers=h)
        print(f"    borrar el pago de A: {r.status_code}")
    r = client.post(f"{LIQ}/{liq_a}/anular", headers=h)
    print(f"    anular A: {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        print("    (A no se dejo anular: el candado aguanta)")
        return

    liqs = todas(client, h)
    for liq in liqs:
        pinta(liq, f"S8 tras anular A - {liq['id'][:8]}")
    gen = generar(client, h)
    print(f"    Generar tras anular: generadas={len(gen['generadas'])} "
          f"omitidas={[(o['motivo_codigo'], o['motivo'][:70]) for o in gen.get('omitidas', [])]}")
    liqs = todas(client, h)
    for liq in liqs:
        pinta(liq, f"S8 final - {liq['id'][:8]}")
    despues = cobrado_del_viaje(liqs, DIA_1, "A fabrica")
    f_a, f_m = foto(db_session, a["id"]), foto(db_session, tarde["id"])
    print(f"    DESPUES: el viaje del {DIA_1} esta cobrado en ${despues}")
    print(f"    fotos vivas del {DIA_1}: Aurelio ${f_a} + Marleny ${f_m} = ${f_a + f_m}")
    assert despues == FIJO, (
        f"el {DIA_1} tiene 181,68 L de leche viva y el viaje quedo cobrado en "
        f"${despues}: faltan ${FIJO - despues}")
    assert f_a + f_m == despues, (
        f"las fotos del dia suman ${f_a + f_m} y el viaje se cobra ${despues}")


# ===========================================================================
# S9. CAMBIAR LA TARIFA FIJA con el papel ya emitido.
# ===========================================================================
def test_zzaudit_cambiar_la_tarifa_con_el_papel_emitido(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    m = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})

    nuevo = D("207450.77")
    r = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(nuevo),
             "modo_transporte": "dia_fijo"},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": "litro"},
        ]}, headers=h)
    assert r.status_code == 200, r.text

    liq = leer(client, h, liq_id)
    pinta(liq, "S9 tras subir el fijo (sin tocar el comprobante)")
    assert D(liq["valor_transporte"]) == FIJO

    put_recepcion(client, h, m["id"], {"observaciones": "llego a las 6"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S9 tras escribir una observacion (recuadre)")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S9 recuadre")
    assert t == FIJO, f"escribir una observacion re-precifico el papel: ${FIJO} -> ${t}"

    _post(client, h, f"{LIQ}/{liq_id}/recalcular", {})
    liq = leer(client, h, liq_id)
    pinta(liq, "S9 tras RECALCULAR")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S9 recalcular")
    assert t == nuevo, f"Recalcular tenia que dejar el viaje en ${nuevo} y dio ${t}"


# ===========================================================================
# S10. CAMBIAR EL MODO con el papel ya emitido: fijo -> por litro.
# ===========================================================================
def test_zzaudit_cambiar_el_modo_con_el_papel_emitido(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})

    r = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": "litro"},
        ]}, headers=h)
    assert r.status_code == 200, r.text

    liq = leer(client, h, liq_id)
    pinta(liq, "S10 tras cambiar el modo a POR LITRO (sin tocar el papel)")
    assert D(liq["valor_transporte"]) == FIJO

    put_recepcion(client, h, a["id"], {"cantidad_litros": "91.30"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S10 tras corregir litros (recuadre, modo ya cambiado)")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S10 recuadre")
    assert t == FIJO, f"el papel decia Dia completo ${FIJO} y el recuadre lo dejo en ${t}"
    assert all(d["modo_transporte"] == "dia_fijo" for d in liq["detalles"])

    _post(client, h, f"{LIQ}/{liq_id}/recalcular", {})
    liq = leer(client, h, liq_id)
    pinta(liq, "S10 tras RECALCULAR (ya por litro)")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S10 recalcular")
    a_mano = cent((D("91.30") + D("137.45")) * NAPOLES)
    print(f"    a mano: (91,30 + 137,45) x {NAPOLES} = {a_mano}")
    assert t == a_mano


# ===========================================================================
# S11. CAMBIAR EL MODO al revés (por litro -> día fijo) con el papel emitido,
#      que es el cruce que más duele: hoy un fijo vale $0 por litro.
# ===========================================================================
def test_zzaudit_de_por_litro_a_dia_fijo_con_el_papel_emitido(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    # la ruta "A fabrica" se emite POR LITRO a $311,08
    por_litro = D("311.08")
    r = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "rutas": [{"ruta_id": esc["fabrica"]["id"],
                   "valor_transporte": str(por_litro), "modo_transporte": "litro"}]},
        headers=h)
    assert r.status_code == 200, r.text
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    liq = leer(client, h, liq_id)
    pinta(liq, "S11 emitido POR LITRO a $311,08")
    emitido = D(liq["valor_transporte"])
    assert emitido == cent((D("44.23") + D("137.45")) * por_litro)

    # ahora la ruta pasa a DIA FIJO $183.333,33
    r = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(FIJO),
             "modo_transporte": "dia_fijo"},
        ]}, headers=h)
    assert r.status_code == 200, r.text

    put_recepcion(client, h, a["id"], {"cantidad_litros": "91.30"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S11 tras corregir litros (recuadre con el modo ya cambiado)")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S11 recuadre")
    a_mano = cent((D("91.30") + D("137.45")) * por_litro)
    print(f"    a mano, a la tarifa con que se emitio: (91,30 + 137,45) x "
          f"{por_litro} = {a_mano}")
    assert all(d["modo_transporte"] == "litro" for d in liq["detalles"]), (
        "el recuadre re-clasifico un papel emitido POR LITRO")
    assert t == a_mano, f"el recuadre dejo ${t} y a la tarifa emitida son ${a_mano}"


# ===========================================================================
# S12. UN COMPROBANTE CON PLATA YA SALIDA NO SE MUEVE, por ninguna puerta.
# ===========================================================================
def test_zzaudit_con_plata_salida_no_se_mueve_nada(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    m = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    # UN ABONO, no el pago completo: la mitad justa de una cifra fea.
    _post(client, h, f"{LIQ}/{liq_id}/pagos",
          {"fecha": DIA_2, "valor": "91666.67", "destinatario": "Alex"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S12 con un abono de $91.666,67")
    antes = D(liq["valor_transporte"])
    fot_a, fot_m = foto(db_session, a["id"]), foto(db_session, m["id"])

    intentos = [
        ("litros", a["id"], {"cantidad_litros": "91.30"}),
        ("fecha", a["id"], {"fecha": DIA_2}),
        ("estado", a["id"], {"estado": "inactivo"}),
        ("ruta", a["id"], {"ruta_id": esc["napoles"]["id"]}),
        ("transportador", a["id"], {"transportador_id": esc["beto"]["id"]}),
    ]
    for nombre, rid, body in intentos:
        r = client.put(f"{RECEPCIONES}/{rid}", json=body, headers=h)
        print(f"    PUT {nombre:<14} -> {r.status_code}")
        assert r.status_code >= 400, (
            f"con un abono hecho se dejo cambiar {nombre}: {r.text[:200]}")

    r = client.delete(f"{RECEPCIONES}/{a['id']}", headers=h)
    print(f"    DELETE recepcion -> {r.status_code}")
    assert r.status_code >= 400

    r = client.post(f"{LIQ}/{liq_id}/recalcular", headers=h)
    print(f"    POST recalcular  -> {r.status_code}")
    assert r.status_code >= 400

    # subir la tarifa NO puede mover el papel abonado
    client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "rutas": [{"ruta_id": esc["fabrica"]["id"],
                   "valor_transporte": "207450.77", "modo_transporte": "dia_fijo"}]},
        headers=h)
    # y una observacion, que SI es un campo libre, tampoco
    put_recepcion(client, h, a["id"], {"observaciones": "el camion llego tarde"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S12 despues de todos los intentos")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    assert D(liq["valor_transporte"]) == antes, (
        f"el papel abonado paso de ${antes} a ${D(liq['valor_transporte'])}")
    assert (foto(db_session, a["id"]), foto(db_session, m["id"])) == (fot_a, fot_m), (
        "se movieron las fotos de un flete con plata ya entregada")
    cuadre(liq, fot, "S12 final")
