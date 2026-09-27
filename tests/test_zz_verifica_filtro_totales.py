"""VERIFICADOR ADVERSARIAL, lente filtro_totales.

Re-mide los hallazgos del informe sobre las quincenas que ya existen, y prueba las
formas de fila que ese informe no cubrió. Filas escritas directo en la tabla (como
tests/test_zz_existentes_filtro.py) o por la API cuando lo que se mide es un flujo.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.core.pagination import PageParams
from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.repository import LiquidacionRepository
from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
V = "/api/v1"
PD = "pagada · quedó debiendo"


def _prov(client, h, nombre):
    r = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "X", "precio_litro": "2000"}, headers=h)
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


def _fila(db, emp, *, prov, periodo, estado, vt, an="0", sa="0", pg="0", saldo=None, clave=""):
    vt, an, sa, pg = (Decimal(x) for x in (vt, an, sa, pg))
    liq = Liquidacion(
        empresa_id=emp, tipo="proveedor", proveedor_id=prov,
        periodo_inicio=periodo[0], periodo_fin=periodo[1],
        total_litros=Decimal("100"), valor_bruto=vt, valor_total=vt,
        anticipos=an, saldo_anterior=sa, pagado=pg,
        saldo=(vt - an - sa - pg) if saldo is None else Decimal(saldo),
        estado=estado, version=1, observaciones=clave,
    )
    db.add(liq)
    db.flush()
    return liq


# ---------------------------------------------------------------------------------------
def test_la_pagada_de_antes_de_la_migracion_de_abonos_debe_y_no_lleva_rotulo(
        client, db_session, base_datos):
    """Forma que el catálogo del informe NO trae: la 'pagada' anterior a la migración
    a5e7c1b4d9f2. Esa migración les puso pagado = valor_total - anticipos y saldo = 0,
    así que si los anticipos pasaban del valor, pagado quedó NEGATIVO y saldo en 0."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    p = _prov(client, h, "Antes de agosto")
    # Como estaba ANTES de la migración: pagada, pagado 0 y saldo = neto completo.
    liq = _fila(db_session, emp, prov=p, periodo=(date(2026, 7, 1), date(2026, 7, 15)),
                estado="pagada", vt="180000", an="300000", saldo="-120000", clave="pre_mig")
    db_session.commit()
    # El UPDATE textual de alembic/versions/a5e7c1b4d9f2_pagos_parciales_de_liquidaciones.py:77-84
    db_session.execute(text(
        """
        UPDATE liquidaciones
           SET pagado = COALESCE(valor_total, 0) - COALESCE(anticipos, 0),
               saldo = 0
         WHERE estado = 'pagada'
        """
    ))
    db_session.commit()
    db_session.refresh(liq)

    fila = client.get(f"{API}/{liq.id}", headers=h).json()
    en = {e: {x["id"] for x in client.get(API, params={"estado": e, "page_size": 200},
                                         headers=h).json()["items"]}
          for e in ("aprobada", "pagada")}
    tablero = client.get(f"{V}/reportes/dashboard", headers=h).json()
    print(f"\n  pre_mig tras migrar: estado={fila['estado']} pagado={fila['pagado']} "
          f"saldo={fila['saldo']} neto={fila['neto_a_pagar']} le_queda_debiendo="
          f"{fila['le_queda_debiendo']} chip={fila['estado_visible']!r} "
          f"en_pagada={str(liq.id) in en['pagada']} tablero_le_deben="
          f"{tablero['terceros_le_quedan_debiendo']}")
    assert Decimal(fila["pagado"]) == Decimal("-120000")
    assert Decimal(fila["saldo"]) == 0
    assert Decimal(fila["neto_a_pagar"]) == Decimal(fila["pagado"]) + Decimal(fila["saldo"])
    # Los anticipos pasaron del valor en $120.000, pero el rótulo no lo dice:
    assert fila["estado_visible"] == "pagada"
    assert Decimal(fila["le_queda_debiendo"]) == 0
    assert str(liq.id) in en["pagada"]
    assert Decimal(tablero["terceros_le_quedan_debiendo"]) == 0


def test_f1_la_pagada_vieja_con_deuda_ya_se_perdia_antes_del_cambio(
        client, db_session, base_datos):
    """Alcance de F1: la parte 'aprobada' es nueva; la parte 'pagada con saldo negativo'
    (botón Pagar de antes) ya caía fuera de las 200 antes del cambio."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    se_fue, otra, constante = (_prov(client, h, n) for n in ("Se fue", "Otra", "Constante"))
    apr = _fila(db_session, emp, prov=se_fue, periodo=(date(2025, 1, 1), date(2025, 1, 15)),
                estado="aprobada", vt="180000", an="300000", clave="apr_vieja")
    pag = _fila(db_session, emp, prov=otra, periodo=(date(2025, 1, 1), date(2025, 1, 15)),
                estado="pagada", vt="100000", an="150000", clave="pag_vieja")
    for i in range(200):
        ini = date(2025, 1, 16) + timedelta(days=i * 2)
        _fila(db_session, emp, prov=constante, periodo=(ini, ini + timedelta(days=1)),
              estado="pagada", vt="100000", pg="100000", clave=f"p{i}")
    db_session.commit()
    repo = LiquidacionRepository(db_session, emp)
    antes_pag, total_antes_pag = repo.list_paginated(PageParams(page=1, page_size=200),
                                                     estado="pagada")
    antes_apr, _ = repo.list_paginated(PageParams(page=1, page_size=200), estado="aprobada")
    ahora_pag = client.get(API, params={"estado": "pagada", "page_size": 200}, headers=h).json()
    ahora_ids = {x["id"] for x in ahora_pag["items"]}
    print(f"\n  ANTES: pagada total={total_antes_pag} pag_vieja en 200? "
          f"{pag.id in {x.id for x in antes_pag}} · apr_vieja en aprobada? "
          f"{apr.id in {x.id for x in antes_apr}}")
    print(f"  AHORA: pagada total={ahora_pag['total']} pag_vieja? {str(pag.id) in ahora_ids}"
          f" apr_vieja? {str(apr.id) in ahora_ids}")
    assert pag.id not in {x.id for x in antes_pag}, "ya se perdía antes del cambio"
    assert apr.id in {x.id for x in antes_apr}, "la aprobada sí llegaba antes"
    assert str(apr.id) not in ahora_ids and str(pag.id) not in ahora_ids


def test_f2_paginacion_con_empates_en_sqlite(client, db_session, base_datos):
    """45 liquidaciones de la MISMA quincena, paginadas de 20 y de 7."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    for i in range(45):
        p = _prov(client, h, f"P{i:02d}")
        _fila(db_session, emp, prov=p, periodo=(date(2026, 8, 1), date(2026, 8, 15)),
              estado="aprobada", vt="100000", clave=f"q{i}")
    db_session.commit()
    for size in (20, 7):
        vistos = []
        pagina = 1
        while True:
            r = client.get(API, params={"page": pagina, "page_size": size}, headers=h).json()
            if not r["items"]:
                break
            vistos += [x["id"] for x in r["items"]]
            pagina += 1
        print(f"\n  page_size={size}: filas vistas={len(vistos)} distintas={len(set(vistos))}")
        assert len(set(vistos)) == 45
        assert len(vistos) == 45


def test_f4_saldo_cero_por_anticipos_se_cierra_con_pagar(client, db_session, base_datos):
    """El apr_cero del informe (anticipos = valor): Pagar lo manda a 'pagada'."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    p = _prov(client, h, "Cero por anticipos")
    liq = _fila(db_session, emp, prov=p, periodo=(date(2026, 8, 1), date(2026, 8, 15)),
                estado="aprobada", vt="500000", an="500000", clave="apr_cero")
    db_session.commit()
    r = client.post(f"{API}/{liq.id}/pagar", headers=h)
    print(f"\n  apr_cero -> pagar: {r.status_code} {r.json().get('estado')} "
          f"{r.json().get('estado_visible')}")
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "pagada"


def test_f4_saldo_cero_por_deuda_arrastrada_queda_aprobada_para_siempre(
        client, db_session, base_datos):
    """El neto cayó justo en 0 por un saldo_anterior: Pagar rebota y sigue contando en
    'Aprobadas por pagar' con el chip 'aprobada'."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    p = _prov(client, h, "Cero por deuda")
    liq = _fila(db_session, emp, prov=p, periodo=(date(2026, 8, 1), date(2026, 8, 15)),
                estado="aprobada", vt="120000", sa="120000", clave="apr_cero_deuda")
    db_session.commit()
    fila = client.get(f"{API}/{liq.id}", headers=h).json()
    en_apr = {x["id"] for x in client.get(API, params={"estado": "aprobada", "page_size": 200},
                                          headers=h).json()["items"]}
    r = client.post(f"{API}/{liq.id}/pagar", headers=h)
    print(f"\n  apr_cero_deuda: chip={fila['estado_visible']} en_aprobada={str(liq.id) in en_apr}"
          f" pagar={r.status_code} {r.text[:120]}")
    assert fila["estado_visible"] == "aprobada"
    assert str(liq.id) in en_apr
    assert r.status_code >= 400


def test_f5_dia_de_una_aprobada_con_deuda_cobrada_esta_trabado_y_dice_aprobada(
        client, db_session, base_datos):
    """Por la API: Q1 queda debiendo y se aprueba; Q2 se la cobra. El día de Q1 en
    Recepciones trae liquidacion_estado='aprobada' (el chip '✓ Aprobada' con el tooltip
    'si corrige el día, vuelve a borrador'), pero el backend lo tiene trabado."""
    h = auth_headers(client, "admin.a")
    prov = client.post(f"{V}/proveedores", json={
        "nombre": "Henri", "vereda": "El Roble", "precio_litro": "2000"}, headers=h).json()
    r = client.post(f"{V}/recepciones", json={
        "fecha": "2026-08-02", "proveedor_id": prov["id"], "cantidad_litros": "90"}, headers=h)
    assert r.status_code == 201, r.text
    dia_q1 = r.json()["id"]
    r = client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-08-03",
        "valor": "300000"}, headers=h)
    assert r.status_code == 201, r.text
    anticipo_q1 = r.json()["id"]
    gen = client.post(f"{API}/generar", json={
        "periodo_inicio": "2026-08-01", "periodo_fin": "2026-08-15", "tipo": "proveedor"},
        headers=h).json()["generadas"]
    q1 = next(x for x in gen if x["proveedor_id"] == prov["id"])
    q1 = client.post(f"{API}/{q1['id']}/aprobar", headers=h).json()
    assert q1["estado_visible"] == PD
    r = client.post(f"{V}/recepciones", json={
        "fecha": "2026-08-17", "proveedor_id": prov["id"], "cantidad_litros": "200"}, headers=h)
    assert r.status_code == 201, r.text
    gen2 = client.post(f"{API}/generar", json={
        "periodo_inicio": "2026-08-16", "periodo_fin": "2026-08-31", "tipo": "proveedor"},
        headers=h).json()["generadas"]
    q2 = next(x for x in gen2 if x["proveedor_id"] == prov["id"])
    q1 = client.get(f"{API}/{q1['id']}", headers=h).json()
    assert q1["deuda_trasladada_a_id"] == q2["id"], (q1["deuda_trasladada_a_id"], q2["id"])

    rec = client.get(f"{V}/recepciones", params={"desde": "2026-08-01", "hasta": "2026-08-15",
                                                 "page_size": 200}, headers=h).json()["items"]
    dia = next(x for x in rec if x["id"] == dia_q1)
    print(f"\n  q1 guardado={q1['estado']} chip={q1['estado_visible']} cobrada_en={q2['id'][:8]}")
    print(f"  día Q1 en Recepciones: liquidacion_estado={dia['liquidacion_estado']} "
          f"bloqueados={dia.get('campos_bloqueados')} aviso={dia.get('candado_aviso')!r}")
    assert dia["liquidacion_estado"] == "aprobada"
    assert dia.get("campos_bloqueados"), "el día está trabado por la deuda ya cobrada"
    # Y editarlo rebota, contra lo que dice el tooltip del chip 'Aprobada'.
    r = client.put(f"{V}/recepciones/{dia_q1}", json={"cantidad_litros": "95"}, headers=h)
    if r.status_code == 405:
        r = client.patch(f"{V}/recepciones/{dia_q1}", json={"cantidad_litros": "95"}, headers=h)
    print(f"  editar litros del día Q1: {r.status_code} {r.text[:160]}")
    assert r.status_code >= 400

    # ANTICIPOS: el adelanto de Q1 sale con liquidacion_estado='aprobada' (el aviso
    # "vuelve a borrador" de anticipo-list.page.ts:107-111). ¿Sale trabado? ¿Se deja editar?
    ant = client.get(f"{V}/anticipos/{anticipo_q1}", headers=h).json()
    r = client.put(f"{V}/anticipos/{anticipo_q1}", json={"valor": "290000"}, headers=h)
    print(f"  anticipo Q1: liquidacion_estado={ant['liquidacion_estado']} "
          f"bloqueado={ant['bloqueado']} · editar valor: {r.status_code} {r.text[:160]}")
    assert ant["liquidacion_estado"] == "aprobada"
    assert ant["bloqueado"] is False, "la pantalla lo muestra editable"
    assert r.status_code >= 400, "pero el servidor rebota la edición"
