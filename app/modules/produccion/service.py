import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from app.common.service import BaseService
from app.core.exceptions import ConflictError
from app.core.pagination import PageParams
from app.modules.produccion.models import Produccion, TipoQueso
from app.modules.produccion.repository import ProduccionRepository, TipoQuesoRepository

CERO = Decimal("0")


class TipoQuesoService(BaseService[TipoQueso]):
    repository_cls = TipoQuesoRepository
    modulo = "produccion"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(TipoQueso.nombre == data["nombre"]):
            raise ConflictError(f"Ya existe el tipo de queso '{data['nombre']}'")


class ProduccionService(BaseService[Produccion]):
    repository_cls = ProduccionRepository
    modulo = "produccion"

    @staticmethod
    def _calcular_rendimiento(data: dict[str, Any], actual: Produccion | None = None) -> dict[str, Any]:
        peso = Decimal(data.get("peso_kg") or (actual.peso_kg if actual else CERO))
        litros = Decimal(
            data.get("litros_usados")
            if data.get("litros_usados") is not None
            else (actual.litros_usados if actual else CERO)
        )
        data["rendimiento"] = (peso / litros).quantize(Decimal("0.0001")) if litros else CERO
        return data

    def crear(self, payload: Any) -> Produccion:
        data = payload.model_dump(exclude_unset=True)
        TipoQuesoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["tipo_queso_id"])
        return super().crear(self._calcular_rendimiento(data))

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Produccion:
        actual = self.repo.get_or_fail(entity_id)
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        return super().actualizar(entity_id, self._calcular_rendimiento(data, actual))

    def listar_filtrado(
        self,
        params: PageParams,
        *,
        tipo_queso_id: uuid.UUID | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> tuple[list[Produccion], int]:
        extra = []
        if desde:
            extra.append(Produccion.fecha >= desde)
        if hasta:
            extra.append(Produccion.fecha <= hasta)
        return self.repo.list_paginated(
            params, filters={"tipo_queso_id": tipo_queso_id}, extra_criteria=extra
        )
