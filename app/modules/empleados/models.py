import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class Empleado(TenantMixin, AuditMixin, Base):
    __tablename__ = "empleados"

    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    apellido: Mapped[str] = mapped_column(String(100), nullable=False)
    documento: Mapped[str | None] = mapped_column(String(30), index=True)
    cargo: Mapped[str | None] = mapped_column(String(80))
    telefono: Mapped[str | None] = mapped_column(String(30))
    direccion: Mapped[str | None] = mapped_column(String(200))
    fecha_ingreso: Mapped[date | None] = mapped_column(Date)
    salario: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # Valor del jornal (pago por día trabajado). Se usa para calcular la nómina.
    valor_dia: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


class PagoEmpleado(TenantMixin, AuditMixin, Base):
    """Pago a un empleado por los días trabajados en un período (jornal × días)."""

    __tablename__ = "pagos_empleado"

    empleado_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("empleados.id"), index=True, nullable=False
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    periodo: Mapped[str | None] = mapped_column(String(100))
    dias_trabajados: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    valor_dia: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    empleado = relationship("Empleado", lazy="joined")

    @property
    def empleado_nombre(self) -> str | None:
        if not self.empleado:
            return None
        return f"{self.empleado.nombre} {self.empleado.apellido}".strip()
