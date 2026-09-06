"""AUDITORIA DE LOS COMPROBANTES DE LIQUIDACION EN PDF.

Son los papeles que se imprimen y se le entregan EN LA MANO al productor y al
transportador. Se miden los tres:

  · el comprobante del PROVEEDOR   (GET  /liquidaciones/{id}/pdf)
  · el comprobante del TRANSPORTADOR (el mismo, con la columna Ruta)
  · el AVANCE / pre-liquidacion    (POST /liquidaciones/previsualizar/pdf)

LO QUE SE LE EXIGE A CADA PAPEL, sumando de arriba abajo como el dueno:
  1. cada renglon del "Detalle diario" se reproduce multiplicando
     (litros x precio/L = valor), o dice por que no ("Dia completo");
  2. los renglones del detalle SUMAN EXACTO la cifra del resumen
     (Valor bruto / Valor transporte);
  3. los renglones del resumen, con su signo escrito, caen EXACTO en las dos
     cifras destacadas (VALOR TOTAL y SALDO A PAGAR);
  4. el papel dice LO MISMO que la pantalla (el JSON de la liquidacion);
  5. el AVANCE y el comprobante OFICIAL del mismo tercero se leen igual.

Cifras feas a proposito: $242,76 · $1.833,33 el litro · 137,45 L · 44,23 L.
"""
import pytest

import io
import re
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQ = "/api/v1/liquidaciones"

INICIO, FIN = "2026-07-16", "2026-07-31"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def texto_pdf(contenido: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)


def plano(t: str) -> str:
    return re.sub(r"\s+", " ", t)


def a_numero(txt: str) -> Decimal:
    return D(txt.replace("$", "").replace(" ", "").replace(".", "").replace(",", "."))


def crear(client, h, url, payload):
    r = client.post(url, json=payload, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def renglon(t: str, rotulo: str) -> Decimal:
    """La cifra del resumen que va a la derecha de un rotulo, CON su signo."""
    m = re.search(re.escape(rotulo) + r"\s*([-+]\s*)?(\$[\d.,]+)", plano(t))
    assert m, f"no encontre <<{rotulo}>> en el papel:\n{plano(t)[:2500]}"
    valor = a_numero(m.group(2))
    return -valor if (m.group(1) or "").strip() == "-" else valor


def hay(t: str, rotulo: str) -> bool:
    return re.search(re.escape(rotulo) + r"\s*([-+]\s*)?\$[\d.,]+", plano(t)) is not None


# ------------------------------------------------------- las filas del detalle
FILA_PROV = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s+([\d.,]+) L\s+(\$[\d.,]+)\s+(\$[\d.,]+)")
FILA_FLETE = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s+(.+?)\s+([\d.,]+) L\s+(\$[\d.,]+|Día completo|Ya cobrado)\s+(\$[\d.,]+)")


def filas_proveedor(t: str):
    return [(f, a_numero(l), a_numero(p), a_numero(v))
            for f, l, p, v in FILA_PROV.findall(plano(t))]


def filas_flete(t: str):
    salida = []
    for f, ruta, l, p, v in FILA_FLETE.findall(plano(t)):
        salida.append((f, ruta.strip(), a_numero(l), p, a_numero(v)))
    return salida


# --------------------------------------------------------------- el escenario
def escenario_proveedor(client, h):
    """Dos productores con precios feos y litros con decimales."""
    prov = {}
    for nombre, precio in (("Henri Camelo", "1833.33"), ("Nohora Bermudez", "1750.55")):
        prov[nombre] = crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "La Vega", "precio_litro": precio})
    trans = crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "242.76",
        "modo_transporte": "litro"})
    dias = [("2026-07-16", "137.45"), ("2026-07-17", "44.23"),
            ("2026-07-18", "103.75"), ("2026-07-19", "82.48")]
    for fecha, litros in dias:
        for nombre in prov:
            crear(client, h, RECEPCIONES, {
                "fecha": fecha, "proveedor_id": prov[nombre]["id"],
                "transportador_id": trans["id"], "cantidad_litros": litros})
    return prov, trans


def liquidar(client, h, tipo):
    r = client.post(f"{LIQ}/generar",
                    json={"periodo_inicio": INICIO, "periodo_fin": FIN, "tipo": tipo},
                    headers=h)
    assert r.status_code in (200, 201), r.text
    generadas = r.json()["generadas"]
    assert generadas, r.json()
    return generadas


def leer(client, h, liq_id):
    r = client.get(f"{LIQ}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def pdf_de(client, h, liq_id) -> str:
    r = client.get(f"{LIQ}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    return texto_pdf(r.content)


# ===========================================================================
def test_zzaudit_liq_proveedor_el_papel_se_reproduce_a_mano(client, base_datos):
    """El comprobante del PRODUCTOR: cada renglon se multiplica, la columna suma
    el Valor bruto y el resumen cae en las dos cifras destacadas."""
    h = auth_headers(client, "admin.a")
    escenario_proveedor(client, h)
    liq = leer(client, h, liquidar(client, h, "proveedor")[0]["id"])
    t = pdf_de(client, h, liq["id"])
    filas = filas_proveedor(t)
    print(f"\n  renglones impresos: {len(filas)}")
    for f, l, p, v in filas:
        print(f"    {f}  {l} L x ${p} = ${centavos(l * p)}   el papel dice ${v}")

    assert len(filas) == len(liq["detalles"]), (
        f"el papel imprimio {len(filas)} renglones y la liquidacion tiene "
        f"{len(liq['detalles'])}")
    malos = [(f, l, p, v) for f, l, p, v in filas if centavos(l * p) != v]
    assert not malos, f"renglones que no se reproducen multiplicando: {malos}"

    suma = sum((v for *_, v in filas), D(0))
    bruto = renglon(t, "Valor bruto")
    print(f"  suma de los renglones = {suma}   Valor bruto impreso = {bruto}")
    assert suma == bruto, f"los renglones suman {suma} y Valor bruto dice {bruto}"

    total = renglon(t, "VALOR TOTAL")
    bonif = renglon(t, "Bonificaciones")
    desc = renglon(t, "Descuentos")
    print(f"  {bruto} + {bonif} + ({desc}) = {bruto + bonif + desc}  vs VALOR TOTAL {total}")
    assert bruto + bonif + desc == total

    anticipos = renglon(t, "Anticipos aplicados")
    saldo = renglon(t, "SALDO A PAGAR")
    print(f"  {total} + ({anticipos}) = {total + anticipos}  vs SALDO A PAGAR {saldo}")
    assert total + anticipos == saldo


def test_zzaudit_liq_proveedor_papel_contra_pantalla(client, base_datos):
    """Las cifras del papel son las mismas que muestra la pantalla."""
    h = auth_headers(client, "admin.a")
    escenario_proveedor(client, h)
    liq = leer(client, h, liquidar(client, h, "proveedor")[0]["id"])
    t = pdf_de(client, h, liq["id"])
    print(f"\n  pantalla: bruto={liq['valor_bruto']} total={liq['valor_total']} "
          f"saldo={liq['saldo']} litros={liq['total_litros']}")
    assert renglon(t, "Valor bruto") == centavos(liq["valor_bruto"])
    assert renglon(t, "VALOR TOTAL") == centavos(liq["valor_total"])
    assert renglon(t, "SALDO A PAGAR") == centavos(liq["saldo"])
    litros_txt = re.search(r"Total litros\s+([\d.,]+) L", plano(t))
    assert litros_txt, plano(t)[:1500]
    assert a_numero(litros_txt.group(1)) == D(liq["total_litros"]).normalize()


def test_zzaudit_liq_avance_y_oficial_se_leen_igual(client, base_datos):
    """El AVANCE (pre-liquidacion) y el comprobante OFICIAL del mismo tercero
    tienen que decir las mismas cifras: el productor recibe los dos."""
    h = auth_headers(client, "admin.a")
    prov, _ = escenario_proveedor(client, h)
    henri = prov["Henri Camelo"]
    r = client.post(f"{LIQ}/previsualizar/pdf",
                    json={"periodo_inicio": INICIO, "periodo_fin": FIN,
                          "tipo": "proveedor", "tercero_id": henri["id"]}, headers=h)
    assert r.status_code == 200, r.text
    avance = texto_pdf(r.content)

    filas = filas_proveedor(avance)
    print(f"\n  AVANCE: {len(filas)} renglones")
    for f, l, p, v in filas:
        print(f"    {f}  {l} L x ${p} = ${centavos(l * p)}   el papel dice ${v}")
    malos = [x for x in filas if centavos(x[1] * x[2]) != x[3]]
    assert not malos, f"renglones del avance que no se reproducen: {malos}"
    suma = sum((v for *_, v in filas), D(0))
    assert suma == renglon(avance, "Valor bruto"), (
        f"el avance: los renglones suman {suma} y Valor bruto dice "
        f"{renglon(avance, 'Valor bruto')}")
    assert (renglon(avance, "VALOR TOTAL") + renglon(avance, "Anticipos aplicados")
            == renglon(avance, "SALDO ESTIMADO"))
    assert "PRE-LIQUIDACI" in plano(avance).upper(), "el avance no se marca como preliminar"

    # y ahora el oficial del mismo senor
    ids = {l["proveedor_nombre"]: l["id"] for l in liquidar(client, h, "proveedor")}
    oficial = pdf_de(client, h, ids["Henri Camelo"])
    print(f"  AVANCE  VALOR TOTAL = {renglon(avance, 'VALOR TOTAL')}")
    print(f"  OFICIAL VALOR TOTAL = {renglon(oficial, 'VALOR TOTAL')}")
    assert renglon(avance, "VALOR TOTAL") == renglon(oficial, "VALOR TOTAL"), (
        "el avance y el comprobante oficial del mismo productor dicen cifras "
        "distintas para el mismo periodo")


def test_zzaudit_liq_transportador_por_litro_cuadra(client, base_datos):
    """El comprobante del CONDUCTOR cobrado POR LITRO."""
    h = auth_headers(client, "admin.a")
    escenario_proveedor(client, h)
    liq = leer(client, h, liquidar(client, h, "transportador")[0]["id"])
    t = pdf_de(client, h, liq["id"])
    filas = filas_flete(t)
    print(f"\n  renglones del flete: {len(filas)}")
    for f, ruta, l, p, v in filas:
        calc = centavos(l * a_numero(p)) if p.startswith("$") else None
        print(f"    {f} [{ruta}] {l} L x {p} = {calc}   el papel dice ${v}")
    assert filas, plano(t)[:2000]
    malos = [x for x in filas
             if x[3].startswith("$") and centavos(x[2] * a_numero(x[3])) != x[4]]
    assert not malos, f"renglones de flete que no se reproducen: {malos}"

    suma = sum((v for *_, v in filas), D(0))
    transporte = renglon(t, "Valor transporte")
    print(f"  suma = {suma}   Valor transporte impreso = {transporte}")
    assert suma == transporte
    assert transporte + renglon(t, "Anticipos aplicados") == renglon(t, "SALDO A PAGAR")


def test_zzaudit_liq_transportador_dia_fijo_se_verifica_leyendo(client, base_datos):
    """DIA FIJO. El renglon no se puede multiplicar y el papel TIENE que decirlo;
    y los renglones siguen sumando EXACTO el total."""
    h = auth_headers(client, "admin.a")
    fabrica = crear(client, h, RUTAS, {"nombre": "A fabrica", "municipio": "Granada"})
    napoles = crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "200", "modo_transporte": "litro",
        "rutas": [
            {"ruta_id": fabrica["id"], "valor_transporte": "150000",
             "modo_transporte": "dia_fijo"},
            {"ruta_id": napoles["id"], "valor_transporte": "242.76",
             "modo_transporte": "litro"},
        ]})
    prov = {}
    for nombre, ruta in (("Aurelio", fabrica), ("Marleny", fabrica),
                         ("Gilberto", fabrica), ("Henri", napoles)):
        prov[nombre] = crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "x", "precio_litro": "1800",
            "ruta_id": ruta["id"]})
    for nombre, litros in (("Aurelio", "137.45"), ("Marleny", "44.23"),
                           ("Gilberto", "103.75"), ("Henri", "82.48")):
        crear(client, h, RECEPCIONES, {
            "fecha": "2026-07-16", "proveedor_id": prov[nombre]["id"],
            "transportador_id": alex["id"], "cantidad_litros": litros})

    liq = leer(client, h, liquidar(client, h, "transportador")[0]["id"])
    t = pdf_de(client, h, liq["id"])
    filas = filas_flete(t)
    print(f"\n  {len(filas)} renglones:")
    for f, ruta, l, p, v in filas:
        print(f"    {f} [{ruta}] {l} L  precio={p!r}  valor=${v}")

    fijos = [x for x in filas if x[3] == "Día completo"]
    assert fijos, f"no salio el rotulo <<Dia completo>>:\n{plano(t)[:2500]}"
    assert fijos[0][4] == D("150000"), f"el dia completo no vale 150.000: {fijos[0]}"
    # la nota que le dice al conductor por que no multiplique
    assert "se cobran POR DÍA y no por litro" in plano(t), (
        "falta la letra chica del dia fijo")

    por_litro = [x for x in filas if x[3].startswith("$")]
    malos = [x for x in por_litro if centavos(x[2] * a_numero(x[3])) != x[4]]
    assert not malos, f"renglones por litro que no se reproducen: {malos}"

    suma = sum((v for *_, v in filas), D(0))
    transporte = renglon(t, "Valor transporte")
    print(f"  suma de renglones = {suma}   Valor transporte = {transporte}")
    assert suma == transporte, f"los renglones suman {suma} y el total dice {transporte}"

    # Con dias fijos mezclados el promedio NO puede aparecer como tarifa.
    assert not hay(t, "Precio promedio"), (
        "el comprobante del conductor imprimio un <<Precio promedio>> que no "
        "reproduce ningun renglon")


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_liq_el_nombre_con_acentos_rompe_la_descarga(client, base_datos):
    """El nombre del tercero viaja SIN SANEAR al Content-Disposition:
        filename = f"liquidacion_{tercero}_{fecha}.pdf"
    (LiquidacionService.generar_pdf). Con "Sebastián" el header sale con el byte
    0xE1 crudo y deja de ser UTF-8 valido. Los estados de cuenta de reventa SI lo
    sanean; este no."""
    h = auth_headers(client, "admin.a")
    prov = crear(client, h, PROVEEDORES, {
        "nombre": "Sebastián Muñoz", "vereda": "La Vega", "precio_litro": "1833.33"})
    trans = crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "242.76"})
    crear(client, h, RECEPCIONES, {
        "fecha": "2026-07-16", "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "137.45"})
    liq = liquidar(client, h, "proveedor")[0]
    fallo = None
    cabecera = ""
    try:
        r = client.get(f"{LIQ}/{liq['id']}/pdf", headers=h)
        cabecera = r.headers.get("content-disposition", "")
        print(f"\n  content-disposition = {cabecera!r}")
    except UnicodeDecodeError as e:
        fallo = e
        print(f"\n  la descarga NI SE PUDO LEER: {e}")
    assert fallo is None, (
        f"descargar el comprobante de un productor con acentos rompe el cliente: {fallo}")
    assert cabecera.isascii(), f"header no ASCII: {cabecera!r}"
