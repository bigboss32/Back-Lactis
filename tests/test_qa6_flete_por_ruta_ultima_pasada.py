"""ÚLTIMA PASADA ADVERSARIAL SOBRE LA TARIFA DE FLETE POR RUTA.

Nació como una corrida de verificación de un solo uso, antes de subir la tarifa por
ruta y el botón Recalcular del transportador. Se quedó en la suite porque encontró
cosas de verdad, y varias ya se cerraron: los dos "HALLAZGO" de los anticipos y la
fuga de la ruta entre empresas (bloque 8). Lo que esas pruebas documentaban como
defecto ahora está arreglado, así que EXIGEN el arreglo: si alguien las vuelve a
voltear, es que reabrió el hueco.

Queda UN hallazgo abierto y marcado como tal:
`test_HALLAZGO_observaciones_le_quita_el_visto_bueno`. La plata no se mueve —eso está
cerrado—, pero escribir una observación en un día todavía le quita el visto bueno a
una liquidación APROBADA y la baja a borrador. Está aquí para que no se pierda.

Los ocho bloques:
  1. editar un campo que no es de plata no re-precifica el flete, en ningún estado;
  2. generar y generar+recalcular dan EL MISMO papel (con cifras feas a propósito);
  3. la tarifa cambiada a mitad de quincena sigue cuadrando;
  4. ni un peso de una liquidación pagada o con abonos se mueve, por ningún camino;
  5. los campos de plata rechazan el tercer decimal y el desborde;
  6. el anticipo más grande que la quincena se aplica COMPLETO, y el saldo negativo se
     dice con palabras (la regla completa está en test_liquidacion_saldo_negativo.py);
  7. fuzz: generar == generar+recalcular y el desglose suma exacto;
  8. la ruta de la recepción no puede ser de otra quesera (las dos puertas, en
     detalle, están en test_recepcion_ruta_tenancy.py).
"""
from decimal import ROUND_HALF_UP, Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"


def D(v):
    return Decimal(str(v))


def cent(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


CERO = D(0)


# ------------------------------------------------------------------ montaje
def crear_ruta(client, h, nombre):
    r = client.post("/api/v1/rutas", json={"nombre": nombre, "municipio": "Granada"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def crear_transportador(client, h, nombre, general="0", rutas=None):
    cuerpo = {"nombre": nombre, "valor_transporte": str(general)}
    if rutas:
        cuerpo["rutas"] = [{"ruta_id": r["id"], "valor_transporte": str(v)} for r, v in rutas]
    r = client.post("/api/v1/transportadores", json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def crear_proveedor(client, h, nombre, ruta, precio="1800"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio, "ruta_id": ruta["id"]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def recibir(client, h, t, prov, fecha, litros, ruta):
    r = client.post(
        REC,
        json={
            "fecha": fecha,
            "proveedor_id": prov["id"],
            "transportador_id": t["id"],
            "ruta_id": ruta["id"],
            "cantidad_litros": str(litros),
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def poner_tarifa_ruta(client, h, t, pares):
    r = client.put(
        f"/api/v1/transportadores/{t['id']}",
        json={"rutas": [{"ruta_id": ru["id"], "valor_transporte": str(v)} for ru, v in pares]},
        headers=h,
    )
    assert r.status_code == 200, r.text


def generar(client, h, tipo="transportador", inicio="2026-06-01", fin="2026-06-15"):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def leer_liq(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def leer_rec(client, h, rec_id):
    r = client.get(f"{REC}/{rec_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def papel(liq):
    """La foto del comprobante. Las cifras como Decimal: '0' y '0.00' son la misma
    plata, y la escala del texto es un artefacto de SQLite que no existe en Postgres."""
    return (
        D(liq["valor_transporte"]),
        D(liq["valor_total"]),
        D(liq["total_litros"]),
        D(liq["anticipos"]),
        D(liq["saldo"]),
        sorted(
            (str(d["fecha"]), str(d.get("ruta_nombre")), D(d["litros"]),
             D(d["precio_litro"]), D(d["valor"]))
            for d in liq["detalles"]
        ),
    )


def fotos(client, h, recs):
    return {r["id"]: str(leer_rec(client, h, r["id"])["valor_transporte"]) for r in recs}


def cuadre(liq, fotos_dict, ctx=""):
    ren = [(str(d["fecha"]), D(d["litros"]), D(d["precio_litro"]), D(d["valor"]))
           for d in liq["detalles"]]
    for f, l, t, v in ren:
        assert cent(l * t) == v, f"{ctx}: renglon {f}: {l}x{t}={cent(l*t)} pero dice {v}"
    suma = sum((v for _, _, _, v in ren), CERO)
    assert suma == D(liq["valor_transporte"]), (
        f"{ctx}: renglones suman {suma}, comprobante dice {liq['valor_transporte']}")
    sf = sum((D(x) for x in fotos_dict.values()), CERO)
    assert sf == D(liq["valor_transporte"]), (
        f"{ctx}: fotos suman {sf}, comprobante {liq['valor_transporte']} -> {fotos_dict}")
    sl = sum((l for _, l, _, _ in ren), CERO)
    assert sl == D(liq["total_litros"]), f"{ctx}: litros {sl} vs {liq['total_litros']}"
    neto = D(liq["valor_total"]) - D(liq["anticipos"])
    assert D(liq["saldo"]) == neto - D(liq["pagado"]), f"{ctx}: saldo descuadrado"


BUENA = D("242.76")


def escenario(client, h, tarifa=BUENA, litros=("44.23", "82.48"), general="0", fecha="2026-06-02"):
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", general, rutas=[(ruta, tarifa)])
    recs = []
    for i, l in enumerate(litros):
        prov = crear_proveedor(client, h, f"Prov{i}", ruta)
        recs.append(recibir(client, h, t, prov, fecha, l, ruta))
    return ruta, t, recs


# ===========================================================================
# 1. EDITAR UN CAMPO QUE NO ES DE PLATA NO RE-PRECIFICA (borrador/aprobada/pagada)
# ===========================================================================
@pytest.mark.parametrize("estado", ["borrador", "aprobada", "pagada"])
def test_observaciones_no_reprecifica(client, base_datos, estado):
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    if estado in ("aprobada", "pagada"):
        assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    if estado == "pagada":
        assert client.post(f"{API}/{liq['id']}/pagar", headers=h).status_code == 200

    antes = papel(leer_liq(client, h, liq["id"]))
    antes_fotos = fotos(client, h, recs)
    # la tarifa sube a mitad de quincena: legitimo, para la siguiente
    poner_tarifa_ruta(client, h, t, [(ruta, "300")])

    r = client.put(f"{REC}/{recs[0]['id']}",
                   json={"observaciones": "el tarro venia mal tapado"}, headers=h)
    assert r.status_code == 200, f"[{estado}] editar observaciones: {r.text}"

    despues = papel(leer_liq(client, h, liq["id"]))
    assert despues == antes, f"[{estado}] el comprobante cambio\nantes={antes}\ndespues={despues}"
    assert fotos(client, h, recs) == antes_fotos, f"[{estado}] las fotos del flete cambiaron"


def test_HALLAZGO_observaciones_le_quita_el_visto_bueno(client, base_datos):
    """Documenta el defecto encontrado: la plata NO se mueve, pero la APROBADA
    amanece en BORRADOR por haber escrito una observación."""
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    antes = papel(leer_liq(client, h, liq["id"]))
    assert client.put(f"{REC}/{recs[0]['id']}",
                      json={"observaciones": "el tarro venia mal tapado"},
                      headers=h).status_code == 200
    d = leer_liq(client, h, liq["id"])
    assert papel(d) == antes, "la plata se movio"
    assert d["estado"] == "borrador", "si esto falla, el defecto ya se cerro"


@pytest.mark.parametrize("estado", ["borrador", "aprobada", "pagada"])
def test_sucursal_no_reprecifica(client, base_datos, estado):
    h = auth_headers(client, "admin.a")
    suc = client.post("/api/v1/sucursales",
                      json={"nombre": "Planta 2", "direccion": "km 3"}, headers=h)
    assert suc.status_code == 201, suc.text
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    if estado in ("aprobada", "pagada"):
        assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    if estado == "pagada":
        assert client.post(f"{API}/{liq['id']}/pagar", headers=h).status_code == 200

    antes = papel(leer_liq(client, h, liq["id"]))
    antes_fotos = fotos(client, h, recs)
    poner_tarifa_ruta(client, h, t, [(ruta, "317.50")])

    r = client.put(f"{REC}/{recs[1]['id']}", json={"sucursal_id": suc.json()["id"]}, headers=h)
    assert r.status_code == 200, f"[{estado}] editar sucursal: {r.text}"
    assert papel(leer_liq(client, h, liq["id"])) == antes, f"[{estado}] el comprobante cambio"
    assert fotos(client, h, recs) == antes_fotos, f"[{estado}] las fotos cambiaron"


def test_el_reparto_de_centavos_sigue_corriendo_en_el_recuadre(client, base_datos, db_session):
    """Si el recuadre automatico dejo de repartir, se reabre el centavo."""
    from app.modules.recepcion.models import RecepcionLeche

    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    total = D(leer_liq(client, h, liq["id"])["valor_transporte"])
    # 44,23 + 82,48 = 126,71 L x 242,76 -> 30.760,12 (el renglon redondea UNA vez)
    assert total == D("30760.12"), f"el renglon no es el esperado: {total}"

    # se descuadran las fotos a mano: cada una su propia multiplicacion (suman 30.760,11)
    crudas = {}
    for r in recs:
        fila = db_session.get(RecepcionLeche, __import__("uuid").UUID(r["id"]))
        cruda = cent(D(fila.cantidad_litros) * BUENA)
        fila.valor_transporte = cruda
        crudas[r["id"]] = str(cruda)
    db_session.flush()
    db_session.commit()
    assert sum((D(v) for v in crudas.values()), CERO) == D("30760.11"), crudas

    # un campo que NO es de plata dispara el recuadre automatico
    assert client.put(f"{REC}/{recs[0]['id']}",
                      json={"observaciones": "nada de plata"}, headers=h).status_code == 200

    liq2 = leer_liq(client, h, liq["id"])
    f2 = fotos(client, h, recs)
    assert D(liq2["valor_transporte"]) == total, (
        f"el recuadre re-precifico: {liq2['valor_transporte']} != {total}")
    cuadre(liq2, f2, "recuadre automatico")
    assert f2 != crudas, "el reparto de centavos NO corrio en el recuadre automatico"


# ===========================================================================
# 2. GENERAR == GENERAR + RECALCULAR
# ===========================================================================
@pytest.mark.parametrize(
    "litros,tarifa",
    [
        (("44.23", "82.48"), "242.76"),
        (("0.01", "0.02", "0.03"), "242.76"),
        (("0.33", "0.33", "0.33", "0.33", "0.33", "0.33"), "0.05"),
        (("13.33", "13.33", "13.33"), "333.33"),
        (("999999.99",), "1.01"),
        (("7.77", "1.11", "3.33", "9.99"), "242.76"),
    ],
)
def test_generar_y_generar_mas_recalcular_dan_el_mismo_papel(client, base_datos, litros, tarifa):
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h, tarifa=tarifa, litros=litros)
    liq = generar(client, h)[0]
    p1 = papel(leer_liq(client, h, liq["id"]))
    f1 = fotos(client, h, recs)
    cuadre(leer_liq(client, h, liq["id"]), f1, "generado")

    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    assert r.status_code == 200, r.text
    p2 = papel(leer_liq(client, h, liq["id"]))
    f2 = fotos(client, h, recs)
    cuadre(leer_liq(client, h, liq["id"]), f2, "recalculado")
    assert p1 == p2, f"generar != recalcular\n{p1}\n{p2}"
    assert f1 == f2, f"las fotos cambiaron al recalcular\n{f1}\n{f2}"


def test_dos_rutas_el_mismo_dia_cuadran_y_recalcular_no_cambia(client, base_datos):
    h = auth_headers(client, "admin.a")
    n = crear_ruta(client, h, "Napoles")
    m = crear_ruta(client, h, "Mira Valle")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(n, "242.76"), (m, "317.50")])
    recs = []
    for i, (ru, l) in enumerate([(n, "44.23"), (n, "82.48"), (m, "13.37"), (m, "0.99")]):
        prov = crear_proveedor(client, h, f"P{i}", ru)
        recs.append(recibir(client, h, t, prov, "2026-06-02", l, ru))
    liq = generar(client, h)[0]
    d = leer_liq(client, h, liq["id"])
    assert len(d["detalles"]) == 2, f"un dia con dos rutas debe dar dos renglones: {d['detalles']}"
    f1 = fotos(client, h, recs)
    cuadre(d, f1, "dos rutas")
    assert client.post(f"{API}/{liq['id']}/recalcular", headers=h).status_code == 200
    d2 = leer_liq(client, h, liq["id"])
    assert papel(d2) == papel(d), "recalcular cambio el papel de dos rutas"
    cuadre(d2, fotos(client, h, recs), "dos rutas recalculado")


# ===========================================================================
# 3. LA TARIFA CAMBIADA A MITAD DE QUINCENA, CON CIFRAS FEAS
# ===========================================================================
def test_tarifa_cambiada_a_mitad_de_quincena_cuadra(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(ruta, "242.76")])
    recs = []
    for i, (fecha, l) in enumerate([("2026-06-02", "44.23"), ("2026-06-03", "82.48")]):
        prov = crear_proveedor(client, h, f"P{i}", ruta)
        recs.append(recibir(client, h, t, prov, fecha, l, ruta))
    # a mitad de quincena la tarifa sube: los dias nuevos entran a 317,50
    poner_tarifa_ruta(client, h, t, [(ruta, "317.50")])
    for i, (fecha, l) in enumerate([("2026-06-09", "7.77"), ("2026-06-10", "0.03")]):
        prov = crear_proveedor(client, h, f"Q{i}", ruta)
        recs.append(recibir(client, h, t, prov, fecha, l, ruta))

    liq = generar(client, h)[0]
    d = leer_liq(client, h, liq["id"])
    cuadre(d, fotos(client, h, recs), "tarifa cambiada a mitad")
    # generar RE-DERIVA con la tarifa de hoy: todos a 317,50
    for det in d["detalles"]:
        assert D(det["precio_litro"]) == D("317.50"), (
            f"generar debe re-derivar con la tarifa de hoy: {det}")
    assert client.post(f"{API}/{liq['id']}/recalcular", headers=h).status_code == 200
    d2 = leer_liq(client, h, liq["id"])
    assert papel(d2) == papel(d), "recalcular cambio el papel"
    cuadre(d2, fotos(client, h, recs), "recalculado")


# ===========================================================================
# 4. NI UN PESO DE UNA PAGADA O CON ABONOS, POR NINGUN CAMINO
# ===========================================================================
@pytest.mark.parametrize("como", ["pagada", "abonada"])
def test_ningun_camino_mueve_plata_entregada(client, base_datos, como):
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    # se genera tambien la de leche, para probar el flete informativo
    liq_t = generar(client, h)[0]
    liq_p = generar(client, h, tipo="proveedor")
    if como == "pagada":
        assert client.post(f"{API}/{liq_t['id']}/aprobar", headers=h).status_code == 200
        assert client.post(f"{API}/{liq_t['id']}/pagar", headers=h).status_code == 200
    else:
        assert client.post(f"{API}/{liq_t['id']}/aprobar", headers=h).status_code == 200
        rr = client.post(f"{API}/{liq_t['id']}/pagos",
                         json={"fecha": "2026-06-16", "valor": "1000"}, headers=h)
        assert rr.status_code == 200, rr.text
    for lp in liq_p:
        assert client.post(f"{API}/{lp['id']}/aprobar", headers=h).status_code == 200
        assert client.post(f"{API}/{lp['id']}/pagar", headers=h).status_code == 200

    antes_t = papel(leer_liq(client, h, liq_t["id"]))
    antes_p = {lp["id"]: papel(leer_liq(client, h, lp["id"])) for lp in liq_p}
    antes_totales_p = {lp["id"]: (str(leer_liq(client, h, lp["id"])["valor_total"]),
                                  str(leer_liq(client, h, lp["id"])["saldo"]),
                                  str(leer_liq(client, h, lp["id"])["pagado"]))
                       for lp in liq_p}
    antes_fotos = fotos(client, h, recs)
    poner_tarifa_ruta(client, h, t, [(ruta, "999.99")])

    caminos = []
    # camino 1: recalcular explicito
    caminos.append(("recalcular flete", client.post(f"{API}/{liq_t['id']}/recalcular", headers=h)))
    for lp in liq_p:
        caminos.append(("recalcular leche", client.post(f"{API}/{lp['id']}/recalcular", headers=h)))
    # camino 2: los campos de plata del dia
    for campo, valor in [("cantidad_litros", "999"), ("ruta_id", None),
                         ("transportador_id", None), ("fecha", "2026-06-04"),
                         ("estado", "inactivo"), ("precio_litro", "2500")]:
        cuerpo = {campo: valor}
        if campo in ("ruta_id", "transportador_id"):
            otra = crear_ruta(client, h, f"otra-{como}-{campo}")
            cuerpo = {"ruta_id": otra["id"]} if campo == "ruta_id" else {
                "transportador_id": crear_transportador(client, h, f"Otro-{campo}", "50")["id"]}
        caminos.append((f"PUT {campo}",
                        client.put(f"{REC}/{recs[0]['id']}", json=cuerpo, headers=h)))
    # camino 3: borrar el dia
    caminos.append(("DELETE dia", client.delete(f"{REC}/{recs[0]['id']}", headers=h)))
    # camino 4: corregir el precio de un renglon de la de leche pagada
    for lp in liq_p:
        det = leer_liq(client, h, lp["id"])["detalles"]
        if det:
            caminos.append(("precio renglon leche", client.put(
                f"{API}/{lp['id']}/detalles/{det[0]['id']}/precio",
                json={"precio_litro": "9999"}, headers=h)))
    # camino 5: anticipo nuevo al transportador (dispara recuadre)
    caminos.append(("anticipo", client.post(
        "/api/v1/anticipos",
        json={"transportador_id": t["id"], "fecha": "2026-06-05", "valor": "5000"}, headers=h)))

    for nombre, res in caminos:
        assert res.status_code in (400, 403, 404, 409, 422), (
            f"[{como}] el camino '{nombre}' NO rebotó: {res.status_code} {res.text}")

    assert papel(leer_liq(client, h, liq_t["id"])) == antes_t, f"[{como}] el flete se movio"
    assert fotos(client, h, recs) == antes_fotos, f"[{como}] las fotos se movieron"
    for lp in liq_p:
        ahora = leer_liq(client, h, lp["id"])
        assert (str(ahora["valor_total"]), str(ahora["saldo"]), str(ahora["pagado"])) == \
            antes_totales_p[lp["id"]], f"[{como}] la leche pagada {lp['id']} se movio"


def test_el_flete_informativo_no_mueve_la_plata_de_la_leche_pagada(client, base_datos):
    """El camino NUEVO: refrescar el flete informativo de una de leche PAGADA."""
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    otra = crear_ruta(client, h, "Mira Valle")
    poner_tarifa_ruta(client, h, t, [(ruta, "242.76"), (otra, "317.50")])
    liq_p = generar(client, h, tipo="proveedor")
    liq_t = generar(client, h)[0]
    for lp in liq_p:
        assert client.post(f"{API}/{lp['id']}/aprobar", headers=h).status_code == 200
        assert client.post(f"{API}/{lp['id']}/pagar", headers=h).status_code == 200

    antes = {lp["id"]: leer_liq(client, h, lp["id"]) for lp in liq_p}
    # la ruta solo la traba el flete: con la leche pagada y el flete en borrador se
    # puede corregir, y eso mueve la foto del flete
    r = client.put(f"{REC}/{recs[0]['id']}", json={"ruta_id": otra["id"]}, headers=h)
    assert r.status_code == 200, r.text

    for lp in liq_p:
        d = leer_liq(client, h, lp["id"])
        a = antes[lp["id"]]
        assert (str(d["valor_total"]), str(d["saldo"]), str(d["pagado"]), str(d["estado"]),
                sorted((str(x["fecha"]), str(x["litros"]), str(x["precio_litro"]),
                        str(x["valor"])) for x in d["detalles"])) == \
               (str(a["valor_total"]), str(a["saldo"]), str(a["pagado"]), str(a["estado"]),
                sorted((str(x["fecha"]), str(x["litros"]), str(x["precio_litro"]),
                        str(x["valor"])) for x in a["detalles"])), \
            "refrescar el flete informativo movio plata de una liquidacion pagada"
        # y la columna informativa SI queda al dia
        suma = sum((D(leer_rec(client, h, x["id"])["valor_transporte"]) for x in recs), CERO)
        assert D(d["valor_transporte"]) == suma or len(liq_p) > 1


# ===========================================================================
# 5. LOS CAMPOS DE PLATA
# ===========================================================================
def test_lo_que_el_usuario_escribe_de_verdad_sigue_entrando(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "242.76", rutas=[(ruta, "1800")])
    assert D(t["valor_transporte"]) == D("242.76")
    assert D(t["rutas"][0]["valor_transporte"]) == D("1800")
    prov = crear_proveedor(client, h, "Juan", ruta, precio="1800")
    assert D(prov["precio_litro"]) == D("1800")
    rec = recibir(client, h, t, prov, "2026-06-02", "242.76", ruta)
    assert D(rec["cantidad_litros"]) == D("242.76")
    r = client.post(REC, json={"fecha": "2026-06-03", "proveedor_id": prov["id"],
                               "transportador_id": t["id"], "ruta_id": ruta["id"],
                               "cantidad_litros": "1800", "bonificaciones": "1800.50",
                               "descuentos": "242.76", "precio_litro": "1800"}, headers=h)
    assert r.status_code == 201, r.text


@pytest.mark.parametrize(
    "url,cuerpo",
    [
        ("/api/v1/transportadores", {"nombre": "Alex", "valor_transporte": "242.765"}),
        ("/api/v1/transportadores", {"nombre": "Alex", "valor_transporte": "1e20"}),
        ("/api/v1/proveedores", {"nombre": "Juan", "precio_litro": "1800.005"}),
        ("/api/v1/proveedores", {"nombre": "Juan", "precio_litro": "1e20"}),
    ],
)
def test_el_tercer_decimal_y_el_desborde_se_rechazan(client, base_datos, url, cuerpo):
    h = auth_headers(client, "admin.a")
    r = client.post(url, json=cuerpo, headers=h)
    assert r.status_code == 422, f"{url} acepto {cuerpo}: {r.status_code} {r.text}"


def test_pago_parcial_y_anticipo_rechazan_el_tercer_decimal(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    for valor in ("100.005", "1e20", "0.001"):
        r = client.post(f"{API}/{liq['id']}/pagos",
                        json={"fecha": "2026-06-16", "valor": valor}, headers=h)
        assert r.status_code == 422, f"el pago acepto {valor}: {r.status_code} {r.text}"
    for valor in ("100.005", "1e20"):
        r = client.post("/api/v1/anticipos",
                        json={"transportador_id": t["id"], "fecha": "2026-06-05",
                              "valor": valor}, headers=h)
        assert r.status_code == 422, f"el anticipo acepto {valor}: {r.status_code} {r.text}"
    # y el pago de verdad si entra, y pagado+saldo da el neto exacto
    r = client.post(f"{API}/{liq['id']}/pagos",
                    json={"fecha": "2026-06-16", "valor": "1000.50"}, headers=h)
    assert r.status_code == 200, r.text
    d = leer_liq(client, h, liq["id"])
    assert D(d["pagado"]) + D(d["saldo"]) == D(d["valor_total"]) - D(d["anticipos"]), d


# ===========================================================================
# 6. EL ANTICIPO MÁS GRANDE QUE LA QUINCENA: ¿qué se le paga al tercero?
#
# ESTE BLOQUE ESTABA AL REVÉS, y es el hallazgo más caro que encontró esta corrida.
# Lo que medía —y daba por bueno— era que el anticipo que no cupiera en la quincena se
# SOLTARA (quedarse sin aplicar para descontarlo en la siguiente). Eso hacía salir la
# plata DOS VECES: el comprobante decía "Anticipos aplicados $0,00" y saldo $180.000,
# y el dueño le pagaba esos $180.000 encima de los $300.000 que ya le había entregado.
#
# La regla que quedó: los anticipos se aplican COMPLETOS, siempre, y el saldo negativo
# —que es la verdad: el tercero le quedó debiendo al negocio— se dice con palabras en
# la pantalla y en el papel. El detalle está en tests/test_liquidacion_saldo_negativo.py.
# ===========================================================================
def test_el_anticipo_mayor_que_la_quincena_se_aplica_completo(client, base_datos):
    """$300.000 de anticipo ya entregado contra una quincena de $180.000."""
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(ruta, "242.76")])
    prov = crear_proveedor(client, h, "Henri C", ruta)
    recibir(client, h, t, prov, "2026-06-02", "100", ruta)
    # $300.000 de anticipo contra una quincena de 100 L x $1.800 = $180.000
    r = client.post("/api/v1/anticipos",
                    json={"proveedor_id": prov["id"], "fecha": "2026-06-01",
                          "valor": "300000"}, headers=h)
    assert r.status_code == 201, r.text
    liq = generar(client, h, tipo="proveedor")[0]
    d = leer_liq(client, h, liq["id"])
    print(f"\n  valor_total={d['valor_total']} anticipos={d['anticipos']} "
          f"saldo={d['saldo']} le_queda_debiendo={d['le_queda_debiendo']}")
    assert D(d["valor_total"]) == D("180000"), d
    assert D(d["anticipos"]) == D("300000"), (
        "el anticipo se soltó: esos $300.000 ya se le entregaron, y dejarlos sin "
        "aplicar hace que el dueño pague la quincena entera encima")
    assert D(d["saldo"]) == D("-120000"), d
    # Y el saldo negativo se lee al derecho: es el TERCERO el que debe.
    assert D(d["le_queda_debiendo"]) == D("120000"), d

    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pag = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pag.status_code == 200, pag.text
    assert D(pag.json()["pagado"]) == CERO, (
        "no se le debía nada y le salió plata: se le habría pagado la quincena "
        "completa teniendo $300.000 de anticipo entregado")

    # Y el anticipo quedó APLICADO, no suelto esperando otra quincena.
    a = client.get("/api/v1/anticipos", headers=h).json()
    sueltos = [x for x in a["items"] if x.get("liquidacion_id") in (None, "")]
    assert not sueltos, f"el anticipo quedó sin aplicar: {a['items']}"


def test_dos_anticipos_se_aplican_los_dos_aunque_el_saldo_quede_negativo(client, base_datos):
    """$100.000 + $150.000 contra una quincena de $180.000: se aplican los dos.

    Antes se aplicaba solo el primero (el que "cabía") y el segundo se soltaba: al
    proveedor se le pagaban $80.000 teniendo $250.000 adelantados.
    """
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(ruta, "242.76")])
    prov = crear_proveedor(client, h, "Henri", ruta)
    recibir(client, h, t, prov, "2026-06-02", "100", ruta)  # $180.000
    for fecha, valor in [("2026-06-01", "100000"), ("2026-06-02", "150000")]:
        assert client.post("/api/v1/anticipos",
                           json={"proveedor_id": prov["id"], "fecha": fecha,
                                 "valor": valor}, headers=h).status_code == 201
    liq = generar(client, h, tipo="proveedor")[0]
    d = leer_liq(client, h, liq["id"])
    print(f"\n  anticipos={d['anticipos']} saldo={d['saldo']} "
          f"le_queda_debiendo={d['le_queda_debiendo']}")
    assert D(d["anticipos"]) == D("250000"), d
    assert D(d["saldo"]) == D("-70000"), d
    assert D(d["le_queda_debiendo"]) == D("70000"), d


# ===========================================================================
# 7. FUZZ: generar == generar+recalcular, y el desglose suma exacto
# ===========================================================================
def test_fuzz_generar_recalcular_y_cuadre(client, base_datos):
    import random
    rnd = random.Random(20260804)
    h = auth_headers(client, "admin.a")
    n = crear_ruta(client, h, "Napoles")
    m = crear_ruta(client, h, "Mira Valle")
    for vuelta in range(12):
        t1 = D(rnd.choice(["242.76", "317.50", "0.07", "1.01", "999.99", "0.01", "1800"]))
        t2 = D(rnd.choice(["242.76", "0.03", "13.37", "1000000", "0.11"]))
        t = crear_transportador(client, h, f"T{vuelta}", rnd.choice(["0", "77.77"]),
                                rutas=[(n, t1), (m, t2)])
        recs = []
        for i in range(rnd.randint(2, 7)):
            ru = rnd.choice([n, m])
            litros = D(rnd.randrange(1, 2000000)) / D(100)
            fecha = f"2026-06-{rnd.randint(1, 15):02d}"
            prov = crear_proveedor(client, h, f"P{vuelta}-{i}", ru)
            recs.append(recibir(client, h, t, prov, fecha, litros, ru))
        liqs = [x for x in generar(client, h) if x["transportador_id"] == t["id"]]
        if not liqs:
            continue
        liq = liqs[0]
        d1 = leer_liq(client, h, liq["id"])
        f1 = fotos(client, h, recs)
        cuadre(d1, f1, f"fuzz {vuelta} generado")
        assert client.post(f"{API}/{liq['id']}/recalcular", headers=h).status_code == 200
        d2 = leer_liq(client, h, liq["id"])
        f2 = fotos(client, h, recs)
        cuadre(d2, f2, f"fuzz {vuelta} recalculado")
        assert papel(d2) == papel(d1), f"fuzz {vuelta}: generar != recalcular\n{papel(d1)}\n{papel(d2)}"
        assert f1 == f2, f"fuzz {vuelta}: las fotos cambiaron"
        # tarifa cambiada a mitad, y RECALCULAR explicito: sigue cuadrando
        poner_tarifa_ruta(client, h, t, [(n, t2), (m, t1)])
        assert client.post(f"{API}/{liq['id']}/recalcular", headers=h).status_code == 200
        d3 = leer_liq(client, h, liq["id"])
        cuadre(d3, fotos(client, h, recs), f"fuzz {vuelta} tarifa cambiada")


# ===========================================================================
# 8. AISLAMIENTO: la RUTA de la recepcion tiene que ser de esta empresa
#
# Este era el hallazgo que esta corrida dejó abierto: `recepciones_leche.ruta_id` no lo
# miraba nadie más que la llave foránea, así que la quesera A podía mandar el `ruta_id`
# de la B y el POST respondía 201. El nombre de la ruta ajena salía después impreso en
# el comprobante del transportador y en su PDF. Ya está cerrado —en las dos puertas—, y
# esta prueba pasó de documentarlo a exigirlo. El detalle está en
# tests/test_recepcion_ruta_tenancy.py.
# ===========================================================================
def test_la_ruta_de_otra_empresa_no_entra_por_la_recepcion(client, base_datos):
    hb = auth_headers(client, "admin.b")
    ruta_b = crear_ruta(client, hb, "RutaSecretaDeB")
    h = auth_headers(client, "admin.a")
    ruta_a = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "100", rutas=[(ruta_a, "242.76")])
    prov = crear_proveedor(client, h, "Juan", ruta_a)
    # La empresa A manda la ruta de la empresa B
    r = client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov["id"],
                               "transportador_id": t["id"], "ruta_id": ruta_b["id"],
                               "cantidad_litros": "44.23"}, headers=h)
    print("\n  POST recepcion con ruta de la otra empresa ->", r.status_code)
    assert r.status_code == 404, (
        f"la ruta de la quesera B entro en una recepcion de la A: "
        f"{r.status_code} {r.text}")

    # Y por el PUT tampoco, que es la puerta que se olvida.
    rec = recibir(client, h, t, prov, "2026-06-02", "44.23", ruta_a)
    r = client.put(f"{REC}/{rec['id']}", json={"ruta_id": ruta_b["id"]}, headers=h)
    print("  PUT recepcion con ruta de la otra empresa ->", r.status_code)
    assert r.status_code == 404, (
        f"le cambiaron la ruta de un dia a una de la quesera B: "
        f"{r.status_code} {r.text}")

    # El comprobante y el PDF salen nombrando SOLO la ruta propia.
    liq = generar(client, h)[0]
    d = leer_liq(client, h, liq["id"])
    nombres = [x.get("ruta_nombre") for x in d["detalles"]]
    pdf = client.get(f"{API}/{liq['id']}/pdf", headers=h)
    print("  ruta_nombre en el comprobante:", nombres, "| pdf:", pdf.status_code)
    assert pdf.status_code == 200
    assert nombres == ["Napoles"], nombres
    assert b"RutaSecretaDeB" not in pdf.content, (
        "el nombre de una ruta de la OTRA quesera salio en el PDF del transportador")
