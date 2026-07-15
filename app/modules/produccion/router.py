import uuid
from datetime import date

from fastapi import Depends, Query

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.produccion.schemas import (
    ProduccionCreate,
    ProduccionRead,
    ProduccionUpdate,
    TipoQuesoCreate,
    TipoQuesoRead,
    TipoQuesoUpdate,
)
from app.modules.produccion.service import ProduccionService, TipoQuesoService


def _to_read(produccion) -> ProduccionRead:
    dto = ProduccionRead.model_validate(produccion)
    dto.tipo_queso_nombre = produccion.tipo_queso.nombre if produccion.tipo_queso else None
    return dto


router = build_crud_router(
    modulo="produccion",
    service_cls=ProduccionService,
    read_schema=ProduccionRead,
    create_schema=ProduccionCreate,
    update_schema=ProduccionUpdate,
    tags=["Producción"],
)


@router.get("/filtrar/avanzado", response_model=Page[ProduccionRead], summary="Listar producción con filtros")
def filtrar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("produccion", "consultar")),
    params: PageParams = Depends(page_params),
    tipo_queso_id: uuid.UUID | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[ProduccionRead]:
    items, total = ProduccionService(db, ctx).listar_filtrado(
        params, tipo_queso_id=tipo_queso_id, desde=desde, hasta=hasta
    )
    return Page.build([_to_read(p) for p in items], total, params)


tipos_queso_router = build_crud_router(
    modulo="produccion",
    service_cls=TipoQuesoService,
    read_schema=TipoQuesoRead,
    create_schema=TipoQuesoCreate,
    update_schema=TipoQuesoUpdate,
    tags=["Producción"],
)
