"""Dependencias de inyección: sesión de BD, usuario actual, contexto multi-tenant
y verificación de permisos RBAC.
"""
import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.context import RequestContext
from app.core.database import get_db
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.permissions import ROL_SUPERADMIN
from app.core.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_PREFIX}/auth/login", auto_error=False
)

DbSession = Annotated[Session, Depends(get_db)]


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def get_current_user(db: DbSession, token: str | None = Depends(oauth2_scheme)):
    from app.modules.usuarios.models import Rol, Usuario, UsuarioRol

    if not token:
        raise UnauthorizedError("No autenticado")
    payload = decode_token(token, "access")
    user_id = uuid.UUID(payload["sub"])
    user = db.scalars(
        select(Usuario)
        .options(
            selectinload(Usuario.asignaciones)
            .joinedload(UsuarioRol.rol)
            .selectinload(Rol.permisos)
        )
        .where(Usuario.id == user_id, Usuario.deleted_at.is_(None))
    ).first()
    if user is None:
        raise UnauthorizedError("Usuario no existe")
    if user.bloqueado:
        raise ForbiddenError("Usuario bloqueado. Contacte al administrador")
    if user.estado != "activo":
        raise ForbiddenError("Usuario inactivo")
    return user


def get_context(
    request: Request,
    db: DbSession,
    user=Depends(get_current_user),
    x_empresa_id: Annotated[str | None, Header(alias="X-Empresa-Id")] = None,
) -> RequestContext:
    # Superadmin = tiene el rol global (fila sin empresa) de Administrador General
    is_superadmin = any(
        asignacion.empresa_id is None
        and asignacion.rol is not None
        and asignacion.rol.nombre == ROL_SUPERADMIN
        for asignacion in user.asignaciones
    )
    membresias = user.empresas_ids
    empresa_id = user.empresa_id
    # El header X-Empresa-Id elige la empresa activa: el superadmin puede operar
    # sobre cualquiera; un usuario normal solo sobre empresas de las que es miembro
    if x_empresa_id:
        try:
            empresa_header = uuid.UUID(x_empresa_id)
        except ValueError as exc:
            raise ForbiddenError("X-Empresa-Id inválido") from exc
        if not is_superadmin and empresa_header not in membresias:
            raise ForbiddenError("No pertenece a la empresa indicada")
        empresa_id = empresa_header
    # Un usuario normal sin empresa resuelta no puede operar: sin este cierre
    # vería datos sin filtro tenant (p. ej. usuarios de todas las empresas)
    if not is_superadmin and empresa_id is None:
        raise ForbiddenError("El usuario no tiene una empresa asignada. Contacte al administrador")
    # Roles y permisos SOLO de la empresa activa (más los globales)
    roles_ctx = user.roles_en(empresa_id)
    return RequestContext(
        user=user,
        user_id=user.id,
        empresa_id=empresa_id,
        sucursal_id=user.sucursal_id,
        empresa_ids=membresias,
        roles=[rol.nombre for rol in roles_ctx],
        permisos={
            (permiso.modulo, permiso.accion)
            for rol in roles_ctx
            for permiso in rol.permisos
        },
        is_superadmin=is_superadmin,
        ip=_client_ip(request),
    )


Context = Annotated[RequestContext, Depends(get_context)]


def require_permission(modulo: str, accion: str) -> Callable[..., RequestContext]:
    def dependency(ctx: Context) -> RequestContext:
        if not ctx.tiene_permiso(modulo, accion):
            raise ForbiddenError(f"No tiene permiso para '{accion}' en el módulo '{modulo}'")
        return ctx

    return dependency
