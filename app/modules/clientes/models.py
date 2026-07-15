from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class Cliente(TenantMixin, AuditMixin, Base):
    __tablename__ = "clientes"

    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    documento: Mapped[str | None] = mapped_column(String(30), index=True)
    telefono: Mapped[str | None] = mapped_column(String(30))
    correo: Mapped[str | None] = mapped_column(String(150))
    direccion: Mapped[str | None] = mapped_column(String(200))
    ciudad: Mapped[str | None] = mapped_column(String(100))
    observaciones: Mapped[str | None] = mapped_column(String(500))
