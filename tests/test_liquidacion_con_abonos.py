"""LA PALABRA "ABONO" SALE DE LA PLATA QUE SALIÓ POR PAGOS, NO DEL ESTADO GUARDADO.

100 L × $1.800 = $180.000 cubiertos EXACTO por un adelanto de $180.000: Pagar la cierra
'pagada' sin renglón (pagado $0). Corregir le mete un día olvidado de 20 L = $36.000 y
queda 'parcial' v2 con pagado $0, pagos = [] y saldo $36.000. Con calculadora: por estas
cifras no ha salido un peso por pagos y se le deben $36.000. Aun así la pantalla decía
"Se le abonó una parte", "Con abonos, debiendo" y, en Recepción diaria, "Con abono",
porque todos lo sacaban de `estado == 'parcial'`.

`Liquidacion.con_abonos` es la pregunta, y viaja en `LiquidacionRead.con_abonos`:
  · Σ(pagos) > 0: hubo pagos con renglón;
  · o pagado > 0 sin ningún renglón: la 'pagada' de antes del 01/08/2026, a la que la
    migración le escribió pagado = neto (se pagó por fuera del sistema). Corregida hacia
    arriba queda 'parcial' con esa plata entregada, y ahí sí hubo abono.
No es `tiene_pagos` (pagado > 0), que en las filas con deuda borrada dice que no hubo nada
cuando hubo un pago de $50.000.
"""
import uuid
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import (
    _antes_del_guardia,
    _corregir,
    _migradas,
)
from tests.test_liquidacion_migrada_deuda_borrada import (
    _correr_la_migracion,
    _julio_aprobada,
    _leer,
)

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"


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


def _aprobada(client, h, nombre, adelanto=None):
    """100 L × $1.800 = $180.000 del 02/06, con o sin adelanto, aprobada."""
    prov = _proveedor(client, h, nombre)
    _dia(client, h, prov, "2026-06-02", "100")
    if adelanto:
        r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                   "fecha": "2026-06-01", "valor": adelanto}, headers=h)
        assert r.status_code == 201, r.text
    g = client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                            "periodo_fin": "2026-06-15",
                                            "tipo": "proveedor"}, headers=h)
    assert g.status_code == 200, g.text
    liq = next(x for x in g.json()["generadas"] if x["proveedor_id"] == prov)["id"]
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    return prov, liq


def _dia_olvidado(client, h, prov, liq):
    olvidado = _dia(client, h, prov, "2026-06-05", "20")
    r = client.post(f"{API}/{liq}/corregir", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}, headers=h)
    assert r.status_code == 200, r.text


def _en_el_listado(client, h, liq):
    r = client.get(API, params={"page_size": 200}, headers=h)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["items"] if x["id"] == liq)


def test_la_parcial_v2_del_adelanto_exacto_no_tiene_abonos(client, base_datos):
    h = auth_headers(client, "admin.a")
    prov, liq = _aprobada(client, h, "Abono Adelanto Exacto", adelanto="180000")
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    _dia_olvidado(client, h, prov, liq)
    hoy = _leer(client, h, liq)
    # 216.000 − 180.000 − 0 = 36.000 por entregar, y ningún pago.
    assert (hoy["estado"], hoy["version"], D(hoy["pagado"]), hoy["pagos"],
            D(hoy["saldo"])) == ("parcial", 2, D(0), [], D(36000))
    assert hoy["con_abonos"] is False
    assert _en_el_listado(client, h, liq)["con_abonos"] is False


def test_el_pago_borrado_de_la_corregida_se_lleva_el_abono(client, base_datos):
    """Sin adelanto: se paga con un pago de $180.000, se corrige con el día de $36.000
    (parcial v2 con $180.000 entregados: ahí sí hay abono) y se borra ese pago, que
    estaba mal registrado: 'parcial' v2, pagado $0, saldo $216.000 —el valor entero—."""
    h = auth_headers(client, "admin.a")
    prov, liq = _aprobada(client, h, "Abono Borrado")
    r = client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20", "valor": "180000"},
                    headers=h)
    assert r.status_code == 200, r.text
    _dia_olvidado(client, h, prov, liq)
    con_el_pago = _leer(client, h, liq)
    assert (con_el_pago["estado"], D(con_el_pago["pagado"]), D(con_el_pago["saldo"])) == (
        "parcial", D(180000), D(36000))
    assert con_el_pago["con_abonos"] is True
    (pago,) = con_el_pago["pagos"]
    assert client.delete(f"{API}/{liq}/pagos/{pago['id']}", headers=h).status_code == 200
    hoy = _leer(client, h, liq)
    assert (hoy["estado"], hoy["version"], D(hoy["pagado"]), hoy["pagos"],
            D(hoy["saldo"])) == ("parcial", 2, D(0), [], D(216000))
    assert hoy["con_abonos"] is False


def test_el_abono_de_verdad_sigue_contando(client, base_datos):
    """Control: la parcial v1 con un abono de $50.000 sobre $180.000."""
    h = auth_headers(client, "admin.a")
    _, liq = _aprobada(client, h, "Abono De Verdad")
    r = client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20", "valor": "50000"},
                    headers=h)
    assert r.status_code == 200, r.text
    hoy = _leer(client, h, liq)
    assert (hoy["estado"], hoy["version"], D(hoy["saldo"])) == ("parcial", 1, D(130000))
    assert hoy["con_abonos"] is True
    assert _en_el_listado(client, h, liq)["con_abonos"] is True


def test_la_pagada_de_antes_corregida_hacia_arriba_si_tiene_abono(
        client, base_datos, db_session):
    """100 L × $2.000 = $200.000 − $50.000 de adelanto: 'pagada' con el botón de antes y
    migrada, pagado $150.000 SIN renglón (se pagó por fuera). Corregida con un día de
    10 L ($20.000) queda 'parcial' con esos $150.000 entregados: ahí "se le abonó una
    parte" es verdad, aunque no haya renglones de pago."""
    h = auth_headers(client, "admin.a")
    prov, liq = _julio_aprobada(client, h, "Abono De Antes", "100", "50000")
    db_session.get(Liquidacion, uuid.UUID(liq["id"])).estado = "pagada"
    db_session.commit()
    _correr_la_migracion(db_session)
    olvidado = client.post(REC, json={"fecha": "2026-07-08", "proveedor_id": prov,
                                      "cantidad_litros": "10"}, headers=h).json()["id"]
    _corregir(client, h, liq["id"], olvidado)
    hoy = _leer(client, h, liq["id"])
    assert (hoy["estado"], D(hoy["pagado"]), hoy["pagos"], D(hoy["saldo"]),
            D(hoy["deuda_borrada_por_la_migracion"])) == (
        "parcial", D(150000), [], D(20000), D(0))
    assert hoy["con_abonos"] is True


def test_con_la_deuda_borrada_cuentan_los_pagos_y_no_el_signo(
        client, base_datos, db_session, monkeypatch):
    """La migrada de $180.000 contra $300.000 (pagado −$120.000, sin pagos) no tiene
    abonos. La misma corregida con un día de $50.000 y pagada por el código de agosto
    tiene un pago de $50.000 con pagado −$70.000: `tiene_pagos` dice que no, y sí salió
    plata."""
    h = auth_headers(client, "admin.a")
    filas = _migradas(client, h, db_session, "Abono Plain", "Abono Mas50")
    _, plain = filas["Abono Plain"]
    prov, mas50 = filas["Abono Mas50"]
    olvidado = client.post(REC, json={"fecha": "2026-07-08", "proveedor_id": prov,
                                      "cantidad_litros": "25"}, headers=h).json()["id"]
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, mas50, olvidado)
    assert client.post(f"{API}/{mas50}/pagar", headers=h).status_code == 200
    monkeypatch.undo()
    assert _leer(client, h, plain)["con_abonos"] is False
    hoy = _leer(client, h, mas50)
    assert (D(hoy["pagado"]), [D(p["valor"]) for p in hoy["pagos"]]) == (
        D(-70000), [D(50000)])
    modelo = db_session.get(Liquidacion, uuid.UUID(mas50))
    db_session.refresh(modelo)
    assert modelo.tiene_pagos is False
    assert hoy["con_abonos"] is True
