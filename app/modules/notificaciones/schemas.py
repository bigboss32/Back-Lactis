import uuid

from app.common.schemas import BaseSchema, TenantRead


class NotificacionRead(TenantRead):
    usuario_id: uuid.UUID | None
    tipo: str
    titulo: str
    mensaje: str
    referencia: str | None
    leida: bool


class GenerarAlertasResponse(BaseSchema):
    generadas: int
    detalle: dict[str, int]
