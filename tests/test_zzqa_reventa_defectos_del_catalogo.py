"""DEFECTOS ENCONTRADOS: el resumen y el inventario solo entienden TRES productos.

Esto salió buscando plata huérfana, y es más grande que lo que se estaba buscando.

El catálogo (`productos_reventa`) existe para que el dueño pueda "comprar y vender
algo que quiera el cliente": agrega 'Panela' o 'Huevos' y registra sus compras y
sus ventas con esa clave. Pero el resumen, la cartera y los tres guardias de
inventario siguen preguntando por las TRES CADENAS QUE ESTÁN ESCRITAS EN EL CÓDIGO
—'queso', 'borona' y 'mozzarella'—, no por la unidad del catálogo. Resultado: todo
producto que el dueño agregue queda mal contado, y en cuatro formas distintas.

CADA PRUEBA DE ESTE ARCHIVO ESTÁ MARCADA `xfail(strict=True)`: hoy falla porque el
defecto está vivo, y el día que se arregle va a dar XPASS —que también falla— para
que nadie lo tape sin darse cuenta. Las cifras se imprimen todas, porque el dueño
las cuadra a mano.

NINGUNO DE LOS CUATRO LO INTRODUJO EL ARREGLO DE LA FUGA ENTRE EMPRESAS: los cuatro
están igual en HEAD, y vienen del corte que permitió productos por unidad.
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


def producto(client, h, nombre, unidad="kg"):
    r = client.post(PROD, json={"nombre": nombre, "unidad": unidad}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def resumen(client, h):
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def pintar(titulo, res, campos):
    print(f"\n===== {titulo} =====")
    for c in campos:
        print(f"   {c:32} = {res[c]}")
    print("   desglose:")
    for f in res["por_producto"]:
        print(f"     {f['etiqueta']:34} {f['unidad']:6} kilos={f['kilos']:>9} "
              f"barras={str(f.get('barras')):>6} costo={f['costo']:>13} "
              f"ingreso={f['ingreso']:>13} ganancia={f['ganancia']:>13}")


# ============================================================================ 1
@pytest.mark.xfail(strict=True, reason="DEFECTO: la compra de un producto por "
                                       "unidad que no sea la mozzarella se guarda "
                                       "en $0 y con 0 unidades")
def test_la_compra_de_un_producto_por_unidad_se_guarda_en_cero(client, h):
    """LA PLATA DE LA COMPRA DESAPARECE ENTERA.

    `CompraQuesoService._calcular` solo toma la rama de las unidades cuando el tipo
    es LITERALMENTE 'mozzarella'. Cualquier otra clave por unidad —'panela',
    'huevo', lo que el dueño agregue— cae en la rama de los kilos, que pone
    `barras = 0`, `precio_barra = 0` y calcula la plata como kilos × precio por
    kilo. Y como `preparar_renglones` RECHAZA que una compra por unidad traiga
    kilos, esos dos factores son cero por obligación: valor_total = 0 × 0 = 0.

    O sea que NO EXISTE forma de registrar la compra de un producto por unidad con
    su plata. Se acepta con 201 y se guarda en ceros.
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

    res = resumen(client, h)
    pintar("resumen después de comprar 100 panelas a $2.000", res, (
        "kilos_comprados", "total_compras", "barras_compradas",
        "total_compras_mozzarella", "precio_promedio_compra_barra",
    ))
    assert D(guardada["barras"]) == D(100), (
        f"se compraron 100 panelas y quedaron guardadas {guardada['barras']}"
    )
    assert D(guardada["valor_total"]) == D("200000.00"), (
        f"100 panelas a $2.000 son $200.000 y quedó guardado {guardada['valor_total']}"
    )
    assert D(res["total_compras"]) == D("200000.00"), (
        f"el resumen dice que se compró {res['total_compras']} y fueron $200.000"
    )


# ============================================================================ 2
@pytest.mark.xfail(strict=True, reason="DEFECTO: la venta de un producto en kilos "
                                       "que no sea 'queso' se cuenta como BORONA")
def test_la_venta_de_un_producto_en_kilos_se_cuenta_como_borona(client, h):
    """LA PLATA APARECE, PERO ROTULADA COMO BORONA Y SIN COSTO.

    `VentaQuesoRepository.acumulados` y `totales_periodo(tipo='queso')` filtran por
    la cadena literal 'queso'. El resumen saca la borona POR DIFERENCIA (`kilos de
    todas las ventas en kilos − kilos de las ventas de tipo 'queso'`), así que TODO
    producto que se pese y no se llame 'queso' cae en la canasta de la borona.

    Lo que el dueño ve entonces, con 300 kg de panela comprados a $2.000 y 250
    vendidos a $3.500:
      · "kilos vendidos" en 0,00 cuando vendió 250 kg;
      · "borona vendida" 250 kg por $875.000, y él no tiene borona;
      · el renglón de la borona con ingreso $875.000 y COSTO $0 —ganancia pura—;
      · el costo de esos 250 kg ($500.000) parado en "queso que sigue en
        inventario", que además dice 300 kg cuando en bodega quedan 50.
    La columna SUMA el encabezado, así que la calculadora no lo delata: lo que está
    mal es a QUÉ RENGLÓN se le anotó cada peso.
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
    assert D(res["kilos_pendientes"]) == D(50), (
        f"en bodega quedan 50 kg de panela y el resumen dice "
        f"{res['kilos_pendientes']}"
    )


# ============================================================================ 3
@pytest.mark.xfail(strict=True, reason="DEFECTO: vender un producto en kilos que no "
                                       "sea 'queso' no baja el inventario, así que "
                                       "se puede vender sin límite")
def test_vender_un_producto_en_kilos_no_baja_el_inventario(client, h):
    """SE PUEDE VENDER LA MISMA MERCANCÍA UNA Y OTRA VEZ.

    El guardia de existencias compara contra `queso_disponible`, que es
    `kilos comprados (todos) − kilos vendidos DE TIPO 'queso' − convertidos`. Una
    venta de 'panela' no entra en el sustraendo, así que el disponible NO BAJA
    NUNCA: se compran 100 kg y se pueden vender 100 kg cuantas veces se quiera.

    Y como sí entra en el minuendo al comprar, comprar panela además AUTORIZA a
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
              f"{r.status_code}")
        if r.status_code != 201:
            rechazada = vuelta
            break
        vendidos += D(100)
    res = resumen(client, h)
    pintar(f"se compraron 100 kg y se vendieron {vendidos}", res, (
        "kilos_comprados", "total_compras", "total_ventas", "kilos_borona_vendidos",
        "kilos_pendientes", "ganancia_estimada",
    ))
    assert rechazada is not None, (
        f"se despacharon {vendidos} kg de panela habiendo comprado solo 100, y el "
        f"sistema no rechazó ni una: el inventario de un producto del catálogo no se "
        f"controla contra nada"
    )


# ============================================================================ 4
@pytest.mark.xfail(strict=True, reason="DEFECTO: los productos por unidad se suman "
                                       "todos en la canasta de la mozzarella, y su "
                                       "inventario no se controla")
def test_los_productos_por_unidad_se_mezclan_todos_en_la_mozzarella(client, h):
    """DOS PRODUCTOS DISTINTOS, UN SOLO RENGLÓN, Y UN PROMEDIO QUE NO SIGNIFICA NADA.

    El resumen tiene UNA canasta para lo que se cuenta, y se llama 'mozzarella':
    `totales_periodo_barras` mete ahí toda fila que el catálogo marque por unidad.
    Así que las panelas del dueño se suman con sus barras de mozzarella en el mismo
    renglón, y "precio promedio por barra" promedia panelas de $3.000 con barras de
    $12.000.

    Encima, el guardia de existencias de una venta por unidad que no sea mozzarella
    va a mirar el inventario DE QUESO EN KILOS (`_inventario_de` solo conoce tres
    tipos) y compara contra `fila.kilos`, que en una venta por unidad es cero: se
    pueden despachar 5.000 panelas sin haber comprado una.
    """
    producto(client, h, "Panela", unidad="unidad")
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-05", "cliente": "Don Jose", "tipo": "panela",
              "barras": 5000, "precio_barra": 3000},
        headers=h,
    )
    print("\nvender 5.000 panelas sin haber comprado ni una ->", r.status_code)
    vendio_sin_tener = r.status_code == 201

    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-06", "cliente": "Don Jose", "tipo": "mozzarella",
              "barras": 3, "precio_barra": 12000},
        headers=h,
    )
    print("vender 3 barras de mozzarella sin haber comprado ->", r.status_code,
          r.text[:150])

    res = resumen(client, h)
    pintar("panelas y mozzarella en la misma canasta", res, (
        "barras_vendidas", "total_ventas_mozzarella", "precio_promedio_venta_barra",
        "barras_pendientes", "total_ventas",
    ))
    assert not vendio_sin_tener, (
        "se despacharon 5.000 panelas sin haber comprado ni una: el guardia de "
        "existencias de un producto por unidad mira el inventario de queso en kilos"
    )


# ============================================================================ 5
def test_la_mozzarella_si_esta_bien_controlada(client, h):
    """CONTROL: con la mozzarella —la única clave por unidad que el código conoce—
    todo funciona. Esta pasa, y es la que demuestra que los cuatro defectos de
    arriba no son del motor sino de las cadenas escritas a mano."""
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
          r.status_code, r.text[:160])
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
