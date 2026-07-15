import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class RecepcionLeche(TenantMixin, AuditMixin, Base):
    __tablename__ = "recepciones_leche"
    __table_args__ = (
        Index("ix_recepcion_fecha_proveedor", "empresa_id", "fecha", "proveedor_id"),
    )

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    proveedor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proveedores.id"), nullable=False)
    transportador_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transportadores.id"))
    ruta_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rutas.id"))
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))

    cantidad_litros: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_litro: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bonificaciones: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    descuentos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    # Calculados por el servicio en cada escritura
    valor_bruto: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    valor_transporte: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    valor_neto: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))

    # Marcas de liquidación: una recepción se liquida al proveedor (leche)
    # y al transportador (flete) por separado; cada marca evita duplicados
    liquidacion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("liquidaciones.id"), index=True
    )
    liquidacion_transporte_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("liquidaciones.id"), index=True
    )

    proveedor = relationship("Proveedor", lazy="joined")
    transportador = relationship("Transportador", lazy="joined")
    ruta = relationship("Ruta", lazy="joined")
