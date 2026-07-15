from app.common.crud_router import build_crud_router
from app.modules.clientes.schemas import ClienteCreate, ClienteRead, ClienteUpdate
from app.modules.clientes.service import ClienteService

router = build_crud_router(
    modulo="clientes",
    service_cls=ClienteService,
    read_schema=ClienteRead,
    create_schema=ClienteCreate,
    update_schema=ClienteUpdate,
    tags=["Clientes"],
)
