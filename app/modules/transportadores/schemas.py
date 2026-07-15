import uuid
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class TransportadorCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=150)
    documento: str | None = None
    telefono: str | None = None
    ruta_id: uuid.UUID | None = None
    valor_transporte: Decimal = Field(default=Decimal("0"), ge=0)


class TransportadorUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    documento: str | None = None
    telefono: str | None = None
    ruta_id: uuid.UUID | None = None
    valor_transporte: Decimal | None = Field(default=None, ge=0)
    estado: str | None = None


class TransportadorRead(TenantRead):
    nombre: str
    documento: str | None
    telefono: str | None
    ruta_id: uuid.UUID | None
    valor_transporte: Decimal
