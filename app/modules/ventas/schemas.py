import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class VentaDetalleCreate(BaseSchema):
    producto_id: uuid.UUID
    descripcion: str | None = None
    cantidad: Decimal = Field(gt=0)
    precio_unitario: Decimal = Field(ge=0)


class VentaCreate(BaseSchema):
    tipo: Literal["factura", "remision"] = "factura"
    cliente_id: uuid.UUID
    fecha: date
    descuento: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None
    detalles: list[VentaDetalleCreate] = Field(min_length=1)
    descontar_inventario: bool = True


class VentaUpdate(BaseSchema):
    observaciones: str | None = None


class VentaDetalleRead(BaseSchema):
    producto_id: uuid.UUID
    descripcion: str | None
    cantidad: Decimal
    precio_unitario: Decimal
    total: Decimal


class VentaRead(TenantRead):
    numero: int
    tipo: str
    cliente_id: uuid.UUID
    cliente_nombre: str | None = None
    fecha: date
    subtotal: Decimal
    descuento: Decimal
    total: Decimal
    pagado: Decimal
    saldo: Decimal
    observaciones: str | None
    detalles: list[VentaDetalleRead] = []


class PagoCreate(BaseSchema):
    venta_id: uuid.UUID
    fecha: date
    valor: Decimal = Field(gt=0)
    metodo: Literal["efectivo", "transferencia", "otro"] = "efectivo"
    referencia: str | None = None
    observaciones: str | None = None


class PagoRead(TenantRead):
    venta_id: uuid.UUID
    fecha: date
    valor: Decimal
    metodo: str
    referencia: str | None
    observaciones: str | None


class CarteraCliente(BaseSchema):
    cliente_id: uuid.UUID
    cliente_nombre: str
    ventas_pendientes: int
    total_facturado: Decimal
    total_pagado: Decimal
    saldo: Decimal
