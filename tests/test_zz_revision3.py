"""REVISIÓN ADVERSARIAL 3, lado backend (sobre K1-K6). Solo mide; no arregla.

Las `test_d*` REPRODUCEN UN DEFECTO y pasan mientras el defecto siga ahí (igual que los
demás archivos zz); las `test_ok*` son controles de lo que quedó bien.

  D1  La quincena corregida que quedó 'parcial' v2 SIN NINGÚN PAGO (el adelanto la cubría
      exacto, se cerró con Pagar sin renglón y se le metió un día olvidado): sus días dicen
      "ya se le abonó" y los 422 "ya tiene un pago registrado… Elimine primero ese pago".
      No hay pago que eliminar; el anticipo de esa misma quincena dice otra cosa.
  D2  La 'pagada' del NETO EN CERO POR LA DEUDA (la que el botón Pagar de antes cerraba
      sin que saliera un peso): `pagada_sin_que_saliera_un_peso` no la ve, y sus días y
      sus observaciones dicen "ya se pagó", el aviso que el propio guardia de Pagar llama
      "un aviso que no es cierto".
  D3  El 422 de observaciones manda a "Corregir esta quincena", y Corregir no cambia
      observaciones: con solo el motivo rebota "no hay nada que corregir".
"""
import uuid
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import (
    _abonar,
    _antes_del_guardia,
    _corregir,
    _entregado,
    _migradas,
)
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _dia, _leer

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"


def D(v):
    return Decimal(str(v))


def _proveedor(client, h, nombre, precio):
    r = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                              "precio_litro": precio}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _generar(client, h, prov, ini, fin):
    r = client.post(f"{API}/generar", json={"periodo_inicio": ini, "periodo_fin": fin,
                                            "tipo": "proveedor"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)["id"]


def _celda(client, h, prov, fecha, desde, hasta):
    r = client.get(f"{REC}/grilla/quincena", params={"desde": desde, "hasta": hasta},
                   headers=h)
    assert r.status_code == 200, r.text
    fila = next(f for f in r.json()["filas"] if f["proveedor_id"] == prov)
    return fila["celdas"][fecha]


# =====================================================================================
# D1. 'parcial' v2 sin ningún pago: el día dice que hay un pago.
# =====================================================================================
def test_d1_corregida_sin_pagos_el_dia_dice_que_hay_un_pago_que_no_existe(
        client, base_datos, db_session):
    """100 L × $1.800 = $180.000 cubiertos EXACTO por un adelanto de $180.000: Pagar la
    cierra 'pagada' sin renglón (pagado $0). Se le mete un día olvidado de 20 L = $36.000
    con Corregir: 'parcial' v2, pagado $0, pagos = [], saldo $36.000. Con calculadora: no
    se le ha abonado un peso por estas cifras; se le deben $36.000."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Corregida Sin Abono", "1800")
    dia = _dia(client, h, prov, "2026-06-02", "100")
    ant = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                 "fecha": "2026-06-01", "valor": "180000"}, headers=h)
    assert ant.status_code == 201, ant.text
    liq = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    olvidado = _dia(client, h, prov, "2026-06-05", "20")
    r = client.post(f"{API}/{liq}/corregir", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}, headers=h)
    assert r.status_code == 200, r.text
    hoy = _leer(client, h, liq)
    print(f"\n  estado={hoy['estado']} v{hoy['version']} pagado={hoy['pagado']} "
          f"pagos={hoy['pagos']} saldo={hoy['saldo']}")
    assert (hoy["estado"], hoy["version"], D(hoy["pagado"]), hoy["pagos"], D(hoy["saldo"])) \
        == ("parcial", 2, D("0"), [], D("36000"))

    dialogo = client.get(f"{REC}/{dia}", headers=h).json()
    celda = _celda(client, h, prov, "2026-06-02", "2026-06-01", "2026-06-15")
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    delete = client.delete(f"{REC}/{dia}", headers=h)
    anticipo = client.get(f"{ANT}/{ant.json()['id']}", headers=h).json()
    obs = client.put(f"{API}/{liq}", json={"observaciones": "nota"}, headers=h)
    print(f"  día candado_aviso: {dialogo['candado_aviso']!r}")
    print(f"  celda candado_aviso: {celda.get('candado_aviso')!r}")
    print(f"  PUT día -> {put.status_code} {_detalle(put)!r}")
    print(f"  DELETE día -> {delete.status_code} {_detalle(delete)!r}")
    print(f"  anticipo candado_aviso: {anticipo['candado_aviso']!r}")
    print(f"  PUT observaciones -> {obs.status_code} {_detalle(obs)!r}")
    # La verdad la dicen el anticipo y las observaciones de ESA quincena:
    assert "ya emitió un comprobante corregido" in anticipo["candado_aviso"]
    assert _detalle(obs).startswith("Esta quincena ya emitió un comprobante corregido")
    # EL DEFECTO: el día (diálogo y celda) dice que se le abonó, y los dos 422 mandan a
    # "eliminar primero ese pago", que no existe.
    assert "ya se le abonó" in dialogo["candado_aviso"]
    assert celda["candado_aviso"] == dialogo["candado_aviso"]
    assert put.status_code == 422 and delete.status_code == 422
    for texto in (_detalle(put), _detalle(delete)):
        assert "ya tiene un pago registrado" in texto
        assert "Elimine primero ese pago" in texto


# =====================================================================================
# D2. La 'pagada' del neto en cero por la deuda arrastrada: "ya se pagó".
# =====================================================================================
def test_d2_pagada_del_neto_en_cero_por_la_deuda_dice_ya_se_pago(
        client, base_datos, db_session):
    """Quincena 1: 90 L × $2.000 = $180.000 contra $300.000 de adelanto → debe $120.000.
    Quincena 2: 60 L × $2.000 = $120.000, sin adelanto; al generarla se cobra los
    $120.000 → neto $0,00 clavado. Pagar HOY rebota diciendo que marcarla pagada
    "trabaría los días de la quincena con un aviso que no es cierto". El botón de antes
    sí la marcaba 'pagada' (pagado $0, sin renglón); esas filas siguen en producción."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Neto En Cero", "2000")
    _dia(client, h, prov, "2026-06-02", "90")
    assert client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                  "fecha": "2026-06-01", "valor": "300000"},
                       headers=h).status_code == 201
    q1 = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{q1}/aprobar", headers=h).status_code == 200
    dia2 = _dia(client, h, prov, "2026-06-17", "60")
    q2 = _generar(client, h, prov, "2026-06-16", "2026-06-30")
    assert client.post(f"{API}/{q2}/aprobar", headers=h).status_code == 200
    leida = _leer(client, h, q2)
    assert (D(leida["valor_total"]), D(leida["saldo_anterior"]), D(leida["saldo"])) == (
        D("120000"), D("120000"), D("0"))
    pagar = client.post(f"{API}/{q2}/pagar", headers=h)
    print(f"\n  Pagar hoy -> {pagar.status_code} {_detalle(pagar)!r}")
    assert pagar.status_code == 422 and "un aviso que no es cierto" in _detalle(pagar)

    # Lo que dejó el botón de antes.
    db_session.get(Liquidacion, uuid.UUID(q2)).estado = "pagada"
    db_session.commit()
    fila = _leer(client, h, q2)
    assert (fila["estado"], D(fila["pagado"]), fila["pagos"], D(fila["le_queda_debiendo"])) \
        == ("pagada", D("0"), [], D("0"))
    dialogo = client.get(f"{REC}/{dia2}", headers=h).json()
    put = client.put(f"{REC}/{dia2}", json={"cantidad_litros": "55"}, headers=h)
    obs = client.put(f"{API}/{q2}", json={"observaciones": "nota"}, headers=h)
    print(f"  día candado_aviso: {dialogo['candado_aviso']!r}")
    print(f"  PUT día -> {put.status_code} {_detalle(put)!r}")
    print(f"  PUT observaciones -> {obs.status_code} {_detalle(obs)!r}")
    # EL DEFECTO: exactamente el aviso que el guardia de Pagar dice que no es cierto.
    assert "ya se le pagó" in dialogo["candado_aviso"]
    assert put.status_code == 422 and "la leche ya se pagó" in _detalle(put)
    assert obs.status_code == 422 and _detalle(obs).startswith("Esta quincena ya se pagó")


# =====================================================================================
# D3. "Use 'Corregir esta quincena'" para cambiar observaciones: Corregir no las cambia.
# =====================================================================================
def test_d3_observaciones_mandan_a_corregir_que_no_cambia_observaciones(
        client, base_datos, db_session):
    """100 L × $1.800 = $180.000 − $50.000 de adelanto = $130.000, pagada con Pagar. El
    dueño quiere arreglar un error de digitación en la nota del comprobante."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Nota Mala", "1800")
    _dia(client, h, prov, "2026-06-02", "100")
    assert client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                  "fecha": "2026-06-01", "valor": "50000"},
                       headers=h).status_code == 201
    liq = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200

    obs = client.put(f"{API}/{liq}", json={"observaciones": "nota corregida"}, headers=h)
    print(f"\n  PUT observaciones -> {obs.status_code} {_detalle(obs)!r}")
    assert obs.status_code == 422 and "Use 'Corregir esta quincena'" in _detalle(obs)
    # Se sigue la salida que nombra: el cuerpo de Corregir no tiene observaciones, y con
    # solo el motivo (que es lo único que puede "dejar escrito") rebota.
    corregir = client.post(f"{API}/{liq}/corregir", json={
        "motivo": "la nota del comprobante quedó mal escrita"}, headers=h)
    print(f"  Corregir solo con motivo -> {corregir.status_code} {_detalle(corregir)!r}")
    assert corregir.status_code == 422
    assert "no hay nada que corregir" in _detalle(corregir)
    assert _leer(client, h, liq)["version"] == 1


# =====================================================================================
# OK. Controles.
# =====================================================================================
def test_ok_k1_masc50_y_paso_de_cero_dicen_lo_que_debe_hoy(
        client, base_datos, db_session, monkeypatch):
    """MAS-CINCUENTA (neto −70.000, $50.000 entregados → debe $120.000) y PASO-DE-CERO
    (neto 80.000, $200.000 entregados → debe $120.000): los dos con saldo 0."""
    h = auth_headers(client, "admin.a")
    filas = _migradas(client, h, db_session, "Ok Mas 50", "Ok Paso Cero")
    _antes_del_guardia(monkeypatch)
    for nombre, litros in (("Ok Mas 50", "25"), ("Ok Paso Cero", "100")):
        prov, liq_id = filas[nombre]
        _corregir(client, h, liq_id, _dia(client, h, prov, "2026-07-08", litros))
        assert client.post(f"{API}/{liq_id}/pagar", headers=h).status_code == 200
    monkeypatch.undo()
    for nombre, (_, liq_id) in filas.items():
        hoy = _leer(client, h, liq_id)
        verdad = D(hoy["neto_a_pagar"]) - _entregado(hoy)
        assert verdad == D("-120000"), nombre
        r = client.post(f"{API}/{liq_id}/pagar", headers=h)
        assert r.status_code == 422
        assert "Hoy el tercero todavía le debe $120.000 a la quesera" in _detalle(r), nombre


def test_ok_k2_borrar_los_dos_abonos_deja_la_cifra_en_120000(
        client, base_datos, db_session, monkeypatch):
    """DOS-ABONOS y se borran LOS DOS: pagado 80.000 − 150.000 − 50.000 = −120.000,
    saldo 80.000 + 120.000 = 200.000, borrada 120.000; la verdad (saldo − borrada) =
    $80.000 que la quesera le debe, igual que UPWARD-UNPAID."""
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Ok Dos Borrados").values()
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, _dia(client, h, prov, "2026-07-08", "100"))
    _abonar(client, h, liq_id, "150000")
    _abonar(client, h, liq_id, "50000")
    monkeypatch.undo()
    for pago in _leer(client, h, liq_id)["pagos"]:
        assert client.delete(f"{API}/{liq_id}/pagos/{pago['id']}",
                             headers=h).status_code == 200
    hoy = _leer(client, h, liq_id)
    assert (D(hoy["pagado"]), D(hoy["saldo"]), hoy["pagos"]) == (
        D("-120000"), D("200000"), [])
    assert D(hoy["deuda_borrada_por_la_migracion"]) == D("120000")
    assert D(hoy["neto_a_pagar"]) == D(hoy["pagado"]) + D(hoy["saldo"])
    r = client.post(f"{API}/{liq_id}/pagar", headers=h)
    assert "lo que de verdad falta entregarle es $80.000" in _detalle(r)
