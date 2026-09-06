"""LA RUTA AJENA Y LA PLATA DEL FLETE FIJO POR DIA Y POR RUTA.

Ya quedo medido que un `ruta_id` de la quesera A entra a la quesera B por
`POST/PUT /proveedores` y sale IMPRESO en el comprobante del conductor de B.
Falta lo que mas duele: con el flete FIJO POR DIA Y POR RUTA, ¿esa ruta ajena
tambien decide la plata?
"""
import io
from decimal import Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

V = "/api/v1"


def D(x):
    return Decimal(str(x))


def _pdf(client, h, liq_id):
    r = client.get(f"{V}/liquidaciones/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)


def test_el_flete_por_dia_y_ruta_con_una_ruta_de_la_otra_quesera(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    ruta_a = client.post(f"{V}/rutas",
                         json={"nombre": "RutaSecretaDeA", "municipio": "Calamar"},
                         headers=ha).json()
    ruta_b = client.post(f"{V}/rutas",
                         json={"nombre": "RutaPropiaDeB", "municipio": "ElRetorno"},
                         headers=hb).json()

    # El transportador de B cobra FIJO POR DIA sobre SU ruta
    trans_b = client.post(f"{V}/transportadores", json={
        "nombre": "TransportadorDeB", "valor_transporte": "242.76",
        "modo_transporte": "dia_fijo",
        "rutas": [{"ruta_id": ruta_b["id"], "valor_transporte": "45000",
                   "modo_transporte": "dia_fijo"}],
    }, headers=hb)
    print("transportador de B en dia_fijo ->", trans_b.status_code, trans_b.text[:220])
    assert trans_b.status_code == 201, trans_b.text
    trans_b = trans_b.json()

    # Dos productores de B: uno con SU ruta, otro con la ruta AJENA
    prov_ok = client.post(f"{V}/proveedores", json={
        "nombre": "ProductorLimpio", "vereda": "V", "precio_litro": "1833.33",
        "ruta_id": ruta_b["id"],
    }, headers=hb).json()
    prov_sucio = client.post(f"{V}/proveedores", json={
        "nombre": "ProductorContaminado", "vereda": "V", "precio_litro": "1833.33",
        "ruta_id": ruta_a["id"],
    }, headers=hb)
    print("proveedor de B con la ruta de A ->", prov_sucio.status_code)
    assert prov_sucio.status_code == 201, prov_sucio.text
    prov_sucio = prov_sucio.json()

    for prov in (prov_ok, prov_sucio):
        r = client.post(f"{V}/recepciones", json={
            "fecha": "2026-07-01", "proveedor_id": prov["id"],
            "transportador_id": trans_b["id"], "cantidad_litros": "137.45",
            "precio_litro": "1833.33",
        }, headers=hb)
        assert r.status_code == 201, r.text
        print(f"recepcion de {prov['nombre']} -> ruta_id {r.json().get('ruta_id')}")

    gen = client.post(f"{V}/liquidaciones/generar", json={
        "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "transportador",
    }, headers=hb)
    assert gen.status_code == 200, gen.text
    liq_id = gen.json()["generadas"][0]["id"]
    liq = client.get(f"{V}/liquidaciones/{liq_id}", headers=hb).json()

    print("\n--- comprobante del transportador de B ---")
    print("valor_transporte:", liq["valor_transporte"], " neto:", liq["neto_a_pagar"])
    suma = Decimal("0")
    for d in liq["detalles"]:
        print("  detalle COMPLETO:", d)
        suma += D(d.get("valor") or 0)
    print("suma de los renglones:", suma, " cabecera:", liq["valor_transporte"],
          " ¿CUADRA?", suma == D(liq["valor_transporte"]))

    texto = _pdf(client, hb, liq_id)
    print("¿'RutaSecretaDeA' IMPRESA en el PDF de B?", "RutaSecretaDeA" in texto)
    for linea in texto.splitlines():
        if "Ruta" in linea:
            print("   PDF:", linea.strip())
