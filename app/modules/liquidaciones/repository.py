import uuid
from datetime import date

from app.common.repository import BaseRepository
from app.modules.liquidaciones.models import Anticipo, Liquidacion


class LiquidacionRepository(BaseRepository[Liquidacion]):
    model = Liquidacion
    default_order_by = "periodo_inicio"

    def existe_para_periodo(
        self, tipo: str, tercero_id: uuid.UUID, inicio: date, fin: date
    ) -> bool:
        campo = Liquidacion.proveedor_id if tipo == "proveedor" else Liquidacion.transportador_id
        return self.exists_where(
            Liquidacion.tipo == tipo,
            campo == tercero_id,
            Liquidacion.periodo_inicio <= fin,
            Liquidacion.periodo_fin >= inicio,
            Liquidacion.estado != "anulada",
        )


class AnticipoRepository(BaseRepository[Anticipo]):
    model = Anticipo
    default_order_by = "fecha"

    def pendientes_de(self, proveedor_id: uuid.UUID, hasta: date) -> list[Anticipo]:
        stmt = self.base_query().where(
            Anticipo.proveedor_id == proveedor_id,
            Anticipo.liquidacion_id.is_(None),
            Anticipo.fecha <= hasta,
        )
        return list(self.db.scalars(stmt).all())
