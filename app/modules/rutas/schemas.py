from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class RutaCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=150)
    municipio: str | None = None
    descripcion: str | None = None


class RutaUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    municipio: str | None = None
    descripcion: str | None = None
    estado: str | None = None


class RutaRead(TenantRead):
    nombre: str
    municipio: str | None
    descripcion: str | None
