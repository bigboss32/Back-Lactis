import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class RecepcionCreate(BaseSchema):
    fecha: date
    proveedor_id: uuid.UUID
    transportador_id: uuid.UUID | None = None
    ruta_id: uuid.UUID | None = None
    sucursal_id: uuid.UUID | None = None
    cantidad_litros: Decimal = Field(gt=0)
    precio_litro: Decimal | None = Field(default=None, ge=0, description="Si no se envía, usa el precio del proveedor")
    bonificaciones: Decimal = Field(default=Decimal("0"), ge=0)
    descuentos: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None


class RecepcionUpdate(BaseSchema):
    fecha: date | None = None
    transportador_id: uuid.UUID | None = None
    ruta_id: uuid.UUID | None = None
    sucursal_id: uuid.UUID | None = None
    cantidad_litros: Decimal | None = Field(default=None, gt=0)
    precio_litro: Decimal | None = Field(default=None, ge=0)
    bonificaciones: Decimal | None = Field(default=None, ge=0)
    descuentos: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = None
    estado: str | None = None


class RecepcionRead(TenantRead):
    fecha: date
    proveedor_id: uuid.UUID
    proveedor_nombre: str | None = None
    transportador_id: uuid.UUID | None
    ruta_id: uuid.UUID | None
    sucursal_id: uuid.UUID | None
    cantidad_litros: Decimal
    precio_litro: Decimal
    bonificaciones: Decimal
    descuentos: Decimal
    valor_bruto: Decimal
    valor_transporte: Decimal
    valor_neto: Decimal
    observaciones: str | None
    liquidacion_id: uuid.UUID | None
    # Estado de la liquidación que manda sobre este día ('borrador', 'aprobada',
    # 'parcial', 'pagada') o null si todavía no está en ninguna. Bloquean las que
    # ya tienen pagos ('parcial' y 'pagada'); en borrador y aprobada se puede
    # editar y la liquidación se recuadra sola.
    liquidacion_estado: str | None = None


class ResumenDia(BaseSchema):
    fecha: date
    total_litros: Decimal
    valor_bruto: Decimal
    valor_transporte: Decimal
    valor_neto: Decimal
    recepciones: int


class ResumenPeriodo(BaseSchema):
    desde: date
    hasta: date
    total_litros: Decimal
    valor_bruto: Decimal
    valor_transporte: Decimal
    valor_neto: Decimal
    precio_promedio: Decimal
    dias: list[ResumenDia]


# ------------------------------------------------------------ grilla quincena
class CeldaGrilla(BaseSchema):
    """Una recepción vista como celda proveedor × día de la grilla."""

    recepcion_id: uuid.UUID
    litros: Decimal
    # El día ya está dentro de una liquidación generada (la de la leche o la del
    # flete), sin importar el estado. Es una SEÑA para avisar que al tocarlo se
    # va a mover una liquidación ya emitida, no un candado.
    liquidada: bool
    # El candado de verdad: alguna de esas liquidaciones ya tiene pagos, sea
    # 'pagada' o 'parcial'. Basta un abono: esa plata ya salió contra este día.
    pagada: bool = False
    # 'borrador' | 'aprobada' | 'parcial' | 'pagada' | None, para explicar en
    # pantalla qué pasa si se edita el día.
    liquidacion_estado: str | None = None
    # True si la recepción tiene transportador asignado (se marca con un ícono).
    con_transporte: bool = False


class FilaGrilla(BaseSchema):
    proveedor_id: uuid.UUID
    proveedor_nombre: str
    vereda: str | None
    precio_litro: Decimal
    # False si el proveedor fue retirado/eliminado pero aún tiene recepciones
    # en el período (se conserva en la grilla para poder liquidarlo).
    proveedor_activo: bool = True
    celdas: dict[str, CeldaGrilla]  # clave: fecha ISO 'YYYY-MM-DD'
    total_litros: Decimal
    valor_bruto: Decimal
    descuentos: Decimal
    bonificaciones: Decimal
    valor_neto: Decimal
    valor_transporte: Decimal


class GrillaQuincena(BaseSchema):
    """Vista proveedores × días, equivalente a la hoja 'LITROS Y TRANSPORTE'."""

    desde: date
    hasta: date
    fechas: list[date]
    filas: list[FilaGrilla]
    totales_dia: dict[str, Decimal]
    total_litros: Decimal
    total_valor_neto: Decimal
    total_transporte: Decimal
