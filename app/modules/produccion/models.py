import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class TipoQueso(TenantMixin, AuditMixin, Base):
    __tablename__ = "tipos_queso"

    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(300))
    precio_referencia: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))


class Produccion(TenantMixin, AuditMixin, Base):
    __tablename__ = "producciones"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tipo_queso_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tipos_queso.id"), nullable=False)
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))

    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))  # unidades/bloques
    peso_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    litros_usados: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # rendimiento = kg producidos por litro de leche; merma en kg
    rendimiento: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"))
    merma: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    tipo_queso = relationship("TipoQueso", lazy="joined")


# Con qué texto se marcan los ajustes de inventario que crea un cierre de ciclo.
# Es la misma idea que "Producción #" y "venta #": la cadena de lotes reconoce por
# la referencia qué movimientos ya vienen contados por otro lado y no los vuelve a
# procesar como ajustes sueltos. Si se contaran las dos veces, la merma del ciclo
# se le restaría dos veces a la bodega y a la utilidad.
REFERENCIA_MERMA_CICLO = "Merma ciclo"


class CicloDespacho(TenantMixin, AuditMixin, Base):
    """Un CICLO DE DESPACHO: las tandas que salen juntas para Bogotá.

    EL PROBLEMA QUE RESUELVE. El queso se pesa dos veces: cuando se hace y cuando
    se vende. Entre las dos se seca y pierde peso —una tanda de 130 kg rinde 125
    al venderse—. Como en Bogotá se vende por kilos y sin saber de qué tanda
    salieron, esos 5 kg no desaparecen de la cuenta: se quedan en la cola FIFO
    como QUESO EN BODEGA QUE NO EXISTE, con su costo, y van corriendo de un lote
    al siguiente hasta acumularse en el último. El inventario queda inflado y la
    utilidad se ve mejor de lo que es.

    LO QUE HACE QUE LA RESTA SEA HONESTA es que el despacho va POR CICLOS: se
    acumulan las tandas de unos siete días, se despachan, y el ciclo se reinicia.
    Al terminar uno, de esas tandas no debería quedar nada, así que ahí —y solo
    ahí— la cuenta

        producido en el ciclo − lo que de verdad salió = MERMA del ciclo

    se puede hacer sin adivinar. Fuera del cierre esa resta no significa nada,
    porque en cualquier día suelto lo que no ha salido todavía sí existe.

    POR QUÉ ESTA TABLA SÍ GUARDA CIFRAS, AL REVÉS QUE `Temporada`. Una temporada
    de reventa es solo un rango con nombre y sus cifras se recalculan siempre,
    porque cerrarla no escribe nada. Cerrar un CICLO sí escribe: crea ajustes de
    inventario que bajan el queso fantasma de la bodega. Las cifras que se
    guardan aquí son la FOTO DE LO QUE EL DUEÑO ACEPTÓ ese día —los kilos que se
    dieron por perdidos y lo que valían—, no un cálculo que se repite. Es plata
    que se da por perdida: tiene que quedar constancia de qué se aceptó y cuándo.

    Ojo con `costo_merma`: la merma se registra en KILOS, y su costo lo saca la
    cadena de lotes con el costo por kilo de cada tanda. Si mañana se le corrige
    el precio a una leche de esos días, la utilidad por lote se mueve sola y esta
    columna se queda con el valor del día del cierre. Es a propósito: esto es el
    acta, no el saldo.

    `cerrado_at` en NULL significa CICLO ABIERTO: la fila existe con su rango
    pero todavía no se le aplicó ninguna merma. Es el estado en que queda un
    ciclo que se reabrió. La única fuente de verdad de si está cerrado es esta
    columna, no el `estado` del AuditMixin: dos fuentes para el mismo hecho
    terminan contradiciéndose.

    LOS CICLOS NO SE PUEDEN SOLAPAR (lo valida el servicio). Si se cruzaran, las
    mismas tandas y los mismos despachos caerían en dos ciclos y la merma se
    cobraría dos veces sobre el mismo queso.
    """

    __tablename__ = "ciclos_despacho"

    nombre: Mapped[str] = mapped_column(String(80), nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # El ciclo es un rango que YA PASÓ: las dos fechas son obligatorias. Lo que
    # está corriendo no es una fila, es "desde el último cierre hasta hoy", y el
    # servicio lo calcula al vuelo para poder proponerlo.
    fecha_fin: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cerrado_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
    notas: Mapped[str | None] = mapped_column(String(500))

    # --- La cuenta que se mostró y que el dueño aceptó, congelada al cerrar
    kilos_producidos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    kilos_vendidos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Lo que el dueño YA había bajado a mano dentro del ciclo (ajustes de
    # inventario negativos). Se guarda porque es el renglón que evita cobrar la
    # merma dos veces, y sin él la cuenta de la pantalla no cuadraría al releerla.
    kilos_ajuste_manual: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    kilos_merma: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    costo_merma: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=Decimal("0"))
    # Si se cerró con avisos (merma negativa o desproporcionada), queda escrito
    # cuáles eran: alguien los leyó y aceptó igual, y eso hay que poder auditarlo.
    advertencias: Mapped[str | None] = mapped_column(String(1000))

    lotes = relationship(
        "CicloDespachoLote",
        back_populates="ciclo",
        lazy="selectin",
        order_by="CicloDespachoLote.fecha_produccion",
    )

    @property
    def cerrado(self) -> bool:
        return self.cerrado_at is not None


class CicloDespachoLote(TenantMixin, AuditMixin, Base):
    """La parte de la merma del ciclo que le tocó a UNA tanda.

    La suma de `kilos_merma` de estas filas es EXACTAMENTE `kilos_merma` del
    ciclo, sin sobrar ni faltar un gramo: el reparto es a prorrata de los kilos
    producidos y el último lote se lleva el residuo del redondeo, igual que en
    `reventa/lotes.py`. El dueño suma esta columna a mano.

    `movimiento_id` apunta al ajuste de inventario que se creó para bajar esos
    kilos de la bodega. Se guarda el vínculo, y no solo el texto de la
    referencia, para que reabrir el ciclo pueda deshacer exactamente los mismos
    movimientos y ninguno más.
    """

    __tablename__ = "ciclos_despacho_lotes"

    ciclo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ciclos_despacho.id"), nullable=False, index=True
    )
    produccion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("producciones.id"), nullable=False, index=True
    )
    tipo_queso_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tipos_queso.id"), nullable=False
    )
    movimiento_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("movimientos_inventario.id"), default=None
    )
    # Se copia la fecha de la tanda para poder ordenar y mostrar el desglose sin
    # tener que volver a la tabla de producciones.
    fecha_produccion: Mapped[date] = mapped_column(Date, nullable=False)
    kilos_producidos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    kilos_merma: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    costo_merma: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=Decimal("0"))

    ciclo = relationship("CicloDespacho", back_populates="lotes")
