import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class VentaDetalleCreate(BaseSchema):
    producto_id: uuid.UUID
    descripcion: str | None = None
    cantidad: Decimal = Field(gt=0, decimal_places=2)
    precio_unitario: Decimal = Field(ge=0, decimal_places=2)


class VentaCreate(BaseSchema):
    tipo: Literal["factura", "remision"] = "factura"
    cliente_id: uuid.UUID
    fecha: date
    descuento: Decimal = Field(default=Decimal("0"), ge=0)
    # Lo que cuesta LLEVAR el despacho (el flete a Bogotá o a donde sea). NO se le
    # suma al total que paga el cliente: es un costo de la quesera, y es lo que hace
    # que el kilo puesto en destino valga más que el kilo en la planta.
    gasto_concepto: str | None = Field(default=None, max_length=150)
    gasto_por_kilo: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None
    detalles: list[VentaDetalleCreate] = Field(min_length=1)
    descontar_inventario: bool = True


class VentaUpdate(BaseSchema):
    tipo: Literal["factura", "remision"] | None = None
    cliente_id: uuid.UUID | None = None
    fecha: date | None = None
    descuento: Decimal | None = Field(default=None, ge=0)
    gasto_concepto: str | None = Field(default=None, max_length=150)
    gasto_por_kilo: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = None
    # Si viene, reemplaza las líneas de la venta (recalcula totales e inventario).
    detalles: list[VentaDetalleCreate] | None = Field(default=None, min_length=1)


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
    # El flete del despacho. `gasto_monto` lo calcula el servicio a partir del valor
    # por kilo y de los kilos que van, y NO está incluido en `total`.
    gasto_concepto: str | None = None
    gasto_por_kilo: Decimal = Decimal("0")
    gasto_monto: Decimal = Decimal("0")
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
