"""Liquidaciones por quincena: agrupa las recepciones no liquidadas del período,
calcula totales, descuenta anticipos y genera el comprobante (PDF/Excel),
replicando el proceso que la quesera llevaba en Excel.
"""
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
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
from app.modules.recepcion.models import RecepcionLeche
from app.modules.recepcion.repository import RecepcionRepository
from app.utils.export import build_liquidacion_pdf, build_pdf, rows_to_excel

CERO = Decimal("0")


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

        generadas = []
        for trans_id, recepciones in por_transportador.items():
            total_litros = sum((r.cantidad_litros for r in recepciones), CERO)
            valor_transporte = sum((r.valor_transporte for r in recepciones), CERO)
            if valor_transporte == CERO:
                continue
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
                valor_total=valor_transporte,
                saldo=valor_transporte,
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
            self.db.flush()
            self._audit("crear", liquidacion.id, None, serialize_entity(liquidacion))
            generadas.append(liquidacion)
        return generadas

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

        detalle_rows = [
            [d.fecha.strftime("%d/%m/%Y"), f"{d.litros:,.1f}", f"${d.precio_litro:,.0f}", f"${d.valor:,.0f}"]
            for d in liquidacion.detalles
        ]

        if es_proveedor:
            resumen_rows = [
                ("Total litros", f"{liquidacion.total_litros:,.1f}", False),
                ("Precio promedio", f"${liquidacion.precio_promedio:,.2f}", False),
                ("Valor bruto", f"${liquidacion.valor_bruto:,.0f}", False),
                ("Bonificaciones", f"+ ${liquidacion.bonificaciones:,.0f}", False),
                ("Descuentos", f"- ${liquidacion.descuentos:,.0f}", False),
                ("Anticipos aplicados", f"- ${liquidacion.anticipos:,.0f}", False),
                ("VALOR TOTAL", f"${liquidacion.valor_total:,.0f}", True),
                ("SALDO A PAGAR", f"${liquidacion.saldo:,.0f}", True),
            ]
        else:
            resumen_rows = [
                ("Total litros", f"{liquidacion.total_litros:,.1f}", False),
                ("Valor transporte", f"${liquidacion.valor_transporte:,.0f}", False),
                ("VALOR TOTAL", f"${liquidacion.valor_total:,.0f}", True),
                ("SALDO A PAGAR", f"${liquidacion.saldo:,.0f}", True),
            ]

        anticipos_rows: list[list[Any]] = []
        if es_proveedor:
            anticipos = self.db.scalars(
                AnticipoRepository(self.db, self.ctx.empresa_id)
                .base_query()
                .where(Anticipo.liquidacion_id == liquidacion.id)
            ).all()
            anticipos_rows = [
                [a.fecha.strftime("%d/%m/%Y"), f"${a.valor:,.0f}", a.observaciones or "—"]
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

    def exportar_excel(self, desde: date, hasta: date) -> tuple[bytes, str]:
        items, _ = self.repo.list_paginated(
            PageParams(page=1, page_size=200),
            extra_criteria=[Liquidacion.periodo_fin >= desde, Liquidacion.periodo_inicio <= hasta],
        )
        if not items:
            raise NotFoundError("No hay liquidaciones en el período indicado")
        headers = [
            "Tipo", "Tercero", "Inicio", "Fin", "Litros", "Precio Prom.", "Valor Bruto",
            "Bonificaciones", "Descuentos", "Transporte", "Anticipos", "Valor Total", "Saldo", "Estado",
        ]
        rows = [
            [
                liq.tipo, self._nombre_tercero(liq), liq.periodo_inicio, liq.periodo_fin,
                liq.total_litros, liq.precio_promedio, liq.valor_bruto, liq.bonificaciones,
                liq.descuentos, liq.valor_transporte, liq.anticipos, liq.valor_total, liq.saldo, liq.estado,
            ]
            for liq in items
        ]
        excel = rows_to_excel(
            title=f"Liquidaciones del {desde.isoformat()} al {hasta.isoformat()}",
            headers=headers,
            rows=rows,
            sheet_name="Liquidaciones",
            money_columns=(7, 8, 9, 10, 11, 12, 13),
        )
        return excel, f"liquidaciones_{desde.isoformat()}_{hasta.isoformat()}.xlsx"


class AnticipoService(BaseService[Anticipo]):
    repository_cls = AnticipoRepository
    modulo = "liquidaciones"

    def validar_actualizar(self, obj: Anticipo, data: dict[str, Any]) -> None:
        if obj.liquidacion_id is not None:
            raise BusinessError("No se puede modificar un anticipo ya aplicado a una liquidación")

    def validar_eliminar(self, obj: Anticipo) -> None:
        if obj.liquidacion_id is not None:
            raise BusinessError("No se puede eliminar un anticipo ya aplicado a una liquidación")
