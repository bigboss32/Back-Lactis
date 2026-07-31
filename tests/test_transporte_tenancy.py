"""El aislamiento multi-tenant del módulo de transporte: la turbo, sus viajes,
su cartera y sus alertas son de UNA empresa; la otra no ve ni un peso, ni
puede colarse por los endpoints de escritura o los reportes."""
from datetime import date, timedelta

from tests.conftest import auth_headers

API = "/api/v1/transporte"


def _montar_operacion(client, headers):
    """Vehículo + viaje con un flete a crédito, un gasto y un SOAT por vencer:
    lo mínimo para que todos los reportes tengan números."""
    r = client.post(
        f"{API}/vehiculos",
        json={"placa": "ABC123", "nombre": "La Turbo", "tarifa_kilo": "1200"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    vehiculo = r.json()

    r = client.post("/api/v1/clientes", json={"nombre": "Alba Ricaute"}, headers=headers)
    assert r.status_code == 201, r.text
    cliente = r.json()

    r = client.post(
        f"{API}/viajes",
        json={"vehiculo_id": vehiculo["id"], "fecha_salida": "2026-07-10",
              "origen": "San José del Guaviare", "destino": "Villavicencio"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    viaje = r.json()

    r = client.post(
        f"{API}/viajes/{viaje['id']}/servicios",
        json={"descripcion": "Carga de queso", "cliente_id": cliente["id"], "kilos": "38"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    servicio = r.json()

    r = client.post(
        f"{API}/viajes/{viaje['id']}/gastos",
        json={"fecha": "2026-07-10", "categoria": "combustible", "valor": "80000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    gasto = r.json()

    r = client.post(
        f"{API}/documentos",
        json={"vehiculo_id": vehiculo["id"], "tipo": "soat",
              "fecha_vencimiento": (date.today() + timedelta(days=10)).isoformat()},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return {"vehiculo": vehiculo, "cliente": cliente, "viaje": viaje,
            "servicio": servicio, "gasto": gasto}


def test_empresa_no_ve_el_transporte_de_otra(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")
    datos = _montar_operacion(client, headers_a)

    # Las listas de B están vacías
    for recurso in ("vehiculos", "viajes", "gastos", "mantenimientos", "documentos"):
        assert client.get(f"{API}/{recurso}", headers=headers_b).json()["total"] == 0

    # Y los registros de A no existen para B
    assert client.get(f"{API}/viajes/{datos['viaje']['id']}", headers=headers_b).status_code == 404
    assert client.get(
        f"{API}/vehiculos/{datos['vehiculo']['id']}", headers=headers_b
    ).status_code == 404
    assert client.get(f"{API}/gastos/{datos['gasto']['id']}", headers=headers_b).status_code == 404

    # A sí ve lo suyo
    assert client.get(f"{API}/viajes", headers=headers_a).json()["total"] == 1


def test_empresa_no_puede_tocar_operacion_ajena(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")
    datos = _montar_operacion(client, headers_a)
    viaje_id = datos["viaje"]["id"]
    servicio_id = datos["servicio"]["id"]

    assert client.put(
        f"{API}/viajes/{viaje_id}", json={"origen": "Hackeado"}, headers=headers_b
    ).status_code == 404
    assert client.post(
        f"{API}/viajes/{viaje_id}/finalizar", json={}, headers=headers_b
    ).status_code == 404
    assert client.post(
        f"{API}/viajes/{viaje_id}/servicios",
        json={"descripcion": "Colado", "tipo_cobro": "precio_fijo",
              "cliente_nombre": "X Y", "valor_total": "1000", "pagado_de_contado": True},
        headers=headers_b,
    ).status_code == 404
    # Ni abonarle plata al servicio de otra empresa
    assert client.post(
        f"{API}/servicios/{servicio_id}/abonos",
        json={"fecha": "2026-07-12", "valor": "1000"},
        headers=headers_b,
    ).status_code == 404
    assert client.delete(f"{API}/viajes/{viaje_id}", headers=headers_b).status_code == 404

    # El viaje de A quedó intacto
    detalle = client.get(f"{API}/viajes/{viaje_id}", headers=headers_a).json()
    assert detalle["origen"] == "San José del Guaviare"
    assert len(detalle["servicios"]) == 1
    assert float(detalle["saldo_cartera"]) == 45_600


def test_cliente_y_vehiculo_de_otra_empresa_no_sirven(client, base_datos):
    """Las FK se validan DENTRO del tenant: usar el id de un cliente o un
    vehículo ajeno no puede colar (ni siquiera filtrar que existe)."""
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")
    datos = _montar_operacion(client, headers_a)

    # La placa de A no estorba en B: la restricción es por empresa
    r = client.post(
        f"{API}/vehiculos", json={"placa": "ABC123", "tarifa_kilo": "1000"}, headers=headers_b
    )
    assert r.status_code == 201, r.text
    vehiculo_b = r.json()
    r = client.post(
        f"{API}/viajes",
        json={"vehiculo_id": vehiculo_b["id"], "fecha_salida": "2026-07-10",
              "origen": "Calamar", "destino": "Villavicencio"},
        headers=headers_b,
    )
    assert r.status_code == 201, r.text
    viaje_b = r.json()

    # El cliente del directorio de A no existe para un flete de B
    r = client.post(
        f"{API}/viajes/{viaje_b['id']}/servicios",
        json={"descripcion": "Carga", "cliente_id": datos["cliente"]["id"], "kilos": "10"},
        headers=headers_b,
    )
    assert r.status_code == 404

    # Y el vehículo de A tampoco recibe viajes ni gastos de B
    r = client.post(
        f"{API}/viajes",
        json={"vehiculo_id": datos["vehiculo"]["id"], "fecha_salida": "2026-07-10",
              "origen": "Calamar", "destino": "Villavicencio"},
        headers=headers_b,
    )
    assert r.status_code == 404
    r = client.post(
        f"{API}/gastos",
        json={"vehiculo_id": datos["vehiculo"]["id"], "fecha": "2026-07-10",
              "categoria": "combustible", "valor": "1000"},
        headers=headers_b,
    )
    assert r.status_code == 404


def test_reportes_de_transporte_no_mezclan_empresas(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")
    _montar_operacion(client, headers_a)

    rango = "desde=2026-07-01&hasta=2026-07-31"
    resumen_b = client.get(f"{API}/resumen-mensual?{rango}", headers=headers_b).json()
    assert resumen_b["viajes_realizados"] == 0
    assert float(resumen_b["total_ingresos"]) == 0
    assert float(resumen_b["total_gastos"]) == 0
    assert float(resumen_b["por_cobrar"]) == 0
    assert client.get(f"{API}/cartera", headers=headers_b).json() == []
    alertas_b = client.get(f"{API}/alertas", headers=headers_b).json()
    assert alertas_b == {"documentos": [], "mantenimientos": []}

    # Los números de A siguen siendo de A
    resumen_a = client.get(f"{API}/resumen-mensual?{rango}", headers=headers_a).json()
    assert float(resumen_a["por_cobrar"]) == 45_600
    assert len(client.get(f"{API}/alertas", headers=headers_a).json()["documentos"]) == 1


def test_superadmin_opera_transporte_por_empresa_con_header(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    _montar_operacion(client, headers_a)

    headers_super = auth_headers(client, "superadmin")
    empresa_a_id = str(base_datos["empresa_a"].id)
    empresa_b_id = str(base_datos["empresa_b"].id)

    con_header = client.get(
        f"{API}/viajes", headers={**headers_super, "X-Empresa-Id": empresa_a_id}
    ).json()
    assert con_header["total"] == 1

    otra = client.get(
        f"{API}/viajes", headers={**headers_super, "X-Empresa-Id": empresa_b_id}
    ).json()
    assert otra["total"] == 0

    # Sin decir la empresa no hay contexto: se rechaza en vez de mezclar
    r = client.get(f"{API}/viajes", headers=headers_super)
    assert r.status_code == 422
    assert "X-Empresa-Id" in r.json()["error"]["detail"]
