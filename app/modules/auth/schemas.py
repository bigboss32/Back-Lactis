import uuid

from pydantic import EmailStr, field_validator

from app.common.schemas import BaseSchema
from app.modules.usuarios.schemas import validar_fortaleza_password


class TokenResponse(BaseSchema):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseSchema):
    refresh_token: str


class LogoutRequest(BaseSchema):
    refresh_token: str


class RecuperarPasswordRequest(BaseSchema):
    correo: EmailStr


class ResetPasswordRequest(BaseSchema):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def password_fuerte(cls, v: str) -> str:
        return validar_fortaleza_password(v)


class CambiarPasswordRequest(BaseSchema):
    password_actual: str
    password_nueva: str

    @field_validator("password_nueva")
    @classmethod
    def password_fuerte(cls, v: str) -> str:
        return validar_fortaleza_password(v)


class PerfilResponse(BaseSchema):
    id: uuid.UUID
    nombre: str
    apellido: str
    correo: str
    username: str
    foto_url: str | None
    empresa_id: uuid.UUID | None
    sucursal_id: uuid.UUID | None
    roles: list[str]
    permisos: list[str]
    es_superadmin: bool
