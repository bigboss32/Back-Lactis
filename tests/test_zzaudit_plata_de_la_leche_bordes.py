"""AUDITORÍA (tercera pasada): los bordes de la plata de la leche.

Precio corregido desde el comprobante, neto que cae exacto en cero, anticipos
corregidos y borrados, la deuda que crece tres quincenas seguidas, y la quincena
congelada porque su deuda ya se cobró.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_zzaudit_plata_de_la_leche import (
    ANT,
    API,
    CERO,
    D,
    Q1,
    Q2,
    Q3,
    REC,
    _anticipo,
    _aprobar,
    _de,
    _generar,
    _leer,
    _pagar,
    _proveedor,
    _recepcion,
    cuadra_el_comprobante,
)


# ===========================================================================
# 21. CORREGIR EL PRECIO DE UN DÍA DESDE EL COMPROBANTE
# ===========================================================================
def test_zzaudit_21_precio_corregido_desde_el_comprobante(client, base_datos):
    """Un día con bonificación y descuento, y le corrigen el precio por litro.

        antes:  137,45 L x $1.833,33 = $251.991,21
                + $242,76 - $1.833,33 -> valor del día $250.400,64
        después:137,45 L x $1.922,77 = $264.284,7365 -> $264.284,74
                + $242,76 - $1.833,33 -> valor del día $262.694,17
        y el precio nuevo tiene que quedar también en la recepción del día.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    rec = _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33",
                     bonif="242.76", desc="1833.33")
    liq = _leer(client, h, _de(_generar(client, h, Q1), "Henri C")["id"])
    assert D(liq["valor_total"]) == D("250400.64"), liq["valor_total"]
    cuadra_el_comprobante(liq, "antes de corregir")

    detalle = liq["detalles"][0]
    r = client.put(
        f"{API}/{liq['id']}/detalles/{detalle['id']}",
        json={"precio_litro": "1922.77"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    tras = _leer(client, h, liq["id"])
    assert D(tras["valor_bruto"]) == D("264284.74"), tras["valor_bruto"]
    assert D(tras["valor_total"]) == D("262694.17"), tras["valor_total"]
    assert D(tras["detalles"][0]["valor"]) == D("262694.17")
    cuadra_el_comprobante(tras, "tras corregir el precio")

    dia = client.get(f"{REC}/{rec['id']}", headers=h).json()
    assert D(dia["precio_litro"]) == D("1922.77"), dia["precio_litro"]
    assert D(dia["valor_bruto"]) == D("264284.74"), dia["valor_bruto"]
    assert D(dia["valor_neto"]) == D("262694.17"), dia["valor_neto"]


# ===========================================================================
# 22. EL NETO QUE CAE EXACTO EN CERO PORQUE LOS ANTICIPOS LO CUBRIERON JUSTO
# ===========================================================================
def test_zzaudit_22_los_anticipos_cubren_exacto_la_quincena(client, base_datos):
    """44,23 L x $1.833,33 = $81.088,19 con un anticipo de EXACTAMENTE $81.088,19.

        neto = 81.088,19 - 81.088,19 = $0,00
        "Pagar" la marca pagada sin registrar un peso (esa plata ya salió como
        anticipo, en la mano) y no queda ninguna deuda que viaje a la siguiente.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "81088.19", proveedor=prov)
    liq = _leer(client, h, _de(_generar(client, h, Q1), "Henri C")["id"])
    assert D(liq["neto_a_pagar"]) == CERO, liq["neto_a_pagar"]
    assert D(liq["le_queda_debiendo"]) == CERO
    cuadra_el_comprobante(liq, "neto en cero")

    _aprobar(client, h, liq["id"])
    pagada = _pagar(client, h, liq["id"])
    assert pagada["estado"] == "pagada"
    assert D(pagada["pagado"]) == CERO
    assert pagada["pagos"] == []
    cuadra_el_comprobante(_leer(client, h, liq["id"]), "pagada con neto cero")

    # No arrastra nada a la quincena siguiente.
    _recepcion(client, h, prov, "2026-06-20", "137.45", precio="1833.33")
    q2 = _leer(client, h, _de(_generar(client, h, Q2), "Henri C")["id"])
    assert D(q2["saldo_anterior"]) == CERO, q2["saldo_anterior"]
    assert D(q2["neto_a_pagar"]) == D("251991.21")


# ===========================================================================
# 23. ANTICIPO CORREGIDO Y ANTICIPO BORRADO SOBRE UN BORRADOR
# ===========================================================================
def test_zzaudit_23_anticipo_corregido_y_borrado_dejan_la_cifra_al_dia(client, base_datos):
    """Leche $251.991,21 (137,45 L x $1.833,33) con anticipo de $300.000,77.

        al generar:            neto = 251.991,21 - 300.000,77 = -$48.009,56
        corregido a 44.230,07: neto = 251.991,21 -  44.230,07 = $207.761,14
        borrado:               neto = 251.991,21 -          0 = $251.991,21
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    ant = _anticipo(client, h, "2026-06-05", "300000.77", proveedor=prov)
    liq = _leer(client, h, _de(_generar(client, h, Q1), "Henri C")["id"])
    assert D(liq["neto_a_pagar"]) == D("-48009.56"), liq["neto_a_pagar"]

    r = client.put(f"{ANT}/{ant['id']}", json={"valor": "44230.07"}, headers=h)
    assert r.status_code == 200, r.text
    tras = _leer(client, h, liq["id"])
    assert D(tras["anticipos"]) == D("44230.07"), tras["anticipos"]
    assert D(tras["neto_a_pagar"]) == D("207761.14"), tras["neto_a_pagar"]
    cuadra_el_comprobante(tras, "anticipo corregido")

    assert client.delete(f"{ANT}/{ant['id']}", headers=h).status_code == 204
    tras = _leer(client, h, liq["id"])
    assert D(tras["anticipos"]) == CERO, tras["anticipos"]
    assert D(tras["neto_a_pagar"]) == D("251991.21"), tras["neto_a_pagar"]
    cuadra_el_comprobante(tras, "anticipo borrado")


# ===========================================================================
# 24. UN ANTICIPO CUYA FECHA SE MUEVE FUERA DEL PERÍODO
# ===========================================================================
def test_zzaudit_24_anticipo_que_se_muda_de_quincena(client, base_datos):
    """El anticipo del 10/06 ($120.000,55) se corrige al 20/06: pertenece a Q2.

        Q1 después:  251.991,21 - 0 = $251.991,21
        Q2:          85.044,12 - 120.000,55 = -$34.956,43
        El anticipo se descuenta UNA sola vez entre las dos.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    _recepcion(client, h, prov, "2026-06-22", "44.23", precio="1922.77")
    ant = _anticipo(client, h, "2026-06-10", "120000.55", proveedor=prov)
    q1 = _leer(client, h, _de(_generar(client, h, Q1), "Henri C")["id"])
    assert D(q1["anticipos"]) == D("120000.55")

    r = client.put(f"{ANT}/{ant['id']}", json={"fecha": "2026-06-20"}, headers=h)
    assert r.status_code == 200, r.text
    q1 = _leer(client, h, q1["id"])
    assert D(q1["anticipos"]) == CERO, q1["anticipos"]
    assert D(q1["neto_a_pagar"]) == D("251991.21")
    cuadra_el_comprobante(q1, "Q1 sin el anticipo mudado")

    q2 = _leer(client, h, _de(_generar(client, h, Q2), "Henri C")["id"])
    assert D(q2["anticipos"]) == D("120000.55"), q2["anticipos"]
    assert D(q2["neto_a_pagar"]) == D("-34956.43"), q2["neto_a_pagar"]
    cuadra_el_comprobante(q2, "Q2 con el anticipo mudado")
    assert D(q1["anticipos"]) + D(q2["anticipos"]) == D("120000.55")


# ===========================================================================
# 25. LA DEUDA QUE NO ALCANZA A CUBRIRSE Y SIGUE VIAJANDO
# ===========================================================================
def test_zzaudit_25_la_deuda_que_viaja_tres_quincenas(client, base_datos):
    """Tres quincenas: la deuda de la primera no la alcanza a cubrir la segunda.

        Q1  44,23 L x 1.833,33 =  $81.088,19   anticipo $300.000,77
            neto = -$218.912,58 -> debe $218.912,58
        Q2  44,23 L x 1.922,77 =  $85.044,12   anticipo $12.345,67
            neto = 85.044,12 - 12.345,67 - 218.912,58 = -$146.214,13 -> debe eso
        Q3 137,45 L x 1.755,11 = $241.239,8695 -> $241.239,87
            neto = 241.239,87 - 146.214,13 = $95.025,74 -> se paga

        LA CUENTA DEL DUEÑO:
            leche     = 81.088,19 + 85.044,12 + 241.239,87 = $407.372,18
            anticipos = 300.000,77 + 12.345,67             = $312.346,44
            pagado    =                                      $95.025,74
            312.346,44 + 95.025,74 = $407.372,18  EXACTO
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")
    _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)
    q1 = _leer(client, h, _de(_generar(client, h, Q1), "Marleny R")["id"])
    assert D(q1["le_queda_debiendo"]) == D("218912.58"), q1["le_queda_debiendo"]
    _aprobar(client, h, q1["id"])

    _recepcion(client, h, prov, "2026-06-22", "44.23", precio="1922.77")
    _anticipo(client, h, "2026-06-21", "12345.67", proveedor=prov)
    q2 = _leer(client, h, _de(_generar(client, h, Q2), "Marleny R")["id"])
    assert D(q2["saldo_anterior"]) == D("218912.58"), q2["saldo_anterior"]
    assert D(q2["anticipos"]) == D("12345.67")
    assert D(q2["le_queda_debiendo"]) == D("146214.13"), q2["le_queda_debiendo"]
    cuadra_el_comprobante(q2, "Q2")
    _aprobar(client, h, q2["id"])

    _recepcion(client, h, prov, "2026-07-03", "137.45", precio="1755.11")
    q3 = _leer(client, h, _de(_generar(client, h, Q3), "Marleny R")["id"])
    assert D(q3["valor_total"]) == D("241239.87"), q3["valor_total"]
    assert D(q3["saldo_anterior"]) == D("146214.13"), q3["saldo_anterior"]
    assert D(q3["neto_a_pagar"]) == D("95025.74"), q3["neto_a_pagar"]
    # El desglose de la deuda cobrada: UNA sola quincena origen (la Q2), no dos.
    assert [x["id"] for x in q3["deudas_cobradas"]] == [q2["id"]], q3["deudas_cobradas"]
    cuadra_el_comprobante(q3, "Q3")
    _aprobar(client, h, q3["id"])
    _pagar(client, h, q3["id"])

    todas = [_leer(client, h, x["id"]) for x in (q1, q2, q3)]
    leche = sum((D(x["valor_total"]) for x in todas), CERO)
    anticipos = sum((D(x["anticipos"]) for x in todas), CERO)
    pagado = sum((D(x["pagado"]) for x in todas), CERO)
    assert leche == D("407372.18"), leche
    assert anticipos == D("312346.44"), anticipos
    assert pagado == D("95025.74"), pagado
    assert anticipos + pagado == leche, f"{anticipos} + {pagado} != {leche}"


# ===========================================================================
# 26. LA QUINCENA CUYA DEUDA YA SE COBRÓ QUEDA CONGELADA POR TODOS LADOS
# ===========================================================================
def test_zzaudit_26_la_quincena_con_la_deuda_cobrada_no_se_mueve(client, base_datos):
    """Q1 debe $218.912,58 y Q2 se los cobra. Desde ese momento las cifras de Q1
    están congeladas: moverlas descuadraría los dos papeles a la vez."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")
    rec = _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    ant = _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)
    q1 = _de(_generar(client, h, Q1), "Marleny R")
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, prov, "2026-06-20", "137.45", precio="1833.33")
    q2 = _de(_generar(client, h, Q2), "Marleny R")
    assert D(q2["saldo_anterior"]) == D("218912.58")

    antes = _leer(client, h, q1["id"])
    detalle_id = antes["detalles"][0]["id"]
    puertas = {
        "recalcular Q1": lambda: client.post(f"{API}/{q1['id']}/recalcular", headers=h),
        "anular Q1": lambda: client.post(f"{API}/{q1['id']}/anular", headers=h),
        "corregir el precio del día": lambda: client.put(
            f"{API}/{q1['id']}/detalles/{detalle_id}", json={"precio_litro": "1922.77"},
            headers=h,
        ),
        "cambiar los litros": lambda: client.put(
            f"{REC}/{rec['id']}", json={"cantidad_litros": "219.45"}, headers=h
        ),
        "apagar el día": lambda: client.put(
            f"{REC}/{rec['id']}", json={"estado": "inactivo"}, headers=h
        ),
        "corregir el anticipo": lambda: client.put(
            f"{ANT}/{ant['id']}", json={"valor": "100000.11"}, headers=h
        ),
        "borrar el anticipo": lambda: client.delete(f"{ANT}/{ant['id']}", headers=h),
    }
    fallas = [n for n, disparo in puertas.items() if disparo().status_code < 400]
    despues = _leer(client, h, q1["id"])
    for campo in ("valor_total", "anticipos", "neto_a_pagar", "saldo",
                  "le_queda_debiendo", "total_litros"):
        assert despues[campo] == antes[campo], (
            f"Q1 se movió en {campo}: {antes[campo]} -> {despues[campo]} ({fallas})"
        )
    # Y la que se la cobró sigue cobrando exactamente lo mismo.
    assert D(_leer(client, h, q2["id"])["saldo_anterior"]) == D("218912.58")
    assert not fallas, f"puertas que movieron una quincena con la deuda ya cobrada: {fallas}"


# ===========================================================================
# 27. LOS CENTAVOS QUE NO EXISTEN NO ENTRAN
# ===========================================================================
def test_zzaudit_27_un_pago_de_tres_decimales_rebota(client, base_datos):
    """La columna es Numeric(14, 2): $100.000,005 no se puede guardar, y aceptarlo
    dejaría `pagado + saldo` un centavo por encima del neto."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    liq = _de(_generar(client, h, Q1), "Henri C")
    _aprobar(client, h, liq["id"])

    r = client.post(
        f"{API}/{liq['id']}/pagos",
        json={"fecha": "2026-06-16", "valor": "100000.005"},
        headers=h,
    )
    assert r.status_code == 422, r.text
    r = client.post(
        f"{API}/{liq['id']}/pagos", json={"fecha": "2026-06-16", "valor": "0.001"}, headers=h
    )
    assert r.status_code == 422, r.text
    r = client.post(ANT, json={
        "fecha": "2026-06-05", "valor": "1E+20", "tipo": "proveedor",
        "proveedor_id": prov["id"],
    }, headers=h)
    assert r.status_code == 422, r.text
    cuadra_el_comprobante(_leer(client, h, liq["id"]), "tras los intentos")


# ===========================================================================
# 28. UN DESCUENTO QUE DEJA EL DÍA EN ROJO NO ENTRA
# ===========================================================================
def test_zzaudit_28_un_descuento_mayor_que_el_dia_rebota(client, base_datos):
    """44,23 L x $1.833,33 = $81.088,19 con un descuento de $81.088,20."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    r = client.post(REC, json={
        "fecha": "2026-06-04", "proveedor_id": prov["id"], "cantidad_litros": "44.23",
        "precio_litro": "1833.33", "descuentos": "81088.20",
    }, headers=h)
    assert r.status_code >= 400, r.text

    rec = _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    liq = _leer(client, h, _de(_generar(client, h, Q1), "Henri C")["id"])
    subir = client.put(f"{REC}/{rec['id']}", json={"descuentos": "81088.20"}, headers=h)
    assert subir.status_code >= 400, subir.text
    tras = _leer(client, h, liq["id"])
    assert D(tras["valor_total"]) == D("81088.19"), tras["valor_total"]
    cuadra_el_comprobante(tras, "tras el descuento rebotado")
