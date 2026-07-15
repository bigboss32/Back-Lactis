from app.common.repository import BaseRepository
from app.modules.rutas.models import Ruta


class RutaRepository(BaseRepository[Ruta]):
    model = Ruta
    search_fields = ("nombre", "municipio", "descripcion")
