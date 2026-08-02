import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.ventas.schemas import (
    CarteraCliente,
    ConductoresPanel,
    PagoConductorCreate,
    PagoConductorRead,
    PagoCreate,
    PagoRead,
    SugerenciasConductores,
    VentaCreate,
    VentaRead,
    VentaUpdate,
)
from app.modules.ventas.service import ConductorService, PagoService, VentaService

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


# ------------------------------------------ lo que se le debe a los conductores
#
# Va COLGADO DE /ventas y no en un módulo aparte (era la duda del dueño): el dato
# nace en el tramo del flete de la venta, usa el mismo permiso `ventas` y se mira
# junto a las ventas. Un módulo propio sería un segundo sitio donde buscar lo
# mismo, con su propio permiso que habría que acordarse de dar.
#
# OJO CON EL ORDEN: estas rutas van ANTES de /{entity_id}, o "conductores" se
# leería como un uuid de venta y respondería 422.
@router.get(
    "/conductores",
    response_model=ConductoresPanel,
    summary="Cuánto se le debe a cada conductor de despachos",
)
def conductores(
    db: DbSession,
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    ctx: RequestContext = Depends(require_permission("ventas", "consultar")),
) -> ConductoresPanel:
    return ConductorService(db, ctx).panel(desde, hasta)


@router.get(
    "/conductores/sugerencias",
    response_model=SugerenciasConductores,
    summary="Nombres de conductor ya usados (autocompletar)",
)
def sugerencias_conductores(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "consultar")),
) -> SugerenciasConductores:
    return SugerenciasConductores(conductores=ConductorService(db, ctx).sugerencias())


@router.get(
    "/conductores/pagos",
    response_model=Page[PagoConductorRead],
    summary="Historial de pagos a conductores",
)
def listar_pagos_conductor(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "consultar")),
    params: PageParams = Depends(page_params),
    conductor: str | None = Query(None, max_length=150),
) -> Page[PagoConductorRead]:
    items, total = ConductorService(db, ctx).listar_pagos(params, conductor=conductor)
    return Page.build(items, total, params)


@router.post(
    "/conductores/pagos",
    response_model=PagoConductorRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un pago a un conductor",
)
def registrar_pago_conductor(
    payload: PagoConductorCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("ventas", "crear")),
) -> PagoConductorRead:
    return ConductorService(db, ctx).registrar_pago(payload)


@router.delete(
    "/conductores/pagos/{pago_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un pago mal registrado a un conductor",
)
def eliminar_pago_conductor(
    pago_id: uuid.UUID,
    db: DbSession,
    # `eliminar` y NO `crear`: borrar un pago SUBE lo que se le debe al conductor.
    # Ya nos pasó al revés en otro módulo y quien solo podía registrar terminaba
    # pudiendo deshacer.
    ctx: RequestContext = Depends(require_permission("ventas", "eliminar")),
) -> None:
    ConductorService(db, ctx).eliminar(pago_id)


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


@router.put("/{entity_id}", response_model=VentaRead, summary="Editar venta (productos, descuento, datos)")
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
