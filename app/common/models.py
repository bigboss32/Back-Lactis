"""Mixins base: toda tabla del sistema hereda auditoría, soft delete y estado.

- AuditMixin: id UUID, timestamps, soft delete, created_by/updated_by, estado.
- TenantMixin: empresa_id obligatorio para aislamiento multi-tenant.
- HoraDeRegistroMixin: `created_at` con microsegundos, escrito por la aplicación,
  para las tablas donde el ORDEN de las filas es un dato y no un adorno.
"""
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

ESTADO_ACTIVO = "activo"
ESTADO_INACTIVO = "inactivo"


class AuditMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    estado: Mapped[str] = mapped_column(
        String(30), default=ESTADO_ACTIVO, server_default=ESTADO_ACTIVO, index=True
    )


class TenantMixin:
    """Toda entidad de negocio pertenece a una empresa (multi-tenant por fila)."""

    @declared_attr
    def empresa_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(ForeignKey("empresas.id"), index=True, nullable=False)


# ------------------------------------------------- la hora que ordena los hechos
_UN_MICROSEGUNDO = timedelta(microseconds=1)
_candado_de_la_hora = threading.Lock()
_ultima_hora_entregada: datetime | None = None


def _hora_de_registro() -> datetime:
    """La hora de registro de una fila: UTC, con microsegundos y ESTRICTAMENTE
    CRECIENTE dentro del proceso.

    Lo de "estrictamente creciente" es la parte que importa y no es paranoia: el
    reloj de pared NO avanza en cada llamada. En Windows (donde se corren las
    pruebas) `datetime.now()` se mueve cada uno o dos milisegundos, así que cinco
    llamadas seguidas devuelven la MISMA hora; en Linux es más fino pero tampoco
    hay garantía. Y aquí la hora ES la llave de orden del reparto FIFO: si dos
    filas distintas empataran, el desempate volvería a caer en criterios que no
    son "cuál se registró primero" (ver `HoraDeRegistroMixin`). Cuando el reloj no
    alcanza a moverse se avanza UN MICROSEGUNDO sobre la última hora entregada:
    la hora sigue siendo la real con un error de microsegundos —que a ninguna
    cifra del negocio le cambia nada, porque los informes van por fecha— y en
    cambio el orden de registro queda siendo un hecho.

    El candado es porque las peticiones sincrónicas de FastAPI corren en un pool
    de hilos: dos a la vez podrían leer y escribir `_ultima_hora_entregada`
    entrelazadas y entregar la misma hora, que es justo lo que esto evita.

    Ojo con lo que NO garantiza: si producción corre con varios procesos, cada uno
    lleva su propia última hora, así que dos filas de procesos distintos escritas
    en el mismo microsegundo todavía podrían empatar. Para eso está el resto de la
    llave de orden del repositorio, que desempata con datos del negocio y no con
    el `id`.
    """
    global _ultima_hora_entregada
    with _candado_de_la_hora:
        ahora = datetime.now(timezone.utc)
        if _ultima_hora_entregada is not None and ahora <= _ultima_hora_entregada:
            ahora = _ultima_hora_entregada + _UN_MICROSEGUNDO
        _ultima_hora_entregada = ahora
        return ahora


class HoraDeRegistroMixin:
    """`created_at` lo escribe LA APLICACIÓN, no el reloj de la base.

    PÓNGASELO A TODA TABLA DONDE EL ORDEN DE LAS FILAS SEA UN DATO, y no un adorno.
    Hoy lo llevan cinco:

    · compras, ventas y ajustes de reventa, donde el orden decide PLATA: el reparto
      FIFO consume el inventario en ese orden, y de ahí sale a qué productor se le
      carga el costo de una venta y cuánta ganancia le queda (ver
      `app/modules/reventa/lotes.py` y la llave de orden de
      `app/modules/reventa/repository.py`). "Se vende primero lo que se compró
      primero" necesita, entonces, poder decir cuál se registró primero;
    · la BITÁCORA (`Auditoria`), que se lee de la más nueva a la más vieja y es la
      respuesta a "¿por qué el comprobante cambió de cifra?": con dos renglones
      empatados el libro cuenta la historia al revés;
    · y los SOPORTES DE PAGO (`AdjuntoPagoLiquidacion`), que se listan "primero la
      que mandó primero" y suben todos en una misma petición.

    El `server_default=func.now()` que traía el AuditMixin NO alcanza para eso, y
    por dos razones distintas en cada motor:

    - En SQLite —donde corren las pruebas— CURRENT_TIMESTAMP tiene resolución de UN
      SEGUNDO. Dos compras registradas seguidas caen en el mismo segundo y empatan.
    - En Postgres —que es producción— `now()` es la hora de la TRANSACCIÓN, así que
      todas las filas escritas en la misma petición traen el mismo instante.

    Empatar ahí obligaba a desempatar más abajo, y el último criterio era el `id`,
    que es un UUID ALEATORIO: a cuál productor se le consumían los kilos primero lo
    decidía la suerte, y una misma base podía dar dos respuestas distintas. Con la
    hora puesta desde la aplicación, cada fila tiene su propio instante con
    microsegundos, el orden de registro es un hecho y NO una suposición, y da lo
    mismo en los dos motores.

    NO CAMBIA EL ESQUEMA: la columna es idéntica (mismo tipo, mismo
    `server_default`, misma restricción). Lo único que cambia es que el valor viaja
    en el INSERT en vez de dejárselo a la base, así que no hay migración que correr
    y las filas que ya existen se siguen leyendo igual. El `server_default` se
    conserva a propósito: es la red por si algún día una fila entra por SQL crudo.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_hora_de_registro,
        nullable=False,
    )
