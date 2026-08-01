import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import EmailStr

from app.common.schemas import BaseSchema


class FuentePagoRead(BaseSchema):
    """Datos públicos de la tarjeta guardada (el PAN nunca llega al backend)."""

    id: uuid.UUID
    marca: str | None
    ultimos4: str | None
    exp_mes: str | None
    exp_anio: str | None
    customer_email: str | None


class SuscripcionResumen(BaseSchema):
    """Bloque liviano de /auth/me: lo mínimo para el banner y el guard."""

    estado: str
    # Último día cubierto (pagado o de prueba); null solo para exentas
    pagada_hasta: date | None
    dias_restantes: int | None
    dias_gracia: int
    tarifa: Decimal
    tiene_fuente_pago: bool


class SuscripcionDetalle(SuscripcionResumen):
    """Respuesta de GET /suscripcion: el resumen más lo que ve la pantalla."""

    exenta: bool
    pago_pendiente: bool
    fuente_pago: FuentePagoRead | None


class PagoSuscripcionRead(BaseSchema):
    id: uuid.UUID
    referencia: str
    wompi_transaction_id: str | None
    monto: Decimal
    moneda: str
    estado_transaccion: str
    origen: str
    periodo_desde: date | None
    periodo_hasta: date | None
    created_at: datetime


class TokenAceptacion(BaseSchema):
    """Token de aceptación de Wompi con el permalink del documento que cubre."""

    acceptance_token: str
    permalink: str


class ConfigWompiResponse(BaseSchema):
    """Lo que el formulario de tarjeta necesita para hablar directo con Wompi."""

    public_key: str
    tokenizacion_url: str
    acceptance: TokenAceptacion
    personal_data_auth: TokenAceptacion


class FuentePagoCreate(BaseSchema):
    """Alta de fuente de pago: token creado por el NAVEGADOR contra Wompi más
    los dos tokens de aceptación que el usuario marcó en el formulario."""

    token: str
    customer_email: EmailStr
    acceptance_token: str
    accept_personal_auth: str


class PagarResponse(BaseSchema):
    """Resultado de 'Pagar ahora': el pago (APPROVED o DECLINED, ambos 200)
    y el estado de la suscripción ya recalculado."""

    pago: PagoSuscripcionRead
    suscripcion: SuscripcionDetalle


class CobrarVencidasResponse(BaseSchema):
    """Conteos del barrido de cobro automático."""

    evaluadas: int
    cobradas: int
    rechazadas: int
    pendientes: int
    omitidas: int
    errores: int
