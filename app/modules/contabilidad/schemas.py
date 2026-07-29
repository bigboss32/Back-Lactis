from datetime import date
from decimal import Decimal

from app.common.schemas import BaseSchema
from app.modules.produccion.schemas import OrigenDelCosto


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
    """Estado de resultados del período.

    LA CORRECCIÓN IMPORTANTE: antes se restaba TODA la leche que entró en el mes
    contra TODO el queso que se vendió en el mes. Pero la leche del 1 de julio se
    convierte en queso que puede venderse 60 días después: no son el mismo queso.
    El resultado salía negativo sin que el negocio estuviera perdiendo, porque la
    plata de la leche estaba ahí, convertida en queso, en la bodega.

    Ahora se resta el COSTO DE LO QUE SE VENDIÓ, que sale de la cadena de lotes de
    producción, y la leche comprada queda en un bloque aparte, informativo.

    Los ingresos se abren en tres renglones que suman `ingresos_ventas` exacto
    (queso + otras ventas - descuentos), para que se pueda ver de dónde sale la
    cifra y para no cambiar el total que el usuario ya conocía.
    """

    desde: date
    hasta: date
    ingresos_ventas: Decimal
    # --- Ingresos, abiertos. Los tres suman ingresos_ventas exacto.
    queso_vendido: Decimal = Decimal("0")
    otras_ventas: Decimal = Decimal("0")
    descuentos: Decimal = Decimal("0")
    # --- Lo que costó lo que se vendió (de la cadena de lotes)
    costo_queso_vendido: Decimal = Decimal("0")
    transporte_despachos: Decimal = Decimal("0")
    queso_danado: Decimal = Decimal("0")
    # --- Aviso: queso vendido que no salió de ningún lote, así que no se pudo
    # costear. Si es distinto de cero, la utilidad se ve mejor de lo que es.
    queso_vendido_sin_costo: Decimal = Decimal("0")
    # De qué producciones salió el queso que se vendió. La suma de sus costos ES
    # `costo_queso_vendido`: es la cuenta que el usuario puede seguir para ver que
    # la leche sí se está restando.
    origen_del_costo: list[OrigenDelCosto] = []
    # --- Informativo: lo que compró en el mes y cuánto de eso sigue sin venderse.
    # NO entra en la utilidad, porque no es pérdida: la plata está ahí.
    costo_leche: Decimal
    costo_transporte: Decimal
    leche_sin_usar: Decimal = Decimal("0")
    queso_en_bodega: Decimal = Decimal("0")
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
