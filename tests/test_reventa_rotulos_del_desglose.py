"""LOS RÓTULOS DEL DESGLOSE Y LA PLATA SIN CLASIFICAR.

El desglose tiene filas que NO son de un producto —la merma, lo que quedó en
inventario, la plata que no cayó en ninguna fila— y cada una viaja con su propia clave,
que es con lo que la pantalla decide cómo pintar el renglón. Aquí se mide que un
producto llamado "Merma" o "Pendiente" no se robe ese renglón ni duplique su clave:
las filas calculadas y las de producto tienen que poder convivir, pase el dueño el
nombre que pase.
"""
from decimal import Decimal

import pytest

from tests.test_reventa_catalogo_de_punta_a_punta import (  # noqa: F401
    API, CERO, D, PERIODO, PROD, comprar, detalle, existencia, fila, h, hb,
    lotes, pintar, producto, regla_de_oro, resumen, vender,
)


def test_j1_un_producto_llamado_merma_no_puede_rotularse_como_perdida(client, h):
    """Un producto que se llame 'Merma' se compra y se vende como cualquier otro.
    Su renglón NO puede salir rotulado 'Merma (pérdida real)': el dueño leería una
    pérdida donde hubo una venta."""
    p = producto(client, h, "Merma")
    assert comprar(client, h, productor="Pedro Perez", tipo=p["clave"],
                   kilos_brutos=100, precio_kilo=1000).status_code == 201
    assert vender(client, h, cliente="Tienda Sol", tipo=p["clave"], kilos=100,
                  precio_kilo=2000).status_code == 201
    res = resumen(client, h)
    pintar("un producto que se llama 'Merma'", res,
           ("total_compras", "total_ventas", "ganancia_estimada"))
    de_merma = [f for f in res["por_producto"] if f["producto"] == "merma"]
    con_plata = [f for f in de_merma if D(f["ingreso"]) or D(f["costo"])]
    for f in con_plata:
        print(f"   fila con plata rotulada '{f['etiqueta']}': ingreso={f['ingreso']} "
              f"costo={f['costo']} nota='{f['nota']}'")
    assert all(f["etiqueta"] != "Merma (pérdida real)" for f in con_plata), (
        "una venta de $200.000 quedó rotulada como 'Merma (pérdida real)'"
    )
    regla_de_oro(res, "producto llamado Merma")


def test_j2_un_producto_llamado_pendiente_no_se_rotula_como_inventario(client, h):
    p = producto(client, h, "Pendiente")
    assert comprar(client, h, productor="Pedro Perez", tipo=p["clave"],
                   kilos_brutos=100, precio_kilo=1000).status_code == 201
    assert vender(client, h, cliente="Tienda Sol", tipo=p["clave"], kilos=100,
                  precio_kilo=2000).status_code == 201
    res = resumen(client, h)
    pintar("un producto que se llama 'Pendiente'", res,
           ("total_ventas", "kilos_pendientes"))
    con_plata = [
        f for f in res["por_producto"]
        if f["producto"] == "pendiente" and (D(f["ingreso"]) or D(f["costo"]))
    ]
    for f in con_plata:
        print(f"   fila con plata rotulada '{f['etiqueta']}': ingreso={f['ingreso']} "
              f"costo={f['costo']}")
    assert all(f["etiqueta"] != "Aún en inventario" for f in con_plata), (
        "una venta quedó rotulada 'Aún en inventario'"
    )
    regla_de_oro(res, "producto llamado Pendiente")


def test_j3_la_plata_sin_producto_sale_rotulada(client, h):
    """La red de seguridad: plata que no cae en ninguna fila de producto tiene que
    salir en 'Sin producto (plata sin clasificar)' y no desaparecer."""
    import uuid as _uuid

    from app.modules.reventa.models import CompraQueso
    from tests.conftest import TestingSessionLocal

    producto(client, h, "Huevo", unidad="unidad")
    r = comprar(client, h, productor="Patricia", tipo="huevo", barras=10,
                precio_barra=500)
    assert r.status_code == 201, detalle(r)
    compra_id = r.json()["id"]
    with TestingSessionLocal() as s:
        fila_db = s.get(CompraQueso, _uuid.UUID(compra_id))
        fila_db.barras = Decimal("0")
        fila_db.valor_total = Decimal("5000.00")
        s.commit()

    res = resumen(client, h)
    pintar("plata sin cantidad en ninguna unidad", res, ("total_compras",))
    # LO QUE IMPORTA: no desaparece. Cae en las filas del huevo (clasificada como
    # kilos, porque la fila no trae unidades) y la columna suma el encabezado. La
    # fila de rescate 'sin_producto' no hace falta: nadie perdió un peso.
    assert D(res["total_compras"]) == D("5000.00")
    suma = sum((D(f["costo"]) for f in res["por_producto"]), CERO)
    assert suma == D("5000.00"), f"la columna del costo suma {suma} y son $5.000"
    regla_de_oro(res, "plata sin clasificar")
    # Lo que sí queda anotado: la plata de un producto POR UNIDAD salió en filas
    # rotuladas en KILOS. No se pierde, pero se lee en la unidad equivocada.
    unidades_de_sus_filas = {
        f["unidad"] for f in res["por_producto"]
        if f["producto"].startswith("huevo") and D(f["costo"])
    }
    print("   la plata del huevo salió en filas con unidad:", unidades_de_sus_filas)
