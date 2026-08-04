import uuid
from decimal import Decimal
from typing import Any

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema, TenantRead

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
    """El `Field` de una tarifa por litro. Una sola definición para las dos."""
    return Field(ge=0, max_digits=12, decimal_places=2, **extra)


class TransportadorRutaIn(BaseSchema):
    """Una ruta que hace el transportador, con la tarifa por litro que cobra en ella."""

    ruta_id: uuid.UUID
    valor_transporte: Decimal = tarifa_por_litro()


class TransportadorRutaRead(BaseSchema):
    """Lo mismo, pero CON EL NOMBRE de la ruta.

    Se manda el nombre y no solo el id para que la pantalla del transportador
    muestre "Nápoles — $242,76" sin tener que ir a pedir el catálogo de rutas
    aparte. Sale de la property `nombre` de TransportadorRuta.
    """

    ruta_id: uuid.UUID
    nombre: str | None = None
    valor_transporte: Decimal
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
    rutas: list[TransportadorRutaIn] | None = None


class TransportadorUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    documento: str | None = None
    telefono: str | None = None
    valor_transporte: Decimal | None = tarifa_por_litro(default=None)
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
