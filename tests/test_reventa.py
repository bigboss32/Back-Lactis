"""Compra y venta de queso (reventa) con los números reales del cuaderno:
Sebastián entrega 800 kg con 11,2 de merma a $18.000 y recibe abono de $12.100.000.
"""
from tests.conftest import auth_headers


def test_compra_con_merma_y_abono(client, base_datos):
    headers = auth_headers(client, "admin.a")

    r = client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-12",
            "productor": "Sebastián",
            "kilos_brutos": "800",
            "merma_kilos": "11.2",
            "borona_kilos": "56.7",
            "precio_kilo": "18000",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    compra = r.json()
    # 800 - 11.2 = 788.8 kg netos × $18.000 = $14.198.400
    assert float(compra["kilos_netos"]) == 788.8
    assert float(compra["valor_total"]) == 14_198_400
    assert compra["estado"] == "pendiente"

    r = client.post(
        f"/api/v1/reventa/compras/{compra['id']}/abonos",
        json={"fecha": "2026-07-12", "valor": "12100000"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    compra = r.json()
    assert float(compra["abonado"]) == 12_100_000
    assert float(compra["saldo"]) == 2_098_400
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
        json={"fecha": "2026-07-15", "valor": "2098400"},
        headers=headers,
    )
    assert r.json()["estado"] == "pagada"

    # Con abonos ya no se puede editar
    r = client.put(
        f"/api/v1/reventa/compras/{compra['id']}",
        json={"precio_kilo": "17000"},
        headers=headers,
    )
    assert r.status_code == 422


def test_venta_y_resumen(client, base_datos):
    headers = auth_headers(client, "admin.a")

    client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-12", "productor": "Sebastián",
            "kilos_brutos": "800", "merma_kilos": "11.2", "precio_kilo": "18000",
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
    assert float(resumen["kilos_comprados"]) == 788.8
    assert float(resumen["kilos_vendidos"]) == 500
    assert float(resumen["total_ventas"]) == 9_750_000
    # Ganancia: 9.750.000 - 500 kg × $18.000 = $750.000
    assert float(resumen["ganancia_estimada"]) == 750_000
    assert float(resumen["margen_por_kilo"]) == 1_500
    assert float(resumen["kilos_disponibles"]) == 288.8
    assert float(resumen["por_cobrar_clientes"]) == 100 * 19_500


def test_borona_ciclo_completo(client, base_datos):
    headers = auth_headers(client, "admin.a")

    # Compra con 56,7 kg de borona incluida (no se paga, pero entra al inventario)
    client.post(
        "/api/v1/reventa/compras",
        json={
            "fecha": "2026-07-12", "productor": "Sebastián", "kilos_brutos": "800",
            "merma_kilos": "11.2", "borona_kilos": "56.7", "precio_kilo": "18000",
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
    # Queso: 788,8 comprados - 0 vendidos - 20 convertidos = 768,8
    assert float(resumen["kilos_disponibles"]) == 768.8
    # Borona: 56,7 de compras + 20 convertidos - 30 vendidos = 46,7
    assert float(resumen["borona_disponible"]) == 46.7
    assert float(resumen["kilos_borona_vendidos"]) == 30
    assert float(resumen["total_ventas_borona"]) == 240_000
    # Sin ventas de queso, la ganancia del período es lo vendido de borona
    assert float(resumen["ganancia_estimada"]) == 240_000

    # No se puede vender más borona de la disponible (46,7 kg)
    r = client.post(
        "/api/v1/reventa/ventas",
        json={"fecha": "2026-07-17", "cliente": "Otro", "tipo": "borona",
              "kilos": "100", "precio_kilo": "8000"},
        headers=headers,
    )
    assert r.status_code == 422
    assert "borona" in r.json()["error"]["detail"].lower()


def test_no_vender_mas_queso_del_disponible(client, base_datos):
    headers = auth_headers(client, "admin.a")
    client.post(
        "/api/v1/reventa/compras",
        json={"fecha": "2026-07-12", "productor": "Sebastián",
              "kilos_brutos": "100", "merma_kilos": "0", "precio_kilo": "18000"},
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
