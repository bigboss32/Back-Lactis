"""Lo que se le hace a un SOPORTE DE PAGO antes de guardarlo en el bucket.

Dos cosas, y las dos las comparten los adjuntos de reventa y los soportes de un
pago de liquidación. Viven acá, en un módulo hermano de `storage.py`, porque
escribirlas dos veces es como terminan aceptando cosas distintas: el día que se
agregue un formato o se cambie un tope, se cambia en un solo sitio.

1. RECONOCER QUÉ ES EL ARCHIVO DE VERDAD (`detectar_tipo`), mirándole los
   primeros bytes y no la extensión ni el Content-Type que manda el navegador.

2. BAJARLE EL PESO (`comprimir_soporte`). Lo pidió el dueño con estas palabras:
   "de paso también le reducimos la calidad para ahorrar espacio". Una foto de
   comprobante tomada con un celular pesa entre 3 y 5 MB y el bucket se paga por
   lo que ocupa; con esto queda en unos 300 KB.

LO PRIMERO QUE PASA CON UNA IMAGEN ES UN PRESUPUESTO DE MEMORIA, y se mira ANTES
de decodificarla. El tope de megabytes no protege de nada acá: mide el archivo
comprimido, y un WEBP de 3.116 BYTES pedía 1.226 MB al abrirlo. Las dos queseras
comparten servidor y un solo proceso. Son TRES palancas —decodificar sin copias
de más, un tope graduado por lo que cuesta cada formato, y una fila para que
varias subidas no se multipliquen—, y el porqué completo de las tres, con las
cifras medidas, está en el bloque "EL PRESUPUESTO DE MEMORIA" de más abajo.
LÉELO ANTES DE SUBIR NINGÚN TOPE: ahí está escrito lo que se gasta.

Y HAY UNA CUARTA QUE NO SE PARECE A LAS OTRAS TRES, porque no es una palanca sino
un CAMBIO DE ESTRATEGIA. Las tres de arriba PREDICEN cuánta memoria va a costar
cada formato, y esa predicción se equivocó tres veces seguidas: primero el tope
por megabytes, después el WEBP sin pérdida, y después un JPEG PROGRESIVO de 2 KB
que pedía 660,6 MB y respondía 201 (`Image.draft` reduce la salida de libjpeg
pero NO el arreglo de coeficientes, que en un progresivo se arma completo antes
del primer píxel). La lista de formatos y de modos de codificación no la
controlamos nosotros, así que se dejó de adivinar: LA DECODIFICACIÓN SE HACE EN
OTRO PROCESO, al que el sistema operativo le pone un techo que sí hace cumplir
(`app/core/imagenes_obrero.py`). Una imagen que se pase se muere ELLA SOLA y la
API contesta un 422 legible, sin que nadie haya tenido que prever su formato.

EL PRESUPUESTO NO SE QUITÓ AL PONER EL TECHO, y saber por qué es la mitad de
entender este archivo: LE CAMBIÓ EL PAPEL. Antes era la defensa, y por eso cada
formato que no encajaba en su cuenta era un agujero. Ahora es el FILTRO RÁPIDO:
rebota gratis lo que ya sabe reconocer, en medio milisegundo y sin gastar un
proceso, para que una lluvia de imágenes imposibles no ocupe la fila de las
buenas. El que defiende es el techo.

LOS DOS NÚMEROS DE LA COMPRESIÓN Y POR QUÉ ESOS. 1600 píxeles de lado mayor y
calidad 75 en JPEG. UN COMPROBANTE ES EVIDENCIA: tiene que seguir leyéndose el
monto, la cuenta y el número de referencia. Apretar más —1000 px, calidad 50—
ahorra unos KB más y DESTRUYE LA PRUEBA: los dígitos chiquitos de una referencia
bancaria se vuelven manchas y el soporte deja de servir para lo único que sirve.
Con 1600 px una foto de 12 MP conserva de sobra el texto de una pantalla de banco,
y a calidad 75 el JPEG no muestra los cuadritos que delatan una imagen apretada.

LO QUE NO SE TOCA:

  · LOS PDF PASAN DERECHO. No son imágenes: el comprobante que descarga el banco
    ya viene liviano y re-armarlo sería convertir texto nítido en una foto de
    texto. Es el mejor soporte que hay y se guarda tal como llegó.
  · LOS ARCHIVOS QUE YA ESTÁN SUBIDOS se quedan como están. Esto corre solo en el
    camino de subida; no hay ninguna pasada que vuelva a comprimir lo viejo.
  · UNA IMAGEN QUE YA ESTÁ CHICA no se agranda ni se re-comprime por deporte (ver
    `AHORRO_MINIMO_*`): pasar dos veces un JPEG por el compresor le quita calidad
    una segunda vez, y hacerlo para ahorrar tres kilobytes es un mal negocio
    cuando lo que se está guardando es la prueba de que se pagó. PERO ESE ATAJO
    NO SE TOMA en tres casos, porque en los tres el original miente: si la foto
    trae el GPS pegado, si trae la etiqueta de giro sin aplicar, o si es una HEIC
    (que el navegador del productor no dibuja). Los tres están explicados en
    `comprimir_soporte`.

PILLOW SE IMPORTA PEREZOSAMENTE, igual que boto3 en `storage.py`: DENTRO de la
función, nunca al importar el módulo. Y si no está instalado, la imagen pasa SIN
COMPRIMIR con un aviso en el log, en vez de rebotarle la subida al dueño. La
diferencia con boto3 es de fondo: sin boto3 no hay dónde guardar el archivo y el
adjunto es imposible; sin Pillow el adjunto se guarda perfectamente, solo que
pesado. Tumbar una función que el cliente ya usa —pegarle la foto al pago— por
una librería que solo sirve para ahorrar espacio sería cambiar un problema de
plata por uno de trabajo.

PERO OJO CON HASTA DÓNDE LLEGA ESA RED, porque acá antes decía de más. Lo que
sigue funcionando sin Pillow es ESTE MÓDULO, no la aplicación: Lactis NO ARRANCA
sin Pillow, y no es por acá. `app/utils/export.py` importa reportlab al cargarse,
reportlab hace `from PIL import Image` en su propio `lib/utils.py` sin ninguna
red, y de ese módulo cuelgan las liquidaciones, la reventa, transporte, ventas y
las notificaciones. O sea que Pillow NO ES OPCIONAL en este proyecto —es lo que
imprime cada liquidación en PDF— y por eso siempre estuvo instalada aunque no
apareciera en requirements.txt. La pereza de acá abajo sigue valiendo por dos
razones concretas y ninguna es "la aplicación arranca igual": deja probar este
módulo solo, y evita que el día que reportlab suelte a Pillow la subida de
soportes se caiga con ella. Está comprobado, en los dos sentidos, en
`tests/test_compresion_soportes.py`.

LAS FOTOS DE iPHONE (HEIC) SÍ ENTRAN, y SIEMPRE salen guardadas como JPEG: Pillow
sola no abre ese formato, pero `pillow-heif` —declarada en requirements.txt— le
enseña. El dueño fotografía las transferencias con el celular, así que rechazarlas
no era aceptable. El "siempre" es literal y hubo que escribirlo aparte: una HEIC
ya recortada pesa menos de 40 KB, se colaba por el atajo de "no vale la pena
re-comprimir" y se guardaba como `image/heic`, o sea un enlace que el navegador
del productor NO DIBUJA. Ver `heic_disponible` y `comprimir_soporte`.
"""
from __future__ import annotations

import io
import os
import sys
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.exceptions import BusinessError
from app.core.logging_config import get_logger

logger = get_logger("imagenes")

# --------------------------------------------------------------- qué se acepta
# SE ACEPTA PDF, y es una decisión, no un descuido: los bancos colombianos
# (Bancolombia, Nequi, Davivienda) entregan el comprobante de una transferencia
# como PDF descargable, y ese PDF ES el soporte bueno — más que una foto de la
# pantalla. Rechazarlo obligaría al dueño a tomarle una foto al comprobante que
# ya tenía, que es peor soporte y trabajo de más.
#
# HEIC/HEIF ESTÁN Y AHORA SÍ FUNCIONAN. Es el formato con el que graba un iPhone
# de fábrica, y el dueño fotografía las transferencias con el celular: es la foto
# de todos los días, no un caso raro. Con `pillow-heif` instalada (requirements.txt)
# entran, se comprimen igual que las demás y salen guardadas como JPEG. Si algún
# día la librería faltara siguen entrando a propósito, para que el archivo llegue
# hasta el compresor y el dueño reciba un mensaje que le dice QUÉ HACER con su
# foto, en vez del "tipo no permitido" seco que saldría si se rechazaran acá
# arriba.
#
# No entran videos ni ofimática: esto es el respaldo de que se pagó, no un
# archivador. Cada tipo que se abre es un tipo más que hay que servir con un
# enlace firmado, y un .html o un .svg firmados serían código ejecutándose en el
# navegador de quien reciba el enlace.
TIPOS_SOPORTE_PERMITIDOS: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
}

TIPOS_EN_CRISTIANO = "fotos JPG, PNG, WEBP o HEIC, y comprobantes en PDF"

# Marcas HEIF/HEIC: los primeros bytes son el tamaño de la caja, luego 'ftyp' y
# luego la marca. Se listan las que usan las cámaras de los teléfonos.
MARCAS_HEIC = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"hevs"}
MARCAS_HEIF = {b"mif1", b"msf1"}

# ==========================================================================
# EL PRESUPUESTO DE MEMORIA. Léelo entero antes de subir un tope.
# ==========================================================================
# LO QUE SE ESTÁ GASTANDO, EN PLATA Y EN RIESGO. Este servidor corre UN SOLO
# uvicorn (ver `start.sh`, sin `--workers`) y los endpoints son `def`, o sea
# síncronos: FastAPI los manda al threadpool de anyio, hasta 40 A LA VEZ en el
# MISMO proceso y la MISMA memoria. Las dos queseras comparten ese proceso. Lo
# que se lleve la memoria las deja sin ERP a LAS DOS, y el OOM-kill no avisa.
#
# POR QUÉ NO ALCANZA CON EL TOPE DE MEGABYTES. `ADJUNTOS_MAX_MB` mide el archivo
# COMPRIMIDO, y ese número NO DICE NADA de lo que ocupa la imagen abierta. Un
# WEBP sin pérdida de 3.116 BYTES —tres kilobytes— trae 16.383 × 4.882 puntos, y
# abrirlo pedía 1.226 MB. Cuatro a la vez, 4.854 MB. Medido, no estimado.
#
# LA CUENTA QUE HAY QUE HACER, Y QUE ES LA ÚNICA QUE VALE:
#
#     memoria = megapíxeles QUE SE VAN A DECODIFICAR × costo del formato
#               × cuántas subidas caben a la vez
#
# y las tres partes de esa cuenta están abajo, cada una con su número medido.
#
# 1) CUÁNTOS MEGAPÍXELES SE DECODIFICAN, que NO son los que dice el archivo. A un
#    JPEG se le pide a libjpeg que decodifique YA REDUCIDO (`Image.draft`, ver
#    `_pedir_la_imagen_ya_reducida`): como el destino son 1.600 px, una foto de
#    4.032 × 3.024 se decodifica a 2.016 × 1.512 y una de 64 MP a 2.312 × 1.734.
#    Por eso el tope se mira DESPUÉS de pedir la reducción: sobre lo que de
#    verdad va a ocupar, no sobre lo que el archivo dice medir.
#
# 2) LO QUE CUESTA UN MEGAPÍXEL, por formato y MEDIDO en esta máquina con psutil:
#    el PICO de RSS de la tubería entera, en un proceso limpio, dividido por los
#    megapíxeles que se decodificaron. No es el mismo número para todos y por eso
#    hay tabla y no una constante:
#
#      formato / caso medido            decodifica    pico     MB por MP
#      ------------------------------   ----------   -------   ---------
#      JPEG 4.032 × 3.024 (12 MP)         3,05 MP    29,4 MB      9,6
#      JPEG 9.248 × 6.936 (64 MP)         4,01 MP    33,5 MB      8,4
#      PNG  4.032 × 3.024 RGB             12,19 MP   73,2 MB      6,0
#      PNG  4.032 × 3.024 RGBA            12,19 MP   93,9 MB      7,7
#      PNG  8.000 × 6.250 gris plano      50,00 MP   67,3 MB      1,3
#      HEIC 4.032 × 3.024 (iPhone)        12,19 MP   89,7 MB      7,4
#      WEBP 4.000 × 3.000 sin pérdida     12,00 MP  196,8 MB     16,4  ← el peor
#
#    POR QUÉ WEBP CUESTA EL DOBLE QUE LOS DEMÁS: Pillow guarda 4 bytes por punto
#    para todo lo que tenga 3 o 4 bandas, pero libwebp arma ADEMÁS su propio
#    búfer completo antes de entregar la imagen. Ese formato es exactamente con el
#    que se reprodujo el problema, y no es casualidad.
#
#    Los números de la tabla van REDONDEADOS HACIA ARRIBA sobre lo medido, y en
#    cada formato se toma su peor modo (RGBA, no el gris plano). Si algún día se
#    cambia cómo se decodifica, ESTA TABLA HAY QUE VOLVER A MEDIRLA: es lo que
#    sostiene el tope.
COSTO_MB_POR_MEGAPIXEL: dict[str, float] = {
    "JPEG": 10.0,  # con `draft` nunca decodifica más de ~10 MP, mida lo que mida
    "MPO":  10.0,  # el JPEG doble de las cámaras 3D; mismo decodificador
    "PNG":   8.0,
    "HEIF":  8.0,  # la foto del iPhone
    "WEBP": 17.0,  # el peor: libwebp arma su propio búfer entero aparte
}
# Un formato que no esté en la tabla —uno nuevo, o uno que registre otra
# librería— paga el precio del PEOR. Equivocarse por lo alto cuesta que rebote
# una imagen grande; por lo bajo cuesta el proceso.
COSTO_MB_POR_MEGAPIXEL_DESCONOCIDO = 17.0

# 3) EL PRESUPUESTO: lo que UNA imagen puede pedirle prestado al proceso. 256 MB.
#    De dónde sale: la API en reposo pesa unos 44 MB; con 256 MB por imagen y
#    `SUBIDAS_A_LA_VEZ = 3`, lo más que la compresión puede tener pedido al mismo
#    tiempo son 768 MB, y el proceso entero se queda por debajo de 850 MB en el
#    peor imaginable. Antes, sin ninguna de las tres palancas, CUATRO archivos de
#    3 KB pedían 4.854 MB y ocho capturas PNG normales de 12 MP pedían 1.183 MB.
#    SI ALGÚN DÍA HAY QUE SUBIRLO se sube MIRANDO LA MEMORIA DE LA INSTANCIA:
#    este número por `SUBIDAS_A_LA_VEZ` tiene que caber, con aire, en lo que la
#    instancia tenga menos lo que pesa la aplicación. Eso es lo que se está
#    gastando; que quede escrito para el que venga.
PRESUPUESTO_MB_POR_IMAGEN = 256

# LO QUE ESE PRESUPUESTO DEJA PASAR, en megapíxeles y por formato (presupuesto
# dividido por el costo de arriba), y con la foto real al lado:
#
#   · JPEG: 25 MP DECODIFICADOS, que con `draft` no se alcanzan nunca. O sea que
#     una foto de celular pasa mida lo que mida: la de 12 MP, la de 48 MP
#     (8.000 × 6.000) y la de 64 MP (9.248 × 6.936) entran las tres, y cuestan
#     29, 29 y 34 MB. Lo que de verdad las frena son los 15 MB de
#     `ADJUNTOS_MAX_MB`, que es un tope sobre el archivo y no sobre la memoria.
#   · HEIC (iPhone): 32 MP. Entra con holgura la de 12 MP, que es la de todos los
#     días (cuesta 90 MB, un tercio del presupuesto). Las de 48 MP no llegan
#     hasta acá: pesan más de 18 MB y rebotan antes, en `ADJUNTOS_MAX_MB`.
#   · PNG: 32 MP. Una captura de celular son 2 a 12 MP y la de un monitor 5K,
#     14,7 MP: caben todas con el doble de aire.
#   · WEBP: 15 MP. Es el que menos deja pasar porque es el que más cuesta.
#
# Y UN TOPE DURO POR ENCIMA DE TODO, que no depende del formato: `Image.open`
# hace su propia cuenta de bomba de descompresión con `Image.MAX_IMAGE_PIXELS`
# (89.478.485). Por encima de ese número Pillow solo AVISA —un warning que nadie
# lee— y DECODIFICA IGUAL; solo revienta pasado el DOBLE. Esa franja ciega entre
# 89 y 179 millones es justo donde vivía el problema, así que acá no se alcanza
# nunca: nada con más de 80 millones de puntos DECLARADOS se abre, ni siquiera
# un JPEG que se fuera a decodificar reducido. Y deja pasar los 64 MP del
# teléfono más grande que existe hoy.
MAX_PIXELES_SOPORTE_ABSOLUTO = 80_000_000

# El tope en puntos DECODIFICADOS para el caso corriente —una foto de 3 o 4
# bandas, 10 MB por megapíxel—. Se deja calculado y con nombre porque es el que
# miran las pruebas; la comprobación de verdad la hace
# `_exigir_que_quepa_en_memoria`, formato por formato.
MAX_PIXELES_SOPORTE = int(PRESUPUESTO_MB_POR_IMAGEN / 10.0 * 1_000_000)

# ==========================================================================
# EL TECHO DE VERDAD: lo que ya no depende de haber adivinado bien
# ==========================================================================
# TODO LO DE ARRIBA ES UNA PREDICCIÓN, y las predicciones se equivocaron tres
# veces: el tope por megabytes, el WEBP sin pérdida y el JPEG progresivo. El
# presupuesto SE QUEDA —es el filtro rápido que rebota gratis lo obvio, sin
# gastar un proceso—, pero YA NO ES LA DEFENSA. La defensa es que la
# decodificación ocurre en OTRO PROCESO con un tope que hace cumplir el sistema
# operativo (ver `app/core/imagenes_obrero.py`, que explica el porqué entero).
#
# CUÁNTO AIRE SE LE DA AL OBRERO POR ENCIMA DEL PRESUPUESTO, y por qué hace falta
# darle alguno. Los dos números miden cosas distintas a propósito:
#
#   · el PRESUPUESTO (256 MB) es el del filtro: se cobra por adelantado, con la
#     tabla redondeada HACIA ARRIBA, y rebota con un mensaje amable;
#   · el TECHO es el del sistema operativo: no cobra, MATA. Tiene que quedar por
#     ENCIMA de todo lo que el filtro deja pasar, porque si quedara por debajo
#     mataría fotos legítimas que el filtro ya aprobó —y el dueño vería fallar
#     una foto que ayer subía bien.
#
# 64 MB de aire, y sale de lo medido: el caso legítimo más caro que el filtro
# deja pasar es el WEBP sin pérdida de 12 MP, que el filtro cobra a 17 MB por
# megapíxel (204 MB) y que MIDE 196,8 MB. Entre lo que se cobra y lo que se gasta
# hay margen, pero `RLIMIT_AS` no cuenta memoria usada sino ESPACIO DE
# DIRECCIONES PEDIDO, que siempre es más: el que reserva de a bloques es el
# asignador, no el programa. 64 MB cubren esa diferencia con holgura y dejan el
# techo del obrero en 320 MB por encima de lo que ya traía puesto.
TECHO_EXTRA_MB = 64

# CUÁNTO SE ESPERA AL OBRERO ANTES DE DARLO POR COLGADO. Una foto legítima, la
# más cara de todas (una HEIC de iPhone de 12 MP), tarda menos de un segundo. 30
# segundos no es "un poco más": es treinta veces el peor caso conocido, así que
# si se cumplen es que el obrero se trabó de verdad —o que el sistema operativo
# lo está matando muy despacio— y lo que corresponde es matarlo y contestar.
#
# ESTO TAPA UNA SEGUNDA FAMILIA, la de las bombas de TIEMPO y no de memoria: un
# archivo que no pide memoria de más pero que deja al decodificador dando vueltas
# se llevaba uno de los 40 hilos para siempre. Ahora se lleva un obrero, que se
# tira y se hace otro.
ESPERA_MAXIMA_DEL_OBRERO = 30

# Y CUÁNTO SE ESPERA EL SALUDO, que es otra cosa. Un obrero sano saluda en medio
# segundo; si a los quince no ha dicho nada, no es que esté ocupado —todavía no
# le han mandado nada— sino que no va a arrancar. Se espera menos que por una
# foto a propósito: mientras se espera el saludo no se está haciendo ningún
# trabajo, solo se está retrasando la respuesta al dueño.
ESPERA_DEL_SALUDO = 15

# Un JPEG PROGRESIVO guarda sus coeficientes con DOS BYTES cada uno (es un entero
# de 16 bits con signo: en libjpeg se llama JCOEF). Es la constante que convierte
# "cuántos coeficientes tiene esta imagen" en megabytes, y está acá con nombre
# porque es la que hace que la cuenta de `_costo_de_los_coeficientes` cuadre con
# lo medido en vez de ser un factor de corrección puesto a ojo.
BYTES_POR_COEFICIENTE = 2

# ----------------------------------------------------- cuántas subidas a la vez
# QUE OCHO SUBIDAS NO PIDAN OCHO VECES LA MEMORIA. Bajar las copias y graduar el
# tope arreglan UNA imagen; esto arregla VARIAS. Sin esto, ocho fotos normales de
# 12 MP entrando a la vez pedían 738 MB, y ocho capturas PNG de 12 MP, 1.183 MB:
# ninguna de las ocho es abusiva y entre todas tumban el proceso.
#
# TRES A LA VEZ, y las demás HACEN FILA en vez de rebotar. MEDIDO con ocho
# subidas simultáneas, pico de RSS del proceso y lo que tardan las ocho:
#
#   ocho a la vez, de                antes         ahora (3 turnos)
#   ------------------------------   -----------   ----------------
#   JPEG de celular 12 MP              738 MB        105 MB · 0,69 s
#   capturas PNG RGBA 12 MP          1.183 MB        607 MB · 1,09 s
#   HEIC de iPhone 12 MP               561 MB        252 MB · 2,50 s
#   WEBP sin pérdida 12 MP           1.544 MB        618 MB · 1,55 s
#   cuatro WEBP de 3 KB (la bomba)   4.862 MB        rebotan las cuatro
#
# POR QUÉ TRES Y NO UNO. Con las capturas PNG —el caso más caro que pasa— la
# cuenta por número de turnos es 446 MB con uno, 518 con dos y 590 con tres, y el
# tiempo va al revés: 1,43 s con uno, 0,92 con dos, 0,77 con tres. O sea que
# bajar a un turno ahorra 144 MB y DUPLICA la espera del dueño, que está en el
# campo con mala señal. Y esos 446 MB que no bajan con los turnos NO son la
# compresión: son los bytes de los ocho archivos, que el servidor ya tiene en
# memoria por haberlos recibido, y que costarían igual sin comprimir nada.
#
# NO ROMPE LA SUBIDA DE VARIOS ARCHIVOS. El turno se pide y se suelta POR IMAGEN,
# no por petición: una subida de 20 fotos las pasa una por una y suelta el turno
# entre cada una, así que dos dueños subiendo lotes se van intercalando en vez de
# bloquearse. Y como un hilo nunca tiene más de un turno a la vez, no hay forma
# de que se traben entre ellos.
# BAJADO A UNO, Y LA RAZÓN ES EL TAMAÑO DE LA MÁQUINA, NO LA DE LA FILA.
#
# Con tres, la verificación midió lo que de verdad cuesta: el piso en reposo pasa
# de 47,5 MB a 260,6 MB (son 69 MB por obrero, y se quedan puestos), y tres
# subidas pesadas LEGÍTIMAS a la vez piden 717 MB. El techo duro es POR PROCESO:
# no acota la suma de los tres. En una instancia de 512 MB —que es lo normal en
# los planes baratos de Render— eso es una muerte por falta de memoria, y como el
# proceso es uno solo para las DOS queseras, se van las dos.
#
# Y la concurrencia que se compraba no existe: esto es una quesera anotando unos
# pocos comprobantes al día, no un sitio de fotos. Con un turno, dos subidas
# simultáneas se hacen una detrás de la otra —0,08 s cada una— y el dueño no
# nota la diferencia; lo que sí notaría es que el sistema se cayó.
#
# SI ALGÚN DÍA LA MÁQUINA CRECE, este número se puede subir: cada unidad son
# ~69 MB de piso más hasta ~320 MB de pico. Mide la memoria de la instancia antes
# de tocarlo, que es justo la cuenta que este comentario existe para no olvidar.
SUBIDAS_A_LA_VEZ = 1

# Y SI LA FILA NO AVANZA, SE LE DICE. Un minuto es muchísimo más de lo que
# cualquier fila real va a tardar; si se cumple, algo está mal de verdad y es
# mejor un mensaje que dice "vuelva a intentar" que una petición colgada
# ocupando uno de los 40 hilos para siempre.
ESPERA_MAXIMA_SEGUNDOS = 60

_turnos = threading.BoundedSemaphore(SUBIDAS_A_LA_VEZ)

# ------------------------------------------------------------ la compresión
LADO_MAYOR_MAX = 1600
CALIDAD_JPEG = 75

# CUÁNDO VALE LA PENA RE-COMPRIMIR. Si la versión comprimida no ahorra al menos
# esto, se guarda EL ORIGINAL, byte por byte. Sin este freno, una captura de
# 90 KB que ya venía apretada se volvía a apretar para ahorrar 4 KB, perdiendo
# calidad una segunda vez sobre la prueba de un pago. Son dos condiciones a la
# vez —un porcentaje y una cifra— porque cada una sola se equivoca por un lado:
# el 10 % pelado deja pasar re-comprimir un archivo de 50 KB para ganar 5 KB, y
# los 40 KB pelados dejan pasar re-comprimir uno de 4 MB para ganar 41 KB.
AHORRO_MINIMO_PORCENTAJE = 10
AHORRO_MINIMO_BYTES = 40 * 1024


def detectar_tipo(cabeza: bytes) -> str | None:
    """Qué es el archivo DE VERDAD, mirándole los primeros bytes.

    No se confía en el Content-Type que manda el navegador ni en la extensión
    del nombre: los dos los pone quien sube y los dos se cambian solos. Y aquí
    importa de verdad, porque de estos objetos se reparten enlaces firmados que
    se abren en el navegador de otra persona: un .html disfrazado de .jpg sería
    una página que corre en el dominio del almacenamiento con un enlace que el
    dueño repartió de buena fe por WhatsApp.

    Devuelve el tipo reconocido, o None si no es ninguno de los permitidos.
    """
    if cabeza.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if cabeza.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if cabeza[:4] == b"RIFF" and cabeza[8:12] == b"WEBP":
        return "image/webp"
    if cabeza[4:8] == b"ftyp":
        marca = cabeza[8:12]
        if marca in MARCAS_HEIC:
            return "image/heic"
        if marca in MARCAS_HEIF:
            return "image/heif"
    if cabeza.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def tamano_legible(bytes_: int) -> str:
    """"4,2 MB" — con coma decimal, que es como se escribe en Colombia."""
    if bytes_ < 1024:
        return f"{bytes_} bytes"
    if bytes_ < 1024 * 1024:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_ / (1024 * 1024):.1f}".replace(".", ",") + " MB"


def pillow_disponible() -> bool:
    """¿Este servidor sabe abrir imágenes? Sin Pillow, pasan sin comprimir."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


# Se recuerda el resultado para no reintentar el import en cada foto. `None` es
# "todavía no se preguntó".
_heic_listo: bool | None = None


def heic_disponible() -> bool:
    """¿Este servidor sabe abrir la foto de un iPhone? Y de paso, se lo enseña.

    Pillow SOLA no abre HEIC. `pillow-heif` le registra el formato adentro y
    desde ese momento `Image.open` lo abre como cualquier otra foto: no hay un
    camino aparte para las HEIC, entran por el mismo compresor, se encogen igual
    y salen guardadas como JPEG.

    EL REGISTRO SE HACE UNA SOLA VEZ Y SOLO CUANDO LLEGA UNA HEIC. Son 27 MB con
    un decodificador en C adentro, y el proceso que en toda su vida solo va a
    recibir JPEG no tiene por qué cargarlos. `register_heif_opener` es idempotente
    y solo escribe en las tablas de formatos de Pillow, así que dos peticiones
    entrando a la vez no se estorban.
    """
    global _heic_listo
    if _heic_listo is None:
        try:
            import pillow_heif
        except ImportError:  # pragma: no cover - depende del entorno
            logger.warning(
                "Falta pillow-heif: las fotos de iPhone (HEIC) se van a rechazar"
            )
            _heic_listo = False
        else:
            pillow_heif.register_heif_opener()
            _heic_listo = True
    return _heic_listo


def _en_cristiano(numero: int) -> str:
    """12000 → "12.000": punto de miles, que es como se escriben acá."""
    return f"{numero:,}".replace(",", ".")


def _mensaje_demasiados_pixeles(
    nombre: str | None,
    tamano: tuple[int, int] | None,
    tope_megapixeles: int = MAX_PIXELES_SOPORTE_ABSOLUTO // 1_000_000,
) -> str:
    """Por qué se rebotó la imagen SIN abrirla, y qué hacer.

    SE DICEN LAS MEDIDAS DE VERDAD porque son lo único que le deja al dueño
    entender qué pasó: "es demasiado grande" a secas, sobre un archivo que pesa
    150 KB, no se entiende y suena a error del sistema. Con las medidas escritas
    se ve de una que eso no salió de la cámara de un teléfono.

    EL TOPE QUE SE LE DICE ES EL DE SU FORMATO, porque el tope de verdad depende
    del formato (ver el presupuesto de arriba): un WEBP cuesta cuatro veces lo
    que un JPEG del mismo tamaño. Decirle un número que no es el suyo lo mandaría
    a encoger la foto a una medida que igual va a rebotar.
    """
    quien = f"«{nombre}»" if nombre else "La imagen"
    tope = tope_megapixeles
    if tamano:
        ancho, alto = tamano
        medida = (
            f": son {_en_cristiano(ancho)} × {_en_cristiano(alto)} puntos "
            f"({ancho * alto // 1_000_000} megapíxeles) y el máximo son {tope}"
        )
    else:
        medida = f": pasa de los {tope} megapíxeles que se pueden procesar"
    return (
        f"{quien} es demasiado grande para procesarla{medida}. Mándela como una "
        f"foto normal del celular, o el comprobante en PDF"
    )


def _mensaje_ilegible(content_type: str, nombre: str | None) -> str:
    """Por qué no se pudo abrir la imagen, y QUÉ HACER. Nunca un 500.

    EL CASO DEL iPHONE LLEVA MENSAJE PROPIO, PERO SOLO CUANDO DE VERDAD APLICA.
    Hoy este servidor SÍ abre HEIC —`pillow-heif` está declarada—, así que una
    HEIC que no se pudo abrir teniendo la librería es una foto DAÑADA, no un
    formato desconocido: mandar al dueño a cambiarle el formato en los Ajustes
    del teléfono sería mandarlo a arreglar lo que no está roto. El mensaje del
    iPhone queda para el día que la librería falte —un despliegue a medias, una
    imagen de Docker armada sin ella—, que es cuando lo que de verdad necesita es
    el camino para mandar la foto.
    """
    quien = f"«{nombre}»" if nombre else "La imagen"
    if content_type in ("image/heic", "image/heif") and not heic_disponible():
        return (
            f"{quien} es una foto de iPhone (formato HEIC) y este servidor no la "
            "puede procesar. Mándela como JPG: en el iPhone entre a Ajustes → "
            "Cámara → Formatos y elija «Más compatible», o mande la foto por "
            "WhatsApp, que la convierte sola"
        )
    return (
        f"{quien} no se pudo abrir como imagen: puede estar dañada o haber "
        "llegado a medias. Vuelva a tomarla, o mande el comprobante en PDF"
    )


def _trae_ubicacion(imagen: Any) -> bool:
    """¿La foto viene con el GPS de dónde se tomó pegado en la EXIF?

    Se pregunta ANTES de decidir si vale la pena re-comprimir, porque de esa
    decisión depende si lo que se guarda son los bytes ORIGINALES —que llevan la
    EXIF entera, ubicación adentro— o los re-codificados, que salen sin ninguna.

    PERO NO SE PREGUNTA ANTES DEL PRESUPUESTO DE MEMORIA, y acá está el porqué,
    que es contraintuitivo: en JPEG leer la EXIF es gratis —vive en la cabecera y
    `Image.open` ya la dejó en `info`—, pero EN PNG el trozo `eXIf` puede venir
    DESPUÉS de los datos de la imagen, así que Pillow DECODIFICA EL ARCHIVO
    ENTERO para poder contestar. Medido: 305 MB con un PNG de 8.944 × 8.944.
    Llamar a esta función un renglón antes del presupuesto lo dejaba sin efecto.
    """
    try:
        exif = imagen.getexif()
        if not exif:
            return False
        # 0x8825 = GPSInfo, el sub-directorio donde el celular escribe la latitud
        # y la longitud de dónde se tomó la foto.
        return bool(exif.get_ifd(0x8825))
    except Exception:  # pragma: no cover - EXIF rota: se trata como que no trae
        return False


def _trae_giro(imagen: Any) -> bool:
    """¿La foto viene acostada, con la etiqueta EXIF que dice cómo enderezarla?

    Se pregunta en el mismo momento que el GPS y por la misma razón: de esto
    depende si al final se puede devolver el original byte por byte. Un original
    con etiqueta de giro tiene los píxeles ACOSTADOS, así que devolverlo tal cual
    deja el comprobante de lado para todo el que no lea EXIF. Y por el mismo
    motivo que allá, esta pregunta va DESPUÉS del presupuesto de memoria: mirar
    la EXIF de un PNG lo decodifica entero.

    La etiqueta 274 es Orientation. El 1 es "ya está derecha"; del 2 al 8 hay algo
    que hacerle (girar, espejar, o las dos).
    """
    try:
        return imagen.getexif().get(274, 1) not in (1, None)
    except Exception:  # pragma: no cover - EXIF rota: se trata como que no trae
        return False


def _con_extension(nombre: str, extension: str) -> str:
    """"captura.png" + ".jpg" → "captura.jpg". Sin extensión, se le pega.

    EL RECORTE A 255 SE LE HACE A LA RAÍZ, NUNCA A LA EXTENSIÓN, y esa es la
    parte que antes estaba al revés. Recortando el resultado entero, un nombre
    que ya venía con 255 caracteres se llevaba por delante el ".jpg" completo y
    el soporte aterrizaba SIN extensión en el computador del productor —que es
    justo el problema que este arreglo vino a cerrar—. La extensión es lo que
    hace que el archivo se abra; el nombre solo tiene que decir de qué es.
    """
    raiz, punto, _ = nombre.rpartition(".")
    if not punto:
        raiz = nombre
    return f"{raiz[: 255 - len(extension)]}{extension}"


def _tiene_transparencia(imagen: Any) -> bool:
    """¿Esta imagen trae canal alfa (o el color transparente de una paleta)?"""
    return imagen.mode in ("RGBA", "LA", "PA") or (
        imagen.mode == "P" and "transparency" in imagen.info
    )


def _sin_transparencia(imagen: Any) -> Any:
    """Aplana el canal alfa sobre BLANCO. Va ANTES de encoger, y por dos razones.

    SOBRE BLANCO Y NO SOBRE EL NEGRO que pondría Pillow por omisión: la captura de
    un comprobante suele venir con fondo transparente, y sobre negro el texto
    negro del monto desaparece — se guardaría un soporte en blanco.

    Y ANTES DE ENCOGER PORQUE SALE MÁS BARATO, medido: encoger una RGBA le cuesta
    a Pillow DOS copias de más (premultiplica el alfa convirtiendo a RGBa, encoge,
    y vuelve), mientras que aplanar cuesta UN lienzo RGB y deja el encogido
    trabajando sobre tres bandas. Sobre una captura de 12 megapíxeles: 94 MB
    aplanando primero contra 120 MB aplanando después. Y el resultado es EL MISMO
    ARCHIVO, byte por byte, comprobado — así que no se está cambiando calidad por
    memoria, solo el orden.

    DOS COPIAS MENOS QUE ANTES, además, y ninguna de las dos hacía falta. La
    primera era `convert("RGBA")` sobre una imagen que YA venía en RGBA: Pillow
    devuelve una copia entera para no cambiar nada. La segunda era `split()[-1]`,
    que parte la imagen en cuatro bandas nuevas solo para quedarse con el alfa,
    cuando `paste` acepta la RGBA entera como máscara y usa su alfa sin partir
    nada. Sobre esa misma captura, esas dos copias costaban 105 MB.
    """
    from PIL import Image

    if not _tiene_transparencia(imagen):
        return imagen
    rgba = imagen if imagen.mode == "RGBA" else imagen.convert("RGBA")
    fondo = Image.new("RGB", rgba.size, (255, 255, 255))
    fondo.paste(rgba, mask=rgba)
    return fondo


def _a_rgb(imagen: Any) -> Any:
    """La imagen en RGB, que es lo único que sabe guardar un JPEG.

    Va DESPUÉS de encoger: a 1.600 píxeles convertir no cuesta nada, y a 4.032 sí
    —una foto en gris o en CMYK pagaba la conversión entera para tirar el 94 % un
    renglón después—. La transparencia es el caso al revés y por eso se aplana
    aparte y antes, en `_sin_transparencia`.

    El `if` de la transparencia se queda igual como red: esta función también se
    usa suelta, y sobre una imagen con alfa tiene que seguir aplanando sobre
    blanco en vez de dejar que el JPEG la tumbe.
    """
    if imagen.mode == "RGB":
        return imagen
    if _tiene_transparencia(imagen):
        return _sin_transparencia(imagen)
    return imagen.convert("RGB")


def _listo_para_encoger(imagen: Any) -> Any:
    """La imagen en un modo que se pueda encoger BIEN. Casi siempre, la misma.

    PILLOW ENCOGE LAS IMÁGENES DE PALETA CON "EL VECINO MÁS CERCANO", pase el
    filtro que se le pase: en modo "P" o "1" ignora el LANCZOS que se le pide,
    porque promediar números de paleta no significa nada. El resultado es una
    foto con los bordes en escalones — sobre el texto chiquito de una referencia
    bancaria, ilegible. Así que a esas dos se les cambia el modo ANTES de
    encoger, que además es barato: un punto de paleta ocupa un byte.

    Las demás pasan derecho y se encogen tal como vienen: RGBA y LA las
    premultiplica el propio Pillow antes de encogerlas, así que no hay que
    aplanarlas primero para que no queden con halos.
    """
    if imagen.mode in ("P", "1", "PA"):
        return imagen.convert("RGBA" if _tiene_transparencia(imagen) else "RGB")
    return imagen


def _destino_del_encogido(medidas: tuple[int, int]) -> tuple[int, int] | None:
    """A qué tamaño va a quedar la foto, o None si ya está por debajo del tope.

    Es la misma cuenta que hace `Image.thumbnail` —el lado mayor a
    `LADO_MAYOR_MAX` conservando la proporción—, escrita acá porque hay que
    saberla ANTES de decodificar: es lo que se le pide a `Image.draft`.
    """
    ancho, alto = medidas
    if ancho <= LADO_MAYOR_MAX and alto <= LADO_MAYOR_MAX:
        return None
    if ancho >= alto:
        return LADO_MAYOR_MAX, max(1, round(alto * LADO_MAYOR_MAX / ancho))
    return max(1, round(ancho * LADO_MAYOR_MAX / alto)), LADO_MAYOR_MAX


def _pedir_la_imagen_ya_reducida(imagen: Any) -> None:
    """Le pide al decodificador que entregue la foto YA CHICA. La palanca grande.

    `Image.draft` NO decodifica: le dice al decodificador a qué escala tiene que
    entregar la imagen cuando le toque. En JPEG eso es gratis y exacto —libjpeg
    sabe reconstruir a 1/2, 1/4 y 1/8 desde los mismos coeficientes, sin armar
    nunca la imagen completa— y como el destino son 1.600 px, NO HACE FALTA ABRIR
    LOS 4.032 COMPLETOS. Una foto de celular de 12 MP se decodifica a 2.016 ×
    1.512 (3 MP) y una de 64 MP a 2.312 × 1.734 (4 MP).

    HAY QUE PEDIRLO ACÁ Y NO DESPUÉS. `thumbnail` también llama a `draft`, pero
    para entonces la imagen ya está decodificada entera —`exif_transpose` la
    carga— y la llamada no sirve de nada. Un renglón más abajo ya es tarde.

    Y NO SIRVE PARA TODOS LOS FORMATOS: PNG, WEBP y HEIC no saben entregar a
    escala y `draft` es un no-op silencioso en ellos. Por eso el presupuesto de
    memoria se mira DESPUÉS de esta llamada, sobre `imagen.size`, que es el
    tamaño que de verdad se va a decodificar: para un JPEG ya viene reducido y
    para los demás sigue siendo el original.

    SE LE PIDE EL TAMAÑO FINAL EXACTO, no el doble. Pedirle el doble dejaría más
    margen para el encogido de después, pero con una foto de 4.032 × 3.024 la
    escala que sale de esa cuenta es 1 —o sea, no ahorra nada— justo en el caso
    de todos los días. Con el tamaño final la escala es 1/2 y el LANCZOS de
    `thumbnail` todavía baja de 2.016 a 1.600. Que eso no le quita legibilidad al
    comprobante está MEDIDO con OCR sobre la referencia bancaria, no supuesto:
    ver `test_la_referencia_bancaria_se_sigue_leyendo`.
    """
    destino = _destino_del_encogido(imagen.size)
    if destino is None:
        return
    try:
        imagen.draft(None, destino)
    except Exception:  # pragma: no cover - un decodificador que no sabe de draft
        # Es una optimización, no una regla: si el formato no la entiende, se
        # sigue derecho y se decodifica completo (y el presupuesto lo frena).
        pass


def _costo_por_megapixel(formato: str | None) -> float:
    """Cuántos MB de memoria cuesta un megapíxel de ESTE formato (ver la tabla)."""
    return COSTO_MB_POR_MEGAPIXEL.get(
        (formato or "").upper(), COSTO_MB_POR_MEGAPIXEL_DESCONOCIDO
    )


def _costo_de_los_coeficientes(imagen: Any, declaradas: tuple[int, int]) -> float:
    """Los MB que un JPEG PROGRESIVO pide ADEMÁS de lo que sale del decodificador.

    ACÁ SE ARREGLA LA MENTIRA QUE TENÍA EL FILTRO, y no es un caso más de una
    lista: es que la cuenta de arriba usaba un dato equivocado. Toda la tabla del
    presupuesto se apoya en `Image.draft` —"a un JPEG se le pide que decodifique
    ya reducido, así que se cobra sobre lo reducido"—. En un JPEG BASELINE eso es
    cierto y está medido: libjpeg va reconstruyendo por franjas y nunca arma la
    imagen entera. EN UN PROGRESIVO ES FALSO.

    Un progresivo trae la imagen repartida en varias pasadas —primero el trazo
    grueso, después el detalle—, así que para poder dibujar el primer píxel
    libjpeg necesita TODOS los coeficientes de TODAS las pasadas ya leídos y
    juntos. Arma el arreglo COMPLETO, del tamaño DECLARADO, antes de entregar
    nada. `draft` reduce la SALIDA; no toca ese arreglo.

    MEDIDO, con el archivo entrando por el endpoint de verdad:

      JPEG progresivo, 10.325 × 7.744           cobraba   costaba
      -------------------------------------     -------   -------
      CMYK, 4 componentes sin submuestrear       50 MB     660,6 MB
      RGB, con submuestreo 2×2 en el color       50 MB     261,0 MB

    LA CUENTA, QUE ES EXACTA Y NO UN FACTOR A OJO. Cada componente (Y, Cb, Cr, K)
    se guarda a su propia resolución: los de color suelen ir a la mitad de lado,
    o sea a un cuarto de los puntos. `imagen.layer` trae, por componente, sus dos
    factores de muestreo, y con eso la cantidad de coeficientes es

        puntos declarados × (suma de h×v de cada componente) / (h máximo × v máximo)

    y cada coeficiente ocupa `BYTES_POR_COEFICIENTE`. Contra lo medido:

      · CMYK (4 componentes a 1×1): 79,957 MP × 4/1 = 319,827 MP de coeficientes,
        × 2 bytes = 639,7 MB. Más los 20,0 MB de la salida = 659,6 MB.
        Se midieron 660,6 MB.
      · RGB (Y a 2×2, Cb y Cr a 1×1): 79,957 × 6/4 = 119,935 MP de coeficientes,
        × 2 bytes = 239,9 MB. Más los 15,0 MB de la salida = 254,9 MB.
        Se midieron 261,0 MB.

    SE LEEN LOS FACTORES DE VERDAD EN VEZ DE SUPONER EL PEOR CASO, y eso importa
    para no rebotar fotos buenas: suponiendo "sin submuestreo" a una foto de
    48 MP progresiva se le cobrarían 288 MB y rebotaría, cuando de verdad cuesta
    144. Con los factores leídos del archivo se le cobran los 144 y pasa.

    Y QUE ESTO SIGA SIENDO UNA PREDICCIÓN NO SE OLVIDA: es el filtro rápido, el
    que ahorra gastar un obrero. Lo que de verdad sostiene el techo es el proceso
    aparte, porque el próximo modo que no encaje tampoco lo vamos a haber previsto.
    """
    if (getattr(imagen, "format", None) or "").upper() not in ("JPEG", "MPO"):
        return 0.0
    if not imagen.info.get("progression"):
        return 0.0
    capas = getattr(imagen, "layer", None)
    if not capas:  # pragma: no cover - un JPEG sin tabla de componentes
        return 0.0
    try:
        h_max = max(componente[1] for componente in capas)
        v_max = max(componente[2] for componente in capas)
        suma = sum(componente[1] * componente[2] for componente in capas)
    except (IndexError, TypeError, ValueError):  # pragma: no cover - tabla rara
        return 0.0
    if h_max <= 0 or v_max <= 0:  # pragma: no cover - tabla rara
        return 0.0
    # LAS MEDIDAS VIENEN DE AFUERA, Y ES LA PARTE QUE HAY QUE CUIDAR. Tienen que
    # ser las DECLARADAS por el archivo, que son las del arreglo de coeficientes.
    # No se pueden sacar de la imagen acá adentro: `draft` ya le pisó `size` con
    # el tamaño reducido —y también `_size`, que es de donde `size` lo lee—, así
    # que preguntárselo a ella sería repetir exactamente el error que esta función
    # viene a corregir. El que llama las tiene guardadas de antes de `draft`.
    ancho, alto = declaradas
    coeficientes = ancho * alto / 1_000_000 * suma / (h_max * v_max)
    return coeficientes * BYTES_POR_COEFICIENTE


def _exigir_que_quepa_en_memoria(
    imagen: Any, nombre: str | None, medidas_reales: tuple[int, int]
) -> None:
    """El presupuesto, aplicado sobre lo que DE VERDAD se va a decodificar.

    Va DESPUÉS de `_pedir_la_imagen_ya_reducida` y ANTES del primer píxel: en ese
    punto `imagen.size` ya dice el tamaño al que va a salir del decodificador, y
    todavía no se ha pagado un solo byte de esa memoria. Un renglón más abajo ya
    es tarde.

    `medidas_reales` son las que trae el archivo —las de antes de pedir la
    reducción—, y sirven para DOS cosas: para escribírselas al dueño en el mensaje
    (decirle "12.000 × 12.000" le deja ver que eso no salió de un teléfono) y para
    cobrar el arreglo de coeficientes de un JPEG progresivo, que se dimensiona por
    lo declarado y no por lo que sale.

    LA CUENTA TIENE DOS SUMANDOS Y SUMAN EXACTO EL TOTAL:

        lo que cuesta = LA SALIDA del decodificador (megapíxeles que de verdad se
                        van a entregar × lo que cuesta un megapíxel de ese
                        formato, la tabla de arriba)
                      + LOS COEFICIENTES, que solo cobra un JPEG progresivo y que
                        van sobre lo DECLARADO (ver `_costo_de_los_coeficientes`)

    En todo lo que no sea un JPEG progresivo el segundo sumando es CERO y la
    cuenta queda igualita a como estaba.
    """
    ancho, alto = imagen.size
    megapixeles = ancho * alto / 1_000_000
    costo = _costo_por_megapixel(getattr(imagen, "format", None))
    salida = megapixeles * costo
    coeficientes = _costo_de_los_coeficientes(imagen, medidas_reales)
    if salida + coeficientes <= PRESUPUESTO_MB_POR_IMAGEN:
        return
    logger.warning(
        "Rebotada una imagen que no cabe en el presupuesto "
        "(%s, %s × %s, %s MB = %s de salida + %s de coeficientes): %s",
        getattr(imagen, "format", "?"),
        ancho,
        alto,
        round(salida + coeficientes),
        round(salida),
        round(coeficientes),
        nombre,
    )
    raise BusinessError(
        _mensaje_demasiados_pixeles(
            nombre, medidas_reales, int(PRESUPUESTO_MB_POR_IMAGEN / costo)
        )
    )


@contextmanager
def _turno_para_decodificar(nombre: str | None) -> Iterator[None]:
    """Hace fila para que ocho subidas a la vez no pidan ocho veces la memoria.

    Se pide el turno DESPUÉS de haber rebotado lo que no cabe: una lluvia de
    imágenes imposibles no puede llenar la fila de las buenas, porque ninguna
    llega hasta acá.
    """
    if not _turnos.acquire(timeout=ESPERA_MAXIMA_SEGUNDOS):
        quien = f"«{nombre}»" if nombre else "La imagen"
        logger.warning("Se llenó la fila de compresión esperando por %s", nombre)
        raise BusinessError(
            f"{quien} no se pudo procesar porque el servidor está atendiendo "
            "otras fotos en este momento. Espere unos segundos y vuelva a "
            "mandarla"
        )
    try:
        yield
    finally:
        _turnos.release()


def _comprimir_aqui_mismo(
    contenido: bytes, content_type: str, *, nombre: str | None = None
) -> tuple[bytes, str]:
    """La tubería entera, decodificando EN ESTE PROCESO, sea cual sea.

    ESTA ES LA FUNCIÓN QUE CORRE ADENTRO DEL OBRERO (`imagenes_obrero.py`), y es
    la misma que corre acá mismo el día que no haya obrero. Que sea UNA sola es
    a propósito: si el camino con techo y el camino sin techo fueran dos códigos
    distintos, el que casi nunca corre sería el que nadie prueba, y sería
    justamente el que atiende al cliente el día que algo falle.

    NO PIDE TURNO. El turno se pide afuera, en `comprimir_soporte`, porque es del
    proceso que atiende —el que tiene los 40 hilos— y no del que decodifica.

    Devuelve SIEMPRE una pareja (bytes, content_type), y ese content_type es el
    que manda: si la imagen se comprimió, sale como `image/jpeg` aunque haya
    entrado como PNG, y quien llame tiene que guardar ESE tipo y darle al objeto
    la extensión que le corresponde. Si no se tocó, sale el tipo que entró.

    Levanta BusinessError —con un mensaje que dice qué hacer— cuando dice ser
    una imagen y no se puede abrir. NO se guarda cruda: un archivo que ni el
    servidor puede abrir tampoco lo va a poder abrir quien reciba el enlace, y
    quedaría ocupando espacio siendo un soporte que no muestra nada.

    EL ORDEN DE LOS PASOS ES LA MITAD DEL ARREGLO DE MEMORIA. Va así, y cada
    renglón está donde está por una razón medida:

      1. `Image.open`, que solo lee la cabecera y no toca un píxel.
      2. SE PIDE LA IMAGEN YA REDUCIDA (`draft`). En JPEG el decodificador
         entrega 2.016 × 1.512 en vez de 4.032 × 3.024, y ahí se va la mayor
         parte del gasto.
      3. EL PRESUPUESTO DE MEMORIA, sobre lo que de verdad se va a decodificar.
         Es el último punto donde ya se sabe cuánto va a costar y todavía no se
         ha pagado. (Se repite acá aunque `comprimir_soporte` ya lo haya mirado
         afuera: cuesta medio milisegundo y hace que esta función se sostenga
         sola, que es lo que la vuelve probable de a una y segura de reusar.)
      4. Se le lee la EXIF (el GPS y el giro). Va acá y no arriba porque
         preguntarle la EXIF a un PNG lo DECODIFICA ENTERO — el porqué, con la
         cifra, está en el renglón donde se hace.
      5. Se aplana la transparencia, si la trae — eso SÍ conviene hacerlo antes
         de encoger (ver `_sin_transparencia`).
      6. Se encoge a 1.600 px, Y DESPUÉS se gira y se convierte a RGB. Girar y
         convertir una imagen de 1.600 px no cuesta nada; hacerlo antes costaba
         dos copias del tamaño completo. Da lo mismo en el resultado porque el
         destino del encogido es un CUADRADO (`LADO_MAYOR_MAX` ×
         `LADO_MAYOR_MAX`): girar y encoger conmutan cuando la caja es cuadrada,
         y esto se comprueba con los ocho valores de orientación en las pruebas.
      7. Se guarda como JPEG.

    LA ORIENTACIÓN SE APLICA A LOS PÍXELES (`exif_transpose`) y no se hereda del
    metadato. Las fotos de celular salen casi siempre guardadas de lado, con una
    etiqueta EXIF que dice cómo hay que girarlas; Pillow NO la aplica sola, así
    que sin esta línea el comprobante quedaba acostado — ilegible justo cuando
    hay que leerle el monto.

    Y LA UBICACIÓN NO SE QUEDA PEGADA AL SOPORTE, POR NINGUNO DE LOS DOS CAMINOS.
    Al re-guardar, la EXIF se queda por fuera: ya no hace falta (el giro está en
    los píxeles) y ahí viaja el GPS de dónde se tomó la foto, que no tiene por
    qué viajar con el comprobante de un pago que se manda por WhatsApp.

    EL ATAJO DE "NO VALÍA LA PENA RE-COMPRIMIR" TIENE TRES PUERTAS CERRADAS, y
    las tres se cerraron porque cada una volvía mentira una frase de este mismo
    docstring cuando el archivo era chico:

      · SI TRAE GPS no se toma: devolver el original byte por byte devolvía la
        EXIF entera, con la ubicación de la finca adentro.
      · SI TRAE GIRO tampoco: devolver el original devolvía los píxeles SIN
        girar y la etiqueta puesta. En pantalla el navegador lo endereza, pero
        cualquier otra cosa que mire el archivo —una miniatura, un export— muestra
        el comprobante acostado, y la promesa de arriba quedaba escrita y sin
        cumplir.
      · SI ES UNA HEIC tampoco, NUNCA. Ese es el punto entero de tener
        `pillow-heif`: una HEIC guardada como HEIC es un enlace que el navegador
        del productor NO DIBUJA. Una foto de iPhone ya recortada o ya reducida
        pesa menos de 40 KB y se colaba por este atajo, guardándose como
        `image/heic` — el problema exacto que la librería vino a resolver.

    Quitarle la ubicación a un JPEG sin volver a codificarlo se puede hacer —es
    cirugía sobre los segmentos del formato—, pero no vale un lector de JPEG
    escrito a mano en un sistema que mueve plata.
    """
    if not (content_type or "").startswith("image/"):
        # Los PDF pasan derecho: no son imágenes y ya vienen livianos.
        return contenido, content_type

    try:
        # Import PEREZOSO a propósito (ver el encabezado del módulo).
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - depende del entorno
        logger.warning(
            "Falta Pillow en el servidor: el soporte se guarda SIN comprimir (%s bytes)",
            len(contenido),
        )
        return contenido, content_type

    es_heic = content_type in ("image/heic", "image/heif")
    if es_heic:
        # Le enseña a Pillow a abrir la foto del iPhone (una sola vez por proceso)
        # ANTES de intentar abrirla. Si la librería no está, `Image.open` va a
        # fallar más abajo y el mensaje que sale dice cómo mandar la foto.
        heic_disponible()

    try:
        original = Image.open(io.BytesIO(contenido))
        try:
            medidas = original.size
            # EL TOPE DURO, ANTES QUE NADA: por encima de esto no se abre ni un
            # JPEG que se fuera a decodificar reducido, para no rozar siquiera la
            # franja ciega de Pillow (ver `MAX_PIXELES_SOPORTE_ABSOLUTO`).
            if medidas[0] * medidas[1] > MAX_PIXELES_SOPORTE_ABSOLUTO:
                raise BusinessError(_mensaje_demasiados_pixeles(nombre, medidas))
            # LA PALANCA GRANDE: que el decodificador entregue la foto ya chica.
            _pedir_la_imagen_ya_reducida(original)
            # Y el presupuesto, sobre el tamaño que de verdad va a salir. Hasta
            # este renglón NO SE HA TOCADO UN PÍXEL, y por eso todo lo que rebota
            # rebota gratis.
            _exigir_que_quepa_en_memoria(original, nombre, medidas)

            # LA EXIF SE LEE DESPUÉS DEL PRESUPUESTO, y no antes como parecería
            # natural. En JPEG leerla es gratis —vive en la cabecera y
            # `Image.open` ya la dejó en `info`—, PERO EN PNG NO: el trozo `eXIf`
            # puede venir DESPUÉS de los datos de la imagen, así que Pillow, para
            # poder contestar, DECODIFICA EL ARCHIVO ENTERO. Medido: preguntarle
            # la EXIF a un PNG de 8.944 × 8.944 se lleva 305 MB. Leerla antes del
            # presupuesto era abrir por la ventana justo lo que el presupuesto
            # cierra por la puerta —el mismo defecto de siempre, un renglón más
            # arriba—. Acá, en cambio, la imagen ya pasó el presupuesto y de todas
            # formas se va a decodificar.
            trae_ubicacion = _trae_ubicacion(original)
            trae_giro = _trae_giro(original)
            imagen = _listo_para_encoger(original)
            # El alfa se aplana ANTES de encoger, que es lo barato (el porqué,
            # con las cifras, está en `_sin_transparencia`).
            imagen = _sin_transparencia(imagen)
            if imagen is not original:
                # SE SUELTA LA IMAGEN ABIERTA APENAS DEJA DE HACER FALTA, y no
                # al final en el `finally`. Cuando aplanar o cambiar de modo
                # devolvió una imagen NUEVA, la de adentro de `original` ya no
                # la mira nadie, pero seguía ocupando su memoria durante el
                # encogido — que es justo el momento del pico. Sobre una
                # captura de 12 megapíxeles con transparencia son 26 MB de
                # diferencia. `close` es idempotente: el `finally` de abajo lo
                # vuelve a llamar sin problema.
                original.close()
            # `thumbnail` SOLO ENCOGE: una foto más chica que el tope se queda
            # del tamaño que venía. Agrandarla no le agregaría un detalle que
            # no tiene y dejaría un archivo más pesado que el original.
            imagen.thumbnail((LADO_MAYOR_MAX, LADO_MAYOR_MAX), Image.LANCZOS)
            # AHORA SÍ el giro y la conversión, sobre una imagen de 1.600 px:
            # `in_place` para que no se haga una copia más (sin él, Pillow
            # copia la imagen ENTERA incluso cuando no hay nada que girar).
            ImageOps.exif_transpose(imagen, in_place=True)
            imagen = _a_rgb(imagen)
            buffer = io.BytesIO()
            imagen.save(
                buffer,
                format="JPEG",
                quality=CALIDAD_JPEG,
                optimize=True,
                # Progresivo: el mismo peso, pero en el campo —con señal
                # mala— la foto se va viendo entera y borrosa desde el primer
                # pedazo en vez de aparecer por franjas.
                progressive=True,
            )
        finally:
            original.close()
    except BusinessError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        # El tope PROPIO de Pillow, que salta desde adentro de `Image.open` —antes
        # que el de arriba— cuando la imagen pasa del DOBLE de
        # `Image.MAX_IMAGE_PIXELS`. Sale con el MISMO mensaje que el tope de acá:
        # el suyo está en inglés y habla de un ataque de denegación de servicio,
        # que no es lo que el dueño necesita leer cuando mandó una captura rara.
        logger.warning("Rebotada una imagen enorme (%s): %s", content_type, exc)
        raise BusinessError(_mensaje_demasiados_pixeles(nombre, None)) from exc
    except Exception as exc:
        # Cualquier otra cosa: archivo truncado, JPEG que no era JPEG, una HEIC
        # cuando falta `pillow-heif`. Todas salen con un mensaje que se entiende
        # y ninguna con un 500.
        logger.warning("No se pudo comprimir un soporte (%s): %s", content_type, exc)
        raise BusinessError(_mensaje_ilegible(content_type, nombre)) from exc

    comprimido = buffer.getvalue()
    ahorro = len(contenido) - len(comprimido)
    no_vale_la_pena = (
        ahorro < AHORRO_MINIMO_BYTES
        or ahorro * 100 < len(contenido) * AHORRO_MINIMO_PORCENTAJE
    )
    # Las tres puertas del atajo, explicadas en el docstring.
    hay_que_reescribirla = trae_ubicacion or trae_giro or es_heic
    if no_vale_la_pena and not hay_que_reescribirla:
        # No valía la pena: se guarda el original tal como llegó, con su tipo.
        return contenido, content_type
    return comprimido, "image/jpeg"


# =========================================================================
# EL OBRERO: decodificar aparte, donde el sistema operativo pone el techo
# =========================================================================
# EL PORQUÉ ENTERO —por qué se dejó de predecir y se pasó a un techo de verdad—
# está en `app/core/imagenes_obrero.py`. Acá está solamente la mitad del padre:
# cómo se arranca el obrero, cómo se le habla y qué se hace cuando se muere.
#
# CUÁNTOS OBREROS: los mismos que turnos (`SUBIDAS_A_LA_VEZ`). No es casualidad
# ni una constante nueva que haya que mantener a la par: adentro del turno nunca
# hay más de `SUBIDAS_A_LA_VEZ` hilos, así que con esa cantidad de obreros SIEMPRE
# hay uno libre para el que entra y ninguno espera dos veces. Un obrero de más
# sería memoria parada sin usar; uno de menos volvería a serializar lo que el
# turno ya dejó pasar en paralelo.
#
# SE REUTILIZAN, y esa es la decisión que hace que esto sea aceptable para el
# dueño. MEDIDO en el portátil, con la foto de celular de 12 MP:
#
#   dónde se decodifica                        por foto
#   ----------------------------------------   --------
#   en el mismo proceso (como estaba)           0,076 s
#   en un obrero REUTILIZADO                    0,081 s   ← +0,005 s
#   en un proceso NUEVO cada vez                0,550 s   ← +0,474 s, siete veces
#
# O sea que arrancar un proceso por foto costaba SIETE VECES lo que cuesta
# comprimirla, y reutilizarlo cuesta cinco milésimas. El arranque (0,53 s en
# Windows, y menos en Linux) se paga UNA vez, en la primera foto que suba el
# servidor después de encender.
#
# LO QUE ESTO CUESTA EN MEMORIA, QUE NO ES GRATIS Y HAY QUE DECIRLO. Tres obreros
# son tres Python más, y eso se paga aunque no estén haciendo nada. Medido en el
# portátil, con psutil, y el desglose suma la cifra grande:
#
#   qué                                          RSS
#   ------------------------------------------   --------
#   Python pelado                                 18,3 MB
#   + Pillow                                       5,5 MB
#   + `app.core.imagenes` (arrastra starlette
#     por `BusinessError`)                        21,4 MB
#   ------------------------------------------   --------
#   un obrero recién arrancado                    45,2 MB
#   lo que retiene el asignador tras decodificar   23,8 MB
#   ------------------------------------------   --------
#   un obrero ya usado, parado                    69,0 MB
#
# Y EL CONJUNTO, que es la cifra que le importa a la instancia:
#
#   la API sola, antes de la primera foto         47,5 MB
#   la API después de comprimir                   53,4 MB   ← casi no crece
#   los tres obreros (69,1 + 69,2 + 68,9)        207,2 MB
#   ------------------------------------------   --------
#   la API + los tres obreros, todos parados     260,6 MB
#
# EL CAMBIO, ENTONCES, ES ESTE, Y ASÍ HAY QUE JUZGARLO: se pasa de UN proceso que
# en reposo pesaba 47,5 MB y que en el peor caso CONOCIDO llegaba a 660 MB —y en
# el peor DESCONOCIDO, a lo que fuera— a un conjunto que en reposo pesa 260,6 MB
# pero que YA NO TIENE PEOR CASO DESCONOCIDO: por encima del techo, el que se
# muere es un obrero. Se está comprando un tope a cambio de 213,1 MB de piso
# (260,6 − 47,5), y se compra porque un piso alto se ve venir en la factura y un
# techo que no existe se ve venir cuando las dos queseras se quedan sin ERP un
# martes.
#
# LOS OBREROS ARRANCAN PEREZOSAMENTE, así que un servidor que todavía no ha
# recibido ningún soporte sigue pesando los 47,5 MB de siempre. SI ALGÚN DÍA ESOS
# 213 MB estorbaran en una instancia chica, lo que hay que bajar es
# `SUBIDAS_A_LA_VEZ` —que baja obreros y turnos a la vez— sabiendo lo que cuesta:
# con un solo turno, ocho capturas PNG pasaban de 0,77 s a 1,43 s. Es esa cuenta,
# y no otra.
_OBREROS: Any = None
_candado_obreros = threading.Lock()


class _Obrero:
    """Un proceso hijo que decodifica fotos, y que se rehace solo si se muere.

    SE ARRANCA PEREZOSAMENTE, con la primera foto que le toque. El servidor que
    en toda su vida no reciba un soporte no paga ni un proceso; y la primera
    subida después de encender paga el arranque una sola vez.
    """

    def __init__(self) -> None:
        self.proceso: Any = None
        self.techo: str = ""

    def _arrancar(self) -> None:
        import subprocess

        from app.core import imagenes_obrero as obrero

        raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        entorno = dict(os.environ)
        # Que el hijo encuentre `app.` aunque a quien lo arrancó le hayan cambiado
        # el directorio de trabajo. En producción el WORKDIR es /app y sobraría;
        # en el portátil, corriendo pytest desde cualquier carpeta, no sobra.
        entorno["PYTHONPATH"] = os.pathsep.join(
            [raiz, entorno["PYTHONPATH"]] if entorno.get("PYTHONPATH") else [raiz]
        )
        try:
            self.proceso = subprocess.Popen(
                [sys.executable, "-m", "app.core.imagenes_obrero"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # El log del obrero (la línea que dice si quedó techo o no) se
                # deja ir a la misma salida de errores del servidor, que es donde
                # el que administra ya está mirando. Capturarlo en una tubería que
                # nadie lee llenaría el búfer y colgaría al obrero a la larga.
                stderr=None,
                cwd=raiz,
                env=entorno,
            )
        except OSError as exc:
            # No hay con qué crear el proceso (sin permiso, sin descriptores, un
            # `sys.executable` que ya no está). TODAS las formas de no arrancar
            # salen por la misma puerta, para que quien llama no tenga que
            # adivinar cuáles cuentan como "acá no hay obrero": son todas.
            raise _ObreroNoArranca(f"no se pudo crear el proceso: {exc}") from exc

        # EL SALUDO. Hasta que no llegue, este obrero no ha demostrado nada; y si
        # no llega, lo que falla es el MECANISMO y no la foto (el porqué, entero,
        # está en `imagenes_obrero.main`). Se levanta `_ObreroNoArranca`, que NO
        # es un BusinessError: quien llama la trata como "acá no hay obrero" y
        # decodifica en este mismo proceso, en vez de rebotarle la foto al dueño.
        saludo = _con_plazo(
            lambda: obrero._leer_marco(self.proceso.stdout), ESPERA_DEL_SALUDO
        )
        if saludo is None or not saludo.startswith(obrero.SALUDO):
            self._matar()
            raise _ObreroNoArranca(
                "el obrero no saludó: no se pudo arrancar el proceso de imágenes"
            )
        self.techo = saludo[len(obrero.SALUDO):].decode("utf-8", "replace")
        logger.info("Obrero de imágenes listo: %s", self.techo)

    def _matar(self) -> None:
        """Se acabó este obrero. Matarlo es normal: es de usar y tirar."""
        proceso, self.proceso = self.proceso, None
        if proceso is None:
            return
        try:
            proceso.kill()
            # Se espera a que muera de verdad para no dejar zombis colgando del
            # servidor: son procesos que ya no hacen nada pero siguen en la tabla.
            proceso.wait(timeout=5)
        except Exception:  # pragma: no cover - ya estaba muerto
            pass
        for tuberia in (proceso.stdin, proceso.stdout):
            try:
                if tuberia is not None:
                    tuberia.close()
            except Exception:  # pragma: no cover
                pass

    def comprimir(
        self, contenido: bytes, content_type: str, nombre: str | None
    ) -> tuple[bytes, str]:
        """Le manda la foto al obrero y espera la respuesta. Levanta BusinessError.

        SI EL OBRERO SE MUERE, ESO ES EL TECHO FUNCIONANDO, no una avería: es
        exactamente lo que pasa cuando una imagen se pasa de lo que el núcleo le
        permite pedir. Al dueño se le contesta con un mensaje del mismo estilo que
        el del presupuesto —qué pasó y qué mandar en su lugar—, pero no el mismo:
        acá NO se sabe cuánto medía la imagen (murió el que la estaba abriendo),
        así que prometerle unas medidas sería inventarlas. Ver `_muerte_del_obrero`.
        """
        from app.core import imagenes_obrero as obrero

        if self.proceso is None or self.proceso.poll() is not None:
            self._matar()
            self._arrancar()

        try:
            entrada = self.proceso.stdin
            obrero._escribir_marco(entrada, contenido)
            obrero._escribir_marco(entrada, content_type.encode("utf-8"))
            obrero._escribir_marco(entrada, (nombre or "").encode("utf-8"))
            entrada.flush()
        except (OSError, ValueError) as exc:
            # Se murió mientras se le hablaba: la tubería se rompe al escribir.
            self._matar()
            raise _muerte_del_obrero(nombre, f"se cerró al mandarle la foto: {exc}")

        respuesta = _esperar_al_obrero(self.proceso)
        if respuesta is None:
            self._matar()
            raise _muerte_del_obrero(nombre, "no contestó a tiempo o se murió")

        estado, primero, segundo = respuesta
        if estado == obrero.RESPUESTA_BIEN:
            return primero, segundo.decode("utf-8")

        mensaje = primero.decode("utf-8", "replace")
        if mensaje.startswith("__inesperado__"):
            # Algo que la tubería no supo clasificar. Al log lo de adentro, y al
            # dueño el mensaje de siempre: nunca un rastro de Python en pantalla.
            logger.warning(
                "El obrero no pudo con un soporte (%s): %s",
                content_type,
                mensaje[len("__inesperado__"):],
            )
            raise BusinessError(_mensaje_ilegible(content_type, nombre))
        raise BusinessError(mensaje)


def _muerte_del_obrero(nombre: str | None, porque: str) -> BusinessError:
    """El 422 que sale cuando el techo mató al obrero. Se explica en cristiano."""
    logger.warning("Se murió el obrero de imágenes con «%s»: %s", nombre, porque)
    quien = f"«{nombre}»" if nombre else "La imagen"
    return BusinessError(
        f"{quien} es demasiado grande o demasiado complicada para procesarla y el "
        "servidor tuvo que dejarla. Mándela como una foto normal del celular, o "
        "el comprobante en PDF"
    )


class _ObreroNoArranca(Exception):
    """El mecanismo no sirve en esta máquina. NO es una imagen mala.

    Se distingue a propósito de `BusinessError`: un BusinessError le sale al
    dueño como "su foto no sirve", y eso sería MENTIRA y encima grave si lo que
    pasó es que el servidor no puede crear procesos. Esta se trata como "acá no
    hay obrero" y la foto se decodifica en el mismo proceso, como siempre.
    """


def _con_plazo(leer: Any, plazo: float = ESPERA_MAXIMA_DEL_OBRERO) -> Any:
    """Lo que devuelva `leer`, o None si se pasó del plazo.

    SE LEE EN UN HILO APARTE Y NO DERECHO, y no es adorno: leer de una tubería es
    una espera que NO SE PUEDE INTERRUMPIR, y en Windows tampoco se le puede poner
    un plazo (`select` no sirve sobre tuberías). Un obrero trabado se llevaría
    para siempre uno de los 40 hilos del servidor — que es justo la clase de
    problema que este archivo vino a cerrar, y sería feo cerrarla por un lado y
    abrirla por el otro. Con el hilo aparte, el que espera puede rendirse.

    EL HILO QUE SE QUEDÓ LEYENDO NO SE FUGA: quien se rinde mata al obrero, y al
    cerrarse la tubería la lectura termina y el hilo se acaba solo.
    """
    salida: list = []

    def intentar() -> None:
        try:
            devuelto = leer()
        except (OSError, ValueError):  # pragma: no cover - tubería rota
            return
        if devuelto is not None:
            salida.append(devuelto)

    lector = threading.Thread(target=intentar, daemon=True)
    lector.start()
    lector.join(timeout=plazo)
    return salida[0] if salida else None


def _esperar_al_obrero(proceso: Any) -> tuple[bytes, bytes, bytes] | None:
    """La respuesta del obrero, o None si se murió o se pasó de tiempo."""
    from app.core import imagenes_obrero as obrero

    def leer() -> tuple[bytes, bytes, bytes] | None:
        estado = proceso.stdout.read(1)
        if not estado:
            return None
        primero = obrero._leer_marco(proceso.stdout)
        if primero is None:
            return None
        if estado == obrero.RESPUESTA_BIEN:
            segundo = obrero._leer_marco(proceso.stdout)
            if segundo is None:
                return None
        else:
            segundo = b""
        return estado, primero, segundo

    return _con_plazo(leer)


def _sacar_un_obrero() -> Any:
    """La fila de obreros libres, armada la primera vez que alguien la pide."""
    global _OBREROS
    if _OBREROS is None:
        with _candado_obreros:
            if _OBREROS is None:
                import queue

                fila: Any = queue.LifoQueue()
                for _ in range(SUBIDAS_A_LA_VEZ):
                    fila.put(_Obrero())
                _OBREROS = fila
    return _OBREROS


# Se deja como variable para que las pruebas puedan apagar el obrero y comprobar
# que sin él la subida SIGUE FUNCIONANDO. No es un interruptor de configuración:
# no se lee de ninguna variable de entorno a propósito, porque un servidor con
# esto apagado sin querer sería un servidor sin techo y sin que nadie se entere.
_OBREROS_PERMITIDOS = True

# Y ESTE SE PONE SOLO, LA PRIMERA VEZ QUE UN OBRERO NO ARRANCA. Las razones por
# las que no arranca no se arreglan solas —falta un módulo, el PYTHONPATH quedó
# torcido, la plataforma no deja crear procesos—, así que reintentarlo con cada
# foto sería pagar el arranque (y hasta la espera del saludo) una y otra vez para
# terminar siempre en lo mismo. Se intenta UNA vez, se anota a gritos en el log y
# se sigue trabajando por el camino de siempre.
_OBREROS_SE_RINDIERON = False


def hay_obrero() -> bool:
    """¿Se puede decodificar aparte en este servidor?

    Se puede siempre que se pueda arrancar un proceso de Python, que es todo lo
    que hace falta: no pide `fork`, ni `resource`, ni nada que Windows no tenga.
    Lo que SÍ cambia entre sistemas es si además hay tope duro; eso lo dice
    `como_esta_el_techo`.

    Vive como función y no como constante para que una prueba pueda taparla y
    comprobar el camino degradado — que es el que atiende al cliente el día que
    esto falle, y por eso tiene que estar probado.
    """
    return _OBREROS_PERMITIDOS and not _OBREROS_SE_RINDIERON


def como_esta_el_techo() -> str:
    """Una frase que dice qué protección hay puesta de verdad en este servidor.

    EXISTE PARA QUE NADIE TENGA QUE CREER. En Linux hay tope duro; en Windows solo
    aislamiento. Si alguien mira el portátil y ve las pruebas en verde, tiene que
    poder enterarse de que producción tiene una defensa MÁS que la que él está
    viendo — y no al revés, que sería lo peligroso.

    PREGUNTA POR `hay_obrero()` Y NO POR LA VARIABLE, que es la diferencia entre
    decir la verdad y decir la intención: un servidor donde el obrero NO ARRANCÓ
    tiene que contestar que no hay obrero, aunque estuviera permitido tenerlo.
    """
    if not hay_obrero():
        return (
            "SIN obrero: se decodifica en el mismo proceso, con el presupuesto "
            "por formato como única defensa"
        )
    try:
        import resource  # noqa: F401
    except ImportError:
        return (
            "obrero aparte SIN tope duro (este sistema no tiene setrlimit): la "
            "API está aislada, pero la máquina no tiene techo"
        )
    return (
        f"obrero aparte CON tope duro de "
        f"{PRESUPUESTO_MB_POR_IMAGEN + TECHO_EXTRA_MB} MB (setrlimit/RLIMIT_AS)"
    )


def comprimir_soporte(
    contenido: bytes, content_type: str, *, nombre: str | None = None
) -> tuple[bytes, str]:
    """Los bytes ya livianos y el content_type con el que hay que guardarlos.

    ES LA PUERTA, y hace tres cosas en este orden, cada una con su porqué:

      1. EL FILTRO RÁPIDO (`_filtro_rapido`), que rebota gratis lo obvio. No se
         quitó al poner el techo y no sobra: rebotar acá cuesta MEDIO MILISEGUNDO
         y no gasta un obrero, así que una lluvia de imágenes imposibles no
         alcanza a ocupar la fila de las buenas. Lo que cambió es su PAPEL: antes
         era la defensa —y por eso cada formato que no encajaba en su cuenta era
         un agujero—; ahora es el ahorro, y el que defiende es el techo.
      2. EL TURNO, para que ocho subidas a la vez no pidan ocho veces la memoria.
      3. LA DECODIFICACIÓN CON TECHO, en el obrero.

    Y SI EL OBRERO NO SE PUEDE ARRANCAR, LA SUBIDA NO SE CAE: se decodifica en
    este mismo proceso, con el filtro rápido como defensa —que es exactamente lo
    que había hasta hoy y llevaba meses funcionando—, y queda un aviso en el log.
    Es la misma decisión que ya estaba tomada para Pillow unas líneas más arriba,
    y por la misma razón: el cliente tiene que poder pegarle la foto al pago. Lo
    que NO se hace nunca es lo tercero, decodificar sin filtro y sin techo.
    """
    if not (content_type or "").startswith("image/"):
        # Los PDF pasan derecho: no son imágenes y ya vienen livianos.
        return contenido, content_type

    if not pillow_disponible():  # pragma: no cover - depende del entorno
        logger.warning(
            "Falta Pillow en el servidor: el soporte se guarda SIN comprimir (%s bytes)",
            len(contenido),
        )
        return contenido, content_type

    _filtro_rapido(contenido, content_type, nombre)

    with _turno_para_decodificar(nombre):
        return _decodificar_con_techo(contenido, content_type, nombre)


def _decodificar_con_techo(
    contenido: bytes, content_type: str, nombre: str | None
) -> tuple[bytes, str]:
    """Decodifica donde haya techo, y si no lo hay, acá mismo antes que fallar.

    ES EL ÚNICO RENGLÓN QUE CORRE ADENTRO DEL TURNO, y por eso es el sitio donde
    se mide cuántas subidas están pasando a la vez de verdad
    (`test_la_fila_de_verdad_deja_pasar_de_a_tres` cuenta acá). Antes esa cuenta
    se hacía sobre un paso interno del compresor; ahora ese paso vive en otro
    proceso, y contar acá es además más honesto: lo que el turno regula es ESTO,
    no un detalle de adentro.
    """
    if not hay_obrero():
        return _comprimir_aqui_mismo(contenido, content_type, nombre=nombre)
    obrero = _sacar_un_obrero().get()
    try:
        return obrero.comprimir(contenido, content_type, nombre)
    except BusinessError:
        raise
    except _ObreroNoArranca as exc:
        # NO SE PUDO ARRANCAR EL PROCESO: falta un módulo, el PYTHONPATH quedó
        # torcido, la plataforma no deja crear procesos. Nada de eso se arregla
        # solo, así que se deja de intentar (ver `_OBREROS_SE_RINDIERON`) y se
        # sigue derecho acá mismo antes que dejar al cliente sin poder subir el
        # soporte de un pago.
        global _OBREROS_SE_RINDIERON
        _OBREROS_SE_RINDIERON = True
        logger.error(
            "NO SE PUDO ARRANCAR EL OBRERO DE IMÁGENES (%s). Las fotos se van a "
            "decodificar en el mismo proceso que atiende, con el presupuesto por "
            "formato como única defensa y SIN el tope del sistema operativo. "
            "Las subidas siguen funcionando; el techo, no.",
            exc,
        )
        return _comprimir_aqui_mismo(contenido, content_type, nombre=nombre)
    except Exception as exc:  # noqa: BLE001
        # Cualquier otra cosa rara. Esta foto se saca adelante acá mismo, PERO NO
        # SE RENUNCIA AL TECHO: a diferencia de la de arriba, esto puede ser algo
        # de un momento, y apagar la única defensa de verdad para siempre por un
        # tropiezo pasajero sería cambiar un susto por un agujero.
        logger.warning(
            "El obrero de imágenes falló de forma inesperada (%s); esta foto se "
            "decodifica en el mismo proceso y con la siguiente se vuelve a "
            "intentar",
            exc,
        )
        return _comprimir_aqui_mismo(contenido, content_type, nombre=nombre)
    finally:
        _sacar_un_obrero().put(obrero)


def _filtro_rapido(
    contenido: bytes, content_type: str, nombre: str | None
) -> None:
    """Rebota SIN DECODIFICAR lo que ya se sabe que no cabe. Barato a propósito.

    Abre solo la cabecera (`Image.open` no toca un píxel: medido, entre 0,6 y
    3,5 MB para todos los casos raros que se probaron), le pide la reducción y
    mira el presupuesto. Todo lo que rebota acá rebota GRATIS y sin gastar un
    obrero.

    NO LEVANTA NADA QUE NO LEVANTARÍA EL OBRERO: los mismos topes, los mismos
    mensajes. Si un día los dos discrepan, el que manda es el obrero —él tiene el
    techo—; este solo se adelanta.
    """
    from PIL import Image

    if content_type in ("image/heic", "image/heif"):
        heic_disponible()
    try:
        original = Image.open(io.BytesIO(contenido))
        try:
            medidas = original.size
            if medidas[0] * medidas[1] > MAX_PIXELES_SOPORTE_ABSOLUTO:
                raise BusinessError(_mensaje_demasiados_pixeles(nombre, medidas))
            _pedir_la_imagen_ya_reducida(original)
            _exigir_que_quepa_en_memoria(original, nombre, medidas)
        finally:
            original.close()
    except BusinessError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        logger.warning("Rebotada una imagen enorme (%s): %s", content_type, exc)
        raise BusinessError(_mensaje_demasiados_pixeles(nombre, None)) from exc
    except Exception as exc:
        logger.warning("No se pudo comprimir un soporte (%s): %s", content_type, exc)
        raise BusinessError(_mensaje_ilegible(content_type, nombre)) from exc


# ------------------------------------------------- leer el archivo que llegó
def leer_y_validar_soporte(
    archivo: Any, *, max_bytes: int, max_mb: int
) -> tuple[bytes, str, str, str]:
    """(contenido, content_type, extensión, nombre) — o BusinessError legible.

    El único camino por el que entra un soporte al sistema, lo use reventa o lo
    usen los pagos de liquidación. Hace, en este orden:

    1. MIDE ANTES DE LEER. Un archivo de 400 MB no se carga en memoria solo para
       después decir que no cabe. En el campo la señal es mala y una subida
       equivocada se nota tarde; lo que no puede pasar es que tumbe el servidor.
    2. RECONOCE EL TIPO por los primeros bytes, no por el nombre.
    3. COMPRIME. Después del tope de tamaño, no antes: el tope existe para no
       cargar en memoria lo que no cabe, y comprimir exige justamente cargarlo.
       Y ANTES de subir nada, que es lo que permite que un lote de fotos se
       valide completo y se rechace entero si una no sirve.

    La extensión que devuelve es la del tipo YA COMPRIMIDO: una foto que entró
    como PNG y salió como JPEG se guarda con `.jpg`, para que el objeto del
    bucket no mienta sobre lo que tiene adentro.

    Y EL NOMBRE SE CORRIGE CON ELLA, que es la mitad que faltaba. Ese nombre no
    es decorativo: es el que viaja en el enlace firmado como `nombre_descarga`, o
    sea el nombre con el que el archivo aterriza en el computador del productor
    cuando abre el enlace que le mandaron por WhatsApp. Si se subió "captura.png",
    se guardó un JPEG y el nombre seguía diciendo `.png`, el que lo recibe se baja
    un archivo que su equipo no sabe abrir por creerle a la extensión — y encima
    no hay a quién preguntarle, porque el enlace caduca. Solo se cambia cuando el
    tipo CAMBIÓ: un "foto.jpeg" que sigue siendo JPEG conserva su nombre tal cual.
    """
    nombre = (getattr(archivo, "filename", "") or "").strip() or "soporte"
    nombre = nombre[:255]

    origen = archivo.file
    try:
        origen.seek(0, os.SEEK_END)
        tamano = origen.tell()
        origen.seek(0)
    except (AttributeError, OSError):  # pragma: no cover - flujo no medible
        tamano = -1

    if tamano == 0:
        raise BusinessError(f"El archivo «{nombre}» está vacío")
    if tamano > max_bytes:
        raise BusinessError(
            f"«{nombre}» pesa {tamano_legible(tamano)} y el máximo son "
            f"{max_mb} MB. Tome la foto en menor calidad "
            f"o mande el comprobante en PDF"
        )

    contenido = origen.read()
    # Segunda medición, por si la de arriba no se pudo hacer.
    if len(contenido) > max_bytes:
        raise BusinessError(
            f"«{nombre}» pesa {tamano_legible(len(contenido))} y el máximo son "
            f"{max_mb} MB"
        )
    if not contenido:
        raise BusinessError(f"El archivo «{nombre}» está vacío")

    tipo = detectar_tipo(contenido[:64])
    if tipo is None or tipo not in TIPOS_SOPORTE_PERMITIDOS:
        raise BusinessError(
            f"«{nombre}» no es una imagen ni un PDF. Solo se aceptan "
            f"{TIPOS_EN_CRISTIANO}"
        )

    contenido, tipo_final = comprimir_soporte(contenido, tipo, nombre=nombre)
    extension = TIPOS_SOPORTE_PERMITIDOS[tipo_final]
    if tipo_final != tipo:
        nombre = _con_extension(nombre, extension)
    return contenido, tipo_final, extension, nombre
