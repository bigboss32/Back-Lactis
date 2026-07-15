from app.common.repository import BaseRepository
from app.modules.notificaciones.models import Notificacion


class NotificacionRepository(BaseRepository[Notificacion]):
    model = Notificacion
    search_fields = ("titulo", "mensaje")

    def existe_pendiente(self, tipo: str, referencia: str) -> bool:
        return self.exists_where(
            Notificacion.tipo == tipo,
            Notificacion.referencia == referencia,
            Notificacion.leida.is_(False),
        )
