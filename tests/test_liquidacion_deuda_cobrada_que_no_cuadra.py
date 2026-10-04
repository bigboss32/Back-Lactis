"""LAS QUINCENAS QUE EL BORRADO DE PAGOS DE ANTES YA DESCUADRÓ NO SE PAGAN EN SILENCIO.

El caso de Henri, con las cifras del dueño:

  · Q1 (01–15/06): 250 L × $2.000 = $500.000, pagada con UN pago de $500.000. Corregida a
    $1.600 vale $400.000: saldo −$100.000, Henri quedó debiendo $100.000.
  · Q2 (16–30/06): 150 L × $2.000 = $300.000, se cobra esos $100.000 y queda en $200.000.

Hoy la basura de ese pago rebota (`_razon_para_no_borrar_un_pago`). Pero el código de
antes respondía 200, y las filas que dejó así siguen en la base: Q1 'parcial', pagado $0,
saldo $400.000, debiendo $0 y con la marca hacia Q2; Q2 descontando los $100.000 con un
desglose que ya suma $0. Pagar Q1 ($400.000) y Q2 ($200.000) daba 200 y 200: $600.000 de
plata por $700.000 de leche, con los dos saldos en $0 y ninguna pantalla diciéndolo.

Aquí la fila se arma COMO LA DEJÓ EL CÓDIGO DE ANTES: el mismo DELETE con ese guardia
apagado solo durante esa llamada (para esta fila es la única diferencia con `main`). Y se
mide que Pagar y Abonar rebotan en las DOS puntas con un texto cierto, que la pantalla
recibe ese mismo texto, que la salida que nombra funciona y deja la plata cuadrada, y que
la pareja sana —y su Pagar— siguen exactamente igual.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.context import RequestContext
from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import _deuda_cobrada_que_no_cuadra
from tests.conftest import auth_headers
from tests.test_liquidacion_borrar_pago_deuda_cobrada import _henri
from tests.test_liquidacion_corregir_permisos import crear_usuario_con_rol
from tests.test_liquidacion_corregir_pagada import (
    API,
    Q1,
    Q2,
    _aprobar,
    _corregir,
    _de,
    _generar,
    _leer,
    _pagar,
    _proveedor,
    _recepcion,
)

Q1_TEXTO = "01/06/2026 al 15/06/2026"
Q2_TEXTO = "16/06/2026 al 30/06/2026"
NO_CUADRAN = (
    "Los dos comprobantes ya no cuadran entre sí, y hay que revisarlos antes de entregarle "
    "plata."
)


def D(v):
    return Decimal(str(v))


def _detalle(r):
    return r.json()["error"]["detail"]


def _borrar_con_el_codigo_de_antes(client, h, monkeypatch, liq_id, pago_id):
    """El DELETE de antes del guardia. Hoy rebota; con el guardia apagado solo durante
    esta llamada responde 200, que es lo que hizo en producción."""
    import app.modules.liquidaciones.service as servicio

    hoy = client.delete(f"{API}/{liq_id}/pagos/{pago_id}", headers=h)
    assert hoy.status_code == 422, "el guardia de hoy dejó borrar el pago"
    monkeypatch.setattr(servicio, "_razon_para_no_borrar_un_pago", lambda *a, **k: None)
    antes = client.delete(f"{API}/{liq_id}/pagos/{pago_id}", headers=h)
    monkeypatch.undo()
    assert antes.status_code == 200, antes.text


def _heredada(client, h, monkeypatch):
    henri, q1, q2 = _henri(client, h)
    _borrar_con_el_codigo_de_antes(client, h, monkeypatch, q1["id"], q1["pagos"][0]["id"])
    q1, q2 = _leer(client, h, q1["id"]), _leer(client, h, q2["id"])
    # Así quedó: Q1 por pagar $400.000 sin deber nada y con la marca; Q2 descontando
    # $100.000 que su desglose ya no explica.
    assert (q1["estado"], D(q1["pagado"]), D(q1["saldo"]), D(q1["le_queda_debiendo"])) == (
        "parcial", D(0), D("400000"), D(0))
    assert q1["deuda_trasladada_a_id"] == q2["id"] and q1["pagos"] == []
    assert D(q2["saldo_anterior"]) == D("100000")
    assert sum((D(o["le_queda_debiendo"]) for o in q2["deudas_cobradas"]), D(0)) == 0
    return henri, q1, q2


def _plata(*liquidaciones):
    return sum((D(p["valor"]) for liq in liquidaciones for p in liq["pagos"]), D(0))


# ---------------------------------------------------------------------------------------
def test_la_que_dejo_la_deuda_no_se_paga_ni_se_abona(client, base_datos, monkeypatch):
    h = auth_headers(client, "admin.a")
    _, q1, q2 = _heredada(client, h, monkeypatch)

    pagar = client.post(f"{API}/{q1['id']}/pagar", headers=h)
    abonar = client.post(f"{API}/{q1['id']}/pagos",
                         json={"fecha": "2026-07-01", "valor": "100000"}, headers=h)
    print(f"\n  pagar Q1 -> {pagar.status_code}: {_detalle(pagar)}")
    print(f"  abonar Q1 -> {abonar.status_code}: {_detalle(abonar)}")
    assert (pagar.status_code, abonar.status_code) == (422, 422)
    hecho = (
        "esta liquidación: lo que el tercero quedaba debiendo en ella ($100.000) ya se le "
        f"cobró en la liquidación del {Q2_TEXTO}, pero después sus cifras cambiaron: hoy ya "
        f"no queda debiendo nada y su saldo dice $400.000 por entregar. {NO_CUADRAN}"
    )
    # Q2 está en borrador, versión 1 y sin pagos: anularla sí suelta la marca, y el
    # Administrador puede. Es el consejo de la deuda cobrada, llamado y no copiado.
    consejo = ("Anule primero esa liquidación —así esta deuda vuelve a quedar libre— y "
               "vuelva a intentarlo.")
    assert _detalle(pagar) == f"No se puede pagar {hecho} {consejo}"
    assert _detalle(abonar) == f"No se puede registrarle un pago a {hecho} {consejo}"

    # No se movió un peso, y la pantalla lee los mismos dos textos (criterios 1 y 2).
    q1 = _leer(client, h, q1["id"])
    assert (D(q1["pagado"]), q1["pagos"], D(q1["saldo"])) == (D(0), [], D("400000"))
    assert q1["avisos_deuda_cobrada"]["pagar"] == _detalle(pagar)
    assert q1["avisos_deuda_cobrada"]["registrar_pago"] == _detalle(abonar)
    assert q1["aviso_sin_un_peso_por_la_deuda"] is None


def test_la_que_cobro_no_se_paga_y_la_salida_deja_la_plata_cuadrada(
    client, base_datos, monkeypatch
):
    """La plata se pierde en Q2: pagaría $200.000 donde hoy se deben $300.000."""
    h = auth_headers(client, "admin.a")
    henri, q1, q2 = _heredada(client, h, monkeypatch)
    _aprobar(client, h, q2["id"])

    pagar = client.post(f"{API}/{q2['id']}/pagar", headers=h)
    abonar = client.post(f"{API}/{q2['id']}/pagos",
                         json={"fecha": "2026-07-01", "valor": "50000"}, headers=h)
    print(f"\n  pagar Q2 -> {pagar.status_code}: {_detalle(pagar)}")
    assert (pagar.status_code, abonar.status_code) == (422, 422)
    hecho = (
        "esta liquidación: descuenta $100.000 de lo que el tercero quedaba debiendo en la "
        f"liquidación del {Q1_TEXTO}, pero después de cobrárselo las cifras de esa "
        f"quincena cambiaron y hoy ya no queda debiendo nada. {NO_CUADRAN}"
    )
    consejo = ("Si hay que rehacerla, anúlela y vuelva a generarla: la nueva solo "
               "descontará lo que hoy se deba")
    assert _detalle(pagar) == f"No se puede pagar {hecho} {consejo}"
    assert _detalle(abonar) == f"No se puede registrarle un pago a {hecho} {consejo}"
    q2 = _leer(client, h, q2["id"])
    assert (q2["estado"], q2["pagos"]) == ("aprobada", [])
    assert q2["avisos_deuda_cobrada"] == {"pagar": _detalle(pagar),
                                          "registrar_pago": _detalle(abonar)}
    # "La deuda vieja se llevó el neto" sería falso aquí: no lo dice nadie.
    assert q2["aviso_sin_un_peso_por_la_deuda"] is None

    # SE SIGUE EL CONSEJO, y se mide con calculadora.
    r = client.post(f"{API}/{q2['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    q1 = _leer(client, h, q1["id"])
    assert q1["deuda_trasladada_a_id"] is None and q1["avisos_deuda_cobrada"] == {}
    q1 = _pagar(client, h, q1["id"])
    assert (q1["estado"], D(q1["pagado"]), D(q1["saldo"])) == ("pagada", D("400000"), D(0))
    nueva = _de(_generar(client, h, Q2), henri)
    assert (D(nueva["saldo_anterior"]), D(nueva["neto_a_pagar"])) == (D(0), D("300000"))
    _aprobar(client, h, nueva["id"])
    nueva = _pagar(client, h, nueva["id"])
    leche = D(q1["valor_total"]) + D(nueva["valor_total"])
    plata = _plata(q1, nueva)
    print(f"  leche {leche} · plata que salió {plata}")
    assert leche == plata == D("700000")


def test_con_la_que_cobro_ya_pagada_el_consejo_nombra_su_pago(
    client, base_datos, monkeypatch
):
    """La variante peligrosa: Q2 ya pagada con $200.000 antes del borrado. Pagar Q1 daba
    200 con $400.000 y la plata quedaba en $600.000 por $700.000 de leche."""
    h = auth_headers(client, "admin.a")
    henri, q1, q2 = _henri(client, h)
    _aprobar(client, h, q2["id"])
    q2 = _pagar(client, h, q2["id"])
    _borrar_con_el_codigo_de_antes(client, h, monkeypatch, q1["id"], q1["pagos"][0]["id"])

    pagar = client.post(f"{API}/{q1['id']}/pagar", headers=h)
    print(f"\n  pagar Q1 -> {pagar.status_code}: {_detalle(pagar)}")
    assert pagar.status_code == 422
    texto = _detalle(pagar)
    assert NO_CUADRAN in texto and f"en la liquidación del {Q2_TEXTO}" in texto
    assert "Esa liquidación ya tiene un pago registrado por $200.000" in texto
    assert "registre el ajuste en la quincena siguiente" in texto
    q1, q2 = _leer(client, h, q1["id"]), _leer(client, h, q2["id"])
    assert _plata(q1, q2) == D("200000")
    # Q2 'pagada': su Pagar rebota por el estado, que es lo primero; no hay clave que dar.
    assert "pagar" not in q2["avisos_deuda_cobrada"]


def test_la_que_cobro_ve_el_descuadre_aunque_la_otra_siga_debiendo_algo(
    client, base_datos, monkeypatch
):
    """Q1 pagada en dos abonos ($440.000 + $60.000), corregida a $400.000 (debe $100.000) y
    cobrada en Q2. El código de antes le borra el de $60.000: Q1 debe ahora $40.000, y Q2
    sigue descontando $100.000. Q1 ya rebotaba por "quedó debiendo"; Q2 no rebotaba."""
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri Abonos")
    _recepcion(client, h, henri, "2026-06-02", "250")
    q1 = _de(_generar(client, h, Q1), henri)
    _aprobar(client, h, q1["id"])
    for valor in ("440000", "60000"):
        r = client.post(f"{API}/{q1['id']}/pagos", json={"fecha": "2026-06-16", "valor": valor},
                        headers=h)
        assert r.status_code == 200, r.text
    dia = next(d for d in r.json()["detalles"] if d.get("deleted_at") is None)
    _corregir(client, h, q1["id"], {"motivo": "el precio eran $1.600",
                                    "precios": [{"detalle_id": dia["id"],
                                                 "precio_litro": "1600"}]})
    _recepcion(client, h, henri, "2026-06-20", "150")
    q2 = _de(_generar(client, h, Q2), henri)
    assert D(q2["saldo_anterior"]) == D("100000")
    _aprobar(client, h, q2["id"])
    pago_60 = next(p for p in _leer(client, h, q1["id"])["pagos"] if D(p["valor"]) == D(60000))
    _borrar_con_el_codigo_de_antes(client, h, monkeypatch, q1["id"], pago_60["id"])
    q1 = _leer(client, h, q1["id"])
    assert (D(q1["saldo"]), D(q1["le_queda_debiendo"])) == (D("-40000"), D("40000"))

    # Q1 sigue 'pagada' (v2, debe $40.000): su Pagar rebota por el estado, como siempre.
    pagar_q1 = client.post(f"{API}/{q1['id']}/pagar", headers=h)
    assert pagar_q1.status_code == 422
    assert _detalle(pagar_q1) == "No se puede pasar de 'pagada' a 'pagada'"

    pagar_q2 = client.post(f"{API}/{q2['id']}/pagar", headers=h)
    print(f"\n  pagar Q2 -> {pagar_q2.status_code}: {_detalle(pagar_q2)}")
    assert pagar_q2.status_code == 422
    assert ("descuenta $100.000 de lo que el tercero quedaba debiendo en la liquidación "
            f"del {Q1_TEXTO}, pero después de cobrárselo las cifras de esa quincena "
            "cambiaron y hoy queda debiendo $40.000.") in _detalle(pagar_q2)
    assert _leer(client, h, q2["id"])["pagos"] == []


def test_la_pareja_sana_se_paga_igual_que_siempre(client, base_datos):
    """Control: Henri sin el borrado. Q2 descuenta $100.000 y Q1 los debe: se paga Q2 con
    $200.000, Q1 ('pagada' debiendo $100.000) sigue rebotando con su porqué de siempre y
    la plata cierra."""
    h = auth_headers(client, "admin.a")
    _, q1, q2 = _henri(client, h)
    pagar_q1 = client.post(f"{API}/{q1['id']}/pagar", headers=h)
    assert pagar_q1.status_code == 422
    assert _detalle(pagar_q1) == "No se puede pasar de 'pagada' a 'pagada'"
    _aprobar(client, h, q2["id"])
    q2_leida = _leer(client, h, q2["id"])
    assert "pagar" not in q2_leida["avisos_deuda_cobrada"]
    assert "registrar_pago" not in q2_leida["avisos_deuda_cobrada"]
    q2 = _pagar(client, h, q2["id"])
    assert (q2["estado"], D(q2["pagado"])) == ("pagada", D("200000"))
    q1 = _leer(client, h, q1["id"])
    assert "pagar" not in q1["avisos_deuda_cobrada"]
    assert D(q1["valor_total"]) + D(q2["valor_total"]) == _plata(q1, q2) == D("700000")


def test_a_quien_no_puede_anular_no_le_nombra_el_boton(client, base_datos, db_session,
                                                      monkeypatch):
    """El consejo de la que cobró, para quien no tiene 'administrar' (el que pide Anular):
    "pídale a un Administrador". Y Compras recibe las claves de Pagar y de Abonar —el
    descuadre es un hecho de la fila— pero no la de Anular, cuyo botón le da 403."""
    h = auth_headers(client, "admin.a")
    _, q1, q2 = _heredada(client, h, monkeypatch)
    _aprobar(client, h, q2["id"])
    fila = db_session.get(Liquidacion, uuid.UUID(q2["id"]))
    db_session.refresh(fila)
    sin_administrar = RequestContext(empresa_id=base_datos["empresa_a"].id,
                                     user_id=uuid.uuid4(), permisos=set())
    texto = _deuda_cobrada_que_no_cuadra(fila, "pagar", sin_administrar)
    assert texto.endswith(
        "Si hay que rehacerla, pídale a un Administrador de la empresa que la anule y la "
        "vuelva a generar: la nueva solo descontará lo que hoy se deba")
    assert "anúlela" not in texto

    crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Compras", "rol.compras.nc")
    hc = auth_headers(client, "rol.compras.nc")
    for liq in (q1, q2):
        leida = client.get(f"{API}/{liq['id']}", headers=hc).json()
        assert {"pagar", "registrar_pago"} <= set(leida["avisos_deuda_cobrada"])
        assert "anular" not in leida["avisos_deuda_cobrada"]
    assert client.post(f"{API}/{q2['id']}/pagar", headers=hc).status_code == 403


@pytest.mark.parametrize("rol", ["Compras", "Consulta", "Supervisor", "Contador"])
def test_el_descuadre_lo_lee_todo_el_que_ve_la_fila(rol, client, base_datos, db_session,
                                                    monkeypatch):
    """EL DESCUADRE ES UN HECHO DE LA FILA, NO DE QUIEN LA MIRA. Henri con Q2 aprobada: Q1
    'parcial' v2 por $400.000 debiendo $0 con la marca, Q2 $300.000 − $100.000 = $200.000.
    Pagar y Abonar le dan 422 al Administrador en las dos. La clave solo le llegaba a quien
    tiene 'administrar', y sin ella la pantalla de Compras o Consulta decía "el pago lo
    registra un Administrador de la empresa cuando se entregue el dinero": un pago que el
    servidor le rebota también a él.

    Cada rol que lee la liquidación recibe el MISMO hecho del 422 del Administrador, con el
    consejo dicho para él: nada de "anúlela", "bórrelo" ni "vuelva a intentarlo" —anular,
    borrar y pagar le dan 403—, sino lo que tiene que pedirle a un Administrador. Las
    demás claves (anular, corregir, eliminar_pago) siguen filtradas por el permiso."""
    h = auth_headers(client, "admin.a")
    _, q1, q2 = _heredada(client, h, monkeypatch)
    _aprobar(client, h, q2["id"])
    del_admin = {}
    for liq in (q1, q2):
        for accion, r in (
            ("pagar", client.post(f"{API}/{liq['id']}/pagar", headers=h)),
            ("registrar_pago", client.post(f"{API}/{liq['id']}/pagos",
                                           json={"fecha": "2026-07-01", "valor": "50000"},
                                           headers=h)),
        ):
            assert r.status_code == 422
            del_admin[liq["id"], accion] = _detalle(r)

    hecho_q1 = (
        "esta liquidación: lo que el tercero quedaba debiendo en ella ($100.000) ya se le "
        f"cobró en la liquidación del {Q2_TEXTO}, pero después sus cifras cambiaron: hoy ya "
        f"no queda debiendo nada y su saldo dice $400.000 por entregar. {NO_CUADRAN}"
    )
    hecho_q2 = (
        "esta liquidación: descuenta $100.000 de lo que el tercero quedaba debiendo en la "
        f"liquidación del {Q1_TEXTO}, pero después de cobrárselo las cifras de esa "
        f"quincena cambiaron y hoy ya no queda debiendo nada. {NO_CUADRAN}"
    )
    pidale_q1 = ("Pídale a un Administrador de la empresa que anule primero esa "
                 "liquidación —así esta deuda vuelve a quedar libre— y que después ")
    pidale_q2 = ("Si hay que rehacerla, pídale a un Administrador de la empresa que la "
                 "anule y la vuelva a generar: la nueva solo descontará lo que hoy se deba")
    esperado = {
        (q1["id"], "pagar"): f"No se puede pagar {hecho_q1} {pidale_q1}pague esta.",
        (q1["id"], "registrar_pago"):
            f"No se puede registrarle un pago a {hecho_q1} {pidale_q1}le registre el pago a "
            "esta.",
        (q2["id"], "pagar"): f"No se puede pagar {hecho_q2} {pidale_q2}",
        (q2["id"], "registrar_pago"): f"No se puede registrarle un pago a {hecho_q2} {pidale_q2}",
    }
    # El hecho es el del 422 del Administrador, letra por letra; cambia solo el consejo.
    assert del_admin[q1["id"], "pagar"] == (
        f"No se puede pagar {hecho_q1} Anule primero esa liquidación —así esta deuda vuelve "
        "a quedar libre— y vuelva a intentarlo.")
    assert del_admin[q2["id"], "pagar"] == (
        f"No se puede pagar {hecho_q2} Si hay que rehacerla, anúlela y vuelva a generarla: "
        "la nueva solo descontará lo que hoy se deba")

    usuario = f"rol.{rol.lower()}.descuadre"
    crear_usuario_con_rol(db_session, base_datos["empresa_a"], rol, usuario)
    hr = auth_headers(client, usuario)
    for liq in (q1, q2):
        avisos = client.get(f"{API}/{liq['id']}", headers=hr).json()["avisos_deuda_cobrada"]
        print(f"\n  {rol} lee {liq['periodo_inicio']}: {avisos.get('pagar')}")
        for accion in ("pagar", "registrar_pago"):
            texto = avisos[accion]
            assert texto == esperado[liq["id"], accion]
            fin_del_hecho = del_admin[liq["id"], accion].index(NO_CUADRAN) + len(NO_CUADRAN)
            assert texto[:fin_del_hecho] == del_admin[liq["id"], accion][:fin_del_hecho]
            for boton in ("anúlela", "Anule", "bórrelo", "vuelva a intentarlo"):
                assert boton not in texto
        assert not {"anular", "corregir", "eliminar_pago"} & set(avisos)
        # Y el botón sigue siendo del Administrador: a este rol Pagar y Abonar le dan 403.
        assert client.post(f"{API}/{liq['id']}/pagar", headers=hr).status_code == 403
        assert client.post(f"{API}/{liq['id']}/pagos", json={
            "fecha": "2026-07-01", "valor": "50000"}, headers=hr).status_code == 403
    q1, q2 = _leer(client, h, q1["id"]), _leer(client, h, q2["id"])
    assert _plata(q1, q2) == D(0)


@pytest.mark.parametrize("saldo, no_cuadra", [("-100000", False), ("0", True)])
def test_la_pregunta_no_marca_la_marca_sana(saldo, no_cuadra):
    """Sin base de datos: la que dejó la deuda con saldo negativo es la sana —la marca se
    puso porque debía, y sus cifras se congelan—; con saldo en cero ya no debe lo que la
    otra le cobró. La que cobró una deuda que la otra sigue debiendo entera es sana."""
    origen = Liquidacion(estado="parcial", saldo=D(saldo), saldo_anterior=D(0),
                         deuda_trasladada_a_id=uuid.uuid4())
    texto = _deuda_cobrada_que_no_cuadra(origen, "pagar")
    assert (texto is not None) is no_cuadra
    if no_cuadra:
        assert "pero después sus cifras cambiaron: hoy ya no queda debiendo nada." in texto
    cobro = Liquidacion(estado="aprobada", saldo=D("200000"), saldo_anterior=D("100000"))
    cobro.deudas_cobradas = [Liquidacion(saldo=D(saldo), periodo_inicio=date(2026, 6, 1),
                                         periodo_fin=date(2026, 6, 15))]
    texto = _deuda_cobrada_que_no_cuadra(cobro, "pagar")
    assert (texto is not None) is no_cuadra
