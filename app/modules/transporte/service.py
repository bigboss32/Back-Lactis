"""Transporte ("la turbo"): viajes con fletes de terceros y queso propio,
cartera con abonos por servicio, gastos del vehículo, mantenimientos y
documentos legales con alertas. Libro independiente de contabilidad.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import UploadFile

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, ConflictError, NotFoundError
from app.core.pagination import PageParams
from app.modules.clientes.repository import ClienteRepository
from app.modules.transporte.models import (
    COBRO_POR_KILO,
    ESTADO_SERVICIO_ANULADA,
    ESTADO_SERVICIO_INTERNO,
    ESTADO_SERVICIO_PAGADA,
    ESTADO_SERVICIO_PARCIAL,
    ESTADO_SERVICIO_PENDIENTE,
    ESTADO_VIAJE_ANULADO,
    ESTADO_VIAJE_EN_CURSO,
    ESTADO_VIAJE_FINALIZADO,
    METODO_EFECTIVO,
    AbonoFlete,
    Vehiculo,
    VehiculoDocumento,
    VehiculoGasto,
    VehiculoMantenimiento,
    Viaje,
    ViajeServicio,
)
from app.modules.transporte.repository import (
    VehiculoDocumentoRepository,
    VehiculoGastoRepository,
    VehiculoMantenimientoRepository,
    VehiculoRepository,
    ViajeRepository,
    ViajeServicioRepository,
)
from app.modules.transporte.schemas import (
    AbonoFleteRead,
    AlertaDocumento,
    AlertaMantenimiento,
    AlertasTransporte,
    CarteraFleteCliente,
    CarteraFleteDetalle,
    ResumenTransporte,
    SerieMensualTransporte,
    ServicioCarteraRead,
)
from app.utils.export import pesos
from app.utils.files import save_upload

CERO = Decimal("0")
DOS_DECIMALES = Decimal("0.01")


def _estado_pago(valor_total: Decimal, abonado: Decimal) -> str:
    if abonado <= CERO:
        return ESTADO_SERVICIO_PENDIENTE
    return ESTADO_SERVICIO_PAGADA if abonado >= valor_total else ESTADO_SERVICIO_PARCIAL


def _actualizar_odometro(vehiculo: Vehiculo, odometro: Decimal | None) -> None:
    """El odómetro del vehículo solo SUBE: al finalizar un viaje o registrar un
    mantenimiento/gasto con odómetro mayor. Un dato menor no lo baja (sería un
    registro viejo); la corrección manual va por el PUT del vehículo."""
    if odometro is not None and Decimal(odometro) > Decimal(vehiculo.odometro_actual or CERO):
        vehiculo.odometro_actual = Decimal(odometro)


class VehiculoService(BaseService[Vehiculo]):
    repository_cls = VehiculoRepository
    modulo = "transporte"

    @staticmethod
    def _normalizar_placa(data: dict[str, Any]) -> dict[str, Any]:
        """La placa se guarda sin espacios y en mayúsculas: "abc 123" y
        "ABC123" son el mismo vehículo y deben chocar con la restricción."""
        if data.get("placa"):
            data["placa"] = "".join(data["placa"].split()).upper()
        return data

    def crear(self, payload: Any) -> Vehiculo:
        data = self._normalizar_placa(payload.model_dump(exclude_unset=True))
        if self.repo.exists_where(Vehiculo.placa == data["placa"]):
            raise ConflictError(f"Ya existe un vehículo con la placa {data['placa']}")
        return super().crear(data)

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Vehiculo:
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        data = self._normalizar_placa(data)
        if data.get("placa") and self.repo.exists_where(
            Vehiculo.placa == data["placa"], exclude_id=entity_id
        ):
            raise ConflictError(f"Ya existe un vehículo con la placa {data['placa']}")
        return super().actualizar(entity_id, data)

    def validar_eliminar(self, obj: Vehiculo) -> None:
        if ViajeRepository(self.db, self.ctx.empresa_id).exists_where(
            Viaje.vehiculo_id == obj.id
        ):
            raise BusinessError(
                "No se puede eliminar un vehículo con viajes registrados"
            )


class ViajeService(BaseService[Viaje]):
    repository_cls = ViajeRepository
    modulo = "transporte"

    def _vehiculos(self) -> VehiculoRepository:
        return VehiculoRepository(self.db, self.ctx.empresa_id)

    @staticmethod
    def _validar_coherencia(
        fecha_salida: date,
        fecha_regreso: date | None,
        odometro_salida: Decimal | None,
        odometro_regreso: Decimal | None,
    ) -> None:
        if fecha_regreso is not None and fecha_regreso < fecha_salida:
            raise BusinessError("La fecha de regreso no puede ser anterior a la de salida")
        if (
            odometro_salida is not None
            and odometro_regreso is not None
            and Decimal(odometro_regreso) < Decimal(odometro_salida)
        ):
            raise BusinessError("El odómetro de regreso no puede ser menor que el de salida")

    def crear(self, payload: Any) -> Viaje:
        data = payload.model_dump(exclude_unset=True)
        self._vehiculos().get_or_fail(data["vehiculo_id"])
        data["numero"] = self.repo.siguiente_numero()
        data["estado"] = ESTADO_VIAJE_EN_CURSO
        return super().crear(data)

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Viaje:
        viaje = self.repo.get_or_fail(entity_id)
        if viaje.estado == ESTADO_VIAJE_ANULADO:
            raise BusinessError("No se puede modificar un viaje anulado")
        if viaje.estado == ESTADO_VIAJE_FINALIZADO:
            raise BusinessError("El viaje está finalizado: reábralo para corregirlo")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        if data.get("vehiculo_id") and data["vehiculo_id"] != viaje.vehiculo_id:
            self._vehiculos().get_or_fail(data["vehiculo_id"])
            if viaje.gastos_vigentes:
                raise BusinessError(
                    "No se puede cambiar el vehículo de un viaje con gastos registrados"
                )
        self._validar_coherencia(
            data.get("fecha_salida") or viaje.fecha_salida,
            data["fecha_regreso"] if "fecha_regreso" in data else viaje.fecha_regreso,
            data["odometro_salida"] if "odometro_salida" in data else viaje.odometro_salida,
            data["odometro_regreso"] if "odometro_regreso" in data else viaje.odometro_regreso,
        )
        return super().actualizar(entity_id, data)

    def validar_eliminar(self, obj: Viaje) -> None:
        if any(Decimal(s.abonado) > CERO for s in obj.servicios_vigentes):
            raise BusinessError(
                "No se puede eliminar un viaje con abonos registrados; "
                "elimine primero los abonos o anúlelo"
            )

    def finalizar(self, viaje_id: uuid.UUID, payload: Any) -> Viaje:
        viaje = self.repo.get_or_fail(viaje_id)
        if viaje.estado != ESTADO_VIAJE_EN_CURSO:
            raise BusinessError("Solo se puede finalizar un viaje en curso")
        fecha_regreso = payload.fecha_regreso or date.today()
        self._validar_coherencia(
            viaje.fecha_salida, fecha_regreso, viaje.odometro_salida, payload.odometro_regreso
        )
        antes = {"estado": viaje.estado}
        viaje.fecha_regreso = fecha_regreso
        if payload.odometro_regreso is not None:
            viaje.odometro_regreso = Decimal(payload.odometro_regreso)
            _actualizar_odometro(viaje.vehiculo, viaje.odometro_regreso)
        viaje.estado = ESTADO_VIAJE_FINALIZADO
        viaje.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", viaje.id, antes, {"estado": viaje.estado})
        return viaje

    def reabrir(self, viaje_id: uuid.UUID) -> Viaje:
        """Volver a en curso para corregir servicios o gastos de un viaje
        finalizado (los abonos nunca se bloquean: la cartera se cobra después)."""
        viaje = self.repo.get_or_fail(viaje_id)
        if viaje.estado != ESTADO_VIAJE_FINALIZADO:
            raise BusinessError("Solo se puede reabrir un viaje finalizado")
        antes = {"estado": viaje.estado}
        viaje.estado = ESTADO_VIAJE_EN_CURSO
        viaje.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", viaje.id, antes, {"estado": viaje.estado})
        return viaje

    def anular(self, viaje_id: uuid.UUID) -> Viaje:
        """Anula el viaje y sus servicios en cascada. Exige cero abonos: si hay
        plata recibida hay que devolverla (eliminar los abonos) primero."""
        viaje = self.repo.get_or_fail(viaje_id)
        if viaje.estado == ESTADO_VIAJE_ANULADO:
            raise BusinessError("El viaje ya está anulado")
        if any(Decimal(s.abonado) > CERO for s in viaje.servicios_vigentes):
            raise BusinessError("No se puede anular un viaje con abonos registrados")
        antes = {"estado": viaje.estado}
        for servicio in viaje.servicios_vigentes:
            if servicio.estado != ESTADO_SERVICIO_ANULADA:
                servicio.estado = ESTADO_SERVICIO_ANULADA
                servicio.updated_by = self.ctx.user_id
        viaje.estado = ESTADO_VIAJE_ANULADO
        viaje.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", viaje.id, antes, {"estado": viaje.estado})
        return viaje

    def listar_filtrado(
        self, params: PageParams, *, search: str | None, estado: str | None,
        vehiculo_id: uuid.UUID | None, desde: date | None, hasta: date | None,
    ) -> tuple[list[Viaje], int]:
        extra = []
        if desde:
            extra.append(Viaje.fecha_salida >= desde)
        if hasta:
            extra.append(Viaje.fecha_salida <= hasta)
        return self.repo.list_paginated(
            params, search=search, estado=estado,
            filters={"vehiculo_id": vehiculo_id}, extra_criteria=extra,
        )


class ViajeServicioService(BaseService[ViajeServicio]):
    repository_cls = ViajeServicioRepository
    modulo = "transporte"

    def _viajes(self) -> ViajeRepository:
        return ViajeRepository(self.db, self.ctx.empresa_id)

    @staticmethod
    def _exigir_en_curso(viaje: Viaje) -> None:
        """Con el viaje finalizado se bloquea tocar servicios y gastos (se
        reabre para corregir); los abonos NO pasan por aquí a propósito."""
        if viaje.estado == ESTADO_VIAJE_ANULADO:
            raise BusinessError("El viaje está anulado")
        if viaje.estado == ESTADO_VIAJE_FINALIZADO:
            raise BusinessError(
                "El viaje está finalizado: reábralo para modificar sus servicios"
            )

    @staticmethod
    def _servicio_de(viaje: Viaje, servicio_id: uuid.UUID) -> ViajeServicio:
        servicio = next(
            (s for s in viaje.servicios_vigentes if s.id == servicio_id), None
        )
        if servicio is None:
            raise NotFoundError("Servicio no encontrado en este viaje")
        return servicio

    def _resolver(
        self,
        data: dict[str, Any],
        viaje: Viaje,
        actual: ViajeServicio | None = None,
        de_contado: bool = False,
    ) -> dict[str, Any]:
        """Aplica las reglas del servicio y calcula el valor total. Con `actual`
        (edición) los campos que no vienen conservan el valor guardado."""

        def vigente(campo: str, defecto: Any = None) -> Any:
            if campo in data:
                return data[campo]
            if actual is not None:
                return getattr(actual, campo)
            return defecto

        es_interno = bool(vigente("es_interno", False))
        data["es_interno"] = es_interno
        tipo_cobro = vigente("tipo_cobro", COBRO_POR_KILO)
        if es_interno:
            # Queso propio: se valora a tarifa por kilo para medir la
            # rentabilidad real del viaje, pero no es plata que entre.
            if de_contado:
                raise BusinessError(
                    "Un servicio interno (queso propio) no genera cobro: "
                    "no puede ir pagado de contado"
                )
            if data.get("cliente_id") or data.get("cliente_nombre"):
                raise BusinessError("Un servicio interno (queso propio) no lleva cliente")
            tipo_cobro = COBRO_POR_KILO
            data["cliente_id"] = None
            data["cliente_nombre"] = None
        data["tipo_cobro"] = tipo_cobro

        if tipo_cobro == COBRO_POR_KILO:
            kilos = vigente("kilos")
            if kilos is None or Decimal(kilos) <= CERO:
                raise BusinessError("Un servicio por kilo exige los kilos transportados")
            tarifa = vigente("tarifa_kilo")
            if tarifa is None:
                tarifa = viaje.vehiculo.tarifa_kilo
            if Decimal(tarifa) <= CERO:
                raise BusinessError(
                    "Defina la tarifa por kilo (el vehículo no tiene tarifa base)"
                )
            data["kilos"] = Decimal(kilos)
            data["tarifa_kilo"] = Decimal(tarifa)
            data["valor_total"] = (Decimal(kilos) * Decimal(tarifa)).quantize(DOS_DECIMALES)
        else:
            valor_total = vigente("valor_total")
            if valor_total is None or Decimal(valor_total) <= CERO:
                raise BusinessError("Un servicio a precio fijo exige el valor acordado")
            data["valor_total"] = Decimal(valor_total)

        if not es_interno:
            cliente_id = vigente("cliente_id")
            cliente_nombre = vigente("cliente_nombre")
            if not cliente_id and not cliente_nombre:
                raise BusinessError(
                    "El servicio necesita un cliente: uno del directorio o el "
                    "nombre del ocasional"
                )
            if cliente_id:
                # Valida que exista Y que sea de esta empresa (aísla el tenant)
                cliente = ClienteRepository(self.db, self.ctx.empresa_id).get_or_fail(
                    cliente_id
                )
                # Se guarda el nombre como quedó en el directorio para mostrarlo
                # sin otra vuelta a la base
                if "cliente_id" in data and not data.get("cliente_nombre"):
                    data["cliente_nombre"] = cliente.nombre
            else:
                abonado = Decimal(actual.abonado) if actual is not None else CERO
                if not de_contado and Decimal(data["valor_total"]) > abonado:
                    raise BusinessError(
                        "Un servicio a crédito exige un cliente del directorio; "
                        "los ocasionales pagan de contado"
                    )
        return data

    def crear_en_viaje(self, viaje_id: uuid.UUID, payload: Any) -> ViajeServicio:
        viaje = self._viajes().get_or_fail(viaje_id)
        self._exigir_en_curso(viaje)
        data = payload.model_dump(exclude_unset=True)
        de_contado = data.pop("pagado_de_contado", False)
        data = self._resolver(data, viaje, de_contado=de_contado)
        data["viaje_id"] = viaje.id
        if data["es_interno"]:
            data["estado"] = ESTADO_SERVICIO_INTERNO
        elif de_contado:
            data["abonado"] = data["valor_total"]
            data["estado"] = ESTADO_SERVICIO_PAGADA
        else:
            data["estado"] = ESTADO_SERVICIO_PENDIENTE
        servicio = super().crear(data)
        if de_contado:
            servicio.abonos.append(
                AbonoFlete(
                    fecha=viaje.fecha_salida, valor=servicio.valor_total,
                    metodo=METODO_EFECTIVO, observaciones="Pago de contado",
                    created_by=self.ctx.user_id,
                )
            )
            self.db.flush()
        return servicio

    def actualizar_en_viaje(
        self, viaje_id: uuid.UUID, servicio_id: uuid.UUID, payload: Any
    ) -> ViajeServicio:
        viaje = self._viajes().get_or_fail(viaje_id)
        self._exigir_en_curso(viaje)
        servicio = self._servicio_de(viaje, servicio_id)
        if servicio.estado == ESTADO_SERVICIO_ANULADA:
            raise BusinessError("No se puede modificar un servicio anulado")
        data = payload.model_dump(exclude_unset=True)
        if (
            "es_interno" in data
            and bool(data["es_interno"]) != servicio.es_interno
            and Decimal(servicio.abonado) > CERO
        ):
            raise BusinessError(
                "No se puede cambiar entre interno y tercero un servicio con abonos"
            )
        data = self._resolver(data, viaje, actual=servicio)
        if Decimal(data["valor_total"]) < Decimal(servicio.abonado):
            raise BusinessError(
                f"El total no puede quedar por debajo de lo ya abonado "
                f"({pesos(servicio.abonado)}); elimine primero los abonos que sobren"
            )
        if data["es_interno"]:
            data["estado"] = ESTADO_SERVICIO_INTERNO
        else:
            data["estado"] = _estado_pago(data["valor_total"], servicio.abonado)
        return super().actualizar(servicio.id, data)

    def eliminar_en_viaje(self, viaje_id: uuid.UUID, servicio_id: uuid.UUID) -> None:
        viaje = self._viajes().get_or_fail(viaje_id)
        self._exigir_en_curso(viaje)
        servicio = self._servicio_de(viaje, servicio_id)
        if Decimal(servicio.abonado) > CERO:
            raise BusinessError(
                "No se puede eliminar un servicio con abonos; "
                "elimine primero los abonos o anúlelo"
            )
        antes = serialize_entity(servicio)
        # Se saca de la relación (el cascade delete-orphan borra la fila): así
        # los totales del viaje quedan al día en la misma respuesta.
        viaje.servicios.remove(servicio)
        self.db.flush()
        self._audit("eliminar", servicio.id, antes, None)

    def anular_en_viaje(self, viaje_id: uuid.UUID, servicio_id: uuid.UUID) -> ViajeServicio:
        viaje = self._viajes().get_or_fail(viaje_id)
        self._exigir_en_curso(viaje)
        servicio = self._servicio_de(viaje, servicio_id)
        if servicio.estado == ESTADO_SERVICIO_ANULADA:
            raise BusinessError("El servicio ya está anulado")
        if Decimal(servicio.abonado) > CERO:
            raise BusinessError("No se puede anular un servicio con abonos registrados")
        antes = servicio.estado
        servicio.estado = ESTADO_SERVICIO_ANULADA
        servicio.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", servicio.id, {"estado": antes}, {"estado": servicio.estado})
        return servicio

    # ----------------------------------------------------------------- abonos
    # SIEMPRE permitidos con el viaje finalizado: la cartera se cobra después.
    def registrar_abono(self, servicio_id: uuid.UUID, payload: Any) -> ViajeServicio:
        servicio = self.repo.get_or_fail(servicio_id)
        if servicio.estado == ESTADO_SERVICIO_ANULADA:
            raise BusinessError("El servicio está anulado")
        if servicio.es_interno:
            raise BusinessError(
                "Un servicio interno (queso propio) no genera cartera: no recibe abonos"
            )
        if servicio.viaje.estado == ESTADO_VIAJE_ANULADO:
            raise BusinessError("El viaje está anulado")
        valor = Decimal(payload.valor)
        if valor > servicio.saldo:
            # pesos() y no "{:,.0f}": el formato con coma es gringo y
            # "$1,200,000" en Colombia se lee como un peso con veinte centavos.
            raise BusinessError(
                f"El abono ({pesos(valor)}) supera el saldo ({pesos(servicio.saldo)})"
            )
        # Se agrega A LA RELACIÓN, no con db.add(): la colección ya viene
        # cargada (lazy="selectin") y un db.add() suelto la dejaría
        # desactualizada, así que la respuesta saldría sin el abono.
        servicio.abonos.append(
            AbonoFlete(
                fecha=payload.fecha, valor=valor, metodo=payload.metodo,
                referencia=payload.referencia, observaciones=payload.observaciones,
                created_by=self.ctx.user_id,
            )
        )
        servicio.abonado += valor
        servicio.estado = _estado_pago(servicio.valor_total, servicio.abonado)
        servicio.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit(
            "editar", servicio.id, None, {"abono": float(valor), "estado": servicio.estado}
        )
        return servicio

    def eliminar_abono(self, servicio_id: uuid.UUID, abono_id: uuid.UUID) -> ViajeServicio:
        """Elimina un abono mal registrado: baja el abonado y recalcula el estado."""
        servicio = self.repo.get_or_fail(servicio_id)
        abono = next((a for a in servicio.abonos if a.id == abono_id), None)
        if abono is None:
            raise NotFoundError("Abono no encontrado")
        valor = Decimal(abono.valor)
        servicio.abonado = max(servicio.abonado - valor, CERO)
        servicio.estado = _estado_pago(servicio.valor_total, servicio.abonado)
        servicio.updated_by = self.ctx.user_id
        # Se saca de la relación (el cascade delete-orphan borra la fila): así
        # la colección en memoria queda igual que la base.
        servicio.abonos.remove(abono)
        self.db.flush()
        self._audit(
            "editar", servicio.id, None,
            {"abono_eliminado": float(valor), "estado": servicio.estado},
        )
        return servicio


class VehiculoGastoService(BaseService[VehiculoGasto]):
    repository_cls = VehiculoGastoRepository
    modulo = "transporte"

    def _viaje_modificable(self, viaje_id: uuid.UUID | None) -> None:
        """Un gasto atado a un viaje solo se toca con el viaje en curso."""
        if viaje_id is None:
            return
        viaje = ViajeRepository(self.db, self.ctx.empresa_id).get(viaje_id)
        if viaje is None:
            return  # el viaje ya no existe: no puede bloquear la corrección
        if viaje.estado == ESTADO_VIAJE_ANULADO:
            raise BusinessError("El viaje está anulado")
        if viaje.estado == ESTADO_VIAJE_FINALIZADO:
            raise BusinessError(
                "El viaje está finalizado: reábralo para modificar sus gastos"
            )

    def _validar_relaciones(
        self, data: dict[str, Any], actual: VehiculoGasto | None = None
    ) -> Vehiculo:
        """Coherencia vehículo↔viaje: el gasto de un viaje es del mismo vehículo
        del viaje, y el viaje debe estar en curso."""
        vehiculo_id = data.get("vehiculo_id") or (actual.vehiculo_id if actual else None)
        viaje_id = data["viaje_id"] if "viaje_id" in data else (
            actual.viaje_id if actual else None
        )
        vehiculo = VehiculoRepository(self.db, self.ctx.empresa_id).get_or_fail(vehiculo_id)
        if viaje_id is not None:
            viaje = ViajeRepository(self.db, self.ctx.empresa_id).get_or_fail(viaje_id)
            if viaje.estado == ESTADO_VIAJE_ANULADO:
                raise BusinessError("El viaje está anulado")
            if viaje.estado == ESTADO_VIAJE_FINALIZADO:
                raise BusinessError(
                    "El viaje está finalizado: reábralo para modificar sus gastos"
                )
            if viaje.vehiculo_id != vehiculo.id:
                raise BusinessError("El gasto debe ser del mismo vehículo del viaje")
        return vehiculo

    def crear(self, payload: Any) -> VehiculoGasto:
        data = payload.model_dump(exclude_unset=True)
        vehiculo = self._validar_relaciones(data)
        gasto = super().crear(data)
        _actualizar_odometro(vehiculo, gasto.odometro)
        return gasto

    def crear_en_viaje(self, viaje_id: uuid.UUID, payload: Any) -> VehiculoGasto:
        """Atajo desde el detalle del viaje: fija el viaje y su vehículo."""
        viaje = ViajeRepository(self.db, self.ctx.empresa_id).get_or_fail(viaje_id)
        data = payload.model_dump(exclude_unset=True)
        data["viaje_id"] = viaje.id
        data["vehiculo_id"] = viaje.vehiculo_id
        vehiculo = self._validar_relaciones(data)
        gasto = super().crear(data)
        _actualizar_odometro(vehiculo, gasto.odometro)
        return gasto

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> VehiculoGasto:
        actual = self.repo.get_or_fail(entity_id)
        self._viaje_modificable(actual.viaje_id)
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        vehiculo = self._validar_relaciones(data, actual)
        gasto = super().actualizar(entity_id, data)
        _actualizar_odometro(vehiculo, gasto.odometro)
        return gasto

    def validar_eliminar(self, obj: VehiculoGasto) -> None:
        self._viaje_modificable(obj.viaje_id)

    def listar_de_viaje(self, viaje_id: uuid.UUID) -> list[VehiculoGasto]:
        viaje = ViajeRepository(self.db, self.ctx.empresa_id).get_or_fail(viaje_id)
        return viaje.gastos_vigentes

    def listar_filtrado(
        self, params: PageParams, *, search: str | None = None,
        vehiculo_id: uuid.UUID | None = None, viaje_id: uuid.UUID | None = None,
        categoria: str | None = None, desde: date | None = None,
        hasta: date | None = None, solo_generales: bool = False,
    ) -> tuple[list[VehiculoGasto], int]:
        extra = []
        if desde:
            extra.append(VehiculoGasto.fecha >= desde)
        if hasta:
            extra.append(VehiculoGasto.fecha <= hasta)
        if solo_generales:
            extra.append(VehiculoGasto.viaje_id.is_(None))
        return self.repo.list_paginated(
            params, search=search,
            filters={"vehiculo_id": vehiculo_id, "viaje_id": viaje_id, "categoria": categoria},
            extra_criteria=extra,
        )

    def adjuntar_archivo(self, entity_id: uuid.UUID, file: UploadFile) -> VehiculoGasto:
        gasto = self.repo.get_or_fail(entity_id)
        gasto.adjunto_url = save_upload(
            file, empresa_id=self.ctx.empresa_id, subdir="transporte"
        )
        gasto.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", gasto.id, None, {"adjunto_url": gasto.adjunto_url})
        return gasto


class VehiculoMantenimientoService(BaseService[VehiculoMantenimiento]):
    repository_cls = VehiculoMantenimientoRepository
    modulo = "transporte"

    def crear(self, payload: Any) -> VehiculoMantenimiento:
        data = payload.model_dump(exclude_unset=True)
        vehiculo = VehiculoRepository(self.db, self.ctx.empresa_id).get_or_fail(
            data["vehiculo_id"]
        )
        mantenimiento = super().crear(data)
        _actualizar_odometro(vehiculo, mantenimiento.odometro)
        return mantenimiento

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> VehiculoMantenimiento:
        actual = self.repo.get_or_fail(entity_id)
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        vehiculo = VehiculoRepository(self.db, self.ctx.empresa_id).get_or_fail(
            data.get("vehiculo_id") or actual.vehiculo_id
        )
        mantenimiento = super().actualizar(entity_id, data)
        _actualizar_odometro(vehiculo, mantenimiento.odometro)
        return mantenimiento

    def listar_filtrado(
        self, params: PageParams, *, search: str | None = None,
        vehiculo_id: uuid.UUID | None = None, tipo: str | None = None,
        desde: date | None = None, hasta: date | None = None,
    ) -> tuple[list[VehiculoMantenimiento], int]:
        extra = []
        if desde:
            extra.append(VehiculoMantenimiento.fecha >= desde)
        if hasta:
            extra.append(VehiculoMantenimiento.fecha <= hasta)
        return self.repo.list_paginated(
            params, search=search,
            filters={"vehiculo_id": vehiculo_id, "tipo": tipo}, extra_criteria=extra,
        )

    def adjuntar_archivo(self, entity_id: uuid.UUID, file: UploadFile) -> VehiculoMantenimiento:
        mantenimiento = self.repo.get_or_fail(entity_id)
        mantenimiento.adjunto_url = save_upload(
            file, empresa_id=self.ctx.empresa_id, subdir="transporte"
        )
        mantenimiento.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", mantenimiento.id, None, {"adjunto_url": mantenimiento.adjunto_url})
        return mantenimiento


class VehiculoDocumentoService(BaseService[VehiculoDocumento]):
    repository_cls = VehiculoDocumentoRepository
    modulo = "transporte"

    @staticmethod
    def _validar_fechas(expedicion: date | None, vencimiento: date | None) -> None:
        if expedicion is not None and vencimiento is not None and vencimiento < expedicion:
            raise BusinessError(
                "La fecha de vencimiento no puede ser anterior a la de expedición"
            )

    def crear(self, payload: Any) -> VehiculoDocumento:
        data = payload.model_dump(exclude_unset=True)
        VehiculoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["vehiculo_id"])
        self._validar_fechas(data.get("fecha_expedicion"), data.get("fecha_vencimiento"))
        return super().crear(data)

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> VehiculoDocumento:
        actual = self.repo.get_or_fail(entity_id)
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        if data.get("vehiculo_id"):
            VehiculoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["vehiculo_id"])
        self._validar_fechas(
            data["fecha_expedicion"] if "fecha_expedicion" in data else actual.fecha_expedicion,
            data.get("fecha_vencimiento") or actual.fecha_vencimiento,
        )
        return super().actualizar(entity_id, data)

    def listar_filtrado(
        self, params: PageParams, *, search: str | None = None,
        vehiculo_id: uuid.UUID | None = None, tipo: str | None = None,
        desde: date | None = None, hasta: date | None = None,
    ) -> tuple[list[VehiculoDocumento], int]:
        """El rango de fechas filtra por VENCIMIENTO, que es lo que se busca."""
        extra = []
        if desde:
            extra.append(VehiculoDocumento.fecha_vencimiento >= desde)
        if hasta:
            extra.append(VehiculoDocumento.fecha_vencimiento <= hasta)
        return self.repo.list_paginated(
            params, search=search,
            filters={"vehiculo_id": vehiculo_id, "tipo": tipo}, extra_criteria=extra,
        )

    def adjuntar_archivo(self, entity_id: uuid.UUID, file: UploadFile) -> VehiculoDocumento:
        documento = self.repo.get_or_fail(entity_id)
        documento.adjunto_url = save_upload(
            file, empresa_id=self.ctx.empresa_id, subdir="transporte"
        )
        documento.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", documento.id, None, {"adjunto_url": documento.adjunto_url})
        return documento


class TransporteReporteService:
    """Cartera de fletes, resumen del período y alertas de vencimiento
    (independiente de contabilidad, como el resumen de reventa)."""

    def __init__(self, db, ctx):
        self.db = db
        self.ctx = ctx
        self.viajes = ViajeRepository(db, ctx.empresa_id)
        self.servicios = ViajeServicioRepository(db, ctx.empresa_id)
        self.gastos = VehiculoGastoRepository(db, ctx.empresa_id)
        self.mantenimientos = VehiculoMantenimientoRepository(db, ctx.empresa_id)
        self.documentos = VehiculoDocumentoRepository(db, ctx.empresa_id)
        self.vehiculos = VehiculoRepository(db, ctx.empresa_id)

    # ------------------------------------------------------------------ cartera
    def cartera(self) -> list[CarteraFleteCliente]:
        """Saldo por cliente (directorio y ocasionales), de mayor a menor."""
        filas = self.servicios.cartera_directorio() + self.servicios.cartera_ocasionales()
        resultado = [
            CarteraFleteCliente(
                cliente_id=fila[0],
                cliente_nombre=fila[1],
                servicios_pendientes=int(fila[2]),
                total_facturado=Decimal(fila[3]),
                total_abonado=Decimal(fila[4]),
                saldo=Decimal(fila[3]) - Decimal(fila[4]),
            )
            for fila in filas
        ]
        resultado.sort(key=lambda fila: fila.saldo, reverse=True)
        return resultado

    def cartera_detalle(
        self, cliente_id: uuid.UUID | None, cliente_nombre: str | None
    ) -> CarteraFleteDetalle:
        if cliente_id is None and not (cliente_nombre or "").strip():
            raise BusinessError("Indique el cliente: cliente_id o cliente_nombre")
        if cliente_id is not None:
            cliente = ClienteRepository(self.db, self.ctx.empresa_id).get_or_fail(cliente_id)
            nombre = cliente.nombre
        else:
            nombre = " ".join(cliente_nombre.split())
        servicios = self.servicios.pendientes_de_cliente(
            cliente_id=cliente_id, cliente_nombre=cliente_nombre
        )
        filas = [
            ServicioCarteraRead(
                id=s.id,
                viaje_id=s.viaje_id,
                viaje_numero=s.viaje.numero,
                viaje_fecha=s.viaje.fecha_salida,
                sentido=s.sentido,
                tipo_cobro=s.tipo_cobro,
                descripcion=s.descripcion,
                kilos=s.kilos,
                tarifa_kilo=s.tarifa_kilo,
                valor_total=s.valor_total,
                abonado=s.abonado,
                saldo=s.saldo,
                estado=s.estado,
                abonos=[AbonoFleteRead.model_validate(a) for a in s.abonos],
            )
            for s in servicios
        ]
        total_facturado = sum((f.valor_total for f in filas), CERO)
        total_abonado = sum((f.abonado for f in filas), CERO)
        return CarteraFleteDetalle(
            cliente_id=cliente_id,
            cliente_nombre=nombre,
            servicios=filas,
            total_facturado=total_facturado,
            total_abonado=total_abonado,
            saldo=total_facturado - total_abonado,
        )

    # ------------------------------------------------------------------ resumen
    @staticmethod
    def _meses(desde: date, hasta: date) -> list[str]:
        """Todos los meses del rango ("2026-07"), incluidos los sin movimiento:
        la gráfica necesita el eje completo."""
        meses: list[str] = []
        anio, mes = desde.year, desde.month
        while (anio, mes) <= (hasta.year, hasta.month):
            meses.append(f"{anio:04d}-{mes:02d}")
            anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
        return meses

    def _serie_mensual(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None
    ) -> list[SerieMensualTransporte]:
        """Ingresos y egresos por mes. Se agrupa en PYTHON a propósito:
        strftime (SQLite) y date_trunc (Postgres) no son portables y las
        pruebas corren en SQLite. Los egresos suman los mismos buckets de la
        utilidad neta, así la serie cuadra con la cifra grande."""
        ingresos: dict[str, Decimal] = {}
        egresos: dict[str, Decimal] = {}

        def acumular(bucket: dict[str, Decimal], filas: list[tuple[date, Decimal]]) -> None:
            for fecha, valor in filas:
                clave = f"{fecha.year:04d}-{fecha.month:02d}"
                bucket[clave] = bucket.get(clave, CERO) + valor

        acumular(ingresos, self.servicios.filas_para_serie(desde, hasta, vehiculo_id))
        acumular(egresos, self.gastos.filas_para_serie(desde, hasta, vehiculo_id))
        acumular(egresos, self.viajes.filas_para_serie(desde, hasta, vehiculo_id))
        acumular(egresos, self.mantenimientos.filas_para_serie(desde, hasta, vehiculo_id))
        acumular(egresos, self.documentos.filas_para_serie(desde, hasta, vehiculo_id))

        serie = []
        for mes in self._meses(desde, hasta):
            entrada = ingresos.get(mes, CERO)
            salida = egresos.get(mes, CERO)
            serie.append(
                SerieMensualTransporte(
                    mes=mes, ingresos=entrada, gastos=salida, utilidad=entrada - salida
                )
            )
        return serie

    def resumen(
        self, desde: date, hasta: date, vehiculo_id: uuid.UUID | None = None
    ) -> ResumenTransporte:
        if vehiculo_id is not None:
            self.vehiculos.get_or_fail(vehiculo_id)
        viajes_realizados, total_pago_conductores, kilometros = self.viajes.totales_periodo(
            desde, hasta, vehiculo_id
        )
        ingresos_terceros, ingresos_internos, kilos = self.servicios.ingresos_periodo(
            desde, hasta, vehiculo_id
        )
        por_categoria = dict(self.gastos.por_categoria_periodo(desde, hasta, vehiculo_id))
        total_gastos = sum(por_categoria.values(), CERO)
        total_mantenimientos = self.mantenimientos.total_periodo(desde, hasta, vehiculo_id)
        total_documentos = self.documentos.total_periodo(desde, hasta, vehiculo_id)
        total_ingresos = ingresos_terceros + ingresos_internos
        utilidad_operativa = (
            total_ingresos - total_gastos - total_pago_conductores
        ).quantize(DOS_DECIMALES)
        utilidad_neta = (
            utilidad_operativa - total_mantenimientos - total_documentos
        ).quantize(DOS_DECIMALES)
        return ResumenTransporte(
            desde=desde,
            hasta=hasta,
            vehiculo_id=vehiculo_id,
            viajes_realizados=viajes_realizados,
            kilos_transportados=kilos,
            kilometros=kilometros,
            ingresos_terceros=ingresos_terceros,
            ingresos_internos=ingresos_internos,
            total_ingresos=total_ingresos,
            total_pago_conductores=total_pago_conductores,
            gastos_por_categoria=por_categoria,
            total_gastos=total_gastos,
            total_mantenimientos=total_mantenimientos,
            total_documentos=total_documentos,
            utilidad_operativa=utilidad_operativa,
            utilidad_neta=utilidad_neta,
            por_cobrar=self.servicios.por_cobrar(vehiculo_id),
            serie_mensual=self._serie_mensual(desde, hasta, vehiculo_id),
        )

    # ------------------------------------------------------------------ alertas
    def alertas(
        self, dias: int, umbral_km: Decimal, vehiculo_id: uuid.UUID | None = None
    ) -> AlertasTransporte:
        """Documentos por vencer/vencidos y mantenimientos próximos.

        De los documentos solo se evalúa EL DE VENCIMIENTO MÁS RECIENTE por
        (vehículo, tipo): la renovación es un registro nuevo y sin este filtro
        las vigencias viejas alertarían eternamente. Igual con el último
        mantenimiento por (vehículo, tipo) que anuncia el próximo.
        """
        hoy = date.today()

        documentos: list[AlertaDocumento] = []
        vistos: set[tuple[uuid.UUID, str]] = set()
        # Vienen del vencimiento más reciente al más viejo: el primero por
        # (vehículo, tipo) es el vigente y los demás son historia.
        for doc in self.documentos.todos_para_alertas(vehiculo_id):
            clave = (doc.vehiculo_id, doc.tipo)
            if clave in vistos:
                continue
            vistos.add(clave)
            restantes = (doc.fecha_vencimiento - hoy).days
            if restantes > dias:
                continue
            documentos.append(
                AlertaDocumento(
                    documento_id=doc.id,
                    vehiculo_id=doc.vehiculo_id,
                    vehiculo_placa=doc.vehiculo.placa,
                    vehiculo_nombre=doc.vehiculo.nombre,
                    tipo=doc.tipo,
                    descripcion=doc.descripcion,
                    numero=doc.numero,
                    fecha_vencimiento=doc.fecha_vencimiento,
                    dias_restantes=restantes,
                    estado="vencido" if restantes < 0 else "por_vencer",
                )
            )
        documentos.sort(key=lambda alerta: alerta.dias_restantes)

        mantenimientos: list[AlertaMantenimiento] = []
        vistos = set()
        for mant in self.mantenimientos.con_proximo(vehiculo_id):
            clave = (mant.vehiculo_id, mant.tipo)
            if clave in vistos:
                continue
            vistos.add(clave)
            dias_restantes = (
                (mant.proxima_fecha - hoy).days if mant.proxima_fecha is not None else None
            )
            km_restantes = (
                Decimal(mant.proximo_odometro) - Decimal(mant.vehiculo.odometro_actual or CERO)
                if mant.proximo_odometro is not None
                else None
            )
            por_fecha = dias_restantes is not None and dias_restantes <= dias
            por_kilometraje = km_restantes is not None and km_restantes <= Decimal(umbral_km)
            if not por_fecha and not por_kilometraje:
                continue
            vencido = (dias_restantes is not None and dias_restantes < 0) or (
                km_restantes is not None and km_restantes < CERO
            )
            mantenimientos.append(
                AlertaMantenimiento(
                    mantenimiento_id=mant.id,
                    vehiculo_id=mant.vehiculo_id,
                    vehiculo_placa=mant.vehiculo.placa,
                    vehiculo_nombre=mant.vehiculo.nombre,
                    tipo=mant.tipo,
                    descripcion=mant.descripcion,
                    fecha=mant.fecha,
                    proxima_fecha=mant.proxima_fecha,
                    proximo_odometro=mant.proximo_odometro,
                    dias_restantes=dias_restantes,
                    km_restantes=km_restantes,
                    estado="vencido" if vencido else "por_vencer",
                )
            )
        return AlertasTransporte(documentos=documentos, mantenimientos=mantenimientos)
