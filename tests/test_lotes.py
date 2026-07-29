"""Ganancia POR LOTE de compra: qué dejó cada tanda de queso que se compró.

Un lote son todas las compras de queso de una misma fecha. Las ventas no dicen de
qué lote salió el queso, así que se reparten FIFO (se vende del lote más viejo
primero, porque el queso es perecedero).

LA PRUEBA QUE MANDA es el cuadre: para cada lote,
    costo_vendido + costo_borona_vendida + costo_merma + costo_sin_vender
tiene que dar EXACTAMENTE lo que se pagó por el lote. Cada peso pagado está en uno
de esos cuatro sitios y en ninguno más. Si no cuadrara, habría plata que se
esfuma o que se cuenta dos veces, y el usuario lo detecta con la calculadora.

Los números se imprimen porque él revisa los desgloses a mano.
"""
from datetime import date
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/reventa"


def D(valor):
    return Decimal(str(valor))


def compra(client, headers, **datos):
    r = client.post(f"{API}/compras", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, headers, **datos):
    r = client.post(f"{API}/ventas", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def ajuste(client, headers, **datos):
    r = client.post(f"{API}/conversiones", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def panel(client, headers, **params):
    r = client.get(f"{API}/lotes", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def por_fecha(p):
    return {lote["fecha"]: lote for lote in p["lotes"]}


def comprobar_cuadre(p, etiqueta=""):
    """El cuadre peso a peso de CADA lote. Es la prueba que manda."""
    for lote in p["lotes"]:
        repartido = (
            D(lote["costo_vendido"])
            + D(lote["costo_borona_vendida"])
            + D(lote["costo_merma"])
            + D(lote["costo_sin_vender"])
        )
        assert repartido == D(lote["costo_total"]), (
            f"{etiqueta} lote {lote['fecha']}: los cuatro destinos del costo suman "
            f"{repartido} pero se pagaron {lote['costo_total']}"
        )
        # Y los kilos: los cuatro destinos suman lo comprado
        kilos = (
            D(lote["kilos_vendidos"])
            + D(lote["kilos_a_borona"])
            + D(lote["kilos_merma"])
            + D(lote["kilos_sin_vender"])
        )
        assert kilos == D(lote["kilos_comprados"]), (
            f"{etiqueta} lote {lote['fecha']}: los destinos suman {kilos} kg pero se "
            f"compraron {lote['kilos_comprados']} kg"
        )
        # Y la ganancia es la resta que dice ser
        esperada = (
            D(lote["ingresos"])
            - D(lote["costo_vendido"])
            - D(lote["costo_borona_vendida"])
            - D(lote["costo_merma"])
            - D(lote["gastos"])
        )
        assert D(lote["ganancia"]) == esperada, (
            f"{etiqueta} lote {lote['fecha']}: ganancia {lote['ganancia']} != {esperada}"
        )


# ---------------------------------------------------------------------------
# 1. El caso del usuario: dos lotes, una venta que se parte entre los dos
# ---------------------------------------------------------------------------
def test_una_venta_se_reparte_entre_dos_lotes(client, base_datos):
    """El lote viejo se agota primero y el resto sale del nuevo, cada uno con SU
    costo por kilo. Es lo que hace que los dos lotes tengan margen distinto
    aunque el queso se haya vendido al mismo precio."""
    h = auth_headers(client, "admin.a")
    # Lote A: 1.000 kg a 17.000 = 17.000.000
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="1000", precio_kilo="17000")
    # Lote B: 500 kg a 18.000 = 9.000.000
    compra(client, h, fecha="2026-07-25", productor="Juan Gómez",
           kilos_brutos="500", precio_kilo="18000")
    # Venta 1: 600 kg -> toda del lote A
    venta(client, h, fecha="2026-07-20", cliente="Depósito El Trébol", kilos="600",
          precio_kilo="20000", pagada_de_contado=True)
    # Venta 2: 700 kg -> 400 del A (lo que le queda) y 300 del B, con flete 300/kg
    venta(client, h, fecha="2026-07-27", cliente="Alba Nieto", kilos="700",
          precio_kilo="21000", gasto_concepto="Flete", gasto_por_kilo="300",
          pagada_de_contado=True)

    p = panel(client, h)
    lotes = por_fecha(p)
    print("\n===== 1. UNA VENTA REPARTIDA ENTRE DOS LOTES =====")
    for fecha in ("2026-07-18", "2026-07-25"):
        l = lotes[fecha]
        print(f"  {fecha}: comprado {l['kilos_comprados']} kg a {l['costo_kilo']}/kg"
              f" = {l['costo_total']}")
        print(f"     vendidos {l['kilos_vendidos']} kg | ingreso {l['ingreso_queso']}"
              f" | costo de lo vendido {l['costo_vendido']} | gastos {l['gastos']}")
        print(f"     GANANCIA {l['ganancia']} | sin vender {l['kilos_sin_vender']} kg"
              f" (costo {l['costo_sin_vender']}) | cerrado={l['cerrado']}")

    a, b = lotes["2026-07-18"], lotes["2026-07-25"]
    # Lote A: se vendió completo (600 + 400)
    assert D(a["kilos_vendidos"]) == 1000
    assert D(a["ingreso_queso"]) == 20_400_000  # 12.000.000 + 8.400.000
    assert D(a["costo_vendido"]) == 17_000_000
    assert D(a["gastos"]) == 120_000  # 400/700 del flete de 210.000
    assert D(a["ganancia"]) == 3_280_000
    assert D(a["kilos_sin_vender"]) == 0 and a["cerrado"] is True
    # Lote B: solo salieron 300 de sus 500
    assert D(b["kilos_vendidos"]) == 300
    assert D(b["ingreso_queso"]) == 6_300_000
    assert D(b["costo_vendido"]) == 5_400_000
    assert D(b["gastos"]) == 90_000
    assert D(b["ganancia"]) == 810_000
    assert D(b["kilos_sin_vender"]) == 200
    assert D(b["costo_sin_vender"]) == 3_600_000
    assert b["cerrado"] is False
    # El total y el cuadre
    assert D(p["total_ganancia"]) == 4_090_000
    comprobar_cuadre(p, "caso 1")
    # Nadie se quedó sin lote
    assert D(p["kilos_sin_lote"]) == 0


def test_el_margen_por_kilo_distingue_el_lote_bueno_del_malo(client, base_datos):
    """Es para lo que sirve la pantalla: el mismo queso vendido al mismo precio
    deja distinto según a cómo se compró."""
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="500", precio_kilo="16000")
    compra(client, h, fecha="2026-07-25", productor="Juan Gómez",
           kilos_brutos="500", precio_kilo="20000")
    # Las dos ventas al MISMO precio, una por lote
    venta(client, h, fecha="2026-07-20", cliente="Alba Nieto", kilos="500",
          precio_kilo="21000", pagada_de_contado=True)
    venta(client, h, fecha="2026-07-27", cliente="Alba Nieto", kilos="500",
          precio_kilo="21000", pagada_de_contado=True)

    lotes = por_fecha(panel(client, h))
    print("\n===== 2. EL LOTE BUENO Y EL MALO =====")
    for fecha in ("2026-07-18", "2026-07-25"):
        l = lotes[fecha]
        print(f"  {fecha}: comprado a {l['costo_kilo']}/kg, vendido a"
              f" {l['precio_venta_kilo']}/kg -> margen {l['margen_kilo']}/kg"
              f" (ganancia {l['ganancia']})")
    assert D(lotes["2026-07-18"]["margen_kilo"]) == 5000
    assert D(lotes["2026-07-25"]["margen_kilo"]) == 1000
    assert D(lotes["2026-07-18"]["ganancia"]) == 2_500_000
    assert D(lotes["2026-07-25"]["ganancia"]) == 500_000


# ---------------------------------------------------------------------------
# 3. Borona: la que llega gratis y la que sale de pasar queso a borona
# ---------------------------------------------------------------------------
def test_la_borona_gratis_es_ganancia_pura_del_lote(client, base_datos):
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="500", borona_kilos="42", precio_kilo="18000")
    venta(client, h, fecha="2026-07-20", cliente="Alba Nieto", kilos="500",
          precio_kilo="21000", pagada_de_contado=True)
    venta(client, h, fecha="2026-07-21", cliente="La Ganancia", tipo="borona",
          kilos="42", precio_kilo="8000", pagada_de_contado=True)

    p = panel(client, h)
    l = p["lotes"][0]
    print("\n===== 3. BORONA QUE LLEGA GRATIS =====")
    print(f"  recibida {l['borona_recibida']} kg | vendida {l['borona_vendida']} kg"
          f" | ingreso {l['ingreso_borona']} | costo {l['costo_borona_vendida']}")
    print(f"  ganancia del lote {l['ganancia']} (queso 1.500.000 + borona 336.000)")
    assert D(l["borona_recibida"]) == 42
    assert D(l["borona_vendida"]) == 42
    assert D(l["ingreso_borona"]) == 336_000
    # Cuesta CERO: no se paga, así que lo que deja es ganancia pura
    assert D(l["costo_borona_vendida"]) == 0
    assert D(l["ganancia"]) == 1_836_000  # 1.500.000 del queso + 336.000 de borona
    assert D(l["borona_sin_vender"]) == 0
    comprobar_cuadre(p, "borona gratis")
    # Y no toca los kilos de queso comprados: la borona no se compró
    assert D(l["kilos_comprados"]) == 500


def test_el_queso_pasado_a_borona_se_lleva_su_costo(client, base_datos):
    """Si la borona que sale de queso entrara con costo cero, la plata del lote
    desaparecería: se habrían pagado 100 kg a 18.000 y de repente no costarían
    nada. Tiene que arrastrar el costo del queso del que salió."""
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    # 100 kg de queso se dañan y se pasan a borona
    ajuste(client, h, fecha="2026-07-19", kilos="100", destino="borona")
    # Se vende el queso que queda y la borona a precio de borona
    venta(client, h, fecha="2026-07-20", cliente="Alba Nieto", kilos="400",
          precio_kilo="21000", pagada_de_contado=True)
    venta(client, h, fecha="2026-07-21", cliente="La Ganancia", tipo="borona",
          kilos="100", precio_kilo="8000", pagada_de_contado=True)

    p = panel(client, h)
    l = p["lotes"][0]
    print("\n===== 4. QUESO PASADO A BORONA =====")
    print(f"  pasado a borona {l['kilos_a_borona']} kg | borona vendida"
          f" {l['borona_vendida']} kg por {l['ingreso_borona']}")
    print(f"  costo de esa borona {l['costo_borona_vendida']} (100 kg x 18.000)")
    print(f"  ganancia del lote {l['ganancia']}")
    assert D(l["kilos_a_borona"]) == 100
    assert D(l["borona_vendida"]) == 100
    assert D(l["ingreso_borona"]) == 800_000
    # Arrastra el costo del queso: 100 x 18.000
    assert D(l["costo_borona_vendida"]) == 1_800_000
    # 400 kg de queso: 8.400.000 - 7.200.000 = 1.200.000; borona: 800.000 - 1.800.000
    assert D(l["ganancia"]) == 200_000
    comprobar_cuadre(p, "queso a borona")


def test_la_merma_es_perdida_del_lote_que_la_sufrio(client, base_datos):
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    compra(client, h, fecha="2026-07-25", productor="Juan Gómez",
           kilos_brutos="500", precio_kilo="18000")
    # Se venden 450 del A y luego se pierden 50: los 50 son del A (FIFO)
    venta(client, h, fecha="2026-07-20", cliente="Alba Nieto", kilos="450",
          precio_kilo="21000", pagada_de_contado=True)
    ajuste(client, h, fecha="2026-07-22", kilos="50", destino="merma")

    p = panel(client, h)
    lotes = por_fecha(p)
    a, b = lotes["2026-07-18"], lotes["2026-07-25"]
    print("\n===== 5. LA MERMA ES DEL LOTE QUE LA SUFRIÓ =====")
    print(f"  18/07: merma {a['kilos_merma']} kg (costo {a['costo_merma']})"
          f" ganancia {a['ganancia']}")
    print(f"  25/07: merma {b['kilos_merma']} kg | sin vender {b['kilos_sin_vender']} kg")
    assert D(a["kilos_merma"]) == 50
    assert D(a["costo_merma"]) == 900_000
    # 9.450.000 de venta - 8.100.000 de costo de lo vendido - 900.000 de merma
    assert D(a["ganancia"]) == 450_000
    assert a["cerrado"] is True  # no le queda nada
    # El lote nuevo no cargó con la merma del viejo
    assert D(b["kilos_merma"]) == 0
    assert D(b["kilos_sin_vender"]) == 500
    comprobar_cuadre(p, "merma")


# ---------------------------------------------------------------------------
# 6. Lo que no se puede esconder: vender más de lo comprado
# ---------------------------------------------------------------------------
def test_anular_una_compra_ya_vendida_deja_kilos_sin_lote(client, base_datos):
    """El backend NO deja vender mas de lo disponible, asi que "sin lote" solo
    puede aparecer por un camino: anular (o rebajar) una compra DESPUES de haber
    vendido contra ella. Pasa de verdad cuando se cae en cuenta de que una compra
    estaba mal cargada.

    Cuando pasa, la pantalla NO puede repartir esos kilos entre los lotes que si
    existen, porque les inventaria un costo que nadie pago. Los declara aparte.
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastian Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    mal_cargada = compra(client, h, fecha="2026-07-19", productor="Juan Gomez",
                         kilos_brutos="300", precio_kilo="19000")
    # Se venden 700: 500 del primero y 200 del segundo
    venta(client, h, fecha="2026-07-20", cliente="Alba Nieto", kilos="700",
          precio_kilo="21000", pagada_de_contado=True)
    # Y despues se anula la segunda compra
    r = client.post(f"{API}/compras/{mal_cargada['id']}/anular", headers=h)
    assert r.status_code == 200, r.text

    p = panel(client, h)
    l = p["lotes"][0]
    print("\n===== 6. ANULAR UNA COMPRA YA VENDIDA =====")
    print(f"  queda un lote: {l['fecha']} con {l['kilos_comprados']} kg")
    print(f"  vendidos del lote {l['kilos_vendidos']} kg -> {l['ingreso_queso']}")
    print(f"  SIN LOTE: {p['kilos_sin_lote']} kg por {p['ingreso_sin_lote']}")
    assert len(p["lotes"]) == 1
    assert D(l["kilos_vendidos"]) == 500
    # Solo la parte de la plata que corresponde a los kilos cubiertos:
    # 500/700 de 14.700.000 = 10.500.000
    assert D(l["ingreso_queso"]) == 10_500_000
    assert D(p["kilos_sin_lote"]) == 200
    assert D(p["ingreso_sin_lote"]) == 4_200_000
    # La suma de las dos partes es la venta completa: no se pierde ni un peso
    assert D(l["ingreso_queso"]) + D(p["ingreso_sin_lote"]) == 14_700_000
    comprobar_cuadre(p, "sin lote")


def test_el_guardia_de_inventario_no_mira_fechas(client, base_datos):
    """Dos comportamientos distintos que conviene tener escritos, porque juntos
    explican cuándo puede aparecer "sin lote".

    - Vender MÁS de lo que hay se rechaza: es la primera línea de defensa y por eso
      "sin lote" es raro.
    - Vender con una fecha ANTERIOR a la compra se acepta, porque el guardia mira
      el total histórico (comprado - vendido - ajustado) y no las fechas. Es
      razonable —una venta se puede registrar tarde— pero deja el caso en que la
      venta ocurre antes de que exista el lote, y ahí el reparto tiene que decirlo
      en vez de cargársela al lote posterior.
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastian Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    r = client.post(f"{API}/ventas", headers=h, json={
        "fecha": "2026-07-20", "cliente": "Alba Nieto", "kilos": "700",
        "precio_kilo": "21000", "pagada_de_contado": True,
    })
    print("\n===== 7. EL GUARDIA DE INVENTARIO =====")
    print(f"  vender 700 teniendo 500: {r.status_code} |"
          f" {r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "disponibles" in r.json()["error"]["detail"]

    # Con fecha anterior a la compra SÍ pasa: el guardia no mira fechas
    venta(client, h, fecha="2026-07-10", cliente="Alba Nieto", kilos="100",
          precio_kilo="21000", pagada_de_contado=True)
    p = panel(client, h)
    l = p["lotes"][0]
    print(f"  vender 100 el 10/07 (antes de la compra del 18): aceptada")
    print(f"  el reparto lo declara: sin lote {p['kilos_sin_lote']} kg por"
          f" {p['ingreso_sin_lote']}")
    print(f"  y el lote del 18 sigue con sus {l['kilos_sin_vender']} kg sin vender")
    assert D(p["kilos_sin_lote"]) == 100
    assert D(p["ingreso_sin_lote"]) == 2_100_000
    # El lote posterior NO absorbe esa venta: sus 500 kg siguen enteros
    assert D(l["kilos_vendidos"]) == 0
    assert D(l["kilos_sin_vender"]) == 500
    comprobar_cuadre(p, "venta anticipada")


def test_comprar_y_vender_el_mismo_dia(client, base_datos):
    """Lo normal es comprar en la mañana y despachar en la tarde. Si la venta se
    procesara antes de la compra del mismo día, se iría a "sin lote" sin razón."""
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    venta(client, h, fecha="2026-07-18", cliente="Alba Nieto", kilos="500",
          precio_kilo="21000", pagada_de_contado=True)

    p = panel(client, h)
    l = p["lotes"][0]
    print("\n===== 8. COMPRA Y VENTA EL MISMO DÍA =====")
    print(f"  vendidos {l['kilos_vendidos']} kg | sin lote {p['kilos_sin_lote']} kg"
          f" | ganancia {l['ganancia']}")
    assert D(l["kilos_vendidos"]) == 500
    assert D(p["kilos_sin_lote"]) == 0
    assert D(l["ganancia"]) == 1_500_000
    comprobar_cuadre(p, "mismo día")


# ---------------------------------------------------------------------------
# 9. Con las cifras REALES del usuario, que no son redondas
# ---------------------------------------------------------------------------
def test_cuadra_con_las_cifras_reales_del_usuario(client, base_datos):
    """Los kilos y los precios de verdad: 823, 802, 946, 693, 68, 54, 12 kg a
    precios distintos. Aquí es donde el redondeo puede dejar la columna un peso
    por debajo de la cifra grande, que es lo que él nota con la calculadora.
    """
    h = auth_headers(client, "admin.a")
    # Lote del 18: seis productores
    # La borona que llega con los lotes va con decimales a proposito (23,7 + 9,7
    # = 33,4): es el caso en que el reparto proporcional puede dejar centavos.
    for productor, kilos, precio, borona in [
        ("Patricia", "12", "12800", "0"), ("Rubiela", "68", "15200", "0"),
        ("Sebastian", "946", "18100", "23.7"), ("Juan", "693", "17400", "9.7"),
        ("Prieto", "54", "15200", "0"), ("Yeffer", "115", "14900", "0"),
    ]:
        compra(client, h, fecha="2026-07-18", productor=productor,
               kilos_brutos=kilos, precio_kilo=precio, borona_kilos=borona)
    # Lote del 25: dos productores
    compra(client, h, fecha="2026-07-25", productor="Juan",
           kilos_brutos="823", precio_kilo="18000")
    compra(client, h, fecha="2026-07-25", productor="Sebastian",
           kilos_brutos="802", precio_kilo="18800")
    # Ventas que cruzan los dos lotes, con fletes y kilos con decimales
    venta(client, h, fecha="2026-07-21", cliente="Depósito El Trébol", kilos="1200.5",
          precio_kilo="20300", gasto_concepto="Flete", gasto_por_kilo="287.5",
          pagada_de_contado=True)
    venta(client, h, fecha="2026-07-28", cliente="Alba Nieto", kilos="1100.75",
          precio_kilo="21150", gasto_concepto="Flete", gasto_por_kilo="312.5")
    venta(client, h, fecha="2026-07-29", cliente="La Ganancia", tipo="borona",
          kilos="33.4", precio_kilo="8200", pagada_de_contado=True)

    p = panel(client, h)
    lotes = por_fecha(p)
    print("\n===== 9. CIFRAS REALES =====")
    for fecha in sorted(lotes):
        l = lotes[fecha]
        print(f"  {fecha}: {l['compras']} compras, {l['kilos_comprados']} kg,"
              f" costo {l['costo_total']} ({l['costo_kilo']}/kg)")
        print(f"     vendidos {l['kilos_vendidos']} kg -> {l['ingreso_queso']}"
              f" | costo vendido {l['costo_vendido']} | gastos {l['gastos']}")
        print(f"     sin vender {l['kilos_sin_vender']} kg (costo"
              f" {l['costo_sin_vender']}) | GANANCIA {l['ganancia']}")
        cuatro = (D(l["costo_vendido"]) + D(l["costo_borona_vendida"])
                  + D(l["costo_merma"]) + D(l["costo_sin_vender"]))
        print(f"     cuadre: {cuatro} == {l['costo_total']}")
    print(f"  TOTAL ganancia {p['total_ganancia']} | costo {p['total_costo']}"
          f" | sin lote {p['kilos_sin_lote']} kg")

    # El cuadre peso a peso con cifras que no son redondas
    comprobar_cuadre(p, "cifras reales")
    # Los totales son la suma exacta de los lotes
    assert D(p["total_ganancia"]) == sum(D(l["ganancia"]) for l in p["lotes"])
    assert D(p["total_costo"]) == sum(D(l["costo_total"]) for l in p["lotes"])
    assert D(p["total_kilos_comprados"]) == sum(D(l["kilos_comprados"]) for l in p["lotes"])
    # Los costos: 12x12800 + 68x15200 + 946x18100 + 693x17400 + 54x15200 + 115x14900
    assert D(lotes["2026-07-18"]["costo_total"]) == (
        153_600 + 1_033_600 + 17_122_600 + 12_058_200 + 820_800 + 1_713_500
    )
    assert D(lotes["2026-07-25"]["costo_total"]) == 14_814_000 + 15_077_600
    # Y toda la plata de las ventas está repartida o declarada sin lote
    ingresos_repartidos = sum(D(l["ingresos"]) for l in p["lotes"])
    total_vendido = (
        D("1200.5") * 20300 + D("1100.75") * 21150 + D("33.4") * 8200
    )
    assert ingresos_repartidos + D(p["ingreso_sin_lote"]) == total_vendido


def test_el_por_pagar_es_del_lote_no_de_la_cartera(client, base_datos):
    """Cada lote dice lo que falta pagarles a SUS productores. Es exacto, no
    repartido: el saldo está en cada compra."""
    h = auth_headers(client, "admin.a")
    c1 = compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
                kilos_brutos="500", precio_kilo="18000")
    compra(client, h, fecha="2026-07-25", productor="Juan Gómez",
           kilos_brutos="500", precio_kilo="18000")
    # Se le paga completo al del 18 y nada al del 25
    r = client.post(f"{API}/compras/{c1['id']}/abonos",
                    json={"fecha": "2026-07-19", "valor": "9000000"}, headers=h)
    assert r.status_code == 200, r.text

    lotes = por_fecha(panel(client, h))
    print("\n===== 10. POR PAGAR DE CADA LOTE =====")
    for fecha in sorted(lotes):
        print(f"  {fecha}: falta pagar {lotes[fecha]['por_pagar']}")
    assert D(lotes["2026-07-18"]["por_pagar"]) == 0
    assert D(lotes["2026-07-25"]["por_pagar"]) == 9_000_000


def test_anuladas_y_borradas_no_son_lote(client, base_datos):
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    anulada = compra(client, h, fecha="2026-07-20", productor="Juan Gómez",
                     kilos_brutos="300", precio_kilo="19000")
    borrada = compra(client, h, fecha="2026-07-22", productor="Prieto",
                     kilos_brutos="200", precio_kilo="16000")
    assert client.post(f"{API}/compras/{anulada['id']}/anular", headers=h).status_code == 200
    assert client.delete(f"{API}/compras/{borrada['id']}", headers=h).status_code == 204

    p = panel(client, h)
    print("\n===== 11. ANULADAS Y BORRADAS =====")
    print(f"  lotes: {[l['fecha'] for l in p['lotes']]}"
          f" | kilos {p['total_kilos_comprados']}")
    assert [l["fecha"] for l in p["lotes"]] == ["2026-07-18"]
    assert D(p["total_kilos_comprados"]) == 500
    comprobar_cuadre(p, "anuladas")


def test_el_filtro_recorta_la_vista_pero_no_el_calculo(client, base_datos):
    """Si el filtro recortara el cálculo, el inventario inicial sería inventado y
    las ventas del primer día del rango se irían a "sin lote"."""
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-06-10", productor="Sebastián Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    compra(client, h, fecha="2026-07-18", productor="Juan Gómez",
           kilos_brutos="500", precio_kilo="17000")
    # Esta venta se lleva TODO el lote de junio y 100 kg del de julio
    venta(client, h, fecha="2026-07-20", cliente="Alba Nieto", kilos="600",
          precio_kilo="21000", pagada_de_contado=True)

    completo = panel(client, h)
    solo_julio = panel(client, h, desde="2026-07-01", hasta="2026-07-31")
    print("\n===== 12. EL FILTRO NO CAMBIA EL CÁLCULO =====")
    print(f"  completo: {[l['fecha'] for l in completo['lotes']]}")
    print(f"  solo julio: {[l['fecha'] for l in solo_julio['lotes']]}")
    lote_julio_completo = por_fecha(completo)["2026-07-18"]
    lote_julio_filtrado = por_fecha(solo_julio)["2026-07-18"]
    print(f"  julio vendidos: completo={lote_julio_completo['kilos_vendidos']}"
          f" filtrado={lote_julio_filtrado['kilos_vendidos']}")
    # El lote de julio dice lo MISMO con y sin filtro: solo 100 kg suyos salieron
    assert D(lote_julio_filtrado["kilos_vendidos"]) == 100
    assert D(lote_julio_completo["kilos_vendidos"]) == 100
    assert lote_julio_filtrado == lote_julio_completo
    # Y no se inventó nada sin lote por haber filtrado
    assert D(solo_julio["kilos_sin_lote"]) == 0
    assert len(solo_julio["lotes"]) == 1 and len(completo["lotes"]) == 2


def test_no_cruza_empresas(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    compra(client, ha, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    pb = panel(client, hb)
    print("\n===== 13. AISLAMIENTO =====")
    print(f"  A: {len(panel(client, ha)['lotes'])} lotes | B: {len(pb['lotes'])} lotes")
    assert pb["lotes"] == []
    assert D(pb["total_ganancia"]) == 0
    assert D(pb["kilos_sin_lote"]) == 0


def test_sin_movimientos_no_revienta(client, base_datos):
    h = auth_headers(client, "admin.a")
    p = panel(client, h)
    print("\n===== 14. SIN MOVIMIENTOS =====")
    print(f"  lotes={len(p['lotes'])} ganancia={p['total_ganancia']}"
          f" mejor={p['mejor']}")
    assert p["lotes"] == []
    assert D(p["total_ganancia"]) == 0
    assert p["mejor"] is None and p["peor"] is None


def test_los_lotes_exigen_permiso(client, base_datos, db_session):
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.modules.usuarios.models import Rol, Usuario
    from tests.conftest import PASSWORD

    # "Consulta" tiene reventa:consultar, así que SÍ puede ver los lotes
    rol = db_session.scalars(select(Rol).where(Rol.nombre == "Consulta")).one()
    mirona = Usuario(
        nombre="Solo", apellido="Mira", correo="mira.lotes@test.local",
        username="mira.lotes", hashed_password=hash_password(PASSWORD),
        empresa_id=base_datos["empresa_a"].id,
    )
    mirona.roles = [rol]
    db_session.add(mirona)
    db_session.commit()

    h = auth_headers(client, "mira.lotes")
    r = client.get(f"{API}/lotes", headers=h)
    print("\n===== 15. PERMISOS =====")
    print(f"  con 'consultar': {r.status_code}")
    assert r.status_code == 200, r.text
    # Y sin autenticación, nada
    assert client.get(f"{API}/lotes").status_code == 401


def test_el_motor_fifo_directo_sin_base_de_datos():
    """El reparto es una función pura: se prueba con eventos armados a mano, sin
    pasar por la API. Aquí se comprueba el caso que por la API no se puede armar:
    dos compras el mismo día con el orden invertido."""
    from app.modules.reventa.lotes import (
        CompraEvento,
        VentaEvento,
        repartir_lotes,
    )

    compras = [
        CompraEvento(fecha=date(2026, 7, 18), orden=0, productor="Sebastián",
                     kilos=D("300"), borona_kilos=D("0"), precio_kilo=D("18000"),
                     valor_total=D("5400000"), saldo=D("0")),
        CompraEvento(fecha=date(2026, 7, 18), orden=1, productor="Juan",
                     kilos=D("200"), borona_kilos=D("0"), precio_kilo=D("19000"),
                     valor_total=D("3800000"), saldo=D("3800000")),
    ]
    ventas = [
        VentaEvento(fecha=date(2026, 7, 20), orden=0, cliente="Alba Nieto",
                    tipo="queso", kilos=D("400"), precio_kilo=D("21000"),
                    valor_total=D("8400000"), gasto_monto=D("0")),
    ]
    reparto = repartir_lotes(compras, ventas, [])
    lote = reparto.lotes[0]
    print("\n===== 16. MOTOR DIRECTO =====")
    print(f"  un solo lote con 2 compras: {lote.compras} compras,"
          f" {lote.productores}, {lote.kilos_comprados} kg")
    print(f"  vendidos {lote.kilos_vendidos} kg, costo de lo vendido"
          f" {lote.costo_vendido}, ganancia {lote.ganancia}")
    # Las dos compras del mismo día son UN lote
    assert len(reparto.lotes) == 1
    assert lote.compras == 2
    assert lote.productores == ["Sebastián", "Juan"]
    assert lote.kilos_comprados == 500
    assert lote.costo_total == 9_200_000
    # FIFO dentro del lote: 300 de la primera (a 18.000) y 100 de la segunda (a 19.000)
    assert lote.kilos_vendidos == 400
    assert lote.costo_vendido == 300 * 18_000 + 100 * 19_000
    assert lote.kilos_sin_vender == 100
    assert lote.costo_sin_vender == 100 * 19_000
    # Y el cuadre
    assert lote.costo_vendido + lote.costo_sin_vender == lote.costo_total
    assert lote.por_pagar == 3_800_000


# ---------------------------------------------------------------------------
# 17. El detalle del lote: quién aportó qué y a quién se le vendió
# ---------------------------------------------------------------------------
def test_la_suma_de_los_productores_da_la_ganancia_del_lote(client, base_datos):
    """La ganancia de cada productor NO es la del lote repartida a prorrata: son
    SUS kilos costeados al precio que se le pagó a él. Por eso dos productores del
    mismo lote pueden tener margen distinto, y por eso la suma tiene que dar la
    cifra grande sin sobrar ni faltar un peso."""
    h = auth_headers(client, "admin.a")
    # Mismo día, tres precios muy distintos
    compra(client, h, fecha="2026-07-18", productor="Patricia Ospina",
           kilos_brutos="200", precio_kilo="15000")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    compra(client, h, fecha="2026-07-18", productor="Yeffer Alarcón",
           kilos_brutos="300", precio_kilo="20500")
    # Todo se vende al mismo precio, así el margen solo depende de la compra
    venta(client, h, fecha="2026-07-20", cliente="Alba Nieto", kilos="1000",
          precio_kilo="21000", pagada_de_contado=True)

    lote = panel(client, h)["lotes"][0]
    print("\n===== 17. DETALLE POR PRODUCTOR =====")
    for c in lote["detalle_compras"]:
        print(f"  {c['productor']:18} {c['kilos']:>8} kg a {c['precio_kilo']:>10}/kg"
              f" -> ganancia {c['ganancia']:>12} (margen {c['margen_kilo']}/kg)")
    print(f"  SUMA de los tres -> {lote['ganancia']}")

    # Cada uno con SU margen: 21.000 menos lo que se le pagó
    por_nombre = {c["productor"]: c for c in lote["detalle_compras"]}
    assert D(por_nombre["Patricia Ospina"]["margen_kilo"]) == 6000
    assert D(por_nombre["Sebastián Ruiz"]["margen_kilo"]) == 3000
    assert D(por_nombre["Yeffer Alarcón"]["margen_kilo"]) == 500
    # Y la suma da la ganancia del lote, exacta
    suma = sum(D(c["ganancia"]) for c in lote["detalle_compras"])
    assert suma == D(lote["ganancia"])
    assert suma == 1_200_000 + 1_500_000 + 150_000
    # Los kilos, el costo y el saldo también
    assert sum(D(c["kilos"]) for c in lote["detalle_compras"]) == D(lote["kilos_comprados"])
    assert sum(D(c["valor_total"]) for c in lote["detalle_compras"]) == D(lote["costo_total"])
    assert sum(D(c["saldo"]) for c in lote["detalle_compras"]) == D(lote["por_pagar"])


def test_las_ventas_del_lote_suman_sus_ingresos(client, base_datos):
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="1000", precio_kilo="17000")
    venta(client, h, fecha="2026-07-20", cliente="Depósito El Trébol", kilos="400",
          precio_kilo="20000", pagada_de_contado=True)
    venta(client, h, fecha="2026-07-22", cliente="Alba Nieto", kilos="300",
          precio_kilo="21500", gasto_concepto="Flete", gasto_por_kilo="300",
          pagada_de_contado=True)

    lote = panel(client, h)["lotes"][0]
    print("\n===== 18. VENTAS QUE SALIERON DEL LOTE =====")
    for v in lote["detalle_ventas"]:
        print(f"  {v['fecha']} {v['cliente']:20} {v['kilos']:>8} kg de"
              f" {v['kilos_venta']:>8} kg a {v['precio_kilo']:>9}/kg"
              f" -> ingreso {v['ingreso']:>12} ganancia {v['ganancia']}"
              f" partida={v['partida']}")
    # Las dos ventas, de la más reciente a la más vieja
    assert [v["fecha"] for v in lote["detalle_ventas"]] == ["2026-07-22", "2026-07-20"]
    # Ninguna se partió: el lote tenía de sobra
    assert all(v["partida"] is False for v in lote["detalle_ventas"])
    # Y suman lo del lote
    assert sum(D(v["ingreso"]) for v in lote["detalle_ventas"]) == D(lote["ingresos"])
    assert sum(D(v["gasto"]) for v in lote["detalle_ventas"]) == D(lote["gastos"])
    assert sum(D(v["kilos"]) for v in lote["detalle_ventas"]) == D(lote["kilos_vendidos"])
    assert sum(D(v["ganancia"]) for v in lote["detalle_ventas"]) == D(lote["ganancia"])


def test_una_venta_partida_se_ve_partida_en_los_dos_lotes(client, base_datos):
    """Si mostrara solo los kilos que tomó de cada lote, la venta parecería más
    pequeña de lo que fue. Por eso cada fila lleva los dos números."""
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="400", precio_kilo="17000")
    compra(client, h, fecha="2026-07-25", productor="Juan Gómez",
           kilos_brutos="400", precio_kilo="18000")
    # Una sola venta de 700 kg: 400 del viejo y 300 del nuevo
    venta(client, h, fecha="2026-07-27", cliente="Alba Nieto", kilos="700",
          precio_kilo="21000", pagada_de_contado=True)

    lotes = por_fecha(panel(client, h))
    print("\n===== 19. UNA VENTA PARTIDA =====")
    for fecha in sorted(lotes):
        v = lotes[fecha]["detalle_ventas"][0]
        print(f"  lote {fecha}: {v['kilos']} kg de los {v['kilos_venta']} kg de la"
              f" venta | ingreso {v['ingreso']} | partida={v['partida']}")
    a, b = lotes["2026-07-18"], lotes["2026-07-25"]
    assert D(a["detalle_ventas"][0]["kilos"]) == 400
    assert D(a["detalle_ventas"][0]["kilos_venta"]) == 700
    assert a["detalle_ventas"][0]["partida"] is True
    assert D(b["detalle_ventas"][0]["kilos"]) == 300
    assert b["detalle_ventas"][0]["partida"] is True
    # Y las dos partes suman la venta completa
    ingresos = D(a["detalle_ventas"][0]["ingreso"]) + D(b["detalle_ventas"][0]["ingreso"])
    assert ingresos == 700 * 21000


def test_una_venta_de_dos_compras_del_mismo_lote_es_una_sola_fila(client, base_datos):
    """El usuario piensa en ventas, no en trozos de inventario. Si una venta se
    lleva kilos de dos compras del MISMO lote, tiene que salir una fila con los
    kilos sumados y no dos filas de la misma venta."""
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Patricia Ospina",
           kilos_brutos="200", precio_kilo="15000")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="300", precio_kilo="18000")
    # Una venta de 450 kg: 200 de Patricia y 250 de Sebastián
    venta(client, h, fecha="2026-07-20", cliente="Alba Nieto", kilos="450",
          precio_kilo="21000", pagada_de_contado=True)

    lote = panel(client, h)["lotes"][0]
    print("\n===== 20. UNA VENTA, DOS COMPRAS DEL MISMO LOTE =====")
    print(f"  filas de venta: {len(lote['detalle_ventas'])}")
    for v in lote["detalle_ventas"]:
        print(f"    {v['cliente']}: {v['kilos']} kg | ingreso {v['ingreso']}")
    for c in lote["detalle_compras"]:
        print(f"    {c['productor']:18} vendidos {c['kilos_vendidos']} kg"
              f" ganancia {c['ganancia']}")
    # UNA sola fila con los 450 kg
    assert len(lote["detalle_ventas"]) == 1
    assert D(lote["detalle_ventas"][0]["kilos"]) == 450
    assert D(lote["detalle_ventas"][0]["ingreso"]) == 450 * 21000
    assert lote["detalle_ventas"][0]["partida"] is False
    # Pero el detalle por productor sí los separa, cada uno con su margen
    por_nombre = {c["productor"]: c for c in lote["detalle_compras"]}
    assert D(por_nombre["Patricia Ospina"]["kilos_vendidos"]) == 200
    assert D(por_nombre["Sebastián Ruiz"]["kilos_vendidos"]) == 250
    assert D(por_nombre["Patricia Ospina"]["margen_kilo"]) == 6000
    assert D(por_nombre["Sebastián Ruiz"]["margen_kilo"]) == 3000
    # Y la suma sigue cuadrando
    assert sum(D(c["ganancia"]) for c in lote["detalle_compras"]) == D(lote["ganancia"])


def test_el_detalle_cuadra_con_las_cifras_reales(client, base_datos):
    """El cuadre del detalle con los kilos y precios de verdad, incluida la compra
    de Yeffer a $1/kg, que el usuario confirmó que está bien. Aquí es donde el
    redondeo del reparto proporcional puede dejar centavos sueltos."""
    h = auth_headers(client, "admin.a")
    for productor, kilos, precio, borona in [
        ("Patricia", "12", "12800", "0"), ("Rubiela", "68", "15200", "0"),
        ("Sebastian", "946", "18100", "23.7"), ("Juan", "693", "17400", "9.7"),
        ("Prieto", "54", "15200", "0"), ("Yeffer", "115", "1", "0"),
    ]:
        compra(client, h, fecha="2026-07-18", productor=productor,
               kilos_brutos=kilos, precio_kilo=precio, borona_kilos=borona)
    compra(client, h, fecha="2026-07-25", productor="Juan",
           kilos_brutos="823", precio_kilo="18000")
    compra(client, h, fecha="2026-07-25", productor="Sebastian",
           kilos_brutos="802", precio_kilo="18800")
    venta(client, h, fecha="2026-07-21", cliente="Depósito El Trébol", kilos="1200.5",
          precio_kilo="20300", gasto_concepto="Flete", gasto_por_kilo="287.5",
          pagada_de_contado=True)
    venta(client, h, fecha="2026-07-28", cliente="Alba Nieto", kilos="1100.75",
          precio_kilo="21150", gasto_concepto="Flete", gasto_por_kilo="312.5")
    venta(client, h, fecha="2026-07-29", cliente="La Ganancia", tipo="borona",
          kilos="33.4", precio_kilo="8200", pagada_de_contado=True)

    p = panel(client, h)
    print("\n===== 21. EL DETALLE CON CIFRAS REALES =====")
    for lote in p["lotes"]:
        print(f"  lote {lote['fecha']} (ganancia {lote['ganancia']}):")
        suma_c = sum(D(c["ganancia"]) for c in lote["detalle_compras"])
        for c in lote["detalle_compras"]:
            print(f"    {c['productor']:12} {c['kilos']:>9} kg a {c['precio_kilo']:>10}"
                  f" -> vendidos {c['kilos_vendidos']:>9} kg | ganancia {c['ganancia']:>13}")
        print(f"    suma de productores: {suma_c} == {lote['ganancia']}")
        suma_v = sum(D(v["ingreso"]) for v in lote["detalle_ventas"])
        for v in lote["detalle_ventas"]:
            print(f"    venta {v['fecha']} {v['cliente']:20} {v['kilos']:>9} kg"
                  f" de {v['kilos_venta']:>9} | ingreso {v['ingreso']:>13}")
        print(f"    suma de ventas: {suma_v} == {lote['ingresos']}")
        # Los dos desgloses cuadran con la cifra grande
        assert suma_c == D(lote["ganancia"])
        assert suma_v == D(lote["ingresos"])
        assert sum(D(v["gasto"]) for v in lote["detalle_ventas"]) == D(lote["gastos"])
        assert sum(D(v["ganancia"]) for v in lote["detalle_ventas"]) == D(lote["ganancia"])
        # Y por compra, los dos destinos del costo suman lo que se le pagó
        for c in lote["detalle_compras"]:
            dos = D(c["costo_realizado"]) + D(c["costo_sin_vender"])
            assert dos == D(c["valor_total"]), (
                f"{c['productor']}: {dos} != {c['valor_total']}"
            )
            kilos = (D(c["kilos_vendidos"]) + D(c["kilos_a_borona"])
                     + D(c["kilos_merma"]) + D(c["kilos_sin_vender"]))
            assert kilos == D(c["kilos"]), f"{c['productor']}: {kilos} != {c['kilos']}"
    comprobar_cuadre(p, "detalle real")


def test_con_merma_las_ventas_no_suman_la_ganancia_del_lote(client, base_datos):
    """La merma NO sale en ninguna venta, porque no se vendió, pero sí se le resta
    a la ganancia del lote. Entonces:

        suma de lo que dejaron las ventas - merma = ganancia del lote

    Esta prueba existe porque las otras dos del detalle no tenían merma y me
    ocultaron el problema: el pie de la tabla de ventas mostraba la ganancia del
    lote, y con merma las filas no lo sumaban. La pantalla ahora muestra la merma
    como un renglón propio que lleva de una cifra a la otra.
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-18", productor="Sebastián Ruiz",
           kilos_brutos="500", precio_kilo="18000")
    venta(client, h, fecha="2026-07-20", cliente="Alba Nieto", kilos="450",
          precio_kilo="21000", gasto_concepto="Flete", gasto_por_kilo="200",
          pagada_de_contado=True)
    # Los 50 kg que faltan se pierden como merma
    ajuste(client, h, fecha="2026-07-22", kilos="50", destino="merma")

    lote = panel(client, h)["lotes"][0]
    ventas = lote["detalle_ventas"]
    suma_ventas = sum(D(v["ganancia"]) for v in ventas)
    print("\n===== 22. MERMA Y EL PIE DE LA TABLA DE VENTAS =====")
    for v in ventas:
        print(f"  {v['fecha']} {v['cliente']}: entró {v['ingreso']}"
              f" | costó {v['costo']} | gasto {v['gasto']} -> dejó {v['ganancia']}")
    print(f"  suma de las ventas: {suma_ventas}")
    print(f"  (-) merma perdida:  {lote['costo_merma']}")
    print(f"  ganancia del lote:  {lote['ganancia']}")

    # 9.450.000 - 8.100.000 - 90.000 = 1.260.000 de las ventas
    assert suma_ventas == 1_260_000
    # La merma son 50 kg x 18.000 = 900.000
    assert D(lote["costo_merma"]) == 900_000
    # Y la ganancia del lote es la resta: NO es la suma de las ventas
    assert D(lote["ganancia"]) == 360_000
    assert suma_ventas - D(lote["costo_merma"]) == D(lote["ganancia"])
    assert suma_ventas != D(lote["ganancia"])

    # El detalle por productor SÍ suma la ganancia del lote, porque la merma se le
    # carga a la compra de la que salió el queso perdido
    assert sum(D(c["ganancia"]) for c in lote["detalle_compras"]) == D(lote["ganancia"])
    comprobar_cuadre(p={"lotes": [lote]}, etiqueta="merma")
