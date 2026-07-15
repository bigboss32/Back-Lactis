import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class TipoQueso(TenantMixin, AuditMixin, Base):
    __tablename__ = "tipos_queso"

    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(300))
    precio_referencia: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))


class Produccion(TenantMixin, AuditMixin, Base):
    __tablename__ = "producciones"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tipo_queso_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tipos_queso.id"), nullable=False)
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))

    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))  # unidades/bloques
    peso_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    litros_usados: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # rendimiento = kg producidos por litro de leche; merma en kg
    rendimiento: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"))
    merma: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    tipo_queso = relationship("TipoQueso", lazy="joined")
