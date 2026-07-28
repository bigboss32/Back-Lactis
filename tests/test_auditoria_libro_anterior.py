"""AUDITORIA ADVERSARIAL: el libro anterior NO debe contaminar el negocio de aqui."""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/reventa"

CAMPOS_INTOCABLES = [
    "kilos_comprados", "total_compras", "kilos_vendidos", "total_ventas",
    "precio_promedio_compra", "precio_promedio_venta", "total_gastos",
    "ganancia_estimada", "margen_por_kilo", "valor_realizado_kilo",
    "kilos_borona_vendidos", "total_ventas_borona", "kilos_a_borona",
    "kilos_merma", "kilos_pendientes", "kilos_disponibles", "borona_disponible",
]

PER = {"desde": "2026-07-01", "hasta": "2026-07-31"}


def D(x):
    return Decimal(str(x))


def resumen(client, h):
    r = client.get(f"{API}/resumen", params=PER, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def sembrar(client, h):
    """Negocio real: 2 productores, compras, ventas, conversion, gastos."""
    client.post(f"{API}/compras", json={
        "fecha": "2026-07-02", "productor": "Sebastián Ruiz",
        "kilos_brutos": "800", "borona_kilos": "50", "precio_kilo": "18000"}, headers=h)
    client.post(f"{API}/compras", json={
        "fecha": "2026-07-05", "productor": "Carlos Ricaute",
        "kilos_brutos": "300", "precio_kilo": "17500"}, headers=h)
    client.post(f"{API}/ventas", json={
        "fecha": "2026-07-10", "cliente": "Alba", "kilos": "400",
        "precio_kilo": "19500", "pagada_de_contado": True, "gasto_monto": "120000"}, headers=h)
    client.post(f"{API}/ventas", json={
        "fecha": "2026-07-12", "cliente": "Yojan", "kilos": "250",
        "precio_kilo": "20000"}, headers=h)
    client.post(f"{API}/conversiones", json={
        "fecha": "2026-07-15", "kilos": "30", "destino": "borona",
        "precio_kilo": "9000"}, headers=h)
    client.post(f"{API}/conversiones", json={
        "fecha": "2026-07-16", "kilos": "10", "destino": "merma"}, headers=h)


def crear_saldo(client, h, **kw):
    r = client.post(f"{API}/saldos-anteriores", json=kw, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def sum_(items, campo):
    return sum((D(i[campo]) for i in items), Decimal("0"))


# ---------------------------------------------------------------- 1, 2, 3, 4
def test_auditoria_resumen(client, base_datos, capsys):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    antes = resumen(client, h)

    crear_saldo(client, h, tipo="cobrar", tercero="Alba", fecha="2025-11-03",
                concepto="Venta 120 kg del 3 de mayo", valor_total="3000000",
                abonado="1000000")
    crear_saldo(client, h, tipo="cobrar", tercero="Marta Solo Vieja",
                fecha="2025-12-01", concepto="Factura 045", valor_total="500000")
    crear_saldo(client, h, tipo="pagar", tercero="Sebastián Ruiz",
                fecha="2025-10-20", concepto="Compra vieja", valor_total="2000000",
                abonado="500000")
    crear_saldo(client, h, tipo="pagar", tercero="Pedro Solo Libro",
                fecha="2025-09-09", concepto="Factura vieja", valor_total="800000")

    despues = resumen(client, h)

    print("\n===== 1. CAMPOS QUE CAMBIARON AL CARGAR EL LIBRO ANTERIOR =====")
    cambiaron = []
    for k in CAMPOS_INTOCABLES:
        if D(antes[k]) != D(despues[k]):
            cambiaron.append(k)
            print(f"  CAMBIO {k}: {antes[k]} -> {despues[k]}")
    if not cambiaron:
        print("  (ninguno)")
    print("  por_producto identico:", antes["por_producto"] == despues["por_producto"])
    for a, d in zip(antes["por_producto"], despues["por_producto"]):
        if a != d:
            print("  CAMBIO por_producto:", a, "->", d)

    print("\n===== 2. INVARIANTE suma(por_producto.ganancia) == ganancia_estimada =====")
    for etiqueta, res in (("antes", antes), ("despues", despues)):
        s = sum_(res["por_producto"], "ganancia")
        g = D(res["ganancia_estimada"])
        print(f"  {etiqueta}: suma={s}  ganancia_estimada={g}  dif={s - g}")

    print("\n===== 3. suma(por_productor.por_pagar) == por_pagar_productores =====")
    for etiqueta, res in (("antes", antes), ("despues", despues)):
        s = sum_(res["por_productor"], "por_pagar")
        c = D(res["por_pagar_productores"])
        print(f"  {etiqueta}: columna={s}  tarjeta={c}  dif={s - c}")
    print("  filas por_productor despues:")
    for f in despues["por_productor"]:
        print(f"    {f['productor']!r:28} compras={f['compras']} kilos={f['kilos']}"
              f" por_pagar={f['por_pagar']} ganancia={f['ganancia_estimada']}")
    print("  suma(por_productor.ganancia_estimada) =",
          sum_(despues["por_productor"], "ganancia_estimada"),
          " vs ganancia_estimada =", despues["ganancia_estimada"])

    print("\n===== 4. CARTERA: sistema + libro, sin doble conteo =====")
    print(f"  por_cobrar antes(sistema)={antes['por_cobrar_clientes']}"
          f"  despues={despues['por_cobrar_clientes']}"
          f"  libro={despues['por_cobrar_libro_anterior']}")
    print(f"  por_pagar  antes(sistema)={antes['por_pagar_productores']}"
          f"  despues={despues['por_pagar_productores']}"
          f"  libro={despues['por_pagar_libro_anterior']}")
    ok_c = D(despues["por_cobrar_clientes"]) == D(antes["por_cobrar_clientes"]) + D(despues["por_cobrar_libro_anterior"])
    ok_p = D(despues["por_pagar_productores"]) == D(antes["por_pagar_productores"]) + D(despues["por_pagar_libro_anterior"])
    print("  cobrar cuadra:", ok_c, " pagar cuadra:", ok_p)

    assert not cambiaron, cambiaron
    assert antes["por_producto"] == despues["por_producto"]
    assert sum_(despues["por_producto"], "ganancia") == D(despues["ganancia_estimada"])
    assert sum_(despues["por_productor"], "por_pagar") == D(despues["por_pagar_productores"])
    assert ok_c and ok_p


# --------------------------------------------------------------- 2 redondeo
def test_redondeo_7kg(client, base_datos):
    h = auth_headers(client, "admin.a")
    r = client.post(f"{API}/compras", json={
        "fecha": "2026-07-02", "productor": "Uno", "kilos_brutos": "7",
        "precio_kilo": "14285.7142857"}, headers=h)
    assert r.status_code == 201, r.text
    client.post(f"{API}/ventas", json={
        "fecha": "2026-07-10", "cliente": "Alba", "kilos": "7",
        "precio_kilo": "19999.99", "gasto_monto": "33333.33"}, headers=h)
    crear_saldo(client, h, tipo="cobrar", tercero="Alba", fecha="2025-01-01",
                concepto="xx", valor_total="333333.33", abonado="111111.11")
    crear_saldo(client, h, tipo="pagar", tercero="Uno", fecha="2025-01-01",
                concepto="yy", valor_total="777777.77")
    res = resumen(client, h)
    print("\n===== 2b. REDONDEO (7 kg) =====")
    print("  ganancia_estimada =", res["ganancia_estimada"])
    for f in res["por_producto"]:
        print(f"    {f['producto']:8} ganancia={f['ganancia']}")
    print("  suma por_producto.ganancia =", sum_(res["por_producto"], "ganancia"))
    print("  suma por_productor.por_pagar =", sum_(res["por_productor"], "por_pagar"),
          " tarjeta =", res["por_pagar_productores"])
    assert sum_(res["por_producto"], "ganancia") == D(res["ganancia_estimada"])
    assert sum_(res["por_productor"], "por_pagar") == D(res["por_pagar_productores"])


# ---------------------------------------------------------- 5 anulado/borrado
def test_anulado_y_borrado_no_cuentan(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    base = resumen(client, h)

    s1 = crear_saldo(client, h, tipo="cobrar", tercero="Fantasma", fecha="2025-01-01",
                     concepto="anular", valor_total="9000000")
    s2 = crear_saldo(client, h, tipo="pagar", tercero="Fantasma2", fecha="2025-01-01",
                     concepto="borrar", valor_total="7000000")
    r = client.post(f"{API}/saldos-anteriores/{s1['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    r = client.delete(f"{API}/saldos-anteriores/{s2['id']}", headers=h)
    assert r.status_code == 204, r.text

    fin = resumen(client, h)
    print("\n===== 5. ANULADO Y BORRADO NO CUENTAN =====")
    for k in CAMPOS_INTOCABLES + ["por_cobrar_clientes", "por_pagar_productores",
                                  "por_cobrar_libro_anterior", "por_pagar_libro_anterior"]:
        if D(base[k]) != D(fin[k]):
            print(f"  CAMBIO {k}: {base[k]} -> {fin[k]}")
    print("  por_cobrar_libro_anterior =", fin["por_cobrar_libro_anterior"])
    print("  por_pagar_libro_anterior  =", fin["por_pagar_libro_anterior"])
    print("  filas por_productor:", [(f["productor"], f["por_pagar"]) for f in fin["por_productor"]])
    assert D(fin["por_cobrar_libro_anterior"]) == 0
    assert D(fin["por_pagar_libro_anterior"]) == 0
    assert sum_(fin["por_productor"], "por_pagar") == D(fin["por_pagar_productores"])
    assert "Fantasma2" not in [f["productor"] for f in fin["por_productor"]]


# -------------------------------------------------------------- 6 estado cta
def test_estado_cuenta_cuadra(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    crear_saldo(client, h, tipo="cobrar", tercero="Yojan", fecha="2025-11-03",
                concepto="Venta 120 kg del 3 de mayo", valor_total="3000000",
                abonado="1000000")
    crear_saldo(client, h, tipo="cobrar", tercero="Marta Solo Vieja", fecha="2025-12-01",
                concepto="Factura 045", valor_total="500000", abonado="200000")

    print("\n===== 6. ESTADO DE CUENTA =====")
    for caso, cliente in (("solo ventas del sistema", "Alba"),
                          ("ventas + libro", "Yojan"),
                          ("solo deuda vieja", "Marta Solo Vieja")):
        r = client.get(f"{API}/estado-cuenta", params={"cliente": cliente}, headers=h)
        assert r.status_code == 200, (cliente, r.status_code, r.text)
        d = r.json()
        izq = D(d["total_facturado"]) - D(d["total_abonado"]) + D(d["libro_anterior_saldo"])
        print(f"  [{caso}] {cliente}: fact={d['total_facturado']} abon={d['total_abonado']}"
              f" libro_saldo={d['libro_anterior_saldo']} saldo={d['saldo']} -> cuadra={izq == D(d['saldo'])}")
        print(f"      libro_total={d['libro_anterior_total']} libro_abonado={d['libro_anterior_abonado']}"
              f" filas_libro={len(d['saldos_anteriores'])} compras={d['compras']}")
        assert izq == D(d["saldo"])
        rp = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": cliente}, headers=h)
        print(f"      PDF: {rp.status_code} {len(rp.content)} bytes")
        assert rp.status_code == 200, rp.text
        assert rp.content[:4] == b"%PDF"


def test_estado_cuenta_con_rango_de_fechas(client, base_datos):
    """El caso de uso real: la pantalla manda desde/hasta del periodo actual."""
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    crear_saldo(client, h, tipo="cobrar", tercero="Yojan", fecha="2025-11-03",
                concepto="Deuda vieja", valor_total="3000000", abonado="1000000")
    print("\n===== 6b. ESTADO DE CUENTA CON RANGO (lo que manda la pantalla) =====")
    r = client.get(f"{API}/estado-cuenta",
                   params={"cliente": "Yojan", **PER}, headers=h)
    d = r.json()
    print(f"  con desde/hasta {PER}: saldo={d['saldo']}"
          f" libro_saldo={d['libro_anterior_saldo']} filas_libro={len(d['saldos_anteriores'])}")
    r2 = client.get(f"{API}/estado-cuenta", params={"cliente": "Yojan"}, headers=h)
    d2 = r2.json()
    print(f"  sin rango:                 saldo={d2['saldo']}"
          f" libro_saldo={d2['libro_anterior_saldo']} filas_libro={len(d2['saldos_anteriores'])}")
    print("  >>> el libro anterior desaparece del estado de cuenta cuando hay rango:",
          D(d["libro_anterior_saldo"]) == 0 and D(d2["libro_anterior_saldo"]) > 0)


# ------------------------------------------------------------- 7 inventario
def test_inventario_no_se_mueve(client, base_datos):
    h = auth_headers(client, "admin.a")
    client.post(f"{API}/compras", json={
        "fecha": "2026-07-02", "productor": "Sebastián Ruiz",
        "kilos_brutos": "100", "precio_kilo": "18000"}, headers=h)
    antes = resumen(client, h)
    crear_saldo(client, h, tipo="pagar", tercero="Sebastián Ruiz", fecha="2025-01-01",
                concepto="1000 kg del libro viejo", valor_total="18000000")
    crear_saldo(client, h, tipo="cobrar", tercero="Alba", fecha="2025-01-01",
                concepto="500 kg del libro viejo", valor_total="10000000")
    despues = resumen(client, h)
    print("\n===== 7. INVENTARIO =====")
    print(f"  kilos_disponibles antes={antes['kilos_disponibles']} despues={despues['kilos_disponibles']}")
    print(f"  borona_disponible antes={antes['borona_disponible']} despues={despues['borona_disponible']}")
    r = client.post(f"{API}/ventas", json={
        "fecha": "2026-07-20", "cliente": "Alba", "kilos": "150",
        "precio_kilo": "20000"}, headers=h)
    print(f"  vender 150 kg teniendo 100: HTTP {r.status_code} -> {r.json().get('detail', r.text)[:120]}")
    assert D(antes["kilos_disponibles"]) == D(despues["kilos_disponibles"])
    assert r.status_code != 201


# ------------------------------------------------------- extras adversariales
def test_variantes_de_escritura_y_tenencia(client, base_datos):
    h = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    client.post(f"{API}/compras", json={
        "fecha": "2026-07-02", "productor": "Carlos Ricaute",
        "kilos_brutos": "100", "precio_kilo": "18000"}, headers=h)
    s = crear_saldo(client, h, tipo="pagar", tercero="carlos ricaute",
                    fecha="2025-01-01", concepto="vieja", valor_total="1000000")
    print("\n===== EXTRA: canonizacion y multiempresa =====")
    print("  tercero guardado:", s["tercero"])
    res = resumen(client, h)
    print("  filas por_productor:", [(f["productor"], f["por_pagar"]) for f in res["por_productor"]])
    print("  tarjeta por_pagar:", res["por_pagar_productores"],
          " columna:", sum_(res["por_productor"], "por_pagar"))
    assert sum_(res["por_productor"], "por_pagar") == D(res["por_pagar_productores"])

    resb = resumen(client, hb)
    print("  empresa B ve libro:", resb["por_pagar_libro_anterior"], resb["por_cobrar_libro_anterior"])
    assert D(resb["por_pagar_libro_anterior"]) == 0
    r = client.get(f"{API}/saldos-anteriores/{s['id']}", headers=hb)
    print("  empresa B GET saldo ajeno:", r.status_code)


def test_abonos_y_validaciones(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== EXTRA: abonos, estados y validaciones =====")
    s = crear_saldo(client, h, tipo="cobrar", tercero="Alba", fecha="2025-01-01",
                    concepto="xx", valor_total="1000000", abonado="400000")
    print("  creado con abono inicial: estado=", s["estado"], " abonos=", len(s["abonos"]),
          " abonado=", s["abonado"], " saldo=", s["saldo"])
    r = client.post(f"{API}/saldos-anteriores/{s['id']}/abonos",
                    json={"fecha": "2026-07-01", "valor": "900000"}, headers=h)
    print("  abono mayor al saldo:", r.status_code, r.json().get("detail"))
    r = client.post(f"{API}/saldos-anteriores/{s['id']}/abonos",
                    json={"fecha": "2026-07-01", "valor": "600000"}, headers=h)
    s = r.json()
    print("  abono que completa:", s["estado"], s["saldo"], "abonos=", len(s["abonos"]))
    r = client.post(f"{API}/saldos-anteriores/{s['id']}/anular", headers=h)
    print("  anular con abonos:", r.status_code, r.json().get("detail"))
    r = client.delete(f"{API}/saldos-anteriores/{s['id']}", headers=h)
    print("  eliminar con abonos:", r.status_code, r.json().get("detail"))
    r = client.post(f"{API}/saldos-anteriores", json={
        "tipo": "cobrar", "tercero": "Z", "fecha": "2025-01-01", "concepto": "xx",
        "valor_total": "100", "abonado": "500"}, headers=h)
    print("  abonado > valor_total al crear:", r.status_code, r.json().get("detail"))

    # editar bajando el valor por debajo de lo abonado
    s2 = crear_saldo(client, h, tipo="cobrar", tercero="Beto", fecha="2025-01-01",
                     concepto="xx", valor_total="1000000", abonado="800000")
    r = client.put(f"{API}/saldos-anteriores/{s2['id']}",
                   json={"valor_total": "500000"}, headers=h)
    print("  editar valor_total 1.000.000 -> 500.000 con 800.000 abonado:",
          r.status_code, r.json().get("estado"), "saldo=", r.json().get("saldo"))
    res = resumen(client, h)
    print("  por_cobrar_libro_anterior con ese saldo negativo:",
          res["por_cobrar_libro_anterior"], " por_cobrar_clientes:", res["por_cobrar_clientes"])

    # cambiar el tipo de un saldo por PUT
    s3 = crear_saldo(client, h, tipo="cobrar", tercero="Ana", fecha="2025-01-01",
                     concepto="xx", valor_total="100000")
    r = client.put(f"{API}/saldos-anteriores/{s3['id']}", json={"tipo": "pagar"}, headers=h)
    print("  cambiar tipo cobrar->pagar por PUT:", r.status_code, r.json().get("tipo"))
