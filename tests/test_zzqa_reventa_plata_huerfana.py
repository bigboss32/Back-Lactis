"""FRENTE 3: QUE NO QUEDE PLATA HUÉRFANA NI SE DIVIDA ENTRE CERO.

Dos preguntas, y las dos se contestan sobre el resumen COMPLETO:

1. ¿Hay algún camino donde una cifra entre en un total del encabezado y NO aparezca
   en ninguna fila del desglose? El dueño suma esa columna a mano con calculadora:
   si el encabezado dice $200.000 y las filas suman $0, la cifra en la que él
   confía está mal.

2. ¿Hay algún promedio calculado sobre cero? Un "precio promedio por barra" en $0
   con plata al lado es la forma amable de decir que se dividió entre cero, y el
   dueño lo lee como "me la regalaron".

`exigir_cuentas_sanas` es el chequeo completo, y se le pasa a TODOS los casos: el
producto sin movimientos, el que tiene compras y ninguna venta, el que solo tiene
ventas, el desactivado con historia, y la fila con el producto en nulo o apuntando
a un producto que no está en el catálogo.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PROD = f"{API}/productos"
CERO = Decimal("0")
TODO_2026 = {"desde": "2026-01-01", "hasta": "2026-12-31"}


def D(v):
    return Decimal(str(v))


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


# ------------------------------------------------------------------ utilidades
def producto(client, h, nombre, unidad="kg"):
    r = client.post(PROD, json={"nombre": nombre, "unidad": unidad}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def desactivar(client, h, producto_id):
    r = client.put(f"{PROD}/{producto_id}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def compra(client, h, fecha, productor, tipo, kilos, precio, borona=0):
    r = client.post(
        f"{API}/compras",
        json={"fecha": fecha, "productor": productor, "tipo": tipo,
              "kilos_brutos": kilos, "precio_kilo": precio, "borona_kilos": borona},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, h, fecha, cliente, tipo, kilos, precio, gasto=0):
    r = client.post(
        f"{API}/ventas",
        json={"fecha": fecha, "cliente": cliente, "tipo": tipo, "kilos": kilos,
              "precio_kilo": precio, "gasto_por_kilo": gasto},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def resumen(client, h, periodo=None):
    r = client.get(f"{API}/resumen", params=periodo or TODO_2026, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def suma(res, campo):
    return sum((D(f[campo]) for f in res["por_producto"]), CERO)


def pintar(titulo, res):
    print(f"\n===== {titulo} =====")
    for c in ("kilos_comprados", "total_compras", "kilos_vendidos", "total_ventas",
              "total_gastos", "ganancia_estimada", "precio_promedio_compra",
              "precio_promedio_venta", "margen_por_kilo", "valor_realizado_kilo",
              "barras_compradas", "total_compras_mozzarella", "barras_vendidas",
              "total_ventas_mozzarella", "total_gastos_mozzarella",
              "precio_promedio_compra_barra", "precio_promedio_venta_barra",
              "kilos_pendientes", "barras_pendientes"):
        print(f"   {c:30} = {res[c]}")
    print("   desglose:")
    for f in res["por_producto"]:
        print(f"     {f['producto']:22} {f['unidad']:6} kilos={f['kilos']:>9} "
              f"vend={f['kilos_vendidos']:>9} barras={str(f.get('barras')):>6} "
              f"costo={f['costo']:>13} ingreso={f['ingreso']:>13} "
              f"gastos={f['gastos']:>10} ganancia={f['ganancia']:>13} "
              f"pvk={f['precio_venta_kilo']:>10} ck={f['costo_kilo']:>10} "
              f"pvb={str(f.get('precio_venta_barra')):>10} "
              f"cb={str(f.get('costo_barra')):>10}")
    print(f"     {'SUMA':22} {'':6} {'':>9} {'':>9} {'':>6} "
          f"costo={suma(res,'costo'):>13} ingreso={suma(res,'ingreso'):>13} "
          f"gastos={suma(res,'gastos'):>10} ganancia={suma(res,'ganancia'):>13}")


# ------------------------------------------------------- el chequeo completo
def exigir_cuentas_sanas(res, etiqueta):
    """LA REGLA DE ORO más los promedios. Se corre en todos los casos."""
    # ---- 1) el desglose suma EXACTO el encabezado, en las cuatro columnas
    for campo_fila, campo_total in (
        ("costo", "total_compras"),
        ("ingreso", "total_ventas"),
        ("gastos", "total_gastos"),
        ("ganancia", "ganancia_estimada"),
    ):
        assert suma(res, campo_fila) == D(res[campo_total]), (
            f"{etiqueta}: PLATA HUÉRFANA. El desglose suma {suma(res, campo_fila)} en "
            f"'{campo_fila}' y el encabezado dice {res[campo_total]} en '{campo_total}'"
        )

    # ---- 2) la fila de la red de seguridad NO puede aparecer: si aparece, es que
    # hubo plata que ninguna fila de verdad explicó.
    coladas = [f for f in res["por_producto"] if f["producto"] == "sin_producto"]
    assert not coladas, (
        f"{etiqueta}: hubo plata que no cupo en ninguna fila del desglose y salió por "
        f"la red de seguridad: {coladas}"
    )

    # ---- 3) ningún promedio sobre cero. Se comprueba al revés, que es lo que el
    # dueño ve: si hay PLATA, tiene que haber CANTIDAD que la explique.
    compras_kilos = D(res["total_compras"]) - D(res["total_compras_mozzarella"])
    if compras_kilos:
        assert D(res["kilos_comprados"]) != CERO, (
            f"{etiqueta}: hay {compras_kilos} de compras en kilos y "
            f"kilos_comprados = {res['kilos_comprados']}: el precio promedio por "
            f"kilo se está calculando sobre cero"
        )
        assert D(res["precio_promedio_compra"]) != CERO, (
            f"{etiqueta}: hay {compras_kilos} de compras en kilos y el precio "
            f"promedio de compra salió en {res['precio_promedio_compra']}"
        )
    if D(res["total_compras_mozzarella"]):
        assert D(res["barras_compradas"]) != CERO, (
            f"{etiqueta}: hay {res['total_compras_mozzarella']} de compras por unidad "
            f"y barras_compradas = 0: el promedio por barra divide entre cero"
        )
        assert D(res["precio_promedio_compra_barra"]) != CERO, (
            f"{etiqueta}: hay {res['total_compras_mozzarella']} de compras por unidad "
            f"y el precio promedio por barra salió en "
            f"{res['precio_promedio_compra_barra']}"
        )
    if D(res["total_ventas_mozzarella"]):
        assert D(res["barras_vendidas"]) != CERO, (
            f"{etiqueta}: hay {res['total_ventas_mozzarella']} de ventas por unidad y "
            f"barras_vendidas = 0"
        )
        assert D(res["precio_promedio_venta_barra"]) != CERO, (
            f"{etiqueta}: hay {res['total_ventas_mozzarella']} de ventas por unidad y "
            f"el precio promedio de venta por barra salió en "
            f"{res['precio_promedio_venta_barra']}"
        )

    # ---- 3b) EL OTRO DESGLOSE: la columna "ganancia estimada" del ranking de
    # productores también tiene que sumar la tarjeta del período. Tiene UNA
    # excepción declarada y hay que respetarla: sin compras en el período no hay a
    # quién repartirle (la ganancia salió de queso comprado antes), y el propio
    # resumen dice que ahí la columna no suma. Se comprueba cuando sí hay a quién.
    hay_barras = D(res["total_compras_mozzarella"]) or D(res["total_ventas_mozzarella"])
    reparte_kilos = D(res["kilos_comprados"]) != CERO
    reparte_barras = (not hay_barras) or D(res["barras_compradas"]) != CERO
    if reparte_kilos and reparte_barras:
        suma_productores = sum(
            (D(f["ganancia_estimada"]) for f in res["por_productor"]), CERO
        )
        assert suma_productores == D(res["ganancia_estimada"]), (
            f"{etiqueta}: el ranking de productores suma {suma_productores} y la "
            f"tarjeta del período dice {res['ganancia_estimada']}"
        )

    # ---- 4) fila por fila: la que tiene INGRESO tiene que tener cantidad vendida
    # en SU unidad, o su "precio de venta" es un promedio sobre cero.
    for f in res["por_producto"]:
        if D(f["ingreso"]) == CERO:
            continue
        if f["unidad"] == "barra":
            assert D(f["barras_vendidas"]) != CERO, (
                f"{etiqueta}: la fila '{f['producto']}' tiene ingreso {f['ingreso']} y "
                f"cero barras vendidas"
            )
            assert D(f["precio_venta_barra"]) != CERO, (
                f"{etiqueta}: la fila '{f['producto']}' tiene ingreso {f['ingreso']} y "
                f"precio de venta por barra en cero"
            )
        else:
            assert D(f["kilos_vendidos"]) != CERO, (
                f"{etiqueta}: la fila '{f['producto']}' tiene ingreso {f['ingreso']} y "
                f"cero kilos vendidos: su precio de venta por kilo es un promedio "
                f"sobre cero"
            )
            assert D(f["precio_venta_kilo"]) != CERO, (
                f"{etiqueta}: la fila '{f['producto']}' tiene ingreso {f['ingreso']} y "
                f"precio de venta por kilo en cero"
            )


# =========================================================== 1) sin movimientos
def test_producto_sin_movimientos(client, h):
    """Un producto recién agregado y nada más. Nada puede quedar en ningún lado, y
    ningún promedio se puede calcular."""
    producto(client, h, "Panela", unidad="kg")
    producto(client, h, "Huevo", unidad="unidad")
    res = resumen(client, h)
    pintar("producto sin movimientos", res)
    exigir_cuentas_sanas(res, "producto sin movimientos")
    assert D(res["total_compras"]) == CERO
    assert D(res["total_ventas"]) == CERO
    assert D(res["ganancia_estimada"]) == CERO
    assert suma(res, "costo") == CERO


# ================================================== 2) con compras y sin ventas
def test_con_movimientos_y_sin_ventas(client, h):
    """Se compró y no se ha vendido nada: todo el costo tiene que aparecer en la
    fila del inventario pendiente, y no puede haber ni un promedio de venta."""
    compra(client, h, "2026-03-01", "Patricia", "queso", 500, 3000, borona=20)
    res = resumen(client, h)
    pintar("con compras y sin ventas", res)
    exigir_cuentas_sanas(res, "con compras y sin ventas")
    assert D(res["total_compras"]) == D("1500000.00")
    assert D(res["total_ventas"]) == CERO
    pendiente = [f for f in res["por_producto"] if f["producto"] == "pendiente"]
    assert pendiente, "sin ventas, todo el costo va a la fila del inventario pendiente"
    assert D(pendiente[0]["costo"]) == D("1500000.00"), (
        f"el costo del inventario pendiente es {pendiente[0]['costo']} y se compraron "
        f"$1.500.000"
    )
    assert D(res["precio_promedio_venta"]) == CERO
    assert D(res["margen_por_kilo"]) == CERO


# ==================================================== 3) SOLO con ventas
def test_solo_con_ventas_en_el_periodo(client, h):
    """El queso se compró en enero y se vendió en marzo. Si se mira SOLO marzo, el
    período tiene ventas y CERO compras: el residuo sale negativo y su costo es un
    crédito. Ahí es donde el promedio de compra se calcularía sobre cero kilos."""
    compra(client, h, "2026-01-15", "Patricia", "queso", 400, 2000)
    venta(client, h, "2026-03-10", "Don Jose", "queso", 300, 4000, gasto=100)
    solo_marzo = {"desde": "2026-03-01", "hasta": "2026-03-31"}
    res = resumen(client, h, solo_marzo)
    pintar("SOLO marzo (ventas sin compras)", res)
    exigir_cuentas_sanas(res, "solo ventas")
    assert D(res["total_compras"]) == CERO
    assert D(res["total_ventas"]) == D("1200000.00")
    assert D(res["precio_promedio_compra"]) == CERO, (
        "sin compras en el período no hay precio promedio de compra que mostrar"
    )
    anterior = [f for f in res["por_producto"] if f["producto"] == "anterior"]
    assert anterior, "las ventas sin compras dejan el residuo en la fila 'anterior'"
    print("   fila 'anterior':", anterior[0]["etiqueta"], anterior[0]["costo"])


# ============================================= 4) desactivado con historia
def test_producto_desactivado_con_historia(client, h):
    """El dueño deja de manejar un producto que YA tiene compras y ventas. La salida
    es desactivarlo (quitarlo no se puede, y eso hay que comprobarlo también). Su
    historia se queda completa y las cifras no se pueden mover ni un peso."""
    p = producto(client, h, "Panela", unidad="kg")
    compra(client, h, "2026-03-01", "Patricia", "panela", 300, 2000)
    venta(client, h, "2026-03-05", "Don Jose", "panela", 200, 3500, gasto=40)
    antes = resumen(client, h)
    pintar("panela con historia, ANTES de desactivar", antes)

    # No se puede QUITAR con movimientos encima
    r = client.delete(f"{PROD}/{p['id']}", headers=h)
    print("\nintento de quitarla del catálogo:", r.status_code, r.text[:220])
    assert r.status_code == 422, (
        "un producto con movimientos NO se puede quitar del catálogo: si se pudiera, "
        "sus filas quedarían hablando de algo que ya no está en ninguna lista y, peor, "
        "la clasificación por unidad cambiaría de la noche a la mañana"
    )

    desactivada = desactivar(client, h, p["id"])
    assert desactivada["estado"] == "inactivo"
    despues = resumen(client, h)
    pintar("panela con historia, DESPUÉS de desactivar", despues)
    exigir_cuentas_sanas(despues, "producto desactivado con historia")
    movidos = {
        c: (antes[c], despues[c])
        for c in antes
        if c != "por_producto" and antes[c] != despues[c]
    }
    assert not movidos, f"desactivar el producto movió cifras: {movidos}"
    assert antes["por_producto"] == despues["por_producto"], (
        "desactivar el producto cambió el desglose"
    )


def test_producto_por_unidad_desactivado_con_historia(client, h):
    """El mismo caso pero con un producto QUE SE CUENTA. Aquí desactivarlo es más
    delicado: si la clasificación por unidad mirara el estado además de los
    borrados, desactivarlo movería su plata de la canasta de las barras a la de los
    kilos, o sea de un renglón del desglose a otro."""
    # La mozzarella ya viene sembrada en cada empresa: se busca, no se crea.
    lst = client.get(PROD, params={"page": 1, "size": 50}, headers=h)
    assert lst.status_code == 200, lst.text
    p = next(x for x in lst.json()["items"] if x["clave"] == "mozzarella")
    print("\nla mozzarella sembrada:", p["nombre"], "unidad:", p["unidad"])
    assert p["unidad"] == "unidad", "la mozzarella se cuenta por barras"
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-01", "productor": "Patricia", "tipo": "mozzarella",
              "barras": 100, "precio_barra": 2500},
        headers=h,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-05", "cliente": "Don Jose", "tipo": "mozzarella",
              "barras": 60, "precio_barra": 4000, "gasto_por_barra": 100},
        headers=h,
    )
    assert r.status_code == 201, r.text
    antes = resumen(client, h)
    pintar("mozzarella con historia, ANTES de desactivar", antes)
    exigir_cuentas_sanas(antes, "mozzarella con historia")

    desactivar(client, h, p["id"])
    despues = resumen(client, h)
    pintar("mozzarella con historia, DESPUÉS de desactivar", despues)
    exigir_cuentas_sanas(despues, "mozzarella desactivada con historia")
    movidos = {
        c: (antes[c], despues[c])
        for c in antes
        if c != "por_producto" and antes[c] != despues[c]
    }
    assert not movidos, (
        f"desactivar la mozzarella movió cifras del encabezado: {movidos}"
    )
    assert antes["por_producto"] == despues["por_producto"], (
        "desactivar la mozzarella movió su plata de un renglón del desglose a otro"
    )


# ================================= 5) el producto en NULO, en blanco, o fantasma
def test_el_producto_en_nulo_no_cabe_en_la_base(client, db_session, h):
    """"Una fila con el producto en nulo" NO PUEDE EXISTIR, y eso es una buena
    noticia que conviene tener escrita: las dos columnas son NOT NULL, así que la
    base rechaza el UPDATE. No hay que preguntarse a dónde iría su plata porque no
    hay forma de que esa fila llegue a estar ahí, ni por SQL suelto ni por una
    migración."""
    c = compra(client, h, "2026-03-01", "Patricia", "queso", 100, 3000)
    v = venta(client, h, "2026-03-05", "Don Jose", "queso", 80, 5000)
    for tabla, fila in (("compras_queso", c), ("ventas_queso", v)):
        with pytest.raises(Exception) as caida:
            db_session.execute(
                text(f"UPDATE {tabla} SET tipo = NULL WHERE id = :i"),
                {"i": uuid.UUID(fila["id"]).hex},
            )
            db_session.flush()
        print(f"\n{tabla}: la base rechaza el producto en nulo -> "
              f"{type(caida.value).__name__}: {str(caida.value)[:90]}")
        db_session.rollback()


@pytest.mark.parametrize(
    "como_queda,descripcion",
    [
        ("", "en blanco"),
        ("fantasma", "apuntando a un producto que no está en el catálogo"),
    ],
)
def test_fila_con_el_producto_raro(client, db_session, h, como_queda, descripcion):
    """Filas cuyo `tipo` no nombra a ningún producto vivo del catálogo. No se pueden
    crear por el endpoint —se arman a mano, como quedarían por una migración o por
    un SQL suelto—, y lo que hay que exigir es que su plata NO DESAPAREZCA del
    desglose."""
    compra(client, h, "2026-03-01", "Patricia", "queso", 100, 3000)
    venta(client, h, "2026-03-05", "Don Jose", "queso", 80, 5000, gasto=20)
    # Una compra más y una venta más, y a esas dos se les deja el tipo raro
    c = compra(client, h, "2026-03-02", "Sebastian", "queso", 50, 4000)
    v = venta(client, h, "2026-03-06", "Doña Rosa", "queso", 30, 6000, gasto=10)
    # OJO CON EL id EN SQL CRUDO: SQLAlchemy guarda los UUID como 32 caracteres
    # HEX SIN GUIONES, así que un `WHERE id = '...-...'` no encuentra nada y la
    # prueba pasaría sin haber probado nada. Se comprueba que la fila cambió.
    for tabla, fila in (("compras_queso", c), ("ventas_queso", v)):
        r = db_session.execute(
            text(f"UPDATE {tabla} SET tipo = :t WHERE id = :i"),
            {"t": como_queda, "i": uuid.UUID(fila["id"]).hex},
        )
        assert r.rowcount == 1, (
            f"la prueba no alcanzó a dejar el producto {descripcion} en {tabla}"
        )
    db_session.commit()
    quedo = db_session.execute(
        text("SELECT tipo FROM compras_queso WHERE id = :i"),
        {"i": uuid.UUID(c["id"]).hex},
    ).scalar()
    assert quedo == como_queda, f"el tipo quedó en {quedo!r} y se esperaba {como_queda!r}"

    res = resumen(client, h)
    pintar(f"con una compra y una venta con el producto {descripcion}", res)
    exigir_cuentas_sanas(res, f"producto {descripcion}")
    # La plata de las dos filas raras tiene que seguir contada en el encabezado
    assert D(res["total_compras"]) == D("500000.00"), (
        f"se compraron $300.000 + $200.000 y el total dice {res['total_compras']}"
    )
    assert D(res["total_ventas"]) == D("580000.00"), (
        f"se vendieron $400.000 + $180.000 y el total dice {res['total_ventas']}"
    )
    assert D(res["kilos_comprados"]) == D(150), (
        f"se compraron 150 kg y el resumen dice {res['kilos_comprados']}"
    )


# ================== 5b) desde el otro extremo: la base contra el encabezado
def test_ninguna_fila_de_la_base_se_queda_por_fuera_del_encabezado(client, db_session, h):
    """EL CHEQUEO AL REVÉS, y es el más fuerte de todos.

    Los demás miran si el desglose suma el encabezado. Este mira si el ENCABEZADO
    suma LA BASE: se le pregunta a la tabla, con SQL pelado y sin ninguna de las
    condiciones del resumen, cuánto costaron todas las compras del período; y eso
    tiene que ser exactamente `total_compras`.

    Importa porque la clasificación por unidad parte las filas en dos canastas
    (kilos y unidades) y el encabezado las suma. Si alguna fila no cayera en
    NINGUNA de las dos —por ejemplo si una condición se volviera NULA en vez de
    falsa—, su plata no aparecería en ningún total, y ninguna prueba de "el
    desglose suma el encabezado" lo notaría: los dos lados estarían mal por igual.

    Se corre con las dos unidades y con filas de tipo desconocido en la mezcla.
    """
    compra(client, h, "2026-03-01", "Patricia", "queso", 100, 3000, borona=5)
    producto(client, h, "Panela", unidad="kg")
    compra(client, h, "2026-03-02", "Sebastian", "panela", 50, 4000)
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-03-03", "productor": "Aurelio", "tipo": "mozzarella",
              "barras": 30, "precio_barra": 7000},
        headers=h,
    )
    assert r.status_code == 201, r.text
    # Y una fila que habla de un producto que no está en el catálogo
    c = compra(client, h, "2026-03-04", "Patricia", "queso", 20, 1500)
    up = db_session.execute(
        text("UPDATE compras_queso SET tipo='fantasma' WHERE id=:i"),
        {"i": uuid.UUID(c["id"]).hex},
    )
    assert up.rowcount == 1
    db_session.commit()

    crudo = db_session.execute(
        text("SELECT COALESCE(SUM(valor_total), 0) FROM compras_queso "
             "WHERE deleted_at IS NULL AND estado <> 'anulada' "
             "AND fecha BETWEEN '2026-01-01' AND '2026-12-31'")
    ).scalar()
    res = resumen(client, h)
    pintar("cuatro compras de tres clases distintas", res)
    print(f"\n   la base dice que se compró: {D(crudo)}")
    print(f"   el encabezado dice        : {res['total_compras']}")
    assert D(res["total_compras"]) == D(crudo), (
        f"hay filas de compra cuya plata no entró en NINGUNA canasta: la base suma "
        f"{D(crudo)} y el encabezado dice {res['total_compras']}"
    )
    exigir_cuentas_sanas(res, "la base contra el encabezado")

    crudo_ventas = db_session.execute(
        text("SELECT COALESCE(SUM(valor_total), 0) FROM ventas_queso "
             "WHERE deleted_at IS NULL AND estado <> 'anulada' "
             "AND fecha BETWEEN '2026-01-01' AND '2026-12-31'")
    ).scalar()
    assert D(res["total_ventas"]) == D(crudo_ventas)


# ============================ 6) el barrido: muchas formas, un solo invariante
def test_barrido_de_combinaciones(client, h):
    """Un barrido corto por las formas que puede tomar un período, exigiéndole a
    cada una las mismas cuentas sanas. La idea es que si algún camino deja plata
    fuera del desglose o parte entre cero, caiga acá."""
    casos = []

    # solo una compra de barras, sin ventas
    r = client.post(
        f"{API}/compras",
        json={"fecha": "2026-02-01", "productor": "Patricia", "tipo": "mozzarella",
              "barras": 40, "precio_barra": 5000},
        headers=h,
    )
    assert r.status_code == 201, r.text
    casos.append(("solo compra de barras", resumen(client, h)))

    # solo la VENTA de barras en su propio período (compradas antes)
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-05-10", "cliente": "Don Jose", "tipo": "mozzarella",
              "barras": 25, "precio_barra": 8000, "gasto_por_barra": 200},
        headers=h,
    )
    assert r.status_code == 201, r.text
    casos.append(("solo venta de barras (mayo)",
                  resumen(client, h, {"desde": "2026-05-01", "hasta": "2026-05-31"})))
    casos.append(("barras compradas y vendidas (todo el año)", resumen(client, h)))

    # kilos con borona, merma y conversión
    compra(client, h, "2026-06-01", "Sebastian", "queso", 200, 2000, borona=15)
    for destino, kilos in (("borona", 20), ("merma", 5)):
        r = client.post(f"{API}/conversiones",
                        json={"fecha": "2026-06-03", "kilos": kilos, "destino": destino},
                        headers=h)
        assert r.status_code == 201, r.text
    casos.append(("kilos con ajustes y sin ventas",
                  resumen(client, h, {"desde": "2026-06-01", "hasta": "2026-06-30"})))
    venta(client, h, "2026-06-10", "Don Jose", "queso", 150, 3500, gasto=25)
    venta(client, h, "2026-06-11", "Doña Rosa", "borona", 30, 1200)
    casos.append(("kilos completos", resumen(client, h, {"desde": "2026-06-01",
                                                         "hasta": "2026-06-30"})))
    casos.append(("todo junto, las dos unidades", resumen(client, h)))
    # un período totalmente vacío
    casos.append(("período vacío", resumen(client, h, {"desde": "2026-09-01",
                                                       "hasta": "2026-09-30"})))

    for etiqueta, res in casos:
        pintar(etiqueta, res)
        exigir_cuentas_sanas(res, etiqueta)
