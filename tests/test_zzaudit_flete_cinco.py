"""AUDITORÍA DEL FLETE — sondas 29 a 34: el candado POR CAMPO (leche pagada con
el flete todavía en borrador y al revés), la deuda arrastrada de un día fijo y
una secuencia larga de correcciones repetidas.
"""
from decimal import Decimal

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
from tests.test_zzaudit_flete_dos import todas

ANTICIPOS = "/api/v1/anticipos"


def generar_todo(client, h, inicio=INICIO, fin=FIN):
    return _post(client, h, f"{LIQ}/generar",
                 {"periodo_inicio": inicio, "periodo_fin": fin, "tipo": "ambos"})


# ===========================================================================
# S29. LA LECHE YA PAGADA y el FLETE todavía en borrador: el candado es POR
#      CAMPO, así que el transportador y la ruta se pueden corregir — y el
#      comprobante del flete tiene que quedar cuadrado.
# ===========================================================================
def test_zzaudit_leche_pagada_y_flete_en_borrador(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    m = recibir(client, h, esc, DIA_1, "Marleny", "137.45")

    gen = generar_todo(client, h)
    leche = [g for g in gen["generadas"] if g["tipo"] == "proveedor"]
    flete = [g for g in gen["generadas"] if g["tipo"] == "transportador"]
    assert leche and flete, gen
    for g in leche:
        _post(client, h, f"{LIQ}/{g['id']}/aprobar", {})
        _post(client, h, f"{LIQ}/{g['id']}/pagar", {})
    liq_flete = flete[0]["id"]
    pinta(leer(client, h, liq_flete), "S29 flete en borrador, leche PAGADA")

    # la ruta solo la traba el flete: con la leche pagada se puede corregir
    r = client.put(f"{RECEPCIONES}/{a['id']}",
                   json={"ruta_id": esc["napoles"]["id"]}, headers=h)
    print(f"    corregir la ruta con la leche pagada -> {r.status_code}")
    assert r.status_code == 200, r.text
    liq = leer(client, h, liq_flete)
    pinta(liq, "S29 tras mover Aurelio a Napoles")
    _, fot = fotos_del_comprobante(db_session, liq_flete)
    t = cuadre(liq, fot, "S29")
    cuadre_por_renglon(db_session, liq, "S29")
    a_mano = FIJO + cent(D("44.23") * NAPOLES)
    print(f"    a mano: {FIJO} + {cent(D('44.23') * NAPOLES)} = {a_mano}")
    assert t == a_mano

    # y la columna informativa de flete de la liquidación de LECHE PAGADA queda al día
    pagada = leer(client, h, leche[0]["id"])
    suma_dias = sum(
        (foto(db_session, x["id"]) for x in (a, m)
         if str(x["proveedor_id"]) == str(pagada["proveedor_id"])), D(0))
    print(f"    la liquidacion de leche pagada dice flete "
          f"${D(pagada['valor_transporte'])}; sus dias suman ${suma_dias}")
    assert D(pagada["valor_transporte"]) == suma_dias, (
        "la columna informativa de flete de la liquidacion pagada quedo desfasada")


# ===========================================================================
# S30. EL FLETE YA PAGADO y la LECHE en borrador: se puede corregir el precio
#      por litro de la leche, y el flete no se puede mover ni un peso.
# ===========================================================================
def test_zzaudit_flete_pagado_y_leche_en_borrador(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    _post(client, h, f"{LIQ}/{liq_id}/pagar", {})
    antes = D(leer(client, h, liq_id)["valor_transporte"])
    fot_a = foto(db_session, a["id"])

    r = client.put(f"{RECEPCIONES}/{a['id']}",
                   json={"precio_litro": "1911.37"}, headers=h)
    print(f"\n    corregir el precio por litro con el flete pagado -> "
          f"{r.status_code}")
    assert r.status_code == 200, r.text
    liq = leer(client, h, liq_id)
    pinta(liq, "S30 el flete pagado tras corregir el precio de la leche")
    assert D(liq["valor_transporte"]) == antes
    assert foto(db_session, a["id"]) == fot_a, "se movio la foto de un flete pagado"

    # y la ruta SI la traba el flete pagado
    r = client.put(f"{RECEPCIONES}/{a['id']}",
                   json={"ruta_id": esc["napoles"]["id"]}, headers=h)
    print(f"    corregir la ruta con el flete pagado -> {r.status_code}")
    assert r.status_code >= 400


# ===========================================================================
# S31. LA DEUDA ARRASTRADA de un día fijo: el anticipo se come el viaje y lo
#      que queda debiendo se le cobra en la quincena siguiente.
# ===========================================================================
def test_zzaudit_deuda_arrastrada_de_un_dia_fijo(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    _post(client, h, ANTICIPOS, {
        "tipo": "transportador", "transportador_id": esc["alex"]["id"],
        "fecha": DIA_1, "valor": "250000.00"})
    q1 = leer(client, h, generar(client, h)["generadas"][0]["id"])
    pinta(q1, "S31 quincena 1 (anticipo de $250.000 contra un viaje de $183.333,33)")
    print(f"    queda debiendo: ${D(q1.get('le_queda_debiendo') or 0)}  "
          f"(a mano {D('250000.00') - FIJO})")
    assert D(q1["saldo"]) == FIJO - D("250000.00")
    _post(client, h, f"{LIQ}/{q1['id']}/aprobar", {})

    recibir(client, h, esc, "2026-08-03", "Marleny", "137.45")
    q2 = leer(client, h, generar(client, h, "2026-08-01", "2026-08-15")["generadas"][0]["id"])
    pinta(q2, "S31 quincena 2")
    _, fot = fotos_del_comprobante(db_session, q2["id"])
    t = cuadre(q2, fot, "S31 q2")
    deuda = D(q2["saldo_anterior"] or 0)
    print(f"    total ${t} - deuda arrastrada ${deuda} - anticipos "
          f"${D(q2['anticipos'])} = saldo ${D(q2['saldo'])}")
    assert deuda == D("250000.00") - FIJO, f"la deuda arrastrada es ${deuda}"
    assert D(q2["saldo"]) == t - deuda - D(q2["anticipos"]), (
        "el desglose de la quincena 2 no suma la cifra grande")


# ===========================================================================
# S32. UNA SECUENCIA LARGA DE CORRECCIONES, repetida: el papel tiene que
#      volver siempre al mismo sitio.
# ===========================================================================
def test_zzaudit_secuencia_larga_de_correcciones(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    fab, nap = esc["fabrica"]["id"], esc["napoles"]["id"]
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    m = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    recibir(client, h, esc, DIA_2, "Henri", "61.07", ruta_id=nap)
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    liq = leer(client, h, liq_id)
    pinta(liq, "S32 punto de partida")
    partida = cuadre(liq, None, "S32 partida")
    fotos_partida = {r["id"]: foto(db_session, r["id"]) for r in (a, m)}

    for vuelta in range(3):
        put_recepcion(client, h, a["id"], {"cantidad_litros": "91.30"})
        put_recepcion(client, h, a["id"], {"estado": "inactivo"})
        put_recepcion(client, h, a["id"], {"estado": "activo"})
        put_recepcion(client, h, a["id"], {"ruta_id": nap})
        put_recepcion(client, h, a["id"], {"ruta_id": fab})
        put_recepcion(client, h, a["id"], {"transportador_id": esc["beto"]["id"]})
        put_recepcion(client, h, a["id"], {"transportador_id": esc["alex"]["id"]})
        put_recepcion(client, h, a["id"], {"fecha": DIA_2})
        put_recepcion(client, h, a["id"], {"fecha": DIA_1})
        put_recepcion(client, h, a["id"], {"cantidad_litros": "44.23"})
        liq = leer(client, h, liq_id)
        _, fot = fotos_del_comprobante(db_session, liq_id)
        t = cuadre(liq, fot, f"S32 vuelta {vuelta + 1}")
        cuadre_por_renglon(db_session, liq, f"S32 vuelta {vuelta + 1}")
        ahora = {r["id"]: foto(db_session, r["id"]) for r in (a, m)}
        pinta(liq, f"S32 tras la vuelta {vuelta + 1}")
        print(f"    fotos: {[str(v) for v in ahora.values()]} "
              f"(al empezar {[str(v) for v in fotos_partida.values()]})")
        assert t == partida, (
            f"la vuelta {vuelta + 1} dejo el papel en ${t} y empezo en ${partida}")
        assert ahora == fotos_partida, (
            f"la vuelta {vuelta + 1} dejo otras fotos: {ahora} vs {fotos_partida}")


# ===========================================================================
# S33. EL REPARTO DEL FIJO A MANO: tres proveedores, cifras feas, resto mayor.
# ===========================================================================
def test_zzaudit_el_reparto_del_fijo_a_mano(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    m = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    g = recibir(client, h, esc, DIA_1, "Gilberto", "82.48")
    litros = D("44.23") + D("137.45") + D("82.48")
    print(f"\n    litros del dia: {litros}")
    exactos = {}
    for nombre, rid, l in (("Aurelio", a["id"], D("44.23")),
                           ("Marleny", m["id"], D("137.45")),
                           ("Gilberto", g["id"], D("82.48"))):
        exacto = FIJO * l / litros
        piso = exacto.quantize(D("0.01"), rounding="ROUND_DOWN")
        exactos[nombre] = (exacto, piso, foto(db_session, rid))
        print(f"    {nombre:<9}{l:>8} L   exacto {exacto}   piso {piso}   "
              f"guardado ${exactos[nombre][2]}")
    suma = sum(v[2] for v in exactos.values())
    pisos = sum(v[1] for v in exactos.values())
    print(f"    suma de los pisos ${pisos}; faltaban ${FIJO - pisos}; "
          f"suma guardada ${suma}")
    assert suma == FIJO, f"las tres fotos suman ${suma} y el viaje vale ${FIJO}"
    for nombre, (exacto, piso, guardado) in exactos.items():
        assert abs(guardado - exacto) <= D("0.01"), (
            f"la foto de {nombre} se aparto mas de un centavo de su cuenta")

    liq = leer(client, h, generar(client, h)["generadas"][0]["id"])
    pinta(liq, "S33")
    _, fot = fotos_del_comprobante(db_session, liq["id"])
    assert cuadre(liq, fot, "S33") == FIJO
    cuadre_por_renglon(db_session, liq, "S33")
