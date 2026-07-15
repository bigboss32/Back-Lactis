"""Mixins base: toda tabla del sistema hereda auditoría, soft delete y estado.

- AuditMixin: id UUID, timestamps, soft delete, created_by/updated_by, estado.
- TenantMixin: empresa_id obligatorio para aislamiento multi-tenant.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

ESTADO_ACTIVO = "activo"
ESTADO_INACTIVO = "inactivo"


class AuditMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    estado: Mapped[str] = mapped_column(
        String(30), default=ESTADO_ACTIVO, server_default=ESTADO_ACTIVO, index=True
    )


class TenantMixin:
    """Toda entidad de negocio pertenece a una empresa (multi-tenant por fila)."""

    @declared_attr
    def empresa_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(ForeignKey("empresas.id"), index=True, nullable=False)
