import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class CategoriaGastoCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=100)
    descripcion: str | None = None


class CategoriaGastoUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    descripcion: str | None = None
    estado: str | None = None


class CategoriaGastoRead(TenantRead):
    nombre: str
    descripcion: str | None


class GastoCreate(BaseSchema):
    fecha: date
    categoria_id: uuid.UUID
    concepto: str = Field(min_length=2, max_length=200)
    proveedor: str | None = None
    # Opcional (ej. flete por kilo): si vienen ambos, valor = cantidad * precio.
    cantidad: Decimal | None = Field(default=None, ge=0)
    precio_unitario: Decimal | None = Field(default=None, ge=0)
    valor: Decimal = Field(gt=0)
    numero_factura: str | None = None
    observaciones: str | None = None
    sucursal_id: uuid.UUID | None = None


class GastoUpdate(BaseSchema):
    fecha: date | None = None
    categoria_id: uuid.UUID | None = None
    concepto: str | None = Field(default=None, min_length=2, max_length=200)
    proveedor: str | None = None
    cantidad: Decimal | None = Field(default=None, ge=0)
    precio_unitario: Decimal | None = Field(default=None, ge=0)
    valor: Decimal | None = Field(default=None, gt=0)
    numero_factura: str | None = None
    observaciones: str | None = None
    sucursal_id: uuid.UUID | None = None
    estado: str | None = None


class GastoRead(TenantRead):
    fecha: date
    categoria_id: uuid.UUID
    categoria_nombre: str | None = None
    concepto: str
    proveedor: str | None
    cantidad: Decimal | None
    precio_unitario: Decimal | None
    valor: Decimal
    numero_factura: str | None
    observaciones: str | None
    adjunto_url: str | None
    sucursal_id: uuid.UUID | None
