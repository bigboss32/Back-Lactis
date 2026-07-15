import uuid
from datetime import date

from fastapi import Depends, Query, UploadFile

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.gastos.schemas import (
    CategoriaGastoCreate,
    CategoriaGastoRead,
    CategoriaGastoUpdate,
    GastoCreate,
    GastoRead,
    GastoUpdate,
)
from app.modules.gastos.service import CategoriaGastoService, GastoService


def _to_read(gasto) -> GastoRead:
    dto = GastoRead.model_validate(gasto)
    dto.categoria_nombre = gasto.categoria.nombre if gasto.categoria else None
    return dto


router = build_crud_router(
    modulo="gastos",
    service_cls=GastoService,
    read_schema=GastoRead,
    create_schema=GastoCreate,
    update_schema=GastoUpdate,
    tags=["Gastos"],
)


@router.get("/filtrar/avanzado", response_model=Page[GastoRead], summary="Listar gastos con filtros")
def filtrar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("gastos", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None),
    categoria_id: uuid.UUID | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[GastoRead]:
    items, total = GastoService(db, ctx).listar_filtrado(
        params, search=search, categoria_id=categoria_id, desde=desde, hasta=hasta
    )
    return Page.build([_to_read(g) for g in items], total, params)


@router.post("/{entity_id}/adjunto", response_model=GastoRead, summary="Adjuntar factura o soporte")
def adjuntar(
    entity_id: uuid.UUID,
    file: UploadFile,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("gastos", "editar")),
) -> GastoRead:
    return _to_read(GastoService(db, ctx).adjuntar_archivo(entity_id, file))


categorias_router = build_crud_router(
    modulo="gastos",
    service_cls=CategoriaGastoService,
    read_schema=CategoriaGastoRead,
    create_schema=CategoriaGastoCreate,
    update_schema=CategoriaGastoUpdate,
    tags=["Gastos"],
)
