"""Reinicio de datos transaccionales de una empresa (solo superadmin).

Verifica que: borra lo transaccional, conserva catálogos, exige confirmación,
solo lo hace el superadmin, y NUNCA toca los datos de otra empresa.
"""
from tests.conftest import auth_headers


def _sembrar(client, headers):
    """Crea catálogo (cliente, producto, empleado) + transacciones (movimiento,
    venta, nómina) para una empresa. Devuelve el producto creado."""
    cli = client.post("/api/v1/clientes", json={"nombre": "Cliente X"}, headers=headers).json()
    prod = client.post(
        "/api/v1/inventario/productos",
        json={"nombre": "Queso X", "categoria": "producto_terminado", "unidad": "kg"},
        headers=headers,
    ).json()
    client.post(
        "/api/v1/inventario/movimientos",
        json={"producto_id": prod["id"], "fecha": "2026-07-01", "tipo": "entrada",
              "cantidad": "50", "costo_unitario": "1000"},
        headers=headers,
    )
    client.post(
        "/api/v1/ventas",
        json={"cliente_id": cli["id"], "fecha": "2026-07-10",
              "detalles": [{"producto_id": prod["id"], "cantidad": "5", "precio_unitario": "10000"}]},
        headers=headers,
    )
    emp = client.post(
        "/api/v1/empleados",
        json={"nombre": "Juan", "apellido": "Perez", "valor_dia": "40000"},
        headers=headers,
    ).json()
    client.post(
        "/api/v1/nomina",
        json={"empleado_id": emp["id"], "fecha": "2026-07-05", "dias_trabajados": "3"},
        headers=headers,
    )
    return prod


def _stock(client, headers, producto_id) -> float:
    return float(
        client.get(f"/api/v1/inventario/productos/{producto_id}/kardex", headers=headers)
        .json()["stock_actual"]
    )


def test_reiniciar_empresa_solo_transacciones(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prod_a = _sembrar(client, ha)
    prod_b = _sembrar(client, hb)

    hs = auth_headers(client, "superadmin")
    empresa_a = str(base_datos["empresa_a"].id)

    # Confirmación equivocada -> rechazada, no borra nada
    assert client.post(
        f"/api/v1/empresas/{empresa_a}/reiniciar", json={"confirmacion": "nombre malo"}, headers=hs
    ).status_code == 422

    # Un usuario NO superadmin no puede reiniciar
    assert client.post(
        f"/api/v1/empresas/{empresa_a}/reiniciar", json={"confirmacion": "Quesera A"}, headers=ha
    ).status_code == 403

    # Superadmin reinicia la empresa A
    ok = client.post(
        f"/api/v1/empresas/{empresa_a}/reiniciar", json={"confirmacion": "Quesera A"}, headers=hs
    )
    assert ok.status_code == 200, ok.text

    # --- Empresa A: transaccional BORRADO ---
    assert client.get("/api/v1/ventas", headers=ha).json()["total"] == 0
    assert client.get("/api/v1/nomina", headers=ha).json()["total"] == 0
    assert _stock(client, ha, prod_a["id"]) == 0  # movimientos borrados

    # --- Empresa A: catálogo CONSERVADO ---
    assert client.get("/api/v1/clientes", headers=ha).json()["total"] >= 1
    assert client.get("/api/v1/inventario/productos", headers=ha).json()["total"] >= 1
    assert client.get("/api/v1/empleados", headers=ha).json()["total"] >= 1

    # --- Empresa B: TODO intacto (no se tocó) ---
    assert client.get("/api/v1/ventas", headers=hb).json()["total"] == 1
    assert client.get("/api/v1/nomina", headers=hb).json()["total"] == 1
    assert _stock(client, hb, prod_b["id"]) == 45  # 50 - 5
