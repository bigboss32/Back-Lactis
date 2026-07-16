"""Nómina de empleados: pago por jornal (valor/día × días trabajados)."""
from tests.conftest import auth_headers


def _crear_empleado(client, headers, valor_dia=None):
    body = {"nombre": "Miguel", "apellido": "Garzon"}
    if valor_dia is not None:
        body["valor_dia"] = valor_dia
    r = client.post("/api/v1/empleados", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_pago_usa_valor_dia_del_empleado(client, base_datos):
    headers = auth_headers(client, "admin.a")
    emp = _crear_empleado(client, headers, valor_dia="50000")
    r = client.post(
        "/api/v1/nomina",
        json={"empleado_id": emp["id"], "fecha": "2026-07-15", "dias_trabajados": "5"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    pago = r.json()
    assert float(pago["valor_dia"]) == 50000
    assert float(pago["total"]) == 250000  # 5 días × 50.000
    assert pago["empleado_nombre"] == "Miguel Garzon"


def test_pago_valor_dia_explicito_y_medio_dia(client, base_datos):
    headers = auth_headers(client, "admin.a")
    emp = _crear_empleado(client, headers)  # sin valor_dia en la ficha
    r = client.post(
        "/api/v1/nomina",
        json={
            "empleado_id": emp["id"], "fecha": "2026-07-15",
            "dias_trabajados": "2.5", "valor_dia": "40000",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert float(r.json()["total"]) == 100000  # 2,5 días × 40.000


def test_pago_sin_valor_dia_falla(client, base_datos):
    headers = auth_headers(client, "admin.a")
    emp = _crear_empleado(client, headers)  # sin valor_dia ni en ficha ni en pago
    r = client.post(
        "/api/v1/nomina",
        json={"empleado_id": emp["id"], "fecha": "2026-07-15", "dias_trabajados": "3"},
        headers=headers,
    )
    assert r.status_code == 422
    assert "valor por d" in r.json()["error"]["detail"].lower()


def test_listar_pagos_por_empleado(client, base_datos):
    headers = auth_headers(client, "admin.a")
    emp = _crear_empleado(client, headers, valor_dia="30000")
    for fecha, dias in [("2026-07-15", "2"), ("2026-07-16", "3")]:
        client.post(
            "/api/v1/nomina",
            json={"empleado_id": emp["id"], "fecha": fecha, "dias_trabajados": dias},
            headers=headers,
        )
    lista = client.get(f"/api/v1/nomina?empleado_id={emp['id']}", headers=headers).json()
    assert lista["total"] == 2
