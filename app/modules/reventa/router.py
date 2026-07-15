import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.reventa.schemas import (
    AbonoCreate,
    CompraQuesoCreate,
    CompraQuesoRead,
    CompraQuesoUpdate,
    ConversionCreate,
    ConversionRead,
    ResumenReventa,
    VentaQuesoCreate,
    VentaQuesoRead,
    VentaQuesoUpdate,
)
from app.modules.reventa.service import (
    CompraQuesoService,
    ConversionBoronaService,
    ReventaResumenService,
    VentaQuesoService,
)

router = APIRouter(tags=["Compra y venta de queso"])


@router.get("/resumen", response_model=ResumenReventa, summary="Resumen del negocio de reventa")
def resumen(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> ResumenReventa:
    return ReventaResumenService(db, ctx).resumen(desde, hasta)


@router.get("/export/excel", summary="Exportar compras y ventas del período")
def exportar(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("reventa", "exportar")),
) -> Response:
    contenido, filename = ReventaResumenService(db, ctx).exportar_excel(desde, hasta)
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------------------- compras
@router.get("/compras", response_model=Page[CompraQuesoRead], summary="Listar compras de queso")
def listar_compras(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None),
    estado: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[CompraQuesoRead]:
    items, total = CompraQuesoService(db, ctx).listar_filtrado(
        params, search=search, estado=estado, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@router.post("/compras", response_model=CompraQuesoRead, status_code=status.HTTP_201_CREATED, summary="Registrar compra de queso")
def crear_compra(
    payload: CompraQuesoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).crear(payload)


@router.put("/compras/{entity_id}", response_model=CompraQuesoRead, summary="Editar compra (sin abonos)")
def editar_compra(
    entity_id: uuid.UUID,
    payload: CompraQuesoUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).actualizar(entity_id, payload)


@router.delete("/compras/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar compra")
def eliminar_compra(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    CompraQuesoService(db, ctx).eliminar(entity_id)


@router.post("/compras/{entity_id}/abonos", response_model=CompraQuesoRead, summary="Abonar a un productor")
def abonar_compra(
    entity_id: uuid.UUID,
    payload: AbonoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).registrar_abono(entity_id, payload)


@router.post("/compras/{entity_id}/anular", response_model=CompraQuesoRead, summary="Anular compra")
def anular_compra(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "administrar")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).anular(entity_id)


# -------------------------------------------------------------- conversiones
@router.get("/conversiones", response_model=Page[ConversionRead], summary="Queso pasado a borona")
def listar_conversiones(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
) -> Page[ConversionRead]:
    service = ConversionBoronaService(db, ctx)
    items, total = service.listar(params)
    return Page.build(items, total, params)


@router.post(
    "/conversiones",
    response_model=ConversionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Pasar queso a borona",
)
def crear_conversion(
    payload: ConversionCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> ConversionRead:
    return ConversionBoronaService(db, ctx).crear(payload)


@router.delete(
    "/conversiones/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar conversión",
)
def eliminar_conversion(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    ConversionBoronaService(db, ctx).eliminar(entity_id)


# -------------------------------------------------------------------- ventas
@router.get("/ventas", response_model=Page[VentaQuesoRead], summary="Listar ventas de queso")
def listar_ventas(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None),
    estado: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[VentaQuesoRead]:
    items, total = VentaQuesoService(db, ctx).listar_filtrado(
        params, search=search, estado=estado, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@router.post("/ventas", response_model=VentaQuesoRead, status_code=status.HTTP_201_CREATED, summary="Registrar venta de queso")
def crear_venta(
    payload: VentaQuesoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).crear(payload)


@router.put("/ventas/{entity_id}", response_model=VentaQuesoRead, summary="Editar venta (sin abonos)")
def editar_venta(
    entity_id: uuid.UUID,
    payload: VentaQuesoUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).actualizar(entity_id, payload)


@router.delete("/ventas/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar venta")
def eliminar_venta(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    VentaQuesoService(db, ctx).eliminar(entity_id)


@router.post("/ventas/{entity_id}/abonos", response_model=VentaQuesoRead, summary="Registrar abono del cliente")
def abonar_venta(
    entity_id: uuid.UUID,
    payload: AbonoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).registrar_abono(entity_id, payload)


@router.post("/ventas/{entity_id}/anular", response_model=VentaQuesoRead, summary="Anular venta")
def anular_venta(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "administrar")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).anular(entity_id)
