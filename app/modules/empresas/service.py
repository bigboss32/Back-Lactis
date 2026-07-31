import uuid
from typing import Any

from fastapi import UploadFile

from app.common.service import BaseService
from app.core.exceptions import BusinessError, ConflictError, ForbiddenError, NotFoundError
from app.core.pagination import PageParams
from app.modules.empresas.models import Empresa
from app.modules.empresas.repository import EmpresaRepository
from app.utils.files import save_upload


class EmpresaService(BaseService[Empresa]):
    repository_cls = EmpresaRepository
    modulo = "empresas"

    def listar(self, params: PageParams, **kwargs: Any) -> tuple[list[Empresa], int]:
        # Un usuario no superadmin solo ve las empresas de las que es miembro
        if not self.ctx.is_superadmin:
            kwargs.setdefault("extra_criteria", []).append(Empresa.id.in_(self.ctx.empresa_ids))
        return super().listar(params, **kwargs)

    def obtener(self, entity_id: uuid.UUID) -> Empresa:
        if not self.ctx.is_superadmin and entity_id not in self.ctx.empresa_ids:
            raise ForbiddenError("No puede acceder a información de otra empresa")
        return super().obtener(entity_id)

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.exists_where(Empresa.nit == data["nit"]):
            raise ConflictError(f"Ya existe una empresa con NIT {data['nit']}")

    def validar_actualizar(self, obj: Empresa, data: dict[str, Any]) -> None:
        if not self.ctx.is_superadmin and obj.id not in self.ctx.empresa_ids:
            raise ForbiddenError("No puede modificar otra empresa")
        if data.get("nit") and self.repo.exists_where(Empresa.nit == data["nit"], exclude_id=obj.id):
            raise ConflictError(f"Ya existe una empresa con NIT {data['nit']}")

    def subir_logo(self, entity_id: uuid.UUID, file: UploadFile) -> Empresa:
        empresa = self.obtener(entity_id)
        ruta = save_upload(file, empresa_id=empresa.id, subdir="logos")
        return self.actualizar(entity_id, {"logo_url": ruta})

    def reiniciar(self, entity_id: uuid.UUID, confirmacion: str) -> dict[str, int]:
        """Borra TODOS los datos transaccionales de una empresa (conservando
        catálogos y usuarios). Operación irreversible, solo para superadmin y con
        confirmación por nombre. Solo afecta a la empresa indicada.
        """
        from sqlalchemy import delete, select

        from app.core.database import Base
        from app.modules.auditoria.models import Auditoria
        from app.modules.bancos.models import MovimientoBancario
        from app.modules.caja.models import CajaDiaria, MovimientoCaja
        from app.modules.empleados.models import PagoEmpleado
        from app.modules.gastos.models import Gasto
        from app.modules.inventario.models import MovimientoInventario
        from app.modules.liquidaciones.models import (
            Anticipo,
            Liquidacion,
            LiquidacionDetalle,
        )
        from app.modules.notificaciones.models import Notificacion
        from app.modules.produccion.models import Produccion
        from app.modules.recepcion.models import RecepcionLeche
        from app.modules.reventa.models import (
            AbonoCompraQueso,
            AbonoSaldoAnterior,
            AbonoVentaQueso,
            CompraQueso,
            ConversionBorona,
            SaldoAnterior,
            Temporada,
            VentaQueso,
        )
        from app.modules.ventas.models import Pago, Venta, VentaDetalle

        if not self.ctx.is_superadmin:
            raise ForbiddenError("Solo el Administrador General puede reiniciar una empresa")
        empresa = self.db.get(Empresa, entity_id)
        if empresa is None:
            raise NotFoundError("Empresa no encontrada")
        if (confirmacion or "").strip() != (empresa.nombre or "").strip():
            raise BusinessError("La confirmación no coincide con el nombre de la empresa")

        borrados: dict[str, int] = {}

        # 1) Detalles sin empresa_id: se borran por su documento padre de esta empresa.
        detalles = [
            (VentaDetalle, VentaDetalle.venta_id, Venta),
            (LiquidacionDetalle, LiquidacionDetalle.liquidacion_id, Liquidacion),
            (AbonoCompraQueso, AbonoCompraQueso.compra_id, CompraQueso),
            (AbonoVentaQueso, AbonoVentaQueso.venta_id, VentaQueso),
            (AbonoSaldoAnterior, AbonoSaldoAnterior.saldo_id, SaldoAnterior),
        ]
        for modelo, fk, padre in detalles:
            res = self.db.execute(
                delete(modelo).where(fk.in_(select(padre.id).where(padre.empresa_id == entity_id)))
            )
            borrados[modelo.__tablename__] = res.rowcount or 0

        # 2) Tablas transaccionales (todas con empresa_id). El orden hijos->padres
        #    lo da reversed(sorted_tables), respetando las llaves foráneas.
        # SaldoAnterior y Temporada van aquí y no se quedan como "catálogo": un
        # saldo del libro anterior es PLATA (suma en por cobrar y por pagar), así
        # que si sobreviviera al reinicio la cartera seguiría mostrando deuda de
        # una empresa que se supone que quedó en ceros. Y una temporada sin
        # movimientos es un rango con nombre que ya no significa nada.
        transaccionales = {
            Pago, MovimientoInventario, MovimientoCaja, MovimientoBancario,
            ConversionBorona, PagoEmpleado, Anticipo, Notificacion, RecepcionLeche,
            Venta, Liquidacion, Produccion, CompraQueso, VentaQueso, Gasto, CajaDiaria,
            SaldoAnterior, Temporada,
        }
        tablas = {m.__table__ for m in transaccionales}
        for table in reversed(Base.metadata.sorted_tables):
            if table in tablas:
                res = self.db.execute(table.delete().where(table.c.empresa_id == entity_id))
                borrados[table.name] = res.rowcount or 0

        self.db.flush()
        self.db.add(
            Auditoria(
                empresa_id=entity_id,
                usuario_id=self.ctx.user_id,
                ip=self.ctx.ip,
                modulo="empresas",
                accion="reiniciar",
                entidad="Empresa",
                entidad_id=entity_id,
                antes=None,
                despues={"solo_transacciones": True, "borrados": borrados},
            )
        )
        return borrados
