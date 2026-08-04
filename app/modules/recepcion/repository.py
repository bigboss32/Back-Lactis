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
        """Si la casilla (proveedor, día) ya está ocupada por un día que CUENTA.

        LA CASILLA LA RESERVA SOLO UN DÍA ACTIVO, y el filtro de estado es el
        arreglo. La regla existe para que la leche de un día no se anote —ni se
        pague— dos veces; un día APAGADO no se paga ninguna: sale de los dos
        comprobantes, de la grilla y del costo de contabilidad. Reservando la
        casilla con un día apagado no se evitaba ningún doble pago y sí se perdía la
        única forma de anotar la leche buena de ese día: el POST rebotaba con un 409
        ("Edite el registro existente") y el registro existente es justo el que la
        quesera decidió no contar. La casilla quedaba muerta.

        Es además lo que ya pasaba con el día BORRADO en suave: `base_query` filtra
        `deleted_at IS NULL`, así que borrar el día SÍ liberaba la casilla. Apagarlo
        y borrarlo dejan el día igual de afuera de toda la plata, así que no hay
        motivo para que uno libere la casilla y el otro no.

        SIGUE SIENDO IMPOSIBLE TENER DOS DÍAS ACTIVOS IGUALES, que es lo que
        importa: con un día apagado y uno activo en la misma casilla, volver a
        prender el apagado pasa por este mismo chequeo (el PUT lo llama con
        `exclude_id` de la fila que se edita), encuentra el activo y rebota con el
        409. Editar el apagado también rebota mientras su casilla esté ocupada, y
        está bien: para corregirlo hay que sacar primero al que está contando.
        """
        return self.exists_where(
            RecepcionLeche.proveedor_id == proveedor_id,
            RecepcionLeche.fecha == fecha,
            RecepcionLeche.estado == "activo",
            exclude_id=exclude_id,
        )

    def resumen_por_dia(self, desde: date, hasta: date) -> list:
        """Litros y plata por día del período (GET /recepciones/resumen/periodo).

        SOLO LOS DÍAS ACTIVOS. Sin ese filtro este resumen contradecía a
        contabilidad y a los dos comprobantes sobre la misma leche: en una quincena
        de un solo proveedor con un día apagado decía 253,42 L y $61.520,23 de flete
        contra los 170,94 L y $41.497,39 que mostraban la grilla, el estado de
        resultados y las dos liquidaciones. Son 82,48 L de diferencia, y el dueño
        cuadra estas dos pantallas a mano.
        """
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
                RecepcionLeche.estado == "activo",
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

    def eventos_para_lotes(self) -> list[tuple]:
        """Toda la leche recibida vigente, en orden cronológico, para el reparto.

        Sin filtro de fechas A PROPÓSITO: la leche del 30 de junio se convierte en
        el queso de julio, así que para costear la producción de julio hay que haber
        procesado lo de antes. Filtrar aquí dejaría la producción de los primeros
        días sin leche que la respalde.

        Devuelve (fecha, created_at, proveedor, litros, valor_neto, transporte).
        """
        from app.modules.proveedores.models import Proveedor

        return list(
            self.db.execute(
                select(
                    RecepcionLeche.fecha,
                    RecepcionLeche.created_at,
                    Proveedor.nombre,
                    RecepcionLeche.cantidad_litros,
                    RecepcionLeche.valor_neto,
                    RecepcionLeche.valor_transporte,
                )
                .join(Proveedor, Proveedor.id == RecepcionLeche.proveedor_id)
                .where(
                    RecepcionLeche.empresa_id == self.empresa_id,
                    RecepcionLeche.deleted_at.is_(None),
                    RecepcionLeche.estado == "activo",
                )
                .order_by(RecepcionLeche.fecha, RecepcionLeche.created_at)
            ).all()
        )
