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
    EstadoCuentaCliente,
    EstadoCuentaProductor,
    ResumenReventa,
    SaldoAnteriorCreate,
    SaldoAnteriorRead,
    SaldoAnteriorUpdate,
    SugerenciasReventa,
    VentaQuesoCreate,
    VentaQuesoRead,
    VentaQuesoUpdate,
)
from app.modules.reventa.service import (
    CompraQuesoService,
    ConversionBoronaService,
    ReventaResumenService,
    SaldoAnteriorService,
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


@router.get(
    "/estado-cuenta",
    response_model=EstadoCuentaCliente,
    summary="Estado de cuenta de un cliente (compras, pagos y saldo)",
)
def estado_cuenta(
    db: DbSession,
    # max_length igual al de la columna (String(150)): un nombre más largo no
    # puede existir en la base, así que se rechaza antes de tocar la consulta.
    cliente: str = Query(..., min_length=2, max_length=150),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> EstadoCuentaCliente:
    """Sin desde/hasta cubre todo el histórico del cliente: el saldo real que debe."""
    return ReventaResumenService(db, ctx).estado_cuenta(cliente, desde, hasta)


@router.get(
    "/estado-cuenta/pdf",
    summary="Descargar el estado de cuenta del cliente en PDF (para enviárselo)",
)
def estado_cuenta_pdf(
    db: DbSession,
    # Mismo tope que la columna String(150), igual que en la ruta del JSON.
    cliente: str = Query(..., min_length=2, max_length=150),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    ctx: RequestContext = Depends(require_permission("reventa", "imprimir")),
) -> Response:
    contenido, filename = ReventaResumenService(db, ctx).estado_cuenta_pdf(
        cliente, desde, hasta
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/estado-cuenta-productor",
    response_model=EstadoCuentaProductor,
    summary="Estado de cuenta de un productor (compras, pagos y saldo a su favor)",
)
def estado_cuenta_productor(
    db: DbSession,
    # max_length igual al de la columna (String(150)), como en el del cliente: un
    # nombre más largo no puede existir en la base, así que se rechaza antes de
    # tocar la consulta.
    productor: str = Query(..., min_length=2, max_length=150),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> EstadoCuentaProductor:
    """Sin desde/hasta cubre todo el histórico del productor, que es el caso
    normal: el saldo real que se le debe.

    OJO CON EL SIGNO, que va al contrario del estado de cuenta del cliente: aquí
    un saldo positivo significa que LA QUESERA LE DEBE A ÉL.
    """
    return ReventaResumenService(db, ctx).estado_cuenta_productor(productor, desde, hasta)


@router.get(
    "/estado-cuenta-productor/pdf",
    summary="Descargar el estado de cuenta del productor en PDF (para entregárselo)",
)
def estado_cuenta_productor_pdf(
    db: DbSession,
    # Mismo tope que la columna String(150), igual que en la ruta del JSON.
    productor: str = Query(..., min_length=2, max_length=150),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    ctx: RequestContext = Depends(require_permission("reventa", "imprimir")),
) -> Response:
    contenido, filename = ReventaResumenService(db, ctx).estado_cuenta_productor_pdf(
        productor, desde, hasta
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/sugerencias",
    response_model=SugerenciasReventa,
    summary="Nombres ya usados de productores y clientes (autocompletar)",
)
def sugerencias(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> SugerenciasReventa:
    return ReventaResumenService(db, ctx).sugerencias()


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


@router.put("/compras/{entity_id}", response_model=CompraQuesoRead, summary="Editar compra (recalcula el estado con los abonos ya hechos)")
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


@router.delete(
    "/compras/{entity_id}/abonos/{abono_id}",
    response_model=CompraQuesoRead,
    summary="Eliminar un abono mal registrado de la compra",
)
def eliminar_abono_compra(
    entity_id: uuid.UUID,
    abono_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).eliminar_abono(entity_id, abono_id)


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


@router.put("/ventas/{entity_id}", response_model=VentaQuesoRead, summary="Editar venta (recalcula el estado con los abonos ya hechos)")
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


@router.delete(
    "/ventas/{entity_id}/abonos/{abono_id}",
    response_model=VentaQuesoRead,
    summary="Eliminar un abono mal registrado de la venta",
)
def eliminar_abono_venta(
    entity_id: uuid.UUID,
    abono_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).eliminar_abono(entity_id, abono_id)


@router.post("/ventas/{entity_id}/anular", response_model=VentaQuesoRead, summary="Anular venta")
def anular_venta(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "administrar")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).anular(entity_id)


# ---------------------------------------------- saldos de la cuenta anterior
@router.get(
    "/saldos-anteriores",
    response_model=Page[SaldoAnteriorRead],
    summary="Listar saldos traídos del sistema anterior",
)
def listar_saldos_anteriores(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
    tipo: str | None = Query(None, description="'cobrar' (clientes) o 'pagar' (productores)"),
    search: str | None = Query(None),
    estado: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[SaldoAnteriorRead]:
    items, total = SaldoAnteriorService(db, ctx).listar_filtrado(
        params, tipo=tipo, search=search, estado=estado, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@router.post(
    "/saldos-anteriores",
    response_model=SaldoAnteriorRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cargar un saldo del sistema anterior (no toca inventario ni ganancia)",
)
def crear_saldo_anterior(
    payload: SaldoAnteriorCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).crear(payload)


@router.put(
    "/saldos-anteriores/{entity_id}",
    response_model=SaldoAnteriorRead,
    summary="Editar saldo anterior (recalcula el estado con los abonos ya hechos)",
)
def editar_saldo_anterior(
    entity_id: uuid.UUID,
    payload: SaldoAnteriorUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).actualizar(entity_id, payload)


@router.delete(
    "/saldos-anteriores/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar saldo anterior",
)
def eliminar_saldo_anterior(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    SaldoAnteriorService(db, ctx).eliminar(entity_id)


@router.post(
    "/saldos-anteriores/{entity_id}/abonos",
    response_model=SaldoAnteriorRead,
    summary="Registrar un abono sobre un saldo del sistema anterior",
)
def abonar_saldo_anterior(
    entity_id: uuid.UUID,
    payload: AbonoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).registrar_abono(entity_id, payload)


@router.delete(
    "/saldos-anteriores/{entity_id}/abonos/{abono_id}",
    response_model=SaldoAnteriorRead,
    summary="Eliminar un abono mal registrado del saldo anterior",
)
def eliminar_abono_saldo_anterior(
    entity_id: uuid.UUID,
    abono_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).eliminar_abono(entity_id, abono_id)


@router.post(
    "/saldos-anteriores/{entity_id}/anular",
    response_model=SaldoAnteriorRead,
    summary="Anular saldo anterior",
)
def anular_saldo_anterior(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "administrar")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).anular(entity_id)
