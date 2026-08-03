import uuid
from datetime import date

from sqlalchemy import func, select

from app.common.repository import BaseRepository
from app.modules.produccion.models import (
    CicloDespacho,
    CicloDespachoLote,
    Produccion,
    TipoQueso,
)


class TipoQuesoRepository(BaseRepository[TipoQueso]):
    model = TipoQueso
    search_fields = ("nombre", "descripcion")


class ProduccionRepository(BaseRepository[Produccion]):
    model = Produccion
    default_order_by = "fecha"

    def eventos_para_lotes(self) -> list[tuple]:
        """Todas las producciones vigentes en orden cronológico, para el reparto.

        Devuelve (fecha, created_at, tipo_queso_id, tipo_queso, litros_usados,
        peso_kg, merma, id).

        El `id` va al final para no mover las posiciones que ya usaba quien llama.
        Se necesita porque la merma de un cierre de ciclo se le carga a UNA tanda
        concreta y no a la más vieja de la cola: sin el id no habría cómo decir de
        quién es cada kilo que se secó.
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
                    Produccion.id,
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

    def tandas_del_rango(self, desde: date, hasta: date) -> list[tuple]:
        """Las tandas hechas entre dos fechas, para armar el cierre de un ciclo.

        Devuelve (id, fecha, tipo_queso_id, tipo_queso, peso_kg, sucursal_id), en
        orden cronológico. Es el mismo criterio de vigencia que
        `eventos_para_lotes`: si una producción no cuenta para la utilidad por
        lote, tampoco puede contar para la merma del ciclo.
        """
        return list(
            self.db.execute(
                select(
                    Produccion.id,
                    Produccion.fecha,
                    Produccion.tipo_queso_id,
                    TipoQueso.nombre,
                    Produccion.peso_kg,
                    Produccion.sucursal_id,
                )
                .join(TipoQueso, TipoQueso.id == Produccion.tipo_queso_id)
                .where(
                    Produccion.empresa_id == self.empresa_id,
                    Produccion.deleted_at.is_(None),
                    Produccion.estado == "activo",
                    Produccion.fecha >= desde,
                    Produccion.fecha <= hasta,
                )
                .order_by(Produccion.fecha, Produccion.created_at)
            ).all()
        )

    def primera_fecha(self) -> date | None:
        """La fecha de la producción más vieja. Es el arranque del primer ciclo
        cuando todavía no se ha cerrado ninguno."""
        return self.db.scalar(
            select(func.min(Produccion.fecha)).where(
                Produccion.empresa_id == self.empresa_id,
                Produccion.deleted_at.is_(None),
                Produccion.estado == "activo",
            )
        )


class CicloDespachoRepository(BaseRepository[CicloDespacho]):
    """Los ciclos de despacho de la empresa.

    Igual que las temporadas de reventa: no se pueden solapar, y por la misma
    razón elevada al cuadrado. Allá un solape haría que la misma plata se contara
    dos veces al mirarla; aquí haría que la misma merma se COBRARA dos veces,
    porque cerrar sí escribe: crea ajustes de inventario.
    """

    model = CicloDespacho
    search_fields = ("nombre",)
    default_order_by = "fecha_inicio"

    def vigentes(self) -> list[CicloDespacho]:
        """Todos los ciclos, del más reciente al más viejo: lo que interesa
        primero es el último que se cerró."""
        return list(
            self.db.execute(
                select(CicloDespacho)
                .where(
                    CicloDespacho.empresa_id == self.empresa_id,
                    CicloDespacho.deleted_at.is_(None),
                )
                .order_by(CicloDespacho.fecha_inicio.desc())
            ).scalars()
        )

    def solapados(
        self,
        inicio: date,
        fin: date,
        *,
        solo_cerrados: bool = True,
        excluir_id: uuid.UUID | None = None,
    ) -> list[CicloDespacho]:
        """Los ciclos que se cruzan con el rango dado.

        Dos rangos se cruzan cuando cada uno empieza antes de que termine el otro.
        Aquí las dos fechas son obligatorias, así que no hace falta el COALESCE
        contra NULL que sí necesitan las temporadas de reventa.

        SOLO LOS CERRADOS RESERVAN SUS FECHAS, y por eso `solo_cerrados` va en
        True por defecto: lo que no se puede repetir es COBRAR la merma dos veces
        sobre el mismo queso, y un ciclo reabierto ya no tiene merma —se deshizo
        al reabrirlo—. Si los reabiertos también bloquearan, el dueño que cierra
        por equivocación quedaría atrapado: reabre para corregir y el sistema le
        dice que no puede volver a cerrar por culpa del ciclo que acaba de vaciar,
        con un motivo que además sería mentira.
        """
        criterios = [
            CicloDespacho.empresa_id == self.empresa_id,
            CicloDespacho.deleted_at.is_(None),
            CicloDespacho.fecha_inicio <= fin,
            CicloDespacho.fecha_fin >= inicio,
        ]
        if solo_cerrados:
            criterios.append(CicloDespacho.cerrado_at.is_not(None))
        else:
            criterios.append(CicloDespacho.cerrado_at.is_(None))
        if excluir_id is not None:
            criterios.append(CicloDespacho.id != excluir_id)
        return list(
            self.db.execute(
                select(CicloDespacho)
                .where(*criterios)
                .order_by(CicloDespacho.fecha_inicio)
            ).scalars()
        )

    def solapado(
        self, inicio: date, fin: date, excluir_id: uuid.UUID | None = None
    ) -> CicloDespacho | None:
        """El primer ciclo CERRADO que se cruce con el rango, si hay."""
        cruzados = self.solapados(inicio, fin, excluir_id=excluir_id)
        return cruzados[0] if cruzados else None

    def ultimo_cierre(self) -> date | None:
        """La fecha de fin del ciclo CERRADO más reciente.

        Solo los cerrados: un ciclo que se reabrió dejó de tapar su rango, y el
        siguiente que se proponga tiene que volver a incluir esos días o se
        quedarían sin cerrar para siempre.
        """
        return self.db.scalar(
            select(func.max(CicloDespacho.fecha_fin)).where(
                CicloDespacho.empresa_id == self.empresa_id,
                CicloDespacho.deleted_at.is_(None),
                CicloDespacho.cerrado_at.is_not(None),
            )
        )

    def mermas_para_lotes(self) -> list[tuple]:
        """La merma ya repartida de todos los ciclos CERRADOS, para la cadena.

        Devuelve (fecha_produccion, produccion_id, tipo_queso_id, kilos_merma),
        en orden cronológico.

        Solo la de los ciclos cerrados. Al reabrir uno sus filas se borran, así
        que el filtro por `cerrado_at` es un cinturón de seguridad: si se colara
        la merma de un ciclo reabierto, se estaría bajando de la bodega un queso
        cuyo ajuste de inventario ya no existe, y la pantalla de lotes y la de
        inventario volverían a decir cosas distintas de los mismos kilos.
        """
        return list(
            self.db.execute(
                select(
                    CicloDespachoLote.fecha_produccion,
                    CicloDespachoLote.produccion_id,
                    CicloDespachoLote.tipo_queso_id,
                    CicloDespachoLote.kilos_merma,
                )
                .join(CicloDespacho, CicloDespacho.id == CicloDespachoLote.ciclo_id)
                .where(
                    CicloDespachoLote.empresa_id == self.empresa_id,
                    CicloDespachoLote.deleted_at.is_(None),
                    CicloDespacho.empresa_id == self.empresa_id,
                    CicloDespacho.deleted_at.is_(None),
                    CicloDespacho.cerrado_at.is_not(None),
                    CicloDespachoLote.kilos_merma > 0,
                )
                .order_by(
                    CicloDespachoLote.fecha_produccion, CicloDespachoLote.created_at
                )
            ).all()
        )


class CicloDespachoLoteRepository(BaseRepository[CicloDespachoLote]):
    model = CicloDespachoLote
    default_order_by = "fecha_produccion"

    def del_ciclo(self, ciclo_id: uuid.UUID) -> list[CicloDespachoLote]:
        return list(
            self.db.execute(
                select(CicloDespachoLote)
                .where(
                    CicloDespachoLote.empresa_id == self.empresa_id,
                    CicloDespachoLote.deleted_at.is_(None),
                    CicloDespachoLote.ciclo_id == ciclo_id,
                )
                .order_by(CicloDespachoLote.fecha_produccion)
            ).scalars()
        )
