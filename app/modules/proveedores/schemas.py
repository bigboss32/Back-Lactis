import uuid
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class ProveedorCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=150)
    documento: str | None = None
    vereda: str | None = None
    municipio: str | None = None
    telefono: str | None = None
    precio_litro: Decimal = Field(default=Decimal("0"), ge=0)
    ruta_id: uuid.UUID | None = None
    observaciones: str | None = None


class ProveedorUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    documento: str | None = None
    vereda: str | None = None
    municipio: str | None = None
    telefono: str | None = None
    precio_litro: Decimal | None = Field(default=None, ge=0)
    ruta_id: uuid.UUID | None = None
    observaciones: str | None = None
    estado: str | None = None


class ProveedorRead(TenantRead):
    nombre: str
    documento: str | None
    vereda: str | None
    municipio: str | None
    telefono: str | None
    precio_litro: Decimal
    ruta_id: uuid.UUID | None
    observaciones: str | None
