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
