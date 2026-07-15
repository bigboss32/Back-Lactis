import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
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

    cliente = relationship("Cliente", lazy="joined")
    detalles: Mapped[list["VentaDetalle"]] = relationship(
        back_populates="venta", lazy="selectin", cascade="all, delete-orphan"
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


class Pago(TenantMixin, AuditMixin, Base):
    __tablename__ = "pagos"

    venta_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ventas.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    metodo: Mapped[str] = mapped_column(String(30), default=METODO_EFECTIVO)
    referencia: Mapped[str | None] = mapped_column(String(100))
    observaciones: Mapped[str | None] = mapped_column(String(300))

    venta: Mapped[Venta] = relationship(back_populates="pagos")
