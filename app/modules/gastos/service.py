import uuid
from datetime import date
from typing import Any

from fastapi import UploadFile

from app.common.service import BaseService
from app.core.exceptions import ConflictError
from app.core.pagination import PageParams
from app.modules.gastos.models import CategoriaGasto, Gasto
from app.modules.gastos.repository import CategoriaGastoRepository, GastoRepository
from app.utils.files import save_upload


class CategoriaGastoService(BaseService[CategoriaGasto]):
    repository_cls = CategoriaGastoRepository
    modulo = "gastos"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(CategoriaGasto.nombre == data["nombre"]):
            raise ConflictError(f"Ya existe la categoría '{data['nombre']}'")


class GastoService(BaseService[Gasto]):
    repository_cls = GastoRepository
    modulo = "gastos"

    def crear(self, payload: Any) -> Gasto:
        data = payload.model_dump(exclude_unset=True)
        CategoriaGastoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["categoria_id"])
        return super().crear(data)

    def listar_filtrado(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        categoria_id: uuid.UUID | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> tuple[list[Gasto], int]:
        extra = []
        if desde:
            extra.append(Gasto.fecha >= desde)
        if hasta:
            extra.append(Gasto.fecha <= hasta)
        return self.repo.list_paginated(
            params, search=search, filters={"categoria_id": categoria_id}, extra_criteria=extra
        )

    def adjuntar_archivo(self, entity_id: uuid.UUID, file: UploadFile) -> Gasto:
        gasto = self.repo.get_or_fail(entity_id)
        gasto.adjunto_url = save_upload(file, empresa_id=self.ctx.empresa_id, subdir="gastos")
        gasto.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", gasto.id, None, {"adjunto_url": gasto.adjunto_url})
        return gasto
