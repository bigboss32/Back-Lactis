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
    saldo: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    proveedor = relationship("Proveedor", lazy="joined")
    transportador = relationship("Transportador", lazy="joined")
    detalles: Mapped[list["LiquidacionDetalle"]] = relationship(
        back_populates="liquidacion", lazy="selectin", cascade="all, delete-orphan",
        order_by="LiquidacionDetalle.fecha",
    )


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


class Anticipo(TenantMixin, AuditMixin, Base):
    """Anticipo entregado a un proveedor; se descuenta en su próxima liquidación."""

    __tablename__ = "anticipos"

    proveedor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proveedores.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))
    liquidacion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("liquidaciones.id"), index=True
    )

    proveedor = relationship("Proveedor", lazy="joined")
