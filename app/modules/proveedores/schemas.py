import uuid
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead

# EL PRECIO POR LITRO QUE SE LE PAGA AL PRODUCTOR, con la forma EXACTA de la
# columna que lo va a guardar: `proveedores.precio_litro` es Numeric(12, 2).
#
# ES EL MOLDE DE transportadores/schemas.py::tarifa_por_litro, y está acá por el
# mismo defecto: con un `Field(ge=0)` pelado el schema aceptaba cifras que la
# columna no puede guardar, y eso no da un error: da una cifra distinta, callada.
#
#   · $1.800,005 entraba y el POST respondía $1.800,005, pero la columna guarda
#     $1.800,01 (Postgres redondea la escala en silencio). O sea que la pantalla
#     del proveedor mostraba un precio por litro que NO es el que se le va a pagar,
#     y al recargar la pantalla salía otra cifra. Y es plata del productor: este
#     precio es el que hereda la recepción del día;
#   · 1E+20 entraba también, y en Postgres el INSERT reventaba con un 22003
#     (numeric field overflow) — un 500 en la cara del usuario en vez de un mensaje.
#
# `max_digits=12, decimal_places=2` es la columna escrita en el schema: hasta
# $9.999.999.999,99 y ni un tercer decimal. ACÁ SE RECHAZA Y NO SE REDONDEA, al
# contrario que en los litros de la recepción: un peso mal tecleado en el precio no
# es un pesaje que haya que ajustar, es un dato equivocado, y redondearlo en
# silencio le guardaría al productor una tarifa que él no acordó.
#
# `le` es el tope de cordura, el mismo que `LiquidacionDetallePrecioUpdate` ya
# declara para el precio del litro y por el mismo motivo escrito allá: el litro
# anda por los $1.800 y quien teclea "1800000" por error se lleva una liquidación
# de cientos de millones. Va como entero porque el manejador de errores serializa
# el contexto del error a JSON y un Decimal ahí devuelve un 500 en vez del 422.
TOPE_PRECIO_LITRO = 1_000_000


def precio_por_litro(**extra: Any) -> Any:
    """El `Field` del precio por litro del proveedor. Una sola definición para el
    POST y el PUT: dos copias es como terminan aceptando cosas distintas."""
    return Field(ge=0, le=TOPE_PRECIO_LITRO, max_digits=12, decimal_places=2, **extra)


class ProveedorCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=150)
    documento: str | None = None
    vereda: str | None = None
    municipio: str | None = None
    telefono: str | None = None
    precio_litro: Decimal = precio_por_litro(default=Decimal("0"))
    ruta_id: uuid.UUID | None = None
    observaciones: str | None = None


class ProveedorUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    documento: str | None = None
    vereda: str | None = None
    municipio: str | None = None
    telefono: str | None = None
    precio_litro: Decimal | None = precio_por_litro(default=None)
    ruta_id: uuid.UUID | None = None
    observaciones: str | None = None
    # OJO: aquí NO va 'estado'. Antes sí estaba y era un hueco por dos lados:
    # · aceptaba cualquier texto ("retirado", "xyz"), y con eso el chip de la
    #   tabla y el filtro de Estado quedaban mostrando un estado que no existe.
    # · dejaba al proveedor en 'inactivo' SIN que nada lo hiciera cumplir: se le
    #   seguía pudiendo registrar leche como si nada.
    # Ahora el estado se cambia solo por /desactivar y /activar, que sí validan
    # el valor, dejan rastro en auditoría y son los que la recepción respeta.


class ProveedorRead(TenantRead):
    nombre: str
    documento: str | None
    vereda: str | None
    municipio: str | None
    telefono: str | None
    precio_litro: Decimal
    ruta_id: uuid.UUID | None
    observaciones: str | None
