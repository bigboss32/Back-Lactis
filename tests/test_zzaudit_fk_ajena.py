"""AUDITORIA DE AISLAMIENTO: llaves foraneas de la OTRA quesera.

Sonda de reconocimiento: empresa B manda ids de empresa A en los POST.
Imprime el resultado de cada puerta; no asume nada.
"""
from tests.conftest import auth_headers

V = "/api/v1"


def _post(client, h, url, body, tag, out):
    r = client.post(f"{V}{url}", json=body, headers=h)
    out.append((tag, r.status_code, r.text[:200]))
    return r


def _mundo_a(client, ha):
    """Todo lo que la quesera A tiene, para que B lo intente usar."""
    m = {}
    m["sucursal"] = client.post(f"{V}/sucursales", json={"nombre": "SucursalA"}, headers=ha).json()
    m["ruta"] = client.post(f"{V}/rutas", json={"nombre": "RutaSecretaDeA", "municipio": "Calamar"}, headers=ha).json()
    m["proveedor"] = client.post(
        f"{V}/proveedores",
        json={"nombre": "ProductorSecretoA", "vereda": "El Retorno", "precio_litro": "1833.33"},
        headers=ha,
    ).json()
    m["transportador"] = client.post(
        f"{V}/transportadores",
        json={"nombre": "TransportadorSecretoA", "valor_transporte": "242.76"},
        headers=ha,
    ).json()
    m["tipo_queso"] = client.post(
        f"{V}/tipos-queso", json={"nombre": "QuesoSecretoA", "precio_referencia": "12500"}, headers=ha
    ).json()
    m["cliente"] = client.post(f"{V}/clientes", json={"nombre": "ClienteSecretoA"}, headers=ha).json()
    m["empleado"] = client.post(
        f"{V}/empleados", json={"nombre": "EmpleadoA", "apellido": "Secreto", "valor_dia": "55000"}, headers=ha
    ).json()
    m["categoria"] = client.post(
        f"{V}/categorias-gasto", json={"nombre": "CategoriaSecretaA"}, headers=ha
    ).json()
    m["producto"] = client.post(
        f"{V}/inventario/productos",
        json={"nombre": "ProductoSecretoA", "categoria": "insumo", "unidad": "kg", "costo_unitario": "4400.23"},
        headers=ha,
    ).json()
    m["cuenta"] = client.post(
        f"{V}/bancos/cuentas",
        json={"banco": "BancoA", "numero_cuenta": "111-A", "saldo_inicial": "1000000"},
        headers=ha,
    ).json()
    m["caja"] = client.post(
        f"{V}/caja/abrir", json={"fecha": "2026-07-01", "saldo_inicial": "500000"}, headers=ha
    ).json()
    m["vehiculo"] = client.post(
        f"{V}/transporte/vehiculos", json={"placa": "AAA111", "nombre": "CamionA"}, headers=ha
    ).json()
    m["viaje"] = client.post(
        f"{V}/transporte/viajes",
        json={"vehiculo_id": m["vehiculo"]["id"], "fecha_salida": "2026-07-02", "origen": "SanJose", "destino": "Bogota"},
        headers=ha,
    ).json()
    m["recepcion"] = client.post(
        f"{V}/recepciones",
        json={
            "fecha": "2026-07-03",
            "proveedor_id": m["proveedor"]["id"],
            "cantidad_litros": "137.45",
            "precio_litro": "1833.33",
        },
        headers=ha,
    ).json()
    m["prod_reventa"] = client.post(
        f"{V}/reventa/productos", json={"nombre": "QuesoReventaA", "unidad": "kg"}, headers=ha
    ).json()
    return m


def test_sonda_fk_ajena(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    A = _mundo_a(client, ha)

    for k, v in A.items():
        assert isinstance(v, dict) and "id" in v, f"NO SE CREO {k}: {v}"

    out = []
    _post(client, hb, "/recepciones", {
        "fecha": "2026-07-03", "proveedor_id": A["proveedor"]["id"], "cantidad_litros": "137.45",
    }, "recepcion.proveedor_id ajeno", out)

    prov_b = client.post(f"{V}/proveedores", json={"nombre": "ProdB", "vereda": "V", "precio_litro": "1700"}, headers=hb).json()
    _post(client, hb, "/recepciones", {
        "fecha": "2026-07-03", "proveedor_id": prov_b["id"], "cantidad_litros": "137.45",
        "transportador_id": A["transportador"]["id"],
    }, "recepcion.transportador_id ajeno", out)
    _post(client, hb, "/recepciones", {
        "fecha": "2026-07-04", "proveedor_id": prov_b["id"], "cantidad_litros": "44.23",
        "ruta_id": A["ruta"]["id"],
    }, "recepcion.ruta_id ajeno", out)
    _post(client, hb, "/recepciones", {
        "fecha": "2026-07-05", "proveedor_id": prov_b["id"], "cantidad_litros": "44.23",
        "sucursal_id": A["sucursal"]["id"],
    }, "recepcion.sucursal_id ajeno", out)

    _post(client, hb, "/proveedores", {
        "nombre": "ProdB2", "vereda": "V", "precio_litro": "1700", "ruta_id": A["ruta"]["id"],
    }, "proveedor.ruta_id ajeno", out)
    _post(client, hb, "/transportadores", {
        "nombre": "TranspB", "valor_transporte": "100",
        "rutas": [{"ruta_id": A["ruta"]["id"], "valor_transporte": "242.76"}],
    }, "transportador.rutas[].ruta_id ajeno", out)

    _post(client, hb, "/produccion", {
        "fecha": "2026-07-06", "tipo_queso_id": A["tipo_queso"]["id"], "peso_kg": "44.23", "litros_usados": "137.45",
    }, "produccion.tipo_queso_id ajeno", out)
    _post(client, hb, "/produccion", {
        "fecha": "2026-07-06", "tipo_queso_id": A["tipo_queso"]["id"], "peso_kg": "44.23",
        "sucursal_id": A["sucursal"]["id"],
    }, "produccion.sucursal_id ajeno", out)

    _post(client, hb, "/inventario/productos", {
        "nombre": "ProdInvB", "tipo_queso_id": A["tipo_queso"]["id"],
    }, "inventario.producto.tipo_queso_id ajeno", out)
    _post(client, hb, "/inventario/movimientos", {
        "producto_id": A["producto"]["id"], "fecha": "2026-07-07", "tipo": "entrada", "cantidad": "44.23",
        "costo_unitario": "4400.23",
    }, "inventario.movimiento.producto_id ajeno", out)

    _post(client, hb, "/ventas", {
        "cliente_id": A["cliente"]["id"], "fecha": "2026-07-08",
        "detalles": [{"producto_id": A["producto"]["id"], "cantidad": "44.23", "precio_unitario": "12500"}],
    }, "venta.cliente_id+producto_id ajenos", out)

    _post(client, hb, "/gastos", {
        "fecha": "2026-07-09", "categoria_id": A["categoria"]["id"], "concepto": "Fuga", "valor": "242.76",
    }, "gasto.categoria_id ajeno", out)

    _post(client, hb, "/caja/movimientos", {
        "caja_id": A["caja"]["id"], "tipo": "entrada", "concepto": "Fuga", "valor": "242.76",
    }, "caja.caja_id ajeno", out)
    _post(client, hb, "/bancos/movimientos", {
        "cuenta_id": A["cuenta"]["id"], "fecha": "2026-07-10", "tipo": "consignacion", "valor": "242.76",
        "concepto": "Fuga",
    }, "banco.cuenta_id ajeno", out)

    _post(client, hb, "/nomina", {
        "empleado_id": A["empleado"]["id"], "fecha": "2026-07-11", "dias_trabajados": "15", "valor_dia": "55000",
    }, "nomina.empleado_id ajeno", out)

    for campo, ident in (
        ("proveedor_id", A["proveedor"]["id"]),
        ("transportador_id", A["transportador"]["id"]),
        ("empleado_id", A["empleado"]["id"]),
    ):
        tipo = {"proveedor_id": "proveedor", "transportador_id": "transportador", "empleado_id": "empleado"}[campo]
        _post(client, hb, "/anticipos", {
            "tipo": tipo, campo: ident, "fecha": "2026-07-12", "valor": "242.76",
        }, f"anticipo.{campo} ajeno", out)

    _post(client, hb, "/transporte/viajes", {
        "vehiculo_id": A["vehiculo"]["id"], "fecha_salida": "2026-07-13", "origen": "X", "destino": "Y",
    }, "viaje.vehiculo_id ajeno", out)
    _post(client, hb, "/transporte/gastos", {
        "fecha": "2026-07-14", "categoria": "combustible", "valor": "242.76", "vehiculo_id": A["vehiculo"]["id"],
    }, "transporte.gasto.vehiculo_id ajeno", out)
    _post(client, hb, "/transporte/documentos", {
        "vehiculo_id": A["vehiculo"]["id"], "tipo": "soat", "fecha_vencimiento": "2027-01-01",
    }, "transporte.documento.vehiculo_id ajeno", out)
    _post(client, hb, "/transporte/mantenimientos", {
        "vehiculo_id": A["vehiculo"]["id"], "fecha": "2026-07-15", "tipo": "preventivo", "valor": "242.76",
        "descripcion": "Fuga",
    }, "transporte.mantenimiento.vehiculo_id ajeno", out)
    viaje_a = A["viaje"]["id"]
    _post(client, hb, f"/transporte/viajes/{viaje_a}/servicios", {
        "cliente": "ClienteB", "kilos": "44.23", "valor": "242.76",
    }, "transporte.servicio en viaje ajeno", out)
    _post(client, hb, f"/transporte/viajes/{viaje_a}/gastos", {
        "fecha": "2026-07-16", "categoria": "peaje", "valor": "242.76",
    }, "transporte.gasto en viaje ajeno", out)

    _post(client, hb, "/reventa/productos", {
        "nombre": "SubB", "subproducto_de_id": A["prod_reventa"]["id"],
    }, "reventa.producto.subproducto_de_id ajeno", out)

    _post(client, hb, "/liquidaciones/previsualizar", {
        "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "proveedor",
        "tercero_id": A["proveedor"]["id"],
    }, "liquidacion.previsualizar tercero ajeno", out)
    _post(client, hb, "/liquidaciones/generar", {
        "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "proveedor",
        "proveedor_id": A["proveedor"]["id"],
    }, "liquidacion.generar proveedor ajeno", out)

    print("\n===== SONDA FK AJENA =====")
    sospechosos = []
    for tag, code, body in out:
        marca = "  " if code >= 400 else "!!"
        print(f"{marca} {code}  {tag}   {body if code < 400 else ''}")
        if code < 400:
            sospechosos.append(tag)
    print("SOSPECHOSOS:", sospechosos)
