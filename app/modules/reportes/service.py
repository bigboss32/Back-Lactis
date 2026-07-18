"""Dashboard ejecutivo con indicadores del negocio."""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.exceptions import BusinessError
from app.modules.gastos.models import Gasto
from app.modules.gastos.repository import GastoRepository
from app.modules.liquidaciones.models import Liquidacion
from app.modules.notificaciones.models import Notificacion
from app.modules.produccion.models import Produccion, TipoQueso
from app.modules.proveedores.models import Proveedor
from app.modules.recepcion.models import RecepcionLeche
from app.modules.reportes.schemas import DashboardResponse, SerieCategoria, SerieDia
from app.modules.ventas.models import Venta

CERO = Decimal("0")


def _inicio_quincena(hoy: date) -> date:
    return hoy.replace(day=1) if hoy.day <= 15 else hoy.replace(day=16)


class ReporteService:
    modulo = "reportes"

    def __init__(self, db: Session, ctx: RequestContext):
        self.db = db
        self.ctx = ctx
        if ctx.empresa_id is None:
            raise BusinessError("Debe operar en el contexto de una empresa (header X-Empresa-Id)")

    def _sum(self, columna, modelo, *criterios) -> Decimal:
        return self.db.scalar(
            select(func.coalesce(func.sum(columna), 0)).where(
                modelo.empresa_id == self.ctx.empresa_id,
                modelo.deleted_at.is_(None),
                *criterios,
            )
        ) or CERO

    # --------------------------------------------------------------- dashboard
    def dashboard(self) -> DashboardResponse:
        hoy = date.today()
        inicio_quincena = _inicio_quincena(hoy)
        inicio_mes = hoy.replace(day=1)
        hace_30 = hoy - timedelta(days=30)
        empresa = self.ctx.empresa_id

        # Rangos del período anterior (para el comparativo ▲▼).
        fin_quincena_anterior = inicio_quincena - timedelta(days=1)
        inicio_quincena_anterior = _inicio_quincena(fin_quincena_anterior)
        fin_mes_anterior = inicio_mes - timedelta(days=1)
        inicio_mes_anterior = fin_mes_anterior.replace(day=1)

        litros_por_dia = [
            SerieDia(fecha=f, valor=v or CERO)
            for f, v in self.db.execute(
                select(RecepcionLeche.fecha, func.sum(RecepcionLeche.cantidad_litros))
                .where(
                    RecepcionLeche.empresa_id == empresa,
                    RecepcionLeche.deleted_at.is_(None),
                    RecepcionLeche.fecha >= hace_30,
                )
                .group_by(RecepcionLeche.fecha)
                .order_by(RecepcionLeche.fecha)
            ).all()
        ]
        ventas_por_dia = [
            SerieDia(fecha=f, valor=v or CERO)
            for f, v in self.db.execute(
                select(Venta.fecha, func.sum(Venta.total))
                .where(
                    Venta.empresa_id == empresa,
                    Venta.deleted_at.is_(None),
                    Venta.estado != "anulada",
                    Venta.fecha >= hace_30,
                )
                .group_by(Venta.fecha)
                .order_by(Venta.fecha)
            ).all()
        ]
        gastos_por_categoria = [
            SerieCategoria(etiqueta=fila.nombre, valor=fila.total or CERO)
            for fila in GastoRepository(self.db, empresa).total_por_categoria(inicio_mes, hoy)
        ]
        produccion_por_tipo = [
            SerieCategoria(etiqueta=nombre, valor=total or CERO)
            for nombre, total in self.db.execute(
                select(TipoQueso.nombre, func.sum(Produccion.peso_kg))
                .join(TipoQueso, TipoQueso.id == Produccion.tipo_queso_id)
                .where(
                    Produccion.empresa_id == empresa,
                    Produccion.deleted_at.is_(None),
                    Produccion.fecha >= inicio_mes,
                )
                .group_by(TipoQueso.nombre)
            ).all()
        ]
        top_proveedores = [
            SerieCategoria(etiqueta=nombre, valor=total or CERO)
            for nombre, total in self.db.execute(
                select(Proveedor.nombre, func.sum(RecepcionLeche.cantidad_litros))
                .join(Proveedor, Proveedor.id == RecepcionLeche.proveedor_id)
                .where(
                    RecepcionLeche.empresa_id == empresa,
                    RecepcionLeche.deleted_at.is_(None),
                    RecepcionLeche.fecha >= inicio_quincena,
                )
                .group_by(Proveedor.nombre)
                .order_by(func.sum(RecepcionLeche.cantidad_litros).desc())
                .limit(5)
            ).all()
        ]

        alertas = self.db.scalar(
            select(func.count(Notificacion.id)).where(
                Notificacion.empresa_id == empresa,
                Notificacion.deleted_at.is_(None),
                Notificacion.leida.is_(False),
            )
        ) or 0

        return DashboardResponse(
            fecha=hoy,
            litros_hoy=self._sum(
                RecepcionLeche.cantidad_litros, RecepcionLeche, RecepcionLeche.fecha == hoy
            ),
            litros_quincena=self._sum(
                RecepcionLeche.cantidad_litros, RecepcionLeche, RecepcionLeche.fecha >= inicio_quincena
            ),
            valor_leche_quincena=self._sum(
                RecepcionLeche.valor_neto, RecepcionLeche, RecepcionLeche.fecha >= inicio_quincena
            ),
            produccion_kg_mes=self._sum(Produccion.peso_kg, Produccion, Produccion.fecha >= inicio_mes),
            ventas_mes=self._sum(
                Venta.total, Venta, Venta.estado != "anulada", Venta.fecha >= inicio_mes
            ),
            gastos_mes=self._sum(
                Gasto.valor, Gasto, Gasto.estado == "activo", Gasto.fecha >= inicio_mes
            ),
            litros_quincena_anterior=self._sum(
                RecepcionLeche.cantidad_litros, RecepcionLeche,
                RecepcionLeche.fecha >= inicio_quincena_anterior,
                RecepcionLeche.fecha <= fin_quincena_anterior,
            ),
            produccion_kg_mes_anterior=self._sum(
                Produccion.peso_kg, Produccion,
                Produccion.fecha >= inicio_mes_anterior, Produccion.fecha <= fin_mes_anterior,
            ),
            ventas_mes_anterior=self._sum(
                Venta.total, Venta, Venta.estado != "anulada",
                Venta.fecha >= inicio_mes_anterior, Venta.fecha <= fin_mes_anterior,
            ),
            gastos_mes_anterior=self._sum(
                Gasto.valor, Gasto, Gasto.estado == "activo",
                Gasto.fecha >= inicio_mes_anterior, Gasto.fecha <= fin_mes_anterior,
            ),
            cartera_pendiente=self._sum(
                Venta.total - Venta.pagado, Venta, Venta.estado.in_(["pendiente", "parcial"])
            ),
            liquidaciones_por_pagar=self._sum(
                Liquidacion.saldo, Liquidacion, Liquidacion.estado.in_(["borrador", "aprobada"])
            ),
            alertas_no_leidas=alertas,
            litros_por_dia=litros_por_dia,
            ventas_por_dia=ventas_por_dia,
            gastos_por_categoria=gastos_por_categoria,
            produccion_por_tipo=produccion_por_tipo,
            top_proveedores=top_proveedores,
        )
