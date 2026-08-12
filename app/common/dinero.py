"""Repartos de plata que TIENEN QUE SUMAR EXACTO la cifra grande.

POR QUÉ ESTO VIVE EN `app/common/` Y NO EN UN MÓDULO. Porque la regla de la casa
—todo desglose suma exacto el encabezado, un centavo de diferencia es un defecto— no
es de las liquidaciones ni de la reventa: es del negocio. El dueño revisa las columnas
a mano con calculadora, y no le importa de qué pantalla salió el papel.

Cuando cada módulo llevaba su propia versión del reparto, dos pantallas repartían los
mismos centavos de dos formas distintas. Aquí hay UNA implementación, con UNA prueba,
y los dos módulos la llaman.
"""
from decimal import ROUND_DOWN, Decimal
from typing import Any

CERO = Decimal("0")
CENTAVOS = Decimal("0.01")


def repartir_al_resto_mayor(
    exactos: list[tuple[Any, Decimal]], total: Decimal
) -> dict[Any, Decimal]:
    """Reparte `total` en centavos entre unas partes cuyo valor EXACTO ya se conoce.

    Es el reparto por RESTO MAYOR: a cada parte se le da su valor truncado al
    centavo y los centavos que faltan para llegar a `total` se entregan de a uno,
    empezando por la parte cuya fracción de centavo quedó más grande. Así ninguna
    parte se desvía más de un centavo de lo que le corresponde y la suma da EXACTO
    la cifra grande, que es la regla de la casa.

    Se prefiere a "que el último de la lista se lleve el residuo" porque las partes
    son PLATA YA CALCULADA de algo concreto —un día, un productor, un producto—:
    cargarle dos o tres centavos al último de la lista deja su foto sin poderse
    reproducir con su propia multiplicación, y el dueño también revisa fila por fila.
    Con el resto mayor cada fila queda a lo sumo un centavo de su cuenta.

    El desempate (fracción igual) va por el valor más grande y después por la clave,
    para que el mismo comprobante generado dos veces reparta los centavos igual: si
    dependiera del orden en que la base devolvió las filas, el papel podría salir
    distinto en cada impresión.

    OJO CON LAS CLAVES: tienen que ser comparables como texto (`str(clave)`) y
    distintas entre sí. Si dos partes traen la misma clave, la segunda le pisa el
    valor a la primera —es un diccionario— y el reparto pierde una fila.
    """
    if not exactos:
        return {}
    pisos = {clave: exacto.quantize(CENTAVOS, rounding=ROUND_DOWN) for clave, exacto in exactos}
    faltan = int((total - sum(pisos.values())) / CENTAVOS)
    orden = sorted(
        exactos,
        key=lambda par: (-(par[1] - pisos[par[0]]), -par[1], str(par[0])),
    )
    asignado = dict(pisos)
    for clave, _ in orden[: max(faltan, 0)]:
        asignado[clave] += CENTAVOS
    # Cinturón: si por lo que sea la cuenta de centavos no cerró (una parte con
    # valor negativo, un total que no viene de estas mismas partes), el residuo se
    # le carga a la parte más grande. La suma tiene que dar `total` SIEMPRE; es la
    # única cosa que no se negocia.
    residuo = total - sum(asignado.values())
    if residuo != CERO:
        asignado[max(exactos, key=lambda par: par[1])[0]] += residuo
    return asignado
