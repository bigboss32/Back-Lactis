"""Hashing de contraseñas y emisión/verificación de tokens JWT."""
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings
from app.core.exceptions import UnauthorizedError

TokenType = Literal["access", "refresh", "password_reset"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _create_token(subject: str, token_type: TokenType, expires_delta: timedelta, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(user_id: uuid.UUID, empresa_id: uuid.UUID | None, roles: list[str]) -> str:
    return _create_token(
        str(user_id),
        "access",
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        {"empresa_id": str(empresa_id) if empresa_id else None, "roles": roles},
    )


def create_refresh_token(user_id: uuid.UUID) -> tuple[str, str, datetime]:
    """Retorna (token, jti, expiración) para persistir y poder revocarlo."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    jti = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": str(user_id), "type": "refresh", "iat": now, "exp": expires_at, "jti": jti},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return token, jti, expires_at


def create_password_reset_token(user_id: uuid.UUID) -> str:
    return _create_token(
        str(user_id), "password_reset", timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Token expirado") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Token inválido") from exc
    if payload.get("type") != expected_type:
        raise UnauthorizedError("Tipo de token incorrecto")
    return payload


def generate_random_password(length: int = 12) -> str:
    return secrets.token_urlsafe(length)
