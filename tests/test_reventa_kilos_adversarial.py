"""AUDITORÍA ADVERSARIAL del lote "un producto del catálogo medido en KILOS se
puede comprar y vender de punta a punta".

No arregla nada: mide. Cada prueba imprime las cifras para que se puedan cuadrar
a mano, y afirma LO QUE EL SISTEMA HACE HOY (no lo que debería hacer), con el
defecto anotado en el docstring. Así el reporte queda con números y las pruebas
se vuelven rojas el día que alguien arregle el defecto.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.modules.reventa.models import CompraQueso, ProductoReventa, VentaQueso
from app.seeds.seed import ensure_catalogos_empresas
from tests.conftest import auth_headers

API = "/api/v1/reventa"
PROD = f"{API}/productos"


def detalle(r) -> str:
    cuerpo = r.json()
    if isinstance(cuerpo, dict) and "error" in cuerpo:
        return str(cuerpo["error"].get("detail", cuerpo["error"]))
    return str(cuerpo)


@pytest.fixture()
def h(client, base_datos, db_session):
    ensure_catalogos_empresas(db_session)
    db_session.commit()
    return auth_headers(client, "admin.a")


@pytest.fixture()
def hb(client, base_datos, db_session):
    ensure_catalogos_empresas(db_session)
    db_session.commit()
    return auth_headers(client, "admin.b")


def crear_producto(client, headers, nombre, **extra):
    r = client.post(PROD, json={"nombre": nombre, **extra}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def comprar(client, headers, **datos):
    return client.post(f"{API}/compras", json=datos, headers=headers)


def vender(client, headers, **datos):
    return client.post(f"{API}/ventas", json=datos, headers=headers)


def resumen(client, headers, desde="2026-01-01", hasta="2026-12-31"):
    r = client.get(f"{API}/resumen", params={"desde": desde, "hasta": hasta}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def lotes(client, headers):
    r = client.get(f"{API}/lotes", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def imprimir_resumen(titulo, res):
    print(f"\n===== {titulo} =====")
    for k in (
        "kilos_comprados", "total_compras", "kilos_vendidos", "total_ventas",
        "precio_promedio_compra", "precio_promedio_venta",
        "kilos_borona_vendidos", "total_ventas_borona",
        "kilos_disponibles", "borona_disponible", "kilos_pendientes",
        "ganancia_estimada", "margen_por_kilo",
        "por_cobrar_clientes", "por_pagar_productores",
    ):
        print(f"  {k:26} = {res[k]}")
    print("  por_producto:")
    for f in res["por_producto"]:
        print(
            f"    {f['producto']:22} kilos={f.get('kilos')} vendidos={f.get('kilos_vendidos')} "
            f"ingreso={f.get('ingreso')} costo={f.get('costo')} ganancia={f.get('ganancia')}"
        )


# ===========================================================================
# 1. PUNTA A PUNTA: comprar y vender un producto nuevo por kilo
# ===========================================================================
def test_producto_nuevo_por_kilo_punta_a_punta(client, h):
    """DEFECTO 1 y 2: la plata del producto nuevo se le acredita a la BORONA en el
    resumen, y sus kilos vendidos NO bajan el inventario de queso.

    Compra: 100 kg de 'Costeño' a $5.000 = $500.000
    Venta:   60 kg de 'Costeño' a $9.000 = $540.000
    """
    crear_producto(client, h, "Costeno")
    r = comprar(
        client, h,
        fecha="2026-03-01", productor="Pedro Perez", tipo="costeno",
        kilos_brutos="100", precio_kilo="5000",
    )
    print("\nCOMPRA costeno ->", r.status_code, r.json() if r.status_code < 300 else detalle(r))
    assert r.status_code == 201
    compra = r.json()
    assert Decimal(compra["valor_total"]) == Decimal("500000.00")
    assert compra["tipo"] == "costeno"
    assert compra["unidad"] == "kg"

    r = vender(
        client, h,
        fecha="2026-03-05", cliente="Tienda Sol", tipo="costeno",
        kilos="60", precio_kilo="9000",
    )
    print("VENTA costeno ->", r.status_code, r.json() if r.status_code < 300 else detalle(r))
    assert r.status_code == 201
    assert Decimal(r.json()["valor_total"]) == Decimal("540000.00")

    res = resumen(client, h)
    imprimir_resumen("RESUMEN con un producto nuevo por kilo", res)

    # --- DEFECTO 1: no hay fila del producto nuevo. Su venta cayó en 'borona'.
    productos = [f["producto"] for f in res["por_producto"]]
    assert "costeno" not in productos, "si aparece, el defecto 1 está arreglado"
    fila_borona = next(f for f in res["por_producto"] if f["producto"] == "borona")
    assert Decimal(fila_borona["ingreso"]) == Decimal("540000.00"), (
        "los $540.000 del costeño se le acreditaron a la BORONA"
    )
    assert Decimal(res["kilos_borona_vendidos"]) == Decimal("60.00")
    # Y el queso, que es lo que de verdad se vendió cero, dice cero.
    fila_queso = next(f for f in res["por_producto"] if f["producto"] == "queso")
    assert Decimal(fila_queso["ingreso"]) == CERO_D

    # --- DEFECTO 2: los 100 kg comprados entraron al inventario de QUESO y los
    # 60 vendidos no salieron de ninguno.
    assert Decimal(res["kilos_disponibles"]) == Decimal("100.00"), (
        "se compraron 100 y se vendieron 60: el disponible debería ser 40"
    )
    print("\n  >>> kilos_disponibles dice 100.00 y en bodega quedan 40.00 kg")


CERO_D = Decimal("0")


# ===========================================================================
# 2. VENDER SIN HABER COMPRADO / VENDER DE MÁS
# ===========================================================================
def test_vender_producto_nuevo_sin_haberlo_comprado_sale_del_queso(client, h):
    """DEFECTO 3: la venta de un producto nuevo se valida y se costea contra el
    inventario de QUESO. Con queso en bodega se puede vender un producto que nunca
    se compró, y el kilo de queso desaparece de la ganancia del queso."""
    crear_producto(client, h, "Costeno")
    # 100 kg de QUESO comprados a $20.000
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="queso", kilos_brutos="100", precio_kilo="20000")
    assert r.status_code == 201, r.text

    # Nunca se compró costeño, pero se venden 80 kg: pasa.
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="costeno", kilos="80", precio_kilo="9000")
    print("\nVENTA de costeno SIN haberlo comprado ->", r.status_code,
          "" if r.status_code == 201 else detalle(r))
    assert r.status_code == 201, "pasa: se validó contra el inventario de queso"

    panel = lotes(client, h)
    print("\n===== PANEL DE LOTES =====")
    print("  kilos_sin_lote     =", panel["kilos_sin_lote"])
    print("  borona_sin_lote    =", panel["borona_sin_lote"])
    print("  ingreso_sin_lote   =", panel["ingreso_sin_lote"])
    for lote in panel["lotes"]:
        print(f"  lote {lote['fecha']}: comprados={lote['kilos_comprados']} "
              f"vendidos={lote['kilos_vendidos']} ingreso_queso={lote['ingreso_queso']} "
              f"costo_vendido={lote['costo_vendido']} ganancia={lote['ganancia']} "
              f"sin_vender={lote['kilos_sin_vender']}")

    lote = panel["lotes"][0]
    # El FIFO le cargó al queso los 80 kg del costeño: costo 80 x 20.000
    assert Decimal(lote["kilos_vendidos"]) == Decimal("80.00")
    assert Decimal(lote["costo_vendido"]) == Decimal("1600000.00")
    assert Decimal(lote["ingreso_queso"]) == Decimal("720000.00")
    assert Decimal(lote["ganancia"]) == Decimal("-880000.00")
    print("\n  >>> el lote de queso muestra una PÉRDIDA de $880.000 por una venta "
          "de un producto que nunca se compró")

    # Y el disponible de queso NO bajó: los 80 kg siguen ahí para volverse a vender.
    res = resumen(client, h)
    assert Decimal(res["kilos_disponibles"]) == Decimal("100.00")
    r = vender(client, h, fecha="2026-03-03", cliente="Tienda Sol",
               tipo="queso", kilos="100", precio_kilo="25000")
    print("\nVENTA de 100 kg de QUESO despues de haber vendido 80 como costeno ->",
          r.status_code, "" if r.status_code == 201 else detalle(r))
    assert r.status_code == 201, "se despacharon 180 kg de una compra de 100"
    res = resumen(client, h)
    imprimir_resumen("RESUMEN: 100 kg comprados, 180 kg vendidos", res)
    assert Decimal(res["kilos_disponibles"]) == CERO_D
    print("  >>> se vendieron 180 kg de una compra de 100 y el disponible dice 0")


def test_vender_de_mas_el_producto_nuevo(client, h):
    """DEFECTO 4: el guardia mide contra el queso, no contra lo comprado del
    producto. Comprar 10 kg de costeño autoriza a vender 10 kg... y también a
    vender los kilos de queso que haya, y al revés."""
    crear_producto(client, h, "Costeno")
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="costeno", kilos_brutos="10", precio_kilo="5000")
    assert r.status_code == 201, r.text

    # Vender 40 kg de un producto del que solo se compraron 10: el mensaje habla
    # de "queso", que no es lo que el dueño está vendiendo.
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="costeno", kilos="40", precio_kilo="9000")
    print("\nVENDER 40 kg de costeno con 10 comprados ->", r.status_code, detalle(r))
    assert r.status_code in (400, 422), r.status_code
    assert "queso" in detalle(r).lower(), "el mensaje habla de queso, no del costeño"

    # Pero 10 de costeño + 10 de queso comprados dejan vender 20 de costeño.
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="queso", kilos_brutos="10", precio_kilo="20000")
    assert r.status_code == 201, r.text
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="costeno", kilos="20", precio_kilo="9000")
    print("VENDER 20 kg de costeno con 10 de costeno + 10 de queso ->",
          r.status_code, "" if r.status_code == 201 else detalle(r))
    assert r.status_code == 201, "los 10 kg de queso pagaron por el costeño"
    print("  >>> los inventarios NO están separados: el queso respalda al costeño")


def test_dos_renglones_del_producto_nuevo_en_la_misma_factura(client, h):
    """El guardia de la factura suma los renglones del mismo tipo (eso sí sirve),
    pero los mide contra el queso."""
    crear_producto(client, h, "Costeno")
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="costeno", kilos_brutos="100", precio_kilo="5000")
    assert r.status_code == 201, r.text
    payload = {
        "tipo": "venta", "fecha": "2026-03-05", "tercero": "Tienda Sol",
        "renglones": [
            {"tipo": "costeno", "kilos": "70", "precio_kilo": "9000"},
            {"tipo": "costeno", "kilos": "60", "precio_kilo": "9500"},
        ],
    }
    r = client.post(f"{API}/documentos", json=payload, headers=h)
    print("\nFACTURA de 70 + 60 kg de costeno con 100 comprados ->",
          r.status_code, detalle(r))
    assert r.status_code in (400, 422), r.status_code
    assert "130" in detalle(r) and "2 renglones" in detalle(r)

    payload["renglones"] = [
        {"tipo": "costeno", "kilos": "40", "precio_kilo": "9000"},
        {"tipo": "costeno", "kilos": "60", "precio_kilo": "9500"},
    ]
    r = client.post(f"{API}/documentos", json=payload, headers=h)
    assert r.status_code == 201, r.text
    doc = r.json()
    print("FACTURA 40 + 60 kg ->", doc["total"], [x["valor_total"] for x in doc["renglones"]])
    assert Decimal(doc["total"]) == Decimal("930000.00")

    res = resumen(client, h)
    imprimir_resumen("RESUMEN factura de dos renglones del producto nuevo", res)
    assert Decimal(res["kilos_disponibles"]) == Decimal("100.00"), (
        "se vendió el lote completo y el disponible sigue en 100"
    )


# ===========================================================================
# 3. EL SUBPRODUCTO QUE NO SE PAGA (como la borona)
# ===========================================================================
def test_subproducto_nuevo_no_tiene_donde_recibir_lo_que_llega_gratis(client, h):
    """DEFECTO 5: un producto nuevo marcado como subproducto NO puede llegar junto
    con su padre. `borona_kilos` es una columna sola, cableada a la clave 'borona':
    lo que llegue de un subproducto nuevo entra al inventario de BORONA."""
    queso = next(p for p in client.get(PROD, headers=h).json()["items"]
                 if p["clave"] == "queso")
    suero = crear_producto(client, h, "Cuajada suelta", subproducto_de_id=queso["id"])
    print("\nSUBPRODUCTO nuevo:", suero["clave"], "subproducto_de=", suero["subproducto_de_nombre"])

    # Se compra queso y llegan 20 kg de cuajada gratis: no hay campo para decirlo.
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez", tipo="queso",
                kilos_brutos="100", precio_kilo="20000", borona_kilos="20")
    assert r.status_code == 201, r.text
    res = resumen(client, h)
    print("  borona_disponible tras 'borona_kilos=20' =", res["borona_disponible"])
    assert Decimal(res["borona_disponible"]) == Decimal("20.00"), (
        "los 20 kg de cuajada quedaron contados como BORONA"
    )

    # Vender la cuajada: se costea contra el QUESO (cola_queso), no contra la cola
    # de costo cero. Así la venta NO suma completa a la ganancia.
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="cuajada_suelta", kilos="20", precio_kilo="3000")
    print("VENTA de 20 kg de cuajada_suelta ->", r.status_code,
          "" if r.status_code == 201 else detalle(r))
    assert r.status_code == 201
    panel = lotes(client, h)
    lote = panel["lotes"][0]
    print("\n===== LOTE con el subproducto nuevo =====")
    print(f"  kilos_vendidos={lote['kilos_vendidos']} ingreso_queso={lote['ingreso_queso']} "
          f"costo_vendido={lote['costo_vendido']} borona_vendida={lote['borona_vendida']} "
          f"ingreso_borona={lote['ingreso_borona']} ganancia={lote['ganancia']}")
    # Con la borona de verdad esto daría ingreso_borona=60.000 y costo 0.
    assert Decimal(lote["ingreso_borona"]) == CERO_D
    assert Decimal(lote["borona_vendida"]) == CERO_D
    assert Decimal(lote["ingreso_queso"]) == Decimal("60000.00")
    assert Decimal(lote["costo_vendido"]) == Decimal("400000.00")
    print("  >>> la cuajada 'gratis' se costeó a $20.000/kg: $400.000 de costo "
          "contra $60.000 de ingreso. Como borona serían $60.000 de ganancia pura")

    # Comparación con la BORONA de verdad, en el mismo escenario.
    r = vender(client, h, fecha="2026-03-03", cliente="Tienda Sol",
               tipo="borona", kilos="20", precio_kilo="3000")
    assert r.status_code == 201, r.text
    panel = lotes(client, h)
    lote = panel["lotes"][0]
    print(f"  tras vender la BORONA de verdad: borona_vendida={lote['borona_vendida']} "
          f"ingreso_borona={lote['ingreso_borona']} costo_borona_vendida="
          f"{lote['costo_borona_vendida']}")
    assert Decimal(lote["ingreso_borona"]) == Decimal("60000.00")
    assert Decimal(lote["costo_borona_vendida"]) == CERO_D


def test_cadena_prohibida_de_dos_niveles(client, h):
    """Esto SÍ está bien puesto: la cadena de subproductos se corta en un nivel."""
    items = client.get(PROD, headers=h).json()["items"]
    borona = next(p for p in items if p["clave"] == "borona")
    r = client.post(PROD, json={"nombre": "Migaja", "subproducto_de_id": borona["id"]},
                    headers=h)
    print("\nSUBPRODUCTO de la borona ->", r.status_code, detalle(r))
    assert r.status_code in (400, 422), r.status_code
    assert "un nivel" in detalle(r)


# ===========================================================================
# 4. DESACTIVAR Y RENOMBRAR CON MOVIMIENTOS ENCIMA
# ===========================================================================
def test_desactivar_y_renombrar_un_producto_con_movimientos(client, h):
    """DEFECTO 6: desactivar NO impide seguir comprando y vendiendo con esa clave;
    el nombre nuevo no aparece en ninguna parte del dinero (todo habla por la
    clave)."""
    p = crear_producto(client, h, "Costeno")
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="costeno", kilos_brutos="100", precio_kilo="5000")
    assert r.status_code == 201, r.text

    # No se puede quitar: correcto.
    r = client.delete(f"{PROD}/{p['id']}", headers=h)
    print("\nQUITAR un producto con movimientos ->", r.status_code, detalle(r))
    assert r.status_code in (400, 422), r.status_code and "desactívelo" in detalle(r)

    # Desactivar
    r = client.put(f"{PROD}/{p['id']}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "inactivo"
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="costeno", kilos="10", precio_kilo="9000")
    print("VENDER un producto DESACTIVADO ->", r.status_code,
          "" if r.status_code == 201 else detalle(r))
    assert r.status_code == 201, "el producto está desactivado y se sigue vendiendo"

    # Renombrar: la clave no se mueve (correcto) pero el nombre no llega al dinero.
    r = client.put(f"{PROD}/{p['id']}", json={"nombre": "Costeno artesanal"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["clave"] == "costeno"
    compras = client.get(f"{API}/compras", headers=h).json()["items"]
    print("RENOMBRADO a 'Costeno artesanal'; la compra sigue diciendo tipo =",
          compras[0]["tipo"])
    assert compras[0]["tipo"] == "costeno"
    res = resumen(client, h)
    nombres = [f["producto"] for f in res["por_producto"]]
    print("  filas del resumen:", nombres)
    assert "Costeno artesanal" not in nombres and "costeno" not in nombres
    print("  >>> el nombre nuevo no aparece ni en la compra ni en el resumen")


# ===========================================================================
# 5. UN PRODUCTO POR UNIDAD
# ===========================================================================
def test_producto_por_unidad_mensaje_entendible(client, h):
    """Qué pasa hoy con un producto POR UNIDAD: se puede crear y se puede vender."""
    p = client.post(PROD, json={"nombre": "Huevo", "unidad": "unidad"}, headers=h)
    print("\nCREAR producto por unidad ->", p.status_code, detalle(p))
    if p.status_code != 201:
        pytest.skip("no se pueden crear productos por unidad")
    assert p.json()["decimales"] == 0 and p.json()["admite_ajustes"] is False

    # Con kilos: rebota con mensaje.
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="huevo", kilos_brutos="10", precio_kilo="500")
    print("COMPRAR 'huevo' con KILOS ->", r.status_code, detalle(r))
    assert r.status_code in (400, 422), r.status_code

    # Con barras/unidades: entra.
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="huevo", barras="30", precio_barra="500")
    print("COMPRAR 30 'huevo' por unidad ->", r.status_code,
          r.json() if r.status_code < 300 else detalle(r))
    assert r.status_code == 201
    # OJO: la plata se pierde. Medido aparte en
    # test_por_unidad_no_mozzarella_pierde_toda_la_plata (defecto 11).
    assert Decimal(r.json()["valor_total"]) == CERO_D

    # DEFECTO 7: el guardia de existencias de un producto por unidad que no es
    # mozzarella mide contra el inventario de QUESO (kilos).
    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="huevo", barras="30", precio_barra="900")
    print("VENDER 30 'huevo' sin queso en bodega ->", r.status_code, detalle(r))
    res = resumen(client, h)
    imprimir_resumen("RESUMEN con un producto por unidad que no es mozzarella", res)
    print("  barras_disponibles =", res["barras_disponibles"])


# ===========================================================================
# 6. PRODUCTO DE OTRA EMPRESA / INVENTADO / NULO
# ===========================================================================
def test_producto_de_otra_empresa_en_un_renglon(client, h, hb, db_session):
    """DEFECTO 8, ARREGLADO: `preparar_renglones` consultaba productos_reventa SIN
    filtrar por empresa_id ni por deleted_at, así que la unidad de un producto de
    OTRA empresa decidía la validación de esta.

    Antes esta compra rebotaba con un 422 que decía "una compra de panela necesita
    las barras y el precio por barra", hablando de un producto que en el catálogo de
    la empresa A no existe. Ahora la unidad se pregunta al catálogo PROPIO (ver
    `ProductoReventaRepository.unidades_por_clave`) y la compra pasa, medida en
    kilos como se pidió. El caso completo, con las cifras de la plata que se
    movía, está en tests/test_reventa_fuga_entre_empresas.py."""
    # La empresa B crea 'Panela' POR UNIDAD. La empresa A no la tiene.
    r = client.post(PROD, json={"nombre": "Panela", "unidad": "unidad"}, headers=hb)
    assert r.status_code == 201, r.text
    claves_a = [p["clave"] for p in client.get(PROD, headers=h).json()["items"]]
    print("\ncatálogo de la empresa A:", claves_a)
    assert "panela" not in claves_a

    # La empresa A compra 'panela' EN KILOS: el producto de la empresa B ya no
    # opina sobre sus renglones.
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="panela", kilos_brutos="50", precio_kilo="4000")
    print("empresa A compra 'panela' en KILOS ->", r.status_code, detalle(r))
    assert r.status_code == 201, (
        "la fila de OTRA empresa decidió la unidad de esta compra: " + detalle(r)
    )
    compra = r.json()
    assert compra["unidad"] == "kg"
    assert Decimal(compra["kilos_netos"]) == Decimal("50.00")
    assert Decimal(compra["valor_total"]) == Decimal("200000.00")
    # Y su plata cuenta como plata de kilos en SU resumen
    res = resumen(client, h)
    assert Decimal(res["kilos_comprados"]) == Decimal("50.00")
    assert Decimal(res["total_compras_mozzarella"]) == CERO_D
    print("  >>> la unidad la pone el catálogo de la propia empresa")


def test_producto_inventado_y_tipo_nulo(client, h):
    """DEFECTO 9: un tipo que no existe en el catálogo se acepta en silencio como
    kilos (`productos.get(tipo, "kg")`), y crea un producto fantasma con plata."""
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="no_existe_este_producto", kilos_brutos="100", precio_kilo="7000")
    print("\nCOMPRAR un tipo INVENTADO ->", r.status_code,
          r.json() if r.status_code < 300 else detalle(r))
    assert r.status_code == 201, "se aceptó un producto que no está en el catálogo"
    assert Decimal(r.json()["valor_total"]) == Decimal("700000.00")

    r = vender(client, h, fecha="2026-03-02", cliente="Tienda Sol",
               tipo="tampoco_existe", kilos="100", precio_kilo="9000")
    print("VENDER un tipo INVENTADO ->", r.status_code,
          "" if r.status_code == 201 else detalle(r))
    assert r.status_code == 201

    res = resumen(client, h)
    imprimir_resumen("RESUMEN con dos productos FANTASMA", res)
    productos = [f["producto"] for f in res["por_producto"]]
    assert "no_existe_este_producto" not in productos
    assert "tampoco_existe" not in productos
    assert "Sin producto" not in productos and "sin_producto" not in productos
    fila_borona = next(f for f in res["por_producto"] if f["producto"] == "borona")
    print("  >>> los $900.000 de un producto inventado se le acreditaron a la "
          f"BORONA: {fila_borona['ingreso']}")
    assert Decimal(fila_borona["ingreso"]) == Decimal("900000.00")

    # tipo nulo
    r = comprar(client, h, fecha="2026-03-03", productor="Pedro Perez",
                tipo=None, kilos_brutos="5", precio_kilo="1000")
    print("COMPRAR con tipo NULO ->", r.status_code,
          r.json().get("tipo") if r.status_code < 300 else detalle(r))
    # El tipo VACÍO revienta con un 500 (NameError): medido en
    # test_tipo_vacio_revienta_con_un_500 (defecto 16).


# ===========================================================================
# 7. CONVERSIÓN ENTRE PRODUCTOS QUE NO SON PADRE E HIJO
# ===========================================================================
def test_conversion_no_conoce_los_productos(client, h):
    """DEFECTO 10: `/conversiones` no recibe producto: siempre saca del queso y
    siempre mete a la borona, sin importar el catálogo."""
    crear_producto(client, h, "Costeno")
    r = comprar(client, h, fecha="2026-03-01", productor="Pedro Perez",
                tipo="costeno", kilos_brutos="100", precio_kilo="5000")
    assert r.status_code == 201, r.text

    # Nunca se compró queso; hay 100 kg de costeño. La conversión pasa igual.
    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-02", "kilos": "30",
                          "destino": "borona", "precio_kilo": "3000"},
                    headers=h)
    print("\nCONVERTIR 30 kg a borona sin haber comprado queso ->",
          r.status_code, "" if r.status_code < 300 else detalle(r))
    assert r.status_code == 201, "el costeño se desmenuzó como si fuera queso"
    res = resumen(client, h)
    imprimir_resumen("RESUMEN tras convertir kilos de otro producto a borona", res)
    assert Decimal(res["borona_disponible"]) == Decimal("30.00")
    assert Decimal(res["kilos_disponibles"]) == Decimal("70.00")
    print("  >>> 30 kg de 'costeño' se volvieron BORONA vendible sin que exista "
          "ninguna relación padre-hijo entre los dos productos")
