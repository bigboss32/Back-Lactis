import uuid
from typing import Any

from fastapi import UploadFile

from app.common.service import BaseService
from app.core.exceptions import ConflictError, ForbiddenError
from app.core.pagination import PageParams
from app.modules.empresas.models import Empresa
from app.modules.empresas.repository import EmpresaRepository
from app.utils.files import save_upload


class EmpresaService(BaseService[Empresa]):
    repository_cls = EmpresaRepository
    modulo = "empresas"

    def listar(self, params: PageParams, **kwargs: Any) -> tuple[list[Empresa], int]:
        # Un usuario no superadmin solo ve su propia empresa
        if not self.ctx.is_superadmin:
            kwargs.setdefault("extra_criteria", []).append(Empresa.id == self.ctx.empresa_id)
        return super().listar(params, **kwargs)

    def obtener(self, entity_id: uuid.UUID) -> Empresa:
        if not self.ctx.is_superadmin and entity_id != self.ctx.empresa_id:
            raise ForbiddenError("No puede acceder a información de otra empresa")
        return super().obtener(entity_id)

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(Empresa.nit == data["nit"]):
            raise ConflictError(f"Ya existe una empresa con NIT {data['nit']}")

    def validar_actualizar(self, obj: Empresa, data: dict[str, Any]) -> None:
        if not self.ctx.is_superadmin and obj.id != self.ctx.empresa_id:
            raise ForbiddenError("No puede modificar otra empresa")
        if data.get("nit") and self.repo.exists_where(Empresa.nit == data["nit"], exclude_id=obj.id):
            raise ConflictError(f"Ya existe una empresa con NIT {data['nit']}")

    def subir_logo(self, entity_id: uuid.UUID, file: UploadFile) -> Empresa:
        empresa = self.obtener(entity_id)
        ruta = save_upload(file, empresa_id=empresa.id, subdir="logos")
        return self.actualizar(entity_id, {"logo_url": ruta})
