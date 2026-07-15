from app.common.crud_router import build_crud_router
from app.modules.sucursales.schemas import SucursalCreate, SucursalRead, SucursalUpdate
from app.modules.sucursales.service import SucursalService

router = build_crud_router(
    modulo="sucursales",
    service_cls=SucursalService,
    read_schema=SucursalRead,
    create_schema=SucursalCreate,
    update_schema=SucursalUpdate,
    tags=["Sucursales"],
)
