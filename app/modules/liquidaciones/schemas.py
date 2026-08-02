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
    # El id viaja al frontend porque el día es editable: sin él, la pantalla
    # tendría que señalar la fila por fecha y dos días iguales (o un cambio de
    # orden) apuntarían al renglón equivocado.
    id: uuid.UUID
    fecha: date
    litros: Decimal
    precio_litro: Decimal
    valor: Decimal


class PagoLiquidacionRead(BaseSchema):
    id: uuid.UUID
    fecha: date
    valor: Decimal
    observaciones: str | None


class PagoLiquidacionCreate(BaseSchema):
    """Un pago parcial contra una liquidación aprobada.

    Mismos campos que el abono de reventa (fecha, valor, observaciones) para que
    registrar un pago se sienta igual en todo el sistema. El tope real —que no
    se pueda abonar más que el saldo— lo pone el servicio, que es el único que
    sabe cuánto queda debiendo en ese instante.
    """

    fecha: date
    valor: Decimal = Field(gt=0)
    observaciones: str | None = None


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
    # La cifra grande contra la que se abona: valor_total - anticipos. Viaja
    # calculada desde el modelo para que la pantalla no tenga que repetir la
    # resta y arriesgarse a mostrar una cifra distinta a la del comprobante.
    neto_a_pagar: Decimal
    pagado: Decimal
    # Lo que TODAVÍA se debe. Se cumple exacto: neto_a_pagar = pagado + saldo.
    saldo: Decimal
    observaciones: str | None
    detalles: list[LiquidacionDetalleRead] = []
    pagos: list[PagoLiquidacionRead] = []


class LiquidacionUpdate(BaseSchema):
    observaciones: str | None = None


class LiquidacionDetallePrecioUpdate(BaseSchema):
    """Corrección del precio por litro de UN día de la liquidación.

    El tope de 1.000.000 no es capricho: el precio del litro anda por los $1.800
    y quien teclea "1800000" por error se lleva una liquidación de cientos de
    millones. Mejor que rebote a que el dueño la descubra en el comprobante.
    """

    # Los topes van como enteros a propósito: el manejador de errores de
    # validación serializa el contexto del error a JSON tal cual, y un Decimal
    # ahí revienta la respuesta con un 500 en vez de devolver el 422.
    precio_litro: Decimal = Field(gt=0, le=1_000_000)


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
    # "Ya está descontado en una liquidación o en una nómina". Es una SEÑA, no un
    # candado: desde que el anticipo se puede corregir mientras no se haya pagado,
    # aplicado y trabado dejaron de ser lo mismo.
    aplicado: bool = False
    # Estado de la liquidación que lo tiene descontado ('borrador', 'aprobada',
    # 'parcial', 'pagada') o null si todavía no está en ninguna. Sirve para
    # avisarle al usuario que al corregirlo va a mover una liquidación ya
    # generada, y que si estaba aprobada vuelve a borrador.
    liquidacion_estado: str | None = None
    # El candado de verdad: ya salió plata contra este anticipo (la liquidación
    # tiene pagos, sea 'parcial' o 'pagada') o quedó descontado en una nómina.
    bloqueado: bool = False
