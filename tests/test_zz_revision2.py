"""zz REVISIÓN 2 — lo que el servidor contesta en los casos donde la pantalla (Front-Lactis,
ronda 2) ofrece o dice otra cosa. Solo mide; no toca código.

  A. La migrada corregida con $50.000 y pagada antes del guardia queda 'aprobada', y
     `anular` la rebota por la deuda borrada: la pantalla le pinta el botón Anular.
  B. La migrada corregida HACIA ABAJO y con su deuda ya cobrada en la siguiente tiene LAS
     DOS marcas. `corregir` rebota por la deuda borrada (va primera); la pantalla dice
     "Anule primero esa liquidación", y anularla no destraba nada.
  C. El día de una 'pagada' del botón de antes (pagado $0, el tercero debía) cuya deuda
     ya se cobró: `candado_aviso` dice "ya se le cobró", no "ya se le pagó".
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
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _dia, _julio_aprobada, _leer

V = "/api/v1"
API = f"{V}/liquidaciones"


def D(v):
    return Decimal(str(v))


def _generar_segunda(client, h, prov):
    _dia(client, h, prov, "2026-07-20", "10")
    r = client.post(f"{API}/generar", json={"periodo_inicio": "2026-07-16",
                                            "periodo_fin": "2026-07-31", "tipo": "proveedor"},
                    headers=h)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)


def test_a_la_aprobada_con_deuda_borrada_no_se_deja_anular(
        client, base_datos, db_session, monkeypatch):
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Anula Cincuenta").values()
    olvidado = _dia(client, h, prov, "2026-07-08", "25")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, olvidado)
    assert client.post(f"{API}/{liq_id}/pagar", headers=h).status_code == 200
    monkeypatch.undo()

    hoy = _leer(client, h, liq_id)
    print(f"\n  estado={hoy['estado']} v{hoy['version']} pagado={hoy['pagado']} "
          f"saldo={hoy['saldo']} traslado={hoy.get('deuda_trasladada_a_id')} "
          f"borrada={hoy['deuda_borrada_por_la_migracion']}")
    # Lo que mira `puedeAnular` en la pantalla: 'aprobada' y sin deuda trasladada.
    assert hoy["estado"] == "aprobada"
    assert hoy.get("deuda_trasladada_a_id") is None
    assert D(hoy["deuda_borrada_por_la_migracion"]) == D("120000")

    r = client.post(f"{API}/{liq_id}/anular", headers=h)
    print(f"  anular -> {r.status_code} {_detalle(r)!r}")
    assert r.status_code == 422
    assert "viene de antes de que existieran los abonos" in _detalle(r)


def test_b_con_las_dos_marcas_corregir_rebota_por_la_borrada_y_anular_la_otra_no_sirve(
        client, base_datos, db_session, monkeypatch):
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Baja Precio").values()
    leida = _leer(client, h, liq_id)
    detalle_id = leida["detalles"][0]["id"]
    _antes_del_guardia(monkeypatch)
    # $2.000 → $1.500 en los 90 L: valor $135.000, neto −$165.000, saldo −$45.000.
    r = client.post(f"{API}/{liq_id}/corregir", json={
        "motivo": "precio mal digitado",
        "precios": [{"detalle_id": detalle_id, "precio_litro": "1500"}]}, headers=h)
    assert r.status_code == 200, r.text
    monkeypatch.undo()
    segunda = _generar_segunda(client, h, prov)

    hoy = _leer(client, h, liq_id)
    print(f"\n  estado={hoy['estado']} v{hoy['version']} pagado={hoy['pagado']} "
          f"saldo={hoy['saldo']} debe={hoy['le_queda_debiendo']} "
          f"traslado={hoy.get('deuda_trasladada_a_id')} "
          f"borrada={hoy['deuda_borrada_por_la_migracion']} "
          f"segunda.saldo_anterior={segunda.get('saldo_anterior')}")
    assert hoy.get("deuda_trasladada_a_id") == segunda["id"]
    assert D(hoy["deuda_borrada_por_la_migracion"]) == D("120000")

    r = client.post(f"{API}/{liq_id}/corregir/previsualizar", json={
        "motivo": "otra vez", "precios": [{"detalle_id": detalle_id, "precio_litro": "1600"}]},
        headers=h)
    print(f"  corregir -> {r.status_code} {_detalle(r)!r}")
    assert r.status_code == 422
    assert "viene de antes de que existieran los abonos" in _detalle(r)
    assert "Anule primero" not in _detalle(r)

    # Lo que manda la pantalla ("Anule primero esa liquidación"), hecho: y no destraba.
    r = client.post(f"{API}/{segunda['id']}/anular", headers=h)
    print(f"  anular la segunda -> {r.status_code}")
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/{liq_id}/corregir/previsualizar", json={
        "motivo": "otra vez", "precios": [{"detalle_id": detalle_id, "precio_litro": "1600"}]},
        headers=h)
    print(f"  corregir después -> {r.status_code} {_detalle(r)!r}")
    assert r.status_code == 422
    assert "viene de antes de que existieran los abonos" in _detalle(r)


def test_c_el_dia_de_la_pagada_sin_plata_cuya_deuda_ya_se_cobro(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, liq = _julio_aprobada(client, h, "Pagada Sin Plata", "90", "300000")
    # El botón Pagar de antes: solo cambiaba el estado. Sin migración: saldo −$120.000.
    fila = db_session.get(Liquidacion, uuid.UUID(liq["id"]))
    fila.estado = "pagada"
    db_session.commit()
    _generar_segunda(client, h, prov)
    hoy = _leer(client, h, liq["id"])
    assert hoy["estado"] == "pagada" and D(hoy["pagado"]) == 0
    assert hoy.get("deuda_trasladada_a_id") is not None

    r = client.get(f"{V}/recepciones", params={"desde": "2026-07-01", "hasta": "2026-07-15",
                                              "proveedor_id": prov}, headers=h)
    assert r.status_code == 200, r.text
    (dia,) = [x for x in r.json()["items"] if x["proveedor_id"] == prov and x["fecha"] == "2026-07-04"]
    print(f"\n  leche_pagada={dia['leche_pagada']} estado_leche={dia['liquidacion_estado_leche']}"
          f"\n  candado_aviso={dia['candado_aviso']!r}")
    assert dia["leche_pagada"] is True and dia["liquidacion_estado_leche"] == "pagada"
    # El servidor dice la verdad: no salió un peso, se cobró en la otra.
    assert "ya se le cobró" in dia["candado_aviso"]
    assert "ya se le pagó" not in dia["candado_aviso"]


def test_d_el_tablero_y_el_balance_cuentan_por_pagar_lo_que_ningun_boton_entrega(
        client, base_datos, db_session, monkeypatch):
    """La corregida sin reparar (parcial v2, saldo +$50.000, debe $70.000): la tarjeta del
    listado la saca de la plata por pagar (`resumen_por_estado`); el tablero y el balance,
    no. La pantalla dice en los dos "cuánta plata tiene que sacar el dueño"."""
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Tablero Cincuenta").values()
    olvidado = _dia(client, h, prov, "2026-07-08", "25")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, olvidado)
    monkeypatch.undo()
    hoy = _leer(client, h, liq_id)
    assert hoy["estado"] == "parcial" and D(hoy["saldo"]) == D("50000")
    assert D(hoy["deuda_borrada_por_la_migracion"]) == D("120000")
    assert client.post(f"{API}/{liq_id}/pagar", headers=h).status_code == 422

    tarjetas = client.get(f"{API}/resumen", headers=h).json()
    tablero = client.get(f"{V}/reportes/dashboard", headers=h).json()
    balance = client.get(f"{V}/contabilidad/balance", headers=h).json()
    print(f"\n  tarjeta saldo_parciales={tarjetas['saldo_parciales']} "
          f"tablero={tablero['liquidaciones_por_pagar']} balance={balance['liquidaciones_por_pagar']}")
    assert D(tarjetas["saldo_parciales"]) == 0
    # Lo que reproduce el defecto: $50.000 "por pagar" que el servidor no deja pagar.
    assert D(tablero["liquidaciones_por_pagar"]) >= D("50000")
    assert D(balance["liquidaciones_por_pagar"]) >= D("50000")
