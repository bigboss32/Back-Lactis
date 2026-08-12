"""LA MATRIZ: cada operación del catálogo, una por prueba, sobre la MISMA historia.

QUÉ MIDE. El catálogo es lo que el dueño administra: agrega un producto, lo sube o lo
baja en la lista, lo renombra, lo desactiva, lo cuelga de otro, lo quita. Nada de eso es
un movimiento de plata, así que ninguna de esas operaciones puede cambiar una sola cifra
de lo que ya está anotado.

CÓMO. Cada caso arranca con la base limpia, carga la historia más completa que sabemos
armar por la API —dos grupos de costeo, un subproducto COMPRADO directamente, kilos que
llegan gratis a los dos subproductos, ajustes a subproducto y a merma, un producto por
unidades y ventas de los cinco—, toma la foto de TODO lo que el dueño mira, hace UNA
cosa en el catálogo y vuelve a medir, cifra por cifra.

Y CADA OPERACIÓN SE MIDE DOS VECES, porque son dos preguntas distintas y las dos
importan:

  1. ¿MOVIÓ ALGO? Si la operación se aceptó, ninguna cifra pudo cambiar; si se rechazó,
     tampoco pudo haber movido nada por el camino.
  2. ¿SE PUEDE SEGUIR TRABAJANDO? Después de la operación —se haya aceptado o no— el
     dueño tiene que poder registrar la compra de todos los días, el ajuste de todos los
     días y una venta. Un rechazo del catálogo que dejara el sistema sin poder anotar la
     leche sería peor que el defecto que evita.
"""
import pytest

from tests.ayudas_reventa import (
    PROD, crear_producto, exigir_que_se_pueda_trabajar, exigir_quieto, foto,
    historia, historia_gorda, productos, regla_de_oro, resumen, diferencias,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


@pytest.fixture()
def hb(client, base_datos):
    return auth_headers(client, "admin.b")


# ==========================================================================
# LAS OPERACIONES
# ==========================================================================
def _aplicar(client, h, cat, operacion):
    """Hace UNA cosa en el catálogo y devuelve la respuesta del último llamado."""
    queso, borona, mozza = cat["queso"], cat["borona"], cat["mozzarella"]
    costeno, recorte = cat["costeno"], cat["recorte"]

    if operacion == "crear_raiz_final":
        return client.post(PROD, json={"nombre": "Panela", "unidad": "kg"}, headers=h)
    if operacion == "crear_raiz_orden_0":
        return client.post(PROD, json={"nombre": "Panela", "unidad": "kg", "orden": 0},
                           headers=h)
    if operacion == "crear_sub_queso_orden_0":
        return client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                                       "subproducto_de_id": queso["id"], "orden": 0},
                           headers=h)
    if operacion == "crear_sub_costeno_orden_0":
        return client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                                       "subproducto_de_id": costeno["id"], "orden": 0},
                           headers=h)
    if operacion == "crear_sub_orden_repetido":
        return client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                                       "subproducto_de_id": queso["id"],
                                       "orden": queso["orden"]}, headers=h)
    if operacion == "reordenar_todo_al_reves":
        for pid, orden in ((recorte["id"], 0), (costeno["id"], 1), (mozza["id"], 2),
                           (borona["id"], 3), (queso["id"], 4)):
            r = client.put(f"{PROD}/{pid}", json={"orden": orden}, headers=h)
            assert r.status_code == 200, r.text
        return r
    if operacion == "reordenar_todos_al_mismo":
        for pid in (queso["id"], borona["id"], mozza["id"], costeno["id"],
                    recorte["id"]):
            r = client.put(f"{PROD}/{pid}", json={"orden": 0}, headers=h)
            assert r.status_code == 200, r.text
        return r
    if operacion == "renombrar_queso_a_merma":
        return client.put(f"{PROD}/{queso['id']}", json={"nombre": "Merma"}, headers=h)
    if operacion == "renombrar_borona_a_pendiente":
        return client.put(f"{PROD}/{borona['id']}", json={"nombre": "Pendiente"},
                          headers=h)
    if operacion == "renombrar_costeno_a_anterior":
        return client.put(f"{PROD}/{costeno['id']}", json={"nombre": "Anterior"},
                          headers=h)
    if operacion == "renombrar_recorte_a_sin_producto":
        return client.put(f"{PROD}/{recorte['id']}", json={"nombre": "Sin producto"},
                          headers=h)
    if operacion == "desactivar_queso":
        return client.put(f"{PROD}/{queso['id']}", json={"estado": "inactivo"},
                          headers=h)
    if operacion == "desactivar_borona":
        return client.put(f"{PROD}/{borona['id']}", json={"estado": "inactivo"},
                          headers=h)
    if operacion == "desactivar_recorte":
        return client.put(f"{PROD}/{recorte['id']}", json={"estado": "inactivo"},
                          headers=h)
    if operacion == "desactivar_y_reactivar_todo":
        for pid in (queso["id"], borona["id"], mozza["id"], costeno["id"],
                    recorte["id"]):
            client.put(f"{PROD}/{pid}", json={"estado": "inactivo"}, headers=h)
        for pid in (queso["id"], borona["id"], mozza["id"], costeno["id"],
                    recorte["id"]):
            r = client.put(f"{PROD}/{pid}", json={"estado": "activo"}, headers=h)
        return r
    if operacion == "descolgar_borona":
        return client.put(f"{PROD}/{borona['id']}", json={"subproducto_de_id": None},
                          headers=h)
    if operacion == "descolgar_recorte":
        return client.put(f"{PROD}/{recorte['id']}", json={"subproducto_de_id": None},
                          headers=h)
    if operacion == "recolgar_borona_del_costeno":
        return client.put(f"{PROD}/{borona['id']}",
                          json={"subproducto_de_id": costeno["id"]}, headers=h)
    if operacion == "recolgar_recorte_del_queso":
        return client.put(f"{PROD}/{recorte['id']}",
                          json={"subproducto_de_id": queso["id"]}, headers=h)
    if operacion == "colgar_mozzarella_del_queso":
        return client.put(f"{PROD}/{mozza['id']}",
                          json={"subproducto_de_id": queso["id"]}, headers=h)
    if operacion == "borrar_borona":
        return client.delete(f"{PROD}/{borona['id']}", headers=h)
    if operacion == "borrar_recorte":
        return client.delete(f"{PROD}/{recorte['id']}", headers=h)
    if operacion == "borrar_queso":
        return client.delete(f"{PROD}/{queso['id']}", headers=h)
    raise AssertionError(f"operación desconocida: {operacion}")


OPERACIONES = [
    "crear_raiz_final", "crear_raiz_orden_0", "crear_sub_queso_orden_0",
    "crear_sub_costeno_orden_0", "crear_sub_orden_repetido",
    "reordenar_todo_al_reves", "reordenar_todos_al_mismo",
    "renombrar_queso_a_merma", "renombrar_borona_a_pendiente",
    "renombrar_costeno_a_anterior", "renombrar_recorte_a_sin_producto",
    "desactivar_queso", "desactivar_borona", "desactivar_recorte",
    "desactivar_y_reactivar_todo",
    "descolgar_borona", "descolgar_recorte",
    "recolgar_borona_del_costeno", "recolgar_recorte_del_queso",
    "colgar_mozzarella_del_queso",
    "borrar_borona", "borrar_recorte", "borrar_queso",
]

# LAS QUE TIENEN QUE SER RECHAZADAS, y por qué cada una. Van escritas aquí y no
# deducidas de la respuesta a propósito: si mañana una de estas empezara a pasar, esta
# lista lo dice en vez de dejar que la prueba se acomode a lo que el código haga.
#
# Todas comparten la misma razón de fondo: mueven de grupo de costeo, o quitan del
# catálogo, un producto que YA TIENE MERCANCÍA REGISTRADA a nombre suyo —comprada,
# vendida, ajustada, o llegada gratis encima de la compra de otro—.
RECHAZADAS = {
    "descolgar_borona", "descolgar_recorte",
    "recolgar_borona_del_costeno", "recolgar_recorte_del_queso",
    "colgar_mozzarella_del_queso",
    "borrar_borona", "borrar_recorte", "borrar_queso",
}


@pytest.mark.parametrize("operacion", OPERACIONES)
def test_una_operacion_del_catalogo_no_mueve_plata(client, h, operacion):
    historia_gorda(client, h)
    antes = foto(client, h)
    regla_de_oro(resumen(client, h), "la historia gorda")
    cat = productos(client, h)

    r = _aplicar(client, h, cat, operacion)
    print(f"\n   [{operacion}] -> {r.status_code} {r.text[:220]}")
    if operacion in RECHAZADAS:
        assert r.status_code == 422, (
            f"'{operacion}' toca un producto con mercancía encima y pasó"
        )
        # Un rechazo tampoco puede haber movido nada por el camino.
        exigir_quieto(antes, foto(client, h), f"{operacion} (rechazada)")
        return

    assert r.status_code in (200, 201, 204), r.text
    exigir_quieto(antes, foto(client, h), operacion)
    regla_de_oro(resumen(client, h), operacion)


@pytest.mark.parametrize("operacion", OPERACIONES)
def test_despues_de_la_operacion_se_sigue_pudiendo_trabajar(client, h, operacion):
    """Lo de todos los días, después de cada cosa que se le puede hacer al catálogo.

    Se mide SIEMPRE, se haya aceptado la operación o no: un rechazo también deja un
    estado, y ese estado tiene que dejar trabajar. Antes esta prueba se saltaba los
    casos rechazados, y saltarse un caso es no medirlo.
    """
    historia_gorda(client, h)
    cat = productos(client, h)
    r = _aplicar(client, h, cat, operacion)
    print(f"\n   [{operacion}] -> {r.status_code}")
    exigir_que_se_pueda_trabajar(client, h, operacion)
    regla_de_oro(resumen(client, h), f"{operacion} + lo de todos los días")


# ==========================================================================
# LA CADENA COMPLETA: TODAS LAS OPERACIONES SEGUIDAS SOBRE LA MISMA BASE
# ==========================================================================
def test_todas_las_operaciones_encadenadas_no_mueven_plata(client, h):
    """Una tras otra, midiendo la foto entera después de cada una.

    No es lo mismo que la matriz de arriba: aquí cada operación cae sobre el catálogo
    que dejó la anterior, que es lo que de verdad pasa con el tiempo.
    """
    historia(client, h)
    antes = foto(client, h)
    regla_de_oro(resumen(client, h), "la historia cargada")
    cat = productos(client, h)
    queso, borona, mozza = cat["queso"], cat["borona"], cat["mozzarella"]
    creados: dict[str, dict] = {}

    def crear(titulo, payload, espera=201):
        r = client.post(PROD, json=payload, headers=h)
        print(f"\n   crear {titulo} -> {r.status_code} {r.text[:220]}")
        assert r.status_code == espera, r.text
        if r.status_code == 201:
            creados[titulo] = r.json()
            exigir_quieto(antes, foto(client, h), f"crear {titulo}")
            regla_de_oro(resumen(client, h), f"crear {titulo}")
        return r

    def editar(titulo, producto_id, payload, espera=200):
        r = client.put(f"{PROD}/{producto_id}", json=payload, headers=h)
        print(f"\n   editar {titulo} -> {r.status_code} {r.text[:220]}")
        assert r.status_code == espera, r.text
        if r.status_code == 200:
            exigir_quieto(antes, foto(client, h), f"editar {titulo}")
            regla_de_oro(resumen(client, h), f"editar {titulo}")
        return r

    # --- crear en todas las posiciones de orden
    crear("raíz al final", {"nombre": "Panela de la finca", "unidad": "kg"})
    crear("raíz orden 0", {"nombre": "Costeño", "unidad": "kg", "orden": 0})
    crear("subproducto orden 0",
          {"nombre": "Migajón", "unidad": "kg",
           "subproducto_de_id": queso["id"], "orden": 0})
    crear("subproducto orden repetido",
          {"nombre": "Recorte", "unidad": "kg",
           "subproducto_de_id": queso["id"], "orden": 0})
    # orden negativo: el esquema lo tiene ge=0, así que tiene que rebotar con 422
    r = client.post(PROD, json={"nombre": "Suero", "unidad": "kg", "orden": -5},
                    headers=h)
    print("\n   crear con orden negativo ->", r.status_code)
    assert r.status_code == 422, r.text

    # --- reordenar de todas las formas, incluidos empates
    for pid in (queso["id"], borona["id"], mozza["id"],
                creados["raíz orden 0"]["id"]):
        editar(f"orden 0 empatado ({pid[:8]})", pid, {"orden": 0})
    for pid, orden in ((queso["id"], 9), (borona["id"], 8), (mozza["id"], 7)):
        editar(f"orden {orden}", pid, {"orden": orden})

    # --- renombrar, incluso a los rótulos calculados
    for nombre in ("Merma", "Pendiente", "Anterior", "Sin producto",
                   "Producto sin identificar", "Queso costeño artesanal"):
        r = editar(f"renombrar el queso a '{nombre}'", queso["id"], {"nombre": nombre})
        assert r.json()["clave"] == "queso", "la clave es la identidad"

    # --- desactivar y reactivar un producto con movimientos
    editar("desactivar el queso", queso["id"], {"estado": "inactivo"})
    editar("reactivar el queso", queso["id"], {"estado": "activo"})
    editar("desactivar la borona", borona["id"], {"estado": "inactivo"})
    editar("reactivar la borona", borona["id"], {"estado": "activo"})

    # --- marcar y desmarcar el "llega gratis con otro" en uno sin movimientos
    panela = creados["raíz al final"]
    editar("colgar la panela del queso", panela["id"],
           {"subproducto_de_id": queso["id"]})
    editar("descolgar la panela", panela["id"], {"subproducto_de_id": None})

    # --- la cadena prohibida de dos niveles
    migajon = creados["subproducto orden 0"]
    r = client.post(PROD, json={"nombre": "Migajita", "unidad": "kg",
                                "subproducto_de_id": migajon["id"]}, headers=h)
    print("\n   subproducto de un subproducto ->", r.status_code, r.text[:220])
    assert r.status_code in (400, 409, 422), r.text
    exigir_quieto(antes, foto(client, h), "intentar la cadena de dos niveles")

    # --- borrar uno sin movimientos, y NO poder borrar uno con mercancía
    costeno = creados["raíz orden 0"]
    r = client.delete(f"{PROD}/{costeno['id']}", headers=h)
    print("\n   borrar un producto sin movimientos ->", r.status_code)
    assert r.status_code == 204, r.text
    sin_el_costeno = {k: v for k, v in antes.items() if "costeno" not in k}
    exigir_quieto(sin_el_costeno, foto(client, h), "borrar un producto sin movimientos")
    regla_de_oro(resumen(client, h), "después de borrar")
    r = client.delete(f"{PROD}/{borona['id']}", headers=h)
    print("   borrar la borona (con mercancía) ->", r.status_code, r.text[:220])
    assert r.status_code == 422, r.text

    # --- ponerle a un producto el nombre del que se borró
    editar("ponerle a la panela el nombre del borrado", panela["id"],
           {"nombre": "Costeño"})

    # --- y volver a crear el borrado (revive la fila dormida)
    r = client.post(PROD, json={"nombre": "Costeño", "unidad": "kg"}, headers=h)
    print("\n   recrear el borrado con el nombre ya ocupado ->",
          r.status_code, r.text[:220])
    exigir_quieto(antes, foto(client, h), "recrear el producto borrado")
    regla_de_oro(resumen(client, h), "después de recrear")

    # --- y al final de todo, el dueño tiene que poder seguir trabajando
    exigir_que_se_pueda_trabajar(client, h, "toda la cadena de operaciones")


# ==========================================================================
# EN UNA QUESERA, MIDIENDO LA OTRA
# ==========================================================================
def test_tocar_el_catalogo_de_una_quesera_no_mueve_la_otra(client, h, hb):
    historia(client, h)
    historia(client, hb)
    antes_b = foto(client, hb)
    cat_a = productos(client, h)

    client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                            "subproducto_de_id": cat_a["queso"]["id"],
                            "orden": 0}, headers=h)
    client.put(f"{PROD}/{cat_a['queso']['id']}",
               json={"nombre": "Merma", "orden": 9}, headers=h)
    client.put(f"{PROD}/{cat_a['borona']['id']}",
               json={"estado": "inactivo"}, headers=h)
    exigir_quieto(antes_b, foto(client, hb), "tocar el catálogo de la quesera A")
    regla_de_oro(resumen(client, hb), "quesera B")

    # Y al revés: lo de B no mueve lo de A.
    antes_a = foto(client, h)
    cat_b = productos(client, hb)
    client.post(PROD, json={"nombre": "Panela", "unidad": "kg", "orden": 0},
                headers=hb)
    client.put(f"{PROD}/{cat_b['queso']['id']}", json={"orden": 7}, headers=hb)
    exigir_quieto(antes_a, foto(client, h), "tocar el catálogo de la quesera B")


# ==========================================================================
# DETERMINISMO: dos corridas iguales
# ==========================================================================
def test_el_resumen_es_identico_llamado_dos_veces(client, h):
    historia(client, h)
    cat = productos(client, h)
    crear_producto(client, h, nombre="Migajón", unidad="kg",
                   subproducto_de_id=cat["queso"]["id"], orden=0)
    crear_producto(client, h, nombre="Costeño", unidad="kg", orden=0)
    primera = foto(client, h)
    segunda = foto(client, h)
    movidas, nacidas = diferencias(primera, segunda)
    for ruta, viejo, nuevo in movidas[:40]:
        print(f"   NO DETERMINISTA {ruta}: {viejo} -> {nuevo}")
    assert not movidas and not nacidas, "dos corridas seguidas dieron distinto"


# ==========================================================================
# LOS RÓTULOS CALCULADOS Y EL NOMBRE DEL DUEÑO
# ==========================================================================
@pytest.mark.parametrize("nombre", ["Merma", "Pendiente", "Anterior", "Sin producto",
                                    "Queso merma", "Queso pendiente",
                                    "Mozzarella pendiente", "Producto sin identificar"])
def test_un_producto_con_nombre_de_rotulo_calculado_no_pisa_su_fila(client, h, nombre):
    """Las filas calculadas del desglose ('Merma', 'Aún en inventario') tienen sus
    claves propias: un producto que se llame igual no les puede caer encima."""
    historia(client, h)
    antes = foto(client, h)
    r = client.post(PROD, json={"nombre": nombre, "unidad": "kg"}, headers=h)
    print(f"\n   crear '{nombre}' -> {r.status_code} clave="
          f"{r.json().get('clave') if r.status_code == 201 else r.text[:160]}")
    assert r.status_code == 201, r.text
    exigir_quieto(antes, foto(client, h), f"crear un producto llamado '{nombre}'")
    res = resumen(client, h)
    regla_de_oro(res, f"con un producto llamado '{nombre}'")
    print("   filas:", {f["producto"]: f["etiqueta"] for f in res["por_producto"]})


def test_renombrar_un_producto_al_rotulo_de_una_fila_calculada(client, h):
    """Renombrar NO recalcula la clave; el rótulo del desglose sí cambia.

    Se mide que las dos filas puedan convivir y que la plata no se mueva.
    """
    historia(client, h)
    antes = foto(client, h)
    cat = productos(client, h)
    for nombre in ("Merma", "Pendiente", "Anterior", "Sin producto"):
        r = client.put(f"{PROD}/{cat['borona']['id']}", json={"nombre": nombre},
                       headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["clave"] == "borona"
        exigir_quieto(antes, foto(client, h), f"renombrar la borona a '{nombre}'")
        res = resumen(client, h)
        regla_de_oro(res, f"borona renombrada '{nombre}'")
        print(f"\n   con la borona llamada '{nombre}':")
        for f in res["por_producto"]:
            print(f"      {f['producto']:26} {f['etiqueta']}")
