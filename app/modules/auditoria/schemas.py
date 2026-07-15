import uuid
from datetime import datetime
from typing import Any

from app.common.schemas import BaseSchema


class AuditoriaRead(BaseSchema):
    id: uuid.UUID
    created_at: datetime
    empresa_id: uuid.UUID | None
    usuario_id: uuid.UUID | None
    ip: str | None
    modulo: str
    accion: str
    entidad: str
    entidad_id: uuid.UUID | None
    antes: dict[str, Any] | None
    despues: dict[str, Any] | None


class LoginAuditRead(BaseSchema):
    id: uuid.UUID
    created_at: datetime
    usuario_id: uuid.UUID | None
    username_intentado: str | None
    exito: bool
    motivo: str | None
    ip: str | None
    user_agent: str | None
