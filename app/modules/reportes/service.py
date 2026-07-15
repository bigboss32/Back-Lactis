"""Dashboard ejecutivo y exportaciones a Excel de los módulos principales."""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.exceptions import BusinessError
from app.modules.gastos.models import CategoriaGasto, Gasto
from app.modules.gastos.repository import GastoRepository
from app.modules.liquidaciones.models import Liquidacion
from app.modules.notificaciones.models import Notificacion
from app.modules.produccion.models import Produccion, TipoQueso
from app.modules.proveedores.models import Proveedor
from app.modules.recepcion.models import RecepcionLeche
from app.modules.reportes.schemas import DashboardResponse, SerieCategoria, SerieDia
from app.modules.ventas.models import Venta
from app.utils.export import rows_to_excel

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

    # ------------------------------------------------------- export recepciones
    def exportar_recepciones_quincena(self, desde: date, hasta: date) -> tuple[bytes, str]:
        """Grilla proveedor × día, como la hoja 'LITROS Y TRANSPORTE' del Excel original."""
        recepciones = self.db.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.empresa_id == self.ctx.empresa_id,
                RecepcionLeche.deleted_at.is_(None),
                RecepcionLeche.fecha.between(desde, hasta),
            )
        ).all()

        dias = []
        d = desde
        while d <= hasta:
            dias.append(d)
            d += timedelta(days=1)

        por_proveedor: dict = {}
        for r in recepciones:
            info = por_proveedor.setdefault(
                r.proveedor_id,
                {
                    "nombre": r.proveedor.nombre if r.proveedor else "-",
                    "vereda": r.proveedor.vereda if r.proveedor else "",
                    "litros": {},
                    "precio": r.precio_litro,
                    "bruto": CERO,
                    "descuentos": CERO,
                    "bonificaciones": CERO,
                    "neto": CERO,
                    "transporte": CERO,
                },
            )
            info["litros"][r.fecha] = info["litros"].get(r.fecha, CERO) + r.cantidad_litros
            info["bruto"] += r.valor_bruto
            info["descuentos"] += r.descuentos
            info["bonificaciones"] += r.bonificaciones
            info["neto"] += r.valor_neto
            info["transporte"] += r.valor_transporte

        headers = (
            ["Proveedor", "Vereda"]
            + [d.strftime("%d/%m") for d in dias]
            + ["Total Litros", "Precio/L", "Valor Bruto", "Descuentos", "Bonificaciones", "Valor Neto", "Transporte"]
        )
        rows = []
        for info in sorted(por_proveedor.values(), key=lambda x: x["nombre"]):
            litros_dia = [info["litros"].get(d, CERO) for d in dias]
            rows.append(
                [info["nombre"], info["vereda"]]
                + litros_dia
                + [
                    sum(litros_dia, CERO), info["precio"], info["bruto"],
                    info["descuentos"], info["bonificaciones"], info["neto"], info["transporte"],
                ]
            )
        money_start = len(dias) + 4
        excel = rows_to_excel(
            title=f"Litros y transporte del {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}",
            headers=headers,
            rows=rows,
            sheet_name="Litros y Transporte",
            money_columns=tuple(range(money_start, money_start + 6)),
        )
        return excel, f"recepciones_{desde.isoformat()}_{hasta.isoformat()}.xlsx"

    # ------------------------------------------------------------ export ventas
    def exportar_ventas(self, desde: date, hasta: date) -> tuple[bytes, str]:
        ventas = self.db.scalars(
            select(Venta).where(
                Venta.empresa_id == self.ctx.empresa_id,
                Venta.deleted_at.is_(None),
                Venta.fecha.between(desde, hasta),
            ).order_by(Venta.fecha)
        ).all()
        rows = [
            [
                v.numero, v.fecha, v.tipo, v.cliente.nombre if v.cliente else "-",
                v.subtotal, v.descuento, v.total, v.pagado, v.saldo, v.estado,
            ]
            for v in ventas
        ]
        excel = rows_to_excel(
            title=f"Ventas del {desde.isoformat()} al {hasta.isoformat()}",
            headers=["N°", "Fecha", "Tipo", "Cliente", "Subtotal", "Descuento", "Total", "Pagado", "Saldo", "Estado"],
            rows=rows,
            sheet_name="Ventas",
            money_columns=(5, 6, 7, 8, 9),
        )
        return excel, f"ventas_{desde.isoformat()}_{hasta.isoformat()}.xlsx"

    # ------------------------------------------------------------ export gastos
    def exportar_gastos(self, desde: date, hasta: date) -> tuple[bytes, str]:
        gastos = self.db.scalars(
            select(Gasto)
            .join(CategoriaGasto, CategoriaGasto.id == Gasto.categoria_id)
            .where(
                Gasto.empresa_id == self.ctx.empresa_id,
                Gasto.deleted_at.is_(None),
                Gasto.fecha.between(desde, hasta),
            )
            .order_by(Gasto.fecha)
        ).all()
        rows = [
            [
                g.fecha, g.categoria.nombre if g.categoria else "-", g.concepto,
                g.proveedor or "", g.numero_factura or "", g.valor,
            ]
            for g in gastos
        ]
        excel = rows_to_excel(
            title=f"Gastos del {desde.isoformat()} al {hasta.isoformat()}",
            headers=["Fecha", "Categoría", "Concepto", "Proveedor", "Factura", "Valor"],
            rows=rows,
            sheet_name="Gastos",
            money_columns=(6,),
        )
        return excel, f"gastos_{desde.isoformat()}_{hasta.isoformat()}.xlsx"
