import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import EmailStr, Field

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
    # Con qué se pagó: 'CARD' o 'PSE'
    metodo: str = "CARD"
    # Solo en PSE y mientras esté PENDING: el portal del banco donde quedó el
    # pago a medias, para poder retomarlo en vez de esperar a que expire.
    url_banco: str | None = None


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


# ------------------------------------------------------------------- PSE
class BancoPSE(BaseSchema):
    """Un banco habilitado para PSE, como lo devuelve Wompi."""

    financial_institution_code: str
    financial_institution_name: str


class PagarPseRequest(BaseSchema):
    """Lo que hay que saber para mandar a la persona al portal de su banco.

    Son los datos que exige PSE, no un capricho: el banco identifica a quien
    debita por su documento, y necesita saber si es persona natural o empresa
    porque el débito sale de cuentas distintas.
    """

    banco: str = Field(min_length=1, max_length=20, description="Código del banco en Wompi")
    # PSE lo maneja así: "0" natural, "1" jurídica. Se deja como texto porque es
    # lo que espera la pasarela y convertirlo a número solo invita a un 0 perdido.
    tipo_persona: Literal["0", "1"] = "0"
    tipo_documento: Literal["CC", "CE", "NIT", "TI", "PP"] = "CC"
    documento: str = Field(min_length=4, max_length=20)


class PagarPseResponse(BaseSchema):
    """El pago queda PENDING y la persona tiene que ir al banco a aprobarlo.

    `url_banco` es a donde hay que mandarla. No hay un resultado que mostrar
    todavía: llega por el webhook cuando el banco responde.
    """

    pago: PagoSuscripcionRead
    url_banco: str | None
    suscripcion: SuscripcionDetalle
