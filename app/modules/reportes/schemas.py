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
    # Valores del período anterior, para el comparativo (▲▼ %).
    litros_quincena_anterior: Decimal
    produccion_kg_mes_anterior: Decimal
    ventas_mes_anterior: Decimal
    gastos_mes_anterior: Decimal
    cartera_pendiente: Decimal
    # CUÁNTA PLATA TIENE QUE SACAR EL DUEÑO por liquidaciones: solo los saldos
    # positivos. Ver el porqué, con las cifras, en `reportes/service.py`.
    liquidaciones_por_pagar: Decimal
    # Y LO QUE LOS TERCEROS LE QUEDARON DEBIENDO A ÉL, en positivo y aparte. Son dos
    # preguntas distintas y mezclarlas no contesta ninguna. Cero en la enorme mayoría
    # de las queseras; se llena cuando a alguien los anticipos le pasaron la quincena.
    terceros_le_quedan_debiendo: Decimal = Decimal("0")
    alertas_no_leidas: int
    litros_por_dia: list[SerieDia]
    ventas_por_dia: list[SerieDia]
    gastos_por_categoria: list[SerieCategoria]
    produccion_por_tipo: list[SerieCategoria]
    top_proveedores: list[SerieCategoria]
