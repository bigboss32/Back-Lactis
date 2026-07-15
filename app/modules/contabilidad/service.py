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
from app.modules.gastos.models import Gasto
from app.modules.gastos.repository import GastoRepository
from app.modules.liquidaciones.models import Liquidacion
from app.modules.recepcion.models import RecepcionLeche
from app.modules.ventas.models import Pago, Venta

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
        asientos: list[AsientoLibroDiario] = []
        empresa = self.ctx.empresa_id

        pagos = self.db.scalars(
            select(Pago).where(
                Pago.empresa_id == empresa,
                Pago.deleted_at.is_(None),
                Pago.fecha.between(desde, hasta),
            )
        ).all()
        asientos += [
            AsientoLibroDiario(
                fecha=p.fecha, origen="pago", concepto=f"Pago venta ({p.metodo})",
                ingreso=p.valor, egreso=CERO, referencia=str(p.venta_id),
            )
            for p in pagos
        ]

        gastos = self.db.scalars(
            select(Gasto).where(
                Gasto.empresa_id == empresa,
                Gasto.deleted_at.is_(None),
                Gasto.estado == "activo",
                Gasto.fecha.between(desde, hasta),
            )
        ).all()
        asientos += [
            AsientoLibroDiario(
                fecha=g.fecha, origen="gasto", concepto=g.concepto,
                ingreso=CERO, egreso=g.valor, referencia=g.numero_factura,
            )
            for g in gastos
        ]

        recepciones = self.db.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.empresa_id == empresa,
                RecepcionLeche.deleted_at.is_(None),
                RecepcionLeche.estado == "activo",
                RecepcionLeche.fecha.between(desde, hasta),
            )
        ).all()
        asientos += [
            AsientoLibroDiario(
                fecha=r.fecha, origen="recepcion", concepto="Compra de leche",
                ingreso=CERO, egreso=r.valor_neto, referencia=str(r.proveedor_id),
            )
            for r in recepciones
        ]

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

        saldo_cajas = self.db.scalar(
            select(func.coalesce(func.sum(CajaDiaria.saldo_final), 0)).where(
                CajaDiaria.empresa_id == empresa,
                CajaDiaria.deleted_at.is_(None),
                CajaDiaria.estado == "abierta",
            )
        ) or CERO

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
