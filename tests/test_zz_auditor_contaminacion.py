"""AUDITOR INDEPENDIENTE: el libro anterior NO puede contaminar las cifras del negocio.

Escrito desde cero (no reusa los tests de los agentes que implementaron).
Sesgo: acusar. Cada prueba imprime la evidencia literal.
"""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PER = {"desde": "2026-07-01", "hasta": "2026-07-31"}

# Todo lo que el contrato prohibe mover
INTOCABLES = [
    "kilos_comprados", "total_compras", "kilos_vendidos", "total_ventas",
    "precio_promedio_compra", "precio_promedio_venta", "total_gastos",
    "ganancia_estimada", "margen_por_kilo", "valor_realizado_kilo",
    "kilos_borona_vendidos", "total_ventas_borona", "kilos_a_borona",
    "kilos_merma", "kilos_pendientes", "kilos_disponibles", "borona_disponible",
]


def D(x):
    return Decimal(str(x))


def suma(items, campo):
    return sum((D(i[campo]) for i in items), Decimal("0"))


def resumen(client, h, **params):
    r = client.get(f"{API}/resumen", params={**PER, **params}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def compra(client, h, **kw):
    r = client.post(f"{API}/compras", json=kw, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, h, **kw):
    r = client.post(f"{API}/ventas", json=kw, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def conversion(client, h, **kw):
    r = client.post(f"{API}/conversiones", json=kw, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def saldo(client, h, esperado=201, **kw):
    r = client.post(f"{API}/saldos-anteriores", json=kw, headers=h)
    assert r.status_code == esperado, r.text
    return r.json()


def abonar(client, h, saldo_id, esperado=200, **kw):
    r = client.post(f"{API}/saldos-anteriores/{saldo_id}/abonos", json=kw, headers=h)
    assert r.status_code == esperado, r.text
    return r.json()


def sembrar(client, h):
    """Un negocio de reventa realista dentro del periodo."""
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="800", borona_kilos="50", precio_kilo="18000")
    compra(client, h, fecha="2026-07-05", productor="Carlos Ricaute",
           kilos_brutos="300", precio_kilo="17500")
    venta(client, h, fecha="2026-07-10", cliente="Alba Nieto", kilos="400",
          precio_kilo="19500", pagada_de_contado=True,
          gasto_por_kilo="300", gasto_concepto="Flete")
    venta(client, h, fecha="2026-07-12", cliente="Yojan Pérez", kilos="250",
          precio_kilo="20000")
    venta(client, h, fecha="2026-07-18", cliente="Alba Nieto", tipo="borona",
          kilos="40", precio_kilo="9000")
    conversion(client, h, fecha="2026-07-15", kilos="30", destino="borona")
    conversion(client, h, fecha="2026-07-16", kilos="10", destino="merma")


def diferencias(antes, despues, campos):
    return [
        (c, antes[c], despues[c]) for c in campos if D(antes[c]) != D(despues[c])
    ]


# ---------------------------------------------------------------------------
# 1. El resumen no se mueve
# ---------------------------------------------------------------------------
def test_1_agregar_saldos_no_mueve_ninguna_cifra_del_negocio(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    antes = resumen(client, h)

    saldo(client, h, tipo="cobrar", tercero="Alba Nieto", fecha="2026-03-04",
          concepto="Venta 120 kg del 3 de mayo", valor_total="2400000",
          abonado="400000", observaciones="NOTA INTERNA margen 12%")
    saldo(client, h, tipo="cobrar", tercero="Cliente Solo Viejo", fecha="2026-02-01",
          concepto="Factura 045", valor_total="1000000")
    saldo(client, h, tipo="pagar", tercero="Carlos Ricaute", fecha="2026-01-15",
          concepto="Compra vieja 200 kg", valor_total="3500000", abonado="500000")
    saldo(client, h, tipo="pagar", tercero="Productor Solo Viejo", fecha="2026-01-20",
          concepto="Factura 8", valor_total="900000")

    despues = resumen(client, h)

    print("\n--- 1. CAMPOS QUE CAMBIARON (deberia ser solo la cartera) ---")
    movidos = diferencias(antes, despues, INTOCABLES)
    for c, a, d in movidos:
        print(f"  CONTAMINADO {c}: {a} -> {d}")
    print(f"  intocables movidos: {len(movidos)}")
    for c in ("por_cobrar_clientes", "por_pagar_productores",
              "por_cobrar_libro_anterior", "por_pagar_libro_anterior"):
        print(f"  esperado que cambie {c}: {antes[c]} -> {despues[c]}")

    print("--- por_producto fila por fila ---")
    assert len(antes["por_producto"]) == len(despues["por_producto"])
    filas_movidas = []
    for fa, fd in zip(antes["por_producto"], despues["por_producto"]):
        for k in fa:
            if fa[k] != fd[k]:
                filas_movidas.append((fa["producto"], k, fa[k], fd[k]))
        print(f"  {fa['producto']:10} kilos={fa['kilos']} ingreso={fa['ingreso']} "
              f"costo={fa['costo']} gastos={fa['gastos']} ganancia={fa['ganancia']} (igual)")
    for f in filas_movidas:
        print(f"  CONTAMINADO por_producto {f}")

    assert movidos == [], f"el libro anterior movio cifras del negocio: {movidos}"
    assert filas_movidas == [], f"por_producto cambio: {filas_movidas}"


# ---------------------------------------------------------------------------
# 2. Invariante de la ganancia
# ---------------------------------------------------------------------------
def test_2_invariante_ganancia_por_producto(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    r1 = resumen(client, h)
    s1 = suma(r1["por_producto"], "ganancia")
    print("\n--- 2a. sin saldos ---")
    print(f"  suma(por_producto.ganancia)={s1}  ganancia_estimada={r1['ganancia_estimada']}")
    assert s1 == D(r1["ganancia_estimada"])

    saldo(client, h, tipo="cobrar", tercero="Alba Nieto", fecha="2026-03-04",
          concepto="Venta vieja", valor_total="2400000", abonado="400000")
    saldo(client, h, tipo="pagar", tercero="Sebastián Ruiz", fecha="2026-03-04",
          concepto="Compra vieja", valor_total="7777777.77", abonado="1111111.11")
    r2 = resumen(client, h)
    s2 = suma(r2["por_producto"], "ganancia")
    print("--- 2b. con saldos del libro ---")
    print(f"  suma(por_producto.ganancia)={s2}  ganancia_estimada={r2['ganancia_estimada']}")
    print(f"  suma(por_producto.costo)={suma(r2['por_producto'], 'costo')} "
          f"total_compras={r2['total_compras']}")
    assert s2 == D(r2["ganancia_estimada"])
    assert suma(r2["por_producto"], "costo") == D(r2["total_compras"])


def test_2c_invariante_con_redondeo_7kg_por_100000(client, base_datos):
    h = auth_headers(client, "admin.a")
    # 100.000 / 7 no es exacto: es el caso donde el reparto de costo puede
    # dejar centavos sueltos.
    compra(client, h, fecha="2026-07-03", productor="Redondeo",
           kilos_brutos="7", precio_kilo="14285.71")
    venta(client, h, fecha="2026-07-04", cliente="Cli R", kilos="3",
          precio_kilo="33333.33", gasto_por_kilo="333.33")
    conversion(client, h, fecha="2026-07-05", kilos="2", destino="borona")
    conversion(client, h, fecha="2026-07-06", kilos="1", destino="merma")
    saldo(client, h, tipo="pagar", tercero="Redondeo", fecha="2026-01-01",
          concepto="Deuda vieja con centavos", valor_total="333333.33", abonado="0.01")
    saldo(client, h, tipo="cobrar", tercero="Cli R", fecha="2026-01-01",
          concepto="Deuda vieja con centavos", valor_total="0.03")
    r = resumen(client, h)
    print("\n--- 2c. redondeo (7 kg, precios con centavos) ---")
    print(f"  total_compras={r['total_compras']} total_ventas={r['total_ventas']} "
          f"total_gastos={r['total_gastos']}")
    for f in r["por_producto"]:
        print(f"  {f['producto']:10} kilos={f['kilos']} costo={f['costo']} ganancia={f['ganancia']}")
    print(f"  suma ganancias={suma(r['por_producto'], 'ganancia')} "
          f"ganancia_estimada={r['ganancia_estimada']}")
    print(f"  suma costos={suma(r['por_producto'], 'costo')} total_compras={r['total_compras']}")
    assert suma(r["por_producto"], "ganancia") == D(r["ganancia_estimada"])
    assert suma(r["por_producto"], "costo") == D(r["total_compras"])


# ---------------------------------------------------------------------------
# 3. El desglose por productor tiene que sumar la tarjeta
# ---------------------------------------------------------------------------
def test_3_cuadre_columna_por_pagar_con_la_tarjeta(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    tarjeta_0 = D(resumen(client, h)["por_pagar_productores"])

    # productor con compras en el periodo + deuda vieja
    saldo(client, h, tipo="pagar", tercero="Carlos Ricaute", fecha="2026-01-15",
          concepto="Compra vieja", valor_total="3500000", abonado="500000")
    # productor SIN compras aqui, solo deuda vieja
    saldo(client, h, tipo="pagar", tercero="Productor Solo Viejo", fecha="2026-01-20",
          concepto="Factura 8", valor_total="900000")
    # variante de escritura del mismo productor (minusculas)
    saldo(client, h, tipo="pagar", tercero="sebastián ruiz", fecha="2026-02-02",
          concepto="Otra vieja", valor_total="1200000")
    # saldo ya pagado: no debe aportar ni generar fila
    s_pago = saldo(client, h, tipo="pagar", tercero="Pagado Del Todo", fecha="2026-02-03",
                   concepto="Saldada", valor_total="500000", abonado="500000")
    # saldo de tipo cobrar con nombre de productor: NO puede aparecer en por pagar
    saldo(client, h, tipo="cobrar", tercero="Carlos Ricaute", fecha="2026-02-04",
          concepto="Esto es de un cliente", valor_total="4444444")

    r = resumen(client, h)
    col = suma(r["por_productor"], "por_pagar")
    print("\n--- 3. detalle por productor vs tarjeta ---")
    for f in r["por_productor"]:
        print(f"  {f['productor']:22} kilos={f['kilos']:>8} por_pagar={f['por_pagar']:>12} "
              f"ganancia={f['ganancia_estimada']}")
    print(f"  suma columna por_pagar   = {col}")
    print(f"  tarjeta por_pagar_productores = {r['por_pagar_productores']}")
    print(f"  (del libro anterior      = {r['por_pagar_libro_anterior']})")
    print(f"  tarjeta antes del libro  = {tarjeta_0}")
    print(f"  estado del saldo pagado  = {s_pago['estado']}, saldo={s_pago['saldo']}")
    assert col == D(r["por_pagar_productores"]), "el desglose NO suma la cifra grande"
    # La ganancia del ranking se mide en test_16: descuadra por redondeo YA SIN
    # saldos anteriores, asi que no es cosa de este cambio.
    print(f"  (suma ganancia del ranking={suma(r['por_productor'], 'ganancia_estimada')} "
          f"vs ganancia_estimada={r['ganancia_estimada']})")


def test_3b_productor_con_deuda_del_sistema_fuera_del_periodo(client, base_datos):
    """Caso limite del cuadre: compra vigente FUERA del periodo consultado.

    Se mide primero SIN saldos anteriores para saber si el descuadre (si existe)
    lo trae el libro anterior o ya estaba.
    """
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    compra(client, h, fecha="2026-05-10", productor="Viejo Del Sistema",
           kilos_brutos="100", precio_kilo="10000")
    r0 = resumen(client, h)
    col0 = suma(r0["por_productor"], "por_pagar")
    print("\n--- 3b. compra vigente fuera del periodo ---")
    print(f"  SIN saldos: columna={col0} tarjeta={r0['por_pagar_productores']} "
          f"-> {'CUADRA' if col0 == D(r0['por_pagar_productores']) else 'DESCUADRA (pre-existente)'}")
    saldo(client, h, tipo="pagar", tercero="Otro Del Libro", fecha="2026-01-02",
          concepto="Vieja", valor_total="700000")
    r1 = resumen(client, h)
    col1 = suma(r1["por_productor"], "por_pagar")
    dif0 = D(r0["por_pagar_productores"]) - col0
    dif1 = D(r1["por_pagar_productores"]) - col1
    print(f"  CON saldos: columna={col1} tarjeta={r1['por_pagar_productores']} "
          f"-> {'CUADRA' if col1 == D(r1['por_pagar_productores']) else 'DESCUADRA'}")
    print(f"  descuadre sin libro = {dif0} ; con libro = {dif1} "
          f"(si son iguales, el libro no lo empeoro)")


# ---------------------------------------------------------------------------
# 4. Descomposicion de la cartera, sin dobles conteos
# ---------------------------------------------------------------------------
def test_4_cartera_es_sistema_mas_libro_sin_doble_conteo(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    base = resumen(client, h)
    sis_cobrar, sis_pagar = D(base["por_cobrar_clientes"]), D(base["por_pagar_productores"])
    print("\n--- 4. cartera ---")
    print(f"  sistema solo: por_cobrar={sis_cobrar} por_pagar={sis_pagar}")
    assert D(base["por_cobrar_libro_anterior"]) == 0
    assert D(base["por_pagar_libro_anterior"]) == 0

    s1 = saldo(client, h, tipo="cobrar", tercero="Alba Nieto", fecha="2026-03-04",
               concepto="Vieja", valor_total="2400000", abonado="400000")
    saldo(client, h, tipo="cobrar", tercero="Otro Cliente", fecha="2026-03-05",
          concepto="Vieja", valor_total="1000000")
    saldo(client, h, tipo="pagar", tercero="Carlos Ricaute", fecha="2026-03-06",
          concepto="Vieja", valor_total="3500000", abonado="500000")

    r = resumen(client, h)
    esperado_cobrar = sis_cobrar + D("2000000") + D("1000000")
    esperado_pagar = sis_pagar + D("3000000")
    print(f"  con libro: por_cobrar={r['por_cobrar_clientes']} (esperado {esperado_cobrar})")
    print(f"             libro_cobrar={r['por_cobrar_libro_anterior']} (esperado 3000000)")
    print(f"             por_pagar={r['por_pagar_productores']} (esperado {esperado_pagar})")
    print(f"             libro_pagar={r['por_pagar_libro_anterior']} (esperado 3000000)")
    assert D(r["por_cobrar_clientes"]) == esperado_cobrar
    assert D(r["por_cobrar_libro_anterior"]) == D("3000000")
    assert D(r["por_pagar_productores"]) == esperado_pagar
    assert D(r["por_pagar_libro_anterior"]) == D("3000000")
    assert (D(r["por_cobrar_clientes"]) - D(r["por_cobrar_libro_anterior"])) == sis_cobrar
    assert (D(r["por_pagar_productores"]) - D(r["por_pagar_libro_anterior"])) == sis_pagar

    # un abono posterior baja la cartera y solo la del libro
    abonar(client, h, s1["id"], fecha="2026-07-20", valor="500000")
    r2 = resumen(client, h)
    print(f"  tras abonar 500.000: por_cobrar={r2['por_cobrar_clientes']} "
          f"libro={r2['por_cobrar_libro_anterior']} ganancia={r2['ganancia_estimada']}")
    assert D(r2["por_cobrar_libro_anterior"]) == D("2500000")
    assert D(r2["por_cobrar_clientes"]) == sis_cobrar + D("2500000")
    assert D(r2["ganancia_estimada"]) == D(base["ganancia_estimada"])
    # abono mayor al saldo se rechaza
    r_mal = client.post(f"{API}/saldos-anteriores/{s1['id']}/abonos",
                        json={"fecha": "2026-07-21", "valor": "99999999"}, headers=h)
    print(f"  abono excesivo -> {r_mal.status_code} {r_mal.json().get('error', {}).get('detail')}")
    assert r_mal.status_code == 422


# ---------------------------------------------------------------------------
# 5. Anulado y borrado no cuentan
# ---------------------------------------------------------------------------
def test_5_anulado_y_borrado_no_suman(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    base = resumen(client, h)

    s_anular = saldo(client, h, tipo="cobrar", tercero="Alba Nieto", fecha="2026-03-04",
                     concepto="Se anula", valor_total="5000000")
    s_borrar = saldo(client, h, tipo="pagar", tercero="Carlos Ricaute", fecha="2026-03-05",
                     concepto="Se borra", valor_total="6000000")
    con = resumen(client, h)
    print("\n--- 5. anulado y borrado ---")
    print(f"  con los dos vigentes: cobrar={con['por_cobrar_clientes']} "
          f"pagar={con['por_pagar_productores']}")

    ra = client.post(f"{API}/saldos-anteriores/{s_anular['id']}/anular", headers=h)
    assert ra.status_code == 200, ra.text
    print(f"  anulado -> estado={ra.json()['estado']}")
    rd = client.delete(f"{API}/saldos-anteriores/{s_borrar['id']}", headers=h)
    assert rd.status_code == 204, rd.text
    print("  borrado -> 204")

    fin = resumen(client, h)
    print(f"  despues: cobrar={fin['por_cobrar_clientes']} (base {base['por_cobrar_clientes']})")
    print(f"           pagar={fin['por_pagar_productores']} (base {base['por_pagar_productores']})")
    print(f"           libro_cobrar={fin['por_cobrar_libro_anterior']} "
          f"libro_pagar={fin['por_pagar_libro_anterior']}")
    print(f"  columna por productor={suma(fin['por_productor'], 'por_pagar')}")
    assert D(fin["por_cobrar_clientes"]) == D(base["por_cobrar_clientes"])
    assert D(fin["por_pagar_productores"]) == D(base["por_pagar_productores"])
    assert D(fin["por_cobrar_libro_anterior"]) == 0
    assert D(fin["por_pagar_libro_anterior"]) == 0
    assert suma(fin["por_productor"], "por_pagar") == D(fin["por_pagar_productores"])
    assert diferencias(base, fin, INTOCABLES) == []

    # y no salen en el estado de cuenta del cliente
    ec = client.get(f"{API}/estado-cuenta", params={"cliente": "Alba Nieto"}, headers=h)
    assert ec.status_code == 200, ec.text
    d = ec.json()
    print(f"  estado de cuenta de Alba: saldos_anteriores={d['saldos_anteriores']} "
          f"libro_saldo={d['libro_anterior_saldo']}")
    assert d["saldos_anteriores"] == []
    assert D(d["libro_anterior_saldo"]) == 0
    # no se puede anular ni eliminar con abonos
    s3 = saldo(client, h, tipo="cobrar", tercero="Alba Nieto", fecha="2026-03-06",
               concepto="Con abono", valor_total="1000000", abonado="100000")
    r1 = client.post(f"{API}/saldos-anteriores/{s3['id']}/anular", headers=h)
    r2 = client.delete(f"{API}/saldos-anteriores/{s3['id']}", headers=h)
    print(f"  anular con abonos -> {r1.status_code}; eliminar con abonos -> {r2.status_code}")
    assert r1.status_code == 422 and r2.status_code == 422
    print(f"  historial del abono inicial: {s3['abonos']}")
    assert len(s3["abonos"]) == 1 and D(s3["abonos"][0]["valor"]) == D("100000")


# ---------------------------------------------------------------------------
# 6. El estado de cuenta cuadra
# ---------------------------------------------------------------------------
def estado(client, h, cliente, **params):
    r = client.get(f"{API}/estado-cuenta", params={"cliente": cliente, **params}, headers=h)
    return r


def _verificar_cuadre(nombre, d):
    izq = (D(d["total_facturado"]) - D(d["total_abonado"])) + D(d["libro_anterior_saldo"])
    print(f"  {nombre}: facturado={d['total_facturado']} abonado={d['total_abonado']} "
          f"libro_total={d['libro_anterior_total']} libro_abonado={d['libro_anterior_abonado']} "
          f"libro_saldo={d['libro_anterior_saldo']} SALDO={d['saldo']}")
    print(f"     ({d['total_facturado']} - {d['total_abonado']}) + "
          f"{d['libro_anterior_saldo']} = {izq}  vs saldo {d['saldo']}")
    assert izq == D(d["saldo"]), f"{nombre} NO cuadra"
    assert D(d["libro_anterior_total"]) - D(d["libro_anterior_abonado"]) == D(
        d["libro_anterior_saldo"]
    )
    assert suma(d["saldos_anteriores"], "valor_total") == D(d["libro_anterior_total"])
    assert suma(d["saldos_anteriores"], "abonado") == D(d["libro_anterior_abonado"])
    assert suma(d["saldos_anteriores"], "saldo") == D(d["libro_anterior_saldo"])


def test_6_estado_de_cuenta_cuadra_en_los_tres_casos(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    print("\n--- 6. estado de cuenta ---")

    # a) solo ventas del sistema
    d_solo_sistema = estado(client, h, "Yojan Pérez").json()
    _verificar_cuadre("solo sistema", d_solo_sistema)
    assert d_solo_sistema["saldos_anteriores"] == []

    # b) cliente que NO existe aun -> 404
    r404 = estado(client, h, "Fantasma")
    print(f"  cliente inexistente -> {r404.status_code} {r404.json().get('error', {}).get('detail')}")
    assert r404.status_code == 404

    # c) solo deuda vieja (antes daba 404)
    saldo(client, h, tipo="cobrar", tercero="Solo Libro", fecha="2026-02-10",
          concepto="Factura 045", valor_total="1500000", abonado="500000",
          observaciones="OBSERVACION INTERNA no debe salir")
    r = estado(client, h, "solo libro")
    print(f"  cliente solo con deuda vieja -> {r.status_code}")
    assert r.status_code == 200, r.text
    d = r.json()
    print(f"     nombre devuelto={d['cliente']!r} compras={d['compras']} ventas={d['ventas']}")
    _verificar_cuadre("solo libro", d)
    assert D(d["saldo"]) == D("1000000")
    assert D(d["total_facturado"]) == 0 and D(d["total_abonado"]) == 0
    assert "observaciones" not in d["saldos_anteriores"][0]

    # d) las dos cosas
    saldo(client, h, tipo="cobrar", tercero="Yojan Pérez", fecha="2026-02-11",
          concepto="Venta 120 kg del 3 de mayo", valor_total="2400000", abonado="400000")
    d2 = estado(client, h, "Yojan Pérez").json()
    _verificar_cuadre("sistema + libro", d2)
    assert D(d2["total_facturado"]) == D(d_solo_sistema["total_facturado"])
    assert D(d2["saldo"]) == D(d_solo_sistema["saldo"]) + D("2000000")

    # e) un saldo tipo 'pagar' con el mismo nombre NO puede entrar en el estado
    #    de cuenta del cliente
    saldo(client, h, tipo="pagar", tercero="Yojan Pérez", fecha="2026-02-12",
          concepto="Esto es de un productor", valor_total="9999999")
    d3 = estado(client, h, "Yojan Pérez").json()
    print(f"  tras cargar un 'pagar' homonimo: saldo={d3['saldo']} "
          f"libro={d3['libro_anterior_saldo']} filas={len(d3['saldos_anteriores'])}")
    assert D(d3["saldo"]) == D(d2["saldo"]), "un saldo por PAGAR se colo en el cobro al cliente"

    # f) con rango de fechas: el saldo viejo queda fuera
    d4 = estado(client, h, "Yojan Pérez", desde="2026-07-01", hasta="2026-07-31").json()
    print(f"  con rango julio: libro_saldo={d4['libro_anterior_saldo']} saldo={d4['saldo']}")
    _verificar_cuadre("sistema + libro (rango julio)", d4)
    assert D(d4["libro_anterior_saldo"]) == 0

    r_fuera = estado(client, h, "Solo Libro", desde="2026-07-01", hasta="2026-07-31")
    print(f"  solo-libro fuera de rango -> {r_fuera.status_code} "
          f"{r_fuera.json().get('error', {}).get('detail')}")
    assert r_fuera.status_code == 404


# ---------------------------------------------------------------------------
# 7. El inventario no se mueve
# ---------------------------------------------------------------------------
def test_7_inventario_intacto_y_no_deja_vender_mas(client, base_datos):
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Uno", kilos_brutos="100",
           precio_kilo="10000")
    venta(client, h, fecha="2026-07-03", cliente="Cli", kilos="60", precio_kilo="12000")
    antes = resumen(client, h)
    print("\n--- 7. inventario ---")
    print(f"  disponible antes={antes['kilos_disponibles']} "
          f"borona={antes['borona_disponible']}")

    saldo(client, h, tipo="pagar", tercero="Uno", fecha="2026-01-01",
          concepto="1000 kg del libro viejo", valor_total="10000000")
    saldo(client, h, tipo="cobrar", tercero="Cli", fecha="2026-01-01",
          concepto="900 kg del libro viejo", valor_total="9000000")
    despues = resumen(client, h)
    print(f"  disponible despues={despues['kilos_disponibles']} "
          f"borona={despues['borona_disponible']}")
    assert D(despues["kilos_disponibles"]) == D(antes["kilos_disponibles"]) == D("40")
    assert D(despues["borona_disponible"]) == D(antes["borona_disponible"])

    r = client.post(f"{API}/ventas", json={"fecha": "2026-07-04", "cliente": "Cli",
                                           "kilos": "41", "precio_kilo": "12000"}, headers=h)
    print(f"  vender 41 kg con 40 disponibles -> {r.status_code} {r.json().get('error', {}).get('detail')}")
    assert r.status_code == 422
    rb = client.post(f"{API}/conversiones", json={"fecha": "2026-07-04", "kilos": "41",
                                                  "destino": "borona"}, headers=h)
    print(f"  convertir 41 kg -> {rb.status_code} {rb.json().get('error', {}).get('detail')}")
    assert rb.status_code == 422


# ---------------------------------------------------------------------------
# 8. Tenencia: los saldos de una empresa no se ven en la otra
# ---------------------------------------------------------------------------
def test_8_los_saldos_no_cruzan_de_empresa(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    saldo(client, ha, tipo="cobrar", tercero="Cliente A", fecha="2026-01-01",
          concepto="Vieja de A", valor_total="5000000")
    ra, rb = resumen(client, ha), resumen(client, hb)
    print("\n--- 8. tenencia ---")
    print(f"  empresa A: libro_cobrar={ra['por_cobrar_libro_anterior']} "
          f"cartera={ra['por_cobrar_clientes']}")
    print(f"  empresa B: libro_cobrar={rb['por_cobrar_libro_anterior']} "
          f"cartera={rb['por_cobrar_clientes']}")
    assert D(ra["por_cobrar_libro_anterior"]) == D("5000000")
    assert D(rb["por_cobrar_libro_anterior"]) == 0
    assert D(rb["por_cobrar_clientes"]) == 0
    lb = client.get(f"{API}/saldos-anteriores", headers=hb)
    print(f"  listado de B: total={lb.json()['total']}")
    assert lb.json()["total"] == 0
    ec = client.get(f"{API}/estado-cuenta", params={"cliente": "Cliente A"}, headers=hb)
    print(f"  estado de cuenta de 'Cliente A' pedido por B -> {ec.status_code}")
    assert ec.status_code == 404


# ---------------------------------------------------------------------------
# 9. El PDF: seccion nueva, cuadre y confidencialidad
# ---------------------------------------------------------------------------
def _texto_pdf(contenido):
    import io

    from pypdf import PdfReader

    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)


def test_9_pdf_con_y_sin_saldos_anteriores(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    r0 = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": "Yojan Pérez"}, headers=h)
    assert r0.status_code == 200, r0.text
    t0 = _texto_pdf(r0.content)
    print("\n--- 9. PDF ---")
    print(f"  sin saldos: 'Saldos de la cuenta anterior' presente? "
          f"{'Saldos de la cuenta anterior' in t0}")
    assert "Saldos de la cuenta anterior" not in t0

    saldo(client, h, tipo="cobrar", tercero="Yojan Pérez", fecha="2026-02-11",
          concepto="Venta 120 kg del 3 de mayo <b>ojo</b>", valor_total="2400000",
          abonado="400000", observaciones="SECRETO INTERNO margen 32%")
    d = estado(client, h, "Yojan Pérez").json()
    r1 = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": "Yojan Pérez"}, headers=h)
    assert r1.status_code == 200, r1.text
    t1 = _texto_pdf(r1.content)
    print(f"  con saldos: seccion presente? {'Saldos de la cuenta anterior' in t1}")
    print(f"  concepto impreso? {'Venta 120 kg del 3 de mayo' in t1}")
    print(f"  nota presente? {'sistema que se usaba antes' in t1}")
    print(f"  'Saldo de la cuenta anterior' en resumen? {'Saldo de la cuenta anterior' in t1}")
    print(f"  observacion interna filtrada? {'SECRETO' in t1}")
    print(f"  nombres de productores filtrados? "
          f"{[n for n in ('Sebastián', 'Ruiz', 'Ricaute') if n in t1]}")
    assert "Saldos de la cuenta anterior" in t1
    assert "Venta 120 kg del 3 de mayo" in t1
    assert "SECRETO" not in t1 and "32%" not in t1
    assert not [n for n in ("Ricaute", "Ruiz") if n in t1]

    # el saldo destacado del PDF es el total (sistema + libro)
    from app.utils.export import pesos

    print(f"  saldo del JSON={d['saldo']} -> impreso {pesos(D(d['saldo']))}: "
          f"{pesos(D(d['saldo'])) in t1}")
    sis = D(d["total_facturado"]) - D(d["total_abonado"])
    print(f"  saldo del sistema {pesos(sis)} en la fila TOTALES: {pesos(sis) in t1}")
    assert pesos(D(d["saldo"])) in t1
    assert pesos(sis) in t1

    # cliente que solo trae deuda vieja: el PDF debe salir igual
    saldo(client, h, tipo="cobrar", tercero="Solo Libro", fecha="2026-02-10",
          concepto="Factura 045", valor_total="1500000", abonado="500000")
    r2 = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": "Solo Libro"}, headers=h)
    print(f"  PDF de cliente solo-libro -> {r2.status_code}, "
          f"{len(r2.content) if r2.status_code == 200 else '-'} bytes")
    assert r2.status_code == 200, r2.text
    t2 = _texto_pdf(r2.content)
    print(f"     'Sin compras registradas'? {'Sin compras registradas' in t2}")
    assert "Sin compras registradas" in t2 and "Saldos de la cuenta anterior" in t2


# ---------------------------------------------------------------------------
# 10. Adversarial: editar el valor por debajo de lo abonado
# ---------------------------------------------------------------------------
def test_10_editar_valor_por_debajo_de_lo_abonado(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    base = resumen(client, h)
    s = saldo(client, h, tipo="cobrar", tercero="Alba Nieto", fecha="2026-03-04",
              concepto="Vieja", valor_total="5000000", abonado="5000000")
    r = client.put(f"{API}/saldos-anteriores/{s['id']}",
                   json={"valor_total": "1000000"}, headers=h)
    print("\n--- 10. editar valor_total por debajo de lo abonado ---")
    print(f"  PUT -> {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        fin = resumen(client, h)
        print(f"  saldo resultante = {d['saldo']} (estado {d['estado']})")
        print(f"  por_cobrar_clientes: {base['por_cobrar_clientes']} -> "
              f"{fin['por_cobrar_clientes']}")
        print(f"  por_cobrar_libro_anterior = {fin['por_cobrar_libro_anterior']}")
        # misma prueba contra una VENTA del sistema, para ver si es un problema
        # nuevo o el comportamiento que ya tenian compras y ventas
        v = venta(client, h, fecha="2026-07-19", cliente="Alba Nieto", kilos="10",
                  precio_kilo="20000", pagada_de_contado=True)
        rv = client.put(f"{API}/ventas/{v['id']}", json={"precio_kilo": "1000"}, headers=h)
        print(f"  misma maniobra en una VENTA -> {rv.status_code} "
              f"saldo={rv.json().get('saldo')}")
    # 11. Bono: editar el TIPO de un saldo con abonos (cobrar -> pagar)
    s2 = saldo(client, h, tipo="cobrar", tercero="Alba Nieto", fecha="2026-03-05",
               concepto="Cambia de lado", valor_total="1000000", abonado="200000")
    r2 = client.put(f"{API}/saldos-anteriores/{s2['id']}", json={"tipo": "pagar"}, headers=h)
    print(f"  cambiar tipo cobrar->pagar -> {r2.status_code} tercero={r2.json().get('tercero')!r}")
    fin2 = resumen(client, h)
    print(f"  cartera final: cobrar={fin2['por_cobrar_clientes']} "
          f"libro_cobrar={fin2['por_cobrar_libro_anterior']} "
          f"pagar={fin2['por_pagar_productores']} "
          f"libro_pagar={fin2['por_pagar_libro_anterior']}")
    print(f"  columna por productor={suma(fin2['por_productor'], 'por_pagar')} "
          f"vs tarjeta={fin2['por_pagar_productores']}")
    print(f"  intocables movidos por todo esto: "
          f"{diferencias(base, fin2, [c for c in INTOCABLES if c != 'total_ventas'])}")


# ---------------------------------------------------------------------------
# 11. Centavos exactos (sin artefactos de float en la suma)
# ---------------------------------------------------------------------------
def test_11_centavos_exactos_en_la_cartera(client, base_datos):
    h = auth_headers(client, "admin.a")
    saldo(client, h, tipo="cobrar", tercero="Cli Centavos", fecha="2026-01-01",
          concepto="Uno", valor_total="0.01")
    saldo(client, h, tipo="cobrar", tercero="Cli Centavos", fecha="2026-01-02",
          concepto="Dos", valor_total="0.02")
    saldo(client, h, tipo="pagar", tercero="Prod Centavos", fecha="2026-01-03",
          concepto="Tres", valor_total="333333.33", abonado="0.01")
    saldo(client, h, tipo="pagar", tercero="Prod Centavos", fecha="2026-01-04",
          concepto="Cuatro", valor_total="7777777.77", abonado="1111111.11")
    r = resumen(client, h)
    print("\n--- 11. centavos ---")
    print(f"  por_cobrar_libro_anterior = {r['por_cobrar_libro_anterior']!r} (esperado 0.03)")
    print(f"  por_pagar_libro_anterior  = {r['por_pagar_libro_anterior']!r} (esperado 6999999.98)")
    print(f"  por_cobrar_clientes       = {r['por_cobrar_clientes']!r}")
    print(f"  columna por productor     = {suma(r['por_productor'], 'por_pagar')!r}")
    for f in r["por_productor"]:
        print(f"    {f['productor']} -> {f['por_pagar']!r}")
    assert D(r["por_cobrar_libro_anterior"]) == D("0.03")
    assert D(r["por_pagar_libro_anterior"]) == D("6999999.98")
    assert str(r["por_cobrar_libro_anterior"]) in ("0.03", "0.0300")
    assert suma(r["por_productor"], "por_pagar") == D(r["por_pagar_productores"])
    ec = estado(client, h, "Cli Centavos").json()
    _verificar_cuadre("centavos", ec)
    assert D(ec["saldo"]) == D("0.03")


# ---------------------------------------------------------------------------
# 12. Ningun agujero de empresa: B no puede tocar el saldo de A por id
# ---------------------------------------------------------------------------
def test_12_empresa_b_no_puede_tocar_el_saldo_de_a(client, base_datos):
    ha, hb = auth_headers(client, "admin.a"), auth_headers(client, "admin.b")
    s = saldo(client, ha, tipo="cobrar", tercero="Cliente A", fecha="2026-01-01",
              concepto="De A", valor_total="5000000")
    print("\n--- 12. tenencia en escritura ---")
    for nombre, r in [
        ("PUT", client.put(f"{API}/saldos-anteriores/{s['id']}",
                           json={"valor_total": "1"}, headers=hb)),
        ("abono", client.post(f"{API}/saldos-anteriores/{s['id']}/abonos",
                              json={"fecha": "2026-07-01", "valor": "1"}, headers=hb)),
        ("anular", client.post(f"{API}/saldos-anteriores/{s['id']}/anular", headers=hb)),
        ("DELETE", client.delete(f"{API}/saldos-anteriores/{s['id']}", headers=hb)),
    ]:
        print(f"  {nombre} desde empresa B -> {r.status_code}")
        assert r.status_code == 404, f"{nombre} dejo entrar a la otra empresa"
    ra = resumen(client, ha)
    print(f"  A sigue igual: libro_cobrar={ra['por_cobrar_libro_anterior']}")
    assert D(ra["por_cobrar_libro_anterior"]) == D("5000000")


# ---------------------------------------------------------------------------
# 13. Linea base de los dos descuadres sospechosos, SIN libro anterior
# ---------------------------------------------------------------------------
def test_13_linea_base_sin_libro_anterior(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    r = resumen(client, h)
    col = suma(r["por_productor"], "por_pagar")
    gan = suma(r["por_productor"], "ganancia_estimada")
    print("\n--- 13. linea base SIN NINGUN saldo anterior ---")
    print(f"  columna por_pagar={col} tarjeta={r['por_pagar_productores']} "
          f"-> {'CUADRA' if col == D(r['por_pagar_productores']) else 'DESCUADRA'}")
    print(f"  suma ganancia del ranking={gan} ganancia_estimada={r['ganancia_estimada']} "
          f"-> diferencia {D(r['ganancia_estimada']) - gan}")
    print(f"  valor_realizado_kilo={r['valor_realizado_kilo']} x kilos_comprados="
          f"{r['kilos_comprados']} = {D(r['valor_realizado_kilo']) * D(r['kilos_comprados'])} "
          f"vs total_ventas-total_gastos="
          f"{D(r['total_ventas']) - D(r['total_gastos'])}")
    assert col == D(r["por_pagar_productores"])


# ---------------------------------------------------------------------------
# 14. Canonizacion del tercero y filas duplicadas
# ---------------------------------------------------------------------------
def test_14_canonizacion_del_tercero(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    print("\n--- 14. canonizacion ---")
    a = saldo(client, h, tipo="pagar", tercero="carlos   ricaute", fecha="2026-01-01",
              concepto="Vieja", valor_total="1000000")
    b = saldo(client, h, tipo="cobrar", tercero="alba    nieto", fecha="2026-01-01",
              concepto="Vieja", valor_total="1000000")
    c = saldo(client, h, tipo="cobrar", tercero="carlos ricaute", fecha="2026-01-01",
              concepto="Este es cliente, no productor", valor_total="1000000")
    print(f"  pagar 'carlos   ricaute'  -> {a['tercero']!r} (productor de compras)")
    print(f"  cobrar 'alba    nieto'    -> {b['tercero']!r} (cliente de ventas)")
    print(f"  cobrar 'carlos ricaute'   -> {c['tercero']!r} (NO debe adoptar el productor)")
    assert a["tercero"] == "Carlos Ricaute"
    assert b["tercero"] == "Alba Nieto"
    assert c["tercero"] == "carlos ricaute"

    # la deuda de Alba no queda partida en dos en su estado de cuenta
    d = estado(client, h, "ALBA NIETO").json()
    print(f"  estado de cuenta 'ALBA NIETO': cliente={d['cliente']!r} "
          f"filas_libro={len(d['saldos_anteriores'])} libro_saldo={d['libro_anterior_saldo']}")
    _verificar_cuadre("alba", d)

    # una sola fila por productor en el detalle
    r = resumen(client, h)
    nombres = [f["productor"] for f in r["por_productor"]]
    print(f"  filas del detalle por productor: {nombres}")
    assert len(nombres) == len(set(n.lower() for n in nombres)), "fila duplicada"
    assert suma(r["por_productor"], "por_pagar") == D(r["por_pagar_productores"])


def test_15_productor_con_deuda_vieja_y_compra_fuera_del_periodo(client, base_datos):
    """El caso exacto del cliente que migra: fechas viejas fuera del periodo."""
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    compra(client, h, fecha="2026-05-10", productor="Mixto", kilos_brutos="100",
           precio_kilo="10000")  # 1.000.000 pendiente, FUERA del periodo
    saldo(client, h, tipo="pagar", tercero="Mixto", fecha="2026-01-02",
          concepto="Vieja", valor_total="700000")
    r = resumen(client, h)
    print("\n--- 15. productor con compra fuera del periodo + deuda del libro ---")
    for f in r["por_productor"]:
        print(f"  {f['productor']:22} kilos={f['kilos']:>8} por_pagar={f['por_pagar']}")
    col = suma(r["por_productor"], "por_pagar")
    print(f"  columna={col} tarjeta={r['por_pagar_productores']} "
          f"libro={r['por_pagar_libro_anterior']}")
    print(f"  falta en la columna = {D(r['por_pagar_productores']) - col} "
          f"(el pendiente de la compra de mayo)")
    filas_mixto = [f for f in r["por_productor"] if f["productor"] == "Mixto"]
    print(f"  filas de 'Mixto': {len(filas_mixto)} -> {[f['por_pagar'] for f in filas_mixto]}")
    print("  a 'Mixto' se le deben 1.700.000 (1.000.000 de la compra de mayo + "
          "700.000 del libro) y la fila nueva solo muestra el pedazo del libro")
    assert len(filas_mixto) == 1, "fila duplicada"
    # HALLAZGO: la fila que agrega el codigo nuevo para 'los que solo tienen
    # deuda del libro' tambien se emite cuando el productor SI tiene compras
    # vigentes, si son de otro periodo. La fila entonces MIENTE por defecto.
    assert D(filas_mixto[0]["por_pagar"]) == D("1700000"), (
        f"la fila de Mixto muestra {filas_mixto[0]['por_pagar']} de una deuda real de 1700000"
    )


# ---------------------------------------------------------------------------
# 16. PRE-EXISTENTE (no lo trae el libro anterior): la columna ganancia del
#     ranking por productor no sumaba exacto la ganancia del periodo por el
#     redondeo de valor_realizado_kilo. Se mide SIN ningun saldo anterior.
#     CORREGIDO: el reparto ya no quantiza el valor por kilo antes de
#     multiplicarlo y la diferencia de centavos se la lleva la ultima fila, asi
#     que la columna suma exacto y el xfail estricto se retiro.
# ---------------------------------------------------------------------------
def test_16_preexistente_ganancia_del_ranking_no_suma(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    r = resumen(client, h)
    gan = suma(r["por_productor"], "ganancia_estimada")
    print("--- 16. PRE-EXISTENTE: ganancia del ranking, sin saldos del libro ---")
    print(f"  suma={gan} ganancia_estimada={r['ganancia_estimada']} "
          f"diferencia={D(r['ganancia_estimada']) - gan}")
    assert gan == D(r["ganancia_estimada"])
