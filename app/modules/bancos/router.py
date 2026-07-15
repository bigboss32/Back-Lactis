import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.bancos.models import MovimientoBancario
from app.modules.bancos.schemas import (
    ConciliarRequest,
    CuentaCreate,
    CuentaRead,
    CuentaSaldoRead,
    CuentaUpdate,
    MovimientoBancarioCreate,
    MovimientoBancarioRead,
)
from app.modules.bancos.service import CuentaBancariaService, MovimientoBancarioService

router = build_crud_router(
    modulo="bancos",
    service_cls=CuentaBancariaService,
    read_schema=CuentaRead,
    create_schema=CuentaCreate,
    update_schema=CuentaUpdate,
    tags=["Bancos"],
)


@router.get("/{entity_id}/saldo", response_model=CuentaSaldoRead, summary="Saldo actual de la cuenta")
def saldo(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("bancos", "consultar")),
) -> CuentaSaldoRead:
    return CuentaBancariaService(db, ctx).con_saldo(entity_id)


movimientos_router = APIRouter(tags=["Bancos"])


@movimientos_router.get("", response_model=Page[MovimientoBancarioRead], summary="Listar movimientos bancarios")
def listar_movimientos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("bancos", "consultar")),
    params: PageParams = Depends(page_params),
    cuenta_id: uuid.UUID | None = Query(None),
    conciliado: bool | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[MovimientoBancarioRead]:
    service = MovimientoBancarioService(db, ctx)
    extra = []
    if desde:
        extra.append(MovimientoBancario.fecha >= desde)
    if hasta:
        extra.append(MovimientoBancario.fecha <= hasta)
    items, total = service.repo.list_paginated(
        params,
        filters={"cuenta_id": cuenta_id, "conciliado": conciliado},
        extra_criteria=extra,
    )
    return Page.build(items, total, params)


@movimientos_router.post("", response_model=MovimientoBancarioRead, status_code=201, summary="Registrar movimiento bancario")
def crear_movimiento(
    payload: MovimientoBancarioCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("bancos", "crear")),
) -> MovimientoBancarioRead:
    return MovimientoBancarioService(db, ctx).crear(payload)


@movimientos_router.post(
    "/conciliar", response_model=list[MovimientoBancarioRead], summary="Conciliar movimientos bancarios"
)
def conciliar(
    payload: ConciliarRequest,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("bancos", "administrar")),
) -> list[MovimientoBancarioRead]:
    return MovimientoBancarioService(db, ctx).conciliar(payload.movimiento_ids)
