"""Compra y venta de queso (reventa) con los números reales del cuaderno.

Ojo con el negocio de HOY: al comprar se paga por TODOS los kilos recibidos
(ya no se descuenta merma en la compra), así que 800 kg brutos son 800 kg
netos. La merma real son solo los ajustes con destino 'merma'; el queso que se
pasa a borona NO es merma, es un subproducto que se vende más barato.
"""
import io

from tests.conftest import auth_headers


def fila_producto(resumen: dict, producto: str) -> dict:
    """Fila del desglose por producto ('queso', 'borona', 'merma', ...)."""
    filas = [f for f in resumen["por_producto"] if f["producto"] == producto]
    assert len(filas) == 1, f"se esperaba una sola fila de {producto}: {resumen['por_producto']}"
    return filas[0]


def test_compra_y_abonos(client, base_datos):
    """Al comprar se paga por todo lo recibido y la compra se puede editar
    aunque ya tenga abonos (se recalcula el estado con lo abonado)."""
    headers = auth_headers(client, "admin.a")

    r = client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-12",
            "productor": "Sebastián",
            "kilos_brutos": "800",
            "borona_kilos": "56.7",
            "precio_kilo": "18000",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    compra = r.json()
    # Sin merma en la compra: 800 kg netos × $18.000 = $14.400.000
    assert float(compra["kilos_netos"]) == 800
    # La merma de la compra quedó obsoleta: ya no se expone en la respuesta
    assert "merma_kilos" not in compra
    assert float(compra["valor_total"]) == 14_400_000
    assert compra["estado"] == "pendiente"

    r = client.post(
        f"/api/v1/reventa/compras/{compra['id']}/abonos",
        json={"fecha": "2026-07-12", "valor": "12100000"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    compra = r.json()
    assert float(compra["abonado"]) == 12_100_000
    assert float(compra["saldo"]) == 2_300_000
    assert compra["estado"] == "parcial"

    # Con abonos SÍ se puede editar: se recalcula el valor y el estado con lo
    # ya abonado. 800 kg × $17.000 = $13.600.000 y sigue quedando saldo.
    r = client.put(
        f"/api/v1/reventa/compras/{compra['id']}",
        json={"precio_kilo": "17000"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    compra = r.json()
    assert float(compra["valor_total"]) == 13_600_000
    assert float(compra["saldo"]) == 1_500_000
    assert compra["estado"] == "parcial"

    # Un abono mayor al saldo se rechaza
    r = client.post(
        f"/api/v1/reventa/compras/{compra['id']}/abonos",
        json={"fecha": "2026-07-13", "valor": "99999999"},
        headers=headers,
    )
    assert r.status_code == 422

    # Completar el pago
    r = client.post(
        f"/api/v1/reventa/compras/{compra['id']}/abonos",
        json={"fecha": "2026-07-15", "valor": "1500000"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "pagada"
    assert float(r.json()["saldo"]) == 0


def test_venta_y_resumen(client, base_datos):
    headers = auth_headers(client, "admin.a")

    client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-12", "productor": "Sebastián",
            "kilos_brutos": "800", "precio_kilo": "18000",
        },
        headers=headers,
    )
    r = client.post(
        "/api/v1/reventa/ventas",
        json={
            "fecha": "2026-07-13", "cliente": "Alba", "kilos": "400",
            "precio_kilo": "19500", "pagada_de_contado": True,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    venta = r.json()
    assert float(venta["valor_total"]) == 7_800_000
    assert venta["estado"] == "pagada"
    assert float(venta["saldo"]) == 0

    r = client.post(
        "/api/v1/reventa/ventas",
        json={"fecha": "2026-07-14", "cliente": "Yojan", "kilos": "100", "precio_kilo": "19500"},
        headers=headers,
    )
    venta_credito = r.json()
    assert venta_credito["estado"] == "pendiente"

    resumen = client.get(
        "/api/v1/reventa/resumen?desde=2026-07-01&hasta=2026-07-31", headers=headers
    ).json()
    assert float(resumen["kilos_comprados"]) == 800
    assert float(resumen["total_compras"]) == 14_400_000
    assert float(resumen["kilos_vendidos"]) == 500
    assert float(resumen["total_ventas"]) == 9_750_000
    assert float(resumen["precio_promedio_compra"]) == 18_000
    assert float(resumen["precio_promedio_venta"]) == 19_500
    # Ganancia del período = ventas - TODA la compra - gastos:
    # 9.750.000 - 14.400.000 - 0 = -4.650.000 (quedan 300 kg sin vender)
    assert float(resumen["ganancia_estimada"]) == -4_650_000
    # Margen por kilo MOVIDO (queso + borona): -4.650.000 / 500 = -9.300
    assert float(resumen["margen_por_kilo"]) == -9_300
    # Neto que dejó cada kilo COMPRADO: 9.750.000 / 800 kg = 12.187,50
    assert float(resumen["valor_realizado_kilo"]) == 12_187.50
    assert float(resumen["kilos_disponibles"]) == 300
    assert float(resumen["por_cobrar_clientes"]) == 100 * 19_500
    # La merma falsa (comprado - vendido) ya no existe en la respuesta
    assert "merma_estimada" not in resumen
    assert float(resumen["kilos_merma"]) == 0
    assert float(resumen["kilos_a_borona"]) == 0
    assert float(resumen["kilos_pendientes"]) == 300

    # Desglose: el queso vendido dejó 9.750.000 - 500 kg × $18.000 = $750.000
    queso = fila_producto(resumen, "queso")
    assert float(queso["kilos"]) == 500
    assert float(queso["ingreso"]) == 9_750_000
    assert float(queso["costo"]) == 9_000_000
    assert float(queso["ganancia"]) == 750_000
    # Los 300 kg que quedaron en inventario son plata invertida sin vender
    pendiente = fila_producto(resumen, "pendiente")
    assert float(pendiente["kilos"]) == 300
    assert float(pendiente["costo"]) == 5_400_000
    assert float(pendiente["ganancia"]) == -5_400_000
    # Invariante: las cuatro filas suman exactamente la ganancia del período
    assert sum(float(f["ganancia"]) for f in resumen["por_producto"]) == float(
        resumen["ganancia_estimada"]
    )


def test_borona_ciclo_completo(client, base_datos):
    headers = auth_headers(client, "admin.a")

    # Compra con 56,7 kg de borona incluida (no se paga, pero entra al inventario)
    client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-12", "productor": "Sebastián", "kilos_brutos": "800",
            "borona_kilos": "56.7", "precio_kilo": "18000",
        },
        headers=headers,
    )
    # Un queso devuelto se pasa a borona (20 kg)
    r = client.post(
        "/api/v1/reventa/conversiones",
        json={"fecha": "2026-07-15", "kilos": "20", "observaciones": "Queso devuelto del viaje"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    # No se puede convertir más queso del disponible
    r = client.post(
        "/api/v1/reventa/conversiones",
        json={"fecha": "2026-07-15", "kilos": "5000"},
        headers=headers,
    )
    assert r.status_code == 422

    # Venta de borona a menor precio
    r = client.post(
        "/api/v1/reventa/ventas",
        json={
            "fecha": "2026-07-16", "cliente": "Alba", "tipo": "borona",
            "kilos": "30", "precio_kilo": "8000", "pagada_de_contado": True,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["tipo"] == "borona"

    resumen = client.get(
        "/api/v1/reventa/resumen?desde=2026-07-01&hasta=2026-07-31", headers=headers
    ).json()
    # Queso: 800 comprados - 0 vendidos - 20 convertidos = 780
    assert float(resumen["kilos_disponibles"]) == 780
    # Borona: 56,7 de compras + 20 convertidos - 30 vendidos = 46,7
    assert float(resumen["borona_disponible"]) == 46.7
    assert float(resumen["kilos_borona_vendidos"]) == 30
    assert float(resumen["total_ventas_borona"]) == 240_000
    # Los 20 kg pasados a borona NO son merma
    assert float(resumen["kilos_a_borona"]) == 20
    assert float(resumen["kilos_merma"]) == 0
    # La compra también cae en el período, así que la ganancia no es solo la
    # venta de borona: 240.000 - 14.400.000 - 0 = -14.160.000
    assert float(resumen["ganancia_estimada"]) == -14_160_000
    # Aunque no se vendió nada de queso el margen ya no da 0: se divide entre
    # los kilos movidos (30 kg de borona). -14.160.000 / 30 = -472.000
    assert float(resumen["margen_por_kilo"]) == -472_000
    # Neto por kilo COMPRADO: 240.000 / 800 = 300
    assert float(resumen["valor_realizado_kilo"]) == 300
    # El residuo del lote NO puede contradecir al inventario disponible: de los
    # 30 kg de borona vendidos solo 20 salieron del queso comprado (los otros
    # 10 vinieron gratis con el lote), así que se restan los 20 convertidos.
    assert float(resumen["kilos_pendientes"]) == 780
    assert float(resumen["kilos_pendientes"]) == float(resumen["kilos_disponibles"])

    # Desglose: la fila de borona se cuesta por los kilos CONVERTIDOS (20), no
    # por los vendidos (30): costear los vendidos inventaba costos no pagados.
    borona = fila_producto(resumen, "borona")
    assert float(borona["kilos"]) == 20
    assert float(borona["kilos_vendidos"]) == 30
    assert float(borona["ingreso"]) == 240_000
    assert float(borona["costo"]) == 360_000  # 20 kg × $18.000
    assert float(borona["ganancia"]) == -120_000
    assert float(fila_producto(resumen, "queso")["kilos"]) == 0
    assert float(fila_producto(resumen, "merma")["kilos"]) == 0
    assert sum(float(f["ganancia"]) for f in resumen["por_producto"]) == float(
        resumen["ganancia_estimada"]
    )

    # No se puede vender más borona de la disponible (46,7 kg)
    r = client.post(
        "/api/v1/reventa/ventas",
        json={"fecha": "2026-07-17", "cliente": "Otro", "tipo": "borona",
              "kilos": "100", "precio_kilo": "8000"},
        headers=headers,
    )
    assert r.status_code == 422
    assert "borona" in r.json()["error"]["detail"].lower()


def test_ganancia_por_producto_y_productor(client, base_datos):
    """Caso real del usuario: 12 kg comprados que se pasaron a borona y se
    vendieron como borona. No son merma ni quedaron "sin vender"."""
    headers = auth_headers(client, "admin.a")

    r = client.post(
        "/api/v1/reventa/compras",
        json={"fecha": "2026-07-10", "productor": "Sebastián",
              "kilos_brutos": "12", "precio_kilo": "15000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert float(r.json()["valor_total"]) == 180_000

    # Los 12 kg se pasan a borona
    r = client.post(
        "/api/v1/reventa/conversiones",
        json={"fecha": "2026-07-11", "kilos": "12", "destino": "borona"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    # Y se venden como borona: 12 kg × $18.000 = $216.000
    r = client.post(
        "/api/v1/reventa/ventas",
        json={"fecha": "2026-07-12", "cliente": "Alba", "tipo": "borona",
              "kilos": "12", "precio_kilo": "18000", "pagada_de_contado": True},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert float(r.json()["valor_total"]) == 216_000

    resumen = client.get(
        "/api/v1/reventa/resumen?desde=2026-07-01&hasta=2026-07-31", headers=headers
    ).json()
    # El bug: estos 12 kg se contaban como merma / "sin vender"
    assert float(resumen["kilos_merma"]) == 0
    assert float(resumen["kilos_a_borona"]) == 12
    assert float(resumen["kilos_pendientes"]) == 0
    assert float(resumen["ganancia_estimada"]) == 36_000
    # Sin ventas de queso el margen por kilo ya no da 0: 36.000 / 12 = 3.000
    assert float(resumen["margen_por_kilo"]) == 3_000
    assert float(resumen["valor_realizado_kilo"]) == 18_000

    borona = fila_producto(resumen, "borona")
    assert float(borona["kilos"]) == 12
    assert float(borona["kilos_vendidos"]) == 12
    assert float(borona["ingreso"]) == 216_000
    assert float(borona["costo"]) == 180_000
    assert float(borona["ganancia"]) == 36_000
    assert float(fila_producto(resumen, "merma")["kilos"]) == 0
    assert float(fila_producto(resumen, "pendiente")["kilos"]) == 0
    # Siempre cuatro filas, en orden, y su ganancia suma la del período
    assert [f["producto"] for f in resumen["por_producto"]] == [
        "queso", "borona", "merma", "pendiente",
    ]
    assert sum(float(f["ganancia"]) for f in resumen["por_producto"]) == float(
        resumen["ganancia_estimada"]
    )

    # Ganancia estimada del único productor: 216.000 - 180.000 = 36.000
    assert len(resumen["por_productor"]) == 1
    productor = resumen["por_productor"][0]
    assert productor["productor"] == "Sebastián"
    assert productor["compras"] == 1
    assert float(productor["kilos"]) == 12
    # Es el valor de sus compras, NO lo que se le ha pagado (aún se le debe todo)
    assert float(productor["total_comprado"]) == 180_000
    assert float(productor["precio_promedio"]) == 15_000
    assert float(productor["por_pagar"]) == 180_000
    assert float(productor["margen_por_kilo"]) == 3_000
    assert float(productor["ganancia_estimada"]) == 36_000


def test_por_productor_ordenado_por_ganancia(client, base_datos):
    """Al que se le compró más barato deja más margen y va primero."""
    headers = auth_headers(client, "admin.a")

    for productor, precio in (("Caro", "20000"), ("Barato", "10000")):
        r = client.post(
            "/api/v1/reventa/compras",
            json={"fecha": "2026-07-10", "productor": productor,
                  "kilos_brutos": "10", "precio_kilo": precio},
            headers=headers,
        )
        assert r.status_code == 201, r.text

    # 20 kg vendidos a $18.000 con $500/kg de transporte ($10.000 de gasto)
    r = client.post(
        "/api/v1/reventa/ventas",
        json={"fecha": "2026-07-12", "cliente": "Alba", "kilos": "20",
              "precio_kilo": "18000", "gasto_concepto": "Transporte",
              "gasto_por_kilo": "500", "pagada_de_contado": True},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert float(r.json()["gasto_monto"]) == 10_000

    resumen = client.get(
        "/api/v1/reventa/resumen?desde=2026-07-01&hasta=2026-07-31", headers=headers
    ).json()
    assert float(resumen["total_compras"]) == 300_000
    assert float(resumen["total_gastos"]) == 10_000
    # (360.000 - 10.000) / 20 kg comprados = $17.500 netos por kilo comprado
    assert float(resumen["valor_realizado_kilo"]) == 17_500
    assert float(resumen["ganancia_estimada"]) == 50_000
    # El gasto de venta se carga a la fila del queso, no a la borona
    assert float(fila_producto(resumen, "queso")["gastos"]) == 10_000
    assert float(fila_producto(resumen, "borona")["gastos"]) == 0

    ganancias = [float(f["ganancia_estimada"]) for f in resumen["por_productor"]]
    assert [f["productor"] for f in resumen["por_productor"]] == ["Barato", "Caro"]
    # Barato: 10 kg × 17.500 - 100.000 = 75.000; Caro: 175.000 - 200.000 = -25.000
    assert ganancias == [75_000, -25_000]
    assert ganancias == sorted(ganancias, reverse=True)
    # Reparte exactamente la venta neta del período entre lo comprado, así que la
    # suma de los productores cuadra con la ganancia neta de la tarjeta de arriba
    esperado = float(resumen["kilos_comprados"]) * float(
        resumen["valor_realizado_kilo"]
    ) - float(resumen["total_compras"])
    assert sum(ganancias) == esperado
    assert sum(ganancias) == float(resumen["ganancia_estimada"])


def test_productor_escrito_de_varias_formas_es_uno_solo(client, base_datos):
    """Partir un productor en dos por una mayúscula partiría sus kilos y su
    ranking. Y el saldo de la fila es histórico, para que cuadre con la tarjeta."""
    headers = auth_headers(client, "admin.a")

    for nombre in ("Sebastián Ruiz", "sebastián ruiz", " Sebastián Ruiz "):
        r = client.post(
            "/api/v1/reventa/compras",
            json={"fecha": "2026-07-10", "productor": nombre,
                  "kilos_brutos": "10", "precio_kilo": "15000"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
    # Una compra vieja, fuera del período consultado: no suma kilos pero sí deuda
    client.post(
        "/api/v1/reventa/compras",
        json={"fecha": "2026-06-01", "productor": "SEBASTIÁN RUIZ",
              "kilos_brutos": "10", "precio_kilo": "10000"},
        headers=headers,
    )

    resumen = client.get(
        "/api/v1/reventa/resumen?desde=2026-07-01&hasta=2026-07-31", headers=headers
    ).json()
    assert len(resumen["por_productor"]) == 1
    productor = resumen["por_productor"][0]
    assert productor["compras"] == 3
    assert float(productor["kilos"]) == 30
    assert float(productor["total_comprado"]) == 450_000
    # El saldo por pagar es histórico: incluye la compra de junio (100.000)
    assert float(productor["por_pagar"]) == 550_000
    assert float(productor["por_pagar"]) == float(resumen["por_pagar_productores"])
    # Y el autocompletar tampoco ofrece la misma persona cuatro veces
    sugerencias = client.get("/api/v1/reventa/sugerencias", headers=headers).json()
    assert len(sugerencias["productores"]) == 1


def test_no_anular_compra_ni_venta_con_abonos(client, base_datos):
    """Anular un documento con abonos borraría dinero real del resumen: se bloquea."""
    headers = auth_headers(client, "admin.a")
    compra = client.post(
        "/api/v1/reventa/compras",
        json={"fecha": "2026-07-12", "productor": "Sebastián",
              "kilos_brutos": "100", "precio_kilo": "18000"},
        headers=headers,
    ).json()
    client.post(
        f"/api/v1/reventa/compras/{compra['id']}/abonos",
        json={"fecha": "2026-07-12", "valor": "100000"},
        headers=headers,
    )
    r = client.post(f"/api/v1/reventa/compras/{compra['id']}/anular", headers=headers)
    assert r.status_code == 422
    assert "abono" in r.json()["error"]["detail"].lower()

    venta = client.post(
        "/api/v1/reventa/ventas",
        json={"fecha": "2026-07-13", "cliente": "Alba", "kilos": "40",
              "precio_kilo": "19500", "pagada_de_contado": True},
        headers=headers,
    ).json()
    r = client.post(f"/api/v1/reventa/ventas/{venta['id']}/anular", headers=headers)
    assert r.status_code == 422
    assert "abono" in r.json()["error"]["detail"].lower()


def test_no_vender_mas_queso_del_disponible(client, base_datos):
    headers = auth_headers(client, "admin.a")
    client.post(
        "/api/v1/reventa/compras",
        json={"fecha": "2026-07-12", "productor": "Sebastián",
              "kilos_brutos": "100", "precio_kilo": "18000"},
        headers=headers,
    )
    r = client.post(
        "/api/v1/reventa/ventas",
        json={"fecha": "2026-07-13", "cliente": "Alba", "kilos": "150", "precio_kilo": "19500"},
        headers=headers,
    )
    assert r.status_code == 422
    assert "queso" in r.json()["error"]["detail"].lower()
    # Y NO contamina el libro de la quesera: estado de resultados sin ingresos
    er = client.get(
        "/api/v1/contabilidad/estado-resultados?desde=2026-07-01&hasta=2026-07-31",
        headers=headers,
    ).json()
    assert float(er["ingresos_ventas"]) == 0


# ======================================================= estado de cuenta
# El estado de cuenta es el PDF que la quesera le manda al cliente por WhatsApp
# para mostrarle cómo va su facturación: qué le vendimos, qué pagó y qué debe.


def crear_inventario(client, headers, *, kilos="1000", borona="200", precio="18000"):
    """Compra que surte el inventario para poder registrar ventas."""
    r = client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-01", "productor": "Sebastián Ruiz",
            "kilos_brutos": kilos, "borona_kilos": borona, "precio_kilo": precio,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def crear_venta(client, headers, **campos):
    r = client.post("/api/v1/reventa/ventas", json=campos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def abonar_venta(client, headers, venta_id, **campos):
    r = client.post(
        f"/api/v1/reventa/ventas/{venta_id}/abonos", json=campos, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


def claves_y_valores(dato, claves=None, valores=None):
    """Recorre recursivamente dicts y listas y devuelve (claves, valores str).

    Se usa para auditar la respuesta COMPLETA del estado de cuenta: no basta
    revisar el primer nivel, un dato interno podría venir escondido dentro de
    una venta o de un pago.
    """
    claves = [] if claves is None else claves
    valores = [] if valores is None else valores
    if isinstance(dato, dict):
        for clave, valor in dato.items():
            claves.append(str(clave))
            claves_y_valores(valor, claves, valores)
    elif isinstance(dato, list):
        for elemento in dato:
            claves_y_valores(elemento, claves, valores)
    elif dato is not None:
        valores.append(str(dato))
    return claves, valores


def test_estado_cuenta_cliente(client, base_datos):
    """Tres compras del mismo cliente (una de borona), dos abonos parciales y
    una venta anulada que NO debe aparecer ni sumar."""
    headers = auth_headers(client, "admin.a")
    crear_inventario(client, headers)

    # Se registran en desorden a propósito: el estado de cuenta las ordena
    venta_20 = crear_venta(
        client, headers,
        fecha="2026-07-20", cliente="Carlos Ricaute", kilos="200", precio_kilo="20000",
    )
    venta_05 = crear_venta(
        client, headers,
        fecha="2026-07-05", cliente="carlos ricaute", kilos="100", precio_kilo="19500",
    )
    crear_venta(
        client, headers,
        fecha="2026-07-12", cliente="Carlos Ricaute", tipo="borona",
        kilos="50", precio_kilo="8000",
    )
    # Una venta anulada: no es plata que el cliente deba
    anulada = crear_venta(
        client, headers,
        fecha="2026-07-25", cliente="Carlos Ricaute", kilos="300", precio_kilo="21000",
    )
    r = client.post(f"/api/v1/reventa/ventas/{anulada['id']}/anular", headers=headers)
    assert r.status_code == 200, r.text

    # Los abonos tampoco se registran en orden de fecha
    abonar_venta(client, headers, venta_20["id"], fecha="2026-07-22", valor="1000000")
    abonar_venta(
        client, headers, venta_05["id"], fecha="2026-07-08", valor="500000",
        observaciones="Consignación Bancolombia",
    )

    # Se consulta escrito distinto (minúsculas y un espacio de sobra): es el
    # mismo cliente, su saldo no se puede partir en dos
    r = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "carlos ricaute "},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    cuenta = r.json()

    # La anulada no cuenta: 3 compras, no 4
    assert cuenta["compras"] == 3
    assert len(cuenta["ventas"]) == 3
    assert float(cuenta["total_kilos"]) == 350
    # 1.950.000 + 400.000 + 4.000.000 = 6.350.000 (sin los 6.300.000 anulados)
    assert float(cuenta["total_facturado"]) == 6_350_000
    assert float(cuenta["total_facturado"]) == sum(
        float(v["valor_total"]) for v in cuenta["ventas"]
    )
    assert float(cuenta["total_abonado"]) == 1_500_000
    assert float(cuenta["total_abonado"]) == sum(float(p["valor"]) for p in cuenta["pagos"])
    assert float(cuenta["saldo"]) == float(cuenta["total_facturado"]) - float(
        cuenta["total_abonado"]
    )
    assert float(cuenta["saldo"]) == 4_850_000

    # Ventas ordenadas por fecha ascendente
    fechas = [v["fecha"] for v in cuenta["ventas"]]
    assert fechas == ["2026-07-05", "2026-07-12", "2026-07-20"]
    assert fechas == sorted(fechas)

    # Los pagos son los de TODAS sus ventas, juntos y ordenados por fecha
    assert [p["fecha"] for p in cuenta["pagos"]] == ["2026-07-08", "2026-07-22"]
    assert [float(p["valor"]) for p in cuenta["pagos"]] == [500_000, 1_000_000]
    # Del abono solo salen fecha y valor: sus observaciones son la nota interna de
    # la quesera y ya no viajan en el estado de cuenta (ver el test de más abajo).
    assert "observaciones" not in cuenta["pagos"][0]

    # La borona sale nombrada para mostrar, no con el código interno
    borona = cuenta["ventas"][1]
    assert borona["tipo"] == "borona"
    assert borona["producto"] == "Borona"
    assert float(borona["valor_total"]) == 400_000
    assert borona["estado"] == "pendiente"
    assert cuenta["ventas"][0]["producto"] == "Queso"
    assert cuenta["ventas"][0]["estado"] == "parcial"

    # El nombre que se le muestra es el GUARDADO, bien escrito, no el de la query
    assert cuenta["cliente"] == "Carlos Ricaute"
    # Sin rango: es todo el histórico
    assert cuenta["desde"] is None and cuenta["hasta"] is None
    assert cuenta["emitido"]


def test_estado_cuenta_no_filtra_datos_internos(client, base_datos):
    """LA BARRERA: este documento se le entrega AL CLIENTE. Si se filtrara el
    gasto de transporte, el costo de compra o el productor, el cliente podría
    calcular exactamente cuánto le gana la quesera a cada kilo que le vende.
    Ese margen es información interna y NO puede salir por este endpoint.
    """
    headers = auth_headers(client, "admin.a")
    # Compra a un productor con su precio pagado (dato interno)
    crear_inventario(client, headers, precio="18123")
    # Venta con gasto de flete: 100 kg × $517 = $51.700 de gasto interno
    crear_venta(
        client, headers,
        fecha="2026-07-05", cliente="Alba Ricaute", kilos="100", precio_kilo="19500",
        gasto_concepto="Flete a Villavicencio", gasto_por_kilo="517",
    )

    r = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "Alba Ricaute"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    cuenta = r.json()
    claves, valores = claves_y_valores(cuenta)
    # El recorrido tiene que haber bajado hasta las ventas y los pagos
    assert "precio_kilo" in claves and "producto" in claves

    # Ninguna clave, a ningún nivel, habla de plata interna de la quesera
    prohibidas = ("gasto", "venta_libre", "venta libre", "margen", "ganancia", "costo",
                  "productor", "compra_precio")
    for clave in claves:
        for palabra in prohibidas:
            assert palabra not in clave.lower(), f"clave interna filtrada: {clave}"
    # Ni ningún valor de texto
    for valor in valores:
        for palabra in prohibidas:
            assert palabra not in valor.lower(), f"valor interno filtrado: {valor}"

    # Ni las cifras internas: el concepto y el monto del flete, el precio de
    # compra al productor, su nombre, ni la "venta libre" (1.950.000 - 51.700)
    texto = " | ".join(claves + valores)
    for secreto in ("Flete", "Villavicencio", "517", "51700", "51.700",
                    "18123", "18.123", "Sebastián", "Ruiz", "1898300", "1.898.300"):
        assert secreto not in texto, f"dato interno filtrado: {secreto}"

    # Lo que sí debe estar: lo que el cliente compró y lo que debe
    assert float(cuenta["total_facturado"]) == 1_950_000
    assert float(cuenta["saldo"]) == 1_950_000


def test_estado_cuenta_pdf(client, base_datos):
    """El PDF que se le manda por WhatsApp: descargable y sin datos internos."""
    headers = auth_headers(client, "admin.a")
    crear_inventario(client, headers, precio="18123")
    venta = crear_venta(
        client, headers,
        fecha="2026-07-05", cliente="Alba Ricaute", kilos="100", precio_kilo="19500",
        gasto_concepto="Flete a Villavicencio", gasto_por_kilo="517",
    )
    abonar_venta(client, headers, venta["id"], fecha="2026-07-08", valor="500000")

    r = client.get(
        "/api/v1/reventa/estado-cuenta/pdf",
        params={"cliente": "Alba Ricaute"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert len(r.content) > 3000
    # El nombre del archivo lleva el del cliente, saneado (nada de comillas ni
    # saltos de línea: sería inyección de cabecera HTTP)
    disposition = r.headers["content-disposition"]
    assert 'filename="estado_cuenta_Alba_Ricaute.pdf"' in disposition

    # El contenido del PDF solo se puede leer si pypdf/PyPDF2 está instalado; si
    # no, se omite esa parte (el resto de las comprobaciones ya corrieron).
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - depende del entorno
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return

    texto = "\n".join(pagina.extract_text() or "" for pagina in PdfReader(io.BytesIO(r.content)).pages)
    assert "ESTADO DE CUENTA" in texto
    assert "Alba Ricaute" in texto
    # El saldo, en pesos colombianos: 1.950.000 - 500.000 = 1.450.000
    assert "$1.450.000" in texto
    assert "$1.950.000" in texto and "$500.000" in texto
    # Y NADA interno: ni el gasto del flete ni el costo de compra ni el productor
    for secreto in ("51.700", "51700", "Flete", "Villavicencio", "18.123", "18123",
                    "Sebastián", "$1.898.300"):
        assert secreto not in texto, f"dato interno impreso en el PDF: {secreto}"


def test_estado_cuenta_rango_y_cliente_inexistente(client, base_datos):
    """Con rango se limita al período; sin rango, todo el histórico."""
    headers = auth_headers(client, "admin.a")
    crear_inventario(client, headers)
    # Una venta vieja (junio) y dos de julio
    crear_venta(
        client, headers,
        fecha="2026-06-20", cliente="Yojan Pérez", kilos="100", precio_kilo="19000",
    )
    crear_venta(
        client, headers,
        fecha="2026-07-10", cliente="Yojan Pérez", kilos="200", precio_kilo="19500",
    )
    venta_julio = crear_venta(
        client, headers,
        fecha="2026-07-15", cliente="Yojan Pérez", kilos="100", precio_kilo="20000",
    )
    abonar_venta(client, headers, venta_julio["id"], fecha="2026-07-16", valor="800000")

    # Sin rango: todo el histórico (1.900.000 + 3.900.000 + 2.000.000)
    todo = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "Yojan Pérez"},
        headers=headers,
    ).json()
    assert todo["compras"] == 3
    assert float(todo["total_kilos"]) == 400
    assert float(todo["total_facturado"]) == 7_800_000
    assert float(todo["saldo"]) == 7_000_000
    assert todo["desde"] is None and todo["hasta"] is None

    # Con rango de julio: la venta de junio queda afuera y los totales bajan
    julio = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "Yojan Pérez", "desde": "2026-07-01", "hasta": "2026-07-31"},
        headers=headers,
    ).json()
    assert julio["desde"] == "2026-07-01" and julio["hasta"] == "2026-07-31"
    assert julio["compras"] == 2
    assert float(julio["total_kilos"]) == 300
    assert float(julio["total_facturado"]) == 5_900_000
    assert float(julio["total_abonado"]) == 800_000
    assert float(julio["saldo"]) == 5_100_000
    assert all(v["fecha"] >= "2026-07-01" for v in julio["ventas"])
    # Baja exactamente el valor de la venta de junio
    assert float(todo["total_facturado"]) - float(julio["total_facturado"]) == 1_900_000

    # Un cliente que nunca compró no tiene estado de cuenta
    r = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "Nadie Existe"},
        headers=headers,
    )
    assert r.status_code == 404
    assert "ventas" in r.json()["error"]["detail"].lower()


def test_estado_cuenta_no_cruza_empresas(client, base_datos):
    """Multi-tenant: el cliente de la Quesera A no existe para la Quesera B.
    Un estado de cuenta cruzado le entregaría la cartera de otra quesera."""
    headers_a = auth_headers(client, "admin.a")
    crear_inventario(client, headers_a)
    crear_venta(
        client, headers_a,
        fecha="2026-07-05", cliente="Carlos Ricaute", kilos="100", precio_kilo="19500",
    )
    # La empresa A sí lo ve
    r = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "Carlos Ricaute"},
        headers=headers_a,
    )
    assert r.status_code == 200, r.text
    assert float(r.json()["total_facturado"]) == 1_950_000

    headers_b = auth_headers(client, "admin.b")
    r = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "Carlos Ricaute"},
        headers=headers_b,
    )
    assert r.status_code == 404, r.text
    # Y tampoco por la puerta del PDF
    r = client.get(
        "/api/v1/reventa/estado-cuenta/pdf",
        params={"cliente": "Carlos Ricaute"},
        headers=headers_b,
    )
    assert r.status_code == 404, r.text
    assert not r.content.startswith(b"%PDF")


# ------------------------------------------- lectura del texto impreso en el PDF
def _lector_pdf():
    """PdfReader de pypdf o PyPDF2, o None si ninguno está instalado."""
    try:
        from pypdf import PdfReader

        return PdfReader
    except ImportError:  # pragma: no cover - depende del entorno
        try:
            from PyPDF2 import PdfReader

            return PdfReader
        except ImportError:
            return None


def texto_del_pdf(contenido: bytes) -> str:
    """Todo el texto que lleva impreso el PDF, en una sola línea.

    Con pypdf/PyPDF2 se extrae bien. Sin ninguno de los dos se descomprimen a
    mano los objetos FlateDecode (los streams de reportlab van comprimidos, así
    que buscar en los bytes crudos no encuentra nada): queda un texto más burdo,
    pero alcanza de sobra para comprobar si algo que NO debería estar aparece.
    """
    lector = _lector_pdf()
    if lector is not None:
        crudo = "\n".join(pagina.extract_text() or "" for pagina in lector(io.BytesIO(contenido)).pages)
        return " ".join(crudo.split())

    import zlib  # pragma: no cover - solo sin pypdf instalado

    partes = []
    for bloque in contenido.split(b"stream")[1:]:
        datos = bloque.split(b"endstream")[0].strip(b"\r\n")
        try:
            partes.append(zlib.decompress(datos).decode("latin-1"))
        except zlib.error:
            # Sin comprimir (reportlab no siempre comprime): se lee tal cual
            partes.append(datos.decode("latin-1", "ignore"))
    return " ".join(" ".join(partes).split())


def test_estado_cuenta_no_expone_observaciones_del_abono(client, base_datos):
    """LA BARRERA que impide filtrarle a un cliente el margen de la quesera.

    Las observaciones de un ABONO son la nota interna que la quesera se escribe a
    sí misma: a qué productor le paga, a cuánto el kilo, qué rebajó por el flete.
    Se imprimían literales en la tabla "Pagos recibidos" del PDF que se le
    entrega al cliente, con lo cual el cliente quedaba sabiendo el costo de
    compra y podía calcular la ganancia de cada kilo que le venden. Ese campo NO
    puede salir ni por el JSON ni por el PDF.
    """
    headers = auth_headers(client, "admin.a")
    crear_inventario(client, headers, precio="18000")
    venta = crear_venta(
        client, headers,
        fecha="2026-07-05", cliente="Alba Ricaute", kilos="100", precio_kilo="19500",
    )
    nota_interna = "INTERNO: a Sebastián Ruiz le pagamos 18.000/kg, margen 1.500"
    abonar_venta(
        client, headers, venta["id"], fecha="2026-07-08", valor="500000",
        observaciones=nota_interna,
    )

    # 1) El JSON: ni la clave ni el texto, a ningún nivel del árbol
    r = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "Alba Ricaute"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    cuenta = r.json()
    assert len(cuenta["pagos"]) == 1
    assert "observaciones" not in cuenta["pagos"][0]
    claves, valores = claves_y_valores(cuenta)
    assert "observaciones" not in claves
    texto_json = " | ".join(claves + valores)
    for secreto in (nota_interna, "INTERNO", "margen", "18.000/kg", "1.500", "Sebastián"):
        assert secreto not in texto_json, f"nota interna filtrada en el JSON: {secreto}"

    # 2) El PDF: tampoco en los bytes del documento
    r = client.get(
        "/api/v1/reventa/estado-cuenta/pdf",
        params={"cliente": "Alba Ricaute"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")
    impreso = texto_del_pdf(r.content)
    # El extractor sí está leyendo el documento (si no, el resto no probaría nada)
    assert "Alba Ricaute" in impreso
    assert "Pagos recibidos" in impreso
    for secreto in ("INTERNO", "margen", "18.000/kg", "Sebastián", "Ruiz le pagamos"):
        assert secreto not in impreso, f"nota interna impresa en el PDF: {secreto}"
    # La columna de observaciones ya no existe en la tabla de pagos
    assert "Observaciones" not in impreso
    # Y el abono sí se ve, con su fecha y su valor: lo que el cliente necesita
    assert "$500.000" in impreso


def test_estado_cuenta_pdf_con_nombre_raro(client, base_datos):
    """Un nombre con '<' es texto libre, no marcado.

    ReportLab interpreta mini-XML dentro de un Paragraph: sin escapar, un cliente
    llamado "Ana <onDraw name='x'/> & Cía" dejaba el endpoint del PDF respondiendo
    500 de forma PERMANENTE (Missing onDraw callback attribute) y un
    "Depósito <El Bueno> & Hnos" se imprimía como "Depósito & Hnos".
    """
    headers = auth_headers(client, "admin.a")
    crear_inventario(client, headers)
    crear_venta(
        client, headers,
        fecha="2026-07-05", cliente="Ana <onDraw name='x'/> & Cía",
        kilos="100", precio_kilo="19500",
    )

    r = client.get(
        "/api/v1/reventa/estado-cuenta/pdf",
        params={"cliente": "Ana <onDraw name='x'/> & Cía"},
        headers=headers,
    )
    assert r.status_code == 200, r.text  # antes: 500
    assert r.content.startswith(b"%PDF")

    impreso = texto_del_pdf(r.content)
    # El nombre sale LITERAL: con sus signos y sin perder el "& Cía"
    for pedazo in ("Ana", "onDraw", "&", "Cía"):
        assert pedazo in impreso, f"el nombre del cliente se imprimió mal: falta {pedazo}"
    # Y escapado UNA sola vez: si se escapara dos veces saldría el "&amp;" crudo
    assert "amp;" not in impreso


def test_estado_cuenta_saldo_a_favor(client, base_datos):
    """Si el cliente abonó de más, el documento no puede decirle que debe.

    Pasa de verdad: editar una venta ya pagada está permitido a propósito, así
    que bajarle el precio deja el saldo negativo. El PDF decía Estado "Con saldo"
    y "SALDO PENDIENTE -$550.000", justo lo contrario de la realidad.
    """
    headers = auth_headers(client, "admin.a")
    crear_inventario(client, headers)
    venta = crear_venta(
        client, headers,
        fecha="2026-07-05", cliente="Alba Ricaute", kilos="100", precio_kilo="19500",
        pagada_de_contado=True,
    )
    assert float(venta["saldo"]) == 0

    # Se le rebaja el precio a la venta ya pagada: 100 kg × $14.000 = $1.400.000
    r = client.put(
        f"/api/v1/reventa/ventas/{venta['id']}",
        json={"precio_kilo": "14000"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert float(r.json()["valor_total"]) == 1_400_000

    cuenta = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "Alba Ricaute"},
        headers=headers,
    ).json()
    # Abonó 1.950.000 por una venta de 1.400.000: le quedan 550.000 a favor
    assert float(cuenta["total_abonado"]) == 1_950_000
    assert float(cuenta["saldo"]) == -550_000

    r = client.get(
        "/api/v1/reventa/estado-cuenta/pdf",
        params={"cliente": "Alba Ricaute"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    impreso = texto_del_pdf(r.content)
    # El rótulo cambia y el valor va en POSITIVO
    assert "SALDO A FAVOR" in impreso
    assert "SALDO PENDIENTE" not in impreso
    assert "$550.000" in impreso
    # Y el estado del encabezado tampoco dice que tiene deuda
    assert "Saldo a favor" in impreso
    assert "Con saldo" not in impreso


def test_estado_cuenta_espacios_en_el_nombre(client, base_datos):
    """Consultar con el MISMO texto que se escribió no puede dar 404.

    Al guardar, el nombre se normaliza (los espacios internos se colapsan), pero
    la comparación de la consulta era lower(trim(...)), que solo recorta las
    puntas: registrar "Sebastián  Ruiz" y buscarlo igual devolvía 404.
    """
    headers = auth_headers(client, "admin.a")
    crear_inventario(client, headers)
    venta = crear_venta(
        client, headers,
        fecha="2026-07-05", cliente="Sebastián  Ruiz", kilos="100", precio_kilo="19500",
    )
    # Se guardó con los espacios colapsados
    assert venta["cliente"] == "Sebastián Ruiz"

    r = client.get(
        "/api/v1/reventa/estado-cuenta",
        params={"cliente": "Sebastián  Ruiz"},
        headers=headers,
    )
    assert r.status_code == 200, r.text  # antes: 404
    cuenta = r.json()
    assert cuenta["cliente"] == "Sebastián Ruiz"
    assert cuenta["compras"] == 1
    assert float(cuenta["total_facturado"]) == 1_950_000

    # Y el PDF entra por el mismo camino
    r = client.get(
        "/api/v1/reventa/estado-cuenta/pdf",
        params={"cliente": "Sebastián  Ruiz"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
