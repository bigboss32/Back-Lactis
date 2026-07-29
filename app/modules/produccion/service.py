import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.common.service import BaseService
from app.core.exceptions import ConflictError
from app.core.pagination import PageParams
from app.modules.inventario.models import (
    CATEGORIA_PRODUCTO_TERMINADO,
    MOVIMIENTO_ENTRADA,
    MOVIMIENTO_SALIDA,
    MovimientoInventario,
    Producto,
)
from app.modules.produccion.models import Produccion, TipoQueso
from app.modules.produccion.repository import ProduccionRepository, TipoQuesoRepository

CERO = Decimal("0")


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

    def panel(self, desde=None, hasta=None):
        from app.modules.inventario.repository import ProductoRepository
        from app.modules.produccion.lotes import (
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
                merma=Decimal(f[6] or 0),
            )
            for i, f in enumerate(ProduccionRepository(self.db, empresa).eventos_para_lotes())
        ]
        ventas = [
            VentaEvento(
                fecha=f[0], orden=i, cliente=f[2], tipo_queso_id=f[3], producto=f[4],
                kilos=Decimal(f[5] or 0), precio_kilo=Decimal(f[6] or 0),
                valor_total=Decimal(f[7] or 0),
            )
            for i, f in enumerate(VentaRepository(self.db, empresa).eventos_para_lotes())
        ]

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

        reparto = repartir_produccion(
            recepciones, producciones, ventas, existencias, bajas
        )
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
                        costo=dinero(v.costo), utilidad=dinero(v.utilidad),
                        partida=v.partida,
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
