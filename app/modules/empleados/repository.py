from app.common.repository import BaseRepository
from app.modules.empleados.models import Empleado


class EmpleadoRepository(BaseRepository[Empleado]):
    model = Empleado
    search_fields = ("nombre", "apellido", "documento", "cargo")
    default_order_by = "nombre"
