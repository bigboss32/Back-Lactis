from app.common.repository import BaseRepository
from app.modules.suscripcion.models import FuentePagoSuscripcion, PagoSuscripcion


class FuentePagoSuscripcionRepository(BaseRepository[FuentePagoSuscripcion]):
    """Sin tenant_required: el webhook de Wompi y los cobros automáticos operan
    sin contexto de empresa (el pago se resuelve por su referencia, no por el
    header X-Empresa-Id)."""

    model = FuentePagoSuscripcion
    tenant_required = False


class PagoSuscripcionRepository(BaseRepository[PagoSuscripcion]):
    model = PagoSuscripcion
    tenant_required = False
