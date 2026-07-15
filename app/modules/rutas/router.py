from app.common.crud_router import build_crud_router
from app.modules.rutas.schemas import RutaCreate, RutaRead, RutaUpdate
from app.modules.rutas.service import RutaService

router = build_crud_router(
    modulo="rutas",
    service_cls=RutaService,
    read_schema=RutaRead,
    create_schema=RutaCreate,
    update_schema=RutaUpdate,
    tags=["Rutas"],
)
