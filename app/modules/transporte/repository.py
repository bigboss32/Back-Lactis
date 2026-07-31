import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, case, func, literal, or_, select

from app.common.repository import BaseRepository
from app.modules.clientes.models import Cliente
from app.modules.transporte.models import (
    ESTADO_SERVICIO_ANULADA,
    ESTADO_SERVICIO_INTERNO,
    ESTADO_SERVICIO_PARCIAL,
    ESTADO_SERVICIO_PENDIENTE,
    ESTADO_VIAJE_ANULADO,
    Vehiculo,
    VehiculoDocumento,
    VehiculoGasto,
    VehiculoMantenimiento,
    Viaje,
    ViajeServicio,
)

CERO = Decimal("0")


def clave_nombre(columna):
    """Clave para agrupar clientes ocasionales escritos distinto: sin mayúsculas
    ni espacios de sobra. Así "Don Pedro" y "don pedro " son el mismo cliente y
    su saldo no queda partido en dos filas de la cartera (mismo criterio del
    módulo reventa)."""
    return func.lower(func.trim(columna))


class VehiculoRepository(BaseRepository[Vehiculo]):
    model = Vehiculo
    search_fields = ("placa", "nombre", "marca")


class ViajeRepository(BaseRepository[Viaje]):
    model = Viaje
    search_fields = ("origen", "destino", "conductor_nombre")
    default_order_by = "fecha_salida"

    def siguiente_numero(self) -> int:
        stmt = select(func.coalesce(func.max(Viaje.numero), 0)).where(
            Viaje.empresa_id == self.empresa_id
        )
        return (self.db.scalar(stmt) or 0) + 1

    def _vigentes(self, vehiculo_id: uuid.UUID | None = None) -> list:
        criterios = [
            Viaje.empresa_id == self.empresa_id,
            Viaje.deleted_at.is_(None),
            Viaje.estado != ESTADO_VIAJE_ANULADO,
        ]
        if vehiculo_id is not None:
            criterios.append(Viaje.vehiculo_id == vehiculo_id)
        return criterios

    def totales_periodo(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> tuple[int, Decimal, Decimal]:
        """(viajes realizados, pago a conductores, kilómetros recorridos).

        Los kilómetros solo suman los viajes con AMBOS odómetros registrados;
        un viaje sin regreso todavía no recorrió nada medible.
        """
        kilometros = case(
            (
                and_(Viaje.odometro_salida.is_not(None), Viaje.odometro_regreso.is_not(None)),
                Viaje.odometro_regreso - Viaje.odometro_salida,
            ),
            else_=literal(0),
        )
        fila = self.db.execute(
            select(
                func.count(Viaje.id),
                func.coalesce(func.sum(Viaje.pago_conductor), 0),
                func.coalesce(func.sum(kilometros), 0),
            ).where(*self._vigentes(vehiculo_id), Viaje.fecha_salida.between(desde, hasta))
        ).one()
        return int(fila[0]), Decimal(fila[1]), Decimal(fila[2])

    def filas_para_serie(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> list[tuple[date, Decimal]]:
        """(fecha de salida, pago del conductor) de cada viaje del período,
        para agrupar la serie mensual en Python (strftime/date_trunc no son
        portables entre SQLite y Postgres)."""
        return [
            (fila[0], Decimal(fila[1] or 0))
            for fila in self.db.execute(
                select(Viaje.fecha_salida, Viaje.pago_conductor).where(
                    *self._vigentes(vehiculo_id), Viaje.fecha_salida.between(desde, hasta)
                )
            ).all()
        ]


class ViajeServicioRepository(BaseRepository[ViajeServicio]):
    model = ViajeServicio
    search_fields = ("descripcion", "cliente_nombre")

    def _criterios_vigentes(self, vehiculo_id: uuid.UUID | None = None) -> list:
        """Servicio que todavía cuenta: no borrado, no anulado y de un viaje
        vivo (no borrado ni anulado). Todas las consultas de plata pasan por
        aquí: los reportes excluyen lo anulado."""
        criterios = [
            ViajeServicio.empresa_id == self.empresa_id,
            ViajeServicio.deleted_at.is_(None),
            ViajeServicio.estado != ESTADO_SERVICIO_ANULADA,
            Viaje.deleted_at.is_(None),
            Viaje.estado != ESTADO_VIAJE_ANULADO,
        ]
        if vehiculo_id is not None:
            criterios.append(Viaje.vehiculo_id == vehiculo_id)
        return criterios

    def ingresos_periodo(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> tuple[Decimal, Decimal, Decimal]:
        """(ingresos de terceros, ingresos internos, kilos transportados) de
        los viajes que SALIERON en el período."""
        fila = self.db.execute(
            select(
                func.coalesce(
                    func.sum(ViajeServicio.valor_total).filter(
                        ViajeServicio.estado != ESTADO_SERVICIO_INTERNO
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(ViajeServicio.valor_total).filter(
                        ViajeServicio.estado == ESTADO_SERVICIO_INTERNO
                    ),
                    0,
                ),
                func.coalesce(func.sum(func.coalesce(ViajeServicio.kilos, 0)), 0),
            )
            .join(Viaje, Viaje.id == ViajeServicio.viaje_id)
            .where(
                *self._criterios_vigentes(vehiculo_id),
                Viaje.fecha_salida.between(desde, hasta),
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1]), Decimal(fila[2])

    def filas_para_serie(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> list[tuple[date, Decimal]]:
        """(fecha de salida del viaje, valor del servicio) para la serie mensual."""
        return [
            (fila[0], Decimal(fila[1] or 0))
            for fila in self.db.execute(
                select(Viaje.fecha_salida, ViajeServicio.valor_total)
                .join(Viaje, Viaje.id == ViajeServicio.viaje_id)
                .where(
                    *self._criterios_vigentes(vehiculo_id),
                    Viaje.fecha_salida.between(desde, hasta),
                )
            ).all()
        ]

    def por_cobrar(self, vehiculo_id: uuid.UUID | None = None) -> Decimal:
        """Saldo HISTÓRICO de la cartera de fletes (sin filtro de fechas): lo
        que los terceros deben hoy, que es la cifra de la tarjeta."""
        total = self.db.scalar(
            select(
                func.coalesce(func.sum(ViajeServicio.valor_total - ViajeServicio.abonado), 0)
            )
            .select_from(ViajeServicio)
            .join(Viaje, Viaje.id == ViajeServicio.viaje_id)
            .where(
                *self._criterios_vigentes(vehiculo_id),
                ViajeServicio.estado.in_(
                    [ESTADO_SERVICIO_PENDIENTE, ESTADO_SERVICIO_PARCIAL]
                ),
            )
        )
        return Decimal(total or 0)

    def _criterios_cartera(self) -> list:
        return [
            *self._criterios_vigentes(),
            ViajeServicio.estado.in_([ESTADO_SERVICIO_PENDIENTE, ESTADO_SERVICIO_PARCIAL]),
        ]

    def cartera_directorio(self) -> list:
        """Cartera de los clientes DEL DIRECTORIO:
        (cliente_id, nombre, servicios pendientes, facturado, abonado)."""
        stmt = (
            select(
                ViajeServicio.cliente_id,
                Cliente.nombre,
                func.count(ViajeServicio.id),
                func.coalesce(func.sum(ViajeServicio.valor_total), 0),
                func.coalesce(func.sum(ViajeServicio.abonado), 0),
            )
            .join(Viaje, Viaje.id == ViajeServicio.viaje_id)
            .join(Cliente, Cliente.id == ViajeServicio.cliente_id)
            .where(*self._criterios_cartera(), ViajeServicio.cliente_id.is_not(None))
            .group_by(ViajeServicio.cliente_id, Cliente.nombre)
        )
        return list(self.db.execute(stmt).all())

    def cartera_ocasionales(self) -> list:
        """Cartera de los clientes OCASIONALES (texto libre), agrupando las
        variantes de escritura: (None, nombre, servicios, facturado, abonado).
        Dos homónimos se mezclan a propósito: los de crédito van al directorio.
        """
        clave = clave_nombre(ViajeServicio.cliente_nombre)
        stmt = (
            select(
                literal(None),
                func.min(ViajeServicio.cliente_nombre),
                func.count(ViajeServicio.id),
                func.coalesce(func.sum(ViajeServicio.valor_total), 0),
                func.coalesce(func.sum(ViajeServicio.abonado), 0),
            )
            .join(Viaje, Viaje.id == ViajeServicio.viaje_id)
            .where(*self._criterios_cartera(), ViajeServicio.cliente_id.is_(None))
            .group_by(clave)
        )
        return list(self.db.execute(stmt).all())

    def pendientes_de_cliente(
        self, cliente_id: uuid.UUID | None = None, cliente_nombre: str | None = None
    ) -> list[ViajeServicio]:
        """Servicios con saldo (pendiente/parcial) de UN cliente, del viaje más
        antiguo al más reciente. El nombre del ocasional se compara NORMALIZADO
        y viaja como parámetro (`literal`), nunca pegado al SQL: lo escribe el
        usuario."""
        criterios = self._criterios_cartera()
        if cliente_id is not None:
            criterios.append(ViajeServicio.cliente_id == cliente_id)
        else:
            buscado = " ".join((cliente_nombre or "").split())
            criterios.append(ViajeServicio.cliente_id.is_(None))
            criterios.append(
                clave_nombre(ViajeServicio.cliente_nombre) == clave_nombre(literal(buscado))
            )
        return list(
            self.db.scalars(
                select(ViajeServicio)
                .join(Viaje, Viaje.id == ViajeServicio.viaje_id)
                .where(*criterios)
                .order_by(Viaje.fecha_salida, ViajeServicio.created_at)
            ).all()
        )


class VehiculoGastoRepository(BaseRepository[VehiculoGasto]):
    model = VehiculoGasto
    search_fields = ("concepto", "categoria")
    default_order_by = "fecha"

    def _criterios_reporte(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> list:
        """Gastos que cuentan en los reportes: los generales siempre; los de un
        viaje solo si el viaje sigue vivo (no anulado ni borrado)."""
        criterios = [
            VehiculoGasto.empresa_id == self.empresa_id,
            VehiculoGasto.deleted_at.is_(None),
            VehiculoGasto.fecha.between(desde, hasta),
            or_(
                VehiculoGasto.viaje_id.is_(None),
                and_(Viaje.deleted_at.is_(None), Viaje.estado != ESTADO_VIAJE_ANULADO),
            ),
        ]
        if vehiculo_id is not None:
            criterios.append(VehiculoGasto.vehiculo_id == vehiculo_id)
        return criterios

    def por_categoria_periodo(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> list[tuple[str, Decimal]]:
        filas = self.db.execute(
            select(VehiculoGasto.categoria, func.coalesce(func.sum(VehiculoGasto.valor), 0))
            .outerjoin(Viaje, Viaje.id == VehiculoGasto.viaje_id)
            .where(*self._criterios_reporte(desde, hasta, vehiculo_id))
            .group_by(VehiculoGasto.categoria)
        ).all()
        return [(fila[0], Decimal(fila[1])) for fila in filas]

    def filas_para_serie(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> list[tuple[date, Decimal]]:
        return [
            (fila[0], Decimal(fila[1] or 0))
            for fila in self.db.execute(
                select(VehiculoGasto.fecha, VehiculoGasto.valor)
                .outerjoin(Viaje, Viaje.id == VehiculoGasto.viaje_id)
                .where(*self._criterios_reporte(desde, hasta, vehiculo_id))
            ).all()
        ]


class VehiculoMantenimientoRepository(BaseRepository[VehiculoMantenimiento]):
    model = VehiculoMantenimiento
    search_fields = ("descripcion", "taller")
    default_order_by = "fecha"

    def _vigentes(self, vehiculo_id: uuid.UUID | None = None) -> list:
        criterios = [
            VehiculoMantenimiento.empresa_id == self.empresa_id,
            VehiculoMantenimiento.deleted_at.is_(None),
        ]
        if vehiculo_id is not None:
            criterios.append(VehiculoMantenimiento.vehiculo_id == vehiculo_id)
        return criterios

    def total_periodo(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> Decimal:
        total = self.db.scalar(
            select(func.coalesce(func.sum(VehiculoMantenimiento.valor), 0)).where(
                *self._vigentes(vehiculo_id),
                VehiculoMantenimiento.fecha.between(desde, hasta),
            )
        )
        return Decimal(total or 0)

    def filas_para_serie(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> list[tuple[date, Decimal]]:
        return [
            (fila[0], Decimal(fila[1] or 0))
            for fila in self.db.execute(
                select(VehiculoMantenimiento.fecha, VehiculoMantenimiento.valor).where(
                    *self._vigentes(vehiculo_id),
                    VehiculoMantenimiento.fecha.between(desde, hasta),
                )
            ).all()
        ]

    def con_proximo(self, vehiculo_id: uuid.UUID | None = None) -> list[VehiculoMantenimiento]:
        """Mantenimientos que anuncian el próximo (por fecha o por odómetro),
        del más reciente al más viejo: el servicio se queda con el último por
        (vehículo, tipo) para las alertas."""
        return list(
            self.db.scalars(
                select(VehiculoMantenimiento)
                .where(
                    *self._vigentes(vehiculo_id),
                    or_(
                        VehiculoMantenimiento.proxima_fecha.is_not(None),
                        VehiculoMantenimiento.proximo_odometro.is_not(None),
                    ),
                )
                .order_by(
                    VehiculoMantenimiento.fecha.desc(),
                    VehiculoMantenimiento.created_at.desc(),
                )
            ).all()
        )


class VehiculoDocumentoRepository(BaseRepository[VehiculoDocumento]):
    model = VehiculoDocumento
    search_fields = ("numero", "descripcion")
    default_order_by = "fecha_vencimiento"

    def _vigentes(self, vehiculo_id: uuid.UUID | None = None) -> list:
        criterios = [
            VehiculoDocumento.empresa_id == self.empresa_id,
            VehiculoDocumento.deleted_at.is_(None),
        ]
        if vehiculo_id is not None:
            criterios.append(VehiculoDocumento.vehiculo_id == vehiculo_id)
        return criterios

    def total_periodo(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> Decimal:
        """Lo pagado por documentos EXPEDIDOS en el período (bucket propio del
        resumen: las categorías de gasto no incluyen documentos)."""
        total = self.db.scalar(
            select(func.coalesce(func.sum(VehiculoDocumento.valor), 0)).where(
                *self._vigentes(vehiculo_id),
                VehiculoDocumento.fecha_expedicion.is_not(None),
                VehiculoDocumento.fecha_expedicion.between(desde, hasta),
            )
        )
        return Decimal(total or 0)

    def filas_para_serie(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> list[tuple[date, Decimal]]:
        return [
            (fila[0], Decimal(fila[1] or 0))
            for fila in self.db.execute(
                select(VehiculoDocumento.fecha_expedicion, VehiculoDocumento.valor).where(
                    *self._vigentes(vehiculo_id),
                    VehiculoDocumento.fecha_expedicion.is_not(None),
                    VehiculoDocumento.fecha_expedicion.between(desde, hasta),
                )
            ).all()
        ]

    def todos_para_alertas(self, vehiculo_id: uuid.UUID | None = None) -> list[VehiculoDocumento]:
        """Todos los documentos vivos, del vencimiento más reciente al más
        viejo: el servicio se queda con el primero por (vehículo, tipo)."""
        return list(
            self.db.scalars(
                select(VehiculoDocumento)
                .where(*self._vigentes(vehiculo_id))
                .order_by(
                    VehiculoDocumento.fecha_vencimiento.desc(),
                    VehiculoDocumento.created_at.desc(),
                )
            ).all()
        )
