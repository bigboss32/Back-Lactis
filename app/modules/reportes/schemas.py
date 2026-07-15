from datetime import date
from decimal import Decimal

from app.common.schemas import BaseSchema


class SerieDia(BaseSchema):
    fecha: date
    valor: Decimal


class SerieCategoria(BaseSchema):
    etiqueta: str
    valor: Decimal


class DashboardResponse(BaseSchema):
    fecha: date
    litros_hoy: Decimal
    litros_quincena: Decimal
    valor_leche_quincena: Decimal
    produccion_kg_mes: Decimal
    ventas_mes: Decimal
    gastos_mes: Decimal
    cartera_pendiente: Decimal
    liquidaciones_por_pagar: Decimal
    alertas_no_leidas: int
    litros_por_dia: list[SerieDia]
    ventas_por_dia: list[SerieDia]
    gastos_por_categoria: list[SerieCategoria]
    produccion_por_tipo: list[SerieCategoria]
    top_proveedores: list[SerieCategoria]
