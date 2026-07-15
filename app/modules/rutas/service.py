from typing import Any

from app.common.service import BaseService
from app.core.exceptions import ConflictError
from app.modules.rutas.models import Ruta
from app.modules.rutas.repository import RutaRepository


class RutaService(BaseService[Ruta]):
    repository_cls = RutaRepository
    modulo = "rutas"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(Ruta.nombre == data["nombre"]):
            raise ConflictError(f"Ya existe una ruta llamada '{data['nombre']}'")

    def validar_actualizar(self, obj: Ruta, data: dict[str, Any]) -> None:
        if data.get("nombre") and self.repo.exists_where(Ruta.nombre == data["nombre"], exclude_id=obj.id):
            raise ConflictError(f"Ya existe una ruta llamada '{data['nombre']}'")
