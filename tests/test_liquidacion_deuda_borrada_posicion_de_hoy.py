"""EL AVISO DE LA DEUDA BORRADA DICE CÓMO ESTÁ LA FILA HOY.

El guardia de la deuda que borró la migración de los abonos decía siempre que pagar era
"pagarle a alguien que todavía debe". En la mitad de las formas que dejó producción es
al revés: la quesera es la que debe. La cuenta del dueño, con calculadora:

    lo que de verdad falta entregar = saldo − borrada   (negativo: lo que él debe)

  · PLAIN (migrada sin tocar): $180.000 contra $300.000, pagado −$120.000, saldo 0.
    0 − 120.000 = −120.000: él debe $120.000.
  · UPWARD-UNPAID (corregida hacia arriba antes del guardia, sin pagar): valor $380.000,
    pagado −$120.000, saldo $200.000. 200.000 − 120.000 = $80.000: la quesera le debe
    $80.000, y pagarle el saldo le entregaría $120.000 de más.
  · EN CERO: corregida hasta $300.000, saldo $120.000: no falta nada, y pagar el saldo
    igual entregaría $120.000 de más.
  · BORRADA Y COBRADA: valor $100.000, saldo −$80.000 que ya cobró otra quincena. Debe
    $200.000 por esta quincena, de los que $80.000 ya se le cobraron: le faltan $120.000.

Y el PUT de observaciones: la deuda borrada va de primera (antes mandaba a "Corregir esta
quincena", que ahí rebota siempre) y "ya se pagó" solo sale cuando es verdad.
"""
import uuid
from datetime import date
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import (
    _antes_del_guardia,
    _corregir,
    _entregado,
    _escribir,
    _migradas,
)
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _dia, _leer, _migrada

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
CORREGIR = "Corregir esta quincena"


def D(v):
    return Decimal(str(v))


def _aviso_del_anticipo(client, h, liq_id):
    lista = client.get(ANT, params={"page_size": 200}, headers=h).json()["items"]
    return next(a for a in lista if a["liquidacion_id"] == liq_id)["candado_aviso"]


def _los_tres_rebotes(client, h, liq_id):
    """Pagar, abonar y el candado del anticipo: los tres dicen lo mismo de la fila."""
    pagar = client.post(f"{API}/{liq_id}/pagar", headers=h)
    abono = client.post(f"{API}/{liq_id}/pagos", json={"fecha": "2026-08-10",
                                                       "valor": "1000"}, headers=h)
    assert pagar.status_code == abono.status_code == 422, (pagar.text, abono.text)
    return [_detalle(pagar), _detalle(abono), _aviso_del_anticipo(client, h, liq_id)]


# ---------------------------------------------------------------------------------------
def test_la_migrada_sin_tocar_dice_que_el_debe_120000(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    (_, liq_id), = _migradas(client, h, db_session, "Plain Posicion").values()
    hoy = _leer(client, h, liq_id)
    # Con calculadora: neto −120.000, nada entregado → él debe $120.000.
    assert D(hoy["neto_a_pagar"]) - _entregado(hoy) == D("-120000")
    for texto in _los_tres_rebotes(client, h, liq_id):
        print(f"\n  {texto}")
        assert "($120.000)" in texto and "repararla" in texto
        assert "Hoy el tercero todavía le debe $120.000 a la quesera" in texto
        assert "El saldo dice" not in texto


def test_la_corregida_hacia_arriba_sin_pagar_dice_que_la_quesera_le_debe_80000(
        client, base_datos, db_session, monkeypatch):
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Arriba Posicion").values()
    grande = _dia(client, h, prov, "2026-07-08", "100")            # $200.000
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, grande)
    monkeypatch.undo()
    hoy = _leer(client, h, liq_id)
    assert (D(hoy["neto_a_pagar"]), D(hoy["saldo"]), _entregado(hoy)) == (
        D("80000"), D("200000"), D("0"))
    assert D(hoy["deuda_borrada_por_la_migracion"]) == D("120000")
    for texto in _los_tres_rebotes(client, h, liq_id):
        print(f"\n  {texto}")
        assert "($120.000)" in texto and "repararla" in texto
        assert "El saldo dice $200.000, pero lo que de verdad falta entregarle es $80.000" \
            in texto
        assert "le entregaría $120.000 de más" in texto
        assert "todavía" not in texto


def test_en_cero_dice_que_no_falta_nada(client, base_datos, db_session):
    """Parcial v2: $300.000 − $300.000 = neto 0, pagado −$120.000 → saldo $120.000."""
    h = auth_headers(client, "admin.a")
    prov = client.post(f"{V}/proveedores", json={"nombre": "En Cero", "vereda": "X",
                                                 "precio_litro": "2000"}, headers=h)
    fila = _escribir(db_session, base_datos["empresa_a"].id, uuid.UUID(prov.json()["id"]),
                     estado="parcial", version=2, valor_total="300000",
                     anticipos="300000", pagado="-120000")
    db_session.commit()
    assert fila.saldo == D("120000")
    r = client.post(f"{API}/{fila.id}/pagar", headers=h)
    print(f"\n  {_detalle(r)}")
    assert r.status_code == 422
    assert "El saldo dice $120.000, pero de verdad no falta entregarle nada" in _detalle(r)
    assert "le entregaría $120.000 de más" in _detalle(r)
    # El saldo entero ($120.000) es lo borrado: son "esos" $120.000, no "los otros", que
    # se leía como si hubiera otros $120.000 aparte. 120.000 − 120.000 = 0 por entregar.
    assert ("no falta entregarle nada: esos $120.000 son la deuda que borró la migración"
            in _detalle(r))
    assert "los otros" not in _detalle(r)


def test_con_la_deuda_ya_cobrada_en_otra_no_la_cuenta_como_pendiente(
        client, base_datos, db_session):
    """Pagada v2, valor $100.000 − $300.000 = neto −$200.000, pagado −$120.000, saldo
    −$80.000; esos $80.000 ya los cobró la segunda de julio. Él debía $200.000 por esta
    quincena; $80.000 ya se le cobraron, así que hoy le faltan $120.000."""
    h = auth_headers(client, "admin.a")
    _, migrada = _migrada(client, h, db_session, "Cobrada Posicion")
    fila = db_session.get(Liquidacion, uuid.UUID(migrada["id"]))
    fila.estado, fila.version = "pagada", 2
    fila.valor_bruto = fila.valor_total = D("100000")
    fila.saldo = D("-80000")
    cobra = Liquidacion(
        empresa_id=fila.empresa_id, tipo="proveedor", proveedor_id=fila.proveedor_id,
        periodo_inicio=date(2026, 7, 16), periodo_fin=date(2026, 7, 31),
        total_litros=D("100"), valor_bruto=D("200000"), valor_total=D("200000"),
        anticipos=D("0"), saldo_anterior=D("80000"), pagado=D("0"), saldo=D("120000"),
        estado="aprobada")
    db_session.add(cobra)
    db_session.flush()
    fila.deuda_trasladada_a_id = cobra.id
    db_session.commit()
    r = client.post(f"{API}/{migrada['id']}/anular", headers=h)
    print(f"\n  {_detalle(r)}")
    assert r.status_code == 422
    assert ("Hoy el tercero todavía le debe $120.000 a la quesera, aparte de los $80.000 "
            "que ya se le cobraron en otra quincena") in _detalle(r)
    assert "Anule primero" not in _detalle(r)


# ---------------------------------------------------------------------------------------
# EL PUT DE OBSERVACIONES (PUT /liquidaciones/{id})
# ---------------------------------------------------------------------------------------
def _obs(client, h, liq_id):
    return client.put(f"{API}/{liq_id}", json={"observaciones": "nota"}, headers=h)


def _quincena(client, h, nombre, litros, adelanto, precio="1800"):
    """litros × $1.800 en junio contra un adelanto; aprobada."""
    r = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                              "precio_litro": precio}, headers=h)
    prov = r.json()["id"]
    assert client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                                  "cantidad_litros": litros}, headers=h).status_code == 201
    if adelanto != "0":
        assert client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                      "fecha": "2026-06-01", "valor": adelanto},
                           headers=h).status_code == 201
    g = client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                            "periodo_fin": "2026-06-15",
                                            "tipo": "proveedor"}, headers=h).json()
    liq = next(x for x in g["generadas"] if x["proveedor_id"] == prov)["id"]
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    return prov, liq


def _dia_olvidado(client, h, prov, litros="20"):
    r = client.post(REC, json={"fecha": "2026-06-05", "proveedor_id": prov,
                               "cantidad_litros": litros}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_observaciones_de_la_migrada_dicen_la_deuda_borrada_y_no_mandan_a_corregir(
        client, base_datos, db_session, monkeypatch):
    h = auth_headers(client, "admin.a")
    filas = _migradas(client, h, db_session, "Obs Plain", "Obs Arriba")
    prov, arriba = filas["Obs Arriba"]
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, arriba, _dia(client, h, prov, "2026-07-08", "100"))
    monkeypatch.undo()
    for nombre, (_, liq_id) in filas.items():
        antes = _leer(client, h, liq_id)["observaciones"]
        r = _obs(client, h, liq_id)
        print(f"\n  {nombre}: {_detalle(r)}")
        assert r.status_code == 422
        assert _detalle(r).startswith(
            "No se puede cambiarle las observaciones a esta quincena: viene de antes de "
            "que existieran los abonos")
        assert "($120.000)" in _detalle(r) and "repararla" in _detalle(r)
        assert CORREGIR not in _detalle(r) and "ya se pagó" not in _detalle(r)
        assert _leer(client, h, liq_id)["observaciones"] == antes


def test_observaciones_de_la_pagada_sin_un_peso_no_dicen_ya_se_pago(
        client, base_datos, db_session):
    """100 L × $1.800 = $180.000 contra $300.000, 'pagada' por el botón de antes con
    pagado $0: ningún pago, solo el adelanto. 'Corregir esta quincena' sí sirve aquí, y se
    comprueba: la salida que nombra el mensaje existe."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h, "Obs Sin Peso", "100", "300000")
    db_session.get(Liquidacion, uuid.UUID(liq)).estado = "pagada"
    db_session.commit()
    r = _obs(client, h, liq)
    print(f"\n  {_detalle(r)}")
    assert r.status_code == 422
    # Sin "sin que saliera un peso": el adelanto de $300.000 salió de la caja.
    assert _detalle(r).startswith(
        "Esta quincena quedó cerrada como pagada sin saldo por entregar, porque los "
        "anticipos que se le aplicaron ($300.000) pasaron de su valor ($180.000) y el "
        "tercero le quedó debiendo $120.000")
    assert "ya se pagó" not in _detalle(r) and CORREGIR in _detalle(r)
    prev = client.post(f"{API}/{liq}/corregir/previsualizar", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [_dia_olvidado(client, h, prov)]},
        headers=h)
    assert prev.status_code == 200, prev.text


def test_observaciones_dicen_lo_que_de_verdad_paso(client, base_datos, db_session):
    """Pagada con plata ("ya se pagó" es verdad), parcial con un abono, corregida en
    'parcial' sin ningún pago, y el flete pagado (que no se corrige: sin la salida)."""
    h = auth_headers(client, "admin.a")
    _, pagada = _quincena(client, h, "Obs Pagada", "100", "50000")          # $130.000
    assert client.post(f"{API}/{pagada}/pagar", headers=h).status_code == 200
    _, abonada = _quincena(client, h, "Obs Abonada", "100", "50000")
    assert client.post(f"{API}/{abonada}/pagos", json={"fecha": "2026-06-20",
                                                       "valor": "30000"},
                       headers=h).status_code == 200
    # $180.000 cubiertos exacto por el adelanto: Pagar la cierra sin renglón; un día
    # olvidado de 20 L ($36.000) la deja 'parcial' v2 con pagado $0.
    prov_c, corregida = _quincena(client, h, "Obs Corregida", "100", "180000")
    assert client.post(f"{API}/{corregida}/pagar", headers=h).status_code == 200
    r = client.post(f"{API}/{corregida}/corregir", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [_dia_olvidado(client, h, prov_c)]},
        headers=h)
    assert r.status_code == 200 and r.json()["estado"] == "parcial", r.text
    assert D(r.json()["pagado"]) == 0 and r.json()["pagos"] == []
    t = client.post(f"{V}/transportadores", json={"nombre": "Obs Flete",
                                                  "valor_transporte": "100"},
                    headers=h).json()["id"]
    flete = _escribir(db_session, base_datos["empresa_a"].id, uuid.UUID(t),
                      tipo="transportador", estado="pagada", valor_total="90000",
                      pagado="90000", pagos=("90000",))
    db_session.commit()

    esperado = {
        pagada: ("Esta quincena ya se pagó:", True),
        abonada: ("Esta quincena ya tiene pagos registrados:", True),
        corregida: ("Esta quincena ya emitió un comprobante corregido:", True),
        str(flete.id): ("Esta quincena ya se pagó:", False),
    }
    for liq_id, (inicio, con_salida) in esperado.items():
        r = _obs(client, h, liq_id)
        print(f"\n  {_detalle(r)}")
        assert r.status_code == 422
        assert _detalle(r).startswith(inicio), _detalle(r)
        assert (CORREGIR in _detalle(r)) is con_salida, _detalle(r)
    # "ya se pagó" solo donde salió la plata entera.
    for liq_id in (abonada, corregida):
        assert "ya se pagó" not in _detalle(_obs(client, h, liq_id))

    # Control: en una aprobada se escriben como siempre.
    _, aprobada = _quincena(client, h, "Obs Aprobada", "100", "0")
    r = _obs(client, h, aprobada)
    assert r.status_code == 200 and r.json()["observaciones"] == "nota", r.text
