from typing import Any

from app.common.service import BaseService
from app.core.exceptions import ConflictError
from app.modules.sucursales.models import Sucursal
from app.modules.sucursales.repository import SucursalRepository


class SucursalService(BaseService[Sucursal]):
    repository_cls = SucursalRepository
    modulo = "sucursales"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(Sucursal.nombre == data["nombre"]):
            raise ConflictError(f"Ya existe una sucursal llamada '{data['nombre']}'")

    def validar_actualizar(self, obj: Sucursal, data: dict[str, Any]) -> None:
        if data.get("nombre") and self.repo.exists_where(
            Sucursal.nombre == data["nombre"], exclude_id=obj.id
        ):
            raise ConflictError(f"Ya existe una sucursal llamada '{data['nombre']}'")
