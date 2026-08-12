import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead

# LA PLATA QUE SE ESCRIBE EN ESTE MÓDULO, con la forma EXACTA de la columna que la
# va a guardar: `pagos_liquidacion.valor` y `anticipos.valor` son Numeric(14, 2).
#
# ES EL MOLDE DE transportadores/schemas.py::tarifa_por_litro, y está acá porque
# faltaba: con un `Field(gt=0)` pelado el schema aceptaba cifras que la columna no
# guarda, y eso no da un error, da una cifra distinta y callada.
#
#   · UN PAGO PARCIAL de $100.000,005 sobre un neto de $1.080.000,00 respondía
#     pagado = $100.000,005 y saldo = $979.999,995. En Postgres las dos columnas
#     suben el medio centavo POR SEPARADO: pagado + saldo da $1.080.000,01 contra
#     un neto de $1.080.000,00, y se rompe la igualdad que el propio modelo promete
#     ("se cumple exacto: neto_a_pagar = pagado + saldo"). El comprobante imprime
#     VALOR TOTAL, Pagado y SALDO A PAGAR y el dueño resta a mano: ahí el centavo
#     se ve. SQLite lo tapaba pasando por float, y por eso la suite no lo delató;
#   · UN PAGO DE $0,001 pasaba el `gt=0`, se guardaba en $0,00 y dejaba la
#     liquidación TRABADA: el estado quedaba en 'parcial' —así que recalcular la
#     rebota— pero `tiene_pagos` decía False, porque $0,00 no es mayor que cero, y
#     entonces el candado no la veía pagada. Nadie podía destrabarla. Con el tercer
#     decimal rechazado, ese pago ya no entra;
#   · UN ANTICIPO de 1E+20 entraba también, y en Postgres el INSERT reventaba con
#     un 22003 (numeric field overflow): un 500 en la cara del usuario.
#
# ACÁ SE RECHAZA Y NO SE REDONDEA, al contrario que en los litros de la recepción:
# un pesaje con tres decimales es un dato real que hay que llevar a dos, pero
# "$100.000,005" de plata entregada es un dato equivocado, y redondearlo en
# silencio cambiaría la cifra de un pago sin que quien lo registró se enterara.
def plata(**extra: Any) -> Any:
    """El `Field` de una columna de plata Numeric(14, 2). Una sola definición para
    todas: copiarla es como terminan aceptando cosas distintas."""
    return Field(max_digits=14, decimal_places=2, **extra)


class GenerarLiquidaciones(BaseSchema):
    periodo_inicio: date
    periodo_fin: date
    tipo: Literal["proveedor", "transportador", "ambos"] = "ambos"
    proveedor_id: uuid.UUID | None = None


# LOS MOTIVOS POR LOS QUE UN TERCERO SE SALTA EN LA CORRIDA, en código además del
# texto. El `motivo` ya viene redactado para mostrarlo tal cual, pero la pantalla
# necesita poder AGRUPAR y ponerle su propio icono o su propio botón a cada caso
# —"arréglele la tarifa" no se resuelve igual que "ajuste las fechas"— y hacerlo
# buscando palabras dentro de una frase en español es lo que se rompe el día que
# alguien le corrija una tilde al mensaje.
MOTIVO_PERIODO_CRUZADO = "periodo_cruzado"
MOTIVO_FLETE_SIN_TARIFA = "flete_sin_tarifa"


class LiquidacionOmitida(BaseSchema):
    """UN TERCERO QUE SE QUEDÓ SIN COMPROBANTE EN ESTA CORRIDA, Y POR QUÉ.

    Existe porque "Generar" es un BOTÓN DE BARRIDA que recorre a todos los terceros
    del período, y saltarse a uno en silencio es peor que el error: el dueño cierra la
    pantalla creyendo que ya liquidó a todo el mundo, y la leche de ese proveedor se
    queda sin papel y sin cifra hasta que alguien reclame. Con esto la corrida sale
    completa para los demás Y LO DICE.

    Cada campo está para algo que la pantalla tiene que poder hacer:
      · `tercero_nombre` es lo que el dueño lee ("Henri C"), y `tercero_id` es con lo
        que la pantalla puede llevarlo a ese tercero o volver a generarle SOLO a él;
      · `cuenta` es "leche" o "flete", el mismo par de palabras con que el candado de
        Recepción diaria nombra las dos liquidaciones: el dueño no dice "tipo
        proveedor". `tipo` va al lado con el valor técnico ('proveedor'/
        'transportador') para el que necesite filtrar;
      · `motivo` viene REDACTADO Y COMPLETO, con nombres, cifras y fechas, y con la
        salida que tiene el dueño. Se muestra tal cual: es el mismo texto que antes
        salía como error;
      · `motivo_codigo` es ese mismo motivo en código (ver arriba).
    """

    tipo: str
    cuenta: str
    tercero_id: uuid.UUID
    tercero_nombre: str
    motivo: str
    motivo_codigo: str


class PrevisualizarLiquidacion(BaseSchema):
    """Pre-liquidación: calcula cómo va un tercero SIN generar ni guardar nada."""

    periodo_inicio: date
    periodo_fin: date
    tipo: Literal["proveedor", "transportador"] = "proveedor"
    tercero_id: uuid.UUID


class PreLiquidacionDetalle(BaseSchema):
    fecha: date
    # La ruta del renglón: solo la traen los renglones del flete, donde el renglón
    # es (día, ruta) y un mismo día puede venir dos veces con tarifas distintas. En
    # los del proveedor viaja en nulo. El nombre va al lado del id para que la
    # pantalla no tenga que pedir el catálogo de rutas aparte.
    ruta_id: uuid.UUID | None = None
    ruta_nombre: str | None = None
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
    # LO QUE EL TERCERO QUEDÓ DEBIENDO DE QUINCENAS ANTERIORES Y QUE ESTE AVANCE
    # TODAVÍA NO DESCUENTA. En POSITIVO, y cero en la inmensa mayoría de los avances.
    #
    # POR QUÉ ESTÁ ACÁ: el papel del avance ya lo advertía —"este avance TODAVÍA NO
    # DESCUENTA lo que Henri C quedó debiendo ($120.000)"— y la pantalla no, así que
    # la pantalla decía "saldo $250.000" y el papel del mismo avance decía que lo que
    # va a salir de la caja son $130.000. Dos cifras para el mismo hecho, y el dueño
    # manda el papel mirando la pantalla.
    #
    # EL SENTIDO ES EXACTAMENTE EL DEL PAPEL, y por eso sale de la misma consulta
    # (`LiquidacionRepository.deuda_pendiente_de`): NO está restada en `saldo`. El
    # avance sigue sin descontarla a propósito —este documento no marca ni aparta
    # nada, la deuda se cobra en el momento de generar— así que la pantalla tiene que
    # mostrarla como un AVISO aparte y no metida en la resta:
    #     saldo (lo que dice arriba) − deuda_pendiente = lo que va a salir de verdad
    # Si queda por debajo de cero, al tercero le va a seguir quedando debiendo.
    deuda_pendiente: Decimal = Decimal("0")
    detalles: list[PreLiquidacionDetalle] = []
    anticipos_detalle: list[PreLiquidacionAnticipo] = []


class LiquidacionDetalleRead(BaseSchema):
    # El id viaja al frontend porque el día es editable: sin él, la pantalla
    # tendría que señalar la fila por fecha y dos días iguales (o un cambio de
    # orden) apuntarían al renglón equivocado.
    id: uuid.UUID
    fecha: date
    # Ver PreLiquidacionDetalle: en el comprobante del transportador el renglón es
    # (día, ruta) y estos dos campos son lo que distingue los dos renglones de un
    # día en que hizo las dos rutas. En el del proveedor van en nulo.
    ruta_id: uuid.UUID | None = None
    ruta_nombre: str | None = None
    litros: Decimal
    precio_litro: Decimal
    valor: Decimal


class PagoLiquidacionRead(BaseSchema):
    id: uuid.UUID
    fecha: date
    valor: Decimal
    destinatario: str | None = None
    observaciones: str | None = None


class PagoLiquidacionCreate(BaseSchema):
    """Un pago parcial contra una liquidación aprobada."""

    fecha: date
    valor: Decimal = plata(gt=0)
    destinatario: str | None = Field(default=None, max_length=150)
    observaciones: str | None = None


class LiquidacionReferencia(BaseSchema):
    """La OTRA liquidación, nombrada con lo mínimo para que una persona la ubique.

    Se usa en las dos puntas de la deuda trasladada. Va aparte de `LiquidacionRead`
    —y no anidada como una liquidación completa— por dos razones: una liquidación
    dentro de otra se llamaría a sí misma sin fin, y lo único que la pantalla
    necesita para decir "se le cobró en la del 16/06/2026 al 30/06/2026" es el
    período y el id para poder abrirla.
    """

    id: uuid.UUID
    periodo_inicio: date
    periodo_fin: date
    # "16/06/2026 al 30/06/2026", ya armado por el backend: si cada pantalla lo
    # formatea, alguna va a mostrar 2026-06-16 y el dueño no lee fechas así.
    periodo_texto: str


class DeudaCobradaRead(LiquidacionReferencia):
    """Una de las liquidaciones cuya deuda se cobró en esta, con su cifra.

    LA SUMA DE ESTOS `le_queda_debiendo` DA EXACTO `saldo_anterior`. Es el desglose
    del renglón "Lo que quedó debiendo de la quincena pasada": el dueño revisa a mano
    y todo desglose tiene que sumar la cifra grande, al centavo.
    """

    le_queda_debiendo: Decimal


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
    # LO QUE EL TERCERO QUEDÓ DEBIENDO DE UNA QUINCENA PASADA Y SE LE COBRA EN ESTA.
    # Es un descuento del neto, igual que los anticipos, y en el comprobante va en su
    # propio renglón ("Lo que quedó debiendo de la quincena pasada"). Cero en la
    # inmensa mayoría de las liquidaciones. El desglose está en `deudas_cobradas`.
    saldo_anterior: Decimal
    # La cifra grande contra la que se abona:
    #   neto_a_pagar = valor_total - anticipos - saldo_anterior.
    # Viaja calculada desde el modelo para que la pantalla no tenga que repetir la
    # resta y arriesgarse a mostrar una cifra distinta a la del comprobante.
    neto_a_pagar: Decimal
    pagado: Decimal
    # Lo que TODAVÍA se debe. Se cumple exacto: neto_a_pagar = pagado + saldo.
    saldo: Decimal
    # Y LA VUELTA DEL SALDO CUANDO QUEDA POR DEBAJO DE CERO: cuánto le quedó
    # debiendo el tercero al negocio, en POSITIVO (cero cuando no debe nada). Con
    # esto la pantalla puede decir "Henri le queda debiendo $4.955,77" en vez de
    # mostrar un "saldo -$4.955,77" bajo el rótulo "Saldo a pagar", que se lee al
    # revés. El porqué completo está en `Liquidacion.le_queda_debiendo`.
    le_queda_debiendo: Decimal
    # LAS DOS PUNTAS DE LA DEUDA, y las dos se ven en la pantalla:
    #
    # · en la liquidación que DEJÓ la deuda, en cuál se le cobró. Mientras esto no
    #   sea nulo, sus cifras están congeladas: anularla o recalcularla rebota, porque
    #   cambiarle el total le cambiaría el descuento a un comprobante ya emitido;
    # · en la que la COBRÓ, de dónde vino cada peso de `saldo_anterior`. Sin esto el
    #   dueño ve un descuento y no sabe de dónde salió.
    deuda_trasladada_a_id: uuid.UUID | None = None
    deuda_trasladada_a: LiquidacionReferencia | None = None
    deudas_cobradas: list[DeudaCobradaRead] = []
    observaciones: str | None
    detalles: list[LiquidacionDetalleRead] = []
    pagos: list[PagoLiquidacionRead] = []


class GenerarLiquidacionesResultado(BaseSchema):
    """LO QUE CONTESTA "Generar": las que salieron Y las que se saltaron.

    ANTES ESTO ERA UNA LISTA PELADA de liquidaciones, y por eso no había dónde decir
    lo otro. La forma nueva tiene dos campos y ninguno se puede leer sin el otro:
    `generadas` es lo que el dueño ya puede imprimir, y `omitidas` es lo que le falta.
    Una corrida puede salir con las dos llenas —es el caso normal del hallazgo que
    arregló esto: siete proveedores con su comprobante y uno saltado por un cruce—, y
    también con `generadas` vacía y `omitidas` con uno solo.

    Vacías las dos es lo de siempre y no es un error: nadie entregó leche en ese
    período, o ya estaba todo liquidado.
    """

    generadas: list[LiquidacionRead] = []
    omitidas: list[LiquidacionOmitida] = []


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
    valor: Decimal = plata(gt=0)
    observaciones: str | None = None


class AnticipoUpdate(BaseSchema):
    fecha: date | None = None
    valor: Decimal | None = plata(default=None, gt=0)
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
