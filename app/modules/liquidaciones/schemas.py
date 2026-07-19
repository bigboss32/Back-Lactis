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


class PrevisualizarLiquidacion(BaseSchema):
    """Pre-liquidación: calcula cómo va un tercero SIN generar ni guardar nada."""

    periodo_inicio: date
    periodo_fin: date
    tipo: Literal["proveedor", "transportador"] = "proveedor"
    tercero_id: uuid.UUID


class PreLiquidacionDetalle(BaseSchema):
    fecha: date
    litros: Decimal
    precio_litro: Decimal
    valor: Decimal


class PreLiquidacionAnticipo(BaseSchema):
    fecha: date
    valor: Decimal
    observaciones: str | None = None


class PreLiquidacionRead(BaseSchema):
    """Resultado de una pre-liquidación (no persistida)."""

    tipo: str
    tercero_id: uuid.UUID
    tercero_nombre: str
    tercero_detalle: str | None = None
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
    detalles: list[PreLiquidacionDetalle] = []
    anticipos_detalle: list[PreLiquidacionAnticipo] = []


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
