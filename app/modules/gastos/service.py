import uuid
from datetime import date
from decimal import Decimal
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

    @staticmethod
    def _calcular_valor(data: dict[str, Any], actual: Gasto | None = None) -> dict[str, Any]:
        """Si el gasto se cobra por unidad (cantidad × precio), calcula el valor."""
        cantidad = data["cantidad"] if "cantidad" in data else (actual.cantidad if actual else None)
        precio = (
            data["precio_unitario"]
            if "precio_unitario" in data
            else (actual.precio_unitario if actual else None)
        )
        if cantidad is not None and precio is not None:
            data["valor"] = (Decimal(cantidad) * Decimal(precio)).quantize(Decimal("0.01"))
        return data

    def crear(self, payload: Any) -> Gasto:
        data = payload.model_dump(exclude_unset=True)
        CategoriaGastoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["categoria_id"])
        return super().crear(self._calcular_valor(data))

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Gasto:
        actual = self.repo.get_or_fail(entity_id)
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        if data.get("categoria_id"):
            CategoriaGastoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["categoria_id"])
        return super().actualizar(entity_id, self._calcular_valor(data, actual))

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
