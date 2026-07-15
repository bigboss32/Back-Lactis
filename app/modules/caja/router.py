import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.caja.models import CajaDiaria
from app.modules.caja.schemas import (
    AbrirCaja,
    CajaRead,
    CerrarCaja,
    MovimientoCajaCreate,
    MovimientoCajaRead,
)
from app.modules.caja.service import CajaService

router = APIRouter(tags=["Caja"])


@router.get("", response_model=Page[CajaRead], summary="Listar cajas diarias")
def listar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("caja", "consultar")),
    params: PageParams = Depends(page_params),
    estado: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[CajaRead]:
    service = CajaService(db, ctx)
    extra = []
    if desde:
        extra.append(CajaDiaria.fecha >= desde)
    if hasta:
        extra.append(CajaDiaria.fecha <= hasta)
    items, total = service.repo.list_paginated(params, estado=estado, extra_criteria=extra)
    return Page.build(items, total, params)


@router.get("/{entity_id}", response_model=CajaRead, summary="Obtener caja con sus movimientos")
def obtener(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("caja", "consultar")),
) -> CajaRead:
    return CajaService(db, ctx).obtener(entity_id)


@router.post("/abrir", response_model=CajaRead, status_code=status.HTTP_201_CREATED, summary="Abrir caja del día")
def abrir(
    payload: AbrirCaja,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("caja", "crear")),
) -> CajaRead:
    return CajaService(db, ctx).abrir(payload)


@router.post("/{entity_id}/cerrar", response_model=CajaRead, summary="Cerrar caja (arqueo)")
def cerrar(
    entity_id: uuid.UUID,
    payload: CerrarCaja,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("caja", "administrar")),
) -> CajaRead:
    return CajaService(db, ctx).cerrar(entity_id, payload.efectivo_contado, payload.observaciones)


@router.post(
    "/movimientos",
    response_model=MovimientoCajaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar ingreso o egreso de caja",
)
def registrar_movimiento(
    payload: MovimientoCajaCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("caja", "crear")),
) -> MovimientoCajaRead:
    return CajaService(db, ctx).registrar_movimiento(payload)
