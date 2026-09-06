import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class CategoriaGastoCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=100)
    descripcion: str | None = None


class CategoriaGastoUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    descripcion: str | None = None
    estado: str | None = None


class CategoriaGastoRead(TenantRead):
    nombre: str
    descripcion: str | None


class GastoCreate(BaseSchema):
    fecha: date
    categoria_id: uuid.UUID
    concepto: str = Field(min_length=2, max_length=200)
    proveedor: str | None = None
    # Opcional (ej. flete por kilo): si vienen ambos, valor = cantidad * precio.
    cantidad: Decimal | None = Field(default=None, ge=0)
    precio_unitario: Decimal | None = Field(default=None, ge=0)
    valor: Decimal = Field(gt=0)
    numero_factura: str | None = None
    observaciones: str | None = None
    sucursal_id: uuid.UUID | None = None


class GastoUpdate(BaseSchema):
    fecha: date | None = None
    categoria_id: uuid.UUID | None = None
    concepto: str | None = Field(default=None, min_length=2, max_length=200)
    proveedor: str | None = None
    cantidad: Decimal | None = Field(default=None, ge=0)
    precio_unitario: Decimal | None = Field(default=None, ge=0)
    valor: Decimal | None = Field(default=None, gt=0)
    numero_factura: str | None = None
    observaciones: str | None = None
    sucursal_id: uuid.UUID | None = None
    estado: str | None = None


class GastoRead(TenantRead):
    fecha: date
    categoria_id: uuid.UUID
    categoria_nombre: str | None = None
    concepto: str
    proveedor: str | None
    cantidad: Decimal | None
    precio_unitario: Decimal | None
    valor: Decimal
    numero_factura: str | None
    observaciones: str | None
    # La factura VIEJA, la que quedó en la carpeta `uploads/`. Sigue saliendo para
    # que la pantalla pueda mostrar las que el dueño ya había subido; las nuevas
    # van en `adjuntos_count` (ver AdjuntoGasto).
    adjunto_url: str | None
    # Cuántas facturas tiene colgadas en el bucket. Va en el listado para que el
    # clip de la grilla salga con el número sin tener que abrir gasto por gasto.
    adjuntos_count: int = 0
    sucursal_id: uuid.UUID | None


# --------------------------------------------------------------------- facturas
# LAS TRES FORMAS DE ABAJO TIENEN QUE DECIR LO MISMO QUE LAS DE LIQUIDACIONES Y
# REVENTA (`AdjuntoPagoRead`, `AdjuntosPagoLista`, `EnlaceSoporteCompartido`),
# porque en el frontend LAS TRES LAS PINTA LA MISMA PANTALLA (`soportes.dialog.ts`,
# que las recibe por `SoportesOrigen`). Están repetidas y no compartidas siguiendo
# lo que ya hacía el proyecto —cada módulo con las suyas—; si algún día se unifican,
# tienen que unificarse las tres a la vez, porque el diálogo no sabe de cuál módulo
# vienen los datos que está mostrando.


class AdjuntoGastoRead(BaseSchema):
    """Una factura del gasto, con un enlace TEMPORAL para verla.

    `url` NO está guardada en ninguna parte: se firma cada vez que se pide esta
    lista y se muere sola a los pocos minutos. Por eso viene siempre acompañada de
    `url_expira`: si la pantalla se queda abierta media hora, los enlaces que tiene
    en memoria ya no sirven y hay que volver a pedir la lista.

    Es `None` cuando el almacenamiento no está configurado: en ese caso la fila
    igual se muestra (nombre, tamaño, quién la subió) pero sin poder abrirla.
    """

    id: uuid.UUID
    gasto_id: uuid.UUID
    nombre_archivo: str
    content_type: str
    tamano_bytes: int
    es_imagen: bool
    subido_por_nombre: str | None
    created_at: datetime
    url: str | None = None
    url_expira: datetime | None = None


class AdjuntosGastoLista(BaseSchema):
    """Las facturas de UN gasto.

    `disponible` en false significa que el almacenamiento no está configurado en
    este servidor. Se responde 200 con el aviso y no un error, porque no es una
    falla de quien pregunta y el resto de la pantalla tiene que seguir usable.
    """

    disponible: bool
    mensaje: str | None = None
    # Cuántas facturas más caben (el tope por documento menos las que ya hay)
    cupo_restante: int = 0
    adjuntos: list[AdjuntoGastoRead] = []


class EnlaceFacturaCompartida(BaseSchema):
    """Enlace de MÁS duración para mandar UNA factura por fuera (WhatsApp, correo).

    `expira_texto` viene armado desde el backend, en hora de Colombia y en
    cristiano ("hasta el martes 5 de agosto a las 3:00 p. m."), porque quien
    reparte una factura tiene que saber hasta cuándo sirve lo que está repartiendo.
    Si la frase la armara cada pantalla, tarde o temprano una la mostraría en UTC
    —cinco horas corridas— o no la mostraría.
    """

    url: str
    nombre_archivo: str
    expira: datetime
    expira_texto: str
    dias: int
