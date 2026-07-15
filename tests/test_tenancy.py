"""El aislamiento multi-tenant es la garantía central del sistema:
ningún usuario puede ver ni tocar datos de otra empresa."""
from tests.conftest import auth_headers


def _crear_proveedor(client, headers, nombre="Proveedor X"):
    response = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "Porvenir", "precio_litro": "1500"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_empresa_no_ve_datos_de_otra(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")

    proveedor = _crear_proveedor(client, headers_a)

    lista_b = client.get("/api/v1/proveedores", headers=headers_b).json()
    assert lista_b["total"] == 0

    detalle_b = client.get(f"/api/v1/proveedores/{proveedor['id']}", headers=headers_b)
    assert detalle_b.status_code == 404

    lista_a = client.get("/api/v1/proveedores", headers=headers_a).json()
    assert lista_a["total"] == 1


def test_empresa_no_puede_editar_datos_ajenos(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")
    proveedor = _crear_proveedor(client, headers_a)

    response = client.put(
        f"/api/v1/proveedores/{proveedor['id']}",
        json={"nombre": "Hackeado"},
        headers=headers_b,
    )
    assert response.status_code == 404


def test_superadmin_opera_por_empresa_con_header(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    _crear_proveedor(client, headers_a)

    headers_super = auth_headers(client, "superadmin")
    empresa_a_id = str(base_datos["empresa_a"].id)

    con_header = client.get(
        "/api/v1/proveedores", headers={**headers_super, "X-Empresa-Id": empresa_a_id}
    ).json()
    assert con_header["total"] == 1

    empresa_b_id = str(base_datos["empresa_b"].id)
    otra = client.get(
        "/api/v1/proveedores", headers={**headers_super, "X-Empresa-Id": empresa_b_id}
    ).json()
    assert otra["total"] == 0


def test_superadmin_sin_header_no_mezcla_empresas(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    _crear_proveedor(client, headers_a)

    headers_super = auth_headers(client, "superadmin")
    response = client.get("/api/v1/proveedores", headers=headers_super)
    assert response.status_code == 422
    assert "X-Empresa-Id" in response.json()["error"]["detail"]


def test_admin_empresa_solo_ve_su_empresa(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    respuesta = client.get("/api/v1/empresas", headers=headers_a).json()
    assert respuesta["total"] == 1
    assert respuesta["items"][0]["nombre"] == "Quesera A"
