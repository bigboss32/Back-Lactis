"""Fábrica de routers CRUD: genera los 5 endpoints estándar de un módulo
(listar, obtener, crear, editar, eliminar) con permisos RBAC y paginación.

Cada módulo construye su router con esta fábrica y agrega sus endpoints propios.
"""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params


def build_crud_router(
    *,
    modulo: str,
    service_cls: type,
    read_schema: type,
    create_schema: type,
    update_schema: type,
    tags: list[str] | None = None,
) -> APIRouter:
    router = APIRouter(tags=tags or [modulo])

    @router.get("", response_model=Page[read_schema], summary=f"Listar {modulo}")
    def listar(
        db: DbSession,
        ctx: RequestContext = Depends(require_permission(modulo, "consultar")),
        params: PageParams = Depends(page_params),
        search: str | None = Query(None, description="Búsqueda por texto"),
        estado: str | None = Query(None, description="Filtrar por estado"),
    ) -> Any:
        items, total = service_cls(db, ctx).listar(params, search=search, estado=estado)
        return Page.build(items, total, params)

    @router.get("/{entity_id}", response_model=read_schema, summary=f"Obtener {modulo} por id")
    def obtener(
        entity_id: uuid.UUID,
        db: DbSession,
        ctx: RequestContext = Depends(require_permission(modulo, "consultar")),
    ) -> Any:
        return service_cls(db, ctx).obtener(entity_id)

    @router.post(
        "", response_model=read_schema, status_code=status.HTTP_201_CREATED, summary=f"Crear {modulo}"
    )
    def crear(
        payload: create_schema,  # type: ignore[valid-type]
        db: DbSession,
        ctx: RequestContext = Depends(require_permission(modulo, "crear")),
    ) -> Any:
        return service_cls(db, ctx).crear(payload)

    @router.put("/{entity_id}", response_model=read_schema, summary=f"Actualizar {modulo}")
    def actualizar(
        entity_id: uuid.UUID,
        payload: update_schema,  # type: ignore[valid-type]
        db: DbSession,
        ctx: RequestContext = Depends(require_permission(modulo, "editar")),
    ) -> Any:
        return service_cls(db, ctx).actualizar(entity_id, payload)

    @router.delete(
        "/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, summary=f"Eliminar {modulo} (soft delete)"
    )
    def eliminar(
        entity_id: uuid.UUID,
        db: DbSession,
        ctx: RequestContext = Depends(require_permission(modulo, "eliminar")),
    ) -> None:
        service_cls(db, ctx).eliminar(entity_id)

    return router
