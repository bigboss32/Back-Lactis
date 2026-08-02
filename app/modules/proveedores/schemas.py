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
    # OJO: aquí NO va 'estado'. Antes sí estaba y era un hueco por dos lados:
    # · aceptaba cualquier texto ("retirado", "xyz"), y con eso el chip de la
    #   tabla y el filtro de Estado quedaban mostrando un estado que no existe.
    # · dejaba al proveedor en 'inactivo' SIN que nada lo hiciera cumplir: se le
    #   seguía pudiendo registrar leche como si nada.
    # Ahora el estado se cambia solo por /desactivar y /activar, que sí validan
    # el valor, dejan rastro en auditoría y son los que la recepción respeta.


class ProveedorRead(TenantRead):
    nombre: str
    documento: str | None
    vereda: str | None
    municipio: str | None
    telefono: str | None
    precio_litro: Decimal
    ruta_id: uuid.UUID | None
    observaciones: str | None
