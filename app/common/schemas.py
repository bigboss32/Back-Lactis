import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class AuditRead(BaseSchema):
    """Campos comunes que exponen todos los schemas de lectura."""

    id: uuid.UUID
    estado: str
    created_at: datetime
    updated_at: datetime


class TenantRead(AuditRead):
    empresa_id: uuid.UUID


class MessageResponse(BaseSchema):
    detail: str
