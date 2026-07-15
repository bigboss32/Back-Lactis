from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.modules.reportes.schemas import DashboardResponse
from app.modules.reportes.service import ReporteService

router = APIRouter(tags=["Reportes"])

EXCEL_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _excel_response(contenido: bytes, filename: str) -> Response:
    return Response(
        content=contenido,
        media_type=EXCEL_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/dashboard", response_model=DashboardResponse, summary="Dashboard ejecutivo con indicadores")
def dashboard(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reportes", "consultar")),
) -> DashboardResponse:
    return ReporteService(db, ctx).dashboard()


@router.get("/export/recepciones", summary="Excel de litros y transporte (grilla proveedor × día)")
def export_recepciones(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("reportes", "exportar")),
) -> Response:
    contenido, filename = ReporteService(db, ctx).exportar_recepciones_quincena(desde, hasta)
    return _excel_response(contenido, filename)


@router.get("/export/ventas", summary="Excel de ventas del período")
def export_ventas(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("reportes", "exportar")),
) -> Response:
    contenido, filename = ReporteService(db, ctx).exportar_ventas(desde, hasta)
    return _excel_response(contenido, filename)


@router.get("/export/gastos", summary="Excel de gastos del período")
def export_gastos(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("reportes", "exportar")),
) -> Response:
    contenido, filename = ReporteService(db, ctx).exportar_gastos(desde, hasta)
    return _excel_response(contenido, filename)
