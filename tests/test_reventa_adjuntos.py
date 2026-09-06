"""Soportes de pago (fotos de transferencias) en las compras y ventas de reventa.

POR QUÉ EXISTE ESTO. El dueño paga y le pagan por transferencia, y quiere dejar
pegada la foto del comprobante al documento: "esto es el respaldo de que se
pagó". Son varias fotos por compra y por venta, y son datos de pago —nombres,
cuentas, montos— así que los archivos son PRIVADOS: no hay URL pública en
ninguna parte, se firma un enlace corto para verlos y uno más largo, aparte,
cuando el dueño quiere mandarle uno a alguien por WhatsApp.

CLOUDFLARE R2 SE REEMPLAZA CON UN DOBLE: estas pruebas NO salen a internet, ni
siquiera cuando la máquina tiene llaves configuradas. Mismo patrón que
`WompiFalso` en tests/test_suscripcion_pse.py: el servicio instancia el cliente
DENTRO de cada método, así que basta con cambiarle el nombre en el módulo.
"""
import io
from datetime import date, datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.modules.reventa.models import AdjuntoReventa
from tests.ayudas_imagenes import (
    EJECUTABLE,
    JPEG,
    PDF,
    PNG,
    TEXTO_PLANO,
    bomba_de_pixeles,
    foto_de_comprobante,
    foto_heic_de_iphone,
    heic_se_puede_abrir,
)
from tests.ayudas_r2 import R2Falso, enchufar
from tests.conftest import PASSWORD, auth_headers

API = "/api/v1/reventa"

# ---------------------------------------------------------------- archivos
# IMÁGENES DE VERDAD, armadas con Pillow (ver tests/ayudas_imagenes.py). Antes de
# la compresión eran una cabecera de JPEG con relleno, y alcanzaba: el backend
# solo le miraba los primeros bytes al archivo. Desde que las fotos SE COMPRIMEN
# al subirlas hay que poder abrirlas de verdad, porque un JPEG de mentira lo
# rechaza el compresor — que es justo lo que tiene que hacer con un archivo que
# llegó dañado.
#
# Las que usan estas pruebas son CHIQUITAS (60 × 40) a propósito: por debajo del
# tope de 1600 px y con tan poco que ahorrar que el compresor las deja pasar TAL
# CUAL. Así el tipo con el que entran es el mismo con el que se guardan, y estas
# pruebas siguen comprobando lo suyo —el flujo, los permisos, el aislamiento
# entre queseras— sin quedar amarradas a cuánto comprime Pillow. Lo que sí se
# comprime se mide aparte, en tests/test_compresion_soportes.py.
#
# El backend NO mira la extensión del nombre ni el Content-Type que manda el
# navegador —los dos los pone quien sube—: mira los primeros bytes.


def foto(nombre="transferencia.jpg", contenido=JPEG, tipo="image/jpeg"):
    """Un archivo listo para el multipart de httpx."""
    return ("files", (nombre, contenido, tipo))


# -------------------------------------------------------------- doble de R2
# Vive en tests/ayudas_r2.py porque lo comparten estas pruebas y las de los
# soportes de los pagos de liquidación: mismo bucket, mismo cliente, mismas
# reglas. Dos dobles distintos serían dos contratos que se van separando.


@pytest.fixture()
def r2(monkeypatch):
    """Enchufa el doble (ver `tests/ayudas_r2.py::enchufar`)."""
    import app.modules.reventa.service as servicio

    return enchufar(monkeypatch, servicio)


# ------------------------------------------------------------------ ayudas
def comprar(client, h, kilos="100", precio="18000", productor="Yeferson"):
    r = client.post(
        f"{API}/compras",
        json={
            "fecha": str(date.today() - timedelta(days=3)),
            "productor": productor,
            "kilos_brutos": kilos,
            "borona_kilos": "0",
            "precio_kilo": precio,
        },
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def vender(client, h, kilos="50", precio="21000", cliente="Tienda La 33"):
    r = client.post(
        f"{API}/ventas",
        json={
            "fecha": str(date.today() - timedelta(days=1)),
            "cliente": cliente,
            "tipo": "queso",
            "kilos": kilos,
            "precio_kilo": precio,
        },
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def crear_usuario_con_rol(db_session, empresa, nombre_rol, username):
    """Un usuario de la empresa con uno de los roles que siembra el sistema."""
    from app.core.security import hash_password
    from app.modules.usuarios.models import Rol, Usuario, UsuarioRol

    rol = db_session.scalars(select(Rol).where(Rol.nombre == nombre_rol)).one()
    usuario = Usuario(
        nombre=username.title(),
        apellido="Prueba",
        correo=f"{username}@test.local",
        username=username,
        hashed_password=hash_password(PASSWORD),
        empresa_id=empresa.id,
    )
    db_session.add(usuario)
    db_session.flush()
    db_session.add(UsuarioRol(usuario_id=usuario.id, rol_id=rol.id, empresa_id=empresa.id))
    db_session.commit()
    return usuario


# ===========================================================================
# a) Subir DOS imágenes a una compra y volverlas a listar
# ===========================================================================
def test_subir_dos_imagenes_a_una_compra_y_listarlas(client, base_datos, r2, db_session):
    """El caso de todos los días: se le compra a un productor, se le transfiere
    en dos giros (porque el banco tiene tope diario) y se pegan las DOS fotos a
    la misma compra.

    Se comprueba de paso que la llave del objeto lleve el empresa_id ADENTRO: es
    la segunda barrera contra ver el archivo de otra empresa. Aunque una consulta
    se escapara sin filtro, la llave que se firmaría empieza por un uuid de
    empresa que no es el suyo, y eso se ve en la auditoría.
    """
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)

    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[foto("giro1.jpg", JPEG), foto("giro2.png", PNG, "image/png")],
        headers=h,
    )
    print("\n===== a) SUBIR DOS SOPORTES A UNA COMPRA =====")
    print(f"  POST adjuntos: {r.status_code}")
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["disponible"] is True
    assert len(cuerpo["adjuntos"]) == 2
    for a in cuerpo["adjuntos"]:
        print(f"    {a['nombre_archivo']:16} {a['content_type']:12} "
              f"{a['tamano_bytes']} bytes · subió: {a['subido_por_nombre']}")
        assert a["compra_id"] == compra["id"]
        assert a["venta_id"] is None
        assert a["es_imagen"] is True
        assert a["subido_por_nombre"] == "Admin.A Prueba"

    # Y al volver a pedirlas, siguen ahí (esto es lo que hace la pantalla al abrir)
    listado = client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=h).json()
    assert len(listado["adjuntos"]) == 2
    print(f"  GET adjuntos: {len(listado['adjuntos'])} soportes · "
          f"cupo restante: {listado['cupo_restante']}")

    # Los dos archivos están de verdad en el almacenamiento, con su tipo
    assert len(R2Falso.objetos) == 2
    claves = list(R2Falso.objetos)
    empresa_a = base_datos["empresa_a"].id
    for clave in claves:
        print(f"    llave: {clave}")
        assert clave.startswith(f"{empresa_a}/reventa/compras/{compra['id']}/")
    assert R2Falso.objetos[claves[0]][1] == "image/jpeg"
    assert R2Falso.objetos[claves[1]][1] == "image/png"

    # El número sale también en la lista de compras, para ver de un vistazo
    # cuáles tienen respaldo del pago y cuáles no.
    fila = client.get(f"{API}/compras", headers=h).json()["items"][0]
    print(f"  la compra en la lista dice: {fila['adjuntos_count']} soportes")
    assert fila["adjuntos_count"] == 2


def test_lo_mismo_funciona_en_las_ventas(client, base_datos, r2):
    """La venta es el otro lado del mismo negocio: el cliente transfiere y esa
    foto es el respaldo de que cobró. Va por rutas propias, así que se prueba
    aparte y no se da por hecho que "es igual a las compras"."""
    h = auth_headers(client, "admin.a")
    comprar(client, h, kilos="100")
    venta = vender(client, h, kilos="50")

    r = client.post(
        f"{API}/ventas/{venta['id']}/adjuntos",
        files=[foto("pago_tienda.jpg", JPEG)],
        headers=h,
    )
    print("\n===== VENTAS =====")
    print(f"  POST adjuntos de venta: {r.status_code}")
    assert r.status_code == 201, r.text
    adjunto = r.json()["adjuntos"][0]
    assert adjunto["venta_id"] == venta["id"]
    assert adjunto["compra_id"] is None
    clave = list(R2Falso.objetos)[0]
    print(f"  llave: {clave}")
    assert f"/reventa/ventas/{venta['id']}/" in clave


def test_se_acepta_el_pdf_del_banco(client, base_datos, r2):
    """Los bancos colombianos entregan el comprobante como PDF descargable, y ese
    PDF es MEJOR soporte que una foto de la pantalla. Rechazarlo obligaría al
    dueño a fotografiar un comprobante que ya tenía."""
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[("files", ("comprobante.pdf", PDF, "application/pdf"))],
        headers=h,
    )
    print("\n===== PDF DEL BANCO =====")
    print(f"  POST comprobante.pdf: {r.status_code}")
    assert r.status_code == 201, r.text
    adjunto = r.json()["adjuntos"][0]
    print(f"  tipo guardado: {adjunto['content_type']} · es_imagen: {adjunto['es_imagen']}")
    assert adjunto["content_type"] == "application/pdf"
    # No es imagen: la pantalla tiene que mostrarle un icono de documento, no
    # intentar dibujar una miniatura que saldría rota.
    assert adjunto["es_imagen"] is False
    # Y sale IDÉNTICO al que entró: el PDF no pasa por el compresor. Comprimirlo
    # sería convertir texto nítido en una foto de texto.
    assert list(R2Falso.objetos.values())[0][0] == PDF


def test_la_foto_del_celular_tambien_se_comprime_en_reventa(client, base_datos, r2):
    """LA COMPRESIÓN TAMBIÉN CORRE POR ACÁ, y eso es la mitad del pedido: "de
    paso también le reducimos la calidad para ahorrar espacio".

    Reventa es donde el dueño lleva más tiempo pegando fotos, así que es donde
    más crece el bucket. Se sube la foto tal como sale del celular y se comprueba
    lo que quedó EN EL ALMACENAMIENTO, que es lo que se paga.

    Lo que ya está subido NO se recomprime: esto corre solo en la subida.
    """
    from PIL import Image

    from tests.ayudas_imagenes import foto_de_celular

    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    original = foto_de_celular()

    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[("files", ("IMG_0421.jpg", original, "image/jpeg"))],
        headers=h,
    )
    assert r.status_code == 201, r.text
    guardado, tipo = list(R2Falso.objetos.values())[0]
    imagen = Image.open(io.BytesIO(guardado))
    print("\n===== LA FOTO SE GUARDA COMPRIMIDA =====")
    print(f"  entra:  {len(original) / 1024 / 1024:.2f} MB (4032 × 3024)")
    print(f"  guarda: {len(guardado) / 1024:.0f} KB ({imagen.size[0]} × {imagen.size[1]}) {tipo}")
    print(f"  ahorro: {100 * (1 - len(guardado) / len(original)):.1f} %")
    assert len(guardado) < len(original) / 5
    assert max(imagen.size) == 1600
    # El peso que se anota es el de lo que QUEDÓ, no el de lo que entró.
    assert r.json()["adjuntos"][0]["tamano_bytes"] == len(guardado)


# ===========================================================================
# b) El enlace es FIRMADO y de corta duración, no una URL pública
# ===========================================================================
def test_el_enlace_de_ver_es_firmado_y_de_corta_duracion(client, base_datos, r2, db_session):
    """La decisión del dueño fue que los archivos son PRIVADOS. Eso significa dos
    cosas que se verifican aquí:

    1. En la BASE no hay ninguna URL. Solo la llave del objeto. Una URL guardada
       en una columna es un permiso permanente: quien la viera en un backup, en
       un log o en un export vería el soporte de pago para siempre.
    2. Lo que se entrega es un enlace FIRMADO que caduca en minutos. Si esa URL
       queda en el historial del navegador o en la vista previa de un chat, ya no
       sirve para cuando alguien la encuentre.
    """
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    client.post(
        f"{API}/compras/{compra['id']}/adjuntos", files=[foto()], headers=h
    )

    adjunto = client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=h).json()[
        "adjuntos"
    ][0]
    print("\n===== b) EL ENLACE ES FIRMADO Y CORTO =====")
    print(f"  url: {adjunto['url'][:90]}...")
    assert "X-Amz-Signature=" in adjunto["url"], "el enlace no viene firmado"
    assert "X-Amz-Expires=" in adjunto["url"], "el enlace no trae caducidad"

    # Corto de verdad: lo que se le pidió firmar al almacenamiento
    _, segundos = R2Falso.firmas[-1]
    print(f"  dura {segundos} segundos ({segundos // 60} minutos)")
    assert segundos == settings.R2_URL_VER_MINUTOS * 60
    assert 60 <= segundos <= 3600, "el enlace de ver no puede durar horas"

    # La pantalla sabe cuándo se le muere el enlace que tiene en memoria
    expira = datetime.fromisoformat(adjunto["url_expira"])
    faltan = (expira - datetime.now(timezone.utc)).total_seconds()
    print(f"  url_expira: {adjunto['url_expira']} (faltan {faltan:.0f} s)")
    assert 0 < faltan <= segundos + 5

    # Y en la BASE no hay ni una URL: solo la llave del objeto
    fila = db_session.scalars(select(AdjuntoReventa)).one()
    guardado = {
        c.key: getattr(fila, c.key) for c in AdjuntoReventa.__mapper__.column_attrs
    }
    print(f"  object_key en la base: {guardado['object_key']}")
    con_url = [
        k for k, v in guardado.items() if isinstance(v, str) and v.startswith("http")
    ]
    assert con_url == [], f"se guardó una URL en la base: {con_url}"


# ===========================================================================
# c) No se puede ver ni compartir un adjunto de OTRA empresa
# ===========================================================================
def test_no_se_ve_ni_se_comparte_el_soporte_de_otra_empresa(client, base_datos, r2):
    """El aislamiento entre empresas es la regla que no se rompe en este sistema,
    y aquí pesa más que en cualquier otra tabla: un soporte de transferencia trae
    el nombre, la cuenta y el monto de un pago real.

    Se prueban los TRES caminos —ver, compartir y borrar—, porque cada uno entra
    por una ruta distinta, y además que no se le haya firmado NADA al intruso: si
    se firmara y después se negara, el enlace ya estaría hecho.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    compra = comprar(client, ha)
    subida = client.post(
        f"{API}/compras/{compra['id']}/adjuntos", files=[foto()], headers=ha
    ).json()
    adjunto_id = subida["adjuntos"][0]["id"]

    firmas_antes = len(R2Falso.firmas)
    print("\n===== c) UNA EMPRESA NO VE LO DE LA OTRA =====")

    ver = client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=hb)
    print(f"  la empresa B lista los soportes de A: {ver.status_code}")
    assert ver.status_code == 404

    compartir = client.post(f"{API}/adjuntos/{adjunto_id}/compartir", headers=hb)
    print(f"  la empresa B comparte un soporte de A: {compartir.status_code}")
    assert compartir.status_code == 404

    borrar = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=hb)
    print(f"  la empresa B borra un soporte de A:    {borrar.status_code}")
    assert borrar.status_code == 404

    # Nada se firmó por el camino: ni un enlace a medio hacer.
    print(f"  enlaces firmados para la empresa B: {len(R2Falso.firmas) - firmas_antes}")
    assert len(R2Falso.firmas) == firmas_antes
    # Y el archivo sigue en el almacenamiento, intacto.
    assert len(R2Falso.objetos) == 1 and R2Falso.borrados == []

    # La dueña sí lo ve, para que quede claro que el 404 es por la empresa y no
    # porque el adjunto estuviera roto.
    suyo = client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=ha)
    print(f"  la empresa A (la dueña) lo lista:      {suyo.status_code}")
    assert suyo.status_code == 200 and len(suyo.json()["adjuntos"]) == 1


# ===========================================================================
# d) Borrar exige 'reventa:eliminar' — NO 'crear'
# ===========================================================================
def test_borrar_un_soporte_exige_el_permiso_de_eliminar(client, base_datos, db_session, r2):
    """En este proyecto ya pasó al revés con los abonos: el borrado quedó pidiendo
    'crear', el mismo permiso de registrarlos, y quien podía anotar un pago podía
    borrarlo. No se repite.

    El rol 'Ventas' que siembra el sistema sirve de conejillo: puede crear,
    editar, consultar y exportar en reventa, pero NO eliminar. Tiene que poder
    subir el soporte y no poder borrarlo.
    """
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Ventas", "vendedora")

    ha = auth_headers(client, "admin.a")
    hv = auth_headers(client, "vendedora")
    compra = comprar(client, ha)

    print("\n===== d) BORRAR EXIGE 'eliminar' =====")
    # Subir sí puede: para eso tiene 'crear' y 'editar'
    subida = client.post(
        f"{API}/compras/{compra['id']}/adjuntos", files=[foto()], headers=hv
    )
    print(f"  la vendedora sube un soporte:  {subida.status_code}")
    assert subida.status_code == 201, subida.text
    adjunto_id = subida.json()["adjuntos"][0]["id"]

    negado = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=hv)
    print(f"  la vendedora lo borra:         {negado.status_code} · "
          f"{negado.json()['error']['detail']}")
    assert negado.status_code == 403
    assert "eliminar" in negado.json()["error"]["detail"]
    # No se tocó el archivo: no basta con negar la fila.
    assert R2Falso.borrados == []
    assert len(R2Falso.objetos) == 1

    # La administradora, que sí tiene 'eliminar', lo borra sin problema
    ok = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=ha)
    print(f"  la administradora lo borra:    {ok.status_code}")
    assert ok.status_code == 204


def test_borrar_quita_tambien_el_archivo_del_almacenamiento(client, base_datos, r2):
    """Borrar solo la fila dejaría el archivo en el bucket para siempre: nadie
    podría verlo, nadie podría borrarlo y se seguiría pagando su almacenamiento.
    Con soportes de pago es peor todavía: el archivo con la cuenta y el monto
    seguiría existiendo después de que el dueño creyó haberlo borrado.
    """
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    subida = client.post(
        f"{API}/compras/{compra['id']}/adjuntos", files=[foto(), foto("otra.jpg")],
        headers=h,
    ).json()
    adjunto_id = subida["adjuntos"][0]["id"]
    clave = list(R2Falso.objetos)[0]

    print("\n===== BORRAR QUITA EL ARCHIVO, NO SOLO LA FILA =====")
    print(f"  antes:  {len(R2Falso.objetos)} objetos en el almacenamiento")
    r = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=h)
    assert r.status_code == 204
    print(f"  después: {len(R2Falso.objetos)} objetos · borrados: {R2Falso.borrados}")
    assert clave in R2Falso.borrados
    assert clave not in R2Falso.objetos

    quedan = client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=h).json()
    print(f"  la lista queda con {len(quedan['adjuntos'])} soporte")
    assert len(quedan["adjuntos"]) == 1


def test_si_el_almacenamiento_falla_al_borrar_el_soporte_igual_desaparece(
    client, base_datos, r2
):
    """EL MISMO ORDEN QUE EN EL GEMELO, y el mismo cambio a cambio.

    `eliminar_adjunto` borraba PRIMERO el objeto en R2 y después la fila, con el
    commit ocurriendo afuera, en `get_db`. Si ese commit fallaba, la sesión hacía
    rollback: la fila REVIVE y el archivo que nombra ya no existe. Ahora primero
    la base y el bucket al confirmar.

    Lo que se cambia a cambio: si el bucket no responde, el soporte se borra
    igual y el archivo queda huérfano ocupando espacio. Es el mal menor —lo dice
    el mismo módulo en `limpiar_de_documento`—: un archivo de sobra cuesta unos
    centavos; un soporte que la pantalla muestra y que no abre cuesta una
    discusión sobre si se pagó.
    """
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    subida = client.post(
        f"{API}/compras/{compra['id']}/adjuntos", files=[foto()], headers=h
    ).json()
    adjunto_id = subida["adjuntos"][0]["id"]

    R2Falso.revienta_al_borrar = True
    r = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=h)
    R2Falso.revienta_al_borrar = False
    print("\n===== SI FALLA EL BORRADO EN R2 =====")
    print(f"  DELETE: {r.status_code} (el soporte se borra igual)")
    assert r.status_code == 204

    quedan = client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=h).json()
    print(f"  la lista queda en {len(quedan['adjuntos'])} · y el archivo quedó "
          f"huérfano en el bucket: {len(R2Falso.objetos)}")
    assert quedan["adjuntos"] == []
    assert len(R2Falso.objetos) == 1


def test_borrar_la_compra_deja_renglon_propio_por_cada_soporte(
    client, base_datos, r2, db_session
):
    """EL DEFECTO MECÁNICO QUE QUEDÓ ANOTADO: el gemelo no auditaba.

    `AdjuntoPagoLiquidacionService._barrer` ya audita soporte por soporte cuando
    se borra el pago. `AdjuntoReventaService.limpiar_de_documento` —la clase que
    el propio docstring del otro llama "el gemelo"— borraba las filas y los
    archivos sin escribir un solo renglón. Así, borrar una compra se llevaba las
    fotos de tres transferencias y en la bitácora quedaba únicamente el "eliminar"
    de la compra: ni un rastro de los soportes.

    Es exactamente el dato que alguien va a buscar cuando pregunte dónde quedó la
    prueba de una entrega de plata.
    """
    from app.modules.auditoria.models import Auditoria

    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[foto("giro1.jpg"), foto("giro2.jpg"), foto("giro3.jpg")],
        headers=h,
    )
    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    assert r.status_code in (200, 204), r.text

    renglones = db_session.scalars(
        select(Auditoria).where(
            Auditoria.entidad == "AdjuntoReventa", Auditoria.accion == "eliminar"
        )
    ).all()
    print("\n===== BORRAR LA COMPRA DEJA RENGLÓN POR CADA SOPORTE =====")
    for renglon in renglones:
        print(f"  {renglon.accion} {renglon.entidad} · "
              f"«{renglon.antes['nombre_archivo']}» · motivo: "
              f"{renglon.despues['motivo']}")
    assert len(renglones) == 3, "los soportes desaparecieron sin dejar rastro"
    assert all(r_.despues["motivo"] == "borrar la compra" for r_ in renglones), (
        "el renglón no dice por qué desapareció el soporte"
    )
    # Con quién los borró y qué eran: la bitácora sirve si se puede leer después.
    assert all(r_.usuario_id is not None for r_ in renglones)
    assert {r_.antes["nombre_archivo"] for r_ in renglones} == {
        "giro1.jpg", "giro2.jpg", "giro3.jpg"
    }


def test_si_el_borrado_de_la_compra_se_cae_a_mitad_no_quedan_filas_sin_archivo(
    client, base_datos, r2, db_session, monkeypatch
):
    """Y el otro lado del mismo arreglo: el bucket, de último.

    `limpiar_de_documento` borraba los objetos de R2 de una, antes del resto del
    borrado de la compra y mucho antes del commit —que ocurre afuera, en
    `get_db`—. Un fallo posterior hacía rollback: las filas RESUCITAN con su
    `deleted_at` en nulo y los archivos que nombran ya no existen. El borrado de
    R2 no se deshace; el de la fila sí, así que lo irreversible va de último.
    """
    import app.modules.reventa.service as servicio

    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[foto("giro1.jpg"), foto("giro2.jpg")],
        headers=h,
    )
    claves_antes = set(R2Falso.objetos)
    assert len(claves_antes) == 2

    original = servicio.borrar_del_bucket_al_confirmar

    def _revienta_despues(*a, **k):
        original(*a, **k)
        raise RuntimeError("se cayó la luz antes de confirmar")

    monkeypatch.setattr(servicio, "borrar_del_bucket_al_confirmar", _revienta_despues)
    with pytest.raises(RuntimeError):
        client.delete(f"{API}/compras/{compra['id']}", headers=h)
    monkeypatch.setattr(servicio, "borrar_del_bucket_al_confirmar", original)

    print("\n===== EL BORRADO DE LA COMPRA SE CAYÓ A MITAD =====")
    print(f"  objetos en el bucket: {len(R2Falso.objetos)} (estaban {len(claves_antes)})")
    print(f"  borrados del bucket:  {len(R2Falso.borrados)}")
    assert set(R2Falso.objetos) == claves_antes, (
        "se borraron archivos de una operación que no cuajó"
    )
    assert R2Falso.borrados == []

    db_session.rollback()
    vivas = db_session.scalars(
        select(AdjuntoReventa).where(AdjuntoReventa.deleted_at.is_(None))
    ).all()
    print(f"  filas vivas: {len(vivas)} · y sus llaves siguen en el bucket: "
          f"{all(v.object_key in R2Falso.objetos for v in vivas)}")
    assert len(vivas) == 2
    assert all(v.object_key in R2Falso.objetos for v in vivas), (
        "quedaron filas apuntando a archivos que ya no existen"
    )


# ===========================================================================
# e) Tipo o tamaño no permitido: rechazo con mensaje claro, no un 500
# ===========================================================================
def test_un_archivo_que_no_es_imagen_ni_pdf_se_rechaza_con_mensaje_claro(
    client, base_datos, r2
):
    """El tipo se decide mirando los PRIMEROS BYTES, no la extensión ni el
    Content-Type que manda el navegador: los dos los pone quien sube.

    Y aquí importa de verdad, porque de estos objetos se reparten enlaces
    firmados que abre OTRA persona en su navegador. Un ejecutable o una página
    HTML disfrazados de .jpg serían un archivo peligroso con un enlace que el
    dueño repartió de buena fe por WhatsApp.
    """
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    print("\n===== e1) TIPO NO PERMITIDO =====")

    for nombre, contenido, tipo_declarado in (
        ("notas.txt", TEXTO_PLANO, "text/plain"),
        # Miente en el nombre Y en el Content-Type: aun así no pasa.
        ("transferencia.jpg", EJECUTABLE, "image/jpeg"),
    ):
        r = client.post(
            f"{API}/compras/{compra['id']}/adjuntos",
            files=[("files", (nombre, contenido, tipo_declarado))],
            headers=h,
        )
        detalle = r.json()["error"]["detail"]
        print(f"  {nombre:22} dice ser {tipo_declarado:12} → {r.status_code} · {detalle}")
        assert r.status_code == 422, r.text
        assert nombre in detalle and "PDF" in detalle
        # Nada llegó al almacenamiento
        assert R2Falso.objetos == {}

    assert client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=h).json()[
        "adjuntos"
    ] == []


def test_la_foto_de_iphone_vuelve_a_entrar_en_reventa(client, base_datos, r2):
    """REVENTA ACEPTABA FOTOS DE iPHONE Y DEJÓ DE ACEPTARLAS cuando llegó la
    compresión: Pillow sola no abre HEIC, así que la foto que ANTES se guardaba
    —cruda, pero se guardaba— empezó a rebotar. Una función que el dueño ya usaba,
    perdida sin querer y sin que nadie la pidiera.

    Con `pillow-heif` vuelve, y mejor que antes: ya no se guarda cruda —un archivo
    que el navegador del productor no dibuja— sino comprimida y vuelta JPEG.
    """
    assert heic_se_puede_abrir(), "falta pillow-heif: no se está probando nada"
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    heic = foto_heic_de_iphone(2400, 1800)

    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[("files", ("IMG_2201.HEIC", heic, "image/heic"))],
        headers=h,
    )
    assert r.status_code == 201, r.text
    fila = r.json()["adjuntos"][0]
    guardado, tipo_guardado = list(R2Falso.objetos.values())[0]
    adentro = Image.open(io.BytesIO(guardado))
    print("\n===== e2) LA FOTO DE iPHONE VUELVE A ENTRAR =====")
    print(f"  sube IMG_2201.HEIC ({len(heic) / 1024:.0f} KB) → queda "
          f"«{fila['nombre_archivo']}» {fila['content_type']}, un {adentro.format} "
          f"de {adentro.size[0]} × {adentro.size[1]}")
    assert (fila["content_type"], fila["nombre_archivo"]) == (
        "image/jpeg",
        "IMG_2201.jpg",
    )
    assert tipo_guardado == "image/jpeg" and adentro.format == "JPEG"


def test_una_foto_de_muchisimos_pixeles_no_tumba_el_servidor(client, base_datos, r2):
    """El tope de 15 MB no puede ver esto: mide el archivo COMPRIMIDO. Un PNG de un
    gris plano de 12.000 × 12.000 pesa 157 KB y al abrirlo se lleva 700 MB de
    memoria — y las dos queseras comparten servidor. Se rebota ANTES de abrirlo,
    con las medidas dichas para que se entienda qué pasó.
    """
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    bomba = bomba_de_pixeles(12000, 12000)

    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[("files", ("captura.png", bomba, "image/png"))],
        headers=h,
    )
    detalle = r.json()["error"]["detail"]
    print("\n===== e3) LA BOMBA DE PÍXELES =====")
    print(f"  captura.png · {len(bomba) / 1024:.0f} KB en disco, 144 megapíxeles "
          f"adentro → {r.status_code}")
    print(f"  {detalle}")
    assert r.status_code == 422
    assert "captura.png" in detalle and "demasiado grande" in detalle
    assert R2Falso.objetos == {}
    assert (
        client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=h).status_code == 200
    )


def test_una_captura_png_se_guarda_como_jpg_y_el_nombre_lo_dice(client, base_datos, r2):
    """El nombre que queda es el `nombre_descarga` del enlace firmado: es con el que
    el archivo aterriza en el computador de quien abre lo que le mandaron. Si se
    subió "captura.png", se guardó un JPEG y el nombre siguiera diciendo .png, el
    que lo recibe se baja algo que su equipo no sabe abrir por creerle al nombre —y
    el enlace ya caducó, así que no hay a quién preguntarle.
    """
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    png_grande = io.BytesIO()
    Image.open(io.BytesIO(foto_de_comprobante(2400, 1800))).save(
        png_grande, format="PNG"
    )

    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[("files", ("captura.png", png_grande.getvalue(), "image/png"))],
        headers=h,
    )
    assert r.status_code == 201, r.text
    fila = r.json()["adjuntos"][0]
    clave = list(R2Falso.objetos)[0]
    print("\n===== e4) EL NOMBRE NO MIENTE =====")
    print(f"  sube «captura.png» image/png → queda «{fila['nombre_archivo']}» "
          f"{fila['content_type']} · llave …{clave[-8:]}")
    assert fila["nombre_archivo"] == "captura.jpg"
    assert fila["content_type"] == "image/jpeg"
    assert clave.endswith(".jpg")
    assert Image.open(io.BytesIO(R2Falso.objetos[clave][0])).format == "JPEG"


def test_un_archivo_demasiado_grande_se_rechaza_diciendo_cuanto_pesa(
    client, base_datos, r2
):
    """Una foto de celular pesa varios MB, así que el tope es alto (15 MB). Lo que
    no puede pasar es que un archivo enorme salga con un 500 "Error interno":
    el dueño está en el campo con mala señal y necesita saber que fue el tamaño
    y qué hacer al respecto.
    """
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    grande = JPEG + b"\x00" * (settings.ADJUNTOS_MAX_MB * 1024 * 1024)

    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[("files", ("foto_enorme.jpg", grande, "image/jpeg"))],
        headers=h,
    )
    detalle = r.json()["error"]["detail"]
    print("\n===== e2) TAMAÑO NO PERMITIDO =====")
    print(f"  se mandan {len(grande) / 1024 / 1024:.1f} MB (tope "
          f"{settings.ADJUNTOS_MAX_MB} MB) → {r.status_code}")
    print(f"  {detalle}")
    assert r.status_code == 422
    assert "foto_enorme.jpg" in detalle
    assert str(settings.ADJUNTOS_MAX_MB) in detalle
    assert R2Falso.objetos == {}


def test_si_una_foto_del_lote_no_sirve_no_se_sube_ninguna(client, base_datos, r2):
    """Se validan TODAS antes de subir NINGUNA. Si la tercera no sirve y las dos
    primeras ya quedaron guardadas, el dueño corrige y vuelve a mandar las tres:
    las dos buenas quedarían duplicadas y tendría que borrarlas a mano."""
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[foto("buena1.jpg"), foto("buena2.jpg"),
               ("files", ("mala.txt", TEXTO_PLANO, "text/plain"))],
        headers=h,
    )
    print("\n===== TODO O NADA =====")
    print(f"  3 archivos, uno malo → {r.status_code}")
    print(f"  objetos en el almacenamiento: {len(R2Falso.objetos)}")
    assert r.status_code == 422
    assert R2Falso.objetos == {}
    assert client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=h).json()[
        "adjuntos"
    ] == []


def test_si_el_almacenamiento_se_cae_a_media_subida_no_quedan_archivos_sueltos(
    client, base_datos, r2
):
    """La excepción hace rollback de la sesión y las filas desaparecen. Sin un
    barrido, los archivos que alcanzaron a subir quedarían en el bucket sin
    ninguna fila que los nombre: invisibles, imborrables y cobrando."""
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    R2Falso.revienta_al_subir_en = 1  # la segunda falla

    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[foto("una.jpg"), foto("dos.jpg")],
        headers=h,
    )
    print("\n===== R2 SE CAE A MEDIA SUBIDA =====")
    print(f"  POST: {r.status_code} · {r.json()['error']['detail']}")
    print(f"  objetos que quedaron: {len(R2Falso.objetos)} · barridos: {len(R2Falso.borrados)}")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "r2_error"
    assert R2Falso.objetos == {}, "quedó un archivo huérfano en el bucket"
    assert len(R2Falso.borrados) == 1

    R2Falso.revienta_al_subir_en = -1
    assert client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=h).json()[
        "adjuntos"
    ] == []


# ===========================================================================
# f) Sin R2 configurado, el resto del módulo sigue funcionando
# ===========================================================================
def test_sin_almacenamiento_configurado_el_modulo_sigue_funcionando(client, base_datos):
    """El caso de las pruebas y el de un portátil sin llaves. La aplicación tiene
    que arrancar igual —si no, no habría llegado ni aquí— y TODO el módulo de
    reventa tiene que seguir usable. Lo único que avisa que no está disponible es
    la parte de adjuntos, y avisa con un mensaje que se entiende, no con un 500.

    Ojo: esta prueba NO usa el doble de R2 a propósito. Corre con la
    configuración de verdad, que en pruebas viene sin llaves.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== f) SIN R2 CONFIGURADO =====")

    # 1. El módulo entero funciona: comprar, vender, resumen, lotes
    compra = comprar(client, h, kilos="100")
    venta = vender(client, h, kilos="40")
    resumen = client.get(
        f"{API}/resumen",
        params={"desde": str(date.today() - timedelta(days=30)), "hasta": str(date.today())},
        headers=h,
    )
    lotes = client.get(f"{API}/lotes", headers=h)
    print(f"  comprar: 201 · vender: 201 · resumen: {resumen.status_code} · "
          f"lotes: {lotes.status_code}")
    assert resumen.status_code == 200 and lotes.status_code == 200
    assert resumen.json()["kilos_comprados"] == "100.00"

    # 2. Listar adjuntos responde 200 con el aviso, no un error: no es culpa de
    #    quien pregunta y la pantalla tiene que poder seguir usándose.
    listado = client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=h)
    cuerpo = listado.json()
    print(f"  listar adjuntos: {listado.status_code} · disponible: {cuerpo['disponible']}")
    print(f"  mensaje: {cuerpo['mensaje']}")
    assert listado.status_code == 200
    assert cuerpo["disponible"] is False
    assert cuerpo["adjuntos"] == []
    assert "no está configurado" in cuerpo["mensaje"]

    # 3. Subir avisa con un mensaje legible y un código que la pantalla reconoce
    for ruta in (f"compras/{compra['id']}", f"ventas/{venta['id']}"):
        r = client.post(f"{API}/{ruta}/adjuntos", files=[foto()], headers=h)
        print(f"  subir a {ruta.split('/')[0]:8}: {r.status_code} · "
              f"{r.json()['error']['code']}")
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "r2_no_configurado"

    # 4. Y la compra sigue diciendo, sin reventar, que no tiene soportes
    fila = client.get(f"{API}/compras", headers=h).json()["items"][0]
    assert fila["adjuntos_count"] == 0


# ===========================================================================
# Compartir: enlace de MÁS duración, con la caducidad escrita
# ===========================================================================
def test_compartir_da_un_enlace_mas_largo_y_dice_hasta_cuando_sirve(
    client, base_datos, r2
):
    """El dueño quiere mandarle el soporte a alguien por WhatsApp, y ese enlace
    no puede ser el mismo de la pantalla: quince minutos no alcanzan cuando el
    que lo recibe está en una vereda y abre el chat en la noche.

    Siete días es el TOPE DURO de una URL firmada con SigV4: más no se puede
    aunque se configure. Y cubre el caso real de "te mando el soporte" el jueves
    y "lo vi el domingo".

    La fecha de caducidad la arma el BACKEND y en hora de Colombia: quien reparte
    un enlace a un comprobante de pago tiene que saber hasta cuándo sirve lo que
    está repartiendo. Si la frase la construyera cada pantalla, tarde o temprano
    una la mostraría en UTC —cinco horas corridas— o no la mostraría.
    """
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    subida = client.post(
        f"{API}/compras/{compra['id']}/adjuntos", files=[foto("giro.jpg")], headers=h
    ).json()
    adjunto_id = subida["adjuntos"][0]["id"]
    segundos_ver = R2Falso.firmas[-1][1]

    r = client.post(f"{API}/adjuntos/{adjunto_id}/compartir", headers=h)
    print("\n===== COMPARTIR =====")
    assert r.status_code == 200, r.text
    enlace = r.json()
    print(f"  url: {enlace['url'][:90]}...")
    print(f"  dias: {enlace['dias']} · texto: «Este enlace sirve {enlace['expira_texto']}»")
    assert "X-Amz-Signature=" in enlace["url"]
    assert enlace["dias"] == 7
    assert enlace["nombre_archivo"] == "giro.jpg"
    assert enlace["expira_texto"].startswith("hasta el ")

    segundos_compartir = R2Falso.firmas[-1][1]
    print(f"  ver dura {segundos_ver} s · compartir dura {segundos_compartir} s")
    assert segundos_compartir == 7 * 24 * 3600
    assert segundos_compartir > segundos_ver, "compartir tiene que durar más que ver"

    # La caducidad que se le promete al usuario cuadra con la firma
    expira = datetime.fromisoformat(enlace["expira"])
    faltan = (expira - datetime.now(timezone.utc)).total_seconds()
    assert abs(faltan - segundos_compartir) < 10

    # Queda en la auditoría: es información de pago saliendo del sistema hacia un
    # enlace que cualquiera que lo reciba puede reenviar.
    from app.modules.auditoria.models import Auditoria

    registros = client.get(
        "/api/v1/auditoria", params={"accion": "compartir"}, headers=h
    )
    assert registros.status_code == 200, registros.text
    assert registros.json()["total"] >= 1, "compartir tiene que quedar auditado"
    anotado = registros.json()["items"][0]
    print(f"  auditoría: {anotado['accion']} · {anotado['entidad']}")
    assert anotado["entidad"] == "AdjuntoReventa"
    # La URL NO se guarda en la auditoría: lleva la firma dentro, así que
    # guardarla sería guardar el acceso.
    assert "X-Amz-Signature" not in str(anotado.get("despues"))
    assert Auditoria is not None


def test_ver_lo_puede_todo_el_que_consulta_pero_compartir_no(
    client, base_datos, db_session, r2
):
    """Compartir saca el soporte DEL SISTEMA hacia afuera: el enlace se reenvía y
    ya no hay forma de recogerlo. Por eso pide 'exportar', la misma acción que ya
    exige sacar datos en los demás módulos, y no 'consultar'.

    Con eso, un rol de solo consulta puede mirar el comprobante en la pantalla
    —que es lo que necesita para cuadrar— pero no repartirlo.
    """
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Consulta", "mirona")

    ha = auth_headers(client, "admin.a")
    hc = auth_headers(client, "mirona")
    compra = comprar(client, ha)
    adjunto_id = client.post(
        f"{API}/compras/{compra['id']}/adjuntos", files=[foto()], headers=ha
    ).json()["adjuntos"][0]["id"]

    print("\n===== VER SÍ, COMPARTIR NO =====")
    ver = client.get(f"{API}/compras/{compra['id']}/adjuntos", headers=hc)
    print(f"  el rol Consulta lo ve:      {ver.status_code}")
    assert ver.status_code == 200 and ver.json()["adjuntos"][0]["url"]

    compartir = client.post(f"{API}/adjuntos/{adjunto_id}/compartir", headers=hc)
    print(f"  el rol Consulta lo comparte: {compartir.status_code} · "
          f"{compartir.json()['error']['detail']}")
    assert compartir.status_code == 403
    assert "exportar" in compartir.json()["error"]["detail"]


# ===========================================================================
# Topes y bordes
# ===========================================================================
def test_no_caben_mas_soportes_de_los_permitidos_por_documento(
    client, base_datos, r2, monkeypatch
):
    """Esto es el respaldo de que se pagó, no un álbum. El tope evita que un error
    de la interfaz (o alguien con malas intenciones) llene el bucket a costa del
    dueño, que es quien paga el almacenamiento."""
    monkeypatch.setattr(settings, "ADJUNTOS_MAX_POR_DOCUMENTO", 3)
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)

    primera = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[foto("a.jpg"), foto("b.jpg")],
        headers=h,
    )
    print("\n===== TOPE POR DOCUMENTO (3 en esta prueba) =====")
    print(f"  suben 2: {primera.status_code} · cupo restante: "
          f"{primera.json()['cupo_restante']}")
    assert primera.status_code == 201
    assert primera.json()["cupo_restante"] == 1

    pasada = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[foto("c.jpg"), foto("d.jpg")],
        headers=h,
    )
    print(f"  suben 2 más: {pasada.status_code} · {pasada.json()['error']['detail']}")
    assert pasada.status_code == 422
    assert "máximo 3" in pasada.json()["error"]["detail"]
    # Ninguna de las dos entró: se comprueba el cupo ANTES de subir nada.
    assert len(R2Falso.objetos) == 2


def test_no_se_le_cuelgan_soportes_a_una_compra_anulada(client, base_datos, r2):
    """Una compra anulada es un documento muerto: no se le registran abonos y
    tampoco se le siguen colgando soportes de pago. Si se pudiera, quedaría un
    comprobante pegado a un documento que dice que no existió."""
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    client.post(f"{API}/compras/{compra['id']}/anular", headers=h)

    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos", files=[foto()], headers=h
    )
    print("\n===== COMPRA ANULADA =====")
    print(f"  adjuntar a una compra anulada: {r.status_code} · "
          f"{r.json()['error']['detail']}")
    assert r.status_code == 422
    assert "anulado" in r.json()["error"]["detail"]
    assert R2Falso.objetos == {}


def test_borrar_la_compra_se_lleva_sus_soportes(client, base_datos, r2):
    """Sin esto, borrar una compra dejaba sus fotos en el bucket PARA SIEMPRE: el
    documento ya no existe, así que nadie las puede ver ni borrar desde la
    aplicación, y el dueño sigue pagando ese almacenamiento sin enterarse.

    El caso al revés también se prueba: si la compra NO se puede borrar (tiene
    abonos), no se le puede tocar ni una foto. Se validan las dos cosas en el
    mismo sitio porque el orden es justo lo que hace la diferencia.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== BORRAR EL DOCUMENTO SE LLEVA LOS SOPORTES =====")

    # 1. Una compra con abonos NO se borra: sus soportes no se tocan.
    con_abono = comprar(client, h, kilos="10", productor="Marta")
    client.post(
        f"{API}/compras/{con_abono['id']}/adjuntos", files=[foto("de_marta.jpg")], headers=h
    )
    client.post(
        f"{API}/compras/{con_abono['id']}/abonos",
        json={"fecha": str(date.today()), "valor": "1000"},
        headers=h,
    )
    negado = client.delete(f"{API}/compras/{con_abono['id']}", headers=h)
    print(f"  borrar una compra CON abonos: {negado.status_code} (no se toca nada)")
    assert negado.status_code == 422
    assert len(R2Falso.objetos) == 1 and R2Falso.borrados == []

    # 2. Una compra sin abonos sí se borra, y se lleva sus fotos.
    compra = comprar(client, h, kilos="20", productor="Yeferson")
    client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[foto("giro1.jpg"), foto("giro2.jpg")],
        headers=h,
    )
    print(f"  antes de borrar: {len(R2Falso.objetos)} objetos")
    r = client.delete(f"{API}/compras/{compra['id']}", headers=h)
    assert r.status_code == 204
    print(f"  después:         {len(R2Falso.objetos)} objetos "
          f"(quedó solo el de la compra que sobrevive)")
    assert len(R2Falso.objetos) == 1
    assert len(R2Falso.borrados) == 2


def test_reiniciar_la_empresa_se_lleva_los_archivos_del_bucket(
    client, base_datos, r2, monkeypatch
):
    """Reiniciar una empresa borra sus movimientos. Si las filas de los adjuntos
    se van pero los archivos no, quedan en el bucket comprobantes de pago de una
    empresa que se supone que quedó en ceros — invisibles y cobrando.

    Se comprueba además que NO se lleva los de la OTRA empresa: es la clase de
    error que solo se nota cuando ya no hay nada que recuperar.
    """
    # El reinicio ya no instancia el cliente: encarga el borrado a
    # `borrar_del_bucket_al_confirmar`, que vive en `app.core.storage` y solo
    # corre si la transacción cuaja. Así que el doble se enchufa AHÍ.
    import app.core.storage as almacenamiento

    monkeypatch.setattr(almacenamiento, "R2Client", R2Falso)
    monkeypatch.setattr(almacenamiento, "r2_configurado", lambda: True)

    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    compra_a = comprar(client, ha)
    compra_b = comprar(client, hb, productor="Otro")
    client.post(f"{API}/compras/{compra_a['id']}/adjuntos", files=[foto("a1.jpg")], headers=ha)
    client.post(f"{API}/compras/{compra_b['id']}/adjuntos", files=[foto("b1.jpg")], headers=hb)

    empresa_a = base_datos["empresa_a"]
    clave_b = next(k for k in R2Falso.objetos if str(empresa_a.id) not in k)

    hs = auth_headers(client, "superadmin")
    print("\n===== REINICIAR LA EMPRESA =====")
    print(f"  antes: {len(R2Falso.objetos)} objetos (uno por empresa)")
    r = client.post(
        f"/api/v1/empresas/{empresa_a.id}/reiniciar",
        json={"confirmacion": empresa_a.nombre},
        headers={**hs, "X-Empresa-Id": str(empresa_a.id)},
    )
    assert r.status_code == 200, r.text
    print(f"  borrados: {r.json().get('adjuntos_reventa')} filas de adjuntos_reventa")
    print(f"  después: {len(R2Falso.objetos)} objetos · quedó el de la empresa B")
    assert r.json()["adjuntos_reventa"] == 1
    assert list(R2Falso.objetos) == [clave_b], "se borró un archivo de la otra empresa"

    # Y a la empresa B no le pasó nada: su soporte sigue listándose.
    quedan = client.get(f"{API}/compras/{compra_b['id']}/adjuntos", headers=hb)
    assert quedan.status_code == 200 and len(quedan.json()["adjuntos"]) == 1


def test_un_archivo_vacio_no_pasa(client, base_datos, r2):
    """Pasa de verdad: se toca "adjuntar" antes de que el celular termine de
    guardar la foto y llega un archivo de cero bytes. Sin este control quedaría
    una fila que promete un soporte y un enlace que abre una imagen rota."""
    h = auth_headers(client, "admin.a")
    compra = comprar(client, h)
    r = client.post(
        f"{API}/compras/{compra['id']}/adjuntos",
        files=[("files", ("vacia.jpg", b"", "image/jpeg"))],
        headers=h,
    )
    print("\n===== ARCHIVO VACÍO =====")
    print(f"  {r.status_code} · {r.json()['error']['detail']}")
    assert r.status_code == 422
    assert "vacío" in r.json()["error"]["detail"]
