import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.liquidaciones.schemas import (
    AnticipoCreate,
    AnticipoRead,
    AnticipoUpdate,
    GenerarLiquidaciones,
    GenerarLiquidacionesResultado,
    LiquidacionDetallePrecioUpdate,
    LiquidacionRead,
    LiquidacionUpdate,
    PagoLiquidacionCreate,
    PreLiquidacionRead,
    PrevisualizarLiquidacion,
)
from app.modules.liquidaciones.service import AnticipoService, LiquidacionService

router = APIRouter(tags=["Liquidaciones"])


def _to_read(liq) -> LiquidacionRead:
    dto = LiquidacionRead.model_validate(liq)
    dto.proveedor_nombre = liq.proveedor.nombre if liq.proveedor else None
    dto.transportador_nombre = liq.transportador.nombre if liq.transportador else None
    return dto


@router.post(
    "/generar",
    response_model=GenerarLiquidacionesResultado,
    summary="Generar liquidaciones del período (devuelve las generadas y las omitidas)",
)
def generar(
    payload: GenerarLiquidaciones,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "crear")),
) -> GenerarLiquidacionesResultado:
    """ESTO YA NO DEVUELVE UNA LISTA PELADA, y el cambio de forma fue el arreglo.

    "Generar" barre TODOS los terceros del período, y un tercero se puede quedar por
    fuera (un período que se cruza, una tarifa de flete sin llenar). Antes eso era o un
    error que tumbaba la corrida completa —dejando sin comprobante a los que no tenían
    nada que ver— o un salto en silencio, y en los dos casos quedaba leche sin papel. La
    respuesta trae ahora `generadas` y `omitidas`; el porqué está en
    `GenerarLiquidacionesResultado` y en `LiquidacionOmitida`.
    """
    liquidaciones, omitidas = LiquidacionService(db, ctx).generar(
        payload.periodo_inicio, payload.periodo_fin, payload.tipo, payload.proveedor_id
    )
    return GenerarLiquidacionesResultado(
        generadas=[_to_read(liq) for liq in liquidaciones], omitidas=omitidas
    )


@router.post(
    "/previsualizar",
    response_model=list[PreLiquidacionRead],
    summary="Pre-liquidación: calcular cómo va un tercero (sin generar)",
)
def previsualizar(
    payload: PrevisualizarLiquidacion,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "consultar")),
) -> list[PreLiquidacionRead]:
    return LiquidacionService(db, ctx).previsualizar(
        payload.periodo_inicio, payload.periodo_fin, payload.tipo, payload.tercero_id
    )


@router.post("/previsualizar/pdf", summary="PDF preliminar de una pre-liquidación")
def previsualizar_pdf(
    payload: PrevisualizarLiquidacion,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "imprimir")),
) -> Response:
    contenido, filename = LiquidacionService(db, ctx).previsualizar_pdf(
        payload.periodo_inicio, payload.periodo_fin, payload.tipo, payload.tercero_id
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@router.put(
    "/{entity_id}/detalles/{detalle_id}",
    response_model=LiquidacionRead,
    summary="Corregir el precio por litro de un día (solo en borrador)",
)
def actualizar_precio_detalle(
    entity_id: uuid.UUID,
    detalle_id: uuid.UUID,
    payload: LiquidacionDetallePrecioUpdate,
    db: DbSession,
    # Mismo permiso que ya exige editar la liquidación: corregir el precio de un
    # día es editarla, no administrarla.
    ctx: RequestContext = Depends(require_permission("liquidaciones", "editar")),
) -> LiquidacionRead:
    return _to_read(
        LiquidacionService(db, ctx).actualizar_precio_detalle(
            entity_id, detalle_id, payload.precio_litro
        )
    )


@router.post(
    "/{entity_id}/recalcular",
    response_model=LiquidacionRead,
    summary="Recalcular un borrador: recoge los anticipos pendientes del tercero",
)
def recalcular(
    entity_id: uuid.UUID,
    db: DbSession,
    # Mismo permiso que corregir el precio de un día: recalcular el borrador es
    # editarlo, no administrarlo.
    ctx: RequestContext = Depends(require_permission("liquidaciones", "editar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).recalcular(entity_id))


@router.post("/{entity_id}/aprobar", response_model=LiquidacionRead, summary="Aprobar liquidación")
def aprobar(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "administrar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).aprobar(entity_id))


@router.post("/{entity_id}/pagar", response_model=LiquidacionRead, summary="Pagar el saldo completo de la liquidación")
def pagar(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "administrar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).pagar(entity_id))


@router.post(
    "/{entity_id}/pagos",
    response_model=LiquidacionRead,
    summary="Registrar un pago parcial (abono) a una liquidación aprobada",
)
def registrar_pago(
    entity_id: uuid.UUID,
    payload: PagoLiquidacionCreate,
    db: DbSession,
    # El MISMO permiso que exigía el botón "Pagar" de siempre: entregar plata es
    # 'administrar'. Con 'crear' —el permiso de generar la quincena, que tiene el
    # rol Compras— cualquiera que arma liquidaciones podría además pagarlas.
    ctx: RequestContext = Depends(require_permission("liquidaciones", "administrar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).registrar_pago(entity_id, payload))


@router.delete(
    "/{entity_id}/pagos/{pago_id}",
    response_model=LiquidacionRead,
    summary="Eliminar un pago mal registrado de la liquidación",
)
def eliminar_pago(
    entity_id: uuid.UUID,
    pago_id: uuid.UUID,
    db: DbSession,
    # 'eliminar', NO 'crear'. En reventa esto estaba con 'crear' y dejaba borrar
    # pagos a quien solo podía anotarlos: borrar un pago le devuelve la deuda al
    # sistema y es la puerta para tapar una entrega de plata.
    ctx: RequestContext = Depends(require_permission("liquidaciones", "eliminar")),
) -> LiquidacionRead:
    return _to_read(LiquidacionService(db, ctx).eliminar_pago(entity_id, pago_id))


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
anticipos_router = APIRouter(tags=["Anticipos"])


@anticipos_router.get("", response_model=Page[AnticipoRead], summary="Listar anticipos")
def listar_anticipos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None, description="Búsqueda por texto"),
    estado: str | None = Query(None, description="Filtrar por estado"),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Any:
    items, total = AnticipoService(db, ctx).listar(params, search=search, estado=estado, desde=desde, hasta=hasta)
    return Page.build(items, total, params)


@anticipos_router.get("/totales/suma", response_model=float, summary="Suma total de anticipos con filtros")
def suma_anticipos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "consultar")),
    search: str | None = Query(None, description="Búsqueda por texto"),
    estado: str | None = Query(None, description="Filtrar por estado"),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> float:
    return float(AnticipoService(db, ctx).suma_filtrada(search=search, estado=estado, desde=desde, hasta=hasta))


@anticipos_router.get("/{entity_id}", response_model=AnticipoRead, summary="Obtener anticipo por id")
def obtener_anticipo(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "consultar")),
) -> Any:
    return AnticipoService(db, ctx).obtener(entity_id)


@anticipos_router.post("", response_model=AnticipoRead, status_code=status.HTTP_201_CREATED, summary="Crear anticipo")
def crear_anticipo(
    payload: AnticipoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "crear")),
) -> Any:
    return AnticipoService(db, ctx).crear(payload)


@anticipos_router.put("/{entity_id}", response_model=AnticipoRead, summary="Actualizar anticipo")
def actualizar_anticipo(
    entity_id: uuid.UUID,
    payload: AnticipoUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "editar")),
) -> Any:
    return AnticipoService(db, ctx).actualizar(entity_id, payload)


@anticipos_router.delete("/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar anticipo")
def eliminar_anticipo(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("liquidaciones", "eliminar")),
) -> None:
    AnticipoService(db, ctx).eliminar(entity_id)
