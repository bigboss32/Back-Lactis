from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

TIPO_PLANTA = "planta"
TIPO_CENTRO_ACOPIO = "centro_acopio"
TIPO_PUNTO_VENTA = "punto_venta"


class Sucursal(TenantMixin, AuditMixin, Base):
    __tablename__ = "sucursales"

    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), default=TIPO_CENTRO_ACOPIO)
    direccion: Mapped[str | None] = mapped_column(String(200))
    telefono: Mapped[str | None] = mapped_column(String(30))
    responsable: Mapped[str | None] = mapped_column(String(150))
