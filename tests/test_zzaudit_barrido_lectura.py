"""BARRIDO DE LECTURA: la quesera B esta VACIA; A esta llena de plata.

Si algun GET de B trae un nombre de A o una cifra distinta de cero, es fuga.
Se recorren listados, filtros, busquedas, tarjetas de tablero y agregados.
"""
import json

from tests.conftest import auth_headers

V = "/api/v1"
MARCA = "SECRETOA"  # todos los nombres de A lo llevan


def _j(r):
    try:
        return r.json()
    except Exception:
        return None


def mundo_a(client, ha):
    """La quesera A: catalogo + plata, con cifras feas."""
    m = {}
    p = lambda url, body: client.post(f"{V}{url}", json=body, headers=ha)  # noqa: E731
    m["sucursal"] = _j(p("/sucursales", {"nombre": "SucursalSECRETOA"}))
    m["ruta"] = _j(p("/rutas", {"nombre": "RutaSECRETOA", "municipio": "Calamar"}))
    m["proveedor"] = _j(p("/proveedores", {
        "nombre": "ProductorSECRETOA", "vereda": "El Retorno", "precio_litro": "1833.33",
        "ruta_id": m["ruta"]["id"],
    }))
    m["transportador"] = _j(p("/transportadores", {
        "nombre": "TransportadorSECRETOA", "valor_transporte": "242.76",
        "rutas": [{"ruta_id": m["ruta"]["id"], "valor_transporte": "242.76"}],
    }))
    m["tipo_queso"] = _j(p("/tipos-queso", {"nombre": "QuesoSECRETOA", "precio_referencia": "12500"}))
    m["cliente"] = _j(p("/clientes", {"nombre": "ClienteSECRETOA", "documento": "9001"}))
    m["empleado"] = _j(p("/empleados", {
        "nombre": "EmpleadoSECRETOA", "apellido": "Lopez", "valor_dia": "55000", "salario": "1650000",
    }))
    m["categoria"] = _j(p("/categorias-gasto", {"nombre": "CategoriaSECRETOA"}))
    m["producto"] = _j(p("/inventario/productos", {
        "nombre": "ProductoSECRETOA", "categoria": "insumo", "unidad": "kg", "costo_unitario": "4400.23",
    }))
    m["cuenta"] = _j(p("/bancos/cuentas", {
        "banco": "BancoSECRETOA", "numero_cuenta": "111-SECRETOA", "titular": "TitularSECRETOA",
        "saldo_inicial": "1000000",
    }))
    m["caja"] = _j(p("/caja/abrir", {"fecha": "2026-07-01", "saldo_inicial": "500000"}))
    m["vehiculo"] = _j(p("/transporte/vehiculos", {
        "placa": "SECRETOA1", "nombre": "CamionSECRETOA", "tarifa_kilo": "242.76",
    }))
    m["viaje"] = _j(p("/transporte/viajes", {
        "vehiculo_id": m["vehiculo"]["id"], "fecha_salida": "2026-07-02",
        "origen": "SanJoseSECRETOA", "destino": "BogotaSECRETOA", "conductor_nombre": "ConductorSECRETOA",
        "pago_conductor": "242760",
    }))
    m["servicio"] = _j(p(f"/transporte/viajes/{m['viaje']['id']}/servicios", {
        "cliente_id": m["cliente"]["id"], "descripcion": "FleteSECRETOA",
        "kilos": "44.23", "valor_total": "1234567.89",
    }))
    m["vgasto"] = _j(p("/transporte/gastos", {
        "fecha": "2026-07-02", "categoria": "combustible", "concepto": "ACPMSECRETOA",
        "valor": "242760.50", "vehiculo_id": m["vehiculo"]["id"],
    }))
    m["mantenimiento"] = _j(p("/transporte/mantenimientos", {
        "vehiculo_id": m["vehiculo"]["id"], "fecha": "2026-07-02", "tipo": "preventivo",
        "descripcion": "CambioSECRETOA", "valor": "444023.00",
    }))
    m["vdoc"] = _j(p("/transporte/documentos", {
        "vehiculo_id": m["vehiculo"]["id"], "tipo": "soat", "descripcion": "SoatSECRETOA",
        "fecha_vencimiento": "2026-09-01", "valor": "13745.00",
    }))
    for dia, litros in (("2026-07-01", "137.45"), ("2026-07-02", "144.23"), ("2026-07-03", "98.76")):
        p("/recepciones", {
            "fecha": dia, "proveedor_id": m["proveedor"]["id"],
            "transportador_id": m["transportador"]["id"], "cantidad_litros": litros,
            "precio_litro": "1833.33",
        })
    m["produccion"] = _j(p("/produccion", {
        "fecha": "2026-07-03", "tipo_queso_id": m["tipo_queso"]["id"],
        "peso_kg": "44.23", "litros_usados": "380.44", "cantidad": "5",
    }))
    p("/inventario/movimientos", {
        "producto_id": m["producto"]["id"], "fecha": "2026-07-02", "tipo": "entrada",
        "cantidad": "137.45", "costo_unitario": "4400.23", "referencia": "REFSECRETOA",
    })
    m["venta"] = _j(p("/ventas", {
        "cliente_id": m["cliente"]["id"], "fecha": "2026-07-04",
        "detalles": [{"producto_id": m["producto"]["id"], "cantidad": "44.23", "precio_unitario": "12500"}],
        "descontar_inventario": False,
    }))
    m["gasto"] = _j(p("/gastos", {
        "fecha": "2026-07-05", "categoria_id": m["categoria"]["id"], "concepto": "GastoSECRETOA",
        "valor": "242760.75",
    }))
    p("/caja/movimientos", {
        "caja_id": m["caja"]["id"], "tipo": "entrada", "concepto": "CajaSECRETOA", "valor": "137450.00",
    })
    p("/bancos/movimientos", {
        "cuenta_id": m["cuenta"]["id"], "fecha": "2026-07-06", "tipo": "ingreso",
        "valor": "1833330.00", "concepto": "BancoMovSECRETOA",
    })
    m["nomina"] = _j(p("/nomina", {
        "empleado_id": m["empleado"]["id"], "fecha": "2026-07-15", "dias_trabajados": "15",
        "valor_dia": "55000", "periodo": "2026-07-Q1",
    }))
    m["anticipo"] = _j(p("/anticipos", {
        "tipo": "proveedor", "proveedor_id": m["proveedor"]["id"], "fecha": "2026-07-02",
        "valor": "242760.00", "observaciones": "AnticipoSECRETOA",
    }))
    m["compra_rev"] = _j(p("/reventa/compras", {
        "fecha": "2026-07-02", "productor": "ProductorRevSECRETOA", "kilos_brutos": "44.23",
        "precio_kilo": "12500",
    }))
    m["venta_rev"] = _j(p("/reventa/ventas", {
        "fecha": "2026-07-03", "cliente": "ClienteRevSECRETOA", "kilos": "22.11", "precio_kilo": "15000",
    }))
    m["saldo_rev"] = _j(p("/reventa/saldos-anteriores", {
        "tipo": "cobrar", "tercero": "TerceroSECRETOA", "fecha": "2026-06-30",
        "concepto": "SaldoSECRETOA", "valor_total": "1833330.00",
    }))
    m["temporada"] = _j(p("/reventa/temporadas", {
        "nombre": "TemporadaSECRETOA", "fecha_inicio": "2026-07-01",
    }))
    m["liq"] = _j(p("/liquidaciones/generar", {
        "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "ambos",
    }))
    p("/notificaciones/generar-alertas", {})
    return m


GETS = [
    ("/reportes/dashboard", {}),
    ("/contabilidad/libro-diario", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/contabilidad/estado-resultados", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/contabilidad/balance", {"fecha": "2026-07-31"}),
    ("/recepciones", {}),
    ("/recepciones/grilla/quincena", {"desde": "2026-07-01", "hasta": "2026-07-15"}),
    ("/recepciones/resumen/periodo", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/recepciones/filtrar/avanzado", {}),
    ("/proveedores", {}),
    ("/proveedores/filtrar/avanzado", {}),
    ("/transportadores", {}),
    ("/rutas", {}),
    ("/clientes", {}),
    ("/empleados", {}),
    ("/nomina", {}),
    ("/sucursales", {}),
    ("/tipos-queso", {}),
    ("/categorias-gasto", {}),
    ("/gastos", {}),
    ("/gastos/filtrar/avanzado", {}),
    ("/inventario/productos", {}),
    ("/inventario/productos/stock/actual", {}),
    ("/inventario/movimientos", {}),
    ("/produccion", {}),
    ("/produccion/lotes", {}),
    ("/produccion/ciclos", {}),
    ("/produccion/ciclos/propuesta", {}),
    ("/produccion/filtrar/avanzado", {}),
    ("/ventas", {}),
    ("/ventas/cartera", {}),
    ("/ventas/conductores", {}),
    ("/ventas/conductores/sugerencias", {}),
    ("/ventas/conductores/pagos", {}),
    ("/pagos", {}),
    ("/caja", {}),
    ("/bancos/cuentas", {}),
    ("/bancos/movimientos", {}),
    ("/liquidaciones", {}),
    ("/anticipos", {}),
    ("/anticipos/totales/suma", {}),
    ("/transporte/viajes", {}),
    ("/transporte/vehiculos", {}),
    ("/transporte/gastos", {}),
    ("/transporte/gastos/filtrar/avanzado", {}),
    ("/transporte/mantenimientos", {}),
    ("/transporte/mantenimientos/filtrar/avanzado", {}),
    ("/transporte/documentos", {}),
    ("/transporte/documentos/filtrar/avanzado", {}),
    ("/transporte/cartera", {}),
    ("/transporte/cartera/detalle", {}),
    ("/transporte/resumen-mensual", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/transporte/alertas", {}),
    ("/reventa/resumen", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/reventa/estado-cuenta", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/reventa/estado-cuenta-productor", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/reventa/sugerencias", {}),
    ("/reventa/productos", {}),
    ("/reventa/documentos", {}),
    ("/reventa/compras", {}),
    ("/reventa/ventas", {}),
    ("/reventa/conversiones", {}),
    ("/reventa/saldos-anteriores", {}),
    ("/reventa/temporadas", {}),
    ("/reventa/ganancia-por-dia", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/reventa/lotes", {}),
    ("/auditoria", {}),
    ("/auditoria/logins", {}),
    ("/notificaciones", {}),
    ("/suscripcion", {}),
    ("/usuarios", {}),
    ("/empresas", {}),
]


def test_barrido_de_lectura_b_vacia(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    A = mundo_a(client, ha)
    faltantes = [k for k, v in A.items() if not v or (isinstance(v, dict) and "id" not in v and "generadas" not in v)]
    print("piezas de A que no se crearon:", faltantes)

    fugas = []
    print("\n===== BARRIDO GET COMO EMPRESA B (vacia) =====")
    for url, params in GETS:
        r = client.get(f"{V}{url}", params=params, headers=hb)
        cuerpo = r.text
        if r.status_code >= 400:
            print(f"   {r.status_code} {url}  {cuerpo[:120]}")
            continue
        if MARCA in cuerpo:
            trozos = sorted({
                w for w in cuerpo.replace('"', " ").replace(",", " ").split() if MARCA in w
            })
            print(f"!! FUGA {url} -> {trozos[:6]}")
            fugas.append((url, trozos[:6]))
        else:
            print(f"   ok  {url}")
    print("\nFUGAS DE NOMBRE:", json.dumps(fugas, ensure_ascii=False))


# Campos numericos que NO son plata ni conteos de negocio (paginacion, etc.)
IGNORAR = {
    "page", "page_size", "pages", "dias_restantes", "dias_aviso", "dias_gracia",
    "dias_prueba", "anio", "mes", "orden", "dias", "precio_referencia",
    "valor_dia", "salario", "stock_minimo", "costo_unitario", "precio_litro",
    "valor_transporte", "tarifa_kilo", "saldo_inicial",
}


def _numeros_no_cero(nodo, ruta="", acc=None):
    if acc is None:
        acc = []
    if isinstance(nodo, dict):
        for k, v in nodo.items():
            if k in IGNORAR:
                continue
            _numeros_no_cero(v, f"{ruta}.{k}", acc)
    elif isinstance(nodo, list):
        for i, v in enumerate(nodo):
            _numeros_no_cero(v, f"{ruta}[{i}]", acc)
    elif isinstance(nodo, bool) or nodo is None:
        pass
    elif isinstance(nodo, (int, float)):
        if nodo != 0:
            acc.append((ruta, nodo))
    elif isinstance(nodo, str):
        try:
            valor = float(nodo)
        except ValueError:
            return acc
        if valor != 0:
            acc.append((ruta, nodo))
    return acc


def test_barrido_de_cifras_b_vacia(client, base_datos):
    """Ninguna tarjeta ni agregado de B puede traer una cifra: B no tiene nada."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    mundo_a(client, ha)

    sospechosos = []
    print("\n===== BARRIDO DE CIFRAS COMO EMPRESA B (vacia) =====")
    for url, params in GETS:
        r = client.get(f"{V}{url}", params=params, headers=hb)
        if r.status_code >= 400:
            continue
        cuerpo = _j(r)
        if cuerpo is None:
            continue
        malos = _numeros_no_cero(cuerpo)
        # el propio usuario/empresa de B aparecen en /usuarios y /empresas
        if url in ("/usuarios", "/empresas"):
            continue
        if malos:
            print(f"!! {url} -> {malos[:10]}")
            sospechosos.append((url, malos[:10]))
        else:
            print(f"   ok  {url}")
    print("\nCIFRAS SOSPECHOSAS:", sospechosos)
