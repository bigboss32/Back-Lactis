"""ATAQUE 4 A «CORREGIR LOS ANTICIPOS DE UNA QUINCENA PAGADA»: LOS BORDES.

QUÉ SE ATACA Y POR QUÉ IMPORTA. Un anticipo NO es un cálculo: es plata que ya salió
de la caja y se le puso en la mano al productor. El comprobante se la resta:

    neto_a_pagar = valor_total − anticipos − saldo_anterior
    saldo        = neto_a_pagar − pagado

La marca que impide descontarla dos veces es UNA SOLA COLUMNA, `Anticipo.liquidacion_id`:
mientras apunte a una quincena, `pendientes_de` no lo vuelve a encontrar. Todo este
archivo ataca esa columna desde afuera — con el anticipo del vecino, con el del
empleado, con el de la otra quesera, con el que ya se llevó otra quincena, con el que
llegó tarde, con cifras imposibles — y mide UNA sola cosa en cada rebote:

    QUE NO SE ESCRIBIÓ NADA, NI A MEDIAS.

"Nada" acá tiene cinco partes y las cinco se verifican juntas (`_intacta`): el mismo
valor_total, LA MISMA CIFRA DE ANTICIPOS, el mismo saldo, la misma versión del folio y
CERO renglones en liquidaciones_correcciones. Y aparte, que el anticipo atacado quedó
sin tocar: mismo valor y apuntando a donde apuntaba.

Un rebote a medias sería peor que no rebotar. Si un intento fallido dejara el anticipo
del vecino marcado contra esta quincena, esa plata se le descontaría a Henri —que nunca
la recibió— y quedaría sin descontarle a quien sí se la llevó. Son dos productores
equivocados de un solo golpe, y el dueño solo lo ve cuando saca la calculadora.

LAS CIFRAS SON LAS MISMAS EN TODO EL ARCHIVO, y están escogidas para que la resta se
pueda hacer de cabeza:

    250 L × $2.000       =  $500.000   valor_total
    − anticipo del 03/06 =  $100.000
    ------------------------------------
    neto a pagar         =  $400.000   ← y eso se le entregó completo
    pagado               =  $400.000
    saldo                =        $0   estado 'pagada', versión 1

LA REGLA DE LA CASA se mide en cada resultado que SÍ escribe:
    neto = pagado + saldo   y   neto = valor_total − anticipos − saldo_anterior.
"""
import uuid
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
ANT = "/api/v1/anticipos"

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")


def D(v):
    return Decimal(str(v))


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="2000"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros):
    r = client.post(
        REC,
        json={"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, prov, fecha, valor, obs=None):
    r = client.post(
        ANT,
        json={
            "tipo": "proveedor",
            "proveedor_id": prov["id"],
            "fecha": fecha,
            "valor": str(valor),
            "observaciones": obs,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _leer_anticipo(client, h, anticipo_id):
    r = client.get(f"{ANT}/{anticipo_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _generar(client, h, periodo):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _de(liquidaciones, nombre):
    encontradas = [liq for liq in liquidaciones if liq.get("proveedor_nombre") == nombre]
    assert len(encontradas) == 1, f"se esperaba una sola de {nombre}: {liquidaciones}"
    return encontradas[0]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _aprobar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _pagar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/pagar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _previsualizar(client, h, liq_id, **payload):
    payload.setdefault("motivo", "se le olvidó un adelanto")
    return client.post(f"{API}/{liq_id}/corregir/previsualizar", json=payload, headers=h)


def _corregir(client, h, liq_id, **payload):
    payload.setdefault("motivo", "se le olvidó un adelanto")
    return client.post(f"{API}/{liq_id}/corregir", json=payload, headers=h)


def _mensaje(respuesta):
    """El texto del rebote. Los errores de negocio salen como
    {"error": {"code": ..., "detail": ...}} y no como el `detail` pelado de FastAPI:
    leerlo mal haría que estas pruebas "pasaran" sin mirar el mensaje."""
    cuerpo = respuesta.json()
    return (cuerpo.get("error") or {}).get("detail", "") or str(cuerpo)


def _correcciones(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/correcciones", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------- escenario
def _escenario(client, h):
    """La quincena de $500.000 con $100.000 de adelanto, pagada con $400.000.

    Devuelve (proveedor, liquidación pagada, anticipo aplicado del 03/06).
    """
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", 250)  # 250 L × $2.000 = $500.000
    anticipo = _anticipo(client, h, prov, "2026-06-03", "100000", "para la droga")

    liq = _de(_generar(client, h, Q1), "Henri C")
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])

    assert D(liq["valor_total"]) == D(500000), liq
    assert D(liq["anticipos"]) == D(100000), liq
    assert D(liq["neto_a_pagar"]) == D(400000), liq
    assert D(liq["pagado"]) == D(400000) and D(liq["saldo"]) == D(0), liq
    assert liq["estado"] == "pagada" and liq["version"] == 1, liq
    return prov, liq, anticipo


def _intacta(client, h, liq_id):
    """Que el rebote no dejó ni un peso escrito. Es la medida de todo el archivo."""
    liq = _leer(client, h, liq_id)
    assert D(liq["valor_total"]) == D(500000), f"le movieron el total: {liq['valor_total']}"
    assert D(liq["anticipos"]) == D(100000), (
        f"LE MOVIERON LOS ANTICIPOS: {liq['anticipos']} — esa cifra es plata que ya se "
        "le puso en la mano al productor"
    )
    assert D(liq["neto_a_pagar"]) == D(400000), f"le movieron el neto: {liq['neto_a_pagar']}"
    assert D(liq["pagado"]) == D(400000), f"le movieron lo pagado: {liq['pagado']}"
    assert D(liq["saldo"]) == D(0), f"le movieron el saldo: {liq['saldo']}"
    assert liq["estado"] == "pagada", f"le movieron el estado: {liq['estado']}"
    assert liq["version"] == 1, f"le subieron la versión del folio: {liq['version']}"
    assert _correcciones(client, h, liq_id) == [], "quedó un renglón de corrección"
    return liq


def _anticipo_intacto(client, h, anticipo_id, *, valor, liquidacion_id):
    """Que el anticipo atacado quedó donde estaba y valiendo lo que valía."""
    a = _leer_anticipo(client, h, anticipo_id)
    assert D(a["valor"]) == D(valor), f"le cambiaron el valor al adelanto: {a['valor']}"
    assert a.get("liquidacion_id") == liquidacion_id, (
        f"le movieron la marca al adelanto: {a.get('liquidacion_id')} (se esperaba "
        f"{liquidacion_id})"
    )
    return a


# ===========================================================================
# LÍNEA DE BASE: si esto no pasa, los rebotes de abajo no prueban nada.
# ===========================================================================
def test_linea_de_base_el_adelanto_olvidado_entra_y_la_cuenta_cuadra(client, base_datos):
    """$500.000 − ($100.000 + $60.000 de adelantos) = $340.000 contra $400.000 entregados.

    Es el camino feliz, puesto acá solo para fijar que el escenario y las llaves de
    todo el archivo funcionan. Mide LA REGLA DE LA CASA sobre el resultado: el neto
    tiene que ser exactamente lo pagado más el saldo, y exactamente el total menos los
    anticipos. Acá el saldo sale NEGATIVO ($340.000 − $400.000 = −$60.000) y eso es la
    verdad: se le entregaron $60.000 de más y la quincena siguiente se los descuenta.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    olvidado = _anticipo(client, h, prov, "2026-06-10", "60000", "el del mercado")

    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[olvidado["id"]])
    assert r.status_code == 200, r.text
    d = r.json()

    assert D(d["anticipos"]) == D(160000), d["anticipos"]
    assert D(d["neto_a_pagar"]) == D(340000), d["neto_a_pagar"]
    assert D(d["neto_a_pagar"]) == D(d["pagado"]) + D(d["saldo"])
    assert D(d["neto_a_pagar"]) == D(d["valor_total"]) - D(d["anticipos"]) - D(
        d["saldo_anterior"]
    )
    assert D(d["saldo"]) == D(-60000) and D(d["le_queda_debiendo"]) == D(60000)
    assert d["version"] == 2

    # Y el adelanto quedó marcado contra ESTA quincena: es lo que impide que la
    # siguiente se lo vuelva a descontar.
    _anticipo_intacto(client, h, olvidado["id"], valor="60000.00", liquidacion_id=liq["id"])


# ===========================================================================
# 1. EL ADELANTO QUE NO ES DE ESTE PRODUCTOR
# ===========================================================================
def test_el_adelanto_de_otro_productor_no_sale_como_candidato(client, base_datos):
    """Marina también recibió plata en la mano; a Henri no se le puede descontar.

    El diálogo del dueño es una lista de casillas. Si el adelanto de Marina apareciera
    ahí, un clic distraído le descuenta a Henri $80.000 que nunca recibió — y a Marina
    se los vuelven a descontar después, porque su marca sigue libre.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, aplicado = _escenario(client, h)

    marina = _proveedor(client, h, "Marina R")
    ajeno = _anticipo(client, h, marina, "2026-06-05", "80000", "el de Marina")

    prev = _previsualizar(client, h, liq["id"])
    assert prev.status_code == 200, prev.text
    p = prev.json()
    ids_sueltos = {a["anticipo_id"] for a in p["anticipos_sueltos"]}
    ids_aplicados = {a["anticipo_id"] for a in p["anticipos_aplicados"]}
    assert ajeno["id"] not in ids_sueltos, "el adelanto de Marina salió como candidato"
    assert ajeno["id"] not in ids_aplicados
    assert ids_aplicados == {aplicado["id"]}


def test_incluir_el_adelanto_de_otro_productor_rebota_sin_escribir(client, base_datos):
    """Con el id en la mano tampoco: rebota y no queda nada escrito."""
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    marina = _proveedor(client, h, "Marina R")
    ajeno = _anticipo(client, h, marina, "2026-06-05", "80000", "el de Marina")

    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[ajeno["id"]])
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"
    assert "suelto" in _mensaje(r) or "quincena" in _mensaje(r), _mensaje(r)

    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, ajeno["id"], valor="80000.00", liquidacion_id=None)


def test_soltar_el_adelanto_de_otro_productor_rebota_sin_escribir(client, base_datos):
    """Y por la puerta de "soltar" tampoco se le toca la marca a un tercero."""
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    marina = _proveedor(client, h, "Marina R")
    _recepcion(client, h, marina, "2026-06-04", 100)  # $200.000
    ajeno = _anticipo(client, h, marina, "2026-06-05", "80000", "el de Marina")
    liq_marina = _de(_generar(client, h, Q1), "Marina R")
    assert D(liq_marina["anticipos"]) == D(80000), liq_marina

    r = _corregir(client, h, liq["id"], anticipos_a_soltar=[ajeno["id"]])
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"

    _intacta(client, h, liq["id"])
    # El de Marina sigue descontado en la quincena de Marina.
    _anticipo_intacto(
        client, h, ajeno["id"], valor="80000.00", liquidacion_id=liq_marina["id"]
    )
    quedo = _leer(client, h, liq_marina["id"])
    assert D(quedo["anticipos"]) == D(80000), "le movieron los anticipos a Marina"
    assert D(quedo["neto_a_pagar"]) == D(120000), quedo["neto_a_pagar"]


def test_corregirle_el_valor_al_adelanto_de_otro_productor_rebota(client, base_datos):
    """Ni el valor. Esa cifra es el recibo de una entrega en efectivo a OTRA persona."""
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    marina = _proveedor(client, h, "Marina R")
    ajeno = _anticipo(client, h, marina, "2026-06-05", "80000", "el de Marina")

    r = _corregir(
        client,
        h,
        liq["id"],
        valores_de_anticipos=[{"anticipo_id": ajeno["id"], "valor": "10"}],
    )
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"

    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, ajeno["id"], valor="80000.00", liquidacion_id=None)


# ===========================================================================
# 2. EL ADELANTO DE LA OTRA QUESERA
# ===========================================================================
def test_el_adelanto_de_otra_quesera_no_se_toca_ni_con_el_id_en_la_mano(client, base_datos):
    """Multiempresa: la Quesera A no ve ni mueve un peso de la Quesera B.

    Las tres puertas de los anticipos (incluir, soltar, corregir el valor) se prueban
    en la misma quincena, porque el hueco de una sirve igual que el de las otras: el
    resultado sería descontarle a un productor de A plata que se entregó en B.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prov, liq, _ = _escenario(client, ha)

    prov_b = _proveedor(client, hb, "Otilia B")
    ajeno_b = _anticipo(client, hb, prov_b, "2026-06-05", "90000", "plata de la Quesera B")

    # Ni siquiera se puede leer desde A.
    assert client.get(f"{ANT}/{ajeno_b['id']}", headers=ha).status_code == 404

    for payload in (
        {"anticipos_a_incluir": [ajeno_b["id"]]},
        {"anticipos_a_soltar": [ajeno_b["id"]]},
        {"valores_de_anticipos": [{"anticipo_id": ajeno_b["id"], "valor": "1000"}]},
    ):
        r = _corregir(client, ha, liq["id"], **payload)
        assert r.status_code in (404, 422), f"{payload} -> {r.status_code}: {r.text}"
        _intacta(client, ha, liq["id"])

    # Y en la Quesera B el adelanto sigue igual, suelto y por $90.000.
    _anticipo_intacto(client, hb, ajeno_b["id"], valor="90000.00", liquidacion_id=None)


# ===========================================================================
# 3. EL ADELANTO DE NÓMINA
# ===========================================================================
def test_el_adelanto_de_un_empleado_no_entra_en_la_quincena_de_un_productor(
    client, base_datos
):
    """El adelanto del quesero ya se le descontó en su pago de nómina.

    Son dos plata distintas y dos personas distintas. Si este adelanto entrara acá, se
    le descontaría DOS VECES al negocio: una en el pago de Aurelio y otra en el
    comprobante de Henri, que nunca recibió ese dinero.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)

    empleado = client.post(
        "/api/v1/empleados",
        json={
            "nombre": "Aurelio",
            "apellido": "Marin",
            "cargo": "Quesero",
            "valor_dia": "50000",
        },
        headers=h,
    ).json()
    adelanto = client.post(
        ANT,
        json={
            "tipo": "empleado",
            "empleado_id": empleado["id"],
            "fecha": "2026-06-05",
            "valor": "120000",
            "observaciones": "adelanto del quesero",
        },
        headers=h,
    )
    assert adelanto.status_code == 201, adelanto.text
    adelanto = adelanto.json()

    pago = client.post(
        "/api/v1/nomina",
        json={"empleado_id": empleado["id"], "fecha": "2026-06-15", "dias_trabajados": "10"},
        headers=h,
    )
    assert pago.status_code == 201, pago.text
    # La marca de nómina quedó puesta: ya se le descontó al empleado.
    en_nomina = _leer_anticipo(client, h, adelanto["id"])
    assert en_nomina["pago_empleado_id"] is not None, en_nomina

    prev = _previsualizar(client, h, liq["id"]).json()
    ids = {a["anticipo_id"] for a in prev["anticipos_sueltos"]}
    assert adelanto["id"] not in ids, "el adelanto del EMPLEADO salió como candidato"

    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[adelanto["id"]])
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"

    _intacta(client, h, liq["id"])
    quedo = _leer_anticipo(client, h, adelanto["id"])
    assert D(quedo["valor"]) == D(120000)
    assert quedo.get("liquidacion_id") is None, "le metieron la marca de una quincena"
    assert quedo["pago_empleado_id"] == en_nomina["pago_empleado_id"], (
        "le movieron la marca de nómina: ese adelanto ya se le descontó al empleado"
    )


# ===========================================================================
# 4. EL ADELANTO QUE YA SE DESCONTÓ EN OTRA QUINCENA
# ===========================================================================
def _segunda_quincena_se_lleva(client, h, prov, fecha_anticipo, valor):
    """Anota un adelanto y deja que la quincena SIGUIENTE se lo descuente.

    Devuelve (anticipo, liquidación de Q2). Es el montaje de los dos ataques de abajo:
    a partir de acá esa plata YA ESTÁ restada en un comprobante que existe.
    """
    anticipo = _anticipo(client, h, prov, fecha_anticipo, valor, "el que se llevó la otra")
    _recepcion(client, h, prov, "2026-06-18", 200)  # $400.000 en Q2
    liq2 = _de(_generar(client, h, Q2), "Henri C")
    assert D(liq2["anticipos"]) == D(valor), liq2
    return anticipo, liq2


def test_el_adelanto_que_ya_descuenta_otra_quincena_no_se_puede_meter_aqui(
    client, base_datos
):
    """Descontarlo dos veces le quitaría $70.000 a un productor que recibió $70.000.

    Q2 ya le restó esos $70.000 y su comprobante puede estar impreso. Si Q1 también se
    los descuenta, el productor pone $140.000 contra una entrega de $70.000.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    ya_usado, liq2 = _segunda_quincena_se_lleva(client, h, prov, "2026-06-12", "70000")

    # No sale como candidato...
    prev = _previsualizar(client, h, liq["id"]).json()
    ids = {a["anticipo_id"] for a in prev["anticipos_sueltos"]}
    assert ya_usado["id"] not in ids, "un adelanto ya descontado salió como suelto"

    # ...y con el id en la mano rebota.
    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[ya_usado["id"]])
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"

    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, ya_usado["id"], valor="70000.00", liquidacion_id=liq2["id"])
    otra = _leer(client, h, liq2["id"])
    assert D(otra["anticipos"]) == D(70000), "le movieron los anticipos a la otra quincena"
    assert D(otra["neto_a_pagar"]) == D(otra["valor_total"]) - D(otra["anticipos"]) - D(
        otra["saldo_anterior"]
    )


def test_soltar_desde_aqui_un_adelanto_que_descuenta_otra_quincena_rebota(client, base_datos):
    """Soltarlo desde Q1 dejaría a Q2 cobrando un descuento que ya no existe.

    Este es el ataque peligroso de verdad: la operación "soltar" existe y funciona, y
    si no mirara a CUÁL quincena pertenece el adelanto, con un id prestado se le
    borraría la marca a un comprobante ajeno — y la próxima corrida se lo descontaría
    otra vez al mismo productor.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    ya_usado, liq2 = _segunda_quincena_se_lleva(client, h, prov, "2026-06-12", "70000")

    r = _corregir(client, h, liq["id"], anticipos_a_soltar=[ya_usado["id"]])
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"

    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, ya_usado["id"], valor="70000.00", liquidacion_id=liq2["id"])
    otra = _leer(client, h, liq2["id"])
    assert D(otra["anticipos"]) == D(70000), "le soltaron el adelanto a la otra quincena"


def test_se_lo_llevaron_entre_la_previsualizacion_y_el_boton(client, base_datos):
    """El dueño ve la casilla, se va a almorzar, y otro genera la quincena siguiente.

    Entre la foto y el botón la marca cambió de dueño. El botón NO puede escribir la
    cifra que prometió la foto: esos $70.000 ya están restados en el otro comprobante.
    Tiene que rebotar y dejar TODO como estaba, en las dos quincenas.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    suelto = _anticipo(client, h, prov, "2026-06-12", "70000", "el del almuerzo")

    # LA FOTO: en este momento sí es candidato, y el diálogo promete la cifra.
    prev = _previsualizar(client, h, liq["id"], anticipos_a_incluir=[suelto["id"]])
    assert prev.status_code == 200, prev.text
    p = prev.json()
    assert D(p["anticipos_antes"]) == D(100000) and D(p["anticipos_despues"]) == D(170000)
    assert D(p["neto_despues"]) == D(330000), p["neto_despues"]

    # OTRO PROCESO SE LO LLEVA.
    _recepcion(client, h, prov, "2026-06-18", 200)
    liq2 = _de(_generar(client, h, Q2), "Henri C")
    assert D(liq2["anticipos"]) == D(70000), liq2

    # EL BOTÓN, con la lista vieja en la mano.
    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[suelto["id"]])
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"
    assert "otra" in _mensaje(r).lower() or "suelto" in _mensaje(r).lower(), _mensaje(r)

    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, suelto["id"], valor="70000.00", liquidacion_id=liq2["id"])
    otra = _leer(client, h, liq2["id"])
    assert D(otra["anticipos"]) == D(70000)


# ===========================================================================
# 5. LAS FECHAS: EL QUE LLEGÓ TARDE Y EL QUE VIENE DE HACE SEIS MESES
# ===========================================================================
def test_el_adelanto_posterior_al_periodo_no_sale_como_candidato(client, base_datos):
    """Un adelanto del 20/06 no es de la quincena que cerró el 15/06.

    `pendientes_de` corta por arriba en `periodo_fin`, y así tiene que ser: esa plata
    se entregó después de cerrar el papel, y le toca a la quincena siguiente. Si
    entrara acá, el comprobante que el productor tiene en la mano quedaría restando un
    adelanto que en su fecha todavía no existía.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    tardio = _anticipo(client, h, prov, "2026-06-20", "45000", "después de cerrar")

    prev = _previsualizar(client, h, liq["id"]).json()
    ids = {a["anticipo_id"] for a in prev["anticipos_sueltos"]}
    assert tardio["id"] not in ids, (
        f"un adelanto del 20/06 salió como candidato de la quincena que cerró el "
        f"{Q1[1]}"
    )

    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[tardio["id"]])
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"

    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, tardio["id"], valor="45000.00", liquidacion_id=None)


def test_el_adelanto_del_ultimo_dia_del_periodo_si_es_candidato(client, base_datos):
    """El borde exacto: el 15/06 SÍ entra, porque el filtro es `fecha <= periodo_fin`.

    Va al lado del anterior a propósito. Los dos miden el mismo corte desde los dos
    lados, y sin este el de arriba pasaría igual con un filtro equivocado que dejara
    por fuera el último día de la quincena — y ese adelanto se quedaría sin descontar
    en ninguna parte.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    ultimo = _anticipo(client, h, prov, Q1[1], "45000", "el del último día")

    prev = _previsualizar(client, h, liq["id"]).json()
    candidatos = {a["anticipo_id"]: a for a in prev["anticipos_sueltos"]}
    assert ultimo["id"] in candidatos, "el adelanto del último día quedó por fuera"
    assert candidatos[ultimo["id"]]["aviso"] is None, (
        "un adelanto DE la quincena no lleva el aviso de los viejos"
    )


def test_el_adelanto_de_hace_seis_meses_sale_pero_señalado(client, base_datos):
    """`pendientes_de` NO tiene cota por abajo: el del 10/12/2025 aparece igual.

    Y ESO ESTÁ BIEN —es plata que el dueño entregó y nunca recuperó— pero tiene que
    verlo SEÑALADO antes de marcarlo. Sin el aviso, un adelanto de $200.000 de hace
    seis meses se le mete a una quincena chica y la deja en rojo sin que el dueño
    entienda de dónde salió. El aviso trae la fecha escrita, que es con lo que él lo
    reconoce ("el de diciembre").
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    viejo = _anticipo(client, h, prov, "2025-12-10", "200000", "el de diciembre")
    del_periodo = _anticipo(client, h, prov, "2026-06-10", "30000", "el del mercado")

    prev = _previsualizar(client, h, liq["id"])
    assert prev.status_code == 200, prev.text
    sueltos = {a["anticipo_id"]: a for a in prev.json()["anticipos_sueltos"]}

    assert viejo["id"] in sueltos, "el adelanto viejo se escondió: esa plata no se recupera"
    aviso = sueltos[viejo["id"]]["aviso"]
    assert aviso, "el adelanto de hace seis meses salió SIN aviso"
    assert "10/12/2025" in aviso, aviso
    assert "ANTES" in aviso, aviso

    # Y el de adentro del período no lleva aviso: si todos lo llevaran, ninguno avisa.
    assert sueltos[del_periodo["id"]]["aviso"] is None, sueltos[del_periodo["id"]]


def test_incluir_el_viejo_deja_la_cuenta_cuadrada_aunque_la_quincena_quede_en_rojo(
    client, base_datos
):
    """Si el dueño lo marca a sabiendas, la cifra queda EXACTA y el rojo se dice.

    $500.000 − ($100.000 + $200.000) = $200.000 de neto contra $400.000 ya entregados:
    el productor le queda debiendo $200.000 al negocio. La quincena NO esconde eso ni
    lo redondea; la regla de la casa se cumple igual.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    viejo = _anticipo(client, h, prov, "2025-12-10", "200000", "el de diciembre")

    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[viejo["id"]])
    assert r.status_code == 200, r.text
    d = r.json()
    assert D(d["anticipos"]) == D(300000), d["anticipos"]
    assert D(d["neto_a_pagar"]) == D(200000), d["neto_a_pagar"]
    assert D(d["neto_a_pagar"]) == D(d["pagado"]) + D(d["saldo"])
    assert D(d["saldo"]) == D(-200000) and D(d["le_queda_debiendo"]) == D(200000)


# ===========================================================================
# 6. LAS CIFRAS IMPOSIBLES
# ===========================================================================
@pytest.mark.parametrize(
    "valor, por_que",
    [
        ("0", "un adelanto de $0 no es una entrega: es borrarlo sin decirlo"),
        ("-5000", "una entrega negativa no existe"),
        ("100000.005", "medio centavo que la columna Numeric(14,2) no guarda"),
        ("1e20", "en Postgres el INSERT revienta con un 22003 (numeric field overflow)"),
        ("99999999999999999999.00", "veinte dígitos contra un max_digits de 14"),
    ],
)
def test_un_valor_imposible_para_un_adelanto_rebota_sin_escribir(
    client, base_datos, valor, por_que
):
    """Las cifras que la columna no guarda se rechazan, NO se redondean.

    Redondear en silencio "$100.000,005" cambiaría el recibo de una entrega en efectivo
    sin que quien la registró se entere. Y 1e20 no es un número grande: es un 500 en la
    cara del usuario el día que corra contra Postgres.

    Lo que se mide además del rebote: que la quincena no quedó a medias. Un 422 que
    igual hubiera escrito el valor sería el peor de los dos mundos.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, aplicado = _escenario(client, h)

    r = _corregir(
        client,
        h,
        liq["id"],
        valores_de_anticipos=[{"anticipo_id": aplicado["id"], "valor": valor}],
    )
    assert r.status_code == 422, f"{por_que}: pasó con {r.status_code} — {r.text[:300]}"

    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, aplicado["id"], valor="100000.00", liquidacion_id=liq["id"])


def test_el_valor_mas_grande_que_si_cabe_en_la_columna_no_descuadra_la_resta(
    client, base_datos
):
    """El tope legítimo de Numeric(14, 2): $999.999.999.999,99.

    No es un caso que el dueño vaya a teclear, pero es el borde de arriba del filtro y
    tiene que quedar claro de qué lado cae. Si pasa, la resta sigue cuadrando al
    centavo (el neto se va a un rojo enorme, que es la verdad de lo que se escribió);
    si rebota, no escribe nada. Las dos son respuestas correctas — lo que NO puede
    pasar es guardar la cifra y descuadrar la resta.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, aplicado = _escenario(client, h)
    tope = "999999999999.99"

    r = _corregir(
        client,
        h,
        liq["id"],
        valores_de_anticipos=[{"anticipo_id": aplicado["id"], "valor": tope}],
    )
    if r.status_code == 422:
        _intacta(client, h, liq["id"])
        _anticipo_intacto(client, h, aplicado["id"], valor="100000.00", liquidacion_id=liq["id"])
        return

    assert r.status_code == 200, r.text
    d = r.json()
    assert D(d["anticipos"]) == D(tope), d["anticipos"]
    assert D(d["neto_a_pagar"]) == D(d["valor_total"]) - D(d["anticipos"]) - D(
        d["saldo_anterior"]
    ), "LA REGLA DE LA CASA se rompió con la cifra tope"
    assert D(d["neto_a_pagar"]) == D(d["pagado"]) + D(d["saldo"])


def test_un_id_inventado_rebota_por_las_tres_puertas(client, base_datos):
    """Tres puertas, tres rebotes, cero escrituras.

    Un id que no existe es lo que manda un cliente viejo, una pantalla con la lista
    caducada o alguien probando a mano. Ninguna de las tres puede dejar la quincena con
    la versión subida y sin cambios: ese folio nuevo obliga al dueño a recogerle el
    papel al productor para nada.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    fantasma = str(uuid.uuid4())

    for payload in (
        {"anticipos_a_incluir": [fantasma]},
        {"anticipos_a_soltar": [fantasma]},
        {"valores_de_anticipos": [{"anticipo_id": fantasma, "valor": "1000"}]},
    ):
        r = _corregir(client, h, liq["id"], **payload)
        assert r.status_code in (404, 422), f"{payload} -> {r.status_code}: {r.text}"
        _intacta(client, h, liq["id"])


def test_el_mismo_adelanto_entrando_y_saliendo_a_la_vez_rebota(client, base_datos):
    """Incluir y soltar el mismo id en la misma corrección no significa nada.

    Sin este guardia el resultado dependería del orden en que se aplicaran las dos
    listas, y el dueño vería una cifra en el diálogo y otra en el papel.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, aplicado = _escenario(client, h)

    r = _corregir(
        client,
        h,
        liq["id"],
        anticipos_a_incluir=[aplicado["id"]],
        anticipos_a_soltar=[aplicado["id"]],
    )
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"
    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, aplicado["id"], valor="100000.00", liquidacion_id=liq["id"])


def test_el_motivo_de_una_letra_rebota_y_no_mueve_el_adelanto(client, base_datos):
    """`min_length=3`: sin motivo escrito, la corrección no se distingue de un error.

    El motivo es lo único que después le explica a alguien por qué el papel del
    productor dice otra cifra. Un "x" no explica nada, y el rebote tiene que ser ANTES
    de tocar la marca del adelanto.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, aplicado = _escenario(client, h)

    r = _corregir(client, h, liq["id"], motivo="x", anticipos_a_soltar=[aplicado["id"]])
    assert r.status_code == 422, r.text

    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, aplicado["id"], valor="100000.00", liquidacion_id=liq["id"])


# ===========================================================================
# 7. LA FAMILIA "PAGADA CON pagado = $0": SOLTAR EL ÚNICO ADELANTO
# ===========================================================================
def _escenario_saldada_por_el_adelanto(client, h):
    """La quincena que el adelanto cubrió EXACTO, y por eso se cerró con `pagado = $0`.

        50 L × $2.000 = $100.000 de quincena
        − adelanto      $100.000  (ya entregado en la mano)
        ------------------------------------
        neto a pagar          $0  → 'pagar' la cierra sin mover un peso de caja

    Es la familia rara y peligrosa: 'pagada' con `pagado` en cero. Devuelve
    (proveedor, liquidación, anticipo, recepción).
    """
    prov = _proveedor(client, h, "Marina R")
    rec = _recepcion(client, h, prov, "2026-06-02", 50)
    anticipo = _anticipo(client, h, prov, "2026-06-03", "100000", "el que la cubrió toda")

    liq = _de(_generar(client, h, Q1), "Marina R")
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])

    assert D(liq["valor_total"]) == D(100000), liq
    assert D(liq["anticipos"]) == D(100000), liq
    assert D(liq["neto_a_pagar"]) == D(0) and D(liq["pagado"]) == D(0), liq
    assert liq["estado"] == "pagada" and liq["version"] == 1, liq
    return prov, liq, anticipo, rec


def test_soltar_el_unico_adelanto_de_la_quincena_saldada_deja_la_cuenta_exacta(
    client, base_datos
):
    """Si ese adelanto no era de aquí, hay que entregarle los $100.000 completos.

    ES EL CASO MÁS DELICADO DEL ARCHIVO. La quincena estaba 'pagada' con `pagado = $0`
    porque el adelanto la cubrió exacto. Al soltarlo:

        valor_total  $100.000
        − anticipos        $0
        ------------------------
        neto         $100.000 = pagado $0 + saldo $100.000   → estado 'parcial'

    Y el adelanto NO se borra: queda suelto, porque esa plata sí se entregó — solo que
    no en esta quincena. La siguiente se lo descuenta.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, anticipo, _rec = _escenario_saldada_por_el_adelanto(client, h)

    r = _corregir(
        client,
        h,
        liq["id"],
        motivo="ese adelanto era de la quincena pasada, no de esta",
        anticipos_a_soltar=[anticipo["id"]],
    )
    assert r.status_code == 200, r.text
    d = r.json()

    assert D(d["anticipos"]) == D(0), d["anticipos"]
    assert D(d["valor_total"]) == D(100000), d["valor_total"]
    assert D(d["neto_a_pagar"]) == D(100000), d["neto_a_pagar"]
    assert D(d["pagado"]) == D(0), "le inventaron un pago"
    assert D(d["saldo"]) == D(100000), d["saldo"]
    assert D(d["neto_a_pagar"]) == D(d["pagado"]) + D(d["saldo"])
    assert D(d["neto_a_pagar"]) == D(d["valor_total"]) - D(d["anticipos"]) - D(
        d["saldo_anterior"]
    )
    # NO puede volver para atrás a 'aprobada': ese estado suelta los candados del día.
    assert d["estado"] == "parcial", d["estado"]
    assert d["version"] == 2, d["version"]

    # El adelanto sigue vivo y suelto: esa plata se entregó.
    _anticipo_intacto(client, h, anticipo["id"], valor="100000.00", liquidacion_id=None)

    corr = _correcciones(client, h, liq["id"])
    assert len(corr) == 1, corr
    assert D(corr[0]["anticipos_antes"]) == D(100000)
    assert D(corr[0]["anticipos_despues"]) == D(0)
    assert [c["accion"] for c in corr[0]["anticipos_cambiados"]] == ["salio"]


def test_soltar_el_unico_adelanto_no_suelta_los_candados_del_dia(client, base_datos):
    """Y los días de esa quincena SIGUEN trabados en Recepción diaria.

    ACÁ ESTÁ EL HUECO QUE ESTA PRUEBA VIGILA. El candado del día pregunta "¿ya salió
    plata?" mirando `pagado > 0` o `estado == 'pagada'`. En esta familia `pagado` es $0,
    así que al pasar a 'parcial' LAS DOS PARTES CAEN A LA VEZ y el día quedaría abierto
    a que cualquiera le cambie los litros — con un comprobante ya impreso y entregado.

    Lo que lo sostiene es `version > 1`. Si esta prueba se pone roja, el día de una
    quincena ya corregida se puede editar desde Recepción diaria, y el papel del
    productor deja de corresponder a la base sin que nadie lo firme.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, anticipo, rec = _escenario_saldada_por_el_adelanto(client, h)

    r = _corregir(
        client,
        h,
        liq["id"],
        motivo="ese adelanto era de la quincena pasada",
        anticipos_a_soltar=[anticipo["id"]],
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "parcial"

    # EL DÍA: 50 L a $2.000. Intentar cambiarle los litros tiene que rebotar.
    editar = client.put(
        f"{REC}/{rec['id']}", json={"cantidad_litros": "80"}, headers=h
    )
    assert editar.status_code in (403, 409, 422), (
        f"SE SOLTÓ EL CANDADO del día de una quincena ya corregida: {editar.status_code} "
        f"{editar.text[:300]}"
    )

    # Y el día quedó como estaba, marcado contra su comprobante.
    quedo = client.get(f"{REC}/{rec['id']}", headers=h).json()
    assert D(quedo["cantidad_litros"]) == D(50), quedo["cantidad_litros"]
    assert quedo["liquidacion_id"] == liq["id"], quedo["liquidacion_id"]

    # Y borrarlo tampoco.
    borrar = client.delete(f"{REC}/{rec['id']}", headers=h)
    assert borrar.status_code in (403, 409, 422), (
        f"se pudo BORRAR el día de una quincena corregida: {borrar.status_code}"
    )


def test_el_adelanto_soltado_se_lo_descuenta_la_quincena_siguiente_una_sola_vez(
    client, base_datos
):
    """La otra punta de "se suelta, no se borra": la siguiente lo recoge, y una vez sola.

    Si al soltarlo se hubiera borrado, esos $100.000 que el productor ya tiene en el
    bolsillo no se le descontarían nunca y el negocio los perdería. Y si la marca
    quedara mal, se los descontaría dos veces. Acá se mide que la quincena siguiente lo
    descuenta EXACTAMENTE UNA VEZ:

        Q2: 100 L × $2.000 = $200.000 − $100.000 = $100.000 de neto.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, anticipo, _rec = _escenario_saldada_por_el_adelanto(client, h)
    _corregir(
        client,
        h,
        liq["id"],
        motivo="ese adelanto era de la quincena pasada",
        anticipos_a_soltar=[anticipo["id"]],
    )

    _recepcion(client, h, prov, "2026-06-18", 100)  # $200.000
    liq2 = _de(_generar(client, h, Q2), "Marina R")

    assert D(liq2["valor_total"]) == D(200000), liq2
    assert D(liq2["anticipos"]) == D(100000), (
        f"la quincena siguiente no recogió el adelanto soltado: {liq2['anticipos']}"
    )
    assert D(liq2["neto_a_pagar"]) == D(100000), liq2["neto_a_pagar"]
    assert D(liq2["neto_a_pagar"]) == D(liq2["valor_total"]) - D(liq2["anticipos"]) - D(
        liq2["saldo_anterior"]
    )
    _anticipo_intacto(client, h, anticipo["id"], valor="100000.00", liquidacion_id=liq2["id"])

    # Y no lo recoge dos veces: una tercera quincena no lo vuelve a ver.
    _recepcion(client, h, prov, "2026-07-02", 100)
    liq3 = _de(
        _generar(client, h, ("2026-07-01", "2026-07-15")),
        "Marina R",
    )
    assert D(liq3["anticipos"]) == D(0), (
        f"el mismo adelanto se descontó DOS VECES: {liq3['anticipos']}"
    )


# ===========================================================================
# 8. QUE UN REBOTE NO DEJE LA MITAD ESCRITA
# ===========================================================================
def test_un_adelanto_bueno_y_uno_malo_en_la_misma_peticion_no_escriben_nada(
    client, base_datos
):
    """La corrección es UNA operación: o entra todo, o no entra nada.

    Se manda un adelanto legítimo de Henri JUNTO con el de Marina. Si el bueno se
    marcara antes de que el malo rebotara, esos $60.000 quedarían descontados sin
    versión nueva, sin motivo y sin renglón de corrección: plata movida en un
    comprobante entregado, sin una sola línea que la explique.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    bueno = _anticipo(client, h, prov, "2026-06-10", "60000", "el bueno")
    marina = _proveedor(client, h, "Marina R")
    malo = _anticipo(client, h, marina, "2026-06-05", "80000", "el de Marina")

    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[bueno["id"], malo["id"]])
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"

    _intacta(client, h, liq["id"])
    _anticipo_intacto(client, h, bueno["id"], valor="60000.00", liquidacion_id=None)
    _anticipo_intacto(client, h, malo["id"], valor="80000.00", liquidacion_id=None)


def test_un_dia_bueno_y_un_adelanto_malo_tampoco_escriben_nada(client, base_datos):
    """El mismo corte, cruzando las dos mitades de la corrección.

    Los días y los adelantos viajan en la MISMA petición y se escriben en pasos
    distintos —el día marca la recepción, el adelanto marca el anticipo—. Si el día se
    marcara y el adelanto rebotara después, ese día quedaría PRESO: fuera de este
    comprobante (que no subió de versión) y fuera de todos los siguientes, porque su
    marca ya apunta acá. Nadie se lo pagaría al productor nunca.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    suelto = _recepcion(client, h, prov, "2026-06-12", 90)  # $180.000
    assert suelto["liquidacion_id"] is None
    marina = _proveedor(client, h, "Marina R")
    malo = _anticipo(client, h, marina, "2026-06-05", "80000", "el de Marina")

    r = _corregir(
        client,
        h,
        liq["id"],
        recepciones_a_incluir=[suelto["id"]],
        anticipos_a_incluir=[malo["id"]],
    )
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"

    _intacta(client, h, liq["id"])
    quedo = client.get(f"{REC}/{suelto['id']}", headers=h).json()
    assert quedo["liquidacion_id"] is None, (
        "EL DÍA QUEDÓ PRESO: un intento que rebotó le puso la marca, y ahora está fuera "
        "de este comprobante y de todos los siguientes"
    )
    _anticipo_intacto(client, h, malo["id"], valor="80000.00", liquidacion_id=None)


def test_repetir_la_misma_correccion_no_descuenta_dos_veces(client, base_datos):
    """El reintento del navegador: la cifra se vuelve a sumar desde cero, no se acumula.

    Es la razón por la que `liquidacion.anticipos` se recalcula desde los que quedaron
    marcados en vez de sumarle a la guardada. Con la primera petición los anticipos
    quedan en $160.000; el reintento con el MISMO id tiene que rebotar (ese adelanto ya
    no está suelto) y dejar la cifra en $160.000 — nunca en $220.000.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    olvidado = _anticipo(client, h, prov, "2026-06-10", "60000", "el del mercado")

    primera = _corregir(client, h, liq["id"], anticipos_a_incluir=[olvidado["id"]])
    assert primera.status_code == 200, primera.text
    assert D(primera.json()["anticipos"]) == D(160000)

    segunda = _corregir(client, h, liq["id"], anticipos_a_incluir=[olvidado["id"]])
    assert segunda.status_code in (404, 422), f"{segunda.status_code}: {segunda.text}"

    d = _leer(client, h, liq["id"])
    assert D(d["anticipos"]) == D(160000), (
        f"el reintento descontó dos veces el mismo adelanto: {d['anticipos']}"
    )
    assert d["version"] == 2, f"el reintento subió la versión del folio: {d['version']}"
    assert len(_correcciones(client, h, liq["id"])) == 1
    assert D(d["neto_a_pagar"]) == D(d["pagado"]) + D(d["saldo"])


# ===========================================================================
# 9. LAS PUERTAS QUE TIENEN QUE SEGUIR CERRADAS
# ===========================================================================
def test_con_la_deuda_ya_cobrada_no_se_le_tocan_los_adelantos(client, base_datos):
    """Cuando la deuda de la quincena ya viajó a la siguiente, la puerta se cierra.

    LAS CIFRAS. El adelanto viejo de $200.000 deja a Henri debiendo $200.000, y la
    quincena siguiente ya se los restó (`saldo_anterior $200.000`, impreso en un papel
    que puede estar entregado). Meterle otro adelanto acá cambiaría esa deuda, y el
    OTRO comprobante quedaría cobrando una cifra que ya no existe. El candado es el
    mismo de la corrección de días, y los anticipos no lo pueden rodear.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    viejo = _anticipo(client, h, prov, "2025-12-10", "200000", "el de diciembre")
    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[viejo["id"]])
    assert r.status_code == 200 and D(r.json()["le_queda_debiendo"]) == D(200000)

    _recepcion(client, h, prov, "2026-06-18", 200)
    liq2 = _de(_generar(client, h, Q2), "Henri C")
    assert D(liq2["saldo_anterior"]) == D(200000), liq2
    assert sum(D(x["le_queda_debiendo"]) for x in liq2["deudas_cobradas"]) == D(200000)

    otro = _anticipo(client, h, prov, "2026-06-11", "10000", "otro que faltaba")
    cerrado = _corregir(client, h, liq["id"], anticipos_a_incluir=[otro["id"]])
    assert cerrado.status_code == 422, cerrado.text
    assert "ya se le cobr" in _mensaje(cerrado), _mensaje(cerrado)

    # Y nada se movió: ni acá, ni en la que cobró la deuda, ni en el adelanto.
    quedo = _leer(client, h, liq["id"])
    assert D(quedo["anticipos"]) == D(300000) and quedo["version"] == 2
    assert D(_leer(client, h, liq2["id"])["saldo_anterior"]) == D(200000)
    _anticipo_intacto(client, h, otro["id"], valor="10000.00", liquidacion_id=None)


def test_el_adelanto_borrado_no_resucita_dentro_de_una_quincena_pagada(client, base_datos):
    """Un adelanto anulado es una entrega que el dueño dijo que NO existió.

    Si el borrado pudiera entrar por esta puerta, se le descontarían al productor
    $60.000 que el propio sistema tiene marcados como inexistentes — y ni siquiera
    saldrían en la pantalla de anticipos para reclamarlos.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    s = _anticipo(client, h, prov, "2026-06-10", "60000", "el que se anuló")

    antes = _previsualizar(client, h, liq["id"]).json()["anticipos_sueltos"]
    assert s["id"] in {a["anticipo_id"] for a in antes}

    assert client.delete(f"{ANT}/{s['id']}", headers=h).status_code == 204

    despues = _previsualizar(client, h, liq["id"]).json()["anticipos_sueltos"]
    assert s["id"] not in {a["anticipo_id"] for a in despues}, "el borrado sigue de candidato"

    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[s["id"]])
    assert r.status_code in (404, 422), f"{r.status_code}: {r.text}"
    _intacta(client, h, liq["id"])


def test_el_adelanto_de_una_quincena_corregida_no_se_toca_por_la_otra_puerta(
    client, base_datos
):
    """El candado de la pantalla de anticipos NO se afloja por haber abierto este botón.

    Es la decisión escrita: la corrección se le pasa POR ENCIMA al candado, con sus
    cinco protecciones (permiso, motivo, cifra a la vista, versión y renglón). Lo que
    no se puede es abrir el candado de allá, donde no hay ninguna de las cinco.

    Medido con la plata: por esa puerta, el adelanto de $60.000 que este comprobante ya
    resta se podría volver $999.999 sin motivo escrito y sin folio nuevo.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    s = _anticipo(client, h, prov, "2026-06-10", "60000", "el del mercado")
    assert _corregir(client, h, liq["id"], anticipos_a_incluir=[s["id"]]).status_code == 200

    editar = client.put(f"{ANT}/{s['id']}", json={"valor": "999999"}, headers=h)
    assert editar.status_code in (403, 409, 422), (
        f"SE AFLOJÓ EL CANDADO de los anticipos: {editar.status_code} {editar.text[:200]}"
    )
    borrar = client.delete(f"{ANT}/{s['id']}", headers=h)
    assert borrar.status_code in (403, 409, 422), (
        f"se pudo BORRAR un adelanto que un comprobante ya resta: {borrar.status_code}"
    )

    _anticipo_intacto(client, h, s["id"], valor="60000.00", liquidacion_id=liq["id"])
    d = _leer(client, h, liq["id"])
    assert D(d["anticipos"]) == D(160000) and D(d["neto_a_pagar"]) == D(340000)


def test_el_adelanto_incluido_no_lo_vuelve_a_descontar_la_quincena_siguiente(
    client, base_datos
):
    """La marca hace su trabajo: lo que entró acá no lo cobra la siguiente.

    Es la otra mitad de "soltarlo lo devuelve a la fila". Si al incluirlo la marca
    quedara mal puesta, la corrida siguiente le volvería a restar los mismos $60.000 a
    un productor que recibió $60.000 una sola vez.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    s = _anticipo(client, h, prov, "2026-06-10", "60000", "el del mercado")
    assert _corregir(client, h, liq["id"], anticipos_a_incluir=[s["id"]]).status_code == 200

    _recepcion(client, h, prov, "2026-06-18", 200)  # $400.000 en Q2
    liq2 = _de(_generar(client, h, Q2), "Henri C")

    assert D(liq2["anticipos"]) == D(0), (
        f"la quincena siguiente volvió a descontar el mismo adelanto: {liq2['anticipos']}"
    )
    # Lo que sí le cobra es la deuda: $340.000 de neto contra $400.000 entregados.
    assert D(liq2["saldo_anterior"]) == D(60000), liq2["saldo_anterior"]
    assert D(liq2["neto_a_pagar"]) == D(340000), liq2["neto_a_pagar"]
    assert D(liq2["neto_a_pagar"]) == D(liq2["valor_total"]) - D(liq2["anticipos"]) - D(
        liq2["saldo_anterior"]
    )


# ===========================================================================
# 10. LOS CENTAVOS Y LA CADENA DE CORRECCIONES
# ===========================================================================
def test_tres_adelantos_con_centavos_suman_exacto_la_cifra_grande(client, base_datos):
    """$33.333,33 + $33.333,33 + $33.333,34 = $100.000,00 EXACTOS.

    El adelanto de $100.000 que se entregó en tres pedazos. Si la suma se hiciera con
    float o se redondeara por el camino, la cifra grande saldría en $99.999,99 o
    $100.000,01 y el dueño lo ve de una: él suma los tres renglones con calculadora
    contra el total de adelantos del comprobante.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, _ = _escenario(client, h)
    pedazos = [
        _anticipo(client, h, prov, f"2026-06-1{i}", v, f"pedazo {i + 1}")
        for i, v in enumerate(["33333.33", "33333.33", "33333.34"])
    ]

    r = _corregir(client, h, liq["id"], anticipos_a_incluir=[p["id"] for p in pedazos])
    assert r.status_code == 200, r.text
    d = r.json()

    assert D(d["anticipos"]) == D(200000), f"$100.000 viejos + $100.000 nuevos: {d['anticipos']}"
    assert D(d["neto_a_pagar"]) == D(300000), d["neto_a_pagar"]
    assert D(d["neto_a_pagar"]) == D(d["valor_total"]) - D(d["anticipos"]) - D(
        d["saldo_anterior"]
    )
    assert D(d["neto_a_pagar"]) == D(d["pagado"]) + D(d["saldo"])

    # EL DESGLOSE SUMA EXACTO LA DIFERENCIA de la cifra grande.
    corr = _correcciones(client, h, liq["id"])[0]
    assert sum(D(c["valor"]) for c in corr["anticipos_cambiados"]) == D(
        corr["anticipos_despues"]
    ) - D(corr["anticipos_antes"])


def test_dos_correcciones_seguidas_empalman_cifra_con_cifra(client, base_datos):
    """Tres hojas de la misma quincena, y las tres tienen que empalmar.

    El dueño puede corregir dos veces, y entonces el productor llega con una hoja
    vieja y hay que reconstruirle la historia. La condición mínima para poder hacerlo
    es que el `anticipos_despues` de una corrección sea EXACTAMENTE el
    `anticipos_antes` de la siguiente — sin esa cadena, el rastro no reconstruye nada.

        v1: $100.000 de adelanto           → neto $400.000
        v2: entra el del mercado ($60.000) → adelantos $160.000, neto $340.000
        v3: sale el de la droga ($100.000) → adelantos  $60.000, neto $440.000
    """
    h = auth_headers(client, "admin.a")
    prov, liq, aplicado = _escenario(client, h)
    mercado = _anticipo(client, h, prov, "2026-06-10", "60000", "el del mercado")

    una = _corregir(
        client, h, liq["id"], motivo="faltaba el del mercado",
        anticipos_a_incluir=[mercado["id"]],
    )
    assert una.status_code == 200, una.text
    assert D(una.json()["anticipos"]) == D(160000)

    dos = _corregir(
        client, h, liq["id"], motivo="el de la droga era de la quincena pasada",
        anticipos_a_soltar=[aplicado["id"]],
    )
    assert dos.status_code == 200, dos.text
    d = dos.json()
    assert D(d["anticipos"]) == D(60000), d["anticipos"]
    assert D(d["neto_a_pagar"]) == D(440000), d["neto_a_pagar"]
    assert D(d["neto_a_pagar"]) == D(d["pagado"]) + D(d["saldo"])
    assert D(d["saldo"]) == D(40000) and d["estado"] == "parcial"
    assert d["version"] == 3, d["version"]

    corr = _correcciones(client, h, liq["id"])
    assert len(corr) == 2, corr
    assert [c["version_nueva"] for c in corr] == [2, 3], corr
    assert D(corr[0]["anticipos_antes"]) == D(100000)
    assert D(corr[0]["anticipos_despues"]) == D(160000)
    assert D(corr[1]["anticipos_antes"]) == D(corr[0]["anticipos_despues"]), (
        "LA CADENA SE ROMPIÓ: el 'antes' de la segunda corrección no es el 'después' de "
        "la primera, y con eso no se reconstruye la hoja vieja del productor"
    )
    assert D(corr[1]["anticipos_despues"]) == D(60000)

    # Y el que salió quedó suelto, esperando a la quincena siguiente.
    _anticipo_intacto(client, h, aplicado["id"], valor="100000.00", liquidacion_id=None)
    _anticipo_intacto(client, h, mercado["id"], valor="60000.00", liquidacion_id=liq["id"])


# ===========================================================================
# 11. EL DEFECTO: UN FOLIO NUEVO POR UNA CORRECCIÓN QUE NO CORRIGIÓ NADA
# ===========================================================================
def test_una_correccion_que_no_cambia_una_sola_cifra_no_deberia_emitir_folio_nuevo(
    client, base_datos
):
    """Teclear el mismo valor que ya tenía el adelanto NO es una corrección.

    LO QUE SE MIDE. Se manda `valores_de_anticipos` con los $100.000 que el adelanto ya
    vale. Ninguna cifra del comprobante cambia —total $500.000, adelantos $100.000,
    neto $400.000, pagado $400.000, saldo $0— y el desglose de la corrección sale
    VACÍO: ni días, ni precios, ni adelantos.

    LO QUE CUESTA. La versión igual sube a 2. Eso significa, en el mundo: un folio
    '-v2', la banda 'COMPROBANTE CORREGIDO' impresa, y el propio sistema avisándole al
    dueño que "el productor puede tener 2 hojas de la misma quincena. Recójale las
    anteriores" — para que vaya hasta la finca a cambiar un papel por otro idéntico. Y
    de paso deja trabada por `version > 1` una quincena que nunca se corrigió.

    Cualquiera de las dos respuestas está bien y la prueba las acepta: rebotar ("no hay
    nada que corregir", que es el mensaje que ya existe) o no subir la versión.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, aplicado = _escenario(client, h)

    r = _corregir(
        client,
        h,
        liq["id"],
        motivo="me quedé mirando y lo dejé igual",
        valores_de_anticipos=[{"anticipo_id": aplicado["id"], "valor": "100000.00"}],
    )
    if r.status_code in (404, 422):
        _intacta(client, h, liq["id"])
        return

    assert r.status_code == 200, r.text
    d = r.json()
    # Primero se deja escrito que, en efecto, NO cambió una sola cifra.
    assert D(d["valor_total"]) == D(500000)
    assert D(d["anticipos"]) == D(100000)
    assert D(d["neto_a_pagar"]) == D(400000)
    assert D(d["pagado"]) == D(400000) and D(d["saldo"]) == D(0)
    corr = _correcciones(client, h, liq["id"])
    assert corr and corr[0]["anticipos_cambiados"] == []
    assert corr[0]["dias_agregados"] == [] and corr[0]["precios_corregidos"] == []
    assert D(corr[0]["anticipos_antes"]) == D(corr[0]["anticipos_despues"])

    # Y ACÁ ES DONDE FALLA: folio nuevo por una corrección que no corrigió nada.
    assert d["version"] == 1, (
        f"subió el folio a -v{d['version']} sin cambiar una cifra: al productor hay que "
        "recogerle el papel viejo para entregarle uno idéntico"
    )
