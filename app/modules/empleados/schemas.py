from datetime import date
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class EmpleadoCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=100)
    apellido: str = Field(min_length=2, max_length=100)
    documento: str | None = None
    cargo: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    fecha_ingreso: date | None = None
    salario: Decimal | None = Field(default=None, ge=0)


class EmpleadoUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    apellido: str | None = Field(default=None, min_length=2, max_length=100)
    documento: str | None = None
    cargo: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    fecha_ingreso: date | None = None
    salario: Decimal | None = Field(default=None, ge=0)
    estado: str | None = None


class EmpleadoRead(TenantRead):
    nombre: str
    apellido: str
    documento: str | None
    cargo: str | None
    telefono: str | None
    direccion: str | None
    fecha_ingreso: date | None
    salario: Decimal | None
