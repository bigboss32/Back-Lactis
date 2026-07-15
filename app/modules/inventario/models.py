import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

CATEGORIA_LECHE = "leche"
CATEGORIA_INSUMO = "insumo"
CATEGORIA_EMPAQUE = "empaque"
CATEGORIA_PRODUCTO_TERMINADO = "producto_terminado"

MOVIMIENTO_ENTRADA = "entrada"
MOVIMIENTO_SALIDA = "salida"
MOVIMIENTO_AJUSTE = "ajuste"


class Producto(TenantMixin, AuditMixin, Base):
    """Ítem de inventario: leche, sal, cuajo, empaques, bolsas, etiquetas o queso."""

    __tablename__ = "productos"

    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    categoria: Mapped[str] = mapped_column(String(30), default=CATEGORIA_INSUMO, index=True)
    unidad: Mapped[str] = mapped_column(String(20), default="unidad")  # litro, kg, unidad...
    stock_minimo: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    costo_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    tipo_queso_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tipos_queso.id"))


class MovimientoInventario(TenantMixin, AuditMixin, Base):
    __tablename__ = "movimientos_inventario"

    producto_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("productos.id"), index=True)
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)  # entrada | salida | ajuste
    # En ajustes la cantidad puede ser negativa (ajuste hacia abajo)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    costo_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    referencia: Mapped[str | None] = mapped_column(String(200))  # ej: producción, venta, compra
    observaciones: Mapped[str | None] = mapped_column(String(500))

    producto = relationship("Producto", lazy="joined")
