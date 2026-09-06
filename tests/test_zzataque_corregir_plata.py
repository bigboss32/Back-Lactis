"""ATAQUE A LA CORRECCIÓN DE UNA QUINCENA PAGADA: QUE LA MISMA PLATA NO SE PAGUE DOS
VECES NI DESAPAREZCA.

Este archivo no comprueba que la corrección "funcione": comprueba que NO SE PUEDA SACAR
PLATA DOS VECES POR NINGUNA DE SUS PUERTAS. Cada prueba persigue un camino concreto por
el que un peso podría contarse dos veces o perderse:

  · un día ya pagado que se vuelva a liquidar (la marca `RecepcionLeche.liquidacion_id`
    es LO ÚNICO que lo impide: `solapada_para_periodo` deja pasar a propósito a las
    pagadas, así que volver a oprimir Generar sobre esa quincena es legítimo);
  · un anticipo que se descuente dos veces, o que un comprobante ya entregado se trague
    uno registrado tarde;
  · una deuda arrastrada que se cobre dos veces, o que deje de cobrarse;
  · corregir y volver a generar el período, buscando un tercer documento que se pise;
  · la igualdad que el dueño verifica con calculadora: neto_a_pagar = pagado + saldo, y
    la suma de la columna Valor = VALOR TOTAL.

TODAS LAS CIFRAS ESTÁN CALCULADAS A MANO en el docstring de cada prueba, nunca con el
mismo código que se está probando.
"""
import io
import re
from decimal import ROUND_HALF_UP, Decimal

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


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="1800"):
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


def _anticipo(client, h, prov, fecha, valor):
    r = client.post(
        ANT,
        json={
            "fecha": fecha,
            "valor": str(valor),
            "tipo": "proveedor",
            "proveedor_id": prov["id"],
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
    return r.json()


def _de(generadas, nombre):
    encontradas = [
        liq
        for liq in generadas
        if (liq.get("proveedor_nombre") or liq.get("transportador_nombre")) == nombre
    ]
    assert len(encontradas) == 1, f"se esperaba una sola de {nombre}: {generadas}"
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


def _previsualizar_correccion(client, h, liq_id, *, motivo="corrección", incluir=(), precios=()):
    r = client.post(
        f"{API}/{liq_id}/corregir/previsualizar",
        json={
            "motivo": motivo,
            "recepciones_a_incluir": list(incluir),
            "precios": list(precios),
        },
        headers=h,
    )
    return r


def _corregir(client, h, liq_id, *, motivo, incluir=(), precios=()):
    r = client.post(
        f"{API}/{liq_id}/corregir",
        json={
            "motivo": motivo,
            "recepciones_a_incluir": list(incluir),
            "precios": list(precios),
        },
        headers=h,
    )
    return r


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
    return " ".join(crudo.split())


_CIFRA = re.compile(r"(-?)\s*\$\s*(-?)([\d.]+(?:,\d{2})?)")


def renglon(papel, rotulo):
    """La cifra del renglón del comprobante, CON SU SIGNO. Se lee del papel impreso
    porque es lo que el dueño suma con calculadora."""
    inicio = papel.find(rotulo)
    assert inicio >= 0, f"el comprobante no trae el renglón «{rotulo}»:\n{papel}"
    resto = papel[inicio + len(rotulo):]
    encontrado = _CIFRA.search(resto)
    assert encontrado, f"el renglón «{rotulo}» salió sin cifra:\n{resto[:120]}"
    signo, signo_interno, cifra = encontrado.groups()
    valor = D(cifra.replace(".", "").replace(",", "."))
    return -valor if (signo == "-" or signo_interno == "-") else valor


# --------------------------------------------------------- la regla de la casa
def cuadra(liq):
    """LA IGUALDAD QUE MANDA SOBRE TODO: neto_a_pagar = pagado + saldo.

    Y la segunda: la columna Valor del detalle suma EXACTO el VALOR TOTAL. El dueño
    verifica las dos a mano con calculadora; un centavo de diferencia es un defecto.
    """
    neto = D(liq["neto_a_pagar"])
    assert neto == D(liq["pagado"]) + D(liq["saldo"]), (
        f"neto_a_pagar {neto} != pagado {liq['pagado']} + saldo {liq['saldo']}"
    )
    # neto = valor_total - anticipos - saldo_anterior, la otra resta impresa
    assert neto == D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq["saldo_anterior"])
    suma_dias = sum((D(d["valor"]) for d in liq.get("detalles", [])), CERO)
    assert suma_dias == D(liq["valor_total"]), (
        f"la columna Valor suma {suma_dias} y el VALOR TOTAL dice {liq['valor_total']}"
    )


def _recepciones_de(client, h, prov, periodo):
    r = client.get(
        REC,
        params={
            "proveedor_id": prov["id"],
            "desde": periodo[0],
            "hasta": periodo[1],
            "size": 100,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ===========================================================================
# 1. EL DÍA OLVIDADO ENTRA UNA SOLA VEZ, Y VOLVER A GENERAR NO PRODUCE UN
#    SEGUNDO DOCUMENTO QUE LO COBRE OTRA VEZ
# ===========================================================================
def test_el_dia_olvidado_entra_una_vez_y_generar_de_nuevo_no_lo_vuelve_a_cobrar(
    client, base_datos
):
    """Las cifras del dueño, calculadas a mano:

    QUINCENA 1 (01 al 15 de junio) — Henri C:
        02/06  100 L a $2.500 = $250.000
        03/06  100 L a $2.500 = $250.000
        VALOR TOTAL                        $500.000
        se paga completa                   $500.000  -> pagada, saldo $0

    ENTRA EL DÍA OLVIDADO del 12/06, 100 L a $1.800 = $180.000:
        VALOR TOTAL                        $680.000
        pagado (NO SE TOCA)               -$500.000
        QUEDA POR ENTREGARLE               $180.000  -> parcial, v2

    EL ATAQUE: volver a oprimir Generar sobre ESE MISMO PERÍODO. Es legítimo hacerlo
    —una pagada no reserva sus fechas a propósito— y lo ÚNICO que impide que el día del
    12/06 salga en un SEGUNDO comprobante de $180.000 es que la corrección le haya
    dejado puesta la marca `liquidacion_id`. Si esa marca no quedara, de la caja
    saldrían $180.000 dos veces por la misma leche.

    Y al final se oprime Pagar: la caja tiene que haber entregado $680.000 EXACTOS por
    $680.000 de leche, ni un peso más.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    assert D(liq["valor_total"]) == D(500000)
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])
    assert liq["estado"] == "pagada"
    assert D(liq["pagado"]) == D(500000)
    cuadra(liq)

    # El día que se anotó tarde: queda SUELTO dentro del período ya pagado.
    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)

    previa = _previsualizar_correccion(
        client, h, liq["id"], incluir=[olvidado["id"]]
    )
    assert previa.status_code == 200, previa.text
    previa = previa.json()
    assert D(previa["valor_total_despues"]) == D(680000)
    assert D(previa["saldo_despues"]) == D(180000)
    assert D(previa["queda_por_entregar"]) == D(180000)

    r = _corregir(
        client, h, liq["id"], motivo="se le olvidó el día 12", incluir=[olvidado["id"]]
    )
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert corregida["version"] == 2
    assert corregida["estado"] == "parcial"
    assert D(corregida["valor_total"]) == D(680000)
    assert D(corregida["pagado"]) == D(500000)
    assert D(corregida["saldo"]) == D(180000)
    assert len(corregida["detalles"]) == 3
    cuadra(corregida)

    # ---- EL ATAQUE: volver a correr la quincena sobre el mismo período.
    respuesta = _generar(client, h, Q1)
    generadas_de_henri = [
        g for g in respuesta["generadas"] if g.get("proveedor_nombre") == "Henri C"
    ]
    assert generadas_de_henri == [], (
        "se generó un SEGUNDO comprobante del mismo período: el día del 12/06 se "
        f"cobraría dos veces. {generadas_de_henri}"
    )

    # y la corregida sigue valiendo lo mismo: nadie le quitó ni le agregó un día
    de_nuevo = _leer(client, h, corregida["id"])
    assert D(de_nuevo["valor_total"]) == D(680000)
    cuadra(de_nuevo)

    # ---- Y AHORA SE PAGA LO QUE FALTA: la caja entrega $180.000 más, no $680.000.
    pagada = _pagar(client, h, corregida["id"])
    assert pagada["estado"] == "pagada"
    assert D(pagada["pagado"]) == D(680000)
    assert D(pagada["saldo"]) == CERO
    cuadra(pagada)
    salido_de_caja = sum((D(p["valor"]) for p in pagada["pagos"]), CERO)
    assert salido_de_caja == D(680000), (
        f"la caja entregó {salido_de_caja} por $680.000 de leche"
    )


# ===========================================================================
# 2. CORREGIR DOS VECES (v3 SOBRE v2): EL PRIMER DÍA OLVIDADO NO SE CUENTA DOS VECES
# ===========================================================================
def test_corregir_dos_veces_no_cuenta_dos_veces_el_dia_que_ya_entro(client, base_datos):
    """Dos días olvidados, entrando de a uno. Las cifras a mano:

        quincena pagada                    $500.000  (pagada con $500.000)
        + día olvidado A (12/06, $180.000) $680.000  -> v2, saldo $180.000
        + día olvidado B (13/06, $90.000)  $770.000  -> v3, saldo $270.000

    EL ATAQUE: en la SEGUNDA corrección se manda otra vez el día A, que ya entró en la
    primera. Si el sistema lo dejara entrar, el 12/06 valdría $360.000 en un comprobante
    donde el productor entregó $180.000 de leche. Tiene que rebotar, y el total tiene que
    quedar en $770.000 exactos: 680.000 + 90.000.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Marleny")
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    dia_a = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)
    dia_b = _recepcion(client, h, prov, "2026-06-13", 50, precio=1800)

    r = _corregir(client, h, liq["id"], motivo="entra el día A", incluir=[dia_a["id"]])
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert v2["version"] == 2
    assert D(v2["valor_total"]) == D(680000)
    cuadra(v2)

    # EL ATAQUE: el día A otra vez, junto con el B.
    r = _corregir(
        client,
        h,
        liq["id"],
        motivo="entra el día B (y se cuela otra vez el A)",
        incluir=[dia_a["id"], dia_b["id"]],
    )
    assert r.status_code == 422, (
        "dejó volver a meter un día que YA está en el comprobante: ese día se cobraría "
        f"dos veces. Respuesta: {r.status_code} {r.text}"
    )

    # y el comprobante no quedó a medias por el rebote: sigue en v2 con $680.000
    sin_tocar = _leer(client, h, liq["id"])
    assert sin_tocar["version"] == 2
    assert D(sin_tocar["valor_total"]) == D(680000)
    cuadra(sin_tocar)

    # la corrección buena, solo con el día B
    r = _corregir(client, h, liq["id"], motivo="entra el día B", incluir=[dia_b["id"]])
    assert r.status_code == 200, r.text
    v3 = r.json()
    assert v3["version"] == 3
    assert D(v3["valor_total"]) == D(770000)
    assert D(v3["saldo"]) == D(270000)
    assert len(v3["detalles"]) == 4
    cuadra(v3)

    # las dos correcciones quedaron escritas, cada una con su antes y su después
    r = client.get(f"{API}/{liq['id']}/correcciones", headers=h)
    assert r.status_code == 200, r.text
    correcciones = r.json()
    assert [c["version_nueva"] for c in correcciones] == [2, 3]
    assert D(correcciones[0]["valor_total_antes"]) == D(500000)
    assert D(correcciones[0]["valor_total_despues"]) == D(680000)
    assert D(correcciones[1]["valor_total_antes"]) == D(680000)
    assert D(correcciones[1]["valor_total_despues"]) == D(770000)


# ===========================================================================
# 3. EL MISMO DÍA DOS VECES EN LA MISMA PETICIÓN
# ===========================================================================
def test_el_mismo_dia_repetido_en_la_peticion_no_vale_el_doble(client, base_datos):
    """EL ATAQUE MÁS BARATO QUE HAY: mandar el mismo `recepcion_id` dos veces en
    `recepciones_a_incluir`. El campo es una lista y nadie le quita los repetidos.

    Las cifras: quincena de $500.000 pagada, día olvidado de $180.000 mandado DOS VECES.
    Lo correcto es $680.000. Si algo suma $860.000 (500.000 + 180.000 + 180.000), esa
    cifra le está cobrando a la quesera $180.000 de leche que nadie entregó.

    Se mide la PREVISUALIZACIÓN y la CORRECCIÓN, porque el dueño confirma mirando la
    primera: si el diálogo dice una cifra y el botón escribe otra, es exactamente la
    diferencia que él descubre con la calculadora cuando ya es tarde.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Aleida", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Aleida")
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)

    r = _corregir(
        client,
        h,
        liq["id"],
        motivo="el mismo día mandado dos veces",
        incluir=[olvidado["id"], olvidado["id"]],
    )
    assert r.status_code in (200, 422), r.text
    if r.status_code == 200:
        corregida = r.json()
        assert D(corregida["valor_total"]) == D(680000), (
            f"el día se contó dos veces: {corregida['valor_total']} por $680.000 de leche"
        )
        cuadra(corregida)


# ===========================================================================
# 4. ESCENARIO B: LA PLATA ENTREGADA DE MÁS SE RECUPERA UNA SOLA VEZ
# ===========================================================================
def test_lo_que_se_le_pago_de_mas_se_cobra_una_sola_vez_en_la_quincena_siguiente(
    client, base_datos
):
    """LAS CIFRAS DEL DUEÑO, calculadas a mano:

    QUINCENA 1 (01 al 15 de junio) — el precio del 02/06 se tecleó a $2.500 y era $1.500:
        antes:  02/06 100 L a $2.500 = $250.000 + 03/06 100 L a $2.500 = $250.000
                VALOR TOTAL $500.000, PAGADA con $500.000 en la mano.
        después de corregir el precio del 02/06 a $1.500:
                02/06 100 L a $1.500 = $150.000
                03/06 100 L a $2.500 = $250.000
                VALOR TOTAL                        $400.000
                pagado                            -$500.000
                SE LE PAGÓ DE MÁS                  $100.000   -> estado 'pagada'

    QUINCENA 2 (16 al 30 de junio) — 100 L a $3.000 = $300.000:
                VALOR TOTAL                        $300.000
                lo que quedó debiendo la pasada   -$100.000
                NETO A PAGAR                       $200.000   <- esto sale de la caja

    LAS DOS HOJAS TIENEN QUE SUMAR: la quesera entregó $500.000 + $200.000 = $700.000 y
    la leche de las dos quincenas valió $400.000 + $300.000 = $700.000. Exacto.

    Y LA QUINCENA 3 NO PUEDE VOLVER A COBRAR ESOS $100.000: si los cobrara, al productor
    le descontarían dos veces la misma plata.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq1 = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    _aprobar(client, h, liq1["id"])
    liq1 = _pagar(client, h, liq1["id"])
    assert D(liq1["pagado"]) == D(500000)

    dia_02 = next(d for d in liq1["detalles"] if d["fecha"] == "2026-06-02")
    r = _corregir(
        client,
        h,
        liq1["id"],
        motivo="el precio del 02/06 estaba en $2.500 y era $1.500",
        precios=[{"detalle_id": dia_02["id"], "precio_litro": "1500"}],
    )
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert D(corregida["valor_total"]) == D(400000)
    assert D(corregida["pagado"]) == D(500000)
    assert D(corregida["saldo"]) == D(-100000)
    assert D(corregida["le_queda_debiendo"]) == D(100000)
    assert corregida["estado"] == "pagada"
    assert corregida["version"] == 2
    cuadra(corregida)

    # el papel corregido lo dice con las palabras del caso: no fue un anticipo, fue
    # efectivo que salió de más
    # Los renglones se leen CON SU SIGNO, tal como salen impresos: "- $500.000" vale
    # -500.000, que es lo que el dueño resta cuando baja la columna con la calculadora.
    papel = _pdf(client, h, corregida["id"])
    assert renglon(papel, "VALOR TOTAL") == D(400000)
    assert renglon(papel, "Pagado") == D(-500000)
    assert renglon(papel, "SE LE PAGÓ DE MÁS") == D(100000)

    # ---- QUINCENA 2: la deuda se cobra AQUÍ, una sola vez
    _recepcion(client, h, prov, "2026-06-20", 100, precio=3000)
    liq2 = _de(_generar(client, h, Q2)["generadas"], "Henri C")
    assert D(liq2["valor_total"]) == D(300000)
    assert D(liq2["saldo_anterior"]) == D(100000), (
        "la quincena siguiente no le cobró lo que se le pagó de más: esos $100.000 "
        "salieron de la caja y nadie los recupera"
    )
    assert D(liq2["neto_a_pagar"]) == D(200000)
    cuadra(liq2)

    # la corregida quedó marcada: su deuda ya viajó y nadie más se la puede cobrar
    origen = _leer(client, h, corregida["id"])
    assert origen["deuda_trasladada_a_id"] == liq2["id"]

    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    assert D(liq2["pagado"]) == D(200000)
    assert D(liq2["saldo"]) == CERO
    cuadra(liq2)

    # ---- QUINCENA 3: la misma deuda NO se puede volver a cobrar
    _recepcion(client, h, prov, "2026-07-05", 100, precio=2000)
    liq3 = _de(_generar(client, h, Q3)["generadas"], "Henri C")
    assert D(liq3["saldo_anterior"]) == CERO, (
        "la quincena 3 volvió a cobrar una deuda que la quincena 2 ya había cobrado: "
        f"le descuentan {liq3['saldo_anterior']} dos veces al productor"
    )
    assert D(liq3["neto_a_pagar"]) == D(200000)
    cuadra(liq3)

    # ---- LAS DOS HOJAS SUMAN, con la calculadora en la mano
    leche = D(400000) + D(300000)
    caja = D(500000) + D(200000)
    assert caja == leche == D(700000)


# ===========================================================================
# 5. EL ANTICIPO YA DESCONTADO NO SE VUELVE A DESCONTAR, Y EL QUE LLEGA TARDE
#    NO SE LO TRAGA UN COMPROBANTE YA ENTREGADO
# ===========================================================================
def test_la_correccion_no_se_traga_el_anticipo_que_llego_tarde_ni_repite_el_de_antes(
    client, base_datos
):
    """Las cifras, a mano:

    QUINCENA 1 (01 al 15 de junio):
        02/06 y 03/06, 100 L a $2.500 cada uno      VALOR TOTAL   $500.000
        anticipo del 05/06 ya entregado en la mano  Anticipos    -$200.000
        NETO A PAGAR                                              $300.000
        se paga completo                                          $300.000 -> pagada

    Después de pagada aparecen DOS cosas: el día olvidado del 12/06 ($180.000) y un
    ANTICIPO DEL 10/06 POR $50.000 que se registró tarde, con fecha DENTRO del período.

    AL CORREGIR:
        VALOR TOTAL                                               $680.000
        Anticipos (SIGUEN SIENDO $200.000, no $250.000)          -$200.000
        NETO                                                      $480.000
        pagado                                                   -$300.000
        QUEDA POR ENTREGARLE                                      $180.000

    SI LA CORRECCIÓN SE TRAGARA EL ANTICIPO NUEVO, el neto sería $430.000 y el papel que
    el productor tiene en la mano —que dice $300.000 de anticipos $200.000— dejaría de
    cuadrar; peor: esos $50.000 quedarían descontados en un comprobante ya entregado y
    NADIE los volvería a cobrar en la quincena siguiente. Ese anticipo tiene que quedar
    SUELTO y lo tiene que recoger la quincena 2, una sola vez.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Aleida", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)
    _anticipo(client, h, prov, "2026-06-05", 200000)

    liq1 = _de(_generar(client, h, Q1)["generadas"], "Aleida")
    assert D(liq1["anticipos"]) == D(200000)
    assert D(liq1["neto_a_pagar"]) == D(300000)
    _aprobar(client, h, liq1["id"])
    liq1 = _pagar(client, h, liq1["id"])
    assert D(liq1["pagado"]) == D(300000)

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)
    tarde = _anticipo(client, h, prov, "2026-06-10", 50000)

    r = _corregir(
        client, h, liq1["id"], motivo="el día 12 se quedó por fuera", incluir=[olvidado["id"]]
    )
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert D(corregida["anticipos"]) == D(200000), (
        "la corrección se tragó el anticipo registrado tarde: el comprobante ya "
        f"entregado decía $200.000 y ahora dice {corregida['anticipos']}"
    )
    assert D(corregida["valor_total"]) == D(680000)
    assert D(corregida["neto_a_pagar"]) == D(480000)
    assert D(corregida["saldo"]) == D(180000)
    cuadra(corregida)

    # el anticipo de $50.000 sigue suelto: no lo apartó el comprobante corregido
    r = client.get(f"{ANT}/{tarde['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["liquidacion_id"] is None, (
        "el anticipo registrado tarde quedó apartado por una quincena ya entregada"
    )

    # y la quincena 2 lo recoge, UNA vez
    _recepcion(client, h, prov, "2026-06-20", 100, precio=2000)
    liq2 = _de(_generar(client, h, Q2)["generadas"], "Aleida")
    assert D(liq2["anticipos"]) == D(50000), (
        f"la quincena 2 aplicó {liq2['anticipos']} de anticipos y solo había $50.000 "
        "sueltos: o se perdió, o se descontó dos veces"
    )
    assert D(liq2["valor_total"]) == D(200000)
    assert D(liq2["neto_a_pagar"]) == D(150000)
    cuadra(liq2)


# ===========================================================================
# 6. LA DEUDA ARRASTRADA NO SE BORRA NI SE COBRA DOS VECES AL CORREGIR LA
#    QUINCENA QUE SE LA COBRÓ
# ===========================================================================
def test_corregir_la_quincena_que_cobro_una_deuda_no_la_cobra_dos_veces(client, base_datos):
    """Las cifras del dueño, calculadas a mano:

    QUINCENA 1 (01 al 15) — 100 L a $1.800 = $180.000 contra $300.000 de anticipo ya
    entregado:  neto -$120.000  ->  HENRI LE QUEDA DEBIENDO $120.000 (queda 'aprobada').

    QUINCENA 2 (16 al 30) — 100 L a $2.500 = $250.000:
        VALOR TOTAL                                     $250.000
        lo que quedó debiendo de la pasada             -$120.000
        NETO                                            $130.000, se paga -> pagada

    SE CORRIGE LA QUINCENA 2 agregando un día olvidado del 25/06 por $100.000:
        VALOR TOTAL                                     $350.000
        lo que quedó debiendo de la pasada             -$120.000   <- SIGUE SIENDO UNA
        NETO                                            $230.000
        pagado                                         -$130.000
        QUEDA POR ENTREGARLE                            $100.000

    EL ATAQUE ES DOBLE: que la corrección BORRE el `saldo_anterior` (y entonces esos
    $120.000 se le pagan al productor por segunda vez, porque su anticipo ya se los
    llevó), o que lo SUME OTRA VEZ dejándolo en $240.000 (y entonces se le descuentan
    dos veces). La marca de la quincena 1 tiene que seguir apuntando a la 2, y la
    quincena 3 no puede volver a encontrar esa deuda.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=1800)
    _anticipo(client, h, prov, "2026-06-01", 300000)

    liq1 = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    assert D(liq1["le_queda_debiendo"]) == D(120000)
    _aprobar(client, h, liq1["id"])

    _recepcion(client, h, prov, "2026-06-20", 100, precio=2500)
    liq2 = _de(_generar(client, h, Q2)["generadas"], "Henri C")
    assert D(liq2["saldo_anterior"]) == D(120000)
    assert D(liq2["neto_a_pagar"]) == D(130000)
    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    assert D(liq2["pagado"]) == D(130000)

    olvidado = _recepcion(client, h, prov, "2026-06-25", 40, precio=2500)

    r = _corregir(
        client, h, liq2["id"], motivo="faltó el día 25", incluir=[olvidado["id"]]
    )
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert D(corregida["valor_total"]) == D(350000)
    assert D(corregida["saldo_anterior"]) == D(120000), (
        f"la corrección dejó `saldo_anterior` en {corregida['saldo_anterior']}: la "
        "deuda de la quincena 1 se está cobrando de más o dejó de cobrarse"
    )
    assert D(corregida["neto_a_pagar"]) == D(230000)
    assert D(corregida["pagado"]) == D(130000)
    assert D(corregida["saldo"]) == D(100000)
    cuadra(corregida)

    # la quincena 1 sigue marcada a la 2, y solo a la 2
    origen = _leer(client, h, liq1["id"])
    assert origen["deuda_trasladada_a_id"] == liq2["id"]
    assert D(origen["le_queda_debiendo"]) == D(120000)

    # y la quincena 3 no la vuelve a encontrar
    _recepcion(client, h, prov, "2026-07-05", 100, precio=2000)
    liq3 = _de(_generar(client, h, Q3)["generadas"], "Henri C")
    assert D(liq3["saldo_anterior"]) == CERO, (
        f"la quincena 3 volvió a cobrar {liq3['saldo_anterior']} que la quincena 2 ya "
        "había cobrado"
    )
    cuadra(liq3)


# ===========================================================================
# 7. DESPUÉS DE CORREGIR HACIA ABAJO, EL PERÍODO QUEDA RESERVADO: NADIE PUEDE
#    GENERAR UN SEGUNDO DOCUMENTO QUE NO LE COBRE LA DEUDA
# ===========================================================================
def test_con_plata_entregada_de_mas_el_periodo_no_deja_nacer_otro_comprobante(
    client, base_datos
):
    """Las cifras: quincena de $500.000 pagada con $500.000; se le corrige el precio del
    02/06 y baja a $400.000 -> SE LE PAGÓ DE MÁS $100.000, sin cobrar todavía.

    EL ATAQUE: en ese estado queda OTRO día suelto del mismo período (13/06, $90.000).
    Si Generar dejara nacer un segundo comprobante de ese período, ese comprobante NO le
    cobraría los $100.000 —la deuda solo viaja a un período que empiece después de que
    el origen termine— y de la caja saldrían $90.000 completos a quien debe $100.000.

    Tiene que salir OMITIDO, con el motivo redactado. La salida es generarle la quincena
    siguiente, que sí se cobra la deuda; ahí el período se destraba.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq1 = _de(_generar(client, h, Q1)["generadas"], "Marleny")
    _aprobar(client, h, liq1["id"])
    liq1 = _pagar(client, h, liq1["id"])

    dia_02 = next(d for d in liq1["detalles"] if d["fecha"] == "2026-06-02")
    r = _corregir(
        client,
        h,
        liq1["id"],
        motivo="el precio del 02/06 estaba mal",
        precios=[{"detalle_id": dia_02["id"], "precio_litro": "1500"}],
    )
    assert r.status_code == 200, r.text
    assert D(r.json()["saldo"]) == D(-100000)

    # otro día suelto del MISMO período
    _recepcion(client, h, prov, "2026-06-13", 50, precio=1800)

    respuesta = _generar(client, h, Q1)
    generadas = [g for g in respuesta["generadas"] if g.get("proveedor_nombre") == "Marleny"]
    omitidas = [o for o in respuesta["omitidas"] if o.get("tercero_nombre") == "Marleny"]
    assert generadas == [], (
        "nació un segundo comprobante del período montado sobre uno que quedó debiendo "
        f"$100.000: esa deuda no se cobra ahí. {generadas}"
    )
    assert omitidas, f"se saltó a Marleny en silencio: {respuesta['omitidas']}"


# ===========================================================================
# 8. CORREGIR NO SUELTA NI UN DÍA, NI UN ANTICIPO, NI UN PAGO
# ===========================================================================
def test_corregir_no_suelta_las_marcas_que_impiden_el_doble_cobro(client, base_datos):
    """La marca `liquidacion_id` de cada recepción, la del anticipo y los pagos son LO
    ÚNICO que impide que esta quincena se cobre otra vez. Se cuentan a mano después de
    corregir:

        3 recepciones del período, TODAS marcadas a esta liquidación (las 2 de siempre
        más el día olvidado que acaba de entrar);
        1 anticipo de $200.000, marcado a esta liquidación;
        1 pago de $300.000, intacto.

    Si la corrección soltara una sola de esas marcas, la próxima corrida se llevaría ese
    día (o ese anticipo) a un comprobante nuevo y la quesera lo pagaría dos veces.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Aleida", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)
    anticipo = _anticipo(client, h, prov, "2026-06-05", 200000)

    liq = _de(_generar(client, h, Q1)["generadas"], "Aleida")
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)
    r = _corregir(client, h, liq["id"], motivo="entra el 12", incluir=[olvidado["id"]])
    assert r.status_code == 200, r.text
    corregida = r.json()

    dias = _recepciones_de(client, h, prov, Q1)
    assert len(dias) == 3
    for dia in dias:
        assert dia["liquidacion_id"] == liq["id"], (
            f"el día {dia['fecha']} quedó suelto después de corregir: la próxima "
            "corrida se lo lleva y se paga dos veces"
        )

    r = client.get(f"{ANT}/{anticipo['id']}", headers=h)
    assert r.json()["liquidacion_id"] == liq["id"], (
        "el anticipo quedó suelto después de corregir: la quincena siguiente se lo "
        "vuelve a descontar al productor"
    )

    assert len(corregida["pagos"]) == 1
    assert D(corregida["pagos"][0]["valor"]) == D(300000)
    assert D(corregida["pagado"]) == D(300000)
    cuadra(corregida)


# ===========================================================================
# 9. EL PAPEL CORREGIDO SUMA EXACTO DE ARRIBA ABAJO
# ===========================================================================
def test_el_comprobante_corregido_suma_exacto_renglon_por_renglon(client, base_datos):
    """El dueño suma la columna del resumen con calculadora, de arriba abajo:

        valor bruto + bonificaciones − descuentos             = VALOR TOTAL
        VALOR TOTAL − anticipos − lo que quedó debiendo − pagado = SALDO

    Con las cifras de esta prueba, después de corregir:
        02/06 100 L a $2.500                     $250.000
        03/06 100 L a $2.500                     $250.000
        12/06 100 L a $1.800 (el olvidado)       $180.000
        valor bruto                              $680.000
        bonificaciones                              +$0
        descuentos                                  -$0
        VALOR TOTAL                              $680.000
        Anticipos                               -$200.000
        Pagado                                  -$300.000
        QUEDA POR ENTREGARLE (SALDO A PAGAR)     $180.000
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)
    _anticipo(client, h, prov, "2026-06-05", 200000)

    liq = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)
    r = _corregir(client, h, liq["id"], motivo="entra el 12", incluir=[olvidado["id"]])
    assert r.status_code == 200, r.text

    # Cada renglón se lee CON EL SIGNO IMPRESO ("- $200.000" vale -200.000), así que la
    # columna se baja SUMANDO, que es exactamente lo que hace el dueño con la
    # calculadora: si un renglón saliera sin su menos, esta suma no daría.
    papel = _pdf(client, h, liq["id"])
    bruto = renglon(papel, "Valor bruto")
    bonif = renglon(papel, "Bonificaciones")
    desc = renglon(papel, "Descuentos")
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    pagado = renglon(papel, "Pagado")
    saldo = renglon(papel, "SALDO A PAGAR")

    assert bruto + bonif + desc == total == D(680000)
    assert anticipos == D(-200000)
    assert pagado == D(-300000)
    assert total + anticipos + pagado == saldo == D(180000)


# ===========================================================================
# 10. LA PREVISUALIZACIÓN Y EL BOTÓN TIENEN QUE DECIR LA MISMA CIFRA
# ===========================================================================
def test_la_previsualizacion_no_puede_prometer_una_cifra_que_el_boton_no_escribe(
    client, base_datos
):
    """Las cifras, calculadas a mano:

        quincena pagada                                  $500.000
        día olvidado del 12/06 (100 L a $1.800)          $180.000
        VALOR TOTAL correcto                             $680.000
        saldo correcto (680.000 - 500.000 ya pagados)    $180.000

    EL ATAQUE: mandar el mismo `recepcion_id` DOS VECES. La lista
    `recepciones_a_incluir` es una lista pelada y nadie le quita los repetidos.

    LA CORRECCIÓN DE VERDAD AGUANTA —vuelve a sumar desde las recepciones marcadas, y
    una fila marcada dos veces sigue siendo una fila—, pero la previsualización arma su
    suma con la lista tal como llegó. Y la previsualización es la mitad de la seguridad
    de esta operación: es la pantalla que el dueño compara con el papel que tiene al
    lado ANTES de confirmar.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Aleida", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Aleida")
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)

    previa = _previsualizar_correccion(
        client, h, liq["id"], incluir=[olvidado["id"], olvidado["id"]]
    )
    assert previa.status_code in (200, 422), previa.text
    if previa.status_code == 422:
        return  # rebotar el repetido también sería una respuesta correcta
    cifras = previa.json()
    assert D(cifras["valor_total_despues"]) == D(680000), (
        "la previsualización contó el mismo día dos veces: le promete al dueño "
        f"{cifras['valor_total_despues']} donde la leche vale $680.000"
    )
    assert D(cifras["saldo_despues"]) == D(180000)
    assert D(cifras["queda_por_entregar"]) == D(180000)


# ===========================================================================
# 11. LA CADENA COMPLETA DE TRES QUINCENAS: LA CAJA SUMA EXACTO LA LECHE
# ===========================================================================
def test_tres_quincenas_encadenadas_la_caja_entrega_lo_que_valio_la_leche(
    client, base_datos
):
    """EL CAMINO LARGO, con toda la plata contada a mano. Es el que más duele si se
    descuadra, porque cada quincena le pasa una cifra a la siguiente.

    ANTICIPO ENTREGADO EN LA MANO el 01/06:                       $300.000

    QUINCENA 1 (01 al 15) — 100 L a $1.800:
        VALOR TOTAL $180.000 - anticipos $300.000 = -$120.000
        -> HENRI LE QUEDA DEBIENDO $120.000, queda 'aprobada' (no sale un peso)

    QUINCENA 2 (16 al 30) — 100 L a $2.500 = $250.000:
        VALOR TOTAL $250.000 - deuda de la pasada $120.000 = $130.000, SE PAGA.

    SE CORRIGE LA QUINCENA 2 (el precio del 20/06 estaba en $2.500 y era $1.500):
        VALOR TOTAL                                     $150.000
        lo que quedó debiendo de la pasada              -$120.000
        NETO                                             $30.000
        pagado                                         -$130.000
        SE LE PAGÓ DE MÁS                               $100.000  -> viaja a la 3

    QUINCENA 3 (01 al 15 de julio) — 100 L a $2.000 = $200.000:
        VALOR TOTAL $200.000 - lo que quedó debiendo $100.000 = $100.000, SE PAGA.

    LA CUENTA QUE TIENE QUE CUADRAR:
        salió de la caja:  $300.000 + $130.000 + $100.000 = $530.000
        valió la leche:    $180.000 + $150.000 + $200.000 = $530.000
    Ni un peso de más ni de menos, y NINGUNA de las dos deudas cobrada dos veces.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C", precio="1800")
    _anticipo(client, h, prov, "2026-06-01", 300000)
    _recepcion(client, h, prov, "2026-06-02", 100, precio=1800)

    liq1 = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    assert D(liq1["valor_total"]) == D(180000)
    assert D(liq1["le_queda_debiendo"]) == D(120000)
    _aprobar(client, h, liq1["id"])
    cuadra(liq1)

    _recepcion(client, h, prov, "2026-06-20", 100, precio=2500)
    liq2 = _de(_generar(client, h, Q2)["generadas"], "Henri C")
    assert D(liq2["saldo_anterior"]) == D(120000)
    assert D(liq2["neto_a_pagar"]) == D(130000)
    _aprobar(client, h, liq2["id"])
    liq2 = _pagar(client, h, liq2["id"])
    assert D(liq2["pagado"]) == D(130000)
    cuadra(liq2)

    # LA CORRECCIÓN, sobre la quincena que se cobró una deuda ajena
    dia_20 = next(d for d in liq2["detalles"] if d["fecha"] == "2026-06-20")
    r = _corregir(
        client,
        h,
        liq2["id"],
        motivo="el precio del 20/06 estaba en $2.500 y era $1.500",
        precios=[{"detalle_id": dia_20["id"], "precio_litro": "1500"}],
    )
    assert r.status_code == 200, r.text
    liq2c = r.json()
    assert D(liq2c["valor_total"]) == D(150000)
    assert D(liq2c["saldo_anterior"]) == D(120000)
    assert D(liq2c["neto_a_pagar"]) == D(30000)
    assert D(liq2c["pagado"]) == D(130000)
    assert D(liq2c["saldo"]) == D(-100000)
    assert D(liq2c["le_queda_debiendo"]) == D(100000)
    cuadra(liq2c)

    # la quincena 1 SIGUE marcada a la 2: su deuda no volvió a quedar libre
    origen = _leer(client, h, liq1["id"])
    assert origen["deuda_trasladada_a_id"] == liq2["id"], (
        "corregir la quincena 2 le soltó la marca a la quincena 1: esos $120.000 se "
        "cobrarían otra vez en la quincena 3"
    )

    _recepcion(client, h, prov, "2026-07-05", 100, precio=2000)
    liq3 = _de(_generar(client, h, Q3)["generadas"], "Henri C")
    assert D(liq3["valor_total"]) == D(200000)
    assert D(liq3["saldo_anterior"]) == D(100000), (
        "la quincena 3 tenía que cobrar SOLO los $100.000 que se le pagaron de más en "
        f"la 2; cobró {liq3['saldo_anterior']}"
    )
    assert D(liq3["neto_a_pagar"]) == D(100000)
    _aprobar(client, h, liq3["id"])
    liq3 = _pagar(client, h, liq3["id"])
    cuadra(liq3)

    # LA CUENTA GRANDE, con calculadora
    salio_de_caja = D(300000) + D(130000) + D(100000)
    valio_la_leche = D(180000) + D(150000) + D(200000)
    assert salio_de_caja == valio_la_leche == D(530000)


# ===========================================================================
# 12. NO SE PUEDE CORREGIR LA QUINCENA CUYA DEUDA YA SE COBRÓ EN OTRA
# ===========================================================================
def test_la_quincena_cuya_deuda_ya_viajo_no_se_deja_corregir(client, base_datos):
    """Las cifras: la quincena 1 quedó debiendo $120.000 ($180.000 de leche contra
    $300.000 de anticipo) y la quincena 2 ya se los restó en un papel que puede estar
    pagado y en la mano del productor.

    EL ATAQUE: corregirle a la quincena 1 el precio del 02/06, para que su deuda deje de
    ser $120.000. Si pasara, la quincena 2 quedaría cobrando una deuda que ya no existe
    y los DOS comprobantes se descuadrarían de un solo golpe.

    Tiene que rebotar con 422 y nombrando la liquidación que se la cobró: lo que el
    dueño necesita saber es qué anular primero.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny", precio="1800")
    _anticipo(client, h, prov, "2026-06-01", 300000)
    _recepcion(client, h, prov, "2026-06-02", 100, precio=1800)

    liq1 = _de(_generar(client, h, Q1)["generadas"], "Marleny")
    _aprobar(client, h, liq1["id"])

    _recepcion(client, h, prov, "2026-06-20", 100, precio=2500)
    liq2 = _de(_generar(client, h, Q2)["generadas"], "Marleny")
    assert D(liq2["saldo_anterior"]) == D(120000)
    _aprobar(client, h, liq2["id"])
    _pagar(client, h, liq2["id"])

    detalles = _leer(client, h, liq1["id"])["detalles"]
    dia_02 = next(d for d in detalles if d["fecha"] == "2026-06-02")
    r = _corregir(
        client,
        h,
        liq1["id"],
        motivo="quiero cambiarle el precio a una deuda ya cobrada",
        precios=[{"detalle_id": dia_02["id"], "precio_litro": "2500"}],
    )
    assert r.status_code == 422, (
        "dejó corregir una quincena cuya deuda ya está restada en otro comprobante: "
        f"los dos papeles se descuadran. {r.status_code} {r.text}"
    )
    assert "16/06/2026 al 30/06/2026" in r.text, r.text

    # y la quincena 1 no quedó tocada
    sin_tocar = _leer(client, h, liq1["id"])
    assert D(sin_tocar["valor_total"]) == D(180000)
    assert D(sin_tocar["le_queda_debiendo"]) == D(120000)
    assert sin_tocar["version"] == 1
    cuadra(sin_tocar)


# ===========================================================================
# 13. LA QUINCENA QUE LOS ANTICIPOS CUBRIERON EXACTO: CORREGIRLA NO PUEDE
#     SOLTAR EL ANTICIPO QUE YA SE ENTREGÓ EN LA MANO
# ===========================================================================
def test_corregir_la_que_el_anticipo_cubrio_exacto_no_libera_ese_anticipo(
    client, base_datos
):
    """LA FAMILIA PELIGROSA: la quincena que cerró en 'pagada' CON `pagado = $0`, porque
    los anticipos la cubrieron exacto. Las cifras:

        02/06 y 03/06, 100 L a $2.500 cada uno      VALOR TOTAL   $500.000
        anticipo del 01/06 entregado en la mano                  -$500.000
        NETO A PAGAR                                                   $0  -> 'pagada'

    Se corrige agregando el día olvidado del 12/06 ($180.000):
        VALOR TOTAL                                  $680.000
        Anticipos                                   -$500.000
        QUEDA POR ENTREGARLE                         $180.000  -> 'parcial', pagado $0

    EL ATAQUE: en ese estado la quincena ya NO está en 'pagada' y `tiene_pagos` es
    falso, que son las dos preguntas con las que el candado de Anticipos decide si un
    anticipo se puede borrar. Si se dejara borrar, el neto saltaría de $180.000 a
    $680.000 y la quesera le entregaría OTRA VEZ los $500.000 que ya le puso en la mano
    al productor. Medio millón pagado dos veces.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Aleida", precio="1800")
    anticipo_id = _anticipo(client, h, prov, "2026-06-01", 500000)["id"]
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Aleida")
    assert D(liq["anticipos"]) == D(500000)
    assert D(liq["neto_a_pagar"]) == CERO
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])
    assert liq["estado"] == "pagada"
    assert D(liq["pagado"]) == CERO, "esta es la familia 'pagada con pagado = $0'"

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)
    r = _corregir(client, h, liq["id"], motivo="entra el día 12", incluir=[olvidado["id"]])
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert corregida["estado"] == "parcial"
    assert D(corregida["pagado"]) == CERO
    assert D(corregida["anticipos"]) == D(500000)
    assert D(corregida["saldo"]) == D(180000)
    cuadra(corregida)

    # ---- EL ATAQUE: borrar el anticipo que ya se entregó en la mano
    r = client.delete(f"{ANT}/{anticipo_id}", headers=h)
    assert r.status_code >= 400, (
        "dejó borrar el anticipo de $500.000 que ya se le entregó en la mano al "
        "productor, sobre una quincena ya corregida y con papel emitido: el neto pasa "
        "de $180.000 a $680.000 y esa plata sale dos veces"
    )

    # y el anticipo sigue vivo y aplicado, con la quincena intacta
    r = client.get(f"{ANT}/{anticipo_id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["liquidacion_id"] == liq["id"]
    despues = _leer(client, h, liq["id"])
    assert D(despues["anticipos"]) == D(500000)
    assert D(despues["saldo"]) == D(180000)
    cuadra(despues)


# ===========================================================================
# 14. LOS DÍAS DE UNA QUINCENA CORREGIDA NO SE TOCAN POR RECEPCIÓN DIARIA
# ===========================================================================
def test_el_dia_de_una_quincena_corregida_no_se_reprecia_por_la_puerta_de_atras(
    client, base_datos
):
    """Las cifras: quincena de $500.000 pagada con $500.000, corregida a $680.000 con el
    día olvidado del 12/06 (100 L a $1.800). El comprobante v2 ya salió impreso.

    EL ATAQUE: entrar por Recepción diaria y subirle el precio a ese día de $1.800 a
    $2.500 ($180.000 -> $250.000). Por esa puerta no queda ni motivo escrito ni versión
    nueva, así que el papel que el productor tiene en la mano diría $680.000 mientras el
    sistema dice $750.000 sobre el MISMO folio, y los $70.000 de diferencia no tendrían
    dueño. La quincena corregida se corrige por su botón, que deja constancia.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)
    r = _corregir(client, h, liq["id"], motivo="entra el 12", incluir=[olvidado["id"]])
    assert r.status_code == 200, r.text
    assert D(r.json()["valor_total"]) == D(680000)

    r = client.put(
        f"{REC}/{olvidado['id']}", json={"precio_litro": "2500"}, headers=h
    )
    assert r.status_code >= 400, (
        "dejó reprecificar por Recepción diaria un día que ya salió en un comprobante "
        f"corregido: el papel diría $680.000 y el sistema $750.000. {r.text}"
    )

    despues = _leer(client, h, liq["id"])
    assert D(despues["valor_total"]) == D(680000)
    assert despues["version"] == 2
    cuadra(despues)


# ===========================================================================
# 15. EL DÍA DE OTRO PRODUCTOR NO SE PUEDE METER EN ESTE COMPROBANTE
# ===========================================================================
def test_no_se_puede_meter_en_la_quincena_de_uno_la_leche_de_otro(client, base_datos):
    """EL ATAQUE QUE SE PAGA DOS VECES ENTERO: mandar en `recepciones_a_incluir` el id
    de una recepción de OTRO proveedor que está suelta en el mismo período.

    Las cifras: la quincena de Henri vale $500.000 y está pagada. Marleny entregó
    100 L a $1.800 = $180.000 el 12/06, sin liquidar. Si esos $180.000 entraran en el
    comprobante de Henri, la quesera se los pagaría A HENRI... y después se los volvería
    a pagar A MARLENY cuando le corra su quincena, porque su día seguiría contando en la
    suya. $180.000 pagados dos veces por la misma leche, y encima a la persona
    equivocada.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C", precio="1800")
    marleny = _proveedor(client, h, "Marleny", precio="1800")
    _recepcion(client, h, henri, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, henri, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    de_marleny = _recepcion(client, h, marleny, "2026-06-12", 100, precio=1800)

    r = _corregir(
        client,
        h,
        liq["id"],
        motivo="me quiero llevar la leche de Marleny",
        incluir=[de_marleny["id"]],
    )
    assert r.status_code == 422, (
        "dejó meter la leche de OTRO productor en este comprobante: esos $180.000 se "
        f"pagan dos veces, y a quien no era. {r.status_code} {r.text}"
    )

    # el comprobante de Henri no se movió, y el día de Marleny sigue suelto para ella
    sin_tocar = _leer(client, h, liq["id"])
    assert D(sin_tocar["valor_total"]) == D(500000)
    assert sin_tocar["version"] == 1
    cuadra(sin_tocar)

    suya = _de(_generar(client, h, Q1)["generadas"], "Marleny")
    assert D(suya["valor_total"]) == D(180000)
    cuadra(suya)


# ===========================================================================
# 16. EL PRECIO DE UN DÍA DE OTRO COMPROBANTE NO SE CORRIGE DESDE ESTE
# ===========================================================================
def test_no_se_le_puede_cambiar_el_precio_a_un_dia_de_otra_liquidacion(client, base_datos):
    """EL ATAQUE: mandar en `precios` el `detalle_id` de un día que pertenece a OTRA
    liquidación (la de Marleny), corrigiendo la de Henri.

    Las cifras: el día de Marleny del 20/06 vale 100 L a $2.500 = $250.000 en un
    comprobante suyo. Si desde la corrección de Henri se le pudiera bajar a $1.500, el
    comprobante de Marleny —que ella tiene en la mano— pasaría a $150.000 sin que quede
    ni motivo escrito ni versión nueva EN EL SUYO: $100.000 que se le quitan a una
    persona desde el papel de otra.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C", precio="1800")
    marleny = _proveedor(client, h, "Marleny", precio="1800")
    _recepcion(client, h, henri, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, henri, "2026-06-03", 100, precio=2500)
    _recepcion(client, h, marleny, "2026-06-20", 100, precio=2500)

    generadas = _generar(client, h, Q1)["generadas"]
    liq_henri = _de(generadas, "Henri C")
    _aprobar(client, h, liq_henri["id"])
    _pagar(client, h, liq_henri["id"])

    liq_marleny = _de(_generar(client, h, Q2)["generadas"], "Marleny")
    dia_ajeno = liq_marleny["detalles"][0]
    assert D(dia_ajeno["valor"]) == D(250000)

    r = _corregir(
        client,
        h,
        liq_henri["id"],
        motivo="le bajo el precio a un día que no es de este comprobante",
        precios=[{"detalle_id": dia_ajeno["id"], "precio_litro": "1500"}],
    )
    assert r.status_code in (404, 422), (
        "dejó tocar el precio de un día que pertenece a OTRO comprobante: "
        f"{r.status_code} {r.text}"
    )

    # el comprobante de Marleny sigue valiendo lo mismo
    suyo = _leer(client, h, liq_marleny["id"])
    assert D(suyo["valor_total"]) == D(250000)
    assert suyo["version"] == 1
    cuadra(suyo)


# ===========================================================================
# 17. LA QUESERA DE AL LADO NO PUEDE CORREGIR ESTE COMPROBANTE
# ===========================================================================
def test_la_otra_quesera_no_puede_corregir_ni_ver_esta_quincena(client, base_datos):
    """Dos queseras distintas liquidan el mismo período. EL ATAQUE: el administrador de
    la Quesera B le corrige a la Quesera A una quincena de $500.000 ya pagada.

    No es solo un asunto de permisos: la corrección MUEVE la plata (sube o baja el
    total, cambia el estado y emite una versión nueva del papel) y lo haría sobre el
    comprobante de un productor que la Quesera B ni siquiera debería poder ver.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    prov = _proveedor(client, h_a, "Henri C", precio="1800")
    _recepcion(client, h_a, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h_a, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h_a, Q1)["generadas"], "Henri C")
    _aprobar(client, h_a, liq["id"])
    _pagar(client, h_a, liq["id"])

    olvidado = _recepcion(client, h_a, prov, "2026-06-12", 100, precio=1800)

    r = _previsualizar_correccion(client, h_b, liq["id"], incluir=[olvidado["id"]])
    assert r.status_code in (403, 404), r.text
    r = _corregir(
        client, h_b, liq["id"], motivo="corrigiendo lo ajeno", incluir=[olvidado["id"]]
    )
    assert r.status_code in (403, 404), (
        f"la Quesera B movió la plata de un comprobante de la Quesera A: {r.text}"
    )

    sin_tocar = _leer(client, h_a, liq["id"])
    assert D(sin_tocar["valor_total"]) == D(500000)
    assert sin_tocar["version"] == 1
    cuadra(sin_tocar)


# ===========================================================================
# 18. DESPUÉS DE CORREGIR NO SE PUEDE ABONAR MÁS DE LO QUE FALTA
# ===========================================================================
def test_despues_de_corregir_no_se_puede_pagar_mas_de_lo_que_falta(client, base_datos):
    """Las cifras: quincena de $500.000 pagada con $500.000; entra el día olvidado de
    $180.000 y queda un saldo de $180.000.

    EL ATAQUE: registrar un abono de $680.000 sobre la corregida —la cifra grande del
    papel nuevo, que es justo la que alguien podría teclear mirando el VALOR TOTAL—.
    Si pasara, la caja habría entregado $500.000 + $680.000 = $1.180.000 por $680.000 de
    leche: medio millón de más.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)
    r = _corregir(client, h, liq["id"], motivo="entra el 12", incluir=[olvidado["id"]])
    assert r.status_code == 200, r.text
    assert D(r.json()["saldo"]) == D(180000)

    r = client.post(
        f"{API}/{liq['id']}/pagos",
        json={"fecha": "2026-06-16", "valor": "680000"},
        headers=h,
    )
    assert r.status_code == 422, (
        "dejó abonar $680.000 sobre un saldo de $180.000: de la caja salen $1.180.000 "
        f"por $680.000 de leche. {r.text}"
    )

    # el abono bueno cierra la cuenta exacta
    r = client.post(
        f"{API}/{liq['id']}/pagos",
        json={"fecha": "2026-06-16", "valor": "180000"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    final = r.json()
    assert final["estado"] == "pagada"
    assert D(final["pagado"]) == D(680000)
    assert D(final["saldo"]) == CERO
    cuadra(final)
    assert sum((D(p["valor"]) for p in final["pagos"]), CERO) == D(680000)


# ===========================================================================
# 19. UNA CORRECCIÓN QUE NO CAMBIA NADA NO PUEDE SUBIR LA VERSIÓN
# ===========================================================================
def test_una_correccion_vacia_no_emite_un_papel_nuevo_que_dice_lo_mismo(client, base_datos):
    """EL ATAQUE MUDO: oprimir Corregir sin escoger ningún día ni ningún precio.

    Si pasara, la quincena subiría a v2 sin que cambiara un peso: el productor tendría
    que devolver un papel para recibir OTRO idéntico, y en el libro quedaría una
    corrección de $500.000 a $500.000 que no explica nada. Peor todavía: `version > 1`
    es lo que traba los días de esa quincena en Recepción diaria, así que una corrección
    vacía traba el período sin que haya pasado nada.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    r = _corregir(client, h, liq["id"], motivo="no escogí nada")
    assert r.status_code == 422, r.text

    sin_tocar = _leer(client, h, liq["id"])
    assert sin_tocar["version"] == 1
    assert D(sin_tocar["valor_total"]) == D(500000)
    cuadra(sin_tocar)

    r = client.get(f"{API}/{liq['id']}/correcciones", headers=h)
    assert r.json() == []


# ===========================================================================
# 20. EL FLETE DEL DÍA QUE ENTRA POR LA CORRECCIÓN NO SE LE COBRA DOS VECES
#     AL TRANSPORTADOR — QUE ES EL PAPEL DE OTRA PERSONA
# ===========================================================================
def test_el_viaje_ya_cobrado_no_se_le_vuelve_a_cobrar_por_el_dia_que_entro_corrigiendo(
    client, base_datos
):
    """EL CAMINO COMPLETO, con las dos cuentas —la de la leche y la del flete—, porque
    la corrección de una toca los días de la otra.

    EL MONTAJE: Alex cobra la ruta "A fábrica" POR DÍA COMPLETO, $150.000 el viaje: el
    día vale $150.000 recoja a uno o recoja a seis.

        02/06  Henri 100 L y Marleny 100 L  -> UN solo viaje, el del 02/06
        03/06  Henri 100 L                   -> viaje del 03/06
        12/06  Marleny 100 L                 -> viaje del 12/06
        COMPROBANTE DEL FLETE de Alex (01 al 15): 3 viajes × $150.000 = $450.000,
        aprobado y PAGADO. Esos $450.000 ya salieron de la caja.

    EL DÍA OLVIDADO: la leche de HENRI del 12/06 (100 L a $1.800 = $180.000) se anota
    tarde, con Alex y la misma ruta. Entra en la quincena de leche de Henri por el botón
    Corregir.

    EL ATAQUE: volver a correr el comprobante del flete de ese mismo período. Si el
    viaje del 12/06 saliera OTRA VEZ como "Día completo $150.000", a Alex se le pagarían
    $450.000 por 2 viajes que valen $300.000: $150.000 de más por recoger un proveedor
    más en un día que ya estaba cobrado y pagado. Y esa plata es de OTRA PERSONA: ni
    sale en la pantalla de la leche ni el dueño la va a estar mirando.

    LA CUENTA QUE TIENE QUE CUADRAR AL FINAL:
        leche de Henri (02, 03 y el 12 corregido)                 $680.000
        flete de Alex por los TRES viajes, ni un peso más          $450.000
    """
    h = auth_headers(client, "admin.a")

    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "A fabrica", "municipio": "Granada"}, headers=h
    ).json()
    alex = client.post(
        "/api/v1/transportadores",
        json={
            "nombre": "Alex",
            "valor_transporte": "100",
            "modo_transporte": "litro",
            "rutas": [
                {
                    "ruta_id": ruta["id"],
                    "valor_transporte": "150000",
                    "modo_transporte": "dia_fijo",
                }
            ],
        },
        headers=h,
    )
    assert alex.status_code == 201, alex.text
    alex = alex.json()

    def _dia(prov, fecha, litros, precio):
        r = client.post(
            REC,
            json={
                "fecha": fecha,
                "proveedor_id": prov["id"],
                "cantidad_litros": str(litros),
                "precio_litro": str(precio),
                "transportador_id": alex["id"],
                "ruta_id": ruta["id"],
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        return r.json()

    henri = _proveedor(client, h, "Henri C", precio="1800")
    marleny = _proveedor(client, h, "Marleny", precio="1800")

    _dia(henri, "2026-06-02", 100, 2500)
    _dia(henri, "2026-06-03", 100, 2500)
    _dia(marleny, "2026-06-02", 100, 1800)
    _dia(marleny, "2026-06-12", 100, 1800)

    # ---- EL FLETE: dos viajes de $150.000, aprobado y PAGADO
    flete = _de(_generar(client, h, Q1, tipo="transportador")["generadas"], "Alex")
    assert D(flete["valor_transporte"]) == D(450000), (
        f"tres viajes por día completo son $450.000; dice {flete['valor_transporte']}"
    )
    _aprobar(client, h, flete["id"])
    flete = _pagar(client, h, flete["id"])
    assert D(flete["pagado"]) == D(450000)

    # ---- LA LECHE DE HENRI: $500.000, aprobada y pagada
    liq = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    assert D(liq["valor_total"]) == D(500000)
    _aprobar(client, h, liq["id"])
    _pagar(client, h, liq["id"])

    # ---- EL DÍA OLVIDADO de Henri, el MISMO 12/06 que Alex ya cobró
    olvidado = _dia(henri, "2026-06-12", 100, 1800)

    previa = _previsualizar_correccion(client, h, liq["id"], incluir=[olvidado["id"]])
    assert previa.status_code == 200, previa.text
    suelto = next(
        d for d in previa.json()["dias_sueltos"] if d["recepcion_id"] == olvidado["id"]
    )
    assert suelto["nota_flete"] and "ya se le cobró" in suelto["nota_flete"], (
        "el diálogo no le avisa al dueño que el flete de ese día vale $0,00 porque el "
        f"viaje ya está cobrado: {suelto['nota_flete']}"
    )

    r = _corregir(client, h, liq["id"], motivo="faltó el 12", incluir=[olvidado["id"]])
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert D(corregida["valor_total"]) == D(680000)
    cuadra(corregida)

    # ---- EL ATAQUE: volver a correr el flete del mismo período
    respuesta = _generar(client, h, Q1, tipo="transportador")
    nuevos = [g for g in respuesta["generadas"] if g.get("transportador_nombre") == "Alex"]
    cobrado_de_mas = sum((D(g["valor_transporte"]) for g in nuevos), CERO)
    assert cobrado_de_mas == CERO, (
        f"se le volvió a cobrar {cobrado_de_mas} a Alex por un viaje que ya estaba "
        "cobrado y pagado: el día 12/06 vale $150.000 recoja a uno o recoja a seis"
    )

    # el comprobante pagado de Alex no se movió ni un peso
    flete_ahora = _leer(client, h, flete["id"])
    assert D(flete_ahora["valor_transporte"]) == D(450000)
    assert D(flete_ahora["pagado"]) == D(450000)
    assert D(flete_ahora["saldo"]) == CERO
    cuadra(flete_ahora)


# ===========================================================================
# 21. BAJAR Y DESPUÉS SUBIR: LA DEUDA QUE ALCANZÓ A EXISTIR NO PUEDE QUEDAR
#     COBRÁNDOSE EN LA QUINCENA SIGUIENTE
# ===========================================================================
def test_corregir_hacia_abajo_y_despues_hacia_arriba_no_deja_una_deuda_fantasma(
    client, base_datos
):
    """Las cifras, calculadas a mano:

        quincena pagada                                        $500.000 (pagado $500.000)
        v2: el precio del 02/06 baja de $2.500 a $1.500        $400.000
            -> SE LE PAGÓ DE MÁS $100.000 (todavía sin cobrar)
        v3: entra el día olvidado del 12/06 (100 L a $1.800)   $580.000
            -> pagado $500.000, QUEDA POR ENTREGARLE $80.000

    EL ATAQUE: entre la v2 y la v3 la quincena ESTUVO debiendo $100.000. Si esa deuda
    dejara cualquier rastro —una marca, un `saldo_anterior` sembrado en otra hoja—, la
    quincena siguiente se los descontaría al productor Y ADEMÁS la quesera le seguiría
    debiendo los $80.000 de la v3: $100.000 cobrados por una deuda que ya no existe.

    La quincena 2 tiene que salir limpia: `saldo_anterior` en $0.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Henri C")
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])

    dia_02 = next(d for d in liq["detalles"] if d["fecha"] == "2026-06-02")
    r = _corregir(
        client,
        h,
        liq["id"],
        motivo="el precio del 02/06 estaba mal",
        precios=[{"detalle_id": dia_02["id"], "precio_litro": "1500"}],
    )
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert D(v2["saldo"]) == D(-100000)
    assert D(v2["le_queda_debiendo"]) == D(100000)
    cuadra(v2)

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)
    r = _corregir(client, h, liq["id"], motivo="y además faltaba el 12", incluir=[olvidado["id"]])
    assert r.status_code == 200, r.text
    v3 = r.json()
    assert v3["version"] == 3
    assert D(v3["valor_total"]) == D(580000)
    assert D(v3["pagado"]) == D(500000)
    assert D(v3["saldo"]) == D(80000)
    assert D(v3["le_queda_debiendo"]) == CERO, (
        "la quincena sigue diciendo que el productor debe, cuando lo que pasa es que se "
        "le deben $80.000"
    )
    assert v3["estado"] == "parcial"
    assert v3["deuda_trasladada_a_id"] is None
    cuadra(v3)

    # ---- la quincena siguiente sale limpia: no hay deuda que cobrar
    _recepcion(client, h, prov, "2026-06-20", 100, precio=2000)
    liq2 = _de(_generar(client, h, Q2)["generadas"], "Henri C")
    assert D(liq2["saldo_anterior"]) == CERO, (
        f"la quincena 2 le está cobrando {liq2['saldo_anterior']} de una deuda que la "
        "corrección siguiente ya había tapado"
    )
    assert D(liq2["neto_a_pagar"]) == D(200000)
    cuadra(liq2)

    # y la primera se cierra pagando lo que falta: $580.000 exactos de caja
    pagada = _pagar(client, h, liq["id"])
    assert D(pagada["pagado"]) == D(580000)
    assert D(pagada["saldo"]) == CERO
    cuadra(pagada)
    assert sum((D(p["valor"]) for p in pagada["pagos"]), CERO) == D(580000)


# ===========================================================================
# 22. CORREGIR UNA QUINCENA A MEDIO PAGAR: LOS ABONOS SIGUEN CUADRANDO
# ===========================================================================
def test_corregir_una_quincena_con_abono_a_medias_no_descuadra_lo_ya_entregado(
    client, base_datos
):
    """Las cifras, a mano:

        quincena aprobada                                      $500.000
        abono entregado en la mano                             $200.000  -> 'parcial'
        entra el día olvidado del 12/06 (100 L a $1.800)       $680.000
        pagado (NO SE MUEVE)                                   $200.000
        QUEDA POR ENTREGARLE                                   $480.000
        se abona el resto                                      $480.000
        TOTAL ENTREGADO                                        $680.000

    EL ATAQUE: que el abono de $200.000 se pierda de vista, o que el saldo se recalcule
    contra el total viejo. Cualquiera de las dos rompe la igualdad que el dueño verifica
    a mano: neto_a_pagar = pagado + saldo.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny", precio="1800")
    _recepcion(client, h, prov, "2026-06-02", 100, precio=2500)
    _recepcion(client, h, prov, "2026-06-03", 100, precio=2500)

    liq = _de(_generar(client, h, Q1)["generadas"], "Marleny")
    _aprobar(client, h, liq["id"])
    r = client.post(
        f"{API}/{liq['id']}/pagos",
        json={"fecha": "2026-06-16", "valor": "200000"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "parcial"

    olvidado = _recepcion(client, h, prov, "2026-06-12", 100, precio=1800)
    r = _corregir(client, h, liq["id"], motivo="entra el 12", incluir=[olvidado["id"]])
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert D(corregida["valor_total"]) == D(680000)
    assert D(corregida["pagado"]) == D(200000), "el abono ya entregado no se puede mover"
    assert D(corregida["saldo"]) == D(480000)
    assert corregida["estado"] == "parcial"
    assert len(corregida["pagos"]) == 1
    cuadra(corregida)

    final = _pagar(client, h, liq["id"])
    assert D(final["pagado"]) == D(680000)
    assert D(final["saldo"]) == CERO
    assert final["estado"] == "pagada"
    cuadra(final)
    assert sum((D(p["valor"]) for p in final["pagos"]), CERO) == D(680000), (
        "los abonos tienen que sumar exactamente lo que valió la leche"
    )
