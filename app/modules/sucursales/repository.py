from app.common.repository import BaseRepository
from app.modules.sucursales.models import Sucursal


class SucursalRepository(BaseRepository[Sucursal]):
    model = Sucursal
    search_fields = ("nombre", "direccion", "responsable")
