import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from app.common.schemas import BaseSchema, TenantRead, a_dos_decimales

# LOS CUATRO CAMPOS DE ENTRADA, CON LA FORMA EXACTA DE SU COLUMNA.
#
# El redondeo a dos decimales lo hace `a_dos_decimales` (ver su docstring: es lo
# que hace que lo validado, lo guardado y lo calculado sean el mismo número en
# SQLite y en Postgres). Lo que agregan `max_digits`/`decimal_places` es EL TECHO,
# que faltaba y era un 500:
#
#   · `cantidad_litros = "1e20"` entraba con un 201 y en Postgres el INSERT moría
#     con 22003 (numeric field overflow) — un 500 en la cara del usuario en vez de
#     un mensaje;
#   · y era además el único que podía atajar el redondeo cuando `quantize` se
#     rendía: `bonificaciones = 1E+30` se guardaba CRUDA, sin redondear, porque no
#     había ningún constraint que la rechazara.
#
# `max_digits` es el ancho de la columna escrito en el schema, igual que en
# transportadores/schemas.py::tarifa_por_litro, que es de donde sale el molde:
# hasta $9.999.999.999,99 en las Numeric(12,2) y ni un tercer decimal.
# `decimal_places=2` es redundante mientras el redondeo funcione, y está a
# propósito: si algún día el redondeo se rinde otra vez, esto lo delata con un 422
# en vez de dejar pasar la cifra que la columna no guarda.
#
# ------------------------------------------------------------------------------
# Y OJO CON EL ORDEN DE ESTOS DOS: `Field(...)` VA ANTES DEL `BeforeValidator`.
#
# No es estilo, es la mitad del techo. Pydantic aplica la metadata de un Annotated
# de izquierda a derecha, así que con el redondeo primero las restricciones caían
# sobre la función que envuelve al decimal y no sobre el decimal, y por ese camino
# pydantic revisa MENOS: mira el total de dígitos y los decimales, pero NO cuántos
# dígitos quedan ANTES del punto. Medido, con `max_digits=12, decimal_places=2`:
#
#   · con el redondeo primero, `cantidad_litros = "10000000000"` entraba con un 201.
#     Son once dígitos enteros en una Numeric(12,2), que solo guarda diez, así que en
#     Postgres el INSERT moría con 22003 (numeric field overflow) — el 500 en la cara
#     del usuario que este techo venía justamente a evitar. Pasaba porque pydantic
#     cuenta los dígitos sobre la cifra normalizada: 10000000000,00 se le vuelve
#     1E+10, o sea "once dígitos", y once cabe en doce;
#   · con el `Field` primero, la misma cifra rebota con un 422 que dice la verdad:
#     "no more than 10 digits before the decimal point".
#
# El redondeo SIGUE CORRIENDO ANTES que la validación —un BeforeValidator envuelve al
# decimal, y el decimal es el que lleva las restricciones—, así que 44,235 L sigue
# entrando como 44,24 y nada de lo que el usuario escribe de verdad cambió. Lo único
# que cambia es que ahora el techo se cumple completo, y eso es lo que hace honesto el
# `return numero` de `a_dos_decimales` cuando la cifra es tan grande que no hay nada
# que redondear: ahí de verdad la para el campo, no se queda apostando a que alguien
# más la rechace.
# ------------------------------------------------------------------------------

# Los litros del día: recepciones.cantidad_litros es Numeric(12, 2).
Litros = Annotated[
    Decimal, Field(max_digits=12, decimal_places=2), BeforeValidator(a_dos_decimales)
]
# El precio por litro: recepciones.precio_litro es Numeric(12, 2), la misma forma
# que la tarifa del transportador. Va aparte de `Plata` porque su columna es más
# angosta que la de los ajustes, y con el ancho de la otra el techo no serviría.
PrecioLitro = Annotated[
    Decimal, Field(max_digits=12, decimal_places=2), BeforeValidator(a_dos_decimales)
]
# La plata de los ajustes del día (bonificaciones, descuentos): Numeric(14, 2).
Plata = Annotated[
    Decimal, Field(max_digits=14, decimal_places=2), BeforeValidator(a_dos_decimales)
]

# EL TOPE DEL PRECIO POR LITRO, en pesos. No es capricho ni es nuevo: es el mismo
# que `LiquidacionDetallePrecioUpdate` ya declara para ESTA MISMA COLUMNA cuando el
# precio del día se corrige desde el comprobante, y el motivo que allá está escrito
# vale igual acá —"el precio del litro anda por los $1.800 y quien teclea 1800000
# por error se lleva una liquidación de cientos de millones"—. Dos reglas para la
# misma columna es como terminan diciendo cosas distintas: el mismo dato entraba
# por esta puerta sin tope y por la otra con tope.
#
# Va como entero a propósito: el manejador de errores de validación serializa el
# contexto del error a JSON tal cual, y un Decimal ahí revienta la respuesta con un
# 500 en vez de devolver el 422.
TOPE_PRECIO_LITRO = 1_000_000


class RecepcionCreate(BaseSchema):
    fecha: date
    proveedor_id: uuid.UUID
    transportador_id: uuid.UUID | None = None
    ruta_id: uuid.UUID | None = None
    sucursal_id: uuid.UUID | None = None
    cantidad_litros: Litros = Field(gt=0)
    precio_litro: PrecioLitro | None = Field(
        default=None,
        ge=0,
        le=TOPE_PRECIO_LITRO,
        description="Si no se envía, usa el precio del proveedor",
    )
    bonificaciones: Plata = Field(default=Decimal("0"), ge=0)
    descuentos: Plata = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None


class RecepcionUpdate(BaseSchema):
    fecha: date | None = None
    transportador_id: uuid.UUID | None = None
    ruta_id: uuid.UUID | None = None
    sucursal_id: uuid.UUID | None = None
    cantidad_litros: Litros | None = Field(default=None, gt=0)
    precio_litro: PrecioLitro | None = Field(default=None, ge=0, le=TOPE_PRECIO_LITRO)
    bonificaciones: Plata | None = Field(default=None, ge=0)
    descuentos: Plata | None = Field(default=None, ge=0)
    observaciones: str | None = None
    # SOLO estos dos valores, y ahora importa más que antes: apagar un día lo saca de
    # los dos comprobantes (al transportador y al productor) y del costo de
    # contabilidad. Con un `str` pelado, un "Activo" con mayúscula —o texto basura por
    # la dirección del endpoint— dejaba el día en un estado que no es 'activo', o sea
    # APAGADO, y sin que nadie lo dijera: la leche desaparecía de la liquidación y el
    # dueño solo lo iba a notar cuadrando a mano contra el cuaderno. Es el mismo hueco
    # que en proveedores se cerró quitando el campo; acá el campo tiene que quedarse,
    # porque prender y apagar el día es justo lo que el usuario hace desde esta
    # pantalla, así que lo que se cierra es el JUEGO DE VALORES.
    estado: Literal["activo", "inactivo"] | None = None


class RecepcionRead(TenantRead):
    fecha: date
    proveedor_id: uuid.UUID
    proveedor_nombre: str | None = None
    transportador_id: uuid.UUID | None
    ruta_id: uuid.UUID | None
    sucursal_id: uuid.UUID | None
    cantidad_litros: Decimal
    precio_litro: Decimal
    bonificaciones: Decimal
    descuentos: Decimal
    valor_bruto: Decimal
    valor_transporte: Decimal
    valor_neto: Decimal
    observaciones: str | None
    liquidacion_id: uuid.UUID | None
    liquidacion_transporte_id: uuid.UUID | None = None
    # Estado de la liquidación que manda sobre este día ('borrador', 'aprobada',
    # 'parcial', 'pagada') o null si todavía no está en ninguna. Bloquean las que
    # ya tienen pagos ('parcial' y 'pagada'); en borrador y aprobada se puede
    # editar y la liquidación se recuadra sola.
    liquidacion_estado: str | None = None

    # ------------------------------------------------------ el candado por campo
    # Un día vive en DOS liquidaciones de dos personas distintas: la leche al
    # proveedor y el flete al transportador. Estos dos campos las separan, porque
    # `liquidacion_estado` (que es el estado de la más trabada) no alcanza: con la
    # leche pagada y el flete sin liquidar decía 'pagada', y la pantalla trababa
    # todo cuando el transportador sí se podía corregir.
    liquidacion_estado_leche: str | None = None
    liquidacion_estado_flete: str | None = None
    leche_pagada: bool = False
    flete_pagado: bool = False
    # El candado ya resuelto por el backend, que es el que manda. La pantalla
    # apaga los `campos_bloqueados` y deja escribir en los `campos_editables`, sin
    # tener que repetir aquí la regla de a quién le mueve la plata cada campo: si
    # se repitiera, mañana las dos versiones dirían cosas distintas.
    campos_bloqueados: list[str] = []
    campos_editables: list[str] = []
    # La explicación en español para el usuario ("la leche de este día ya se le
    # pagó a Patricia Laguna: … sí se puede corregir el transportador, porque su
    # flete todavía no se ha liquidado"). Null cuando no hay nada trabado.
    candado_aviso: str | None = None


class ResumenDia(BaseSchema):
    fecha: date
    total_litros: Decimal
    valor_bruto: Decimal
    valor_transporte: Decimal
    valor_neto: Decimal
    recepciones: int


class ResumenPeriodo(BaseSchema):
    desde: date
    hasta: date
    total_litros: Decimal
    valor_bruto: Decimal
    valor_transporte: Decimal
    valor_neto: Decimal
    precio_promedio: Decimal
    dias: list[ResumenDia]


# ------------------------------------------------------------ grilla quincena
class CeldaGrilla(BaseSchema):
    """Una recepción vista como celda proveedor × día de la grilla."""

    recepcion_id: uuid.UUID
    litros: Decimal
    # El día ya está dentro de una liquidación generada (la de la leche o la del
    # flete), sin importar el estado. Es una SEÑA para avisar que al tocarlo se
    # va a mover una liquidación ya emitida, no un candado.
    liquidada: bool
    # Alguna de esas liquidaciones ya tiene pagos, sea 'pagada' o 'parcial'. Basta
    # un abono: esa plata ya salió contra este día. Ya NO significa "no editable":
    # significa "este día tiene campos trabados" y por eso lleva el candado.
    pagada: bool = False
    # Cuál de las dos platas fue, que es lo que hace honesto el tooltip. Con la
    # leche pagada y el flete sin liquidar el día se sigue pudiendo corregir —el
    # transportador, la ruta, las observaciones—, así que la celda ya no puede
    # decir "Pagada — no editable" ni negar el clic.
    leche_pagada: bool = False
    flete_pagado: bool = False
    # 'borrador' | 'aprobada' | 'parcial' | 'pagada' | None, para explicar en
    # pantalla qué pasa si se edita el día.
    liquidacion_estado: str | None = None
    # True si la recepción tiene transportador asignado (se marca con un ícono).
    con_transporte: bool = False


class FilaGrilla(BaseSchema):
    proveedor_id: uuid.UUID
    proveedor_nombre: str
    vereda: str | None
    precio_litro: Decimal
    # False si el proveedor fue retirado/eliminado pero aún tiene recepciones
    # en el período (se conserva en la grilla para poder liquidarlo).
    proveedor_activo: bool = True
    celdas: dict[str, CeldaGrilla]  # clave: fecha ISO 'YYYY-MM-DD'
    total_litros: Decimal
    valor_bruto: Decimal
    descuentos: Decimal
    bonificaciones: Decimal
    valor_neto: Decimal
    valor_transporte: Decimal


class GrillaQuincena(BaseSchema):
    """Vista proveedores × días, equivalente a la hoja 'LITROS Y TRANSPORTE'."""

    desde: date
    hasta: date
    fechas: list[date]
    filas: list[FilaGrilla]
    totales_dia: dict[str, Decimal]
    total_litros: Decimal
    total_valor_neto: Decimal
    total_transporte: Decimal
