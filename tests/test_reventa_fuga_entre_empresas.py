"""LA FUGA ENTRE EMPRESAS AL CLASIFICAR PRODUCTOS, y las dos consecuencias que
destapó en el desglose.

El dueño maneja DOS QUESERAS en la misma instalación, y el catálogo de productos
es de cada una: el UNIQUE es por (empresa_id, clave), así que las dos pueden tener
su propia 'panela'. Las dos consultas que decidían si una fila se mide en kilos o
en unidades no filtraban ni `empresa_id` ni `deleted_at`, así que la unidad de una
fila la podía estar poniendo el catálogo de la OTRA quesera: los kilos de una
compra desaparecían de "kilos comprados" y su plata salía rotulada como
mozzarella.

Y de ahí salieron dos defectos de LA REGLA DE ORO (todo desglose suma exacto la
cifra grande) que seguirían ahí aunque la fuga se cerrara:

- plata que quedaba en el encabezado sin aparecer en NINGUNA fila del desglose
  (las dos filas de barras solo se imprimen "si hubo barras", y esa plata no traía
  barras);
- un "precio promedio por barra" de $0 con plata al lado, que es la forma amable
  de decir que se dividió entre cero barras.

Cada prueba imprime las cifras porque el dueño las cuadra a mano con calculadora.
"""
from decimal import Decimal

import pytest

from app.modules.reventa.service import ReventaResumenService
from tests.conftest import auth_headers

API = "/api/v1/reventa"
PROD = f"{API}/productos"
PERIODO = {"desde": "2026-01-01", "hasta": "2026-12-31"}
CERO = Decimal("0")


def D(valor):
    return Decimal(str(valor))


def detalle(r) -> str:
    cuerpo = r.json()
    if isinstance(cuerpo, dict) and "error" in cuerpo:
        return str(cuerpo["error"].get("detail", cuerpo["error"]))
    return str(cuerpo)


@pytest.fixture()
def ha(client, base_datos):
    """La quesera A."""
    return auth_headers(client, "admin.a")


@pytest.fixture()
def hb(client, base_datos):
    """La quesera B, que es el otro negocio del mismo dueño."""
    return auth_headers(client, "admin.b")


def crear_producto(client, headers, nombre, **extra):
    r = client.post(PROD, json={"nombre": nombre, **extra}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def comprar(client, headers, **datos):
    return client.post(f"{API}/compras", json=datos, headers=headers)


def vender(client, headers, **datos):
    return client.post(f"{API}/ventas", json=datos, headers=headers)


def resumen(client, headers):
    r = client.get(f"{API}/resumen", params=PERIODO, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def lotes(client, headers):
    r = client.get(f"{API}/lotes", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def suma(filas, campo):
    return sum((D(f[campo]) for f in filas), CERO)


def imprimir_desglose(titulo, res):
    print(f"\n===== {titulo} =====")
    for campo in (
        "kilos_comprados", "total_compras", "precio_promedio_compra",
        "barras_compradas", "total_compras_mozzarella",
        "precio_promedio_compra_barra", "kilos_pendientes", "ganancia_estimada",
    ):
        print(f"  {campo:28} = {res[campo]}")
    print("  desglose por producto:")
    for f in res["por_producto"]:
        print(f"    {f['producto']:22} {f['unidad']:6} kilos={f['kilos']:>8} "
              f"barras={f['barras']:>5} costo={f['costo']:>14} "
              f"ganancia={f['ganancia']:>14}")
    print(f"    {'SUMA DE LAS FILAS':22} {'':6} {'':>8} {'':>5} "
          f"costo={suma(res['por_producto'], 'costo'):>14} "
          f"ganancia={suma(res['por_producto'], 'ganancia'):>14}")


def exigir_la_regla_de_oro(res, etiqueta=""):
    """El desglose por producto suma EXACTO el encabezado. Las tres igualdades."""
    assert suma(res["por_producto"], "costo") == D(res["total_compras"]), (
        f"{etiqueta}: los costos del desglose no suman total_compras"
    )
    assert suma(res["por_producto"], "ingreso") == D(res["total_ventas"]), (
        f"{etiqueta}: los ingresos del desglose no suman total_ventas"
    )
    assert suma(res["por_producto"], "gastos") == D(res["total_gastos"]), (
        f"{etiqueta}: los gastos del desglose no suman total_gastos"
    )
    assert suma(res["por_producto"], "ganancia") == D(res["ganancia_estimada"]), (
        f"{etiqueta}: las ganancias del desglose no suman ganancia_estimada"
    )


# ===========================================================================
# 1. EL CASO DE LAS DOS QUESERAS CON "PANELA"
# ===========================================================================
def test_la_panela_de_la_otra_quesera_no_mueve_la_plata(client, ha, hb):
    """La quesera A tiene 'Panela' POR UNIDAD; la B tiene su propia 'Panela' POR
    KILO y compra 100 kg a $2.000 = $200.000. El resumen de la B habla de kilos.

    LO QUE PASABA ANTES (medido, no supuesto):
        kilos_comprados               0,00      <- los 100 kg desaparecieron
        total_compras           $200.000
        precio_promedio_compra        $0        <- $200.000 entre 0 kilos
        barras_compradas              0
        total_compras_mozzarella $200.000      <- plata de kilos rotulada mozzarella
        precio_promedio_compra_barra  $0        <- $200.000 entre 0 barras
        desglose: 4 filas en ceros, suma $0 contra un encabezado de -$200.000

    LO QUE PASA AHORA: los 100 kg son kilos, la plata es de kilos, el promedio de
    compra es $2.000 y la fila "Aún en inventario" del desglose se lleva los
    $200.000. La cifra nueva es la correcta porque la unidad de un producto es un
    dato del catálogo DE SU EMPRESA: lo que la quesera A llame 'panela' no puede
    cambiar en qué se miden los kilos que compró la B.
    """
    crear_producto(client, ha, "Panela", unidad="unidad")
    crear_producto(client, hb, "Panela")  # por kilo, que es el defecto

    r = comprar(client, hb, fecha="2026-03-01", productor="Pedro Perez",
                tipo="panela", kilos_brutos="100", precio_kilo="2000")
    assert r.status_code == 201, detalle(r)
    assert D(r.json()["valor_total"]) == D("200000.00")
    assert r.json()["unidad"] == "kg"

    res = resumen(client, hb)
    imprimir_desglose("QUESERA B: 100 kg de panela a $2.000", res)

    assert D(res["kilos_comprados"]) == D("100.00")
    assert D(res["total_compras"]) == D("200000.00")
    assert D(res["precio_promedio_compra"]) == D("2000.00")
    # Nada de esto es mozzarella, y ninguna cifra de barras trae plata
    assert D(res["barras_compradas"]) == CERO
    assert D(res["total_compras_mozzarella"]) == CERO
    assert D(res["precio_promedio_compra_barra"]) == CERO
    # Los 100 kg están en inventario y su plata está en ESA fila
    assert D(res["kilos_pendientes"]) == D("100.00")
    pendiente = next(f for f in res["por_producto"] if f["producto"] == "pendiente")
    assert D(pendiente["kilos"]) == D("100.00")
    assert D(pendiente["costo"]) == D("200000.00")
    # No hace falta ninguna fila de rescate: la plata cayó donde le corresponde
    assert "sin_producto" not in [f["producto"] for f in res["por_producto"]]
    exigir_la_regla_de_oro(res, "quesera B con su panela por kilo")

    # Y la quesera A, que es la que tiene la panela por unidad, sigue en ceros: lo
    # que hizo la B tampoco se le devuelve.
    res_a = resumen(client, ha)
    assert D(res_a["total_compras"]) == CERO and D(res_a["kilos_comprados"]) == CERO
    exigir_la_regla_de_oro(res_a, "quesera A sin movimientos")


def test_esos_kilos_vuelven_a_entrar_al_reparto_por_lotes(client, ha, hb):
    """La misma fuga dejaba esos kilos FUERA del reparto FIFO, y con ellos su
    ganancia: el panel de ganancia por lote no mostraba ningún lote.

    Es la otra mitad del daño y va en su propia prueba porque se ve en otra
    pantalla: el motor de lotes solo recibe las compras que se miden en kilos (una
    compra de barras no tiene kilos a los que repartirle el costo), así que una
    compra de kilos mal clasificada quedaba sin lote, y la venta de esos kilos
    quedaba "sin lote" también, gritando que faltaba cargar una compra.
    """
    crear_producto(client, ha, "Panela", unidad="unidad")
    crear_producto(client, hb, "Panela")
    r = comprar(client, hb, fecha="2026-03-01", productor="Pedro Perez",
                tipo="panela", kilos_brutos="100", precio_kilo="2000")
    assert r.status_code == 201, detalle(r)
    r = vender(client, hb, fecha="2026-03-05", cliente="Tienda Sol",
               tipo="panela", kilos="60", precio_kilo="3500",
               pagada_de_contado=True)
    assert r.status_code == 201, detalle(r)

    panel = lotes(client, hb)
    print("\n===== PANEL DE LOTES DE LA QUESERA B =====")
    print(f"  kilos_sin_lote={panel['kilos_sin_lote']} "
          f"ingreso_sin_lote={panel['ingreso_sin_lote']}")
    for lote in panel["lotes"]:
        print(f"  lote {lote['fecha']}: comprados={lote['kilos_comprados']} "
              f"costo_total={lote['costo_total']} vendidos={lote['kilos_vendidos']} "
              f"ingresos={lote['ingresos']} ganancia={lote['ganancia']} "
              f"sin_vender={lote['kilos_sin_vender']}")

    assert len(panel["lotes"]) == 1, "la compra volvió a tener su lote"
    lote = panel["lotes"][0]
    assert D(lote["kilos_comprados"]) == D("100.00")
    assert D(lote["costo_total"]) == D("200000.00")
    assert D(lote["kilos_vendidos"]) == D("60.00")
    assert D(lote["costo_vendido"]) == D("120000.00")  # 60 kg × $2.000
    assert D(lote["ingresos"]) == D("210000.00")  # 60 kg × $3.500
    assert D(lote["ganancia"]) == D("90000.00")
    # Nada quedó sin lote: la venta encontró de dónde salir
    assert D(panel["kilos_sin_lote"]) == CERO
    assert D(panel["ingreso_sin_lote"]) == CERO
    # Y el cuadre peso a peso del lote
    repartido = (
        D(lote["costo_vendido"]) + D(lote["costo_borona_vendida"])
        + D(lote["costo_merma"]) + D(lote["costo_sin_vender"])
    )
    assert repartido == D(lote["costo_total"])


def test_un_producto_borrado_en_suave_ya_no_decide_la_unidad(client, ha, hb):
    """Un producto BORRADO sigue ocupando su clave, y mientras la consulta no
    filtraba `deleted_at` seguía decidiendo la unidad de las filas.

    Dos caminos, los dos reales:

    - En la MISMA empresa: la quesera A agrega 'Panela' por unidad, la quita (se
      puede, porque no tiene movimientos) y después compra 100 kg de 'panela'. La
      fila borrada seguía diciendo "esto se cuenta" y los 100 kg desaparecían.
    - Entre empresas: la panela viva de la B tampoco puede opinar sobre la A.
    """
    panela_a = crear_producto(client, ha, "Panela", unidad="unidad")
    r = client.delete(f"{PROD}/{panela_a['id']}", headers=ha)
    assert r.status_code in (200, 204), detalle(r)
    claves_vivas = [p["clave"] for p in client.get(PROD, headers=ha).json()["items"]]
    print("\ncatálogo VIVO de la quesera A:", claves_vivas)
    assert "panela" not in claves_vivas

    # La quesera B tiene la suya, viva y por unidad
    crear_producto(client, hb, "Panela", unidad="unidad")

    r = comprar(client, ha, fecha="2026-03-01", productor="Pedro Perez",
                tipo="panela", kilos_brutos="100", precio_kilo="2000")
    print("la quesera A compra 100 kg de 'panela' ->", r.status_code, detalle(r))
    assert r.status_code == 201, "ni la fila borrada ni la de la otra empresa manda"

    res = resumen(client, ha)
    imprimir_desglose("QUESERA A con su 'panela' borrada del catálogo", res)
    assert D(res["kilos_comprados"]) == D("100.00")
    assert D(res["total_compras"]) == D("200000.00")
    assert D(res["total_compras_mozzarella"]) == CERO
    exigir_la_regla_de_oro(res, "producto borrado en suave")


# ===========================================================================
# 2. LA ESCRITURA
# ===========================================================================
def test_la_validacion_de_la_unidad_mira_solo_el_catalogo_propio(client, ha, hb):
    """El camino de ESCRITURA tenía la misma fuga, y era peor: armaba un
    diccionario {clave: unidad} con los productos de TODAS las empresas, así que
    dos claves iguales se pisaban y cuál quedaba de última lo decidía el orden en
    que la base devolviera las filas. La misma compra se podía aceptar o rechazar
    en dos intentos idénticos.

    Aquí cada quesera compra SU panela como es la suya, y las dos pasan.
    """
    crear_producto(client, ha, "Panela", unidad="unidad")
    crear_producto(client, hb, "Panela")

    # La A la cuenta: barras y precio por barra
    r = comprar(client, ha, fecha="2026-03-01", productor="Pedro Perez",
                tipo="panela", barras="30", precio_barra="1500")
    print("\nA (panela por unidad) compra 30 barras ->", r.status_code, detalle(r))
    assert r.status_code == 201, detalle(r)
    # La B la pesa: kilos y precio por kilo
    r = comprar(client, hb, fecha="2026-03-01", productor="Pedro Perez",
                tipo="panela", kilos_brutos="100", precio_kilo="2000")
    print("B (panela por kilo) compra 100 kg    ->", r.status_code, detalle(r))
    assert r.status_code == 201, detalle(r)

    # Y cada una rebota lo que no es su unidad, con el mensaje de SU catálogo
    r = comprar(client, ha, fecha="2026-03-02", productor="Pedro Perez",
                tipo="panela", kilos_brutos="10", precio_kilo="2000")
    print("A intenta comprar su panela en KILOS ->", r.status_code, detalle(r))
    assert r.status_code in (400, 422)
    assert "barras" in detalle(r)
    r = comprar(client, hb, fecha="2026-03-02", productor="Pedro Perez",
                tipo="panela", barras="10", precio_barra="2000")
    print("B intenta comprar su panela en BARRAS ->", r.status_code, detalle(r))
    assert r.status_code in (400, 422)
    assert "kilos" in detalle(r)


def test_un_tipo_que_no_esta_en_el_catalogo_propio_se_pesa(client, ha, hb):
    """Una clave que no existe en el catálogo de esta empresa se trata como kilos,
    y eso tiene que ser IGUAL al escribir y al leer.

    Es la regla que hace que una fila no pueda entrar validada como una unidad y
    quedar leída como la otra: `unidades_por_clave` no la encuentra y quien pregunta
    la pesa; `se_mide_en_kilos` tampoco la reconoce y también la pesa. (Que se
    acepte un tipo que no está en el catálogo es otro asunto, medido aparte en la
    auditoría adversarial.)
    """
    crear_producto(client, hb, "Panela", unidad="unidad")
    r = comprar(client, ha, fecha="2026-03-01", productor="Pedro Perez",
                tipo="panela", kilos_brutos="50", precio_kilo="4000")
    print("\nA compra 'panela' en kilos sin tenerla en su catálogo ->",
          r.status_code, detalle(r))
    assert r.status_code == 201, detalle(r)

    res = resumen(client, ha)
    assert D(res["kilos_comprados"]) == D("50.00")
    assert D(res["total_compras"]) == D("200000.00")
    assert D(res["total_compras_mozzarella"]) == CERO
    exigir_la_regla_de_oro(res, "tipo que no está en el catálogo")


# ===========================================================================
# 3. LA REGLA DE ORO: NINGUNA PLATA SE QUEDA SIN FILA
# ===========================================================================
def test_el_desglose_suma_el_encabezado_con_las_dos_unidades_revueltas(client, ha, hb):
    """Un período con TODO revuelto: queso, borona, mozzarella (barras), un
    producto nuevo por kilo y una panela que la otra quesera mide por unidad. El
    desglose tiene que sumar el encabezado en las cuatro columnas.
    """
    crear_producto(client, hb, "Panela", unidad="unidad")  # la trampa
    crear_producto(client, ha, "Panela")  # la mía, por kilo
    crear_producto(client, ha, "Costeno")

    for datos in (
        dict(tipo="queso", kilos_brutos="200", precio_kilo="18000", borona_kilos="10"),
        dict(tipo="costeno", kilos_brutos="100", precio_kilo="5000"),
        dict(tipo="panela", kilos_brutos="100", precio_kilo="2000"),
        dict(tipo="mozzarella", barras="40", precio_barra="9000"),
    ):
        r = comprar(client, ha, fecha="2026-03-01", productor="Pedro Perez", **datos)
        assert r.status_code == 201, detalle(r)
    for datos in (
        dict(tipo="queso", kilos="120", precio_kilo="21000", gasto_concepto="Flete",
             gasto_por_kilo="300"),
        dict(tipo="borona", kilos="8", precio_kilo="6000"),
        dict(tipo="mozzarella", barras="25", precio_barra="12000",
             gasto_concepto="Flete", gasto_por_barra="400"),
    ):
        r = vender(client, ha, fecha="2026-03-10", cliente="Tienda Sol",
                   pagada_de_contado=True, **datos)
        assert r.status_code == 201, detalle(r)
    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-12", "kilos": "15", "destino": "merma"},
                    headers=ha)
    assert r.status_code == 201, detalle(r)

    res = resumen(client, ha)
    imprimir_desglose("PERÍODO REVUELTO: kilos y barras juntos", res)
    # Las dos unidades por separado, cada una con su plata
    assert D(res["kilos_comprados"]) == D("400.00")  # 200 queso + 100 costeño + 100 panela
    assert D(res["barras_compradas"]) == D("40")
    assert D(res["total_compras_mozzarella"]) == D("360000.00")
    assert D(res["total_compras"]) == D("3600000.00") + D("500000.00") + D(
        "200000.00"
    ) + D("360000.00")
    exigir_la_regla_de_oro(res, "período revuelto")
    # Y el promedio por barra es la plata de las barras entre las barras
    assert D(res["precio_promedio_compra_barra"]) == D("9000.00")


def test_la_red_del_desglose_saca_una_fila_propia_en_vez_de_perder_la_plata():
    """La red de seguridad de `_cuadrar_desglose`, probada de frente.

    Hoy ninguna combinación de datos que el sistema sepa escribir la dispara (la
    clasificación ya no deja plata sin unidad), y por eso se prueba llamándola con
    un desglose al que le falta plata: es la garantía de que si MAÑANA una rama
    nueva deja un peso sin fila, ese peso sale en su propia fila y el desglose sigue
    sumando el encabezado. "Imposible" y no "improbable".
    """
    filas = ReventaResumenService._cuadrar_desglose(
        [],
        total_compras=D("200000.00"),
        total_ventas=D("50000.00"),
        total_gastos=D("1000.00"),
    )
    print("\n===== DESGLOSE VACÍO CONTRA UN ENCABEZADO CON PLATA =====")
    for f in filas:
        print(f"  {f.producto:14} {f.etiqueta:38} costo={f.costo} "
              f"ingreso={f.ingreso} gastos={f.gastos} ganancia={f.ganancia}")
    assert [f.producto for f in filas] == ["sin_producto"]
    huerfana = filas[0]
    assert huerfana.costo == D("200000.00")
    assert huerfana.ingreso == D("50000.00")
    assert huerfana.gastos == D("1000.00")
    assert huerfana.ganancia == D("-151000.00")
    # Sin cantidad inventada: su asunto son los pesos
    assert huerfana.kilos == CERO and huerfana.barras == CERO
    # Y cuando el desglose ya cuadra, la red no agrega nada
    assert ReventaResumenService._cuadrar_desglose(
        [], total_compras=CERO, total_ventas=CERO, total_gastos=CERO
    ) == []


def test_el_promedio_por_barra_no_divide_plata_entre_cero_barras(client, ha):
    """Una fila con una clave "por unidad" pero SIN unidades: su plata no puede
    contarse como plata de barras.

    Se llega por el PUT de la compra, que no mira el catálogo (defecto aparte, ya
    medido en la auditoría): a una compra de 'panela' por unidad se le meten kilos.
    La fila queda con 50 kg, 0 barras y $200.000, y antes eso significaba
    $200.000 de "compras de mozzarella" con cero barras: el promedio por barra
    salía en $0 —dividir $200.000 entre 0 barras— y el desglose no imprimía
    ninguna fila de barras donde poner esa plata.

    Ahora la unidad de la fila la decide LO QUE LA FILA TRAE: sin barras, se pesa.
    Los $200.000 son de kilos, el promedio por kilo es real ($4.000) y el de barras
    no tiene ni plata ni barras que dividir.
    """
    crear_producto(client, ha, "Panela", unidad="unidad")
    r = comprar(client, ha, fecha="2026-03-01", productor="Pedro Perez",
                tipo="panela", barras="30", precio_barra="4000")
    assert r.status_code == 201, detalle(r)
    compra_id = r.json()["id"]
    r = client.put(f"{API}/compras/{compra_id}",
                   json={"kilos_brutos": "50", "precio_kilo": "4000"}, headers=ha)
    assert r.status_code == 200, detalle(r)
    fila = r.json()
    print(f"\ncompra editada: tipo={fila['tipo']} kilos_netos={fila['kilos_netos']} "
          f"barras={fila['barras']} valor_total={fila['valor_total']}")
    assert D(fila["kilos_netos"]) == D("50.00") and D(fila["barras"]) == CERO

    res = resumen(client, ha)
    imprimir_desglose("COMPRA CON CLAVE 'POR UNIDAD' Y CERO BARRAS", res)
    assert D(res["kilos_comprados"]) == D("50.00")
    assert D(res["precio_promedio_compra"]) == D("4000.00")
    # Ni un peso rotulado como barras, y por lo tanto nada que dividir entre cero
    assert D(res["barras_compradas"]) == CERO
    assert D(res["total_compras_mozzarella"]) == CERO
    assert D(res["precio_promedio_compra_barra"]) == CERO
    exigir_la_regla_de_oro(res, "clave por unidad con cero barras")
