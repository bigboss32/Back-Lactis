"""AUDITORÍA — QUE TODAS LAS PANTALLAS DE REVENTA DIGAN LA MISMA CIFRA.

El dueño mira cinco sitios y suma con la calculadora: el resumen (con su desglose
por producto y por productor), el panel de lotes (con su detalle por compra y por
venta), la ganancia por día, los estados de cuenta y las temporadas. Acá se exige
que las cifras grandes de cada uno sean EXACTAMENTE la suma de sus renglones, y que
donde dos pantallas hablan de lo mismo digan lo mismo.

Se corre sobre la historia más gorda que la API sabe armar (productos propios del
dueño, subproducto comprado directamente, kilos gratis, ajustes de las dos clases,
un producto por unidades y ventas de los cinco) más un libro anterior y una venta
sobrepagada.
"""
from decimal import Decimal

import pytest

from tests.ayudas_reventa import (
    API,
    PERIODO,
    CERO,
    D,
    compra,
    historia,
    historia_gorda,
    lotes,
    regla_de_oro,
    resumen,
    venta,
)
from tests.conftest import auth_headers


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


def sugerencias(client, h):
    r = client.get(f"{API}/sugerencias", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def ec_cliente(client, h, cliente):
    r = client.get(f"{API}/estado-cuenta", params={"cliente": cliente}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def ec_productor(client, h, productor):
    r = client.get(f"{API}/estado-cuenta-productor",
                   params={"productor": productor}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ============================================================ la cartera
def test_zzaudit_la_cartera_es_la_suma_de_los_estados_de_cuenta(client, h):
    historia_gorda(client, h)
    # Una venta SOBREPAGADA (queda saldo a favor) y un libro anterior de las dos
    # clases: son los dos casos que pueden hacer que una resta se coma la cartera.
    v = venta(client, h, fecha="2026-03-05", cliente="Tienda La Esquina",
              tipo="borona", kilos="4.11", precio_kilo="4133")
    r = client.post(f"{API}/ventas/{v['id']}/abonos",
                    json={"fecha": "2026-03-06", "valor": v["valor_total"]}, headers=h)
    assert r.status_code in (200, 201), r.text
    for tipo, tercero, valor, abonado in (
        ("cobrar", "Don José Pérez", "1833333.33", "333333.33"),
        ("pagar", "Patricia Rojas", "2427600.77", "427600.77"),
    ):
        r = client.post(f"{API}/saldos-anteriores", json={
            "tipo": tipo, "tercero": tercero, "fecha": "2025-12-31",
            "valor_total": valor, "abonado": abonado,
            "concepto": "libro viejo"},
            headers=h)
        assert r.status_code == 201, r.text

    res = resumen(client, h)
    sug = sugerencias(client, h)
    clientes = sorted(set(sug["clientes"]) | {"Don José Pérez"})
    productores = sorted(set(sug["productores"]) | {"Patricia Rojas"})
    print("\n   clientes:", clientes)
    print("   productores:", productores)

    suma_cobrar = CERO
    for c in clientes:
        e = ec_cliente(client, h, c)
        print(f"   EC cliente {c:22} saldo {e['saldo']:>16} a favor {e['saldo_a_favor']}")
        suma_cobrar += D(e["saldo"])
    suma_pagar = CERO
    for p in productores:
        e = ec_productor(client, h, p)
        print(f"   EC productor {p:20} saldo {e['saldo']:>16} a favor {e['saldo_a_favor']}")
        suma_pagar += D(e["saldo"])

    print("   por_cobrar_clientes  :", res["por_cobrar_clientes"], " suma EC:", suma_cobrar)
    print("   por_pagar_productores:", res["por_pagar_productores"], " suma EC:", suma_pagar)
    assert D(res["por_cobrar_clientes"]) == suma_cobrar, (
        f"la tarjeta de por cobrar ({res['por_cobrar_clientes']}) no es la suma de "
        f"los estados de cuenta ({suma_cobrar}); diferencia "
        f"{D(res['por_cobrar_clientes']) - suma_cobrar}"
    )
    assert D(res["por_pagar_productores"]) == suma_pagar, (
        f"la tarjeta de por pagar ({res['por_pagar_productores']}) no es la suma de "
        f"los estados de cuenta ({suma_pagar}); diferencia "
        f"{D(res['por_pagar_productores']) - suma_pagar}"
    )


def test_zzaudit_el_desglose_por_productor_suma_la_cartera(client, h):
    historia_gorda(client, h)
    res = resumen(client, h)
    suma = sum((D(f["por_pagar"]) for f in res["por_productor"]), CERO)
    print("\n   SUMA  por_productor.por_pagar:", suma)
    print("   por_pagar_productores   :", res["por_pagar_productores"])
    assert suma == D(res["por_pagar_productores"]), (
        f"diferencia {suma - D(res['por_pagar_productores'])}"
    )
    ganancias = sum((D(f["ganancia_estimada"]) for f in res["por_productor"]), CERO)
    print("   SUMA  por_productor.ganancia_estimada:", ganancias)
    print("   ganancia_estimada del período    :", res["ganancia_estimada"])


# ============================================================ los lotes
CAMPOS_DE_LA_COMPRA = (
    ("kilos", "kilos_comprados"),
    ("valor_total", "costo_total"),
    ("saldo", "por_pagar"),
    ("borona_recibida", "borona_recibida"),
    ("kilos_vendidos", "kilos_vendidos"),
    ("kilos_a_borona", "kilos_a_borona"),
    ("kilos_merma", "kilos_merma"),
    ("kilos_sin_vender", "kilos_sin_vender"),
    ("borona_vendida", "borona_vendida"),
    ("borona_sin_vender", "borona_sin_vender"),
    ("ingresos", "ingresos"),
    ("gastos", "gastos"),
    ("costo_sin_vender", "costo_sin_vender"),
    ("ganancia", "ganancia"),
)


def test_zzaudit_cada_lote_cierra_por_compra_al_centavo(client, h):
    historia_gorda(client, h)
    pan = lotes(client, h)
    for lote in pan["lotes"]:
        print(f"\n   --- lote {lote['fecha']} ({lote['compras']} compras) ---")
        for campo_compra, campo_lote in CAMPOS_DE_LA_COMPRA:
            suma = sum((D(c[campo_compra]) for c in lote["detalle_compras"]), CERO)
            grande = D(lote[campo_lote])
            marca = "" if suma == grande else "   <<< NO CIERRA"
            print(f"      {campo_lote:20} lote {grande:>16}  SUMA compras {suma:>16}{marca}")
            assert suma == grande, (
                f"lote {lote['fecha']}: '{campo_lote}' vale {grande} y sus compras "
                f"suman {suma} (diferencia {suma - grande})"
            )
        # EL COSTO REALIZADO DE LA COMPRA es lo que se le fue por los tres caminos:
        # lo que se vendió como el producto, lo que se vendió como subproducto y lo
        # que se perdió en merma. Es la única forma de que el detalle por compra
        # cierre contra el encabezado del lote.
        realizado = sum((D(c["costo_realizado"]) for c in lote["detalle_compras"]), CERO)
        tres_caminos = (D(lote["costo_vendido"]) + D(lote["costo_borona_vendida"])
                        + D(lote["costo_merma"]))
        marca = "" if realizado == tres_caminos else "   <<< NO CIERRA"
        print(f"      costo_realizado      SUMA compras {realizado:>16}  "
              f"vendido+borona+merma {tres_caminos:>16}{marca}")
        assert realizado == tres_caminos, (
            f"lote {lote['fecha']}: las compras realizaron {realizado} y el lote "
            f"reporta {tres_caminos} (diferencia {realizado - tres_caminos})"
        )


def test_zzaudit_las_ventas_del_lote_suman_sus_ingresos(client, h):
    historia_gorda(client, h)
    pan = lotes(client, h)
    for lote in pan["lotes"]:
        ingreso = sum((D(v["ingreso"]) for v in lote["detalle_ventas"]), CERO)
        gasto = sum((D(v["gasto"]) for v in lote["detalle_ventas"]), CERO)
        costo = sum((D(v["costo"]) for v in lote["detalle_ventas"]), CERO)
        print(f"\n   lote {lote['fecha']}: ingresos {lote['ingresos']} vs SUMA ventas {ingreso}")
        print(f"      gastos {lote['gastos']} vs SUMA  {gasto}")
        print(f"      costo_vendido+borona {D(lote['costo_vendido'])} vs SUMA  {costo}")
        assert ingreso == D(lote["ingresos"]), (
            f"lote {lote['fecha']}: ingresos {lote['ingresos']} != SUMA  ventas {ingreso}"
        )
        assert gasto == D(lote["gastos"])
        # El costo de las ventas del lote = lo vendido como producto + lo vendido
        # como subproducto (la merma no es una venta y por eso no está acá).
        con_subproducto = D(lote["costo_vendido"]) + D(lote["costo_borona_vendida"])
        assert costo == con_subproducto, (
            f"lote {lote['fecha']}: las ventas costaron {costo} y el lote reporta "
            f"{con_subproducto} (diferencia {costo - con_subproducto})"
        )
        ganancia_ventas = sum((D(v["ganancia"]) for v in lote["detalle_ventas"]), CERO)
        esperado = D(lote["ganancia"]) + D(lote["costo_merma"])
        print(f"      ganancia de las ventas {ganancia_ventas} vs lote+merma {esperado}")
        assert ganancia_ventas == esperado, (
            f"lote {lote['fecha']}: las ventas ganaron {ganancia_ventas} y el lote "
            f"dice {lote['ganancia']} + merma {lote['costo_merma']} = {esperado}"
        )


def test_zzaudit_los_totales_del_panel_suman_los_lotes(client, h):
    historia_gorda(client, h)
    pan = lotes(client, h)
    for campo_lote, campo_total, extra in (
        ("kilos_comprados", "total_kilos_comprados", "kilos_sin_lote"),
        ("costo_total", "total_costo", None),
        ("ingresos", "total_ingresos", "ingreso_sin_lote"),
        ("ganancia", "total_ganancia", None),
        ("por_pagar", "total_por_pagar", None),
        ("kilos_sin_vender", "total_kilos_sin_vender", None),
        ("costo_sin_vender", "total_costo_sin_vender", None),
    ):
        suma = sum((D(l[campo_lote]) for l in pan["lotes"]), CERO)
        if extra:
            suma += D(pan[extra])
        grande = D(pan[campo_total])
        marca = "" if suma == grande else "   <<< NO CIERRA"
        print(f"   {campo_total:26} {grande:>16}  SUMA lotes {suma:>16}{marca}")
        assert suma == grande, (
            f"'{campo_total}' vale {grande} y los lotes suman {suma} "
            f"(diferencia {suma - grande})"
        )


# ==================================================== la ganancia por día
def test_zzaudit_la_ganancia_por_dia_suma_su_encabezado(client, h):
    historia_gorda(client, h)
    r = client.get(f"{API}/ganancia-por-dia", params=PERIODO, headers=h)
    assert r.status_code == 200, r.text
    g = r.json()
    for campo in ("kilos", "ingresos", "costo", "gastos", "ganancia"):
        suma = sum((D(d[campo]) for d in g["dias"]), CERO)
        marca = "" if suma == D(g[campo]) else "   <<< NO CIERRA"
        print(f"   {campo:10} encabezado {g[campo]:>16}  SUMA días {suma:>16}{marca}")
        assert suma == D(g[campo]), (
            f"'{campo}' vale {g[campo]} y los días suman {suma} "
            f"(diferencia {suma - D(g[campo])})"
        )
    for d in g["dias"]:
        esperado = D(d["ingresos"]) - D(d["costo"]) - D(d["gastos"])
        assert D(d["ganancia"]) == esperado, (
            f"el día {d['fecha']}: ganancia {d['ganancia']} != "
            f"{d['ingresos']} - {d['costo']} - {d['gastos']} = {esperado}"
        )


def test_zzaudit_ganancia_por_dia_vs_lotes_y_resumen(client, h):
    """Las tres pantallas hablan del mismo negocio; ¿dicen lo mismo?"""
    historia_gorda(client, h)
    res = resumen(client, h)
    pan = lotes(client, h)
    r = client.get(f"{API}/ganancia-por-dia", params=PERIODO, headers=h)
    g = r.json()
    print("\n   ganancia_por_dia.ingresos :", g["ingresos"])
    print("   lotes.total_ingresos      :", pan["total_ingresos"])
    print("   resumen.total_ventas      :", res["total_ventas"])
    print("   ganancia_por_dia.gastos   :", g["gastos"])
    print("   resumen.total_gastos      :", res["total_gastos"])
    print("   barras fuera del reparto  :", pan["barras_fuera_del_reparto"])
    assert D(g["ingresos"]) == D(pan["total_ingresos"]), (
        f"la ganancia por día dice {g['ingresos']} de ingresos y el panel de lotes "
        f"{pan['total_ingresos']}"
    )


# ==================================================== temporadas == resumen
def test_zzaudit_la_temporada_completa_dice_lo_del_resumen(client, h):
    historia_gorda(client, h)
    res = resumen(client, h)
    r = client.post(f"{API}/temporadas", json={
        "nombre": "Toda 2026", "fecha_inicio": PERIODO["desde"],
        "fecha_fin": PERIODO["hasta"]},
        headers=h)
    assert r.status_code == 201, r.text
    r = client.get(f"{API}/temporadas", headers=h)
    assert r.status_code == 200, r.text
    panel = r.json()
    fila = panel["temporadas"][0]
    print("\n   temporada:", {k: v for k, v in fila.items() if isinstance(v, str)})
    for campo_temporada, campo_resumen in (
        ("total_compras", "total_compras"),
        ("total_ventas", "total_ventas"),
        ("ganancia", "ganancia_estimada"),
        ("kilos_comprados", "kilos_comprados"),
        ("kilos_vendidos", "kilos_vendidos"),
    ):
        if campo_temporada not in fila:
            print(f"   (la temporada no trae '{campo_temporada}')")
            continue
        marca = "" if D(fila[campo_temporada]) == D(res[campo_resumen]) else "  <<< DIFIERE"
        print(f"   {campo_temporada:16} temporada {fila[campo_temporada]:>18} "
              f"resumen {res[campo_resumen]:>18}{marca}")
        assert D(fila[campo_temporada]) == D(res[campo_resumen]), (
            f"la temporada dice {fila[campo_temporada]} y el resumen "
            f"{res[campo_resumen]}"
        )


# ==================================================== la regla de oro, siempre
@pytest.mark.parametrize("cual", ["historia", "gorda"])
def test_zzaudit_el_desglose_suma_el_encabezado(client, h, cual):
    (historia if cual == "historia" else historia_gorda)(client, h)
    res = resumen(client, h)
    for f in res["por_producto"]:
        print(f"   {f['producto']:24} costo {f['costo']:>16} ingreso {f['ingreso']:>16} "
              f"gastos {f['gastos']:>12} ganancia {f['ganancia']:>16}")
    print(f"   {'TOTAL':24} costo {res['total_compras']:>16} "
          f"ingreso {res['total_ventas']:>16} gastos {res['total_gastos']:>12} "
          f"ganancia {res['ganancia_estimada']:>16}")
    regla_de_oro(res, cual)
