"""Cliente HTTP de Wompi y utilidades de firma/checksum de la integración.

- La llave PÚBLICA lee los tokens de aceptación del comercio.
- La llave PRIVADA crea fuentes de pago y transacciones.
- El PAN jamás pasa por aquí: el navegador tokeniza directo contra Wompi y el
  backend solo maneja tokens e ids. NUNCA loguear payloads de tarjetas ni llaves.
"""
import hashlib
import hmac
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.core.logging_config import get_logger

logger = get_logger("wompi")

# Campos que el checksum de un evento de transacción TIENE que cubrir. Wompi los
# documenta en signature.properties, pero esa lista viaja dentro del propio
# evento: si no se exige un mínimo, quien mande el evento decide qué queda
# firmado y qué no (ver validar_checksum_evento).
PROPIEDADES_OBLIGATORIAS = (
    "transaction.id",
    "transaction.status",
    "transaction.amount_in_cents",
)


def firma_integridad(referencia: str, monto_en_centavos: int, moneda: str, secreto: str) -> str:
    """Firma de integridad de una transacción (docs.wompi.co): SHA-256 hex de
    la concatenación '<referencia><monto_en_centavos><moneda><secreto>'."""
    cadena = f"{referencia}{monto_en_centavos}{moneda}{secreto}"
    return hashlib.sha256(cadena.encode("utf-8")).hexdigest()


def validar_checksum_evento(payload: dict[str, Any], secreto: str) -> bool:
    """Valida el checksum de un evento del webhook de Wompi.

    Wompi manda en signature.properties las rutas (con punto, dentro de data)
    de los valores que, concatenados en ese orden y seguidos del timestamp y
    del secreto de eventos, deben producir el SHA-256 hex de signature.checksum.
    La comparación usa compare_digest (tiempo constante) y es insensible a
    mayúsculas: Wompi documenta el checksum en MAYÚSCULAS.
    """
    firma = payload.get("signature") or {}
    checksum = firma.get("checksum") or ""
    propiedades = firma.get("properties") or []
    timestamp = payload.get("timestamp")
    if not checksum or timestamp is None:
        return False
    # Los campos que DECIDEN el resultado tienen que estar firmados, sí o sí.
    #
    # La lista sale del propio payload, que es lo que manda Wompi, pero eso solo
    # es seguro si se exige que incluya lo que importa. Sin esta comprobación:
    #   - `properties: []` pasaba, y entonces la cadena era solo timestamp +
    #     secreto: el checksum no autenticaba NADA del bloque `data`, así que el
    #     mismo checksum valía para cualquier transacción, importe y estado.
    #   - controlando a la vez las RUTAS y los VALORES se podía reconstruir la
    #     misma cadena firmada repartiendo los campos de otra forma, y convertir
    #     un evento legítimo observado una sola vez en una firma reutilizable.
    # Exigirlos deja fuera los dos caminos: el estado y el importe quedan
    # siempre dentro de lo firmado.
    faltantes = [p for p in PROPIEDADES_OBLIGATORIAS if p not in propiedades]
    if faltantes:
        logger.warning(
            "Evento de Wompi sin firmar los campos que deciden el resultado: faltan %s",
            ", ".join(faltantes),
        )
        return False
    datos = payload.get("data") or {}
    partes: list[str] = []
    for propiedad in propiedades:
        valor: Any = datos
        for clave in str(propiedad).split("."):
            if not isinstance(valor, dict):
                return False
            valor = valor.get(clave)
        if valor is None:
            return False
        partes.append(str(valor))
    cadena = "".join(partes) + str(timestamp) + secreto
    esperado = hashlib.sha256(cadena.encode("utf-8")).hexdigest()
    # Se comparan BYTES: compare_digest con str exige ASCII puro y lanza
    # TypeError si el otro lado trae una tilde o cualquier carácter fuera de
    # ASCII. Como el checksum llega del exterior, eso convertía un payload
    # cualquiera en un 500 público del webhook en vez del 400 que corresponde.
    return hmac.compare_digest(
        esperado.lower().encode("utf-8"), str(checksum).lower().encode("utf-8")
    )


class WompiClient:
    """Cliente síncrono de la API de Wompi.

    El service lo instancia DENTRO de cada método (no en el constructor ni a
    nivel de módulo) para que las pruebas lo reemplacen con monkeypatch sin
    tocar la inyección de dependencias, y para que la app arranque aunque las
    llaves no estén configuradas.
    """

    def __init__(self) -> None:
        if not settings.WOMPI_PUBLIC_KEY or not settings.WOMPI_PRIVATE_KEY:
            raise BusinessError(
                "La pasarela de pagos no está configurada: faltan las llaves de Wompi",
                code="wompi_no_configurado",
            )
        self.base_url = settings.WOMPI_BASE_URL.rstrip("/")

    # ------------------------------------------------------------- transporte
    def _request(
        self,
        metodo: str,
        ruta: str,
        *,
        llave: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as cliente:
                respuesta = cliente.request(
                    metodo,
                    f"{self.base_url}{ruta}",
                    headers={"Authorization": f"Bearer {llave}"},
                    json=json,
                )
        except httpx.HTTPError as exc:
            logger.warning("Wompi no disponible (%s %s): %s", metodo, ruta, exc)
            raise BusinessError(
                "No fue posible comunicarse con la pasarela de pagos. Intente de nuevo",
                code="wompi_no_disponible",
            ) from exc
        try:
            cuerpo = respuesta.json()
        except ValueError:
            cuerpo = {}
        if respuesta.status_code >= 400:
            error = cuerpo.get("error") or {}
            mensaje = error.get("reason") or error.get("type") or f"HTTP {respuesta.status_code}"
            # Solo el motivo: jamás el payload (puede referirse a datos de pago)
            logger.warning("Wompi rechazó %s %s: %s", metodo, ruta, mensaje)
            raise BusinessError(
                f"La pasarela de pagos rechazó la operación: {mensaje}",
                code="wompi_error",
                extra={"wompi": error.get("messages") or {}},
            )
        return cuerpo.get("data") or {}

    # -------------------------------------------------------------- endpoints
    def tokens_aceptacion(self) -> dict[str, Any]:
        """GET /merchants/{public_key}: trae los DOS tokens de aceptación
        (términos y datos personales) con sus permalinks. Son JWT que expiran,
        así que se piden frescos en cada carga del formulario."""
        return self._request(
            "GET", f"/merchants/{settings.WOMPI_PUBLIC_KEY}", llave=settings.WOMPI_PUBLIC_KEY
        )

    def crear_fuente_pago(
        self,
        *,
        token: str,
        customer_email: str,
        acceptance_token: str,
        accept_personal_auth: str,
    ) -> dict[str, Any]:
        """POST /payment_sources: convierte el token de tarjeta (creado por el
        NAVEGADOR) en una fuente de pago recurrente. Exige AMBOS tokens de
        aceptación: términos y autorización de datos personales."""
        return self._request(
            "POST",
            "/payment_sources",
            llave=settings.WOMPI_PRIVATE_KEY,
            json={
                "type": "CARD",
                "token": token,
                "customer_email": customer_email,
                "acceptance_token": acceptance_token,
                "accept_personal_auth": accept_personal_auth,
            },
        )

    def crear_transaccion(
        self,
        *,
        referencia: str,
        monto_en_centavos: int,
        customer_email: str,
        payment_source_id: int,
    ) -> dict[str, Any]:
        """POST /transactions: cobra contra la fuente de pago guardada."""
        if not settings.WOMPI_INTEGRITY_SECRET:
            raise BusinessError(
                "La pasarela de pagos no está configurada: falta el secreto de integridad",
                code="wompi_no_configurado",
            )
        moneda = "COP"
        return self._request(
            "POST",
            "/transactions",
            llave=settings.WOMPI_PRIVATE_KEY,
            json={
                "amount_in_cents": monto_en_centavos,
                "currency": moneda,
                "signature": firma_integridad(
                    referencia, monto_en_centavos, moneda, settings.WOMPI_INTEGRITY_SECRET
                ),
                "customer_email": customer_email,
                "reference": referencia,
                "payment_source_id": payment_source_id,
                "recurrent": True,
                "payment_method": {"type": "CARD", "installments": 1},
            },
        )

    def consultar_transaccion(self, transaction_id: str) -> dict[str, Any]:
        """GET /transactions/{id}: estado actual de una transacción (poll)."""
        return self._request(
            "GET", f"/transactions/{transaction_id}", llave=settings.WOMPI_PRIVATE_KEY
        )
