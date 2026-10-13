"""Manejo centralizado de excepciones de la aplicación."""
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.core.logging_config import get_logger

logger = get_logger("exceptions")

# LOS DOS CÓDIGOS DE POSTGRES QUE QUIEREN DECIR "OTRA PERSONA HACÍA LO MISMO Y ESTA PETICIÓN
# PERDIÓ": 40P01 (deadlock detectado: dos peticiones se esperaban entre sí y Postgres tumba
# a una) y 40001 (fallo de serialización). En los dos la transacción se revierte entera, así
# que NO queda nada a medias; lo único que hay que hacer es volver a intentar. Salían como
# un 500 "Error interno del servidor", que no dice eso y asusta.
CODIGOS_DE_CONCURRENCIA = frozenset({"40P01", "40001"})
MENSAJE_DE_CONCURRENCIA = (
    "Otra persona estaba cambiando lo mismo en el mismo momento y esta vez no se alcanzó a "
    "guardar. No se perdió ni se movió nada: vuelva a intentarlo."
)


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
        # El detalle de pydantic trae el valor del límite tal cual está declarado,
        # y en los campos de plata ese límite es un Decimal, que json.dumps no
        # sabe escribir: la respuesta reventaba y salía un 500 "Error interno del
        # servidor" donde tenía que salir "El valor debe ser mayor que 0". Pasaba
        # con cualquier campo Decimal con tope (el valor de un anticipo, por
        # ejemplo). jsonable_encoder los baja a tipos que sí se pueden escribir.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=jsonable_encoder(
                {"error": {"code": "validation_error", "detail": detalle, "errors": errores}}
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Error no controlado en %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "internal_error", "detail": "Error interno del servidor"}},
        )

    @app.exception_handler(DBAPIError)
    async def database_exception_handler(request: Request, exc: DBAPIError) -> JSONResponse:
        """Un abrazo mortal (o un fallo de serialización) es un 409 que dice qué hacer; cualquier
        otro error de la base de datos sigue siendo el mismo 500 de siempre."""
        if getattr(exc.orig, "pgcode", None) in CODIGOS_DE_CONCURRENCIA:
            logger.warning(
                "Conflicto de concurrencia (%s) en %s %s",
                exc.orig.pgcode, request.method, request.url.path,
            )
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"error": {"code": "concurrency_conflict", "detail": MENSAJE_DE_CONCURRENCIA}},
            )
        return await unhandled_exception_handler(request, exc)
