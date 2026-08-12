"""LO QUE LLEGA GRATIS ES MERCANCÍA, Y EL CATÁLOGO TIENE QUE TRATARLA COMO TAL.

EL HUECO QUE ESTA PRUEBA CIERRA. Una compra habla de DOS productos: `tipo` dice qué se
compró y `subproducto_tipo` dice a quién le entraron los kilos que llegaron GRATIS
encima (ver `CompraQueso`). Las dos puertas que protegen el catálogo —la que impide
cambiarle el padre a un producto con historia y la que impide quitarlo de la lista—
miraban SOLO `tipo`, así que un producto que únicamente hubiera recibido kilos gratis no
tenía "movimientos" para ellas.

QUÉ SE PODÍA HACER, MEDIDO: con 25,36 kg de borona en la bodega —los mismos que el
resumen reportaba en `existencias` y en `borona_disponible` en esa misma respuesta— se
la podía DESCOLGAR del queso y se la podía BORRAR del catálogo. Y de ahí salían tres
daños seguidos, todos sin haber movido un solo documento:

  · borrarla le borraba su renglón del desglose mientras la mercancía seguía saliendo
    en las existencias: dos partes de la misma respuesta diciendo cosas distintas;
  · la COMPRA DE TODOS LOS DÍAS —la que paga la leche— rebotaba con 422, porque ya no
    había a quién darle los kilos gratis: el dueño sin poder anotar su trabajo;
  · el ajuste de queso a borona también rebotaba, incluso nombrando el destino a mano.

LO QUE SE EXIGE AQUÍ, Y SON DOS COSAS QUE VAN JUNTAS:

  1. NO SE PUEDE TOCAR EL CATÁLOGO POR DEBAJO DE LA MERCANCÍA. Las dos puertas cuentan
     también las compras que nombran al producto en `subproducto_tipo`, así que
     descolgarlo o quitarlo se RECHAZA mientras esos kilos existan.
  2. Y EL CAMINO CONTRARIO: si un catálogo NO tiene el producto —una base vieja, una
     empresa recién creada, un producto que se quitó cuando todavía no tenía nada—,
     registrar lo de todos los días NO se puede bloquear. La compra se acepta, la fila
     nombra al producto de siempre, la mercancía aparece en su renglón del desglose y
     en las existencias, y se puede vender. Un problema de una lista no puede parar el
     negocio.
"""
import pytest

from tests.ayudas_reventa import (
    API, CERO, D, PROD, compra, crear_producto, existencia, exigir_quieto,
    exigir_que_se_pueda_trabajar, fila, foto, historia_solo_gratis, productos,
    regla_de_oro, resumen,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


# ==========================================================================
# 1. LAS DOS PUERTAS ATAJAN MIENTRAS HAYA MERCANCÍA
# ==========================================================================
def test_no_se_puede_descolgar_ni_borrar_un_producto_que_solo_recibio_kilos_gratis(
    client, h
):
    """El caso exacto del hueco, con las cifras a la vista."""
    historia_solo_gratis(client, h)
    res = resumen(client, h)
    print("\n   borona en bodega según el propio resumen:", existencia(res, "borona"))
    print("   borona_disponible (campo viejo)        :", res["borona_disponible"])
    assert existencia(res, "borona") == D("25.36")
    assert D(res["borona_disponible"]) == D("25.36")

    antes = foto(client, h)
    cat = productos(client, h)
    r1 = client.put(f"{PROD}/{cat['borona']['id']}",
                    json={"subproducto_de_id": None}, headers=h)
    print("   PUT subproducto_de_id = null ->", r1.status_code, r1.text[:220])
    r2 = client.delete(f"{PROD}/{cat['borona']['id']}", headers=h)
    print("   DELETE del producto          ->", r2.status_code, r2.text[:220])
    assert r1.status_code == 422, (
        "se le cambió el padre a un producto con 25,36 kg en bodega"
    )
    assert r2.status_code == 422, (
        "se quitó del catálogo un producto con 25,36 kg en bodega"
    )
    # Un rechazo tiene que decir QUÉ HACER, no solo que no se puede.
    assert "desactív" in r2.text.lower(), r2.text
    # Y un rechazo no puede haber movido nada por el camino.
    exigir_quieto(antes, foto(client, h), "los dos rechazos")
    assert existencia(resumen(client, h), "borona") == D("25.36")


def test_tampoco_se_puede_recolgar_de_otro_producto(client, h):
    """Re-colgarlo le cambiaría el grupo de costeo a mercancía ya registrada."""
    historia_solo_gratis(client, h)
    costeno = crear_producto(client, h, nombre="Costeño", unidad="kg")
    antes = foto(client, h)
    cat = productos(client, h)

    r = client.put(f"{PROD}/{cat['borona']['id']}",
                   json={"subproducto_de_id": costeno["id"]}, headers=h)
    print("\n   recolgar la borona (con 25,36 kg gratis) del costeño ->",
          r.status_code, r.text[:220])
    assert r.status_code == 422, r.text
    exigir_quieto(antes, foto(client, h), "recolgar la borona de otro producto")


def test_el_mismo_candado_en_un_producto_propio_del_dueno(client, h):
    """No es cosa de la borona: le pasa a cualquier producto del catálogo."""
    costeno = crear_producto(client, h, nombre="Costeño", unidad="kg")
    recorte = crear_producto(client, h, nombre="Recorte", unidad="kg",
                             subproducto_de_id=costeno["id"])
    compra(client, h, fecha="2026-02-03", productor="Patricia Rojas",
           tipo="costeno", kilos_brutos="200.00", precio_kilo="9000",
           borona_kilos="30.00", subproducto_tipo="recorte")

    res = resumen(client, h)
    print("\n   recorte en bodega:", existencia(res, "recorte"))
    assert existencia(res, "recorte") == D("30.00")

    r = client.delete(f"{PROD}/{recorte['id']}", headers=h)
    print("   borrar el recorte del catálogo ->", r.status_code, r.text[:220])
    assert r.status_code == 422, "se quitó del catálogo un producto con 30 kg en bodega"
    r = client.put(f"{PROD}/{recorte['id']}", json={"subproducto_de_id": None},
                   headers=h)
    print("   descolgar el recorte           ->", r.status_code, r.text[:220])
    assert r.status_code == 422, r.text
    assert existencia(resumen(client, h), "recorte") == D("30.00")


def test_un_producto_que_nunca_recibio_nada_si_se_puede_quitar(client, h):
    """El candado es por MERCANCÍA y no por precaución: sin kilos encima, se quita.

    Sin esto, el arreglo habría sido "no se puede quitar nada nunca", que no es un
    arreglo: el dueño tiene que poder limpiar un producto que creó por error.
    """
    historia_solo_gratis(client, h)
    panela = crear_producto(client, h, nombre="Panela de la finca", unidad="kg")
    antes = foto(client, h)
    r = client.delete(f"{PROD}/{panela['id']}", headers=h)
    print("\n   borrar un producto sin mercancía ->", r.status_code, r.text[:200])
    assert r.status_code == 204, r.text
    # Se le quitan al cotejo los renglones DEL PRODUCTO QUE SE QUITÓ: desaparecer de
    # la lista de existencias es justamente lo que se pidió, y esa fila estaba en cero.
    # Lo que se exige es que no se haya movido nada de LOS DEMÁS.
    sin_la_panela = {
        ruta: valor for ruta, valor in antes.items() if "panela_de_la_finca" not in ruta
    }
    exigir_quieto(sin_la_panela, foto(client, h), "borrar un producto sin mercancía")
    regla_de_oro(resumen(client, h), "después de quitar un producto sin mercancía")


def test_lo_que_atajan_las_puertas_no_le_para_el_trabajo_al_dueno(client, h):
    """Los rechazos del catálogo no pueden dejar sin registrar lo de todos los días."""
    historia_solo_gratis(client, h)
    cat = productos(client, h)
    client.put(f"{PROD}/{cat['borona']['id']}", json={"subproducto_de_id": None},
               headers=h)
    client.delete(f"{PROD}/{cat['borona']['id']}", headers=h)
    exigir_que_se_pueda_trabajar(client, h, "los rechazos del catálogo")
    regla_de_oro(resumen(client, h), "después de trabajar")


def test_el_hueco_de_una_quesera_no_toca_a_la_otra(client, base_datos):
    """Descolgar y borrar en A, midiendo B entera."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    historia_solo_gratis(client, ha)
    historia_solo_gratis(client, hb)
    antes_b = foto(client, hb)

    cat_a = productos(client, ha)
    client.put(f"{PROD}/{cat_a['borona']['id']}", json={"subproducto_de_id": None},
               headers=ha)
    client.delete(f"{PROD}/{cat_a['borona']['id']}", headers=ha)

    exigir_quieto(antes_b, foto(client, hb), "tocar el catálogo de la quesera A")
    regla_de_oro(resumen(client, hb), "quesera B")
    assert "borona" in productos(client, hb), "lo que se hizo en A le quitó algo a B"
    exigir_que_se_pueda_trabajar(client, hb, "tocar el catálogo de la quesera A")


# ==========================================================================
# 2. EL CAMINO CONTRARIO: UN CATÁLOGO SIN ESE PRODUCTO NO PARA EL NEGOCIO
# ==========================================================================
#
# Se llega a este estado quitando la borona ANTES de que tenga un solo kilo, que es lo
# que se puede hacer legítimamente. Es el mismo estado en el que está una base vieja o
# una empresa a la que nunca se le sembró ese producto.
def _sin_borona_en_el_catalogo(client, h) -> None:
    cat = productos(client, h)
    r = client.delete(f"{PROD}/{cat['borona']['id']}", headers=h)
    assert r.status_code == 204, r.text
    assert "borona" not in productos(client, h)


def test_sin_el_producto_en_el_catalogo_la_compra_de_todos_los_dias_pasa(client, h):
    """LO MÁS GRAVE EN LA PRÁCTICA: no mueve plata, le para el negocio."""
    _sin_borona_en_el_catalogo(client, h)
    r = client.post(f"{API}/compras",
                    json={"fecha": "2026-03-01", "productor": "Patricia Rojas",
                          "kilos_brutos": "100.00", "precio_kilo": "14000",
                          "borona_kilos": "5.00"}, headers=h)
    print("\n   compra de queso con borona encima, sin borona en el catálogo ->",
          r.status_code, r.text[:220])
    assert r.status_code == 201, "el dueño se quedó sin poder registrar su compra"
    # La fila NOMBRA a su producto: esos kilos no quedan sueltos ni se le acreditan
    # al queso.
    assert r.json()["subproducto_tipo"] == "borona", r.json()


def test_sin_el_producto_en_el_catalogo_la_mercancia_no_se_pierde(client, h):
    """Se puede ver en el desglose, se puede contar en las existencias y se vende."""
    _sin_borona_en_el_catalogo(client, h)
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="14000", borona_kilos="5.00")

    res = resumen(client, h)
    print("\n   existencias:", {e["producto"]: e["disponible"]
                                for e in res["existencias"]})
    for f in res["por_producto"]:
        print(f"      {f['producto']:24} {f['etiqueta']:40} kilos={f['kilos']:>10} "
              f"costo={f['costo']:>14}")
    assert existencia(res, "borona") == D("5.00"), (
        "los 5 kg que llegaron gratis desaparecieron de las existencias"
    )
    assert fila(res, "borona") is not None, (
        "la mercancía sale en las existencias y no tiene renglón en el desglose: "
        "dos partes de la misma respuesta diciendo cosas distintas"
    )
    regla_de_oro(res, "borona fuera del catálogo")

    r = client.post(f"{API}/ventas",
                    json={"fecha": "2026-03-02", "cliente": "Tienda La Esquina",
                          "tipo": "borona", "kilos": "1.00", "precio_kilo": "4000"},
                    headers=h)
    print("   vender 1 kg de esa borona ->", r.status_code, r.text[:220])
    assert r.status_code == 201, "no se puede vender la mercancía que hay en bodega"
    assert existencia(resumen(client, h), "borona") == D("4.00")


def test_y_el_dia_que_el_producto_vuelva_al_catalogo_los_kilos_ya_son_suyos(client, h):
    """La fila los nombró: volver a agregarlo los encuentra a nombre suyo."""
    _sin_borona_en_el_catalogo(client, h)
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="14000", borona_kilos="5.00")
    revivida = crear_producto(client, h, nombre="Borona", unidad="kg")
    assert revivida["clave"] == "borona"

    res = resumen(client, h)
    print("\n   existencias tras revivir la borona:",
          {e["producto"]: e["disponible"] for e in res["existencias"]})
    assert existencia(res, "borona") == D("5.00")
    regla_de_oro(res, "borona revivida")
    # Y ya está protegida: tiene mercancía encima.
    r = client.delete(f"{PROD}/{revivida['id']}", headers=h)
    print("   volver a borrarla ->", r.status_code, r.text[:200])
    assert r.status_code == 422, r.text


def test_una_compra_vieja_se_puede_seguir_editando(client, h):
    """Corregirle el precio a una compra con kilos gratis, con el catálogo cambiado.

    La fila ya nombró a su destinatario el día que se registró; el catálogo de hoy no
    puede opinar sobre eso. Sin este cuidado, el PUT se validaba otra vez contra la
    lista de productos y una corrección de precio rebotaba con 422.
    """
    hecha = compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
                   kilos_brutos="100.00", precio_kilo="14000", borona_kilos="5.00")
    # Se le agrega OTRO subproducto al queso: ahora hay dos que se pesan y el
    # catálogo, por sí solo, no sabría a cuál darle los kilos gratis.
    cat = productos(client, h)
    crear_producto(client, h, nombre="Migajón", unidad="kg",
                   subproducto_de_id=cat["queso"]["id"])

    r = client.put(f"{API}/compras/{hecha['id']}",
                   json={"precio_kilo": "14500"}, headers=h)
    print("\n   corregirle el precio a la compra vieja ->", r.status_code, r.text[:220])
    assert r.status_code == 200, r.text
    assert r.json()["subproducto_tipo"] == "borona", (
        "editar la compra le cambió de dueño a los kilos que ya estaban anotados"
    )
    assert existencia(resumen(client, h), "borona") == D("5.00")


def test_con_dos_subproductos_que_se_pesan_no_se_le_adivina_a_cual(client, h):
    """CON VARIOS CANDIDATOS NO SE ESCOGE ENTRE LOS PRODUCTOS DEL DUEÑO, y tampoco se
    le para la compra.

    Son las dos cosas que no se pueden hacer, y por eso el camino es un tercero:

      · anotárselos a uno de los dos le movería el inventario de un producto que él no
        nombró, y desde ahí sus ventas rebotan o pasan sin respaldo;
      · y RECHAZAR la compra lo deja sin poder anotar lo que ya compró y ya pagó, por
        un problema de su lista de productos. Está medido: quitando la borona con dos
        subproductos en la lista se le caía el día completo —compra, ajuste y venta—,
        y nombrar los productos a mano tampoco lo salvaba.

    Los kilos se anotan con la CLAVE DE SIEMPRE (el campo que él llenó se llama
    borona), que no le mueve el inventario a ninguno de los dos y sale en su propia
    fila del desglose y en las existencias, donde él la ve y la puede vender. Y si
    quiere otro destinatario, lo nombra: ahí manda lo que él diga.
    """
    _sin_borona_en_el_catalogo(client, h)
    cat = productos(client, h)
    crear_producto(client, h, nombre="Migajón", unidad="kg",
                   subproducto_de_id=cat["queso"]["id"])
    crear_producto(client, h, nombre="Recorte", unidad="kg",
                   subproducto_de_id=cat["queso"]["id"])

    r = client.post(f"{API}/compras",
                    json={"fecha": "2026-03-01", "productor": "Patricia Rojas",
                          "kilos_brutos": "100.00", "precio_kilo": "14000",
                          "borona_kilos": "5.00"}, headers=h)
    print("\n   compra con dos subproductos que se pesan ->", r.status_code,
          r.text[:260])
    assert r.status_code == 201, "el dueño se quedó sin poder registrar su compra"
    assert r.json()["subproducto_tipo"] == "borona", r.json()

    res = resumen(client, h)
    print("   existencias:", {e["producto"]: e["disponible"]
                              for e in res["existencias"]})
    assert existencia(res, "borona") == D("5.00")
    assert existencia(res, "migajon") in (None, CERO), (
        "los kilos se le anotaron a un producto que el dueño no nombró"
    )
    assert existencia(res, "recorte") in (None, CERO), (
        "los kilos se le anotaron a un producto que el dueño no nombró"
    )
    assert fila(res, "borona") is not None, "la mercancía no tiene renglón propio"
    regla_de_oro(res, "dos subproductos y nadie dijo cuál")

    # Y nombrándolo a mano, manda lo que él diga.
    r = client.post(f"{API}/compras",
                    json={"fecha": "2026-03-01", "productor": "Patricia Rojas",
                          "kilos_brutos": "100.00", "precio_kilo": "14000",
                          "borona_kilos": "5.00", "subproducto_tipo": "migajon"},
                    headers=h)
    assert r.status_code == 201, r.text
    assert existencia(resumen(client, h), "migajon") == D("5.00")


def test_nombrar_un_producto_que_no_existe_si_se_rechaza(client, h):
    """Escribir mal el destinatario es un error de dedo, y se dice.

    El arreglo del bloqueo no puede volverse "acepta cualquier cosa": lo que se acepta
    sin nombre es el producto DE SIEMPRE, no el que alguien tecleó mal.
    """
    r = client.post(f"{API}/compras",
                    json={"fecha": "2026-03-01", "productor": "Patricia Rojas",
                          "kilos_brutos": "100.00", "precio_kilo": "14000",
                          "borona_kilos": "5.00", "subproducto_tipo": "boronaa"},
                    headers=h)
    print("\n   nombrando un producto que no existe ->", r.status_code, r.text[:220])
    assert r.status_code == 422, r.text
    assert existencia(resumen(client, h), "boronaa") in (None, CERO)


# ==========================================================================
# 3. EL AJUSTE DE TODOS LOS DÍAS SIGUE PUDIÉNDOSE REGISTRAR
# ==========================================================================
def test_el_ajuste_de_queso_a_borona_no_lo_puede_romper_el_catalogo(client, h):
    """Antes: un PUT de presentación dejaba al dueño sin poder ajustar.

    Descolgar la borona la sacaba del grupo del queso, y desde ahí el ajuste rebotaba
    con 422 incluso nombrando el destino a mano. Hoy no se puede descolgar teniendo
    mercancía encima, así que ese 422 no puede ocurrir.
    """
    historia_solo_gratis(client, h)
    cat = productos(client, h)
    r = client.put(f"{PROD}/{cat['borona']['id']}",
                   json={"subproducto_de_id": None}, headers=h)
    print("\n   descolgar la borona ->", r.status_code, r.text[:220])
    assert r.status_code == 422, r.text

    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-01", "kilos": "10.00",
                          "destino": "borona", "precio_kilo": "3000"}, headers=h)
    print("   ajuste queso -> borona ->", r.status_code, r.text[:220])
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-01", "kilos": "10.00",
                          "destino": "borona", "producto_origen": "queso",
                          "producto_destino": "borona", "precio_kilo": "3000"},
                    headers=h)
    print("   nombrando los dos productos a mano ->", r.status_code, r.text[:220])
    assert r.status_code == 201, r.text
    regla_de_oro(resumen(client, h), "después de los dos ajustes")
