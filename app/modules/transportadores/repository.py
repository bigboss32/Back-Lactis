from app.common.repository import BaseRepository
from app.modules.transportadores.models import Transportador


class TransportadorRepository(BaseRepository[Transportador]):
    model = Transportador
    search_fields = ("nombre", "documento", "telefono")
