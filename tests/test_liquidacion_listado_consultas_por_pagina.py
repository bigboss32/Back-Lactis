"""EL LISTADO DE LIQUIDACIONES NO HACE UNA CONSULTA POR FILA PARA LAS DOS PUNTAS DE LA DEUDA.

`deudas_cobradas` y `deuda_trasladada_a` apuntan a la misma tabla, y en una relación así
SQLAlchemy no aplica la carga selectin del modelo en la consulta de primer nivel: cada
fila que la respuesta serializa disparaba su propio SELECT. Medido con 50 proveedores de
100 L × $1.800 = $180.000: 57 consultas por página, 50 de ellas "… WHERE ? =
liquidaciones.deuda_trasladada_a_id". Y cuando la otra punta queda FUERA de la página
(20 proveedores, 8 de ellos con $180.000 contra $300.000 de adelanto, debe $120.000,
cobrados en la del 16/06; listado hasta el 15/06): 51 consultas para 20 filas, tres más
por cada quincena cobrada (la otra, sus días y sus pagos).

Ahora el listado carga las dos puntas en la misma pasada para toda la página
(`LiquidacionRepository.pagina_del_listado`), y la cuenta no crece con las filas. Las
cifras no se tocan: cada desglose de `deudas_cobradas` suma exacto su `saldo_anterior`, y
neto = valor − anticipos − deuda vieja ($250.000 − $120.000 = $130.000).
"""
import re
from decimal import Decimal

from sqlalchemy import event

from tests.conftest import auth_headers, engine

V = "/api/v1"
API = f"{V}/liquidaciones"
POR_FILA = re.compile(r"WHERE \? = liquidaciones\.deuda_trasladada_a_id")


def D(v):
    return Decimal(str(v))


def _proveedores(client, h, desde, hasta, deudores=0):
    """100 L × $1.800 = $180.000 el 02/06. Los primeros `deudores` traen además $300.000
    de adelanto (deben $120.000) y 100 L × $2.500 = $250.000 el 20/06, que se los cobra."""
    for i in range(desde, hasta):
        r = client.post(f"{V}/proveedores", json={"nombre": f"Listado {i}", "vereda": "X",
                                                  "precio_litro": "1800"}, headers=h)
        assert r.status_code == 201, r.text
        prov = r.json()["id"]
        r = client.post(f"{V}/recepciones", json={"fecha": "2026-06-02", "proveedor_id": prov,
                                                  "cantidad_litros": "100"}, headers=h)
        assert r.status_code == 201, r.text
        if i < deudores:
            r = client.post(f"{V}/recepciones", json={
                "fecha": "2026-06-20", "proveedor_id": prov, "cantidad_litros": "100",
                "precio_litro": "2500"}, headers=h)
            assert r.status_code == 201, r.text
            r = client.post(f"{V}/anticipos", json={"tipo": "proveedor", "proveedor_id": prov,
                                                    "fecha": "2026-06-01", "valor": "300000"},
                            headers=h)
            assert r.status_code == 201, r.text


def _generar(client, h, *periodos):
    for inicio, fin in periodos:
        r = client.post(f"{API}/generar", json={"periodo_inicio": inicio, "periodo_fin": fin,
                                                "tipo": "proveedor"}, headers=h)
        assert r.status_code == 200, r.text


def _medir(client, h, db_session, **params):
    """Consultas de UNA página del listado, con la sesión limpia: con los objetos de la
    preparación en memoria la otra punta saldría del mapa de identidad sin ir a la base."""
    db_session.expunge_all()
    sentencias = []

    def contar(conn, cursor, sentencia, *a):
        sentencias.append(sentencia)

    event.listen(engine, "before_cursor_execute", contar)
    try:
        r = client.get(API, params={"page_size": 50, **params}, headers=h)
    finally:
        event.remove(engine, "before_cursor_execute", contar)
    assert r.status_code == 200, r.text
    return r.json()["items"], len(sentencias), sum(1 for s in sentencias if POR_FILA.search(s))


def test_la_pagina_no_crece_en_consultas_con_las_filas(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _proveedores(client, h, 0, 1)
    _generar(client, h, ("2026-06-01", "2026-06-15"))
    una, con_una, por_fila_una = _medir(client, h, db_session)

    _proveedores(client, h, 1, 50)
    _generar(client, h, ("2026-06-01", "2026-06-15"))
    cincuenta, con_cincuenta, por_fila = _medir(client, h, db_session)

    print(f"\n  1 fila: {con_una} consultas ({por_fila_una} por fila) · "
          f"50 filas: {con_cincuenta} consultas ({por_fila} por fila)")
    assert (len(una), len(cincuenta)) == (1, 50)
    assert con_cincuenta == con_una
    assert por_fila == 0
    # Las cifras de la página no cambian: 50 × $180.000 = $9.000.000.
    assert sum(D(x["neto_a_pagar"]) for x in cincuenta) == D("9000000")


def test_la_otra_punta_fuera_de_la_pagina_tampoco_cuesta_por_fila(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    _proveedores(client, h, 0, 2, deudores=1)
    _generar(client, h, ("2026-06-01", "2026-06-15"), ("2026-06-16", "2026-06-30"))
    pocas, con_pocas, _ = _medir(client, h, db_session, hasta="2026-06-15")

    _proveedores(client, h, 2, 20, deudores=9)
    _generar(client, h, ("2026-06-01", "2026-06-15"), ("2026-06-16", "2026-06-30"))
    muchas, con_muchas, por_fila = _medir(client, h, db_session, hasta="2026-06-15")

    trasladadas = [x for x in muchas if x["deuda_trasladada_a"]]
    print(f"\n  {len(pocas)} filas: {con_pocas} consultas · {len(muchas)} filas "
          f"({len(trasladadas)} con la deuda cobrada en la del 16/06): {con_muchas}")
    assert (len(pocas), len(muchas), len(trasladadas)) == (2, 20, 8)
    assert con_muchas == con_pocas
    assert por_fila == 0
    for x in trasladadas:
        assert x["deuda_trasladada_a"]["periodo_texto"] == "16/06/2026 al 30/06/2026"
        assert D(x["le_queda_debiendo"]) == D("120000")

    # Y la página completa, con las dos puntas adentro: cada desglose suma su renglón.
    todas, _, por_fila = _medir(client, h, db_session)
    assert por_fila == 0
    cobradoras = [x for x in todas if x["deudas_cobradas"]]
    assert len(cobradoras) == 8
    for x in cobradoras:
        desglose = sum((D(o["le_queda_debiendo"]) for o in x["deudas_cobradas"]), D(0))
        assert desglose == D(x["saldo_anterior"]) == D("120000")
        assert D(x["valor_total"]) - D(x["anticipos"]) - D(x["saldo_anterior"]) == \
            D(x["neto_a_pagar"]) == D("130000")
