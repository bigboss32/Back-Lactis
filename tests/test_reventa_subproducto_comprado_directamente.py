"""UN SUBPRODUCTO QUE SE COMPRA DIRECTAMENTE TIENE SU PROPIO COSTO.

LAS DOS COSAS CONVIVEN EN EL MISMO PRODUCTO, y esa es toda la prueba:

  · LO QUE LLEGA GRATIS CON SU PADRE HEREDA EL COSTO DEL PADRE. Eso es lo que
    significa la marca del catálogo: la borona que viene encima del lote de queso no
    se paga (cuesta cero) y la que sale de desmenuzar queso arrastra el costo del
    queso del que salió.
  · LO QUE SE COMPRA DIRECTAMENTE CUESTA LO QUE SE PAGÓ POR ÉL. Si el dueño le compra
    50 kg de borona a un productor, esa compra tiene su propio precio por kilo y entra
    a su propia cola del FIFO como cualquier otro producto.

QUÉ PASABA ANTES, MEDIDO. Comprar borona directamente ya dejaba venderla —antes de eso
la venta rebotaba con "Solo hay 0.00 kg"—, pero el COSTEO no se había enterado: el
desglose tenía un solo pozo por grupo y sus destinos eran "lo vendido de la raíz" y "lo
convertido al subproducto", así que la compra del subproducto entraba al pozo sin que
ningún destino la consumiera. Resultado con 50 kg de borona comprados a $1.000 y
vendidos a $2.000:

  · los $50.000 de esa compra se anotaban en "Aún en inventario" —la fila del QUESO—,
    como si hubiera 50 kg de queso que nadie compró;
  · la borona vendida salía con ganancia PURA de $100.000;
  · `kilos_pendientes` decía 50,00 de una mercancía que no existe;
  · y el panel de lotes, que sí le lleva una cola de inventario a cada producto, decía
    un costo distinto para la MISMA venta. Dos pantallas que el dueño cruza a mano.
"""
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PROD = f"{API}/productos"
PERIODO = {"desde": "2026-01-01", "hasta": "2026-12-31"}
CERO = Decimal("0")


def D(v) -> Decimal:
    return Decimal(str(v))


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


def compra(client, h, **campos):
    r = client.post(f"{API}/compras", json={"fecha": "2026-03-01", **campos}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, h, **campos):
    r = client.post(f"{API}/ventas", json={"fecha": "2026-03-10", **campos}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def resumen(client, h) -> dict:
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def fila(res, clave) -> dict:
    filas = [f for f in res["por_producto"] if f["producto"] == clave]
    assert len(filas) == 1, (
        f"se esperaba UNA fila de '{clave}' y hay {len(filas)}: "
        + str([f["producto"] for f in res["por_producto"]])
    )
    return filas[0]


def existencia(res, clave) -> Decimal:
    return D(next(e for e in res["existencias"] if e["producto"] == clave)["disponible"])


def regla_de_oro(res, titulo) -> None:
    for campo, columna in (("total_compras", "costo"), ("total_ventas", "ingreso"),
                           ("total_gastos", "gastos"),
                           ("ganancia_estimada", "ganancia")):
        suma = sum((D(f[columna]) for f in res["por_producto"]), CERO)
        assert suma == D(res[campo]), (
            f"{titulo}: la columna '{columna}' suma {suma} y '{campo}' dice "
            f"{res[campo]}"
        )


def pintar(titulo, res) -> None:
    print(f"\n===== {titulo} =====")
    for campo in ("total_compras", "total_ventas", "ganancia_estimada",
                  "kilos_pendientes"):
        print(f"   {campo:24} = {res[campo]}")
    for f in res["por_producto"]:
        print(f"     {f['producto']:22} {f['etiqueta']:40} kilos={f['kilos']:>9} "
              f"costo={f['costo']:>14} ingreso={f['ingreso']:>14} "
              f"ganancia={f['ganancia']:>14}")
    for e in res["existencias"]:
        print(f"     existencia {e['producto']:18} {e['unidad']:7} {e['disponible']:>10}")


def lotes_de(client, h) -> dict:
    r = client.get(f"{API}/lotes", headers=h)
    assert r.status_code == 200, r.text
    salida = {}
    for lote in r.json()["lotes"]:
        for c in lote["detalle_compras"]:
            salida[f"{lote['fecha']} {c['productor']}"] = {
                k: D(c[k]) for k in (
                    "kilos", "borona_recibida", "kilos_vendidos", "kilos_a_borona",
                    "kilos_merma", "kilos_sin_vender", "borona_vendida",
                    "borona_sin_vender", "costo_sin_vender", "ingresos",
                    "costo_realizado", "ganancia",
                )
            }
    return salida


# ====================================================== las dos cosas, en una historia
def test_lo_gratis_hereda_y_lo_comprado_cuesta_lo_suyo(client, h):
    """LA HISTORIA COMPLETA, con las dos clases de borona conviviendo.

        100,00 kg de queso a $20.000     = $2.000.000, con 20,00 kg de borona GRATIS
         10,00 kg de queso pasados a borona (arrastran $20.000/kg = $200.000)
         50,00 kg de BORONA comprados a $1.000 = $50.000
         50,00 kg de queso vendidos a $25.000 = $1.250.000
         60,00 kg de borona vendidos a $3.000 =   $180.000

    La borona vendió 60 kg: 20 llegaron gratis (cuestan cero), 10 vinieron convertidos
    (ya los costeó el pozo del queso, $200.000) y 30 salieron de SU propia compra
    ($1.000 el kilo = $30.000). Le quedan 20 kg en bodega, que son los que le sobran de
    su compra y valen $20.000.
    """
    compra(client, h, productor="Pedro Perez", tipo="queso", kilos_brutos="100.00",
           precio_kilo="20000", borona_kilos="20.00")
    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-05", "kilos": "10.00",
                          "destino": "borona"}, headers=h)
    assert r.status_code == 201, r.text
    compra(client, h, fecha="2026-03-06", productor="Marleny Gomez", tipo="borona",
           kilos_brutos="50.00", precio_kilo="1000")
    venta(client, h, cliente="Tienda Sol", tipo="queso", kilos="50.00",
          precio_kilo="25000")
    venta(client, h, fecha="2026-03-12", cliente="Don Jose", tipo="borona",
          kilos="60.00", precio_kilo="3000")

    res = resumen(client, h)
    pintar("borona gratis + convertida + comprada, y 60 kg vendidos", res)

    # ------------------------------------------------------------------ el queso
    del_queso = fila(res, "queso")
    assert D(del_queso["kilos"]) == D("50.00")
    assert D(del_queso["costo"]) == D("1000000.00")
    assert D(del_queso["ingreso"]) == D("1250000.00")

    # ------------------------------------------------------------------ la borona
    de_borona = fila(res, "borona")
    assert D(de_borona["ingreso"]) == D("180000.00")
    assert D(de_borona["costo"]) == D("230000.00"), (
        "la borona cuesta lo convertido ($200.000, heredado del queso) MÁS lo que se "
        "le compró y se vendió (30 kg × $1.000 = $30.000). Lo que llegó gratis no "
        "cuesta"
    )

    # ---------------------------------------------- lo que quedó, cada uno en su fila
    quedan_queso = fila(res, "pendiente")
    assert D(quedan_queso["kilos"]) == D("40.00"), (
        "el queso pendiente son 100 − 50 vendidos − 10 convertidos"
    )
    assert D(quedan_queso["costo"]) == D("800000.00")
    quedan_borona = fila(res, "borona_pendiente")
    assert D(quedan_borona["kilos"]) == D("20.00")
    assert D(quedan_borona["costo"]) == D("20000.00"), (
        "los 20 kg de borona que quedan son de SU compra y valen lo que se pagó"
    )

    # ------------------------------------------------------------- las existencias
    assert existencia(res, "queso") == D("40.00")
    assert existencia(res, "borona") == D("20.00")
    assert D(res["kilos_pendientes"]) == D("60.00"), (
        "40 kg de queso más 20 de borona: los dos son mercancía pagada que sigue en "
        "bodega"
    )
    regla_de_oro(res, "las dos boronas")

    # ------------------------------------- y las dos pantallas dicen LO MISMO
    porlote = lotes_de(client, h)
    print("\n   lotes:", {k: {kk: str(vv) for kk, vv in v.items()}
                          for k, v in porlote.items()})
    costo_realizado = sum((v["costo_realizado"] for v in porlote.values()), CERO)
    costo_resumen = sum(
        (D(f["costo"]) for f in res["por_producto"]
         if f["producto"] in ("queso", "borona")),
        CERO,
    )
    print(f"\n   costo realizado según lotes = {costo_realizado}   "
          f"según el desglose = {costo_resumen}")
    assert costo_realizado == costo_resumen, (
        "el panel de lotes y el desglose del resumen dicen costos distintos para las "
        "MISMAS ventas"
    )


def test_la_compra_del_subproducto_no_le_inventa_inventario_al_padre(client, h):
    """El caso pelado, que es el que se reprodujo: solo borona comprada y vendida.

    50 kg a $1.000 comprados y vendidos a $2.000. La ganancia son $50.000 y no
    $100.000, y el queso —que nadie compró— no puede aparecer con nada en bodega.
    """
    compra(client, h, productor="Pedro Perez", tipo="borona", kilos_brutos="50.00",
           precio_kilo="1000")
    venta(client, h, cliente="Tienda Sol", tipo="borona", kilos="50.00",
          precio_kilo="2000")

    res = resumen(client, h)
    pintar("50 kg de borona comprados y vendidos", res)
    de_borona = fila(res, "borona")
    assert D(de_borona["ingreso"]) == D("100000.00")
    assert D(de_borona["costo"]) == D("50000.00")
    assert D(de_borona["ganancia"]) == D("50000.00"), (
        "con costo cero, el renglón mostraba ganancia pura de $100.000"
    )
    assert D(fila(res, "pendiente")["kilos"]) == CERO, (
        "el queso no se movió: no puede quedar 'aún en inventario' con kilos"
    )
    assert D(fila(res, "pendiente")["costo"]) == CERO, (
        "los $50.000 de la borona se le estaban anotando al inventario del queso"
    )
    assert D(res["kilos_pendientes"]) == CERO
    assert existencia(res, "borona") == CERO
    regla_de_oro(res, "borona comprada y vendida")


def test_la_borona_comprada_y_no_vendida_queda_con_su_costo(client, h):
    """Comprada y NO vendida: sus kilos y su plata quedan en SU fila de inventario,
    no en la del queso."""
    compra(client, h, productor="Pedro Perez", tipo="borona", kilos_brutos="37.50",
           precio_kilo="1333")
    res = resumen(client, h)
    pintar("37,50 kg de borona comprados y nada vendido", res)
    quedan = fila(res, "borona_pendiente")
    assert D(quedan["kilos"]) == D("37.50")
    assert D(quedan["costo"]) == D("49987.50")  # 37,50 × 1.333
    assert D(fila(res, "pendiente")["kilos"]) == CERO
    assert existencia(res, "borona") == D("37.50")
    assert D(res["kilos_pendientes"]) == D("37.50")
    regla_de_oro(res, "borona comprada y guardada")


def test_el_costo_del_padre_no_se_mezcla_con_el_del_subproducto(client, h):
    """Cada pozo con SU precio. Con el queso a $20.000 y la borona a $1.000 el kilo,
    un pozo compartido daría un promedio de $13.666,67 que no está en ningún recibo."""
    compra(client, h, productor="Pedro Perez", tipo="queso", kilos_brutos="100.00",
           precio_kilo="20000")
    compra(client, h, productor="Marleny Gomez", tipo="borona", kilos_brutos="50.00",
           precio_kilo="1000")
    venta(client, h, cliente="Tienda Sol", tipo="queso", kilos="10.00",
          precio_kilo="26000")
    venta(client, h, cliente="Don Jose", tipo="borona", kilos="10.00",
          precio_kilo="2500")

    res = resumen(client, h)
    pintar("queso a $20.000 y borona a $1.000, 10 kg vendidos de cada uno", res)
    assert D(fila(res, "queso")["costo"]) == D("200000.00")
    assert D(fila(res, "borona")["costo"]) == D("10000.00"), (
        "los 10 kg de borona cuestan $1.000 el kilo, que es lo que se pagó por ellos"
    )
    assert D(fila(res, "queso")["costo_kilo"]) == D("20000.00")
    assert D(fila(res, "borona")["costo_kilo"]) == D("1000.00"), (
        "el costo por kilo de la fila es el de SU pozo, no un promedio del grupo"
    )
    assert D(fila(res, "pendiente")["costo"]) == D("1800000.00")
    assert D(fila(res, "borona_pendiente")["costo"]) == D("40000.00")
    regla_de_oro(res, "dos pozos con precios distintos")


def test_en_el_panel_de_lotes_los_cuatro_destinos_suman_los_kilos_comprados(client, h):
    """LA CUENTA DEL PANEL DE LOTES: vendidos + a borona + merma + sin vender = kilos.

    Es la columna que el dueño suma con la calculadora en la pantalla de ganancia por
    lote, y se rompía con una compra directa de borona: esos 50 kg pagados se anotaban
    en las columnas de lo que llega GRATIS (`borona_vendida` / `borona_sin_vender`),
    así que la compra salía con `kilos = 50` y sus cuatro destinos sumando cero.

    Ahora la clase la dice el trozo de inventario y no el producto: lo que la compra
    pagó va con lo vendido, y lo que llegó gratis o salió de convertir va con lo del
    subproducto.
    """
    compra(client, h, productor="Pedro Perez", tipo="queso", kilos_brutos="100.00",
           precio_kilo="20000", borona_kilos="20.00")
    compra(client, h, fecha="2026-03-06", productor="Marleny Gomez", tipo="borona",
           kilos_brutos="50.00", precio_kilo="1000")
    venta(client, h, cliente="Tienda Sol", tipo="queso", kilos="60.00",
          precio_kilo="25000")
    venta(client, h, fecha="2026-03-12", cliente="Don Jose", tipo="borona",
          kilos="30.00", precio_kilo="3000")

    porlote = lotes_de(client, h)
    print("\n===== el panel de lotes =====")
    for nombre, cifras in porlote.items():
        print(f"   {nombre}")
        for campo, valor in cifras.items():
            print(f"      {campo:22} = {valor}")
        cuatro = (
            cifras["kilos_vendidos"] + cifras["kilos_a_borona"]
            + cifras["kilos_merma"] + cifras["kilos_sin_vender"]
        )
        assert cuatro == cifras["kilos"], (
            f"{nombre}: los cuatro destinos suman {cuatro} y la compra fue de "
            f"{cifras['kilos']} kg"
        )
        gratis = cifras["borona_vendida"] + cifras["borona_sin_vender"]
        assert gratis == cifras["borona_recibida"], (
            f"{nombre}: lo que llegó gratis fueron {cifras['borona_recibida']} kg y "
            f"las columnas del subproducto suman {gratis}"
        )
    # Y EL FIFO REPARTIÓ COMO TIENE QUE REPARTIR: de los 30 kg de borona vendidos, los
    # 20 primeros salieron de la borona GRATIS que llegó el 1 de marzo con el lote de
    # Pedro (por eso su `borona_vendida` son 20 y no cuestan nada) y los 10 restantes
    # de la compra de Marleny del 6, que sí se pagó. Lo viejo primero, como en la
    # bodega.
    de_pedro = porlote["2026-03-01 Pedro Perez"]
    assert de_pedro["borona_vendida"] == D("20.00")
    assert de_pedro["borona_sin_vender"] == CERO
    de_marleny = porlote["2026-03-06 Marleny Gomez"]
    assert de_marleny["kilos_vendidos"] == D("10.00"), (
        "la borona COMPRADA que se vendió es mercancía pagada: va con lo vendido"
    )
    assert de_marleny["kilos_sin_vender"] == D("40.00")
    assert de_marleny["borona_vendida"] == CERO, (
        "sus kilos se pagaron: no pueden salir en las columnas de lo que llega gratis"
    )
    assert de_marleny["borona_sin_vender"] == CERO
    assert de_marleny["costo_realizado"] == D("10000.00")

    # Y LAS DOS PANTALLAS DICEN LO MISMO: el desglose le carga a la borona esos mismos
    # $10.000 (30 vendidos − 20 que llegaron gratis = 10 kg pagados a $1.000).
    res = resumen(client, h)
    pintar("30 kg de borona vendidos: 20 gratis y 10 comprados", res)
    assert D(fila(res, "borona")["costo"]) == de_marleny["costo_realizado"]


def test_vender_borona_comprada_baja_su_inventario_y_no_el_del_queso(client, h):
    """Y el guardia de existencias mira el inventario de SU producto."""
    compra(client, h, productor="Pedro Perez", tipo="borona", kilos_brutos="20.00",
           precio_kilo="1000")
    r = client.post(f"{API}/ventas",
                    json={"fecha": "2026-03-10", "cliente": "Don Jose",
                          "tipo": "borona", "kilos": "20.01",
                          "precio_kilo": "2000"}, headers=h)
    print("\n   vender 20,01 kg de borona con 20,00 comprados ->", r.status_code)
    assert r.status_code == 422, "se despacharon 20,01 kg de 20,00"
    venta(client, h, cliente="Don Jose", tipo="borona", kilos="20.00",
          precio_kilo="2000")
    res = resumen(client, h)
    assert existencia(res, "borona") == CERO
    assert existencia(res, "queso") == CERO
    regla_de_oro(res, "el límite exacto de la borona comprada")
