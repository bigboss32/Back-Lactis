import uuid
from typing import Any

from sqlalchemy import JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AuditMixin, HoraDeRegistroMixin
from app.core.database import Base


class Auditoria(HoraDeRegistroMixin, AuditMixin, Base):
    """Bitácora de todas las operaciones de escritura del sistema.

    La fecha de la operación es created_at; antes/después guardan el snapshot
    JSON de la entidad para trazabilidad completa.

    LA HORA LA ESCRIBE LA APLICACIÓN, CON MICROSEGUNDOS (`HoraDeRegistroMixin`), y
    esto NO es un detalle técnico: en la bitácora el ORDEN es el dato. Se lee de la
    más nueva a la más vieja (`created_at` descendente, ver `BaseRepository`) y es la
    respuesta a "¿por qué el comprobante cambió de cifra?": el dueño mira el último
    renglón. Si dos renglones traen la MISMA hora, cuál sale de último lo decide el
    motor, y el libro cuenta la historia al revés —"le cambió el flete a 2 días"
    cuando el último recálculo no movió un peso—.

    Y empataban de verdad, en los dos motores, por razones distintas:

    · En SQLite —donde corren las pruebas— CURRENT_TIMESTAMP tiene resolución de UN
      SEGUNDO: dos renglones seguidos caen en el mismo segundo y empatan. Así se
      atrapó esto, con dos recálculos seguidos del mismo comprobante.
    · En Postgres —que es producción— `now()` es la hora de la TRANSACCIÓN: TODOS los
      renglones que deja una misma petición traen el mismo instante exacto. Y una
      sola petición deja varios (recalcular el flete anota además cada liquidación de
      leche que puso al día), así que allá el empate no es cuestión de suerte: es
      seguro.

    Es el mismo remedio que ya se le había puesto al reparto FIFO de reventa cuando
    el desempate de la plata terminaba dependiendo de un UUID aleatorio. No cambia el
    esquema (misma columna, mismo `server_default`), así que no hay migración que
    correr y las filas viejas se siguen leyendo igual.
    """

    __tablename__ = "auditorias"

    # Uuid plano (sin FK) para que la bitácora sobreviva a cualquier borrado
    empresa_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    ip: Mapped[str | None] = mapped_column(String(60))
    modulo: Mapped[str] = mapped_column(String(50), index=True)
    accion: Mapped[str] = mapped_column(String(30), index=True)
    entidad: Mapped[str] = mapped_column(String(80))
    entidad_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    antes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    despues: Mapped[dict[str, Any] | None] = mapped_column(JSON)
