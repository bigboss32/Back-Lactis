"""Utilidad POR LOTE DE PRODUCCIÓN: qué dejó el queso que se hizo cada día.

EL PROBLEMA QUE RESUELVE, que es el que reportó el usuario: el estado de
resultados del mes resta TODA la leche que entró contra TODO el queso que se
vendió, pero la leche del 1 de julio se convierte en queso que puede venderse 60
días después. Las dos cifras no son del mismo queso, así que el resultado sale
negativo sin que el negocio esté perdiendo: la plata está en la bodega.

Son DOS cadenas encadenadas y hacen falta las dos:
  leche recibida -> producción (los litros salen de la leche más vieja)
  producción -> venta (los kilos salen del lote más viejo, POR TIPO DE QUESO)

LA PRUEBA QUE MANDA es el cuadre: para cada lote,
    costo del queso vendido + costo del queso en bodega = costo del lote
Cada peso de leche está en uno de esos dos sitios y en ninguno más.

Los números se imprimen porque el usuario los revisa a mano.
"""
from decimal import Decimal

from tests.conftest import auth_headers


def D(valor):
    return Decimal(str(valor))


def montar_leche(client, h, precio_litro="1800", transporte="100"):
    """Ruta, transportador y dos proveedores con precios distintos."""
    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"}, headers=h
    ).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Stella", "ruta_id": ruta["id"], "valor_transporte": transporte},
        headers=h,
    ).json()
    proveedores = {}
    for nombre, precio in [("Libardo", precio_litro), ("Carmen", "1650")]:
        proveedores[nombre] = client.post(
            "/api/v1/proveedores",
            json={"nombre": nombre, "vereda": "Granada", "precio_litro": precio,
                  "ruta_id": ruta["id"]},
            headers=h,
        ).json()
    return transportador, proveedores


def recibir(client, h, fecha, proveedor, litros, transportador=None):
    datos = {"fecha": fecha, "proveedor_id": proveedor["id"], "cantidad_litros": str(litros)}
    if transportador:
        datos["transportador_id"] = transportador["id"]
    r = client.post("/api/v1/recepciones", json=datos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def tipo_queso(client, h, nombre):
    r = client.post("/api/v1/tipos-queso", json={"nombre": nombre}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def producir(client, h, fecha, tipo, litros, kilos, merma="0"):
    r = client.post(
        "/api/v1/produccion",
        json={"fecha": fecha, "tipo_queso_id": tipo["id"], "litros_usados": str(litros),
              "peso_kg": str(kilos), "merma": str(merma)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def cliente_nuevo(client, h, nombre="Supermercado La 14"):
    r = client.post("/api/v1/clientes", json={"nombre": nombre}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def producto_de(client, h, tipo):
    """El producto terminado que la producción crea sola para ese tipo de queso."""
    productos = client.get("/api/v1/inventario/productos", headers=h).json()["items"]
    coincide = [p for p in productos if p.get("tipo_queso_id") == tipo["id"]]
    assert coincide, f"la producción no creó el producto terminado: {productos}"
    return coincide[0]


def vender(client, h, fecha, cliente, producto, kilos, precio):
    r = client.post(
        "/api/v1/ventas",
        json={"cliente_id": cliente["id"], "fecha": fecha,
              "detalles": [{"producto_id": producto["id"], "cantidad": str(kilos),
                            "precio_unitario": str(precio)}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def panel(client, h, **params):
    r = client.get("/api/v1/produccion/lotes", params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def comprobar_cuadre(p, etiqueta=""):
    """El cuadre de CADA lote: lo vendido más lo de bodega da el costo del lote."""
    for lote in p["lotes"]:
        tres = (D(lote["costo_vendido"]) + D(lote["costo_de_baja"])
                + D(lote["costo_en_bodega"]))
        assert tres == D(lote["costo_total"]), (
            f"{etiqueta} lote {lote['fecha']}: vendido {lote['costo_vendido']} + baja "
            f"{lote['costo_de_baja']} + bodega {lote['costo_en_bodega']} = {tres}, pero "
            f"el lote costó {lote['costo_total']}"
        )
        kilos = (D(lote["kilos_vendidos"]) + D(lote["kilos_de_baja"])
                 + D(lote["kilos_en_bodega"]))
        assert kilos == D(lote["kilos_producidos"]), (
            f"{etiqueta} lote {lote['fecha']}: {kilos} kg repartidos pero se produjeron "
            f"{lote['kilos_producidos']} kg"
        )
        # En los de producción el costo es la leche más su transporte; en los de
        # existencia es lo que se cargó a mano, y no tienen leche.
        if lote["origen"] == "produccion":
            assert D(lote["costo_total"]) == D(lote["costo_leche"]) + D(lote["costo_transporte"])
        # Y la utilidad es la resta que dice ser
        assert D(lote["utilidad"]) == (
            D(lote["ingresos"]) - D(lote["costo_vendido"]) - D(lote["costo_de_baja"])
        )
        # El detalle de la leche suma el costo del lote (si hubo leche que lo
        # respalde). En los de existencia no hay leche que revisar.
        if lote["origen"] == "produccion" and not D(lote["litros_sin_recepcion"]):
            assert sum(D(x["costo"]) for x in lote["detalle_leche"]) == D(lote["costo_total"])
            assert sum(D(x["litros"]) for x in lote["detalle_leche"]) == D(lote["litros_usados"])
        # Y el de ventas suma los ingresos
        assert sum(D(v["ingreso"]) for v in lote["detalle_ventas"]) == D(lote["ingresos"])
        assert sum(D(v["kilos"]) for v in lote["detalle_ventas"]) == D(lote["kilos_vendidos"])
        # OJO: la suma de las VENTAS no es la utilidad del lote cuando hubo baja.
        # Lo que se dio de baja no sale en ninguna venta (no se vendió) pero sí se
        # le resta al lote, así que la relación es con la baja restada. La pantalla
        # muestra la baja como un renglón propio que lleva de una cifra a la otra.
        suma_ventas = sum(D(v["utilidad"]) for v in lote["detalle_ventas"])
        assert suma_ventas - D(lote["costo_de_baja"]) == D(lote["utilidad"]), (
            f"{etiqueta} lote {lote['fecha']}: ventas {suma_ventas} - baja "
            f"{lote['costo_de_baja']} != utilidad {lote['utilidad']}"
        )


# ---------------------------------------------------------------------------
# 1. El caso del usuario: se produce el 1 y se vende 60 días después
# ---------------------------------------------------------------------------
def test_el_queso_del_dia_1_vendido_60_dias_despues(client, base_datos):
    """El caso exacto que reportó: la producción del 1 de julio se vende el 30 de
    agosto. La utilidad de ESE lote tiene que salir bien, aunque la venta esté en
    otro mes que la leche."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    # 1.000 litros a 1.800 = 1.800.000 de leche + 100.000 de flete
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    # Se producen 100 kg con esos litros
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)
    cliente = cliente_nuevo(client, h)
    producto = producto_de(client, h, tipo)
    # Y se venden 60 días después, a 25.000/kg
    vender(client, h, "2026-08-30", cliente, producto, kilos=100, precio=25000)

    p = panel(client, h)
    lote = p["lotes"][0]
    print("\n===== 1. PRODUCIDO EL 1, VENDIDO 60 DÍAS DESPUÉS =====")
    print(f"  lote {lote['fecha']} de {lote['tipo_queso']}")
    print(f"  usó {lote['litros_usados']} litros -> {lote['kilos_producidos']} kg"
          f" (rendimiento {lote['rendimiento']} kg/litro)")
    print(f"  costó {lote['costo_leche']} de leche + {lote['costo_transporte']} de flete"
          f" = {lote['costo_total']} ({lote['costo_kilo']}/kg)")
    print(f"  vendió {lote['kilos_vendidos']} kg por {lote['ingresos']}"
          f" -> UTILIDAD {lote['utilidad']}")
    for v in lote["detalle_ventas"]:
        print(f"    venta {v['fecha']} a {v['cliente']}: {v['kilos']} kg a"
              f" {v['precio_kilo']} = {v['ingreso']} (dejó {v['utilidad']})")

    assert D(lote["costo_leche"]) == 1_800_000
    assert D(lote["costo_transporte"]) == 100_000
    assert D(lote["costo_total"]) == 1_900_000
    assert D(lote["costo_kilo"]) == 19_000
    assert D(lote["kilos_vendidos"]) == 100
    assert D(lote["ingresos"]) == 2_500_000
    assert D(lote["utilidad"]) == 600_000
    assert D(lote["kilos_en_bodega"]) == 0 and lote["vendido_completo"] is True
    assert D(lote["rendimiento"]) == D("0.1")
    comprobar_cuadre(p, "caso 1")
    assert D(p["kilos_sin_lote"]) == 0
    assert D(p["litros_sin_recepcion"]) == 0


def test_lo_que_sigue_en_bodega_no_se_resta(client, base_datos):
    """Es el corazón del arreglo: el queso que no se ha vendido NO es pérdida. Si
    se le restara, un lote grande recién hecho saldría con una pérdida enorme."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)
    cliente = cliente_nuevo(client, h)
    producto = producto_de(client, h, tipo)
    # Solo se venden 40 de los 100 kg
    vender(client, h, "2026-07-15", cliente, producto, kilos=40, precio=25000)

    p = panel(client, h)
    lote = p["lotes"][0]
    print("\n===== 2. LO DE BODEGA NO SE RESTA =====")
    print(f"  producidos {lote['kilos_producidos']} kg, vendidos {lote['kilos_vendidos']} kg,"
          f" en bodega {lote['kilos_en_bodega']} kg")
    print(f"  ingresos {lote['ingresos']} - costo de lo vendido {lote['costo_vendido']}"
          f" = UTILIDAD {lote['utilidad']}")
    print(f"  en bodega quedan {lote['costo_en_bodega']} invertidos (NO se restan)")
    # 40 kg a 19.000 de costo = 760.000; se vendieron por 1.000.000
    assert D(lote["costo_vendido"]) == 760_000
    assert D(lote["ingresos"]) == 1_000_000
    assert D(lote["utilidad"]) == 240_000
    # Los 60 kg que quedan valen 1.140.000 y NO bajan la utilidad
    assert D(lote["kilos_en_bodega"]) == 60
    assert D(lote["costo_en_bodega"]) == 1_140_000
    assert lote["vendido_completo"] is False
    # Si se le restara, daría -900.000: eso es lo que hace hoy el estado de resultados
    assert D(lote["utilidad"]) != D(lote["ingresos"]) - D(lote["costo_total"])
    comprobar_cuadre(p, "bodega")


# ---------------------------------------------------------------------------
# 3. Cadena 1: la leche de varias recepciones, a precios distintos
# ---------------------------------------------------------------------------
def test_el_costo_del_lote_es_la_leche_real_no_un_promedio(client, base_datos):
    """El lote usa leche de dos proveedores a precios distintos. El costo tiene que
    ser la suma real de lo que se pagó por ESOS litros, y el detalle tiene que
    decir de quién vino cada uno."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    # 600 litros de Libardo a 1.800 y 400 de Carmen a 1.650
    recibir(client, h, "2026-07-01", prov["Libardo"], 600, transportador)
    recibir(client, h, "2026-07-01", prov["Carmen"], 400, transportador)
    producir(client, h, "2026-07-02", tipo, litros=1000, kilos=100)

    p = panel(client, h)
    lote = p["lotes"][0]
    print("\n===== 3. LA LECHE REAL DEL LOTE =====")
    for x in lote["detalle_leche"]:
        print(f"    {x['proveedor']:10} del {x['fecha_recepcion']}: {x['litros']} litros"
              f" -> {x['costo_leche']} de leche + {x['costo_transporte']} de flete")
    print(f"  costo del lote {lote['costo_total']} ({lote['costo_kilo']}/kg)")
    por_proveedor = {x["proveedor"]: x for x in lote["detalle_leche"]}
    assert D(por_proveedor["Libardo"]["litros"]) == 600
    assert D(por_proveedor["Libardo"]["costo_leche"]) == 600 * 1800
    assert D(por_proveedor["Carmen"]["litros"]) == 400
    assert D(por_proveedor["Carmen"]["costo_leche"]) == 400 * 1650
    # 1.080.000 + 660.000 = 1.740.000 de leche, más 100.000 de flete
    assert D(lote["costo_leche"]) == 1_740_000
    assert D(lote["costo_transporte"]) == 100_000
    assert D(lote["costo_total"]) == 1_840_000
    # Y NO es el promedio simple de los dos precios (1.725/litro daría 1.725.000)
    assert D(lote["costo_leche"]) != 1_725_000
    comprobar_cuadre(p, "leche real")


def test_la_leche_mas_vieja_se_usa_primero(client, base_datos):
    """La leche es lo más perecedero: la del lunes se usa antes que la del martes.
    Con dos producciones, la primera se lleva la leche vieja y la segunda la nueva,
    y por eso pueden tener costos por kilo distintos."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    # Lunes: leche barata de Carmen (1.650). Martes: leche cara de Libardo (1.800)
    recibir(client, h, "2026-07-01", prov["Carmen"], 500, transportador)
    recibir(client, h, "2026-07-02", prov["Libardo"], 500, transportador)
    producir(client, h, "2026-07-02", tipo, litros=500, kilos=50)
    producir(client, h, "2026-07-03", tipo, litros=500, kilos=50)

    p = panel(client, h)
    por_fecha = {l["fecha"]: l for l in p["lotes"]}
    print("\n===== 4. LA LECHE VIEJA PRIMERO =====")
    for fecha in sorted(por_fecha):
        l = por_fecha[fecha]
        quienes = ", ".join(f"{x['proveedor']} ({x['litros']} L)" for x in l["detalle_leche"])
        print(f"  lote {fecha}: {quienes} -> costo {l['costo_total']} ({l['costo_kilo']}/kg)")
    primero, segundo = por_fecha["2026-07-02"], por_fecha["2026-07-03"]
    # El primer lote se llevó la leche de Carmen (la del lunes), que es más barata
    assert [x["proveedor"] for x in primero["detalle_leche"]] == ["Carmen"]
    assert [x["proveedor"] for x in segundo["detalle_leche"]] == ["Libardo"]
    assert D(primero["costo_leche"]) == 500 * 1650
    assert D(segundo["costo_leche"]) == 500 * 1800
    # Y por eso el costo por kilo del segundo es mayor
    assert D(segundo["costo_kilo"]) > D(primero["costo_kilo"])
    comprobar_cuadre(p, "fifo leche")


# ---------------------------------------------------------------------------
# 5. Cadena 2: la cola de queso es POR TIPO
# ---------------------------------------------------------------------------
def test_no_se_despacha_un_tipo_de_queso_desde_el_lote_de_otro(client, base_datos):
    """Si la cola fuera una sola, vender campesino consumiría el lote de doble
    crema y los costos de los dos productos quedarían mezclados."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    campesino = tipo_queso(client, h, "Queso campesino")
    doble = tipo_queso(client, h, "Queso doble crema")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    recibir(client, h, "2026-07-02", prov["Carmen"], 1000, transportador)
    # El campesino se produce PRIMERO, así que en una cola única iría primero
    producir(client, h, "2026-07-01", campesino, litros=1000, kilos=100)
    producir(client, h, "2026-07-02", doble, litros=1000, kilos=120)
    cliente = cliente_nuevo(client, h)
    # Se vende DOBLE CREMA: no puede tocar el lote de campesino
    vender(client, h, "2026-07-10", cliente, producto_de(client, h, doble), kilos=120, precio=28000)

    p = panel(client, h)
    por_tipo = {l["tipo_queso"]: l for l in p["lotes"]}
    print("\n===== 5. LA COLA ES POR TIPO DE QUESO =====")
    for nombre, l in por_tipo.items():
        print(f"  {nombre}: producidos {l['kilos_producidos']} kg, vendidos"
              f" {l['kilos_vendidos']} kg, en bodega {l['kilos_en_bodega']} kg,"
              f" utilidad {l['utilidad']}")
    # El de doble crema se vendió completo
    assert D(por_tipo["Queso doble crema"]["kilos_vendidos"]) == 120
    # Y el campesino sigue intacto en bodega, aunque sea más viejo
    assert D(por_tipo["Queso campesino"]["kilos_vendidos"]) == 0
    assert D(por_tipo["Queso campesino"]["kilos_en_bodega"]) == 100
    assert D(por_tipo["Queso campesino"]["utilidad"]) == 0
    comprobar_cuadre(p, "por tipo")


def test_una_venta_se_reparte_entre_dos_lotes_del_mismo_tipo(client, base_datos):
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Carmen"], 500, transportador)
    recibir(client, h, "2026-07-08", prov["Libardo"], 500, transportador)
    producir(client, h, "2026-07-01", tipo, litros=500, kilos=50)
    producir(client, h, "2026-07-08", tipo, litros=500, kilos=50)
    cliente = cliente_nuevo(client, h)
    # 70 kg: 50 del lote viejo y 20 del nuevo
    vender(client, h, "2026-07-20", cliente, producto_de(client, h, tipo), kilos=70, precio=25000)

    p = panel(client, h)
    por_fecha = {l["fecha"]: l for l in p["lotes"]}
    print("\n===== 6. UNA VENTA ENTRE DOS LOTES =====")
    for fecha in sorted(por_fecha):
        l = por_fecha[fecha]
        v = l["detalle_ventas"][0] if l["detalle_ventas"] else None
        print(f"  lote {fecha}: vendidos {l['kilos_vendidos']} kg"
              + (f" ({v['kilos']} de los {v['kilos_venta']} kg del despacho,"
                 f" partida={v['partida']})" if v else "")
              + f" | utilidad {l['utilidad']}")
    viejo, nuevo = por_fecha["2026-07-01"], por_fecha["2026-07-08"]
    assert D(viejo["kilos_vendidos"]) == 50
    assert D(nuevo["kilos_vendidos"]) == 20
    assert viejo["detalle_ventas"][0]["partida"] is True
    assert nuevo["detalle_ventas"][0]["partida"] is True
    # Las dos partes suman el despacho completo
    assert D(viejo["ingresos"]) + D(nuevo["ingresos"]) == 70 * 25000
    comprobar_cuadre(p, "venta partida")


# ---------------------------------------------------------------------------
# 7. Lo que no se puede esconder
# ---------------------------------------------------------------------------
def test_producir_sin_leche_registrada_se_avisa(client, base_datos):
    """Pasa cuando se empieza a usar el sistema a mitad de camino: hay leche en el
    tanque que nunca se cargó. El lote queda con el costo de lo que sí se pudo
    respaldar y la diferencia se declara, en vez de inventarle un precio."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 400, transportador)
    # Se produce con 1.000 litros pero solo hay 400 registrados
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)

    p = panel(client, h)
    lote = p["lotes"][0]
    print("\n===== 7. PRODUCIR SIN LECHE REGISTRADA =====")
    print(f"  usó {lote['litros_usados']} litros, respaldados {400}, sin respaldo"
          f" {lote['litros_sin_recepcion']}")
    print(f"  el lote costó {lote['costo_total']} (solo la leche que sí está)")
    assert D(lote["litros_sin_recepcion"]) == 600
    assert D(p["litros_sin_recepcion"]) == 600
    # El costo es solo el de los 400 litros que existen
    assert D(lote["costo_leche"]) == 400 * 1800
    comprobar_cuadre(p, "sin leche")


def test_vender_queso_sin_produccion_se_avisa(client, base_datos):
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)
    cliente = cliente_nuevo(client, h)
    producto = producto_de(client, h, tipo)
    # Se despacha con fecha ANTERIOR a la producción: el stock histórico lo permite
    vender(client, h, "2026-06-20", cliente, producto, kilos=30, precio=25000)

    p = panel(client, h)
    lote = p["lotes"][0]
    print("\n===== 8. VENDER ANTES DE PRODUCIR =====")
    print(f"  sin lote: {p['kilos_sin_lote']} kg por {p['ingreso_sin_lote']}")
    print(f"  el lote del 01/07 sigue con sus {lote['kilos_en_bodega']} kg")
    assert D(p["kilos_sin_lote"]) == 30
    assert D(p["ingreso_sin_lote"]) == 750_000
    # El lote posterior NO absorbe ese despacho
    assert D(lote["kilos_vendidos"]) == 0
    assert D(lote["kilos_en_bodega"]) == 100
    comprobar_cuadre(p, "sin lote")


def test_la_leche_sin_usar_se_reporta(client, base_datos):
    """La leche que entró y todavía no se ha convertido en queso no es costo de
    ningún lote: es inventario de materia prima, y hay que poder verlo."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-01", tipo, litros=600, kilos=60)

    p = panel(client, h)
    print("\n===== 9. LECHE SIN USAR =====")
    print(f"  quedan {p['litros_sin_usar']} litros sin usar, por"
          f" {p['costo_litros_sin_usar']}")
    assert D(p["litros_sin_usar"]) == 400
    # 400 litros a 1.800 + su parte del flete (100.000 / 1000 x 400 = 40.000)
    assert D(p["costo_litros_sin_usar"]) == 400 * 1800 + 40_000
    comprobar_cuadre(p, "leche sin usar")


# ---------------------------------------------------------------------------
# 10. Bordes
# ---------------------------------------------------------------------------
def test_la_ruta_lotes_no_la_tapa_el_comodin_de_id(client, base_datos):
    """El CRUD de producción tiene un /{entity_id} que es comodín de un segmento.
    Si se registrara antes que /lotes, FastAPI intentaría leer "lotes" como un
    UUID y devolvería 422. La ruta va primero a propósito."""
    h = auth_headers(client, "admin.a")
    r = client.get("/api/v1/produccion/lotes", headers=h)
    print("\n===== 10. LA RUTA NO SE TAPA =====")
    print(f"  GET /produccion/lotes -> {r.status_code}")
    assert r.status_code == 200, r.text
    assert "lotes" in r.json()


def test_sin_movimientos_no_revienta(client, base_datos):
    h = auth_headers(client, "admin.a")
    p = panel(client, h)
    print("\n===== 11. SIN MOVIMIENTOS =====")
    print(f"  lotes={len(p['lotes'])} utilidad={p['total_utilidad']} mejor={p['mejor']}")
    assert p["lotes"] == []
    assert D(p["total_utilidad"]) == 0
    assert p["mejor"] is None and p["peor"] is None


def test_los_totales_suman_los_lotes(client, base_datos):
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    recibir(client, h, "2026-07-08", prov["Carmen"], 800, transportador)
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)
    producir(client, h, "2026-07-08", tipo, litros=800, kilos=85)
    cliente = cliente_nuevo(client, h)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-07-20", cliente, producto, kilos=150, precio=25000)

    p = panel(client, h)
    print("\n===== 12. LOS TOTALES CUADRAN =====")
    for l in p["lotes"]:
        print(f"  {l['fecha']}: {l['litros_usados']} L -> {l['kilos_producidos']} kg,"
              f" costo {l['costo_total']}, vendidos {l['kilos_vendidos']} kg,"
              f" utilidad {l['utilidad']}")
    print(f"  TOTAL utilidad {p['total_utilidad']} | costo {p['total_costo']}"
          f" | en bodega {p['total_kilos_en_bodega']} kg por {p['total_costo_en_bodega']}")
    assert D(p["total_utilidad"]) == sum(D(l["utilidad"]) for l in p["lotes"])
    assert D(p["total_costo"]) == sum(D(l["costo_total"]) for l in p["lotes"])
    assert D(p["total_kilos"]) == sum(D(l["kilos_producidos"]) for l in p["lotes"])
    assert D(p["total_litros"]) == sum(D(l["litros_usados"]) for l in p["lotes"])
    assert D(p["total_ingresos"]) == sum(D(l["ingresos"]) for l in p["lotes"])
    assert D(p["total_costo_en_bodega"]) == sum(D(l["costo_en_bodega"]) for l in p["lotes"])
    comprobar_cuadre(p, "totales")


def test_el_filtro_recorta_la_vista_pero_no_el_calculo(client, base_datos):
    """Si el filtro recortara el cálculo, la producción de julio no encontraría la
    leche de junio y quedaría sin costo."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    # La leche entra el 30 de JUNIO y el queso se hace el 1 de JULIO
    recibir(client, h, "2026-06-30", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)

    completo = panel(client, h)
    solo_julio = panel(client, h, desde="2026-07-01", hasta="2026-07-31")
    print("\n===== 13. EL FILTRO NO CAMBIA EL CÁLCULO =====")
    print(f"  completo: {[l['fecha'] for l in completo['lotes']]}")
    print(f"  solo julio: {[l['fecha'] for l in solo_julio['lotes']]}")
    print(f"  costo del lote de julio: completo={completo['lotes'][0]['costo_total']}"
          f" filtrado={solo_julio['lotes'][0]['costo_total']}")
    # El lote de julio tiene su costo completo aunque la leche sea de junio
    assert D(solo_julio["lotes"][0]["costo_total"]) == 1_900_000
    assert solo_julio["lotes"][0] == completo["lotes"][0]
    assert D(solo_julio["litros_sin_recepcion"]) == 0
    comprobar_cuadre(solo_julio, "filtro")


def test_no_cruza_empresas(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    transportador, prov = montar_leche(client, ha)
    tipo = tipo_queso(client, ha, "Queso campesino")
    recibir(client, ha, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, ha, "2026-07-01", tipo, litros=1000, kilos=100)

    pb = panel(client, hb)
    print("\n===== 14. AISLAMIENTO =====")
    print(f"  A: {len(panel(client, ha)['lotes'])} lotes | B: {len(pb['lotes'])} lotes")
    assert pb["lotes"] == []
    assert D(pb["total_utilidad"]) == 0
    assert D(pb["litros_sin_usar"]) == 0


def test_produccion_borrada_no_es_lote(client, base_datos):
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-01", tipo, litros=500, kilos=50)
    borrada = producir(client, h, "2026-07-05", tipo, litros=500, kilos=50)
    r = client.delete(f"/api/v1/produccion/{borrada['id']}", headers=h)
    assert r.status_code == 204, r.text

    p = panel(client, h)
    print("\n===== 15. PRODUCCIÓN BORRADA =====")
    print(f"  lotes: {[l['fecha'] for l in p['lotes']]}"
          f" | litros sin usar {p['litros_sin_usar']}")
    assert [l["fecha"] for l in p["lotes"]] == ["2026-07-01"]
    # Y sus litros vuelven a estar sin usar
    assert D(p["litros_sin_usar"]) == 500
    comprobar_cuadre(p, "borrada")


def test_los_lotes_de_produccion_exigen_permiso(client, base_datos, db_session):
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.modules.usuarios.models import Rol, Usuario
    from tests.conftest import PASSWORD

    rol = db_session.scalars(select(Rol).where(Rol.nombre == "Consulta")).one()
    mirona = Usuario(
        nombre="Solo", apellido="Mira", correo="mira.prod@test.local",
        username="mira.prod", hashed_password=hash_password(PASSWORD),
        empresa_id=base_datos["empresa_a"].id,
    )
    mirona.roles = [rol]
    db_session.add(mirona)
    db_session.commit()

    h = auth_headers(client, "mira.prod")
    r = client.get("/api/v1/produccion/lotes", headers=h)
    print("\n===== 16. PERMISOS =====")
    print(f"  con 'consultar': {r.status_code}")
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/produccion/lotes").status_code == 401


# ---------------------------------------------------------------------------
# 17. Queso que YA estaba en bodega, cargado a mano (sin lote de producción)
# ---------------------------------------------------------------------------
def crear_producto_queso(client, h, tipo, nombre=None):
    """El producto terminado de un tipo de queso, creado a mano.

    La producción lo crea sola la primera vez, pero para cargar existencia ANTES
    de la primera producción hay que crearlo aparte.
    """
    r = client.post(
        "/api/v1/inventario/productos",
        json={"nombre": nombre or f"{tipo['nombre']} (bodega)",
              "categoria": "producto_terminado", "unidad": "kg",
              "tipo_queso_id": tipo["id"]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def movimiento(client, h, producto, fecha, tipo_mov, cantidad, costo="0", referencia=None):
    datos = {"producto_id": producto["id"], "fecha": fecha, "tipo": tipo_mov,
             "cantidad": str(cantidad), "costo_unitario": str(costo)}
    if referencia:
        datos["referencia"] = referencia
    r = client.post("/api/v1/inventario/movimientos", json=datos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_la_entrada_de_una_produccion_no_se_cuenta_dos_veces(client, base_datos):
    """LA PRUEBA MÁS IMPORTANTE DE ESTE LOTE DE CAMBIOS.

    La producción crea sola su entrada de inventario. Si esa entrada se contara
    además como "existencia cargada a mano", los kilos del lote se DUPLICARÍAN y
    la bodega mostraría el doble de queso del que hay. Se distinguen por la
    referencia ("Producción #xxxxxxxx"), así que esta prueba es la que protege ese
    acuerdo: si alguien cambia el texto de la referencia, esto se cae.
    """
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)

    p = panel(client, h)
    print("\n===== 17. LA PRODUCCIÓN NO SE CUENTA DOS VECES =====")
    print(f"  lotes={len(p['lotes'])} kilos totales={p['total_kilos']}")
    for l in p["lotes"]:
        print(f"    {l['fecha']} origen={l['origen']} {l['kilos_producidos']} kg"
              f" costo {l['costo_total']}")
    # UN solo lote, de origen 'produccion', con sus 100 kg (no 200)
    assert len(p["lotes"]) == 1
    assert p["lotes"][0]["origen"] == "produccion"
    assert D(p["total_kilos"]) == 100
    assert D(p["total_costo"]) == 1_900_000
    comprobar_cuadre(p, "sin duplicar")


def test_la_existencia_cargada_a_mano_es_un_lote_con_su_costo(client, base_datos):
    """El caso del usuario: ya tenía queso registrado que no viene de ninguna
    producción. Esas entradas traen su costo unitario, así que se costean de
    verdad en vez de quedar sin lote."""
    h = auth_headers(client, "admin.a")
    tipo = tipo_queso(client, h, "Queso campesino")
    producto = crear_producto_queso(client, h, tipo)
    # 400 kg que ya estaban en bodega, a 18.000 el kilo
    movimiento(client, h, producto, "2026-06-15", "entrada", 400, costo="18000",
               referencia="Existencia inicial")
    cliente = cliente_nuevo(client, h)
    vender(client, h, "2026-07-10", cliente, producto, kilos=250, precio=25000)

    p = panel(client, h)
    lote = p["lotes"][0]
    print("\n===== 18. EXISTENCIA CARGADA A MANO =====")
    print(f"  lote {lote['fecha']} origen={lote['origen']}"
          f" referencia={lote['referencia']}")
    print(f"  {lote['kilos_producidos']} kg que costaron {lote['costo_total']}"
          f" ({lote['costo_kilo']}/kg)")
    print(f"  vendidos {lote['kilos_vendidos']} kg por {lote['ingresos']}"
          f" -> UTILIDAD {lote['utilidad']}")
    print(f"  en bodega {lote['kilos_en_bodega']} kg por {lote['costo_en_bodega']}")
    assert lote["origen"] == "existencia"
    assert lote["referencia"] == "Existencia inicial"
    assert D(lote["kilos_producidos"]) == 400
    assert D(lote["costo_total"]) == 7_200_000
    assert D(lote["costo_kilo"]) == 18_000
    # No tiene leche detrás, y eso está bien
    assert lote["detalle_leche"] == []
    assert D(lote["litros_usados"]) == 0
    # 250 kg vendidos a 25.000 = 6.250.000; costaron 250 x 18.000 = 4.500.000
    assert D(lote["ingresos"]) == 6_250_000
    assert D(lote["costo_vendido"]) == 4_500_000
    assert D(lote["utilidad"]) == 1_750_000
    assert D(lote["kilos_en_bodega"]) == 150
    assert D(lote["costo_en_bodega"]) == 2_700_000
    # Y NO queda nada sin lote: ese era el problema que se venía a resolver
    assert D(p["kilos_sin_lote"]) == 0
    comprobar_cuadre(p, "existencia")


def test_la_existencia_vieja_se_despacha_antes_que_la_produccion_nueva(client, base_datos):
    """La existencia entra a la cola por su fecha, como todo lo demás: si es más
    vieja, sale primero. Si no, se estaría vendiendo queso nuevo con queso viejo
    todavía en bodega."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    producto = crear_producto_queso(client, h, tipo)
    # Existencia vieja, barata
    movimiento(client, h, producto, "2026-06-01", "entrada", 100, costo="16000",
               referencia="Existencia inicial")
    # Producción nueva, más cara
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)
    cliente = cliente_nuevo(client, h)
    # Se venden 100 kg: tienen que salir de la existencia vieja
    vender(client, h, "2026-07-10", cliente, producto, kilos=100, precio=25000)

    p = panel(client, h)
    por_origen = {l["origen"]: l for l in p["lotes"]}
    print("\n===== 19. LA EXISTENCIA VIEJA SALE PRIMERO =====")
    for l in p["lotes"]:
        print(f"  {l['fecha']} ({l['origen']}): vendidos {l['kilos_vendidos']} kg,"
              f" en bodega {l['kilos_en_bodega']} kg, utilidad {l['utilidad']}")
    assert D(por_origen["existencia"]["kilos_vendidos"]) == 100
    assert D(por_origen["produccion"]["kilos_vendidos"]) == 0
    assert D(por_origen["produccion"]["kilos_en_bodega"]) == 100
    # La utilidad es la del queso VIEJO: 2.500.000 - 1.600.000
    assert D(por_origen["existencia"]["utilidad"]) == 900_000
    comprobar_cuadre(p, "existencia vieja")


def test_la_existencia_sin_costo_se_avisa(client, base_datos):
    """Si se cargó queso sin ponerle costo, sus kilos salen como si hubieran
    costado cero y la utilidad se ve mejor de lo que es. Hay que decirlo."""
    h = auth_headers(client, "admin.a")
    tipo = tipo_queso(client, h, "Queso campesino")
    producto = crear_producto_queso(client, h, tipo)
    movimiento(client, h, producto, "2026-06-15", "entrada", 300, costo="0",
               referencia="Carga inicial sin costo")
    cliente = cliente_nuevo(client, h)
    vender(client, h, "2026-07-10", cliente, producto, kilos=300, precio=25000)

    p = panel(client, h)
    lote = p["lotes"][0]
    print("\n===== 20. EXISTENCIA SIN COSTO =====")
    print(f"  {lote['kilos_producidos']} kg cargados sin costo"
          f" (sin_costo={lote['sin_costo']})")
    print(f"  la utilidad sale de {lote['utilidad']}, que es TODO el ingreso")
    print(f"  el panel lo avisa: {p['kilos_existencia_sin_costo']} kg sin costo")
    assert lote["sin_costo"] is True
    assert D(lote["costo_total"]) == 0
    # La utilidad es todo el ingreso, que no es la realidad: por eso el aviso
    assert D(lote["utilidad"]) == 7_500_000
    assert D(lote["utilidad"]) == D(lote["ingresos"])
    assert D(p["kilos_existencia_sin_costo"]) == 300
    comprobar_cuadre(p, "sin costo")


def test_un_ajuste_hacia_abajo_es_baja_y_si_se_resta(client, base_datos):
    """El stock del inventario baja con un ajuste negativo. Si el reparto no lo
    bajara también, "en bodega" mostraría queso que ya no existe y las dos
    pantallas dirían cosas distintas de los mismos kilos.

    Y sí se le resta a la utilidad: es plata que salió del lote sin ingreso.
    """
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)
    producto = producto_de(client, h, tipo)
    cliente = cliente_nuevo(client, h)
    vender(client, h, "2026-07-10", cliente, producto, kilos=60, precio=25000)
    # 20 kg se dañaron
    movimiento(client, h, producto, "2026-07-12", "ajuste", -20,
               referencia="Se dañó en bodega")

    p = panel(client, h)
    lote = p["lotes"][0]
    print("\n===== 21. AJUSTE HACIA ABAJO =====")
    print(f"  producidos {lote['kilos_producidos']} kg: vendidos"
          f" {lote['kilos_vendidos']}, de baja {lote['kilos_de_baja']},"
          f" en bodega {lote['kilos_en_bodega']}")
    print(f"  ingresos {lote['ingresos']} - costo vendido {lote['costo_vendido']}"
          f" - baja {lote['costo_de_baja']} = UTILIDAD {lote['utilidad']}")
    assert D(lote["kilos_de_baja"]) == 20
    assert D(lote["costo_de_baja"]) == 380_000  # 20 kg x 19.000
    assert D(lote["kilos_en_bodega"]) == 20
    # 1.500.000 - 1.140.000 - 380.000
    assert D(lote["utilidad"]) == -20_000
    # Y el stock del inventario dice lo mismo que el lote
    stock = client.get(
        f"/api/v1/inventario/productos/{producto['id']}/kardex", headers=h
    ).json()["stock_actual"]
    print(f"  stock del inventario: {stock} kg | en bodega del lote:"
          f" {lote['kilos_en_bodega']} kg")
    assert D(stock) == D(lote["kilos_en_bodega"])
    comprobar_cuadre(p, "baja")
