"""Cuarto lote de QA del libro anterior: los siete hallazgos que se corrigieron.

Cada prueba imprime la evidencia con números, porque el usuario verifica los
desgloses a mano con calculadora. Los casos NO repiten los del auditor
(tests/test_zz_auditor_contaminacion.py): aquí se fija el comportamiento nuevo.
"""
import io
from decimal import Decimal

import pytest
from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PERIODO = {"desde": "2026-07-01", "hasta": "2026-07-31"}


def D(valor):
    return Decimal(str(valor))


def suma(filas, campo):
    return sum((D(f[campo]) for f in filas), Decimal("0"))


def resumen(client, headers):
    r = client.get(f"{API}/resumen", params=PERIODO, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def compra(client, headers, **datos):
    r = client.post(f"{API}/compras", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, headers, **datos):
    r = client.post(f"{API}/ventas", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def saldo_anterior(client, headers, **datos):
    r = client.post(f"{API}/saldos-anteriores", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def abonar_saldo(client, headers, saldo_id, **datos):
    r = client.post(f"{API}/saldos-anteriores/{saldo_id}/abonos", json=datos, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def texto_pdf(contenido):
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)


def detalle(client, headers):
    """Imprime el detalle por productor y devuelve (filas, resumen)."""
    datos = resumen(client, headers)
    for fila in datos["por_productor"]:
        print(f"    {fila['productor']:22} compras={fila['compras']} "
              f"kilos={fila['kilos']:>8} comprado={fila['total_comprado']:>12} "
              f"por_pagar={fila['por_pagar']:>12} ganancia={fila['ganancia_estimada']}")
    columna = suma(datos["por_productor"], "por_pagar")
    print(f"    suma(por_pagar)={columna}  tarjeta={datos['por_pagar_productores']}  "
          f"del libro={datos['por_pagar_libro_anterior']}")
    return datos, columna


# ---------------------------------------------------------------------------
# 1. El detalle por productor sale del conjunto HISTÓRICO de deudores
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("compras_viejas", "con_libro"),
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["sin-viejas-sin-libro", "viejas-sin-libro", "libro-sin-viejas", "viejas-y-libro"],
)
def test_1_columna_por_pagar_cuadra_en_los_cuatro_casos(
    client, base_datos, compras_viejas, con_libro
):
    """La columna `por_pagar` es histórica, así que las filas también.

    Antes las filas solo salían de los productores con compras EN EL PERÍODO: a
    quien se le compró en mayo y no se le pagó no aparecía en ninguna fila y
    faltaba su plata en la columna (descuadre PREVIO al libro anterior).
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="800", borona_kilos="50", precio_kilo="18000")
    compra(client, h, fecha="2026-07-05", productor="Carlos Ricaute",
           kilos_brutos="300", precio_kilo="17500")
    venta(client, h, fecha="2026-07-10", cliente="Alba Nieto", kilos="400",
          precio_kilo="19500", pagada_de_contado=True, gasto_por_kilo="300",
          gasto_concepto="Flete")
    if compras_viejas:
        # Compra vigente y sin abonar, FUERA del período consultado
        compra(client, h, fecha="2026-05-10", productor="Mixto",
               kilos_brutos="100", precio_kilo="10000")
    if con_libro:
        saldo_anterior(client, h, tipo="pagar", tercero="Mixto", fecha="2026-01-02",
                       concepto="Compra vieja del libro", valor_total="700000")

    print(f"\n--- 1. detalle por productor (viejas={compras_viejas} libro={con_libro}) ---")
    datos, columna = detalle(client, h)
    assert columna == D(datos["por_pagar_productores"]), "el desglose NO suma la cifra grande"

    filas_mixto = [f for f in datos["por_productor"] if f["productor"] == "Mixto"]
    if compras_viejas or con_libro:
        assert len(filas_mixto) == 1, "fila duplicada o ausente"
        esperado = (D("1000000") if compras_viejas else Decimal("0")) + (
            D("700000") if con_libro else Decimal("0")
        )
        print(f"    fila de 'Mixto': por_pagar={filas_mixto[0]['por_pagar']} "
              f"(esperado {esperado})")
        assert D(filas_mixto[0]["por_pagar"]) == esperado
        # Sin compras en el período: la fila no puede inventar kilos ni ganancia
        assert filas_mixto[0]["compras"] == 0
        assert D(filas_mixto[0]["kilos"]) == 0
        assert D(filas_mixto[0]["total_comprado"]) == 0
        assert D(filas_mixto[0]["ganancia_estimada"]) == 0
    else:
        assert filas_mixto == []


def test_1b_productor_de_compra_vieja_ya_pagada_no_genera_fila(client, base_datos):
    """Si la compra vieja ya se pagó no hay deuda: la fila sobraría."""
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Del Periodo",
           kilos_brutos="100", precio_kilo="10000")
    vieja = compra(client, h, fecha="2026-05-10", productor="Ya Pagado",
                   kilos_brutos="50", precio_kilo="10000")
    r = client.post(f"{API}/compras/{vieja['id']}/abonos",
                    json={"fecha": "2026-05-11", "valor": "500000"}, headers=h)
    assert r.status_code == 200, r.text
    print("\n--- 1b. compra vieja ya pagada ---")
    datos, columna = detalle(client, h)
    assert [f["productor"] for f in datos["por_productor"]] == ["Del Periodo"]
    assert columna == D(datos["por_pagar_productores"]) == D("1000000")


# ---------------------------------------------------------------------------
# 2. No se puede dejar el total por debajo de lo ya abonado
# ---------------------------------------------------------------------------
def test_2_editar_un_saldo_no_lo_puede_dejar_negativo(client, base_datos):
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="100", precio_kilo="18000")
    venta(client, h, fecha="2026-07-10", cliente="Alba Nieto", kilos="50",
          precio_kilo="19500")
    antes = resumen(client, h)
    s = saldo_anterior(client, h, tipo="cobrar", tercero="Alba Nieto",
                       fecha="2026-03-04", concepto="Venta vieja",
                       valor_total="5000000", abonado="5000000")
    r = client.put(f"{API}/saldos-anteriores/{s['id']}",
                   json={"valor_total": "1000000"}, headers=h)
    detalle_error = r.json().get("error", {}).get("detail")
    print("\n--- 2. bajar el total por debajo de lo abonado ---")
    print(f"    saldo 'cobrar' de $5.000.000 con $5.000.000 abonados")
    print(f"    PUT valor_total=1.000.000 -> {r.status_code} {detalle_error}")
    assert r.status_code == 422
    assert "$5.000.000" in detalle_error, "el mensaje debe decir cuánto hay abonado"

    despues = resumen(client, h)
    print(f"    por_cobrar_clientes: {antes['por_cobrar_clientes']} -> "
          f"{despues['por_cobrar_clientes']}")
    print(f"    por_cobrar_libro_anterior = {despues['por_cobrar_libro_anterior']} "
          f"(el saldo quedó pagado, no negativo)")
    assert D(despues["por_cobrar_libro_anterior"]) == 0
    assert D(despues["por_cobrar_clientes"]) == D(antes["por_cobrar_clientes"])

    # Y el estado de cuenta del cliente no le rebaja la deuda
    cuenta = client.get(f"{API}/estado-cuenta", params={"cliente": "Alba Nieto"},
                        headers=h).json()
    print(f"    estado de cuenta: libro_saldo={cuenta['libro_anterior_saldo']} "
          f"saldo={cuenta['saldo']}")
    assert D(cuenta["libro_anterior_saldo"]) == 0
    assert D(cuenta["saldo"]) == D("975000")

    # Bajarlo hasta lo abonado sí se permite (queda en cero, no negativo)
    ok = client.put(f"{API}/saldos-anteriores/{s['id']}",
                    json={"valor_total": "5000000"}, headers=h)
    print(f"    PUT valor_total=5.000.000 (igual a lo abonado) -> {ok.status_code} "
          f"saldo={ok.json().get('saldo')}")
    assert ok.status_code == 200 and D(ok.json()["saldo"]) == 0


def test_2b_mismo_guardia_en_las_compras(client, base_datos):
    """El patrón heredado también dejaba negativa una compra del sistema."""
    h = auth_headers(client, "admin.a")
    c = compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
               kilos_brutos="100", precio_kilo="10000")
    r = client.post(f"{API}/compras/{c['id']}/abonos",
                    json={"fecha": "2026-07-03", "valor": "800000"}, headers=h)
    assert r.status_code == 200, r.text
    print("\n--- 2b. misma maniobra en una COMPRA ---")
    r = client.put(f"{API}/compras/{c['id']}", json={"precio_kilo": "5000"}, headers=h)
    print(f"    compra de $1.000.000 con $800.000 abonados, PUT precio=5.000 "
          f"(total 500.000) -> {r.status_code} "
          f"{r.json().get('error', {}).get('detail')}")
    assert r.status_code == 422
    datos = resumen(client, h)
    print(f"    por_pagar_productores = {datos['por_pagar_productores']} (sigue en 200.000)")
    assert D(datos["por_pagar_productores"]) == D("200000")
    # Subirlo o dejarlo por encima de lo abonado sigue permitido
    ok = client.put(f"{API}/compras/{c['id']}", json={"precio_kilo": "9000"}, headers=h)
    print(f"    PUT precio=9.000 (total 900.000) -> {ok.status_code} "
          f"saldo={ok.json().get('saldo')}")
    assert ok.status_code == 200 and D(ok.json()["saldo"]) == D("100000")


# ---------------------------------------------------------------------------
# 3. El PDF no le puede negar al cliente un pago que sí hizo
# ---------------------------------------------------------------------------
def test_3_pdf_no_niega_el_abono_al_saldo_del_libro(client, base_datos):
    h = auth_headers(client, "admin.a")
    s = saldo_anterior(client, h, tipo="cobrar", tercero="Hilda", fecha="2026-02-10",
                       concepto="Factura 045", valor_total="1000000")
    abonar_saldo(client, h, s["id"], fecha="2026-07-20", valor="400000")
    r = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": "Hilda"}, headers=h)
    assert r.status_code == 200, r.text
    impreso = texto_pdf(r.content)
    print("\n--- 3. PDF de un cliente que solo abonó al libro anterior ---")
    print(f"    'Sin pagos registrados' presente? "
          f"{'Sin pagos registrados' in impreso}")
    print(f"    aclara que son los de ESTE sistema? "
          f"{'Sin pagos recibidos por compras registradas en este sistema.' in impreso}")
    print(f"    remite a la columna Abonado? "
          f"{'columna \"Abonado\"' in impreso}")
    assert "Sin pagos registrados" not in impreso
    assert "Sin pagos recibidos por compras registradas en este sistema." in impreso
    assert 'columna "Abonado"' in impreso
    # El abono sí está en la sección de arriba y el documento sigue cuadrando
    assert "Saldos de la cuenta anterior" in impreso
    cuenta = client.get(f"{API}/estado-cuenta", params={"cliente": "Hilda"},
                        headers=h).json()
    print(f"    facturado={cuenta['total_facturado']} abonado={cuenta['total_abonado']} "
          f"libro_total={cuenta['libro_anterior_total']} "
          f"libro_abonado={cuenta['libro_anterior_abonado']} saldo={cuenta['saldo']}")
    assert D(cuenta["libro_anterior_abonado"]) == D("400000")
    assert D(cuenta["saldo"]) == D("600000")


def test_3b_con_pagos_del_sistema_y_del_libro_se_remite_a_la_otra_seccion(
    client, base_datos
):
    """Con las dos cosas, la tabla de pagos no puede leerse como la lista completa."""
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="100", precio_kilo="10000")
    venta(client, h, fecha="2026-07-10", cliente="Hilda", kilos="50",
          precio_kilo="20000", pagada_de_contado=True)
    s = saldo_anterior(client, h, tipo="cobrar", tercero="Hilda", fecha="2026-02-10",
                       concepto="Factura 045", valor_total="1000000")
    abonar_saldo(client, h, s["id"], fecha="2026-07-20", valor="400000")
    r = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": "Hilda"}, headers=h)
    assert r.status_code == 200, r.text
    impreso = texto_pdf(r.content)
    print("\n--- 3b. PDF con pagos del sistema Y del libro ---")
    print(f"    tabla 'Pagos recibidos' presente? {'Pagos recibidos' in impreso}")
    print(f"    remite a la columna Abonado? {'columna \"Abonado\"' in impreso}")
    assert "Pagos recibidos" in impreso
    assert 'columna "Abonado"' in impreso
    # El pago del sistema (1.000.000 de contado) sigue impreso
    assert "$1.000.000" in impreso


# ---------------------------------------------------------------------------
# 4. El resumen del PDF se puede reproducir con la calculadora
# ---------------------------------------------------------------------------
def test_4_saldo_a_favor_con_libro_deja_explicita_la_operacion(client, base_datos):
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="100", precio_kilo="10000")
    v = venta(client, h, fecha="2026-07-10", cliente="Hilda", kilos="100",
              precio_kilo="30000", pagada_de_contado=True)
    # Se le rebaja el precio a la venta ya pagada: queda con saldo a favor
    r = client.put(f"{API}/ventas/{v['id']}", json={"precio_kilo": "10000"}, headers=h)
    assert r.status_code == 200, r.text
    saldo_anterior(client, h, tipo="cobrar", tercero="Hilda", fecha="2026-02-10",
                   concepto="Factura 045", valor_total="500000")

    cuenta = client.get(f"{API}/estado-cuenta", params={"cliente": "Hilda"},
                        headers=h).json()
    print("\n--- 4. saldo a favor con saldo de la cuenta anterior ---")
    print(f"    facturado={cuenta['total_facturado']} abonado={cuenta['total_abonado']} "
          f"libro_saldo={cuenta['libro_anterior_saldo']} saldo={cuenta['saldo']}")
    assert D(cuenta["saldo"]) == D("-1500000")

    r = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": "Hilda"}, headers=h)
    assert r.status_code == 200, r.text
    impreso = texto_pdf(r.content)
    for esperado in (
        "(-) Total abonado",
        "(+) Saldo de la cuenta anterior",
        "SALDO A FAVOR DEL CLIENTE",
        "$1.000.000 - $3.000.000 + $500.000 = -$1.500.000",
    ):
        print(f"    {esperado!r} impreso? {esperado in impreso}")
        assert esperado in impreso, f"falta en el PDF: {esperado}"
    assert "SALDO PENDIENTE" not in impreso


def test_4b_sin_saldo_a_favor_el_resumen_no_cambia(client, base_datos):
    """El caso normal (con deuda) no puede llevar la nota del saldo a favor."""
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="100", precio_kilo="10000")
    venta(client, h, fecha="2026-07-10", cliente="Hilda", kilos="50",
          precio_kilo="20000")
    saldo_anterior(client, h, tipo="cobrar", tercero="Hilda", fecha="2026-02-10",
                   concepto="Factura 045", valor_total="500000")
    r = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": "Hilda"}, headers=h)
    impreso = texto_pdf(r.content)
    print("\n--- 4b. cliente con deuda ---")
    print(f"    'SALDO PENDIENTE' impreso? {'SALDO PENDIENTE' in impreso}")
    print(f"    'queda a favor suyo' impreso? {'queda a favor suyo' in impreso}")
    assert "SALDO PENDIENTE" in impreso
    assert "queda a favor suyo" not in impreso
    # 1.000.000 - 0 + 500.000 = 1.500.000
    assert "$1.500.000" in impreso


# ---------------------------------------------------------------------------
# 5. El autocompletado ofrece los terceros del libro, cada uno de su lado
# ---------------------------------------------------------------------------
def test_5_sugerencias_incluyen_los_terceros_del_libro(client, base_datos):
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="100", precio_kilo="10000")
    venta(client, h, fecha="2026-07-10", cliente="Alba Nieto", kilos="10",
          precio_kilo="20000")
    saldo_anterior(client, h, tipo="cobrar", tercero="Pedro Solo Libro",
                   fecha="2026-02-10", concepto="Factura 045", valor_total="1000000")
    saldo_anterior(client, h, tipo="pagar", tercero="Prod Solo Libro",
                   fecha="2026-01-20", concepto="Factura 8", valor_total="900000")
    # El mismo tercero en los dos lados no se duplica ni se ofrece dos veces
    saldo_anterior(client, h, tipo="pagar", tercero="Sebastián Ruiz",
                   fecha="2026-01-21", concepto="Compra vieja", valor_total="500000")

    s = client.get(f"{API}/sugerencias", headers=h).json()
    print("\n--- 5. sugerencias ---")
    print(f"    clientes    = {s['clientes']}")
    print(f"    productores = {s['productores']}")
    assert s["clientes"] == ["Alba Nieto", "Pedro Solo Libro"]
    assert s["productores"] == ["Prod Solo Libro", "Sebastián Ruiz"]

    # La primera venta a ese cliente adopta la escritura ya guardada
    nueva = venta(client, h, fecha="2026-07-11", cliente="pedro solo libro",
                  kilos="10", precio_kilo="20000")
    print(f"    venta escrita 'pedro solo libro' -> {nueva['cliente']!r}")
    assert nueva["cliente"] == "Pedro Solo Libro"
    # Y la primera compra a ese productor, igual
    nueva_c = compra(client, h, fecha="2026-07-12", productor="prod   solo libro",
                     kilos_brutos="10", precio_kilo="10000")
    print(f"    compra escrita 'prod   solo libro' -> {nueva_c['productor']!r}")
    assert nueva_c["productor"] == "Prod Solo Libro"

    # El estado de cuenta junta la deuda vieja con la venta nueva (un solo cliente)
    cuenta = client.get(f"{API}/estado-cuenta", params={"cliente": "Pedro Solo Libro"},
                        headers=h).json()
    print(f"    estado de cuenta: cliente={cuenta['cliente']!r} "
          f"compras={cuenta['compras']} saldo={cuenta['saldo']}")
    assert cuenta["compras"] == 1
    assert D(cuenta["saldo"]) == D("1200000")


def test_5b_los_dos_lados_no_se_mezclan(client, base_datos):
    """Un tercero del libro por 'pagar' es productor: no puede salir de cliente."""
    h = auth_headers(client, "admin.a")
    saldo_anterior(client, h, tipo="pagar", tercero="Solo Productor",
                   fecha="2026-01-20", concepto="Factura 8", valor_total="900000")
    saldo_anterior(client, h, tipo="cobrar", tercero="Solo Cliente",
                   fecha="2026-01-21", concepto="Factura 9", valor_total="800000")
    s = client.get(f"{API}/sugerencias", headers=h).json()
    print("\n--- 5b. cada lado por separado ---")
    print(f"    clientes={s['clientes']} productores={s['productores']}")
    assert s["clientes"] == ["Solo Cliente"]
    assert s["productores"] == ["Solo Productor"]


# ---------------------------------------------------------------------------
# 6. El tipo de un saldo no se cambia por PUT
# ---------------------------------------------------------------------------
def test_6_no_se_puede_cambiar_el_tipo_del_saldo(client, base_datos):
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="100", precio_kilo="10000")
    venta(client, h, fecha="2026-07-10", cliente="Alba Nieto", kilos="50",
          precio_kilo="20000")
    s = saldo_anterior(client, h, tipo="cobrar", tercero="Alba Nieto",
                       fecha="2026-03-05", concepto="Cambia de lado",
                       valor_total="1000000", abonado="200000")
    r = client.put(f"{API}/saldos-anteriores/{s['id']}", json={"tipo": "pagar"},
                   headers=h)
    print("\n--- 6. cambiar el tipo por PUT ---")
    print(f"    cobrar -> pagar: {r.status_code} "
          f"{r.json().get('error', {}).get('detail')}")
    assert r.status_code == 422

    datos = resumen(client, h)
    print(f"    libro_cobrar={datos['por_cobrar_libro_anterior']} "
          f"libro_pagar={datos['por_pagar_libro_anterior']}")
    print(f"    filas por productor: "
          f"{[f['productor'] for f in datos['por_productor']]}")
    assert D(datos["por_cobrar_libro_anterior"]) == D("800000")
    assert D(datos["por_pagar_libro_anterior"]) == 0
    assert "Alba Nieto" not in [f["productor"] for f in datos["por_productor"]]

    # La deuda sigue saliendo en el estado de cuenta de la clienta
    cuenta = client.get(f"{API}/estado-cuenta", params={"cliente": "Alba Nieto"},
                        headers=h).json()
    print(f"    estado de cuenta: libro_saldo={cuenta['libro_anterior_saldo']}")
    assert D(cuenta["libro_anterior_saldo"]) == D("800000")

    # Mandar el MISMO tipo (el formulario envía todo el objeto) sí se acepta
    ok = client.put(f"{API}/saldos-anteriores/{s['id']}",
                    json={"tipo": "cobrar", "concepto": "Corregido"}, headers=h)
    print(f"    mismo tipo 'cobrar' + concepto nuevo -> {ok.status_code} "
          f"concepto={ok.json().get('concepto')!r}")
    assert ok.status_code == 200 and ok.json()["concepto"] == "Corregido"


# ---------------------------------------------------------------------------
# 7. Los dos desgloses de la ganancia suman EXACTO la cifra grande
# ---------------------------------------------------------------------------
def test_7_los_desgloses_de_la_ganancia_suman_exacto(client, base_datos):
    """El reparto por productor ya no se desvía por centavos.

    Con 1.100 kg comprados y (ventas - gastos) = 13.040.000 el valor por kilo no
    es exacto: repartirlo con la cifra ya redondeada a dos decimales dejaba la
    columna 5 pesos por debajo de la ganancia del período.
    """
    h = auth_headers(client, "admin.a")
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="800", borona_kilos="50", precio_kilo="18000")
    compra(client, h, fecha="2026-07-05", productor="Carlos Ricaute",
           kilos_brutos="300", precio_kilo="17500")
    venta(client, h, fecha="2026-07-10", cliente="Alba Nieto", kilos="400",
          precio_kilo="19500", pagada_de_contado=True, gasto_por_kilo="300",
          gasto_concepto="Flete")
    venta(client, h, fecha="2026-07-12", cliente="Yojan Pérez", kilos="250",
          precio_kilo="20000")
    venta(client, h, fecha="2026-07-18", cliente="Alba Nieto", tipo="borona",
          kilos="40", precio_kilo="9000")

    datos = resumen(client, h)
    por_producto = suma(datos["por_producto"], "ganancia")
    por_productor = suma(datos["por_productor"], "ganancia_estimada")
    print("\n--- 7. cuadre de los dos desgloses de la ganancia ---")
    print(f"    kilos_comprados={datos['kilos_comprados']} "
          f"ventas-gastos={D(datos['total_ventas']) - D(datos['total_gastos'])} "
          f"valor_realizado_kilo={datos['valor_realizado_kilo']}")
    for fila in datos["por_productor"]:
        print(f"    {fila['productor']:22} kilos={fila['kilos']:>8} "
              f"ganancia={fila['ganancia_estimada']:>14}")
    print(f"    suma(por_producto.ganancia)          = {por_producto}")
    print(f"    suma(por_productor.ganancia_estimada)= {por_productor}")
    print(f"    ganancia_estimada                    = {datos['ganancia_estimada']}")
    assert por_producto == D(datos["ganancia_estimada"])
    assert por_productor == D(datos["ganancia_estimada"])


def test_7b_el_cuadre_aguanta_precios_con_centavos(client, base_datos):
    """Peor caso del redondeo: kilos y precios que no dividen exacto."""
    h = auth_headers(client, "admin.a")
    for productor, kilos, precio in (
        ("Uno", "7", "14285.71"),
        ("Dos", "3", "9999.99"),
        ("Tres", "0.33", "33333.33"),
    ):
        compra(client, h, fecha="2026-07-03", productor=productor,
               kilos_brutos=kilos, precio_kilo=precio)
    venta(client, h, fecha="2026-07-04", cliente="Cliente R", kilos="3",
          precio_kilo="33333.33", gasto_por_kilo="333.33")
    saldo_anterior(client, h, tipo="pagar", tercero="Cuatro Del Libro",
                   fecha="2026-01-01", concepto="Con centavos",
                   valor_total="333333.33", abonado="0.01")

    datos = resumen(client, h)
    print("\n--- 7b. centavos ---")
    for fila in datos["por_productor"]:
        print(f"    {fila['productor']:22} kilos={fila['kilos']:>8} "
              f"ganancia={fila['ganancia_estimada']:>14} "
              f"por_pagar={fila['por_pagar']}")
    print(f"    suma(ganancia)={suma(datos['por_productor'], 'ganancia_estimada')} "
          f"ganancia_estimada={datos['ganancia_estimada']}")
    print(f"    suma(por_pagar)={suma(datos['por_productor'], 'por_pagar')} "
          f"tarjeta={datos['por_pagar_productores']}")
    assert suma(datos["por_producto"], "ganancia") == D(datos["ganancia_estimada"])
    assert suma(datos["por_productor"], "ganancia_estimada") == D(
        datos["ganancia_estimada"]
    )
    assert suma(datos["por_productor"], "por_pagar") == D(
        datos["por_pagar_productores"]
    )
