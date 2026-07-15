"""Autenticación: login con bloqueo por intentos, refresh rotativo, logout,
recuperación y cambio de contraseña. Todo intento queda en auditoría de login.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.logging_config import get_logger
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.usuarios.models import LoginAudit, PasswordResetToken, RefreshToken, Usuario
from app.modules.usuarios.repository import RefreshTokenRepository, UsuarioRepository

logger = get_logger("auth")


class AuthService:
    def __init__(self, db: Session):
        self.db = db
        self.usuarios = UsuarioRepository(db)
        self.refresh_tokens = RefreshTokenRepository(db)

    # ------------------------------------------------------------------ login
    def _registrar_login(
        self,
        usuario: Usuario | None,
        username: str,
        exito: bool,
        motivo: str | None,
        ip: str | None,
        user_agent: str | None,
    ) -> None:
        self.db.add(
            LoginAudit(
                usuario_id=usuario.id if usuario else None,
                username_intentado=username,
                exito=exito,
                motivo=motivo,
                ip=ip,
                user_agent=user_agent[:300] if user_agent else None,
            )
        )
        self.db.flush()

    def login(
        self, username: str, password: str, ip: str | None = None, user_agent: str | None = None
    ) -> tuple[str, str]:
        usuario = self.usuarios.get_by_username_or_email(username)

        # En las rutas de fallo se confirma la transacción antes de lanzar la
        # excepción: si no, el rollback de get_db borraría el contador de
        # intentos y la auditoría del intento fallido.
        if usuario is None:
            self._registrar_login(None, username, False, "usuario no existe", ip, user_agent)
            self.db.commit()
            raise UnauthorizedError("Credenciales inválidas")

        if usuario.bloqueado:
            self._registrar_login(usuario, username, False, "usuario bloqueado", ip, user_agent)
            self.db.commit()
            raise ForbiddenError("Usuario bloqueado por intentos fallidos. Contacte al administrador")

        if usuario.estado != "activo":
            self._registrar_login(usuario, username, False, "usuario inactivo", ip, user_agent)
            self.db.commit()
            raise ForbiddenError("Usuario inactivo")

        if not verify_password(password, usuario.hashed_password):
            usuario.intentos_fallidos += 1
            motivo = "contraseña incorrecta"
            if usuario.intentos_fallidos >= settings.MAX_LOGIN_ATTEMPTS:
                usuario.bloqueado = True
                motivo = f"bloqueado tras {usuario.intentos_fallidos} intentos"
            self.db.flush()
            self._registrar_login(usuario, username, False, motivo, ip, user_agent)
            self.db.commit()
            raise UnauthorizedError("Credenciales inválidas")

        usuario.intentos_fallidos = 0
        usuario.ultimo_acceso = datetime.now(timezone.utc)
        self.db.flush()
        self._registrar_login(usuario, username, True, None, ip, user_agent)

        access = create_access_token(usuario.id, usuario.empresa_id, [r.nombre for r in usuario.roles])
        refresh, jti, expires_at = create_refresh_token(usuario.id)
        self.db.add(RefreshToken(usuario_id=usuario.id, jti=jti, expires_at=expires_at))
        self.db.flush()
        return access, refresh

    # ---------------------------------------------------------------- refresh
    def refresh(self, refresh_token: str) -> tuple[str, str]:
        payload = decode_token(refresh_token, "refresh")
        stored = self.refresh_tokens.get_by_jti(payload["jti"])
        if stored is None or stored.revoked_at is not None:
            raise UnauthorizedError("Refresh token revocado o desconocido")

        usuario = self.usuarios.get(uuid.UUID(payload["sub"]))
        if usuario is None or usuario.bloqueado or usuario.estado != "activo":
            raise UnauthorizedError("Usuario no habilitado")

        # Rotación: se revoca el token usado y se emite uno nuevo
        stored.revoked_at = datetime.now(timezone.utc)
        access = create_access_token(usuario.id, usuario.empresa_id, [r.nombre for r in usuario.roles])
        refresh, jti, expires_at = create_refresh_token(usuario.id)
        self.db.add(RefreshToken(usuario_id=usuario.id, jti=jti, expires_at=expires_at))
        self.db.flush()
        return access, refresh

    # ----------------------------------------------------------------- logout
    def logout(self, refresh_token: str) -> None:
        try:
            payload = decode_token(refresh_token, "refresh")
        except UnauthorizedError:
            return  # token ya inválido: logout idempotente
        stored = self.refresh_tokens.get_by_jti(payload["jti"])
        if stored and stored.revoked_at is None:
            stored.revoked_at = datetime.now(timezone.utc)
            self.db.flush()

    # ------------------------------------------------------- password recovery
    def solicitar_recuperacion(self, correo: str) -> str | None:
        """Genera token de recuperación. En producción se enviaría por correo;
        aquí se registra en logs y (solo en desarrollo) se retorna en la respuesta.
        """
        usuario = self.usuarios.get_by_username_or_email(correo)
        if usuario is None:
            return None  # respuesta genérica: no revelar si el correo existe
        token = create_password_reset_token(usuario.id)
        payload = decode_token(token, "password_reset")
        self.db.add(
            PasswordResetToken(
                usuario_id=usuario.id,
                jti=payload["jti"],
                expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            )
        )
        self.db.flush()
        logger.info("Token de recuperación generado para %s", correo)
        return token

    def restablecer_password(self, token: str, password: str) -> None:
        payload = decode_token(token, "password_reset")
        stored = self.db.scalars(
            select(PasswordResetToken).where(PasswordResetToken.jti == payload["jti"])
        ).first()
        if stored is None or stored.used_at is not None:
            raise UnauthorizedError("Token de recuperación inválido o ya utilizado")
        usuario = self.usuarios.get(uuid.UUID(payload["sub"]))
        if usuario is None:
            raise UnauthorizedError("Usuario no existe")
        usuario.hashed_password = hash_password(password)
        usuario.intentos_fallidos = 0
        usuario.bloqueado = False
        stored.used_at = datetime.now(timezone.utc)
        self.db.flush()

    def cambiar_password(self, usuario: Usuario, actual: str, nueva: str) -> None:
        if not verify_password(actual, usuario.hashed_password):
            raise UnauthorizedError("La contraseña actual no es correcta")
        usuario.hashed_password = hash_password(nueva)
        self.db.flush()
