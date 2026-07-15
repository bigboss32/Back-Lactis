from datetime import date
from decimal import Decimal

from app.common.schemas import BaseSchema


class AsientoLibroDiario(BaseSchema):
    fecha: date
    origen: str  # venta | pago | gasto | caja | banco | recepcion
    concepto: str
    ingreso: Decimal
    egreso: Decimal
    referencia: str | None


class LibroDiarioResponse(BaseSchema):
    desde: date
    hasta: date
    total_ingresos: Decimal
    total_egresos: Decimal
    asientos: list[AsientoLibroDiario]


class LineaCategoria(BaseSchema):
    categoria: str
    total: Decimal


class EstadoResultados(BaseSchema):
    desde: date
    hasta: date
    ingresos_ventas: Decimal
    costo_leche: Decimal
    costo_transporte: Decimal
    gastos_por_categoria: list[LineaCategoria]
    total_gastos: Decimal
    utilidad_bruta: Decimal
    utilidad_neta: Decimal
    margen_neto: Decimal  # porcentaje


class BalanceResponse(BaseSchema):
    fecha_corte: date
    saldo_cajas: Decimal
    saldo_bancos: Decimal
    cartera_por_cobrar: Decimal
    liquidaciones_por_pagar: Decimal
    total_disponible: Decimal
