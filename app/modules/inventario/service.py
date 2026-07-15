import uuid
from decimal import Decimal
from typing import Any

from app.common.service import BaseService
from app.core.exceptions import BusinessError, ConflictError
from app.core.pagination import PageParams
from app.modules.inventario.models import MovimientoInventario, Producto
from app.modules.inventario.repository import (
    MovimientoInventarioRepository,
    ProductoRepository,
)
from app.modules.inventario.schemas import (
    KardexEntry,
    KardexResponse,
    ProductoStockRead,
)

CERO = Decimal("0")


class ProductoService(BaseService[Producto]):
    repository_cls = ProductoRepository
    modulo = "inventario"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(Producto.nombre == data["nombre"]):
            raise ConflictError(f"Ya existe un producto '{data['nombre']}'")

    def stock_actual(self, params: PageParams, *, solo_bajo_minimo: bool = False) -> tuple[list[ProductoStockRead], int]:
        from app.modules.inventario.schemas import ProductoRead

        items, total = self.repo.list_paginated(params, estado="activo")
        stocks = self.repo.stock_por_producto()
        resultado = []
        for producto in items:
            stock = stocks.get(producto.id, CERO)
            base = ProductoRead.model_validate(producto).model_dump()
            dto = ProductoStockRead(
                **base, stock_actual=stock, bajo_minimo=stock < producto.stock_minimo
            )
            resultado.append(dto)
        if solo_bajo_minimo:
            resultado = [p for p in resultado if p.bajo_minimo]
            total = len(resultado)
        return resultado, total


class MovimientoInventarioService(BaseService[MovimientoInventario]):
    repository_cls = MovimientoInventarioRepository
    modulo = "inventario"

    def crear(self, payload: Any) -> MovimientoInventario:
        data = payload.model_dump(exclude_unset=True)
        productos = ProductoRepository(self.db, self.ctx.empresa_id)
        producto = productos.get_or_fail(data["producto_id"])

        if data["tipo"] in ("entrada", "salida") and data["cantidad"] <= 0:
            raise BusinessError("La cantidad debe ser positiva para entradas y salidas")
        if data["tipo"] == "salida":
            stock = productos.stock_de(producto.id)
            if stock < data["cantidad"]:
                raise BusinessError(
                    f"Stock insuficiente de '{producto.nombre}': disponible {stock}, solicitado {data['cantidad']}"
                )
        if not data.get("costo_unitario"):
            data["costo_unitario"] = producto.costo_unitario
        return super().crear(data)

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> MovimientoInventario:
        raise BusinessError(
            "Los movimientos de inventario no se editan: registre un ajuste para corregir"
        )

    def kardex(self, producto_id: uuid.UUID) -> KardexResponse:
        productos = ProductoRepository(self.db, self.ctx.empresa_id)
        producto = productos.get_or_fail(producto_id)
        movimientos = list(
            self.db.scalars(
                self.repo.base_query()
                .where(MovimientoInventario.producto_id == producto_id)
                .order_by(MovimientoInventario.fecha, MovimientoInventario.created_at)
            ).all()
        )
        saldo = CERO
        entries = []
        for m in movimientos:
            delta = m.cantidad if m.tipo != "salida" else -m.cantidad
            saldo += delta
            entries.append(
                KardexEntry(
                    fecha=m.fecha,
                    tipo=m.tipo,
                    cantidad=m.cantidad,
                    costo_unitario=m.costo_unitario,
                    referencia=m.referencia,
                    saldo=saldo,
                )
            )
        return KardexResponse(
            producto_id=producto.id,
            producto_nombre=producto.nombre,
            unidad=producto.unidad,
            stock_actual=saldo,
            movimientos=entries,
        )
