"""Fuentes de pago y pagos de la suscripción mensual (Wompi).

Ambas tablas llevan empresa_id como FK PLANA, sin TenantMixin: no son datos del
negocio del tenant sino de la relación comercial entre la plataforma y la
empresa. Las consulta el administrador de la empresa (su propia tarjeta y sus
pagos) y las escribe también el webhook de Wompi, que llega SIN contexto de
empresa (se resuelve por la referencia del pago).

El número de tarjeta jamás pasa por aquí: el navegador tokeniza directo contra
Wompi y el backend solo guarda el id de la fuente y los datos públicos
(marca, últimos 4 dígitos, expiración).
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin
from app.core.database import Base

# Ciclo de vida de la fuente (columna estado del AuditMixin)
FUENTE_ACTIVA = "activo"
FUENTE_REEMPLAZADA = "reemplazada"

# Estado de la TRANSACCIÓN en Wompi (aparte del estado del mixin): se guarda
# tal cual lo reporta la pasarela, en mayúsculas.
TRANSACCION_PENDIENTE = "PENDING"
TRANSACCION_APROBADA = "APPROVED"
TRANSACCION_RECHAZADA = "DECLINED"
TRANSACCION_ANULADA = "VOIDED"
TRANSACCION_ERROR = "ERROR"

ESTADOS_TRANSACCION_FINALES = (
    TRANSACCION_APROBADA,
    TRANSACCION_RECHAZADA,
    TRANSACCION_ANULADA,
    TRANSACCION_ERROR,
)

# Con qué se pagó.
#
# La diferencia que importa: la TARJETA se guarda como fuente de pago y se puede
# cobrar sola cada mes; PSE no. PSE es un débito de una cuenta bancaria que exige
# que la persona entre al portal de su banco y lo apruebe, así que sirve para
# pagar ESTE mes y nada más. Por eso el cobro automático solo mira las tarjetas
# (cobrar_vencidas busca una fuente activa, y un pago PSE no crea ninguna).
METODO_TARJETA = "CARD"
METODO_PSE = "PSE"

# Quién disparó el cobro
ORIGEN_MANUAL = "manual"          # botón "Pagar ahora"
ORIGEN_AUTOMATICO = "automatico"  # gatillo perezoso de /auth/me
ORIGEN_CRON = "cron"              # POST /suscripcion/cobrar-vencidas


class FuentePagoSuscripcion(AuditMixin, Base):
    __tablename__ = "fuentes_pago_suscripcion"

    empresa_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("empresas.id"), index=True, nullable=False
    )
    # Id numérico de la fuente en Wompi (con él se cobra sin volver a pedir tarjeta)
    wompi_payment_source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    marca: Mapped[str | None] = mapped_column(String(30))  # VISA, MASTERCARD, ...
    ultimos4: Mapped[str | None] = mapped_column(String(4))
    exp_mes: Mapped[str | None] = mapped_column(String(2))
    exp_anio: Mapped[str | None] = mapped_column(String(2))
    customer_email: Mapped[str | None] = mapped_column(String(150))
    # Metadatos públicos que devolvió Wompi (nunca contiene el PAN)
    detalle: Mapped[str | None] = mapped_column(Text)


class PagoSuscripcion(AuditMixin, Base):
    __tablename__ = "pagos_suscripcion"
    __table_args__ = (
        # Candado real anti doble cobro: UN solo pago PENDING por empresa.
        # Índice único PARCIAL, como los de usuario_roles (Postgres y SQLite).
        Index(
            "uq_pago_suscripcion_pending",
            "empresa_id",
            unique=True,
            postgresql_where=text("estado_transaccion = 'PENDING'"),
            sqlite_where=text("estado_transaccion = 'PENDING'"),
        ),
    )

    empresa_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("empresas.id"), index=True, nullable=False
    )
    # Nullable: si la fuente se elimina o reemplaza, el histórico de pagos queda
    fuente_pago_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fuentes_pago_suscripcion.id"), index=True
    )
    # Referencia propia enviada a Wompi: por ella el webhook encuentra el pago
    # Con qué se pagó: 'CARD' (tarjeta guardada) o 'PSE' (débito por el banco).
    metodo: Mapped[str] = mapped_column(
        String(10), default=METODO_TARJETA, server_default=METODO_TARJETA, nullable=False
    )
    # Solo en PSE: la URL del portal del banco a la que hay que mandar a la
    # persona. Se guarda para poder RETOMAR el pago si cerró la pestaña: sin
    # esto, el candado de "ya hay un pago en proceso" la dejaría esperando el
    # vencimiento sin forma de terminar lo que empezó.
    url_banco: Mapped[str | None] = mapped_column(Text)
    referencia: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    wompi_transaction_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    monto: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(String(3), default="COP", server_default="COP")
    estado_transaccion: Mapped[str] = mapped_column(
        String(20),
        default=TRANSACCION_PENDIENTE,
        server_default=TRANSACCION_PENDIENTE,
        index=True,
    )
    origen: Mapped[str] = mapped_column(String(20), default=ORIGEN_MANUAL)
    # Periodo de vigencia que cubrió este pago (se fija al quedar APPROVED)
    periodo_desde: Mapped[date | None] = mapped_column(Date)
    periodo_hasta: Mapped[date | None] = mapped_column(Date)
    # Mensaje de la pasarela (status_message, motivo del rechazo, etc.)
    detalle: Mapped[str | None] = mapped_column(Text)

    fuente_pago = relationship("FuentePagoSuscripcion", lazy="joined")
