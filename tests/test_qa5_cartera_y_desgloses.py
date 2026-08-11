"""Quinto lote de QA del libro anterior: lo que se puede fijar en el backend.

Cada prueba imprime la evidencia con números, porque el usuario verifica los
desgloses a mano con calculadora. Los dos hallazgos del frontend (la vista previa
del estado de cuenta y el filtro de la tabla "Ganancia por producto") se fijan
aquí por el lado de los DATOS: qué manda el backend y por qué la pantalla no
puede esconderlo.
"""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PERIODO = {"desde": "2026-07-01", "hasta": "2026-07-31"}


def D(valor):
    return Decimal(str(valor))


def suma(filas, campo):
    return sum((D(f[campo]) for f in filas), Decimal("0"))


def resumen(client, headers):
    r = client.get(f"{API}/resumen", params=PERIODO, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def compra(client, headers, **datos):
    r = client.post(f"{API}/compras", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, headers, **datos):
    r = client.post(f"{API}/ventas", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def conversion(client, headers, **datos):
    r = client.post(f"{API}/conversiones", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def saldo_anterior(client, headers, **datos):
    r = client.post(f"{API}/saldos-anteriores", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1. Período con ventas y SIN compras: nadie se inventa la ganancia
# ---------------------------------------------------------------------------
def test_1_periodo_sin_compras_no_reparte_la_ganancia(client, base_datos):
    """La ganancia salió de queso comprado ANTES del período.

    Las filas de los deudores históricos se quedan en 0 a propósito (no se les
    compró nada este período) y el frontend lo detecta con kilos_comprados == 0
    para no prometer un cuadre que no existe. Lo que NO puede pasar es que la
    diferencia de redondeo del reparto se le cuelgue a la última fila.
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-05-01", productor="Prod Mayo",
           kilos_brutos="500", precio_kilo="9000")
    venta(client, h, fecha="2026-07-20", cliente="Cliente Julio", kilos="400",
          precio_kilo="13000")

    datos = resumen(client, h)
    print("\n--- 1. ventas sin compras en el período ---")
    print(f"    kilos_comprados={datos['kilos_comprados']} "
          f"total_compras={datos['total_compras']} "
          f"ganancia_estimada={datos['ganancia_estimada']}")
    for fila in datos["por_productor"]:
        print(f"    {fila['productor']:14} compras={fila['compras']} "
              f"kilos={fila['kilos']} ganancia={fila['ganancia_estimada']} "
              f"por_pagar={fila['por_pagar']}")

    assert D(datos["kilos_comprados"]) == 0
    assert D(datos["ganancia_estimada"]) == D("5200000")
    # Ninguna fila se lleva el neto del período
    assert suma(datos["por_productor"], "ganancia_estimada") == 0
    assert [f["productor"] for f in datos["por_productor"]] == ["Prod Mayo"]
    assert D(datos["por_productor"][0]["por_pagar"]) == D("4500000")
    # Y los dos cuadres de siempre siguen en pie
    assert suma(datos["por_productor"], "por_pagar") == D(
        datos["por_pagar_productores"]
    )
    assert suma(datos["por_producto"], "ganancia") == D(datos["ganancia_estimada"])
    # La plata sí queda explicada, pero en el desglose por PRODUCTO
    anterior = [f for f in datos["por_producto"] if f["producto"] == "anterior"]
    print(f"    fila que explica de dónde salió: {anterior[0]['etiqueta']} "
          f"({anterior[0]['kilos']} kg)")
    assert len(anterior) == 1 and D(anterior[0]["kilos"]) == D("400")


# ---------------------------------------------------------------------------
# 2. Un saldo negativo no resta de los agregados de cartera
# ---------------------------------------------------------------------------
def test_2_venta_sobrepagada_no_rebaja_la_cartera(client, base_datos):
    """Rebajarle el precio a una venta ya pagada sigue permitido (deja saldo a
    favor del cliente), pero lo que un cliente pagó de MÁS no reduce lo que
    deben los otros: los agregados suman el saldo de cada fila acotado en cero.

    LA EXPECTATIVA DE LA FILA CAMBIÓ, Y LA NUEVA ES LA CORRECTA. Esta prueba
    exigía `saldo == -550.000` en la venta editada, de cuando el `saldo` del
    modelo era la resta cruda `valor_total - abonado`. Hoy son DOS hechos con dos
    nombres: `saldo` es lo que FALTA COBRAR y se acota en cero, y `saldo_a_favor`
    es lo que el cliente pagó de más. Se exige lo nuevo por tres razones:

    - Un "saldo de -$550.000" no significa nada para el dueño; lo que él necesita
      leer es "de esta venta no falta cobrar nada y hay $550.000 a favor del
      cliente", que es justo lo que dicen los dos campos.
    - La fila y los agregados cuentan ahora con EL MISMO criterio (ver
      `saldo_pendiente` en el repositorio, que acota fila por fila antes de sumar).
      Mientras la fila mostrara el negativo crudo y la tarjeta lo acotara, la
      columna del detalle no sumaba la tarjeta y él lo notaba con la calculadora:
      esa contradicción es lo que se cerró.
    - El saldo a favor NO SE ESCONDE, que es lo único que habría que defenderle a
      la expectativa vieja: sigue completo en su propio campo aquí y con su signo
      en el estado de cuenta del cliente, y las dos cosas se exigen más abajo en
      esta misma prueba.
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-01", productor="Sebastián Ruiz",
           kilos_brutos="300", precio_kilo="10000")
    pagada = venta(client, h, fecha="2026-07-10", cliente="Doña Rosa", kilos="100",
                   precio_kilo="19500", pagada_de_contado=True)
    venta(client, h, fecha="2026-07-12", cliente="Otro Cliente", kilos="100",
          precio_kilo="15000")

    r = client.put(f"{API}/ventas/{pagada['id']}", json={"precio_kilo": "14000"},
                   headers=h)
    assert r.status_code == 200, r.text
    editada = r.json()
    print("\n--- 2. venta pagada de contado y rebajada ---")
    print(f"    total={editada['valor_total']} abonado={editada['abonado']} "
          f"saldo={editada['saldo']} saldo_a_favor={editada['saldo_a_favor']}")
    # De esta venta no falta cobrar nada...
    assert D(editada["saldo"]) == 0
    # ...y los $550.000 que pagó de más están dichos, no perdidos
    assert D(editada["saldo_a_favor"]) == D("550000")
    assert D(editada["abonado"]) - D(editada["valor_total"]) == D("550000")

    datos = resumen(client, h)
    print(f"    suma cruda de saldos (lo que daba antes) = "
          f"{D('1400000') - D('1950000') + D('1500000')}")
    print(f"    por_cobrar_clientes = {datos['por_cobrar_clientes']}")
    assert D(datos["por_cobrar_clientes"]) == D("1500000")

    # El saldo a favor NO se esconde: sigue en el estado de cuenta del cliente
    cuenta = client.get(f"{API}/estado-cuenta", params={"cliente": "Doña Rosa"},
                        headers=h).json()
    print(f"    estado de cuenta de Doña Rosa: saldo={cuenta['saldo']}")
    assert D(cuenta["saldo"]) == D("-550000")

    # Con el libro anterior la tarjeta suma los dos lados, sin el negativo
    saldo_anterior(client, h, tipo="cobrar", tercero="Tercero Libro",
                   fecha="2026-02-01", concepto="Factura vieja",
                   valor_total="300000")
    con_libro = resumen(client, h)
    print(f"    con libro por cobrar 300.000 -> "
          f"por_cobrar_clientes={con_libro['por_cobrar_clientes']}")
    assert D(con_libro["por_cobrar_clientes"]) == D("1800000")
    # Y el otro desglose sigue cuadrando con su tarjeta
    assert suma(con_libro["por_productor"], "por_pagar") == D(
        con_libro["por_pagar_productores"]
    )


# ---------------------------------------------------------------------------
# 3. El desglose por producto manda el centavo del redondeo en una fila de 0 kg
# ---------------------------------------------------------------------------
def test_3_el_residuo_de_cero_kilos_lleva_el_centavo(client, base_datos):
    """Cuando el lote queda repartido exacto (kilos_pendientes = 0), la fila del
    residuo se lleva los centavos del redondeo.

    Es la razón por la que la tabla del frontend NO puede esconder una fila por
    ser "pequeña": esconderla dejaba las filas visibles sumando un centavo
    distinto del Total de su propio pie. Solo se puede esconder la que no aporta
    nada: 0 kilos Y ganancia exactamente 0.
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Centavos",
           kilos_brutos="3", precio_kilo="3333.3366666667")
    venta(client, h, fecha="2026-07-10", cliente="Cliente C", kilos="1",
          precio_kilo="5000")
    conversion(client, h, fecha="2026-07-11", kilos="1", destino="borona",
               precio_kilo="0")
    conversion(client, h, fecha="2026-07-12", kilos="1", destino="merma")

    datos = resumen(client, h)
    print("\n--- 3. residuo de 0 kg con el centavo del redondeo ---")
    for fila in datos["por_producto"]:
        print(f"    {fila['producto']:10} kilos={fila['kilos']:>7} "
              f"ganancia={fila['ganancia']:>12}")
    print(f"    total_compras={datos['total_compras']} "
          f"kilos_pendientes={datos['kilos_pendientes']} "
          f"ganancia_estimada={datos['ganancia_estimada']}")

    assert D(datos["total_compras"]) == D("10000.01")
    assert D(datos["kilos_pendientes"]) == 0
    assert D(datos["ganancia_estimada"]) == D("-5000.01")
    residuo = [f for f in datos["por_producto"] if f["producto"] == "pendiente"]
    assert len(residuo) == 1
    assert D(residuo[0]["kilos"]) == 0 and D(residuo[0]["ganancia"]) == D("0.01")
    # Las cuatro filas suman EXACTO la cifra grande: si la pantalla esconde la
    # del centavo, deja de sumar su pie.
    assert suma(datos["por_producto"], "ganancia") == D(datos["ganancia_estimada"])
    sin_el_centavo = suma(
        [f for f in datos["por_producto"] if f["producto"] != "pendiente"], "ganancia"
    )
    print(f"    sin la fila del residuo la columna daría {sin_el_centavo} "
          f"contra un pie de {datos['ganancia_estimada']}")
    assert sin_el_centavo != D(datos["ganancia_estimada"])
    assert suma(datos["por_productor"], "por_pagar") == D(
        datos["por_pagar_productores"]
    )
