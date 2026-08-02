"""Liquidaciones por quincena: agrupa las recepciones no liquidadas del período,
calcula totales, descuenta anticipos y genera el comprobante (PDF/Excel),
replicando el proceso que la quesera llevaba en Excel.
"""
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, NotFoundError
from app.core.pagination import PageParams
from app.modules.empresas.repository import EmpresaRepository
from app.modules.liquidaciones.models import (
    ESTADO_ANULADA,
    ESTADO_APROBADA,
    ESTADO_BORRADOR,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    TIPO_PROVEEDOR,
    TIPO_TRANSPORTADOR,
    Anticipo,
    Liquidacion,
    LiquidacionDetalle,
    PagoLiquidacion,
)
from app.modules.liquidaciones.repository import AnticipoRepository, LiquidacionRepository
from app.modules.liquidaciones.schemas import (
    PagoLiquidacionCreate,
    PreLiquidacionAnticipo,
    PreLiquidacionDetalle,
    PreLiquidacionRead,
)
from app.modules.proveedores.repository import ProveedorRepository
from app.modules.recepcion.models import RecepcionLeche
from app.modules.recepcion.repository import RecepcionRepository
from app.modules.transportadores.repository import TransportadorRepository
from app.utils.export import build_liquidacion_pdf, litros, pesos

CERO = Decimal("0")
CENTAVOS = Decimal("0.01")


def _centavos(valor: Decimal) -> Decimal:
    """Redondea a centavos como lo haría una persona: el medio centavo sube.

    Se usa al recalcular un día corregido a mano. El resto de las cifras de la
    liquidación NO se re-redondean: se suman tal como están guardadas, para que
    el total sea exactamente la suma de los renglones que el dueño ve.
    """
    return Decimal(valor).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def _estado_pago(neto_a_pagar: Decimal, pagado: Decimal) -> str:
    """Estado de una liquidación en firme, deducido SIEMPRE de sus cifras.

    Misma idea (y mismo nombre) que en reventa: el estado no se escribe a mano en
    cada camino, se vuelve a calcular desde la plata. Así no puede quedar una
    liquidación marcada "pagada" que todavía deba, ni una "parcial" sin deber
    nada, que es como aparecen los descuadres que el dueño encuentra a mano.

    Sin pagos vuelve a APROBADA: es el estado del que salió (en borrador no se
    puede pagar), y es lo que tiene que quedar cuando se borra el último pago.
    """
    if pagado <= CERO:
        return ESTADO_APROBADA
    return ESTADO_PAGADA if pagado >= neto_a_pagar else ESTADO_PARCIAL


def _refrescar_saldo(liquidacion: Liquidacion) -> None:
    """Deja el saldo al día: lo que falta por pagar.

    Un solo sitio calcula esta resta para que la igualdad que el dueño verifica a
    mano —neto a pagar = pagado + saldo— no dependa de acordarse de repetirla
    bien en los cinco caminos que recalculan una liquidación.
    """
    liquidacion.saldo = liquidacion.neto_a_pagar - Decimal(liquidacion.pagado or 0)


def _bloquear(db: Session, liquidacion: Liquidacion) -> Liquidacion:
    """Relee la liquidación con FOR UPDATE antes de tocarle la plata.

    Sin esto, dos pagos a la vez sobre la misma liquidación se pisan: los dos
    leen el `pagado` viejo, los dos validan contra el mismo saldo y el segundo
    escribe encima del primero. Se pierde un pago —el proveedor reclama y en el
    sistema no está— y la cuenta deja de cuadrar.

    Tres detalles que ya costaron caro en reventa y que aquí también aplican:

    - `populate_existing`: sin él, el FOR UPDATE bloquea la fila en la base pero
      SQLAlchemy devuelve el objeto que ya tenía en memoria, CON LOS VALORES
      VIEJOS. Y es peor de lo que suena: quien llega segundo espera el candado
      justo mientras el primero escribe, así que al soltarse tiene en la mano
      exactamente los datos de antes.
    - `pagos` es lazy="selectin", no "joined": con un LEFT JOIN de por medio
      Postgres rechaza el FOR UPDATE con 0A000.
    - Y por eso mismo hay que apagar aquí el eager load de `proveedor` y
      `transportador`, que SÍ son lazy="joined" sobre FK anulables: sin
      `lazyload` esta consulta saldría con dos LEFT JOIN y Postgres la rechazaría
      con ese mismo 0A000. Quedan como carga diferida, así que el nombre del
      tercero se sigue leyendo igual cuando la respuesta lo pide.

    SQLite descarta el FOR UPDATE en silencio, así que la suite no delata nada de
    esto. La corrección se sostiene por lectura del código, no por la prueba.
    """
    return db.execute(
        select(Liquidacion)
        .where(Liquidacion.id == liquidacion.id)
        .options(lazyload(Liquidacion.proveedor), lazyload(Liquidacion.transportador))
        .execution_options(populate_existing=True)
        .with_for_update()
    ).scalar_one()


class LiquidacionService(BaseService[Liquidacion]):
    repository_cls = LiquidacionRepository
    modulo = "liquidaciones"

    # ------------------------------------------------------------- generación
    def generar(
        self,
        periodo_inicio: date,
        periodo_fin: date,
        tipo: str = "ambos",
        proveedor_id: uuid.UUID | None = None,
    ) -> list[Liquidacion]:
        if periodo_fin < periodo_inicio:
            raise BusinessError("El fin del período no puede ser anterior al inicio")
        recepciones_repo = RecepcionRepository(self.db, self.ctx.empresa_id)
        generadas: list[Liquidacion] = []

        if tipo in ("proveedor", "ambos"):
            generadas.extend(
                self._generar_proveedores(recepciones_repo, periodo_inicio, periodo_fin, proveedor_id)
            )
        if tipo in ("transportador", "ambos"):
            generadas.extend(
                self._generar_transportadores(recepciones_repo, periodo_inicio, periodo_fin)
            )
        return generadas

    def _generar_proveedores(
        self,
        recepciones_repo: RecepcionRepository,
        inicio: date,
        fin: date,
        proveedor_id: uuid.UUID | None,
    ) -> list[Liquidacion]:
        pendientes = recepciones_repo.sin_liquidar(inicio, fin, proveedor_id)
        por_proveedor: dict[uuid.UUID, list[RecepcionLeche]] = defaultdict(list)
        for r in pendientes:
            por_proveedor[r.proveedor_id].append(r)

        anticipos_repo = AnticipoRepository(self.db, self.ctx.empresa_id)
        generadas = []
        for prov_id, recepciones in por_proveedor.items():
            total_litros = sum((r.cantidad_litros for r in recepciones), CERO)
            valor_bruto = sum((r.valor_bruto for r in recepciones), CERO)
            bonificaciones = sum((r.bonificaciones for r in recepciones), CERO)
            descuentos = sum((r.descuentos for r in recepciones), CERO)
            valor_total = valor_bruto + bonificaciones - descuentos

            anticipos = anticipos_repo.pendientes_de(prov_id, fin)
            total_anticipos = sum((a.valor for a in anticipos), CERO)

            liquidacion = Liquidacion(
                empresa_id=self.ctx.empresa_id,
                tipo=TIPO_PROVEEDOR,
                proveedor_id=prov_id,
                periodo_inicio=inicio,
                periodo_fin=fin,
                total_litros=total_litros,
                precio_promedio=(valor_bruto / total_litros).quantize(Decimal("0.01"))
                if total_litros
                else CERO,
                valor_bruto=valor_bruto,
                bonificaciones=bonificaciones,
                descuentos=descuentos,
                valor_transporte=sum((r.valor_transporte for r in recepciones), CERO),
                anticipos=total_anticipos,
                valor_total=valor_total,
                saldo=valor_total - total_anticipos,
                estado=ESTADO_BORRADOR,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            liquidacion.detalles = [
                LiquidacionDetalle(
                    fecha=r.fecha, litros=r.cantidad_litros, precio_litro=r.precio_litro, valor=r.valor_neto
                )
                for r in sorted(recepciones, key=lambda x: x.fecha)
            ]
            self.db.add(liquidacion)
            self.db.flush()
            for r in recepciones:
                r.liquidacion_id = liquidacion.id
            for a in anticipos:
                a.liquidacion_id = liquidacion.id
            self.db.flush()
            self._audit("crear", liquidacion.id, None, serialize_entity(liquidacion))
            generadas.append(liquidacion)
        return generadas

    def _generar_transportadores(
        self, recepciones_repo: RecepcionRepository, inicio: date, fin: date
    ) -> list[Liquidacion]:
        stmt = recepciones_repo.base_query().where(
            RecepcionLeche.fecha >= inicio,
            RecepcionLeche.fecha <= fin,
            RecepcionLeche.liquidacion_transporte_id.is_(None),
            RecepcionLeche.transportador_id.is_not(None),
            RecepcionLeche.estado == "activo",
        )
        pendientes = list(self.db.scalars(stmt).all())
        por_transportador: dict[uuid.UUID, list[RecepcionLeche]] = defaultdict(list)
        for r in pendientes:
            por_transportador[r.transportador_id].append(r)

        anticipos_repo = AnticipoRepository(self.db, self.ctx.empresa_id)
        generadas = []
        for trans_id, recepciones in por_transportador.items():
            total_litros = sum((r.cantidad_litros for r in recepciones), CERO)
            valor_transporte = sum((r.valor_transporte for r in recepciones), CERO)
            if valor_transporte == CERO:
                continue
            anticipos = anticipos_repo.pendientes_transportador(trans_id, fin)
            total_anticipos = sum((a.valor for a in anticipos), CERO)
            # El transportador cobra por día la suma de litros de su ruta
            por_dia: dict[date, list[RecepcionLeche]] = defaultdict(list)
            for r in recepciones:
                por_dia[r.fecha].append(r)

            liquidacion = Liquidacion(
                empresa_id=self.ctx.empresa_id,
                tipo=TIPO_TRANSPORTADOR,
                transportador_id=trans_id,
                periodo_inicio=inicio,
                periodo_fin=fin,
                total_litros=total_litros,
                precio_promedio=(valor_transporte / total_litros).quantize(Decimal("0.01"))
                if total_litros
                else CERO,
                valor_transporte=valor_transporte,
                anticipos=total_anticipos,
                valor_total=valor_transporte,
                saldo=valor_transporte - total_anticipos,
                estado=ESTADO_BORRADOR,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            liquidacion.detalles = [
                LiquidacionDetalle(
                    fecha=fecha,
                    litros=sum((r.cantidad_litros for r in rs), CERO),
                    precio_litro=rs[0].transportador.valor_transporte if rs[0].transportador else CERO,
                    valor=sum((r.valor_transporte for r in rs), CERO),
                )
                for fecha, rs in sorted(por_dia.items())
            ]
            self.db.add(liquidacion)
            self.db.flush()
            for r in recepciones:
                r.liquidacion_transporte_id = liquidacion.id
            for a in anticipos:
                a.liquidacion_id = liquidacion.id
            self.db.flush()
            self._audit("crear", liquidacion.id, None, serialize_entity(liquidacion))
            generadas.append(liquidacion)
        return generadas

    # -------------------------------------------------------- previsualización
    def previsualizar(
        self, inicio: date, fin: date, tipo: str, tercero_id: uuid.UUID
    ) -> list[PreLiquidacionRead]:
        """Calcula cómo va un tercero en el período SIN generar ni guardar nada.

        Sirve para mostrarle a un proveedor/transportador su avance ("¿cómo voy?")
        antes de la liquidación oficial. No toca recepciones ni anticipos.
        """
        if fin < inicio:
            raise BusinessError("El fin del período no puede ser anterior al inicio")
        recepciones_repo = RecepcionRepository(self.db, self.ctx.empresa_id)
        if tipo == TIPO_PROVEEDOR:
            pre = self._preview_proveedor(recepciones_repo, tercero_id, inicio, fin)
        elif tipo == TIPO_TRANSPORTADOR:
            pre = self._preview_transportador(recepciones_repo, tercero_id, inicio, fin)
        else:
            raise BusinessError("Tipo inválido para pre-liquidación")
        return [pre] if pre else []

    def _preview_proveedor(
        self, recepciones_repo: RecepcionRepository, prov_id: uuid.UUID, inicio: date, fin: date
    ) -> PreLiquidacionRead | None:
        recepciones = recepciones_repo.sin_liquidar(inicio, fin, prov_id)
        if not recepciones:
            return None
        total_litros = sum((r.cantidad_litros for r in recepciones), CERO)
        valor_bruto = sum((r.valor_bruto for r in recepciones), CERO)
        bonificaciones = sum((r.bonificaciones for r in recepciones), CERO)
        descuentos = sum((r.descuentos for r in recepciones), CERO)
        valor_transporte = sum((r.valor_transporte for r in recepciones), CERO)
        valor_total = valor_bruto + bonificaciones - descuentos
        anticipos = AnticipoRepository(self.db, self.ctx.empresa_id).pendientes_de(prov_id, fin)
        total_anticipos = sum((a.valor for a in anticipos), CERO)
        proveedor = ProveedorRepository(self.db, self.ctx.empresa_id).get(prov_id)
        return PreLiquidacionRead(
            tipo=TIPO_PROVEEDOR,
            tercero_id=prov_id,
            tercero_nombre=proveedor.nombre if proveedor else "-",
            tercero_detalle=getattr(proveedor, "vereda", None) if proveedor else None,
            periodo_inicio=inicio,
            periodo_fin=fin,
            total_litros=total_litros,
            precio_promedio=(valor_bruto / total_litros).quantize(Decimal("0.01"))
            if total_litros
            else CERO,
            valor_bruto=valor_bruto,
            bonificaciones=bonificaciones,
            descuentos=descuentos,
            valor_transporte=valor_transporte,
            anticipos=total_anticipos,
            valor_total=valor_total,
            saldo=valor_total - total_anticipos,
            detalles=[
                PreLiquidacionDetalle(
                    fecha=r.fecha, litros=r.cantidad_litros, precio_litro=r.precio_litro, valor=r.valor_neto
                )
                for r in sorted(recepciones, key=lambda x: x.fecha)
            ],
            anticipos_detalle=[
                PreLiquidacionAnticipo(fecha=a.fecha, valor=a.valor, observaciones=a.observaciones)
                for a in anticipos
            ],
        )

    def _preview_transportador(
        self, recepciones_repo: RecepcionRepository, trans_id: uuid.UUID, inicio: date, fin: date
    ) -> PreLiquidacionRead | None:
        stmt = recepciones_repo.base_query().where(
            RecepcionLeche.fecha >= inicio,
            RecepcionLeche.fecha <= fin,
            RecepcionLeche.liquidacion_transporte_id.is_(None),
            RecepcionLeche.transportador_id == trans_id,
            RecepcionLeche.estado == "activo",
        )
        recepciones = list(self.db.scalars(stmt).all())
        if not recepciones:
            return None
        total_litros = sum((r.cantidad_litros for r in recepciones), CERO)
        valor_transporte = sum((r.valor_transporte for r in recepciones), CERO)
        anticipos = AnticipoRepository(self.db, self.ctx.empresa_id).pendientes_transportador(
            trans_id, fin
        )
        total_anticipos = sum((a.valor for a in anticipos), CERO)
        transportador = TransportadorRepository(self.db, self.ctx.empresa_id).get(trans_id)
        por_dia: dict[date, list[RecepcionLeche]] = defaultdict(list)
        for r in recepciones:
            por_dia[r.fecha].append(r)
        return PreLiquidacionRead(
            tipo=TIPO_TRANSPORTADOR,
            tercero_id=trans_id,
            tercero_nombre=transportador.nombre if transportador else "-",
            periodo_inicio=inicio,
            periodo_fin=fin,
            total_litros=total_litros,
            precio_promedio=(valor_transporte / total_litros).quantize(Decimal("0.01"))
            if total_litros
            else CERO,
            valor_bruto=CERO,
            bonificaciones=CERO,
            descuentos=CERO,
            valor_transporte=valor_transporte,
            anticipos=total_anticipos,
            valor_total=valor_transporte,
            saldo=valor_transporte - total_anticipos,
            detalles=[
                PreLiquidacionDetalle(
                    fecha=fecha,
                    litros=sum((r.cantidad_litros for r in rs), CERO),
                    precio_litro=rs[0].transportador.valor_transporte if rs[0].transportador else CERO,
                    valor=sum((r.valor_transporte for r in rs), CERO),
                )
                for fecha, rs in sorted(por_dia.items())
            ],
            anticipos_detalle=[
                PreLiquidacionAnticipo(fecha=a.fecha, valor=a.valor, observaciones=a.observaciones)
                for a in anticipos
            ],
        )

    # --------------------------------------------- corrección de un día suelto
    def _recepciones_de(self, liquidacion: Liquidacion) -> list[RecepcionLeche]:
        """Las recepciones que esta liquidación tiene apartadas.

        Va por el repositorio para no saltarse el filtro por empresa ni el de
        borrados: son datos de plata de un tenant y aquí se reescriben.
        """
        stmt = (
            RecepcionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(RecepcionLeche.liquidacion_id == liquidacion.id)
            .order_by(RecepcionLeche.fecha)
        )
        return list(self.db.scalars(stmt).all())

    def _renglon_del_dia(self, liquidacion: Liquidacion, fecha: date) -> LiquidacionDetalle:
        """El renglón de ese día, creándolo si la liquidación todavía no lo tiene.

        Hace falta porque los renglones ya no son fijos: desde que se puede
        corregir un día que pertenece a una liquidación sin pagar, un día puede
        aparecer (una recepción que cambió de fecha) o desaparecer (una recepción
        borrada). Ver `_quitar_renglones_sin_dia`.
        """
        for detalle in liquidacion.detalles:
            if detalle.fecha == fecha:
                return detalle
        detalle = LiquidacionDetalle(
            fecha=fecha, created_by=self.ctx.user_id, updated_by=self.ctx.user_id
        )
        liquidacion.detalles.append(detalle)
        return detalle

    def _quitar_renglones_sin_dia(self, liquidacion: Liquidacion, fechas: set[date]) -> None:
        """Bota los renglones de días que ya no tienen recepción detrás.

        Si se quedaran, la columna Valor del comprobante dejaría de sumar el
        VALOR TOTAL —que se calcula desde las recepciones que quedan—, y ese
        cuadre es justo el que el dueño verifica a mano contra el cuaderno.
        Con cascade delete-orphan, sacarlo de la colección lo borra.
        """
        for sobrante in [d for d in liquidacion.detalles if d.fecha not in fechas]:
            liquidacion.detalles.remove(sobrante)

    def _recalcular_desde_recepciones(self, liquidacion: Liquidacion) -> None:
        """Rearma el detalle y los totales desde las recepciones del período.

        Se recalcula TODO en vez de "ajustar la diferencia" porque el dueño suma
        la columna Valor a mano y compara con el total: si el total se arrastrara
        de un cálculo anterior, un peso de diferencia lo mandaría a buscar un
        error que no existe.

        La clave del cuadre está en que el valor del día se arma con las MISMAS
        piezas que se suman arriba (bruto + bonificaciones - descuentos) y no
        leyendo `valor_neto`: así la suma de los días es idéntica al valor total,
        sin depender de cómo redondeó cada quien.
        """
        recepciones = self._recepciones_de(liquidacion)
        por_fecha = {r.fecha: r for r in recepciones}

        self._quitar_renglones_sin_dia(liquidacion, set(por_fecha))
        for fecha, recepcion in por_fecha.items():
            detalle = self._renglon_del_dia(liquidacion, fecha)
            detalle.litros = recepcion.cantidad_litros
            detalle.precio_litro = recepcion.precio_litro
            detalle.valor = (
                Decimal(recepcion.valor_bruto)
                + Decimal(recepcion.bonificaciones)
                - Decimal(recepcion.descuentos)
            )
            detalle.updated_by = self.ctx.user_id
        # El comprobante se lee de arriba abajo por fecha; los renglones nuevos se
        # agregaron al final de la colección, así que se vuelve a ordenar.
        liquidacion.detalles.sort(key=lambda d: d.fecha)

        total_litros = sum((Decimal(r.cantidad_litros) for r in recepciones), CERO)
        valor_bruto = sum((Decimal(r.valor_bruto) for r in recepciones), CERO)
        bonificaciones = sum((Decimal(r.bonificaciones) for r in recepciones), CERO)
        descuentos = sum((Decimal(r.descuentos) for r in recepciones), CERO)
        valor_total = valor_bruto + bonificaciones - descuentos

        liquidacion.total_litros = total_litros
        liquidacion.valor_bruto = valor_bruto
        liquidacion.bonificaciones = bonificaciones
        liquidacion.descuentos = descuentos
        liquidacion.valor_transporte = sum((Decimal(r.valor_transporte) for r in recepciones), CERO)
        liquidacion.precio_promedio = (
            (valor_bruto / total_litros).quantize(CENTAVOS) if total_litros else CERO
        )
        liquidacion.valor_total = valor_total
        # Los anticipos no se tocan: ya quedaron aplicados a esta liquidación al
        # generarla y corregir un precio no cambia lo que se le adelantó.
        _refrescar_saldo(liquidacion)

    def _recepciones_transporte_de(self, liquidacion: Liquidacion) -> list[RecepcionLeche]:
        """Las recepciones cuyo FLETE tiene apartado esta liquidación.

        Ojo con la marca: la de leche va en `liquidacion_id` y la de flete en
        `liquidacion_transporte_id`. Un mismo día lleva las dos y son
        liquidaciones distintas, de dos personas distintas.
        """
        stmt = (
            RecepcionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(RecepcionLeche.liquidacion_transporte_id == liquidacion.id)
            .order_by(RecepcionLeche.fecha)
        )
        return list(self.db.scalars(stmt).all())

    def _recalcular_transporte_desde_recepciones(self, liquidacion: Liquidacion) -> None:
        """Rearma la liquidación de flete desde las recepciones que recogió.

        Existe por la misma razón que la de proveedor: si se corrigen los litros
        de un día, el flete de ese día también cambia (se cobra por litro
        recogido) y el comprobante del transportador quedaría diciendo una cifra
        que ya no corresponde a sus recepciones.

        Aquí el renglón es POR DÍA y agrupa toda la ruta —el transportador cobra
        la suma de litros que recogió ese día, no proveedor por proveedor—, tal
        como se arma al generarla en `_generar_transportadores`.
        """
        recepciones = self._recepciones_transporte_de(liquidacion)
        por_dia: dict[date, list[RecepcionLeche]] = defaultdict(list)
        for r in recepciones:
            por_dia[r.fecha].append(r)

        self._quitar_renglones_sin_dia(liquidacion, set(por_dia))
        for fecha, del_dia in por_dia.items():
            detalle = self._renglon_del_dia(liquidacion, fecha)
            detalle.litros = sum((Decimal(r.cantidad_litros) for r in del_dia), CERO)
            # La tarifa del día se toma del transportador de esas recepciones; si
            # quedó sin transportador no hay tarifa que mostrar (y su valor es 0).
            detalle.precio_litro = (
                Decimal(del_dia[0].transportador.valor_transporte)
                if del_dia[0].transportador
                else CERO
            )
            detalle.valor = sum((Decimal(r.valor_transporte) for r in del_dia), CERO)
            detalle.updated_by = self.ctx.user_id
        liquidacion.detalles.sort(key=lambda d: d.fecha)

        total_litros = sum((Decimal(r.cantidad_litros) for r in recepciones), CERO)
        valor_transporte = sum((Decimal(r.valor_transporte) for r in recepciones), CERO)
        liquidacion.total_litros = total_litros
        liquidacion.valor_transporte = valor_transporte
        liquidacion.precio_promedio = (
            (valor_transporte / total_litros).quantize(CENTAVOS) if total_litros else CERO
        )
        liquidacion.valor_total = valor_transporte
        _refrescar_saldo(liquidacion)

    def actualizar_precio_detalle(
        self, entity_id: uuid.UUID, detalle_id: uuid.UUID, precio_litro: Decimal
    ) -> Liquidacion:
        """Corrige el precio por litro de un día sin salir del comprobante.

        Nace de un caso real: la liquidación salió a $1.800 el litro cuando no
        era ese el precio, y arreglarlo obligaba a anular la liquidación entera.

        SOLO en borrador y SOLO de proveedor:
        - Aprobada o anulada no se toca ni por la dirección del endpoint: ese
          precio ya se le pagó a alguien (o la liquidación ya se dio de baja).
        - En la de transportador el "precio" del renglón es la tarifa de flete
          del día y agrupa varias recepciones de la ruta; cambiarla ahí sería
          otra cosa, y se cruzaría con el transporte que ya lleva la liquidación
          del proveedor. Se deja por fuera a propósito.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado != ESTADO_BORRADOR:
            raise BusinessError(
                f"Esta liquidación está en '{liquidacion.estado}': solo se puede "
                "corregir el precio mientras sea un borrador"
            )
        if liquidacion.tipo != TIPO_PROVEEDOR:
            raise BusinessError(
                "Solo se puede corregir el precio por litro en liquidaciones de proveedor"
            )

        detalle = next(
            (d for d in liquidacion.detalles if d.id == detalle_id and d.deleted_at is None), None
        )
        if detalle is None:
            raise NotFoundError("Ese día no pertenece a la liquidación")

        recepcion = self.db.scalars(
            RecepcionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(
                RecepcionLeche.liquidacion_id == liquidacion.id,
                RecepcionLeche.proveedor_id == liquidacion.proveedor_id,
                RecepcionLeche.fecha == detalle.fecha,
            )
        ).first()
        if recepcion is None:
            raise BusinessError(
                "No se encontró la recepción de ese día; anule la liquidación y vuelva a generarla"
            )

        precio = _centavos(precio_litro)
        nuevo_bruto = _centavos(Decimal(recepcion.cantidad_litros) * precio)
        nuevo_neto = nuevo_bruto + Decimal(recepcion.bonificaciones) - Decimal(recepcion.descuentos)
        # Se valida ANTES de escribir nada: si el precio nuevo deja el día en rojo
        # la corrección no debe dejar a medias ni la recepción ni la liquidación.
        if nuevo_neto < CERO:
            raise BusinessError(
                "Con ese precio el valor del día queda negativo: revise los descuentos"
            )

        antes_recepcion = serialize_entity(recepcion)
        antes_liquidacion = serialize_entity(liquidacion)

        recepcion.precio_litro = precio
        recepcion.valor_bruto = nuevo_bruto
        recepcion.valor_neto = nuevo_neto
        recepcion.updated_by = self.ctx.user_id
        # La sesión no hace autoflush: sin este flush, el recálculo volvería a
        # consultar las recepciones y el día corregido podría releerse con el
        # precio viejo. Se baja el cambio antes de sumar.
        self.db.flush()

        self._recalcular_desde_recepciones(liquidacion)
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()

        # Se auditan las dos cosas: la liquidación, que es donde el dueño ve el
        # cambio, y la recepción del día, que es el dato de origen que quedó
        # corregido. La de recepción se arma a mano —y no con self._audit— para
        # que en el libro quede bajo su propio módulo y entidad; si no, saldría
        # como si alguien hubiera editado una liquidación con el id de otra cosa.
        from app.modules.auditoria.models import Auditoria

        self._audit("editar", liquidacion.id, antes_liquidacion, serialize_entity(liquidacion))
        self.db.add(
            Auditoria(
                empresa_id=self.ctx.empresa_id,
                usuario_id=self.ctx.user_id,
                ip=self.ctx.ip,
                modulo="recepcion",
                accion="editar",
                entidad="RecepcionLeche",
                entidad_id=recepcion.id,
                antes=antes_recepcion,
                despues=serialize_entity(recepcion),
            )
        )
        return liquidacion

    # ------------------------------------------------- anticipos del borrador
    def _anticipos_de(self, liquidacion: Liquidacion) -> list[Anticipo]:
        """Los anticipos que hoy están marcados contra esta liquidación.

        Va por el repositorio para no saltarse el filtro por empresa ni el de
        borrados: es plata de un tenant y con esto se reescribe el saldo.
        """
        stmt = (
            AnticipoRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(Anticipo.liquidacion_id == liquidacion.id)
            .order_by(Anticipo.fecha)
        )
        return list(self.db.scalars(stmt).all())

    def _aplicar_anticipos_pendientes(self, liquidacion: Liquidacion) -> Decimal:
        """Le marca al borrador los anticipos del tercero que todavía no se le han
        descontado a nadie, y deja el total y el saldo al día.

        Nace de un caso real: la liquidación de un proveedor se generó el mismo
        día en que después se le registró un anticipo de $500.000. Como los
        anticipos solo se aplicaban en el instante de generar, el borrador quedó
        con "Anticipos aplicados $0" y no había forma de recogerlo: volver a darle
        a "Generar" no hace nada, porque las recepciones ya están apartadas.

        Tres cuidados, en este orden de importancia:
        · SOLO en borrador. Aprobada o pagada esa cifra ya se le dio a alguien.
        · Un anticipo no se puede descontar dos veces: `pendientes_de` solo trae
          los que tienen `liquidacion_id` en nulo, así que el que ya se aplicó en
          otra quincena no vuelve a aparecer.
        · El total NO se le suma encima al guardado: se vuelve a sumar desde los
          anticipos que hoy apuntan a esta liquidación. Así, llamar dos veces
          (el botón oprimido dos veces, un reintento del navegador) da lo mismo.
        """
        if liquidacion.estado != ESTADO_BORRADOR:
            return Decimal(liquidacion.anticipos)

        anticipos_repo = AnticipoRepository(self.db, self.ctx.empresa_id)
        if liquidacion.tipo == TIPO_PROVEEDOR:
            pendientes = (
                anticipos_repo.pendientes_de(liquidacion.proveedor_id, liquidacion.periodo_fin)
                if liquidacion.proveedor_id
                else []
            )
        else:
            pendientes = (
                anticipos_repo.pendientes_transportador(
                    liquidacion.transportador_id, liquidacion.periodo_fin
                )
                if liquidacion.transportador_id
                else []
            )
        for anticipo in pendientes:
            anticipo.liquidacion_id = liquidacion.id
            anticipo.updated_by = self.ctx.user_id
        # Se baja el cambio antes de volver a leer: sin autoflush, la consulta de
        # abajo no vería los que se acaban de marcar y el total saldría corto.
        self.db.flush()

        total = sum((Decimal(a.valor) for a in self._anticipos_de(liquidacion)), CERO)
        liquidacion.anticipos = total
        _refrescar_saldo(liquidacion)
        return total

    def recalcular(self, entity_id: uuid.UUID) -> Liquidacion:
        """Vuelve a armar el borrador con lo que hay hoy en el sistema.

        Es la salida para el caso de siempre: la liquidación se generó y después
        se registró un anticipo (o se corrigió una recepción). Un borrador todavía
        no es plata entregada, así que se puede volver a cuadrar; aprobada o
        pagada rebota, porque ahí ya se le pagó a alguien.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado != ESTADO_BORRADOR:
            raise BusinessError(
                f"Esta liquidación está en '{liquidacion.estado}': solo se puede "
                "recalcular mientras sea un borrador"
            )
        antes = serialize_entity(liquidacion)
        # Cada tipo se rearma con su propia marca: la de proveedor por
        # `liquidacion_id` (un renglón por día del proveedor) y la de
        # transportador por `liquidacion_transporte_id` (un renglón por día con
        # toda la ruta sumada). Cruzarlas dejaría la liquidación en ceros.
        if liquidacion.tipo == TIPO_PROVEEDOR:
            self._recalcular_desde_recepciones(liquidacion)
        else:
            self._recalcular_transporte_desde_recepciones(liquidacion)
        self._aplicar_anticipos_pendientes(liquidacion)
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", liquidacion.id, antes, serialize_entity(liquidacion))
        return liquidacion

    def recuadrar(
        self,
        entity_id: uuid.UUID,
        motivo: str = "cambiaron las recepciones de la liquidación",
    ) -> bool:
        """Vuelve a cuadrar una liquidación cuyas cifras de origen acaban de cambiar.

        Es la contraparte de haber aflojado el candado de Recepción diaria: ahora
        un día se puede corregir mientras su liquidación no esté PAGADA, y esta es
        la que evita que quede un descuadre silencioso. Después se le aflojó el
        mismo candado a los ANTICIPOS, que entran por aquí igual: el `motivo` es
        lo único que cambia, y viaja a la bitácora para que mañana se pueda leer
        POR QUÉ una aprobada amaneció en borrador —si dijera siempre "cambiaron
        las recepciones", el libro estaría mintiendo la mitad de las veces—.

        - En borrador: se recalcula y ya.
        - APROBADA: se DEVUELVE A BORRADOR y se recalcula. Aprobar es un visto
          bueno sobre unas cifras; si las cifras cambian, el visto bueno ya no
          vale y hay que volver a darlo. El cambio de estado queda en auditoría
          aparte del recálculo, para que en el libro se lea qué pasó y por qué.
        - Pagada, o CON CUALQUIER PAGO REGISTRADO (parcial): no llega aquí (los
          guardias de Recepción diaria y de Anticipos rebotan antes), pero si
          llegara rebota igual: esa plata ya salió de la caja contra estas cifras.
        - Anulada: no se toca. Al anular se le sueltan las recepciones Y los
          anticipos, así que ninguno debería seguir apuntándole.

        Devuelve True si hubo que devolverla a borrador, para poder avisarle al
        usuario que la tiene que revisar y aprobar otra vez.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado == ESTADO_PAGADA:
            raise BusinessError(
                "Esta liquidación ya está pagada: sus días no se pueden modificar"
            )
        # Con un abono hecho ya no vale devolverla a borrador y recalcularla: el
        # pago se registró contra un total que dejaría de existir.
        if liquidacion.tiene_pagos:
            raise BusinessError(
                "Esta liquidación ya tiene pagos registrados: sus días no se pueden modificar"
            )
        if liquidacion.estado == ESTADO_ANULADA:
            return False

        devuelta_a_borrador = liquidacion.estado == ESTADO_APROBADA
        if devuelta_a_borrador:
            liquidacion.estado = ESTADO_BORRADOR
            liquidacion.updated_by = self.ctx.user_id
            self.db.flush()
            self._audit(
                "editar",
                liquidacion.id,
                {"estado": ESTADO_APROBADA},
                {"estado": ESTADO_BORRADOR, "motivo": motivo},
            )
        # Se reúsa el recálculo de siempre (el mismo del botón "Recalcular"), que
        # exige borrador: por eso el cambio de estado va primero.
        self.recalcular(entity_id)
        return devuelta_a_borrador

    # ------------------------------------------------------------ transiciones
    def _transicionar(self, entity_id: uuid.UUID, desde: tuple[str, ...], hacia: str) -> Liquidacion:
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado not in desde:
            raise BusinessError(
                f"No se puede pasar de '{liquidacion.estado}' a '{hacia}'"
            )
        antes = liquidacion.estado
        liquidacion.estado = hacia
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", liquidacion.id, {"estado": antes}, {"estado": hacia})
        return liquidacion

    def aprobar(self, entity_id: uuid.UUID) -> Liquidacion:
        """Aprobar es el último momento en que se puede corregir: enseguida se paga.

        Por eso antes de cambiar el estado se barren los anticipos pendientes del
        tercero. Si no, un anticipo registrado después de generar la liquidación
        se quedaría por fuera y se le pagaría al proveedor plata que ya se le
        había adelantado. Solo aplica sobre el borrador; el anticipo que ya se
        descontó en otra liquidación no vuelve a entrar.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado == ESTADO_BORRADOR:
            self._aplicar_anticipos_pendientes(liquidacion)
        return self._transicionar(entity_id, (ESTADO_BORRADOR,), ESTADO_APROBADA)

    # ------------------------------------------------------- pagos parciales
    def _exigir_pagable(self, liquidacion: Liquidacion) -> None:
        """Solo se le abona a una liquidación EN FIRME y que todavía deba algo.

        En borrador no, y esto no es formalismo: un borrador se recalcula solo
        cuando cambian las recepciones o entra un anticipo, así que el total
        contra el que se abonó puede cambiar debajo del pago y dejarlo
        descuadrado. Aprobar es justamente el momento en que las cifras quedan
        en firme.
        """
        if liquidacion.estado not in (ESTADO_APROBADA, ESTADO_PARCIAL):
            raise BusinessError(
                f"Esta liquidación está en '{liquidacion.estado}': solo se le puede "
                "pagar a una liquidación aprobada"
            )
        if Decimal(liquidacion.saldo) <= CERO:
            raise BusinessError("Esta liquidación no tiene saldo pendiente por pagar")

    def registrar_pago(self, entity_id: uuid.UUID, payload: Any) -> Liquidacion:
        """Registra un pago parcial (abono) contra una liquidación aprobada.

        Lo pidió el dueño: a un proveedor se le puede pagar una parte y quedarle
        debiendo el resto. Mientras deba algo la liquidación queda en PARCIAL, y
        pasa a PAGADA sola cuando el saldo llega a cero.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        # Con candado: dos pagos simultáneos sobre la misma liquidación se
        # pisarían y uno de los dos se perdería (ver `_bloquear`).
        liquidacion = _bloquear(self.db, liquidacion)
        self._exigir_pagable(liquidacion)

        valor = Decimal(payload.valor)
        pendiente = Decimal(liquidacion.saldo)
        if valor > pendiente:
            # pesos() y no "{:,.0f}": el formato con coma es gringo y "$1,200,000"
            # en Colombia se lee como un peso con veinte centavos.
            raise BusinessError(
                f"El pago ({pesos(valor)}) supera el saldo pendiente ({pesos(pendiente)})"
            )

        self.db.add(
            PagoLiquidacion(
                liquidacion_id=liquidacion.id,
                fecha=payload.fecha,
                valor=valor,
                observaciones=payload.observaciones,
                created_by=self.ctx.user_id,
            )
        )
        liquidacion.pagado = Decimal(liquidacion.pagado) + valor
        _refrescar_saldo(liquidacion)
        liquidacion.estado = _estado_pago(liquidacion.neto_a_pagar, liquidacion.pagado)
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()
        # Se refresca la lista de pagos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `pagado` nuevo pero SIN el pago en la
        # lista. La pantalla pinta las dos cosas juntas y se contradicen a la
        # vista.
        self.db.refresh(liquidacion, ["pagos"])
        self._audit(
            "editar", liquidacion.id, None,
            {"pago": float(valor), "estado": liquidacion.estado, "saldo": float(liquidacion.saldo)},
        )
        return liquidacion

    def eliminar_pago(self, entity_id: uuid.UUID, pago_id: uuid.UUID) -> Liquidacion:
        """Elimina un pago mal registrado: devuelve el saldo y recalcula el estado."""
        liquidacion = self.repo.get_or_fail(entity_id)
        liquidacion = _bloquear(self.db, liquidacion)
        pago = next((p for p in liquidacion.pagos if p.id == pago_id), None)
        if pago is None:
            raise NotFoundError("Pago no encontrado")
        valor = Decimal(pago.valor)
        liquidacion.pagado = max(Decimal(liquidacion.pagado) - valor, CERO)
        _refrescar_saldo(liquidacion)
        liquidacion.estado = _estado_pago(liquidacion.neto_a_pagar, liquidacion.pagado)
        liquidacion.updated_by = self.ctx.user_id
        self.db.delete(pago)
        self.db.flush()
        # Mismo motivo que al registrar: sin refrescar, la respuesta traería el
        # pago borrado todavía dentro de la lista.
        self.db.refresh(liquidacion, ["pagos"])
        self._audit(
            "editar", liquidacion.id, None,
            {
                "pago_eliminado": float(valor),
                "estado": liquidacion.estado,
                "saldo": float(liquidacion.saldo),
            },
        )
        return liquidacion

    def pagar(self, entity_id: uuid.UUID) -> Liquidacion:
        """El botón "Pagar" de siempre: saldar la liquidación de una vez.

        Antes solo cambiaba el estado a 'pagada' (no movía caja ni bancos, y eso
        se conserva). Ahora hace lo mismo pero DEJANDO CONSTANCIA: registra un
        pago por todo el saldo pendiente y el estado sale de `_estado_pago`, así
        que una liquidación pagada de un solo golpe y otra pagada en tres abonos
        quedan contadas igual y las dos aparecen en el historial.

        El caso raro se respeta: si los anticipos se comieron todo y no queda
        saldo, no hay pago que registrar y solo se marca pagada, como antes.
        """
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado not in (ESTADO_APROBADA, ESTADO_PARCIAL):
            raise BusinessError(
                f"No se puede pasar de '{liquidacion.estado}' a '{ESTADO_PAGADA}'"
            )
        pendiente = Decimal(liquidacion.saldo)
        if pendiente <= CERO:
            return self._transicionar(
                entity_id, (ESTADO_APROBADA, ESTADO_PARCIAL), ESTADO_PAGADA
            )
        return self.registrar_pago(
            entity_id,
            PagoLiquidacionCreate(
                fecha=date.today(),
                valor=pendiente,
                observaciones="Pago total de la liquidación",
            ),
        )

    def anular(self, entity_id: uuid.UUID) -> Liquidacion:
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado == ESTADO_PAGADA:
            raise BusinessError("No se puede anular una liquidación ya pagada")
        # Anular suelta las recepciones y los anticipos para volver a liquidar el
        # período. Con un abono hecho eso dejaría un pago colgando de un
        # documento que ya no representa nada: primero se borra el pago.
        if liquidacion.tiene_pagos:
            raise BusinessError(
                "No se puede anular una liquidación con pagos registrados: "
                "elimine primero los pagos"
            )
        # Liberar recepciones y anticipos para poder re-liquidar
        campo = (
            RecepcionLeche.liquidacion_id
            if liquidacion.tipo == TIPO_PROVEEDOR
            else RecepcionLeche.liquidacion_transporte_id
        )
        recepciones = self.db.scalars(
            RecepcionRepository(self.db, self.ctx.empresa_id).base_query().where(campo == liquidacion.id)
        ).all()
        for r in recepciones:
            if liquidacion.tipo == TIPO_PROVEEDOR:
                r.liquidacion_id = None
            else:
                r.liquidacion_transporte_id = None
        anticipos = self.db.scalars(
            AnticipoRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(Anticipo.liquidacion_id == liquidacion.id)
        ).all()
        for a in anticipos:
            a.liquidacion_id = None
        return self._transicionar(entity_id, (ESTADO_BORRADOR, ESTADO_APROBADA), ESTADO_ANULADA)

    # ------------------------------------------------------------------ listar
    def listar_filtrado(
        self,
        params: PageParams,
        *,
        tipo: str | None = None,
        estado: str | None = None,
        proveedor_id: uuid.UUID | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> tuple[list[Liquidacion], int]:
        extra = []
        if desde:
            extra.append(Liquidacion.periodo_fin >= desde)
        if hasta:
            extra.append(Liquidacion.periodo_inicio <= hasta)
        return self.repo.list_paginated(
            params,
            estado=estado,
            filters={"tipo": tipo, "proveedor_id": proveedor_id},
            extra_criteria=extra,
        )

    # ----------------------------------------------------------------- export
    def _nombre_tercero(self, liquidacion: Liquidacion) -> str:
        if liquidacion.tipo == TIPO_PROVEEDOR and liquidacion.proveedor:
            return liquidacion.proveedor.nombre
        if liquidacion.transportador:
            return liquidacion.transportador.nombre
        return "-"

    def generar_pdf(self, entity_id: uuid.UUID) -> tuple[bytes, str]:
        liquidacion = self.repo.get_or_fail(entity_id)
        empresa = EmpresaRepository(self.db).get(self.ctx.empresa_id)
        nombre_empresa = empresa.nombre if empresa else "Quesera"
        nit = empresa.nit if empresa else None
        ubicacion = (
            ", ".join(p for p in [empresa.ciudad, empresa.departamento] if p) or None
            if empresa
            else None
        )
        tercero = self._nombre_tercero(liquidacion)
        es_proveedor = liquidacion.tipo == TIPO_PROVEEDOR
        tercero_detalle = (
            getattr(liquidacion.proveedor, "vereda", None)
            if es_proveedor and liquidacion.proveedor
            else None
        )

        # Toda la plata y los litros salen por los formateadores colombianos
        # (pesos / litros): el productor lee $18.525.000, no $18,525,000.
        detalle_rows = [
            [d.fecha.strftime("%d/%m/%Y"), litros(d.litros), pesos(d.precio_litro), pesos(d.valor)]
            for d in liquidacion.detalles
        ]

        # El renglón "Pagado" solo aparece cuando de verdad se abonó algo. Sin él
        # el comprobante de una liquidación a medio pagar mostraría un SALDO A
        # PAGAR más chico que VALOR TOTAL menos anticipos, sin explicar por qué:
        # el dueño cuadra estas cifras a mano y esa diferencia muda es justo lo
        # que le hace perder la confianza en el papel.
        pagado_rows = (
            [("Pagado", f"- {pesos(liquidacion.pagado)}", False)]
            if Decimal(liquidacion.pagado or 0) > CERO
            else []
        )

        if es_proveedor:
            resumen_rows = [
                ("Total litros", litros(liquidacion.total_litros), False),
                ("Precio promedio", pesos(liquidacion.precio_promedio), False),
                ("Valor bruto", pesos(liquidacion.valor_bruto), False),
                ("Bonificaciones", f"+ {pesos(liquidacion.bonificaciones)}", False),
                ("Descuentos", f"- {pesos(liquidacion.descuentos)}", False),
                ("Anticipos aplicados", f"- {pesos(liquidacion.anticipos)}", False),
                *pagado_rows,
                ("VALOR TOTAL", pesos(liquidacion.valor_total), True),
                ("SALDO A PAGAR", pesos(liquidacion.saldo), True),
            ]
        else:
            # El renglón de anticipos también va en la del transportador: sin él,
            # el comprobante mostraba VALOR TOTAL y SALDO A PAGAR distintos sin
            # explicar la diferencia, y el dueño cuadra estas cifras a mano.
            resumen_rows = [
                ("Total litros", litros(liquidacion.total_litros), False),
                ("Valor transporte", pesos(liquidacion.valor_transporte), False),
                ("Anticipos aplicados", f"- {pesos(liquidacion.anticipos)}", False),
                *pagado_rows,
                ("VALOR TOTAL", pesos(liquidacion.valor_total), True),
                ("SALDO A PAGAR", pesos(liquidacion.saldo), True),
            ]

        anticipos = self._anticipos_de(liquidacion)
        anticipos_rows: list[list[Any]] = [
            [a.fecha.strftime("%d/%m/%Y"), pesos(a.valor), a.observaciones or "—"]
            for a in anticipos
        ]

        periodo = (
            f"{liquidacion.periodo_inicio.strftime('%d/%m/%Y')} al "
            f"{liquidacion.periodo_fin.strftime('%d/%m/%Y')}"
        )
        pdf = build_liquidacion_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            folio=str(liquidacion.id)[:8].upper(),
            estado=liquidacion.estado,
            emitido=datetime.now().strftime("%d/%m/%Y %H:%M"),
            tercero_label="Proveedor" if es_proveedor else "Transportador",
            tercero_nombre=tercero,
            tercero_detalle=tercero_detalle,
            periodo=periodo,
            detalle_headers=["Fecha", "Litros", "Precio/L", "Valor"],
            detalle_rows=detalle_rows,
            resumen_rows=resumen_rows,
            anticipos_rows=anticipos_rows,
            observaciones=liquidacion.observaciones,
        )
        filename = f"liquidacion_{tercero}_{liquidacion.periodo_inicio.isoformat()}.pdf".replace(" ", "_")
        return pdf, filename

    def previsualizar_pdf(
        self, inicio: date, fin: date, tipo: str, tercero_id: uuid.UUID
    ) -> tuple[bytes, str]:
        """PDF preliminar (marcado como no oficial) del avance de un tercero."""
        previews = self.previsualizar(inicio, fin, tipo, tercero_id)
        if not previews:
            raise BusinessError(
                "No hay recepciones sin liquidar para ese tercero en el período"
            )
        pre = previews[0]
        empresa = EmpresaRepository(self.db).get(self.ctx.empresa_id)
        nombre_empresa = empresa.nombre if empresa else "Quesera"
        nit = empresa.nit if empresa else None
        ubicacion = (
            (", ".join(p for p in [empresa.ciudad, empresa.departamento] if p) or None)
            if empresa
            else None
        )
        es_proveedor = pre.tipo == TIPO_PROVEEDOR
        # Mismo formato colombiano que el comprobante oficial: el productor recibe
        # los dos documentos y no pueden leerse distinto.
        detalle_rows = [
            [d.fecha.strftime("%d/%m/%Y"), litros(d.litros), pesos(d.precio_litro), pesos(d.valor)]
            for d in pre.detalles
        ]
        if es_proveedor:
            resumen_rows = [
                ("Total litros", litros(pre.total_litros), False),
                ("Precio promedio", pesos(pre.precio_promedio), False),
                ("Valor bruto", pesos(pre.valor_bruto), False),
                ("Bonificaciones", f"+ {pesos(pre.bonificaciones)}", False),
                ("Descuentos", f"- {pesos(pre.descuentos)}", False),
                ("Anticipos aplicados", f"- {pesos(pre.anticipos)}", False),
                ("VALOR TOTAL", pesos(pre.valor_total), True),
                ("SALDO ESTIMADO", pesos(pre.saldo), True),
            ]
        else:
            resumen_rows = [
                ("Total litros", litros(pre.total_litros), False),
                ("Valor transporte", pesos(pre.valor_transporte), False),
                ("Anticipos aplicados", f"- {pesos(pre.anticipos)}", False),
                ("VALOR TOTAL", pesos(pre.valor_total), True),
                ("SALDO ESTIMADO", pesos(pre.saldo), True),
            ]
        anticipos_rows = [
            [a.fecha.strftime("%d/%m/%Y"), pesos(a.valor), a.observaciones or "—"]
            for a in pre.anticipos_detalle
        ]
        periodo = f"{inicio.strftime('%d/%m/%Y')} al {fin.strftime('%d/%m/%Y')}"
        pdf = build_liquidacion_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            folio="PRELIMINAR",
            estado="preliminar",
            emitido=datetime.now().strftime("%d/%m/%Y %H:%M"),
            tercero_label="Proveedor" if es_proveedor else "Transportador",
            tercero_nombre=pre.tercero_nombre,
            tercero_detalle=pre.tercero_detalle,
            periodo=periodo,
            detalle_headers=["Fecha", "Litros", "Precio/L", "Valor"],
            detalle_rows=detalle_rows,
            resumen_rows=resumen_rows,
            anticipos_rows=anticipos_rows,
            observaciones="PRE-LIQUIDACIÓN — documento informativo del avance; no constituye pago.",
        )
        filename = (
            f"preliquidacion_{pre.tercero_nombre}_{inicio.isoformat()}.pdf".replace(" ", "_")
        )
        return pdf, filename


class AnticipoService(BaseService[Anticipo]):
    repository_cls = AnticipoRepository
    modulo = "liquidaciones"

    _CAMPO_POR_TIPO = {
        "proveedor": "proveedor_id",
        "transportador": "transportador_id",
        "empleado": "empleado_id",
    }

    def crear(self, payload: Any) -> Anticipo:
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        tipo = data.get("tipo") or TIPO_PROVEEDOR
        campo = self._CAMPO_POR_TIPO.get(tipo)
        if campo is None:
            raise BusinessError(f"Tipo de anticipo inválido: {tipo}")
        if not data.get(campo):
            raise BusinessError(f"Debe indicar el {tipo} del anticipo")
        data["tipo"] = tipo
        # Deja solo el id del beneficiario que corresponde al tipo
        for otro in self._CAMPO_POR_TIPO.values():
            if otro != campo:
                data[otro] = None
        anticipo = super().crear(data)
        # Nace suelto (sin liquidación), pero la respuesta trae los mismos campos
        # que el listado: si no se marcaran, la pantalla los vería en nulo y no
        # sabría si el recién creado se puede corregir.
        self._marcar_liquidacion([anticipo])
        return anticipo

    # ------------------------------------------- el candado: "ya se pagó", no
    #                                              "ya se liquidó"
    def _liquidacion_de(self, anticipo: Anticipo) -> Liquidacion | None:
        """La liquidación en la que este anticipo quedó descontado, si hay alguna.

        Va por el repositorio para no saltarse el filtro por empresa ni el de
        borrados: de esto depende si se deja o no tocar plata de un tenant.
        """
        if anticipo.liquidacion_id is None:
            return None
        stmt = (
            LiquidacionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(Liquidacion.id == anticipo.liquidacion_id)
        )
        return self.db.scalars(stmt).first()

    def _exigir_no_pagado(self, anticipo: Anticipo, verbo: str) -> Liquidacion | None:
        """El candado de verdad: se traba en cuanto SALIÓ PLATA contra el anticipo.

        Antes se trababa apenas el anticipo tuviera liquidación, sin mirar el
        estado, así que quedaba congelado desde que se GENERABA la quincena
        —aunque todavía no se le hubiera pagado un peso a nadie—. Es el mismo
        problema que ya se resolvió en Recepción diaria y la regla es la misma,
        copiada de `RecepcionService._exigir_no_pagada` a propósito: dos criterios
        distintos para la misma pregunta terminan contradiciéndose.

        "Ya salió plata" es TENER ALGÚN PAGO, no estar en 'pagada': si al
        proveedor se le abonó la mitad y después le cambian el anticipo, ese abono
        queda contra un neto que ya no existe.

        Devuelve la liquidación tocada, para recuadrarla después de escribir.
        """
        # NÓMINA: camino aparte y sin tocar. Un pago de empleado no tiene estados
        # (ni borrador, ni aprobada) ni pagos parciales: existe = ya se le pagó al
        # empleado con el anticipo ya descontado. No hay nada que "estar sin
        # pagar", así que ahí el candado se queda como estaba.
        if anticipo.pago_empleado_id is not None:
            raise BusinessError(
                f"No se puede {verbo} este anticipo: ya se le descontó al empleado "
                "en un pago de nómina"
            )

        if anticipo.liquidacion_id is None:
            return None

        liquidacion = self._liquidacion_de(anticipo)
        if liquidacion is None:
            # El anticipo apunta a una liquidación que esta empresa no ve. No
            # debería pasar; ante la duda se traba, que es el lado seguro cuando
            # de por medio hay plata.
            raise BusinessError(
                f"No se puede {verbo} este anticipo: está aplicado a una "
                "liquidación que no se puede consultar"
            )

        # Con candado antes de decidir: se va a mirar `pagado` y enseguida a
        # reescribir el total de la liquidación. Sin el FOR UPDATE, un pago que
        # entre en ese instante se lee como "todavía no hay pagos" y el recálculo
        # le pasa por encima. Ver `_bloquear`.
        liquidacion = _bloquear(self.db, liquidacion)

        # Dos mensajes porque son dos situaciones distintas para el usuario: de la
        # pagada no hay nada que hacer por dentro; del abono sí, se puede borrar el
        # pago, corregir el anticipo y volver a abonar.
        if liquidacion.estado == ESTADO_PAGADA:
            raise BusinessError(
                f"No se puede {verbo} este anticipo: la liquidación en la que se "
                "descontó ya se pagó. Si la cifra está mala, registre el ajuste en "
                "la quincena siguiente"
            )
        if liquidacion.tiene_pagos:
            raise BusinessError(
                f"No se puede {verbo} este anticipo: la liquidación en la que se "
                "descontó ya tiene un pago registrado. Elimine primero ese pago si "
                "de verdad hay que corregirlo, o registre el ajuste en la quincena "
                "siguiente"
            )
        return liquidacion

    def _recuadrar(self, liquidacion: Liquidacion | None, motivo: str) -> None:
        """Vuelve a cuadrar la liquidación a la que se le movió un anticipo.

        Sin esto queda el descuadre silencioso: la liquidación seguiría diciendo
        "Anticipos aplicados $500.000" cuando el anticipo se corrigió a $300.000 o
        se borró, y su neto a pagar —la cifra grande del comprobante— saldría mal
        por la diferencia.
        """
        if liquidacion is None:
            return
        LiquidacionService(self.db, self.ctx).recuadrar(liquidacion.id, motivo)

    # ------------------------------------------------------- estado para la UI
    def _marcar_liquidacion(self, anticipos: list[Anticipo]) -> None:
        """Le cuelga a cada anticipo el estado de su liquidación y si está trabado.

        No son columnas: se resuelven de un solo golpe para toda la lista (una
        consulta, no una por fila) y se exponen en `AnticipoRead` para que la
        pantalla sepa cuándo poner el candado. Con el candado viejo bastaba
        `aplicado`; ahora "aplicado" y "trabado" son cosas distintas, y si la
        pantalla siguiera mirando `aplicado` seguiría escondiendo los botones de
        anticipos que sí se pueden corregir.
        """
        ids = {a.liquidacion_id for a in anticipos if a.liquidacion_id is not None}
        estados: dict[uuid.UUID, tuple[str, bool]] = {}
        if ids:
            stmt = (
                LiquidacionRepository(self.db, self.ctx.empresa_id)
                .base_query()
                .where(Liquidacion.id.in_(ids))
            )
            estados = {
                liq.id: (liq.estado, liq.tiene_pagos or liq.estado == ESTADO_PAGADA)
                for liq in self.db.scalars(stmt).all()
            }
        for anticipo in anticipos:
            # El default cubre el anticipo suelto (None, False) y el que apunta a
            # una liquidación que no se ve, que se muestra trabado igual que lo
            # rebota el guardia.
            estado, con_pago = estados.get(
                anticipo.liquidacion_id, (None, anticipo.liquidacion_id is not None)
            )
            anticipo.liquidacion_estado = estado
            anticipo.bloqueado = con_pago or anticipo.pago_empleado_id is not None

    def obtener(self, entity_id: uuid.UUID) -> Anticipo:
        anticipo = super().obtener(entity_id)
        self._marcar_liquidacion([anticipo])
        return anticipo

    def listar(self, params: PageParams, **kwargs: Any) -> tuple[list[Anticipo], int]:
        items, total = super().listar(params, **kwargs)
        self._marcar_liquidacion(items)
        return items, total

    # ------------------------------------------------------------- correcciones
    def validar_actualizar(self, obj: Anticipo, data: dict[str, Any]) -> None:
        self._exigir_no_pagado(obj, "modificar")

    def validar_eliminar(self, obj: Anticipo) -> None:
        self._exigir_no_pagado(obj, "eliminar")

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Anticipo:
        """Corrige un anticipo y deja cuadrada la liquidación que lo tenía.

        La liquidación se apunta ANTES de escribir, porque después de guardar hay
        que volver a cuadrarla con la cifra nueva.
        """
        actual = self.repo.get_or_fail(entity_id)
        liquidacion = self._exigir_no_pagado(actual, "modificar")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)

        if liquidacion is not None:
            # Si al anticipo le corrigen la fecha y se pasa del fin del período,
            # ese adelanto ya no pertenece a esta quincena: se suelta para que lo
            # recoja la que le toca. Es el mismo criterio que con las recepciones
            # —si se quedara, el comprobante de junio descontaría un anticipo de
            # julio— y encaja con `pendientes_de`, que solo recoge los que tienen
            # fecha hasta el fin del período.
            nueva_fecha = data.get("fecha") or actual.fecha
            if nueva_fecha > liquidacion.periodo_fin:
                data["liquidacion_id"] = None

        anticipo = super().actualizar(entity_id, data)
        # Se baja el cambio antes de recuadrar: la sesión no hace autoflush y el
        # recálculo vuelve a leer los anticipos desde la base.
        self.db.flush()
        self._recuadrar(liquidacion, "se corrigió un anticipo aplicado a la liquidación")
        self._marcar_liquidacion([anticipo])
        return anticipo

    def eliminar(self, entity_id: uuid.UUID) -> None:
        """Borra el anticipo y deja cuadrada la liquidación que lo tenía.

        El borrado es lógico y `_anticipos_de` filtra por `deleted_at IS NULL`, así
        que al recuadrar la liquidación el anticipo borrado simplemente deja de
        sumar y el neto a pagar sube en ese valor, que es lo correcto: si el
        adelanto nunca existió, no hay nada que descontarle al productor.
        """
        anticipo = self.repo.get_or_fail(entity_id)
        liquidacion = self._exigir_no_pagado(anticipo, "eliminar")
        super().eliminar(entity_id)
        self.db.flush()
        self._recuadrar(liquidacion, "se eliminó un anticipo aplicado a la liquidación")
