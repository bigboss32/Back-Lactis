import uuid
from typing import Any

from sqlalchemy import func, select

from app.common.models import ESTADO_ACTIVO, ESTADO_INACTIVO
from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, ConflictError
from app.modules.proveedores.models import Proveedor
from app.modules.proveedores.repository import ProveedorRepository


class ProveedorService(BaseService[Proveedor]):
    repository_cls = ProveedorRepository
    modulo = "proveedores"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(Proveedor.documento == data["documento"]):
            raise ConflictError(f"Ya existe un proveedor con documento {data['documento']}")

    def validar_actualizar(self, obj: Proveedor, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(
            Proveedor.documento == data["documento"], exclude_id=obj.id
        ):
            raise ConflictError(f"Ya existe un proveedor con documento {data['documento']}")

    # ------------------------------------------------------- activar/desactivar
    def _cambiar_estado(self, entity_id: uuid.UUID, nuevo_estado: str) -> Proveedor:
        """Aparta o vuelve a habilitar a un proveedor SIN tocarle la historia.

        Lo único que se escribe es la columna `estado`. Las recepciones, las
        liquidaciones y los anticipos que ya tenía se quedan exactamente como
        están: desactivar es "este señor dejó de entregar leche", no "olvidemos
        lo que le debemos".

        `get_or_fail` va por el repositorio, así que ya trae puesto el filtro de
        empresa y el de borrados: nadie puede desactivar al proveedor de otra
        quesera aunque se sepa el id.

        Es idempotente a propósito: si ya está en ese estado no se vuelve a
        escribir ni se ensucia la auditoría con un cambio de nada a nada. Así el
        botón oprimido dos veces (o el reintento del navegador) da lo mismo.
        """
        obj = self.repo.get_or_fail(entity_id)
        if obj.estado == nuevo_estado:
            return obj
        antes = serialize_entity(obj)
        obj.estado = nuevo_estado
        obj.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", obj.id, antes, serialize_entity(obj))
        return obj

    def desactivar(self, entity_id: uuid.UUID) -> Proveedor:
        return self._cambiar_estado(entity_id, ESTADO_INACTIVO)

    def activar(self, entity_id: uuid.UUID) -> Proveedor:
        return self._cambiar_estado(entity_id, ESTADO_ACTIVO)

    # ----------------------------------------------------------------- eliminar
    def _cuenta_historia(self, proveedor_id: uuid.UUID) -> tuple[int, int]:
        """Cuántas recepciones y liquidaciones vivas cuelgan de este proveedor.

        Se cuenta con empresa_id + deleted_at IS NULL, igual que cualquier otra
        consulta del sistema, para no contar la historia de otra quesera ni la
        que ya se dio de baja.
        """
        from app.modules.liquidaciones.models import Liquidacion
        from app.modules.recepcion.models import RecepcionLeche

        recepciones = self.db.scalar(
            select(func.count(RecepcionLeche.id)).where(
                RecepcionLeche.empresa_id == self.ctx.empresa_id,
                RecepcionLeche.deleted_at.is_(None),
                RecepcionLeche.proveedor_id == proveedor_id,
            )
        ) or 0
        liquidaciones = self.db.scalar(
            select(func.count(Liquidacion.id)).where(
                Liquidacion.empresa_id == self.ctx.empresa_id,
                Liquidacion.deleted_at.is_(None),
                Liquidacion.proveedor_id == proveedor_id,
            )
        ) or 0
        return recepciones, liquidaciones

    def validar_eliminar(self, obj: Proveedor) -> None:
        """Al proveedor con historia no se le da caneca: se DESACTIVA.

        El borrado es lógico (deja la fila con deleted_at), así que la plata no
        se pierde de la base de datos. Pero el filtro `deleted_at IS NULL` está
        en TODA consulta, así que el proveedor eliminado desaparece de la
        pantalla de Proveedores —incluso filtrando por Estado: Inactivo— y no
        hay forma de devolverlo desde la aplicación. Para el dueño eso es
        indistinguible de "se eliminó todo", que es justo lo que él reportó.

        Por eso, si tiene leche recibida o liquidaciones, la caneca rebota y lo
        manda a desactivar, que hace lo que él quiere (apartarlo) sin esconderle
        la historia. Un proveedor recién creado por error sí se puede eliminar:
        ahí no hay nada que perder.
        """
        recepciones, liquidaciones = self._cuenta_historia(obj.id)
        if not recepciones and not liquidaciones:
            return
        partes = []
        if recepciones:
            partes.append(f"{recepciones} recepción(es) de leche")
        if liquidaciones:
            partes.append(f"{liquidaciones} liquidación(es)")
        raise BusinessError(
            f"No se puede eliminar a «{obj.nombre}»: tiene {' y '.join(partes)} en el sistema "
            "y al eliminarlo se perdería de vista esa historia. Use «Desactivar» para apartarlo: "
            "deja de aparecer para registrar leche nueva, pero conserva sus recepciones, "
            "liquidaciones y pagos, y se puede reactivar cuando vuelva a entregar."
        )


def exigir_proveedor_activo(proveedor: Proveedor) -> None:
    """Guarda compartida: a un proveedor inactivo no se le registra leche nueva.

    Vive en el backend y no en la pantalla a propósito. Esconderlo del selector
    de la recepción no alcanza: el id sigue siendo válido y cualquiera que le
    pegue a la API directo (o que tenga la pantalla abierta desde antes de que
    lo desactivaran) le seguiría metiendo litros al que ya se retiró.

    El mensaje dice el nombre y cómo devolverse, porque quien lo ve es el
    operario que está registrando la quincena, no un programador.
    """
    if proveedor.estado != ESTADO_ACTIVO:
        raise BusinessError(
            f"El proveedor «{proveedor.nombre}» está inactivo: no se le puede registrar "
            "leche nueva. Si volvió a entregar, actívelo en Proveedores con la acción "
            "«Reactivar» y vuelva a intentarlo. Sus recepciones y liquidaciones "
            "anteriores siguen intactas."
        )
