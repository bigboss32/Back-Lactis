"""AUDITORÍA — LAS DOS DEUDAS CONOCIDAS DE REVENTA, medidas contra la API.

1) Crear un producto puede desordenar el ranking de ganancia por productor.
2) Un producto desactivado sigue admitiendo compras y ventas.

No se arregla nada: solo se mide y se imprime la cifra.
"""
from decimal import Decimal

import pytest

from tests.ayudas_reventa import (
    API,
    PROD,
    PERIODO,
    D,
    compra,
    crear_producto,
    diferencias,
    foto,
    historia,
    productos,
    resumen,
    venta,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


# ---------------------------------------------------------------- DEUDA 1
def _ranking(res):
    return [(f["productor"], D(f["ganancia_estimada"]), D(f["total_comprado"]))
            for f in res["por_productor"]]


def test_zzaudit_crear_producto_y_el_ranking_por_productor(client, h):
    """¿Agregar un producto al catálogo le mueve la ganancia a los productores?"""
    historia(client, h)
    antes = _ranking(resumen(client, h))
    foto_antes = foto(client, h)
    print("\n=== RANKING ANTES ===")
    for n, g, c in antes:
        print(f"   {n:20} ganancia {g:>16}  comprado {c:>16}")

    # Un producto propio del dueño, puesto de PRIMERO en la lista (orden 0), que es
    # el caso que rompía: el orden es un campo de presentación.
    nuevo = crear_producto(client, h, nombre="Queso Costeño 44,23", unidad="kg", orden=0)
    print("   creado:", nuevo["clave"], "orden", nuevo["orden"])

    despues = _ranking(resumen(client, h))
    print("=== RANKING DESPUÉS ===")
    for n, g, c in despues:
        print(f"   {n:20} ganancia {g:>16}  comprado {c:>16}")

    movidas, nacidas = diferencias(foto_antes, foto(client, h))
    print(f"   cifras movidas: {len(movidas)}, nacidas: {len(nacidas)}")
    for ruta, viejo, nvo in movidas[:40]:
        print(f"      MOVIÓ {ruta}: {viejo} -> {nvo}")
    for ruta, valor in nacidas[:40]:
        print(f"      NACIÓ {ruta} = {valor}")

    assert [n for n, _, _ in antes] == [n for n, _, _ in despues], (
        f"el ranking se reordenó: {[n for n,_,_ in antes]} -> {[n for n,_,_ in despues]}"
    )
    assert antes == despues, f"la ganancia por productor se movió: {antes} vs {despues}"


def test_zzaudit_crear_subproducto_del_queso_y_el_ranking(client, h):
    """El mismo ataque pero colgando el producto nuevo del queso (mismo grupo)."""
    historia(client, h)
    antes = _ranking(resumen(client, h))
    foto_antes = foto(client, h)
    queso = productos(client, h)["queso"]
    nuevo = crear_producto(client, h, nombre="Recorte 137,45", unidad="kg",
                           subproducto_de_id=queso["id"], orden=0)
    print("\n   creado subproducto:", nuevo["clave"])
    despues = _ranking(resumen(client, h))
    for (n1, g1, c1), (n2, g2, c2) in zip(antes, despues):
        print(f"   {n1:20} {g1:>16} -> {g2:>16}")
    movidas, nacidas = diferencias(foto_antes, foto(client, h))
    print(f"   cifras movidas: {len(movidas)}, nacidas: {len(nacidas)}")
    for ruta, viejo, nvo in movidas[:40]:
        print(f"      MOVIÓ {ruta}: {viejo} -> {nvo}")
    assert antes == despues, f"{antes} vs {despues}"


# ---------------------------------------------------------------- DEUDA 2
def _desactivar(client, h, producto_id):
    r = client.put(f"{PROD}/{producto_id}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_producto_desactivado_sigue_admitiendo_compras_y_ventas(client, h):
    """Se desactiva un producto propio y se intenta comprarlo y venderlo."""
    nuevo = crear_producto(client, h, nombre="Panela 44,23", unidad="kg")
    clave = nuevo["clave"]
    _desactivar(client, h, nuevo["id"])

    # ¿Sigue apareciendo en la lista del catálogo?
    r = client.get(PROD, params={"size": 100}, headers=h)
    todos = {p["clave"]: p.get("estado") for p in r.json()["items"]}
    print("\n   catálogo tras desactivar:", todos)

    rc = client.post(f"{API}/compras", json={
        "fecha": "2026-04-02", "productor": "Patricia Rojas", "tipo": clave,
        "kilos_brutos": "137.45", "precio_kilo": "1833.33"}, headers=h)
    print(f"   COMPRA de '{clave}' desactivado -> {rc.status_code}  {rc.text[:180]}")

    rv = None
    if rc.status_code == 201:
        rv = client.post(f"{API}/ventas", json={
            "fecha": "2026-04-05", "cliente": "Don José Pérez", "tipo": clave,
            "kilos": "44.23", "precio_kilo": "2427.60"}, headers=h)
        print(f"   VENTA  de '{clave}' desactivado -> {rv.status_code}  {rv.text[:180]}")
        res = resumen(client, h)
        filas = {f["producto"]: f for f in res["por_producto"]}
        print("   fila del desactivado en el desglose:",
              filas.get(clave, {}).get("costo"), filas.get(clave, {}).get("ingreso"))
        print("   total_compras:", res["total_compras"], " total_ventas:", res["total_ventas"])

    assert rc.status_code >= 400, (
        f"un producto DESACTIVADO admitió una compra: {rc.status_code} "
        f"({rc.json().get('valor_total') if rc.status_code == 201 else ''})"
    )


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_producto_de_siembra_desactivado_sigue_admitiendo(client, h):
    """El mismo ataque sobre 'queso', que es el producto de todos los días."""
    queso = productos(client, h)["queso"]
    _desactivar(client, h, queso["id"])
    rc = client.post(f"{API}/compras", json={
        "fecha": "2026-04-02", "productor": "Patricia Rojas",
        "kilos_brutos": "137.45", "precio_kilo": "1833.33",
        "borona_kilos": "3.17"}, headers=h)
    print(f"\n   COMPRA de queso desactivado -> {rc.status_code} {rc.text[:200]}")
    assert rc.status_code >= 400, "el queso desactivado admitió una compra"
