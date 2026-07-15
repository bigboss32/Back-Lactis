from app.common.repository import BaseRepository
from app.modules.proveedores.models import Proveedor


class ProveedorRepository(BaseRepository[Proveedor]):
    model = Proveedor
    search_fields = ("nombre", "documento", "vereda", "municipio")
