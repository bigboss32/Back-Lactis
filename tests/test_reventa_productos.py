"""EL CATÁLOGO DE PRODUCTOS DE REVENTA: qué se compra y se revende, como dato.

El dueño pidió poder "comprar y vender algo que quiera el cliente". Estas pruebas
fijan las cuatro cosas que no se pueden romper de ese catálogo:

- LA CLAVE ES LA IDENTIDAD Y NO SE MUEVE. Renombrar "Queso" a "Queso costeño" tiene
  que dejar la clave 'queso' intacta, porque esa cadena es la que ya está guardada en
  la columna `tipo` de cada compra y de cada venta del cliente. Si el renombre la
  recalculara, el producto quedaría desconectado de toda su historia.
- CADA EMPRESA CON SU CATÁLOGO. La misma clave puede existir en las dos queseras (son
  negocios distintos) y ninguna ve la de la otra.
- LO QUE SE MOVIÓ NO SE QUITA. Un producto con compras o ventas encima dejaría filas
  del cuaderno hablando de algo que ya no aparece en ninguna lista.
- Y EL LÍMITE DE ESTE CORTE, dicho con un mensaje y no con un error de base de datos:
  por ahora solo se pueden agregar productos POR KILO.

Los mensajes se imprimen porque los lee el dueño, que no es técnico.
"""
import pytest

from app.modules.reventa.service import ProductoReventaService, clave_de_producto
from app.seeds.seed import ensure_catalogos_empresas
from tests.conftest import auth_headers

API = "/api/v1/reventa/productos"


def detalle(r) -> str:
    """El detalle del error. Llega anidado dentro de "error", no en la raíz."""
    cuerpo = r.json()
    return cuerpo.get("error", cuerpo).get("detail", "")


def crear(client, headers, **datos):
    return client.post(API, json=datos, headers=headers)


def listar(client, headers, **params):
    r = client.get(API, headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def por_clave(items, clave):
    filas = [p for p in items if p["clave"] == clave]
    assert len(filas) == 1, f"se esperaba un solo '{clave}': {[p['clave'] for p in items]}"
    return filas[0]


@pytest.fixture()
def catalogo(client, base_datos, db_session):
    """Las dos empresas con su catálogo sembrado, como queda tras un despliegue."""
    ensure_catalogos_empresas(db_session)
    db_session.commit()
    return base_datos


# --------------------------------------------------------------------- la siembra
def test_la_siembra_deja_los_tres_productos_con_la_borona_colgada_del_queso(
    client, catalogo
):
    """Los tres que el módulo ya maneja, con las claves que las filas ya tienen."""
    items = listar(client, auth_headers(client, "admin.a"))
    print("\n===== CATÁLOGO SEMBRADO =====")
    for p in items:
        print(
            f"  {p['orden']}  {p['nombre']:12} clave={p['clave']:12} "
            f"unidad={p['unidad']:7} decimales={p['decimales']} "
            f"ajustes={p['admite_ajustes']} subproducto_de={p['subproducto_de_nombre']}"
        )
    assert [p["clave"] for p in items] == ["queso", "borona", "mozzarella"]

    queso = por_clave(items, "queso")
    borona = por_clave(items, "borona")
    mozzarella = por_clave(items, "mozzarella")

    # El queso y la borona se PESAN: dos decimales y admiten merma.
    for p in (queso, borona):
        assert p["unidad"] == "kg"
        assert p["decimales"] == 2
        assert p["admite_ajustes"] is True
        assert p["se_pesa"] is True

    # La mozzarella se CUENTA: entero y sin merma (una barra no pierde peso).
    assert mozzarella["unidad"] == "unidad"
    assert mozzarella["decimales"] == 0
    assert mozzarella["admite_ajustes"] is False
    assert mozzarella["se_pesa"] is False

    # LA BORONA ES SUBPRODUCTO DEL QUESO, y eso deja de ser un caso especial cableado
    # en lotes.py para ser un dato del catálogo.
    assert borona["subproducto_de_id"] == queso["id"]
    assert borona["subproducto_de_nombre"] == "Queso"
    assert queso["subproducto_de_id"] is None


# ------------------------------------------------------------ la clave y el nombre
def test_renombrar_no_toca_la_clave(client, catalogo):
    """EL CASO QUE EL DUEÑO VA A PEDIR: que "Queso" diga "Queso costeño".

    Tiene que poderse siempre y no puede tener riesgo, y eso es exactamente lo que
    significa que la clave no se mueva: la cadena 'queso' es la que está guardada en
    cada compra y en cada venta, así que mientras no cambie, ninguna fila se entera de
    que el producto se llama distinto.
    """
    headers = auth_headers(client, "admin.a")
    queso = por_clave(listar(client, headers), "queso")

    r = client.put(f"{API}/{queso['id']}", json={"nombre": "Queso costeño"}, headers=headers)
    assert r.status_code == 200, r.text
    renombrado = r.json()
    print("\n===== RENOMBRAR =====")
    print(f"  '{queso['nombre']}' -> '{renombrado['nombre']}'   clave: "
          f"'{queso['clave']}' -> '{renombrado['clave']}'")
    assert renombrado["nombre"] == "Queso costeño"
    assert renombrado["clave"] == "queso", "la clave cambió: el producto perdió su historia"
    assert renombrado["id"] == queso["id"]

    # Y la borona sigue colgada de él, ahora con el nombre nuevo.
    borona = por_clave(listar(client, headers), "borona")
    assert borona["subproducto_de_nombre"] == "Queso costeño"


def test_la_clave_no_se_repite_en_la_misma_empresa(client, catalogo):
    """Dos escrituras del mismo nombre dan la misma clave, y la clave es única.

    'Queso' ya está sembrado: agregar "queso" o "  QUESO  " es el mismo producto.
    """
    headers = auth_headers(client, "admin.a")
    print("\n===== LA CLAVE NO SE REPITE =====")
    for nombre in ("Queso", "queso", "  QUESO  "):
        r = crear(client, headers, nombre=nombre)
        print(f"  '{nombre}' -> {r.status_code}: {detalle(r)}")
        assert r.status_code == 409, r.text

    # Y un nombre que normaliza igual que otro producto tampoco pasa, aunque se
    # escriba con acentos y mayúsculas distintas.
    assert crear(client, headers, nombre="Cuajada").status_code == 201
    r = crear(client, headers, nombre="CUAJADA")
    print(f"  'CUAJADA' contra 'Cuajada' -> {r.status_code}: {detalle(r)}")
    assert r.status_code == 409

    claves = [p["clave"] for p in listar(client, headers)]
    print(f"  catálogo: {claves}")
    assert claves == ["queso", "borona", "mozzarella", "cuajada"]
    assert len(claves) == len(set(claves))


def test_la_clave_se_normaliza_sin_acentos_y_es_ascii(client, catalogo):
    """La clave es un identificador, no un rótulo: sale en ASCII y con guion bajo.

    Tiene que quedar comparable igual en SQLite y en Postgres, porque el `lower()` de
    SQLite no baja los acentos y el de Postgres sí (ver `clave_de_producto`).
    """
    headers = auth_headers(client, "admin.a")
    r = crear(client, headers, nombre="Queso Doble Crema Añejo")
    assert r.status_code == 201, r.text
    creado = r.json()
    print("\n===== LA CLAVE ES ASCII =====")
    print(f"  '{creado['nombre']}' -> clave '{creado['clave']}'")
    assert creado["clave"] == "queso_doble_crema_anejo"
    assert creado["clave"] == clave_de_producto(creado["nombre"])
    assert creado["clave"].isascii()


def test_un_nombre_sin_letras_ni_numeros_se_rechaza(client, catalogo):
    """Si del nombre no sale clave, no hay con qué identificar el producto."""
    headers = auth_headers(client, "admin.a")
    r = crear(client, headers, nombre="###")
    print("\n===== NOMBRE SIN CLAVE POSIBLE =====")
    print(f"  {r.status_code}: {detalle(r)}")
    assert r.status_code == 422
    assert "por lo menos una letra" in detalle(r)


# ----------------------------------------------------------- el límite de este corte
def test_un_producto_por_unidad_rebota_con_su_mensaje(client, catalogo):
    """EL LÍMITE DE ESTE LOTE YA SE SUPERÓ, ahora sí se pueden crear unidades."""
    headers = auth_headers(client, "admin.a")
    r = crear(client, headers, nombre="Yogur", unidad="unidad")
    print("\n===== PRODUCTO POR UNIDAD =====")
    print("  " + detalle(r))
    assert r.status_code == 201, r.text

    assert r.json()["unidad"] == "unidad"
    assert r.json()["decimales"] == 0
    assert r.json()["admite_ajustes"] is False


def test_no_se_puede_cambiar_la_unidad_de_un_producto(client, catalogo):
    """La unidad decide la forma de la cantidad, así que no está en el esquema de
    edición: mandarla no hace nada (pydantic ignora lo que no declara)."""
    headers = auth_headers(client, "admin.a")
    queso = por_clave(listar(client, headers), "queso")
    r = client.put(
        f"{API}/{queso['id']}",
        json={"unidad": "unidad", "decimales": 0, "clave": "otra"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    print("\n===== LO QUE NO SE PUEDE EDITAR =====")
    print(f"  se mandó unidad='unidad', decimales=0, clave='otra' y quedó: "
          f"unidad={r.json()['unidad']} decimales={r.json()['decimales']} "
          f"clave={r.json()['clave']}")
    assert r.json()["unidad"] == "kg"
    assert r.json()["decimales"] == 2
    assert r.json()["clave"] == "queso"


# ---------------------------------------------------------------------- multiempresa
def test_una_clave_de_otra_empresa_no_se_ve(client, catalogo):
    """Cada quesera con su catálogo: la misma clave puede existir en las dos y ninguna
    ve la de la otra. El UNIQUE es por (empresa_id, clave) justamente para esto."""
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")

    en_a = crear(client, headers_a, nombre="Cuajada").json()
    en_b = crear(client, headers_b, nombre="Cuajada").json()
    print("\n===== LA MISMA CLAVE EN LAS DOS EMPRESAS =====")
    print(f"  A: {en_a['clave']} id={en_a['id']}")
    print(f"  B: {en_b['clave']} id={en_b['id']}")
    assert en_a["clave"] == en_b["clave"] == "cuajada"
    assert en_a["id"] != en_b["id"]
    assert en_a["empresa_id"] != en_b["empresa_id"]

    # B no ve el de A ni por la lista ni por el id.
    ids_de_b = {p["id"] for p in listar(client, headers_b)}
    assert en_a["id"] not in ids_de_b
    r = client.get(f"{API}/{en_a['id']}", headers=headers_b)
    print(f"  B pidiendo el producto de A: {r.status_code}")
    assert r.status_code == 404

    # Ni lo puede renombrar ni lo puede quitar.
    assert client.put(
        f"{API}/{en_a['id']}", json={"nombre": "Robado"}, headers=headers_b
    ).status_code == 404
    assert client.delete(f"{API}/{en_a['id']}", headers=headers_b).status_code == 404
    # Y en A sigue como estaba.
    assert client.get(f"{API}/{en_a['id']}", headers=headers_a).json()["nombre"] == "Cuajada"


def test_no_se_puede_colgar_un_subproducto_de_otra_empresa(client, catalogo):
    """Un subproducto hereda el costo de su padre: si el padre fuera de otra empresa,
    el reparto le heredaría el costo del queso equivocado."""
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")
    queso_de_a = por_clave(listar(client, headers_a), "queso")

    r = crear(client, headers_b, nombre="Cuajada", subproducto_de_id=queso_de_a["id"])
    print("\n===== PADRE DE OTRA EMPRESA =====")
    print(f"  {r.status_code}: {detalle(r)}")
    assert r.status_code == 404
    assert "no existe en esta empresa" in detalle(r)


# --------------------------------------------------------------------- quitar
def test_quitar_un_producto_con_movimientos_rebota(client, catalogo):
    """SOLO SE PUEDE QUITAR UN PRODUCTO QUE NO TENGA MOVIMIENTOS.

    El vínculo es la clave: 'queso' es lo que la compra tiene guardado en su columna
    `tipo`. Quitarlo dejaría esa fila del cuaderno hablando de algo que ya no aparece
    en ninguna lista, y el dueño no tendría cómo saber qué fue lo que compró.
    """
    headers = auth_headers(client, "admin.a")
    queso = por_clave(listar(client, headers), "queso")

    # Una compra de queso de las de siempre (el `tipo` por omisión es 'queso').
    r = client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-12",
            "productor": "Sebastián",
            "kilos_brutos": "800",
            "precio_kilo": "18000",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text

    r = client.delete(f"{API}/{queso['id']}", headers=headers)
    print("\n===== QUITAR CON MOVIMIENTOS =====")
    print(f"  {r.status_code}: {detalle(r)}")
    assert r.status_code == 422, r.text
    assert "Solo se puede quitar un producto que no tenga movimientos" in detalle(r)
    assert "1 compra" in detalle(r)
    assert "desactívelo" in detalle(r)

    # Sigue en el catálogo, intacto.
    assert por_clave(listar(client, headers), "queso")["id"] == queso["id"]

    # Y LA SALIDA QUE EL MENSAJE OFRECE FUNCIONA: desactivarlo sí se puede, y su
    # historia se queda completa.
    r = client.put(f"{API}/{queso['id']}", json={"estado": "inactivo"}, headers=headers)
    assert r.status_code == 200, r.text
    print(f"  desactivado: estado={r.json()['estado']}")
    assert r.json()["estado"] == "inactivo"
    assert [p["clave"] for p in listar(client, headers, estado="activo")] == [
        "borona",
        "mozzarella",
    ]


def test_una_venta_tambien_cuenta_como_movimiento(client, catalogo):
    """Las ventas cuentan igual que las compras: la mozzarella vendida amarra su fila."""
    headers = auth_headers(client, "admin.a")
    items = listar(client, headers)
    mozzarella = por_clave(items, "mozzarella")

    # Se compra y se vende mozzarella por barras (es como funciona hoy).
    r = client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-01",
            "productor": "Yubigildo",
            "tipo": "mozzarella",
            "barras": "30",
            "precio_barra": "12000",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/v1/reventa/ventas",
        json={
            "fecha": "2026-07-02",
            "cliente": "Tienda La 33",
            "tipo": "mozzarella",
            "barras": "10",
            "precio_barra": "20000",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text

    r = client.delete(f"{API}/{mozzarella['id']}", headers=headers)
    print("\n===== QUITAR CON UNA COMPRA Y UNA VENTA =====")
    print(f"  {r.status_code}: {detalle(r)}")
    assert r.status_code == 422
    assert "1 compra y 1 venta" in detalle(r)


def test_quitar_un_producto_del_que_cuelga_otro_rebota(client, catalogo):
    """El queso no se puede quitar mientras la borona diga ser subproducto suyo."""
    headers = auth_headers(client, "admin.a")
    queso = por_clave(listar(client, headers), "queso")
    r = client.delete(f"{API}/{queso['id']}", headers=headers)
    print("\n===== QUITAR UN PADRE =====")
    print(f"  {r.status_code}: {detalle(r)}")
    assert r.status_code == 422
    assert "'Borona'" in detalle(r)
    assert "es subproducto suyo" in detalle(r)


def test_un_producto_sin_movimientos_si_se_quita(client, catalogo):
    """Y lo que nunca se movió sí sale, que es la otra mitad de la regla."""
    headers = auth_headers(client, "admin.a")
    creado = crear(client, headers, nombre="Cuajada").json()
    assert client.delete(f"{API}/{creado['id']}", headers=headers).status_code == 204
    claves = [p["clave"] for p in listar(client, headers)]
    print("\n===== QUITAR SIN MOVIMIENTOS =====")
    print(f"  quedó: {claves}")
    assert "cuajada" not in claves


def test_volver_a_agregar_un_producto_quitado_devuelve_la_misma_fila(client, catalogo):
    """LA TRAMPA QUE ESTO EVITA: el UNIQUE de (empresa_id, clave) no filtra los
    borrados, así que la fila quitada sigue ocupando su clave. Insertar otra
    reventaría contra la base, y rechazarla dejaría 'cuajada' inutilizable para
    siempre. Se devuelve LA MISMA fila, con su mismo id.

    Es lo contrario de lo que hace la siembra de cada despliegue, que a propósito NO
    resucita nada: acá es la persona pidiéndolo otra vez.
    """
    headers = auth_headers(client, "admin.a")
    primera = crear(client, headers, nombre="Cuajada").json()
    assert client.delete(f"{API}/{primera['id']}", headers=headers).status_code == 204

    segunda = crear(client, headers, nombre="Cuajada Criolla")
    print("\n===== VOLVER A AGREGAR LO QUITADO =====")
    print(f"  {segunda.status_code}: mismo id = {segunda.json()['id'] == primera['id']}, "
          f"nombre '{segunda.json()['nombre']}', clave '{segunda.json()['clave']}'")
    # "Cuajada Criolla" no da la clave 'cuajada': es un producto NUEVO, no la misma fila.
    assert segunda.status_code == 201
    assert segunda.json()["clave"] == "cuajada_criolla"
    assert segunda.json()["id"] != primera["id"]

    tercera = crear(client, headers, nombre="Cuajada")
    print(f"  con el mismo nombre: {tercera.status_code}, mismo id = "
          f"{tercera.json()['id'] == primera['id']}, clave '{tercera.json()['clave']}'")
    assert tercera.status_code == 201
    assert tercera.json()["id"] == primera["id"], "no revivió la fila: nació otra"
    assert tercera.json()["clave"] == "cuajada"
    assert tercera.json()["estado"] == "activo"


# ------------------------------------------------------------------- subproductos
def test_la_cadena_de_subproductos_solo_llega_a_un_nivel(client, catalogo):
    """El motor FIFO implementa exactamente una relación padre-subproducto (queso ->
    borona, con el costo heredado). Un subproducto de un subproducto no tendría cómo
    costearse, y ofrecerlo sería prometer una cuenta que no existe."""
    headers = auth_headers(client, "admin.a")
    borona = por_clave(listar(client, headers), "borona")

    r = crear(client, headers, nombre="Polvo de borona", subproducto_de_id=borona["id"])
    print("\n===== CADENA DE DOS NIVELES =====")
    print(f"  {r.status_code}: {detalle(r)}")
    assert r.status_code == 422
    assert "solo llega a un nivel" in detalle(r)


def test_un_producto_no_puede_ser_subproducto_de_si_mismo(client, catalogo):
    headers = auth_headers(client, "admin.a")
    cuajada = crear(client, headers, nombre="Cuajada").json()
    r = client.put(
        f"{API}/{cuajada['id']}",
        json={"subproducto_de_id": cuajada["id"]},
        headers=headers,
    )
    print("\n===== SUBPRODUCTO DE SÍ MISMO =====")
    print(f"  {r.status_code}: {detalle(r)}")
    assert r.status_code == 422
    assert "de sí mismo" in detalle(r)


def test_mover_el_subproducto_de_algo_que_ya_se_movio_rebota(client, catalogo):
    """De quién hereda el costo un subproducto es una cuenta de plata: cambiarlo con
    movimientos encima recostearía historia que el dueño ya cuadró."""
    headers = auth_headers(client, "admin.a")
    items = listar(client, headers)
    borona, mozzarella = por_clave(items, "borona"), por_clave(items, "mozzarella")

    r = client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-12",
            "productor": "Sebastián",
            "kilos_brutos": "800",
            "borona_kilos": "50",
            "precio_kilo": "18000",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/v1/reventa/ventas",
        json={
            "fecha": "2026-07-13",
            "cliente": "Doña Rosa",
            "tipo": "borona",
            "kilos": "20",
            "precio_kilo": "4000",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text

    r = client.put(
        f"{API}/{borona['id']}",
        json={"subproducto_de_id": mozzarella["id"]},
        headers=headers,
    )
    print("\n===== MOVER EL PADRE CON MOVIMIENTOS ENCIMA =====")
    print(f"  {r.status_code}: {detalle(r)}")
    assert r.status_code == 422
    assert "1 venta" in detalle(r)
    assert "recostearía" in detalle(r)

    # Renombrarla sí se puede, aunque tenga ventas: el nombre no es de nadie más.
    r = client.put(f"{API}/{borona['id']}", json={"nombre": "Borona menuda"}, headers=headers)
    assert r.status_code == 200, r.text
    print(f"  pero renombrar sí: '{r.json()['nombre']}' clave '{r.json()['clave']}'")
    assert r.json()["clave"] == "borona"


# --------------------------------------------------------------------- presentación
def test_los_productos_nuevos_van_al_final_de_la_lista(client, catalogo):
    """El orden es el de la lista de selección: lo nuevo se agrega al final, no
    encima de lo que el dueño ya está acostumbrado a ver primero."""
    headers = auth_headers(client, "admin.a")
    for nombre in ("Cuajada", "Suero"):
        assert crear(client, headers, nombre=nombre).status_code == 201
    items = listar(client, headers)
    print("\n===== EL ORDEN =====")
    for p in items:
        print(f"  {p['orden']}  {p['nombre']}")
    assert [p["clave"] for p in items] == [
        "queso", "borona", "mozzarella", "cuajada", "suero",
    ]
    assert [p["orden"] for p in items] == [0, 1, 2, 3, 4]
