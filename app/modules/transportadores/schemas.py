import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema, TenantRead
from app.modules.transportadores.models import MODO_DIA_FIJO, MODO_POR_LITRO

# EL MODO EN QUE SE COBRA LA TARIFA, con los dos únicos valores que existen.
#
# `Literal` y no `str`: por esta puerta entra plata, y un modo mal escrito —"fijo",
# "DIA_FIJO", texto basura por la dirección del endpoint— se guardaría en la columna
# y `tarifas._modo_de` lo leería como POR LITRO. O sea: el dueño cree que dejó "a
# fábrica" en $150.000 el día y el sistema le cobra $150.000 POR LITRO. Con el
# Literal eso rebota con un 422 que dice cuáles son los dos valores posibles.
#
# El por omisión es 'litro' en las tres puertas (crear, la fila de ruta y la
# lectura): es lo que significaba la tarifa desde que existe, así que una pantalla
# vieja que no mande el campo sigue guardando exactamente lo que guardaba.
#
# Los dos valores salen de las constantes del MODELO y no se reescriben acá: dos
# listas de modos es como terminan aceptando cosas distintas.
ModoDeTransporte = Literal[MODO_POR_LITRO, MODO_DIA_FIJO]  # type: ignore[valid-type]

# LA TARIFA POR LITRO, con la forma EXACTA de la columna que la va a guardar.
#
# Las dos columnas donde cae esto —`transportadores.valor_transporte` (la general)
# y `transportador_rutas.valor_transporte` (la de la ruta)— son Numeric(12, 2). Con
# un `Field(ge=0)` pelado el schema aceptaba cifras que la columna no puede
# guardar, y eso no da un error: da una cifra distinta, callada.
#
#   · $242,765 entraba y el POST respondía $242,765, pero la columna guarda
#     $242,77 (Postgres redondea la escala en silencio; SQLite ni eso, deja
#     $242,76). O sea que la pantalla mostraba una tarifa por litro que NO es la que
#     se le va a pagar al transportador: en 82 litros son $0,41 de diferencia, y el
#     dueño cuadra esto a mano;
#   · 1E+20 entraba también, y en Postgres el INSERT reventaba con un 22003
#     (numeric field overflow) — un 500 en la cara del usuario en vez de un mensaje.
#
# `max_digits=12, decimal_places=2` es la columna escrita en el schema: hasta
# $9.999.999.999,99 y ni un tercer decimal. El patrón es el de
# ventas/schemas.py (`Field(ge=0, decimal_places=2)`), con el techo que allá no
# hacía falta y acá sí.
def tarifa_por_litro(**extra: Any) -> Any:
    """El `Field` de una tarifa de transporte. Una sola definición para las dos.

    Se llama "por litro" por su origen y el nombre se conserva porque el techo es el
    mismo en los dos modos (la columna es la misma): en modo `dia_fijo` esta cifra es
    lo que vale el día completo, y $150.000 le cabe igual de sobra.
    """
    return Field(ge=0, max_digits=12, decimal_places=2, **extra)


class TransportadorRutaIn(BaseSchema):
    """Una ruta que hace el transportador, con la tarifa que cobra en ella y su modo."""

    ruta_id: uuid.UUID
    valor_transporte: Decimal = tarifa_por_litro()
    # 'litro' (litros × esta cifra) o 'dia_fijo' (el día vale esta cifra, sin
    # multiplicar por nada).
    #
    # VA ANULABLE Y NO CON 'litro' POR OMISIÓN, y eso cierra un hueco con plata de
    # verdad. La lista de rutas SE REEMPLAZA COMPLETA en cada PUT (ver
    # `TransportadorUpdate.rutas`), así que un cliente que mande la fila SIN el modo
    # —una pantalla vieja, un payload armado a mano, un script— le estaría poniendo
    # 'litro' a una ruta que estaba en día fijo… SIN cambiarle la cifra. Y ahí está el
    # desastre: los $150.000 del DÍA se vuelven $150.000 POR LITRO, o sea $45.000.000
    # de flete en un día de 300 litros. La cifra en la columna se ve igual; lo único que
    # cambió es una palabra que ese cliente ni sabe que existe.
    #
    # Con `None` = "no me toque el modo": si esa ruta ya estaba pegada al transportador
    # conserva el suyo, y si es nueva entra por litro (que es el significado de siempre y
    # el único que no cobra de más). Es la misma regla que este mismo schema ya usa para
    # `rutas` en el PUT, y por el mismo motivo. Quien SÍ quiere cambiar el modo lo manda,
    # y entonces manda los dos campos juntos, que es como se deben mover.
    modo_transporte: ModoDeTransporte | None = None


class TransportadorRutaRead(BaseSchema):
    """Lo mismo, pero CON EL NOMBRE de la ruta.

    Se manda el nombre y no solo el id para que la pantalla del transportador
    muestre "Nápoles — $242,76" sin tener que ir a pedir el catálogo de rutas
    aparte. Sale de la property `nombre` de TransportadorRuta.
    """

    ruta_id: uuid.UUID
    nombre: str | None = None
    valor_transporte: Decimal
    # El modo viaja SIEMPRE en la lectura, aunque sea 'litro', para que la pantalla
    # no tenga que adivinar cómo escribir la cifra: "$242,76 / L" es una cosa y
    # "$150.000 el día" es otra, y mostrar la segunda como la primera es la mentira
    # más fácil de cometer en esta pantalla.
    modo_transporte: ModoDeTransporte = MODO_POR_LITRO
    # La ruta está borrada del catálogo, pero la fila y su tarifa siguen acá.
    # La pantalla la tiene que MOSTRAR (es historia del transportador y esa tarifa
    # todavía cobra) y a la vez marcarla, para que el dueño entienda por qué le
    # aparece una ruta que ya no ve en el listado de rutas. Y se puede REENVIAR tal
    # cual en el PUT: la escritura acepta una ruta borrada que ya estaba pegada a
    # ese transportador. Ver TransportadorService._filas_de_rutas.
    ruta_eliminada: bool = False


class TransportadorCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=150)
    documento: str | None = None
    telefono: str | None = None
    # Tarifa GENERAL: la que se usa cuando el día no tiene ruta, o cuando la ruta
    # no tiene tarifa propia. Ver el comentario del modelo.
    valor_transporte: Decimal = tarifa_por_litro(default=Decimal("0"))
    # Y el modo de ESA tarifa general. El de cada ruta va en su propia fila: el mismo
    # transportador puede tener Nápoles por litro y "a fábrica" por día fijo.
    modo_transporte: ModoDeTransporte = MODO_POR_LITRO
    rutas: list[TransportadorRutaIn] | None = None


class TransportadorUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    documento: str | None = None
    telefono: str | None = None
    valor_transporte: Decimal | None = tarifa_por_litro(default=None)
    # El PUT es parcial: si el modo no viene, no se toca. Cambiar el modo de una
    # tarifa NO le mueve la plata a ningún comprobante ya emitido —las recepciones
    # guardan su propia foto del flete y los comprobantes su renglón—; le cambia lo
    # que va a valer de aquí en adelante, y lo que RECALCULAR vuelva a derivar.
    modo_transporte: ModoDeTransporte | None = None
    estado: str | None = None
    # OJO con los tres estados de este campo, porque el PUT es parcial:
    #   · no viene         → no se toca ninguna ruta (el diálogo que solo cambió
    #                        el teléfono no le puede borrar las tarifas);
    #   · viene []         → se le quitan TODAS las rutas (queda con la general);
    #   · viene con filas  → esas quedan y las demás se van.
    # `None` y "no viene" caen en el mismo caso a propósito: no hay ninguna
    # intención razonable detrás de mandar `"rutas": null` distinta de "no la
    # toques", y confundirla con [] sería borrarle tarifas sin que nadie lo pida.
    rutas: list[TransportadorRutaIn] | None = None


class TransportadorRead(TenantRead):
    nombre: str
    documento: str | None
    telefono: str | None
    valor_transporte: Decimal
    modo_transporte: ModoDeTransporte = MODO_POR_LITRO
    rutas: list[TransportadorRutaRead] = []

    @model_validator(mode="before")
    @classmethod
    def _sin_las_rutas_de_otra_empresa(cls, datos: Any) -> Any:
        """Esconde de la respuesta las filas cuya ruta es de OTRA empresa.

        LA OTRA MITAD DEL HUECO DE AISLAMIENTO. La escritura valida la ruta contra
        el repositorio de la empresa, así que por el API no entra una ajena; pero el
        backfill de la migración c6b1e4a8d3f7 sí podía dejarla (el endpoint viejo
        aceptaba cualquier `ruta_id` sin mirar la empresa), y `TransportadorRuta.ruta`
        es un relationship por llave primaria que la carga igual. Resultado: la
        Quesera A veía el nombre y la tarifa de una ruta de la Quesera B como si
        fueran de ella.

        SE ESCONDE LA FILA COMPLETA, no solo el nombre. Una fila cruzada no es una
        ruta de este transportador en esta empresa: es basura, y no puede cobrar ni
        un peso —el flete sale de la ruta de la recepción, que siempre es de la
        empresa, así que esa fila nunca la escoge `tarifa_por_litro`—. Mostrarla sin
        nombre solo pondría un renglón "— $999,99" que el dueño no puede ni
        entender ni arreglar. Y como no sale en la lectura, el primer PUT que la
        pantalla haga con lo que leyó la borra: la basura se limpia sola.

        La auditoría sí la sigue viendo (`TransportadorService._foto` va contra el
        modelo, no contra este schema): lo que quedó guardado se registra tal cual,
        aunque no se muestre.
        """
        rutas = getattr(datos, "rutas", None)
        if rutas is None:
            # Ya viene como dict (o sin rutas cargadas): no hay nada que filtrar.
            return datos
        empresa_id = getattr(datos, "empresa_id", None)
        propias = [fila for fila in rutas if fila.es_de_la_empresa(empresa_id)]
        if len(propias) == len(rutas):
            return datos
        # Se copia campo por campo desde `model_fields` y no a mano: si mañana
        # `TenantRead` gana una columna, esta rama no se queda sin ella.
        campos = {nombre: getattr(datos, nombre) for nombre in cls.model_fields}
        campos["rutas"] = propias
        return campos
