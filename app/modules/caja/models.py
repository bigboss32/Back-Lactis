import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

# Estados del ciclo de caja diaria (columna estado del AuditMixin)
ESTADO_ABIERTA = "abierta"
ESTADO_CERRADA = "cerrada"

TIPO_INGRESO = "ingreso"
TIPO_EGRESO = "egreso"


class CajaDiaria(TenantMixin, AuditMixin, Base):
    __tablename__ = "cajas_diarias"
    __table_args__ = (
        UniqueConstraint("empresa_id", "fecha", "sucursal_id", name="uq_caja_fecha_sucursal"),
    )

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))
    saldo_inicial: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    total_ingresos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    total_egresos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    saldo_final: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Arqueo: efectivo contado físicamente al cierre y diferencia vs saldo esperado
    efectivo_contado: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    diferencia: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    movimientos: Mapped[list["MovimientoCaja"]] = relationship(
        back_populates="caja", lazy="selectin", cascade="all, delete-orphan"
    )


class MovimientoCaja(TenantMixin, AuditMixin, Base):
    __tablename__ = "movimientos_caja"

    caja_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cajas_diarias.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)  # ingreso | egreso
    concepto: Mapped[str] = mapped_column(String(200), nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    referencia: Mapped[str | None] = mapped_column(String(100))

    caja: Mapped[CajaDiaria] = relationship(back_populates="movimientos")
