"""ATAQUE AL ANTICIPO DENTRO DE LA CORRECCIÓN: QUE LA MISMA PLATA ENTREGADA EN LA MANO
NO SE DESCUENTE DOS VECES, NI SE PIERDA.

Un anticipo NO es un cálculo: es plata que ya salió de la caja y se le puso en la mano al
productor. El comprobante solo la RESTA:

    neto_a_pagar = valor_total − anticipos − saldo_anterior
    saldo        = neto_a_pagar − pagado

La única marca que impide que esa resta ocurra dos veces es `Anticipo.liquidacion_id`:
mientras apunte a una quincena, `pendientes_de` no lo vuelve a encontrar. La corrección de
una quincena YA PAGADA ahora mueve esa marca a mano —la pone, la quita y le cambia el
valor—, o sea que toca el único candado que había. Este archivo ataca ese candado por los
cinco caminos por los que un peso podría contarse dos veces o desaparecer:

  · que el mismo adelanto quede descontado en DOS quincenas a la vez;
  · que el que se suelta de esta quincena NO lo recoja la siguiente (plata entregada que
    se pierde);
  · que el que se suelta lo recoja la siguiente Y ADEMÁS siga restando aquí;
  · que soltar y volver a incluir el mismo anticipo en dos correcciones seguidas
    descuadre la cifra;
  · que mandar la MISMA corrección dos veces —un reintento del navegador, un doble
    clic— descuente doble.

LA PRUEBA DE VERDAD ES EL CUADRE DE CAJA CONTRA LA LECHE, y por eso la primera prueba
corre el ciclo COMPLETO (quincena 1 con el anticipo → se suelta → quincena 2 del mismo
proveedor): lo que salió de la caja por todos los conceptos tiene que valer exactamente lo
que valió la leche que entró. Un anticipo descontado dos veces deja esa igualdad rota por
su valor exacto, y un anticipo perdido la rompe en el otro sentido.

TODAS LAS CIFRAS ESTÁN CALCULADAS A MANO en el docstring de cada prueba, nunca con el
mismo código que se está probando. Se escogieron redondas a propósito (250 L a $2.000 =
$500.000, adelanto de $300.000) para que cualquiera rehaga la cuenta en una servilleta.

REGLA DE LA CASA, que manda sobre todo lo demás: TODO DESGLOSE SUMA EXACTO LA CIFRA
GRANDE. Aquí se comprueba en las dos igualdades, en cada paso y con el anticipo movido:
    neto = pagado + saldo
    neto = valor_total − anticipos − saldo_anterior
"""
import io
import re
from decimal import Decimal

import pytest
from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
ANT = "/api/v1/anticipos"

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
Q3 = ("2026-07-01", "2026-07-15")


def D(v):
    return Decimal(str(v))


CERO = D(0)


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre="Henri C", precio="2000"):
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


def _anticipo(client, h, prov, fecha, valor, observaciones="para la droga"):
    r = client.post(
        ANT,
        json={
            "tipo": "proveedor",
            "proveedor_id": prov["id"],
            "fecha": fecha,
            "valor": str(valor),
            "observaciones": observaciones,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ver_anticipo(client, h, anticipo_id):
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


def _de(generadas, proveedor):
    encontradas = [x for x in generadas if x["proveedor_id"] == proveedor["id"]]
    assert len(encontradas) == 1, f"se esperaba una sola de ese proveedor: {generadas}"
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


def _cuerpo(motivo, *, incluir=(), soltar=(), valores=(), dias=(), precios=()):
    """El cuerpo de la corrección tal cual lo manda la pantalla.

    Va en una función para que la previsualización y el botón manden BYTE POR BYTE lo
    mismo: si la prueba armara dos diccionarios parecidos, una diferencia entre el
    diálogo y la escritura se escondería en la prueba misma.
    """
    return {
        "motivo": motivo,
        "recepciones_a_incluir": list(dias),
        "precios": list(precios),
        "anticipos_a_incluir": list(incluir),
        "anticipos_a_soltar": list(soltar),
        "valores_de_anticipos": [
            {"anticipo_id": a, "valor": str(v)} for a, v in valores
        ],
    }


def _previsualizar(client, h, liq_id, cuerpo):
    r = client.post(f"{API}/{liq_id}/corregir/previsualizar", json=cuerpo, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _corregir(client, h, liq_id, cuerpo):
    r = client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _corregir_crudo(client, h, liq_id, cuerpo):
    """Sin `assert`: para los ataques, donde lo que se mide es el rebote."""
    return client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)


def _correcciones(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/correcciones", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------- el papel
# El papel se LEE: lo que el dueño y el productor suman son los caracteres que salieron
# impresos, no lo que contesta la API. El lector es el mismo molde de
# tests/test_liquidacion_saldo_anterior.py, para que las dos pruebas lean el mismo
# comprobante de la misma manera.
_CIFRA = re.compile(r"(-?)\s*\$\s*(-?)([\d.]+(?:,\d{2})?)")


def texto_pdf(contenido):
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    return texto_pdf(r.content)


def renglon(papel, rotulo):
    """La cifra del renglón, CON SU SIGNO: "- $300.000,00" vale -300000,00."""
    inicio = papel.find(rotulo)
    assert inicio >= 0, f"el comprobante no trae el renglón «{rotulo}»:\n{papel}"
    resto = papel[inicio + len(rotulo):]
    encontrado = _CIFRA.search(resto)
    assert encontrado, f"el renglón «{rotulo}» salió sin cifra:\n{resto[:120]}"
    signo, signo_interno, cifra = encontrado.groups()
    valor = D(cifra.replace(".", "").replace(",", "."))
    return -valor if (signo == "-" or signo_interno == "-") else valor


# ------------------------------------------------------------ la regla de la casa
def cuadra(liq, donde=""):
    """Las dos igualdades que el dueño verifica con calculadora, en CADA paso.

        neto = pagado + saldo
        neto = valor_total − anticipos − saldo_anterior

    Se llama después de cada operación y no solo al final a propósito: si una de las dos
    se rompe en el medio y se vuelve a cuadrar sola, el papel intermedio —el que el
    productor ya tiene en la mano— mintió igual.
    """
    neto = D(liq["neto_a_pagar"])
    assert neto == D(liq["pagado"]) + D(liq["saldo"]), (
        f"{donde}: neto {neto} ≠ pagado {liq['pagado']} + saldo {liq['saldo']}"
    )
    assert neto == D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq["saldo_anterior"]), (
        f"{donde}: neto {neto} ≠ total {liq['valor_total']} − anticipos "
        f"{liq['anticipos']} − saldo anterior {liq['saldo_anterior']}"
    )


def _quincena_con_anticipo(client, h, prov, *, litros="250", anticipo="300000"):
    """La quincena 1 tal cual queda el día que el dueño se da cuenta del error.

    250 L el 02/06 a $2.000 = $500.000 de leche, menos $300.000 que ya se le habían
    entregado en la mano el 05/06: el productor recibió $200.000 y firmó el papel.
    """
    _recepcion(client, h, prov, "2026-06-02", litros)
    ant = _anticipo(client, h, prov, "2026-06-05", anticipo)
    liq = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])
    return liq, ant


# ===========================================================================
# 1 · EL CICLO COMPLETO, Y EL CUADRE DE CAJA CONTRA LA LECHE
# ===========================================================================
def test_el_adelanto_que_se_suelta_lo_descuenta_la_quincena_siguiente_una_sola_vez(
    client, base_datos
):
    """EL ATAQUE PRINCIPAL, con el ciclo entero y las cifras a mano.

    El adelanto de $300.000 se le descontó a la quincena 1, pero era de la quincena
    anterior: el dueño lo suelta corrigiendo. Esa plata NO desaparece —se entregó— y
    tiene que reaparecer descontada en la quincena 2, UNA sola vez.

    QUINCENA 1 (01/06 al 15/06), como está el día del error:
        02/06 · 250,00 L a $2.000,00          $500.000
        VALOR TOTAL                           $500.000
        − Anticipos                           $300.000
        NETO A PAGAR                          $200.000   ← y se le pagaron

    QUINCENA 1 DESPUÉS DE SOLTAR EL ADELANTO (v2):
        VALOR TOTAL                           $500.000
        − Anticipos                                 $0
        NETO A PAGAR                          $500.000
        − Pagado                              $200.000
        QUEDA POR ENTREGARLE                  $300.000   ← estado 'parcial'
      y se oprime Pagar: pagado $500.000, saldo $0.

    QUINCENA 2 (16/06 al 30/06):
        20/06 · 250,00 L a $2.000,00          $500.000
        VALOR TOTAL                           $500.000
        − Anticipos                           $300.000   ← el que se soltó, aquí
        NETO A PAGAR                          $200.000   ← y se le pagan

    EL CUADRE DE CAJA, que es la prueba de verdad:
        leche entregada  = 500.000 + 500.000                 = $1.000.000
        plata que salió  = 300.000 (adelanto, en la mano)
                         + 500.000 (quincena 1, ya corregida)
                         + 200.000 (quincena 2)              = $1.000.000
    Si el adelanto se descuenta dos veces salen $700.000 y faltan $300.000; si se pierde
    salen $1.300.000 y sobran $300.000. Solo cuadra si se descontó EXACTAMENTE una vez.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)

    assert D(liq1["valor_total"]) == D("500000")
    assert D(liq1["anticipos"]) == D("300000")
    assert D(liq1["neto_a_pagar"]) == D("200000")
    assert D(liq1["pagado"]) == D("200000")
    cuadra(liq1, "quincena 1 recién pagada")

    # --- se suelta el adelanto: no iba en esta quincena
    liq1 = _corregir(
        client,
        h,
        liq1["id"],
        _cuerpo("ese adelanto era de la quincena de mayo", soltar=[ant["id"]]),
    )
    assert D(liq1["anticipos"]) == CERO, "el adelanto soltado sigue restando aquí"
    assert D(liq1["neto_a_pagar"]) == D("500000")
    assert D(liq1["pagado"]) == D("200000"), "corregir movió un peso de lo ya entregado"
    assert D(liq1["saldo"]) == D("300000")
    assert liq1["estado"] == "parcial"
    assert liq1["version"] == 2
    cuadra(liq1, "quincena 1 corregida")

    liq1 = _pagar(client, h, liq1["id"])
    assert D(liq1["pagado"]) == D("500000")
    assert D(liq1["saldo"]) == CERO
    cuadra(liq1, "quincena 1 pagada del todo")

    # --- la quincena 2 lo recoge
    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    assert D(liq2["valor_total"]) == D("500000")
    assert D(liq2["anticipos"]) == D("300000"), (
        "la quincena siguiente NO recogió el adelanto que se soltó: esa plata se "
        "entregó en la mano y se acaba de perder"
    )
    assert D(liq2["neto_a_pagar"]) == D("200000")
    cuadra(liq2, "quincena 2 generada")

    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    cuadra(liq2, "quincena 2 pagada")

    # --- la marca: descontado en UNA sola quincena, y es la 2
    visto = _ver_anticipo(client, h, ant["id"])
    assert visto["liquidacion_id"] == liq2["id"], (
        "el anticipo quedó apuntando a la quincena equivocada"
    )

    # --- y la quincena 1 no volvió a restarlo por detrás
    liq1 = _leer(client, h, liq1["id"])
    assert D(liq1["anticipos"]) == CERO

    # --- EL CUADRE DE CAJA
    leche = D("500000") + D("500000")
    caja = D(ant["valor"]) + D(liq1["pagado"]) + D(liq2["pagado"])
    print("\n===== CUADRE DE CAJA CONTRA LA LECHE =====")
    print(f"  leche entregada : {leche}")
    print(f"  adelanto en mano: {ant['valor']}")
    print(f"  quincena 1      : {liq1['pagado']}")
    print(f"  quincena 2      : {liq2['pagado']}")
    print(f"  salió de caja   : {caja}")
    assert caja == leche, (
        f"la caja entregó {caja} por {leche} de leche: el adelanto de $300.000 se "
        "contó dos veces o se perdió"
    )


# ===========================================================================
# 2 · EL QUE YA RECOGIÓ LA SIGUIENTE NO VUELVE A ENTRAR ACÁ
# ===========================================================================
def test_el_adelanto_que_ya_descuenta_la_quincena_2_no_se_puede_meter_otra_vez_en_la_1(
    client, base_datos
):
    """La puerta del doble descuento: dos quincenas restando el MISMO adelanto.

    Después del ciclo de arriba el adelanto de $300.000 está descontado en la quincena 2.
    Si la corrección de la quincena 1 lo dejara incluir otra vez, el productor quedaría
    con $300.000 restados DOS veces: recibiría $300.000 menos de lo que le corresponde
    por su leche, y en el papel las dos quincenas dirían "− Anticipos $300.000".

    La quincena 2 no puede moverse un peso, y la 1 tiene que quedar como estaba.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)
    liq1 = _corregir(
        client, h, liq1["id"], _cuerpo("no iba en esta quincena", soltar=[ant["id"]])
    )
    _pagar(client, h, liq1["id"])

    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    assert D(liq2["anticipos"]) == D("300000")

    # EL ATAQUE: volver a meterlo en la quincena 1, que sigue siendo corregible.
    r = _corregir_crudo(
        client,
        h,
        liq1["id"],
        _cuerpo("me arrepentí, devuélvanmelo acá", incluir=[ant["id"]]),
    )
    assert r.status_code == 422, (
        f"se dejó descontar el mismo adelanto en dos quincenas a la vez: {r.text}"
    )

    liq1 = _leer(client, h, liq1["id"])
    liq2 = _leer(client, h, liq2["id"])
    assert D(liq1["anticipos"]) == CERO, "la quincena 1 volvió a restar el adelanto"
    assert D(liq2["anticipos"]) == D("300000"), "el rebote le movió cifras a la quincena 2"
    assert liq1["version"] == 2, "un intento rebotado subió la versión del comprobante"
    assert _ver_anticipo(client, h, ant["id"])["liquidacion_id"] == liq2["id"]
    cuadra(liq1, "quincena 1 tras el rebote")
    cuadra(liq2, "quincena 2 tras el rebote")


# ===========================================================================
# 3 · EL REINTENTO DEL NAVEGADOR
# ===========================================================================
def test_soltar_el_mismo_adelanto_dos_veces_seguidas_no_lo_suelta_dos_veces(
    client, base_datos
):
    """Un reintento del navegador manda la MISMA corrección otra vez.

    Cifras: quincena de $500.000 con $300.000 de adelanto, pagada por $200.000. La
    primera corrección suelta el adelanto y deja neto $500.000 con saldo $300.000. La
    segunda —idéntica, byte por byte— no puede volver a soltar nada ni subir la versión:
    el productor no puede terminar con dos hojas distintas del mismo período por un clic
    repetido, y la cifra no puede moverse.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)

    cuerpo = _cuerpo("el adelanto no era de esta quincena", soltar=[ant["id"]])
    liq1 = _corregir(client, h, liq1["id"], cuerpo)
    assert D(liq1["anticipos"]) == CERO
    assert D(liq1["saldo"]) == D("300000")
    assert liq1["version"] == 2

    r = _corregir_crudo(client, h, liq1["id"], cuerpo)
    assert r.status_code in (404, 422), f"el reintento pasó como si fuera nuevo: {r.text}"

    despues = _leer(client, h, liq1["id"])
    assert D(despues["anticipos"]) == CERO
    assert D(despues["neto_a_pagar"]) == D("500000"), "el reintento movió el neto"
    assert D(despues["pagado"]) == D("200000")
    assert D(despues["saldo"]) == D("300000")
    assert despues["version"] == 2, "el reintento emitió un comprobante nuevo que dice lo mismo"
    assert len(_correcciones(client, h, liq1["id"])) == 1
    cuadra(despues, "tras el reintento de soltar")


def test_incluir_el_mismo_adelanto_dos_veces_seguidas_no_lo_descuenta_dos_veces(
    client, base_datos
):
    """El otro lado del reintento: la petición que INCLUYE, mandada dos veces.

    Quincena de $500.000 pagada completa ($500.000), y el adelanto de $300.000 se
    registró tarde —después de pagar— así que quedó suelto. La primera corrección lo
    mete: neto $200.000 contra $500.000 ya entregados, saldo −$300.000 (el productor le
    quedó debiendo $300.000). La segunda, idéntica, NO puede volver a restarlo: dejaría
    neto −$100.000 y una deuda inventada de $600.000.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    _recepcion(client, h, prov, "2026-06-02", "250")
    liq1 = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq1["id"])
    liq1 = _pagar(client, h, liq1["id"])
    assert D(liq1["pagado"]) == D("500000")
    # el adelanto llega DESPUÉS de pagar: queda suelto
    ant = _anticipo(client, h, prov, "2026-06-05", "300000")
    assert _ver_anticipo(client, h, ant["id"])["liquidacion_id"] is None

    cuerpo = _cuerpo("se me olvidó anotar el adelanto del 05", incluir=[ant["id"]])
    liq1 = _corregir(client, h, liq1["id"], cuerpo)
    assert D(liq1["anticipos"]) == D("300000")
    assert D(liq1["neto_a_pagar"]) == D("200000")
    assert D(liq1["saldo"]) == D("-300000")
    assert D(liq1["le_queda_debiendo"]) == D("300000")
    cuadra(liq1, "tras incluir el adelanto")

    r = _corregir_crudo(client, h, liq1["id"], cuerpo)
    assert r.status_code in (404, 422), f"el reintento descontó otra vez: {r.text}"

    despues = _leer(client, h, liq1["id"])
    assert D(despues["anticipos"]) == D("300000"), "el adelanto quedó restado dos veces"
    assert D(despues["neto_a_pagar"]) == D("200000")
    assert D(despues["le_queda_debiendo"]) == D("300000")
    assert despues["version"] == 2
    cuadra(despues, "tras el reintento de incluir")


# ===========================================================================
# 4 · EL MISMO ID REPETIDO DENTRO DE UNA SOLA PETICIÓN
# ===========================================================================
def test_el_mismo_adelanto_repetido_en_la_peticion_no_descuenta_el_doble(
    client, base_datos
):
    """Un doble clic, o una lista que se duplica al reabrir el diálogo, manda el MISMO id
    dos veces en `anticipos_a_incluir`.

    Cifras: quincena de $500.000 pagada completa, adelanto suelto de $300.000.
        con el id una vez  → anticipos $300.000, neto $200.000, saldo −$300.000
        con el id dos veces→ tiene que dar LO MISMO
    Si se sumara dos veces: anticipos $600.000, neto −$100.000 y una deuda de $600.000
    contra un adelanto de $300.000 que es lo único que salió de la caja.

    Y EL DIÁLOGO TIENE QUE DECIR LO MISMO QUE EL BOTÓN: se previsualiza con el id
    repetido y se compara contra lo que quedó escrito.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    _recepcion(client, h, prov, "2026-06-02", "250")
    liq1 = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq1["id"])
    liq1 = _pagar(client, h, liq1["id"])
    ant = _anticipo(client, h, prov, "2026-06-05", "300000")

    cuerpo = _cuerpo("el adelanto del 05", incluir=[ant["id"], ant["id"]])
    previo = _previsualizar(client, h, liq1["id"], cuerpo)
    assert D(previo["anticipos_despues"]) == D("300000"), (
        "el diálogo prometió un descuento doble por un id repetido"
    )
    assert D(previo["neto_despues"]) == D("200000")

    liq1 = _corregir(client, h, liq1["id"], cuerpo)
    assert D(liq1["anticipos"]) == D("300000"), "el id repetido descontó $600.000"
    assert D(liq1["neto_a_pagar"]) == D(previo["neto_despues"])
    assert D(liq1["saldo"]) == D(previo["saldo_despues"])
    cuadra(liq1, "con el id repetido")


def test_el_mismo_adelanto_repetido_al_soltar_deja_un_solo_renglon_en_el_desglose(
    client, base_datos
):
    """El id repetido en `anticipos_a_soltar`.

    La cifra grande no puede moverse (un adelanto solo se suelta una vez), y el desglose
    de la corrección —lo que sale impreso y lo que el dueño suma— no puede decir que
    salieron DOS adelantos de $300.000 cuando solo había uno. Esa es la regla de la casa
    rota dentro del propio soporte de la corrección.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)

    liq1 = _corregir(
        client,
        h,
        liq1["id"],
        _cuerpo("no iba aquí", soltar=[ant["id"], ant["id"]]),
    )
    assert D(liq1["anticipos"]) == CERO
    assert D(liq1["neto_a_pagar"]) == D("500000")
    cuadra(liq1, "soltando con el id repetido")

    correccion = _correcciones(client, h, liq1["id"])[0]
    salidas = [c for c in correccion["anticipos_cambiados"] if c["accion"] == "salio"]
    assert len(salidas) == 1, (
        f"el desglose dice que salieron {len(salidas)} adelantos y solo había uno: "
        f"{correccion['anticipos_cambiados']}"
    )
    assert D(correccion["anticipos_antes"]) == D("300000")
    assert D(correccion["anticipos_despues"]) == CERO
    # el desglose suma exacto la diferencia de la cifra grande
    movido = sum((D(c["valor"]) for c in salidas), CERO)
    assert movido == D(correccion["anticipos_antes"]) - D(correccion["anticipos_despues"])


# ===========================================================================
# 5 · SOLTAR Y VOLVER A INCLUIR: IDA Y VUELTA SIN DERIVA
# ===========================================================================
def test_soltar_y_volver_a_incluir_el_mismo_adelanto_devuelve_la_cifra_exacta(
    client, base_datos
):
    """Dos correcciones seguidas, una deshaciendo a la otra.

    El dueño suelta el adelanto y a los cinco minutos se da cuenta de que sí era de esta
    quincena. Como la cifra se vuelve a sumar DESDE CERO con los que quedan marcados —y
    no sumándole o restándole a la columna guardada—, la ida y la vuelta tienen que
    dejar exactamente lo de antes:

        v1: anticipos $300.000 · neto $200.000 · pagado $200.000 · saldo $0
        v2 (soltar):  anticipos       $0 · neto $500.000 · saldo  $300.000
        v3 (incluir): anticipos $300.000 · neto $200.000 · saldo        $0  ← igual que v1

    Lo único que cambia es la VERSIÓN del papel y el rastro. Un peso de deriva aquí es un
    peso que el dueño no encuentra con la calculadora.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)
    original = dict(liq1)

    liq1 = _corregir(client, h, liq1["id"], _cuerpo("lo suelto", soltar=[ant["id"]]))
    assert D(liq1["anticipos"]) == CERO
    assert D(liq1["saldo"]) == D("300000")
    cuadra(liq1, "ida")

    liq1 = _corregir(client, h, liq1["id"], _cuerpo("sí era de aquí", incluir=[ant["id"]]))
    print("\n===== IDA Y VUELTA DEL ADELANTO =====")
    print(f"  v1 anticipos {original['anticipos']} · neto {original['neto_a_pagar']}")
    print(f"  v3 anticipos {liq1['anticipos']} · neto {liq1['neto_a_pagar']}")
    assert D(liq1["anticipos"]) == D(original["anticipos"]) == D("300000")
    assert D(liq1["neto_a_pagar"]) == D(original["neto_a_pagar"]) == D("200000")
    assert D(liq1["pagado"]) == D(original["pagado"]) == D("200000")
    assert D(liq1["saldo"]) == D(original["saldo"]) == CERO
    assert liq1["estado"] == "pagada"
    assert liq1["version"] == 3
    cuadra(liq1, "vuelta")

    assert _ver_anticipo(client, h, ant["id"])["liquidacion_id"] == liq1["id"]

    # El rastro cuenta las dos, con las dos cifras encadenadas: 300.000 → 0 → 300.000.
    corr = sorted(_correcciones(client, h, liq1["id"]), key=lambda c: c["version_nueva"])
    assert [c["version_nueva"] for c in corr] == [2, 3]
    assert [D(c["anticipos_antes"]) for c in corr] == [D("300000"), CERO]
    assert [D(c["anticipos_despues"]) for c in corr] == [CERO, D("300000")]
    assert D(corr[0]["anticipos_despues"]) == D(corr[1]["anticipos_antes"]), (
        "la cadena del rastro se rompe entre una corrección y la siguiente"
    )


def test_el_adelanto_recuperado_no_lo_recoge_ademas_la_quincena_siguiente(
    client, base_datos
):
    """Soltarlo y volver a incluirlo lo deja MARCADO otra vez: la quincena 2 no puede
    recogerlo también.

    Es el camino por el que el mismo adelanto quedaría restado en dos quincenas: se
    suelta (y queda visible como pendiente), se vuelve a incluir en la 1, y si la marca
    no volviera a ponerse bien, al generar la 2 `pendientes_de` lo encontraría otra vez.

    Cuadre de caja: leche $500.000 + $500.000 = $1.000.000.
    Caja: adelanto $300.000 + quincena 1 $200.000 + quincena 2 $500.000 = $1.000.000.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)
    liq1 = _corregir(client, h, liq1["id"], _cuerpo("lo suelto", soltar=[ant["id"]]))
    liq1 = _corregir(client, h, liq1["id"], _cuerpo("sí era de aquí", incluir=[ant["id"]]))
    assert D(liq1["anticipos"]) == D("300000")

    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    assert D(liq2["anticipos"]) == CERO, (
        "la quincena 2 volvió a descontar un adelanto que la 1 ya tiene marcado"
    )
    assert D(liq2["neto_a_pagar"]) == D("500000")
    cuadra(liq2, "quincena 2 después de la ida y vuelta")

    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    liq1 = _leer(client, h, liq1["id"])

    leche = D("1000000")
    caja = D(ant["valor"]) + D(liq1["pagado"]) + D(liq2["pagado"])
    assert caja == leche, f"la caja entregó {caja} por {leche} de leche"


# ===========================================================================
# 6 · LAS DOS LISTAS CRUZADAS EN LA MISMA PETICIÓN
# ===========================================================================
def test_incluir_y_soltar_el_mismo_adelanto_a_la_vez_rebota_sin_escribir_nada(
    client, base_datos
):
    """El mismo id en las dos listas: el resultado dependería del orden en que se
    aplicaran, y el dueño vería una cifra distinta según de dónde saliera la cuenta.

    Tiene que rebotar ANTES de escribir un peso: la quincena queda en v1, con sus
    $300.000 de adelanto y su saldo en cero.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)

    r = _corregir_crudo(
        client,
        h,
        liq1["id"],
        _cuerpo("a ver qué pasa", incluir=[ant["id"]], soltar=[ant["id"]]),
    )
    assert r.status_code == 422, r.text

    despues = _leer(client, h, liq1["id"])
    assert D(despues["anticipos"]) == D("300000")
    assert D(despues["neto_a_pagar"]) == D("200000")
    assert despues["version"] == 1, "un intento rebotado emitió un comprobante nuevo"
    assert _correcciones(client, h, liq1["id"]) == []
    assert _ver_anticipo(client, h, ant["id"])["liquidacion_id"] == liq1["id"]
    cuadra(despues, "tras el rebote de las listas cruzadas")


def test_el_adelanto_de_otro_productor_no_se_puede_colar_en_esta_quincena(
    client, base_datos
):
    """El adelanto que se le entregó a OTRO productor no puede restarse aquí.

    Sería el doble descuento más caro de todos: al de acá se le restan $300.000 que
    nunca recibió, y al otro se le vuelven a restar cuando le llegue su quincena.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    otro = _proveedor(client, h, "Marco A")
    liq1, _ = _quincena_con_anticipo(client, h, henri)
    ajeno = _anticipo(client, h, otro, "2026-06-05", "300000")

    r = _corregir_crudo(
        client, h, liq1["id"], _cuerpo("me equivoqué de productor", incluir=[ajeno["id"]])
    )
    assert r.status_code == 422, f"se coló el adelanto de otro productor: {r.text}"

    despues = _leer(client, h, liq1["id"])
    assert D(despues["anticipos"]) == D("300000")
    assert despues["version"] == 1
    assert _ver_anticipo(client, h, ajeno["id"])["liquidacion_id"] is None
    cuadra(despues, "tras el intento con el adelanto ajeno")

    r = _corregir_crudo(
        client, h, liq1["id"], _cuerpo("suéltame el ajeno", soltar=[ajeno["id"]])
    )
    assert r.status_code == 404, f"se dejó soltar un adelanto que no es de esta quincena: {r.text}"


# ===========================================================================
# 7 · CORREGIRLE EL VALOR: TAMPOCO SE ACUMULA
# ===========================================================================
def test_corregir_el_valor_del_adelanto_dos_veces_no_acumula_la_rebaja(
    client, base_datos
):
    """El valor del adelanto estaba mal digitado: eran $150.000 y se anotaron $300.000.

        v1: total $500.000 − anticipos $300.000 = neto $200.000, pagado $200.000
        v2: total $500.000 − anticipos $150.000 = neto $350.000, saldo $150.000

    Y la MISMA petición repetida tiene que dar $150.000 otra vez, nunca $75.000 ni
    $450.000: la cifra se vuelve a sumar desde los que quedan marcados, no se le aplica
    la rebaja a la columna guardada.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)

    cuerpo = _cuerpo("el adelanto eran $150.000, no $300.000", valores=[(ant["id"], "150000")])
    liq1 = _corregir(client, h, liq1["id"], cuerpo)
    assert D(liq1["anticipos"]) == D("150000")
    assert D(liq1["neto_a_pagar"]) == D("350000")
    assert D(liq1["pagado"]) == D("200000")
    assert D(liq1["saldo"]) == D("150000")
    cuadra(liq1, "valor corregido")
    assert D(_ver_anticipo(client, h, ant["id"])["valor"]) == D("150000")

    # el reintento
    r = _corregir_crudo(client, h, liq1["id"], cuerpo)
    assert r.status_code in (200, 404, 422), r.text
    despues = _leer(client, h, liq1["id"])
    assert D(despues["anticipos"]) == D("150000"), (
        f"el reintento acumuló la rebaja: quedó en {despues['anticipos']}"
    )
    assert D(despues["neto_a_pagar"]) == D("350000")
    cuadra(despues, "tras el reintento del valor")


def test_el_valor_corregido_es_el_que_se_lleva_la_quincena_siguiente_al_soltarlo(
    client, base_datos
):
    """Se le corrige el valor al adelanto y DESPUÉS se suelta: la quincena siguiente
    tiene que descontar la cifra corregida, no la vieja ni las dos.

        adelanto anotado $300.000, corregido a $150.000 (era lo que salió de la caja)
        quincena 1: $500.000 − $150.000 = $350.000, ya pagados $200.000, saldo $150.000
        se suelta: neto $500.000, saldo $300.000 → se paga → pagado $500.000
        quincena 2: $500.000 − $150.000 = $350.000

    Cuadre de caja: leche $1.000.000 = adelanto $150.000 + $500.000 + $350.000.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)

    liq1 = _corregir(
        client, h, liq1["id"], _cuerpo("eran $150.000", valores=[(ant["id"], "150000")])
    )
    liq1 = _corregir(
        client, h, liq1["id"], _cuerpo("y además no iba aquí", soltar=[ant["id"]])
    )
    assert D(liq1["anticipos"]) == CERO
    assert D(liq1["saldo"]) == D("300000")
    liq1 = _pagar(client, h, liq1["id"])
    assert D(liq1["pagado"]) == D("500000")
    cuadra(liq1, "quincena 1 con el valor corregido y soltado")

    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    assert D(liq2["anticipos"]) == D("150000"), (
        f"la quincena 2 descontó {liq2['anticipos']} y el adelanto vale $150.000"
    )
    assert D(liq2["neto_a_pagar"]) == D("350000")
    cuadra(liq2, "quincena 2 con el valor corregido")

    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    caja = D("150000") + D(liq1["pagado"]) + D(liq2["pagado"])
    assert caja == D("1000000"), f"la caja entregó {caja} por $1.000.000 de leche"


# ===========================================================================
# 8 · EL DIÁLOGO NO PUEDE PROMETER UNA CIFRA QUE EL BOTÓN NO ESCRIBE
# ===========================================================================
def test_el_valor_de_un_adelanto_suelto_que_no_se_incluye_rebota_en_las_dos_puertas(
    client, base_datos
):
    """Mandar `valores_de_anticipos` de un adelanto que está SUELTO y que NO se está
    incluyendo.

    Es lo que pasa cuando el dueño le teclea el valor en el diálogo y después desmarca
    la casilla: la pantalla manda el valor y no manda el id en `anticipos_a_incluir`.

    Ese valor NO puede escribirse en silencio —sería tocar plata de un documento que no
    se está corrigiendo, sin que ningún comprobante lo explique— y tampoco puede
    reventar. Tiene que rebotar POR LAS DOS PUERTAS con el mismo veredicto: el diálogo y
    el botón no pueden estar en desacuerdo sobre si esto se puede o no.

    Cifras del montaje: quincena de $500.000 pagada completa, adelanto suelto de
    $300.000, al que se le teclea $150.000 sin marcarlo. Después del rebote el adelanto
    tiene que seguir valiendo $300.000 y seguir suelto, que es lo que le permite a la
    quincena siguiente descontarlo entero.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    _recepcion(client, h, prov, "2026-06-02", "250")
    liq1 = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq1["id"])
    liq1 = _pagar(client, h, liq1["id"])
    ant = _anticipo(client, h, prov, "2026-06-05", "300000")

    cuerpo = _cuerpo("le teclee el valor y desmarque la casilla", valores=[(ant["id"], "150000")])

    r_previo = client.post(
        f"{API}/{liq1['id']}/corregir/previsualizar", json=cuerpo, headers=h
    )
    r_boton = _corregir_crudo(client, h, liq1["id"], cuerpo)
    assert r_boton.status_code < 500, (
        f"el botón reventó con {r_boton.status_code} sobre una quincena ya pagada: "
        f"{r_boton.text}"
    )
    assert r_previo.status_code == r_boton.status_code == 422, (
        f"el diálogo contestó {r_previo.status_code} y el botón {r_boton.status_code}: "
        "uno de los dos está mintiendo"
    )

    despues = _leer(client, h, liq1["id"])
    assert D(despues["anticipos"]) == CERO
    assert despues["version"] == 1, "un intento rebotado emitió un comprobante nuevo"
    visto = _ver_anticipo(client, h, ant["id"])
    assert D(visto["valor"]) == D("300000"), (
        f"el rebote alcanzó a reescribirle el valor al adelanto: quedó en {visto['valor']}"
    )
    assert visto["liquidacion_id"] is None
    cuadra(despues, "tras el valor sin incluir")


def test_incluir_y_corregirle_el_valor_de_una_vez_deja_un_solo_renglon_en_el_desglose(
    client, base_datos
):
    """El adelanto llegó tarde Y además estaba mal digitado: entra y se corrige en la
    MISMA corrección.

        quincena $500.000, pagada completa ($500.000)
        adelanto suelto anotado $300.000, pero lo que salió de la caja fueron $150.000
        entra corregido      → anticipos $150.000
        neto  = 500.000 − 150.000                        = $350.000
        saldo = 350.000 − 500.000                        = −$150.000  (le quedó debiendo)

    Y EL DESGLOSE SUMA EXACTO LA DIFERENCIA: un solo renglón, de $150.000, que es lo que
    el comprobante resta. Dos renglones —uno "entró $300.000" y otro "valor $150.000"—
    sumarían $450.000 contra una cifra grande que se movió $150.000.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    _recepcion(client, h, prov, "2026-06-02", "250")
    liq1 = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq1["id"])
    liq1 = _pagar(client, h, liq1["id"])
    ant = _anticipo(client, h, prov, "2026-06-05", "300000")

    cuerpo = _cuerpo(
        "entró tarde y estaba mal digitado",
        incluir=[ant["id"]],
        valores=[(ant["id"], "150000")],
    )
    previo = _previsualizar(client, h, liq1["id"], cuerpo)
    assert D(previo["anticipos_despues"]) == D("150000")

    liq1 = _corregir(client, h, liq1["id"], cuerpo)
    assert D(liq1["anticipos"]) == D("150000")
    assert D(liq1["neto_a_pagar"]) == D("350000")
    assert D(liq1["saldo"]) == D("-150000")
    assert D(liq1["le_queda_debiendo"]) == D("150000")
    cuadra(liq1, "entró corregido")
    assert D(_ver_anticipo(client, h, ant["id"])["valor"]) == D("150000")

    correccion = _correcciones(client, h, liq1["id"])[0]
    movidos = correccion["anticipos_cambiados"]
    assert len(movidos) == 1, f"el desglose trae {len(movidos)} renglones: {movidos}"
    movido = sum((D(c["valor"]) for c in movidos), CERO)
    diferencia = D(correccion["anticipos_despues"]) - D(correccion["anticipos_antes"])
    assert movido == diferencia == D("150000"), (
        f"el desglose suma {movido} y la cifra grande se movió {diferencia}"
    )


# ===========================================================================
# 9 · EL ADELANTO SOLTADO CUANDO LA SIGUIENTE YA NO ES UN BORRADOR
# ===========================================================================
def test_el_adelanto_soltado_no_se_pierde_aunque_la_quincena_siguiente_ya_este_aprobada(
    client, base_datos
):
    """El caso feo del calendario: cuando se suelta el adelanto, la quincena siguiente YA
    ESTÁ APROBADA, y los anticipos pendientes solo se barren sobre un BORRADOR.

    Esa plata se le entregó en la mano. Si ninguna quincena vuelve a encontrarla, se
    perdió. Aquí se comprueba que sigue PENDIENTE y que la quincena que venga —la 3— se
    la descuenta, una sola vez.

        quincena 1 (01–15/06): $500.000 − $300.000 de adelanto = $200.000  ← pagados
        quincena 2 (16–30/06): $500.000, aprobada ANTES de corregir        → $500.000
        se corrige la 1 y se suelta el adelanto: neto $500.000, faltan     → $300.000
        quincena 3 (01–15/07): $400.000 − $300.000                        = $100.000

        leche = 500.000 + 500.000 + 400.000                    = $1.400.000
        caja  = 300.000 (adelanto) + 500.000 + 500.000 + 100.000 = $1.400.000
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)

    # la quincena 2 se genera y se APRUEBA antes de tocar la 1
    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    _aprobar(client, h, liq2["id"])
    assert D(liq2["anticipos"]) == CERO

    liq1 = _corregir(
        client, h, liq1["id"], _cuerpo("era de la quincena de mayo", soltar=[ant["id"]])
    )
    liq1 = _pagar(client, h, liq1["id"])
    assert D(liq1["pagado"]) == D("500000")

    liq2 = _pagar(client, h, liq2["id"])
    assert D(liq2["anticipos"]) == CERO, "la aprobada se tragó un adelanto por detrás"
    assert D(liq2["pagado"]) == D("500000")
    cuadra(liq2, "quincena 2 ya aprobada")

    # y la que venga se lo descuenta
    _recepcion(client, h, prov, "2026-07-03", "200")
    liq3 = _de(_generar(client, h, Q3), prov)
    assert D(liq3["valor_total"]) == D("400000")
    assert D(liq3["anticipos"]) == D("300000"), (
        "el adelanto soltado no lo recogió ninguna quincena: esa plata se entregó en la "
        "mano y se acaba de perder"
    )
    assert D(liq3["neto_a_pagar"]) == D("100000")
    cuadra(liq3, "quincena 3")
    _aprobar(client, h, liq3["id"])
    liq3 = _pagar(client, h, liq3["id"])

    assert _ver_anticipo(client, h, ant["id"])["liquidacion_id"] == liq3["id"]
    leche = D("500000") + D("500000") + D("400000")
    caja = D("300000") + D(liq1["pagado"]) + D(liq2["pagado"]) + D(liq3["pagado"])
    print("\n===== TRES QUINCENAS: LA CAJA CONTRA LA LECHE =====")
    print(f"  leche {leche} · caja {caja}")
    assert caja == leche, f"la caja entregó {caja} por {leche} de leche"


def test_el_adelanto_que_ya_reservo_un_borrador_de_la_siguiente_no_entra_aqui(
    client, base_datos
):
    """El borrador de la quincena 2 ya se apartó el adelanto suelto.

    Un borrador no es un papel entregado, pero su cifra ya está escrita y el dueño la
    está mirando. Si la corrección de la quincena 1 se lo pudiera llevar, el borrador de
    la 2 seguiría diciendo "− Anticipos $300.000" con un adelanto que ya no es suyo —y si
    en vez de llevárselo lo COPIARA, el mismo adelanto quedaría restado en dos
    comprobantes—.

        quincena 1: $500.000 pagados completos, adelanto registrado tarde y suelto
        quincena 2 (borrador): $500.000 − $300.000 = $200.000
        se intenta meter el adelanto en la 1 → tiene que rebotar
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    _recepcion(client, h, prov, "2026-06-02", "250")
    liq1 = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq1["id"])
    liq1 = _pagar(client, h, liq1["id"])
    ant = _anticipo(client, h, prov, "2026-06-05", "300000")

    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    assert D(liq2["anticipos"]) == D("300000")
    assert liq2["estado"] == "borrador"

    r = _corregir_crudo(
        client, h, liq1["id"], _cuerpo("ese adelanto era de junio 1", incluir=[ant["id"]])
    )
    assert r.status_code == 422, (
        f"el adelanto del borrador de la quincena 2 se coló en la 1: {r.text}"
    )

    liq1 = _leer(client, h, liq1["id"])
    liq2 = _leer(client, h, liq2["id"])
    assert D(liq1["anticipos"]) == CERO
    assert liq1["version"] == 1
    assert D(liq2["anticipos"]) == D("300000"), "el rebote le movió la cifra al borrador"
    assert _ver_anticipo(client, h, ant["id"])["liquidacion_id"] == liq2["id"]
    cuadra(liq1, "quincena 1 tras el rebote")
    cuadra(liq2, "borrador de la quincena 2 tras el rebote")


# ===========================================================================
# 10 · LA OTRA QUESERA
# ===========================================================================
def test_la_otra_quesera_no_puede_soltar_ni_incluir_el_adelanto_de_esta(
    client, base_datos
):
    """El adelanto es plata de un tenant. Ni el id de la quincena ni el del anticipo
    pueden servirle a la quesera de al lado para mover una cifra acá.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    prov = _proveedor(client, h_a)
    liq1, ant = _quincena_con_anticipo(client, h_a, prov)

    for cuerpo in (
        _cuerpo("suéltamelo", soltar=[ant["id"]]),
        _cuerpo("méteme ese", incluir=[ant["id"]]),
    ):
        r = _corregir_crudo(client, h_b, liq1["id"], cuerpo)
        assert r.status_code == 404, f"la otra quesera llegó a la quincena: {r.text}"

    despues = _leer(client, h_a, liq1["id"])
    assert D(despues["anticipos"]) == D("300000")
    assert despues["version"] == 1
    assert _ver_anticipo(client, h_a, ant["id"])["liquidacion_id"] == liq1["id"]
    cuadra(despues, "tras el intento de la otra quesera")


# ===========================================================================
# 11 · EL PAPEL: EL DESGLOSE DE ADELANTOS SUMA EXACTO EL RENGLÓN DEL RESUMEN
# ===========================================================================
def test_el_comprobante_corregido_no_imprime_el_adelanto_que_ya_se_solto(
    client, base_datos
):
    """EL PAPEL SE LEE, NO SE SUPONE. Con DOS adelantos y uno soltado.

    Es el renglón donde el doble descuento se vería de verdad: el productor tiene la
    hoja en la mano y suma la tabla "Anticipos descontados" contra el renglón "Anticipos
    aplicados" del resumen. Si el soltado sigue impreso, el papel está cobrando plata que
    esta quincena ya no descuenta.

        02/06 · 250,00 L a $2.000,00                  $500.000
        VALOR TOTAL                                   $500.000
        Anticipos: 05/06 $300.000 (droga) + 08/06 $120.000 (mercado) = $420.000
        NETO A PAGAR = 500.000 − 420.000            = $80.000   ← y se le pagaron

    Se suelta el del MERCADO ($120.000), que era de la quincena pasada:

        VALOR TOTAL                                   $500.000
        − Anticipos aplicados                         $300.000   ← solo el de la droga
        − Pagado                                       $80.000
        SALDO A PAGAR                                 $120.000

    Y la tabla de adelantos del papel tiene que traer UN solo renglón, el de $300.000,
    que suma exacto el renglón del resumen.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    _recepcion(client, h, prov, "2026-06-02", "250")
    droga = _anticipo(client, h, prov, "2026-06-05", "300000", "el de la droga")
    mercado = _anticipo(client, h, prov, "2026-06-08", "120000", "el del mercado")
    liq = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])
    assert D(liq["anticipos"]) == D("420000")
    assert D(liq["pagado"]) == D("80000")

    liq = _corregir(
        client,
        h,
        liq["id"],
        _cuerpo("ese adelanto era de la quincena pasada", soltar=[mercado["id"]]),
    )
    assert D(liq["anticipos"]) == D("300000")
    assert D(liq["saldo"]) == D("120000")
    cuadra(liq, "con dos adelantos y uno soltado")

    papel = _pdf(client, h, liq["id"])
    print("\n===== EL PAPEL CORREGIDO =====")
    print(papel[:1200])

    assert "el de la droga" in papel, "el adelanto que SÍ se descuenta no salió impreso"
    assert "el del mercado" not in papel, (
        "el comprobante sigue imprimiendo un adelanto que esta quincena ya no descuenta: "
        "el productor lo va a sumar dos veces"
    )
    # Y el papel DICE lo que pasó con el que salió, con su fecha y su cifra: es lo que le
    # explica al productor por qué su hoja nueva descuenta $120.000 menos, y a dónde se
    # fueron esos $120.000 que él sí recibió.
    assert "08/06/2026" in papel and "$120.000" in papel, (
        "el papel no dice qué se hizo con el adelanto que salió"
    )
    assert "se le descuenta en la siguiente" in papel, papel

    # El resumen, leído de arriba abajo como lo suma el dueño.
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    pagado = renglon(papel, "Pagado")
    saldo = renglon(papel, "SALDO A PAGAR")
    assert total == D("500000")
    assert anticipos == D("-300000"), f"el papel dice {anticipos} de adelantos"
    assert pagado == D("-80000")
    assert total + anticipos + pagado == saldo == D("120000"), (
        f"la columna del resumen no cae en el saldo: {total} {anticipos} {pagado} → {saldo}"
    )

    # Y la tabla de adelantos suma exacto ese renglón.
    fila = renglon(papel, "05/06/2026")
    assert fila == D("300000")
    assert fila == -anticipos, (
        "la tabla 'Anticipos descontados' no suma el renglón 'Anticipos aplicados'"
    )


# ===========================================================================
# 12 · CUANDO LA DEUDA YA VIAJÓ, EL ADELANTO NO SE PUEDE MOVER
# ===========================================================================
def test_el_adelanto_que_dejo_la_quincena_debiendo_no_se_suelta_despues_de_que_viajo(
    client, base_datos
):
    """El camino más caro que queda: mover el adelanto DESPUÉS de que su deuda se cobró
    en otro comprobante.

    Incluir el adelanto deja la quincena 1 en negativo, y la quincena 2 se cobra esa
    deuda en su renglón CONGELADO `saldo_anterior`. Si después se pudiera soltar el
    adelanto de la 1, su saldo volvería a cero mientras la 2 ya le cobró $300.000 por esa
    misma deuda: el mismo adelanto descontado dos veces, una por cada hoja, y las dos
    impresas.

        quincena 1: $500.000, pagada completa                     pagado $500.000
        entra el adelanto de $300.000 (llegó tarde):
            neto = 500.000 − 300.000                            = $200.000
            saldo = 200.000 − 500.000                           = −$300.000
        quincena 2: $500.000 de leche − $300.000 de deuda vieja  = $200.000

        leche = 500.000 + 500.000                                = $1.000.000
        caja  = 300.000 (adelanto) + 500.000 + 200.000           = $1.000.000
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    _recepcion(client, h, prov, "2026-06-02", "250")
    liq1 = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq1["id"])
    liq1 = _pagar(client, h, liq1["id"])
    ant = _anticipo(client, h, prov, "2026-06-05", "300000")

    liq1 = _corregir(client, h, liq1["id"], _cuerpo("el adelanto del 05", incluir=[ant["id"]]))
    assert D(liq1["saldo"]) == D("-300000")
    assert D(liq1["le_queda_debiendo"]) == D("300000")
    cuadra(liq1, "quincena 1 en negativo por el adelanto")

    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    assert D(liq2["saldo_anterior"]) == D("300000"), "la deuda del adelanto no viajó"
    assert D(liq2["neto_a_pagar"]) == D("200000")
    cuadra(liq2, "quincena 2 cobrando la deuda")

    # EL ATAQUE: soltar el adelanto ahora que su deuda ya está cobrada en la 2.
    r = _corregir_crudo(client, h, liq1["id"], _cuerpo("mejor lo suelto", soltar=[ant["id"]]))
    assert r.status_code == 422, (
        f"se soltó el adelanto después de que su deuda ya se cobró en otra hoja: {r.text}"
    )
    r = _corregir_crudo(
        client, h, liq1["id"], _cuerpo("le bajo el valor", valores=[(ant["id"], "100000")])
    )
    assert r.status_code == 422, f"se le cambió el valor con la deuda ya cobrada: {r.text}"

    liq1 = _leer(client, h, liq1["id"])
    assert D(liq1["anticipos"]) == D("300000")
    assert D(liq1["le_queda_debiendo"]) == D("300000"), (
        "la deuda que la quincena 2 ya cobró cambió por detrás"
    )
    assert D(_ver_anticipo(client, h, ant["id"])["valor"]) == D("300000")
    cuadra(liq1, "quincena 1 tras el rebote")

    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    caja = D("300000") + D(liq1["pagado"]) + D(liq2["pagado"])
    assert caja == D("1000000"), f"la caja entregó {caja} por $1.000.000 de leche"


# ===========================================================================
# 13 · LA QUINCENA SIGUIENTE SE GENERA ANTES DE PAGAR LA CORREGIDA
# ===========================================================================
def test_el_adelanto_soltado_lo_recoge_la_siguiente_aunque_la_corregida_siga_debiendo(
    client, base_datos
):
    """El orden de verdad en la finca: el dueño corrige, NO alcanza a pagar la diferencia
    ese día, y a la semana siguiente le toca generar la quincena nueva.

    La quincena 1 queda 'parcial' debiéndole $300.000 al productor, y la 2 tiene que
    descontarle el adelanto igual: son dos cosas distintas —lo que se le debe por leche y
    lo que se le adelantó en efectivo— y confundirlas es justo lo que deja la plata
    contada dos veces.

        quincena 1 corregida: neto $500.000, pagado $200.000, saldo $300.000 (sin pagar)
        quincena 2:           $500.000 − $300.000 de adelanto = $200.000

        leche = 500.000 + 500.000                                  = $1.000.000
        caja  = 300.000 (adelanto) + 200.000 + 300.000 + 200.000   = $1.000.000
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)
    liq1 = _corregir(client, h, liq1["id"], _cuerpo("era de mayo", soltar=[ant["id"]]))
    assert liq1["estado"] == "parcial"
    assert D(liq1["saldo"]) == D("300000")

    # la quincena 2 se genera CON la 1 todavía debiendo
    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    assert D(liq2["anticipos"]) == D("300000"), (
        "la quincena 2 no recogió el adelanto soltado porque la 1 quedó debiendo"
    )
    assert D(liq2["saldo_anterior"]) == CERO, (
        "lo que el NEGOCIO le debe al productor se le cobró como si fuera deuda de él"
    )
    assert D(liq2["neto_a_pagar"]) == D("200000")
    cuadra(liq2, "quincena 2 con la 1 en parcial")

    liq1 = _pagar(client, h, liq1["id"])
    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    caja = D("300000") + D(liq1["pagado"]) + D(liq2["pagado"])
    assert caja == D("1000000"), f"la caja entregó {caja} por $1.000.000 de leche"
    assert _ver_anticipo(client, h, ant["id"])["liquidacion_id"] == liq2["id"]


def test_soltar_uno_y_corregirle_el_valor_al_otro_en_la_misma_correccion_cuadra(
    client, base_datos
):
    """Las dos operaciones sobre adelantos distintos, en una sola corrección.

        02/06 · 250,00 L a $2.000,00                       $500.000
        adelantos: 05/06 $300.000 (droga) + 08/06 $120.000 (mercado) = $420.000
        NETO = 500.000 − 420.000 = $80.000                 ← y se le pagaron

    Se suelta el del mercado (era de la quincena pasada) y el de la droga estaba mal
    digitado: eran $250.000.

        anticipos = $250.000
        neto  = 500.000 − 250.000                        = $250.000
        saldo = 250.000 − 80.000                         = $170.000

    Y el desglose de la corrección tiene que sumar exacto lo que se movió la cifra grande:
        420.000 → 250.000, o sea −$170.000
        salió el del mercado −$120.000, y el de la droga bajó $50.000 = −$170.000
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    _recepcion(client, h, prov, "2026-06-02", "250")
    droga = _anticipo(client, h, prov, "2026-06-05", "300000", "el de la droga")
    mercado = _anticipo(client, h, prov, "2026-06-08", "120000", "el del mercado")
    liq = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])
    assert D(liq["anticipos"]) == D("420000")
    assert D(liq["pagado"]) == D("80000")

    cuerpo = _cuerpo(
        "el del mercado no iba aquí y el otro estaba mal digitado",
        soltar=[mercado["id"]],
        valores=[(droga["id"], "250000")],
    )
    previo = _previsualizar(client, h, liq["id"], cuerpo)
    assert D(previo["anticipos_despues"]) == D("250000")

    liq = _corregir(client, h, liq["id"], cuerpo)
    assert D(liq["anticipos"]) == D("250000")
    assert D(liq["neto_a_pagar"]) == D("250000")
    assert D(liq["saldo"]) == D("170000")
    cuadra(liq, "una operación de cada tipo")
    assert D(_ver_anticipo(client, h, droga["id"])["valor"]) == D("250000")
    assert D(_ver_anticipo(client, h, mercado["id"])["valor"]) == D("120000"), (
        "al que se soltó se le tocó el valor de rebote"
    )

    correccion = _correcciones(client, h, liq["id"])[0]
    movido = CERO
    for c in correccion["anticipos_cambiados"]:
        if c["accion"] == "salio":
            movido -= D(c["valor"])
        elif c["accion"] == "valor":
            movido += D(c["valor"]) - D(c["valor_antes"])
    diferencia = D(correccion["anticipos_despues"]) - D(correccion["anticipos_antes"])
    assert movido == diferencia == D("-170000"), (
        f"el desglose suma {movido} y la cifra grande se movió {diferencia}"
    )

    # la quincena siguiente recoge el soltado, con SU valor, una sola vez
    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    assert D(liq2["anticipos"]) == D("120000")
    cuadra(liq2, "quincena 2")


# ===========================================================================
# 14 · EL ADELANTO SOLTADO SE QUEDA SIN CANDADO
# ===========================================================================
# Estas dos están en ROJO A PROPÓSITO (xfail estricto): miden un hueco real, reproducido
# corriendo el código, y NO se arreglan desde aquí.
#
# EL CANDADO DE LOS ANTICIPOS (`AnticipoService._exigir_no_pagado`) rebota borrar o
# modificar el adelanto de una quincena ya pagada, con estas palabras: «No se puede
# eliminar este anticipo: la liquidación en la que se descontó ya se pagó». Ese candado
# mira UNA sola cosa: a qué liquidación apunta el anticipo.
#
# La corrección lo SUELTA (`liquidacion_id = None`), que es la decisión correcta —esa
# plata se entregó y no se puede borrar— PERO al soltarlo el candado se abre solo: el
# adelanto vuelve a parecer un pendiente recién registrado, y desde la pantalla de
# anticipos se puede borrar, o cambiarle el valor y la fecha, sin motivo, sin versión y
# sin que ningún papel lo diga.
#
# Y NO ES UN PENDIENTE CUALQUIERA: ya salió impreso en DOS comprobantes. El v1 decía
# «Anticipos aplicados − $300.000» y el v2 dice, con todas sus letras, «el adelanto del
# 05/06/2026 ($300.000) ya NO se descuenta en esta quincena: se le descuenta en la
# siguiente». Borrarlo o rebajarlo rompe esa promesa impresa en silencio, y la plata que
# el productor recibió en la mano no la recupera nadie.
def test_el_adelanto_soltado_no_deberia_poderse_borrar_desde_la_pantalla_de_anticipos(
    client, base_datos
):
    """Se suelta el adelanto y acto seguido se borra desde la pantalla de anticipos.

        quincena 1: $500.000 − $300.000 de adelanto = $200.000  ← pagados
        se suelta el adelanto → saldo $300.000 → se paga        → pagado $500.000
        SE BORRA EL ADELANTO  (hoy contesta 204: se borró)
        quincena 2: $500.000, sin adelanto que descontar        → $500.000

        leche = 500.000 + 500.000                               = $1.000.000
        caja  = 300.000 (en la mano) + 500.000 + 500.000        = $1.300.000
                                                        de más  =   $300.000

    Con el candado puesto el borrado rebota, la quincena 2 descuenta los $300.000, le
    paga $200.000 y la caja cae exacta en $1.000.000: esta prueba se pone verde sola.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)
    liq1 = _corregir(client, h, liq1["id"], _cuerpo("era de mayo", soltar=[ant["id"]]))
    liq1 = _pagar(client, h, liq1["id"])
    assert D(liq1["pagado"]) == D("500000")

    r = client.delete(f"{ANT}/{ant['id']}", headers=h)
    print(f"\n===== BORRAR EL ADELANTO SOLTADO -> {r.status_code} {r.text[:160]}")

    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    cuadra(liq2, "quincena 2 con el adelanto borrado")

    leche = D("1000000")
    caja = D("300000") + D(liq1["pagado"]) + D(liq2["pagado"])
    print(f"===== leche {leche} · caja {caja} · de más {caja - leche}")
    assert caja == leche, (
        f"la caja entregó {caja} por {leche} de leche: se borró un adelanto de $300.000 "
        "que ya estaba impreso en dos comprobantes y nadie lo recuperó"
    )


def test_al_adelanto_soltado_no_deberia_poderse_rebajar_el_valor_sin_motivo(
    client, base_datos
):
    """La misma puerta, sin borrar: se le rebaja el valor a $1.

    Es peor que el borrado porque no deja hueco visible —el adelanto sigue ahí, con su
    fecha y sus observaciones— y la quincena siguiente descuenta $1 donde tenían que ir
    $300.000. Esa rebaja no pasa por ninguna de las cinco protecciones de la corrección:
    ni motivo escrito, ni versión del comprobante, ni renglón que lo diga.

        quincena 1: $500.000 − $300.000 = $200.000 pagados, se suelta, se paga $500.000
        se le baja el valor a $1        (hoy contesta 200: se cambió)
        quincena 2: $500.000 − $1 = $499.999

        leche = $1.000.000
        caja  = 300.000 + 500.000 + 499.999 = $1.299.999 · de más $299.999
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    liq1, ant = _quincena_con_anticipo(client, h, prov)
    liq1 = _corregir(client, h, liq1["id"], _cuerpo("era de mayo", soltar=[ant["id"]]))
    liq1 = _pagar(client, h, liq1["id"])

    r = client.put(f"{ANT}/{ant['id']}", json={"valor": "1"}, headers=h)
    print(f"\n===== REBAJARLE EL VALOR AL SOLTADO -> {r.status_code} {r.text[:160]}")

    _recepcion(client, h, prov, "2026-06-20", "250")
    liq2 = _de(_generar(client, h, Q2), prov)
    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    cuadra(liq2, "quincena 2 con el adelanto rebajado")

    leche = D("1000000")
    caja = D("300000") + D(liq1["pagado"]) + D(liq2["pagado"])
    print(f"===== leche {leche} · caja {caja} · de más {caja - leche}")
    assert caja == leche, (
        f"la caja entregó {caja} por {leche} de leche: al adelanto soltado se le rebajó "
        "el valor sin motivo ni versión y la quincena siguiente descontó $1"
    )
