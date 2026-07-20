import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.common.service import BaseService
from app.core.exceptions import BusinessError, ConflictError
from app.core.pagination import PageParams
from app.modules.proveedores.models import Proveedor
from app.modules.proveedores.repository import ProveedorRepository
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


class RecepcionService(BaseService[RecepcionLeche]):
    repository_cls = RecepcionRepository
    modulo = "recepcion"

    def _completar_y_calcular(self, data: dict[str, Any], actual: RecepcionLeche | None = None) -> dict[str, Any]:
        """Completa precio/ruta desde el proveedor y calcula los valores monetarios."""
        proveedor_id = data.get("proveedor_id") or (actual.proveedor_id if actual else None)
        proveedor = ProveedorRepository(self.db, self.ctx.empresa_id).get_or_fail(proveedor_id)

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

        data["valor_bruto"] = litros * precio
        data["valor_transporte"] = litros * tarifa
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
        return super().crear(data)

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> RecepcionLeche:
        actual = self.repo.get_or_fail(entity_id)
        if actual.liquidacion_id is not None:
            raise BusinessError("No se puede modificar una recepción ya liquidada")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        nueva_fecha = data.get("fecha", actual.fecha)
        if self.repo.existe_registro_dia(actual.proveedor_id, nueva_fecha, exclude_id=entity_id):
            raise ConflictError("Ya existe una recepción de este proveedor en esa fecha")
        data = self._completar_y_calcular(data, actual)
        return super().actualizar(entity_id, data)

    def validar_eliminar(self, obj: RecepcionLeche) -> None:
        if obj.liquidacion_id is not None:
            raise BusinessError("No se puede eliminar una recepción ya liquidada")

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
        return self.repo.list_paginated(params, filters=filters, extra_criteria=extra)

    def grilla_quincena(
        self,
        desde: date,
        hasta: date,
        *,
        search: str | None = None,
        ruta_id: uuid.UUID | None = None,
    ) -> GrillaQuincena:
        """Grilla proveedores × días como la hoja 'LITROS Y TRANSPORTE' del Excel.

        Incluye todos los proveedores activos (aunque no tengan recepciones)
        para que la grilla sirva también como superficie de registro diario.
        Se puede filtrar por nombre de proveedor (search) y por ruta (ruta_id).
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

        recepciones = list(
            self.db.scalars(
                self.repo.base_query().where(
                    RecepcionLeche.fecha >= desde,
                    RecepcionLeche.fecha <= hasta,
                    RecepcionLeche.estado == "activo",
                )
            ).all()
        )

        proveedores = {
            p.id: p for p in ProveedorRepository(self.db, self.ctx.empresa_id).all(estado="activo")
        }
        activos_ids = set(proveedores.keys())
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
                recepcion_id=r.id, litros=r.cantidad_litros, liquidada=r.liquidacion_id is not None
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
