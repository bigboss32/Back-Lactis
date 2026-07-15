import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class AbrirCaja(BaseSchema):
    fecha: date
    sucursal_id: uuid.UUID | None = None
    saldo_inicial: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None


class CerrarCaja(BaseSchema):
    efectivo_contado: Decimal = Field(ge=0)
    observaciones: str | None = None


class MovimientoCajaCreate(BaseSchema):
    caja_id: uuid.UUID
    tipo: Literal["ingreso", "egreso"]
    concepto: str = Field(min_length=2, max_length=200)
    valor: Decimal = Field(gt=0)
    referencia: str | None = None


class MovimientoCajaRead(TenantRead):
    caja_id: uuid.UUID
    tipo: str
    concepto: str
    valor: Decimal
    referencia: str | None


class CajaRead(TenantRead):
    fecha: date
    sucursal_id: uuid.UUID | None
    saldo_inicial: Decimal
    total_ingresos: Decimal
    total_egresos: Decimal
    saldo_final: Decimal
    efectivo_contado: Decimal | None
    diferencia: Decimal | None
    observaciones: str | None
    movimientos: list[MovimientoCajaRead] = []
