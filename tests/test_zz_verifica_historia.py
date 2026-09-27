"""VERIFICACIÓN ADVERSARIAL de tests/test_zz_existentes_historia.py (lente "historia").

Se rehace cada hallazgo por un camino propio, sin reusar las utilidades del otro archivo,
y se miran las cifras que el otro no aisló: el tablero y el balance SIN generar la
quincena siguiente (en el original, el control ya había trasladado su deuda cuando se
leyó el tablero), el arreglo de datos sugerido, y qué pasa si después se borra el pago.

Solo SQLite (Docker apagado). El UPDATE de a5e7c1b4d9f2 es SQL plano.
"""
import importlib.util
import io
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import text

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
V = "/api/v1"
ROTULO = "pagada · quedó debiendo"
MIGRACION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "a5e7c1b4d9f2_pagos_parciales_de_liquidaciones.py"
)


def _hx(u):
    return u.replace("-", "")


def _sql(db, s, **p):
    db.execute(text(s), p)
    db.commit()
    db.expire_all()


def _correr_a5e7(db):
    spec = importlib.util.spec_from_file_location("a5e7_verifica", MIGRACION)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    class Op:
        def __getattr__(self, nombre):
            if nombre == "execute":
                return lambda s: db.execute(text(s))
            if nombre == "f":
                return lambda n: n
            return lambda *a, **k: None

    m.op = Op()
    m.upgrade()
    db.commit()
    db.expire_all()


def _prov(c, h, nombre):
    r = c.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "La Loma", "precio_litro": "2000"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _dia(c, h, pid, fecha, litros):
    r = c.post(f"{V}/recepciones", json={
        "fecha": fecha, "proveedor_id": pid, "cantidad_litros": litros}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _adelanto(c, h, pid, fecha, valor):
    r = c.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": pid, "fecha": fecha, "valor": valor}, headers=h)
    assert r.status_code == 201, r.text


def _generar(c, h, ini, fin):
    r = c.post(f"{API}/generar", json={
        "periodo_inicio": ini, "periodo_fin": fin, "tipo": "proveedor"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return {x["proveedor_id"]: x for x in r.json()["generadas"]}


def _julio_con_deuda(c, h, nombre, litros="90", adelanto="300000"):
    """Primera quincena de julio aprobada: 90 L x $2.000 = 180.000 contra 300.000."""
    pid = _prov(c, h, nombre)
    _dia(c, h, pid, "2026-07-04", litros)
    _adelanto(c, h, pid, "2026-07-06", adelanto)
    liq = _generar(c, h, "2026-07-01", "2026-07-15")[pid]
    r = c.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    return pid, r.json()


def _get(c, h, i):
    r = c.get(f"{API}/{i}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _tablero(c, h):
    r = c.get(f"{V}/reportes/dashboard", headers=h)
    assert r.status_code == 200, r.text
    return Decimal(str(r.json()["terceros_le_quedan_debiendo"]))


def _balance(c, h):
    r = c.get(f"{V}/contabilidad/balance", headers=h)
    assert r.status_code == 200, r.text
    return Decimal(str(r.json()["terceros_le_quedan_debiendo"]))


def _papel(c, h, i):
    r = c.get(f"{API}/{i}/pdf", headers=h)
    assert r.status_code == 200, r.text
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)


# ------------------------------------------------------------------------------ H1
def test_v_h1_tablero_y_balance_aislados(client, base_datos, db_session):
    """Solo la migrada en la empresa: tablero y balance en 0. Luego se agrega UNA fila con
    la forma de la ventana 01-04/08 (sin generar la siguiente): suben a 120.000 exactos,
    o sea que la migrada no aporta nada."""
    h = auth_headers(client, "admin.a")
    _, m = _julio_con_deuda(client, h, "Solo Migrada")
    assert Decimal(m["saldo"]) == Decimal("-120000.00")
    assert _tablero(client, h) == Decimal("120000"), "antes de pagar, la aprobada sí cuenta"
    _sql(db_session, "UPDATE liquidaciones SET estado='pagada' WHERE id=:i", i=_hx(m["id"]))
    _correr_a5e7(db_session)

    mig = _get(client, h, m["id"])
    print(f"\n  migrada: estado={mig['estado']} visible={mig['estado_visible']!r} "
          f"saldo={mig['saldo']} pagado={mig['pagado']} neto={mig['neto_a_pagar']} "
          f"le_debe={mig['le_queda_debiendo']} pagos={mig['pagos']}")
    t0, b0 = _tablero(client, h), _balance(client, h)
    print(f"  solo la migrada -> tablero={t0} balance={b0}")
    assert mig["estado_visible"] == "pagada"
    assert t0 == 0 and b0 == 0

    _, ctrl = _julio_con_deuda(client, h, "Ventana")
    _sql(db_session, "UPDATE liquidaciones SET estado='pagada' WHERE id=:i", i=_hx(ctrl["id"]))
    t1, b1 = _tablero(client, h), _balance(client, h)
    print(f"  + fila de la ventana -> tablero={t1} balance={b1}")
    assert t1 == Decimal("120000") and b1 == Decimal("120000")

    papel_m, papel_c = _papel(client, h, m["id"]), _papel(client, h, ctrl["id"])
    print(f"  PDF migrada: 'DEBIENDO'={'DEBIENDO' in papel_m.upper()} "
          f"'Pagado'={'PAGADO' in papel_m.upper()}")
    print(f"  PDF ventana: 'DEBIENDO'={'DEBIENDO' in papel_c.upper()}")
    assert "DEBIENDO" not in papel_m.upper()
    assert "DEBIENDO" in papel_c.upper()


def test_v_h1_pagada_con_neto_positivo_la_migracion_la_deja_bien(client, base_datos, db_session):
    """Control del hecho 12: la pagada de julio que SÍ tenía que recibir plata (saldo > 0)
    queda pagado = neto, saldo 0, rótulo 'pagada'."""
    h = auth_headers(client, "admin.a")
    _, liq = _julio_con_deuda(client, h, "Positiva", litros="200", adelanto="100000")
    assert Decimal(liq["saldo"]) == Decimal("300000.00")
    _sql(db_session, "UPDATE liquidaciones SET estado='pagada' WHERE id=:i", i=_hx(liq["id"]))
    _correr_a5e7(db_session)
    l = _get(client, h, liq["id"])
    print(f"\n  positiva: pagado={l['pagado']} saldo={l['saldo']} visible={l['estado_visible']!r}")
    assert Decimal(l["pagado"]) == Decimal("300000.00")
    assert Decimal(l["saldo"]) == 0
    assert l["estado_visible"] == "pagada"
    assert l["pagos"] == []
    papel = _papel(client, h, liq["id"])
    renglones = [x for x in papel.splitlines() if "Pagado" in x or "SALDO" in x.upper()]
    print(f"  PDF positiva, renglones de Pagado/Saldo: {renglones}")


def test_v_h1_arreglo_sugerido_la_vuelve_forma_de_ventana(client, base_datos, db_session):
    """SET saldo = pagado, pagado = 0 sobre la migrada: rótulo, tablero y cobro en la
    siguiente, igual que la forma de la ventana."""
    h = auth_headers(client, "admin.a")
    pid, m = _julio_con_deuda(client, h, "Arreglable")
    _sql(db_session, "UPDATE liquidaciones SET estado='pagada' WHERE id=:i", i=_hx(m["id"]))
    _correr_a5e7(db_session)
    filas = db_session.execute(text(
        "SELECT count(*) FROM liquidaciones WHERE deleted_at IS NULL AND pagado < 0")).scalar()
    print(f"\n  filas con pagado < 0 tras la migración: {filas}")
    assert filas == 1
    _sql(db_session, "UPDATE liquidaciones SET saldo = pagado, pagado = 0 "
         "WHERE pagado < 0 AND estado = 'pagada'")
    l = _get(client, h, m["id"])
    assert l["estado_visible"] == ROTULO
    assert Decimal(l["neto_a_pagar"]) == Decimal(l["pagado"]) + Decimal(l["saldo"])
    assert _tablero(client, h) == Decimal("120000")
    _dia(client, h, pid, "2026-07-20", "250")
    sig = _generar(client, h, "2026-07-16", "2026-07-31")[pid]
    print(f"  tras el arreglo -> visible={l['estado_visible']!r} siguiente: "
          f"saldo_anterior={sig['saldo_anterior']} neto={sig['neto_a_pagar']}")
    assert Decimal(sig["saldo_anterior"]) == Decimal("120000.00")
    assert Decimal(sig["neto_a_pagar"]) == Decimal("380000.00")


# ------------------------------------------------------------------------------ H2
def test_v_h2_corregir_y_pagar_la_migrada(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    pid, m = _julio_con_deuda(client, h, "Corrige Migrada")
    _sql(db_session, "UPDATE liquidaciones SET estado='pagada' WHERE id=:i", i=_hx(m["id"]))
    _correr_a5e7(db_session)
    dia = _dia(client, h, pid, "2026-07-08", "25")  # 50.000 olvidados
    cuerpo = {"motivo": "día olvidado", "recepciones_a_incluir": [dia]}
    prev = client.post(f"{API}/{m['id']}/corregir/previsualizar", json=cuerpo, headers=h)
    assert prev.status_code == 200, prev.text
    p = prev.json()
    print(f"\n  previa: neto_despues={p.get('neto_despues')} saldo_despues={p['saldo_despues']} "
          f"queda_por_entregar={p['queda_por_entregar']} visible={p['estado_visible_despues']!r} "
          f"avisos={p.get('avisos')}")
    assert Decimal(p["saldo_despues"]) == Decimal("50000.00")
    assert Decimal(p["queda_por_entregar"]) == Decimal("50000.00")

    hecho = client.post(f"{API}/{m['id']}/corregir", json=cuerpo, headers=h)
    assert hecho.status_code == 200, hecho.text
    c = hecho.json()
    print(f"  corregida: estado={c['estado']} saldo={c['saldo']} pagado={c['pagado']} "
          f"neto={c['neto_a_pagar']} version={c.get('version')}")
    assert c["estado"] == "parcial" and Decimal(c["saldo"]) == Decimal("50000.00")

    pag = client.post(f"{API}/{m['id']}/pagar", headers=h)
    assert pag.status_code == 200, pag.text
    q = pag.json()
    suma_pagos = sum(Decimal(x["valor"]) for x in q["pagos"])
    print(f"  pagada: estado={q['estado']} visible={q['estado_visible']!r} pagado={q['pagado']} "
          f"saldo={q['saldo']} suma de pagos={suma_pagos}")
    assert q["estado"] == "aprobada"
    assert Decimal(q["pagado"]) == Decimal("-70000.00")
    assert suma_pagos == Decimal("50000.00")

    # Y si el dueño se da cuenta y borra ese pago de 50.000:
    pago_id = q["pagos"][0]["id"]
    borra = client.delete(f"{API}/{m['id']}/pagos/{pago_id}", headers=h)
    print(f"  borrar el pago -> {borra.status_code} "
          f"{ {k: borra.json().get(k) for k in ('estado', 'estado_visible', 'pagado', 'saldo')} }")
    if borra.status_code == 200:
        b = borra.json()
        assert Decimal(b["pagado"]) == 0
        assert Decimal(b["saldo"]) == Decimal("-70000.00")


# ------------------------------------------------------------------------------ H3
def test_v_h3_neto_cero_por_deuda(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    pid, q1 = _julio_con_deuda(client, h, "Cero Por Deuda")
    _dia(client, h, pid, "2026-07-22", "60")  # 120.000 exactos
    q2 = _generar(client, h, "2026-07-16", "2026-07-31")[pid]
    r = client.post(f"{API}/{q2['id']}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    q2 = r.json()
    pagar = client.post(f"{API}/{q2['id']}/pagar", headers=h)
    lista = client.get(f"{API}?estado=aprobada&page_size=100", headers=h).json()
    print(f"\n  q2: estado={q2['estado']} visible={q2['estado_visible']!r} saldo={q2['saldo']} "
          f"saldo_anterior={q2['saldo_anterior']} pagar -> {pagar.status_code} "
          f"{pagar.json().get('detail')!r}")
    print(f"  ?estado=aprobada total={lista['total']} ids={[x['id'] for x in lista['items']]}")
    assert q2["estado_visible"] == "aprobada"
    assert pagar.status_code == 422
    assert q2["id"] in {x["id"] for x in lista["items"]}
