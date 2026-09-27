"""LAS QUINCENAS QUE YA ESTÁN EN PRODUCCIÓN, CON LA FORMA QUE LES DEJÓ EL CÓDIGO VIEJO.

Pregunta del dueño: "¿y qué pasa con las que ya cumplen con esa regla?". Las filas de
producción no las escribió el código de hoy: las escribieron versiones anteriores y una
migración que reescribió plata (a5e7c1b4d9f2, pagos parciales). Aquí se reproduce cada
forma que el historial de git deja posible, poniéndola en la base con SQL directo (o
corriendo la migración misma), y se lee lo que devuelve la API: `estado_visible`,
`le_queda_debiendo`, el filtro, la deuda que viaja y la corrección.

Solo SQLite: Docker no estaba prendido cuando se escribió, así que nada de esto corrió
contra Postgres. El UPDATE de la migración es SQL plano (COALESCE y una resta), y se
ejecuta aquí llamando a su propio `upgrade()` con un `op` de mentiras que solo ejecuta
los `op.execute`.
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
PAGADA_DEBIENDO = "pagada · quedó debiendo"
MIGRACION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "a5e7c1b4d9f2_pagos_parciales_de_liquidaciones.py"
)


# ----------------------------------------------------------------------- utilidades
def _proveedor(client, h, nombre, precio="2000"):
    r = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": precio}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _recepcion(client, h, prov_id, fecha, litros):
    r = client.post(f"{V}/recepciones", json={
        "fecha": fecha, "proveedor_id": prov_id, "cantidad_litros": litros}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _anticipo(client, h, prov_id, fecha, valor):
    r = client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": prov_id, "fecha": fecha, "valor": valor},
        headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, inicio, fin, prov_id):
    r = client.post(f"{API}/generar", json={
        "periodo_inicio": inicio, "periodo_fin": fin, "tipo": "proveedor"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov_id)


def _quincena_que_queda_debiendo(client, h, nombre):
    """180.000 de leche (90 L a $2.000) contra 300.000 de adelanto: debe 120.000.
    Aprobada, que es como la dejaba el código de antes del botón Pagar."""
    prov = _proveedor(client, h, nombre)
    _recepcion(client, h, prov["id"], "2026-07-02", "90")
    _anticipo(client, h, prov["id"], "2026-07-03", "300000")
    liq = _generar(client, h, "2026-07-01", "2026-07-15", prov["id"])
    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    liq = r.json()
    assert Decimal(liq["saldo"]) == Decimal("-120000.00")
    return prov, liq


def _sql(db, sentencia, **params):
    db.execute(text(sentencia), params)
    db.commit()
    db.expire_all()


def _hex(uuid_str):
    """SQLite guarda los Uuid de SQLAlchemy como 32 hex sin guiones."""
    return uuid_str.replace("-", "")


def _pagar_como_antes_del_1_de_agosto(db, liq_id):
    """El `pagar` de e455639..a38888b era `_transicionar(aprobada -> pagada)`: cambiaba
    el estado y NADA MÁS. Ni validaba el saldo ni tocaba cifras (ver
    `git show a38888b:app/modules/liquidaciones/service.py`, def pagar)."""
    _sql(db, "UPDATE liquidaciones SET estado='pagada' WHERE id=:id", id=_hex(liq_id))


def _correr_migracion_pagos_parciales(db):
    """Corre el `upgrade()` REAL de a5e7c1b4d9f2. Las columnas y la tabla ya existen
    (create_all), así que el `op` de mentiras solo ejecuta el UPDATE de datos."""
    spec = importlib.util.spec_from_file_location("migracion_a5e7", MIGRACION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    class OpDeMentiras:
        def add_column(self, *a, **k):
            pass

        def create_table(self, *a, **k):
            pass

        def create_index(self, *a, **k):
            pass

        def f(self, nombre):
            return nombre

        def execute(self, sentencia):
            db.execute(text(sentencia))

    modulo.op = OpDeMentiras()
    modulo.upgrade()
    db.commit()
    db.expire_all()


def _get(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _ids(client, h, estado):
    r = client.get(f"{API}?estado={estado}&page_size=100", headers=h)
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages).upper()


# --------------------------------------------------------------- la forma migrada
def test_la_pagada_de_julio_con_deuda_sale_de_la_migracion_sin_rotulo(
    client, base_datos, db_session
):
    """Forma M: 'pagada' antes del 01/08 con más adelanto que leche. La migración de
    pagos parciales le puso saldo = 0 y pagado = valor_total - anticipos (NEGATIVO)."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena_que_queda_debiendo(client, h, "Julio Pagada")
    _pagar_como_antes_del_1_de_agosto(db_session, liq["id"])

    antes = _get(client, h, liq["id"])
    print(f"\n  ANTES de a5e7: estado={antes['estado']} saldo={antes['saldo']} "
          f"pagado={antes['pagado']} visible={antes['estado_visible']!r}")

    _correr_migracion_pagos_parciales(db_session)
    despues = _get(client, h, liq["id"])
    print(f"  DESPUÉS de a5e7: estado={despues['estado']} saldo={despues['saldo']} "
          f"pagado={despues['pagado']} neto={despues['neto_a_pagar']} "
          f"le_queda_debiendo={despues['le_queda_debiendo']} "
          f"visible={despues['estado_visible']!r}")

    # Lo que hizo la migración con la fila:
    assert Decimal(despues["saldo"]) == Decimal("0")
    assert Decimal(despues["pagado"]) == Decimal("-120000.00")
    # La igualdad sigue cuadrando (con un pagado negativo):
    assert Decimal(despues["neto_a_pagar"]) == (
        Decimal(despues["pagado"]) + Decimal(despues["saldo"])
    )
    # Y lo que ve el dueño: el tercero DEBE 120.000 de verdad y la fila no lo dice.
    assert Decimal(despues["le_queda_debiendo"]) == Decimal("0")
    assert despues["estado_visible"] == "pagada"
    assert liq["id"] in _ids(client, h, "pagada")

    papel = _pdf(client, h, liq["id"])
    print(f"  PDF: 'QUEDÓ DEBIENDO' en el papel? {'QUEDÓ DEBIENDO' in papel} · "
          f"'LE QUEDA DEBIENDO'? {'LE QUEDA DEBIENDO' in papel}")
    assert "QUEDÓ DEBIENDO" not in papel


def test_la_deuda_que_borro_la_migracion_no_se_cobra_en_la_siguiente(
    client, base_datos, db_session
):
    """La deuda de la forma M no viaja: `deudas_sin_cobrar` pide saldo < 0. Control: la
    misma quincena en la forma de la ventana 01/08-04/08 (pagada, saldo -120.000,
    pagado 0) sí la viaja."""
    h = auth_headers(client, "admin.a")
    prov_m, liq_m = _quincena_que_queda_debiendo(client, h, "Migrada")
    _pagar_como_antes_del_1_de_agosto(db_session, liq_m["id"])
    _correr_migracion_pagos_parciales(db_session)

    prov_c, liq_c = _quincena_que_queda_debiendo(client, h, "Control Ventana")
    # El `pagar` de 1caff9b (01/08 a 04/08): `pendiente <= 0` -> solo cambia el estado.
    _sql(db_session, "UPDATE liquidaciones SET estado='pagada' WHERE id=:id",
         id=_hex(liq_c["id"]))
    control = _get(client, h, liq_c["id"])
    assert control["estado_visible"] == PAGADA_DEBIENDO

    for prov in (prov_m, prov_c):
        _recepcion(client, h, prov["id"], "2026-07-20", "250")
    r = client.post(f"{API}/generar", json={
        "periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31", "tipo": "proveedor"},
        headers=h)
    assert r.status_code in (200, 201), r.text
    generadas = {x["proveedor_id"]: x for x in r.json()["generadas"]}
    sig_m, sig_c = generadas[prov_m["id"]], generadas[prov_c["id"]]
    print(f"\n  siguiente de la MIGRADA: saldo_anterior={sig_m['saldo_anterior']} "
          f"neto={sig_m['neto_a_pagar']}")
    print(f"  siguiente del CONTROL:   saldo_anterior={sig_c['saldo_anterior']} "
          f"neto={sig_c['neto_a_pagar']}")
    assert Decimal(sig_c["saldo_anterior"]) == Decimal("120000.00")
    assert Decimal(sig_m["saldo_anterior"]) == Decimal("0"), (
        "la migrada no le cobra los 120.000 que el tercero debe"
    )

    # El tablero tampoco la cuenta entre lo que le deben a la quesera.
    tablero = client.get(f"{V}/reportes/dashboard", headers=h)
    if tablero.status_code == 200:
        print(f"  tablero terceros_le_quedan_debiendo="
              f"{tablero.json().get('terceros_le_quedan_debiendo')}")


def test_corregir_la_migrada_manda_a_entregarle_plata_a_quien_debe(
    client, base_datos, db_session
):
    """Con pagado NEGATIVO, `saldo = neto - pagado` le suma la deuda borrada al saldo.
    Se le agrega un día olvidado de 50.000 (25 L): la verdad es 230.000 - 300.000 =
    -70.000 (sigue debiendo 70.000). Control con la forma de la ventana."""
    h = auth_headers(client, "admin.a")
    prov_m, liq_m = _quincena_que_queda_debiendo(client, h, "Migrada Corrige")
    _pagar_como_antes_del_1_de_agosto(db_session, liq_m["id"])
    _correr_migracion_pagos_parciales(db_session)

    prov_c, liq_c = _quincena_que_queda_debiendo(client, h, "Control Corrige")
    _sql(db_session, "UPDATE liquidaciones SET estado='pagada' WHERE id=:id",
         id=_hex(liq_c["id"]))

    resultados = {}
    for nombre, prov, liq in (("MIGRADA", prov_m, liq_m), ("CONTROL", prov_c, liq_c)):
        dia = _recepcion(client, h, prov["id"], "2026-07-05", "25")
        payload = {"motivo": "faltaba un día", "recepciones_a_incluir": [dia["id"]]}
        r = client.post(f"{API}/{liq['id']}/corregir/previsualizar", json=payload, headers=h)
        assert r.status_code == 200, r.text
        prev = r.json()
        resultados[nombre] = prev
        print(f"\n  {nombre}: valor_total_despues={prev.get('valor_total_despues')} "
              f"neto_despues={prev.get('neto_despues')} saldo_despues={prev['saldo_despues']} "
              f"estado_despues={prev['estado_despues']} "
              f"visible_despues={prev['estado_visible_despues']!r} "
              f"queda_por_entregar={prev['queda_por_entregar']} "
              f"se_le_pago_de_mas={prev['se_le_pago_de_mas']}")

    assert Decimal(resultados["CONTROL"]["saldo_despues"]) == Decimal("-70000.00")
    assert resultados["CONTROL"]["estado_visible_despues"] == PAGADA_DEBIENDO
    assert Decimal(resultados["MIGRADA"]["saldo_despues"]) == Decimal("50000.00")
    assert Decimal(resultados["MIGRADA"]["queda_por_entregar"]) == Decimal("50000.00")
    assert resultados["MIGRADA"]["estado_visible_despues"] == "parcial"

    # Y si se confirma y se oprime Pagar, salen 50.000 de la caja hacia quien debe 70.000.
    hecho = client.post(f"{API}/{liq_m['id']}/corregir", json={
        "motivo": "faltaba un día",
        "recepciones_a_incluir": [resultados["MIGRADA"]["dias_sueltos"][0]["recepcion_id"]]
        if resultados["MIGRADA"].get("dias_sueltos") else []},
        headers=h)
    print(f"  corregir MIGRADA -> {hecho.status_code} "
          f"{ {k: hecho.json().get(k) for k in ('estado', 'estado_visible', 'saldo', 'pagado')} }")
    if hecho.status_code == 200 and Decimal(hecho.json()["saldo"]) > 0:
        pagar = client.post(f"{API}/{liq_m['id']}/pagar", headers=h)
        cuerpo = pagar.json()
        print(f"  pagar MIGRADA -> {pagar.status_code} estado={cuerpo.get('estado')} "
              f"visible={cuerpo.get('estado_visible')!r} pagado={cuerpo.get('pagado')} "
              f"saldo={cuerpo.get('saldo')} pagos={[p['valor'] for p in cuerpo.get('pagos', [])]}")


# ---------------------------------------------------- el mapa de formas y su rótulo
def test_mapa_de_formas_historicas_y_su_rotulo(client, base_datos, db_session):
    """Cada forma que el historial deja posible, puesta con SQL sobre una fila base, y lo
    que devuelve GET /{id}. La tabla se imprime con -s."""
    h = auth_headers(client, "admin.a")
    _, base = _quincena_que_queda_debiendo(client, h, "Mapa")
    otra_prov = _proveedor(client, h, "Cobradora")
    _recepcion(client, h, otra_prov["id"], "2026-07-20", "250")
    cobradora = _generar(client, h, "2026-07-16", "2026-07-31", otra_prov["id"])

    formas = [
        # (nombre, estado, valor_total, anticipos, saldo_anterior, pagado, saldo, trasladada, esperado)
        ("aprobada debe (post 04/08)", "aprobada", 180000, 300000, 0, 0, -120000, False,
         PAGADA_DEBIENDO),
        ("aprobada debe · cobrada", "aprobada", 180000, 300000, 0, 0, -120000, True,
         PAGADA_DEBIENDO),
        ("pagada debe (ventana 01-04/08)", "pagada", 180000, 300000, 0, 0, -120000, False,
         PAGADA_DEBIENDO),
        ("pagada · migrada a5e7", "pagada", 180000, 300000, 0, -120000, 0, False, "pagada"),
        ("pagada de más tras corregir", "pagada", 400000, 0, 0, 500000, -100000, False,
         PAGADA_DEBIENDO),
        ("parcial con saldo < 0 (SQL)", "parcial", 180000, 300000, 0, 0, -120000, False,
         PAGADA_DEBIENDO),
        ("borrador debe", "borrador", 180000, 300000, 0, 0, -120000, False, "borrador"),
        ("anulada debe", "anulada", 180000, 300000, 0, 0, -120000, False, "anulada"),
        ("pagada exacta por anticipo", "pagada", 180000, 180000, 0, 0, 0, False, "pagada"),
        ("aprobada neto 0 por deuda", "aprobada", 120000, 0, 120000, 0, 0, False, "aprobada"),
        ("aprobada saldo -0.00", "aprobada", 180000, 180000, 0, 0, -0.0, False,
         "aprobada"),
        ("aprobada debe un centavo", "aprobada", 180000, 180000.01, 0, 0,
         -0.01, False, PAGADA_DEBIENDO),
    ]
    print()
    malas = []
    for (nombre, estado, vt, ant, sa, pagado, saldo, trasladada, esperado) in formas:
        _sql(db_session, """
            UPDATE liquidaciones
               SET estado=:estado, valor_total=:vt, anticipos=:ant, saldo_anterior=:sa,
                   pagado=:pagado, saldo=:saldo, deuda_trasladada_a_id=:tras
             WHERE id=:id
        """, estado=estado, vt=vt, ant=ant, sa=sa, pagado=pagado, saldo=saldo,
             tras=_hex(cobradora["id"]) if trasladada else None, id=_hex(base["id"]))
        leida = _get(client, h, base["id"])
        en_pagadas = base["id"] in _ids(client, h, "pagada")
        en_aprobadas = base["id"] in _ids(client, h, "aprobada")
        print(f"  {nombre:32s} estado={leida['estado']:9s} saldo={leida['saldo']:>11s} "
              f"le_debe={leida['le_queda_debiendo']:>10s} -> {leida['estado_visible']!r:28s} "
              f"filtro pagada={en_pagadas!s:5s} aprobada={en_aprobadas}")
        if leida["estado_visible"] != esperado:
            malas.append((nombre, leida["estado_visible"], esperado))
    assert not malas, malas


# --------------------------------------------------- el neto en cero por la deuda
def test_el_neto_en_cero_por_la_deuda_se_sigue_leyendo_aprobada(client, base_datos):
    """Quincena 1 deja 120.000 de deuda; la 2 vale EXACTO 120.000 (60 L). Nada que
    entregarle, Pagar rebota, y la fila sigue diciendo 'aprobada' y saliendo en el
    filtro Aprobadas."""
    h = auth_headers(client, "admin.a")
    prov, q1 = _quincena_que_queda_debiendo(client, h, "Neto Cero")
    _recepcion(client, h, prov["id"], "2026-07-20", "60")
    q2 = _generar(client, h, "2026-07-16", "2026-07-31", prov["id"])
    r = client.post(f"{API}/{q2['id']}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    q2 = r.json()
    pagar = client.post(f"{API}/{q2['id']}/pagar", headers=h)
    q1_leida = _get(client, h, q1["id"])
    print(f"\n  q1: visible={q1_leida['estado_visible']!r} cobrada_en={q1_leida['deuda_trasladada_a_id']}")
    print(f"  q2: estado={q2['estado']} saldo_anterior={q2['saldo_anterior']} "
          f"saldo={q2['saldo']} visible={q2['estado_visible']!r} "
          f"en filtro aprobada={q2['id'] in _ids(client, h, 'aprobada')} "
          f"pagar -> {pagar.status_code} {pagar.json().get('detail', '')[:90]!r}")
    assert Decimal(q2["saldo"]) == Decimal("0")
    assert pagar.status_code >= 400
    assert q2["estado_visible"] == "aprobada"
    assert q2["id"] in _ids(client, h, "aprobada")
