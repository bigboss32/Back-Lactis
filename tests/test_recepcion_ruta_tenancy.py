"""LA RUTA DE LA RECEPCIÓN NO PUEDE SER DE OTRA QUESERA.

`recepciones_leche.ruta_id` no lo miraba nadie: solo la llave foránea, que en una
base multiempresa POR FILA no sabe de empresas. La fuga se reprodujo de punta a
punta y sale impresa:

  el admin de la quesera B crea la ruta "RutaSecretaDeB";
  el admin de la quesera A manda ese `ruta_id` en un POST /recepciones -> 201;
  y de ahí el NOMBRE de esa ruta ajena aparece en la recepción, en el comprobante
  del transportador y en el PDF que se le entrega al conductor.

Y no es solo el nombre: desde que la tarifa se cobra POR RUTA, un `ruta_id` ajeno
también decide la plata del flete de ese día.

Se cierra en las DOS puertas que escriben una recepción (POST y PUT), con el mismo
remedio que ya usaba el transportador: buscar la ruta por
`RutaRepository(db, ctx.empresa_id)`, cuya consulta base ya filtra empresa y
borrados. Lo que tiene que SEGUIR funcionando —y también se prueba acá— es la ruta
propia, la ruta que se hereda del proveedor, y quitarle la ruta a un día.
"""
import io
from decimal import Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
REC = "/api/v1/recepciones"
API = "/api/v1/liquidaciones"


def D(v):
    return Decimal(str(v))


def _ruta(client, h, nombre):
    r = client.post(RUTAS, json={"nombre": nombre, "municipio": "Granada"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _transportador(client, h, nombre, rutas, general="100"):
    r = client.post(
        TRANSPORTADORES,
        json={
            "nombre": nombre,
            "valor_transporte": general,
            "rutas": [{"ruta_id": ru["id"], "valor_transporte": str(v)} for ru, v in rutas],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _proveedor(client, h, nombre, ruta=None):
    cuerpo = {"nombre": nombre, "vereda": "El Roble", "precio_litro": "1800"}
    if ruta:
        cuerpo["ruta_id"] = ruta["id"]
    r = client.post(PROVEEDORES, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _escenario_a(client, h):
    """La quesera A, completa: su ruta, su transportador con tarifa y su proveedor."""
    napoles = _ruta(client, h, "Napoles")
    alex = _transportador(client, h, "Alex", [(napoles, "242.76")])
    juan = _proveedor(client, h, "Juan", napoles)
    return napoles, alex, juan


# ===========================================================================
# 1. LA PUERTA DEL POST
# ===========================================================================
def test_el_post_rechaza_la_ruta_de_otra_empresa(client, base_datos):
    """El caso reproducido, tal cual: A manda el `ruta_id` de B."""
    hb = auth_headers(client, "admin.b")
    ruta_b = _ruta(client, hb, "RutaSecretaDeB")

    h = auth_headers(client, "admin.a")
    _, alex, juan = _escenario_a(client, h)

    r = client.post(
        REC,
        json={
            "fecha": "2026-06-02",
            "proveedor_id": juan["id"],
            "transportador_id": alex["id"],
            "ruta_id": ruta_b["id"],
            "cantidad_litros": "44.23",
        },
        headers=h,
    )
    print("\n===== 1. POST CON LA RUTA DE LA OTRA QUESERA =====")
    print(f"  POST /recepciones con ruta_id de B -> {r.status_code}")
    assert r.status_code == 404, (
        f"la ruta de la quesera B entró en una recepción de la A: {r.status_code} {r.text}"
    )

    # Y no quedó nada escrito: el día no existe.
    listado = client.get(f"{REC}?desde=2026-06-01&hasta=2026-06-15", headers=h).json()
    assert listado["total"] == 0, f"quedó una recepción a medio crear: {listado}"


def test_el_nombre_de_la_ruta_ajena_ya_no_puede_llegar_al_comprobante(client, base_datos):
    """Lo que la fuga hacía visible: el nombre ajeno impreso en el papel.

    Se recorre el camino completo —recepción, comprobante del transportador y PDF—
    para que la prueba falle donde de verdad dolía si alguien vuelve a abrir la
    puerta: en el papel que firma el conductor.
    """
    hb = auth_headers(client, "admin.b")
    ruta_b = _ruta(client, hb, "RutaSecretaDeB")

    h = auth_headers(client, "admin.a")
    napoles, alex, juan = _escenario_a(client, h)
    r = client.post(
        REC,
        json={
            "fecha": "2026-06-02", "proveedor_id": juan["id"],
            "transportador_id": alex["id"], "ruta_id": ruta_b["id"],
            "cantidad_litros": "44.23",
        },
        headers=h,
    )
    assert r.status_code == 404, r.text

    # El día se recibe con la ruta PROPIA, que es lo que tenía que pasar desde el
    # principio, y el comprobante sale nombrando SOLO esa.
    r = client.post(
        REC,
        json={
            "fecha": "2026-06-02", "proveedor_id": juan["id"],
            "transportador_id": alex["id"], "ruta_id": napoles["id"],
            "cantidad_litros": "44.23",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    liqs = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15",
              "tipo": "transportador"},
        headers=h,
    ).json()
    liq = liqs[0]
    nombres = [d["ruta_nombre"] for d in liq["detalles"]]
    pdf = client.get(f"{API}/{liq['id']}/pdf", headers=h)
    assert pdf.status_code == 200
    texto = " ".join(
        "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf.content)).pages).split()
    )
    print("\n===== 2. EL PAPEL DEL CONDUCTOR =====")
    print(f"  rutas del comprobante: {nombres}")
    assert nombres == ["Napoles"], nombres
    assert "RutaSecretaDeB" not in nombres
    assert "RutaSecretaDeB" not in texto, (
        "el nombre de una ruta de la otra quesera salió impreso en el PDF"
    )
    assert "Napoles" in texto


# ===========================================================================
# 2. LA PUERTA DEL PUT
# ===========================================================================
def test_el_put_rechaza_la_ruta_de_otra_empresa(client, base_datos):
    """La otra puerta, y la más fácil de olvidar: cambiarle la ruta a un día que ya
    existe. Además del nombre impreso, acá la ruta ajena le cambiaría la TARIFA con
    la que se le cobra el flete a ese día."""
    hb = auth_headers(client, "admin.b")
    ruta_b = _ruta(client, hb, "RutaSecretaDeB")
    # A la ruta de B se le pone tarifa en B, para que si la fuga se reabriera el
    # flete cambiara de cifra y no solo de nombre.
    prov_b = _proveedor(client, hb, "Alguien de B", ruta_b)
    assert prov_b["ruta_id"] == ruta_b["id"]

    h = auth_headers(client, "admin.a")
    napoles, alex, juan = _escenario_a(client, h)
    rec = client.post(
        REC,
        json={
            "fecha": "2026-06-02", "proveedor_id": juan["id"],
            "transportador_id": alex["id"], "ruta_id": napoles["id"],
            "cantidad_litros": "44.23",
        },
        headers=h,
    ).json()
    flete_antes = D(rec["valor_transporte"])
    assert flete_antes == D("10737.27")  # 44,23 L x $242,76

    r = client.put(f"{REC}/{rec['id']}", json={"ruta_id": ruta_b["id"]}, headers=h)
    print("\n===== 3. PUT CON LA RUTA DE LA OTRA QUESERA =====")
    print(f"  PUT /recepciones/{{id}} con ruta_id de B -> {r.status_code}")
    assert r.status_code == 404, (
        f"le cambiaron la ruta de un día a una ruta de la quesera B: "
        f"{r.status_code} {r.text}"
    )

    # Ni la ruta ni la plata del flete se movieron.
    releido = client.get(f"{REC}/{rec['id']}", headers=h).json()
    print(f"  la recepción sigue en ruta_id propia y flete ${releido['valor_transporte']}")
    assert releido["ruta_id"] == napoles["id"]
    assert D(releido["valor_transporte"]) == flete_antes


def test_una_ruta_que_no_existe_en_ninguna_parte_tambien_rebota(client, base_datos):
    """Un uuid inventado (o el de una ruta ya borrada que el día no tenía): antes
    solo lo paraba la llave foránea, y con un 500 en la cara del usuario."""
    import uuid as _uuid

    h = auth_headers(client, "admin.a")
    napoles, alex, juan = _escenario_a(client, h)
    inventado = str(_uuid.uuid4())
    r = client.post(
        REC,
        json={
            "fecha": "2026-06-02", "proveedor_id": juan["id"],
            "transportador_id": alex["id"], "ruta_id": inventado,
            "cantidad_litros": "44.23",
        },
        headers=h,
    )
    print("\n===== 4. UNA RUTA QUE NO EXISTE =====")
    print(f"  POST con un uuid inventado -> {r.status_code}")
    assert r.status_code == 404, r.text

    rec = client.post(
        REC,
        json={
            "fecha": "2026-06-02", "proveedor_id": juan["id"],
            "transportador_id": alex["id"], "cantidad_litros": "44.23",
        },
        headers=h,
    ).json()
    r = client.put(f"{REC}/{rec['id']}", json={"ruta_id": inventado}, headers=h)
    print(f"  PUT con un uuid inventado  -> {r.status_code}")
    assert r.status_code == 404, r.text


# ===========================================================================
# 3. LO QUE TIENE QUE SEGUIR FUNCIONANDO
# ===========================================================================
def test_la_ruta_que_se_hereda_del_proveedor_sigue_entrando(client, base_datos):
    """El caso normal, y el que no se podía romper: el POST no manda `ruta_id` y el
    día toma la del proveedor. Con esa ruta se le deriva el flete."""
    h = auth_headers(client, "admin.a")
    napoles, alex, juan = _escenario_a(client, h)
    r = client.post(
        REC,
        json={
            "fecha": "2026-06-02", "proveedor_id": juan["id"],
            "transportador_id": alex["id"], "cantidad_litros": "44.23",
        },
        headers=h,
    )
    print("\n===== 5. LA RUTA HEREDADA DEL PROVEEDOR =====")
    assert r.status_code == 201, r.text
    rec = r.json()
    print(f"  sin ruta_id en el POST -> ruta_id={rec['ruta_id'] == napoles['id']} "
          f"flete=${rec['valor_transporte']}")
    assert rec["ruta_id"] == napoles["id"], "el día no heredó la ruta del proveedor"
    # Y el flete salió con la tarifa DE ESA RUTA, no con la general del transportador.
    assert D(rec["valor_transporte"]) == D("10737.27")


def test_la_ruta_propia_y_quitarle_la_ruta_al_dia_siguen_funcionando(client, base_datos):
    """Mandar una ruta PROPIA (otra distinta de la del proveedor) y después dejar el
    día SIN ruta. Las dos son operaciones legítimas de todos los días: un `null` en
    `ruta_id` es "quítele la ruta", y ahí el flete sale de la tarifa general."""
    h = auth_headers(client, "admin.a")
    napoles, alex, juan = _escenario_a(client, h)
    mira_valle = _ruta(client, h, "Mira Valle")
    r = client.put(
        f"{TRANSPORTADORES}/{alex['id']}",
        json={"rutas": [
            {"ruta_id": napoles["id"], "valor_transporte": "242.76"},
            {"ruta_id": mira_valle["id"], "valor_transporte": "317.50"},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text

    rec = client.post(
        REC,
        json={
            "fecha": "2026-06-02", "proveedor_id": juan["id"],
            "transportador_id": alex["id"], "ruta_id": mira_valle["id"],
            "cantidad_litros": "44.23",
        },
        headers=h,
    )
    print("\n===== 6. RUTA PROPIA DISTINTA, Y DESPUÉS SIN RUTA =====")
    assert rec.status_code == 201, rec.text
    rec = rec.json()
    print(f"  con Mira Valle ($317,50) -> ${rec['valor_transporte']}")
    assert rec["ruta_id"] == mira_valle["id"]
    # 44,23 L x $317,50 = 14.043,025 -> el medio centavo sube: $14.043,03
    assert D(rec["valor_transporte"]) == D("14043.03")

    r = client.put(f"{REC}/{rec['id']}", json={"ruta_id": None}, headers=h)
    assert r.status_code == 200, r.text
    sin_ruta = r.json()
    print(f"  sin ruta (tarifa general $100) -> ${sin_ruta['valor_transporte']}")
    assert sin_ruta["ruta_id"] is None
    assert D(sin_ruta["valor_transporte"]) == D("4423.00")  # 44,23 x $100 general


def test_un_dia_con_su_ruta_ya_borrada_se_puede_seguir_editando(client, base_datos):
    """El día ya existe, alguien borró la ruta, y la pantalla reenvía lo que leyó.

    Es la trampa que el transportador ya había pisado: si el PUT exigiera que la ruta
    esté VIGENTE, un día con la ruta borrada quedaría imposible de editar —ni para
    corregirle las observaciones—, porque la lectura devuelve `ruta_id` y la pantalla
    guarda lo que leyó. La ruta borrada se acepta SOLO si es la que el día ya tenía y
    es de esta empresa.
    """
    h = auth_headers(client, "admin.a")
    napoles, alex, juan = _escenario_a(client, h)
    rec = client.post(
        REC,
        json={
            "fecha": "2026-06-02", "proveedor_id": juan["id"],
            "transportador_id": alex["id"], "ruta_id": napoles["id"],
            "cantidad_litros": "44.23",
        },
        headers=h,
    ).json()
    borrado = client.delete(f"{RUTAS}/{napoles['id']}", headers=h)
    print("\n===== 7. LA RUTA DEL DÍA, YA BORRADA =====")
    print(f"  DELETE /rutas/{{id}} -> {borrado.status_code}")
    assert borrado.status_code in (200, 204), borrado.text

    leido = client.get(f"{REC}/{rec['id']}", headers=h).json()
    assert leido["ruta_id"] == napoles["id"], (
        "la lectura ya no devuelve la ruta del día: sin eso no hay nada que reenviar"
    )
    r = client.put(
        f"{REC}/{rec['id']}",
        json={"ruta_id": leido["ruta_id"], "observaciones": "el tarro venia mal tapado"},
        headers=h,
    )
    print(f"  PUT reenviando la ruta borrada del día -> {r.status_code}")
    assert r.status_code == 200, (
        f"el día quedó imposible de editar por tener la ruta borrada: {r.text}"
    )
    assert r.json()["observaciones"] == "el tarro venia mal tapado"
