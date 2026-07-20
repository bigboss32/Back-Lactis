from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.common.repository import BaseRepository
from app.modules.reventa.models import (
    DESTINO_BORONA,
    DESTINO_MERMA,
    CompraQueso,
    ConversionBorona,
    VentaQueso,
)

CERO = Decimal("0")


class CompraQuesoRepository(BaseRepository[CompraQueso]):
    model = CompraQueso
    search_fields = ("productor",)
    default_order_by = "fecha"

    def totales_periodo(self, desde: date, hasta: date) -> tuple[Decimal, Decimal]:
        fila = self.db.execute(
            select(
                func.coalesce(func.sum(CompraQueso.kilos_netos), 0),
                func.coalesce(func.sum(CompraQueso.valor_total), 0),
            ).where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
                CompraQueso.fecha.between(desde, hasta),
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1])

    def acumulados(self) -> tuple[Decimal, Decimal, Decimal]:
        """(kilos netos históricos, borona de compras, saldo por pagar)."""
        fila = self.db.execute(
            select(
                func.coalesce(func.sum(CompraQueso.kilos_netos), 0),
                func.coalesce(func.sum(CompraQueso.borona_kilos), 0),
                func.coalesce(func.sum(CompraQueso.valor_total - CompraQueso.abonado), 0),
            ).where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1]), Decimal(fila[2])

    def nombres_productores(self) -> list[str]:
        """Nombres de productores ya usados (para autocompletar), sin repetir."""
        rows = self.db.scalars(
            select(CompraQueso.productor)
            .where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
            )
            .distinct()
            .order_by(CompraQueso.productor)
        ).all()
        return [r for r in rows if r]


class VentaQuesoRepository(BaseRepository[VentaQueso]):
    model = VentaQueso
    search_fields = ("cliente",)
    default_order_by = "fecha"

    def totales_periodo(
        self, desde: date, hasta: date, tipo: str | None = None
    ) -> tuple[Decimal, Decimal]:
        criterios = [
            VentaQueso.empresa_id == self.empresa_id,
            VentaQueso.deleted_at.is_(None),
            VentaQueso.estado != "anulada",
            VentaQueso.fecha.between(desde, hasta),
        ]
        if tipo:
            criterios.append(VentaQueso.tipo == tipo)
        fila = self.db.execute(
            select(
                func.coalesce(func.sum(VentaQueso.kilos), 0),
                func.coalesce(func.sum(VentaQueso.valor_total), 0),
            ).where(*criterios)
        ).one()
        return Decimal(fila[0]), Decimal(fila[1])

    def acumulados(self) -> tuple[Decimal, Decimal, Decimal]:
        """(kilos queso vendidos, kilos borona vendidos, saldo por cobrar)."""
        fila = self.db.execute(
            select(
                func.coalesce(
                    func.sum(VentaQueso.kilos).filter(VentaQueso.tipo == "queso"), 0
                ),
                func.coalesce(
                    func.sum(VentaQueso.kilos).filter(VentaQueso.tipo == "borona"), 0
                ),
                func.coalesce(func.sum(VentaQueso.valor_total - VentaQueso.abonado), 0),
            ).where(
                VentaQueso.empresa_id == self.empresa_id,
                VentaQueso.deleted_at.is_(None),
                VentaQueso.estado != "anulada",
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1]), Decimal(fila[2])

    def gastos_periodo(self, desde: date, hasta: date) -> Decimal:
        """Suma de gastos de venta (transporte, etc.) del período."""
        total = self.db.scalar(
            select(func.coalesce(func.sum(VentaQueso.gasto_monto), 0)).where(
                VentaQueso.empresa_id == self.empresa_id,
                VentaQueso.deleted_at.is_(None),
                VentaQueso.estado != "anulada",
                VentaQueso.fecha.between(desde, hasta),
            )
        )
        return Decimal(total or 0)

    def nombres_clientes(self) -> list[str]:
        """Nombres de clientes ya usados (para autocompletar), sin repetir."""
        rows = self.db.scalars(
            select(VentaQueso.cliente)
            .where(
                VentaQueso.empresa_id == self.empresa_id,
                VentaQueso.deleted_at.is_(None),
                VentaQueso.estado != "anulada",
            )
            .distinct()
            .order_by(VentaQueso.cliente)
        ).all()
        return [r for r in rows if r]


class ConversionBoronaRepository(BaseRepository[ConversionBorona]):
    model = ConversionBorona
    default_order_by = "fecha"

    def _total(self, destino: str | None = None) -> Decimal:
        criterios = [
            ConversionBorona.empresa_id == self.empresa_id,
            ConversionBorona.deleted_at.is_(None),
            ConversionBorona.estado == "activo",
        ]
        if destino is not None:
            criterios.append(ConversionBorona.destino == destino)
        return Decimal(
            self.db.scalar(
                select(func.coalesce(func.sum(ConversionBorona.kilos), 0)).where(*criterios)
            )
            or CERO
        )

    def total_convertido(self) -> Decimal:
        """Todo lo que sale del queso disponible (borona + merma)."""
        return self._total()

    def total_a_borona(self) -> Decimal:
        """Solo lo que se pasó a borona (suma al inventario de borona)."""
        return self._total(DESTINO_BORONA)

    def total_merma(self) -> Decimal:
        """Solo lo registrado como merma (pérdida)."""
        return self._total(DESTINO_MERMA)
