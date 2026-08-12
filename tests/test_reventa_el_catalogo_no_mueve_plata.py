"""EL CATÁLOGO NO PUEDE MOVER NI UN KILO NI UN PESO DE LO YA REGISTRADO.

QUÉ MIDE ESTA PRUEBA, Y POR QUÉ ES LA QUE IMPORTA. El catálogo de productos es lo que
el dueño administra: agrega un producto, lo baja o lo sube en la lista, lo renombra, lo
desactiva. Nada de eso es un movimiento de plata —no compró, no vendió, no ajustó
nada—, así que ninguna de esas seis operaciones puede cambiar una sola cifra de lo que
ya está anotado.

NO ES UNA PRECAUCIÓN TEÓRICA: PASÓ. Dos revisiones adversariales independientes
encontraron lo mismo, con estas cifras: crear un subproducto del queso con `orden = 0`
—o simplemente reordenar la lista con un PUT— le transfería al producto nuevo TODA la
historia de conversiones de la borona. La fila de la borona pasaba de 40,40 kg /
$498.765,07 / −$362.407,49 a 30,30 kg / $374.073,80 / −$237.716,22; aparecía una fila
con $498.765,07 de mercancía que nunca se compró; la existencia de borona quedaba en
−30,30 kg mientras el campo viejo decía 50,50 en la MISMA respuesta; y vender 1 kg de
borona rebotaba con un 422.

LA RAÍZ ERA UNA SOLA: se estaba usando un dato de PRESENTACIÓN (el orden del catálogo)
para decidir PLATA, porque el hecho que hacía falta —de qué producto a qué producto va
un ajuste, a quién le entra lo que llega gratis con una compra— NO ESTABA GUARDADO. Hoy
lo está: cada ajuste y cada compra nombran sus productos en su propia fila (migración
`c5d9e3a7b1f4`). Esta prueba es la que exige que siga siendo así.

CÓMO SE MIDE. Se carga una historia con cifras feas —decimales que no cuadran redondo,
precios que no son múltiplos de nada, varios productores, varios días, ventas partidas
entre lotes, ajustes de las dos clases, abonos y una compra anulada— y se toma una foto
COMPLETA: el resumen entero con su desglose, el panel de lotes con su detalle por
compra y por venta, la ganancia por día, los estados de cuenta que se le entregan al
cliente y al productor, y las existencias. Después se le hace al catálogo TODO lo que
se le puede hacer, una operación a la vez, y se vuelve a tomar la foto.

La comparación es fila por fila y cifra por cifra: cualquier cantidad o peso que ya
existía tiene que valer EXACTAMENTE lo mismo. Lo único que se le permite a una
operación del catálogo es AGREGAR renglones en cero (un producto nuevo aparece en las
existencias con 0, que es la verdad) y cambiar RÓTULOS (renombrar cambia la etiqueta,
que es justamente para lo que sirve).
"""
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PROD = f"{API}/productos"
PERIODO = {"desde": "2026-01-01", "hasta": "2026-12-31"}
CERO = Decimal("0")

# Los terceros de la historia, para pedir sus estados de cuenta.
CLIENTES = ("Don José Pérez", "Supermercado La 33", "Tienda La Esquina")
PRODUCTORES = ("Patricia Rojas", "Sebastián Ruiz", "Lácteos del Valle")


def D(v) -> Decimal:
    return Decimal(str(v))


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


# ============================================================== la historia
def _compra(client, h, **campos):
    r = client.post(f"{API}/compras", json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _venta(client, h, **campos):
    r = client.post(f"{API}/ventas", json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _ajuste(client, h, **campos):
    r = client.post(f"{API}/conversiones", json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def construir_historia(client, h) -> dict:
    """Compras, ventas, ajustes y abonos con cifras que obligan a repartir centavos.

    Los kilos con dos decimales que no son múltiplos de nada (820,53 / 33,37) y los
    precios impares (14.317 / 21.533) son a propósito: con cifras redondas un reparto
    mal hecho puede dar la misma respuesta por casualidad.
    """
    ids: dict = {}

    # -------------------------------------------------- compras de queso, con borona
    ids["c1"] = _compra(
        client, h, fecha="2026-02-03", productor="Patricia Rojas",
        kilos_brutos="820.53", precio_kilo="14317", borona_kilos="18.27",
    )["id"]
    ids["c2"] = _compra(
        client, h, fecha="2026-02-03", productor="Sebastián Ruiz",
        kilos_brutos="410.11", precio_kilo="15033", borona_kilos="7.09",
    )["id"]
    ids["c3"] = _compra(
        client, h, fecha="2026-02-17", productor="Lácteos del Valle",
        kilos_brutos="633.87", precio_kilo="13871", borona_kilos="25.14",
    )["id"]
    # Una compra ANULADA: su plata no cuenta, pero tiene que seguir sin contar igual
    # después de tocar el catálogo.
    ids["c_anulada"] = _compra(
        client, h, fecha="2026-02-19", productor="Patricia Rojas",
        kilos_brutos="99.99", precio_kilo="11111", borona_kilos="3.33",
    )["id"]
    r = client.post(f"{API}/compras/{ids['c_anulada']}/anular", headers=h)
    assert r.status_code == 200, r.text

    # ------------------------------------------------------ compras de mozzarella
    ids["m1"] = _compra(
        client, h, fecha="2026-02-05", productor="Sebastián Ruiz",
        tipo="mozzarella", barras="137", precio_barra="12433",
    )["id"]

    # ------------------------------------------------------------------- ajustes
    # Los dos destinos, en dos días distintos y con kilos feos.
    _ajuste(client, h, fecha="2026-02-10", kilos="30.30", destino="borona",
            precio_kilo="3100")
    _ajuste(client, h, fecha="2026-02-21", kilos="11.11", destino="merma")
    _ajuste(client, h, fecha="2026-03-02", kilos="7.77", destino="borona",
            precio_kilo="3250")

    # -------------------------------------------------------------------- ventas
    # Una venta grande que se parte entre dos lotes, y ventas de las tres clases.
    ids["v1"] = _venta(
        client, h, fecha="2026-02-12", cliente="Don José Pérez", tipo="queso",
        kilos="900.37", precio_kilo="21533", gasto_por_kilo="137",
    )["id"]
    ids["v2"] = _venta(
        client, h, fecha="2026-02-24", cliente="Supermercado La 33", tipo="queso",
        kilos="311.44", precio_kilo="20917", gasto_por_kilo="93",
    )["id"]
    ids["v3"] = _venta(
        client, h, fecha="2026-02-26", cliente="Tienda La Esquina", tipo="borona",
        kilos="33.37", precio_kilo="4133",
    )["id"]
    ids["v4"] = _venta(
        client, h, fecha="2026-03-04", cliente="Don José Pérez", tipo="mozzarella",
        barras="59", precio_barra="17311", gasto_por_barra="211",
    )["id"]
    ids["v5"] = _venta(
        client, h, fecha="2026-03-09", cliente="Tienda La Esquina", tipo="borona",
        kilos="12.13", precio_kilo="4017",
    )["id"]

    # -------------------------------------------------------------------- abonos
    r = client.post(f"{API}/compras/{ids['c1']}/abonos",
                    json={"fecha": "2026-02-14", "valor": "5000000.33"}, headers=h)
    assert r.status_code in (200, 201), r.text
    r = client.post(f"{API}/ventas/{ids['v1']}/abonos",
                    json={"fecha": "2026-02-20", "valor": "9000000.77"}, headers=h)
    assert r.status_code in (200, 201), r.text

    # ------------------------------------------------- una factura de dos renglones
    r = client.post(
        f"{API}/documentos",
        json={"tipo": "venta", "fecha": "2026-03-11", "tercero": "Supermercado La 33",
              "renglones": [
                  {"tipo": "queso", "kilos": "77.71", "precio_kilo": "22111",
                   "gasto_por_kilo": "51"},
                  {"tipo": "mozzarella", "barras": "23", "precio_barra": "17900"},
              ]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    ids["factura"] = r.json()["id"]
    return ids


# ========================================================== la foto de las cifras
def _cifras(nodo, ruta, salida: dict, *, por_clave: bool) -> None:
    """Mete en `salida` toda cadena que sea un NÚMERO, con su camino completo.

    Se recogen los números y nada más: los rótulos (`etiqueta`, `nota`, `nombre`) se
    quedan afuera a propósito, porque renombrar un producto SÍ los cambia y eso es
    justamente para lo que sirve renombrar. Lo que no se puede mover es la plata.

    `por_clave` dice cómo se nombra cada renglón de una lista:

    · EN EL RESUMEN Y EN LOS LOTES, por la CLAVE del producto o el nombre del tercero.
      Es lo que hace que agregar un producto no corra de sitio a los demás renglones y
      la comparación siga hablando de la misma fila.
    · EN LOS ESTADOS DE CUENTA, por su POSICIÓN. Ahí los renglones son ventas y
      compras (no productos), así que ninguna operación del catálogo puede agregar ni
      quitar uno; y el campo que los identifica es el NOMBRE del producto listo para
      entregárselo al cliente, que sí cambia al renombrar.
    """
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
    """TODAS las cifras que el dueño mira, aplanadas y comparables una por una."""
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
        assert r.status_code == 200, r.text
        _cifras(r.json(), f"ec_cliente::{cliente}", salida, por_clave=False)

    for productor in PRODUCTORES:
        r = client.get(f"{API}/estado-cuenta-productor",
                       params={"productor": productor}, headers=h)
        assert r.status_code == 200, r.text
        _cifras(r.json(), f"ec_productor::{productor}", salida, por_clave=False)

    return salida


# LO ÚNICO QUE UNA FILA RECIÉN NACIDA PUEDE TRAER DISTINTO DE CERO. `costo_kilo` y
# `costo_barra` no son plata de la fila: son el precio promedio de compra DEL GRUPO,
# la columna que el dueño cruza a mano con el recibo del productor, y sale igual en
# todos los renglones del grupo. Un subproducto nuevo sin un solo movimiento sale con
# cantidad 0, ingreso 0, costo 0 y ganancia 0 —que es lo que hay que exigir— y con esa
# columna informativa llena, igual que la borona.
COLUMNAS_INFORMATIVAS = ("costo_kilo", "costo_barra")


def exigir_que_no_se_movio(antes: dict, despues: dict, que_se_hizo: str) -> None:
    """Ni una cifra distinta, y las que aparezcan tienen que ser CERO.

    Las dos mitades hacen falta. Que las viejas no se muevan es lo obvio; que las
    nuevas salgan en cero es lo que atrapa el defecto de verdad, porque así es como se
    veía: al producto recién creado le aparecían 30,30 kg y $374.073,80 de una
    mercancía que nadie le compró nunca.
    """
    movidas = [
        (ruta, valor, despues.get(ruta))
        for ruta, valor in antes.items()
        if ruta not in despues or despues[ruta] != valor
    ]
    nacidas = [
        (ruta, valor) for ruta, valor in despues.items()
        if ruta not in antes
        and valor != CERO
        and not ruta.endswith(COLUMNAS_INFORMATIVAS)
    ]
    if movidas or nacidas:
        print(f"\n===== {que_se_hizo}: SE MOVIERON {len(movidas)} CIFRAS =====")
        for ruta, viejo, nuevo in movidas[:40]:
            print(f"   {ruta}: {viejo} -> {nuevo}")
        for ruta, valor in nacidas[:40]:
            print(f"   {ruta}: APARECIÓ con {valor} (tenía que nacer en cero)")
    assert not movidas, (
        f"{que_se_hizo} movió {len(movidas)} cifras ya registradas; la primera: "
        f"{movidas[0] if movidas else ''}"
    )
    assert not nacidas, (
        f"{que_se_hizo} le estrenó cifras distintas de cero a un renglón nuevo: "
        f"{nacidas[0] if nacidas else ''}"
    )


def queso_id(client, h) -> str:
    productos = client.get(PROD, params={"size": 50}, headers=h).json()["items"]
    return next(p for p in productos if p["clave"] == "queso")["id"]


def _pintar(titulo: str, cifras: dict) -> None:
    """Las cifras gruesas, para que quede en el log qué historia se está midiendo."""
    print(f"\n----- {titulo} -----")
    for campo in ("resumen.total_compras", "resumen.total_ventas",
                  "resumen.total_gastos", "resumen.ganancia_estimada",
                  "resumen.kilos_comprados", "resumen.kilos_a_borona",
                  "resumen.kilos_merma", "resumen.kilos_pendientes",
                  "resumen.por_producto.borona.costo",
                  "resumen.por_producto.borona.kilos",
                  "resumen.por_producto.borona.ingreso",
                  "resumen.existencias.borona.disponible"):
        if campo in cifras:
            print(f"   {campo:38} = {cifras[campo]}")


# ================================================================== las pruebas
def test_ninguna_operacion_del_catalogo_mueve_una_sola_cifra(client, h):
    """LA PRUEBA QUE MANDA: las seis operaciones, una tras otra, sobre la misma
    historia cargada. Después de cada una, la foto completa tiene que ser la misma."""
    construir_historia(client, h)
    antes = foto(client, h)
    _pintar("la historia cargada", antes)
    # Un montaje que no tuviera ajustes ni borona no probaría nada de lo que falló.
    assert antes["resumen.kilos_a_borona"] == D("38.07")
    assert antes["resumen.kilos_merma"] == D("11.11")
    assert antes["resumen.por_producto.borona.kilos_vendidos"] == D("45.50")

    padre = queso_id(client, h)

    # 1. CREAR UN PRODUCTO RAÍZ NUEVO
    r = client.post(PROD, json={"nombre": "Panela de la finca", "unidad": "kg"},
                    headers=h)
    assert r.status_code == 201, r.text
    panela = r.json()
    exigir_que_no_se_movio(antes, foto(client, h), "crear un producto raíz")

    # 2. CREAR UN SUBPRODUCTO DEL QUESO, Y DE PRIMERO EN LA LISTA
    #    Este es EL caso que se llevaba la historia de la borona.
    r = client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                                "subproducto_de_id": padre, "orden": 0}, headers=h)
    assert r.status_code == 201, r.text
    migajon = r.json()
    despues = foto(client, h)
    _pintar("después de crear 'Migajón' con orden = 0", despues)
    exigir_que_no_se_movio(antes, despues, "crear un subproducto con orden 0")

    # 3. REORDENAR LA LISTA (el PUT que también se la llevaba)
    for producto_id, orden in ((migajon["id"], 0), (panela["id"], 1), (padre, 9)):
        r = client.put(f"{PROD}/{producto_id}", json={"orden": orden}, headers=h)
        assert r.status_code == 200, r.text
    despues = foto(client, h)
    _pintar("después de reordenar toda la lista", despues)
    exigir_que_no_se_movio(antes, despues, "reordenar el catálogo")

    # 4. RENOMBRAR un producto CON historia encima
    r = client.put(f"{PROD}/{padre}", json={"nombre": "Queso costeño artesanal"},
                   headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["clave"] == "queso", "la clave es la identidad y no se mueve"
    exigir_que_no_se_movio(antes, foto(client, h), "renombrar el queso")

    # 5. DESACTIVAR un producto con historia (la salida real cuando ya no se maneja)
    r = client.put(f"{PROD}/{padre}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    exigir_que_no_se_movio(antes, foto(client, h), "desactivar el queso")

    # 6. QUITAR del catálogo un producto que nunca se movió
    r = client.delete(f"{PROD}/{panela['id']}", headers=h)
    assert r.status_code in (200, 204), r.text
    exigir_que_no_se_movio(antes, foto(client, h), "quitar un producto sin movimientos")


def test_despues_de_todo_eso_la_borona_se_sigue_pudiendo_vender(client, h):
    """El daño no era solo de informe: con la mercancía movida al producto nuevo, la
    existencia de borona quedaba NEGATIVA y desde ahí sus ventas rebotaban con 422.

    O sea que el dueño se quedaba sin poder trabajar. Se mide vendiendo un kilo.
    """
    construir_historia(client, h)
    padre = queso_id(client, h)
    r = client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                                "subproducto_de_id": padre, "orden": 0}, headers=h)
    assert r.status_code == 201, r.text

    res = client.get(f"{API}/resumen", params=PERIODO, headers=h).json()
    borona = next(e for e in res["existencias"] if e["producto"] == "borona")
    migajon = next(e for e in res["existencias"] if e["producto"] == "migajon")
    print(f"\n   borona disponible = {borona['disponible']}   "
          f"migajón disponible = {migajon['disponible']}")
    # 18,27 + 7,09 + 25,14 gratis + 38,07 convertidos − 45,50 vendidos
    assert D(borona["disponible"]) == D("43.07")
    assert D(migajon["disponible"]) == CERO, (
        "el producto nuevo recibió mercancía que nunca entró"
    )
    # Y las dos cifras de la MISMA respuesta no se pueden contradecir.
    assert D(res["borona_disponible"]) == D(borona["disponible"])

    r = client.post(f"{API}/ventas",
                    json={"fecha": "2026-03-20", "cliente": "Tienda La Esquina",
                          "tipo": "borona", "kilos": "1.00", "precio_kilo": "4000"},
                    headers=h)
    print("   vender 1 kg de borona después de crear el subproducto ->",
          r.status_code)
    assert r.status_code == 201, r.text


def test_el_desglose_sigue_sumando_el_encabezado_despues_de_tocar_el_catalogo(client, h):
    """LA REGLA DE ORO no se puede aflojar por haber tocado el catálogo."""
    construir_historia(client, h)
    padre = queso_id(client, h)
    client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                            "subproducto_de_id": padre, "orden": 0}, headers=h)
    client.post(PROD, json={"nombre": "Panela", "unidad": "kg", "orden": 0}, headers=h)
    client.put(f"{PROD}/{padre}", json={"orden": 8}, headers=h)

    res = client.get(f"{API}/resumen", params=PERIODO, headers=h).json()
    print("\n===== el desglose después de tocar el catálogo =====")
    for f in res["por_producto"]:
        print(f"   {f['producto']:24} {f['etiqueta']:42} {f['unidad']:6} "
              f"costo={f['costo']:>16} ingreso={f['ingreso']:>16} "
              f"ganancia={f['ganancia']:>16}")
    for campo, columna in (("total_compras", "costo"), ("total_ventas", "ingreso"),
                           ("total_gastos", "gastos"),
                           ("ganancia_estimada", "ganancia")):
        suma = sum((D(f[columna]) for f in res["por_producto"]), CERO)
        assert suma == D(res[campo]), (
            f"la columna '{columna}' suma {suma} y '{campo}' dice {res[campo]}"
        )
    # Y ninguna fila puede repetir la clave con la que la pantalla la pinta.
    claves = [f["producto"] for f in res["por_producto"]]
    repetidas = sorted({c for c in claves if claves.count(c) > 1})
    assert not repetidas, f"filas con la clave repetida: {repetidas}"


def test_el_ajuste_guarda_de_que_producto_a_cual(client, h):
    """El hecho que faltaba, ahora guardado: la fila del ajuste nombra sus productos.

    Es lo que hace que ninguna lectura tenga que volver a adivinarlos.
    """
    r = client.post(f"{API}/compras",
                    json={"fecha": "2026-02-03", "productor": "Patricia Rojas",
                          "kilos_brutos": "100.00", "precio_kilo": "20000",
                          "borona_kilos": "20.00"}, headers=h)
    assert r.status_code == 201, r.text
    print("\n   la compra nombró a quien recibe lo gratis:",
          r.json()["subproducto_tipo"])
    assert r.json()["subproducto_tipo"] == "borona"

    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-02-10", "kilos": "30.30",
                          "destino": "borona", "precio_kilo": "3100"}, headers=h)
    assert r.status_code == 201, r.text
    ajuste = r.json()
    print("   el ajuste quedó:", ajuste["producto_origen"], "->",
          ajuste["producto_destino"])
    assert ajuste["producto_origen"] == "queso"
    assert ajuste["producto_destino"] == "borona"

    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-02-11", "kilos": "5.05",
                          "destino": "merma"}, headers=h)
    assert r.status_code == 201, r.text
    merma = r.json()
    print("   la merma quedó:", merma["producto_origen"], "->",
          merma["producto_destino"])
    assert merma["producto_origen"] == "queso"
    assert merma["producto_destino"] is None, (
        "la merma no le entra a nadie: el nulo significa eso y no 'no se sabe'"
    )


def test_un_ajuste_de_un_producto_propio_sale_de_su_inventario_y_no_del_queso(client, h):
    """Ahora que el ajuste nombra su origen, se puede ajustar CUALQUIER producto.

    Y los kilos salen del suyo: el queso no se entera.
    """
    r = client.post(PROD, json={"nombre": "Costeño", "unidad": "kg"}, headers=h)
    assert r.status_code == 201, r.text
    costeno = r.json()
    r = client.post(PROD, json={"nombre": "Recorte", "unidad": "kg",
                                "subproducto_de_id": costeno["id"]}, headers=h)
    assert r.status_code == 201, r.text

    _compra(client, h, fecha="2026-02-03", productor="Patricia Rojas",
            kilos_brutos="100.00", precio_kilo="20000")
    _compra(client, h, fecha="2026-02-03", productor="Patricia Rojas",
            tipo="costeno", kilos_brutos="80.00", precio_kilo="5000")

    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-02-10", "kilos": "12.50",
                          "destino": "borona", "producto_origen": "costeno",
                          "producto_destino": "recorte", "precio_kilo": "1000"},
                    headers=h)
    assert r.status_code == 201, r.text

    res = client.get(f"{API}/resumen", params=PERIODO, headers=h).json()
    disponibles = {e["producto"]: D(e["disponible"]) for e in res["existencias"]}
    print("\n   existencias:", {k: str(v) for k, v in disponibles.items()})
    assert disponibles["queso"] == D("100.00"), (
        "el ajuste del costeño le descontó kilos al QUESO"
    )
    assert disponibles["costeno"] == D("67.50")
    assert disponibles["recorte"] == D("12.50")
    assert disponibles["borona"] == CERO, (
        "los kilos convertidos del costeño se le acreditaron a la borona"
    )
    # Y el costo de esos 12,50 kg sale del pozo del COSTEÑO ($5.000/kg), no del queso.
    fila = next(f for f in res["por_producto"] if f["producto"] == "recorte")
    print(f"   fila del recorte: kilos={fila['kilos']} costo={fila['costo']}")
    assert D(fila["costo"]) == D("62500.00")
    for campo, columna in (("total_compras", "costo"), ("total_ventas", "ingreso")):
        suma = sum((D(f[columna]) for f in res["por_producto"]), CERO)
        assert suma == D(res[campo])


def test_un_producto_desactivado_o_renombrado_no_le_cambia_el_ajuste(client, h):
    """Renombrar o desactivar el producto de un ajuste ya registrado no lo mueve: la
    fila guarda la CLAVE, que es la identidad y no el rótulo."""
    construir_historia(client, h)
    antes = foto(client, h)
    productos = client.get(PROD, params={"size": 50}, headers=h).json()["items"]
    borona = next(p for p in productos if p["clave"] == "borona")

    r = client.put(f"{PROD}/{borona['id']}",
                   json={"nombre": "Borona molida", "estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["clave"] == "borona"
    exigir_que_no_se_movio(antes, foto(client, h),
                           "renombrar y desactivar la borona")


def test_no_se_puede_recolgar_un_producto_que_ya_tiene_ajustes(client, h):
    """Cambiarle el padre a un producto con ajustes encima cruzaría dos grupos de
    costeo: se rechaza igual que ya se rechaza con compras o ventas."""
    construir_historia(client, h)
    productos = client.get(PROD, params={"size": 50}, headers=h).json()["items"]
    borona = next(p for p in productos if p["clave"] == "borona")
    r = client.post(PROD, json={"nombre": "Otro padre", "unidad": "kg"}, headers=h)
    assert r.status_code == 201, r.text
    otro = r.json()

    r = client.put(f"{PROD}/{borona['id']}",
                   json={"subproducto_de_id": otro["id"]}, headers=h)
    print("\n   re-colgar la borona de otro padre ->", r.status_code, r.text[:200])
    assert r.status_code in (400, 422)
    assert "ajuste" in r.text
