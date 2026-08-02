"""Nombres de terceros escritos a mano: cómo se comparan y cómo se unifican.

Esto nació en reventa, para los productores y los clientes, y se subió aquí sin
cambiarle ni una coma cuando el flete por tramos necesitó exactamente lo mismo
para el nombre del CONDUCTOR. Es una sola implementación a propósito: si el
conductor se agrupara con una regla distinta a la del productor, el mismo señor
escrito igual quedaría unificado en una pantalla y partido en dos en la otra.

La regla es la mínima que resuelve el problema real del campo: el dueño escribe
"JOSE LAVADO" hoy y "Jose lavado" mañana, y a veces le mete un espacio de más.
NO se quitan acentos ni se corrigen apellidos: "Munoz" y "Muñoz" son dos
escrituras distintas y unificarlas sería el sistema adivinando.
"""


def clave_de_tercero(nombre: str | None) -> str:
    """La clave con la que se decide que dos escrituras son el mismo tercero:
    sin mayúsculas y sin espacios de sobra.

    Se calcula EN PYTHON a propósito: el lower() de SQLite solo baja letras
    ASCII (deja la Á como Á) y el de Postgres sí baja acentos, así que agrupar
    en SQL daría resultados distintos según la base — uno en las pruebas y otro
    en producción. En Python el resultado es el mismo en las dos.
    """
    return " ".join((nombre or "").split()).lower()


def canonizar_nombre(nombre: str, ya_usados: list[str]) -> str:
    """Si el nombre ya está registrado escrito de otra forma (mayúsculas o
    espacios de sobra), devuelve la escritura que YA está guardada.

    Así "sebastián ruiz" no se vuelve un segundo productor y no se parten sus
    kilos, su saldo ni su puesto en el ranking; y "jose lavado" no se vuelve un
    segundo conductor al que se le deba plata por aparte.

    Si no coincide con ninguno, devuelve el nombre con los espacios normalizados:
    es la primera vez que se ve y esa pasa a ser la escritura buena.
    """
    limpio = " ".join(nombre.split())
    clave = clave_de_tercero(limpio)
    for usado in ya_usados:
        if clave_de_tercero(usado) == clave:
            return usado
    return limpio


def unir_nombres(*listas: list[str]) -> list[str]:
    """Une listas de nombres de terceros sin repetir el mismo escrito de otra
    forma. Gana la escritura de la PRIMERA lista, que es la del sistema: es la
    que agrupa el ranking y el estado de cuenta.

    El resultado va ordenado por la misma clave con la que se comparan, para que
    el autocompletado salga alfabético y no en dos bloques pegados.
    """
    unicos: dict[str, str] = {}
    for lista in listas:
        for nombre in lista:
            if nombre:
                unicos.setdefault(clave_de_tercero(nombre), nombre)
    return [unicos[clave] for clave in sorted(unicos)]
