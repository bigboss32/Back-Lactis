"""LO QUE EL DÍA DICE DE SU FLETE TIENE QUE SER LO QUE EL FLETE ES.

Dos frases del aviso del día hablaban del flete sin mirarlo:

  · "Sí se puede corregir el transportador … porque su flete todavía no se ha pagado (está
    en aprobada)": con el flete de 400 L × $150 = $60.000 contra $100.000 de adelanto al
    transportador, no hay nada que pagarle —él quedó debiendo $40.000— y la lista de
    liquidaciones rotula ese mismo flete "pagada · quedó debiendo". Y al día que no tiene
    transportador le decía "su flete todavía no se ha liquidado", como si tuviera flete.
  · La deuda de un flete ya cobrada en la quincena siguiente se leía "en la quincena de el
    flete".
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _leer

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")


def D(v):
    return Decimal(str(v))


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _transportador(client, h, nombre, tarifa="150"):
    r = client.post(f"{V}/transportadores", json={"nombre": nombre,
                                                  "valor_transporte": tarifa}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _proveedor(client, h, nombre):
    r = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                              "precio_litro": "1800"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _dia(client, h, prov, fecha, litros, transportador=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov, "cantidad_litros": litros}
    if transportador:
        cuerpo["transportador_id"] = transportador
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _adelanto_al_transportador(client, h, transportador, valor):
    r = client.post(ANT, json={"tipo": "transportador", "transportador_id": transportador,
                               "fecha": "2026-06-01", "valor": valor}, headers=h)
    assert r.status_code == 201, r.text


def _generar(client, h, tipo, periodo=Q1):
    r = client.post(f"{API}/generar", json={"periodo_inicio": periodo[0],
                                            "periodo_fin": periodo[1], "tipo": tipo},
                    headers=h)
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _de(generadas, campo, tercero):
    return next(x for x in generadas if x[campo] == tercero)["id"]


def _aviso(client, h, dia):
    return client.get(f"{REC}/{dia}", headers=h).json()["candado_aviso"]


# ---------------------------------------------------------------------------------------
# L2: la cola del transportador
# ---------------------------------------------------------------------------------------
def test_el_flete_que_quedo_debiendo_no_dice_que_falta_pagarlo(client, base_datos):
    h = auth_headers(client, "admin.a")
    stella = _transportador(client, h, "Stella Debe")
    prov = _proveedor(client, h, "Rosa Flete Debe")
    dia = _dia(client, h, prov, "2026-06-02", "400", stella)
    _adelanto_al_transportador(client, h, stella, "100000")
    generadas = _generar(client, h, "ambos")
    leche = _de(generadas, "proveedor_id", prov)
    flete = _de(generadas, "transportador_id", stella)
    _ok(client.post(f"{API}/{leche}/aprobar", headers=h))
    _ok(client.post(f"{API}/{leche}/pagar", headers=h))
    _ok(client.post(f"{API}/{flete}/aprobar", headers=h))
    del_flete = _leer(client, h, flete)
    # 400 L × $150 = $60.000 − $100.000 de adelanto: él debe $40.000.
    assert (del_flete["estado"], D(del_flete["valor_total"]), D(del_flete["saldo"]),
            del_flete["estado_visible"]) == (
        "aprobada", D(60000), D(-40000), "pagada · quedó debiendo")

    aviso = _aviso(client, h, dia)
    print(f"\n  aviso: {aviso}")
    assert "todavía no se ha pagado" not in aviso and "(está en aprobada)" not in aviso
    assert ("Sí se puede corregir el transportador, la ruta, la sucursal y las "
            "observaciones, porque su flete no tiene nada por entregar: Stella Debe quedó "
            "debiendo $40.000 y esa deuda todavía no se ha cobrado (está en pagada · quedó "
            "debiendo); al cambiarlo, el día se suelta de esa liquidación y ella se "
            "recalcula sin él.") in aviso
    # Y lo que dice se puede: el transportador se corrige.
    efrain = _transportador(client, h, "Efraín Debe")
    guardado = _ok(client.put(f"{REC}/{dia}", json={"transportador_id": efrain}, headers=h))
    assert guardado["transportador_id"] == efrain


def test_el_flete_aprobado_sin_deuda_conserva_su_frase(client, base_datos):
    """Control: 400 L × $150 = $60.000 sin adelanto, aprobado. Ahí sí falta pagarlo."""
    h = auth_headers(client, "admin.a")
    stella = _transportador(client, h, "Stella Sin Deuda")
    prov = _proveedor(client, h, "Rosa Sin Deuda")
    dia = _dia(client, h, prov, "2026-06-02", "400", stella)
    generadas = _generar(client, h, "ambos")
    leche = _de(generadas, "proveedor_id", prov)
    _ok(client.post(f"{API}/{leche}/aprobar", headers=h))
    _ok(client.post(f"{API}/{leche}/pagar", headers=h))
    _ok(client.post(f"{API}/{_de(generadas, 'transportador_id', stella)}/aprobar", headers=h))
    assert ("porque su flete todavía no se ha pagado (está en aprobada); al cambiarlo, el "
            "día se suelta de esa liquidación y ella se recalcula sin él.") in _aviso(
        client, h, dia)


def test_el_dia_sin_transportador_no_habla_de_un_flete(client, base_datos):
    """La leche de 100 L × $1.800 = $180.000 pagada, y el día sin transportador: no tiene
    flete que liquidar. Control: con transportador y el flete sin generar, sí."""
    h = auth_headers(client, "admin.a")
    sin = _proveedor(client, h, "Dia Sin Transportador")
    con = _proveedor(client, h, "Dia Con Transportador")
    stella = _transportador(client, h, "Stella Sin Liquidar")
    dia_sin = _dia(client, h, sin, "2026-06-02", "100")
    dia_con = _dia(client, h, con, "2026-06-02", "100", stella)
    generadas = _generar(client, h, "proveedor")
    for prov in (sin, con):
        liq = _de(generadas, "proveedor_id", prov)
        _ok(client.post(f"{API}/{liq}/aprobar", headers=h))
        _ok(client.post(f"{API}/{liq}/pagar", headers=h))

    aviso = _aviso(client, h, dia_sin)
    print(f"\n  sin transportador: {aviso}")
    assert "su flete" not in aviso
    assert aviso.endswith(
        "Sí se puede corregir el transportador, la ruta, la sucursal y las observaciones, "
        "porque este día no tiene transportador.")
    assert _aviso(client, h, dia_con).endswith(
        "Sí se puede corregir el transportador, la ruta, la sucursal y las observaciones, "
        "porque su flete todavía no se ha liquidado.")


# ---------------------------------------------------------------------------------------
# L1: "la quincena del flete"
# ---------------------------------------------------------------------------------------
def test_la_deuda_cobrada_de_un_flete_se_lee_del_flete(client, base_datos):
    """Flete Q1: 400 L × $150 = $60.000 contra $100.000 de adelanto, debe $40.000. Flete Q2:
    600 L × $150 = $90.000 − $40.000 = $50.000 se los cobra."""
    h = auth_headers(client, "admin.a")
    stella = _transportador(client, h, "Stella Cobrada")
    prov = _proveedor(client, h, "Rosa Flete Cobrado")
    dia = _dia(client, h, prov, "2026-06-02", "400", stella)
    _adelanto_al_transportador(client, h, stella, "100000")
    q1 = _de(_generar(client, h, "transportador"), "transportador_id", stella)
    _dia(client, h, prov, "2026-06-20", "600", stella)
    q2 = _de(_generar(client, h, "transportador", Q2), "transportador_id", stella)
    otra = _leer(client, h, q2)
    assert (D(otra["valor_total"]), D(otra["saldo_anterior"]), D(otra["saldo"])) == (
        D(90000), D(40000), D(50000))

    aviso = _aviso(client, h, dia)
    print(f"\n  aviso: {aviso}")
    assert "de el flete" not in aviso
    assert aviso.startswith(
        "Lo que Stella Cobrada quedó debiendo en la quincena del flete de este día ya se le "
        "cobró en la del 16/06/2026 al 30/06/2026:")
    efrain = _transportador(client, h, "Efraín Cobrada")
    put = client.put(f"{REC}/{dia}", json={"transportador_id": efrain}, headers=h)
    texto = _detalle(put)
    print(f"  PUT: {texto}")
    assert put.status_code == 422 and "de el flete" not in texto
    assert texto.startswith(
        "No se puede cambiar el transportador de este día: lo que el tercero quedó debiendo "
        "en esta quincena del flete ya se le cobró en la liquidación del 16/06/2026 al "
        "30/06/2026, así que cambiar la cifra descuadraría los dos comprobantes.")
    # El consejo es anular la que cobró (borrador), y se sigue.
    assert "Anule primero esa liquidación" in texto
    _ok(client.post(f"{API}/{q2}/anular", headers=h))
    _ok(client.put(f"{REC}/{dia}", json={"transportador_id": efrain}, headers=h))
    assert _leer(client, h, q1)["deuda_trasladada_a_id"] is None
