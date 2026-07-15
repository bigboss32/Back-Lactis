"""Repositorio genérico: CRUD, soft delete, búsqueda, filtros, paginación y
aislamiento multi-tenant automático (filtra por empresa_id si el modelo lo tiene).
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError, NotFoundError
from app.core.pagination import PageParams

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]
    search_fields: tuple[str, ...] = ()
    default_order_by: str = "created_at"
    # Si el modelo es multi-tenant, exige contexto de empresa. Evita que un
    # superadmin sin X-Empresa-Id mezcle datos de todas las empresas.
    tenant_required: bool = True

    def __init__(self, db: Session, empresa_id: uuid.UUID | None = None):
        self.db = db
        self.empresa_id = empresa_id

    # ------------------------------------------------------------------ query
    def base_query(self) -> Select:
        stmt = select(self.model).where(self.model.deleted_at.is_(None))
        if hasattr(self.model, "empresa_id"):
            if self.empresa_id is not None:
                stmt = stmt.where(self.model.empresa_id == self.empresa_id)
            elif self.tenant_required:
                raise BusinessError(
                    "Esta operación requiere contexto de empresa: envíe el header X-Empresa-Id"
                )
        return stmt

    def apply_search(self, stmt: Select, search: str | None) -> Select:
        if search and self.search_fields:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(*[getattr(self.model, f).ilike(pattern) for f in self.search_fields])
            )
        return stmt

    def apply_filters(self, stmt: Select, filters: dict[str, Any] | None) -> Select:
        if filters:
            for field_name, value in filters.items():
                if value is not None:
                    stmt = stmt.where(getattr(self.model, field_name) == value)
        return stmt

    # ------------------------------------------------------------------- read
    def get(self, entity_id: uuid.UUID) -> ModelT | None:
        stmt = self.base_query().where(self.model.id == entity_id)
        return self.db.scalars(stmt).first()

    def get_or_fail(self, entity_id: uuid.UUID) -> ModelT:
        obj = self.get(entity_id)
        if obj is None:
            raise NotFoundError(f"{self.model.__name__} no encontrado")
        return obj

    def list_paginated(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        estado: str | None = None,
        filters: dict[str, Any] | None = None,
        extra_criteria: list[Any] | None = None,
        order_by: Any = None,
    ) -> tuple[list[ModelT], int]:
        stmt = self.base_query()
        stmt = self.apply_search(stmt, search)
        stmt = self.apply_filters(stmt, filters)
        if estado:
            stmt = stmt.where(self.model.estado == estado)
        if extra_criteria:
            stmt = stmt.where(*extra_criteria)

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

        if order_by is None:
            order_by = getattr(self.model, self.default_order_by).desc()
        stmt = stmt.order_by(order_by).offset(params.offset).limit(params.page_size)
        items = list(self.db.scalars(stmt).all())
        return items, total

    def all(self, *, estado: str | None = None) -> list[ModelT]:
        stmt = self.base_query()
        if estado:
            stmt = stmt.where(self.model.estado == estado)
        return list(self.db.scalars(stmt).all())

    def exists_where(self, *criteria: Any, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = self.base_query().where(*criteria)
        if exclude_id:
            stmt = stmt.where(self.model.id != exclude_id)
        return self.db.scalars(stmt.limit(1)).first() is not None

    # ------------------------------------------------------------------ write
    def add(self, obj: ModelT) -> ModelT:
        self.db.add(obj)
        self.db.flush()
        return obj

    def create(self, data: dict[str, Any]) -> ModelT:
        return self.add(self.model(**data))

    def update(self, obj: ModelT, data: dict[str, Any]) -> ModelT:
        for key, value in data.items():
            setattr(obj, key, value)
        self.db.flush()
        return obj

    def soft_delete(self, obj: ModelT, *, deleted_by: uuid.UUID | None = None) -> None:
        obj.deleted_at = datetime.now(timezone.utc)
        obj.estado = "inactivo"
        if deleted_by:
            obj.updated_by = deleted_by
        self.db.flush()
