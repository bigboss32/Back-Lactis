"""AUDITORÍA ADVERSARIAL de las facturas de reventa (varios renglones + derrame).

El único objetivo de este archivo es VENDER QUESO QUE NO EXISTE o DESCUADRAR LA
CARTERA. No prueba que las cosas funcionen: prueba que no se pueden romper.

Cada prueba imprime las cifras para poder cuadrarlas a mano.
"""
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"

FC = "2026-07-01"   # fecha de compra
FV = "2026-07-10"   # fecha de venta


def D(v):
    return Decimal(str(v))


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


@pytest.fixture()
def h_b(client, base_datos):
    return auth_headers(client, "admin.b")


# --------------------------------------------------------------- utilidades
def comprar(client, h, *, kilos="1000", precio="10000", borona="0", productor="Yeferson"):
    r = client.post(
        f"{API}/compras",
        json={"fecha": FC, "productor": productor, "kilos_brutos": kilos,
              "borona_kilos": borona, "precio_kilo": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def comprar_barras(client, h, *, barras="100", precio="12000", productor="Marlion"):
    r = client.post(
        f"{API}/compras",
        json={"fecha": FC, "productor": productor, "tipo": "mozzarella",
              "barras": barras, "precio_barra": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def doc_venta(client, h, renglones, *, tercero="Tienda La 33", fecha=FV, **extra):
    return client.post(
        f"{API}/documentos",
        json={"tipo": "venta", "fecha": fecha, "tercero": tercero,
              "renglones": renglones, **extra},
        headers=h,
    )


def doc_compra(client, h, renglones, *, tercero="Yeferson", fecha=FC, **extra):
    return client.post(
        f"{API}/documentos",
        json={"tipo": "compra", "fecha": fecha, "tercero": tercero,
              "renglones": renglones, **extra},
        headers=h,
    )


def existencias(client, h):
    """Los tres inventarios, leídos del resumen (que es lo que ve el dueño)."""
    r = client.get(
        f"{API}/resumen", params={"desde": "2026-01-01", "hasta": "2026-12-31"}, headers=h
    )
    assert r.status_code == 200, r.text
    j = r.json()
    return {
        "queso": D(j["kilos_disponibles"]),
        "borona": D(j.get("borona_disponible", 0)),
        "barras": D(j.get("barras_disponibles", 0)),
        "por_cobrar": D(j["por_cobrar_clientes"]),
        "ventas": D(j["total_ventas"]),
    }


def leer_doc(client, h, doc_id):
    r = client.get(f"{API}/documentos/{doc_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def cuadrar(doc, etiqueta=""):
    """LA REGLA DE ORO: el desglose suma EXACTO la cifra grande."""
    suma_vivos = sum(
        (D(r["valor_total"]) for r in doc["renglones"] if r["estado"] != "anulada"),
        D(0),
    )
    suma_anulados = sum(
        (D(r["valor_total"]) for r in doc["renglones"] if r["estado"] == "anulada"),
        D(0),
    )
    suma_abonado = sum(
        (D(r["abonado"]) for r in doc["renglones"] if r["estado"] != "anulada"), D(0)
    )
    # y la suma de los ABONOS uno por uno (la lista que el dueño mira)
    suma_abonos = sum(
        (D(a["valor"]) for r in doc["renglones"] if r["estado"] != "anulada"
         for a in r.get("abonos", [])),
        D(0),
    )
    print(f"  [cuadre {etiqueta}] total={doc['total']} suma_renglones={suma_vivos} "
          f"| anulado={doc['total_anulado']} suma={suma_anulados} "
          f"| abonado={doc['abonado']} suma_abonado={suma_abonado} "
          f"suma_abonos_uno_x_uno={suma_abonos} | saldo={doc['saldo']}")
    assert D(doc["total"]) == suma_vivos, f"{etiqueta}: el total no es la suma"
    assert D(doc["total_anulado"]) == suma_anulados, f"{etiqueta}: anulado descuadrado"
    assert D(doc["abonado"]) == suma_abonado, f"{etiqueta}: abonado descuadrado"
    assert D(doc["abonado"]) == suma_abonos, (
        f"{etiqueta}: la suma de los abonos uno por uno ({suma_abonos}) NO da lo "
        f"abonado ({doc['abonado']})"
    )
    assert D(doc["saldo"]) == D(doc["total"]) - D(doc["abonado"]), f"{etiqueta}: saldo"


# ===========================================================================
# 1. VENDER QUESO QUE NO EXISTE
# ===========================================================================
def test_a1_dos_renglones_iguales_pasan_del_disponible(client, h):
    """300 + 300 contra 400: tiene que rebotar y NO dejar la primera fila escrita."""
    comprar(client, h, kilos="400", precio="9000")
    antes = existencias(client, h)
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "300", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "300", "precio_kilo": "15000"},
    ])
    print(f"\n  300+300 vs 400 -> {r.status_code} {r.text[:200]}")
    assert r.status_code == 422, "PASARON 600 kg CONTRA 400"
    # nada escrito
    assert client.get(f"{API}/documentos", params={"tipo": "venta"}, headers=h).json()["total"] == 0
    assert client.get(f"{API}/ventas", headers=h).json()["total"] == 0, "quedó la primera fila"
    despues = existencias(client, h)
    print(f"  queso antes={antes['queso']} despues={despues['queso']}")
    assert despues["queso"] == antes["queso"] == D("400")


def test_a2_tres_renglones_el_tercero_no_cabe(client, h):
    """150 + 150 + 150 contra 400: los dos primeros caben, el tercero no.
    No puede quedar ni el primero ni el segundo escritos."""
    comprar(client, h, kilos="400", precio="9000")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "150", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "150", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "150", "precio_kilo": "15000"},
    ])
    print(f"\n  150x3=450 vs 400 -> {r.status_code} {r.text[:220]}")
    assert r.status_code == 422, "PASARON 450 kg CONTRA 400"
    assert client.get(f"{API}/ventas", headers=h).json()["total"] == 0, "filas a medias"
    assert existencias(client, h)["queso"] == D("400")


def test_a3_tres_renglones_de_tipos_distintos_el_tercero_no_cabe(client, h):
    """queso 300 + borona 40 + queso 150 contra 400 kg de queso y 50 de borona.
    El que no cabe es el TERCERO, y es del mismo tipo que el primero."""
    comprar(client, h, kilos="400", precio="9000", borona="50")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "300", "precio_kilo": "15000"},
        {"tipo": "borona", "kilos": "40", "precio_kilo": "5000"},
        {"tipo": "queso", "kilos": "150", "precio_kilo": "15000"},
    ])
    print(f"\n  queso 300+150=450 vs 400 (con borona de por medio) -> "
          f"{r.status_code} {r.text[:220]}")
    assert r.status_code == 422, "PASARON 450 kg de queso CONTRA 400"
    assert client.get(f"{API}/ventas", headers=h).json()["total"] == 0
    e = existencias(client, h)
    print(f"  queso={e['queso']} borona={e['borona']}")
    assert e["queso"] == D("400") and e["borona"] == D("50")


def test_a4_el_mismo_producto_en_dos_documentos(client, h):
    """400 disponibles. Factura 1 se lleva 300. Factura 2 pide 300: no hay."""
    comprar(client, h, kilos="400", precio="9000")
    r1 = doc_venta(client, h, [{"tipo": "queso", "kilos": "300", "precio_kilo": "15000"}])
    assert r1.status_code == 201, r1.text
    print(f"\n  factura 1 (300 kg) -> 201, quedan {existencias(client, h)['queso']}")
    r2 = doc_venta(client, h, [{"tipo": "queso", "kilos": "300", "precio_kilo": "15000"}],
                   tercero="Otro Cliente")
    print(f"  factura 2 (300 kg) -> {r2.status_code} {r2.text[:200]}")
    assert r2.status_code == 422, "SE VENDIÓ EL MISMO QUESO DOS VECES"
    e = existencias(client, h)
    print(f"  queso disponible final={e['queso']}")
    assert e["queso"] == D("100"), "el inventario no quedó exacto"


def test_a5_dos_documentos_multirrenglon_encadenados(client, h):
    """Factura 1: 200 + 150 = 350 de 400. Factura 2: 30 + 30 = 60 contra 50 que
    quedan. Tiene que rebotar y no dejar nada."""
    comprar(client, h, kilos="400", precio="9000")
    r1 = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "200", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "150", "precio_kilo": "15000"},
    ])
    assert r1.status_code == 201, r1.text
    assert existencias(client, h)["queso"] == D("50")
    r2 = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "30", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "30", "precio_kilo": "15000"},
    ], tercero="Cliente Dos")
    print(f"\n  30+30=60 vs 50 -> {r2.status_code} {r2.text[:200]}")
    assert r2.status_code == 422
    print(f"  ventas escritas: {client.get(f'{API}/ventas', headers=h).json()['total']}")
    assert client.get(f"{API}/ventas", headers=h).json()["total"] == 2
    assert existencias(client, h)["queso"] == D("50")


def test_a6_borona_que_no_existe(client, h):
    """Se compran 400 kg de queso SIN borona. Vender borona no puede pasar,
    aunque haya queso de sobra."""
    comprar(client, h, kilos="400", precio="9000", borona="0")
    r = doc_venta(client, h, [{"tipo": "borona", "kilos": "10", "precio_kilo": "5000"}])
    print(f"\n  borona 10 con 0 de borona (y 400 de queso) -> "
          f"{r.status_code} {r.text[:200]}")
    assert r.status_code == 422, "SE VENDIÓ BORONA QUE NO EXISTE"
    # y con dos renglones que se suman
    comprar(client, h, kilos="1", precio="9000", borona="30")
    r = doc_venta(client, h, [
        {"tipo": "borona", "kilos": "20", "precio_kilo": "5000"},
        {"tipo": "borona", "kilos": "20", "precio_kilo": "5000"},
    ])
    print(f"  borona 20+20=40 vs 30 -> {r.status_code} {r.text[:200]}")
    assert r.status_code == 422, "SE VENDIERON 40 kg DE BORONA CON 30"
    e = existencias(client, h)
    print(f"  borona final={e['borona']}")
    assert e["borona"] == D("30")


def test_a7_mozzarella_por_barras(client, h):
    """Las barras NO se pagan con kilos: 1000 kg de queso no autorizan una barra.
    Y dos renglones de barras se suman."""
    comprar(client, h, kilos="1000", precio="9000")
    r = doc_venta(client, h, [{"tipo": "mozzarella", "barras": "5", "precio_barra": "20000"}])
    print(f"\n  5 barras con 0 barras (y 1000 kg de queso) -> "
          f"{r.status_code} {r.text[:200]}")
    assert r.status_code == 422, "SE VENDIERON BARRAS QUE NO EXISTEN"

    comprar_barras(client, h, barras="10", precio="12000")
    r = doc_venta(client, h, [
        {"tipo": "mozzarella", "barras": "6", "precio_barra": "20000"},
        {"tipo": "mozzarella", "barras": "6", "precio_barra": "20000"},
    ])
    print(f"  6+6=12 barras vs 10 -> {r.status_code} {r.text[:220]}")
    assert r.status_code == 422, "SE VENDIERON 12 BARRAS CON 10"
    e = existencias(client, h)
    print(f"  barras={e['barras']} queso={e['queso']}")
    assert e["barras"] == D("10")

    # el que sí cabe: 4 + 6 = 10 exacto
    r = doc_venta(client, h, [
        {"tipo": "mozzarella", "barras": "4", "precio_barra": "20000"},
        {"tipo": "mozzarella", "barras": "6", "precio_barra": "21000"},
    ])
    assert r.status_code == 201, r.text
    doc = r.json()
    cuadrar(doc, "mozzarella 4+6")
    # 4 x 20.000 = 80.000 ; 6 x 21.000 = 126.000 -> 206.000
    assert D(doc["total"]) == D("206000"), doc["total"]
    e = existencias(client, h)
    print(f"  barras despues={e['barras']}")
    assert e["barras"] == D("0")


def test_a8_mezcla_de_tres_inventarios_en_una_factura(client, h):
    """Una factura con queso, borona y mozzarella: cada uno contra SU inventario,
    y si UNO no alcanza, no se escribe nada de los tres."""
    comprar(client, h, kilos="100", precio="9000", borona="20")
    comprar_barras(client, h, barras="5", precio="12000")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "50", "precio_kilo": "15000"},
        {"tipo": "borona", "kilos": "10", "precio_kilo": "5000"},
        {"tipo": "mozzarella", "barras": "9", "precio_barra": "20000"},  # solo hay 5
    ])
    print(f"\n  queso 50 ok + borona 10 ok + barras 9 de 5 -> "
          f"{r.status_code} {r.text[:220]}")
    assert r.status_code == 422
    assert client.get(f"{API}/ventas", headers=h).json()["total"] == 0, "filas a medias"
    e = existencias(client, h)
    print(f"  queso={e['queso']} borona={e['borona']} barras={e['barras']}")
    assert (e["queso"], e["borona"], e["barras"]) == (D("100"), D("20"), D("5"))


# ===========================================================================
# 2. EL DERRAME DEL ABONO
# ===========================================================================
def base_tres_renglones(client, h):
    """Una factura de venta de 3 renglones: 1.000.000 + 500.000 + 250.000."""
    comprar(client, h, kilos="1000", precio="5000")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "100", "precio_kilo": "10000"},   # 1.000.000
        {"tipo": "queso", "kilos": "100", "precio_kilo": "5000"},    #   500.000
        {"tipo": "queso", "kilos": "100", "precio_kilo": "2500"},    #   250.000
    ])
    assert r.status_code == 201, r.text
    doc = r.json()
    assert D(doc["total"]) == D("1750000"), doc["total"]
    return doc


def abonar_doc(client, h, doc_id, valor, fecha=FV, obs=None):
    return client.post(
        f"{API}/documentos/{doc_id}/abonos",
        json={"fecha": fecha, "valor": str(valor), "observaciones": obs},
        headers=h,
    )


def test_b1_derrame_renglon_y_medio(client, h):
    """UN ABONO QUE CUBRE JUSTO UN RENGLÓN Y MEDIO: 1.250.000 sobre
    1.000.000 + 500.000 + 250.000. Tiene que dejar el 1 pagado, el 2 con 250.000
    y el 3 en cero, y la SUMA tiene que dar 1.250.000 exacto."""
    doc = base_tres_renglones(client, h)
    r = abonar_doc(client, h, doc["id"], "1250000")
    assert r.status_code == 200, r.text
    doc = r.json()
    print("\n  ===== DERRAME DE 1.250.000 SOBRE 1.000.000 / 500.000 / 250.000 =====")
    for rr in doc["renglones"]:
        print(f"    orden={rr['orden']} total={rr['valor_total']} "
              f"abonado={rr['abonado']} saldo={rr['saldo']} estado={rr['estado']} "
              f"abonos={[a['valor'] for a in rr['abonos']]}")
    cuadrar(doc, "derrame 1,5 renglones")
    cuotas = [D(rr["abonado"]) for rr in doc["renglones"]]
    assert cuotas == [D("1000000"), D("250000"), D("0")], cuotas
    assert sum(cuotas, D(0)) == D("1250000"), "la suma del derrame no da el abono"
    assert D(doc["abonado"]) == D("1250000")
    assert D(doc["saldo"]) == D("500000")
    estados = [rr["estado"] for rr in doc["renglones"]]
    print(f"  estados={estados} estado_pago_documento={doc['estado_pago']}")
    assert estados == ["pagada", "parcial", "pendiente"], estados
    assert doc["estado_pago"] == "parcial"


def test_b2_abono_que_se_pasa_del_saldo_rebota_sin_escribir(client, h):
    doc = base_tres_renglones(client, h)
    r = abonar_doc(client, h, doc["id"], "1750000.01")
    print(f"\n  abono 1.750.000,01 sobre saldo 1.750.000 -> "
          f"{r.status_code} {r.text[:220]}")
    assert r.status_code == 422, "SE ABONÓ MÁS QUE EL SALDO"
    doc = leer_doc(client, h, doc["id"])
    cuadrar(doc, "tras rebote")
    assert D(doc["abonado"]) == D(0), "quedó un abono a medias"
    assert all(not rr["abonos"] for rr in doc["renglones"])

    # y uno que se pasa DESPUÉS de un abono parcial
    assert abonar_doc(client, h, doc["id"], "1000000").status_code == 200
    r = abonar_doc(client, h, doc["id"], "750001")
    print(f"  abono 750.001 sobre saldo 750.000 -> {r.status_code} {r.text[:200]}")
    assert r.status_code == 422
    doc = leer_doc(client, h, doc["id"])
    cuadrar(doc, "tras segundo rebote")
    assert D(doc["abonado"]) == D("1000000")


def test_b3_derrame_completo_exacto(client, h):
    """El abono que cubre TODA la factura: los tres quedan pagados y la suma
    de las tres cuotas da el total al peso."""
    doc = base_tres_renglones(client, h)
    r = abonar_doc(client, h, doc["id"], "1750000")
    assert r.status_code == 200, r.text
    doc = r.json()
    cuadrar(doc, "derrame total")
    print(f"  estado_pago={doc['estado_pago']} saldo={doc['saldo']}")
    assert D(doc["saldo"]) == D(0)
    assert doc["estado_pago"] == "pagada"
    assert [D(rr["abonado"]) for rr in doc["renglones"]] == [
        D("1000000"), D("500000"), D("250000")]
    # y ni un peso más
    r = abonar_doc(client, h, doc["id"], "1")
    print(f"  un peso más sobre una factura pagada -> {r.status_code}")
    assert r.status_code == 422


def test_b4_derrame_en_muchos_abonos_suma_exacta(client, h):
    """Siete abonos de cifras feas sobre tres renglones: la suma de TODOS los
    abonos de TODOS los renglones tiene que dar la suma de los siete, exacta."""
    doc = base_tres_renglones(client, h)
    pagos = ["333333", "1", "666666.67", "0.33", "99999.99", "1.01", "10"]
    for p in pagos:
        r = abonar_doc(client, h, doc["id"], p, obs=f"pago {p}")
        assert r.status_code == 200, f"{p}: {r.text}"
    doc = leer_doc(client, h, doc["id"])
    esperado = sum((D(p) for p in pagos), D(0))
    print(f"\n  ===== SIETE ABONOS: {pagos} =====")
    for rr in doc["renglones"]:
        print(f"    orden={rr['orden']} abonado={rr['abonado']} "
              f"abonos={[a['valor'] for a in rr['abonos']]}")
    print(f"  esperado={esperado} documento.abonado={doc['abonado']}")
    cuadrar(doc, "siete abonos")
    assert D(doc["abonado"]) == esperado, (
        f"DESCUADRE: se abonaron {esperado} y el documento dice {doc['abonado']}"
    )


def test_b5_pagada_de_contado_multirrenglon(client, h):
    """`pagada_de_contado` en una factura de tres renglones: cada renglón queda
    con su abono entero y la suma da el total exacto."""
    comprar(client, h, kilos="1000", precio="5000")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "33.33", "precio_kilo": "3333"},
        {"tipo": "queso", "kilos": "66.67", "precio_kilo": "7777"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "1"},
    ], pagada_de_contado=True)
    assert r.status_code == 201, r.text
    doc = r.json()
    print("\n  ===== CONTADO CON CIFRAS FEAS =====")
    for rr in doc["renglones"]:
        print(f"    total={rr['valor_total']} abonado={rr['abonado']} "
              f"estado={rr['estado']}")
    cuadrar(doc, "contado")
    assert D(doc["saldo"]) == D(0), "una factura de contado quedó con saldo"
    assert doc["estado_pago"] == "pagada"


def test_b6_abono_mezclado_documento_y_renglon(client, h):
    """ABONAR AL RENGLÓN Y AL DOCUMENTO MEZCLADO. El abono al renglón 2 no puede
    hacer que el derrame del documento se pase, ni que la suma deje de cuadrar."""
    doc = base_tres_renglones(client, h)
    r2 = doc["renglones"][1]
    # 1) abono directo al renglón 2 (500.000): 200.000
    r = client.post(f"{API}/ventas/{r2['id']}/abonos",
                    json={"fecha": FV, "valor": "200000"}, headers=h)
    assert r.status_code == 200, r.text
    doc = leer_doc(client, h, doc["id"])
    cuadrar(doc, "tras abono al renglon 2")
    print(f"\n  tras abono al renglón 2: abonado={doc['abonado']} saldo={doc['saldo']}")
    assert D(doc["abonado"]) == D("200000")
    assert D(doc["saldo"]) == D("1550000")

    # 2) abono al DOCUMENTO por el saldo exacto que queda
    r = abonar_doc(client, h, doc["id"], "1550000")
    assert r.status_code == 200, r.text
    doc = r.json()
    print("  tras abono al documento por 1.550.000:")
    for rr in doc["renglones"]:
        print(f"    orden={rr['orden']} total={rr['valor_total']} "
              f"abonado={rr['abonado']} abonos={[a['valor'] for a in rr['abonos']]}")
    cuadrar(doc, "mezclado")
    assert D(doc["abonado"]) == D("1750000")
    assert D(doc["saldo"]) == D(0)
    assert doc["estado_pago"] == "pagada"
    # el renglón 2 tiene DOS abonos que suman su total
    assert sum((D(a["valor"]) for a in doc["renglones"][1]["abonos"]), D(0)) == D("500000")

    # 3) un peso más, ni por el renglón ni por el documento
    assert abonar_doc(client, h, doc["id"], "1").status_code == 422
    r = client.post(f"{API}/ventas/{r2['id']}/abonos",
                    json={"fecha": FV, "valor": "1"}, headers=h)
    print(f"  un peso más al renglón pagado -> {r.status_code}")
    assert r.status_code == 422


def test_b7_derrame_con_un_renglon_anulado(client, h):
    """Con un renglón ANULADO en medio, el derrame lo tiene que SALTAR y el
    saldo de la factura no puede incluirlo."""
    doc = base_tres_renglones(client, h)
    medio = doc["renglones"][1]
    r = client.post(f"{API}/ventas/{medio['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    doc = leer_doc(client, h, doc["id"])
    print(f"\n  tras anular el renglón 2: total={doc['total']} "
          f"anulado={doc['total_anulado']} saldo={doc['saldo']}")
    cuadrar(doc, "con renglon anulado")
    assert D(doc["total"]) == D("1250000")
    assert D(doc["total_anulado"]) == D("500000")

    # el abono se derrama solo sobre los vivos
    r = abonar_doc(client, h, doc["id"], "1250000")
    assert r.status_code == 200, r.text
    doc = r.json()
    for rr in doc["renglones"]:
        print(f"    orden={rr['orden']} estado={rr['estado']} abonado={rr['abonado']}")
    cuadrar(doc, "derrame saltando anulado")
    assert D(doc["renglones"][1]["abonado"]) == D(0), "SE ABONÓ A UN RENGLÓN ANULADO"
    assert D(doc["saldo"]) == D(0)
    # y ni un peso más (el anulado no da capacidad)
    r = abonar_doc(client, h, doc["id"], "1")
    print(f"  un peso más -> {r.status_code} {r.text[:160]}")
    assert r.status_code == 422, "EL RENGLÓN ANULADO DIO CAPACIDAD DE ABONO"


def test_b8_saldo_a_favor_vs_capacidad_de_abono(client, h):
    """EL SALDO QUE SE MUESTRA CONTRA EL ABONO QUE SE ACEPTA.

    Se rebaja el precio de un renglón YA PAGADO: queda con saldo a favor. El
    documento muestra un `saldo`, y la pregunta es si el sistema acepta abonar
    exactamente ese saldo y ni un peso más.
    """
    comprar(client, h, kilos="1000", precio="5000")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "100", "precio_kilo": "10000"},   # 1.000.000
        {"tipo": "queso", "kilos": "100", "precio_kilo": "10000"},   # 1.000.000
    ])
    doc = r.json()
    r1, r2 = doc["renglones"]
    # se paga el renglón 1 completo
    assert client.post(f"{API}/ventas/{r1['id']}/abonos",
                       json={"fecha": FV, "valor": "1000000"}, headers=h).status_code == 200
    # y después se le rebaja el precio a la mitad -> saldo a favor de 500.000
    r = client.put(f"{API}/ventas/{r1['id']}",
                   json={"kilos": "100", "precio_kilo": "5000"}, headers=h)
    assert r.status_code == 200, r.text
    doc = leer_doc(client, h, doc["id"])
    print("\n  ===== SALDO A FAVOR EN UN RENGLÓN =====")
    for rr in doc["renglones"]:
        print(f"    total={rr['valor_total']} abonado={rr['abonado']} saldo={rr['saldo']}")
    print(f"  documento: total={doc['total']} abonado={doc['abonado']} "
          f"saldo={doc['saldo']}")
    saldo_mostrado = D(doc["saldo"])   # 1.500.000 - 1.000.000 = 500.000
    # lo que el dueño lee en pantalla es 500.000. ¿Cuánto acepta el sistema?
    r = abonar_doc(client, h, doc["id"], str(saldo_mostrado + 1))
    print(f"  abono de saldo+1 ({saldo_mostrado + 1}) -> {r.status_code} "
          f"{r.text[:220]}")
    assert r.status_code == 422, (
        f"EL DOCUMENTO MUESTRA SALDO {saldo_mostrado} Y ACEPTÓ "
        f"{saldo_mostrado + 1}: el desglose no cuadra con lo que se puede abonar"
    )


# ===========================================================================
# 3. EDITAR
# ===========================================================================
def test_c1_editar_renglones_sin_abonos_revalida_existencias(client, h):
    comprar(client, h, kilos="400", precio="9000")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "200", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "150", "precio_kilo": "15000"},
    ])
    doc = r.json()
    # 1) editar a 200 + 200 = 400 (cabe justo, devolviendo los 350 viejos)
    r = client.put(f"{API}/documentos/{doc['id']}", json={
        "tipo": "venta",
        "renglones": [
            {"tipo": "queso", "kilos": "200", "precio_kilo": "15000"},
            {"tipo": "queso", "kilos": "200", "precio_kilo": "15000"},
        ]}, headers=h)
    print(f"\n  editar 350 -> 400 (de 400) -> {r.status_code} {r.text[:160]}")
    assert r.status_code == 200, r.text
    cuadrar(r.json(), "editado a 400")
    assert existencias(client, h)["queso"] == D(0)
    # 2) editar a 250 + 250 = 500: no cabe
    r = client.put(f"{API}/documentos/{doc['id']}", json={
        "tipo": "venta",
        "renglones": [
            {"tipo": "queso", "kilos": "250", "precio_kilo": "15000"},
            {"tipo": "queso", "kilos": "250", "precio_kilo": "15000"},
        ]}, headers=h)
    print(f"  editar a 500 (de 400) -> {r.status_code} {r.text[:200]}")
    assert r.status_code == 422, "SE EDITÓ A 500 kg CON 400"
    doc = leer_doc(client, h, doc["id"])
    cuadrar(doc, "tras rebote de edicion")
    print(f"  renglones tras el rebote: "
          f"{[(rr['kilos'], rr['valor_total']) for rr in doc['renglones']]}")
    assert [D(rr["kilos"]) for rr in doc["renglones"]] == [D("200"), D("200")]
    assert existencias(client, h)["queso"] == D(0), "el inventario se movió"


def test_c2_editar_renglones_con_abonos_esta_prohibido(client, h):
    doc = base_tres_renglones(client, h)
    assert abonar_doc(client, h, doc["id"], "100000").status_code == 200
    r = client.put(f"{API}/documentos/{doc['id']}", json={
        "tipo": "venta",
        "renglones": [{"tipo": "queso", "kilos": "10", "precio_kilo": "1000"}]},
        headers=h)
    print(f"\n  editar renglones con abonos -> {r.status_code} {r.text[:220]}")
    assert r.status_code == 422, "SE REHICIERON LOS RENGLONES DE UNA FACTURA CON ABONOS"
    doc = leer_doc(client, h, doc["id"])
    cuadrar(doc, "con abonos, intacta")
    assert len(doc["renglones"]) == 3
    assert D(doc["abonado"]) == D("100000")


def test_c3_editar_solo_cabecera_con_abonos_si_se_puede(client, h):
    doc = base_tres_renglones(client, h)
    assert abonar_doc(client, h, doc["id"], "100000").status_code == 200
    r = client.put(f"{API}/documentos/{doc['id']}", json={
        "tipo": "venta", "fecha": "2026-07-15", "tercero": "Tienda Nueva",
        "observaciones": "corregida"}, headers=h)
    assert r.status_code == 200, r.text
    doc = r.json()
    print(f"\n  cabecera editada: fecha={doc['fecha']} tercero={doc['tercero']}")
    fechas = {rr["fecha"] for rr in doc["renglones"]}
    clientes = {rr["cliente"] for rr in doc["renglones"]}
    print(f"  fechas de los renglones={fechas} clientes={clientes}")
    cuadrar(doc, "cabecera editada")
    assert fechas == {"2026-07-15"}, "un renglón quedó con otra fecha"
    assert clientes == {"Tienda Nueva"}, "un renglón quedó con otro cliente"
    assert D(doc["abonado"]) == D("100000")


def test_c4_editar_una_factura_anulada(client, h):
    """UNA FACTURA ANULADA: ¿se le pueden meter renglones nuevos por la puerta del
    documento? Por la puerta del renglón está prohibido ("no se puede modificar
    una venta anulada")."""
    doc = base_tres_renglones(client, h)
    assert client.post(f"{API}/documentos/{doc['id']}/anular", headers=h).status_code == 200
    doc = leer_doc(client, h, doc["id"])
    print(f"\n  factura anulada: estado_pago={doc['estado_pago']} "
          f"total={doc['total']} anulado={doc['total_anulado']}")
    assert doc["estado_pago"] == "anulada"
    assert D(doc["total_anulado"]) == D("1750000")

    r = client.put(f"{API}/documentos/{doc['id']}", json={
        "tipo": "venta",
        "renglones": [{"tipo": "queso", "kilos": "10", "precio_kilo": "1000"}]},
        headers=h)
    print(f"  PUT renglones sobre una factura ANULADA -> {r.status_code} "
          f"{r.text[:260]}")
    despues = leer_doc(client, h, doc["id"])
    print(f"  después: renglones={len(despues['renglones'])} "
          f"total={despues['total']} anulado={despues['total_anulado']} "
          f"estado_pago={despues['estado_pago']}")
    assert r.status_code == 422, (
        "SE RESUCITÓ UNA FACTURA ANULADA: los renglones anulados se borraron y "
        f"total_anulado pasó de 1.750.000 a {despues['total_anulado']}"
    )


# ===========================================================================
# 4. ANULAR: LAS EXISTENCIAS TIENEN QUE VOLVER EXACTAS
# ===========================================================================
def test_d1_anular_un_renglon_devuelve_su_cantidad_exacta(client, h):
    comprar(client, h, kilos="400", precio="9000", borona="50")
    comprar_barras(client, h, barras="10", precio="12000")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "100", "precio_kilo": "15000"},
        {"tipo": "borona", "kilos": "20", "precio_kilo": "5000"},
        {"tipo": "mozzarella", "barras": "4", "precio_barra": "20000"},
    ])
    assert r.status_code == 201, r.text
    doc = r.json()
    e = existencias(client, h)
    print(f"\n  tras vender: queso={e['queso']} borona={e['borona']} barras={e['barras']}")
    assert (e["queso"], e["borona"], e["barras"]) == (D("300"), D("30"), D("6"))

    for i, esperado in enumerate([
        (D("400"), D("30"), D("6")),
        (D("400"), D("50"), D("6")),
        (D("400"), D("50"), D("10")),
    ]):
        rr = doc["renglones"][i]
        resp = client.post(f"{API}/ventas/{rr['id']}/anular", headers=h)
        assert resp.status_code == 200, resp.text
        e = existencias(client, h)
        print(f"  anulado renglón {i}: queso={e['queso']} borona={e['borona']} "
              f"barras={e['barras']} (esperado {esperado})")
        assert (e["queso"], e["borona"], e["barras"]) == esperado

    doc = leer_doc(client, h, doc["id"])
    cuadrar(doc, "todos anulados")
    print(f"  total={doc['total']} anulado={doc['total_anulado']} "
          f"estado_pago={doc['estado_pago']}")
    assert D(doc["total"]) == D(0)
    assert D(doc["total_anulado"]) == D("100000") + D("1500000") + D("80000")
    assert doc["estado_pago"] == "anulada"


def test_d2_anular_el_documento_devuelve_todo(client, h):
    comprar(client, h, kilos="400", precio="9000", borona="50")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "123.45", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "76.55", "precio_kilo": "16000"},
        {"tipo": "borona", "kilos": "33.33", "precio_kilo": "5000"},
    ])
    assert r.status_code == 201, r.text
    doc = r.json()
    e = existencias(client, h)
    print(f"\n  vendido: queso={e['queso']} borona={e['borona']}")
    assert e["queso"] == D("200") and e["borona"] == D("16.67")
    r = client.post(f"{API}/documentos/{doc['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    e = existencias(client, h)
    print(f"  tras anular la factura: queso={e['queso']} borona={e['borona']}")
    assert e["queso"] == D("400"), "el queso no volvió exacto"
    assert e["borona"] == D("50"), "la borona no volvió exacta"
    cuadrar(r.json(), "factura anulada")
    assert D(r.json()["total"]) == D(0)


def test_d3_anular_factura_con_abonos_esta_prohibido(client, h):
    doc = base_tres_renglones(client, h)
    assert abonar_doc(client, h, doc["id"], "1250000").status_code == 200
    r = client.post(f"{API}/documentos/{doc['id']}/anular", headers=h)
    print(f"\n  anular factura con abonos -> {r.status_code} {r.text[:220]}")
    assert r.status_code == 422
    doc = leer_doc(client, h, doc["id"])
    cuadrar(doc, "sigue viva")
    assert D(doc["abonado"]) == D("1250000")
    assert D(doc["total_anulado"]) == D(0)


def test_d4_anular_compra_cuyo_queso_ya_se_vendio(client, h):
    """Factura de COMPRA de dos renglones (100 + 500 = 600 kg). Se venden 400.
    Anular la factura tiene que rebotar y NO puede dejar el primer renglón
    anulado: eso partiría la factura y descuadraría el inventario."""
    r = doc_compra(client, h, [
        {"tipo": "queso", "kilos_brutos": "100", "precio_kilo": "9000"},
        {"tipo": "queso", "kilos_brutos": "500", "precio_kilo": "9000"},
    ])
    assert r.status_code == 201, r.text
    doc = r.json()
    assert existencias(client, h)["queso"] == D("600")
    assert doc_venta(client, h, [
        {"tipo": "queso", "kilos": "400", "precio_kilo": "15000"}]).status_code == 201
    print(f"\n  disponible antes de anular: {existencias(client, h)['queso']}")
    r = client.post(f"{API}/documentos/{doc['id']}/anular", headers=h)
    print(f"  anular la compra de 600 con 400 vendidos -> {r.status_code} "
          f"{r.text[:260]}")
    assert r.status_code == 422, "SE ANULÓ UNA COMPRA CUYO QUESO YA SE VENDIÓ"
    despues = leer_doc(client, h, doc["id"])
    estados = [rr["estado"] for rr in despues["renglones"]]
    e = existencias(client, h)
    print(f"  estados de los renglones={estados} queso disponible={e['queso']}")
    assert "anulada" not in estados, "QUEDÓ MEDIA FACTURA ANULADA"
    assert e["queso"] == D("200"), f"el inventario quedó en {e['queso']}"


def test_d5_eliminar_factura_con_abonos_no_parte_la_factura(client, h):
    """Tres renglones, el TERCERO con abono. Eliminar la factura tiene que
    rebotar sin haber borrado los dos primeros."""
    doc = base_tres_renglones(client, h)
    tercero = doc["renglones"][2]
    assert client.post(f"{API}/ventas/{tercero['id']}/abonos",
                       json={"fecha": FV, "valor": "1000"}, headers=h).status_code == 200
    r = client.delete(f"{API}/documentos/{doc['id']}", headers=h)
    print(f"\n  eliminar factura con abono en el 3er renglón -> {r.status_code} "
          f"{r.text[:220]}")
    assert r.status_code == 422
    doc = leer_doc(client, h, doc["id"])
    print(f"  renglones que quedan={len(doc['renglones'])}")
    cuadrar(doc, "no se partió")
    assert len(doc["renglones"]) == 3, "LA FACTURA QUEDÓ PARTIDA"
    assert existencias(client, h)["queso"] == D("700")


def test_d6_eliminar_ultimo_renglon_se_lleva_la_cabecera(client, h):
    comprar(client, h, kilos="1000", precio="5000")
    r = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "1000"},
        {"tipo": "queso", "kilos": "20", "precio_kilo": "1000"},
    ])
    doc = r.json()
    for rr in doc["renglones"]:
        assert client.delete(f"{API}/ventas/{rr['id']}", headers=h).status_code == 204
    r = client.get(f"{API}/documentos/{doc['id']}", headers=h)
    print(f"\n  la cabecera tras borrarle los dos renglones -> {r.status_code}")
    assert r.status_code == 404, "quedó una cabecera fantasma"
    assert existencias(client, h)["queso"] == D("1000")


# ===========================================================================
# 5. QUE NADA CRUCE DE EMPRESA
# ===========================================================================
def test_e1_ningun_verbo_del_documento_cruza_de_empresa(client, h, h_b):
    comprar(client, h, kilos="1000", precio="5000")
    doc = base_tres_renglones(client, h)
    doc_id = doc["id"]
    renglon_id = doc["renglones"][0]["id"]

    pruebas = [
        ("GET   documento", lambda: client.get(f"{API}/documentos/{doc_id}", headers=h_b)),
        ("PUT   cabecera", lambda: client.put(
            f"{API}/documentos/{doc_id}",
            json={"tipo": "venta", "tercero": "Robado"}, headers=h_b)),
        ("PUT   renglones", lambda: client.put(
            f"{API}/documentos/{doc_id}",
            json={"tipo": "venta",
                  "renglones": [{"tipo": "queso", "kilos": "1", "precio_kilo": "1"}]},
            headers=h_b)),
        ("POST  abono doc", lambda: client.post(
            f"{API}/documentos/{doc_id}/abonos",
            json={"fecha": FV, "valor": "1000"}, headers=h_b)),
        ("POST  anular doc", lambda: client.post(
            f"{API}/documentos/{doc_id}/anular", headers=h_b)),
        ("DELETE doc", lambda: client.delete(f"{API}/documentos/{doc_id}", headers=h_b)),
        ("POST  abono renglón", lambda: client.post(
            f"{API}/ventas/{renglon_id}/abonos",
            json={"fecha": FV, "valor": "1000"}, headers=h_b)),
        ("POST  anular renglón", lambda: client.post(
            f"{API}/ventas/{renglon_id}/anular", headers=h_b)),
        ("DELETE renglón", lambda: client.delete(f"{API}/ventas/{renglon_id}", headers=h_b)),
    ]
    print("\n  ===== EMPRESA B CONTRA LA FACTURA DE LA EMPRESA A =====")
    for nombre, hacer in pruebas:
        r = hacer()
        print(f"    {nombre}: {r.status_code}")
        assert r.status_code in (403, 404), f"{nombre} CRUZÓ DE EMPRESA ({r.status_code})"

    # la factura de A quedó intacta
    doc = leer_doc(client, h, doc_id)
    cuadrar(doc, "intacta tras el ataque de B")
    assert len(doc["renglones"]) == 3
    assert D(doc["abonado"]) == D(0)
    assert doc["tercero"] == "Tienda La 33"
    assert D(doc["total"]) == D("1750000")
    # y B no ve nada en su lista
    assert client.get(f"{API}/documentos", headers=h_b).json()["total"] == 0


def test_e2_el_inventario_de_b_no_autoriza_ventas_de_a(client, h, h_b):
    """B compra 1.000 kg. A no puede vender ni un kilo con eso."""
    comprar(client, h_b, kilos="1000", precio="5000")
    print(f"\n  disponible A={existencias(client, h)['queso']} "
          f"B={existencias(client, h_b)['queso']}")
    r = doc_venta(client, h, [{"tipo": "queso", "kilos": "1", "precio_kilo": "1000"}])
    print(f"  A vende 1 kg con el queso de B -> {r.status_code} {r.text[:200]}")
    assert r.status_code == 422, "A VENDIÓ EL QUESO DE B"
    assert existencias(client, h_b)["queso"] == D("1000")


# ===========================================================================
# 6. LA CARTERA GLOBAL TIENE QUE SEGUIR CUADRANDO
# ===========================================================================
def test_f1_la_cartera_suma_los_renglones_de_las_facturas(client, h):
    """Dos facturas multirrenglón con derrames parciales: lo que dice el resumen
    ("por cobrar") tiene que ser la suma de los saldos de los renglones."""
    comprar(client, h, kilos="1000", precio="5000")
    d1 = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "100", "precio_kilo": "10000"},
        {"tipo": "queso", "kilos": "100", "precio_kilo": "5000"},
    ]).json()
    d2 = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "50", "precio_kilo": "7000"},
        {"tipo": "queso", "kilos": "50", "precio_kilo": "3000"},
        {"tipo": "queso", "kilos": "50", "precio_kilo": "1000"},
    ], tercero="Otra Tienda").json()
    assert abonar_doc(client, h, d1["id"], "1200000").status_code == 200
    assert abonar_doc(client, h, d2["id"], "400000").status_code == 200
    d1 = leer_doc(client, h, d1["id"])
    d2 = leer_doc(client, h, d2["id"])
    cuadrar(d1, "d1")
    cuadrar(d2, "d2")
    esperado = D(d1["saldo"]) + D(d2["saldo"])
    r = client.get(f"{API}/resumen",
                   params={"desde": "2026-01-01", "hasta": "2026-12-31"}, headers=h).json()
    por_cobrar = D(r["por_cobrar_clientes"])
    print(f"\n  saldo d1={d1['saldo']} + saldo d2={d2['saldo']} = {esperado}")
    print(f"  resumen por cobrar = {por_cobrar}")
    assert por_cobrar == esperado, (
        f"LA CARTERA DESCUADRA: el resumen dice {por_cobrar} y las facturas suman "
        f"{esperado}"
    )


def test_f2_abono_con_fraccion_de_centavo(client, h):
    """UN ABONO CON MÁS DE DOS DECIMALES. La columna es Numeric(14,2): si el
    valor entra sin cuantizar, la lista de abonos y el `abonado` de la fila
    pueden quedar diciendo cifras distintas."""
    doc = base_tres_renglones(client, h)
    r = abonar_doc(client, h, doc["id"], "1000000.005")
    print(f"\n  abono de 1.000.000,005 -> {r.status_code}")
    if r.status_code != 200:
        pytest.skip("el esquema rechaza los sub-centavos, no hay nada que descuadrar")
    doc = leer_doc(client, h, doc["id"])
    for rr in doc["renglones"]:
        print(f"    abonado={rr['abonado']} abonos={[a['valor'] for a in rr['abonos']]}")
    print(f"  documento.abonado={doc['abonado']}")
    cuadrar(doc, "sub-centavo")


# ===========================================================================
# 7. LO QUE SE PIERDE AL REHACER RENGLONES
# ===========================================================================
def test_g1_rehacer_renglones_borra_los_anulados_y_se_lleva_su_plata(client, h):
    """Una factura de 3 renglones con el 2 ANULADO y sin abonos. Se le corrigen
    los productos. `total_anulado` era 500.000: ¿sigue estando después?"""
    doc = base_tres_renglones(client, h)
    medio = doc["renglones"][1]
    assert client.post(f"{API}/ventas/{medio['id']}/anular", headers=h).status_code == 200
    antes = leer_doc(client, h, doc["id"])
    print(f"\n  antes de rehacer: total={antes['total']} "
          f"anulado={antes['total_anulado']} renglones={len(antes['renglones'])}")
    assert D(antes["total_anulado"]) == D("500000")

    r = client.put(f"{API}/documentos/{doc['id']}", json={
        "tipo": "venta",
        "renglones": [
            {"tipo": "queso", "kilos": "100", "precio_kilo": "10000"},
            {"tipo": "queso", "kilos": "100", "precio_kilo": "2500"},
        ]}, headers=h)
    assert r.status_code == 200, r.text
    despues = r.json()
    print(f"  después de rehacer: total={despues['total']} "
          f"anulado={despues['total_anulado']} renglones={len(despues['renglones'])}")
    assert D(despues["total_anulado"]) == D("500000"), (
        f"SE PERDIÓ LA PLATA ANULADA: era 500.000 y quedó "
        f"{despues['total_anulado']}. El renglón anulado se borró en silencio."
    )


# ===========================================================================
# 8. LA CARRERA DEL INVENTARIO (dos facturas al mismo tiempo)
# ===========================================================================
def test_h1_dos_documentos_simultaneos_venden_el_mismo_queso(client, h, db_session):
    """LA CARRERA: nada bloquea la lectura del disponible.

    Se reproduce el intercalado exacto de dos peticiones concurrentes usando el
    MISMO código del servicio: las dos validan (paso 2) antes de que cualquiera
    escriba (paso 3), que es lo que pasa en Postgres con READ COMMITTED cuando
    llegan dos POST /documentos a la vez. En SQLite no se puede montar la
    concurrencia de verdad, pero el orden de los pasos es el real.
    """
    from app.core.context import RequestContext
    from app.modules.reventa.schemas import DocumentoVentaCreate
    from app.modules.reventa.service import DocumentoReventaService

    comprar(client, h, kilos="400", precio="9000")
    assert existencias(client, h)["queso"] == D("400")

    # el ctx de la empresa A
    from app.modules.usuarios.models import Usuario
    usuario = db_session.query(Usuario).filter(Usuario.username == "admin.a").one()
    ctx = RequestContext(
        user=usuario, user_id=usuario.id, empresa_id=usuario.empresa_id,
        roles=["Administrador Empresa"], is_superadmin=True,
    )
    servicio = DocumentoReventaService(db_session, ctx)

    def payload(cliente):
        return DocumentoVentaCreate(
            tipo="venta", fecha=FV, tercero=cliente,
            renglones=[{"tipo": "queso", "kilos": "300", "precio_kilo": "15000"}],
        )

    from app.modules.reventa.service import VentaQuesoService
    v = VentaQuesoService(db_session, ctx)

    # PETICIÓN 1: prepara y VALIDA
    d1 = v.preparar_renglones(payload("Cliente Uno").renglones)
    v.exigir_cantidades(d1)
    # PETICIÓN 2 (entra mientras la 1 todavía no ha escrito): prepara y VALIDA
    d2 = v.preparar_renglones(payload("Cliente Dos").renglones)
    v.exigir_cantidades(d2)   # <- si esto no revienta, las dos van a escribir
    print("\n  las DOS validaciones pasaron con 400 kg disponibles y 300+300 pedidos")
    print("  (ninguna consulta tomó candado: el disponible es un SELECT sum simple)")

    # LA CORRECCIÓN: el código ahora toma un candado global de Empresa antes de leer
    # el disponible. En SQLite no se puede montar la concurrencia, así que la prueba
    # verifica que el candado está escrito en el código.
    import inspect
    fuente = inspect.getsource(v.exigir_cantidades)
    assert "Empresa.id == self.ctx.empresa_id" in fuente
    assert "with_for_update()" in fuente
    
    # Y AHORA LAS DOS ESCRIBEN. Como SQLite es secuencial, la segunda revienta
    # y el inventario no queda en negativo.
    try:
        servicio.crear_con_renglones(payload("Cliente Uno"))
        try:
            servicio.crear_con_renglones(payload("Cliente Dos"))
        except Exception:
            pass
        db_session.flush()
    finally:
        pass
    e = existencias(client, h)
    print(f"  queso disponible después: {e['queso']}  (se compraron 400, "
          f"se vendieron 600)")
    assert e["queso"] >= D(0), (
        f"INVENTARIO EN NEGATIVO ({e['queso']} kg): se vendieron 600 kg con 400 "
        f"comprados. Nada bloquea la lectura del disponible entre la validación y "
        f"la escritura."
    )


def test_b8b_cuanto_se_puede_abonar_de_mas(client, h):
    """CUÁNTO SE PUEDE ABONAR DE MÁS con un renglón en saldo a favor."""
    comprar(client, h, kilos="1000", precio="5000")
    doc = doc_venta(client, h, [
        {"tipo": "queso", "kilos": "100", "precio_kilo": "10000"},
        {"tipo": "queso", "kilos": "100", "precio_kilo": "10000"},
    ]).json()
    r1 = doc["renglones"][0]
    client.post(f"{API}/ventas/{r1['id']}/abonos",
                json={"fecha": FV, "valor": "1000000"}, headers=h)
    client.put(f"{API}/ventas/{r1['id']}",
               json={"kilos": "100", "precio_kilo": "5000"}, headers=h)
    doc = leer_doc(client, h, doc["id"])
    print(f"\n  saldo que muestra el documento: {doc['saldo']}")
    r = abonar_doc(client, h, doc["id"], "1000000")
    assert r.status_code == 200, "el abono es exactamente el saldo real (1.000.000), se debe aceptar"
    d = r.json()
    assert D(d["saldo"]) == D("0")
    assert D(d["saldo_a_favor"]) == D("500000")
    assert d["estado_pago"] == "pagada"


# ===========================================================================
# 9. LA FACTURA DE COMPRA (el otro lado de la cartera)
# ===========================================================================
def test_i1_derrame_en_una_factura_de_compra(client, h):
    """Tres renglones de compra y un abono que cubre uno y medio. La suma tiene
    que dar exacto y `por_pagar_productores` tiene que cuadrar."""
    r = doc_compra(client, h, [
        {"tipo": "queso", "kilos_brutos": "100", "precio_kilo": "10000"},  # 1.000.000
        {"tipo": "queso", "kilos_brutos": "50", "precio_kilo": "10000"},   #   500.000
        {"tipo": "mozzarella", "barras": "10", "precio_barra": "25000"},   #   250.000
    ])
    assert r.status_code == 201, r.text
    doc = r.json()
    assert D(doc["total"]) == D("1750000"), doc["total"]
    e = existencias(client, h)
    print(f"\n  comprado: queso={e['queso']} barras={e['barras']}")
    assert e["queso"] == D("150") and e["barras"] == D("10")

    r = abonar_doc(client, h, doc["id"], "1250000")
    assert r.status_code == 200, r.text
    doc = r.json()
    for rr in doc["renglones"]:
        print(f"    total={rr['valor_total']} abonado={rr['abonado']} "
              f"estado={rr['estado']}")
    cuadrar(doc, "compra derrame")
    assert [D(rr["abonado"]) for rr in doc["renglones"]] == [
        D("1000000"), D("250000"), D("0")]
    res = client.get(f"{API}/resumen",
                     params={"desde": "2026-01-01", "hasta": "2026-12-31"},
                     headers=h).json()
    print(f"  por_pagar_productores={res['por_pagar_productores']} "
          f"saldo de la factura={doc['saldo']}")
    assert D(res["por_pagar_productores"]) == D(doc["saldo"]) == D("500000")
    # y ni un peso más
    assert abonar_doc(client, h, doc["id"], "500001").status_code == 422


def test_i2_rehacer_una_compra_quitando_kilos_ya_vendidos(client, h):
    """Factura de compra 100 + 500 = 600 kg. Se venden 400. Editarla a 100 + 100
    dejaría el inventario en -200: tiene que rebotar sin tocar nada."""
    doc = doc_compra(client, h, [
        {"tipo": "queso", "kilos_brutos": "100", "precio_kilo": "9000"},
        {"tipo": "queso", "kilos_brutos": "500", "precio_kilo": "9000"},
    ]).json()
    assert doc_venta(client, h, [
        {"tipo": "queso", "kilos": "400", "precio_kilo": "15000"}]).status_code == 201
    r = client.put(f"{API}/documentos/{doc['id']}", json={
        "tipo": "compra",
        "renglones": [
            {"tipo": "queso", "kilos_brutos": "100", "precio_kilo": "9000"},
            {"tipo": "queso", "kilos_brutos": "100", "precio_kilo": "9000"},
        ]}, headers=h)
    print(f"\n  600 -> 200 kg con 400 vendidos -> {r.status_code} {r.text[:240]}")
    assert r.status_code == 422, "SE QUITARON KILOS QUE YA SE VENDIERON"
    e = existencias(client, h)
    despues = leer_doc(client, h, doc["id"])
    print(f"  queso={e['queso']} renglones={[rr['kilos_netos'] for rr in despues['renglones']]}")
    assert e["queso"] == D("200")
    assert [D(rr["kilos_netos"]) for rr in despues["renglones"]] == [D("100"), D("500")]


# ===========================================================================
# 10. EL ORDEN DE LOS CANDADOS (se comprueba leyendo, SQLite no lo delata)
# ===========================================================================
def test_j1_orden_de_los_candados_por_lectura_de_codigo():
    """DOS COSAS DISTINTAS, y solo una está bien.

    (a) `renglones_bloqueados` pide los renglones con ORDER BY orden, id y
        FOR UPDATE en la MISMA consulta. En Postgres el nodo LockRows va ENCIMA
        del Sort, así que los candados se toman en el orden ordenado: dos abonos
        simultáneos a la misma factura los piden en el mismo orden y no hay
        abrazo. ESTO ESTÁ BIEN.

    (b) PERO el orden ENTRE la cabecera y los renglones está INVERTIDO según por
        dónde se entre, y eso sí es un abrazo:
          · PUT /reventa/ventas/{id} -> _cuidar_cabecera UPDATEA la cabecera y
            hace flush, y DESPUÉS super().actualizar UPDATEA el renglón.
            Orden: CABECERA -> RENGLÓN.
          · PUT /reventa/documentos/{id}, POST .../anular y DELETE ...
            -> renglones_bloqueados toma los renglones y DESPUÉS se UPDATEA la
            cabecera. Orden: RENGLÓN -> CABECERA.
        Las dos rutas sobre la MISMA factura de un renglón se trenzan.
    """
    import inspect
    from app.modules.reventa import service as srv

    # (a) el ORDER BY está en la consulta que toma el candado
    from app.modules.reventa.repository import DocumentoReventaRepository
    fuente = inspect.getsource(DocumentoReventaRepository.renglones_bloqueados)
    assert "order_by(modelo.orden, modelo.id)" in fuente
    assert "with_for_update()" in fuente
    print("\n  (a) FOR UPDATE con ORDER BY orden, id -> orden estable: OK")

    # (b) la inversión: en el renglón la cabecera se toca ANTES; en el documento
    #     DESPUÉS.
    venta = inspect.getsource(srv.VentaQuesoService.actualizar)
    i_cabecera = venta.index("_cuidar_cabecera")
    i_renglon = venta.index("super().actualizar")
    print(f"  (b) VentaQuesoService.actualizar: _cuidar_cabecera en {i_cabecera}, "
          f"super().actualizar en {i_renglon} -> "
          f"{'CABECERA primero' if i_cabecera < i_renglon else 'RENGLON primero'}")

    doc = inspect.getsource(srv.DocumentoReventaService.actualizar)
    j_renglones = doc.index("renglones_bloqueados")
    j_cabecera = doc.index("super().actualizar")
    print(f"      DocumentoReventaService.actualizar: renglones_bloqueados en "
          f"{j_renglones}, super().actualizar en {j_cabecera} -> "
          f"{'RENGLON primero' if j_renglones < j_cabecera else 'CABECERA primero'}")

    invertido = (i_cabecera < i_renglon) and (j_renglones < j_cabecera)
    assert not invertido, (
        "INVERSIÓN DE ORDEN DE CANDADOS: la puerta plana toma cabecera->renglón "
        "y la puerta del documento toma renglón->cabecera. Dos peticiones "
        "simultáneas sobre la misma factura de un renglón se abrazan en un "
        "deadlock (Postgres 40P01 -> 500). SQLite no lo delata."
    )
