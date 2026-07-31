"""Transporte de carga ("la turbo"): el negocio paralelo del camión de la quesera.

Se transporta queso propio (ingreso "interno" valorado a tarifa por kilo, para
medir la rentabilidad real del viaje) y carga de terceros (por kilo o a precio
fijo) con cartera y abonos por servicio. Los egresos del vehículo van aparte de
la tabla `gastos` del ERP: gastos por viaje o generales, mantenimientos y
documentos legales (SOAT, tecnomecánica...) con fecha de vencimiento.

Este libro es INDEPENDIENTE del contable de la quesera, como el precedente del
módulo `reventa`: contabilidad/estado de resultados no leen estas tablas.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

CERO = Decimal("0")

# Ciclo de vida del viaje (columna estado del AuditMixin)
ESTADO_VIAJE_EN_CURSO = "en_curso"
ESTADO_VIAJE_FINALIZADO = "finalizado"
ESTADO_VIAJE_ANULADO = "anulado"

# Estado de pago del servicio de flete (columna estado del AuditMixin).
# "interno" es el queso propio: no genera cartera ni recibe abonos.
ESTADO_SERVICIO_PENDIENTE = "pendiente"
ESTADO_SERVICIO_PARCIAL = "parcial"
ESTADO_SERVICIO_PAGADA = "pagada"
ESTADO_SERVICIO_ANULADA = "anulada"
ESTADO_SERVICIO_INTERNO = "interno"

SENTIDO_IDA = "ida"
SENTIDO_REGRESO = "regreso"

COBRO_POR_KILO = "por_kilo"
COBRO_PRECIO_FIJO = "precio_fijo"

METODO_EFECTIVO = "efectivo"

# Categorías de gasto del vehículo. NO incluyen los documentos legales a
# propósito: el SOAT y compañía tienen tabla propia y suman en el resumen como
# bucket aparte (`total_documentos`), así no se cuentan dos veces.
CATEGORIAS_GASTO = (
    "combustible",
    "peajes",
    "viaticos",
    "cargue_descargue",
    "lavada",
    "parqueadero",
    "multa",
    "otros",
)

TIPOS_DOCUMENTO = ("soat", "tecnomecanica", "seguro", "impuesto", "otro")

MANTENIMIENTO_PREVENTIVO = "preventivo"
MANTENIMIENTO_CORRECTIVO = "correctivo"


class Vehiculo(TenantMixin, AuditMixin, Base):
    __tablename__ = "vehiculos"
    __table_args__ = (UniqueConstraint("empresa_id", "placa", name="uq_vehiculo_placa"),)

    placa: Mapped[str] = mapped_column(String(10), nullable=False)
    # Alias con el que lo llaman en la quesera ("La Turbo")
    nombre: Mapped[str | None] = mapped_column(String(80))
    marca: Mapped[str | None] = mapped_column(String(80))
    linea: Mapped[str | None] = mapped_column(String(80))
    anio: Mapped[int | None] = mapped_column(Integer)
    capacidad_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Tarifa base que se cobra por kilo transportado; editable en cada servicio
    tarifa_kilo: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=CERO)
    # Solo SUBE: al finalizar un viaje o registrar mantenimiento/gasto con
    # odómetro mayor. La corrección manual va por el PUT del vehículo.
    odometro_actual: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=CERO)
    observaciones: Mapped[str | None] = mapped_column(String(500))


class Viaje(TenantMixin, AuditMixin, Base):
    __tablename__ = "viajes"
    __table_args__ = (UniqueConstraint("empresa_id", "numero", name="uq_viaje_numero"),)

    numero: Mapped[int] = mapped_column(Integer, nullable=False)  # consecutivo por empresa
    vehiculo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vehiculos.id"), index=True)
    fecha_salida: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fecha_regreso: Mapped[date | None] = mapped_column(Date)
    origen: Mapped[str] = mapped_column(String(120), nullable=False)
    destino: Mapped[str] = mapped_column(String(120), nullable=False)
    # El conductor se paga por viaje (monto digitado); cuenta como gasto del viaje
    conductor_nombre: Mapped[str | None] = mapped_column(String(150))
    pago_conductor: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=CERO)
    odometro_salida: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    odometro_regreso: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    vehiculo: Mapped[Vehiculo] = relationship(lazy="joined")
    servicios: Mapped[list["ViajeServicio"]] = relationship(
        back_populates="viaje", lazy="selectin", cascade="all, delete-orphan",
        order_by="ViajeServicio.created_at",
    )
    gastos: Mapped[list["VehiculoGasto"]] = relationship(
        back_populates="viaje", lazy="selectin", order_by="VehiculoGasto.fecha",
    )

    # ------------------------------------------------- totales SIEMPRE calculados
    # No se desnormalizan a propósito: solo valor_total/abonado del servicio se
    # persisten (patrón de los abonos existentes) y el resto sale de sumar.
    @property
    def servicios_vigentes(self) -> list["ViajeServicio"]:
        return [s for s in self.servicios if s.deleted_at is None]

    @property
    def gastos_vigentes(self) -> list["VehiculoGasto"]:
        return [g for g in self.gastos if g.deleted_at is None]

    @property
    def ingresos_internos(self) -> Decimal:
        """Queso propio valorado a tarifa: mide rentabilidad, no genera cartera."""
        return sum(
            (s.valor_total for s in self.servicios_vigentes
             if s.estado == ESTADO_SERVICIO_INTERNO),
            CERO,
        )

    @property
    def total_ingresos(self) -> Decimal:
        """Terceros + interno; los servicios anulados no cuentan."""
        return sum(
            (s.valor_total for s in self.servicios_vigentes
             if s.estado != ESTADO_SERVICIO_ANULADA),
            CERO,
        )

    @property
    def ingresos_terceros(self) -> Decimal:
        return self.total_ingresos - self.ingresos_internos

    @property
    def total_gastos_viaje(self) -> Decimal:
        """Gastos registrados al viaje MÁS el pago del conductor."""
        return sum((g.valor for g in self.gastos_vigentes), CERO) + (
            self.pago_conductor or CERO
        )

    @property
    def utilidad(self) -> Decimal:
        return self.total_ingresos - self.total_gastos_viaje

    @property
    def saldo_cartera(self) -> Decimal:
        """Lo que los terceros todavía deben de este viaje."""
        return sum(
            (s.saldo for s in self.servicios_vigentes
             if s.estado in (ESTADO_SERVICIO_PENDIENTE, ESTADO_SERVICIO_PARCIAL)),
            CERO,
        )

    # Para las listas: la placa y el alias sin otra vuelta a la base (lazy joined)
    @property
    def vehiculo_placa(self) -> str | None:
        return self.vehiculo.placa if self.vehiculo else None

    @property
    def vehiculo_nombre(self) -> str | None:
        return self.vehiculo.nombre if self.vehiculo else None


class ViajeServicio(TenantMixin, AuditMixin, Base):
    """Un flete dentro del viaje: carga de un tercero (por kilo o a precio fijo)
    o queso propio de la quesera (es_interno)."""

    __tablename__ = "viaje_servicios"

    viaje_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viajes.id", ondelete="CASCADE"), index=True
    )
    sentido: Mapped[str] = mapped_column(
        String(10), default=SENTIDO_IDA, server_default=SENTIDO_IDA
    )
    tipo_cobro: Mapped[str] = mapped_column(
        String(15), default=COBRO_POR_KILO, server_default=COBRO_POR_KILO
    )
    es_interno: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Cliente híbrido: FK al directorio o texto libre para ocasionales de
    # contado. El CRÉDITO exige cliente del directorio (lo valida el servicio).
    cliente_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clientes.id"), index=True)
    cliente_nombre: Mapped[str | None] = mapped_column(String(150))
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False)
    kilos: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    tarifa_kilo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=CERO)
    abonado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=CERO)
    observaciones: Mapped[str | None] = mapped_column(String(500))

    viaje: Mapped[Viaje] = relationship(back_populates="servicios")
    abonos: Mapped[list["AbonoFlete"]] = relationship(
        back_populates="servicio", lazy="selectin", cascade="all, delete-orphan",
        order_by="AbonoFlete.fecha",
    )

    @property
    def saldo(self) -> Decimal:
        # El interno no genera cartera y el anulado ya no se cobra
        if self.es_interno or self.estado == ESTADO_SERVICIO_ANULADA:
            return CERO
        return self.valor_total - self.abonado


class AbonoFlete(AuditMixin, Base):
    """Abono de un tercero a su servicio de flete (sin empresa_id, como
    abonos_compra_queso: el tenant viene del servicio padre)."""

    __tablename__ = "abonos_flete"

    servicio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viaje_servicios.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    metodo: Mapped[str] = mapped_column(String(30), default=METODO_EFECTIVO)
    referencia: Mapped[str | None] = mapped_column(String(100))
    observaciones: Mapped[str | None] = mapped_column(String(300))

    servicio: Mapped[ViajeServicio] = relationship(back_populates="abonos")


class VehiculoGasto(TenantMixin, AuditMixin, Base):
    """Gasto del vehículo: de un viaje (viaje_id) o general (viaje_id nulo)."""

    __tablename__ = "vehiculo_gastos"

    vehiculo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vehiculos.id"), index=True)
    viaje_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("viajes.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    categoria: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    concepto: Mapped[str | None] = mapped_column(String(200))
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    odometro: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    adjunto_url: Mapped[str | None] = mapped_column(String(300))

    viaje: Mapped[Viaje | None] = relationship(back_populates="gastos")


class VehiculoMantenimiento(TenantMixin, AuditMixin, Base):
    __tablename__ = "vehiculo_mantenimientos"

    vehiculo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vehiculos.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(
        String(20), default=MANTENIMIENTO_PREVENTIVO, server_default=MANTENIMIENTO_PREVENTIVO
    )
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False)
    taller: Mapped[str | None] = mapped_column(String(150))
    odometro: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=CERO)
    # Cuándo toca el próximo: por kilometraje, por fecha, o los dos
    proximo_odometro: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    proxima_fecha: Mapped[date | None] = mapped_column(Date)
    adjunto_url: Mapped[str | None] = mapped_column(String(300))

    vehiculo: Mapped[Vehiculo] = relationship(lazy="joined")


class VehiculoDocumento(TenantMixin, AuditMixin, Base):
    """Documento legal del vehículo. La renovación es un registro NUEVO (el
    histórico se conserva); las alertas solo evalúan el de vencimiento más
    reciente por (vehículo, tipo) para no alertar eternamente por vigencias
    viejas."""

    __tablename__ = "vehiculo_documentos"

    vehiculo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vehiculos.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    descripcion: Mapped[str | None] = mapped_column(String(200))
    numero: Mapped[str | None] = mapped_column(String(50))
    fecha_expedicion: Mapped[date | None] = mapped_column(Date)
    fecha_vencimiento: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Lo que costó: va al resumen como bucket propio (total_documentos), nunca
    # como categoría de gasto, para no contarlo dos veces.
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=CERO)
    adjunto_url: Mapped[str | None] = mapped_column(String(300))

    vehiculo: Mapped[Vehiculo] = relationship(lazy="joined")
