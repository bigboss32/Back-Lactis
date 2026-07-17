"""Flujo completo del negocio lechero: recepción con cálculos automáticos
y liquidación por quincena con anticipos, replicando la lógica del Excel."""
from tests.conftest import auth_headers


def _setup_leche(client, headers):
    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"}, headers=headers
    ).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Stella", "ruta_id": ruta["id"], "valor_transporte": "100"},
        headers=headers,
    ).json()
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Libardo", "vereda": "Granada", "precio_litro": "1800", "ruta_id": ruta["id"]},
        headers=headers,
    ).json()
    return ruta, transportador, proveedor


def test_recepcion_calcula_valores(client, base_datos):
    headers = auth_headers(client, "admin.a")
    _, transportador, proveedor = _setup_leche(client, headers)

    response = client.post(
        "/api/v1/recepciones",
        json={
            "fecha": "2026-06-01",
            "proveedor_id": proveedor["id"],
            "transportador_id": transportador["id"],
            "cantidad_litros": "227",
            "descuentos": "10000",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    recepcion = response.json()
    # precio tomado del proveedor: 227 × 1800 = 408600
    assert float(recepcion["precio_litro"]) == 1800
    assert float(recepcion["valor_bruto"]) == 227 * 1800
    assert float(recepcion["valor_transporte"]) == 227 * 100
    assert float(recepcion["valor_neto"]) == 227 * 1800 - 10000


def test_filtro_por_ruta_y_busqueda_por_nombre(client, base_datos):
    headers = auth_headers(client, "admin.a")
    ruta1 = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta Norte", "municipio": "Norte"}, headers=headers
    ).json()
    ruta2 = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta Sur", "municipio": "Sur"}, headers=headers
    ).json()
    prov1 = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Alberto", "vereda": "Norte", "precio_litro": "1800", "ruta_id": ruta1["id"]},
        headers=headers,
    ).json()
    prov2 = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Bernardo", "vereda": "Sur", "precio_litro": "1800", "ruta_id": ruta2["id"]},
        headers=headers,
    ).json()
    client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-06-01", "proveedor_id": prov1["id"], "cantidad_litros": "100"},
        headers=headers,
    )
    client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-06-01", "proveedor_id": prov2["id"], "cantidad_litros": "80"},
        headers=headers,
    )

    # Buscar por nombre "alb" (parcial, sin importar mayúsculas) -> solo Alberto
    r = client.get("/api/v1/recepciones/filtrar/avanzado?search=alb", headers=headers).json()
    assert r["total"] == 1
    assert r["items"][0]["proveedor_nombre"] == "Alberto"

    # Filtrar por Ruta Sur -> solo la recepción de Bernardo
    r = client.get(
        f"/api/v1/recepciones/filtrar/avanzado?ruta_id={ruta2['id']}", headers=headers
    ).json()
    assert r["total"] == 1
    assert r["items"][0]["proveedor_nombre"] == "Bernardo"


def test_grilla_filtra_por_ruta_y_nombre(client, base_datos):
    headers = auth_headers(client, "admin.a")
    ruta1 = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta A", "municipio": "A"}, headers=headers
    ).json()
    ruta2 = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta B", "municipio": "B"}, headers=headers
    ).json()
    p1 = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Aurelio", "vereda": "A", "precio_litro": "1800", "ruta_id": ruta1["id"]},
        headers=headers,
    ).json()
    p2 = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Bruno", "vereda": "B", "precio_litro": "1800", "ruta_id": ruta2["id"]},
        headers=headers,
    ).json()
    client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-06-01", "proveedor_id": p1["id"], "cantidad_litros": "10"},
        headers=headers,
    )
    client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-06-01", "proveedor_id": p2["id"], "cantidad_litros": "20"},
        headers=headers,
    )

    # Buscar por nombre en la grilla -> solo Aurelio
    g = client.get(
        "/api/v1/recepciones/grilla/quincena?desde=2026-06-01&hasta=2026-06-15&search=aur",
        headers=headers,
    ).json()
    assert [f["proveedor_nombre"] for f in g["filas"]] == ["Aurelio"]
    assert float(g["total_litros"]) == 10

    # Filtrar por Ruta B -> solo Bruno
    g = client.get(
        f"/api/v1/recepciones/grilla/quincena?desde=2026-06-01&hasta=2026-06-15&ruta_id={ruta2['id']}",
        headers=headers,
    ).json()
    assert [f["proveedor_nombre"] for f in g["filas"]] == ["Bruno"]
    assert float(g["total_litros"]) == 20


def test_anticipo_transportador_se_descuenta_en_su_liquidacion(client, base_datos):
    headers = auth_headers(client, "admin.a")
    _, transportador, proveedor = _setup_leche(client, headers)
    client.post(
        "/api/v1/recepciones",
        json={
            "fecha": "2026-06-01", "proveedor_id": proveedor["id"],
            "transportador_id": transportador["id"], "cantidad_litros": "100",
        },
        headers=headers,
    )
    # Anticipo AL TRANSPORTADOR
    client.post(
        "/api/v1/anticipos",
        json={"tipo": "transportador", "transportador_id": transportador["id"],
              "fecha": "2026-06-03", "valor": "5000"},
        headers=headers,
    )
    liqs = client.post(
        "/api/v1/liquidaciones/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "ambos"},
        headers=headers,
    ).json()
    liq_t = {liq["tipo"]: liq for liq in liqs}["transportador"]
    # valor_transporte = 100 L × $100 = 10.000; anticipo 5.000; saldo 5.000
    assert float(liq_t["valor_total"]) == 100 * 100
    assert float(liq_t["anticipos"]) == 5000
    assert float(liq_t["saldo"]) == 100 * 100 - 5000


def test_no_permite_duplicar_dia_proveedor(client, base_datos):
    headers = auth_headers(client, "admin.a")
    _, _, proveedor = _setup_leche(client, headers)
    payload = {"fecha": "2026-06-02", "proveedor_id": proveedor["id"], "cantidad_litros": "50"}
    assert client.post("/api/v1/recepciones", json=payload, headers=headers).status_code == 201
    assert client.post("/api/v1/recepciones", json=payload, headers=headers).status_code == 409


def test_grilla_quincena(client, base_datos):
    headers = auth_headers(client, "admin.a")
    _, transportador, proveedor = _setup_leche(client, headers)

    for dia, litros in (("2026-06-01", "100"), ("2026-06-03", "150")):
        client.post(
            "/api/v1/recepciones",
            json={
                "fecha": dia,
                "proveedor_id": proveedor["id"],
                "transportador_id": transportador["id"],
                "cantidad_litros": litros,
            },
            headers=headers,
        )

    r = client.get(
        "/api/v1/recepciones/grilla/quincena?desde=2026-06-01&hasta=2026-06-15", headers=headers
    )
    assert r.status_code == 200, r.text
    grilla = r.json()
    assert len(grilla["fechas"]) == 15
    # El proveedor aparece aunque solo tenga 2 días con registro
    fila = next(f for f in grilla["filas"] if f["proveedor_id"] == proveedor["id"])
    assert float(fila["total_litros"]) == 250
    assert fila["celdas"]["2026-06-01"]["litros"] == "100.00"
    assert fila["celdas"]["2026-06-01"]["liquidada"] is False
    assert "2026-06-02" not in fila["celdas"]
    assert float(grilla["totales_dia"]["2026-06-03"]) == 150
    assert float(grilla["total_litros"]) == 250


def test_liquidacion_quincena_con_anticipo(client, base_datos):
    headers = auth_headers(client, "admin.a")
    _, transportador, proveedor = _setup_leche(client, headers)

    for dia, litros in (("2026-06-01", "100"), ("2026-06-02", "150")):
        client.post(
            "/api/v1/recepciones",
            json={
                "fecha": dia,
                "proveedor_id": proveedor["id"],
                "transportador_id": transportador["id"],
                "cantidad_litros": litros,
            },
            headers=headers,
        )
    client.post(
        "/api/v1/anticipos",
        json={"proveedor_id": proveedor["id"], "fecha": "2026-06-05", "valor": "100000"},
        headers=headers,
    )

    response = client.post(
        "/api/v1/liquidaciones/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "ambos"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    liquidaciones = response.json()
    por_tipo = {liq["tipo"]: liq for liq in liquidaciones}

    liq_proveedor = por_tipo["proveedor"]
    assert float(liq_proveedor["total_litros"]) == 250
    assert float(liq_proveedor["valor_total"]) == 250 * 1800
    assert float(liq_proveedor["anticipos"]) == 100000
    assert float(liq_proveedor["saldo"]) == 250 * 1800 - 100000
    assert len(liq_proveedor["detalles"]) == 2

    liq_transporte = por_tipo["transportador"]
    assert float(liq_transporte["valor_total"]) == 250 * 100

    # Re-generar el mismo período no duplica liquidaciones
    repetido = client.post(
        "/api/v1/liquidaciones/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "ambos"},
        headers=headers,
    )
    assert repetido.json() == []

    # Flujo de estados y PDF
    liq_id = liq_proveedor["id"]
    assert client.post(f"/api/v1/liquidaciones/{liq_id}/aprobar", headers=headers).status_code == 200
    assert client.post(f"/api/v1/liquidaciones/{liq_id}/pagar", headers=headers).status_code == 200
    pdf = client.get(f"/api/v1/liquidaciones/{liq_id}/pdf", headers=headers)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"

    # Una recepción liquidada no se puede editar
    recepciones = client.get("/api/v1/recepciones", headers=headers).json()["items"]
    bloqueada = client.put(
        f"/api/v1/recepciones/{recepciones[0]['id']}",
        json={"cantidad_litros": "999"},
        headers=headers,
    )
    assert bloqueada.status_code == 422
