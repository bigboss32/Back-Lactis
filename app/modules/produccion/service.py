import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.common.service import BaseService
from app.core.exceptions import BusinessError, ConflictError
from app.core.pagination import PageParams
from app.modules.inventario.models import (
    CATEGORIA_PRODUCTO_TERMINADO,
    MOVIMIENTO_AJUSTE,
    MOVIMIENTO_ENTRADA,
    MOVIMIENTO_SALIDA,
    MovimientoInventario,
    Producto,
)
from app.modules.produccion.models import (
    REFERENCIA_MERMA_CICLO,
    CicloDespacho,
    CicloDespachoLote,
    Produccion,
    TipoQueso,
)
from app.modules.produccion.repository import (
    CicloDespachoLoteRepository,
    CicloDespachoRepository,
    ProduccionRepository,
    TipoQuesoRepository,
)

CERO = Decimal("0")
DOS_DECIMALES = Decimal("0.01")
CIEN = Decimal("100")

# Cuántos días dura un ciclo de despacho. El dueño dijo que acumula las tandas del
# día 1 al 7, despacha, y vuelve a empezar. No es una regla del sistema: es cada
# cuánto se le PROPONE cerrar, y las fechas se pueden mover a mano antes de aceptar.
DIAS_DEL_CICLO = 7

# A partir de qué porcentaje una merma deja de ser creíble. Una tanda de 130 kg que
# rinde 125 se secó un 3,8%; si se seca un 25% no es secado, es una venta sin
# anotar o un peso mal tomado, y hay que decirlo antes de dar esa plata por perdida.
MERMA_SOSPECHOSA_PCT = Decimal("10")


def _dinero(valor: Decimal) -> Decimal:
    """Redondea a centavos. Se usa al FINAL, nunca antes de multiplicar."""
    return Decimal(valor).quantize(DOS_DECIMALES)


class TipoQuesoService(BaseService[TipoQueso]):
    repository_cls = TipoQuesoRepository
    modulo = "produccion"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(TipoQueso.nombre == data["nombre"]):
            raise ConflictError(f"Ya existe el tipo de queso '{data['nombre']}'")


class ProduccionService(BaseService[Produccion]):
    repository_cls = ProduccionRepository
    modulo = "produccion"

    @staticmethod
    def _calcular_rendimiento(data: dict[str, Any], actual: Produccion | None = None) -> dict[str, Any]:
        peso = Decimal(data.get("peso_kg") or (actual.peso_kg if actual else CERO))
        litros = Decimal(
            data.get("litros_usados")
            if data.get("litros_usados") is not None
            else (actual.litros_usados if actual else CERO)
        )
        data["rendimiento"] = (peso / litros).quantize(Decimal("0.0001")) if litros else CERO
        return data

    # --------------------------------------------------- vínculo inventario
    # Al registrar producción, el queso producido entra al inventario como
    # producto terminado (por kilos). Se replica el patrón de VentaService.
    def _producto_terminado(self, tipo_queso_id: uuid.UUID) -> Producto:
        """Busca el producto terminado ligado a ese tipo de queso; si no existe, lo crea."""
        producto = self.db.scalars(
            select(Producto).where(
                Producto.empresa_id == self.ctx.empresa_id,
                Producto.tipo_queso_id == tipo_queso_id,
                Producto.deleted_at.is_(None),
            )
        ).first()
        if producto is None:
            tipo = self.db.get(TipoQueso, tipo_queso_id)
            producto = Producto(
                empresa_id=self.ctx.empresa_id,
                nombre=tipo.nombre if tipo else "Queso",
                categoria=CATEGORIA_PRODUCTO_TERMINADO,
                unidad="kg",
                tipo_queso_id=tipo_queso_id,
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            self.db.add(producto)
            self.db.flush()
        return producto

    def _movimiento(
        self,
        producto_id: uuid.UUID,
        tipo: str,
        cantidad: Decimal,
        fecha: date,
        referencia: str,
        sucursal_id: uuid.UUID | None,
    ) -> None:
        self.db.add(
            MovimientoInventario(
                empresa_id=self.ctx.empresa_id,
                producto_id=producto_id,
                sucursal_id=sucursal_id,
                fecha=fecha,
                tipo=tipo,
                cantidad=cantidad,
                referencia=referencia,
                created_by=self.ctx.user_id,
            )
        )
        self.db.flush()

    def _entrada_inventario(self, produccion: Produccion) -> None:
        if not produccion.peso_kg or produccion.peso_kg <= CERO:
            return
        producto = self._producto_terminado(produccion.tipo_queso_id)
        self._movimiento(
            producto.id, MOVIMIENTO_ENTRADA, Decimal(produccion.peso_kg),
            produccion.fecha, f"Producción #{str(produccion.id)[:8]}", produccion.sucursal_id,
        )

    def _salida_inventario(
        self, tipo_queso_id: uuid.UUID, peso: Decimal, fecha: date, referencia: str,
        sucursal_id: uuid.UUID | None,
    ) -> None:
        if not peso or Decimal(peso) <= CERO:
            return
        producto = self.db.scalars(
            select(Producto).where(
                Producto.empresa_id == self.ctx.empresa_id,
                Producto.tipo_queso_id == tipo_queso_id,
                Producto.deleted_at.is_(None),
            )
        ).first()
        if producto is None:
            return
        self._movimiento(
            producto.id, MOVIMIENTO_SALIDA, Decimal(peso), fecha, referencia, sucursal_id
        )

    def crear(self, payload: Any) -> Produccion:
        data = payload.model_dump(exclude_unset=True)
        TipoQuesoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["tipo_queso_id"])
        produccion = super().crear(self._calcular_rendimiento(data))
        self._entrada_inventario(produccion)
        return produccion

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Produccion:
        actual = self.repo.get_or_fail(entity_id)
        peso_antes = Decimal(actual.peso_kg)
        tipo_antes = actual.tipo_queso_id
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        produccion = super().actualizar(entity_id, self._calcular_rendimiento(data, actual))
        # Si cambió el peso o el tipo de queso, se reversa la entrada anterior y
        # se registra la nueva, para que el stock quede coherente.
        if Decimal(produccion.peso_kg) != peso_antes or produccion.tipo_queso_id != tipo_antes:
            self._salida_inventario(
                tipo_antes, peso_antes, produccion.fecha,
                f"Ajuste producción #{str(produccion.id)[:8]}", produccion.sucursal_id,
            )
            self._entrada_inventario(produccion)
        return produccion

    def eliminar(self, entity_id: uuid.UUID) -> None:
        produccion = self.repo.get_or_fail(entity_id)
        self._salida_inventario(
            produccion.tipo_queso_id, Decimal(produccion.peso_kg), produccion.fecha,
            f"Reversa producción #{str(produccion.id)[:8]}", produccion.sucursal_id,
        )
        super().eliminar(entity_id)

    def listar_filtrado(
        self,
        params: PageParams,
        *,
        tipo_queso_id: uuid.UUID | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> tuple[list[Produccion], int]:
        extra = []
        if desde:
            extra.append(Produccion.fecha >= desde)
        if hasta:
            extra.append(Produccion.fecha <= hasta)
        return self.repo.list_paginated(
            params, filters={"tipo_queso_id": tipo_queso_id}, extra_criteria=extra
        )


# ------------------------------------------- utilidad por lote de producción
class LoteProduccionService:
    """Qué dejó el queso que se hizo cada día.

    Toda la mecánica de las dos cadenas (leche -> producción -> venta) está en
    `app.modules.produccion.lotes`, que es una función pura sin base de datos para
    poder probarla con casos armados a mano. Aquí solo se leen los eventos, se
    llama al reparto y se arma la respuesta.

    El reparto se hace SIEMPRE sobre toda la historia, aunque se pidan los lotes de
    un mes: la leche del 30 de junio es el queso de julio, y el queso de julio se
    vende en septiembre. Filtrar la consulta dejaría los primeros días sin
    respaldo. El filtro de fechas se aplica al final, a qué lotes se MUESTRAN.
    """

    modulo = "produccion"

    def __init__(self, db, ctx):
        self.db = db
        self.ctx = ctx

    def _reparto(self):
        """Arma el reparto completo de las dos cadenas.

        Lo usan la pantalla de lotes Y el estado de resultados: si cada uno armara
        el suyo, podrían acabar diciendo cosas distintas del mismo queso.

        Va SIEMPRE sobre toda la historia, sin filtro de fechas: la leche del 30 de
        junio es el queso de julio y el queso de julio se vende en septiembre. Quien
        llama recorta después lo que muestra.
        """
        from app.modules.inventario.repository import ProductoRepository
        from app.modules.produccion.lotes import (
            MOTIVO_MERMA_CICLO,
            BajaEvento,
            ExistenciaEvento,
            ProduccionEvento,
            RecepcionEvento,
            VentaEvento,
            repartir_produccion,
        )
        from app.modules.produccion.repository import ProduccionRepository
        from app.modules.produccion.schemas import (
            LecheDelLoteRead,
            LoteProduccionRead,
            LotesProduccionPanel,
            VentaDelLoteProduccionRead,
        )
        from app.modules.recepcion.repository import RecepcionRepository
        from app.modules.ventas.repository import VentaRepository

        empresa = self.ctx.empresa_id
        recepciones = [
            RecepcionEvento(
                fecha=f[0], orden=i, proveedor=f[2], litros=Decimal(f[3] or 0),
                valor_leche=Decimal(f[4] or 0), valor_transporte=Decimal(f[5] or 0),
            )
            for i, f in enumerate(RecepcionRepository(self.db, empresa).eventos_para_lotes())
        ]
        producciones = [
            ProduccionEvento(
                fecha=f[0], orden=i, tipo_queso_id=f[2], tipo_queso=f[3],
                litros_usados=Decimal(f[4] or 0), kilos=Decimal(f[5] or 0),
                merma=Decimal(f[6] or 0), produccion_id=f[7],
            )
            for i, f in enumerate(ProduccionRepository(self.db, empresa).eventos_para_lotes())
        ]
        # El flete viene de la VENTA completa, así que a cada renglón le toca la
        # parte que corresponde a SUS kilos: si se le cargara entero a cada renglón,
        # una venta de tres productos multiplicaría el flete por tres.
        filas_venta = VentaRepository(self.db, empresa).eventos_para_lotes()
        # Kilos totales por venta, para repartir su flete entre los renglones
        kilos_por_venta: dict = {}
        for f in filas_venta:
            kilos_por_venta[f[9]] = kilos_por_venta.get(f[9], CERO) + Decimal(f[5] or 0)

        ventas = []
        for i, f in enumerate(filas_venta):
            kilos_renglon = Decimal(f[5] or 0)
            kilos_venta = kilos_por_venta.get(f[9], CERO)
            flete_venta = Decimal(f[8] or 0)
            flete_renglon = (
                (flete_venta * kilos_renglon / kilos_venta).quantize(DOS_DECIMALES)
                if kilos_venta > CERO
                else CERO
            )
            ventas.append(
                VentaEvento(
                    fecha=f[0], orden=i, cliente=f[2], tipo_queso_id=f[3], producto=f[4],
                    kilos=kilos_renglon, precio_kilo=Decimal(f[6] or 0),
                    valor_total=Decimal(f[7] or 0), gasto_monto=flete_renglon,
                )
            )

        # Queso que ya estaba en bodega y se cargo a mano, sin pasar por una
        # produccion. El signo del movimiento decide si suma queso (entrada, o
        # ajuste hacia arriba) o si lo saca (ajuste hacia abajo).
        existencias: list = []
        bajas: list = []
        for indice, fila in enumerate(
            ProductoRepository(self.db, empresa).movimientos_de_queso_sin_produccion()
        ):
            fecha, _, tipo_queso_id, tipo_queso, movimiento, cantidad, costo, referencia = fila
            kilos = Decimal(cantidad or 0)
            if movimiento == "ajuste" and kilos < CERO:
                bajas.append(
                    BajaEvento(
                        fecha=fecha, orden=indice, tipo_queso_id=tipo_queso_id,
                        kilos=-kilos,
                    )
                )
            elif kilos > CERO:
                existencias.append(
                    ExistenciaEvento(
                        fecha=fecha, orden=indice, tipo_queso_id=tipo_queso_id,
                        tipo_queso=tipo_queso, kilos=kilos,
                        costo_unitario=Decimal(costo or 0), referencia=referencia,
                    )
                )

        # La merma de los ciclos YA CERRADOS. Entra como una baja más —baja la
        # bodega y le resta a la utilidad, igual que un ajuste— pero CON DUEÑO: se
        # le carga a la tanda a la que le tocó en el reparto y no a la más vieja de
        # la cola. Si fuera FIFO, toda la merma del ciclo caería sobre la última
        # tanda, que es la única que todavía tiene kilos cuando se cierra.
        #
        # Y va con la FECHA DE LA TANDA, no con la del cierre. El queso se secó
        # desde el día en que se hizo, así que ponerla ahí deja el inventario
        # correcto en cualquier fecha intermedia y, sobre todo, hace que la tanda
        # todavía tenga sus kilos en la cola cuando le llega su merma.
        for indice, fila in enumerate(
            CicloDespachoRepository(self.db, empresa).mermas_para_lotes()
        ):
            fecha_tanda, produccion_id, tipo_queso_id, kilos = fila
            if Decimal(kilos or 0) <= CERO:
                continue
            bajas.append(
                BajaEvento(
                    fecha=fecha_tanda,
                    # El orden arranca alto para que la merma de una tanda quede
                    # después de los ajustes sueltos del mismo día: primero lo que
                    # alguien anotó, después lo que dedujo el cierre.
                    orden=1_000_000 + indice,
                    tipo_queso_id=tipo_queso_id,
                    kilos=Decimal(kilos),
                    produccion_id=produccion_id,
                    motivo=MOTIVO_MERMA_CICLO,
                )
            )

        return repartir_produccion(
            recepciones, producciones, ventas, existencias, bajas
        )


    def cifras_del_periodo(self, desde: date, hasta: date):
        """Las cifras que el estado de resultados necesita, cortadas al período.

        Se llama al MISMO reparto que la pantalla de lotes, así que la contabilidad
        y esa pantalla no pueden decir cosas distintas del mismo queso.
        """
        from app.modules.produccion.schemas import CifrasDelPeriodo, OrigenDelCosto

        reparto = self._reparto()
        en_rango = lambda f: desde <= f <= hasta  # noqa: E731

        queso_vendido = CERO
        costo_vendido = CERO
        transporte = CERO
        danado = CERO
        en_bodega = CERO
        origen: list = []
        for lote in reparto.lotes:
            # Cuánto de ESTE lote se vendió dentro del período: es lo que permite
            # decirle al usuario de qué producciones salió el costo que se le resta.
            kilos_lote = CERO
            costo_lote = CERO
            for v in lote.detalle_ventas:
                if en_rango(v.fecha):
                    queso_vendido += v.ingreso
                    costo_vendido += v.costo
                    transporte += v.gasto
                    kilos_lote += v.kilos
                    costo_lote += v.costo
            if kilos_lote > CERO:
                origen.append(
                    OrigenDelCosto(
                        fecha=lote.fecha, tipo_queso=lote.tipo_queso, origen=lote.origen,
                        kilos=kilos_lote, costo=_dinero(costo_lote),
                    )
                )
            for b in lote.detalle_bajas:
                if en_rango(b.fecha):
                    danado += b.costo
            # El queso en bodega se cuenta por la fecha en que se HIZO el lote: de lo
            # que se produjo en el período, esto sigue sin venderse hoy.
            if en_rango(lote.fecha):
                en_bodega += lote.costo_en_bodega

        # La leche sin usar se cuenta por la fecha en que LLEGÓ.
        leche_sin_usar = sum(
            (x.costo for x in reparto.detalle_leche_sin_usar if en_rango(x.fecha_recepcion)),
            CERO,
        )

        return CifrasDelPeriodo(
            queso_vendido=_dinero(queso_vendido),
            costo_queso_vendido=_dinero(costo_vendido),
            transporte_despachos=_dinero(transporte),
            queso_danado=_dinero(danado),
            leche_sin_usar=_dinero(leche_sin_usar),
            queso_en_bodega=_dinero(en_bodega),
            # Este NO se corta por período: es un aviso de que falta cargar una
            # producción, y esconderlo al cambiar de mes sería lo contrario de lo
            # que se busca.
            queso_vendido_sin_costo=_dinero(reparto.ingreso_sin_lote),
            # De la producción que más aportó a la que menos: así los renglones
            # importantes se ven primero y la cola no estorba.
            origen_del_costo=sorted(origen, key=lambda o: o.costo, reverse=True),
        )

    def panel(self, desde=None, hasta=None):
        from app.modules.produccion.schemas import (
            LecheDelLoteRead,
            LoteProduccionRead,
            LotesProduccionPanel,
            VentaDelLoteProduccionRead,
        )

        reparto = self._reparto()
        visibles = [
            l
            for l in reparto.lotes
            if (desde is None or l.fecha >= desde) and (hasta is None or l.fecha <= hasta)
        ]

        def dinero(valor):
            return Decimal(valor).quantize(Decimal("0.01"))

        filas = [
            LoteProduccionRead(
                fecha=l.fecha,
                tipo_queso=l.tipo_queso,
                litros_usados=l.litros_usados,
                kilos_producidos=l.kilos_producidos,
                merma=l.merma,
                rendimiento=Decimal(l.rendimiento).quantize(Decimal("0.0001")),
                costo_leche=dinero(l.costo_leche),
                costo_transporte=dinero(l.costo_transporte),
                costo_total=dinero(l.costo_total),
                costo_kilo=dinero(l.costo_kilo),
                kilos_vendidos=l.kilos_vendidos,
                kilos_en_bodega=l.kilos_en_bodega,
                ingresos=dinero(l.ingresos),
                gastos=dinero(l.gastos),
                costo_puesto_kilo=dinero(l.costo_puesto_kilo),
                costo_vendido=dinero(l.costo_vendido),
                costo_en_bodega=dinero(l.costo_en_bodega),
                utilidad=dinero(l.utilidad),
                precio_venta_kilo=dinero(l.precio_venta_kilo),
                vendido_completo=l.vendido_completo,
                litros_sin_recepcion=l.litros_sin_recepcion,
                origen=l.origen,
                referencia=l.referencia,
                kilos_de_baja=l.kilos_de_baja,
                costo_de_baja=dinero(l.costo_de_baja),
                kilos_merma_ciclo=l.kilos_merma_ciclo,
                costo_merma_ciclo=dinero(l.costo_merma_ciclo),
                sin_costo=l.sin_costo,
                detalle_leche=[
                    LecheDelLoteRead(
                        proveedor=d.proveedor, fecha_recepcion=d.fecha_recepcion,
                        litros=d.litros, costo_leche=dinero(d.costo_leche),
                        costo_transporte=dinero(d.costo_transporte), costo=dinero(d.costo),
                    )
                    for d in l.detalle_leche
                ],
                detalle_ventas=[
                    VentaDelLoteProduccionRead(
                        fecha=v.fecha, cliente=v.cliente, producto=v.producto,
                        kilos=v.kilos, kilos_venta=v.kilos_venta,
                        precio_kilo=dinero(v.precio_kilo), ingreso=dinero(v.ingreso),
                        costo=dinero(v.costo), gasto=dinero(v.gasto),
                        costo_puesto_kilo=dinero(v.costo_puesto_kilo),
                        utilidad=dinero(v.utilidad), partida=v.partida,
                    )
                    for v in l.detalle_ventas
                ],
            )
            for l in visibles
        ]

        mejor = peor = None
        if visibles:
            mejor = max(visibles, key=lambda l: l.utilidad).fecha
            peor = min(visibles, key=lambda l: l.utilidad).fecha

        return LotesProduccionPanel(
            lotes=filas,
            total_utilidad=sum((f.utilidad for f in filas), CERO),
            total_litros=sum((f.litros_usados for f in filas), CERO),
            total_kilos=sum((f.kilos_producidos for f in filas), CERO),
            total_costo=sum((f.costo_total for f in filas), CERO),
            total_ingresos=sum((f.ingresos for f in filas), CERO),
            total_gastos=sum((f.gastos for f in filas), CERO),
            # Estos cuatro son los que dejan encadenar las tarjetas de la pantalla:
            # lo vendido menos su costo, su baja y sus fletes da la utilidad; y ese
            # mismo costo vendido, más la baja, más lo que sigue en bodega, da el
            # costo del lote. Los dos desgloses cuadran al peso porque son sumas de
            # los mismos renglones ya redondeados.
            total_costo_vendido=sum((f.costo_vendido for f in filas), CERO),
            total_costo_de_baja=sum((f.costo_de_baja for f in filas), CERO),
            total_kilos_vendidos=sum((f.kilos_vendidos for f in filas), CERO),
            total_kilos_de_baja=sum((f.kilos_de_baja for f in filas), CERO),
            # Subconjunto de las bajas: la parte que se secó. No entra en ninguno
            # de los dos desgloses de arriba, que ya la llevan dentro de la baja.
            total_kilos_merma_ciclo=sum((f.kilos_merma_ciclo for f in filas), CERO),
            total_costo_merma_ciclo=sum((f.costo_merma_ciclo for f in filas), CERO),
            total_kilos_en_bodega=sum((f.kilos_en_bodega for f in filas), CERO),
            total_costo_en_bodega=sum((f.costo_en_bodega for f in filas), CERO),
            mejor=mejor,
            peor=peor,
            # Los avisos son del reparto COMPLETO y no del filtro: esconderlos al
            # cambiar de mes sería lo contrario de lo que se busca.
            kilos_sin_lote=reparto.kilos_sin_lote,
            kilos_existencia_sin_costo=reparto.kilos_existencia_sin_costo,
            ingreso_sin_lote=dinero(reparto.ingreso_sin_lote),
            litros_sin_recepcion=reparto.litros_sin_recepcion,
            litros_sin_usar=reparto.litros_sin_usar,
            costo_litros_sin_usar=dinero(reparto.costo_litros_sin_usar),
        )


# ---------------------------------------------- cierre de ciclo de despacho
class CicloDespachoService(BaseService[CicloDespacho]):
    """CERRAR EL CICLO DE DESPACHO: el momento en que la resta es honesta.

    EL PROBLEMA. El queso se pesa dos veces —al hacerlo y al venderlo— y entre
    las dos se seca: una tanda de 130 kg rinde 125 al despacharla. Como en
    Bogotá se vende por kilos sin saber de qué tanda salieron, esos 5 kg no se
    borran de la cuenta: se quedan en la cola FIFO como queso en bodega que no
    existe, con su costo, y corren de un lote al siguiente hasta acumularse en
    el último. El inventario queda inflado y la utilidad se ve mejor de lo real.

    LA SOLUCIÓN es aprovechar que el despacho va por ciclos de unos siete días.
    Al terminar uno, de esas tandas no debería quedar nada, así que ahí la resta
    se puede hacer sin adivinar:

        producido − vendido − lo que ya se bajó a mano = MERMA del ciclo

    CÓMO SE REPARTE. A prorrata de los kilos de cada tanda, dentro de cada tipo
    de queso, con el residuo del redondeo en la última tanda para que la suma dé
    EXACTO (ver `repartir_merma_ciclo` en lotes.py). No FIFO: al cerrar, las
    tandas viejas ya salieron y la única con kilos en la cola es la última, así
    que FIFO le cargaría toda la merma del ciclo a ella sola y esa tanda se
    vería pésima mientras las demás se verían perfectas.

    CÓMO SE REGISTRA. Cada parte se escribe como un AJUSTE DE INVENTARIO hacia
    abajo, marcado "Merma ciclo #xxxxxxxx". Con eso el stock del inventario baja
    de verdad, y la cadena de lotes lo recoge como una baja más: `kilos_de_baja`
    y `costo_de_baja` del lote, que la utilidad por lote ya resta y que el estado
    de resultados ya cuenta como queso dañado. No hubo que inventar un camino
    nuevo para que la plata llegue a la contabilidad.

    CÓMO NO SE CUENTA DOS VECES. Dos candados independientes:

    1. Contra los ajustes que el dueño ya anotó a mano dentro del ciclo: se
       RESTAN en la cuenta (`kilos_ajuste_manual`). Esos kilos ya salieron de la
       bodega y su costo ya se le restó al lote; volver a darlos por perdidos
       sería cobrar dos veces el mismo queso.
    2. Contra sí mismo: los ajustes que crea este cierre quedan marcados con una
       referencia propia, y tanto la cadena de lotes como la cuenta del próximo
       ciclo los excluyen. La cadena los lee de `ciclos_despacho_lotes`, que sabe
       de qué tanda es cada kilo; si además entraran como ajustes sueltos, se
       restarían dos veces.

    NO SE CIERRA SOLO. La propuesta llega con la cuenta hecha, pero cerrar es un
    POST explícito: es plata que se da por perdida. Y si la cuenta huele mal
    —merma negativa o desproporcionada— hay que aceptar las advertencias a mano.
    """

    repository_cls = CicloDespachoRepository
    modulo = "produccion"

    # ------------------------------------------------------------ utilidades
    @staticmethod
    def _nombre_de(inicio: date, fin: date) -> str:
        return f"Ciclo del {inicio.strftime('%d/%m/%Y')} al {fin.strftime('%d/%m/%Y')}"

    @staticmethod
    def _pct(parte: Decimal, total: Decimal) -> Decimal:
        if total <= CERO:
            return CERO
        return (parte * CIEN / total).quantize(DOS_DECIMALES)

    def _validar_rango(
        self, inicio: date, fin: date, excluir_id: uuid.UUID | None = None
    ) -> None:
        if fin < inicio:
            raise BusinessError(
                "El ciclo no puede terminar antes de empezar: empieza el "
                f"{inicio.strftime('%d/%m/%Y')} y terminaría el "
                f"{fin.strftime('%d/%m/%Y')}"
            )
        # Solo chocan los ciclos CERRADOS: son los únicos que tienen merma
        # registrada, y lo que no se puede repetir es cobrarla dos veces sobre el
        # mismo queso. Un ciclo reabierto ya no tiene merma, así que no estorba.
        cruzado = self.repo.solapado(inicio, fin, excluir_id=excluir_id)
        if cruzado is not None:
            raise BusinessError(
                f"Se cruza con el {cruzado.nombre} "
                f"({cruzado.fecha_inicio.strftime('%d/%m/%Y')} - "
                f"{cruzado.fecha_fin.strftime('%d/%m/%Y')}), que ya está cerrado. "
                "Los ciclos cerrados no se pueden solapar: la merma de esos días "
                "se cobraría dos veces sobre el mismo queso. Si hay que corregirlo, "
                "reabra ese ciclo primero."
            )

    def _lotes_service(self) -> LoteProduccionService:
        return LoteProduccionService(self.db, self.ctx)

    # ------------------------------------------------------------- la cuenta
    def calcular(self, desde: date, hasta: date) -> Any:
        """La cuenta del ciclo, SIN escribir nada: lo que el dueño va a aceptar.

        Se produjeron X kg, salieron Y, ya se habían bajado Z a mano, y la
        diferencia son W kg que valen $V. Todo desglosado por tipo de queso —no
        se compensa el doble crema que faltó con el campesino que sobró— y por
        tanda, que es el reparto exacto que se va a escribir si se cierra.
        """
        from app.modules.inventario.repository import ProductoRepository
        from app.modules.produccion.lotes import repartir_merma_ciclo
        from app.modules.produccion.schemas import (
            CicloPropuesta,
            MermaDelLoteRead,
            MermaDelTipoRead,
        )
        from app.modules.ventas.repository import VentaRepository

        empresa = self.ctx.empresa_id
        tandas = ProduccionRepository(self.db, empresa).tandas_del_rango(desde, hasta)

        # --- Lo que se produjo, por tipo de queso
        producido: dict[uuid.UUID, Decimal] = {}
        nombres: dict[uuid.UUID, str] = {}
        for _id, _fecha, tipo_id, tipo_nombre, peso, _suc in tandas:
            producido[tipo_id] = producido.get(tipo_id, CERO) + Decimal(peso or 0)
            nombres[tipo_id] = tipo_nombre

        # --- Lo que de verdad salió vendido
        vendido: dict[uuid.UUID, Decimal] = {}
        for tipo_id, tipo_nombre, kilos in VentaRepository(
            self.db, empresa
        ).kilos_de_queso_del_rango(desde, hasta):
            vendido[tipo_id] = Decimal(kilos or 0)
            nombres.setdefault(tipo_id, tipo_nombre)

        # --- Lo que el dueño YA había bajado (o subido) a mano dentro del ciclo
        bajado_a_mano: dict[uuid.UUID, Decimal] = {}
        subido_a_mano: dict[uuid.UUID, Decimal] = {}
        for tipo_id, tipo_nombre, abajo, arriba in ProductoRepository(
            self.db, empresa
        ).ajustes_de_queso_del_rango(desde, hasta):
            bajado_a_mano[tipo_id] = Decimal(abajo or 0)
            subido_a_mano[tipo_id] = Decimal(arriba or 0)
            nombres.setdefault(tipo_id, tipo_nombre)

        # --- El costo por kilo de cada tanda, para poder decir cuánto VALE la
        # merma ANTES de registrarla. Sale de la misma cadena que pinta la
        # utilidad por lote, así que la cifra que se muestra aquí es exactamente
        # la que va a aparecer allá después de cerrar.
        costo_kilo: dict[uuid.UUID, Decimal] = {}
        for lote in self._lotes_service()._reparto().lotes:
            if lote.produccion_id is not None:
                costo_kilo[lote.produccion_id] = lote.costo_kilo

        advertencias: list[str] = []
        por_tipo: list[MermaDelTipoRead] = []
        merma_por_tipo: dict[uuid.UUID, Decimal] = {}
        for tipo_id in sorted(
            set(producido) | set(vendido) | set(bajado_a_mano),
            key=lambda t: nombres.get(t, ""),
        ):
            kilos_prod = producido.get(tipo_id, CERO)
            kilos_vend = vendido.get(tipo_id, CERO)
            kilos_ajus = bajado_a_mano.get(tipo_id, CERO)
            kilos_entr = subido_a_mano.get(tipo_id, CERO)
            merma = kilos_prod - kilos_vend - kilos_ajus
            nombre = nombres.get(tipo_id, "Queso")

            if merma < CERO:
                advertencias.append(
                    f"{nombre}: salieron {abs(merma)} kg MÁS de los que se "
                    f"produjeron en el ciclo ({kilos_vend} kg vendidos y "
                    f"{kilos_ajus} kg bajados a mano, contra {kilos_prod} kg "
                    "producidos). Eso no es merma: o falta cargar una tanda, o se "
                    "despachó queso de un ciclo anterior. A este tipo de queso no "
                    "se le va a cargar merma."
                )
            elif merma > CERO and self._pct(merma, kilos_prod) > MERMA_SOSPECHOSA_PCT:
                advertencias.append(
                    f"{nombre}: la merma da {merma} kg, el "
                    f"{self._pct(merma, kilos_prod)}% de lo producido. El queso "
                    "que se seca pierde alrededor del 4%; un porcentaje así suele "
                    "ser una venta sin anotar, no queso secándose."
                )
            if kilos_entr > CERO:
                advertencias.append(
                    f"{nombre}: dentro del ciclo se cargaron {kilos_entr} kg a "
                    "mano al inventario, que no salieron de ninguna tanda. No "
                    "entran en la cuenta, pero si ese queso se despachó, la merma "
                    "está saliendo más alta de lo que fue."
                )

            # La merma negativa NO se reparte: no se le puede quitar peso a una
            # tanda que ya salió. Queda en cero y el aviso de arriba la explica.
            merma_por_tipo[tipo_id] = merma if merma > CERO else CERO
            por_tipo.append(
                MermaDelTipoRead(
                    tipo_queso_id=tipo_id,
                    tipo_queso=nombre,
                    kilos_producidos=kilos_prod,
                    kilos_vendidos=kilos_vend,
                    kilos_ajuste_manual=kilos_ajus,
                    kilos_entrada_manual=kilos_entr,
                    kilos_merma=merma,
                    porcentaje=self._pct(merma, kilos_prod),
                )
            )

        # --- El reparto entre las tandas, tipo por tipo
        por_lote: list[MermaDelLoteRead] = []
        for tipo_id, merma in merma_por_tipo.items():
            del_tipo = [t for t in tandas if t[2] == tipo_id]
            if merma <= CERO or not del_tipo:
                continue
            partes = repartir_merma_ciclo([Decimal(t[4] or 0) for t in del_tipo], merma)
            for (prod_id, fecha, _t, tipo_nombre, peso, _suc), kilos in zip(
                del_tipo, partes
            ):
                if kilos <= CERO:
                    continue
                por_lote.append(
                    MermaDelLoteRead(
                        produccion_id=prod_id,
                        fecha=fecha,
                        tipo_queso=tipo_nombre,
                        kilos_producidos=Decimal(peso or 0),
                        kilos_merma=kilos,
                        costo_merma=_dinero(kilos * costo_kilo.get(prod_id, CERO)),
                    )
                )
        # De la tanda más vieja a la más nueva, que es como se leen los desgloses
        por_lote.sort(key=lambda l: (l.fecha, l.tipo_queso))

        total_producido = sum(producido.values(), CERO)
        total_merma = sum(merma_por_tipo.values(), CERO)
        hoy = date.today()
        ultimo = self.repo.ultimo_cierre()
        arranque = (ultimo + timedelta(days=1)) if ultimo else desde
        dias_corridos = max((hoy - arranque).days + 1, 0)

        return CicloPropuesta(
            fecha_inicio=desde,
            fecha_fin=hasta,
            dias=(hasta - desde).days + 1,
            nombre_sugerido=self._nombre_de(desde, hasta),
            kilos_producidos=total_producido,
            kilos_vendidos=sum(vendido.values(), CERO),
            kilos_ajuste_manual=sum(bajado_a_mano.values(), CERO),
            kilos_merma=total_merma,
            # El costo total es la SUMA de los renglones por tanda ya redondeados,
            # no un cálculo aparte: calculado aparte podría diferir en pesos de la
            # suma de la columna, y el dueño suma esa columna a mano.
            costo_merma=sum((l.costo_merma for l in por_lote), CERO),
            porcentaje=self._pct(total_merma, total_producido),
            por_tipo=por_tipo,
            por_lote=por_lote,
            advertencias=advertencias,
            toca_cerrar=dias_corridos >= DIAS_DEL_CICLO,
            dias_desde_ultimo_cierre=dias_corridos,
            vacio=not tandas and not vendido,
        )

    # ------------------------------------------------------------- propuesta
    def propuesta(self, desde: date | None = None, hasta: date | None = None) -> Any:
        """El ciclo que el sistema PROPONE cerrar ahora, con su cuenta ya hecha.

        El arranque es el día siguiente al último cierre; si nunca se ha cerrado
        uno, la fecha de la primera tanda que haya. El final es siete días
        después, sin pasar de hoy: no se puede cerrar el futuro.

        Que el sistema proponga en vez de esperar es medio invento del asunto. El
        ciclo se repite cada semana; si hubiera que acordarse de abrirlo y
        cerrarlo, en tres semanas nadie lo haría y los kilos fantasma volverían.
        """
        hoy = date.today()
        if desde is None:
            ultimo = self.repo.ultimo_cierre()
            if ultimo is not None:
                desde = ultimo + timedelta(days=1)
            else:
                desde = ProduccionRepository(
                    self.db, self.ctx.empresa_id
                ).primera_fecha()
        if desde is None:
            # No hay ni una tanda cargada: no hay nada que cerrar todavía.
            return None
        if hasta is None:
            hasta = min(desde + timedelta(days=DIAS_DEL_CICLO - 1), hoy)
        if hasta < desde:
            # El último cierre llega hasta hoy o más allá: no quedan días sueltos.
            hasta = desde
        return self.calcular(desde, hasta)

    # ------------------------------------------------------ cerrar y reabrir
    def cerrar(self, payload: Any) -> CicloDespacho:
        """Registra la merma del ciclo y la baja de la bodega. ESTO SÍ ESCRIBE.

        Crea el ciclo, reparte su merma entre las tandas y escribe un ajuste de
        inventario por cada una. Es plata que se da por perdida, así que no pasa
        con advertencias sin que alguien las acepte a mano.
        """
        datos = payload if isinstance(payload, dict) else payload.model_dump()
        desde: date = datos["fecha_inicio"]
        hasta: date = datos["fecha_fin"]
        self._validar_rango(desde, hasta)

        cuenta = self.calcular(desde, hasta)
        if cuenta.advertencias and not datos.get("aceptar_advertencias"):
            raise BusinessError(
                "La cuenta de este ciclo no cuadra como debería. "
                + " ".join(cuenta.advertencias)
                + " Revísela; si aun así quiere cerrarlo, acepte las advertencias.",
                extra={"advertencias": cuenta.advertencias},
            )
        if cuenta.vacio:
            raise BusinessError(
                f"Entre el {desde.strftime('%d/%m/%Y')} y el "
                f"{hasta.strftime('%d/%m/%Y')} no hay tandas ni despachos. No hay "
                "nada que cerrar; revise las fechas del ciclo."
            )
        if cuenta.kilos_merma <= CERO and not cuenta.advertencias:
            raise BusinessError(
                "En este ciclo no quedó merma que registrar: se despachó todo lo "
                "que se produjo. No hace falta cerrarlo."
            )

        # Los ciclos REABIERTOS que caen en este rango son el rastro de un cierre
        # que ya se deshizo: no tienen merma y sus cifras están en cero. Se borran
        # para no dejar dos filas de los mismos días en la pantalla, una llena y
        # otra vacía, que es la clase de cosa que hace dudar de todo el tablero.
        # El borrado es en blando y queda en la auditoría, así que el rastro no se
        # pierde: deja de estorbar, nada más.
        for viejo in self.repo.solapados(desde, hasta, solo_cerrados=False):
            self.eliminar(viejo.id)

        ciclo = self.crear(
            {
                "nombre": datos.get("nombre") or cuenta.nombre_sugerido,
                "fecha_inicio": desde,
                "fecha_fin": hasta,
                "notas": datos.get("notas"),
                "kilos_producidos": cuenta.kilos_producidos,
                "kilos_vendidos": cuenta.kilos_vendidos,
                "kilos_ajuste_manual": cuenta.kilos_ajuste_manual,
                "kilos_merma": cuenta.kilos_merma,
                "costo_merma": cuenta.costo_merma,
                # Queda escrito con qué avisos se cerró: alguien los leyó y aceptó
                # igual, y eso hay que poder auditarlo después.
                "advertencias": (
                    " | ".join(cuenta.advertencias)[:1000]
                    if cuenta.advertencias
                    else None
                ),
                "cerrado_at": datetime.now(timezone.utc),
            }
        )

        produccion_svc = ProduccionService(self.db, self.ctx)
        marca = f"{REFERENCIA_MERMA_CICLO} #{str(ciclo.id)[:8]}"
        for fila in cuenta.por_lote:
            tanda = self.db.get(Produccion, fila.produccion_id)
            producto = produccion_svc._producto_terminado(tanda.tipo_queso_id)
            # Ajuste con cantidad NEGATIVA: es el mismo movimiento que usa el
            # dueño cuando anota "se dañaron 3 kg", así que el stock, el kardex y
            # la cadena de lotes lo entienden sin tocar nada.
            movimiento = MovimientoInventario(
                empresa_id=self.ctx.empresa_id,
                producto_id=producto.id,
                sucursal_id=tanda.sucursal_id,
                # La fecha de la TANDA, no la del cierre: el queso se secó desde
                # el día en que se hizo, así la bodega queda bien en cualquier día
                # intermedio y —lo importante— la merma alcanza a su tanda con los
                # kilos todavía en la cola, que es lo que permite repartir a
                # prorrata en vez de FIFO.
                fecha=fila.fecha,
                tipo=MOVIMIENTO_AJUSTE,
                cantidad=-fila.kilos_merma,
                referencia=f"{marca} · tanda {fila.fecha.strftime('%d/%m')}",
                observaciones=(
                    "Merma del cierre de ciclo: queso que se secó entre que se "
                    "pesó al hacerlo y se pesó al venderlo."
                ),
                created_by=self.ctx.user_id,
                updated_by=self.ctx.user_id,
            )
            self.db.add(movimiento)
            self.db.flush()
            self.db.add(
                CicloDespachoLote(
                    empresa_id=self.ctx.empresa_id,
                    ciclo_id=ciclo.id,
                    produccion_id=fila.produccion_id,
                    tipo_queso_id=tanda.tipo_queso_id,
                    movimiento_id=movimiento.id,
                    fecha_produccion=fila.fecha,
                    kilos_producidos=fila.kilos_producidos,
                    kilos_merma=fila.kilos_merma,
                    costo_merma=fila.costo_merma,
                    created_by=self.ctx.user_id,
                    updated_by=self.ctx.user_id,
                )
            )
        self.db.flush()
        return ciclo

    def reabrir(self, entity_id: uuid.UUID) -> CicloDespacho:
        """Deshace la merma de un ciclo que se cerró por equivocación.

        Borra los ajustes de inventario que creó el cierre y sus filas de
        reparto. Con eso el queso vuelve a la bodega, el costo vuelve al lote y
        la utilidad por lote vuelve a lo que decía antes: la merma no deja rastro
        en ninguna cifra, solo en la auditoría.

        El borrado es en blando, como todo en el sistema: el movimiento sigue ahí
        para quien audite, simplemente deja de contar.
        """
        ciclo = self.repo.get_or_fail(entity_id)
        if ciclo.cerrado_at is None:
            raise BusinessError(f"El {ciclo.nombre} ya está abierto")

        detalles = CicloDespachoLoteRepository(self.db, self.ctx.empresa_id).del_ciclo(
            ciclo.id
        )
        ahora = datetime.now(timezone.utc)
        for detalle in detalles:
            if detalle.movimiento_id is not None:
                movimiento = self.db.get(MovimientoInventario, detalle.movimiento_id)
                # Se comprueba la empresa aunque el id venga de una fila propia: el
                # día que alguien pase un id por parámetro, el filtro ya está.
                if (
                    movimiento is not None
                    and movimiento.empresa_id == self.ctx.empresa_id
                ):
                    movimiento.deleted_at = ahora
                    movimiento.updated_by = self.ctx.user_id
            detalle.deleted_at = ahora
            detalle.updated_by = self.ctx.user_id
        self.db.flush()

        # Las cifras se dejan en cero: la fila queda como un ciclo abierto, con su
        # rango, sin nada aceptado. Dejar las viejas haría creer que la merma
        # sigue registrada cuando ya se deshizo.
        return self.actualizar(
            entity_id,
            {
                "cerrado_at": None,
                "kilos_merma": CERO,
                "costo_merma": CERO,
                "kilos_producidos": CERO,
                "kilos_vendidos": CERO,
                "kilos_ajuste_manual": CERO,
                "advertencias": None,
            },
        )

    def validar_eliminar(self, obj: CicloDespacho) -> None:
        if obj.cerrado_at is not None:
            raise BusinessError(
                f"El {obj.nombre} está cerrado y su merma ya está registrada en "
                "el inventario. Reábralo primero: así la merma se deshace y no "
                "quedan kilos dados de baja sin un ciclo que los explique."
            )

    # ----------------------------------------------------------------- panel
    def panel(self) -> Any:
        from app.modules.produccion.schemas import CiclosPanel

        filas = [self._leer(c) for c in self.repo.vigentes()]
        return CiclosPanel(
            ciclos=filas,
            # Suma exacta de las filas de la lista, no un recálculo del histórico:
            # con un recálculo aparte, los días que no caen en ningún ciclo harían
            # que el total diera más que la suma de la lista.
            total_kilos_producidos=sum((f.kilos_producidos for f in filas), CERO),
            total_kilos_merma=sum((f.kilos_merma for f in filas), CERO),
            total_costo_merma=sum((f.costo_merma for f in filas), CERO),
            propuesta=self.propuesta(),
        )

    def _leer(self, ciclo: CicloDespacho) -> Any:
        from app.modules.produccion.schemas import CicloDespachoRead, MermaDelLoteRead

        detalles = CicloDespachoLoteRepository(self.db, self.ctx.empresa_id).del_ciclo(
            ciclo.id
        )
        nombres = {
            t.id: t.nombre
            for t in self.db.scalars(
                select(TipoQueso).where(
                    TipoQueso.empresa_id == self.ctx.empresa_id,
                    TipoQueso.deleted_at.is_(None),
                )
            )
        }
        return CicloDespachoRead(
            id=ciclo.id,
            empresa_id=ciclo.empresa_id,
            estado=ciclo.estado,
            created_at=ciclo.created_at,
            updated_at=ciclo.updated_at,
            nombre=ciclo.nombre,
            fecha_inicio=ciclo.fecha_inicio,
            fecha_fin=ciclo.fecha_fin,
            notas=ciclo.notas,
            cerrado=ciclo.cerrado,
            cerrado_at=ciclo.cerrado_at,
            kilos_producidos=ciclo.kilos_producidos,
            kilos_vendidos=ciclo.kilos_vendidos,
            kilos_ajuste_manual=ciclo.kilos_ajuste_manual,
            kilos_merma=ciclo.kilos_merma,
            costo_merma=ciclo.costo_merma,
            porcentaje=self._pct(
                Decimal(ciclo.kilos_merma), Decimal(ciclo.kilos_producidos)
            ),
            advertencias=(
                [a.strip() for a in ciclo.advertencias.split("|")]
                if ciclo.advertencias
                else []
            ),
            dias=(ciclo.fecha_fin - ciclo.fecha_inicio).days + 1,
            por_lote=[
                MermaDelLoteRead(
                    produccion_id=d.produccion_id,
                    fecha=d.fecha_produccion,
                    tipo_queso=nombres.get(d.tipo_queso_id, "Queso"),
                    kilos_producidos=d.kilos_producidos,
                    kilos_merma=d.kilos_merma,
                    costo_merma=d.costo_merma,
                )
                for d in detalles
            ],
        )
