"""LA LISTA DE ANTICIPOS NO HACE MÁS CONSULTAS POR CADA QUINCENA CON LA DEUDA COBRADA.

El candado de cada adelanto (`candado_aviso`) nombra la quincena que le cobró la deuda a
la suya, y esa relación apunta a la misma tabla: SQLAlchemy no la carga sola en la
consulta de primer nivel. Medido con 21 proveedores, 7 de ellos con su deuda cobrada
($180.000 de leche contra $300.000 de adelanto, debe $120.000, cobrados en la del 16/06):
9 consultas en `main` y 30 en la rama, tres más por cada quincena cobrada. Ahora la otra
punta va en la misma consulta por lote, y la cuenta no crece con las filas.
"""
from sqlalchemy import event

from tests.conftest import auth_headers, engine

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"


def _beto(client, h, nombre):
    """100 L × $1.800 = $180.000 contra $300.000; la del 16/06 (100 L × $2.500) le cobra
    los $120.000."""
    r = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                              "precio_litro": "1800"}, headers=h)
    prov = r.json()["id"]
    for fecha, precio in (("2026-06-02", None), ("2026-06-20", "2500")):
        cuerpo = {"fecha": fecha, "proveedor_id": prov, "cantidad_litros": "100"}
        if precio:
            cuerpo["precio_litro"] = precio
        assert client.post(f"{V}/recepciones", json=cuerpo, headers=h).status_code == 201
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                               "fecha": "2026-06-01", "valor": "300000"}, headers=h)
    assert r.status_code == 201, r.text


def _generar(client, h):
    for inicio, fin in (("2026-06-01", "2026-06-15"), ("2026-06-16", "2026-06-30")):
        r = client.post(f"{API}/generar", json={"periodo_inicio": inicio,
                                                "periodo_fin": fin,
                                                "tipo": "proveedor"}, headers=h)
        assert r.status_code == 200, r.text


def _consultas_de_la_lista(client, h, db_session):
    # Sesión limpia: con los objetos de la preparación todavía en memoria, la otra punta
    # saldría del mapa de identidad sin ir a la base, y no se mediría nada.
    db_session.expunge_all()
    sentencias = []

    def contar(conn, cursor, sentencia, *a):
        sentencias.append(sentencia)

    event.listen(engine, "before_cursor_execute", contar)
    try:
        r = client.get(ANT, params={"page_size": 200}, headers=h)
    finally:
        event.remove(engine, "before_cursor_execute", contar)
    assert r.status_code == 200, r.text
    filas = r.json()["items"]
    assert all(a["bloqueado"] and "ya se le cobró en la liquidación del 16/06/2026"
               in a["candado_aviso"] for a in filas)
    return len(filas), len(sentencias)


def test_la_lista_no_crece_en_consultas_con_las_quincenas_cobradas(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    _beto(client, h, "Beto 1")
    _generar(client, h)
    filas_1, con_una = _consultas_de_la_lista(client, h, db_session)

    for i in range(2, 5):
        _beto(client, h, f"Beto {i}")
    _generar(client, h)
    filas_4, con_cuatro = _consultas_de_la_lista(client, h, db_session)

    print(f"\n  {filas_1} adelanto: {con_una} consultas · {filas_4} adelantos: {con_cuatro}")
    assert (filas_1, filas_4) == (1, 4)
    assert con_cuatro == con_una
