"""AUDITORIA DEL RECIBO DE NOMINA EN PDF (el papel que firma el empleado).

Se mide contra la API real: se crea el empleado, se registra el pago, se descarga
el PDF y se le EXTRAE EL TEXTO. Nada se da por bueno leyendo el codigo: todo se
reproduce con calculadora sobre lo que quedo impreso.

Lo que se le exige al papel:
  1. que los renglones del resumen SUMEN la cifra destacada (TOTAL PAGADO);
  2. que cada renglon se pueda reproducir con una multiplicacion
     (dias x valor dia = subtotal devengado);
  3. que la tabla "Anticipos descontados" sume el renglon "Descuento anticipos";
  4. que las cifras salgan en formato colombiano (miles con punto, decimales con
     coma), igual que en todos los demas papeles del sistema;
  5. que el papel diga lo mismo que la pantalla (el JSON de /nomina).

Cifras feas a proposito.
"""
import io
import re
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

NOMINA = "/api/v1/nomina"
EMPLEADOS = "/api/v1/empleados"
ANTICIPOS = "/api/v1/anticipos"

JORNAL = "Valor día (Jornal)"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def texto_pdf(contenido: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)


def plano(t: str) -> str:
    """Todo el texto en una sola linea con espacios normalizados: extract_text
    parte las celdas de una tabla en renglones distintos y no siempre igual."""
    return re.sub(r"\s+", " ", t)


def a_numero(texto_pesos: str) -> Decimal:
    """'$1.833.333,45' -> Decimal('1833333.45'). Formato colombiano."""
    limpio = texto_pesos.replace("$", "").replace(" ", "").replace(".", "").replace(",", ".")
    return D(limpio)


def valor_de(t: str, rotulo: str) -> str:
    """La cifra impresa a la derecha de un rotulo del resumen."""
    m = re.search(re.escape(rotulo) + r"\s*(-?\s*\$[\d.,]+)", plano(t))
    assert m, f"no encontre el renglon <<{rotulo}>> en el papel:\n{plano(t)[:2000]}"
    return m.group(1).replace(" ", "")


def crear_empleado(client, h, **campos):
    # SIN ACENTOS a proposito en el caso base: el nombre del empleado viaja SIN
    # sanear al header Content-Disposition y con acentos la descarga ni siquiera
    # se puede leer (ver test_zzaudit_nomina_el_nombre_con_acentos_rompe_la_descarga).
    body = {"nombre": "Maria Fernanda", "apellido": "Gutierrez"}
    body.update(campos)
    r = client.post(EMPLEADOS, json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def pagar(client, h, **campos):
    r = client.post(NOMINA, json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def recibo(client, h, pago_id) -> str:
    r = client.get(f"{NOMINA}/{pago_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    return texto_pdf(r.content)


# ---------------------------------------------------------------------------
def test_zzaudit_nomina_el_papel_suma_de_arriba_abajo(client, base_datos):
    """La cuenta que hace el empleado con la calculadora:
    dias x jornal = subtotal;  subtotal - anticipos = TOTAL PAGADO."""
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="61733")
    pago = pagar(client, h, empleado_id=emp["id"], fecha="2026-07-31",
                 dias_trabajados="12.50", periodo="2da quincena julio")
    t = recibo(client, h, pago["id"])

    dias_txt = re.search(r"D[ií]as trabajados\s+([^\s]+)", plano(t))
    assert dias_txt, plano(t)[:1500]
    subtotal = a_numero(valor_de(t, "Subtotal devengado"))
    descuento = a_numero(valor_de(t, "Descuento anticipos").lstrip("-"))
    total = a_numero(valor_de(t, "TOTAL PAGADO"))
    jornal = a_numero(valor_de(t, JORNAL))

    print(f"\n  dias impresos = {dias_txt.group(1)!r}  jornal = {jornal}")
    print(f"  subtotal = {subtotal}  descuento = {descuento}  total = {total}")

    assert centavos(D("12.50") * jornal) == subtotal, (
        f"12,50 x {jornal} = {centavos(D('12.50') * jornal)} pero el papel "
        f"dice {subtotal}")
    assert subtotal - descuento == total, (
        f"{subtotal} - {descuento} = {subtotal - descuento} pero TOTAL PAGADO "
        f"dice {total}")


def test_zzaudit_nomina_los_dias_en_formato_colombiano(client, base_datos):
    """Los DIAS TRABAJADOS se imprimen crudos de la base ('12.50'), con PUNTO
    decimal y con ceros de relleno, en un papel donde todo lo demas esta en
    formato colombiano ($771.662,50)."""
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="61733")
    medio = pagar(client, h, empleado_id=emp["id"], fecha="2026-07-31",
                  dias_trabajados="12.50")
    entero = pagar(client, h, empleado_id=emp["id"], fecha="2026-08-15",
                   dias_trabajados="15")

    t_medio = plano(recibo(client, h, medio["id"]))
    t_entero = plano(recibo(client, h, entero["id"]))
    d_medio = re.search(r"D[ií]as trabajados\s+([^\s]+)", t_medio).group(1)
    d_entero = re.search(r"D[ií]as trabajados\s+([^\s]+)", t_entero).group(1)
    print(f"\n  12,50 dias se imprime {d_medio!r}   ·   15 dias se imprime {d_entero!r}")

    assert d_medio == "12,5", (
        f"el papel imprime los dias como {d_medio!r}: punto decimal en un "
        f"documento colombiano")
    assert d_entero == "15", (
        f"el papel imprime los dias como {d_entero!r}: ceros de relleno")


def test_zzaudit_nomina_anticipos_impresos_suman_el_descuento(client, base_datos):
    """La tabla <<Anticipos descontados>> tiene que sumar EXACTO el renglon
    <<Descuento anticipos>>."""
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="61733")
    for fecha, valor in (("2026-07-05", "242760.45"), ("2026-07-12", "183333.33")):
        r = client.post(ANTICIPOS, json={"tipo": "empleado", "empleado_id": emp["id"],
                                         "fecha": fecha, "valor": valor,
                                         "observaciones": "adelanto de la semana"},
                        headers=h)
        assert r.status_code == 201, r.text
    pago = pagar(client, h, empleado_id=emp["id"], fecha="2026-07-31",
                 dias_trabajados="12.50")
    t = plano(recibo(client, h, pago["id"]))
    zona = t.split("Anticipos descontados", 1)
    assert len(zona) == 2, f"no se imprimio la tabla de anticipos:\n{t[:1500]}"
    cuerpo = zona[1].split("Entregu")[0]
    filas = [a_numero(x) for x in re.findall(r"\d{2}/\d{2}/\d{4}\s*(\$[\d.,]+)", cuerpo)]
    descuento = a_numero(valor_de(t, "Descuento anticipos").lstrip("-"))
    print(f"\n  filas de anticipos impresas = {filas}  suma = {sum(filas, D(0))}")
    print(f"  renglon <<Descuento anticipos>> = {descuento}")
    assert filas, f"no se imprimio ninguna fila de anticipos:\n{cuerpo[:800]}"
    assert sum(filas, D(0)) == descuento, (
        f"las filas suman {sum(filas, D(0))} y el renglon dice {descuento}")


def test_zzaudit_nomina_el_papel_dice_lo_mismo_que_la_pantalla(client, base_datos):
    """Papel vs pantalla: las cifras del recibo son las del JSON de nomina."""
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="61733")
    r = client.post(ANTICIPOS, json={"tipo": "empleado", "empleado_id": emp["id"],
                                     "fecha": "2026-07-05", "valor": "242760.45"},
                    headers=h)
    assert r.status_code == 201, r.text
    pago = pagar(client, h, empleado_id=emp["id"], fecha="2026-07-31",
                 dias_trabajados="12.50")
    t = recibo(client, h, pago["id"])
    print(f"\n  pantalla: valor_dia={pago['valor_dia']} anticipos={pago['anticipos']} "
          f"total={pago['total']}")
    assert a_numero(valor_de(t, JORNAL)) == centavos(pago["valor_dia"])
    assert a_numero(valor_de(t, "Descuento anticipos").lstrip("-")) == centavos(pago["anticipos"])
    assert a_numero(valor_de(t, "TOTAL PAGADO")) == centavos(pago["total"])


def test_zzaudit_nomina_el_medio_centavo_del_banquero(client, base_datos):
    """EL REDONDEO. El subtotal impreso se recalcula al vuelo y sale por `pesos`,
    que redondea con el MEDIO PARA ARRIBA de la casa; el TOTAL guardado se
    redondeo con el del BANQUERO (`Decimal.quantize` sin `rounding=` en
    PagoEmpleadoService.crear). Cuando el producto cae justo en el medio, el
    papel no cuadra por un centavo.

        12,50 dias x $685,57 = $8.569,625
            medio para arriba  -> 8.569,63   ("Subtotal devengado")
            medio del banquero -> 8.569,62   (`total` guardado)
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="685.57")
    pago = pagar(client, h, empleado_id=emp["id"], fecha="2026-07-31",
                 dias_trabajados="12.50")
    t = recibo(client, h, pago["id"])
    subtotal = a_numero(valor_de(t, "Subtotal devengado"))
    descuento = a_numero(valor_de(t, "Descuento anticipos").lstrip("-"))
    total = a_numero(valor_de(t, "TOTAL PAGADO"))
    print(f"\n  12,50 x 685,57 = {D('12.50') * D('685.57')}")
    print(f"  total guardado (pantalla) = {pago['total']}")
    print(f"  subtotal impreso = {subtotal}  descuento = {descuento}  total = {total}")
    print(f"  la resta del papel da {subtotal - descuento}")
    assert subtotal - descuento == total, (
        f"el papel dice {subtotal} - {descuento} = {subtotal - descuento}, pero "
        f"la cifra destacada dice {total}")


def test_zzaudit_nomina_el_medio_centavo_con_cifras_de_verdad(client, base_datos):
    """El mismo defecto con cifras que SI se escriben en una quesera: el jornal
    sale de repartir el sueldo del mes ($1.750.000 / 30 = $58.333,33) y se le
    pagan DOS DIAS Y MEDIO.

        2,50 x $58.333,33 = $145.833,325
            "Subtotal devengado" (medio arriba) -> $145.833,33
            `total` guardado    (banquero)      -> $145.833,32
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="58333.33")
    pago = pagar(client, h, empleado_id=emp["id"], fecha="2026-07-31",
                 dias_trabajados="2.50", periodo="del 1 al 3")
    t = recibo(client, h, pago["id"])
    subtotal = a_numero(valor_de(t, "Subtotal devengado"))
    descuento = a_numero(valor_de(t, "Descuento anticipos").lstrip("-"))
    total = a_numero(valor_de(t, "TOTAL PAGADO"))
    print(f"\n  2,50 x 58.333,33 = {D('2.50') * D('58333.33')}")
    print(f"  papel: {subtotal} - {descuento} = {subtotal - descuento}")
    print(f"  cifra destacada / pantalla: {total} / {pago['total']}")
    assert subtotal - descuento == total, (
        f"el papel dice {subtotal} - {descuento} = {subtotal - descuento}, pero "
        f"la cifra destacada dice {total}")


def test_zzaudit_nomina_cifra_larga(client, base_datos):
    """Una cifra de decenas de millones, en formato colombiano."""
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="1833333.45")
    pago = pagar(client, h, empleado_id=emp["id"], fecha="2026-07-31",
                 dias_trabajados="30")
    t = plano(recibo(client, h, pago["id"]))
    print(f"\n  {t[:500]}")
    subtotal = a_numero(valor_de(t, "Subtotal devengado"))
    assert subtotal == centavos(D("30") * D("1833333.45")), subtotal
    assert "$55.000.003,50" in t, f"formato colombiano roto: {t[:600]}"


def test_zzaudit_nomina_el_nombre_con_acentos_rompe_la_descarga(client, base_datos):
    """EL NOMBRE DEL EMPLEADO VIAJA SIN SANEAR AL Content-Disposition.

    `PagoEmpleadoService.generar_pdf` arma
        filename = f"recibo_nomina_{empleado_nombre}_{fecha}.pdf"
    con el nombre tal como lo escribio el dueno. Starlette codifica los headers
    en latin-1, asi que "José" sale como el byte 0xE9 crudo: un header que NO es
    UTF-8 valido. Medido contra uvicorn de verdad, la respuesta sale con
        Content-Disposition: attachment; filename="recibo_nomina_Jos\\xe9_...pdf"
    y todo cliente que decodifique headers en UTF-8 (el TestClient de Starlette,
    `fetch` del navegador, que es por donde descarga el front) o revienta o
    reemplaza la letra por el caracter de sustitucion.

    Los DOS estados de cuenta de reventa SI lo sanean
    (`_nombre_archivo_cliente` / `_nombre_archivo_productor`, quitan acentos);
    nomina y liquidaciones no.

    En el Guaviare los nombres son Jose, Sebastian, Munoz, Gutierrez: es el caso
    NORMAL, no el raro.
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, nombre="José Ángel",
                         apellido="Muñoz Peñaloza", valor_dia="61733")
    pago = pagar(client, h, empleado_id=emp["id"], fecha="2026-07-31",
                 dias_trabajados="12.50")
    fallo = None
    try:
        r = client.get(f"{NOMINA}/{pago['id']}/pdf", headers=h)
        print(f"\n  content-disposition = {r.headers.get('content-disposition')!r}")
        cabecera = r.headers.get("content-disposition", "")
    except UnicodeDecodeError as e:
        fallo = e
        cabecera = ""
        print(f"\n  la descarga NI SE PUDO LEER: {e}")
    assert fallo is None, (
        "descargar el recibo de un empleado con acentos rompe el cliente HTTP: "
        f"{fallo}")
    assert cabecera.isascii(), (
        f"el Content-Disposition lleva bytes no ASCII: {cabecera!r}")
