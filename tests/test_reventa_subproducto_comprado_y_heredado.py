"""COMPRADO Y HEREDADO EN EL MISMO PRODUCTO, Y UNA SOLA CUENTA DEL COSTO.

UN SUBPRODUCTO PUEDE TENER KILOS DE TRES ORÍGENES A LA VEZ:

  · los que LLEGARON GRATIS encima de la compra de su padre (no se pagaron: cuestan
    cero);
  · los que le entraron CONVERTIDOS desde su padre (arrastran el costo del padre);
  · los que SE LE COMPRARON directamente (cuestan lo que se pagó por ellos).

EL DEFECTO QUE ESTA PRUEBA CIERRA. Con las tres cosas juntas, las dos pantallas que el
dueño cruza a mano decían costos distintos DE LA MISMA VENTA, porque cada una ordenaba
la salida a su manera: el panel de lotes despachaba por orden de llegada (FIFO puro) y
el desglose del resumen daba por hecho que lo que no se pagó sale primero. Con 20 kg
gratis el 1, 50 kg comprados el 2, 10 kg convertidos el 3 y 55 kg vendidos el 11:

    costo de esa venta según el DESGLOSE  = $225.000
    costo de esa venta según el de LOTES  = $ 35.000     (diferencia: $190.000)

HOY LA CUENTA ES UNA SOLA (`kilos_que_salen_de_lo_pagado`, en `lotes.py`) y las dos
pantallas la llaman: lo que no se pagó sale primero, en las dos. Las cifras de arriba
quedaron en $225.000 y $225.000.

HASTA DÓNDE LLEGA LA IGUALDAD, DICHO DE FRENTE. Las dos pantallas contestan la misma
pregunta sobre el COSTO DE LO VENDIDO y por eso tienen que coincidir en eso. Lo que
siguen siendo dos cosas distintas es CUÁNDO se cobra lo que se CONVIRTIÓ: el desglose es
un informe de período y le carga al renglón del subproducto los kilos el día que se
convierten, mientras el panel de lotes es un libro FIFO que se los cobra el día que se
venden. Cuando todo lo convertido ya se vendió —que es el caso de esta historia— las dos
cifras son la misma; mientras quede convertido en bodega, el desglose va adelantado. Eso
no es una cuenta doble, es la diferencia entre un informe de período y un libro.
"""
import pytest

from tests.ayudas_reventa import (
    CERO, D, PROD, ajuste, compra, existencia, exigir_quieto, fila, foto,
    lotes as pedir_lotes, productos, regla_de_oro, resumen, venta,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


def pintar(res, titulo):
    print(f"\n===== {titulo} =====")
    for f in res["por_producto"]:
        print(f"   {f['producto']:24} {f['etiqueta']:40} kilos={f['kilos']:>10} "
              f"vend={f['kilos_vendidos']:>10} costo={f['costo']:>14} "
              f"ing={f['ingreso']:>14} gan={f['ganancia']:>14}")
    print(f"   total_compras={res['total_compras']}  total_ventas={res['total_ventas']}"
          f"  kilos_pendientes={res['kilos_pendientes']}")
    print("   existencias:", {e["producto"]: e["disponible"] for e in res["existencias"]})


def costo_vendido_segun_lotes(panel) -> "D":
    """Lo que costó todo lo que YA SALIÓ VENDIDO, según el panel de lotes."""
    return sum(
        (D(l["costo_vendido"]) + D(l["costo_borona_vendida"]) for l in panel["lotes"]),
        CERO,
    )


def costo_vendido_segun_el_desglose(client, h, res) -> "D":
    """Lo mismo según el desglose: las filas de venta de los productos QUE SE PESAN.

    Se dejan afuera las filas calculadas (merma y residuos) porque no son ventas, y los
    productos que se cuentan por unidades porque no entran al reparto por lotes (el
    motor de lotes es de kilos de punta a punta, ver `eventos_para_lotes`).
    """
    de_kilos = {
        clave for clave, p in productos(client, h).items() if p["unidad"] == "kg"
    }
    return sum(
        (D(f["costo"]) for f in res["por_producto"] if f["producto"] in de_kilos), CERO
    )


# ==========================================================================
# 1. CADA ORIGEN POR SEPARADO
# ==========================================================================
def test_subproducto_solo_comprado(client, h):
    """Sin nada gratis ni convertido: la borona es un producto más."""
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="20000")
    compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz",
           tipo="borona", kilos_brutos="50.00", precio_kilo="1000")
    venta(client, h, fecha="2026-03-10", cliente="Tienda La Esquina",
          tipo="borona", kilos="50.00", precio_kilo="2000")

    res = resumen(client, h)
    pintar(res, "solo comprado")
    regla_de_oro(res, "solo comprado")
    f = fila(res, "borona")
    assert D(f["costo"]) == D("50000.00"), f
    assert D(f["ingreso"]) == D("100000.00"), f
    assert D(f["ganancia"]) == D("50000.00"), f
    # Los $50.000 NO se pueden estar anotando en "Aún en inventario" del queso.
    assert D(fila(res, "pendiente")["costo"]) == D("2000000.00")
    # Y `kilos_pendientes` no puede reportar 50 kg de una mercancía que no existe.
    assert D(res["kilos_pendientes"]) == D("100.00"), res["kilos_pendientes"]


def test_subproducto_solo_heredado(client, h):
    """Lo que llega gratis y lo que sale de convertir, sin compras propias."""
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="20000", borona_kilos="20.00")
    ajuste(client, h, fecha="2026-03-03", kilos="10.00", destino="borona",
           precio_kilo="3000")
    venta(client, h, fecha="2026-03-10", cliente="Tienda La Esquina",
          tipo="borona", kilos="25.00", precio_kilo="2000")

    res = resumen(client, h)
    pintar(res, "solo heredado")
    regla_de_oro(res, "solo heredado")
    f = fila(res, "borona")
    # Los 10 kg convertidos consumen el pozo del queso a $20.000: $200.000.
    assert D(f["costo"]) == D("200000.00"), f
    assert D(f["ingreso"]) == D("50000.00"), f


# ==========================================================================
# 2. LOS TRES ORÍGENES JUNTOS: LAS DOS PANTALLAS DICEN LO MISMO
# ==========================================================================
def test_las_dos_pantallas_dicen_el_mismo_costo_de_la_misma_venta(client, h):
    """EL DEFECTO DEL ENCABEZADO, con sus cifras.

        100 kg de queso a $20.000, con 20 kg de borona GRATIS   (1 de marzo)
         50 kg de BORONA comprados a $1.000                     (2 de marzo)
         10 kg de queso convertidos a borona                    (3 de marzo)
         60 kg de queso vendidos a $25.000                      (10 de marzo)
         55 kg de borona vendidos a $2.000                      (11 de marzo)

    Los 55 kg de borona salen de lo que no se pagó primero: 20 gratis (cuestan cero) y
    10 convertidos ($20.000 el kilo = $200.000), y los 25 restantes de su propia compra
    ($1.000 el kilo = $25.000). Son $225.000, y esa es la cifra en las DOS pantallas.
    """
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="20000", borona_kilos="20.00")
    compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz",
           tipo="borona", kilos_brutos="50.00", precio_kilo="1000")
    ajuste(client, h, fecha="2026-03-03", kilos="10.00", destino="borona",
           precio_kilo="3000")
    venta(client, h, fecha="2026-03-10", cliente="Don José Pérez", tipo="queso",
          kilos="60.00", precio_kilo="25000")
    venta(client, h, fecha="2026-03-11", cliente="Tienda La Esquina",
          tipo="borona", kilos="55.00", precio_kilo="2000")

    res = resumen(client, h)
    pintar(res, "comprado + heredado")
    regla_de_oro(res, "comprado + heredado")

    f = fila(res, "borona")
    assert D(f["kilos_vendidos"]) == D("55.00")
    assert D(f["costo"]) == D("225000.00"), (
        "el renglón de la borona cobra lo convertido ($200.000, del pozo del queso) "
        "más los 25 kg que salieron de su propia compra ($25.000)"
    )

    panel = pedir_lotes(client, h)
    print("\n   ===== el panel de LOTES para la misma historia =====")
    for l in panel["lotes"]:
        print(f"   {l['fecha']}  vendido={l['costo_vendido']:>14} "
              f"borona_vendida={l['costo_borona_vendida']:>14} "
              f"merma={l['costo_merma']:>12} sin_vender={l['costo_sin_vender']:>14}")
        for c in l["detalle_compras"]:
            print(f"      compra {c['productor']:20} kilos={c['kilos']:>9} "
                  f"vend={c['kilos_vendidos']:>8} a_borona={c['kilos_a_borona']:>8} "
                  f"merma={c['kilos_merma']:>7} sin_vender={c['kilos_sin_vender']:>8} "
                  f"bor_vend={c['borona_vendida']:>7} bor_sin={c['borona_sin_vender']:>7}")

    de_lotes = costo_vendido_segun_lotes(panel)
    del_desglose = costo_vendido_segun_el_desglose(client, h, res)
    print(f"\n   costo de lo vendido según LOTES     = {de_lotes}")
    print(f"   costo de lo vendido según el DESGLOSE = {del_desglose}")
    assert de_lotes == del_desglose == D("1425000.00"), (
        "las dos pantallas del mismo sistema dicen costos distintos de las MISMAS "
        f"ventas: lotes {de_lotes}, desglose {del_desglose}"
    )

    # Y la cuenta del panel de lotes sigue cerrando compra por compra: los cuatro
    # destinos suman los kilos comprados y los costos suman lo que se pagó.
    for l in panel["lotes"]:
        for c in l["detalle_compras"]:
            destinos = (D(c["kilos_vendidos"]) + D(c["kilos_a_borona"])
                        + D(c["kilos_merma"]) + D(c["kilos_sin_vender"]))
            assert destinos == D(c["kilos"]), (
                f"la compra de {c['productor']} tiene {c['kilos']} kg y sus destinos "
                f"suman {destinos}"
            )
            costos = D(c["costo_realizado"]) + D(c["costo_sin_vender"])
            assert costos == D(c["valor_total"]), (
                f"la compra de {c['productor']} valió {c['valor_total']} y sus costos "
                f"suman {costos}"
            )


def test_lo_que_llego_gratis_sale_antes_que_lo_comprado_en_las_dos(client, h):
    """La regla compartida, medida en el sitio donde se ve: quién despacha primero.

    Se venden solo 20 kg de borona, que es exactamente lo que llegó gratis. Ninguna de
    las dos pantallas puede cobrar un peso por esa venta: la compra de borona sigue
    entera en la bodega.
    """
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="20000", borona_kilos="20.00")
    compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz",
           tipo="borona", kilos_brutos="50.00", precio_kilo="1000")
    venta(client, h, fecha="2026-03-11", cliente="Tienda La Esquina",
          tipo="borona", kilos="20.00", precio_kilo="2000")

    res = resumen(client, h)
    pintar(res, "vendido justo lo que llegó gratis")
    regla_de_oro(res, "vendido justo lo que llegó gratis")
    assert D(fila(res, "borona")["costo"]) == CERO, (
        "esos 20 kg llegaron gratis: cobrarlos le inventa un costo a la venta"
    )
    assert D(fila(res, "borona_pendiente")["costo"]) == D("50000.00"), (
        "la compra de borona sigue entera en la bodega, con su plata"
    )
    panel = pedir_lotes(client, h)
    assert costo_vendido_segun_lotes(panel) == costo_vendido_segun_el_desglose(
        client, h, res
    )
    assert existencia(res, "borona") == D("50.00")


# ==========================================================================
# 3. LOS DOS ORÍGENES, CON EL CATÁLOGO MOVIDO DESPUÉS
# ==========================================================================
@pytest.mark.parametrize("operacion", ["crear_sub_orden_0", "reordenar", "renombrar",
                                       "desactivar", "crear_raiz_orden_0"])
def test_comprado_y_heredado_aguanta_el_catalogo(client, h, operacion):
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="317.53", precio_kilo="14317", borona_kilos="18.27")
    compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz",
           tipo="borona", kilos_brutos="53.37", precio_kilo="1133")
    ajuste(client, h, fecha="2026-03-03", kilos="11.11", destino="borona",
           precio_kilo="3000")
    ajuste(client, h, fecha="2026-03-04", kilos="3.33", destino="merma")
    venta(client, h, fecha="2026-03-10", cliente="Don José Pérez", tipo="queso",
          kilos="200.17", precio_kilo="21533", gasto_por_kilo="137")
    venta(client, h, fecha="2026-03-11", cliente="Tienda La Esquina",
          tipo="borona", kilos="61.11", precio_kilo="2017")

    antes = foto(client, h)
    regla_de_oro(resumen(client, h), "antes")
    cat = productos(client, h)

    if operacion == "crear_sub_orden_0":
        r = client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                                    "subproducto_de_id": cat["queso"]["id"],
                                    "orden": 0}, headers=h)
    elif operacion == "crear_raiz_orden_0":
        r = client.post(PROD, json={"nombre": "Panela", "unidad": "kg", "orden": 0},
                        headers=h)
    elif operacion == "reordenar":
        r = client.put(f"{PROD}/{cat['borona']['id']}", json={"orden": 0}, headers=h)
    elif operacion == "renombrar":
        r = client.put(f"{PROD}/{cat['borona']['id']}", json={"nombre": "Merma"},
                       headers=h)
    else:
        r = client.put(f"{PROD}/{cat['borona']['id']}", json={"estado": "inactivo"},
                       headers=h)
    assert r.status_code in (200, 201), r.text

    despues = foto(client, h)
    movidas = [(k, v, despues.get(k)) for k, v in antes.items()
               if k not in despues or despues[k] != v]
    for k, viejo, nuevo in movidas[:30]:
        print(f"   MOVIÓ {k}: {viejo} -> {nuevo}")
    assert not movidas, f"{operacion} movió {len(movidas)} cifras"
    regla_de_oro(resumen(client, h), operacion)


def test_el_desglose_cuadra_con_subproducto_comprado_y_catalogo_tocado(client, h):
    """Las dos cosas juntas: subproducto comprado Y el catálogo movido después."""
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="317.53", precio_kilo="14317", borona_kilos="18.27")
    compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz",
           tipo="borona", kilos_brutos="53.37", precio_kilo="1133")
    ajuste(client, h, fecha="2026-03-03", kilos="11.11", destino="borona",
           precio_kilo="3000")
    ajuste(client, h, fecha="2026-03-04", kilos="3.33", destino="merma")
    venta(client, h, fecha="2026-03-10", cliente="Don José Pérez", tipo="queso",
          kilos="200.17", precio_kilo="21533", gasto_por_kilo="137")
    venta(client, h, fecha="2026-03-11", cliente="Tienda La Esquina",
          tipo="borona", kilos="61.11", precio_kilo="2017")

    antes = foto(client, h)
    regla_de_oro(resumen(client, h), "antes de tocar el catálogo")
    cat = productos(client, h)
    r = client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                                "subproducto_de_id": cat["queso"]["id"],
                                "orden": 0}, headers=h)
    assert r.status_code == 201, r.text
    exigir_quieto(antes, foto(client, h), "crear un subproducto")
    regla_de_oro(resumen(client, h), "después de crear el subproducto")

    client.put(f"{PROD}/{cat['queso']['id']}", json={"orden": 9}, headers=h)
    exigir_quieto(antes, foto(client, h), "reordenar")
    regla_de_oro(resumen(client, h), "después de reordenar")


def test_el_total_de_compras_es_exacto_con_los_dos_origenes(client, h):
    """La cifra grande no se puede mover por tener las dos clases de kilos."""
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="20000", borona_kilos="20.00")
    compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz",
           tipo="borona", kilos_brutos="50.00", precio_kilo="1000")
    ajuste(client, h, fecha="2026-03-03", kilos="10.00", destino="borona",
           precio_kilo="3000")
    venta(client, h, fecha="2026-03-10", cliente="Don José Pérez", tipo="queso",
          kilos="60.00", precio_kilo="25000")
    venta(client, h, fecha="2026-03-11", cliente="Tienda La Esquina", tipo="borona",
          kilos="55.00", precio_kilo="2000")

    res = resumen(client, h)
    assert D(res["total_compras"]) == D("2050000.00"), res["total_compras"]
    assert D(fila(res, "borona")["ingreso"]) == D("110000.00")
    regla_de_oro(res, "borona comprada + heredada")
