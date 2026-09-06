import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.common.dinero import CERO
from app.common.service import BaseService, serialize_entity
from app.core.config import settings
from app.core.exceptions import BusinessError, ConflictError
from app.core.imagenes import leer_y_validar_soporte
from app.core.logging_config import get_logger
from app.core.pagination import PageParams
from app.core.storage import (
    MENSAJE_NO_CONFIGURADO,
    R2Client,
    borrar_del_bucket_al_confirmar,
    caducidad_utc,
    r2_configurado,
    texto_caducidad,
)
from app.modules.gastos.models import AdjuntoGasto, CategoriaGasto, Gasto
from app.modules.gastos.repository import (
    AdjuntoGastoRepository,
    CategoriaGastoRepository,
    GastoRepository,
)
from app.modules.gastos.schemas import (
    AdjuntoGastoRead,
    AdjuntosGastoLista,
    EnlaceFacturaCompartida,
)

logger_adjuntos = get_logger("gastos.adjuntos")

# Dos decimales: es plata y se muestra con dos.
DOS_DECIMALES = Decimal("0.01")


class CategoriaGastoService(BaseService[CategoriaGasto]):
    repository_cls = CategoriaGastoRepository
    modulo = "gastos"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(CategoriaGasto.nombre == data["nombre"]):
            raise ConflictError(f"Ya existe la categoría '{data['nombre']}'")


class GastoService(BaseService[Gasto]):
    repository_cls = GastoRepository
    modulo = "gastos"

    @staticmethod
    def _calcular_valor(data: dict[str, Any], actual: Gasto | None = None) -> dict[str, Any]:
        """Si el gasto se cobra por unidad (cantidad × precio), calcula el valor."""
        cantidad = data["cantidad"] if "cantidad" in data else (actual.cantidad if actual else None)
        precio = (
            data["precio_unitario"]
            if "precio_unitario" in data
            else (actual.precio_unitario if actual else None)
        )
        if cantidad is not None and precio is not None:
            data["valor"] = (Decimal(cantidad) * Decimal(precio)).quantize(Decimal("0.01"))
        return data

    def crear(self, payload: Any) -> Gasto:
        data = payload.model_dump(exclude_unset=True)
        CategoriaGastoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["categoria_id"])
        return super().crear(self._calcular_valor(data))

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Gasto:
        actual = self.repo.get_or_fail(entity_id)
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        if data.get("categoria_id"):
            CategoriaGastoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["categoria_id"])
        return super().actualizar(entity_id, self._calcular_valor(data, actual))

    @staticmethod
    def _criterios(
        *,
        search: str | None,
        categoria_id: uuid.UUID | None,
        desde: date | None,
        hasta: date | None,
    ) -> dict[str, Any]:
        """LOS FILTROS DEL LISTADO, ESCRITOS UNA SOLA VEZ.

        Existe para que el total de la barra y las filas de la tabla no puedan
        separarse: los dos salen de aquí. Si mañana se agrega un filtro nuevo y
        solo se acuerda de él uno de los dos caminos, el dueño vería una tabla que
        no suma lo que dice el total — y él la revisa con calculadora.
        """
        extra = []
        if desde:
            extra.append(Gasto.fecha >= desde)
        if hasta:
            extra.append(Gasto.fecha <= hasta)
        return {
            "search": search,
            "filters": {"categoria_id": categoria_id},
            "extra_criteria": extra,
        }

    def listar_filtrado(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        categoria_id: uuid.UUID | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> tuple[list[Gasto], int]:
        return self.repo.list_paginated(
            params,
            **self._criterios(
                search=search, categoria_id=categoria_id, desde=desde, hasta=hasta
            ),
        )

    def suma_filtrada(
        self,
        *,
        search: str | None = None,
        categoria_id: uuid.UUID | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> Decimal:
        """Cuánto suman TODOS los gastos que cumplen los filtros.

        SON TODOS, NO LOS DE LA PÁGINA. Es justo para lo que sirve: el dueño filtra
        "combustible de julio", ve 3 páginas de 20 filas y lo que necesita saber es
        cuánto se le fue en combustible ese mes, no cuánto suman las veinte que
        alcanzó a ver.

        SUMA EXACTAMENTE LAS FILAS QUE MUESTRA LA TABLA —las mismas, ni una más—
        porque los filtros salen de `_criterios`, el mismo sitio del que salen las
        filas. Eso incluye los gastos inactivos si el filtro los alcanza: si
        aparecen en la tabla tienen que estar en el total, o la cuenta a mano no da.

        El `quantize` del final es por SQLite, no por Postgres: allá la suma de
        columnas `Numeric` vuelve como flotante y puede traer una cola de decimales
        que en pantalla se vería como un centavo de más.
        """
        criterios = self._criterios(
            search=search, categoria_id=categoria_id, desde=desde, hasta=hasta
        )
        stmt = self.repo.base_query()
        stmt = self.repo.apply_search(stmt, criterios["search"])
        stmt = self.repo.apply_filters(stmt, criterios["filters"])
        if criterios["extra_criteria"]:
            stmt = stmt.where(*criterios["extra_criteria"])

        sub = stmt.subquery()
        total = self.db.scalar(select(func.coalesce(func.sum(sub.c.valor), CERO)))
        return Decimal(total or CERO).quantize(DOS_DECIMALES)

    def eliminar(self, entity_id: uuid.UUID) -> None:
        """Borra el gasto Y se lleva sus facturas.

        Sin esto, borrar un gasto dejaba sus facturas en el bucket para siempre: el
        gasto ya no existe, así que nadie las puede ver ni borrar desde la
        aplicación, y la quesera sigue pagando ese almacenamiento sin saberlo.

        SE VALIDA ANTES DE BARRER, y por eso se llama a `validar_eliminar` aquí
        aunque `super().eliminar` lo vuelva a hacer —hoy no cuesta nada, es un punto
        de extensión vacío—. Al revés, el día que alguien le ponga un guardia a
        borrar gastos, las facturas ya se habrían ido cuando ese guardia dijera que
        no. Marcarlas es reversible con el rollback; el archivo del bucket se borra
        al confirmar, así que tampoco se pierde si la operación no cuaja.
        """
        obj = self.repo.get_or_fail(entity_id)
        self.validar_eliminar(obj)
        AdjuntoGastoService(self.db, self.ctx).limpiar_de_gasto(obj.id)
        super().eliminar(entity_id)


class AdjuntoGastoService(BaseService[AdjuntoGasto]):
    """Las facturas de los gastos, guardadas en el bucket privado.

    TRES CAMINOS DISTINTOS PARA MIRAR UN ARCHIVO, a propósito:

    - VER (`listar`): enlaces de minutos, para la pantalla. Se firman de nuevo cada
      vez que se abre la lista.
    - COMPARTIR (`compartir`): un enlace de días para UNA factura, para mandarla por
      WhatsApp o por correo al contador. Sale con la fecha de caducidad escrita en
      cristiano y queda registrado en la auditoría.
    - BORRAR (`eliminar_adjunto`): quita la fila Y el objeto en R2.

    Los tres empiezan por comprobar que el gasto sea DE LA EMPRESA de quien
    pregunta. Esa comprobación no está en un `if` suelto: se hace buscando el gasto
    con su propio repositorio, que ya filtra por `empresa_id` y `deleted_at IS
    NULL`. Si no es suyo, no aparece, y sale un 404 antes de que se firme nada.
    """

    repository_cls = AdjuntoGastoRepository
    modulo = "gastos"

    # ------------------------------------------------------------- utilidades
    @property
    def _max_bytes(self) -> int:
        return settings.ADJUNTOS_MAX_MB * 1024 * 1024

    def _gasto(self, gasto_id: uuid.UUID) -> Gasto:
        """El gasto, o 404. El filtro por empresa lo pone el repositorio."""
        return GastoRepository(self.db, self.ctx.empresa_id).get_or_fail(gasto_id)

    def _adjunto(self, adjunto_id: uuid.UUID) -> AdjuntoGasto:
        """La factura, con el mismo candado: repositorio con filtro de empresa."""
        return self.repo.get_or_fail(adjunto_id)

    def _clave(self, *, gasto_id: uuid.UUID, extension: str) -> str:
        """`{empresa_id}/gastos/{gasto_id}/{uuid}{ext}`.

        El empresa_id va DENTRO de la llave a propósito: aunque alguien adivinara
        el resto, la llave de un archivo de otra quesera empieza por un uuid que no
        es el suyo. Y el nombre del archivo NO entra en la llave: lo escribe quien
        sube, y un nombre con `../` o con caracteres raros terminaría creando
        objetos donde no van.
        """
        return f"{self.ctx.empresa_id}/gastos/{gasto_id}/{uuid.uuid4().hex}{extension}"

    def _nombre_de_quien_sube(self) -> str | None:
        usuario = getattr(self.ctx, "user", None)
        if usuario is None:
            return None
        nombre = getattr(usuario, "nombre", "") or ""
        apellido = getattr(usuario, "apellido", "") or ""
        return f"{nombre} {apellido}".strip()[:150] or None

    def _a_read(
        self, adjunto: AdjuntoGasto, cliente: R2Client | None
    ) -> AdjuntoGastoRead:
        """Fila lista para la pantalla, con enlace corto si hay almacenamiento."""
        url = None
        expira = None
        if cliente is not None:
            segundos = max(60, settings.R2_URL_VER_MINUTOS * 60)
            url = cliente.enlace_firmado(
                clave=adjunto.object_key,
                segundos=segundos,
                nombre_descarga=adjunto.nombre_archivo,
            )
            expira = caducidad_utc(segundos)
        return AdjuntoGastoRead(
            id=adjunto.id,
            gasto_id=adjunto.gasto_id,
            nombre_archivo=adjunto.nombre_archivo,
            content_type=adjunto.content_type,
            tamano_bytes=adjunto.tamano_bytes,
            es_imagen=adjunto.es_imagen,
            subido_por_nombre=adjunto.subido_por_nombre,
            created_at=adjunto.created_at,
            url=url,
            url_expira=expira,
        )

    # ------------------------------------------------------------------- ver
    def listar(self, gasto_id: uuid.UUID) -> AdjuntosGastoLista:
        """Las facturas del gasto, cada una con su enlace de CORTA duración.

        Sin R2 configurado responde 200 con `disponible: false` en vez de un error:
        no es culpa de quien pregunta y el resto de la pantalla tiene que poder
        seguir usándose. Las filas igual salen (nombre, peso, quién la subió), solo
        que sin enlace para abrirlas.
        """
        self._gasto(gasto_id)
        filas = self.repo.de_gasto(gasto_id)
        cupo = max(0, settings.ADJUNTOS_MAX_POR_DOCUMENTO - len(filas))
        if not r2_configurado():
            return AdjuntosGastoLista(
                disponible=False,
                mensaje=MENSAJE_NO_CONFIGURADO,
                cupo_restante=0,
                adjuntos=[self._a_read(f, None) for f in filas],
            )
        cliente = R2Client()
        return AdjuntosGastoLista(
            disponible=True,
            cupo_restante=cupo,
            adjuntos=[self._a_read(f, cliente) for f in filas],
        )

    # ----------------------------------------------------------------- subir
    def subir(self, archivos: list[Any], *, gasto_id: uuid.UUID) -> AdjuntosGastoLista:
        """Sube N facturas a un gasto.

        SE VALIDAN Y SE COMPRIMEN TODAS ANTES DE SUBIR NINGUNA. Si la tercera página
        no sirve, no tiene sentido que las dos primeras ya estén en el bucket: el
        dueño corrige y vuelve a mandar las tres, y las dos buenas quedarían
        duplicadas y tendría que borrarlas a mano.

        Y si R2 falla a mitad de camino, se borran los objetos que alcanzaron a
        subir. La excepción hace rollback de la sesión, así que las filas
        desaparecen; sin este barrido los archivos quedarían en el bucket sin
        ninguna fila que los nombre — invisibles, imborrables y cobrando.
        """
        self._gasto(gasto_id)
        if not archivos:
            raise BusinessError("No se recibió ningún archivo")
        if not r2_configurado():
            raise BusinessError(MENSAJE_NO_CONFIGURADO, code="r2_no_configurado")

        ya_tiene = self.repo.contar_de(gasto_id)
        tope = settings.ADJUNTOS_MAX_POR_DOCUMENTO
        if ya_tiene + len(archivos) > tope:
            raise BusinessError(
                f"Caben máximo {tope} facturas por gasto. Ya hay {ya_tiene} "
                f"y está mandando {len(archivos)}"
            )

        validados = [
            leer_y_validar_soporte(
                a, max_bytes=self._max_bytes, max_mb=settings.ADJUNTOS_MAX_MB
            )
            for a in archivos
        ]

        cliente = R2Client()
        subidas: list[str] = []
        quien = self._nombre_de_quien_sube()
        try:
            for contenido, tipo, extension, nombre in validados:
                clave = self._clave(gasto_id=gasto_id, extension=extension)
                cliente.subir(clave=clave, contenido=contenido, content_type=tipo)
                subidas.append(clave)
                adjunto = self.repo.create(
                    self._prepare_create_data(
                        {
                            "gasto_id": gasto_id,
                            "object_key": clave,
                            "nombre_archivo": nombre,
                            "content_type": tipo,
                            "tamano_bytes": len(contenido),
                            "subido_por_nombre": quien,
                        }
                    )
                )
                self._audit("crear", adjunto.id, None, serialize_entity(adjunto))
        except Exception:
            for clave in subidas:
                try:
                    cliente.borrar(clave)
                except Exception:  # pragma: no cover - barrido de mejor esfuerzo
                    logger_adjuntos.warning(
                        "Quedó un objeto huérfano en R2 tras una subida fallida: %s", clave
                    )
            raise

        return self.listar(gasto_id)

    # ------------------------------------------------------------- compartir
    def compartir(self, adjunto_id: uuid.UUID) -> EnlaceFacturaCompartida:
        """Enlace de MÁS duración para UNA factura, para mandarla por fuera.

        Es el caso de todos los meses: el contador pide las facturas del período y
        el dueño se las manda. Quince minutos —lo que dura el enlace de la
        pantalla— no alcanzan para eso.

        Queda en la auditoría con su caducidad: es un documento con el NIT del
        proveedor y el valor saliendo del sistema hacia un enlace que cualquiera
        que lo reciba puede reenviar. Que quede escrito quién lo repartió y hasta
        cuándo sirve.
        """
        adjunto = self._adjunto(adjunto_id)
        if not r2_configurado():
            raise BusinessError(MENSAJE_NO_CONFIGURADO, code="r2_no_configurado")

        dias = max(1, min(settings.R2_URL_COMPARTIR_DIAS, 7))
        segundos = dias * 24 * 60 * 60
        url = R2Client().enlace_firmado(
            clave=adjunto.object_key,
            segundos=segundos,
            nombre_descarga=adjunto.nombre_archivo,
        )
        expira = caducidad_utc(segundos)
        # Se audita el HECHO de compartir, nunca la URL: la URL lleva la firma
        # dentro, así que guardarla en la auditoría sería guardar el acceso.
        self._audit(
            "compartir",
            adjunto.id,
            None,
            {
                "nombre_archivo": adjunto.nombre_archivo,
                "gasto_id": str(adjunto.gasto_id),
                "expira": expira.isoformat(),
                "dias": dias,
            },
        )
        return EnlaceFacturaCompartida(
            url=url,
            nombre_archivo=adjunto.nombre_archivo,
            expira=expira,
            expira_texto=texto_caducidad(expira),
            dias=dias,
        )

    # ---------------------------------------------------------------- borrar
    def limpiar_de_gasto(self, gasto_id: uuid.UUID) -> int:
        """Se lleva las facturas cuando se borra el GASTO.

        EN R2 ES DE MEJOR ESFUERZO, al revés que en `eliminar_adjunto`. Allá el
        fallo tiene que detener la operación porque borrar la factura ES la
        operación; aquí la operación es borrar el gasto, y negarla porque el bucket
        no respondió sería peor: el dueño quedaría sin poder corregir un gasto mal
        registrado por un problema de red. Lo que no se pudo borrar queda en el log.

        PRIMERO LA BASE, EL BUCKET DE ÚLTIMO Y SOLO SI LA TRANSACCIÓN CUAJÓ, colgado
        del `after_commit` de la sesión. Es el mismo hueco de una sola dirección que
        ya está explicado en liquidaciones: el borrado de R2 no se deshace, y si
        algo revienta después —el flush, o el commit que ocurre afuera en `get_db`—
        la sesión hace rollback, las filas RESUCITAN con su `deleted_at` en nulo... y
        los archivos que nombran ya no existen. Quedaría una pantalla llena de
        facturas que no abren.
        """
        filas = self.repo.de_gasto(gasto_id)
        if not filas:
            return 0
        motivo = "borrar el gasto"
        claves = [fila.object_key for fila in filas]
        for fila in filas:
            antes = serialize_entity(fila)
            self.repo.soft_delete(fila, deleted_by=self.ctx.user_id)
            self._audit(
                "eliminar", fila.id, antes, serialize_entity(fila) | {"motivo": motivo}
            )
        borrar_del_bucket_al_confirmar(self.db, claves, motivo)
        return len(filas)

    def eliminar_adjunto(self, adjunto_id: uuid.UUID) -> None:
        """Borra la factura: PRIMERO la fila, y el objeto en R2 al confirmar.

        El orden importa y es el mismo que en `limpiar_de_gasto`: la fila de la base
        se puede resucitar con un rollback, el archivo del bucket no. Así que lo
        irreversible va de último.

        LO QUE SE CAMBIA A CAMBIO, dicho claro: si R2 falla DESPUÉS del commit, la
        fila ya no está y el archivo se queda ocupando espacio sin que nadie lo
        pueda ver. Es plata —poca— contra una factura que no se puede recuperar.
        """
        adjunto = self._adjunto(adjunto_id)
        antes = serialize_entity(adjunto)
        clave = adjunto.object_key
        self.repo.soft_delete(adjunto, deleted_by=self.ctx.user_id)
        self._audit("eliminar", adjunto.id, antes, serialize_entity(adjunto))
        borrar_del_bucket_al_confirmar(self.db, [clave], "borrar la factura")
