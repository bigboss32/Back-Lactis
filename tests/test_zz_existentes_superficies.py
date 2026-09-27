"""LAS QUINCENAS QUE YA EXISTEN Y CUMPLEN LA REGLA: ¿qué dicen de ellas las OTRAS pantallas?

Pregunta del dueño: "¿y qué pasa con las que ya cumplen con esa regla?". El rótulo
"pagada · quedó debiendo" se calcula al leer (`Liquidacion.estado_visible`), así que le
llega solo a las filas viejas. Lo que se mide acá es TODO LO DEMÁS que muestra o cuenta
liquidaciones por su estado guardado: tablero, balance de contabilidad, Recepción diaria
(lista y grilla), la pantalla de Anticipos y el papel.

Las formas de fila que pueden estar hoy en la base del cliente, armadas con la API y —las
dos viejas— con la misma escritura que dejó el código de antes:

  A  'aprobada', saldo -120.000, deuda SIN cobrar.
  B  'aprobada', saldo -120.000, deuda YA COBRADA en la quincena siguiente.
  C  'pagada',   saldo -120.000, pagado 0  (el botón Pagar de entre el 01/08 y el arreglo).
  L  'pagada' ANTES de la migración a5e7c1b4d9f2 (pagos parciales, 01/08/2026): esa
     migración les escribió pagado = valor_total - anticipos y saldo = 0. Se reproduce
     corriendo EL MISMO UPDATE de la migración, tal cual está escrito.
  E  pagada normal que una corrección dejó debiendo (sobrepago): saldo -200.000.
  N  control: 'aprobada' con $130.000 por pagarle.

Todas son de $180.000 de leche (100 L a $1.800).
"""
import io
from decimal import Decimal

from pypdf import PdfReader
from sqlalchemy import text

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers

V = "/api/v1"
API = f"{V}/liquidaciones"
REC = f"{V}/recepciones"
ANT = f"{V}/anticipos"
PAGADA_DEBIENDO = "pagada · quedó debiendo"
Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")


def D(v):
    return Decimal(str(v))


def _proveedor(client, h, nombre):
    r = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": "1800"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros, precio=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": litros}
    if precio:
        cuerpo["precio_litro"] = precio
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, prov, fecha, valor):
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov["id"],
                               "fecha": fecha, "valor": valor}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, periodo):
    r = client.post(f"{API}/generar", json={
        "periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h)
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _escenario(client, h, db_session):
    """Arma las seis formas de fila. Devuelve un dict con proveedores, días, anticipos y
    liquidaciones por letra."""
    nombres = {"A": "Ana SinCobrar", "B": "Beto Cobrada", "C": "Carla PagadaVieja",
               "L": "Luis PreMigracion", "E": "Elsa Corregida", "N": "Nora Normal"}
    prov = {k: _proveedor(client, h, v) for k, v in nombres.items()}
    dia = {k: _recepcion(client, h, p, "2026-06-02", "100") for k, p in prov.items()}
    ant = {}
    for k in ("A", "B", "C", "L"):
        ant[k] = _anticipo(client, h, prov[k], "2026-06-01", "300000")
    for k in ("E", "N"):
        ant[k] = _anticipo(client, h, prov[k], "2026-06-01", "50000")

    gen = _generar(client, h, Q1)
    liq = {k: next(x for x in gen if x["proveedor_id"] == p["id"]) for k, p in prov.items()}
    for k, l in liq.items():
        r = client.post(f"{API}/{l['id']}/aprobar", headers=h)
        assert r.status_code == 200, r.text

    # L: el botón Pagar de ANTES de los pagos parciales pasaba 'aprobada' -> 'pagada' sin
    # mirar el saldo (git show 1caff9b^:app/modules/liquidaciones/service.py, `pagar`
    # = `_transicionar(... (ESTADO_APROBADA,), ESTADO_PAGADA)`). Y enseguida corrió la
    # migración a5e7c1b4d9f2 con ESTE UPDATE, copiado tal cual:
    fila_l = db_session.get(Liquidacion, __import__("uuid").UUID(liq["L"]["id"]))
    fila_l.estado = "pagada"
    db_session.commit()
    db_session.execute(text(
        """
        UPDATE liquidaciones
           SET pagado = COALESCE(valor_total, 0) - COALESCE(anticipos, 0),
               saldo = 0
         WHERE estado = 'pagada'
        """
    ))
    db_session.commit()

    # C: el botón Pagar de entre el 01/08 y el arreglo (rama `pendiente <= CERO` de
    # `pagar` en 1caff9b): solo cambia el estado, deja el saldo negativo y pagado en 0.
    fila_c = db_session.get(Liquidacion, __import__("uuid").UUID(liq["C"]["id"]))
    fila_c.estado = "pagada"
    db_session.commit()
    db_session.expire_all()

    # B: la quincena siguiente se cobra su deuda.
    _recepcion(client, h, prov["B"], "2026-06-20", "100", precio="2500")
    gen2 = _generar(client, h, Q2)
    liq["B2"] = next(x for x in gen2 if x["proveedor_id"] == prov["B"]["id"])
    assert D(liq["B2"]["saldo_anterior"]) == D("120000")

    # E: pagada normal ($130.000), aparece un adelanto olvidado de $200.000 y se corrige.
    r = client.post(f"{API}/{liq['E']['id']}/pagar", headers=h)
    assert r.status_code == 200, r.text
    _anticipo(client, h, prov["E"], "2026-06-10", "200000")
    prev = client.post(f"{API}/{liq['E']['id']}/corregir/previsualizar",
                       json={"motivo": "mirar"}, headers=h)
    assert prev.status_code == 200, prev.text
    suelto = prev.json()["anticipos_sueltos"][0]["anticipo_id"]
    r = client.post(f"{API}/{liq['E']['id']}/corregir", json={
        "motivo": "faltaba un adelanto", "anticipos_a_incluir": [suelto]}, headers=h)
    assert r.status_code == 200, r.text

    liq = {k: _leer(client, h, v["id"]) for k, v in liq.items()}
    for k in ("A", "B", "C", "L", "E", "N", "B2"):
        l = liq[k]
        print(f"\n  {k:2} estado={l['estado']:9} visible={l['estado_visible']!r:28} "
              f"valor={l['valor_total']:>10} ant={l['anticipos']:>10} "
              f"pagado={l['pagado']:>11} saldo={l['saldo']:>11} "
              f"debe={l['le_queda_debiendo']:>10} trasladada={bool(l['deuda_trasladada_a_id'])}")
    return prov, dia, ant, liq


# ===========================================================================
# 1. EL RÓTULO LES LLEGA A LAS FILAS QUE YA EXISTEN... MENOS A LAS DE ANTES DEL 01/08
# ===========================================================================
def test_1_el_rotulo_de_las_filas_que_ya_existen(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, _, _, liq = _escenario(client, h, db_session)
    for k in ("A", "B", "C", "E"):
        assert liq[k]["estado_visible"] == PAGADA_DEBIENDO, (k, liq[k]["estado_visible"])
    assert liq["N"]["estado_visible"] == "aprobada"

    # L QUEDÓ DEBIENDO $120.000 IGUAL QUE A y C —$180.000 de leche contra $300.000 de
    # adelanto— pero la migración de pagos parciales le puso saldo = 0 y pagado = -120.000.
    l = liq["L"]
    print(f"\n  L: valor {l['valor_total']} - anticipos {l['anticipos']} = neto "
          f"{l['neto_a_pagar']} ; pagado {l['pagado']} ; saldo {l['saldo']} ; "
          f"le_queda_debiendo {l['le_queda_debiendo']} ; visible {l['estado_visible']!r}")
    assert D(l["neto_a_pagar"]) == D("-120000")
    assert D(l["pagado"]) == D("-120000"), "la migración le dejó el pagado en NEGATIVO"
    assert D(l["saldo"]) == D("0")
    assert D(l["le_queda_debiendo"]) == D("0"), "la deuda de L no se ve por ningún lado"
    assert l["estado_visible"] == "pagada", "L NO recibe el rótulo aunque el tercero debe"


# ===========================================================================
# 2. TABLERO Y BALANCE: nada de lo que quedó debiendo se suma "por pagar"
# ===========================================================================
def test_2_tablero_y_balance_no_suman_la_deuda_como_por_pagar(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, _, _, liq = _escenario(client, h, db_session)
    dash = client.get(f"{V}/reportes/dashboard", headers=h)
    bal = client.get(f"{V}/contabilidad/balance", headers=h)
    assert dash.status_code == 200, dash.text
    assert bal.status_code == 200, bal.text
    dash, bal = dash.json(), bal.json()
    print(f"\n  tablero: por pagar {dash['liquidaciones_por_pagar']} · le deben "
          f"{dash['terceros_le_quedan_debiendo']}")
    print(f"  balance: por pagar {bal['liquidaciones_por_pagar']} · le deben "
          f"{bal['terceros_le_quedan_debiendo']}")

    # POR PAGAR = N ($130.000) + la quincena 2 de B ($250.000 - $120.000 = $130.000, en
    # borrador). Ninguna de las que dicen "pagada · quedó debiendo" entra.
    esperado_por_pagar = D(liq["N"]["saldo"]) + D(liq["B2"]["saldo"])
    assert esperado_por_pagar == D("260000")
    assert D(dash["liquidaciones_por_pagar"]) == esperado_por_pagar
    assert D(bal["liquidaciones_por_pagar"]) == esperado_por_pagar

    # LE QUEDAN DEBIENDO = A + C + E. B no (ya está restada en su quincena 2: correcto).
    esperado_deben = sum(D(liq[k]["le_queda_debiendo"]) for k in ("A", "C", "E"))
    assert esperado_deben == D("440000")
    assert D(dash["terceros_le_quedan_debiendo"]) == esperado_deben
    assert D(bal["terceros_le_quedan_debiendo"]) == esperado_deben

    # Y L, que debe $120.000 de verdad, no está en ninguna de las dos cifras.
    deuda_real_de_l = -(D(liq["L"]["valor_total"]) - D(liq["L"]["anticipos"]))
    assert deuda_real_de_l == D("120000")
    assert D(dash["terceros_le_quedan_debiendo"]) == esperado_deben  # sin los 120.000 de L


# ===========================================================================
# 3. LOS FILTROS DE LA LISTA (tarjetas) con las filas viejas
# ===========================================================================
def test_3_los_filtros_con_las_filas_viejas(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, _, _, liq = _escenario(client, h, db_session)

    def ids(estado):
        r = client.get(f"{API}?estado={estado}&page_size=100", headers=h)
        assert r.status_code == 200, r.text
        return {x["id"] for x in r.json()["items"]}

    aprobadas, parciales, pagadas = ids("aprobada"), ids("parcial"), ids("pagada")
    for k in ("A", "B", "C", "E"):
        assert liq[k]["id"] in pagadas, k
        assert liq[k]["id"] not in aprobadas and liq[k]["id"] not in parciales, k
    assert liq["N"]["id"] in aprobadas
    assert liq["L"]["id"] in pagadas


# ===========================================================================
# 4. RECEPCIÓN DIARIA — la lista y la grilla leen el estado GUARDADO
# ===========================================================================
def test_4_recepcion_diaria_de_la_quincena_cuya_deuda_ya_se_cobro(client, base_datos, db_session):
    """B: el chip de la lista dice "Aprobada: si corrige el día, vuelve a borrador" y la
    grilla la pinta como "en una liquidación sin pagar"; el servidor la tiene TRABADA."""
    h = auth_headers(client, "admin.a")
    _, dia, _, liq = _escenario(client, h, db_session)
    lista = client.get(f"{REC}/filtrar/avanzado?page=1&page_size=200", headers=h).json()["items"]
    por_id = {r["id"]: r for r in lista}

    b = por_id[dia["B"]["id"]]
    print(f"\n  lista B: liquidacion_estado={b['liquidacion_estado']!r} "
          f"leche_pagada={b['leche_pagada']} bloqueados={b['campos_bloqueados']}")
    print(f"           aviso={b['candado_aviso']!r}")
    assert liq["B"]["estado_visible"] == PAGADA_DEBIENDO
    assert b["liquidacion_estado"] == "aprobada"
    assert b["leche_pagada"] is True
    assert "cantidad_litros" in b["campos_bloqueados"]

    grilla = client.get(f"{REC}/grilla/quincena?desde={Q1[0]}&hasta={Q1[1]}", headers=h)
    assert grilla.status_code == 200, grilla.text
    fila = next(f for f in grilla.json()["filas"] if f["proveedor_id"] == liq["B"]["proveedor_id"])
    celda = fila["celdas"]["2026-06-02"]
    print(f"  grilla B: {celda}")
    assert celda["liquidacion_estado"] == "aprobada"
    assert celda["pagada"] is False, (
        "la grilla decide el candado con `pagada` (liquidacion_estado in pagada/parcial)"
    )
    assert celda["leche_pagada"] is True

    # Lo que la lista y la grilla le prometen al dueño ("si corrige el día, vuelve a
    # borrador y se recalcula") el servidor no lo deja hacer:
    r = client.put(f"{REC}/{dia['B']['id']}", json={"cantidad_litros": "90"}, headers=h)
    print(f"  PUT litros sobre el día de B -> {r.status_code} {r.text[:160]}")
    assert r.status_code in (400, 409, 422), r.text
    assert _leer(client, h, liq["B"]["id"])["estado"] == "aprobada"


def test_4b_recepcion_diaria_de_la_quincena_que_debe_sin_cobrar(client, base_datos, db_session):
    """A: la lista dice "Aprobada" al lado de un chip "pagada · quedó debiendo". Lo que
    promete el tooltip (vuelve a borrador) sí es cierto."""
    h = auth_headers(client, "admin.a")
    _, dia, _, liq = _escenario(client, h, db_session)
    lista = client.get(f"{REC}/filtrar/avanzado?page=1&page_size=200", headers=h).json()["items"]
    a = next(r for r in lista if r["id"] == dia["A"]["id"])
    print(f"\n  lista A: liquidacion_estado={a['liquidacion_estado']!r} "
          f"leche_pagada={a['leche_pagada']} aviso={a['candado_aviso']!r}")
    assert liq["A"]["estado_visible"] == PAGADA_DEBIENDO
    assert a["liquidacion_estado"] == "aprobada"
    assert a["leche_pagada"] is False and a["candado_aviso"] is None

    r = client.put(f"{REC}/{dia['A']['id']}", json={"cantidad_litros": "90"}, headers=h)
    assert r.status_code == 200, r.text
    despues = _leer(client, h, liq["A"]["id"])
    print(f"  tras corregir: estado={despues['estado']} visible={despues['estado_visible']!r} "
          f"debe={despues['le_queda_debiendo']}")
    assert despues["estado"] == "borrador"


def test_4c_recepcion_diaria_de_las_pagadas_viejas(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, dia, _, liq = _escenario(client, h, db_session)
    lista = client.get(f"{REC}/filtrar/avanzado?page=1&page_size=200", headers=h).json()["items"]
    por_id = {r["id"]: r for r in lista}
    for k in ("C", "L", "E"):
        r = por_id[dia[k]["id"]]
        print(f"\n  {k}: liquidacion_estado={r['liquidacion_estado']!r} "
              f"leche_pagada={r['leche_pagada']} visible={liq[k]['estado_visible']!r}")
        assert r["liquidacion_estado"] == liq[k]["estado"]
        assert r["leche_pagada"] is True


# ===========================================================================
# 5. ANTICIPOS — el aviso y el candado de la pantalla
# ===========================================================================
def test_5_anticipos_de_la_quincena_cuya_deuda_ya_se_cobro(client, base_datos, db_session):
    """B: la pantalla recibe liquidacion_estado='aprobada' y bloqueado=False, así que
    ofrece Editar/Eliminar con el aviso "vuelve a borrador". El servidor rebota."""
    h = auth_headers(client, "admin.a")
    _, _, ant, liq = _escenario(client, h, db_session)
    items = client.get(f"{ANT}?page=1&page_size=200", headers=h).json()["items"]
    b = next(a for a in items if a["id"] == ant["B"]["id"])
    print(f"\n  anticipo B: liquidacion_estado={b['liquidacion_estado']!r} "
          f"bloqueado={b['bloqueado']}")
    assert b["liquidacion_estado"] == "aprobada"
    assert b["bloqueado"] is False

    r = client.put(f"{ANT}/{ant['B']['id']}", json={"valor": "200000"}, headers=h)
    print(f"  PUT valor -> {r.status_code} {r.text[:160]}")
    assert r.status_code in (400, 409, 422), r.text
    r = client.delete(f"{ANT}/{ant['B']['id']}", headers=h)
    print(f"  DELETE    -> {r.status_code} {r.text[:120]}")
    assert r.status_code in (400, 409, 422), r.text


def test_5b_anticipos_de_las_otras_formas(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, _, ant, liq = _escenario(client, h, db_session)
    items = {a["id"]: a for a in client.get(f"{ANT}?page=1&page_size=200", headers=h).json()["items"]}
    for k in ("A", "C", "L"):
        a = items[ant[k]["id"]]
        print(f"\n  anticipo {k}: liquidacion_estado={a['liquidacion_estado']!r} "
              f"bloqueado={a['bloqueado']} visible={liq[k]['estado_visible']!r}")
    assert items[ant["A"]["id"]]["liquidacion_estado"] == "aprobada"
    assert items[ant["A"]["id"]]["bloqueado"] is False
    assert items[ant["C"]["id"]]["bloqueado"] is True
    assert items[ant["L"]["id"]]["bloqueado"] is True


# ===========================================================================
# 6. EL PAPEL DE L: el desglose no suma el renglón destacado
# ===========================================================================
def test_6_el_comprobante_de_la_pagada_de_antes_del_01_08(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, _, _, liq = _escenario(client, h, db_session)
    pdf = client.get(f"{API}/{liq['L']['id']}/pdf", headers=h)
    assert pdf.status_code == 200
    texto = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    renglones = [x for x in texto.splitlines()
                 if any(p in x.upper() for p in ("VALOR TOTAL", "ANTICIPO", "PAGADO", "SALDO",
                                                  "DEBIENDO", "ESTADO"))]
    print("\n  " + "\n  ".join(renglones))
    print("----- TEXTO COMPLETO -----\n" + texto)
    assert "ESTADO: PAGADA" in texto.upper()
    assert "QUEDÓ DEBIENDO" not in texto.upper() and "LE QUEDA DEBIENDO" not in texto.upper()
    assert "SALDO A PAGAR" in texto.upper()
    # EL DESGLOSE DEL PAPEL NO SUMA: $180.000 - $300.000 = -$120.000 y el renglón
    # destacado dice $0. El renglón "Pagado" (-$120.000) no sale porque solo se imprime
    # con pagado > 0 (service.py, `pagado_rows`).
    assert "VALOR TOTAL\n$180.000" in texto
    assert "Anticipos aplicados\n- $300.000" in texto
    assert "SALDO A PAGAR\n$0" in texto
    assert "\nPagado\n" not in texto


# ===========================================================================
# 7. EL AVISO DE RECEPCIÓN QUE NOMBRA EL ESTADO DEL FLETE CON LA PALABRA GUARDADA
# ===========================================================================
def test_7_el_aviso_dice_flete_sin_pagar_de_uno_que_la_lista_llama_pagada(client, base_datos):
    """Transportador con $50.000 de adelanto contra $30.000 de flete (100 L a $300): su
    liquidación se pinta "pagada · quedó debiendo". La leche del mismo día se le pagó al
    proveedor. El aviso del día (candado_aviso, que escribe el backend) dice "su flete
    todavía no se ha pagado (está en aprobada)"."""
    h = auth_headers(client, "admin.a")
    ruta = client.post(f"{V}/rutas", json={"nombre": "Vereda Alta", "municipio": "Granada"},
                       headers=h).json()
    t = client.post(f"{V}/transportadores", json={
        "nombre": "Alex Flete", "valor_transporte": "0",
        "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "300"}]}, headers=h)
    assert t.status_code == 201, t.text
    t = t.json()
    prov = _proveedor(client, h, "Pepa Leche")
    r = client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov["id"],
                               "cantidad_litros": "100", "transportador_id": t["id"],
                               "ruta_id": ruta["id"]}, headers=h)
    assert r.status_code == 201, r.text
    dia = r.json()
    r = client.post(ANT, json={"tipo": "transportador", "transportador_id": t["id"],
                               "fecha": "2026-06-01", "valor": "50000"}, headers=h)
    assert r.status_code == 201, r.text

    r = client.post(f"{API}/generar", json={"periodo_inicio": Q1[0], "periodo_fin": Q1[1],
                                            "tipo": "transportador"}, headers=h)
    assert r.status_code == 200, r.text
    flete = r.json()["generadas"][0]
    assert client.post(f"{API}/{flete['id']}/aprobar", headers=h).status_code == 200
    leche = _generar(client, h, Q1)[0]
    assert client.post(f"{API}/{leche['id']}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{leche['id']}/pagar", headers=h).status_code == 200

    flete = _leer(client, h, flete["id"])
    print(f"\n  flete: estado={flete['estado']} visible={flete['estado_visible']!r} "
          f"saldo={flete['saldo']}")
    assert flete["estado_visible"] == PAGADA_DEBIENDO

    fila = next(x for x in client.get(f"{REC}/filtrar/avanzado?page=1&page_size=50",
                                      headers=h).json()["items"] if x["id"] == dia["id"])
    print(f"  aviso del día: {fila['candado_aviso']!r}")
    assert fila["liquidacion_estado_flete"] == "aprobada"
    assert "su flete todavía no se ha pagado (está en aprobada)" in fila["candado_aviso"]
