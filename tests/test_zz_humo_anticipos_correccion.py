"""Humo: corregir tambien los anticipos de una quincena pagada."""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
V = "/api/v1"


def montar(client, h):
    prov = client.post(f"{V}/proveedores", json={
        "nombre": "Libardo", "vereda": "El Roble", "precio_litro": "2000"}, headers=h).json()
    client.post(f"{V}/recepciones", json={
        "fecha": "2026-06-02", "proveedor_id": prov["id"], "cantidad_litros": "250"}, headers=h)
    # Un anticipo que SI se le descuenta
    client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-06-03",
        "valor": "100000", "observaciones": "para la droga"}, headers=h)
    gen = client.post(f"{API}/generar", json={
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15",
        "tipo": "proveedor"}, headers=h).json()["generadas"]
    liq = next(x for x in gen if x["proveedor_id"] == prov["id"])
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h).json()
    return prov, liq, pagada


def test_humo_entra_un_anticipo_olvidado(client, base_datos):
    h = auth_headers(client, "admin.a")
    prov, liq, pagada = montar(client, h)
    print("\n=== PAGADA ===", "total", pagada["valor_total"], "anticipos", pagada["anticipos"],
          "neto", pagada["neto_a_pagar"], "pagado", pagada["pagado"], "saldo", pagada["saldo"])
    assert Decimal(pagada["anticipos"]) == Decimal("100000.00")

    # SE LE OLVIDO OTRO ADELANTO
    otro = client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-06-10",
        "valor": "150000", "observaciones": "el del mercado"}, headers=h)
    assert otro.status_code == 201, otro.text

    prev = client.post(f"{API}/{liq['id']}/corregir/previsualizar",
                       json={"motivo": "faltaba el adelanto del 10"}, headers=h)
    assert prev.status_code == 200, prev.text
    p = prev.json()
    print("=== APLICADOS ===", [(a["fecha"], a["valor"]) for a in p["anticipos_aplicados"]])
    print("=== SUELTOS   ===", [(a["fecha"], a["valor"], a["aviso"]) for a in p["anticipos_sueltos"]])
    assert len(p["anticipos_aplicados"]) == 1
    assert len(p["anticipos_sueltos"]) == 1
    suelto = p["anticipos_sueltos"][0]["anticipo_id"]

    prev2 = client.post(f"{API}/{liq['id']}/corregir/previsualizar", json={
        "motivo": "faltaba el adelanto del 10", "anticipos_a_incluir": [suelto]}, headers=h).json()
    print("=== PREVIEW ===", "anticipos", prev2["anticipos_antes"], "->", prev2["anticipos_despues"],
          "| neto", prev2["neto_antes"], "->", prev2["neto_despues"],
          "| saldo", prev2["saldo_antes"], "->", prev2["saldo_despues"])
    assert Decimal(prev2["anticipos_despues"]) == Decimal("250000.00")

    hecho = client.post(f"{API}/{liq['id']}/corregir", json={
        "motivo": "faltaba el adelanto del 10", "anticipos_a_incluir": [suelto]}, headers=h)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== CORREGIDA ===", "total", d["valor_total"], "anticipos", d["anticipos"],
          "neto", d["neto_a_pagar"], "pagado", d["pagado"], "saldo", d["saldo"],
          "estado", d["estado"], "version", d["version"])
    # LA REGLA DE LA CASA
    assert Decimal(d["neto_a_pagar"]) == Decimal(d["pagado"]) + Decimal(d["saldo"])
    assert Decimal(d["neto_a_pagar"]) == Decimal(d["valor_total"]) - Decimal(d["anticipos"])
    assert Decimal(d["anticipos"]) == Decimal("250000.00")
    # Se le pago de mas: el anticipo nuevo baja el neto por debajo de lo entregado
    assert Decimal(d["saldo"]) == Decimal("-150000.00"), d["saldo"]
    assert Decimal(d["le_queda_debiendo"]) == Decimal("150000.00")

    corr = client.get(f"{API}/{liq['id']}/correcciones", headers=h).json()[0]
    print("=== RENGLON ===", corr["anticipos_antes"], "->", corr["anticipos_despues"],
          corr["anticipos_cambiados"])
    assert Decimal(corr["anticipos_antes"]) == Decimal("100000.00")
    assert Decimal(corr["anticipos_despues"]) == Decimal("250000.00")
    assert corr["anticipos_cambiados"][0]["accion"] == "entro"


def test_humo_sale_un_anticipo_que_no_iba(client, base_datos):
    """El que sale NO se borra: queda suelto para la quincena siguiente."""
    h = auth_headers(client, "admin.a")
    prov, liq, pagada = montar(client, h)
    aplicado = client.post(f"{API}/{liq['id']}/corregir/previsualizar",
                           json={"motivo": "mirar"}, headers=h).json()["anticipos_aplicados"][0]

    hecho = client.post(f"{API}/{liq['id']}/corregir", json={
        "motivo": "ese adelanto no era de esta quincena",
        "anticipos_a_soltar": [aplicado["anticipo_id"]]}, headers=h)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("\n=== SIN EL ANTICIPO ===", "total", d["valor_total"], "anticipos", d["anticipos"],
          "neto", d["neto_a_pagar"], "pagado", d["pagado"], "saldo", d["saldo"], "estado", d["estado"])
    assert Decimal(d["anticipos"]) == Decimal("0.00")
    assert Decimal(d["neto_a_pagar"]) == Decimal(d["pagado"]) + Decimal(d["saldo"])
    # Subio el neto: hay que entregarle los $100.000 que se le habian descontado
    assert Decimal(d["saldo"]) == Decimal("100000.00")

    # Y EL ANTICIPO SIGUE VIVO, suelto
    ant = client.get(f"{V}/anticipos/{aplicado['anticipo_id']}", headers=h)
    assert ant.status_code == 200, ant.text
    print("=== EL ANTICIPO ===", ant.json()["valor"], "liquidacion_id:",
          ant.json().get("liquidacion_id"))
    assert ant.json().get("liquidacion_id") is None
    assert Decimal(ant.json()["valor"]) == Decimal("100000.00")


def test_humo_se_corrige_el_valor_de_un_anticipo(client, base_datos):
    h = auth_headers(client, "admin.a")
    prov, liq, pagada = montar(client, h)
    aplicado = client.post(f"{API}/{liq['id']}/corregir/previsualizar",
                           json={"motivo": "mirar"}, headers=h).json()["anticipos_aplicados"][0]

    hecho = client.post(f"{API}/{liq['id']}/corregir", json={
        "motivo": "el adelanto eran 80 mil, no 100 mil",
        "valores_de_anticipos": [{"anticipo_id": aplicado["anticipo_id"], "valor": "80000"}]},
        headers=h)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("\n=== VALOR CORREGIDO ===", "anticipos", d["anticipos"], "neto", d["neto_a_pagar"],
          "pagado", d["pagado"], "saldo", d["saldo"])
    assert Decimal(d["anticipos"]) == Decimal("80000.00")
    assert Decimal(d["neto_a_pagar"]) == Decimal(d["pagado"]) + Decimal(d["saldo"])

    corr = client.get(f"{API}/{liq['id']}/correcciones", headers=h).json()[0]
    cambio = corr["anticipos_cambiados"][0]
    print("=== RENGLON ===", cambio)
    assert cambio["accion"] == "valor"
    assert cambio["valor_antes"] == "100000.00" and cambio["valor"] == "80000.00"


def test_humo_el_papel_nombra_el_anticipo(client, base_datos):
    import io as _io
    from pypdf import PdfReader
    h = auth_headers(client, "admin.a")
    prov, liq, pagada = montar(client, h)
    client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-06-10",
        "valor": "150000", "observaciones": "el del mercado"}, headers=h)
    suelto = client.post(f"{API}/{liq['id']}/corregir/previsualizar",
                         json={"motivo": "mirar"}, headers=h).json()["anticipos_sueltos"][0]
    client.post(f"{API}/{liq['id']}/corregir", json={
        "motivo": "faltaba el adelanto del 10", "anticipos_a_incluir": [suelto["anticipo_id"]]},
        headers=h)

    pdf = client.get(f"{API}/{liq['id']}/pdf", headers=h)
    assert pdf.status_code == 200
    t = "\n".join(p.extract_text() for p in PdfReader(_io.BytesIO(pdf.content)).pages)
    print("\n=== el papel nombra el adelanto? ===", "se le descont" in t)
    print("=== trae la fecha 10/06/2026? ===", "10/06/2026" in t)
    assert "se le descont" in t
    assert "10/06/2026" in t
