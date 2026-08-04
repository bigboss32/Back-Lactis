"""LA ÚNICA cuenta de qué tarifa por litro le aplica a un día de recepción.

Está en un módulo aparte y no dentro del servicio de transportadores por dos
razones prácticas:

  · lo usan DOS hojas del sistema —recepción (que calcula y guarda el flete de
    cada día) y liquidaciones (que arma el comprobante del transportador)—, y
    hasta ahora la fórmula estaba repetida en cuatro sitios: uno en recepción y
    tres en liquidaciones. Cuatro copias de una cuenta de plata es cuatro
    oportunidades de que se desincronicen, y el día que se desincronizan el
    desglose del comprobante deja de sumar el total;
  · este módulo NO importa nada de recepción ni de liquidaciones, solo el modelo
    de transportadores, así que cualquiera de las dos lo puede importar sin
    armar un ciclo de imports.

No toca la base de datos: recibe el transportador YA CARGADO. Sus rutas vienen
con `lazy="selectin"`, así que están ahí sin una consulta por llamada.
"""
import uuid
from decimal import Decimal

from app.modules.transportadores.models import Transportador

CERO = Decimal("0")


def tarifa_por_litro(
    transportador: Transportador | None, ruta_id: uuid.UUID | None
) -> Decimal:
    """Cuánto cobra por litro `transportador` recogiendo leche en `ruta_id`.

    La regla, en el orden en que se mira:

      1. si esa ruta le tiene tarifa propia, MANDA esa (es lo que pidió el dueño:
         Alex hace Nápoles a $242,76 y Mira Valle a $300, y el mismo día puede
         hacer las dos);
      2. si no —porque la recepción quedó sin ruta, o porque a esa ruta no se le
         puso tarifa— la TARIFA GENERAL del transportador;
      3. sin transportador no hay flete que cobrar: cero.

    Devuelve siempre Decimal, nunca float: esto se multiplica por los litros y el
    resultado se guarda como plata.
    """
    if transportador is None:
        return CERO
    general = Decimal(transportador.valor_transporte or 0)
    if ruta_id is None:
        return general
    for fila in transportador.rutas:
        # Una fila cuya ruta es de OTRA empresa no puede fijar plata en esta, ni
        # aunque el id coincidiera. Hoy no puede coincidir —la ruta de la recepción
        # siempre es de la empresa—, así que esto es un cinturón de seguridad y no
        # una corrección; pero es el mismo cinturón que la lectura, y el día que
        # alguien plante una fila cruzada la cuenta cae en la tarifa general en vez
        # de en una tarifa ajena. No cuesta consulta: `fila.ruta` viene con la
        # colección (lazy="selectin").
        if not fila.es_de_la_empresa(transportador.empresa_id):
            continue
        if fila.ruta_id == ruta_id:
            # Se lee tal cual está guardada. Un cero acá es un cero puesto a mano
            # (alguien decidió que en esa ruta no se cobra flete) y se respeta;
            # caer a la general "porque parece vacío" sería adivinar.
            return Decimal(fila.valor_transporte or 0)
    return general
