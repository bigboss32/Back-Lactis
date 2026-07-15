from tests.conftest import PASSWORD, auth_headers, login


def test_login_ok(client, base_datos):
    tokens = login(client, "admin.a")
    assert tokens["access_token"]
    assert tokens["refresh_token"]


def test_login_credenciales_invalidas(client, base_datos):
    response = client.post(
        "/api/v1/auth/login", data={"username": "admin.a", "password": "incorrecta1"}
    )
    assert response.status_code == 401


def test_bloqueo_tras_intentos_fallidos(client, base_datos):
    for _ in range(5):
        client.post("/api/v1/auth/login", data={"username": "admin.b", "password": "malaClave1"})
    # Con la contraseña correcta ya debe estar bloqueado
    response = client.post(
        "/api/v1/auth/login", data={"username": "admin.b", "password": PASSWORD}
    )
    assert response.status_code == 403
    assert "bloqueado" in response.json()["error"]["detail"].lower()


def test_refresh_rotativo(client, base_datos):
    tokens = login(client, "admin.a")
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    # El refresh anterior quedó revocado (rotación)
    reuso = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuso.status_code == 401


def test_perfil_me(client, base_datos):
    headers = auth_headers(client, "admin.a")
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    perfil = response.json()
    assert perfil["username"] == "admin.a"
    assert "Administrador Empresa" in perfil["roles"]
    assert perfil["es_superadmin"] is False


def test_cambiar_password(client, base_datos):
    headers = auth_headers(client, "admin.a")
    response = client.post(
        "/api/v1/auth/cambiar-password",
        json={"password_actual": PASSWORD, "password_nueva": "NuevaClave9*"},
        headers=headers,
    )
    assert response.status_code == 200
    assert login(client, "admin.a", "NuevaClave9*")["access_token"]
