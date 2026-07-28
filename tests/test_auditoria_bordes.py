"""AUDITORIA parte 2: bordes donde el cuadre se puede romper."""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PER = {"desde": "2026-07-01", "hasta": "2026-07-31"}


def D(x):
    return Decimal(str(x))


def sum_(items, campo):
    return sum((D(i[campo]) for i in items), Decimal("0"))


def resumen(client, h, per=None):
    r = client.get(f"{API}/resumen", params=per or PER, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def crear_saldo(client, h, **kw):
    r = client.post(f"{API}/saldos-anteriores", json=kw, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_productor_con_compras_fuera_del_periodo(client, base_datos):
    """El productor compró en JUNIO y se le debe; el resumen se pide de JULIO."""
    h = auth_headers(client, "admin.a")
    client.post(f"{API}/compras", json={
        "fecha": "2026-06-10", "productor": "Junio Perez",
        "kilos_brutos": "100", "precio_kilo": "18000"}, headers=h)
    client.post(f"{API}/compras", json={
        "fecha": "2026-07-10", "productor": "Julio Gomez",
        "kilos_brutos": "100", "precio_kilo": "18000"}, headers=h)

    base = resumen(client, h)
    print("\n===== BORDE A: productor que solo compró FUERA del período =====")
    print("  SIN libro anterior:")
    print("   filas:", [(f["productor"], f["por_pagar"]) for f in base["por_productor"]])
    print("   columna =", sum_(base["por_productor"], "por_pagar"),
          " tarjeta =", base["por_pagar_productores"],
          " dif =", sum_(base["por_productor"], "por_pagar") - D(base["por_pagar_productores"]))

    crear_saldo(client, h, tipo="pagar", tercero="Junio Perez", fecha="2025-01-01",
                concepto="deuda vieja", valor_total="500000")
    con = resumen(client, h)
    print("  CON libro anterior de ese mismo productor:")
    print("   filas:", [(f["productor"], f["compras"], f["por_pagar"]) for f in con["por_productor"]])
    print("   columna =", sum_(con["por_productor"], "por_pagar"),
          " tarjeta =", con["por_pagar_productores"],
          " dif =", sum_(con["por_productor"], "por_pagar") - D(con["por_pagar_productores"]))
    print("   por_pagar_libro_anterior =", con["por_pagar_libro_anterior"])


def test_saldo_pagado_no_desaparece_del_cuadre(client, base_datos):
    h = auth_headers(client, "admin.a")
    client.post(f"{API}/compras", json={
        "fecha": "2026-07-10", "productor": "Ana", "kilos_brutos": "10",
        "precio_kilo": "1000"}, headers=h)
    crear_saldo(client, h, tipo="pagar", tercero="Zulema Pagada", fecha="2025-01-01",
                concepto="ya pagada", valor_total="100000", abonado="100000")
    res = resumen(client, h)
    print("\n===== BORDE B: saldo ya pagado del libro =====")
    print("  filas:", [(f["productor"], f["por_pagar"]) for f in res["por_productor"]])
    print("  columna =", sum_(res["por_productor"], "por_pagar"),
          " tarjeta =", res["por_pagar_productores"])
    assert sum_(res["por_productor"], "por_pagar") == D(res["por_pagar_productores"])


def test_saldo_sobreabonado_en_el_cuadre(client, base_datos):
    """Ya NO se puede dejar un saldo del libro sobreabonado.

    Antes bajar el valor_total por debajo de lo abonado devolvia 200 y el saldo
    quedaba NEGATIVO, restando de la cartera. Ahora se rechaza y el saldo sigue
    como estaba, asi que la columna sigue cuadrando con la tarjeta.
    """
    h = auth_headers(client, "admin.a")
    client.post(f"{API}/compras", json={
        "fecha": "2026-07-10", "productor": "Ana", "kilos_brutos": "10",
        "precio_kilo": "1000"}, headers=h)
    s = crear_saldo(client, h, tipo="pagar", tercero="Ana", fecha="2025-01-01",
                    concepto="vieja", valor_total="1000000", abonado="900000")
    r = client.put(f"{API}/saldos-anteriores/{s['id']}",
                   json={"valor_total": "500000"}, headers=h)
    print("\n===== BORDE C: saldo del libro sobreabonado =====")
    print("  editar 1.000.000 -> 500.000 con 900.000 abonado:", r.status_code,
          r.json().get("error", {}).get("detail"))
    assert r.status_code == 422
    res = resumen(client, h)
    print("  por_pagar_libro_anterior =", res["por_pagar_libro_anterior"])
    print("  filas:", [(f["productor"], f["por_pagar"]) for f in res["por_productor"]])
    print("  columna =", sum_(res["por_productor"], "por_pagar"),
          " tarjeta =", res["por_pagar_productores"],
          " dif =", sum_(res["por_productor"], "por_pagar") - D(res["por_pagar_productores"]))
    assert D(res["por_pagar_libro_anterior"]) == D("100000")
    assert sum_(res["por_productor"], "por_pagar") == D(res["por_pagar_productores"])


def test_dos_variantes_de_escritura_del_mismo_tercero(client, base_datos):
    h = auth_headers(client, "admin.a")
    client.post(f"{API}/compras", json={
        "fecha": "2026-07-10", "productor": "José Niño", "kilos_brutos": "10",
        "precio_kilo": "1000"}, headers=h)
    a = crear_saldo(client, h, tipo="pagar", tercero="José Niño", fecha="2025-01-01",
                    concepto="una", valor_total="100000")
    b = crear_saldo(client, h, tipo="pagar", tercero="JOSE NINO", fecha="2025-01-02",
                    concepto="otra", valor_total="200000")
    print("\n===== BORDE D: acentos / variantes =====")
    print("  guardados:", repr(a["tercero"]), repr(b["tercero"]))
    res = resumen(client, h)
    print("  filas:", [(f["productor"], f["por_pagar"]) for f in res["por_productor"]])
    print("  columna =", sum_(res["por_productor"], "por_pagar"),
          " tarjeta =", res["por_pagar_productores"],
          " dif =", sum_(res["por_productor"], "por_pagar") - D(res["por_pagar_productores"]))
    assert sum_(res["por_productor"], "por_pagar") == D(res["por_pagar_productores"])


def test_pdf_no_filtra_nada_interno(client, base_datos):
    """El PDF va al cliente: no puede llevar observaciones ni nada de la quesera."""
    from pypdf import PdfReader
    import io as _io

    h = auth_headers(client, "admin.a")
    client.post(f"{API}/compras", json={
        "fecha": "2026-07-02", "productor": "SECRETO PRODUCTOR",
        "kilos_brutos": "100", "precio_kilo": "18000"}, headers=h)
    client.post(f"{API}/ventas", json={
        "fecha": "2026-07-10", "cliente": "Alba", "kilos": "50",
        "precio_kilo": "20000", "gasto_monto": "77777"}, headers=h)
    crear_saldo(client, h, tipo="cobrar", tercero="Alba", fecha="2025-05-03",
                concepto="Venta 120 kg del 3 de mayo", valor_total="3000000",
                abonado="1000000", observaciones="OBSERVACION INTERNA SECRETA")
    r = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": "Alba"}, headers=h)
    assert r.status_code == 200
    texto = "\n".join(p.extract_text() for p in PdfReader(_io.BytesIO(r.content)).pages)
    print("\n===== PDF: TEXTO EXTRAIDO =====")
    print(texto)
    print("===== FIN PDF =====")
    for prohibido in ("OBSERVACION INTERNA SECRETA", "SECRETO PRODUCTOR", "77.777", "77777"):
        print(f"  contiene {prohibido!r}: {prohibido in texto}")
        assert prohibido not in texto
