import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class GenerarLiquidaciones(BaseSchema):
    periodo_inicio: date
    periodo_fin: date
    tipo: Literal["proveedor", "transportador", "ambos"] = "ambos"
    proveedor_id: uuid.UUID | None = None


class LiquidacionDetalleRead(BaseSchema):
    fecha: date
    litros: Decimal
    precio_litro: Decimal
    valor: Decimal


class LiquidacionRead(TenantRead):
    tipo: str
    proveedor_id: uuid.UUID | None
    proveedor_nombre: str | None = None
    transportador_id: uuid.UUID | None
    transportador_nombre: str | None = None
    periodo_inicio: date
    periodo_fin: date
    total_litros: Decimal
    precio_promedio: Decimal
    valor_bruto: Decimal
    bonificaciones: Decimal
    descuentos: Decimal
    valor_transporte: Decimal
    anticipos: Decimal
    valor_total: Decimal
    saldo: Decimal
    observaciones: str | None
    detalles: list[LiquidacionDetalleRead] = []


class LiquidacionUpdate(BaseSchema):
    observaciones: str | None = None


class AnticipoCreate(BaseSchema):
    tipo: Literal["proveedor", "transportador", "empleado"] = "proveedor"
    proveedor_id: uuid.UUID | None = None
    transportador_id: uuid.UUID | None = None
    empleado_id: uuid.UUID | None = None
    fecha: date
    valor: Decimal = Field(gt=0)
    observaciones: str | None = None


class AnticipoUpdate(BaseSchema):
    fecha: date | None = None
    valor: Decimal | None = Field(default=None, gt=0)
    observaciones: str | None = None


class AnticipoRead(TenantRead):
    tipo: str
    proveedor_id: uuid.UUID | None
    transportador_id: uuid.UUID | None
    empleado_id: uuid.UUID | None
    proveedor_nombre: str | None = None
    tercero_nombre: str | None = None
    fecha: date
    valor: Decimal
    observaciones: str | None
    liquidacion_id: uuid.UUID | None
    pago_empleado_id: uuid.UUID | None
    aplicado: bool = False
