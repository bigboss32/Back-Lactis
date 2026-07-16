"""Ventas con descuento de inventario, control de stock y cartera."""
from tests.conftest import auth_headers


def _setup_venta(client, headers):
    producto = client.post(
        "/api/v1/inventario/productos",
        json={"nombre": "Queso Costeño", "categoria": "producto_terminado", "unidad": "kg", "stock_minimo": "10"},
        headers=headers,
    ).json()
    client.post(
        "/api/v1/inventario/movimientos",
        json={
            "producto_id": producto["id"], "fecha": "2026-06-01",
            "tipo": "entrada", "cantidad": "100", "costo_unitario": "12000",
        },
        headers=headers,
    )
    cliente = client.post(
        "/api/v1/clientes", json={"nombre": "Alba"}, headers=headers
    ).json()
    return producto, cliente


def test_venta_descuenta_inventario_y_cartera(client, base_datos):
    headers = auth_headers(client, "admin.a")
    producto, cliente = _setup_venta(client, headers)

    venta = client.post(
        "/api/v1/ventas",
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-10",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "30", "precio_unitario": "17000"}
            ],
        },
        headers=headers,
    )
    assert venta.status_code == 201, venta.text
    data = venta.json()
    assert data["numero"] == 1
    assert float(data["total"]) == 30 * 17000
    assert data["estado"] == "pendiente"

    kardex = client.get(
        f"/api/v1/inventario/productos/{producto['id']}/kardex", headers=headers
    ).json()
    assert float(kardex["stock_actual"]) == 70

    # Pago parcial y luego total
    pago = client.post(
        "/api/v1/pagos",
        json={"venta_id": data["id"], "fecha": "2026-06-11", "valor": "200000", "metodo": "efectivo"},
        headers=headers,
    )
    assert pago.status_code == 201
    venta_actualizada = client.get(f"/api/v1/ventas/{data['id']}", headers=headers).json()
    assert venta_actualizada["estado"] == "parcial"
    assert float(venta_actualizada["saldo"]) == 30 * 17000 - 200000

    cartera = client.get("/api/v1/ventas/cartera", headers=headers).json()
    assert len(cartera) == 1
    assert float(cartera[0]["saldo"]) == 30 * 17000 - 200000


def test_venta_sin_stock_suficiente(client, base_datos):
    headers = auth_headers(client, "admin.a")
    producto, cliente = _setup_venta(client, headers)
    response = client.post(
        "/api/v1/ventas",
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-10",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "500", "precio_unitario": "17000"}
            ],
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "insuficiente" in response.json()["error"]["detail"].lower()


def test_alerta_stock_bajo(client, base_datos):
    headers = auth_headers(client, "admin.a")
    producto, cliente = _setup_venta(client, headers)
    # Vender 95 deja el stock (5) por debajo del mínimo (10)
    client.post(
        "/api/v1/ventas",
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-10",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "95", "precio_unitario": "17000"}
            ],
        },
        headers=headers,
    )
    alertas = client.post("/api/v1/notificaciones/generar-alertas", headers=headers).json()
    assert alertas["detalle"]["stock_bajo"] == 1

    notificaciones = client.get("/api/v1/notificaciones", headers=headers).json()
    assert any(n["tipo"] == "stock_bajo" for n in notificaciones["items"])


def test_venta_con_descuento_total_nace_pagada(client, base_datos):
    """Descuento igual al subtotal -> total 0 -> estado pagada, fuera de cartera."""
    headers = auth_headers(client, "admin.a")
    producto, cliente = _setup_venta(client, headers)
    venta = client.post(
        "/api/v1/ventas",
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-10",
            "descuento": "51000",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "3", "precio_unitario": "17000"}
            ],
        },
        headers=headers,
    ).json()
    assert float(venta["total"]) == 0
    assert venta["estado"] == "pagada"
    cartera = client.get("/api/v1/ventas/cartera", headers=headers).json()
    assert all(c["id"] != venta["id"] for c in cartera)


def test_venta_subtotal_cuadra_con_detalles(client, base_datos):
    """El subtotal es la suma exacta de los totales de línea (redondeados a 2 dec)."""
    headers = auth_headers(client, "admin.a")
    producto, cliente = _setup_venta(client, headers)
    venta = client.post(
        "/api/v1/ventas",
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-10",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "1.5", "precio_unitario": "0.15"},
                {"producto_id": producto["id"], "cantidad": "1.5", "precio_unitario": "0.15"},
            ],
        },
        headers=headers,
    ).json()
    suma_detalles = sum(float(d["total"]) for d in venta["detalles"])
    assert abs(float(venta["subtotal"]) - suma_detalles) < 0.001
