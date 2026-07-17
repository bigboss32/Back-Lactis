import uuid

from fastapi import Depends, Query

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.proveedores.schemas import ProveedorCreate, ProveedorRead, ProveedorUpdate
from app.modules.proveedores.service import ProveedorService

router = build_crud_router(
    modulo="proveedores",
    service_cls=ProveedorService,
    read_schema=ProveedorRead,
    create_schema=ProveedorCreate,
    update_schema=ProveedorUpdate,
    tags=["Proveedores de Leche"],
)


@router.get(
    "/filtrar/avanzado",
    response_model=Page[ProveedorRead],
    summary="Listar proveedores con búsqueda, estado y filtro por ruta",
)
def filtrar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("proveedores", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None, description="Buscar por nombre/vereda"),
    estado: str | None = Query(None),
    ruta_id: uuid.UUID | None = Query(None),
) -> Page[ProveedorRead]:
    items, total = ProveedorService(db, ctx).listar(
        params, search=search, estado=estado, filters={"ruta_id": ruta_id}
    )
    return Page.build(items, total, params)
