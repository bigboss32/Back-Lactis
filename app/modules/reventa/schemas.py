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


# --------------------------------------- estado de cuenta DEL PRODUCTOR
# CONFIDENCIALIDAD, AL REVÉS QUE EN EL DEL CLIENTE: este documento SE LE ENTREGA
# AL PRODUCTOR para cuadrar cuentas con él. Por eso estos esquemas NO llevan
# NADA del lado de la venta: ni a qué precio se revendió su queso, ni
# total_ventas, ni precio_promedio_venta, ni valor_realizado_kilo, ni márgenes,
# ni ganancia, ni los gastos de venta (flete), ni el nombre de ningún CLIENTE.
# Tampoco llevan los saldos del libro anterior de tipo 'cobrar': esos son deudas
# de CLIENTES con la quesera y no tienen nada que ver con un productor.
# Solo va lo que es suyo: lo que le compraron, lo que le pagaron y lo que se le
# debe.
#
# OJO CON LOS SIGNOS, que van al contrario del documento del cliente: allá un
# saldo positivo significa que ÉL DEBE; aquí un saldo positivo significa que LA
# QUESERA LE DEBE A ÉL. Siempre va rotulado ("saldo a favor del productor"), o
# se lee invertido.
class EstadoCuentaCompra(BaseSchema):
    """Una compra que se le hizo al productor, con lo que lleva abonado."""

    fecha: date
    kilos: Decimal  # kilos_netos: los que se le pagan
    borona_kilos: Decimal  # la borona que vino con el lote (no se paga); 0 si no hubo
    precio_kilo: Decimal
    valor_total: Decimal
    abonado: Decimal
    saldo: Decimal
    estado: str  # pendiente | parcial | pagada


class EstadoCuentaPagoProductor(BaseSchema):
    """Un pago que se le hizo al productor (sin importar a qué compra se aplicó).

    NO lleva `observaciones` A PROPÓSITO, por la misma razón que
    EstadoCuentaPago: la observación del abono es la nota INTERNA que la quesera
    se escribe a sí misma, y este esquema se le entrega al productor. Ya hubo un
    incidente por esto en el documento del cliente y se quitó; no se repite.
    """

    fecha: date
    valor: Decimal


class EstadoCuentaProductor(BaseSchema):
    productor: str
    desde: date | None
    hasta: date | None
    emitido: date  # fecha de generación
    compras: int  # cuántas compras se le hicieron (las del sistema, no las del libro)
    total_kilos: Decimal  # kilos netos, los que se le pagan
    # `total_comprado` y `total_pagado` son SOLO del sistema; lo que venía del
    # libro anterior va aparte en los tres campos libro_anterior_*.
    total_comprado: Decimal  # lo que valen sus compras
    total_pagado: Decimal  # lo que se le ha abonado
    # Lo que traía a medio pagar del sistema anterior: SOLO los saldos de tipo
    # 'pagar' (los de tipo 'cobrar' son deudas de clientes y no entran aquí).
    saldos_anteriores: list[EstadoCuentaSaldoAnterior] = []
    libro_anterior_total: Decimal = Decimal("0")
    libro_anterior_abonado: Decimal = Decimal("0")
    libro_anterior_saldo: Decimal = Decimal("0")
    # TODO lo que se le debe hoy, que es la única cifra que le importa a él:
    #   (total_comprado - total_pagado) + libro_anterior_saldo = saldo
    saldo: Decimal
    compras_detalle: list[EstadoCuentaCompra] = []
    pagos: list[EstadoCuentaPagoProductor] = []


# -------------------------------------------------------------- temporadas
class TemporadaCreate(BaseSchema):
    """`fecha_fin` en null crea la temporada ABIERTA (la que está corriendo)."""

    nombre: str = Field(min_length=2, max_length=80)
    fecha_inicio: date
    fecha_fin: date | None = None
    notas: str | None = Field(default=None, max_length=500)


class TemporadaUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=80)
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    notas: str | None = Field(default=None, max_length=500)


class TemporadaCerrar(BaseSchema):
    """Cerrar la temporada es ponerle fecha de fin. Por defecto, hoy."""

    fecha_fin: date | None = None


class TemporadaRead(TenantRead):
    nombre: str
    fecha_inicio: date
    fecha_fin: date | None
    notas: str | None
    abierta: bool


class TemporadaResumen(BaseSchema):
    """Una temporada con sus cifras, calculadas con el MISMO motor del resumen.

    Ojo con de dónde sale cada cifra, que es lo que hace que se pueda cuadrar:

    - Todo lo de plata y de kilos es DEL PERÍODO de la temporada, sacado de
      `ReventaResumenService.resumen(fecha_inicio, fecha_fin)`. Por eso `ganancia`
      es exactamente la misma cifra que muestra el Resumen si se filtra a esas
      fechas: es la misma función, no una copia.
    - `por_cobrar` y `por_pagar` son también SOLO de los documentos de esas
      fechas, no la cartera de siempre. Si no, una temporada vieja ya cobrada
      aparecería con deuda por culpa de la que está corriendo.
    - `por_cobrar`/`por_pagar` NO incluyen el libro anterior: esas cuentas vienen
      de otro sistema, no tienen kilos y no pertenecen a ninguna temporada.
    """

    id: uuid.UUID
    nombre: str
    fecha_inicio: date
    # En la temporada abierta es la fecha con la que se calculó (hoy), para que en
    # pantalla se vea hasta dónde llegan las cifras y no parezcan de todo el año.
    fecha_fin: date
    abierta: bool
    dias: int
    notas: str | None
    # Kilos
    kilos_comprados: Decimal
    kilos_vendidos: Decimal
    kilos_borona_vendidos: Decimal
    kilos_a_borona: Decimal
    kilos_merma: Decimal
    kilos_pendientes: Decimal
    # Plata
    total_compras: Decimal
    total_ventas: Decimal
    total_gastos: Decimal
    ganancia: Decimal
    margen_por_kilo: Decimal
    precio_promedio_compra: Decimal
    precio_promedio_venta: Decimal
    # Lo que falta de ESTA temporada
    por_cobrar: Decimal
    por_pagar: Decimal
    # Si ya no falta nada: sin queso pendiente, sin cobrar y sin pagar
    cerrada_de_verdad: bool


class TemporadasPanel(BaseSchema):
    """Lo que necesita la pantalla de temporadas en una sola llamada.

    Los totales son la SUMA de las temporadas listadas, no el histórico completo:
    lo que está fuera de toda temporada no puede aparecer sumado aquí porque
    entonces la lista no daría el total y ese es justo el desglose que el usuario
    revisa con calculadora. `dias_sin_temporada` avisa de esos huecos.
    """

    temporadas: list[TemporadaResumen] = []
    # Suma de las temporadas de la lista
    total_ganancia: Decimal
    total_kilos_comprados: Decimal
    total_ventas: Decimal
    total_compras: Decimal
    # La mejor y la peor por ganancia (null si no hay ninguna temporada)
    mejor: str | None = None
    peor: str | None = None
    # Días con movimientos que no caen en ninguna temporada
    dias_sin_temporada: int = 0
    # Inicio que se propone para la próxima (día siguiente al último cierre)
    proximo_inicio: date | None = None
