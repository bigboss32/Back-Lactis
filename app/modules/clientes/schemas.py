from pydantic import EmailStr, Field

from app.common.schemas import BaseSchema, TenantRead


class ClienteCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=150)
    documento: str | None = None
    telefono: str | None = None
    correo: EmailStr | None = None
    direccion: str | None = None
    ciudad: str | None = None
    observaciones: str | None = None


class ClienteUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    documento: str | None = None
    telefono: str | None = None
    correo: EmailStr | None = None
    direccion: str | None = None
    ciudad: str | None = None
    observaciones: str | None = None
    estado: str | None = None


class ClienteRead(TenantRead):
    nombre: str
    documento: str | None
    telefono: str | None
    correo: str | None
    direccion: str | None
    ciudad: str | None
    observaciones: str | None
