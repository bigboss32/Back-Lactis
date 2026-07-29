from sqlalchemy import select

from app.common.repository import BaseRepository
from app.modules.produccion.models import Produccion, TipoQueso


class TipoQuesoRepository(BaseRepository[TipoQueso]):
    model = TipoQueso
    search_fields = ("nombre", "descripcion")


class ProduccionRepository(BaseRepository[Produccion]):
    model = Produccion
    default_order_by = "fecha"

    def eventos_para_lotes(self) -> list[tuple]:
        """Todas las producciones vigentes en orden cronológico, para el reparto.

        Devuelve (fecha, created_at, tipo_queso_id, tipo_queso, litros_usados,
        peso_kg, merma).
        """
        return list(
            self.db.execute(
                select(
                    Produccion.fecha,
                    Produccion.created_at,
                    Produccion.tipo_queso_id,
                    TipoQueso.nombre,
                    Produccion.litros_usados,
                    Produccion.peso_kg,
                    Produccion.merma,
                )
                .join(TipoQueso, TipoQueso.id == Produccion.tipo_queso_id)
                .where(
                    Produccion.empresa_id == self.empresa_id,
                    Produccion.deleted_at.is_(None),
                    Produccion.estado == "activo",
                )
                .order_by(Produccion.fecha, Produccion.created_at)
            ).all()
        )
