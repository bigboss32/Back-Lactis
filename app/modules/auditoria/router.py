import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.auditoria.schemas import AuditoriaRead, LoginAuditRead
from app.modules.auditoria.service import AuditoriaService
from app.modules.usuarios.repository import LoginAuditRepository

router = APIRouter(tags=["Auditoría"])


@router.get("", response_model=Page[AuditoriaRead], summary="Consultar bitácora de auditoría")
def listar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("auditoria", "consultar")),
    params: PageParams = Depends(page_params),
    modulo: str | None = Query(None),
    accion: str | None = Query(None),
    usuario_id: uuid.UUID | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[AuditoriaRead]:
    items, total = AuditoriaService(db, ctx).listar_filtrado(
        params, modulo=modulo, accion=accion, usuario_id=usuario_id, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@router.get("/logins", response_model=Page[LoginAuditRead], summary="Auditoría de inicios de sesión")
def listar_logins(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("auditoria", "consultar")),
    params: PageParams = Depends(page_params),
    usuario_id: uuid.UUID | None = Query(None),
    exito: bool | None = Query(None),
) -> Page[LoginAuditRead]:
    repo = LoginAuditRepository(db)
    items, total = repo.list_paginated(
        params, filters={"usuario_id": usuario_id, "exito": exito}
    )
    return Page.build(items, total, params)
