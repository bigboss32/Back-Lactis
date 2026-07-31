"""Usuarios multi-empresa: membresías con roles POR empresa (asignadas solo por
el superadmin), header X-Empresa-Id para elegir la empresa activa y scoping de
usuarios por membresía. La retrocompatibilidad mono-empresa y del superadmin
también se verifica aquí."""
import uuid

import pytest

from tests.conftest import PASSWORD, auth_headers, login


def _con_empresa(headers: dict, empresa_id) -> dict:
    return {**headers, "X-Empresa-Id": str(empresa_id)}


def _roles_por_nombre(client, headers_super) -> dict[str, str]:
    respuesta = client.get("/api/v1/roles?page_size=100", headers=headers_super)
    assert respuesta.status_code == 200, respuesta.text
    return {rol["nombre"]: rol["id"] for rol in respuesta.json()["items"]}


def _crear_proveedor(client, headers, nombre="Proveedor X", esperado=201):
    response = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "Porvenir", "precio_litro": "1500"},
        headers=headers,
    )
    assert response.status_code == esperado, response.text
    return response.json()


@pytest.fixture()
def entorno(client, base_datos):
    """El superadmin crea al usuario 'multi' y le asigna membresías con roles
    distintos por empresa: Administrador Empresa en A y Consulta en B
    (empresa principal: A)."""
    headers_super = auth_headers(client, "superadmin")
    roles = _roles_por_nombre(client, headers_super)
    empresa_a = str(base_datos["empresa_a"].id)
    empresa_b = str(base_datos["empresa_b"].id)

    creado = client.post(
        "/api/v1/usuarios",
        json={
            "nombre": "Multi",
            "apellido": "Empresa",
            "correo": "multi@pruebas.com",
            "username": "multi",
            "password": PASSWORD,
            "rol_ids": [roles["Administrador Empresa"]],
        },
        headers=_con_empresa(headers_super, empresa_a),
    )
    assert creado.status_code == 201, creado.text
    multi_id = creado.json()["id"]

    asignacion = client.put(
        f"/api/v1/usuarios/{multi_id}/empresas",
        json={
            "membresias": [
                {"empresa_id": empresa_a, "rol_ids": [roles["Administrador Empresa"]]},
                {"empresa_id": empresa_b, "rol_ids": [roles["Consulta"]]},
            ]
        },
        headers=headers_super,
    )
    assert asignacion.status_code == 200, asignacion.text
    return {
        "headers_super": headers_super,
        "roles": roles,
        "empresa_a": empresa_a,
        "empresa_b": empresa_b,
        "multi_id": multi_id,
        "membresias": asignacion.json(),
    }


# ------------------------------------------------------------ PUT/GET membresías
def test_superadmin_asigna_y_consulta_membresias(client, base_datos, entorno):
    # El PUT de la fixture devuelve el estado resultante, igual que el GET
    respuesta = client.get(
        f"/api/v1/usuarios/{entorno['multi_id']}/empresas", headers=entorno["headers_super"]
    )
    assert respuesta.status_code == 200, respuesta.text
    membresias = respuesta.json()
    assert membresias == entorno["membresias"]
    assert [m["empresa_nombre"] for m in membresias] == ["Quesera A", "Quesera B"]
    por_empresa = {m["empresa_nombre"]: [r["nombre"] for r in m["roles"]] for m in membresias}
    assert por_empresa == {
        "Quesera A": ["Administrador Empresa"],
        "Quesera B": ["Consulta"],
    }


def test_admin_de_empresa_no_gestiona_membresias(client, base_datos, entorno):
    """Las membresías son exclusivas del superadmin, aunque el admin de empresa
    tenga el permiso usuarios:administrar."""
    headers_a = auth_headers(client, "admin.a")
    multi_id = entorno["multi_id"]

    consulta = client.get(f"/api/v1/usuarios/{multi_id}/empresas", headers=headers_a)
    assert consulta.status_code == 403
    assert "Administrador General" in consulta.json()["error"]["detail"]

    cambio = client.put(
        f"/api/v1/usuarios/{multi_id}/empresas",
        json={
            "membresias": [
                {"empresa_id": entorno["empresa_a"], "rol_ids": [entorno["roles"]["Consulta"]]}
            ]
        },
        headers=headers_a,
    )
    assert cambio.status_code == 403
    assert "Administrador General" in cambio.json()["error"]["detail"]


# --------------------------------------------------------------------- /auth/me
def test_perfil_sin_header_usa_la_empresa_principal(client, base_datos, entorno):
    headers_multi = auth_headers(client, "multi")
    perfil = client.get("/api/v1/auth/me", headers=headers_multi).json()
    assert perfil["empresa_id"] == entorno["empresa_a"]
    assert perfil["roles"] == ["Administrador Empresa"]
    assert perfil["es_superadmin"] is False
    assert [e["nombre"] for e in perfil["empresas"]] == ["Quesera A", "Quesera B"]


def test_perfil_con_header_cambia_empresa_roles_y_permisos(client, base_datos, entorno):
    headers_multi = auth_headers(client, "multi")
    perfil = client.get(
        "/api/v1/auth/me", headers=_con_empresa(headers_multi, entorno["empresa_b"])
    ).json()
    assert perfil["empresa_id"] == entorno["empresa_b"]
    assert perfil["roles"] == ["Consulta"]
    assert "proveedores:consultar" in perfil["permisos"]
    assert "proveedores:crear" not in perfil["permisos"]
    # La lista de empresas no depende de la empresa activa
    assert [e["nombre"] for e in perfil["empresas"]] == ["Quesera A", "Quesera B"]


def test_header_de_empresa_donde_no_es_miembro(client, base_datos, entorno):
    headers_a = auth_headers(client, "admin.a")
    respuesta = client.get(
        "/api/v1/auth/me", headers=_con_empresa(headers_a, entorno["empresa_b"])
    )
    assert respuesta.status_code == 403
    assert "No pertenece a la empresa indicada" in respuesta.json()["error"]["detail"]


def test_header_con_uuid_malformado(client, base_datos, entorno):
    # Aplica a cualquier usuario, superadmin incluido
    for username in ("admin.a", "superadmin"):
        headers = auth_headers(client, username)
        respuesta = client.get(
            "/api/v1/auth/me", headers={**headers, "X-Empresa-Id": "no-es-un-uuid"}
        )
        assert respuesta.status_code == 403
        assert "X-Empresa-Id inválido" in respuesta.json()["error"]["detail"]


# ------------------------------------------------- permisos y datos por empresa
def test_permisos_distintos_por_empresa(client, base_datos, entorno):
    """multi es Administrador Empresa en A (puede crear) y Consulta en B (no)."""
    headers_multi = auth_headers(client, "multi")
    _crear_proveedor(client, _con_empresa(headers_multi, entorno["empresa_a"]))
    respuesta = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Prohibido", "vereda": "Porvenir", "precio_litro": "1500"},
        headers=_con_empresa(headers_multi, entorno["empresa_b"]),
    )
    assert respuesta.status_code == 403
    assert "crear" in respuesta.json()["error"]["detail"]


def test_multi_solo_ve_datos_de_la_empresa_activa(client, base_datos, entorno):
    _crear_proveedor(client, auth_headers(client, "admin.a"), nombre="Solo A")
    _crear_proveedor(client, auth_headers(client, "admin.b"), nombre="Solo B")

    headers_multi = auth_headers(client, "multi")
    lista_a = client.get(
        "/api/v1/proveedores", headers=_con_empresa(headers_multi, entorno["empresa_a"])
    ).json()
    assert lista_a["total"] == 1
    assert lista_a["items"][0]["nombre"] == "Solo A"

    lista_b = client.get(
        "/api/v1/proveedores", headers=_con_empresa(headers_multi, entorno["empresa_b"])
    ).json()
    assert lista_b["total"] == 1
    assert lista_b["items"][0]["nombre"] == "Solo B"


# ------------------------------------------------------------ asignar_roles scoped
def test_asignar_roles_solo_toca_la_empresa_activa(client, base_datos, entorno):
    headers_super = entorno["headers_super"]
    multi_id = entorno["multi_id"]

    respuesta = client.post(
        f"/api/v1/usuarios/{multi_id}/roles",
        json={"rol_ids": [entorno["roles"]["Supervisor"]]},
        headers=_con_empresa(headers_super, entorno["empresa_a"]),
    )
    assert respuesta.status_code == 200, respuesta.text
    assert [r["nombre"] for r in respuesta.json()["roles"]] == ["Supervisor"]

    # La membresía en B queda intacta
    membresias = client.get(
        f"/api/v1/usuarios/{multi_id}/empresas", headers=headers_super
    ).json()
    por_empresa = {m["empresa_nombre"]: [r["nombre"] for r in m["roles"]] for m in membresias}
    assert por_empresa == {"Quesera A": ["Supervisor"], "Quesera B": ["Consulta"]}


def test_asignar_roles_requiere_contexto_de_empresa(client, base_datos, entorno):
    respuesta = client.post(
        f"/api/v1/usuarios/{entorno['multi_id']}/roles",
        json={"rol_ids": [entorno["roles"]["Consulta"]]},
        headers=entorno["headers_super"],
    )
    assert respuesta.status_code == 422
    assert "X-Empresa-Id" in respuesta.json()["error"]["detail"]


# -------------------------------------------------- scoping de usuarios por membresía
def test_listado_y_edicion_de_usuarios_por_membresia(client, base_datos, entorno):
    """El admin de A ve/edita a multi aunque su empresa PRINCIPAL sea B: lo que
    manda es la membresía. El admin de B no ve a los usuarios de A."""
    multi_id = entorno["multi_id"]
    cambio = client.put(
        f"/api/v1/usuarios/{multi_id}/empresas",
        json={
            "membresias": [
                {"empresa_id": entorno["empresa_a"], "rol_ids": [entorno["roles"]["Administrador Empresa"]]},
                {"empresa_id": entorno["empresa_b"], "rol_ids": [entorno["roles"]["Consulta"]]},
            ],
            "empresa_principal_id": entorno["empresa_b"],
        },
        headers=entorno["headers_super"],
    )
    assert cambio.status_code == 200, cambio.text

    headers_a = auth_headers(client, "admin.a")
    usernames_a = {u["username"] for u in client.get("/api/v1/usuarios", headers=headers_a).json()["items"]}
    assert usernames_a == {"admin.a", "multi"}

    headers_b = auth_headers(client, "admin.b")
    usernames_b = {u["username"] for u in client.get("/api/v1/usuarios", headers=headers_b).json()["items"]}
    assert usernames_b == {"admin.b", "multi"}
    assert "admin.a" not in usernames_b

    # Detalle y edición desde A, aunque la principal de multi sea B
    detalle = client.get(f"/api/v1/usuarios/{multi_id}", headers=headers_a)
    assert detalle.status_code == 200, detalle.text
    editado = client.put(
        f"/api/v1/usuarios/{multi_id}", json={"telefono": "3001234567"}, headers=headers_a
    )
    assert editado.status_code == 200, editado.text
    assert editado.json()["telefono"] == "3001234567"


def test_usuario_read_muestra_roles_del_contexto_y_empresas(client, base_datos, entorno):
    multi_id = entorno["multi_id"]

    # Para el admin de A (contexto A): solo los roles de A + columna de empresas
    headers_a = auth_headers(client, "admin.a")
    lista = client.get("/api/v1/usuarios", headers=headers_a).json()
    multi = next(u for u in lista["items"] if u["username"] == "multi")
    assert [r["nombre"] for r in multi["roles"]] == ["Administrador Empresa"]
    assert multi["empresas"] == ["Quesera A", "Quesera B"]

    # Superadmin con header B: los roles de B
    con_header_b = client.get(
        f"/api/v1/usuarios/{multi_id}",
        headers=_con_empresa(entorno["headers_super"], entorno["empresa_b"]),
    ).json()
    assert [r["nombre"] for r in con_header_b["roles"]] == ["Consulta"]

    # Superadmin sin header: todos los roles (fallback a la property)
    sin_header = client.get(
        f"/api/v1/usuarios/{multi_id}", headers=entorno["headers_super"]
    ).json()
    assert {r["nombre"] for r in sin_header["roles"]} == {"Administrador Empresa", "Consulta"}


# ------------------------------------------------------------- retrocompatibilidad
def test_retrocompatibilidad_usuario_mono_empresa(client, base_datos):
    """Sin membresías nuevas todo sigue como antes: el admin de A opera sin
    header sobre su única empresa."""
    headers_a = auth_headers(client, "admin.a")
    perfil = client.get("/api/v1/auth/me", headers=headers_a).json()
    assert perfil["empresa_id"] == str(base_datos["empresa_a"].id)
    assert perfil["roles"] == ["Administrador Empresa"]
    assert [e["nombre"] for e in perfil["empresas"]] == ["Quesera A"]

    roles = _roles_por_nombre(client, headers_a)
    creado = client.post(
        "/api/v1/usuarios",
        json={
            "nombre": "Operario",
            "apellido": "Planta",
            "correo": "operario.a@pruebas.com",
            "username": "operario.a",
            "password": PASSWORD,
            "rol_ids": [roles["Auxiliar"]],
        },
        headers=headers_a,
    )
    assert creado.status_code == 201, creado.text
    assert creado.json()["empresa_id"] == str(base_datos["empresa_a"].id)
    assert creado.json()["empresas"] == ["Quesera A"]

    perfil_operario = client.get(
        "/api/v1/auth/me", headers=auth_headers(client, "operario.a")
    ).json()
    assert perfil_operario["empresa_id"] == str(base_datos["empresa_a"].id)
    assert perfil_operario["roles"] == ["Auxiliar"]


def test_superadmin_opera_igual_que_antes(client, base_datos):
    headers_super = auth_headers(client, "superadmin")

    perfil = client.get("/api/v1/auth/me", headers=headers_super).json()
    assert perfil["es_superadmin"] is True
    assert perfil["empresa_id"] is None
    assert perfil["roles"] == ["Administrador General"]
    # Su selector se alimenta de /auth/me: recibe TODAS las empresas activas
    assert [e["nombre"] for e in perfil["empresas"]] == ["Quesera A", "Quesera B"]

    # Usuarios sin header: los ve todos (el repo no exige tenant)
    usernames = {u["username"] for u in client.get("/api/v1/usuarios", headers=headers_super).json()["items"]}
    assert usernames == {"superadmin", "admin.a", "admin.b"}

    # Datos multi-tenant sin header: sigue exigiendo el header, como siempre
    respuesta = client.get("/api/v1/proveedores", headers=headers_super)
    assert respuesta.status_code == 422
    assert "X-Empresa-Id" in respuesta.json()["error"]["detail"]


# --------------------------------------------------------------- empresa principal
def test_principal_se_reasigna_al_quitar_esa_membresia(client, base_datos, entorno):
    """Si la empresa principal deja de ser membresía, pasa a la primera de la
    lista sin dejar al usuario colgado de una empresa ajena."""
    multi_id = entorno["multi_id"]
    respuesta = client.put(
        f"/api/v1/usuarios/{multi_id}/empresas",
        json={
            "membresias": [
                {"empresa_id": entorno["empresa_b"], "rol_ids": [entorno["roles"]["Consulta"]]}
            ]
        },
        headers=entorno["headers_super"],
    )
    assert respuesta.status_code == 200, respuesta.text
    assert [m["empresa_nombre"] for m in respuesta.json()] == ["Quesera B"]

    headers_multi = auth_headers(client, "multi")
    perfil = client.get("/api/v1/auth/me", headers=headers_multi).json()
    assert perfil["empresa_id"] == entorno["empresa_b"]
    assert perfil["roles"] == ["Consulta"]

    # Ya no puede pararse en A con el header
    rechazo = client.get(
        "/api/v1/auth/me", headers=_con_empresa(headers_multi, entorno["empresa_a"])
    )
    assert rechazo.status_code == 403
    assert "No pertenece a la empresa indicada" in rechazo.json()["error"]["detail"]


def test_empresa_principal_explicita_en_el_put(client, base_datos, entorno):
    multi_id = entorno["multi_id"]
    respuesta = client.put(
        f"/api/v1/usuarios/{multi_id}/empresas",
        json={
            "membresias": [
                {"empresa_id": entorno["empresa_a"], "rol_ids": [entorno["roles"]["Administrador Empresa"]]},
                {"empresa_id": entorno["empresa_b"], "rol_ids": [entorno["roles"]["Consulta"]]},
            ],
            "empresa_principal_id": entorno["empresa_b"],
        },
        headers=entorno["headers_super"],
    )
    assert respuesta.status_code == 200, respuesta.text

    perfil = client.get("/api/v1/auth/me", headers=auth_headers(client, "multi")).json()
    assert perfil["empresa_id"] == entorno["empresa_b"]
    assert perfil["roles"] == ["Consulta"]


# ----------------------------------------------------------- validaciones del PUT
def test_validaciones_del_put_de_membresias(client, base_datos, entorno):
    headers_super = entorno["headers_super"]
    multi_id = entorno["multi_id"]
    empresa_a = entorno["empresa_a"]
    rol_consulta = entorno["roles"]["Consulta"]

    def put(usuario_id, body):
        return client.put(f"/api/v1/usuarios/{usuario_id}/empresas", json=body, headers=headers_super)

    # Usuario inexistente
    respuesta = put(uuid.uuid4(), {"membresias": [{"empresa_id": empresa_a, "rol_ids": [rol_consulta]}]})
    assert respuesta.status_code == 404
    assert "Usuario no encontrado" in respuesta.json()["error"]["detail"]

    # Lista vacía
    respuesta = put(multi_id, {"membresias": []})
    assert respuesta.status_code == 422
    assert "al menos una empresa" in respuesta.json()["error"]["detail"]

    # Empresas repetidas
    respuesta = put(
        multi_id,
        {"membresias": [
            {"empresa_id": empresa_a, "rol_ids": [rol_consulta]},
            {"empresa_id": empresa_a, "rol_ids": [rol_consulta]},
        ]},
    )
    assert respuesta.status_code == 422
    assert "repetidas" in respuesta.json()["error"]["detail"]

    # Empresa sin roles
    respuesta = put(multi_id, {"membresias": [{"empresa_id": empresa_a, "rol_ids": []}]})
    assert respuesta.status_code == 422
    assert "al menos un rol" in respuesta.json()["error"]["detail"]

    # Empresa inexistente
    respuesta = put(
        multi_id, {"membresias": [{"empresa_id": str(uuid.uuid4()), "rol_ids": [rol_consulta]}]}
    )
    assert respuesta.status_code == 404
    assert "empresas no existen" in respuesta.json()["error"]["detail"]

    # Rol inexistente
    respuesta = put(
        multi_id, {"membresias": [{"empresa_id": empresa_a, "rol_ids": [str(uuid.uuid4())]}]}
    )
    assert respuesta.status_code == 404
    assert "roles no existen" in respuesta.json()["error"]["detail"]

    # El rol de superadmin es global: no se asigna por empresa
    respuesta = put(
        multi_id,
        {"membresias": [{"empresa_id": empresa_a, "rol_ids": [entorno["roles"]["Administrador General"]]}]},
    )
    assert respuesta.status_code == 422
    assert "global" in respuesta.json()["error"]["detail"]

    # La principal debe estar entre las asignadas
    respuesta = put(
        multi_id,
        {
            "membresias": [{"empresa_id": empresa_a, "rol_ids": [rol_consulta]}],
            "empresa_principal_id": entorno["empresa_b"],
        },
    )
    assert respuesta.status_code == 422
    assert "empresa principal" in respuesta.json()["error"]["detail"]

    # El superadmin no lleva membresías por empresa
    respuesta = put(
        str(base_datos["superadmin"].id),
        {"membresias": [{"empresa_id": empresa_a, "rol_ids": [rol_consulta]}]},
    )
    assert respuesta.status_code == 422
    assert "no lleva membresías" in respuesta.json()["error"]["detail"]


# --------------------------------------------------- promoción/democión superadmin
def test_promocion_y_democion_de_superadmin(client, base_datos, entorno):
    headers_super = entorno["headers_super"]
    multi_id = entorno["multi_id"]
    rol_general = entorno["roles"]["Administrador General"]
    con_a = _con_empresa(headers_super, entorno["empresa_a"])

    # Un admin de empresa NO puede asignar el rol de superadmin
    intruso = client.post(
        f"/api/v1/usuarios/{multi_id}/roles",
        json={"rol_ids": [rol_general]},
        headers=auth_headers(client, "admin.a"),
    )
    assert intruso.status_code == 403

    # Promoción: el superadmin materializa la fila global
    promocion = client.post(
        f"/api/v1/usuarios/{multi_id}/roles", json={"rol_ids": [rol_general]}, headers=con_a
    )
    assert promocion.status_code == 200, promocion.text
    perfil = client.get("/api/v1/auth/me", headers=auth_headers(client, "multi")).json()
    assert perfil["es_superadmin"] is True
    assert perfil["roles"] == ["Administrador General"]

    # Guard: no puede quitarse a sí mismo el rol global
    autodemocion = client.post(
        f"/api/v1/usuarios/{base_datos['superadmin'].id}/roles",
        json={"rol_ids": [entorno["roles"]["Administrador Empresa"]]},
        headers=con_a,
    )
    assert autodemocion.status_code == 422
    assert "sí mismo" in autodemocion.json()["error"]["detail"]

    # Democión de multi: quita la fila global y deja roles normales en A;
    # la membresía en B nunca se tocó
    democion = client.post(
        f"/api/v1/usuarios/{multi_id}/roles",
        json={"rol_ids": [entorno["roles"]["Administrador Empresa"]]},
        headers=con_a,
    )
    assert democion.status_code == 200, democion.text
    perfil = client.get("/api/v1/auth/me", headers=auth_headers(client, "multi")).json()
    assert perfil["es_superadmin"] is False
    assert perfil["roles"] == ["Administrador Empresa"]
    membresias = client.get(f"/api/v1/usuarios/{multi_id}/empresas", headers=headers_super).json()
    por_empresa = {m["empresa_nombre"]: [r["nombre"] for r in m["roles"]] for m in membresias}
    assert por_empresa == {"Quesera A": ["Administrador Empresa"], "Quesera B": ["Consulta"]}


def test_usuario_sin_empresa_no_puede_operar(client, base_datos):
    """Cierre del hueco: un usuario normal sin empresa resuelta no ve NADA
    (antes habría listado usuarios de todas las empresas)."""
    headers_super = auth_headers(client, "superadmin")
    creado = client.post(
        "/api/v1/usuarios",
        json={
            "nombre": "Huerfano",
            "apellido": "Sin Empresa",
            "correo": "huerfano@pruebas.com",
            "username": "huerfano",
            "password": PASSWORD,
        },
        headers=headers_super,
    )
    assert creado.status_code == 201, creado.text
    assert creado.json()["empresa_id"] is None

    headers_huerfano = auth_headers(client, "huerfano")
    respuesta = client.get("/api/v1/auth/me", headers=headers_huerfano)
    assert respuesta.status_code == 403
    assert "no tiene una empresa asignada" in respuesta.json()["error"]["detail"]
