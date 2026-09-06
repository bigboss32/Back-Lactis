"""AUDITORIA DE LOS DOS ESTADOS DE CUENTA DE REVENTA EN PDF.

Son los dos papeles que se imprimen y se le entregan a un tercero:

  · GET /reventa/estado-cuenta/pdf            -> se lo lleva EL CLIENTE
  · GET /reventa/estado-cuenta-productor/pdf  -> se lo lleva EL PRODUCTOR

DOS COSAS SE MIDEN, y las dos sobre el TEXTO EXTRAIDO DEL PDF DE VERDAD:

1. CONFIDENCIALIDAD (es legal, no cosmetica). Se siembran CANARIOS: cifras y
   palabras unicas que solo existen del lado prohibido. En el papel del cliente
   no puede aparecer nada de la compra (costo, productor, margen, ganancia,
   gastos, venta libre) ni de otro cliente; en el del productor no puede aparecer
   nada de la venta (precio de venta, cliente, gasto, margen) ni de otro
   productor. Se busca en el texto, en los metadatos y en el nombre del archivo.

2. QUE EL PAPEL CUADRE A MANO. Cada renglon impreso se reproduce con una
   multiplicacion (kilos x precio = total), la columna suma la fila de TOTALES, y
   el resumen cae EXACTO en la cifra destacada.

Cifras feas a proposito.
"""
import io
import re
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/reventa"

# ------------------------------------------------------------------ canarios
PRODUCTOR = "Aurelio Bermudez"
OTRO_PRODUCTOR = "Nohora CANARIOPRODUCTORDOS"
CLIENTE = "Deposito La 44"
OTRO_CLIENTE = "CANARIOCLIENTEDOS Tienda"

COSTO_KILO = Decimal("11317.45")     # lo del productor: PROHIBIDO ante el cliente
VENTA_KILO = Decimal("23457.76")     # lo de la venta:   PROHIBIDO ante el productor
GASTO_KILO = Decimal("419.83")       # gasto de venta:   PROHIBIDO en los dos
OBS_COMPRA = "NOTAINTERNACOMPRA se lo revendo al Deposito"
OBS_ABONO_COMPRA = "NOTAINTERNAABONOCOMPRA le rebaje el flete"
OBS_ABONO_VENTA = "NOTAINTERNAABONOVENTA quedo debiendo por el queso malo"
OBS_SALDO = "NOTAINTERNASALDO cuadre a ojo del libro viejo"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def texto_pdf(contenido: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)


def metadatos(contenido: bytes) -> str:
    meta = PdfReader(io.BytesIO(contenido)).metadata or {}
    return " | ".join(f"{k}={v}" for k, v in meta.items())


def plano(t: str) -> str:
    return re.sub(r"\s+", " ", t)


def a_numero(txt: str) -> Decimal:
    return D(txt.replace("$", "").replace(" ", "").replace(".", "").replace(",", "."))


def renglon(t: str, rotulo: str) -> Decimal:
    m = re.search(re.escape(rotulo) + r"\s*(\$[\d.,]+)", plano(t))
    assert m, f"no encontre <<{rotulo}>>:\n{plano(t)[:2500]}"
    return a_numero(m.group(1))


def crear(client, h, url, payload):
    r = client.post(url, json=payload, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def sembrar(client, h) -> dict:
    """Compras, ventas, abonos y saldos del libro viejo, todo con canarios."""
    ids = {}
    ids["c1"] = crear(client, h, f"{API}/compras", {
        "fecha": "2026-02-03", "productor": PRODUCTOR, "kilos_brutos": "820.53",
        "precio_kilo": str(COSTO_KILO), "borona_kilos": "18.27",
        "observaciones": OBS_COMPRA})["id"]
    ids["c2"] = crear(client, h, f"{API}/compras", {
        "fecha": "2026-02-11", "productor": PRODUCTOR, "kilos_brutos": "410.11",
        "precio_kilo": "12033.55", "borona_kilos": "7.09"})["id"]
    # Un SEGUNDO productor, cuyo nombre no puede salir en el papel del primero.
    ids["c3"] = crear(client, h, f"{API}/compras", {
        "fecha": "2026-02-05", "productor": OTRO_PRODUCTOR, "kilos_brutos": "233.41",
        "precio_kilo": "9137", "borona_kilos": "11.03"})["id"]

    ids["v1"] = crear(client, h, f"{API}/ventas", {
        "fecha": "2026-02-12", "cliente": CLIENTE, "tipo": "queso",
        "kilos": "900.37", "precio_kilo": str(VENTA_KILO),
        "gasto_por_kilo": str(GASTO_KILO)})["id"]
    ids["v2"] = crear(client, h, f"{API}/ventas", {
        "fecha": "2026-02-24", "cliente": CLIENTE, "tipo": "queso",
        "kilos": "311.44", "precio_kilo": "20917.33", "gasto_por_kilo": "93.45"})["id"]
    # Un SEGUNDO cliente, cuyo nombre no puede salir en el papel del primero.
    ids["v3"] = crear(client, h, f"{API}/ventas", {
        "fecha": "2026-02-26", "cliente": OTRO_CLIENTE, "tipo": "queso",
        "kilos": "33.37", "precio_kilo": "24133"})["id"]

    r = client.post(f"{API}/compras/{ids['c1']}/abonos",
                    json={"fecha": "2026-02-14", "valor": "5000000.33",
                          "observaciones": OBS_ABONO_COMPRA}, headers=h)
    assert r.status_code in (200, 201), r.text
    r = client.post(f"{API}/ventas/{ids['v1']}/abonos",
                    json={"fecha": "2026-02-20", "valor": "9000000.77",
                          "observaciones": OBS_ABONO_VENTA}, headers=h)
    assert r.status_code in (200, 201), r.text
    return ids


def sembrar_saldos_viejos(client, h) -> None:
    """Un saldo del libro anterior POR PAGAR (del productor) y otro POR COBRAR
    (del cliente). Cada papel solo puede traer el suyo."""
    for tipo, tercero, concepto, valor in (
        ("pagar", PRODUCTOR, "Compra vieja del libro 044", "1833333.45"),
        ("cobrar", CLIENTE, "Venta vieja del libro 077", "2427600.45"),
    ):
        r = client.post(f"{API}/saldos-anteriores", json={
            "tipo": tipo, "tercero": tercero, "fecha": "2025-11-30",
            "concepto": concepto, "valor_total": valor, "abonado": "133333.33",
            "observaciones": OBS_SALDO}, headers=h)
        assert r.status_code in (200, 201), r.text


def pdf_cliente(client, h, cliente=CLIENTE):
    r = client.get(f"{API}/estado-cuenta/pdf", params={"cliente": cliente}, headers=h)
    assert r.status_code == 200, r.text
    return r.content, r.headers.get("content-disposition", "")


def pdf_productor(client, h, productor=PRODUCTOR):
    r = client.get(f"{API}/estado-cuenta-productor/pdf",
                   params={"productor": productor}, headers=h)
    assert r.status_code == 200, r.text
    return r.content, r.headers.get("content-disposition", "")


def json_cliente(client, h, cliente=CLIENTE):
    r = client.get(f"{API}/estado-cuenta", params={"cliente": cliente}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def json_productor(client, h, productor=PRODUCTOR):
    r = client.get(f"{API}/estado-cuenta-productor",
                   params={"productor": productor}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def sin_puntos(t: str) -> str:
    """El texto con las cifras 'desformateadas' para poder buscar un canario
    numerico: 11.317,45 -> 11317.45."""
    return re.sub(r"(\d)\.(?=\d{3})", r"\1", t).replace(",", ".")


# ===================================================== 1. CONFIDENCIALIDAD
def test_zzaudit_reventa_el_papel_del_cliente_no_lleva_nada_de_la_compra(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    sembrar_saldos_viejos(client, h)
    contenido, disposicion = pdf_cliente(client, h)
    t = plano(texto_pdf(contenido))
    todo = f"{t} || {metadatos(contenido)} || {disposicion}"
    numeros = sin_puntos(todo)
    print(f"\n  --- PAPEL DEL CLIENTE ---\n  {t[:1600]}")

    # el escaner tiene dientes: lo que SI tiene que estar
    assert CLIENTE in t, "el papel ni siquiera nombra al cliente"
    assert "23457" in numeros, "el precio de VENTA (que si es suyo) no aparece"

    prohibido = {
        "nombre del productor": PRODUCTOR,
        "nombre del otro productor": "CANARIOPRODUCTORDOS",
        "nombre del otro cliente": "CANARIOCLIENTEDOS",
        "nota interna de la compra": "NOTAINTERNACOMPRA",
        "nota interna del abono de la compra": "NOTAINTERNAABONOCOMPRA",
        "nota interna del abono de la venta": "NOTAINTERNAABONOVENTA",
        "nota interna del saldo viejo": "NOTAINTERNASALDO",
        "concepto del saldo del PRODUCTOR": "libro 044",
    }
    encontrados = {k: v for k, v in prohibido.items() if v in todo}
    numericos = {
        "costo de compra por kilo": "11317.45",
        "gasto de venta por kilo": "419.83",
    }
    encontrados.update({k: v for k, v in numericos.items() if v in numeros})
    for k, v in encontrados.items():
        print(f"  FUGA: {k} -> {v!r}")
    assert not encontrados, f"el papel del CLIENTE lleva datos internos: {encontrados}"

    for palabra in ("Ganancia", "Margen", "Costo", "Venta libre", "Gastos",
                    "Utilidad", "Productor"):
        assert palabra.lower() not in t.lower(), (
            f"el papel del cliente dice la palabra {palabra!r}: {t[:1200]}")


def test_zzaudit_reventa_el_papel_del_productor_no_lleva_nada_de_la_venta(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    sembrar_saldos_viejos(client, h)
    contenido, disposicion = pdf_productor(client, h)
    t = plano(texto_pdf(contenido))
    todo = f"{t} || {metadatos(contenido)} || {disposicion}"
    numeros = sin_puntos(todo)
    print(f"\n  --- PAPEL DEL PRODUCTOR ---\n  {t[:1600]}")

    assert PRODUCTOR in t, "el papel ni siquiera nombra al productor"
    assert "11317.45" in numeros, "su propio precio de compra no aparece"

    prohibido = {
        "nombre del cliente": CLIENTE,
        "nombre del otro cliente": "CANARIOCLIENTEDOS",
        "nombre del otro productor": "CANARIOPRODUCTORDOS",
        "nota interna de la compra": "NOTAINTERNACOMPRA",
        "nota interna del abono": "NOTAINTERNAABONOCOMPRA",
        "nota interna del saldo viejo": "NOTAINTERNASALDO",
        "concepto del saldo del CLIENTE": "libro 077",
    }
    encontrados = {k: v for k, v in prohibido.items() if v in todo}
    numericos = {
        "precio de venta por kilo": "23457.76",
        "gasto de venta por kilo": "419.83",
        "deuda vieja del CLIENTE": "2427600.45",
    }
    encontrados.update({k: v for k, v in numericos.items() if v in numeros})
    for k, v in encontrados.items():
        print(f"  FUGA: {k} -> {v!r}")
    assert not encontrados, f"el papel del PRODUCTOR lleva datos de la venta: {encontrados}"

    for palabra in ("Ganancia", "Margen", "Cliente", "Venta libre", "Utilidad"):
        assert palabra.lower() not in t.lower(), (
            f"el papel del productor dice la palabra {palabra!r}: {t[:1200]}")


def test_zzaudit_reventa_ningun_papel_cruza_de_empresa(client, base_datos):
    """La regla multiempresa en el papel: la quesera B tiene un cliente y un
    productor CON EL MISMO NOMBRE y otras cifras. Ninguna puede aparecer."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    sembrar(client, ha)
    crear(client, hb, f"{API}/compras", {
        "fecha": "2026-02-03", "productor": PRODUCTOR, "kilos_brutos": "777.77",
        "precio_kilo": "7777.77", "observaciones": "CANARIODEB compra de la otra quesera"})
    crear(client, hb, f"{API}/ventas", {
        "fecha": "2026-02-12", "cliente": CLIENTE, "tipo": "queso",
        "kilos": "555.55", "precio_kilo": "5555.55"})

    for etiqueta, (contenido, disp) in (
        ("cliente", pdf_cliente(client, ha)),
        ("productor", pdf_productor(client, ha)),
    ):
        todo = f"{plano(texto_pdf(contenido))} || {metadatos(contenido)} || {disp}"
        numeros = sin_puntos(todo)
        print(f"\n  papel del {etiqueta}: {todo[:400]}")
        for canario in ("CANARIODEB", "777.77", "7777.77", "555.55", "5555.55"):
            assert canario not in numeros, (
                f"el papel del {etiqueta} de la quesera A lleva {canario!r}, que es "
                "de la quesera B")


# ============================================== 2. QUE EL PAPEL CUADRE A MANO
FILA_CLIENTE = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s+(\S+)\s+([\d.,]+) kg\s+(\$[\d.,]+)\s+(\$[\d.,]+)"
    r"\s+(\$[\d.,]+)\s+(\$[\d.,]+)")
FILA_PRODUCTOR = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s+([\d.,]+) kg\s+(\$[\d.,]+)\s+(\$[\d.,]+)"
    r"\s+(\$[\d.,]+)\s+(\$[\d.,]+)")


def test_zzaudit_reventa_el_papel_del_cliente_cuadra_a_mano(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    contenido, _ = pdf_cliente(client, h)
    t = plano(texto_pdf(contenido))
    datos = json_cliente(client, h)
    filas = FILA_CLIENTE.findall(t)
    print(f"\n  {len(filas)} renglones impresos")
    total_filas = D(0)
    abonado_filas = D(0)
    for fecha, prod, kilos, precio, total, abonado, saldo in filas:
        k, p, tot = a_numero(kilos), a_numero(precio), a_numero(total)
        print(f"    {fecha} {prod} {k} kg x ${p} = ${centavos(k * p)}  papel=${tot} "
              f"abonado=${a_numero(abonado)} saldo=${a_numero(saldo)}")
        assert centavos(k * p) == tot, (
            f"{k} kg x ${p} = {centavos(k * p)} pero el papel dice {tot}")
        assert tot - a_numero(abonado) == a_numero(saldo), (
            f"la fila del {fecha} no cuadra: {tot} - {a_numero(abonado)} != "
            f"{a_numero(saldo)}")
        total_filas += tot
        abonado_filas += a_numero(abonado)

    assert len(filas) == len(datos["ventas"]), (
        f"el papel imprimio {len(filas)} filas y la pantalla tiene "
        f"{len(datos['ventas'])}")
    facturado = renglon(t, "Total facturado")
    abonado_res = renglon(t, r"(-) Total abonado")
    saldo = renglon(t, "SALDO PENDIENTE")
    print(f"  filas: total={total_filas} abonado={abonado_filas}")
    print(f"  resumen: facturado={facturado} abonado={abonado_res} saldo={saldo}")
    assert total_filas == facturado, (
        f"las filas suman {total_filas} y <<Total facturado>> dice {facturado}")
    assert abonado_filas == abonado_res
    assert facturado - abonado_res == saldo, (
        f"{facturado} - {abonado_res} = {facturado - abonado_res} pero la cifra "
        f"destacada dice {saldo}")
    assert facturado == centavos(datos["total_facturado"]), "papel != pantalla"
    assert saldo == centavos(datos["saldo"]), "papel != pantalla"


def test_zzaudit_reventa_el_papel_del_productor_cuadra_a_mano(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    contenido, _ = pdf_productor(client, h)
    t = plano(texto_pdf(contenido))
    datos = json_productor(client, h)
    filas = FILA_PRODUCTOR.findall(t)
    print(f"\n  {len(filas)} renglones impresos")
    total_filas = D(0)
    pagado_filas = D(0)
    for fecha, kilos, precio, total, abonado, saldo in filas:
        k, p, tot = a_numero(kilos), a_numero(precio), a_numero(total)
        print(f"    {fecha} {k} kg x ${p} = ${centavos(k * p)}  papel=${tot} "
              f"abonado=${a_numero(abonado)} saldo=${a_numero(saldo)}")
        assert centavos(k * p) == tot, (
            f"{k} kg x ${p} = {centavos(k * p)} pero el papel dice {tot}")
        assert tot - a_numero(abonado) == a_numero(saldo)
        total_filas += tot
        pagado_filas += a_numero(abonado)

    assert len(filas) == len(datos["compras_detalle"])
    comprado = renglon(t, "Total comprado")
    pagado = renglon(t, r"(-) Total pagado")
    saldo = renglon(t, "SALDO A FAVOR DEL PRODUCTOR")
    print(f"  filas: comprado={total_filas} pagado={pagado_filas}")
    print(f"  resumen: comprado={comprado} pagado={pagado} saldo={saldo}")
    assert total_filas == comprado, (
        f"las filas suman {total_filas} y <<Total comprado>> dice {comprado}")
    assert pagado_filas == pagado
    assert comprado - pagado == saldo
    assert comprado == centavos(datos["total_comprado"]), "papel != pantalla"
    assert saldo == centavos(datos["saldo"]), "papel != pantalla"


def test_zzaudit_reventa_con_libro_viejo_los_tres_renglones_suman(client, base_datos):
    """Con saldos del sistema anterior el resumen tiene TRES renglones y el
    dueno los suma de arriba abajo: facturado - abonado + saldo viejo = saldo."""
    h = auth_headers(client, "admin.a")
    sembrar(client, h)
    sembrar_saldos_viejos(client, h)
    for quien, (contenido, _), rotulos in (
        ("cliente", pdf_cliente(client, h),
         ("Total facturado", "(-) Total abonado", "SALDO PENDIENTE")),
        ("productor", pdf_productor(client, h),
         ("Total comprado", "(-) Total pagado", "SALDO A FAVOR DEL PRODUCTOR")),
    ):
        t = plano(texto_pdf(contenido))
        a = renglon(t, rotulos[0])
        b = renglon(t, rotulos[1])
        c = renglon(t, "(+) Saldo de la cuenta anterior")
        d = renglon(t, rotulos[2])
        print(f"\n  {quien}: {a} - {b} + {c} = {a - b + c}   destacado = {d}")
        assert a - b + c == d, (
            f"el papel del {quien} no suma: {a} - {b} + {c} = {a - b + c} pero la "
            f"cifra destacada dice {d}")
        # y la tabla del libro viejo tiene que cuadrar sola
        tot = renglon(t, "TOTALES")
        print(f"  {quien}: fila TOTALES de la tabla = {tot}")


def test_zzaudit_reventa_acentos_en_el_nombre_del_archivo(client, base_datos):
    """Los dos estados de cuenta SI sanean el nombre del archivo."""
    h = auth_headers(client, "admin.a")
    crear(client, h, f"{API}/compras", {
        "fecha": "2026-02-03", "productor": "Sebastián Muñoz",
        "kilos_brutos": "820.53", "precio_kilo": "11317.45"})
    crear(client, h, f"{API}/ventas", {
        "fecha": "2026-02-12", "cliente": "Depósito El Ñame", "tipo": "queso",
        "kilos": "100.37", "precio_kilo": "23457.76"})
    _, d1 = pdf_productor(client, h, "Sebastián Muñoz")
    _, d2 = pdf_cliente(client, h, "Depósito El Ñame")
    print(f"\n  productor -> {d1!r}\n  cliente   -> {d2!r}")
    assert d1.isascii() and d2.isascii(), (d1, d2)


# =================================== 3. LA TABLA DE PAGOS SE PUEDE SUMAR
def test_zzaudit_reventa_los_pagos_impresos_suman_el_total_abonado(client, base_datos):
    """Las tablas <<Pagos recibidos>> / <<Pagos realizados>> NO llevan fila de
    totales: el tercero las suma a mano y tienen que dar el renglon del resumen."""
    h = auth_headers(client, "admin.a")
    ids = sembrar(client, h)
    # varios abonos, con centavos, a compras y ventas distintas
    for url, valor, fecha in (
        (f"{API}/ventas/{ids['v1']}/abonos", "242760.45", "2026-02-21"),
        (f"{API}/ventas/{ids['v2']}/abonos", "183333.33", "2026-02-25"),
        (f"{API}/compras/{ids['c2']}/abonos", "419830.07", "2026-02-13"),
    ):
        r = client.post(url, json={"fecha": fecha, "valor": valor}, headers=h)
        assert r.status_code in (200, 201), r.text

    for etiqueta, (contenido, _), titulo, rotulo in (
        ("cliente", pdf_cliente(client, h), "Pagos recibidos", "(-) Total abonado"),
        ("productor", pdf_productor(client, h), "Pagos realizados", "(-) Total pagado"),
    ):
        t = plano(texto_pdf(contenido))
        z = t.split(titulo, 1)
        assert len(z) == 2, f"no salio la tabla <<{titulo}>>:\n{t[:2500]}"
        cuerpo = z[1].split("Total ")[0]
        filas = [a_numero(x) for x in
                 re.findall(r"\d{2}/\d{2}/\d{4}\s*(\$[\d.,]+)", cuerpo)]
        total = renglon(t, rotulo)
        print(f"\n  {etiqueta}: filas={filas} suma={sum(filas, D(0))} renglon={total}")
        assert filas, f"no se imprimio ningun pago:\n{cuerpo[:600]}"
        assert sum(filas, D(0)) == total, (
            f"el papel del {etiqueta}: los pagos impresos suman "
            f"{sum(filas, D(0))} y <<{rotulo}>> dice {total}")


def test_zzaudit_reventa_con_rango_de_fechas_el_papel_sigue_cuadrando(client, base_datos):
    """El dueno saca el estado de cuenta de un mes. Un abono hecho FUERA de ese
    mes, a una compra que si esta dentro, no puede descuadrar el papel."""
    h = auth_headers(client, "admin.a")
    ids = sembrar(client, h)
    # abono de MARZO a una venta de FEBRERO
    r = client.post(f"{API}/ventas/{ids['v1']}/abonos",
                    json={"fecha": "2026-03-20", "valor": "419830.07"}, headers=h)
    assert r.status_code in (200, 201), r.text

    r = client.get(f"{API}/estado-cuenta/pdf",
                   params={"cliente": CLIENTE, "desde": "2026-02-01",
                           "hasta": "2026-02-28"}, headers=h)
    assert r.status_code == 200, r.text
    t = plano(texto_pdf(r.content))
    print(f"\n  {t[:1300]}")
    facturado = renglon(t, "Total facturado")
    abonado = renglon(t, "(-) Total abonado")
    saldo = renglon(t, "SALDO PENDIENTE")
    filas = FILA_CLIENTE.findall(t)
    suma_filas = sum((a_numero(x[4]) for x in filas), D(0))
    suma_saldos = sum((a_numero(x[6]) for x in filas), D(0))
    print(f"  filas={len(filas)} suman={suma_filas}  saldos={suma_saldos}")
    print(f"  facturado={facturado} abonado={abonado} saldo={saldo}")
    assert suma_filas == facturado, (
        f"con rango de fechas las filas suman {suma_filas} y el resumen dice {facturado}")
    assert facturado - abonado == saldo
    assert suma_saldos == saldo, (
        f"la columna Saldo suma {suma_saldos} y la cifra destacada dice {saldo}")
