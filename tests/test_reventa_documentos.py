"""Las facturas de reventa: varios productos en una sola compra o venta.

Aquí se prueba el CONTRATO y los guardias que no son opcionales:

  · las existencias se validan SUMANDO los renglones del mismo producto y sobre la
    factura completa ANTES de escribir la primera fila;
  · el abono a la factura se DERRAMA (no se divide) y la suma da exacta;
  · editar los productos de una factura CON abonos está prohibido, y SIN abonos
    revalida las existencias contra el conjunto nuevo completo;
  · el payload plano de un solo producto sigue dando exactamente lo mismo;
  · y nada se cruza de empresa.

La NEUTRALIDAD —que una factura de tres renglones dé las mismas cifras que tres
ventas sueltas— vive en test_reventa_documentos_neutralidad.py, que es la que
importa de verdad.
"""
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"

FECHA_COMPRA = "2026-07-01"
FECHA_VENTA = "2026-07-10"


def D(v):
    return Decimal(str(v))


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


@pytest.fixture()
def h_b(client, base_datos):
    return auth_headers(client, "admin.b")


def comprar(client, h, *, kilos="1000", precio="10000", borona="0", productor="Yeferson"):
    r = client.post(
        f"{API}/compras",
        json={"fecha": FECHA_COMPRA, "productor": productor, "kilos_brutos": kilos,
              "borona_kilos": borona, "precio_kilo": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def comprar_barras(client, h, *, barras="100", precio="12000"):
    r = client.post(
        f"{API}/compras",
        json={"fecha": FECHA_COMPRA, "productor": "Marlion", "tipo": "mozzarella",
              "barras": barras, "precio_barra": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def factura_venta(client, h, renglones, **extra):
    return client.post(
        f"{API}/documentos",
        json={"tipo": "venta", "fecha": FECHA_VENTA, "tercero": "Tienda La 33",
              "renglones": renglones, **extra},
        headers=h,
    )


# ---------------------------------------------------------------------------
# 1. Las existencias: el mismo producto repetido en dos renglones SE SUMA
# ---------------------------------------------------------------------------
def test_dos_renglones_del_mismo_producto_se_suman_contra_el_disponible(client, h):
    """EL HUECO QUE LOS DOCUMENTOS PODÍAN ABRIR, y por eso esta prueba existe.

    Con 400 kg en bodega, dos renglones de 300 kg pasan uno por uno —cada 300 es
    menor que 400— y la factura despacharía 600 kg que no existen. El inventario
    quedaría en -200, y con el inventario en negativo NINGUNA venta vuelve a pasar
    el control: el dueño se queda sin poder trabajar sin entender por qué.
    """
    comprar(client, h, kilos="400", precio="9000")

    r = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "300", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "300", "precio_kilo": "15000"},
    ])
    print("\n===== 600 kg CONTRA 400 DISPONIBLES =====")
    print(f"  {r.status_code}: {r.json()}")
    assert r.status_code == 422, "¡pasaron 600 kg contra 400 disponibles!"
    detalle = r.json()["error"]["detail"]
    assert "disponible" in detalle
    # El mensaje tiene que decir que la cuenta es la SUMA: si no, el usuario ve
    # "solo hay 400" al lado de un renglón de 300 y cree que el sistema se equivocó.
    assert "600" in detalle and "renglones" in detalle, detalle

    # Y NO SE ESCRIBIÓ NADA: ni la cabecera ni el primer renglón.
    r = client.get(f"{API}/documentos", params={"tipo": "venta"}, headers=h)
    assert r.json()["total"] == 0, "quedó una factura a medias"
    res = client.get(
        f"{API}/resumen", params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=h
    ).json()
    print(f"  kilos disponibles después del rechazo: {res['kilos_disponibles']}")
    assert D(res["kilos_disponibles"]) == D("400"), "el inventario se movió"
    assert D(res["total_ventas"]) == D(0)


def test_dos_renglones_que_si_caben_pasan(client, h):
    """250 + 150 = 400 contra 400 disponibles: cabe justo, y tiene que pasar."""
    comprar(client, h, kilos="400", precio="9000")
    r = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "250", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "150", "precio_kilo": "16000"},
    ])
    assert r.status_code == 201, r.text
    factura = r.json()
    # 250 × 15.000 = 3.750.000 y 150 × 16.000 = 2.400.000 -> 6.150.000
    print(f"\n  total de la factura: {factura['total']}  (a mano: 6150000.00)")
    assert D(factura["total"]) == D("6150000.00")
    res = client.get(
        f"{API}/resumen", params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=h
    ).json()
    assert D(res["kilos_disponibles"]) == D(0)


def test_cada_inventario_se_mira_con_el_suyo(client, h):
    """Tener kilos de queso no autoriza a despachar barras que no se compraron."""
    comprar(client, h, kilos="1000", precio="9000")
    r = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "mozzarella", "barras": "3", "precio_barra": "20000"},
    ])
    print("\n===== BARRAS SIN HABER COMPRADO MOZZARELLA =====")
    print(f"  {r.status_code}: {r.json()}")
    assert r.status_code == 422
    assert "mozzarella" in r.json()["error"]["detail"]


def test_la_borona_de_la_factura_tambien_tiene_su_guardia(client, h):
    comprar(client, h, kilos="100", precio="9000", borona="5")
    r = factura_venta(client, h, [
        {"tipo": "borona", "kilos": "3", "precio_kilo": "4000"},
        {"tipo": "borona", "kilos": "3", "precio_kilo": "4000"},
    ])
    print("\n===== 6 kg DE BORONA CONTRA 5 DISPONIBLES =====")
    print(f"  {r.status_code}: {r.json()}")
    assert r.status_code == 422
    assert "borona" in r.json()["error"]["detail"]


# ---------------------------------------------------------------------------
# 2. El derrame del abono
# ---------------------------------------------------------------------------
def test_el_abono_se_derrama_en_orden_y_suma_exacto(client, h):
    """El abono NO SE DIVIDE: llena el primer renglón, después el segundo.

    Tres renglones de 3 kg × $111.111 = $333.333 cada uno (total $999.999, un peso
    impar a propósito) y un abono de $500.000:
      · al primero le entran sus $333.333 completos,
      · al segundo le entran los $166.667 que quedaban,
      · al tercero nada.
    Suma: 333.333 + 166.667 = 500.000 EXACTO, sin un centavo acomodado. Un reparto
    proporcional entre tres partiría los $500.000 en $166.666,33 tres veces —que
    suman $499.998,99— y habría que "acomodar" el peso que falta en alguna: ahí
    nace el descuadre que el dueño ve al sumar la columna.
    """
    comprar(client, h, kilos="1000", precio="9000")
    r = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "3", "precio_kilo": "111111"},   # 333.333
        {"tipo": "queso", "kilos": "3", "precio_kilo": "111111"},   # 333.333
        {"tipo": "queso", "kilos": "3", "precio_kilo": "111111"},   # 333.333
    ])
    assert r.status_code == 201, r.text
    factura = r.json()
    assert D(factura["total"]) == D("999999.00")

    r = client.post(
        f"{API}/documentos/{factura['id']}/abonos",
        json={"fecha": FECHA_VENTA, "valor": "500000"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    factura = r.json()
    print("\n===== EL DERRAME =====")
    esperado = [D("333333.00"), D("166667.00"), D(0)]
    for renglon, cuota in zip(factura["renglones"], esperado):
        print(f"  renglon {renglon['orden']}: valor {renglon['valor_total']:>10} "
              f"abonado {renglon['abonado']:>10}  {renglon['estado']}  "
              f"abonos={[a['valor'] for a in renglon['abonos']]}")
        assert D(renglon["abonado"]) == cuota
        # Una sola cifra entera por renglón, señalable con el dedo.
        assert len(renglon["abonos"]) == (1 if cuota else 0)
    assert D(factura["abonado"]) == D("500000.00")
    assert D(factura["saldo"]) == D("499999.00")
    assert factura["estado_pago"] == "parcial"


def test_el_derrame_tambien_funciona_pagandole_a_un_productor(client, h):
    """El otro lado del negocio: se le abona a la FACTURA DE COMPRA del productor.

    No es el mismo camino que la venta: el abono se guarda en `abonos_compra_queso`
    y cuelga de `compra_id`. Si esa pieza estuviera cruzada, la plata del productor
    se registraría contra una venta.

    Cifras: 77,77 kg × $10.333 = $803.597,41 y 12 barras × $11.317 = $135.804,00
    (total $939.401,41). Un abono de $850.000 se derrama así:
      · al renglón del queso le entran sus $803.597,41 completos,
      · al de la mozzarella le entran los $46.402,59 que quedaban.
      Suma: 803.597,41 + 46.402,59 = $850.000,00 EXACTO.
    """
    factura = client.post(
        f"{API}/documentos",
        json={"tipo": "compra", "fecha": FECHA_COMPRA, "tercero": "Yubigildo",
              "renglones": [
                  {"tipo": "queso", "kilos_brutos": "77.77", "precio_kilo": "10333"},
                  {"tipo": "mozzarella", "barras": "12", "precio_barra": "11317"},
              ]},
        headers=h,
    ).json()
    assert D(factura["total"]) == D("939401.41")

    r = client.post(
        f"{API}/documentos/{factura['id']}/abonos",
        json={"fecha": FECHA_COMPRA, "valor": "850000", "observaciones": "transferencia"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    factura = r.json()
    print("\n===== ABONO A UNA FACTURA DE COMPRA =====")
    esperado = [D("803597.41"), D("46402.59")]
    for renglon, cuota in zip(factura["renglones"], esperado):
        print(f"  renglon {renglon['orden']} {renglon['tipo']:11} "
              f"valor {renglon['valor_total']:>12} abonado {renglon['abonado']:>12} "
              f"{renglon['estado']}")
        assert D(renglon["abonado"]) == cuota
        assert [a["observaciones"] for a in renglon["abonos"]] == ["transferencia"]
    assert D(factura["abonado"]) == D("850000.00")
    assert D(factura["saldo"]) == D("89401.41")
    assert factura["estado_pago"] == "parcial"

    # Y la cartera del productor se mueve con ella, ni un peso más.
    res = client.get(
        f"{API}/resumen", params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=h
    ).json()
    print(f"  por pagar a productores: {res['por_pagar_productores']}")
    assert D(res["por_pagar_productores"]) == D("89401.41")


def test_el_abono_no_puede_pasarse_del_saldo_de_la_factura(client, h):
    comprar(client, h, kilos="1000", precio="9000")
    r = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
    ])
    factura = r.json()
    assert D(factura["total"]) == D("300000.00")
    r = client.post(
        f"{API}/documentos/{factura['id']}/abonos",
        json={"fecha": FECHA_VENTA, "valor": "300001"},
        headers=h,
    )
    print("\n===== UN ABONO DE MÁS =====")
    print(f"  {r.status_code}: {r.json()}")
    assert r.status_code == 422
    assert "supera el saldo" in r.json()["error"]["detail"]
    # Y no quedó ni un abono a medias en ningún renglón.
    factura = client.get(f"{API}/documentos/{factura['id']}", headers=h).json()
    assert D(factura["abonado"]) == D(0)
    assert all(not g["abonos"] for g in factura["renglones"])


def test_el_derrame_llena_toda_la_factura_y_la_deja_pagada(client, h):
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "7.77", "precio_kilo": "13333"},
        {"tipo": "queso", "kilos": "3.33", "precio_kilo": "11317"},
    ]).json()
    total = D(factura["total"])
    r = client.post(
        f"{API}/documentos/{factura['id']}/abonos",
        json={"fecha": FECHA_VENTA, "valor": str(total)},
        headers=h,
    )
    assert r.status_code == 200, r.text
    factura = r.json()
    print(f"\n  total {total} abonado {factura['abonado']} -> {factura['estado_pago']}")
    assert D(factura["abonado"]) == total
    assert D(factura["saldo"]) == D(0)
    assert factura["estado_pago"] == "pagada"
    assert all(g["estado"] == "pagada" for g in factura["renglones"])


def test_el_abono_por_renglon_sigue_funcionando_igual(client, h):
    """La puerta de siempre: abonarle a UN producto de la factura."""
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "16000"},
    ]).json()
    segundo = factura["renglones"][1]
    r = client.post(
        f"{API}/ventas/{segundo['id']}/abonos",
        json={"fecha": FECHA_VENTA, "valor": "60000"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    factura = client.get(f"{API}/documentos/{factura['id']}", headers=h).json()
    print("\n===== ABONO A UN SOLO RENGLÓN =====")
    for g in factura["renglones"]:
        print(f"  renglon {g['orden']}: abonado {g['abonado']} {g['estado']}")
    assert D(factura["renglones"][0]["abonado"]) == D(0)
    assert D(factura["renglones"][1]["abonado"]) == D("60000.00")
    assert D(factura["abonado"]) == D("60000.00")
    assert factura["estado_pago"] == "parcial"


def test_pagada_de_contado_paga_toda_la_factura(client, h):
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(
        client, h,
        [{"tipo": "queso", "kilos": "9.99", "precio_kilo": "15333"},
         {"tipo": "queso", "kilos": "1.01", "precio_kilo": "16777"}],
        pagada_de_contado=True,
    ).json()
    print(f"\n  de contado: total {factura['total']} abonado {factura['abonado']}")
    assert D(factura["abonado"]) == D(factura["total"])
    assert factura["estado_pago"] == "pagada"
    for g in factura["renglones"]:
        assert [a["observaciones"] for a in g["abonos"]] == ["Pago de contado"]
        assert D(g["abonado"]) == D(g["valor_total"])


# ---------------------------------------------------------------------------
# 3. Editar la factura
# ---------------------------------------------------------------------------
def test_no_se_cambian_los_productos_de_una_factura_con_abonos(client, h):
    """El candado: con plata encima hay que anular y rehacer.

    Rehacer los renglones es borrarlos y volverlos a escribir, y los abonos cuelgan
    de los renglones: se irían con ellos, o habría que re-derramarlos sobre
    productos distintos de los que el dueño vio cuando recibió la plata. Es el
    mismo criterio que ya tienen las ventas propias.
    """
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "16000"},
    ]).json()
    r = client.post(
        f"{API}/documentos/{factura['id']}/abonos",
        json={"fecha": FECHA_VENTA, "valor": "50000"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    r = client.put(
        f"{API}/documentos/{factura['id']}",
        json={"tipo": "venta", "renglones": [
            {"tipo": "queso", "kilos": "5", "precio_kilo": "15000"},
        ]},
        headers=h,
    )
    print("\n===== CAMBIAR PRODUCTOS CON ABONOS =====")
    print(f"  {r.status_code}: {r.json()}")
    assert r.status_code == 422
    assert "anularla y rehacerla" in r.json()["error"]["detail"]
    # Los dos renglones siguen ahí, con su plata intacta.
    factura = client.get(f"{API}/documentos/{factura['id']}", headers=h).json()
    assert factura["cantidad_renglones"] == 2
    assert D(factura["abonado"]) == D("50000.00")


def test_la_cabecera_si_se_corrige_aunque_tenga_abonos(client, h):
    """La fecha, el nombre y la nota se pueden arreglar siempre, y se les COPIAN a
    los renglones: el resumen y la cartera leen la fecha y el nombre DEL RENGLÓN."""
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "16000"},
    ]).json()
    client.post(
        f"{API}/documentos/{factura['id']}/abonos",
        json={"fecha": FECHA_VENTA, "valor": "50000"}, headers=h,
    )
    r = client.put(
        f"{API}/documentos/{factura['id']}",
        json={"tipo": "venta", "fecha": "2026-07-15", "tercero": "Doña Rosa",
              "observaciones": "corregida"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    factura = r.json()
    print("\n===== LA CABECERA CORREGIDA SE COPIA A LOS RENGLONES =====")
    print(f"  cabecera: {factura['fecha']} · {factura['tercero']} · {factura['observaciones']}")
    for g in factura["renglones"]:
        print(f"  renglon {g['orden']}: {g['fecha']} · {g['cliente']}")
        assert g["fecha"] == "2026-07-15"
        assert g["cliente"] == "Doña Rosa"
    # Y la cartera se mudó con ella: el estado de cuenta viejo queda vacío.
    cta = client.get(f"{API}/estado-cuenta", params={"cliente": "Doña Rosa"}, headers=h).json()
    print(f"  estado de cuenta de Doña Rosa: facturado {cta['total_facturado']}")
    assert D(cta["total_facturado"]) == D(factura["total"])


def test_rehacer_los_renglones_sin_abonos_revalida_las_existencias(client, h):
    comprar(client, h, kilos="400", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "100", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "100", "precio_kilo": "15000"},
    ]).json()

    # Editar a 200 + 200 = 400: cabe justo, porque los 200 que se van vuelven al
    # inventario antes de comparar.
    r = client.put(
        f"{API}/documentos/{factura['id']}",
        json={"tipo": "venta", "renglones": [
            {"tipo": "queso", "kilos": "200", "precio_kilo": "15000"},
            {"tipo": "queso", "kilos": "200", "precio_kilo": "15000"},
        ]},
        headers=h,
    )
    print("\n===== REHACER LOS RENGLONES =====")
    print(f"  a 200+200 contra 400 comprados: {r.status_code}")
    assert r.status_code == 200, r.text
    factura = r.json()
    assert factura["cantidad_renglones"] == 2
    assert D(factura["total"]) == D("6000000.00")

    # Y a 300 + 300 = 600 NO cabe, aunque los 400 viejos vuelvan al inventario.
    r = client.put(
        f"{API}/documentos/{factura['id']}",
        json={"tipo": "venta", "renglones": [
            {"tipo": "queso", "kilos": "300", "precio_kilo": "15000"},
            {"tipo": "queso", "kilos": "300", "precio_kilo": "15000"},
        ]},
        headers=h,
    )
    print(f"  a 300+300 contra 400 comprados: {r.status_code} {r.json()}")
    assert r.status_code == 422
    # Y la factura quedó como estaba, no a medias.
    factura = client.get(f"{API}/documentos/{factura['id']}", headers=h).json()
    assert D(factura["total"]) == D("6000000.00")
    assert factura["cantidad_renglones"] == 2


def test_rehacer_los_renglones_cambia_cuantos_hay(client, h):
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "16000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "17000"},
    ]).json()
    ids_viejos = {g["id"] for g in factura["renglones"]}

    r = client.put(
        f"{API}/documentos/{factura['id']}",
        json={"tipo": "venta", "renglones": [
            {"tipo": "queso", "kilos": "7.77", "precio_kilo": "15333"},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    factura = r.json()
    print("\n===== DE TRES RENGLONES A UNO =====")
    print(f"  quedan {factura['cantidad_renglones']} · total {factura['total']}")
    assert factura["cantidad_renglones"] == 1
    assert D(factura["total"]) == (D("7.77") * D("15333")).quantize(D("0.01"))
    # Los viejos se fueron: ya no salen en la lista de ventas ni suman en el resumen.
    ventas = client.get(f"{API}/ventas", headers=h).json()["items"]
    assert not (ids_viejos & {v["id"] for v in ventas})
    res = client.get(
        f"{API}/resumen", params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=h
    ).json()
    print(f"  total de ventas en el resumen: {res['total_ventas']}")
    assert D(res["total_ventas"]) == D(factura["total"])


def test_no_se_convierte_una_compra_en_venta(client, h):
    factura = client.post(
        f"{API}/documentos",
        json={"tipo": "compra", "fecha": FECHA_COMPRA, "tercero": "Yeferson",
              "renglones": [{"tipo": "queso", "kilos_brutos": "10", "precio_kilo": "9000"}]},
        headers=h,
    ).json()
    r = client.put(
        f"{API}/documentos/{factura['id']}",
        json={"tipo": "venta", "fecha": FECHA_VENTA},
        headers=h,
    )
    print(f"\n  convertir compra en venta: {r.status_code} {r.json()}")
    assert r.status_code == 422
    assert "anule la factura" in r.json()["error"]["detail"]


def test_un_renglon_de_una_factura_de_varios_no_cambia_de_fecha_por_su_cuenta(client, h):
    """Cambiarle la fecha a UN renglón partiría la factura en dos fechas: la
    pantalla de facturas mostraría una y el resumen del día la otra."""
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "16000"},
    ]).json()
    primero = factura["renglones"][0]
    r = client.put(
        f"{API}/ventas/{primero['id']}",
        json={"fecha": "2026-07-20", "kilos": "10", "precio_kilo": "15000"},
        headers=h,
    )
    print("\n===== CAMBIARLE LA FECHA A UN SOLO RENGLÓN =====")
    print(f"  {r.status_code}: {r.json()}")
    assert r.status_code == 422
    assert "se cambian en la factura" in r.json()["error"]["detail"]

    # Pero la CANTIDAD y el PRECIO de ese renglón sí se pueden corregir.
    r = client.put(
        f"{API}/ventas/{primero['id']}",
        json={"fecha": FECHA_VENTA, "kilos": "12", "precio_kilo": "15500"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    print(f"  corregir cantidad y precio: {r.json()['valor_total']}")
    assert D(r.json()["valor_total"]) == D("186000.00")


# ---------------------------------------------------------------------------
# 4. Anular y eliminar
# ---------------------------------------------------------------------------
def test_anular_la_factura_anula_todos_sus_renglones(client, h):
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "16000"},
    ]).json()
    r = client.post(f"{API}/documentos/{factura['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    factura = r.json()
    print("\n===== FACTURA ANULADA =====")
    print(f"  total {factura['total']} · anulado {factura['total_anulado']} "
          f"· estado {factura['estado_pago']}")
    assert factura["estado_pago"] == "anulada"
    # La plata NO desaparece de la vista: se va a `total_anulado`, y la igualdad
    # `total + total_anulado == suma de los renglones` sigue cerrando.
    assert D(factura["total"]) == D(0)
    assert D(factura["total_anulado"]) == D("310000.00")
    suma = sum((D(g["valor_total"]) for g in factura["renglones"]), D(0))
    assert suma == D(factura["total"]) + D(factura["total_anulado"])
    # Y no sigue sumando en el negocio.
    res = client.get(
        f"{API}/resumen", params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=h
    ).json()
    assert D(res["total_ventas"]) == D(0)
    assert D(res["kilos_disponibles"]) == D("1000")


def test_un_renglon_anulado_deja_la_cuenta_cerrando(client, h):
    """Se anula UN renglón de tres: el desglose tiene que seguir sumando."""
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "16000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "17000"},
    ]).json()
    r = client.post(f"{API}/ventas/{factura['renglones'][1]['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    factura = client.get(f"{API}/documentos/{factura['id']}", headers=h).json()
    print("\n===== UN RENGLÓN ANULADO DE TRES =====")
    print(f"  total {factura['total']} · anulado {factura['total_anulado']} "
          f"· renglones {factura['cantidad_renglones']}")
    assert D(factura["total"]) == D("320000.00")       # 150.000 + 170.000
    assert D(factura["total_anulado"]) == D("160000.00")
    suma = sum((D(g["valor_total"]) for g in factura["renglones"]), D(0))
    assert suma == D(factura["total"]) + D(factura["total_anulado"]), (
        "el desglose de la factura dejó de sumar la cifra grande"
    )


def test_no_se_anula_ni_se_elimina_una_factura_con_abonos(client, h):
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
    ]).json()
    client.post(
        f"{API}/documentos/{factura['id']}/abonos",
        json={"fecha": FECHA_VENTA, "valor": "1000"}, headers=h,
    )
    r = client.post(f"{API}/documentos/{factura['id']}/anular", headers=h)
    print(f"\n  anular con abonos: {r.status_code} {r.json()}")
    assert r.status_code == 422
    r = client.delete(f"{API}/documentos/{factura['id']}", headers=h)
    print(f"  eliminar con abonos: {r.status_code} {r.json()}")
    assert r.status_code == 422


def test_eliminar_la_factura_se_lleva_sus_renglones(client, h):
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "16000"},
    ]).json()
    r = client.delete(f"{API}/documentos/{factura['id']}", headers=h)
    assert r.status_code == 204, r.text
    assert client.get(f"{API}/documentos/{factura['id']}", headers=h).status_code == 404
    ventas = client.get(f"{API}/ventas", headers=h).json()
    print(f"\n  ventas que quedan: {ventas['total']}")
    assert ventas["total"] == 0
    res = client.get(
        f"{API}/resumen", params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=h
    ).json()
    assert D(res["total_ventas"]) == D(0)
    assert D(res["kilos_disponibles"]) == D("1000")


def test_borrar_el_ultimo_renglon_se_lleva_la_factura(client, h):
    """SIN ESTO LA PANTALLA DE FACTURAS SE LLENA DE FANTASMAS.

    Borrar una compra por la pantalla de siempre dejaba su cabecera viva y vacía:
    una factura con total cero que el dueño no puede abrir ni entender ni quitar. Y
    pasaría todo el tiempo, porque toda compra suelta es una factura de un renglón.
    """
    compra = comprar(client, h, kilos="50", precio="9877")
    documento_id = compra["documento_id"]
    assert client.get(f"{API}/documentos/{documento_id}", headers=h).status_code == 200

    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    assert r.status_code == 204, r.text
    r = client.get(f"{API}/documentos/{documento_id}", headers=h)
    print(f"\n  la cabecera después de borrar su único renglón: {r.status_code}")
    assert r.status_code == 404, "quedó una factura fantasma"
    lista = client.get(f"{API}/documentos", headers=h).json()
    print(f"  facturas en la lista: {lista['total']}")
    assert lista["total"] == 0


def test_borrar_un_renglon_de_varios_deja_la_factura_viva(client, h):
    """Y al contrario: si quedan hermanos, la factura sigue —con un renglón menos."""
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "10", "precio_kilo": "16000"},
    ]).json()
    r = client.delete(f"{API}/ventas/{factura['renglones'][0]['id']}", headers=h)
    assert r.status_code == 204, r.text
    factura = client.get(f"{API}/documentos/{factura['id']}", headers=h).json()
    print(f"\n  quedan {factura['cantidad_renglones']} renglones · total {factura['total']}")
    assert factura["cantidad_renglones"] == 1
    assert D(factura["total"]) == D("160000.00")


def test_una_factura_con_todo_anulado_no_se_borra(client, h):
    """Anular NO es borrar: el dueño tiene que poder abrirla y ver qué anuló, y su
    plata sigue saliendo en `total_anulado` para que la cuenta cierre."""
    compra = comprar(client, h, kilos="50", precio="9877")
    r = client.post(f"{API}/compras/{compra['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    factura = client.get(f"{API}/documentos/{compra['documento_id']}", headers=h)
    print(f"\n  la factura anulada sigue ahí: {factura.status_code}")
    assert factura.status_code == 200
    factura = factura.json()
    assert factura["estado_pago"] == "anulada"
    assert D(factura["total"]) == D(0)
    assert D(factura["total_anulado"]) == D(compra["valor_total"])


def test_no_se_anula_una_factura_de_compra_cuyo_queso_ya_se_vendio(client, h):
    """El guardia de la compra sigue vivo renglón por renglón: anularla dejaría el
    inventario en negativo y desde ahí ninguna venta pasaría el control."""
    factura = client.post(
        f"{API}/documentos",
        json={"tipo": "compra", "fecha": FECHA_COMPRA, "tercero": "Yeferson",
              "renglones": [
                  {"tipo": "queso", "kilos_brutos": "100", "precio_kilo": "9000"},
                  {"tipo": "queso", "kilos_brutos": "50", "precio_kilo": "9500"},
              ]},
        headers=h,
    ).json()
    r = client.post(
        f"{API}/ventas",
        json={"fecha": FECHA_VENTA, "cliente": "Tienda", "kilos": "120",
              "precio_kilo": "15000"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/documentos/{factura['id']}/anular", headers=h)
    print(f"\n  anular la compra ya vendida: {r.status_code} {r.json()}")
    assert r.status_code == 422
    assert "ya se vendió" in r.json()["error"]["detail"]


# ---------------------------------------------------------------------------
# 5. El payload plano sigue dando lo mismo
# ---------------------------------------------------------------------------
def test_el_payload_plano_arma_una_factura_de_un_renglon(client, h):
    """La puerta plana no cambió por fuera, y por dentro es una factura de uno."""
    comprar(client, h, kilos="1000", precio="9000")
    r = client.post(
        f"{API}/ventas",
        json={"fecha": FECHA_VENTA, "cliente": "Tienda La 33", "kilos": "33.33",
              "precio_kilo": "13333", "observaciones": "la de siempre"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    venta = r.json()
    esperado = (D("33.33") * D("13333")).quantize(D("0.01"))
    print("\n===== EL PAYLOAD PLANO =====")
    print(f"  33,33 kg × $13.333 = {venta['valor_total']}  (a mano: {esperado})")
    assert D(venta["valor_total"]) == esperado
    assert venta["documento_id"], "la venta plana quedó sin factura"
    assert venta["orden"] == 0

    factura = client.get(f"{API}/documentos/{venta['documento_id']}", headers=h).json()
    print(f"  la factura: {factura['tipo']} · {factura['tercero']} · {factura['total']} "
          f"· {factura['cantidad_renglones']} renglon(es)")
    assert factura["cantidad_renglones"] == 1
    assert D(factura["total"]) == esperado
    assert factura["tercero"] == "Tienda La 33"
    assert factura["fecha"] == FECHA_VENTA
    # La nota del producto es también la de la factura de un solo producto.
    assert factura["observaciones"] == "la de siempre"
    assert factura["renglones"][0]["id"] == venta["id"]


def test_la_compra_plana_tambien_y_se_le_puede_corregir_la_fecha(client, h):
    compra = comprar(client, h, kilos="50", precio="9877")
    assert compra["documento_id"]
    r = client.put(
        f"{API}/compras/{compra['id']}",
        json={"fecha": "2026-07-05", "productor": "Yeferson", "kilos_brutos": "50",
              "precio_kilo": "9877"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    factura = client.get(f"{API}/documentos/{compra['documento_id']}", headers=h).json()
    print("\n===== CORREGIR LA FECHA DE UNA COMPRA PLANA =====")
    print(f"  la cabecera se movió con ella: {factura['fecha']}")
    assert factura["fecha"] == "2026-07-05"
    assert factura["renglones"][0]["fecha"] == "2026-07-05"


# ---------------------------------------------------------------------------
# 6. Lo que no puede cruzarse de empresa
# ---------------------------------------------------------------------------
def test_las_facturas_no_se_cruzan_de_empresa(client, h, h_b):
    comprar(client, h, kilos="1000", precio="9000")
    factura = factura_venta(client, h, [
        {"tipo": "queso", "kilos": "10", "precio_kilo": "15000"},
    ]).json()

    print("\n===== LA QUESERA B NO PUEDE TOCAR LA FACTURA DE LA A =====")
    for metodo, url, kwargs in (
        ("get", f"{API}/documentos/{factura['id']}", {}),
        ("put", f"{API}/documentos/{factura['id']}",
         {"json": {"tipo": "venta", "tercero": "Robada"}}),
        ("post", f"{API}/documentos/{factura['id']}/abonos",
         {"json": {"fecha": FECHA_VENTA, "valor": "1000"}}),
        ("post", f"{API}/documentos/{factura['id']}/anular", {}),
        ("delete", f"{API}/documentos/{factura['id']}", {}),
    ):
        r = getattr(client, metodo)(url, headers=h_b, **kwargs)
        print(f"  {metodo.upper():6} {r.status_code}")
        assert r.status_code == 404, f"{metodo} {url} dejó pasar a otra empresa"

    # Y la lista de la B no la ve.
    lista = client.get(f"{API}/documentos", headers=h_b).json()
    print(f"  facturas que ve la B: {lista['total']}")
    assert lista["total"] == 0
    # La de la A sigue intacta.
    factura = client.get(f"{API}/documentos/{factura['id']}", headers=h).json()
    assert factura["tercero"] == "Tienda La 33"
    assert D(factura["abonado"]) == D(0)


def test_una_factura_de_la_b_no_entra_en_las_cifras_de_la_a(client, h, h_b):
    comprar(client, h, kilos="100", precio="9000")
    comprar(client, h_b, kilos="100", precio="9000")
    factura_venta(client, h_b, [
        {"tipo": "queso", "kilos": "50", "precio_kilo": "15000"},
        {"tipo": "queso", "kilos": "20", "precio_kilo": "16000"},
    ])
    res = client.get(
        f"{API}/resumen", params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=h
    ).json()
    print("\n===== LAS CIFRAS DE LA A NO VEN LA FACTURA DE LA B =====")
    print(f"  ventas de la A: {res['total_ventas']} · disponibles {res['kilos_disponibles']}")
    assert D(res["total_ventas"]) == D(0)
    assert D(res["kilos_disponibles"]) == D("100")


# ---------------------------------------------------------------------------
# 7. Bordes del contrato
# ---------------------------------------------------------------------------
def test_una_factura_sin_renglones_no_existe(client, h):
    r = factura_venta(client, h, [])
    print(f"\n  factura sin renglones: {r.status_code}")
    assert r.status_code == 422


def test_un_renglon_de_venta_mandado_en_una_compra_se_rechaza(client, h):
    """El discriminador: una compra necesita `kilos_brutos`, no `kilos`."""
    r = client.post(
        f"{API}/documentos",
        json={"tipo": "compra", "fecha": FECHA_COMPRA, "tercero": "Yeferson",
              "renglones": [{"tipo": "queso", "kilos": "10", "precio_kilo": "9000"}]},
        headers=h,
    )
    print(f"\n  renglón de venta en una compra: {r.status_code}")
    assert r.status_code == 422
    assert "kilos" in r.text


def test_la_lista_filtra_por_tipo_y_por_tercero(client, h):
    comprar(client, h, kilos="1000", precio="9000", productor="Yeferson")
    factura_venta(client, h, [{"tipo": "queso", "kilos": "10", "precio_kilo": "15000"}])
    compras = client.get(f"{API}/documentos", params={"tipo": "compra"}, headers=h).json()
    ventas = client.get(f"{API}/documentos", params={"tipo": "venta"}, headers=h).json()
    print(f"\n  facturas de compra: {compras['total']} · de venta: {ventas['total']}")
    assert compras["total"] == 1 and ventas["total"] == 1
    assert compras["items"][0]["tercero"] == "Yeferson"
    buscadas = client.get(
        f"{API}/documentos", params={"search": "Tienda"}, headers=h
    ).json()
    assert buscadas["total"] == 1
    assert buscadas["items"][0]["tercero"] == "Tienda La 33"


def test_el_nombre_del_tercero_se_canoniza_como_en_el_payload_plano(client, h):
    """"yeferson" y "Yeferson" son el mismo señor, se registre por donde se registre:
    si no, su cartera se partiría en dos según la puerta que se usó."""
    comprar(client, h, kilos="100", precio="9000", productor="Yeferson Muñoz")
    factura = client.post(
        f"{API}/documentos",
        json={"tipo": "compra", "fecha": FECHA_COMPRA, "tercero": "  yeferson muñoz ",
              "renglones": [{"tipo": "queso", "kilos_brutos": "10", "precio_kilo": "9500"}]},
        headers=h,
    ).json()
    print(f"\n  se mandó '  yeferson muñoz ' y quedó: '{factura['tercero']}'")
    assert factura["tercero"] == "Yeferson Muñoz"
    assert factura["renglones"][0]["productor"] == "Yeferson Muñoz"
    res = client.get(
        f"{API}/resumen", params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=h
    ).json()
    productores = [p.get("productor") for p in res["por_productor"]]
    print(f"  productores en el desglose: {productores}")
    assert productores.count("Yeferson Muñoz") == 1, "el mismo productor salió dos veces"
