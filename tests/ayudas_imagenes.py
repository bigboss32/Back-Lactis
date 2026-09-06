"""Imágenes DE VERDAD para las pruebas de soportes de pago.

POR QUÉ NO SIRVEN LOS BYTES INVENTADOS. Estas pruebas usaban antes una cabecera
de JPEG seguida de relleno (`b"\\xff\\xd8\\xff..." + b"\\x11" * 400`). Eso alcanzaba
mientras el backend solo miraba los primeros bytes para saber qué era el archivo,
pero desde que las fotos SE COMPRIMEN al subirlas hay que poder abrirlas de
verdad: un JPEG de mentira lo rechaza el compresor, que es exactamente lo que
debe hacer con un archivo dañado. Así que las imágenes se arman aquí con Pillow,
que es la misma librería que las va a comprimir.

TODO ES DETERMINISTA (semilla fija). Las pruebas de compresión comparan tamaños
en bytes y comprueban cuánto se ahorró; con contenido aleatorio, esas cifras
bailarían entre corridas y la prueba se volvería un aviso que a veces suena.
"""
from __future__ import annotations

import io
import random

# Pillow es dependencia declarada del proyecto (requirements.txt) y estas ayudas
# son solo de pruebas: aquí sí se importa arriba, sin la pereza que sí necesita
# el código de la aplicación.
from PIL import Image, ImageDraw

# Un PDF: los comprobantes del banco llegan así y NO se tocan al subirlos.
PDF = b"%PDF-1.4\n" + b"comprobante bancolombia " * 20
TEXTO_PLANO = b"esto no es una imagen, es un archivo de texto cualquiera"
# Un ejecutable de Windows renombrado a .jpg: el caso que de verdad importa,
# porque de estos objetos se reparten enlaces que abre otra persona.
EJECUTABLE = b"MZ\x90\x00\x03" + b"\x00" * 400
# Una HEIC ROTA: la caja 'ftyp' con la marca 'heic' y nada más adentro. El backend
# la reconoce como foto de iPhone por los primeros bytes, y abrirla es imposible
# —ni con `pillow-heif` puesta—, que es justo lo que se quiere para probar el
# mensaje que sale cuando una HEIC no se puede procesar. Para la HEIC de verdad,
# la que sí se abre y se comprime, ver `foto_heic_de_iphone`.
HEIC_ROTA = (
    b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1"
    + b"\x00\x00\x00\x08meta"
    + b"\x00" * 200
)


def _capa(ancho: int, alto: int, division: int, rnd: random.Random) -> Image.Image:
    """Manchas de color: ruido chiquito agrandado con BICUBIC."""
    chico = (max(4, ancho // division), max(4, alto // division))
    crudo = bytes(rnd.getrandbits(8) for _ in range(chico[0] * chico[1] * 3))
    return Image.frombytes("RGB", chico, crudo).resize((ancho, alto), Image.BICUBIC)


def _lienzo(ancho: int, alto: int, semilla: int) -> Image.Image:
    """Un fondo con la pinta de una foto, y no un plano de color.

    DOS CAPAS de manchas, gruesas y finas, y esa es la parte que importa para que
    la medición de la compresión no mienta. Un color plano se comprimiría a nada;
    pero manchas gruesas más grano tampoco alcanzan, porque el grano DESAPARECE al
    encoger la foto a 1600 px (encoger promedia píxeles) y el resultado sale
    irrealmente liviano. Las manchas finas sí sobreviven al encogido, que es lo
    que hace una foto de verdad: tiene detalle en todos los tamaños.
    """
    rnd = random.Random(semilla)
    grueso = _capa(ancho, alto, 24, rnd)
    fino = _capa(ancho, alto, 6, rnd)
    return Image.blend(grueso, fino, 0.5)


def foto_de_comprobante(
    ancho: int = 1200,
    alto: int = 900,
    *,
    semilla: int = 7,
    calidad: int = 92,
    orientacion: int | None = None,
    grano: bool = True,
) -> bytes:
    """Una foto con la pinta de un comprobante fotografiado con el celular.

    Fondo con manchas (la mesa), un rectángulo blanco (la pantalla del banco) y
    renglones de texto oscuro encima (el monto, la cuenta, la referencia), más
    grano de sensor. Guardada como JPEG de calidad 92, que es lo que produce un
    teléfono.

    `orientacion` escribe la etiqueta EXIF de giro —6 es "la foto está acostada,
    gírela 90°"—, que es la que traen las fotos verticales de un celular y la que
    hay que aplicar al comprimir para que el comprobante no quede de lado.
    """
    imagen = _lienzo(ancho, alto, semilla)
    dibujo = ImageDraw.Draw(imagen)
    margen_x, margen_y = ancho // 8, alto // 8
    dibujo.rectangle(
        [margen_x, margen_y, ancho - margen_x, alto - margen_y], fill=(250, 250, 248)
    )
    renglones = [
        "TRANSFERENCIA EXITOSA",
        "Valor: $ 1.842.500",
        "Cuenta destino: ****4417",
        "Referencia: 000283917455",
        "Fecha: 05/09/2026  10:14 a. m.",
    ]
    y = margen_y + alto // 20
    for texto in renglones:
        dibujo.text((margen_x + ancho // 20, y), texto, fill=(20, 20, 24))
        y += alto // 16

    if grano:
        # Grano de sensor, ENCIMA DE TODO y no solo del fondo: es lo que hace que
        # una foto no se comprima a nada. Sin él, la pantalla blanca del banco
        # sería un plano perfecto, el JPEG la reduciría a nada y la medición del
        # ahorro saldría irrealmente buena — justo la cifra que hay que reportar
        # honesta.
        ruido = Image.effect_noise((ancho, alto), 14).convert("RGB")
        imagen = Image.blend(imagen, ruido, 0.25)

    buffer = io.BytesIO()
    guardar: dict = {"format": "JPEG", "quality": calidad}
    if orientacion is not None:
        exif = Image.Exif()
        exif[274] = orientacion  # 274 = Orientation
        guardar["exif"] = exif
    imagen.save(buffer, **guardar)
    return buffer.getvalue()


def foto_de_celular(semilla: int = 7) -> bytes:
    """La foto tal como sale de un celular: 12 megapíxeles y varios MB.

    Es la que se usa para MEDIR cuánto ahorra la compresión, porque es el archivo
    que de verdad manda el dueño desde el campo.
    """
    return foto_de_comprobante(4032, 3024, semilla=semilla, calidad=92)


def png_de(ancho: int = 60, alto: int = 40, *, semilla: int = 3) -> bytes:
    """Un PNG real y CHIQUITO: sirve de "imagen que ya está por debajo del tope"."""
    buffer = io.BytesIO()
    _lienzo(ancho, alto, semilla).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_de(ancho: int = 60, alto: int = 40, *, semilla: int = 5, calidad: int = 80) -> bytes:
    """Un JPEG real y CHIQUITO, para el mismo caso."""
    buffer = io.BytesIO()
    _lienzo(ancho, alto, semilla).save(buffer, format="JPEG", quality=calidad)
    return buffer.getvalue()


def bomba_de_pixeles(ancho: int = 12000, alto: int = 12000) -> bytes:
    """El hallazgo, reproducido: pocos KB en disco, cientos de MB al abrirla.

    UN PNG DE UN COLOR CASI PLANO. El PNG comprime sin pérdida por renglones
    iguales, así que 144 millones de píxeles del mismo gris caben en unos 157 KB
    —muy por debajo de los 15 MB de `ADJUNTOS_MAX_MB`—, pero al DECODIFICARLA
    ocupa un byte por píxel, y en RGB tres. Medido: 700 MB de memoria de más en el
    proceso. Como las dos queseras comparten servidor, una sola de estas las deja a
    las dos sin API.

    12.000 × 12.000 no es un número cualquiera: cae en la FRANJA CIEGA de Pillow.
    Su `MAX_IMAGE_PIXELS` es 89.478.485 y por encima de eso solo AVISA (decodifica
    igual); únicamente revienta pasado el doble. O sea que entre 89 y 179 millones
    de puntos la defensa de Pillow no defiende, y es ahí donde vive el problema.
    """
    buffer = io.BytesIO()
    # Un gris plano y `compress_level=9`: lo que hace que pese ridículamente poco.
    Image.new("L", (ancho, alto), 200).save(
        buffer, format="PNG", compress_level=9
    )
    return buffer.getvalue()


def foto_con_ubicacion(ancho: int = 60, alto: int = 40, *, semilla: int = 5) -> bytes:
    """Una foto CHIQUITA con el GPS de la finca pegado en la EXIF.

    Chiquita a propósito: así cae en el camino de "no vale la pena re-comprimir",
    que es justo el que devolvía el original con la EXIF entera y dejaba la
    ubicación pegada al soporte de un pago. Las coordenadas son las de un punto
    cualquiera del oriente antioqueño.
    """
    from PIL.TiffImagePlugin import IFDRational

    def grados(*partes: int) -> tuple:
        return tuple(IFDRational(p) for p in partes)

    exif = Image.Exif()
    exif[274] = 1  # orientación derecha: lo que se prueba acá es el GPS
    exif[0x8825] = {  # 0x8825 = GPSInfo
        1: "N",
        2: grados(6, 12, 0),
        3: "W",
        4: grados(75, 33, 0),
    }
    buffer = io.BytesIO()
    _lienzo(ancho, alto, semilla).save(
        buffer, format="JPEG", quality=80, exif=exif
    )
    return buffer.getvalue()


def heic_se_puede_abrir() -> bool:
    """¿Está `pillow-heif`? (o sea: ¿este entorno abre las fotos de iPhone?)"""
    try:
        import pillow_heif  # noqa: F401
    except ImportError:
        return False
    return True


def foto_heic_de_iphone(ancho: int = 4032, alto: int = 3024) -> bytes:
    """Una HEIC DE VERDAD, la que graba un iPhone de fábrica.

    Se arma con `pillow-heif`, que es la misma librería con la que el servidor la
    va a abrir. Los bytes de mentira no sirven acá: lo que se prueba es que la foto
    ENTRA y SE COMPRIME, y para eso tiene que ser una HEIC que de verdad se abra.
    """
    import pillow_heif

    pillow_heif.register_heif_opener()
    imagen = Image.open(io.BytesIO(foto_de_comprobante(ancho, alto, calidad=92)))
    buffer = io.BytesIO()
    imagen.save(buffer, format="HEIF", quality=90)
    return buffer.getvalue()


# Los dos que usan las pruebas cuando lo que importa no es el peso sino el flujo:
# chiquitos a propósito, para que el compresor los deje pasar TAL CUAL (no vale la
# pena re-comprimir para ahorrar unos bytes) y el tipo con el que entran sea el
# mismo con el que se guardan.
JPEG = jpeg_de()
PNG = png_de()


def bomba_webp(ancho: int = 16383, alto: int = 4882) -> bytes:
    """LA BOMBA DEL REVISOR: 3.116 BYTES que pedían 1.226 MB de memoria.

    Un WEBP SIN PÉRDIDA de un color plano. Tres kilobytes en disco —cinco mil
    veces por debajo de los 15 MB de `ADJUNTOS_MAX_MB`— con 79,98 millones de
    puntos adentro, o sea justo por DEBAJO del tope viejo de 80 millones: pasaba
    el tope, respondía 201, y hacía crecer el proceso 1.220 MB. Cuatro a la vez,
    4.854 MB, y las dos queseras sin ERP.

    16.383 × 4.882 no es un número cualquiera: 16.383 es el ancho máximo que
    admite el formato WEBP, y el alto está elegido para quedar pegado por debajo
    del tope viejo. La amplificación es de 400.000 veces.
    """
    buffer = io.BytesIO()
    Image.new("RGBA", (ancho, alto), (200, 200, 200, 255)).save(
        buffer, format="WEBP", lossless=True
    )
    return buffer.getvalue()


def jpeg_progresivo(
    ancho: int = 10325, alto: int = 7744, *, modo: str = "CMYK"
) -> bytes:
    """EL HUECO QUE QUEDABA: 2 KB que pedían 660 MB, y respondían 201.

    UN JPEG PROGRESIVO. No es un formato raro ni hace falta mala intención para
    tener uno: es lo que produce cualquier optimizador web y lo que sirve media
    internet, porque en una conexión lenta la foto se ve entera y borrosa desde el
    primer pedazo en vez de ir apareciendo por franjas. Este mismo backend GUARDA
    en progresivo (ver `comprimir_soporte`), y por buenas razones.

    POR QUÉ SE LE ESCAPABA AL PRESUPUESTO, que es la parte que importa: la tabla
    de costos se apoya entera en que a un JPEG se le puede pedir la imagen YA
    REDUCIDA (`Image.draft`), y por eso a esta foto le cobraba los 5 megapíxeles
    que iban a SALIR — 50 MB— en vez de lo que iba a PEDIR. En un progresivo
    `draft` reduce la salida pero NO el arreglo de coeficientes, que se arma
    COMPLETO y del tamaño declarado antes del primer píxel. Medido con el archivo
    entrando por el endpoint de verdad: 660,6 MB y un 201. Tres a la vez —lo que
    el semáforo permite— eran 1.933 MB, y las dos queseras sin ERP.

    SE ARMA PARCHÁNDOLE EL TAMAÑO DECLARADO Y NO CODIFICANDO 80 MEGAPÍXELES, y
    hay dos razones. La primera es el costo: codificar la foto de verdad se lleva
    más de un giga en el proceso de las PRUEBAS, que es un precio absurdo por un
    archivo que se va a rechazar. La segunda es que ASÍ ES COMO LLEGARÍA: el
    tamaño de un JPEG vive en la cabecera SOF2 —dos números de dos bytes—, y
    cambiarlos es todo lo que hay que hacer para que el decodificador reserve por
    ochenta millones de puntos que el archivo no trae.

    Y NO ES UNA BOMBA DE MENTIRA. Está comprobado que sigue costando lo mismo:
    2.311 bytes que al decodificarse piden 659,7 MB, contra los 660,6 MB del
    archivo completo de 1,31 MB. libjpeg reserva por lo que dice la cabecera antes
    de mirar si los datos alcanzan. La amplificación es de 285.000 veces.
    """
    import struct

    chico = Image.new(modo, (64, 48))
    puntos = chico.load()
    bandas = len(chico.getbands())
    for x in range(64):
        for y in range(48):
            puntos[x, y] = tuple((x * 4 + y * 3 + c * 17) % 256 for c in range(bandas))
    buffer = io.BytesIO()
    chico.save(buffer, format="JPEG", quality=60, progressive=True)

    crudo = bytearray(buffer.getvalue())
    # SOF2 (0xFFC2) es "aquí empieza un cuadro progresivo". Detrás van el largo
    # del segmento (2 bytes), la precisión (1) y AHÍ el alto y el ancho, dos
    # bytes cada uno: por eso el desplazamiento es 2 + 2 + 1 = 5 desde la marca.
    donde = crudo.find(b"\xff\xc2")
    if donde < 0:  # pragma: no cover - Pillow siempre escribe SOF2 en progresivo
        raise AssertionError("no se encontró la cabecera SOF2 del JPEG progresivo")
    struct.pack_into(">HH", crudo, donde + 5, alto, ancho)
    return bytes(crudo)


def captura_png_rgba(ancho: int = 4032, alto: int = 3024, *, semilla: int = 11) -> bytes:
    """Una captura de pantalla PNG con canal alfa: 4 canales, y ninguna maldad.

    Es el caso que el revisor midió y que NO necesita mala intención: ocho de
    estas entrando a la vez pedían 1.141 MB. Cuatro canales cuestan el doble que
    tres, y un PNG no se puede pedir reducido como un JPEG.
    """
    buffer = io.BytesIO()
    _lienzo(ancho, alto, semilla).convert("RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


def foto_heic_chiquita(ancho: int = 640, alto: int = 480) -> bytes:
    """Una HEIC DE VERDAD que pesa menos de 40 KB: la que se colaba sin convertir.

    Es la foto de iPhone ya recortada o ya reducida —la que se manda por segunda
    vez, o la que el teléfono guardó en pequeño—. Como el atajo de "no vale la
    pena re-comprimir" devuelve el ORIGINAL con su tipo original, esta se
    guardaba como `image/heic` con extensión `.heic`, y el enlace que el dueño
    manda por WhatsApp entregaba un archivo que Chrome NO DIBUJA. Que es
    exactamente el problema que `pillow-heif` vino a resolver.
    """
    import pillow_heif

    pillow_heif.register_heif_opener()
    imagen = Image.open(io.BytesIO(foto_de_comprobante(ancho, alto, calidad=60)))
    buffer = io.BytesIO()
    imagen.save(buffer, format="HEIF", quality=35)
    return buffer.getvalue()


def foto_chica_acostada(ancho: int = 800, alto: int = 600, *, orientacion: int = 6) -> bytes:
    """Una foto CHIQUITA tomada de lado: la que se guardaba acostada.

    Chiquita a propósito, igual que `foto_con_ubicacion`: así cae en el camino de
    "no vale la pena re-comprimir", que devolvía los bytes originales —píxeles sin
    girar y la etiqueta 274 todavía puesta— y volvía mentira la promesa de que "la
    orientación se aplica a los píxeles". En pantalla el navegador la endereza,
    así que el defecto no se ve; lo ve cualquier cosa que no lea EXIF.
    """
    return foto_de_comprobante(
        ancho, alto, semilla=13, calidad=55, orientacion=orientacion, grano=False
    )


# ===========================================================================
# ¿SE SIGUE LEYENDO? El OCR por plantilla con el que se mide la legibilidad
# ===========================================================================
# POR QUÉ ESTO EXISTE Y NO ALCANZA CON "LA IMAGEN NO SE ALEJÓ MUCHO". Un
# comprobante es EVIDENCIA: lo único que importa es que se le sigan leyendo el
# monto, la cuenta y el número de referencia. La diferencia media por píxel es
# una buena alarma, pero no dice si los dígitos se leen; una foto puede alejarse
# poco en promedio y tener la referencia convertida en manchas.
#
# ASÍ QUE SE LEE DE VERDAD, y sin traer una librería de OCR (que sería una
# dependencia enorme para probar una cosa): la foto se arma acá, así que se sabe
# en qué píxel quedó cada dígito. Se recorta cada uno de la imagen comprimida y
# se compara contra las diez plantillas del mismo tipo de letra y el mismo
# tamaño; gana la de menor diferencia.
#
# EL MAPA ES LO QUE LO HACE HONESTO. Las coordenadas no se calculan a mano: se
# pinta una imagen del mismo tamaño que la foto con la caja de cada dígito
# rellena de un gris propio, y se le hace lo MISMO que a la foto. Así sirve
# igual para las fotos acostadas —si el compresor no aplicara el giro, los
# recortes caerían en el sitio equivocado y la prueba fallaría, que es justo lo
# que tiene que pasar.
#
# ESTA MEDICIÓN ES LA QUE HAY QUE VOLVER A CORRER cuando se toque cómo se
# decodifica. `Image.draft` cambia la calidad de la decodificación (libjpeg
# reconstruye a 1/2, 1/4 o 1/8 desde los coeficientes), así que su efecto sobre
# la prueba se mide, no se supone.
REFERENCIA_BANCARIA = "000283917455"

# Tipos de letra de verdad, que es lo que hay en la pantalla de un banco. Se usan
# los del sistema y la prueba se salta si no están: no se trae un .ttf al
# repositorio por una prueba.
FUENTES_DEL_SISTEMA = (
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

# Los ocho valores de la etiqueta EXIF de orientación y lo que significan.
GIROS_EXIF = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
# Lo que hay que hacerle a los píxeles ANTES de guardarlos para que la etiqueta
# diga la verdad: la foto se guarda ya torcida, como sale del teléfono, y el
# compresor la endereza. Así el mapa de las cajas se queda derecho, y los dígitos
# tienen que salir derechos: si no, es que el giro no se aplicó.
DESTUERCE_EXIF = {2: 2, 3: 3, 4: 4, 5: 5, 6: 8, 7: 7, 8: 6}


def fuentes_disponibles() -> list[str]:
    """Los tipos de letra de verdad que tiene esta máquina."""
    import os

    return [ruta for ruta in FUENTES_DEL_SISTEMA if os.path.exists(ruta)]


def foto_con_referencia(
    ancho: int = 4032,
    alto: int = 3024,
    *,
    ruta_fuente: str,
    orientacion: int | None = None,
) -> tuple[bytes, Image.Image, int]:
    """(bytes de la foto, mapa de las cajas, alto de letra) para medir el OCR."""
    from PIL import ImageFont

    rnd = random.Random(7)

    def capa(division: int) -> Image.Image:
        chico = (max(4, ancho // division), max(4, alto // division))
        crudo = bytes(rnd.getrandbits(8) for _ in range(chico[0] * chico[1] * 3))
        return Image.frombytes("RGB", chico, crudo).resize((ancho, alto), Image.BICUBIC)

    imagen = Image.blend(capa(24), capa(6), 0.5)
    dibujo = ImageDraw.Draw(imagen)
    margen_x, margen_y = ancho // 8, alto // 8
    dibujo.rectangle(
        [margen_x, margen_y, ancho - margen_x, alto - margen_y], fill=(250, 250, 248)
    )

    # La pantalla del banco ocupa tres cuartos del encuadre: la referencia es una
    # línea de ese tamaño, no un texto microscópico.
    tam = max(12, alto // 34)
    letra = ImageFont.truetype(ruta_fuente, tam)
    x0, y0 = margen_x + ancho // 20, margen_y + alto // 8
    dibujo.text((x0, y0), "TRANSFERENCIA EXITOSA", font=letra, fill=(20, 20, 24))
    dibujo.text((x0, y0 + int(tam * 1.6)), "Valor: $ 1.842.500", font=letra, fill=(20, 20, 24))
    y_ref = y0 + int(tam * 3.2)
    dibujo.text((x0, y_ref), "Referencia: ", font=letra, fill=(20, 20, 24))
    x = x0 + dibujo.textlength("Referencia: ", font=letra)

    mapa = Image.new("L", (ancho, alto), 0)
    pintor = ImageDraw.Draw(mapa)
    for i, caracter in enumerate(REFERENCIA_BANCARIA):
        dibujo.text((x, y_ref), caracter, font=letra, fill=(20, 20, 24))
        pintor.rectangle(
            dibujo.textbbox((x, y_ref), caracter, font=letra), fill=(i + 1) * 15
        )
        x += dibujo.textlength(caracter, font=letra)

    ruido = Image.effect_noise((ancho, alto), 14).convert("RGB")
    imagen = Image.blend(imagen, ruido, 0.25)

    buffer = io.BytesIO()
    guardar: dict = {"format": "JPEG", "quality": 92}
    if orientacion is not None:
        imagen = imagen.transpose(GIROS_EXIF[DESTUERCE_EXIF[orientacion]])
        exif = Image.Exif()
        exif[274] = orientacion
        guardar["exif"] = exif
    imagen.save(buffer, **guardar)
    return buffer.getvalue(), mapa, tam


_CAJA_OCR = (24, 34)


def _normalizar_recorte(recorte: Image.Image) -> Image.Image:
    """Gris, estirado a blanco y negro y llevado a un tamaño fijo."""
    gris = recorte.convert("L").resize(_CAJA_OCR, Image.LANCZOS)
    bajo, alto = gris.getextrema()
    if alto > bajo:
        gris = gris.point(lambda v: int(255 * (v - bajo) / (alto - bajo)))
    return gris


def _plantillas_de_digitos(ruta_fuente: str, alto_px: int) -> dict:
    from PIL import ImageFont

    letra = ImageFont.truetype(ruta_fuente, max(8, alto_px))
    salida = {}
    for caracter in "0123456789":
        lienzo = Image.new("L", (alto_px * 3, int(alto_px * 2.2)), 255)
        dibujo = ImageDraw.Draw(lienzo)
        posicion = (alto_px // 2, alto_px // 2)
        dibujo.text(posicion, caracter, font=letra, fill=0)
        salida[caracter] = _normalizar_recorte(
            lienzo.crop(dibujo.textbbox(posicion, caracter, font=letra))
        )
    return salida


def leer_la_referencia(
    comprimida: bytes, mapa: Image.Image, alto_letra: int, ruta_fuente: str
) -> tuple[int, str]:
    """(cuántos de los 12 dígitos se leen bien, lo que se leyó)."""
    from PIL import ImageChops, ImageStat

    imagen = Image.open(io.BytesIO(comprimida)).convert("L")
    factor = imagen.height / mapa.height
    marcas = mapa.resize(imagen.size, Image.NEAREST)
    plantillas = _plantillas_de_digitos(ruta_fuente, max(8, round(alto_letra * factor)))

    aciertos, leido = 0, []
    for i, esperado in enumerate(REFERENCIA_BANCARIA):
        caja = marcas.point(lambda v, k=(i + 1) * 15: 255 if v == k else 0).getbbox()
        if caja is None:
            leido.append("?")
            continue
        x0, y0, x1, y1 = caja
        normalizado = _normalizar_recorte(imagen.crop((x0 - 1, y0 - 1, x1 + 1, y1 + 1)))
        mejor, mejor_diferencia = "?", float("inf")
        for caracter, plantilla in plantillas.items():
            diferencia = ImageStat.Stat(
                ImageChops.difference(normalizado, plantilla)
            ).sum[0]
            if diferencia < mejor_diferencia:
                mejor, mejor_diferencia = caracter, diferencia
        leido.append(mejor)
        aciertos += mejor == esperado
    return aciertos, "".join(leido)
