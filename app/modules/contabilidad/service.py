"""Contabilidad gerencial calculada sobre los módulos operativos:
libro diario consolidado, estado de resultados y balance de disponible.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.exceptions import BusinessError
from app.modules.bancos.models import CuentaBancaria, MovimientoBancario
from app.modules.caja.models import CajaDiaria, MovimientoCaja
from app.modules.contabilidad.schemas import (
    AsientoLibroDiario,
    BalanceResponse,
    EstadoResultados,
    LibroDiarioResponse,
    LineaCategoria,
)
from app.modules.gastos.repository import GastoRepository
from app.modules.liquidaciones.models import Liquidacion
from app.modules.recepcion.models import RecepcionLeche
from app.modules.ventas.models import Venta

CERO = Decimal("0")


class ContabilidadService:
    modulo = "contabilidad"

    def __init__(self, db: Session, ctx: RequestContext):
        self.db = db
        self.ctx = ctx
        if ctx.empresa_id is None:
            raise BusinessError("Debe operar en el contexto de una empresa (header X-Empresa-Id)")

    # ------------------------------------------------------------ libro diario
    def libro_diario(self, desde: date, hasta: date) -> LibroDiarioResponse:
        """Flujo de caja real: SOLO movimientos de caja y bancos del período.

        No se suman por separado los pagos de venta, gastos ni recepciones: son
        documentos cuyo dinero se refleja cuando se mueve por caja/banco. Contarlos
        además de los movimientos de tesorería contaría el mismo dinero dos veces.
        """
        asientos: list[AsientoLibroDiario] = []
        empresa = self.ctx.empresa_id

        movimientos_caja = self.db.scalars(
            select(MovimientoCaja)
            .join(CajaDiaria, CajaDiaria.id == MovimientoCaja.caja_id)
            .where(
                MovimientoCaja.empresa_id == empresa,
                MovimientoCaja.deleted_at.is_(None),
                CajaDiaria.fecha.between(desde, hasta),
            )
        ).all()
        asientos += [
            AsientoLibroDiario(
                fecha=m.caja.fecha, origen="caja", concepto=m.concepto,
                ingreso=m.valor if m.tipo == "ingreso" else CERO,
                egreso=m.valor if m.tipo == "egreso" else CERO,
                referencia=m.referencia,
            )
            for m in movimientos_caja
        ]

        movimientos_banco = self.db.scalars(
            select(MovimientoBancario).where(
                MovimientoBancario.empresa_id == empresa,
                MovimientoBancario.deleted_at.is_(None),
                MovimientoBancario.fecha.between(desde, hasta),
            )
        ).all()
        asientos += [
            AsientoLibroDiario(
                fecha=m.fecha, origen="banco", concepto=m.concepto,
                ingreso=m.valor if m.tipo == "ingreso" else CERO,
                egreso=m.valor if m.tipo == "egreso" else CERO,
                referencia=m.referencia,
            )
            for m in movimientos_banco
        ]

        asientos.sort(key=lambda a: a.fecha)
        return LibroDiarioResponse(
            desde=desde,
            hasta=hasta,
            total_ingresos=sum((a.ingreso for a in asientos), CERO),
            total_egresos=sum((a.egreso for a in asientos), CERO),
            asientos=asientos,
        )

    # ----------------------------------------------------- estado de resultados
    def estado_resultados(self, desde: date, hasta: date) -> EstadoResultados:
        empresa = self.ctx.empresa_id

        ingresos_ventas = self.db.scalar(
            select(func.coalesce(func.sum(Venta.total), 0)).where(
                Venta.empresa_id == empresa,
                Venta.deleted_at.is_(None),
                Venta.estado != "anulada",
                Venta.fecha.between(desde, hasta),
            )
        ) or CERO

        costo_leche = self.db.scalar(
            select(func.coalesce(func.sum(RecepcionLeche.valor_neto), 0)).where(
                RecepcionLeche.empresa_id == empresa,
                RecepcionLeche.deleted_at.is_(None),
                RecepcionLeche.estado == "activo",
                RecepcionLeche.fecha.between(desde, hasta),
            )
        ) or CERO

        costo_transporte = self.db.scalar(
            select(func.coalesce(func.sum(RecepcionLeche.valor_transporte), 0)).where(
                RecepcionLeche.empresa_id == empresa,
                RecepcionLeche.deleted_at.is_(None),
                RecepcionLeche.estado == "activo",
                RecepcionLeche.fecha.between(desde, hasta),
            )
        ) or CERO

        gastos_categorias = GastoRepository(self.db, empresa).total_por_categoria(desde, hasta)
        lineas = [LineaCategoria(categoria=g.nombre, total=g.total or CERO) for g in gastos_categorias]
        total_gastos = sum((linea.total for linea in lineas), CERO)

        utilidad_bruta = ingresos_ventas - costo_leche - costo_transporte
        utilidad_neta = utilidad_bruta - total_gastos
        margen = (
            (utilidad_neta / ingresos_ventas * 100).quantize(Decimal("0.01"))
            if ingresos_ventas
            else CERO
        )
        return EstadoResultados(
            desde=desde,
            hasta=hasta,
            ingresos_ventas=ingresos_ventas,
            costo_leche=costo_leche,
            costo_transporte=costo_transporte,
            gastos_por_categoria=lineas,
            total_gastos=total_gastos,
            utilidad_bruta=utilidad_bruta,
            utilidad_neta=utilidad_neta,
            margen_neto=margen,
        )

    # ---------------------------------------------------------------- balance
    def balance(self) -> BalanceResponse:
        empresa = self.ctx.empresa_id

        # Efectivo disponible = saldo_final de la ÚLTIMA caja de cada sucursal
        # (esté abierta o cerrada). Así el efectivo de días ya cerrados no
        # desaparece, y no se suman saldos de días distintos (evita doble conteo).
        cajas = self.db.scalars(
            select(CajaDiaria)
            .where(CajaDiaria.empresa_id == empresa, CajaDiaria.deleted_at.is_(None))
            .order_by(CajaDiaria.fecha.desc(), CajaDiaria.created_at.desc())
        ).all()
        ultima_por_sucursal: dict = {}
        for c in cajas:
            ultima_por_sucursal.setdefault(c.sucursal_id, c)
        saldo_cajas = sum((c.saldo_final for c in ultima_por_sucursal.values()), CERO)

        cuentas = self.db.scalars(
            select(CuentaBancaria).where(
                CuentaBancaria.empresa_id == empresa,
                CuentaBancaria.deleted_at.is_(None),
                CuentaBancaria.estado == "activo",
            )
        ).all()
        from app.modules.bancos.repository import CuentaBancariaRepository

        cuentas_repo = CuentaBancariaRepository(self.db, empresa)
        saldo_bancos = sum((cuentas_repo.saldo_de(c) for c in cuentas), CERO)

        cartera = self.db.scalar(
            select(func.coalesce(func.sum(Venta.total - Venta.pagado), 0)).where(
                Venta.empresa_id == empresa,
                Venta.deleted_at.is_(None),
                Venta.estado.in_(["pendiente", "parcial"]),
            )
        ) or CERO

        por_pagar = self.db.scalar(
            select(func.coalesce(func.sum(Liquidacion.saldo), 0)).where(
                Liquidacion.empresa_id == empresa,
                Liquidacion.deleted_at.is_(None),
                Liquidacion.estado.in_(["borrador", "aprobada"]),
            )
        ) or CERO

        return BalanceResponse(
            fecha_corte=date.today(),
            saldo_cajas=saldo_cajas,
            saldo_bancos=saldo_bancos,
            cartera_por_cobrar=cartera,
            liquidaciones_por_pagar=por_pagar,
            total_disponible=saldo_cajas + saldo_bancos,
        )
