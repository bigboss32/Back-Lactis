from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead

TipoSucursal = Literal["planta", "centro_acopio", "punto_venta"]


class SucursalCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=150)
    tipo: TipoSucursal = "centro_acopio"
    direccion: str | None = None
    telefono: str | None = None
    responsable: str | None = None


class SucursalUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    tipo: TipoSucursal | None = None
    direccion: str | None = None
    telefono: str | None = None
    responsable: str | None = None
    estado: str | None = None


class SucursalRead(TenantRead):
    nombre: str
    tipo: str
    direccion: str | None
    telefono: str | None
    responsable: str | None
