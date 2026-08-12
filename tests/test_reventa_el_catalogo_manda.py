"""QUIEN MANDA SOBRE UN RENGLÓN DE PLATA ES EL PRODUCTO DEL CATÁLOGO.

DE DÓNDE SALE ESTE ARCHIVO. El catálogo (`productos_reventa`) se abrió para que el
dueño pudiera "comprar y vender algo que quiera el cliente": agrega 'Panela' o
'Huevos' y registra sus compras y sus ventas con esa clave. Pero los caminos de la
plata seguían decidiendo por el LITERAL del tipo, comparándolo contra los tres nombres
cableados en el código —queso, borona y mozzarella—. Se abrió la puerta sin conectar la
tubería, y el resultado eran cifras mal calculadas EN PRODUCCIÓN, de cinco formas
distintas y tres de ellas críticas.

Estas pruebas nacieron marcadas `xfail(strict=True)`, midiendo el daño con cifras. Ya no:
cada una exige AHORA EL COMPORTAMIENTO BUENO, con las mismas cifras que antes probaban
que estaba mal. Se imprimen todas, porque el dueño las cuadra a mano con calculadora.

LAS CINCO REGLAS QUE ESTE ARCHIVO DEFIENDE, y son una sola idea repetida:

  1. LA UNIDAD SALE DEL PRODUCTO, y decide en qué columnas va la cantidad y el precio
     —al escribir y al leer—. Una compra por unidad de un producto que no es la
     mozzarella se guarda con SU cantidad y SU precio, no en ceros.
  2. EL DESGLOSE ES POR PRODUCTO: cada producto tiene su fila, y la venta de un
     producto propio NO se cuenta como borona.
  3. UN INVENTARIO POR PRODUCTO, no tres canastas: lo que se despacha de un producto
     se compara contra lo que hay DE ESE PRODUCTO.
  4. Y eso vale también para lo que se cuenta: las panelas no comparten canasta con
     las barras de mozzarella.
  5. EL COSTO SALE DEL POZO DE SU PRODUCTO: un producto que se compra tiene costo, y
     solo el que llega gratis con otro (`subproducto_de_id`) hereda y no se paga.

Y la que las amarra todas: el desglose SUMA EXACTO el encabezado, siempre.
"""
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PROD = f"{API}/productos"
PERIODO = {"desde": "2026-01-01", "hasta": "2026-12-31"}
CERO = Decimal("0")


def D(v):
    return Decimal(str(v))


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


def producto(client, h, nombre, unidad="kg", **extra):
    r = client.post(PROD, json={"nombre": nombre, "unidad": unidad, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def resumen(client, h):
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def detalle(r) -> str:
    cuerpo = r.json()
    if isinstance(cuerpo, dict) and "error" in cuerpo:
        return str(cuerpo["error"].get("detail", cuerpo["error"]))
    return str(cuerpo)


def fila(res, producto_):
    filas = [f for f in res["por_producto"] if f["producto"] == producto_]
    assert len(filas) == 1, (
        f"se esperaba UNA fila de {producto_} y hay {len(filas)}: "
        + str([f["producto"] for f in res["por_producto"]])
    )
    return filas[0]


def existencia(res, clave):
    filas = [e for e in res["existencias"] if e["producto"] == clave]
    assert len(filas) == 1, f"no hay existencias de {clave}: {res['existencias']}"
    return filas[0]


def pintar(titulo, res, campos=()):
    print(f"\n===== {titulo} =====")
    for c in campos:
        print(f"   {c:32} = {res[c]}")
    print("   desglose:")
    for f in res["por_producto"]:
        print(f"     {f['etiqueta']:40} {f['unidad']:6} kilos={f['kilos']:>9} "
              f"barras={str(f.get('barras')):>6} costo={f['costo']:>13} "
              f"ingreso={f['ingreso']:>13} ganancia={f['ganancia']:>13}")
    print("   existencias:")
    for e in res["existencias"]:
        print(f"     {e['etiqueta']:40} {e['unidad']:6} {e['disponible']:>10}")


def exigir_la_regla_de_oro(res, titulo):
    """El desglose suma EXACTO el encabezado. Un centavo es un defecto."""
    for campo, columna in (
        ("total_compras", "costo"),
        ("total_ventas", "ingreso"),
        ("total_gastos", "gastos"),
        ("ganancia_estimada", "ganancia"),
    ):
        suma = sum((D(f[columna]) for f in res["por_producto"]), CERO)
        assert suma == D(res[campo]), (
            f"{titulo}: la columna '{columna}' del desglose suma {suma} y "
            f"'{campo}' dice {res[campo]}"
        )


# ============================================================================ 1
def test_la_compra_de_un_producto_por_unidad_guarda_su_plata(client, h):
    """CRÍTICO: LA PLATA DE LA COMPRA NO PUEDE DESAPARECER.

    `CompraQuesoService._calcular` solo tomaba la rama de las unidades cuando el tipo
    era LITERALMENTE 'mozzarella'. Cualquier otra clave por unidad —'panela', 'huevo',
    lo que el dueño agregue— caía en la rama de los kilos, que pone `barras = 0`,
    `precio_barra = 0` y calcula la plata como kilos × precio por kilo. Y como el
    esquema de entrada RECHAZA que una compra por unidad traiga kilos, esos dos
    factores eran cero por obligación: valor_total = 0 × 0 = 0.

    O sea que NO EXISTÍA forma de registrar la compra de un producto por unidad con su
    plata: se aceptaba con 201 y se guardaba en ceros. Ahora la unidad la manda el
    catálogo y las 100 panelas a $2.000 son $200.000.
    """
    producto(client, h, "Panela", unidad="unidad")
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Patricia", "tipo": "panela",
              "barras": 100, "precio_barra": 2000},
        headers=h,
    )
    assert r.status_code == 201, r.text
    guardada = r.json()
    print("\n100 panelas a $2.000 (o sea $200.000) quedaron guardadas así:")
    for c in ("tipo", "unidad", "barras", "precio_barra", "kilos_netos",
              "precio_kilo", "valor_total", "saldo"):
        print(f"   {c:16} = {guardada[c]}")

    assert D(guardada["barras"]) == D(100), (
        f"se compraron 100 panelas y quedaron guardadas {guardada['barras']}"
    )
    assert D(guardada["valor_total"]) == D("200000.00"), (
        f"100 panelas a $2.000 son $200.000 y quedó guardado {guardada['valor_total']}"
    )
    # Y no se le colaron kilos: una panela se cuenta, no se pesa.
    assert D(guardada["kilos_netos"]) == CERO and D(guardada["precio_kilo"]) == CERO
    assert guardada["unidad"] == "unidad"

    res = resumen(client, h)
    pintar("después de comprar 100 panelas a $2.000", res, (
        "kilos_comprados", "total_compras", "barras_compradas",
        "total_compras_mozzarella",
    ))
    assert D(res["total_compras"]) == D("200000.00"), (
        f"el resumen dice que se compró {res['total_compras']} y fueron $200.000"
    )
    # Ni un kilo, porque no se compró ningún kilo.
    assert D(res["kilos_comprados"]) == CERO
    # Su plata está en SU fila del desglose, con sus 100 unidades y su precio.
    en_inventario = fila(res, "panela_pendiente")
    assert D(en_inventario["barras"]) == D(100)
    assert D(en_inventario["costo"]) == D("200000.00")
    assert D(en_inventario["costo_barra"]) == D("2000.00")
    # Y las 100 panelas están en el inventario DE LA PANELA.
    assert D(existencia(res, "panela")["disponible"]) == D(100)
    # Los campos de la mozzarella hablan de la mozzarella: nadie compró mozzarella.
    assert D(res["barras_compradas"]) == CERO
    assert D(res["total_compras_mozzarella"]) == CERO
    exigir_la_regla_de_oro(res, "una compra por unidad")


# ============================================================================ 2
def test_la_venta_de_un_producto_en_kilos_no_se_cuenta_como_borona(client, h):
    """CRÍTICO: LA PLATA NO PUEDE QUEDAR ROTULADA COMO BORONA Y SIN COSTO.

    `totales_periodo(tipo='queso')` filtraba por la cadena literal 'queso' y el
    resumen sacaba la borona POR DIFERENCIA (kilos de todas las ventas en kilos −
    kilos de las de tipo 'queso'), así que TODO producto que se pesara y no se llamara
    'queso' caía en la canasta de la borona.

    Lo que el dueño veía, con 300 kg de panela comprados a $2.000 y 250 vendidos a
    $3.500:
      · "kilos vendidos" en 0,00 cuando vendió 250 kg;
      · "borona vendida" 250 kg por $875.000, y él no tiene borona;
      · el renglón de la borona con ingreso $875.000 y COSTO $0 —ganancia pura,
        porque la borona es subproducto sin costo—, o sea la ganancia INFLADA;
      · el costo de esos 250 kg ($500.000) parado en "queso que sigue en inventario",
        que además decía 300 kg cuando en bodega quedaban 50.
    La columna SUMABA el encabezado, así que la calculadora no lo delataba: lo que
    estaba mal era a QUÉ RENGLÓN se le anotaba cada peso.

    Ahora la borona es borona por su `subproducto_de_id` y no por su nombre, y la
    panela tiene su propia fila, su propio costo y su propio inventario.
    """
    producto(client, h, "Panela", unidad="kg")
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Patricia", "tipo": "panela",
              "kilos_brutos": 300, "precio_kilo": 2000},
        headers=h,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-05", "cliente": "Don Jose", "tipo": "panela",
              "kilos": 250, "precio_kilo": 3500, "gasto_por_kilo": 40},
        headers=h,
    )
    assert r.status_code == 201, r.text

    res = resumen(client, h)
    pintar("300 kg de panela comprados, 250 vendidos", res, (
        "kilos_comprados", "total_compras", "kilos_vendidos", "total_ventas",
        "kilos_borona_vendidos", "total_ventas_borona", "kilos_a_borona",
        "kilos_pendientes", "precio_promedio_venta", "margen_por_kilo",
    ))
    assert D(res["kilos_vendidos"]) == D(250), (
        f"se vendieron 250 kg de panela y el resumen dice {res['kilos_vendidos']}"
    )
    assert D(res["kilos_borona_vendidos"]) == CERO, (
        f"no se vendió ni un kilo de borona y el resumen dice "
        f"{res['kilos_borona_vendidos']} kg por {res['total_ventas_borona']}"
    )
    assert D(res["total_ventas_borona"]) == CERO
    assert D(res["kilos_pendientes"]) == D(50), (
        f"en bodega quedan 50 kg de panela y el resumen dice {res['kilos_pendientes']}"
    )
    assert D(existencia(res, "panela")["disponible"]) == D(50)

    # LA PLATA, RENGLÓN POR RENGLÓN. La panela vendida: $875.000 de ingreso, su costo
    # de verdad (250 × $2.000 = $500.000) y $10.000 de gasto.
    vendida = fila(res, "panela")
    assert D(vendida["kilos"]) == D(250)
    assert D(vendida["ingreso"]) == D("875000.00")
    assert D(vendida["costo"]) == D("500000.00"), (
        "los 250 kg vendidos cuestan 250 × $2.000; con la venta contada como borona "
        "su costo era CERO y la ganancia salía inflada"
    )
    assert D(vendida["gastos"]) == D("10000.00")
    assert D(vendida["ganancia"]) == D("365000.00")
    # Y lo que quedó en bodega carga SU costo, no el de lo vendido.
    assert D(fila(res, "panela_pendiente")["costo"]) == D("100000.00")
    # La borona del catálogo existe pero no se movió: su fila va en ceros.
    assert D(fila(res, "borona")["ingreso"]) == CERO
    assert D(fila(res, "borona")["costo"]) == CERO
    exigir_la_regla_de_oro(res, "panela comprada y vendida")


# ============================================================================ 3
def test_vender_un_producto_en_kilos_baja_su_inventario(client, h):
    """CRÍTICO: NO SE PUEDE DESPACHAR SEIS VECES LA MISMA MERCANCÍA.

    El guardia de existencias comparaba contra `queso_disponible`, que era "kilos
    comprados (todos) − kilos vendidos DE TIPO 'queso' − convertidos". Una venta de
    'panela' no entraba en el sustraendo, así que el disponible NO BAJABA NUNCA: se
    compraban 100 kg y se podían vender 100 kg cuantas veces se quisiera. Se
    despacharon 600 kg de 100, con $1.600.000 de ganancia de una mercancía que no
    existe, y las seis ventas respondieron 201.

    Y como sí entraba en el minuendo al comprar, comprar panela además AUTORIZABA a
    despachar queso que no se compró.
    """
    producto(client, h, "Panela", unidad="kg")
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Patricia", "tipo": "panela",
              "kilos_brutos": 100, "precio_kilo": 2000},
        headers=h,
    )
    assert r.status_code == 201, r.text

    vendidos = CERO
    rechazada = None
    for vuelta in range(1, 7):
        r = client.post(
            f"{API}/ventas",
            json={"fecha": "2026-03-05", "cliente": "Don Jose", "tipo": "panela",
                  "kilos": 100, "precio_kilo": 3000},
            headers=h,
        )
        print(f"   venta {vuelta} de 100 kg de panela (comprados: 100) -> "
              f"{r.status_code} {detalle(r) if r.status_code != 201 else ''}")
        if r.status_code != 201:
            rechazada = vuelta
            break
        vendidos += D(100)

    assert rechazada == 2, (
        f"se despacharon {vendidos} kg de panela habiendo comprado 100: la primera "
        f"venta tenía que pasar y la SEGUNDA rebotar, y rebotó la {rechazada}"
    )
    # El mensaje habla de LA PANELA, no del queso: es lo que el dueño tiene en la mano.
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-06", "cliente": "Don Jose", "tipo": "panela",
              "kilos": 1, "precio_kilo": 3000},
        headers=h,
    )
    print("   vender 1 kg más ->", r.status_code, detalle(r))
    assert r.status_code == 422
    assert "Panela" in detalle(r) and "kg" in detalle(r)

    res = resumen(client, h)
    pintar(f"se compraron 100 kg y se vendieron {vendidos}", res, (
        "kilos_comprados", "total_compras", "total_ventas", "kilos_pendientes",
        "ganancia_estimada",
    ))
    assert D(res["kilos_pendientes"]) == CERO
    assert D(existencia(res, "panela")["disponible"]) == CERO
    # La ganancia es la de UNA venta de 100 kg, no la de seis.
    assert D(res["total_ventas"]) == D("300000.00")
    assert D(res["ganancia_estimada"]) == D("100000.00")
    exigir_la_regla_de_oro(res, "el inventario de la panela")


def test_comprar_un_producto_no_autoriza_a_despachar_otro(client, h):
    """LOS INVENTARIOS ESTÁN SEPARADOS: el queso no respalda al costeño.

    Antes el guardia medía todo contra el disponible del queso, así que 10 kg de
    costeño más 10 kg de queso comprados dejaban vender 20 kg de costeño: los kilos de
    queso pagaban por el costeño, y el queso seguía en el inventario para venderse otra
    vez.
    """
    producto(client, h, "Costeno")
    for tipo, kilos, precio in (("costeno", 10, 5000), ("queso", 10, 20000)):
        r = client.post(
            f"{API}/compras",
            json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": tipo,
                  "kilos_brutos": kilos, "precio_kilo": precio},
            headers=h,
        )
        assert r.status_code == 201, r.text

    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-02", "cliente": "Tienda Sol", "tipo": "costeno",
              "kilos": 20, "precio_kilo": 9000},
        headers=h,
    )
    print("\nVENDER 20 kg de costeño con 10 de costeño + 10 de queso ->",
          r.status_code, detalle(r))
    assert r.status_code == 422, "los 10 kg de queso pagaron por el costeño"
    assert "Costeno" in detalle(r), (
        f"el mensaje tiene que hablar del costeño: {detalle(r)}"
    )
    # Los 10 que sí hay pasan.
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-02", "cliente": "Tienda Sol", "tipo": "costeno",
              "kilos": 10, "precio_kilo": 9000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)
    res = resumen(client, h)
    pintar("costeño vendido completo, queso intacto", res, ("kilos_pendientes",))
    assert D(existencia(res, "costeno")["disponible"]) == CERO
    assert D(existencia(res, "queso")["disponible"]) == D(10), (
        "el queso no se movió: nadie vendió queso"
    )


def test_los_renglones_del_mismo_producto_se_suman_contra_su_disponible(client, h):
    """La factura suma los renglones del MISMO producto antes de comparar, y los
    compara contra el disponible DE ESE PRODUCTO.

    Sin sumar primero, dos renglones de 70 y 60 kg pasan uno por uno contra 100
    disponibles y la factura despacha 130 kg que no existen.
    """
    producto(client, h, "Costeno")
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": "costeno",
              "kilos_brutos": 100, "precio_kilo": 5000},
        headers=h,
    )
    assert r.status_code == 201, r.text

    payload = {
        "tipo": "venta", "fecha": "2026-03-05", "tercero": "Tienda Sol",
        "renglones": [
            {"tipo": "costeno", "kilos": "70", "precio_kilo": "9000"},
            {"tipo": "costeno", "kilos": "60", "precio_kilo": "9500"},
        ],
    }
    r = client.post(f"{API}/documentos", json=payload, headers=h)
    print("\nFACTURA de 70 + 60 kg de costeño con 100 comprados ->",
          r.status_code, detalle(r))
    assert r.status_code == 422
    # El mensaje tiene que decir que la cuenta es LA SUMA, y de qué producto.
    assert "130" in detalle(r) and "2 renglones" in detalle(r)
    assert "Costeno" in detalle(r)

    payload["renglones"] = [
        {"tipo": "costeno", "kilos": "40", "precio_kilo": "9000"},
        {"tipo": "costeno", "kilos": "60", "precio_kilo": "9500"},
    ]
    r = client.post(f"{API}/documentos", json=payload, headers=h)
    assert r.status_code == 201, detalle(r)
    assert D(r.json()["total"]) == D("930000.00")
    res = resumen(client, h)
    pintar("factura de dos renglones del costeño", res, ("kilos_pendientes",))
    assert D(existencia(res, "costeno")["disponible"]) == CERO, (
        "se vendió el lote completo y el disponible del costeño tiene que quedar en 0"
    )


# ============================================================================ 4
def test_cada_producto_por_unidad_tiene_su_propia_canasta(client, h):
    """ALTO: LAS PANELAS NO COMPARTEN INVENTARIO CON LAS BARRAS DE MOZZARELLA.

    El resumen tenía UNA canasta para lo que se cuenta, y se llamaba 'mozzarella':
    toda fila que el catálogo marcara por unidad se sumaba ahí, así que "precio
    promedio por barra" promediaba panelas de $3.000 con barras de $12.000.

    Y el guardia de existencias de una venta por unidad que no fuera la mozzarella
    miraba el inventario DE QUESO EN KILOS y comparaba contra `fila.kilos`, que en una
    venta por unidad es CERO: se podían despachar 5.000 panelas sin haber comprado una.
    Eso además dejaba la canasta de la mozzarella en NEGATIVO, y desde ahí sus ventas
    legítimas quedaban bloqueadas.
    """
    producto(client, h, "Panela", unidad="unidad")
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-05", "cliente": "Don Jose", "tipo": "panela",
              "barras": 5000, "precio_barra": 3000},
        headers=h,
    )
    print("\nvender 5.000 panelas sin haber comprado ni una ->", r.status_code,
          detalle(r))
    assert r.status_code == 422, (
        "se despacharon 5.000 panelas sin haber comprado ni una"
    )
    assert "Panela" in detalle(r) and "unidades" in detalle(r)

    # La mozzarella, que nadie tocó, sigue con su inventario en CERO y no en negativo.
    res = resumen(client, h)
    assert D(existencia(res, "mozzarella")["disponible"]) == CERO
    assert D(existencia(res, "panela")["disponible"]) == CERO

    # Ahora sí: se compran 200 panelas y 3 barras, y cada una cuenta en la suya.
    for tipo, cantidad, precio in (("panela", 200, 3000), ("mozzarella", 3, 12000)):
        r = client.post(
            f"{API}/compras",
            json={"fecha": "2026-03-01", "productor": "Patricia", "tipo": tipo,
                  "barras": cantidad, "precio_barra": precio},
            headers=h,
        )
        assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-06", "cliente": "Don Jose", "tipo": "mozzarella",
              "barras": 3, "precio_barra": 20000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)

    res = resumen(client, h)
    pintar("200 panelas y 3 barras, cada una en su canasta", res, (
        "barras_compradas", "barras_vendidas", "total_compras_mozzarella",
        "precio_promedio_compra_barra", "barras_disponibles", "total_compras",
    ))
    # Los campos `barras_*` hablan de LA MOZZARELLA: 3 compradas a $12.000, 3 vendidas.
    assert D(res["barras_compradas"]) == D(3)
    assert D(res["barras_vendidas"]) == D(3)
    assert D(res["precio_promedio_compra_barra"]) == D("12000.00"), (
        "el promedio por barra promediaba panelas de $3.000 con barras de $12.000"
    )
    assert D(res["barras_disponibles"]) == CERO
    # Y las panelas están en SU fila y en SU inventario.
    assert D(existencia(res, "panela")["disponible"]) == D(200)
    assert D(fila(res, "panela_pendiente")["barras"]) == D(200)
    assert D(fila(res, "panela_pendiente")["costo_barra"]) == D("3000.00")
    # La plata de las dos SÍ se suma, porque los pesos son pesos.
    assert D(res["total_compras"]) == D("636000.00")  # 200×3.000 + 3×12.000
    exigir_la_regla_de_oro(res, "dos productos por unidad")


def test_la_mozzarella_si_esta_bien_controlada(client, h):
    """CONTROL: con la mozzarella —la única clave por unidad que el código conocía—
    todo funcionaba, y tiene que seguir funcionando igual. Es la prueba que demuestra
    que los defectos de arriba no eran del motor sino de las cadenas escritas a mano.
    """
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Patricia", "tipo": "mozzarella",
              "barras": 100, "precio_barra": 2000},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert D(r.json()["valor_total"]) == D("200000.00"), (
        "la mozzarella SÍ guarda su plata: 100 × $2.000 = $200.000"
    )
    assert D(r.json()["barras"]) == D(100)

    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-05", "cliente": "Don Jose", "tipo": "mozzarella",
              "barras": 500, "precio_barra": 3000},
        headers=h,
    )
    print("\nvender 500 barras de mozzarella habiendo comprado 100 ->",
          r.status_code, detalle(r))
    assert r.status_code == 422, (
        "la mozzarella SÍ controla su inventario: no se pueden vender 500 de 100"
    )
    res = resumen(client, h)
    pintar("mozzarella (el control)", res, (
        "barras_compradas", "total_compras_mozzarella",
        "precio_promedio_compra_barra", "barras_pendientes",
    ))
    assert D(res["barras_compradas"]) == D(100)
    assert D(res["total_compras_mozzarella"]) == D("200000.00")
    assert D(res["precio_promedio_compra_barra"]) == D("2000.00")


# ============================================================================ 5
def test_el_costo_de_un_producto_sale_de_sus_propias_compras(client, h):
    """CRÍTICO PARA LA CIFRA QUE EL DUEÑO CUADRA A MANO: el pozo del costo es de cada
    producto.

    Con queso y un producto nuevo en el mismo período, el desglose repartía el costo
    de TODOS los kilos comprados entre los destinos DEL QUESO. O sea que el costo de lo
    vendido del producto nuevo se le cargaba al queso, y el queso quedaba diciendo que
    tenía en bodega un queso que nunca se compró.

    Los hechos:
        compra 100 kg de QUESO   a $20.000 = $2.000.000
        compra 100 kg de COSTEÑO a  $5.000 =   $500.000
        venta   50 kg de QUESO   a $25.000 = $1.250.000
        venta  100 kg de COSTEÑO a  $9.000 =   $900.000

    La verdad, producto por producto:
        queso:   1.250.000 − (50 × 20.000 = 1.000.000) =  250.000
        costeño:   900.000 − (100 × 5.000 =   500.000) =  400.000
    """
    producto(client, h, "Costeno")
    for tipo, kilos, precio in (("queso", 100, 20000), ("costeno", 100, 5000)):
        r = client.post(
            f"{API}/compras",
            json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": tipo,
                  "kilos_brutos": kilos, "precio_kilo": precio},
            headers=h,
        )
        assert r.status_code == 201, r.text
    for tipo, kilos, precio in (("queso", 50, 25000), ("costeno", 100, 9000)):
        r = client.post(
            f"{API}/ventas",
            json={"fecha": "2026-03-05", "cliente": "Tienda Sol", "tipo": tipo,
                  "kilos": kilos, "precio_kilo": precio},
            headers=h,
        )
        assert r.status_code == 201, detalle(r)

    res = resumen(client, h)
    pintar("queso y costeño en el mismo período", res, (
        "kilos_comprados", "total_compras", "kilos_vendidos", "total_ventas",
        "precio_promedio_compra", "ganancia_estimada", "kilos_pendientes",
    ))

    # CADA PRODUCTO CON SU COSTO Y SU PRECIO PROMEDIO.
    del_queso = fila(res, "queso")
    assert D(del_queso["ingreso"]) == D("1250000.00")
    assert D(del_queso["costo"]) == D("1000000.00"), (
        "50 kg de queso a $20.000 cuestan $1.000.000; con el pozo compartido le "
        "caían los precios del costeño"
    )
    assert D(del_queso["ganancia"]) == D("250000.00")
    assert D(del_queso["costo_kilo"]) == D("20000.00")

    del_costeno = fila(res, "costeno")
    assert D(del_costeno["ingreso"]) == D("900000.00")
    assert D(del_costeno["costo"]) == D("500000.00")
    assert D(del_costeno["ganancia"]) == D("400000.00")
    assert D(del_costeno["costo_kilo"]) == D("5000.00")

    # El queso que quedó en bodega es SOLO queso: 50 kg por $1.000.000.
    en_bodega = fila(res, "pendiente")
    assert D(en_bodega["kilos"]) == D(50)
    assert D(en_bodega["costo"]) == D("1000000.00")
    # Y el costeño no dejó nada en bodega, así que su fila de residuo va en ceros.
    assert D(fila(res, "costeno_pendiente")["kilos"]) == CERO
    assert D(fila(res, "costeno_pendiente")["costo"]) == CERO

    assert D(res["kilos_pendientes"]) == D(50)
    assert D(existencia(res, "queso")["disponible"]) == D(50)
    assert D(existencia(res, "costeno")["disponible"]) == CERO
    exigir_la_regla_de_oro(res, "dos productos que se pesan")


def test_el_fifo_no_sirve_la_venta_de_un_producto_con_las_compras_de_otro(client, h):
    """CADA PRODUCTO TIENE SU COLA EN EL REPARTO FIFO.

    El reparto por lotes tenía UNA cola para todo lo que se pesa, así que la venta de
    un producto se servía de las compras de OTRO: la que estuviera primero en la cola.
    Con 100 kg de queso a $20.000 y 100 kg de costeño a $5.000 comprados el mismo día,
    los 100 kg vendidos de costeño se servían de la compra de QUESO y se les cargaba
    $20.000 el kilo. El panel de ganancia por lote mostraba la compra del queso con 100
    kg vendidos —cuando solo se vendieron 50— y el costo de cada venta cruzado.

    Esto es lo que el dueño lee en "ganancia por lote" y en "ganancia por día", y es de
    donde sale a qué productor se le carga el costo de una venta.
    """
    producto(client, h, "Costeno")
    for tipo, kilos, precio, quien in (
        ("queso", 100, 20000, "Patricia"),
        ("costeno", 100, 5000, "Sebastian"),
    ):
        r = client.post(
            f"{API}/compras",
            json={"fecha": "2026-03-01", "productor": quien, "tipo": tipo,
                  "kilos_brutos": kilos, "precio_kilo": precio},
            headers=h,
        )
        assert r.status_code == 201, detalle(r)
    for tipo, kilos, precio in (("queso", 50, 25000), ("costeno", 100, 9000)):
        r = client.post(
            f"{API}/ventas",
            json={"fecha": "2026-03-10", "cliente": "Tienda Sol", "tipo": tipo,
                  "kilos": kilos, "precio_kilo": precio},
            headers=h,
        )
        assert r.status_code == 201, detalle(r)

    panel = client.get(f"{API}/lotes", headers=h)
    assert panel.status_code == 200, panel.text
    lote = panel.json()["lotes"][0]
    print("\n===== LOTE 2026-03-01, cada producto con su cola =====")
    for c in lote["detalle_compras"]:
        print(f"   compra de {c['productor']:12} kilos={c['kilos']:>8} "
              f"precio_kilo={c['precio_kilo']:>10} vendidos={c['kilos_vendidos']:>8} "
              f"ingresos={c['ingresos']:>12} costo_realizado={c['costo_realizado']:>12} "
              f"sin_vender={c['kilos_sin_vender']:>8} ganancia={c['ganancia']:>12}")

    de_patricia = next(c for c in lote["detalle_compras"] if c["productor"] == "Patricia")
    de_sebastian = next(c for c in lote["detalle_compras"] if c["productor"] == "Sebastian")
    # LA COMPRA DE QUESO solo pagó los 50 kg de queso que se vendieron: los otros 50
    # siguen en bodega. Antes cubría 100 kg —los 50 del queso y 50 del costeño—.
    assert D(de_patricia["kilos_vendidos"]) == D(50), (
        f"la compra de queso cubrió {de_patricia['kilos_vendidos']} kg: le sirvieron "
        f"los kilos del costeño"
    )
    assert D(de_patricia["kilos_sin_vender"]) == D(50)
    assert D(de_patricia["ingresos"]) == D("1250000.00")
    assert D(de_patricia["costo_realizado"]) == D("1000000.00")
    assert D(de_patricia["ganancia"]) == D("250000.00")
    # Y LA DEL COSTEÑO pagó sus 100 kg, al precio que se le pagó a él.
    assert D(de_sebastian["kilos_vendidos"]) == D(100)
    assert D(de_sebastian["kilos_sin_vender"]) == CERO
    assert D(de_sebastian["ingresos"]) == D("900000.00")
    assert D(de_sebastian["costo_realizado"]) == D("500000.00")
    assert D(de_sebastian["ganancia"]) == D("400000.00")
    # Y el costo de lo que sigue en bodega es el del queso, no el del costeño.
    assert D(de_patricia["costo_sin_vender"]) == D("1000000.00")
    assert D(de_sebastian["costo_sin_vender"]) == CERO

    # La ganancia por día parte de lo mismo, así que también queda por producto.
    r = client.get(f"{API}/ganancia-por-dia", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    dias = r.json()
    print(f"   ganancia por día: {[(d['fecha'], d['ganancia']) for d in dias['dias']]}")
    assert D(dias["ganancia"]) == D("650000.00"), (
        "la ganancia realizada son los $250.000 del queso más los $400.000 del costeño"
    )


def test_solo_el_subproducto_hereda_y_no_se_paga(client, h):
    """LA BORONA ES BORONA POR SU `subproducto_de_id`, NO POR SU NOMBRE.

    Es la otra cara del defecto 2: si "lo que no es queso es borona" se arreglara
    mirando otra cadena, un producto propio del dueño seguiría contándose como
    subproducto sin costo. Lo que decide es la relación del catálogo.

    Con 100 kg de queso comprados a $20.000 y 20 kg de borona que llegaron GRATIS:
      · vender la borona es ganancia pura (costo cero: no se pagó);
      · vender el mismo peso de un producto PROPIO cuesta lo que costó.
    """
    producto(client, h, "Costeno")
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": "queso",
              "kilos_brutos": 100, "precio_kilo": 20000, "borona_kilos": 20},
        headers=h,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": "costeno",
              "kilos_brutos": 20, "precio_kilo": 3000},
        headers=h,
    )
    assert r.status_code == 201, r.text

    for tipo in ("borona", "costeno"):
        r = client.post(
            f"{API}/ventas",
            json={"fecha": "2026-03-02", "cliente": "Tienda Sol", "tipo": tipo,
                  "kilos": 20, "precio_kilo": 3000},
            headers=h,
        )
        assert r.status_code == 201, detalle(r)

    res = resumen(client, h)
    pintar("la borona no se paga; el costeño sí", res, (
        "kilos_vendidos", "kilos_borona_vendidos", "total_ventas_borona",
    ))
    # La borona: 20 kg vendidos, y su fila NO carga costo porque llegó gratis (los
    # kilos que consumen el pozo son los CONVERTIDOS, y no se convirtió ninguno).
    de_borona = fila(res, "borona")
    assert D(res["kilos_borona_vendidos"]) == D(20)
    assert D(de_borona["ingreso"]) == D("60000.00")
    assert D(de_borona["costo"]) == CERO, (
        "la borona llegó gratis con el lote: lo que se venda de ella es ganancia pura"
    )
    # El costeño: los mismos 20 kg y el mismo precio, pero SÍ cuesta.
    del_costeno = fila(res, "costeno")
    assert D(del_costeno["ingreso"]) == D("60000.00")
    assert D(del_costeno["costo"]) == D("60000.00"), (
        "el costeño se compró a $3.000 el kilo: contarlo como subproducto sin costo "
        "sería inventarse $60.000 de ganancia"
    )
    assert D(del_costeno["ganancia"]) == CERO
    # Y sus kilos NO son "borona vendida": son kilos vendidos.
    assert D(res["kilos_vendidos"]) == D(20)
    exigir_la_regla_de_oro(res, "subproducto contra producto propio")


# ============================================================================ y el resto
def test_un_producto_nuevo_por_kilo_de_punta_a_punta(client, h):
    """El recorrido completo de un producto que el dueño agrega: se compra, se vende,
    tiene su fila, su costo, su inventario y su renglón en el ranking de productores.
    """
    producto(client, h, "Costeno")
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": "costeno",
              "kilos_brutos": 100, "precio_kilo": 5000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)
    compra = r.json()
    assert D(compra["valor_total"]) == D("500000.00")
    assert compra["tipo"] == "costeno" and compra["unidad"] == "kg"

    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-05", "cliente": "Tienda Sol", "tipo": "costeno",
              "kilos": 60, "precio_kilo": 9000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)
    assert D(r.json()["valor_total"]) == D("540000.00")

    res = resumen(client, h)
    pintar("un producto nuevo por kilo, de punta a punta", res, (
        "kilos_comprados", "total_compras", "kilos_vendidos", "total_ventas",
        "kilos_borona_vendidos", "kilos_disponibles", "kilos_pendientes",
        "ganancia_estimada",
    ))
    # Tiene SU fila, con su nombre del catálogo en la etiqueta.
    suya = fila(res, "costeno")
    assert suya["etiqueta"] == "Vendido como Costeno"
    assert D(suya["kilos"]) == D(60)
    assert D(suya["ingreso"]) == D("540000.00")
    assert D(suya["costo"]) == D("300000.00")
    assert D(suya["ganancia"]) == D("240000.00")
    # Su plata NO se le acreditó a la borona.
    assert D(res["kilos_borona_vendidos"]) == CERO
    assert D(fila(res, "borona")["ingreso"]) == CERO
    # Su inventario bajó: 100 − 60 = 40.
    assert D(existencia(res, "costeno")["disponible"]) == D(40)
    # Y el productor tiene su fila con la ganancia de ese producto.
    del_productor = next(
        f for f in res["por_productor"] if f["productor"] == "Pedro Perez"
    )
    print("   por productor:", del_productor)
    assert D(del_productor["kilos"]) == D(100)
    assert D(del_productor["total_comprado"]) == D("500000.00")
    exigir_la_regla_de_oro(res, "producto nuevo de punta a punta")
    # La columna del ranking suma la ganancia del período.
    suma = sum((D(f["ganancia_estimada"]) for f in res["por_productor"]), CERO)
    assert suma == D(res["ganancia_estimada"]), (
        f"el ranking suma {suma} y la ganancia del período es {res['ganancia_estimada']}"
    )


def test_anular_la_compra_de_un_producto_ya_vendido_se_rechaza(client, h):
    """El guardia de anular mide el inventario DE SU PRODUCTO.

    Antes medía el del queso, y las ventas del producto nuevo no lo bajaban: la compra
    se anulaba con todo vendido y quedaba una venta sin ninguna compra detrás —plata de
    una mercancía que, según el sistema, nunca se compró—.
    """
    producto(client, h, "Costeno")
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": "costeno",
              "kilos_brutos": 100, "precio_kilo": 5000},
        headers=h,
    )
    compra_id = r.json()["id"]
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-02", "cliente": "Tienda Sol", "tipo": "costeno",
              "kilos": 100, "precio_kilo": 9000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)

    r = client.post(f"{API}/compras/{compra_id}/anular", headers=h)
    print("\nANULAR la compra con los 100 kg YA VENDIDOS ->", r.status_code, detalle(r))
    assert r.status_code == 422, "se anuló una compra cuyo producto ya salió"
    assert "Costeno" in detalle(r)

    res = resumen(client, h)
    pintar("la compra sigue viva porque su producto ya salió", res, (
        "total_compras", "ganancia_estimada",
    ))
    assert D(res["total_compras"]) == D("500000.00")
    assert D(res["ganancia_estimada"]) == D("400000.00")


def test_el_estado_de_cuenta_le_dice_al_cliente_el_nombre_del_producto(client, h):
    """EL DOCUMENTO QUE SE LE ENTREGA AL CLIENTE dice el nombre del catálogo.

    Antes el rótulo salía de `clave.capitalize()`, así que "Queso costeño artesanal"
    llegaba al cliente como 'Queso_costeno_artesanal' —con guiones bajos y sin tilde—
    y renombrar el producto no cambiaba nada de lo que él leía.
    """
    p = producto(client, h, "Queso costeno artesanal")
    print("\nclave generada:", p["clave"], f"({len(p['clave'])} caracteres)")
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": p["clave"],
              "kilos_brutos": 100, "precio_kilo": 5000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-02", "cliente": "Tienda Sol", "tipo": p["clave"],
              "kilos": 50, "precio_kilo": 9000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)

    ec = client.get(f"{API}/estado-cuenta", params={"cliente": "Tienda Sol"}, headers=h)
    assert ec.status_code == 200, ec.text
    lineas = ec.json()["ventas"]
    for f in lineas:
        print(f"   {f['fecha']}  producto='{f['producto']}'  unidad={f['unidad']} "
              f"kilos={f['kilos']} total={f['valor_total']}")
    assert lineas[0]["producto"] == "Queso costeno artesanal", (
        "el cliente estaba leyendo la clave con guiones bajos"
    )

    # Y renombrarlo SÍ cambia lo que el cliente lee: es para lo que sirve renombrar.
    r = client.put(f"{PROD}/{p['id']}", json={"nombre": "Costeno de la casa"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["clave"] == p["clave"], "la clave es la identidad y no se mueve"
    ec = client.get(f"{API}/estado-cuenta", params={"cliente": "Tienda Sol"},
                    headers=h).json()
    print("tras renombrar a 'Costeno de la casa':", ec["ventas"][0]["producto"])
    assert ec["ventas"][0]["producto"] == "Costeno de la casa"


def test_la_clave_de_un_producto_cabe_en_la_columna_del_tipo(client, h):
    """LA CLAVE TIENE QUE CABER DONDE SE GUARDA, y esto solo se veía en Postgres.

    `productos_reventa.clave` es varchar(80) y es la MISMA cadena que se guarda en
    `compras_queso.tipo` y `ventas_queso.tipo`. Esas dos columnas eran varchar(20), así
    que un nombre de dos o tres palabras generaba una clave que NO CABÍA: en SQLite
    —donde corren las pruebas— el ancho no se valida y todo pasaba, y en Postgres —la
    base del cliente— el INSERT se caía con 'value too long for type character
    varying(20)' y el dueño veía un 500 al registrar la compra. Un producto que se podía
    crear pero no se podía comprar ni vender.

    Lo cierra la migración `b1c2d3e4f5a6`, que ensancha las dos columnas a varchar(80).
    Esta prueba mide los anchos del modelo, que es lo único que se puede comprobar sin
    Postgres, y además registra el movimiento de punta a punta.
    """
    from app.modules.reventa.models import CompraQueso, ProductoReventa, VentaQueso

    assert ProductoReventa.clave.type.length == 80
    assert CompraQueso.tipo.type.length == ProductoReventa.clave.type.length, (
        "la clave del catálogo no cabe en compras_queso.tipo: en Postgres el INSERT "
        "de la compra revienta con un 500"
    )
    assert VentaQueso.tipo.type.length == ProductoReventa.clave.type.length, (
        "la clave del catálogo no cabe en ventas_queso.tipo"
    )

    p = producto(client, h, "Queso costeno artesanal de la finca")
    print(f"\nclave='{p['clave']}' -> {len(p['clave'])} caracteres; "
          f"compras_queso.tipo admite {CompraQueso.tipo.type.length}")
    assert len(p["clave"]) > 20, "la prueba necesita una clave de más de 20 caracteres"
    assert len(p["clave"]) <= CompraQueso.tipo.type.length

    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": p["clave"],
              "kilos_brutos": 100, "precio_kilo": 5000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)
    assert r.json()["tipo"] == p["clave"]
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-05", "cliente": "Tienda Sol", "tipo": p["clave"],
              "kilos": 40, "precio_kilo": 9000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)
    res = resumen(client, h)
    pintar("un producto de nombre largo, comprado y vendido", res, (
        "total_compras", "total_ventas",
    ))
    assert D(existencia(res, p["clave"])["disponible"]) == D(60)
    exigir_la_regla_de_oro(res, "clave larga")


def test_el_tipo_en_blanco_entra_como_el_producto_de_siempre(client, h):
    """Un renglón sin tipo se registra como el producto de siempre ('queso'), que es
    además cómo lo lee la clasificación. La compra entra en vez de tumbarle la pantalla
    al dueño con un 500."""
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Pedro Perez", "tipo": "",
              "kilos_brutos": 5, "precio_kilo": 1000},
        headers=h,
    )
    print("\nPOST /reventa/compras con tipo='' ->", r.status_code, detalle(r))
    assert r.status_code == 201, detalle(r)
    assert r.json()["tipo"] == "queso"
    assert D(r.json()["valor_total"]) == D("5000.00")
    res = resumen(client, h)
    assert D(res["kilos_comprados"]) == D("5.00")
    assert D(res["total_compras"]) == D("5000.00")
    exigir_la_regla_de_oro(res, "tipo en blanco")


def test_un_tipo_que_no_esta_en_el_catalogo_tiene_su_propia_fila(client, h):
    """Una clave que NO está en el catálogo —una fila vieja, una importada, un tipo
    escrito mal— se pesa y es su propio producto: su plata sale en SU fila y no se le
    acredita a la borona ni a nadie.

    Antes esos pesos caían en la canasta de la borona (que se sacaba por diferencia), y
    el dueño veía "borona vendida" que no existe.
    """
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Pedro Perez",
              "tipo": "no_esta_en_el_catalogo", "kilos_brutos": 100,
              "precio_kilo": 7000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)
    assert D(r.json()["valor_total"]) == D("700000.00")
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-02", "cliente": "Tienda Sol",
              "tipo": "no_esta_en_el_catalogo", "kilos": 100, "precio_kilo": 9000},
        headers=h,
    )
    assert r.status_code == 201, detalle(r)

    res = resumen(client, h)
    pintar("un producto que no está en el catálogo", res, (
        "kilos_comprados", "total_compras", "kilos_vendidos", "total_ventas",
        "kilos_borona_vendidos", "ganancia_estimada",
    ))
    suya = fila(res, "no_esta_en_el_catalogo")
    assert D(suya["ingreso"]) == D("900000.00")
    assert D(suya["costo"]) == D("700000.00")
    assert D(res["kilos_borona_vendidos"]) == CERO
    assert D(fila(res, "borona")["ingreso"]) == CERO, (
        "los $900.000 de un producto desconocido se le acreditaban a la BORONA"
    )
    # Y no hizo falta la fila de rescate: la plata cayó en una fila de producto.
    assert "sin_producto" not in [f["producto"] for f in res["por_producto"]]
    exigir_la_regla_de_oro(res, "producto fuera del catálogo")
