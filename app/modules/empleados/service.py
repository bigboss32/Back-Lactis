from typing import Any

from app.common.service import BaseService
from app.core.exceptions import ConflictError
from app.modules.empleados.models import Empleado
from app.modules.empleados.repository import EmpleadoRepository


class EmpleadoService(BaseService[Empleado]):
    repository_cls = EmpleadoRepository
    modulo = "empleados"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(Empleado.documento == data["documento"]):
            raise ConflictError(f"Ya existe un empleado con documento {data['documento']}")

    def validar_actualizar(self, obj: Empleado, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(
            Empleado.documento == data["documento"], exclude_id=obj.id
        ):
            raise ConflictError(f"Ya existe un empleado con documento {data['documento']}")
