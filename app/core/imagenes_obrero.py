"""EL OBRERO: el proceso aparte donde se decodifican las fotos, con techo.

POR QUÉ EXISTE ESTE ARCHIVO, y es un cambio de estrategia, no un parche más.

Durante tres rondas la defensa fue PREDECIR cuánta memoria iba a costar cada
formato antes de abrirlo (el "presupuesto" de `imagenes.py`). Y en cada ronda
apareció uno que no encajaba en la cuenta:

  · primero el tope por MEGABYTES del archivo — un WEBP de 3.116 bytes pedía
    1.226 MB, porque el peso del archivo no dice nada de lo que ocupa abierto;
  · después el WEBP SIN PÉRDIDA — libwebp arma su propio búfer entero aparte, y
    costaba el doble por megapíxel que cualquier otro formato;
  · y ahora el JPEG PROGRESIVO — `Image.draft` reduce la SALIDA de libjpeg pero
    NO el arreglo de coeficientes, que en un progresivo se arma COMPLETO antes
    del primer píxel. El presupuesto miraba los 5 megapíxeles que salían y cobraba
    50 MB cuando lo medido eran 660,6 MB. Doce veces por debajo.

EL PATRÓN ES EL PROBLEMA, NO CADA CASO. La lista de formatos y de modos de
codificación NO LA CONTROLAMOS NOSOTROS: la ponen libjpeg, libwebp, libheif y la
que se instale mañana. Cada vez que se agrega una fila a la tabla del presupuesto
se está apostando a que ya se conocen todos los modos que existen, y esa apuesta
se perdió tres veces seguidas.

ASÍ QUE SE DEJA DE ADIVINAR. La decodificación se saca del proceso que atiende a
las dos queseras y se hace ACÁ, en un proceso aparte al que el SISTEMA OPERATIVO
le pone un techo de memoria. Una imagen que se pase NO TUMBA NADA: mata a este
obrero, que es de usar y tirar, y la API contesta un 422 que se entiende. No hay
que haber predicho su costo — no hay que saber siquiera qué formato era.

LAS DOS MITADES DEL TECHO, Y SON DISTINTAS:

  1. AISLAR (funciona en Linux Y en Windows). La memoria que pide el
     decodificador se pide EN ESTE PROCESO, no en el uvicorn. Aunque el techo de
     abajo no estuviera, el proceso que atiende a las dos queseras ya no crece:
     lo que se descontrola se descontrola aparte.
  2. EL TOPE DURO (`RLIMIT_AS`, solo Linux, que es donde corre producción). Le
     dice al núcleo "este proceso no puede tener más de tanto espacio pedido". El
     malloc que se pase devuelve NULL, libjpeg aborta su propia decodificación y
     acá sale un error normal; y si el que se queda sin memoria es Python, el
     proceso se muere entero — que también está bien, porque el que se muere es
     ESTE y no la API.

EN WINDOWS NO HAY `setrlimit` (el módulo `resource` no existe) y por lo tanto no
hay tope duro: queda la mitad 1, el aislamiento. Windows es el portátil de
desarrollo y las pruebas; producción es Linux. Eso está dicho en voz alta, se
escribe en el log al arrancar el obrero y se puede consultar con
`imagenes.como_esta_el_techo()`, para que nadie crea que el portátil está
probando algo que producción no tiene.

CÓMO SE HABLA CON ÉL. Por la entrada y la salida estándar, con un marco de
longitud adelante y NADA de pickle. Los mensajes son tres cosas simples —los
bytes de la foto, el tipo y el nombre— y devolver los bytes ya comprimidos; para
eso no hace falta serializar objetos de Python, y no serializarlos quita de en
medio toda una familia de problemas. El formato está escrito en `_leer_marco`.

POR QUÉ SE ARRANCA CON `python -m` Y NO CON `multiprocessing`. Con
`multiprocessing` en modo "spawn" el hijo REIMPORTA el módulo `__main__` del
padre, y acá el `__main__` del padre es el guion de arranque de uvicorn (ver
`start.sh`: `exec uvicorn app.main:app`). Eso funciona de casualidad —el guion
trae su `if __name__ == "__main__"`—, pero es una casualidad que depende de cómo
lo empaquete pip, y esto corre en el servidor de un cliente de verdad. Con
`python -m app.core.imagenes_obrero` se importa EXACTAMENTE este archivo y nada
más: no hay magia que revisar cuando algo salga raro.

Y POR QUÉ NO SE HACE `fork`. Es lo más barato en Linux, pero uvicorn atiende con
hasta 40 hilos y `fork` sobre un proceso con hilos clona SOLO el hilo que llama:
si otro hilo venía con un candado tomado (el de `malloc`, el del log), el hijo
nace con ese candado tomado para siempre y se cuelga. Python 3.12 ya avisa de eso.
Como el obrero SE REUTILIZA —el arranque se paga una vez, no una vez por foto—,
lo barato de `fork` no compra nada y sí traería ese riesgo.
"""
from __future__ import annotations

import os
import struct
import sys

# El marco: 4 bytes con la longitud (big-endian, sin signo) y detrás el bloque.
# Se eligió `>I` y no `>Q` porque ningún soporte pasa de 15 MB (`ADJUNTOS_MAX_MB`)
# y 4.294 millones de bytes es tope de sobra; el marco corto se lee de un tirón.
_LARGO = struct.Struct(">I")

# Las respuestas llevan una letra adelante que dice qué pasó:
#   b"O" — salió bien: detrás van los bytes comprimidos y el content_type.
#   b"M" — mal, pero de forma controlada: detrás va el mensaje para el dueño.
# Lo que NO tiene letra es que el obrero se murió, y eso lo ve el padre porque la
# tubería se cierra: ese es justo el caso del techo, y por eso no hace falta que
# el obrero sepa avisar de su propia muerte.
RESPUESTA_BIEN = b"O"
RESPUESTA_MAL = b"M"

# Lo primero que dice un obrero que arrancó bien, antes de que le manden nada.
# Detrás va la frase de qué techo le quedó puesto, para que el padre la pueda
# escribir en el log del servidor sin tener que adivinarla.
SALUDO = b"LISTO:"


def _leer_exacto(flujo, cuantos: int) -> bytes | None:
    """Exactamente `cuantos` bytes, o None si la tubería se cerró antes.

    `read` de una tubería puede devolver MENOS de lo que se le pide sin que haya
    pasado nada malo (llegó solo un pedazo del mensaje), así que se insiste hasta
    completar. Leer de menos acá sería partir una foto por la mitad.
    """
    pedazos = []
    faltan = cuantos
    while faltan > 0:
        pedazo = flujo.read(faltan)
        if not pedazo:
            return None
        pedazos.append(pedazo)
        faltan -= len(pedazo)
    return b"".join(pedazos)


def _leer_marco(flujo) -> bytes | None:
    """Un bloque con su longitud adelante, o None si se acabó la conversación."""
    cabeza = _leer_exacto(flujo, _LARGO.size)
    if cabeza is None:
        return None
    return _leer_exacto(flujo, _LARGO.unpack(cabeza)[0])


def _escribir_marco(flujo, bloque: bytes) -> None:
    flujo.write(_LARGO.pack(len(bloque)))
    flujo.write(bloque)


def _espacio_ya_tomado() -> int | None:
    """Cuánto espacio de direcciones tiene YA pedido este proceso, en bytes.

    HACE FALTA PARA PODER PONER EL TECHO, y es la parte que no es obvia:
    `RLIMIT_AS` no limita "lo que falta por pedir" sino EL TOTAL del proceso. Un
    obrero recién arrancado ya trae Python, Pillow y sus librerías en C mapeadas,
    así que ponerle un tope de 256 MB pelados lo mataría antes de la primera foto.
    El tope que se le pone es "lo que ya tiene" MÁS el presupuesto.

    Se lee de `/proc/self/statm`, que en Linux es un archivo de texto con el
    tamaño en PÁGINAS. Si no se puede leer, se devuelve None y el obrero se queda
    SIN techo duro pero avisando: es preferible a inventarse el número y matar al
    obrero en cada foto legítima.
    """
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as archivo:
            paginas = int(archivo.read().split()[0])
        return paginas * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _cabe_pedir(cuantos_mb: int) -> bool:
    """¿Este proceso todavía puede pedir tantos MB sin que lo maten?

    SE PREGUNTA RESERVANDO ESPACIO DE DIRECCIONES Y NO MEMORIA DE VERDAD, que es
    la diferencia que hace que esto sea barato: `mmap` de un bloque anónimo pide
    las direcciones —que es EXACTAMENTE lo que `RLIMIT_AS` limita— sin tocar una
    sola página, así que no se gasta ni un byte de memoria física ni un
    milisegundo en llenarla. Un `bytearray` del mismo tamaño sí la llenaría de
    ceros y costaría los 256 MB de verdad.
    """
    import mmap

    try:
        reserva = mmap.mmap(-1, cuantos_mb * 1024 * 1024)
    except (OSError, ValueError, MemoryError):
        return False
    reserva.close()
    return True


def poner_el_techo(presupuesto_mb: int) -> str:
    """Le pide al sistema operativo el tope de memoria. Devuelve qué quedó puesto.

    Devuelve una frase para el log —no un booleano— porque las respuestas
    posibles ("quedó en tanto", "acá no hay setrlimit", "no se pudo leer cuánto
    tenía") significan cosas distintas para el que esté mirando por qué una foto
    rebotó, y un True/False las volvería la misma.

    NO SE TOCA EL TOPE DURO DEL SISTEMA. `setrlimit` recibe un tope blando y uno
    duro; acá solo se baja el blando y el duro se deja como estaba. Si el
    contenedor ya venía con un tope propio más apretado, ese manda: se toma el
    menor de los dos, porque subir un tope que puso el que administra el servidor
    no le corresponde a esto.

    Y DESPUÉS SE COMPRUEBA QUE EL TECHO QUEDÓ USABLE, que es la parte que no
    sobra. El tope se calcula sobre `/proc/self/statm`, y si por lo que sea ese
    número saliera corto, el obrero quedaría con un techo tan apretado que
    moriría con LA PRIMERA FOTO — y entonces NINGÚN soporte se podría subir. Para
    un sistema que está en producción con un cliente de verdad, ese fallo sería
    peor que el problema que este archivo vino a resolver: cambiar "se cae con
    una foto rara" por "no se puede trabajar" no es un arreglo.
    """
    try:
        import resource
    except ImportError:
        # Windows: el módulo no existe. Queda el aislamiento, que no es poco.
        return "sin tope duro (este sistema no tiene setrlimit); solo aislamiento"

    tomado = _espacio_ya_tomado()
    if tomado is None:
        return "sin tope duro (no se pudo leer /proc/self/statm); solo aislamiento"

    try:
        _blando_antes, duro = resource.getrlimit(resource.RLIMIT_AS)
    except (ValueError, OSError) as exc:  # pragma: no cover - núcleo sin RLIMIT_AS
        return f"sin tope duro (no se pudo leer el tope: {exc}); solo aislamiento"

    # SE INTENTA APRETADO Y SE VA AFLOJANDO. Se prueba el techo que se quería; si
    # con él ya no cabría ni una foto legítima, se duplica el aire y se vuelve a
    # probar. Dos intentos y se abandona: más que eso sería un techo tan alto que
    # ya no estaría protegiendo nada, y en ese caso es más honesto decir que no
    # hay techo que dejar uno puesto que no sirve.
    for aire in (presupuesto_mb, presupuesto_mb * 2, presupuesto_mb * 4):
        tope = tomado + aire * 1024 * 1024
        if duro != resource.RLIM_INFINITY:
            tope = min(tope, duro)
        try:
            resource.setrlimit(resource.RLIMIT_AS, (tope, duro))
        except (ValueError, OSError) as exc:
            return f"sin tope duro (setrlimit falló: {exc}); solo aislamiento"
        if _cabe_pedir(presupuesto_mb):
            return (
                f"tope duro en {tope // 1024 // 1024} MB de espacio de direcciones "
                f"({tomado // 1024 // 1024} MB que ya tenía + {aire} MB de aire), "
                f"comprobado pidiendo {presupuesto_mb} MB"
            )

    # Ninguno sirvió: se suelta el tope antes que dejar un obrero que no puede
    # trabajar. Queda el aislamiento y queda dicho a gritos en el log.
    try:
        resource.setrlimit(resource.RLIMIT_AS, (duro, duro))
    except (ValueError, OSError):  # pragma: no cover
        pass
    return (
        "sin tope duro (el que se calculó no dejaba pasar ni una foto legítima, "
        "así que se soltó para no dejar al cliente sin poder subir soportes); "
        "solo aislamiento"
    )


def main() -> int:
    """El bucle del obrero: una foto, una respuesta, hasta que cierren la entrada.

    EL ORDEN IMPORTA: primero se importa Pillow y DESPUÉS se pone el techo. Al
    revés, los MB que ocupan sus librerías en C al mapearse saldrían del
    presupuesto de la primera foto y esa foto rebotaría sin razón.

    LA QUE SÍ SALE DEL PRESUPUESTO ES `pillow-heif`, y es a propósito. Se carga
    perezosamente, o sea DESPUÉS del techo, así que sus 27 MB se le descuentan al
    aire de la foto que la necesite. Cabe de sobra —una HEIC de iPhone de 12 MP
    cuesta 89,7 MB y el aire son 320— y así se conserva lo que ya estaba decidido
    en `imagenes.heic_disponible`: el servidor que en toda su vida solo reciba
    JPEG no carga ese decodificador ni una vez.
    """
    from app.core.exceptions import BusinessError
    from app.core.imagenes import (
        PRESUPUESTO_MB_POR_IMAGEN,
        TECHO_EXTRA_MB,
        _comprimir_aqui_mismo,
        _mensaje_demasiados_pixeles,
    )

    # Pillow ya cargada ANTES del techo (ver el docstring): que sus librerías en C
    # no le coman el presupuesto a la primera foto.
    from PIL import Image, ImageOps  # noqa: F401

    entrada, salida = sys.stdin.buffer, sys.stdout.buffer
    if os.name == "nt":  # pragma: no cover - solo el portátil de desarrollo
        # En Windows los descriptores 0 y 1 pueden venir en modo TEXTO, y en modo
        # texto un 0x0A de los bytes de una foto se convertiría en 0x0D 0x0A: la
        # imagen llegaría corrompida y de forma intermitente, que es la peor clase
        # de error. Se fuerzan a binario.
        import msvcrt

        msvcrt.setmode(entrada.fileno(), os.O_BINARY)
        msvcrt.setmode(salida.fileno(), os.O_BINARY)

    estado = poner_el_techo(PRESUPUESTO_MB_POR_IMAGEN + TECHO_EXTRA_MB)
    # Al log del obrero, que es la salida de ERRORES: la salida normal es la
    # tubería por donde viajan las fotos y meterle una línea de texto la partiría.
    print(f"[obrero de imagenes] {estado}", file=sys.stderr, flush=True)

    # EL SALUDO, y es lo que distingue las dos muertes que NO se parecen en nada:
    #
    #   · un obrero que SALUDÓ y después se muere → lo mató el techo, o sea que el
    #     sistema hizo su trabajo: al dueño le sale un 422 que le dice qué mandar.
    #   · un obrero que NUNCA SALUDÓ → no arrancó nunca (falta una librería, el
    #     PYTHONPATH quedó mal, no hay permiso para crear procesos). Eso NO es una
    #     imagen mala: es este mecanismo que no sirve en esta máquina, y entonces
    #     hay que decodificar en el proceso de siempre.
    #
    # Sin este saludo las dos se veían igual desde el padre —la tubería cerrada—,
    # y un despliegue con el PYTHONPATH torcido le habría contestado a TODAS las
    # fotos «es demasiado grande». El cliente no habría podido subir un solo
    # soporte, y el mensaje ni siquiera le habría dicho la verdad.
    _escribir_marco(salida, SALUDO + estado.encode("utf-8"))
    salida.flush()

    while True:
        contenido = _leer_marco(entrada)
        if contenido is None:
            return 0  # cerraron la entrada: es la forma normal de despedirse
        tipo_crudo = _leer_marco(entrada)
        nombre_crudo = _leer_marco(entrada)
        if tipo_crudo is None or nombre_crudo is None:
            return 0
        tipo = tipo_crudo.decode("utf-8", "replace")
        nombre = nombre_crudo.decode("utf-8", "replace") or None

        try:
            datos, tipo_final = _comprimir_aqui_mismo(contenido, tipo, nombre=nombre)
        except BusinessError as exc:
            salida.write(RESPUESTA_MAL)
            _escribir_marco(salida, str(exc).encode("utf-8"))
        except MemoryError:
            # EL TECHO DISPARANDO POR LAS BUENAS. Cuando `RLIMIT_AS` corta, el
            # `malloc` de adentro devuelve nada y eso sube hasta acá como un
            # MemoryError — la muerte LIMPIA, la que alcanza a contestar. (La otra
            # es que al núcleo se le acabe la paciencia y mate el proceso sin
            # avisar; esa la ve el padre porque la tubería se cierra.)
            #
            # LE SALE EL MENSAJE DE "DEMASIADO GRANDE" Y NO EL DE "ESTÁ DAÑADA",
            # porque es la verdad y porque son consejos distintos: al que mandó
            # una foto enorme hay que decirle que mande una más chica, no que
            # vuelva a tomarla.
            salida.write(RESPUESTA_MAL)
            _escribir_marco(
                salida, _mensaje_demasiados_pixeles(nombre, None).encode("utf-8")
            )
            salida.flush()
            # Y SE RETIRA. Este proceso acaba de tocar su techo: aunque lo que
            # falló ya se liberó, seguir trabajando en un proceso que quedó al
            # borde es pedir que la foto siguiente —una buena— muera por culpa de
            # la anterior. El padre ve la tubería cerrada y hace uno nuevo, que
            # cuesta medio segundo y solo lo paga quien mandó la bomba.
            return 0
        except Exception as exc:  # noqa: BLE001
            # Cualquier otra cosa se manda como un "mal" controlado en vez de
            # dejar morir al obrero: matarlo por un archivo raro costaría medio
            # segundo de arranque en la foto siguiente, y este camino ya devuelve
            # el mensaje que el dueño necesita leer.
            salida.write(RESPUESTA_MAL)
            _escribir_marco(salida, f"__inesperado__{type(exc).__name__}: {exc}".encode("utf-8"))
        else:
            salida.write(RESPUESTA_BIEN)
            _escribir_marco(salida, datos)
            _escribir_marco(salida, tipo_final.encode("utf-8"))
        salida.flush()


if __name__ == "__main__":
    sys.exit(main())
