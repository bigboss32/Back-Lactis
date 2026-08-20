"""Transportadores: sus datos y, lo que de verdad importa, SUS TARIFAS POR RUTA.

Un transportador puede hacer varias rutas y cobrar distinto en cada una, así que
guardar la lista de rutas no es guardar unas etiquetas: es guardar plata. De ahí
que este servicio no se conforme con el CRUD genérico y haga tres cosas más:

  1. valida que cada ruta EXISTA, sea DE LA MISMA EMPRESA y no esté borrada;
  2. rechaza la misma ruta repetida en la lista, en vez de quedarse callado con
     una de las dos tarifas;
  3. mete las rutas y sus tarifas en la auditoría, que si no solo vería columnas.
"""
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError
from app.modules.rutas.models import Ruta
from app.modules.rutas.repository import RutaRepository
from app.modules.transportadores.models import (
    MODO_POR_LITRO,
    Transportador,
    TransportadorRuta,
)
from app.modules.transportadores.repository import TransportadorRepository


def _modo_general(foto: dict[str, Any]) -> str:
    return foto.get("modo_transporte") or MODO_POR_LITRO


def _modo_efectivo(foto: dict[str, Any], ruta_id: str) -> str:
    """CON QUÉ MODO se cobraría hoy un día de esa ruta, con esta foto del transportador.

    Es la misma regla que `tarifas.tarifa_de_transporte`, dicha sobre la foto que va a la
    bitácora: si la ruta tiene fila propia manda su modo, y si no manda el general. Por eso
    se pregunta por el modo EFECTIVO y no por la fila: quitarle a una ruta su tarifa propia
    no la deja sin modo, la manda a la general.
    """
    for fila in foto.get("rutas") or []:
        if str(fila["ruta_id"]) == ruta_id:
            return fila.get("modo_transporte") or MODO_POR_LITRO
    return _modo_general(foto)


def _le_cambiaron_el_modo(antes: dict[str, Any], despues: dict[str, Any]) -> bool:
    """Si este guardado le cambió el MODO a alguna tarifa (por litro ↔ día fijo).

    LA CIFRA Y EL MODO NO SE TRATAN IGUAL, y esa diferencia es deliberada:

      · corregir la CIFRA de una tarifa ($242,76 → $255, o quitarle a una ruta su tarifa
        propia para que caiga en la general) deja las fotos de los días ya anotados
        corridas, y esa corrección les llega por donde siempre: al guardar el día, al
        generar el comprobante o al recalcularlo. Se deja tal cual estaba —es
        comportamiento probado, el dueño ya lo conoce, y hay una prueba que exige que
        Generar no le borre el flete a un día que se quedó sin tarifa—;
      · cambiar el MODO no corrige una cifra, le cambia el SIGNIFICADO. Pasar la ruta "A
        fábrica" de DÍA FIJO $150.000 a POR LITRO $242,76 deja fotos que son la parte de
        un fijo que ya no existe, y no se diferencian en nada de una cifra buena: la
        grilla de la quincena, el resumen y la contabilidad las siguen sumando. Con dos
        días de 219,45 L eso son $150.000 en pantalla contra los $53.273,68 que de verdad
        se van a cobrar. Un orden de magnitud, no unos pesos.

    Se compara el modo EFECTIVO de cada ruta que aparece en cualquiera de las dos fotos, y
    no la presencia de la fila: quitarle a una ruta su tarifa propia solo cambia el modo si
    la general tiene OTRO modo, y en ese caso sí hay que rehacer las fotos.
    """
    if _modo_general(antes) != _modo_general(despues):
        return True
    rutas = {
        str(fila["ruta_id"])
        for foto in (antes, despues)
        for fila in (foto.get("rutas") or [])
    }
    return any(_modo_efectivo(antes, r) != _modo_efectivo(despues, r) for r in rutas)


class TransportadorService(BaseService[Transportador]):
    repository_cls = TransportadorRepository
    modulo = "transportadores"

    # ------------------------------------------------------------------ rutas
    def _ruta_borrada_ya_pegada(self, ruta_id: uuid.UUID) -> Ruta | None:
        """La ruta por id SIN el filtro de `deleted_at`, pero CON el de empresa.

        Es la ÚNICA consulta del módulo que se salta el `deleted_at IS NULL`, y se
        salta solo eso: `empresa_id` sigue puesto, y además se EXIGE que la ruta
        esté borrada (`deleted_at IS NOT NULL`), porque si estuviera viva la habría
        encontrado el repositorio y no se habría llegado hasta acá. Así esta puerta
        no sirve para nada distinto de lo que dice su nombre.

        Hace falta para que una ruta borrada que ya estaba pegada al transportador
        se pueda reenviar en el PUT: ver `_filas_de_rutas`.
        """
        return self.db.scalars(
            select(Ruta).where(
                Ruta.id == ruta_id,
                Ruta.empresa_id == self.ctx.empresa_id,
                Ruta.deleted_at.is_not(None),
            )
        ).first()

    def _filas_de_rutas(
        self,
        rutas_data: list[dict[str, Any]],
        *,
        transportador: Transportador | None = None,
    ) -> list[TransportadorRuta]:
        """Convierte lo que llegó del formulario en filas de la tabla puente.

        LA VALIDACIÓN DE EMPRESA ES EL HUECO QUE MÁS IMPORTA y se cierra en una
        sola línea: la ruta se busca por `RutaRepository(db, ctx.empresa_id).get`,
        cuya consulta base ya trae `empresa_id = <la del token>` y `deleted_at IS
        NULL`. Una ruta de otra quesera simplemente no aparece y se rebota con
        BusinessError. Escribir el filtro a mano acá sería otra copia de la regla de
        aislamiento, y la que se olvida de actualizar.

        LA EXCEPCIÓN, Y POR QUÉ EXISTE: una ruta BORRADA se acepta si YA estaba
        pegada a ESE transportador (para eso llega `transportador`, que en el crear
        es None porque todavía no hay nada pegado). Sin esta excepción el
        transportador quedaba IMPOSIBLE DE EDITAR, y así de literal: la lectura
        devuelve la ruta borrada —tiene que devolverla, es historia y esa tarifa
        todavía cobra—, la pantalla hace lo que hace toda pantalla (leer, cambiar un
        campo, guardar lo leído) y el PUT rebotaba con 422 "la ruta no existe". No se
        podía guardar ni el teléfono. No se le puede exigir a nadie que quite de la
        lista algo que el propio API le mandó y que la respuesta ni marcaba.

        Lo que sigue rechazado es lo que de verdad hay que rechazar: una ruta NUEVA
        borrada (ponerle tarifa a un recorrido que ya no existe no tiene sentido) y
        una ruta de otra empresa, pegada o no. Una cruzada que estuviera pegada
        tampoco pasa: la lectura ya no la muestra, así que la pantalla nunca la
        reenvía, y aceptarla sería perpetuar la fuga en vez de cerrarla.

        Se resuelve TODO antes de tocar la base: si una fila está mala, el
        transportador no queda a medio guardar con unas rutas sí y otras no.

        EL MODO QUE NO VIENE NO SE TOCA: si la fila llega sin `modo_transporte` y esa
        ruta YA estaba pegada a este transportador, se conserva el modo que tenía. Sin
        eso, un PUT que no supiera del campo le cambiaba el modo a la ruta dejándole la
        cifra intacta —$150.000 el día pasando a $150.000 el litro, $45 millones en un
        día de 300 litros— y nadie lo vería en la pantalla, porque la única cosa que
        cambió es una palabra. El porqué completo está en `TransportadorRutaIn`.
        """
        repo = RutaRepository(self.db, self.ctx.empresa_id)
        # Las rutas que este transportador YA tiene pegadas, CON SU MODO. Se lee ANTES de
        # que `_reemplazar_rutas` vacíe la colección, que es el otro motivo para
        # resolver todas las filas primero.
        modos_de_antes: dict[uuid.UUID, str] = (
            {fila.ruta_id: (fila.modo_transporte or MODO_POR_LITRO) for fila in transportador.rutas}
            if transportador is not None
            else {}
        )
        ya_pegadas: set[uuid.UUID] = set(modos_de_antes)
        vistas: set[uuid.UUID] = set()
        filas: list[TransportadorRuta] = []
        for item in rutas_data:
            ruta_id = item["ruta_id"]
            ruta = repo.get(ruta_id)
            if ruta is None and ruta_id in ya_pegadas:
                ruta = self._ruta_borrada_ya_pegada(ruta_id)
            if ruta is None:
                raise BusinessError(
                    "Una de las rutas que le asignó al transportador no existe en esta "
                    "empresa o está eliminada. Vuelva a escogerla de la lista de rutas"
                )
            if ruta_id in vistas:
                raise BusinessError(
                    f"La ruta '{ruta.nombre}' viene dos veces en la lista. Si le puso dos "
                    "tarifas distintas no se sabe cuál manda: déjela una sola vez, con la "
                    "tarifa que cobra por litro en esa ruta"
                )
            vistas.add(ruta_id)
            filas.append(
                TransportadorRuta(
                    ruta_id=ruta.id,
                    # Se cuelga la Ruta ya cargada para que la property `nombre`
                    # responda sin una consulta más al devolver la respuesta.
                    ruta=ruta,
                    valor_transporte=Decimal(item["valor_transporte"]),
                    # EL MODO QUE VENGA; si no viene, EL QUE ESA RUTA YA TENÍA; y si la
                    # ruta es nueva, por litro (el significado de siempre). Ver el
                    # docstring: es lo que evita que un payload sin el campo le cambie el
                    # modo a una tarifa dejándole la cifra igual.
                    modo_transporte=(
                        item.get("modo_transporte")
                        or modos_de_antes.get(ruta_id)
                        or MODO_POR_LITRO
                    ),
                )
            )
        return filas

    def _reemplazar_rutas(
        self, transportador: Transportador, filas: list[TransportadorRuta]
    ) -> None:
        """Deja al transportador exactamente con las rutas de `filas`, y ya.

        VA EN DOS BAJADAS A LA BASE Y NO EN UNA, y esto no es paranoia: el caso
        normal es corregirle la tarifa a una ruta que YA tiene (subir Nápoles de
        $242,76 a $300), o sea que la fila nueva y la vieja comparten la pareja
        (transportador_id, ruta_id). SQLAlchemy resuelve un `obj.rutas = filas` de
        un solo golpe y mete los INSERT antes de los DELETE, así que el único de
        esa pareja reventaba con un IntegrityError: cambiar una tarifa fallaba.

        Botando primero y bajando el DELETE con un flush, la pareja queda libre
        cuando entran las filas nuevas. Cuesta una consulta más y evita que
        corregir una tarifa sea imposible.
        """
        transportador.rutas = []
        self.db.flush()
        transportador.rutas = filas
        self.db.flush()

    def _foto(self, transportador: Transportador) -> dict[str, Any]:
        """El estado del transportador para la auditoría, CON sus rutas y tarifas.

        `serialize_entity` solo mira columnas, así que sin esto cambiarle la tarifa
        de Nápoles de $242,76 a $300 dejaría en la auditoría un "editar" con el
        antes idéntico al después: un cambio de plata sin rastro. Y es justamente
        el cambio que hay que poder reconstruir cuando el dueño pregunte por qué la
        quincena le dio distinto.

        EL MODO VA EN LA FOTO POR LA MISMA RAZÓN, y es un cambio de plata todavía más
        grande que la cifra: pasar "a fábrica" de $150.000 por litro a $150.000 por
        día deja las dos columnas idénticas y la quincena en otro orden de magnitud.
        Sin el modo en la bitácora ese cambio no dejaría rastro ninguno.
        """
        datos = serialize_entity(transportador)
        datos["rutas"] = [
            {
                "ruta_id": str(fila.ruta_id),
                "nombre": fila.nombre,
                # float y no Decimal porque esto va a una columna JSON, igual que
                # el resto de las cifras que arma `serialize_entity`.
                "valor_transporte": float(fila.valor_transporte or 0),
                "modo_transporte": fila.modo_transporte or MODO_POR_LITRO,
            }
            for fila in sorted(transportador.rutas, key=lambda f: str(f.ruta_id))
        ]
        return datos

    # ------------------------------------------------------------------- CRUD
    def crear(self, payload: BaseModel | dict[str, Any]) -> Transportador:
        """Igual que el genérico, pero con las rutas y con la foto que las incluye."""
        data = (
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, BaseModel)
            else dict(payload)
        )
        # `rutas` no es una columna: sale de `data` antes de armar la fila, o el
        # constructor del modelo recibiría diccionarios donde espera objetos.
        filas = self._filas_de_rutas(data.pop("rutas", None) or [])
        self.validar_crear(data)
        obj = self.repo.create(self._prepare_create_data(data))
        obj.rutas = filas
        # Se baja antes de la foto para que las filas ya tengan id y la auditoría
        # registre lo que de verdad quedó guardado.
        self.db.flush()
        self._audit("crear", obj.id, None, self._foto(obj))
        return obj

    def actualizar(
        self, entity_id: uuid.UUID, payload: BaseModel | dict[str, Any]
    ) -> Transportador:
        """Edita el transportador; las rutas solo si el PUT de verdad las manda."""
        obj = self.repo.get_or_fail(entity_id)
        data = (
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, BaseModel)
            else dict(payload)
        )
        # None (o ausente) = el PATCH no habla de las rutas y no se tocan.
        # [] = quitarle todas. Ver el comentario de TransportadorUpdate.rutas.
        rutas_data = data.pop("rutas", None)
        # Se validan ANTES de escribir los otros campos: si la ruta está mala, no
        # queda a medias un transportador con el nombre nuevo y las tarifas viejas.
        # Y se le pasa `obj` para que una ruta borrada que YA tenía pegada se acepte
        # de vuelta: sin eso, botar una ruta dejaba al transportador sin poderse
        # editar. Ver `_filas_de_rutas`.
        filas = (
            None
            if rutas_data is None
            else self._filas_de_rutas(rutas_data, transportador=obj)
        )
        self.validar_actualizar(obj, data)
        antes = self._foto(obj)
        data["updated_by"] = self.ctx.user_id
        obj = self.repo.update(obj, data)
        if filas is not None:
            self._reemplazar_rutas(obj, filas)
        despues = self._foto(obj)
        self._audit("editar", obj.id, antes, despues)
        # CAMBIARLE EL MODO A UNA TARIFA REHACE LAS FOTOS DE LOS DÍAS TODAVÍA SUELTOS.
        # Ver `_le_cambiaron_el_modo`, que es donde está el porqué, y
        # `RecepcionService.rehacer_las_fotos_sueltas`, que es la cuenta.
        #
        # El import va aquí adentro y no arriba: recepción ya importa este módulo para
        # leer las tarifas, y con el import arriba las dos hojas quedarían amarradas en
        # tiempo de carga.
        if _le_cambiaron_el_modo(antes, despues):
            from app.modules.recepcion.service import RecepcionService

            RecepcionService(self.db, self.ctx).rehacer_las_fotos_sueltas(obj.id)
        return obj

    def eliminar(self, entity_id: uuid.UUID) -> None:
        """Soft delete del transportador. Sus tarifas por ruta se quedan.

        A propósito: la fila del transportador no se borra de verdad, así que sus
        rutas siguen colgando de ella. Si mañana hay que reconstruir por qué una
        quincena vieja dio la cifra que dio, las tarifas todavía están ahí.
        """
        obj = self.repo.get_or_fail(entity_id)
        self.validar_eliminar(obj)
        antes = self._foto(obj)
        self.repo.soft_delete(obj, deleted_by=self.ctx.user_id)
        self._audit("eliminar", obj.id, antes, self._foto(obj))
