import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import UploadFile
from pydantic import BaseModel

from app.common.service import BaseService, serialize_entity
from app.core.config import settings
from app.core.exceptions import BusinessError, ConflictError, ForbiddenError, NotFoundError
from app.core.logging_config import get_logger
from app.core.pagination import PageParams
from app.modules.empresas.models import Empresa
from app.modules.empresas.repository import EmpresaRepository
from app.utils.files import save_upload

logger = get_logger("empresas")


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

    def _prepare_create_data(self, data: dict[str, Any]) -> dict[str, Any]:
        data = super()._prepare_create_data(data)
        # Toda empresa nueva arranca con el periodo de prueba ya materializado:
        # así la fecha queda visible (y editable por el superadmin) desde el día uno
        if not data.get("pagada_hasta"):
            data["pagada_hasta"] = date.today() + timedelta(days=settings.SUSCRIPCION_DIAS_PRUEBA)
        return data

    def validar_eliminar(self, obj: Empresa) -> None:
        """Solo el superadmin borra empresas.

        Sin esto corría el no-op de BaseService, y como Empresa NO tiene columna
        empresa_id el repositorio tampoco recorta por tenant: cualquiera con el
        permiso (empresas, eliminar) borraba la empresa de OTRO cliente, o la
        suya. Encadenado con el paywall, borrar la propia empresa era además la
        forma de dejar de pagar. Mismo criterio que `reiniciar`.
        """
        if not self.ctx.is_superadmin:
            raise ForbiddenError("Solo el Administrador General puede eliminar una empresa")

    def validar_actualizar(self, obj: Empresa, data: dict[str, Any]) -> None:
        if not self.ctx.is_superadmin and obj.id not in self.ctx.empresa_ids:
            raise ForbiddenError("No puede modificar otra empresa")
        if data.get("nit") and self.repo.exists_where(Empresa.nit == data["nit"], exclude_id=obj.id):
            raise ConflictError(f"Ya existe una empresa con NIT {data['nit']}")

    def subir_logo(self, entity_id: uuid.UUID, file: UploadFile) -> Empresa:
        empresa = self.obtener(entity_id)
        ruta = save_upload(file, empresa_id=empresa.id, subdir="logos")
        return self.actualizar(entity_id, {"logo_url": ruta})

    def actualizar_suscripcion(self, entity_id: uuid.UUID, payload: BaseModel) -> Empresa:
        """Ajusta tarifa, exención y vigencia de la suscripción de una empresa.
        Solo superadmin (patrón reiniciar: el chequeo vive aquí, no en el
        router) y sin scoping de membresía: administra CUALQUIER empresa."""
        if not self.ctx.is_superadmin:
            raise ForbiddenError(
                "Solo el Administrador General puede modificar la suscripción de una empresa"
            )
        empresa = self.db.get(Empresa, entity_id)
        if empresa is None or empresa.deleted_at is not None:
            raise NotFoundError("Empresa no encontrada")
        # exclude_unset: los null EXPLÍCITOS también aplican (tarifa_mensual
        # null = volver a la tarifa global; pagada_hasta null = volver a prueba)
        data = payload.model_dump(exclude_unset=True)
        if not data:
            raise BusinessError("No se envió ningún campo de suscripción para actualizar")
        antes = serialize_entity(empresa)
        for campo, valor in data.items():
            setattr(empresa, campo, valor)
        empresa.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("suscripcion", empresa.id, antes, serialize_entity(empresa))
        return empresa

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
            PagoLiquidacion,
        )
        from app.modules.notificaciones.models import Notificacion
        from app.modules.produccion.models import Produccion
        from app.modules.recepcion.models import RecepcionLeche
        from app.modules.reventa.models import (
            AbonoCompraQueso,
            AbonoSaldoAnterior,
            AbonoVentaQueso,
            AdjuntoReventa,
            CompraQueso,
            ConversionBorona,
            DocumentoReventa,
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

        # 0) Los ARCHIVOS de los soportes de reventa, antes de borrar sus filas.
        #
        # Borrar solo las filas dejaría las fotos de las transferencias en el
        # bucket de Cloudflare para siempre: sin fila que las nombre, nadie las
        # puede ver ni borrar desde la aplicación, y se seguiría pagando ese
        # almacenamiento. Peor todavía: son comprobantes de pago de una empresa
        # que se supone que quedó en ceros.
        #
        # Es de MEJOR ESFUERZO. Si el bucket no responde, el reinicio sigue: es
        # una operación de superadmin, irreversible y ya confirmada por nombre,
        # y detenerla a la mitad dejaría la empresa medio borrada. Lo que no se
        # pudo borrar queda en el log para limpiarlo a mano.
        claves = list(
            self.db.scalars(
                select(AdjuntoReventa.object_key).where(
                    AdjuntoReventa.empresa_id == entity_id
                )
            ).all()
        )
        if claves:
            from app.core.storage import R2Client, r2_configurado

            if r2_configurado():
                cliente = R2Client()
                for clave in claves:
                    try:
                        cliente.borrar(clave)
                    except Exception:
                        logger.warning("Quedó un objeto en R2 tras reiniciar la empresa: %s", clave)
            else:
                logger.warning(
                    "Se reinició la empresa %s con %d soportes en R2 y sin llaves "
                    "configuradas: esos archivos hay que borrarlos a mano",
                    entity_id,
                    len(claves),
                )

        # 1) Detalles sin empresa_id: se borran por su documento padre de esta empresa.
        detalles = [
            (VentaDetalle, VentaDetalle.venta_id, Venta),
            (LiquidacionDetalle, LiquidacionDetalle.liquidacion_id, Liquidacion),
            # Los pagos cuelgan de la liquidación y no llevan empresa_id propio,
            # igual que los abonos de reventa: se borran por su padre o se
            # quedarían apuntando a una liquidación que ya no existe.
            (PagoLiquidacion, PagoLiquidacion.liquidacion_id, Liquidacion),
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
        # AdjuntoReventa va aquí y no en los "detalles": tiene empresa_id propio,
        # y `reversed(sorted_tables)` lo borra ANTES que compras_queso y
        # ventas_queso, que es de quienes depende. Va explícito y no confiado al
        # ON DELETE CASCADE porque en SQLite las llaves foráneas están apagadas
        # por defecto y ahí las filas sobrevivirían al reinicio.
        # DocumentoReventa va aquí porque es una transacción, no un catálogo: es la
        # FACTURA de una compra o de una venta. Y el orden lo resuelve solo
        # `reversed(sorted_tables)`: `compras_queso` y `ventas_queso` apuntan a
        # `documentos_reventa`, así que sus filas se borran ANTES que las cabeceras
        # y ninguna llave foránea queda colgando. Va explícito y no confiado al
        # ON DELETE SET NULL porque lo que hay que borrar son las cabeceras
        # también: dejarlas vivas sería dejar facturas de una empresa que se supone
        # que quedó en ceros.
        transaccionales = {
            Pago, MovimientoInventario, MovimientoCaja, MovimientoBancario,
            AdjuntoReventa, ConversionBorona, PagoEmpleado, Anticipo, Notificacion,
            RecepcionLeche, Venta, Liquidacion, Produccion, CompraQueso, VentaQueso,
            DocumentoReventa, Gasto, CajaDiaria, SaldoAnterior, Temporada,
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
