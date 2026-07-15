import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class Proveedor(TenantMixin, AuditMixin, Base):
    __tablename__ = "proveedores"

    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    documento: Mapped[str | None] = mapped_column(String(30), index=True)
    vereda: Mapped[str | None] = mapped_column(String(100))
    municipio: Mapped[str | None] = mapped_column(String(100))
    telefono: Mapped[str | None] = mapped_column(String(30))
    # Precio acordado por litro con este proveedor (cada uno negocia el suyo)
    precio_litro: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    ruta_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rutas.id"))
    observaciones: Mapped[str | None] = mapped_column(String(500))
