from typing import Any

from app.common.service import BaseService
from app.core.exceptions import ConflictError
from app.modules.proveedores.models import Proveedor
from app.modules.proveedores.repository import ProveedorRepository


class ProveedorService(BaseService[Proveedor]):
    repository_cls = ProveedorRepository
    modulo = "proveedores"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(Proveedor.documento == data["documento"]):
            raise ConflictError(f"Ya existe un proveedor con documento {data['documento']}")

    def validar_actualizar(self, obj: Proveedor, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(
            Proveedor.documento == data["documento"], exclude_id=obj.id
        ):
            raise ConflictError(f"Ya existe un proveedor con documento {data['documento']}")
