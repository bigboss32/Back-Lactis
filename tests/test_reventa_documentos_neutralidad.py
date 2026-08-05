"""LA PRUEBA QUE IMPORTA: una factura de tres renglones tiene que dar EXACTAMENTE
las mismas cifras que esos tres productos vendidos por separado.

Es la razón de ser de todo el diseño. La cabecera (`documentos_reventa`) no guarda
plata: agrupa renglones que son las MISMAS filas de `ventas_queso` de siempre. Si eso
es cierto, entonces el resumen, la ganancia por lote, la cartera y el estado de cuenta
—que leen filas, no facturas— no pueden notar la diferencia. Esta prueba lo mide en vez
de confiarlo: monta el MISMO negocio en dos queseras, en la A con una factura de tres
renglones y en la B con tres ventas sueltas, y exige que TODAS las cifras coincidan.

LAS CIFRAS, A MANO Y FEAS A PROPÓSITO (los números redondos esconden los errores de
redondeo; estos no):

  COMPRAS, iguales en las dos queseras
    · queso        123,45 kg × $9.877  = $1.219.315,65   (+ 46,7 kg de borona)
    · mozzarella   30 barras × $12.345 =   $370.350,00
      -------------------------------------------------
      total compras                    = $1.589.665,65

  LA VENTA (en la A: UNA factura de tres renglones; en la B: tres ventas sueltas)
    · renglón 1  queso       99,11 kg × $15.777 = $1.563.658,47  (flete $317/kg)
    · renglón 2  borona      12,35 kg ×  $4.333 =    $53.512,55
    · renglón 3  mozzarella  7 barras × $21.999 =   $153.993,00
      ----------------------------------------------------------
      TOTAL DE LA FACTURA                       = $1.771.164,02

  El flete del renglón 1: $317 × 99,11 kg = $31.417,87

  EL ABONO DE $1.600.000, DERRAMADO (no dividido)
    · al renglón 1 le entra su saldo completo    = $1.563.658,47
    · al renglón 2 le entra lo que quedaba       =    $36.341,53
    · al renglón 3 no le entra nada
      ----------------------------------------------------------
      suma de los abonos                         = $1.600.000,00  <- EXACTA
      saldo de la factura: 1.771.164,02 − 1.600.000 =  $171.164,02

  Y ese derrame es lo que se le hace a mano a las tres ventas sueltas de la quesera B:
  $1.563.658,47 a la primera y $36.341,53 a la segunda. Si el derrame reparte distinto,
  la cartera de las dos queseras deja de coincidir y esta prueba lo grita.
"""
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"

# --------------------------------------------------------------- las cifras
COMPRA_QUESO_KILOS = "123.45"
COMPRA_QUESO_PRECIO = "9877"
COMPRA_BORONA_KILOS = "46.7"
COMPRA_BARRAS = "30"
COMPRA_PRECIO_BARRA = "12345"

RENGLONES = [
    {"tipo": "queso", "kilos": "99.11", "precio_kilo": "15777",
     "gasto_concepto": "flete", "gasto_por_kilo": "317"},
    {"tipo": "borona", "kilos": "12.35", "precio_kilo": "4333"},
    {"tipo": "mozzarella", "barras": "7", "precio_barra": "21999"},
]

TOTAL_FACTURA = Decimal("1771164.02")
ABONO = Decimal("1600000")
# Cómo tiene que caer el abono, renglón por renglón (esto es el derrame)
CUOTAS = [Decimal("1563658.47"), Decimal("36341.53"), Decimal("0")]

FECHA_COMPRA = "2026-07-01"
FECHA_VENTA = "2026-07-10"
FECHA_ABONO = "2026-07-11"
PERIODO = {"desde": "2026-07-01", "hasta": "2026-07-31"}
CLIENTE = "Tienda La 33"


def D(v):
    """Los Decimal viajan como string en JSON."""
    return Decimal(str(v))


@pytest.fixture()
def cabeceras(client, base_datos):
    """Los dos administradores: la quesera A y la quesera B."""
    return auth_headers(client, "admin.a"), auth_headers(client, "admin.b")


def comprar_lo_mismo(client, h):
    """Las dos compras, idénticas en las dos queseras."""
    r = client.post(
        f"{API}/compras",
        json={
            "fecha": FECHA_COMPRA, "productor": "Yeferson",
            "kilos_brutos": COMPRA_QUESO_KILOS, "borona_kilos": COMPRA_BORONA_KILOS,
            "precio_kilo": COMPRA_QUESO_PRECIO,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/compras",
        json={
            "fecha": FECHA_COMPRA, "productor": "Marlion", "tipo": "mozzarella",
            "barras": COMPRA_BARRAS, "precio_barra": COMPRA_PRECIO_BARRA,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text


def resumen(client, h):
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def lotes(client, h):
    r = client.get(f"{API}/lotes", headers=h)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    return cuerpo if isinstance(cuerpo, list) else cuerpo.get("lotes", cuerpo.get("items", []))


def estado_cuenta(client, h):
    r = client.get(f"{API}/estado-cuenta", params={"cliente": CLIENTE}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_una_factura_de_tres_renglones_da_lo_mismo_que_tres_ventas_sueltas(client, cabeceras):
    """EL CORAZÓN DEL TRABAJO. Ver las cifras en el docstring del módulo."""
    h_a, h_b = cabeceras
    comprar_lo_mismo(client, h_a)
    comprar_lo_mismo(client, h_b)

    # ---------------- quesera A: UNA factura de tres renglones
    r = client.post(
        f"{API}/documentos",
        json={
            "tipo": "venta", "fecha": FECHA_VENTA, "tercero": CLIENTE,
            "observaciones": "pedido del sabado", "renglones": RENGLONES,
        },
        headers=h_a,
    )
    assert r.status_code == 201, r.text
    factura = r.json()
    assert D(factura["total"]) == TOTAL_FACTURA, "el total de la factura no da"

    r = client.post(
        f"{API}/documentos/{factura['id']}/abonos",
        json={"fecha": FECHA_ABONO, "valor": str(ABONO)},
        headers=h_a,
    )
    assert r.status_code == 200, r.text
    factura = r.json()

    # ---------------- quesera B: las tres ventas por separado, con los mismos abonos
    sueltas = []
    for renglon in RENGLONES:
        r = client.post(
            f"{API}/ventas",
            json={"fecha": FECHA_VENTA, "cliente": CLIENTE, **renglon},
            headers=h_b,
        )
        assert r.status_code == 201, r.text
        sueltas.append(r.json())
    for venta, cuota in zip(sueltas, CUOTAS):
        if not cuota:
            continue
        r = client.post(
            f"{API}/ventas/{venta['id']}/abonos",
            json={"fecha": FECHA_ABONO, "valor": str(cuota)},
            headers=h_b,
        )
        assert r.status_code == 200, r.text

    # ---------------- 1) el derrame cayó como en el papel
    print("\n===== EL DERRAME DEL ABONO =====")
    for renglon, cuota in zip(factura["renglones"], CUOTAS):
        abonos = [D(a["valor"]) for a in renglon["abonos"]]
        print(f"  renglon {renglon['orden']} {renglon['tipo']:11} "
              f"valor {renglon['valor_total']:>14}  abonado {renglon['abonado']:>14}  "
              f"{renglon['estado']}")
        assert D(renglon["abonado"]) == cuota
        # UNA cifra entera por renglón, no un pedazo dividido: es lo que el dueño
        # puede señalar con el dedo. Un reparto proporcional dejaría tres cuotas
        # con centavos acomodados.
        assert abonos == ([cuota] if cuota else [])
    suma_cuotas = sum((D(r["abonado"]) for r in factura["renglones"]), D(0))
    print(f"  suma de los abonos: {suma_cuotas}   (el abono fue {ABONO})")
    assert suma_cuotas == ABONO, "el derrame no suma el abono: hay plata perdida"

    # ---------------- 2) el desglose de la factura suma la cifra grande
    suma_renglones = sum((D(r["valor_total"]) for r in factura["renglones"]), D(0))
    print("\n===== EL DESGLOSE DE LA FACTURA =====")
    print(f"  suma de renglones {suma_renglones}  ·  total {factura['total']}  "
          f"·  anulado {factura['total_anulado']}")
    assert suma_renglones == D(factura["total"]) + D(factura["total_anulado"])
    assert D(factura["saldo"]) == TOTAL_FACTURA - ABONO == Decimal("171164.02")
    assert factura["estado_pago"] == "parcial"

    # ---------------- 3) LA NEUTRALIDAD: A contra B, cifra por cifra
    res_a, res_b = resumen(client, h_a), resumen(client, h_b)
    print("\n===== EL RESUMEN: FACTURA (A) CONTRA VENTAS SUELTAS (B) =====")
    intocables = [
        "total_compras", "total_ventas", "kilos_comprados", "kilos_vendidos",
        "kilos_disponibles", "borona_disponible", "barras_compradas",
        "barras_vendidas", "barras_disponibles", "total_gastos",
        "ganancia_estimada", "por_cobrar_clientes", "por_pagar_productores",
        "precio_promedio_compra", "precio_promedio_venta",
    ]
    for campo in intocables:
        if campo not in res_a:
            continue
        print(f"  {campo:24} A {str(res_a[campo]):>16}   B {str(res_b[campo]):>16}")
        assert str(res_a[campo]) == str(res_b[campo]), (
            f"'{campo}' cambia según si la venta se registró como factura o suelta"
        )
    assert D(res_a["total_ventas"]) == TOTAL_FACTURA, "el resumen no ve el total de la factura"
    assert D(res_a["total_gastos"]) == Decimal("31417.87"), "el flete del renglón se perdió"
    assert D(res_a["por_cobrar_clientes"]) == Decimal("171164.02")

    # ---------------- 4) la ganancia por lote (el reparto FIFO)
    lotes_a, lotes_b = lotes(client, h_a), lotes(client, h_b)
    print("\n===== LA GANANCIA POR LOTE =====")
    assert len(lotes_a) == len(lotes_b)
    for lote_a, lote_b in zip(lotes_a, lotes_b):
        for campo, valor in lote_a.items():
            if isinstance(valor, (list, dict)):
                continue
            assert str(valor) == str(lote_b[campo]), (
                f"el lote del {lote_a.get('fecha')} cambia en '{campo}': "
                f"A={valor} B={lote_b[campo]}"
            )
        print(f"  lote {lote_a.get('fecha')}: costo {lote_a.get('costo_total')} "
              f"· ganancia {lote_a.get('ganancia')} (igual en las dos)")

    # ---------------- 5) el estado de cuenta del cliente
    cta_a, cta_b = estado_cuenta(client, h_a), estado_cuenta(client, h_b)
    print("\n===== EL ESTADO DE CUENTA DE " + CLIENTE + " =====")
    for campo in ("total_facturado", "total_abonado", "saldo"):
        if campo not in cta_a:
            continue
        print(f"  {campo:18} A {str(cta_a[campo]):>14}   B {str(cta_b[campo]):>14}")
        assert str(cta_a[campo]) == str(cta_b[campo])
    # Y los renglones son los mismos tres, con la misma plata, en los dos lados.
    ventas_a = sorted(D(v["valor_total"]) for v in cta_a["ventas"])
    ventas_b = sorted(D(v["valor_total"]) for v in cta_b["ventas"])
    print(f"  ventas A {ventas_a}\n  ventas B {ventas_b}")
    assert ventas_a == ventas_b
    pagos_a = sorted(D(p["valor"]) for p in cta_a["pagos"])
    pagos_b = sorted(D(p["valor"]) for p in cta_b["pagos"])
    print(f"  pagos  A {pagos_a}\n  pagos  B {pagos_b}")
    assert pagos_a == pagos_b
    assert sum(pagos_a, D(0)) == ABONO


def test_la_ganancia_por_dia_tampoco_nota_la_diferencia(client, cabeceras):
    """La otra ganancia —la del FIFO día por día— también lee filas, no facturas."""
    h_a, h_b = cabeceras
    comprar_lo_mismo(client, h_a)
    comprar_lo_mismo(client, h_b)

    r = client.post(
        f"{API}/documentos",
        json={"tipo": "venta", "fecha": FECHA_VENTA, "tercero": CLIENTE,
              "renglones": RENGLONES},
        headers=h_a,
    )
    assert r.status_code == 201, r.text
    for renglon in RENGLONES:
        r = client.post(
            f"{API}/ventas",
            json={"fecha": FECHA_VENTA, "cliente": CLIENTE, **renglon},
            headers=h_b,
        )
        assert r.status_code == 201, r.text

    def por_dia(h):
        r = client.get(f"{API}/ganancia-por-dia", params=PERIODO, headers=h)
        assert r.status_code == 200, r.text
        return r.json()

    dia_a, dia_b = por_dia(h_a), por_dia(h_b)
    print("\n===== GANANCIA POR DÍA =====")
    for campo, valor in dia_a.items():
        if isinstance(valor, (list, dict)):
            continue
        print(f"  {campo:22} A {str(valor):>16}   B {str(dia_b[campo]):>16}")
        assert str(valor) == str(dia_b[campo]), f"'{campo}' cambia con la factura"
    assert len(dia_a["dias"]) == len(dia_b["dias"])
    for uno, otro in zip(dia_a["dias"], dia_b["dias"]):
        assert uno == otro, f"el día {uno.get('fecha')} no coincide"


def test_una_compra_de_dos_renglones_da_lo_mismo_que_dos_compras(client, cabeceras):
    """El mismo criterio del otro lado del negocio: comprar varios productos.

    Cifras, a mano:
      · queso       77,77 kg × $10.333 = $803.597,41   (+ 3,33 kg de borona)
      · mozzarella  12 barras × $11.317 = $135.804,00
        ------------------------------------------------
        TOTAL DE LA FACTURA            = $939.401,41
    """
    h_a, h_b = cabeceras
    renglones = [
        {"tipo": "queso", "kilos_brutos": "77.77", "borona_kilos": "3.33",
         "precio_kilo": "10333"},
        {"tipo": "mozzarella", "barras": "12", "precio_barra": "11317"},
    ]
    r = client.post(
        f"{API}/documentos",
        json={"tipo": "compra", "fecha": FECHA_COMPRA, "tercero": "Yubigildo",
              "renglones": renglones},
        headers=h_a,
    )
    assert r.status_code == 201, r.text
    factura = r.json()
    print("\n===== UNA COMPRA DE DOS RENGLONES =====")
    print(f"  renglones: {[(g['tipo'], g['valor_total']) for g in factura['renglones']]}")
    print(f"  total: {factura['total']}")
    assert D(factura["total"]) == Decimal("939401.41")
    assert sum((D(g["valor_total"]) for g in factura["renglones"]), D(0)) == D(factura["total"])

    for renglon in renglones:
        r = client.post(
            f"{API}/compras",
            json={"fecha": FECHA_COMPRA, "productor": "Yubigildo", **renglon},
            headers=h_b,
        )
        assert r.status_code == 201, r.text

    res_a, res_b = resumen(client, h_a), resumen(client, h_b)
    for campo in ("total_compras", "kilos_comprados", "borona_disponible",
                  "barras_compradas", "por_pagar_productores"):
        print(f"  {campo:22} A {str(res_a[campo]):>14}   B {str(res_b[campo]):>14}")
        assert str(res_a[campo]) == str(res_b[campo])
    assert D(res_a["total_compras"]) == Decimal("939401.41")
