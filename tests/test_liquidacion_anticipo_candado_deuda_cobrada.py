"""EL CANDADO DE LOS ANTICIPOS PREGUNTA LO MISMO QUE EL GUARDIA: también la deuda cobrada.

El caso de Beto: 100 L a $1.800 = $180.000 de leche contra un adelanto de $300.000. La
quincena queda debiendo $120.000 y se aprueba (se lee "pagada · quedó debiendo", pero
sigue guardada 'aprobada'). Mientras esa deuda no se cobre, el adelanto se corrige: la
quincena vuelve a borrador y se recalcula. En cuanto la quincena siguiente se cobra los
$120.000, las cifras de la primera quedan congeladas y el servidor rebota el cambio.

La pantalla de Anticipos pinta Editar/Eliminar según `bloqueado`. Esa marca miraba solo
`ya_salio_papel_o_plata`, así que para la quincena cobrada decía "se puede" y el PUT
daba 422: un botón que siempre falla. Ahora las dos preguntan `cifras_congeladas`.
"""
from decimal import Decimal

from tests.conftest import auth_headers

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"


def D(v):
    return Decimal(str(v))


def _proveedor(client, h, nombre):
    r = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": "1800"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros, precio=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": litros}
    if precio:
        cuerpo["precio_litro"] = precio
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, inicio, fin, prov):
    r = client.post(f"{API}/generar", json={
        "periodo_inicio": inicio, "periodo_fin": fin, "tipo": "proveedor"}, headers=h)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov["id"])


def _quincena_que_queda_debiendo(client, h, nombre):
    """$180.000 de leche contra $300.000 de adelanto, aprobada: debe $120.000."""
    prov = _proveedor(client, h, nombre)
    _recepcion(client, h, prov, "2026-06-02", "100")
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov["id"],
                               "fecha": "2026-06-01", "valor": "300000"}, headers=h)
    assert r.status_code == 201, r.text
    anticipo = r.json()
    q1 = _generar(client, h, "2026-06-01", "2026-06-15", prov)
    r = client.post(f"{API}/{q1['id']}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    q1 = r.json()
    assert D(q1["saldo"]) == D("-120000")
    return prov, anticipo, q1


def _marca(client, h, anticipo_id):
    """La marca por los DOS caminos que la pantalla usa: la lista y el GET de uno."""
    uno = client.get(f"{ANT}/{anticipo_id}", headers=h)
    assert uno.status_code == 200, uno.text
    lista = client.get(ANT, params={"page_size": 200}, headers=h)
    assert lista.status_code == 200, lista.text
    en_lista = next(a for a in lista.json()["items"] if a["id"] == anticipo_id)
    assert uno.json()["bloqueado"] == en_lista["bloqueado"]
    return uno.json()


def test_con_la_deuda_ya_cobrada_el_anticipo_sale_trabado_y_el_servidor_lo_rebota(
        client, base_datos):
    h = auth_headers(client, "admin.a")
    prov, anticipo, q1 = _quincena_que_queda_debiendo(client, h, "Beto Cobrada")
    # La quincena siguiente: 100 L a $2.500 = $250.000 − $120.000 de la pasada.
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, "2026-06-16", "2026-06-30", prov)
    assert D(q2["saldo_anterior"]) == D("120000")
    antes = client.get(f"{API}/{q1['id']}", headers=h).json()
    assert antes["deuda_trasladada_a_id"] == q2["id"]
    assert antes["estado"] == "aprobada"

    marca = _marca(client, h, anticipo["id"])
    print(f"\n  anticipo de Beto: liquidacion_estado={marca['liquidacion_estado']} "
          f"bloqueado={marca['bloqueado']}")
    assert marca["bloqueado"] is True

    put = client.put(f"{ANT}/{anticipo['id']}", json={"valor": "200000"}, headers=h)
    delete = client.delete(f"{ANT}/{anticipo['id']}", headers=h)
    print(f"  PUT -> {put.status_code} {put.json()['error']['detail']!r}")
    print(f"  DELETE -> {delete.status_code}")
    assert put.status_code == 422, put.text
    assert "ya se le cobró" in put.json()["error"]["detail"]
    assert delete.status_code == 422, delete.text

    # Y no se movió un peso de ninguna de las dos.
    despues = client.get(f"{API}/{q1['id']}", headers=h).json()
    for campo in ("estado", "anticipos", "saldo", "deuda_trasladada_a_id"):
        assert despues[campo] == antes[campo], campo
    assert D(client.get(f"{API}/{q2['id']}", headers=h).json()["saldo_anterior"]) == D("120000")


def test_con_la_deuda_todavia_sin_cobrar_el_anticipo_se_sigue_corrigiendo(client, base_datos):
    """Lo que no cambió: sin la quincena siguiente, la marca dice "se puede" y el PUT
    pasa. La quincena vuelve a borrador y queda debiendo $110.000."""
    h = auth_headers(client, "admin.a")
    _, anticipo, q1 = _quincena_que_queda_debiendo(client, h, "Ana SinCobrar")

    marca = _marca(client, h, anticipo["id"])
    assert marca["liquidacion_estado"] == "aprobada"
    assert marca["bloqueado"] is False

    put = client.put(f"{ANT}/{anticipo['id']}", json={"valor": "290000"}, headers=h)
    assert put.status_code == 200, put.text
    q1 = client.get(f"{API}/{q1['id']}", headers=h).json()
    assert q1["estado"] == "borrador"
    assert D(q1["anticipos"]) == D("290000")
    assert D(q1["saldo"]) == D("-110000")          # 180.000 − 290.000
    assert D(q1["neto_a_pagar"]) == D(q1["pagado"]) + D(q1["saldo"])


def test_la_pagada_sigue_trabada_como_siempre(client, base_datos):
    """Control: $180.000 de leche con $50.000 de adelanto, pagada. Trabada y rebota."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Nora Pagada")
    _recepcion(client, h, prov, "2026-06-02", "100")
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov["id"],
                               "fecha": "2026-06-01", "valor": "50000"}, headers=h)
    anticipo = r.json()
    q1 = _generar(client, h, "2026-06-01", "2026-06-15", prov)
    assert client.post(f"{API}/{q1['id']}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{q1['id']}/pagar", headers=h).status_code == 200

    assert _marca(client, h, anticipo["id"])["bloqueado"] is True
    r = client.put(f"{ANT}/{anticipo['id']}", json={"valor": "40000"}, headers=h)
    assert r.status_code == 422, r.text
