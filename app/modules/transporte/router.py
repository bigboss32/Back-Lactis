import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, UploadFile, status

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.transporte.schemas import (
    AbonoFleteCreate,
    AlertasTransporte,
    CarteraFleteCliente,
    CarteraFleteDetalle,
    DocumentoCreate,
    DocumentoRead,
    DocumentoUpdate,
    MantenimientoCreate,
    MantenimientoRead,
    MantenimientoUpdate,
    ResumenTransporte,
    VehiculoCreate,
    VehiculoGastoCreate,
    VehiculoGastoRead,
    VehiculoGastoUpdate,
    VehiculoRead,
    VehiculoUpdate,
    ViajeCreate,
    ViajeDetalleRead,
    ViajeFinalizar,
    ViajeGastoCreate,
    ViajeRead,
    ViajeServicioCreate,
    ViajeServicioRead,
    ViajeServicioUpdate,
    ViajeUpdate,
)
from app.modules.transporte.service import (
    TransporteReporteService,
    VehiculoDocumentoService,
    VehiculoGastoService,
    VehiculoMantenimientoService,
    VehiculoService,
    ViajeService,
    ViajeServicioService,
)

router = APIRouter(tags=["Transporte"])


# --------------------------------------------------------------------- viajes
@router.get("/viajes", response_model=Page[ViajeRead], summary="Listar viajes")
def listar_viajes(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None, description="Origen, destino o conductor"),
    estado: str | None = Query(None, description="en_curso | finalizado | anulado"),
    vehiculo_id: uuid.UUID | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[ViajeRead]:
    items, total = ViajeService(db, ctx).listar_filtrado(
        params, search=search, estado=estado, vehiculo_id=vehiculo_id,
        desde=desde, hasta=hasta,
    )
    return Page.build(items, total, params)


@router.post(
    "/viajes", response_model=ViajeDetalleRead, status_code=status.HTTP_201_CREATED,
    summary="Registrar un viaje (queda en curso, con consecutivo por empresa)",
)
def crear_viaje(
    payload: ViajeCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "crear")),
) -> ViajeDetalleRead:
    return ViajeService(db, ctx).crear(payload)


@router.get(
    "/viajes/{viaje_id}", response_model=ViajeDetalleRead,
    summary="Detalle del viaje (es el reporte de rentabilidad: servicios, gastos y totales)",
)
def obtener_viaje(
    viaje_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
) -> ViajeDetalleRead:
    return ViajeService(db, ctx).obtener(viaje_id)


@router.put("/viajes/{viaje_id}", response_model=ViajeDetalleRead, summary="Editar viaje (solo en curso)")
def editar_viaje(
    viaje_id: uuid.UUID,
    payload: ViajeUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "editar")),
) -> ViajeDetalleRead:
    return ViajeService(db, ctx).actualizar(viaje_id, payload)


@router.delete(
    "/viajes/{viaje_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar viaje (solo sin abonos)",
)
def eliminar_viaje(
    viaje_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "eliminar")),
) -> None:
    ViajeService(db, ctx).eliminar(viaje_id)


@router.post(
    "/viajes/{viaje_id}/finalizar", response_model=ViajeDetalleRead,
    summary="Finalizar el viaje (bloquea servicios y gastos; los abonos siguen abiertos)",
)
def finalizar_viaje(
    viaje_id: uuid.UUID,
    payload: ViajeFinalizar,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "editar")),
) -> ViajeDetalleRead:
    return ViajeService(db, ctx).finalizar(viaje_id, payload)


@router.post(
    "/viajes/{viaje_id}/reabrir", response_model=ViajeDetalleRead,
    summary="Reabrir un viaje finalizado para corregirlo",
)
def reabrir_viaje(
    viaje_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "editar")),
) -> ViajeDetalleRead:
    return ViajeService(db, ctx).reabrir(viaje_id)


@router.post(
    "/viajes/{viaje_id}/anular", response_model=ViajeDetalleRead,
    summary="Anular el viaje y sus servicios en cascada (exige cero abonos)",
)
def anular_viaje(
    viaje_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "administrar")),
) -> ViajeDetalleRead:
    return ViajeService(db, ctx).anular(viaje_id)


# ------------------------------------------------------------------ servicios
@router.post(
    "/viajes/{viaje_id}/servicios", response_model=ViajeServicioRead,
    status_code=status.HTTP_201_CREATED,
    summary="Agregar un flete al viaje (tercero por kilo o precio fijo, o queso propio)",
)
def crear_servicio(
    viaje_id: uuid.UUID,
    payload: ViajeServicioCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "crear")),
) -> ViajeServicioRead:
    return ViajeServicioService(db, ctx).crear_en_viaje(viaje_id, payload)


@router.put(
    "/viajes/{viaje_id}/servicios/{servicio_id}", response_model=ViajeServicioRead,
    summary="Editar un flete (solo con el viaje en curso)",
)
def editar_servicio(
    viaje_id: uuid.UUID,
    servicio_id: uuid.UUID,
    payload: ViajeServicioUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "editar")),
) -> ViajeServicioRead:
    return ViajeServicioService(db, ctx).actualizar_en_viaje(viaje_id, servicio_id, payload)


@router.delete(
    "/viajes/{viaje_id}/servicios/{servicio_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un flete (solo sin abonos y con el viaje en curso)",
)
def eliminar_servicio(
    viaje_id: uuid.UUID,
    servicio_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "eliminar")),
) -> None:
    ViajeServicioService(db, ctx).eliminar_en_viaje(viaje_id, servicio_id)


@router.post(
    "/viajes/{viaje_id}/servicios/{servicio_id}/anular", response_model=ViajeServicioRead,
    summary="Anular un flete (exige cero abonos)",
)
def anular_servicio(
    viaje_id: uuid.UUID,
    servicio_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "administrar")),
) -> ViajeServicioRead:
    return ViajeServicioService(db, ctx).anular_en_viaje(viaje_id, servicio_id)


# --------------------------------------------------------------------- abonos
@router.post(
    "/servicios/{servicio_id}/abonos", response_model=ViajeServicioRead,
    summary="Registrar un abono del cliente (permitido aunque el viaje esté finalizado)",
)
def abonar_servicio(
    servicio_id: uuid.UUID,
    payload: AbonoFleteCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "crear")),
) -> ViajeServicioRead:
    return ViajeServicioService(db, ctx).registrar_abono(servicio_id, payload)


@router.delete(
    "/servicios/{servicio_id}/abonos/{abono_id}", response_model=ViajeServicioRead,
    summary="Eliminar un abono mal registrado del flete",
)
def eliminar_abono_servicio(
    servicio_id: uuid.UUID,
    abono_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "crear")),
) -> ViajeServicioRead:
    return ViajeServicioService(db, ctx).eliminar_abono(servicio_id, abono_id)


# ----------------------------------------------------------- gastos del viaje
@router.get(
    "/viajes/{viaje_id}/gastos", response_model=list[VehiculoGastoRead],
    summary="Gastos del viaje",
)
def listar_gastos_viaje(
    viaje_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
) -> list[VehiculoGastoRead]:
    return VehiculoGastoService(db, ctx).listar_de_viaje(viaje_id)


@router.post(
    "/viajes/{viaje_id}/gastos", response_model=VehiculoGastoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un gasto del viaje (fija el viaje y su vehículo)",
)
def crear_gasto_viaje(
    viaje_id: uuid.UUID,
    payload: ViajeGastoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "crear")),
) -> VehiculoGastoRead:
    return VehiculoGastoService(db, ctx).crear_en_viaje(viaje_id, payload)


# ------------------------------------------------------------------- reportes
@router.get(
    "/cartera", response_model=list[CarteraFleteCliente],
    summary="Cartera de fletes por cliente (saldos pendientes)",
)
def cartera(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
) -> list[CarteraFleteCliente]:
    return TransporteReporteService(db, ctx).cartera()


@router.get(
    "/cartera/detalle", response_model=CarteraFleteDetalle,
    summary="Servicios con saldo de un cliente (del directorio u ocasional)",
)
def cartera_detalle(
    db: DbSession,
    cliente_id: uuid.UUID | None = Query(None),
    cliente_nombre: str | None = Query(None, max_length=150),
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
) -> CarteraFleteDetalle:
    return TransporteReporteService(db, ctx).cartera_detalle(cliente_id, cliente_nombre)


@router.get(
    "/resumen-mensual", response_model=ResumenTransporte,
    summary="Resumen del período: viajes, kilos, ingresos, gastos y utilidad",
)
def resumen_mensual(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    vehiculo_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
) -> ResumenTransporte:
    return TransporteReporteService(db, ctx).resumen(desde, hasta, vehiculo_id)


@router.get(
    "/alertas", response_model=AlertasTransporte,
    summary="Documentos por vencer y mantenimientos próximos (solo el vigente por vehículo y tipo)",
)
def alertas(
    db: DbSession,
    dias: int = Query(30, ge=0, le=365, description="Umbral de días al vencimiento"),
    umbral_km: Decimal = Query(Decimal("500"), ge=0, description="Umbral de km al próximo mantenimiento"),
    vehiculo_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
) -> AlertasTransporte:
    return TransporteReporteService(db, ctx).alertas(dias, umbral_km, vehiculo_id)


# ------------------------------------------------------------ CRUD vehículos
vehiculos_router = build_crud_router(
    modulo="transporte",
    service_cls=VehiculoService,
    read_schema=VehiculoRead,
    create_schema=VehiculoCreate,
    update_schema=VehiculoUpdate,
    tags=["Transporte"],
)


# --------------------------------------------------- CRUD gastos del vehículo
gastos_router = build_crud_router(
    modulo="transporte",
    service_cls=VehiculoGastoService,
    read_schema=VehiculoGastoRead,
    create_schema=VehiculoGastoCreate,
    update_schema=VehiculoGastoUpdate,
    tags=["Transporte"],
)


@gastos_router.get(
    "/filtrar/avanzado", response_model=Page[VehiculoGastoRead],
    summary="Listar gastos del vehículo con filtros",
)
def filtrar_gastos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None),
    vehiculo_id: uuid.UUID | None = Query(None),
    viaje_id: uuid.UUID | None = Query(None),
    categoria: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    solo_generales: bool = Query(False, description="Solo gastos sin viaje"),
) -> Page[VehiculoGastoRead]:
    items, total = VehiculoGastoService(db, ctx).listar_filtrado(
        params, search=search, vehiculo_id=vehiculo_id, viaje_id=viaje_id,
        categoria=categoria, desde=desde, hasta=hasta, solo_generales=solo_generales,
    )
    return Page.build(items, total, params)


@gastos_router.post(
    "/{entity_id}/adjunto", response_model=VehiculoGastoRead,
    summary="Adjuntar factura o soporte del gasto",
)
def adjuntar_gasto(
    entity_id: uuid.UUID,
    file: UploadFile,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "editar")),
) -> VehiculoGastoRead:
    return VehiculoGastoService(db, ctx).adjuntar_archivo(entity_id, file)


# ------------------------------------------------------- CRUD mantenimientos
mantenimientos_router = build_crud_router(
    modulo="transporte",
    service_cls=VehiculoMantenimientoService,
    read_schema=MantenimientoRead,
    create_schema=MantenimientoCreate,
    update_schema=MantenimientoUpdate,
    tags=["Transporte"],
)


@mantenimientos_router.get(
    "/filtrar/avanzado", response_model=Page[MantenimientoRead],
    summary="Listar mantenimientos con filtros",
)
def filtrar_mantenimientos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None),
    vehiculo_id: uuid.UUID | None = Query(None),
    tipo: str | None = Query(None, description="preventivo | correctivo"),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[MantenimientoRead]:
    items, total = VehiculoMantenimientoService(db, ctx).listar_filtrado(
        params, search=search, vehiculo_id=vehiculo_id, tipo=tipo, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@mantenimientos_router.post(
    "/{entity_id}/adjunto", response_model=MantenimientoRead,
    summary="Adjuntar factura o soporte del mantenimiento",
)
def adjuntar_mantenimiento(
    entity_id: uuid.UUID,
    file: UploadFile,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "editar")),
) -> MantenimientoRead:
    return VehiculoMantenimientoService(db, ctx).adjuntar_archivo(entity_id, file)


# ------------------------------------------------------------ CRUD documentos
documentos_router = build_crud_router(
    modulo="transporte",
    service_cls=VehiculoDocumentoService,
    read_schema=DocumentoRead,
    create_schema=DocumentoCreate,
    update_schema=DocumentoUpdate,
    tags=["Transporte"],
)


@documentos_router.get(
    "/filtrar/avanzado", response_model=Page[DocumentoRead],
    summary="Listar documentos del vehículo con filtros (rango por vencimiento)",
)
def filtrar_documentos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None),
    vehiculo_id: uuid.UUID | None = Query(None),
    tipo: str | None = Query(None, description="soat | tecnomecanica | seguro | impuesto | otro"),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[DocumentoRead]:
    items, total = VehiculoDocumentoService(db, ctx).listar_filtrado(
        params, search=search, vehiculo_id=vehiculo_id, tipo=tipo, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@documentos_router.post(
    "/{entity_id}/adjunto", response_model=DocumentoRead,
    summary="Adjuntar el documento escaneado",
)
def adjuntar_documento(
    entity_id: uuid.UUID,
    file: UploadFile,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("transporte", "editar")),
) -> DocumentoRead:
    return VehiculoDocumentoService(db, ctx).adjuntar_archivo(entity_id, file)
