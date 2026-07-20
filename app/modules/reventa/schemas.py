import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class AbonoRead(BaseSchema):
    id: uuid.UUID
    fecha: date
    valor: Decimal
    observaciones: str | None


class AbonoCreate(BaseSchema):
    fecha: date
    valor: Decimal = Field(gt=0)
    observaciones: str | None = None


# ----------------------------------------------------------------- compras
class CompraQuesoCreate(BaseSchema):
    fecha: date
    productor: str = Field(min_length=2, max_length=150)
    kilos_brutos: Decimal = Field(gt=0)
    borona_kilos: Decimal = Field(default=Decimal("0"), ge=0)
    precio_kilo: Decimal = Field(gt=0)
    observaciones: str | None = None


class CompraQuesoUpdate(BaseSchema):
    fecha: date | None = None
    productor: str | None = Field(default=None, min_length=2, max_length=150)
    kilos_brutos: Decimal | None = Field(default=None, gt=0)
    borona_kilos: Decimal | None = Field(default=None, ge=0)
    precio_kilo: Decimal | None = Field(default=None, gt=0)
    observaciones: str | None = None


class CompraQuesoRead(TenantRead):
    fecha: date
    productor: str
    kilos_brutos: Decimal
    borona_kilos: Decimal
    kilos_netos: Decimal
    precio_kilo: Decimal
    valor_total: Decimal
    abonado: Decimal
    saldo: Decimal
    observaciones: str | None
    abonos: list[AbonoRead] = []


# ------------------------------------------------------------------ ventas
class VentaQuesoCreate(BaseSchema):
    fecha: date
    cliente: str = Field(min_length=2, max_length=150)
    tipo: Literal["queso", "borona"] = "queso"
    kilos: Decimal = Field(gt=0)
    precio_kilo: Decimal = Field(gt=0)
    gasto_concepto: str | None = Field(default=None, max_length=150)
    gasto_por_kilo: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None
    # Pago inmediato: registra la venta ya pagada por completo
    pagada_de_contado: bool = False


class VentaQuesoUpdate(BaseSchema):
    fecha: date | None = None
    cliente: str | None = Field(default=None, min_length=2, max_length=150)
    kilos: Decimal | None = Field(default=None, gt=0)
    precio_kilo: Decimal | None = Field(default=None, gt=0)
    gasto_concepto: str | None = Field(default=None, max_length=150)
    gasto_por_kilo: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = None


class VentaQuesoRead(TenantRead):
    fecha: date
    cliente: str
    tipo: str
    kilos: Decimal
    precio_kilo: Decimal
    valor_total: Decimal
    gasto_concepto: str | None
    gasto_por_kilo: Decimal
    gasto_monto: Decimal
    abonado: Decimal
    saldo: Decimal
    observaciones: str | None
    abonos: list[AbonoRead] = []


# ------------------------------------------------------------ conversiones
class ConversionCreate(BaseSchema):
    fecha: date
    kilos: Decimal = Field(gt=0)
    destino: Literal["borona", "merma"] = "borona"
    observaciones: str | None = None


class ConversionRead(TenantRead):
    fecha: date
    kilos: Decimal
    destino: str
    observaciones: str | None


# ----------------------------------------------------------------- resumen
class ResumenReventa(BaseSchema):
    desde: date
    hasta: date
    # Del período (queso)
    kilos_comprados: Decimal
    total_compras: Decimal
    kilos_vendidos: Decimal  # solo ventas tipo queso
    total_ventas: Decimal  # queso + borona
    precio_promedio_compra: Decimal
    precio_promedio_venta: Decimal  # solo queso
    total_gastos: Decimal  # gastos de venta del período (transporte, etc.)
    merma_estimada: Decimal  # kilos comprados - kilos vendidos (queso) del período
    ganancia_estimada: Decimal  # ventas totales - costo del queso vendido - gastos
    margen_por_kilo: Decimal  # ganancia neta por kilo de queso vendido
    # Del período (borona)
    kilos_borona_vendidos: Decimal
    total_ventas_borona: Decimal
    # Acumulados (histórico, sin filtro de fechas)
    kilos_disponibles: Decimal  # queso: comprados netos - vendidos - pasados a borona
    borona_disponible: Decimal  # de compras + conversiones - vendida
    por_pagar_productores: Decimal
    por_cobrar_clientes: Decimal


class SugerenciasReventa(BaseSchema):
    """Nombres ya registrados para autocompletar al crear compras/ventas."""

    productores: list[str]
    clientes: list[str]
