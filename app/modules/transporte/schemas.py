import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead

Sentido = Literal["ida", "regreso"]
TipoCobro = Literal["por_kilo", "precio_fijo"]
CategoriaGastoVehiculo = Literal[
    "combustible",
    "peajes",
    "viaticos",
    "cargue_descargue",
    "lavada",
    "parqueadero",
    "multa",
    "otros",
]
TipoDocumento = Literal["soat", "tecnomecanica", "seguro", "impuesto", "otro"]
TipoMantenimiento = Literal["preventivo", "correctivo"]


# ------------------------------------------------------------------ vehículos
class VehiculoCreate(BaseSchema):
    placa: str = Field(min_length=3, max_length=10)
    nombre: str | None = Field(default=None, max_length=80)
    marca: str | None = Field(default=None, max_length=80)
    linea: str | None = Field(default=None, max_length=80)
    anio: int | None = Field(default=None, ge=1950, le=2100)
    capacidad_kg: Decimal | None = Field(default=None, gt=0)
    tarifa_kilo: Decimal = Field(default=Decimal("0"), ge=0)
    odometro_actual: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = Field(default=None, max_length=500)


class VehiculoUpdate(BaseSchema):
    placa: str | None = Field(default=None, min_length=3, max_length=10)
    nombre: str | None = Field(default=None, max_length=80)
    marca: str | None = Field(default=None, max_length=80)
    linea: str | None = Field(default=None, max_length=80)
    anio: int | None = Field(default=None, ge=1950, le=2100)
    capacidad_kg: Decimal | None = Field(default=None, gt=0)
    tarifa_kilo: Decimal | None = Field(default=None, ge=0)
    # La corrección MANUAL del odómetro va por aquí (el flujo normal solo sube)
    odometro_actual: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = Field(default=None, max_length=500)
    estado: str | None = None


class VehiculoRead(TenantRead):
    placa: str
    nombre: str | None
    marca: str | None
    linea: str | None
    anio: int | None
    capacidad_kg: Decimal | None
    tarifa_kilo: Decimal
    odometro_actual: Decimal
    observaciones: str | None


# --------------------------------------------------------------------- abonos
class AbonoFleteCreate(BaseSchema):
    fecha: date
    valor: Decimal = Field(gt=0)
    metodo: str = Field(default="efectivo", max_length=30)
    referencia: str | None = Field(default=None, max_length=100)
    observaciones: str | None = Field(default=None, max_length=300)


class AbonoFleteRead(BaseSchema):
    id: uuid.UUID
    fecha: date
    valor: Decimal
    metodo: str
    referencia: str | None
    observaciones: str | None


# ------------------------------------------------------------------ servicios
class ViajeServicioCreate(BaseSchema):
    sentido: Sentido = "ida"
    tipo_cobro: TipoCobro = "por_kilo"
    # Queso propio de la quesera: se valora a tarifa por kilo, sin cliente y
    # sin cartera (el servicio queda en estado "interno")
    es_interno: bool = False
    cliente_id: uuid.UUID | None = None
    cliente_nombre: str | None = Field(default=None, max_length=150)
    descripcion: str = Field(min_length=2, max_length=200)
    kilos: Decimal | None = Field(default=None, gt=0)
    # Sin tarifa se toma la base del vehículo
    tarifa_kilo: Decimal | None = Field(default=None, ge=0)
    # Solo para precio fijo (en por_kilo lo calcula el servicio)
    valor_total: Decimal | None = Field(default=None, gt=0)
    observaciones: str | None = Field(default=None, max_length=500)
    # Pago inmediato: crea el abono automático y deja el servicio pagado
    pagado_de_contado: bool = False


class ViajeServicioUpdate(BaseSchema):
    sentido: Sentido | None = None
    tipo_cobro: TipoCobro | None = None
    es_interno: bool | None = None
    cliente_id: uuid.UUID | None = None
    cliente_nombre: str | None = Field(default=None, max_length=150)
    descripcion: str | None = Field(default=None, min_length=2, max_length=200)
    kilos: Decimal | None = Field(default=None, gt=0)
    tarifa_kilo: Decimal | None = Field(default=None, ge=0)
    valor_total: Decimal | None = Field(default=None, gt=0)
    observaciones: str | None = Field(default=None, max_length=500)


class ViajeServicioRead(TenantRead):
    viaje_id: uuid.UUID
    sentido: str
    tipo_cobro: str
    es_interno: bool
    cliente_id: uuid.UUID | None
    cliente_nombre: str | None
    descripcion: str
    kilos: Decimal | None
    tarifa_kilo: Decimal | None
    valor_total: Decimal
    abonado: Decimal
    saldo: Decimal
    observaciones: str | None
    abonos: list[AbonoFleteRead] = []


# --------------------------------------------------------------------- viajes
class ViajeCreate(BaseSchema):
    vehiculo_id: uuid.UUID
    fecha_salida: date
    origen: str = Field(min_length=2, max_length=120)
    destino: str = Field(min_length=2, max_length=120)
    conductor_nombre: str | None = Field(default=None, max_length=150)
    pago_conductor: Decimal = Field(default=Decimal("0"), ge=0)
    odometro_salida: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = Field(default=None, max_length=500)


class ViajeUpdate(BaseSchema):
    vehiculo_id: uuid.UUID | None = None
    fecha_salida: date | None = None
    fecha_regreso: date | None = None
    origen: str | None = Field(default=None, min_length=2, max_length=120)
    destino: str | None = Field(default=None, min_length=2, max_length=120)
    conductor_nombre: str | None = Field(default=None, max_length=150)
    pago_conductor: Decimal | None = Field(default=None, ge=0)
    odometro_salida: Decimal | None = Field(default=None, ge=0)
    odometro_regreso: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = Field(default=None, max_length=500)


class ViajeFinalizar(BaseSchema):
    """Cerrar el viaje: sin fecha de regreso se toma hoy; el odómetro de
    regreso actualiza el del vehículo (solo sube)."""

    fecha_regreso: date | None = None
    odometro_regreso: Decimal | None = Field(default=None, ge=0)


class ViajeRead(TenantRead):
    numero: int
    vehiculo_id: uuid.UUID
    vehiculo_placa: str | None
    vehiculo_nombre: str | None
    fecha_salida: date
    fecha_regreso: date | None
    origen: str
    destino: str
    conductor_nombre: str | None
    pago_conductor: Decimal
    odometro_salida: Decimal | None
    odometro_regreso: Decimal | None
    observaciones: str | None
    # Totales calculados por el backend (evita N+1 en el frontend)
    total_ingresos: Decimal
    ingresos_terceros: Decimal
    ingresos_internos: Decimal
    total_gastos_viaje: Decimal
    utilidad: Decimal
    saldo_cartera: Decimal


# --------------------------------------------------------- gastos del vehículo
class ViajeGastoCreate(BaseSchema):
    """Atajo desde el detalle del viaje: el viaje y el vehículo los fija la ruta."""

    fecha: date
    categoria: CategoriaGastoVehiculo
    concepto: str | None = Field(default=None, max_length=200)
    valor: Decimal = Field(gt=0)
    odometro: Decimal | None = Field(default=None, ge=0)


class VehiculoGastoCreate(ViajeGastoCreate):
    vehiculo_id: uuid.UUID
    viaje_id: uuid.UUID | None = None  # nulo = gasto general del vehículo


class VehiculoGastoUpdate(BaseSchema):
    vehiculo_id: uuid.UUID | None = None
    viaje_id: uuid.UUID | None = None
    fecha: date | None = None
    categoria: CategoriaGastoVehiculo | None = None
    concepto: str | None = Field(default=None, max_length=200)
    valor: Decimal | None = Field(default=None, gt=0)
    odometro: Decimal | None = Field(default=None, ge=0)


class VehiculoGastoRead(TenantRead):
    vehiculo_id: uuid.UUID
    viaje_id: uuid.UUID | None
    fecha: date
    categoria: str
    concepto: str | None
    valor: Decimal
    odometro: Decimal | None
    adjunto_url: str | None


class ViajeDetalleRead(ViajeRead):
    """El detalle del viaje ES el reporte de rentabilidad: servicios, gastos y
    los totales de arriba en una sola respuesta."""

    servicios: list[ViajeServicioRead] = []
    # Se lee de `gastos_vigentes` (propiedad del modelo): los gastos eliminados
    # con soft delete siguen colgados de la relación y no deben salir aquí.
    gastos: list[VehiculoGastoRead] = Field(default=[], validation_alias="gastos_vigentes")


# ------------------------------------------------------------- mantenimientos
class MantenimientoCreate(BaseSchema):
    vehiculo_id: uuid.UUID
    fecha: date
    tipo: TipoMantenimiento = "preventivo"
    descripcion: str = Field(min_length=2, max_length=200)
    taller: str | None = Field(default=None, max_length=150)
    odometro: Decimal | None = Field(default=None, ge=0)
    valor: Decimal = Field(default=Decimal("0"), ge=0)
    proximo_odometro: Decimal | None = Field(default=None, ge=0)
    proxima_fecha: date | None = None


class MantenimientoUpdate(BaseSchema):
    vehiculo_id: uuid.UUID | None = None
    fecha: date | None = None
    tipo: TipoMantenimiento | None = None
    descripcion: str | None = Field(default=None, min_length=2, max_length=200)
    taller: str | None = Field(default=None, max_length=150)
    odometro: Decimal | None = Field(default=None, ge=0)
    valor: Decimal | None = Field(default=None, ge=0)
    proximo_odometro: Decimal | None = Field(default=None, ge=0)
    proxima_fecha: date | None = None


class MantenimientoRead(TenantRead):
    vehiculo_id: uuid.UUID
    fecha: date
    tipo: str
    descripcion: str
    taller: str | None
    odometro: Decimal | None
    valor: Decimal
    proximo_odometro: Decimal | None
    proxima_fecha: date | None
    adjunto_url: str | None


# ------------------------------------------------------------------ documentos
class DocumentoCreate(BaseSchema):
    """Renovar un documento es CREAR un registro nuevo: el histórico se conserva
    y las alertas solo miran el de vencimiento más reciente por (vehículo, tipo)."""

    vehiculo_id: uuid.UUID
    tipo: TipoDocumento
    descripcion: str | None = Field(default=None, max_length=200)
    numero: str | None = Field(default=None, max_length=50)
    fecha_expedicion: date | None = None
    fecha_vencimiento: date
    valor: Decimal = Field(default=Decimal("0"), ge=0)


class DocumentoUpdate(BaseSchema):
    vehiculo_id: uuid.UUID | None = None
    tipo: TipoDocumento | None = None
    descripcion: str | None = Field(default=None, max_length=200)
    numero: str | None = Field(default=None, max_length=50)
    fecha_expedicion: date | None = None
    fecha_vencimiento: date | None = None
    valor: Decimal | None = Field(default=None, ge=0)


class DocumentoRead(TenantRead):
    vehiculo_id: uuid.UUID
    tipo: str
    descripcion: str | None
    numero: str | None
    fecha_expedicion: date | None
    fecha_vencimiento: date
    valor: Decimal
    adjunto_url: str | None


# -------------------------------------------------------------------- cartera
class CarteraFleteCliente(BaseSchema):
    """Una fila de la cartera de fletes: cliente del directorio (cliente_id) u
    ocasional agrupado por nombre normalizado."""

    cliente_id: uuid.UUID | None
    cliente_nombre: str
    servicios_pendientes: int
    total_facturado: Decimal
    total_abonado: Decimal
    saldo: Decimal


class ServicioCarteraRead(BaseSchema):
    """Un servicio con saldo dentro del detalle de cartera, con lo mínimo del
    viaje para poder ir a él desde la pantalla."""

    id: uuid.UUID
    viaje_id: uuid.UUID
    viaje_numero: int
    viaje_fecha: date
    sentido: str
    tipo_cobro: str
    descripcion: str
    kilos: Decimal | None
    tarifa_kilo: Decimal | None
    valor_total: Decimal
    abonado: Decimal
    saldo: Decimal
    estado: str
    abonos: list[AbonoFleteRead] = []


class CarteraFleteDetalle(BaseSchema):
    cliente_id: uuid.UUID | None
    cliente_nombre: str
    servicios: list[ServicioCarteraRead] = []
    total_facturado: Decimal
    total_abonado: Decimal
    saldo: Decimal


# -------------------------------------------------------------------- resumen
class SerieMensualTransporte(BaseSchema):
    mes: str  # "2026-07"
    ingresos: Decimal
    gastos: Decimal
    utilidad: Decimal


class ResumenTransporte(BaseSchema):
    desde: date
    hasta: date
    vehiculo_id: uuid.UUID | None
    viajes_realizados: int
    kilos_transportados: Decimal  # terceros + queso propio
    kilometros: Decimal  # solo viajes con ambos odómetros
    ingresos_terceros: Decimal
    ingresos_internos: Decimal
    total_ingresos: Decimal
    total_pago_conductores: Decimal
    gastos_por_categoria: dict[str, Decimal] = {}
    total_gastos: Decimal  # suma de las categorías (sin documentos: bucket aparte)
    total_mantenimientos: Decimal
    total_documentos: Decimal  # por fecha de expedición
    # Operativa = ingresos - gastos - conductores; neta además resta
    # mantenimientos y documentos del período
    utilidad_operativa: Decimal
    utilidad_neta: Decimal
    # Cartera HISTÓRICA (lo que se debe hoy), como las tarjetas de reventa
    por_cobrar: Decimal
    serie_mensual: list[SerieMensualTransporte] = []


# -------------------------------------------------------------------- alertas
class AlertaDocumento(BaseSchema):
    documento_id: uuid.UUID
    vehiculo_id: uuid.UUID
    vehiculo_placa: str
    vehiculo_nombre: str | None
    tipo: str
    descripcion: str | None
    numero: str | None
    fecha_vencimiento: date
    dias_restantes: int  # negativo = ya vencido
    estado: str  # 'vencido' | 'por_vencer'


class AlertaMantenimiento(BaseSchema):
    mantenimiento_id: uuid.UUID
    vehiculo_id: uuid.UUID
    vehiculo_placa: str
    vehiculo_nombre: str | None
    tipo: str
    descripcion: str
    fecha: date  # cuándo se hizo el último
    proxima_fecha: date | None
    proximo_odometro: Decimal | None
    dias_restantes: int | None  # negativo = ya se pasó la fecha
    km_restantes: Decimal | None  # negativo = ya se pasó el odómetro
    estado: str  # 'vencido' | 'por_vencer'


class AlertasTransporte(BaseSchema):
    documentos: list[AlertaDocumento] = []
    mantenimientos: list[AlertaMantenimiento] = []
