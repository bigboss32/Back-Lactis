"""Verifica que registrar una producción suma el queso al inventario (por kg)."""
import uuid

from sqlalchemy import select

from app.modules.inventario.models import MovimientoInventario, Producto
from tests.conftest import auth_headers


def test_produccion_genera_entrada_inventario(client, base_datos, db_session):
    headers = auth_headers(client, "admin.a")

    r = client.post("/api/v1/tipos-queso", json={"nombre": "Queso Test"}, headers=headers)
    assert r.status_code == 201, r.text
    tipo_id = r.json()["id"]

    r = client.post(
        "/api/v1/produccion",
        json={
            "fecha": "2026-07-15",
            "tipo_queso_id": tipo_id,
            "cantidad": 100,
            "peso_kg": 12,
            "litros_usados": 1000,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text

    tid = uuid.UUID(tipo_id)
    productos = db_session.scalars(
        select(Producto).where(Producto.tipo_queso_id == tid)
    ).all()
    assert len(productos) == 1, "debe crearse un producto terminado ligado al tipo de queso"
    assert productos[0].categoria == "producto_terminado"
    assert productos[0].unidad == "kg"

    movs = db_session.scalars(
        select(MovimientoInventario).where(
            MovimientoInventario.producto_id == productos[0].id
        )
    ).all()
    assert len(movs) == 1, "debe registrarse un movimiento de inventario"
    assert movs[0].tipo == "entrada"
    assert float(movs[0].cantidad) == 12.0


def test_eliminar_produccion_reversa_inventario(client, base_datos, db_session):
    headers = auth_headers(client, "admin.a")
    tipo_id = client.post(
        "/api/v1/tipos-queso", json={"nombre": "Queso Reversa"}, headers=headers
    ).json()["id"]
    prod = client.post(
        "/api/v1/produccion",
        json={"fecha": "2026-07-15", "tipo_queso_id": tipo_id, "peso_kg": 10, "litros_usados": 500},
        headers=headers,
    ).json()

    r = client.delete(f"/api/v1/produccion/{prod['id']}", headers=headers)
    assert r.status_code in (200, 204), r.text

    tid = uuid.UUID(tipo_id)
    producto = db_session.scalars(select(Producto).where(Producto.tipo_queso_id == tid)).first()
    movs = db_session.scalars(
        select(MovimientoInventario).where(MovimientoInventario.producto_id == producto.id)
    ).all()
    tipos = sorted(m.tipo for m in movs)
    assert tipos == ["entrada", "salida"], f"esperaba entrada+salida, obtuvo {tipos}"
