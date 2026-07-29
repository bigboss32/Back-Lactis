import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class TipoQuesoCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=100)
    descripcion: str | None = None
    precio_referencia: Decimal = Field(default=Decimal("0"), ge=0)


class TipoQuesoUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    descripcion: str | None = None
    precio_referencia: Decimal | None = Field(default=None, ge=0)
    estado: str | None = None


class TipoQuesoRead(TenantRead):
    nombre: str
    descripcion: str | None
    precio_referencia: Decimal


class ProduccionCreate(BaseSchema):
    fecha: date
    tipo_queso_id: uuid.UUID
    sucursal_id: uuid.UUID | None = None
    cantidad: Decimal = Field(default=Decimal("0"), ge=0)
    peso_kg: Decimal = Field(gt=0)
    litros_usados: Decimal = Field(default=Decimal("0"), ge=0)
    merma: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None


class ProduccionUpdate(BaseSchema):
    fecha: date | None = None
    tipo_queso_id: uuid.UUID | None = None
    sucursal_id: uuid.UUID | None = None
    cantidad: Decimal | None = Field(default=None, ge=0)
    peso_kg: Decimal | None = Field(default=None, gt=0)
    litros_usados: Decimal | None = Field(default=None, ge=0)
    merma: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = None
    estado: str | None = None


class ProduccionRead(TenantRead):
    fecha: date
    tipo_queso_id: uuid.UUID
    tipo_queso_nombre: str | None = None
    sucursal_id: uuid.UUID | None
    cantidad: Decimal
    peso_kg: Decimal
    litros_usados: Decimal
    rendimiento: Decimal
    merma: Decimal
    observaciones: str | None


# ------------------------------------------- utilidad por lote de producción
class LecheDelLoteRead(BaseSchema):
    """De qué proveedor vino la leche que usó este lote y cuánto costó.

    Es lo que hace que el costo del lote sea real y no un promedio del mes: son
    las recepciones concretas que se consumieron, con el precio de cada una.
    """

    proveedor: str
    fecha_recepcion: date
    litros: Decimal
    costo_leche: Decimal
    costo_transporte: Decimal
    costo: Decimal  # leche + transporte


class VentaDelLoteProduccionRead(BaseSchema):
    """Una venta que se llevó kilos de este lote.

    `kilos` son los que salieron de ESTE lote y `kilos_venta` los del renglón
    completo: un despacho grande se reparte entre varios lotes.
    """

    fecha: date
    cliente: str
    producto: str
    kilos: Decimal
    kilos_venta: Decimal
    precio_kilo: Decimal
    ingreso: Decimal
    costo: Decimal
    utilidad: Decimal
    partida: bool


class LoteProduccionRead(BaseSchema):
    """Una producción con lo que costó y lo que dejó.

    OJO con `utilidad`: es la de lo que YA se vendió del lote, y NO le resta el
    costo del queso que sigue en bodega. Restárselo es justo el error que hace que
    el estado de resultados del mes salga negativo cuando el negocio va bien: la
    plata de la leche está ahí, convertida en queso, esperando venderse.
    """

    fecha: date
    tipo_queso: str
    # 'produccion' = se hizo aquí, con su leche detrás.
    # 'existencia' = ya estaba en bodega y se cargó a mano; su costo es el que se
    # cargó y no tiene leche. Es el caso normal al empezar a usar el sistema.
    origen: str
    referencia: str | None = None
    # Existencia cargada SIN costo: sus kilos salen como si hubieran costado cero,
    # así que hacen ver la utilidad mejor de lo que es.
    sin_costo: bool = False
    litros_usados: Decimal
    kilos_producidos: Decimal
    merma: Decimal
    rendimiento: Decimal  # kilos de queso por litro de leche
    # Lo que costó
    costo_leche: Decimal
    costo_transporte: Decimal
    costo_total: Decimal
    costo_kilo: Decimal
    # A dónde fueron los kilos (los tres suman kilos_producidos)
    kilos_vendidos: Decimal
    # Ajustes de inventario hacia abajo: se dañó o se corrigió un sobrante. Sí se
    # le resta a la utilidad, porque es plata que salió sin ingreso.
    kilos_de_baja: Decimal = Decimal("0")
    kilos_en_bodega: Decimal
    # Plata
    ingresos: Decimal
    costo_vendido: Decimal
    costo_de_baja: Decimal = Decimal("0")
    costo_en_bodega: Decimal
    # ingresos - costo_vendido - costo_de_baja. Lo de bodega NO se resta: ese queso
    # está ahí, no se ha perdido.
    utilidad: Decimal
    precio_venta_kilo: Decimal
    vendido_completo: bool
    # Litros que se usaron sin leche registrada que los respalde
    litros_sin_recepcion: Decimal
    detalle_leche: list[LecheDelLoteRead] = []
    detalle_ventas: list[VentaDelLoteProduccionRead] = []


class LotesProduccionPanel(BaseSchema):
    """Los lotes de producción con lo que dejó cada uno.

    Los totales son la suma EXACTA de los lotes listados. Los tres avisos del
    final no se esconden nunca: significan que falta cargar algo y que la cuenta
    está incompleta.
    """

    lotes: list[LoteProduccionRead] = []
    total_utilidad: Decimal
    total_litros: Decimal
    total_kilos: Decimal
    total_costo: Decimal
    total_ingresos: Decimal
    total_kilos_en_bodega: Decimal
    total_costo_en_bodega: Decimal
    mejor: date | None = None
    peor: date | None = None
    # Queso vendido (o dado de baja) que no salió de ningún lote registrado
    kilos_sin_lote: Decimal
    # Existencia cargada a mano sin costo: hace ver la utilidad mejor de lo que es
    kilos_existencia_sin_costo: Decimal = Decimal("0")
    ingreso_sin_lote: Decimal
    # Litros usados en producciones sin leche registrada que los respalde
    litros_sin_recepcion: Decimal
    # Leche recibida que todavía no se ha usado en ninguna producción
    litros_sin_usar: Decimal
    costo_litros_sin_usar: Decimal
