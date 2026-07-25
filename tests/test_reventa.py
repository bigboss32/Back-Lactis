"""Compra y venta de queso (reventa) con los números reales del cuaderno.

Ojo con el negocio de HOY: al comprar se paga por TODOS los kilos recibidos
(ya no se descuenta merma en la compra), así que 800 kg brutos son 800 kg
netos. La merma real son solo los ajustes con destino 'merma'; el queso que se
pasa a borona NO es merma, es un subproducto que se vende más barato.
"""
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
