import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

from pydantic import BaseModel, ConfigDict

DOS_DECIMALES = Decimal("0.01")

# HASTA DÓNDE SE REDONDEA. Ninguna columna del sistema pasa de Numeric(14, 2)
# —doce dígitos enteros—, así que cuarenta es más del triple y por encima de eso no
# hay nada sensato que redondear: la cifra se devuelve tal cual y la para el techo
# del campo (`max_digits`) con un 422.
#
# El tope es además una defensa, y por eso no se estira "lo que haga falta": sin
# él, un "1E+999999999" en el JSON haría que `quantize` materializara mil millones
# de dígitos. Con esto el costo del redondeo está acotado y aun así cubre con
# muchísima holgura cualquier cifra que un teclado —o una integración— pueda mandar.
MAX_DIGITOS_ENTEROS = 40


def a_dos_decimales(valor: Any) -> Any:
    """Redondea a dos decimales EN LA ENTRADA: los mismos que caben en la columna.

    UNA SOLA COPIA de esta regla, a propósito. Vivía duplicada en
    `recepcion/schemas.py` y en `reventa/schemas.py`, y el defecto del `except`
    (ver abajo) estaba en las DOS: es lo que pasa cuando la misma regla se
    escribe dos veces. Los alias con la forma de cada columna (`Litros`, `Plata`,
    `Kilos`) siguen viviendo en su módulo, porque cada columna tiene su ancho.

    POR QUÉ SE REDONDEA ACÁ Y NO SE DEJA PARA LA BASE: las columnas de litros,
    kilos y plata son Numeric(_, 2). Si entran tres decimales, la columna guarda
    el número redondeado pero el servicio calcula la plata con el valor CRUDO, y
    la fila QUEDA CONTRADICIÉNDOSE SOLA:

      · 44,235 L en Nápoles a $242,76: la columna guarda 44,23 L y la foto del
        flete se calculó con 44,235 -> $10.738,49, cuando 44,23 × 242,76 =
        $10.737,27. Son $1,22 en UN día, y el dueño multiplica a mano;
      · con `precio_litro` es la plata del productor: $1.800,005 se guarda
        $1.800,01 y el renglón dice 137,45 × $1.800,01 pero el valor era
        $247.410,69 en vez de $247.411,37;
      · con los kilos de reventa: 10,005 kg a $1.000 se guarda "10,01 kg" y
        "$10.005", y el dueño ve que no le da.

    SQLite no delata nada de esto (se guarda los tres decimales tan tranquilo), y
    por eso la suite pasaba con el defecto puesto; Postgres sí redondea, en
    silencio. Redondear ACÁ hace que lo validado, lo guardado y lo calculado sean
    el MISMO número en los dos motores. El medio SUBE (0,005 -> 0,01), como en
    toda la plata del proyecto y como lo espera quien lee una báscula.

    Y OJO CON EL REDONDEO QUE SE RENDÍA, que era el defecto: `quantize` levanta
    InvalidOperation cuando el resultado no cabe en la precisión del contexto
    —28 dígitos por omisión—, y la versión anterior atrapaba eso y DEVOLVÍA EL
    VALOR CRUDO "confiando en que lo rechace Pydantic". Con `bonificaciones =
    1E+30` no había nada que lo rechazara: se guardaba tal cual, sin redondear.
    Acá la precisión va HOLGADA Y ACOTADA (`MAX_DIGITOS_ENTEROS`), así que toda
    cifra finita que quepa en una columna —y muchísimo más— se redondea de
    verdad; y a las absurdas las para el `max_digits` del campo con un 422, que
    es un mensaje y no un 500.

    Y AHÍ ESTÁ LA LETRA MENUDA, que hay que leer antes de escribir el próximo alias
    con esta función: lo de "las para el `max_digits` del campo" solo es cierto si el
    `Field(max_digits=..., decimal_places=...)` VA ANTES de este `BeforeValidator` en
    el `Annotated`. Al revés, pydantic pone las restricciones sobre la función que
    envuelve al decimal y deja de revisar cuántos dígitos van ANTES del punto: por ese
    camino, `10000000000` (once dígitos enteros) se colaba en una Numeric(12,2) y
    reventaba en Postgres con un 22003. El orden bueno está escrito, con las cifras
    medidas, en `recepcion/schemas.py`.
    """
    if valor is None or isinstance(valor, bool):
        return valor
    try:
        numero = Decimal(str(valor))
    except (ArithmeticError, TypeError, ValueError):
        # No es un número (texto libre, None raro): que lo rechace Pydantic con su
        # mensaje, no un error raro desde aquí.
        return valor
    if not numero.is_finite() or numero.adjusted() >= MAX_DIGITOS_ENTEROS:
        # Infinito, NaN o una cifra tan grande que no cabe ni de lejos en ninguna
        # columna: no hay nada que redondear. La rechaza el techo del campo.
        return numero
    with localcontext() as ctx:
        ctx.prec = MAX_DIGITOS_ENTEROS + 3
        return numero.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class AuditRead(BaseSchema):
    """Campos comunes que exponen todos los schemas de lectura."""

    id: uuid.UUID
    estado: str
    created_at: datetime
    updated_at: datetime


class TenantRead(AuditRead):
    empresa_id: uuid.UUID


class MessageResponse(BaseSchema):
    detail: str
