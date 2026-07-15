import uuid

from sqlalchemy import Boolean, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

TIPO_STOCK_BAJO = "stock_bajo"
TIPO_SIN_LIQUIDAR = "proveedores_sin_liquidar"
TIPO_PAGOS_PENDIENTES = "pagos_pendientes"
TIPO_USUARIO_BLOQUEADO = "usuario_bloqueado"


class Notificacion(TenantMixin, AuditMixin, Base):
    __tablename__ = "notificaciones"

    # usuario_id nulo = notificación para toda la empresa
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    tipo: Mapped[str] = mapped_column(String(50), index=True)
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    mensaje: Mapped[str] = mapped_column(String(500), nullable=False)
    referencia: Mapped[str | None] = mapped_column(String(200))  # clave de dedupe
    leida: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
