from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

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
