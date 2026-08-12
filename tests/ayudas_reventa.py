"""LO QUE COMPARTEN LAS PRUEBAS DEL CATÁLOGO DE REVENTA: historias y la foto.

NO ES UN MÓDULO DE PRUEBAS (no empieza por `test_`, así que pytest no lo recoge). Es
donde viven las tres piezas que se repetían copiadas en cuatro archivos:

  · CÓMO SE ESCRIBE (compras, ventas, ajustes, el catálogo);
  · LAS HISTORIAS con las que se mide, con cifras feas a propósito —decimales que no
    cuadran redondo y precios que no son múltiplos de nada— para que los redondeos se
    noten;
  · LA FOTO: todo lo que el dueño mira, aplanado cifra por cifra, y las dos funciones
    que la interrogan (`exigir_quieto` y `regla_de_oro`).

POR QUÉ LA FOTO ES ASÍ DE ANCHA. Lo que hay que demostrar no es que una pantalla siga
dando lo mismo: es que NINGUNA se movió. Por eso `foto` recorre el resumen entero con su
desglose y sus existencias, el panel de lotes con su detalle por compra y por venta, la
ganancia por día y los estados de cuenta que se le entregan al cliente y al productor,
y guarda cada número con su camino completo. Comparar dos fotos dice CUÁL cifra se
movió, cuánto valía y cuánto vale, y no solo que algo cambió.

Y SE INDEXA POR CLAVE Y NO POR POSICIÓN: cada fila se guarda bajo el nombre del
producto, del productor o del cliente del que habla. Con índices, agregar un producto al
catálogo correría la lista y TODAS las filas de abajo saldrían reportadas como movidas,
que es ruido que tapa la única que de verdad importa.
"""
from decimal import Decimal

API = "/api/v1/reventa"
PROD = f"{API}/productos"
PERIODO = {"desde": "2026-01-01", "hasta": "2026-12-31"}
CERO = Decimal("0")

# Los terceros de las historias, para pedir sus estados de cuenta.
CLIENTES = ("Don José Pérez", "Supermercado La 33", "Tienda La Esquina")
PRODUCTORES = ("Patricia Rojas", "Sebastián Ruiz", "Lácteos del Valle")


def D(v) -> Decimal:
    return Decimal(str(v))


# --------------------------------------------------------------- escritura
def compra(client, h, **campos):
    r = client.post(f"{API}/compras", json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, h, **campos):
    r = client.post(f"{API}/ventas", json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def ajuste(client, h, **campos):
    r = client.post(f"{API}/conversiones", json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def crear_producto(client, h, **campos):
    r = client.post(PROD, json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def productos(client, h) -> dict:
    """El catálogo POR CLAVE, que es la identidad del producto (el nombre cambia)."""
    r = client.get(PROD, params={"size": 100}, headers=h)
    assert r.status_code == 200, r.text
    return {p["clave"]: p for p in r.json()["items"]}


# ------------------------------------------------------------------ lectura
def resumen(client, h) -> dict:
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def lotes(client, h) -> dict:
    r = client.get(f"{API}/lotes", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def fila(res, clave):
    """La fila del desglose de ese producto, o None si no tiene ninguna."""
    filas = [f for f in res["por_producto"] if f["producto"] == clave]
    assert len(filas) <= 1, f"hay {len(filas)} filas de '{clave}'"
    return filas[0] if filas else None


def existencia(res, clave):
    """Lo que el resumen dice que hay en bodega de ese producto, o None."""
    filas = [e for e in res["existencias"] if e["producto"] == clave]
    return D(filas[0]["disponible"]) if filas else None


# --------------------------------------------------------------- la historia
def historia(client, h) -> dict:
    """Compras, ventas, ajustes, abonos y los tres productos de siempre."""
    ids: dict = {}
    ids["c1"] = compra(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                       kilos_brutos="820.53", precio_kilo="14317",
                       borona_kilos="18.27")["id"]
    ids["c2"] = compra(client, h, fecha="2026-02-03", productor="Sebastián Ruiz",
                       kilos_brutos="410.11", precio_kilo="15033",
                       borona_kilos="7.09")["id"]
    ids["c3"] = compra(client, h, fecha="2026-02-17", productor="Lácteos del Valle",
                       kilos_brutos="633.87", precio_kilo="13871",
                       borona_kilos="25.14")["id"]
    ids["m1"] = compra(client, h, fecha="2026-02-05", productor="Sebastián Ruiz",
                       tipo="mozzarella", barras="137", precio_barra="12433")["id"]

    ajuste(client, h, fecha="2026-02-10", kilos="30.30", destino="borona",
           precio_kilo="3100")
    ajuste(client, h, fecha="2026-02-21", kilos="11.11", destino="merma")
    ajuste(client, h, fecha="2026-03-02", kilos="7.77", destino="borona",
           precio_kilo="3250")

    ids["v1"] = venta(client, h, fecha="2026-02-12", cliente="Don José Pérez",
                      tipo="queso", kilos="900.37", precio_kilo="21533",
                      gasto_por_kilo="137")["id"]
    ids["v2"] = venta(client, h, fecha="2026-02-24", cliente="Supermercado La 33",
                      tipo="queso", kilos="311.44", precio_kilo="20917",
                      gasto_por_kilo="93")["id"]
    ids["v3"] = venta(client, h, fecha="2026-02-26", cliente="Tienda La Esquina",
                      tipo="borona", kilos="33.37", precio_kilo="4133")["id"]
    ids["v4"] = venta(client, h, fecha="2026-03-04", cliente="Don José Pérez",
                      tipo="mozzarella", barras="59", precio_barra="17311",
                      gasto_por_barra="211")["id"]
    ids["v5"] = venta(client, h, fecha="2026-03-09", cliente="Tienda La Esquina",
                      tipo="borona", kilos="12.13", precio_kilo="4017")["id"]

    r = client.post(f"{API}/compras/{ids['c1']}/abonos",
                    json={"fecha": "2026-02-14", "valor": "5000000.33"}, headers=h)
    assert r.status_code in (200, 201), r.text
    r = client.post(f"{API}/ventas/{ids['v1']}/abonos",
                    json={"fecha": "2026-02-20", "valor": "9000000.77"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return ids


def historia_gorda(client, h) -> dict:
    """Todo junto: heredado, comprado, gratis a un producto propio, ajustes y ventas.

    Es la historia más completa que sabemos armar por la API: dos grupos de costeo
    (queso con su borona, y un costeño propio del dueño con su recorte), un
    subproducto COMPRADO directamente, kilos que llegan gratis a los dos subproductos,
    ajustes a subproducto y a merma de los dos padres, un producto por unidades, y
    ventas de los cinco productos con una partida entre lotes.
    """
    ids: dict = {}
    ids["costeno"] = crear_producto(client, h, nombre="Costeño", unidad="kg")
    ids["recorte"] = crear_producto(client, h, nombre="Recorte", unidad="kg",
                                    subproducto_de_id=ids["costeno"]["id"])

    compra(client, h, fecha="2026-02-03", productor="Patricia Rojas",
           kilos_brutos="820.53", precio_kilo="14317", borona_kilos="18.27")
    compra(client, h, fecha="2026-02-03", productor="Sebastián Ruiz",
           kilos_brutos="410.11", precio_kilo="15033", borona_kilos="7.09")
    # Compra de COSTEÑO, con recorte gratis encima (nombrando al destinatario).
    compra(client, h, fecha="2026-02-04", productor="Lácteos del Valle",
           tipo="costeno", kilos_brutos="233.41", precio_kilo="9137",
           borona_kilos="11.03", subproducto_tipo="recorte")
    # Un SUBPRODUCTO COMPRADO DIRECTAMENTE: la borona con su propio costo.
    compra(client, h, fecha="2026-02-06", productor="Sebastián Ruiz",
           tipo="borona", kilos_brutos="53.37", precio_kilo="1133")
    compra(client, h, fecha="2026-02-05", productor="Sebastián Ruiz",
           tipo="mozzarella", barras="137", precio_barra="12433")

    ajuste(client, h, fecha="2026-02-10", kilos="30.30", destino="borona",
           precio_kilo="3100")
    ajuste(client, h, fecha="2026-02-21", kilos="11.11", destino="merma")
    ajuste(client, h, fecha="2026-02-22", kilos="9.13", destino="borona",
           producto_origen="costeno", producto_destino="recorte", precio_kilo="900")
    ajuste(client, h, fecha="2026-02-23", kilos="2.07", destino="merma",
           producto_origen="costeno")

    venta(client, h, fecha="2026-02-12", cliente="Don José Pérez", tipo="queso",
          kilos="900.37", precio_kilo="21533", gasto_por_kilo="137")
    venta(client, h, fecha="2026-02-24", cliente="Supermercado La 33", tipo="queso",
          kilos="211.44", precio_kilo="20917", gasto_por_kilo="93")
    venta(client, h, fecha="2026-02-26", cliente="Tienda La Esquina", tipo="borona",
          kilos="61.37", precio_kilo="4133")
    venta(client, h, fecha="2026-02-27", cliente="Supermercado La 33", tipo="costeno",
          kilos="150.11", precio_kilo="13711", gasto_por_kilo="41")
    venta(client, h, fecha="2026-02-28", cliente="Tienda La Esquina", tipo="recorte",
          kilos="13.03", precio_kilo="2017")
    venta(client, h, fecha="2026-03-04", cliente="Don José Pérez", tipo="mozzarella",
          barras="59", precio_barra="17311", gasto_por_barra="211")
    return ids


def historia_solo_gratis(client, h) -> None:
    """Queso comprado y vendido; la borona SOLO llegó GRATIS.

    Es la historia que destapó el hueco: sin una compra, una venta ni un ajuste
    suyos, la borona no tenía "movimientos" para el catálogo, aunque tuviera 25,36 kg
    en la bodega y esos kilos salieran reportados en las existencias del resumen.
    """
    compra(client, h, fecha="2026-02-03", productor="Patricia Rojas",
           kilos_brutos="820.53", precio_kilo="14317", borona_kilos="18.27")
    compra(client, h, fecha="2026-02-17", productor="Sebastián Ruiz",
           kilos_brutos="410.11", precio_kilo="15033", borona_kilos="7.09")
    venta(client, h, fecha="2026-02-12", cliente="Don José Pérez", tipo="queso",
          kilos="500.37", precio_kilo="21533", gasto_por_kilo="137")


# ------------------------------------------------------------------ la foto
def _cifras(nodo, ruta, salida: dict, *, por_clave: bool) -> None:
    if isinstance(nodo, dict):
        etiqueta = None
        if por_clave:
            etiqueta = nodo.get("producto") or nodo.get("productor") or nodo.get("cliente")
        for clave, valor in nodo.items():
            paso = f"{clave}" if etiqueta is None else f"{etiqueta}.{clave}"
            _cifras(valor, f"{ruta}.{paso}", salida, por_clave=por_clave)
    elif isinstance(nodo, list):
        for i, hijo in enumerate(nodo):
            marca = ""
            if not por_clave or not (
                isinstance(hijo, dict)
                and (hijo.get("producto") or hijo.get("productor") or hijo.get("cliente"))
            ):
                marca = f"[{i}]"
            _cifras(hijo, f"{ruta}{marca}", salida, por_clave=por_clave)
    elif isinstance(nodo, str):
        try:
            salida[ruta] = Decimal(nodo)
        except Exception:
            pass
    elif isinstance(nodo, (int, float)) and not isinstance(nodo, bool):
        salida[ruta] = Decimal(str(nodo))


def foto(client, h) -> dict[str, Decimal]:
    """Todo lo que el dueño mira, aplanado a {camino: cifra}."""
    salida: dict[str, Decimal] = {}
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    _cifras(r.json(), "resumen", salida, por_clave=True)
    r = client.get(f"{API}/lotes", headers=h)
    assert r.status_code == 200, r.text
    _cifras(r.json(), "lotes", salida, por_clave=True)
    r = client.get(f"{API}/ganancia-por-dia", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    _cifras(r.json(), "ganancia_por_dia", salida, por_clave=True)
    for cliente in CLIENTES:
        r = client.get(f"{API}/estado-cuenta", params={"cliente": cliente}, headers=h)
        assert r.status_code in (200, 404), r.text
        salida[f"ec_cliente::{cliente}::http"] = D(r.status_code)
        if r.status_code == 200:
            _cifras(r.json(), f"ec_cliente::{cliente}", salida, por_clave=False)
    for productor in PRODUCTORES:
        r = client.get(f"{API}/estado-cuenta-productor",
                       params={"productor": productor}, headers=h)
        assert r.status_code in (200, 404), r.text
        salida[f"ec_productor::{productor}::http"] = D(r.status_code)
        if r.status_code == 200:
            _cifras(r.json(), f"ec_productor::{productor}", salida, por_clave=False)
    return salida


# Columnas que un producto nuevo puede ESTRENAR sin que eso sea un movimiento: son
# promedios por unidad de una fila que nació en cero, no plata.
COLUMNAS_INFORMATIVAS = ("costo_kilo", "costo_barra")


def diferencias(antes: dict, despues: dict) -> tuple[list, list]:
    movidas = [
        (ruta, valor, despues.get(ruta))
        for ruta, valor in antes.items()
        if ruta not in despues or despues[ruta] != valor
    ]
    nacidas = [
        (ruta, valor) for ruta, valor in despues.items()
        if ruta not in antes and valor != CERO
        and not ruta.endswith(COLUMNAS_INFORMATIVAS)
    ]
    return movidas, nacidas


def exigir_quieto(antes: dict, despues: dict, que_se_hizo: str) -> None:
    """Ni una cifra movida y ninguna estrenada distinta de cero."""
    movidas, nacidas = diferencias(antes, despues)
    if movidas or nacidas:
        print(f"\n===== {que_se_hizo}: {len(movidas)} movidas, {len(nacidas)} nacidas =====")
        for ruta, viejo, nuevo in movidas[:60]:
            print(f"   MOVIÓ {ruta}: {viejo} -> {nuevo}")
        for ruta, valor in nacidas[:60]:
            print(f"   NACIÓ  {ruta} = {valor}")
    assert not movidas, f"{que_se_hizo} movió {len(movidas)} cifras: {movidas[:3]}"
    assert not nacidas, f"{que_se_hizo} estrenó cifras: {nacidas[:3]}"


def regla_de_oro(res, titulo) -> None:
    """LA REGLA DE ORO: cada columna del desglose suma EXACTO su cifra grande.

    Y ninguna clave repetida: dos filas con la misma clave son la misma mercancía
    contada dos veces, y la pantalla no sabría cuál pintar.
    """
    for campo, columna in (("total_compras", "costo"), ("total_ventas", "ingreso"),
                           ("total_gastos", "gastos"),
                           ("ganancia_estimada", "ganancia")):
        suma = sum((D(f[columna]) for f in res["por_producto"]), CERO)
        assert suma == D(res[campo]), (
            f"[{titulo}] la columna '{columna}' suma {suma} y '{campo}' dice "
            f"{res[campo]}  (diferencia {suma - D(res[campo])})"
        )
    claves = [f["producto"] for f in res["por_producto"]]
    repetidas = sorted({c for c in claves if claves.count(c) > 1})
    assert not repetidas, f"[{titulo}] filas con la clave repetida: {repetidas}"


def se_puede_seguir_trabajando(client, h) -> dict:
    """Lo de todos los días: la compra con borona encima, el ajuste y la venta.

    Devuelve {qué se intentó: (código, cuerpo)}. Es lo que hay que poder hacer DESPUÉS
    de cualquier cosa que se le haga al catálogo: un problema de una lista de productos
    no puede dejar al dueño sin poder anotar su trabajo.
    """
    resultados: dict[str, tuple[int, str]] = {}
    r = client.post(f"{API}/compras",
                    json={"fecha": "2026-03-15", "productor": "Patricia Rojas",
                          "kilos_brutos": "100.00", "precio_kilo": "14000",
                          "borona_kilos": "5.00"}, headers=h)
    resultados["compra de queso con borona encima"] = (r.status_code, r.text[:200])
    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-16", "kilos": "3.00",
                          "destino": "borona", "precio_kilo": "3000"}, headers=h)
    resultados["ajuste de queso a borona"] = (r.status_code, r.text[:200])
    r = client.post(f"{API}/ventas",
                    json={"fecha": "2026-03-17", "cliente": "Tienda La Esquina",
                          "tipo": "borona", "kilos": "1.00", "precio_kilo": "4000"},
                    headers=h)
    resultados["venta de borona"] = (r.status_code, r.text[:200])
    return resultados


def exigir_que_se_pueda_trabajar(client, h, despues_de: str) -> None:
    resultados = se_puede_seguir_trabajando(client, h)
    print(f"\n   ===== lo de todos los días después de [{despues_de}] =====")
    for que, (codigo, cuerpo) in resultados.items():
        print(f"      {que:34} -> {codigo}  {cuerpo if codigo >= 400 else ''}")
    fallidas = {q: v for q, v in resultados.items() if v[0] >= 400}
    assert not fallidas, (
        f"después de '{despues_de}' el dueño no puede: {list(fallidas)} -> {fallidas}"
    )
