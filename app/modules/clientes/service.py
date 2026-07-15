from typing import Any

from app.common.service import BaseService
from app.core.exceptions import ConflictError
from app.modules.clientes.models import Cliente
from app.modules.clientes.repository import ClienteRepository


class ClienteService(BaseService[Cliente]):
    repository_cls = ClienteRepository
    modulo = "clientes"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(Cliente.documento == data["documento"]):
            raise ConflictError(f"Ya existe un cliente con documento {data['documento']}")

    def validar_actualizar(self, obj: Cliente, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(
            Cliente.documento == data["documento"], exclude_id=obj.id
        ):
            raise ConflictError(f"Ya existe un cliente con documento {data['documento']}")
