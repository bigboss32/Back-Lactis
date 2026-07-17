from fastapi import APIRouter, Depends

from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.modules.reportes.schemas import DashboardResponse
from app.modules.reportes.service import ReporteService

router = APIRouter(tags=["Reportes"])


@router.get("/dashboard", response_model=DashboardResponse, summary="Dashboard ejecutivo con indicadores")
def dashboard(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reportes", "consultar")),
) -> DashboardResponse:
    return ReporteService(db, ctx).dashboard()
