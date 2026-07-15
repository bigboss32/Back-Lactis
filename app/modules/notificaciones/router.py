import uuid

from fastapi import APIRouter, Depends, Query

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.notificaciones.schemas import GenerarAlertasResponse, NotificacionRead
from app.modules.notificaciones.service import NotificacionService

router = APIRouter(tags=["Notificaciones"])


@router.get("", response_model=Page[NotificacionRead], summary="Mis notificaciones")
def listar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("notificaciones", "consultar")),
    params: PageParams = Depends(page_params),
    solo_no_leidas: bool = Query(False),
) -> Page[NotificacionRead]:
    items, total = NotificacionService(db, ctx).listar_para_usuario(
        params, solo_no_leidas=solo_no_leidas
    )
    return Page.build(items, total, params)


@router.post("/{entity_id}/leer", response_model=NotificacionRead, summary="Marcar notificación como leída")
def marcar_leida(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("notificaciones", "consultar")),
) -> NotificacionRead:
    return NotificacionService(db, ctx).marcar_leida(entity_id)


@router.post("/leer-todas", summary="Marcar todas como leídas")
def marcar_todas(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("notificaciones", "consultar")),
) -> dict:
    return {"marcadas": NotificacionService(db, ctx).marcar_todas_leidas()}


@router.post("/generar-alertas", response_model=GenerarAlertasResponse, summary="Ejecutar reglas de alertas")
def generar_alertas(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("notificaciones", "administrar")),
) -> GenerarAlertasResponse:
    detalle = NotificacionService(db, ctx).generar_alertas()
    return GenerarAlertasResponse(generadas=sum(detalle.values()), detalle=detalle)
