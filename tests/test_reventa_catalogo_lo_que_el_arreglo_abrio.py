"""LO QUE EL ARREGLO DEL CATÁLOGO PUDO ABRIR: segunda pasada, con cifras.

Las de este archivo son las pruebas que encontraron los dos defectos gordos y que hoy
los sostienen cerrados: el resumen y el panel de lotes contradiciéndose en el costo de
la MISMA venta, y el orden del catálogo —un campo de presentación— moviéndole a la
borona meses de historia. Cada una exige el comportamiento bueno y lo imprime.
"""
from decimal import Decimal

import pytest

from tests.test_reventa_catalogo_de_punta_a_punta import (  # noqa: F401
    API, CERO, D, PERIODO, PROD, comprar, detalle, existencia, fila, h, hb,
    lotes, pintar, producto, regla_de_oro, resumen, vender,
)


def test_i1_el_resumen_y_los_lotes_tienen_que_decir_el_mismo_costo(client, h):
    """LAS DOS PANTALLAS QUE EL DUEÑO CRUZA A MANO NO PUEDEN CONTRADECIRSE.

    50 kg de borona comprados a $1.000 y vendidos a $2.000. El panel de lotes
    reparte por producto y le carga a esa venta su costo de $50.000; el resumen
    tiene que decir lo mismo.
    """
    assert comprar(client, h, productor="Pedro Perez", tipo="borona",
                   kilos_brutos=50, precio_kilo=1000).status_code == 201
    assert vender(client, h, cliente="Tienda Sol", tipo="borona", kilos=50,
                  precio_kilo=2000).status_code == 201

    res = resumen(client, h)
    pintar("borona comprada y vendida: resumen", res,
           ("total_compras", "total_ventas", "kilos_pendientes"))
    porlote = lotes(client, h)
    print("   lotes:", porlote)
    del_lote = porlote["2026-03-01 Pedro Perez"]
    costo_resumen = D(fila(res, "borona")["costo"])
    print(f"\n   costo de la borona vendida: lotes={del_lote['costo_realizado']} "
          f"resumen={costo_resumen}")
    assert costo_resumen == del_lote["costo_realizado"], (
        "el panel de lotes y el desglose del resumen dicen costos distintos para la "
        "MISMA venta"
    )
    assert del_lote["kilos_sin_vender"] == CERO


def test_i2_kilos_pendientes_no_puede_contradecir_a_las_existencias(client, h):
    """La cifra grande 'kilos pendientes' y la lista de existencias salen en la
    MISMA respuesta: no pueden decir cosas distintas."""
    assert comprar(client, h, productor="Pedro Perez", tipo="borona",
                   kilos_brutos=50, precio_kilo=1000).status_code == 201
    assert vender(client, h, cliente="Tienda Sol", tipo="borona", kilos=50,
                  precio_kilo=2000).status_code == 201
    res = resumen(client, h)
    pintar("borona comprada y vendida", res, ("kilos_pendientes",))
    en_kilos = sum(
        (D(e["disponible"]) for e in res["existencias"] if e["unidad"] == "kg"), CERO
    )
    print(f"\n   kilos_pendientes={res['kilos_pendientes']}  "
          f"suma de existencias en kg={en_kilos}")
    assert D(res["kilos_pendientes"]) == en_kilos


def test_i3_reordenar_el_catalogo_no_puede_mover_la_borona(client, h):
    """EL ORDEN DEL CATÁLOGO ES PRESENTACIÓN Y NO PUEDE MOVER PLATA.

    El dueño ya tiene queso y borona con su historia. Si agrega otro subproducto
    del queso y lo sube al primer puesto de la lista, los ajustes que ya están
    registrados —los kilos que pasó a borona— no se pueden ir a parar al producto
    nuevo.
    """
    cat = client.get(PROD, params={"size": 50}, headers=h).json()["items"]
    queso = next(p for p in cat if p["clave"] == "queso")
    assert comprar(client, h, productor="Pedro Perez", tipo="queso",
                   kilos_brutos=100, precio_kilo=20000,
                   borona_kilos=20).status_code == 201
    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-02", "kilos": 10, "destino": "borona"},
                    headers=h)
    assert r.status_code == 201, detalle(r)
    antes = resumen(client, h)
    pintar("antes de agregar el subproducto nuevo", antes,
           ("kilos_a_borona", "kilos_pendientes"))
    borona_antes = D(existencia(antes, "borona")["disponible"])
    assert borona_antes == D(30), "montaje malo"

    nuevo = producto(client, h, "Migajon", subproducto_de_id=queso["id"])
    r = client.put(f"{PROD}/{nuevo['id']}", json={"orden": 0}, headers=h)
    assert r.status_code == 200, detalle(r)
    despues = resumen(client, h)
    pintar("después de subir 'Migajon' al primer puesto", despues,
           ("kilos_a_borona", "kilos_pendientes"))
    assert D(existencia(despues, "borona")["disponible"]) == borona_antes, (
        "cambiar el ORDEN del catálogo le quitó la mercancía a la borona"
    )
    assert D(existencia(despues, "migajon")["disponible"]) == CERO, (
        "el producto nuevo recibió mercancía que nunca entró, solo por quedar de "
        "primero en la lista"
    )
    assert D(fila(despues, "borona")["costo"]) == D(fila(antes, "borona")["costo"])
    regla_de_oro(despues, "catálogo reordenado")


def test_i4_un_producto_llamado_como_una_fila_del_desglose(client, h):
    """Un producto cuyo nombre genera la clave de una fila que el desglose ya usa
    ('Pendiente', 'Merma', 'Sin producto') no puede producir dos renglones con la
    misma clave: la pantalla los pinta por esa clave."""
    for nombre in ("Pendiente", "Merma", "Sin producto"):
        p = producto(client, h, nombre)
        assert comprar(client, h, productor="Pedro Perez", tipo=p["clave"],
                       kilos_brutos=10, precio_kilo=1000).status_code == 201
    res = resumen(client, h)
    pintar("productos llamados como las filas del desglose", res, ("total_compras",))
    claves = [f["producto"] for f in res["por_producto"]]
    repetidas = sorted({c for c in claves if claves.count(c) > 1})
    assert not repetidas, f"hay filas del desglose con la clave repetida: {repetidas}"
    regla_de_oro(res, "claves que chocan")


def test_i5_editar_una_compra_por_unidad_no_le_borra_la_plata(client, h):
    """Editar por PUT una compra de un producto POR UNIDAD que no es la mozzarella:
    la plata se recalcula con SU unidad, no se pone en cero."""
    producto(client, h, "Huevo", unidad="unidad")
    r = comprar(client, h, productor="Patricia", tipo="huevo", barras=100,
                precio_barra=500)
    compra_id = r.json()["id"]
    r = client.put(f"{API}/compras/{compra_id}", json={"barras": 120}, headers=h)
    print("\neditar la compra a 120 huevos ->", r.status_code, detalle(r))
    assert r.status_code == 200, detalle(r)
    assert D(r.json()["valor_total"]) == D("60000.00")
    assert D(r.json()["barras"]) == D(120)
    res = resumen(client, h)
    pintar("compra por unidad editada", res, ("total_compras",))
    assert D(res["total_compras"]) == D("60000.00")
    assert D(existencia(res, "huevo")["disponible"]) == D(120)
    regla_de_oro(res, "compra por unidad editada")


def test_i6_bajarle_los_kilos_a_una_compra_no_deja_el_inventario_en_negativo(client, h):
    """Bajar una compra de 100 a 10 kg con 80 ya vendidos deja el inventario en
    −70, y desde ahí ninguna venta vuelve a pasar."""
    producto(client, h, "Panela")
    r = comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=100,
                precio_kilo=2000)
    compra_id = r.json()["id"]
    assert vender(client, h, cliente="Don Jose", tipo="panela", kilos=80,
                  precio_kilo=3000).status_code == 201
    r = client.put(f"{API}/compras/{compra_id}", json={"kilos_brutos": 10}, headers=h)
    print("\nbajar la compra de 100 a 10 kg con 80 vendidos ->", r.status_code,
          detalle(r))
    assert r.status_code == 422, "el inventario de la panela quedó en negativo"
    assert "Panela" in detalle(r)
    res = resumen(client, h)
    assert D(existencia(res, "panela")["disponible"]) == D(20)


def test_i7_el_estado_de_cuenta_del_productor_cuadra_con_un_producto_nuevo(client, h):
    """El estado de cuenta que se le ENTREGA al productor tiene que cuadrar."""
    producto(client, h, "Panela")
    assert comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=100,
                   precio_kilo=2000).status_code == 201
    producto(client, h, "Huevo", unidad="unidad")
    assert comprar(client, h, productor="Patricia", tipo="huevo", barras=50,
                   precio_barra=600).status_code == 201
    r = client.get(f"{API}/estado-cuenta-productor",
                   params={"productor": "Patricia"}, headers=h)
    assert r.status_code == 200, r.text
    ec = r.json()
    for c in ec["compras_detalle"]:
        print("   ", {k: c[k] for k in c if k in (
            "fecha", "producto", "tipo", "unidad", "kilos", "barras",
            "valor_total")})
    print("   total_comprado:", ec["total_comprado"], " total_kilos:",
          ec["total_kilos"], " total_barras:", ec["total_barras"],
          " saldo:", ec["saldo"])
    suma = sum((D(c["valor_total"]) for c in ec["compras_detalle"]), CERO)
    assert suma == D("230000.00"), f"el detalle suma {suma} y son $230.000"
    assert D(ec["total_comprado"]) == D("230000.00")
    assert D(ec["saldo"]) == D("230000.00")
    assert D(ec["total_kilos"]) == D(100), (
        "los kilos del productor son los 100 de la panela; los 50 huevos no son kilos"
    )
    assert D(ec["total_barras"]) == D(50), (
        f"las 50 unidades de huevo no aparecen en total_barras: {ec['total_barras']}"
    )


def test_i8_el_subproducto_de_los_ajustes_con_un_hermano_nuevo(client, h):
    """Agregar un SEGUNDO subproducto del queso (después de la borona en la lista)
    no le puede mover nada a la borona que ya tiene historia."""
    cat = client.get(PROD, params={"size": 50}, headers=h).json()["items"]
    queso = next(p for p in cat if p["clave"] == "queso")
    assert comprar(client, h, productor="Pedro Perez", tipo="queso",
                   kilos_brutos=100, precio_kilo=20000,
                   borona_kilos=20).status_code == 201
    antes = resumen(client, h)
    producto(client, h, "Migajon", subproducto_de_id=queso["id"])
    despues = resumen(client, h)
    pintar("con un segundo subproducto del queso", despues, ("kilos_pendientes",))
    assert D(existencia(despues, "borona")["disponible"]) == \
        D(existencia(antes, "borona")["disponible"])
    for campo in ("total_compras", "ganancia_estimada", "kilos_pendientes"):
        assert D(despues[campo]) == D(antes[campo]), (
            f"agregar un subproducto movió {campo}"
        )
    regla_de_oro(despues, "segundo subproducto")


def test_i9_vender_dos_productos_en_una_misma_factura(client, h):
    """Una factura con renglones de DOS productos distintos, uno por kilo y otro
    por unidad: cada uno contra su inventario y su plata en su fila."""
    producto(client, h, "Panela")
    producto(client, h, "Huevo", unidad="unidad")
    assert comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=100,
                   precio_kilo=2000).status_code == 201
    assert comprar(client, h, productor="Patricia", tipo="huevo", barras=50,
                   precio_barra=600).status_code == 201
    r = client.post(f"{API}/documentos",
                    json={"tipo": "venta", "fecha": "2026-03-05",
                          "tercero": "Don Jose",
                          "renglones": [
                              {"tipo": "panela", "kilos": 60, "precio_kilo": 3000},
                              {"tipo": "huevo", "barras": 60, "precio_barra": 900},
                          ]},
                    headers=h)
    print("\nfactura con 60 kg de panela y 60 huevos (hay 50) ->", r.status_code,
          detalle(r))
    assert r.status_code == 422, "la factura despachó 60 huevos de 50"
    assert "Huevo" in detalle(r)
    res = resumen(client, h)
    assert D(existencia(res, "panela")["disponible"]) == D(100), (
        "el renglón de panela se escribió aunque la factura no era válida"
    )
    assert D(res["total_ventas"]) == CERO

    r = client.post(f"{API}/documentos",
                    json={"tipo": "venta", "fecha": "2026-03-05",
                          "tercero": "Don Jose",
                          "renglones": [
                              {"tipo": "panela", "kilos": 60, "precio_kilo": 3000},
                              {"tipo": "huevo", "barras": 40, "precio_barra": 900},
                          ]},
                    headers=h)
    assert r.status_code == 201, detalle(r)
    res = resumen(client, h)
    pintar("factura de dos productos distintos", res,
           ("total_ventas", "ganancia_estimada"))
    assert D(existencia(res, "panela")["disponible"]) == D(40)
    assert D(existencia(res, "huevo")["disponible"]) == D(10)
    assert D(fila(res, "panela")["ingreso"]) == D("180000.00")
    assert D(fila(res, "panela")["costo"]) == D("120000.00")
    assert D(fila(res, "huevo")["ingreso"]) == D("36000.00")
    assert D(fila(res, "huevo")["costo"]) == D("24000.00")
    regla_de_oro(res, "factura de dos productos")


def test_i10_crear_el_subproducto_ya_de_primero(client, h):
    cat = client.get(PROD, params={"size": 50}, headers=h).json()["items"]
    queso = next(p for p in cat if p["clave"] == "queso")
    assert comprar(client, h, productor="Pedro Perez", tipo="queso",
                   kilos_brutos=100, precio_kilo=20000,
                   borona_kilos=20).status_code == 201
    antes = resumen(client, h)
    print("\nborona antes:", existencia(antes, "borona")["disponible"])
    r = client.post(PROD, json={"nombre": "Migajon", "unidad": "kg",
                                "subproducto_de_id": queso["id"], "orden": 0},
                    headers=h)
    print("crear 'Migajon' con orden=0 ->", r.status_code, detalle(r))
    assert r.status_code == 201, detalle(r)
    despues = resumen(client, h)
    pintar("con Migajon creado de primero", despues, ("kilos_pendientes",))
    assert D(existencia(despues, "borona")["disponible"]) == \
        D(existencia(antes, "borona")["disponible"]), (
        "crear un subproducto con orden=0 le quito los kilos gratis a la borona"
    )
