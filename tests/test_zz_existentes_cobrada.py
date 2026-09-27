"""LAS FILAS DE LA CAPTURA DEL DUEÑO Y LO QUE SE PUEDE HACER CON ELLAS.

Pregunta del dueño: "¿y qué pasa con las que ya cumplen con esa regla?". La captura
mostraba dos filas "Aprobada" + "quedó debiendo · cobrada" (un proveedor y un
transportador). Aquí se arma ese mismo estado con la API, igual que quedó en
producción (estado guardado 'aprobada', saldo negativo, deuda ya trasladada), y se mide:

  1. qué rótulo y qué papel salen de la que ya cobró su deuda, y de la siguiente;
  2. el transportador con rutas: mismo rótulo, mismo signo;
  3. qué pasa al oprimir cada acción sobre una fila que dice "pagada · quedó debiendo"
     pero está guardada 'aprobada';
  4. reimprimir el PDF: qué escribe y qué queda en la bitácora.

Solo lectura del código de la app: nada de app/ se modifica.
"""
import io
from decimal import Decimal

from pypdf import PdfReader
from sqlalchemy import select

from app.modules.auditoria.models import Auditoria
from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
ANT = "/api/v1/anticipos"
PD = "pagada · quedó debiendo"

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
Q3 = ("2026-07-01", "2026-07-15")


def D(v):
    return Decimal(str(v))


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="1800"):
    r = client.post("/api/v1/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": precio}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _ruta(client, h, nombre):
    r = client.post("/api/v1/rutas", json={"nombre": nombre, "municipio": "Granada"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _transportador(client, h, nombre, rutas):
    r = client.post("/api/v1/transportadores", json={
        "nombre": nombre, "valor_transporte": "0",
        "rutas": [{"ruta_id": ru["id"], "valor_transporte": str(v)} for ru, v in rutas],
    }, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros, *, precio=None, t=None, ruta=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)}
    if precio is not None:
        cuerpo["precio_litro"] = str(precio)
    if t:
        cuerpo["transportador_id"] = t["id"]
    if ruta:
        cuerpo["ruta_id"] = ruta["id"]
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, fecha, valor, *, proveedor=None, transportador=None):
    cuerpo = {"fecha": fecha, "valor": str(valor)}
    if proveedor:
        cuerpo.update(tipo="proveedor", proveedor_id=proveedor["id"])
    else:
        cuerpo.update(tipo="transportador", transportador_id=transportador["id"])
    r = client.post(ANT, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, periodo, tipo="proveedor"):
    r = client.post(f"{API}/generar", json={
        "periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": tipo}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _de(liqs, nombre):
    x = [l for l in liqs if (l.get("proveedor_nombre") or l.get("transportador_nombre")) == nombre]
    assert len(x) == 1, liqs
    return x[0]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _aprobar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _ids(client, h, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = client.get(f"{API}?{qs}&page_size=100", headers=h)
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
    return " ".join(crudo.split())


def _foto(liq):
    """Lo que no debe moverse cuando una acción rebota."""
    return {k: liq[k] for k in (
        "estado", "estado_visible", "valor_total", "anticipos", "saldo_anterior", "pagado",
        "saldo", "version", "deuda_trasladada_a_id", "fecha_primera_impresion")} | {
        "pagos": len(liq.get("pagos") or [])}


def _henri_cobrada(client, h, *, litros_q2="100", precio_q2="2500"):
    """El caso de producción: Q1 aprobada con saldo -$120.000, deuda cobrada en Q2."""
    henri = _proveedor(client, h, "Henri C")
    rec1 = _recepcion(client, h, henri, "2026-06-02", "100")  # 100 x 1.800 = 180.000
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, henri, "2026-06-20", litros_q2, precio=precio_q2)
    q2 = _generar(client, h, Q2)[0]
    return henri, rec1, _leer(client, h, q1["id"]), q2


# ===========================================================================
# 1. LA QUE YA COBRÓ SU DEUDA, Y LA SIGUIENTE
# ===========================================================================
def test_1_la_cobrada_se_lee_pagada_quedo_debiendo_y_su_papel_lo_dice(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, _, q1, q2 = _henri_cobrada(client, h)
    print("\n===== 1. LA COBRADA =====")
    print(f"  Q1 guardado={q1['estado']} visible={q1['estado_visible']} saldo={q1['saldo']} "
          f"debe={q1['le_queda_debiendo']} trasladada_a={'Q2' if q1['deuda_trasladada_a_id'] == q2['id'] else q1['deuda_trasladada_a_id']}")
    assert q1["estado"] == "aprobada"
    assert D(q1["saldo"]) == D("-120000")
    assert q1["deuda_trasladada_a_id"] == q2["id"]
    assert q1["estado_visible"] == PD

    # El filtro de la lista va con el chip.
    assert q1["id"] in _ids(client, h, estado="pagada")
    assert q1["id"] not in _ids(client, h, estado="aprobada")

    papel = _pdf(client, h, q1["id"])
    i = papel.find("Estado:")
    print(f"  PDF Q1 encabezado: {papel[i:i+45]!r}")
    j = papel.find("LE QUEDA DEBIENDO")
    print(f"  PDF Q1 cierre: {papel[j:j+40]!r}")
    k = papel.find("Lo que quedó debiendo en esta quincena")
    print(f"  PDF Q1 nota: {papel[k:k+190]!r}")
    assert "Estado: PAGADA · QUEDÓ DEBIENDO" in papel
    assert "LE QUEDA DEBIENDO $120.000" in papel
    assert "ya se le cobró en la liquidación del 16/06/2026 al 30/06/2026" in papel
    assert "no hay que volver a cobrarlo" in papel

    # LA SIGUIENTE (la que cobró): borrador -> aprobada con saldo positivo -> pagada.
    print(f"  Q2 generada: estado={q2['estado']} visible={q2['estado_visible']} "
          f"saldo_anterior={q2['saldo_anterior']} saldo={q2['saldo']}")
    assert q2["estado_visible"] == "borrador"
    q2 = _aprobar(client, h, q2["id"])
    print(f"  Q2 aprobada: visible={q2['estado_visible']} saldo={q2['saldo']}")
    assert q2["estado_visible"] == "aprobada"
    assert D(q2["saldo"]) == D("130000")
    assert q2["id"] in _ids(client, h, estado="aprobada")
    r = client.post(f"{API}/{q2['id']}/pagar", headers=h)
    assert r.status_code == 200, r.text
    print(f"  Q2 pagada: visible={r.json()['estado_visible']}")
    assert r.json()["estado_visible"] == "pagada"
    # Y Q1 no cambia por pagar la otra.
    assert _leer(client, h, q1["id"])["estado_visible"] == PD


def test_1b_la_siguiente_que_cae_en_cero_por_la_deuda_sigue_aprobada_y_no_se_puede_cerrar(
    client, base_datos
):
    """Q1 deja -$120.000. Q2 vale EXACTO $120.000 (100 L a $1.200): neto $0 por la deuda.

    Estado visible = 'aprobada' (le_queda_debiendo = 0), sale en el filtro 'aprobada' y en
    la tarjeta "Aprobadas por pagar", y el POST /pagar (que es lo que llama el botón
    "Marcar pagada" del detalle) rebota. Queda "Aprobada" para siempre con $0 por pagar.
    """
    h = auth_headers(client, "admin.a")
    _, _, q1, q2 = _henri_cobrada(client, h, litros_q2="100", precio_q2="1200")
    q2 = _aprobar(client, h, q2["id"])
    print("\n===== 1b. LA SIGUIENTE EN CERO =====")
    print(f"  Q2 total={q2['valor_total']} saldo_anterior={q2['saldo_anterior']} "
          f"neto={q2['neto_a_pagar']} saldo={q2['saldo']} estado={q2['estado']} "
          f"visible={q2['estado_visible']}")
    assert D(q2["saldo"]) == D("0")
    assert q2["estado"] == "aprobada"
    assert q2["estado_visible"] == "aprobada"
    assert q2["id"] in _ids(client, h, estado="aprobada")
    assert q2["id"] not in _ids(client, h, estado="pagada")

    antes = _foto(_leer(client, h, q2["id"]))
    r = client.post(f"{API}/{q2['id']}/pagar", headers=h)
    print(f"  POST /pagar -> {r.status_code} · {r.json().get('error', {}).get('detail')}")
    assert r.status_code == 422
    assert "no hay que pagarla" in r.json()["error"]["detail"]
    assert _foto(_leer(client, h, q2["id"])) == antes
    papel = _pdf(client, h, q2["id"])
    i = papel.find("Estado:")
    print(f"  PDF Q2 encabezado: {papel[i:i+30]!r}")
    assert "Estado: APROBADA" in papel


def test_1c_la_cadena_la_siguiente_que_vuelve_a_quedar_debiendo(client, base_datos):
    """Q2 cobra -$120.000 y vale $90.000 -> neto -$30.000: también PD cuando se aprueba."""
    h = auth_headers(client, "admin.a")
    _, _, q1, q2 = _henri_cobrada(client, h, litros_q2="50", precio_q2="1800")
    print("\n===== 1c. LA CADENA =====")
    print(f"  Q2 borrador visible={q2['estado_visible']} saldo={q2['saldo']}")
    assert q2["estado_visible"] == "borrador"
    q2 = _aprobar(client, h, q2["id"])
    print(f"  Q2 aprobada visible={q2['estado_visible']} debe={q2['le_queda_debiendo']}")
    assert q2["estado_visible"] == PD
    assert D(q2["le_queda_debiendo"]) == D("30000")


def test_1d_el_origen_en_borrador_cuya_deuda_ya_se_cobro_no_lleva_rotulo(client, base_datos):
    """La deuda de un BORRADOR también viaja (repository.deudas_sin_cobrar). Ese origen
    queda 'borrador' + 'quedó debiendo · cobrada', sin el rótulo nuevo (a propósito)."""
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]  # sin aprobar
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    q1 = _leer(client, h, q1["id"])
    print("\n===== 1d. ORIGEN EN BORRADOR, YA COBRADO =====")
    print(f"  Q1 estado={q1['estado']} visible={q1['estado_visible']} "
          f"trasladada={'sí' if q1['deuda_trasladada_a_id'] else 'no'} Q2.saldo_anterior={q2['saldo_anterior']}")
    assert q1["deuda_trasladada_a_id"] == q2["id"]
    assert q1["estado_visible"] == "borrador"
    # Aprobarla después: pasa a PD sin tocar cifras.
    q1b = _aprobar(client, h, q1["id"])
    print(f"  Q1 aprobada después: visible={q1b['estado_visible']} saldo={q1b['saldo']}")
    assert q1b["estado_visible"] == PD
    assert D(q1b["saldo"]) == D(q1["saldo"])


# ===========================================================================
# 2. TRANSPORTADOR CON RUTAS
# ===========================================================================
def test_2_transportador_con_dos_rutas_mismo_rotulo_mismo_signo(client, base_datos):
    """Alex hace Nápoles a $1/L y RES a $2/L. Q1: 100 L + 50 L = $100 + $100 = $200 de
    flete, anticipo $600 -> saldo -$400 (debe $400). Q2: 100 L en Nápoles = $100, cobra
    $400 -> neto -$300."""
    h = auth_headers(client, "admin.a")
    napoles = _ruta(client, h, "Napoles")
    res = _ruta(client, h, "RES")
    alex = _transportador(client, h, "Alex", [(napoles, "1"), (res, "2")])
    patricia = _proveedor(client, h, "Patricia")
    rosa = _proveedor(client, h, "Rosa")
    _recepcion(client, h, patricia, "2026-06-02", "100", t=alex, ruta=napoles)
    _recepcion(client, h, rosa, "2026-06-02", "50", t=alex, ruta=res)
    _anticipo(client, h, "2026-06-01", "600", transportador=alex)
    t1 = _de(_generar(client, h, Q1, tipo="transportador"), "Alex")
    print("\n===== 2. TRANSPORTADOR =====")
    print(f"  T1 total={t1['valor_total']} ant={t1['anticipos']} saldo={t1['saldo']} "
          f"neto={t1['neto_a_pagar']} debe={t1['le_queda_debiendo']} visible={t1['estado_visible']}")
    assert D(t1["valor_total"]) == D("200")
    assert D(t1["saldo"]) == D("-400")
    assert D(t1["le_queda_debiendo"]) == D("400")
    assert D(t1["neto_a_pagar"]) == D(t1["pagado"]) + D(t1["saldo"])
    assert t1["estado_visible"] == "borrador"
    t1 = _aprobar(client, h, t1["id"])
    assert t1["estado_visible"] == PD
    # El pagar del flete también rebota.
    r = client.post(f"{API}/{t1['id']}/pagar", headers=h)
    print(f"  T1 POST /pagar -> {r.status_code}")
    assert r.status_code == 422

    _recepcion(client, h, patricia, "2026-06-20", "100", t=alex, ruta=napoles)
    t2 = _de(_generar(client, h, Q2, tipo="transportador"), "Alex")
    t1 = _leer(client, h, t1["id"])
    print(f"  T1 tras Q2: estado={t1['estado']} visible={t1['estado_visible']} "
          f"trasladada={'T2' if t1['deuda_trasladada_a_id'] == t2['id'] else t1['deuda_trasladada_a_id']}")
    print(f"  T2 total={t2['valor_total']} saldo_anterior={t2['saldo_anterior']} "
          f"saldo={t2['saldo']} debe={t2['le_queda_debiendo']}")
    assert t1["deuda_trasladada_a_id"] == t2["id"]
    assert t1["estado_visible"] == PD
    assert D(t2["saldo_anterior"]) == D("400")
    assert D(t2["saldo"]) == D("-300")
    t2 = _aprobar(client, h, t2["id"])
    assert t2["estado_visible"] == PD

    assert t1["id"] in _ids(client, h, tipo="transportador", estado="pagada")
    assert t1["id"] not in _ids(client, h, tipo="transportador", estado="aprobada")
    papel = _pdf(client, h, t1["id"])
    i = papel.find("Estado:")
    print(f"  PDF T1 encabezado: {papel[i:i+45]!r}")
    assert "Estado: PAGADA · QUEDÓ DEBIENDO" in papel
    assert "LE QUEDA DEBIENDO $400" in papel
    assert "Transportador" in papel


# ===========================================================================
# 3. LAS ACCIONES SOBRE LA FILA QUE DICE "PAGADA · QUEDÓ DEBIENDO"
# ===========================================================================
def test_3a_acciones_sobre_la_cobrada_rebotan_todas_sin_mover_nada(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, _, q1, _ = _henri_cobrada(client, h)
    antes = _foto(q1)
    print("\n===== 3a. ACCIONES SOBRE LA COBRADA (guardada aprobada, visible PD) =====")
    intentos = [
        ("POST /pagar", "post", f"{API}/{q1['id']}/pagar", None),
        ("POST /pagos $1", "post", f"{API}/{q1['id']}/pagos", {"fecha": "2026-06-20", "valor": "1"}),
        ("POST /pagos $0", "post", f"{API}/{q1['id']}/pagos", {"fecha": "2026-06-20", "valor": "0"}),
        ("POST /pagos -$5", "post", f"{API}/{q1['id']}/pagos", {"fecha": "2026-06-20", "valor": "-5"}),
        ("POST /pagos $120000", "post", f"{API}/{q1['id']}/pagos", {"fecha": "2026-06-20", "valor": "120000"}),
        ("POST /anular", "post", f"{API}/{q1['id']}/anular", None),
        ("POST /aprobar", "post", f"{API}/{q1['id']}/aprobar", None),
        ("POST /recalcular", "post", f"{API}/{q1['id']}/recalcular", None),
        ("POST /corregir/previsualizar", "post", f"{API}/{q1['id']}/corregir/previsualizar", {"motivo": "revisar"}),
        ("POST /corregir", "post", f"{API}/{q1['id']}/corregir", {"motivo": "faltó un día"}),
    ]
    for nombre, metodo, url, cuerpo in intentos:
        r = getattr(client, metodo)(url, json=cuerpo, headers=h) if cuerpo is not None \
            else getattr(client, metodo)(url, headers=h)
        err = r.json().get("error", {})
        print(f"  {nombre:28s} -> {r.status_code} · {str(err.get('detail') or err)[:230]}")
        assert r.status_code == 422, (nombre, r.text)
        assert _foto(_leer(client, h, q1["id"])) == antes, nombre


def test_3b_acciones_sobre_la_pendiente_anular_si_pasa(client, base_datos):
    """La que dice PD pero cuya deuda TODAVÍA no se cobró: Pagar/abono rebotan; Anular
    pasa (200) y la deja 'anulada' soltando el anticipo."""
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _aprobar(client, h, _generar(client, h, Q1)[0]["id"])
    print("\n===== 3b. ACCIONES SOBRE LA PENDIENTE =====")
    print(f"  Q1 estado={q1['estado']} visible={q1['estado_visible']} trasladada={q1['deuda_trasladada_a_id']}")
    assert q1["estado_visible"] == PD and q1["deuda_trasladada_a_id"] is None
    antes = _foto(q1)
    for nombre, url, cuerpo in [
        ("POST /pagar", f"{API}/{q1['id']}/pagar", None),
        ("POST /pagos $1", f"{API}/{q1['id']}/pagos", {"fecha": "2026-06-10", "valor": "1"}),
        ("POST /recalcular", f"{API}/{q1['id']}/recalcular", None),
        ("POST /corregir/previsualizar", f"{API}/{q1['id']}/corregir/previsualizar", {"motivo": "revisar"}),
    ]:
        r = client.post(url, json=cuerpo, headers=h) if cuerpo else client.post(url, headers=h)
        print(f"  {nombre:28s} -> {r.status_code} · {r.json().get('error', {}).get('detail', '')[:200]}")
        assert r.status_code == 422
        assert _foto(_leer(client, h, q1["id"])) == antes
    r = client.post(f"{API}/{q1['id']}/anular", headers=h)
    print(f"  POST /anular                  -> {r.status_code} · estado={r.json().get('estado')} "
          f"visible={r.json().get('estado_visible')}")
    assert r.status_code == 200
    assert r.json()["estado_visible"] == "anulada"
    suelto = client.get(f"{ANT}/{ant['id']}", headers=h).json()
    print(f"  el anticipo queda liquidacion_id={suelto['liquidacion_id']}")
    assert suelto["liquidacion_id"] is None


# ===========================================================================
# 4. REIMPRIMIR EL PAPEL YA ENTREGADO
# ===========================================================================
def test_4_reimprimir_no_muta_cifras_ni_version_y_la_bitacora_guarda_el_estado_guardado(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    _, _, q1, _ = _henri_cobrada(client, h)
    assert q1["fecha_primera_impresion"] is None
    p1 = _pdf(client, h, q1["id"])
    tras1 = _leer(client, h, q1["id"])
    p2 = _pdf(client, h, q1["id"])
    tras2 = _leer(client, h, q1["id"])
    print("\n===== 4. REIMPRESIÓN =====")
    print(f"  1ª impresión fecha_primera_impresion={tras1['fecha_primera_impresion']} v={tras1['version']}")
    print(f"  2ª impresión fecha_primera_impresion={tras2['fecha_primera_impresion']} v={tras2['version']}")
    assert tras1["fecha_primera_impresion"] is not None
    assert tras2["fecha_primera_impresion"] == tras1["fecha_primera_impresion"]
    assert tras2["version"] == tras1["version"] == 1
    for k in ("estado", "valor_total", "anticipos", "saldo", "pagado", "saldo_anterior",
              "deuda_trasladada_a_id"):
        assert tras2[k] == q1[k], k
    corr = client.get(f"{API}/{q1['id']}/correcciones", headers=h)
    print(f"  correcciones -> {corr.status_code} {corr.json()}")
    assert corr.status_code == 200 and corr.json() == []
    folio = str(q1["id"])[:8].upper()
    assert f"N.º {folio} " in p1 and f"N.º {folio} " in p2
    assert "COMPROBANTE CORREGIDO" not in p2
    # Lo que queda en la bitácora de cada impresión.
    filas = db_session.scalars(select(Auditoria).where(Auditoria.accion == "imprimir")).all()
    mias = [f for f in filas if str(f.entidad_id) == q1["id"]]
    print(f"  bitácora 'imprimir' x{len(mias)}: estado={[f.despues.get('estado') for f in mias]} "
          f"· el papel dice {'PAGADA · QUEDÓ DEBIENDO' if 'PAGADA · QUEDÓ DEBIENDO' in p2 else '?'}")
    assert len(mias) == 2
    assert all(f.despues.get("estado") == "aprobada" for f in mias)
    assert "Estado: PAGADA · QUEDÓ DEBIENDO" in p2


# ===========================================================================
# 5. EL DÍA DE LA COBRADA EN RECEPCIÓN
# ===========================================================================
def test_5_el_dia_de_la_cobrada_llega_a_recepcion_como_aprobada_y_trabado(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, rec1, q1, _ = _henri_cobrada(client, h)
    dia = client.get(f"{REC}/{rec1['id']}", headers=h).json()
    print("\n===== 5. EL DÍA EN RECEPCIÓN =====")
    print(f"  liquidacion_estado={dia['liquidacion_estado']} leche_pagada={dia['leche_pagada']} "
          f"pagada={dia.get('pagada')} aviso={dia.get('candado_aviso')!r}")
    assert dia["liquidacion_estado"] == "aprobada"
    assert dia["leche_pagada"] is True


# ===========================================================================
# 6. EL ANTICIPO DE LA COBRADA EN LA PANTALLA DE ANTICIPOS
# ===========================================================================
def test_6_el_anticipo_de_la_cobrada_llega_sin_candado_y_el_servidor_lo_rebota(
    client, base_datos
):
    """La pantalla de Anticipos decide los botones Editar/Eliminar con `bloqueado`
    (anticipo-list.page.html:93) y el aviso con `liquidacion_estado`
    (anticipo-list.page.ts:107). Aquí se mide qué le llega para el adelanto de $300.000
    de la fila de la captura, y qué hace el servidor si se oprime."""
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _aprobar(client, h, _generar(client, h, Q1)[0]["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    _generar(client, h, Q2)
    q1 = _leer(client, h, q1["id"])
    assert q1["deuda_trasladada_a_id"] and q1["estado_visible"] == PD

    leido = client.get(f"{ANT}/{ant['id']}", headers=h).json()
    en_lista = [a for a in client.get(f"{ANT}?page_size=100", headers=h).json()["items"]
                if a["id"] == ant["id"]][0]
    print("\n===== 6. EL ANTICIPO DE LA COBRADA =====")
    print(f"  GET uno  -> liquidacion_estado={leido['liquidacion_estado']} bloqueado={leido['bloqueado']}")
    print(f"  GET lista-> liquidacion_estado={en_lista['liquidacion_estado']} bloqueado={en_lista['bloqueado']}")
    r_put = client.put(f"{ANT}/{ant['id']}", json={"valor": "200000"}, headers=h)
    r_del = client.delete(f"{ANT}/{ant['id']}", headers=h)
    print(f"  PUT  -> {r_put.status_code} · {r_put.json().get('error', {}).get('detail', '')[:200]}")
    print(f"  DELETE -> {r_del.status_code} · "
          f"{(r_del.json().get('error', {}).get('detail', '') if r_del.content else '')[:200]}")
    assert en_lista["liquidacion_estado"] == "aprobada"
    assert en_lista["bloqueado"] is False
    assert r_put.status_code == 422 and r_del.status_code == 422
    assert "ya se le cobró" in r_put.json()["error"]["detail"]
    # Y nada se movió.
    assert _leer(client, h, q1["id"])["saldo"] == q1["saldo"]


def test_5b_y_si_se_intenta_corregir_los_litros_de_ese_dia_rebota(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, rec1, q1, _ = _henri_cobrada(client, h)
    r = client.put(f"{REC}/{rec1['id']}", json={"cantidad_litros": "120"}, headers=h)
    print(f"\n  PUT litros del día -> {r.status_code} · {r.json().get('error', {}).get('detail', '')[:160]}")
    assert r.status_code == 422
    assert _leer(client, h, q1["id"])["estado"] == "aprobada"
