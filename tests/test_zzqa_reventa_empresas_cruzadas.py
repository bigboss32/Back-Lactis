"""FRENTE 2: QUE NO SE CRUCEN LAS EMPRESAS.

El dueño maneja DOS QUESERAS en la misma instalación y el catálogo de productos es
de cada una (el UNIQUE es por empresa_id + clave), así que las dos pueden tener su
propia 'panela'. La pregunta que estas pruebas contestan es una sola:

    ¿PUEDE LO QUE HAGA UNA QUESERA MOVER UNA CIFRA DE LA OTRA?

Y se contesta de la forma que no admite discusión: se mide el resumen COMPLETO de
una quesera ANTES de que la otra exista, después se le da vida a la otra —con
productos de la misma clave, en la unidad que más incomode— y se vuelve a medir. Si
cambió un solo campo, hay fuga. Si no cambió ninguno, no hay por dónde.

Todo pasa POR EL ENDPOINT, con el token de cada administradora, que es como lo hace
el dueño. Y en cada caso se exige además LA REGLA DE ORO en las dos empresas: el
desglose por producto suma EXACTO el encabezado.
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
def ha(client, base_datos):
    return auth_headers(client, "admin.a")


@pytest.fixture()
def hb(client, base_datos):
    return auth_headers(client, "admin.b")


# ------------------------------------------------------------------ utilidades
def producto(client, h, nombre, unidad="kg", esperado=201):
    r = client.post(PROD, json={"nombre": nombre, "unidad": unidad}, headers=h)
    assert r.status_code == esperado, r.text
    return r.json()


def borrar_producto(client, h, producto_id):
    r = client.delete(f"{PROD}/{producto_id}", headers=h)
    assert r.status_code in (200, 204), r.text


def renombrar(client, h, producto_id, nombre):
    r = client.put(f"{PROD}/{producto_id}", json={"nombre": nombre}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def compra_kilos(client, h, fecha, productor, tipo, kilos, precio, borona=0):
    r = client.post(
        f"{API}/compras",
        json={"fecha": fecha, "productor": productor, "tipo": tipo,
              "kilos_brutos": kilos, "precio_kilo": precio, "borona_kilos": borona},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def venta_kilos(client, h, fecha, cliente, tipo, kilos, precio, gasto=0):
    r = client.post(
        f"{API}/ventas",
        json={"fecha": fecha, "cliente": cliente, "tipo": tipo, "kilos": kilos,
              "precio_kilo": precio, "gasto_por_kilo": gasto},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def compra_unidades(client, h, fecha, productor, tipo, barras, precio_barra):
    r = client.post(
        f"{API}/compras",
        json={"fecha": fecha, "productor": productor, "tipo": tipo,
              "barras": barras, "precio_barra": precio_barra},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def venta_unidades(client, h, fecha, cliente, tipo, barras, precio_barra, gasto=0):
    r = client.post(
        f"{API}/ventas",
        json={"fecha": fecha, "cliente": cliente, "tipo": tipo, "barras": barras,
              "precio_barra": precio_barra, "gasto_por_barra": gasto},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def resumen(client, h):
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


CAMPOS_DEL_ENCABEZADO = (
    "kilos_comprados", "total_compras", "kilos_vendidos", "total_ventas",
    "precio_promedio_compra", "precio_promedio_venta", "total_gastos",
    "ganancia_estimada", "margen_por_kilo", "valor_realizado_kilo",
    "kilos_borona_vendidos", "total_ventas_borona", "kilos_a_borona",
    "kilos_merma", "kilos_pendientes",
    "barras_compradas", "total_compras_mozzarella", "barras_vendidas",
    "total_ventas_mozzarella", "total_gastos_mozzarella",
    "precio_promedio_compra_barra", "precio_promedio_venta_barra",
    "margen_por_barra", "valor_realizado_barra", "barras_pendientes",
)


def pintar(titulo, res):
    print(f"\n===== {titulo} =====")
    for campo in CAMPOS_DEL_ENCABEZADO:
        print(f"   {campo:30} = {res[campo]}")
    print("   desglose:")
    for f in res["por_producto"]:
        print(f"     {f['producto']:22} {f['unidad']:6} kilos={f['kilos']:>9} "
              f"barras={str(f.get('barras')):>6} costo={f['costo']:>13} "
              f"ingreso={f['ingreso']:>13} gastos={f['gastos']:>10} "
              f"ganancia={f['ganancia']:>13}")
    print(f"     {'SUMA':22} {'':6} {'':>9} {'':>6} "
          f"costo={suma(res,'costo'):>13} ingreso={suma(res,'ingreso'):>13} "
          f"gastos={suma(res,'gastos'):>10} ganancia={suma(res,'ganancia'):>13}")


def suma(res, campo):
    return sum((D(f[campo]) for f in res["por_producto"]), CERO)


def exigir_regla_de_oro(res, etiqueta):
    """El desglose suma EXACTO el encabezado. Las cuatro igualdades."""
    for campo_fila, campo_total in (
        ("costo", "total_compras"),
        ("ingreso", "total_ventas"),
        ("gastos", "total_gastos"),
        ("ganancia", "ganancia_estimada"),
    ):
        assert suma(res, campo_fila) == D(res[campo_total]), (
            f"{etiqueta}: el desglose suma {suma(res, campo_fila)} en '{campo_fila}' "
            f"y el encabezado dice {res[campo_total]} en '{campo_total}'"
        )


def exigir_que_no_se_movio(antes, despues, etiqueta):
    """NI UN CAMPO del encabezado, y el desglose completo igual."""
    movidos = {
        c: (antes[c], despues[c]) for c in CAMPOS_DEL_ENCABEZADO if antes[c] != despues[c]
    }
    assert not movidos, (
        f"{etiqueta}: lo que hizo la otra quesera movió estas cifras: " + "; ".join(
            f"{c}: {v[0]} -> {v[1]}" for c, v in movidos.items()
        )
    )
    assert antes["por_producto"] == despues["por_producto"], (
        f"{etiqueta}: lo que hizo la otra quesera cambió el desglose por producto"
    )


def exigir_que_no_ve_la_plata_de_la_otra(res, plata_ajena, etiqueta):
    """Ninguna cifra de plata de este resumen puede ser la de la otra quesera."""
    for campo in ("total_compras", "total_ventas", "total_compras_mozzarella",
                  "total_ventas_mozzarella"):
        assert D(res[campo]) != plata_ajena or plata_ajena == CERO, (
            f"{etiqueta}: '{campo}' vale {res[campo]}, que es justo la plata de la "
            f"otra quesera"
        )


# ======================== 0) el camino de ESCRITURA, que es el otro lado
def test_la_validacion_de_la_unidad_mira_el_catalogo_de_su_propia_empresa(
    client, ha, hb
):
    """LA MISMA PETICIÓN, ACEPTADA EN UNA QUESERA Y RECHAZADA EN LA OTRA, y eso es
    lo correcto: cada una tiene su panela en su unidad.

    Antes el diccionario de unidades se armaba con los productos de TODAS las
    empresas y dos claves iguales se pisaban entre sí, así que cuál ganaba lo
    decidía el orden en que la base devolviera las filas: la misma compra podía
    pasar o reventar en dos intentos idénticos, y el mensaje de error hablaba de una
    unidad que en el catálogo del dueño no existe.

    Aquí se manda la MISMA petición diez veces a cada quesera, para que si dependiera
    del orden de las filas se caiga."""
    producto(client, ha, "Panela", unidad="unidad")
    producto(client, hb, "Panela", unidad="kg")

    en_kilos = {"fecha": "2026-03-01", "productor": "Patricia", "tipo": "panela",
                "kilos_brutos": 10, "precio_kilo": 100}
    en_unidades = {"fecha": "2026-03-01", "productor": "Patricia", "tipo": "panela",
                   "barras": 10, "precio_barra": 100}
    for intento in range(1, 11):
        # La B pesa su panela: en kilos SÍ, en unidades NO
        r = client.post(f"{API}/compras", json=en_kilos, headers=hb)
        assert r.status_code == 201, (
            f"intento {intento}: la B pesa su panela y le rechazaron los kilos: {r.text}"
        )
        r = client.post(f"{API}/compras", json=en_unidades, headers=hb)
        assert r.status_code == 422, (
            f"intento {intento}: la panela de la B se pesa y le aceptaron barras"
        )
        assert "los kilos y el precio por kilo" in r.text, r.text

        # La A la cuenta: en unidades SÍ, en kilos NO
        r = client.post(f"{API}/compras", json=en_unidades, headers=ha)
        assert r.status_code == 201, (
            f"intento {intento}: la A cuenta su panela y le rechazaron las unidades: "
            f"{r.text}"
        )
        r = client.post(f"{API}/compras", json=en_kilos, headers=ha)
        assert r.status_code == 422, (
            f"intento {intento}: la panela de la A se cuenta y le aceptaron kilos"
        )
        assert "las barras y el precio por barra" in r.text, r.text
    print("\n10 intentos idénticos por quesera: la respuesta fue siempre la misma "
          "y siempre la de SU catálogo")


# ============================================================== 1) misma clave,
#                                                        unidades DISTINTAS
def test_mismo_nombre_en_unidades_distintas(client, ha, hb):
    """La quesera A registra 'Panela' POR UNIDAD; la B tiene su propia 'Panela'
    POR KILO. Es el caso que rotulaba la plata de los kilos de la B como
    mozzarella y le desaparecía los kilos."""
    # --- la B trabaja sola: 100 kg a $2.000 = $200.000, y vende 60 kg
    producto(client, hb, "Panela", unidad="kg")
    compra_kilos(client, hb, "2026-03-01", "Patricia", "panela", 100, 2000)
    venta_kilos(client, hb, "2026-03-05", "Don Jose", "panela", 60, 3500, gasto=50)
    b_sola = resumen(client, hb)
    pintar("B SOLA (su panela por kilo)", b_sola)
    exigir_regla_de_oro(b_sola, "B sola")
    assert D(b_sola["kilos_comprados"]) == D(100), (
        f"B compró 100 kg de panela y su resumen dice {b_sola['kilos_comprados']}"
    )
    assert D(b_sola["total_compras"]) == D("200000.00")
    assert D(b_sola["barras_compradas"]) == CERO, (
        "la panela de la B se pesa: no puede tener barras"
    )

    # --- ahora la A estrena SU panela, pero POR UNIDAD, y le mueve movimientos
    producto(client, ha, "Panela", unidad="unidad")
    compra_unidades(client, ha, "2026-03-01", "Aurelio", "panela", 100, 2000)
    venta_unidades(client, ha, "2026-03-05", "Doña Rosa", "panela", 40, 3000, gasto=10)
    a = resumen(client, ha)
    pintar("A (su panela por unidad)", a)

    b_despues = resumen(client, hb)
    pintar("B DESPUES de que A estrenara su panela por unidad", b_despues)
    exigir_que_no_se_movio(b_sola, b_despues, "la panela por unidad de A vs el resumen de B")
    exigir_regla_de_oro(b_despues, "B después")
    exigir_regla_de_oro(a, "A")
    # Y al contrario: la A no ve los kilos ni la plata de la B
    assert D(a["kilos_comprados"]) == CERO, (
        f"la A no compró ni un kilo y su resumen dice {a['kilos_comprados']}"
    )
    exigir_que_no_ve_la_plata_de_la_otra(a, D("200000.00"), "A")


# ================================================ 2) misma clave, MISMA unidad
def test_mismo_nombre_en_la_misma_unidad(client, ha, hb):
    """Las dos con 'Queso costeño' por kilo, cada una con su plata."""
    producto(client, ha, "Queso costeño", unidad="kg")
    compra_kilos(client, ha, "2026-03-01", "Patricia", "queso_costeno", 100, 1000, borona=5)
    venta_kilos(client, ha, "2026-03-04", "Don Jose", "queso_costeno", 70, 2000, gasto=30)
    a_sola = resumen(client, ha)
    pintar("A SOLA", a_sola)

    producto(client, hb, "Queso costeño", unidad="kg")
    compra_kilos(client, hb, "2026-03-01", "Aurelio", "queso_costeno", 900, 7000)
    venta_kilos(client, hb, "2026-03-04", "Doña Rosa", "queso_costeno", 800, 9000, gasto=99)
    b = resumen(client, hb)
    pintar("B (misma clave, misma unidad)", b)

    a_despues = resumen(client, ha)
    pintar("A DESPUES", a_despues)
    exigir_que_no_se_movio(a_sola, a_despues, "misma clave y misma unidad")
    exigir_regla_de_oro(a_despues, "A")
    exigir_regla_de_oro(b, "B")
    assert D(a_despues["kilos_comprados"]) == D(100)
    assert D(b["kilos_comprados"]) == D(900)
    assert D(a_despues["total_compras"]) == D("100000.00")
    assert D(b["total_compras"]) == D("6300000.00")


# ============================== 3) borrado en suave de una, vivo en la otra
def test_producto_borrado_en_una_con_el_nombre_de_uno_vivo_en_la_otra(client, ha, hb):
    """La A estrena 'Panela' POR UNIDAD y la quita del catálogo (se puede: no tiene
    movimientos). Pero una fila borrada en suave SIGUE OCUPANDO SU CLAVE, así que
    sin el filtro de borrados esa panela muerta de la A seguiría decidiendo que la
    panela VIVA de la B se cuenta por unidades."""
    muerta = producto(client, ha, "Panela", unidad="unidad")
    borrar_producto(client, ha, muerta["id"])

    producto(client, hb, "Panela", unidad="kg")
    compra_kilos(client, hb, "2026-03-01", "Patricia", "panela", 250, 4000)
    venta_kilos(client, hb, "2026-03-06", "Don Jose", "panela", 200, 6000, gasto=25)
    b = resumen(client, hb)
    pintar("B con su panela viva (la de A está borrada en suave)", b)
    exigir_regla_de_oro(b, "B con la panela muerta de A al lado")
    assert D(b["kilos_comprados"]) == D(250), (
        f"la panela BORRADA de la A le desapareció los kilos a la B: "
        f"kilos_comprados = {b['kilos_comprados']} en vez de 250"
    )
    assert D(b["total_compras"]) == D("1000000.00")
    assert D(b["barras_compradas"]) == CERO
    assert D(b["total_compras_mozzarella"]) == CERO, (
        f"la plata de los kilos de la B salió rotulada como mozzarella: "
        f"{b['total_compras_mozzarella']}"
    )
    # Y el revés: la A revive su panela POR UNIDAD (revivir no redefine la unidad)
    revivida = producto(client, ha, "Panela", unidad="kg")
    print("\nA revive su panela: unidad =", revivida["unidad"],
          "(revivir NO redefine la unidad, vuelve como estaba)")
    b_despues = resumen(client, hb)
    exigir_que_no_se_movio(b, b_despues, "A revivió su panela por unidad")


# ==================================== 4) mayúsculas, acentos y espacios de sobra
def test_claves_con_mayusculas_acentos_y_espacios(client, ha, hb):
    """Los tres nombres son la MISMA clave ('queso_costeno'). Que la clave salga
    igual escrita desde cualquier teclado es lo que hace que los movimientos viejos
    sigan cuadrando; que las dos empresas no se vean es cosa aparte, y aquí se
    exigen las dos."""
    a_prod = producto(client, ha, "  QUESO   COSTEÑO  ", unidad="unidad")
    b_prod = producto(client, hb, "queso costeno", unidad="kg")
    print("\nclave de A:", repr(a_prod["clave"]), "nombre:", repr(a_prod["nombre"]))
    print("clave de B:", repr(b_prod["clave"]), "nombre:", repr(b_prod["nombre"]))
    assert a_prod["clave"] == b_prod["clave"] == "queso_costeno", (
        "las dos escrituras tienen que dar la MISMA clave, o los movimientos "
        "viejos se desconectan de su producto"
    )
    # Un tercer nombre que también choca, en la A: tiene que ser rechazado por
    # nombre repetido DENTRO de su empresa (no por lo que tenga la otra).
    r = client.post(PROD, json={"nombre": "Queso Costeno", "unidad": "kg"}, headers=ha)
    print("tercer nombre con la misma clave en la A:", r.status_code, r.text[:200])
    assert r.status_code == 409, (
        "dos productos con la misma clave en la MISMA empresa tienen que chocar"
    )

    compra_kilos(client, hb, "2026-03-01", "Patricia", "queso_costeno", 300, 5000)
    venta_kilos(client, hb, "2026-03-05", "Don Jose", "queso_costeno", 250, 7000, gasto=40)
    b = resumen(client, hb)
    pintar("B (kilos) con la clave gemela de A por unidad", b)
    exigir_regla_de_oro(b, "B con clave gemela")
    assert D(b["kilos_comprados"]) == D(300), (
        f"los kilos de la B se fueron: {b['kilos_comprados']}"
    )
    assert D(b["total_compras_mozzarella"]) == CERO
    a = resumen(client, ha)
    pintar("A (sin movimientos, con la clave gemela)", a)
    exigir_regla_de_oro(a, "A sin movimientos")
    assert D(a["total_compras"]) == CERO
    assert D(a["total_ventas"]) == CERO


# =========================== 5) renombrado DESPUÉS de tener movimientos
def test_renombrar_con_movimientos_no_mueve_ni_una_cifra(client, ha, hb):
    """El dueño renombra 'Panela' a 'Panela pura de caña' cuando ya tiene compras y
    ventas encima. La CLAVE no cambia (es la identidad, no el rótulo), así que
    ninguna cifra se puede mover; y la otra quesera, que tiene su propia 'Panela'
    por unidad, tampoco puede tocar nada."""
    p = producto(client, ha, "Panela", unidad="kg")
    compra_kilos(client, ha, "2026-03-01", "Patricia", "panela", 400, 1500, borona=10)
    venta_kilos(client, ha, "2026-03-05", "Don Jose", "panela", 350, 2500, gasto=60)
    antes = resumen(client, ha)
    pintar("A antes de renombrar", antes)
    exigir_regla_de_oro(antes, "A antes de renombrar")

    renombrado = renombrar(client, ha, p["id"], "Panela pura de caña")
    print("\nrenombrado:", renombrado["nombre"], "clave:", renombrado["clave"])
    assert renombrado["clave"] == "panela", "la clave NO puede cambiar al renombrar"
    despues = resumen(client, ha)
    exigir_que_no_se_movio(antes, despues, "renombrar el producto con movimientos encima")
    exigir_regla_de_oro(despues, "A después de renombrar")

    # Y ahora la B estrena SU 'Panela', por unidad, con el nombre que la A dejó libre
    producto(client, hb, "Panela", unidad="unidad")
    compra_unidades(client, hb, "2026-03-01", "Aurelio", "panela", 500, 800)
    venta_unidades(client, hb, "2026-03-07", "Doña Rosa", "panela", 300, 1200)
    final = resumen(client, ha)
    pintar("A después de que B estrenara su panela por unidad", final)
    exigir_que_no_se_movio(antes, final, "la panela por unidad de B vs el resumen de A")
    exigir_regla_de_oro(final, "A al final")
    b = resumen(client, hb)
    pintar("B (su panela por unidad)", b)
    exigir_regla_de_oro(b, "B")
    assert D(b["kilos_comprados"]) == CERO, "la B no compró kilos"
    assert D(final["kilos_comprados"]) == D(400)


# ============================ 6) los dos catálogos completos chocando de frente
def test_las_dos_queseras_con_todo_chocando(client, ha, hb):
    """El caso completo: las dos con las MISMAS TRES claves, en unidades cruzadas,
    con compras, ventas, ajustes y borona. Se mide el resumen entero de las dos."""
    # A: panela por unidad, queso_costeno por kilo
    producto(client, ha, "Panela", unidad="unidad")
    producto(client, ha, "Queso costeño", unidad="kg")
    # B: panela por KILO, queso_costeno por UNIDAD (todo al revés)
    producto(client, hb, "Panela", unidad="kg")
    producto(client, hb, "Queso costeño", unidad="unidad")

    compra_unidades(client, ha, "2026-03-01", "Aurelio", "panela", 200, 1000)
    compra_kilos(client, ha, "2026-03-01", "Patricia", "queso_costeno", 300, 4000, borona=12)
    venta_kilos(client, ha, "2026-03-05", "Don Jose", "queso_costeno", 250, 6000, gasto=45)
    venta_kilos(client, ha, "2026-03-06", "Don Jose", "borona", 8, 900)
    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-04", "kilos": 10, "destino": "merma"},
                    headers=ha)
    assert r.status_code == 201, r.text

    compra_kilos(client, hb, "2026-03-01", "Patricia", "panela", 700, 2500)
    compra_unidades(client, hb, "2026-03-01", "Aurelio", "queso_costeno", 900, 3000)
    venta_kilos(client, hb, "2026-03-05", "Doña Rosa", "panela", 600, 3300, gasto=77)

    a = resumen(client, ha)
    b = resumen(client, hb)
    pintar("A (panela=unidad, queso_costeno=kilo)", a)
    pintar("B (panela=kilo, queso_costeno=unidad)", b)
    exigir_regla_de_oro(a, "A cruzada")
    exigir_regla_de_oro(b, "B cruzada")

    # A pesa 300 kg (su queso_costeno) y cuenta 200 unidades de panela
    assert D(a["kilos_comprados"]) == D(300), (
        f"A: kilos_comprados = {a['kilos_comprados']}, esperado 300 "
        f"(solo su queso_costeno se pesa)"
    )
    # B pesa 700 kg (su panela) y cuenta 900 unidades de queso_costeno
    assert D(b["kilos_comprados"]) == D(700), (
        f"B: kilos_comprados = {b['kilos_comprados']}, esperado 700 "
        f"(solo su panela se pesa)"
    )
    # OJO: aquí NO se exige que "kilos vendidos" ni "barras compradas" digan la
    # verdad, y no es un descuido. Las dos salen mal —A vendió 250 kg y su resumen
    # dice 0,00; B compró 900 unidades y su resumen dice 0 barras— pero eso NO es
    # una fuga entre empresas: es que el resumen y el inventario solo entienden las
    # tres claves escritas a mano ('queso', 'borona', 'mozzarella'). Está medido con
    # cifras y aparte, en `test_zzqa_reventa_defectos_del_catalogo.py`. Esta prueba
    # es de aislamiento entre queseras y no se le puede pedir que además tape eso.
    print(f"\n   [otro defecto, medido aparte] A vendió 250 kg y su resumen dice "
          f"kilos_vendidos = {a['kilos_vendidos']}; B compró 900 unidades y dice "
          f"barras_compradas = {b['barras_compradas']}")
    # Y las listas tampoco se ven
    for etiqueta, h, esperados in (("A", ha, 2), ("B", hb, 2)):
        lst = client.get(f"{API}/compras", params=PERIODO, headers=h)
        assert lst.status_code == 200
        items = lst.json()["items"]
        print(f"\ncompras que ve {etiqueta}: {len(items)}")
        for it in items:
            print("   ", it["tipo"], it["productor"], it["kilos_netos"], it["barras"],
                  it["valor_total"])
        assert len(items) == esperados, (
            f"{etiqueta} ve {len(items)} compras y solo registró {esperados}"
        )
