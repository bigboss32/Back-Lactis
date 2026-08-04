import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select

from app.common.service import BaseService
from app.core.exceptions import BusinessError, ConflictError
from app.core.pagination import PageParams
from app.modules.liquidaciones.models import (
    ESTADO_APROBADA,
    ESTADO_BORRADOR,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    TIPO_PROVEEDOR,
    Liquidacion,
)
from app.modules.liquidaciones.repository import LiquidacionRepository
from app.modules.proveedores.models import Proveedor
from app.modules.proveedores.repository import ProveedorRepository
from app.modules.proveedores.service import exigir_proveedor_activo
from app.modules.recepcion.models import RecepcionLeche
from app.modules.recepcion.repository import RecepcionRepository
from app.modules.recepcion.schemas import (
    CeldaGrilla,
    FilaGrilla,
    GrillaQuincena,
    ResumenDia,
    ResumenPeriodo,
)
from app.modules.transportadores.repository import TransportadorRepository

CERO = Decimal("0")
CENTAVOS = Decimal("0.01")


def _centavos(valor: Decimal) -> Decimal:
    """Redondea a centavos con el medio centavo PARA ARRIBA, como lo hace una persona.

    Hace falta porque los litros llevan dos decimales y la tarifa del
    transportador TAMBIÉN puede llevarlos (el dueño tiene un transportador a
    $242,76 por litro): 227,55 L × $242,76 da 55.239,978, o sea tres decimales
    que la columna Numeric(14,2) no guarda.

    Sin redondear aquí pasaban dos cosas, y las dos con plata:
      - la sesión no expira los objetos al hacer commit (expire_on_commit=False),
        así que al guardar se devolvía el 55.239,978 de memoria mientras en la
        base quedaba 55.239,98: la pantalla mostraba una cifra y al recargar
        salía otra;
      - la liquidación del transportador suma lo que está GUARDADO, así que el
        desglose por día no cuadraba contra el total que el dueño revisa a mano.

    Se usa ROUND_HALF_UP y no el ROUND_HALF_EVEN que Python trae por defecto
    porque así es como redondea Postgres al meter el valor en la columna: de esta
    forma lo que se devuelve y lo que queda guardado son el mismo número.

    Con las tarifas de pesos enteros que hay hoy esto NO mueve ni un peso: litros
    (2 decimales) por una tarifa entera da 2 decimales, y redondear a 2 decimales
    algo que ya tiene 2 decimales lo deja idéntico.
    """
    return valor.quantize(CENTAVOS, rounding=ROUND_HALF_UP)

# De más trabada a menos. Un día puede estar en DOS liquidaciones a la vez (la
# leche al proveedor y el flete al transportador): la que manda para el candado
# es la más trabada de las dos, porque basta con que UNA ya se haya pagado para
# que ese día no se pueda tocar.
#
# 'parcial' va arriba, junto a 'pagada': una liquidación a medio pagar TRABA el
# día igual que una pagada del todo. Si ya salió plata de la caja contra esas
# cifras, cambiar los litros deja el abono descuadrado.
_ORDEN_DE_CANDADO = (ESTADO_PAGADA, ESTADO_PARCIAL, ESTADO_APROBADA, ESTADO_BORRADOR)

# Los dos estados con los que un día queda trabado en la grilla y en el guardia.
_ESTADOS_CON_PAGO = (ESTADO_PAGADA, ESTADO_PARCIAL)


def _estado_que_manda(estados: list[str]) -> str | None:
    for estado in _ORDEN_DE_CANDADO:
        if estado in estados:
            return estado
    return None


class RecepcionService(BaseService[RecepcionLeche]):
    repository_cls = RecepcionRepository
    modulo = "recepcion"

    # ------------------------------------------- liquidaciones de una recepción
    def _liquidaciones_de(self, recepcion: RecepcionLeche) -> list[Liquidacion]:
        """Las liquidaciones que hoy tienen apartado este día: leche y/o flete.

        Va por el repositorio para no saltarse el filtro por empresa ni el de
        borrados: de esto depende si se deja o no tocar plata de un tenant.
        """
        ids = {
            liq_id
            for liq_id in (recepcion.liquidacion_id, recepcion.liquidacion_transporte_id)
            if liq_id is not None
        }
        if not ids:
            return []
        stmt = (
            LiquidacionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(Liquidacion.id.in_(ids))
        )
        return list(self.db.scalars(stmt).all())

    def _exigir_no_pagada(self, recepcion: RecepcionLeche, verbo: str) -> list[Liquidacion]:
        """El candado de verdad: se traba en cuanto SALIÓ PLATA por ese día.

        Antes se trababa apenas la recepción tuviera liquidación, sin mirar el
        estado, y el dueño se quedaba sin poder corregir un día desde que la
        generaba —aunque todavía no le hubiera pagado a nadie—. Ahora en borrador
        y en aprobada se deja corregir (la liquidación se recuadra sola, ver
        `_recuadrar`) y solo se dice que no cuando la plata ya salió.

        Con los pagos parciales, "ya salió plata" es TENER ALGÚN PAGO, no estar
        en 'pagada': si al proveedor se le abonó la mitad y después le cambian
        los litros, ese abono queda contra un total que ya no existe.

        Devuelve las liquidaciones tocadas, para recuadrarlas después de escribir.
        """
        liquidaciones = self._liquidaciones_de(recepcion)
        con_pago = [liq for liq in liquidaciones if liq.tiene_pagos or liq.estado == ESTADO_PAGADA]
        if con_pago:
            liq = con_pago[0]
            de_quien = "la leche" if liq.tipo == TIPO_PROVEEDOR else "el flete"
            # Dos mensajes porque son dos situaciones distintas para el usuario:
            # de la pagada no hay nada que hacer por dentro; del abono sí, se
            # puede borrar el pago, corregir el día y volver a abonar.
            if liq.estado == ESTADO_PAGADA:
                raise BusinessError(
                    f"No se puede {verbo} este día: {de_quien} ya se pagó en una "
                    "liquidación. Si la cifra está mala, corríjala por fuera del sistema "
                    "o registre el ajuste en la quincena siguiente"
                )
            raise BusinessError(
                f"No se puede {verbo} este día: {de_quien} ya tiene un pago "
                "registrado en una liquidación. Elimine primero ese pago si de verdad "
                "hay que corregir la cifra, o registre el ajuste en la quincena siguiente"
            )
        return liquidaciones

    def _marcas_a_soltar(
        self,
        actual: RecepcionLeche,
        liquidaciones: list[Liquidacion],
        data: dict[str, Any],
        nueva_fecha: date,
    ) -> dict[str, None]:
        """Cuándo un día deja de pertenecer a la liquidación que lo tenía apartado.

        Editar un día ya no está prohibido, pero hay dos cambios que lo sacan de
        su liquidación y que —si no se atienden— le pagarían a quien no era o
        meterían leche de otra quincena en un comprobante ya emitido:

        · Cambia el TRANSPORTADOR: el flete de ese día es de otra persona. Se
          suelta de la liquidación de flete vieja (que se recuadra sin él) y
          queda disponible para liquidárselo al que sí recogió.
        · La FECHA se sale del período de la liquidación: esa leche pertenece a
          otra quincena. Se suelta para que entre en la liquidación que le toca;
          si se quedara, el comprobante de junio traería un día de julio.

        Un cambio de fecha DENTRO del mismo período no suelta nada: es la
        corrección de todos los días y la liquidación simplemente se recuadra.
        """
        por_id = {liq.id: liq for liq in liquidaciones}
        nuevo_transportador = data.get("transportador_id", actual.transportador_id)
        soltar: dict[str, None] = {}

        def fuera_de_periodo(liq_id: uuid.UUID | None) -> bool:
            liq = por_id.get(liq_id) if liq_id else None
            return liq is not None and not (liq.periodo_inicio <= nueva_fecha <= liq.periodo_fin)

        if fuera_de_periodo(actual.liquidacion_id):
            soltar["liquidacion_id"] = None
        if actual.liquidacion_transporte_id is not None and (
            nuevo_transportador != actual.transportador_id
            or fuera_de_periodo(actual.liquidacion_transporte_id)
        ):
            soltar["liquidacion_transporte_id"] = None
        return soltar

    def _recuadrar(self, liquidaciones: list[Liquidacion]) -> None:
        """Vuelve a cuadrar las liquidaciones cuyo día se acaba de tocar.

        Se importa aquí adentro y no arriba a propósito: el servicio de
        liquidaciones ya usa el repositorio de recepciones, y con el import
        arriba las dos hojas quedarían amarradas en tiempo de carga.
        """
        if not liquidaciones:
            return
        from app.modules.liquidaciones.service import LiquidacionService

        servicio = LiquidacionService(self.db, self.ctx)
        for liquidacion in liquidaciones:
            servicio.recuadrar(liquidacion.id)

    def _marcar_estado_liquidacion(self, recepciones: list[RecepcionLeche]) -> None:
        """Le cuelga a cada recepción el estado de la liquidación que manda.

        No es una columna: se resuelve de un solo golpe para toda la lista (una
        consulta, no una por fila) y se expone en `RecepcionRead` para que la
        pantalla sepa cuándo poner el candado y cuándo avisar que al tocar el día
        se va a mover una liquidación ya generada.
        """
        ids = {
            liq_id
            for r in recepciones
            for liq_id in (r.liquidacion_id, r.liquidacion_transporte_id)
            if liq_id is not None
        }
        estados: dict[uuid.UUID, str] = {}
        if ids:
            stmt = (
                LiquidacionRepository(self.db, self.ctx.empresa_id)
                .base_query()
                .where(Liquidacion.id.in_(ids))
            )
            estados = {liq.id: liq.estado for liq in self.db.scalars(stmt).all()}
        for r in recepciones:
            propios = [
                estados[liq_id]
                for liq_id in (r.liquidacion_id, r.liquidacion_transporte_id)
                if liq_id in estados
            ]
            r.liquidacion_estado = _estado_que_manda(propios)

    def _completar_y_calcular(self, data: dict[str, Any], actual: RecepcionLeche | None = None) -> dict[str, Any]:
        """Completa precio/ruta desde el proveedor y calcula los valores monetarios."""
        proveedor_id = data.get("proveedor_id") or (actual.proveedor_id if actual else None)
        proveedor = ProveedorRepository(self.db, self.ctx.empresa_id).get_or_fail(proveedor_id)

        # Al proveedor inactivo no se le recibe leche nueva. Se revisa al crear y
        # también cuando una recepción existente se le quiere pasar a otro
        # proveedor que está inactivo.
        #
        # Lo que NO se bloquea es corregir una recepción que YA era de ese
        # proveedor: si se retiró a mitad de quincena, la última quincena todavía
        # hay que cuadrarla y liquidársela, y dejarla congelada obligaría a
        # reactivarlo solo para arreglarle un dato. Apartarlo es para que no
        # entre leche nueva, no para volverle la historia de solo lectura.
        if actual is None or proveedor_id != actual.proveedor_id:
            exigir_proveedor_activo(proveedor)

        if data.get("precio_litro") is None and actual is None:
            data["precio_litro"] = proveedor.precio_litro
        if data.get("ruta_id") is None and actual is None:
            data["ruta_id"] = proveedor.ruta_id

        litros = Decimal(data.get("cantidad_litros") or (actual.cantidad_litros if actual else CERO))
        precio = Decimal(
            data.get("precio_litro")
            if data.get("precio_litro") is not None
            else (actual.precio_litro if actual else CERO)
        )
        bonif = Decimal(
            data.get("bonificaciones")
            if data.get("bonificaciones") is not None
            else (actual.bonificaciones if actual else CERO)
        )
        desc = Decimal(
            data.get("descuentos")
            if data.get("descuentos") is not None
            else (actual.descuentos if actual else CERO)
        )

        transportador_id = data.get("transportador_id") or (actual.transportador_id if actual else None)
        tarifa = CERO
        if transportador_id:
            transportador = TransportadorRepository(self.db, self.ctx.empresa_id).get_or_fail(transportador_id)
            tarifa = Decimal(transportador.valor_transporte)

        # Se redondea a centavos ACÁ y no se deja que lo haga la columna, para que
        # la cifra que se devuelve sea exactamente la que queda guardada. Ver
        # _centavos: es lo que hace que el desglose de la liquidación sume el total.
        data["valor_bruto"] = _centavos(litros * precio)
        data["valor_transporte"] = _centavos(litros * tarifa)
        data["valor_neto"] = data["valor_bruto"] + bonif - desc
        if data["valor_neto"] < 0:
            raise BusinessError("El valor neto no puede ser negativo: revise los descuentos")
        return data

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.existe_registro_dia(data["proveedor_id"], data["fecha"]):
            raise ConflictError(
                "Ya existe una recepción de este proveedor en esa fecha. Edite el registro existente"
            )

    def crear(self, payload: Any) -> RecepcionLeche:
        data = payload.model_dump(exclude_unset=True)
        data = self._completar_y_calcular(data)
        recepcion = super().crear(data)
        self._marcar_estado_liquidacion([recepcion])
        return recepcion

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> RecepcionLeche:
        actual = self.repo.get_or_fail(entity_id)
        # El candado es "ya se pagó", no "ya se liquidó": ver `_exigir_no_pagada`.
        # Las liquidaciones se apuntan ANTES de escribir, porque después de
        # guardar hay que volver a cuadrarlas con la cifra nueva.
        liquidaciones = self._exigir_no_pagada(actual, "modificar")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        nueva_fecha = data.get("fecha", actual.fecha)
        if self.repo.existe_registro_dia(actual.proveedor_id, nueva_fecha, exclude_id=entity_id):
            raise ConflictError("Ya existe una recepción de este proveedor en esa fecha")
        data = self._completar_y_calcular(data, actual)
        data.update(self._marcas_a_soltar(actual, liquidaciones, data, nueva_fecha))

        recepcion = super().actualizar(entity_id, data)
        # Se baja el cambio antes de recuadrar: la sesión no hace autoflush y el
        # recálculo vuelve a leer las recepciones desde la base.
        self.db.flush()
        self._recuadrar(liquidaciones)
        self._marcar_estado_liquidacion([recepcion])
        return recepcion

    def validar_eliminar(self, obj: RecepcionLeche) -> None:
        self._exigir_no_pagada(obj, "eliminar")

    def eliminar(self, entity_id: uuid.UUID) -> None:
        """Borra el día y deja cuadradas las liquidaciones que lo tenían.

        Sin el recuadre, la liquidación se quedaría con el renglón de un día que
        ya no existe y su total dejaría de ser la suma de sus recepciones: el
        descuadre silencioso que el dueño detecta cuadrando a mano.
        """
        recepcion = self.repo.get_or_fail(entity_id)
        liquidaciones = self._exigir_no_pagada(recepcion, "eliminar")
        super().eliminar(entity_id)
        self.db.flush()
        self._recuadrar(liquidaciones)

    # ---------------------------------------------------------------- lecturas
    def obtener(self, entity_id: uuid.UUID) -> RecepcionLeche:
        recepcion = super().obtener(entity_id)
        self._marcar_estado_liquidacion([recepcion])
        return recepcion

    def listar(self, params: PageParams, **kwargs: Any) -> tuple[list[RecepcionLeche], int]:
        items, total = super().listar(params, **kwargs)
        self._marcar_estado_liquidacion(items)
        return items, total

    def listar_filtrado(
        self,
        params: PageParams,
        *,
        proveedor_id: uuid.UUID | None = None,
        ruta_id: uuid.UUID | None = None,
        transportador_id: uuid.UUID | None = None,
        desde: date | None = None,
        hasta: date | None = None,
        search: str | None = None,
    ) -> tuple[list[RecepcionLeche], int]:
        filters = {
            "proveedor_id": proveedor_id,
            "ruta_id": ruta_id,
            "transportador_id": transportador_id,
        }
        extra = self.repo.rango_criteria(desde, hasta)
        # Búsqueda por NOMBRE de proveedor: filtra por los proveedores de la
        # empresa cuyo nombre coincide, sin necesitar el id exacto.
        if search and search.strip():
            proveedores = select(Proveedor.id).where(
                Proveedor.empresa_id == self.ctx.empresa_id,
                Proveedor.nombre.ilike(f"%{search.strip()}%"),
            )
            extra.append(RecepcionLeche.proveedor_id.in_(proveedores))
        items, total = self.repo.list_paginated(params, filters=filters, extra_criteria=extra)
        self._marcar_estado_liquidacion(items)
        return items, total

    def grilla_quincena(
        self,
        desde: date,
        hasta: date,
        *,
        search: str | None = None,
        ruta_id: uuid.UUID | None = None,
        transportador_id: uuid.UUID | None = None,
    ) -> GrillaQuincena:
        """Grilla proveedores × días como la hoja 'LITROS Y TRANSPORTE' del Excel.

        Incluye todos los proveedores activos (aunque no tengan recepciones)
        para que la grilla sirva también como superficie de registro diario.
        Se puede filtrar por nombre de proveedor (search), por ruta (ruta_id) y
        por transportador (transportador_id).

        Los tres filtros SE COMBINAN (se aplican todos a la vez, no se pisan),
        pero no trabajan al mismo nivel, y la diferencia importa:

        - search y ruta_id son filtros DE FILA: escogen proveedores (el de la
          ruta es la ruta del PROVEEDOR, no la de cada recepción) y de los
          elegidos se muestran todos sus días.
        - transportador_id es un filtro DE CELDA: el transportador se guarda en
          CADA recepción (columna transportador_id de recepciones_leche), así
          que un mismo proveedor puede haber sido recogido por Stella el lunes y
          por Efraín el martes. Filtrar por Stella deja solo los días de Stella,
          y los totales de la fila, del día y del pie se recalculan sobre esas
          celdas: lo que suma la pantalla es exactamente lo que se ve en ella.
        """
        if hasta < desde:
            raise BusinessError("El fin del período no puede ser anterior al inicio")
        if (hasta - desde).days > 31:
            raise BusinessError("El período máximo de la grilla es de 31 días")

        fechas: list[date] = []
        d = desde
        while d <= hasta:
            fechas.append(d)
            d += timedelta(days=1)

        # El filtro de transportador se aplica EN LA CONSULTA (base_query ya trae
        # empresa_id y deleted_at IS NULL): la quincena de una quesera con muchos
        # proveedores son cientos de filas y recortarlas después —o peor, en el
        # navegador— sería un filtro de mentiras.
        consulta = self.repo.base_query().where(
            RecepcionLeche.fecha >= desde,
            RecepcionLeche.fecha <= hasta,
            RecepcionLeche.estado == "activo",
        )
        if transportador_id is not None:
            consulta = consulta.where(RecepcionLeche.transportador_id == transportador_id)
        recepciones = list(self.db.scalars(consulta).all())
        # De un solo golpe para toda la quincena: cada celda necesita saber si su
        # día está trabado (liquidación pagada) o solo apartado en una liquidación
        # sin pagar, que se puede editar pero avisando.
        self._marcar_estado_liquidacion(recepciones)

        activos = ProveedorRepository(self.db, self.ctx.empresa_id).all(estado="activo")
        activos_ids = {p.id for p in activos}
        # Sin filtro de transportador la grilla es TAMBIÉN la libreta de registro
        # diario, por eso arranca con todos los proveedores activos aunque no
        # tengan nada anotado: las celdas vacías son donde se anota.
        #
        # Con filtro de transportador, no. La pregunta ahí es "¿qué recogió
        # Stella esta quincena?", y contestarla con treinta filas en blanco de
        # proveedores a los que Stella nunca les recogió no contesta nada:
        # esconde las pocas filas que sí importan. Quedan solo los proveedores
        # con leche recogida por él (y por eso tampoco se anota leche nueva con
        # el filtro puesto: para eso se quita y la grilla vuelve a estar completa).
        proveedores: dict = {} if transportador_id is not None else {p.id: p for p in activos}
        # Proveedores retirados/eliminados pero con recepciones en el rango también
        # se muestran (marcados como inactivos) para poder liquidarlos.
        for r in recepciones:
            if r.proveedor_id not in proveedores and r.proveedor:
                proveedores[r.proveedor_id] = r.proveedor

        # Filtros opcionales: por ruta y por nombre de proveedor
        if ruta_id is not None:
            proveedores = {pid: p for pid, p in proveedores.items() if p.ruta_id == ruta_id}
        if search and search.strip():
            texto = search.strip().lower()
            proveedores = {
                pid: p for pid, p in proveedores.items() if texto in (p.nombre or "").lower()
            }

        filas_map: dict = {
            pid: FilaGrilla(
                proveedor_id=pid,
                proveedor_nombre=p.nombre,
                vereda=p.vereda,
                precio_litro=p.precio_litro,
                proveedor_activo=pid in activos_ids,
                celdas={},
                total_litros=CERO,
                valor_bruto=CERO,
                descuentos=CERO,
                bonificaciones=CERO,
                valor_neto=CERO,
                valor_transporte=CERO,
            )
            for pid, p in proveedores.items()
        }

        totales_dia: dict[str, Decimal] = {f.isoformat(): CERO for f in fechas}
        for r in recepciones:
            fila = filas_map.get(r.proveedor_id)
            if fila is None:  # proveedor excluido por el filtro
                continue
            clave = r.fecha.isoformat()
            fila.celdas[clave] = CeldaGrilla(
                recepcion_id=r.id,
                litros=r.cantidad_litros,
                # "liquidada" es apenas "ya está dentro de una liquidación
                # generada" (la de la leche o la del flete): es una seña, no un
                # candado. El candado es "ya tiene pagos": pagada del todo o
                # parcial, porque en las dos ya salió plata contra ese día.
                liquidada=r.liquidacion_estado is not None,
                pagada=r.liquidacion_estado in _ESTADOS_CON_PAGO,
                liquidacion_estado=r.liquidacion_estado,
                con_transporte=r.transportador_id is not None,
            )
            fila.total_litros += r.cantidad_litros
            fila.valor_bruto += r.valor_bruto
            fila.descuentos += r.descuentos
            fila.bonificaciones += r.bonificaciones
            fila.valor_neto += r.valor_neto
            fila.valor_transporte += r.valor_transporte
            totales_dia[clave] += r.cantidad_litros

        filas = sorted(filas_map.values(), key=lambda f: (f.vereda or "", f.proveedor_nombre))
        return GrillaQuincena(
            desde=desde,
            hasta=hasta,
            fechas=fechas,
            filas=filas,
            totales_dia=totales_dia,
            total_litros=sum((f.total_litros for f in filas), CERO),
            total_valor_neto=sum((f.valor_neto for f in filas), CERO),
            total_transporte=sum((f.valor_transporte for f in filas), CERO),
        )

    def resumen_periodo(self, desde: date, hasta: date) -> ResumenPeriodo:
        filas = self.repo.resumen_por_dia(desde, hasta)
        dias = [
            ResumenDia(
                fecha=f.fecha,
                total_litros=f.total_litros or CERO,
                valor_bruto=f.valor_bruto or CERO,
                valor_transporte=f.valor_transporte or CERO,
                valor_neto=f.valor_neto or CERO,
                recepciones=f.recepciones,
            )
            for f in filas
        ]
        total_litros = sum((d.total_litros for d in dias), CERO)
        valor_bruto = sum((d.valor_bruto for d in dias), CERO)
        return ResumenPeriodo(
            desde=desde,
            hasta=hasta,
            total_litros=total_litros,
            valor_bruto=valor_bruto,
            valor_transporte=sum((d.valor_transporte for d in dias), CERO),
            valor_neto=sum((d.valor_neto for d in dias), CERO),
            precio_promedio=(valor_bruto / total_litros).quantize(Decimal("0.01")) if total_litros else CERO,
            dias=dias,
        )
