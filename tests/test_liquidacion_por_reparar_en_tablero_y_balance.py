"""LA QUINCENA CON DEUDA BORRADA YA NO DESAPARECE DEL TABLERO NI DEL BALANCE SIN AVISO.

"Por pagar" deja por fuera las filas con deuda borrada por la migración, porque el
servidor rebota Pagar y abonar en ellas (`LiquidacionRepository.saldo_por_pagar`). El
listado lo decía con su tarjeta "Deuda borrada por reparar"; el tablero y el balance no
tenían dónde decirlo, y su $0 se leía como "no se le debe nada a nadie".

El caso del dueño, por el camino real: la de julio, 90 L × $2.000 = $180.000 contra
$300.000 de adelanto, 'pagada' con el botón de antes y pasada por la migración (pagado
−$120.000, saldo $0). Después, antes del guardia, Corregir le agregó un día olvidado de
100 L = $200.000:
    valor $380.000 − anticipos $300.000 = neto $80.000
    pagado −$120.000 → saldo $80.000 − (−$120.000) = $200.000
    deuda borrada $120.000 → de verdad falta entregarle $200.000 − $120.000 = $80.000
El tablero y el balance decían $0 por pagar sin una palabra; ahora traen
`quincenas_por_reparar` = 1, el mismo número que la tarjeta del listado, y "por pagar"
sigue en $0 porque ningún botón entrega esa plata.
"""
import uuid
from datetime import date
from decimal import Decimal

from app.modules.contabilidad.schemas import BalanceResponse
from app.modules.reportes.schemas import DashboardResponse
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import (
    _antes_del_guardia,
    _corregir,
    _escribir,
    _migradas,
)
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _dia, _leer, _proveedor

V = "/api/v1"
API = f"{V}/liquidaciones"


def D(v):
    return Decimal(str(v))


def _las_tres(client, h, **filtros):
    """Lo que dicen las tres pantallas: la tarjeta del listado, el tablero y el balance."""
    resumen = client.get(f"{API}/resumen", params=filtros, headers=h)
    tablero = client.get(f"{V}/reportes/dashboard", headers=h)
    balance = client.get(f"{V}/contabilidad/balance", headers=h)
    for r in (resumen, tablero, balance):
        assert r.status_code == 200, r.text
    return resumen.json(), tablero.json(), balance.json()


def _corregida_hacia_arriba(client, h, db, monkeypatch, nombre, *, con_plain=None):
    """La de julio de $180.000 contra $300.000, migrada y corregida antes del guardia con
    un día olvidado de 100 L = $200.000. Con `con_plain`, otra migrada que no se tocó
    (pagada, saldo $0, debe $120.000), en la MISMA corrida de la migración."""
    nombres = (nombre,) + ((con_plain,) if con_plain else ())
    filas = _migradas(client, h, db, *nombres)
    prov, liq_id = filas[nombre]
    olvidado = _dia(client, h, prov, "2026-07-08", "100")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, olvidado)
    monkeypatch.undo()
    return liq_id


def test_la_corregida_hacia_arriba_se_cuenta_igual_en_las_tres_pantallas(
        client, base_datos, db_session, monkeypatch):
    h = auth_headers(client, "admin.a")
    liq_id = _corregida_hacia_arriba(client, h, db_session, monkeypatch, "Henri Tablero")

    hoy = _leer(client, h, liq_id)
    entregado = sum((D(p["valor"]) for p in hoy["pagos"]), D(0))
    print(f"\n  corregida: {hoy['estado']} v{hoy['version']} valor={hoy['valor_total']} "
          f"anticipos={hoy['anticipos']} pagado={hoy['pagado']} saldo={hoy['saldo']} "
          f"borrada={hoy['deuda_borrada_por_la_migracion']} entregado={entregado}")
    assert (hoy["estado"], hoy["version"]) == ("parcial", 2)
    assert (D(hoy["valor_total"]), D(hoy["anticipos"]), D(hoy["pagado"]), D(hoy["saldo"]),
            D(hoy["deuda_borrada_por_la_migracion"])) == (
        D("380000"), D("300000"), D("-120000"), D("200000"), D("120000"))
    # Con calculadora: neto $80.000 − $0 entregado = $80.000 = saldo − la deuda borrada.
    assert D(hoy["neto_a_pagar"]) - entregado == D("80000")
    assert D(hoy["saldo"]) - D(hoy["deuda_borrada_por_la_migracion"]) == D("80000")

    r, t, b = _las_tres(client, h)
    print(f"  resumen: por_reparar={r['por_reparar']} deuda_borrada={r['deuda_borrada']} "
          f"saldo_parciales={r['saldo_parciales']}")
    print(f"  tablero: por_pagar={t['liquidaciones_por_pagar']} "
          f"por_reparar={t.get('quincenas_por_reparar')}")
    print(f"  balance: por_pagar={b['liquidaciones_por_pagar']} "
          f"por_reparar={b.get('quincenas_por_reparar')}")

    # Las tres dicen que hay UNA quincena por reparar.
    assert (r["por_reparar"], D(r["deuda_borrada"])) == (1, D("120000"))
    assert t["quincenas_por_reparar"] == b["quincenas_por_reparar"] == r["por_reparar"] == 1
    # Y "por pagar" no la mete: ningún botón entrega esa plata, ni los $200.000 del saldo
    # ni los $80.000 que de verdad faltan.
    assert D(t["liquidaciones_por_pagar"]) == D(b["liquidaciones_por_pagar"]) == D(0)
    assert D(r["saldo_parciales"]) == D(0) and r["parciales"] == 1
    assert D(t["terceros_le_quedan_debiendo"]) == D(b["terceros_le_quedan_debiendo"]) == 0
    pagar = client.post(f"{API}/{liq_id}/pagar", headers=h)
    assert pagar.status_code == 422 and "$80.000" in _detalle(pagar), pagar.text


def test_cuenta_el_mismo_universo_que_la_tarjeta_y_solo_de_la_empresa(
        client, base_datos, db_session, monkeypatch):
    """Cuentan: la corregida hacia arriba y la migrada sin tocar (pagada, saldo $0, el
    tercero debe $120.000). No cuentan: la anulada con deuda borrada (no vale nada), la
    borrada de la base, la de otra empresa ni la aprobada sana de $250.000, que sí es
    "por pagar"."""
    h = auth_headers(client, "admin.a")
    emp_a, emp_b = base_datos["empresa_a"].id, base_datos["empresa_b"].id
    _corregida_hacia_arriba(client, h, db_session, monkeypatch, "Henri Dos",
                            con_plain="Plain Dos")

    otro = uuid.UUID(_proveedor(client, h, "Fuera De La Cuenta"))
    agosto, septiembre = (date(2026, 8, 1), date(2026, 8, 15)), (date(2026, 9, 1),
                                                                  date(2026, 9, 15))
    anulada = _escribir(db_session, emp_a, otro, estado="anulada", valor_total="180000",
                        anticipos="300000", pagado="-120000", periodo=agosto)
    borrada_de_la_base = _escribir(db_session, emp_a, otro, estado="pagada",
                                   valor_total="180000", anticipos="300000",
                                   pagado="-120000", borrada=True,
                                   periodo=(date(2026, 8, 16), date(2026, 8, 31)))
    _escribir(db_session, emp_a, otro, estado="aprobada", valor_total="250000",
              periodo=septiembre)
    hb = auth_headers(client, "admin.b")
    de_b = _escribir(db_session, emp_b, uuid.UUID(_proveedor(client, hb, "Henri B")),
                     estado="pagada", valor_total="180000", anticipos="300000",
                     pagado="-120000")
    db_session.commit()
    for fila in (anulada, borrada_de_la_base, de_b):
        assert fila.deuda_borrada_por_la_migracion == D("120000")

    r, t, b = _las_tres(client, h)
    print(f"\n  A: resumen={r['por_reparar']} tablero={t['quincenas_por_reparar']} "
          f"balance={b['quincenas_por_reparar']} por_pagar={t['liquidaciones_por_pagar']}")
    assert t["quincenas_por_reparar"] == b["quincenas_por_reparar"] == r["por_reparar"] == 2
    assert D(r["deuda_borrada"]) == D("240000")
    # La aprobada sana es lo único por pagar, en las tres.
    assert (D(t["liquidaciones_por_pagar"]), D(b["liquidaciones_por_pagar"]),
            D(r["saldo_aprobadas"]) + D(r["saldo_parciales"])) == (
        D("250000"), D("250000"), D("250000"))

    # La empresa B ve solo la suya.
    rb, tb, bb = _las_tres(client, hb)
    assert tb["quincenas_por_reparar"] == bb["quincenas_por_reparar"] == rb["por_reparar"] == 1

    # La tarjeta del listado se filtra por fechas y el tablero y el balance no: con el
    # listado en septiembre la tarjeta dice 0 y las otras dos siguen diciendo 2.
    r_sep, t_sep, b_sep = _las_tres(client, h, desde="2026-09-01", hasta="2026-09-30")
    assert r_sep["por_reparar"] == 0
    assert t_sep["quincenas_por_reparar"] == b_sep["quincenas_por_reparar"] == 2


def test_sin_quincenas_por_reparar_dicen_cero_y_el_campo_tiene_su_cero(
        client, base_datos, db_session):
    """Un front viejo con el back nuevo (o al revés) no se rompe: el campo existe y vale
    cero por defecto."""
    h = auth_headers(client, "admin.a")
    _, t, b = _las_tres(client, h)
    assert t["quincenas_por_reparar"] == b["quincenas_por_reparar"] == 0
    assert DashboardResponse.model_fields["quincenas_por_reparar"].default == 0
    assert BalanceResponse.model_fields["quincenas_por_reparar"].default == 0
