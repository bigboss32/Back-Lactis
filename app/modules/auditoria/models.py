import uuid
from typing import Any

from sqlalchemy import JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AuditMixin
from app.core.database import Base


class Auditoria(AuditMixin, Base):
    """Bitácora de todas las operaciones de escritura del sistema.

    La fecha de la operación es created_at; antes/después guardan el snapshot
    JSON de la entidad para trazabilidad completa.
    """

    __tablename__ = "auditorias"

    # Uuid plano (sin FK) para que la bitácora sobreviva a cualquier borrado
    empresa_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    ip: Mapped[str | None] = mapped_column(String(60))
    modulo: Mapped[str] = mapped_column(String(50), index=True)
    accion: Mapped[str] = mapped_column(String(30), index=True)
    entidad: Mapped[str] = mapped_column(String(80))
    entidad_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    antes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    despues: Mapped[dict[str, Any] | None] = mapped_column(JSON)
