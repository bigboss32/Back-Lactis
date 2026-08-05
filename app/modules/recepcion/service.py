import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select

from app.common.service import BaseService
from app.core.exceptions import BusinessError, ConflictError
from app.core.pagination import PageParams
from app.modules.liquidaciones.models import (
    ESTADO_APROBADA,
    ESTADO_BORRADOR,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    TIPO_PROVEEDOR,
    Liquidacion,
)
from app.modules.liquidaciones.repository import LiquidacionRepository
from app.modules.proveedores.models import Proveedor
from app.modules.proveedores.repository import ProveedorRepository
from app.modules.proveedores.service import exigir_proveedor_activo
from app.modules.recepcion.models import RecepcionLeche
from app.modules.recepcion.repository import RecepcionRepository
from app.modules.recepcion.schemas import (
    CeldaGrilla,
    FilaGrilla,
    GrillaQuincena,
    ResumenDia,
    ResumenPeriodo,
)
from app.modules.rutas.repository import RutaRepository
from app.modules.transportadores.repository import TransportadorRepository
from app.modules.transportadores.tarifas import tarifa_por_litro

CERO = Decimal("0")
CENTAVOS = Decimal("0.01")


def _centavos(valor: Decimal) -> Decimal:
    """Redondea a centavos con el medio centavo PARA ARRIBA, como lo hace una persona.

    Hace falta porque los litros llevan dos decimales y la tarifa del
    transportador TAMBIÉN puede llevarlos (el dueño tiene un transportador a
    $242,76 por litro): 227,55 L × $242,76 da 55.239,978, o sea tres decimales
    que la columna Numeric(14,2) no guarda.

    Sin redondear aquí pasaban dos cosas, y las dos con plata:
      - la sesión no expira los objetos al hacer commit (expire_on_commit=False),
        así que al guardar se devolvía el 55.239,978 de memoria mientras en la
        base quedaba 55.239,98: la pantalla mostraba una cifra y al recargar
        salía otra;
      - la liquidación del transportador suma lo que está GUARDADO, así que el
        desglose por día no cuadraba contra el total que el dueño revisa a mano.

    Se usa ROUND_HALF_UP y no el ROUND_HALF_EVEN que Python trae por defecto
    porque así es como redondea Postgres al meter el valor en la columna: de esta
    forma lo que se devuelve y lo que queda guardado son el mismo número.

    Con las tarifas de pesos enteros que hay hoy esto NO mueve ni un peso: litros
    (2 decimales) por una tarifa entera da 2 decimales, y redondear a 2 decimales
    algo que ya tiene 2 decimales lo deja idéntico.
    """
    return valor.quantize(CENTAVOS, rounding=ROUND_HALF_UP)

# De más trabada a menos. Un día puede estar en DOS liquidaciones a la vez (la
# leche al proveedor y el flete al transportador): la que manda para el candado
# es la más trabada de las dos, porque basta con que UNA ya se haya pagado para
# que ese día no se pueda tocar.
#
# 'parcial' va arriba, junto a 'pagada': una liquidación a medio pagar TRABA el
# día igual que una pagada del todo. Si ya salió plata de la caja contra esas
# cifras, cambiar los litros deja el abono descuadrado.
_ORDEN_DE_CANDADO = (ESTADO_PAGADA, ESTADO_PARCIAL, ESTADO_APROBADA, ESTADO_BORRADOR)

# Los dos estados con los que un día queda trabado en la grilla y en el guardia.
_ESTADOS_CON_PAGO = (ESTADO_PAGADA, ESTADO_PARCIAL)


def _estado_que_manda(estados: list[str]) -> str | None:
    for estado in _ORDEN_DE_CANDADO:
        if estado in estados:
            return estado
    return None


# --------------------------------------------------------- el candado POR CAMPO
# Un día vive en DOS liquidaciones independientes y de dos personas distintas: la
# leche al proveedor (`liquidacion_id`) y el flete al transportador
# (`liquidacion_transporte_id`). Antes bastaba con que UNA de las dos ya hubiera
# movido plata para trabar LA FILA ENTERA, y eso dejó al dueño sin salida en un
# caso muy concreto que reportó así: "no se deja editar, y lo que necesito es
# solo poder editar el transportador, porque el transportador no se ha
# liquidado". Le habían pagado la leche del 29/07 a Patricia Laguna (44 L a
# $2.050 = $90.200), pero se equivocaron al anotar quién la recogió y el flete de
# ese día todavía no se había liquidado: no había ninguna razón para trabarlo.
#
# La regla correcta es por CAMPO, según a quién le mueve la plata cada uno.

# Campos que le mueven la cuenta al PROVEEDOR (la liquidación de la leche).
_CAMPOS_DE_LA_LECHE = frozenset(
    {
        "cantidad_litros",  # litros × precio es el valor bruto de la quincena
        "precio_litro",
        "bonificaciones",  # entran en el VALOR TOTAL del comprobante
        "descuentos",
        "fecha",  # decide en qué quincena cae el día y en qué renglón
        "proveedor_id",  # le cambiaría de dueño una leche ya pagada
        "estado",  # apagar un día ya pagado es como borrarlo
    }
)

# Campos que le mueven la cuenta al TRANSPORTADOR (la liquidación del flete).
_CAMPOS_DEL_FLETE = frozenset(
    {
        "cantidad_litros",  # el flete se cobra POR LITRO recogido
        "transportador_id",  # decide de quién es ese flete
        # LA RUTA ENTRÓ EN LA PLATA y por eso está acá desde que un transportador
        # puede cobrar distinto en cada ruta ("cada ruta puede tener un valor
        # diferente de litro por leche"): la ruta del día es la que escoge la
        # tarifa, así que cambiarla recalcula el flete guardado. Antes era un dato
        # de clasificación y no trababa nada; dejarla suelta ahora dejaría mover la
        # cifra de un flete YA PAGADO, y el comprobante que el transportador tiene
        # en la mano dejaría de cuadrar contra sus recepciones.
        "ruta_id",
        "fecha",
        "estado",
    }
)

# ------------------------------------- CUÁNDO SE VUELVE A CALCULAR LA CIFRA DEL FLETE
# `valor_transporte` es una cifra DERIVADA: litros × tarifa_por_litro(transportador,
# ruta). No es un campo que el usuario mande, así que el candado de arriba —que mira
# campos— no la cubre, y hay que decidir aparte cuándo se vuelve a derivar.
#
# LA REGLA SON TRES CASOS, y la frontera es SI ESE DÍA YA ESTÁ EN UN COMPROBANTE DE
# FLETE. Está implementada en `_hay_que_rederivar_el_flete` y usada por `actualizar`:
#
#   1. POR ESE FLETE YA SALIÓ PLATA (pagado o con abonos): NO se toca. Esa cifra es la
#      que el transportador tiene en la mano y contra la que se le entregó el dinero.
#   2. EL DÍA TODAVÍA NO ESTÁ EN NINGÚN COMPROBANTE DE FLETE: se re-deriva siempre, con
#      la tarifa de hoy. Es la salida del dueño cuando teclea mal una tarifa —teclea
#      $100 por litro en vez de $242,76, lo corrige en la pantalla del transportador,
#      abre el día, guarda, y la foto queda buena—: la tarifa no es un campo de la
#      recepción, así que este guardado es el único momento en que le puede llegar a un
#      día suelto. No hay comprobante que re-precificar, así que no hay nada que dañar.
#   3. EL DÍA YA ESTÁ EN UN COMPROBANTE que todavía no ha movido plata: se re-deriva
#      SOLO si el guardado cambió algo que de verdad le mueve la cuenta al flete (los
#      de `_CAMPOS_DEL_FLETE`: litros, ruta, transportador, fecha, estado). Si el
#      guardado fue de un campo libre —una observación, el precio de la leche—, la foto
#      se queda como está.
#
# EL CASO 3 ES EL ARREGLO, y estas son sus cifras: dos días del 02/06 en Nápoles
# (44,23 + 82,48 = 126,71 L) con el comprobante APROBADO en $30.760,12 a $242,76.
# Alguien sube la tarifa a $300 —un cambio legítimo, para la quincena siguiente— y
# alguien más escribe "el tarro venía mal tapado" en uno de los días. Con la regla
# vieja ("se re-deriva siempre") esa nota re-precificaba el comprobante a $38.013,00 y
# le quitaba el visto bueno: $7.252,88 de cambio por un campo que no tiene nada que ver
# con la plata. Re-precificar un comprobante ya emitido es lo que hace el botón
# RECALCULAR, que el dueño oprime a propósito; guardar una observación no.
#
# Y no le quita al dueño la salida del caso 2: si el día ya está en un comprobante, la
# tarifa corregida le llega por el botón Recalcular de ese comprobante, que es donde
# tiene que estar la decisión de re-precificar.

# Consecuencia de las dos listas de arriba, y es la parte que hay que leer con
# cuidado: LOS LITROS, LA FECHA y EL ESTADO están en las DOS, así que quedan
# trabados si CUALQUIERA de las dos liquidaciones ya movió plata. El precio por
# litro (y las bonificaciones y los descuentos) solo los traba la leche, y el
# transportador y la ruta solo los traba el flete: lo del transportador es justo
# el caso del dueño.
#
# Lo que NO traba nadie: la sucursal y las observaciones. Son el único dato de
# clasificación y la anotación libre; no entran en ninguna liquidación. Así, con
# las DOS liquidaciones pagadas todavía se puede dejar escrito qué pasó ese día.

# El proveedor NO se cambia nunca, con liquidación o sin ella: `RecepcionUpdate`
# ni siquiera trae el campo, así que el PUT no lo puede recibir. Se queda arriba
# en `_CAMPOS_DE_LA_LECHE` para que el guardia lo cubra si mañana alguien lo
# agrega al schema, pero NUNCA se anuncia como corregible: ofrecerle al usuario
# "sí se puede corregir el proveedor" sería mandarlo a intentar algo imposible.
_NUNCA_EDITABLES = frozenset({"proveedor_id"})

# Orden y nombre con que los campos salen en los mensajes, para que el aviso se
# lea como lo diría una persona y no como una lista de columnas de la base.
_ETIQUETAS: tuple[tuple[str, str], ...] = (
    ("fecha", "la fecha"),
    ("proveedor_id", "el proveedor"),
    ("cantidad_litros", "los litros"),
    ("precio_litro", "el precio por litro"),
    ("bonificaciones", "las bonificaciones"),
    ("descuentos", "los descuentos"),
    ("transportador_id", "el transportador"),
    ("ruta_id", "la ruta"),
    ("sucursal_id", "la sucursal"),
    ("observaciones", "las observaciones"),
    # 'el estado del día' y no 'el estado' a secas: en esta pantalla "estado" ya
    # significa el de la liquidación, y el dueño leería que no puede cambiar algo
    # que nunca se cambia a mano. Este es el activo/inactivo del registro.
    ("estado", "el estado del día"),
)
_ORDEN_DE_CAMPOS: tuple[str, ...] = tuple(campo for campo, _ in _ETIQUETAS)
_NOMBRE_DE_CAMPO: dict[str, str] = dict(_ETIQUETAS)


def _ya_salio_plata(liquidacion: Liquidacion) -> bool:
    """Si contra esta liquidación ya se le entregó plata al tercero.

    Es "tiene algún pago", no "está en pagada": con un solo abono hecho, cambiar
    las cifras deja ese abono contra un total que ya no existe. Se mira además el
    estado 'pagada' porque hay un camino que la marca pagada SIN registrar pago
    —cuando los anticipos se comieron EXACTO todo el saldo— y ahí tampoco hay nada
    que corregir: esa plata salió como anticipo, en la mano.
    """
    return liquidacion.tiene_pagos or liquidacion.estado == ESTADO_PAGADA


def _traba_el_dia(liquidacion: Liquidacion) -> bool:
    """Si esta liquidación deja SUS CIFRAS congeladas, y por lo tanto traba el día.

    SON DOS RAZONES DISTINTAS y conviene no confundirlas, porque el mensaje que el
    usuario lee es distinto en cada una:

      1. YA SALIÓ PLATA contra ella (un abono, el pago total, o el anticipo que la
         saldó). Cambiar los litros deja esa entrega contra un total que ya no existe.
      2. LO QUE ESTA QUINCENA QUEDÓ DEBIENDO YA SE LE COBRÓ EN OTRA liquidación. Acá no
         salió un peso, pero sus cifras están igual de congeladas: su
         `le_queda_debiendo` está restado en un SEGUNDO comprobante —que puede estar
         pagado y en la mano del proveedor—, así que corregirle un litro descuadraría
         los dos papeles de una sola vez.

    La segunda es nueva, y es la contraparte de haber quitado el 'pagada' falso: una
    liquidación de saldo negativo se queda ahora en 'aprobada', o sea que sus días
    siguen corregibles —que es lo que pidió el dueño: un día no puede quedar trabado si
    no salió plata de verdad— HASTA el momento en que su deuda se cobra en la quincena
    siguiente. Ese es el instante en que la quincena queda cerrada de verdad, y ahí sí
    se traba. El porqué completo está en la nota "MARCAR PAGADA UNA LIQUIDACIÓN QUE
    NADIE PAGÓ", en app/modules/liquidaciones/service.py.
    """
    return _ya_salio_plata(liquidacion) or liquidacion.deuda_ya_cobrada


def _nombre_del_tercero(liquidacion: Liquidacion) -> str | None:
    """A quién se le pagó: el proveedor de la leche o el transportador del flete."""
    if liquidacion.tipo == TIPO_PROVEEDOR:
        return liquidacion.proveedor.nombre if liquidacion.proveedor else None
    return liquidacion.transportador.nombre if liquidacion.transportador else None


def _en_palabras(campos: list[str]) -> str:
    """'los litros, el precio por litro y la fecha' — con 'y', no con coma final."""
    nombres = [_NOMBRE_DE_CAMPO.get(campo, campo) for campo in campos]
    if not nombres:
        return ""
    if len(nombres) == 1:
        return nombres[0]
    return f"{', '.join(nombres[:-1])} y {nombres[-1]}"


def _de_quien(liquidacion: Liquidacion) -> str:
    """'la leche' o 'el flete': qué plata apartó esta liquidación."""
    return "la leche" if liquidacion.tipo == TIPO_PROVEEDOR else "el flete"


def _por_que_esta_trabada(liquidacion: Liquidacion) -> str:
    """'la leche de este día ya se le pagó a Patricia Laguna'."""
    que = _de_quien(liquidacion)
    nombre = _nombre_del_tercero(liquidacion)
    a_quien = f" a {nombre}" if nombre else ""
    # LA DEUDA YA COBRADA VA PRIMERO porque es la razón que el usuario no puede
    # adivinar: por este día no salió plata, así que "ya se le pagó" sería mentira y lo
    # mandaría a buscar un pago que no existe. Lo que necesita saber es en qué OTRA
    # liquidación se le cobró, que es la que tendría que anular para poder corregir.
    if liquidacion.deuda_ya_cobrada:
        otra = liquidacion.deuda_trasladada_a
        donde = f" en la del {otra.periodo_texto}" if otra is not None else " en otra"
        de_quien = f"{nombre} " if nombre else ""
        return (
            f"lo que {de_quien}quedó debiendo en la quincena de {que} de este día ya "
            f"se le cobró{donde}"
        )
    # Se distingue el pago total del abono porque para el usuario son dos
    # situaciones distintas: del pagado no hay nada que hacer por dentro; del
    # abono sí, se puede borrar el pago, corregir el día y volver a abonar.
    if liquidacion.estado == ESTADO_PAGADA:
        return f"{que} de este día ya se le pagó{a_quien}"
    return f"{que} de este día ya se le abonó{a_quien}"


class CandadoRecepcion:
    """Qué se puede y qué no se puede tocar de un día, y por qué.

    Se calcula en UN SOLO SITIO para que las tres cosas digan exactamente lo
    mismo: el guardia del backend (que es el que de verdad manda), el aviso del
    diálogo de la recepción y el tooltip de la celda en la grilla. Antes el
    tooltip decía "Liquidada — no editable" y con la regla nueva estaría
    mintiendo la mayoría de las veces.
    """

    __slots__ = ("liquidaciones", "leche", "flete", "bloqueados")

    def __init__(
        self,
        liquidaciones: list[Liquidacion],
        leche: Liquidacion | None,
        flete: Liquidacion | None,
        bloqueados: dict[str, Liquidacion],
    ) -> None:
        self.liquidaciones = liquidaciones
        self.leche = leche
        self.flete = flete
        # campo -> la liquidación pagada que lo traba (sirve para el mensaje)
        self.bloqueados = bloqueados

    @property
    def campos_bloqueados(self) -> list[str]:
        return [campo for campo in _ORDEN_DE_CAMPOS if campo in self.bloqueados]

    @property
    def campos_editables(self) -> list[str]:
        return [
            campo
            for campo in _ORDEN_DE_CAMPOS
            if campo not in self.bloqueados and campo not in _NUNCA_EDITABLES
        ]

    # Estas dos viajan a la pantalla (`RecepcionRead`, `CeldaGrilla`) y son las que
    # apagan las celdas de la grilla. Van por `_traba_el_dia` y NO por
    # `_ya_salio_plata` para que la grilla apague exactamente lo que el guardia va a
    # rebotar: una quincena cuya deuda ya se cobró en otra liquidación también tiene
    # las cifras congeladas, aunque no haya salido un peso. Se conservan los nombres
    # —la pantalla ya los usa— y el aviso que va al lado explica el motivo real.
    @property
    def leche_pagada(self) -> bool:
        return self.leche is not None and _traba_el_dia(self.leche)

    @property
    def flete_pagado(self) -> bool:
        return self.flete is not None and _traba_el_dia(self.flete)

    def _cola_del_transportador(self) -> str:
        """El remate del aviso cuando lo que se puede corregir es el transportador.

        Es el caso del dueño y merece la explicación completa: no basta decirle
        que "sí se puede", hay que decirle qué va a pasar con la liquidación del
        flete si ya existía —porque el día se le sale y esa liquidación se
        recalcula sin él—.
        """
        if "transportador_id" in self.bloqueados:
            return ""
        if self.flete is None:
            return ", porque su flete todavía no se ha liquidado"
        return (
            f", porque su flete todavía no se ha pagado (está en {self.flete.estado}); "
            "al cambiarlo, el día se suelta de esa liquidación y ella se recalcula sin él"
        )

    def aviso(self) -> str | None:
        """El texto que la pantalla muestra, en el español del dueño.

        Sale del backend y no del navegador a propósito: así la explicación y el
        guardia no se pueden desincronizar. Si mañana cambia la regla, cambia
        aquí y la pantalla dice la verdad nueva sola.
        """
        if not self.bloqueados:
            return None
        razones = [
            _por_que_esta_trabada(liq)
            for liq in (self.leche, self.flete)
            if liq is not None and _traba_el_dia(liq)
        ]
        motivo = "; ".join(razones) or "por este día ya salió plata"
        motivo = motivo[0].upper() + motivo[1:]
        no_se_puede = _en_palabras(self.campos_bloqueados)
        editables = self.campos_editables
        if not editables:
            return f"{motivo}: no se puede cambiar {no_se_puede}, y no queda nada por corregir."
        return (
            f"{motivo}: no se puede cambiar {no_se_puede}. "
            f"Sí se puede corregir {_en_palabras(editables)}{self._cola_del_transportador()}."
        )


class RecepcionService(BaseService[RecepcionLeche]):
    repository_cls = RecepcionRepository
    modulo = "recepcion"

    # ------------------------------------------- liquidaciones de una recepción
    def _liquidaciones_de(self, recepcion: RecepcionLeche) -> list[Liquidacion]:
        """Las liquidaciones que hoy tienen apartado este día: leche y/o flete.

        Va por el repositorio para no saltarse el filtro por empresa ni el de
        borrados: de esto depende si se deja o no tocar plata de un tenant.
        """
        ids = {
            liq_id
            for liq_id in (recepcion.liquidacion_id, recepcion.liquidacion_transporte_id)
            if liq_id is not None
        }
        if not ids:
            return []
        stmt = (
            LiquidacionRepository(self.db, self.ctx.empresa_id)
            .base_query()
            .where(Liquidacion.id.in_(ids))
        )
        return list(self.db.scalars(stmt).all())

    def _candado_de(
        self, recepcion: RecepcionLeche, por_id: dict[uuid.UUID, Liquidacion]
    ) -> CandadoRecepcion:
        """Arma el candado de un día con las liquidaciones YA CARGADAS.

        Recibe el mapa en vez de consultarlo porque las lecturas lo resuelven de
        un solo golpe para toda la quincena: si cada fila fuera a la base, la
        grilla de una quesera con cientos de días haría cientos de consultas.
        """
        leche = por_id.get(recepcion.liquidacion_id) if recepcion.liquidacion_id else None
        flete = (
            por_id.get(recepcion.liquidacion_transporte_id)
            if recepcion.liquidacion_transporte_id
            else None
        )
        bloqueados: dict[str, Liquidacion] = {}
        # El flete va primero y la leche después para que, cuando las DOS estén
        # pagadas, de los campos compartidos (litros, fecha, estado) se culpe a la
        # leche: es la cifra grande, la que el dueño reconoce en el comprobante.
        for liquidacion, campos in ((flete, _CAMPOS_DEL_FLETE), (leche, _CAMPOS_DE_LA_LECHE)):
            if liquidacion is not None and _traba_el_dia(liquidacion):
                for campo in campos:
                    bloqueados[campo] = liquidacion
        return CandadoRecepcion(
            liquidaciones=[liq for liq in (leche, flete) if liq is not None],
            leche=leche,
            flete=flete,
            bloqueados=bloqueados,
        )

    def _candado(self, recepcion: RecepcionLeche) -> CandadoRecepcion:
        """El candado de un día, consultando sus liquidaciones. Para escribir."""
        liquidaciones = self._liquidaciones_de(recepcion)
        return self._candado_de(recepcion, {liq.id: liq for liq in liquidaciones})

    def _cambios_reales(self, actual: RecepcionLeche, data: dict[str, Any]) -> set[str]:
        """Los campos que el PUT de verdad quiere CAMBIAR, no los que vienen.

        Hace falta porque el diálogo manda TODO el formulario en cada guardado
        (incluidos los campos que dejó apagados). Si el guardia mirara la simple
        presencia del campo, corregirle el transportador a un día con la leche ya
        pagada rebotaría por culpa de unos litros que llegaron idénticos a los que
        ya estaban guardados: el usuario no cambió nada ahí.

        Los Decimal se comparan por valor y no por texto, porque '44' y '44.00'
        son la misma leche y con `!=` de cadenas saldrían distintos.
        """
        cambios: set[str] = set()
        for campo, valor in data.items():
            if not hasattr(actual, campo):
                continue
            guardado = getattr(actual, campo)
            if valor is None and guardado is None:
                continue
            if valor is None or guardado is None:
                cambios.add(campo)
                continue
            if isinstance(valor, Decimal) or isinstance(guardado, Decimal):
                if Decimal(valor) != Decimal(guardado):
                    cambios.add(campo)
            elif valor != guardado:
                cambios.add(campo)
        return cambios

    def _exigir_campos_libres(
        self, candado: CandadoRecepcion, cambios: set[str], verbo: str = "cambiar"
    ) -> None:
        """El candado POR CAMPO: rebota solo lo que de verdad movería plata pagada.

        Es el guardia que reemplazó al de toda la fila. Está EN EL BACKEND y no en
        la pantalla a propósito: quien conozca la dirección del endpoint entra
        igual, y aquí es donde se decide si una liquidación pagada se descuadra.
        """
        choques = [
            campo
            for campo in _ORDEN_DE_CAMPOS
            if campo in cambios and campo in candado.bloqueados
        ]
        if not choques:
            return
        # Se nombra el PRIMER campo en conflicto (el orden de `_ETIQUETAS` va de
        # lo más grave a lo más leve) y enseguida qué sí se puede corregir: es la
        # pregunta que el dueño tenía sin responder cuando le salía "no se deja
        # editar" y no sabía qué era lo que no se dejaba.
        liquidacion = candado.bloqueados[choques[0]]
        que_toco = _en_palabras(choques)
        editables = candado.campos_editables
        salida = f" Sí se puede corregir {_en_palabras(editables)}." if editables else ""
        de_quien = _de_quien(liquidacion)
        # LA DEUDA YA COBRADA TIENE SU PROPIO MENSAJE, y va antes que los dos de pago:
        # por este día no salió plata, así que "ya se pagó" mandaría al usuario a buscar
        # un pago que no existe. Lo que le sirve es cuál liquidación se cobró esa deuda
        # —esa es la que tendría que anular para poder corregir el día— y por eso el
        # mensaje la nombra con su período.
        if liquidacion.deuda_ya_cobrada:
            otra = liquidacion.deuda_trasladada_a
            donde = (
                f"la liquidación del {otra.periodo_texto}"
                if otra is not None
                else "otra liquidación"
            )
            # Y EL ORDEN EN QUE HAY QUE VOLVER A GENERARLAS, con las fechas concretas:
            # este es EL mensaje que le manda al dueño a anular las dos quincenas, y
            # regenerarlas empezando por la nueva le saca plata de más ($480.000 por
            # $430.000 de leche, medido). La redacción vive en el modelo
            # (`orden_para_volver_a_generar`) para que este mensaje, el de anular una
            # liquidación y el de borrar un día digan exactamente lo mismo.
            orden = liquidacion.orden_para_volver_a_generar
            raise BusinessError(
                f"No se puede {verbo} {que_toco} de este día: lo que el tercero quedó "
                f"debiendo en esta quincena de {de_quien} ya se le cobró en {donde}, así "
                f"que cambiar la cifra descuadraría los dos comprobantes.{salida} Si de "
                f"verdad hay que corregirlo, anule primero esa liquidación. {orden}".rstrip()
            )
        if liquidacion.estado == ESTADO_PAGADA:
            raise BusinessError(
                f"No se puede {verbo} {que_toco} de este día: {de_quien} ya se pagó en una "
                f"liquidación.{salida} Si la cifra está mala, corríjala por fuera del sistema "
                "o registre el ajuste en la quincena siguiente"
            )
        raise BusinessError(
            f"No se puede {verbo} {que_toco} de este día: {de_quien} ya tiene un pago "
            f"registrado en una liquidación.{salida} Elimine primero ese pago si de verdad "
            "hay que corregir la cifra, o registre el ajuste en la quincena siguiente"
        )

    def _hay_que_rederivar_el_flete(
        self, candado: CandadoRecepcion, cambios: set[str]
    ) -> bool:
        """Si este guardado tiene que volver a calcular la FOTO del flete del día.

        Los tres casos están explicados arriba, en el bloque "CUÁNDO SE VUELVE A
        CALCULAR LA CIFRA DEL FLETE". En una línea: nunca sobre un flete pagado;
        siempre sobre un día que no está en ningún comprobante; y sobre un día que sí
        está, solo cuando el guardado cambió algo que le mueve la cuenta al flete.

        `cambios` son los cambios REALES (ver `_cambios_reales`), no los campos que el
        formulario mandó: el diálogo manda todo el formulario en cada guardado y mirar
        la presencia haría que reenviarlo sin tocar nada re-precificara el comprobante,
        que es justo lo que esto viene a evitar.
        """
        if candado.flete_pagado:
            return False
        if candado.flete is None:
            return True
        return bool(cambios & _CAMPOS_DEL_FLETE)

    def _exigir_no_pagada(self, recepcion: RecepcionLeche, verbo: str) -> list[Liquidacion]:
        """Para BORRAR el día: lo traba cualquiera de las dos liquidaciones pagadas.

        Aquí sí es todo o nada, y con razón: borrar un día no cambia un campo, lo
        saca de las DOS liquidaciones a la vez. Si a alguno de los dos terceros ya
        se le pagó, su comprobante se quedaría con un renglón sin recepción detrás
        y su total dejaría de ser la suma de los días —el descuadre silencioso que
        el dueño encuentra cuadrando a mano contra el cuaderno—.

        "Ya salió plata" es TENER ALGÚN PAGO, no estar en 'pagada': si al proveedor
        se le abonó la mitad y después se le borra un día, ese abono queda contra
        un total que ya no existe. Ver `_ya_salio_plata`.

        Y TAMBIÉN LO TRABA UNA DEUDA YA COBRADA (ver `_traba_el_dia`): borrarle un día a
        la quincena que quedó debiendo le cambiaría el `le_queda_debiendo`, que ya está
        restado en el comprobante de la quincena siguiente.

        Devuelve las liquidaciones tocadas, para recuadrarlas después de escribir.
        """
        candado = self._candado(recepcion)
        con_pago = [liq for liq in candado.liquidaciones if _traba_el_dia(liq)]
        if con_pago:
            liq = con_pago[0]
            de_quien = "la leche" if liq.tipo == TIPO_PROVEEDOR else "el flete"
            if liq.deuda_ya_cobrada:
                otra = liq.deuda_trasladada_a
                donde = (
                    f"la liquidación del {otra.periodo_texto}"
                    if otra is not None
                    else "otra liquidación"
                )
                # Con el orden para regenerarlas, igual que el mensaje de corregir un
                # campo: la misma redacción del modelo para la misma regla.
                raise BusinessError(
                    f"No se puede {verbo} este día: lo que el tercero quedó debiendo en "
                    f"esta quincena de {de_quien} ya se le cobró en {donde}. Si de verdad "
                    "hay que corregirlo, anule primero esa liquidación. "
                    f"{liq.orden_para_volver_a_generar}".rstrip()
                )
            # Dos mensajes porque son dos situaciones distintas para el usuario:
            # de la pagada no hay nada que hacer por dentro; del abono sí, se
            # puede borrar el pago, corregir el día y volver a abonar.
            if liq.estado == ESTADO_PAGADA:
                raise BusinessError(
                    f"No se puede {verbo} este día: {de_quien} ya se pagó en una "
                    "liquidación. Si la cifra está mala, corríjala por fuera del sistema "
                    "o registre el ajuste en la quincena siguiente"
                )
            raise BusinessError(
                f"No se puede {verbo} este día: {de_quien} ya tiene un pago "
                "registrado en una liquidación. Elimine primero ese pago si de verdad "
                "hay que corregir la cifra, o registre el ajuste en la quincena siguiente"
            )
        return candado.liquidaciones

    def _marcas_a_soltar(
        self,
        actual: RecepcionLeche,
        liquidaciones: list[Liquidacion],
        data: dict[str, Any],
        nueva_fecha: date,
    ) -> dict[str, None]:
        """Cuándo un día deja de pertenecer a la liquidación que lo tenía apartado.

        Editar un día ya no está prohibido, pero hay dos cambios que lo sacan de
        su liquidación y que —si no se atienden— le pagarían a quien no era o
        meterían leche de otra quincena en un comprobante ya emitido:

        · Cambia el TRANSPORTADOR: el flete de ese día es de otra persona. Se
          suelta de la liquidación de flete vieja (que se recuadra sin él) y
          queda disponible para liquidárselo al que sí recogió.
        · La FECHA se sale del período de la liquidación: esa leche pertenece a
          otra quincena. Se suelta para que entre en la liquidación que le toca;
          si se quedara, el comprobante de junio traería un día de julio.

        Un cambio de fecha DENTRO del mismo período no suelta nada: es la
        corrección de todos los días y la liquidación simplemente se recuadra.
        """
        por_id = {liq.id: liq for liq in liquidaciones}
        nuevo_transportador = data.get("transportador_id", actual.transportador_id)
        soltar: dict[str, None] = {}

        def fuera_de_periodo(liq_id: uuid.UUID | None) -> bool:
            liq = por_id.get(liq_id) if liq_id else None
            return liq is not None and not (liq.periodo_inicio <= nueva_fecha <= liq.periodo_fin)

        if fuera_de_periodo(actual.liquidacion_id):
            soltar["liquidacion_id"] = None
        if actual.liquidacion_transporte_id is not None and (
            nuevo_transportador != actual.transportador_id
            or fuera_de_periodo(actual.liquidacion_transporte_id)
        ):
            soltar["liquidacion_transporte_id"] = None
        return soltar

    def _recuadrar(self, liquidaciones: list[Liquidacion]) -> None:
        """Vuelve a cuadrar las liquidaciones cuyo día se acaba de tocar.

        Se salta las que YA MOVIERON PLATA, y esto es nuevo: desde que el candado
        es por campo, un día puede tener la leche pagada y el flete en borrador, y
        al corregirle el transportador llegan aquí las dos. `recuadrar` rebota con
        un error si le pasan una pagada (y hace bien: recalcularla descuadraría el
        pago), así que la corrección legítima moriría por culpa de la liquidación
        que ni se tocó. Se recuadra solo la que sí cambió.

        DE LA PAGADA SE ARREGLA UNA SOLA COSA, y es a propósito: la columna
        informativa `valor_transporte` de la liquidación del PROVEEDOR, que es la
        suma del flete de sus días. No entra en el VALOR TOTAL (bruto +
        bonificaciones - descuentos) ni sale en el PDF, pero sí en la pantalla, y
        cuando se le corrige la ruta a un día —permitido: la ruta solo la traba el
        flete— la foto del flete cambia y esa cifra se queda diciendo otra cosa que
        sus propias recepciones. Se pone al día SIN tocar nada de lo que se le pagó.
        Lo que sigue sin hacerse es recalcular la plata de una liquidación pagada,
        que es peor remedio que la enfermedad.

        Se importa aquí adentro y no arriba a propósito: el servicio de
        liquidaciones ya usa el repositorio de recepciones, y con el import
        arriba las dos hojas quedarían amarradas en tiempo de carga.
        """
        if not liquidaciones:
            return
        from app.modules.liquidaciones.service import LiquidacionService

        servicio = LiquidacionService(self.db, self.ctx)
        # EL FLETE PRIMERO, por lo mismo que `LiquidacionService.generar` genera el
        # flete antes que la leche: el recuadre del flete REPARTE centavos entre las
        # fotos, y la liquidación del proveedor guarda la suma de esas fotos en su
        # columna informativa. Al revés, la de la leche sumaría las fotos de antes del
        # reparto y quedaría un centavo corrida hasta el siguiente recuadre.
        en_orden = sorted(liquidaciones, key=lambda liq: liq.tipo == TIPO_PROVEEDOR)
        for liquidacion in en_orden:
            # `_traba_el_dia` y no `_ya_salio_plata`: la que dejó una deuda ya cobrada
            # tampoco se puede recuadrar (rebota), y llegar acá con ella tumbaría el
            # guardado de un campo libre —una observación— con un 422 que el usuario no
            # entendería. Se le pone al día su columna informativa de flete y nada más,
            # que es lo mismo que se hace con las pagadas.
            if _traba_el_dia(liquidacion):
                servicio.refrescar_transporte_informativo(liquidacion.id)
            else:
                servicio.recuadrar(liquidacion.id)

    def _marcar_estado_liquidacion(self, recepciones: list[RecepcionLeche]) -> None:
        """Le cuelga a cada recepción su candado, ya resuelto campo por campo.

        Nada de esto son columnas: se resuelve de un solo golpe para toda la lista
        (una consulta, no una por fila) y se expone en `RecepcionRead` y en
        `CeldaGrilla` para que la pantalla apague EXACTAMENTE los campos que el
        backend va a rebotar, y no la fila entera.

        `liquidacion_estado` se conserva tal como estaba —el estado de la
        liquidación que manda, la más trabada de las dos— porque la lista y la
        grilla lo usan para avisar que al tocar el día se mueve una liquidación ya
        generada. Lo que se agrega al lado es el detalle que faltaba: cuál de las
        dos plata está pagada y, de ahí, qué campos quedan trabados.
        """
        ids = {
            liq_id
            for r in recepciones
            for liq_id in (r.liquidacion_id, r.liquidacion_transporte_id)
            if liq_id is not None
        }
        por_id: dict[uuid.UUID, Liquidacion] = {}
        if ids:
            stmt = (
                LiquidacionRepository(self.db, self.ctx.empresa_id)
                .base_query()
                .where(Liquidacion.id.in_(ids))
            )
            por_id = {liq.id: liq for liq in self.db.scalars(stmt).all()}
        for r in recepciones:
            propios = [
                por_id[liq_id].estado
                for liq_id in (r.liquidacion_id, r.liquidacion_transporte_id)
                if liq_id in por_id
            ]
            r.liquidacion_estado = _estado_que_manda(propios)
            candado = self._candado_de(r, por_id)
            r.liquidacion_estado_leche = candado.leche.estado if candado.leche else None
            r.liquidacion_estado_flete = candado.flete.estado if candado.flete else None
            r.leche_pagada = candado.leche_pagada
            r.flete_pagado = candado.flete_pagado
            r.campos_bloqueados = candado.campos_bloqueados
            r.campos_editables = candado.campos_editables
            r.candado_aviso = candado.aviso()

    def _transportador_de_la_tarifa(
        self, transportador_id: uuid.UUID, actual: RecepcionLeche | None
    ):
        """El transportador del que sale la tarifa, aguantando que lo hayan BORRADO.

        Normalmente sale del repositorio, que filtra por empresa y por borrados. Pero
        un transportador se puede retirar del sistema (borrado lógico) SIN que los
        días que él recogió se vayan con él: esos días existen, están liquidados y
        siguen siendo editables. Con `get_or_fail` pelado, anotarle una observación a
        uno de esos días respondía "Transportador no encontrado" (404) y el día
        quedaba congelado para siempre, sin que hubiera plata que proteger.

        Así que si el id NO se encuentra pero es EL MISMO que el día ya tenía
        guardado, se usa la fila que trae la recepción (la relación carga el
        transportador aunque esté borrado, igual que el nombre del proveedor retirado
        en los listados). Se exige que sea de esta empresa: es historia de este día,
        no una puerta para leerle la tarifa a otra quesera.

        Un id DISTINTO del guardado sí tiene que existir y estar vigente: asignarle un
        día a un transportador retirado es un dato nuevo equivocado, no historia.
        """
        repo = TransportadorRepository(self.db, self.ctx.empresa_id)
        vigente = repo.get(transportador_id)
        if vigente is not None:
            return vigente
        if (
            actual is not None
            and actual.transportador_id == transportador_id
            and actual.transportador is not None
            and actual.transportador.empresa_id == self.ctx.empresa_id
        ):
            return actual.transportador
        # Ni vigente ni el que el día ya tenía: que reviente con el mensaje de
        # siempre, que es el correcto para un id que no existe en esta empresa.
        return repo.get_or_fail(transportador_id)

    def _exigir_ruta_de_la_empresa(
        self, ruta_id: uuid.UUID, actual: RecepcionLeche | None
    ) -> None:
        """La ruta del día tiene que ser de ESTA quesera. Cierra una fuga medida.

        `recepciones_leche.ruta_id` no lo miraba nadie: solo la llave foránea, que en
        una base multiempresa por FILA no sabe de empresas. Reproducido de punta a
        punta: el admin de la quesera B crea la ruta "RutaSecretaDeB"; el admin de la A
        manda ese `ruta_id` en un POST /recepciones y responde 201; y de ahí en
        adelante EL NOMBRE DE LA RUTA AJENA SALE IMPRESO en el comprobante del
        transportador y en su PDF, que es un papel que se le entrega a una persona.
        Además la ruta decide la tarifa, así que un id ajeno también le mueve la plata
        del flete de ese día.

        Se cierra por el REPOSITORIO y no con un filtro escrito a mano, igual que en
        `TransportadorService._filas_de_rutas`: `RutaRepository(db, ctx.empresa_id).get`
        ya trae `empresa_id = <la del token>` y `deleted_at IS NULL`, así que una ruta
        de otra quesera —o una borrada— simplemente no aparece. Escribir el filtro otra
        vez acá sería una segunda copia de la regla de aislamiento, y la que se olvida
        de actualizar.

        LA EXCEPCIÓN, Y ES LA MISMA QUE ALLÁ: una ruta BORRADA se acepta si es LA QUE EL
        DÍA YA TENÍA GUARDADA y es de esta empresa. Sin eso el día quedaba imposible de
        editar: la lectura devuelve `ruta_id` —tiene que devolverlo, es historia y esa
        ruta decidió el flete que ya se cobró—, la pantalla hace lo que hace toda
        pantalla (leer, cambiar un campo, guardar lo leído) y el PUT rebotaría con "la
        ruta no existe" hasta por corregirle las observaciones. Una ruta ajena no pasa
        ni estando guardada: se exige `empresa_id`, y perpetuar la fuga no es
        conservar historia.

        No se valida la ruta que se HEREDA DEL PROVEEDOR (`proveedor.ruta_id`): esa no
        la escogió quien manda el POST, ya viene de una fila de esta empresa, y si
        alguien le borró la ruta al proveedor el día tiene que poder recibirse igual.
        """
        repo = RutaRepository(self.db, self.ctx.empresa_id)
        if repo.get(ruta_id) is not None:
            return
        if (
            actual is not None
            and actual.ruta_id == ruta_id
            and actual.ruta is not None
            and actual.ruta.empresa_id == self.ctx.empresa_id
        ):
            return
        # Que reviente con el mensaje de siempre para un id que no existe en esta
        # empresa: es el mismo 404 que da cualquier otra puerta del sistema.
        repo.get_or_fail(ruta_id)

    def _completar_y_calcular(
        self,
        data: dict[str, Any],
        actual: RecepcionLeche | None = None,
        *,
        recalcular_flete: bool = True,
    ) -> dict[str, Any]:
        """Completa precio/ruta desde el proveedor y calcula los valores monetarios.

        `recalcular_flete=False` deja la FOTO del flete (`valor_transporte`) tal como
        está guardada. Lo decide `actualizar` con la regla que está escrita allá: la
        cifra del flete se vuelve a derivar salvo que por ese flete ya haya salido
        plata. Al CREAR siempre se calcula, que es el momento en que la foto se toma.
        """
        proveedor_id = data.get("proveedor_id") or (actual.proveedor_id if actual else None)
        proveedor = ProveedorRepository(self.db, self.ctx.empresa_id).get_or_fail(proveedor_id)

        # Al proveedor inactivo no se le recibe leche nueva. Se revisa al crear y
        # también cuando una recepción existente se le quiere pasar a otro
        # proveedor que está inactivo.
        #
        # Lo que NO se bloquea es corregir una recepción que YA era de ese
        # proveedor: si se retiró a mitad de quincena, la última quincena todavía
        # hay que cuadrarla y liquidársela, y dejarla congelada obligaría a
        # reactivarlo solo para arreglarle un dato. Apartarlo es para que no
        # entre leche nueva, no para volverle la historia de solo lectura.
        if actual is None or proveedor_id != actual.proveedor_id:
            exigir_proveedor_activo(proveedor)

        if data.get("precio_litro") is None and actual is None:
            data["precio_litro"] = proveedor.precio_litro
        # La ruta que MANDÓ el usuario se valida contra esta empresa ANTES de tocar
        # nada. Va aquí arriba —antes de heredarla del proveedor y antes de derivar el
        # flete— porque las dos puertas que escriben una recepción (crear y actualizar)
        # pasan por esta función, y una ruta ajena mueve la plata del flete además de
        # salir impresa en el comprobante. Ver `_exigir_ruta_de_la_empresa`.
        #
        # `is not None` y no `in data`: en el PUT un `ruta_id: null` es "quítele la
        # ruta", que es legítimo y no hay nada que validar.
        if data.get("ruta_id") is not None:
            self._exigir_ruta_de_la_empresa(data["ruta_id"], actual)
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

        # Se redondea a centavos ACÁ y no se deja que lo haga la columna, para que
        # la cifra que se devuelve sea exactamente la que queda guardada. Ver
        # _centavos: es lo que hace que el desglose de la liquidación sume el total.
        data["valor_bruto"] = _centavos(litros * precio)
        if recalcular_flete:
            transportador_id = data.get("transportador_id") or (
                actual.transportador_id if actual else None
            )
            # LA RUTA DEL DÍA DECIDE LA TARIFA, ya no es solo una etiqueta: el mismo
            # transportador cobra $242,76 en Nápoles y $300 en Mira Valle, y el mismo
            # día puede haber hecho las dos. Se usa la ruta EFECTIVA —la que el PUT
            # trae si la trae (aunque venga en None, que es "quítele la ruta"), si no
            # la que ya estaba guardada—, porque arriba `data["ruta_id"]` solo se
            # completa desde el proveedor cuando el día es nuevo.
            ruta_id = data["ruta_id"] if "ruta_id" in data else (actual.ruta_id if actual else None)
            tarifa = CERO
            if transportador_id:
                transportador = self._transportador_de_la_tarifa(transportador_id, actual)
                # UNA SOLA función para esta cuenta, compartida con liquidaciones: la
                # tarifa de la ruta si la tiene, si no la general del transportador.
                tarifa = tarifa_por_litro(transportador, ruta_id)
            data["valor_transporte"] = _centavos(litros * tarifa)
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
        recepcion = super().crear(data)
        self._marcar_estado_liquidacion([recepcion])
        return recepcion

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> RecepcionLeche:
        actual = self.repo.get_or_fail(entity_id)
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        # El candado es POR CAMPO y no por fila: lo que traba un campo es que la
        # plata a la que ESE campo le mueve la cuenta ya haya salido. Por eso se
        # revisa contra los cambios de verdad y no contra la fila entera; es lo que
        # deja corregirle el transportador a un día cuya leche ya se pagó, que es
        # lo que el dueño necesitaba. Ver `_candado` y `_exigir_campos_libres`.
        candado = self._candado(actual)
        cambios = self._cambios_reales(actual, data)
        self._exigir_campos_libres(candado, cambios)
        # Las liquidaciones se apuntan ANTES de escribir, porque después de
        # guardar hay que volver a cuadrarlas con la cifra nueva.
        liquidaciones = candado.liquidaciones
        nueva_fecha = data.get("fecha", actual.fecha)
        if self.repo.existe_registro_dia(actual.proveedor_id, nueva_fecha, exclude_id=entity_id):
            raise ConflictError("Ya existe una recepción de este proveedor en esa fecha")
        # ------------------------------------------- CUÁNDO SE VUELVE A CALCULAR EL FLETE
        # La cifra del flete (`valor_transporte`) es la plata que se le va a cobrar al
        # transportador por ese día y es DERIVADA (litros × tarifa de hoy). Cuándo se
        # vuelve a derivar lo decide `_hay_que_rederivar_el_flete`, y la regla completa
        # —con las cifras del caso que cerró— está en el bloque "CUÁNDO SE VUELVE A
        # CALCULAR LA CIFRA DEL FLETE" del encabezado.
        #
        # Lo que hay que tener presente al leer esta línea: un día que YA ESTÁ en un
        # comprobante de flete solo se re-deriva si el guardado le movió algo al flete.
        # Guardar una observación no re-precifica un comprobante ya emitido; para eso
        # está el botón Recalcular, que el dueño oprime a propósito.
        recalcular_flete = self._hay_que_rederivar_el_flete(candado, cambios)
        data = self._completar_y_calcular(data, actual, recalcular_flete=recalcular_flete)
        data.update(self._marcas_a_soltar(actual, liquidaciones, data, nueva_fecha))

        recepcion = super().actualizar(entity_id, data)
        # Se baja el cambio antes de recuadrar: la sesión no hace autoflush y el
        # recálculo vuelve a leer las recepciones desde la base.
        self.db.flush()
        self._recuadrar(liquidaciones)
        self._marcar_estado_liquidacion([recepcion])
        return recepcion

    def validar_eliminar(self, obj: RecepcionLeche) -> None:
        self._exigir_no_pagada(obj, "eliminar")

    def eliminar(self, entity_id: uuid.UUID) -> None:
        """Borra el día y deja cuadradas las liquidaciones que lo tenían.

        Sin el recuadre, la liquidación se quedaría con el renglón de un día que
        ya no existe y su total dejaría de ser la suma de sus recepciones: el
        descuadre silencioso que el dueño detecta cuadrando a mano.
        """
        recepcion = self.repo.get_or_fail(entity_id)
        liquidaciones = self._exigir_no_pagada(recepcion, "eliminar")
        super().eliminar(entity_id)
        self.db.flush()
        self._recuadrar(liquidaciones)

    # ---------------------------------------------------------------- lecturas
    def obtener(self, entity_id: uuid.UUID) -> RecepcionLeche:
        recepcion = super().obtener(entity_id)
        self._marcar_estado_liquidacion([recepcion])
        return recepcion

    def listar(self, params: PageParams, **kwargs: Any) -> tuple[list[RecepcionLeche], int]:
        items, total = super().listar(params, **kwargs)
        self._marcar_estado_liquidacion(items)
        return items, total

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
        items, total = self.repo.list_paginated(params, filters=filters, extra_criteria=extra)
        self._marcar_estado_liquidacion(items)
        return items, total

    def grilla_quincena(
        self,
        desde: date,
        hasta: date,
        *,
        search: str | None = None,
        ruta_id: uuid.UUID | None = None,
        transportador_id: uuid.UUID | None = None,
    ) -> GrillaQuincena:
        """Grilla proveedores × días como la hoja 'LITROS Y TRANSPORTE' del Excel.

        Incluye todos los proveedores activos (aunque no tengan recepciones)
        para que la grilla sirva también como superficie de registro diario.
        Se puede filtrar por nombre de proveedor (search), por ruta (ruta_id) y
        por transportador (transportador_id).

        Los tres filtros SE COMBINAN (se aplican todos a la vez, no se pisan),
        pero no trabajan al mismo nivel, y la diferencia importa:

        - search y ruta_id son filtros DE FILA: escogen proveedores (el de la
          ruta es la ruta del PROVEEDOR, no la de cada recepción) y de los
          elegidos se muestran todos sus días.
        - transportador_id es un filtro DE CELDA: el transportador se guarda en
          CADA recepción (columna transportador_id de recepciones_leche), así
          que un mismo proveedor puede haber sido recogido por Stella el lunes y
          por Efraín el martes. Filtrar por Stella deja solo los días de Stella,
          y los totales de la fila, del día y del pie se recalculan sobre esas
          celdas: lo que suma la pantalla es exactamente lo que se ve en ella.
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

        # El filtro de transportador se aplica EN LA CONSULTA (base_query ya trae
        # empresa_id y deleted_at IS NULL): la quincena de una quesera con muchos
        # proveedores son cientos de filas y recortarlas después —o peor, en el
        # navegador— sería un filtro de mentiras.
        consulta = self.repo.base_query().where(
            RecepcionLeche.fecha >= desde,
            RecepcionLeche.fecha <= hasta,
            RecepcionLeche.estado == "activo",
        )
        if transportador_id is not None:
            consulta = consulta.where(RecepcionLeche.transportador_id == transportador_id)
        recepciones = list(self.db.scalars(consulta).all())
        # De un solo golpe para toda la quincena: cada celda necesita saber si su
        # día está trabado (liquidación pagada) o solo apartado en una liquidación
        # sin pagar, que se puede editar pero avisando.
        self._marcar_estado_liquidacion(recepciones)

        activos = ProveedorRepository(self.db, self.ctx.empresa_id).all(estado="activo")
        activos_ids = {p.id for p in activos}
        # Sin filtro de transportador la grilla es TAMBIÉN la libreta de registro
        # diario, por eso arranca con todos los proveedores activos aunque no
        # tengan nada anotado: las celdas vacías son donde se anota.
        #
        # Con filtro de transportador, no. La pregunta ahí es "¿qué recogió
        # Stella esta quincena?", y contestarla con treinta filas en blanco de
        # proveedores a los que Stella nunca les recogió no contesta nada:
        # esconde las pocas filas que sí importan. Quedan solo los proveedores
        # con leche recogida por él (y por eso tampoco se anota leche nueva con
        # el filtro puesto: para eso se quita y la grilla vuelve a estar completa).
        proveedores: dict = {} if transportador_id is not None else {p.id: p for p in activos}
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
                recepcion_id=r.id,
                litros=r.cantidad_litros,
                # "liquidada" es apenas "ya está dentro de una liquidación
                # generada" (la de la leche o la del flete): es una seña, no un
                # candado. El candado es "ya tiene pagos": pagada del todo o
                # parcial, porque en las dos ya salió plata contra ese día.
                liquidada=r.liquidacion_estado is not None,
                pagada=r.liquidacion_estado in _ESTADOS_CON_PAGO,
                liquidacion_estado=r.liquidacion_estado,
                con_transporte=r.transportador_id is not None,
                # Las dos platas por separado, que es lo que le faltaba a la
                # pantalla: con `pagada` sola, un día con el flete pagado y la
                # leche sin pagar se veía igual que uno intocable, y el tooltip
                # decía "Pagada — no editable" cuando sí se podía corregir casi
                # todo. Con estos dos, la celda dice la verdad y deja pasar el
                # clic a lo que sigue siendo editable.
                leche_pagada=r.leche_pagada,
                flete_pagado=r.flete_pagado,
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
            # Con `_centavos` y no con `.quantize(...)` a secas: sin decir el modo,
            # Python usa ROUND_HALF_EVEN y $2,505 saldría $2,50, mientras toda la
            # plata del proyecto sube el medio centavo ($2,51). Ver `_centavos`.
            precio_promedio=_centavos(valor_bruto / total_litros) if total_litros else CERO,
            dias=dias,
        )
