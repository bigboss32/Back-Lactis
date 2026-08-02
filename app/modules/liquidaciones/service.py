"""Liquidaciones por quincena: agrupa las recepciones no liquidadas del período,
calcula totales, descuenta anticipos y genera el comprobante (PDF/Excel),
replicando el proceso que la quesera llevaba en Excel.
"""
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, NotFoundError
from app.core.pagination import PageParams
from app.modules.empresas.repository import EmpresaRepository
from app.modules.liquidaciones.models import (
    ESTADO_ANULADA,
    ESTADO_APROBADA,
    ESTADO_BORRADOR,
    ESTADO_PAGADA,
    TIPO_PROVEEDOR,
    TIPO_TRANSPORTADOR,
    Anticipo,
    Liquidacion,
    LiquidacionDetalle,
)
from app.modules.liquidaciones.repository import AnticipoRepository, LiquidacionRepository
from app.modules.liquidaciones.schemas import (
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

        valor_por_dia = {
            r.fecha: Decimal(r.valor_bruto) + Decimal(r.bonificaciones) - Decimal(r.descuentos)
            for r in recepciones
        }
        for detalle in liquidacion.detalles:
            recepcion = por_fecha.get(detalle.fecha)
            if recepcion is None:
                continue
            detalle.litros = recepcion.cantidad_litros
            detalle.precio_litro = recepcion.precio_litro
            detalle.valor = valor_por_dia[detalle.fecha]
            detalle.updated_by = self.ctx.user_id

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
        liquidacion.saldo = valor_total - Decimal(liquidacion.anticipos)

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
        liquidacion.saldo = Decimal(liquidacion.valor_total) - total
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
        # En la del transportador los renglones son la tarifa del flete por día y
        # agrupan varias recepciones de la ruta: rearmarlos desde las recepciones
        # del proveedor sería otra cosa. Ahí solo se barren los anticipos.
        if liquidacion.tipo == TIPO_PROVEEDOR:
            self._recalcular_desde_recepciones(liquidacion)
        self._aplicar_anticipos_pendientes(liquidacion)
        liquidacion.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", liquidacion.id, antes, serialize_entity(liquidacion))
        return liquidacion

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

    def pagar(self, entity_id: uuid.UUID) -> Liquidacion:
        return self._transicionar(entity_id, (ESTADO_APROBADA,), ESTADO_PAGADA)

    def anular(self, entity_id: uuid.UUID) -> Liquidacion:
        liquidacion = self.repo.get_or_fail(entity_id)
        if liquidacion.estado == ESTADO_PAGADA:
            raise BusinessError("No se puede anular una liquidación ya pagada")
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

        if es_proveedor:
            resumen_rows = [
                ("Total litros", litros(liquidacion.total_litros), False),
                ("Precio promedio", pesos(liquidacion.precio_promedio), False),
                ("Valor bruto", pesos(liquidacion.valor_bruto), False),
                ("Bonificaciones", f"+ {pesos(liquidacion.bonificaciones)}", False),
                ("Descuentos", f"- {pesos(liquidacion.descuentos)}", False),
                ("Anticipos aplicados", f"- {pesos(liquidacion.anticipos)}", False),
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
        return super().crear(data)

    def validar_actualizar(self, obj: Anticipo, data: dict[str, Any]) -> None:
        if obj.aplicado:
            raise BusinessError("No se puede modificar un anticipo ya aplicado")

    def validar_eliminar(self, obj: Anticipo) -> None:
        if obj.aplicado:
            raise BusinessError("No se puede eliminar un anticipo ya aplicado")
