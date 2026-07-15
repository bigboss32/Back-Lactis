import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

TIPO_INGRESO = "ingreso"
TIPO_EGRESO = "egreso"


class CuentaBancaria(TenantMixin, AuditMixin, Base):
    __tablename__ = "cuentas_bancarias"

    banco: Mapped[str] = mapped_column(String(100), nullable=False)
    numero_cuenta: Mapped[str] = mapped_column(String(50), nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), default="ahorros")  # ahorros | corriente
    titular: Mapped[str | None] = mapped_column(String(150))
    saldo_inicial: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))

    movimientos: Mapped[list["MovimientoBancario"]] = relationship(
        back_populates="cuenta", lazy="noload"
    )


class MovimientoBancario(TenantMixin, AuditMixin, Base):
    __tablename__ = "movimientos_bancarios"

    cuenta_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cuentas_bancarias.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)  # ingreso | egreso
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    concepto: Mapped[str] = mapped_column(String(200), nullable=False)
    referencia: Mapped[str | None] = mapped_column(String(100))
    conciliado: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    fecha_conciliacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cuenta: Mapped[CuentaBancaria] = relationship(back_populates="movimientos")
