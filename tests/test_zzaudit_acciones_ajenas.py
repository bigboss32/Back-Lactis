"""ACCIONES CON IDS DE LA OTRA QUESERA: aprobar, pagar, anular, abonar, borrar.

Las sub-rutas (abonos, pagos, detalles, servicios, adjuntos) son las que se
saltan el repositorio con filtro de empresa. Aca se golpean todas.
"""
from tests.conftest import auth_headers

V = "/api/v1"


def _r(out, tag, r):
    out.append((tag, r.status_code, r.text[:160]))
    return r


def _mundo_a_con_plata(client, ha):
    m = {}
    p = lambda u, b: client.post(f"{V}{u}", json=b, headers=ha)  # noqa: E731
    m["ruta"] = p("/rutas", {"nombre": "RutaA", "municipio": "Calamar"}).json()
    m["prov"] = p("/proveedores", {
        "nombre": "ProductorA", "vereda": "V", "precio_litro": "1833.33", "ruta_id": m["ruta"]["id"],
    }).json()
    m["trans"] = p("/transportadores", {
        "nombre": "TransportadorA", "valor_transporte": "242.76",
        "rutas": [{"ruta_id": m["ruta"]["id"], "valor_transporte": "242.76"}],
    }).json()
    for dia, litros in (("2026-07-01", "137.45"), ("2026-07-02", "144.23")):
        p("/recepciones", {
            "fecha": dia, "proveedor_id": m["prov"]["id"], "transportador_id": m["trans"]["id"],
            "cantidad_litros": litros, "precio_litro": "1833.33",
        })
    gen = p("/liquidaciones/generar", {
        "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "ambos",
    }).json()
    m["liqs"] = gen["generadas"]
    liq_id = m["liqs"][0]["id"]
    m["liq_id"] = liq_id
    detalle = client.get(f"{V}/liquidaciones/{liq_id}", headers=ha).json()
    m["liq"] = detalle
    m["detalle_id"] = (detalle.get("detalles") or [{}])[0].get("id")
    client.post(f"{V}/liquidaciones/{liq_id}/aprobar", json={}, headers=ha)
    pago = p(f"/liquidaciones/{liq_id}/pagos", {
        "fecha": "2026-07-16", "valor": "1000.00", "metodo": "efectivo",
    })
    m["pago"] = pago.json() if pago.status_code < 400 else None

    m["cliente"] = p("/clientes", {"nombre": "ClienteA"}).json()
    m["vehiculo"] = p("/transporte/vehiculos", {"placa": "AAA111", "nombre": "CamionA", "tarifa_kilo": "242.76"}).json()
    _v = p("/transporte/viajes", {
        "vehiculo_id": m["vehiculo"]["id"], "fecha_salida": "2026-07-02", "origen": "SanJose", "destino": "Bogota",
    })
    print("crear viaje A ->", _v.status_code, _v.text[:200])
    m["viaje"] = _v.json()
    serv = p(f"/transporte/viajes/{m['viaje']['id']}/servicios", {
        "cliente_id": m["cliente"]["id"], "descripcion": "FleteA",
        "kilos": "44.23", "valor_total": "1234567.89",
    })
    print("crear servicio A ->", serv.status_code, serv.text[:250])
    m["servicio"] = serv.json() if serv.status_code < 400 else None
    if m["servicio"]:
        ab = p(f"/transporte/servicios/{m['servicio']['id']}/abonos", {
            "fecha": "2026-07-05", "valor": "242.76",
        })
        m["abono_servicio"] = ab.json() if ab.status_code < 400 else None

    m["compra_rev"] = p("/reventa/compras", {
        "fecha": "2026-07-02", "productor": "ProductorRevA", "kilos_brutos": "44.23", "precio_kilo": "12500",
    }).json()
    ab = p(f"/reventa/compras/{m['compra_rev']['id']}/abonos", {"fecha": "2026-07-03", "valor": "242.76"})
    m["abono_compra"] = ab.json() if ab.status_code < 400 else None

    m["saldo_rev"] = p("/reventa/saldos-anteriores", {
        "tipo": "cobrar", "tercero": "TerceroA", "fecha": "2026-06-30",
        "concepto": "SaldoA", "valor_total": "1833330.00",
    }).json()
    m["temporada"] = p("/reventa/temporadas", {"nombre": "TemporadaA", "fecha_inicio": "2026-07-01"}).json()
    m["anticipo"] = p("/anticipos", {
        "tipo": "proveedor", "proveedor_id": m["prov"]["id"], "fecha": "2026-07-02", "valor": "242.76",
    }).json()
    m["caja"] = p("/caja/abrir", {"fecha": "2026-07-01", "saldo_inicial": "500000"}).json()
    m["cuenta"] = p("/bancos/cuentas", {
        "banco": "BancoA", "numero_cuenta": "111", "saldo_inicial": "1000000",
    }).json()
    m["mov_banco"] = p("/bancos/movimientos", {
        "cuenta_id": m["cuenta"]["id"], "fecha": "2026-07-06", "tipo": "ingreso",
        "valor": "1833330.00", "concepto": "MovA",
    }).json()
    m["nomina"] = p("/nomina", {
        "empleado_id": p("/empleados", {"nombre": "EmpA", "apellido": "Lo", "valor_dia": "55000"}).json()["id"],
        "fecha": "2026-07-15", "dias_trabajados": "15", "valor_dia": "55000",
    }).json()
    return m


def test_acciones_con_ids_ajenos(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    A = _mundo_a_con_plata(client, ha)
    out = []

    liq = A["liq_id"]
    _r(out, "GET liquidacion ajena", client.get(f"{V}/liquidaciones/{liq}", headers=hb))
    _r(out, "GET pdf liquidacion ajena", client.get(f"{V}/liquidaciones/{liq}/pdf", headers=hb))
    _r(out, "POST aprobar liquidacion ajena", client.post(f"{V}/liquidaciones/{liq}/aprobar", json={}, headers=hb))
    _r(out, "POST pagar liquidacion ajena", client.post(f"{V}/liquidaciones/{liq}/pagar", json={"fecha": "2026-07-20"}, headers=hb))
    _r(out, "POST recalcular liquidacion ajena", client.post(f"{V}/liquidaciones/{liq}/recalcular", json={}, headers=hb))
    _r(out, "POST anular liquidacion ajena", client.post(f"{V}/liquidaciones/{liq}/anular", json={}, headers=hb))
    _r(out, "PUT liquidacion ajena", client.put(f"{V}/liquidaciones/{liq}", json={"observaciones": "hackeado"}, headers=hb))
    if A["detalle_id"]:
        _r(out, "PUT detalle de liquidacion ajena",
           client.put(f"{V}/liquidaciones/{liq}/detalles/{A['detalle_id']}",
                      json={"precio_litro": "1.00"}, headers=hb))
    _r(out, "POST pago en liquidacion ajena",
       client.post(f"{V}/liquidaciones/{liq}/pagos", json={"fecha": "2026-07-20", "valor": "100"}, headers=hb))
    if A.get("pago"):
        _r(out, "DELETE pago de liquidacion ajena",
           client.delete(f"{V}/liquidaciones/{liq}/pagos/{A['pago']['id']}", headers=hb))

    if A.get("servicio"):
        s = A["servicio"]["id"]
        vj = A["viaje"]["id"]
        _r(out, "POST abono a servicio de transporte ajeno",
           client.post(f"{V}/transporte/servicios/{s}/abonos", json={"fecha": "2026-07-06", "valor": "242.76"}, headers=hb))
        if A.get("abono_servicio"):
            ab = A["abono_servicio"]
            ab_id = ab.get("id") or (ab.get("abonos") or [{}])[-1].get("id")
            if ab_id:
                _r(out, "DELETE abono de servicio ajeno",
                   client.delete(f"{V}/transporte/servicios/{s}/abonos/{ab_id}", headers=hb))
        _r(out, "PUT servicio en viaje ajeno",
           client.put(f"{V}/transporte/viajes/{vj}/servicios/{s}", json={"valor_total": "1.00"}, headers=hb))
        _r(out, "POST anular servicio ajeno",
           client.post(f"{V}/transporte/viajes/{vj}/servicios/{s}/anular", json={}, headers=hb))
        _r(out, "DELETE servicio ajeno",
           client.delete(f"{V}/transporte/viajes/{vj}/servicios/{s}", headers=hb))
    vj = A["viaje"]["id"]
    _r(out, "POST finalizar viaje ajeno", client.post(f"{V}/transporte/viajes/{vj}/finalizar", json={}, headers=hb))
    _r(out, "POST anular viaje ajeno", client.post(f"{V}/transporte/viajes/{vj}/anular", json={}, headers=hb))
    _r(out, "GET gastos de viaje ajeno", client.get(f"{V}/transporte/viajes/{vj}/gastos", headers=hb))

    c = A["compra_rev"]["id"]
    _r(out, "POST abono a compra reventa ajena",
       client.post(f"{V}/reventa/compras/{c}/abonos", json={"fecha": "2026-07-04", "valor": "242.76"}, headers=hb))
    if A.get("abono_compra"):
        abc = A["abono_compra"]
        ab_id = abc.get("id") or (abc.get("abonos") or [{}])[-1].get("id")
        if ab_id:
            _r(out, "DELETE abono de compra reventa ajena",
               client.delete(f"{V}/reventa/compras/{c}/abonos/{ab_id}", headers=hb))
    _r(out, "POST anular compra reventa ajena",
       client.post(f"{V}/reventa/compras/{c}/anular", json={}, headers=hb))
    _r(out, "GET adjuntos de compra reventa ajena", client.get(f"{V}/reventa/compras/{c}/adjuntos", headers=hb))

    s = A["saldo_rev"]["id"]
    _r(out, "POST abono a saldo anterior ajeno",
       client.post(f"{V}/reventa/saldos-anteriores/{s}/abonos", json={"fecha": "2026-07-04", "valor": "242.76"}, headers=hb))
    _r(out, "POST anular saldo anterior ajeno",
       client.post(f"{V}/reventa/saldos-anteriores/{s}/anular", json={}, headers=hb))

    t = A["temporada"]["id"]
    _r(out, "POST cerrar temporada ajena", client.post(f"{V}/reventa/temporadas/{t}/cerrar", json={}, headers=hb))
    _r(out, "POST reabrir temporada ajena", client.post(f"{V}/reventa/temporadas/{t}/reabrir", json={}, headers=hb))

    _r(out, "PUT anticipo ajeno", client.put(f"{V}/anticipos/{A['anticipo']['id']}", json={"valor": "1.00"}, headers=hb))
    _r(out, "DELETE anticipo ajeno", client.delete(f"{V}/anticipos/{A['anticipo']['id']}", headers=hb))

    _r(out, "POST cerrar caja ajena", client.post(f"{V}/caja/{A['caja']['id']}/cerrar", json={"efectivo_contado": "1.00"}, headers=hb))
    _r(out, "GET saldo de cuenta bancaria ajena", client.get(f"{V}/bancos/cuentas/{A['cuenta']['id']}/saldo", headers=hb))
    _r(out, "POST conciliar movimiento bancario ajeno",
       client.post(f"{V}/bancos/movimientos/conciliar", json={"movimiento_ids": [A["mov_banco"]["id"]]}, headers=hb))

    _r(out, "GET pdf nomina ajena", client.get(f"{V}/nomina/{A['nomina']['id']}/pdf", headers=hb))
    _r(out, "DELETE nomina ajena", client.delete(f"{V}/nomina/{A['nomina']['id']}", headers=hb))

    empresa_a = str(base_datos["empresa_a"].id)
    _r(out, "GET empresa ajena", client.get(f"{V}/empresas/{empresa_a}", headers=hb))
    _r(out, "PUT empresa ajena", client.put(f"{V}/empresas/{empresa_a}", json={"nombre": "Hackeada"}, headers=hb))
    _r(out, "POST REINICIAR empresa ajena", client.post(f"{V}/empresas/{empresa_a}/reiniciar", json={"confirmacion": "REINICIAR"}, headers=hb))
    _r(out, "PUT suscripcion de empresa ajena",
       client.put(f"{V}/empresas/{empresa_a}/suscripcion", json={"exenta": True}, headers=hb))
    _r(out, "DELETE empresa ajena", client.delete(f"{V}/empresas/{empresa_a}", headers=hb))

    print("\n===== ACCIONES CON IDS AJENOS =====")
    pasaron = []
    for tag, code, body in out:
        marca = "  " if code >= 400 else "!!"
        print(f"{marca} {code}  {tag}   {body if code < 400 else body[:90]}")
        if code < 400:
            pasaron.append(tag)
    print("PASARON (deberian haber sido rechazadas):", pasaron)
