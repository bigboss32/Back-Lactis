"""GASTOS: el total según los filtros, y la factura guardada donde nadie la vea.

LO QUE PIDIÓ EL DUEÑO, textual: "quiero hacer lo mismo que se hizo en anticipos,
que se ve el valor total dependiendo los filtros; también con los gastos, y los
gastos que se les pueda subir la factura".

Son dos cosas y las dos se miden acá:

  (A) EL TOTAL. Que sume TODO lo filtrado y no la página que se está viendo, y
      —esto es lo que de verdad importa— que sume EXACTAMENTE las filas que la
      tabla muestra. El dueño revisa con calculadora: si la tabla trae seis
      renglones y el total dice otra cosa, el número no sirve para nada.

  (B) LA FACTURA. Ya se podía subir, pero iba a la carpeta `uploads/` del
      servidor, publicada SIN CLAVE en `/uploads`: la factura de una quesera se
      bajaba desde cualquier navegador sin entrar al sistema. Ahora va al bucket
      privado, con enlaces firmados que caducan solos — el mismo mecanismo de los
      soportes de pago de las liquidaciones. Se mide que la puerta vieja quedó
      cerrada y que la nueva aísla a las dos queseras en los CUATRO verbos.
"""
import io
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.modules.gastos.models import AdjuntoGasto
from tests.ayudas_imagenes import JPEG, PDF, PNG, TEXTO_PLANO, foto_de_celular
from tests.ayudas_r2 import R2Falso, enchufar
from tests.conftest import PASSWORD, auth_headers

V = "/api/v1"
G = f"{V}/gastos"


def archivo(nombre="factura.jpg", contenido=JPEG, tipo="image/jpeg"):
    """Un archivo listo para el multipart de httpx."""
    return ("files", (nombre, contenido, tipo))


@pytest.fixture()
def r2(monkeypatch):
    """Enchufa el doble de R2 (ver `tests/ayudas_r2.py::enchufar`)."""
    import app.modules.gastos.service as servicio

    return enchufar(monkeypatch, servicio)


# ------------------------------------------------------------------ montaje
# Cifras con centavos y a propósito: si en algún lado se redondea de más, la
# diferencia sale en la resta y no se puede confundir con "un problema de
# presentación". El total de todo esto es 386.077,44.
SIEMBRA = (
    # (categoría, concepto, proveedor, nº factura, fecha, valor)
    ("Combustible", "ACPM del camión", "Terpel", "F-001", "2026-07-03", "242760.75"),
    ("Combustible", "Gasolina moto", "Terpel", "F-002", "2026-07-19", "37450.45"),
    ("Servicios", "Energía de julio", "EPM", "F-003", "2026-07-28", "94030.07"),
    ("Servicios", "Agua de julio", "EPM", "F-004", "2026-08-02", "11836.16"),
    ("Insumos", "Cuajo", "Quimicos SA", "F-005", "2026-06-30", "0.01"),
)
TOTAL_SEMBRADO = Decimal("386077.44")


def categoria(client, h, nombre: str) -> str:
    """El id de la categoría, sembrándola si el sistema no la trae ya.

    Varias de las de aquí —Combustible, Servicios, Insumos— vienen sembradas por
    defecto con cada empresa, y crearlas otra vez rebota con un 409.
    """
    existentes = client.get(f"{V}/categorias-gasto?page_size=100", headers=h).json()["items"]
    for c in existentes:
        if c["nombre"] == nombre:
            return c["id"]
    r = client.post(f"{V}/categorias-gasto", json={"nombre": nombre}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def sembrar(client, h) -> dict[str, str]:
    """Deja los cinco gastos puestos y devuelve el id de cada categoría."""
    categorias: dict[str, str] = {}
    for cat, concepto, proveedor, factura, fecha, valor in SIEMBRA:
        if cat not in categorias:
            categorias[cat] = categoria(client, h, cat)
        r = client.post(
            G,
            json={
                "fecha": fecha,
                "categoria_id": categorias[cat],
                "concepto": concepto,
                "proveedor": proveedor,
                "numero_factura": factura,
                "valor": valor,
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
    return categorias


def listar_todo(client, h, query="") -> tuple[list[Decimal], int]:
    """TODAS las filas que devuelve el listado con esos filtros, paginando.

    Pagina de verdad —de a dos— porque el defecto que se está buscando es
    justamente el de un total que solo mira la primera página.
    """
    valores: list[Decimal] = []
    pagina = 1
    while True:
        sep = "&" if query else ""
        r = client.get(f"{G}/filtrar/avanzado?page={pagina}&page_size=2{sep}{query}", headers=h)
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        valores += [Decimal(str(x["valor"])) for x in cuerpo["items"]]
        if len(valores) >= cuerpo["total"] or not cuerpo["items"]:
            return valores, cuerpo["total"]
        pagina += 1


def suma(client, h, query="") -> Decimal:
    r = client.get(f"{G}/totales/suma?{query}", headers=h)
    assert r.status_code == 200, r.text
    return Decimal(str(r.json())).quantize(Decimal("0.01"))


# ===========================================================================
# A) EL TOTAL SEGÚN LOS FILTROS
# ===========================================================================
def test_el_total_suma_todo_lo_filtrado_y_no_solo_la_pagina(client, base_datos):
    """Cinco gastos, páginas de a dos: el total tiene que ser el de los cinco.

    Es el punto entero de la cifra. Si sumara la página, el dueño que filtra
    "julio" vería el total de los dos primeros renglones y creería que ese es el
    gasto del mes.
    """
    h = auth_headers(client, "admin.a")
    sembrar(client, h)

    valores, total_filas = listar_todo(client, h)
    assert total_filas == 5 and len(valores) == 5
    assert sum(valores) == TOTAL_SEMBRADO
    assert suma(client, h) == TOTAL_SEMBRADO


COMBOS = (
    "",
    "search=Terpel",
    "search=EPM",
    "search=F-003",
    "search=Gasolina",
    "search=nada que exista",
    "desde=2026-07-01",
    "hasta=2026-07-31",
    "desde=2026-07-01&hasta=2026-07-31",
    "desde=2026-07-01&hasta=2026-07-31&search=Terpel",
    "desde=2026-09-01",
)


def test_el_total_es_exactamente_la_suma_de_los_renglones_que_muestra(client, base_datos):
    """LA REGLA DE LA CASA: todo desglose suma exacto la cifra grande.

    Se prueban once combinaciones de filtros —incluida una que no trae nada— y en
    todas se compara el total contra la suma de los renglones listados, sumados
    aquí uno por uno. No se compara contra una cifra escrita a mano a propósito:
    lo que se está midiendo es que los dos caminos del backend usen los MISMOS
    filtros, que es justo lo que se rompe cuando alguien agrega un filtro nuevo y
    se acuerda de un solo lado.
    """
    h = auth_headers(client, "admin.a")
    categorias = sembrar(client, h)
    combos = list(COMBOS) + [
        f"categoria_id={categorias['Combustible']}",
        f"categoria_id={categorias['Servicios']}&desde=2026-07-01&hasta=2026-07-31",
        f"categoria_id={categorias['Insumos']}&search=Cuajo",
    ]

    descuadres = []
    for q in combos:
        valores, total_filas = listar_todo(client, h, q)
        s = suma(client, h, q)
        if sum(valores) != s or len(valores) != total_filas:
            descuadres.append((q, str(sum(valores)), str(s), len(valores), total_filas))
    assert not descuadres, "TOTAL != RENGLONES LISTADOS:\n" + "\n".join(map(str, descuadres))


def test_el_total_respeta_el_rango_de_fechas(client, base_datos):
    """Julio son los tres de julio; agosto, uno; y septiembre, nada."""
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    assert suma(client, h, "desde=2026-07-01&hasta=2026-07-31") == Decimal("374241.27")
    assert suma(client, h, "desde=2026-08-01&hasta=2026-08-31") == Decimal("11836.16")
    assert suma(client, h, "desde=2026-09-01") == Decimal("0.00")


def test_el_total_no_cruza_de_quesera(client, base_datos):
    """La Quesera B no ve ni un peso de los gastos de la A."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    sembrar(client, ha)
    assert suma(client, ha) == TOTAL_SEMBRADO
    assert suma(client, hb) == Decimal("0.00")
    assert suma(client, hb, "search=Terpel") == Decimal("0.00")


def test_borrar_un_gasto_lo_saca_del_total(client, base_datos):
    """Y la resta es EXACTA: el gasto que se fue, ni un centavo más."""
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    antes = suma(client, h)
    uno = client.get(f"{G}/filtrar/avanzado?search=Gasolina", headers=h).json()["items"][0]
    assert client.delete(f"{G}/{uno['id']}", headers=h).status_code == 204

    despues = suma(client, h)
    assert antes - despues == Decimal("37450.45"), (str(antes), str(despues))
    valores, total_filas = listar_todo(client, h)
    assert sum(valores) == despues and total_filas == 4


def test_el_total_pide_permiso_de_consultar(client, base_datos, db_session):
    """La cifra es tan sensible como la tabla que resume: mismo permiso."""
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Consulta", "mirona")
    sembrar(client, auth_headers(client, "admin.a"))

    h = auth_headers(client, "mirona")
    assert client.get(f"{G}/totales/suma", headers=h).status_code == 200
    # Sin token no se responde ninguna cifra.
    assert client.get(f"{G}/totales/suma").status_code in (401, 403)


# ===========================================================================
# B) LA FACTURA EN EL BUCKET PRIVADO
# ===========================================================================
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


def un_gasto(client, h, *, concepto="ACPM del camión", valor="242760.75") -> dict:
    r = client.post(
        G,
        json={
            "fecha": "2026-07-03",
            "categoria_id": categoria(client, h, "Combustible"),
            "concepto": concepto,
            "proveedor": "Terpel",
            "valor": valor,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_subir_dos_paginas_de_una_factura_y_volverlas_a_listar(
    client, base_datos, r2, db_session
):
    """El caso de todos los días: la factura del ACPM tiene dos hojas.

    Antes solo cabía UNA: la segunda pisaba a la primera y la primera se perdía
    sin avisar.
    """
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)

    subida = client.post(
        f"{G}/{gasto['id']}/adjuntos",
        files=[archivo("hoja1.jpg"), archivo("hoja2.pdf", PDF, "application/pdf")],
        headers=h,
    )
    assert subida.status_code == 201, subida.text
    cuerpo = subida.json()
    assert cuerpo["disponible"] is True
    assert [a["nombre_archivo"] for a in cuerpo["adjuntos"]] == ["hoja1.jpg", "hoja2.pdf"]
    assert cuerpo["cupo_restante"] == settings.ADJUNTOS_MAX_POR_DOCUMENTO - 2

    # Vuelven a salir en el mismo orden en que se subieron: con una factura de
    # varias hojas, verlas al revés no es un detalle.
    otra_vez = client.get(f"{G}/{gasto['id']}/adjuntos", headers=h).json()
    assert [a["nombre_archivo"] for a in otra_vez["adjuntos"]] == ["hoja1.jpg", "hoja2.pdf"]

    # La llave lleva el empresa_id adentro y el nombre del archivo NO.
    empresa_a = str(base_datos["empresa_a"].id)
    for clave in R2Falso.objetos:
        assert clave.startswith(f"{empresa_a}/gastos/{gasto['id']}/")
        assert "hoja1" not in clave and "hoja2" not in clave

    # Y EN LA BASE NO QUEDA NINGUNA URL: solo la llave del objeto.
    filas = db_session.scalars(select(AdjuntoGasto)).all()
    assert len(filas) == 2
    for fila in filas:
        assert "http" not in fila.object_key
        assert not hasattr(fila, "url")


def test_el_enlace_es_firmado_y_de_corta_duracion(client, base_datos, r2):
    """Lo que se manda a la pantalla dura minutos, no para siempre."""
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)
    client.post(f"{G}/{gasto['id']}/adjuntos", files=[archivo()], headers=h)

    fila = client.get(f"{G}/{gasto['id']}/adjuntos", headers=h).json()["adjuntos"][0]
    assert "X-Amz-Signature" in fila["url"]
    assert fila["url_expira"] is not None
    # Ninguna firma de la pantalla dura más que el tope configurado para ver.
    tope = max(60, settings.R2_URL_VER_MINUTOS * 60)
    assert all(segundos <= tope for _, segundos in R2Falso.firmas)


def test_el_clip_de_la_grilla_trae_el_numero_de_facturas(client, base_datos, r2):
    """El dueño revisa la lista buscando a cuáles les falta la factura.

    Sin este número tendría que abrir gasto por gasto para saberlo.
    """
    h = auth_headers(client, "admin.a")
    con_factura = un_gasto(client, h, concepto="ACPM del camión")
    sin_factura = un_gasto(client, h, concepto="Peajes")
    client.post(
        f"{G}/{con_factura['id']}/adjuntos",
        files=[archivo("hoja1.jpg"), archivo("hoja2.jpg")],
        headers=h,
    )

    filas = {x["concepto"]: x for x in client.get(f"{G}/filtrar/avanzado", headers=h).json()["items"]}
    assert filas["ACPM del camión"]["adjuntos_count"] == 2
    assert filas["Peajes"]["adjuntos_count"] == 0
    assert sin_factura["id"] == filas["Peajes"]["id"]


def test_la_quesera_b_no_toca_las_facturas_de_la_a(client, base_datos, r2):
    """LOS CUATRO VERBOS, uno por uno, y sin que se le firme medio enlace.

    Es la razón de fondo de todo este cambio: una factura trae el NIT del
    proveedor, el valor y a veces la cuenta a la que se pagó.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    gasto = un_gasto(client, ha)
    subida = client.post(f"{G}/{gasto['id']}/adjuntos", files=[archivo()], headers=ha)
    adjunto = subida.json()["adjuntos"][0]

    firmas_antes = len(R2Falso.firmas)
    intentos = {
        "listar": client.get(f"{G}/{gasto['id']}/adjuntos", headers=hb),
        "subir": client.post(f"{G}/{gasto['id']}/adjuntos", files=[archivo()], headers=hb),
        "compartir": client.post(f"{G}/adjuntos/{adjunto['id']}/compartir", headers=hb),
        "borrar": client.delete(f"{G}/adjuntos/{adjunto['id']}", headers=hb),
    }
    for verbo, r in intentos.items():
        assert r.status_code == 404, f"{verbo} respondió {r.status_code}: {r.text[:200]}"
    # 404 y no 403: un 403 le confirmaría a la otra quesera que el gasto existe.
    assert len(R2Falso.firmas) == firmas_antes, "se firmó un enlace por el camino"
    # Y la factura de A sigue entera.
    assert len(client.get(f"{G}/{gasto['id']}/adjuntos", headers=ha).json()["adjuntos"]) == 1


def test_compartir_pide_exportar_y_queda_en_la_auditoria(client, base_datos, r2, db_session):
    """El enlace largo saca la factura del sistema: no lo reparte cualquiera.

    Un rol de solo consulta puede MIRARLA en pantalla pero no repartirla — el
    enlace se reenvía y ya no hay forma de recogerlo.
    """
    from app.modules.auditoria.models import Auditoria

    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Consulta", "mirona")
    ha = auth_headers(client, "admin.a")
    gasto = un_gasto(client, ha)
    adjunto = client.post(
        f"{G}/{gasto['id']}/adjuntos", files=[archivo()], headers=ha
    ).json()["adjuntos"][0]

    h_consulta = auth_headers(client, "mirona")
    assert client.get(f"{G}/{gasto['id']}/adjuntos", headers=h_consulta).status_code == 200
    assert client.post(f"{G}/adjuntos/{adjunto['id']}/compartir", headers=h_consulta).status_code == 403

    enlace = client.post(f"{G}/adjuntos/{adjunto['id']}/compartir", headers=ha)
    assert enlace.status_code == 200, enlace.text
    datos = enlace.json()
    assert datos["dias"] >= 1 and datos["expira_texto"]
    assert "X-Amz-Signature" in datos["url"]

    # Queda el HECHO de compartir, nunca la URL: la URL lleva la firma adentro.
    renglones = db_session.scalars(
        select(Auditoria).where(Auditoria.accion == "compartir")
    ).all()
    assert len(renglones) == 1
    assert "X-Amz-Signature" not in str(renglones[0].despues)
    assert str(gasto["id"]) in str(renglones[0].despues)


def test_borrar_la_factura_se_lleva_el_archivo_del_bucket(client, base_datos, r2):
    """Si la fila se va y el archivo se queda, la quesera lo paga para siempre."""
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)
    adjunto = client.post(
        f"{G}/{gasto['id']}/adjuntos", files=[archivo()], headers=h
    ).json()["adjuntos"][0]
    clave = list(R2Falso.objetos)[0]

    assert client.delete(f"{G}/adjuntos/{adjunto['id']}", headers=h).status_code == 204
    assert R2Falso.borrados == [clave]
    assert R2Falso.objetos == {}
    assert client.get(f"{G}/{gasto['id']}/adjuntos", headers=h).json()["adjuntos"] == []


def test_borrar_el_gasto_se_lleva_sus_facturas(client, base_datos, r2):
    """Borrar el gasto sin barrer sus facturas las dejaba en el bucket para
    siempre: el gasto ya no existe, así que nadie las puede ver ni borrar desde la
    aplicación, y la quesera sigue pagando ese almacenamiento sin saberlo."""
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)
    client.post(
        f"{G}/{gasto['id']}/adjuntos",
        files=[archivo("hoja1.jpg"), archivo("hoja2.jpg")],
        headers=h,
    )
    claves = sorted(R2Falso.objetos)

    assert client.delete(f"{G}/{gasto['id']}", headers=h).status_code == 204
    assert sorted(R2Falso.borrados) == claves
    assert R2Falso.objetos == {}


def test_reiniciar_la_empresa_se_lleva_las_facturas_del_bucket(
    client, base_datos, r2, monkeypatch
):
    """Reiniciar una empresa borra sus movimientos. Si las filas de las facturas se
    van pero los archivos no, quedan en el bucket facturas de una empresa que se
    supone que quedó en ceros — invisibles, imborrables y cobrando.

    Se comprueba además que NO se lleva las de la OTRA quesera: es la clase de
    error que solo se nota cuando ya no hay nada que recuperar.
    """
    # El reinicio no instancia el cliente: encarga el borrado a
    # `borrar_del_bucket_al_confirmar`, que vive en `app.core.storage` y solo corre
    # si la transacción cuaja. Así que el doble se enchufa AHÍ.
    import app.core.storage as almacenamiento

    monkeypatch.setattr(almacenamiento, "R2Client", R2Falso)
    monkeypatch.setattr(almacenamiento, "r2_configurado", lambda: True)

    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    gasto_a = un_gasto(client, ha)
    gasto_b = un_gasto(client, hb, concepto="ACPM de la B")
    client.post(f"{G}/{gasto_a['id']}/adjuntos", files=[archivo("a1.jpg")], headers=ha)
    client.post(f"{G}/{gasto_b['id']}/adjuntos", files=[archivo("b1.jpg")], headers=hb)

    empresa_a = base_datos["empresa_a"]
    clave_b = next(k for k in R2Falso.objetos if str(empresa_a.id) not in k)

    hs = auth_headers(client, "superadmin")
    r = client.post(
        f"{V}/empresas/{empresa_a.id}/reiniciar",
        json={"confirmacion": empresa_a.nombre},
        headers={**hs, "X-Empresa-Id": str(empresa_a.id)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["adjuntos_gasto"] == 1
    assert list(R2Falso.objetos) == [clave_b], "se borró un archivo de la otra quesera"

    # Y a la quesera B no le pasó nada: su factura sigue listándose.
    quedan = client.get(f"{G}/{gasto_b['id']}/adjuntos", headers=hb)
    assert quedan.status_code == 200 and len(quedan.json()["adjuntos"]) == 1


def test_el_tope_por_gasto_se_respeta_y_se_explica(client, base_datos, r2):
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)
    tope = settings.ADJUNTOS_MAX_POR_DOCUMENTO

    llenar = client.post(
        f"{G}/{gasto['id']}/adjuntos",
        files=[archivo(f"h{i}.jpg") for i in range(tope)],
        headers=h,
    )
    assert llenar.status_code == 201, llenar.text
    assert llenar.json()["cupo_restante"] == 0

    una_mas = client.post(f"{G}/{gasto['id']}/adjuntos", files=[archivo()], headers=h)
    assert una_mas.status_code == 422
    assert str(tope) in una_mas.text and "gasto" in una_mas.text.lower()


def test_un_archivo_que_no_es_factura_rebota_con_el_mensaje_en_cristiano(
    client, base_datos, r2
):
    """Y rebota ANTES de subir nada: el bucket queda limpio."""
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)
    r = client.post(
        f"{G}/{gasto['id']}/adjuntos",
        files=[("files", ("cuentas.txt", TEXTO_PLANO, "text/plain"))],
        headers=h,
    )
    assert r.status_code == 422
    assert "PDF" in r.text or "JPG" in r.text
    assert R2Falso.objetos == {}


def test_la_foto_del_celular_se_guarda_comprimida_y_el_pdf_pasa_derecho(
    client, base_datos, r2
):
    """Lo que pidió el dueño: "de paso le reducimos la calidad para ahorrar
    espacio". Una factura fotografiada con el celular pesa varios megas y se lee
    igual de bien encogida; un PDF ya viene liviano y tocarlo solo lo dañaría."""
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)
    original = foto_de_celular()

    r = client.post(
        f"{G}/{gasto['id']}/adjuntos",
        files=[
            archivo("factura.jpg", original, "image/jpeg"),
            archivo("factura.pdf", PDF, "application/pdf"),
        ],
        headers=h,
    )
    assert r.status_code == 201, r.text
    filas = {a["nombre_archivo"]: a for a in r.json()["adjuntos"]}
    assert filas["factura.jpg"]["tamano_bytes"] < len(original), "la foto no se comprimió"
    assert filas["factura.pdf"]["tamano_bytes"] == len(PDF), "el PDF no debía tocarse"


def test_sin_almacenamiento_configurado_la_pantalla_sigue_usable(client, base_datos):
    """Sin R2 no se puede subir —y se dice por qué—, pero listar responde 200.

    No es culpa de quien mira, y el resto de la pantalla de gastos tiene que poder
    seguir usándose.
    """
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)

    lista = client.get(f"{G}/{gasto['id']}/adjuntos", headers=h)
    assert lista.status_code == 200
    assert lista.json()["disponible"] is False
    assert lista.json()["mensaje"]

    subir = client.post(f"{G}/{gasto['id']}/adjuntos", files=[archivo()], headers=h)
    assert subir.status_code == 422


def test_la_puerta_vieja_e_insegura_quedo_cerrada(client, base_datos):
    """`POST /gastos/{id}/adjunto` guardaba la factura en `uploads/`, publicada
    SIN CLAVE en `/uploads`. Ya no existe: si volviera a aparecer, la factura
    volvería a bajarse desde cualquier navegador sin entrar al sistema."""
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)
    r = client.post(
        f"{G}/{gasto['id']}/adjunto",
        files={"file": ("factura.png", io.BytesIO(PNG), "image/png")},
        headers=h,
    )
    assert r.status_code in (404, 405), f"la subida insegura sigue viva: {r.status_code}"


def test_la_factura_vieja_se_sigue_viendo(client, base_datos):
    """Las que el dueño ya había subido no desaparecen de la pantalla.

    La columna `adjunto_url` no se escribe más, pero sale igual en el listado:
    borrarla ahora sería esconderle facturas que él ya cargó.
    """
    h = auth_headers(client, "admin.a")
    gasto = un_gasto(client, h)
    fila = client.get(f"{G}/filtrar/avanzado", headers=h).json()["items"][0]
    assert "adjunto_url" in fila and fila["id"] == gasto["id"]
