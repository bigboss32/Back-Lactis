"""AUDITORÍA (cuarta pasada): correcciones sobre una quincena ya generada.

Bonificaciones y descuentos corregidos, días borrados, y el renglón de la deuda
arrastrada aguantando un recálculo del comprobante que la está cobrando.
"""
from tests.conftest import auth_headers
from tests.test_zzaudit_plata_de_la_leche import (
    API,
    CERO,
    D,
    Q1,
    Q2,
    REC,
    _anticipo,
    _aprobar,
    _de,
    _generar,
    _leer,
    _proveedor,
    _recepcion,
    cuadra_el_comprobante,
)


def test_zzaudit_33_el_saldo_anterior_aguanta_un_recalculo(client, base_datos):
    """Q2 le está cobrando $218.912,58 a Marleny y le corrigen un día.

        Q1  44,23 L x 1.833,33 =  $81.088,19  anticipo $300.000,77 -> debe $218.912,58
        Q2 219,45 L x 1.755,11 = $385.158,8895 -> $385.158,89
            neto = 385.158,89 - 218.912,58 = $166.246,31
        se corrige el día de Q2 a 137,45 L:
            137,45 x 1.755,11 = $241.239,8695 -> $241.239,87
            neto = 241.239,87 - 218.912,58 = $22.327,29
        La deuda arrastrada NO se recalcula: sale de otro documento ya marcado.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")
    _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)
    q1 = _de(_generar(client, h, Q1), "Marleny R")
    _aprobar(client, h, q1["id"])

    rec2 = _recepcion(client, h, prov, "2026-06-20", "219.45", precio="1755.11")
    q2 = _leer(client, h, _de(_generar(client, h, Q2), "Marleny R")["id"])
    assert D(q2["neto_a_pagar"]) == D("166246.31"), q2["neto_a_pagar"]
    _aprobar(client, h, q2["id"])

    r = client.put(f"{REC}/{rec2['id']}", json={"cantidad_litros": "137.45"}, headers=h)
    assert r.status_code == 200, r.text
    tras = _leer(client, h, q2["id"])
    assert tras["estado"] == "borrador", tras["estado"]
    assert D(tras["valor_total"]) == D("241239.87"), tras["valor_total"]
    assert D(tras["saldo_anterior"]) == D("218912.58"), tras["saldo_anterior"]
    assert D(tras["neto_a_pagar"]) == D("22327.29"), tras["neto_a_pagar"]
    assert [x["id"] for x in tras["deudas_cobradas"]] == [q1["id"]]
    cuadra_el_comprobante(tras, "Q2 recalculada con deuda")

    # Y recalculando a mano tampoco se mueve la deuda.
    assert client.post(f"{API}/{q2['id']}/recalcular", headers=h).status_code == 200
    otra = _leer(client, h, q2["id"])
    assert D(otra["saldo_anterior"]) == D("218912.58"), otra["saldo_anterior"]
    assert D(otra["neto_a_pagar"]) == D("22327.29")
    cuadra_el_comprobante(otra, "Q2 recalculada a mano")


def test_zzaudit_34_bonificaciones_y_descuentos_corregidos(client, base_datos):
    """137,45 L x $1.833,33 = $251.991,21 y después le agregan los ajustes del día.

        + bonificación $242,76
        - descuento  $1.833,33
        VALOR TOTAL = 251.991,21 + 242,76 - 1.833,33 = $250.400,64
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    rec = _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    liq = _de(_generar(client, h, Q1), "Henri C")
    _aprobar(client, h, liq["id"])

    r = client.put(
        f"{REC}/{rec['id']}",
        json={"bonificaciones": "242.76", "descuentos": "1833.33"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    tras = _leer(client, h, liq["id"])
    assert D(tras["bonificaciones"]) == D("242.76")
    assert D(tras["descuentos"]) == D("1833.33")
    assert D(tras["valor_total"]) == D("250400.64"), tras["valor_total"]
    assert D(tras["detalles"][0]["valor"]) == D("250400.64")
    cuadra_el_comprobante(tras, "ajustes del día corregidos")


def test_zzaudit_35_borrar_un_dia_del_borrador(client, base_datos):
    """Dos días liquidados; se BORRA uno.

        02/06 137,45 x 1.833,33 = $251.991,21
        05/06  44,23 x 1.922,77 =  $85.044,12
        total antes = $337.035,33 ; después de borrar el 05/06 = $251.991,21
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    r2 = _recepcion(client, h, prov, "2026-06-05", "44.23", precio="1922.77")
    liq = _de(_generar(client, h, Q1), "Henri C")
    assert D(_leer(client, h, liq["id"])["valor_total"]) == D("337035.33")

    assert client.delete(f"{REC}/{r2['id']}", headers=h).status_code == 204
    tras = _leer(client, h, liq["id"])
    assert D(tras["valor_total"]) == D("251991.21"), tras["valor_total"]
    assert len(tras["detalles"]) == 1, tras["detalles"]
    assert D(tras["total_litros"]) == D("137.45")
    cuadra_el_comprobante(tras, "día borrado")


def test_zzaudit_36_la_deuda_que_deja_de_existir_antes_de_cobrarse(client, base_datos):
    """Marleny queda debiendo $218.912,58 y ANTES de que nadie se lo cobre le
    corrigen los litros del día: 44,23 L estaban mal, eran 219,45 L.

        219,45 x 1.833,33 = $402.324,2685 -> $402.324,27
        neto = 402.324,27 - 300.000,77 = $102.323,50  -> ya no debe nada
        y la quincena siguiente sale con $0,00 de deuda arrastrada.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")
    rec = _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)
    q1 = _leer(client, h, _de(_generar(client, h, Q1), "Marleny R")["id"])
    assert D(q1["le_queda_debiendo"]) == D("218912.58")

    r = client.put(f"{REC}/{rec['id']}", json={"cantidad_litros": "219.45"}, headers=h)
    assert r.status_code == 200, r.text
    tras = _leer(client, h, q1["id"])
    assert D(tras["valor_total"]) == D("402324.27"), tras["valor_total"]
    assert D(tras["neto_a_pagar"]) == D("102323.50"), tras["neto_a_pagar"]
    assert D(tras["le_queda_debiendo"]) == CERO
    cuadra_el_comprobante(tras, "la deuda que desapareció")

    _recepcion(client, h, prov, "2026-06-20", "137.45", precio="1833.33")
    q2 = _leer(client, h, _de(_generar(client, h, Q2), "Marleny R")["id"])
    assert D(q2["saldo_anterior"]) == CERO, q2["saldo_anterior"]
    assert q2["deudas_cobradas"] == []
    cuadra_el_comprobante(q2, "Q2 sin deuda")
