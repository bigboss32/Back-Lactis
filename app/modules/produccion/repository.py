from app.common.repository import BaseRepository
from app.modules.produccion.models import Produccion, TipoQueso


class TipoQuesoRepository(BaseRepository[TipoQueso]):
    model = TipoQueso
    search_fields = ("nombre", "descripcion")


class ProduccionRepository(BaseRepository[Produccion]):
    model = Produccion
    default_order_by = "fecha"
