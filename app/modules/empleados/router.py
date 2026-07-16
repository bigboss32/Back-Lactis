from app.common.crud_router import build_crud_router
from app.modules.empleados.schemas import EmpleadoCreate, EmpleadoRead, EmpleadoUpdate
from app.modules.empleados.service import EmpleadoService

router = build_crud_router(
    modulo="empleados",
    service_cls=EmpleadoService,
    read_schema=EmpleadoRead,
    create_schema=EmpleadoCreate,
    update_schema=EmpleadoUpdate,
    tags=["Empleados"],
)
