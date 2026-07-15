from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm

from app.common.schemas import MessageResponse
from app.core.config import settings
from app.core.deps import Context, DbSession, get_current_user
from app.modules.auth.schemas import (
    CambiarPasswordRequest,
    LogoutRequest,
    PerfilResponse,
    RecuperarPasswordRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.modules.auth.service import AuthService

router = APIRouter(tags=["Autenticación"])


def _client_info(request: Request) -> tuple[str | None, str | None]:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    return ip, request.headers.get("user-agent")


@router.post("/login", response_model=TokenResponse, summary="Iniciar sesión")
def login(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> TokenResponse:
    ip, user_agent = _client_info(request)
    access, refresh = AuthService(db).login(form.username, form.password, ip, user_agent)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse, summary="Renovar tokens")
def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    access, refresh_token = AuthService(db).refresh(payload.refresh_token)
    return TokenResponse(access_token=access, refresh_token=refresh_token)


@router.post("/logout", response_model=MessageResponse, summary="Cerrar sesión")
def logout(payload: LogoutRequest, db: DbSession) -> MessageResponse:
    AuthService(db).logout(payload.refresh_token)
    return MessageResponse(detail="Sesión cerrada")


@router.post(
    "/recuperar-password", response_model=MessageResponse, summary="Solicitar recuperación de contraseña"
)
def recuperar_password(payload: RecuperarPasswordRequest, db: DbSession) -> MessageResponse:
    token = AuthService(db).solicitar_recuperacion(payload.correo)
    detail = "Si el correo existe, se enviaron instrucciones de recuperación"
    if token and not settings.is_production:
        # Solo en desarrollo: facilita probar el flujo sin servidor de correo
        detail = f"{detail}. Token (solo dev): {token}"
    return MessageResponse(detail=detail)


@router.post("/reset-password", response_model=MessageResponse, summary="Restablecer contraseña con token")
def reset_password(payload: ResetPasswordRequest, db: DbSession) -> MessageResponse:
    AuthService(db).restablecer_password(payload.token, payload.password)
    return MessageResponse(detail="Contraseña restablecida correctamente")


@router.post("/cambiar-password", response_model=MessageResponse, summary="Cambiar mi contraseña")
def cambiar_password(
    payload: CambiarPasswordRequest,
    db: DbSession,
    usuario=Depends(get_current_user),
) -> MessageResponse:
    AuthService(db).cambiar_password(usuario, payload.password_actual, payload.password_nueva)
    return MessageResponse(detail="Contraseña actualizada")


@router.get("/me", response_model=PerfilResponse, summary="Perfil y permisos del usuario actual")
def perfil(ctx: Context) -> PerfilResponse:
    user = ctx.user
    return PerfilResponse(
        id=user.id,
        nombre=user.nombre,
        apellido=user.apellido,
        correo=user.correo,
        username=user.username,
        foto_url=user.foto_url,
        empresa_id=ctx.empresa_id,
        sucursal_id=user.sucursal_id,
        roles=ctx.roles,
        permisos=sorted(f"{m}:{a}" for m, a in ctx.permisos),
        es_superadmin=ctx.is_superadmin,
    )
