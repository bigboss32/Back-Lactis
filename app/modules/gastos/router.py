import uuid
from datetime import date

from fastapi import Depends, Query, UploadFile, status

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.gastos.schemas import (
    AdjuntosGastoLista,
    CategoriaGastoCreate,
    CategoriaGastoRead,
    CategoriaGastoUpdate,
    EnlaceFacturaCompartida,
    GastoCreate,
    GastoRead,
    GastoUpdate,
)
from app.modules.gastos.service import (
    AdjuntoGastoService,
    CategoriaGastoService,
    GastoService,
)


def _to_read(gasto) -> GastoRead:
    """La fila para la pantalla.

    `adjuntos_count` no se copia a mano: `GastoRead` lee los atributos del modelo
    (`from_attributes`) y `Gasto.adjuntos_count` es una propiedad, así que llega
    sola —y llega también en las respuestas del CRUD genérico, que usan el mismo
    esquema—. `categoria_nombre` sí toca ponerlo: viene de la categoría, no del
    gasto.
    """
    dto = GastoRead.model_validate(gasto)
    dto.categoria_nombre = gasto.categoria.nombre if gasto.categoria else None
    return dto


router = build_crud_router(
    modulo="gastos",
    service_cls=GastoService,
    read_schema=GastoRead,
    create_schema=GastoCreate,
    update_schema=GastoUpdate,
    tags=["Gastos"],
)


@router.get("/filtrar/avanzado", response_model=Page[GastoRead], summary="Listar gastos con filtros")
def filtrar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("gastos", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None),
    categoria_id: uuid.UUID | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[GastoRead]:
    items, total = GastoService(db, ctx).listar_filtrado(
        params, search=search, categoria_id=categoria_id, desde=desde, hasta=hasta
    )
    return Page.build([_to_read(g) for g in items], total, params)


@router.get(
    "/totales/suma",
    response_model=float,
    summary="Suma total de los gastos que cumplen los filtros",
)
def suma_gastos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("gastos", "consultar")),
    search: str | None = Query(None),
    categoria_id: uuid.UUID | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> float:
    """Cuánto suma TODO lo filtrado, no solo la página que se está viendo.

    RECIBE LOS MISMOS FILTROS QUE `/filtrar/avanzado` y no unos parecidos: es la
    cifra que el dueño compara contra las filas de la tabla, sumándolas a mano.
    """
    return float(
        GastoService(db, ctx).suma_filtrada(
            search=search, categoria_id=categoria_id, desde=desde, hasta=hasta
        )
    )


# ------------------------------- facturas del gasto (guardadas en el bucket)
# Antes la factura se subía a la carpeta `uploads/` del servidor, publicada SIN
# CLAVE en `/uploads`: la dirección de la factura de una quesera se abría desde
# cualquier navegador, sin entrar al sistema. Ahora va al bucket privado y se ve
# con enlaces firmados que caducan solos. Ver `AdjuntoGasto` en models.py.
#
# Cuatro rutas y tres permisos distintos, con el mismo criterio de los soportes de
# pago de las liquidaciones, ajustado a que aquí la factura se le cuelga a un gasto
# que ya existe:
#
# - SUBIR con 'editar', EL MISMO PERMISO QUE CORREGIR EL GASTO —y el que ya pedía
#   la subida vieja—: colgarle la factura es completar ese registro.
# - VER con 'consultar', que es lo mismo que ver el gasto.
# - COMPARTIR con 'exportar': el enlace largo saca del sistema un documento con el
#   NIT del proveedor y el valor, y quien lo recibe lo puede reenviar. Deja fuera a
#   los roles de solo consulta, que pueden mirar la factura en pantalla pero no
#   repartirla.
# - BORRAR con 'eliminar', a secas, igual que borrar el gasto.
@router.post(
    "/{entity_id}/adjuntos",
    response_model=AdjuntosGastoLista,
    status_code=status.HTTP_201_CREATED,
    summary="Adjuntar facturas al gasto (varias fotos o PDF)",
)
def subir_adjuntos(
    entity_id: uuid.UUID,
    files: list[UploadFile],
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("gastos", "editar")),
) -> AdjuntosGastoLista:
    """Devuelve la lista completa ya actualizada, con enlaces frescos, para que la
    pantalla no tenga que pedirla otra vez después de subir.

    Las fotos se COMPRIMEN al entrar (1600 px de lado mayor, calidad 75): una foto
    de celular de 4 MB queda en unos 300 KB y se le sigue leyendo el NIT, el
    concepto y el valor. Los PDF pasan derecho, sin tocar — y las facturas suelen
    llegar en PDF.
    """
    return AdjuntoGastoService(db, ctx).subir(files, gasto_id=entity_id)


@router.get(
    "/{entity_id}/adjuntos",
    response_model=AdjuntosGastoLista,
    summary="Facturas del gasto, con enlaces firmados de corta duración",
)
def listar_adjuntos(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("gastos", "consultar")),
) -> AdjuntosGastoLista:
    return AdjuntoGastoService(db, ctx).listar(entity_id)


@router.post(
    "/adjuntos/{adjunto_id}/compartir",
    response_model=EnlaceFacturaCompartida,
    summary="Enlace de más duración para mandar UNA factura por fuera",
)
def compartir_adjunto(
    adjunto_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("gastos", "exportar")),
) -> EnlaceFacturaCompartida:
    """El enlace trae escrito hasta cuándo sirve, en hora de Colombia, porque quien
    lo reparte tiene que saber qué está repartiendo. Queda en la auditoría."""
    return AdjuntoGastoService(db, ctx).compartir(adjunto_id)


@router.delete(
    "/adjuntos/{adjunto_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar una factura (borra también el archivo del almacenamiento)",
)
def eliminar_adjunto(
    adjunto_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("gastos", "eliminar")),
) -> None:
    AdjuntoGastoService(db, ctx).eliminar_adjunto(adjunto_id)


categorias_router = build_crud_router(
    modulo="gastos",
    service_cls=CategoriaGastoService,
    read_schema=CategoriaGastoRead,
    create_schema=CategoriaGastoCreate,
    update_schema=CategoriaGastoUpdate,
    tags=["Gastos"],
)
