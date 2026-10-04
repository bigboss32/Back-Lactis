"""EL CONSEJO DEL DÍA SALE DE LA MISMA PREGUNTA QUE EL BOTÓN, Y SE SIGUE.

Recepción diaria rebota el cambio de un día cuya quincena ya está en firme, y el 422
termina con un consejo. Ese consejo se escogía por el ESTADO guardado, sin preguntarle a
"Corregir esta quincena" si de verdad la acepta, y para la misma fila daba tres salidas
distintas aunque Corregir la aceptaba en las tres:

  · D) v1 de 100 L × $1.800 = $180.000, pagada: "corríjala por fuera del sistema";
  · E) la misma con un abono de $50.000 (saldo $130.000): "Elimine primero ese pago", que
    se lleva los soportes del pago, cuando para el precio había salida sin borrar nada;
  · F) la del adelanto exacto de $180.000 corregida con un día olvidado de 20 L ($216.000,
    saldo $36.000) mandaba a Corregir mientras estaba 'parcial', y pagados los $36.000
    pasaba a "por fuera del sistema" con Corregir aceptándola igual.

Y la deuda ya cobrada en otra quincena decía siempre "anule primero esa liquidación",
también cuando esa no se deja anular o cuando anularla no suelta el día. Ahora dice el
mismo consejo que el anticipo de la misma quincena (`consejo_deuda_cobrada`).

Cada prueba sigue el consejo y mide que lleve a donde dice, con la regla de oro:
neto = valor_total − anticipos − saldo_anterior; saldo = neto − pagado.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _leer
from tests.test_recepcion_candado_quincena_corregida import _corregida_sin_pagos

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")

USE_CORREGIR = "Si lo que está mal es el precio del día, use 'Corregir esta quincena'"
POR_FUERA = "corríjala por fuera del sistema"
ELIMINE = "Elimine primero ese pago"
ANULE = (
    "Anule primero esa liquidación —así esta deuda vuelve a quedar libre— y vuelva a "
    "intentarlo."
)
ORDEN = "empiece por la más vieja —la del 01/06/2026 al 15/06/2026—"


def D(v):
    return Decimal(str(v))


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _proveedor(client, h, nombre, precio="1800"):
    r = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                              "precio_litro": precio}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _dia(client, h, prov, fecha, litros, precio=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov, "cantidad_litros": litros}
    if precio:
        cuerpo["precio_litro"] = precio
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _adelanto(client, h, prov, valor, fecha="2026-06-01"):
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                               "fecha": fecha, "valor": valor}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _generar(client, h, prov, periodo=Q1):
    r = client.post(f"{API}/generar", json={"periodo_inicio": periodo[0],
                                            "periodo_fin": periodo[1],
                                            "tipo": "proveedor"}, headers=h)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)["id"]


def _aprobada_de_180(client, h, nombre):
    """100 L × $1.800 = $180.000 el 02/06, generada y aprobada."""
    prov = _proveedor(client, h, nombre)
    dia = _dia(client, h, prov, "2026-06-02", "100")
    liq = _generar(client, h, prov)
    _ok(client.post(f"{API}/{liq}/aprobar", headers=h))
    return prov, dia, liq


def _cuadra(liq):
    """La regla de oro sobre la fila tal como la lee la pantalla."""
    neto = D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq["saldo_anterior"] or 0)
    assert D(liq["saldo"]) == neto - D(liq["pagado"]), liq


def _precio_del_02(client, h, liq, precio="1700"):
    detalle = next(d for d in _leer(client, h, liq)["detalles"] if d["fecha"] == "2026-06-02")
    return {"motivo": "precio mal digitado",
            "precios": [{"detalle_id": detalle["id"], "precio_litro": precio}]}


def _seguir_corregir(client, h, liq):
    """Lo que manda el consejo para el precio: la vista previa y la corrección pasan."""
    cuerpo = _precio_del_02(client, h, liq)
    prev = client.post(f"{API}/{liq}/corregir/previsualizar", json=cuerpo, headers=h)
    assert prev.status_code == 200, prev.text
    return _ok(client.post(f"{API}/{liq}/corregir", json=cuerpo, headers=h))


# ---------------------------------------------------------------------------------------
# C6: el consejo pregunta a Corregir
# ---------------------------------------------------------------------------------------
def test_D_la_pagada_v1_manda_a_corregir_el_precio_y_es_cierto(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, dia, liq = _aprobada_de_180(client, h, "Consejo Pagada")
    _ok(client.post(f"{API}/{liq}/pagar", headers=h))
    antes = _leer(client, h, liq)
    assert (antes["estado"], antes["version"], D(antes["pagado"]), len(antes["pagos"])) == (
        "pagada", 1, D(180000), 1)

    put = client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h)
    borrar = client.delete(f"{REC}/{dia}", headers=h)
    print(f"\n  PUT: {_detalle(put)}\n  DELETE: {_detalle(borrar)}")
    for r in (put, borrar):
        assert r.status_code == 422
        assert USE_CORREGIR in _detalle(r) and POR_FUERA not in _detalle(r)
    assert _detalle(put).startswith(
        "No se puede cambiar el precio por litro de este día: la leche ya se pagó en una "
        "liquidación.")
    # El Anular de la misma fila dice la misma salida.
    assert "use 'Corregir esta quincena'" in _detalle(
        client.post(f"{API}/{liq}/anular", headers=h))

    # Se sigue: 100 L × $1.700 = $170.000, el pago de $180.000 se conserva y la quesera
    # le pagó $10.000 de más (saldo −$10.000).
    despues = _seguir_corregir(client, h, liq)
    assert (despues["estado"], despues["version"], D(despues["valor_total"]),
            D(despues["pagado"]), D(despues["saldo"]), len(despues["pagos"])) == (
        "pagada", 2, D(170000), D(180000), D(-10000), 1)
    _cuadra(despues)


def test_E_la_parcial_con_abono_ofrece_corregir_antes_que_borrar_el_pago(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, dia, liq = _aprobada_de_180(client, h, "Consejo Abono")
    _ok(client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20", "valor": "50000"},
                    headers=h))
    antes = _leer(client, h, liq)
    assert (antes["estado"], antes["version"], D(antes["saldo"])) == ("parcial", 1, D(130000))

    put = client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h)
    texto = _detalle(put)
    print(f"\n  PUT: {texto}")
    assert put.status_code == 422
    assert "ya tiene un pago registrado" in texto
    # Primero la salida que conserva el pago; borrarlo queda de segunda.
    assert USE_CORREGIR in texto and ELIMINE in texto
    assert texto.index(USE_CORREGIR) < texto.index(ELIMINE)
    assert "que conserva el pago y sus soportes" in texto
    assert "sus soportes, que no se recuperan" in texto

    # Se sigue la primera: $170.000 − $50.000 = $120.000 por entregar, el abono sigue ahí.
    despues = _seguir_corregir(client, h, liq)
    assert (despues["estado"], despues["version"], D(despues["valor_total"]),
            D(despues["pagado"]), D(despues["saldo"]), len(despues["pagos"])) == (
        "parcial", 2, D(170000), D(50000), D(120000), 1)
    _cuadra(despues)


def test_E_para_los_litros_borrar_el_abono_sigue_siendo_la_salida(client, base_datos):
    """Corregir no cambia litros: para esos la salida por dentro es borrar el abono (y se
    dice), y se comprueba que de verdad suelta el día."""
    h = auth_headers(client, "admin.a")
    _, dia, liq = _aprobada_de_180(client, h, "Consejo Abono Litros")
    _ok(client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20", "valor": "50000"},
                    headers=h))
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    assert put.status_code == 422 and ELIMINE in _detalle(put)
    assert "si es otra cifra, registre el ajuste en la quincena siguiente" in _detalle(put)

    (pago,) = _leer(client, h, liq)["pagos"]
    _ok(client.delete(f"{API}/{liq}/pagos/{pago['id']}", headers=h))
    _ok(client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h))
    hoy = _leer(client, h, liq)
    # 90 L × $1.800 = $162.000, sin pagos: volvió a borrador para aprobarla otra vez.
    assert (hoy["estado"], D(hoy["valor_total"]), D(hoy["pagado"]), D(hoy["saldo"])) == (
        "borrador", D(162000), D(0), D(162000))
    _cuadra(hoy)


def test_F_la_v2_pagada_sigue_mandando_a_corregir(client, base_datos):
    """Ayer, 'parcial' v2, mandaba a Corregir; pagados los $36.000 queda 'pagada' v2 y
    Corregir la sigue aceptando: el consejo no cambia de un día para otro."""
    h = auth_headers(client, "admin.a")
    _, dia, _, liq = _corregida_sin_pagos(client, h, "Consejo Corregida Pagada")
    ayer = _detalle(client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h))
    assert USE_CORREGIR in ayer
    _ok(client.post(f"{API}/{liq}/pagar", headers=h))
    hoy = _leer(client, h, liq)
    assert (hoy["estado"], hoy["version"], D(hoy["valor_total"]), D(hoy["anticipos"]),
            D(hoy["pagado"]), D(hoy["saldo"])) == (
        "pagada", 2, D(216000), D(180000), D(36000), D(0))

    put = client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h)
    print(f"\n  PUT: {_detalle(put)}")
    assert put.status_code == 422
    assert USE_CORREGIR in _detalle(put) and POR_FUERA not in _detalle(put)

    # 100 L × $1.700 + 20 L × $1.800 = $206.000 − $180.000 = $26.000; ya se le pagaron
    # $36.000, así que la quesera le pagó $10.000 de más.
    despues = _seguir_corregir(client, h, liq)
    assert (D(despues["valor_total"]), D(despues["saldo"]), despues["version"]) == (
        D(206000), D(-10000), 3)
    _cuadra(despues)


def test_el_flete_pagado_conserva_su_consejo(client, base_datos):
    """Corregir es solo para la leche: el flete pagado sigue diciendo lo de siempre, y no
    nombra Corregir."""
    h = auth_headers(client, "admin.a")
    stella = client.post(f"{V}/transportadores", json={"nombre": "Stella Consejo",
                                                       "valor_transporte": "100"},
                         headers=h).json()
    efrain = client.post(f"{V}/transportadores", json={"nombre": "Efraín Consejo",
                                                       "valor_transporte": "120"},
                         headers=h).json()
    prov = _proveedor(client, h, "Flete Consejo")
    r = client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                               "cantidad_litros": "100", "transportador_id": stella["id"]},
                    headers=h)
    assert r.status_code == 201, r.text
    dia = r.json()["id"]
    g = client.post(f"{API}/generar", json={"periodo_inicio": Q1[0], "periodo_fin": Q1[1],
                                            "tipo": "transportador"}, headers=h)
    assert g.status_code == 200, g.text
    (flete,) = g.json()["generadas"]
    _ok(client.post(f"{API}/{flete['id']}/aprobar", headers=h))
    _ok(client.post(f"{API}/{flete['id']}/pagar", headers=h))

    put = client.put(f"{REC}/{dia}", json={"transportador_id": efrain["id"]}, headers=h)
    print(f"\n  PUT: {_detalle(put)}")
    assert put.status_code == 422
    assert "el flete ya se pagó en una liquidación" in _detalle(put)
    assert POR_FUERA in _detalle(put) and "Corregir esta quincena" not in _detalle(put)


# ---------------------------------------------------------------------------------------
# C1 (Recepción): la deuda ya cobrada dice el consejo del anticipo, y es cierto
# ---------------------------------------------------------------------------------------
def _beto(client, h, nombre):
    """Q1: 100 L × $1.800 = $180.000 contra $300.000 de adelanto, debe $120.000 (borrador).
    Q2: 100 L × $2.500 = $250.000 − $120.000 = $130.000 (borrador)."""
    prov = _proveedor(client, h, nombre)
    dia1 = _dia(client, h, prov, "2026-06-02", "100")
    adelanto = _adelanto(client, h, prov, "300000")
    q1 = _generar(client, h, prov, Q1)
    _dia(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, prov, Q2)
    assert D(_leer(client, h, q2)["saldo_anterior"]) == D(120000)
    assert D(_leer(client, h, q1)["saldo"]) == D(-120000)
    return prov, dia1, adelanto, q1, q2


def _consejo_del_anticipo(client, h, adelanto):
    """Lo que el candado del adelanto dice después del hecho ('… ya se le cobró en …')."""
    aviso = client.get(f"{ANT}/{adelanto}", headers=h).json()["candado_aviso"]
    return aviso.split("al 30/06/2026. ", 1)[1]


def test_con_la_otra_en_borrador_el_dia_manda_a_anular_y_es_cierto(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, dia1, adelanto, q1, q2 = _beto(client, h, "Beto Dia Borrador")
    put = client.put(f"{REC}/{dia1}", json={"cantidad_litros": "90"}, headers=h)
    texto = _detalle(put)
    print(f"\n  PUT: {texto}")
    assert put.status_code == 422
    assert ANULE in texto and ORDEN in texto
    assert texto.endswith(_consejo_del_anticipo(client, h, adelanto))

    _ok(client.post(f"{API}/{q2}/anular", headers=h))
    _ok(client.put(f"{REC}/{dia1}", json={"cantidad_litros": "90"}, headers=h))
    # 90 L × $1.800 = $162.000 − $300.000 = −$138.000: debe $138.000 y nadie se los cobró.
    hoy = _leer(client, h, q1)
    assert (D(hoy["valor_total"]), D(hoy["saldo"]), hoy["deuda_trasladada_a_id"]) == (
        D(162000), D(-138000), None)
    _cuadra(hoy)


def test_la_otra_corregida_no_se_deja_anular_y_el_dia_no_manda_ahi(client, base_datos):
    """Beto sin salida: la del 16/06 se pagó ($130.000) y se corrigió con un día olvidado de
    10 L × $2.500 (v2). Anularla rebota siempre: el día no manda a anularla."""
    h = auth_headers(client, "admin.a")
    prov, dia1, adelanto, q1, q2 = _beto(client, h, "Beto Dia Sinsalida")
    _ok(client.post(f"{API}/{q2}/aprobar", headers=h))
    _ok(client.post(f"{API}/{q2}/pagar", headers=h))
    olvidado = _dia(client, h, prov, "2026-06-25", "10", precio="2500")
    otra = _ok(client.post(f"{API}/{q2}/corregir", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}, headers=h))
    assert (otra["estado"], otra["version"], D(otra["pagado"]), D(otra["saldo"])) == (
        "parcial", 2, D(130000), D(25000))

    for r in (client.put(f"{REC}/{dia1}", json={"cantidad_litros": "90"}, headers=h),
              client.delete(f"{REC}/{dia1}", headers=h)):
        texto = _detalle(r)
        print(f"\n  {r.request.method}: {texto}")
        assert r.status_code == 422
        assert "ya se le cobró en la liquidación del 16/06/2026 al 30/06/2026" in texto
        assert "nule primero" not in texto and ORDEN not in texto
        assert "por ahí no hay salida dentro del sistema" in texto
        assert texto.endswith(_consejo_del_anticipo(client, h, adelanto))
    # Y es cierto: la otra no se deja anular.
    assert client.post(f"{API}/{q2}/anular", headers=h).status_code == 422


def test_si_anular_la_otra_no_suelta_el_dia_no_se_aconseja(client, base_datos):
    """Carla: $180.000 cubiertos exacto por su adelanto, pagada y corregida a $1.500 (v2,
    $150.000, debe $30.000); la siguiente (aprobada, $180.000 − $30.000 = $150.000) se los
    cobra. Anular la siguiente no suelta el día: sigue pagada y corregida."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Carla Dia")
    dia1 = _dia(client, h, prov, "2026-06-02", "100")
    adelanto = _adelanto(client, h, prov, "180000")
    q1 = _generar(client, h, prov, Q1)
    _ok(client.post(f"{API}/{q1}/aprobar", headers=h))
    _ok(client.post(f"{API}/{q1}/pagar", headers=h))
    q1_v2 = _ok(client.post(f"{API}/{q1}/corregir", json=_precio_del_02(client, h, q1, "1500"),
                            headers=h))
    assert (q1_v2["estado"], q1_v2["version"], D(q1_v2["saldo"])) == ("pagada", 2, D(-30000))
    _dia(client, h, prov, "2026-06-20", "100")
    q2 = _generar(client, h, prov, Q2)
    _ok(client.post(f"{API}/{q2}/aprobar", headers=h))
    assert D(_leer(client, h, q2)["saldo"]) == D(150000)

    put = client.put(f"{REC}/{dia1}", json={"cantidad_litros": "90"}, headers=h)
    texto = _detalle(put)
    print(f"\n  PUT: {texto}")
    assert put.status_code == 422 and "nule primero" not in texto
    assert ("Y anular esa liquidación no la destrabaría, porque esta quincena ya quedó "
            "cerrada como pagada. Si la cifra está mala, registre el ajuste en la quincena "
            "siguiente") in texto
    assert texto.endswith(_consejo_del_anticipo(client, h, adelanto))

    # Se comprueba que no destraba: anulada la siguiente, el día sigue trabado.
    _ok(client.post(f"{API}/{q2}/anular", headers=h))
    assert client.put(f"{REC}/{dia1}", json={"cantidad_litros": "90"},
                      headers=h).status_code == 422
