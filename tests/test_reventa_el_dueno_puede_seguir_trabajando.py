"""DESPUÉS DE TOCARLE EL CATÁLOGO, ¿PUEDE EL DUEÑO SEGUIR TRABAJANDO?

No mide plata: mide si el dueño se queda SIN PODER ANOTAR lo de todos los días
(la compra con la borona encima, el ajuste y la venta) después de cualquier cosa
que se le haga a la lista de productos.

QUÉ DEFECTO FIJA, MEDIDO CONTRA EL CÓDIGO DESPLEGADO. El ajuste del día rebotaba
con 422 donde antes respondía 201, en siete combinaciones del catálogo: una
empresa recién creada (crear la empresa NO siembra la lista; eso solo pasa en el
despliegue siguiente), el catálogo sin borona, la borona descolgada de su padre.
Y quitando la borona con dos subproductos en la lista se caía el día completo:
compra, ajuste y venta. Nombrar el origen y el destino a mano tampoco lo salvaba.

LA REGLA QUE ESTE ARCHIVO EXIGE: REGISTRAR SIEMPRE GANA. Un problema de la LISTA
DE PRODUCTOS no puede dejar al dueño sin poder anotar lo que ya hizo — la lista se
arregla después; lo que él compró hoy ya es plata que se movió. Los rechazos que
quedan son los que de verdad necesitan que una persona decida y ninguno es un
problema de la lista (no hay esos kilos en la bodega, o le está metiendo kilos a
un producto que se cuenta por unidades); se exigen en
tests/test_reventa_lo_que_llega_gratis_es_mercancia.py.

Cada caso corre sobre una base limpia. Se prueba el día a día DE DOS FORMAS:
  · como lo manda la pantalla HOY (sin nombrar productos), y
  · nombrando los productos a mano (que es la salida que el mensaje de error
    sugiere). Si ni nombrándolos se puede, no hay salida.

Las operaciones que el catálogo RECHAZA se saltan: ahí no hay nada que medir,
porque el dueño se quedó con la lista que ya tenía y trabajando como siempre.
"""
import pytest

from tests.ayudas_reventa import (
    API, PROD, compra, crear_producto, productos, regla_de_oro, resumen, venta,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


# ------------------------------------------------------------------ el día a día
def dia_a_dia(client, h, *, nombrando: bool) -> dict:
    """Lo de todos los días. Devuelve {qué: (código, cuerpo)}."""
    sufijo = "n" if nombrando else "s"
    out = {}
    payload_compra = {
        "fecha": "2026-06-15", "productor": f"Productor {sufijo}",
        "kilos_brutos": "100.00", "precio_kilo": "14000", "borona_kilos": "5.00",
    }
    if nombrando:
        payload_compra["tipo"] = "queso"
        payload_compra["subproducto_tipo"] = "borona"
    r = client.post(f"{API}/compras", json=payload_compra, headers=h)
    out["compra con borona encima"] = (r.status_code, r.text[:160])

    payload_ajuste = {"fecha": "2026-06-16", "kilos": "3.00", "destino": "borona",
                      "precio_kilo": "3000"}
    if nombrando:
        payload_ajuste["producto_origen"] = "queso"
        payload_ajuste["producto_destino"] = "borona"
    r = client.post(f"{API}/conversiones", json=payload_ajuste, headers=h)
    out["ajuste de queso a borona"] = (r.status_code, r.text[:160])

    r = client.post(f"{API}/ventas",
                    json={"fecha": "2026-06-17", "cliente": "Tienda La Esquina",
                          "tipo": "borona", "kilos": "1.00", "precio_kilo": "4000"},
                    headers=h)
    out["venta de borona"] = (r.status_code, r.text[:160])
    return out


def informar(titulo, resultados) -> list[str]:
    print(f"\n   ---- {titulo} ----")
    rotos = []
    for que, (codigo, cuerpo) in resultados.items():
        print(f"      {que:28} -> {codigo} {cuerpo if codigo >= 400 else ''}")
        if codigo >= 400:
            rotos.append(f"{que} ({codigo})")
    return rotos


# --------------------------------------------------------- operaciones del catálogo
def _put(client, h, prod_id, **campos):
    r = client.put(f"{PROD}/{prod_id}", json=campos, headers=h)
    return r


def op_borrar_borona(client, h, p):
    return client.delete(f"{PROD}/{p['borona']['id']}", headers=h)


def op_borrar_mozzarella(client, h, p):
    return client.delete(f"{PROD}/{p['mozzarella']['id']}", headers=h)


def op_borrar_queso(client, h, p):
    _put(client, h, p["borona"]["id"], subproducto_de_id=None)
    return client.delete(f"{PROD}/{p['queso']['id']}", headers=h)


def op_borrar_todo(client, h, p):
    _put(client, h, p["borona"]["id"], subproducto_de_id=None)
    for clave in ("borona", "mozzarella", "queso"):
        r = client.delete(f"{PROD}/{p[clave]['id']}", headers=h)
        if r.status_code >= 400:
            return r
    return r


def op_descolgar_borona(client, h, p):
    return _put(client, h, p["borona"]["id"], subproducto_de_id=None)


def op_recolgar_borona_a_mozzarella(client, h, p):
    return _put(client, h, p["borona"]["id"],
                subproducto_de_id=p["mozzarella"]["id"])


def op_desactivar_borona(client, h, p):
    return _put(client, h, p["borona"]["id"], estado="inactivo")


def op_desactivar_queso(client, h, p):
    return _put(client, h, p["queso"]["id"], estado="inactivo")


def op_renombrar_borona(client, h, p):
    return _put(client, h, p["borona"]["id"], nombre="Migajas de la casa")


def op_renombrar_queso_merma(client, h, p):
    return _put(client, h, p["queso"]["id"], nombre="Merma")


def op_reordenar_al_reves(client, h, p):
    for i, clave in enumerate(("mozzarella", "borona", "queso")):
        r = _put(client, h, p[clave]["id"], orden=i)
        if r.status_code >= 400:
            return r
    return r


def op_todos_el_mismo_orden(client, h, p):
    for clave in ("queso", "borona", "mozzarella"):
        r = _put(client, h, p[clave]["id"], orden=0)
        if r.status_code >= 400:
            return r
    return r


def op_segundo_subproducto(client, h, p):
    crear_producto(client, h, nombre="Suero", unidad="kg",
                   subproducto_de_id=p["queso"]["id"])
    return None


def op_dos_subproductos_sin_borona(client, h, p):
    """Le falta la borona al catálogo y el queso tiene otros dos subproductos."""
    r = client.delete(f"{PROD}/{p['borona']['id']}", headers=h)
    if r.status_code >= 400:
        return r
    crear_producto(client, h, nombre="Suero", unidad="kg",
                   subproducto_de_id=p["queso"]["id"])
    crear_producto(client, h, nombre="Cuajada suelta", unidad="kg",
                   subproducto_de_id=p["queso"]["id"])
    return None


def op_falta_la_borona(client, h, p):
    return client.delete(f"{PROD}/{p['borona']['id']}", headers=h)


def op_producto_nuevo_en_orden_cero(client, h, p):
    crear_producto(client, h, nombre="Panela", unidad="kg", orden=0)
    return None


def op_revivir_borona_sin_padre(client, h, p):
    r = client.delete(f"{PROD}/{p['borona']['id']}", headers=h)
    if r.status_code >= 400:
        return r
    crear_producto(client, h, nombre="Borona")
    return None


def op_nada(client, h, p):
    return None


OPERACIONES = [
    ("nada (control)", op_nada),
    ("borrar la borona", op_borrar_borona),
    ("borrar la mozzarella", op_borrar_mozzarella),
    ("descolgar la borona + borrar el queso", op_borrar_queso),
    ("borrar TODO el catálogo", op_borrar_todo),
    ("descolgar la borona", op_descolgar_borona),
    ("colgar la borona de la mozzarella", op_recolgar_borona_a_mozzarella),
    ("desactivar la borona", op_desactivar_borona),
    ("desactivar el queso", op_desactivar_queso),
    ("renombrar la borona", op_renombrar_borona),
    ("renombrar el queso a 'Merma'", op_renombrar_queso_merma),
    ("reordenar al revés", op_reordenar_al_reves),
    ("todos con el mismo orden", op_todos_el_mismo_orden),
    ("agregar un segundo subproducto", op_segundo_subproducto),
    ("quitar la borona y dejar dos subproductos", op_dos_subproductos_sin_borona),
    ("agregar un producto en orden 0", op_producto_nuevo_en_orden_cero),
    ("quitar la borona y volverla a agregar sin padre", op_revivir_borona_sin_padre),
]


@pytest.mark.parametrize("titulo,operacion", OPERACIONES, ids=[o[0] for o in OPERACIONES])
def test_el_dueno_puede_trabajar_despues_de(client, h, titulo, operacion):
    p = productos(client, h)
    r = operacion(client, h, p)
    if r is not None and r.status_code >= 400:
        pytest.skip(f"el catálogo no dejó hacer '{titulo}': {r.status_code} {r.text[:120]}")

    print(f"\n===== después de [{titulo}] =====")
    rotos_sin = informar("como lo manda la pantalla hoy", dia_a_dia(client, h, nombrando=False))
    rotos_con = informar("nombrando los productos a mano", dia_a_dia(client, h, nombrando=True))
    assert not rotos_sin and not rotos_con, (
        f"después de '{titulo}' el dueño NO puede: sin nombrar={rotos_sin} "
        f"nombrando={rotos_con}"
    )
    # Y LO QUE QUEDÓ ANOTADO TIENE QUE CUADRAR: el desglose suma exacto su cifra
    # grande y ninguna clave sale repetida. Aceptar siempre no puede significar
    # aceptar de cualquier forma.
    regla_de_oro(resumen(client, h), titulo)


@pytest.mark.parametrize("titulo,operacion", OPERACIONES, ids=[o[0] for o in OPERACIONES])
def test_el_dueno_puede_trabajar_con_historia_encima(client, h, titulo, operacion):
    """Lo mismo, pero sobre una quesera que YA tiene meses anotados (que es la del
    cliente real). Las operaciones que el catálogo rechaza se saltan."""
    from tests.ayudas_reventa import historia

    historia(client, h)
    p = productos(client, h)
    r = operacion(client, h, p)
    if r is not None and r.status_code >= 400:
        pytest.skip(f"el catálogo no dejó hacer '{titulo}': {r.status_code} {r.text[:120]}")

    print(f"\n===== con historia, después de [{titulo}] =====")
    rotos_sin = informar("como lo manda la pantalla hoy", dia_a_dia(client, h, nombrando=False))
    rotos_con = informar("nombrando los productos a mano", dia_a_dia(client, h, nombrando=True))
    assert not rotos_sin and not rotos_con, (
        f"con historia, después de '{titulo}' el dueño NO puede: sin nombrar={rotos_sin} "
        f"nombrando={rotos_con}"
    )
    regla_de_oro(resumen(client, h), f"con historia, {titulo}")


# ==========================================================================
# UNA EMPRESA RECIÉN CREADA: LA SIEMBRA DEL CATÁLOGO NO CORRE AL CREARLA
# ==========================================================================
def test_empresa_recien_creada_sin_catalogo_sembrado(client, base_datos):
    """`ensure_catalogos_empresas` solo corre en el despliegue, no al crear la empresa.

    Entre que se crea la tercera quesera y el siguiente despliegue, su catálogo de
    reventa está VACÍO. Ahí es donde hay que ver si el dueño puede trabajar.
    """
    sa = auth_headers(client, "superadmin")
    r = client.post("/api/v1/empresas", json={"nombre": "Quesera C", "nit": "900C"},
                    headers=sa)
    assert r.status_code == 201, r.text
    empresa_id = r.json()["id"]
    h = {**sa, "X-Empresa-Id": empresa_id}

    cat = client.get(PROD, params={"size": 100}, headers=h)
    assert cat.status_code == 200, cat.text
    print("\n   catálogo de la empresa recién creada:", cat.json()["total"],
          [x["clave"] for x in cat.json()["items"]])

    print("\n===== empresa recién creada, catálogo vacío =====")
    rotos_sin = informar("como lo manda la pantalla hoy", dia_a_dia(client, h, nombrando=False))
    rotos_con = informar("nombrando los productos a mano", dia_a_dia(client, h, nombrando=True))
    assert not rotos_sin and not rotos_con, (
        f"en una empresa recién creada el dueño NO puede: sin nombrar={rotos_sin} "
        f"nombrando={rotos_con}"
    )
    regla_de_oro(resumen(client, h), "empresa recién creada")
