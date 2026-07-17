import uuid
from datetime import date

from fastapi import Depends, Query

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.recepcion.schemas import (
    GrillaQuincena,
    RecepcionCreate,
    RecepcionRead,
    RecepcionUpdate,
    ResumenPeriodo,
)
from app.modules.recepcion.service import RecepcionService


def _to_read(recepcion) -> RecepcionRead:
    dto = RecepcionRead.model_validate(recepcion)
    dto.proveedor_nombre = recepcion.proveedor.nombre if recepcion.proveedor else None
    return dto


router = build_crud_router(
    modulo="recepcion",
    service_cls=RecepcionService,
    read_schema=RecepcionRead,
    create_schema=RecepcionCreate,
    update_schema=RecepcionUpdate,
    tags=["Recepción de Leche"],
)


@router.get("/filtrar/avanzado", response_model=Page[RecepcionRead], summary="Listar recepciones con filtros")
def filtrar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("recepcion", "consultar")),
    params: PageParams = Depends(page_params),
    proveedor_id: uuid.UUID | None = Query(None),
    ruta_id: uuid.UUID | None = Query(None),
    transportador_id: uuid.UUID | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    search: str | None = Query(None, description="Buscar por nombre de proveedor"),
) -> Page[RecepcionRead]:
    items, total = RecepcionService(db, ctx).listar_filtrado(
        params,
        proveedor_id=proveedor_id,
        ruta_id=ruta_id,
        transportador_id=transportador_id,
        desde=desde,
        hasta=hasta,
        search=search,
    )
    return Page.build([_to_read(r) for r in items], total, params)


@router.get(
    "/grilla/quincena",
    response_model=GrillaQuincena,
    summary="Grilla proveedores × días (equivalente a la hoja de Excel)",
)
def grilla_quincena(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    search: str | None = Query(None, description="Buscar por nombre de proveedor"),
    ruta_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_permission("recepcion", "consultar")),
) -> GrillaQuincena:
    return RecepcionService(db, ctx).grilla_quincena(desde, hasta, search=search, ruta_id=ruta_id)


@router.get("/resumen/periodo", response_model=ResumenPeriodo, summary="Totales diarios de un período")
def resumen_periodo(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("recepcion", "consultar")),
) -> ResumenPeriodo:
    return RecepcionService(db, ctx).resumen_periodo(desde, hasta)
