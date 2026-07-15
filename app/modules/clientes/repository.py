from app.common.repository import BaseRepository
from app.modules.clientes.models import Cliente


class ClienteRepository(BaseRepository[Cliente]):
    model = Cliente
    search_fields = ("nombre", "documento", "telefono", "ciudad")
