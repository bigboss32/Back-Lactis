"""Filtro de proveedores por ruta (y búsqueda por nombre)."""
from tests.conftest import auth_headers


def test_proveedores_filtra_por_ruta_y_busqueda(client, base_datos):
    headers = auth_headers(client, "admin.a")
    r1 = client.post("/api/v1/rutas", json={"nombre": "R1", "municipio": "M1"}, headers=headers).json()
    r2 = client.post("/api/v1/rutas", json={"nombre": "R2", "municipio": "M2"}, headers=headers).json()
    for nombre, ruta in [("Ana", r1), ("Beto", r2), ("Caro", r1)]:
        client.post(
            "/api/v1/proveedores",
            json={"nombre": nombre, "precio_litro": "1800", "ruta_id": ruta["id"]},
            headers=headers,
        )

    # Solo los de la ruta 1
    res = client.get(
        f"/api/v1/proveedores/filtrar/avanzado?ruta_id={r1['id']}", headers=headers
    ).json()
    assert res["total"] == 2
    assert sorted(p["nombre"] for p in res["items"]) == ["Ana", "Caro"]

    # Ruta 1 + búsqueda "ana"
    res = client.get(
        f"/api/v1/proveedores/filtrar/avanzado?ruta_id={r1['id']}&search=ana", headers=headers
    ).json()
    assert [p["nombre"] for p in res["items"]] == ["Ana"]
