import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class VentaDetalleCreate(BaseSchema):
    producto_id: uuid.UUID
    descripcion: str | None = None
    cantidad: Decimal = Field(gt=0, decimal_places=2)
    precio_unitario: Decimal = Field(ge=0, decimal_places=2)


class VentaTramoFleteCreate(BaseSchema):
    """Un tramo del recorrido: de dónde a dónde, cuánto por kilo y quién maneja.

    `destino` es obligatorio (a diferencia de la columna, que lo admite vacío
    para los fletes viejos que se migraron): un tramo nuevo sin destino no se
    puede leer ni sumar a la ruta. `origen` es opcional porque el primer tramo
    casi siempre sale de la planta.
    """

    origen: str | None = Field(default=None, max_length=120)
    destino: str = Field(min_length=1, max_length=120)
    # Texto libre a propósito: el dueño no registra conductores antes de
    # despachar. El servicio lo canoniza contra los nombres ya usados, así que
    # "JOSE LAVADO" y "Jose lavado" terminan siendo el mismo señor.
    conductor: str | None = Field(default=None, max_length=150)
    valor_por_kilo: Decimal = Field(default=Decimal("0"), ge=0)


class VentaCreate(BaseSchema):
    tipo: Literal["factura", "remision"] = "factura"
    cliente_id: uuid.UUID
    fecha: date
    descuento: Decimal = Field(default=Decimal("0"), ge=0)
    # Lo que cuesta LLEVAR el despacho (el flete a Bogotá o a donde sea). NO se le
    # suma al total que paga el cliente: es un costo de la quesera, y es lo que hace
    # que el kilo puesto en destino valga más que el kilo en la planta.
    #
    # El flete va por TRAMOS: "de la quesera a San Vicente 400 y de San Vicente a
    # Bogotá 600". `gasto_concepto` y `gasto_por_kilo` se siguen aceptando para no
    # romper a quien ya llamaba así: equivalen a mandar UN tramo. Si vienen los
    # dos, mandan los tramos.
    tramos: list[VentaTramoFleteCreate] | None = None
    gasto_concepto: str | None = Field(default=None, max_length=150)
    gasto_por_kilo: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None
    detalles: list[VentaDetalleCreate] = Field(min_length=1)
    descontar_inventario: bool = True


class VentaUpdate(BaseSchema):
    tipo: Literal["factura", "remision"] | None = None
    cliente_id: uuid.UUID | None = None
    fecha: date | None = None
    descuento: Decimal | None = Field(default=None, ge=0)
    # Si viene, REEMPLAZA todos los tramos del despacho. Una lista vacía deja la
    # venta sin flete (se lo recogieron en la planta).
    tramos: list[VentaTramoFleteCreate] | None = None
    gasto_concepto: str | None = Field(default=None, max_length=150)
    gasto_por_kilo: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = None
    # Si viene, reemplaza las líneas de la venta (recalcula totales e inventario).
    detalles: list[VentaDetalleCreate] | None = Field(default=None, min_length=1)


class VentaDetalleRead(BaseSchema):
    producto_id: uuid.UUID
    descripcion: str | None
    cantidad: Decimal
    precio_unitario: Decimal
    total: Decimal


class VentaTramoFleteRead(BaseSchema):
    id: uuid.UUID
    orden: int
    origen: str | None
    destino: str | None
    conductor: str | None
    valor_por_kilo: Decimal
    valor_total: Decimal


class VentaRead(TenantRead):
    numero: int
    tipo: str
    cliente_id: uuid.UUID
    cliente_nombre: str | None = None
    fecha: date
    subtotal: Decimal
    descuento: Decimal
    total: Decimal
    pagado: Decimal
    saldo: Decimal
    observaciones: str | None
    # El flete del despacho, que NO está incluido en `total`. Estos tres campos son
    # el RESUMEN de `tramos_flete`: la ruta en cristiano, la suma de lo que cobra
    # cada tramo por kilo, y la suma de los totales de los tramos. Los tres los
    # calcula el servicio; los `tramos_flete` son el dato original.
    gasto_concepto: str | None = None
    gasto_por_kilo: Decimal = Decimal("0")
    gasto_monto: Decimal = Decimal("0")
    tramos_flete: list[VentaTramoFleteRead] = []
    detalles: list[VentaDetalleRead] = []


class PagoCreate(BaseSchema):
    venta_id: uuid.UUID
    fecha: date
    valor: Decimal = Field(gt=0)
    metodo: Literal["efectivo", "transferencia", "otro"] = "efectivo"
    referencia: str | None = None
    observaciones: str | None = None


class PagoRead(TenantRead):
    venta_id: uuid.UUID
    fecha: date
    valor: Decimal
    metodo: str
    referencia: str | None
    observaciones: str | None


class CarteraCliente(BaseSchema):
    cliente_id: uuid.UUID
    cliente_nombre: str
    ventas_pendientes: int
    total_facturado: Decimal
    total_pagado: Decimal
    saldo: Decimal


# --------------------------------------------------- lo que se le debe a cada
#                                                      conductor de despachos
class PagoConductorCreate(BaseSchema):
    # El nombre va en el cuerpo y no en la ruta: se canoniza contra los que ya
    # existen, igual que al despachar, para que el pago le llegue al mismo señor
    # aunque se escriba distinto.
    conductor: str = Field(min_length=2, max_length=150)
    fecha: date
    valor: Decimal = Field(gt=0)
    observaciones: str | None = Field(default=None, max_length=300)


class PagoConductorRead(BaseSchema):
    id: uuid.UUID
    conductor: str
    fecha: date
    valor: Decimal
    observaciones: str | None


class ConductorTramoRead(BaseSchema):
    """Un viaje que se le acumuló al conductor: de qué despacho salió y cuánto."""

    venta_id: uuid.UUID
    venta_numero: int
    fecha: date
    cliente: str | None
    origen: str | None
    destino: str | None
    kilos: Decimal
    valor_por_kilo: Decimal
    valor: Decimal


class ConductorResumen(BaseSchema):
    conductor: str
    # Lo del PERÍODO que se está mirando: la suma de `tramos`, exacta.
    acumulado_periodo: Decimal
    pagado_periodo: Decimal
    # Lo de SIEMPRE, sin filtro de fechas. `saldo` es lo que de verdad se le debe
    # hoy: acotar la deuda al período haría que cambiar el filtro cambiara lo que
    # se le debe a una persona, que es justo lo que no puede pasar.
    total_acumulado: Decimal
    total_pagado: Decimal
    saldo: Decimal
    tramos: list[ConductorTramoRead] = []
    pagos: list[PagoConductorRead] = []


class ConductoresPanel(BaseSchema):
    desde: date | None
    hasta: date | None
    conductores: list[ConductorResumen] = []
    # Los totales van calculados aquí y no en la pantalla: son la cifra grande
    # contra la que el dueño cuadra las filas a mano, y tienen que salir de la
    # misma suma que las filas.
    total_acumulado_periodo: Decimal = Decimal("0")
    total_pagado_periodo: Decimal = Decimal("0")
    total_saldo: Decimal = Decimal("0")


class SugerenciasConductores(BaseSchema):
    conductores: list[str] = []
