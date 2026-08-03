import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.exceptions import BusinessError
from app.core.pagination import Page, PageParams, page_params
from app.modules.produccion.schemas import (
    CicloCerrar,
    CicloDespachoRead,
    CicloPropuesta,
    CiclosPanel,
    LotesProduccionPanel,
    ProduccionCreate,
    ProduccionRead,
    ProduccionUpdate,
    TipoQuesoCreate,
    TipoQuesoRead,
    TipoQuesoUpdate,
)
from app.modules.produccion.service import (
    CicloDespachoService,
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


# --------------------------------------------------- ciclos de despacho
# El queso se pesa al hacerlo y al venderlo, y entre las dos se seca. Esa
# diferencia se queda en la bodega como queso que no existe hasta que se cierra
# el ciclo, que es el único momento en que la resta "producido − salido" se
# puede hacer sin adivinar. Ver `CicloDespachoService` para el porqué completo.
@router.get(
    "/ciclos",
    response_model=CiclosPanel,
    summary="Ciclos de despacho cerrados, y el que toca cerrar ahora",
)
def panel_ciclos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("produccion", "consultar")),
) -> CiclosPanel:
    """La lista de ciclos con la merma que se aceptó en cada uno, y la PROPUESTA
    del que sigue con su cuenta ya hecha.

    La propuesta es la mitad del asunto: el ciclo se repite cada semana, y si
    hubiera que acordarse de abrirlo y cerrarlo, en tres semanas nadie lo haría y
    los kilos fantasma volverían a acumularse.
    """
    return CicloDespachoService(db, ctx).panel()


@router.get(
    "/ciclos/propuesta",
    response_model=CicloPropuesta | None,
    summary="La cuenta de un ciclo antes de cerrarlo (no escribe nada)",
)
def propuesta_ciclo(
    db: DbSession,
    desde: date | None = Query(None, description="Primer día del ciclo"),
    hasta: date | None = Query(None, description="Último día del ciclo"),
    ctx: RequestContext = Depends(require_permission("produccion", "consultar")),
) -> CicloPropuesta | None:
    """Se produjeron X kg, salieron Y, ya se habían bajado Z a mano, la diferencia
    son W kg que valen $V. Con el desglose por tipo de queso y por tanda.

    Sin `desde`/`hasta` propone el ciclo que sigue: arranca el día después del
    último cierre y dura siete días, sin pasar de hoy. Con fechas, calcula el
    rango que se le pida, que es lo que permite corregir la propuesta antes de
    aceptarla.

    NO ESCRIBE NADA. Es la pantalla que el dueño lee antes de decidir.
    """
    if desde and hasta and hasta < desde:
        raise BusinessError("La fecha final no puede ser anterior a la inicial")
    return CicloDespachoService(db, ctx).propuesta(desde, hasta)


@router.post(
    "/ciclos/cerrar",
    response_model=CicloDespachoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cerrar el ciclo: registra la merma y la baja de la bodega",
)
def cerrar_ciclo(
    payload: CicloCerrar,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("produccion", "editar")),
) -> CicloDespachoRead:
    """ESTO SÍ ESCRIBE: es plata que se da por perdida.

    Reparte la merma del ciclo entre sus tandas a prorrata de los kilos de cada
    una, y por cada parte crea un ajuste de inventario hacia abajo. El queso
    fantasma sale de la bodega, su costo se le resta al lote y la utilidad por
    lote pasa a ser la real.

    Si la cuenta huele mal —merma negativa o desproporcionada— no pasa sin
    `aceptar_advertencias`: puede ser una venta sin anotar y no queso secándose.

    El permiso es `produccion:editar`, el mismo que usan cerrar y reabrir las
    temporadas de reventa, que es la operación equivalente del otro módulo.
    """
    servicio = CicloDespachoService(db, ctx)
    return servicio._leer(servicio.cerrar(payload))


@router.post(
    "/ciclos/{entity_id}/reabrir",
    response_model=CicloDespachoRead,
    summary="Reabrir un ciclo cerrado por equivocación (deshace su merma)",
)
def reabrir_ciclo(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("produccion", "editar")),
) -> CicloDespachoRead:
    """Borra los ajustes de inventario que creó el cierre: el queso vuelve a la
    bodega, el costo vuelve al lote y la utilidad vuelve a lo que decía antes."""
    servicio = CicloDespachoService(db, ctx)
    return servicio._leer(servicio.reabrir(entity_id))


@router.delete(
    "/ciclos/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un ciclo (solo si está abierto)",
)
def eliminar_ciclo(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("produccion", "eliminar")),
) -> None:
    """Solo se puede borrar un ciclo REABIERTO. Uno cerrado tiene su merma
    registrada en el inventario: borrarlo dejaría kilos dados de baja sin nada
    que los explique."""
    CicloDespachoService(db, ctx).eliminar(entity_id)


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
