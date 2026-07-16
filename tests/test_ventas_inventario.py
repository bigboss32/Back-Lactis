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


def test_libro_diario_solo_cuenta_caja_y_bancos(client, base_datos):
    """El libro diario NO debe contar los pagos de venta (solo caja/bancos): así
    se evita el doble conteo del mismo dinero (pago + ingreso de caja)."""
    headers = auth_headers(client, "admin.a")
    producto, cliente = _setup_venta(client, headers)
    venta = client.post(
        "/api/v1/ventas",
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-10",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "10", "precio_unitario": "17000"}
            ],
        },
        headers=headers,
    ).json()
    # Pago de la venta (antes se contaba en el libro diario -> doble conteo)
    client.post(
        "/api/v1/pagos",
        json={"venta_id": venta["id"], "fecha": "2026-06-11", "valor": "170000", "metodo": "efectivo"},
        headers=headers,
    )
    # Ingreso de caja por ese mismo cobro (lo que sí debe contar el libro diario)
    caja = client.post(
        "/api/v1/caja/abrir", json={"fecha": "2026-06-11", "saldo_inicial": "0"}, headers=headers
    ).json()
    client.post(
        "/api/v1/caja/movimientos",
        json={"caja_id": caja["id"], "tipo": "ingreso", "concepto": "Cobro venta", "valor": "170000"},
        headers=headers,
    )

    libro = client.get(
        "/api/v1/contabilidad/libro-diario",
        params={"desde": "2026-06-01", "hasta": "2026-06-30"},
        headers=headers,
    ).json()
    origenes = {a["origen"] for a in libro["asientos"]}
    assert "pago" not in origenes
    assert origenes <= {"caja", "banco"}
    # Solo el ingreso de caja (170.000), no el pago -> sin doble conteo
    assert float(libro["total_ingresos"]) == 170000


def test_precio_con_mas_de_dos_decimales_se_rechaza(client, base_datos):
    """El precio_unitario no admite más de 2 decimales (evita descuadres)."""
    headers = auth_headers(client, "admin.a")
    producto, cliente = _setup_venta(client, headers)
    r = client.post(
        "/api/v1/ventas",
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-10",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "1", "precio_unitario": "3.333"}
            ],
        },
        headers=headers,
    )
    assert r.status_code == 422


def test_balance_incluye_efectivo_de_caja_cerrada(client, base_datos):
    """El disponible en caja incluye el efectivo de cajas ya cerradas (arqueo)."""
    headers = auth_headers(client, "admin.a")
    caja = client.post(
        "/api/v1/caja/abrir", json={"fecha": "2026-06-01", "saldo_inicial": "0"}, headers=headers
    ).json()
    client.post(
        "/api/v1/caja/movimientos",
        json={"caja_id": caja["id"], "tipo": "ingreso", "concepto": "Cobro", "valor": "800000"},
        headers=headers,
    )
    cerrada = client.post(
        f"/api/v1/caja/{caja['id']}/cerrar",
        json={"efectivo_contado": "800000"},
        headers=headers,
    )
    assert cerrada.status_code == 200, cerrada.text
    balance = client.get("/api/v1/contabilidad/balance", headers=headers).json()
    assert float(balance["saldo_cajas"]) == 800000
