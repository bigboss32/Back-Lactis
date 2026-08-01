"""Servicio de la suscripción mensual con Wompi.

No hereda de BaseService: no es el CRUD de una entidad tenant sino un flujo
sobre empresas + pasarela. Reglas centrales:

- `cobrar()` es el ÚNICO camino que crea pagos y `aplicar_resultado()` el
  ÚNICO que los muta y extiende la vigencia (idempotente por transición).
- El pago PENDING se confirma con commit ANTES de llamar a Wompi. Es una
  DESVIACIÓN deliberada al patrón commit-al-final del resto del sistema: el
  webhook llega por OTRA conexión y tiene que encontrar la referencia ya
  persistida, y el índice único parcial de PENDING necesita la fila visible
  para frenar un segundo cobro concurrente.
"""
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import lazyload
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.service import serialize_entity
from app.core.config import settings
from app.core.context import RequestContext, system_context
from app.core.exceptions import BusinessError, NotFoundError
from app.core.logging_config import get_logger
from app.core.pagination import PageParams
from app.modules.empresas.models import Empresa
from app.modules.suscripcion.estado import (
    ESTADO_BLOQUEADA,
    ESTADO_GRACIA,
    ResumenEstado,
    estado_suscripcion,
    limite_pago,
    sumar_un_mes,
)
from app.modules.suscripcion.models import (
    ESTADOS_TRANSACCION_FINALES,
    FUENTE_ACTIVA,
    FUENTE_REEMPLAZADA,
    ORIGEN_AUTOMATICO,
    ORIGEN_CRON,
    ORIGEN_MANUAL,
    TRANSACCION_APROBADA,
    TRANSACCION_ERROR,
    TRANSACCION_PENDIENTE,
    TRANSACCION_RECHAZADA,
    FuentePagoSuscripcion,
    PagoSuscripcion,
)
from app.modules.suscripcion.repository import (
    FuentePagoSuscripcionRepository,
    PagoSuscripcionRepository,
)
from app.modules.suscripcion.schemas import FuentePagoCreate
from app.modules.suscripcion.wompi import WompiClient

logger = get_logger("suscripcion")

# Un PENDING más viejo que esto se consulta contra Wompi (poll perezoso);
# más viejo que la expiración se da por muerto y se marca ERROR local.
EDAD_MINIMA_POLL = timedelta(minutes=2)
EXPIRACION_PENDIENTE = timedelta(hours=24)
# Tras un rechazo, los cobros AUTOMÁTICOS esperan esto antes de reintentar
COOLDOWN_RECHAZO = timedelta(hours=24)


def _utc(momento: datetime) -> datetime:
    """SQLite guarda los timestamps naive; Postgres con zona. Se normaliza a
    UTC para poder restar contra el reloj sin sorpresas."""
    if momento.tzinfo is None:
        return momento.replace(tzinfo=timezone.utc)
    return momento


class SuscripcionService:
    def __init__(self, db: Session, ctx: RequestContext):
        self.db = db
        self.ctx = ctx
        self.fuentes = FuentePagoSuscripcionRepository(db, ctx.empresa_id)
        self.pagos = PagoSuscripcionRepository(db, ctx.empresa_id)

    # -------------------------------------------------------------- auditoría
    def _audit(
        self,
        accion: str,
        entidad: str,
        entidad_id: uuid.UUID | None,
        antes: dict[str, Any] | None,
        despues: dict[str, Any] | None,
        *,
        empresa_id: uuid.UUID | None = None,
    ) -> None:
        from app.modules.auditoria.models import Auditoria

        self.db.add(
            Auditoria(
                empresa_id=self.ctx.empresa_id or empresa_id,
                usuario_id=self.ctx.user_id,
                ip=self.ctx.ip,
                modulo="suscripcion",
                accion=accion,
                entidad=entidad,
                entidad_id=entidad_id,
                antes=antes,
                despues=despues,
            )
        )

    # ------------------------------------------------------------- utilitarios
    def _empresa(self) -> Empresa:
        if self.ctx.empresa_id is None:
            raise BusinessError(
                "Esta operación requiere contexto de empresa: envíe el header X-Empresa-Id"
            )
        empresa = self.db.get(Empresa, self.ctx.empresa_id)
        if empresa is None or empresa.deleted_at is not None:
            raise NotFoundError("Empresa no encontrada")
        return empresa

    def _tarifa(self, empresa: Empresa) -> Decimal:
        if empresa.tarifa_mensual is not None:
            return empresa.tarifa_mensual
        return Decimal(settings.SUSCRIPCION_TARIFA_DEFAULT)

    def _estado(self, empresa: Empresa) -> ResumenEstado:
        return estado_suscripcion(
            exenta=empresa.exenta,
            pagada_hasta=empresa.pagada_hasta,
            creada=empresa.created_at.date(),
            hoy=date.today(),
            dias_aviso=settings.SUSCRIPCION_DIAS_AVISO,
            dias_gracia=settings.SUSCRIPCION_DIAS_GRACIA,
            dias_prueba=settings.SUSCRIPCION_DIAS_PRUEBA,
        )

    def _fuente_activa(self, empresa_id: uuid.UUID) -> FuentePagoSuscripcion | None:
        return self.db.scalars(
            select(FuentePagoSuscripcion)
            .where(
                FuentePagoSuscripcion.empresa_id == empresa_id,
                FuentePagoSuscripcion.deleted_at.is_(None),
                FuentePagoSuscripcion.estado == FUENTE_ACTIVA,
            )
            .order_by(FuentePagoSuscripcion.created_at.desc())
            .limit(1)
        ).first()

    def _pago_pendiente(self, empresa_id: uuid.UUID) -> PagoSuscripcion | None:
        return self.db.scalars(
            select(PagoSuscripcion)
            .where(
                PagoSuscripcion.empresa_id == empresa_id,
                PagoSuscripcion.deleted_at.is_(None),
                PagoSuscripcion.estado_transaccion == TRANSACCION_PENDIENTE,
            )
            .limit(1)
        ).first()

    def _en_cooldown(self, empresa_id: uuid.UUID) -> bool:
        """True si el último intento rechazado/errado es de hace menos de 24h.
        Solo frena a los orígenes automáticos: no se martilla la tarjeta del
        cliente, pero su 'Pagar ahora' manual siempre puede."""
        ultimo = self.db.scalars(
            select(PagoSuscripcion)
            .where(
                PagoSuscripcion.empresa_id == empresa_id,
                PagoSuscripcion.deleted_at.is_(None),
                PagoSuscripcion.estado_transaccion.in_(
                    (TRANSACCION_RECHAZADA, TRANSACCION_ERROR)
                ),
            )
            .order_by(PagoSuscripcion.updated_at.desc())
            .limit(1)
        ).first()
        if ultimo is None:
            return False
        return datetime.now(timezone.utc) - _utc(ultimo.updated_at) < COOLDOWN_RECHAZO

    # ---------------------------------------------------------------- lecturas
    def resumen(self) -> dict[str, Any]:
        """Estado completo de la suscripción de la empresa activa. Antes de
        calcular hace el poll perezoso: si hay un PENDING viejo, le pregunta a
        Wompi (o lo expira) para que la pantalla no se quede esperando un
        webhook que quizá nunca llegó (Render dormido, red, etc.)."""
        empresa = self._empresa()
        self._poll_pendiente(empresa.id)
        fuente = self._fuente_activa(empresa.id)
        resultado = self._estado(empresa)
        return {
            "estado": resultado.estado,
            "pagada_hasta": resultado.limite,
            "dias_restantes": resultado.dias_restantes,
            "dias_gracia": settings.SUSCRIPCION_DIAS_GRACIA,
            "tarifa": self._tarifa(empresa),
            "tiene_fuente_pago": fuente is not None,
            "exenta": empresa.exenta,
            "pago_pendiente": self._pago_pendiente(empresa.id) is not None,
            "fuente_pago": fuente,
        }

    def resumen_perfil(self, background: BackgroundTasks | None = None) -> dict[str, Any] | None:
        """Bloque liviano para /auth/me: SIN poll a Wompi (el frontend lo llama
        en cada refresco). Si la suscripción está vencida y hay tarjeta, encola
        el cobro automático para DESPUÉS de responder (latencia cero)."""
        if self.ctx.empresa_id is None:
            return None
        empresa = self.db.get(Empresa, self.ctx.empresa_id)
        if empresa is None or empresa.deleted_at is not None:
            return None
        resultado = self._estado(empresa)
        fuente = self._fuente_activa(empresa.id)
        if (
            background is not None
            and fuente is not None
            and resultado.estado in (ESTADO_GRACIA, ESTADO_BLOQUEADA)
        ):
            background.add_task(cobrar_vencida_en_segundo_plano, empresa.id)
        return {
            "estado": resultado.estado,
            "pagada_hasta": resultado.limite,
            "dias_restantes": resultado.dias_restantes,
            "dias_gracia": settings.SUSCRIPCION_DIAS_GRACIA,
            "tarifa": self._tarifa(empresa),
            "tiene_fuente_pago": fuente is not None,
        }

    def listar_pagos(self, params: PageParams) -> tuple[list[PagoSuscripcion], int]:
        empresa = self._empresa()
        repo = PagoSuscripcionRepository(self.db, empresa.id)
        return repo.list_paginated(params)

    def config_pasarela(self) -> dict[str, Any]:
        """Config para el formulario de tarjeta: la llave pública, la URL de
        tokenización (navegador → Wompi, el PAN no toca este backend) y los DOS
        tokens de aceptación frescos (son JWT con expiración)."""
        datos = WompiClient().tokens_aceptacion()
        aceptacion = datos.get("presigned_acceptance") or {}
        datos_personales = datos.get("presigned_personal_data_auth") or {}
        if not aceptacion.get("acceptance_token") or not datos_personales.get("acceptance_token"):
            raise BusinessError(
                "La pasarela de pagos no devolvió los tokens de aceptación",
                code="wompi_error",
            )
        return {
            "public_key": settings.WOMPI_PUBLIC_KEY,
            "tokenizacion_url": f"{settings.WOMPI_BASE_URL.rstrip('/')}/tokens/cards",
            "acceptance": {
                "acceptance_token": aceptacion.get("acceptance_token"),
                "permalink": aceptacion.get("permalink") or "",
            },
            "personal_data_auth": {
                "acceptance_token": datos_personales.get("acceptance_token"),
                "permalink": datos_personales.get("permalink") or "",
            },
        }

    def _poll_pendiente(self, empresa_id: uuid.UUID) -> None:
        """Poll perezoso del PENDING: >2 minutos se consulta contra Wompi;
        >24 horas sin resolverse se marca ERROR local (expiró). Es cortesía:
        si Wompi no responde, el resumen no se rompe."""
        pago = self._pago_pendiente(empresa_id)
        if pago is None:
            return
        edad = datetime.now(timezone.utc) - _utc(pago.created_at)
        if edad > EXPIRACION_PENDIENTE:
            self.aplicar_resultado(
                pago,
                TRANSACCION_ERROR,
                detalle="Expirado: sin respuesta de la pasarela en 24 horas",
            )
            return
        if edad < EDAD_MINIMA_POLL or not pago.wompi_transaction_id:
            return
        try:
            datos = WompiClient().consultar_transaccion(pago.wompi_transaction_id)
        except BusinessError:
            return
        estado = (datos.get("status") or "").upper()
        if estado in ESTADOS_TRANSACCION_FINALES:
            self.aplicar_resultado(pago, estado, detalle=datos.get("status_message"))

    # ------------------------------------------------------------ fuente de pago
    def guardar_fuente(self, payload: FuentePagoCreate) -> FuentePagoSuscripcion:
        """Crea la fuente de pago en Wompi y la guarda; si ya había una activa,
        la nueva la REEMPLAZA (una sola tarjeta vigente por empresa)."""
        empresa = self._empresa()
        datos = WompiClient().crear_fuente_pago(
            token=payload.token,
            customer_email=str(payload.customer_email),
            acceptance_token=payload.acceptance_token,
            accept_personal_auth=payload.accept_personal_auth,
        )
        if not datos.get("id"):
            raise BusinessError(
                "La pasarela de pagos no devolvió el id de la fuente de pago",
                code="wompi_error",
            )
        publica = datos.get("public_data") or {}
        anterior = self._fuente_activa(empresa.id)
        if anterior is not None:
            anterior.estado = FUENTE_REEMPLAZADA
            anterior.updated_by = self.ctx.user_id
        fuente = FuentePagoSuscripcion(
            empresa_id=empresa.id,
            wompi_payment_source_id=int(datos["id"]),
            marca=publica.get("brand"),
            ultimos4=publica.get("last_four"),
            exp_mes=publica.get("exp_month"),
            exp_anio=publica.get("exp_year"),
            customer_email=str(payload.customer_email),
            # Solo metadatos públicos: aquí no viaja (ni se loguea) el PAN
            detalle=json.dumps(
                {"type": datos.get("type"), "status": datos.get("status")}, ensure_ascii=False
            ),
            created_by=self.ctx.user_id,
            updated_by=self.ctx.user_id,
        )
        self.db.add(fuente)
        self.db.flush()
        self._audit("crear", "FuentePagoSuscripcion", fuente.id, None, serialize_entity(fuente))
        return fuente

    def eliminar_fuente(self) -> None:
        empresa = self._empresa()
        fuente = self._fuente_activa(empresa.id)
        if fuente is None:
            raise NotFoundError("La empresa no tiene una fuente de pago registrada")
        antes = serialize_entity(fuente)
        self.fuentes.soft_delete(fuente, deleted_by=self.ctx.user_id)
        self._audit("eliminar", "FuentePagoSuscripcion", fuente.id, antes, serialize_entity(fuente))

    # ------------------------------------------------------------------- cobro
    def pagar(self) -> tuple[PagoSuscripcion, dict[str, Any]]:
        """'Pagar ahora' manual. Un DECLINED NO es excepción: se devuelve el
        pago con su estado para que la pantalla lo muestre."""
        empresa = self._empresa()
        fuente = self._fuente_activa(empresa.id)
        pago = self.cobrar(empresa, fuente, ORIGEN_MANUAL)
        return pago, self.resumen()

    def cobrar(
        self,
        empresa: Empresa,
        fuente: FuentePagoSuscripcion | None,
        origen: str,
    ) -> PagoSuscripcion:
        """Núcleo del cobro: guardas → pago PENDING con commit → transacción en
        Wompi → aplicar_resultado. Ver la nota del módulo sobre el commit
        intermedio (única desviación al patrón commit-al-final)."""
        if empresa.exenta:
            raise BusinessError(
                "La empresa está exenta de pago: no hay nada que cobrar",
                code="empresa_exenta",
            )
        if fuente is None:
            raise BusinessError(
                "La empresa no tiene una fuente de pago registrada",
                code="sin_fuente_pago",
            )
        if self._pago_pendiente(empresa.id) is not None:
            raise BusinessError(
                "Ya hay un pago en proceso para esta empresa",
                code="pago_pendiente",
            )
        if origen != ORIGEN_MANUAL and self._en_cooldown(empresa.id):
            raise BusinessError(
                "El último intento de cobro fue rechazado hace menos de 24 horas",
                code="cobro_en_espera",
            )

        tarifa = self._tarifa(empresa)
        if tarifa <= 0:
            raise BusinessError("La tarifa mensual debe ser mayor que cero", code="tarifa_invalida")
        referencia = f"susc-{empresa.id.hex}-{uuid.uuid4().hex[:12]}"
        pago = PagoSuscripcion(
            empresa_id=empresa.id,
            fuente_pago_id=fuente.id,
            referencia=referencia,
            monto=tarifa,
            moneda="COP",
            estado_transaccion=TRANSACCION_PENDIENTE,
            origen=origen,
            created_by=self.ctx.user_id,
            updated_by=self.ctx.user_id,
        )
        self.db.add(pago)
        # DESVIACIÓN deliberada: commit ANTES de llamar a Wompi. El webhook
        # llega por otra conexión y debe encontrar la referencia; además el
        # índice único parcial de PENDING convierte una carrera de dos cobros
        # simultáneos en un IntegrityError aquí mismo (en el flush o en el
        # commit, según dónde alcance a chocar).
        try:
            self.db.flush()  # materializa el id antes de auditar
            self._audit(
                "cobrar", "PagoSuscripcion", pago.id, None, serialize_entity(pago),
                empresa_id=empresa.id,
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise BusinessError(
                "Ya hay un pago en proceso para esta empresa",
                code="pago_pendiente",
            ) from None

        try:
            datos = WompiClient().crear_transaccion(
                referencia=referencia,
                monto_en_centavos=int(tarifa * 100),
                customer_email=fuente.customer_email or "",
                payment_source_id=fuente.wompi_payment_source_id,
            )
        except BusinessError as exc:
            # No se sabe si Wompi alcanzó a crear la transacción: el pago queda
            # en ERROR (libera el candado de PENDING) y, si el webhook llega
            # después con esta referencia, el resultado real se aplica encima
            # (ERROR no es terminal; APPROVED sí).
            self.aplicar_resultado(pago, TRANSACCION_ERROR, detalle=exc.detail)
            self.db.commit()
            raise

        pago.wompi_transaction_id = str(datos.get("id")) if datos.get("id") else None
        estado = (datos.get("status") or TRANSACCION_PENDIENTE).upper()
        if estado in ESTADOS_TRANSACCION_FINALES:
            self.aplicar_resultado(pago, estado, detalle=datos.get("status_message"))
        else:
            self.db.flush()
        return pago

    def aplicar_resultado(
        self,
        pago: PagoSuscripcion,
        nuevo_estado: str,
        detalle: str | None = None,
    ) -> bool:
        """ÚNICO punto que muta un pago y extiende la vigencia. Idempotente por
        transición: el mismo estado repetido (re-entrega del webhook) es no-op,
        y APPROVED es terminal — un VOIDED posterior NO revierte la vigencia
        (eso lo ajusta el superadmin a mano por PUT /empresas/{id}/suscripcion).
        Bloquea la fila (FOR UPDATE en Postgres; SQLite lo ignora) porque el
        webhook y el poll pueden llegar a la vez por conexiones distintas."""
        pago = self.db.execute(
            select(PagoSuscripcion)
            .where(PagoSuscripcion.id == pago.id)
            # Sin el join de la fuente de pago (lazy="joined" sobre una FK
            # nullable): Postgres no admite FOR UPDATE sobre el lado exterior de
            # un LEFT JOIN y aborta con 0A000. Aquí solo se bloquea el pago.
            # Mismo caso —y misma solución— que en ventas/service.py al bloquear
            # una venta con su cliente. SQLite no lo delata: descarta FOR UPDATE
            # en silencio, así que las pruebas pasaban con el defecto puesto.
            .options(lazyload(PagoSuscripcion.fuente_pago))
            .with_for_update()
        ).scalar_one()
        if pago.estado_transaccion == nuevo_estado:
            return False
        if pago.estado_transaccion == TRANSACCION_APROBADA:
            return False
        antes = serialize_entity(pago)
        pago.estado_transaccion = nuevo_estado
        if detalle:
            pago.detalle = str(detalle)
        pago.updated_by = self.ctx.user_id
        if nuevo_estado == TRANSACCION_APROBADA:
            empresa = self.db.execute(
                select(Empresa).where(Empresa.id == pago.empresa_id).with_for_update()
            ).scalar_one()
            # El pago cubre UN MES desde donde iba la vigencia (o desde hoy si
            # ya estaba vencida: los días perdidos no se cobran dos veces).
            base = max(
                limite_pago(
                    empresa.pagada_hasta,
                    empresa.created_at.date(),
                    settings.SUSCRIPCION_DIAS_PRUEBA,
                ),
                date.today(),
            )
            pago.periodo_desde = base
            pago.periodo_hasta = sumar_un_mes(base)
            empresa.pagada_hasta = pago.periodo_hasta
        self.db.flush()
        self._audit(
            "aplicar_resultado", "PagoSuscripcion", pago.id, antes, serialize_entity(pago),
            empresa_id=pago.empresa_id,
        )
        return True

    # ----------------------------------------------------------------- webhook
    def procesar_evento(self, payload: dict[str, Any]) -> str:
        """Aplica un evento de Wompi YA validado (el checksum se verifica en el
        router). Todo lo que no sea nuestro devuelve un resultado benigno: al
        webhook hay que responderle 200 salvo checksum inválido, porque Wompi
        reintenta ante cualquier no-200."""
        if payload.get("event") != "transaction.updated":
            return "ignorado"
        transaccion = (payload.get("data") or {}).get("transaction") or {}
        referencia = transaccion.get("reference")
        pago = None
        if referencia:
            pago = self.db.scalars(
                select(PagoSuscripcion).where(PagoSuscripcion.referencia == referencia)
            ).first()
        if pago is None:
            return "desconocida"
        if not pago.wompi_transaction_id and transaccion.get("id"):
            pago.wompi_transaction_id = str(transaccion["id"])
        estado = (transaccion.get("status") or "").upper()
        if estado not in ESTADOS_TRANSACCION_FINALES:
            return "ignorado"
        aplicado = self.aplicar_resultado(pago, estado, detalle=transaccion.get("status_message"))
        return "aplicado" if aplicado else "repetido"

    # ---------------------------------------------------------- cobro automático
    def cobrar_vencidas(self) -> dict[str, int]:
        """Barrido de cobro automático (cron externo): cobra a toda empresa
        vencida (gracia o bloqueada) con tarjeta guardada. Cada empresa va en
        su propio try/except: una tarjeta mala no frena a las demás."""
        contadores = {
            "evaluadas": 0,
            "cobradas": 0,
            "rechazadas": 0,
            "pendientes": 0,
            "omitidas": 0,
            "errores": 0,
        }
        empresas = self.db.scalars(
            select(Empresa).where(Empresa.deleted_at.is_(None), Empresa.estado == "activo")
        ).all()
        for empresa in empresas:
            resultado = self._estado(empresa)
            if resultado.estado not in (ESTADO_GRACIA, ESTADO_BLOQUEADA):
                continue
            contadores["evaluadas"] += 1
            try:
                pago = self.cobrar(empresa, self._fuente_activa(empresa.id), ORIGEN_CRON)
            except BusinessError as exc:
                # rollback ANTES de seguir: si la excepción vino de la base (el
                # IntegrityError del PENDING duplicado ya hizo el suyo, pero otras
                # no), la sesión queda en 'needs rollback' y TODAS las empresas
                # que falten revientan con PendingRollbackError.
                self.db.rollback()
                if exc.code in ("empresa_exenta", "sin_fuente_pago", "pago_pendiente", "cobro_en_espera"):
                    contadores["omitidas"] += 1
                else:
                    contadores["errores"] += 1
                continue
            except Exception:
                logger.exception("Cobro automático fallido para la empresa %s", empresa.id)
                self.db.rollback()
                contadores["errores"] += 1
                continue
            if pago.estado_transaccion == TRANSACCION_APROBADA:
                contadores["cobradas"] += 1
            elif pago.estado_transaccion == TRANSACCION_PENDIENTE:
                contadores["pendientes"] += 1
            else:
                contadores["rechazadas"] += 1
            # Se confirma AL CERRAR CADA EMPRESA, no al final del barrido.
            #
            # `cobrar` solo hace commit del PENDING; el id de la transacción y la
            # vigencia extendida quedaban en flush, esperando un commit posterior.
            # Si una empresa más adelante chocaba con el índice único y hacía
            # rollback, se llevaba por delante el APPROVED ya cobrado de otra:
            # tarjeta debitada y mes no acreditado. Con el commit por empresa,
            # cada una responde solo por sí misma.
            self.db.commit()
        return contadores


def cobrar_vencida_en_segundo_plano(empresa_id: uuid.UUID) -> None:
    """Gatillo perezoso desde /auth/me: corre DESPUÉS de responder, con sesión
    PROPIA (la de la petición ya se cerró) y contexto de sistema. Re-verifica
    todas las guardas: entre encolar y ejecutar pudo pagar otro proceso."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        servicio = SuscripcionService(db, system_context(empresa_id))
        empresa = db.get(Empresa, empresa_id)
        if empresa is None or empresa.deleted_at is not None:
            return
        resultado = servicio._estado(empresa)
        if resultado.estado not in (ESTADO_GRACIA, ESTADO_BLOQUEADA):
            return
        try:
            servicio.cobrar(empresa, servicio._fuente_activa(empresa_id), ORIGEN_AUTOMATICO)
        except BusinessError:
            # Sin fuente, pago en proceso, cooldown o pasarela caída: quedará
            # para el siguiente gatillo (otro /auth/me o el cron).
            pass
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falló el cobro automático en segundo plano (empresa %s)", empresa_id)
    finally:
        db.close()
