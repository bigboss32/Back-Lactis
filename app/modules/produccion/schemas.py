import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class TipoQuesoCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=100)
    descripcion: str | None = None
    precio_referencia: Decimal = Field(default=Decimal("0"), ge=0)


class TipoQuesoUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    descripcion: str | None = None
    precio_referencia: Decimal | None = Field(default=None, ge=0)
    estado: str | None = None


class TipoQuesoRead(TenantRead):
    nombre: str
    descripcion: str | None
    precio_referencia: Decimal


class ProduccionCreate(BaseSchema):
    fecha: date
    tipo_queso_id: uuid.UUID
    sucursal_id: uuid.UUID | None = None
    cantidad: Decimal = Field(default=Decimal("0"), ge=0)
    peso_kg: Decimal = Field(gt=0)
    litros_usados: Decimal = Field(default=Decimal("0"), ge=0)
    merma: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None


class ProduccionUpdate(BaseSchema):
    fecha: date | None = None
    tipo_queso_id: uuid.UUID | None = None
    sucursal_id: uuid.UUID | None = None
    cantidad: Decimal | None = Field(default=None, ge=0)
    peso_kg: Decimal | None = Field(default=None, gt=0)
    litros_usados: Decimal | None = Field(default=None, ge=0)
    merma: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = None
    estado: str | None = None


class ProduccionRead(TenantRead):
    fecha: date
    tipo_queso_id: uuid.UUID
    tipo_queso_nombre: str | None = None
    sucursal_id: uuid.UUID | None
    cantidad: Decimal
    peso_kg: Decimal
    litros_usados: Decimal
    rendimiento: Decimal
    merma: Decimal
    observaciones: str | None
