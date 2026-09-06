"""LA RUTA AJENA VUELVE A ENTRAR, PERO POR LA PUERTA DEL PROVEEDOR.

POST/PUT /recepciones ya rechaza un `ruta_id` de otra quesera (eso lo cerro
`test_recepcion_ruta_tenancy`). Pero `POST /proveedores` NO mira la empresa de
`ruta_id`, y la recepcion HEREDA la ruta del proveedor. Se prueba de punta a
punta: ¿el nombre de la ruta de A sale en los papeles de B, y decide plata?
"""
import io
from decimal import Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

V = "/api/v1"


def D(x):
    return Decimal(str(x))


def test_la_ruta_de_a_entra_a_b_por_el_proveedor(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    # --- La quesera A tiene una ruta con nombre reconocible
    ruta_a = client.post(
        f"{V}/rutas", json={"nombre": "RutaSecretaDeA", "municipio": "Calamar"}, headers=ha
    ).json()
    assert "id" in ruta_a, ruta_a

    # --- B crea un proveedor con la ruta de A: la puerta que no mira
    prov_b = client.post(
        f"{V}/proveedores",
        json={
            "nombre": "ProductorDeB",
            "vereda": "El Roble",
            "precio_litro": "1833.33",
            "ruta_id": ruta_a["id"],
        },
        headers=hb,
    )
    print("POST /proveedores con ruta de A ->", prov_b.status_code)
    assert prov_b.status_code == 201, prov_b.text
    prov_b = prov_b.json()
    assert prov_b["ruta_id"] == ruta_a["id"]
    print("  ruta_nombre que le devuelve a B:", prov_b.get("ruta_nombre"))

    # --- B no puede ver esa ruta por su propio endpoint (o sea: es ajena)
    ver = client.get(f"{V}/rutas/{ruta_a['id']}", headers=hb)
    assert ver.status_code == 404, ver.text

    # --- Un transportador de B con tarifa POR RUTA sobre la ruta ajena
    trans_b = client.post(
        f"{V}/transportadores",
        json={"nombre": "TransportadorDeB", "valor_transporte": "100"},
        headers=hb,
    ).json()

    # --- Recepcion de B: NO manda ruta_id, la hereda del proveedor
    rec = client.post(
        f"{V}/recepciones",
        json={
            "fecha": "2026-07-03",
            "proveedor_id": prov_b["id"],
            "transportador_id": trans_b["id"],
            "cantidad_litros": "137.45",
            "precio_litro": "1833.33",
        },
        headers=hb,
    )
    assert rec.status_code == 201, rec.text
    rec = rec.json()
    print("Recepcion de B -> ruta_id:", rec.get("ruta_id"), "ruta_nombre:", rec.get("ruta_nombre"))

    # ¿La recepcion de B quedo con la ruta de A?
    heredo = rec.get("ruta_id") == ruta_a["id"]
    print("HEREDO LA RUTA AJENA:", heredo)

    # --- La grilla de la quincena de B
    grilla = client.get(
        f"{V}/recepciones/grilla/quincena",
        params={"desde": "2026-07-01", "hasta": "2026-07-15"},
        headers=hb,
    )
    assert grilla.status_code == 200, grilla.text
    texto_grilla = grilla.text
    print("¿'RutaSecretaDeA' en la grilla de B?", "RutaSecretaDeA" in texto_grilla)

    # --- El comprobante del transportador de B
    gen = client.post(
        f"{V}/liquidaciones/generar",
        json={"periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "transportador"},
        headers=hb,
    )
    assert gen.status_code == 200, gen.text
    generadas = gen.json()["generadas"]
    print("liquidaciones generadas para B:", len(generadas))

    encontrado_en_pdf = False
    if generadas:
        liq_id = generadas[0]["id"]
        det = client.get(f"{V}/liquidaciones/{liq_id}", headers=hb).json()
        print("¿'RutaSecretaDeA' en el detalle de la liquidacion de B?",
              "RutaSecretaDeA" in str(det))
        pdf = client.get(f"{V}/liquidaciones/{liq_id}/pdf", headers=hb)
        assert pdf.status_code == 200, pdf.text
        texto = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf.content)).pages)
        encontrado_en_pdf = "RutaSecretaDeA" in texto
        print("¿'RutaSecretaDeA' IMPRESA en el PDF que recibe el conductor de B?",
              encontrado_en_pdf)
        if encontrado_en_pdf:
            for linea in texto.splitlines():
                if "RutaSecretaDeA" in linea:
                    print("   LINEA IMPRESA:", linea.strip())

    print("RESUMEN: heredo=", heredo, " en_pdf=", encontrado_en_pdf)


def test_put_proveedor_tambien_acepta_ruta_ajena(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    ruta_a = client.post(
        f"{V}/rutas", json={"nombre": "OtraRutaDeA", "municipio": "Miraflores"}, headers=ha
    ).json()
    prov_b = client.post(
        f"{V}/proveedores",
        json={"nombre": "ProdB", "vereda": "V", "precio_litro": "1700"},
        headers=hb,
    ).json()
    r = client.put(
        f"{V}/proveedores/{prov_b['id']}", json={"ruta_id": ruta_a["id"]}, headers=hb
    )
    print("PUT /proveedores con ruta de A ->", r.status_code, r.text[:200])
    assert r.status_code in (200, 404, 422)


def test_las_otras_puertas_de_la_ruta_ajena(client, base_datos):
    """Control: que puertas SI rechazan la ruta de la otra quesera."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    ruta_a = client.post(f"{V}/rutas", json={"nombre": "RutaDeA3", "municipio": "Calamar"},
                         headers=ha).json()
    prov_b = client.post(f"{V}/proveedores",
                         json={"nombre": "ProdB3", "vereda": "V", "precio_litro": "1700"},
                         headers=hb).json()
    trans_b = client.post(f"{V}/transportadores",
                          json={"nombre": "TransB3", "valor_transporte": "242.76"},
                          headers=hb).json()
    rec = client.post(f"{V}/recepciones", json={
        "fecha": "2026-07-07", "proveedor_id": prov_b["id"], "cantidad_litros": "44.23",
        "precio_litro": "1700",
    }, headers=hb).json()

    pruebas = [
        ("POST /recepciones con ruta de A", client.post(f"{V}/recepciones", json={
            "fecha": "2026-07-08", "proveedor_id": prov_b["id"], "cantidad_litros": "44.23",
            "ruta_id": ruta_a["id"],
        }, headers=hb)),
        ("PUT /recepciones con ruta de A", client.put(f"{V}/recepciones/{rec['id']}", json={
            "ruta_id": ruta_a["id"],
        }, headers=hb)),
        ("POST /transportadores con ruta de A", client.post(f"{V}/transportadores", json={
            "nombre": "TransB4", "valor_transporte": "100",
            "rutas": [{"ruta_id": ruta_a["id"], "valor_transporte": "242.76"}],
        }, headers=hb)),
        ("PUT /transportadores con ruta de A", client.put(
            f"{V}/transportadores/{trans_b['id']}", json={
                "rutas": [{"ruta_id": ruta_a["id"], "valor_transporte": "242.76"}],
            }, headers=hb)),
        ("GET /rutas/{ajena}", client.get(f"{V}/rutas/{ruta_a['id']}", headers=hb)),
        ("PUT /rutas/{ajena}", client.put(f"{V}/rutas/{ruta_a['id']}",
                                          json={"nombre": "Robada"}, headers=hb)),
        ("DELETE /rutas/{ajena}", client.delete(f"{V}/rutas/{ruta_a['id']}", headers=hb)),
        ("POST /proveedores con ruta de A", client.post(f"{V}/proveedores", json={
            "nombre": "ProdB5", "vereda": "V", "precio_litro": "1700", "ruta_id": ruta_a["id"],
        }, headers=hb)),
        ("PUT /proveedores con ruta de A", client.put(f"{V}/proveedores/{prov_b['id']}", json={
            "ruta_id": ruta_a["id"],
        }, headers=hb)),
    ]
    print("\n===== TODAS LAS PUERTAS DE LA RUTA AJENA =====")
    abiertas = []
    for tag, r in pruebas:
        marca = "  " if r.status_code >= 400 else "!!"
        print(f"{marca} {r.status_code}  {tag}")
        if r.status_code < 400:
            abiertas.append(tag)
    print("PUERTAS ABIERTAS:", abiertas)
