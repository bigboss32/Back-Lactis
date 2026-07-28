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


# ------------------------------------------- saldos de la cuenta anterior
class SaldoAnteriorCreate(BaseSchema):
    """Una cuenta a medio pagar traída del sistema anterior.

    `abonado` es lo que el tercero ya había pagado en el libro viejo: casi
    ninguna cuenta llega en ceros. Se guarda además como el primer abono del
    historial para que el detalle cuadre con el total abonado.
    """

    tipo: Literal["cobrar", "pagar"]
    tercero: str = Field(min_length=2, max_length=150)
    fecha: date
    concepto: str = Field(min_length=2, max_length=200)
    valor_total: Decimal = Field(gt=0)
    abonado: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = Field(default=None, max_length=500)


class SaldoAnteriorUpdate(BaseSchema):
    """`abonado` NO se edita aquí: solo se mueve registrando o eliminando abonos,
    igual que en las compras y en las ventas."""

    tipo: Literal["cobrar", "pagar"] | None = None
    tercero: str | None = Field(default=None, min_length=2, max_length=150)
    fecha: date | None = None
    concepto: str | None = Field(default=None, min_length=2, max_length=200)
    valor_total: Decimal | None = Field(default=None, gt=0)
    observaciones: str | None = Field(default=None, max_length=500)


class SaldoAnteriorRead(TenantRead):
    tipo: str
    tercero: str
    fecha: date
    concepto: str
    valor_total: Decimal
    abonado: Decimal
    saldo: Decimal
    observaciones: str | None
    abonos: list[AbonoRead] = []


# ------------------------------------------------------------ conversiones
class ConversionCreate(BaseSchema):
    fecha: date
    kilos: Decimal = Field(gt=0)
    destino: Literal["borona", "merma"] = "borona"
    precio_kilo: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None


class ConversionRead(TenantRead):
    fecha: date
    kilos: Decimal
    destino: str
    precio_kilo: Decimal
    observaciones: str | None


# ----------------------------------------------------------------- resumen
class GananciaProducto(BaseSchema):
    """Una línea del desglose de la ganancia del período: a dónde fue el queso
    comprado (vendido como queso, pasado a borona, perdido como merma o todavía
    en inventario) y cuánta plata dejó cada destino."""

    producto: str  # 'queso' | 'borona' | 'merma' | 'pendiente' | 'anterior'
    etiqueta: str  # texto listo para mostrar en la UI
    nota: str  # sub-texto explicativo corto
    # Kilos DEL LOTE COMPRADO que fueron a este destino (siempre >= 0). Los
    # cuatro destinos suman exactamente kilos_comprados.
    kilos: Decimal
    # Kilos realmente VENDIDOS de este producto. En el queso es igual a `kilos`;
    # en la borona puede diferir (se puede vender borona convertida en otro
    # período, o la que llegó gratis con el lote). En merma/residuo es 0.
    kilos_vendidos: Decimal
    ingreso: Decimal
    costo: Decimal  # negativo solo en la fila 'anterior' (se pagó en otro período)
    gastos: Decimal
    ganancia: Decimal  # ingreso - costo - gastos
    precio_venta_kilo: Decimal  # ingreso / kilos_vendidos (0 si no se vendió)
    costo_kilo: Decimal  # precio promedio de compra del período


class GananciaProductor(BaseSchema):
    """Ganancia ESTIMADA de lo comprado a un productor en el período: reparte el
    valor neto que dejó cada kilo comprado entre los kilos de cada productor.
    La suma de las filas cuadra con la ganancia neta del período."""

    productor: str
    compras: int  # cuántas compras en el período
    kilos: Decimal
    total_comprado: Decimal  # valor de sus compras (NO es lo que se le ha pagado)
    precio_promedio: Decimal  # total comprado / kilos
    por_pagar: Decimal  # saldo pendiente con ese productor (histórico)
    margen_por_kilo: Decimal  # valor realizado por kilo - su precio promedio
    ganancia_estimada: Decimal


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
    ganancia_estimada: Decimal  # ventas totales - compras del período - gastos
    margen_por_kilo: Decimal  # ganancia neta por kilo vendido (queso + borona)
    # Lo neto que dejó cada kilo comprado en el período: (ventas - gastos) /
    # kilos comprados. Es la base para repartir la ganancia entre productores.
    valor_realizado_kilo: Decimal
    # Del período (borona)
    kilos_borona_vendidos: Decimal
    total_ventas_borona: Decimal
    # Del período (ajustes del inventario de queso)
    kilos_a_borona: Decimal  # queso pasado a borona
    kilos_merma: Decimal  # LA MERMA REAL: ajustes con destino merma
    # Residuo CON SIGNO del lote comprado: comprado - vendido como queso -
    # pasado a borona - merma. Negativo = se movió queso de otra temporada.
    kilos_pendientes: Decimal
    # Desgloses de la ganancia del período
    por_producto: list[GananciaProducto] = []
    por_productor: list[GananciaProductor] = []
    # Acumulados (histórico, sin filtro de fechas)
    kilos_disponibles: Decimal  # queso: comprados netos - vendidos - ajustados
    borona_disponible: Decimal  # de compras + conversiones - vendida
    # Las dos cifras de cartera INCLUYEN los saldos de la cuenta anterior: es lo
    # que de verdad se debe cobrar y pagar hoy. Los dos campos de abajo son ese
    # pedazo por separado, para poder mostrar el desglose y que se vea de dónde
    # sale la suma. Ojo: los saldos anteriores NO tocan kilos ni ganancia.
    por_pagar_productores: Decimal
    por_cobrar_clientes: Decimal
    por_cobrar_libro_anterior: Decimal = Decimal("0")
    por_pagar_libro_anterior: Decimal = Decimal("0")


class SugerenciasReventa(BaseSchema):
    """Nombres ya registrados para autocompletar al crear compras/ventas."""

    productores: list[str]
    clientes: list[str]


# ------------------------------------------------------- estado de cuenta
# CONFIDENCIALIDAD: el estado de cuenta SE LE ENTREGA AL CLIENTE (se le manda por
# WhatsApp). Por eso estos esquemas NO llevan gasto_concepto, gasto_por_kilo,
# gasto_monto, "venta libre", costos de compra, productores, márgenes ni las
# observaciones de la venta o del abono: serían los números internos de la
# quesera y le revelarían su ganancia al cliente.
class EstadoCuentaVenta(BaseSchema):
    """Una compra que le hicimos al cliente, con lo que lleva abonado."""

    fecha: date
    tipo: str  # 'queso' | 'borona'
    producto: str  # 'Queso' | 'Borona' (listo para mostrar)
    kilos: Decimal
    precio_kilo: Decimal
    valor_total: Decimal
    abonado: Decimal
    saldo: Decimal
    estado: str  # pendiente | parcial | pagada


class EstadoCuentaPago(BaseSchema):
    """Un abono recibido del cliente (sin importar a qué venta se aplicó).

    NO lleva `observaciones` A PROPÓSITO: la observación del abono es la nota
    INTERNA que la quesera se escribe a sí misma ("le rebajé el flete", "a tal
    productor le pagamos tanto el kilo"), y este esquema se le entrega al
    cliente. Se filtraba el flete, el nombre del productor y el precio de compra.
    Si algún día se quiere mostrarle al cliente una referencia del pago (número
    de consignación, banco), va en un campo NUEVO pensado para eso y llenado
    para él, nunca reutilizando el interno.
    """

    fecha: date
    valor: Decimal


class EstadoCuentaSaldoAnterior(BaseSchema):
    """Una cuenta a medio pagar que el cliente traía del sistema anterior.

    Solo lleva lo que el cliente reconoce de su propia deuda: la fecha del
    documento viejo, de qué era, cuánto valía, cuánto abonó y cuánto queda. Las
    `observaciones` del saldo NO salen: son la nota interna de la quesera, igual
    que en EstadoCuentaPago.
    """

    fecha: date
    concepto: str
    valor_total: Decimal
    abonado: Decimal
    saldo: Decimal


class EstadoCuentaCliente(BaseSchema):
    cliente: str
    desde: date | None
    hasta: date | None
    emitido: date  # fecha de generación
    compras: int  # cuántas ventas se le hicieron (las del sistema, no las del libro)
    total_kilos: Decimal
    # `total_facturado` y `total_abonado` son SOLO del sistema; lo que venía del
    # libro anterior va aparte en los tres campos libro_anterior_*.
    total_facturado: Decimal
    total_abonado: Decimal
    # TODO lo que el cliente debe hoy, que es la única cifra que le importa:
    #   (total_facturado - total_abonado) + libro_anterior_saldo = saldo
    saldo: Decimal
    ventas: list[EstadoCuentaVenta] = []
    pagos: list[EstadoCuentaPago] = []
    # Lo que traía debiendo del sistema anterior (vacío para casi todos)
    saldos_anteriores: list[EstadoCuentaSaldoAnterior] = []
    libro_anterior_total: Decimal = Decimal("0")
    libro_anterior_abonado: Decimal = Decimal("0")
    libro_anterior_saldo: Decimal = Decimal("0")
