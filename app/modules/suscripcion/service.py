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
    METODO_PSE,
    METODO_TARJETA,
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
from app.modules.suscripcion.wompi import WompiClient, url_del_banco

logger = get_logger("suscripcion")

# Un PENDING más viejo que esto se consulta contra Wompi (poll perezoso);
# más viejo que la expiración se da por muerto y se marca ERROR local.
EDAD_MINIMA_POLL = timedelta(minutes=2)
EXPIRACION_PENDIENTE = timedelta(hours=24)
# Techo para el PENDING que ni siquiera llegó a la pasarela o cuya consulta
# lleva días fallando. No es un plazo de negocio, es una válvula: sin él, un
# pago atascado deja el candado puesto y la empresa no puede volver a pagar
# NUNCA. Siete días es muchísimo más que la vida de cualquier PSE, así que
# llegar aquí significa que algo está roto y por eso se registra como error.
EXPIRACION_SIN_RESPUESTA = timedelta(days=7)


def _solo_digitos(texto: str) -> str:
    """Deja el teléfono en puros números.

    La gente lo escribe como quiere: "310 765 0926", "(310) 765-0926",
    "+57 310...". Wompi quiere el número y nada más. El indicativo del país se
    quita porque el prefijo va aparte y mandarlo pegado hace que el banco no
    reconozca el número.
    """
    digitos = "".join(c for c in texto if c.isdigit())
    if len(digitos) > 10 and digitos.startswith("57"):
        digitos = digitos[2:]
    return digitos
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
        """True si el último intento CON TARJETA rechazado/errado es de hace
        menos de 24h. Solo frena a los orígenes automáticos: no se martilla la
        tarjeta del cliente, pero su 'Pagar ahora' manual siempre puede.

        Solo cuentan los pagos con tarjeta, y por lo que protege el cooldown:
        no machacar una tarjeta que el emisor acaba de rechazar. Un PSE que se
        abandonó en el portal del banco no dice absolutamente nada sobre la
        tarjeta, y contarlo apagaba el cobro automático 24 horas de una tarjeta
        perfectamente cobrable: la empresa se comía la gracia y terminaba
        bloqueada teniendo con qué pagar.
        """
        ultimo = self.db.scalars(
            select(PagoSuscripcion)
            .where(
                PagoSuscripcion.empresa_id == empresa_id,
                PagoSuscripcion.deleted_at.is_(None),
                PagoSuscripcion.metodo == METODO_TARJETA,
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
    def resumen(self, forzar_poll: bool = False) -> dict[str, Any]:
        """Estado completo de la suscripción de la empresa activa. Antes de
        calcular hace el poll perezoso: si hay un PENDING viejo, le pregunta a
        Wompi (o lo expira) para que la pantalla no se quede esperando un
        webhook que quizá nunca llegó (Render dormido, red, etc.).

        `forzar_poll` salta la edad mínima. Es para cuando lo pide una persona
        a propósito (el botón "Actualizar estado"): ahí esperar dos minutos no
        tiene sentido, quien acaba de pagar quiere saber YA.
        """
        empresa = self._empresa()
        self._poll_pendiente(empresa.id, forzar=forzar_poll)
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

    def actualizar_estado(self) -> dict[str, Any]:
        """Le pregunta a Wompi AHORA cómo quedó el pago que está en curso.

        Es la salida cuando el webhook no llega: Wompi lo reintenta tres veces
        en 24 horas y si el backend estaba dormido en las tres, el pago se queda
        pendiente para siempre desde el punto de vista de la pantalla, aunque el
        banco ya haya debitado. Con esto la persona pregunta ella misma en vez
        de quedarse mirando.

        Devuelve además cómo quedó, para poder decírselo con palabras en vez de
        dejar que adivine mirando una tabla.
        """
        empresa = self._empresa()
        pendiente = self._pago_pendiente(empresa.id)
        id_pendiente = pendiente.id if pendiente is not None else None

        detalle = self.resumen(forzar_poll=True)

        estado_pago = None
        if id_pendiente is not None:
            pago = self.db.get(PagoSuscripcion, id_pendiente)
            estado_pago = pago.estado_transaccion if pago is not None else None
        return {
            "suscripcion": detalle,
            # Hubo pago en curso y dejó de estar pendiente: algo cambió
            "cambio": id_pendiente is not None and estado_pago != TRANSACCION_PENDIENTE,
            "estado_pago": estado_pago,
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

    def _poll_pendiente(self, empresa_id: uuid.UUID, forzar: bool = False) -> None:
        """Poll perezoso del PENDING contra Wompi, que es la fuente de la verdad.

        EL ORDEN DE AQUÍ IMPORTA MÁS QUE NADA. Antes se miraba primero la edad:
        un PENDING de más de 24 horas se marcaba ERROR sin preguntarle a nadie.
        Con tarjeta casi no dolía (una tarjeta resuelve en segundos, un PENDING
        de un día sí está muerto). Con PSE es un desastre: el pago pendiente
        largo es LO NORMAL, y si el webhook se perdió —Render dormido, un
        redespliegue, la red— se escribía ERROR sobre un pago que el banco ya
        había debitado. Plata cobrada, mes no acreditado, empresa bloqueada, y
        además el candado del índice parcial liberado, o sea vía libre para
        pagar dos veces el mismo mes.

        Ahora: si hay transacción en la pasarela, se le pregunta a la pasarela.
        Solo se da por muerto lo que Wompi confirma muerto, lo que nunca llegó a
        crearse allá, o lo que lleva tantos días sin respuesta que dejarlo
        pendiente sería peor (ver EXPIRACION_SIN_RESPUESTA).
        """
        pago = self._pago_pendiente(empresa_id)
        if pago is None:
            return
        edad = datetime.now(timezone.utc) - _utc(pago.created_at)

        # Un PSE sin URL del banco es un pago que nadie puede aprobar: se busca
        # YA, sin esperar la edad mínima. Wompi publica esa URL un instante
        # después de crear la transacción, no en la respuesta de creación.
        urgente = forzar or (pago.metodo == METODO_PSE and not pago.url_banco)
        if edad < EDAD_MINIMA_POLL and not urgente:
            return

        if pago.wompi_transaction_id:
            try:
                datos = WompiClient().consultar_transaccion(pago.wompi_transaction_id)
            except BusinessError:
                # Wompi no contestó. NO se da nada por muerto con la pasarela
                # muda: se reintenta en el siguiente poll. La única excepción es
                # el pago tan viejo que dejarlo pendiente ya hace más daño.
                if edad > EXPIRACION_SIN_RESPUESTA:
                    logger.error(
                        "Pago %s lleva %s sin que la pasarela responda: se cierra como ERROR "
                        "para no dejar a la empresa sin poder pagar. REVISAR A MANO.",
                        pago.id,
                        edad,
                    )
                    self.aplicar_resultado(
                        pago,
                        TRANSACCION_ERROR,
                        detalle="La pasarela no respondió durante días; revisar a mano",
                    )
                return
            if not pago.url_banco:
                url = url_del_banco(datos)
                if url:
                    pago.url_banco = url
                    self.db.commit()
            estado = (datos.get("status") or "").upper()
            if estado in ESTADOS_TRANSACCION_FINALES:
                self.aplicar_resultado(pago, estado, detalle=datos.get("status_message"))
            # Si Wompi lo sigue dando por PENDING, sigue PENDING. Da igual la
            # edad: quien manda es la pasarela, no nuestro reloj.
            return

        # Sin id de transacción no hay a quién preguntarle: la transacción nunca
        # llegó a existir en la pasarela (se cayó entre el commit del pago y la
        # llamada). Ese sí se puede cerrar al expirar, y hay que hacerlo o el
        # candado del PENDING deja a la empresa sin poder pagar nunca más.
        if edad > EXPIRACION_PENDIENTE:
            self.aplicar_resultado(
                pago,
                TRANSACCION_ERROR,
                detalle="Expirado: la pasarela nunca confirmó la transacción",
            )

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


    # ------------------------------------------------------------------- PSE
    def _correo_de_cobro(self, empresa: Empresa) -> str:
        """A qué correo se le asocia el pago en la pasarela.

        El de la empresa, y si no lo tiene, el de quien está pagando. PSE lo
        exige, y mandarlo vacío hace que Wompi rechace la transacción con un
        mensaje que no dice nada útil; mejor fallar aquí y explicar qué falta.

        En la tarjeta esto no hacía falta porque el correo viene con la fuente
        de pago, que lo pidió al guardarla.
        """
        if empresa.correo:
            return empresa.correo
        from app.modules.usuarios.models import Usuario

        usuario = self.db.get(Usuario, self.ctx.user_id) if self.ctx.user_id else None
        if usuario is not None and usuario.correo:
            return usuario.correo
        raise BusinessError(
            "Para pagar por PSE hace falta un correo: agréguele uno a la empresa",
            code="sin_correo",
        )

    def _datos_del_pagador(self, empresa: Empresa, payload: Any) -> tuple[str, str]:
        """Nombre y teléfono de quien paga, que PSE exige en `customer_data`.

        Wompi no perdona que falte: responde INPUT_VALIDATION_ERROR y el pago no
        llega a crearse. El sandbox sí lo deja pasar, así que esto solo se ve
        con llaves de producción.

        Se toma lo que mande la pantalla y, si no manda nada, lo que ya sabemos:
        primero el usuario que está pagando, después la empresa. Solo se le pide
        a la persona lo que de verdad no tengamos.
        """
        from app.modules.usuarios.models import Usuario

        usuario = self.db.get(Usuario, self.ctx.user_id) if self.ctx.user_id else None

        nombre = (getattr(payload, "nombre_completo", None) or "").strip()
        if not nombre and usuario is not None:
            nombre = f"{usuario.nombre} {usuario.apellido}".strip()
        if not nombre:
            nombre = (empresa.nombre or "").strip()

        telefono = _solo_digitos(getattr(payload, "telefono", None) or "")
        if not telefono and usuario is not None:
            telefono = _solo_digitos(usuario.telefono or "")
        if not telefono:
            telefono = _solo_digitos(empresa.telefono or "")

        if len(nombre) < 3:
            raise BusinessError(
                "Para pagar por PSE hace falta el nombre de quien paga",
                code="sin_nombre_pagador",
            )
        if len(telefono) < 7:
            raise BusinessError(
                "Para pagar por PSE hace falta un teléfono de contacto: "
                "escríbalo en el formulario o agréguelo a los datos de la empresa",
                code="sin_telefono",
            )
        return nombre[:100], telefono[:20]

    def bancos_pse(self) -> list[dict[str, Any]]:
        """Los bancos habilitados para PSE, frescos de Wompi."""
        return WompiClient().bancos_pse()

    def pagar_con_pse(self, payload: Any) -> tuple[PagoSuscripcion, str | None, dict[str, Any]]:
        """Arranca un pago por PSE y devuelve a dónde mandar a la persona.

        NO cobra nada aquí: crea la transacción en Wompi, que nace PENDING, y
        entrega la URL del portal del banco. El resultado llega después por el
        webhook, que ya sabe acreditar por referencia — el mismo camino que usa
        la tarjeta, así que la vigencia se extiende igual y sin código nuevo.

        PSE no deja fuente de pago guardada: paga ESTE mes y ya. Quien quiera que
        se le cobre solo cada mes tiene que guardar una tarjeta.
        """
        empresa = self._empresa()
        if empresa.exenta:
            raise BusinessError(
                "La empresa está exenta de pago: no hay nada que cobrar",
                code="empresa_exenta",
            )
        pendiente = self._pago_pendiente(empresa.id)
        if pendiente is not None:
            # No se devuelve aquí la URL del banco: la pantalla recarga al
            # cerrar el diálogo y ya pinta el aviso de "continuar en el banco"
            # con el enlace, que sale de la lista de pagos. Un campo extra que
            # nadie lee solo sirve para que alguien crea que sí se usa.
            raise BusinessError(
                "Ya hay un pago en proceso para esta empresa",
                code="pago_pendiente",
            )
        tarifa = self._tarifa(empresa)
        if tarifa <= 0:
            raise BusinessError("La tarifa mensual debe ser mayor que cero", code="tarifa_invalida")

        # El token de aceptación se pide FRESCO: es un JWT que expira.
        datos_comercio = WompiClient().tokens_aceptacion()
        aceptacion = (datos_comercio.get("presigned_acceptance") or {}).get("acceptance_token")
        if not aceptacion:
            raise BusinessError(
                "La pasarela de pagos no devolvió el token de aceptación",
                code="wompi_error",
            )

        referencia = f"susc-{empresa.id.hex}-{uuid.uuid4().hex[:12]}"
        pago = PagoSuscripcion(
            empresa_id=empresa.id,
            fuente_pago_id=None,  # PSE no deja fuente guardada
            metodo=METODO_PSE,
            referencia=referencia,
            monto=tarifa,
            moneda="COP",
            estado_transaccion=TRANSACCION_PENDIENTE,
            origen=ORIGEN_MANUAL,  # PSE siempre lo inicia una persona
            created_by=self.ctx.user_id,
            updated_by=self.ctx.user_id,
        )
        self.db.add(pago)
        # Mismo commit anticipado que en `cobrar`, y por lo mismo: el webhook
        # llega por otra conexión y tiene que encontrar la referencia. En PSE
        # pesa todavía más, porque entre crear la transacción y que el banco
        # responda pueden pasar minutos.
        try:
            self.db.flush()
            self._audit(
                "pagar_pse", "PagoSuscripcion", pago.id, None, serialize_entity(pago),
                empresa_id=empresa.id,
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise BusinessError(
                "Ya hay un pago en proceso para esta empresa",
                code="pago_pendiente",
            ) from None

        nombre_pagador, telefono_pagador = self._datos_del_pagador(empresa, payload)
        try:
            datos = WompiClient().crear_transaccion_pse(
                referencia=referencia,
                monto_en_centavos=int((tarifa * 100).to_integral_value()),
                customer_email=self._correo_de_cobro(empresa),
                acceptance_token=aceptacion,
                banco=payload.banco,
                tipo_persona=payload.tipo_persona,
                tipo_documento=payload.tipo_documento,
                documento=payload.documento,
                descripcion=f"Suscripcion Lactis {empresa.nombre}"[:64],
                nombre_completo=nombre_pagador,
                telefono=telefono_pagador,
                redirect_url=settings.WOMPI_REDIRECT_URL or None,
            )
        except BusinessError:
            # La transacción no se creó: se cierra el pago para no dejar el
            # candado de PENDING puesto sin nada detrás que lo resuelva.
            self.aplicar_resultado(
                pago, TRANSACCION_ERROR, detalle="No se pudo iniciar el pago con PSE"
            )
            self.db.commit()
            raise

        pago.wompi_transaction_id = datos.get("id")
        # La URL del banco casi nunca viene en la respuesta de creación (se
        # verificó contra el sandbox): Wompi la publica un segundo después. Si
        # no está, se le pregunta unas pocas veces antes de responder, porque
        # sin ella la persona se queda mirando un pago que no puede aprobar.
        pago.url_banco = url_del_banco(datos)
        if not pago.url_banco and pago.wompi_transaction_id:
            pago.url_banco = WompiClient().esperar_url_del_banco(pago.wompi_transaction_id)
        estado = (datos.get("status") or TRANSACCION_PENDIENTE).upper()
        if estado in ESTADOS_TRANSACCION_FINALES:
            # Raro en PSE (nace PENDING), pero si Wompi ya resolvió, se aplica
            self.aplicar_resultado(pago, estado, detalle=datos.get("status_message"))
        else:
            self.db.flush()
        self.db.commit()
        # El resumen PRIMERO: por dentro hace el poll perezoso, que puede
        # rescatar la url_banco. Python evalúa la tupla de izquierda a derecha,
        # así que leer `pago.url_banco` antes devolvería el valor de antes del
        # rescate — justo el caso en que hace falta.
        resumen = self.resumen()
        return pago, pago.url_banco, resumen

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
            # populate_existing: sin esto el FOR UPDATE bloquea la fila pero
            # SQLAlchemy devuelve el objeto que ya tenía en memoria, con los
            # valores VIEJOS. Y es peor de lo que parece: quien llega segundo se
            # queda esperando el candado justo mientras el primero acredita, así
            # que al soltarse el candado sus datos son exactamente los de antes
            # de la acreditación. Las dos guardas de idempotencia de abajo leían
            # ese estado rancio y podían acreditar el mismo mes dos veces.
            .execution_options(populate_existing=True)
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
                # Igual que arriba: hay que releer la fila que se acaba de
                # bloquear. `pagada_hasta` rancio significa sumarle un mes a una
                # fecha que otro proceso ya movió, o sea perder un mes pagado.
                select(Empresa)
                .where(Empresa.id == pago.empresa_id)
                .execution_options(populate_existing=True)
                .with_for_update()
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
        transaccion_id = transaccion.get("id")

        # SE BUSCA POR EL ID DE LA TRANSACCIÓN, NO POR LA REFERENCIA.
        #
        # El checksum solo cubre lo que exige PROPIEDADES_OBLIGATORIAS: el id,
        # el estado y el importe. La `reference` NO va firmada. Buscar por ella
        # dejaba abierto lo siguiente: tomar un evento legítimo de aprobación,
        # cambiarle la referencia por la de OTRA empresa y reenviarlo. El
        # checksum seguía cuadrando (los tres campos firmados no se tocaron) y
        # el mes se le acreditaba a quien no pagó. Buscando por el id firmado,
        # el evento solo puede tocar la transacción que él mismo declara.
        pago = None
        if transaccion_id:
            pago = self.db.scalars(
                select(PagoSuscripcion).where(
                    PagoSuscripcion.wompi_transaction_id == str(transaccion_id)
                )
            ).first()
        if pago is None and referencia:
            # Respaldo para la única ventana legítima: el webhook llegó tan
            # rápido que el id todavía no estaba guardado. Se acepta solo si el
            # pago no tiene id puesto; si lo tiene y es otro, no es este evento.
            pago = self.db.scalars(
                select(PagoSuscripcion).where(
                    PagoSuscripcion.referencia == referencia,
                    PagoSuscripcion.wompi_transaction_id.is_(None),
                )
            ).first()
        if pago is None:
            return "desconocida"

        # El importe también va firmado: si no cuadra con lo que se cobró, este
        # evento no habla de este pago por mucho que el id coincida.
        centavos = transaccion.get("amount_in_cents")
        if centavos is not None:
            try:
                esperados = int((Decimal(str(pago.monto)) * 100).to_integral_value())
                if int(centavos) != esperados:
                    logger.warning(
                        "Evento de Wompi con importe que no cuadra para el pago %s: "
                        "%s centavos contra %s esperados",
                        pago.id,
                        centavos,
                        esperados,
                    )
                    return "desconocida"
            except (TypeError, ValueError, ArithmeticError):
                return "desconocida"

        if not pago.wompi_transaction_id and transaccion_id:
            pago.wompi_transaction_id = str(transaccion_id)
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
        # Solo los ids: el barrido tarda (una llamada a la pasarela por
        # empresa) y las filas cambian mientras corre. Cada empresa se relee
        # justo antes de decidir sobre ella.
        ids = list(
            self.db.scalars(
                select(Empresa.id).where(
                    Empresa.deleted_at.is_(None), Empresa.estado == "activo"
                )
            ).all()
        )
        for empresa_id in ids:
            # RELEER, no confiar en la foto del principio. Si mientras el cron
            # iba por otras empresas el webhook acreditó el PSE de ésta, la foto
            # vieja seguiría diciendo "vencida" y se le debitaría la tarjeta por
            # un mes ya pagado. Con populate_existing la fila se trae fresca
            # aunque el objeto siga en la sesión (expire_on_commit=False).
            empresa = self.db.scalars(
                select(Empresa)
                .where(Empresa.id == empresa_id)
                .execution_options(populate_existing=True)
            ).first()
            if empresa is None or empresa.deleted_at is not None or empresa.estado != "activo":
                continue
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
