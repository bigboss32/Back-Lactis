import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.ventas.schemas import (
    CarteraCliente,
    PagoCreate,
    PagoRead,
    VentaCreate,
    VentaRead,
    VentaUpdate,
)
from app.modules.ventas.service import PagoService, VentaService

router = APIRouter(tags=["Ventas"])


def _to_read(venta) -> VentaRead:
    # from_attributes lee también la property saldo del modelo
    dto = VentaRead.model_validate(venta)
    dto.cliente_nombre = venta.cliente.nombre if venta.cliente else None
    return dto


@router.get("", response_model=Page[VentaRead], summary="Listar ventas")
def listar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "consultar")),
    params: PageParams = Depends(page_params),
    cliente_id: uuid.UUID | None = Query(None),
    tipo: str | None = Query(None),
    estado: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[VentaRead]:
    items, total = VentaService(db, ctx).listar_filtrado(
        params, cliente_id=cliente_id, tipo=tipo, estado=estado, desde=desde, hasta=hasta
    )
    return Page.build([_to_read(v) for v in items], total, params)


@router.get("/cartera", response_model=list[CarteraCliente], summary="Estado de cartera por cliente")
def cartera(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "consultar")),
) -> list[CarteraCliente]:
    return VentaService(db, ctx).cartera()


@router.get("/{entity_id}", response_model=VentaRead, summary="Obtener venta")
def obtener(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "consultar")),
) -> VentaRead:
    return _to_read(VentaService(db, ctx).obtener(entity_id))


@router.post("", response_model=VentaRead, status_code=status.HTTP_201_CREATED, summary="Crear venta")
def crear(
    payload: VentaCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "crear")),
) -> VentaRead:
    return _to_read(VentaService(db, ctx).crear(payload))


@router.put("/{entity_id}", response_model=VentaRead, summary="Actualizar observaciones de la venta")
def actualizar(
    entity_id: uuid.UUID,
    payload: VentaUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "editar")),
) -> VentaRead:
    return _to_read(VentaService(db, ctx).actualizar(entity_id, payload))


@router.post("/{entity_id}/anular", response_model=VentaRead, summary="Anular venta (reintegra inventario)")
def anular(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "administrar")),
) -> VentaRead:
    return _to_read(VentaService(db, ctx).anular(entity_id))


# ------------------------------------------------------------------------ pagos
pagos_router = APIRouter(tags=["Ventas"])


@pagos_router.get("", response_model=Page[PagoRead], summary="Listar pagos")
def listar_pagos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "consultar")),
    params: PageParams = Depends(page_params),
    venta_id: uuid.UUID | None = Query(None),
) -> Page[PagoRead]:
    items, total = PagoService(db, ctx).listar(
        params, filters={"venta_id": venta_id} if venta_id else None
    )
    return Page.build(items, total, params)


@pagos_router.post("", response_model=PagoRead, status_code=status.HTTP_201_CREATED, summary="Registrar pago")
def registrar_pago(
    payload: PagoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "crear")),
) -> PagoRead:
    return PagoService(db, ctx).crear(payload)
