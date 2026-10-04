"""BORRAR EL PAGO DE UNA QUINCENA CUYA DEUDA YA SE COBRÓ EN OTRA: REBOTA Y NO MUEVE NADA.

El caso de Henri, con las cifras del dueño:

  · Q1 (01–15/06): 250 L × $2.000 = $500.000, aprobada y pagada con UN pago de $500.000.
  · Con "Corregir esta quincena" el precio pasa a $1.600: valor $400.000 contra $500.000
    entregados. Saldo −$100.000: Henri quedó debiendo $100.000.
  · Q2 (16–30/06): 150 L × $2.000 = $300.000, se cobra esos $100.000 y queda con neto
    $200.000. Q1 queda marcada: su deuda viajó a Q2.

Hasta este arreglo, el botón de la basura del pago de $500.000 de Q1 respondía 200: Q1
pasaba a 'parcial' con $400.000 por pagar y debiendo $0, Q2 seguía descontando los
$100.000, y pagando las dos salían $600.000 por $700.000 de leche. Henri quedaba con
$100.000 a su favor que ninguna pantalla mostraba.

Ahora rebota con 422 antes de tocar nada —ni el pago, ni la foto de la transferencia—, y
la cuenta del dueño cierra: leche $700.000 = plata $500.000 + $200.000.
"""
import uuid
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion, PagoLiquidacion
from tests.ayudas_imagenes import JPEG
from tests.ayudas_r2 import enchufar
from tests.conftest import auth_headers
from tests.test_liquidacion_corregir_pagada import (
    API,
    Q2,
    _aprobar,
    _corregir,
    _de,
    _generar,
    _leer,
    _pagar,
    _proveedor,
    _quincena_pagada,
    _recepcion,
)


def D(v):
    return Decimal(str(v))


def _detalle(r):
    return r.json()["error"]["detail"]


def _henri(client, h):
    """Q1 pagada con $500.000 y corregida a $400.000 (debe $100.000); Q2 se los cobra."""
    henri = _proveedor(client, h)
    q1 = _quincena_pagada(client, h, henri)  # 250 L × $2.000, pagada con $500.000
    dia = next(d for d in q1["detalles"] if d.get("deleted_at") is None)
    q1 = _corregir(client, h, q1["id"], {
        "motivo": "el precio eran $1.600",
        "precios": [{"detalle_id": dia["id"], "precio_litro": "1600"}],
    })
    assert (q1["estado"], q1["version"]) == ("pagada", 2)
    assert D(q1["saldo"]) == D("-100000") and D(q1["le_queda_debiendo"]) == D("100000")
    _recepcion(client, h, henri, "2026-06-20", "150")
    q2 = _de(_generar(client, h, Q2), henri)
    assert D(q2["saldo_anterior"]) == D("100000") and D(q2["neto_a_pagar"]) == D("200000")
    q1 = _leer(client, h, q1["id"])
    assert q1["deuda_trasladada_a_id"] == q2["id"]
    return henri, q1, q2


def _sigue_igual(client, h, q1_id, q2_id):
    """Nada se movió: Q1 sigue pagada debiendo $100.000 con su pago, y el desglose de Q2
    suma exacto su renglón de deuda vieja."""
    q1 = _leer(client, h, q1_id)
    q2 = _leer(client, h, q2_id)
    assert q1["estado"] == "pagada" and q1["version"] == 2
    assert D(q1["pagado"]) == D("500000") and D(q1["saldo"]) == D("-100000")
    assert D(q1["le_queda_debiendo"]) == D("100000")
    assert [D(p["valor"]) for p in q1["pagos"]] == [D("500000")]
    desglose = sum((D(o["le_queda_debiendo"]) for o in q2["deudas_cobradas"]), D(0))
    assert desglose == D(q2["saldo_anterior"]) == D("100000")
    return q1, q2


def test_borrar_el_pago_de_la_que_dejo_la_deuda_rebota_y_la_plata_cierra(
    client, base_datos, monkeypatch
):
    import app.modules.liquidaciones.service as servicio

    r2 = enchufar(monkeypatch, servicio)
    h = auth_headers(client, "admin.a")
    _, q1, q2 = _henri(client, h)
    pago = q1["pagos"][0]
    subida = client.post(
        f"{API}/{q1['id']}/pagos/{pago['id']}/adjuntos",
        files=[("files", ("transferencia.jpg", JPEG, "image/jpeg"))],
        headers=h,
    )
    assert subida.status_code == 201, subida.text

    r = client.delete(f"{API}/{q1['id']}/pagos/{pago['id']}", headers=h)
    print(f"\n  DELETE pago de $500.000 de Q1 -> {r.status_code}: {r.text[:300]}")
    assert r.status_code == 422, "el servidor dejó borrar el pago de un origen congelado"
    texto = _detalle(r)
    assert "($100.000) ya se le cobró en la liquidación del 16/06/2026 al 30/06/2026" in texto
    # Q2 está en borrador, sin pagos: anularla sí destraba el borrado, y el admin puede.
    # Sin "vuelva a generar las dos": Q1 ya es la versión 2 y no se puede anular.
    assert "Anule primero esa liquidación" in texto
    assert "volver a generar las dos" not in texto

    # EL PAGO Y SU SOPORTE SIGUEN VIVOS: un rebote no se lleva la foto de la transferencia.
    q1, q2 = _sigue_igual(client, h, q1["id"], q2["id"])
    assert q1["pagos"][0]["adjuntos_count"] == 1
    assert len(r2.objetos) == 1 and r2.borrados == []

    # LA PANTALLA LEE EL MISMO TEXTO para no pintar la basura (criterio 2).
    assert q1["avisos_deuda_cobrada"]["eliminar_pago"] == texto

    # Y la cuenta del dueño: aprobar y pagar Q2. Leche $700.000 = plata que salió.
    _aprobar(client, h, q2["id"])
    _pagar(client, h, q2["id"])
    q1, q2 = _leer(client, h, q1["id"]), _leer(client, h, q2["id"])
    leche = D(q1["valor_total"]) + D(q2["valor_total"])
    plata = sum((D(p["valor"]) for p in q1["pagos"] + q2["pagos"]), D(0))
    print(f"  leche {leche} · plata que salió {plata}")
    assert leche == plata == D("700000")


def test_con_la_siguiente_ya_pagada_tambien_rebota(client, base_datos):
    """La variante peligrosa: Q2 ya aprobada y pagada con $200.000 (el papel en la mano de
    Henri) antes de oprimir la basura."""
    h = auth_headers(client, "admin.a")
    _, q1, q2 = _henri(client, h)
    _aprobar(client, h, q2["id"])
    _pagar(client, h, q2["id"])
    r = client.delete(f"{API}/{q1['id']}/pagos/{q1['pagos'][0]['id']}", headers=h)
    print(f"\n  DELETE con Q2 pagada -> {r.status_code}: {r.text[:400]}")
    assert r.status_code == 422
    texto = _detalle(r)
    # Q2 ya tiene un pago de $200.000: el consejo lo dice con la cifra y avisa que los
    # soportes no vuelven, en vez de mandar a anularla en seco.
    assert "un pago registrado por $200.000" in texto, texto
    assert "soportes, que no se recuperan" in texto, texto
    assert "registre el ajuste en la quincena siguiente" in texto, texto
    q1, q2 = _sigue_igual(client, h, q1["id"], q2["id"])
    leche = D(q1["valor_total"]) + D(q2["valor_total"])
    plata = sum((D(p["valor"]) for p in q1["pagos"] + q2["pagos"]), D(0))
    assert leche == plata == D("700000")


def test_a_la_que_cobro_la_deuda_si_se_le_puede_borrar_el_pago(client, base_datos):
    """La marca está en la que DEJÓ la deuda. Borrarle el pago a la que la COBRÓ es el
    paso previo a anularla —la salida que nombra el mensaje— y tiene que seguir abierto."""
    h = auth_headers(client, "admin.a")
    _, q1, q2 = _henri(client, h)
    _aprobar(client, h, q2["id"])
    q2 = _pagar(client, h, q2["id"])
    r = client.delete(f"{API}/{q2['id']}/pagos/{q2['pagos'][0]['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert client.post(f"{API}/{q2['id']}/anular", headers=h).status_code == 200
    # Anulada la que la cobró, la deuda vuelve a quedar libre y el pago de Q1 ya se puede
    # borrar: el consejo era cierto.
    q1 = _leer(client, h, q1["id"])
    assert q1["deuda_trasladada_a_id"] is None and q1["avisos_deuda_cobrada"] == {}
    r = client.delete(f"{API}/{q1['id']}/pagos/{q1['pagos'][0]['id']}", headers=h)
    assert r.status_code == 200, r.text


def test_la_fila_que_ya_quedo_debiendo_cero_no_dice_cero(client, base_datos, db_session):
    """Las filas que el borrado de antes ya dejó así siguen en la base: Q1 en 'parcial',
    pagado $0, saldo $400.000, debiendo $0 y con la marca puesta. El aviso decía "lo que el
    tercero quedó debiendo ($0) ya se le cobró", que es falso: se le cobraron $100.000, y
    Q2 es la única deuda que se cobró, así que esa es la cifra."""
    h = auth_headers(client, "admin.a")
    _, q1, q2 = _henri(client, h)
    # Lo que hacía `eliminar_pago` antes del guardia, escrito a mano.
    fila = db_session.get(Liquidacion, uuid.UUID(q1["id"]))
    db_session.delete(db_session.get(PagoLiquidacion, uuid.UUID(q1["pagos"][0]["id"])))
    fila.pagado, fila.saldo, fila.estado = D("0"), D("400000"), "parcial"
    db_session.commit()
    db_session.expire_all()
    leida = _leer(client, h, q1["id"])
    assert D(leida["le_queda_debiendo"]) == 0 and leida["deuda_trasladada_a_id"] == q2["id"]

    r = client.post(f"{API}/{q1['id']}/corregir/previsualizar",
                    json={"motivo": "mirar"}, headers=h)
    texto = _detalle(r)
    print(f"\n  corregir -> {r.status_code}: {texto}")
    assert r.status_code == 422
    assert "($0)" not in texto
    assert "lo que el tercero quedaba debiendo ($100.000) ya se le cobró en la liquidación " \
           "del 16/06/2026 al 30/06/2026" in texto
    assert leida["avisos_deuda_cobrada"]["corregir"] == texto
