"""Cuarta tanda: la MISMA fuga de plata del desglose, pero SIN necesitar la otra
empresa. La puerta es el PUT de la compra, que no pasa por `preparar_renglones` y
por lo tanto nunca mira el catálogo.

LA PUERTA SIGUE ABIERTA (el PUT todavía no mira el catálogo: eso es lo que esta
prueba mide y no arregla), pero YA NO SE PIERDE LA PLATA. La clasificación por
unidad ahora exige que la fila TRAIGA unidades, así que una compra con una clave
"por unidad" pero con kilos y cero barras se cuenta donde de verdad está: en los
kilos. Sus pesos aparecen en el desglose, sus kilos entran al reparto por lotes, y
el promedio por barra no divide plata entre cero barras.
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


def test_editar_una_compra_por_unidad_le_mete_kilos_pero_la_plata_no_se_pierde(
    client, h
):
    """DEFECTO 18: `CompraQuesoService.actualizar` llama a `_calcular` directo, sin
    pasar por `preparar_renglones`, así que el PUT NUNCA mira el catálogo. A una
    compra de un producto POR UNIDAD se le pueden meter kilos por PUT. ESO SIGUE
    ABIERTO y esta prueba lo deja medido.

    LO QUE SÍ SE CERRÓ es la fuga de plata que producía. Antes la fila quedaba con
    kilos > 0 y una clave que la clasificación consideraba "de unidades", así que su
    plata salía del bloque de kilos y caía en el de barras... donde las dos filas
    solo se imprimen si hay barras. El desglose sumaba $0 contra un encabezado de
    -$200.000, el promedio por barra dividía $200.000 entre 0 barras y los 50 kg no
    aparecían en ningún lote.

    Ahora la unidad de una fila la decide LO QUE LA FILA TRAE (ver
    `se_mide_en_unidades`): sin barras, se pesa. Las cifras de abajo son las nuevas,
    y son las correctas: $200.000 por 50 kg son $4.000 el kilo, y eso es lo que el
    dueño va a leer."""
    r = client.post(PROD, json={"nombre": "Panela", "unidad": "unidad"}, headers=h)
    assert r.status_code == 201, r.text

    # La compra "por unidad" ya nace en $0 (defecto 11).
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="panela", barras="30", precio_barra="4000")
    assert r.status_code == 201, r.text
    compra_id = r.json()["id"]
    print("\nCOMPRA de 30 panelas a $4.000 -> valor_total =", r.json()["valor_total"])

    # El PUT le mete KILOS a una compra de un producto que se cuenta.
    r = client.put(f"{API}/compras/{compra_id}",
                   json={"kilos_brutos": "50", "precio_kilo": "4000"}, headers=h)
    print("PUT con kilos a una compra POR UNIDAD ->", r.status_code,
          "" if r.status_code == 200 else detalle(r))
    assert r.status_code == 200, "el PUT no consulta el catálogo"
    c = r.json()
    print(f"  tipo={c['tipo']} unidad={c['unidad']} kilos_netos={c['kilos_netos']} "
          f"barras={c['barras']} valor_total={c['valor_total']}")
    assert Decimal(c["kilos_netos"]) == Decimal("50.00")
    assert Decimal(c["valor_total"]) == Decimal("200000.00")

    res = resumen(client, h)
    imprimir_resumen("RESUMEN: $200.000 de compra con una clave 'por unidad'", res)
    print("  total_compras_mozzarella =", res["total_compras_mozzarella"])
    print("  barras_compradas         =", res["barras_compradas"])
    print("  precio_promedio_compra_barra =", res["precio_promedio_compra_barra"])
    suma = sum(Decimal(f["ganancia"]) for f in res["por_producto"])
    print(f"\n  suma de las filas del desglose = {suma}")
    print(f"  ganancia_estimada             = {res['ganancia_estimada']}")
    # La plata está donde están los kilos, y el desglose vuelve a sumar la tarjeta.
    assert Decimal(res["kilos_comprados"]) == Decimal("50.00")
    assert Decimal(res["total_compras"]) == Decimal("200000.00")
    assert Decimal(res["precio_promedio_compra"]) == Decimal("4000.00")
    # Nada de esto es mozzarella, así que no hay plata de barras entre cero barras
    assert Decimal(res["total_compras_mozzarella"]) == CERO_D
    assert Decimal(res["barras_compradas"]) == CERO_D
    assert Decimal(res["precio_promedio_compra_barra"]) == CERO_D
    # Las mismas cuatro filas de siempre, y la del residuo con los $200.000
    assert [f["producto"] for f in res["por_producto"]] == [
        "queso", "borona", "merma", "pendiente"
    ]
    pendiente = next(f for f in res["por_producto"] if f["producto"] == "pendiente")
    assert Decimal(pendiente["kilos"]) == Decimal("50.00")
    assert Decimal(pendiente["costo"]) == Decimal("200000.00")
    assert suma == Decimal(res["ganancia_estimada"]) == Decimal("-200000.00")
    print("  >>> la suma de las filas ya da la misma cifra de la tarjeta")

    # Y esos 50 kg vuelven a existir para el inventario de kilos y para el FIFO.
    print("  kilos_disponibles =", res["kilos_disponibles"])
    panel = lotes(client, h)
    print("  lotes del panel:", len(panel["lotes"]),
          "total_kilos_comprados =", panel["total_kilos_comprados"])
    assert len(panel["lotes"]) == 1
    assert Decimal(panel["total_kilos_comprados"]) == Decimal("50.00")
    assert Decimal(panel["lotes"][0]["costo_total"]) == Decimal("200000.00")
    print("  >>> los 50 kg comprados ya aparecen en su lote, con su costo")
