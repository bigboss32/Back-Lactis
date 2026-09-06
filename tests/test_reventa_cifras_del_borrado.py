"""LAS CIFRAS EXACTAS DEL BORRADO DE UNA COMPRA YA VENDIDA, impresas para leerlas.

Es la sonda con la que se midió el defecto, y se queda porque imprime el resumen
ENTERO —las tarjetas, el desglose por producto, las existencias y el panel de
lotes— antes y después del intento de borrado. Cuando algo de esto se mueva, la
cifra que se movió va a estar en la salida y no habrá que buscarla.

Lo que fija: el DELETE de una compra ya vendida rebota, la compra sigue viva, y
ninguna de las dos cifras de bodega —los kilos y las barras— queda en negativo.
Las reglas y los mensajes están en test_reventa_borrar_lo_que_ya_se_vendio.py.
"""
from decimal import Decimal

import pytest

from tests.ayudas_reventa import API, CERO, D, regla_de_oro, resumen, lotes
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


def _pintar(client, h, titulo):
    res = resumen(client, h)
    print(f"\n===== {titulo} =====")
    print(f"   total_compras {res['total_compras']}  total_ventas {res['total_ventas']}"
          f"  ganancia_estimada {res['ganancia_estimada']}")
    print(f"   kilos_comprados {res['kilos_comprados']}  "
          f"kilos_vendidos {res['kilos_vendidos']}  "
          f"kilos_disponibles {res['kilos_disponibles']}")
    print(f"   por_pagar_productores {res['por_pagar_productores']}  "
          f"por_cobrar_clientes {res['por_cobrar_clientes']}")
    for f in res["por_producto"]:
        print(f"   fila {f['producto']:14} kilos {f['kilos']:>10} "
              f"costo {f['costo']:>16} ingreso {f['ingreso']:>16} "
              f"ganancia {f['ganancia']:>16}")
    for e in res["existencias"]:
        print(f"   existencia {e['producto']:14} {e['disponible']:>10}")
    regla_de_oro(res, titulo)
    pan = lotes(client, h)
    print(f"   lotes: {len(pan['lotes'])}  total_costo {pan['total_costo']}  "
          f"total_ingresos {pan['total_ingresos']}  "
          f"total_ganancia {pan['total_ganancia']}  "
          f"kilos_sin_lote {pan['kilos_sin_lote']}  "
          f"ingreso_sin_lote {pan['ingreso_sin_lote']}")
    return res


def test_cifras_exactas_del_borrado(client, h):
    c = client.post(f"{API}/compras", json={
        "fecha": "2026-02-03", "productor": "Patricia Rojas",
        "kilos_brutos": "137.45", "precio_kilo": "14317"}, headers=h)
    assert c.status_code == 201, c.text
    v = client.post(f"{API}/ventas", json={
        "fecha": "2026-02-12", "cliente": "Don José Pérez", "tipo": "queso",
        "kilos": "120.23", "precio_kilo": "21533", "gasto_por_kilo": "137"},
        headers=h)
    assert v.status_code == 201, v.text
    _pintar(client, h, "ANTES del borrado")
    r = client.delete(f"{API}/compras/{c.json()['id']}", headers=h)
    print("\n   DELETE ->", r.status_code, r.text[:200])
    assert r.status_code == 422, "se borró una compra con 120,23 kg ya vendidos"
    despues = _pintar(client, h, "DESPUÉS del borrado")
    assert D(despues["kilos_disponibles"]) >= CERO, (
        f"kilos_disponibles = {despues['kilos_disponibles']}"
    )
    # Y la compra sigue viva: 137,45 kg a $14.317 = $1.968.032,65 en el total.
    assert D(despues["kilos_disponibles"]) == D("17.22")
    assert D(despues["kilos_comprados"]) == D("137.45")


def test_el_mismo_borrado_con_un_producto_por_unidades(client, h):
    c = client.post(f"{API}/compras", json={
        "fecha": "2026-02-05", "productor": "Sebastián Ruiz", "tipo": "mozzarella",
        "barras": "137", "precio_barra": "12433"}, headers=h)
    assert c.status_code == 201, c.text
    v = client.post(f"{API}/ventas", json={
        "fecha": "2026-03-04", "cliente": "Don José Pérez", "tipo": "mozzarella",
        "barras": "59", "precio_barra": "17311", "gasto_por_barra": "211"},
        headers=h)
    assert v.status_code == 201, v.text
    r = client.delete(f"{API}/compras/{c.json()['id']}", headers=h)
    print("\n   DELETE de la compra de 137 barras con 59 vendidas ->",
          r.status_code, r.text[:200])
    assert r.status_code == 422, "se borró una compra con 59 barras ya vendidas"
    res = _pintar(client, h, "barras tras el borrado")
    otra = client.post(f"{API}/ventas", json={
        "fecha": "2026-03-10", "cliente": "Don José Pérez", "tipo": "mozzarella",
        "barras": "1", "precio_barra": "17311"}, headers=h)
    print("   ¿se puede vender 1 barra más? ->", otra.status_code, otra.text[:160])
    assert otra.status_code == 201, (
        "el rechazo dejó la bodega torcida: no se puede seguir vendiendo"
    )
    assert D(res["barras_disponibles"]) >= CERO, (
        f"barras_disponibles = {res['barras_disponibles']}"
    )
    assert D(res["barras_disponibles"]) == D("78"), "137 - 59 = 78 barras"
