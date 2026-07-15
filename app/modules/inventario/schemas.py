import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead

Categoria = Literal["leche", "insumo", "empaque", "producto_terminado"]
TipoMovimiento = Literal["entrada", "salida", "ajuste"]


class ProductoCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=150)
    categoria: Categoria = "insumo"
    unidad: str = "unidad"
    stock_minimo: Decimal = Field(default=Decimal("0"), ge=0)
    costo_unitario: Decimal = Field(default=Decimal("0"), ge=0)
    tipo_queso_id: uuid.UUID | None = None


class ProductoUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    categoria: Categoria | None = None
    unidad: str | None = None
    stock_minimo: Decimal | None = Field(default=None, ge=0)
    costo_unitario: Decimal | None = Field(default=None, ge=0)
    tipo_queso_id: uuid.UUID | None = None
    estado: str | None = None


class ProductoRead(TenantRead):
    nombre: str
    categoria: str
    unidad: str
    stock_minimo: Decimal
    costo_unitario: Decimal
    tipo_queso_id: uuid.UUID | None


class ProductoStockRead(ProductoRead):
    stock_actual: Decimal
    bajo_minimo: bool


class MovimientoCreate(BaseSchema):
    producto_id: uuid.UUID
    sucursal_id: uuid.UUID | None = None
    fecha: date
    tipo: TipoMovimiento
    cantidad: Decimal
    costo_unitario: Decimal = Field(default=Decimal("0"), ge=0)
    referencia: str | None = None
    observaciones: str | None = None


class MovimientoRead(TenantRead):
    producto_id: uuid.UUID
    producto_nombre: str | None = None
    sucursal_id: uuid.UUID | None
    fecha: date
    tipo: str
    cantidad: Decimal
    costo_unitario: Decimal
    referencia: str | None
    observaciones: str | None


class KardexEntry(BaseSchema):
    fecha: date
    tipo: str
    cantidad: Decimal
    costo_unitario: Decimal
    referencia: str | None
    saldo: Decimal


class KardexResponse(BaseSchema):
    producto_id: uuid.UUID
    producto_nombre: str
    unidad: str
    stock_actual: Decimal
    movimientos: list[KardexEntry]
