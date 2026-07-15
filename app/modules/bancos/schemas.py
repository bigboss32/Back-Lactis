import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class CuentaCreate(BaseSchema):
    banco: str = Field(min_length=2, max_length=100)
    numero_cuenta: str = Field(min_length=3, max_length=50)
    tipo: Literal["ahorros", "corriente"] = "ahorros"
    titular: str | None = None
    saldo_inicial: Decimal = Field(default=Decimal("0"))


class CuentaUpdate(BaseSchema):
    banco: str | None = None
    numero_cuenta: str | None = None
    tipo: Literal["ahorros", "corriente"] | None = None
    titular: str | None = None
    estado: str | None = None


class CuentaRead(TenantRead):
    banco: str
    numero_cuenta: str
    tipo: str
    titular: str | None
    saldo_inicial: Decimal


class CuentaSaldoRead(CuentaRead):
    saldo_actual: Decimal


class MovimientoBancarioCreate(BaseSchema):
    cuenta_id: uuid.UUID
    fecha: date
    tipo: Literal["ingreso", "egreso"]
    valor: Decimal = Field(gt=0)
    concepto: str = Field(min_length=2, max_length=200)
    referencia: str | None = None


class MovimientoBancarioRead(TenantRead):
    cuenta_id: uuid.UUID
    fecha: date
    tipo: str
    valor: Decimal
    concepto: str
    referencia: str | None
    conciliado: bool
    fecha_conciliacion: datetime | None


class ConciliarRequest(BaseSchema):
    movimiento_ids: list[uuid.UUID] = Field(min_length=1)
