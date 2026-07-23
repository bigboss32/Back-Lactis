import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

# Categorías sembradas por defecto para cada empresa.
# NO se incluyen "Compra de leche" ni el transporte de recepción de leche: esos
# costos ya se contabilizan vía recepciones/liquidaciones y duplicarlos aquí
# inflaría el estado de resultados. "Flete de ventas" SÍ va: es el despacho del
# queso vendido, un costo distinto que no se registra en ningún otro lado.
CATEGORIAS_DEFECTO = (
    "Combustible",
    "Servicios",
    "Nómina",
    "Mantenimiento",
    "Papelería",
    "Insumos",
    "Flete de ventas",
    "Otros",
)


class CategoriaGasto(TenantMixin, AuditMixin, Base):
    __tablename__ = "categorias_gasto"

    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(300))


class Gasto(TenantMixin, AuditMixin, Base):
    __tablename__ = "gastos"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    categoria_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categorias_gasto.id"), index=True)
    concepto: Mapped[str] = mapped_column(String(200), nullable=False)
    proveedor: Mapped[str | None] = mapped_column(String(150))
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    numero_factura: Mapped[str | None] = mapped_column(String(50))
    observaciones: Mapped[str | None] = mapped_column(String(500))
    adjunto_url: Mapped[str | None] = mapped_column(String(300))
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))

    categoria = relationship("CategoriaGasto", lazy="joined")
