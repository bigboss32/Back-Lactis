"""LA PANTALLA RECIBE EL 422 DE CADA BOTÓN QUE LA DEUDA BORRADA O LA DEUDA VIEJA TRABAN.

Dos campos nuevos de `LiquidacionRead`, y los dos salen de las MISMAS funciones que usan
los guardias, no de una copia:

  · `avisos_deuda_borrada`: en la quincena a la que la migración de los abonos le borró la
    deuda, el texto exacto del 422 de Corregir, Pagar, Abonar y Anular. La pantalla tenía
    su propia redacción para esos candados, y medida no era la del servidor (en la de
    Arriba v2 el candado decía "le pagaría esos $ 120.000 de más" y el 422 "lo que de
    verdad falta entregarle es $80.000"). Solo las acciones que quien mira puede oprimir.
  · `aviso_sin_un_peso_por_la_deuda`: el 422 de Pagar cuando la deuda de la quincena pasada
    se llevó el neto ($120.000 de leche contra $120.000 que quedó debiendo). La pantalla
    copiaba las tres condiciones para esconder "Marcar pagada"; ahora pregunta la de Pagar
    entera, en su orden.
"""
import uuid
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_corregir_permisos import crear_usuario_con_rol
from tests.test_liquidacion_migrada_deuda_borrada import (
    API,
    _detalle,
    _dia,
    _leer,
    _migrada,
    _proveedor,
)
from tests.test_liquidacion_pagada_neto_en_cero_por_la_deuda import _adelanto, _generar


def D(v):
    return Decimal(str(v))


def _los_cuatro_422(client, h, liq, olvidado):
    cuerpo = {"motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}
    respuestas = {
        "corregir": client.post(f"{API}/{liq}/corregir", json=cuerpo, headers=h),
        "pagar": client.post(f"{API}/{liq}/pagar", headers=h),
        "registrar_pago": client.post(f"{API}/{liq}/pagos",
                                      json={"fecha": "2026-08-10", "valor": "10000"},
                                      headers=h),
        "anular": client.post(f"{API}/{liq}/anular", headers=h),
    }
    for accion, r in respuestas.items():
        assert r.status_code == 422, (accion, r.text)
    return {accion: _detalle(r) for accion, r in respuestas.items()}


def test_la_migrada_trae_el_422_de_cada_boton(client, base_datos, db_session):
    """90 L × $2.000 = $180.000 contra $300.000 de adelanto, 'pagada' con el botón de antes
    y migrada: pagado −$120.000, saldo $0. Hoy el tercero todavía debe $120.000."""
    h = auth_headers(client, "admin.a")
    prov, migrada = _migrada(client, h, db_session)
    olvidado = _dia(client, h, prov, "2026-07-08", "25")
    rebotes = _los_cuatro_422(client, h, migrada["id"], olvidado)
    leida = _leer(client, h, migrada["id"])
    for accion, texto in rebotes.items():
        print(f"\n  {accion}: {texto}")
    assert leida["avisos_deuda_borrada"] == rebotes
    assert rebotes["pagar"].startswith(
        "No se puede pagar esta quincena: viene de antes de que existieran los abonos, y el "
        "sistema de esa época le borró lo que el tercero quedaba debiendo ($120.000).")
    assert rebotes["registrar_pago"].startswith("No se puede registrarle un pago a esta")
    # La posición de hoy del aviso rojo va adentro de cada uno, letra por letra.
    for texto in rebotes.values():
        assert leida["aviso_deuda_borrada"] in texto
    # Pagar rebota por la deuda borrada, que va primero: no por la deuda vieja.
    assert leida["aviso_sin_un_peso_por_la_deuda"] is None


def test_quien_no_puede_oprimirlos_no_los_recibe(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, migrada = _migrada(client, h, db_session, "Migrada Compras")
    crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Compras", "avisos.compras")
    hc = auth_headers(client, "avisos.compras")
    leida = _leer(client, hc, migrada["id"])
    assert leida["avisos_deuda_borrada"] == {}
    assert leida["aviso_deuda_borrada"] is not None
    assert client.post(f"{API}/{migrada['id']}/pagar", headers=hc).status_code == 403


def test_salen_del_mismo_guardia(client, base_datos, db_session, monkeypatch):
    """Con `_exigir_sin_deuda_borrada` apagado —el código de agosto que reproducen las
    pruebas de la migración— el 422 deja de salir y el campo también: no hay una segunda
    pregunta escondida en la respuesta."""
    import app.modules.liquidaciones.service as servicio

    h = auth_headers(client, "admin.a")
    _, migrada = _migrada(client, h, db_session, "Migrada Guardia")
    assert _leer(client, h, migrada["id"])["avisos_deuda_borrada"]
    monkeypatch.setattr(servicio, "_exigir_sin_deuda_borrada", lambda *a, **k: None)
    assert _leer(client, h, migrada["id"])["avisos_deuda_borrada"] == {}


def test_la_fila_sana_no_trae_ninguno(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Sana Avisos")
    _dia(client, h, prov, "2026-06-02", "90")
    liq = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    leida = _leer(client, h, liq)
    assert (leida["avisos_deuda_borrada"], leida["aviso_sin_un_peso_por_la_deuda"]) == ({}, None)
    # Y en el listado, lo mismo para todas las filas.
    for fila in client.get(API, headers=h).json()["items"]:
        assert fila["avisos_deuda_borrada"] == {}


def test_el_neto_que_se_llevo_la_deuda_vieja_trae_el_422_de_pagar(client, base_datos, db_session):
    """Q1: 90 L × $2.000 = $180.000 contra $300.000 → debe $120.000. Q2: 60 L × $2.000 =
    $120.000, sin adelanto: 120.000 − 0 − 120.000 = $0. En borrador Pagar rebota por el
    estado, así que el campo va en None; aprobada, rebota por la deuda vieja, y el campo
    es ese 422 letra por letra."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Pura Avisos")
    _dia(client, h, prov, "2026-06-02", "90")
    _adelanto(client, h, prov, "2026-06-01", "300000")
    q1 = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{q1}/aprobar", headers=h).status_code == 200
    _dia(client, h, prov, "2026-06-17", "60")
    q2 = _generar(client, h, prov, "2026-06-16", "2026-06-30")
    borrador = _leer(client, h, q2)
    assert (D(borrador["valor_total"]), D(borrador["saldo_anterior"]), D(borrador["saldo"])) == (
        D("120000"), D("120000"), D(0))
    assert borrador["aviso_sin_un_peso_por_la_deuda"] is None
    assert client.post(f"{API}/{q2}/aprobar", headers=h).status_code == 200
    pagar = client.post(f"{API}/{q2}/pagar", headers=h)
    assert pagar.status_code == 422
    aprobada = _leer(client, h, q2)
    assert aprobada["aviso_sin_un_peso_por_la_deuda"] == _detalle(pagar)
    assert "($120.000) se llevó lo que faltaba del neto" in _detalle(pagar)
    assert aprobada["avisos_deuda_borrada"] == {}
    # Y no depende de quién mira: dice un hecho de la fila, no nombra ningún botón.
    crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Consulta", "avisos.consulta")
    otro = _leer(client, auth_headers(client, "avisos.consulta"), q2)
    assert otro["aviso_sin_un_peso_por_la_deuda"] == _detalle(pagar)
    assert db_session.get(Liquidacion, uuid.UUID(q2)).estado == "aprobada"


def test_el_listado_trae_los_mismos_textos_que_el_detalle(client, base_datos, db_session):
    """EL CONTRATO CON LA PANTALLA, en las dos puertas: la lista de Liquidaciones y el detalle
    leen las mismas dos claves, con el mismo tipo (un diccionario de textos; un texto o
    null) y la misma letra. La migrada (90 L × $2.000 = $180.000 contra $300.000, pagado
    −$120.000) trae los cuatro 422; la pura ($120.000 − $120.000 de la quincena pasada = $0,
    aprobada) trae el de Pagar. Una sola página para las dos."""
    h = auth_headers(client, "admin.a")
    _, migrada = _migrada(client, h, db_session, "Migrada Lista")
    prov = _proveedor(client, h, "Pura Lista")
    _dia(client, h, prov, "2026-06-02", "90")
    _adelanto(client, h, prov, "2026-06-01", "300000")
    q1 = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{q1}/aprobar", headers=h).status_code == 200
    _dia(client, h, prov, "2026-06-17", "60")
    pura = _generar(client, h, prov, "2026-06-16", "2026-06-30")
    assert client.post(f"{API}/{pura}/aprobar", headers=h).status_code == 200

    pagina = client.get(API, params={"page_size": 100}, headers=h).json()["items"]
    en_lista = {fila["id"]: fila for fila in pagina}
    for liq in (migrada["id"], pura):
        detalle = _leer(client, h, liq)
        fila = en_lista[liq]
        for campo in ("avisos_deuda_borrada", "aviso_sin_un_peso_por_la_deuda"):
            assert fila[campo] == detalle[campo], (liq, campo)
        assert isinstance(fila["avisos_deuda_borrada"], dict)
        assert all(isinstance(t, str) for t in fila["avisos_deuda_borrada"].values())
        neto = D(fila["valor_total"]) - D(fila["anticipos"]) - D(fila["saldo_anterior"])
        assert D(fila["neto_a_pagar"]) == neto and D(fila["saldo"]) == neto - D(fila["pagado"])
    assert set(en_lista[migrada["id"]]["avisos_deuda_borrada"]) == {
        "corregir", "pagar", "registrar_pago", "anular"}
    assert en_lista[migrada["id"]]["aviso_sin_un_peso_por_la_deuda"] is None
    assert en_lista[pura]["avisos_deuda_borrada"] == {}
    pagar = client.post(f"{API}/{pura}/pagar", headers=h)
    assert pagar.status_code == 422
    assert en_lista[pura]["aviso_sin_un_peso_por_la_deuda"] == _detalle(pagar)
