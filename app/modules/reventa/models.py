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


class ConversionBorona(TenantMixin, AuditMixin, Base):
    """Queso del inventario de reventa que se pasa a borona
    (se devolvió o ya no se puede vender como queso entero)."""

    __tablename__ = "conversiones_borona"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
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
