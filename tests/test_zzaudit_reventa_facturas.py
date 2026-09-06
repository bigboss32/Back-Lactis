"""AUDITORÍA — LA FACTURA DE VARIOS RENGLONES DE REVENTA.

Se mide, contra la API y con cifras feas:

  · que una factura de N renglones dé EXACTAMENTE lo mismo que esos N registrados
    por separado (la foto completa: resumen, lotes, ganancia por día y estados de
    cuenta);
  · que el mismo producto repetido en dos renglones no se pise ni se duplique;
  · que el abono al documento se derrame sin perder ni un centavo;
  · que anular y editar la factura dejen la igualdad del encabezado en pie:
        total + total_anulado == suma de los valor_total de TODOS los renglones
        saldo == total - abonado
"""
from decimal import Decimal

import pytest

from tests.ayudas_reventa import (
    API,
    PERIODO,
    D,
    CERO,
    crear_producto,
    diferencias,
    foto,
    regla_de_oro,
    resumen,
)
from tests.conftest import auth_headers


@pytest.fixture()
def ha(client, base_datos):
    return auth_headers(client, "admin.a")


@pytest.fixture()
def hb(client, base_datos):
    return auth_headers(client, "admin.b")


def doc(client, h, **campos):
    r = client.post(f"{API}/documentos", json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def leer_doc(client, h, doc_id):
    r = client.get(f"{API}/documentos/{doc_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def abastecer(client, h, kilos="2000.00", precio="14317", fecha="2026-01-15"):
    """Queso en la bodega, para poder vender. Fuera del período que se mide cuando
    haga falta; acá va dentro, y su plata se cuenta en las afirmaciones."""
    r = client.post(f"{API}/compras", json={
        "fecha": fecha, "productor": "Lácteos del Valle",
        "kilos_brutos": kilos, "precio_kilo": precio}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def cuadre_del_documento(d, titulo):
    """LA IGUALDAD QUE EL DUEÑO PUEDE VERIFICAR A MANO en el encabezado."""
    suma_todos = sum((D(r["valor_total"]) for r in d["renglones"]), CERO)
    assert D(d["total"]) + D(d["total_anulado"]) == suma_todos, (
        f"[{titulo}] total {d['total']} + anulado {d['total_anulado']} != "
        f"suma de renglones {suma_todos}"
    )
    vivos = [r for r in d["renglones"] if r["estado"] != "anulada"]
    suma_abonado = sum((D(r["abonado"]) for r in vivos), CERO)
    assert D(d["abonado"]) == suma_abonado, (
        f"[{titulo}] abonado {d['abonado']} != suma de abonos de renglones "
        f"{suma_abonado}"
    )
    assert D(d["saldo"]) == D(d["total"]) - D(d["abonado"]) + D(d["saldo_a_favor"]), (
        f"[{titulo}] saldo {d['saldo']} != total {d['total']} - abonado "
        f"{d['abonado']} (+a favor {d['saldo_a_favor']})"
    )
    assert d["cantidad_renglones"] == len(d["renglones"]), (
        f"[{titulo}] dice {d['cantidad_renglones']} renglones y trae "
        f"{len(d['renglones'])}"
    )


# --------------------------------------------------------- N renglones == N sueltos
RENGLONES_COMPRA = [
    {"tipo": "queso", "kilos_brutos": "820.53", "precio_kilo": "14317",
     "borona_kilos": "18.27"},
    {"tipo": "costeno", "kilos_brutos": "233.41", "precio_kilo": "9137",
     "borona_kilos": "11.03"},
    {"tipo": "mozzarella", "barras": "137", "precio_barra": "12433"},
]
RENGLONES_VENTA = [
    {"tipo": "queso", "kilos": "500.37", "precio_kilo": "21533",
     "gasto_por_kilo": "137"},
    {"tipo": "borona", "kilos": "22.13", "precio_kilo": "4133"},
    {"tipo": "mozzarella", "barras": "59", "precio_barra": "17311",
     "gasto_por_barra": "211"},
]
ABONO_COMPRA = Decimal("3333333.77")
ABONO_VENTA = Decimal("5000000.33")


def _catalogo_propio(client, h):
    crear_producto(client, h, nombre="Costeño", unidad="kg")


def _por_factura(client, h):
    _catalogo_propio(client, h)
    dc = doc(client, h, tipo="compra", fecha="2026-02-03", tercero="Patricia Rojas",
             renglones=RENGLONES_COMPRA)
    dv = doc(client, h, tipo="venta", fecha="2026-02-12", tercero="Don José Pérez",
             renglones=RENGLONES_VENTA)
    r = client.post(f"{API}/documentos/{dc['id']}/abonos",
                    json={"fecha": "2026-02-14", "valor": str(ABONO_COMPRA)}, headers=h)
    assert r.status_code in (200, 201), r.text
    r = client.post(f"{API}/documentos/{dv['id']}/abonos",
                    json={"fecha": "2026-02-20", "valor": str(ABONO_VENTA)}, headers=h)
    assert r.status_code in (200, 201), r.text
    return dc, dv


def _uno_por_uno(client, h):
    """Los mismos seis movimientos, cada uno por su puerta plana, con el abono
    derramado A MANO igual que lo hace la factura (primero se llena el primero)."""
    _catalogo_propio(client, h)
    compras = []
    for renglon in RENGLONES_COMPRA:
        r = client.post(f"{API}/compras",
                        json={**renglon, "fecha": "2026-02-03",
                              "productor": "Patricia Rojas"}, headers=h)
        assert r.status_code == 201, r.text
        compras.append(r.json())
    ventas = []
    for renglon in RENGLONES_VENTA:
        r = client.post(f"{API}/ventas",
                        json={**renglon, "fecha": "2026-02-12",
                              "cliente": "Don José Pérez"}, headers=h)
        assert r.status_code == 201, r.text
        ventas.append(r.json())

    def derramar(filas, ruta, valor, fecha):
        restante = D(valor)
        for fila in filas:
            if restante <= CERO:
                break
            cuota = min(restante, D(fila["saldo"]))
            if cuota <= CERO:
                continue
            r = client.post(f"{API}/{ruta}/{fila['id']}/abonos",
                            json={"fecha": fecha, "valor": str(cuota)}, headers=h)
            assert r.status_code in (200, 201), r.text
            restante -= cuota
        assert restante == CERO

    derramar(compras, "compras", ABONO_COMPRA, "2026-02-14")
    derramar(ventas, "ventas", ABONO_VENTA, "2026-02-20")
    return compras, ventas


def test_zzaudit_factura_de_tres_renglones_es_igual_a_tres_sueltos(client, ha, hb):
    dc, dv = _por_factura(client, ha)
    _uno_por_uno(client, hb)
    con_factura = foto(client, ha)
    sueltos = foto(client, hb)
    movidas, nacidas = diferencias(sueltos, con_factura)
    print("\n=== factura de 3 renglones VS 3 sueltos ===")
    for ruta, viejo, nuevo in movidas[:60]:
        print(f"   DIFIERE {ruta}: sueltos {viejo} -> factura {nuevo}")
    for ruta, valor in nacidas[:60]:
        print(f"   SOLO EN LA FACTURA {ruta} = {valor}")
    faltan = [(r, v) for r, v in sueltos.items() if r not in con_factura and v != CERO]
    for ruta, valor in faltan[:60]:
        print(f"   SOLO EN LOS SUELTOS {ruta} = {valor}")
    assert not movidas and not nacidas, (
        f"{len(movidas)} cifras distintas entre la factura y los sueltos"
    )

    cuadre_del_documento(leer_doc(client, ha, dc["id"]), "compra de 3 renglones")
    cuadre_del_documento(leer_doc(client, ha, dv["id"]), "venta de 3 renglones")
    regla_de_oro(resumen(client, ha), "con factura")
    regla_de_oro(resumen(client, hb), "sueltos")


# ----------------------------------------- el mismo producto en dos renglones
def test_zzaudit_mismo_producto_repetido_en_dos_renglones(client, ha):
    """Dos renglones del MISMO producto en una factura: ni se pisan ni se duplican."""
    dc = doc(client, ha, tipo="compra", fecha="2026-02-03", tercero="Patricia Rojas",
             renglones=[
                 {"tipo": "queso", "kilos_brutos": "137.45", "precio_kilo": "14317"},
                 {"tipo": "queso", "kilos_brutos": "44.23", "precio_kilo": "15033"},
             ])
    leido = leer_doc(client, ha, dc["id"])
    esperado = (D("137.45") * D("14317") + D("44.23") * D("15033")).quantize(D("0.01"))
    print("\n   total de la factura:", leido["total"], " esperado:", esperado)
    assert D(leido["total"]) == esperado
    cuadre_del_documento(leido, "compra con el queso repetido")

    res = resumen(client, ha)
    regla_de_oro(res, "queso repetido")
    filas = [f for f in res["por_producto"] if f["producto"] == "queso"]
    print("   filas de 'queso' en el desglose:", len(filas))
    print("   kilos_comprados:", res["kilos_comprados"], " total_compras:", res["total_compras"])
    assert D(res["kilos_comprados"]) == D("137.45") + D("44.23")
    assert D(res["total_compras"]) == esperado

    # Y la venta de los dos renglones juntos tiene que caber en la bodega.
    rv = client.post(f"{API}/ventas", json={
        "fecha": "2026-02-12", "cliente": "Don José Pérez", "tipo": "queso",
        "kilos": "181.68", "precio_kilo": "21533"}, headers=ha)
    print("   venta de los 181,68 kg juntos ->", rv.status_code, rv.text[:160])
    assert rv.status_code == 201, rv.text


# ----------------------------------------------------- el abono se derrama
def test_zzaudit_abono_al_documento_no_pierde_ni_un_centavo(client, ha):
    abastecer(client, ha)
    dv = doc(client, ha, tipo="venta", fecha="2026-02-12", tercero="Don José Pérez",
             renglones=[
                 {"tipo": "queso", "kilos": "44.23", "precio_kilo": "21533"},
                 {"tipo": "queso", "kilos": "137.45", "precio_kilo": "20917"},
                 {"tipo": "queso", "kilos": "12.07", "precio_kilo": "19833.33"},
             ])
    total = D(leer_doc(client, ha, dv["id"])["total"])
    print("\n   total de la venta de 3 renglones:", total)

    # Tres abonos con cifras feas, el último exacto al saldo.
    for fecha, valor in (("2026-02-13", "1000000.07"), ("2026-02-15", "999999.93")):
        r = client.post(f"{API}/documentos/{dv['id']}/abonos",
                        json={"fecha": fecha, "valor": valor}, headers=ha)
        assert r.status_code in (200, 201), r.text
        leido = leer_doc(client, ha, dv["id"])
        cuadre_del_documento(leido, f"tras abonar {valor}")
        print(f"   abonado {valor} -> abonado {leido['abonado']} saldo {leido['saldo']}")

    leido = leer_doc(client, ha, dv["id"])
    saldo = D(leido["saldo"])
    r = client.post(f"{API}/documentos/{dv['id']}/abonos",
                    json={"fecha": "2026-02-28", "valor": str(saldo)}, headers=ha)
    assert r.status_code in (200, 201), r.text
    leido = leer_doc(client, ha, dv["id"])
    cuadre_del_documento(leido, "tras pagarla toda")
    print("   final: abonado", leido["abonado"], "saldo", leido["saldo"],
          "estado", leido["estado_pago"])
    assert D(leido["abonado"]) == total
    assert D(leido["saldo"]) == CERO
    assert leido["estado_pago"] == "pagada"

    # Y la cartera del resumen tiene que reflejarlo.
    res = resumen(client, ha)
    print("   por_cobrar_clientes:", res["por_cobrar_clientes"])
    assert D(res["por_cobrar_clientes"]) == CERO

    # Un peso más se rechaza.
    r = client.post(f"{API}/documentos/{dv['id']}/abonos",
                    json={"fecha": "2026-03-01", "valor": "1"}, headers=ha)
    print("   un peso de más ->", r.status_code, r.text[:140])
    assert r.status_code >= 400


def test_zzaudit_abono_que_se_pasa_del_saldo_de_la_factura(client, ha):
    abastecer(client, ha)
    dv = doc(client, ha, tipo="venta", fecha="2026-02-12", tercero="Don José Pérez",
             renglones=[{"tipo": "queso", "kilos": "44.23", "precio_kilo": "21533"}])
    total = D(leer_doc(client, ha, dv["id"])["total"])
    r = client.post(f"{API}/documentos/{dv['id']}/abonos",
                    json={"fecha": "2026-02-13", "valor": str(total + D("0.01"))},
                    headers=ha)
    print(f"\n   abono de {total + D('0.01')} sobre {total} -> {r.status_code} {r.text[:160]}")
    assert r.status_code >= 400, "el abono se pasó del saldo y entró igual"
    leido = leer_doc(client, ha, dv["id"])
    assert D(leido["abonado"]) == CERO, "el abono rechazado dejó plata escrita"
    cuadre_del_documento(leido, "tras el abono rechazado")


# ------------------------------------------------------------ anular y editar
def test_zzaudit_anular_un_renglon_deja_el_encabezado_cuadrado(client, ha):
    dc = doc(client, ha, tipo="compra", fecha="2026-02-03", tercero="Patricia Rojas",
             renglones=[
                 {"tipo": "queso", "kilos_brutos": "820.53", "precio_kilo": "14317"},
                 {"tipo": "queso", "kilos_brutos": "137.45", "precio_kilo": "15033"},
             ])
    leido = leer_doc(client, ha, dc["id"])
    antes_total = D(leido["total"])
    renglon = leido["renglones"][1]
    r = client.post(f"{API}/compras/{renglon['id']}/anular", headers=ha)
    print("\n   anular el segundo renglón ->", r.status_code, r.text[:160])
    assert r.status_code in (200, 201), r.text
    leido = leer_doc(client, ha, dc["id"])
    cuadre_del_documento(leido, "con un renglón anulado")
    print(f"   total {antes_total} -> {leido['total']}  anulado {leido['total_anulado']}")
    assert D(leido["total"]) == antes_total - D(renglon["valor_total"])
    assert D(leido["total_anulado"]) == D(renglon["valor_total"])

    res = resumen(client, ha)
    regla_de_oro(res, "con un renglón anulado")
    print("   kilos_comprados:", res["kilos_comprados"], " total_compras:", res["total_compras"])
    assert D(res["kilos_comprados"]) == D("820.53")


def test_zzaudit_anular_toda_la_factura(client, ha):
    abastecer(client, ha)
    dv = doc(client, ha, tipo="venta", fecha="2026-02-12", tercero="Don José Pérez",
             renglones=[{"tipo": "queso", "kilos": "44.23", "precio_kilo": "21533"}])
    r = client.post(f"{API}/documentos/{dv['id']}/anular", headers=ha)
    print("\n   anular la factura entera ->", r.status_code, r.text[:200])
    assert r.status_code in (200, 201), r.text
    leido = leer_doc(client, ha, dv["id"])
    cuadre_del_documento(leido, "factura anulada")
    print("   estado_pago:", leido["estado_pago"], "total:", leido["total"],
          "anulado:", leido["total_anulado"])
    assert leido["estado_pago"] == "anulada"
    assert D(leido["total"]) == CERO
    res = resumen(client, ha)
    regla_de_oro(res, "factura anulada")
    print("   total_ventas tras anular:", res["total_ventas"],
          " por_cobrar:", res["por_cobrar_clientes"])
    assert D(res["total_ventas"]) == CERO
    assert D(res["por_cobrar_clientes"]) == CERO


def test_zzaudit_editar_la_factura_rehaciendo_renglones(client, ha):
    dc = doc(client, ha, tipo="compra", fecha="2026-02-03", tercero="Patricia Rojas",
             renglones=[
                 {"tipo": "queso", "kilos_brutos": "820.53", "precio_kilo": "14317"},
                 {"tipo": "queso", "kilos_brutos": "137.45", "precio_kilo": "15033"},
             ])
    r = client.put(f"{API}/documentos/{dc['id']}", json={
        "tipo": "compra",
        "renglones": [
            {"tipo": "queso", "kilos_brutos": "820.53", "precio_kilo": "14317"},
            {"tipo": "queso", "kilos_brutos": "44.23", "precio_kilo": "15033"},
            {"tipo": "queso", "kilos_brutos": "12.07", "precio_kilo": "13871"},
        ]}, headers=ha)
    print("\n   rehacer los renglones ->", r.status_code, r.text[:200])
    assert r.status_code == 200, r.text
    leido = leer_doc(client, ha, dc["id"])
    cuadre_del_documento(leido, "factura rehecha")
    esperado = (D("820.53") * D("14317") + D("44.23") * D("15033")
                + D("12.07") * D("13871")).quantize(D("0.01"))
    print("   total:", leido["total"], " esperado:", esperado)
    assert D(leido["total"]) == esperado
    res = resumen(client, ha)
    regla_de_oro(res, "factura rehecha")
    assert D(res["total_compras"]) == esperado
    assert D(res["kilos_comprados"]) == D("820.53") + D("44.23") + D("12.07")


def test_zzaudit_editar_la_factura_con_abonos_encima(client, ha):
    abastecer(client, ha)
    dv = doc(client, ha, tipo="venta", fecha="2026-02-12", tercero="Don José Pérez",
             renglones=[
                 {"tipo": "queso", "kilos": "44.23", "precio_kilo": "21533"},
                 {"tipo": "queso", "kilos": "137.45", "precio_kilo": "20917"},
             ])
    r = client.post(f"{API}/documentos/{dv['id']}/abonos",
                    json={"fecha": "2026-02-13", "valor": "1000000.07"}, headers=ha)
    assert r.status_code in (200, 201), r.text
    r = client.put(f"{API}/documentos/{dv['id']}", json={
        "tipo": "venta",
        "renglones": [{"tipo": "queso", "kilos": "12.07", "precio_kilo": "19833.33"}]},
        headers=ha)
    print("\n   rehacer renglones con abonos encima ->", r.status_code, r.text[:250])
    leido = leer_doc(client, ha, dv["id"])
    cuadre_del_documento(leido, "tras intentar rehacerla con abonos")
    print("   total:", leido["total"], " abonado:", leido["abonado"],
          " saldo:", leido["saldo"])
    assert D(leido["abonado"]) == D("1000000.07"), (
        "el abono ya registrado se movió al rehacer los renglones"
    )
    if r.status_code == 200:
        assert D(leido["total"]) >= D(leido["abonado"]), (
            f"la factura quedó con un total ({leido['total']}) menor que lo ya "
            f"abonado ({leido['abonado']})"
        )
