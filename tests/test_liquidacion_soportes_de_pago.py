"""Soportes de pago (la foto de la transferencia) en los PAGOS de liquidación.

LO QUE PIDIÓ EL DUEÑO, textual: "que se le puedan agregar los comprobantes a los
pagos de los proveedores, y de paso también le reducimos la calidad para ahorrar
espacio". Hasta hoy solo reventa tenía adjuntos; las liquidaciones —que son las
que mueven la plata de los productores y de los transportadores— no.

Es el gemelo de tests/test_reventa_adjuntos.py y comparte con él el doble de R2 y
las imágenes: mismo bucket, mismas reglas, misma disciplina de llaves. Lo que se
prueba acá es lo que cambia:

  (a) subir, listar y volver a listar, con la llave llevando el empresa_id dentro;
  (b) que sirva IGUAL para el pago de un productor (leche) y el de un
      transportador (flete): las dos clases de liquidación usan la misma tabla de
      pagos, y si una funcionara y la otra no, el dueño lo descubriría el día que
      le va a mandar el soporte al conductor;
  (c) el enlace es FIRMADO y de corta duración, y en la base no queda ninguna URL;
  (d) la Quesera B no lista, ni sube, ni comparte, ni borra nada de la Quesera A
      —los CUATRO verbos— y no se le firma ni medio enlace por el camino;
  (e) los permisos: subir pide 'administrar' (el mismo que registrar el pago),
      compartir pide 'exportar' y borrar pide 'eliminar';
  (f) borrar el soporte se lleva el archivo del bucket; borrar el PAGO se lleva
      los suyos; y borrar la LIQUIDACIÓN se lleva los de todos sus pagos;
  (g) los topes de tamaño y de cantidad, con mensajes que se entienden;
  (h) el PDF del banco pasa derecho y una foto de verdad se guarda COMPRIMIDA.
"""
import io
from decimal import Decimal

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import settings
from app.modules.liquidaciones.models import AdjuntoPagoLiquidacion
from tests.ayudas_imagenes import (
    JPEG,
    PDF,
    PNG,
    TEXTO_PLANO,
    bomba_de_pixeles,
    foto_de_celular,
    foto_heic_de_iphone,
    heic_se_puede_abrir,
)
from tests.ayudas_r2 import R2Falso, enchufar
from tests.conftest import PASSWORD, auth_headers

API = "/api/v1/liquidaciones"


def foto(nombre="transferencia.jpg", contenido=JPEG, tipo="image/jpeg"):
    """Un archivo listo para el multipart de httpx."""
    return ("files", (nombre, contenido, tipo))


@pytest.fixture()
def r2(monkeypatch):
    """Enchufa el doble (ver `tests/ayudas_r2.py::enchufar`)."""
    import app.modules.liquidaciones.service as servicio

    return enchufar(monkeypatch, servicio)


# ------------------------------------------------------------------ montaje
def liquidacion_de_leche(client, h, *, nombre="Libardo", litros="250", precio="1800"):
    """Un productor con un día anotado y su quincena APROBADA."""
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    ).json()
    r = client.post(
        "/api/v1/recepciones",
        json={
            "fecha": "2026-06-02",
            "proveedor_id": proveedor["id"],
            "cantidad_litros": litros,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    generadas = client.post(
        f"{API}/generar",
        json={
            "periodo_inicio": "2026-06-01",
            "periodo_fin": "2026-06-15",
            "tipo": "proveedor",
        },
        headers=h,
    ).json()["generadas"]
    liq = next(x for x in generadas if x["proveedor_id"] == proveedor["id"])
    aprobada = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert aprobada.status_code == 200, aprobada.text
    return aprobada.json()


def liquidacion_de_flete(client, h, *, nombre="Alex", litros="227.55", tarifa="242.76"):
    """Un transportador con un viaje anotado y su comprobante de flete APROBADO."""
    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "Napoles", "municipio": "Granada"}, headers=h
    ).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={
            "nombre": nombre,
            "valor_transporte": "100",
            "rutas": [{"ruta_id": ruta["id"], "valor_transporte": tarifa}],
        },
        headers=h,
    ).json()
    proveedor = client.post(
        "/api/v1/proveedores",
        json={
            "nombre": "Patricia",
            "vereda": "El Roble",
            "precio_litro": "1800",
            "ruta_id": ruta["id"],
        },
        headers=h,
    ).json()
    r = client.post(
        "/api/v1/recepciones",
        json={
            "fecha": "2026-06-02",
            "proveedor_id": proveedor["id"],
            "transportador_id": transportador["id"],
            "ruta_id": ruta["id"],
            "cantidad_litros": litros,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    generadas = client.post(
        f"{API}/generar",
        json={
            "periodo_inicio": "2026-06-01",
            "periodo_fin": "2026-06-15",
            "tipo": "transportador",
        },
        headers=h,
    ).json()["generadas"]
    liq = generadas[0]
    aprobada = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert aprobada.status_code == 200, aprobada.text
    return aprobada.json()


def pagar(client, h, liq, valor="200000", fecha="2026-06-16"):
    """Registra un pago parcial y devuelve (liquidación, pago)."""
    r = client.post(
        f"{API}/{liq['id']}/pagos",
        json={"fecha": fecha, "valor": str(valor), "observaciones": "Transferencia"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    return cuerpo, cuerpo["pagos"][-1]


def montar_con_pago(client, h, **kwargs):
    liq = liquidacion_de_leche(client, h, **kwargs)
    return pagar(client, h, liq)


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


def ruta_adjuntos(liq, pago):
    return f"{API}/{liq['id']}/pagos/{pago['id']}/adjuntos"


# ===========================================================================
# a) Subir DOS soportes al pago de un productor y volverlos a listar
# ===========================================================================
def test_subir_dos_soportes_al_pago_de_un_productor(client, base_datos, r2, db_session):
    """El caso de todos los días: se le paga la quincena a Libardo en dos giros
    (el banco tiene tope diario) y se pegan las DOS fotos al mismo pago.

    Se comprueba de paso que la llave del objeto lleve el empresa_id ADENTRO: es
    la segunda barrera contra ver el archivo de otra quesera. Aunque una consulta
    se escapara sin filtro, la llave que se firmaría empieza por un uuid de
    empresa que no es el suyo, y eso se ve en la auditoría.
    """
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)

    r = client.post(
        ruta_adjuntos(liq, pago),
        files=[foto("giro1.jpg", JPEG), foto("giro2.png", PNG, "image/png")],
        headers=h,
    )
    print("\n===== a) DOS SOPORTES AL PAGO DE UN PRODUCTOR =====")
    print(f"  la quincena de Libardo: neto {liq['neto_a_pagar']} · "
          f"pago de {pago['valor']} · estado {liq['estado']}")
    print(f"  POST adjuntos: {r.status_code}")
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["disponible"] is True
    assert len(cuerpo["adjuntos"]) == 2
    for a in cuerpo["adjuntos"]:
        print(f"    {a['nombre_archivo']:16} {a['content_type']:12} "
              f"{a['tamano_bytes']} bytes · subió: {a['subido_por_nombre']}")
        assert a["pago_id"] == pago["id"]
        assert a["es_imagen"] is True
        assert a["subido_por_nombre"] == "Admin.A Prueba"

    # Y al volver a pedirlas, siguen ahí (esto es lo que hace la pantalla al abrir)
    listado = client.get(ruta_adjuntos(liq, pago), headers=h).json()
    print(f"  GET adjuntos: {len(listado['adjuntos'])} soportes · "
          f"cupo restante: {listado['cupo_restante']}")
    assert len(listado["adjuntos"]) == 2

    # Los dos archivos están de verdad en el almacenamiento, con su tipo
    assert len(R2Falso.objetos) == 2
    empresa_a = base_datos["empresa_a"].id
    for clave in R2Falso.objetos:
        print(f"    llave: {clave}")
        assert clave.startswith(f"{empresa_a}/liquidaciones/pagos/{pago['id']}/")

    # El número sale también en el pago, para ver de un vistazo cuáles tienen
    # respaldo de la transferencia y cuáles no.
    #
    # `expire_all` es un detalle DE LA PRUEBA y no del sistema: acá todas las
    # peticiones comparten UNA sesión (ver conftest), así que el pago que quedó en
    # memoria con su lista de soportes vacía se devuelve tal cual. En producción
    # cada petición abre su propia sesión y la lista se carga fresca. Sin esto, la
    # prueba mediría el caché de SQLAlchemy en vez del dato.
    db_session.expire_all()
    detalle = client.get(f"{API}/{liq['id']}", headers=h).json()
    print(f"  el pago en la liquidación dice: {detalle['pagos'][0]['adjuntos_count']} soportes")
    assert detalle["pagos"][0]["adjuntos_count"] == 2


def test_los_soportes_de_un_mismo_envio_se_listan_en_el_orden_en_que_se_mandaron(
    client, base_datos, r2
):
    """Dos fotos en UN SOLO envío: la lista las devuelve en el orden en que salieron.

    El repositorio promete "primero la que mandó primero" (ver `de_pago`) y esa
    promesa se apoyaba en la hora de la base, que NO alcanza a distinguir dos filas
    del mismo envío: en Postgres `now()` es la hora de la TRANSACCIÓN —las dos fotos
    de un POST quedan con el MISMO instante exacto— y en SQLite CURRENT_TIMESTAMP va
    de segundo en segundo. Con la hora empatada, cuál sale de primera la decide el
    motor, y el dueño ve el segundo giro arriba del primero.

    Es la HERMANA del temblor de la bitácora (ver
    tests/test_liquidacion_flete_recalculo_adversarial.py) y se arregló igual: la
    hora la escribe la aplicación, con microsegundos. Por eso acá se comprueban las
    dos cosas —el orden Y que las horas sean distintas—: sin lo segundo, lo primero
    vuelve a ser cuestión de suerte.
    """
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)

    res = client.post(
        ruta_adjuntos(liq, pago),
        files=[foto("giro1.jpg", JPEG), foto("giro2.png", PNG, "image/png")],
        headers=h,
    )
    assert res.status_code == 201, res.text

    listado = client.get(ruta_adjuntos(liq, pago), headers=h).json()["adjuntos"]
    nombres = [a["nombre_archivo"] for a in listado]
    print("\n===== LOS DOS GIROS, EN EL ORDEN EN QUE SE MANDARON =====")
    print("  se mandaron: ['giro1.jpg', 'giro2.png']")
    print(f"  se listan  : {nombres}")
    assert nombres == ["giro1.jpg", "giro2.png"], (
        f"se mandaron giro1 y luego giro2 y la lista los devuelve {nombres}: "
        "el dueño ve el segundo giro arriba del primero"
    )

    horas = [a["created_at"] for a in listado]
    print(f"  sus horas  : {horas}")
    assert horas[0] != horas[1], (
        f"las dos fotos del mismo envío quedaron con LA MISMA hora ({horas[0]}): con "
        "la hora empatada, en qué orden se listan lo decide el motor"
    )


def test_lo_mismo_funciona_en_el_pago_del_transportador(client, base_datos, r2):
    """La liquidación de FLETE usa la misma tabla de pagos, así que el soporte se
    le pega igual al comprobante del conductor. Se prueba aparte y no se da por
    hecho: es la mitad del pedido del dueño y si fallara, lo descubriría el día
    que le va a mandar el comprobante a Alex."""
    h = auth_headers(client, "admin.a")
    flete = liquidacion_de_flete(client, h)
    liq, pago = pagar(client, h, flete, valor="30000")

    r = client.post(
        ruta_adjuntos(liq, pago), files=[foto("pago_alex.jpg")], headers=h
    )
    print("\n===== EL PAGO DEL TRANSPORTADOR =====")
    print(f"  comprobante de flete · neto {flete['neto_a_pagar']} · "
          f"tipo {flete['tipo']} · POST adjuntos: {r.status_code}")
    assert r.status_code == 201, r.text
    adjunto = r.json()["adjuntos"][0]
    assert adjunto["pago_id"] == pago["id"]
    clave = list(R2Falso.objetos)[0]
    print(f"  llave: {clave}")
    assert f"/liquidaciones/pagos/{pago['id']}/" in clave


def test_se_acepta_el_pdf_del_banco(client, base_datos, r2):
    """Los bancos colombianos entregan el comprobante como PDF descargable, y ese
    PDF es MEJOR soporte que una foto de la pantalla: se guarda TAL CUAL, sin
    pasarlo por el compresor. Comprimirlo sería volver texto nítido en una foto
    de texto."""
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    r = client.post(
        ruta_adjuntos(liq, pago),
        files=[("files", ("comprobante.pdf", PDF, "application/pdf"))],
        headers=h,
    )
    print("\n===== EL PDF DEL BANCO =====")
    print(f"  POST comprobante.pdf: {r.status_code}")
    assert r.status_code == 201, r.text
    adjunto = r.json()["adjuntos"][0]
    print(f"  tipo guardado: {adjunto['content_type']} · es_imagen: {adjunto['es_imagen']} · "
          f"{adjunto['tamano_bytes']} bytes (entraron {len(PDF)})")
    assert adjunto["content_type"] == "application/pdf"
    assert adjunto["tamano_bytes"] == len(PDF), "el PDF no puede salir alterado"
    # No es imagen: la pantalla tiene que mostrar un icono de documento, no
    # intentar dibujar una miniatura que saldría rota.
    assert adjunto["es_imagen"] is False
    assert list(R2Falso.objetos.values())[0][0] == PDF


# ===========================================================================
# h) La foto se guarda COMPRIMIDA (que es la otra mitad del pedido)
# ===========================================================================
def test_la_foto_del_celular_se_guarda_comprimida(client, base_datos, r2):
    """"De paso también le reducimos la calidad para ahorrar espacio."

    Se sube la foto tal como sale del celular y se comprueba lo que quedó EN EL
    BUCKET, que es lo que se paga: menos de una décima parte, con el lado mayor
    en 1600 px y ya como JPEG aunque hubiera entrado como PNG.

    Lo que se mide de la compresión misma —que la prueba siga legible, la
    orientación, los formatos que no se pueden abrir— está en
    tests/test_compresion_soportes.py.
    """
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    original = foto_de_celular()

    r = client.post(
        ruta_adjuntos(liq, pago),
        files=[("files", ("IMG_0421.jpg", original, "image/jpeg"))],
        headers=h,
    )
    assert r.status_code == 201, r.text
    adjunto = r.json()["adjuntos"][0]
    guardado, tipo = list(R2Falso.objetos.values())[0]
    imagen = Image.open(io.BytesIO(guardado))

    print("\n===== LA FOTO SE GUARDA COMPRIMIDA =====")
    print(f"  entra:  {len(original) / 1024 / 1024:.2f} MB (4032 × 3024)")
    print(f"  guarda: {len(guardado) / 1024:.0f} KB ({imagen.size[0]} × {imagen.size[1]}) {tipo}")
    print(f"  ahorro: {100 * (1 - len(guardado) / len(original)):.1f} %")
    assert len(guardado) < len(original) / 5, "la foto no se comprimió"
    assert max(imagen.size) == 1600
    # Lo que se anota en la base es el peso de lo que QUEDÓ, no el de lo que
    # entró: es lo que se está pagando y lo que hay que poder sumar para saber
    # cuánto ocupa la empresa.
    assert adjunto["tamano_bytes"] == len(guardado)


# ===========================================================================
# c) El enlace es FIRMADO y de corta duración, no una URL pública
# ===========================================================================
def test_el_enlace_es_firmado_y_corto_y_en_la_base_no_hay_ninguna_url(
    client, base_datos, r2, db_session
):
    """Los archivos son PRIVADOS, y eso significa dos cosas que se verifican acá:

    1. En la BASE no hay ninguna URL. Solo la llave del objeto. Una URL guardada
       en una columna es un permiso permanente: quien la viera en un backup, en un
       log o en un export vería el comprobante de la transferencia para siempre,
       con el nombre, la cuenta y el monto.
    2. Lo que se entrega es un enlace FIRMADO que caduca en minutos. Si esa URL
       queda en el historial del navegador o en la vista previa de un chat, ya no
       sirve para cuando alguien la encuentre.
    """
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    client.post(ruta_adjuntos(liq, pago), files=[foto()], headers=h)

    adjunto = client.get(ruta_adjuntos(liq, pago), headers=h).json()["adjuntos"][0]
    print("\n===== c) EL ENLACE ES FIRMADO Y CORTO =====")
    print(f"  url: {adjunto['url'][:90]}...")
    assert "X-Amz-Signature=" in adjunto["url"], "el enlace no viene firmado"
    assert "X-Amz-Expires=" in adjunto["url"], "el enlace no trae caducidad"

    _, segundos = R2Falso.firmas[-1]
    print(f"  dura {segundos} segundos ({segundos // 60} minutos)")
    assert segundos == settings.R2_URL_VER_MINUTOS * 60
    assert 60 <= segundos <= 3600, "el enlace de ver no puede durar horas"

    fila = db_session.scalars(select(AdjuntoPagoLiquidacion)).one()
    guardado = {
        c.key: getattr(fila, c.key)
        for c in AdjuntoPagoLiquidacion.__mapper__.column_attrs
    }
    print(f"  object_key en la base: {guardado['object_key']}")
    con_url = [
        k for k, v in guardado.items() if isinstance(v, str) and v.startswith("http")
    ]
    assert con_url == [], f"se guardó una URL en la base: {con_url}"


def test_compartir_da_un_enlace_mas_largo_y_dice_hasta_cuando_sirve(
    client, base_datos, r2
):
    """El productor llama a preguntar si ya le consignaron y el dueño le manda la
    foto por WhatsApp. Ese enlace no puede ser el mismo de la pantalla: quince
    minutos no alcanzan cuando el que lo recibe está en una vereda y abre el chat
    en la noche. Siete días es el TOPE DURO de una URL firmada con SigV4.

    Queda en la auditoría: es información de pago saliendo del sistema hacia un
    enlace que cualquiera que lo reciba puede reenviar.
    """
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    adjunto_id = client.post(
        ruta_adjuntos(liq, pago), files=[foto("giro.jpg")], headers=h
    ).json()["adjuntos"][0]["id"]
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

    registros = client.get(
        "/api/v1/auditoria", params={"accion": "compartir"}, headers=h
    )
    assert registros.status_code == 200, registros.text
    anotado = registros.json()["items"][0]
    print(f"  auditoría: {anotado['accion']} · {anotado['entidad']}")
    assert anotado["entidad"] == "AdjuntoPagoLiquidacion"
    # La URL NO se guarda en la auditoría: lleva la firma dentro, así que
    # guardarla sería guardar el acceso.
    assert "X-Amz-Signature" not in str(anotado.get("despues"))


# ===========================================================================
# d) Una quesera NO ve, ni sube, ni comparte, ni borra lo de la otra
# ===========================================================================
def test_la_otra_quesera_no_puede_hacer_nada_con_el_soporte(client, base_datos, r2):
    """El aislamiento entre empresas es la regla que no se rompe en este sistema,
    y acá pesa más que en cualquier otra tabla: un soporte de transferencia trae
    el nombre, la cuenta y el monto de un pago real a una persona real.

    Se prueban los CUATRO verbos —listar, subir, compartir y borrar—, porque cada
    uno entra por una ruta distinta, y además que no se le haya firmado NADA al
    intruso: si se firmara y después se negara, el enlace ya estaría hecho.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    liq, pago = montar_con_pago(client, ha)
    adjunto_id = client.post(
        ruta_adjuntos(liq, pago), files=[foto()], headers=ha
    ).json()["adjuntos"][0]["id"]

    firmas_antes = len(R2Falso.firmas)
    print("\n===== d) UNA QUESERA NO VE LO DE LA OTRA =====")

    ver = client.get(ruta_adjuntos(liq, pago), headers=hb)
    print(f"  la Quesera B lista los soportes de A:   {ver.status_code}")
    assert ver.status_code == 404

    subir = client.post(ruta_adjuntos(liq, pago), files=[foto("intruso.jpg")], headers=hb)
    print(f"  la Quesera B sube un soporte al pago de A: {subir.status_code}")
    assert subir.status_code == 404

    compartir = client.post(f"{API}/adjuntos/{adjunto_id}/compartir", headers=hb)
    print(f"  la Quesera B comparte un soporte de A:  {compartir.status_code}")
    assert compartir.status_code == 404

    borrar = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=hb)
    print(f"  la Quesera B borra un soporte de A:     {borrar.status_code}")
    assert borrar.status_code == 404

    # Nada se firmó por el camino: ni un enlace a medio hacer.
    print(f"  enlaces firmados para la Quesera B: {len(R2Falso.firmas) - firmas_antes}")
    assert len(R2Falso.firmas) == firmas_antes
    # Y el archivo sigue en el almacenamiento, intacto y sin compañía.
    assert len(R2Falso.objetos) == 1 and R2Falso.borrados == []

    # La dueña sí lo ve, para que quede claro que el 404 es por la empresa y no
    # porque el soporte estuviera roto.
    suyo = client.get(ruta_adjuntos(liq, pago), headers=ha)
    print(f"  la Quesera A (la dueña) lo lista:       {suyo.status_code}")
    assert suyo.status_code == 200 and len(suyo.json()["adjuntos"]) == 1


def test_no_se_le_cuelga_un_soporte_al_pago_de_otra_liquidacion(client, base_datos, r2):
    """El pago no lleva empresa_id propio: se entra SIEMPRE por su liquidación. Si
    alguien mezcla los dos ids —el pago de una quincena bajo la liquidación de
    otra— tiene que salir 404, no un soporte colgado donde no va."""
    h = auth_headers(client, "admin.a")
    liq_uno, pago_uno = montar_con_pago(client, h, nombre="Libardo")
    liq_dos, _ = montar_con_pago(client, h, nombre="Marta")

    r = client.post(
        f"{API}/{liq_dos['id']}/pagos/{pago_uno['id']}/adjuntos",
        files=[foto()],
        headers=h,
    )
    print("\n===== EL PAGO DE OTRA LIQUIDACIÓN =====")
    print(f"  el pago de Libardo bajo la liquidación de Marta: {r.status_code}")
    assert r.status_code == 404
    assert R2Falso.objetos == {}


# ===========================================================================
# e) Los permisos
# ===========================================================================
def test_subir_exige_administrar_el_mismo_permiso_que_registrar_el_pago(
    client, base_datos, db_session, r2
):
    """El soporte es la prueba de una entrega de plata, así que lo aporta quien
    hace esa entrega: 'administrar', el mismo permiso que ya exigía registrar el
    pago. Con 'crear' —el permiso de generar la quincena, que tiene el rol
    Compras— cualquiera que arma liquidaciones podría colgarle un comprobante a un
    pago que no puede hacer.

    El rol 'Compras' que siembra el sistema sirve de conejillo: en liquidaciones
    puede crear, editar, consultar, exportar e imprimir, pero NO administrar.
    """
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Compras", "comprador")

    ha = auth_headers(client, "admin.a")
    hc = auth_headers(client, "comprador")
    liq, pago = montar_con_pago(client, ha)

    print("\n===== e1) SUBIR EXIGE 'administrar' =====")
    negado = client.post(ruta_adjuntos(liq, pago), files=[foto()], headers=hc)
    print(f"  el rol Compras sube un soporte: {negado.status_code} · "
          f"{negado.json()['error']['detail']}")
    assert negado.status_code == 403
    assert "administrar" in negado.json()["error"]["detail"]
    assert R2Falso.objetos == {}

    # Pero SÍ lo puede ver: consultar la quincena es parte de su trabajo.
    ve = client.get(ruta_adjuntos(liq, pago), headers=hc)
    print(f"  el rol Compras los lista:       {ve.status_code}")
    assert ve.status_code == 200

    ok = client.post(ruta_adjuntos(liq, pago), files=[foto()], headers=ha)
    print(f"  la administradora sube:         {ok.status_code}")
    assert ok.status_code == 201


def test_borrar_exige_eliminar_y_compartir_exige_exportar(
    client, base_datos, db_session, r2
):
    """Dos candados distintos sobre el mismo archivo, y la diferencia importa.

    BORRAR pide 'eliminar', igual que borrar el pago. Ya pasó en este proyecto al
    revés con los abonos de reventa: el borrado quedó pidiendo 'crear' y quien
    podía anotar un pago podía borrarlo.

    COMPARTIR pide 'exportar' porque saca el comprobante DEL SISTEMA hacia afuera:
    el enlace se reenvía y ya no hay forma de recogerlo. Con eso, un rol de solo
    consulta puede mirar el soporte en pantalla —que es lo que necesita para
    cuadrar— pero no repartirlo.
    """
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Consulta", "mirona")
    crear_usuario_con_rol(db_session, empresa, "Compras", "comprador")

    ha = auth_headers(client, "admin.a")
    hm = auth_headers(client, "mirona")
    hc = auth_headers(client, "comprador")
    liq, pago = montar_con_pago(client, ha)
    adjunto_id = client.post(
        ruta_adjuntos(liq, pago), files=[foto()], headers=ha
    ).json()["adjuntos"][0]["id"]

    print("\n===== e2) VER SÍ, COMPARTIR Y BORRAR NO =====")
    ver = client.get(ruta_adjuntos(liq, pago), headers=hm)
    print(f"  el rol Consulta lo ve:            {ver.status_code}")
    assert ver.status_code == 200 and ver.json()["adjuntos"][0]["url"]

    compartir = client.post(f"{API}/adjuntos/{adjunto_id}/compartir", headers=hm)
    print(f"  el rol Consulta lo comparte:      {compartir.status_code} · "
          f"{compartir.json()['error']['detail']}")
    assert compartir.status_code == 403
    assert "exportar" in compartir.json()["error"]["detail"]

    # El rol Compras SÍ tiene 'exportar', así que ese sí puede repartirlo.
    reparte = client.post(f"{API}/adjuntos/{adjunto_id}/compartir", headers=hc)
    print(f"  el rol Compras (con 'exportar') lo comparte: {reparte.status_code}")
    assert reparte.status_code == 200

    negado = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=hc)
    print(f"  el rol Compras lo borra:          {negado.status_code} · "
          f"{negado.json()['error']['detail']}")
    assert negado.status_code == 403
    assert "eliminar" in negado.json()["error"]["detail"]
    # No se tocó el archivo: no basta con negar la fila.
    assert R2Falso.borrados == [] and len(R2Falso.objetos) == 1

    ok = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=ha)
    print(f"  la administradora lo borra:       {ok.status_code}")
    assert ok.status_code == 204


# ===========================================================================
# f) Borrar: el soporte, el pago y la liquidación
# ===========================================================================
def test_borrar_el_soporte_quita_tambien_el_archivo(client, base_datos, r2):
    """Borrar solo la fila dejaría el archivo en el bucket para siempre: nadie
    podría verlo, nadie podría borrarlo y se seguiría pagando su almacenamiento.
    Con un comprobante de pago es peor todavía: el archivo con la cuenta y el
    monto seguiría existiendo después de que el dueño creyó haberlo borrado."""
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    subida = client.post(
        ruta_adjuntos(liq, pago), files=[foto(), foto("otra.jpg")], headers=h
    ).json()
    adjunto_id = subida["adjuntos"][0]["id"]
    clave = list(R2Falso.objetos)[0]

    print("\n===== BORRAR QUITA EL ARCHIVO, NO SOLO LA FILA =====")
    print(f"  antes:   {len(R2Falso.objetos)} objetos en el almacenamiento")
    r = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=h)
    assert r.status_code == 204
    print(f"  después: {len(R2Falso.objetos)} objetos · borrados: {len(R2Falso.borrados)}")
    assert clave in R2Falso.borrados and clave not in R2Falso.objetos

    quedan = client.get(ruta_adjuntos(liq, pago), headers=h).json()
    print(f"  la lista queda con {len(quedan['adjuntos'])} soporte")
    assert len(quedan["adjuntos"]) == 1


def test_si_el_borrado_del_soporte_no_alcanza_a_confirmar_no_queda_fila_sin_archivo(
    client, base_datos, r2, db_session, monkeypatch
):
    """EL MISMO ORDEN, EN EL BOTÓN DE BORRAR. Estaba al revés, y era el único
    sitio donde había quedado al revés.

    `_barrer` ya borraba la base primero y el bucket al confirmar; `eliminar_adjunto`
    —la misma clase, veinte renglones más abajo— conservaba el orden viejo: primero
    el objeto en R2 y después la fila, con el commit ocurriendo AFUERA, en `get_db`.
    Bastaba con que ese commit fallara —la conexión con Postgres, un tiempo agotado,
    un tropiezo insertando el renglón de auditoría— para que la sesión hiciera
    rollback: la fila REVIVE con su `deleted_at` en nulo y el archivo que nombra ya
    NO EXISTE. La pantalla lista un soporte que no va a abrir nunca.

    Acá se rompe el borrado justo después de que el servicio hizo lo suyo y se
    comprueba lo único que hace consistente al sistema: si la operación no cuajó,
    NO se tocó el bucket.
    """
    import app.modules.liquidaciones.service as servicio

    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    adjunto_id = client.post(
        ruta_adjuntos(liq, pago), files=[foto()], headers=h
    ).json()["adjuntos"][0]["id"]
    claves_antes = set(R2Falso.objetos)
    assert len(claves_antes) == 1

    # Revienta JUSTO DESPUÉS de que el servicio ya hizo todo lo suyo —marcó la
    # fila, escribió la auditoría y dejó encargado el borrado del bucket— y ANTES
    # del commit. Es el hueco exacto donde vivía el defecto: con el orden viejo,
    # aquí el archivo YA no existía.
    original = servicio.borrar_del_bucket_al_confirmar

    def _revienta_despues(*a, **k):
        original(*a, **k)
        raise RuntimeError("se cayó la conexión antes de confirmar")

    monkeypatch.setattr(servicio, "borrar_del_bucket_al_confirmar", _revienta_despues)
    with pytest.raises(RuntimeError):
        client.delete(f"{API}/adjuntos/{adjunto_id}", headers=h)
    monkeypatch.setattr(servicio, "borrar_del_bucket_al_confirmar", original)

    print("\n===== EL BORRADO DEL SOPORTE NO ALCANZÓ A CONFIRMAR =====")
    print(f"  objetos en el bucket: {len(R2Falso.objetos)} (estaban {len(claves_antes)})")
    print(f"  borrados del bucket:  {len(R2Falso.borrados)}")
    assert set(R2Falso.objetos) == claves_antes, (
        "se borró del bucket un archivo de una operación que no cuajó"
    )
    assert R2Falso.borrados == []

    db_session.rollback()
    vivas = db_session.scalars(
        select(AdjuntoPagoLiquidacion).where(AdjuntoPagoLiquidacion.deleted_at.is_(None))
    ).all()
    print(f"  filas vivas: {len(vivas)} · y sus llaves siguen en el bucket: "
          f"{all(v.object_key in R2Falso.objetos for v in vivas)}")
    assert len(vivas) == 1
    assert all(v.object_key in R2Falso.objetos for v in vivas), (
        "quedó una fila apuntando a un archivo que ya no existe"
    )


def test_si_el_almacenamiento_falla_al_borrar_el_soporte_igual_desaparece(
    client, base_datos, r2
):
    """LO QUE SE CAMBIA A CAMBIO, dicho en una prueba y no en un comentario.

    Con el orden nuevo el borrado en R2 pasa a ser de MEJOR ESFUERZO: si el bucket
    no responde, el dueño ve que el soporte se borró —y se borró de verdad, ya no
    está ni en la pantalla ni en la base— pero el archivo se queda ocupando espacio
    sin nada que lo nombre. Eso queda en el log y lo recoge el barrido del reinicio
    de empresa.

    Es el mal menor, y es el mismo que ya defendía `_barrer`: un archivo de sobra
    cuesta unos centavos; un soporte de pago que la pantalla muestra y que no abre
    cuesta una discusión con el productor sobre si le pagaron.
    """
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    adjunto_id = client.post(
        ruta_adjuntos(liq, pago), files=[foto()], headers=h
    ).json()["adjuntos"][0]["id"]

    R2Falso.revienta_al_borrar = True
    r = client.delete(f"{API}/adjuntos/{adjunto_id}", headers=h)
    R2Falso.revienta_al_borrar = False
    print("\n===== SI FALLA EL BORRADO EN R2 =====")
    print(f"  DELETE: {r.status_code} (el soporte se borra igual)")
    assert r.status_code == 204

    quedan = client.get(ruta_adjuntos(liq, pago), headers=h).json()
    print(f"  la lista queda en {len(quedan['adjuntos'])} · y el archivo quedó "
          f"huérfano en el bucket: {len(R2Falso.objetos)}")
    assert quedan["adjuntos"] == []
    assert len(R2Falso.objetos) == 1, (
        "el archivo tenía que quedar en el bucket: el fallo era del bucket"
    )


def test_borrar_el_pago_se_lleva_sus_soportes(client, base_datos, r2, db_session):
    """Un pago mal registrado se borra y le devuelve la deuda al sistema. Sus
    fotos se van con él: si se quedaran, serían comprobantes de una transferencia
    que el sistema dice que no ocurrió, invisibles —el pago ya no existe, no hay
    pantalla que los liste— y cobrando almacenamiento.

    Se comprueban las dos mitades: que el bucket quedó limpio Y que la plata de la
    liquidación volvió a su sitio (pagado + saldo sigue dando el neto).
    """
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h, litros="250")
    client.post(
        ruta_adjuntos(liq, pago), files=[foto("giro1.jpg"), foto("giro2.jpg")], headers=h
    )
    # Un segundo pago CON su soporte, para comprobar que borrar uno no se lleva
    # los del otro.
    liq2, otro_pago = pagar(client, h, liq, valor="50000", fecha="2026-06-17")
    client.post(ruta_adjuntos(liq, otro_pago), files=[foto("del_otro.jpg")], headers=h)

    print("\n===== BORRAR EL PAGO SE LLEVA SUS SOPORTES =====")
    print(f"  antes: {len(R2Falso.objetos)} objetos (2 del primer pago, 1 del segundo)")
    assert len(R2Falso.objetos) == 3
    claves_del_primero = [c for c in R2Falso.objetos if f"/pagos/{pago['id']}/" in c]

    r = client.delete(f"{API}/{liq['id']}/pagos/{pago['id']}", headers=h)
    assert r.status_code == 200, r.text
    despues = r.json()
    print(f"  después: {len(R2Falso.objetos)} objetos · borrados del bucket: "
          f"{len(R2Falso.borrados)}")
    assert len(R2Falso.objetos) == 1, "quedaron archivos del pago borrado"
    for clave in claves_del_primero:
        assert clave in R2Falso.borrados
    # El del OTRO pago no se tocó.
    assert f"/pagos/{otro_pago['id']}/" in list(R2Falso.objetos)[0]

    # Y la plata volvió a su sitio: la regla de oro sigue cuadrando.
    pagado, saldo, neto = (
        Decimal(despues["pagado"]), Decimal(despues["saldo"]),
        Decimal(despues["neto_a_pagar"]),
    )
    print(f"  la liquidación: pagado {pagado} + saldo {saldo} = neto {neto} · "
          f"estado {despues['estado']}")
    assert pagado + saldo == neto
    assert pagado == Decimal("50000")

    # Y las filas de los soportes borrados ya no se cuentan.
    vivos = db_session.scalars(
        select(AdjuntoPagoLiquidacion).where(AdjuntoPagoLiquidacion.deleted_at.is_(None))
    ).all()
    print(f"  filas de soportes vivas: {len(vivos)}")
    assert len(vivos) == 1


def test_borrar_el_pago_deja_renglon_propio_por_cada_soporte(
    client, base_datos, r2, db_session
):
    """CADA COMPROBANTE QUE DESAPARECE DEJA SU RENGLÓN, y no solo cuando se borra
    con el botón de su papelera.

    Borrar el pago se llevaba las fotos de tres transferencias y en la bitácora
    quedaba una sola línea de "editar liquidación": la plata quedaba explicada y
    las pruebas de esa plata se iban sin dejar rastro. Un soporte de pago es lo que
    demuestra que se giró; que desaparezca sin renglón propio es exactamente lo que
    una bitácora existe para impedir.

    El motivo va escrito adentro para que se distinga de un borrado a mano: no es
    lo mismo que alguien quite una foto equivocada a que se vayan con el pago.
    """
    from app.modules.auditoria.models import Auditoria

    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h, litros="250")
    client.post(
        ruta_adjuntos(liq, pago),
        files=[foto("giro1.jpg"), foto("giro2.jpg")],
        headers=h,
    )
    ids = {a["id"] for a in client.get(ruta_adjuntos(liq, pago), headers=h).json()["adjuntos"]}
    assert len(ids) == 2

    r = client.delete(f"{API}/{liq['id']}/pagos/{pago['id']}", headers=h)
    assert r.status_code == 200, r.text

    renglones = db_session.scalars(
        select(Auditoria).where(
            Auditoria.entidad == "AdjuntoPagoLiquidacion", Auditoria.accion == "eliminar"
        )
    ).all()
    print("\n===== BORRAR EL PAGO DEJA RENGLÓN POR CADA SOPORTE =====")
    for renglon in renglones:
        print(f"  {renglon.accion} {renglon.entidad} · "
              f"«{renglon.antes['nombre_archivo']}» · motivo: {renglon.despues['motivo']}")
    assert len(renglones) == 2, "los soportes se fueron sin dejar renglón propio"
    assert {str(r_.entidad_id) for r_ in renglones} == ids
    assert all(r_.despues["motivo"] == "borrar el pago" for r_ in renglones)
    # Con quién los borró y qué eran: la bitácora sirve si se puede leer después.
    assert all(r_.usuario_id is not None for r_ in renglones)
    assert {r_.antes["nombre_archivo"] for r_ in renglones} == {"giro1.jpg", "giro2.jpg"}


def test_si_el_borrado_del_pago_se_cae_a_mitad_no_quedan_filas_sin_archivo(
    client, base_datos, r2, db_session, monkeypatch
):
    """EL ORDEN QUE IMPORTA: la base se puede deshacer, el bucket NO.

    Los archivos se borraban de una, antes del `delete` del pago y del `flush`. Si
    algo reventaba después —una validación, el flush, o el commit que ocurre afuera
    en `get_db`— la sesión hacía rollback y las filas RESUCITABAN con su `deleted_at`
    en nulo... apuntando a archivos que ya no existían. Al dueño le quedaba una
    pantalla llena de soportes que no abren, sin ninguna manera de saber por qué.

    Acá se rompe el borrado a propósito justo después de que el barrido ya corrió, y
    se comprueba lo único que hace consistente al sistema: si la operación no cuajó,
    NO se tocó el bucket, y las filas y los archivos siguen contándose igual.
    """
    import app.modules.liquidaciones.service as servicio

    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h, litros="250")
    client.post(
        ruta_adjuntos(liq, pago), files=[foto("giro1.jpg"), foto("giro2.jpg")], headers=h
    )
    claves_antes = set(R2Falso.objetos)
    assert len(claves_antes) == 2

    # Revienta DESPUÉS del barrido: `_estado_pago` se llama más abajo en
    # `eliminar_pago`, ya con los soportes marcados y las claves apuntadas.
    original = servicio._estado_pago

    def _revienta(*a, **k):
        raise RuntimeError("se cayó la luz a mitad del borrado")

    monkeypatch.setattr(servicio, "_estado_pago", _revienta)
    with pytest.raises(RuntimeError):
        client.delete(f"{API}/{liq['id']}/pagos/{pago['id']}", headers=h)
    monkeypatch.setattr(servicio, "_estado_pago", original)

    print("\n===== EL BORRADO SE CAYÓ A MITAD =====")
    print(f"  objetos en el bucket: {len(R2Falso.objetos)} (estaban {len(claves_antes)})")
    print(f"  borrados del bucket:  {len(R2Falso.borrados)}")
    assert set(R2Falso.objetos) == claves_antes, "se borraron archivos de una operación que no cuajó"
    assert R2Falso.borrados == []

    # Y las filas siguen vivas, así que fila y archivo se siguen correspondiendo:
    # el dueño vuelve a intentar y ve exactamente lo que tenía.
    db_session.rollback()
    vivas = db_session.scalars(
        select(AdjuntoPagoLiquidacion).where(AdjuntoPagoLiquidacion.deleted_at.is_(None))
    ).all()
    print(f"  filas vivas: {len(vivas)} · y sus llaves siguen existiendo en el bucket: "
          f"{all(v.object_key in R2Falso.objetos for v in vivas)}")
    assert len(vivas) == 2
    assert all(v.object_key in R2Falso.objetos for v in vivas), (
        "quedaron filas apuntando a archivos que ya no existen"
    )


def test_borrar_la_liquidacion_se_lleva_los_soportes_de_todos_sus_pagos(
    client, base_datos, r2, db_session
):
    """La red de abajo, y va probada aunque hoy no se llegue por la puerta.

    Borrar una liquidación CON pagos rebota (`validar_eliminar`), así que en la
    práctica cuando el borrado llega al barrido ya no queda ninguno. Pero el
    barrido existe porque el día que ese guardia cambie —o aparezca otro camino
    que borre liquidaciones en bloque— lo que quedaría no sería un error visible
    sino comprobantes de pago en el bucket sin nada que los nombre. Acá se llama
    al servicio de frente para comprobar que ese barrido de verdad barre.
    """
    from app.core.context import RequestContext
    from app.modules.liquidaciones.service import AdjuntoPagoLiquidacionService

    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    _, otro = pagar(client, h, liq, valor="50000", fecha="2026-06-17")
    client.post(ruta_adjuntos(liq, pago), files=[foto("uno.jpg")], headers=h)
    client.post(ruta_adjuntos(liq, otro), files=[foto("dos.jpg")], headers=h)

    print("\n===== BORRAR LA LIQUIDACIÓN SE LLEVA TODO =====")
    print(f"  la liquidación tiene 2 pagos y {len(R2Falso.objetos)} soportes")
    assert len(R2Falso.objetos) == 2

    # 1. Por la puerta, con pagos, no se borra: y no se toca ni un archivo.
    import uuid as _uuid

    from app.modules.usuarios.models import Usuario
    from app.modules.liquidaciones.service import LiquidacionService

    admin = db_session.scalars(
        select(Usuario).where(Usuario.username == "admin.a")
    ).one()
    ctx = RequestContext(
        user_id=admin.id,
        empresa_id=base_datos["empresa_a"].id,
        is_superadmin=False,
        permisos={("liquidaciones", "eliminar")},
        ip="127.0.0.1",
    )
    import pytest as _pytest

    from app.core.exceptions import BusinessError

    with _pytest.raises(BusinessError) as error:
        LiquidacionService(db_session, ctx).eliminar(_uuid.UUID(liq["id"]))
    print(f"  borrarla con pagos: rebota · {error.value}")
    assert len(R2Falso.objetos) == 2, "un borrado que rebotó se llevó archivos"

    # 2. El barrido, llamado de frente: se lleva los soportes de LOS DOS pagos.
    servicio = AdjuntoPagoLiquidacionService(db_session, ctx)
    cuantos = servicio.limpiar_de_liquidacion(_uuid.UUID(liq["id"]))
    print(f"  el barrido de la liquidación se llevó {cuantos} soportes")
    assert cuantos == 2

    # PERO LOS ARCHIVOS TODAVÍA ESTÁN, y eso es a propósito: el borrado del bucket
    # es lo ÚNICO que no se puede deshacer, así que espera al `after_commit`. Si
    # esto reventara antes de confirmar, las filas resucitarían con el rollback y
    # los archivos que nombran ya no existirían — soportes que la pantalla lista y
    # que no abren nunca. Por la puerta normal el commit lo hace `get_db`; acá, que
    # se llamó al servicio de frente, hay que hacerlo a mano.
    print(f"  antes de confirmar: {len(R2Falso.objetos)} objetos siguen en el bucket")
    assert len(R2Falso.objetos) == 2, "se borró del bucket antes de confirmar la base"

    db_session.commit()
    print(f"  después de confirmar: quedan {len(R2Falso.objetos)} · "
          f"borrados {len(R2Falso.borrados)}")
    assert R2Falso.objetos == {}
    assert len(R2Falso.borrados) == 2


def test_reiniciar_la_quesera_se_lleva_los_soportes_de_sus_pagos(
    client, base_datos, r2, monkeypatch
):
    """Reiniciar una empresa borra sus movimientos. Si las filas de los soportes
    se van pero los archivos no, quedan en el bucket comprobantes de pago de una
    quesera que se supone que quedó en ceros — invisibles y cobrando.

    Se comprueba además que NO se lleva los de la OTRA quesera: es la clase de
    error que solo se nota cuando ya no hay nada que recuperar.
    """
    import app.core.storage as almacenamiento

    monkeypatch.setattr(almacenamiento, "R2Client", R2Falso)
    monkeypatch.setattr(almacenamiento, "r2_configurado", lambda: True)

    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    liq_a, pago_a = montar_con_pago(client, ha, nombre="Libardo")
    liq_b, pago_b = montar_con_pago(client, hb, nombre="Otro")
    client.post(ruta_adjuntos(liq_a, pago_a), files=[foto("de_a.jpg")], headers=ha)
    client.post(ruta_adjuntos(liq_b, pago_b), files=[foto("de_b.jpg")], headers=hb)

    empresa_a = base_datos["empresa_a"]
    clave_b = next(k for k in R2Falso.objetos if str(empresa_a.id) not in k)

    hs = auth_headers(client, "superadmin")
    print("\n===== REINICIAR LA QUESERA =====")
    print(f"  antes: {len(R2Falso.objetos)} objetos (uno por quesera)")
    r = client.post(
        f"/api/v1/empresas/{empresa_a.id}/reiniciar",
        json={"confirmacion": empresa_a.nombre},
        headers={**hs, "X-Empresa-Id": str(empresa_a.id)},
    )
    assert r.status_code == 200, r.text
    print(f"  borradas {r.json().get('adjuntos_pago_liquidacion')} filas de "
          f"adjuntos_pago_liquidacion")
    print(f"  después: {len(R2Falso.objetos)} objetos · quedó el de la Quesera B")
    assert r.json()["adjuntos_pago_liquidacion"] == 1
    assert list(R2Falso.objetos) == [clave_b], "se borró un archivo de la otra quesera"

    # Y a la Quesera B no le pasó nada: su soporte sigue listándose.
    quedan = client.get(ruta_adjuntos(liq_b, pago_b), headers=hb)
    assert quedan.status_code == 200 and len(quedan.json()["adjuntos"]) == 1


def test_si_el_reinicio_de_la_quesera_se_cae_a_mitad_no_borra_ningun_archivo(
    client, base_datos, r2, db_session, monkeypatch
):
    """EL MISMO ORDEN, EN LA PEOR ESCALA. Un reinicio toca decenas de tablas.

    El paso 0 del reinicio juntaba las llaves de los soportes de reventa y de los
    pagos de liquidación y las borraba del bucket DE UNA: antes del `delete()` de
    las filas y mucho antes del commit, que ocurre afuera en `get_db`. Un fallo a
    la mitad —y a la mitad de un reinicio no es exótico— hacía rollback y dejaba
    una empresa con TODOS sus comprobantes en la base y NINGUNO en el bucket, sin
    nada en la respuesta que lo dijera.

    Reproducido antes del arreglo: 2 soportes → reiniciar → rollback → 2 filas
    vivas y 0 objetos. Ahora el borrado espera al commit, así que si el reinicio
    no cuaja, no se pierde un solo archivo.
    """
    import app.core.storage as almacenamiento

    ha = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, ha, nombre="Libardo")
    client.post(
        ruta_adjuntos(liq, pago),
        files=[foto("giro1.jpg"), foto("giro2.jpg")],
        headers=ha,
    )
    claves_antes = set(R2Falso.objetos)
    assert len(claves_antes) == 2

    original = almacenamiento.borrar_del_bucket_al_confirmar

    def _revienta_despues(*a, **k):
        original(*a, **k)
        raise RuntimeError("se cayó la luz a mitad del reinicio")

    monkeypatch.setattr(
        almacenamiento, "borrar_del_bucket_al_confirmar", _revienta_despues
    )
    empresa_a = base_datos["empresa_a"]
    hs = auth_headers(client, "superadmin")
    with pytest.raises(RuntimeError):
        client.post(
            f"/api/v1/empresas/{empresa_a.id}/reiniciar",
            json={"confirmacion": empresa_a.nombre},
            headers={**hs, "X-Empresa-Id": str(empresa_a.id)},
        )
    monkeypatch.setattr(almacenamiento, "borrar_del_bucket_al_confirmar", original)

    print("\n===== EL REINICIO SE CAYÓ A MITAD =====")
    print(f"  objetos en el bucket: {len(R2Falso.objetos)} (estaban {len(claves_antes)})")
    print(f"  borrados del bucket:  {len(R2Falso.borrados)}")
    assert set(R2Falso.objetos) == claves_antes, (
        "el reinicio se llevó archivos de una operación que no cuajó"
    )
    assert R2Falso.borrados == []

    db_session.rollback()
    vivas = db_session.scalars(
        select(AdjuntoPagoLiquidacion).where(AdjuntoPagoLiquidacion.deleted_at.is_(None))
    ).all()
    print(f"  filas vivas: {len(vivas)} · y sus llaves siguen en el bucket: "
          f"{all(v.object_key in R2Falso.objetos for v in vivas)}")
    assert len(vivas) == 2
    assert all(v.object_key in R2Falso.objetos for v in vivas), (
        "quedaron filas apuntando a archivos que ya no existen"
    )


# ===========================================================================
# g) Topes y archivos que no sirven
# ===========================================================================
def test_un_archivo_que_no_es_imagen_ni_pdf_se_rechaza_con_mensaje_claro(
    client, base_datos, r2
):
    """El tipo se decide mirando los PRIMEROS BYTES, no la extensión ni el
    Content-Type que manda el navegador: los dos los pone quien sube. Y acá
    importa de verdad, porque de estos objetos se reparten enlaces firmados que
    abre OTRA persona en su navegador."""
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    r = client.post(
        ruta_adjuntos(liq, pago),
        files=[("files", ("notas.txt", TEXTO_PLANO, "text/plain"))],
        headers=h,
    )
    detalle = r.json()["error"]["detail"]
    print("\n===== g1) TIPO NO PERMITIDO =====")
    print(f"  notas.txt dice ser text/plain → {r.status_code} · {detalle}")
    assert r.status_code == 422
    assert "notas.txt" in detalle and "PDF" in detalle
    assert R2Falso.objetos == {}


def test_la_foto_de_iphone_se_sube_y_se_guarda_comprimida(client, base_datos, r2):
    """LA FOTO DE TODOS LOS DÍAS: el dueño fotografía la transferencia con el
    celular, y el iPhone graba en HEIC de fábrica.

    Antes rebotaba con un mensaje que lo mandaba a los Ajustes del teléfono a
    cambiarle el formato a la cámara. Con `pillow-heif` entra por el mismo camino
    que las demás y queda guardada como JPEG. Se comprueban las tres cosas que
    importan: que la subida pase, que el objeto del bucket tenga adentro un JPEG de
    verdad —lo que el navegador del productor sí sabe dibujar— y que el nombre lo
    diga, porque es el que viaja en el enlace que se manda por WhatsApp.
    """
    assert heic_se_puede_abrir(), "falta pillow-heif: no se está probando nada"
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    heic = foto_heic_de_iphone(4032, 3024)

    r = client.post(
        ruta_adjuntos(liq, pago),
        files=[("files", ("IMG_4417.HEIC", heic, "image/heic"))],
        headers=h,
    )
    assert r.status_code == 201, r.text
    fila = r.json()["adjuntos"][0]
    clave = list(R2Falso.objetos)[0]
    guardado, tipo_guardado = R2Falso.objetos[clave]
    adentro = Image.open(io.BytesIO(guardado))

    print("\n===== g2) LA FOTO DE iPHONE (HEIC) ENTRA =====")
    print(f"  sube  IMG_4417.HEIC · {len(heic) / 1024 / 1024:.1f} MB")
    print(f"  queda «{fila['nombre_archivo']}» · {fila['content_type']} · "
          f"{fila['tamano_bytes'] / 1024:.0f} KB")
    print(f"  y en el bucket hay un {adentro.format} de {adentro.size[0]} × "
          f"{adentro.size[1]}, con la llave terminando en …{clave[-12:]}")
    assert fila["content_type"] == "image/jpeg"
    assert fila["nombre_archivo"] == "IMG_4417.jpg", "el nombre siguió diciendo .HEIC"
    assert clave.endswith(".jpg")
    assert tipo_guardado == "image/jpeg" and adentro.format == "JPEG"
    assert fila["tamano_bytes"] < len(heic), "la HEIC no se comprimió"


def test_una_foto_enorme_rebota_sin_tumbar_la_api(client, base_datos, r2):
    """EL HALLAZGO CRÍTICO, por la puerta de la API.

    Un PNG de 12.000 × 12.000 de un gris plano pesa unos 157 KB: pasa por debajo
    del tope de 15 MB sin despeinarse, y al abrirlo se lleva 700 MB de memoria. Las
    dos queseras comparten servidor, así que una sola de estas las dejaba a LAS DOS
    sin API — y no hace falta mala intención, basta una captura rara.

    Lo que tiene que pasar: un 422 con un mensaje que el dueño entienda, nada en el
    bucket, y la API respondiéndole a las dos queseras después como si nada.
    """
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    bomba = bomba_de_pixeles(12000, 12000)

    r = client.post(
        ruta_adjuntos(liq, pago),
        files=[("files", ("captura.png", bomba, "image/png"))],
        headers=h,
    )
    detalle = r.json()["error"]["detail"]
    print("\n===== g3) LA BOMBA DE PÍXELES =====")
    print(f"  sube captura.png · {len(bomba) / 1024:.0f} KB en disco · "
          f"144 megapíxeles al abrirla")
    print(f"  {r.status_code} · {detalle}")
    assert r.status_code == 422
    assert "captura.png" in detalle and "demasiado grande" in detalle
    assert "12.000" in detalle and "80" in detalle
    assert R2Falso.objetos == {}, "se guardó algo de una imagen que ni se abrió"

    # Y la API sigue viva, para esta quesera y para la otra.
    assert client.get(ruta_adjuntos(liq, pago), headers=h).status_code == 200
    assert (
        client.get(
            "/api/v1/liquidaciones", headers=auth_headers(client, "admin.b")
        ).status_code
        == 200
    )
    print("  y las dos queseras siguen atendiendo después")


def test_un_archivo_demasiado_grande_se_rechaza_diciendo_cuanto_pesa(
    client, base_datos, r2
):
    """Una foto de celular pesa varios MB, así que el tope es alto (15 MB). Lo que
    no puede pasar es que un archivo enorme salga con un 500 "Error interno": el
    dueño está en el campo con mala señal y necesita saber que fue el tamaño.

    Ojo al orden: el tope se mide ANTES de comprimir. Comprimir exige cargar el
    archivo entero en memoria, que es justo lo que el tope existe para evitar.
    """
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    grande = JPEG + b"\x00" * (settings.ADJUNTOS_MAX_MB * 1024 * 1024)

    r = client.post(
        ruta_adjuntos(liq, pago),
        files=[("files", ("foto_enorme.jpg", grande, "image/jpeg"))],
        headers=h,
    )
    detalle = r.json()["error"]["detail"]
    print("\n===== g3) TAMAÑO NO PERMITIDO =====")
    print(f"  se mandan {len(grande) / 1024 / 1024:.1f} MB (tope "
          f"{settings.ADJUNTOS_MAX_MB} MB) → {r.status_code}")
    print(f"  {detalle}")
    assert r.status_code == 422
    assert "foto_enorme.jpg" in detalle
    assert str(settings.ADJUNTOS_MAX_MB) in detalle
    assert R2Falso.objetos == {}


def test_no_caben_mas_soportes_de_los_permitidos_por_pago(
    client, base_datos, r2, monkeypatch
):
    """Esto es el respaldo de que se pagó, no un álbum. El tope evita que un error
    de la interfaz (o alguien con malas intenciones) llene el bucket a costa del
    dueño, que es quien paga el almacenamiento."""
    monkeypatch.setattr(settings, "ADJUNTOS_MAX_POR_DOCUMENTO", 3)
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)

    primera = client.post(
        ruta_adjuntos(liq, pago), files=[foto("a.jpg"), foto("b.jpg")], headers=h
    )
    print("\n===== TOPE POR PAGO (3 en esta prueba) =====")
    print(f"  suben 2: {primera.status_code} · cupo restante: "
          f"{primera.json()['cupo_restante']}")
    assert primera.status_code == 201
    assert primera.json()["cupo_restante"] == 1

    pasada = client.post(
        ruta_adjuntos(liq, pago), files=[foto("c.jpg"), foto("d.jpg")], headers=h
    )
    print(f"  suben 2 más: {pasada.status_code} · {pasada.json()['error']['detail']}")
    assert pasada.status_code == 422
    assert "máximo 3" in pasada.json()["error"]["detail"]
    # Ninguna de las dos entró: el cupo se comprueba ANTES de subir nada.
    assert len(R2Falso.objetos) == 2


def test_si_una_foto_del_lote_no_sirve_no_se_sube_ninguna(client, base_datos, r2):
    """Se validan y se comprimen TODAS antes de subir NINGUNA. Si la tercera no
    sirve y las dos primeras ya quedaron guardadas, el dueño corrige y vuelve a
    mandar las tres: las dos buenas quedarían duplicadas y tendría que borrarlas a
    mano."""
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    r = client.post(
        ruta_adjuntos(liq, pago),
        files=[
            foto("buena1.jpg"),
            foto("buena2.jpg"),
            ("files", ("mala.txt", TEXTO_PLANO, "text/plain")),
        ],
        headers=h,
    )
    print("\n===== TODO O NADA =====")
    print(f"  3 archivos, uno malo → {r.status_code}")
    print(f"  objetos en el almacenamiento: {len(R2Falso.objetos)}")
    assert r.status_code == 422
    assert R2Falso.objetos == {}
    assert client.get(ruta_adjuntos(liq, pago), headers=h).json()["adjuntos"] == []


def test_si_el_almacenamiento_se_cae_a_media_subida_no_quedan_archivos_sueltos(
    client, base_datos, r2
):
    """La excepción hace rollback de la sesión y las filas desaparecen. Sin un
    barrido, los archivos que alcanzaron a subir quedarían en el bucket sin
    ninguna fila que los nombre: invisibles, imborrables y cobrando."""
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    R2Falso.revienta_al_subir_en = 1  # la segunda falla

    r = client.post(
        ruta_adjuntos(liq, pago), files=[foto("una.jpg"), foto("dos.jpg")], headers=h
    )
    print("\n===== R2 SE CAE A MEDIA SUBIDA =====")
    print(f"  POST: {r.status_code} · {r.json()['error']['detail']}")
    print(f"  objetos que quedaron: {len(R2Falso.objetos)} · "
          f"barridos: {len(R2Falso.borrados)}")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "r2_error"
    assert R2Falso.objetos == {}, "quedó un archivo huérfano en el bucket"
    assert len(R2Falso.borrados) == 1

    R2Falso.revienta_al_subir_en = -1
    assert client.get(ruta_adjuntos(liq, pago), headers=h).json()["adjuntos"] == []


def test_un_archivo_vacio_no_pasa(client, base_datos, r2):
    """Pasa de verdad: se toca "adjuntar" antes de que el celular termine de
    guardar la foto y llega un archivo de cero bytes. Sin este control quedaría
    una fila que promete un soporte y un enlace que abre una imagen rota."""
    h = auth_headers(client, "admin.a")
    liq, pago = montar_con_pago(client, h)
    r = client.post(
        ruta_adjuntos(liq, pago),
        files=[("files", ("vacia.jpg", b"", "image/jpeg"))],
        headers=h,
    )
    print("\n===== ARCHIVO VACÍO =====")
    print(f"  {r.status_code} · {r.json()['error']['detail']}")
    assert r.status_code == 422
    assert "vacío" in r.json()["error"]["detail"]


# ===========================================================================
# Sin almacenamiento configurado, el módulo entero sigue funcionando
# ===========================================================================
def test_sin_almacenamiento_configurado_las_liquidaciones_siguen_funcionando(
    client, base_datos
):
    """El caso de un portátil sin llaves. La aplicación tiene que arrancar igual
    —si no, no habría llegado ni acá— y TODO el módulo de liquidaciones tiene que
    seguir usable: generar, aprobar, pagar, imprimir. Lo único que avisa que no
    está disponible es la parte de soportes, y avisa con un mensaje que se
    entiende, no con un 500.

    Ojo: esta prueba NO usa el doble de R2 a propósito. Corre con la configuración
    de verdad, que en pruebas viene sin llaves.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== SIN R2 CONFIGURADO =====")

    liq, pago = montar_con_pago(client, h)
    pdf = client.get(f"{API}/{liq['id']}/pdf", headers=h)
    print(f"  generar, aprobar y pagar: OK · el PDF del comprobante: {pdf.status_code}")
    assert pdf.status_code == 200

    listado = client.get(ruta_adjuntos(liq, pago), headers=h)
    cuerpo = listado.json()
    print(f"  listar soportes: {listado.status_code} · disponible: {cuerpo['disponible']}")
    print(f"  mensaje: {cuerpo['mensaje']}")
    assert listado.status_code == 200
    assert cuerpo["disponible"] is False
    assert cuerpo["adjuntos"] == []
    assert "no está configurado" in cuerpo["mensaje"]

    subir = client.post(ruta_adjuntos(liq, pago), files=[foto()], headers=h)
    print(f"  subir: {subir.status_code} · {subir.json()['error']['code']}")
    assert subir.status_code == 422
    assert subir.json()["error"]["code"] == "r2_no_configurado"

    # Y el pago sigue diciendo, sin reventar, que no tiene soportes.
    detalle = client.get(f"{API}/{liq['id']}", headers=h).json()
    assert detalle["pagos"][0]["adjuntos_count"] == 0
