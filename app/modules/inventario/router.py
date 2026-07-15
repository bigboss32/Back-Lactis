import uuid

from fastapi import Depends, Query

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.inventario.schemas import (
    KardexResponse,
    MovimientoCreate,
    MovimientoRead,
    ProductoCreate,
    ProductoRead,
    ProductoStockRead,
    ProductoUpdate,
)
from app.modules.inventario.service import MovimientoInventarioService, ProductoService

router = build_crud_router(
    modulo="inventario",
    service_cls=ProductoService,
    read_schema=ProductoRead,
    create_schema=ProductoCreate,
    update_schema=ProductoUpdate,
    tags=["Inventario"],
)


@router.get("/stock/actual", response_model=Page[ProductoStockRead], summary="Stock actual por producto")
def stock_actual(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("inventario", "consultar")),
    params: PageParams = Depends(page_params),
    solo_bajo_minimo: bool = Query(False, description="Solo productos bajo stock mínimo"),
) -> Page[ProductoStockRead]:
    items, total = ProductoService(db, ctx).stock_actual(params, solo_bajo_minimo=solo_bajo_minimo)
    return Page.build(items, total, params)


@router.get("/{entity_id}/kardex", response_model=KardexResponse, summary="Kardex del producto")
def kardex(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("inventario", "consultar")),
) -> KardexResponse:
    return MovimientoInventarioService(db, ctx).kardex(entity_id)


# ------------------------------------------------------------------ movimientos
def _mov_to_read(m) -> MovimientoRead:
    dto = MovimientoRead.model_validate(m)
    dto.producto_nombre = m.producto.nombre if m.producto else None
    return dto


movimientos_router = build_crud_router(
    modulo="inventario",
    service_cls=MovimientoInventarioService,
    read_schema=MovimientoRead,
    create_schema=MovimientoCreate,
    update_schema=MovimientoCreate,
    tags=["Inventario"],
)
