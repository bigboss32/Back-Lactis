from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class Ruta(TenantMixin, AuditMixin, Base):
    __tablename__ = "rutas"

    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    municipio: Mapped[str | None] = mapped_column(String(100))
    descripcion: Mapped[str | None] = mapped_column(String(300))
