from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.modules.contabilidad.schemas import (
    BalanceResponse,
    EstadoResultados,
    LibroDiarioResponse,
)
from app.modules.contabilidad.service import ContabilidadService

router = APIRouter(tags=["Contabilidad"])


@router.get("/libro-diario", response_model=LibroDiarioResponse, summary="Libro diario consolidado")
def libro_diario(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("contabilidad", "consultar")),
) -> LibroDiarioResponse:
    return ContabilidadService(db, ctx).libro_diario(desde, hasta)


@router.get("/estado-resultados", response_model=EstadoResultados, summary="Estado de resultados del período")
def estado_resultados(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("contabilidad", "consultar")),
) -> EstadoResultados:
    return ContabilidadService(db, ctx).estado_resultados(desde, hasta)


@router.get("/balance", response_model=BalanceResponse, summary="Balance de disponible, cartera y por pagar")
def balance(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("contabilidad", "consultar")),
) -> BalanceResponse:
    return ContabilidadService(db, ctx).balance()
