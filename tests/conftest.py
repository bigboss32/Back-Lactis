"""Fixtures de pruebas: BD SQLite en memoria, app con override de sesión,
dos empresas con sus administradores y un superadmin."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models_registry  # noqa: F401
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app as fastapi_app
from app.modules.empresas.models import Empresa
from app.modules.usuarios.models import Usuario
from app.seeds.seed import seed_permisos, seed_roles

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

PASSWORD = "Clave1234*"


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture()
def base_datos(db_session):
    """Dos empresas, un admin por empresa y un superadmin global."""
    permisos = seed_permisos(db_session)
    roles = seed_roles(db_session, permisos)

    empresa_a = Empresa(nombre="Quesera A", nit="900A")
    empresa_b = Empresa(nombre="Quesera B", nit="900B")
    db_session.add_all([empresa_a, empresa_b])
    db_session.flush()

    def crear_usuario(username, empresa_id, rol):
        usuario = Usuario(
            nombre=username.title(),
            apellido="Prueba",
            correo=f"{username}@test.local",
            username=username,
            hashed_password=hash_password(PASSWORD),
            empresa_id=empresa_id,
        )
        usuario.roles = [rol]
        db_session.add(usuario)
        return usuario

    superadmin = crear_usuario("superadmin", None, roles["Administrador General"])
    admin_a = crear_usuario("admin.a", empresa_a.id, roles["Administrador Empresa"])
    admin_b = crear_usuario("admin.b", empresa_b.id, roles["Administrador Empresa"])
    db_session.flush()
    db_session.commit()
    return {
        "empresa_a": empresa_a,
        "empresa_b": empresa_b,
        "superadmin": superadmin,
        "admin_a": admin_a,
        "admin_b": admin_b,
    }


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict:
    response = client.post(
        "/api/v1/auth/login", data={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


def auth_headers(client: TestClient, username: str) -> dict:
    tokens = login(client, username)
    return {"Authorization": f"Bearer {tokens['access_token']}"}
