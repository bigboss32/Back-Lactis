import uuid
from datetime import datetime

from pydantic import AliasChoices, EmailStr, Field, field_validator

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
    # roles_ctx (transitorio, roles de la empresa activa) tiene prioridad; si el
    # service no lo adjuntó cae a la property roles (todas las empresas). El JSON
    # de salida sigue llamándose "roles": la UI actual no se rompe.
    roles: list[RolResumen] = Field(
        default=[], validation_alias=AliasChoices("roles_ctx", "roles")
    )
    # Nombres de las empresas de las que el usuario es miembro
    empresas: list[str] = Field(
        default=[], validation_alias=AliasChoices("empresas_nombres", "empresas")
    )


class AsignarRoles(BaseSchema):
    rol_ids: list[uuid.UUID]


class MembresiaEmpresaIn(BaseSchema):
    empresa_id: uuid.UUID
    rol_ids: list[uuid.UUID]


class AsignarEmpresas(BaseSchema):
    membresias: list[MembresiaEmpresaIn]
    empresa_principal_id: uuid.UUID | None = None


class MembresiaEmpresaRead(BaseSchema):
    empresa_id: uuid.UUID
    empresa_nombre: str
    roles: list[RolResumen] = []


class AsignarPermisos(BaseSchema):
    permiso_ids: list[uuid.UUID]


class CambiarPasswordAdmin(BaseSchema):
    """Un administrador restablece la contraseña de otro usuario."""

    password: str

    @field_validator("password")
    @classmethod
    def password_fuerte(cls, v: str) -> str:
        return validar_fortaleza_password(v)
