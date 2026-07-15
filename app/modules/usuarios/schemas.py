import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.common.schemas import AuditRead, BaseSchema


def validar_fortaleza_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    if not any(c.isdigit() for c in password):
        raise ValueError("La contraseña debe incluir al menos un número")
    if not any(c.isalpha() for c in password):
        raise ValueError("La contraseña debe incluir al menos una letra")
    return password


class PermisoRead(AuditRead):
    modulo: str
    accion: str
    descripcion: str | None


class RolCreate(BaseSchema):
    nombre: str = Field(min_length=3, max_length=80)
    descripcion: str | None = None
    permiso_ids: list[uuid.UUID] = []


class RolUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=3, max_length=80)
    descripcion: str | None = None
    estado: str | None = None


class RolRead(AuditRead):
    nombre: str
    descripcion: str | None
    es_sistema: bool
    permisos: list[PermisoRead] = []


class RolResumen(BaseSchema):
    id: uuid.UUID
    nombre: str


class UsuarioCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=100)
    apellido: str = Field(min_length=2, max_length=100)
    documento: str | None = None
    correo: EmailStr
    telefono: str | None = None
    username: str = Field(min_length=3, max_length=60)
    password: str
    empresa_id: uuid.UUID | None = None
    sucursal_id: uuid.UUID | None = None
    rol_ids: list[uuid.UUID] = []

    @field_validator("password")
    @classmethod
    def password_fuerte(cls, v: str) -> str:
        return validar_fortaleza_password(v)


class UsuarioUpdate(BaseSchema):
    nombre: str | None = None
    apellido: str | None = None
    documento: str | None = None
    correo: EmailStr | None = None
    telefono: str | None = None
    sucursal_id: uuid.UUID | None = None
    estado: str | None = None


class UsuarioRead(AuditRead):
    nombre: str
    apellido: str
    documento: str | None
    correo: str
    telefono: str | None
    username: str
    foto_url: str | None
    empresa_id: uuid.UUID | None
    sucursal_id: uuid.UUID | None
    ultimo_acceso: datetime | None
    bloqueado: bool
    roles: list[RolResumen] = []


class AsignarRoles(BaseSchema):
    rol_ids: list[uuid.UUID]


class AsignarPermisos(BaseSchema):
    permiso_ids: list[uuid.UUID]


class CambiarPasswordAdmin(BaseSchema):
    """Un administrador restablece la contraseña de otro usuario."""

    password: str

    @field_validator("password")
    @classmethod
    def password_fuerte(cls, v: str) -> str:
        return validar_fortaleza_password(v)
