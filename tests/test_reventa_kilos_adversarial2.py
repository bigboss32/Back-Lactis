"""Segunda tanda de la auditoría adversarial: pérdida total de la plata por
unidad, colisión de clave entre empresas, anular lo ya vendido, el nombre en el
estado de cuenta, el ancho de la columna `tipo` (defecto que SOLO se ve en
Postgres) y el 500 con el tipo en blanco.
"""
from decimal import Decimal

from app.modules.reventa.models import CompraQueso, ProductoReventa, VentaQueso
from tests.test_reventa_kilos_adversarial import (  # noqa: F401  (fixtures)
    API,
    CERO_D,
    PROD,
    comprar,
    crear_producto,
    detalle,
    h,
    hb,
    imprimir_resumen,
    lotes,
    resumen,
    vender,
)


def test_por_unidad_no_mozzarella_pierde_toda_la_plata(client, h):
    """DEFECTO 11 (CRÍTICO): `CompraQuesoService._calcular` solo sabe de barras
    cuando el tipo es EXACTAMENTE 'mozzarella'. Cualquier otro producto por unidad
    cae en la rama de kilos: barras y precio_barra se ponen en CERO y
    valor_total = 0 x 0 = $0. La compra se guarda por CERO PESOS."""
    r = client.post(PROD, json={"nombre": "Huevo", "unidad": "unidad"}, headers=h)
    assert r.status_code == 201, r.text
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="huevo", barras="30", precio_barra="500")
    assert r.status_code == 201, r.text
    c = r.json()
    print("\nCOMPRA de 30 huevos a $500 (=$15.000):")
    print(f"  barras={c['barras']} precio_barra={c['precio_barra']} "
          f"valor_total={c['valor_total']} unidad={c['unidad']} saldo={c['saldo']}")
    assert Decimal(c["valor_total"]) == CERO_D, "los $15.000 se perdieron"
    assert Decimal(c["barras"]) == CERO_D, "las 30 unidades tampoco se guardaron"

    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="huevo", barras="30", precio_barra="900")
    print("VENTA de 30 huevos a $900 ->", r.status_code,
          "" if r.status_code == 201 else detalle(r))
    assert r.status_code == 201, "sin haber comprado nada: el guardia mide KILOS"
    v = r.json()
    print(f"  barras={v['barras']} valor_total={v['valor_total']} unidad={v['unidad']}")
    assert Decimal(v["valor_total"]) == Decimal("27000.00")

    res = resumen(client, h)
    imprimir_resumen("RESUMEN con un producto por unidad que NO es mozzarella", res)
    for k in ("barras_compradas", "barras_vendidas", "barras_disponibles",
              "total_compras_mozzarella", "total_ventas_mozzarella",
              "precio_promedio_compra_barra", "margen_por_barra"):
        print(f"  {k:28} = {res[k]}")
    assert Decimal(res["barras_compradas"]) == CERO_D
    assert Decimal(res["barras_vendidas"]) == Decimal("30")
    assert Decimal(res["barras_disponibles"]) == Decimal("-30")
    fila_mozza = next(f for f in res["por_producto"] if f["producto"] == "mozzarella")
    print("  fila MOZZARELLA del desglose:", fila_mozza)
    print("  >>> la plata de los huevos aparece como MOZZARELLA y el inventario "
          "de barras queda en -30")


def test_colision_de_clave_entre_empresas_mueve_la_plata_de_la_otra(client, h, hb):
    """DEFECTO 12 (CRÍTICO, multiempresa), ARREGLADO: `se_mide_en_kilos` /
    `se_mide_en_unidades` consultaban productos_reventa SIN empresa_id, así que si
    la OTRA quesera creaba un producto POR UNIDAD con la misma clave, la plata de
    este cliente se mudaba de renglón en su propio resumen.

    La prueba quedó al revés de como nació: mide LO MISMO —el resumen antes y
    después de que la empresa B agregue su producto— pero ahora exige que NO SE
    MUEVA NI UN PESO. Es la forma más directa de decir qué se arregló: el resumen de
    una quesera no puede cambiar por lo que haga la otra, y ninguna de las dos
    subconsultas mira ya fuera de su empresa.

    Y con la plata quieta, el desglose vuelve a sumar la cifra grande: los $400.000
    de ganancia están en las filas y no solo en la tarjeta."""
    crear_producto(client, h, "Costeno")
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="costeno", kilos_brutos="100", precio_kilo="5000")
    assert r.status_code == 201, r.text
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="costeno", kilos="100", precio_kilo="9000")
    assert r.status_code == 201, r.text

    antes = resumen(client, h)
    imprimir_resumen("ANTES: la empresa B no tiene 'costeno'", antes)

    r = client.post(PROD, json={"nombre": "Costeno", "unidad": "unidad"}, headers=hb)
    assert r.status_code == 201, r.text

    despues = resumen(client, h)
    imprimir_resumen("DESPUÉS de que la empresa B creó 'Costeno' por unidad", despues)
    print("\n  DIFERENCIAS en la empresa A, sin que A tocara nada:")
    movidas = [
        k for k in ("kilos_comprados", "total_compras", "kilos_borona_vendidos",
                    "total_ventas_borona", "precio_promedio_compra",
                    "total_compras_mozzarella", "total_ventas_mozzarella",
                    "barras_compradas", "barras_vendidas", "kilos_disponibles",
                    "ganancia_estimada")
        if antes[k] != despues[k]
    ]
    for k in movidas:
        print(f"    {k:26} {antes[k]}  ->  {despues[k]}")
    print(f"    (cifras que se movieron: {len(movidas)})")
    assert movidas == [], f"lo que hizo la empresa B le movió cifras a la A: {movidas}"
    assert antes["por_producto"] == despues["por_producto"]
    print("  >>> el resumen de una quesera no cambia por lo que haga la otra")

    # ------------------------------------------------------- LA REGLA DE ORO
    # Antes cuadraba y después NO: las dos filas de mozzarella solo se emiten
    # `if barras_compradas or barras_vendidas`, y esas filas tenían barras = 0, así
    # que la plata se iba al bloque de barras y ese bloque no imprimía ninguna fila.
    # Ahora cuadra en los dos momentos.
    suma_antes = sum(Decimal(f["ganancia"]) for f in antes["por_producto"])
    suma_despues = sum(Decimal(f["ganancia"]) for f in despues["por_producto"])
    print(f"\n  ANTES:   suma de las filas = {suma_antes}   "
          f"ganancia_estimada = {antes['ganancia_estimada']}")
    print(f"  DESPUÉS: suma de las filas = {suma_despues}   "
          f"ganancia_estimada = {despues['ganancia_estimada']}")
    assert suma_antes == Decimal(antes["ganancia_estimada"]) == Decimal("400000.00")
    assert suma_despues == Decimal(despues["ganancia_estimada"]) == Decimal("400000.00")
    # Y no hizo falta ninguna fila de rescate: la plata nunca se salió de su unidad
    assert "sin_producto" not in [f["producto"] for f in despues["por_producto"]]

    r = vender(client, h, fecha="2026-03-03", cliente="Tienda Sol",
               tipo="costeno", kilos="10", precio_kilo="9000")
    print("  vender su propio costeño en kilos ->", r.status_code, detalle(r))
    assert r.status_code == 201, detalle(r)


def test_anular_la_compra_del_producto_nuevo_despues_de_venderla_toda(client, h):
    """DEFECTO 13: el guardia de anular mide el inventario de QUESO, y las ventas
    del producto nuevo no lo bajan: la compra se anula con todo vendido."""
    crear_producto(client, h, "Costeno")
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="costeno", kilos_brutos="100", precio_kilo="5000")
    compra_id = r.json()["id"]
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="costeno", kilos="100", precio_kilo="9000")
    assert r.status_code == 201, r.text

    r = client.post(f"{API}/compras/{compra_id}/anular", headers=h)
    print("\nANULAR la compra con los 100 kg YA VENDIDOS ->", r.status_code,
          "" if r.status_code in (200, 201) else detalle(r))
    assert r.status_code == 200, "se anuló una compra cuyo producto ya salió"
    res = resumen(client, h)
    imprimir_resumen("RESUMEN con la compra anulada y la venta viva", res)
    print("  >>> queda una venta de $900.000 sin ninguna compra detrás; "
          f"kilos_disponibles = {res['kilos_disponibles']}")
    assert Decimal(res["total_compras"]) == CERO_D
    assert Decimal(res["ganancia_estimada"]) == Decimal("900000.00")


def test_el_estado_de_cuenta_no_dice_el_nombre_del_producto(client, h):
    """DEFECTO 14: el documento que se le entrega al cliente rotula el producto con
    `clave.capitalize()`, no con el nombre del catálogo. Renombrar no cambia nada
    de lo que el cliente lee."""
    p = crear_producto(client, h, "Queso costeno artesanal")
    print("\nclave generada:", p["clave"], f"({len(p['clave'])} caracteres)")
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo=p["clave"], kilos_brutos="100", precio_kilo="5000")
    assert r.status_code == 201, r.text
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo=p["clave"], kilos="50", precio_kilo="9000")
    assert r.status_code == 201, r.text

    r = client.get(f"{API}/estado-cuenta", params={"cliente": "Tienda Sol"}, headers=h)
    assert r.status_code == 200, r.text
    ec = r.json()
    print("ESTADO DE CUENTA que recibe el cliente:")
    for f in ec["ventas"]:
        print(f"  {f['fecha']}  producto='{f['producto']}'  unidad={f['unidad']} "
              f"kilos={f['kilos']} precio={f['precio_kilo']} total={f['valor_total']}")
    assert ec["ventas"][0]["producto"] == "Queso_costeno_artesanal", (
        "el cliente lee la clave con guiones bajos"
    )

    r = client.put(f"{PROD}/{p['id']}", json={"nombre": "Costeno de la casa"}, headers=h)
    assert r.status_code == 200, r.text
    ec = client.get(f"{API}/estado-cuenta", params={"cliente": "Tienda Sol"},
                    headers=h).json()
    print("tras renombrar a 'Costeno de la casa':", ec["ventas"][0]["producto"])
    assert ec["ventas"][0]["producto"] == "Queso_costeno_artesanal"


def test_la_clave_no_cabe_en_la_columna_tipo(client, h):
    """DEFECTO 15 (CRÍTICO EN POSTGRES, invisible en SQLite):
    `productos_reventa.clave` es String(80) y `compras_queso.tipo` /
    `ventas_queso.tipo` son String(20). Un nombre de producto un poco largo genera
    una clave que NO CABE en la columna donde hay que guardarla. SQLite no valida
    el ancho y la prueba pasa; Postgres —la base del cliente— tumba el INSERT con
    'value too long for type character varying(20)' y el dueño ve un 500."""
    assert ProductoReventa.clave.type.length == 80
    assert CompraQueso.tipo.type.length == 20
    assert VentaQueso.tipo.type.length == 20

    p = crear_producto(client, h, "Queso costeno artesanal")
    print(f"\nclave='{p['clave']}' -> {len(p['clave'])} caracteres, "
          f"y compras_queso.tipo admite {CompraQueso.tipo.type.length}")
    assert len(p["clave"]) > 20, "la clave no cabe en la columna tipo"

    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo=p["clave"], kilos_brutos="100", precio_kilo="5000")
    print("COMPRA en SQLITE ->", r.status_code,
          f"tipo guardado='{r.json().get('tipo')}'" if r.status_code < 300 else detalle(r))
    assert r.status_code == 201
    assert len(r.json()["tipo"]) == len(p["clave"]) > 20
    print("  >>> EN POSTGRES este mismo INSERT revienta: la columna es varchar(20)")


def test_tipo_vacio_ya_no_revienta_con_un_500(client, h):
    """DEFECTO 16 (CRÍTICO), ARREGLADO: `TIPO_QUESO` no estaba importado en
    service.py y se usaba dos veces en `CompraQuesoService.preparar_renglones`, así
    que una compra con el tipo en cadena vacía caía con NameError -> 500.

    Se arregló al reescribir ese bloque para la fuga entre empresas (el import
    faltante estaba justo en las dos líneas que había que cambiar), y lo correcto
    es lo que ya decía el código: al renglón sin tipo se le pone 'queso', que es el
    producto de siempre y es además cómo lo lee el resumen (`se_mide_en_kilos`
    cuenta como kilos todo lo que tenga el tipo en blanco). La compra entra en vez
    de tumbarle la pantalla al dueño."""
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="", kilos_brutos="5", precio_kilo="1000")
    print("\nPOST /reventa/compras con tipo='' ->", r.status_code,
          f"tipo guardado='{r.json().get('tipo')}'" if r.status_code < 300 else detalle(r))
    assert r.status_code == 201, detalle(r)
    assert r.json()["tipo"] == "queso"
    assert Decimal(r.json()["valor_total"]) == Decimal("5000.00")
    res = resumen(client, h)
    assert Decimal(res["kilos_comprados"]) == Decimal("5.00")
    assert Decimal(res["total_compras"]) == Decimal("5000.00")
