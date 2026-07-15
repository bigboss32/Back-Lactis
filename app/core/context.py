"""Contexto de la petición: usuario autenticado, empresa activa e IP de origen."""
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestContext:
    user: Any | None = None
    user_id: uuid.UUID | None = None
    empresa_id: uuid.UUID | None = None
    sucursal_id: uuid.UUID | None = None
    roles: list[str] = field(default_factory=list)
    permisos: set[tuple[str, str]] = field(default_factory=set)
    is_superadmin: bool = False
    ip: str | None = None

    def tiene_permiso(self, modulo: str, accion: str) -> bool:
        return self.is_superadmin or (modulo, accion) in self.permisos


def system_context(empresa_id: uuid.UUID | None = None) -> RequestContext:
    """Contexto para procesos internos (seeds, tareas programadas)."""
    return RequestContext(empresa_id=empresa_id, is_superadmin=True, roles=["system"])
