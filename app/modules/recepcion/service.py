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
from app.modules.transportadores.models import MODO_POR_LITRO, Transportador
from app.modules.transportadores.repository import TransportadorRepository
from app.modules.transportadores.tarifas import (
    Tarifa,
    reparto_entre_las_fotos,
    tarifa_de_transporte,
    valor_del_grupo,
)

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

# Los tres campos que MUEVEN el día de un grupo de flete a otro. No es lo mismo que
# `_CAMPOS_DEL_FLETE`: corregirle los litros le cambia la cuenta al día pero lo deja en
# el mismo (transportador, día, ruta); estos tres lo sacan de un viaje y lo meten en
# otro. Es la única puerta por la que una recepción puede volver a un viaje fijo que ya
# está cobrado, y por eso son los únicos que disparan `_volver_al_viaje_ya_cobrado`.
_CAMPOS_QUE_MUEVEN_EL_DIA = frozenset({"transportador_id", "fecha", "ruta_id"})

# ------------------------------------- CUÁNDO SE VUELVE A CALCULAR LA CIFRA DEL FLETE
# `valor_transporte` es una cifra DERIVADA de la tarifa del transportador en esa ruta
# (`tarifas.tarifa_de_transporte`): litros × tarifa si se cobra por litro, o la parte
# que le toca del fijo del día si se cobra por día completo. No es un campo que el
# usuario mande, así que el candado de arriba —que mira campos— no la cubre, y hay que
# decidir aparte cuándo se vuelve a derivar.
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
#      Cuenta como suelto el día que ESTE guardado suelta (se le cambia el transportador,
#      o se le saca la fecha del período): se va con la tarifa de su grupo nuevo.
#   3. EL DÍA SE QUEDA EN SU COMPROBANTE: NO SE LE TOCA LA FOTO. Se la pone el RECUADRE
#      de ese comprobante, y punto. Con dos excepciones, y las dos por la misma razón —el
#      comprobante no puede ponerle la foto a un día que ya no le pertenece a ese
#      renglón—:
#        · EL DÍA QUE QUEDA APAGADO. El recuadre solo mira los activos, así que un día
#          apagado no compone ningún renglón: su foto se deriva acá con la tarifa de hoy
#          (en día fijo, CERO, que es lo que vale un día que no se le paga a nadie);
#        · EL DÍA AL QUE LE CAMBIAN LA RUTA. La ruta es la que ESCOGE la tarifa, así que
#          cambiarla es escoger otra a propósito: el día pasa a un grupo que el
#          comprobante puede no haber cobrado nunca —Nápoles, por litro— y de ese grupo
#          el papel no sabe ningún precio que conservar. No abre ninguna puerta: si el
#          grupo al que llega SÍ está en el comprobante, el recuadre le vuelve a poner
#          encima la cifra del papel.
#
# EL CASO 3 ES LA REGLA QUE CIERRA EL CRUCE DE MODOS, Y LA DA EL DUEÑO:
#
#     LA FOTO DE UNA RECEPCIÓN QUE YA ESTÁ EN UN COMPROBANTE LA MANDA ESE COMPROBANTE,
#     NO LA TARIFA DE HOY.
#
# El porqué, con las dos cifras del caso que lo destapó. La ruta "A fábrica" era POR
# LITRO a $242,76; el 16/07 Aurelio 82,00 L y Marleny 137,45 L, comprobante emitido en
# 219,45 L × $242,76 = $53.273,68. El dueño le pone DÍA FIJO $150.000 a esa ruta y
# después le corrige a Aurelio 82,00 → 91,30 L, una corrección de las de todos los días:
#
#   · escribiendo la foto con el modo de HOY (día fijo) esa recepción quedaba en $0,00
#     —en un fijo la fila sola no vale nada, solo tiene una PARTE del día—, el
#     comprobante, que está armado POR LITRO, sumaba lo que quedaba y caía a $33.367,36,
#     y el PDF imprimía "91,3 L $0 $0". Se aprobaba y se pagaba: al conductor le
#     faltaban $22.163,99 contra su propia cuenta por litro. Y el desglose seguía
#     cuadrando —fotos == renglones == total—, así que ninguna red de cuadre lo veía;
#   · sin tocarle la foto, el recuadre rehace el renglón con LA TARIFA CON QUE SE EMITIÓ
#     y los litros de hoy: 228,75 L × $242,76 = $55.531,35. Que es exactamente lo que se
#     le debe por lo que recogió, en el modo en que se le firmó el papel.
#
# Y hay una segunda cifra, del mismo cruce: corregirle la FECHA a un día de ese
# comprobante le inyectaba un día fijo completo y lo dejaba en $183.367,36 (+$130.093,68)
# sin que nadie oprimiera Recalcular. La cierra la misma regla, del lado del comprobante:
# ver `_ComoCobroLaRuta` en liquidaciones/service.py, que hoy lee cómo cobró cada ruta de
# lo que el comprobante dejó ESCRITO al emitirse (`LiquidacionRuta`) y no de deducirlo de
# los renglones que le sobrevivan. Deducirlo dejaba dos puertas abiertas —apagar el día y
# prenderlo, o cambiarle la ruta y devolverla— por las que el mismo papel se re-precificaba
# solo: $150.000 amanecían en $19.906,32 y al revés.
#
# ANTES DE ESTO EL CASO 3 DECÍA "se re-deriva si el guardado le movió algo al flete", y
# esa versión ya había arreglado la mitad del problema: dos días del 02/06 en Nápoles
# (44,23 + 82,48 = 126,71 L) con el comprobante APROBADO en $30.760,12 a $242,76; alguien
# sube la tarifa a $300 —legítimo, para la quincena siguiente— y alguien más escribe "el
# tarro venía mal tapado" en uno de los días. Con la regla vieja ("se re-deriva siempre")
# esa nota re-precificaba el comprobante a $38.013,00 y le quitaba el visto bueno.
# Escribir una observación sigue sin mover un peso; lo que cambia ahora es que corregir
# los LITROS tampoco re-precifica: mueve el renglón por los litros, a la tarifa vieja.
#
# Y no le quita al dueño la salida del caso 2: si el día ya está en un comprobante, la
# tarifa corregida le llega por el botón Recalcular de ese comprobante, que es donde
# tiene que estar la decisión de re-precificar.

# ---------------------------------------------------------------------------
# DÓNDE VIVE LA CUENTA DEL DÍA FIJO, Y POR QUÉ AQUÍ
# ---------------------------------------------------------------------------
# EL PROBLEMA, dicho en una línea: la foto del flete se escribe al registrar CADA
# recepción, pero el fijo del día solo se conoce cuando se sabe CUÁLES son todas las
# recepciones de ese día. Registrar la segunda recepción del 16/07 le cambia lo que le
# tocaba a la primera: con una sola, esa recepción carga los $150.000 completos; con
# dos, $75.000 cada una. En modo POR LITRO esto no pasa —la foto de un día se sostiene
# sola, litros × tarifa, sin mirar a nadie más—.
#
# LA REGLA, ANTES QUE NADA, porque es la que ordena todo lo demás: EN UN DÍA FIJO EL
# RENGLÓN VALE LA TARIFA, nunca la suma de las fotos. Las fotos son SOLO el reparto de
# esa cifra entre las recepciones de ese (día, ruta), y su única obligación es sumarla
# exacto. La cuenta que la dice está en UN solo sitio —`tarifas.valor_del_grupo`— y no
# hay ninguna otra: dos verdades sobre la misma plata es la enfermedad que esto cerró.
#
# LA DECISIÓN: la cuenta del fijo NO es de una fila, es DEL GRUPO (transportador, día,
# ruta), y vive en dos sitios que se reparten el trabajo sin pisarse. Cada uno es dueño
# de las recepciones que le corresponden, y la frontera es EL COMPROBANTE:
#
#   1. LAS RECEPCIONES QUE TODAVÍA NO ESTÁN EN NINGÚN COMPROBANTE DE FLETE
#      (`liquidacion_transporte_id IS NULL`) las reparte ESTE servicio, en
#      `_repartir_el_fijo_del_dia`, después de cada escritura: crear, corregir, apagar
#      y borrar. Son las que van a formar el renglón del comprobante que todavía no
#      existe, así que su suma tiene que ser el fijo del día en todo momento —la grilla
#      de la quincena y contabilidad las están leyendo—.
#   2. LAS QUE YA ESTÁN EN UN COMPROBANTE las reparte EL COMPROBANTE
#      (`_reparto_del_flete` en liquidaciones/service.py, que ya existía y ya repartía
#      centavos), al generar, al recalcular y al recuadrar. Ahí el renglón es la verdad
#      y las fotos se acomodan a él.
#
# LAS DOS USAN LAS MISMAS DOS FUNCIONES: `tarifas.valor_del_grupo` para saber cuánto vale
# el grupo y `tarifas.reparto_entre_las_fotos` para bajarlo a las fotos. Una sola cuenta
# de cada cosa, porque dos formas de valorar (o de repartir) los mismos $150.000 es como
# aparece el centavo —o los $400.000— que no cuadran.
#
# Y EL GRUPO ES EL MISMO EN LAS DOS: (transportador, día, ruta) dentro de un mismo
# comprobante —o dentro de "todavía sin comprobante"—. O sea que cada grupo es
# EXACTAMENTE el conjunto de recepciones que produce UN renglón de UN comprobante, y por
# eso la suma de las fotos de un grupo es siempre, al centavo, su renglón.
#
# EL ORDEN ENTRE LOS DOS: primero el comprobante (2) y después lo pendiente (1). No es
# indiferente: lo pendiente le pregunta al comprobante si ese (día, ruta) ya se cobró
# completo, así que el comprobante tiene que estar al día cuando se le pregunta. Ver
# `actualizar`, que es donde está el caso con cifras.
#
# EL CANDADO MANDA SOBRE TODO ESTO: si por el flete de ese día ya salió plata, la foto
# NO se toca. Del lado 1 eso es gratis —una recepción cuyo flete ya se pagó no está en
# el grupo pendiente, está en el comprobante pagado— y del lado 2 lo hace cumplir
# `_fotos_congeladas`, que si encuentra UNA foto congelada no deja que el reparto
# escriba ninguna.
#
# LO QUE SÍ QUEDA DICHO, porque es la única grieta y es mejor tenerla escrita que
# descubrirla: si a un (día, ruta) FIJO se le anota leche DESPUÉS de que su día ya se
# cobró en un comprobante, esa recepción nueva entra con flete $0,00. No es un descuido:
# ese día ya costó $150.000 y ya se pagó, y recoger un proveedor más no cuesta más —que
# es literalmente lo que significa la tarifa fija—. Quien lo decide es
# `LiquidacionRepository.viajes_ya_cobrados`, y ahí está el caso completo con
# cifras. Si el dueño ANULA ese comprobante, el día vuelve a estar por cobrar y las
# recepciones se rearman todas juntas en un solo fijo.

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
        self,
        candado: CandadoRecepcion,
        *,
        se_queda_en_el_comprobante: bool,
        queda_apagada: bool,
    ) -> bool:
        """Si este guardado tiene que volver a calcular la FOTO del flete del día.

        Los casos están explicados arriba, en el bloque "CUÁNDO SE VUELVE A CALCULAR LA
        CIFRA DEL FLETE". En una línea: nunca sobre un flete pagado; siempre sobre un día
        que NO está en ningún comprobante; y NUNCA sobre un día que se queda dentro del
        suyo, porque ahí la foto la manda ese comprobante y no la tarifa de hoy.

        `se_queda_en_el_comprobante` es lo que decide, y es la regla entera del cruce de
        modos: mientras el día siga dentro de su comprobante de flete, quien le pone la
        foto es el RECUADRE de ese comprobante —el único que sabe en qué modo y a qué
        tarifa se armó ese papel—. Si el guardado lo SUELTA (le cambia el transportador,
        o le saca la fecha del período), el día vuelve a estar suelto y ahí sí manda la
        tarifa de hoy, que es la que le corresponde a su grupo nuevo.

        Quien llama cuenta también como "no se queda" el día al que le CAMBIAN LA RUTA,
        aunque la marca del comprobante no se le suelte: la ruta es la que escoge la
        tarifa, así que el día pasa a un grupo del que ese papel puede no saber ningún
        precio. Está explicado en el sitio donde se decide (`actualizar`) y en el bloque
        del encabezado.

        `queda_apagada` es la otra excepción, y es de las que hay que decir en voz alta:
        un día APAGADO no compone ningún renglón —el recuadre solo mira los activos—, así
        que el comprobante no puede ponerle la foto ni aunque quisiera. Se le deriva con
        la tarifa de hoy, exactamente como se hacía siempre: en día fijo eso es CERO (no
        se le paga a nadie por un día apagado, y así no queda un fijo fantasma colgado) y
        por litro es litros × tarifa, que es lo que esa foto valía y sigue valiendo.
        """
        if candado.flete_pagado:
            return False
        if candado.flete is None:
            return True
        return not se_queda_en_el_comprobante or queda_apagada

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
            tarifa = Tarifa(MODO_POR_LITRO, CERO)
            if transportador_id:
                transportador = self._transportador_de_la_tarifa(transportador_id, actual)
                # UNA SOLA función para esta cuenta, compartida con liquidaciones: la
                # tarifa de la ruta si la tiene, si no la general del transportador. Y
                # trae el MODO pegado a la cifra: sin él, $150.000 el día se leerían
                # como $150.000 el litro.
                tarifa = tarifa_de_transporte(transportador, ruta_id)
            # LO QUE SE ESCRIBE ACÁ ES LA FOTO DE ESTA FILA MIRÁNDOSE SOLA, y en modo
            # por litro eso es la respuesta final (litros × tarifa, redondeado una vez:
            # la misma cuenta de siempre, ni un centavo distinto).
            #
            # EN MODO DÍA FIJO SE ESCRIBE CERO, Y ES LO ÚNICO HONESTO QUE SE PUEDE
            # ESCRIBIR MIRANDO ESTA FILA SOLA: en un fijo la recepción no tiene ninguna
            # cifra propia que defender, solo tiene una PARTE de lo que vale el día, y
            # cuánto es esa parte depende de quiénes más estén en ese (día, ruta) —cosa
            # que desde aquí todavía no se sabe: la fila ni siquiera ha bajado a la
            # base—. Un cero es un lugar vacío que el reparto llena enseguida; cualquier
            # otra cifra sería una segunda verdad sobre la misma plata.
            #
            # ANTES SE ESCRIBÍA EL FIJO COMPLETO, y ese fue el defecto: era cierto
            # mientras esta fuera la única recepción del día, y falso en cuanto la
            # recepción YA ESTABA EN UN COMPROBANTE, porque entonces el reparto de abajo
            # no la alcanzaba (solo reparte las que no están en ninguno) y el
            # comprobante terminaba SUMANDO ese fijo completo como si fuera una cifra
            # propia. Corregirle los litros a un proveedor del día de $150.000 lo dejaba
            # en $261.045,13; corrigiéndole a los cinco, en $554.826,77. Y esa cifra se
            # aprobaba y se pagaba.
            #
            # QUIÉN LO LLENA, siempre, y por eso el cero nunca se queda:
            #   · si el día NO está en ningún comprobante → `_repartir_el_fijo_del_dia`,
            #     un renglón más abajo en `crear` y en `actualizar`;
            #   · si el día YA ESTÁ en uno → el recuadre de ESE comprobante
            #     (`_recuadrar`), que es el dueño de esas fotos.
            # Ver el bloque "DÓNDE VIVE LA CUENTA DEL DÍA FIJO" del encabezado.
            #
            # Y si la recepción queda APAGADA, el cero se queda, que es exactamente lo
            # que tiene que quedar: un día apagado no compone ningún renglón, así que no
            # le toca ni un peso del fijo. Es la foto fantasma de $150.000 que antes
            # quedaba colgada en la recepción apagada.
            data["valor_transporte"] = CERO if tarifa.es_dia_fijo else valor_del_grupo(
                tarifa, litros
            )
        data["valor_neto"] = data["valor_bruto"] + bonif - desc
        if data["valor_neto"] < 0:
            raise BusinessError("El valor neto no puede ser negativo: revise los descuentos")
        return data

    # ------------------------------------------------- el fijo del día, repartido
    def _pendientes_del_dia(
        self, transportador_id: uuid.UUID, fecha: date, ruta_id: uuid.UUID | None
    ) -> list[RecepcionLeche]:
        """TODAS las recepciones PENDIENTES de ese (transportador, día, ruta).

        "Pendientes" es `liquidacion_transporte_id IS NULL`: las que todavía no están en
        ningún comprobante de flete y que por lo tanto van a formar UN renglón del
        comprobante que se genere. Ese es el grupo del que este servicio es dueño (ver el
        bloque "DÓNDE VIVE LA CUENTA DEL DÍA FIJO"); las que ya están en un comprobante
        las reparte el comprobante.

        VIENEN LAS APAGADAS TAMBIÉN, y quien llama las separa. No entran al reparto —un
        día apagado no se le paga a nadie y no puede reclamar parte del fijo— pero hay
        que verlas para poder DEJARLES LA FOTO EN CERO: si se las deja por fuera de la
        consulta, la foto que traían se queda colgada ahí, y una foto colgada de $150.000
        es un fijo fantasma esperando a que alguien vuelva a prender el día. Ver
        `_repartir_el_fijo_del_dia`.

        Va por `base_query`, así que el filtro por empresa y el de borrados no se pueden
        olvidar: acá se reescribe plata.

        La ruta se compara con `IS NULL` cuando viene en nulo y no con `= NULL`, que en
        SQL no encuentra nada: el día SIN ruta también puede tener fijo (por la tarifa
        general del transportador) y es un grupo igual de válido que los demás.
        """
        consulta = self.repo.base_query().where(
            RecepcionLeche.transportador_id == transportador_id,
            RecepcionLeche.fecha == fecha,
            RecepcionLeche.liquidacion_transporte_id.is_(None),
            RecepcionLeche.ruta_id.is_(None) if ruta_id is None
            else RecepcionLeche.ruta_id == ruta_id,
        )
        return list(self.db.scalars(consulta).all())

    def _repartir_el_fijo_del_dia(
        self,
        transportador_id: uuid.UUID | None,
        fecha: date | None,
        ruta_id: uuid.UUID | None,
        *,
        recepcion_del_grupo: RecepcionLeche | None = None,
    ) -> None:
        """Reparte el FIJO de un (transportador, día, ruta) entre sus recepciones.

        ES LA CUENTA QUE HACE IMPOSIBLE EL ERROR DE LOS $750.000. Si ese día recogió
        leche de cinco proveedores en la ruta "a fábrica", el flete del día son $150.000
        —no $150.000 × 5— y esta función es la que baja esos $150.000 a las cinco
        recepciones de modo que la suma de las cinco fotos dé EXACTO $150.000.

        SE LLAMA DESPUÉS DE CADA ESCRITURA de una recepción, con la fila ya bajada a la
        base (`flush`), porque solo entonces el grupo está completo y se puede ver quién
        lo compone. Y se llama DOS VECES cuando la recepción se movió de día, de ruta o
        de transportador: una por el grupo que dejó (los que se quedan absorben el fijo
        completo) y otra por el grupo al que llegó.

        NO HACE NADA EN MODO POR LITRO, y es a propósito: ahí la foto de cada día se
        sostiene sola (litros × tarifa) y los centavos del grupo los cierra el
        comprobante al generarlo, exactamente como venía funcionando. Meter el reparto
        también ahí movería centavos en un momento en que hoy no se mueven, sin ninguna
        necesidad.

        SI EL DÍA YA SE COBRÓ COMPLETO en un comprobante, el grupo pendiente vale $0,00:
        ese día ya costó $150.000, ya se cobró, y recoger un proveedor más no cuesta más.
        Ver `LiquidacionRepository.viajes_ya_cobrados`, que es donde está el caso con
        cifras. POR ESO ESTO CORRE DESPUÉS DEL RECUADRE y no antes: quién ya cobró qué lo
        dicen los comprobantes, así que primero se dejan ellos al día. Al revés, apagar
        la única recepción que sostenía el renglón de un comprobante dejaba el día
        pendiente en $0,00 leyendo un renglón que un renglón después iba a desaparecer:
        el camión hizo el viaje y no se le pagaba nada.

        LAS RECEPCIONES APAGADAS DEL MISMO (DÍA, RUTA) QUEDAN EN $0,00. No entran al
        reparto —no se le paga a nadie por un día apagado— pero tampoco se las puede
        dejar con la foto que traían: esa cifra no compone ningún renglón, así que si se
        quedara, el desglose del día dejaría de sumar el día. Es la foto fantasma de
        $150.000 que quedaba colgada al apagar una recepción de un día fijo.

        `recepcion_del_grupo` es un salvavidas para leer la tarifa de un transportador
        RETIRADO (borrado en suave): el repositorio no lo devuelve, pero sus días siguen
        existiendo y siguen siendo editables, así que la tarifa se lee de la fila que
        trae la recepción —exigiendo que sea de esta empresa, igual que
        `_transportador_de_la_tarifa`—. Sin esto, corregirle una observación a un día
        viejo de un transportador retirado dejaría el grupo sin repartir.
        """
        if transportador_id is None or fecha is None:
            return
        transportador = self._transportador_del_grupo(transportador_id, recepcion_del_grupo)
        tarifa = tarifa_de_transporte(transportador, ruta_id)
        if not tarifa.es_dia_fijo:
            return
        pendientes = self._pendientes_del_dia(transportador_id, fecha, ruta_id)
        grupo = [r for r in pendientes if r.estado == "activo"]
        apagadas = [r for r in pendientes if r.estado != "activo"]
        ya_cobrados = LiquidacionRepository(
            self.db, self.ctx.empresa_id
        ).viajes_ya_cobrados(transportador_id)
        # LA ÚNICA CUENTA, la misma que usa el comprobante: el día vale la tarifa (o
        # $0,00 si ya se cobró completo en otro comprobante), NUNCA la suma de las fotos.
        # Acá no hay `ya_pactado` que pasarle porque este grupo todavía no está en ningún
        # comprobante: no hay ningún renglón anterior que conservar.
        total = valor_del_grupo(
            tarifa,
            sum((Decimal(r.cantidad_litros or 0) for r in grupo), CERO),
            ya_cobrado=(fecha, ruta_id) in ya_cobrados,
        )
        asignado = reparto_entre_las_fotos(
            tarifa, [(r.id, Decimal(r.cantidad_litros or 0)) for r in grupo], total
        )
        # Las apagadas van a cero por fuera del reparto: no son partes de nada.
        asignado.update({r.id: CERO for r in apagadas})
        self._bajar_las_fotos(
            grupo + apagadas,
            asignado,
            "cambió cuántas recepciones tiene ese día en esa ruta, así que el flete de "
            "día fijo se repartió otra vez; es una cifra informativa y no mueve el valor "
            "total, el saldo ni el PDF de esta liquidación",
        )

    def _bajar_las_fotos(
        self,
        filas: list[RecepcionLeche],
        asignado: dict[uuid.UUID, Decimal],
        motivo: str,
    ) -> None:
        """Escribe las fotos que cambiaron y deja al día lo que dependía de ellas.

        Es la mitad final —y compartida— de los dos caminos que rehacen fotos de días
        SUELTOS: el reparto del fijo de un día (`_repartir_el_fijo_del_dia`) y la
        rederivación completa cuando a un transportador le cambian el MODO de una tarifa
        (`rehacer_las_fotos_sueltas`). Está en una función porque las dos tienen que
        escribir igual: solo lo que de verdad cambió, y sin olvidarse de la columna
        informativa de la liquidación de la leche.

        LAS LIQUIDACIONES DE LECHE de los días que de verdad cambiaron de foto: su
        columna informativa `valor_transporte` es la suma del flete de sus días y se
        quedaría diciendo otra cosa que sus propias recepciones. Es el mismo defecto —y el
        mismo remedio— que ya estaba resuelto para el otro camino; ver
        `LiquidacionService.refrescar_transporte_informativo`.
        """
        de_la_leche: set[uuid.UUID] = set()
        for recepcion in filas:
            nueva = asignado.get(recepcion.id)
            if nueva is None or nueva == Decimal(recepcion.valor_transporte or 0):
                continue
            recepcion.valor_transporte = nueva
            recepcion.updated_by = self.ctx.user_id
            if recepcion.liquidacion_id is not None:
                de_la_leche.add(recepcion.liquidacion_id)
        # Se baja el reparto SIEMPRE, aunque no haya nada más que hacer: lo que sigue
        # después de esto —el recuadre de las liquidaciones, la respuesta— vuelve a leer
        # estas mismas filas desde la base, y la sesión no hace autoflush.
        self.db.flush()
        if not de_la_leche:
            return
        from app.modules.liquidaciones.service import LiquidacionService

        servicio = LiquidacionService(self.db, self.ctx)
        # Ordenado por el texto del id para que dos corridas dejen las entradas de
        # bitácora en el mismo orden, igual que `_poner_al_dia_el_flete_de_la_leche`.
        for liq_id in sorted(de_la_leche, key=str):
            servicio.refrescar_transporte_informativo(liq_id, motivo=motivo)

    def rehacer_las_fotos_sueltas(self, transportador_id: uuid.UUID) -> None:
        """Vuelve a derivar el flete de TODOS los días SUELTOS de un transportador.

        LA LLAMA LA PANTALLA DE TARIFAS cuando a un transportador le cambian el MODO de
        una tarifa (por litro ↔ día fijo). Ver `TransportadorService.actualizar`, que es
        donde está la decisión de cuándo, y por qué solo con el modo.

        EL PROBLEMA QUE CIERRA: la foto del flete de un día es DERIVADA de la tarifa, pero
        la tarifa no es un campo de la recepción, así que cambiarla no llegaba sola a los
        días ya anotados. Mientras eso fuera un cambio de CIFRA —de $242,76 a $255— la
        foto quedaba corrida por unos pesos y se corregía al guardar el día, al generar el
        comprobante o al recalcularlo. Con un cambio de MODO no: pasar la ruta "A fábrica"
        de DÍA FIJO $150.000 a POR LITRO $242,76 deja fotos que ya no significan nada —una
        parte de un fijo que ya no existe—, y la grilla de la quincena, el resumen y la
        contabilidad las siguen sumando. Con dos días de 219,45 L eso son $150.000 en
        pantalla contra los $53.273,68 que de verdad se van a cobrar: $96.726,32 de
        diferencia esperando a que alguien genere el comprobante.

        SOLO TOCA LOS DÍAS SUELTOS —los que no están en ningún comprobante de flete—, y
        eso no es una limitación sino LA MISMA REGLA de siempre dicha desde el otro lado:
        la foto de una recepción que ya está en un comprobante la manda ese comprobante y
        se re-precifica con el botón Recalcular, que el dueño oprime a propósito. Cambiarle
        el modo a una tarifa NO puede re-precificar un papel ya emitido.

        LA CUENTA ES LA MISMA de siempre y sale de las mismas dos funciones
        (`valor_del_grupo` y `reparto_entre_las_fotos`), por (día, ruta):

          · POR LITRO cada día se sostiene solo: litros × tarifa. Idéntico a lo que
            escribe `_completar_y_calcular` al guardar el día;
          · DÍA FIJO el grupo vale la tarifa —o $0,00 si ese (día, ruta) ya se cobró
            completo en un comprobante— y se reparte entre sus recepciones vivas; las
            apagadas quedan en $0,00, que es lo que vale un día que no compone renglón.
        """
        transportador = TransportadorRepository(self.db, self.ctx.empresa_id).get(
            transportador_id
        )
        if transportador is None:
            return
        sueltas = list(
            self.db.scalars(
                self.repo.base_query().where(
                    RecepcionLeche.transportador_id == transportador_id,
                    RecepcionLeche.liquidacion_transporte_id.is_(None),
                )
            ).all()
        )
        if not sueltas:
            return
        ya_cobrados = LiquidacionRepository(
            self.db, self.ctx.empresa_id
        ).viajes_ya_cobrados(transportador_id)
        grupos: dict[tuple[date, uuid.UUID | None], list[RecepcionLeche]] = {}
        for recepcion in sueltas:
            grupos.setdefault((recepcion.fecha, recepcion.ruta_id), []).append(recepcion)
        asignado: dict[uuid.UUID, Decimal] = {}
        for (fecha, ruta_id), del_grupo in grupos.items():
            tarifa = tarifa_de_transporte(transportador, ruta_id)
            if not tarifa.es_dia_fijo:
                for recepcion in del_grupo:
                    asignado[recepcion.id] = valor_del_grupo(
                        tarifa, Decimal(recepcion.cantidad_litros or 0)
                    )
                continue
            vivas = [r for r in del_grupo if r.estado == "activo"]
            total = valor_del_grupo(
                tarifa,
                sum((Decimal(r.cantidad_litros or 0) for r in vivas), CERO),
                ya_cobrado=(fecha, ruta_id) in ya_cobrados,
            )
            asignado.update(
                reparto_entre_las_fotos(
                    tarifa,
                    [(r.id, Decimal(r.cantidad_litros or 0)) for r in vivas],
                    total,
                )
            )
            asignado.update({r.id: CERO for r in del_grupo if r.estado != "activo"})
        self._bajar_las_fotos(
            sueltas,
            asignado,
            "le cambiaron el MODO de la tarifa al transportador (por litro ↔ día fijo), "
            "así que el flete de sus días todavía sin liquidar se volvió a derivar; es "
            "una cifra informativa y no mueve el valor total, el saldo ni el PDF de esta "
            "liquidación",
        )

    def _volver_al_viaje_ya_cobrado(self, recepcion: RecepcionLeche) -> Liquidacion | None:
        """El día que se MUEVE a un viaje ya cobrado entra a ese viaje, no se queda afuera.

        EL CASO, con las cifras. El 16/07 en la ruta fija ($150.000 el día): Aurelio
        82,00 L y Marleny 137,45 L, comprobante emitido y repartido $56.049,21 /
        $93.950,79. A Aurelio le cambian el transportador —anotaron mal quién recogió—:
        el día se suelta de ese comprobante, que se recuadra sin él y le deja a Marleny
        los $150.000 completos, que es lo correcto mientras Aurelio esté en otra parte.
        Un minuto después le corrigen el transportador de vuelta.

        Sin esto, Aurelio vuelve SUELTO: el viaje del 16/07 ya está cobrado, así que su
        parte del fijo es $0,00 y la grilla queda diciendo que recoger 137,45 L costó
        $150.000 y recoger 82,00 L no costó nada. Las dos cifras suman bien —el desglose
        cuadra— y las dos son falsas: vinieron en el mismo camión. Y esas fotos son las
        que lee el costeo del queso.

        LO QUE HACE: devolverle la marca del comprobante que cobra ese viaje, para que el
        recuadre que viene enseguida vuelva a repartir los $150.000 entre las dos. EL
        VIAJE SIGUE VALIENDO $150.000 —no se cobra un peso más, ni se cobra dos veces—:
        lo único que cambia es entre quiénes se reparte, que es lo que la foto significa.

        LAS TRES CONDICIONES, y cada una tapa algo distinto:

          · SOLO CUANDO EL DÍA SE MOVIÓ (`_CAMPOS_QUE_MUEVEN_EL_DIA`, lo revisa quien
            llama). Esto NO es para la leche anotada tarde: una recepción NUEVA de un día
            que ya se cobró entra en $0,00 y así se queda, porque ese día ya costó
            $150.000 y recoger un proveedor más no cuesta más —es literalmente lo que
            significa una tarifa fija, y es como estaba probado—. Lo que se corrige acá es
            otra cosa: una leche que ya estaba en el viaje, se salió por una corrección y
            volvió.
          · SOLO SI ESTÁ SUELTA Y VIVA. Si todavía tiene comprobante, no hay nada que
            devolver; si quedó apagada, no compone ningún renglón.
          · SOLO SI ESE COMPROBANTE TODAVÍA SE PUEDE TOCAR. Si ya se pagó (o tiene un
            abono, o su deuda ya se cobró en otra quincena) sus cifras están congeladas y
            meterle un día más sería mover plata entregada: ahí la recepción se queda
            suelta en $0,00, que es la verdad —ese viaje ya se pagó y no se puede
            repartir otra vez—.

        VALE PARA LOS DOS MODOS. En el fijo es donde duele —quedarse afuera vale $0,00 con
        la leche viva— pero por litro el resultado es el mismo y es igual de correcto: el
        día vuelve a su papel, el renglón se rehace con los litros de hoy a la tarifa con
        que se emitió, y el comprobante queda otra vez EXACTAMENTE como estaba antes de que
        el día se fuera. Deshacer una corrección tiene que deshacerla del todo.

        Devuelve la liquidación a la que volvió, para que quien llama la recuadre.
        """
        if (
            recepcion.liquidacion_transporte_id is not None
            or recepcion.estado != "activo"
            or recepcion.transportador_id is None
        ):
            return None
        liquidacion = LiquidacionRepository(
            self.db, self.ctx.empresa_id
        ).comprobante_que_cobra_el_viaje(
            recepcion.transportador_id, recepcion.fecha, recepcion.ruta_id
        )
        if liquidacion is None or _traba_el_dia(liquidacion):
            return None
        recepcion.liquidacion_transporte_id = liquidacion.id
        recepcion.updated_by = self.ctx.user_id
        # Se baja antes de recuadrar: el recuadre lee sus recepciones desde la base y la
        # sesión no hace autoflush.
        self.db.flush()
        return liquidacion

    def _transportador_del_grupo(
        self, transportador_id: uuid.UUID, recepcion: RecepcionLeche | None
    ) -> Transportador | None:
        """El transportador del que sale la tarifa del grupo, aguantando que esté BORRADO.

        Es el mismo razonamiento de `_transportador_de_la_tarifa` (ver su docstring: un
        transportador retirado no se lleva sus días con él), pero acá NO revienta si no
        lo encuentra: esto se llama después de haber escrito, y un 404 a estas alturas
        tumbaría un guardado que ya pasó todas las puertas. Sin transportador no hay
        tarifa que leer y el reparto simplemente no corre.

        SE MIRA PRIMERO LA FILA QUE YA TRAE LA RECEPCIÓN, y eso ahorra una consulta EN
        CADA ESCRITURA de recepción: `RecepcionLeche.transportador` es lazy="joined", así
        que viene cargado con la fila, y es el MISMO objeto que devolvería el repositorio
        (el mapa de identidad de la sesión). Se le exige `empresa_id`, que es lo que el
        repositorio aportaba: la tarifa de otra quesera no puede decidir plata en esta. Y
        de paso resuelve solo el caso del transportador retirado, que el repositorio no
        devuelve.

        Se cae al repositorio cuando la recepción NO es de ese transportador, que es el
        caso del grupo que un día ACABA de dejar al cambiarle el transportador.

        SE LE EXIGE AL OBJETO CARGADO QUE SEA EL DEL ID, y no basta con que la columna
        `transportador_id` coincida. Parece redundante y no lo es: acabamos de ESCRIBIR
        esa columna, y cambiarle el id a una relación YA CARGADA no la recarga sola —el
        objeto que cuelga de `recepcion.transportador` sigue siendo el transportador VIEJO
        hasta que la sesión lo expire—. Sin esta línea, pasarle un día de un transportador
        a otro leía la tarifa del que lo dejó: la ruta "A fábrica" de Alex es de DÍA FIJO,
        así que el día de 82,00 L que se le pasaba a Beto —que cobra $310,15 POR LITRO, o
        sea $25.432,30— salía con una foto de $150.000, un viaje fijo de un transportador
        que no cobra por viaje. Y el mismo día terminaba cobrado dos veces: $150.000 en el
        comprobante de Alex y otros $150.000 de fantasma del lado de Beto.
        """
        if (
            recepcion is not None
            and recepcion.transportador_id == transportador_id
            and recepcion.transportador is not None
            and recepcion.transportador.id == transportador_id
            and recepcion.transportador.empresa_id == self.ctx.empresa_id
        ):
            return recepcion.transportador
        return TransportadorRepository(self.db, self.ctx.empresa_id).get(transportador_id)

    def validar_crear(self, data: dict[str, Any]) -> None:
        if self.repo.existe_registro_dia(data["proveedor_id"], data["fecha"]):
            raise ConflictError(
                "Ya existe una recepción de este proveedor en esa fecha. Edite el registro existente"
            )

    def crear(self, payload: Any) -> RecepcionLeche:
        data = payload.model_dump(exclude_unset=True)
        data = self._completar_y_calcular(data)
        recepcion = super().crear(data)
        # Se baja la fila ANTES de repartir: el reparto lee el grupo desde la base (la
        # sesión no hace autoflush) y sin esto la recepción que se acaba de crear no
        # estaría ahí, o sea que el fijo se repartiría entre todas menos ella.
        self.db.flush()
        self._repartir_el_fijo_del_dia(
            recepcion.transportador_id,
            recepcion.fecha,
            recepcion.ruta_id,
            recepcion_del_grupo=recepcion,
        )
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
        # LO QUE ESTE GUARDADO SUELTA, resuelto ANTES de calcular la plata y no después,
        # porque es lo que decide si la foto del flete la pone este camino o el
        # comprobante. Sigue devolviendo lo mismo que devolvía calculándolo abajo: solo
        # mira el transportador y la fecha, que ya están en `data`.
        soltar = self._marcas_a_soltar(actual, liquidaciones, data, nueva_fecha)
        # ------------------------------------------- CUÁNDO SE VUELVE A CALCULAR EL FLETE
        # La cifra del flete (`valor_transporte`) es la plata que se le va a cobrar al
        # transportador por ese día y es DERIVADA (litros × tarifa de hoy, o la parte que
        # le toca del fijo). Cuándo se vuelve a derivar lo decide
        # `_hay_que_rederivar_el_flete`, y la regla completa —con las cifras del caso que
        # cerró— está en el bloque "CUÁNDO SE VUELVE A CALCULAR LA CIFRA DEL FLETE" del
        # encabezado.
        #
        # Lo que hay que tener presente al leer esta línea: MIENTRAS EL DÍA SE QUEDE EN SU
        # COMPROBANTE, ACÁ NO SE LE TOCA LA FOTO. Se la pone el recuadre de ese
        # comprobante, que es el único que sabe en qué modo y a qué tarifa se armó ese
        # papel. Este camino solo la deriva cuando el día está (o queda) suelto, o cuando
        # queda apagado y por lo tanto ya no compone ningún renglón.
        recalcular_flete = self._hay_que_rederivar_el_flete(
            candado,
            se_queda_en_el_comprobante=(
                actual.liquidacion_transporte_id is not None
                and "liquidacion_transporte_id" not in soltar
                # LA RUTA ES LA QUE ESCOGE LA TARIFA, así que cambiarla es escoger otra a
                # propósito: ahí sí se re-deriva con la de hoy, como siempre. El día pasa
                # a un grupo que el comprobante puede no haber cobrado nunca —Nápoles,
                # por litro— y de ese grupo el papel no sabe ningún precio que conservar.
                # No abre ninguna puerta: si el grupo al que llega SÍ está en el
                # comprobante, el recuadre le vuelve a poner encima la cifra del papel.
                and "ruta_id" not in cambios
            ),
            queda_apagada=data.get("estado", actual.estado) != "activo",
        )
        data = self._completar_y_calcular(data, actual, recalcular_flete=recalcular_flete)
        data.update(soltar)
        # EL GRUPO QUE ESTE DÍA TENÍA ANTES DE MOVERSE, apuntado antes de escribir: si el
        # guardado le cambia el día, la ruta o el transportador —o lo apaga—, el fijo del
        # grupo que deja tiene que repartirse otra vez entre los que se quedan, o esos
        # $150.000 quedarían repartidos entre una recepción de más.
        grupo_de_antes = (actual.transportador_id, actual.fecha, actual.ruta_id)

        recepcion = super().actualizar(entity_id, data)
        # Se baja el cambio antes de recuadrar: la sesión no hace autoflush y el
        # recálculo vuelve a leer las recepciones desde la base.
        self.db.flush()
        # EL DÍA QUE SE MUEVE A UN VIAJE FIJO YA COBRADO ENTRA A ESE VIAJE, y esto va
        # ANTES del recuadre para que el comprobante que lo recibe se rearme ya con él.
        # Ver `_volver_al_viaje_ya_cobrado`.
        if cambios & _CAMPOS_QUE_MUEVEN_EL_DIA:
            recuperado = self._volver_al_viaje_ya_cobrado(recepcion)
            if recuperado is not None:
                liquidaciones = liquidaciones + [recuperado]
        # EL RECUADRE VA PRIMERO Y EL FIJO PENDIENTE DESPUÉS, y el orden es plata.
        #
        # Cada uno es dueño de sus propias recepciones y en eso no se pisan (el recuadre
        # rearma las que están EN un comprobante; el reparto de abajo, las que no están en
        # ninguno). Pero el de abajo LE PREGUNTA AL DE ARRIBA: para saber cuánto vale un
        # día fijo pendiente hay que saber si ese (día, ruta) ya se cobró completo en un
        # comprobante, y eso lo dicen los renglones que el recuadre acaba de rehacer.
        #
        # Al revés se perdía plata, y así: el 16/07 fijo con dos recepciones, una en el
        # comprobante y otra suelta (anotada tarde, entró en $0,00 porque el día ya
        # estaba cobrado). Se apaga la del comprobante. Con el reparto primero, el
        # pendiente leía el renglón viejo —todavía existía—, se declaraba "ya cobrado" y
        # se quedaba en $0,00; un renglón después el recuadre borraba ese renglón, y el
        # día terminaba valiendo $0,00 con leche activa: el camión hizo el viaje y no se
        # le paga nada. Con el recuadre primero, el renglón ya no está cuando el
        # pendiente pregunta, y el día vuelve a valer sus $150.000.
        #
        # Ver el bloque "DÓNDE VIVE LA CUENTA DEL DÍA FIJO".
        self._recuadrar(liquidaciones)
        grupo_de_ahora = (recepcion.transportador_id, recepcion.fecha, recepcion.ruta_id)
        for transportador_id, fecha, ruta_id in dict.fromkeys((grupo_de_antes, grupo_de_ahora)):
            self._repartir_el_fijo_del_dia(
                transportador_id, fecha, ruta_id, recepcion_del_grupo=recepcion
            )
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
        # El grupo del que sale, apuntado antes de borrar: si era un día fijo, sus
        # $150.000 tienen que volver a repartirse entre las recepciones que quedan.
        grupo = (recepcion.transportador_id, recepcion.fecha, recepcion.ruta_id)
        super().eliminar(entity_id)
        self.db.flush()
        # El recuadre primero y el fijo pendiente después, por lo mismo que en
        # `actualizar`: el pendiente necesita saber qué días ya están cobrados, y eso lo
        # dicen los renglones que el recuadre acaba de rehacer.
        self._recuadrar(liquidaciones)
        self._repartir_el_fijo_del_dia(*grupo, recepcion_del_grupo=recepcion)

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
