"""LO QUE EL TERCERO QUEDÓ DEBIENDO SE LE COBRA EN LA LIQUIDACIÓN SIGUIENTE.

Lo pidió el dueño con estas palabras: "necesito que en la liquidación, a los que
quedaron en negativo, ese saldo que se queda debiendo —es decir, el proveedor a la
quesera— se cobre en la siguiente liquidación".

DE DÓNDE SALE ESE NEGATIVO, con las cifras del caso real: los anticipos que se le
entregaron en la mano suman más que lo que valió su quincena. $180.000 de leche contra
$300.000 de anticipo ya entregado -> el proveedor le quedó debiendo $120.000. Hasta
antes de este cambio eso solo se DECÍA (el rótulo "LE QUEDA DEBIENDO" del comprobante,
ver tests/test_liquidacion_saldo_negativo.py): no había nada que lo cobrara después, y
la plata se quedaba escrita en un papel viejo.

CÓMO QUEDÓ LA CUENTA:

    neto_a_pagar = valor_total - anticipos - saldo_anterior

`saldo_anterior` es lo que quedó debiendo de quincenas pasadas, y la liquidación que
dejó la deuda queda MARCADA (`deuda_trasladada_a_id`) con el id de la que se la cobró.
Esa marca es lo que hace imposible cobrar la misma deuda dos veces, y es el mismo
idioma que el proyecto ya usa para las recepciones y los anticipos.

TODAS LAS CIFRAS DE ESTE ARCHIVO ESTÁN CALCULADAS A MANO en el docstring de cada
prueba, no con el mismo código que se está probando.
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


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="1800"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ruta(client, h, nombre):
    r = client.post(
        "/api/v1/rutas", json={"nombre": nombre, "municipio": "Granada"}, headers=h
    )
    assert r.status_code == 201, r.text
    return r.json()


def _transportador(client, h, nombre, rutas):
    r = client.post(
        "/api/v1/transportadores",
        json={
            "nombre": nombre,
            "valor_transporte": "0",
            "rutas": [{"ruta_id": ru["id"], "valor_transporte": str(v)} for ru, v in rutas],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros, *, precio=None, bonif=None, desc=None,
               t=None, ruta=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)}
    if precio is not None:
        cuerpo["precio_litro"] = str(precio)
    if bonif is not None:
        cuerpo["bonificaciones"] = str(bonif)
    if desc is not None:
        cuerpo["descuentos"] = str(desc)
    if t:
        cuerpo["transportador_id"] = t["id"]
    if ruta:
        cuerpo["ruta_id"] = ruta["id"]
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, fecha, valor, *, proveedor=None, transportador=None):
    cuerpo = {"fecha": fecha, "valor": str(valor)}
    if proveedor:
        cuerpo.update(tipo="proveedor", proveedor_id=proveedor["id"])
    else:
        cuerpo.update(tipo="transportador", transportador_id=transportador["id"])
    r = client.post(ANT, json=cuerpo, headers=h)
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


def _pagar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/pagar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _de(liquidaciones, nombre):
    """La liquidación de ESE tercero, buscada por nombre en la respuesta de generar."""
    encontradas = [
        liq
        for liq in liquidaciones
        if (liq.get("proveedor_nombre") or liq.get("transportador_nombre")) == nombre
    ]
    assert len(encontradas) == 1, f"se esperaba una sola de {nombre}: {liquidaciones}"
    return encontradas[0]


def texto_pdf(contenido):
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    return texto_pdf(r.content)


def _avance_pdf(client, h, periodo, tercero, tipo="proveedor"):
    """El PDF PRELIMINAR del avance ("¿cómo voy?"), el que se le muestra al tercero."""
    r = client.post(
        f"{API}/previsualizar/pdf",
        json={
            "periodo_inicio": periodo[0],
            "periodo_fin": periodo[1],
            "tipo": tipo,
            "tercero_id": tercero["id"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    return r


# El renglón del resumen del comprobante, con SU SIGNO: "- $8.468,53" vale -8468.53.
# Se lee del papel impreso y no de la API a propósito: lo que el dueño suma a mano son
# los caracteres que salieron en la hoja, y es ahí donde un renglón mal ubicado —o un
# signo que no está— hace que la resta no le dé.
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


# ===========================================================================
# 1. EL CASO DEL DUEÑO, CON LAS CIFRAS EN LA MANO
# ===========================================================================
def test_el_caso_del_dueno_la_deuda_de_la_quincena_pasada_se_cobra_en_la_siguiente(
    client, base_datos
):
    """Las cifras del dueño, calculadas a mano:

    QUINCENA 1 (01 al 15 de junio) — Henri C, 100 L a $1.800:
        valor total                        $180.000
        anticipo YA ENTREGADO EN LA MANO  -$300.000
        neto a pagar                      -$120.000
        -> HENRI LE QUEDA DEBIENDO         $120.000   (no sale un peso de la caja)

    QUINCENA 2 (16 al 30 de junio) — 100 L a $2.500:
        valor total                        $250.000
        anticipos                                $0
        lo que quedó debiendo la pasada   -$120.000
        neto a pagar                       $130.000   <- ESTO ES LO QUE SE LE PAGA
        pagado                            -$130.000
        saldo                                    $0

    Y la cuenta grande, la que el dueño hace de memoria: $180.000 + $250.000 de leche
    = $430.000; menos los $300.000 que ya le entregó = $130.000. Es exactamente lo que
    se le paga en la segunda quincena, ni un peso más.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")

    _recepcion(client, h, henri, "2026-06-02", "100")  # 100 L x $1.800 = $180.000
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    print("\n===== 1. EL CASO DEL DUEÑO =====")
    print(f"  QUINCENA 1 · total={q1['valor_total']} anticipos={q1['anticipos']} "
          f"neto={q1['neto_a_pagar']} le_queda_debiendo={q1['le_queda_debiendo']}")
    assert D(q1["valor_total"]) == D("180000")
    assert D(q1["anticipos"]) == D("300000")
    assert D(q1["neto_a_pagar"]) == D("-120000")
    assert D(q1["le_queda_debiendo"]) == D("120000")
    assert D(q1["saldo_anterior"]) == CERO, "la primera quincena no arrastra nada"

    # La deuda solo viaja desde una liquidación EN FIRME.
    _aprobar(client, h, q1["id"])

    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")  # $250.000
    q2 = _generar(client, h, Q2)[0]
    print(f"  QUINCENA 2 · total={q2['valor_total']} anticipos={q2['anticipos']} "
          f"saldo_anterior={q2['saldo_anterior']} neto={q2['neto_a_pagar']}")

    assert D(q2["valor_total"]) == D("250000")
    assert D(q2["anticipos"]) == CERO
    assert D(q2["saldo_anterior"]) == D("120000"), (
        "la deuda de la quincena pasada no se cobró: el dueño le pagaría $250.000 "
        "teniendo $120.000 a favor"
    )
    assert D(q2["neto_a_pagar"]) == D("130000")

    # LAS DOS PUNTAS SE VEN, que es lo que el dueño necesita para no quedarse
    # mirando un descuento sin explicación.
    assert q1["id"] in [o["id"] for o in q2["deudas_cobradas"]]
    assert D(q2["deudas_cobradas"][0]["le_queda_debiendo"]) == D("120000")
    assert q2["deudas_cobradas"][0]["periodo_texto"] == "01/06/2026 al 15/06/2026"
    # Y EL DESGLOSE SUMA EXACTO LA CIFRA GRANDE: la regla de oro del proyecto.
    assert sum((D(o["le_queda_debiendo"]) for o in q2["deudas_cobradas"]), CERO) == D(
        q2["saldo_anterior"]
    )

    q1_despues = _leer(client, h, q1["id"])
    print(f"  la quincena 1 quedó marcada · se le cobró en "
          f"{q1_despues['deuda_trasladada_a']['periodo_texto']}")
    assert q1_despues["deuda_trasladada_a_id"] == q2["id"]
    assert q1_despues["deuda_trasladada_a"]["periodo_texto"] == "16/06/2026 al 30/06/2026"

    # Y se le paga: $130.000, no $250.000.
    _aprobar(client, h, q2["id"])
    pagada = _pagar(client, h, q2["id"])
    print(f"  se le pagó · pagado={pagada['pagado']} saldo={pagada['saldo']} "
          f"estado={pagada['estado']}")
    assert D(pagada["pagado"]) == D("130000"), (
        "se le pagó una cifra que no es el neto: la deuda de la quincena pasada no "
        "quedó descontada"
    )
    assert D(pagada["saldo"]) == CERO
    assert pagada["estado"] == "pagada"
    # La invariante de siempre, exacta: neto a pagar = pagado + saldo.
    assert D(pagada["neto_a_pagar"]) == D(pagada["pagado"]) + D(pagada["saldo"])


def test_el_comprobante_dice_de_donde_salio_el_descuento_y_la_otra_donde_se_cobro(
    client, base_datos
):
    """Las dos puntas, en los DOS papeles. Sin esto el dueño ve un descuento de
    $120.000 en un comprobante y no tiene de dónde sacar de qué quincena salió, y en
    el otro ve "LE QUEDA DEBIENDO $120.000" sin saber si eso ya se cobró o si todavía
    tiene que ir a cobrarlo.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]

    papel2 = _pdf(client, h, q2["id"])
    print("\n===== 2. LOS DOS PAPELES =====")
    print(f"  el que COBRA: ...{papel2[papel2.find('Lo que'):][:150]}...")
    assert "Lo que quedó debiendo de la quincena pasada" in papel2, papel2
    assert "$120.000" in papel2
    assert "01/06/2026 al 15/06/2026" in papel2, (
        "el comprobante descuenta $120.000 y no dice de qué quincena vienen"
    )

    papel1 = _pdf(client, h, q1["id"])
    print(f"  el que DEBE : ...{papel1[papel1.find('LE QUEDA DEBIENDO'):][:150]}...")
    assert "LE QUEDA DEBIENDO" in papel1
    assert "16/06/2026 al 30/06/2026" in papel1, (
        "el comprobante dice que le queda debiendo y no dice dónde se le cobró"
    )
    assert "no hay que volver a cobrarlo" in papel1


# ===========================================================================
# 2. TRES QUINCENAS SEGUIDAS EN NEGATIVO: LA DEUDA NO SE COBRA DOS VECES
# ===========================================================================
def test_tres_quincenas_seguidas_en_negativo_no_cobran_la_deuda_dos_veces(
    client, base_datos
):
    """El peligro de la cadena, con las cifras a mano:

    QUINCENA 1 (01-15/06): 100 L a $1.800 = $180.000, anticipo $300.000
        neto = 180.000 - 300.000 = -120.000   -> queda debiendo $120.000
    QUINCENA 2 (16-30/06): 50 L a $1.800 = $90.000, sin anticipos nuevos
        neto = 90.000 - 0 - 120.000 = -30.000 -> queda debiendo $30.000
    QUINCENA 3 (01-15/07): 100 L a $1.800 = $180.000
        neto = 180.000 - 0 - 30.000 = $150.000  <- se le paga esto

    LO QUE ESTA PRUEBA VIGILA: que la tercera cobre $30.000 y NO $150.000. El
    remanente de la segunda ($30.000) YA TRAE ADENTRO la deuda vieja —los $120.000
    están restados en su propio neto—, así que volver a sumar la deuda de la primera
    sería cobrarle dos veces al proveedor.

    Y LA CUENTA GRANDE, la que cuadra el dueño: $180.000 + $90.000 + $180.000 =
    $450.000 de leche en las tres quincenas; menos los $300.000 de anticipo que ya le
    entregó = $150.000. Es exactamente lo que se le paga, y se le paga una sola vez.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")  # $180.000
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])

    _recepcion(client, h, henri, "2026-06-20", "50")  # $90.000
    q2 = _generar(client, h, Q2)[0]
    _aprobar(client, h, q2["id"])

    _recepcion(client, h, henri, "2026-07-02", "100")  # $180.000
    q3 = _generar(client, h, Q3)[0]

    print("\n===== 3. TRES QUINCENAS SEGUIDAS =====")
    for nombre, liq in (("Q1", q1), ("Q2", q2), ("Q3", q3)):
        leida = _leer(client, h, liq["id"])
        print(f"  {nombre} · total={leida['valor_total']} ant={leida['anticipos']} "
              f"saldo_anterior={leida['saldo_anterior']} neto={leida['neto_a_pagar']} "
              f"debe={leida['le_queda_debiendo']} -> trasladada_a="
              f"{'sí' if leida['deuda_trasladada_a_id'] else 'no'}")

    q2 = _leer(client, h, q2["id"])
    assert D(q2["valor_total"]) == D("90000")
    assert D(q2["saldo_anterior"]) == D("120000")
    assert D(q2["neto_a_pagar"]) == D("-30000")
    assert D(q2["le_queda_debiendo"]) == D("30000")

    assert D(q3["valor_total"]) == D("180000")
    assert D(q3["saldo_anterior"]) == D("30000"), (
        f"la tercera quincena se cobró {q3['saldo_anterior']}: la deuda de la primera "
        "se está cobrando dos veces (una en la segunda y otra acá)"
    )
    assert D(q3["neto_a_pagar"]) == D("150000")
    # La tercera solo cobra a la SEGUNDA: la primera ya la cobró la segunda.
    assert [o["id"] for o in q3["deudas_cobradas"]] == [q2["id"]]

    # EL COMPROBANTE DEL MEDIO LLEVA LAS DOS NOTAS, que es el caso de la cadena: cobró
    # la deuda de la primera y dejó la suya, que se cobró en la tercera. Un papel que
    # dijera solo una de las dos cosas deja al dueño sin la mitad del hilo.
    papel2 = _pdf(client, h, q2["id"])
    print(f"  el comprobante del medio dice: "
          f"...{papel2[papel2.find('El renglón'):][:230]}...")
    assert "01/06/2026 al 15/06/2026" in papel2, papel2
    assert "01/07/2026 al 15/07/2026" in papel2, papel2
    assert "LE QUEDA DEBIENDO" in papel2
    assert "Lo que quedó debiendo de la quincena pasada" in papel2

    _aprobar(client, h, q3["id"])
    pagada = _pagar(client, h, q3["id"])
    total_pagado = sum(
        (D(_leer(client, h, liq["id"])["pagado"]) for liq in (q1, q2, q3)), CERO
    )
    print(f"  TOTAL entregado en las tres quincenas = {total_pagado} "
          f"(leche $450.000 - anticipo $300.000 = $150.000)")
    assert D(pagada["pagado"]) == D("150000")
    assert total_pagado == D("150000"), (
        "sumando las tres quincenas se le entregó una cifra distinta a "
        "leche - anticipos: en algún lado se cobró o se pagó dos veces"
    )


def test_generar_dos_veces_el_mismo_periodo_no_cobra_la_deuda_dos_veces(
    client, base_datos
):
    """El botón oprimido dos veces (o el navegador que reintenta). $120.000 de deuda.

    La segunda corrida no tiene recepciones nuevas, así que no genera nada.

    Y SI APARECE UN DÍA NUEVO DEL MISMO PERÍODO, la corrida SALTA A ESE TERCERO Y LO
    REPORTA, nombrando la liquidación que ya existe. Antes salía un segundo comprobante
    del mismo período que —eso sí— nacía con $0 de deuda arrastrada, así que la deuda no
    se cobraba dos veces; pero por ese mismo camino —dos liquidaciones del mismo tercero
    con los períodos montados— se le pagaba completo a quien todavía debía: $500.000 de la
    caja por $380.000 de leche, medidos. Hoy ese documento no nace (ver
    `_omitido_por_periodo_cruzado`) y la deuda sigue cobrada UNA sola vez.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])

    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    print("\n===== 4. GENERAR DOS VECES =====")
    otra_vez = _generar(client, h, Q2)
    print(f"  segunda corrida sin días nuevos -> {len(otra_vez)} liquidaciones")
    assert otra_vez == [], "volvió a generar sobre recepciones ya liquidadas"
    q2 = _leer(client, h, q2["id"])
    assert D(q2["saldo_anterior"]) == D("120000"), "la deuda se cobró dos veces"

    # Ahora sí aparece un día nuevo del mismo período: a Henri se lo salta nombrando la
    # liquidación que ya lo cubre —y lo dice en `omitidas`—, y la deuda sigue cobrada una
    # sola vez.
    _recepcion(client, h, henri, "2026-06-25", "10")
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": Q2[0], "periodo_fin": Q2[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    corrida = r.json()
    omitida = corrida["omitidas"][0]
    print(f"  con un día nuevo -> generadas={len(corrida['generadas'])} "
          f"omitidas={len(corrida['omitidas'])}: {omitida['motivo'][:120]}")
    assert corrida["generadas"] == []
    assert omitida["motivo_codigo"] == "periodo_cruzado"
    assert omitida["tercero_nombre"] == "Henri C" and omitida["cuenta"] == "leche"
    assert (
        "Henri C ya tiene una liquidación de leche del 16/06/2026 al 30/06/2026"
        in omitida["motivo"]
    )
    assert D(_leer(client, h, q2["id"])["saldo_anterior"]) == D("120000"), (
        "la deuda se cobró dos veces"
    )
    # Y no nació un segundo comprobante del mismo período.
    listado = client.get(f"{API}?page=1&page_size=50", headers=h).json()["items"]
    vivas = [liq for liq in listado if liq["estado"] != "anulada"]
    assert len(vivas) == 2, [
        (liq["periodo_inicio"], liq["periodo_fin"], liq["estado"]) for liq in vivas
    ]


# ===========================================================================
# 3. DESHACER: DONDE ESTO SE VUELVE PELIGROSO
# ===========================================================================
def test_anular_la_que_cobro_la_deuda_la_suelta_y_la_siguiente_se_la_cobra(
    client, base_datos
):
    """Si la que se cobró la deuda se anula y la deuda no se suelta, el proveedor
    queda debiendo $120.000 que nadie va a volver a cobrar nunca: la consulta de
    deudas salta a las que están marcadas.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    print("\n===== 5. ANULAR LA QUE COBRÓ =====")
    r = client.post(f"{API}/{q2['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    soltada = _leer(client, h, q1["id"])
    print(f"  después de anular · q1.deuda_trasladada_a_id="
          f"{soltada['deuda_trasladada_a_id']} debe={soltada['le_queda_debiendo']}")
    assert soltada["deuda_trasladada_a_id"] is None, (
        "la deuda quedó marcada contra una liquidación anulada: esos $120.000 no los "
        "vuelve a cobrar nadie"
    )
    assert soltada["deuda_trasladada_a"] is None
    assert D(soltada["le_queda_debiendo"]) == D("120000")

    # Y la próxima que se genere se la vuelve a cobrar (al anular, sus días quedaron
    # libres, así que el mismo período se puede volver a liquidar).
    q2b = _generar(client, h, Q2)[0]
    print(f"  la nueva del mismo período cobra {q2b['saldo_anterior']}")
    assert D(q2b["saldo_anterior"]) == D("120000")
    assert D(q2b["neto_a_pagar"]) == D("130000")


def test_borrar_en_suave_la_que_cobro_la_deuda_tambien_la_suelta(
    client, base_datos, db_session
):
    """Lo mismo que anular, por el otro camino: el borrado en suave.

    Se entra por el servicio porque no hay endpoint que borre liquidaciones, y así
    tiene que quedar cubierto igual: el día que se exponga, la deuda no se puede
    quedar marcada contra un documento que las consultas ya no devuelven.
    """
    from app.core.context import RequestContext
    from app.modules.liquidaciones.service import LiquidacionService

    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    empresa = base_datos["empresa_a"]
    servicio = LiquidacionService(db_session, RequestContext(empresa_id=empresa.id))
    import uuid as _uuid

    servicio.eliminar(_uuid.UUID(q2["id"]))
    db_session.flush()

    soltada = _leer(client, h, q1["id"])
    print("\n===== 6. BORRAR EN SUAVE LA QUE COBRÓ =====")
    print(f"  q1.deuda_trasladada_a_id={soltada['deuda_trasladada_a_id']} "
          f"debe={soltada['le_queda_debiendo']}")
    assert soltada["deuda_trasladada_a_id"] is None
    assert D(soltada["le_queda_debiendo"]) == D("120000")


def test_anular_el_origen_con_la_deuda_ya_cobrada_rebota_nombrando_la_otra(
    client, base_datos
):
    """Cambiarle la cifra a una deuda YA COBRADA descuadra dos comprobantes de una.

    La liquidación 1 quedó debiendo $120.000 y esos $120.000 están restados en el
    comprobante de la quincena 2, que puede estar pagado y en la mano del proveedor.
    Anular la 1 dejaría a la 2 cobrando una deuda de un documento anulado.

    Y EL MENSAJE TIENE QUE NOMBRAR LA OTRA CON SU PERÍODO, porque lo que el dueño
    necesita saber es qué anular primero.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    _generar(client, h, Q2)

    print("\n===== 7. ANULAR EL ORIGEN YA COBRADO =====")
    r = client.post(f"{API}/{q1['id']}/anular", headers=h)
    detalle = r.json()["error"]["detail"]
    print(f"  anular  -> {r.status_code} · {detalle}")
    assert r.status_code == 422, r.text
    assert "16/06/2026 al 30/06/2026" in detalle, detalle
    assert "$120.000" in detalle, detalle
    assert _leer(client, h, q1["id"])["estado"] == "aprobada"


def test_recalcular_el_origen_con_la_deuda_ya_cobrada_rebota_nombrando_la_otra(
    client, base_datos
):
    """Igual que anularlo: recalcular el origen le movería la cifra a la deuda que ya
    se cobró en otro comprobante. Rebota, y el mensaje nombra a la otra.

    Se prueba por los DOS caminos que llegan a recalcular:
      · el botón "Recalcular" del comprobante;
      · y el recuadre automático que dispara editar un día de esa quincena en
        Recepción diaria, que es por donde de verdad llega (ese camino la devolvía a
        borrador y la recalculaba sin preguntar).
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    rec = _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    _generar(client, h, Q2)

    print("\n===== 8. RECALCULAR EL ORIGEN YA COBRADO =====")
    r = client.post(f"{API}/{q1['id']}/recalcular", headers=h)
    detalle = r.json()["error"]["detail"]
    print(f"  recalcular -> {r.status_code} · {detalle}")
    assert r.status_code == 422, r.text
    assert "16/06/2026 al 30/06/2026" in detalle, detalle

    # Y por el lado del día: cambiarle los litros a una recepción de esa quincena.
    r = client.put(f"{REC}/{rec['id']}", json={"cantidad_litros": "120"}, headers=h)
    detalle = r.json()["error"]["detail"]
    print(f"  editar el día -> {r.status_code} · {detalle}")
    assert r.status_code == 422, r.text
    assert "16/06/2026 al 30/06/2026" in detalle, detalle
    assert "anule primero esa liquidación" in detalle.lower(), detalle
    # La cifra no se movió ni un peso.
    assert D(_leer(client, h, q1["id"])["valor_total"]) == D("180000")

    # Y la pantalla lo dice con el mismo motivo, no con un "ya se pagó" que sería
    # mentira: por este día no salió un peso.
    celda = client.get(f"{REC}/{rec['id']}", headers=h).json()
    print(f"  aviso de la pantalla: {celda['candado_aviso']}")
    assert celda["leche_pagada"] is True, (
        "la grilla deja el día abierto y el guardia lo rebota: la pantalla y el "
        "backend tienen que decir lo mismo"
    )
    assert "quedó debiendo" in celda["candado_aviso"]
    assert "16/06/2026 al 30/06/2026" in celda["candado_aviso"]


def test_corregir_el_anticipo_de_una_deuda_ya_cobrada_rebota(client, base_datos):
    """El otro camino que le movía la cifra en silencio: corregir el ANTICIPO.

    Bajar el anticipo de $300.000 a $200.000 dejaría la deuda en $20.000, y el
    comprobante de la quincena siguiente —que ya descontó $120.000— quedaría cobrando
    $100.000 que ya nadie debe.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]

    print("\n===== 9. CORREGIR EL ANTICIPO YA COBRADO =====")
    r = client.put(f"{ANT}/{ant['id']}", json={"valor": "200000"}, headers=h)
    detalle = r.json()["error"]["detail"]
    print(f"  editar el anticipo -> {r.status_code} · {detalle}")
    assert r.status_code == 422, r.text
    assert "16/06/2026 al 30/06/2026" in detalle, detalle

    r = client.delete(f"{ANT}/{ant['id']}", headers=h)
    print(f"  borrar el anticipo -> {r.status_code}")
    assert r.status_code == 422, r.text

    # Nada se movió, ni en la vieja ni en la nueva.
    assert D(_leer(client, h, q1["id"])["anticipos"]) == D("300000")
    assert D(_leer(client, h, q2["id"])["saldo_anterior"]) == D("120000")


# ===========================================================================
# 4. DEL BORRADOR SÍ VIAJA LA DEUDA (esta expectativa estaba al revés)
# ===========================================================================
# ESTA PRUEBA DECÍA LO CONTRARIO Y ESTABA EQUIVOCADA. Se llamaba
# `test_de_un_borrador_no_viaja_la_deuda_pero_no_se_pierde` y exigía que la deuda de un
# borrador NO se cobrara, con el argumento de que un borrador todavía puede cambiar de
# cifra. El argumento suena bien y PIERDE PLATA DE VERDAD: la deuda es el anticipo que
# ya salió de la caja, y el estado dice si el dueño aprobó las CIFRAS, no si la deuda
# existe. Está medido en las dos pruebas de sobrepago de la sección 9.
#
# Lo que hace segura la cobranza no es exigir aprobación —son las otras dos cosas, que
# ya estaban: al cobrarla el origen queda CONGELADO por todos los lados, y si el dueño
# se equivocó anula la nueva y la deuda vuelve a quedar libre—.
def test_del_borrador_si_viaja_la_deuda_porque_la_plata_ya_salio(client, base_datos):
    """La deuda de un BORRADOR se cobra igual, y el borrador queda congelado.

    Cifras a mano:
        QUINCENA 1 (01-15/06), sin aprobar: 100 L a $1.800 = $180.000 con $300.000 de
            anticipo ya entregado -> queda debiendo $120.000
        QUINCENA 2 (16-30/06): 100 L a $2.500 = $250.000
            - lo que quedó debiendo la pasada                -$120.000
            neto a pagar                                     $130.000

    Y desde que se le cobra, el borrador del origen queda con las cifras congeladas:
    recalcularlo rebota nombrando la quincena que se las cobró.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]  # queda en BORRADOR: no se aprueba
    assert q1["estado"] == "borrador"
    assert D(q1["le_queda_debiendo"]) == D("120000")

    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    print("\n===== 10. DEL BORRADOR SÍ VIAJA =====")
    print(f"  q1 en {q1['estado']} -> q2.saldo_anterior={q2['saldo_anterior']} "
          f"neto={q2['neto_a_pagar']}")
    assert D(q2["saldo_anterior"]) == D("120000"), (
        "no se cobró la deuda de un borrador: esos $120.000 son un anticipo que YA "
        "salió de la caja, y si nadie los cobra el dueño le paga dos veces la misma leche"
    )
    assert D(q2["neto_a_pagar"]) == D("130000")

    # Y el origen quedó congelado desde el instante en que se le cobró.
    q1_despues = _leer(client, h, q1["id"])
    print(f"  q1 quedó marcada -> se le cobró en "
          f"{q1_despues['deuda_trasladada_a']['periodo_texto']}")
    assert q1_despues["deuda_trasladada_a_id"] == q2["id"]
    assert q1_despues["estado"] == "borrador", "cobrarle la deuda no le cambia el estado"
    r = client.post(f"{API}/{q1['id']}/recalcular", headers=h)
    print(f"  recalcular el borrador congelado -> {r.status_code}")
    assert r.status_code == 422, r.text
    assert "16/06/2026 al 30/06/2026" in r.json()["error"]["detail"]


def test_aprobar_el_borrador_congelado_no_le_barre_un_anticipo_nuevo(client, base_datos):
    """El último camino que le movía las cifras al origen por detrás.

    Aprobar barre los anticipos pendientes del tercero, y eso está bien: un anticipo
    registrado después de generar no se puede quedar por fuera. Pero si la deuda del
    borrador YA se cobró en otra quincena, barrerle un anticipo nuevo le cambiaría la
    deuda a un comprobante ya emitido.

    Cifras a mano:
        QUINCENA 1 (01-15/06), borrador: $180.000 de leche - $300.000 de anticipo
            -> queda debiendo $120.000
        QUINCENA 2 (16-30/06): $250.000 - $120.000 = neto $130.000
        se registra un anticipo NUEVO de $50.000 con fecha 10/06 y se aprueba la 1:
            si se lo barriera -> anticipos $350.000 y deuda $170.000, y el comprobante
            de la 2 quedaría cobrando $50.000 de menos.
        Lo que tiene que pasar: la 1 se queda en $300.000 de anticipos y debiendo
            $120.000, y el anticipo nuevo queda SUELTO para la próxima quincena.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    nuevo = _anticipo(client, h, "2026-06-10", "50000", proveedor=henri)
    _aprobar(client, h, q1["id"])
    q1_despues = _leer(client, h, q1["id"])
    print("\n===== 10.b APROBAR EL BORRADOR CONGELADO =====")
    print(f"  q1 · anticipos={q1_despues['anticipos']} "
          f"debe={q1_despues['le_queda_debiendo']} estado={q1_despues['estado']}")
    assert D(q1_despues["anticipos"]) == D("300000"), (
        "aprobar le barrió un anticipo nuevo a una quincena cuya deuda ya está "
        "restada en otro comprobante"
    )
    assert D(q1_despues["le_queda_debiendo"]) == D("120000")
    assert D(_leer(client, h, q2["id"])["saldo_anterior"]) == D("120000")

    # Y el anticipo nuevo no se perdió: sigue suelto y lo recoge la próxima.
    suelto = client.get(f"{ANT}/{nuevo['id']}", headers=h).json()
    print(f"  el anticipo nuevo quedó suelto: liquidacion_id={suelto['liquidacion_id']}")
    assert suelto["liquidacion_id"] is None
    _recepcion(client, h, henri, "2026-07-02", "100")  # $180.000
    q3 = _generar(client, h, Q3)[0]
    print(f"  q3 · anticipos={q3['anticipos']} neto={q3['neto_a_pagar']}")
    assert D(q3["anticipos"]) == D("50000"), "el anticipo nuevo se perdió"
    assert D(q3["neto_a_pagar"]) == D("130000")  # 180.000 - 50.000 - 0


# ===========================================================================
# 5. NO SE MEZCLAN TERCEROS, NI TIPOS, NI EMPRESAS
# ===========================================================================
def test_no_se_mezclan_los_terceros_henri_no_paga_lo_de_henri_c(client, base_datos):
    """Henri y Henri C son DOS productores. La búsqueda va por id y nunca por nombre.

    Cifras: Henri C queda debiendo $120.000 (100 L a $1.800 con $300.000 de anticipo).
    Henri no debe nada (100 L a $1.800, sin anticipos). En la quincena siguiente los
    dos entregan 100 L a $2.500 = $250.000, y solo a Henri C se le descuentan los
    $120.000: su neto es $130.000 y el de Henri, $250.000.
    """
    h = auth_headers(client, "admin.a")
    henri_c = _proveedor(client, h, "Henri C")
    henri = _proveedor(client, h, "Henri")
    _recepcion(client, h, henri_c, "2026-06-02", "100")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri_c)

    primeras = _generar(client, h, Q1)
    for liq in primeras:
        _aprobar(client, h, liq["id"])
    assert D(_de(primeras, "Henri C")["le_queda_debiendo"]) == D("120000")
    assert D(_de(primeras, "Henri")["le_queda_debiendo"]) == CERO

    _recepcion(client, h, henri_c, "2026-06-20", "100", precio="2500")
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    segundas = _generar(client, h, Q2)
    de_henri_c = _de(segundas, "Henri C")
    de_henri = _de(segundas, "Henri")
    print("\n===== 11. HENRI Y HENRI C NO SE MEZCLAN =====")
    print(f"  Henri C · saldo_anterior={de_henri_c['saldo_anterior']} "
          f"neto={de_henri_c['neto_a_pagar']}")
    print(f"  Henri   · saldo_anterior={de_henri['saldo_anterior']} "
          f"neto={de_henri['neto_a_pagar']}")
    assert D(de_henri_c["saldo_anterior"]) == D("120000")
    assert D(de_henri_c["neto_a_pagar"]) == D("130000")
    assert D(de_henri["saldo_anterior"]) == CERO, (
        "a Henri le descontaron la deuda de Henri C: son dos productores distintos"
    )
    assert D(de_henri["neto_a_pagar"]) == D("250000")


def test_no_se_mezclan_los_tipos_la_deuda_del_flete_no_se_le_cobra_a_la_leche(
    client, base_datos
):
    """Proveedor con proveedor, transportador con transportador: son dos cuentas y dos
    comprobantes distintos, y cada uno se cobra en el suyo.

    Cifras: Alex recoge 100 L en Nápoles a $1,00 el litro = $100 de flete, con $600 de
    anticipo ya entregado -> ALEX LE QUEDA DEBIENDO $500. La leche de esos 100 L es de
    Patricia, a $1.800 = $180.000, y ella no debe nada.

    En la quincena siguiente: el flete de Alex vale $100 y la leche de Patricia
    $180.000. A la de Alex se le cobran sus $500 (neto -$400: vuelve a quedar
    debiendo); a la de Patricia, nada.
    """
    h = auth_headers(client, "admin.a")
    napoles = _ruta(client, h, "Napoles")
    alex = _transportador(client, h, "Alex", [(napoles, "1")])
    patricia = _proveedor(client, h, "Patricia")
    _recepcion(client, h, patricia, "2026-06-02", "100", t=alex, ruta=napoles)
    _anticipo(client, h, "2026-06-01", "600", transportador=alex)

    primeras = _generar(client, h, Q1, tipo="ambos")
    flete1 = _de(primeras, "Alex")
    leche1 = _de(primeras, "Patricia")
    print("\n===== 12. FLETE Y LECHE NO SE MEZCLAN =====")
    print(f"  Q1 flete · total={flete1['valor_total']} ant={flete1['anticipos']} "
          f"debe={flete1['le_queda_debiendo']}")
    assert D(flete1["valor_total"]) == D("100")
    assert D(flete1["le_queda_debiendo"]) == D("500")
    assert D(leche1["le_queda_debiendo"]) == CERO
    for liq in primeras:
        _aprobar(client, h, liq["id"])

    _recepcion(client, h, patricia, "2026-06-20", "100", t=alex, ruta=napoles)
    segundas = _generar(client, h, Q2, tipo="ambos")
    flete2 = _de(segundas, "Alex")
    leche2 = _de(segundas, "Patricia")
    print(f"  Q2 flete · saldo_anterior={flete2['saldo_anterior']} "
          f"neto={flete2['neto_a_pagar']}")
    print(f"  Q2 leche · saldo_anterior={leche2['saldo_anterior']} "
          f"neto={leche2['neto_a_pagar']}")
    assert D(flete2["saldo_anterior"]) == D("500")
    assert D(flete2["neto_a_pagar"]) == D("-400")
    assert D(leche2["saldo_anterior"]) == CERO, (
        "a la liquidación de LA LECHE le cobraron la deuda DEL FLETE: son dos "
        "comprobantes de dos personas distintas"
    )
    assert D(leche2["neto_a_pagar"]) == D("180000")


def test_no_se_mezclan_las_empresas(client, base_datos):
    """La deuda de un proveedor de la Quesera A no aparece en la Quesera B.

    Se prueba con el mismo nombre en las dos empresas, que es el caso en que un filtro
    olvidado se nota: la de B tiene que salir con $0 de deuda arrastrada.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    henri_a = _proveedor(client, ha, "Henri C")
    henri_b = _proveedor(client, hb, "Henri C")

    _recepcion(client, ha, henri_a, "2026-06-02", "100")
    _anticipo(client, ha, "2026-06-01", "300000", proveedor=henri_a)
    q1a = _generar(client, ha, Q1)[0]
    _aprobar(client, ha, q1a["id"])
    assert D(q1a["le_queda_debiendo"]) == D("120000")

    _recepcion(client, hb, henri_b, "2026-06-20", "100", precio="2500")
    q2b = _generar(client, hb, Q2)[0]
    print("\n===== 13. LAS EMPRESAS NO SE MEZCLAN =====")
    print(f"  la de la Quesera B · saldo_anterior={q2b['saldo_anterior']} "
          f"neto={q2b['neto_a_pagar']}")
    assert D(q2b["saldo_anterior"]) == CERO
    assert D(q2b["neto_a_pagar"]) == D("250000")
    assert _leer(client, ha, q1a["id"])["deuda_trasladada_a_id"] is None


# ===========================================================================
# 6. EL COMPROBANTE SUMA DE ARRIBA ABAJO, CON CIFRAS FEAS Y CON CENTAVOS
# ===========================================================================
def test_el_resumen_del_pdf_suma_de_arriba_abajo_hasta_la_cifra_grande(
    client, base_datos
):
    """El papel, renglón por renglón, con cifras feas y centavos. TODO A MANO:

    QUINCENA 1 — Marleny, 44,23 L a $1.837,77:
        44,23 x 1.837,77 = 81.284,5671 -> valor bruto     $81.284,57
        + bonificaciones                                   $1.234,56
        - descuentos                                         -$987,65
        VALOR TOTAL                                       $81.531,48
        - anticipo ya entregado                          -$90.000,01
        neto a pagar                                      -$8.468,53
        -> LE QUEDA DEBIENDO                               $8.468,53

    QUINCENA 2 — 77,77 L a $2.345,67:
        77,77 x 2.345,67 = 182.422,7559 -> valor bruto    $182.422,76
        + bonificaciones                                       $0,07
        - descuentos                                          -$1,13
        VALOR TOTAL                                      $182.421,70
        - anticipos                                            $0,00
        - lo que quedó debiendo la quincena pasada         -$8.468,53
        neto a pagar                                     $173.953,17
        - pagado (un abono parcial)                      -$50.000,55
        SALDO A PAGAR                                    $123.952,62

    LO QUE VIGILA ESTA PRUEBA: que los renglones del comprobante, sumados EN EL ORDEN
    EN QUE ESTÁN IMPRESOS, caigan exacto en VALOR TOTAL y en el SALDO. El orden viejo
    ponía "Anticipos aplicados" y "Pagado" ARRIBA de VALOR TOTAL y el dueño, que suma
    de arriba abajo, no llegaba nunca a la cifra grande.
    """
    h = auth_headers(client, "admin.a")
    marleny = _proveedor(client, h, "Marleny", precio="1837.77")
    _recepcion(client, h, marleny, "2026-06-02", "44.23", bonif="1234.56", desc="987.65")
    _anticipo(client, h, "2026-06-01", "90000.01", proveedor=marleny)
    q1 = _generar(client, h, Q1)[0]
    assert D(q1["valor_bruto"]) == D("81284.57"), q1["valor_bruto"]
    assert D(q1["valor_total"]) == D("81531.48"), q1["valor_total"]
    assert D(q1["le_queda_debiendo"]) == D("8468.53"), q1["le_queda_debiendo"]
    _aprobar(client, h, q1["id"])

    _recepcion(
        client, h, marleny, "2026-06-20", "77.77", precio="2345.67", bonif="0.07", desc="1.13"
    )
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["valor_bruto"]) == D("182422.76"), q2["valor_bruto"]
    assert D(q2["valor_total"]) == D("182421.70"), q2["valor_total"]
    assert D(q2["saldo_anterior"]) == D("8468.53"), (
        f"la deuda viajó redondeada: {q2['saldo_anterior']} en vez de $8.468,53"
    )
    assert D(q2["neto_a_pagar"]) == D("173953.17"), q2["neto_a_pagar"]

    _aprobar(client, h, q2["id"])
    r = client.post(
        f"{API}/{q2['id']}/pagos",
        json={"fecha": "2026-07-01", "valor": "50000.55"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert D(r.json()["saldo"]) == D("123952.62"), r.json()["saldo"]

    papel = _pdf(client, h, q2["id"])
    bruto = renglon(papel, "Valor bruto")
    bonif = renglon(papel, "Bonificaciones")
    desc = renglon(papel, "Descuentos")
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    deuda = renglon(papel, "Lo que quedó debiendo de la quincena pasada")
    pagado = renglon(papel, "Pagado")
    saldo = renglon(papel, "SALDO A PAGAR")

    print("\n===== 14. EL RESUMEN DEL PDF, SUMADO DE ARRIBA ABAJO =====")
    print(f"    Valor bruto                                  {bruto}")
    print(f"    Bonificaciones                               {bonif}")
    print(f"    Descuentos                                   {desc}")
    print(f"    VALOR TOTAL                                  {total}")
    print(f"    Anticipos aplicados                          {anticipos}")
    print(f"    Lo que quedó debiendo de la quincena pasada   {deuda}")
    print(f"    Pagado                                       {pagado}")
    print(f"    SALDO A PAGAR                                {saldo}")

    assert bruto + bonif + desc == total, (
        f"los renglones de arriba no dan VALOR TOTAL: {bruto} + {bonif} + {desc} = "
        f"{bruto + bonif + desc} contra {total}"
    )
    assert total + anticipos + deuda + pagado == saldo, (
        f"de VALOR TOTAL para abajo no se llega al saldo: {total} + {anticipos} + "
        f"{deuda} + {pagado} = {total + anticipos + deuda + pagado} contra {saldo}"
    )
    # Y las cifras del papel son EXACTAMENTE las de arriba, escritas a mano.
    assert total == D("182421.70")
    assert deuda == D("-8468.53")
    assert saldo == D("123952.62")
    # LOS DOS RENGLONES QUE ESTABAN EN EL ORDEN EQUIVOCADO: ahora van DEBAJO de la
    # cifra grande, que es como el dueño lee el papel.
    assert papel.index("VALOR TOTAL") < papel.index("Anticipos aplicados"), papel
    assert papel.index("Anticipos aplicados") < papel.index("Lo que quedó debiendo"), papel
    assert papel.index("Lo que quedó debiendo") < papel.index("Pagado"), papel
    assert papel.index("Pagado") < papel.index("SALDO A PAGAR"), papel


def test_el_comprobante_del_transportador_tambien_suma_de_arriba_abajo(
    client, base_datos
):
    """El mismo cuadre en el comprobante del flete, que tiene otro resumen.

    Cifras a mano: Alex recoge 44,23 L en Nápoles a $242,76 el litro.
        44,23 x 242,76 = 10.737,2748 -> VALOR TOTAL       $10.737,27
        - anticipo ya entregado                           -$5.000,00
        neto de la quincena 1                              $5.737,27

    Se le corrige la tarifa a $1,00 y se recalcula: el flete cae a $44,23 y el
    anticipo de $5.000 ya no cabe -> ALEX LE QUEDA DEBIENDO $4.955,77.

    QUINCENA 2, 44,23 L a $1,00 = $44,23:
        VALOR TOTAL                                           $44,23
        - anticipos                                            $0,00
        - lo que quedó debiendo la quincena pasada         -$4.955,77
        neto a pagar                                      -$4.911,54  (sigue debiendo)
    """
    h = auth_headers(client, "admin.a")
    napoles = _ruta(client, h, "Napoles")
    alex = _transportador(client, h, "Alex", [(napoles, "242.76")])
    prov = _proveedor(client, h, "Patricia")
    _recepcion(client, h, prov, "2026-06-02", "44.23", t=alex, ruta=napoles)
    _anticipo(client, h, "2026-06-01", "5000", transportador=alex)
    q1 = _generar(client, h, Q1, tipo="transportador")[0]
    assert D(q1["valor_total"]) == centavos(D("44.23") * D("242.76")) == D("10737.27")

    # Se corrige la tarifa y se recalcula (el botón del dueño).
    r = client.put(
        f"/api/v1/transportadores/{alex['id']}",
        json={"rutas": [{"ruta_id": napoles["id"], "valor_transporte": "1"}]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert client.post(f"{API}/{q1['id']}/recalcular", headers=h).status_code == 200
    q1 = _leer(client, h, q1["id"])
    assert D(q1["le_queda_debiendo"]) == D("4955.77"), q1["le_queda_debiendo"]
    _aprobar(client, h, q1["id"])

    _recepcion(client, h, prov, "2026-06-20", "44.23", t=alex, ruta=napoles)
    q2 = _generar(client, h, Q2, tipo="transportador")[0]
    print("\n===== 15. EL COMPROBANTE DEL FLETE =====")
    print(f"  total={q2['valor_total']} saldo_anterior={q2['saldo_anterior']} "
          f"neto={q2['neto_a_pagar']} debe={q2['le_queda_debiendo']}")
    assert D(q2["valor_total"]) == D("44.23")
    assert D(q2["saldo_anterior"]) == D("4955.77")
    assert D(q2["neto_a_pagar"]) == D("-4911.54")

    papel = _pdf(client, h, q2["id"])
    transporte = renglon(papel, "Valor transporte")
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    deuda = renglon(papel, "Lo que quedó debiendo de la quincena pasada")
    debe = renglon(papel, "LE QUEDA DEBIENDO")
    print(f"    Valor transporte {transporte} · VALOR TOTAL {total} · anticipos "
          f"{anticipos} · deuda {deuda} · LE QUEDA DEBIENDO {debe}")
    assert transporte == total
    # El último renglón va en POSITIVO con el rótulo volteado, así que la resta de
    # arriba abajo tiene que dar su negativo.
    assert total + anticipos + deuda == -debe, (
        f"{total} + {anticipos} + {deuda} = {total + anticipos + deuda} contra "
        f"-{debe}"
    )
    assert debe == D(q2["le_queda_debiendo"])


def test_la_liquidacion_de_siempre_no_cambia_de_papel(client, base_datos):
    """El 99% de los comprobantes: nadie debe nada de antes.

    El renglón de la deuda NO aparece —un "$0,00" ahí solo hace ruido— y el resumen
    sigue cuadrando de arriba abajo. Cifras: 100 L a $1.800 = $180.000 con $50.000 de
    anticipo -> saldo $130.000.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "50000", proveedor=henri)
    liq = _generar(client, h, Q1)[0]
    assert D(liq["saldo"]) == D("130000")
    assert D(liq["saldo_anterior"]) == CERO
    assert liq["deudas_cobradas"] == []

    papel = _pdf(client, h, liq["id"])
    print("\n===== 16. LA LIQUIDACIÓN DE SIEMPRE =====")
    print(f"  ...{papel[papel.find('Valor bruto'):][:160]}...")
    assert "Lo que quedó debiendo" not in papel, (
        "el comprobante normal salió con un renglón de deuda que no existe"
    )
    bruto = renglon(papel, "Valor bruto")
    bonif = renglon(papel, "Bonificaciones")
    desc = renglon(papel, "Descuentos")
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    saldo = renglon(papel, "SALDO A PAGAR")
    assert bruto + bonif + desc == total == D("180000")
    assert total + anticipos == saldo == D("130000")


# ===========================================================================
# 7. LA INVARIANTE DE SIEMPRE SIGUE EXACTA
# ===========================================================================
def test_la_invariante_neto_igual_pagado_mas_saldo_sigue_exacta_con_la_deuda(
    client, base_datos
):
    """`neto_a_pagar = pagado + saldo` en cada paso, y con centavos.

    Es la igualdad que el dueño verifica a mano contra el comprobante, y ahora el neto
    tiene una resta más adentro (la deuda arrastrada): si `saldo` se calculara en algún
    camino sin restarla, la igualdad se rompe y el papel deja de cuadrar.
    """
    h = auth_headers(client, "admin.a")
    marleny = _proveedor(client, h, "Marleny", precio="1837.77")
    _recepcion(client, h, marleny, "2026-06-02", "44.23")
    _anticipo(client, h, "2026-06-01", "90000.01", proveedor=marleny)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, marleny, "2026-06-20", "77.77", precio="2345.67")
    q2 = _generar(client, h, Q2)[0]
    _aprobar(client, h, q2["id"])

    print("\n===== 17. LA INVARIANTE, PASO A PASO =====")
    for abono in ("0.01", "1234.56", "50000"):
        r = client.post(
            f"{API}/{q2['id']}/pagos",
            json={"fecha": "2026-07-01", "valor": abono},
            headers=h,
        )
        assert r.status_code == 200, r.text
        leida = _leer(client, h, q2["id"])
        print(f"  abono {abono:>9} · neto={leida['neto_a_pagar']} "
              f"pagado={leida['pagado']} saldo={leida['saldo']}")
        assert D(leida["neto_a_pagar"]) == D(leida["pagado"]) + D(leida["saldo"]), leida
        assert D(leida["neto_a_pagar"]) == (
            D(leida["valor_total"]) - D(leida["anticipos"]) - D(leida["saldo_anterior"])
        ), leida


def test_por_pagar_es_lo_que_hay_que_sacar_y_lo_que_le_deben_va_aparte(
    client, base_datos
):
    """"Liquidaciones por pagar" en el tablero y en el balance: UNA SOLA CIFRA Y BIEN.

    ESTA PRUEBA CAMBIÓ DE EXPECTATIVA. Antes exigía que la deuda sin cobrar entrara en
    "por pagar" con su signo negativo (esperaba -$120.000), o sea que la cifra fuera el
    NETO entre lo que la quesera debe y lo que le deben. Eso contesta una pregunta que el
    dueño no hace: cuando él lee "por pagar" quiere saber CUÁNTA PLATA TIENE QUE SACAR.

    Las cifras del caso, dos proveedores a la vez:
        Doña Rosa (01-15/06): 100 L a $1.800 = $180.000, sin anticipos
            -> saldo +$130.000 después de un anticipo de $50.000   <- SÍ hay que pagarle
        Henri C  (01-15/06): 100 L a $1.800 = $180.000 con $300.000 de anticipo
            -> saldo -$120.000                                     <- él quedó debiendo

        POR PAGAR             = $130.000   (solo el positivo: es lo que sale de la caja)
        LE QUEDAN DEBIENDO    = $120.000   (aparte, en positivo y con su nombre)

        Sumando los dos saldos daba $10.000, y con eso el tablero decía que la quincena
        se paga con diez mil pesos.

    Y DESPUÉS DE COBRAR LA DEUDA EN LA QUINCENA SIGUIENTE la misma plata no se cuenta dos
    veces: la de Henri pasa a valer $250.000 con $120.000 ya restados -> saldo $130.000,
    y su deuda vieja sale de "le quedan debiendo" porque ya está cobrada.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    rosa = _proveedor(client, h, "Dona Rosa")
    _recepcion(client, h, henri, "2026-06-02", "100")  # $180.000
    _recepcion(client, h, rosa, "2026-06-02", "100")  # $180.000
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    _anticipo(client, h, "2026-06-01", "50000", proveedor=rosa)
    primeras = _generar(client, h, Q1)
    for liq in primeras:
        _aprobar(client, h, liq["id"])
    assert D(_de(primeras, "Dona Rosa")["saldo"]) == D("130000")
    assert D(_de(primeras, "Henri C")["saldo"]) == D("-120000")

    def cifras():
        tablero = client.get("/api/v1/reportes/dashboard", headers=h).json()
        balance = client.get("/api/v1/contabilidad/balance", headers=h).json()
        # Las dos pantallas tienen que decir lo mismo: son la misma pregunta.
        for campo in ("liquidaciones_por_pagar", "terceros_le_quedan_debiendo"):
            assert D(tablero[campo]) == D(balance[campo]), campo
        return (
            D(tablero["liquidaciones_por_pagar"]),
            D(tablero["terceros_le_quedan_debiendo"]),
        )

    print("\n===== 19. EL TABLERO: DOS PREGUNTAS, DOS CIFRAS =====")
    por_pagar, le_deben = cifras()
    print(f"  por pagar={por_pagar} · le quedan debiendo={le_deben}")
    assert por_pagar == D("130000"), (
        f"por pagar muestra {por_pagar}: está mezclando el saldo negativo de Henri con "
        "el positivo de Doña Rosa, y esa suma no es la plata que hay que sacar"
    )
    assert le_deben == D("120000"), (
        "lo que el tercero le quedó debiendo tiene que verse, aparte y en positivo"
    )

    # Se le cobra la deuda a Henri en la quincena siguiente.
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")  # $250.000
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo"]) == D("130000")
    por_pagar, le_deben = cifras()
    print(f"  después de cobrarla · por pagar={por_pagar} · le quedan debiendo={le_deben}")
    assert por_pagar == D("260000"), (
        f"por pagar muestra {por_pagar}: los $130.000 de Doña Rosa más los $130.000 de "
        "la quincena nueva de Henri son $260.000"
    )
    assert le_deben == CERO, (
        "la deuda ya cobrada sigue contando como si el tercero la debiera: está restada "
        "adentro del saldo de la quincena que se la cobró"
    )


def test_recalcular_la_que_cobro_la_deuda_no_le_borra_el_saldo_anterior(
    client, base_datos
):
    """`saldo_anterior` NO se recalcula: no sale de las recepciones, es una plata que se
    arrastra de otro documento que ya quedó marcado. Pero el neto y el saldo sí tienen
    que quedar bien después.

    Cifras: la quincena 2 vale $250.000 con $120.000 de deuda arrastrada (neto
    $130.000). Se le corrige un día de 100 L a 120 L a $2.500 = $300.000, y el neto
    tiene que quedar en $300.000 - $120.000 = $180.000.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    rec2 = _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["neto_a_pagar"]) == D("130000")

    # Se corrige el día (esto recuadra la liquidación por dentro) y se recalcula.
    r = client.put(f"{REC}/{rec2['id']}", json={"cantidad_litros": "120"}, headers=h)
    assert r.status_code == 200, r.text
    assert client.post(f"{API}/{q2['id']}/recalcular", headers=h).status_code == 200

    despues = _leer(client, h, q2["id"])
    print("\n===== 19.b RECALCULAR LA QUE COBRÓ =====")
    print(f"  total={despues['valor_total']} saldo_anterior={despues['saldo_anterior']} "
          f"neto={despues['neto_a_pagar']} saldo={despues['saldo']}")
    assert D(despues["valor_total"]) == D("300000")
    assert D(despues["saldo_anterior"]) == D("120000"), (
        "el recálculo le borró la deuda arrastrada: el proveedor se queda con "
        "$120.000 que ya nadie le va a cobrar"
    )
    assert D(despues["neto_a_pagar"]) == D("180000")
    assert D(despues["saldo"]) == D("180000")
    assert [o["id"] for o in despues["deudas_cobradas"]] == [q1["id"]]


# ===========================================================================
# 9. LOS DOS CAMINOS POR LOS QUE LA DEUDA SE PERDÍA Y SALÍA PLATA DE MÁS
# ===========================================================================
# Los dos llegan al mismo sitio: el ORIGEN deja de estar 'aprobada' y la consulta que
# busca deudas —que exigía APROBADA o PAGADA— no lo volvía a ver. Están medidos peso por
# peso porque es el defecto que le sacaba plata de la caja al dueño.
def test_una_observacion_en_un_dia_no_puede_perder_la_deuda_de_la_quincena(
    client, base_datos
):
    """EL SOBREPAGO POR EL CAMPO QUE NO MUEVE UN PESO. Cifras a mano:

        QUINCENA 1 (01-15/06) — Henri C, 100 L a $1.800:
            valor total                       $180.000
            anticipo YA ENTREGADO            -$300.000
            -> LE QUEDA DEBIENDO              $120.000    y se APRUEBA

        Alguien le escribe una OBSERVACIÓN al día del 02/06 —un campo que no mueve un
        peso y que este trabajo dejó editable a propósito—. El recuadre automático
        devuelve la quincena 1 a 'borrador'. Sus cifras no cambian: sigue debiendo
        $120.000.

        QUINCENA 2 (16-30/06) — 100 L a $2.500 = $250.000:
            ANTES (la deuda se perdía)     neto $250.000  -> de la caja salían $550.000
            AHORA                          neto $130.000  -> de la caja salen $430.000

        Y LA CUENTA GRANDE, la que el dueño hace de memoria: $180.000 + $250.000 =
        $430.000 de leche. Con los $300.000 de anticipo ya entregados, lo que falta por
        entregar son $130.000. Un peso más es plata regalada.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    rec1 = _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    assert D(q1["le_queda_debiendo"]) == D("120000")

    print("\n===== 20. LA OBSERVACIÓN QUE PERDÍA LA DEUDA =====")
    r = client.put(
        f"{REC}/{rec1['id']}", json={"observaciones": "llego tarde"}, headers=h
    )
    assert r.status_code == 200, r.text
    q1_despues = _leer(client, h, q1["id"])
    print(f"  tras la observación · q1 quedó en '{q1_despues['estado']}' "
          f"debe={q1_despues['le_queda_debiendo']}")
    assert q1_despues["estado"] == "borrador", (
        "el escenario del defecto exige que la observación la devuelva a borrador"
    )
    assert D(q1_despues["le_queda_debiendo"]) == D("120000")

    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    print(f"  q2 · total={q2['valor_total']} saldo_anterior={q2['saldo_anterior']} "
          f"neto={q2['neto_a_pagar']}")
    assert D(q2["saldo_anterior"]) == D("120000"), (
        "la deuda se perdió porque una observación devolvió el origen a borrador: al "
        "pagar esta quincena salen $250.000 en vez de $130.000, $120.000 de sobrepago"
    )
    assert D(q2["neto_a_pagar"]) == D("130000")

    _aprobar(client, h, q2["id"])
    pagada = _pagar(client, h, q2["id"])
    entregado = sum(
        (D(_leer(client, h, liq["id"])["pagado"]) for liq in (q1, q2)), CERO
    )
    print(f"  se le pagó {pagada['pagado']} · TOTAL entregado en las dos quincenas "
          f"= {entregado} (leche $430.000 - anticipo $300.000 = $130.000)")
    assert D(pagada["pagado"]) == D("130000")
    assert entregado == D("130000"), (
        f"se le entregaron {entregado} por $430.000 de leche con $300.000 de anticipo "
        "ya dados: hay sobrepago"
    )


def test_en_el_flete_una_observacion_tampoco_pierde_la_deuda(client, base_datos):
    """El mismo defecto por el lado del TRANSPORTADOR. Cifras a mano:

        QUINCENA 1 (01-15/06) — Alex recoge 100 L en Nápoles a $1,00 el litro:
            valor total del flete                 $100
            anticipo YA ENTREGADO                -$600
            -> ALEX LE QUEDA DEBIENDO             $500     y se APRUEBA

        Se le escribe una observación al día -> la del flete vuelve a 'borrador'.

        QUINCENA 2 (16-30/06) — otros 100 L en Nápoles = $100 de flete:
            ANTES  neto  $100   (la deuda se perdía y se le pagaban $100)
            AHORA  neto -$400   (se le cobran los $500 y sigue debiendo $400)
    """
    h = auth_headers(client, "admin.a")
    napoles = _ruta(client, h, "Napoles")
    alex = _transportador(client, h, "Alex", [(napoles, "1")])
    patricia = _proveedor(client, h, "Patricia")
    rec1 = _recepcion(client, h, patricia, "2026-06-02", "100", t=alex, ruta=napoles)
    _anticipo(client, h, "2026-06-01", "600", transportador=alex)
    flete1 = _de(_generar(client, h, Q1, tipo="transportador"), "Alex")
    _aprobar(client, h, flete1["id"])
    assert D(flete1["valor_total"]) == D("100")
    assert D(flete1["le_queda_debiendo"]) == D("500")

    print("\n===== 21. LA OBSERVACIÓN, EN EL FLETE =====")
    r = client.put(f"{REC}/{rec1['id']}", json={"observaciones": "lluvia"}, headers=h)
    assert r.status_code == 200, r.text
    flete1_despues = _leer(client, h, flete1["id"])
    print(f"  tras la observación · quedó en '{flete1_despues['estado']}' "
          f"debe={flete1_despues['le_queda_debiendo']}")
    assert flete1_despues["estado"] == "borrador"
    assert D(flete1_despues["le_queda_debiendo"]) == D("500")

    _recepcion(client, h, patricia, "2026-06-20", "100", t=alex, ruta=napoles)
    flete2 = _de(_generar(client, h, Q2, tipo="transportador"), "Alex")
    print(f"  q2 flete · total={flete2['valor_total']} "
          f"saldo_anterior={flete2['saldo_anterior']} neto={flete2['neto_a_pagar']}")
    assert D(flete2["saldo_anterior"]) == D("500"), (
        "la deuda del flete se perdió: se le pagarían $100 teniendo $500 a favor"
    )
    assert D(flete2["neto_a_pagar"]) == D("-400")


def test_anular_las_dos_y_regenerar_no_pierde_la_deuda(client, base_datos):
    """EL FLUJO DE CORRECCIÓN QUE EL PROPIO MENSAJE DE ERROR RECOMIENDA.

    Cuando el dueño quiere corregir la quincena que dejó debiendo, el sistema le dice
    "anule primero esa liquidación". Haciéndole caso: anula la que cobró la deuda, anula
    la vieja, y vuelve a generar las dos. Cifras a mano:

        q1b (01-15/06), regenerada, en BORRADOR: $180.000 - $300.000 de anticipo
            -> queda debiendo $120.000
        q2b (16-30/06), regenerada: $250.000 - $120.000 = neto $130.000

    ANTES q2b nacía con saldo_anterior $0 —q1b estaba en borrador y la consulta de
    deudas solo miraba aprobadas— y se le pagaban $250.000: $120.000 de sobrepago por
    seguir el flujo que el sistema mismo recomienda.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    print("\n===== 22. ANULAR LAS DOS Y REGENERAR =====")
    assert client.post(f"{API}/{q2['id']}/anular", headers=h).status_code == 200
    assert client.post(f"{API}/{q1['id']}/anular", headers=h).status_code == 200

    q1b = _generar(client, h, Q1)[0]
    print(f"  q1b · estado={q1b['estado']} total={q1b['valor_total']} "
          f"ant={q1b['anticipos']} debe={q1b['le_queda_debiendo']}")
    assert q1b["estado"] == "borrador"
    assert D(q1b["valor_total"]) == D("180000")
    assert D(q1b["anticipos"]) == D("300000"), (
        "al anular no se soltó el anticipo: la deuda regenerada no da"
    )
    assert D(q1b["le_queda_debiendo"]) == D("120000")

    q2b = _generar(client, h, Q2)[0]
    print(f"  q2b · total={q2b['valor_total']} saldo_anterior={q2b['saldo_anterior']} "
          f"neto={q2b['neto_a_pagar']}")
    assert D(q2b["saldo_anterior"]) == D("120000"), (
        "regenerando las dos se perdió la deuda: al pagar q2b salen $250.000 en vez de "
        "$130.000"
    )
    assert D(q2b["neto_a_pagar"]) == D("130000")
    assert [o["id"] for o in q2b["deudas_cobradas"]] == [q1b["id"]]


# ===========================================================================
# 10. LA DEUDA NO VIAJA HACIA ATRÁS EN EL TIEMPO
# ===========================================================================
def test_la_deuda_solo_viaja_de_una_quincena_anterior(client, base_datos):
    """Generar la segunda quincena ANTES que la primera. Cifras a mano:

        Se genera primero la QUINCENA 2 (16-30/06) — 100 L a $2.500:
            valor total                       $250.000
            anticipo YA ENTREGADO (16/06)    -$400.000
            -> LE QUEDA DEBIENDO              $150.000

        Y después la QUINCENA 1 (01-15/06) — 100 L a $1.800:
            valor total                       $180.000
            lo que quedó debiendo la pasada         $0    <- no existe "la pasada"
            neto a pagar                      $180.000

    ANTES la consulta de deudas no miraba fechas, así que el comprobante del 01/06 al
    15/06 cobraba $150.000 "de la quincena pasada" —y lo decía por escrito, nombrando la
    del 16/06 al 30/06, que todavía no había empezado— y su neto salía en $30.000.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    _anticipo(client, h, "2026-06-16", "400000", proveedor=henri)
    q2 = _generar(client, h, Q2)[0]
    print("\n===== 23. LA DEUDA NO VIAJA HACIA ATRÁS =====")
    print(f"  q2 (generada primero) · total={q2['valor_total']} ant={q2['anticipos']} "
          f"debe={q2['le_queda_debiendo']}")
    assert D(q2["valor_total"]) == D("250000")
    assert D(q2["anticipos"]) == D("400000")
    assert D(q2["le_queda_debiendo"]) == D("150000")

    _recepcion(client, h, henri, "2026-06-02", "100")  # $180.000
    q1 = _generar(client, h, Q1)[0]
    print(f"  q1 (generada después) · total={q1['valor_total']} "
          f"saldo_anterior={q1['saldo_anterior']} neto={q1['neto_a_pagar']}")
    assert D(q1["saldo_anterior"]) == CERO, (
        "la quincena del 01 al 15 se cobró una deuda de la del 16 al 30: la deuda "
        "viajó hacia atrás en el tiempo"
    )
    assert D(q1["neto_a_pagar"]) == D("180000")
    assert q1["deudas_cobradas"] == []
    assert _leer(client, h, q2["id"])["deuda_trasladada_a_id"] is None

    # Y el papel del 01 al 15 no nombra la quincena que todavía no había empezado.
    papel = _pdf(client, h, q1["id"])
    assert "Lo que quedó debiendo" not in papel, papel
    assert "16/06/2026 al 30/06/2026" not in papel, papel

    # La que sí va después se la cobra: la del 01 al 15 de JULIO.
    _recepcion(client, h, henri, "2026-07-02", "100")  # $180.000
    q3 = _generar(client, h, Q3)[0]
    print(f"  q3 (01-15/07) · saldo_anterior={q3['saldo_anterior']} "
          f"neto={q3['neto_a_pagar']}")
    assert D(q3["saldo_anterior"]) == D("150000"), (
        "la deuda de la quincena del 16 al 30 de junio sí tiene que viajar a julio"
    )
    assert D(q3["neto_a_pagar"]) == D("30000")


# ===========================================================================
# 11. BORRAR EN SUAVE EL ORIGEN QUE TODAVÍA DEBE
# ===========================================================================
def test_borrar_en_suave_el_origen_libre_no_pierde_la_deuda_ni_el_anticipo(
    client, base_datos, db_session
):
    """Borrar la quincena que dejó debiendo, cuando esa deuda TODAVÍA no se ha cobrado.

    ANTES pasaba en silencio y costaba plata dos veces: la deuda de $120.000
    desaparecía con el documento (las consultas no devuelven lo borrado) y el anticipo
    de $300.000 se quedaba pegado a ese documento borrado, PRESO PARA SIEMPRE —solo se
    vuelven a aplicar los anticipos que tienen la liquidación en nulo—. Al proveedor le
    quedaban $120.000 que nadie le iba a cobrar nunca.

    Cifras a mano:
        q1 (01-15/06): 100 L a $1.800 = $180.000 con $300.000 de anticipo
            -> queda debiendo $120.000. Se APRUEBA y se borra en suave.
        Al borrarla, su día y su anticipo quedan libres.
        Se vuelve a generar el mismo período:
            valor total $180.000, anticipos $300.000 -> vuelve a quedar debiendo $120.000
        Y la quincena siguiente se la cobra: $250.000 - $120.000 = neto $130.000

    Se entra por el servicio porque no hay endpoint que borre liquidaciones; el día que
    se exponga tiene que estar cubierto igual.
    """
    import uuid as _uuid

    from app.core.context import RequestContext
    from app.modules.liquidaciones.service import LiquidacionService

    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    rec1 = _recepcion(client, h, henri, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    assert D(q1["le_queda_debiendo"]) == D("120000")

    empresa = base_datos["empresa_a"]
    servicio = LiquidacionService(db_session, RequestContext(empresa_id=empresa.id))
    servicio.eliminar(_uuid.UUID(q1["id"]))
    db_session.flush()

    print("\n===== 24. BORRAR EN SUAVE EL ORIGEN QUE TODAVÍA DEBE =====")
    suelto = client.get(f"{ANT}/{ant['id']}", headers=h).json()
    print(f"  el anticipo quedó · liquidacion_id={suelto['liquidacion_id']} "
          f"valor={suelto['valor']}")
    assert suelto["liquidacion_id"] is None, (
        "el anticipo de $300.000 quedó preso de una liquidación borrada: no se puede "
        "volver a aplicar, y esa plata ya salió de la caja"
    )
    dia = client.get(f"{REC}/{rec1['id']}", headers=h).json()
    assert dia["liquidacion_id"] is None, "el día quedó apartado por un documento borrado"

    # Se vuelve a generar y la deuda reaparece completa: no se perdió un peso.
    q1b = _generar(client, h, Q1)[0]
    print(f"  q1b · total={q1b['valor_total']} ant={q1b['anticipos']} "
          f"debe={q1b['le_queda_debiendo']}")
    assert D(q1b["valor_total"]) == D("180000")
    assert D(q1b["anticipos"]) == D("300000")
    assert D(q1b["le_queda_debiendo"]) == D("120000")

    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    print(f"  q2 · saldo_anterior={q2['saldo_anterior']} neto={q2['neto_a_pagar']}")
    assert D(q2["saldo_anterior"]) == D("120000")
    assert D(q2["neto_a_pagar"]) == D("130000")


def test_no_se_borra_en_suave_una_liquidacion_con_plata_entregada(
    client, base_datos, db_session
):
    """Borrar la que ya tiene un abono soltaría sus días y su anticipo para que otra
    liquidación se los vuelva a cobrar, dejando el pago colgando de un documento que ya
    no existe: esa plata se contaría dos veces. Es el mismo criterio de `anular`.

    Cifras: 100 L a $1.800 = $180.000 sin anticipos, con un abono de $50.000.
    """
    import uuid as _uuid

    from app.core.context import RequestContext
    from app.core.exceptions import BusinessError
    from app.modules.liquidaciones.service import LiquidacionService

    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri")
    _recepcion(client, h, henri, "2026-06-02", "100")
    liq = _generar(client, h, Q1)[0]
    _aprobar(client, h, liq["id"])
    r = client.post(
        f"{API}/{liq['id']}/pagos", json={"fecha": "2026-06-16", "valor": "50000"}, headers=h
    )
    assert r.status_code == 200, r.text

    empresa = base_datos["empresa_a"]
    servicio = LiquidacionService(db_session, RequestContext(empresa_id=empresa.id))
    print("\n===== 25. NO SE BORRA LO QUE YA TIENE PLATA ENTREGADA =====")
    try:
        servicio.eliminar(_uuid.UUID(liq["id"]))
        raise AssertionError("se borró una liquidación con un abono registrado")
    except BusinessError as error:
        print(f"  eliminar -> {error}")
        assert "pagos registrados" in str(error)


# ===========================================================================
# 12. EL NETO EN CERO POR LA DEUDA: NADIE PAGÓ, NO SE MARCA PAGADA
# ===========================================================================
def test_el_neto_en_cero_por_la_deuda_no_se_marca_pagada_ni_traba_los_dias(
    client, base_datos
):
    """El borde exacto: el saldo no baja de cero, CAE JUSTO EN CERO. Cifras a mano:

        QUINCENA 1 (01-15/06): 100 L a $1.800 = $180.000 con $300.000 de anticipo
            -> LE QUEDA DEBIENDO $120.000, y se aprueba
        QUINCENA 2 (16-30/06): 48 L a $2.500 = $120.000, sin anticipos propios
            - lo que quedó debiendo la pasada     -$120.000
            neto a pagar                             $0,00
            pagado                                   $0,00

    ANTES "Pagar" devolvía 200 y la marcaba 'pagada' —el guardia solo rebotaba si
    `le_queda_debiendo` era mayor que cero, y acá es cero— sin que saliera un peso de la
    caja. Y después los días de esa quincena salían trabados en Recepción diaria
    diciendo "ya se pagó", que es justo la mentira que este trabajo vino a quitar.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])

    rec2 = _recepcion(client, h, henri, "2026-06-20", "48", precio="2500")  # $120.000
    q2 = _generar(client, h, Q2)[0]
    print("\n===== 26. EL NETO EN CERO POR LA DEUDA =====")
    print(f"  q2 · total={q2['valor_total']} saldo_anterior={q2['saldo_anterior']} "
          f"neto={q2['neto_a_pagar']} debe={q2['le_queda_debiendo']}")
    assert D(q2["valor_total"]) == D("120000")
    assert D(q2["saldo_anterior"]) == D("120000")
    assert D(q2["neto_a_pagar"]) == CERO
    assert D(q2["le_queda_debiendo"]) == CERO, "el borde de esta prueba es el cero exacto"
    _aprobar(client, h, q2["id"])

    r = client.post(f"{API}/{q2['id']}/pagar", headers=h)
    detalle = r.json()["error"]["detail"]
    print(f"  pagar -> {r.status_code} · {detalle}")
    assert r.status_code == 422, r.text
    assert "$120.000" in detalle, detalle
    assert "aprobada" in detalle, detalle

    despues = _leer(client, h, q2["id"])
    print(f"  q2 quedó en '{despues['estado']}' con pagado={despues['pagado']}")
    assert despues["estado"] == "aprobada", (
        "quedó marcada 'pagada' sin que saliera un peso: eso traba los días de la "
        "quincena para siempre y de 'pagada' no se puede anular"
    )
    assert D(despues["pagado"]) == CERO

    # Y abonarle también rebota, con un aviso que explica el cero (no un "no hay saldo").
    r = client.post(
        f"{API}/{q2['id']}/pagos", json={"fecha": "2026-07-01", "valor": "1000"}, headers=h
    )
    print(f"  abonarle -> {r.status_code} · {r.json()['error']['detail']}")
    assert r.status_code == 422, r.text
    assert "$120.000" in r.json()["error"]["detail"]

    # LOS DÍAS DE ESTA QUINCENA SIGUEN CORREGIBLES: por ellos no salió plata.
    celda = client.get(f"{REC}/{rec2['id']}", headers=h).json()
    print(f"  el día de q2 · leche_pagada={celda['leche_pagada']} "
          f"aviso={celda['candado_aviso']}")
    assert celda["leche_pagada"] is False, (
        "el día quedó trabado sin que hubiera salido un peso contra esa quincena"
    )
    r = client.put(f"{REC}/{rec2['id']}", json={"cantidad_litros": "50"}, headers=h)
    print(f"  corregirle los litros -> {r.status_code}")
    assert r.status_code == 200, r.text
    # 50 L a $2.500 = $125.000 - $120.000 de deuda = neto $5.000
    corregida = _leer(client, h, q2["id"])
    print(f"  q2 tras la corrección · total={corregida['valor_total']} "
          f"saldo_anterior={corregida['saldo_anterior']} neto={corregida['neto_a_pagar']}")
    assert D(corregida["valor_total"]) == D("125000")
    assert D(corregida["saldo_anterior"]) == D("120000")
    assert D(corregida["neto_a_pagar"]) == D("5000")


def test_el_saldo_en_cero_por_los_anticipos_propios_sigue_marcandose_pagada(
    client, base_datos
):
    """LO QUE NO CAMBIÓ, y la diferencia importa: acá el cero lo puso un anticipo de
    ESTA quincena, o sea plata que salió de la caja en la mano del proveedor. La
    quincena está saldada de verdad y 'pagada' es la verdad.

    Cifras: 100 L a $1.800 = $180.000 con un anticipo de $180.000 exactos -> saldo
    $0,00 y ninguna deuda arrastrada de por medio.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "180000", proveedor=henri)
    liq = _generar(client, h, Q1)[0]
    assert D(liq["valor_total"]) == D("180000")
    assert D(liq["anticipos"]) == D("180000")
    assert D(liq["saldo"]) == CERO
    assert D(liq["saldo_anterior"]) == CERO
    _aprobar(client, h, liq["id"])

    pagada = _pagar(client, h, liq["id"])
    print("\n===== 27. EL CERO POR EL ANTICIPO PROPIO SIGUE SIENDO 'PAGADA' =====")
    print(f"  estado={pagada['estado']} pagado={pagada['pagado']} saldo={pagada['saldo']}")
    assert pagada["estado"] == "pagada"
    assert D(pagada["pagado"]) == CERO


# ===========================================================================
# 13. LA ANULADA NO IMPRIME UN DESCUENTO QUE NO PUEDE EXPLICAR
# ===========================================================================
def test_la_anulada_deja_de_cobrar_la_deuda_y_su_resumen_vuelve_a_cuadrar(
    client, base_datos
):
    """Al anular la que cobró la deuda se suelta la marca del origen (correcto), y el
    papel de la anulada quedaba imprimiendo "Lo que quedó debiendo de la quincena
    pasada - $120.000" SIN la nota que decía de qué quincena venía: un descuento
    huérfano, una cifra que el papel no puede explicar.

    LA DECISIÓN: la anulada DEJA DE MOSTRAR UN COBRO QUE YA NO TIENE. Anular es dejar de
    cobrar; una liquidación tachada no le descuenta nada a nadie. Cifras a mano:

        q2 antes de anular:  VALOR TOTAL $250.000 - $120.000 de deuda = saldo $130.000
        q2 anulada:          VALOR TOTAL $250.000 - $0                = saldo $250.000

    Y el resumen del papel vuelve a cuadrar de arriba abajo, que es la regla que el
    dueño verifica a mano.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    print("\n===== 28. LA ANULADA NO IMPRIME UN DESCUENTO HUÉRFANO =====")
    assert client.post(f"{API}/{q2['id']}/anular", headers=h).status_code == 200
    anulada = _leer(client, h, q2["id"])
    print(f"  q2 anulada · estado={anulada['estado']} "
          f"saldo_anterior={anulada['saldo_anterior']} saldo={anulada['saldo']} "
          f"deudas_cobradas={anulada['deudas_cobradas']}")
    assert anulada["estado"] == "anulada"
    assert D(anulada["saldo_anterior"]) == CERO
    assert D(anulada["saldo"]) == D("250000")
    assert anulada["deudas_cobradas"] == []

    papel = _pdf(client, h, q2["id"])
    print(f"  ...{papel[papel.find('Valor bruto'):][:170]}...")
    assert "Lo que quedó debiendo" not in papel, (
        "el papel de la anulada imprime un descuento y no tiene con qué explicarlo"
    )
    bruto = renglon(papel, "Valor bruto")
    bonif = renglon(papel, "Bonificaciones")
    desc = renglon(papel, "Descuentos")
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    saldo = renglon(papel, "SALDO A PAGAR")
    assert bruto + bonif + desc == total == D("250000")
    assert total + anticipos == saldo == D("250000"), (
        f"el resumen de la anulada no cuadra: {total} + {anticipos} contra {saldo}"
    )

    # Y la deuda del origen sigue viva y libre, lista para la próxima.
    soltada = _leer(client, h, q1["id"])
    assert soltada["deuda_trasladada_a_id"] is None
    assert D(soltada["le_queda_debiendo"]) == D("120000")


# ===========================================================================
# 14. EL PAPEL DEL AVANCE: MISMO ORDEN QUE EL OFICIAL, Y AVISA
# ===========================================================================
def test_el_pdf_del_avance_suma_de_arriba_abajo_y_avisa_de_la_deuda(client, base_datos):
    """El avance ("¿cómo voy?") es un papel que se le muestra al proveedor.

    DOS DEFECTOS, y los dos se ven en el mismo papel. Cifras a mano:

        QUINCENA 1 (01-15/06): 100 L a $1.800 = $180.000 con $300.000 de anticipo
            -> LE QUEDA DEBIENDO $120.000, aprobada
        AVANCE de la quincena 2 (16-30/06): 100 L a $2.500
            Valor bruto                         $250.000
            + bonificaciones                          $0
            - descuentos                              $0
            VALOR TOTAL                         $250.000
            - anticipos                               $0
            SALDO ESTIMADO                      $250.000

        1. "Anticipos aplicados" salía ARRIBA de VALOR TOTAL —el defecto exacto que el
           dueño reclamó en el comprobante oficial, que sí se reordenó— así que quien
           suma la columna de arriba abajo no llegaba nunca a la cifra grande.
        2. Y el papel prometía "SALDO ESTIMADO $250.000" cuando lo que va a salir de la
           caja son $250.000 - $120.000 = $130.000, porque la deuda se cobra al generar.
           El avance sigue sin descontarla (no marca ni aparta nada) pero ahora LO DICE.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _aprobar(client, h, q1["id"])
    assert D(q1["le_queda_debiendo"]) == D("120000")

    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    r = _avance_pdf(client, h, Q2, henri)
    papel = texto_pdf(r.content)
    print("\n===== 29. EL PAPEL DEL AVANCE =====")
    print(f"  ...{papel[papel.find('Valor bruto'):][:260]}...")

    bruto = renglon(papel, "Valor bruto")
    bonif = renglon(papel, "Bonificaciones")
    desc = renglon(papel, "Descuentos")
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    estimado = renglon(papel, "SALDO ESTIMADO")
    print(f"    bruto {bruto} · bonif {bonif} · desc {desc} · VALOR TOTAL {total} · "
          f"anticipos {anticipos} · SALDO ESTIMADO {estimado}")
    assert bruto + bonif + desc == total == D("250000")
    assert total + anticipos == estimado == D("250000")
    # EL ORDEN, que es el mismo del comprobante oficial.
    assert papel.index("VALOR TOTAL") < papel.index("Anticipos aplicados"), papel
    assert papel.index("Anticipos aplicados") < papel.index("SALDO ESTIMADO"), papel

    # Y EL AVISO, con la cifra de verdad.
    print(f"  aviso: ...{papel[papel.find('AVISO'):][:230]}...")
    assert "AVISO" in papel, (
        "el avance promete un SALDO ESTIMADO de $250.000 cuando van a salir $130.000, y "
        "este papel se le muestra al proveedor"
    )
    assert "$120.000" in papel, papel
    assert "$130.000" in papel, papel

    # El avance de un proveedor que no debe nada sale sin aviso, como siempre.
    rosa = _proveedor(client, h, "Dona Rosa")
    _recepcion(client, h, rosa, "2026-06-20", "100")
    limpio = texto_pdf(_avance_pdf(client, h, Q2, rosa).content)
    print(f"  el avance de quien no debe nada: 'AVISO' en el papel = {'AVISO' in limpio}")
    assert "AVISO" not in limpio
