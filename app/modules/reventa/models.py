"""Compra y venta de queso (reventa): negocio paralelo a la producción propia.

Se compra queso a productores (con merma y borona), se les abona por partes,
y se revende a un precio mayor. Esta contabilidad es INDEPENDIENTE del libro
de la quesera: contabilidad/estado de resultados no leen estas tablas.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

# Estado de pago (columna estado del AuditMixin)
ESTADO_PENDIENTE = "pendiente"
ESTADO_PARCIAL = "parcial"
ESTADO_PAGADA = "pagada"
ESTADO_ANULADA = "anulada"

# ------------------------------------------------- qué se comercia y en qué unidad
# El queso y la borona se pesan (kilos). La mozzarella se cuenta: entra como BARRA
# y sale como BARRA, y el peso de la barra no hace falta para ninguna cuenta.
TIPO_QUESO = "queso"
TIPO_BORONA = "borona"
TIPO_MOZZARELLA = "mozzarella"

UNIDAD_KILO = "kg"
UNIDAD_BARRA = "barra"


def unidad_de(tipo: str | None) -> str:
    """La unidad en la que se mide un tipo de producto.

    UNA SOLA FUENTE DE VERDAD, y por eso NO hay columna `unidad` en ninguna tabla:
    la unidad se DEDUCE del tipo. Guardarla aparte sería un segundo nombre para un
    hecho que ya está en la fila, y el día que las dos se contradigan (una fila con
    tipo 'mozzarella' y unidad 'kg') no habría manera de saber cuál creer. Es la
    misma razón por la que `Temporada.abierta` se deduce de `fecha_fin`.

    Lo que NO se deduce es la CANTIDAD: los kilos viven en las columnas de kilos y
    las barras en las columnas de barras, siempre. Ver los CHECK de cada tabla.
    """
    return UNIDAD_BARRA if tipo == TIPO_MOZZARELLA else UNIDAD_KILO


class CompraQueso(TenantMixin, AuditMixin, Base):
    __tablename__ = "compras_queso"
    __table_args__ = (
        # POR QUÉ ESTE CHECK ES LA PIEZA CENTRAL DE LA MOZZARELLA.
        #
        # La regla que no se puede romper es que los kilos y las barras NUNCA se
        # sumen en una misma cifra. La forma de garantizarlo no es acordarse de
        # filtrar por tipo en cada consulta —cualquier `sum()` que se olvide del
        # filtro rompe la regla en silencio— sino que las barras vivan en OTRA
        # columna y que esa columna esté en cero en las filas de kilos y al
        # contrario. Así `sum(kilos_netos)` no puede recoger barras ni por
        # descuido: no hay barras que recoger en esa columna.
        #
        # Con este CHECK la garantía la da la base de datos y no la disciplina de
        # quien escriba la próxima consulta. Es el mismo criterio del CHECK de
        # adjuntos_reventa: lo que tiene que ser verdad siempre, se exige en la
        # tabla.
        CheckConstraint(
            "(tipo <> 'mozzarella' AND barras = 0 AND precio_barra = 0) "
            "OR (tipo = 'mozzarella' AND kilos_brutos = 0 AND kilos_netos = 0 "
            "AND merma_kilos = 0 AND borona_kilos = 0 AND precio_kilo = 0)",
            name="ck_compras_queso_cantidad_en_su_unidad",
        ),
    )

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    productor: Mapped[str] = mapped_column(String(150), nullable=False)
    # Qué se le compró: queso (se pesa) o mozzarella (se cuenta por barras).
    # server_default 'queso' a propósito: TODAS las filas que ya existen son de
    # kilos, y así quedan marcadas como tal y no en un estado ambiguo.
    tipo: Mapped[str] = mapped_column(
        String(20), default=TIPO_QUESO, server_default=TIPO_QUESO, index=True
    )
    kilos_brutos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Merma en la compra quedó obsoleta: al comprar se paga por todo lo que se
    # recibe. La merma real se ve al vender (se pesa menos). Se conserva la
    # columna (siempre 0) por compatibilidad con datos históricos.
    merma_kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # Borona que llega con el lote: no se paga, pero entra al inventario
    # de borona para venderse como subproducto
    borona_kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # Kilos por los que se paga (= kilos_brutos ahora que no hay merma)
    kilos_netos: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    precio_kilo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # ------------------------------------------------------------- mozzarella
    # Barras compradas y lo que costó CADA BARRA. Escala 0 (sin decimales) a
    # propósito: una barra es una barra, no hay media barra, y una columna que
    # admitiera decimales dejaría entrar "8,5 barras" —que no existe— o
    # redondearía en silencio como ya pasó con los kilos de tres decimales.
    # En las compras de kilos van en CERO, y el CHECK de arriba lo exige.
    barras: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), default=Decimal("0"), server_default="0"
    )
    precio_barra: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    abonado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    abonos: Mapped[list["AbonoCompraQueso"]] = relationship(
        back_populates="compra", lazy="selectin", cascade="all, delete-orphan",
        order_by="AbonoCompraQueso.fecha",
    )
    # Soportes de pago (fotos de las transferencias). viewonly y con el filtro de
    # borrados DENTRO del join: los adjuntos se borran en suave, y sin el filtro
    # aquí la lista seguiría contando los que ya se borraron. Es de solo lectura
    # para que el ORM no intente cascadas: borrar un adjunto tiene que pasar por
    # el servicio, que además borra el objeto en R2.
    adjuntos: Mapped[list["AdjuntoReventa"]] = relationship(
        primaryjoin=(
            "and_(CompraQueso.id == AdjuntoReventa.compra_id, "
            "AdjuntoReventa.deleted_at.is_(None))"
        ),
        viewonly=True,
        lazy="selectin",
        order_by="AdjuntoReventa.created_at",
    )

    @property
    def saldo(self) -> Decimal:
        return self.valor_total - self.abonado

    @property
    def unidad(self) -> str:
        """En qué se mide esta compra: 'kg' o 'barra'. Se deduce del tipo (ver
        `unidad_de`). Viaja en la respuesta para que la pantalla ponga el rótulo
        correcto sin tener que conocer la regla."""
        return unidad_de(self.tipo)

    @property
    def adjuntos_count(self) -> int:
        """Cuántos soportes tiene. Va en la lista para que se vea de un vistazo
        cuáles compras tienen respaldo del pago y cuáles no."""
        return len(self.adjuntos)


class AbonoCompraQueso(AuditMixin, Base):
    __tablename__ = "abonos_compra_queso"

    compra_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compras_queso.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    compra: Mapped[CompraQueso] = relationship(back_populates="abonos")


# Nombres viejos del tipo de venta. Se conservan porque están usados en todo el
# módulo y en las pruebas; son los mismos valores de TIPO_QUESO/TIPO_BORONA.
TIPO_VENTA_QUESO = TIPO_QUESO
TIPO_VENTA_BORONA = TIPO_BORONA
TIPO_VENTA_MOZZARELLA = TIPO_MOZZARELLA


class VentaQueso(TenantMixin, AuditMixin, Base):
    __tablename__ = "ventas_queso"
    __table_args__ = (
        # El mismo CHECK que en las compras y por la misma razón: las barras van
        # en sus columnas y los kilos en las suyas, para que ningún `sum()` de
        # kilos pueda recoger barras. Ver el comentario largo en CompraQueso.
        #
        # Se escribe con `tipo <> 'mozzarella'` y no enumerando queso y borona a
        # propósito: si una fila vieja trajera el tipo en blanco (caso que el
        # resumen ya contempla), sigue siendo una venta en kilos y no una fila
        # que la base rechace al migrar.
        CheckConstraint(
            "(tipo <> 'mozzarella' AND barras = 0 AND precio_barra = 0 "
            "AND gasto_por_barra = 0) "
            "OR (tipo = 'mozzarella' AND kilos = 0 AND precio_kilo = 0 "
            "AND gasto_por_kilo = 0)",
            name="ck_ventas_queso_cantidad_en_su_unidad",
        ),
    )

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cliente: Mapped[str] = mapped_column(String(150), nullable=False)
    # Qué se vende: queso entero (kg), borona (kg, subproducto más barato) o
    # mozzarella (barras). La unidad se deduce del tipo, ver `unidad_de`.
    tipo: Mapped[str] = mapped_column(
        String(20), default=TIPO_VENTA_QUESO, server_default=TIPO_VENTA_QUESO, index=True
    )
    kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_kilo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # ------------------------------------------------------------- mozzarella
    # Barras vendidas y precio de CADA BARRA. Escala 0 por lo mismo que en la
    # compra: no existe media barra. En las ventas de kilos van en cero.
    barras: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), default=Decimal("0"), server_default="0"
    )
    precio_barra: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Gastos que conlleva vender el lote (ej. transporte por kilo). NO cambian lo
    # que paga el cliente (valor_total); solo reducen la ganancia de la reventa.
    gasto_concepto: Mapped[str | None] = mapped_column(String(150))
    gasto_por_kilo: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    # El mismo gasto pero POR BARRA, para las ventas de mozzarella. Va en columna
    # aparte y no reutilizando `gasto_por_kilo` porque un valor "por barra"
    # guardado en una columna que se llama por_kilo es exactamente la confusión
    # que este trabajo tiene que evitar: el día que alguien sume esa columna
    # creyendo que son pesos por kilo, la cifra que saque no significa nada.
    gasto_por_barra: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    # Total del gasto, en PESOS: gasto_por_kilo * kilos en las ventas de kilos y
    # gasto_por_barra * barras en las de mozzarella (lo calcula el servicio).
    # Es una sola columna porque los pesos SÍ se suman entre unidades.
    gasto_monto: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )
    abonado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    abonos: Mapped[list["AbonoVentaQueso"]] = relationship(
        back_populates="venta", lazy="selectin", cascade="all, delete-orphan",
        order_by="AbonoVentaQueso.fecha",
    )
    # Soportes de pago de la venta. Mismo trato que en la compra: ver allá.
    adjuntos: Mapped[list["AdjuntoReventa"]] = relationship(
        primaryjoin=(
            "and_(VentaQueso.id == AdjuntoReventa.venta_id, "
            "AdjuntoReventa.deleted_at.is_(None))"
        ),
        viewonly=True,
        lazy="selectin",
        order_by="AdjuntoReventa.created_at",
    )

    @property
    def saldo(self) -> Decimal:
        return self.valor_total - self.abonado

    @property
    def unidad(self) -> str:
        """'kg' para queso y borona, 'barra' para mozzarella (ver `unidad_de`)."""
        return unidad_de(self.tipo)

    @property
    def adjuntos_count(self) -> int:
        return len(self.adjuntos)


# Destino de un ajuste del queso disponible de reventa
DESTINO_BORONA = "borona"  # pasa a borona (subproducto vendible)
DESTINO_MERMA = "merma"  # pérdida (no se vende ni suma a ningún inventario)


class ConversionBorona(TenantMixin, AuditMixin, Base):
    """Ajuste que reduce el queso disponible de reventa. Según `destino`:
    - borona: el queso se pasa a borona (devuelto o ya no vendible como entero)
      y suma al inventario de borona para venderse como subproducto.
    - merma: pérdida de peso (se pesó menos al vender); no suma a ningún lado.

    SOLO APLICA AL QUESO EN KILOS, y por eso no tiene ni tipo ni barras: pasar a
    borona es desmenuzar queso, y la merma es peso que se perdió. Una barra de
    mozzarella no se desmenuza ni pierde peso en el camino —entra como barra y
    sale como barra—, así que la mozzarella no participa en estos ajustes. Si
    algún día una barra se daña, eso es otro concepto (una baja de unidades) y
    tendría que ser su propio movimiento, nunca kilos metidos en esta tabla.
    """

    __tablename__ = "conversiones_borona"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    destino: Mapped[str] = mapped_column(
        String(20), default=DESTINO_BORONA, server_default=DESTINO_BORONA, index=True
    )
    # Precio por kilo de la borona (solo aplica cuando destino = borona; la merma
    # es pérdida sin valor). Sirve para valorar la borona generada.
    precio_kilo: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    observaciones: Mapped[str | None] = mapped_column(String(300))


class AbonoVentaQueso(AuditMixin, Base):
    __tablename__ = "abonos_venta_queso"

    venta_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ventas_queso.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    venta: Mapped[VentaQueso] = relationship(back_populates="abonos")


# Saldos que vienen del sistema anterior ("el libro viejo")
TIPO_SALDO_COBRAR = "cobrar"  # un cliente le quedó debiendo una venta vieja
TIPO_SALDO_PAGAR = "pagar"  # él le quedó debiendo una compra vieja a un productor


class SaldoAnterior(TenantMixin, AuditMixin, Base):
    """Cuenta a medio pagar heredada del sistema que usaba antes el cliente.

    Es un concepto APARTE de las compras y las ventas a propósito. Suma en
    "por cobrar a clientes" o en "por pagar a productores", acepta abonos y
    sale en el estado de cuenta, pero NO toca el inventario, NI los kilos, NI
    la ganancia: aquí nunca se compró ni se vendió ese queso, así que cargarlo
    como compra o venta obligaría a inventar kilos y rompería la
    reconciliación del desglose de la ganancia con la cifra del período.

    El estado (columna del AuditMixin) usa las mismas constantes de pago que
    las compras y las ventas: pendiente / parcial / pagada / anulada.
    """

    __tablename__ = "saldos_anteriores"

    tipo: Mapped[str] = mapped_column(
        String(20), default=TIPO_SALDO_COBRAR, server_default=TIPO_SALDO_COBRAR, index=True
    )
    # Nombre del cliente (si el tipo es 'cobrar') o del productor (si es 'pagar')
    tercero: Mapped[str] = mapped_column(String(150), nullable=False)
    # Fecha ORIGINAL del documento en el libro viejo, no la de la carga: es la
    # que el tercero reconoce cuando se le manda el estado de cuenta.
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # De qué era la deuda: "Venta 120 kg del 3 de mayo", "Factura 045"
    concepto: Mapped[str] = mapped_column(String(200), nullable=False)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    abonado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    abonos: Mapped[list["AbonoSaldoAnterior"]] = relationship(
        back_populates="saldo", lazy="selectin", cascade="all, delete-orphan",
        order_by="AbonoSaldoAnterior.fecha",
    )

    @property
    def saldo(self) -> Decimal:
        return self.valor_total - self.abonado


class AbonoSaldoAnterior(AuditMixin, Base):
    __tablename__ = "abonos_saldo_anterior"

    saldo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("saldos_anteriores.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    saldo: Mapped[SaldoAnterior] = relationship(back_populates="abonos")


class Temporada(TenantMixin, AuditMixin, Base):
    """Un ciclo de compra y reventa con nombre y fechas: "Semana Santa 2026".

    Es SOLO un rango de fechas con nombre. No tiene columnas de plata a
    propósito, y esa es la decisión de diseño importante: la ganancia de la
    temporada NO se guarda, se calcula cada vez con el mismo motor del resumen
    (`ReventaResumenService.resumen`) sobre `fecha_inicio..fecha_fin`.

    Se hizo así por dos razones:

    1. Sirve HACIA ATRÁS. Como las cifras salen de los movimientos que ya están
       cargados, se puede registrar hoy una temporada de marzo y su ganancia
       aparece de inmediato. Con una cifra congelada habría que esperar a cerrar
       la próxima para empezar a tener historia.
    2. NO SE DESACTUALIZA. Si mañana se le corrige el precio a una compra de esa
       temporada, la ganancia se mueve con ella. Una cifra congelada quedaría
       distinta de la que muestra el Resumen filtrado a las mismas fechas, y el
       usuario cuadra estos números a mano: dos cifras que no coinciden para el
       mismo rango es exactamente lo que le haría perder la confianza en todo el
       tablero.

    La contra es que "cerrar" una temporada no congela nada, pero eso es honesto:
    lo que se cierra es el ciclo del queso, no el libro.

    Las temporadas de una empresa NO SE PUEDEN SOLAPAR (lo valida el servicio).
    Si se solaparan, el mismo kilo y el mismo peso caerían en dos temporadas y la
    suma de las ganancias por temporada no daría la ganancia total.

    `fecha_fin` en NULL significa TEMPORADA ABIERTA (la que está corriendo), y
    solo puede haber una así. El estado del AuditMixin no se usa para esto: la
    única fuente de verdad de si está abierta es `fecha_fin`, porque dos fuentes
    para el mismo hecho terminan contradiciéndose.
    """

    __tablename__ = "temporadas"

    nombre: Mapped[str] = mapped_column(String(80), nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fecha_fin: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    notas: Mapped[str | None] = mapped_column(String(500))

    @property
    def abierta(self) -> bool:
        return self.fecha_fin is None


class AdjuntoReventa(TenantMixin, AuditMixin, Base):
    """Un soporte de pago (foto de la transferencia) de una compra O de una venta.

    POR QUÉ NO SE GUARDA NINGUNA URL. Solo se guarda `object_key`, la llave del
    objeto dentro del bucket privado de Cloudflare R2. La URL para verlo se firma
    en el momento en que alguien la pide y caduca sola (ver app/core/storage.py).
    Una URL guardada en una columna es un permiso permanente: quien la viera —en
    un backup, en un log, en un export— vería el soporte de pago para siempre,
    con el nombre, la cuenta y el monto de una transferencia real.

    POR QUÉ LA LLAVE LLEVA EL empresa_id ADENTRO. El formato es
    `{empresa_id}/reventa/compras/{compra_id}/{uuid}.jpg`. Además del filtro por
    empresa en cada consulta, la llave misma queda amarrada a la empresa dueña:
    si algún día una consulta se escapara sin filtro, la llave que se firmaría
    seguiría siendo la de un archivo de OTRA empresa y se notaría de inmediato
    en la auditoría; y como la llave lleva un uuid aleatorio, tampoco se puede
    adivinar la de nadie.

    UN ADJUNTO CUELGA DE UNA COMPRA O DE UNA VENTA, NUNCA DE LAS DOS NI DE
    NINGUNA. Lo garantiza un CHECK en la tabla y no solo el servicio: una fila
    con las dos en NULL sería un archivo huérfano pagando almacenamiento sin que
    nadie pueda verlo ni borrarlo desde la interfaz.
    """

    __tablename__ = "adjuntos_reventa"
    __table_args__ = (
        CheckConstraint(
            "(compra_id IS NOT NULL AND venta_id IS NULL) "
            "OR (compra_id IS NULL AND venta_id IS NOT NULL)",
            name="ck_adjuntos_reventa_un_solo_dueno",
        ),
        # Con nombre explícito y no con `unique=True` en la columna: así el
        # nombre es el MISMO que el de la migración y un `alembic revision
        # --autogenerate` futuro no propone borrarla y volverla a crear.
        UniqueConstraint("object_key", name="uq_adjuntos_reventa_object_key"),
    )

    compra_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compras_queso.id", ondelete="CASCADE"), index=True, default=None
    )
    venta_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ventas_queso.id", ondelete="CASCADE"), index=True, default=None
    )
    # Llave del objeto en R2. Única (ver __table_args__): dos filas apuntando al
    # mismo archivo harían que borrar una dejara a la otra señalando un objeto
    # que ya no existe.
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # Nombre con el que llegó el archivo, para mostrarlo y para nombrar la
    # descarga. NO se usa para armar la llave: el nombre lo escribe quien sube.
    nombre_archivo: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    tamano_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Quién lo subió: el id va en `created_by` (AuditMixin), que es la única
    # fuente de ese dato. Aquí se guarda solo el NOMBRE tal como estaba al subir,
    # que es un hecho distinto: si mañana el usuario se borra o se le cambia el
    # nombre, el soporte tiene que seguir diciendo quién lo aportó.
    subido_por_nombre: Mapped[str | None] = mapped_column(String(150), default=None)

    @property
    def es_imagen(self) -> bool:
        return (self.content_type or "").startswith("image/")
