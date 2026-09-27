"""REVISIÓN ADVERSARIAL (lente "verdad en pantalla y regresiones") de B1-B4.

Cada prueba arma un caso por su cuenta y mira lo que la pantalla recibe contra lo que el
servidor hace. Las que se llaman `test_d*` REPRODUCEN UN DEFECTO y pasan mientras el
defecto siga ahí (igual que los archivos zz de la auditoría); las `test_ok*` son
controles de lo que quedó bien. Nada de app/ se toca.
"""
import importlib.util
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.modules.liquidaciones.models import Liquidacion, PagoLiquidacion
from tests.conftest import auth_headers

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
MIGRACION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "a5e7c1b4d9f2_pagos_parciales_de_liquidaciones.py"
)


def D(v):
    return Decimal(str(v))


def _detalle(r):
    return r.json()["error"]["detail"]


def _proveedor(c, h, nombre, precio="1800"):
    r = c.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": precio}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _dia(c, h, prov, fecha, litros, precio=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": litros}
    if precio:
        cuerpo["precio_litro"] = precio
    r = c.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _adelanto(c, h, prov, fecha, valor):
    r = c.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov["id"],
                          "fecha": fecha, "valor": valor}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _generar(c, h, periodo, prov):
    r = c.post(f"{API}/generar", json={
        "periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov["id"])


def _leer(c, h, liq_id):
    r = c.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _motivo_del_candado_de_la_pantalla(fila):
    """COPIA LITERAL de `motivoDelCandado` (Front-Lactis anticipo-list.page.ts:120-133):
    es el texto que el dueño lee en el tooltip del candado de un anticipo."""
    if fila.get("pago_empleado_id"):
        return "Ya se le descontó al empleado en un pago de nómina: no se puede editar ni eliminar."
    if fila.get("liquidacion_estado") == "pagada":
        return ("La liquidación en la que se descontó ya se pagó. Si la cifra está mala, "
                "registre el ajuste en la quincena siguiente.")
    return ("La liquidación en la que se descontó ya tiene un pago registrado. Elimine "
            "primero ese pago si de verdad hay que corregirlo.")


def _correr_a5e7(db):
    spec = importlib.util.spec_from_file_location("a5e7_revision", MIGRACION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    class Op:
        def __getattr__(self, nombre):
            if nombre == "execute":
                return lambda s: db.execute(text(s))
            if nombre == "f":
                return lambda n: n
            return lambda *a, **k: None

    modulo.op = Op()
    modulo.upgrade()
    db.commit()
    db.expire_all()


def _migrada_con_adelanto(c, h, db, nombre):
    """90 L × $2.000 = $180.000 contra $300.000 de adelanto; 'pagada' con el botón viejo
    y después la migración: pagado −$120.000, saldo 0."""
    prov = _proveedor(c, h, nombre, precio="2000")
    _dia(c, h, prov, "2026-07-04", "90")
    adelanto = _adelanto(c, h, prov, "2026-07-06", "300000")
    liq = _generar(c, h, ("2026-07-01", "2026-07-15"), prov)
    r = c.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    fila = db.get(Liquidacion, uuid.UUID(liq["id"]))
    fila.estado = "pagada"
    db.commit()
    _correr_a5e7(db)
    leida = _leer(c, h, liq["id"])
    assert D(leida["pagado"]) == D("-120000") and D(leida["saldo"]) == 0
    return prov, adelanto, leida


# =====================================================================================
# D1. B2: el anticipo de la quincena cuya deuda ya se cobró sale con candado, pero la
#     pantalla explica el candado con un pago que NO existe.
# =====================================================================================
def test_d1_candado_del_anticipo_cobrado_se_explica_con_un_pago_que_no_existe(
        client, base_datos):
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Beto Cobrada")
    _dia(client, h, prov, "2026-06-02", "100")                 # 100 L × 1.800 = 180.000
    adelanto = _adelanto(client, h, prov, "2026-06-01", "300000")
    q1 = _generar(client, h, Q1, prov)
    assert client.post(f"{API}/{q1['id']}/aprobar", headers=h).status_code == 200
    _dia(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2, prov)
    assert D(q2["saldo_anterior"]) == D("120000")

    q1 = _leer(client, h, q1["id"])
    assert q1["deuda_trasladada_a_id"] == q2["id"]
    assert q1["pagos"] == [] and D(q1["pagado"]) == 0, "no hay pago en esa quincena"

    fila = client.get(f"{ANT}/{adelanto['id']}", headers=h).json()
    put = client.put(f"{ANT}/{adelanto['id']}", json={"valor": "200000"}, headers=h)
    tooltip = _motivo_del_candado_de_la_pantalla(fila)
    print(f"\n  AnticipoRead: bloqueado={fila['bloqueado']} "
          f"liquidacion_estado={fila['liquidacion_estado']!r} "
          f"pago_empleado_id={fila.get('pago_empleado_id')}")
    print(f"  tooltip del candado en pantalla: {tooltip!r}")
    print(f"  lo que dice el servidor al PUT:   {_detalle(put)!r}")
    assert fila["bloqueado"] is True
    assert put.status_code == 422 and "ya se le cobró" in _detalle(put)
    # EL DEFECTO: la pantalla manda a "eliminar primero ese pago" y ese pago no existe;
    # y la API no trae ningún campo con el motivo real para que la pantalla lo diga.
    assert "ya tiene un pago registrado" in tooltip
    assert not any(k in fila for k in ("candado_aviso", "motivo_bloqueo", "motivo_candado"))


# =====================================================================================
# D2. B4: la fila migrada que se CORRIGIÓ Y PAGÓ antes de este guardia (dos meses en
#     producción con el botón abierto). Se rehace por el camino real: corregir + Pagar
#     con el guardia apagado SOLO en ese paso (es el código de HEAD), y después se mira
#     qué dice el guardia nuevo.
# =====================================================================================
def _corregida_y_pagada_antes_del_guardia(c, h, db, monkeypatch, nombre, litros):
    import app.modules.liquidaciones.service as servicio

    prov, _, migrada = _migrada_con_adelanto(c, h, db, nombre)
    olvidado = _dia(c, h, prov, "2026-07-08", litros)
    with monkeypatch.context() as m:
        m.setattr(servicio, "_exigir_sin_deuda_borrada", lambda *a, **k: None)
        r = c.post(f"{API}/{migrada['id']}/corregir",
                   json={"motivo": "día olvidado", "recepciones_a_incluir": [olvidado["id"]]},
                   headers=h)
        assert r.status_code == 200, r.text
        r = c.post(f"{API}/{migrada['id']}/pagar", headers=h)
        assert r.status_code == 200, r.text
    leida = _leer(c, h, migrada["id"])
    assert D(leida["neto_a_pagar"]) == D(leida["pagado"]) + D(leida["saldo"])
    return prov, leida


def test_d2_la_cifra_de_la_deuda_borrada_miente_si_la_fila_ya_tiene_un_pago(
        client, base_datos, db_session, monkeypatch):
    """Día olvidado de 25 L × $2.000 = $50.000: neto 230.000 − 300.000 = −70.000, saldo
    −70.000 − (−120.000) = +50.000; Pagar entrega $50.000 y deja pagado −70.000.
    Con calculadora: el neto es −$70.000 y encima se le entregaron $50.000, así que el
    tercero debe $120.000, y lo que borró la migración fueron $120.000. La API y el
    mensaje dicen $70.000."""
    h = auth_headers(client, "admin.a")
    _, leida = _corregida_y_pagada_antes_del_guardia(
        client, h, db_session, monkeypatch, "Corregida Y Pagada", "25")
    entregado = sum((D(p["valor"]) for p in leida["pagos"]), D(0))
    neto = D(leida["neto_a_pagar"])
    debe_de_verdad = -neto + entregado
    abono = client.post(f"{API}/{leida['id']}/pagos",
                        json={"fecha": "2026-08-10", "valor": "1000"}, headers=h)
    print(f"\n  estado={leida['estado']} v{leida['version']} pagado={leida['pagado']} "
          f"saldo={leida['saldo']} neto={neto} entregado={entregado} "
          f"debe de verdad={debe_de_verdad}")
    print(f"  deuda_borrada_por_la_migracion={leida['deuda_borrada_por_la_migracion']}")
    print(f"  abonar -> {abono.status_code} {_detalle(abono)!r}")
    assert (neto, entregado, debe_de_verdad) == (D("-70000"), D("50000"), D("120000"))
    assert abono.status_code == 422
    # EL DEFECTO: la API y el mensaje dicen $70.000.
    assert D(leida["deuda_borrada_por_la_migracion"]) == D("70000")
    assert "($70.000)" in _detalle(abono) and "($120.000)" not in _detalle(abono)


def test_d2b_si_el_pago_de_antes_paso_de_cero_el_guardia_ya_no_la_ve(
        client, base_datos, db_session, monkeypatch):
    """Día olvidado de 100 L × $2.000 = $200.000: neto 380.000 − 300.000 = 80.000, saldo
    80.000 − (−120.000) = 200.000, y Pagar entregó $200.000: pagado 80.000, 'pagada'. Al
    tercero se le entregaron $120.000 de más, pero `pagado` ya no es negativo y el
    guardia no la reconoce: la siguiente corrección vuelve a mandar a Pagar."""
    h = auth_headers(client, "admin.a")
    prov, leida = _corregida_y_pagada_antes_del_guardia(
        client, h, db_session, monkeypatch, "Paso De Cero", "100")
    entregado = sum((D(p["valor"]) for p in leida["pagos"]), D(0))
    otro = _dia(client, h, prov, "2026-07-09", "5")              # $10.000
    prev = client.post(f"{API}/{leida['id']}/corregir/previsualizar",
                       json={"motivo": "otro día olvidado",
                             "recepciones_a_incluir": [otro["id"]]}, headers=h)
    print(f"\n  estado={leida['estado']} v{leida['version']} pagado={leida['pagado']} "
          f"entregado en pagos={entregado} neto={leida['neto_a_pagar']} "
          f"deuda_borrada={leida['deuda_borrada_por_la_migracion']}")
    print(f"  previsualizar -> {prev.status_code} saldo_despues="
          f"{prev.json().get('saldo_despues')} queda_por_entregar="
          f"{prev.json().get('queda_por_entregar')}")
    assert D(leida["pagado"]) == D("80000") and entregado == D("200000")
    # EL DEFECTO: pagado − Σpagos = 80.000 − 200.000 = −120.000 delata la migración,
    # pero el guardia mira solo `pagado < 0` y deja corregir y mandar a pagar $10.000.
    assert D(leida["deuda_borrada_por_la_migracion"]) == 0
    assert prev.status_code == 200 and D(prev.json()["saldo_despues"]) == D("10000")


# =====================================================================================
# D3. B4: el guardia del anticipo de una quincena migrada y ya corregida manda a usar
#     "Corregir esta quincena", que ahora rebota siempre en esa misma fila.
# =====================================================================================
def test_d3_el_anticipo_manda_a_corregir_la_quincena_que_ya_no_se_deja_corregir(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, adelanto, migrada = _migrada_con_adelanto(client, h, db_session, "V2 Migrada")
    fila = db_session.get(Liquidacion, uuid.UUID(migrada["id"]))
    # La forma que dejó corregir ANTES del guardia: parcial, v2, saldo +50.000.
    fila.estado, fila.version = "parcial", 2
    fila.valor_bruto = fila.valor_total = D("230000")
    fila.saldo = D("50000")
    db_session.commit()

    put = client.put(f"{ANT}/{adelanto['id']}", json={"valor": "250000"}, headers=h)
    olvidado = _dia(client, h, prov, "2026-07-09", "5")
    corregir = client.post(f"{API}/{migrada['id']}/corregir/previsualizar",
                           json={"motivo": "día olvidado",
                                 "recepciones_a_incluir": [olvidado["id"]]},
                           headers=h)
    print(f"\n  PUT anticipo -> {put.status_code} {_detalle(put)!r}")
    print(f"  corregir     -> {corregir.status_code} {_detalle(corregir)!r}")
    assert put.status_code == 422 and "use 'Corregir esta quincena'" in _detalle(put)
    assert corregir.status_code == 422 and "repararla" in _detalle(corregir)


# =====================================================================================
# OK1. B1: más formas y bordes de fecha; las tarjetas siguen siendo lo que da la lista.
# =====================================================================================
def _fila(db, emp, tercero, periodo, estado, vt, an="0", sa="0", pg="0"):
    vt, an, sa, pg = (D(x) for x in (vt, an, sa, pg))
    liq = Liquidacion(empresa_id=emp, tipo="proveedor", proveedor_id=uuid.UUID(tercero),
                      periodo_inicio=periodo[0], periodo_fin=periodo[1],
                      total_litros=D("100"), valor_bruto=vt, valor_total=vt, anticipos=an,
                      saldo_anterior=sa, pagado=pg, saldo=vt - an - sa - pg, estado=estado)
    db.add(liq)
    db.flush()
    return liq


def test_ok1_resumen_en_los_bordes_es_la_lista(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    a, b, c_ = (_proveedor(client, h, n)["id"] for n in ("Borde A", "Borde B", "Borde C"))
    P1 = (date(2026, 6, 1), date(2026, 6, 15))
    P2 = (date(2026, 6, 16), date(2026, 6, 30))
    # aprobada con saldo EXACTO en cero (la deuda vieja se comió el neto): cuenta en
    # aprobadas con $0 por pagar.
    _fila(db_session, emp, a, P2, "aprobada", "120000", sa="120000")
    # parcial con saldo negativo (se le pagó de más al corregir): se lee pagada y debe.
    _fila(db_session, emp, b, P1, "parcial", "100000", pg="150000")
    # borrador que debía y ya se la cobró otra: cuenta en borradores y NO en la deuda.
    cobrada = _fila(db_session, emp, c_, P1, "borrador", "100000", an="160000")
    cobra = _fila(db_session, emp, c_, P2, "aprobada", "200000", sa="60000")
    cobrada.deuda_trasladada_a_id = cobra.id
    db_session.commit()

    def lista(**q):
        r = client.get(API, params={**{k: v for k, v in q.items() if v is not None},
                                    "page_size": 200}, headers=h)
        assert r.status_code == 200, r.text
        return r.json()

    for desde, hasta in ((None, None), ("2026-06-15", None), (None, "2026-06-16"),
                         ("2026-06-15", "2026-06-15"), ("2026-06-16", "2026-06-16")):
        q = {"desde": desde, "hasta": hasta}
        r = client.get(f"{API}/resumen", params={k: v for k, v in q.items() if v}, headers=h)
        assert r.status_code == 200, r.text
        res = r.json()
        por = {e: lista(estado=e, **q) for e in ("borrador", "aprobada", "parcial", "pagada")}
        deben = [x for e in por for x in por[e]["items"]
                 if D(x["le_queda_debiendo"]) > 0 and not x["deuda_trasladada_a_id"]]
        esperado = {
            "borradores": por["borrador"]["total"], "aprobadas": por["aprobada"]["total"],
            "parciales": por["parcial"]["total"], "pagadas": por["pagada"]["total"],
            "saldo_aprobadas": sum((max(D(0), D(x["saldo"])) for x in por["aprobada"]["items"]), D(0)),
            "saldo_parciales": sum((max(D(0), D(x["saldo"])) for x in por["parcial"]["items"]), D(0)),
            "le_quedaron_debiendo": sum((D(x["le_queda_debiendo"]) for x in deben), D(0)),
            "liquidaciones_que_deben": len(deben),
        }
        obtenido = {k: (D(v) if isinstance(v, str) else v) for k, v in res.items()}
        print(f"\n  {q}: {obtenido}")
        assert obtenido == esperado, (q, obtenido, esperado)
    tablero = client.get(f"{V}/reportes/dashboard", headers=h).json()
    total = client.get(f"{API}/resumen", headers=h).json()
    assert D(tablero["terceros_le_quedan_debiendo"]) == D(total["le_quedaron_debiendo"]) == D("50000")


def test_ok2_resumen_y_lista_rebotan_igual_sin_empresa_y_con_tipo_vacio(client, base_datos):
    h = auth_headers(client, "admin.a")
    assert client.get(f"{API}/resumen", params={"tipo": ""}, headers=h).json()["pagadas"] == 0
    assert client.get(API, params={"tipo": ""}, headers=h).json()["total"] == 0
    hs = auth_headers(client, "superadmin")
    lista = client.get(API, headers=hs)
    res = client.get(f"{API}/resumen", headers=hs)
    print(f"\n  superadmin sin empresa: lista={lista.status_code} resumen={res.status_code}")
    assert lista.status_code == res.status_code


# =====================================================================================
# OK3. B2/B3 con la deuda cobrada de un BORRADOR (la cobranza también se lleva la de un
#      borrador): el día y el anticipo salen trabados y el servidor les da la razón.
# =====================================================================================
def test_ok3_borrador_cobrado_traba_dia_y_anticipo_en_la_grilla_y_en_el_guardia(
        client, base_datos):
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Borrador Cobrado")
    dia = _dia(client, h, prov, "2026-06-02", "100")
    adelanto = _adelanto(client, h, prov, "2026-06-01", "300000")
    q1 = _generar(client, h, Q1, prov)                          # borrador, debe 120.000
    _dia(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2, prov)
    assert D(q2["saldo_anterior"]) == D("120000")
    q1 = _leer(client, h, q1["id"])
    assert q1["estado"] == "borrador" and q1["deuda_trasladada_a_id"] == q2["id"]

    grilla = client.get(f"{REC}/grilla/quincena", params={"desde": Q1[0], "hasta": Q1[1]},
                        headers=h).json()
    celda = next(f for f in grilla["filas"] if f["proveedor_id"] == prov["id"])["celdas"]["2026-06-02"]
    anticipo = client.get(f"{ANT}/{adelanto['id']}", headers=h).json()
    put_dia = client.put(f"{REC}/{dia['id']}", json={"cantidad_litros": "90"}, headers=h)
    put_ant = client.put(f"{ANT}/{adelanto['id']}", json={"valor": "200000"}, headers=h)
    print(f"\n  celda pagada={celda['pagada']} aviso={celda['candado_aviso']!r}")
    print(f"  anticipo bloqueado={anticipo['bloqueado']}  PUT día={put_dia.status_code} "
          f"PUT anticipo={put_ant.status_code}")
    assert celda["pagada"] is True and put_dia.status_code == 422
    assert anticipo["bloqueado"] is True and put_ant.status_code == 422


# =====================================================================================
# OK4. B3: la corregida a la que se le borró el pago (vuelve a 'parcial'/'aprobada' con
#      v2) también sale con candado en la grilla, como la traba el PUT.
# =====================================================================================
def test_ok4_la_grilla_traba_la_quincena_ya_corregida_aunque_no_tenga_pagos(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Corregida Sin Pago")
    dia = _dia(client, h, prov, "2026-06-02", "100")
    q1 = _generar(client, h, Q1, prov)
    fila = db_session.get(Liquidacion, uuid.UUID(q1["id"]))
    fila.estado, fila.version = "aprobada", 2
    db_session.commit()
    grilla = client.get(f"{REC}/grilla/quincena", params={"desde": Q1[0], "hasta": Q1[1]},
                        headers=h).json()
    celda = next(f for f in grilla["filas"] if f["proveedor_id"] == prov["id"])["celdas"]["2026-06-02"]
    put = client.put(f"{REC}/{dia['id']}", json={"cantidad_litros": "90"}, headers=h)
    print(f"\n  v2 aprobada: celda pagada={celda['pagada']} PUT={put.status_code}")
    assert celda["pagada"] is (put.status_code == 422) is True
