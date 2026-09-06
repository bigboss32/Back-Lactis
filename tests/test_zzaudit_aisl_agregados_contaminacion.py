"""¿LA PLATA DE A LE MUEVE LAS CIFRAS A B?

Metodo: se llena la quesera B, se FOTOGRAFIAN todas sus tarjetas y agregados;
despues se llena la quesera A con cifras distintas y se vuelven a fotografiar
las de B. Si una sola cifra de B cambio, la subconsulta o el agregado no filtra
empresa. No hay que sumar a mano: la cifra de B tiene que ser identica.
"""
import json

from tests.conftest import auth_headers

V = "/api/v1"


def _llenar(client, h, marca, litros, precio, kilos, precio_kilo, valor_gasto):
    p = lambda u, b: client.post(f"{V}{u}", json=b, headers=h)  # noqa: E731
    ruta = p("/rutas", {"nombre": f"Ruta{marca}", "municipio": "Calamar"}).json()
    prov = p("/proveedores", {
        "nombre": f"Productor{marca}", "vereda": "El Retorno", "precio_litro": precio,
        "ruta_id": ruta["id"],
    }).json()
    trans = p("/transportadores", {
        "nombre": f"Transportador{marca}", "valor_transporte": "242.76",
        "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "242.76"}],
    }).json()
    for dia, lit in zip(("2026-07-01", "2026-07-02", "2026-07-03"), litros):
        p("/recepciones", {
            "fecha": dia, "proveedor_id": prov["id"], "transportador_id": trans["id"],
            "cantidad_litros": lit, "precio_litro": precio,
        })
    tipo = p("/tipos-queso", {"nombre": f"Queso{marca}"}).json()
    p("/produccion", {
        "fecha": "2026-07-03", "tipo_queso_id": tipo["id"], "peso_kg": kilos,
        "litros_usados": "380.44", "cantidad": "5",
    })
    cliente = p("/clientes", {"nombre": f"Cliente{marca}"}).json()
    prod = p("/inventario/productos", {
        "nombre": f"Producto{marca}", "categoria": "insumo", "unidad": "kg",
        "costo_unitario": "4400.23",
    }).json()
    p("/inventario/movimientos", {
        "producto_id": prod["id"], "fecha": "2026-07-02", "tipo": "entrada",
        "cantidad": "137.45", "costo_unitario": "4400.23",
    })
    p("/ventas", {
        "cliente_id": cliente["id"], "fecha": "2026-07-04",
        "detalles": [{"producto_id": prod["id"], "cantidad": kilos, "precio_unitario": precio_kilo}],
        "descontar_inventario": False,
    })
    cat = p("/categorias-gasto", {"nombre": f"Categoria{marca}"}).json()
    p("/gastos", {
        "fecha": "2026-07-05", "categoria_id": cat["id"], "concepto": f"Gasto{marca}",
        "valor": valor_gasto,
    })
    caja = p("/caja/abrir", {"fecha": "2026-07-01", "saldo_inicial": "500000"}).json()
    p("/caja/movimientos", {
        "caja_id": caja["id"], "tipo": "entrada", "concepto": f"Caja{marca}", "valor": "137450.00",
    })
    cuenta = p("/bancos/cuentas", {
        "banco": f"Banco{marca}", "numero_cuenta": f"111{marca}", "saldo_inicial": "1000000",
    }).json()
    p("/bancos/movimientos", {
        "cuenta_id": cuenta["id"], "fecha": "2026-07-06", "tipo": "ingreso",
        "valor": "1833330.00", "concepto": f"Mov{marca}",
    })
    emp = p("/empleados", {"nombre": f"Emp{marca}", "apellido": "Lo", "valor_dia": "55000"}).json()
    p("/nomina", {
        "empleado_id": emp["id"], "fecha": "2026-07-15", "dias_trabajados": "15", "valor_dia": "55000",
    })
    p("/anticipos", {
        "tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-07-02", "valor": "242760.55",
    })
    veh = p("/transporte/vehiculos", {
        "placa": f"{marca}111"[:6], "nombre": f"Camion{marca}", "tarifa_kilo": "242.76",
    }).json()
    viaje = p("/transporte/viajes", {
        "vehiculo_id": veh["id"], "fecha_salida": "2026-07-02", "origen": "SanJose",
        "destino": "Bogota", "conductor_nombre": f"Conductor{marca}", "pago_conductor": "242760",
    }).json()
    p(f"/transporte/viajes/{viaje['id']}/servicios", {
        "cliente_id": cliente["id"], "descripcion": f"Flete{marca}",
        "kilos": "44.23", "valor_total": "1234567.89",
    })
    p("/transporte/gastos", {
        "fecha": "2026-07-02", "categoria": "combustible", "concepto": f"ACPM{marca}",
        "valor": "242760.50", "vehiculo_id": veh["id"],
    })
    p("/reventa/compras", {
        "fecha": "2026-07-02", "productor": f"ProductorRev{marca}",
        "kilos_brutos": kilos, "precio_kilo": precio_kilo,
    })
    p("/reventa/ventas", {
        "fecha": "2026-07-03", "cliente": f"ClienteRev{marca}",
        "kilos": "22.11", "precio_kilo": "15000",
    })
    p("/reventa/saldos-anteriores", {
        "tipo": "cobrar", "tercero": f"Tercero{marca}", "fecha": "2026-06-30",
        "concepto": f"Saldo{marca}", "valor_total": "1833330.00",
    })
    p("/liquidaciones/generar", {
        "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "ambos",
    })
    p("/notificaciones/generar-alertas", {})
    return {"prov": prov, "trans": trans, "cliente": cliente, "producto": prod, "vehiculo": veh}


AGREGADOS = [
    ("/reportes/dashboard", {}),
    ("/contabilidad/libro-diario", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/contabilidad/estado-resultados", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/contabilidad/balance", {"fecha": "2026-07-31"}),
    ("/recepciones/resumen/periodo", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/recepciones/grilla/quincena", {"desde": "2026-07-01", "hasta": "2026-07-15"}),
    ("/inventario/productos/stock/actual", {}),
    ("/produccion/lotes", {}),
    ("/produccion/ciclos/propuesta", {}),
    ("/ventas/cartera", {}),
    ("/ventas/conductores", {}),
    ("/anticipos/totales/suma", {}),
    ("/liquidaciones", {"page_size": 100}),
    ("/transporte/cartera", {}),
    ("/transporte/resumen-mensual", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/transporte/alertas", {}),
    ("/reventa/resumen", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/reventa/ganancia-por-dia", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
    ("/reventa/lotes", {}),
    ("/reventa/sugerencias", {}),
    ("/nomina", {"page_size": 100}),
    ("/caja", {}),
    ("/bancos/cuentas", {}),
]


def _plano(valor):
    texto = f"{valor:f}"
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto or "0"


def _norm(nodo):
    """Normaliza las cifras: '0' y '0.00' son la MISMA plata. Lo que se busca
    aqui es contaminacion entre queseras, no como se escribe un cero."""
    from decimal import Decimal, InvalidOperation
    if isinstance(nodo, dict):
        return {k: _norm(v) for k, v in nodo.items()}
    if isinstance(nodo, list):
        return [_norm(v) for v in nodo]
    if isinstance(nodo, str):
        try:
            return "#" + _plano(Decimal(nodo))
        except (InvalidOperation, ValueError):
            return nodo
    if isinstance(nodo, (int, float)) and not isinstance(nodo, bool):
        return "#" + _plano(Decimal(str(nodo)))
    return nodo


def _foto(client, h):
    foto = {}
    for url, params in AGREGADOS:
        r = client.get(f"{V}{url}", params=params, headers=h)
        try:
            cuerpo = json.dumps(_norm(r.json()), sort_keys=True)
        except Exception:
            cuerpo = r.text
        foto[url] = (r.status_code, cuerpo)
    return foto


def test_la_plata_de_a_no_le_mueve_una_cifra_a_b(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    # 1. Solo B tiene datos
    _llenar(client, hb, "DEB", ("137.45", "144.23", "98.76"), "1700.00", "44.23", "15000", "242760.75")
    antes = _foto(client, hb)

    # 2. Ahora A tambien, con OTRAS cifras
    _llenar(client, ha, "DEA", ("311.11", "222.22", "444.44"), "1833.33", "88.88", "19999", "999999.99")
    despues = _foto(client, hb)

    print("\n===== CIFRAS DE B, ANTES Y DESPUES DE QUE A CARGARA SU PLATA =====")
    movidas = []
    for url, _ in AGREGADOS:
        a_code, a_body = antes[url]
        d_code, d_body = despues[url]
        if a_code != d_code or a_body != d_body:
            movidas.append(url)
            print(f"!! CAMBIO {url}")
            import difflib as _d
            ja, jd = json.loads(a_body), json.loads(d_body)
            la = json.dumps(ja, indent=1, sort_keys=True).splitlines()
            ld = json.dumps(jd, indent=1, sort_keys=True).splitlines()
            for linea in _d.unified_diff(la, ld, "antes", "despues", n=2, lineterm=""):
                print("   ", linea)
        else:
            print(f"   igual  {url}  ({a_code})")
    print("\nAGREGADOS DE B CONTAMINADOS POR A:", json.dumps(movidas))
    assert not movidas, f"la plata de A le movio cifras a B en: {movidas}"


def test_al_reves_la_plata_de_b_no_le_mueve_una_cifra_a_a(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    _llenar(client, ha, "DEA", ("311.11", "222.22", "444.44"), "1833.33", "88.88", "19999", "999999.99")
    antes = _foto(client, ha)
    _llenar(client, hb, "DEB", ("137.45", "144.23", "98.76"), "1700.00", "44.23", "15000", "242760.75")
    despues = _foto(client, ha)

    movidas = [u for u, _ in AGREGADOS if antes[u] != despues[u]]
    print("\nAGREGADOS DE A CONTAMINADOS POR B:", json.dumps(movidas))
    for u in movidas:
        print("!!", u)
        print("   antes :", antes[u][1][:400])
        print("   despues:", despues[u][1][:400])
    assert not movidas, f"la plata de B le movio cifras a A en: {movidas}"
