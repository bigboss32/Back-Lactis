import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import lazyload

from app.common.nombres import canonizar_nombre, clave_de_tercero, unir_nombres
from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, NotFoundError
from app.core.pagination import PageParams
from app.modules.clientes.repository import ClienteRepository
from app.modules.empresas.models import Empresa
from app.modules.inventario.repository import ProductoRepository
from app.modules.inventario.models import MovimientoInventario
from app.modules.ventas.models import (
    ESTADO_ANULADA,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    ESTADO_PENDIENTE,
    Pago,
    PagoConductor,
    Venta,
    VentaDetalle,
    VentaTramoFlete,
)
from app.modules.ventas.repository import (
    PagoConductorRepository,
    PagoRepository,
    VentaRepository,
)
from app.modules.ventas.schemas import (
    CarteraCliente,
    ConductoresPanel,
    ConductorResumen,
    ConductorTramoRead,
    PagoConductorRead,
)
from app.utils.export import pesos

CERO = Decimal("0")
DOS_DECIMALES = Decimal("0.01")


def _gasto_de_despacho(por_kilo: Decimal, kilos: Decimal) -> Decimal:
    """Lo que cuesta UN tramo: su valor por kilo por los kilos que van.

    Se calcula sobre los kilos de TODOS los renglones de la venta, porque el flete
    se paga por el peso que sube al camión y no por producto. Igual que en las
    ventas de reventa.

    NO se le suma al total que paga el cliente: es un costo de la quesera, y es lo
    que hace que el kilo puesto en destino valga más que el kilo en la planta.
    """
    return (Decimal(por_kilo or 0) * Decimal(kilos or 0)).quantize(DOS_DECIMALES)


def _ruta_en_cristiano(tramos: list[VentaTramoFlete]) -> str | None:
    """La ruta completa leída de corrido: "Quesera → San Vicente → Bogotá".

    Es lo que queda en `Venta.gasto_concepto`, que es el campo que ya mostraban
    la lista de ventas y los informes. Con un solo tramo devuelve su destino tal
    cual, así que un flete de siempre ("Transporte a Bogotá") se sigue viendo
    exactamente igual que antes de que existieran los tramos.

    Los puntos repetidos se colapsan: el destino de un tramo suele ser el origen
    del siguiente, y "San Vicente → San Vicente" no le dice nada a nadie.
    """
    partes: list[str] = []
    for tramo in tramos:
        for punto in (tramo.origen, tramo.destino):
            if punto and (not partes or partes[-1] != punto):
                partes.append(punto)
    return " → ".join(partes)[:150] or None


def _resumen_del_flete(tramos: list[VentaTramoFlete]) -> tuple[str | None, Decimal, Decimal]:
    """(ruta, suma de los por-kilo, suma de los totales) de los tramos.

    LOS TOTALES SE SUMAN YA REDONDEADOS, uno por uno. No se recalcula
    `suma_de_por_kilo × kilos`, que es lo que saldría "natural": con centavos de
    por medio las dos cuentas pueden diferir en un peso, y entonces el desglose
    que el dueño ve en pantalla no sumaría la cifra grande que resta la utilidad.
    Sumando los sumandos, cuadra siempre.
    """
    por_kilo = sum((Decimal(t.valor_por_kilo or 0) for t in tramos), CERO)
    monto = sum((Decimal(t.valor_total or 0) for t in tramos), CERO)
    return _ruta_en_cristiano(tramos), por_kilo, monto


class VentaService(BaseService[Venta]):
    repository_cls = VentaRepository
    modulo = "ventas"

    # ------------------------------------------------------------ flete/tramos
    def _nombres_de_conductores(self) -> list[str]:
        """Contra qué lista se canoniza el nombre del conductor: los que ya
        manejaron algún tramo MÁS los que ya han recibido un pago.

        Primero los de los tramos: si el nombre está en los dos lados manda la
        escritura de los despachos, que es la que agrupa lo que se le debe.
        """
        return unir_nombres(
            self.repo.nombres_conductores(),
            PagoConductorRepository(self.db, self.ctx.empresa_id).nombres_conductores(),
        )

    def _tramos_desde_datos(
        self, tramos_data: list[dict[str, Any]], kilos: Decimal
    ) -> list[VentaTramoFlete]:
        """Arma los tramos del despacho a partir de lo que llegó del formulario.

        Cada tramo calcula su propio total (`valor_por_kilo × kilos`) y lo guarda
        redondeado: ese es el sumando exacto del flete de la venta.

        Los nombres de conductor se canonizan contra los ya usados —una sola
        consulta para todos los tramos, no una por tramo— para que "JOSE LAVADO"
        y "Jose lavado" sean el mismo señor y su deuda no salga partida en dos.
        """
        if not tramos_data:
            return []
        ya_usados = (
            self._nombres_de_conductores()
            if any((t.get("conductor") or "").strip() for t in tramos_data)
            else []
        )
        tramos: list[VentaTramoFlete] = []
        for indice, datos in enumerate(tramos_data, start=1):
            crudo = (datos.get("conductor") or "").strip()
            conductor = canonizar_nombre(crudo, ya_usados) if crudo else None
            if conductor and conductor not in ya_usados:
                # Se agrega a la lista en caliente: si el mismo despacho lleva
                # dos tramos del mismo señor escritos distinto, el segundo adopta
                # la escritura del primero sin esperar a que se guarde.
                ya_usados.append(conductor)
            por_kilo = Decimal(datos.get("valor_por_kilo") or CERO)
            tramos.append(
                VentaTramoFlete(
                    orden=indice,
                    origen=(datos.get("origen") or "").strip() or None,
                    destino=(datos.get("destino") or "").strip() or None,
                    conductor=conductor,
                    conductor_clave=clave_de_tercero(conductor) if conductor else None,
                    valor_por_kilo=por_kilo,
                    valor_total=_gasto_de_despacho(por_kilo, kilos),
                )
            )
        return tramos

    @staticmethod
    def _legado_a_tramos(
        concepto: str | None, por_kilo: Decimal | None
    ) -> list[dict[str, Any]]:
        """Traduce el flete de un solo valor (`gasto_concepto` + `gasto_por_kilo`)
        a la lista de tramos, para no romper a quien ya llamaba así.

        Con el valor por kilo en cero NO se crea ningún tramo, que es exactamente
        lo que pasaba antes: la venta queda con flete 0 aunque tenga escrito un
        concepto (el dueño anotó a dónde iba y después decidió que lo recogían).
        """
        if not por_kilo:
            return []
        return [
            {
                "origen": None,
                "destino": concepto,
                "conductor": None,
                "valor_por_kilo": por_kilo,
            }
        ]

    @staticmethod
    def _sincronizar_resumen(venta: Venta) -> None:
        """Pone al día `gasto_concepto`, `gasto_por_kilo` y `gasto_monto` con lo
        que digan los tramos.

        Es la ÚNICA puerta por la que se escriben esas tres columnas. Importa que
        sea una sola: todo lo que resta el flete de la utilidad (la pantalla de
        lotes de producción, el estado de resultados, la contabilidad) sigue
        leyendo `gasto_monto`, así que si alguien tocara los tramos sin pasar por
        aquí la utilidad mentiría sin que nada fallara.
        """
        concepto, por_kilo, monto = _resumen_del_flete(list(venta.tramos_flete))
        venta.gasto_concepto = concepto
        venta.gasto_por_kilo = por_kilo
        venta.gasto_monto = monto

    def _aplicar_flete(self, venta: Venta, tramos: list[VentaTramoFlete]) -> None:
        """Reemplaza los tramos de la venta y sincroniza el resumen.

        Asignar la colección borra los tramos anteriores (delete-orphan), igual
        que al reemplazar los renglones de la venta: son líneas del documento, no
        historia que haya que conservar.
        """
        venta.tramos_flete = tramos
        self._sincronizar_resumen(venta)

    def _recalcular_flete_por_kilos(self, venta: Venta, kilos: Decimal) -> None:
        """Vuelve a repartir el flete cuando cambiaron los KILOS del despacho.

        Los tramos y sus precios por kilo no se tocan; lo que cambia es el total
        de cada uno. Sin esto, editar los renglones dejaría el monto viejo y el
        kilo puesto en destino saldría mal.
        """
        for tramo in venta.tramos_flete:
            tramo.valor_total = _gasto_de_despacho(tramo.valor_por_kilo, kilos)
        self._sincronizar_resumen(venta)

    def crear(self, payload: Any) -> Venta:
        data = payload.model_dump(exclude_unset=True)
        detalles_data = data.pop("detalles")
        descontar_inventario = data.pop("descontar_inventario", True)
        # El flete sale de los tramos y de nadie más: se saca de `data` para que
        # el resumen no se escriba dos veces (una aquí y otra en _aplicar_flete).
        tramos_data = data.pop("tramos", None)
        gasto_concepto = data.pop("gasto_concepto", None)
        gasto_por_kilo = data.pop("gasto_por_kilo", None)

        # Serializa la numeración por empresa: evita números de venta duplicados
        # (y el error 500 del índice único) cuando dos ventas se crean a la vez.
        self.db.execute(
            select(Empresa.id).where(Empresa.id == self.ctx.empresa_id).with_for_update()
        )
        ClienteRepository(self.db, self.ctx.empresa_id).get_or_fail(data["cliente_id"])
        productos_repo = ProductoRepository(self.db, self.ctx.empresa_id)

        detalles = []
        subtotal = CERO
        for d in detalles_data:
            producto = productos_repo.get_or_fail(d["producto_id"])
            total_linea = (Decimal(d["cantidad"]) * Decimal(d["precio_unitario"])).quantize(
                Decimal("0.01")
            )
            subtotal += total_linea
            detalles.append(
                VentaDetalle(
                    producto_id=producto.id,
                    descripcion=d.get("descripcion") or producto.nombre,
                    cantidad=d["cantidad"],
                    precio_unitario=d["precio_unitario"],
                    total=total_linea,
                )
            )
            if descontar_inventario:
                stock = productos_repo.stock_de(producto.id)
                if stock < Decimal(d["cantidad"]):
                    raise BusinessError(
                        f"Stock insuficiente de '{producto.nombre}': disponible {stock}"
                    )

        descuento = Decimal(data.get("descuento") or CERO)
        if descuento > subtotal:
            raise BusinessError("El descuento no puede superar el subtotal")
        total = (subtotal - descuento).quantize(Decimal("0.01"))

        # El flete del despacho: sale de los kilos que van, no del total en plata.
        # Si vinieron tramos mandan ellos; si no, el flete de un solo valor se
        # traduce a un tramo para que en la base todo se guarde igual.
        kilos_despachados = sum((Decimal(d["cantidad"]) for d in detalles_data), CERO)
        if tramos_data is None:
            tramos_data = self._legado_a_tramos(gasto_concepto, gasto_por_kilo)
        tramos = self._tramos_desde_datos(tramos_data, kilos_despachados)
        concepto_flete, por_kilo_flete, monto_flete = _resumen_del_flete(tramos)

        venta = Venta(
            **data,
            empresa_id=self.ctx.empresa_id,
            numero=self.repo.siguiente_numero(),
            subtotal=subtotal,
            total=total,
            pagado=CERO,
            gasto_concepto=concepto_flete,
            gasto_por_kilo=por_kilo_flete,
            gasto_monto=monto_flete,
            # Una venta sin saldo (p.ej. descuento del 100%) nace PAGADA para no
            # quedar atrapada como pendiente en la cartera sin poder cerrarse.
            estado=ESTADO_PAGADA if total <= CERO else ESTADO_PENDIENTE,
            created_by=self.ctx.user_id,
            updated_by=self.ctx.user_id,
        )
        venta.detalles = detalles
        venta.tramos_flete = tramos
        self.db.add(venta)
        self.db.flush()

        if descontar_inventario:
            for detalle in venta.detalles:
                self.db.add(
                    MovimientoInventario(
                        empresa_id=self.ctx.empresa_id,
                        producto_id=detalle.producto_id,
                        fecha=venta.fecha,
                        tipo="salida",
                        cantidad=detalle.cantidad,
                        costo_unitario=detalle.precio_unitario,
                        referencia=f"venta #{venta.numero}",
                        created_by=self.ctx.user_id,
                    )
                )
            self.db.flush()

        self._audit("crear", venta.id, None, serialize_entity(venta))
        return venta

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Venta:
        venta = self.repo.get_or_fail(entity_id)
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("No se puede editar una venta anulada")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        antes = serialize_entity(venta)
        detalles_data = data.pop("detalles", None)

        # Cambiar productos o descuento afecta importes: solo se permite si la
        # venta aún no tiene pagos (si los tuviera, hay que anular y rehacer).
        afecta_importes = detalles_data is not None or "descuento" in data
        if afecta_importes and venta.pagado > CERO:
            raise BusinessError(
                "No se puede cambiar los productos o el descuento de una venta con pagos; "
                "anúlela y créela de nuevo"
            )

        if "cliente_id" in data:
            ClienteRepository(self.db, self.ctx.empresa_id).get_or_fail(data["cliente_id"])

        if detalles_data is not None:
            productos_repo = ProductoRepository(self.db, self.ctx.empresa_id)
            # Reintegra el inventario de las líneas actuales (entrada) para dejar el
            # stock como estaba antes de la venta y poder validar/descontar las nuevas.
            for detalle in venta.detalles:
                self.db.add(
                    MovimientoInventario(
                        empresa_id=self.ctx.empresa_id,
                        producto_id=detalle.producto_id,
                        fecha=date.today(),
                        tipo="entrada",
                        cantidad=detalle.cantidad,
                        costo_unitario=detalle.precio_unitario,
                        referencia=f"edición venta #{venta.numero}",
                        created_by=self.ctx.user_id,
                    )
                )
            self.db.flush()

            nuevos = []
            subtotal = CERO
            for d in detalles_data:
                producto = productos_repo.get_or_fail(d["producto_id"])
                total_linea = (Decimal(d["cantidad"]) * Decimal(d["precio_unitario"])).quantize(
                    Decimal("0.01")
                )
                subtotal += total_linea
                nuevos.append(
                    VentaDetalle(
                        producto_id=producto.id,
                        descripcion=d.get("descripcion") or producto.nombre,
                        cantidad=d["cantidad"],
                        precio_unitario=d["precio_unitario"],
                        total=total_linea,
                    )
                )
                stock = productos_repo.stock_de(producto.id)
                if stock < Decimal(d["cantidad"]):
                    raise BusinessError(
                        f"Stock insuficiente de '{producto.nombre}': disponible {stock}"
                    )
            venta.detalles = nuevos
            venta.subtotal = subtotal
            self.db.flush()
            for detalle in venta.detalles:
                self.db.add(
                    MovimientoInventario(
                        empresa_id=self.ctx.empresa_id,
                        producto_id=detalle.producto_id,
                        fecha=venta.fecha,
                        tipo="salida",
                        cantidad=detalle.cantidad,
                        costo_unitario=detalle.precio_unitario,
                        referencia=f"venta #{venta.numero}",
                        created_by=self.ctx.user_id,
                    )
                )

        if "descuento" in data:
            venta.descuento = Decimal(data["descuento"])

        # El flete se rehace si cambiaron los tramos O si cambiaron los kilos
        # despachados: si solo se miraran los tramos, cambiar los renglones dejaría
        # el monto viejo y el kilo puesto en destino saldría mal.
        kilos_despachados = sum((Decimal(d.cantidad) for d in venta.detalles), CERO)
        legado = "gasto_concepto" in data or "gasto_por_kilo" in data
        if "tramos" in data:
            self._aplicar_flete(
                venta, self._tramos_desde_datos(data["tramos"] or [], kilos_despachados)
            )
        elif legado:
            # Alguien mandó el flete a la vieja usanza (un concepto y un valor por
            # kilo). Se acepta y se guarda como UN tramo, pero solo si el despacho
            # no tiene ya varios: aplastar tres tramos en uno borraría en silencio
            # a los conductores y con ellos lo que se les debe. Antes que eso, se
            # dice qué hacer.
            if len(venta.tramos_flete) > 1:
                raise BusinessError(
                    "Este despacho tiene el flete partido en varios tramos: mándelos "
                    "en 'tramos' para editarlo. Si manda un solo valor por kilo se "
                    "perderían los conductores y lo que se les debe"
                )
            actual = venta.tramos_flete[0] if venta.tramos_flete else None
            concepto = (
                data["gasto_concepto"]
                if "gasto_concepto" in data
                else (actual.destino if actual else None)
            )
            por_kilo = (
                Decimal(data["gasto_por_kilo"] or CERO)
                if "gasto_por_kilo" in data
                else Decimal(actual.valor_por_kilo if actual else CERO)
            )
            nuevos = self._legado_a_tramos(concepto, por_kilo)
            # El conductor que ya tuviera el tramo se conserva: el formulario viejo
            # no lo manda, y perderlo por editar el valor del flete le borraría al
            # señor un viaje que sí hizo.
            if nuevos and actual is not None and actual.conductor:
                nuevos[0]["conductor"] = actual.conductor
                nuevos[0]["origen"] = actual.origen
            self._aplicar_flete(venta, self._tramos_desde_datos(nuevos, kilos_despachados))
        elif detalles_data is not None:
            self._recalcular_flete_por_kilos(venta, kilos_despachados)

        if afecta_importes:
            if venta.descuento > venta.subtotal:
                raise BusinessError("El descuento no puede superar el subtotal")
            venta.total = (venta.subtotal - venta.descuento).quantize(Decimal("0.01"))
            # Sin pagos (validado arriba): pendiente, o pagada si el total quedó en 0.
            venta.estado = ESTADO_PAGADA if venta.total <= CERO else ESTADO_PENDIENTE

        for campo in ("tipo", "cliente_id", "fecha", "observaciones"):
            if campo in data:
                setattr(venta, campo, data[campo])

        venta.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", venta.id, antes, serialize_entity(venta))
        return venta

    def anular(self, entity_id: uuid.UUID) -> Venta:
        venta = self.repo.get_or_fail(entity_id)
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("La venta ya está anulada")
        if venta.pagado > CERO:
            raise BusinessError("No se puede anular una venta con pagos registrados")
        antes = venta.estado
        venta.estado = ESTADO_ANULADA
        # Reintegrar el inventario descontado
        for detalle in venta.detalles:
            self.db.add(
                MovimientoInventario(
                    empresa_id=self.ctx.empresa_id,
                    producto_id=detalle.producto_id,
                    fecha=date.today(),
                    tipo="entrada",
                    cantidad=detalle.cantidad,
                    costo_unitario=detalle.precio_unitario,
                    referencia=f"anulación venta #{venta.numero}",
                    created_by=self.ctx.user_id,
                )
            )
        self.db.flush()
        self._audit("editar", venta.id, {"estado": antes}, {"estado": ESTADO_ANULADA})
        return venta

    def listar_filtrado(
        self,
        params: PageParams,
        *,
        cliente_id: uuid.UUID | None = None,
        tipo: str | None = None,
        estado: str | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> tuple[list[Venta], int]:
        extra = []
        if desde:
            extra.append(Venta.fecha >= desde)
        if hasta:
            extra.append(Venta.fecha <= hasta)
        return self.repo.list_paginated(
            params, estado=estado, filters={"cliente_id": cliente_id, "tipo": tipo}, extra_criteria=extra
        )

    def cartera(self) -> list[CarteraCliente]:
        return [
            CarteraCliente(
                cliente_id=fila.cliente_id,
                cliente_nombre=fila.nombre,
                ventas_pendientes=fila.ventas_pendientes,
                total_facturado=fila.total_facturado or CERO,
                total_pagado=fila.total_pagado or CERO,
                saldo=fila.saldo or CERO,
            )
            for fila in self.repo.cartera_por_cliente()
        ]


def _suma(valor: Any) -> Decimal:
    """Una suma traída de la base, como Decimal de dos decimales.

    Pasa por str porque SQLite devuelve float en algunos SUM y `Decimal(float)`
    arrastra la basura binaria del float ("40000.000000000007"). Con plata de un
    cliente real eso termina en un peso de diferencia en pantalla.
    """
    return Decimal(str(valor or 0)).quantize(DOS_DECIMALES)


class ConductorService(BaseService[PagoConductor]):
    """Cuánto se le debe a cada conductor de despachos, y lo que se le paga.

    NO ES UN MÓDULO APARTE ni una pantalla de administración de conductores: el
    conductor es texto libre dentro del tramo del flete. Esto es solo la cuenta
    que sale de esos tramos, y vive bajo `ventas` (mismo permiso, misma
    pantalla) porque el dato nace ahí y tenerlo en dos sitios sería tener dos
    sitios donde buscar lo mismo.

    LA DEUDA NO SE GUARDA EN NINGUNA COLUMNA: se calcula cada vez como la suma de
    sus tramos menos la suma de sus pagos. Ver el docstring de PagoConductor.
    """

    repository_cls = PagoConductorRepository
    modulo = "ventas"

    # ------------------------------------------------------------------ cuentas
    def _acumulado_por_conductor(self) -> dict[str, Decimal]:
        """Lo que se le ha acumulado a cada conductor DESDE SIEMPRE.

        Sin filtro de fechas a propósito: es lo que se le debe de verdad. Se
        agrupa por `conductor_clave`, que se calculó en Python al guardar, y no
        por lower(conductor) en SQL, porque el lower() de SQLite y el de Postgres
        no tratan igual los acentos y la agrupación cambiaría según la base.
        """
        filas = self.db.execute(
            select(
                VentaTramoFlete.conductor_clave,
                func.coalesce(func.sum(VentaTramoFlete.valor_total), 0),
            )
            .join(Venta, Venta.id == VentaTramoFlete.venta_id)
            .where(
                Venta.empresa_id == self.ctx.empresa_id,
                Venta.deleted_at.is_(None),
                Venta.estado != ESTADO_ANULADA,
                VentaTramoFlete.deleted_at.is_(None),
                VentaTramoFlete.conductor_clave.is_not(None),
            )
            .group_by(VentaTramoFlete.conductor_clave)
        ).all()
        return {clave: _suma(total) for clave, total in filas}

    def _nombres_por_clave(self) -> dict[str, str]:
        """clave → nombre tal como está escrito, mirando TODA la historia.

        Hace falta para el conductor al que se le debe de antes pero que no movió
        nada en el período que se está mirando: sin esto salía en la pantalla con
        la clave en minúsculas ("jose lavado") en vez de con su nombre.
        """
        nombres: dict[str, str] = {}
        for consulta in (
            select(VentaTramoFlete.conductor_clave, VentaTramoFlete.conductor)
            .join(Venta, Venta.id == VentaTramoFlete.venta_id)
            .where(
                Venta.empresa_id == self.ctx.empresa_id,
                Venta.deleted_at.is_(None),
                VentaTramoFlete.deleted_at.is_(None),
                VentaTramoFlete.conductor_clave.is_not(None),
            )
            .distinct(),
            select(PagoConductor.conductor_clave, PagoConductor.conductor)
            .where(
                PagoConductor.empresa_id == self.ctx.empresa_id,
                PagoConductor.deleted_at.is_(None),
            )
            .distinct(),
        ):
            for clave, nombre in self.db.execute(consulta).all():
                if clave and nombre:
                    nombres.setdefault(clave, nombre)
        return nombres

    def _pagado_por_conductor(self) -> dict[str, Decimal]:
        """Lo que se le ha pagado a cada conductor desde siempre."""
        filas = self.db.execute(
            select(
                PagoConductor.conductor_clave,
                func.coalesce(func.sum(PagoConductor.valor), 0),
            )
            .where(
                PagoConductor.empresa_id == self.ctx.empresa_id,
                PagoConductor.deleted_at.is_(None),
            )
            .group_by(PagoConductor.conductor_clave)
        ).all()
        return {clave: _suma(total) for clave, total in filas}

    def _saldo_de(self, clave: str) -> Decimal:
        """Lo que se le debe HOY a ese conductor: acumulado − pagado."""
        return self._acumulado_por_conductor().get(clave, CERO) - self._pagado_por_conductor().get(
            clave, CERO
        )

    # -------------------------------------------------------------------- vista
    def panel(self, desde: date | None, hasta: date | None) -> ConductoresPanel:
        """La pantalla completa: por conductor, lo del período y lo que se le debe.

        DOS CIFRAS DISTINTAS Y A PROPÓSITO:

        - `acumulado_periodo` / `pagado_periodo` son del rango que se está
          mirando, y suman EXACTO el detalle que se muestra debajo de cada uno.
        - `saldo` es de siempre. Acotar la deuda al período haría que mover el
          filtro cambiara lo que se le debe a una persona, y el dueño usa
          justamente esa cifra para pagar.

        Sale también quien no despachó nada en el período pero quedó debiéndosele
        de antes: si no, bastaría con mover las fechas para que una deuda real
        desapareciera de la pantalla.
        """
        tramos_periodo = self.repo_ventas.tramos_de_conductores(desde=desde, hasta=hasta)
        pagos_periodo = self.repo.pagos_de_conductores(desde=desde, hasta=hasta)
        acumulado_total = self._acumulado_por_conductor()
        pagado_total = self._pagado_por_conductor()

        nombres: dict[str, str] = {}
        detalle: dict[str, list[ConductorTramoRead]] = {}
        acumulado_periodo: dict[str, Decimal] = {}
        for fila in tramos_periodo:
            (
                clave, conductor, venta_id, numero, fecha, cliente,
                origen, destino, por_kilo, valor, kilos,
            ) = fila
            nombres.setdefault(clave, conductor)
            detalle.setdefault(clave, []).append(
                ConductorTramoRead(
                    venta_id=venta_id, venta_numero=numero, fecha=fecha, cliente=cliente,
                    origen=origen, destino=destino,
                    kilos=Decimal(str(kilos or 0)),
                    valor_por_kilo=Decimal(str(por_kilo or 0)),
                    valor=Decimal(str(valor or 0)),
                )
            )
            acumulado_periodo[clave] = acumulado_periodo.get(clave, CERO) + _suma(valor)

        pagos_detalle: dict[str, list[PagoConductorRead]] = {}
        pagado_periodo: dict[str, Decimal] = {}
        for pago in pagos_periodo:
            nombres.setdefault(pago.conductor_clave, pago.conductor)
            pagos_detalle.setdefault(pago.conductor_clave, []).append(
                PagoConductorRead(
                    id=pago.id, conductor=pago.conductor, fecha=pago.fecha,
                    valor=Decimal(pago.valor), observaciones=pago.observaciones,
                )
            )
            pagado_periodo[pago.conductor_clave] = (
                pagado_periodo.get(pago.conductor_clave, CERO) + _suma(pago.valor)
            )

        # Quien tiene saldo pendiente sale aunque no haya movido nada en el rango:
        # si no, bastaría con mover las fechas para que una deuda real
        # desapareciera de la pantalla. El nombre se busca en toda la historia,
        # porque el del período no existe (no despachó ni cobró en ese rango).
        pendientes = {
            clave
            for clave in set(acumulado_total) | set(pagado_total)
            if acumulado_total.get(clave, CERO) - pagado_total.get(clave, CERO) != CERO
        }
        if pendientes - set(nombres):
            de_siempre = self._nombres_por_clave()
            for clave in pendientes:
                nombres.setdefault(clave, de_siempre.get(clave, clave))

        conductores = [
            ConductorResumen(
                conductor=nombres[clave],
                acumulado_periodo=acumulado_periodo.get(clave, CERO),
                pagado_periodo=pagado_periodo.get(clave, CERO),
                total_acumulado=acumulado_total.get(clave, CERO),
                total_pagado=pagado_total.get(clave, CERO),
                saldo=acumulado_total.get(clave, CERO) - pagado_total.get(clave, CERO),
                tramos=detalle.get(clave, []),
                pagos=pagos_detalle.get(clave, []),
            )
            for clave in nombres
        ]
        # Primero al que más se le debe: es a quien hay que pagarle.
        conductores.sort(key=lambda c: (-c.saldo, c.conductor))

        # Los totales son la SUMA DE LAS FILAS que se muestran, sin recortar ni
        # acotar en cero. Si a alguien se le pagó de más su saldo sale negativo y
        # baja el total: se ve la fila y se entiende por qué. Acotarlo dejaría un
        # total que no cuadra con el desglose, y el dueño lo suma a mano.
        return ConductoresPanel(
            desde=desde,
            hasta=hasta,
            conductores=conductores,
            total_acumulado_periodo=sum((c.acumulado_periodo for c in conductores), CERO),
            total_pagado_periodo=sum((c.pagado_periodo for c in conductores), CERO),
            total_saldo=sum((c.saldo for c in conductores), CERO),
        )

    @property
    def repo_ventas(self) -> VentaRepository:
        return VentaRepository(self.db, self.ctx.empresa_id)

    def sugerencias(self) -> list[str]:
        """Nombres de conductor ya usados, para el autocompletado del formulario."""
        return unir_nombres(
            self.repo_ventas.nombres_conductores(), self.repo.nombres_conductores()
        )

    # -------------------------------------------------------------------- pagos
    def registrar_pago(self, payload: Any) -> PagoConductor:
        """Le paga a un conductor, sin pasarse de lo que se le debe.

        NO HAY UNA FILA DE "CONDUCTOR" QUE BLOQUEAR (el conductor es texto libre
        dentro del tramo), así que se bloquea la fila de la EMPRESA, que es el
        mismo candado con el que se serializa la numeración de las ventas. Con él
        puesto se vuelve a sumar la deuda contra la base y recién ahí se valida:
        sin eso, dos pagos a la vez al mismo señor leerían el mismo saldo, los dos
        pasarían y se le terminaría pagando el doble de lo que se le debía.
        """
        data = payload.model_dump()
        self.db.execute(
            select(Empresa.id).where(Empresa.id == self.ctx.empresa_id).with_for_update()
        )

        crudo = data["conductor"].strip()
        conductor = canonizar_nombre(crudo, self.sugerencias())
        clave = clave_de_tercero(conductor)
        saldo = self._saldo_de(clave)
        valor = Decimal(data["valor"])
        if saldo <= CERO:
            raise BusinessError(
                f"A {conductor} no se le debe nada en este momento"
                if saldo == CERO
                else f"A {conductor} ya se le pagó de más ({pesos(-saldo)} de sobra)"
            )
        if valor > saldo:
            # Las cifras van por pesos(): este mensaje lo lee el dueño en el
            # celular y un Decimal crudo ("150000.00") no se lee como plata
            # colombiana.
            raise BusinessError(
                f"El pago ({pesos(valor)}) supera lo que se le debe a {conductor} "
                f"({pesos(saldo)})"
            )

        pago = PagoConductor(
            empresa_id=self.ctx.empresa_id,
            conductor=conductor,
            conductor_clave=clave,
            fecha=data["fecha"],
            valor=valor,
            observaciones=data.get("observaciones"),
            created_by=self.ctx.user_id,
            updated_by=self.ctx.user_id,
        )
        self.db.add(pago)
        self.db.flush()
        self._audit("crear", pago.id, None, serialize_entity(pago))
        return pago

    def listar_pagos(
        self, params: PageParams, *, conductor: str | None = None
    ) -> tuple[list[PagoConductor], int]:
        """Historial de pagos. Si viene el nombre, se filtra por su CLAVE, para
        que traiga los del mismo señor aunque se hubiera escrito de otra forma."""
        extra = []
        if conductor:
            extra.append(PagoConductor.conductor_clave == clave_de_tercero(conductor))
        return self.repo.list_paginated(params, extra_criteria=extra)


class PagoService(BaseService[Pago]):
    repository_cls = PagoRepository
    modulo = "ventas"

    def crear(self, payload: Any) -> Pago:
        data = payload.model_dump(exclude_unset=True)
        # Bloquea la fila de la venta para evitar sobrepagos en pagos concurrentes.
        venta = self.db.scalars(
            select(Venta)
            .where(
                Venta.id == data["venta_id"],
                Venta.empresa_id == self.ctx.empresa_id,
                Venta.deleted_at.is_(None),
            )
            # Sin el join del cliente (lazy="joined"): Postgres no admite FOR UPDATE
            # sobre el lado exterior de un LEFT JOIN. Aquí solo se bloquea la venta.
            .options(lazyload(Venta.cliente))
            .with_for_update()
        ).first()
        if venta is None:
            raise NotFoundError("Venta no encontrada")
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("No se pueden registrar pagos sobre una venta anulada")
        if venta.estado == ESTADO_PAGADA:
            raise BusinessError("La venta ya está totalmente pagada")
        valor = Decimal(data["valor"])
        if valor > venta.saldo:
            # Las cifras van por pesos(): este mensaje lo lee el dueño en el celular
            # y un Decimal crudo ("150000.00") no se lee como plata colombiana.
            raise BusinessError(
                f"El pago ({pesos(valor)}) supera el saldo pendiente ({pesos(venta.saldo)})"
            )

        pago = super().crear(data)
        venta.pagado += valor
        venta.estado = ESTADO_PAGADA if venta.pagado >= venta.total else ESTADO_PARCIAL
        self.db.flush()
        return pago

    def validar_eliminar(self, obj: Pago) -> None:
        raise BusinessError("Los pagos no se eliminan; anule la venta o registre un ajuste contable")
