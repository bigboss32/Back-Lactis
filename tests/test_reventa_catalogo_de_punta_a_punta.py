"""EL CATÁLOGO DE REVENTA, DE PUNTA A PUNTA: comprar, vender, ajustar y cuadrar.

DE DÓNDE SALE ESTE ARCHIVO. Es la auditoría adversarial que se le hizo al arreglo que
conectó el catálogo de productos a los cálculos de plata, escrita para MEDIR y no para
arreglar: cada prueba exige el comportamiento bueno y lo imprime con cifras, para que
un defecto no se pueda esconder detrás de un "pasó".

Cubre el recorrido completo de un producto que el dueño agrega —un producto por kilo y
uno por unidad—: su compra, su venta, su inventario, su fila del desglose, su lugar en
el ranking de productores y en el panel de lotes. Y los ataques: vender sin comprar,
vender de más por un centavo, dos renglones de la misma factura que juntos no caben,
editar, anular y borrar, desactivar y renombrar un producto con historia encima, los
ids de la otra quesera y el tipo en blanco.
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


@pytest.fixture()
def hb(client, base_datos):
    return auth_headers(client, "admin.b")


def producto(client, h, nombre, unidad="kg", **extra):
    r = client.post(PROD, json={"nombre": nombre, "unidad": unidad, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def resumen(client, h, **params):
    r = client.get(f"{API}/resumen", params={**PERIODO, **params}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def detalle(r) -> str:
    cuerpo = r.json()
    if isinstance(cuerpo, dict) and "error" in cuerpo:
        return str(cuerpo["error"].get("detail", cuerpo["error"]))
    return str(cuerpo)


def fila(res, clave):
    filas = [f for f in res["por_producto"] if f["producto"] == clave]
    assert len(filas) == 1, (
        f"se esperaba UNA fila de {clave} y hay {len(filas)}: "
        + str([f["producto"] for f in res["por_producto"]])
    )
    return filas[0]


def hay_fila(res, clave):
    return [f for f in res["por_producto"] if f["producto"] == clave]


def existencia(res, clave):
    filas = [e for e in res["existencias"] if e["producto"] == clave]
    assert len(filas) == 1, f"no hay existencias de {clave}: {res['existencias']}"
    return filas[0]


def pintar(titulo, res, campos=()):
    print(f"\n===== {titulo} =====")
    for c in campos:
        print(f"   {c:32} = {res[c]}")
    print("   desglose:")
    for f in res["por_producto"]:
        print(f"     {f['producto']:26} {f['etiqueta']:38} {f['unidad']:6} "
              f"kilos={f['kilos']:>9} barras={str(f.get('barras')):>6} "
              f"costo={f['costo']:>13} ingreso={f['ingreso']:>13} "
              f"ganancia={f['ganancia']:>13}")
    print("   existencias:")
    for e in res["existencias"]:
        print(f"     {e['producto']:26} {e['unidad']:6} {e['disponible']:>10}")


def regla_de_oro(res, titulo):
    for campo, columna in (
        ("total_compras", "costo"),
        ("total_ventas", "ingreso"),
        ("total_gastos", "gastos"),
        ("ganancia_estimada", "ganancia"),
    ):
        suma = sum((D(f[columna]) for f in res["por_producto"]), CERO)
        assert suma == D(res[campo]), (
            f"{titulo}: la columna '{columna}' suma {suma} y '{campo}' dice "
            f"{res[campo]}"
        )


def comprar(client, h, **kw):
    return client.post(f"{API}/compras", json={"fecha": "2026-03-01", **kw}, headers=h)


def vender(client, h, **kw):
    return client.post(f"{API}/ventas", json={"fecha": "2026-03-05", **kw}, headers=h)


def lotes(client, h):
    r = client.get(f"{API}/lotes", headers=h)
    assert r.status_code == 200, r.text
    salida = {}
    for lote in r.json()["lotes"]:
        for c in lote["detalle_compras"]:
            salida[f"{lote['fecha']} {c['productor']}"] = {
                k: D(c[k]) for k in (
                    "kilos_vendidos", "kilos_sin_vender", "costo_realizado",
                    "ingresos", "ganancia", "costo_sin_vender",
                )
            }
    return salida


# =========================================================== A. punta a punta
def test_a1_producto_por_unidad_de_punta_a_punta(client, h):
    """Un producto POR UNIDAD que el dueño crea: cantidad, costo, ganancia, fila,
    inventario, ranking de productores. 500 huevos a $500 = $250.000; se venden 200
    a $900 = $180.000; costo de lo vendido = 200 × $500 = $100.000."""
    producto(client, h, "Huevo", unidad="unidad")
    r = comprar(client, h, productor="Patricia", tipo="huevo", barras=500,
                precio_barra=500)
    assert r.status_code == 201, detalle(r)
    c = r.json()
    print("\ncompra guardada:", {k: c[k] for k in (
        "tipo", "unidad", "barras", "precio_barra", "kilos_netos", "precio_kilo",
        "valor_total", "saldo")})
    assert D(c["barras"]) == D(500) and D(c["valor_total"]) == D("250000.00")
    assert D(c["kilos_netos"]) == CERO and D(c["precio_kilo"]) == CERO

    r = vender(client, h, cliente="Don Jose", tipo="huevo", barras=200,
               precio_barra=900, gasto_por_barra=20)
    assert r.status_code == 201, detalle(r)
    v = r.json()
    assert D(v["barras"]) == D(200) and D(v["valor_total"]) == D("180000.00")
    assert D(v["gasto_monto"]) == D("4000.00")

    res = resumen(client, h)
    pintar("500 huevos comprados, 200 vendidos", res, (
        "total_compras", "total_ventas", "total_gastos", "ganancia_estimada",
        "kilos_comprados", "kilos_vendidos", "barras_compradas", "barras_vendidas",
    ))
    assert D(res["total_compras"]) == D("250000.00")
    assert D(res["total_ventas"]) == D("180000.00")
    assert D(res["kilos_comprados"]) == CERO and D(res["kilos_vendidos"]) == CERO
    # No es mozzarella: los campos de la mozzarella no se enteran.
    assert D(res["barras_compradas"]) == CERO
    assert D(res["barras_vendidas"]) == CERO

    vendido = fila(res, "huevo")
    assert D(vendido["barras"]) == D(200)
    assert D(vendido["ingreso"]) == D("180000.00")
    assert D(vendido["costo"]) == D("100000.00")
    assert D(vendido["gastos"]) == D("4000.00")
    assert D(vendido["ganancia"]) == D("76000.00")
    quedan = fila(res, "huevo_pendiente")
    assert D(quedan["barras"]) == D(300)
    assert D(quedan["costo"]) == D("150000.00")
    assert D(existencia(res, "huevo")["disponible"]) == D(300)
    regla_de_oro(res, "huevo de punta a punta")

    # El ranking de productores suma la ganancia del período.
    suma = sum((D(f["ganancia_estimada"]) for f in res["por_productor"]), CERO)
    assert suma == D(res["ganancia_estimada"]), (
        f"el ranking por productor suma {suma} y la ganancia dice "
        f"{res['ganancia_estimada']}"
    )
    de_patricia = next(f for f in res["por_productor"] if f["productor"] == "Patricia")
    print("   por productor:", de_patricia)
    assert D(de_patricia["total_comprado"]) == D("250000.00")


def test_a2_producto_por_kilo_de_punta_a_punta_cuadra_con_lotes(client, h):
    """Un producto POR KILO nuevo: el resumen, el panel de lotes y la ganancia por
    día tienen que contar LA MISMA plata."""
    producto(client, h, "Panela")
    r = comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=300,
                precio_kilo=2000)
    assert r.status_code == 201, detalle(r)
    r = vender(client, h, cliente="Don Jose", tipo="panela", kilos=250,
               precio_kilo=3500, gasto_por_kilo=40)
    assert r.status_code == 201, detalle(r)

    res = resumen(client, h)
    pintar("300 kg de panela, 250 vendidos", res, (
        "total_compras", "total_ventas", "total_gastos", "ganancia_estimada",
        "kilos_pendientes",
    ))
    f = fila(res, "panela")
    assert D(f["ingreso"]) == D("875000.00")
    assert D(f["costo"]) == D("500000.00")
    assert D(f["gastos"]) == D("10000.00")
    assert D(f["ganancia"]) == D("365000.00")
    assert D(existencia(res, "panela")["disponible"]) == D(50)
    regla_de_oro(res, "panela de punta a punta")

    porlote = lotes(client, h)
    print("   lotes:", porlote)
    de_patricia = porlote["2026-03-01 Patricia"]
    assert de_patricia["kilos_vendidos"] == D(250)
    assert de_patricia["kilos_sin_vender"] == D(50)
    assert de_patricia["costo_realizado"] == D("500000.00")
    assert de_patricia["ingresos"] == D("875000.00")
    assert de_patricia["costo_sin_vender"] == D("100000.00")

    r = client.get(f"{API}/ganancia-por-dia", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    print("   ganancia por día:", r.json()["ganancia"])
    assert D(r.json()["ganancia"]) == D("365000.00"), (
        "la ganancia realizada del día es ingreso − costo − gastos de lo vendido"
    )


# ============================================== B. inventario: los cuatro ataques
def test_b1_vender_sin_comprar_en_las_dos_unidades(client, h):
    producto(client, h, "Panela")
    producto(client, h, "Huevo", unidad="unidad")
    r = vender(client, h, cliente="Don Jose", tipo="panela", kilos=1, precio_kilo=1)
    print("\nvender 1 kg de panela sin comprar ->", r.status_code, detalle(r))
    assert r.status_code == 422 and "Panela" in detalle(r)
    r = vender(client, h, cliente="Don Jose", tipo="huevo", barras=1, precio_barra=1)
    print("vender 1 huevo sin comprar ->", r.status_code, detalle(r))
    assert r.status_code == 422 and "Huevo" in detalle(r)
    res = resumen(client, h)
    assert D(res["total_ventas"]) == CERO
    assert D(existencia(res, "panela")["disponible"]) == CERO
    assert D(existencia(res, "huevo")["disponible"]) == CERO
    assert D(existencia(res, "mozzarella")["disponible"]) == CERO, (
        "el intento de vender huevos dejó negativa la canasta de la mozzarella"
    )


def test_b2_vender_de_mas_por_un_centavo(client, h):
    """El límite exacto: con 100,00 kg se pueden vender 100,00 y no 100,01."""
    producto(client, h, "Panela")
    assert comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=100,
                   precio_kilo=2000).status_code == 201
    r = vender(client, h, cliente="Don Jose", tipo="panela", kilos="100.01",
               precio_kilo=3000)
    print("\nvender 100,01 kg de 100 ->", r.status_code, detalle(r))
    assert r.status_code == 422
    r = vender(client, h, cliente="Don Jose", tipo="panela", kilos="100.00",
               precio_kilo=3000)
    assert r.status_code == 201, detalle(r)
    res = resumen(client, h)
    assert D(existencia(res, "panela")["disponible"]) == CERO
    regla_de_oro(res, "el límite exacto")


def test_b3_dos_renglones_del_mismo_producto_por_unidad(client, h):
    """La factura suma los renglones del MISMO producto POR UNIDAD antes de
    comparar. 60 + 50 huevos con 100 comprados no puede pasar."""
    producto(client, h, "Huevo", unidad="unidad")
    assert comprar(client, h, productor="Patricia", tipo="huevo", barras=100,
                   precio_barra=500).status_code == 201
    payload = {
        "tipo": "venta", "fecha": "2026-03-05", "tercero": "Don Jose",
        "renglones": [
            {"tipo": "huevo", "barras": 60, "precio_barra": 900},
            {"tipo": "huevo", "barras": 50, "precio_barra": 950},
        ],
    }
    r = client.post(f"{API}/documentos", json=payload, headers=h)
    print("\nfactura de 60 + 50 huevos con 100 comprados ->", r.status_code,
          detalle(r))
    assert r.status_code == 422, "la factura despachó 110 huevos de 100"
    assert "110" in detalle(r) and "2 renglones" in detalle(r)
    assert "Huevo" in detalle(r) and "unidades" in detalle(r)

    payload["renglones"][1]["barras"] = 40
    r = client.post(f"{API}/documentos", json=payload, headers=h)
    assert r.status_code == 201, detalle(r)
    res = resumen(client, h)
    pintar("factura de dos renglones de huevo", res, ("total_ventas",))
    assert D(existencia(res, "huevo")["disponible"]) == CERO
    assert D(res["total_ventas"]) == D("92000.00")  # 60×900 + 40×950
    regla_de_oro(res, "dos renglones por unidad")


def test_b4_dos_facturas_cada_una_cabe_pero_juntas_no(client, h):
    """Dos facturas seguidas, cada una dentro del disponible por su cuenta: la
    segunda tiene que rebotar porque la primera YA bajó el inventario."""
    producto(client, h, "Panela")
    assert comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=100,
                   precio_kilo=2000).status_code == 201
    hechas = []
    for vuelta in range(2):
        r = client.post(
            f"{API}/documentos",
            json={"tipo": "venta", "fecha": "2026-03-05", "tercero": "Don Jose",
                  "renglones": [{"tipo": "panela", "kilos": 80, "precio_kilo": 3000}]},
            headers=h,
        )
        print(f"\nfactura {vuelta + 1} de 80 kg (comprados 100) ->", r.status_code,
              detalle(r) if r.status_code != 201 else "")
        hechas.append(r.status_code)
    assert hechas == [201, 422], f"se despacharon 160 kg de 100: {hechas}"
    res = resumen(client, h)
    assert D(existencia(res, "panela")["disponible"]) == D(20)
    assert D(res["total_ventas"]) == D("240000.00")
    regla_de_oro(res, "dos facturas")


# ============================================ C. lo que llega gratis y la cadena
def test_c1_lo_que_llega_gratis_no_cuesta_y_lo_convertido_hereda(client, h):
    """La borona que LLEGA GRATIS con el lote no cuesta (su venta es ganancia pura);
    la que sale de CONVERTIR queso hereda el costo del queso y se lo quita.

    100 kg de queso a $20.000 = $2.000.000, con 20 kg de borona gratis.
    Se convierten 10 kg de queso a borona: esos 10 kg valen $200.000.
    Se venden los 30 kg de borona a $3.000 = $90.000.
    """
    r = comprar(client, h, productor="Pedro Perez", tipo="queso", kilos_brutos=100,
                precio_kilo=20000, borona_kilos=20)
    assert r.status_code == 201, detalle(r)
    r = client.post(f"{API}/conversiones",
                    json={"fecha": "2026-03-02", "kilos": 10, "destino": "borona"},
                    headers=h)
    assert r.status_code == 201, detalle(r)
    r = vender(client, h, cliente="Tienda Sol", tipo="borona", kilos=30,
               precio_kilo=3000)
    assert r.status_code == 201, detalle(r)

    res = resumen(client, h)
    pintar("borona: 20 gratis + 10 convertidos, 30 vendidos", res, (
        "kilos_a_borona", "kilos_borona_vendidos", "total_ventas_borona",
        "ganancia_estimada",
    ))
    de_borona = fila(res, "borona")
    # La columna `kilos` de la fila de la borona es lo que CONSUME del pozo (los
    # convertidos), y `kilos_vendidos` lo que salió vendido. Los 20 gratis no
    # consumen nada.
    assert D(de_borona["kilos"]) == D(10)
    assert D(de_borona["kilos_vendidos"]) == D(30)
    assert D(res["kilos_borona_vendidos"]) == D(30)
    assert D(de_borona["ingreso"]) == D("90000.00")
    assert D(de_borona["costo"]) == D("200000.00"), (
        "la borona consume del pozo los kilos CONVERTIDOS (10 × $20.000), no los "
        "vendidos: los otros 20 llegaron gratis"
    )
    assert D(existencia(res, "borona")["disponible"]) == CERO
    assert D(existencia(res, "queso")["disponible"]) == D(90)
    regla_de_oro(res, "gratis y convertido")


def test_c2_la_cadena_de_dos_niveles_se_rechaza(client, h):
    """Un subproducto de un subproducto no lo sabe costear el reparto: se rechaza."""
    cat = client.get(PROD, params={"size": 50}, headers=h).json()["items"]
    borona = next(p for p in cat if p["clave"] == "borona")
    queso = next(p for p in cat if p["clave"] == "queso")
    r = client.post(PROD, json={"nombre": "Migaja", "unidad": "kg",
                                "subproducto_de_id": borona["id"]}, headers=h)
    print("\ncrear 'Migaja' como subproducto de la BORONA ->", r.status_code,
          detalle(r))
    assert r.status_code in (400, 422), "se creó una cadena de dos niveles"

    # Y al revés: un producto que ya tiene subproductos no puede volverse
    # subproducto de otro.
    nuevo = producto(client, h, "Sobrante", subproducto_de_id=queso["id"])
    r = client.put(f"{PROD}/{queso['id']}",
                   json={"subproducto_de_id": nuevo["id"]}, headers=h)
    print("volver al QUESO subproducto de 'Sobrante' ->", r.status_code, detalle(r))
    assert r.status_code in (400, 422), "se cerró el ciclo queso -> sobrante -> queso"


def test_c3_un_subproducto_propio_no_regala_inventario(client, h):
    """Un subproducto que el dueño crea NO tiene de dónde recibir mercancía gratis:
    venderlo sin haberlo comprado ni convertido tiene que rebotar, no salir de la
    bodega de su padre."""
    costeno = producto(client, h, "Costeno")
    producto(client, h, "Recorte", subproducto_de_id=costeno["id"])
    assert comprar(client, h, productor="Pedro Perez", tipo="costeno",
                   kilos_brutos=100, precio_kilo=5000).status_code == 201
    r = vender(client, h, cliente="Tienda Sol", tipo="recorte", kilos=10,
               precio_kilo=2000)
    print("\nvender 10 kg de 'Recorte' con 100 kg del padre en bodega ->",
          r.status_code, detalle(r))
    assert r.status_code == 422, (
        "se vendió un subproducto que nunca entró: salió de la bodega del padre"
    )
    res = resumen(client, h)
    assert D(existencia(res, "costeno")["disponible"]) == D(100)
    regla_de_oro(res, "subproducto propio sin existencias")


def test_c4_comprar_directamente_el_subproducto_le_deja_su_costo(client, h):
    """SI SE COMPRA BORONA DIRECTAMENTE, su costo tiene que quedar en SU fila.

    50 kg de borona comprados a $1.000 = $50.000, y vendidos a $2.000 = $100.000.
    La ganancia de esa borona son $50.000, no $100.000, y el queso no puede quedar
    con 50 kg "aún en inventario" que nadie compró.
    """
    r = comprar(client, h, productor="Pedro Perez", tipo="borona", kilos_brutos=50,
                precio_kilo=1000)
    assert r.status_code == 201, detalle(r)
    r = vender(client, h, cliente="Tienda Sol", tipo="borona", kilos=50,
               precio_kilo=2000)
    assert r.status_code == 201, detalle(r)

    res = resumen(client, h)
    pintar("50 kg de BORONA comprados y vendidos", res, (
        "total_compras", "total_ventas", "ganancia_estimada", "kilos_pendientes",
    ))
    de_borona = fila(res, "borona")
    assert D(de_borona["ingreso"]) == D("100000.00")
    assert D(de_borona["costo"]) == D("50000.00"), (
        "la borona que se COMPRÓ cuesta lo que se pagó por ella; con costo cero su "
        "renglón muestra ganancia pura y el costo se le carga a un inventario de "
        "queso que no existe"
    )
    assert D(fila(res, "pendiente")["kilos"]) == CERO, (
        "el queso no se movió: no puede quedar 'aún en inventario' con kilos"
    )
    assert D(existencia(res, "borona")["disponible"]) == CERO
    regla_de_oro(res, "borona comprada")


# ==================================================== D. rehacer los renglones
def montar_reparto(client, h):
    """Dos compras del MISMO día a precios muy distintos y una venta que se lleva
    más de lo que trajo la primera: el orden del día decide de quién sale el resto."""
    r = client.post(f"{API}/documentos",
                    json={"tipo": "compra", "fecha": "2026-03-01",
                          "tercero": "Patricia",
                          "renglones": [{"kilos_brutos": 100, "precio_kilo": 1000}]},
                    headers=h)
    assert r.status_code == 201, r.text
    primera = r.json()
    r = client.post(f"{API}/documentos",
                    json={"tipo": "compra", "fecha": "2026-03-01",
                          "tercero": "Sebastian",
                          "renglones": [{"kilos_brutos": 100, "precio_kilo": 5000}]},
                    headers=h)
    assert r.status_code == 201, r.text
    r = vender(client, h, cliente="Don Jose", tipo="queso", kilos=150,
               precio_kilo=8000, gasto_por_kilo=100)
    assert r.status_code == 201, detalle(r)
    return primera


def test_d1_rehacer_con_datos_distintos_no_cambia_el_puesto(client, h):
    """Rehacer los renglones CAMBIÁNDOLES el precio no le puede cambiar el PUESTO a
    la factura: los 100 kg de Patricia siguen siendo los primeros del día."""
    factura = montar_reparto(client, h)
    antes = lotes(client, h)
    print("\nantes:", antes)
    assert antes["2026-03-01 Patricia"]["kilos_vendidos"] == D(100), "montaje malo"

    r = client.put(f"{API}/documentos/{factura['id']}",
                   json={"tipo": "compra",
                         "renglones": [{"kilos_brutos": 100, "precio_kilo": 1200}]},
                   headers=h)
    assert r.status_code == 200, detalle(r)
    despues = lotes(client, h)
    print("después (precio 1000 -> 1200):", despues)
    # Los kilos que cubre cada productor NO se mueven: solo cambia la plata de la
    # compra que se corrigió.
    assert despues["2026-03-01 Patricia"]["kilos_vendidos"] == D(100), (
        "corregirle el precio a la factura de la mañana le movió el puesto: sus "
        "kilos dejaron de servir la venta"
    )
    assert despues["2026-03-01 Sebastian"]["kilos_vendidos"] == D(50)
    assert despues["2026-03-01 Sebastian"]["kilos_sin_vender"] == D(50)
    assert despues["2026-03-01 Patricia"]["costo_realizado"] == D("120000.00")


def test_d2_rehacer_una_factura_de_venta_no_mueve_el_reparto(client, h):
    """La misma puerta, del lado de las VENTAS: rehacer los renglones de la venta de
    la mañana no le puede cambiar el puesto frente a la venta de la tarde."""
    for quien, precio in (("Patricia", 1000), ("Sebastian", 5000)):
        assert comprar(client, h, productor=quien, tipo="queso", kilos_brutos=100,
                       precio_kilo=precio).status_code == 201
    r = client.post(f"{API}/documentos",
                    json={"tipo": "venta", "fecha": "2026-03-05",
                          "tercero": "Don Jose",
                          "renglones": [{"kilos": 100, "precio_kilo": 8000}]},
                    headers=h)
    assert r.status_code == 201, detalle(r)
    manana = r.json()
    r = client.post(f"{API}/documentos",
                    json={"tipo": "venta", "fecha": "2026-03-05",
                          "tercero": "Tienda Sol",
                          "renglones": [{"kilos": 100, "precio_kilo": 9000}]},
                    headers=h)
    assert r.status_code == 201, detalle(r)

    antes = lotes(client, h)
    print("\nantes:", antes)
    r = client.put(f"{API}/documentos/{manana['id']}",
                   json={"tipo": "venta",
                         "renglones": [{"kilos": 100, "precio_kilo": 8000}]},
                   headers=h)
    assert r.status_code == 200, detalle(r)
    despues = lotes(client, h)
    print("después de rehacer la venta de la mañana:", despues)
    movidos = {k: (antes[k], despues[k]) for k in antes if antes[k] != despues[k]}
    assert not movidos, f"rehacer la venta movió el reparto: {movidos}"


# ================================= E. editar, anular y borrar; el inventario vuelve
def test_e1_editar_anular_y_borrar_devuelven_el_inventario_exacto(client, h):
    """Con un producto nuevo por kilo: editar la venta, anularla y borrarla tienen
    que dejar el inventario EXACTAMENTE donde estaba."""
    producto(client, h, "Panela")
    assert comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=100,
                   precio_kilo=2000).status_code == 201
    r = vender(client, h, cliente="Don Jose", tipo="panela", kilos=40,
               precio_kilo=3000)
    assert r.status_code == 201, detalle(r)
    venta_id = r.json()["id"]
    assert D(existencia(resumen(client, h), "panela")["disponible"]) == D(60)

    # EDITAR hacia arriba dentro de lo que hay
    r = client.put(f"{API}/ventas/{venta_id}", json={"kilos": 90}, headers=h)
    assert r.status_code == 200, detalle(r)
    assert D(existencia(resumen(client, h), "panela")["disponible"]) == D(10)
    # EDITAR por encima de lo que hay: rebota y no deja rastro
    r = client.put(f"{API}/ventas/{venta_id}", json={"kilos": 130}, headers=h)
    print("\neditar la venta a 130 kg con 100 comprados ->", r.status_code,
          detalle(r))
    assert r.status_code == 422
    assert D(existencia(resumen(client, h), "panela")["disponible"]) == D(10)

    # ANULAR devuelve los 90
    r = client.post(f"{API}/ventas/{venta_id}/anular", headers=h)
    assert r.status_code == 200, detalle(r)
    res = resumen(client, h)
    pintar("después de anular la venta de 90 kg", res, ("total_ventas",))
    assert D(existencia(res, "panela")["disponible"]) == D(100)
    assert D(res["total_ventas"]) == CERO
    regla_de_oro(res, "venta anulada")

    # BORRAR no puede volver a devolverlos (ya se devolvieron al anular)
    r = client.delete(f"{API}/ventas/{venta_id}", headers=h)
    assert r.status_code in (200, 204), detalle(r)
    res = resumen(client, h)
    assert D(existencia(res, "panela")["disponible"]) == D(100), (
        "borrar una venta ya anulada devolvió los kilos DOS veces"
    )
    regla_de_oro(res, "venta borrada")


def test_e2_borrar_la_factura_entera_devuelve_el_inventario(client, h):
    producto(client, h, "Huevo", unidad="unidad")
    assert comprar(client, h, productor="Patricia", tipo="huevo", barras=100,
                   precio_barra=500).status_code == 201
    r = client.post(f"{API}/documentos",
                    json={"tipo": "venta", "fecha": "2026-03-05",
                          "tercero": "Don Jose",
                          "renglones": [
                              {"tipo": "huevo", "barras": 30, "precio_barra": 900},
                              {"tipo": "huevo", "barras": 20, "precio_barra": 900},
                          ]},
                    headers=h)
    assert r.status_code == 201, detalle(r)
    doc = r.json()
    assert D(existencia(resumen(client, h), "huevo")["disponible"]) == D(50)

    r = client.delete(f"{API}/documentos/{doc['id']}", headers=h)
    assert r.status_code in (200, 204), detalle(r)
    res = resumen(client, h)
    pintar("factura de 2 renglones de huevo borrada", res, ("total_ventas",))
    assert D(existencia(res, "huevo")["disponible"]) == D(100)
    assert D(res["total_ventas"]) == CERO
    regla_de_oro(res, "factura borrada")


def test_e3_anular_la_compra_de_un_producto_nuevo_baja_su_inventario(client, h):
    producto(client, h, "Panela")
    r = comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=100,
                precio_kilo=2000)
    compra_id = r.json()["id"]
    assert vender(client, h, cliente="Don Jose", tipo="panela", kilos=30,
                  precio_kilo=3000).status_code == 201
    r = client.post(f"{API}/compras/{compra_id}/anular", headers=h)
    print("\nanular la compra con 30 kg ya vendidos ->", r.status_code, detalle(r))
    assert r.status_code == 422, "se anuló una compra cuya mercancía ya salió"
    assert "Panela" in detalle(r)

    # Con la venta anulada, la compra sí se puede anular y el inventario queda en 0
    ventas = client.get(f"{API}/ventas", headers=h).json()["items"]
    assert client.post(f"{API}/ventas/{ventas[0]['id']}/anular",
                       headers=h).status_code == 200
    r = client.post(f"{API}/compras/{compra_id}/anular", headers=h)
    assert r.status_code == 200, detalle(r)
    res = resumen(client, h)
    pintar("compra y venta anuladas", res, ("total_compras", "total_ventas"))
    assert D(existencia(res, "panela")["disponible"]) == CERO
    assert D(res["total_compras"]) == CERO and D(res["total_ventas"]) == CERO
    regla_de_oro(res, "todo anulado")


# ============================== F. desactivar y renombrar un producto con movimientos
def test_f1_desactivar_un_producto_no_le_cambia_la_unidad_ni_la_plata(client, h):
    """DESACTIVAR es la salida del dueño cuando ya no maneja un producto. Su
    historia no se puede mover ni un peso, y sobre todo no puede dejar de ser por
    unidad: si el catálogo dejara de verlo, sus barras se leerían como kilos."""
    p = producto(client, h, "Huevo", unidad="unidad")
    assert comprar(client, h, productor="Patricia", tipo="huevo", barras=100,
                   precio_barra=500).status_code == 201
    assert vender(client, h, cliente="Don Jose", tipo="huevo", barras=40,
                  precio_barra=900).status_code == 201
    antes = resumen(client, h)
    pintar("antes de desactivar el huevo", antes, ("total_compras", "total_ventas"))

    r = client.put(f"{PROD}/{p['id']}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, detalle(r)
    despues = resumen(client, h)
    pintar("después de desactivar el huevo", despues,
           ("total_compras", "total_ventas"))
    assert fila(despues, "huevo")["unidad"] == fila(antes, "huevo")["unidad"]
    for campo in ("total_compras", "total_ventas", "ganancia_estimada",
                  "kilos_comprados", "kilos_vendidos"):
        assert D(despues[campo]) == D(antes[campo]), (
            f"desactivar el producto movió {campo}: {antes[campo]} -> "
            f"{despues[campo]}"
        )
    assert D(existencia(despues, "huevo")["disponible"]) == D(60)
    regla_de_oro(despues, "producto desactivado")


def test_f2_renombrar_no_mueve_ni_un_peso_y_si_cambia_el_rotulo(client, h):
    p = producto(client, h, "Panela")
    assert comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=100,
                   precio_kilo=2000).status_code == 201
    assert vender(client, h, cliente="Don Jose", tipo="panela", kilos=40,
                  precio_kilo=3000).status_code == 201
    antes = resumen(client, h)
    r = client.put(f"{PROD}/{p['id']}", json={"nombre": "Panela de la casa"},
                   headers=h)
    assert r.status_code == 200, detalle(r)
    assert r.json()["clave"] == "panela", "la clave es la identidad y no se mueve"
    despues = resumen(client, h)
    pintar("después de renombrar", despues, ("total_compras", "total_ventas"))
    assert fila(despues, "panela")["etiqueta"] == "Vendido como Panela de la casa"
    for campo in ("total_compras", "total_ventas", "ganancia_estimada"):
        assert D(despues[campo]) == D(antes[campo])
    for f_antes, f_despues in zip(antes["por_producto"], despues["por_producto"]):
        assert D(f_antes["costo"]) == D(f_despues["costo"])
        assert D(f_antes["ingreso"]) == D(f_despues["ingreso"])
    regla_de_oro(despues, "producto renombrado")


# ============================================ G. los ids raros y el producto en nulo
def test_g1_un_producto_de_otra_empresa_no_manda_aqui(client, h, hb):
    """La quesera B crea 'Panela' POR UNIDAD. En la quesera A, que no la tiene, una
    compra de 'panela' no puede calcularse con la unidad del vecino."""
    producto(client, hb, "Panela", unidad="unidad")
    r = comprar(client, h, productor="Patricia", tipo="panela", kilos_brutos=100,
                precio_kilo=2000)
    print("\ncompra de 'panela' en la quesera A (la B la tiene por unidad) ->",
          r.status_code, detalle(r))
    assert r.status_code == 201, detalle(r)
    assert D(r.json()["valor_total"]) == D("200000.00"), (
        "la unidad del catálogo del vecino puso la plata en cero"
    )
    res = resumen(client, h)
    pintar("quesera A con una clave que solo el vecino tiene", res,
           ("total_compras",))
    assert D(res["total_compras"]) == D("200000.00")
    regla_de_oro(res, "clave del vecino")
    # Y a la quesera B no le llegó nada.
    res_b = resumen(client, hb)
    assert D(res_b["total_compras"]) == CERO


def test_g2_subproducto_de_id_de_otra_empresa_y_id_inventado(client, h, hb):
    import uuid as _uuid
    ajeno = producto(client, hb, "Ajeno")
    r = client.post(PROD, json={"nombre": "Mio", "unidad": "kg",
                                "subproducto_de_id": ajeno["id"]}, headers=h)
    print("\nsubproducto_de_id de OTRA empresa ->", r.status_code, detalle(r))
    assert r.status_code in (404, 422), "se colgó de un producto de la otra quesera"
    r = client.post(PROD, json={"nombre": "Mio", "unidad": "kg",
                                "subproducto_de_id": str(_uuid.uuid4())}, headers=h)
    print("subproducto_de_id inventado ->", r.status_code, detalle(r))
    assert r.status_code in (404, 422)
    r = client.post(PROD, json={"nombre": "Mio", "unidad": "kg",
                                "subproducto_de_id": None}, headers=h)
    assert r.status_code == 201, detalle(r)


def test_g3_el_tipo_en_nulo_entra_como_el_producto_de_siempre(client, h):
    r = comprar(client, h, productor="Pedro Perez", tipo=None, kilos_brutos=10,
                precio_kilo=1000)
    print("\ncompra con tipo=null ->", r.status_code, detalle(r))
    # El esquema lo rechaza con un mensaje, que también es una respuesta buena:
    # lo que no puede es entrar y quedar mal contado.
    assert r.status_code == 422, detalle(r)
    r = comprar(client, h, productor="Pedro Perez", tipo="", kilos_brutos=10,
                precio_kilo=1000)
    assert r.status_code == 201, detalle(r)
    assert r.json()["tipo"] == "queso"
    assert D(r.json()["valor_total"]) == D("10000.00")
    r = client.post(f"{API}/documentos",
                    json={"tipo": "venta", "fecha": "2026-03-05",
                          "tercero": "Don Jose",
                          "renglones": [{"tipo": None, "kilos": 10,
                                         "precio_kilo": 2000}]},
                    headers=h)
    print("renglón con tipo=null ->", r.status_code, detalle(r))
    assert r.status_code == 422, detalle(r)
    r = client.post(f"{API}/documentos",
                    json={"tipo": "venta", "fecha": "2026-03-05",
                          "tercero": "Don Jose",
                          "renglones": [{"kilos": 10, "precio_kilo": 2000}]},
                    headers=h)
    assert r.status_code == 201, detalle(r)
    res = resumen(client, h)
    pintar("tipo en nulo", res, ("total_compras", "total_ventas"))
    assert D(res["total_compras"]) == D("10000.00")
    assert D(res["total_ventas"]) == D("20000.00")
    assert D(existencia(res, "queso")["disponible"]) == CERO
    regla_de_oro(res, "tipo en nulo")


# ================================================ H. la plata que quede sin producto
def test_h1_la_plata_sin_producto_sale_en_su_fila_y_no_desaparece(client, h):
    """Una fila que quedó CONTADA en unidades pero sin unidades —una compra por
    unidad con barras en cero, que es como quedaban antes del arreglo— tiene plata y
    no cae en ninguna fila de producto. Tiene que salir en 'Sin producto'."""
    from app.modules.reventa.models import CompraQueso

    producto(client, h, "Huevo", unidad="unidad")
    r = comprar(client, h, productor="Patricia", tipo="huevo", barras=10,
                precio_barra=500)
    assert r.status_code == 201, detalle(r)
    compra_id = r.json()["id"]

    # Se rompe la fila POR DEBAJO de la API, que es la única forma de que exista:
    # plata sin cantidad en ninguna de las dos unidades.
    import uuid as _uuid
    from tests.conftest import TestingSessionLocal
    with TestingSessionLocal() as s:
        fila_db = s.get(CompraQueso, _uuid.UUID(compra_id))
        fila_db.barras = Decimal("0")
        fila_db.valor_total = Decimal("5000.00")
        s.commit()

    res = resumen(client, h)
    pintar("una compra con plata y sin cantidad", res,
           ("total_compras", "ganancia_estimada"))
    assert D(res["total_compras"]) == D("5000.00")
    suma = sum((D(f["costo"]) for f in res["por_producto"]), CERO)
    assert suma == D("5000.00"), (
        f"la columna del costo suma {suma} y el encabezado dice {res['total_compras']}"
    )
    regla_de_oro(res, "plata sin producto")
