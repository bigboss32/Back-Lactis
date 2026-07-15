import uuid

from fastapi import APIRouter, Depends, UploadFile

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.usuarios.schemas import (
    AsignarPermisos,
    AsignarRoles,
    CambiarPasswordAdmin,
    PermisoRead,
    RolCreate,
    RolRead,
    RolUpdate,
    UsuarioCreate,
    UsuarioRead,
    UsuarioUpdate,
)
from app.modules.usuarios.service import PermisoService, RolService, UsuarioService

# --------------------------------------------------------------------- usuarios
router = build_crud_router(
    modulo="usuarios",
    service_cls=UsuarioService,
    read_schema=UsuarioRead,
    create_schema=UsuarioCreate,
    update_schema=UsuarioUpdate,
    tags=["Usuarios"],
)


@router.post("/{entity_id}/roles", response_model=UsuarioRead, summary="Asignar roles a un usuario")
def asignar_roles(
    entity_id: uuid.UUID,
    payload: AsignarRoles,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("usuarios", "administrar")),
) -> UsuarioRead:
    return UsuarioService(db, ctx).asignar_roles(entity_id, payload.rol_ids)


@router.post("/{entity_id}/bloquear", response_model=UsuarioRead, summary="Bloquear usuario")
def bloquear(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("usuarios", "administrar")),
) -> UsuarioRead:
    return UsuarioService(db, ctx).bloquear(entity_id, True)


@router.post("/{entity_id}/desbloquear", response_model=UsuarioRead, summary="Desbloquear usuario")
def desbloquear(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("usuarios", "administrar")),
) -> UsuarioRead:
    return UsuarioService(db, ctx).bloquear(entity_id, False)


@router.post(
    "/{entity_id}/restablecer-password",
    response_model=UsuarioRead,
    summary="Restablecer contraseña de un usuario",
)
def restablecer_password(
    entity_id: uuid.UUID,
    payload: CambiarPasswordAdmin,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("usuarios", "administrar")),
) -> UsuarioRead:
    return UsuarioService(db, ctx).restablecer_password(entity_id, payload.password)


@router.post("/{entity_id}/foto", response_model=UsuarioRead, summary="Subir foto de perfil")
def subir_foto(
    entity_id: uuid.UUID,
    file: UploadFile,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("usuarios", "editar")),
) -> UsuarioRead:
    return UsuarioService(db, ctx).subir_foto(entity_id, file)


# ------------------------------------------------------------------------ roles
roles_router = build_crud_router(
    modulo="roles",
    service_cls=RolService,
    read_schema=RolRead,
    create_schema=RolCreate,
    update_schema=RolUpdate,
    tags=["Roles y Permisos"],
)


@roles_router.put("/{entity_id}/permisos", response_model=RolRead, summary="Asignar permisos a un rol")
def asignar_permisos(
    entity_id: uuid.UUID,
    payload: AsignarPermisos,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("roles", "administrar")),
) -> RolRead:
    return RolService(db, ctx).asignar_permisos(entity_id, payload.permiso_ids)


# --------------------------------------------------------------------- permisos
permisos_router = APIRouter(tags=["Roles y Permisos"])


@permisos_router.get("", response_model=Page[PermisoRead], summary="Catálogo de permisos")
def listar_permisos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("roles", "consultar")),
    params: PageParams = Depends(page_params),
    modulo: str | None = None,
) -> Page[PermisoRead]:
    items, total = PermisoService(db, ctx).listar(
        params, filters={"modulo": modulo} if modulo else None
    )
    return Page.build(items, total, params)
