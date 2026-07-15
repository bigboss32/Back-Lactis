import uuid
from datetime import date

from sqlalchemy import func, select

from app.common.repository import BaseRepository
from app.modules.recepcion.models import RecepcionLeche


class RecepcionRepository(BaseRepository[RecepcionLeche]):
    model = RecepcionLeche
    default_order_by = "fecha"

    def rango_criteria(self, desde: date | None, hasta: date | None) -> list:
        criteria = []
        if desde:
            criteria.append(RecepcionLeche.fecha >= desde)
        if hasta:
            criteria.append(RecepcionLeche.fecha <= hasta)
        return criteria

    def existe_registro_dia(
        self, proveedor_id: uuid.UUID, fecha: date, exclude_id: uuid.UUID | None = None
    ) -> bool:
        return self.exists_where(
            RecepcionLeche.proveedor_id == proveedor_id,
            RecepcionLeche.fecha == fecha,
            exclude_id=exclude_id,
        )

    def resumen_por_dia(self, desde: date, hasta: date) -> list:
        stmt = (
            select(
                RecepcionLeche.fecha,
                func.sum(RecepcionLeche.cantidad_litros).label("total_litros"),
                func.sum(RecepcionLeche.valor_bruto).label("valor_bruto"),
                func.sum(RecepcionLeche.valor_transporte).label("valor_transporte"),
                func.sum(RecepcionLeche.valor_neto).label("valor_neto"),
                func.count(RecepcionLeche.id).label("recepciones"),
            )
            .where(
                RecepcionLeche.deleted_at.is_(None),
                RecepcionLeche.empresa_id == self.empresa_id,
                RecepcionLeche.fecha >= desde,
                RecepcionLeche.fecha <= hasta,
            )
            .group_by(RecepcionLeche.fecha)
            .order_by(RecepcionLeche.fecha)
        )
        return list(self.db.execute(stmt).all())

    def sin_liquidar(self, desde: date, hasta: date, proveedor_id: uuid.UUID | None = None) -> list[RecepcionLeche]:
        stmt = self.base_query().where(
            RecepcionLeche.fecha >= desde,
            RecepcionLeche.fecha <= hasta,
            RecepcionLeche.liquidacion_id.is_(None),
            RecepcionLeche.estado == "activo",
        )
        if proveedor_id:
            stmt = stmt.where(RecepcionLeche.proveedor_id == proveedor_id)
        return list(self.db.scalars(stmt).all())
