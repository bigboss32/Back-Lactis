"""EL CANDADO DEL DÍA LEE LA «DEUDA BORRADA» DE UNA SOLA FOTO: UN ABONO AJENO NO LA INVENTA.

El PUT y el DELETE de un día en Recepción diaria explican su 422 con la quincena que lo
traba, y la primera razón que se pregunta es la deuda borrada por la migración: Σ(pagos) −
pagado. Esas dos mitades salían de dos SELECT sin candado —la fila de la liquidación y,
aparte, su colección de pagos (selectin)— y en Postgres cada sentencia ve su propia foto.

Medido con una carrera de verdad (Postgres 15, READ COMMITTED): la quincena de agosto de
Don Ramiro, 125 L × $2.000 = $250.000 en 'parcial', pagado $100.000 con pagos de $60.000 y
$40.000, nunca pasó por la migración. Mientras un usuario corrige los litros del 04/08, otro
confirma un abono de $50.000. El 422 decía "el sistema de esa época le borró lo que Don
Ramiro quedaba debiendo ($50.000); hay que repararla antes de tocarla": el rebote era
correcto, la razón falsa, y el consejo vacío —ni Corregir el precio ni borrar los pagos, que
sí existen para esa fila—. El DELETE, lo mismo con un abono de $10.000.

SQLite tiene una sola conexión, así que la carrera se arma a mano, igual que en
test_liquidacion_deuda_borrada_bajo_candado.py: el abono ajeno se escribe por debajo del
ORM, la fila en memoria se queda con su `pagado` viejo y solo la colección de pagos queda
por volver a leer. Es la foto mezclada que deja la carrera. El candado del día toma ahora la
liquidación con FOR UPDATE y `populate_existing` antes de preguntar, y lee las dos mitades
frescas.

Regla de oro sobre la fila después: neto = valor_total − anticipos − saldo_anterior;
saldo = neto − pagado.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_bajo_candado import _abono_ajeno_en_el_medio

V = "/api/v1"
API = f"{V}/liquidaciones"
REC = f"{V}/recepciones"
BORRADA = "antes de que existieran los abonos"
CON_PAGO = "la leche ya tiene un pago registrado en una liquidación"


def D(v):
    return Decimal(str(v))


def _detalle(r):
    return r.json()["error"]["detail"]


def _cuadra(liq):
    neto = D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq["saldo_anterior"] or 0)
    assert D(liq["saldo"]) == neto - D(liq["pagado"]), liq


def _don_ramiro(client, h, nombre):
    """125 L × $2.000 = $250.000 el 04/08, aprobada, con abonos de $60.000 y $40.000."""
    r = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                              "precio_litro": "2000"}, headers=h)
    prov = r.json()["id"]
    r = client.post(REC, json={"fecha": "2026-08-04", "proveedor_id": prov,
                               "cantidad_litros": "125"}, headers=h)
    assert r.status_code == 201, r.text
    dia = r.json()["id"]
    r = client.post(f"{API}/generar", json={"periodo_inicio": "2026-08-01",
                                            "periodo_fin": "2026-08-15",
                                            "tipo": "proveedor"}, headers=h)
    liq = next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)["id"]
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    for valor in ("60000", "40000"):
        r = client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-08-16", "valor": valor},
                        headers=h)
        assert r.status_code == 200, r.text
    antes = client.get(f"{API}/{liq}", headers=h).json()
    assert (antes["estado"], D(antes["pagado"]), D(antes["saldo"])) == (
        "parcial", D(100000), D(150000))
    assert antes["aviso_deuda_borrada"] is None
    _cuadra(antes)
    return dia, liq


def test_put_del_dia_con_un_abono_ajeno_en_el_medio_dice_la_razon_de_verdad(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    dia, liq = _don_ramiro(client, h, "Don Ramiro PUT")
    en_memoria = _abono_ajeno_en_el_medio(db_session, liq, "50000")
    r = client.put(f"{REC}/{dia}", json={"cantidad_litros": "130"}, headers=h)
    del en_memoria
    print(f"\n  PUT litros -> {r.status_code}: {_detalle(r)}")
    # El rebote es el mismo —el día sí está trabado por los pagos—, con la razón verdadera
    # y su consejo: Corregir el precio, o borrar los TRES pagos (el ajeno incluido).
    assert r.status_code == 422
    assert BORRADA not in _detalle(r)
    assert _detalle(r).startswith(f"No se puede cambiar los litros de este día: {CON_PAGO}.")
    assert "use 'Corregir esta quincena'" in _detalle(r)
    assert "Elimine primero esos 3 pagos" in _detalle(r)
    # Lo que quedó: 250.000 − (60.000 + 40.000 + 50.000) = $100.000 por entregar.
    despues = client.get(f"{API}/{liq}", headers=h).json()
    assert (D(despues["pagado"]), D(despues["saldo"])) == (D(150000), D(100000))
    assert despues["aviso_deuda_borrada"] is None
    _cuadra(despues)


def test_delete_del_dia_con_un_abono_ajeno_en_el_medio_dice_la_razon_de_verdad(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    dia, liq = _don_ramiro(client, h, "Don Ramiro DELETE")
    en_memoria = _abono_ajeno_en_el_medio(db_session, liq, "10000")
    r = client.delete(f"{REC}/{dia}", headers=h)
    del en_memoria
    print(f"\n  DELETE -> {r.status_code}: {_detalle(r)}")
    assert r.status_code == 422
    assert BORRADA not in _detalle(r)
    assert _detalle(r).startswith(f"No se puede eliminar este día: {CON_PAGO}.")
    assert "Elimine primero esos 3 pagos" in _detalle(r)
    # 250.000 − (60.000 + 40.000 + 10.000) = $140.000 por entregar, y el día sigue ahí.
    despues = client.get(f"{API}/{liq}", headers=h).json()
    assert (D(despues["pagado"]), D(despues["saldo"])) == (D(110000), D(140000))
    _cuadra(despues)
    assert client.get(f"{REC}/{dia}", headers=h).status_code == 200


def test_la_deuda_borrada_de_verdad_sigue_yendo_primera(client, base_datos, db_session):
    """El candado no borra la razón cuando es cierta: la fila que la migración dejó con
    pagado −$120.000 ($180.000 contra $300.000 de adelanto, saldo 0, sin renglón de pago)
    sigue rebotando el día con la deuda borrada, en el PUT y en el DELETE."""
    from tests.test_liquidacion_migrada_deuda_borrada import _migrada

    h = auth_headers(client, "admin.a")
    prov, migrada = _migrada(client, h, db_session, "Julio Candado Dia")
    assert D(migrada["deuda_borrada_por_la_migracion"]) == D(120000)
    r = client.get(REC, params={"proveedor_id": prov}, headers=h)
    assert r.status_code == 200, r.text
    (dia,) = [x["id"] for x in r.json()["items"]]
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "80"}, headers=h)
    borrar = client.delete(f"{REC}/{dia}", headers=h)
    print(f"\n  PUT: {_detalle(put)}\n  DELETE: {_detalle(borrar)}")
    for r in (put, borrar):
        assert r.status_code == 422
        assert BORRADA in _detalle(r) and "($120.000)" in _detalle(r)
