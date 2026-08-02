from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    PROJECT_NAME: str = "Quesera ERP"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"

    DATABASE_URL: str = "postgresql+psycopg2://quesera:quesera@localhost:5433/quesera_erp"

    SECRET_KEY: str = "cambiar-en-produccion-por-una-clave-larga-y-aleatoria"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30
    MAX_LOGIN_ATTEMPTS: int = 5

    # Incluye el puerto del dev-server web de Flutter (verificación local)
    CORS_ORIGINS: list[str] = [
        "http://localhost:4200",
        "http://localhost:8000",
        "http://localhost:5000",
    ]

    UPLOADS_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 10

    FIRST_ADMIN_USERNAME: str = "admin"
    FIRST_ADMIN_PASSWORD: str = "Admin123*"
    FIRST_ADMIN_EMAIL: str = "admin@quesera.local"
    SEED_DEMO_DATA: bool = True

    # ---- Suscripción mensual (pasarela Wompi) ----
    # Las llaves viven SOLO en el .env local (gitignored) y en el dashboard del
    # hosting. Vacías por defecto: la app arranca sin Wompi y falla con un error
    # legible (wompi_no_configurado) al primer uso, nunca al importar.
    WOMPI_BASE_URL: str = "https://sandbox.wompi.co/v1"
    WOMPI_PUBLIC_KEY: str = ""
    WOMPI_PRIVATE_KEY: str = ""
    WOMPI_INTEGRITY_SECRET: str = ""
    WOMPI_EVENT_SECRET: str = ""
    # A dónde devuelve Wompi a la persona cuando termina en el portal del
    # banco (PSE). Si queda vacío, Wompi la deja en su propia pantalla y la
    # persona tiene que volver a la aplicación a mano.
    WOMPI_REDIRECT_URL: str = ""
    # Tarifa mensual en COP para empresas sin tarifa propia (empresas.tarifa_mensual NULL)
    SUSCRIPCION_TARIFA_DEFAULT: int = 100000
    SUSCRIPCION_DIAS_GRACIA: int = 5
    SUSCRIPCION_DIAS_AVISO: int = 5
    SUSCRIPCION_DIAS_PRUEBA: int = 30
    # Secreto del endpoint POST /suscripcion/cobrar-vencidas (vacío = cron deshabilitado)
    SUSCRIPCION_CRON_SECRET: str = ""

    # ---- Adjuntos en Cloudflare R2 (almacenamiento compatible con S3) ----
    # Mismo trato que las llaves de Wompi: viven SOLO en el .env local
    # (gitignored) y en el dashboard del hosting. Vacías por defecto, para que la
    # app arranque sin R2 —en las pruebas y en un portátil sin llaves— y el
    # módulo de reventa siga funcionando completo: lo único que avisa que no está
    # disponible es la parte de adjuntos, y avisa con un mensaje legible, nunca
    # reventando el import.
    #
    # Los objetos son PRIVADOS: no se guardan ni se sirven URLs públicas. Para
    # ver una imagen se firma un enlace al momento (ver app/core/storage.py).
    R2_ENDPOINT_URL: str = ""
    R2_BUCKET: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    # R2 no tiene regiones como AWS: su cliente S3 espera literalmente "auto".
    R2_REGION: str = "auto"

    # Cuánto vive el enlace de VER (el de la pantalla). Corto a propósito: si la
    # URL queda en el historial del navegador, en un log de proxy o en la vista
    # previa de un chat, ya no sirve para cuando alguien la encuentre. Quince
    # minutos alcanzan de sobra para abrir varias fotos con señal mala.
    R2_URL_VER_MINUTOS: int = 15
    # Cuánto vive el enlace de COMPARTIR (el que se manda por WhatsApp). Siete
    # días es el TOPE DURO de una URL firmada con SigV4: más no se puede, aunque
    # se configure. Ver comentario en R2Client.enlace_firmado.
    R2_URL_COMPARTIR_DIAS: int = 7
    # Tamaño máximo por archivo. Una foto de celular pesa entre 2 y 8 MB, así que
    # 10 MB (el tope general de UPLOADS) se queda corto para una foto de un
    # equipo nuevo. 15 MB da aire sin dejar subir un video renombrado a .jpg.
    ADJUNTOS_MAX_MB: int = 15
    # Cuántos soportes caben en una misma compra o venta. Es un soporte de pago,
    # no un álbum: veinte es holgado y evita que un error de la interfaz (o
    # alguien con malas intenciones) llene el bucket a costa del dueño.
    ADJUNTOS_MAX_POR_DOCUMENTO: int = 20

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
