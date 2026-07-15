from app.common.repository import BaseRepository
from app.modules.empresas.models import Empresa


class EmpresaRepository(BaseRepository[Empresa]):
    model = Empresa
    search_fields = ("nombre", "nit", "ciudad")
