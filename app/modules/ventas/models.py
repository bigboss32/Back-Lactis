import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

TIPO_FACTURA = "factura"
TIPO_REMISION = "remision"

# Estado de cartera de la venta (columna estado del AuditMixin)
ESTADO_PENDIENTE = "pendiente"
ESTADO_PARCIAL = "parcial"
ESTADO_PAGADA = "pagada"
ESTADO_ANULADA = "anulada"

METODO_EFECTIVO = "efectivo"
METODO_TRANSFERENCIA = "transferencia"
METODO_OTRO = "otro"


class Venta(TenantMixin, AuditMixin, Base):
    __tablename__ = "ventas"
    __table_args__ = (UniqueConstraint("empresa_id", "numero", name="uq_venta_numero"),)

    numero: Mapped[int] = mapped_column(Integer, nullable=False)  # consecutivo por empresa
    tipo: Mapped[str] = mapped_column(String(20), default=TIPO_FACTURA)
    cliente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clientes.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    descuento: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    pagado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    # Lo que cuesta LLEVAR el despacho: el flete a Bogotá o a donde sea. Es el
    # mismo concepto que el `gasto_*` de las ventas de reventa, y se llaman igual a
    # propósito para que los dos módulos se lean y se mantengan igual.
    #
    # OJO: NO cambia el `total` que paga el cliente. Es un costo de la quesera que
    # reduce la utilidad, y es lo que hace que el kilo PUESTO EN DESTINO valga más
    # que el kilo en la planta. Sin esto, la utilidad por lote sale mejor de lo real.
    #
    # DESDE QUE EL FLETE VA POR TRAMOS, estas tres columnas son el RESUMEN de los
    # tramos, no el dato original. Se siguen manteniendo al día porque son las que
    # lee todo lo demás del sistema (la utilidad por lote de producción, el estado
    # de resultados, la pantalla de lotes): ver `VentaTramoFlete`. Quien las lea
    # sigue viendo el flete completo del despacho, con uno o con cinco tramos.
    #
    # - gasto_concepto: la ruta en cristiano ("Quesera → San Vicente → Bogotá").
    # - gasto_por_kilo: la SUMA de lo que cobra cada tramo por kilo (400 + 600).
    # - gasto_monto:    la SUMA de los totales de cada tramo. Se suma la cifra ya
    #                   redondeada de cada tramo, nunca se recalcula desde el
    #                   por-kilo total: así el desglose SUMA EXACTO la cifra grande
    #                   y no queda un peso de diferencia que el dueño encuentra al
    #                   cuadrar a mano.
    gasto_concepto: Mapped[str | None] = mapped_column(String(150))
    gasto_por_kilo: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    # Total del gasto = suma de los `valor_total` de los tramos (lo calcula el servicio)
    gasto_monto: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )

    cliente = relationship("Cliente", lazy="joined")
    detalles: Mapped[list["VentaDetalle"]] = relationship(
        back_populates="venta", lazy="selectin", cascade="all, delete-orphan"
    )
    # Los tramos del flete. selectin y NO joined, por lo mismo que los abonos de
    # reventa: con un LEFT JOIN de por medio Postgres rechaza el SELECT ... FOR
    # UPDATE con un 0A000, y el pago de una venta bloquea justamente esta fila.
    tramos_flete: Mapped[list["VentaTramoFlete"]] = relationship(
        back_populates="venta",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="VentaTramoFlete.orden",
    )
    pagos: Mapped[list["Pago"]] = relationship(back_populates="venta", lazy="selectin")

    @property
    def saldo(self) -> Decimal:
        return self.total - self.pagado


class VentaDetalle(AuditMixin, Base):
    __tablename__ = "venta_detalles"

    venta_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ventas.id", ondelete="CASCADE"), index=True
    )
    producto_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("productos.id"))
    descripcion: Mapped[str | None] = mapped_column(String(200))
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))

    venta: Mapped[Venta] = relationship(back_populates="detalles")
    producto = relationship("Producto", lazy="joined")


class VentaTramoFlete(AuditMixin, Base):
    """Un TRAMO del flete de un despacho: de dónde sale, a dónde llega, cuánto
    cobra por kilo y quién lo maneja.

    Lo pidió el dueño con estas palabras: "el flete puede ser varios; ejemplo
    puede ser de la quesera a San Vicente 400 y de San Vicente a Bogotá 600, y el
    nombre del conductor, porque necesito saber cuánto se le tiene que pagar".

    POR QUÉ ORIGEN **Y** DESTINO, Y NO SOLO "A DÓNDE VA". Con un solo campo, el
    segundo tramo del ejemplo diría "Bogotá" y se perdería el dato de que ese
    trayecto ARRANCA en San Vicente y no en la quesera. Y ese dato no es adorno:
    es lo que permite ver que la cadena está completa (que el destino de un tramo
    es el origen del siguiente) y lo que hace que el precio del tramo se entienda
    — $600/kg de la quesera a Bogotá y $600/kg de San Vicente a Bogotá son dos
    negocios distintos. El origen es opcional porque el primer tramo casi siempre
    sale de la planta y obligar a escribirlo sería trabajo de más.

    EL CONDUCTOR ES TEXTO LIBRE, igual que los productores y los clientes de
    reventa: el dueño no tiene que registrar a nadie antes de despachar. Para que
    "JOSE LAVADO" y "Jose lavado" no se cuenten como dos personas, el servicio
    canoniza el nombre con el mismo `_canonizar_nombre` de reventa y guarda
    aparte `conductor_clave`, que es con lo que se agrupa.

    POR QUÉ SE GUARDA `conductor_clave` Y NO SE AGRUPA CON lower() EN SQL. El
    lower() de SQLite no baja los acentos y el de Postgres sí, así que agrupar en
    SQL daría un resultado distinto en pruebas y en producción. La clave se
    calcula en Python (una sola función, `clave_de_conductor`) y se guarda, así
    que el GROUP BY es idéntico en las dos bases.

    NO LLEVA empresa_id, igual que VentaDetalle, los abonos de reventa y los
    pagos de liquidación: la empresa la pone la venta padre, que es por donde se
    entra siempre y por donde hay que pasar de todos modos para saber la fecha y
    si está anulada. Una segunda copia del tenant en el hijo es una fuente más
    que se puede desincronizar y que hay que acordarse de filtrar.
    """

    __tablename__ = "venta_tramos_flete"
    __table_args__ = (
        # Para "cuánto le debo a cada conductor": agrupa por conductor sin tener
        # que recorrer todos los tramos de la empresa.
        Index("ix_venta_tramo_conductor", "conductor_clave"),
    )

    venta_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ventas.id", ondelete="CASCADE"), index=True
    )
    # El orden en que el dueño los escribió: es el orden del recorrido, y sin él
    # la ruta se mostraría al azar ("San Vicente → Bogotá" antes que "Quesera →
    # San Vicente") y no se podría leer como una cadena.
    orden: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    origen: Mapped[str | None] = mapped_column(String(120))
    # Nullable a propósito: los fletes que ya existían antes de los tramos se
    # migraron desde `gasto_concepto`, que a veces viene vacío. Inventarles un
    # destino sería inventar un dato. Para los tramos NUEVOS el esquema de la API
    # sí lo exige.
    destino: Mapped[str | None] = mapped_column(String(120))
    conductor: Mapped[str | None] = mapped_column(String(150))
    conductor_clave: Mapped[str | None] = mapped_column(String(150))
    valor_por_kilo: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    # valor_por_kilo * kilos del despacho, YA redondeado a centavos. Se guarda en
    # vez de recalcularse porque es el sumando exacto de `Venta.gasto_monto`: si
    # cada lector lo recalculara, dos lecturas podrían redondear distinto y el
    # desglose dejaría de sumar la cifra grande.
    valor_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )

    venta: Mapped[Venta] = relationship(back_populates="tramos_flete")

    @property
    def ruta(self) -> str:
        """"Quesera → San Vicente", o solo el destino si no se anotó el origen."""
        destino = self.destino or "sin destino"
        return f"{self.origen} → {destino}" if self.origen else destino


class PagoConductor(TenantMixin, AuditMixin, Base):
    """Un pago hecho a un conductor de despachos.

    POR QUÉ NO CUELGA DE UN TRAMO NI DE UNA VENTA. A un conductor se le paga por
    lo que lleva acumulado, no despacho por despacho: hace cuatro viajes en la
    semana y el sábado se le entrega una plata sola. Amarrar el pago a un tramo
    obligaría al dueño a repartir a mano esa plata entre los cuatro viajes, que
    es justo la cuenta que él quería que hiciera el sistema. Por eso el pago va
    contra el CONDUCTOR (por su clave canonizada) y la deuda se calcula siempre
    como: suma de sus tramos − suma de sus pagos.

    POR QUÉ NO HAY UNA COLUMNA `pagado` NI UNA TABLA DE CONDUCTORES. En reventa
    el saldo vive en el documento (`abonado`) porque hay un documento; aquí no lo
    hay, y una tabla de conductores con un acumulado tendría que mantenerse al
    día cada vez que se edita una venta, se le cambian los kilos o se anula. Esa
    columna se desincroniza el día que alguien olvide actualizarla, y entonces el
    "se le debe" miente. Calculándolo de los tramos no hay nada que desincronizar
    y el desglose SIEMPRE suma exacto, que es como el dueño lo verifica.

    Este conductor NO es un `Transportador`: esos recogen la LECHE. Estos llevan
    el QUESO YA HECHO y son otra gente. Tampoco tiene que ver con el negocio de
    transporte ("la turbo"), que es aparte.
    """

    __tablename__ = "pagos_conductor"
    __table_args__ = (
        Index("ix_pago_conductor_empresa_clave", "empresa_id", "conductor_clave"),
    )

    # Cómo se escribió el nombre al pagar, para mostrarlo tal cual.
    conductor: Mapped[str] = mapped_column(String(150), nullable=False)
    # Con qué conductor se agrupa. Misma clave que en el tramo (ver allá).
    conductor_clave: Mapped[str] = mapped_column(String(150), nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))


class Pago(TenantMixin, AuditMixin, Base):
    __tablename__ = "pagos"

    venta_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ventas.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    metodo: Mapped[str] = mapped_column(String(30), default=METODO_EFECTIVO)
    referencia: Mapped[str | None] = mapped_column(String(100))
    observaciones: Mapped[str | None] = mapped_column(String(300))

    venta: Mapped[Venta] = relationship(back_populates="pagos")
