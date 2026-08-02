import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

TIPO_PROVEEDOR = "proveedor"
TIPO_TRANSPORTADOR = "transportador"

# Flujo de estados de una liquidación (usa la columna estado del AuditMixin)
ESTADO_BORRADOR = "borrador"
ESTADO_APROBADA = "aprobada"
# Se le abonó algo pero todavía queda debiendo. Lo pidió el dueño con estas
# palabras: "el pagado no siempre es pagado definitivo; a un proveedor se le
# puede pagar y quedar debiendo otra parte". Se llama igual que en reventa
# (pendiente/parcial/pagada) para que el sistema se lea igual en todas partes.
ESTADO_PARCIAL = "parcial"
ESTADO_PAGADA = "pagada"
ESTADO_ANULADA = "anulada"


class Liquidacion(TenantMixin, AuditMixin, Base):
    __tablename__ = "liquidaciones"
    __table_args__ = (
        Index("ix_liquidacion_periodo", "empresa_id", "periodo_inicio", "periodo_fin"),
    )

    tipo: Mapped[str] = mapped_column(String(20), default=TIPO_PROVEEDOR)
    proveedor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proveedores.id"), index=True)
    transportador_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transportadores.id"), index=True
    )
    periodo_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    periodo_fin: Mapped[date] = mapped_column(Date, nullable=False)

    total_litros: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    precio_promedio: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    valor_bruto: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    bonificaciones: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    descuentos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    valor_transporte: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    anticipos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Lo que ya se le entregó al tercero, sumando todos los pagos parciales.
    # Se guarda como columna (en vez de sumar `pagos` cada vez) por lo mismo que
    # `abonado` en reventa: el tablero y la contabilidad suman esta cifra en SQL
    # sobre cientos de filas y no pueden cargar el historial de cada una.
    pagado: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )
    # Lo que TODAVÍA se le debe = (valor_total - anticipos) - pagado.
    #
    # OJO con el cambio de sentido: antes de los pagos parciales esta columna era
    # el "neto a pagar" y nunca se movía. Ahora baja con cada pago hasta llegar a
    # cero, que es como el dueño lee la palabra "saldo" ("¿cuánto le debo?") y lo
    # que hace que la cuenta cuadre exacta: neto_a_pagar = pagado + saldo.
    # Mientras no haya ningún pago vale lo mismo que antes, así que la lista, el
    # comprobante y las tarjetas del tablero siguen mostrando la misma cifra.
    saldo: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    proveedor = relationship("Proveedor", lazy="joined")
    transportador = relationship("Transportador", lazy="joined")
    detalles: Mapped[list["LiquidacionDetalle"]] = relationship(
        back_populates="liquidacion", lazy="selectin", cascade="all, delete-orphan",
        order_by="LiquidacionDetalle.fecha",
    )
    # selectin y NO joined, igual que los abonos de reventa: con un LEFT JOIN de
    # por medio Postgres rechaza el SELECT ... FOR UPDATE con un 0A000, y ese
    # candado es justo lo que evita que dos pagos simultáneos se pisen.
    pagos: Mapped[list["PagoLiquidacion"]] = relationship(
        back_populates="liquidacion", lazy="selectin", cascade="all, delete-orphan",
        order_by="PagoLiquidacion.fecha",
    )

    @property
    def neto_a_pagar(self) -> Decimal:
        """Lo que hay que entregarle al tercero por esta quincena.

        Es la cifra grande contra la que se abona: el valor total menos los
        anticipos que ya se le habían adelantado. No se guarda porque se deduce
        de dos columnas que sí están, y dos fuentes para el mismo hecho terminan
        contradiciéndose.
        """
        return Decimal(self.valor_total or 0) - Decimal(self.anticipos or 0)

    @property
    def tiene_pagos(self) -> bool:
        """Si ya salió plata contra esta liquidación (aunque sea un abono).

        Es la pregunta que manda para trabar las recepciones del período: con un
        solo pago hecho, cambiar los litros deja ese pago descuadrado.
        """
        return Decimal(self.pagado or 0) > Decimal("0")


class LiquidacionDetalle(AuditMixin, Base):
    __tablename__ = "liquidacion_detalles"

    liquidacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("liquidaciones.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    litros: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    precio_litro: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))

    liquidacion: Mapped[Liquidacion] = relationship(back_populates="detalles")


class PagoLiquidacion(AuditMixin, Base):
    """Un pago (abono) contra una liquidación aprobada.

    Copia el patrón de AbonoCompraQueso: fecha, valor y observaciones, colgado
    del documento por una FK con ondelete CASCADE y SIN empresa_id propio. La
    empresa la pone la liquidación padre, que es por donde se entra siempre; una
    segunda copia del tenant en el hijo es una fuente más que se puede
    desincronizar y que hay que acordarse de filtrar.

    No lleva medio de pago porque el "Pagar" que había tampoco lo llevaba: no
    mueve caja ni bancos, solo deja constancia de cuánto se entregó y cuándo.
    """

    __tablename__ = "pagos_liquidacion"

    liquidacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("liquidaciones.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    liquidacion: Mapped[Liquidacion] = relationship(back_populates="pagos")


class Anticipo(TenantMixin, AuditMixin, Base):
    """Anticipo a un proveedor, transportador o empleado. Se descuenta en su
    próxima liquidación (proveedor/transportador) o pago de nómina (empleado)."""

    __tablename__ = "anticipos"

    # Beneficiario: uno de los tres según 'tipo'
    tipo: Mapped[str] = mapped_column(
        String(20), default=TIPO_PROVEEDOR, server_default=TIPO_PROVEEDOR, index=True
    )
    proveedor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proveedores.id"), index=True)
    transportador_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transportadores.id"), index=True
    )
    empleado_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("empleados.id"), index=True)

    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    # Marcas de aplicado: liquidación (proveedor/transportador) o nómina (empleado)
    liquidacion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("liquidaciones.id"), index=True
    )
    pago_empleado_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pagos_empleado.id"), index=True
    )

    proveedor = relationship("Proveedor", lazy="joined")
    transportador = relationship("Transportador", lazy="joined")
    empleado = relationship("Empleado", lazy="joined")

    @property
    def aplicado(self) -> bool:
        return self.liquidacion_id is not None or self.pago_empleado_id is not None

    @property
    def tercero_nombre(self) -> str | None:
        if self.tipo == TIPO_TRANSPORTADOR:
            return self.transportador.nombre if self.transportador else None
        if self.tipo == "empleado":
            return (
                f"{self.empleado.nombre} {self.empleado.apellido}".strip()
                if self.empleado
                else None
            )
        return self.proveedor.nombre if self.proveedor else None

    @property
    def proveedor_nombre(self) -> str | None:
        # Se expone en AnticipoRead. La relación carga el proveedor aunque esté
        # eliminado (soft delete), por lo que el nombre se conserva en el listado.
        return self.proveedor.nombre if self.proveedor else None
