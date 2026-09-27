""""POR PAGAR" ES LA MISMA CIFRA EN LAS TRES PANTALLAS QUE LA DICEN.

Las tarjetas del listado ya no sumaban la fila con deuda borrada por la migración (el
servidor rebota Pagar y abonar en ella), pero el tablero y el balance sí: la corregida
antes del guardia (parcial v2, $230.000 − $300.000, pagado −$120.000, saldo +$50.000; la
verdad: debe $70.000) salía "$0 por pagar" en una pantalla y "$50.000" en las otras dos.
Ahora las tres leen `LiquidacionRepository.saldo_por_pagar`.

Las filas, a mano:
  con deuda borrada (no suman en ninguna):
    · parcial v2  $230.000 − $300.000, pagado −$120.000            → saldo  $50.000
    · parcial v2  $380.000 − $300.000, pagado −$70.000, pago $50.000 → saldo $150.000
    · aprobada v2 $330.000 − $300.000, pagado −$120.000             → saldo $150.000
  sanas (suman en las tres):
    · aprobada    $250.000                                          → saldo $250.000
    · parcial     $600.000 − $200.000, pago $100.000                → saldo $300.000
                                                              por pagar = $550.000
"""
import uuid
from datetime import date
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import _escribir
from tests.test_liquidacion_migrada_deuda_borrada import _proveedor

V = "/api/v1"
API = f"{V}/liquidaciones"


def D(v):
    return Decimal(str(v))


def _por_pagar_en_las_tres(client, h):
    tarjetas = client.get(f"{API}/resumen", headers=h)
    tablero = client.get(f"{V}/reportes/dashboard", headers=h)
    balance = client.get(f"{V}/contabilidad/balance", headers=h)
    for r in (tarjetas, tablero, balance):
        assert r.status_code == 200, r.text
    t = tarjetas.json()
    return {
        "tarjetas": D(t["saldo_aprobadas"]) + D(t["saldo_parciales"]),
        "tablero": D(tablero.json()["liquidaciones_por_pagar"]),
        "balance": D(balance.json()["liquidaciones_por_pagar"]),
    }, t


def test_el_tablero_el_balance_y_las_tarjetas_no_cuentan_la_deuda_borrada(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    p = uuid.UUID(_proveedor(client, h, "Por Pagar Tres"))
    julio, agosto, septiembre = (
        (date(2026, 7, 1), date(2026, 7, 15)), (date(2026, 8, 1), date(2026, 8, 15)),
        (date(2026, 9, 1), date(2026, 9, 15)))
    borradas = [
        _escribir(db_session, emp, p, estado="parcial", version=2, valor_total="230000",
                  anticipos="300000", pagado="-120000", periodo=julio),
        _escribir(db_session, emp, p, estado="parcial", version=2, valor_total="380000",
                  anticipos="300000", pagado="-70000", pagos=("50000",),
                  periodo=(date(2026, 7, 16), date(2026, 7, 31))),
        _escribir(db_session, emp, p, estado="aprobada", version=2, valor_total="330000",
                  anticipos="300000", pagado="-120000", periodo=agosto),
    ]
    db_session.commit()
    assert [b.saldo for b in borradas] == [D("50000"), D("150000"), D("150000")]
    assert all(b.deuda_borrada_por_la_migracion == D("120000") for b in borradas)

    # Solo con las borradas: las tres pantallas dicen $0, y ningún botón las paga.
    cifras, t = _por_pagar_en_las_tres(client, h)
    print(f"\n  solo las borradas: {cifras}")
    assert set(cifras.values()) == {D("0")}
    assert (t["por_reparar"], D(t["deuda_borrada"])) == (3, D("360000"))
    for b in borradas:
        r = client.post(f"{API}/{b.id}/pagos", json={"fecha": "2026-09-20",
                                                     "valor": "1000"}, headers=h)
        assert r.status_code == 422, r.text

    # Con las sanas: las tres dicen $550.000, exactamente la suma de sus saldos.
    _escribir(db_session, emp, p, estado="aprobada", valor_total="250000",
              periodo=(date(2026, 8, 16), date(2026, 8, 31)))
    _escribir(db_session, emp, p, estado="parcial", valor_total="600000",
              anticipos="200000", pagado="100000", pagos=("100000",), periodo=septiembre)
    db_session.commit()
    cifras, t = _por_pagar_en_las_tres(client, h)
    print(f"  con las sanas: {cifras}")
    assert cifras == {"tarjetas": D("550000"), "tablero": D("550000"),
                      "balance": D("550000")}
    assert (D(t["saldo_aprobadas"]), D(t["saldo_parciales"])) == (D("250000"), D("300000"))
    # Las borradas siguen contadas en su tarjeta: la lista las muestra al tocarla.
    assert (t["aprobadas"], t["parciales"]) == (2, 3)
