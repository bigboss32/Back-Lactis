from app.common.crud_router import build_crud_router
from app.modules.transportadores.schemas import (
    TransportadorCreate,
    TransportadorRead,
    TransportadorUpdate,
)
from app.modules.transportadores.service import TransportadorService

router = build_crud_router(
    modulo="transportadores",
    service_cls=TransportadorService,
    read_schema=TransportadorRead,
    create_schema=TransportadorCreate,
    update_schema=TransportadorUpdate,
    tags=["Transportadores"],
)
