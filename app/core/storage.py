"""Almacenamiento de adjuntos PRIVADOS en Cloudflare R2 (compatible con S3).

Aquí vive todo lo que toca el bucket. Tres decisiones que mandan sobre el resto:

1. LOS OBJETOS SON PRIVADOS. No se guarda ninguna URL en la base ni se sirve
   ninguna URL pública. Cada vez que alguien quiere ver una imagen se le firma
   un enlace de corta duración, y ese enlace caduca solo. Guardar una URL sería
   guardar un permiso permanente en una columna, y con soportes de pago (fotos
   de transferencias, con nombres, cuentas y montos) eso no se hace.

2. LA APP TIENE QUE ARRANCAR SIN LLAVES. Igual que el cliente de Wompi: boto3
   se importa DENTRO del constructor y el constructor falla con un error de
   negocio legible, nunca al importar el módulo. Así el módulo de reventa
   completo sigue funcionando en un portátil sin llaves y en las pruebas; lo
   único que avisa que no está disponible es la parte de adjuntos.

3. EL CLIENTE SE INSTANCIA DENTRO DE CADA MÉTODO del servicio (no en el
   constructor ni a nivel de módulo), para que las pruebas lo reemplacen con
   monkeypatch y NO salgan a internet. Ver tests/test_reventa_adjuntos.py.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.core.logging_config import get_logger

logger = get_logger("storage")

# Tope duro de una URL firmada con SigV4 (7 días). No es una política nuestra:
# AWS/R2 rechazan la firma si X-Amz-Expires pasa de 604800 segundos, así que un
# enlace "para siempre" es imposible por diseño del protocolo. Se acota aquí para
# que una configuración con 30 días no produzca enlaces rotos en silencio.
MAX_SEGUNDOS_FIRMA = 7 * 24 * 60 * 60

# Colombia no tiene horario de verano: siempre UTC-5. Se usa un desfase fijo en
# vez de zoneinfo porque en Windows la base de datos de zonas horarias no viene
# instalada, y una fecha de caducidad mal calculada es peor que ninguna.
HORA_COLOMBIA = timezone(timedelta(hours=-5))


def r2_configurado() -> bool:
    """¿Hay llaves de R2? Sin las cuatro, la parte de adjuntos no opera."""
    return bool(
        settings.R2_ENDPOINT_URL
        and settings.R2_BUCKET
        and settings.R2_ACCESS_KEY_ID
        and settings.R2_SECRET_ACCESS_KEY
    )


MENSAJE_NO_CONFIGURADO = (
    "El almacenamiento de imágenes no está configurado. "
    "Los soportes no se pueden subir ni ver hasta que se configure"
)


def _nombre_ascii(nombre: str) -> str:
    """Nombre de archivo apto para viajar en una cabecera HTTP.

    El Content-Disposition de la respuesta firmada viaja como cabecera, y una
    cabecera con tildes o eñes la rechazan unos servidores y la corrompen otros.
    Se quitan los acentos (que es lo que se puede hacer sin cambiar el nombre) y
    lo que no sea imprimible en ASCII se cambia por guion bajo. Las comillas
    dobles se van sí o sí: cerrarían el valor de la cabecera antes de tiempo.
    """
    plano = unicodedata.normalize("NFKD", nombre or "")
    plano = plano.encode("ascii", "ignore").decode("ascii")
    limpio = "".join(c if 32 <= ord(c) < 127 and c != '"' else "_" for c in plano)
    limpio = limpio.strip() or "soporte"
    return limpio[:120]


class R2Client:
    """Cliente de Cloudflare R2 por su API compatible con S3.

    No expone `boto3` hacia afuera: el resto de la aplicación solo conoce estos
    tres verbos (subir, firmar, borrar). Cualquier fallo de red o del bucket
    sale como BusinessError con mensaje legible — nunca como un 500.
    """

    def __init__(self) -> None:
        if not r2_configurado():
            raise BusinessError(MENSAJE_NO_CONFIGURADO, code="r2_no_configurado")
        try:
            # Import PEREZOSO a propósito: si boto3 no está instalado, la
            # aplicación tiene que arrancar igual y fallar solo aquí, con un
            # mensaje que se entiende, cuando alguien intente usar adjuntos.
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise BusinessError(
                "Falta la librería de almacenamiento (boto3) en el servidor",
                code="r2_no_configurado",
            ) from exc

        self._bucket = settings.R2_BUCKET
        self._s3 = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name=settings.R2_REGION,
            config=Config(
                # R2 solo entiende firmas v4.
                signature_version="s3v4",
                # Sin timeout, una subida con señal mala en el campo deja el
                # worker colgado hasta que el proxy corta: el dueño ve "cargando"
                # eterno y vuelve a intentar, duplicando la foto.
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    # ------------------------------------------------------------------ verbos
    def subir(self, *, clave: str, contenido: bytes, content_type: str) -> None:
        """Guarda el objeto. Sin ACL: en R2 todo objeto nace privado."""
        try:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=clave,
                Body=contenido,
                ContentType=content_type,
            )
        except Exception as exc:  # boto3 lanza ClientError, BotoCoreError, etc.
            # El detalle técnico va al log; al usuario, algo que pueda hacer.
            logger.warning("R2 no pudo guardar %s: %s", clave, exc)
            raise BusinessError(
                "No fue posible guardar la imagen. Verifique la conexión e intente de nuevo",
                code="r2_error",
            ) from exc

    def enlace_firmado(
        self, *, clave: str, segundos: int, nombre_descarga: str | None = None
    ) -> str:
        """URL temporal que deja VER ese objeto y nada más.

        Va firmada con la llave secreta y lleva su propia caducidad dentro: no
        hay que revocar nada, se muere sola. `segundos` se acota al tope de
        SigV4 (7 días) porque más allá la firma la rechaza el propio R2.

        `nombre_descarga` fija el nombre con el que el navegador muestra o
        guarda el archivo. Va como `inline` para que al abrirlo desde WhatsApp
        se vea la foto en vez de descargarse un archivo suelto.
        """
        segundos = max(60, min(int(segundos), MAX_SEGUNDOS_FIRMA))
        params: dict[str, str] = {"Bucket": self._bucket, "Key": clave}
        if nombre_descarga:
            params["ResponseContentDisposition"] = (
                f'inline; filename="{_nombre_ascii(nombre_descarga)}"'
            )
        try:
            return self._s3.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=segundos
            )
        except Exception as exc:
            logger.warning("R2 no pudo firmar %s: %s", clave, exc)
            raise BusinessError(
                "No fue posible generar el enlace de la imagen. Intente de nuevo",
                code="r2_error",
            ) from exc

    def borrar(self, clave: str) -> None:
        """Borra el objeto del bucket.

        S3/R2 tratan el borrado como idempotente: borrar algo que ya no está
        también responde bien, así que reintentar no rompe nada. Si falla de
        verdad (red, permisos), se propaga: el servicio NO borra la fila, para
        que el dueño pueda reintentar y no queden archivos huérfanos pagando
        almacenamiento sin que nadie sepa que existen.
        """
        try:
            self._s3.delete_object(Bucket=self._bucket, Key=clave)
        except Exception as exc:
            logger.warning("R2 no pudo borrar %s: %s", clave, exc)
            raise BusinessError(
                "No fue posible borrar la imagen del almacenamiento. Intente de nuevo",
                code="r2_error",
            ) from exc


# ------------------------------------------------------- caducidad en cristiano
DIAS_SEMANA = (
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
)
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def texto_caducidad(momento: datetime) -> str:
    """"hasta el martes 5 de agosto a las 3:00 p. m." — en hora de Colombia.

    Lo arma el backend y no la pantalla a propósito: el dueño está repartiendo
    un enlace que da acceso a un soporte de pago y tiene que saber hasta cuándo
    sirve lo que reparte. Si la frase la construyera cada pantalla, tarde o
    temprano una lo mostraría en UTC —cinco horas corridas— o no lo mostraría.
    """
    local = momento.astimezone(HORA_COLOMBIA)
    hora12 = local.hour % 12 or 12
    sufijo = "a. m." if local.hour < 12 else "p. m."
    return (
        f"hasta el {DIAS_SEMANA[local.weekday()]} {local.day} de {MESES[local.month - 1]} "
        f"a las {hora12}:{local.minute:02d} {sufijo}"
    )


def caducidad_utc(segundos: int) -> datetime:
    """Momento exacto (UTC) en que muere un enlace firmado de `segundos`."""
    return datetime.now(timezone.utc) + timedelta(seconds=segundos)
