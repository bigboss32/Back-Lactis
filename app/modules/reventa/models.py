"""Compra y venta de queso (reventa): negocio paralelo a la producción propia.

Se compra queso a productores (con merma y borona), se les abona por partes,
y se revende a un precio mayor. Esta contabilidad es INDEPENDIENTE del libro
de la quesera: contabilidad/estado de resultados no leen estas tablas.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

# Estado de pago (columna estado del AuditMixin)
ESTADO_PENDIENTE = "pendiente"
ESTADO_PARCIAL = "parcial"
ESTADO_PAGADA = "pagada"
ESTADO_ANULADA = "anulada"


class CompraQueso(TenantMixin, AuditMixin, Base):
    __tablename__ = "compras_queso"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    productor: Mapped[str] = mapped_column(String(150), nullable=False)
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
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    abonado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    abonos: Mapped[list["AbonoCompraQueso"]] = relationship(
        back_populates="compra", lazy="selectin", cascade="all, delete-orphan",
        order_by="AbonoCompraQueso.fecha",
    )

    @property
    def saldo(self) -> Decimal:
        return self.valor_total - self.abonado


class AbonoCompraQueso(AuditMixin, Base):
    __tablename__ = "abonos_compra_queso"

    compra_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compras_queso.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    compra: Mapped[CompraQueso] = relationship(back_populates="abonos")


TIPO_VENTA_QUESO = "queso"
TIPO_VENTA_BORONA = "borona"


class VentaQueso(TenantMixin, AuditMixin, Base):
    __tablename__ = "ventas_queso"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cliente: Mapped[str] = mapped_column(String(150), nullable=False)
    # Qué se vende: queso entero o borona (subproducto a menor precio)
    tipo: Mapped[str] = mapped_column(
        String(20), default=TIPO_VENTA_QUESO, server_default=TIPO_VENTA_QUESO, index=True
    )
    kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_kilo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Gastos que conlleva vender el lote (ej. transporte por kilo). NO cambian lo
    # que paga el cliente (valor_total); solo reducen la ganancia de la reventa.
    gasto_concepto: Mapped[str | None] = mapped_column(String(150))
    gasto_por_kilo: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    # Total del gasto = gasto_por_kilo * kilos (lo calcula el servicio)
    gasto_monto: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )
    abonado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    abonos: Mapped[list["AbonoVentaQueso"]] = relationship(
        back_populates="venta", lazy="selectin", cascade="all, delete-orphan",
        order_by="AbonoVentaQueso.fecha",
    )

    @property
    def saldo(self) -> Decimal:
        return self.valor_total - self.abonado


# Destino de un ajuste del queso disponible de reventa
DESTINO_BORONA = "borona"  # pasa a borona (subproducto vendible)
DESTINO_MERMA = "merma"  # pérdida (no se vende ni suma a ningún inventario)


class ConversionBorona(TenantMixin, AuditMixin, Base):
    """Ajuste que reduce el queso disponible de reventa. Según `destino`:
    - borona: el queso se pasa a borona (devuelto o ya no vendible como entero)
      y suma al inventario de borona para venderse como subproducto.
    - merma: pérdida de peso (se pesó menos al vender); no suma a ningún lado.
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
