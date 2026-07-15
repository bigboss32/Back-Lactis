"""Manejo centralizado de excepciones de la aplicación."""
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging_config import get_logger

logger = get_logger("exceptions")


class AppException(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "error"

    def __init__(self, detail: str, *, code: str | None = None, extra: dict[str, Any] | None = None):
        self.detail = detail
        if code:
            self.code = code
        self.extra = extra or {}
        super().__init__(detail)


class NotFoundError(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppException):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class UnauthorizedError(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppException):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class BusinessError(AppException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "business_rule"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "detail": exc.detail, **exc.extra}},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errores = exc.errors()
        detalle = "Datos de entrada inválidos"
        # Nombrar el primer campo inválido para que el usuario sepa qué corregir
        if errores:
            campo = ".".join(str(p) for p in errores[0].get("loc", []) if p not in ("body", "query"))
            mensaje = errores[0].get("msg", "")
            if campo:
                detalle = f"Dato inválido en '{campo}': {mensaje}"
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": {"code": "validation_error", "detail": detalle, "errors": errores}},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Error no controlado en %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "internal_error", "detail": "Error interno del servidor"}},
        )
