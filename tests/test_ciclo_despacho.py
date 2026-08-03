"""CERRAR EL CICLO DE DESPACHO: los kilos fantasma que inflaban la bodega.

EL PROBLEMA QUE CONTÓ EL DUEÑO. El queso se pesa dos veces: cuando se hace y
cuando se vende en Bogotá. Entre las dos se seca y pierde peso: una tanda que
pesó 130 kg al hacerse rinde 125 al venderse. Y en Bogotá se vende POR KILOS,
sin saber de qué tanda salió.

Esos 5 kg no desaparecían: se quedaban en la cola FIFO como QUESO EN BODEGA QUE
NO EXISTE, con su costo. El lote 1 prometía 130 y solo salían 125 reales, así
que la falta corría al lote 2, del 2 al 3, y la deuda se acumulaba en el último
lote. Inventario inflado, utilidad más alta de la real.

LO QUE LO ARREGLA es que el despacho va por CICLOS de unos siete días. Al
terminar uno, de esas tandas no debería quedar nada, y ahí la resta sí es
honesta:

    producido en el ciclo − vendido − lo que ya se bajó a mano = MERMA

Las siete pruebas de aquí son las siete formas en que esto podía salir mal:

  1. El caso del dueño: 130 producidos, 125 vendidos, 5 kg de merma con su
     costo, y la bodega baja de verdad.
  2. Lo repartido suma EXACTO la merma, incluso con kilos feos y con residuo de
     redondeo. El dueño suma esta columna a mano.
  3. Cerrar dos veces no duplica la merma.
  4. Reabrir la deshace por completo: el queso vuelve a la bodega.
  5. Si el dueño YA había anotado un ajuste dentro del ciclo, esos kilos no se
     cobran otra vez.
  6. Una merma absurda avisa en vez de registrarse callada.
  7. Los ciclos de una empresa no tocan los de la otra.

Los números se imprimen porque el dueño los cuadra a mano contra su cuaderno.
"""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/produccion"


def D(valor):
    return Decimal(str(valor))


def detalle(r) -> str:
    """El detalle de un BusinessError: llega como 422 y anidado dentro de
    "error", no en la raíz de la respuesta."""
    cuerpo = r.json()
    return cuerpo.get("error", cuerpo).get("detail", "")


# --------------------------------------------------------------------- montaje
def montar_leche(client, h, precio_litro="1800", transporte="100"):
    """Ruta, transportador y un proveedor. La leche es lo que le pone COSTO al
    queso: sin ella los lotes valdrían cero y la merma no valdría nada."""
    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"},
        headers=h,
    ).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Stella", "ruta_id": ruta["id"], "valor_transporte": transporte},
        headers=h,
    ).json()
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Libardo", "vereda": "Granada", "precio_litro": precio_litro,
              "ruta_id": ruta["id"]},
        headers=h,
    ).json()
    return transportador, proveedor


def recibir(client, h, fecha, proveedor, litros, transportador=None):
    datos = {"fecha": fecha, "proveedor_id": proveedor["id"],
             "cantidad_litros": str(litros)}
    if transportador:
        datos["transportador_id"] = transportador["id"]
    r = client.post("/api/v1/recepciones", json=datos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def tipo_queso(client, h, nombre):
    r = client.post("/api/v1/tipos-queso", json={"nombre": nombre}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def producir(client, h, fecha, tipo, litros, kilos):
    r = client.post(
        f"{API}",
        json={"fecha": fecha, "tipo_queso_id": tipo["id"], "litros_usados": str(litros),
              "peso_kg": str(kilos)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def producto_de(client, h, tipo):
    """El producto terminado que la producción crea sola para ese tipo de queso."""
    productos = client.get("/api/v1/inventario/productos", headers=h).json()["items"]
    coincide = [p for p in productos if p.get("tipo_queso_id") == tipo["id"]]
    assert coincide, f"la producción no creó el producto terminado: {productos}"
    return coincide[0]


def cliente_nuevo(client, h, nombre="Depósito de Bogotá"):
    r = client.post("/api/v1/clientes", json={"nombre": nombre}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


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


def movimiento(client, h, producto, fecha, tipo_mov, cantidad, costo="0", referencia=None):
    datos = {"producto_id": producto["id"], "fecha": fecha, "tipo": tipo_mov,
             "cantidad": str(cantidad), "costo_unitario": str(costo)}
    if referencia:
        datos["referencia"] = referencia
    r = client.post("/api/v1/inventario/movimientos", json=datos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------- consultas
def propuesta(client, h, **params):
    r = client.get(f"{API}/ciclos/propuesta", params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def cerrar(client, h, desde, hasta, **extra):
    datos = {"fecha_inicio": desde, "fecha_fin": hasta, **extra}
    return client.post(f"{API}/ciclos/cerrar", json=datos, headers=h)


def panel_ciclos(client, h):
    r = client.get(f"{API}/ciclos", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def panel_lotes(client, h, **params):
    r = client.get(f"{API}/lotes", params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def stock(client, h, producto):
    r = client.get(f"/api/v1/inventario/productos/{producto['id']}/kardex", headers=h)
    assert r.status_code == 200, r.text
    return D(r.json()["stock_actual"])


def comprobar_cuadre(p, etiqueta=""):
    """El cuadre que el dueño revisa: para CADA lote, lo vendido más lo dado de
    baja más lo que sigue en bodega tiene que dar el costo del lote, y los kilos
    igual. La merma del ciclo entra DENTRO de la baja, no como un cuarto destino.
    """
    for lote in p["lotes"]:
        tres = (D(lote["costo_vendido"]) + D(lote["costo_de_baja"])
                + D(lote["costo_en_bodega"]))
        assert tres == D(lote["costo_total"]), (
            f"{etiqueta} lote {lote['fecha']}: vendido {lote['costo_vendido']} + baja "
            f"{lote['costo_de_baja']} + bodega {lote['costo_en_bodega']} = {tres}, "
            f"pero el lote costó {lote['costo_total']}"
        )
        kilos = (D(lote["kilos_vendidos"]) + D(lote["kilos_de_baja"])
                 + D(lote["kilos_en_bodega"]))
        assert kilos == D(lote["kilos_producidos"]), (
            f"{etiqueta} lote {lote['fecha']}: {kilos} kg repartidos pero se "
            f"produjeron {lote['kilos_producidos']} kg"
        )
        # La merma del ciclo es un SUBCONJUNTO de la baja, nunca más que ella
        assert D(lote["kilos_merma_ciclo"]) <= D(lote["kilos_de_baja"])
        assert D(lote["costo_merma_ciclo"]) <= D(lote["costo_de_baja"])
        assert D(lote["utilidad"]) == (
            D(lote["ingresos"]) - D(lote["costo_vendido"]) - D(lote["costo_de_baja"])
            - D(lote["gastos"])
        )


# ---------------------------------------------------------------------------
# 1. EL CASO DEL DUEÑO: 130 kg producidos, 125 vendidos, 5 de merma
# ---------------------------------------------------------------------------
def test_el_caso_del_dueno_130_producidos_125_vendidos(client, base_datos):
    """El caso literal que contó el dueño, con tres tandas dentro del ciclo.

    Se hacen 130 kg entre el 16 y el 18, se despachan 125 el 22, y quedan 5 kg
    que no existen: se secaron. ANTES del cierre esos 5 kg salían como queso en
    bodega con su costo, y la utilidad se veía mejor de lo que era. DESPUÉS del
    cierre la bodega queda en cero, los 5 kg están dados de baja con su costo, y
    la utilidad es la de verdad.
    """
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    # 1.300 litros a 1.800 + 100 de flete = 2.470.000 de costo para 130 kg,
    # o sea 19.000 el kilo hecho.
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=500, kilos=50)
    producir(client, h, "2026-07-17", tipo, litros=500, kilos=50)
    producir(client, h, "2026-07-18", tipo, litros=300, kilos=30)
    producto = producto_de(client, h, tipo)
    cliente = cliente_nuevo(client, h)
    vender(client, h, "2026-07-22", cliente, producto, kilos=125, precio=25000)

    print("\n===== 1. EL CASO DEL DUEÑO =====")
    antes = panel_lotes(client, h)
    print(f"  ANTES de cerrar: en bodega {antes['total_kilos_en_bodega']} kg"
          f" que valen {antes['total_costo_en_bodega']}"
          f" | utilidad {antes['total_utilidad']}")
    assert D(antes["total_kilos_en_bodega"]) == 5, "el queso fantasma no está ahí"
    assert stock(client, h, producto) == 5

    # La cuenta ANTES de aceptarla: es la pantalla que lee el dueño
    p = propuesta(client, h, desde="2026-07-16", hasta="2026-07-22")
    print(f"  LA CUENTA: se produjeron {p['kilos_producidos']} kg,"
          f" salieron {p['kilos_vendidos']},"
          f" la diferencia son {p['kilos_merma']} kg"
          f" que valen {p['costo_merma']} ({p['porcentaje']}%)")
    for fila in p["por_lote"]:
        print(f"    tanda {fila['fecha']}: produjo {fila['kilos_producidos']} kg"
              f" -> le tocan {fila['kilos_merma']} kg ({fila['costo_merma']})")
    assert D(p["kilos_producidos"]) == 130
    assert D(p["kilos_vendidos"]) == 125
    assert D(p["kilos_merma"]) == 5
    assert D(p["costo_merma"]) == 95_000  # 5 kg x 19.000
    assert p["advertencias"] == [], p["advertencias"]

    r = cerrar(client, h, "2026-07-16", "2026-07-22")
    assert r.status_code == 201, r.text
    ciclo = r.json()
    print(f"  cerrado: {ciclo['nombre']} · merma {ciclo['kilos_merma']} kg"
          f" por {ciclo['costo_merma']}")

    despues = panel_lotes(client, h)
    print(f"  DESPUÉS: en bodega {despues['total_kilos_en_bodega']} kg"
          f" | de baja {despues['total_kilos_de_baja']} kg"
          f" (de esos, merma de ciclo {despues['total_kilos_merma_ciclo']} kg"
          f" por {despues['total_costo_merma_ciclo']})")
    print(f"  utilidad: {antes['total_utilidad']} -> {despues['total_utilidad']}")

    # LA BODEGA BAJA: ya no hay queso fantasma, ni en el reparto ni en el stock
    assert D(despues["total_kilos_en_bodega"]) == 0
    assert D(despues["total_costo_en_bodega"]) == 0
    assert stock(client, h, producto) == 0
    # Y los 5 kg están dados de baja CON SU COSTO
    assert D(despues["total_kilos_de_baja"]) == 5
    assert D(despues["total_kilos_merma_ciclo"]) == 5
    assert D(despues["total_costo_merma_ciclo"]) == 95_000
    # La utilidad real es 95.000 más baja que la que se mostraba antes
    assert D(despues["total_utilidad"]) == D(antes["total_utilidad"]) - 95_000
    comprobar_cuadre(despues, "caso del dueño")


def test_la_merma_no_cae_toda_sobre_la_ultima_tanda(client, base_datos):
    """POR QUÉ EL REPARTO ES A PRORRATA Y NO FIFO.

    Al cerrar, las tandas viejas ya salieron completas y la única que todavía
    tiene kilos en la cola es la última. Un reparto FIFO le cargaría LOS 5 KG a
    ella sola: esa tanda se vería pésima y las otras dos perfectas, cuando en
    realidad las tres se secaron igual. Y el dueño usa justamente esta pantalla
    para decidir qué días producen bien.
    """
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=500, kilos=50)
    producir(client, h, "2026-07-17", tipo, litros=500, kilos=50)
    producir(client, h, "2026-07-18", tipo, litros=300, kilos=30)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 125, 25000)
    assert cerrar(client, h, "2026-07-16", "2026-07-22").status_code == 201

    p = panel_lotes(client, h)
    por_fecha = {l["fecha"]: l for l in p["lotes"]}
    print("\n===== 1b. LA MERMA SE REPARTE, NO SE AMONTONA =====")
    for fecha in sorted(por_fecha):
        l = por_fecha[fecha]
        print(f"  {fecha}: produjo {l['kilos_producidos']} kg ->"
              f" merma {l['kilos_merma_ciclo']} kg ({l['costo_merma_ciclo']})")
    # 5 kg sobre 130: a las de 50 kg les toca 1,92 y a la de 30 kg el resto
    assert D(por_fecha["2026-07-16"]["kilos_merma_ciclo"]) == D("1.92")
    assert D(por_fecha["2026-07-17"]["kilos_merma_ciclo"]) == D("1.92")
    assert D(por_fecha["2026-07-18"]["kilos_merma_ciclo"]) == D("1.16")
    # Ninguna se llevó todo el golpe
    assert all(
        D(l["kilos_merma_ciclo"]) < 5 for l in p["lotes"]
    ), "una sola tanda se llevó toda la merma del ciclo: eso es FIFO, no prorrata"
    comprobar_cuadre(p, "prorrata")


# ---------------------------------------------------------------------------
# 2. LO REPARTIDO SUMA EXACTO LA MERMA DEL CICLO
# ---------------------------------------------------------------------------
def test_el_reparto_suma_exacto_con_kilos_feos(client, base_datos):
    """LA PRUEBA QUE MANDA: la columna de la pantalla tiene que dar la cifra
    grande sin sobrar ni faltar un gramo, porque el dueño la suma a mano.

    Se usan kilos feos a propósito (43,37 · 41,63 · 45,03) y una merma que no
    reparte redondo, para que el residuo del redondeo tenga que caer en alguna
    parte. Si el último lote no se lo llevara, la suma daría 4,99 o 5,01.
    """
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso doble crema")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=433, kilos="43.37")
    producir(client, h, "2026-07-17", tipo, litros=416, kilos="41.63")
    producir(client, h, "2026-07-18", tipo, litros=451, kilos="45.03")
    producto = producto_de(client, h, tipo)
    # 130,03 producidos, se despachan 124,71: quedan 5,32 kg de merma
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, "124.71", 25000)

    p = propuesta(client, h, desde="2026-07-16", hasta="2026-07-22")
    print("\n===== 2. EL REPARTO CUADRA AL GRAMO =====")
    print(f"  producido {p['kilos_producidos']} − vendido {p['kilos_vendidos']}"
          f" = MERMA {p['kilos_merma']} kg por {p['costo_merma']}")
    suma_kilos = sum(D(l["kilos_merma"]) for l in p["por_lote"])
    suma_costo = sum(D(l["costo_merma"]) for l in p["por_lote"])
    for l in p["por_lote"]:
        print(f"    {l['fecha']}: {l['kilos_producidos']} kg -> "
              f"{l['kilos_merma']} kg · {l['costo_merma']}")
    print(f"    SUMA:                     {suma_kilos} kg · {suma_costo}")
    assert D(p["kilos_merma"]) == D("5.32")
    assert suma_kilos == D(p["kilos_merma"]), "la columna de kilos no da la merma"
    assert suma_costo == D(p["costo_merma"]), "la columna de plata no da el costo"

    assert cerrar(client, h, "2026-07-16", "2026-07-22").status_code == 201
    lotes = panel_lotes(client, h)
    repartido = sum(D(l["kilos_merma_ciclo"]) for l in lotes["lotes"])
    print(f"  ya cerrado, en la pantalla de lotes: {repartido} kg repartidos")
    assert repartido == D("5.32")
    assert D(lotes["total_kilos_en_bodega"]) == 0
    comprobar_cuadre(lotes, "kilos feos")


def test_el_reparto_puro_cuadra_con_tres_decimales(client, base_datos):
    """El reparto es una función pura, así que se puede exigir el cuadre a
    cualquier precisión, sin pasar por las columnas de la base (que guardan dos
    decimales). Con kilos de tres decimales y mermas que no dividen redondo, la
    suma de los pedazos tiene que seguir siendo la merma EXACTA.
    """
    from app.modules.produccion.lotes import repartir_merma_ciclo

    print("\n===== 2b. EL REPARTO PURO, CON TRES DECIMALES =====")
    casos = [
        ([D("43.337"), D("41.631"), D("45.032")], D("5.317"), D("0.001")),
        ([D("100"), D("100"), D("100")], D("1"), D("0.001")),  # 0,333 x3 = 0,999
        ([D("7.777"), D("3.333"), D("1.111")], D("0.999"), D("0.001")),
        ([D("130")], D("5"), D("0.01")),
        # Un lote sin kilos en medio: no recibe nada y no se queda con el residuo
        ([D("50"), D("0"), D("30")], D("5"), D("0.01")),
    ]
    for kilos, merma, paso in casos:
        partes = repartir_merma_ciclo(kilos, merma, paso)
        print(f"  {[str(k) for k in kilos]} · merma {merma} -> "
              f"{[str(x) for x in partes]} suma {sum(partes)}")
        assert sum(partes) == merma, (
            f"repartir {merma} entre {kilos} dio {sum(partes)}"
        )
        # Nadie recibe más de lo que produjo, y quien no produjo no recibe nada
        for k, parte in zip(kilos, partes):
            assert parte <= k
            if k <= 0:
                assert parte == 0

    # Y no se le inventa merma a nadie cuando no hay qué repartir
    assert repartir_merma_ciclo([D("50"), D("30")], D("0")) == [D("0"), D("0")]
    assert repartir_merma_ciclo([D("0")], D("5")) == [D("0")]
    print("  merma en cero y lotes en cero: no se reparte nada")


# ---------------------------------------------------------------------------
# 3. CERRAR DOS VECES NO DUPLICA LA MERMA
# ---------------------------------------------------------------------------
def test_cerrar_dos_veces_no_duplica_la_merma(client, base_datos):
    """Si el mismo ciclo se pudiera cerrar dos veces, la merma se cobraría dos
    veces sobre el mismo queso: la bodega quedaría en negativo y la utilidad
    más baja de la real. Lo tapa la regla de que los ciclos no se solapan.
    """
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=1300, kilos=130)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 125, 25000)

    print("\n===== 3. CERRAR DOS VECES =====")
    primero = cerrar(client, h, "2026-07-16", "2026-07-22")
    assert primero.status_code == 201, primero.text
    print(f"  primer cierre: merma {primero.json()['kilos_merma']} kg")

    # El mismo rango, otra vez
    segundo = cerrar(client, h, "2026-07-16", "2026-07-22")
    print(f"  segundo cierre (mismo rango): {segundo.status_code} · "
          f"{detalle(segundo)}")
    assert segundo.status_code == 422
    assert "solapar" in detalle(segundo)

    # Y un rango que se le monta encima aunque no sea igual
    montado = cerrar(client, h, "2026-07-20", "2026-07-25")
    print(f"  otro que se le monta:         {montado.status_code} · "
          f"{detalle(montado)}")
    assert montado.status_code == 422

    p = panel_lotes(client, h)
    print(f"  la merma sigue siendo una sola: {p['total_kilos_merma_ciclo']} kg"
          f" por {p['total_costo_merma_ciclo']}")
    assert D(p["total_kilos_merma_ciclo"]) == 5
    assert D(p["total_kilos_en_bodega"]) == 0
    assert stock(client, h, producto) == 0
    comprobar_cuadre(p, "doble cierre")


def test_un_ciclo_sin_merma_no_se_cierra_por_gusto(client, base_datos):
    """Si se despachó todo lo que se produjo no hay nada que dar por perdido.
    Cerrar igual dejaría un ciclo en cero tapando un rango, y el próximo ciclo
    arrancaría después: los días de ese rango no se volverían a mirar nunca."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=1300, kilos=130)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 130, 25000)

    r = cerrar(client, h, "2026-07-16", "2026-07-22")
    print("\n===== 3b. SIN MERMA NO HAY QUE CERRAR =====")
    print(f"  se despacharon los 130 kg: {r.status_code} · {detalle(r)}")
    assert r.status_code == 422
    assert "no quedó merma" in detalle(r)


# ---------------------------------------------------------------------------
# 4. REABRIR DESHACE LA MERMA
# ---------------------------------------------------------------------------
def test_reabrir_deshace_la_merma_completa(client, base_datos):
    """Se cerró por equivocación (el rango estaba mal, o faltaba cargar una
    venta). Reabrir tiene que devolver TODO a como estaba: el queso a la bodega,
    el costo al lote y la utilidad a la cifra de antes. Si quedara la mitad
    deshecha sería peor que no poder reabrir.
    """
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=800, kilos=80)
    producir(client, h, "2026-07-17", tipo, litros=500, kilos=50)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 125, 25000)

    antes = panel_lotes(client, h)
    print("\n===== 4. REABRIR =====")
    print(f"  antes:  bodega {antes['total_kilos_en_bodega']} kg"
          f" ({antes['total_costo_en_bodega']}) · utilidad {antes['total_utilidad']}")

    ciclo = cerrar(client, h, "2026-07-16", "2026-07-22").json()
    cerrado = panel_lotes(client, h)
    print(f"  cerrado: bodega {cerrado['total_kilos_en_bodega']} kg"
          f" · merma {cerrado['total_kilos_merma_ciclo']} kg"
          f" · utilidad {cerrado['total_utilidad']}")
    assert D(cerrado["total_kilos_merma_ciclo"]) == 5

    r = client.post(f"{API}/ciclos/{ciclo['id']}/reabrir", headers=h)
    assert r.status_code == 200, r.text
    reabierto = r.json()
    print(f"  reabierto: cerrado={reabierto['cerrado']}"
          f" merma={reabierto['kilos_merma']}")
    assert reabierto["cerrado"] is False
    assert D(reabierto["kilos_merma"]) == 0
    assert reabierto["por_lote"] == []

    despues = panel_lotes(client, h)
    print(f"  después: bodega {despues['total_kilos_en_bodega']} kg"
          f" ({despues['total_costo_en_bodega']}) · utilidad"
          f" {despues['total_utilidad']}")
    # Todo vuelve exactamente a donde estaba, cifra por cifra
    assert D(despues["total_kilos_en_bodega"]) == D(antes["total_kilos_en_bodega"])
    assert D(despues["total_costo_en_bodega"]) == D(antes["total_costo_en_bodega"])
    assert D(despues["total_utilidad"]) == D(antes["total_utilidad"])
    assert D(despues["total_kilos_de_baja"]) == 0
    assert D(despues["total_kilos_merma_ciclo"]) == 0
    # Y el stock del inventario dice lo mismo que el reparto
    assert stock(client, h, producto) == D(despues["total_kilos_en_bodega"]) == 5
    comprobar_cuadre(despues, "reabierto")

    # Reabrir dos veces no tiene sentido y se avisa
    otra = client.post(f"{API}/ciclos/{ciclo['id']}/reabrir", headers=h)
    print(f"  reabrir otra vez: {otra.status_code} · {detalle(otra)}")
    assert otra.status_code == 422

    # Y se puede volver a cerrar DE UNA. Un ciclo reabierto no tapa sus fechas:
    # ya no tiene merma, así que no hay nada que se pueda cobrar dos veces. Si
    # estorbara, el dueño que cierra por equivocación quedaría atrapado.
    devuelta = cerrar(client, h, "2026-07-16", "2026-07-22")
    print(f"  volver a cerrar el mismo rango: {devuelta.status_code}")
    assert devuelta.status_code == 201, detalle(devuelta)
    assert D(panel_lotes(client, h)["total_kilos_merma_ciclo"]) == 5

    # Y no quedan dos filas de los mismos días: la vacía se recogió sola
    lista = panel_ciclos(client, h)["ciclos"]
    print(f"  ciclos en la lista: {len(lista)} "
          f"({[c['nombre'] for c in lista]})")
    assert len(lista) == 1, "quedó el ciclo reabierto vacío al lado del bueno"
    assert D(panel_ciclos(client, h)["total_kilos_merma"]) == 5


def test_un_ciclo_cerrado_no_se_puede_borrar(client, base_datos):
    """Borrar un ciclo cerrado dejaría sus ajustes de inventario huérfanos: kilos
    dados de baja sin nada que los explique, y sin forma de deshacerlos."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=1300, kilos=130)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 125, 25000)
    ciclo = cerrar(client, h, "2026-07-16", "2026-07-22").json()

    r = client.delete(f"{API}/ciclos/{ciclo['id']}", headers=h)
    print("\n===== 4b. NO SE BORRA UN CICLO CERRADO =====")
    print(f"  {r.status_code} · {detalle(r)}")
    assert r.status_code == 422
    assert "Reábralo primero" in detalle(r)


# ---------------------------------------------------------------------------
# 5. NO SE CUENTA DOS VECES SI YA HABÍA UN AJUSTE A MANO
# ---------------------------------------------------------------------------
def test_un_ajuste_a_mano_dentro_del_ciclo_no_se_cobra_dos_veces(client, base_datos):
    """EL RIESGO MÁS FEO DE TODOS.

    Si el dueño ya anotó "se dañaron 3 kg" dentro del ciclo, esos kilos YA
    salieron de la bodega y su costo YA se le restó al lote. Si el cierre
    calculara la merma solo como producido − vendido, volvería a dar por
    perdidos esos 3 kg: el mismo queso cobrado dos veces, la bodega en negativo
    y la utilidad más baja de la real.

    La cuenta los RESTA: 130 producidos − 122 vendidos − 3 bajados a mano = 5 de
    merma, no 8.
    """
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=1300, kilos=130)
    producto = producto_de(client, h, tipo)
    # Se dañaron 3 kg y el dueño los anotó a mano
    movimiento(client, h, producto, "2026-07-19", "ajuste", -3,
               referencia="Se dañó una bola")
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 122, 25000)

    p = propuesta(client, h, desde="2026-07-16", hasta="2026-07-22")
    print("\n===== 5. EL AJUSTE A MANO NO SE COBRA DOS VECES =====")
    print(f"  producido {p['kilos_producidos']}"
          f" − vendido {p['kilos_vendidos']}"
          f" − ya bajado a mano {p['kilos_ajuste_manual']}"
          f" = MERMA {p['kilos_merma']} kg")
    assert D(p["kilos_producidos"]) == 130
    assert D(p["kilos_vendidos"]) == 122
    assert D(p["kilos_ajuste_manual"]) == 3
    assert D(p["kilos_merma"]) == 5, "se están cobrando otra vez los 3 kg dañados"

    assert cerrar(client, h, "2026-07-16", "2026-07-22").status_code == 201
    lotes = panel_lotes(client, h)
    lote = lotes["lotes"][0]
    print(f"  el lote: vendidos {lote['kilos_vendidos']} kg,"
          f" de baja {lote['kilos_de_baja']} (de esos,"
          f" {lote['kilos_merma_ciclo']} de merma de ciclo),"
          f" en bodega {lote['kilos_en_bodega']}")
    # 122 vendidos + 8 de baja (3 dañados + 5 secados) + 0 en bodega = 130
    assert D(lote["kilos_de_baja"]) == 8
    assert D(lote["kilos_merma_ciclo"]) == 5
    assert D(lote["kilos_en_bodega"]) == 0
    assert stock(client, h, producto) == 0
    comprobar_cuadre(lotes, "ajuste a mano")

    # Y CONTRA SÍ MISMO: el ajuste que creó el cierre no se puede volver a contar
    # como un ajuste suelto del dueño en el ciclo siguiente.
    siguiente = propuesta(client, h, desde="2026-07-23", hasta="2026-07-29")
    print(f"  el ciclo siguiente NO ve la merma del anterior como ajuste a mano:"
          f" {siguiente['kilos_ajuste_manual']} kg")
    assert D(siguiente["kilos_ajuste_manual"]) == 0


# ---------------------------------------------------------------------------
# 6. UNA MERMA ABSURDA AVISA
# ---------------------------------------------------------------------------
def test_una_merma_negativa_avisa_en_vez_de_registrarse_callada(client, base_datos):
    """Si salió MÁS queso del que se produjo, eso no es merma: o falta cargar
    una tanda, o se despachó queso de un ciclo anterior. Registrarlo callado
    escondería el problema debajo de una cifra que parece normal."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov, 2000, transportador)
    # Queso de un ciclo anterior que quedó en bodega
    producir(client, h, "2026-07-01", tipo, litros=700, kilos=70)
    producir(client, h, "2026-07-16", tipo, litros=1000, kilos=100)
    producto = producto_de(client, h, tipo)
    # Se despachan 140: 100 del ciclo y 40 del queso viejo
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 140, 25000)

    p = propuesta(client, h, desde="2026-07-16", hasta="2026-07-22")
    print("\n===== 6. MERMA NEGATIVA =====")
    print(f"  producido {p['kilos_producidos']} − vendido {p['kilos_vendidos']}"
          f" = {p['kilos_merma']} kg")
    for aviso in p["advertencias"]:
        print(f"    AVISO: {aviso}")
    assert D(p["por_tipo"][0]["kilos_merma"]) == -40
    assert p["advertencias"], "una merma negativa pasó sin avisar"
    assert "MÁS de los que se" in p["advertencias"][0]
    # No se le carga merma a nadie: no se le puede quitar peso a lo que ya salió
    assert D(p["kilos_merma"]) == 0
    assert p["por_lote"] == []

    # Y no pasa sin que alguien acepte el aviso a mano
    r = cerrar(client, h, "2026-07-16", "2026-07-22")
    print(f"  cerrar sin aceptar: {r.status_code} · {detalle(r)[:90]}...")
    assert r.status_code == 422
    assert "no cuadra" in detalle(r)

    con_visto_bueno = cerrar(client, h, "2026-07-16", "2026-07-22",
                             aceptar_advertencias=True)
    print(f"  cerrar aceptando:   {con_visto_bueno.status_code}")
    assert con_visto_bueno.status_code == 201
    # Queda escrito con qué avisos se cerró, para poder auditarlo
    assert con_visto_bueno.json()["advertencias"], "no quedó constancia del aviso"


def test_una_merma_desproporcionada_avisa(client, base_datos):
    """El queso que se seca pierde alrededor del 4%. Un 25% no es secado: es una
    venta sin anotar. Se avisa antes de dar esa plata por perdida."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=1300, kilos=130)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 97, 25000)

    p = propuesta(client, h, desde="2026-07-16", hasta="2026-07-22")
    print("\n===== 6b. MERMA DESPROPORCIONADA =====")
    print(f"  merma {p['kilos_merma']} kg = {p['porcentaje']}% de lo producido")
    for aviso in p["advertencias"]:
        print(f"    AVISO: {aviso}")
    assert D(p["kilos_merma"]) == 33
    assert D(p["porcentaje"]) > 10
    assert any("venta sin anotar" in a for a in p["advertencias"])
    assert cerrar(client, h, "2026-07-16", "2026-07-22").status_code == 422
    assert cerrar(client, h, "2026-07-16", "2026-07-22",
                  aceptar_advertencias=True).status_code == 201


def test_el_sistema_propone_cerrar_a_los_siete_dias(client, base_datos):
    """QUE NO SEA UNA TAREA MÁS. El ciclo se repite cada semana; si hubiera que
    acordarse de abrirlo, en tres semanas nadie lo haría. El sistema propone el
    rango con la cuenta ya hecha y solo falta el botón."""
    from datetime import date, timedelta

    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    hace_ocho = date.today() - timedelta(days=8)
    hace_dos = date.today() - timedelta(days=2)
    recibir(client, h, str(hace_ocho), prov, 1300, transportador)
    producir(client, h, str(hace_ocho), tipo, litros=1300, kilos=130)
    producto = producto_de(client, h, tipo)
    vender(client, h, str(hace_dos), cliente_nuevo(client, h), producto, 125, 25000)

    panel = panel_ciclos(client, h)
    p = panel["propuesta"]
    print("\n===== 6c. EL SISTEMA PROPONE =====")
    print(f"  {p['nombre_sugerido']} · {p['dias']} días")
    print(f"  van {p['dias_desde_ultimo_cierre']} días desde el último cierre"
          f" -> toca_cerrar={p['toca_cerrar']}")
    print(f"  y la cuenta ya viene hecha: {p['kilos_merma']} kg por"
          f" {p['costo_merma']}")
    assert p["fecha_inicio"] == str(hace_ocho), "no arrancó en la primera tanda"
    assert p["toca_cerrar"] is True
    assert D(p["kilos_merma"]) == 5
    assert D(p["costo_merma"]) == 95_000

    # Al cerrar, la próxima propuesta arranca al día siguiente del último cierre
    assert cerrar(client, h, p["fecha_inicio"], p["fecha_fin"]).status_code == 201
    siguiente = panel_ciclos(client, h)["propuesta"]
    esperado = (hace_ocho + timedelta(days=7)).isoformat()
    print(f"  la próxima propuesta arranca el {siguiente['fecha_inicio']}"
          f" (día siguiente al cierre)")
    assert siguiente["fecha_inicio"] == esperado


# ---------------------------------------------------------------------------
# 7. NO SE CRUZAN LAS EMPRESAS
# ---------------------------------------------------------------------------
def test_los_ciclos_de_una_empresa_no_tocan_los_de_la_otra(client, base_datos):
    """Dos queseras distintas en la misma base. El ciclo de una no puede ver, ni
    cerrar, ni reabrir nada de la otra, y la merma de una no puede bajarle el
    queso a la otra. Es el candado de siempre: empresa_id en toda consulta."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    # Las dos empresas montan lo mismo, el mismo día y con los mismos kilos
    for h in (ha, hb):
        transportador, prov = montar_leche(client, h)
        tipo = tipo_queso(client, h, "Queso campesino")
        recibir(client, h, "2026-07-16", prov, 1300, transportador)
        producir(client, h, "2026-07-16", tipo, litros=1300, kilos=130)
        producto = producto_de(client, h, tipo)
        vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 125, 25000)

    print("\n===== 7. DOS EMPRESAS =====")
    # Solo la A cierra su ciclo
    ciclo_a = cerrar(client, ha, "2026-07-16", "2026-07-22").json()
    a = panel_lotes(client, ha)
    b = panel_lotes(client, hb)
    print(f"  empresa A (cerró): bodega {a['total_kilos_en_bodega']} kg,"
          f" merma {a['total_kilos_merma_ciclo']} kg")
    print(f"  empresa B (no):    bodega {b['total_kilos_en_bodega']} kg,"
          f" merma {b['total_kilos_merma_ciclo']} kg")
    assert D(a["total_kilos_merma_ciclo"]) == 5
    assert D(a["total_kilos_en_bodega"]) == 0
    # A la B no le pasó nada: sus 5 kg fantasma siguen ahí
    assert D(b["total_kilos_merma_ciclo"]) == 0
    assert D(b["total_kilos_en_bodega"]) == 5

    # La B no ve el ciclo de la A ni en la lista
    lista_b = panel_ciclos(client, hb)
    print(f"  la B ve {len(lista_b['ciclos'])} ciclos en su lista")
    assert lista_b["ciclos"] == []

    # Ni lo puede reabrir ni borrar aunque tenga el id
    r = client.post(f"{API}/ciclos/{ciclo_a['id']}/reabrir", headers=hb)
    print(f"  la B intenta reabrir el ciclo de la A: {r.status_code}")
    assert r.status_code == 404
    r = client.delete(f"{API}/ciclos/{ciclo_a['id']}", headers=hb)
    print(f"  la B intenta borrarlo:                 {r.status_code}")
    assert r.status_code == 404

    # Y la A sigue viendo el suyo intacto
    lista_a = panel_ciclos(client, ha)
    assert len(lista_a["ciclos"]) == 1
    assert D(lista_a["total_kilos_merma"]) == 5
    # Y el total del panel es la suma exacta de las filas de la lista
    assert D(lista_a["total_kilos_merma"]) == sum(
        D(c["kilos_merma"]) for c in lista_a["ciclos"]
    )
    assert D(lista_a["total_costo_merma"]) == sum(
        D(c["costo_merma"]) for c in lista_a["ciclos"]
    )
    comprobar_cuadre(a, "empresa A")
    comprobar_cuadre(b, "empresa B")


def test_la_merma_no_se_pasa_de_un_tipo_de_queso_a_otro(client, base_datos):
    """No se puede compensar el doble crema que faltó con el campesino que
    sobró: son dos productos, dos colas de inventario y dos rendimientos. Si se
    mezclaran, la cuenta escondería que a uno le falta queso."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    campesino = tipo_queso(client, h, "Queso campesino")
    doble = tipo_queso(client, h, "Queso doble crema")
    recibir(client, h, "2026-07-16", prov, 2000, transportador)
    producir(client, h, "2026-07-16", campesino, litros=1000, kilos=100)
    producir(client, h, "2026-07-17", doble, litros=1000, kilos=100)
    p_campesino = producto_de(client, h, campesino)
    p_doble = producto_de(client, h, doble)
    cliente = cliente_nuevo(client, h)
    # Del campesino se despacha todo; del doble crema faltan 4 kg
    vender(client, h, "2026-07-22", cliente, p_campesino, 100, 25000)
    vender(client, h, "2026-07-22", cliente, p_doble, 96, 28000)

    p = propuesta(client, h, desde="2026-07-16", hasta="2026-07-22")
    print("\n===== 7b. CADA TIPO DE QUESO POR SEPARADO =====")
    for t in p["por_tipo"]:
        print(f"  {t['tipo_queso']}: produjo {t['kilos_producidos']},"
              f" vendió {t['kilos_vendidos']} -> merma {t['kilos_merma']} kg")
    por_nombre = {t["tipo_queso"]: t for t in p["por_tipo"]}
    assert D(por_nombre["Queso campesino"]["kilos_merma"]) == 0
    assert D(por_nombre["Queso doble crema"]["kilos_merma"]) == 4
    # El total es la suma exacta de los renglones por tipo
    assert D(p["kilos_merma"]) == sum(
        max(D(t["kilos_merma"]), D(0)) for t in p["por_tipo"]
    )

    assert cerrar(client, h, "2026-07-16", "2026-07-22").status_code == 201
    lotes = panel_lotes(client, h)
    por_tipo = {l["tipo_queso"]: l for l in lotes["lotes"]}
    print(f"  al campesino no le tocó merma:"
          f" {por_tipo['Queso campesino']['kilos_merma_ciclo']} kg")
    print(f"  al doble crema sí:"
          f" {por_tipo['Queso doble crema']['kilos_merma_ciclo']} kg")
    assert D(por_tipo["Queso campesino"]["kilos_merma_ciclo"]) == 0
    assert D(por_tipo["Queso doble crema"]["kilos_merma_ciclo"]) == 4
    comprobar_cuadre(lotes, "dos tipos")


def test_la_merma_del_ciclo_llega_al_estado_de_resultados(client, base_datos):
    """La merma tenía que entrar por `kilos_de_baja` justamente para esto: para
    que la contabilidad la recoja sin tocar nada. El queso que se secó es plata
    que salió sin ingreso, igual que el que se daña."""
    h = auth_headers(client, "admin.a")
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-16", prov, 1300, transportador)
    producir(client, h, "2026-07-16", tipo, litros=1300, kilos=130)
    producto = producto_de(client, h, tipo)
    vender(client, h, "2026-07-22", cliente_nuevo(client, h), producto, 125, 25000)

    def resultados():
        r = client.get(
            "/api/v1/contabilidad/estado-resultados",
            params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=h,
        )
        assert r.status_code == 200, r.text
        return r.json()

    antes = resultados()
    assert cerrar(client, h, "2026-07-16", "2026-07-22").status_code == 201
    despues = resultados()

    print("\n===== 7c. LA MERMA LLEGA A LA CONTABILIDAD =====")
    print(f"  queso dañado: {antes.get('queso_danado')} -> "
          f"{despues.get('queso_danado')}")
    print(f"  queso en bodega: {antes.get('queso_en_bodega')} -> "
          f"{despues.get('queso_en_bodega')}")
    assert D(despues["queso_danado"]) == D(antes["queso_danado"]) + 95_000
    assert D(despues["queso_en_bodega"]) == D(antes["queso_en_bodega"]) - 95_000
