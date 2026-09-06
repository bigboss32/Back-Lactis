"""La compresión de los soportes de pago, medida aparte y con cifras.

POR QUÉ EXISTE ESTO. El dueño lo pidió pegado a lo otro: "que se le puedan
agregar los comprobantes a los pagos de los proveedores, y de paso también le
reducimos la calidad para ahorrar espacio". Lo segundo es plata: el bucket de
Cloudflare se paga por lo que ocupa, y una foto de comprobante sale del celular
pesando entre 3 y 5 MB.

LA TENSIÓN QUE MIDEN ESTAS PRUEBAS. Un comprobante es EVIDENCIA: tiene que
seguir leyéndose el monto, la cuenta y la referencia. Apretar más ahorra unos KB
y destruye la prueba. Así que no basta con comprobar que el archivo pesa menos:
se comprueba TAMBIÉN que la imagen que queda no se aleja de la original (ver
`test_la_calidad_75_no_destruye_el_comprobante`), que la referencia bancaria SE
SIGUE LEYENDO dígito por dígito (`test_la_referencia_bancaria_se_sigue_leyendo`,
un OCR por plantilla), que no queda acostada, y que a una foto que ya venía chica
no se le toca ni un byte.

Y LA OTRA MITAD ES LA MEMORIA, que es la que tumba el servidor. Hay tres
palancas y cada una tiene su prueba, porque ninguna arregla lo que arreglan las
otras dos:

  · MENOS COPIAS: la foto se decodifica ya reducida y se gira y se convierte
    cuando ya está chica (`test_una_foto_de_celular_no_se_decodifica_completa`,
    `test_las_tres_palancas_bajaron_la_memoria_de_verdad`).
  · UN TOPE GRADUADO por lo que cuesta cada formato, no por corazonada
    (`test_el_presupuesto_de_memoria_esta_escrito_y_cuadra`,
    `test_la_bomba_webp_de_tres_kilobytes_rebota`).
  · UNA FILA para que varias subidas a la vez no se multipliquen
    (`test_ocho_subidas_a_la_vez_no_piden_ocho_veces_la_memoria`,
    `test_la_fila_deja_pasar_exactamente_las_configuradas`).

Las pruebas de memoria miden el PICO con un muestreador (`_pico_mientras`) y no
la resta de antes y después: restar da CERO cuando la imagen se decodificó y se
soltó, que es justo el caso que hay que atrapar.

Van aparte de las pruebas de subir/borrar a propósito: aquí no hay base de datos,
ni sesión, ni R2. Es la función sola, con bytes de entrada y bytes de salida.
"""
import io
import sys
import threading
import time

import pytest
from PIL import Image, ImageDraw

from app.core.exceptions import BusinessError
from app.core.imagenes import (
    CALIDAD_JPEG,
    COSTO_MB_POR_MEGAPIXEL,
    COSTO_MB_POR_MEGAPIXEL_DESCONOCIDO,
    LADO_MAYOR_MAX,
    MAX_PIXELES_SOPORTE,
    MAX_PIXELES_SOPORTE_ABSOLUTO,
    PRESUPUESTO_MB_POR_IMAGEN,
    SUBIDAS_A_LA_VEZ,
    TECHO_EXTRA_MB,
    _con_extension,
    como_esta_el_techo,
    comprimir_soporte,
    detectar_tipo,
    hay_obrero,
    leer_y_validar_soporte,
    pillow_disponible,
)
from tests.ayudas_imagenes import (
    EJECUTABLE,
    HEIC_ROTA,
    JPEG,
    PDF,
    PNG,
    REFERENCIA_BANCARIA,
    foto_con_referencia,
    fuentes_disponibles,
    jpeg_progresivo,
    leer_la_referencia,
    bomba_de_pixeles,
    bomba_webp,
    captura_png_rgba,
    foto_chica_acostada,
    foto_con_ubicacion,
    foto_de_celular,
    foto_de_comprobante,
    foto_heic_chiquita,
    foto_heic_de_iphone,
    heic_se_puede_abrir,
)


def _abrir(datos: bytes) -> Image.Image:
    return Image.open(io.BytesIO(datos))


class _Subido:
    """Lo mínimo que `leer_y_validar_soporte` le pide a un archivo de FastAPI."""

    def __init__(self, filename: str, contenido: bytes):
        self.filename = filename
        self.file = io.BytesIO(contenido)


def _archivo(nombre: str, contenido: bytes) -> _Subido:
    return _Subido(nombre, contenido)


def _gps(datos: bytes):
    """Las coordenadas pegadas a la foto, o {} si no trae ninguna."""
    return dict(_abrir(datos).getexif().get_ifd(0x8825))


def _pico_mientras(hacer) -> tuple:
    """(lo que devolvió, el PICO de memoria en MB por encima de donde arrancó).

    SE MUESTREA MIENTRAS CORRE, no se resta al final, y esa diferencia es lo que
    hace que estas pruebas sirvan de algo. Restar el RSS de después menos el de
    antes da CERO cuando la imagen se decodificó y se soltó — o sea, justo en el
    caso que hay que atrapar. El pico sí lo ve.
    """
    proceso = pytest.importorskip("psutil").Process()
    marca = {"max": 0.0}
    parar = threading.Event()

    def vigilar():
        while not parar.is_set():
            marca["max"] = max(marca["max"], proceso.memory_info().rss)
            time.sleep(0.002)

    base = proceso.memory_info().rss
    marca["max"] = base
    hilo = threading.Thread(target=vigilar, daemon=True)
    hilo.start()
    try:
        devuelto = hacer()
    finally:
        parar.set()
        hilo.join()
    return devuelto, (marca["max"] - base) / 1e6


def _kb(n: int) -> str:
    return f"{n / 1024:,.0f} KB".replace(",", ".")


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MB".replace(".", ",")


# ===========================================================================
# EL TOPE DE PÍXELES: la imagen que dejaba sin API a las dos queseras
# ===========================================================================
def test_una_bomba_de_pixeles_rebota_sin_tumbar_nada():
    """EL HALLAZGO, reproducido y tapado.

    Un PNG de 12.000 × 12.000 de un gris plano pesa unos 157 KB —pasa cómodo por
    debajo de los 15 MB de `ADJUNTOS_MAX_MB`, que es el ÚNICO tope que había— y al
    abrirlo se lleva 700 MB de memoria. Las dos queseras comparten servidor: una
    sola foto así las deja a LAS DOS sin API, y no hace falta mala intención,
    basta una captura rara.

    El tope de bytes no podía verlo porque mide el archivo COMPRIMIDO. Lo que hay
    ahora es un tope de PÍXELES que se mira ANTES de decodificar: `Image.open` solo
    lee la cabecera, así que ahí ya se sabe el tamaño y todavía no se ha pagado la
    memoria. Y no sale un 500 ni un mensaje en inglés sobre un ataque: sale uno que
    el dueño entiende y que le dice qué hacer.
    """
    bomba = bomba_de_pixeles(12000, 12000)
    with pytest.raises(BusinessError) as error:
        comprimir_soporte(bomba, "image/png", nombre="captura.png")

    mensaje = str(error.value)
    print("\n===== LA BOMBA DE PÍXELES REBOTA =====")
    print(f"  entra: 12.000 × 12.000 px (144 megapíxeles) · {_kb(len(bomba))} en disco")
    print(f"  o sea: pasa el tope de 15 MB por bytes sin despeinarse")
    print(f"  {mensaje}")
    assert "captura.png" in mensaje
    # Le dice las medidas de verdad: "es demasiado grande" sobre un archivo de
    # 157 KB no se entiende y suena a error del sistema.
    assert "12.000" in mensaje and "144" in mensaje
    # Esta rebota por el TOPE DURO —144 megapíxeles pasan de los 80 que se abren
    # sea cual sea el formato—, así que el mensaje dice ese número.
    assert str(MAX_PIXELES_SOPORTE_ABSOLUTO // 1_000_000) in mensaje
    assert "foto normal" in mensaje or "PDF" in mensaje

    # Y la que queda POR DEBAJO del tope duro rebota igual, por el presupuesto, y
    # diciendo el tope DE SU FORMATO: un WEBP cuesta cuatro veces lo que un PNG
    # del mismo tamaño, así que mandar al dueño a encoger a una medida que no es
    # la suya lo haría rebotar una segunda vez.
    mediana = bomba_de_pixeles(7000, 7000)  # 49 megapíxeles: caben en los 80
    with pytest.raises(BusinessError) as otro:
        comprimir_soporte(mediana, "image/png", nombre="mediana.png")
    tope_png = int(PRESUPUESTO_MB_POR_IMAGEN / COSTO_MB_POR_MEGAPIXEL["PNG"])
    print(f"  y la de 7.000 × 7.000 (49 MP), que sí cabe en el tope duro:")
    print(f"  {otro.value}")
    assert str(tope_png) in str(otro.value)


def test_la_bomba_ni_siquiera_se_decodifica():
    """Que rebote no basta: tiene que rebotar SIN abrirla.

    Si se rechazara después de decodificar, el mensaje saldría bonito y el servidor
    se habría caído igual — que es exactamente el defecto. Se mide la memoria del
    proceso: la bomba ocupa 700 MB al abrirla, así que si el rechazo ocurre antes,
    la subida tiene que ser de unos pocos MB.
    """
    bomba = bomba_de_pixeles(12000, 12000)

    def rebotar():
        with pytest.raises(BusinessError):
            comprimir_soporte(bomba, "image/png", nombre="captura.png")

    _, subida = _pico_mientras(rebotar)

    print("\n===== SE RECHAZA ANTES DE DECODIFICAR =====")
    print(f"  memoria que se llevó el rechazo: {subida:.1f} MB")
    print(f"  (decodificarla costaba unos 700 MB)")
    assert subida < 100, (
        "la imagen se decodificó antes de rechazarla: el mensaje es bonito pero "
        "el servidor ya se cayó"
    )


def test_una_imagen_todavia_mas_grande_tambien_rebota_entendible():
    """El otro borde, el de arriba. Pasado el DOBLE de `Image.MAX_IMAGE_PIXELS`,
    Pillow revienta por su cuenta desde adentro de `Image.open`, antes de que
    nuestro tope alcance a mirar nada. Ese error suyo está en inglés y habla de un
    "decompression bomb DOS attack": no es lo que el dueño tiene que leer cuando
    mandó una captura rara. Sale traducido al mismo mensaje.
    """
    bomba = bomba_de_pixeles(24000, 24000)  # 576 megapíxeles
    with pytest.raises(BusinessError) as error:
        comprimir_soporte(bomba, "image/png", nombre="escaneo.png")
    mensaje = str(error.value)
    print("\n===== LA BOMBA ENORME (576 MEGAPÍXELES) =====")
    print(f"  {_kb(len(bomba))} en disco · {mensaje}")
    assert "escaneo.png" in mensaje
    assert "demasiado grande" in mensaje
    assert "attack" not in mensaje and "pixels" not in mensaje


@pytest.mark.parametrize(
    "ancho,alto,etiqueta",
    [
        (4032, 3024, "celular 12 MP, horizontal"),
        (3024, 4032, "celular 12 MP, vertical"),
        (8000, 6000, "celular 48 MP"),
        (9248, 6936, "celular 64 MP, el sensor más grande que existe hoy"),
        (9000, 6000, "celular 54 MP"),
    ],
)
def test_las_fotos_de_celular_de_verdad_siguen_pasando(ancho, alto, etiqueta):
    """EL TOPE NO PUEDE DEJAR POR FUERA LA FOTO QUE EL DUEÑO MANDA TODOS LOS DÍAS.

    Es la mitad que de verdad importa de un tope: frenar lo absurdo sin frenar lo
    normal. Un tope apretado sería peor que no tener ninguno, porque rompería la
    función que el cliente usa en vez de una que nadie usa.
    """
    foto = foto_de_comprobante(ancho, alto, calidad=92)
    salida, tipo = comprimir_soporte(foto, "image/jpeg", nombre="transferencia.jpg")
    resultado = _abrir(salida)
    print(f"\n===== {etiqueta.upper()} =====")
    print(f"  entra {ancho} × {alto} ({ancho * alto / 1e6:.0f} MP · {_mb(len(foto))}) "
          f"→ sale {resultado.size[0]} × {resultado.size[1]} · {_kb(len(salida))}")
    assert tipo == "image/jpeg"
    assert max(resultado.size) == LADO_MAYOR_MAX


def test_el_presupuesto_de_memoria_esta_escrito_y_cuadra():
    """EL TOPE SE EXPLICA CON MEMORIA, NO CON UNA CORAZONADA. Esta prueba es el
    candado sobre esa cuenta.

    El defecto de la ronda anterior no fue que faltara el tope —estaba, y bien
    puesto, antes de decodificar— sino que el NÚMERO era una corazonada: 80
    millones de puntos son 1,2 GB de memoria, y nadie había hecho esa
    multiplicación. Ahora el tope no se escribe a mano: se DERIVA de un
    presupuesto en megabytes dividido por lo que cuesta un megapíxel de cada
    formato. Si alguien sube el presupuesto, acá se ve de una en cuánta memoria
    se traduce.
    """
    print("\n===== EL PRESUPUESTO DE MEMORIA =====")
    print(f"  presupuesto por imagen:   {PRESUPUESTO_MB_POR_IMAGEN} MB")
    print(f"  subidas a la vez:         {SUBIDAS_A_LA_VEZ}")
    print(f"  lo más que puede pedir la compresión: "
          f"{PRESUPUESTO_MB_POR_IMAGEN * SUBIDAS_A_LA_VEZ} MB")
    for formato, costo in sorted(COSTO_MB_POR_MEGAPIXEL.items()):
        print(f"    {formato:5} {costo:5.1f} MB por megapíxel → tope "
              f"{PRESUPUESTO_MB_POR_IMAGEN / costo:5.1f} megapíxeles")

    # La cuenta que no se había hecho: con el tope viejo de 80 millones y el
    # costo real, UNA sola imagen pedía más de un gigabyte.
    con_el_tope_viejo = 80 * COSTO_MB_POR_MEGAPIXEL["WEBP"]
    print(f"  con el tope viejo (80 megapíxeles) una sola WEBP pedía "
          f"{con_el_tope_viejo:.0f} MB")
    assert con_el_tope_viejo > 1000

    # Y lo que pide ahora, entre todas, tiene que caber en una instancia normal.
    assert PRESUPUESTO_MB_POR_IMAGEN * SUBIDAS_A_LA_VEZ <= 1024, (
        "el presupuesto por el número de turnos se salió de lo que cabe en la "
        "instancia: o baja el presupuesto, o bajan los turnos"
    )
    # La tabla se redondea HACIA ARRIBA sobre lo medido: equivocarse por lo bajo
    # cuesta el proceso, por lo alto solo cuesta que rebote una imagen grande.
    assert min(COSTO_MB_POR_MEGAPIXEL.values()) >= 5
    assert COSTO_MB_POR_MEGAPIXEL_DESCONOCIDO >= max(COSTO_MB_POR_MEGAPIXEL.values()), (
        "un formato que no esté en la tabla tiene que pagar el precio del PEOR"
    )
    # Y el tope duro queda por debajo de la franja ciega de Pillow —89 a 179
    # millones, donde solo avisa y decodifica igual— y por encima de los 64
    # megapíxeles del teléfono más grande que existe hoy.
    print(f"  tope duro: {MAX_PIXELES_SOPORTE_ABSOLUTO / 1e6:.0f} megapíxeles · "
          f"franja ciega de Pillow: 89 a 179")
    assert 9248 * 6936 < MAX_PIXELES_SOPORTE_ABSOLUTO < Image.MAX_IMAGE_PIXELS
    # Y el tope del caso corriente —el que sale en las cuentas de todos los días—
    # deja pasar la captura de 12 megapíxeles con el doble de aire.
    print(f"  tope del caso corriente: {MAX_PIXELES_SOPORTE / 1e6:.0f} megapíxeles "
          f"(la captura de un celular son 12)")
    assert MAX_PIXELES_SOPORTE > 4032 * 3024 * 2, "el tope corriente quedó pegado"
    assert MAX_PIXELES_SOPORTE < Image.MAX_IMAGE_PIXELS


def test_la_bomba_webp_de_tres_kilobytes_rebota():
    """EL HALLAZGO DE ESTA RONDA, reproducido y tapado.

    3.116 BYTES. No kilobytes: bytes. Un WEBP sin pérdida de 16.383 × 4.882 pasaba
    el tope viejo de 80 millones de puntos por dos centésimas, respondía 201 y
    hacía crecer el proceso 1.220 MB. Cuatro a la vez, 4.854 MB. La amplificación
    es de 400.000 veces, y es el mismo camino que usan reventa y los pagos.

    Rebota por el PRESUPUESTO, no por el tope duro: 79,98 megapíxeles caben en los
    80 millones, pero a 16 MB por megapíxel son 1.280 MB y eso no cabe en ningún
    lado.
    """
    bomba = bomba_webp()
    guardado = {}

    def rebotar():
        with pytest.raises(BusinessError) as error:
            comprimir_soporte(bomba, "image/webp", nombre="captura.webp")
        guardado["mensaje"] = str(error.value)

    _, subida = _pico_mientras(rebotar)
    mensaje = guardado["mensaje"]
    print("\n===== LA BOMBA WEBP DE 3.116 BYTES =====")
    print(f"  entra: 16.383 × 4.882 px (79,98 megapíxeles) · {len(bomba)} BYTES")
    print(f"  antes: 201 y +1.220 MB de memoria")
    print(f"  ahora: {mensaje}")
    print(f"  memoria que se llevó el rechazo: {subida:.1f} MB")
    assert len(bomba) < 4096, "la bomba tiene que seguir siendo ridículamente chica"
    assert "captura.webp" in mensaje
    assert str(int(PRESUPUESTO_MB_POR_IMAGEN / COSTO_MB_POR_MEGAPIXEL["WEBP"])) in mensaje
    assert subida < 100, (
        "la bomba se decodificó antes de rechazarla: el mensaje es bonito pero "
        "el proceso ya se cayó"
    )


def test_una_captura_png_de_cuatro_canales_ya_no_pide_un_gigabyte():
    """El caso SIN MALA INTENCIÓN, que es el que de verdad iba a pasar.

    Una captura PNG con canal alfa de 8.944 × 8.944 pesa 330 KB y pedía 1.224 MB.
    No es una bomba: es una captura grande. Con el presupuesto graduado rebota
    diciendo su tope, y la que SÍ pasa —12 megapíxeles, la captura de una
    tableta— cuesta hoy la octava parte de lo que costaba.
    """
    enorme = captura_png_rgba(8944, 8944)
    guardado = {}

    def rebotar():
        with pytest.raises(BusinessError) as error:
            comprimir_soporte(enorme, "image/png", nombre="pantalla.png")
        guardado["mensaje"] = str(error.value)

    _, subida = _pico_mientras(rebotar)

    print("\n===== LA CAPTURA PNG DE CUATRO CANALES =====")
    print(f"  8.944 × 8.944 RGBA (80 megapíxeles): {guardado['mensaje']}")
    print(f"  memoria que se llevó el rechazo: {subida:.1f} MB "
          f"(decodificarla costaba 306 MB)")
    assert subida < 100, (
        "la captura se decodificó antes de rechazarla. OJO CON LA EXIF: "
        "preguntarle la EXIF a un PNG lo decodifica entero, así que esa pregunta "
        "tiene que quedar DESPUÉS del presupuesto, no antes"
    )

    chica = captura_png_rgba(4032, 3024)
    salida, tipo = comprimir_soporte(chica, "image/png", nombre="pantalla.png")
    print(f"  4.032 × 3.024 RGBA (12 megapíxeles): pasa · {_kb(len(chica))} → "
          f"{_kb(len(salida))} {tipo}")
    assert tipo == "image/jpeg"
    assert max(_abrir(salida).size) == LADO_MAYOR_MAX


# ===========================================================================
# Cuánto ahorra, con una foto de verdad
# ===========================================================================
def test_cuanto_ahorra_con_una_foto_de_celular():
    """La medición que le importa al dueño: cuánto deja de pagar por el bucket.

    La foto es la que manda desde el campo: 4032 × 3024 (12 megapíxeles), calidad
    92 como la que produce el teléfono, con grano de sensor y detalle en todos los
    tamaños — que es lo que hace que una foto NO se comprima a nada. Si el lienzo
    fuera un color plano, esta prueba mediría una fantasía.
    """
    foto = foto_de_celular()
    comprimida, tipo = comprimir_soporte(foto, "image/jpeg", nombre="transferencia.jpg")

    antes, despues = len(foto), len(comprimida)
    ahorro = 100 * (1 - despues / antes)
    print("\n===== CUÁNTO AHORRA LA COMPRESIÓN =====")
    print(f"  entra:  {_abrir(foto).size[0]} × {_abrir(foto).size[1]} px · {_mb(antes)}")
    print(f"  sale:   {_abrir(comprimida).size[0]} × {_abrir(comprimida).size[1]} px · "
          f"{_kb(despues)} · {tipo}")
    print(f"  ahorro: {ahorro:.1f} %  ({_mb(antes - despues)} menos por cada foto)")
    print(f"  · 100 soportes al mes: {_mb(antes * 100)} contra {_mb(despues * 100)}")

    assert tipo == "image/jpeg"
    assert max(_abrir(comprimida).size) == LADO_MAYOR_MAX
    # Muy por debajo del megabyte: el número que hace que el bucket no crezca
    # sin control. El umbral es holgado a propósito —lo que se fija es el orden
    # de magnitud, no el byte exacto que devuelva una versión de Pillow.
    assert despues < 700 * 1024, "una foto de celular tiene que quedar por debajo de 700 KB"
    assert ahorro > 70, "la compresión tiene que ahorrar la mayor parte del archivo"


def test_la_calidad_75_no_destruye_el_comprobante():
    """Que pese menos no sirve de nada si el monto ya no se lee.

    Se compara el resultado real (1600 px, calidad 75) contra la MISMA imagen
    encogida igual pero guardada sin pérdida. La diferencia media por píxel es lo
    que la compresión le quitó a la imagen: si fuera grande, los dígitos de la
    referencia bancaria serían manchas. Esto es lo que impide que alguien "ahorre
    más" bajando la calidad a 40 sin darse cuenta de lo que rompe.
    """
    from PIL import ImageChops, ImageStat

    foto = foto_de_celular()
    comprimida, _ = comprimir_soporte(foto, "image/jpeg")

    referencia = _abrir(foto).convert("RGB")
    referencia.thumbnail((LADO_MAYOR_MAX, LADO_MAYOR_MAX), Image.LANCZOS)
    diferencia = ImageChops.difference(referencia, _abrir(comprimida).convert("RGB"))
    media = sum(ImageStat.Stat(diferencia).mean) / 3

    print("\n===== LA COMPRESIÓN NO DESTRUYE LA PRUEBA =====")
    print(f"  calidad usada: {CALIDAD_JPEG} · lado mayor: {LADO_MAYOR_MAX} px")
    print(f"  diferencia media contra la misma foto sin pérdida: {media:.2f} de 255")
    assert media < 8, (
        "la imagen comprimida se alejó demasiado de la original: a este nivel "
        "los dígitos de una referencia bancaria empiezan a volverse manchas"
    )


# ===========================================================================
# Lo que NO se toca
# ===========================================================================
def test_el_pdf_del_banco_pasa_derecho():
    """No es una imagen. El comprobante que descarga el banco ya viene liviano y
    re-armarlo sería convertir texto nítido en una foto de texto: es el MEJOR
    soporte que hay y se guarda tal como llegó, byte por byte."""
    salida, tipo = comprimir_soporte(PDF, "application/pdf", nombre="comprobante.pdf")
    print("\n===== EL PDF PASA DERECHO =====")
    print(f"  entra {len(PDF)} bytes application/pdf → sale {len(salida)} bytes {tipo}")
    assert salida == PDF
    assert tipo == "application/pdf"


def test_una_imagen_que_ya_esta_chica_no_se_toca_ni_se_agranda():
    """Dos cosas a la vez, y las dos importan.

    NO SE AGRANDA: una foto de 60 × 40 sale de 60 × 40. Estirarla no le agregaría
    un detalle que no tiene y dejaría un archivo más pesado por nada.

    NO SE RE-COMPRIME SIN NECESIDAD: sale el MISMO archivo, byte por byte, con su
    tipo original. Pasar un JPEG dos veces por el compresor le quita calidad una
    segunda vez, y hacerlo para ahorrar unos kilobytes es un mal negocio cuando lo
    que se guarda es la prueba de que se pagó.
    """
    print("\n===== LA IMAGEN CHICA SE QUEDA COMO ESTÁ =====")
    for datos, tipo_entra, etiqueta in (
        (JPEG, "image/jpeg", "JPEG 60 × 40"),
        (PNG, "image/png", "PNG 60 × 40"),
    ):
        salida, tipo = comprimir_soporte(datos, tipo_entra, nombre="chiquita")
        print(f"  {etiqueta:14} {len(datos):5} bytes {tipo_entra:10} → "
              f"{len(salida):5} bytes {tipo:10} · idéntico: {salida == datos}")
        assert salida == datos, "se re-comprimió una imagen que ya estaba chica"
        assert tipo == tipo_entra
        assert _abrir(salida).size == _abrir(datos).size


def test_una_foto_apenas_por_debajo_del_tope_conserva_su_tamano():
    """1600 px es el TOPE, no una meta: una foto de 1400 px no se estira a 1600."""
    foto = foto_de_comprobante(1400, 1050, calidad=95)
    salida, _ = comprimir_soporte(foto, "image/jpeg")
    print("\n===== EL TOPE NO ES UNA META =====")
    print(f"  entra 1400 × 1050 ({_kb(len(foto))}) → sale "
          f"{_abrir(salida).size[0]} × {_abrir(salida).size[1]} ({_kb(len(salida))})")
    assert _abrir(salida).size == (1400, 1050)


# ===========================================================================
# La orientación: que el comprobante no quede acostado
# ===========================================================================
def test_la_foto_del_celular_no_queda_acostada():
    """Las fotos verticales de un celular vienen guardadas ACOSTADAS, con una
    etiqueta EXIF que dice "gírela 90°". Pillow NO la aplica sola: sin
    `exif_transpose`, la foto se re-guarda de lado y el EXIF se pierde en el
    camino, así que el comprobante queda acostado para siempre — ilegible justo
    cuando hay que leerle el monto.

    Se comprueban las dos mitades: que los PÍXELES quedaron girados (1200 × 900
    entra, 900 × 1200 sale) y que la etiqueta NO quedó puesta encima del giro ya
    aplicado, que giraría la foto una segunda vez al mostrarla.
    """
    acostada = foto_de_comprobante(1200, 900, orientacion=6)  # 6 = girar 90°
    assert _abrir(acostada).getexif().get(274) == 6
    assert _abrir(acostada).size == (1200, 900)

    salida, _ = comprimir_soporte(acostada, "image/jpeg", nombre="giro.jpg")
    resultado = _abrir(salida)
    print("\n===== LA FOTO NO SE VOLTEA =====")
    print(f"  entra 1200 × 900 con EXIF orientación 6 (acostada)")
    print(f"  sale  {resultado.size[0]} × {resultado.size[1]} con EXIF orientación "
          f"{resultado.getexif().get(274)}")
    assert resultado.size == (900, 1200), "no se aplicó el giro de la EXIF"
    assert resultado.getexif().get(274) in (None, 1), (
        "quedó la etiqueta de giro sobre una imagen YA girada: se volvería a girar"
    )


def test_una_foto_derecha_no_se_gira():
    """El otro lado de lo mismo: sin etiqueta de giro, la foto sale como entró."""
    derecha = foto_de_comprobante(1800, 1200)
    salida, _ = comprimir_soporte(derecha, "image/jpeg")
    print("\n===== LA FOTO DERECHA SE QUEDA DERECHA =====")
    print(f"  entra 1800 × 1200 → sale {_abrir(salida).size}")
    assert _abrir(salida).size == (1600, 1067)  # encogida, no girada


# ===========================================================================
# Lo que no se puede abrir: mensaje, nunca un 500
# ===========================================================================
def test_una_imagen_que_no_se_puede_abrir_se_rechaza_con_mensaje():
    """Un archivo que dice ser JPEG y no lo es (llegó a medias, se dañó en la
    subida). No se guarda crudo: si ni el servidor lo puede abrir, tampoco lo va
    a poder abrir quien reciba el enlace, y quedaría ocupando espacio siendo un
    soporte que no muestra nada. Y no revienta con un 500: sale un mensaje que
    dice qué hacer.
    """
    falso = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x11" * 400
    with pytest.raises(BusinessError) as error:
        comprimir_soporte(falso, "image/jpeg", nombre="transferencia.jpg")
    print("\n===== IMAGEN QUE NO SE PUEDE ABRIR =====")
    print(f"  {error.value}")
    assert "transferencia.jpg" in str(error.value)
    assert "PDF" in str(error.value) or "dañada" in str(error.value)


# ===========================================================================
# Las fotos de iPhone (HEIC): entran y se comprimen
# ===========================================================================
def test_la_foto_de_iphone_entra_y_se_comprime():
    """LO QUE EL DUEÑO HACE TODOS LOS DÍAS: le toma la foto a la transferencia con
    el celular. El iPhone graba en HEIC de fábrica.

    Antes esa foto se RECHAZABA —Pillow sola no abre HEIC— y el mensaje lo mandaba
    a los Ajustes del teléfono a cambiarle el formato a la cámara: trabajo de más,
    todos los días, para el que ya está haciendo el favor de mandar el soporte. Con
    `pillow-heif` declarada en requirements.txt entra por el MISMO camino que las
    demás: se abre, se le aplica el giro, se encoge a 1600 px y se guarda como
    JPEG, que además es lo que el navegador del productor sabe dibujar.
    """
    assert heic_se_puede_abrir(), (
        "falta pillow-heif: las fotos de iPhone no se están probando de verdad"
    )
    heic = foto_heic_de_iphone(4032, 3024)
    assert detectar_tipo(heic[:64]) == "image/heic", "no se reconoció como HEIC"

    salida, tipo = comprimir_soporte(heic, "image/heic", nombre="IMG_4417.HEIC")
    resultado = _abrir(salida)
    print("\n===== LA FOTO DE iPHONE (HEIC) ENTRA Y SE COMPRIME =====")
    print(f"  entra: 4032 × 3024 HEIC · {_mb(len(heic))}")
    print(f"  sale:  {resultado.size[0]} × {resultado.size[1]} {resultado.format} · "
          f"{_kb(len(salida))} · {tipo}")
    assert tipo == "image/jpeg", "la HEIC tiene que salir guardada como JPEG"
    assert resultado.format == "JPEG"
    assert max(resultado.size) == LADO_MAYOR_MAX
    assert len(salida) < len(heic), "la HEIC no se comprimió"


def test_la_foto_de_iphone_se_guarda_con_nombre_de_jpg():
    """El otro lado de lo mismo: si adentro quedó un JPEG, el nombre lo dice.

    "IMG_4417.HEIC" es lo que manda el teléfono, pero lo que se guarda es un JPEG.
    Ese nombre es el que viaja en el enlace firmado, así que si siguiera diciendo
    .HEIC el productor se bajaría del WhatsApp un archivo que su computador no
    sabe abrir por creerle a la extensión.
    """
    archivo = _archivo("IMG_4417.HEIC", foto_heic_de_iphone(2400, 1800))
    contenido, tipo, extension, nombre = leer_y_validar_soporte(
        archivo, max_bytes=15 * 1024 * 1024, max_mb=15
    )
    print("\n===== EL NOMBRE DICE LO QUE HAY ADENTRO =====")
    print(f"  entra «IMG_4417.HEIC» → sale «{nombre}» · {tipo} · {extension}")
    assert (tipo, extension, nombre) == ("image/jpeg", ".jpg", "IMG_4417.jpg")
    assert _abrir(contenido).format == "JPEG"


def test_si_faltara_pillow_heif_el_mensaje_sigue_diciendo_que_hacer(monkeypatch):
    """La red de abajo: el día que la librería no esté —un despliegue a medias, una
    imagen construida sin ella—, la foto vuelve a rebotar, pero NUNCA con un error
    seco. El mensaje tiene que decirle al dueño CÓMO mandar la foto.

    Se simulan las dos mitades a la vez: una HEIC que no se puede abrir y un
    servidor que dice no tener con qué abrirla.
    """
    monkeypatch.setattr("app.core.imagenes.heic_disponible", lambda: False)
    with pytest.raises(BusinessError) as error:
        comprimir_soporte(HEIC_ROTA, "image/heic", nombre="IMG_4417.HEIC")
    mensaje = str(error.value)
    print("\n===== SIN pillow-heif EN EL SERVIDOR =====")
    print(f"  {mensaje}")
    assert "IMG_4417.HEIC" in mensaje
    assert "iPhone" in mensaje
    # Le dice qué hacer, no solo que no se pudo.
    assert "JPG" in mensaje and ("Compatible" in mensaje or "WhatsApp" in mensaje)


def test_una_heic_dañada_no_manda_a_cambiar_el_formato_del_telefono():
    """Y el revés del revés. Teniendo la librería, una HEIC que no abre es una foto
    DAÑADA, no un formato desconocido: decirle "cámbiele el formato en Ajustes"
    sería mandarlo a arreglar lo que no está roto, y volvería a fallar igual.
    """
    assert heic_se_puede_abrir()
    with pytest.raises(BusinessError) as error:
        comprimir_soporte(HEIC_ROTA, "image/heic", nombre="IMG_9999.HEIC")
    mensaje = str(error.value)
    print("\n===== UNA HEIC DAÑADA, CON LA LIBRERÍA PUESTA =====")
    print(f"  {mensaje}")
    assert "Ajustes" not in mensaje and "iPhone" not in mensaje
    assert "dañada" in mensaje or "PDF" in mensaje


# ===========================================================================
# La ubicación NO se queda pegada al soporte de un pago
# ===========================================================================
def test_la_foto_grande_no_se_lleva_el_gps_al_bucket():
    """El camino normal: la foto se re-guarda y la EXIF se queda por fuera entera,
    con el GPS adentro. Un comprobante de pago se manda por WhatsApp al productor;
    la ubicación de dónde estaba el dueño cuando tomó la foto no tiene por qué
    viajar con él.
    """
    con_gps = foto_de_comprobante(2400, 1800)
    # Se le pega el GPS a una foto grande re-armándola con la EXIF de la chiquita.
    imagen = _abrir(con_gps)
    buffer = io.BytesIO()
    imagen.save(buffer, format="JPEG", quality=92, exif=_abrir(foto_con_ubicacion()).getexif())
    con_gps = buffer.getvalue()
    assert _gps(con_gps), "la foto de prueba no quedó con GPS: no se está probando nada"

    salida, _ = comprimir_soporte(con_gps, "image/jpeg", nombre="transferencia.jpg")
    print("\n===== EL GPS NO LLEGA AL BUCKET (foto grande) =====")
    print(f"  entra con GPS {_gps(con_gps)}")
    print(f"  sale  con GPS {_gps(salida) or '{}'} · {_kb(len(salida))}")
    assert not _gps(salida)


def test_la_foto_chica_con_gps_tampoco_se_lleva_la_ubicacion():
    """EL CAMINO QUE SE ESCAPABA. Cuando no vale la pena re-comprimir se devolvía
    EL ORIGINAL byte por byte — y el original trae la EXIF completa, GPS incluido.
    O sea que la promesa de arriba era cierta para las fotos grandes y mentira para
    las chiquitas, que es la peor forma de tenerla: escrita y falsa la mitad de las
    veces.

    Ahora, si la foto trae ubicación, ese atajo NO se toma: se guarda la versión
    re-codificada aunque no ahorre nada. Cuesta una re-compresión sobre una imagen
    que ya es chiquita; la ubicación de una finca vale más que eso.
    """
    con_gps = foto_con_ubicacion()
    assert _gps(con_gps), "la foto de prueba no quedó con GPS"

    salida, tipo = comprimir_soporte(con_gps, "image/jpeg", nombre="finca.jpg")
    print("\n===== EL GPS NO LLEGA AL BUCKET (foto chica, el camino corto) =====")
    print(f"  entra {len(con_gps)} bytes con GPS {_gps(con_gps)}")
    print(f"  sale  {len(salida)} bytes con GPS {_gps(salida) or '{}'} · {tipo}")
    assert not _gps(salida), "el atajo devolvió el original con la ubicación pegada"
    assert salida != con_gps, "se devolvió el original tal cual, con su EXIF"


def test_una_foto_chica_SIN_gps_si_se_sigue_devolviendo_intacta():
    """Y que arreglar lo de arriba no haya roto lo de al lado: la foto chiquita que
    NO trae ubicación sigue saliendo byte por byte igual. Re-comprimirla le quitaría
    calidad una segunda vez a la prueba de un pago para no ganar nada.
    """
    salida, tipo = comprimir_soporte(JPEG, "image/jpeg", nombre="chiquita.jpg")
    print("\n===== SIN GPS, EL ATAJO SIGUE INTACTO =====")
    print(f"  {len(JPEG)} bytes → {len(salida)} bytes · idéntico: {salida == JPEG}")
    assert salida == JPEG and tipo == "image/jpeg"


# ===========================================================================
# El nombre con el que se descarga dice lo que hay adentro
# ===========================================================================
def test_una_captura_png_que_se_guarda_como_jpeg_cambia_de_nombre():
    """SE SUBE "captura.png", SE GUARDA UN JPEG, Y EL NOMBRE DECÍA .png.

    Ese nombre no es decorativo: es el `nombre_descarga` que viaja dentro del
    enlace firmado, o sea el nombre con el que el archivo aterriza en el computador
    del productor cuando abre lo que le mandaron por WhatsApp. Con la extensión
    equivocada, se baja algo que su equipo no sabe abrir por creerle al nombre — y
    no hay a quién preguntarle, porque el enlace ya caducó.
    """
    captura = foto_de_comprobante(2400, 1800)
    buffer = io.BytesIO()
    _abrir(captura).save(buffer, format="PNG")
    archivo = _archivo("captura.png", buffer.getvalue())

    contenido, tipo, extension, nombre = leer_y_validar_soporte(
        archivo, max_bytes=15 * 1024 * 1024, max_mb=15
    )
    print("\n===== EL NOMBRE NO MIENTE =====")
    print(f"  entra «captura.png» image/png")
    print(f"  sale  «{nombre}» {tipo} {extension} · adentro hay un "
          f"{_abrir(contenido).format}")
    assert (tipo, extension, nombre) == ("image/jpeg", ".jpg", "captura.jpg")
    assert _abrir(contenido).format == "JPEG"


def test_el_que_no_cambia_de_tipo_conserva_su_nombre_tal_cual():
    """Solo se toca el nombre cuando el tipo CAMBIÓ. Un "comprobante.jpeg" que
    sigue siendo JPEG no se renombra a ".jpg" por gusto —es el nombre que el dueño
    le puso— y un PDF menos todavía.
    """
    print("\n===== LO QUE NO CAMBIÓ, NO SE RENOMBRA =====")
    for nombre_entra, datos, tipo_espera, nombre_espera in (
        ("comprobante.jpeg", JPEG, "image/jpeg", "comprobante.jpeg"),
        ("banco.pdf", PDF, "application/pdf", "banco.pdf"),
        ("sin_extension", JPEG, "image/jpeg", "sin_extension"),
    ):
        _, tipo, _, nombre = leer_y_validar_soporte(
            _archivo(nombre_entra, datos), max_bytes=15 * 1024 * 1024, max_mb=15
        )
        print(f"  «{nombre_entra}» → «{nombre}» ({tipo})")
        assert (tipo, nombre) == (tipo_espera, nombre_espera)


def test_un_ejecutable_disfrazado_de_foto_tampoco_se_abre():
    """Llega hasta acá solo si alguien lo llamara imagen: el reconocimiento por
    los primeros bytes ya lo para antes (ver test_reventa_adjuntos). Se prueba
    igual, porque lo que no puede pasar en ningún camino es que un ejecutable
    termine guardado en el bucket con un enlace que el dueño reparte."""
    with pytest.raises(BusinessError):
        comprimir_soporte(EJECUTABLE, "image/jpeg", nombre="foto.jpg")


# ===========================================================================
# Bordes
# ===========================================================================
def test_una_captura_con_transparencia_no_queda_en_negro():
    """La captura de la app del banco suele traer fondo transparente. Un JPEG no
    guarda transparencia, y Pillow la aplana sobre NEGRO por omisión: el texto
    negro del monto desaparecería y se guardaría un soporte en blanco (o más bien
    en negro). Se aplana sobre BLANCO.
    """
    lienzo = Image.new("RGBA", (2000, 1500), (255, 255, 255, 0))
    from PIL import ImageDraw

    ImageDraw.Draw(lienzo).text((80, 80), "Valor: $ 1.842.500", fill=(0, 0, 0, 255))
    buffer = io.BytesIO()
    lienzo.save(buffer, format="PNG")

    salida, tipo = comprimir_soporte(buffer.getvalue(), "image/png")
    esquina = _abrir(salida).convert("RGB").getpixel((5, 5))
    print("\n===== TRANSPARENCIA =====")
    print(f"  PNG transparente {len(buffer.getvalue())} bytes → {tipo} {len(salida)} bytes")
    print(f"  el fondo quedó en RGB{esquina}")
    assert min(esquina) > 200, "la transparencia se aplanó sobre negro: el texto se pierde"


# ===========================================================================
# Sin Pillow: hasta dónde llega la red, y hasta dónde NO
# ===========================================================================
def test_sin_pillow_el_soporte_igual_se_sube_sin_comprimir(monkeypatch):
    """Si mañana Pillow no está, adjuntar el comprobante TIENE que seguir
    funcionando: solo se pierde el ahorro de espacio, y queda el aviso en el log.

    Es la diferencia de fondo con boto3, que también se importa perezoso pero cuya
    falta hace imposible el adjunto (no hay dónde guardarlo). Tumbar una función
    que el cliente ya usa por una librería que solo sirve para ahorrar plata sería
    cambiar un problema de plata por uno de trabajo.

    Se prueba por el CAMINO COMPLETO —`leer_y_validar_soporte`, que es por donde
    entra todo soporte, venga de reventa o de un pago de liquidación— y no solo por
    el compresor: lo que importa no es que una función devuelva algo, es que la
    subida entera siga en pie.
    """
    # La foto se arma ANTES de esconder Pillow: armarla también lo necesita, y lo
    # que se está probando es el camino de la aplicación, no el de las ayudas.
    foto = foto_de_comprobante(2400, 1800)
    monkeypatch.setitem(sys.modules, "PIL", None)

    contenido, tipo, extension, nombre = leer_y_validar_soporte(
        _archivo("giro.jpg", foto), max_bytes=15 * 1024 * 1024, max_mb=15
    )
    print("\n===== SIN PILLOW EN EL SERVIDOR =====")
    print(f"  entra {_kb(len(foto))} → sale {_kb(len(contenido))} {tipo} (sin comprimir)")
    print(f"  el nombre y la extensión salen sin tocar: «{nombre}» {extension}")
    assert contenido == foto, "el soporte no se subió tal como llegó"
    assert (tipo, extension, nombre) == ("image/jpeg", ".jpg", "giro.jpg")
    assert not pillow_disponible()


def test_pillow_no_es_opcional_en_lactis_aunque_el_import_sea_perezoso():
    """LA VERDAD QUE HAY QUE DEJAR ESCRITA, porque en requirements.txt estaba al
    revés y alguien la va a volver a escribir.

    El import de `app/core/imagenes.py` es perezoso de verdad —lo prueba el test de
    arriba— pero eso NO quiere decir que Lactis arranque sin Pillow: `app/utils/
    export.py` importa reportlab al cargarse, reportlab hace `from PIL import Image`
    en su propio `lib/utils.py` sin ninguna red, y de export.py cuelgan
    liquidaciones, reventa, transporte, ventas y notificaciones.

    O sea que Pillow SIEMPRE estuvo instalada en el servidor —pip la trae como
    dependencia de reportlab— y la línea de requirements.txt no la agregó: la
    declaró. Lo que la pereza compra es otra cosa, y también vale: poder probar este
    módulo solo, y que el día que reportlab suelte a Pillow la subida de soportes no
    se caiga con ella.

    Esta prueba comprueba las dos mitades del hecho, para que el comentario no
    vuelva a mentir.
    """
    from importlib.metadata import requires

    exigen_pillow = [
        r for r in (requires("reportlab") or []) if r.lower().startswith("pillow")
    ]
    print("\n===== PILLOW NO ES OPCIONAL =====")
    print(f"  reportlab declara: {exigen_pillow}")
    assert exigen_pillow, (
        "reportlab ya no exige Pillow: ahora sí es una dependencia solo nuestra, y "
        "hay que revisar el comentario de requirements.txt (y este mensaje)"
    )

    # Y que reportlab de verdad la importa al cargarse, que es lo que tumba el
    # arranque: no basta con que la declare.
    import reportlab.lib.utils

    print(f"  y la importa al cargarse: reportlab.lib.utils.Image = "
          f"{reportlab.lib.utils.Image.__name__}")
    assert reportlab.lib.utils.Image.__name__ == "PIL.Image", (
        "reportlab dejó de importar Pillow al cargarse: ahora sí valdría la pena "
        "revisar si la aplicación arranca sin ella"
    )


def test_este_entorno_si_tiene_pillow():
    """Aviso temprano: si Pillow desapareciera del entorno, la mitad de estas
    pruebas mediría el camino de "pasa sin comprimir" creyendo que mide otra cosa.
    """
    assert pillow_disponible(), "falta Pillow: la compresión no se está probando"


# ===========================================================================
# PALANCA 1: menos copias. Se le pide la imagen YA REDUCIDA al decodificador
# ===========================================================================
def test_una_foto_de_celular_no_se_decodifica_completa():
    """LA PALANCA GRANDE, medida: la foto de 12 MP nunca se abre entera.

    El destino son 1.600 px, así que abrir los 4.032 completos es trabajo —y
    memoria— tirados a la basura. `Image.draft` le pide a libjpeg que reconstruya
    ya reducido: 4.032 × 3.024 sale del decodificador como 2.016 × 1.512, que es
    la CUARTA parte de los puntos. Y funciona igual de bien con las grandes: una
    de 64 megapíxeles se decodifica a 4 megapíxeles.

    Esto no se puede pedir un renglón más abajo. `thumbnail` también llama a
    `draft`, pero para entonces la imagen ya está decodificada entera y la llamada
    no hace nada.
    """
    from app.core.imagenes import _pedir_la_imagen_ya_reducida

    print("\n===== SE DECODIFICA YA REDUCIDA =====")
    for ancho, alto, esperado in [
        (4032, 3024, (2016, 1512)),
        (9248, 6936, (2312, 1734)),
        (1400, 1050, (1400, 1050)),   # ya está por debajo del tope: no se toca
    ]:
        foto = foto_de_comprobante(ancho, alto, calidad=92)
        abierta = Image.open(io.BytesIO(foto))
        _pedir_la_imagen_ya_reducida(abierta)
        puntos_antes = ancho * alto / 1e6
        puntos_ahora = abierta.size[0] * abierta.size[1] / 1e6
        print(f"  {ancho} × {alto} ({puntos_antes:.1f} MP) → decodifica "
              f"{abierta.size[0]} × {abierta.size[1]} ({puntos_ahora:.1f} MP)")
        assert abierta.size == esperado
        abierta.close()


def test_las_tres_palancas_bajaron_la_memoria_de_verdad():
    """Y que la bajada esté medida, no afirmada.

    Se mide el PICO de memoria del proceso mientras corre la compresión de una
    foto de celular de verdad. Antes de bajar las copias eran 94 MB para una foto
    de 12 megapíxeles —cuatro copias de la imagen abierta: decodificar, la copia
    de `exif_transpose`, la conversión a RGB y el lienzo del aplanado—. El umbral
    de esta prueba es holgado a propósito: lo que se fija es el ORDEN DE MAGNITUD,
    para que nadie vuelva a meter una copia entera sin enterarse.
    """
    foto = foto_de_celular()
    (salida, tipo), subida = _pico_mientras(
        lambda: comprimir_soporte(foto, "image/jpeg", nombre="transferencia.jpg")
    )

    print("\n===== LO QUE CUESTA COMPRIMIR UNA FOTO DE 12 MEGAPÍXELES =====")
    print(f"  antes de bajar las copias: 94 MB de pico")
    print(f"  ahora:                     {subida:.1f} MB de pico")
    print(f"  ({_mb(len(foto))} → {_kb(len(salida))} {tipo})")
    assert tipo == "image/jpeg"
    assert subida < 60, (
        "la compresión volvió a pedir la memoria de antes: alguien metió una "
        "copia de la imagen completa (mire el orden de los pasos en "
        "comprimir_soporte)"
    )


# ===========================================================================
# LO QUE NO SE PUEDE PERDER AL OPTIMIZAR: que el comprobante SE LEA
# ===========================================================================
def test_la_referencia_bancaria_se_sigue_leyendo():
    """LA PRUEBA QUE MANDA SOBRE TODAS LAS OPTIMIZACIONES DE MEMORIA.

    Un comprobante es EVIDENCIA. Que pese menos y que se comprima más rápido no
    sirve de nada si el número de referencia dejó de leerse. Acá se LEE de verdad,
    dígito por dígito, con un OCR por plantilla (ver `tests/ayudas_imagenes.py`).

    ESTA ES LA MEDICIÓN QUE HAY QUE VOLVER A CORRER cuando se toque cómo se
    decodifica. `Image.draft` cambia la calidad de la decodificación —libjpeg
    reconstruye desde los coeficientes a 1/2, 1/4 o 1/8—, así que su efecto se
    mide. Medido: 12 de 12 dígitos antes de ponerlo y 12 de 12 después, en las
    dos fuentes y en todas las medidas; la diferencia media contra la misma foto
    encogida sin pérdida bajó de 5,19 a 5,09 sobre 255.
    """
    fuentes = fuentes_disponibles()
    if not fuentes:
        pytest.skip("esta máquina no tiene tipos de letra de verdad instalados")

    print("\n===== ¿SE SIGUE LEYENDO LA REFERENCIA BANCARIA? =====")
    print(f"  {'fuente':14s} {'medidas':11s} {'giro':5s} {'leído':14s} aciertos")
    total = 0
    casos = 0
    for ruta in fuentes:
        for ancho, alto, giro in [
            (4032, 3024, None),
            (4032, 3024, 6),
            (3024, 4032, None),
            (2400, 1800, None),
            (1200, 900, None),
        ]:
            datos, mapa, alto_letra = foto_con_referencia(
                ancho, alto, ruta_fuente=ruta, orientacion=giro
            )
            comprimida, _ = comprimir_soporte(datos, "image/jpeg", nombre="ref.jpg")
            aciertos, leido = leer_la_referencia(comprimida, mapa, alto_letra, ruta)
            nombre = ruta.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            print(f"  {nombre:14s} {f'{ancho}x{alto}':11s} {str(giro):5s} "
                  f"{leido:14s} {aciertos}/12")
            total += aciertos
            casos += 1
            assert aciertos >= 11, (
                f"la referencia dejó de leerse en {ancho}x{alto} ({nombre}): se "
                f"leyó «{leido}» y era «{REFERENCIA_BANCARIA}». La compresión "
                f"está destruyendo la prueba de un pago"
            )
    print(f"  TOTAL: {total} de {casos * 12} dígitos")
    assert total >= casos * 12 - 1, "se perdió legibilidad en más de un dígito"


@pytest.mark.parametrize("giro", [2, 3, 4, 5, 6, 7, 8])
def test_los_ocho_valores_de_orientacion_se_aplican_a_los_pixeles(giro):
    """LOS OCHO GIROS, y ahora importan el doble.

    El encogido pasó a hacerse ANTES del giro —girar una imagen de 1.600 px no
    cuesta nada, girar una de 4.032 costaba una copia entera—. Eso es correcto
    porque la caja del encogido es CUADRADA (1600 × 1600) y entonces girar y
    encoger conmutan... pero "es correcto porque conmutan" es un argumento, y un
    argumento no es una prueba. Esta sí lo es: se dibuja la referencia bancaria,
    se guarda la foto TORCIDA como la guarda el teléfono, y se comprueba que sale
    derecha leyéndola.
    """
    fuentes = fuentes_disponibles()
    if not fuentes:
        pytest.skip("esta máquina no tiene tipos de letra de verdad instalados")
    datos, mapa, alto_letra = foto_con_referencia(
        2400, 1800, ruta_fuente=fuentes[0], orientacion=giro
    )
    comprimida, _ = comprimir_soporte(datos, "image/jpeg", nombre="torcida.jpg")
    aciertos, leido = leer_la_referencia(comprimida, mapa, alto_letra, fuentes[0])
    resultado = _abrir(comprimida)
    print(f"\n===== ORIENTACIÓN EXIF {giro} =====")
    print(f"  sale {resultado.size[0]} × {resultado.size[1]} · etiqueta "
          f"{resultado.getexif().get(274)} · se leyó «{leido}» ({aciertos}/12)")
    assert aciertos >= 11, "la foto no quedó derecha: la referencia no se lee"
    # Y la etiqueta NO queda puesta sobre una imagen ya girada: giraría dos veces.
    assert resultado.getexif().get(274) in (None, 1)


# ===========================================================================
# PALANCA 3: que varias a la vez no se multipliquen
# ===========================================================================
def test_ocho_subidas_a_la_vez_no_piden_ocho_veces_la_memoria():
    """LA TERCERA PALANCA, y la que no arregla ninguna de las otras dos.

    Bajar las copias y graduar el tope arreglan UNA imagen. Pero los endpoints son
    síncronos y FastAPI los corre en el threadpool de anyio: hasta 40 a la vez en
    el MISMO proceso. Ocho fotos de celular normales —ninguna abusiva— pedían
    734 MB entre todas, y ocho capturas PNG, 1.232 MB.

    La fila (`SUBIDAS_A_LA_VEZ`) deja pasar tres a la vez y las demás esperan su
    turno. NO REBOTA NINGUNA: las ocho se comprimen, solo que de a tres.
    """
    psutil = pytest.importorskip("psutil")
    proceso = psutil.Process()
    foto = foto_de_celular()

    salidas: list = []
    fallos: list = []

    def subir():
        try:
            salidas.append(comprimir_soporte(foto, "image/jpeg", nombre="lote.jpg"))
        except Exception as exc:  # noqa: BLE001
            fallos.append(exc)

    pico = {"max": 0}
    parar = threading.Event()

    def vigilar():
        while not parar.is_set():
            pico["max"] = max(pico["max"], proceso.memory_info().rss)
            time.sleep(0.002)

    base = proceso.memory_info().rss
    pico["max"] = base
    vigilante = threading.Thread(target=vigilar, daemon=True)
    vigilante.start()
    hilos = [threading.Thread(target=subir) for _ in range(8)]
    comenzo = time.perf_counter()
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    tardo = time.perf_counter() - comenzo
    parar.set()
    vigilante.join()
    subida = (pico["max"] - base) / 1e6

    print("\n===== OCHO SUBIDAS A LA VEZ =====")
    print(f"  turnos a la vez: {SUBIDAS_A_LA_VEZ}")
    print(f"  antes: 734 MB de pico")
    print(f"  ahora: {subida:.1f} MB de pico · las ocho en {tardo:.2f} s")
    assert not fallos, f"se rebotó una subida legítima: {fallos[0]}"
    assert len(salidas) == 8, "no se comprimieron las ocho"
    assert all(tipo == "image/jpeg" for _, tipo in salidas)
    assert subida < 400, (
        "ocho subidas normales volvieron a multiplicar la memoria: mire "
        "SUBIDAS_A_LA_VEZ y el presupuesto"
    )
    # Y que la fila no se vuelva una espera eterna: el dueño está en el campo.
    assert tardo < 20, "la fila dejó al dueño esperando demasiado"


def test_la_fila_deja_pasar_exactamente_las_configuradas(monkeypatch):
    """Y que la fila se MIRE, no que se deduzca de la memoria.

    La cifra de memoria es la consecuencia; esto es la causa. Se cuentan los hilos
    que están DENTRO del compresor al mismo tiempo: el máximo tiene que ser
    exactamente `SUBIDAS_A_LA_VEZ`, ni uno más. Sin esta prueba, alguien podría
    quitar el semáforo y la de memoria seguiría pasando el día que las fotos sean
    livianas.

    El contador se pone en `_decodificar_con_techo`, que es LO ÚNICO que corre
    dentro del turno: si se colara un hilo de más, aquí se ve.

    ANTES ESTE CONTADOR ESTABA EN `_listo_para_encoger`, un paso de adentro del
    compresor, y hubo que moverlo el día que la decodificación se fue a otro
    proceso: un contador puesto allá ya no cuenta nada, porque ese renglón ahora
    corre en el obrero y no acá. Contar en el turno es además lo que la prueba
    decía medir desde el principio —lo que el semáforo regula es cuántas subidas
    pasan, no un detalle de adentro del compresor—.
    """
    import app.core.imagenes as modulo

    original = modulo._decodificar_con_techo
    adentro = {"ahora": 0, "max": 0}
    candado = threading.Lock()

    def contando(*args, **kwargs):
        with candado:
            adentro["ahora"] += 1
            adentro["max"] = max(adentro["max"], adentro["ahora"])
        try:
            time.sleep(0.05)  # para que los turnos se solapen de verdad
            return original(*args, **kwargs)
        finally:
            with candado:
                adentro["ahora"] -= 1

    monkeypatch.setattr(modulo, "_decodificar_con_techo", contando)

    foto = foto_de_comprobante(2000, 1500, calidad=90)
    hechas: list = []

    def subir():
        hechas.append(comprimir_soporte(foto, "image/jpeg", nombre="fila.jpg"))

    hilos = [threading.Thread(target=subir) for _ in range(12)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    print("\n===== CUÁNTAS ENTRAN A LA VEZ =====")
    print(f"  turnos configurados: {SUBIDAS_A_LA_VEZ}")
    print(f"  lo más que se vio adentro a la vez: {adentro['max']}")
    print(f"  y se comprimieron las {len(hechas)} de 12")
    assert len(hechas) == 12, "la fila dejó a alguna por fuera"
    assert adentro["max"] <= SUBIDAS_A_LA_VEZ, (
        f"entraron {adentro['max']} a la vez y la fila era de {SUBIDAS_A_LA_VEZ}: "
        "el semáforo no está frenando nada"
    )
    # Y QUE ENTREN LAS QUE SON, no "más de una". Antes esta línea exigía `> 1`,
    # con el número metido a mano, y se cayó sola el día que la fila bajó a un
    # turno por la memoria de la instancia. Lo que hay que exigir es que el
    # semáforo deje pasar EXACTAMENTE lo configurado: ni de más (no frena) ni de
    # menos (el dueño espera sin razón), sea cual sea ese número mañana.
    assert adentro["max"] == SUBIDAS_A_LA_VEZ, (
        f"entraron {adentro['max']} a la vez y la fila era de {SUBIDAS_A_LA_VEZ}"
    )


def test_la_fila_no_rompe_la_subida_de_varios_archivos():
    """El turno se pide y se suelta POR IMAGEN, no por petición.

    Es lo que hace que una subida de 20 fotos —el tope por documento— no se trabe
    contra sí misma ni deje a los demás afuera mientras dura. Si el turno se
    tomara por petición, dos dueños subiendo lotes se bloquearían el uno al otro
    durante todo el lote.

    Se comprueba de la única forma que no miente: subiendo un lote entero por el
    camino de verdad (`leer_y_validar_soporte`, una por una) y viendo que las 20
    salen.
    """
    from app.core.imagenes import SUBIDAS_A_LA_VEZ as turnos

    lote = [_archivo(f"foto{i}.jpg", foto_de_comprobante(1800, 1350, semilla=i))
            for i in range(20)]
    comenzo = time.perf_counter()
    resultados = [
        leer_y_validar_soporte(a, max_bytes=15 * 1024 * 1024, max_mb=15) for a in lote
    ]
    tardo = time.perf_counter() - comenzo
    print("\n===== UN LOTE DE 20 FOTOS =====")
    print(f"  turnos a la vez: {turnos} · las 20 en {tardo:.2f} s")
    assert len(resultados) == 20
    assert all(tipo == "image/jpeg" for _, tipo, _, _ in resultados)


# ===========================================================================
# La HEIC chiquita: el atajo que la guardaba como HEIC
# ===========================================================================
@pytest.mark.skipif(
    not heic_se_puede_abrir(), reason="falta pillow-heif en este entorno"
)
def test_una_heic_chiquita_igual_sale_como_jpeg():
    """EL DEFECTO: la foto de iPhone que se guardaba como HEIC y no se veía.

    El atajo de "no vale la pena re-comprimir" devuelve el ORIGINAL con su tipo
    original, y `AHORRO_MINIMO_BYTES` son 40 KB: así que TODA foto de menos de
    40 KB se devolvía tal cual, HEIC incluida. La de todos los días —recién
    tomada, de 1,5 a 3 MB— sí se convertía; la que se escapaba era la ya recortada
    o ya reducida.

    Lo que eso significaba: el enlace firmado que el dueño manda por WhatsApp
    entregaba un `.heic` servido como `image/heic`, y Chrome —en Android y en
    Windows— NO LO DIBUJA. O sea, exactamente el problema que `pillow-heif` vino
    a resolver, y en contra de lo que dicen `imagenes.py`, `requirements.txt` y la
    pantalla de soportes.

    Ahora una HEIC SIEMPRE se re-escribe, ahorre o no ahorre.
    """
    heic = foto_heic_chiquita()
    salida, tipo = comprimir_soporte(heic, "image/heic", nombre="IMG_0042.HEIC")
    print("\n===== LA HEIC CHIQUITA TAMBIÉN SALE COMO JPEG =====")
    print(f"  entra {_kb(len(heic))} image/heic (por debajo de los 40 KB del atajo)")
    print(f"  sale  {_kb(len(salida))} {tipo} · adentro hay un "
          f"{_abrir(salida).format}")
    assert len(heic) < 40 * 1024, (
        "esta foto tiene que caer en el atajo de 'no vale la pena', que es donde "
        "estaba el defecto"
    )
    assert tipo == "image/jpeg", "se guardó una HEIC que el navegador no dibuja"
    assert _abrir(salida).format == "JPEG"
    # Y el nombre lo dice, porque es el que aterriza en el computador del productor.
    archivo = _archivo("IMG_0042.HEIC", heic)
    _, tipo_final, extension, nombre = leer_y_validar_soporte(
        archivo, max_bytes=15 * 1024 * 1024, max_mb=15
    )
    print(f"  y el nombre: «IMG_0042.HEIC» → «{nombre}» ({tipo_final}, {extension})")
    assert (tipo_final, extension, nombre) == ("image/jpeg", ".jpg", "IMG_0042.jpg")


# ===========================================================================
# La foto chica acostada: el otro lado del mismo atajo
# ===========================================================================
def test_una_foto_chica_acostada_se_endereza_igual():
    """EL DEFECTO GEMELO: la orientación tampoco se aplicaba por el atajo.

    El docstring prometía "la orientación se aplica a los píxeles y no se hereda
    del metadato". Cuando se tomaba el atajo de "no vale la pena re-comprimir" eso
    era FALSO: se devolvían los bytes originales, con los píxeles sin girar y la
    etiqueta 274 adentro. El arreglo del GPS había tapado ese atajo solo para el
    GPS y no para el giro.

    En pantalla no se nota —el `<img>` de Chrome lee la etiqueta y endereza—, así
    que el defecto sobrevive callado hasta que algo que no lee EXIF mira el
    archivo: una miniatura hecha con canvas, un export, un PDF.
    """
    acostada = foto_chica_acostada(800, 600, orientacion=6)
    assert _abrir(acostada).size == (800, 600)
    assert _abrir(acostada).getexif().get(274) == 6

    salida, tipo = comprimir_soporte(acostada, "image/jpeg", nombre="chica.jpg")
    resultado = _abrir(salida)
    print("\n===== LA FOTO CHICA ACOSTADA SE ENDEREZA =====")
    print(f"  entra 800 × 600 con etiqueta 6 · {_kb(len(acostada))}")
    print(f"  sale  {resultado.size[0]} × {resultado.size[1]} · etiqueta "
          f"{resultado.getexif().get(274)} · {_kb(len(salida))} {tipo}")
    assert salida != acostada, "se devolvió el original acostado (el atajo del defecto)"
    assert resultado.size == (600, 800), "no se aplicó el giro a los píxeles"
    assert resultado.getexif().get(274) in (None, 1)


def test_una_foto_chica_DERECHA_si_se_sigue_devolviendo_intacta():
    """Y el otro lado, para que el arreglo no se convierta en "recomprimir todo".

    Sin etiqueta de giro, sin GPS y sin ser HEIC, una foto que ya está chica sale
    IDÉNTICA byte por byte. Ese ahorro es el que evita quitarle calidad una
    segunda vez a la prueba de un pago.
    """
    derecha = foto_chica_acostada(800, 600, orientacion=1)
    salida, tipo = comprimir_soporte(derecha, "image/jpeg", nombre="derecha.jpg")
    print("\n===== LA FOTO CHICA DERECHA NO SE TOCA =====")
    print(f"  {len(derecha)} bytes → {len(salida)} bytes · idéntica: "
          f"{salida == derecha}")
    assert salida == derecha and tipo == "image/jpeg"


# ===========================================================================
# El nombre de descarga: la extensión no se puede perder
# ===========================================================================
def test_un_nombre_de_255_caracteres_no_pierde_la_extension():
    """EL DEFECTO EN LA ESQUINA: el recorte se comía el ".jpg".

    `_con_extension` recortaba a 255 el resultado ENTERO, así que un nombre que ya
    venía con 255 caracteres y sin punto se llevaba por delante la extensión
    completa. Ese nombre es el que viaja como `nombre_descarga` en el enlace
    firmado, o sea el nombre con el que el archivo aterriza en el computador del
    productor: sin extensión, su equipo no sabe con qué abrirlo, y el enlace
    caduca antes de que alcance a preguntar.

    Ahora se recorta la RAÍZ y la extensión se respeta siempre.
    """
    print("\n===== EL NOMBRE LARGO CONSERVA LA EXTENSIÓN =====")
    casos = [
        ("a" * 255, ".jpg"),
        ("b" * 251 + ".png", ".jpg"),
        ("c" * 300, ".jpg"),
        ("captura.png", ".jpg"),
        ("captura", ".jpg"),
        ("reporte.final.png", ".jpg"),
        ("foto.", ".jpg"),
    ]
    for nombre, extension in casos:
        salida = _con_extension(nombre, extension)
        print(f"  ({len(nombre):3} caracteres) → ({len(salida):3}) …{salida[-12:]}")
        assert len(salida) <= 255
        assert salida.endswith(extension), (
            f"se perdió la extensión en un nombre de {len(nombre)} caracteres"
        )
    assert _con_extension("captura.png", ".jpg") == "captura.jpg"
    assert _con_extension("reporte.final.png", ".jpg") == "reporte.final.jpg"


def test_un_escaneo_tramado_no_se_encoge_a_escalones():
    """El detalle que se ve cuando ya es tarde: Pillow ignora el LANCZOS en modo
    paleta y en blanco y negro.

    Una imagen en modo "1" o "P" —el escaneo de una oficina en «texto», un fax,
    un PNG-8— la encoge Pillow SIEMPRE con «el vecino más cercano», pase el
    filtro que se le pase, porque promediar números de paleta no significa nada.
    Sobre un escaneo TRAMADO —que es como una fotocopiadora convierte los grises:
    puntos negros más juntos o más separados— encoger tirando uno de cada dos
    puntos no deja una foto peor: deja RUIDO. La medición: la imagen se aleja
    85 sobre 255 del original, o sea deja de parecerse a lo que se escaneó, y el
    archivo sale de 1.116 KB en vez de 493 — más pesado que el original, que es
    lo contrario de para lo que existe todo esto.

    Por eso `_listo_para_encoger` les cambia el modo ANTES de encoger, que además
    es barato: un punto de paleta ocupa un byte.
    """
    from PIL import ImageChops, ImageStat

    gris = _abrir(foto_de_comprobante(3000, 2250, calidad=95)).convert("L")
    # `convert("1")` trama con Floyd-Steinberg, que es justo lo que hace un
    # escáner de oficina puesto en blanco y negro.
    escaneo = gris.convert("1")
    buffer = io.BytesIO()
    escaneo.save(buffer, format="PNG")
    entrada = buffer.getvalue()

    salida, tipo = comprimir_soporte(entrada, "image/png", nombre="escaneo.png")
    resultado = _abrir(salida).convert("RGB")

    bueno = gris.convert("RGB")
    bueno.thumbnail((LADO_MAYOR_MAX, LADO_MAYOR_MAX), Image.LANCZOS)
    media = sum(
        ImageStat.Stat(ImageChops.difference(bueno, resultado)).mean
    ) / 3

    print("\n===== UN ESCANEO TRAMADO (modo 1) =====")
    print(f"  entra 3.000 × 2.250 modo «1» · {_kb(len(entrada))}")
    print(f"  sale  {resultado.size[0]} × {resultado.size[1]} · {_kb(len(salida))} · {tipo}")
    print(f"  se aleja {media:.1f} de 255 del original en gris")
    print(f"  (con «el vecino más cercano» se alejaba 85,0 y pesaba {_kb(1116510)})")
    assert tipo == "image/jpeg"
    assert max(resultado.size) == LADO_MAYOR_MAX
    assert media < 40, (
        "el escaneo tramado se encogió con «el vecino más cercano» y quedó en "
        "ruido. Mire `_listo_para_encoger`"
    )
    assert len(salida) < len(entrada), (
        "el resultado pesa más que el original: los escalones no se dejan apretar"
    )


def test_una_foto_en_paleta_pesa_menos_si_se_encoge_bien():
    """El mismo arreglo, medido en plata: los escalones no se dejan comprimir.

    Una foto pasada a paleta de 256 colores (un PNG-8) encogida con «el vecino
    más cercano» queda llena de ruido de alta frecuencia, y eso es justo lo que
    un JPEG no sabe apretar: el archivo sale un 25 % más pesado por nada. El
    bucket se paga por lo que ocupa.
    """
    foto = _abrir(foto_de_comprobante(3000, 2250, calidad=95)).convert("RGB")
    paleta = foto.convert("P", palette=Image.ADAPTIVE, colors=256)
    buffer = io.BytesIO()
    paleta.save(buffer, format="PNG")
    entrada = buffer.getvalue()

    salida, tipo = comprimir_soporte(entrada, "image/png", nombre="paleta.png")
    resultado = _abrir(salida)
    print("\n===== UNA FOTO EN PALETA (PNG-8) =====")
    print(f"  entra 3.000 × 2.250 modo P · {_kb(len(entrada))}")
    print(f"  sale  {resultado.size[0]} × {resultado.size[1]} {resultado.mode} · "
          f"{_kb(len(salida))} · {tipo}")
    print(f"  (con «el vecino más cercano» pesaba {_kb(256023)})")
    assert tipo == "image/jpeg"
    assert max(resultado.size) == LADO_MAYOR_MAX
    assert len(salida) < 230 * 1024, (
        "el archivo salió con el peso de los escalones: se encogió con «el "
        "vecino más cercano». Mire `_listo_para_encoger`"
    )


# ===========================================================================
# EL TECHO DE VERDAD: cerrar la clase entera, no un formato más
# ===========================================================================
# POR QUÉ HAY UNA SECCIÓN NUEVA Y NO UNA PRUEBA MÁS EN LA DE ARRIBA. Todo lo
# anterior comprueba que el PRESUPUESTO adivina bien. Estas comprueban lo otro:
# que cuando el presupuesto adivine MAL —y ya adivinó mal tres veces— la imagen
# se muera sola en vez de llevarse por delante el proceso que atiende a las dos
# queseras. Son dos defensas distintas y por eso se prueban aparte:
#
#   · el PRESUPUESTO rebota barato lo que ya sabe reconocer, sin gastar un
#     proceso, y sigue siendo el que contesta el 422 en el caso conocido;
#   · el OBRERO aguanta lo que el presupuesto no supo ver, y es el único que no
#     depende de haberlo previsto.
def _rebota(contenido: bytes, tipo: str, nombre: str) -> tuple[bool, str]:
    """(¿rebotó?, el mensaje que le sale al dueño). Nunca deja escapar un 500."""
    try:
        comprimir_soporte(contenido, tipo, nombre=nombre)
    except BusinessError as exc:
        return True, str(exc)
    return False, ""


def test_el_jpeg_progresivo_ya_no_se_lleva_el_proceso():
    """EL HUECO QUE QUEDABA, cerrado y medido: 2 KB que pedían 660 MB.

    Un JPEG PROGRESIVO de 10.325 × 7.744 en CMYK entraba por el endpoint de
    verdad, respondía 201 y hacía crecer el proceso 660,6 MB en 0,73 s. Tres a la
    vez —lo que el semáforo deja pasar— eran 1.982 MB.

    LA CAUSA, y es la que explica por qué no bastaba con «subirle el costo al
    CMYK»: `Image.draft` reduce la SALIDA de libjpeg pero NO el arreglo de
    coeficientes, que en un progresivo se arma COMPLETO —del tamaño DECLARADO—
    antes del primer píxel. El presupuesto miraba los 5 megapíxeles que iban a
    salir y cobraba 50 MB cuando lo medido eran 660,6. Doce veces por debajo.
    """
    bomba = jpeg_progresivo()
    (rebotada, mensaje), subida = _pico_mientras(
        lambda: _rebota(bomba, "image/jpeg", "progresivo.jpg")
    )

    print("\n===== EL JPEG PROGRESIVO =====")
    print(f"  el archivo pesa {len(bomba):,} bytes".replace(",", "."))
    print("  declara 10.325 × 7.744 CMYK (79,96 megapíxeles)")
    print("  antes: 201 y 660,6 MB de pico")
    print(f"  ahora: 422 y {subida:.1f} MB de pico")
    print(f"  «{mensaje}»")
    assert rebotada, "el JPEG progresivo volvió a pasar: mire el presupuesto"
    assert subida < 60, (
        f"el JPEG progresivo volvió a pedir memoria de verdad ({subida:.0f} MB): "
        "se decodificó cuando no debía"
    )
    # Y que el mensaje sirva: el dueño tiene que saber qué mandar en vez de esto.
    assert "demasiado grande" in mensaje
    assert "PDF" in mensaje


def test_el_progresivo_no_era_cosa_del_cmyk():
    """EL HERMANO: el mismo defecto con tres componentes en vez de cuatro.

    Es la prueba de que lo que falla es el MODO DE CODIFICACIÓN y no el espacio
    de color, y de que arreglarlo «para CMYK» habría sido el cuarto parche por
    formato. Medido, el mismo archivo en RGB: 261,0 MB y un 201.

    Cuesta menos que el CMYK y por una razón que la cuenta ya sabe: en RGB el
    color va submuestreado a la mitad de lado, así que son 1,5 componentes
    efectivos en vez de 4 (ver `_costo_de_los_coeficientes`).
    """
    bomba = jpeg_progresivo(modo="RGB")
    (rebotada, mensaje), subida = _pico_mientras(
        lambda: _rebota(bomba, "image/jpeg", "progresivo_rgb.jpg")
    )
    print("\n===== EL MISMO, PERO EN RGB =====")
    print("  antes: 201 y 261,0 MB de pico")
    print(f"  ahora: 422 y {subida:.1f} MB de pico")
    assert rebotada, "el progresivo en RGB pasó: se arregló solo el caso del CMYK"
    assert subida < 60


def test_la_cuenta_de_los_coeficientes_cuadra_con_lo_medido():
    """Y que el desglose SUME EXACTO, que es como se revisa acá.

    Los dos sumandos del presupuesto tienen que dar la cifra grande, y la cifra
    grande tiene que parecerse a lo que se midió de verdad. Si algún día alguien
    cambia la constante de los dos bytes por coeficiente «para que dé», esta
    prueba lo dice.

    Y EL CONTROL QUE HACE HONESTA A LA PRUEBA: el MISMO tamaño y el MISMO CMYK,
    pero BASELINE, cobra CERO de coeficientes. Si esa línea fallara, sería que se
    está cobrando por ser grande o por ser CMYK y no por ser progresivo — o sea,
    que se rebotarían fotos buenas.
    """
    import struct

    from app.core.imagenes import (
        _costo_de_los_coeficientes,
        _pedir_la_imagen_ya_reducida,
    )

    declaradas = (10325, 7744)
    casos = [
        # (qué es, modo, coeficientes esperados en MB, lo que se midió de verdad)
        ("CMYK progresivo, 4 componentes a 1×1", "CMYK", 639.7, 660.6),
        ("RGB progresivo, color a la mitad", "RGB", 239.9, 261.0),
    ]
    print("\n===== LA CUENTA DE LOS COEFICIENTES =====")
    for que_es, modo, esperado, medido in casos:
        abierta = _abrir(jpeg_progresivo(modo=modo))
        _pedir_la_imagen_ya_reducida(abierta)
        salida = abierta.size[0] * abierta.size[1] / 1e6 * 10.0
        coeficientes = _costo_de_los_coeficientes(abierta, declaradas)
        print(f"  {que_es}")
        print(
            f"    salida {salida:.1f} MB + coeficientes {coeficientes:.1f} MB "
            f"= {salida + coeficientes:.1f} MB  (medido de verdad: {medido} MB)"
        )
        assert abs(coeficientes - esperado) < 1.0, (
            f"la cuenta de los coeficientes cambió: da {coeficientes:.1f} y "
            f"estaba medida en {esperado}"
        )
        # Lo que se cobra tiene que quedar al lado de lo que cuesta o por encima,
        # nunca doce veces por debajo, que era el defecto.
        assert salida + coeficientes >= medido * 0.9
        assert salida + coeficientes > PRESUPUESTO_MB_POR_IMAGEN

    # EL CONTROL: el mismo tamaño y el mismo CMYK, pero sin progresivo. Se le
    # cambia la marca de «cuadro progresivo» (SOF2, 0xC2) por la de «cuadro
    # normal» (SOF0, 0xC0), que es la única diferencia que importa acá.
    crudo = bytearray(jpeg_progresivo())
    crudo[crudo.find(b"\xff\xc2") + 1] = 0xC0
    baseline = _abrir(bytes(crudo))
    _pedir_la_imagen_ya_reducida(baseline)
    sin_progresivo = _costo_de_los_coeficientes(baseline, declaradas)
    print(f"  el MISMO archivo declarado baseline: coeficientes {sin_progresivo:.1f} MB")
    assert sin_progresivo == 0.0, (
        "se está cobrando por ser grande o por ser CMYK, no por ser progresivo"
    )


def test_tres_progresivos_a_la_vez_ya_no_son_dos_gigas():
    """Lo que el semáforo deja pasar a la vez, que es como llegaría de verdad.

    Uno solo eran 660,6 MB; tres —los que `SUBIDAS_A_LA_VEZ` permite— eran
    1.982 MB, y ahí no hay instancia de Render que aguante.
    """
    bomba = jpeg_progresivo()
    pasaron: list = []

    def subir():
        try:
            comprimir_soporte(bomba, "image/jpeg", nombre="a la vez.jpg")
            pasaron.append("pasó")
        except BusinessError:
            pass

    def las_tres():
        hilos = [threading.Thread(target=subir) for _ in range(3)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

    _, subida = _pico_mientras(las_tres)
    print("\n===== TRES PROGRESIVOS A LA VEZ =====")
    print(f"  antes: 1.982 MB   ahora: {subida:.1f} MB")
    assert not pasaron, "un JPEG progresivo volvió a pasar"
    assert subida < 100


def test_la_decodificacion_ocurre_de_verdad_en_otro_proceso():
    """EL AISLAMIENTO, medido: la foto se abre, pero NO en este proceso.

    Es la mitad del techo que funciona en Linux Y en Windows, y la que protege lo
    que de verdad importa: que el uvicorn que atiende a las dos queseras no crezca
    aunque el decodificador se descontrole.

    Se usa una captura PNG RGBA de 12 megapíxeles —una foto legítima y de las
    caras: 93,9 MB medidos cuando se decodificaba acá mismo— y se mira el pico
    del proceso que atiende. Con el obrero puesto, esa memoria se pide EN OTRO
    LADO y por acá solo entran y salen los bytes.
    """
    if not hay_obrero():  # pragma: no cover - solo si alguien lo apagó
        pytest.skip("el obrero está apagado en este entorno")
    captura = captura_png_rgba()
    (salida, tipo), subida = _pico_mientras(
        lambda: comprimir_soporte(captura, "image/png", nombre="captura.png")
    )
    print("\n===== DÓNDE SE GASTA LA MEMORIA =====")
    print(f"  {como_esta_el_techo()}")
    print("  decodificando acá mismo: 93,9 MB de pico en este proceso")
    print(f"  con el obrero:           {subida:.1f} MB de pico en este proceso")
    print(f"  ({_mb(len(captura))} → {_kb(len(salida))} {tipo})")
    assert tipo == "image/jpeg"
    assert subida < 40, (
        f"el proceso que atiende creció {subida:.0f} MB: la decodificación se "
        "está haciendo acá y no en el obrero"
    )


def test_el_obrero_es_de_verdad_otro_proceso():
    """Que no sea una capa de mentira: el que decodifica tiene otro PID."""
    if not hay_obrero():  # pragma: no cover
        pytest.skip("el obrero está apagado en este entorno")
    import os as sistema

    from app.core.imagenes import _sacar_un_obrero

    comprimir_soporte(JPEG, "image/jpeg", nombre="quien.jpg")
    fila = _sacar_un_obrero()
    obrero = fila.get()
    try:
        pid = obrero.proceso.pid if obrero.proceso else None
    finally:
        fila.put(obrero)
    print("\n===== QUIÉN DECODIFICA =====")
    print(f"  este proceso: {sistema.getpid()} · el obrero: {pid}")
    assert pid is not None and pid != sistema.getpid()


def test_un_obrero_muerto_se_rehace_solo():
    """Matar al obrero es lo NORMAL, no una avería: así se comporta el techo.

    Cuando el sistema operativo mata a un obrero por pasarse del tope, la subida
    siguiente no puede quedar rota. Acá se le mata a propósito —que es justo lo
    que hace el núcleo— y se comprueba que la foto siguiente sale igual.
    """
    if not hay_obrero():  # pragma: no cover
        pytest.skip("el obrero está apagado en este entorno")
    from app.core.imagenes import _sacar_un_obrero

    comprimir_soporte(JPEG, "image/jpeg", nombre="antes.jpg")

    muertos = 0
    fila = _sacar_un_obrero()
    prestados = [fila.get() for _ in range(SUBIDAS_A_LA_VEZ)]
    for obrero in prestados:
        if obrero.proceso is not None:
            obrero.proceso.kill()
            obrero.proceso.wait(timeout=5)
            muertos += 1
    for obrero in prestados:
        fila.put(obrero)

    salida, tipo = comprimir_soporte(
        foto_de_comprobante(2000, 1500, calidad=90), "image/jpeg", nombre="despues.jpg"
    )
    print("\n===== SE LE MATA AL OBRERO Y SIGUE =====")
    print(f"  obreros muertos a propósito: {muertos}")
    print(f"  la foto siguiente salió igual: {_kb(len(salida))} {tipo}")
    assert muertos >= 1
    assert tipo == "image/jpeg"
    assert len(salida) > 0


def test_si_el_obrero_no_contesta_sale_un_422_y_no_un_500(monkeypatch):
    """LO QUE PASA CUANDO EL TECHO DISPARA, que es el caso que hay que cuidar.

    Cuando el núcleo mata al obrero por pasarse de memoria, el padre se queda sin
    respuesta. Eso NO puede salir como un error del servidor: el dueño tiene que
    recibir un mensaje que le diga qué hacer con su foto. Se simula la muerte —el
    obrero no contesta— y se comprueba que sale un BusinessError legible.
    """
    if not hay_obrero():  # pragma: no cover
        pytest.skip("el obrero está apagado en este entorno")
    import app.core.imagenes as modulo

    monkeypatch.setattr(modulo, "_esperar_al_obrero", lambda proceso: None)
    rebotada, mensaje = _rebota(
        foto_de_comprobante(2000, 1500), "image/jpeg", "se murio.jpg"
    )
    print("\n===== EL OBRERO SE MURIÓ =====")
    print(f"  «{mensaje}»")
    assert rebotada, "la muerte del obrero se escapó como error del servidor"
    assert "PDF" in mensaje, "el mensaje no le dice al dueño qué hacer"
    assert "Traceback" not in mensaje

    # Y que después de eso el servidor SIGA sirviendo: el obrero se rehace.
    monkeypatch.undo()
    salida, tipo = comprimir_soporte(JPEG, "image/jpeg", nombre="siguiente.jpg")
    assert tipo == "image/jpeg"


def test_sin_obrero_la_subida_no_se_cae_y_el_presupuesto_sigue_frenando(monkeypatch):
    """EL CAMINO DEGRADADO, que es el que atiende al cliente el día que esto falle.

    Si el obrero no se puede arrancar —sin permisos para crear procesos, un
    Python raro, lo que sea— la subida NO se cae: se decodifica en este mismo
    proceso, que es exactamente lo que se venía haciendo hasta hoy. Es la misma
    decisión que ya estaba tomada para Pillow, y por la misma razón: el cliente
    tiene que poder pegarle la foto al pago.

    Y LO QUE NO SE DEGRADA A NADA: el presupuesto sigue puesto. Sin obrero, el
    JPEG progresivo SIGUE REBOTANDO — o sea que el camino degradado no es
    «decodificar sin límite», que es lo único que no se podía aceptar.
    """
    import app.core.imagenes as modulo

    monkeypatch.setattr(modulo, "hay_obrero", lambda: False)

    salida, tipo = comprimir_soporte(
        foto_de_comprobante(2000, 1500, calidad=90), "image/jpeg", nombre="sin.jpg"
    )
    rebotada, _mensaje = _rebota(jpeg_progresivo(), "image/jpeg", "progresivo.jpg")

    print("\n===== SIN OBRERO =====")
    print(f"  una foto normal igual se comprime: {_kb(len(salida))} {tipo}")
    print(f"  y el progresivo IGUAL rebota: {rebotada}")
    assert tipo == "image/jpeg"
    assert rebotada, (
        "sin obrero el progresivo pasó: el camino degradado quedó SIN LÍMITE, "
        "que es lo único que no se podía aceptar"
    )


def test_si_el_obrero_no_arranca_no_se_le_echa_la_culpa_a_la_foto(monkeypatch):
    """LA CONFUSIÓN QUE HABÍA QUE EVITAR, y era la que podía dejar sin trabajar.

    Desde el padre, un obrero que MURIÓ POR EL TECHO y un obrero que NUNCA
    ARRANCÓ se ven igual: la tubería cerrada. Pero no son lo mismo ni de lejos:

      · el que murió por el techo dice que ESA FOTO no se puede procesar → 422;
      · el que nunca arrancó dice que ESTE SERVIDOR no puede con el mecanismo, y
        eso no tiene nada que ver con la foto.

    Si no se distinguieran, un despliegue con el PYTHONPATH torcido le habría
    contestado «es demasiado grande» a TODAS las fotos y el cliente no habría
    podido subir un solo soporte — un arreglo que deja sin trabajar es peor que el
    problema que vino a arreglar. Por eso el obrero SALUDA al arrancar, y el que
    no saluda se trata como «acá no hay obrero».

    LO QUE SE COMPRUEBA ACÁ SON LAS TRES COSAS A LA VEZ: que la foto buena se
    sube igual, que el presupuesto SIGUE frenando el progresivo (o sea que lo
    degradado no es «sin límite»), y que no se reintenta el arranque en cada foto.
    """
    import app.core.imagenes as modulo

    def no_arranca(self):
        raise modulo._ObreroNoArranca("roto a propósito para la prueba")

    monkeypatch.setattr(modulo._Obrero, "_arrancar", no_arranca)
    monkeypatch.setattr(modulo, "_OBREROS_SE_RINDIERON", False)
    # Los obreros que ya estén vivos se apartan, para que la prueba pase de verdad
    # por el arranque roto y no por uno que quedó bueno de otra prueba.
    fila = modulo._sacar_un_obrero()
    apartados = [fila.get() for _ in range(SUBIDAS_A_LA_VEZ)]
    for viejo in apartados:
        viejo._matar()
        fila.put(modulo._Obrero())

    try:
        salida, tipo = comprimir_soporte(
            foto_de_comprobante(2000, 1500, calidad=90), "image/jpeg", nombre="buena.jpg"
        )
        se_rindio = not modulo.hay_obrero()
        rebotada, _mensaje = _rebota(jpeg_progresivo(), "image/jpeg", "progresivo.jpg")
    finally:
        # Se devuelven obreros limpios: el monkeypatch se deshace al salir y los
        # que queden en la fila tienen que poder arrancar de verdad.
        for _ in range(SUBIDAS_A_LA_VEZ):
            fila.get()
        for _ in range(SUBIDAS_A_LA_VEZ):
            fila.put(modulo._Obrero())

    print("\n===== EL OBRERO NO ARRANCA =====")
    print(f"  la foto buena se subió igual: {_kb(len(salida))} {tipo}")
    print(f"  se dejó de reintentar el arranque: {se_rindio}")
    print(f"  y el progresivo IGUAL rebota: {rebotada}")
    assert tipo == "image/jpeg", (
        "un obrero que no arranca dejó sin subir una foto buena: se le echó la "
        "culpa a la foto de un problema del servidor"
    )
    assert se_rindio, (
        "se va a reintentar el arranque con cada foto, y eso no se arregla solo: "
        "cada subida pagaría el arranque para terminar en lo mismo"
    )
    assert rebotada, (
        "sin obrero el progresivo pasó: lo degradado quedó SIN LÍMITE, que es lo "
        "único que no se podía aceptar"
    )


def test_el_servidor_dice_en_voz_alta_que_techo_tiene():
    """Para que nadie crea que el portátil prueba lo que producción no tiene.

    En Linux —producción— hay tope duro del sistema operativo (`RLIMIT_AS`). En
    Windows —el portátil— NO, porque el módulo `resource` no existe: queda el
    aislamiento, que protege a la API pero no a la máquina. Eso tiene que poder
    consultarse, porque es la diferencia entre lo que se está probando acá y lo
    que va a correr allá.
    """
    frase = como_esta_el_techo()
    print("\n===== QUÉ TECHO HAY PUESTO =====")
    print(f"  {sys.platform}: {frase}")
    assert "obrero" in frase
    if sys.platform.startswith("win"):
        assert "SIN tope duro" in frase, (
            "Windows no tiene setrlimit; decir que sí lo tiene sería peor que no "
            "decir nada"
        )
    else:
        assert "CON tope duro" in frase


def test_el_tope_duro_se_le_pide_al_sistema_operativo():
    """Y que la petición del tope sea de verdad, no una frase escrita a mano.

    En Linux tiene que quedar puesto y decir en cuántos MB; en Windows tiene que
    decir que no se puede y por qué. Las dos respuestas son correctas: lo que no
    sería correcto es que el código creyera tener un techo que no tiene.
    """
    from app.core.imagenes_obrero import poner_el_techo

    frase = poner_el_techo(PRESUPUESTO_MB_POR_IMAGEN + TECHO_EXTRA_MB)
    print("\n===== SE LE PIDE EL TOPE AL SISTEMA =====")
    print(f"  {sys.platform}: {frase}")
    if sys.platform.startswith("win"):
        assert "sin tope duro" in frase and "setrlimit" in frase
    else:
        assert "tope duro en" in frase


def test_el_obrero_comprueba_que_su_techo_lo_deja_trabajar():
    """LA VÁLVULA DE SEGURIDAD, y es la que protege del arreglo peor que el mal.

    El tope se calcula sobre lo que el obrero ya tiene pedido. Si ese número
    saliera corto, el obrero quedaría con un techo tan apretado que se moriría con
    LA PRIMERA FOTO — y entonces no se podría subir NINGÚN soporte. Para un
    sistema que está en producción con un cliente de verdad, eso sería peor que el
    problema que se vino a arreglar: cambiar «se cae con una foto rara» por «no se
    puede trabajar» no es un arreglo.

    Por eso, después de poner el techo, el obrero COMPRUEBA que con él todavía
    cabe una foto legítima, y si no cabe lo va aflojando; y si ni aflojándolo
    sirve, lo suelta y lo dice. La comprobación se hace reservando espacio de
    direcciones (`mmap`) y no memoria de verdad: es lo que `RLIMIT_AS` mide y no
    cuesta ni una página.
    """
    from app.core.imagenes_obrero import _cabe_pedir

    print("\n===== LA COMPROBACIÓN DEL TECHO =====")
    cabe = _cabe_pedir(8)
    print(f"  ¿caben 8 MB? {cabe}")
    assert cabe, "no se pudieron reservar ni 8 MB: la comprobación no sirve"

    # Y que sepa decir que NO: un pedido imposible en cualquier máquina de 64 bits.
    no_cabe = _cabe_pedir(1024 * 1024 * 1024)  # un petabyte
    print(f"  ¿cabe un petabyte? {no_cabe}")
    assert not no_cabe, (
        "dijo que sí a un petabyte: la comprobación del techo no comprueba nada, "
        "y un techo mal calculado pasaría desapercibido"
    )


# ---------------------------------------------------------------------------
# LOS HERMANOS: otros sitios donde lo declarado no predice lo que cuesta
# ---------------------------------------------------------------------------
# LA PREGUNTA QUE HABÍA QUE HACERSE ANTES DE DAR ESTO POR CERRADO: si el JPEG
# progresivo se escapó porque lo que declara no dice lo que cuesta, ¿DÓNDE MÁS
# PASA ESO? Se buscaron y se midieron los candidatos con esa misma forma
# —entrelazado, más bits por canal, basura comprimida en los metadatos, y el
# mismo CMYK pero sin progresivo—, y todos quedan cubiertos. Cada uno dice acá
# abajo por CUÁL de las defensas queda cubierto, porque no es la misma en todos
# y esa es justamente la gracia de tener más de una.
def _suave(ancho: int, alto: int, modo: str) -> Image.Image:
    """Un degradado grande y barato de armar: pesa poco y declara mucho."""
    chico = Image.new(modo, (64, 48))
    puntos = chico.load()
    bandas = len(chico.getbands())
    for x in range(64):
        for y in range(48):
            puntos[x, y] = tuple((x * 4 + y * 3 + c * 17) % 256 for c in range(bandas))
    return chico.resize((ancho, alto), Image.BICUBIC)


def _png_crudo(ancho, alto, profundidad, tipo_color, filas, extras=b"") -> bytes:
    """Un PNG escrito a mano: hace falta para los modos que Pillow no escribe."""
    import struct
    import zlib

    def trozo(marca: bytes, datos: bytes) -> bytes:
        return (
            struct.pack(">I", len(datos))
            + marca
            + datos
            + struct.pack(">I", zlib.crc32(marca + datos) & 0xFFFFFFFF)
        )

    cabecera = struct.pack(">IIBBBBB", ancho, alto, profundidad, tipo_color, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + trozo(b"IHDR", cabecera)
        + extras
        + trozo(b"IDAT", zlib.compress(filas, 9))
        + trozo(b"IEND", b"")
    )


def _png_entrelazado(ancho: int, alto: int) -> bytes:
    """Un PNG guardado en siete pasadas (Adam7), el que se ve «apareciendo»."""
    buffer = io.BytesIO()
    _suave(ancho, alto, "RGB").save(
        buffer, format="PNG", interlace=True, compress_level=9
    )
    return buffer.getvalue()


def _png_de_16_bits(ancho: int, alto: int) -> bytes:
    """RGBA con 16 bits por canal: OCHO bytes por punto en el archivo, no tres."""
    fila = b"\x00" + (b"\x30\x40\x50\x60\x70\x80\xff\xff" * ancho)
    return _png_crudo(ancho, alto, 16, 6, fila * alto)


def _png_bomba_de_texto() -> bytes:
    """Chiquito en píxeles, con 600 MB comprimidos en un comentario.

    Es el hermano MÁS INTERESANTE de todos, porque enseña el límite de la idea
    vieja: NO TIENE NI UN PÍXEL DE MÁS, así que un presupuesto que cuente
    píxeles no puede verlo por definición, por mucho que se le afine la tabla.
    """
    import struct
    import zlib

    ancho = alto = 300
    filas = (b"\x00" + b"\x80" * (ancho * 3)) * alto
    basura = zlib.compress(b"A" * (600 * 1024 * 1024), 9)
    texto = b"comentario\x00\x00" + basura
    extras = (
        struct.pack(">I", len(texto))
        + b"zTXt"
        + texto
        + struct.pack(">I", zlib.crc32(b"zTXt" + texto) & 0xFFFFFFFF)
    )
    return _png_crudo(ancho, alto, 8, 2, filas, extras)


@pytest.mark.parametrize(
    "que_es, armar, quien_lo_frena",
    [
        # Adam7: siete pasadas y el lienzo entero vivo todo el rato. Lo frena el
        # presupuesto, que en PNG ya cobra por lo declarado (draft no le sirve).
        (
            "PNG entrelazado (Adam7) 7.000 × 7.000",
            lambda: _png_entrelazado(7000, 7000),
            "el presupuesto",
        ),
        # 16 bits por canal: ocho bytes por punto, no tres. Mismo freno.
        (
            "PNG de 16 bits por canal 6.000 × 6.000",
            lambda: _png_de_16_bits(6000, 6000),
            "el presupuesto",
        ),
        # Basura comprimida en un trozo de TEXTO: ni un píxel de más, así que el
        # presupuesto por píxeles NO PUEDE VERLO. Lo frena Pillow, que tiene su
        # propio tope para los trozos de texto — y el día que no lo tuviera, lo
        # frenaría el techo, que es el único que no depende del formato.
        (
            "PNG con una bomba zip en los metadatos",
            _png_bomba_de_texto,
            "Pillow, y detrás el techo",
        ),
    ],
)
def test_los_hermanos_del_progresivo_tambien_estan_cubiertos(
    que_es, armar, quien_lo_frena
):
    """Cada hermano, medido: ninguno hace crecer el proceso que atiende."""
    contenido = armar()
    (rebotada, mensaje), subida = _pico_mientras(
        lambda: _rebota(contenido, "image/png", "hermano.png")
    )
    print(f"\n===== HERMANO: {que_es} =====")
    print(
        f"  pesa {_kb(len(contenido))} · rebota: {rebotada} ({quien_lo_frena}) · "
        f"pico en este proceso: {subida:.1f} MB"
    )
    if mensaje:
        print(f"  «{mensaje[:100]}»")
    assert rebotada, f"«{que_es}» pasó: hay un hermano del progresivo suelto"
    assert subida < 60, (
        f"«{que_es}» hizo crecer el proceso {subida:.0f} MB: hay un hermano del "
        "JPEG progresivo suelto"
    )
