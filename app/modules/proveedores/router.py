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


# Se piden con 'editar' y no con 'eliminar' ni 'administrar' por dos razones:
#
# · Apartar a un proveedor que dejó de entregar es parte del día a día de quien
#   maneja la leche, y es REVERSIBLE (el botón de al lado lo reactiva). Es menos
#   grave que la caneca, así que no puede exigir un permiso más alto que ella.
# · El rol «Compras» —el que en la práctica lleva proveedores, rutas y
#   recepciones— tiene crear/editar/consultar sobre proveedores pero NO tiene
#   'eliminar'. Con 'editar' ese rol puede retirar al que se fue sin que haya
#   que darle además el poder de borrar registros, que es lo que se le negó a
#   propósito. Es el mismo criterio con el que las liquidaciones dejan
#   'recalcular' y 'corregir precio' en 'editar' y reservan 'administrar' para
#   aprobar, pagar y anular.
@router.post(
    "/{entity_id}/desactivar",
    response_model=ProveedorRead,
    summary="Desactivar un proveedor (deja de recibírsele leche; conserva su historia)",
)
def desactivar(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("proveedores", "editar")),
) -> ProveedorRead:
    return ProveedorService(db, ctx).desactivar(entity_id)


@router.post(
    "/{entity_id}/activar",
    response_model=ProveedorRead,
    summary="Reactivar un proveedor que había sido desactivado",
)
def activar(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("proveedores", "editar")),
) -> ProveedorRead:
    return ProveedorService(db, ctx).activar(entity_id)
