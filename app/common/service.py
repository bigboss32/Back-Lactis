"""Servicio genérico: orquesta el repositorio, asigna tenant/autoría y audita.

Cada operación de escritura queda registrada en el módulo de auditoría con el
estado anterior y posterior de la entidad.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.common.repository import BaseRepository
from app.core.context import RequestContext
from app.core.pagination import PageParams

ModelT = TypeVar("ModelT")


def serialize_entity(obj: Any) -> dict[str, Any]:
    """Convierte una entidad ORM a dict JSON-safe para auditoría."""
    if obj is None:
        return {}
    result: dict[str, Any] = {}
    for column in sa_inspect(obj).mapper.column_attrs:
        value = getattr(obj, column.key)
        if isinstance(value, (uuid.UUID,)):
            value = str(value)
        elif isinstance(value, (datetime, date)):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        result[column.key] = value
    return result


class BaseService(Generic[ModelT]):
    repository_cls: type[BaseRepository[ModelT]]
    modulo: str = ""

    def __init__(self, db: Session, ctx: RequestContext):
        self.db = db
        self.ctx = ctx
        self.repo = self.repository_cls(db, ctx.empresa_id)

    # -------------------------------------------------------------- auditoría
    def _audit(
        self,
        accion: str,
        entidad_id: uuid.UUID | None,
        antes: dict[str, Any] | None,
        despues: dict[str, Any] | None,
    ) -> None:
        from app.modules.auditoria.models import Auditoria

        self.db.add(
            Auditoria(
                empresa_id=self.ctx.empresa_id,
                usuario_id=self.ctx.user_id,
                ip=self.ctx.ip,
                modulo=self.modulo or self.repo.model.__tablename__,
                accion=accion,
                entidad=self.repo.model.__name__,
                entidad_id=entidad_id,
                antes=antes,
                despues=despues,
            )
        )

    # ------------------------------------------------------------------ CRUD
    def listar(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        estado: str | None = None,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[list[ModelT], int]:
        return self.repo.list_paginated(
            params, search=search, estado=estado, filters=filters, **kwargs
        )

    def obtener(self, entity_id: uuid.UUID) -> ModelT:
        return self.repo.get_or_fail(entity_id)

    def _prepare_create_data(self, data: dict[str, Any]) -> dict[str, Any]:
        if hasattr(self.repo.model, "empresa_id") and not data.get("empresa_id"):
            data["empresa_id"] = self.ctx.empresa_id
        data["created_by"] = self.ctx.user_id
        data["updated_by"] = self.ctx.user_id
        return data

    def validar_crear(self, data: dict[str, Any]) -> None:
        """Punto de extensión para reglas de negocio previas a la creación."""

    def validar_actualizar(self, obj: ModelT, data: dict[str, Any]) -> None:
        """Punto de extensión para reglas de negocio previas a la edición."""

    def validar_eliminar(self, obj: ModelT) -> None:
        """Punto de extensión para reglas de negocio previas a la eliminación."""

    def crear(self, payload: BaseModel | dict[str, Any]) -> ModelT:
        data = payload.model_dump(exclude_unset=True) if isinstance(payload, BaseModel) else dict(payload)
        self.validar_crear(data)
        obj = self.repo.create(self._prepare_create_data(data))
        self._audit("crear", obj.id, None, serialize_entity(obj))
        return obj

    def actualizar(self, entity_id: uuid.UUID, payload: BaseModel | dict[str, Any]) -> ModelT:
        obj = self.repo.get_or_fail(entity_id)
        data = payload.model_dump(exclude_unset=True) if isinstance(payload, BaseModel) else dict(payload)
        self.validar_actualizar(obj, data)
        antes = serialize_entity(obj)
        data["updated_by"] = self.ctx.user_id
        obj = self.repo.update(obj, data)
        self._audit("editar", obj.id, antes, serialize_entity(obj))
        return obj

    def eliminar(self, entity_id: uuid.UUID) -> None:
        obj = self.repo.get_or_fail(entity_id)
        self.validar_eliminar(obj)
        antes = serialize_entity(obj)
        self.repo.soft_delete(obj, deleted_by=self.ctx.user_id)
        self._audit("eliminar", obj.id, antes, serialize_entity(obj))
