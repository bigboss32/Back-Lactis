"""REVISIÓN ADVERSARIA (lente: la plata y la regla de oro) de B1-B4.

Solo mide; no arregla nada. Cada prueba intenta romper una promesa concreta:

  R1  /resumen == las tarjetas de antes (sin el tope), con formas raras de filas.
  R2  la tarjeta "por pagar" frente a la fila que el servidor ya no deja pagar (B4).
  R3  centavos: la suma sin tope cuadra al centavo.
  R4  superadmin sin empresa: /resumen rebota igual que la lista.
  R5  la migrada: ninguna otra ruta mueve plata sobre ella.
  R6  anticipo de un BORRADOR cuya deuda ya se cobró (B2 por fuera de 'aprobada').
  R7  grilla del día de ese borrador (B3 por fuera de 'aprobada').
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
ESTADOS = ("borrador", "aprobada", "parcial", "pagada")


def D(v):
    return Decimal(str(v))


def _fila(db, empresa_id, *, tercero_id, periodo, estado, valor_total, anticipos="0",
          saldo_anterior="0", pagado="0", saldo=None, version=1, borrada=False,
          tipo="proveedor"):
    vt, an, sa, pg = (D(x) for x in (valor_total, anticipos, saldo_anterior, pagado))
    liq = Liquidacion(
        empresa_id=empresa_id, tipo=tipo,
        proveedor_id=tercero_id if tipo == "proveedor" else None,
        transportador_id=tercero_id if tipo == "transportador" else None,
        periodo_inicio=periodo[0], periodo_fin=periodo[1], total_litros=D("100"),
        valor_bruto=vt, valor_total=vt, anticipos=an, saldo_anterior=sa, pagado=pg,
        saldo=(vt - an - sa - pg) if saldo is None else D(saldo),
        estado=estado, version=version,
    )
    if borrada:
        liq.deleted_at = datetime.now(timezone.utc)
    db.add(liq)
    db.flush()
    return liq


def _proveedor(client, h, nombre, precio="2000"):
    r = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": precio}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _lista(client, h, **q):
    q = {k: v for k, v in q.items() if v is not None}
    r = client.get(API, params={**q, "page_size": 200}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _tarjetas_viejas(client, h, **filtros):
    """`cargarResumen` de HEAD en Front-Lactis, fila por fila."""
    por = {e: _lista(client, h, estado=e, **filtros) for e in ESTADOS}
    deben = [x for e in ESTADOS for x in por[e]["items"]
             if D(x["le_queda_debiendo"]) > 0 and not x["deuda_trasladada_a_id"]]
    return {
        "borradores": por["borrador"]["total"],
        "aprobadas": por["aprobada"]["total"],
        "saldo_aprobadas": sum((max(D(0), D(x["saldo"])) for x in por["aprobada"]["items"]),
                               D(0)),
        "parciales": por["parcial"]["total"],
        "saldo_parciales": sum((max(D(0), D(x["saldo"])) for x in por["parcial"]["items"]),
                               D(0)),
        "pagadas": por["pagada"]["total"],
        "le_quedaron_debiendo": sum((D(x["le_queda_debiendo"]) for x in deben), D(0)),
        "liquidaciones_que_deben": len(deben),
    }


def _resumen(client, h, **q):
    r = client.get(f"{API}/resumen", params={k: v for k, v in q.items() if v is not None},
                   headers=h)
    assert r.status_code == 200, r.text
    return {k: (D(v) if isinstance(v, str) else v) for k, v in r.json().items()}


# ---------------------------------------------------------------------------------- R1
def test_r1_formas_raras_resumen_igual_a_las_tarjetas_viejas(client, db_session, base_datos):
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    p = [uuid.UUID(_proveedor(client, h, f"P{i}")) for i in range(8)]
    Q1 = (date(2026, 7, 1), date(2026, 7, 15))
    Q2 = (date(2026, 7, 16), date(2026, 7, 31))
    # aprobada con neto CERO porque la deuda arrastrada se lo comió (se queda aprobada)
    cobra = _fila(db_session, emp, tercero_id=p[0], periodo=Q2, estado="aprobada",
                  valor_total="120000", saldo_anterior="120000")
    # borrador que quedó debiendo y cuya deuda ya se cobró (no cuenta como deuda)
    b_cobrada = _fila(db_session, emp, tercero_id=p[0], periodo=Q1, estado="borrador",
                      valor_total="180000", anticipos="300000")
    b_cobrada.deuda_trasladada_a_id = cobra.id
    # parcial v2 a la que se le entregó de más: saldo −$50.000 (se lee pagada, debe)
    _fila(db_session, emp, tercero_id=p[1], periodo=Q1, estado="parcial", version=2,
          valor_total="300000", pagado="350000")
    # pagada con saldo positivo (forma rara): no suma a ningún "por pagar"
    _fila(db_session, emp, tercero_id=p[2], periodo=Q1, estado="pagada",
          valor_total="300000", pagado="250000")
    # anulada debiendo y con marca: no cuenta
    _fila(db_session, emp, tercero_id=p[3], periodo=Q1, estado="anulada",
          valor_total="100000", anticipos="200000")
    # aprobada debiendo pero BORRADA en suave: no cuenta
    _fila(db_session, emp, tercero_id=p[4], periodo=Q1, estado="aprobada",
          valor_total="100000", anticipos="200000", borrada=True)
    # la migrada: pagada, pagado −$120.000, saldo 0
    _fila(db_session, emp, tercero_id=p[5], periodo=Q1, estado="pagada",
          valor_total="180000", anticipos="300000", pagado="-120000")
    # aprobada normal por pagar $80.000 con centavos
    _fila(db_session, emp, tercero_id=p[6], periodo=Q2, estado="aprobada",
          valor_total="80000.35")
    db_session.commit()

    for filtros in ({}, {"tipo": "proveedor"}, {"desde": "2026-07-16"},
                    {"hasta": "2026-07-15"}, {"tipo": "transportador"}):
        nuevo = _resumen(client, h, **filtros)
        viejo = _tarjetas_viejas(client, h, **filtros)
        print(f"\n  {filtros}: resumen={nuevo}")
        assert nuevo == viejo, (filtros, nuevo, viejo)
    # Las cifras a mano sin filtros.
    r = _resumen(client, h)
    assert r["aprobadas"] == 2 and r["saldo_aprobadas"] == D("80000.35")
    assert r["borradores"] == 1 and r["parciales"] == 0 and r["pagadas"] == 3
    assert r["le_quedaron_debiendo"] == D("50000") and r["liquidaciones_que_deben"] == 1


# ---------------------------------------------------------------------------------- R2
def test_r2_la_tarjeta_por_pagar_frente_a_la_fila_que_no_se_deja_pagar(
        client, db_session, base_datos):
    """La forma que dejó una corrección hecha ANTES del guardia B4 (parcial v2, pagado
    −$120.000, saldo +$50.000; la verdad: debe $70.000). Mide qué dicen las tarjetas."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    prov = uuid.UUID(_proveedor(client, h, "Corregida Antes"))
    liq = _fila(db_session, emp, tercero_id=prov, periodo=(date(2026, 7, 1),
                date(2026, 7, 15)), estado="parcial", version=2, valor_total="230000",
                anticipos="300000", pagado="-120000")
    db_session.commit()
    assert liq.saldo == D("50000")

    r = _resumen(client, h)
    pagar = client.post(f"{API}/{liq.id}/pagar", headers=h)
    abono = client.post(f"{API}/{liq.id}/pagos", json={"fecha": "2026-08-10",
                                                       "valor": "10000"}, headers=h)
    fila = client.get(f"{API}/{liq.id}", headers=h).json()
    print(f"\n  resumen={r}")
    print(f"  pagar={pagar.status_code} abono={abono.status_code} "
          f"deuda_borrada={fila['deuda_borrada_por_la_migracion']} "
          f"le_queda_debiendo={fila['le_queda_debiendo']}")
    assert pagar.status_code == 422 and abono.status_code == 422
    # Lo que la tarjeta promete: $50.000 por pagar, en una fila que nadie puede pagar.
    assert r["parciales"] == 1
    assert r["saldo_parciales"] == D("50000")
    # Y lo que el tercero de verdad debe ($70.000) no sale en "Le quedaron debiendo".
    assert r["le_quedaron_debiendo"] == D("0")


# ---------------------------------------------------------------------------------- R3
def test_r3_centavos_sin_tope(client, db_session, base_datos):
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    prov = uuid.UUID(_proveedor(client, h, "Centavos"))
    for i in range(250):
        ini = date(2024, 1, 1) + timedelta(days=i * 2)
        # pagada que quedó debiendo 7 centavos (valor 0.93 − anticipo 1.00)
        _fila(db_session, emp, tercero_id=prov, periodo=(ini, ini + timedelta(days=1)),
              estado="pagada", valor_total="0.93", anticipos="1.00")
    for i in range(3):
        ini = date(2026, 1, 1) + timedelta(days=i * 2)
        _fila(db_session, emp, tercero_id=prov, periodo=(ini, ini + timedelta(days=1)),
              estado="aprobada", valor_total="0.10")
    db_session.commit()
    r = client.get(f"{API}/resumen", headers=h).json()
    print(f"\n  crudo={r}")
    assert D(r["le_quedaron_debiendo"]) == D("17.50")
    assert r["liquidaciones_que_deben"] == 250
    assert D(r["saldo_aprobadas"]) == D("0.30")
    assert r["pagadas"] == 250


# ---------------------------------------------------------------------------------- R4
def test_r4_superadmin_sin_empresa_rebota_igual_que_la_lista(client, db_session, base_datos):
    h = auth_headers(client, "superadmin")
    lista = client.get(API, headers=h)
    resumen = client.get(f"{API}/resumen", headers=h)
    print(f"\n  lista={lista.status_code} resumen={resumen.status_code}")
    assert resumen.status_code == lista.status_code
    assert resumen.status_code >= 400
    hb = {**h, "X-Empresa-Id": str(base_datos["empresa_b"].id)}
    emp_a = base_datos["empresa_a"].id
    prov = uuid.UUID(_proveedor(client, auth_headers(client, "admin.a"), "Solo A"))
    _fila(db_session, emp_a, tercero_id=prov, periodo=(date(2026, 7, 1), date(2026, 7, 15)),
          estado="aprobada", valor_total="100000", anticipos="400000")
    db_session.commit()
    rb = client.get(f"{API}/resumen", headers=hb)
    assert rb.status_code == 200, rb.text
    assert D(rb.json()["le_quedaron_debiendo"]) == 0 and rb.json()["pagadas"] == 0


# ---------------------------------------------------------------------------------- R5
def _migrada_con_dia_y_anticipo(client, h, db):
    prov = _proveedor(client, h, "Migrada Rutas")
    dia = client.post(REC, json={"fecha": "2026-07-04", "proveedor_id": prov,
                                 "cantidad_litros": "90"}, headers=h)
    assert dia.status_code == 201, dia.text
    ant = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                 "fecha": "2026-07-06", "valor": "300000"}, headers=h)
    assert ant.status_code == 201, ant.text
    g = client.post(f"{API}/generar", json={"periodo_inicio": "2026-07-01",
                                            "periodo_fin": "2026-07-15", "tipo": "proveedor"},
                    headers=h)
    liq = next(x for x in g.json()["generadas"] if x["proveedor_id"] == prov)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    fila = db.get(Liquidacion, uuid.UUID(liq["id"]))
    # Lo que dejó a5e7c1b4d9f2: pagada, pagado = valor_total − anticipos, saldo 0.
    fila.estado = "pagada"
    fila.pagado = D(fila.valor_total) - D(fila.anticipos)
    fila.saldo = D("0")
    db.commit()
    db.expire_all()
    return prov, liq["id"], dia.json()["id"], ant.json()["id"]


def _plata(client, h, liq_id):
    x = client.get(f"{API}/{liq_id}", headers=h).json()
    return {k: D(x[k]) for k in ("valor_total", "anticipos", "saldo_anterior", "pagado",
                                 "saldo", "neto_a_pagar")} | {
        "estado": x["estado"], "version": x["version"], "pagos": len(x["pagos"])}


def test_r5_ninguna_otra_ruta_mueve_plata_sobre_la_migrada(client, db_session, base_datos):
    h = auth_headers(client, "admin.a")
    prov, liq_id, dia_id, ant_id = _migrada_con_dia_y_anticipo(client, h, db_session)
    antes = _plata(client, h, liq_id)
    assert antes["pagado"] == D("-120000") and antes["saldo"] == 0
    detalle_id = client.get(f"{API}/{liq_id}", headers=h).json()["detalles"][0]["id"]
    intentos = {
        "anular": client.post(f"{API}/{liq_id}/anular", headers=h),
        "recalcular": client.post(f"{API}/{liq_id}/recalcular", headers=h),
        "precio_detalle": client.put(f"{API}/{liq_id}/detalles/{detalle_id}",
                                     json={"precio_litro": "2500"}, headers=h),
        "dia_litros": client.put(f"{REC}/{dia_id}", json={"cantidad_litros": "100"},
                                 headers=h),
        "anticipo_valor": client.put(f"{ANT}/{ant_id}", json={"valor": "200000"}, headers=h),
        "anticipo_borrar": client.delete(f"{ANT}/{ant_id}", headers=h),
        "pagar": client.post(f"{API}/{liq_id}/pagar", headers=h),
        "abonar": client.post(f"{API}/{liq_id}/pagos", json={"fecha": "2026-08-10",
                                                            "valor": "1000"}, headers=h),
    }
    for nombre, r in intentos.items():
        print(f"\n  {nombre} -> {r.status_code}")
        assert r.status_code >= 400, (nombre, r.text)
    assert _plata(client, h, liq_id) == antes


# ---------------------------------------------------------------------------------- R6
def _borrador_cobrado(client, h):
    """Beto: 100 L × $1.800 = $180.000 contra $300.000; Q1 SE QUEDA EN BORRADOR y la
    segunda de junio le cobra los $120.000 (el borrador también presta su deuda)."""
    prov = _proveedor(client, h, "Beto Borrador", precio="1800")
    dia = client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                                 "cantidad_litros": "100"}, headers=h).json()
    ant = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                 "fecha": "2026-06-01", "valor": "300000"}, headers=h).json()
    g1 = client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                             "periodo_fin": "2026-06-15",
                                             "tipo": "proveedor"}, headers=h).json()
    q1 = next(x for x in g1["generadas"] if x["proveedor_id"] == prov)
    assert q1["estado"] == "borrador" and D(q1["saldo"]) == D("-120000")
    client.post(REC, json={"fecha": "2026-06-20", "proveedor_id": prov,
                           "cantidad_litros": "100", "precio_litro": "2500"}, headers=h)
    g2 = client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-16",
                                             "periodo_fin": "2026-06-30",
                                             "tipo": "proveedor"}, headers=h).json()
    q2 = next(x for x in g2["generadas"] if x["proveedor_id"] == prov)
    assert D(q2["saldo_anterior"]) == D("120000")
    q1 = client.get(f"{API}/{q1['id']}", headers=h).json()
    assert q1["estado"] == "borrador" and q1["deuda_trasladada_a_id"] == q2["id"]
    return prov, dia, ant, q1, q2


def test_r6_anticipo_de_borrador_con_deuda_cobrada(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, _, ant, q1, q2 = _borrador_cobrado(client, h)
    uno = client.get(f"{ANT}/{ant['id']}", headers=h).json()
    put = client.put(f"{ANT}/{ant['id']}", json={"valor": "200000"}, headers=h)
    print(f"\n  bloqueado={uno['bloqueado']} liquidacion_estado={uno['liquidacion_estado']} "
          f"PUT={put.status_code}")
    assert uno["liquidacion_estado"] == "borrador"
    assert uno["bloqueado"] is True
    assert put.status_code == 422
    # La que cobró la deuda sigue cuadrando: neto = total − anticipos − saldo_anterior.
    q2 = client.get(f"{API}/{q2['id']}", headers=h).json()
    neto = D(q2["valor_total"]) - D(q2["anticipos"]) - D(q2["saldo_anterior"])
    assert D(q2["neto_a_pagar"]) == neto == D(q2["pagado"]) + D(q2["saldo"])


# ---------------------------------------------------------------------------------- R7
def test_r7_grilla_del_dia_de_un_borrador_con_deuda_cobrada(client, base_datos):
    h = auth_headers(client, "admin.a")
    prov, dia, _, _, _ = _borrador_cobrado(client, h)
    g = client.get(f"{REC}/grilla/quincena", params={"desde": "2026-06-01",
                                                     "hasta": "2026-06-15"}, headers=h)
    celda = next(f for f in g.json()["filas"] if f["proveedor_id"] == prov)["celdas"][
        "2026-06-02"]
    put = client.put(f"{REC}/{dia['id']}", json={"cantidad_litros": "90"}, headers=h)
    print(f"\n  celda={celda} PUT={put.status_code}")
    assert celda["liquidacion_estado"] == "borrador"
    assert celda["pagada"] is True and celda["candado_aviso"]
    assert put.status_code == 422


# ---------------------------------------------------------------------------------- R8
def test_r8_la_migrada_que_un_pago_real_dejo_con_pagado_positivo_se_escapa_del_guardia(
        client, db_session, base_datos, monkeypatch):
    """La deuda borrada TAPADA por pagos de verdad. Antes del guardia se corrigió la
    migrada (−$120.000) metiéndole 100 L ($200.000) y se pagó: pagado = −120.000 +
    200.000 = $80.000. `pagado` ya no es negativo, pero sigue llevando adentro los
    −$120.000 de la migración: la caja entregó $200.000 contra un neto de $80.000."""
    import app.modules.liquidaciones.service as servicio

    h = auth_headers(client, "admin.a")
    prov, liq_id, _, _ = _migrada_con_dia_y_anticipo(client, h, db_session)

    def dia(fecha, litros):
        r = client.post(REC, json={"fecha": fecha, "proveedor_id": prov,
                                   "cantidad_litros": litros}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    # Lo que pasó en producción ANTES de este arreglo (sin el guardia B4).
    with monkeypatch.context() as m:
        m.setattr(servicio, "_exigir_sin_deuda_borrada", lambda *a, **k: None)
        grande = dia("2026-07-09", "100")                                # $200.000
        r = client.post(f"{API}/{liq_id}/corregir", json={
            "motivo": "días olvidados", "recepciones_a_incluir": [grande]}, headers=h)
        assert r.status_code == 200, r.text
        assert D(r.json()["saldo"]) == D("200000")
        r = client.post(f"{API}/{liq_id}/pagar", headers=h)
        assert r.status_code == 200, r.text
    hoy = client.get(f"{API}/{liq_id}", headers=h).json()
    pagos = sum((D(p["valor"]) for p in hoy["pagos"]), D(0))
    print(f"\n  hoy: estado={hoy['estado']} v{hoy['version']} neto={hoy['neto_a_pagar']} "
          f"pagado={hoy['pagado']} pagos_reales={pagos} "
          f"deuda_borrada={hoy['deuda_borrada_por_la_migracion']}")
    assert D(hoy["pagado"]) == D("80000") and pagos == D("200000")
    assert D(hoy["neto_a_pagar"]) == D("80000")          # la caja entregó 120.000 de más
    assert D(hoy["deuda_borrada_por_la_migracion"]) == 0  # el campo no lo ve

    # Con el guardia puesto: otro día olvidado de $50.000.
    otro = dia("2026-07-10", "25")
    prev = client.post(f"{API}/{liq_id}/corregir/previsualizar", json={
        "motivo": "otro día", "recepciones_a_incluir": [otro]}, headers=h)
    print(f"  previsualizar -> {prev.status_code} "
          f"{prev.json().get('saldo_despues', prev.json())}")
    # La verdad: neto $130.000 contra $200.000 entregados = el tercero debe $70.000.
    # Lo que dice el sistema: faltan $50.000 por entregarle.
    assert prev.status_code == 200, prev.text
    assert D(prev.json()["saldo_despues"]) == D("50000")
