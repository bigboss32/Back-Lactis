"""AUDITORÍA — TOCAR EL CATÁLOGO Y TOCAR LOS ABONOS.

Dos frentes que el dueño toca a diario y que no pueden mover plata ya registrada:

  · EL CATÁLOGO: crear, renombrar, reordenar, desactivar, quitar y recolgar, con
    productos propios y con la historia encima. Además de "no se movió nada", se
    exige que DESPUÉS de cada operación el dueño pueda seguir anotando su día.
  · LOS ABONOS: el que se derramó desde la factura, el que se borra, y lo que eso
    le hace a la cartera y al estado de cuenta que se le entrega al cliente.
"""
from decimal import Decimal

import pytest

from tests.ayudas_reventa import (
    API,
    PROD,
    PERIODO,
    CERO,
    D,
    compra,
    crear_producto,
    diferencias,
    exigir_que_se_pueda_trabajar,
    foto,
    historia,
    historia_gorda,
    productos,
    regla_de_oro,
    resumen,
    venta,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


@pytest.fixture()
def hb(client, base_datos):
    return auth_headers(client, "admin.b")


def leer_doc(client, h, doc_id):
    r = client.get(f"{API}/documentos/{doc_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ============================================================== EL CATÁLOGO
def test_zzaudit_quitar_y_volver_a_crear_un_producto_con_otra_unidad(client, h):
    """Se crea 'Panela' que se pesa, se quita, y se vuelve a crear POR UNIDAD.

    Lo que se mide es qué queda guardado y con qué unidad se le va a calcular la
    plata: una fila que se creyó "por unidad" y se guarda "por kilo" es plata mal
    contada en la primera compra.
    """
    p = crear_producto(client, h, nombre="Panela 44,23", unidad="kg")
    print("\n   creada en:", p["unidad"], " clave:", p["clave"])
    r = client.delete(f"{PROD}/{p['id']}", headers=h)
    assert r.status_code == 204, r.text
    revivido = crear_producto(client, h, nombre="Panela 44,23", unidad="unidad")
    print("   pedida por unidad, quedó en:", revivido["unidad"],
          " se_pesa:", revivido["se_pesa"], " decimales:", revivido["decimales"])
    assert revivido["id"] == p["id"], "revivir tiene que devolver la MISMA fila"

    # Y lo que decide la plata: con qué unidad se le acepta la compra.
    por_unidad = client.post(f"{API}/compras", json={
        "fecha": "2026-02-03", "productor": "Patricia Rojas",
        "tipo": revivido["clave"], "barras": "137", "precio_barra": "12433"},
        headers=h)
    por_kilo = client.post(f"{API}/compras", json={
        "fecha": "2026-02-03", "productor": "Patricia Rojas",
        "tipo": revivido["clave"], "kilos_brutos": "137.45",
        "precio_kilo": "12433"}, headers=h)
    print("   compra por UNIDADES ->", por_unidad.status_code, por_unidad.text[:140])
    print("   compra por KILOS    ->", por_kilo.status_code, por_kilo.text[:140])
    # La única exigencia dura: lo que se guarde tiene que cuadrar con la unidad que
    # la respuesta del catálogo declaró, sin plata en cero.
    aceptada = por_unidad if por_unidad.status_code == 201 else por_kilo
    assert aceptada.status_code == 201, "no se pudo comprar el producto revivido"
    cuerpo = aceptada.json()
    print("   quedó guardada:", {k: cuerpo[k] for k in
                                 ("tipo", "unidad", "kilos_netos", "barras",
                                  "precio_kilo", "precio_barra", "valor_total")})
    assert D(cuerpo["valor_total"]) > CERO, "la compra se guardó en CEROS"
    assert cuerpo["unidad"] == revivido["unidad"], (
        f"el catálogo dice que el producto va en '{revivido['unidad']}' y la compra "
        f"quedó guardada en '{cuerpo['unidad']}'"
    )


@pytest.mark.parametrize("titulo", [
    "renombrar el queso",
    "poner el producto nuevo de primero",
    "desactivar la borona",
    "desligar la borona de su padre",
    "quitar un producto sin movimientos",
])
def test_zzaudit_una_operacion_del_catalogo_no_mueve_plata_ni_bloquea(client, h, titulo):
    """La matriz, con la historia gorda encima y midiendo LAS DOS cosas."""
    historia_gorda(client, h)
    antes = foto(client, h)
    cat = productos(client, h)
    nuevo = crear_producto(client, h, nombre="Cuajada 137,45", unidad="kg")
    # (crear ya es una operación: se mide aparte y se vuelve a tomar la foto)
    despues_de_crear = foto(client, h)
    movidas, nacidas = diferencias(antes, despues_de_crear)
    assert not movidas and not nacidas, f"crear movió {movidas[:3]} / estrenó {nacidas[:3]}"

    base = foto(client, h)
    if titulo == "renombrar el queso":
        r = client.put(f"{PROD}/{cat['queso']['id']}",
                       json={"nombre": "Queso costeño de la finca"}, headers=h)
    elif titulo == "poner el producto nuevo de primero":
        r = client.put(f"{PROD}/{nuevo['id']}", json={"orden": 0}, headers=h)
    elif titulo == "desactivar la borona":
        r = client.put(f"{PROD}/{cat['borona']['id']}",
                       json={"estado": "inactivo"}, headers=h)
    elif titulo == "desligar la borona de su padre":
        r = client.put(f"{PROD}/{cat['borona']['id']}",
                       json={"subproducto_de_id": None}, headers=h)
    else:
        r = client.delete(f"{PROD}/{nuevo['id']}", headers=h)
    print(f"\n   [{titulo}] -> {r.status_code} {r.text[:180]}")

    movidas, nacidas = diferencias(base, foto(client, h))
    # Quitar un producto SÍ le puede quitar su renglón de existencias, y solo si
    # estaba en CERO: eso no es plata que se movió, es una fila que dejó de existir.
    movidas = [m for m in movidas if not (m[2] is None and m[1] == CERO)]
    for ruta, viejo, nvo in movidas[:30]:
        print(f"      MOVIÓ {ruta}: {viejo} -> {nvo}")
    for ruta, valor in nacidas[:30]:
        print(f"      NACIÓ {ruta} = {valor}")
    assert not movidas, f"[{titulo}] movió {len(movidas)} cifras"
    assert not nacidas, f"[{titulo}] estrenó cifras"
    regla_de_oro(resumen(client, h), titulo)
    exigir_que_se_pueda_trabajar(client, h, titulo)


def test_zzaudit_el_catalogo_de_una_quesera_no_manda_en_la_otra(client, h, hb):
    """Las DOS empresas del dueño tienen su 'queso'. Un producto por unidad creado
    en A no puede cambiarle la unidad a un movimiento de B."""
    crear_producto(client, h, nombre="Costeño", unidad="kg")
    historia(client, h)
    historia(client, hb)
    a = resumen(client, h)
    b = resumen(client, hb)
    print("\n   A total_compras:", a["total_compras"], " B:", b["total_compras"])
    print("   A total_ventas :", a["total_ventas"], " B:", b["total_ventas"])
    assert D(a["total_compras"]) == D(b["total_compras"])
    assert D(a["total_ventas"]) == D(b["total_ventas"])
    claves_b = set(productos(client, hb))
    print("   catálogo de B:", sorted(claves_b))
    assert "costeno" not in claves_b, "el producto de A se coló en el catálogo de B"


# ================================================================ LOS ABONOS
def test_zzaudit_borrar_un_abono_que_vino_del_derrame(client, h):
    """Un abono a la FACTURA se parte en un abono por renglón. Borrar uno de esos
    tiene que dejar la factura, la cartera y el estado de cuenta cuadrados."""
    compra(client, h, fecha="2026-01-15", productor="Lácteos del Valle",
           kilos_brutos="2000.00", precio_kilo="14317")
    r = client.post(f"{API}/documentos", json={
        "tipo": "venta", "fecha": "2026-02-12", "tercero": "Don José Pérez",
        "renglones": [
            {"tipo": "queso", "kilos": "44.23", "precio_kilo": "21533"},
            {"tipo": "queso", "kilos": "137.45", "precio_kilo": "20917"},
        ]}, headers=h)
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    total = D(leer_doc(client, h, doc_id)["total"])
    r = client.post(f"{API}/documentos/{doc_id}/abonos",
                    json={"fecha": "2026-02-13", "valor": "1833333.33"}, headers=h)
    assert r.status_code in (200, 201), r.text
    leido = leer_doc(client, h, doc_id)
    print("\n   tras el derrame:")
    for ren in leido["renglones"]:
        print(f"      renglón {ren['orden']}: total {ren['valor_total']} "
              f"abonado {ren['abonado']} abonos {[a['valor'] for a in ren['abonos']]}")
    primero = leido["renglones"][0]
    abono_id = primero["abonos"][0]["id"]
    valor_borrado = D(primero["abonos"][0]["valor"])

    r = client.delete(f"{API}/ventas/{primero['id']}/abonos/{abono_id}", headers=h)
    print(f"   borrar el abono de {valor_borrado} del primer renglón -> {r.status_code}")
    assert r.status_code in (200, 204), r.text

    leido = leer_doc(client, h, doc_id)
    suma = sum((D(x["abonado"]) for x in leido["renglones"]), CERO)
    print("   factura: total", leido["total"], " abonado", leido["abonado"],
          " saldo", leido["saldo"], " SUMA renglones", suma)
    assert D(leido["abonado"]) == suma
    assert D(leido["abonado"]) == D("1833333.33") - valor_borrado
    assert D(leido["saldo"]) == D(leido["total"]) - D(leido["abonado"])

    res = resumen(client, h)
    r = client.get(f"{API}/estado-cuenta", params={"cliente": "Don José Pérez"},
                   headers=h)
    ec = r.json()
    print("   por_cobrar_clientes:", res["por_cobrar_clientes"],
          " EC saldo:", ec["saldo"])
    assert D(res["por_cobrar_clientes"]) == D(ec["saldo"])
    assert D(ec["saldo"]) == total - D(leido["abonado"])
    suma_ventas = sum((D(v["saldo"]) for v in ec["ventas"]), CERO)
    assert suma_ventas == D(ec["saldo"]), (
        f"el estado de cuenta dice saldo {ec['saldo']} y sus renglones suman "
        f"{suma_ventas}"
    )
    suma_pagos = sum((D(p["valor"]) for p in ec["pagos"]), CERO)
    print("   pagos listados:", [p["valor"] for p in ec["pagos"]],
          " total_abonado:", ec["total_abonado"])
    assert suma_pagos == D(ec["total_abonado"]), (
        f"los pagos listados suman {suma_pagos} y el encabezado dice "
        f"{ec['total_abonado']}"
    )


def test_zzaudit_el_estado_de_cuenta_del_productor_cuadra_solo(client, h):
    historia_gorda(client, h)
    for productor in ("Patricia Rojas", "Sebastián Ruiz", "Lácteos del Valle"):
        r = client.get(f"{API}/estado-cuenta-productor",
                       params={"productor": productor}, headers=h)
        assert r.status_code == 200, r.text
        e = r.json()
        suma_total = sum((D(c["valor_total"]) for c in e["compras_detalle"]), CERO)
        suma_saldo = sum((D(c["saldo"]) for c in e["compras_detalle"]), CERO)
        suma_pagos = sum((D(p["valor"]) for p in e["pagos"]), CERO)
        print(f"\n   {productor}: total {e['total_comprado']} (SUMA {suma_total}) "
              f"pagado {e['total_pagado']} (SUMA {suma_pagos}) "
              f"saldo {e['saldo']} (SUMA {suma_saldo})")
        assert suma_total == D(e["total_comprado"]), (
            f"{productor}: el detalle suma {suma_total} y el encabezado dice "
            f"{e['total_comprado']}"
        )
        assert suma_pagos == D(e["total_pagado"]), (
            f"{productor}: los pagos suman {suma_pagos} y el encabezado dice "
            f"{e['total_pagado']}"
        )
        assert suma_saldo == D(e["saldo"]) - D(e["libro_anterior_saldo"]), (
            f"{productor}: los saldos suman {suma_saldo} y el encabezado dice "
            f"{e['saldo']}"
        )
        kilos = sum((D(c["kilos"]) for c in e["compras_detalle"]), CERO)
        assert kilos == D(e["total_kilos"]), (
            f"{productor}: los kilos del detalle suman {kilos} y el encabezado dice "
            f"{e['total_kilos']}"
        )
