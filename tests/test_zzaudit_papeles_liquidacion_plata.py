"""AUDITORIA DEL COMPROBANTE DE LIQUIDACION: LAS TABLAS DE PLATA.

El archivo hermano (test_zzaudit_papeles_liquidacion.py) mide el detalle diario.
Este mide las TRES tablas que explican por que el saldo no es el valor total:

  · "Anticipos aplicados"      -> tiene que sumar el renglon "Anticipos aplicados"
  · "Pagos y giros realizados" -> tiene que sumar el renglon "Pagado"
  · el renglon de la deuda arrastrada y sus dos notas al pie

y el caso en que el resumen NO se puede sumar de arriba abajo: cuando los
anticipos se comen la quincena y el ultimo renglon cambia de rotulo a
"LE QUEDA DEBIENDO" con la cifra en POSITIVO.

Cifras feas a proposito.
"""
import pytest

import io
import re
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
ANTICIPOS = "/api/v1/anticipos"
LIQ = "/api/v1/liquidaciones"

Q1 = ("2026-07-01", "2026-07-15")
Q2 = ("2026-07-16", "2026-07-31")


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def texto_pdf(c: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(c)).pages)


def plano(t: str) -> str:
    return re.sub(r"\s+", " ", t)


def a_numero(txt: str) -> Decimal:
    return D(txt.replace("$", "").replace(" ", "").replace(".", "").replace(",", "."))


def renglon(t: str, rotulo: str) -> Decimal:
    m = re.search(re.escape(rotulo) + r"\s*([-+]\s*)?(\$[\d.,]+)", plano(t))
    assert m, f"no encontre <<{rotulo}>>:\n{plano(t)[:3000]}"
    v = a_numero(m.group(2))
    return -v if (m.group(1) or "").strip() == "-" else v


def crear(client, h, url, payload):
    r = client.post(url, json=payload, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def escenario(client, h, precio="1833.33"):
    prov = crear(client, h, PROVEEDORES, {
        "nombre": "Henri Camelo", "vereda": "La Vega", "precio_litro": precio})
    trans = crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "242.76"})
    return prov, trans


def recibir(client, h, prov, trans, fecha, litros):
    return crear(client, h, RECEPCIONES, {
        "fecha": fecha, "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": litros})


def liquidar(client, h, periodo, tipo="proveedor"):
    r = client.post(f"{LIQ}/generar",
                    json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1],
                          "tipo": tipo}, headers=h)
    assert r.status_code in (200, 201), r.text
    assert r.json()["generadas"], r.json()
    return r.json()["generadas"][0]


def leer(client, h, i):
    r = client.get(f"{LIQ}/{i}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def pdf(client, h, i) -> str:
    r = client.get(f"{LIQ}/{i}/pdf", headers=h)
    assert r.status_code == 200, r.text
    return texto_pdf(r.content)


def tabla(t: str, titulo: str, hasta: str) -> list[Decimal]:
    """Las cifras de la SEGUNDA columna (Valor) de una tabla con filas fechadas."""
    z = plano(t).split(titulo, 1)
    assert len(z) == 2, f"no se imprimio la tabla <<{titulo}>>:\n{plano(t)[:3000]}"
    cuerpo = z[1].split(hasta)[0]
    return [a_numero(x) for x in
            re.findall(r"\d{2}/\d{2}/\d{4}\s*(\$[\d.,]+)", cuerpo)]


# ===========================================================================
def test_zzaudit_liq_los_anticipos_impresos_suman_el_renglon(client, base_datos):
    """La tabla <<Anticipos aplicados>> tiene que sumar EXACTO el renglon del
    resumen: es la resta que el productor no puede comprobar de otra forma."""
    h = auth_headers(client, "admin.a")
    prov, trans = escenario(client, h)
    for f, v in (("2026-07-18", "242760.45"), ("2026-07-22", "183333.33"),
                 ("2026-07-25", "44230.07")):
        crear(client, h, ANTICIPOS, {"tipo": "proveedor", "proveedor_id": prov["id"],
                                     "fecha": f, "valor": v,
                                     "observaciones": "adelanto"})
    for f, l in (("2026-07-16", "137.45"), ("2026-07-17", "103.75"),
                 ("2026-07-18", "82.48")):
        recibir(client, h, prov, trans, f, l)
    liq = leer(client, h, liquidar(client, h, Q2)["id"])
    t = pdf(client, h, liq["id"])
    filas = tabla(t, "Anticipos aplicados", "Entregu")
    # el primer "Anticipos aplicados" que aparece es el del resumen; la tabla va
    # despues, asi que se toma la SEGUNDA aparicion
    z = plano(t).split("Anticipos aplicados")
    cuerpo = z[-1].split("Entregu")[0]
    filas = [a_numero(x) for x in
             re.findall(r"\d{2}/\d{2}/\d{4}\s*(\$[\d.,]+)", cuerpo)]
    renglon_resumen = renglon(t, "Anticipos aplicados")
    print(f"\n  filas impresas = {filas}   suma = {sum(filas, D(0))}")
    print(f"  renglon del resumen = {renglon_resumen}   pantalla = {liq['anticipos']}")
    assert filas, f"no se imprimio ninguna fila de anticipos:\n{cuerpo[:900]}"
    assert sum(filas, D(0)) == abs(renglon_resumen), (
        f"las filas suman {sum(filas, D(0))} y el renglon dice {abs(renglon_resumen)}")
    assert abs(renglon_resumen) == centavos(liq["anticipos"]), "papel != pantalla"


def test_zzaudit_liq_los_pagos_impresos_suman_el_renglon_pagado(client, base_datos):
    """La tabla <<Pagos y giros realizados>> suma el renglon <<Pagado>>."""
    h = auth_headers(client, "admin.a")
    prov, trans = escenario(client, h)
    for f, l in (("2026-07-16", "137.45"), ("2026-07-17", "103.75")):
        recibir(client, h, prov, trans, f, l)
    liq = liquidar(client, h, Q2)
    r = client.post(f"{LIQ}/{liq['id']}/aprobar", headers=h)
    assert r.status_code in (200, 201), r.text
    for f, v, dest in (("2026-08-01", "100000.45", "Henri Camelo"),
                       ("2026-08-03", "242760.76", "Doña Rosa (esposa)")):
        r = client.post(f"{LIQ}/{liq['id']}/pagos",
                        json={"fecha": f, "valor": v, "destinatario": dest,
                              "observaciones": "abono"}, headers=h)
        assert r.status_code in (200, 201), r.text
    liq = leer(client, h, liq["id"])
    t = pdf(client, h, liq["id"])
    z = plano(t).split("Pagos y giros realizados")
    assert len(z) == 2, f"no se imprimio la tabla de pagos:\n{plano(t)[:3000]}"
    cuerpo = z[1].split("Entregu")[0]
    filas = [a_numero(x) for x in
             re.findall(r"\d{2}/\d{2}/\d{4}\s*(\$[\d.,]+)", cuerpo)]
    pagado = renglon(t, "Pagado")
    total = renglon(t, "VALOR TOTAL")
    anticipos = renglon(t, "Anticipos aplicados")
    saldo = renglon(t, "SALDO A PAGAR")
    print(f"\n  filas de pagos = {filas}  suma = {sum(filas, D(0))}")
    print(f"  renglon Pagado = {pagado}   pantalla pagado = {liq['pagado']}")
    print(f"  {total} + ({anticipos}) + ({pagado}) = {total + anticipos + pagado}"
          f"   vs SALDO A PAGAR {saldo}")
    assert sum(filas, D(0)) == abs(pagado), (
        f"las filas suman {sum(filas, D(0))} y el renglon Pagado dice {abs(pagado)}")
    assert total + anticipos + pagado == saldo, (
        "el resumen no suma de arriba abajo")


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_liq_la_deuda_arrastrada_y_sus_dos_notas(client, base_datos):
    """La quincena que quedo debiendo y la que se lo cobra: los dos papeles se
    tienen que poder emparejar, y el resumen de la segunda tiene que sumar."""
    h = auth_headers(client, "admin.a")
    prov, trans = escenario(client, h)
    # Q1: un anticipo grandote se come la quincena -> queda debiendo
    crear(client, h, ANTICIPOS, {"tipo": "proveedor", "proveedor_id": prov["id"],
                                 "fecha": "2026-07-02", "valor": "500000.45"})
    recibir(client, h, prov, trans, "2026-07-03", "137.45")
    q1 = leer(client, h, liquidar(client, h, Q1)["id"])
    print(f"\n  Q1: total={q1['valor_total']} anticipos={q1['anticipos']} "
          f"le_queda_debiendo={q1['le_queda_debiendo']}")
    assert D(q1["le_queda_debiendo"]) > 0, q1
    t1 = pdf(client, h, q1["id"])
    p1 = plano(t1)
    print(f"  Q1 papel: {p1[p1.find('Resumen'):p1.find('Resumen') + 500]}")

    # el ultimo renglon cambia de rotulo y va en POSITIVO
    debe = renglon(t1, "LE QUEDA DEBIENDO")
    total1 = renglon(t1, "VALOR TOTAL")
    ant1 = renglon(t1, "Anticipos aplicados")
    print(f"  Q1: {total1} + ({ant1}) = {total1 + ant1}   destacado dice {debe}")
    assert debe == centavos(q1["le_queda_debiendo"])
    # ESTO es lo que se mide: sumando la columna de arriba abajo da NEGATIVO y la
    # cifra destacada esta en POSITIVO. Los dos estados de cuenta de reventa
    # imprimen, en ese mismo caso, una linea que escribe la operacion con su
    # signo ("La cuenta da X - Y = -Z, es decir que..."). El comprobante no.
    assert total1 + ant1 == -debe, "la aritmetica del papel cambio"
    explica = re.search(r"La cuenta da|es decir que|queda a favor|le qued[oó] debiendo al",
                        p1)
    assert explica, (
        f"el papel imprime «VALOR TOTAL {total1}», «Anticipos aplicados {ant1}» y "
        f"«LE QUEDA DEBIENDO {debe}»: sumando de arriba abajo da {total1 + ant1}, "
        f"o sea el NEGATIVO de la cifra destacada, y no hay ninguna linea que "
        f"escriba la operacion con su signo (los dos estados de cuenta de reventa "
        f"si la imprimen en el mismo caso).")


def test_zzaudit_liq_q2_se_cobra_la_deuda_y_el_resumen_suma(client, base_datos):
    """La quincena siguiente cobra la deuda: el renglon nuevo tiene que entrar en
    la resta y las dos notas al pie tienen que nombrar el otro comprobante."""
    h = auth_headers(client, "admin.a")
    prov, trans = escenario(client, h)
    crear(client, h, ANTICIPOS, {"tipo": "proveedor", "proveedor_id": prov["id"],
                                 "fecha": "2026-07-02", "valor": "500000.45"})
    recibir(client, h, prov, trans, "2026-07-03", "137.45")
    q1 = leer(client, h, liquidar(client, h, Q1)["id"])
    r = client.post(f"{LIQ}/{q1['id']}/aprobar", headers=h)
    assert r.status_code in (200, 201), r.text

    for f, l in (("2026-07-16", "137.45"), ("2026-07-18", "103.75"),
                 ("2026-07-20", "82.48")):
        recibir(client, h, prov, trans, f, l)
    q2 = leer(client, h, liquidar(client, h, Q2)["id"])
    print(f"\n  Q2: total={q2['valor_total']} saldo_anterior={q2['saldo_anterior']} "
          f"saldo={q2['saldo']}")
    assert D(q2["saldo_anterior"]) > 0, "la deuda de Q1 no se cobro en Q2"
    t = pdf(client, h, q2["id"])
    total = renglon(t, "VALOR TOTAL")
    ant = renglon(t, "Anticipos aplicados")
    deuda = renglon(t, "Lo que quedó debiendo de la quincena pasada")
    saldo = renglon(t, "SALDO A PAGAR")
    print(f"  {total} + ({ant}) + ({deuda}) = {total + ant + deuda}  vs SALDO {saldo}")
    assert total + ant + deuda == saldo, "el resumen de Q2 no suma de arriba abajo"
    assert abs(deuda) == centavos(q2["saldo_anterior"]), "papel != pantalla"

    folio1 = str(q1["id"])[:8].upper()
    p = plano(t)
    assert folio1 in p, (
        f"la nota al pie de Q2 no nombra el comprobante N.º {folio1} de donde "
        f"viene la deuda:\n{p[:2500]}")
    # y la punta de alla: Q1 tiene que decir que ya se cobro
    p1 = plano(pdf(client, h, q1["id"]))
    folio2 = str(q2["id"])[:8].upper()
    assert folio2 in p1, (
        f"el comprobante de Q1 no dice que su deuda ya se cobro en el N.º {folio2}")


def test_zzaudit_liq_total_litros_es_la_suma_de_la_columna(client, base_datos):
    """El encabezado <<Total litros>> tiene que ser la suma de la columna Litros
    del detalle: es la primera cifra que el productor comprueba."""
    h = auth_headers(client, "admin.a")
    prov, trans = escenario(client, h)
    litros = ["137.45", "44.23", "103.75", "82.48", "227.5"]
    for i, l in enumerate(litros):
        recibir(client, h, prov, trans, f"2026-07-{16 + i}", l)
    liq = leer(client, h, liquidar(client, h, Q2)["id"])
    t = pdf(client, h, liq["id"])
    columna = [D(x.replace(".", "").replace(",", "."))
               for x in re.findall(r"\d{2}/\d{2}/\d{4}\s+([\d.,]+) L", plano(t))]
    m = re.search(r"Total litros\s+([\d.,]+) L", plano(t))
    assert m, plano(t)[:1500]
    total = D(m.group(1).replace(".", "").replace(",", "."))
    print(f"\n  columna impresa = {columna}   suma = {sum(columna, D(0))}")
    print(f"  Total litros impreso = {total}   pantalla = {liq['total_litros']}")
    assert sum(columna, D(0)) == total, (
        f"la columna suma {sum(columna, D(0))} y el encabezado dice {total}")
