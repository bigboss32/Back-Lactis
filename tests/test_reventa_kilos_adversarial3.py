"""Tercera tanda: LA CIFRA QUE EL DUEÑO CUADRA A MANO cuando en el mismo período
hay queso y un producto nuevo por kilo. Mide el promedio de compra mezclado, el
costo que el desglose le carga al queso y el FIFO que reparte los kilos de un
producto entre las compras de otro.
"""
from decimal import Decimal

from tests.test_reventa_kilos_adversarial import (  # noqa: F401  (fixtures)
    API,
    CERO_D,
    PROD,
    comprar,
    crear_producto,
    detalle,
    h,
    hb,
    imprimir_resumen,
    lotes,
    resumen,
    vender,
)


def test_queso_y_producto_nuevo_en_el_mismo_periodo(client, h):
    """DEFECTO 17: el promedio de compra por kilo mezcla los precios de dos
    productos distintos, y el desglose le carga al QUESO un costo que no es el suyo.

    Los hechos:
      compra 100 kg de QUESO   a $20.000 = $2.000.000
      compra 100 kg de COSTEÑO a  $5.000 =   $500.000
      venta   50 kg de QUESO   a $25.000 = $1.250.000
      venta  100 kg de COSTEÑO a  $9.000 =   $900.000

    La verdad, producto por producto:
      queso:   1.250.000 - (50 x 20.000 = 1.000.000) =   250.000
      costeño:   900.000 - (100 x 5.000 =   500.000) =   400.000
    """
    crear_producto(client, h, "Costeno")
    for tipo, kilos, precio in (("queso", "100", "20000"), ("costeno", "100", "5000")):
        r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                    tipo=tipo, kilos_brutos=kilos, precio_kilo=precio)
        assert r.status_code == 201, r.text
    r = vender(client, h, fecha="2026-03-10", cliente="Tienda Sol",
               tipo="queso", kilos="50", precio_kilo="25000")
    assert r.status_code == 201, r.text
    r = vender(client, h, fecha="2026-03-11", cliente="Tienda Sol",
               tipo="costeno", kilos="100", precio_kilo="9000")
    assert r.status_code == 201, r.text

    res = resumen(client, h)
    imprimir_resumen("RESUMEN mezclado: queso + producto nuevo", res)
    print("\n  LO QUE EL DUEÑO ESPERA vs LO QUE SALE:")
    print("    precio promedio de compra del queso   esperado 20000.00  sale "
          f"{res['precio_promedio_compra']}")
    fila_queso = next(f for f in res["por_producto"] if f["producto"] == "queso")
    fila_borona = next(f for f in res["por_producto"] if f["producto"] == "borona")
    print(f"    costo de los 50 kg de queso           esperado 1000000.00  sale "
          f"{fila_queso['costo']}")
    print(f"    ganancia del queso                    esperado  250000.00  sale "
          f"{fila_queso['ganancia']}")
    print(f"    ganancia del costeño (sale en BORONA) esperado  400000.00  sale "
          f"{fila_borona['ganancia']}")

    # El promedio mezcla los dos precios: 2.500.000 / 200 kg.
    assert Decimal(res["precio_promedio_compra"]) == Decimal("12500.00")
    # Y el costo del queso sale a prorrata de ESE promedio: 50/200 x 2.500.000.
    assert Decimal(fila_queso["costo"]) == Decimal("625000.00")
    assert Decimal(fila_queso["ganancia"]) == Decimal("625000.00")
    assert Decimal(fila_borona["costo"]) == CERO_D
    assert Decimal(fila_borona["ganancia"]) == Decimal("900000.00")

    # La cifra grande SÍ cuadra con la suma de las filas (la borona sale por
    # diferencia), así que el error no se ve sumando la columna: se ve en las filas.
    suma = sum(Decimal(f["ganancia"]) for f in res["por_producto"])
    print(f"\n    suma de las filas = {suma}  y ganancia_estimada = "
          f"{res['ganancia_estimada']}  (cuadra, pero cada fila miente)")
    assert suma == Decimal(res["ganancia_estimada"]) == Decimal("-350000.00")

    # ---------------------------------------------------------------- el FIFO
    panel = lotes(client, h)
    print("\n===== LOTE 2026-03-01: dos productos en la MISMA cola FIFO =====")
    for lote in panel["lotes"]:
        print(f"  lote {lote['fecha']} comprados={lote['kilos_comprados']} "
              f"vendidos={lote['kilos_vendidos']} sin_vender={lote['kilos_sin_vender']}")
        for c in lote["detalle_compras"]:
            print(f"    compra de {c['productor']}: kilos={c['kilos']} "
                  f"precio_kilo={c['precio_kilo']} vendidos={c['kilos_vendidos']} "
                  f"ingresos={c['ingresos']} costo_realizado={c['costo_realizado']} "
                  f"sin_vender={c['kilos_sin_vender']} costo_sin_vender={c['costo_sin_vender']}")
        for v in lote["detalle_ventas"]:
            print(f"    venta {v['fecha']} {v['cliente']} tipo={v['tipo']} "
                  f"kilos={v['kilos']} ingreso={v['ingreso']} costo={v['costo']} "
                  f"ganancia={v['ganancia']}")
    lote = panel["lotes"][0]
    # Las dos compras están en la misma cola: los 150 kg vendidos (50 de queso +
    # 100 de costeño) se sirvieron primero de la compra de QUESO.
    assert Decimal(lote["kilos_vendidos"]) == Decimal("150.00")
    compra_queso = lote["detalle_compras"][0]
    compra_costeno = lote["detalle_compras"][1]
    assert Decimal(compra_queso["kilos_vendidos"]) == Decimal("100.00"), (
        "la compra de queso pagó también los kilos del costeño"
    )
    assert Decimal(compra_costeno["kilos_vendidos"]) == Decimal("50.00")
    print("\n  >>> los 100 kg de la compra de QUESO ($20.000/kg) cubrieron los "
          "50 kg vendidos de queso Y 50 kg del costeño; los otros 50 del costeño "
          "salieron de la compra de costeño. El costo de cada venta quedó cruzado.")
