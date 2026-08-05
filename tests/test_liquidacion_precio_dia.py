"""Corregir el precio por litro de un día desde el comprobante de liquidación.

Sale de un caso real: el dueño abrió la liquidación de un proveedor, vio $1.800
el litro y dijo "no era 1.800, sería bueno poder editarlo acá". Antes, arreglarlo
obligaba a anular la liquidación completa, porque una recepción ya liquidada no
se deja editar desde Recepción diaria.

Lo que se fija aquí es lo que el dueño verifica a mano con el cuaderno:
  (a) corregir el precio en BORRADOR recalcula el valor del día Y los totales,
      y las partes siguen sumando EXACTO la cifra grande;
  (b) sobre una liquidación aprobada o anulada, el endpoint rebota (el guardia
      está en el backend, no en la pantalla);
  (c) la liquidación de otra empresa no se toca ni sabiendo el id.
"""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"


def D(v):
    return Decimal(str(v))


def _proveedor_con_recepciones(client, headers, dias, precio="1800", nombre="Yubijildo triviño"):
    """Un proveedor con una recepción por día y su liquidación en borrador."""
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=headers,
    ).json()
    for fecha, litros in dias:
        r = client.post(
            "/api/v1/recepciones",
            json={
                "fecha": fecha,
                "proveedor_id": proveedor["id"],
                "cantidad_litros": str(litros),
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
    liquidaciones = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"},
        headers=headers,
    ).json()["generadas"]
    liq = next(x for x in liquidaciones if x["proveedor_id"] == proveedor["id"])
    return proveedor, liq


def _cambiar_precio(client, headers, liq_id, detalle_id, precio):
    return client.put(
        f"{API}/{liq_id}/detalles/{detalle_id}",
        json={"precio_litro": str(precio)},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# (a) En borrador: recalcula el día, recalcula los totales y todo cuadra
# ---------------------------------------------------------------------------
def test_corregir_precio_en_borrador_recalcula_dia_y_totales(client, base_datos):
    """El día corregido tiene que valer litros × precio nuevo, y el resumen de
    abajo (total litros, precio promedio, valor bruto, valor total, saldo) tiene
    que quedar al día. De nada sirve arreglar la fila si el total sigue viejo:
    el dueño suma la columna y compara.
    """
    h = auth_headers(client, "admin.a")
    _, liq = _proveedor_con_recepciones(
        client, h, [("2026-06-01", "100"), ("2026-06-02", "150")]
    )

    print("\n===== (a) CORREGIR EL PRECIO DE UN DÍA =====")
    print(f"  antes  · valor bruto: {liq['valor_bruto']} · total: {liq['valor_total']}")
    assert float(liq["valor_total"]) == 250 * 1800

    dia_1 = next(d for d in liq["detalles"] if d["fecha"] == "2026-06-01")
    r = _cambiar_precio(client, h, liq["id"], dia_1["id"], "1750")
    assert r.status_code == 200, r.text
    actualizada = r.json()

    dia_corregido = next(d for d in actualizada["detalles"] if d["fecha"] == "2026-06-01")
    dia_intacto = next(d for d in actualizada["detalles"] if d["fecha"] == "2026-06-02")
    print(f"  día 01 · 100 L × $1.750 = {dia_corregido['valor']}")
    print(f"  día 02 · 150 L × $1.800 = {dia_intacto['valor']} (sin tocar)")
    print(f"  después · valor bruto: {actualizada['valor_bruto']} · "
          f"total: {actualizada['valor_total']} · saldo: {actualizada['saldo']}")

    assert float(dia_corregido["precio_litro"]) == 1750
    assert float(dia_corregido["valor"]) == 100 * 1750
    # El otro día no se movió: se corrige UN día, no la liquidación entera
    assert float(dia_intacto["precio_litro"]) == 1800
    assert float(dia_intacto["valor"]) == 150 * 1800

    esperado = 100 * 1750 + 150 * 1800
    assert float(actualizada["total_litros"]) == 250
    assert float(actualizada["valor_bruto"]) == esperado
    assert float(actualizada["valor_total"]) == esperado
    assert float(actualizada["saldo"]) == esperado
    # Precio promedio ponderado: 445.000 / 250 = 1.780
    assert float(actualizada["precio_promedio"]) == 1780

    # Y recargando desde el servidor sigue igual (quedó guardado, no en memoria)
    recargada = client.get(f"{API}/{liq['id']}", headers=h).json()
    assert float(recargada["valor_total"]) == esperado


def test_las_partes_suman_exacto_la_cifra_grande(client, base_datos):
    """El dueño suma la columna Valor con la calculadora y la compara con el
    VALOR TOTAL del comprobante. Con precios que no dan redondo (litros con
    decimales y precio con centavos) un peso de diferencia es un defecto.

    Se verifican los dos cuadres del comprobante:
      · suma de los días            == valor total
      · bruto + bonif - descuentos  == valor total
    """
    h = auth_headers(client, "admin.a")
    _, liq = _proveedor_con_recepciones(
        client,
        h,
        [("2026-06-01", "227.35"), ("2026-06-02", "183.45"), ("2026-06-03", "199.05")],
    )
    dias = sorted(liq["detalles"], key=lambda d: d["fecha"])

    print("\n===== (b) LAS PARTES SUMAN EXACTO =====")
    # Tres precios feos a propósito: 1.777,33 deja centavos en cada día
    for detalle, precio in zip(dias, ["1777.33", "1810.07", "1755.99"]):
        r = _cambiar_precio(client, h, liq["id"], detalle["id"], precio)
        assert r.status_code == 200, r.text
        liq = r.json()

    suma_dias = sum(D(d["valor"]) for d in liq["detalles"])
    desglose = D(liq["valor_bruto"]) + D(liq["bonificaciones"]) - D(liq["descuentos"])
    total = D(liq["valor_total"])
    for d in sorted(liq["detalles"], key=lambda x: x["fecha"]):
        print(f"  {d['fecha']} · {d['litros']} L × ${d['precio_litro']} = {d['valor']}")
    print(f"  suma de los días          = {suma_dias}")
    print(f"  bruto + bonif - descuento = {desglose}")
    print(f"  VALOR TOTAL               = {total}")
    print(f"  SALDO                     = {liq['saldo']}")

    assert suma_dias == total, "la columna Valor no suma el total"
    assert desglose == total, "el desglose del resumen no da el total"
    assert D(liq["saldo"]) == total - D(liq["anticipos"])


def test_corregir_el_precio_arregla_tambien_la_recepcion_del_dia(client, base_datos):
    """El precio del día NO vive solo en la liquidación: sale de la recepción de
    ese día. Si se corrigiera solo el comprobante, Recepción diaria seguiría
    mostrando $1.800 y el costo de la leche en contabilidad quedaría con la
    cifra vieja: el dueño pagaría una cosa y los libros dirían otra.
    """
    h = auth_headers(client, "admin.a")
    proveedor, liq = _proveedor_con_recepciones(client, h, [("2026-06-01", "100")])
    detalle = liq["detalles"][0]

    liq = _cambiar_precio(client, h, liq["id"], detalle["id"], "1750").json()
    recepcion = client.get("/api/v1/recepciones", headers=h).json()["items"][0]

    print("\n===== (c) LA RECEPCIÓN DEL DÍA TAMBIÉN QUEDA CORREGIDA =====")
    print(f"  recepción · precio: {recepcion['precio_litro']} · "
          f"bruto: {recepcion['valor_bruto']} · neto: {recepcion['valor_neto']}")
    assert float(recepcion["precio_litro"]) == 1750
    assert float(recepcion["valor_bruto"]) == 100 * 1750
    assert float(recepcion["valor_neto"]) == 100 * 1750
    # Y el transporte no se toca: el flete no depende del precio de la leche
    assert float(recepcion["valor_transporte"]) == 0


def test_precio_que_deja_el_dia_en_negativo_rebota_sin_dejar_nada_a_medias(client, base_datos):
    """Con descuentos altos, un precio muy bajo dejaría el día en rojo. Rebota, y
    —esto es lo importante— no puede dejar la recepción corregida y la
    liquidación con los totales viejos.
    """
    h = auth_headers(client, "admin.a")
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Libardo", "vereda": "Granada", "precio_litro": "1800"},
        headers=h,
    ).json()
    client.post(
        "/api/v1/recepciones",
        json={
            "fecha": "2026-06-01", "proveedor_id": proveedor["id"],
            "cantidad_litros": "100", "descuentos": "150000",
        },
        headers=h,
    )
    liq = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"},
        headers=h,
    ).json()["generadas"][0]

    r = _cambiar_precio(client, h, liq["id"], liq["detalles"][0]["id"], "1000")
    print("\n===== (d) PRECIO QUE DEJA EL DÍA EN NEGATIVO =====")
    print(f"  100 L × $1.000 - $150.000 de descuento: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "negativo" in r.json()["error"]["detail"]

    intacta = client.get(f"{API}/{liq['id']}", headers=h).json()
    recepcion = client.get("/api/v1/recepciones", headers=h).json()["items"][0]
    print(f"  liquidación sigue en: {intacta['valor_total']} · "
          f"recepción sigue en: {recepcion['precio_litro']}")
    assert float(intacta["valor_total"]) == 100 * 1800 - 150000
    assert float(recepcion["precio_litro"]) == 1800


# ---------------------------------------------------------------------------
# (b) Fuera de borrador: no se toca, y el guardia vive en el backend
# ---------------------------------------------------------------------------
def test_liquidacion_aprobada_no_deja_cambiar_el_precio(client, base_datos):
    """Aprobada quiere decir que ese precio ya se le pagó a alguien. Esconder el
    campo en pantalla no basta: quien sepa la dirección del endpoint entra igual,
    así que el que dice que no es el backend.
    """
    h = auth_headers(client, "admin.a")
    _, liq = _proveedor_con_recepciones(client, h, [("2026-06-01", "100")])
    detalle = liq["detalles"][0]
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200

    r = _cambiar_precio(client, h, liq["id"], detalle["id"], "1750")
    print("\n===== (e) LIQUIDACIÓN APROBADA =====")
    print(f"  intentar corregir: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "borrador" in r.json()["error"]["detail"]
    assert float(client.get(f"{API}/{liq['id']}", headers=h).json()["valor_total"]) == 100 * 1800


def test_liquidacion_pagada_ni_anulada_dejan_cambiar_el_precio(client, base_datos):
    """Pagada y anulada tampoco: en una la plata ya salió y en la otra el
    período volvió a quedar disponible para re-liquidar."""
    h = auth_headers(client, "admin.a")
    _, pagada = _proveedor_con_recepciones(
        client, h, [("2026-06-01", "100")], nombre="Pagado"
    )
    client.post(f"{API}/{pagada['id']}/aprobar", headers=h)
    client.post(f"{API}/{pagada['id']}/pagar", headers=h)

    _, anulada = _proveedor_con_recepciones(
        client, h, [("2026-06-02", "80")], nombre="Anulado"
    )
    client.post(f"{API}/{anulada['id']}/anular", headers=h)

    print("\n===== (f) PAGADA Y ANULADA =====")
    for etiqueta, liq in (("pagada", pagada), ("anulada", anulada)):
        r = _cambiar_precio(client, h, liq["id"], liq["detalles"][0]["id"], "1750")
        print(f"  {etiqueta}: {r.status_code} · "
              f"{r.json().get('error', {}).get('detail', '')}")
        assert r.status_code == 422


def test_en_liquidacion_de_transportador_no_se_edita_el_precio(client, base_datos):
    """En la del transportador el 'precio' del renglón es la tarifa del flete de
    ese día y agrupa varias recepciones de la ruta. Cambiarla ahí sería otra
    cosa y se cruzaría con el transporte de la liquidación del proveedor; se
    deja por fuera a propósito.
    """
    h = auth_headers(client, "admin.a")
    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"}, headers=h
    ).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Stella", "valor_transporte": "100",
              "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "100"}]},
        headers=h,
    ).json()
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Libardo", "vereda": "Granada", "precio_litro": "1800"},
        headers=h,
    ).json()
    client.post(
        "/api/v1/recepciones",
        json={
            "fecha": "2026-06-01", "proveedor_id": proveedor["id"],
            "transportador_id": transportador["id"], "cantidad_litros": "100",
        },
        headers=h,
    )
    liqs = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "ambos"},
        headers=h,
    ).json()["generadas"]
    liq_t = {x["tipo"]: x for x in liqs}["transportador"]

    r = _cambiar_precio(client, h, liq_t["id"], liq_t["detalles"][0]["id"], "150")
    print("\n===== (g) LIQUIDACIÓN DE TRANSPORTADOR =====")
    print(f"  intentar corregir: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "proveedor" in r.json()["error"]["detail"]


def test_un_dia_de_otra_liquidacion_no_sirve(client, base_datos):
    """Cruzar el id de un día con el de otra liquidación no puede colarse: si no,
    se corregiría el precio de un proveedor desde el comprobante de otro."""
    h = auth_headers(client, "admin.a")
    _, liq_a = _proveedor_con_recepciones(client, h, [("2026-06-01", "100")], nombre="Uno")
    _, liq_b = _proveedor_con_recepciones(client, h, [("2026-06-02", "90")], nombre="Dos")

    r = _cambiar_precio(client, h, liq_a["id"], liq_b["detalles"][0]["id"], "1750")
    print("\n===== (h) DÍA DE OTRA LIQUIDACIÓN =====")
    print(f"  día de 'Dos' contra la liquidación de 'Uno': {r.status_code}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# (c) Multiempresa: la liquidación de otra empresa no se toca
# ---------------------------------------------------------------------------
def test_no_se_puede_tocar_la_liquidacion_de_otra_empresa(client, base_datos):
    """Multiempresa por fila: el admin de la Quesera B no puede corregir un
    precio de la Quesera A ni teniendo los dos ids a la mano. Es el mismo dato
    que un competidor no debería ni ver.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    _, liq_a = _proveedor_con_recepciones(client, h_a, [("2026-06-01", "100")])
    detalle = liq_a["detalles"][0]

    r = _cambiar_precio(client, h_b, liq_a["id"], detalle["id"], "1750")
    print("\n===== (i) OTRA EMPRESA =====")
    print(f"  admin.b contra la liquidación de la empresa A: {r.status_code}")
    assert r.status_code == 404

    sin_cambios = client.get(f"{API}/{liq_a['id']}", headers=h_a).json()
    print(f"  la liquidación de A sigue en: {sin_cambios['valor_total']}")
    assert float(sin_cambios["valor_total"]) == 100 * 1800
    assert float(sin_cambios["detalles"][0]["precio_litro"]) == 1800


def test_precio_invalido_rebota(client, base_datos):
    """Cero, negativo o un precio absurdo (1.800.000 en vez de 1.800) no entran:
    el error de tecleo más común es el que más caro sale."""
    h = auth_headers(client, "admin.a")
    _, liq = _proveedor_con_recepciones(client, h, [("2026-06-01", "100")])
    detalle = liq["detalles"][0]

    print("\n===== (j) PRECIOS INVÁLIDOS =====")
    for precio in ("0", "-500", "1800000.01"):
        r = _cambiar_precio(client, h, liq["id"], detalle["id"], precio)
        print(f"  precio {precio}: {r.status_code}")
        assert r.status_code == 422
