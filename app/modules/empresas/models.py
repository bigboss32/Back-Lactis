from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AuditMixin
from app.core.database import Base


class Empresa(AuditMixin, Base):
    __tablename__ = "empresas"

    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    nit: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    direccion: Mapped[str | None] = mapped_column(String(200))
    ciudad: Mapped[str | None] = mapped_column(String(100))
    departamento: Mapped[str | None] = mapped_column(String(100))
    pais: Mapped[str] = mapped_column(String(100), default="Colombia", server_default="Colombia")
    telefono: Mapped[str | None] = mapped_column(String(30))
    correo: Mapped[str | None] = mapped_column(String(150))
    logo_url: Mapped[str | None] = mapped_column(String(300))

    # ---- Suscripción mensual (Wompi) ----
    # NULL = usa la tarifa global SUSCRIPCION_TARIFA_DEFAULT
    tarifa_mensual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # Empresa exenta: no paga y nunca se bloquea
    exenta: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Último día cubierto por pagos; NULL = periodo de prueba desde created_at
    pagada_hasta: Mapped[date | None] = mapped_column(Date)
