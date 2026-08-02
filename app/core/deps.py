"""Dependencias de inyección: sesión de BD, usuario actual, contexto multi-tenant,
verificación de permisos RBAC y paywall de suscripción.
"""
import uuid
from collections.abc import Callable
from datetime import date
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


# Módulos accesibles aunque la suscripción esté vencida: el propio módulo de
# suscripción ES el paywall — sin él la empresa bloqueada no podría pagar para
# desbloquearse. Nótese que /auth/me usa get_context (no require_permission),
# así que la sesión sigue viva para mostrar el aviso y el botón de pagar.
MODULOS_EXENTOS_SUSCRIPCION: tuple[str, ...] = ("suscripcion",)


def _verificar_suscripcion_vigente(
    request: Request, db: Session, ctx: RequestContext, modulo: str
) -> None:
    """Paywall: con la suscripción vencida más allá de la gracia se corta el
    acceso a los módulos de negocio. El superadmin nunca se bloquea."""
    if ctx.is_superadmin or ctx.empresa_id is None or modulo in MODULOS_EXENTOS_SUSCRIPCION:
        return
    # Memoizado en request.state: varios chequeos de permiso en la misma
    # petición no repiten la consulta ni el cálculo.
    if not hasattr(request.state, "suscripcion_bloqueo"):
        # Imports perezosos, como en get_current_user: deps.py lo importa todo el mundo
        from app.modules.empresas.models import Empresa
        from app.modules.suscripcion.estado import ESTADO_BLOQUEADA, estado_suscripcion

        empresa = db.get(Empresa, ctx.empresa_id)
        # Si la empresa no está, CIERRA. No abre.
        #
        # Antes esto caía en un `bloqueo = None` y la empresa borrada pasaba a
        # TODOS los módulos sin pagar: el soft delete le quitaba el cobro al
        # cliente en vez de quitarle el acceso, que es justo lo contrario de lo
        # que espera quien borra un cliente moroso. Cuando no se puede
        # establecer que está al día, hay que cerrar.
        if empresa is None or empresa.deleted_at is not None:
            raise ForbiddenError(
                "La empresa no está disponible. Contacte al administrador",
                code="empresa_no_disponible",
            )
        resultado = estado_suscripcion(
            exenta=empresa.exenta,
            pagada_hasta=empresa.pagada_hasta,
            creada=empresa.created_at.date(),
            hoy=date.today(),
            dias_aviso=settings.SUSCRIPCION_DIAS_AVISO,
            dias_gracia=settings.SUSCRIPCION_DIAS_GRACIA,
            dias_prueba=settings.SUSCRIPCION_DIAS_PRUEBA,
        )
        bloqueo = None
        if resultado.estado == ESTADO_BLOQUEADA:
            bloqueo = {
                "pagada_hasta": resultado.limite.isoformat(),
                "dias_vencidos": -resultado.dias_restantes,
            }
        request.state.suscripcion_bloqueo = bloqueo
    if request.state.suscripcion_bloqueo is not None:
        raise ForbiddenError(
            "La suscripción de la empresa está vencida. Regularice el pago para continuar",
            code="suscripcion_vencida",
            extra=request.state.suscripcion_bloqueo,
        )


def require_permission(modulo: str, accion: str) -> Callable[..., RequestContext]:
    def dependency(request: Request, db: DbSession, ctx: Context) -> RequestContext:
        if not ctx.tiene_permiso(modulo, accion):
            raise ForbiddenError(f"No tiene permiso para '{accion}' en el módulo '{modulo}'")
        _verificar_suscripcion_vigente(request, db, ctx, modulo)
        return ctx

    return dependency


def require_any_permission(modulo: str, *acciones: str) -> Callable[..., RequestContext]:
    """Deja pasar con CUALQUIERA de las acciones indicadas.

    Existe para operaciones que son la misma cosa vista desde dos lados. El caso
    que la trajo: adjuntar el soporte de pago a una compra de reventa. Para quien
    acaba de registrar la compra eso es parte de 'crear'; para quien le agrega la
    foto al día siguiente es 'editar'. Con `require_permission` habría que
    escoger una, y el rol 'Compras' —que tiene las dos— pasaría igual, pero
    cualquier rol futuro que solo tuviera 'crear' no podría adjuntar el soporte
    de la compra que él mismo acaba de registrar.

    OJO: esto AFLOJA el permiso, así que no se usa para nada destructivo.
    Eliminar un adjunto sigue exigiendo 'eliminar' a secas, con
    `require_permission`: en este proyecto ya se coló un borrado pidiendo 'crear'
    (los abonos) y no se repite.
    """
    if not acciones:  # pragma: no cover - error de programación, no de datos
        raise ValueError("require_any_permission necesita al menos una acción")

    def dependency(request: Request, db: DbSession, ctx: Context) -> RequestContext:
        if not any(ctx.tiene_permiso(modulo, accion) for accion in acciones):
            listado = "' o '".join(acciones)
            raise ForbiddenError(
                f"No tiene permiso para '{listado}' en el módulo '{modulo}'"
            )
        _verificar_suscripcion_vigente(request, db, ctx, modulo)
        return ctx

    return dependency
