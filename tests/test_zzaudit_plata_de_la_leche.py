"""AUDITORÍA: LA PLATA DE LA LECHE, de punta a punta.

El recorrido que se mide: se anota la leche día por día -> se genera la liquidación
del productor -> se aprueba -> se le aplican los anticipos ya entregados -> se paga
(completa o por abonos) -> si queda debiendo, esa deuda se le cobra en la siguiente.

LA CUENTA DEL DUEÑO, la que suma con calculadora:

    anticipos entregados + plata pagada == leche liquidada

y renglón por renglón dentro de cada comprobante:

    valor bruto + bonificaciones - descuentos              = VALOR TOTAL
    VALOR TOTAL - anticipos - lo que quedó debiendo - pagado = SALDO

TODAS LAS CIFRAS DE ESTE ARCHIVO ESTÁN CALCULADAS A MANO en el docstring de cada
prueba, con cifras feas, y NO con el mismo código que se está midiendo.
"""
import io
import re
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
ANT = "/api/v1/anticipos"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


CERO = D(0)

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
Q3 = ("2026-07-01", "2026-07-15")
Q4 = ("2026-07-16", "2026-07-31")


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="1833.33"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros, *, precio=None, bonif=None, desc=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)}
    if precio is not None:
        cuerpo["precio_litro"] = str(precio)
    if bonif is not None:
        cuerpo["bonificaciones"] = str(bonif)
    if desc is not None:
        cuerpo["descuentos"] = str(desc)
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, fecha, valor, *, proveedor):
    r = client.post(
        ANT,
        json={
            "fecha": fecha,
            "valor": str(valor),
            "tipo": "proveedor",
            "proveedor_id": proveedor["id"],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, periodo, tipo="proveedor"):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": tipo},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _aprobar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _abonar(client, h, liq_id, fecha, valor):
    r = client.post(
        f"{API}/{liq_id}/pagos",
        json={"fecha": fecha, "valor": str(valor)},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _pagar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/pagar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _de(liquidaciones, nombre):
    encontradas = [liq for liq in liquidaciones if liq.get("proveedor_nombre") == nombre]
    assert len(encontradas) == 1, f"se esperaba una sola de {nombre}: {liquidaciones}"
    return encontradas[0]


def texto_pdf(contenido):
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    return texto_pdf(r.content)


_CIFRA = re.compile(r"(-?)\s*\$\s*(-?)([\d.]+(?:,\d{2})?)")


def renglon(papel, rotulo):
    inicio = papel.find(rotulo)
    assert inicio >= 0, f"el comprobante no trae el renglón «{rotulo}»:\n{papel}"
    resto = papel[inicio + len(rotulo):]
    encontrado = _CIFRA.search(resto)
    assert encontrado, f"el renglón «{rotulo}» salió sin cifra:\n{resto[:120]}"
    signo, signo_interno, cifra = encontrado.groups()
    valor = D(cifra.replace(".", "").replace(",", "."))
    return -valor if (signo == "-" or signo_interno == "-") else valor


# ------------------------------------------------------- invariantes del papel
def cuadra_el_comprobante(liq, donde=""):
    """Las dos restas del comprobante, al centavo. Ver el encabezado del archivo."""
    bruto = D(liq["valor_bruto"])
    bonif = D(liq["bonificaciones"])
    desc = D(liq["descuentos"])
    total = D(liq["valor_total"])
    assert bruto + bonif - desc == total, (
        f"{donde}: bruto {bruto} + bonif {bonif} - desc {desc} != VALOR TOTAL {total}"
    )

    suma_dias = sum((D(d["valor"]) for d in liq["detalles"]), CERO)
    assert suma_dias == total, (
        f"{donde}: la columna Valor suma {suma_dias} y el VALOR TOTAL dice {total}"
    )

    anticipos = D(liq["anticipos"])
    anterior = D(liq["saldo_anterior"])
    pagado = D(liq["pagado"])
    saldo = D(liq["saldo"])
    neto = D(liq["neto_a_pagar"])
    assert total - anticipos - anterior == neto, (
        f"{donde}: {total} - {anticipos} - {anterior} != neto {neto}"
    )
    assert neto - pagado == saldo, f"{donde}: neto {neto} - pagado {pagado} != saldo {saldo}"

    suma_pagos = sum((D(p["valor"]) for p in liq["pagos"]), CERO)
    assert suma_pagos == pagado, (
        f"{donde}: los abonos suman {suma_pagos} y la columna Pagado dice {pagado}"
    )

    suma_deudas = sum((D(x["le_queda_debiendo"]) for x in liq["deudas_cobradas"]), CERO)
    assert suma_deudas == anterior, (
        f"{donde}: el desglose de la deuda suma {suma_deudas} y el renglón dice {anterior}"
    )

    debe = D(liq["le_queda_debiendo"])
    assert debe == (-saldo if saldo < CERO else CERO), f"{donde}: le_queda_debiendo {debe} vs saldo {saldo}"


# ===========================================================================
# 1. EL DESGLOSE SUMA EXACTO DE ARRIBA ABAJO, con cifras feas
# ===========================================================================
def test_zzaudit_01_el_comprobante_suma_exacto_de_arriba_abajo(client, base_datos):
    """Henri C, quincena del 01 al 15 de junio. Calculado a mano:

        02/06  137,45 L x $1.833,33 = $251.991,2085 -> $251.991,21   (bonif $242,76)
        05/06   44,23 L x $1.922,77 =  $85.044,1171 ->  $85.044,12   (desc $1.833,33)
        11/06  219,45 L x $1.755,11 = $385.158,8895 -> $385.158,89

        valor bruto      = 251.991,21 + 85.044,12 + 385.158,89 = $722.194,22
        bonificaciones   = $242,76
        descuentos       = $1.833,33
        VALOR TOTAL      = 722.194,22 + 242,76 - 1.833,33 = $720.603,65

        anticipos        = 120.000,55 + 95.000,45 = $215.001,00
        NETO             = 720.603,65 - 215.001,00 = $505.602,65

        total litros     = 137,45 + 44,23 + 219,45 = 401,13 L
        precio promedio  = 722.194,22 / 401,13 = $1.800,3994... -> $1.800,40
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33", bonif="242.76")
    _recepcion(client, h, prov, "2026-06-05", "44.23", precio="1922.77", desc="1833.33")
    _recepcion(client, h, prov, "2026-06-11", "219.45", precio="1755.11")
    _anticipo(client, h, "2026-06-03", "120000.55", proveedor=prov)
    _anticipo(client, h, "2026-06-14", "95000.45", proveedor=prov)

    liq = _de(_generar(client, h, Q1), "Henri C")
    liq = _leer(client, h, liq["id"])

    assert D(liq["valor_bruto"]) == D("722194.22"), liq["valor_bruto"]
    assert D(liq["bonificaciones"]) == D("242.76")
    assert D(liq["descuentos"]) == D("1833.33")
    assert D(liq["valor_total"]) == D("720603.65"), liq["valor_total"]
    assert D(liq["anticipos"]) == D("215001.00"), liq["anticipos"]
    assert D(liq["neto_a_pagar"]) == D("505602.65"), liq["neto_a_pagar"]
    assert D(liq["total_litros"]) == D("401.13")
    assert D(liq["precio_promedio"]) == D("1800.40"), liq["precio_promedio"]
    cuadra_el_comprobante(liq, "recién generada")

    # Y EL PAPEL DICE LO MISMO QUE LA API, renglón por renglón.
    papel = _pdf(client, h, liq["id"])
    bruto = renglon(papel, "Valor bruto")
    bonif = renglon(papel, "Bonificaciones")
    desc = renglon(papel, "Descuentos")
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    saldo = renglon(papel, "SALDO A PAGAR")
    assert bruto + bonif + desc == total, f"papel: {bruto} + {bonif} + {desc} != {total}"
    assert total + anticipos == saldo, f"papel: {total} + {anticipos} != {saldo}"
    assert total == D("720603.65") and saldo == D("505602.65")


# ===========================================================================
# 2. ABONO POR ABONO: neto = pagado + saldo
# ===========================================================================
def test_zzaudit_02_abono_por_abono_el_neto_es_pagado_mas_saldo(client, base_datos):
    """Mismo comprobante de arriba: NETO $505.602,65 pagado en tres tandas.

        abono 1  $200.000,33  -> pagado 200.000,33  saldo 305.602,32
        abono 2  $150.000,11  -> pagado 350.000,44  saldo 155.602,21
        "Pagar"  $155.602,21  -> pagado 505.602,65  saldo        0
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33", bonif="242.76")
    _recepcion(client, h, prov, "2026-06-05", "44.23", precio="1922.77", desc="1833.33")
    _recepcion(client, h, prov, "2026-06-11", "219.45", precio="1755.11")
    _anticipo(client, h, "2026-06-03", "120000.55", proveedor=prov)
    _anticipo(client, h, "2026-06-14", "95000.45", proveedor=prov)

    liq = _de(_generar(client, h, Q1), "Henri C")
    _aprobar(client, h, liq["id"])

    liq = _abonar(client, h, liq["id"], "2026-06-16", "200000.33")
    assert D(liq["pagado"]) == D("200000.33")
    assert D(liq["saldo"]) == D("305602.32"), liq["saldo"]
    assert liq["estado"] == "parcial"
    cuadra_el_comprobante(_leer(client, h, liq["id"]), "abono 1")

    liq = _abonar(client, h, liq["id"], "2026-06-17", "150000.11")
    assert D(liq["pagado"]) == D("350000.44")
    assert D(liq["saldo"]) == D("155602.21"), liq["saldo"]
    cuadra_el_comprobante(_leer(client, h, liq["id"]), "abono 2")

    # Un abono de más que el saldo tiene que rebotar: es plata que no se debe.
    r = client.post(
        f"{API}/{liq['id']}/pagos",
        json={"fecha": "2026-06-18", "valor": "155602.22"},
        headers=h,
    )
    assert r.status_code >= 400, r.text

    liq = _pagar(client, h, liq["id"])
    assert D(liq["pagado"]) == D("505602.65"), liq["pagado"]
    assert D(liq["saldo"]) == CERO
    assert liq["estado"] == "pagada"
    final = _leer(client, h, liq["id"])
    cuadra_el_comprobante(final, "pagada")
    assert len(final["pagos"]) == 3

    # LA CUENTA DEL DUEÑO en una sola quincena:
    #   anticipos entregados + plata pagada == leche liquidada
    assert D(final["anticipos"]) + D(final["pagado"]) == D(final["valor_total"])


# ===========================================================================
# 3. LA CADENA DE QUINCENAS: la deuda no se pierde ni se cobra dos veces
# ===========================================================================
def test_zzaudit_03_cuatro_quincenas_encadenadas_la_cuenta_del_dueno(client, base_datos):
    """Marleny R, cuatro quincenas seguidas. Calculado a mano:

        Q1 01-15/06   44,23 L x $1.833,33 = $81.088,1859 -> $81.088,19
                      anticipo 04/06 $300.000,77
                      neto = 81.088,19 - 300.000,77 = -$218.912,58  (le queda debiendo)

        Q2 16-30/06  137,45 L x $1.833,33 = $251.991,2085 -> $251.991,21
                      saldo anterior = $218.912,58
                      neto = 251.991,21 - 0 - 218.912,58 = $33.078,63  -> se paga

        Q3 01-15/07   44,23 L x $1.922,77 = $85.044,1171 -> $85.044,12
                      anticipo 02/07 $100.000,05
                      neto = 85.044,12 - 100.000,05 = -$14.955,93  (le queda debiendo)

        Q4 16-31/07  219,45 L x $1.755,11 = $385.158,8895 -> $385.158,89
                      saldo anterior = $14.955,93
                      neto = 385.158,89 - 14.955,93 = $370.202,96  -> se paga

        LA CUENTA DEL DUEÑO al final de la cadena:
            leche liquidada  = 81.088,19 + 251.991,21 + 85.044,12 + 385.158,89
                             = $803.282,41
            anticipos        = 300.000,77 + 100.000,05 = $400.000,82
            pagado           =  33.078,63 + 370.202,96 = $403.281,59
            400.000,82 + 403.281,59 = $803.282,41  EXACTO
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")

    # ---- Q1: los anticipos se comen la quincena
    _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)
    q1 = _de(_generar(client, h, Q1), "Marleny R")
    q1 = _leer(client, h, q1["id"])
    assert D(q1["valor_total"]) == D("81088.19"), q1["valor_total"]
    assert D(q1["anticipos"]) == D("300000.77")
    assert D(q1["saldo"]) == D("-218912.58"), q1["saldo"]
    assert D(q1["le_queda_debiendo"]) == D("218912.58")
    cuadra_el_comprobante(q1, "Q1")
    _aprobar(client, h, q1["id"])
    # "Pagar" tiene que rebotar: no hay un peso que entregar.
    assert client.post(f"{API}/{q1['id']}/pagar", headers=h).status_code >= 400

    # ---- Q2: se le cobra la deuda de Q1
    _recepcion(client, h, prov, "2026-06-20", "137.45", precio="1833.33")
    q2 = _de(_generar(client, h, Q2), "Marleny R")
    q2 = _leer(client, h, q2["id"])
    assert D(q2["valor_total"]) == D("251991.21"), q2["valor_total"]
    assert D(q2["saldo_anterior"]) == D("218912.58"), q2["saldo_anterior"]
    assert D(q2["neto_a_pagar"]) == D("33078.63"), q2["neto_a_pagar"]
    assert len(q2["deudas_cobradas"]) == 1
    assert q2["deudas_cobradas"][0]["id"] == q1["id"]
    cuadra_el_comprobante(q2, "Q2")
    # y la Q1 quedó marcada, apuntando a la que se la cobró
    assert _leer(client, h, q1["id"])["deuda_trasladada_a_id"] == q2["id"]
    _aprobar(client, h, q2["id"])
    q2 = _pagar(client, h, q2["id"])
    assert D(q2["pagado"]) == D("33078.63")
    cuadra_el_comprobante(_leer(client, h, q2["id"]), "Q2 pagada")

    # ---- Q3: vuelve a quedar debiendo
    _recepcion(client, h, prov, "2026-07-03", "44.23", precio="1922.77")
    _anticipo(client, h, "2026-07-02", "100000.05", proveedor=prov)
    q3 = _de(_generar(client, h, Q3), "Marleny R")
    q3 = _leer(client, h, q3["id"])
    assert D(q3["valor_total"]) == D("85044.12"), q3["valor_total"]
    # LA DEUDA DE Q1 NO SE COBRA DOS VECES: ya la cobró Q2.
    assert D(q3["saldo_anterior"]) == CERO, q3["saldo_anterior"]
    assert D(q3["saldo"]) == D("-14955.93"), q3["saldo"]
    cuadra_el_comprobante(q3, "Q3")
    _aprobar(client, h, q3["id"])

    # ---- Q4: se le cobra la deuda de Q3, y SOLO la de Q3
    _recepcion(client, h, prov, "2026-07-20", "219.45", precio="1755.11")
    q4 = _de(_generar(client, h, Q4), "Marleny R")
    q4 = _leer(client, h, q4["id"])
    assert D(q4["saldo_anterior"]) == D("14955.93"), q4["saldo_anterior"]
    assert D(q4["neto_a_pagar"]) == D("370202.96"), q4["neto_a_pagar"]
    cuadra_el_comprobante(q4, "Q4")
    _aprobar(client, h, q4["id"])
    q4 = _pagar(client, h, q4["id"])

    # ---- LA CUENTA DEL DUEÑO sobre toda la cadena
    todas = [_leer(client, h, x["id"]) for x in (q1, q2, q3, q4)]
    leche = sum((D(x["valor_total"]) for x in todas), CERO)
    pagado = sum((D(x["pagado"]) for x in todas), CERO)
    anticipos = sum((D(x["anticipos"]) for x in todas), CERO)
    assert leche == D("803282.41"), leche
    assert anticipos == D("400000.82"), anticipos
    assert pagado == D("403281.59"), pagado
    assert anticipos + pagado == leche, f"{anticipos} + {pagado} != {leche}"


# ===========================================================================
# 4. UNA LIQUIDACIÓN PAGADA NO SE MUEVE POR NINGÚN CAMINO
# ===========================================================================
def test_zzaudit_04_la_pagada_no_se_mueve_por_ningun_camino(client, base_datos):
    """Comprobante pagado de $505.602,65 de neto. Se le empuja por TODAS las puertas
    que existen y ninguna le puede mover un peso."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    r1 = _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33", bonif="242.76")
    _recepcion(client, h, prov, "2026-06-05", "44.23", precio="1922.77", desc="1833.33")
    _recepcion(client, h, prov, "2026-06-11", "219.45", precio="1755.11")
    a1 = _anticipo(client, h, "2026-06-03", "120000.55", proveedor=prov)
    _anticipo(client, h, "2026-06-14", "95000.45", proveedor=prov)

    liq = _de(_generar(client, h, Q1), "Henri C")
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])
    antes = _leer(client, h, liq["id"])
    assert antes["estado"] == "pagada"
    assert D(antes["pagado"]) == D("505602.65")

    lid = liq["id"]
    detalle_id = antes["detalles"][0]["id"]
    puertas = {
        "recalcular": lambda: client.post(f"{API}/{lid}/recalcular", headers=h),
        "anular": lambda: client.post(f"{API}/{lid}/anular", headers=h),
        "aprobar de nuevo": lambda: client.post(f"{API}/{lid}/aprobar", headers=h),
        "pagar de nuevo": lambda: client.post(f"{API}/{lid}/pagar", headers=h),
        "abonar encima": lambda: client.post(
            f"{API}/{lid}/pagos", json={"fecha": "2026-06-20", "valor": "1000.11"}, headers=h
        ),
        "corregir el precio de un día": lambda: client.put(
            f"{API}/{lid}/detalles/{detalle_id}", json={"precio_litro": "1900.11"}, headers=h
        ),
        "cambiar los litros del día": lambda: client.put(
            f"{REC}/{r1['id']}", json={"cantidad_litros": "999.99"}, headers=h
        ),
        "cambiar el precio del día": lambda: client.put(
            f"{REC}/{r1['id']}", json={"precio_litro": "2100.77"}, headers=h
        ),
        "apagar el día": lambda: client.put(
            f"{REC}/{r1['id']}", json={"estado": "inactivo"}, headers=h
        ),
        "borrar el día": lambda: client.delete(f"{REC}/{r1['id']}", headers=h),
        "corregir el anticipo": lambda: client.put(
            f"{ANT}/{a1['id']}", json={"valor": "10.11"}, headers=h
        ),
        "borrar el anticipo": lambda: client.delete(f"{ANT}/{a1['id']}", headers=h),
    }
    fallas = []
    for nombre, disparo in puertas.items():
        respuesta = disparo()
        if respuesta.status_code < 400:
            fallas.append(f"{nombre} -> {respuesta.status_code}")
    despues = _leer(client, h, lid)
    for campo in (
        "valor_bruto", "bonificaciones", "descuentos", "valor_total",
        "anticipos", "saldo_anterior", "neto_a_pagar", "pagado", "saldo",
        "total_litros", "estado",
    ):
        assert despues[campo] == antes[campo], (
            f"la pagada se movió en {campo}: {antes[campo]} -> {despues[campo]} "
            f"(puertas que no rebotaron: {fallas})"
        )
    cuadra_el_comprobante(despues, "pagada tras el ataque")
    assert not fallas, f"puertas que dejaron tocar una liquidación pagada: {fallas}"


# ===========================================================================
# 5. ANTICIPO REGISTRADO TARDE
# ===========================================================================
def test_zzaudit_05_anticipo_registrado_tarde_lo_recoge_el_borrador(client, base_datos):
    """Se genera la quincena y DESPUÉS se registra el anticipo que ya se entregó.

        VALOR TOTAL      = 137,45 x 1.833,33 = $251.991,21
        anticipo tardío  = $85.000,77 (fecha 06/06, dentro del período)
        neto esperado    = 251.991,21 - 85.000,77 = $166.990,44
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-06", "137.45", precio="1833.33")
    liq = _de(_generar(client, h, Q1), "Henri C")
    assert D(liq["anticipos"]) == CERO
    assert D(liq["neto_a_pagar"]) == D("251991.21")

    _anticipo(client, h, "2026-06-06", "85000.77", proveedor=prov)
    # El borrador no se entera solo: hay que recalcularlo (o aprobarlo).
    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    assert r.status_code == 200, r.text
    tras = _leer(client, h, liq["id"])
    assert D(tras["anticipos"]) == D("85000.77"), tras["anticipos"]
    assert D(tras["neto_a_pagar"]) == D("166990.44"), tras["neto_a_pagar"]
    cuadra_el_comprobante(tras, "tras recalcular")

    # Y APROBAR TAMBIÉN LOS BARRE: un segundo anticipo entre medio.
    _anticipo(client, h, "2026-06-09", "1833.33", proveedor=prov)
    aprobada = _aprobar(client, h, liq["id"])
    assert D(aprobada["anticipos"]) == D("86834.10"), aprobada["anticipos"]
    assert D(aprobada["neto_a_pagar"]) == D("165157.11"), aprobada["neto_a_pagar"]
    cuadra_el_comprobante(_leer(client, h, liq["id"]), "tras aprobar")

    # UN ANTICIPO DE JULIO NO SE LE DESCUENTA A LA QUINCENA DE JUNIO.
    _anticipo(client, h, "2026-07-02", "50000.55", proveedor=prov)
    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    # (aprobada: recalcular rebota; se mide que la cifra no cambió por ningún lado)
    assert D(_leer(client, h, liq["id"])["anticipos"]) == D("86834.10")


# ===========================================================================
# 6. DÍAS APAGADOS Y DÍAS CORREGIDOS DESPUÉS DE LIQUIDAR
# ===========================================================================
def test_zzaudit_06_dia_apagado_y_dia_corregido_despues_de_liquidar(client, base_datos):
    """Quincena aprobada de $722.194,22 de bruto (los tres días de la prueba 1).

        Se APAGA el día del 05/06 ($85.044,12 con $1.833,33 de descuento):
            bruto  = 722.194,22 - 85.044,12 = $637.150,10
            desc   = 1.833,33 - 1.833,33    = $0,00
            total  = 637.150,10 + 242,76    = $637.392,86
        Se CORRIGE el 11/06 de 219,45 L a 137,45 L a $1.755,11:
            137,45 x 1.755,11 = $241.239,8695 -> $241.239,87
            bruto  = 251.991,21 + 241.239,87 = $493.231,08
            total  = 493.231,08 + 242,76     = $493.473,84
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33", bonif="242.76")
    r2 = _recepcion(client, h, prov, "2026-06-05", "44.23", precio="1922.77", desc="1833.33")
    r3 = _recepcion(client, h, prov, "2026-06-11", "219.45", precio="1755.11")
    liq = _de(_generar(client, h, Q1), "Henri C")
    _aprobar(client, h, liq["id"])

    apagar = client.put(f"{REC}/{r2['id']}", json={"estado": "inactivo"}, headers=h)
    assert apagar.status_code == 200, apagar.text
    tras = _leer(client, h, liq["id"])
    assert tras["estado"] == "borrador", tras["estado"]
    assert D(tras["valor_bruto"]) == D("637150.10"), tras["valor_bruto"]
    assert D(tras["descuentos"]) == CERO, tras["descuentos"]
    assert D(tras["valor_total"]) == D("637392.86"), tras["valor_total"]
    assert len(tras["detalles"]) == 2, tras["detalles"]
    cuadra_el_comprobante(tras, "día apagado")

    corregir = client.put(f"{REC}/{r3['id']}", json={"cantidad_litros": "137.45"}, headers=h)
    assert corregir.status_code == 200, corregir.text
    tras = _leer(client, h, liq["id"])
    assert D(tras["valor_bruto"]) == D("493231.08"), tras["valor_bruto"]
    assert D(tras["valor_total"]) == D("493473.84"), tras["valor_total"]
    cuadra_el_comprobante(tras, "día corregido")


# ===========================================================================
# 7. ANULAR Y VOLVER A GENERAR
# ===========================================================================
def test_zzaudit_07_anular_la_que_cobro_la_deuda_no_la_pierde(client, base_datos):
    """Q1 deja debiendo $218.912,58; Q2 se los cobra; se ANULA Q2 y se vuelve a
    generar: la deuda tiene que volver a aparecer, igual y una sola vez."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")
    _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)
    q1 = _de(_generar(client, h, Q1), "Marleny R")
    _aprobar(client, h, q1["id"])

    _recepcion(client, h, prov, "2026-06-20", "137.45", precio="1833.33")
    q2 = _de(_generar(client, h, Q2), "Marleny R")
    assert D(q2["saldo_anterior"]) == D("218912.58")

    r = client.post(f"{API}/{q2['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    anulada = _leer(client, h, q2["id"])
    assert anulada["estado"] == "anulada"
    # La anulada ya no cobra nada, y su propio resumen tiene que seguir cuadrando.
    assert D(anulada["saldo_anterior"]) == CERO, anulada["saldo_anterior"]
    cuadra_el_comprobante(anulada, "Q2 anulada")
    # Y la deuda de Q1 quedó libre otra vez.
    assert _leer(client, h, q1["id"])["deuda_trasladada_a_id"] is None

    q2b = _de(_generar(client, h, Q2), "Marleny R")
    q2b = _leer(client, h, q2b["id"])
    assert D(q2b["valor_total"]) == D("251991.21"), q2b["valor_total"]
    assert D(q2b["saldo_anterior"]) == D("218912.58"), q2b["saldo_anterior"]
    assert D(q2b["neto_a_pagar"]) == D("33078.63")
    cuadra_el_comprobante(q2b, "Q2 regenerada")
    # UNA SOLA VEZ: nadie más se está cobrando esa deuda.
    assert len(q2b["deudas_cobradas"]) == 1


# ===========================================================================
# 8. VARIOS PRODUCTORES EN LA MISMA CORRIDA, PRECIOS DISTINTOS POR DÍA
# ===========================================================================
def test_zzaudit_08_tres_productores_precios_distintos_por_dia(client, base_datos):
    """Tres productores en la misma corrida, cada día a su precio.

        Henri C   02/06 137,45 x 1.833,33 = 251.991,21
                  09/06  44,23 x 1.755,11 =  77.628,52  (1.755,11*44,23 = 77.628,5153)
                  total  $329.619,73
        Marleny R 03/06 219,45 x 1.922,77 = 421.951,88  (1.922,77*219,45=421.951,8765)
                  total  $421.951,88
        Ovidio T  04/06  44,23 x 1.833,33 =  81.088,19
                  bonificación $242,76 -> total $81.330,95
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    marleny = _proveedor(client, h, "Marleny R")
    ovidio = _proveedor(client, h, "Ovidio T")
    _recepcion(client, h, henri, "2026-06-02", "137.45", precio="1833.33")
    _recepcion(client, h, henri, "2026-06-09", "44.23", precio="1755.11")
    _recepcion(client, h, marleny, "2026-06-03", "219.45", precio="1922.77")
    _recepcion(client, h, ovidio, "2026-06-04", "44.23", precio="1833.33", bonif="242.76")

    generadas = _generar(client, h, Q1)
    assert len(generadas) == 3, generadas
    esperado = {
        "Henri C": D("329619.73"),
        "Marleny R": D("421951.88"),
        "Ovidio T": D("81330.95"),
    }
    for nombre, total in esperado.items():
        liq = _leer(client, h, _de(generadas, nombre)["id"])
        assert D(liq["valor_total"]) == total, f"{nombre}: {liq['valor_total']} != {total}"
        cuadra_el_comprobante(liq, nombre)

    # La leche de un productor no se le mete a otro.
    suma = sum((D(_leer(client, h, x["id"])["valor_total"]) for x in generadas), CERO)
    assert suma == D("832902.56"), suma


# ===========================================================================
# 9. LA DEUDA NO SE COBRA DOS VECES AUNQUE SE GENEREN LAS QUINCENAS AL REVÉS
# ===========================================================================
def test_zzaudit_09_deuda_una_sola_vez_generando_al_reves(client, base_datos):
    """Q1 deja debiendo $218.912,58. Se genera PRIMERO Q3 (julio) y después Q2:
    la deuda la cobra UNA sola de las dos, y la otra sale en cero."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")
    _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)
    q1 = _de(_generar(client, h, Q1), "Marleny R")
    _aprobar(client, h, q1["id"])

    _recepcion(client, h, prov, "2026-06-20", "137.45", precio="1833.33")
    _recepcion(client, h, prov, "2026-07-03", "219.45", precio="1755.11")

    q3 = _de(_generar(client, h, Q3), "Marleny R")
    q3 = _leer(client, h, q3["id"])
    q2 = _de(_generar(client, h, Q2), "Marleny R")
    q2 = _leer(client, h, q2["id"])

    cobrada = D(q3["saldo_anterior"]) + D(q2["saldo_anterior"])
    assert cobrada == D("218912.58"), (
        f"la deuda se cobró {cobrada} entre Q2 ({q2['saldo_anterior']}) "
        f"y Q3 ({q3['saldo_anterior']})"
    )
    cuadra_el_comprobante(q2, "Q2 al revés")
    cuadra_el_comprobante(q3, "Q3 al revés")


# ===========================================================================
# 10. LA MISMA QUINCENA DOS VECES NO DUPLICA LA LECHE
# ===========================================================================
def test_zzaudit_10_generar_dos_veces_no_duplica_la_leche(client, base_datos):
    """Oprimir "Generar" dos veces sobre el mismo período no puede sacar un segundo
    comprobante con la misma leche."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    primera = _generar(client, h, Q1)
    assert len(primera) == 1
    segunda = _generar(client, h, Q1)
    assert segunda == [] or all(
        D(x["valor_total"]) == CERO for x in segunda
    ), f"la segunda corrida sacó comprobante: {segunda}"

    listado = client.get(f"{API}?limit=100", headers=h).json()["items"]
    vivas = [x for x in listado if x["estado"] != "anulada"]
    total = sum((D(x["valor_total"]) for x in vivas), CERO)
    assert total == D("251991.21"), f"la leche se contó {total} en {len(vivas)} papeles"
