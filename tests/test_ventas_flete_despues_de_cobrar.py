"""El flete que se olvidó ponerle a una venta YA COBRADA.

El dueño registra la venta, la cobra, y después se acuerda de que no le puso el
transporte. El transporte es un costo de la quesera: NO se le suma al total que
paga el cliente ni le mueve la cartera, así que corregirlo después de cobrar es
legítimo y no descuadra nada de lo ya recibido.

Lo que sí queda cerrado con pagos encima son los productos y el descuento: eso
cambiaría lo que el cliente debe, y para eso hay que anular y rehacer la venta.

Estas pruebas fijan las dos mitades de la regla, porque el formulario del front
depende de ellas: manda SOLO el flete cuando la venta tiene pagos.
"""
from tests.conftest import auth_headers

API = "/api/v1/ventas"


def _preparar(client, headers):
    """Un queso con 100 kg en bodega y un cliente."""
    producto = client.post(
        "/api/v1/inventario/productos",
        json={
            "nombre": "Queso Doble Crema",
            "categoria": "producto_terminado",
            "unidad": "kg",
            "stock_minimo": "5",
        },
        headers=headers,
    ).json()
    client.post(
        "/api/v1/inventario/movimientos",
        json={
            "producto_id": producto["id"], "fecha": "2026-06-01",
            "tipo": "entrada", "cantidad": "100", "costo_unitario": "12000",
        },
        headers=headers,
    )
    cliente = client.post(
        "/api/v1/clientes", json={"nombre": "Tienda La 33"}, headers=headers
    ).json()
    return producto, cliente


def _venta_cobrada(client, headers, producto, cliente, kilos="20", precio="17000"):
    """Vende y cobra el 100%: queda en estado 'pagada' y sin flete."""
    venta = client.post(
        API,
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-10",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": kilos, "precio_unitario": precio}
            ],
        },
        headers=headers,
    )
    assert venta.status_code == 201, venta.text
    venta = venta.json()

    pago = client.post(
        "/api/v1/pagos",
        json={
            "venta_id": venta["id"], "fecha": "2026-06-11",
            "valor": venta["total"], "metodo": "efectivo",
        },
        headers=headers,
    )
    assert pago.status_code == 201, pago.text
    return client.get(f"{API}/{venta['id']}", headers=headers).json()


def _saldo_en_cartera(client, headers, cliente_id):
    """Lo que el cliente debe según la cartera (0 si ya no aparece)."""
    cartera = client.get(f"{API}/cartera", headers=headers).json()
    for fila in cartera:
        if fila["cliente_id"] == cliente_id:
            return float(fila["saldo"])
    return 0.0


def test_ponerle_el_flete_a_una_venta_ya_cobrada(client, base_datos):
    """El botón Editar estaba escondido en toda venta con pagos (el front reusaba
    la condición de Anular), así que el dueño no podía corregir un flete olvidado
    sin anular una venta que ya había cobrado. El backend siempre lo permitió.
    """
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar(client, h)

    # Una segunda venta a crédito del MISMO cliente: así la cartera tiene un saldo
    # real que comparar, y no un 0 que pasaría la prueba por casualidad.
    client.post(
        API,
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-12",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "10", "precio_unitario": "17000"}
            ],
        },
        headers=h,
    )

    venta = _venta_cobrada(client, h, producto, cliente)
    total_antes = float(venta["total"])
    saldo_cliente_antes = _saldo_en_cartera(client, h, cliente["id"])

    print("\n===== FLETE OLVIDADO EN UNA VENTA YA COBRADA =====")
    print(f"  venta Nº{venta['numero']}: 20 kg x 17.000 = {total_antes:,.0f}")
    print(f"  estado: {venta['estado']} · pagado: {float(venta['pagado']):,.0f} · "
          f"saldo: {float(venta['saldo']):,.0f}")
    print(f"  flete al momento de cobrar: {float(venta['gasto_monto']):,.0f}")
    print(f"  cartera del cliente antes:  {saldo_cliente_antes:,.0f}")

    # Solo el flete: ni detalles ni descuento (es lo que manda el formulario).
    r = client.put(
        f"{API}/{venta['id']}",
        json={"gasto_concepto": "Transporte a Bogotá", "gasto_por_kilo": "900"},
        headers=h,
    )
    print(f"  PUT solo flete (900/kg):    {r.status_code} "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 200, r.text

    despues = r.json()
    saldo_cliente_despues = _saldo_en_cartera(client, h, cliente["id"])
    print(f"  flete quedó en:             {float(despues['gasto_monto']):,.0f} "
          f"(900 x 20 kg)")
    print(f"  total del cliente:          {float(despues['total']):,.0f} (sigue igual)")
    print(f"  cartera del cliente ahora:  {saldo_cliente_despues:,.0f} (sigue igual)")

    # El flete es por kilo despachado, no por plata: 900 x 20 kg = 18.000
    assert float(despues["gasto_monto"]) == 900 * 20
    assert despues["gasto_concepto"] == "Transporte a Bogotá"

    # Y nada de lo que el cliente debe se movió
    assert float(despues["total"]) == total_antes
    assert float(despues["pagado"]) == total_antes
    assert float(despues["saldo"]) == 0
    assert despues["estado"] == "pagada"
    assert saldo_cliente_despues == saldo_cliente_antes


def test_una_venta_cobrada_no_deja_cambiar_productos_ni_descuento(client, base_datos):
    """La otra mitad: abrir la edición del flete no puede volverse una puerta para
    cambiar lo que el cliente debe. Por eso el formulario deja los productos y el
    descuento a la vista pero bloqueados, y NO los manda en el payload.
    """
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar(client, h)
    venta = _venta_cobrada(client, h, producto, cliente)

    print("\n===== LO QUE SIGUE BLOQUEADO CON PAGOS ENCIMA =====")

    con_detalles = client.put(
        f"{API}/{venta['id']}",
        json={
            "gasto_concepto": "Transporte a Bogotá",
            "gasto_por_kilo": "900",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "50", "precio_unitario": "17000"}
            ],
        },
        headers=h,
    )
    print(f"  PUT con detalles:  {con_detalles.status_code} · "
          f"{con_detalles.json().get('error', {}).get('detail', '')}")
    assert con_detalles.status_code == 422
    assert "pagos" in con_detalles.json()["error"]["detail"]

    con_descuento = client.put(
        f"{API}/{venta['id']}",
        json={"gasto_por_kilo": "900", "descuento": "50000"},
        headers=h,
    )
    print(f"  PUT con descuento: {con_descuento.status_code} · "
          f"{con_descuento.json().get('error', {}).get('detail', '')}")
    assert con_descuento.status_code == 422

    # Ninguno de los dos intentos dejó rastro: ni el flete se coló por el camino.
    sigue = client.get(f"{API}/{venta['id']}", headers=h).json()
    print(f"  la venta quedó igual: total {float(sigue['total']):,.0f} · "
          f"flete {float(sigue['gasto_monto']):,.0f} · "
          f"{len(sigue['detalles'])} renglón(es)")
    assert float(sigue["total"]) == float(venta["total"])
    assert float(sigue["gasto_monto"]) == 0
    assert float(sigue["detalles"][0]["cantidad"]) == 20


def test_una_venta_anulada_no_se_edita_ni_para_el_flete(client, base_datos):
    """Anulada es anulada: ahí ni el flete se toca. Por eso el front sigue
    escondiendo el botón Editar cuando el estado es 'anulada'."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar(client, h)
    venta = client.post(
        API,
        json={
            "cliente_id": cliente["id"],
            "fecha": "2026-06-10",
            "detalles": [
                {"producto_id": producto["id"], "cantidad": "20", "precio_unitario": "17000"}
            ],
        },
        headers=h,
    ).json()
    anulada = client.post(f"{API}/{venta['id']}/anular", headers=h)
    assert anulada.status_code == 200, anulada.text

    r = client.put(
        f"{API}/{venta['id']}",
        json={"gasto_concepto": "Transporte a Bogotá", "gasto_por_kilo": "900"},
        headers=h,
    )
    print("\n===== VENTA ANULADA =====")
    print(f"  PUT solo flete: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "anulada" in r.json()["error"]["detail"]
