"""HASTA DÓNDE LLEGA EL CATÁLOGO HOY, medido y dicho de frente.

POR QUÉ EXISTE ESTE ARCHIVO. El catálogo ya manda sobre la plata: la unidad, el
inventario, el costo y la fila del desglose de un producto salen de él (eso lo fija
tests/test_reventa_el_catalogo_manda.py). Este archivo mide los bordes.

DOS DE LOS TRES LÍMITES QUE ESTE ARCHIVO DOCUMENTABA YA ESTÁN CERRADOS, y sus pruebas
se quedan aquí porque lo que fijan sigue haciendo falta: QUÉ PASA CUANDO NADIE DICE de
qué producto habla el movimiento, que es exactamente lo que manda la pantalla de hoy.

  1. LO QUE LLEGA GRATIS CON UNA COMPRA (`borona_kilos`) ya no le pertenece a un solo
     subproducto para siempre: la compra NOMBRA a quien lo recibe, en su propia columna
     (`compras_queso.subproducto_tipo`, migración `c5d9e3a7b1f4`). Cuando nadie lo dice,
     se resuelve AL ESCRIBIR y se guarda, y la respuesta no depende del orden del
     catálogo.
  2. LOS AJUSTES ya dicen de qué producto salieron y a cuál entraron
     (`producto_origen` / `producto_destino`, la misma migración), así que ya no hay UNA
     pareja para toda la empresa: cada fila trae la suya.
  3. DESACTIVAR UN PRODUCTO sigue sin impedir comprarlo ni venderlo: el estado del
     catálogo no se consulta al escribir un movimiento. Ese límite sigue vivo, y aquí
     queda escrito con cifras para que nadie lo descubra con la plata del cliente en la
     mano.

Lo que estas pruebas demuestran en los tres casos es lo mismo: NO HAY FUGA. Ninguna
plata se pierde, se le acredita a otro producto o queda fuera del desglose. La regla de
oro se cumple en todos.
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
def h(client, base_datos):
    return auth_headers(client, "admin.a")


def producto(client, h, nombre, **extra):
    r = client.post(PROD, json={"nombre": nombre, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def detalle(r) -> str:
    cuerpo = r.json()
    if isinstance(cuerpo, dict) and "error" in cuerpo:
        return str(cuerpo["error"].get("detail", cuerpo["error"]))
    return str(cuerpo)


def comprar(client, h, **datos):
    return client.post(f"{API}/compras", json=datos, headers=h)


def vender(client, h, **datos):
    return client.post(f"{API}/ventas", json=datos, headers=h)


def resumen(client, h):
    r = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def exigir_la_regla_de_oro(res, titulo):
    """La columna suma EXACTO el encabezado. Se exige en TODOS los límites: un límite
    puede dejar algo sin representar, pero nunca puede perder un peso."""
    for campo, columna in (
        ("total_compras", "costo"),
        ("total_ventas", "ingreso"),
        ("total_gastos", "gastos"),
        ("ganancia_estimada", "ganancia"),
    ):
        suma = sum((D(f[columna]) for f in res["por_producto"]), CERO)
        assert suma == D(res[campo]), (
            f"{titulo}: la columna '{columna}' suma {suma} y '{campo}' dice {res[campo]}"
        )


def pintar(titulo, res):
    print(f"\n===== {titulo} =====")
    for f in res["por_producto"]:
        print(f"   {f['etiqueta']:44} {f['unidad']:6} kilos={f['kilos']:>9} "
              f"costo={f['costo']:>13} ingreso={f['ingreso']:>13}")
    print("   existencias:")
    for e in res["existencias"]:
        print(f"   {e['etiqueta']:44} {e['unidad']:6} {e['disponible']:>10}")


# ============================================================================ 1
def test_un_subproducto_nuevo_no_tiene_donde_recibir_lo_que_llega_gratis(client, h):
    """SIN DECIR NADA, LO GRATIS SIGUE SIENDO DE LA BORONA. Es lo que esa columna ha
    significado desde que existe, y crear un subproducto nuevo no se lo puede quitar.

    QUÉ CAMBIÓ Y QUÉ NO. Ahora la compra puede NOMBRAR a quien recibe esos kilos
    (`subproducto_tipo`), así que un subproducto que el dueño cree SÍ tiene dónde
    recibir lo suyo —lo prueba el final de esta misma función—. Lo que esta prueba fija
    es el caso de la pantalla de hoy, que no lo manda: ahí se resuelve sin mirar el
    orden del catálogo y el resultado es el de siempre.

    Y LO QUE SIGUE GARANTIZADO: esos kilos no se los inventa nadie ni se los queda otro
    producto. La cuajada nueva queda en CERO —correcto: nunca se registró que llegara—
    y venderla rebota, en vez de costearse contra la cola del queso.
    """
    queso = next(
        p for p in client.get(PROD, headers=h).json()["items"] if p["clave"] == "queso"
    )
    cuajada = producto(client, h, "Cuajada suelta", subproducto_de_id=queso["id"])
    print("\nSUBPRODUCTO nuevo:", cuajada["clave"],
          "subproducto_de =", cuajada["subproducto_de_nombre"])
    assert cuajada["subproducto_de_nombre"] == "Queso"

    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez", tipo="queso",
                kilos_brutos="100", precio_kilo="20000", borona_kilos="20")
    assert r.status_code == 201, detalle(r)

    res = resumen(client, h)
    pintar("100 kg de queso con 20 kg 'gratis'", res)
    por_clave = {e["producto"]: D(e["disponible"]) for e in res["existencias"]}
    print("   inventarios:", por_clave)
    # Los 20 kg quedaron contados como BORONA, que es de quien es esa columna.
    assert por_clave["borona"] == D(20)
    # Y la cuajada nueva en CERO: nunca se registró que llegara nada de ella.
    assert por_clave["cuajada_suelta"] == CERO, (
        "el subproducto nuevo no puede aparecer con kilos que nadie registró"
    )

    # Y venderla se RECHAZA, porque no hay: es lo correcto. Antes esa venta pasaba y se
    # costeaba contra la cola del QUESO, así que 20 kg "gratis" se costeaban a
    # $20.000/kg: $400.000 de costo contra $60.000 de ingreso, una pérdida inventada.
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="cuajada_suelta", kilos="20", precio_kilo="3000")
    print("vender 20 kg de la cuajada que nunca entró ->", r.status_code, detalle(r))
    assert r.status_code == 422
    assert "Cuajada suelta" in detalle(r)

    # La borona de verdad, en cambio, sí se puede vender y es ganancia pura.
    r = vender(client, h, fecha="2026-03-03", cliente="Tienda Sol", tipo="borona",
               kilos="20", precio_kilo="3000")
    assert r.status_code == 201, detalle(r)
    res = resumen(client, h)
    pintar("la borona de verdad vendida", res)
    de_borona = next(f for f in res["por_producto"] if f["producto"] == "borona")
    assert D(de_borona["ingreso"]) == D("60000.00")
    assert D(de_borona["costo"]) == CERO, "llegó gratis: no se paga"
    exigir_la_regla_de_oro(res, "subproducto nuevo sin entrada")

    # Y EL LÍMITE YA NO ESTÁ: si la compra DICE que esos kilos son de la cuajada, le
    # entran a la cuajada y a nadie más.
    r = comprar(client, h, fecha="2026-03-06", productor="Pedro Perez", tipo="queso",
                kilos_brutos="50", precio_kilo="20000", borona_kilos="8",
                subproducto_tipo="cuajada_suelta")
    print("comprar nombrando a la cuajada como destinataria ->", r.status_code,
          detalle(r))
    assert r.status_code == 201, detalle(r)
    assert r.json()["subproducto_tipo"] == "cuajada_suelta"
    res = resumen(client, h)
    por_clave = {e["producto"]: D(e["disponible"]) for e in res["existencias"]}
    print("   inventarios después de nombrar a la cuajada:", por_clave)
    assert por_clave["cuajada_suelta"] == D(8)
    assert por_clave["borona"] == CERO, "los 8 kg de la cuajada se le fueron a la borona"
    r = vender(client, h, fecha="2026-03-07", cliente="Tienda Sol",
               tipo="cuajada_suelta", kilos="8", precio_kilo="2500")
    assert r.status_code == 201, detalle(r)
    exigir_la_regla_de_oro(resumen(client, h), "la cuajada nombrada y vendida")


def test_la_cadena_de_subproductos_se_corta_en_un_nivel(client, h):
    """Esto SÍ está bien puesto y conviene tenerlo escrito: el subproducto de un
    subproducto no existe en el motor de reparto, y admitirlo sería ofrecer algo que el
    costeo no sabe calcular."""
    items = client.get(PROD, headers=h).json()["items"]
    borona = next(p for p in items if p["clave"] == "borona")
    r = client.post(PROD, json={"nombre": "Migaja", "subproducto_de_id": borona["id"]},
                    headers=h)
    print("\nSUBPRODUCTO de la borona ->", r.status_code, detalle(r))
    assert r.status_code in (400, 422)
    assert "un nivel" in detalle(r)


# ============================================================================ 2
def test_los_ajustes_salen_de_una_sola_pareja_de_productos(client, h):
    """UN AJUSTE QUE NO DICE DE DÓNDE SALE, SALE DEL QUESO. Y si no hay queso, rebota.

    Es el caso de la pantalla de hoy, que todavía no manda los productos: el origen es
    el de siempre —la clave 'queso', una constante, no "el primero de la lista"— y el
    destino lo resuelve el catálogo sin mirar el orden. La fila queda GUARDADA diciendo
    de qué producto a qué producto, así que ninguna lectura vuelve a adivinarlo (el
    ajuste que sí nombra sus productos se prueba en
    tests/test_reventa_el_catalogo_no_mueve_plata.py).

    LO QUE ESTA PRUEBA FIJA, y era un defecto real: antes el ajuste se validaba contra
    "todos los kilos comprados", así que con 100 kg de costeño y CERO de queso se podían
    convertir 30 kg a borona: los kilos del costeño se desmenuzaban como si fueran queso
    y aparecían como borona vendible. Ahora el ajuste sale del queso, y si no hay queso
    se rechaza con un mensaje que dice de qué producto está hablando.
    """
    producto(client, h, "Costeno")
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez", tipo="costeno",
                kilos_brutos="100", precio_kilo="5000")
    assert r.status_code == 201, detalle(r)

    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-02", "kilos": "30", "destino": "borona",
                          "precio_kilo": "3000"},
                    headers=h)
    print("\nCONVERTIR 30 kg a borona con 100 kg de costeño y CERO de queso ->",
          r.status_code, detalle(r))
    assert r.status_code == 422, "el costeño se desmenuzó como si fuera queso"
    assert "Queso" in detalle(r), (
        f"el mensaje tiene que decir de qué producto sale el ajuste: {detalle(r)}"
    )

    res = resumen(client, h)
    pintar("el costeño intacto", res)
    por_clave = {e["producto"]: D(e["disponible"]) for e in res["existencias"]}
    assert por_clave["costeno"] == D(100), "el ajuste no le quitó kilos al costeño"
    assert por_clave["borona"] == CERO, "no se inventó borona vendible"
    assert D(res["kilos_a_borona"]) == CERO
    exigir_la_regla_de_oro(res, "ajuste rechazado")

    # Con queso comprado, el ajuste sí pasa y sale DEL QUESO.
    r = comprar(client, h, fecha="2026-03-03", productor="Pedro Perez", tipo="queso",
                kilos_brutos="80", precio_kilo="20000")
    assert r.status_code == 201, detalle(r)
    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-04", "kilos": "30", "destino": "borona",
                          "precio_kilo": "3000"},
                    headers=h)
    print("con 80 kg de queso comprados, el mismo ajuste ->", r.status_code, detalle(r))
    assert r.status_code == 201, detalle(r)
    res = resumen(client, h)
    pintar("30 kg de queso pasados a borona", res)
    por_clave = {e["producto"]: D(e["disponible"]) for e in res["existencias"]}
    assert por_clave["queso"] == D(50), "los 30 kg salieron del queso"
    assert por_clave["borona"] == D(30), "y le entraron a la borona"
    assert por_clave["costeno"] == D(100), "el costeño no se movió"
    exigir_la_regla_de_oro(res, "ajuste del queso")


# ============================================================================ 3
def test_desactivar_un_producto_no_impide_seguir_moviendolo(client, h):
    """LÍMITE: el estado del catálogo no se consulta al escribir un movimiento.

    Quitar un producto sí está protegido —no se puede si tiene movimientos—, pero
    DESACTIVARLO no impide seguir comprándolo ni vendiéndolo. Queda medido acá porque
    es una expectativa razonable del dueño ("lo desactivé para que no se use más") que
    hoy no se cumple, y porque el día que se cierre hay que decidir qué pasa con una
    factura a medio registrar de un producto que alguien desactivó en el otro
    computador.

    Lo que sí está garantizado: la plata de esos movimientos se cuenta bien, en la fila
    del producto desactivado, y el desglose sigue sumando el encabezado.
    """
    p = producto(client, h, "Costeno")
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez", tipo="costeno",
                kilos_brutos="100", precio_kilo="5000")
    assert r.status_code == 201, detalle(r)

    # No se puede QUITAR: eso sí está protegido.
    r = client.delete(f"{PROD}/{p['id']}", headers=h)
    print("\nQUITAR un producto con movimientos ->", r.status_code, detalle(r))
    assert r.status_code in (400, 422)

    # Desactivarlo pasa, y seguir vendiéndolo también.
    r = client.put(f"{PROD}/{p['id']}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "inactivo"
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol", tipo="costeno",
               kilos="10", precio_kilo="9000")
    print("VENDER un producto DESACTIVADO ->", r.status_code, detalle(r))
    assert r.status_code == 201, "límite: el estado del catálogo no se mira al escribir"

    # Y su plata queda bien contada, en SU fila.
    res = resumen(client, h)
    pintar("un producto desactivado que se sigue vendiendo", res)
    suya = next(f for f in res["por_producto"] if f["producto"] == "costeno")
    assert D(suya["ingreso"]) == D("90000.00")
    assert D(suya["costo"]) == D("50000.00")
    por_clave = {e["producto"]: D(e["disponible"]) for e in res["existencias"]}
    assert por_clave["costeno"] == D(90)
    exigir_la_regla_de_oro(res, "producto desactivado")
