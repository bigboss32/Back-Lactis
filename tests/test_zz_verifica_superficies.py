"""VERIFICADOR ADVERSARIAL del lente "superficies" (SUP-1..SUP-4).

Escenario propio, armado aparte del de test_zz_existentes_superficies.py, para no heredar
sus supuestos. La forma L se arma corriendo LA FUNCION `upgrade()` DE LA MIGRACION REAL
(a5e7c1b4d9f2) con un `op` falso que solo recoge el SQL de `op.execute`: no se copia el
UPDATE a mano.
"""
import importlib.util
import io
import uuid
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import text

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers

V = "/api/v1"
API = f"{V}/liquidaciones"
REC = f"{V}/recepciones"
ANT = f"{V}/anticipos"
Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
ROTULO = "pagada · quedó debiendo"
MIGRACION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
    "a5e7c1b4d9f2_pagos_parciales_de_liquidaciones.py"


def D(v):
    return Decimal(str(v))


def _sql_de_la_migracion() -> list[str]:
    spec = importlib.util.spec_from_file_location("mig_a5e7", MIGRACION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class OpFalso:
        def __init__(self):
            self.sql = []

        def execute(self, s):
            self.sql.append(str(s))

        def __getattr__(self, nombre):  # add_column, create_table, create_index, f...
            return lambda *a, **k: nombre

    falso = OpFalso()
    mod.op = falso
    mod.upgrade()
    assert len(falso.sql) == 1, falso.sql
    return falso.sql


def _prov(c, h, nombre):
    r = c.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "V", "precio_litro": "1800"},
               headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _rec(c, h, p, fecha, litros="100"):
    r = c.post(REC, json={"fecha": fecha, "proveedor_id": p["id"], "cantidad_litros": litros},
               headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _ant(c, h, p, fecha, valor):
    r = c.post(ANT, json={"tipo": "proveedor", "proveedor_id": p["id"], "fecha": fecha,
                          "valor": valor}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _gen(c, h, q):
    r = c.post(f"{API}/generar", json={"periodo_inicio": q[0], "periodo_fin": q[1],
                                       "tipo": "proveedor"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _get(c, h, i):
    r = c.get(f"{API}/{i}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_v1_forma_L_por_la_migracion_real(client, base_datos, db_session):
    """SUP-1: la migración real deja a L sin rótulo, sin deuda y sin cobro posterior;
    C (misma plata, pagada DESPUÉS de la migración) sí se cobra."""
    h = auth_headers(client, "admin.a")
    L, C = _prov(client, h, "Lalo Vieja"), _prov(client, h, "Cata Vieja")
    for p in (L, C):
        _rec(client, h, p, "2026-06-03")
        _ant(client, h, p, "2026-06-01", "300000")
    g = {x["proveedor_id"]: x for x in _gen(client, h, Q1)}
    lid, cid = g[L["id"]]["id"], g[C["id"]]["id"]
    for i in (lid, cid):
        assert client.post(f"{API}/{i}/aprobar", headers=h).status_code == 200
    # El botón Pagar de hoy rebota el saldo negativo (la ruta que dejaría 'pagada').
    r = client.post(f"{API}/{lid}/pagar", headers=h)
    print(f"\n  pagar hoy sobre saldo -120.000 -> {r.status_code}")
    assert r.status_code == 422

    # ANTES del 01/08: el Pagar viejo ponía 'pagada' sin mirar saldo; enseguida la migración.
    db_session.get(Liquidacion, uuid.UUID(lid)).estado = "pagada"
    db_session.commit()
    for s in _sql_de_la_migracion():
        db_session.execute(text(s))
    db_session.commit()
    # C: 'pagada' puesta DESPUÉS de la migración (saldo negativo intacto).
    db_session.get(Liquidacion, uuid.UUID(cid)).estado = "pagada"
    db_session.commit()
    db_session.expire_all()

    l, c = _get(client, h, lid), _get(client, h, cid)
    print(f"  L: visible={l['estado_visible']!r} pagado={l['pagado']} saldo={l['saldo']} "
          f"debe={l['le_queda_debiendo']} neto={l['neto_a_pagar']}")
    print(f"  C: visible={c['estado_visible']!r} pagado={c['pagado']} saldo={c['saldo']} "
          f"debe={c['le_queda_debiendo']}")
    assert l["estado_visible"] == "pagada"
    assert D(l["pagado"]) == D("-120000") and D(l["saldo"]) == 0
    assert D(l["neto_a_pagar"]) == D(l["pagado"]) + D(l["saldo"])  # la igualdad sí cuadra
    assert c["estado_visible"] == ROTULO

    dash = client.get(f"{V}/reportes/dashboard", headers=h).json()
    print(f"  tablero le deben = {dash['terceros_le_quedan_debiendo']}")
    assert D(dash["terceros_le_quedan_debiendo"]) == D("120000")  # solo C

    # La quincena siguiente: a C se le cobra la deuda, a L NO.
    for p in (L, C):
        _rec(client, h, p, "2026-06-20")
    g2 = {x["proveedor_id"]: x for x in _gen(client, h, Q2)}
    print(f"  Q2 L saldo_anterior={g2[L['id']]['saldo_anterior']} · "
          f"Q2 C saldo_anterior={g2[C['id']]['saldo_anterior']}")
    assert D(g2[L["id"]]["saldo_anterior"]) == 0
    assert D(g2[C["id"]]["saldo_anterior"]) == D("120000")

    pdf = client.get(f"{API}/{lid}/pdf", headers=h)
    texto = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Estado: PAGADA" in texto
    assert "VALOR TOTAL\n$180.000" in texto and "Anticipos aplicados\n- $300.000" in texto
    assert "SALDO A PAGAR\n$0" in texto and "\nPagado\n" not in texto
    assert "DEBIENDO" not in texto.upper()


def _forma_B(client, h):
    B = _prov(client, h, "Bruno Cobrada")
    dia = _rec(client, h, B, "2026-06-03")
    ant = _ant(client, h, B, "2026-06-01", "300000")
    lb = _gen(client, h, Q1)[0]
    assert client.post(f"{API}/{lb['id']}/aprobar", headers=h).status_code == 200
    _rec(client, h, B, "2026-06-20", "200")
    g2 = _gen(client, h, Q2)[0]
    assert D(g2["saldo_anterior"]) == D("120000")
    lb = _get(client, h, lb["id"])
    assert lb["estado"] == "aprobada" and lb["deuda_trasladada_a_id"] == g2["id"]
    assert lb["estado_visible"] == ROTULO
    return B, dia, ant, lb


def test_v2_recepcion_de_B(client, base_datos):
    """SUP-2: grilla sin candado (`pagada` False), lista 'aprobada'; litros 422; y si solo
    se corrigen las observaciones el servidor acepta y la liquidación NO vuelve a borrador."""
    h = auth_headers(client, "admin.a")
    B, dia, _, lb = _forma_B(client, h)
    grilla = client.get(f"{REC}/grilla/quincena?desde={Q1[0]}&hasta={Q1[1]}", headers=h).json()
    celda = next(f for f in grilla["filas"] if f["proveedor_id"] == B["id"])["celdas"]["2026-06-03"]
    print(f"\n  celda B: {celda}")
    assert celda["pagada"] is False and celda["leche_pagada"] is True
    assert celda["liquidada"] is True and celda["liquidacion_estado"] == "aprobada"

    fila = next(x for x in client.get(f"{REC}/filtrar/avanzado?page=1&page_size=100",
                                      headers=h).json()["items"] if x["id"] == dia["id"])
    assert fila["liquidacion_estado"] == "aprobada" and fila["leche_pagada"] is True
    assert "cantidad_litros" in fila["campos_bloqueados"]
    assert "observaciones" in fila["campos_editables"]

    r = client.put(f"{REC}/{dia['id']}", json={"cantidad_litros": "90"}, headers=h)
    print(f"  PUT litros -> {r.status_code}")
    assert r.status_code == 422
    r = client.put(f"{REC}/{dia['id']}", json={"observaciones": "tarro sucio"}, headers=h)
    despues = _get(client, h, lb["id"])
    print(f"  PUT observaciones -> {r.status_code}; estado despues={despues['estado']}")
    assert r.status_code == 200, r.text
    assert despues["estado"] == "aprobada"  # el snackbar "volvió a borrador" no es cierto


def test_v3_anticipo_de_B(client, base_datos):
    """SUP-3: el anticipo de B viaja bloqueado=False y el servidor rebota PUT y DELETE."""
    h = auth_headers(client, "admin.a")
    _, _, ant, _ = _forma_B(client, h)
    items = client.get(f"{ANT}?page=1&page_size=100", headers=h).json()["items"]
    a = next(x for x in items if x["id"] == ant["id"])
    print(f"\n  anticipo B: estado={a['liquidacion_estado']!r} bloqueado={a['bloqueado']}")
    assert a["liquidacion_estado"] == "aprobada" and a["bloqueado"] is False
    r1 = client.put(f"{ANT}/{ant['id']}", json={"valor": "250000"}, headers=h)
    r2 = client.delete(f"{ANT}/{ant['id']}", headers=h)
    print(f"  PUT {r1.status_code} · DELETE {r2.status_code}")
    assert r1.status_code == 422 and r2.status_code == 422


def test_v4_forma_A_y_hecho_E(client, base_datos, db_session):
    """SUP-4 (A: editable, chip 'aprobada', corrige -> borrador) y el hecho de que los
    anticipos de E salen bloqueados (el test_5b del hallazgo no mira E)."""
    h = auth_headers(client, "admin.a")
    from tests.test_zz_existentes_superficies import _escenario
    _, dia, ant, liq = _escenario(client, h, db_session)
    items = {a["id"]: a for a in client.get(f"{ANT}?page=1&page_size=200",
                                            headers=h).json()["items"]}
    e_ants = [a for a in items.values() if a["liquidacion_id"] == liq["E"]["id"]]
    print(f"\n  anticipos de E: {[(a['valor'], a['liquidacion_estado'], a['bloqueado']) for a in e_ants]}")
    assert e_ants and all(a["bloqueado"] for a in e_ants)

    fila = next(x for x in client.get(f"{REC}/filtrar/avanzado?page=1&page_size=200",
                                      headers=h).json()["items"] if x["id"] == dia["A"]["id"])
    assert fila["liquidacion_estado"] == "aprobada" and fila["leche_pagada"] is False
    assert liq["A"]["estado_visible"] == ROTULO
