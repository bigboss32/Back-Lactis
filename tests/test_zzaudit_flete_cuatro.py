"""AUDITORÍA DEL FLETE — sondas 22 a 28: lo que el DUEÑO ve (grilla y PDF), el
desglose con anticipos, el cambio de modo sobre días sueltos, el cruce de modos
con plata ya salida y dos camiones el mismo día en la misma ruta.
"""
import pytest

import io
from decimal import Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers
from tests.test_zzaudit_flete_transportador import (
    D,
    DIA_1,
    DIA_2,
    FIJO,
    FIN,
    INICIO,
    LIQ,
    NAPOLES,
    RECEPCIONES,
    TRANSPORTADORES,
    cent,
    cuadre,
    cuadre_por_renglon,
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
from tests.test_zzaudit_flete_dos import cobrado_del_viaje, todas

GRILLA = "/api/v1/recepciones/grilla/quincena"
RESUMEN = "/api/v1/recepciones/resumen/periodo"
ANTICIPOS = "/api/v1/anticipos"


def transporte_de_la_grilla(client, h, desde=INICIO, hasta=FIN):
    r = client.get(f"{GRILLA}?desde={desde}&hasta={hasta}", headers=h)
    assert r.status_code == 200, r.text
    return D(r.json()["total_transporte"])


def texto_pdf(contenido: bytes) -> str:
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


# ===========================================================================
# S22. LO QUE VE EL DUEÑO EN LA GRILLA cuando el viaje se queda sin cobrar.
#      La grilla suma la foto de cada recepción: tiene que dar lo mismo que
#      cobran los comprobantes.
# ===========================================================================
@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_la_grilla_contra_lo_que_cobran_los_comprobantes(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_a = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_a}/aprobar", {})
    _post(client, h, f"{LIQ}/{liq_a}/pagos",
          {"fecha": DIA_1, "valor": "50000.00", "destinatario": "Alex"})
    tarde = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    recibir(client, h, esc, DIA_2, "Ramiro", "96.31", ruta_id=esc["napoles"]["id"])
    liq_b = generar(client, h)["generadas"][0]["id"]

    napo = cent(D("96.31") * NAPOLES)
    print(f"\n  == estado sano ==")
    grilla = transporte_de_la_grilla(client, h)
    cobran = sum((D(l["valor_transporte"]) for l in todas(client, h)
                  if l["estado"] != "anulada"), D(0))
    print(f"    grilla ${grilla}  ·  suman los comprobantes ${cobran}  "
          f"·  a mano {FIJO} + {napo} = {FIJO + napo}")
    assert grilla == cobran == FIJO + napo

    # el abono estaba mal: se borra y se anula el comprobante A
    pagos = leer(client, h, liq_a)["pagos"]
    client.delete(f"{LIQ}/{liq_a}/pagos/{pagos[0]['id']}", headers=h)
    client.post(f"{LIQ}/{liq_a}/anular", headers=h)
    client.post(f"{LIQ}/{liq_b}/recalcular", headers=h)
    generar(client, h)

    print("  == despues de anular A y recalcular B ==")
    for liq in todas(client, h):
        pinta(liq, liq["id"][:8])
    grilla = transporte_de_la_grilla(client, h)
    cobran = sum((D(l["valor_transporte"]) for l in todas(client, h)
                  if l["estado"] != "anulada"), D(0))
    print(f"    grilla ${grilla}  ·  suman los comprobantes ${cobran}  "
          f"·  diferencia ${grilla - cobran}")
    print(f"    fotos del {DIA_1}: Aurelio ${foto(db_session, a['id'])} + "
          f"Marleny ${foto(db_session, tarde['id'])}")
    assert grilla == cobran, (
        f"la grilla dice ${grilla} de flete y los comprobantes cobran ${cobran}: "
        f"${grilla - cobran} de diferencia")


# ===========================================================================
# S23. EL PDF de un comprobante con día fijo: los renglones impresos suman el
#      total impreso.
# ===========================================================================
def test_zzaudit_el_pdf_del_dia_fijo_cuadra(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    recibir(client, h, esc, DIA_2, "Henri", "61.07", ruta_id=esc["napoles"]["id"])
    liq_id = generar(client, h)["generadas"][0]["id"]
    liq = leer(client, h, liq_id)
    pinta(liq, "S23")
    total = cuadre(liq, None, "S23")

    r = client.get(f"{LIQ}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    texto = texto_pdf(r.content)
    print(f"    el PDF trae 'Dia completo': "
          f"{'Día completo' in texto or 'Dia completo' in texto}")
    for cifra in ("183.333,33", "14.825,35"):
        print(f"    el PDF trae ${cifra}: {cifra in texto}")
        assert cifra in texto, f"el PDF no imprime ${cifra}: {texto[:400]}"
    esperado = f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    print(f"    total esperado en el PDF: ${esperado} -> {esperado in texto}")
    assert esperado in texto, f"el PDF no imprime el total ${esperado}"


# ===========================================================================
# S24. EL DESGLOSE CON ANTICIPOS: total - anticipos = saldo, exacto.
# ===========================================================================
def test_zzaudit_desglose_con_anticipos(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    _post(client, h, ANTICIPOS, {
        "tipo": "transportador", "transportador_id": esc["alex"]["id"],
        "fecha": DIA_1, "valor": "77419.61"})
    liq = leer(client, h, generar(client, h)["generadas"][0]["id"])
    pinta(liq, "S24 con anticipo de $77.419,61")
    _, fot = fotos_del_comprobante(db_session, liq["id"])
    total = cuadre(liq, fot, "S24")
    print(f"    a mano: {FIJO} - 77419.61 = {FIJO - D('77419.61')}")
    assert D(liq["anticipos"]) == D("77419.61")
    assert D(liq["valor_total"]) == total
    assert D(liq["saldo"]) == total - D("77419.61") - D(liq["saldo_anterior"] or 0), (
        f"total ${total} - anticipos ${D(liq['anticipos'])} no da el saldo "
        f"${D(liq['saldo'])}")


# ===========================================================================
# S25. CAMBIAR EL MODO con los días SUELTOS (sin comprobante): ida y vuelta.
# ===========================================================================
def test_zzaudit_cambiar_el_modo_con_los_dias_sueltos(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    m = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    suma = foto(db_session, a["id"]) + foto(db_session, m["id"])
    print(f"\n    en dia fijo: ${foto(db_session, a['id'])} + "
          f"${foto(db_session, m['id'])} = ${suma}")
    assert suma == FIJO

    client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "rutas": [{"ruta_id": esc["fabrica"]["id"],
                   "valor_transporte": str(NAPOLES), "modo_transporte": "litro"}]},
        headers=h)
    f_a, f_m = foto(db_session, a["id"]), foto(db_session, m["id"])
    print(f"    tras pasar a POR LITRO: ${f_a} + ${f_m} = ${f_a + f_m}")
    print(f"    a mano: {cent(D('44.23') * NAPOLES)} + {cent(D('137.45') * NAPOLES)}")
    assert f_a == cent(D("44.23") * NAPOLES)
    assert f_m == cent(D("137.45") * NAPOLES)

    client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "rutas": [{"ruta_id": esc["fabrica"]["id"],
                   "valor_transporte": str(FIJO), "modo_transporte": "dia_fijo"}]},
        headers=h)
    f_a, f_m = foto(db_session, a["id"]), foto(db_session, m["id"])
    print(f"    de vuelta a DIA FIJO: ${f_a} + ${f_m} = ${f_a + f_m}")
    assert f_a + f_m == FIJO, f"el dia fijo quedo en ${f_a + f_m}"

    liq = leer(client, h, generar(client, h)["generadas"][0]["id"])
    pinta(liq, "S25 el papel")
    _, fot = fotos_del_comprobante(db_session, liq["id"])
    assert cuadre(liq, fot, "S25") == FIJO


# ===========================================================================
# S26. CRUCE DE MODOS CON PLATA YA SALIDA: el viaje del 16/07 se pagó como
#      DÍA COMPLETO; después la ruta pasa a POR LITRO y se anota leche tarde
#      de ese MISMO día.
# ===========================================================================
def test_zzaudit_cruce_de_modos_sobre_un_viaje_ya_pagado(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_a = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_a}/aprobar", {})
    _post(client, h, f"{LIQ}/{liq_a}/pagar", {})
    pinta(leer(client, h, liq_a), "S26 A pagado (dia completo)")

    client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "rutas": [{"ruta_id": esc["fabrica"]["id"],
                   "valor_transporte": str(NAPOLES), "modo_transporte": "litro"}]},
        headers=h)
    tarde = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    print(f"    leche anotada tarde con la ruta ya POR LITRO: foto "
          f"${D(tarde['valor_transporte'])}  (a mano "
          f"{cent(D('137.45') * NAPOLES)})")
    gen = generar(client, h)
    print(f"    Generar: generadas={len(gen['generadas'])} "
          f"omitidas={[o['motivo_codigo'] for o in gen.get('omitidas', [])]}")
    liqs = todas(client, h)
    for liq in liqs:
        pinta(liq, f"S26 {liq['id'][:8]}")
    cobrado = cobrado_del_viaje(liqs, DIA_1, "A fabrica")
    print(f"    el viaje del {DIA_1} queda cobrado, entre todos, en ${cobrado}")
    print(f"    (el fijo ya pagado eran ${FIJO})")


# ===========================================================================
# S27. DOS CAMIONES el mismo día en la misma ruta fija: dos viajes, dos fijos.
# ===========================================================================
def test_zzaudit_dos_camiones_el_mismo_dia_en_la_misma_ruta(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    # a Beto se le pone la misma ruta, también por día fijo pero con otra cifra
    otro_fijo = D("96470.11")
    client.put(f"{TRANSPORTADORES}/{esc['beto']['id']}", json={
        "rutas": [{"ruta_id": esc["fabrica"]["id"],
                   "valor_transporte": str(otro_fijo), "modo_transporte": "dia_fijo"}]},
        headers=h)
    recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    _post(client, h, RECEPCIONES, {
        "fecha": DIA_1, "proveedor_id": esc["provs"]["Gilberto"]["id"],
        "transportador_id": esc["beto"]["id"], "cantidad_litros": "82.48"})

    gen = generar(client, h)
    assert len(gen["generadas"]) == 2, gen
    total = D(0)
    for g in gen["generadas"]:
        liq = leer(client, h, g["id"])
        pinta(liq, f"S27 {liq['transportador_nombre']}")
        _, fot = fotos_del_comprobante(db_session, liq["id"])
        total += cuadre(liq, fot, "S27")
        cuadre_por_renglon(db_session, liq, "S27")
    print(f"    dos camiones, dos viajes: ${total} (a mano {FIJO + otro_fijo})")
    assert total == FIJO + otro_fijo
    grilla = transporte_de_la_grilla(client, h)
    print(f"    la grilla dice ${grilla}")
    assert grilla == total


# ===========================================================================
# S28. APAGAR EL ÚNICO DÍA de la ruta fija y volver a prenderlo, con el papel
#      teniendo además otro día por litro.
# ===========================================================================
def test_zzaudit_apagar_el_unico_dia_de_la_ruta_fija(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_2, "Henri", "61.07", ruta_id=esc["napoles"]["id"])
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    antes = D(leer(client, h, liq_id)["valor_transporte"])
    pinta(leer(client, h, liq_id), "S28 emitido")

    put_recepcion(client, h, a["id"], {"estado": "inactivo"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S28 con el unico dia fijo apagado")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    cuadre(liq, fot, "S28 apagado")

    put_recepcion(client, h, a["id"], {"estado": "activo"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S28 prendido otra vez")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    vuelta = cuadre(liq, fot, "S28 prendido")
    cuadre_por_renglon(db_session, liq, "S28 prendido")
    assert vuelta == antes, f"antes ${antes}, despues de apagar y prender ${vuelta}"
