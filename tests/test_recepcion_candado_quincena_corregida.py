"""EL DÍA DE UNA QUINCENA CORREGIDA DICE LO MISMO QUE SU ANTICIPO, Y NO INVENTA UN PAGO.

100 L × $1.800 = $180.000 cubiertos EXACTO por un adelanto de $180.000: Pagar la cierra
'pagada' sin renglón (pagado $0). Se le mete un día olvidado de 20 L = $36.000 con
Corregir: 'parcial' v2, pagado $0, pagos = [], saldo $36.000. Con calculadora: por estas
cifras no se le ha abonado un peso; se le deben $36.000.

Sus días decían "ya se le abonó" y los dos 422 "ya tiene un pago registrado… Elimine
primero ese pago", y ese pago no existe; el anticipo de la misma quincena decía "ya
emitió un comprobante corregido". Ahora el aviso y los dos 422 salen de UNA función
(`recepcion/service.py::_por_que_esta_trabada`), en el orden del candado del anticipo.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _leer

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"

CORREGIDA = "ya emitió un comprobante corregido"
# Lo que solo se puede decir si hay pagos de verdad.
DE_PAGO = ("abonó", "pago registrado", "Elimine primero ese pago", "ya se pagó",
           "ya se le pagó")


def D(v):
    return Decimal(str(v))


def _proveedor(client, h, nombre):
    r = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                              "precio_litro": "1800"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _dia(client, h, prov, fecha, litros):
    r = client.post(REC, json={"fecha": fecha, "proveedor_id": prov,
                               "cantidad_litros": litros}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _celda(client, h, prov, fecha):
    r = client.get(f"{REC}/grilla/quincena",
                   params={"desde": "2026-06-01", "hasta": "2026-06-15"}, headers=h)
    assert r.status_code == 200, r.text
    fila = next(f for f in r.json()["filas"] if f["proveedor_id"] == prov)
    return fila["celdas"][fecha]


def _corregida_sin_pagos(client, h, nombre):
    prov = _proveedor(client, h, nombre)
    dia = _dia(client, h, prov, "2026-06-02", "100")
    ant = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                 "fecha": "2026-06-01", "valor": "180000"}, headers=h)
    assert ant.status_code == 201, ant.text
    g = client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                            "periodo_fin": "2026-06-15",
                                            "tipo": "proveedor"}, headers=h)
    assert g.status_code == 200, g.text
    liq = next(x for x in g.json()["generadas"] if x["proveedor_id"] == prov)["id"]
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    olvidado = _dia(client, h, prov, "2026-06-05", "20")
    r = client.post(f"{API}/{liq}/corregir", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}, headers=h)
    assert r.status_code == 200, r.text
    hoy = _leer(client, h, liq)
    assert (hoy["estado"], hoy["version"], D(hoy["pagado"]), hoy["pagos"],
            D(hoy["saldo"])) == ("parcial", 2, D("0"), [], D("36000"))
    return prov, dia, ant.json()["id"], liq


def _superficies(client, h, prov, dia, ant):
    """El diálogo del día, la celda, los dos 422 del día y el candado del anticipo."""
    dialogo = client.get(f"{REC}/{dia}", headers=h).json()
    celda = _celda(client, h, prov, "2026-06-02")
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    delete = client.delete(f"{REC}/{dia}", headers=h)
    assert put.status_code == 422 and delete.status_code == 422, (put.text, delete.text)
    assert celda["candado_aviso"] == dialogo["candado_aviso"]
    return {
        "dialogo": dialogo["candado_aviso"],
        "put": _detalle(put),
        "delete": _detalle(delete),
        "anticipo": client.get(f"{ANT}/{ant}", headers=h).json()["candado_aviso"],
    }


def test_la_corregida_sin_pagos_dice_el_comprobante_corregido_en_el_dia_y_en_el_anticipo(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, dia, ant, liq = _corregida_sin_pagos(client, h, "Corregida Sin Abono")
    textos = _superficies(client, h, prov, dia, ant)
    for donde, texto in textos.items():
        print(f"\n  {donde}: {texto}")
        # La MISMA razón en las cuatro superficies: la del anticipo.
        assert CORREGIDA in texto, donde
        for frase in DE_PAGO:
            assert frase not in texto, (donde, frase)
    assert textos["put"].startswith(
        "No se puede cambiar los litros de este día: la quincena de la leche de este día "
        f"{CORREGIDA}.")
    assert textos["delete"].startswith(
        f"No se puede eliminar este día: la quincena de la leche de este día {CORREGIDA}.")
    # Nada se movió.
    assert D(_leer(client, h, liq)["saldo"]) == D("36000")


def test_la_salida_que_nombra_el_dia_existe(client, base_datos, db_session):
    """El 422 manda a 'Corregir esta quincena' SOLO para el precio (Corregir no cambia
    litros). Se sigue: corregir el precio de ese día se deja previsualizar."""
    h = auth_headers(client, "admin.a")
    prov, dia, _, liq = _corregida_sin_pagos(client, h, "Salida Del Dia")
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    assert "Si lo que está mal es el precio del día, use 'Corregir esta quincena'" \
        in _detalle(put)
    detalle = next(d for d in _leer(client, h, liq)["detalles"] if d["fecha"] == "2026-06-02")
    prev = client.post(f"{API}/{liq}/corregir/previsualizar", json={
        "motivo": "precio mal digitado",
        "precios": [{"detalle_id": detalle["id"], "precio_litro": "1700"}]}, headers=h)
    assert prev.status_code == 200, prev.text


def test_la_corregida_con_un_abono_nombra_el_comprobante_y_no_manda_a_borrar_el_pago(
        client, base_datos, db_session):
    """La misma corregida con un abono de $10.000. Borrar ese pago NO destraba el día (la
    versión sigue en 2), así que "Elimine primero ese pago" sería un consejo que siempre
    falla: se nombra el comprobante corregido, igual que en su anticipo. Y se comprueba
    siguiéndolo: sin el pago, el día sigue trabado con la misma razón."""
    h = auth_headers(client, "admin.a")
    prov, dia, ant, liq = _corregida_sin_pagos(client, h, "Corregida Con Abono")
    r = client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20", "valor": "10000"},
                    headers=h)
    assert r.status_code == 200, r.text
    con_pago = _superficies(client, h, prov, dia, ant)
    for donde, texto in con_pago.items():
        print(f"\n  {donde}: {texto}")
        assert CORREGIDA in texto, donde
        assert "Elimine primero ese pago" not in texto, donde

    (pago,) = _leer(client, h, liq)["pagos"]
    assert client.delete(f"{API}/{liq}/pagos/{pago['id']}", headers=h).status_code == 200
    sin_pago = _superficies(client, h, prov, dia, ant)
    assert sin_pago == con_pago


def test_el_abono_de_verdad_sigue_diciendo_el_abono(client, base_datos, db_session):
    """Control: una v1 con un abono real sí dice "ya se le abonó" y manda a borrar ese
    pago, que existe (borrarlo sí destraba el día)."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Abono De Verdad")
    dia = _dia(client, h, prov, "2026-06-02", "100")
    g = client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                            "periodo_fin": "2026-06-15",
                                            "tipo": "proveedor"}, headers=h).json()
    liq = next(x for x in g["generadas"] if x["proveedor_id"] == prov)["id"]
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20",
                                                   "valor": "30000"},
                       headers=h).status_code == 200
    aviso = client.get(f"{REC}/{dia}", headers=h).json()["candado_aviso"]
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    delete = client.delete(f"{REC}/{dia}", headers=h)
    assert "ya se le abonó" in aviso
    for r in (put, delete):
        assert r.status_code == 422
        assert "ya tiene un pago registrado" in _detalle(r)
        assert "Elimine primero ese pago" in _detalle(r)
        assert CORREGIDA not in _detalle(r)
