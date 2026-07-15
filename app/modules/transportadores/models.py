import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class Transportador(TenantMixin, AuditMixin, Base):
    __tablename__ = "transportadores"

    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    documento: Mapped[str | None] = mapped_column(String(30), index=True)
    telefono: Mapped[str | None] = mapped_column(String(30))
    ruta_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rutas.id"))
    # Tarifa que cobra por litro transportado (ej. $100, $130 según ruta)
    valor_transporte: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
