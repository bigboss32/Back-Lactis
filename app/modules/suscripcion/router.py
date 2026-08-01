"""Endpoints de la suscripción mensual con Wompi.

El módulo 'suscripcion' está EXENTO del paywall (ver deps.py): una empresa
bloqueada tiene que poder entrar aquí a pagar. El webhook y el cobro por cron
son públicos a su manera: el primero se protege con el checksum de eventos y
el segundo con el header X-Cron-Secret.
"""
import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, status

from app.core.config import settings
from app.core.context import RequestContext, system_context
from app.core.deps import DbSession, require_permission
from app.core.exceptions import AppException, ForbiddenError
from app.core.pagination import Page, PageParams, page_params
from app.modules.suscripcion.schemas import (
    BancoPSE,
    CobrarVencidasResponse,
    ConfigWompiResponse,
    FuentePagoCreate,
    FuentePagoRead,
    PagarPseRequest,
    PagarPseResponse,
    PagarResponse,
    PagoSuscripcionRead,
    SuscripcionDetalle,
)
from app.modules.suscripcion.service import SuscripcionService
from app.modules.suscripcion.wompi import validar_checksum_evento

router = APIRouter(tags=["Suscripción"])


@router.get("", response_model=SuscripcionDetalle, summary="Estado de la suscripción de la empresa activa")
def estado(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("suscripcion", "consultar")),
) -> Any:
    return SuscripcionService(db, ctx).resumen()


@router.get("/pagos", response_model=Page[PagoSuscripcionRead], summary="Historial de pagos de la suscripción")
def pagos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("suscripcion", "consultar")),
    params: PageParams = Depends(page_params),
) -> Any:
    items, total = SuscripcionService(db, ctx).listar_pagos(params)
    return Page.build(items, total, params)


@router.get(
    "/config",
    response_model=ConfigWompiResponse,
    summary="Configuración para el formulario de tarjeta (llave pública y tokens de aceptación)",
)
def config(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("suscripcion", "consultar")),
) -> Any:
    return SuscripcionService(db, ctx).config_pasarela()


@router.post(
    "/fuente-pago",
    response_model=FuentePagoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Guardar la tarjeta tokenizada como fuente de pago (reemplaza la anterior)",
)
def crear_fuente_pago(
    payload: FuentePagoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("suscripcion", "administrar")),
) -> Any:
    return SuscripcionService(db, ctx).guardar_fuente(payload)


@router.delete(
    "/fuente-pago",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar la fuente de pago vigente",
)
def eliminar_fuente_pago(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("suscripcion", "administrar")),
) -> None:
    SuscripcionService(db, ctx).eliminar_fuente()


@router.post(
    "/pagar",
    response_model=PagarResponse,
    summary="Pagar ahora la mensualidad con la tarjeta guardada",
)
def pagar(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("suscripcion", "crear")),
) -> Any:
    # Un DECLINED también es 200: la pantalla muestra el resultado del intento
    pago, resumen = SuscripcionService(db, ctx).pagar()
    return {"pago": pago, "suscripcion": resumen}


@router.post("/webhook", summary="Webhook de eventos de Wompi (público, validado por checksum)")
def webhook(db: DbSession, payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    """Checksum inválido (o secreto sin configurar) → 400 para que Wompi
    reintente. Todo lo demás → 200 aunque el evento no sea nuestro: Wompi
    reintenta 3 veces en 24h ante cualquier respuesta distinta de 200."""
    if not settings.WOMPI_EVENT_SECRET or not validar_checksum_evento(
        payload, settings.WOMPI_EVENT_SECRET
    ):
        raise AppException("Checksum del evento inválido", code="checksum_invalido")
    resultado = SuscripcionService(db, system_context()).procesar_evento(payload)
    return {"detail": resultado}


@router.post(
    "/cobrar-vencidas",
    response_model=CobrarVencidasResponse,
    summary="Cobrar a todas las empresas vencidas (cron externo con X-Cron-Secret)",
)
def cobrar_vencidas(
    db: DbSession,
    x_cron_secret: Annotated[str | None, Header(alias="X-Cron-Secret")] = None,
) -> Any:
    secreto = settings.SUSCRIPCION_CRON_SECRET
    # Sin secreto configurado el endpoint queda deshabilitado; compare_digest
    # para no filtrar el secreto por tiempos de respuesta.
    # Bytes, no str: compare_digest con str exige ASCII y un header con una
    # tilde daba 500 en vez de 403 (ver la misma nota en wompi.py).
    if not secreto or not hmac.compare_digest(
        secreto.encode("utf-8"), (x_cron_secret or "").encode("utf-8")
    ):
        raise ForbiddenError("Secreto de cron inválido", code="cron_secret_invalido")
    return SuscripcionService(db, system_context()).cobrar_vencidas()


# ---------------------------------------------------------------------- PSE
@router.get(
    "/pse/bancos",
    response_model=list[BancoPSE],
    summary="Bancos habilitados para pagar por PSE",
)
def bancos_pse(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("suscripcion", "consultar")),
) -> Any:
    """La lista se pide fresca a Wompi en cada carga del formulario: los bancos
    entran, salen y se ponen en mantenimiento, y una lista guardada mandaría a
    la persona a un banco que hoy no funciona."""
    return SuscripcionService(db, ctx).bancos_pse()


@router.post(
    "/pse/pagar",
    response_model=PagarPseResponse,
    summary="Pagar la mensualidad por PSE (devuelve la URL del banco)",
)
def pagar_pse(
    payload: PagarPseRequest,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("suscripcion", "crear")),
) -> Any:
    """No cobra aquí: deja el pago PENDING y devuelve a dónde mandar a la
    persona. El banco responde después, por el webhook."""
    pago, url_banco, resumen = SuscripcionService(db, ctx).pagar_con_pse(payload)
    return {"pago": pago, "url_banco": url_banco, "suscripcion": resumen}
