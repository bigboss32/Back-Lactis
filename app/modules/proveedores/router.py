from app.common.crud_router import build_crud_router
from app.modules.proveedores.schemas import ProveedorCreate, ProveedorRead, ProveedorUpdate
from app.modules.proveedores.service import ProveedorService

router = build_crud_router(
    modulo="proveedores",
    service_cls=ProveedorService,
    read_schema=ProveedorRead,
    create_schema=ProveedorCreate,
    update_schema=ProveedorUpdate,
    tags=["Proveedores de Leche"],
)
