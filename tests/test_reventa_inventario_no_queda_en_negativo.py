"""LA BODEGA DE REVENTA NUNCA QUEDA EN NEGATIVO — el ataque, puerta por puerta.

El inventario de reventa NO es decorativo: es el guardia que impide vender lo que no
se compró, y el divisor del costeo FIFO. Si queda en negativo, el dueño se queda sin
poder registrar sus ventas y el costo de lo que vendió deja de tener de dónde salir.

Se atacan las CUATRO puertas por las que una compra puede achicarse después de que su
queso ya salió: editarla, anularla, borrar el renglón y borrar la factura entera. Y de
paso los ajustes (conversiones), que también sacan kilos de la bodega.

VINO DE UNA REVISIÓN Y DESTAPÓ DOS HUECOS, medidos contra la API: borrar una compra de
137,45 kg con 120,23 vendidos respondía 204 y dejaba el queso en -120,23 kg, y borrar
un ajuste de 44,23 kg con 40,00 de borona vendidos dejaba la borona en -40,00. Las dos
puertas ya están cerradas (ver `CompraQuesoService.exigir_bodega_al_quitar` y
`ConversionBoronaService.validar_eliminar`); estas pruebas se quedan como el ataque que
las encontró, para que no se vuelvan a abrir. Cada regla con su salida para el dueño
está en test_reventa_borrar_lo_que_ya_se_vendio.py.
"""
from decimal import Decimal

import pytest

from tests.ayudas_reventa import (
    API,
    PERIODO,
    CERO,
    D,
    crear_producto,
    regla_de_oro,
    resumen,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


def existencias(client, h) -> dict:
    res = resumen(client, h)
    return {e["producto"]: D(e["disponible"]) for e in res["existencias"]}


def comprar(client, h, **campos):
    return client.post(f"{API}/compras", json=campos, headers=h)


def vender(client, h, **campos):
    return client.post(f"{API}/ventas", json=campos, headers=h)


def _historia_corta(client, h):
    """Se compran 137,45 kg y se venden 120,23. Quedan 17,22 kg."""
    c = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                kilos_brutos="137.45", precio_kilo="14317")
    assert c.status_code == 201, c.text
    v = vender(client, h, fecha="2026-02-12", cliente="Don José Pérez",
               tipo="queso", kilos="120.23", precio_kilo="21533",
               gasto_por_kilo="137")
    assert v.status_code == 201, v.text
    return c.json(), v.json()


def _mostrar(client, h, titulo):
    disp = existencias(client, h)
    res = resumen(client, h)
    print(f"   [{titulo}] existencias {dict(disp)}  "
          f"kilos_comprados {res['kilos_comprados']}  "
          f"kilos_vendidos {res['kilos_vendidos']}  "
          f"total_compras {res['total_compras']}")
    return disp


# ------------------------------------------------------------- BORRAR el renglón
def test_borrar_una_compra_cuyo_queso_ya_se_vendio(client, h):
    compra, _ = _historia_corta(client, h)
    print("\n   antes de borrar:")
    _mostrar(client, h, "antes")
    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    print(f"   DELETE /compras/{{id}} de una compra ya vendida -> {r.status_code} "
          f"{r.text[:200]}")
    disp = _mostrar(client, h, "después")
    assert disp.get("queso", CERO) >= CERO, (
        f"la bodega quedó en {disp.get('queso')} kg de queso: borrar una compra cuyo "
        f"queso YA SE VENDIÓ dejó el inventario en negativo"
    )
    assert r.status_code >= 400, (
        "se pudo BORRAR una compra cuyo queso ya se vendió (anularla sí está "
        "protegido, borrarla no)"
    )
    # Y el rechazo no le dejó la bodega tocada: los 17,22 kg que no se han vendido
    # siguen ahí y se pueden despachar. Eso es lo que estaba en juego — con el
    # inventario en negativo, NINGUNA venta volvía a pasar el control.
    assert disp["queso"] == D("17.22")
    otra_venta = vender(client, h, fecha="2026-03-02", cliente="Don José Pérez",
                        tipo="queso", kilos="17.22", precio_kilo="21000")
    print(f"   vender los 17,22 kg que quedaban -> {otra_venta.status_code}")
    assert otra_venta.status_code == 201, otra_venta.text
    res = resumen(client, h)
    regla_de_oro(res, "tras el borrado rechazado")
    for f in res["por_producto"]:
        print(f"      {f['producto']:22} costo {f['costo']:>16} "
              f"ingreso {f['ingreso']:>16} kilos {f['kilos']}")


def test_borrar_la_factura_entera_cuyo_queso_ya_se_vendio(client, h):
    compra, _ = _historia_corta(client, h)
    doc_id = compra["documento_id"]
    assert doc_id, "la compra plana tiene que haber armado su factura"
    r = client.delete(f"{API}/documentos/{doc_id}", headers=h)
    print(f"\n   DELETE /documentos/{{id}} de una compra ya vendida -> "
          f"{r.status_code} {r.text[:200]}")
    disp = _mostrar(client, h, "después de borrar la factura")
    assert disp.get("queso", CERO) >= CERO, (
        f"la bodega quedó en {disp.get('queso')} kg"
    )
    assert r.status_code >= 400


def test_anular_una_compra_cuyo_queso_ya_se_vendio(client, h):
    """El contraste: esta puerta SÍ está protegida. Queda medida para saber que la
    diferencia con la de borrar no es una casualidad de la historia."""
    compra, _ = _historia_corta(client, h)
    r = client.post(f"{API}/compras/{compra['id']}/anular", headers=h)
    print(f"\n   POST /compras/{{id}}/anular -> {r.status_code} {r.text[:200]}")
    assert r.status_code >= 400, "anular una compra ya vendida tiene que rebotar"
    disp = _mostrar(client, h, "tras el rechazo")
    assert disp["queso"] == D("17.22")


def test_editar_una_compra_por_debajo_de_lo_ya_vendido(client, h):
    """La otra puerta que sí está protegida: bajarle los kilos a la compra."""
    compra, _ = _historia_corta(client, h)
    r = client.put(f"{API}/compras/{compra['id']}",
                   json={"kilos_brutos": "44.23"}, headers=h)
    print(f"\n   PUT /compras/{{id}} bajando 137,45 -> 44,23 kg -> {r.status_code} "
          f"{r.text[:220]}")
    disp = _mostrar(client, h, "tras editar")
    assert disp["queso"] >= CERO, f"la bodega quedó en {disp['queso']} kg"
    assert r.status_code >= 400


# -------------------------------------------------------------- los ajustes
def test_un_ajuste_de_mas_kilos_de_los_que_hay(client, h):
    comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
            kilos_brutos="44.23", precio_kilo="14317")
    r = client.post(f"{API}/conversiones", json={
        "fecha": "2026-02-10", "kilos": "137.45", "destino": "borona",
        "precio_kilo": "1833.33"}, headers=h)
    print(f"\n   ajuste de 137,45 kg teniendo 44,23 -> {r.status_code} {r.text[:200]}")
    disp = _mostrar(client, h, "tras el ajuste")
    assert r.status_code >= 400, "un ajuste sacó más kilos de los que había"
    assert disp["queso"] == D("44.23"), (
        f"un ajuste rechazado movió la bodega: quedó en {disp['queso']} kg"
    )


def test_borrar_un_ajuste_deja_los_kilos_donde_estaban(client, h):
    comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
            kilos_brutos="137.45", precio_kilo="14317")
    r = client.post(f"{API}/conversiones", json={
        "fecha": "2026-02-10", "kilos": "44.23", "destino": "borona",
        "precio_kilo": "1833.33"}, headers=h)
    assert r.status_code == 201, r.text
    ajuste_id = r.json()["id"]
    antes = _mostrar(client, h, "con el ajuste")
    assert antes["queso"] == D("137.45") - D("44.23")
    assert antes["borona"] == D("44.23")
    # Se vende la borona que el ajuste creó y DESPUÉS se borra el ajuste.
    v = vender(client, h, fecha="2026-02-12", cliente="Tienda La Esquina",
               tipo="borona", kilos="40.00", precio_kilo="4133")
    assert v.status_code == 201, v.text
    r = client.delete(f"{API}/conversiones/{ajuste_id}", headers=h)
    print(f"\n   DELETE del ajuste con su borona ya vendida -> {r.status_code} "
          f"{r.text[:200]}")
    disp = _mostrar(client, h, "tras borrar el ajuste")
    res = resumen(client, h)
    regla_de_oro(res, "ajuste borrado")
    assert disp.get("borona", CERO) >= CERO, (
        f"la borona quedó en {disp.get('borona')} kg tras borrar el ajuste que la "
        f"había creado, con 40,00 kg ya vendidos"
    )
    assert r.status_code >= 400, (
        "se borró el ajuste que sostenía una borona ya vendida"
    )
    # Y nada se movió: los kilos siguen repartidos como estaban.
    assert disp["queso"] == D("93.22") and disp["borona"] == D("4.23")


# ------------------------------------------- el mismo ataque con producto propio
def test_borrar_la_compra_de_un_producto_propio_ya_vendido(client, h):
    crear_producto(client, h, nombre="Costeño 44,23", unidad="kg")
    c = comprar(client, h, fecha="2026-02-03", productor="Lácteos del Valle",
                tipo="costeno_44_23", kilos_brutos="137.45", precio_kilo="9137")
    assert c.status_code == 201, c.text
    v = vender(client, h, fecha="2026-02-12", cliente="Supermercado La 33",
               tipo="costeno_44_23", kilos="120.23", precio_kilo="13711")
    assert v.status_code == 201, v.text
    r = client.delete(f"{API}/compras/{c.json()['id']}", headers=h)
    print(f"\n   DELETE de la compra del producto propio ya vendido -> "
          f"{r.status_code} {r.text[:200]}")
    disp = _mostrar(client, h, "después")
    assert disp.get("costeno_44_23", CERO) >= CERO, (
        f"el producto propio quedó en {disp.get('costeno_44_23')} kg"
    )
    assert r.status_code >= 400


def test_borrar_la_compra_que_trajo_la_borona_ya_vendida(client, h):
    """La compra trae 25,36 kg de borona GRATIS encima; se vende esa borona y se
    borra la compra. La borona no tiene compra propia: nadie la puede devolver."""
    c = comprar(client, h, fecha="2026-02-03", productor="Patricia Rojas",
                kilos_brutos="137.45", precio_kilo="14317", borona_kilos="25.36")
    assert c.status_code == 201, c.text
    v = vender(client, h, fecha="2026-02-12", cliente="Tienda La Esquina",
               tipo="borona", kilos="20.11", precio_kilo="4133")
    assert v.status_code == 201, v.text
    antes = _mostrar(client, h, "antes")
    r = client.delete(f"{API}/compras/{c.json()['id']}", headers=h)
    print(f"\n   DELETE de la compra que trajo la borona vendida -> {r.status_code} "
          f"{r.text[:200]}")
    disp = _mostrar(client, h, "después")
    res = resumen(client, h)
    regla_de_oro(res, "borona huérfana")
    for f in res["por_producto"]:
        print(f"      {f['producto']:22} costo {f['costo']:>16} "
              f"ingreso {f['ingreso']:>16} kilos {f['kilos']}")
    assert disp.get("borona", CERO) >= CERO, (
        f"la borona quedó en {disp.get('borona')} kg tras borrar la compra que la trajo"
    )
    assert r.status_code >= 400, (
        "se borró la compra que sostenía una borona ya vendida: el queso estaba "
        "entero, pero esa borona no tiene ninguna otra fila que la sostenga"
    )
    assert disp["queso"] == D("137.45") and disp["borona"] == D("5.25")


def test_tras_el_rechazo_el_dueno_puede_seguir_trabajando(client, h):
    """LO QUE DE VERDAD ESTABA EN JUEGO: poder seguir registrando el día.

    Antes, el borrado pasaba y dejaba el queso en -120,23 kg; desde ahí el dueño
    tenía que COMPRAR 121 kg de más para poder registrar una venta de 1 kg, o sea
    inventarse una compra para poder anotar la verdad. Ahora el borrado rebota y todo
    lo de siempre sigue funcionando sobre los 17,22 kg que quedan.
    """
    compra, _ = _historia_corta(client, h)
    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    disp = existencias(client, h)
    print(f"\n   DELETE -> {r.status_code} ; bodega {dict(disp)}")
    assert r.status_code >= 400
    assert disp["queso"] == D("17.22")

    v = vender(client, h, fecha="2026-03-02", cliente="Don José Pérez",
               tipo="queso", kilos="1.00", precio_kilo="21000")
    print(f"   vender 1 kg SIN comprar nada de más -> {v.status_code}")
    assert v.status_code == 201, v.text

    c = comprar(client, h, fecha="2026-03-01", productor="Patricia Rojas",
                kilos_brutos="44.23", precio_kilo="14000")
    print(f"   comprar 44,23 kg -> {c.status_code}")
    assert c.status_code == 201, c.text
    assert existencias(client, h)["queso"] == D("60.45")  # 17,22 - 1,00 + 44,23
    regla_de_oro(resumen(client, h), "el día siguió normal")
