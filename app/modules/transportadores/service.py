from app.common.service import BaseService
from app.modules.transportadores.models import Transportador
from app.modules.transportadores.repository import TransportadorRepository


class TransportadorService(BaseService[Transportador]):
    repository_cls = TransportadorRepository
    modulo = "transportadores"
