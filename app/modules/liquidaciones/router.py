import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.liquidaciones.schemas import (
    AnticipoCreate,
    AnticipoRead,
    AnticipoUpdate,
    GenerarLiquidaciones,
    LiquidacionRead,
    LiquidacionUpdate,
)
from app.modules.liquidaciones.service import AnticipoService, LiquidacionService

router = APIRouter(tags=["Liquidaciones"])


def _to_read(liq) -> LiquidacionRead:
    dto = LiquidacionRead.model_validate(liq)
    dto.proveedor_nombre = liq.proveedor.nombre if liq.proveedor else None
    dto.transportador_nombre = liq.transportador.nombre if liq.transportador else None
    return dto


@router.post("/generar", response_model=list[LiquidacionRead], summary="Generar liquidaciones del período")
def generar(
    payload: GenerarLiquidaciones,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "crear")),
) -> list[LiquidacionRead]:
    liquidaciones = LiquidacionService(db, ctx).generar(
        payload.periodo_inicio, payload.periodo_fin, payload.tipo, payload.proveedor_id
    )
    return [_to_read(liq) for liq in liquidaciones]


@router.get("", response_model=Page[LiquidacionRead], summary="Listar liquidaciones")
def listar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "consultar")),
    params: PageParams = Depends(page_params),
    tipo: str | None = Query(None),
    estado: str | None = Query(None),
    proveedor_id: uuid.UUID | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[LiquidacionRead]:
    items, total = LiquidacionService(db, ctx).listar_filtrado(
        params, tipo=tipo, estado=estado, proveedor_id=proveedor_id, desde=desde, hasta=hasta
    )
    return Page.build([_to_read(liq) for liq in items], total, params)


@router.get("/export/excel", summary="Exportar liquidaciones del período a Excel")
def exportar_excel(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("liquidaciones", "exportar")),
) -> Response:
    contenido, filename = LiquidacionService(db, ctx).exportar_excel(desde, hasta)
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{entity_id}", response_model=LiquidacionRead, summary="Obtener liquidación")
def obtener(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "consultar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).obtener(entity_id))


@router.put("/{entity_id}", response_model=LiquidacionRead, summary="Actualizar observaciones")
def actualizar(
    entity_id: uuid.UUID,
    payload: LiquidacionUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "editar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).actualizar(entity_id, payload))


@router.post("/{entity_id}/aprobar", response_model=LiquidacionRead, summary="Aprobar liquidación")
def aprobar(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "administrar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).aprobar(entity_id))


@router.post("/{entity_id}/pagar", response_model=LiquidacionRead, summary="Marcar liquidación como pagada")
def pagar(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "administrar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).pagar(entity_id))


@router.post("/{entity_id}/anular", response_model=LiquidacionRead, summary="Anular liquidación")
def anular(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "administrar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).anular(entity_id))


@router.get("/{entity_id}/pdf", summary="Descargar comprobante PDF de la liquidación")
def descargar_pdf(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "imprimir")),
) -> Response:
    contenido, filename = LiquidacionService(db, ctx).generar_pdf(entity_id)
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# -------------------------------------------------------------------- anticipos
anticipos_router = build_crud_router(
    modulo="liquidaciones",
    service_cls=AnticipoService,
    read_schema=AnticipoRead,
    create_schema=AnticipoCreate,
    update_schema=AnticipoUpdate,
    tags=["Anticipos"],
)
