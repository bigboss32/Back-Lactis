"""AUDITORÍA DEL FLETE — sondas 13 a 20: el desglose POR (día, ruta), mover de
fecha y de ruta con dos modos a la vez, la tarifa general cobrada por día fijo,
el avance contra el papel, la puerta del precio del renglón y el tenant.
"""
import pytest

from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_zzaudit_flete_transportador import (
    D,
    DIA_1,
    DIA_2,
    DIA_3,
    FIJO,
    FIN,
    GENERAL,
    INICIO,
    LIQ,
    NAPOLES,
    PROVEEDORES,
    RECEPCIONES,
    RUTAS,
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


# ===========================================================================
# S13. LA RECUPERACIÓN del renglón "$0 Ya cobrado" que quedó viejo: ¿basta
#      con oprimir Recalcular?
# ===========================================================================
@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_recuperar_el_renglon_ya_cobrado_viejo(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_a = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_a}/aprobar", {})
    _post(client, h, f"{LIQ}/{liq_a}/pagar", {})
    tarde = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    recibir(client, h, esc, DIA_2, "Ramiro", "96.31", ruta_id=esc["napoles"]["id"])
    liq_b = generar(client, h)["generadas"][0]["id"]

    pagos = leer(client, h, liq_a)["pagos"]
    client.delete(f"{LIQ}/{liq_a}/pagos/{pagos[0]['id']}", headers=h)
    client.post(f"{LIQ}/{liq_a}/anular", headers=h)

    print("\n  == el dueno oprime RECALCULAR en el comprobante que quedo ==")
    r = client.post(f"{LIQ}/{liq_b}/recalcular", headers=h)
    print(f"    recalcular B -> {r.status_code}")
    b = leer(client, h, liq_b)
    pinta(b, "S13 B tras Recalcular")
    liqs = todas(client, h)
    cobrado = cobrado_del_viaje(liqs, DIA_1, "A fabrica")
    f_a, f_m = foto(db_session, a["id"]), foto(db_session, tarde["id"])
    print(f"    el viaje del {DIA_1} queda cobrado en ${cobrado}")
    print(f"    fotos del dia: Aurelio (44,23 L, suelto) ${f_a} + "
          f"Marleny (137,45 L, en B) ${f_m} = ${f_a + f_m}")
    gen = generar(client, h)
    print(f"    Generar: generadas={len(gen['generadas'])} "
          f"omitidas={[o['motivo_codigo'] for o in gen.get('omitidas', [])]}")
    liqs = todas(client, h)
    cobrado = cobrado_del_viaje(liqs, DIA_1, "A fabrica")
    f_a, f_m = foto(db_session, a["id"]), foto(db_session, tarde["id"])
    print(f"    FINAL: viaje cobrado ${cobrado}; fotos ${f_a} + ${f_m} = ${f_a + f_m}")
    assert cobrado == FIJO, f"el viaje quedo cobrado ${cobrado}"
    assert f_a + f_m == cobrado, (
        f"el desglose del dia suma ${f_a + f_m} y el viaje se cobra ${cobrado}")


# ===========================================================================
# S14. El MISMO defecto por un camino más corto: basta un ABONO (una
#      liquidación con abono no reserva el período).
# ===========================================================================
@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_el_mismo_hueco_entrando_por_un_abono(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_a = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_a}/aprobar", {})
    _post(client, h, f"{LIQ}/{liq_a}/pagos",
          {"fecha": DIA_1, "valor": "50000.00", "destinatario": "Alex"})
    pinta(leer(client, h, liq_a), "S14 A con abono de $50.000")

    tarde = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    print(f"    leche anotada tarde: foto ${D(tarde['valor_transporte'])}")
    recibir(client, h, esc, DIA_2, "Ramiro", "96.31", ruta_id=esc["napoles"]["id"])
    gen = generar(client, h)
    print(f"    Generar con A abonada: generadas={len(gen['generadas'])} "
          f"omitidas={[o['motivo_codigo'] for o in gen.get('omitidas', [])]}")
    if not gen["generadas"]:
        print("    (con abono el periodo sigue reservado: este camino no abre)")
        return
    liq_b = gen["generadas"][0]["id"]
    pinta(leer(client, h, liq_b), "S14 B")

    pagos = leer(client, h, liq_a)["pagos"]
    r1 = client.delete(f"{LIQ}/{liq_a}/pagos/{pagos[0]['id']}", headers=h)
    r2 = client.post(f"{LIQ}/{liq_a}/anular", headers=h)
    print(f"    borrar abono -> {r1.status_code}; anular A -> {r2.status_code}")
    liqs = todas(client, h)
    for liq in liqs:
        pinta(liq, f"S14 {liq['id'][:8]}")
    cobrado = cobrado_del_viaje(liqs, DIA_1, "A fabrica")
    f_a, f_m = foto(db_session, a["id"]), foto(db_session, tarde["id"])
    print(f"    viaje del {DIA_1} cobrado en ${cobrado}; fotos ${f_a} + ${f_m}")
    assert cobrado == FIJO, (
        f"el {DIA_1} tiene 181,68 L vivos y el viaje quedo cobrado en ${cobrado}")


# ===========================================================================
# S15. MOVER DE FECHA dentro del período: un viaje se parte en dos, y al
#      devolverlo tiene que volver a ser uno.
# ===========================================================================
def test_zzaudit_mover_de_fecha_dentro_del_periodo(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    antes = D(leer(client, h, liq_id)["valor_transporte"])

    put_recepcion(client, h, a["id"], {"fecha": DIA_3})
    liq = leer(client, h, liq_id)
    pinta(liq, "S15 Aurelio movido al 18/07")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    ida = cuadre(liq, fot, "S15 ida")
    cuadre_por_renglon(db_session, liq, "S15 ida")
    print(f"    dos dias, dos viajes: ${ida} (a mano {FIJO * 2})")
    assert ida == FIJO * 2, f"dos dias fijos tenian que valer ${FIJO * 2} y dan ${ida}"

    put_recepcion(client, h, a["id"], {"fecha": DIA_1})
    liq = leer(client, h, liq_id)
    pinta(liq, "S15 Aurelio devuelto al 16/07")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    vuelta = cuadre(liq, fot, "S15 vuelta")
    cuadre_por_renglon(db_session, liq, "S15 vuelta")
    assert vuelta == antes, f"deshacer dejo ${vuelta} y antes era ${antes}"


# ===========================================================================
# S16. MOVER DE FECHA FUERA del período: el día se suelta del comprobante.
# ===========================================================================
def test_zzaudit_mover_de_fecha_fuera_del_periodo(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    m = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})

    put_recepcion(client, h, a["id"], {"fecha": "2026-08-03"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S16 Aurelio movido al 03/08 (otra quincena)")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S16")
    cuadre_por_renglon(db_session, liq, "S16")
    print(f"    foto de Aurelio, ya suelto en agosto: ${foto(db_session, a['id'])}")
    assert t == FIJO
    assert foto(db_session, a["id"]) == FIJO, (
        "el dia solitario de agosto es un viaje entero y tiene que valer el fijo")

    gen = generar(client, h, "2026-08-01", "2026-08-15")
    assert gen["generadas"], gen
    liq2 = leer(client, h, gen["generadas"][0]["id"])
    pinta(liq2, "S16 la quincena de agosto")
    _, fot2 = fotos_del_comprobante(db_session, liq2["id"])
    assert cuadre(liq2, fot2, "S16 agosto") == FIJO


# ===========================================================================
# S17. DOS RUTAS EL MISMO DÍA CON MODOS DISTINTOS: mover una recepción de la
#      fija a la de por litro cuando la de por litro YA está en el papel.
# ===========================================================================
def test_zzaudit_dos_modos_el_mismo_dia_y_mover_entre_ellos(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    fab, nap = esc["fabrica"]["id"], esc["napoles"]["id"]
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    recibir(client, h, esc, DIA_1, "Henri", "61.07", ruta_id=nap)
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    liq = leer(client, h, liq_id)
    pinta(liq, "S17 emitido")
    antes = cuadre(liq, None, "S17 emitido")
    cuadre_por_renglon(db_session, liq, "S17 emitido")
    a_mano = FIJO + cent(D("61.07") * NAPOLES)
    assert antes == a_mano, f"${antes} vs a mano ${a_mano}"

    put_recepcion(client, h, a["id"], {"ruta_id": nap})
    liq = leer(client, h, liq_id)
    pinta(liq, "S17 Aurelio pasado a Napoles")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    ida = cuadre(liq, fot, "S17 ida")
    cuadre_por_renglon(db_session, liq, "S17 ida")
    esperado = FIJO + cent((D("61.07") + D("44.23")) * NAPOLES)
    print(f"    a mano: {FIJO} + (61,07+44,23) x {NAPOLES} = {esperado}")
    assert ida == esperado, f"dio ${ida} y a mano son ${esperado}"

    put_recepcion(client, h, a["id"], {"ruta_id": fab})
    liq = leer(client, h, liq_id)
    pinta(liq, "S17 Aurelio devuelto a A fabrica")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    vuelta = cuadre(liq, fot, "S17 vuelta")
    cuadre_por_renglon(db_session, liq, "S17 vuelta")
    assert vuelta == antes, f"deshacer dejo ${vuelta} y antes era ${antes}"


# ===========================================================================
# S18. LA TARIFA GENERAL COBRADA POR DÍA FIJO (días sin ruta).
# ===========================================================================
def test_zzaudit_tarifa_general_por_dia_fijo(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    fijo_general = D("147850.33")
    alex = _post(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": str(fijo_general),
        "modo_transporte": "dia_fijo", "rutas": []})
    provs = {}
    for nombre in ("Aurelio", "Marleny", "Gilberto"):
        provs[nombre] = _post(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "La Vega", "precio_litro": "1800"})
    for nombre, litros in (("Aurelio", "44.23"), ("Marleny", "137.45"),
                           ("Gilberto", "82.48")):
        _post(client, h, RECEPCIONES, {
            "fecha": DIA_1, "proveedor_id": provs[nombre]["id"],
            "transportador_id": alex["id"], "cantidad_litros": litros})

    liq = leer(client, h, generar(client, h)["generadas"][0]["id"])
    pinta(liq, "S18 tarifa general en dia fijo, tres proveedores sin ruta")
    _, fot = fotos_del_comprobante(db_session, liq["id"])
    t = cuadre(liq, fot, "S18")
    cuadre_por_renglon(db_session, liq, "S18")
    print(f"    el dia vale ${t}; el error que no puede pasar es ${fijo_general * 3}")
    assert t == fijo_general, f"tres proveedores sin ruta dieron ${t}"


# ===========================================================================
# S19. EL AVANCE ("¿cómo voy?") tiene que decir lo mismo que el papel.
# ===========================================================================
def test_zzaudit_el_avance_dice_lo_mismo_que_el_papel(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    recibir(client, h, esc, DIA_2, "Henri", "61.07", ruta_id=esc["napoles"]["id"])

    pre = _post(client, h, f"{LIQ}/previsualizar", {
        "periodo_inicio": INICIO, "periodo_fin": FIN,
        "tipo": "transportador", "tercero_id": esc["alex"]["id"]})
    avance = pre[0] if isinstance(pre, list) else pre
    print("\n  == AVANCE ==")
    for d in avance["detalles"]:
        print(f"    {d['fecha']}  {(d.get('ruta_nombre') or '-'):<11}"
              f"{D(d['litros']):>9} L  [{d.get('modo_transporte')}]  ${D(d['valor'])}")
    suma_avance = sum((D(d["valor"]) for d in avance["detalles"]), D(0))
    print(f"    total avance ${D(avance['valor_transporte'])} "
          f"(renglones suman ${suma_avance})")
    assert suma_avance == D(avance["valor_transporte"])

    liq = leer(client, h, generar(client, h)["generadas"][0]["id"])
    pinta(liq, "S19 el papel")
    _, fot = fotos_del_comprobante(db_session, liq["id"])
    t = cuadre(liq, fot, "S19")
    cuadre_por_renglon(db_session, liq, "S19")
    assert t == D(avance["valor_transporte"]), (
        f"el avance decia ${D(avance['valor_transporte'])} y el papel dice ${t}")


# ===========================================================================
# S20. LA PUERTA DEL PRECIO DEL RENGLÓN: en un comprobante de flete no se
#      puede escribir a mano el precio de un día (menos el de un día fijo).
# ===========================================================================
def test_zzaudit_no_se_escribe_a_mano_el_precio_de_un_renglon_de_flete(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_id = generar(client, h)["generadas"][0]["id"]
    liq = leer(client, h, liq_id)
    detalle = liq["detalles"][0]
    r = client.put(f"{LIQ}/{liq_id}/detalles/{detalle['id']}",
                   json={"precio_litro": "999.99"}, headers=h)
    print(f"\n    PUT precio del renglon de flete -> {r.status_code} {r.text[:160]}")
    assert r.status_code >= 400, "se dejo escribir a mano el precio de un dia de flete"
    liq = leer(client, h, liq_id)
    assert D(liq["valor_transporte"]) == FIJO


# ===========================================================================
# S21. MULTIEMPRESA: el viaje de una quesera no puede decidir el de la otra.
# ===========================================================================
def test_zzaudit_el_viaje_de_una_quesera_no_toca_la_otra(client, base_datos, db_session):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    esc_a = escenario(client, ha)
    esc_b = escenario(client, hb)

    recibir(client, ha, esc_a, DIA_1, "Aurelio", "44.23")
    recibir(client, hb, esc_b, DIA_1, "Aurelio", "137.45")
    liq_a = leer(client, ha, generar(client, ha)["generadas"][0]["id"])
    liq_b = leer(client, hb, generar(client, hb)["generadas"][0]["id"])
    pinta(liq_a, "S21 quesera A")
    pinta(liq_b, "S21 quesera B")
    _, fa = fotos_del_comprobante(db_session, liq_a["id"])
    _, fb = fotos_del_comprobante(db_session, liq_b["id"])
    assert cuadre(liq_a, fa, "S21 A") == FIJO
    assert cuadre(liq_b, fb, "S21 B") == FIJO
    r = client.get(f"{LIQ}/{liq_b['id']}", headers=ha)
    print(f"    la quesera A leyendo el comprobante de B -> {r.status_code}")
    assert r.status_code in (403, 404)
