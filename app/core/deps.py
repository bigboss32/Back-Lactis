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
    from app.modules.usuarios.models import Rol, Usuario

    if not token:
        raise UnauthorizedError("No autenticado")
    payload = decode_token(token, "access")
    user_id = uuid.UUID(payload["sub"])
    user = db.scalars(
        select(Usuario)
        .options(selectinload(Usuario.roles).selectinload(Rol.permisos))
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
    roles = [rol.nombre for rol in user.roles]
    is_superadmin = ROL_SUPERADMIN in roles
    permisos = {
        (permiso.modulo, permiso.accion)
        for rol in user.roles
        for permiso in rol.permisos
    }
    empresa_id = user.empresa_id
    # El superadmin puede operar sobre cualquier empresa vía header X-Empresa-Id
    if is_superadmin and x_empresa_id:
        try:
            empresa_id = uuid.UUID(x_empresa_id)
        except ValueError as exc:
            raise ForbiddenError("X-Empresa-Id inválido") from exc
    return RequestContext(
        user=user,
        user_id=user.id,
        empresa_id=empresa_id,
        sucursal_id=user.sucursal_id,
        roles=roles,
        permisos=permisos,
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
