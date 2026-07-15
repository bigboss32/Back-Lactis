"""Notificaciones y generación de alertas de negocio:
stock bajo, proveedores sin liquidar, pagos pendientes y usuarios bloqueados.
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.common.service import BaseService
from app.core.pagination import PageParams
from app.modules.inventario.repository import ProductoRepository
from app.modules.notificaciones.models import (
    TIPO_PAGOS_PENDIENTES,
    TIPO_SIN_LIQUIDAR,
    TIPO_STOCK_BAJO,
    TIPO_USUARIO_BLOQUEADO,
    Notificacion,
)
from app.modules.notificaciones.repository import NotificacionRepository
from app.modules.recepcion.models import RecepcionLeche
from app.modules.usuarios.models import Usuario
from app.modules.ventas.models import Venta

DIAS_VENCIMIENTO_CARTERA = 30
DIAS_SIN_LIQUIDAR = 20


class NotificacionService(BaseService[Notificacion]):
    repository_cls = NotificacionRepository
    modulo = "notificaciones"

    def listar_para_usuario(
        self, params: PageParams, *, solo_no_leidas: bool = False
    ) -> tuple[list[Notificacion], int]:
        extra = [
            (Notificacion.usuario_id == self.ctx.user_id) | (Notificacion.usuario_id.is_(None))
        ]
        if solo_no_leidas:
            extra.append(Notificacion.leida.is_(False))
        return self.repo.list_paginated(params, extra_criteria=extra)

    def marcar_leida(self, entity_id: uuid.UUID) -> Notificacion:
        notificacion = self.repo.get_or_fail(entity_id)
        notificacion.leida = True
        self.db.flush()
        return notificacion

    def marcar_todas_leidas(self) -> int:
        items, _ = self.listar_para_usuario(PageParams(page=1, page_size=200), solo_no_leidas=True)
        for n in items:
            n.leida = True
        self.db.flush()
        return len(items)

    def _emitir(self, tipo: str, titulo: str, mensaje: str, referencia: str) -> bool:
        if self.repo.existe_pendiente(tipo, referencia):
            return False
        self.db.add(
            Notificacion(
                empresa_id=self.ctx.empresa_id,
                tipo=tipo,
                titulo=titulo,
                mensaje=mensaje,
                referencia=referencia,
                created_by=self.ctx.user_id,
            )
        )
        return True

    def generar_alertas(self) -> dict[str, int]:
        contadores = {
            TIPO_STOCK_BAJO: self._alertas_stock_bajo(),
            TIPO_SIN_LIQUIDAR: self._alertas_sin_liquidar(),
            TIPO_PAGOS_PENDIENTES: self._alertas_pagos_pendientes(),
            TIPO_USUARIO_BLOQUEADO: self._alertas_usuarios_bloqueados(),
        }
        self.db.flush()
        return contadores

    def _alertas_stock_bajo(self) -> int:
        productos_repo = ProductoRepository(self.db, self.ctx.empresa_id)
        stocks = productos_repo.stock_por_producto()
        emitidas = 0
        for producto in productos_repo.all(estado="activo"):
            stock = stocks.get(producto.id, 0)
            if producto.stock_minimo and stock < producto.stock_minimo:
                emitidas += self._emitir(
                    TIPO_STOCK_BAJO,
                    f"Stock bajo: {producto.nombre}",
                    f"Quedan {stock} {producto.unidad} (mínimo {producto.stock_minimo})",
                    f"producto:{producto.id}",
                )
        return emitidas

    def _alertas_sin_liquidar(self) -> int:
        limite = date.today() - timedelta(days=DIAS_SIN_LIQUIDAR)
        stmt = (
            select(RecepcionLeche.proveedor_id)
            .where(
                RecepcionLeche.empresa_id == self.ctx.empresa_id,
                RecepcionLeche.deleted_at.is_(None),
                RecepcionLeche.liquidacion_id.is_(None),
                RecepcionLeche.fecha <= limite,
            )
            .distinct()
        )
        emitidas = 0
        for (proveedor_id,) in self.db.execute(stmt).all():
            emitidas += self._emitir(
                TIPO_SIN_LIQUIDAR,
                "Proveedor con recepciones sin liquidar",
                f"Hay recepciones de hace más de {DIAS_SIN_LIQUIDAR} días sin liquidación",
                f"proveedor:{proveedor_id}",
            )
        return emitidas

    def _alertas_pagos_pendientes(self) -> int:
        limite = date.today() - timedelta(days=DIAS_VENCIMIENTO_CARTERA)
        stmt = select(Venta).where(
            Venta.empresa_id == self.ctx.empresa_id,
            Venta.deleted_at.is_(None),
            Venta.estado.in_(["pendiente", "parcial"]),
            Venta.fecha <= limite,
        )
        emitidas = 0
        for venta in self.db.scalars(stmt).all():
            emitidas += self._emitir(
                TIPO_PAGOS_PENDIENTES,
                f"Venta #{venta.numero} con saldo vencido",
                f"Saldo ${venta.saldo:,.0f} pendiente desde {venta.fecha.isoformat()}",
                f"venta:{venta.id}",
            )
        return emitidas

    def _alertas_usuarios_bloqueados(self) -> int:
        stmt = select(Usuario).where(
            Usuario.empresa_id == self.ctx.empresa_id,
            Usuario.deleted_at.is_(None),
            Usuario.bloqueado.is_(True),
        )
        emitidas = 0
        for usuario in self.db.scalars(stmt).all():
            emitidas += self._emitir(
                TIPO_USUARIO_BLOQUEADO,
                f"Usuario bloqueado: {usuario.username}",
                f"{usuario.nombre_completo} está bloqueado por intentos fallidos",
                f"usuario:{usuario.id}",
            )
        return emitidas
