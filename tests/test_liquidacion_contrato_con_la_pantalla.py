"""LOS CAMPOS QUE LA PANTALLA LEE LLEGAN CON SU NOMBRE Y SU TIPO, EN CADA CLASE DE FILA.

El frontend (`core/models.ts`) los declara opcionales para aguantar una respuesta vieja:
si uno llega con otro nombre, la pantalla no se rompe, se calla. Vuelve a su redacción
local y el aviso que el servidor escribió para esa fila no sale. Por eso el nombre exacto
se comprueba aquí, en la respuesta de la API y no en el esquema, sobre una fila de cada
clase, con las cifras del dueño:

  · normal: 100 L × $1.800 = $180.000 aprobada, sin nada que avisar;
  · 'parcial' v2 sin pagos: $180.000 cubiertos por un adelanto de $180.000, pagada y
    corregida con un día olvidado de 20 L ($36.000). No tiene ningún abono;
  · deuda ya cobrada: Beto, $180.000 contra $300.000, debe $120.000 y la del 16/06 se
    los cobró. Cada botón que eso traba trae su 422;
  · pagada sin pago: la del 16/06 de $120.000 que la deuda de $120.000 dejó en cero;
  · deuda borrada: la de julio, $180.000 contra $300.000, migrada. El tercero debe
    $120.000;
y en Recepción diaria, el tablero y el balance, sus campos.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_con_abonos import _aprobada, _dia_olvidado
from tests.test_liquidacion_consejo_de_la_deuda_cobrada import _beto
from tests.test_liquidacion_migrada_deuda_borrada import _migrada
from tests.test_liquidacion_pagada_neto_en_cero_por_la_deuda import _pura
from tests.test_recepcion_liquidacion_con_abono import _aprobada_de_180

V = "/api/v1"
API = f"{V}/liquidaciones"
REC = f"{V}/recepciones"
ACCIONES = {"anular", "corregir", "recalcular", "precio", "eliminar_pago"}


def D(v):
    return Decimal(str(v))


def _las_dos_lecturas(client, h, liq_id):
    """El GET y el listado, que la pantalla usa por igual, con los cuatro campos."""
    uno = client.get(f"{API}/{liq_id}", headers=h)
    assert uno.status_code == 200, uno.text
    lista = client.get(API, params={"page_size": 200}, headers=h)
    assert lista.status_code == 200, lista.text
    en_la_lista = next(x for x in lista.json()["items"] if x["id"] == liq_id)
    for fila in (uno.json(), en_la_lista):
        assert fila["aviso_deuda_borrada"] is None or isinstance(fila["aviso_deuda_borrada"], str)
        assert isinstance(fila["avisos_deuda_cobrada"], dict)
        assert set(fila["avisos_deuda_cobrada"]) <= ACCIONES
        assert all(isinstance(t, str) and t for t in fila["avisos_deuda_cobrada"].values())
        assert isinstance(fila["con_abonos"], bool)
        assert fila["cerrada_sin_pago"] is None or isinstance(fila["cerrada_sin_pago"], str)
    campos = ("aviso_deuda_borrada", "avisos_deuda_cobrada", "con_abonos", "cerrada_sin_pago")
    assert {c: uno.json()[c] for c in campos} == {c: en_la_lista[c] for c in campos}
    return uno.json()


def _tablero_y_balance(client, h):
    tablero = client.get(f"{V}/reportes/dashboard", headers=h)
    balance = client.get(f"{V}/contabilidad/balance", headers=h)
    assert tablero.status_code == balance.status_code == 200
    return tablero.json()["quincenas_por_reparar"], balance.json()["quincenas_por_reparar"]


def test_la_fila_normal_trae_los_campos_vacios(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, dia, liq = _aprobada_de_180(client, h, "Contrato Normal")
    fila = _las_dos_lecturas(client, h, liq)
    assert (D(fila["saldo"]), fila["aviso_deuda_borrada"], fila["avisos_deuda_cobrada"],
            fila["con_abonos"], fila["cerrada_sin_pago"]) == (D(180000), None, {}, False, None)
    rec = client.get(f"{REC}/{dia}", headers=h).json()
    assert (rec["liquidacion_estado"], rec["liquidacion_con_abono"]) == ("aprobada", False)
    # Un entero, no un decimal en texto: la pantalla lo compara con 0.
    assert _tablero_y_balance(client, h) == (0, 0)
    assert all(type(n) is int for n in _tablero_y_balance(client, h))


def test_la_parcial_sin_pagos_no_tiene_abono_ni_en_la_quincena_ni_en_el_dia(client, base_datos):
    h = auth_headers(client, "admin.a")
    prov, liq = _aprobada(client, h, "Contrato Parcial", adelanto="180000")
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    _dia_olvidado(client, h, prov, liq)
    fila = _las_dos_lecturas(client, h, liq)
    # $216.000 − $180.000 = $36.000 por entregar, y por pagos no ha salido un peso.
    assert (fila["estado"], fila["version"], D(fila["saldo"]), fila["con_abonos"]) == (
        "parcial", 2, D(36000), False)
    dias = client.get(REC, params={"page_size": 200}, headers=h).json()["items"]
    assert {(d["liquidacion_estado"], d["liquidacion_con_abono"]) for d in dias} == {
        ("parcial", False)}


def test_la_deuda_cobrada_trae_el_422_de_cada_boton_que_traba(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, _, q1, q2 = _beto(client, h, "Contrato Beto")
    fila = _las_dos_lecturas(client, h, q1["id"])
    # Borrador sin pagos: Anular, Corregir, Recalcular y el precio; no hay pago que borrar.
    assert set(fila["avisos_deuda_cobrada"]) == {"anular", "corregir", "recalcular", "precio"}
    assert all("ya se le cobró en la liquidación del 16/06/2026 al 30/06/2026" in t
               for t in fila["avisos_deuda_cobrada"].values())
    # La que se la cobró no tiene nada trabado por esa deuda.
    assert _las_dos_lecturas(client, h, q2["id"])["avisos_deuda_cobrada"] == {}


def test_la_pagada_sin_pago_trae_su_frase(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, _, q2, _ = _pura(client, h, "Contrato Pura")
    fila = _las_dos_lecturas(client, h, q2)
    assert fila["cerrada_sin_pago"] == (
        "Esta quincena quedó cerrada como pagada sin que saliera un peso, porque lo que el "
        "tercero quedó debiendo de la quincena pasada ($120.000) se llevó el neto")


def test_la_deuda_borrada_trae_la_posicion_de_hoy_y_cuenta_en_el_tablero(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, leida = _migrada(client, h, db_session, "Contrato Migrada")
    fila = _las_dos_lecturas(client, h, leida["id"])
    assert fila["aviso_deuda_borrada"] == "Hoy el tercero todavía le debe $120.000 a la quesera"
    assert fila["cerrada_sin_pago"] is None
    assert _tablero_y_balance(client, h) == (1, 1)
