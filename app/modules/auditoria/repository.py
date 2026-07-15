from app.common.repository import BaseRepository
from app.modules.auditoria.models import Auditoria


class AuditoriaRepository(BaseRepository[Auditoria]):
    model = Auditoria
    search_fields = ("modulo", "accion", "entidad")
