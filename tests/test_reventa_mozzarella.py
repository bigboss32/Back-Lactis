"""Mozzarella POR BARRAS en reventa: entra como barra y sale como barra.

LA REGLA QUE MANDA SOBRE TODO LO DEMÁS, y lo que estas pruebas fijan:
**los kilos y las barras NO se pueden sumar.** "20 kg + 8 barras" no es un
número, así que ningún total, tarjeta, desglose ni documento puede juntarlos. La
PLATA sí se suma: los pesos son pesos, vengan de kilos o de barras.

Hay un cliente real usando este módulo con dinero de verdad y con compras,
ventas, lotes, cartera y estados de cuenta ya cargados EN KILOS. Por eso la
prueba más importante de este archivo no es ninguna de la mozzarella: es
`test_las_cifras_viejas_en_kilos_no_se_movieron_ni_un_peso`, que registra un
negocio de puro queso, guarda TODAS las cifras del resumen, mete mozzarella al
lado y comprueba que ni una sola de las de kilos se movió.

Estilo: cada prueba explica el POR QUÉ y imprime las cifras, porque el dueño las
cuadra a mano con la calculadora y quien lea el log tiene que poder repetirlas.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"


def D(v):
    """Los Decimal viajan como string en JSON."""
    return Decimal(str(v))


@pytest.fixture()
def h(client, base_datos):
    # Depende de base_datos a propósito: sin él las fixtures corren en el orden
    # equivocado y el login falla porque el usuario todavía no existe.
    return auth_headers(client, "admin.a")


# --------------------------------------------------------------------- ayudas
def comprar_barras(client, h, *, barras, precio_barra, productor="Yeferson", dias=10):
    return client.post(
        f"{API}/compras",
        json={
            "fecha": str(date.today() - timedelta(days=dias)),
            "productor": productor,
            "tipo": "mozzarella",
            "barras": str(barras),
            "precio_barra": str(precio_barra),
        },
        headers=h,
    )


def vender_barras(
    client, h, *, barras, precio_barra, cliente="Tienda La 33", dias=2,
    gasto_por_barra=None, contado=False,
):
    cuerpo = {
        "fecha": str(date.today() - timedelta(days=dias)),
        "cliente": cliente,
        "tipo": "mozzarella",
        "barras": str(barras),
        "precio_barra": str(precio_barra),
        "pagada_de_contado": contado,
    }
    if gasto_por_barra is not None:
        cuerpo["gasto_concepto"] = "Transporte"
        cuerpo["gasto_por_barra"] = str(gasto_por_barra)
    return client.post(f"{API}/ventas", json=cuerpo, headers=h)


def comprar_kilos(client, h, *, kilos, precio, productor="Yeferson", dias=10, borona="0"):
    return client.post(
        f"{API}/compras",
        json={
            "fecha": str(date.today() - timedelta(days=dias)),
            "productor": productor,
            "kilos_brutos": str(kilos),
            "borona_kilos": str(borona),
            "precio_kilo": str(precio),
        },
        headers=h,
    )


def vender_kilos(client, h, *, kilos, precio, cliente="Tienda La 33", dias=2, tipo="queso"):
    return client.post(
        f"{API}/ventas",
        json={
            "fecha": str(date.today() - timedelta(days=dias)),
            "cliente": cliente,
            "tipo": tipo,
            "kilos": str(kilos),
            "precio_kilo": str(precio),
        },
        headers=h,
    )


def resumen(client, h, dias=60):
    r = client.get(
        f"{API}/resumen",
        params={
            "desde": str(date.today() - timedelta(days=dias)),
            "hasta": str(date.today()),
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def fila(res, producto):
    """El renglón del desglose por producto, o None si no está."""
    return next((f for f in res["por_producto"] if f["producto"] == producto), None)


# ---------------------------------------------------------------------------
# 1. La cuenta que pidió el dueño: comprar barras, venderlas y ver la ganancia
# ---------------------------------------------------------------------------
def test_comprar_100_barras_y_vender_60_da_la_ganancia_correcta(client, h):
    """La cuenta que el dueño hace en el cuaderno, sin un kilo de por medio.

    Compra 100 barras a $9.300 y vende 60 a $14.800. Lo que dejó NO es
    "60 × (14.800 − 9.300)": el resumen del período resta TODA la compra, no solo
    el costo de lo vendido, porque las 40 barras que quedaron en la bodega son
    plata invertida que todavía no volvió. Es el mismo criterio que ya tienen los
    kilos, y tiene que ser el mismo o las dos tarjetas del Resumen no se podrían
    leer juntas.

    Lo que sí es exacto y se comprueba aparte es la ganancia REALIZADA de la
    mozzarella, que sale del renglón de barras del desglose: lo que entró por las
    60, menos lo que costaron ESAS 60.
    """
    assert comprar_barras(client, h, barras=100, precio_barra="9300").status_code == 201
    assert vender_barras(client, h, barras=60, precio_barra="14800").status_code == 201

    res = resumen(client, h)
    compras = D(res["total_compras_mozzarella"])
    ventas = D(res["total_ventas_mozzarella"])
    vendida = fila(res, "mozzarella")
    pendiente = fila(res, "mozzarella_pendiente")

    print("\n===== 1. COMPRAR 100 BARRAS Y VENDER 60 =====")
    print(f"  compradas:          {res['barras_compradas']} barras")
    print(f"  vendidas:           {res['barras_vendidas']} barras")
    print(f"  pendientes:         {res['barras_pendientes']} barras")
    print(f"  plata comprada:     {compras}   (100 x 9.300 = 930.000)")
    print(f"  plata vendida:      {ventas}   (60 x 14.800 = 888.000)")
    print(f"  precio prom compra: {res['precio_promedio_compra_barra']} /barra")
    print(f"  precio prom venta:  {res['precio_promedio_venta_barra']} /barra")
    print(f"  ganancia del periodo (ventas - compras): {res['ganancia_estimada']}")
    print(f"  margen por barra vendida:                {res['margen_por_barra']}")
    print("  -- renglon 'mozzarella' (lo que YA se vendio) --")
    print(f"     barras vendidas: {vendida['barras_vendidas']} · unidad: {vendida['unidad']}")
    print(f"     costo de esas barras: {vendida['costo']}   (60 x 9.300 = 558.000)")
    print(f"     ingreso: {vendida['ingreso']} · ganancia realizada: {vendida['ganancia']}")
    print("  -- renglon 'mozzarella_pendiente' (lo que sigue en bodega) --")
    print(f"     barras: {pendiente['barras']} · costo dormido: {pendiente['costo']}")

    # Las cantidades, cada una en su unidad
    assert D(res["barras_compradas"]) == D(100)
    assert D(res["barras_vendidas"]) == D(60)
    assert D(res["barras_pendientes"]) == D(40)
    # La plata
    assert compras == D("930000.00")
    assert ventas == D("888000.00")
    assert D(res["precio_promedio_compra_barra"]) == D("9300.00")
    assert D(res["precio_promedio_venta_barra"]) == D("14800.00")
    # Ganancia del período = vendido - comprado (las 40 en bodega restan todavía)
    assert D(res["ganancia_estimada"]) == D("888000.00") - D("930000.00")

    # LA GANANCIA REALIZADA DE LA MOZZARELLA, que es la que el dueño quiere ver:
    # 60 barras compradas a 9.300 costaron 558.000 y se vendieron por 888.000.
    assert vendida["unidad"] == "barra"
    assert D(vendida["barras_vendidas"]) == D(60)
    assert D(vendida["costo"]) == D("558000.00")
    assert D(vendida["ingreso"]) == D("888000.00")
    assert D(vendida["ganancia"]) == D("330000.00")
    assert D(vendida["costo_barra"]) == D("9300.00")
    assert D(vendida["precio_venta_barra"]) == D("14800.00")
    # Y las 40 que quedaron cargan el resto del costo, sin sobrar ni faltar un peso
    assert D(pendiente["barras"]) == D(40)
    assert D(pendiente["costo"]) == D("372000.00")
    assert D(vendida["costo"]) + D(pendiente["costo"]) == compras


def test_el_gasto_por_barra_no_se_confunde_con_el_gasto_por_kilo(client, h):
    """El flete de una venta de barras se cobra POR BARRA, y va en su columna.

    Si el "$700 por barra" se guardara en `gasto_por_kilo`, el día que alguien
    sume esa columna creyendo que son pesos por kilo la cifra no significaría
    nada. Lo que sí es común es `gasto_monto` (el total en PESOS), porque los
    pesos sí se suman entre unidades: es la única forma de que la tarjeta de
    gastos del Resumen cuadre con los dos tipos de venta a la vez.
    """
    assert comprar_barras(client, h, barras=50, precio_barra="9000").status_code == 201
    v = vender_barras(
        client, h, barras=50, precio_barra="15000", gasto_por_barra="700"
    )
    assert v.status_code == 201, v.text
    venta = v.json()

    res = resumen(client, h)
    print("\n===== 2. GASTO POR BARRA =====")
    print(f"  gasto_por_barra: {venta['gasto_por_barra']} · gasto_por_kilo: "
          f"{venta['gasto_por_kilo']}")
    print(f"  gasto_monto (pesos): {venta['gasto_monto']}   (50 x 700 = 35.000)")
    print(f"  total_gastos del resumen:     {res['total_gastos']}")
    print(f"  total_gastos_mozzarella:      {res['total_gastos_mozzarella']}")
    print(f"  ganancia realizada mozzarella: {fila(res, 'mozzarella')['ganancia']}")

    assert D(venta["gasto_por_barra"]) == D("700.00")
    # La columna de kilos queda en CERO: lo exige el CHECK de la tabla.
    assert D(venta["gasto_por_kilo"]) == D(0)
    assert D(venta["gasto_monto"]) == D("35000.00")
    assert D(res["total_gastos_mozzarella"]) == D("35000.00")
    assert D(res["total_gastos"]) == D("35000.00")
    # 50 x 15.000 - 50 x 9.000 - 35.000
    assert D(fila(res, "mozzarella")["ganancia"]) == D("265000.00")


# ---------------------------------------------------------------------------
# 2. No se pueden vender barras que no se compraron: NI CREANDO NI EDITANDO
# ---------------------------------------------------------------------------
def test_no_se_pueden_vender_mas_barras_de_las_que_hay_al_crear(client, h):
    """El inventario de barras es SUYO y no se cubre con el queso que haya.

    Se compran 10 barras y hay 500 kg de queso en bodega: aun así, vender 11
    barras tiene que rebotar. Si el guardia mirara "el inventario" en general,
    tener queso autorizaría a despachar mozzarella que nunca se compró, y el
    dueño facturaría mercancía que no tiene.
    """
    assert comprar_kilos(client, h, kilos="500", precio="18000").status_code == 201
    assert comprar_barras(client, h, barras=10, precio_barra="9000").status_code == 201

    de_mas = vender_barras(client, h, barras=11, precio_barra="15000")
    detalle = de_mas.json().get("error", {}).get("detail", "")
    print("\n===== 3. VENDER MAS BARRAS DE LAS QUE HAY (CREANDO) =====")
    print("  hay 500 kg de queso y 10 barras")
    print(f"  vender 11 barras: {de_mas.status_code} · {detalle}")

    assert de_mas.status_code == 422, "se vendieron barras que no se compraron"
    # El mensaje tiene que hablar de PIEZAS y del PRODUCTO, no de kg: el dueño lee el
    # error y tiene que entender de qué inventario le está hablando. Antes decía
    # "barras de mozzarella" porque la mozzarella era el único producto que se contaba;
    # ahora dice las unidades y el nombre del producto, que es lo que sirve también
    # cuando el que no alcanza es un producto que él agregó.
    assert "unidades" in detalle
    assert "mozzarella" in detalle.lower()
    assert "kg" not in detalle

    # Y las 10 que sí hay pasan sin problema
    justas = vender_barras(client, h, barras=10, precio_barra="15000")
    print(f"  vender las 10 que hay: {justas.status_code}")
    assert justas.status_code == 201, justas.text


def test_no_se_pueden_inventar_barras_editando_una_venta(client, h):
    """EL DEFECTO QUE YA NOS PASÓ CON LOS KILOS, en la unidad nueva.

    Con los kilos, el guardia estaba SOLO al crear: se registraba una venta de
    1 kg y se editaba a 500, y pasaba. Aquí se comprueba que la mozzarella nace
    con el guardia en LOS DOS lados. Sin esto, el control de existencias de las
    barras sería de adorno: bastaría crear una venta de una barra y editarla.
    """
    assert comprar_barras(client, h, barras=20, precio_barra="9000").status_code == 201
    v = vender_barras(client, h, barras=1, precio_barra="15000").json()

    inflada = client.put(f"{API}/ventas/{v['id']}", json={"barras": "500"}, headers=h)
    detalle = inflada.json().get("error", {}).get("detail", "")
    print("\n===== 4. INVENTAR BARRAS EDITANDO =====")
    print("  hay 20 barras compradas y una venta de 1")
    print(f"  editarla a 500 barras: {inflada.status_code} · {detalle}")

    assert inflada.status_code == 422, "se pudo vender mozzarella que nunca se compró"
    assert "unidades" in detalle and "mozzarella" in detalle.lower()

    # Y el arreglo no puede volverse un estorbo: subirla a las 20 que hay sí se
    # puede, porque al editar hay que devolverle al inventario la barra que esta
    # misma venta ya tenía apartada.
    justa = client.put(f"{API}/ventas/{v['id']}", json={"barras": "20"}, headers=h)
    print(f"  editarla a las 20 que hay: {justa.status_code}")
    assert justa.status_code == 200, justa.text
    assert D(justa.json()["barras"]) == D(20)
    assert D(justa.json()["valor_total"]) == D("300000.00")

    res = resumen(client, h)
    print(f"  barras disponibles despues: {res['barras_disponibles']}")
    assert D(res["barras_disponibles"]) == D(0)


def test_no_se_pueden_quitar_barras_ya_vendidas_editando_la_compra(client, h):
    """El otro lado del mismo hueco: bajar la COMPRA por debajo de lo vendido.

    Bajar una compra de 100 barras a 10 cuando ya se vendieron 80 deja el
    inventario en -70, y con el inventario en negativo NINGUNA venta vuelve a
    pasar el control de existencias: el dueño se queda sin poder trabajar sin
    entender por qué. Es el mismo guardia que ya tienen los kilos.
    """
    compra = comprar_barras(client, h, barras=100, precio_barra="9000").json()
    assert vender_barras(client, h, barras=80, precio_barra="15000").status_code == 201

    recorte = client.put(f"{API}/compras/{compra['id']}", json={"barras": "10"}, headers=h)
    detalle = recorte.json().get("error", {}).get("detail", "")
    print("\n===== 5. QUITAR BARRAS YA VENDIDAS =====")
    print("  compra de 100 barras, 80 vendidas (quedan 20)")
    print(f"  bajar la compra a 10 barras: {recorte.status_code} · {detalle}")

    assert recorte.status_code == 422
    assert "unidades" in detalle and "mozzarella" in detalle.lower()

    # Hasta 80 sí se puede: es lo que de verdad salió.
    valido = client.put(f"{API}/compras/{compra['id']}", json={"barras": "80"}, headers=h)
    print(f"  bajarla a 80 barras: {valido.status_code}")
    assert valido.status_code == 200, valido.text
    assert D(valido.json()["barras"]) == D(80)
    assert D(valido.json()["valor_total"]) == D("720000.00")


def test_no_se_puede_anular_una_compra_de_barras_ya_vendidas(client, h):
    """Anular una compra cuya mozzarella ya salió borraría de la cuenta unas
    barras que se despacharon de verdad, y dejaría el inventario en negativo.
    Igual que en el queso: primero se anulan las ventas, o se corrige la compra.
    """
    compra = comprar_barras(client, h, barras=30, precio_barra="9000").json()
    assert vender_barras(client, h, barras=30, precio_barra="15000").status_code == 201

    r = client.post(f"{API}/compras/{compra['id']}/anular", headers=h)
    detalle = r.json().get("error", {}).get("detail", "")
    print("\n===== 6. ANULAR UNA COMPRA DE BARRAS YA VENDIDAS =====")
    print(f"  anular: {r.status_code} · {detalle}")
    assert r.status_code == 422
    assert "unidades" in detalle and "mozzarella" in detalle.lower()


def test_las_barras_no_admiten_decimales(client, h):
    """"8,5 barras" no es media barra mal medida: es un dato equivocado.

    Aquí NO se redondea, se RECHAZA, y es la diferencia a propósito con los
    kilos (10,005 kg es un pesaje real que se lleva a 10,01). Redondear en
    silencio le guardaría 9 barras a quien escribió 8,5 y la cuenta de la plata
    saldría distinta de la que él hizo con la calculadora. Además la columna es
    Numeric(12,0): si esto no rechazara, Postgres redondearía por su cuenta y la
    fila quedaría contradiciéndose sola.
    """
    media = comprar_barras(client, h, barras="8.5", precio_barra="9000")
    print("\n===== 7. BARRAS CON DECIMALES =====")
    print(f"  comprar 8,5 barras: {media.status_code}")
    assert media.status_code == 422

    assert comprar_barras(client, h, barras=10, precio_barra="9000").status_code == 201
    media_venta = vender_barras(client, h, barras="2.5", precio_barra="15000")
    print(f"  vender 2,5 barras:  {media_venta.status_code}")
    assert media_venta.status_code == 422


def test_una_compra_de_mozzarella_exige_barras_y_precio_por_barra(client, h):
    """Una compra de mozzarella sin barras no es una compra a medias: es un dato
    que no se puede guardar sin inventar. Y una de queso con barras tampoco: el
    CHECK de la tabla la rechazaría, y un 500 de la base no le dice nada al dueño.
    """
    sin_barras = client.post(
        f"{API}/compras",
        json={
            "fecha": str(date.today()),
            "productor": "Yeferson",
            "tipo": "mozzarella",
            "kilos_brutos": "100",
            "precio_kilo": "18000",
        },
        headers=h,
    )
    print("\n===== 8. CAMPOS DE LA UNIDAD =====")
    print(f"  compra 'mozzarella' con kilos y sin barras: {sin_barras.status_code}")
    assert sin_barras.status_code == 422

    sin_kilos = client.post(
        f"{API}/ventas",
        json={
            "fecha": str(date.today()),
            "cliente": "Tienda La 33",
            "tipo": "queso",
            "barras": "8",
            "precio_barra": "15000",
        },
        headers=h,
    )
    print(f"  venta 'queso' con barras y sin kilos:      {sin_kilos.status_code}")
    assert sin_kilos.status_code == 422


# ---------------------------------------------------------------------------
# 3. Kilos y barras no se suman, pero la plata sí
# ---------------------------------------------------------------------------
def test_los_totales_de_kilos_no_incluyen_barras_y_al_contrario(client, h):
    """LA PRUEBA DE LA REGLA. Un negocio con las dos unidades a la vez.

    20 kg de queso y 8 barras de mozzarella no son 28 de nada, así que:
      - `kilos_comprados`, `kilos_vendidos`, `kilos_disponibles` cuentan SOLO kilos
      - `barras_compradas`, `barras_vendidas`, `barras_disponibles` SOLO barras
      - los precios promedio por kilo se sacan con la plata de los kilos, y los
        de por barra con la de las barras: si se cruzaran, el "$/kg" saldría
        inflado con pesos que no salieron de ningún kilo
      - `total_compras`, `total_ventas`, `total_gastos` y `ganancia_estimada` SÍ
        suman las dos, porque los pesos son pesos
    """
    assert comprar_kilos(client, h, kilos="20", precio="18000").status_code == 201
    assert comprar_barras(client, h, barras=8, precio_barra="9000").status_code == 201
    assert vender_kilos(client, h, kilos="15", precio="21000").status_code == 201
    assert vender_barras(client, h, barras=5, precio_barra="15000").status_code == 201

    res = resumen(client, h)
    print("\n===== 9. KILOS Y BARRAS NO SE SUMAN, LA PLATA SI =====")
    print("  -- cantidades, cada una en su unidad --")
    print(f"     kilos_comprados:    {res['kilos_comprados']} kg    (no 28)")
    print(f"     barras_compradas:   {res['barras_compradas']} barras")
    print(f"     kilos_vendidos:     {res['kilos_vendidos']} kg")
    print(f"     barras_vendidas:    {res['barras_vendidas']} barras")
    print(f"     kilos_disponibles:  {res['kilos_disponibles']} kg")
    print(f"     barras_disponibles: {res['barras_disponibles']} barras")
    print("  -- precios promedio, cada uno con la plata de SU unidad --")
    print(f"     compra $/kg:    {res['precio_promedio_compra']}   (360.000 / 20 kg)")
    print(f"     compra $/barra: {res['precio_promedio_compra_barra']}   (72.000 / 8)")
    print(f"     venta  $/kg:    {res['precio_promedio_venta']}   (315.000 / 15 kg)")
    print(f"     venta  $/barra: {res['precio_promedio_venta_barra']}   (75.000 / 5)")
    print("  -- la plata, que SI se suma --")
    print(f"     total_compras: {res['total_compras']}  = 360.000 (kg) + "
          f"{res['total_compras_mozzarella']} (barras)")
    print(f"     total_ventas:  {res['total_ventas']}  = 315.000 (kg) + "
          f"{res['total_ventas_mozzarella']} (barras)")
    print(f"     ganancia:      {res['ganancia_estimada']}")

    # Las cantidades: cada una limpia de la otra
    assert D(res["kilos_comprados"]) == D(20)
    assert D(res["barras_compradas"]) == D(8)
    assert D(res["kilos_vendidos"]) == D(15)
    assert D(res["barras_vendidas"]) == D(5)
    assert D(res["kilos_disponibles"]) == D(5)
    assert D(res["barras_disponibles"]) == D(3)
    # Los promedios: cada uno con la plata de su unidad. Si el de kilos incluyera
    # los 72.000 de la mozzarella daría 21.600 en vez de 18.000.
    assert D(res["precio_promedio_compra"]) == D("18000.00")
    assert D(res["precio_promedio_compra_barra"]) == D("9000.00")
    assert D(res["precio_promedio_venta"]) == D("21000.00")
    assert D(res["precio_promedio_venta_barra"]) == D("15000.00")
    # La plata: la suma de las dos unidades, escrita a mano para que se vea
    assert D(res["total_compras_mozzarella"]) == D("72000.00")
    assert D(res["total_ventas_mozzarella"]) == D("75000.00")
    assert D(res["total_compras"]) == D("360000.00") + D("72000.00")
    assert D(res["total_ventas"]) == D("315000.00") + D("75000.00")
    assert D(res["ganancia_estimada"]) == D(res["total_ventas"]) - D(res["total_compras"])
    # Y el margen por kilo mira SOLO la plata de los kilos: (315.000 - 360.000)/15.
    # Con la de las barras adentro daría otra cifra y no significaría nada del queso.
    assert D(res["margen_por_kilo"]) == D("-3000.00")


def test_el_desglose_por_producto_lleva_cada_renglon_en_su_unidad(client, h):
    """Si alguna pantalla suma la columna `kilos` de todo el desglose, lo que le
    tiene que salir son kilos de verdad.

    La garantía es la misma que en las tablas: en un renglón de barras los campos
    de kilos van en CERO y al contrario. Y el invariante del resumen sigue en pie
    con las dos unidades: la suma de las ganancias de TODOS los renglones da
    exactamente `ganancia_estimada`, y los costos de cada unidad suman exacto la
    plata comprada de esa unidad.
    """
    assert comprar_kilos(client, h, kilos="100", precio="18000").status_code == 201
    assert comprar_barras(client, h, barras=40, precio_barra="9300").status_code == 201
    assert vender_kilos(client, h, kilos="70", precio="21000").status_code == 201
    assert vender_barras(client, h, barras=25, precio_barra="14800").status_code == 201

    res = resumen(client, h)
    print("\n===== 10. DESGLOSE POR PRODUCTO, CADA RENGLON EN SU UNIDAD =====")
    for f in res["por_producto"]:
        print(f"  {f['producto']:<22} unidad={f['unidad']:<6} "
              f"kilos={f['kilos']:>8} barras={f['barras']:>6} "
              f"costo={f['costo']:>12} ganancia={f['ganancia']:>12}")

    en_kilos = [f for f in res["por_producto"] if f["unidad"] == "kg"]
    en_barras = [f for f in res["por_producto"] if f["unidad"] == "barra"]
    assert en_barras, "el desglose no trajo los renglones de la mozzarella"

    # Un renglón de kilos NO tiene barras, y al contrario
    for f in en_kilos:
        assert D(f["barras"]) == D(0) and D(f["barras_vendidas"]) == D(0)
        assert D(f["precio_venta_barra"]) == D(0) and D(f["costo_barra"]) == D(0)
    for f in en_barras:
        assert D(f["kilos"]) == D(0) and D(f["kilos_vendidos"]) == D(0)
        assert D(f["precio_venta_kilo"]) == D(0) and D(f["costo_kilo"]) == D(0)

    # Sumar la columna `kilos` da kilos de verdad: los cuatro destinos del lote
    # comprado en kilos (vendido, borona, merma, inventario).
    suma_kilos = sum(D(f["kilos"]) for f in en_kilos)
    suma_barras = sum(D(f["barras"]) for f in en_barras)
    print(f"  suma columna kilos  = {suma_kilos} kg     (kilos_comprados = "
          f"{res['kilos_comprados']})")
    print(f"  suma columna barras = {suma_barras} barras (barras_compradas = "
          f"{res['barras_compradas']})")
    assert suma_kilos == D(res["kilos_comprados"])
    assert suma_barras == D(res["barras_compradas"])

    # Cada unidad reparte SU plata: ni un peso de las barras cae en los kilos.
    costo_kilos = sum(D(f["costo"]) for f in en_kilos)
    costo_barras = sum(D(f["costo"]) for f in en_barras)
    print(f"  suma costos de kilos  = {costo_kilos}  (compras en kilos = 1.800.000)")
    print(f"  suma costos de barras = {costo_barras}  "
          f"(compras de mozzarella = {res['total_compras_mozzarella']})")
    assert costo_kilos == D("1800000.00")
    assert costo_barras == D(res["total_compras_mozzarella"])
    assert costo_kilos + costo_barras == D(res["total_compras"])

    # EL INVARIANTE: la suma de las ganancias del desglose ES la del período.
    suma_ganancias = sum(D(f["ganancia"]) for f in res["por_producto"])
    print(f"  suma ganancias del desglose = {suma_ganancias}  vs "
          f"ganancia_estimada = {res['ganancia_estimada']}")
    assert suma_ganancias == D(res["ganancia_estimada"])


def test_vender_barras_compradas_antes_del_periodo(client, h):
    """El residuo de las barras puede salir NEGATIVO, y hay que saber decirlo.

    Se compran 100 barras hace 40 días y se venden 60 hace 3. Si se consulta la
    última semana, en ese período no se compró NINGUNA barra y se vendieron 60: el
    residuo es −60. No es un error: son barras de una temporada anterior, igual que
    pasa con los kilos ("Salió de inventario anterior").

    Esta prueba además tapa un hueco que sería un 500 en la cara del dueño: el
    renglón negativo usa otra etiqueta ('mozzarella_anterior'), y si esa etiqueta no
    estuviera registrada el resumen reventaría al consultar ese rango.
    """
    assert comprar_barras(client, h, barras=100, precio_barra="9000", dias=40).status_code == 201
    assert vender_barras(client, h, barras=60, precio_barra="15000", dias=3).status_code == 201

    res = resumen(client, h, dias=7)
    anterior = fila(res, "mozzarella_anterior")
    vendida = fila(res, "mozzarella")
    print("\n===== 11b. BARRAS COMPRADAS ANTES DEL PERIODO =====")
    print(f"  barras_compradas en el periodo: {res['barras_compradas']}")
    print(f"  barras_vendidas en el periodo:  {res['barras_vendidas']}")
    print(f"  barras_pendientes (con signo):  {res['barras_pendientes']}")
    print(f"  renglon 'mozzarella': costo={vendida['costo']} · "
          f"ganancia={vendida['ganancia']}")
    print(f"  renglon 'mozzarella_anterior': {anterior['etiqueta']} · "
          f"barras={anterior['barras']} · costo={anterior['costo']}")

    assert D(res["barras_compradas"]) == D(0)
    assert D(res["barras_vendidas"]) == D(60)
    assert D(res["barras_pendientes"]) == D(-60)
    # Sin compras en el período no hay costo que repartir: el ingreso es ganancia
    # pura de este rango, y el renglón 'anterior' dice de dónde salieron las barras.
    assert anterior is not None, "no salió el renglón de las barras de antes"
    assert anterior["unidad"] == "barra"
    assert D(anterior["barras"]) == D(60)
    assert D(anterior["costo"]) == D(0)
    # Y el invariante aguanta también aquí
    suma = sum(D(f["ganancia"]) for f in res["por_producto"])
    print(f"  suma del desglose = {suma} vs ganancia = {res['ganancia_estimada']}")
    assert suma == D(res["ganancia_estimada"])


def test_el_detalle_por_productor_reparte_cada_unidad_por_su_lado(client, h):
    """A un productor que solo vendió barras no le puede salir ganancia cero.

    El reparto se hace DOS VECES, una por unidad: el neto de las ventas en kilos
    entre los kilos comprados y el de las ventas en barras entre las barras
    compradas. Si se repartiera todo el neto entre los kilos, la plata de la
    mozzarella se les acreditaría a los productores de queso y el ranking diría
    que el mejor negocio lo hizo alguien que no vendió una sola barra.

    Y la columna sigue sumando la ganancia del período, porque las dos partes son
    PESOS: eso es lo que el dueño verifica con la calculadora.
    """
    assert comprar_kilos(
        client, h, kilos="100", precio="18000", productor="Yeferson"
    ).status_code == 201
    assert comprar_barras(
        client, h, barras=50, precio_barra="9000", productor="Marleny"
    ).status_code == 201
    assert vender_kilos(client, h, kilos="80", precio="21000").status_code == 201
    assert vender_barras(client, h, barras=40, precio_barra="15000").status_code == 201

    res = resumen(client, h)
    print("\n===== 11. DETALLE POR PRODUCTOR, CADA UNIDAD POR SU LADO =====")
    for f in res["por_productor"]:
        print(f"  {f['productor']:<10} kilos={f['kilos']:>8} barras={f['barras']:>5} "
              f"$/kg={f['precio_promedio']:>10} $/barra={f['precio_promedio_barra']:>10} "
              f"ganancia={f['ganancia_estimada']:>12}")

    por_nombre = {f["productor"]: f for f in res["por_productor"]}
    yeferson = por_nombre["Yeferson"]
    marleny = por_nombre["Marleny"]

    # Cada uno con su unidad y la otra en cero
    assert D(yeferson["kilos"]) == D(100) and D(yeferson["barras"]) == D(0)
    assert D(marleny["barras"]) == D(50) and D(marleny["kilos"]) == D(0)
    # El precio promedio de cada uno sale de la plata de SU unidad
    assert D(yeferson["precio_promedio"]) == D("18000.00")
    assert D(yeferson["precio_promedio_barra"]) == D(0)
    assert D(marleny["precio_promedio_barra"]) == D("9000.00")
    assert D(marleny["precio_promedio"]) == D(0)
    assert D(marleny["total_comprado_barras"]) == D("450000.00")

    # La de la mozzarella NO es cero: 40 x 15.000 de venta contra 50 x 9.000
    # de compra. Si el reparto fuera uno solo sobre los kilos, sería 0.
    assert D(marleny["ganancia_estimada"]) == D("600000.00") - D("450000.00")

    # LA COLUMNA SUMA LA TARJETA: es lo que se cuadra a mano.
    suma = sum(D(f["ganancia_estimada"]) for f in res["por_productor"])
    print(f"  suma columna ganancia_estimada = {suma}  vs "
          f"ganancia_estimada = {res['ganancia_estimada']}")
    assert suma == D(res["ganancia_estimada"])


def test_la_mozzarella_no_toca_el_inventario_de_queso_ni_el_de_borona(client, h):
    """Comprar mozzarella no puede subir "Queso disponible" ni "Borona disponible".

    Es el error que la pantalla no perdonaría: el dueño mira esa tarjeta para
    saber cuánto queso le queda por vender, y unas barras metidas ahí lo mandarían
    a buscar en la bodega un queso que no existe. Tampoco se pueden pasar barras a
    borona: la borona sale de desmenuzar queso.
    """
    assert comprar_barras(client, h, barras=100, precio_barra="9000").status_code == 201
    res = resumen(client, h)
    print("\n===== 12. LA MOZZARELLA NO TOCA LOS OTROS DOS INVENTARIOS =====")
    print(f"  kilos_disponibles:  {res['kilos_disponibles']} kg")
    print(f"  borona_disponible:  {res['borona_disponible']} kg")
    print(f"  barras_disponibles: {res['barras_disponibles']} barras")
    assert D(res["kilos_disponibles"]) == D(0)
    assert D(res["borona_disponible"]) == D(0)
    assert D(res["barras_disponibles"]) == D(100)

    # Y no se puede pasar a borona lo que no es queso: no hay kilos que convertir.
    conv = client.post(
        f"{API}/conversiones",
        json={"fecha": str(date.today()), "kilos": "10", "destino": "borona",
              "precio_kilo": "5000"},
        headers=h,
    )
    detalle = conv.json().get("error", {}).get("detail", "")
    print(f"  pasar 10 kg a borona con 100 barras en bodega: {conv.status_code} · {detalle}")
    assert conv.status_code == 422

    # Ni vender queso ni borona apoyándose en las barras
    queso = vender_kilos(client, h, kilos="10", precio="21000")
    borona = vender_kilos(client, h, kilos="10", precio="8000", tipo="borona")
    print(f"  vender 10 kg de queso:  {queso.status_code}")
    print(f"  vender 10 kg de borona: {borona.status_code}")
    assert queso.status_code == 422
    assert borona.status_code == 422


# ---------------------------------------------------------------------------
# 4. LA MÁS IMPORTANTE: las cifras que el cliente ya tiene no se movieron
# ---------------------------------------------------------------------------
def test_las_cifras_viejas_en_kilos_no_se_movieron_ni_un_peso(client, h):
    """Hay un cliente real con compras, ventas, cartera y lotes YA CARGADOS.

    Esta es la prueba que protege su plata: se arma un negocio de puro queso con
    números feos (precios que no dividen redondo, borona, un ajuste a merma), se
    guardan TODAS las cifras del resumen y del panel de lotes, se mete mozzarella
    al lado, y se comprueba que NI UNA de las cifras de kilos cambió.

    Se comparan los diccionarios campo por campo y no una cifra suelta a
    propósito: un olvido en cualquier campo del resumen aparece aquí con nombre y
    apellido, en vez de pasar de largo porque la prueba miraba otro.
    """
    assert comprar_kilos(
        client, h, kilos="123.45", precio="18333", productor="Yeferson",
        dias=20, borona="7.3",
    ).status_code == 201
    assert comprar_kilos(
        client, h, kilos="87.6", precio="19111", productor="Marleny", dias=15
    ).status_code == 201
    assert vender_kilos(client, h, kilos="150.7", precio="21777", dias=10).status_code == 201
    assert vender_kilos(
        client, h, kilos="5.2", precio="8333", dias=8, tipo="borona"
    ).status_code == 201
    assert client.post(
        f"{API}/conversiones",
        json={"fecha": str(date.today() - timedelta(days=9)), "kilos": "3.7",
              "destino": "merma", "precio_kilo": "0"},
        headers=h,
    ).status_code == 201

    antes = resumen(client, h)
    lotes_antes = client.get(f"{API}/lotes", headers=h)
    assert lotes_antes.status_code == 200, lotes_antes.text
    lotes_antes = lotes_antes.json()

    # Ahora entra la mozzarella, con plata bien distinta para que cualquier fuga
    # se note de inmediato.
    assert comprar_barras(
        client, h, barras=777, precio_barra="9111", productor="Marleny", dias=12
    ).status_code == 201
    assert vender_barras(
        client, h, barras=333, precio_barra="14777", dias=6, gasto_por_barra="311"
    ).status_code == 201

    despues = resumen(client, h)
    lotes_despues = client.get(f"{API}/lotes", headers=h).json()

    # Los campos de KILOS del resumen, uno por uno
    campos_kilos = [
        "kilos_comprados", "kilos_vendidos", "kilos_borona_vendidos", "kilos_a_borona",
        "kilos_merma", "kilos_pendientes", "kilos_disponibles", "borona_disponible",
        "precio_promedio_compra", "precio_promedio_venta", "margen_por_kilo",
        "valor_realizado_kilo", "total_ventas_borona",
    ]
    print("\n===== 13. LAS CIFRAS VIEJAS EN KILOS NO SE MOVIERON =====")
    for campo in campos_kilos:
        igual = "OK " if D(antes[campo]) == D(despues[campo]) else "CAMBIO"
        print(f"  {igual} {campo:<24} {antes[campo]:>14} -> {despues[campo]:>14}")
    for campo in campos_kilos:
        assert D(antes[campo]) == D(despues[campo]), (
            f"la mozzarella movió {campo}: {antes[campo]} -> {despues[campo]}"
        )

    # El desglose por producto EN KILOS, renglón por renglón e idéntico
    kilos_antes = [f for f in antes["por_producto"] if f["unidad"] == "kg"]
    kilos_despues = [f for f in despues["por_producto"] if f["unidad"] == "kg"]
    print("  -- desglose en kilos --")
    for a, d in zip(kilos_antes, kilos_despues):
        print(f"     {a['producto']:<12} costo {a['costo']:>12} -> {d['costo']:>12} "
              f"| ganancia {a['ganancia']:>12} -> {d['ganancia']:>12}")
    assert kilos_antes == kilos_despues, "cambió un renglón del desglose en kilos"

    # El panel de LOTES entero: la mozzarella no entra al reparto FIFO, así que
    # este panel tiene que salir byte a byte igual.
    print("  -- panel de lotes --")
    print(f"     total_ganancia:  {lotes_antes['total_ganancia']} -> "
          f"{lotes_despues['total_ganancia']}")
    print(f"     total_costo:     {lotes_antes['total_costo']} -> "
          f"{lotes_despues['total_costo']}")
    print(f"     kilos_sin_lote:  {lotes_antes['kilos_sin_lote']} -> "
          f"{lotes_despues['kilos_sin_lote']}")
    print(f"     barras_fuera_del_reparto: {lotes_despues['barras_fuera_del_reparto']}")
    for campo in ("lotes", "total_ganancia", "total_kilos_comprados", "total_costo",
                  "total_ingresos", "total_por_pagar", "total_kilos_sin_vender",
                  "total_costo_sin_vender", "kilos_sin_lote", "borona_sin_lote",
                  "ingreso_sin_lote"):
        assert lotes_antes[campo] == lotes_despues[campo], (
            f"la mozzarella movió {campo} del panel de lotes"
        )
    # Pero el panel DICE que hay barras afuera: no se esconde que su ganancia
    # no está contada aquí.
    assert D(lotes_despues["barras_fuera_del_reparto"]) == D(777)

    # Y la plata SÍ se movió, porque tiene que moverse: son pesos nuevos.
    print("  -- la plata si cambia (y debe) --")
    print(f"     total_compras: {antes['total_compras']} -> {despues['total_compras']}")
    print(f"     total_ventas:  {antes['total_ventas']} -> {despues['total_ventas']}")
    print(f"     ganancia:      {antes['ganancia_estimada']} -> "
          f"{despues['ganancia_estimada']}")
    assert D(despues["total_compras"]) == D(antes["total_compras"]) + D(777) * D("9111")
    assert D(despues["total_ventas"]) == D(antes["total_ventas"]) + D(333) * D("14777")
    assert D(despues["total_gastos"]) == D(antes["total_gastos"]) + D(333) * D("311")
    # Y el desglose sigue cuadrando al peso con las dos unidades adentro
    suma = sum(D(f["ganancia"]) for f in despues["por_producto"])
    print(f"     suma del desglose = {suma} vs ganancia = {despues['ganancia_estimada']}")
    assert suma == D(despues["ganancia_estimada"])
    suma_prod = sum(D(f["ganancia_estimada"]) for f in despues["por_productor"])
    print(f"     suma por productor = {suma_prod}")
    assert suma_prod == D(despues["ganancia_estimada"])


def test_la_cartera_suma_la_plata_de_las_dos_unidades(client, h):
    """Lo que se debe por unas barras es plata que se debe igual.

    Las tarjetas "Por pagar a productores" y "Por cobrar a clientes" son de PESOS,
    no de kilos: dejar la mozzarella afuera mostraría menos deuda de la que el
    negocio tiene, que es justo al revés de lo que hay que hacer con la cartera.
    Y la columna del detalle por productor tiene que seguir sumando la tarjeta.
    """
    assert comprar_kilos(client, h, kilos="10", precio="18000").status_code == 201
    assert comprar_barras(
        client, h, barras=20, precio_barra="9000", productor="Marleny"
    ).status_code == 201
    assert vender_kilos(client, h, kilos="10", precio="21000").status_code == 201
    assert vender_barras(client, h, barras=20, precio_barra="15000").status_code == 201

    res = resumen(client, h)
    print("\n===== 14. LA CARTERA SUMA LAS DOS UNIDADES =====")
    print(f"  por_pagar_productores: {res['por_pagar_productores']}  "
          f"(180.000 de kilos + 180.000 de barras)")
    print(f"  por_cobrar_clientes:   {res['por_cobrar_clientes']}  "
          f"(210.000 de kilos + 300.000 de barras)")
    for f in res["por_productor"]:
        print(f"     {f['productor']:<10} por_pagar={f['por_pagar']}")

    assert D(res["por_pagar_productores"]) == D("180000.00") + D("180000.00")
    assert D(res["por_cobrar_clientes"]) == D("210000.00") + D("300000.00")
    # La columna suma la tarjeta: el mismo invariante de siempre, ahora con barras.
    suma = sum(D(f["por_pagar"]) for f in res["por_productor"])
    print(f"  suma columna por_pagar = {suma}")
    assert suma == D(res["por_pagar_productores"])


# ---------------------------------------------------------------------------
# 5. Documentos: el cliente y el productor tienen que reconocer su entrega
# ---------------------------------------------------------------------------
def test_el_estado_de_cuenta_separa_kilos_de_barras(client, h):
    """El documento se le entrega AL CLIENTE por WhatsApp.

    Si su fila de mozzarella dijera "0 kg" no reconocería su propia compra, y si
    dijera "8 kg" por 8 barras el documento estaría mintiendo sobre lo que se le
    despachó. Los dos totales van separados y NO existe uno que los junte.
    """
    assert comprar_kilos(client, h, kilos="40", precio="18000").status_code == 201
    assert comprar_barras(client, h, barras=8, precio_barra="9000").status_code == 201
    assert vender_kilos(client, h, kilos="40", precio="21000").status_code == 201
    assert vender_barras(client, h, barras=8, precio_barra="15000").status_code == 201

    r = client.get(f"{API}/estado-cuenta", params={"cliente": "Tienda La 33"}, headers=h)
    assert r.status_code == 200, r.text
    ec = r.json()
    print("\n===== 15. ESTADO DE CUENTA DEL CLIENTE =====")
    for v in ec["ventas"]:
        print(f"  {v['fecha']} {v['producto']:<12} unidad={v['unidad']:<6} "
              f"kilos={v['kilos']:>7} barras={v['barras']:>4} total={v['valor_total']}")
    print(f"  total_kilos:  {ec['total_kilos']} kg")
    print(f"  total_barras: {ec['total_barras']} barras")
    print(f"  saldo:        {ec['saldo']}")

    por_tipo = {v["tipo"]: v for v in ec["ventas"]}
    assert por_tipo["queso"]["unidad"] == "kg"
    assert D(por_tipo["queso"]["barras"]) == D(0)
    assert por_tipo["mozzarella"]["unidad"] == "barra"
    assert por_tipo["mozzarella"]["producto"] == "Mozzarella"
    assert D(por_tipo["mozzarella"]["kilos"]) == D(0)
    assert D(por_tipo["mozzarella"]["barras"]) == D(8)
    assert D(por_tipo["mozzarella"]["precio_barra"]) == D("15000.00")
    # Los totales, separados: 40 kg y 8 barras, nunca 48
    assert D(ec["total_kilos"]) == D(40)
    assert D(ec["total_barras"]) == D(8)
    # Y la plata sí suma: 40 x 21.000 + 8 x 15.000
    assert D(ec["total_facturado"]) == D("840000.00") + D("120000.00")
    assert D(ec["saldo"]) == D(ec["total_facturado"]) - D(ec["total_abonado"])

    # El PDF se genera y dice "barras", no "kg", en la fila de la mozzarella
    pdf = client.get(
        f"{API}/estado-cuenta/pdf", params={"cliente": "Tienda La 33"}, headers=h
    )
    print(f"  PDF: {pdf.status_code} · {len(pdf.content)} bytes")
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"


def test_el_estado_de_cuenta_del_productor_separa_kilos_de_barras(client, h):
    """El espejo, y con el mismo riesgo: este documento se usa para cuadrar
    cuentas CON EL PRODUCTOR. Si su fila de mozzarella dijera "0 kg" no
    reconocería la entrega que él mismo hizo y la reunión terminaría en discusión.
    """
    assert comprar_kilos(
        client, h, kilos="50", precio="18000", productor="Marleny"
    ).status_code == 201
    assert comprar_barras(
        client, h, barras=12, precio_barra="9000", productor="Marleny"
    ).status_code == 201

    r = client.get(
        f"{API}/estado-cuenta-productor", params={"productor": "Marleny"}, headers=h
    )
    assert r.status_code == 200, r.text
    ec = r.json()
    print("\n===== 16. ESTADO DE CUENTA DEL PRODUCTOR =====")
    for c in ec["compras_detalle"]:
        print(f"  {c['fecha']} tipo={c['tipo']:<12} unidad={c['unidad']:<6} "
              f"kilos={c['kilos']:>7} barras={c['barras']:>4} total={c['valor_total']}")
    print(f"  total_kilos:  {ec['total_kilos']} kg")
    print(f"  total_barras: {ec['total_barras']} barras")
    print(f"  saldo a favor del productor: {ec['saldo']}")

    por_tipo = {c["tipo"]: c for c in ec["compras_detalle"]}
    assert por_tipo["queso"]["unidad"] == "kg"
    assert por_tipo["mozzarella"]["unidad"] == "barra"
    assert D(por_tipo["mozzarella"]["kilos"]) == D(0)
    assert D(por_tipo["mozzarella"]["barras"]) == D(12)
    assert D(por_tipo["mozzarella"]["precio_barra"]) == D("9000.00")
    assert D(ec["total_kilos"]) == D(50)
    assert D(ec["total_barras"]) == D(12)
    assert D(ec["total_comprado"]) == D("900000.00") + D("108000.00")

    pdf = client.get(
        f"{API}/estado-cuenta-productor/pdf", params={"productor": "Marleny"}, headers=h
    )
    print(f"  PDF: {pdf.status_code} · {len(pdf.content)} bytes")
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"


def test_la_temporada_con_barras_sin_vender_no_esta_cerrada_de_verdad(client, h):
    """Una temporada con 8 barras en la bodega NO está cerrada, aunque no le quede
    un gramo de queso.

    Sin mirar las barras, la pantalla diría "cerrada de verdad" con mercancía
    todavía adentro. Son dos condiciones separadas —los kilos con los kilos y las
    barras con las barras— y no una suma.
    """
    inicio = date.today() - timedelta(days=30)
    t = client.post(
        f"{API}/temporadas",
        json={"nombre": "Semana Santa", "fecha_inicio": str(inicio),
              "fecha_fin": str(date.today())},
        headers=h,
    )
    assert t.status_code in (200, 201), t.text

    assert comprar_kilos(client, h, kilos="10", precio="18000", dias=20).status_code == 201
    assert comprar_barras(client, h, barras=8, precio_barra="9000", dias=20).status_code == 201
    # Todo el queso se vende y se cobra, pero las barras se quedan
    v = vender_kilos(client, h, kilos="10", precio="21000", dias=10).json()
    assert client.post(
        f"{API}/ventas/{v['id']}/abonos",
        json={"fecha": str(date.today()), "valor": str(v["valor_total"])},
        headers=h,
    ).status_code == 200
    compras = client.get(f"{API}/compras", headers=h).json()["items"]
    for c in compras:
        assert client.post(
            f"{API}/compras/{c['id']}/abonos",
            json={"fecha": str(date.today()), "valor": str(c["valor_total"])},
            headers=h,
        ).status_code == 200

    panel = client.get(f"{API}/temporadas", headers=h)
    assert panel.status_code == 200, panel.text
    fila_t = panel.json()["temporadas"][0]
    print("\n===== 17. TEMPORADA CON BARRAS SIN VENDER =====")
    print(f"  kilos_pendientes:  {fila_t['kilos_pendientes']} kg")
    print(f"  barras_compradas:  {fila_t['barras_compradas']} barras")
    print(f"  barras_vendidas:   {fila_t['barras_vendidas']} barras")
    print(f"  barras_pendientes: {fila_t['barras_pendientes']} barras")
    print(f"  por_cobrar: {fila_t['por_cobrar']} · por_pagar: {fila_t['por_pagar']}")
    print(f"  cerrada_de_verdad: {fila_t['cerrada_de_verdad']}")

    assert D(fila_t["kilos_pendientes"]) == D(0)
    assert D(fila_t["barras_pendientes"]) == D(8)
    assert D(fila_t["por_cobrar"]) == D(0) and D(fila_t["por_pagar"]) == D(0)
    assert fila_t["cerrada_de_verdad"] is False, (
        "la temporada se declaró cerrada con 8 barras todavía en la bodega"
    )

    # Vendidas las 8, ahora sí queda cerrada
    v2 = vender_barras(client, h, barras=8, precio_barra="15000", dias=5, contado=True)
    assert v2.status_code == 201, v2.text
    fila_t = client.get(f"{API}/temporadas", headers=h).json()["temporadas"][0]
    print(f"  tras vender las 8: barras_pendientes={fila_t['barras_pendientes']} · "
          f"cerrada_de_verdad={fila_t['cerrada_de_verdad']}")
    assert D(fila_t["barras_pendientes"]) == D(0)
    assert fila_t["cerrada_de_verdad"] is True


# ---------------------------------------------------------------------------
# 6. Las empresas no se cruzan
# ---------------------------------------------------------------------------
def test_las_barras_de_una_empresa_no_sirven_para_vender_en_la_otra(client, base_datos):
    """Cada empresa con su bodega, también en la unidad nueva.

    Es la clase de fuga que se cuela cuando se agregan consultas nuevas: si a
    alguna de las de barras se le olvidara el `empresa_id`, la empresa B podría
    vender la mozzarella de la A y su resumen mostraría barras que no compró.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    assert comprar_barras(client, ha, barras=100, precio_barra="9000").status_code == 201

    res_a = resumen(client, ha)
    res_b = resumen(client, hb)
    print("\n===== 18. LAS EMPRESAS NO SE CRUZAN =====")
    print(f"  empresa A: barras_disponibles={res_a['barras_disponibles']} · "
          f"compradas={res_a['barras_compradas']} · "
          f"compras_mozzarella={res_a['total_compras_mozzarella']}")
    print(f"  empresa B: barras_disponibles={res_b['barras_disponibles']} · "
          f"compradas={res_b['barras_compradas']} · "
          f"compras_mozzarella={res_b['total_compras_mozzarella']}")
    assert D(res_a["barras_disponibles"]) == D(100)
    assert D(res_b["barras_disponibles"]) == D(0)
    assert D(res_b["barras_compradas"]) == D(0)
    assert D(res_b["total_compras_mozzarella"]) == D(0)
    assert D(res_b["total_compras"]) == D(0)

    # Y B no puede vender lo que compró A
    fuga = vender_barras(client, hb, barras=1, precio_barra="15000")
    detalle = fuga.json().get("error", {}).get("detail", "")
    print(f"  B vende 1 barra: {fuga.status_code} · {detalle}")
    assert fuga.status_code == 422

    # La lista de compras de B tampoco ve la de A
    lista_b = client.get(f"{API}/compras", headers=hb).json()
    print(f"  compras que ve B: {lista_b['total']}")
    assert lista_b["total"] == 0

    # Y el detalle por productor de B no trae al productor de A
    assert "Yeferson" not in [f["productor"] for f in res_b["por_productor"]]
