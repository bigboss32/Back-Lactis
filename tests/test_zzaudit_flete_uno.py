"""AUDITORÍA DEL FLETE — sondas 1 a 6: cuadre, idempotencia y las correcciones
del día a día (litros, apagar/prender, borrar, mover de ruta y de transportador)
con el comprobante en borrador y aprobado.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_zzaudit_flete_transportador import (
    D,
    DIA_1,
    DIA_2,
    DIA_3,
    FIJO,
    LIQ,
    NAPOLES,
    RECEPCIONES,
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


# ===========================================================================
# S1. BASE: dos rutas, dos modos, tres días. Cuadre y RE-GENERAR/RECALCULAR
#     varias veces tiene que ser idempotente.
# ===========================================================================
def test_zzaudit_base_cuadre_e_idempotencia(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    nap = esc["napoles"]["id"]

    recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    recibir(client, h, esc, DIA_1, "Henri", "61.07", ruta_id=nap)
    recibir(client, h, esc, DIA_2, "Gilberto", "82.48")
    recibir(client, h, esc, DIA_3, "Ramiro", "96.31", ruta_id=nap)

    liq_id = generar(client, h)["generadas"][0]["id"]
    liq = leer(client, h, liq_id)
    pinta(liq, "S1 recien generado")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    total0 = cuadre(liq, fot, "S1 generado")

    a_mano = FIJO + cent(D("61.07") * NAPOLES) + FIJO + cent(D("96.31") * NAPOLES)
    print(f"    a mano: {FIJO} + {cent(D('61.07') * NAPOLES)} + {FIJO} + "
          f"{cent(D('96.31') * NAPOLES)} = {a_mano}")
    assert total0 == a_mano, f"total ${total0} vs a mano ${a_mano}"

    for i in range(3):
        _post(client, h, f"{LIQ}/{liq_id}/recalcular", {})
        liq = leer(client, h, liq_id)
        _, fot = fotos_del_comprobante(db_session, liq_id)
        t = cuadre(liq, fot, f"S1 recalculo {i + 1}")
        assert t == total0, f"recalculo {i + 1} movio el total: ${total0} -> ${t}"

    otra = generar(client, h)
    print(f"    segunda corrida de Generar: generadas={len(otra['generadas'])} "
          f"omitidas={[o['motivo_codigo'] for o in otra.get('omitidas', [])]}")
    assert not otra["generadas"], f"generar dos veces creo otro comprobante: {otra}"


# ===========================================================================
# S2. CORREGIR LITROS con el comprobante APROBADO. El día fijo no se mueve.
# ===========================================================================
def test_zzaudit_corregir_litros_con_papel_aprobado(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    m = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    liq = leer(client, h, liq_id)
    pinta(liq, "S2 aprobado")
    assert D(liq["valor_transporte"]) == FIJO

    put_recepcion(client, h, a["id"], {"cantidad_litros": "91.30"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S2 tras corregir 44,23 -> 91,30 L")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S2")
    print(f"    fotos: Aurelio ${foto(db_session, a['id'])} + "
          f"Marleny ${foto(db_session, m['id'])} = ${fot}")
    assert t == FIJO, f"el viaje fijo paso de ${FIJO} a ${t} por corregir litros"
    assert liq["estado"] == "borrador"


# ===========================================================================
# S3. APAGAR y volver a PRENDER un día del viaje fijo.
# ===========================================================================
def test_zzaudit_apagar_y_prender(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})

    put_recepcion(client, h, a["id"], {"estado": "inactivo"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S3 con Aurelio apagado")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S3 apagado")
    print(f"    foto de la apagada: ${foto(db_session, a['id'])}")
    assert t == FIJO, f"apagando un dia el viaje paso a ${t}"
    assert foto(db_session, a["id"]) == 0

    put_recepcion(client, h, a["id"], {"estado": "activo"})
    liq = leer(client, h, liq_id)
    pinta(liq, "S3 con Aurelio prendido otra vez")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S3 prendido")
    assert t == FIJO, f"prender otra vez dejo el viaje en ${t}"


# ===========================================================================
# S4. BORRAR una recepción del viaje fijo (papel en borrador).
# ===========================================================================
def test_zzaudit_borrar_una_recepcion_del_viaje(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]

    r = client.delete(f"{RECEPCIONES}/{a['id']}", headers=h)
    assert r.status_code in (200, 204), r.text
    liq = leer(client, h, liq_id)
    pinta(liq, "S4 tras borrar a Aurelio")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    t = cuadre(liq, fot, "S4")
    assert t == FIJO, f"borrando un proveedor el viaje paso a ${t}"


# ===========================================================================
# S5. MOVER DE RUTA (fija -> por litro) y devolverlo.
# ===========================================================================
def test_zzaudit_mover_de_ruta_ida_y_vuelta(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    fab, nap = esc["fabrica"]["id"], esc["napoles"]["id"]
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    antes = D(leer(client, h, liq_id)["valor_transporte"])

    put_recepcion(client, h, a["id"], {"ruta_id": nap})
    liq = leer(client, h, liq_id)
    pinta(liq, "S5 Aurelio movido a Napoles (por litro)")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    ida = cuadre(liq, fot, "S5 ida")
    esperado = FIJO + cent(D("44.23") * NAPOLES)
    print(f"    a mano: {FIJO} + {cent(D('44.23') * NAPOLES)} = {esperado}")

    put_recepcion(client, h, a["id"], {"ruta_id": fab})
    liq = leer(client, h, liq_id)
    pinta(liq, "S5 Aurelio devuelto a A fabrica")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    vuelta = cuadre(liq, fot, "S5 vuelta")
    print(f"    antes ${antes} - ida ${ida} - vuelta ${vuelta}")
    assert vuelta == antes, (
        f"deshacer la correccion tenia que dejar el papel en ${antes} y quedo en "
        f"${vuelta}")


# ===========================================================================
# S6. MOVER DE TRANSPORTADOR y devolverlo.
# ===========================================================================
def test_zzaudit_mover_de_transportador_ida_y_vuelta(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    m = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    liq_id = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_id}/aprobar", {})
    antes = D(leer(client, h, liq_id)["valor_transporte"])

    put_recepcion(client, h, a["id"], {"transportador_id": esc["beto"]["id"]})
    liq = leer(client, h, liq_id)
    pinta(liq, "S6 Aurelio pasado a Beto")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    cuadre(liq, fot, "S6 ida")
    print(f"    foto de Aurelio con Beto: ${foto(db_session, a['id'])} - a mano "
          f"${cent(D('44.23') * D('310.15'))}")

    put_recepcion(client, h, a["id"], {"transportador_id": esc["alex"]["id"]})
    liq = leer(client, h, liq_id)
    pinta(liq, "S6 Aurelio devuelto a Alex")
    _, fot = fotos_del_comprobante(db_session, liq_id)
    vuelta = cuadre(liq, fot, "S6 vuelta")
    print(f"    fotos: Aurelio ${foto(db_session, a['id'])} + "
          f"Marleny ${foto(db_session, m['id'])} = ${fot}")
    assert vuelta == antes, (
        f"devolver el transportador tenia que dejar el papel en ${antes} y quedo "
        f"en ${vuelta}")
    assert foto(db_session, a["id"]) > 0, (
        "Aurelio volvio al mismo viaje con leche viva y su flete quedo en $0")
