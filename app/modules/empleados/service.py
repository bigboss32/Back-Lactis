import re
import unicodedata
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.common.service import BaseService
from app.core.exceptions import BusinessError, ConflictError
from app.modules.empleados.models import Empleado, PagoEmpleado
from app.modules.empleados.repository import EmpleadoRepository, PagoEmpleadoRepository

CERO = Decimal("0")
CENTAVOS = Decimal("0.01")


def _centavos(valor: Any) -> Decimal:
    """Redondea plata a dos decimales con la REGLA DE LA CASA: 0,005 SUBE.

    La nómina era el único servicio de plata del proyecto que se había quedado con
    el `quantize` POR OMISIÓN de Python (ROUND_HALF_EVEN, el del banquero) mientras
    los demás —`recepcion/service.py::_centavos`, `liquidaciones/service.py::
    _centavos`, `transportadores/tarifas.py`— y los formateadores del PDF
    (`app/utils/export.py::_medio_arriba`) usan ROUND_HALF_UP. Con eso EL PAPEL NO
    CUADRABA, con las cifras del dueño: 12,5 jornales de $41.833,33 dan
    $522.916,625 exactos; el recibo imprimía "Subtotal devengado $522.916,63" y
    "TOTAL PAGADO $522.673,86", o sea que $522.916,63 - $242,76 le daba a mano
    $522.673,87 y el papel decía un peso menos. El dueño revisa ESA resta a mano.

    Es además como redondea Postgres al meter el valor en la columna Numeric(14,2),
    así que lo que se devuelve y lo que queda guardado son el mismo número.
    """
    return Decimal(valor).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def _nombre_archivo_empleado(nombre: str) -> str:
    """Nombre de archivo seguro para el recibo de nómina.

    Mismo saneamiento que `reventa/service.py::_nombre_archivo_cliente`, y por la
    misma razón: el nombre del empleado es texto libre y se iba CRUDO al header
    `Content-Disposition`. Con un apellido normal de la región ("Marín") la cabecera
    salía con un byte 0xED que ni siquiera el TestClient del proyecto puede leer
    —o sea que este endpoint no se podía probar con un nombre real—, y con una
    comilla o un salto de línea en el apellido sería una inyección de cabecera HTTP.
    Se quitan los acentos (para que "Marín" siga siendo legible como "Marin") y se
    borra todo lo que no sea alfanumérico, guion o guion bajo.
    """
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", nombre) if not unicodedata.combining(c)
    )
    limpio = re.sub(r"[^A-Za-z0-9_-]", "", "_".join(sin_acentos.split()))
    return limpio or "empleado"


class EmpleadoService(BaseService[Empleado]):
    repository_cls = EmpleadoRepository
    modulo = "empleados"

    def validar_crear(self, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(Empleado.documento == data["documento"]):
            raise ConflictError(f"Ya existe un empleado con documento {data['documento']}")

    def validar_actualizar(self, obj: Empleado, data: dict[str, Any]) -> None:
        if data.get("documento") and self.repo.exists_where(
            Empleado.documento == data["documento"], exclude_id=obj.id
        ):
            raise ConflictError(f"Ya existe un empleado con documento {data['documento']}")


class PagoEmpleadoService(BaseService[PagoEmpleado]):
    repository_cls = PagoEmpleadoRepository
    modulo = "empleados"

    def crear(self, payload: Any) -> PagoEmpleado:
        from app.modules.liquidaciones.repository import AnticipoRepository

        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        empleado = EmpleadoRepository(self.db, self.ctx.empresa_id).get_or_fail(data["empleado_id"])

        valor_dia = data.get("valor_dia")
        if valor_dia is None:
            valor_dia = empleado.valor_dia
        if not valor_dia or Decimal(valor_dia) <= CERO:
            raise BusinessError(
                "El empleado no tiene un valor por día. Indícalo en el pago o en la ficha del empleado."
            )

        # El jornal se redondea ANTES de multiplicar. La columna es Numeric(14,2):
        # si entra un valor por día con tres decimales, Postgres guarda el
        # redondeado pero la cuenta se haría con el crudo, y la fila quedaría
        # contradiciéndose sola —el recibo diría "12,5 × $41.833,34" y el total no
        # saldría de esa multiplicación—. Es el mismo criterio de
        # `app/common/schemas.py::a_dos_decimales`.
        valor_dia = _centavos(valor_dia)
        dias = Decimal(data["dias_trabajados"])
        bruto = _centavos(dias * valor_dia)

        # Descuenta los anticipos pendientes del empleado (los que quepan enteros
        # dentro del pago). Los que no quepan quedan para el siguiente pago.
        #
        # SE ORDENAN ACÁ, DEL MÁS VIEJO AL MÁS NUEVO, y es una decisión con plata:
        # `pendientes_empleado` no trae ORDER BY, así que el orden lo pone la base
        # —y no es el mismo en SQLite (pruebas) que en Postgres (producción)—. En
        # las liquidaciones eso da igual porque se aplican TODOS los pendientes;
        # acá no, porque hay una prueba de si CABE: con dos adelantos de los que
        # solo uno entra en la quincena, el orden decide cuál se descuenta y cuál
        # se le queda debiendo el empleado. Primero el más viejo, que es como lo
        # cuenta el dueño, y con la fecha empatada manda el que se registró antes
        # para que la respuesta no cambie de una corrida a otra.
        pendientes = AnticipoRepository(self.db, self.ctx.empresa_id).pendientes_empleado(
            data["empleado_id"], data["fecha"]
        )
        pendientes.sort(key=lambda a: (a.fecha, a.created_at))
        descontado = CERO
        aplicados = []
        for anticipo in pendientes:
            if descontado + anticipo.valor <= bruto:
                descontado += anticipo.valor
                aplicados.append(anticipo)

        data["valor_dia"] = valor_dia
        data["anticipos"] = descontado
        data["total"] = bruto - descontado
        pago = super().crear(data)
        for anticipo in aplicados:
            anticipo.pago_empleado_id = pago.id
            anticipo.updated_by = self.ctx.user_id
        if aplicados:
            self.db.flush()
        return pago

    # ------------------------------------------------ soltar lo que se apartó
    #
    # LOS CAMINOS QUE PUEDEN HACER DESAPARECER UN PAGO, uno por uno, porque de eso
    # depende que no se le descuente dos veces el mismo adelanto al empleado:
    #
    #  · BORRAR el pago (`DELETE /api/v1/nomina/{id}`) -> es el de abajo, y era el
    #    que estaba mal: heredaba el `eliminar` de `BaseService`, que solo marca
    #    `deleted_at`, y el anticipo se quedaba apuntando a un pago que las
    #    consultas ya no devuelven. PRESO: `pendientes_empleado` solo recoge los
    #    que tienen `pago_empleado_id` en nulo, así que ese adelanto no volvía a
    #    estar disponible NUNCA.
    #  · ANULAR: hoy NO EXISTE ese camino. Un pago de nómina no tiene estados (no
    #    hay borrador ni aprobado ni pagado, existe = ya se le pagó al empleado) y
    #    el router de nómina se arma a mano, sin PUT ni PATCH: `PUT
    #    /api/v1/nomina/{id}` responde 405. Está FIJADO POR UNA PRUEBA en
    #    tests/test_nomina_anticipos_no_se_quedan_presos.py, para que el día que
    #    alguien abra esa puerta la prueba falle y diga, con nombre y apellido, que
    #    por ahí también hay que soltar los anticipos.
    #  · BORRADO EN SUAVE: es el mismo de arriba. `soft_delete` es lo único que
    #    borra en este proyecto, y pasa por `eliminar`.
    #  · BORRAR EL EMPLEADO: no hace desaparecer el pago. `PagoEmpleadoRepository`
    #    filtra por `deleted_at` del PAGO, no del empleado, así que el pago se
    #    sigue listando y su recibo se sigue bajando (la relación carga al empleado
    #    aunque esté borrado, igual que en el resto del sistema). O sea que el
    #    anticipo sigue descontado en un pago que SÍ existe: no queda preso. Y si
    #    después se borra ese pago, el camino de abajo lo suelta igual.
    #  · REINICIAR LA EMPRESA: borra en duro `pagos_empleado` Y `anticipos`, y el
    #    orden lo resuelve `reversed(Base.metadata.sorted_tables)` (anticipos
    #    depende de pagos_empleado, así que sale primero). No queda nada colgando.
    def _anticipos_del_pago(self, pago_id: uuid.UUID) -> list[Any]:
        """Los anticipos que este pago tiene descontados.

        Va por `AnticipoRepository.base_query()` y no por un `select` suelto para
        no saltarse el filtro por empresa ni el de borrados: es la regla de la casa
        en TODA consulta, y acá de por medio hay plata de dos queseras distintas.
        """
        from app.modules.liquidaciones.models import Anticipo
        from app.modules.liquidaciones.repository import AnticipoRepository

        stmt = (
            AnticipoRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(Anticipo.pago_empleado_id == pago_id)
        )
        return list(self.db.scalars(stmt).all())

    def _soltar_anticipos(self, pago: PagoEmpleado, motivo: str) -> int:
        """Suelta los anticipos que este pago tenía descontados y devuelve cuántos.

        Es el mismo idioma de `liquidaciones/service.py::_soltar_lo_apartado` y por
        la misma razón exacta: un pago que ya no existe no le descuenta nada a
        nadie, así que lo que tenía apartado tiene que quedar libre.

        LA PLATA QUE SE PERDÍA, con las cifras del dueño: al empleado se le
        adelantan $242.760,00 el 3 de julio; se le paga la quincena de 12,5
        jornales a $41.833,33 ($522.916,63) descontándoselos, o sea que se le
        entregan $280.156,63; el pago estaba malo y se borra. Sin esto el adelanto
        quedaba marcado en un pago fantasma y salían las dos puntas del mismo hueco:
        si se rehacía el pago, salía por $522.916,63 —al empleado se le entregaron
        $242.760,00 que nadie le descontó nunca, plata de menos en la caja—; y si en
        vez de eso se le registraba OTRO adelanto por el mismo valor, el descuento
        se le hacía dos veces. Soltándolo, el pago rehecho lo vuelve a descontar y
        sale por los mismos $280.156,63: la cuenta cierra.

        NO SE PIERDE EL RASTRO: queda un renglón en la bitácora con cuáles adelantos
        se soltaron y por cuánta plata. Ese renglón se escribe CONTRA EL PAGO y no
        contra cada anticipo, a propósito: `BaseService._audit` pone en `entidad` el
        modelo de ESTE servicio (`PagoEmpleado`), así que auditar uno por uno dejaría
        filas que dicen "PagoEmpleado" con el id de un anticipo —una bitácora que
        miente, y la bitácora es lo que el dueño abre cuando una cifra no le cuadra—.
        Los anticipos quedan marcados con `updated_by`, igual que en
        `_soltar_lo_apartado`.
        """
        anticipos = self._anticipos_del_pago(pago.id)
        if not anticipos:
            return 0
        for anticipo in anticipos:
            anticipo.pago_empleado_id = None
            anticipo.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit(
            "editar",
            pago.id,
            {"anticipos": float(pago.anticipos), "anticipos_descontados": len(anticipos)},
            {
                "anticipos_soltados": [
                    {"id": str(a.id), "fecha": a.fecha.isoformat(), "valor": float(a.valor)}
                    for a in anticipos
                ],
                "total_soltado": float(sum(a.valor for a in anticipos)),
                "motivo": (
                    f"{motivo}: esa plata vuelve a quedar pendiente y se le "
                    "descontará en el próximo pago de nómina que se le registre "
                    "al empleado"
                ),
            },
        )
        return len(anticipos)

    def eliminar(self, entity_id: uuid.UUID) -> None:
        """Borrado en suave, soltando los anticipos que el pago tenía descontados.

        El orden importa: primero se sueltan y después se marca el borrado. Al revés
        `_anticipos_del_pago` seguiría encontrándolos igual (busca por
        `pago_empleado_id`, no por el estado del pago), pero así el `deleted_at` del
        pago y los anticipos sueltos quedan en el mismo flush y no hay un instante
        en que el pago esté borrado con el adelanto todavía preso.
        """
        pago = self.repo.get_or_fail(entity_id)
        # El guardia va ANTES de soltar nada, igual que en
        # `LiquidacionService.eliminar`: hoy `validar_eliminar` no rebota nada, pero
        # si mañana se le pone una regla al borrado —"no se borra un pago de un mes
        # ya cerrado", por ejemplo— el borrado no puede quedar a medio hacer, con
        # los adelantos sueltos y el pago todavía vivo.
        self.validar_eliminar(pago)
        self._soltar_anticipos(
            pago, "se borró el pago de nómina en el que este adelanto estaba descontado"
        )
        super().eliminar(entity_id)

    def generar_pdf(self, entity_id: uuid.UUID) -> tuple[bytes, str]:
        import uuid
        from datetime import datetime
        from app.modules.empresas.repository import EmpresaRepository
        from app.utils.export import build_recibo_empleado_pdf, jornales, pesos

        pago = self.repo.get_or_fail(entity_id)
        empresa = EmpresaRepository(self.db).get(self.ctx.empresa_id)
        nombre_empresa = empresa.nombre if empresa else "Quesera"
        nit = empresa.nit if empresa else None
        ubicacion = (
            ", ".join(p for p in [empresa.ciudad, empresa.departamento] if p) or None
            if empresa
            else None
        )

        empleado = pago.empleado
        empleado_nombre = f"{empleado.nombre} {empleado.apellido}".strip() if empleado else "Empleado"
        empleado_documento = empleado.documento if empleado else None
        empleado_cargo = empleado.cargo if empleado else None

        # El bruto del papel se vuelve a sacar de lo GUARDADO y con el mismo
        # redondeo con que se calculó el total (`_centavos`), para que la resta que
        # el dueño hace a mano sobre el recibo —subtotal menos anticipos— dé
        # exactamente el TOTAL PAGADO que está impreso abajo.
        bruto = _centavos(Decimal(pago.dias_trabajados) * Decimal(pago.valor_dia))

        # Anticipos descontados. Por el repositorio, que ya filtra empresa y
        # borrados: el `select` suelto que había acá se saltaba el `empresa_id`.
        anticipos_pago = self._anticipos_del_pago(pago.id)
        anticipos_rows = [
            [a.fecha.strftime("%d/%m/%Y"), pesos(a.valor), a.observaciones or "—"]
            for a in anticipos_pago
        ]

        folio = str(pago.id)[:8].upper()
        emitido = datetime.now().strftime("%d/%m/%Y %H:%M")

        pdf = build_recibo_empleado_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            folio=folio,
            emitido=emitido,
            empleado_nombre=empleado_nombre,
            empleado_documento=empleado_documento,
            empleado_cargo=empleado_cargo,
            fecha=pago.fecha.strftime("%d/%m/%Y"),
            periodo=pago.periodo,
            # Formateados como todo lo demás del papel: el recibo los imprimía
            # crudos de la base ('12.50') en una hoja donde la plata dice
            # '$771.662,50'. Ver `app/utils/export.py::jornales`.
            dias_trabajados=jornales(pago.dias_trabajados),
            valor_dia=pesos(pago.valor_dia),
            valor_bruto=pesos(bruto),
            anticipos_monto=pesos(pago.anticipos),
            total_pagado=pesos(pago.total),
            anticipos_rows=anticipos_rows,
            observaciones=pago.observaciones,
        )
        filename = (
            f"recibo_nomina_{_nombre_archivo_empleado(empleado_nombre)}"
            f"_{pago.fecha.isoformat()}.pdf"
        )
        return pdf, filename
