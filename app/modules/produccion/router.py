import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.exceptions import BusinessError
from app.core.pagination import Page, PageParams, page_params
from app.modules.produccion.schemas import (
    LotesProduccionPanel,
    ProduccionCreate,
    ProduccionRead,
    ProduccionUpdate,
    TipoQuesoCreate,
    TipoQuesoRead,
    TipoQuesoUpdate,
)
from app.modules.produccion.service import (
    LoteProduccionService,
    ProduccionService,
    TipoQuesoService,
)


def _to_read(produccion) -> ProduccionRead:
    dto = ProduccionRead.model_validate(produccion)
    dto.tipo_queso_nombre = produccion.tipo_queso.nombre if produccion.tipo_queso else None
    return dto


# Las rutas de SEGMENTO FIJO van en este router, que se registra ANTES del CRUD.
# El CRUD trae un `/{entity_id}` que es un comodín de un solo segmento: si se
# registrara primero, se tragaría `/lotes` e intentaría leer "lotes" como un UUID,
# devolviendo 422. (`filtrar/avanzado` se salvaba por casualidad, por tener dos
# segmentos.) Con este orden el problema no se puede repetir al agregar rutas.
router = APIRouter(tags=["Producción"])


@router.get(
    "/lotes",
    response_model=LotesProduccionPanel,
    summary="Utilidad por lote de producción (leche usada, vendido y bodega)",
)
def panel_lotes_produccion(
    db: DbSession,
    desde: date | None = Query(None, description="Filtra qué lotes se muestran"),
    hasta: date | None = Query(None, description="Filtra qué lotes se muestran"),
    ctx: RequestContext = Depends(require_permission("produccion", "consultar")),
) -> LotesProduccionPanel:
    """Qué dejó el queso que se hizo cada día.

    Son dos repartos encadenados: los litros que usó la producción salen de la
    leche más vieja primero (con su precio real, que varía por proveedor), y los
    kilos vendidos salen del lote de producción más viejo, por tipo de queso.

    `desde`/`hasta` recortan qué lotes se MUESTRAN, no el cálculo: la leche del 30
    de junio es el queso de julio y el queso de julio se vende en septiembre.

    El rango se mide por la fecha en que se HIZO el lote, no por la de las ventas:
    la pregunta es "cuánto dejaron los lotes de estos días", así que un lote de
    julio sigue siendo de julio aunque se termine de vender en septiembre.
    """
    if desde and hasta and hasta < desde:
        # Sin esto, un rango al revés devolvería la pantalla vacía y parecería que
        # se perdieron los datos, que es lo peor que puede pasarle a esta pantalla.
        raise BusinessError("La fecha final no puede ser anterior a la inicial")
    return LoteProduccionService(db, ctx).panel(desde, hasta)


@router.get(
    "/filtrar/avanzado",
    response_model=Page[ProduccionRead],
    summary="Listar producción con filtros",
)
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


# El CRUD de último, por lo del comodín explicado arriba.
#
# Se pegan sus rutas a la lista en vez de usar include_router porque el CRUD
# registra "" como ruta (listar y crear van en la raíz del módulo) e
# include_router exige que el prefijo o la ruta no estén vacíos los dos. Las
# rutas ya vienen construidas, así que extender la lista es equivalente y además
# es lo único que conserva el orden que aquí importa.
router.routes.extend(
    build_crud_router(
        modulo="produccion",
        service_cls=ProduccionService,
        read_schema=ProduccionRead,
        create_schema=ProduccionCreate,
        update_schema=ProduccionUpdate,
        tags=["Producción"],
    ).routes
)


tipos_queso_router = build_crud_router(
    modulo="produccion",
    service_cls=TipoQuesoService,
    read_schema=TipoQuesoRead,
    create_schema=TipoQuesoCreate,
    update_schema=TipoQuesoUpdate,
    tags=["Producción"],
)
