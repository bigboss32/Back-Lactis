"""QUE LA PLATA SE QUEDE QUIETA CUANDO SE TOCA LA LISTA DE PRODUCTOS.

Mide tres cosas, todas con la foto completa aplanada cifra por cifra (el resumen
con su desglose y sus existencias, el panel de lotes con su detalle, la ganancia
por día y los estados de cuenta):

  1. que tocar SOLO el catálogo no mueva ni un kilo ni un peso de lo registrado,
     INCLUIDAS LAS PUERTAS QUE EL GUARDIA DE MOVIMIENTOS NO VIGILABA: agregar un
     producto cuya clave YA tiene plata anotada, revivir uno que se había quitado,
     y la siembra del despliegue, que lo dispara SOLA;
  2. que una lista mal armada —un subproducto que se pesa colgado de un padre que
     se cuenta— no saque un desglose con claves repetidas ni le acredite el neto a
     otro grupo;
  3. que el reparto sea determinista: dos historias idénticas, las mismas cifras.

POR QUÉ HUBO QUE CERRAR LA PUERTA DE CREAR. Una clave puede tener plata anotada SIN
estar en el catálogo (una fila vieja, una importada, un tipo escrito de otra forma,
o una empresa a la que todavía no le han sembrado la lista), y esos kilos se leen
como los de un producto RAÍZ, con su pozo y su fila propios. El día que alguien la
agregaba a la lista marcándola subproducto de otro, el grupo de costeo de esas
filas viejas cambiaba y el reparto de la ganancia se rehacía sobre compras y ventas
ya cuadradas: Patricia Rojas pasaba de $340.000,00 a -$373.333,33 y Sebastián Ruiz
de $50.000,00 a $763.333,33 sin que nadie tocara un documento. `validar_actualizar`
y `validar_eliminar` ya tapaban el PUT y el DELETE; faltaban CREAR, REVIVIR y la
siembra de cada despliegue.

Y EL RECHAZO SIEMPRE TRAE LA SALIDA: agregarlo SIN marcarlo como subproducto sí se
puede, y eso no mueve una cifra, porque como producto independiente es exactamente
como sus kilos ya estaban contados. Cada caso de aquí abajo lo comprueba.

LO QUE ESTE ARCHIVO NO MIDE, y no es un olvido: que el costo de la misma venta sea
el mismo en el desglose (promedio ponderado del período) y en el panel de lotes
(cola FIFO). Son dos formas de costear la misma venta y la diferencia es VIEJA —no
la abrió el catálogo—, así que aquí solo se exige lo que sí es asunto de este
archivo: que esa diferencia NO SE MUEVA porque se tocó la lista de productos
(`test_el_costo_sigue_siendo_uno_solo_despues_de_tocar_el_catalogo`).
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.ayudas_reventa import (
    CERO, D, PROD, compra, crear_producto, diferencias, exigir_quieto, fila, foto,
    historia, historia_gorda, lotes as pedir_lotes, productos, regla_de_oro,
    resumen, venta,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


@pytest.fixture()
def hb(client, base_datos):
    return auth_headers(client, "admin.b")


def rechazo(respuesta, *frases: str) -> None:
    """Un rechazo con la salida escrita: 422 y el mensaje diciendo qué hacer."""
    assert respuesta.status_code == 422, respuesta.text
    for frase in frases:
        assert frase in respuesta.text, (
            f"el mensaje tiene que decir '{frase}': {respuesta.text[:300]}"
        )


def pintar_diferencias(antes, despues, titulo) -> list:
    movidas, nacidas = diferencias(antes, despues)
    print(f"\n===== {titulo}: {len(movidas)} movidas, {len(nacidas)} nacidas =====")
    for ruta, viejo, nuevo in movidas[:40]:
        print(f"   MOVIÓ {ruta}: {viejo} -> {nuevo}")
    for ruta, valor in nacidas[:20]:
        print(f"   NACIÓ {ruta} = {valor}")
    return movidas


# ==========================================================================
# 1. REVIVIR UN PRODUCTO QUITADO Y AGREGAR UNO QUE YA TIENE PLATA ANOTADA
# ==========================================================================
def test_revivir_la_borona_colgandola_del_queso_no_puede_mover_plata(client, h):
    """Quitar la borona (sin historia), registrar kilos suyos, y volverla a agregar.

    `validar_actualizar` no deja cambiarle el padre a un producto con movimientos,
    pero `crear` sobre una clave dormida REVIVE la fila y le escribía el padre que
    viniera en el payload SIN preguntar por los movimientos. En el medio, la clave
    'borona' recibió kilos de verdad: los que llegaron gratis con el lote de
    Patricia, que la compra anotó a nombre suyo aunque no estuviera en la lista.

    Ojo con lo que se pregunta acá: NO es si la columna `subproducto_de_id` cambia
    —la fila dormida se quedó con el queso adentro— sino si volver a estar EN LA
    LISTA marcada como subproducto le cambia el grupo a esos kilos. Le cambia.
    """
    p = productos(client, h)
    r = client.delete(f"{PROD}/{p['borona']['id']}", headers=h)
    assert r.status_code in (200, 204), r.text

    # Con la borona fuera del catálogo, la compra igual le anota los kilos gratis
    # (la fila los nombra con la constante de siempre) y la venta sale.
    compra(client, h, fecha="2026-02-03", productor="Patricia Rojas",
           kilos_brutos="820.53", precio_kilo="14317", borona_kilos="18.27")
    venta(client, h, fecha="2026-02-12", cliente="Don José Pérez", tipo="queso",
          kilos="500.37", precio_kilo="21533", gasto_por_kilo="137")
    venta(client, h, fecha="2026-02-26", cliente="Tienda La Esquina", tipo="borona",
          kilos="10.13", precio_kilo="4133")

    antes = foto(client, h)
    res_antes = resumen(client, h)
    print("   fila de borona ANTES:", fila(res_antes, "borona"))

    # El dueño la vuelve a agregar, colgada del queso: se rechaza con la salida.
    r = client.post(PROD, json={"nombre": "Borona", "unidad": "kg",
                                "subproducto_de_id": p["queso"]["id"]}, headers=h)
    print("   revivirla colgada del queso ->", r.status_code, r.text[:220])
    rechazo(r, "kilos que le llegaron", "sin marcarlo como subproducto")
    pintar_diferencias(antes, foto(client, h), "el rechazo")
    exigir_quieto(antes, foto(client, h), "rechazar la revivida con padre")

    # Y LA SALIDA QUE EL MENSAJE OFRECE SÍ SE PUEDE, y tampoco mueve una cifra:
    # como producto independiente es como esos kilos ya estaban contados.
    nuevo = crear_producto(client, h, nombre="Borona")
    print("   revivida suelta:",
          {k: nuevo[k] for k in ("id", "clave", "subproducto_de_id")})
    assert nuevo["clave"] == "borona"
    assert nuevo["subproducto_de_id"] is None

    despues = foto(client, h)
    res_despues = resumen(client, h)
    print("   fila de borona DESPUÉS:", fila(res_despues, "borona"))
    movidas = pintar_diferencias(antes, despues, "revivir la borona suelta")
    regla_de_oro(res_despues, "después de revivir")
    assert not movidas, f"revivir la borona movió {len(movidas)} cifras"


def _quesera_sin_catalogo_que_ya_trabajo(client) -> dict:
    """Una empresa recién creada —catálogo VACÍO, que es lo que deja `POST /empresas`—
    con un día de trabajo encima. Es el estado real de una quesera creada entre dos
    despliegues: la siembra del catálogo solo corre en el despliegue siguiente."""
    sa = auth_headers(client, "superadmin")
    r = client.post("/api/v1/empresas", json={"nombre": "Quesera C", "nit": "900C"},
                    headers=sa)
    assert r.status_code == 201, r.text
    h3 = {**sa, "X-Empresa-Id": r.json()["id"]}
    assert client.get(PROD, headers=h3).json()["total"] == 0

    compra(client, h3, fecha="2026-02-03", productor="Patricia Rojas",
           kilos_brutos="820.53", precio_kilo="14317", borona_kilos="18.27")
    venta(client, h3, fecha="2026-02-12", cliente="Don José Pérez", tipo="queso",
          kilos="500.37", precio_kilo="21533", gasto_por_kilo="137")
    venta(client, h3, fecha="2026-02-26", cliente="Tienda La Esquina", tipo="borona",
          kilos="10.13", precio_kilo="4133")
    return h3


def test_agregar_un_producto_cuya_clave_ya_tiene_mercancia_no_puede_mover_plata(client, h):
    """Sin borrar nada: una empresa cuyo catálogo NO se sembró, y el dueño lo arma.

    La raíz sí se puede agregar —'queso' ya se leía como producto raíz, así que
    ponerlo en la lista no le cambia el grupo a nadie— y el subproducto no.
    """
    h3 = _quesera_sin_catalogo_que_ya_trabajo(client)
    antes = foto(client, h3)
    res_antes = resumen(client, h3)
    print("   borona ANTES:", fila(res_antes, "borona"))
    print("   queso  ANTES:", fila(res_antes, "queso"))

    queso = crear_producto(client, h3, nombre="Queso", unidad="kg")
    exigir_quieto(antes, foto(client, h3), "agregar el queso a la lista")

    r = client.post(PROD, json={"nombre": "Borona", "unidad": "kg",
                                "subproducto_de_id": queso["id"]}, headers=h3)
    print("   agregar la borona colgada del queso ->", r.status_code, r.text[:220])
    rechazo(r, "sin marcarlo como subproducto")

    # Suelta sí, y con la lista completa nada se movió.
    borona = crear_producto(client, h3, nombre="Borona", unidad="kg")
    assert borona["subproducto_de_id"] is None

    despues = foto(client, h3)
    res_despues = resumen(client, h3)
    print("   borona DESPUÉS:", fila(res_despues, "borona"))
    print("   queso  DESPUÉS:", fila(res_despues, "queso"))
    movidas = pintar_diferencias(antes, despues, "armar el catálogo después")
    regla_de_oro(res_despues, "después de armar el catálogo")
    assert not movidas, f"armar el catálogo movió {len(movidas)} cifras"


def test_la_siembra_del_despliegue_no_le_cambia_el_grupo_a_lo_ya_registrado(
    client, db_session, h
):
    """EL CASO QUE SE DISPARA SOLO, sin que nadie toque nada: el siguiente despliegue.

    `ensure_catalogos_empresas` corre en start.sh después de cada `alembic upgrade
    head` y le siembra el catálogo a toda empresa que no lo tenga, con la borona
    colgada del queso. En una quesera que ya trabajó con el catálogo vacío, eso le
    recostearía la plata que el dueño ya cuadró — y él no habría hecho nada.

    La siembra tiene que quedar igual (el dueño necesita su lista) pero con la
    borona SUELTA, que es como sus kilos ya estaban contados.
    """
    from app.seeds.seed import ensure_catalogos_empresas

    h3 = _quesera_sin_catalogo_que_ya_trabajo(client)
    antes = foto(client, h3)

    ensure_catalogos_empresas(db_session)
    db_session.commit()

    cat = productos(client, h3)
    print("\n   catálogo sembrado:",
          {c: p["subproducto_de_id"] for c, p in cat.items()})
    assert set(cat) == {"queso", "borona", "mozzarella"}, (
        "la siembra tiene que dejarle su lista completa"
    )
    assert cat["borona"]["subproducto_de_id"] is None, (
        "la siembra colgó la borona del queso teniendo kilos suyos ya registrados"
    )
    movidas = pintar_diferencias(antes, foto(client, h3), "la siembra del despliegue")
    regla_de_oro(resumen(client, h3), "después de la siembra")
    assert not movidas, f"la siembra movió {len(movidas)} cifras"


def test_en_una_empresa_nueva_la_siembra_deja_la_borona_colgada_del_queso(
    client, db_session, base_datos
):
    """EL CONTROL: sin plata anotada, la siembra hace lo de siempre.

    El cuidado de arriba no puede volverse "la borona nunca se cuelga": en una
    quesera nueva de verdad —la que se crea y todavía no ha registrado nada— la
    lista tiene que quedar como en las otras dos, con la borona como subproducto
    del queso. Si no, el arreglo habría cambiado el catálogo de todo el mundo.
    """
    from app.seeds.seed import ensure_catalogos_empresas

    sa = auth_headers(client, "superadmin")
    r = client.post("/api/v1/empresas", json={"nombre": "Quesera D", "nit": "900D"},
                    headers=sa)
    assert r.status_code == 201, r.text
    h4 = {**sa, "X-Empresa-Id": r.json()["id"]}

    ensure_catalogos_empresas(db_session)
    db_session.commit()

    cat = productos(client, h4)
    print("\n   catálogo de la quesera nueva:",
          {c: p["subproducto_de_id"] for c, p in cat.items()})
    assert cat["borona"]["subproducto_de_id"] == cat["queso"]["id"]


# ==========================================================================
# 2. LA MATRIZ DE SIEMPRE, SOBRE LA HISTORIA MÁS GORDA
# ==========================================================================
def _ops(client, h):
    p = productos(client, h)
    return [
        ("renombrar el queso", lambda: client.put(
            f"{PROD}/{p['queso']['id']}", json={"nombre": "Queso costeño"}, headers=h)),
        ("renombrar la borona a 'Merma'", lambda: client.put(
            f"{PROD}/{p['borona']['id']}", json={"nombre": "Merma"}, headers=h)),
        ("renombrar a 'Pendiente'", lambda: client.put(
            f"{PROD}/{p['mozzarella']['id']}", json={"nombre": "Pendiente"}, headers=h)),
        ("desactivar la borona", lambda: client.put(
            f"{PROD}/{p['borona']['id']}", json={"estado": "inactivo"}, headers=h)),
        ("desactivar el queso", lambda: client.put(
            f"{PROD}/{p['queso']['id']}", json={"estado": "inactivo"}, headers=h)),
        ("reordenar al revés", lambda: [
            client.put(f"{PROD}/{p[c]['id']}", json={"orden": i}, headers=h)
            for i, c in enumerate(("mozzarella", "borona", "queso"))][-1]),
        ("todos con el mismo orden", lambda: [
            client.put(f"{PROD}/{p[c]['id']}", json={"orden": 0}, headers=h)
            for c in ("queso", "borona", "mozzarella")][-1]),
        ("crear un subproducto en orden 0", lambda: client.post(
            PROD, json={"nombre": "Suero", "unidad": "kg", "orden": 0,
                        "subproducto_de_id": p["queso"]["id"]}, headers=h)),
        ("crear una raíz al final", lambda: client.post(
            PROD, json={"nombre": "Panela", "unidad": "kg"}, headers=h)),
        ("intentar descolgar la borona", lambda: client.put(
            f"{PROD}/{p['borona']['id']}", json={"subproducto_de_id": None}, headers=h)),
        ("intentar borrar la borona", lambda: client.delete(
            f"{PROD}/{p['borona']['id']}", headers=h)),
        ("intentar borrar el recorte", lambda: client.delete(
            f"{PROD}/{p['recorte']['id']}", headers=h)),
    ]


def test_la_matriz_completa_sobre_la_historia_gorda(client, h):
    historia_gorda(client, h)
    antes = foto(client, h)
    regla_de_oro(resumen(client, h), "de entrada")

    for titulo, hacer in _ops(client, h):
        r = hacer()
        print(f"\n   [{titulo}] -> {r.status_code} {r.text[:110] if r.status_code >= 400 else ''}")
        despues = foto(client, h)
        exigir_quieto(antes, despues, titulo)
        regla_de_oro(resumen(client, h), titulo)
        antes = despues


def test_la_matriz_encadenada_y_midiendo_la_otra_quesera(client, h, hb):
    """Las operaciones una detrás de otra, y la quesera vecina sin enterarse."""
    historia_gorda(client, h)
    historia(client, hb)
    foto_b = foto(client, hb)
    antes = foto(client, h)

    for titulo, hacer in _ops(client, h):
        hacer()
    despues = foto(client, h)
    exigir_quieto(antes, despues, "todas las operaciones encadenadas")
    exigir_quieto(foto_b, foto(client, hb), "la quesera vecina")
    regla_de_oro(resumen(client, h), "encadenadas")



def _el_dia_de_trabajo_con_las_dos_unidades(client, h) -> None:
    """Queso con borona encima, mozzarella por unidades, y las tres ventas."""
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="20000", borona_kilos="20.00")
    compra(client, h, fecha="2026-03-05", productor="Sebastián Ruiz",
           tipo="mozzarella", barras="40", precio_barra="12000")
    venta(client, h, fecha="2026-03-11", cliente="Don José Pérez", tipo="queso",
          kilos="90.00", precio_kilo="26000")
    venta(client, h, fecha="2026-03-12", cliente="Tienda La Esquina", tipo="borona",
          kilos="15.00", precio_kilo="2000")
    venta(client, h, fecha="2026-03-13", cliente="Don José Pérez", tipo="mozzarella",
          barras="10", precio_barra="17000")


# ==========================================================================
# 3. UNA LISTA MAL ARMADA: EL SUBPRODUCTO Y SU PADRE EN UNIDADES DISTINTAS
# ==========================================================================
def test_un_subproducto_en_kilos_no_se_puede_colgar_de_un_padre_por_unidades(client, h):
    """EL DUEÑO SE EQUIVOCA DE PADRE EN LA LISTA: la borona (kg) bajo la mozzarella
    (unidades). El PUT pasaba con 200 —no hay historia todavía, así que el guardia de
    movimientos no tiene por qué atajarlo— y de ahí salía un desglose con TRES claves
    REPETIDAS, porque el grupo se imprimía dos veces: una en la vuelta de los kilos y
    otra en la de las unidades. La pantalla usa esa clave para decidir cómo pintar cada
    renglón, y el neto de la borona —que llegó gratis con el queso de Patricia— se le
    acreditaba al grupo de la mozzarella: $30.000 al productor equivocado.

    SE RECHAZA LA PAREJA, y es lo correcto y no lo cómodo: el grupo de costeo tiene UN
    pozo y ese pozo está en la unidad de su raíz. Un subproducto que se pesa colgado de
    un padre que se cuenta no tiene de dónde heredar costo, porque las barras no se
    reparten entre kilos. No hay una cuenta buena que darle: lo que hay es una lista
    mal armada, y se dice cuando se está armando.
    """
    p = productos(client, h)
    r = client.put(f"{PROD}/{p['borona']['id']}",
                   json={"subproducto_de_id": p["mozzarella"]["id"]}, headers=h)
    print("\n   colgar la borona (kg) de la mozzarella (unidades) ->",
          r.status_code, r.text[:240])
    rechazo(r, "se cuenta por unidades", "misma unidad")

    # Y por la puerta de crear, tampoco.
    r = client.post(PROD, json={"nombre": "Migajón", "unidad": "kg",
                                "subproducto_de_id": p["mozzarella"]["id"]}, headers=h)
    print("   crear un subproducto en kilos bajo la mozzarella ->",
          r.status_code, r.text[:240])
    rechazo(r, "se cuenta por unidades")

    # Al revés también: uno por unidades colgado de uno que se pesa.
    r = client.post(PROD, json={"nombre": "Tajada", "unidad": "unidad",
                                "subproducto_de_id": p["queso"]["id"]}, headers=h)
    print("   crear un subproducto por unidades bajo el queso ->",
          r.status_code, r.text[:240])
    rechazo(r, "se cuenta por unidades")

    # Y el día de trabajo sale como siempre, con la lista intacta.
    _el_dia_de_trabajo_con_las_dos_unidades(client, h)
    res = resumen(client, h)
    claves = [f["producto"] for f in res["por_producto"]]
    print("   filas del desglose:", claves)
    regla_de_oro(res, "con la lista bien armada")


def test_una_pareja_de_unidades_distintas_ya_guardada_no_rompe_el_desglose(
    client, db_session, h, hb
):
    """LA MISMA PAREJA, PERO YA GUARDADA EN LA BASE: se lee como producto raíz.

    El candado de arriba es de la puerta de escribir, y una fila así pudo quedar
    guardada antes de que existiera (o por un SQL suelto). Leerla tal cual sacaba el
    desglose con claves repetidas, así que el catálogo la lee como lo único que puede
    ser sin inventar una cuenta: un producto INDEPENDIENTE.

    SE MIDE CONTRA UN CONTROL, cifra por cifra, y por eso hay dos queseras: en la A la
    borona queda colgada de la mozzarella escribiendo la base a mano, y en la B se
    descuelga por la puerta de siempre (todavía sin historia, así que se puede). Las
    dos hacen exactamente el mismo día de trabajo, y las dos tienen que sacar
    EXACTAMENTE las mismas cifras: la pareja imposible se lee como lo que la B tiene
    de verdad. Antes, en la A, el neto de la borona —que llegó gratis con el queso de
    Patricia— se le acreditaba al grupo de la mozzarella: $30.000,00 al productor
    equivocado.
    """
    p = productos(client, h)
    hecho = db_session.execute(
        text("UPDATE productos_reventa SET subproducto_de_id = :padre WHERE id = :id"),
        {"padre": uuid.UUID(p["mozzarella"]["id"]).hex,
         "id": uuid.UUID(p["borona"]["id"]).hex},
    )
    assert hecho.rowcount == 1, "la prueba no alcanzó a dejar la pareja mal armada"
    db_session.commit()

    # El control: la misma borona, suelta de verdad y por la puerta de siempre.
    pb = productos(client, hb)
    r = client.put(f"{PROD}/{pb['borona']['id']}",
                   json={"subproducto_de_id": None}, headers=hb)
    assert r.status_code == 200, r.text

    _el_dia_de_trabajo_con_las_dos_unidades(client, h)
    _el_dia_de_trabajo_con_las_dos_unidades(client, hb)

    res = resumen(client, h)
    claves = [f["producto"] for f in res["por_producto"]]
    print("\n   filas del desglose:", claves)
    print("   por_productor:",
          [(x["productor"], x["ganancia_estimada"]) for x in res["por_productor"]])
    repetidas = sorted({c for c in claves if claves.count(c) > 1})
    assert not repetidas, f"el desglose sacó claves repetidas: {repetidas}"
    regla_de_oro(res, "borona colgada de la mozzarella en la base")

    a, b = foto(client, h), foto(client, hb)
    distintas = [(ruta, a[ruta], b.get(ruta)) for ruta in a if b.get(ruta) != a[ruta]]
    for ruta, x, y in distintas[:30]:
        print(f"   DISTINTO {ruta}: pareja imposible={x}  borona suelta={y}")
    assert not distintas, (
        f"{len(distintas)} cifras distintas entre la pareja imposible y una borona "
        "suelta de verdad"
    )

# ==========================================================================
# 4. EL COSTO DE LA MISMA VENTA NO SE MUEVE PORQUE SE TOQUE LA LISTA
#
# El desglose costea el período con el promedio ponderado de las compras y el panel
# de lotes sirve cada venta de la cola FIFO, así que las dos pantallas pueden dar
# cifras distintas para la misma venta. ESA diferencia es vieja y no es asunto de
# este archivo (ver el docstring de arriba); lo que sí es asunto suyo es que no la
# mueva una edición del catálogo, que es lo que se exige aquí.
# ==========================================================================
def costo_segun_lotes(panel) -> Decimal:
    return sum(
        (D(l["costo_vendido"]) + D(l["costo_borona_vendida"]) for l in panel["lotes"]),
        CERO,
    )


def costo_segun_desglose(client, h, res) -> Decimal:
    de_kilos = {c for c, p in productos(client, h).items() if p["unidad"] == "kg"}
    return sum(
        (D(f["costo"]) for f in res["por_producto"] if f["producto"] in de_kilos), CERO
    )


def test_el_costo_sigue_siendo_uno_solo_despues_de_tocar_el_catalogo(client, h):
    historia_gorda(client, h)
    for titulo, hacer in _ops(client, h):
        hacer()
        res, panel = resumen(client, h), pedir_lotes(client, h)
        d, l = costo_segun_desglose(client, h, res), costo_segun_lotes(panel)
        print(f"   [{titulo}] desglose={d}  lotes={l}  dif={d - l}")
        # Con mercancía convertida SIN vender el desglose va adelantado a propósito;
        # lo que no puede es CAMBIAR porque se tocó el catálogo.
        assert d - l == esperado(client, h), titulo


def esperado(client, h, _memo={}) -> Decimal:
    """La diferencia de la primera medición: la que no puede moverse después."""
    clave = id(h)
    if clave not in _memo:
        res, panel = resumen(client, h), pedir_lotes(client, h)
        _memo[clave] = costo_segun_desglose(client, h, res) - costo_segun_lotes(panel)
    return _memo[clave]


# ==========================================================================
# LAS DOS PUERTAS QUE FALTABAN, CON PLATA DE VERDAD ENCIMA
# ==========================================================================
def test_crear_un_producto_sobre_una_clave_que_ya_tiene_movimientos(client, h):
    """SIN BORRAR NI REVIVIR NADA: un POST al catálogo movía el ranking.

    'recorte' nunca estuvo en el catálogo —que es exactamente lo que el propio
    módulo dice que pasa con una fila vieja, una importada o una con el tipo escrito
    de otra forma—, así que sus kilos entraron como producto raíz. El día que el
    dueño lo agregaba a la lista marcándolo subproducto del queso, el reparto de la
    ganancia entre productores se recalculaba sobre compras y ventas ya registradas:
    Patricia Rojas de $340.000,00 a -$373.333,33 y Sebastián Ruiz de $50.000,00 a
    $763.333,33.

    Era el mismo agujero que `validar_actualizar` y `validar_eliminar` sí tapan, en
    la puerta que faltaba: CREAR no le preguntaba al repositorio si esa clave ya
    tenía historia. Ahora se lo pregunta, y agregarlo SUELTO —que es la salida que
    dice el mensaje, y como esos kilos ya estaban contados— no mueve una cifra.
    """
    p = productos(client, h)
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="20000")
    compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz", tipo="recorte",
           kilos_brutos="50.00", precio_kilo="1000")
    venta(client, h, fecha="2026-03-10", cliente="Tienda La Esquina", tipo="recorte",
          kilos="50.00", precio_kilo="2000")
    venta(client, h, fecha="2026-03-11", cliente="Don José Pérez", tipo="queso",
          kilos="90.00", precio_kilo="26000")

    antes = foto(client, h)
    res_a = resumen(client, h)
    ranking_antes = [(x["productor"], x["ganancia_estimada"])
                     for x in res_a["por_productor"]]
    print("   ranking ANTES:  ", ranking_antes)

    r = client.post(PROD, json={"nombre": "Recorte", "unidad": "kg",
                                "subproducto_de_id": p["queso"]["id"]}, headers=h)
    print("   agregar 'Recorte' colgado del queso ->", r.status_code, r.text[:220])
    rechazo(r, "1 compra y 1 venta", "sin marcarlo como subproducto")

    crear_producto(client, h, nombre="Recorte", unidad="kg")

    res_d = resumen(client, h)
    print("   ranking DESPUÉS:",
          [(x["productor"], x["ganancia_estimada"]) for x in res_d["por_productor"]])
    movidas = pintar_diferencias(antes, foto(client, h), "agregar 'Recorte'")
    regla_de_oro(res_d, "después de agregar 'Recorte'")
    assert not movidas, f"agregar un producto movió {len(movidas)} cifras"


def test_revivir_un_subproducto_comprado_directamente(client, h):
    """La borona quitada, COMPRADA APARTE con su propio costo, vendida, y vuelta a
    agregar. Es la misma puerta, con la clave teniendo compras propias y no solo
    kilos que le llegaron encima de otro lote."""
    p = productos(client, h)
    assert client.delete(f"{PROD}/{p['borona']['id']}", headers=h).status_code in (200, 204)
    compra(client, h, fecha="2026-03-01", productor="Patricia Rojas",
           kilos_brutos="100.00", precio_kilo="20000")
    compra(client, h, fecha="2026-03-02", productor="Sebastián Ruiz", tipo="borona",
           kilos_brutos="50.00", precio_kilo="1000")
    venta(client, h, fecha="2026-03-10", cliente="Tienda La Esquina", tipo="borona",
          kilos="50.00", precio_kilo="2000")
    venta(client, h, fecha="2026-03-11", cliente="Don José Pérez", tipo="queso",
          kilos="90.00", precio_kilo="26000")

    antes = foto(client, h)
    res_a = resumen(client, h)
    print("   ranking ANTES:  ",
          [(x["productor"], x["ganancia_estimada"]) for x in res_a["por_productor"]])

    r = client.post(PROD, json={"nombre": "Borona", "unidad": "kg",
                                "subproducto_de_id": p["queso"]["id"]}, headers=h)
    print("   revivir la borona colgada del queso ->", r.status_code, r.text[:220])
    rechazo(r, "1 compra y 1 venta", "sin marcarlo como subproducto")

    crear_producto(client, h, nombre="Borona")

    res_d = resumen(client, h)
    print("   ranking DESPUÉS:",
          [(x["productor"], x["ganancia_estimada"]) for x in res_d["por_productor"]])
    movidas = pintar_diferencias(antes, foto(client, h), "revivir la borona comprada")
    regla_de_oro(res_d, "después de revivir")
    assert not movidas, f"revivir la borona movió {len(movidas)} cifras"

# ==========================================================================
# 5. EL REPARTO ES DETERMINISTA
# ==========================================================================
def test_dos_corridas_identicas_dan_las_mismas_cifras(client, h, hb):
    """La misma historia en las dos queseras: cifra por cifra igual."""
    historia_gorda(client, h)
    historia_gorda(client, hb)
    a, b = foto(client, h), foto(client, hb)
    distintas = [(r, a[r], b.get(r)) for r in a if b.get(r) != a[r]]
    for ruta, x, y in distintas[:30]:
        print(f"   DISTINTO {ruta}: A={x}  B={y}")
    assert not distintas, f"{len(distintas)} cifras distintas entre dos corridas iguales"


# CÓMO SE ESCRIBE EL CERO —'0' para lo que se cuenta por piezas y '0.00' para la
# plata y los kilos— NO SE MIDE AQUÍ: es un criterio declarado y defendido en
# tests/test_reventa_como_se_escribe_una_cifra.py, y no tiene nada que ver con que
# se toque el catálogo.
