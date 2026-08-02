"""EL FILTRO DE FECHAS de la pantalla "Utilidad por lote de producción".

POR QUÉ EXISTE ESTE ARCHIVO. El dueño pidió "filtros por fechas y que se sumen
las utilidades dependiendo de esos filtros". Suena sencillo, pero en esta
pantalla hay dos trampas y las dos hacen que las cifras queden mal sin que se
note:

1. EL FILTRO NO PUEDE TOCAR EL CÁLCULO. El reparto va de lo más viejo primero y
   necesita TODA la historia para saber qué había en bodega. Si al pedir julio se
   consultara solo julio, la leche de junio no existiría, el queso de julio
   saldría sin costo y las ventas de los primeros días quedarían "sin lote". El
   filtro recorta lo que se MUESTRA y se SUMA, nunca lo que se calcula.

2. EL RANGO ES POR FECHA DE PRODUCCIÓN, NO DE VENTA. En un lote hay dos fechas.
   Se escogió la de producción porque la pregunta de la pantalla es "cuánto
   dejaron los lotes que hice estos días": así un lote es de un solo mes y "la
   utilidad del lote" sigue siendo una sola cifra. Si se filtrara por fecha de
   venta, un lote de julio vendido en tres meses se partiría en tres y ya no se
   podría hablar de lo que dejó ese lote. (La otra pregunta, "cuánto entró estos
   días", la responde el estado de resultados, que sí va por fecha de venta.)

Y LA PRUEBA QUE MANDA, porque el dueño suma las columnas a mano con la
calculadora: con el filtro puesto, los dos desgloses de la pantalla tienen que
cuadrar AL PESO contra los lotes que se ven:

    ingresos − costo de lo vendido − lo dañado − fletes = utilidad
    costo de lo vendido + lo dañado + lo que sigue en bodega = costo de los lotes

Los números se imprimen porque el usuario los revisa a mano.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_lotes_produccion import (
    cliente_nuevo,
    montar_leche,
    panel,
    producir,
    producto_de,
    recibir,
    tipo_queso,
    vender,
    vender_con_flete,
)


def D(valor):
    return Decimal(str(valor))


def comprobar_desgloses(p, etiqueta=""):
    """Los dos desgloses del encabezado, que son los que el dueño suma a mano.

    Si alguno no cuadra, en la pantalla se ve una columna que no da la cifra
    grande, y eso es exactamente lo que le hace perder la confianza al sistema.
    """
    # 1. De dónde sale la utilidad
    utilidad = (
        D(p["total_ingresos"])
        - D(p["total_costo_vendido"])
        - D(p["total_costo_de_baja"])
        - D(p["total_gastos"])
    )
    assert utilidad == D(p["total_utilidad"]), (
        f"{etiqueta}: {p['total_ingresos']} − {p['total_costo_vendido']} − "
        f"{p['total_costo_de_baja']} − {p['total_gastos']} = {utilidad}, pero la "
        f"tarjeta dice {p['total_utilidad']}"
    )
    # 2. Dónde está la plata de la leche de esos lotes
    costo = (
        D(p["total_costo_vendido"])
        + D(p["total_costo_de_baja"])
        + D(p["total_costo_en_bodega"])
    )
    assert costo == D(p["total_costo"]), (
        f"{etiqueta}: vendido {p['total_costo_vendido']} + dañado "
        f"{p['total_costo_de_baja']} + bodega {p['total_costo_en_bodega']} = {costo}, "
        f"pero los lotes costaron {p['total_costo']}"
    )
    # 3. Los kilos también: cada kilo que se hizo está vendido, dañado o en
    # bodega, y en ningún otro sitio. Es el mismo cuadre de la plata en kilos, y
    # es lo que sostiene la frase "quedan N kg en bodega" del encabezado.
    kilos = (
        D(p["total_kilos_vendidos"])
        + D(p["total_kilos_de_baja"])
        + D(p["total_kilos_en_bodega"])
    )
    assert kilos == D(p["total_kilos"]), (
        f"{etiqueta}: {p['total_kilos_vendidos']} vendidos + "
        f"{p['total_kilos_de_baja']} dañados + {p['total_kilos_en_bodega']} en "
        f"bodega = {kilos}, pero se hicieron {p['total_kilos']} kg"
    )
    # 4. Y cada total es la suma EXACTA de las filas que se están mostrando
    for campo, en_el_lote in [
        ("total_utilidad", "utilidad"),
        ("total_ingresos", "ingresos"),
        ("total_costo", "costo_total"),
        ("total_costo_vendido", "costo_vendido"),
        ("total_costo_de_baja", "costo_de_baja"),
        ("total_costo_en_bodega", "costo_en_bodega"),
        ("total_gastos", "gastos"),
        ("total_kilos", "kilos_producidos"),
        ("total_kilos_vendidos", "kilos_vendidos"),
        ("total_kilos_de_baja", "kilos_de_baja"),
        ("total_kilos_en_bodega", "kilos_en_bodega"),
        ("total_litros", "litros_usados"),
    ]:
        suma = sum((D(l[en_el_lote]) for l in p["lotes"]), Decimal("0"))
        assert suma == D(p[campo]), (
            f"{etiqueta}: la suma de {en_el_lote} de los lotes es {suma} pero "
            f"{campo} dice {p[campo]}"
        )


def montar_tres_meses(client, h):
    """Tres lotes en tres meses distintos, todos vendidos completos.

    Los tres usan 1.000 litros a 1.800 más 100.000 de flete = 1.900.000 el lote,
    y se venden los 100 kg a 25.000, así que cada uno deja 600.000 limpios. Con
    cifras iguales, cualquier diferencia al filtrar salta a la vista.
    """
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    cliente = cliente_nuevo(client, h)
    for mes in ("06", "07", "08"):
        recibir(client, h, f"2026-{mes}-01", prov["Libardo"], 1000, transportador)
        producir(client, h, f"2026-{mes}-05", tipo, litros=1000, kilos=100)
    producto = producto_de(client, h, tipo)
    # Se venden los 300 kg de un solo golpe: el reparto los saca del lote más
    # viejo primero, así que cada lote queda vendido completo.
    vender(client, h, "2026-09-10", cliente, producto, kilos=300, precio=25000)
    return tipo, producto, cliente


# ---------------------------------------------------------------------------
# 1. Filtrar por rango devuelve SOLO los lotes de ese rango
# ---------------------------------------------------------------------------
def test_el_rango_deja_solo_los_lotes_producidos_en_esos_dias(client, base_datos):
    """Lo primero que pidió el dueño: escoger unos días y ver solo eso."""
    h = auth_headers(client, "admin.a")
    montar_tres_meses(client, h)

    completo = panel(client, h)
    julio = panel(client, h, desde="2026-07-01", hasta="2026-07-31")
    jun_jul = panel(client, h, desde="2026-06-01", hasta="2026-07-31")

    print("\n===== 1. EL RANGO RECORTA LA LISTA =====")
    print(f"  sin filtro : {[l['fecha'] for l in completo['lotes']]}")
    print(f"  julio      : {[l['fecha'] for l in julio['lotes']]}")
    print(f"  junio+julio: {[l['fecha'] for l in jun_jul['lotes']]}")
    # Vienen del más nuevo al más viejo: al buscar un lote, lo primero que se
    # busca es el último que se hizo.
    assert [l["fecha"] for l in completo["lotes"]] == [
        "2026-08-05", "2026-07-05", "2026-06-05",
    ]
    assert [l["fecha"] for l in julio["lotes"]] == ["2026-07-05"]
    assert [l["fecha"] for l in jun_jul["lotes"]] == ["2026-07-05", "2026-06-05"]
    # Los bordes ENTRAN: el 5 de julio con rango 5–5 tiene que aparecer, si no el
    # dueño escogería "hoy" y no vería la producción de hoy.
    borde = panel(client, h, desde="2026-07-05", hasta="2026-07-05")
    print(f"  solo el 5 de julio: {[l['fecha'] for l in borde['lotes']]}")
    assert [l["fecha"] for l in borde["lotes"]] == ["2026-07-05"]


# ---------------------------------------------------------------------------
# 2. Los totales corresponden a lo filtrado, y los desgloses cuadran
# ---------------------------------------------------------------------------
def test_los_totales_son_los_del_rango_y_los_desgloses_cuadran(client, base_datos):
    """"Que se sumen las utilidades dependiendo de esos filtros", tal cual lo
    pidió. Cada lote deja 600.000: un mes tiene que dar 600.000, dos 1.200.000 y
    los tres 1.800.000, sin sobrar ni faltar un peso."""
    h = auth_headers(client, "admin.a")
    montar_tres_meses(client, h)

    print("\n===== 2. LOS TOTALES SIGUEN AL FILTRO =====")
    for etiqueta, params, lotes, utilidad in [
        ("sin filtro", {}, 3, 1_800_000),
        ("junio", {"desde": "2026-06-01", "hasta": "2026-06-30"}, 1, 600_000),
        ("julio", {"desde": "2026-07-01", "hasta": "2026-07-31"}, 1, 600_000),
        ("jun+jul", {"desde": "2026-06-01", "hasta": "2026-07-31"}, 2, 1_200_000),
    ]:
        p = panel(client, h, **params)
        print(f"  {etiqueta:11s}: {len(p['lotes'])} lotes | utilidad "
              f"{p['total_utilidad']} | vendido {p['total_ingresos']} | costó "
              f"{p['total_costo']} | de eso ya salió {p['total_costo_vendido']}")
        assert len(p["lotes"]) == lotes
        assert D(p["total_utilidad"]) == utilidad
        assert D(p["total_costo"]) == lotes * 1_900_000
        assert D(p["total_ingresos"]) == lotes * 2_500_000
        assert D(p["total_kilos"]) == lotes * 100
        comprobar_desgloses(p, etiqueta)


def test_los_desgloses_cuadran_con_bodega_y_flete_de_por_medio(client, base_datos):
    """El caso de verdad, no el redondo: un lote a medio vender y con flete. Es
    donde el desglose se descuadra si alguien suma mal, porque el costo del lote
    se parte entre lo que salió y lo que sigue en bodega."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    cliente = cliente_nuevo(client, h)
    # Julio: 1.900.000 el lote (19.000/kg), se venden 40 de los 100 kg con flete
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-05", tipo, litros=1000, kilos=100)
    producto = producto_de(client, h, tipo)
    vender_con_flete(client, h, "2026-07-20", cliente, producto,
                     kilos=40, precio=25000, flete_kilo=1200)
    # Agosto: otro lote del que NO se ha vendido nada. Al filtrar julio no debe
    # aportar ni un peso, ni siquiera al queso en bodega.
    recibir(client, h, "2026-08-01", prov["Carmen"], 500, transportador)
    producir(client, h, "2026-08-05", tipo, litros=500, kilos=50)

    julio = panel(client, h, desde="2026-07-01", hasta="2026-07-31")
    print("\n===== 3. DESGLOSES CON BODEGA Y FLETE =====")
    print(f"  lotes en julio: {[l['fecha'] for l in julio['lotes']]}")
    print(f"  vendió {julio['total_ingresos']} − costó {julio['total_costo_vendido']}"
          f" − flete {julio['total_gastos']} = UTILIDAD {julio['total_utilidad']}")
    print(f"  costó la leche {julio['total_costo']} = ya salió "
          f"{julio['total_costo_vendido']} + sigue en bodega "
          f"{julio['total_costo_en_bodega']}")
    # 40 kg a 19.000 = 760.000 de costo; 40 x 25.000 = 1.000.000; flete 40 x 1.200
    assert D(julio["total_ingresos"]) == 1_000_000
    assert D(julio["total_costo_vendido"]) == 760_000
    assert D(julio["total_gastos"]) == 48_000
    assert D(julio["total_utilidad"]) == 192_000
    # Los 60 kg que quedan valen 1.140.000 y NO bajan la utilidad
    assert D(julio["total_costo_en_bodega"]) == 1_140_000
    assert D(julio["total_kilos_en_bodega"]) == 60
    # El lote de agosto no se coló por ningún lado
    assert D(julio["total_costo"]) == 1_900_000
    comprobar_desgloses(julio, "julio con bodega")

    completo = panel(client, h)
    print(f"  sin filtro: costó {completo['total_costo']} | en bodega "
          f"{completo['total_costo_en_bodega']} | utilidad {completo['total_utilidad']}")
    # Sin filtro sí entra el de agosto: 500 L de Carmen a 1.650 + 50.000 de flete
    assert D(completo["total_costo"]) == 1_900_000 + 875_000
    assert D(completo["total_utilidad"]) == 192_000
    comprobar_desgloses(completo, "sin filtro")


# ---------------------------------------------------------------------------
# 3. La fila "Total" de la lista cuadra con la tarjeta de arriba
# ---------------------------------------------------------------------------
def test_la_lista_que_ve_el_dueno_suma_la_tarjeta(client, base_datos):
    """La pantalla no lista los lotes que todavía no han vendido nada: dejaban
    una fila en $0 cada uno y llenaban la lista sin aportar. Esto es lo que
    protege esa decisión: los que se dejan fuera valen EXACTAMENTE cero, así que
    la columna que el dueño suma a mano da la misma cifra de la tarjeta."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    cliente = cliente_nuevo(client, h)
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-05", tipo, litros=1000, kilos=100)
    # Tres producciones más del mismo mes que no se han vendido
    for dia in ("10", "15", "20"):
        recibir(client, h, f"2026-07-{dia}", prov["Carmen"], 300, transportador)
        producir(client, h, f"2026-07-{dia}", tipo, litros=300, kilos=30)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-07-25", cliente, producto, kilos=100, precio=25000)

    p = panel(client, h, desde="2026-07-01", hasta="2026-07-31")
    con_venta = [l for l in p["lotes"] if D(l["kilos_vendidos"]) or D(l["kilos_de_baja"])]
    sin_venta = [l for l in p["lotes"] if not D(l["kilos_vendidos"]) and not D(l["kilos_de_baja"])]

    print("\n===== 4. LA COLUMNA QUE SE SUMA A MANO =====")
    for l in con_venta:
        print(f"  se lista  {l['fecha']} {l['tipo_queso']} "
              f"{l['kilos_producidos']} kg -> {l['utilidad']}")
    for l in sin_venta:
        print(f"  agrupado  {l['fecha']} {l['tipo_queso']} "
              f"{l['kilos_producidos']} kg -> {l['utilidad']} (sin vender)")
    suma = sum((D(l["utilidad"]) for l in con_venta), Decimal("0"))
    print(f"  suma de las filas listadas {suma} | tarjeta {p['total_utilidad']}")

    assert len(con_venta) == 1 and len(sin_venta) == 3
    # Los que no han vendido nada valen cero exacto: por eso se pueden agrupar
    assert all(D(l["utilidad"]) == 0 for l in sin_venta)
    assert suma == D(p["total_utilidad"]) == 600_000
    comprobar_desgloses(p, "columna a mano")


def test_varias_producciones_el_mismo_dia_son_lotes_distintos(client, base_datos):
    """La fecha repetida en la lista no era un error del sistema: son varias
    producciones del mismo día, a veces del mismo tipo de queso. Cada una es su
    propio lote con su propio costo, y la pantalla las distingue por tipo y
    kilos. Si algún día se agruparan por fecha, esta prueba se cae."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    campesino = tipo_queso(client, h, "Queso campesino")
    doble = tipo_queso(client, h, "Queso doble crema")
    recibir(client, h, "2026-07-19", prov["Libardo"], 1500, transportador)
    # Tres producciones el MISMO día: dos del mismo tipo y una de otro
    producir(client, h, "2026-07-19", campesino, litros=600, kilos=60)
    producir(client, h, "2026-07-19", campesino, litros=400, kilos=45)
    producir(client, h, "2026-07-19", doble, litros=500, kilos=50)

    p = panel(client, h, desde="2026-07-19", hasta="2026-07-19")
    print("\n===== 5. TRES PRODUCCIONES EL MISMO DÍA =====")
    for l in p["lotes"]:
        print(f"  {l['fecha']} · {l['tipo_queso']} · {l['kilos_producidos']} kg"
              f" · costó {l['costo_total']}")
    assert len(p["lotes"]) == 3
    assert all(l["fecha"] == "2026-07-19" for l in p["lotes"])
    # Lo que las distingue en pantalla: tipo de queso y kilos. Dos comparten tipo,
    # así que los kilos son los que terminan de separarlas.
    assert {(l["tipo_queso"], D(l["kilos_producidos"])) for l in p["lotes"]} == {
        ("Queso campesino", D(60)),
        ("Queso campesino", D(45)),
        ("Queso doble crema", D(50)),
    }
    comprobar_desgloses(p, "mismo día")


# ---------------------------------------------------------------------------
# 4. El filtro recorta la vista, NO el cálculo
# ---------------------------------------------------------------------------
def test_el_filtro_no_toca_el_reparto_de_lo_mas_viejo_primero(client, base_datos):
    """Si el filtro recortara el cálculo, al pedir agosto el reparto no sabría que
    el queso de junio y julio ya se había despachado, y le achacaría esas ventas
    al lote de agosto. El lote tiene que salir IGUAL con filtro y sin él."""
    h = auth_headers(client, "admin.a")
    montar_tres_meses(client, h)

    completo = panel(client, h)
    agosto = panel(client, h, desde="2026-08-01", hasta="2026-08-31")
    de_agosto_completo = [l for l in completo["lotes"] if l["fecha"] == "2026-08-05"][0]

    print("\n===== 6. EL FILTRO NO CAMBIA EL CÁLCULO =====")
    print(f"  el lote de agosto sin filtro: vendió {de_agosto_completo['kilos_vendidos']}"
          f" kg, costó {de_agosto_completo['costo_total']},"
          f" utilidad {de_agosto_completo['utilidad']}")
    print(f"  el mismo lote filtrando agosto: vendió "
          f"{agosto['lotes'][0]['kilos_vendidos']} kg, costó "
          f"{agosto['lotes'][0]['costo_total']}, utilidad "
          f"{agosto['lotes'][0]['utilidad']}")
    # El lote es el MISMO objeto, campo por campo
    assert agosto["lotes"][0] == de_agosto_completo
    # Y no aparecieron ventas sin lote por haber "perdido" la historia
    assert D(agosto["kilos_sin_lote"]) == 0
    assert D(agosto["litros_sin_recepcion"]) == 0
    comprobar_desgloses(agosto, "agosto")


def test_el_lote_se_cuenta_por_la_fecha_en_que_se_hizo_no_por_la_venta(client, base_datos):
    """LA DECISIÓN DE DISEÑO, escrita como prueba: el rango va por fecha de
    PRODUCCIÓN. Un lote hecho en julio y vendido en septiembre aparece al filtrar
    julio (con toda su utilidad) y NO aparece al filtrar septiembre, aunque la
    plata haya entrado en septiembre.

    Si algún día se cambia a fecha de venta, esto se cae y hay que cambiar
    también el texto de la pantalla que se lo explica al dueño."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    cliente = cliente_nuevo(client, h)
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-05", tipo, litros=1000, kilos=100)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-09-15", cliente, producto, kilos=100, precio=25000)

    julio = panel(client, h, desde="2026-07-01", hasta="2026-07-31")
    septiembre = panel(client, h, desde="2026-09-01", hasta="2026-09-30")
    print("\n===== 7. SE CUENTA POR LA FECHA DE PRODUCCIÓN =====")
    print(f"  hecho el 2026-07-05, vendido el 2026-09-15")
    print(f"  filtrando julio     : {len(julio['lotes'])} lote,"
          f" utilidad {julio['total_utilidad']}")
    print(f"  filtrando septiembre: {len(septiembre['lotes'])} lotes,"
          f" utilidad {septiembre['total_utilidad']}")
    assert len(julio["lotes"]) == 1
    assert D(julio["total_utilidad"]) == 600_000
    # La venta de septiembre está DENTRO del lote de julio, con su fecha
    assert julio["lotes"][0]["detalle_ventas"][0]["fecha"] == "2026-09-15"
    # Y septiembre no tiene lotes: ese mes no se hizo queso
    assert septiembre["lotes"] == []
    assert D(septiembre["total_utilidad"]) == 0
    comprobar_desgloses(julio, "por producción")
    comprobar_desgloses(septiembre, "septiembre vacío")


# ---------------------------------------------------------------------------
# 5. Lo que el filtro NO puede esconder
# ---------------------------------------------------------------------------
def test_los_avisos_no_se_esconden_al_filtrar(client, base_datos):
    """La leche sin usar y el queso vendido sin lote son fotos de HOY y avisos de
    que falta cargar algo. Si se recortaran por el rango, el dueño cambiaría de
    mes y creería que el problema se arregló solo."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    # Junio: entran 1.000 litros y solo se usan 600. Quedan 400 sin usar.
    recibir(client, h, "2026-06-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-06-05", tipo, litros=600, kilos=60)
    # Julio: una producción sin leche que la respalde
    producir(client, h, "2026-07-05", tipo, litros=200, kilos=20)

    completo = panel(client, h)
    julio = panel(client, h, desde="2026-07-01", hasta="2026-07-31")
    print("\n===== 8. LOS AVISOS NO SE ESCONDEN =====")
    print(f"  sin filtro: leche sin usar {completo['litros_sin_usar']} L"
          f" ({completo['costo_litros_sin_usar']}) | litros sin respaldo "
          f"{completo['litros_sin_recepcion']}")
    print(f"  filtrando julio: leche sin usar {julio['litros_sin_usar']} L"
          f" ({julio['costo_litros_sin_usar']}) | litros sin respaldo "
          f"{julio['litros_sin_recepcion']}")
    # La leche de junio que no se usó sigue avisándose al mirar julio
    assert D(julio["litros_sin_usar"]) == D(completo["litros_sin_usar"])
    assert D(julio["costo_litros_sin_usar"]) == D(completo["costo_litros_sin_usar"])
    assert D(julio["litros_sin_recepcion"]) == D(completo["litros_sin_recepcion"])
    assert D(julio["litros_sin_usar"]) > 0
    # Pero los totales sí son solo los del rango: el lote de junio no está
    assert [l["fecha"] for l in julio["lotes"]] == ["2026-07-05"]


def test_un_rango_sin_produccion_da_ceros_no_un_error(client, base_datos):
    """La pantalla tiene que poder decir "no se hizo queso en esos días" en vez de
    reventar o de mostrar la cifra del mes anterior."""
    h = auth_headers(client, "admin.a")
    montar_tres_meses(client, h)

    p = panel(client, h, desde="2026-01-01", hasta="2026-01-31")
    print("\n===== 9. UN RANGO VACÍO =====")
    print(f"  lotes={len(p['lotes'])} utilidad={p['total_utilidad']}"
          f" costo={p['total_costo']} mejor={p['mejor']}")
    assert p["lotes"] == []
    assert D(p["total_utilidad"]) == 0
    assert D(p["total_costo"]) == 0
    assert D(p["total_costo_vendido"]) == 0
    assert D(p["total_kilos_vendidos"]) == 0
    assert p["mejor"] is None and p["peor"] is None
    comprobar_desgloses(p, "rango vacío")


def test_el_rango_al_reves_avisa_en_vez_de_mostrar_la_pantalla_vacia(client, base_datos):
    """Con un rango invertido la lista saldría vacía y parecería que se perdieron
    los datos. Mejor decirlo."""
    h = auth_headers(client, "admin.a")
    montar_tres_meses(client, h)

    r = client.get(
        "/api/v1/produccion/lotes",
        params={"desde": "2026-08-31", "hasta": "2026-08-01"},
        headers=h,
    )
    print("\n===== 10. RANGO AL REVÉS =====")
    print(f"  desde 31/08 hasta 01/08 -> {r.status_code} {r.json().get('detail')}")
    assert r.status_code == 422, r.text
    assert "anterior" in str(r.json()).lower()


# ---------------------------------------------------------------------------
# 6. Con filtro puesto, las empresas siguen sin cruzarse
# ---------------------------------------------------------------------------
def test_el_filtro_no_deja_ver_lotes_de_otra_empresa(client, base_datos):
    """Un parámetro nuevo en la consulta es justo donde se cuela un filtro que se
    olvida del empresa_id. Se prueba con el mismo rango en las dos empresas."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    montar_tres_meses(client, ha)

    # La empresa B hace su propio queso, el mismo día que la A
    transportador_b, prov_b = montar_leche(client, hb)
    tipo_b = tipo_queso(client, hb, "Queso B")
    recibir(client, hb, "2026-07-01", prov_b["Libardo"], 500, transportador_b)
    producir(client, hb, "2026-07-05", tipo_b, litros=500, kilos=50)

    julio_a = panel(client, ha, desde="2026-07-01", hasta="2026-07-31")
    julio_b = panel(client, hb, desde="2026-07-01", hasta="2026-07-31")
    print("\n===== 11. AISLAMIENTO CON FILTRO =====")
    print(f"  A en julio: {len(julio_a['lotes'])} lote(s), "
          f"{[l['tipo_queso'] for l in julio_a['lotes']]}, "
          f"costo {julio_a['total_costo']}")
    print(f"  B en julio: {len(julio_b['lotes'])} lote(s), "
          f"{[l['tipo_queso'] for l in julio_b['lotes']]}, "
          f"costo {julio_b['total_costo']}")
    assert [l["tipo_queso"] for l in julio_a["lotes"]] == ["Queso campesino"]
    assert [l["tipo_queso"] for l in julio_b["lotes"]] == ["Queso B"]
    assert D(julio_a["total_costo"]) == 1_900_000
    # 500 litros a 1.800 + 50.000 de flete (100 por litro) de la ruta de B
    assert D(julio_b["total_costo"]) == 950_000
    assert D(julio_b["total_utilidad"]) == 0
    comprobar_desgloses(julio_a, "empresa A")
    comprobar_desgloses(julio_b, "empresa B")
