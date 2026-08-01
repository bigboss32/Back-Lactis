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

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
