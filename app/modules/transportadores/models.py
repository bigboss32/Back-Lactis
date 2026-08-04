import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class Transportador(TenantMixin, AuditMixin, Base):
    __tablename__ = "transportadores"

    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    documento: Mapped[str | None] = mapped_column(String(30), index=True)
    telefono: Mapped[str | None] = mapped_column(String(30))

    # TARIFA GENERAL por litro transportado (ej. $100, $130, $242,76).
    #
    # Antes esta era LA tarifa, porque un transportador tenía UNA ruta. El dueño
    # pidió lo contrario: "ahora el transportador puede tener varias rutas, por
    # ejemplo este tuvo que hacer las dos... pero cada ruta puede tener un valor
    # diferente de litro por leche". La tarifa de verdad vive entonces en `rutas`
    # (una fila por ruta, con su propio valor), y esta columna se queda como la
    # que aplica CUANDO NO HAY RUTA DE DÓNDE SACARLA: la recepción que quedó sin
    # ruta, o una ruta a la que no se le puso tarifa propia.
    #
    # No es duplicar la verdad: en esos dos casos es el único valor posible, y sin
    # ella el flete quedaría en cero callado —el transportador trabajando gratis
    # sin que nadie se dé cuenta hasta la liquidación—. La cuenta de cuál de las
    # dos manda está en UN SOLO SITIO, `tarifas.tarifa_por_litro`, para que
    # recepción y liquidaciones no se puedan desincronizar.
    valor_transporte: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))

    # lazy="selectin" y NUNCA "joined": en este proyecto un lazy="joined" sobre
    # una FK anulable ya rompió un `SELECT ... FOR UPDATE` con un 0A000 de
    # Postgres (ver el encabezado de liquidaciones/service.py). El selectin sale
    # en una consulta aparte, así que nunca le agrega un JOIN a la consulta que lo
    # trajo y no puede volver a romper eso.
    rutas: Mapped[list["TransportadorRuta"]] = relationship(
        back_populates="transportador",
        cascade="all, delete-orphan",
        # Orden estable para que la pantalla y la auditoría no barajen las filas
        # entre una lectura y otra. Es por id de ruta y no por nombre porque el
        # order_by de una relación solo ve columnas de su propia tabla.
        order_by="TransportadorRuta.ruta_id",
        lazy="selectin",
    )


class TransportadorRuta(Base):
    """La TARIFA POR LITRO que un transportador cobra EN UNA RUTA.

    Tabla puente con dato encima, no un `secondary` pelado: lo que se guarda acá
    no es "este señor hace esta ruta" sino cuánto cobra por litro haciéndola, y
    eso es plata. Por eso es un modelo propio (el `rol_permisos` de usuarios no
    sirve de molde: esa sí es una tabla de dos columnas y nada más).

    NO lleva `empresa_id` ni soft delete, y las dos cosas son a propósito:

    · El aislamiento por empresa ya lo da el transportador, que sí es multi-tenant
      y es el único camino por el que se llega a estas filas; y la ruta se valida
      contra el repositorio de rutas DE LA EMPRESA antes de guardarla, así que una
      ruta ajena no entra (ver TransportadorService._filas_de_rutas). Repetir
      `empresa_id` acá sería un dato más que se puede desincronizar del padre.
    · El soft delete pelearía con el único de (transportador_id, ruta_id): al
      volver a agregar una ruta que se había quitado, la fila borrada en suave
      seguiría ocupando la pareja y el índice reventaría. La lista se reemplaza
      completa con delete-orphan, o sea borrado de verdad, y el rastro de lo que
      había antes queda en la auditoría del transportador.
    """

    __tablename__ = "transportador_rutas"
    __table_args__ = (
        UniqueConstraint("transportador_id", "ruta_id", name="uq_transportador_ruta"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    transportador_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transportadores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ruta_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rutas.id"), nullable=False)
    # Numeric(12, 2) igual que la tarifa general: el dueño tiene un transportador
    # a $242,76 por litro y los centavos no se pueden perder.
    valor_transporte: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    transportador: Mapped[Transportador] = relationship(back_populates="rutas")
    # Sin anotación Mapped[...] y con el nombre de la clase en texto, como en
    # recepcion/models.py: `Ruta` no se importa acá para no amarrar los dos
    # módulos, y el declarativo la resuelve por el registro de modelos.
    #
    # OJO: este relationship NO filtra `empresa_id` ni `deleted_at`, y no puede:
    # carga la ruta por su llave primaria y esta tabla no tiene `empresa_id` con
    # el que comparar. Las dos cosas que faltan las ponen los helpers de abajo
    # (`es_de_la_empresa`, `ruta_eliminada`), que son los que usan la lectura y la
    # auditoría. Filtrar acá dejaría la fila con `ruta = None` y se perdería el
    # dato de POR QUÉ está rara.
    ruta = relationship("Ruta", lazy="selectin")

    def es_de_la_empresa(self, empresa_id: uuid.UUID | None) -> bool:
        """¿La ruta pegada en esta fila es de la empresa `empresa_id`?

        Es la SEGUNDA BARRERA del aislamiento por empresa, la del lado de la
        lectura. La primera es la escritura, que valida la ruta contra el
        repositorio de la empresa (ver TransportadorService._filas_de_rutas); pero
        hasta acá el aislamiento de la lectura descansaba ENTERO en esa primera, sin
        nada detrás: una fila cruzada plantada por un script, un seed o —el caso de
        verdad— por el backfill de la migración c6b1e4a8d3f7 sobre un dato viejo
        cruzado, se leía como propia, con el nombre y la tarifa de la otra quesera
        en la pantalla de esta.

        Devuelve False SOLO cuando se puede probar que la ruta es de otra empresa.
        Sin ruta cargada, o sin empresa contra la que comparar, devuelve True: no
        hay prueba de nada y esconder una tarifa buena sería peor (la pantalla
        mostraría una lista incompleta de plata).
        """
        if self.ruta is None or empresa_id is None:
            return True
        return self.ruta.empresa_id == empresa_id

    @property
    def ruta_eliminada(self) -> bool:
        """True si la ruta de esta fila está borrada en suave.

        La fila SE QUEDA cuando se bota la ruta, y eso es a propósito: es la
        historia del transportador y esa tarifa todavía cobra —las recepciones
        viejas de esa ruta se recalculan con ella si hay que recuadrar una
        quincena—. Lo que hacía falta era DECIRLO: la pantalla necesita saber que
        esa ruta ya no está en el catálogo para marcarla y no ofrecerla como si se
        pudiera seguir usando.

        Sale en `TransportadorRutaRead` como `ruta_eliminada`.
        """
        return self.ruta is not None and self.ruta.deleted_at is not None

    @property
    def nombre(self) -> str | None:
        """El nombre de la ruta, para que la pantalla no tenga que ir a buscarlo.

        Va en `TransportadorRutaRead` como `nombre`: el diálogo del transportador
        muestra "Nápoles — $242,76" con una sola llamada al API.

        Devuelve None cuando la ruta es de OTRA empresa. La lectura del
        transportador ya no muestra esas filas (las esconde `TransportadorRead`),
        pero esta property la usan además la auditoría y cualquier código futuro, y
        el nombre de una ruta ajena no se puede escribir en la bitácora de esta
        quesera.
        """
        if self.ruta is None:
            return None
        transportador = self.transportador
        if transportador is not None and not self.es_de_la_empresa(transportador.empresa_id):
            return None
        return self.ruta.nombre
