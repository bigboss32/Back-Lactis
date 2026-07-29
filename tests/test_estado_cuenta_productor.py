"""Estado de cuenta DEL PRODUCTOR: el espejo del estado de cuenta del cliente.

Es el documento que se le entrega al productor para cuadrar cuentas con él: lo
que se le compró, lo que se le pagó y lo que se le debe. Documento INTERNO: sin
numeración consecutiva, sin resolución de la DIAN y sin IVA.

Los números se imprimen en cada prueba porque el usuario verifica los desgloses
a mano con calculadora: todo desglose tiene que sumar EXACTO la cifra grande.
"""
import io
from decimal import Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/reventa"


def D(valor):
    return Decimal(str(valor))


def compra(client, headers, **datos):
    r = client.post(f"{API}/compras", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, headers, **datos):
    r = client.post(f"{API}/ventas", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def abonar_compra(client, headers, compra_id, **datos):
    r = client.post(f"{API}/compras/{compra_id}/abonos", json=datos, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def saldo_anterior(client, headers, **datos):
    r = client.post(f"{API}/saldos-anteriores", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def cuenta_productor(client, headers, productor, **params):
    r = client.get(
        f"{API}/estado-cuenta-productor",
        params={"productor": productor, **params},
        headers=headers,
    )
    return r


def pdf_productor(client, headers, productor, **params):
    return client.get(
        f"{API}/estado-cuenta-productor/pdf",
        params={"productor": productor, **params},
        headers=headers,
    )


def texto_pdf(contenido: bytes) -> str:
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def claves_y_valores(dato, claves=None, valores=None):
    """Recorre recursivamente dicts y listas y devuelve (claves, valores str).

    Igual que en las pruebas del documento del cliente: no basta revisar el
    primer nivel, un dato del lado de la venta podría venir escondido dentro de
    una compra, de un pago o de un saldo del libro anterior.
    """
    claves = [] if claves is None else claves
    valores = [] if valores is None else valores
    if isinstance(dato, dict):
        for clave, valor in dato.items():
            claves.append(str(clave))
            claves_y_valores(valor, claves, valores)
    elif isinstance(dato, list):
        for elemento in dato:
            claves_y_valores(elemento, claves, valores)
    elif dato is not None:
        valores.append(str(dato))
    return claves, valores


def sembrar_compras(client, headers, productor="Sebastián Ruiz"):
    """Dos compras de julio con abonos parciales y borona en la primera.

    14.400.000 + 5.250.000 = 19.650.000 comprados, 13.100.000 pagados.
    """
    primera = compra(
        client, headers, fecha="2026-07-02", productor=productor,
        kilos_brutos="800", borona_kilos="56.7", precio_kilo="18000",
    )
    segunda = compra(
        client, headers, fecha="2026-07-18", productor=productor,
        kilos_brutos="300", precio_kilo="17500",
    )
    # Los abonos se registran en desorden a propósito: el documento los ordena
    abonar_compra(client, headers, segunda["id"], fecha="2026-07-20", valor="1000000")
    abonar_compra(client, headers, primera["id"], fecha="2026-07-12", valor="12100000")
    return primera, segunda


# ---------------------------------------------------------------------------
# 1. El caso normal: varias compras, abonos y el desglose que tiene que cuadrar
# ---------------------------------------------------------------------------
def test_caso_normal_el_desglose_suma_el_saldo(client, base_datos):
    """(total_comprado - total_pagado) + libro_anterior_saldo == saldo.

    Y OJO CON EL SIGNO, al contrario del documento del cliente: aquí un saldo
    positivo significa que LA QUESERA LE DEBE AL PRODUCTOR.
    """
    h = auth_headers(client, "admin.a")
    sembrar_compras(client, h)
    saldo_anterior(
        client, h, tipo="pagar", tercero="Sebastián Ruiz", fecha="2025-11-03",
        concepto="Queso recibido en el libro viejo", valor_total="3000000",
        abonado="1000000",
    )

    r = cuenta_productor(client, h, "sebastián ruiz ")  # escrito distinto: es el mismo
    assert r.status_code == 200, r.text
    d = r.json()

    print("\n===== 1. CASO NORMAL =====")
    print(f"  productor={d['productor']!r} compras={d['compras']} kilos={d['total_kilos']}")
    print(f"  comprado={d['total_comprado']} pagado={d['total_pagado']}")
    print(f"  libro: total={d['libro_anterior_total']} abonado={d['libro_anterior_abonado']}"
          f" saldo={d['libro_anterior_saldo']}")
    izq = D(d["total_comprado"]) - D(d["total_pagado"]) + D(d["libro_anterior_saldo"])
    print(f"  ({d['total_comprado']} - {d['total_pagado']}) + {d['libro_anterior_saldo']}"
          f" = {izq}   saldo impreso = {d['saldo']}")
    assert izq == D(d["saldo"]), "el desglose NO suma la cifra grande"

    assert d["compras"] == 2
    assert len(d["compras_detalle"]) == 2
    assert D(d["total_kilos"]) == 1100  # 800 + 300 netos (la borona no se paga)
    assert D(d["total_comprado"]) == 19_650_000
    assert D(d["total_pagado"]) == 13_100_000
    assert D(d["saldo"]) == 8_550_000  # 6.550.000 del sistema + 2.000.000 del libro

    # El detalle de compras suma los totales, fila por fila
    assert sum(D(c["valor_total"]) for c in d["compras_detalle"]) == D(d["total_comprado"])
    assert sum(D(c["abonado"]) for c in d["compras_detalle"]) == D(d["total_pagado"])
    assert sum(D(c["kilos"]) for c in d["compras_detalle"]) == D(d["total_kilos"])
    # Y los pagos son TODOS los abonos de TODAS sus compras, juntos y ordenados
    assert [p["fecha"] for p in d["pagos"]] == ["2026-07-12", "2026-07-20"]
    assert sum(D(p["valor"]) for p in d["pagos"]) == D(d["total_pagado"])
    # Del abono solo salen fecha y valor (sus observaciones son nota interna)
    assert set(d["pagos"][0]) == {"fecha", "valor"}

    # La borona vino con el lote pero no se paga: va en su campo, sin sumar kilos
    assert D(d["compras_detalle"][0]["borona_kilos"]) == D("56.7")
    assert D(d["compras_detalle"][1]["borona_kilos"]) == 0
    assert d["compras_detalle"][0]["estado"] == "parcial"

    # Compras ordenadas por fecha ascendente y el nombre devuelto es el GUARDADO
    assert [c["fecha"] for c in d["compras_detalle"]] == ["2026-07-02", "2026-07-18"]
    assert d["productor"] == "Sebastián Ruiz"
    assert d["desde"] is None and d["hasta"] is None  # sin rango: todo el histórico
    assert d["emitido"]


# ---------------------------------------------------------------------------
# 2. Solo deuda vieja del libro y ninguna compra: se genera igual
# ---------------------------------------------------------------------------
def test_solo_deuda_del_libro_sin_compras(client, base_datos):
    """El productor que viene del sistema anterior y todavía no le ha vendido
    nada aquí SÍ tiene estado de cuenta: es justo su caso."""
    h = auth_headers(client, "admin.a")
    saldo_anterior(
        client, h, tipo="pagar", tercero="Marta Solo Libro", fecha="2025-12-01",
        concepto="Queso de diciembre pendiente", valor_total="500000", abonado="200000",
    )

    r = cuenta_productor(client, h, "Marta Solo Libro")
    assert r.status_code == 200, r.text  # NO 404
    d = r.json()
    print("\n===== 2. SOLO DEUDA DEL LIBRO =====")
    print(f"  compras={d['compras']} comprado={d['total_comprado']} pagado={d['total_pagado']}"
          f" libro_saldo={d['libro_anterior_saldo']} saldo={d['saldo']}")
    assert d["compras"] == 0 and d["compras_detalle"] == [] and d["pagos"] == []
    assert D(d["total_comprado"]) == 0 and D(d["total_pagado"]) == 0
    assert D(d["libro_anterior_total"]) == 500_000
    assert D(d["libro_anterior_abonado"]) == 200_000
    assert D(d["saldo"]) == 300_000
    izq = D(d["total_comprado"]) - D(d["total_pagado"]) + D(d["libro_anterior_saldo"])
    assert izq == D(d["saldo"])
    # El nombre devuelto es el del saldo guardado
    assert d["productor"] == "Marta Solo Libro"

    # Y el PDF sale, con su sección del libro anterior y sin negar el abono
    rp = pdf_productor(client, h, "Marta Solo Libro")
    assert rp.status_code == 200, rp.text
    impreso = texto_pdf(rp.content)
    print(f"  PDF {len(rp.content)} bytes")
    assert "ESTADO DE CUENTA DEL PRODUCTOR" in impreso
    assert "Sin compras registradas" in impreso
    assert "Saldos de la cuenta anterior" in impreso
    # No se le puede decir "sin pagos" a secas: su abono del libro sí existe
    assert "Sin pagos registrados" not in impreso
    assert "$300.000" in impreso and "$200.000" in impreso


# ---------------------------------------------------------------------------
# 3. Un productor inexistente: 404 por las dos puertas
# ---------------------------------------------------------------------------
def test_productor_inexistente_da_404(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar_compras(client, h)

    r = cuenta_productor(client, h, "Nadie Existe")
    print("\n===== 3. PRODUCTOR INEXISTENTE =====")
    print(f"  JSON: {r.status_code} {r.json()['error']['detail']}")
    assert r.status_code == 404
    assert "compras" in r.json()["error"]["detail"].lower()

    rp = pdf_productor(client, h, "Nadie Existe")
    print(f"  PDF: {rp.status_code}")
    assert rp.status_code == 404
    assert not rp.content.startswith(b"%PDF")


def test_rango_vacio_tiene_su_propio_404(client, base_datos):
    """Si tiene compras pero el RANGO pedido queda vacío, el mensaje lo dice: no
    se le puede decir al usuario que nunca le compró."""
    h = auth_headers(client, "admin.a")
    sembrar_compras(client, h)

    r = cuenta_productor(client, h, "Sebastián Ruiz", desde="2026-09-01", hasta="2026-09-30")
    detalle = r.json()["error"]["detail"]
    print("\n===== 3b. RANGO VACÍO =====")
    print(f"  {r.status_code}: {detalle}")
    assert r.status_code == 404
    assert "período consultado" in detalle

    # Con el rango de julio sí sale, y baja lo que quede afuera
    julio = cuenta_productor(
        client, h, "Sebastián Ruiz", desde="2026-07-01", hasta="2026-07-10"
    ).json()
    print(f"  julio 1-10: compras={julio['compras']} comprado={julio['total_comprado']}"
          f" pagado={julio['total_pagado']} saldo={julio['saldo']}")
    assert julio["compras"] == 1
    assert D(julio["total_comprado"]) == 14_400_000
    # Los abonos que salen son los de las compras del rango (los 12.100.000)
    assert D(julio["total_pagado"]) == 12_100_000
    assert D(julio["saldo"]) == 2_300_000
    assert julio["desde"] == "2026-07-01" and julio["hasta"] == "2026-07-10"


# ---------------------------------------------------------------------------
# 4. LA PRUEBA MÁS IMPORTANTE: confidencialidad al revés
# ---------------------------------------------------------------------------
def test_confidencialidad_no_se_filtra_nada_de_ventas(client, base_datos):
    """ESTA PRUEBA ES LA BARRERA QUE IMPIDE MOSTRARLE AL PRODUCTOR A CÓMO SE
    REVENDE SU QUESO.

    El documento lo lee ÉL. Si se filtrara el precio de venta, el total vendido,
    el margen, la ganancia, el flete o el nombre de un cliente, el productor
    quedaría sabiendo exactamente cuánto le gana la quesera a cada kilo que le
    compra —y a quién se lo vende—. Nada del lado de la venta puede salir por
    este endpoint, a ningún nivel del árbol de la respuesta.
    """
    h = auth_headers(client, "admin.a")
    sembrar_compras(client, h)
    # Ventas REALES cargadas, para que haya algo que filtrar: 400 kg a $19.500
    # con flete de $300/kg (gasto interno de $120.000), y borona a $8.000
    venta(client, h, fecha="2026-07-10", cliente="Alba Nieto", kilos="400",
          precio_kilo="19500", gasto_concepto="Flete a Villavicencio",
          gasto_por_kilo="300", pagada_de_contado=True)
    venta(client, h, fecha="2026-07-14", cliente="Depósito La Ganancia", tipo="borona",
          kilos="50", precio_kilo="8000")
    saldo_anterior(
        client, h, tipo="pagar", tercero="Sebastián Ruiz", fecha="2025-11-03",
        concepto="Queso recibido en el libro viejo", valor_total="3000000",
        abonado="1000000", observaciones="INTERNO: margen de 1.500 por kilo",
    )

    r = cuenta_productor(client, h, "Sebastián Ruiz")
    assert r.status_code == 200, r.text
    d = r.json()
    claves, valores = claves_y_valores(d)
    # El recorrido tiene que haber bajado hasta las compras, los pagos y el libro
    assert "precio_kilo" in claves and "borona_kilos" in claves and "concepto" in claves

    print("\n===== 4. CONFIDENCIALIDAD (la barrera) =====")
    print(f"  claves recorridas: {len(set(claves))}  valores de texto: {len(valores)}")

    # Ninguna clave, a ningún nivel, habla del lado de la venta
    prohibidas = ("venta", "cliente", "margen", "ganancia", "gasto", "venta_libre",
                  "precio_promedio", "valor_realizado", "utilidad", "flete")
    for clave in claves:
        for palabra in prohibidas:
            assert palabra not in clave.lower(), f"clave del lado de la venta filtrada: {clave}"
    # Ni ningún valor de texto
    for valor in valores:
        for palabra in prohibidas:
            assert palabra not in valor.lower(), f"valor del lado de la venta filtrado: {valor}"

    # Ni las cifras: el precio de reventa, lo vendido, el flete, la "venta libre"
    # (7.800.000 - 120.000) ni el nombre de ningún cliente. Solo cifras que no
    # puedan aparecer por casualidad dentro de otra (nada de "300" ni "8000", que
    # son pedazos de los kilos y del precio de compra, que sí son suyos).
    texto = " | ".join(claves + valores)
    for secreto in ("19500", "19.500", "7800000", "7.800.000", "120000", "120.000",
                    "7680000", "7.680.000", "Alba", "Nieto", "Depósito",
                    "Villavicencio", "INTERNO", "1.500"):
        assert secreto not in texto, f"dato del lado de la venta filtrado: {secreto}"

    # Lo que SÍ debe estar: lo suyo
    assert D(d["total_comprado"]) == 19_650_000
    assert D(d["saldo"]) == 8_550_000

    # Y tampoco por la puerta del PDF, que es el que se le entrega en la mano
    rp = pdf_productor(client, h, "Sebastián Ruiz")
    assert rp.status_code == 200, rp.text
    impreso = texto_pdf(rp.content)
    # El extractor sí está leyendo (si no, lo de abajo no probaría nada)
    assert "Sebastián Ruiz" in impreso and "Detalle de compras" in impreso
    for secreto in ("$19.500", "$7.800.000", "$120.000", "$7.680.000", "Alba",
                    "Nieto", "Villavicencio", "Flete", "margen", "Margen",
                    "ganancia", "Ganancia", "INTERNO"):
        assert secreto not in impreso, f"dato del lado de la venta impreso en el PDF: {secreto}"
    # Y no se disfraza de factura: la decisión es que es un documento INTERNO
    # para cuadrar cuentas, sin numeración consecutiva, sin resolución de la DIAN
    # y sin IVA. Lo único que dice de facturas es que NO lo es.
    for fiscal in ("FACTURA", "Factura", "DIAN", "IVA", "Resolución", "N.º"):
        assert fiscal not in impreso, f"el documento parece una factura fiscal: {fiscal}"
    assert "no es una factura" in impreso
    print("  el PDF no imprime nada del lado de la venta ni parece una factura")


# ---------------------------------------------------------------------------
# 5. El libro anterior no se cruza de lado, ni con el mismo nombre
# ---------------------------------------------------------------------------
def test_saldo_cobrar_del_homonimo_no_se_cuela(client, base_datos):
    """Un saldo de tipo 'cobrar' es una deuda de un CLIENTE con la quesera. Si se
    colara en el estado de cuenta del productor, se le estaría cobrando a él una
    plata que debe otra persona (y al revés, se le mostraría al cliente una deuda
    de la quesera). Ni siquiera con el MISMO NOMBRE."""
    h = auth_headers(client, "admin.a")
    sembrar_compras(client, h)
    # Mismo nombre, los dos lados del libro
    saldo_anterior(
        client, h, tipo="pagar", tercero="Sebastián Ruiz", fecha="2025-11-03",
        concepto="Queso recibido en el libro viejo", valor_total="3000000",
        abonado="1000000",
    )
    saldo_anterior(
        client, h, tipo="cobrar", tercero="Sebastián Ruiz", fecha="2025-10-01",
        concepto="Factura 045 del homónimo", valor_total="9999999",
    )

    d = cuenta_productor(client, h, "Sebastián Ruiz").json()
    print("\n===== 5. HOMÓNIMO EN LOS DOS LADOS DEL LIBRO =====")
    print(f"  productor: filas_libro={len(d['saldos_anteriores'])}"
          f" libro_total={d['libro_anterior_total']} saldo={d['saldo']}")
    # Solo entra el de tipo 'pagar'
    assert len(d["saldos_anteriores"]) == 1
    assert D(d["libro_anterior_total"]) == 3_000_000
    assert D(d["libro_anterior_saldo"]) == 2_000_000
    assert D(d["saldo"]) == 8_550_000
    texto = " | ".join(sum(claves_y_valores(d), []))
    for ajeno in ("9999999", "9.999.999", "045", "homónimo"):
        assert ajeno not in texto, f"deuda de un cliente colada en el documento: {ajeno}"

    # Y al revés: el estado de cuenta del CLIENTE con ese mismo nombre solo trae
    # el de 'cobrar', nunca la deuda que la quesera tiene con el productor
    c = client.get(
        f"{API}/estado-cuenta", params={"cliente": "Sebastián Ruiz"}, headers=h
    ).json()
    print(f"  cliente:   filas_libro={len(c['saldos_anteriores'])}"
          f" libro_total={c['libro_anterior_total']} saldo={c['saldo']}")
    assert len(c["saldos_anteriores"]) == 1
    assert D(c["libro_anterior_total"]) == 9_999_999
    texto_cliente = " | ".join(sum(claves_y_valores(c), []))
    for ajeno in ("3000000", "3.000.000", "libro viejo"):
        assert ajeno not in texto_cliente, f"deuda con el productor colada al cliente: {ajeno}"


# ---------------------------------------------------------------------------
# 6. Lo anulado y lo borrado no es plata que se le deba
# ---------------------------------------------------------------------------
def test_anuladas_y_borradas_fuera_de_los_totales(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar_compras(client, h)
    # Una compra anulada (200 kg × $20.000 = $4.000.000)
    anulada = compra(client, h, fecha="2026-07-22", productor="Sebastián Ruiz",
                     kilos_brutos="200", precio_kilo="20000")
    r = client.post(f"{API}/compras/{anulada['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    # Y una borrada (111 kg × $19.000 = $2.109.000). Las cifras se eligen para
    # que no sean pedazo de ninguna otra del documento: "2.100.000" habría dado un
    # falso positivo dentro de los "12.100.000" que sí están abonados.
    borrada = compra(client, h, fecha="2026-07-23", productor="Sebastián Ruiz",
                     kilos_brutos="111", precio_kilo="19000")
    r = client.delete(f"{API}/compras/{borrada['id']}", headers=h)
    assert r.status_code == 204, r.text

    d = cuenta_productor(client, h, "Sebastián Ruiz").json()
    print("\n===== 6. ANULADAS Y BORRADAS =====")
    print(f"  compras={d['compras']} comprado={d['total_comprado']}"
          f" kilos={d['total_kilos']} saldo={d['saldo']}")
    assert d["compras"] == 2, "una compra anulada o borrada entró al documento"
    assert D(d["total_comprado"]) == 19_650_000
    assert D(d["total_kilos"]) == 1100
    assert D(d["saldo"]) == 6_550_000
    fechas = [c["fecha"] for c in d["compras_detalle"]]
    assert "2026-07-22" not in fechas and "2026-07-23" not in fechas
    texto = " | ".join(sum(claves_y_valores(d), []))
    for fantasma in ("4000000", "4.000.000", "2109000", "2.109.000", "111"):
        assert fantasma not in texto, f"plata anulada o borrada en el documento: {fantasma}"


# ---------------------------------------------------------------------------
# 7. Multi-tenant: la empresa B no ve al productor de la empresa A
# ---------------------------------------------------------------------------
def test_no_cruza_empresas(client, base_datos):
    """Un estado de cuenta cruzado le entregaría a otra quesera lo que se le debe
    a un productor que no es suyo."""
    ha = auth_headers(client, "admin.a")
    sembrar_compras(client, ha)
    assert D(cuenta_productor(client, ha, "Sebastián Ruiz").json()["saldo"]) == 6_550_000

    hb = auth_headers(client, "admin.b")
    r = cuenta_productor(client, hb, "Sebastián Ruiz")
    rp = pdf_productor(client, hb, "Sebastián Ruiz")
    print("\n===== 7. AISLAMIENTO POR EMPRESA =====")
    print(f"  empresa B: JSON={r.status_code} PDF={rp.status_code}")
    assert r.status_code == 404, r.text
    assert rp.status_code == 404, rp.text
    assert not rp.content.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# 8. El PDF: cifras que suman, moneda colombiana y nombre de archivo saneado
# ---------------------------------------------------------------------------
def test_pdf_cuadra_y_rotula_el_signo(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar_compras(client, h)
    saldo_anterior(
        client, h, tipo="pagar", tercero="Sebastián Ruiz", fecha="2025-11-03",
        concepto="Queso recibido en el libro viejo", valor_total="3000000",
        abonado="1000000",
    )

    r = pdf_productor(client, h, "Sebastián Ruiz")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    # El nombre del archivo lleva el del productor, saneado (nada de comillas ni
    # saltos de línea: sería inyección de cabecera HTTP)
    disposition = r.headers["content-disposition"]
    print("\n===== 8. PDF =====")
    print(f"  {len(r.content)} bytes · {disposition}")
    assert 'filename="estado_cuenta_productor_Sebastian_Ruiz.pdf"' in disposition

    impreso = texto_pdf(r.content)
    assert "ESTADO DE CUENTA DEL PRODUCTOR" in impreso
    assert "Sebastián Ruiz" in impreso
    assert "Todo el histórico" in impreso
    # Los renglones del resumen, con sus operadores, y la cifra grande
    for pedazo in ("Total comprado", "(-) Total pagado",
                   "(+) Saldo de la cuenta anterior", "SALDO A FAVOR DEL PRODUCTOR"):
        assert pedazo in impreso, f"falta el renglón {pedazo!r} en el resumen"
    # Moneda colombiana: punto de miles. 19.650.000 - 13.100.000 + 2.000.000
    for cifra in ("$19.650.000", "$13.100.000", "$2.000.000", "$8.550.000"):
        assert cifra in impreso, f"falta la cifra {cifra} impresa"
    # La borona se dice pero no lleva columna (no se ensancha la tabla)
    assert "56,7 kg" in impreso and "borona" in impreso
    assert "Borona" not in impreso.split("Detalle de compras")[1].split("Saldos")[0]
    # Con saldo a favor suyo, no "Al día"
    assert "Con saldo a favor suyo" in impreso


def test_pdf_al_dia_cuando_no_se_le_debe_nada(client, base_datos):
    """Pagada completa: el documento dice "Al día" y el saldo va en cero."""
    h = auth_headers(client, "admin.a")
    una = compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
                 kilos_brutos="100", precio_kilo="18000")
    abonar_compra(client, h, una["id"], fecha="2026-07-03", valor="1800000")

    d = cuenta_productor(client, h, "Sebastián Ruiz").json()
    print("\n===== 9. AL DÍA =====")
    print(f"  comprado={d['total_comprado']} pagado={d['total_pagado']} saldo={d['saldo']}")
    assert D(d["saldo"]) == 0
    impreso = texto_pdf(pdf_productor(client, h, "Sebastián Ruiz").content)
    assert "Al día" in impreso
    assert "SALDO A FAVOR DEL PRODUCTOR" in impreso and "$0" in impreso


def test_el_pdf_exige_permiso_de_imprimir(client, base_datos, db_session):
    """El JSON pide 'reventa:consultar' y el PDF pide 'reventa:imprimir', igual
    que en el del cliente: quien solo puede mirar no puede sacar el documento que
    se le entrega al productor."""
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.modules.usuarios.models import Rol, Usuario
    from tests.conftest import PASSWORD

    h = auth_headers(client, "admin.a")
    sembrar_compras(client, h)

    # El rol "Consulta" tiene reventa:consultar y NO tiene reventa:imprimir
    rol = db_session.scalars(select(Rol).where(Rol.nombre == "Consulta")).one()
    mirona = Usuario(
        nombre="Solo", apellido="Mira", correo="solo.mira@test.local",
        username="solo.mira", hashed_password=hash_password(PASSWORD),
        empresa_id=base_datos["empresa_a"].id,
    )
    mirona.roles = [rol]
    db_session.add(mirona)
    db_session.commit()

    hm = auth_headers(client, "solo.mira")
    r_json = cuenta_productor(client, hm, "Sebastián Ruiz")
    r_pdf = pdf_productor(client, hm, "Sebastián Ruiz")
    print("\n===== 10. PERMISOS =====")
    print(f"  solo con 'consultar': JSON={r_json.status_code} PDF={r_pdf.status_code}")
    assert r_json.status_code == 200, r_json.text
    assert r_pdf.status_code == 403, r_pdf.text
    assert not r_pdf.content.startswith(b"%PDF")


def test_pdf_saldo_negativo_se_rotula_al_reves():
    """Si se le pagó de más, el documento NO puede decirle que se le debe.

    Se llama al generador directo porque por la API no se puede llegar a un saldo
    negativo con un productor (CompraQuesoService no deja dejar el total por
    debajo de lo ya abonado), pero el dato puede venir así de datos migrados y el
    rótulo tiene que aguantarlo sin leerse invertido.
    """
    from datetime import date

    from app.utils.export import build_estado_cuenta_productor_pdf

    contenido = build_estado_cuenta_productor_pdf(
        empresa_nombre="Quesera A", empresa_nit="900A", empresa_ubicacion="Bogotá, Cund.",
        productor="Sebastián Ruiz", emitido="29/07/2026", periodo="Todo el histórico",
        compras=1,
        compras_detalle=[{
            "fecha": date(2026, 7, 2), "kilos": Decimal("100"),
            "borona_kilos": Decimal("0"), "precio_kilo": Decimal("14000"),
            "valor_total": Decimal("1400000"), "abonado": Decimal("1950000"),
            "saldo": Decimal("-550000"),
        }],
        pagos=[{"fecha": date(2026, 7, 3), "valor": Decimal("1950000")}],
        total_kilos=Decimal("100"), total_comprado=Decimal("1400000"),
        total_pagado=Decimal("1950000"), saldo=Decimal("-550000"),
    )
    impreso = texto_pdf(contenido)
    print("\n===== 10. SE LE PAGÓ DE MÁS =====")
    print(f"  {len(contenido)} bytes")
    assert "Se le pagó de más" in impreso
    assert "PAGADO DE MÁS" in impreso
    assert "SALDO A FAVOR DEL PRODUCTOR" not in impreso
    # La cifra destacada va en positivo y la operación queda escrita con su signo
    assert "$550.000" in impreso
    assert "-$550.000" in impreso


# ---------------------------------------------------------------------------
# 11. Texto libre con '<': el nombre del productor y el concepto del libro
# ---------------------------------------------------------------------------
def test_nombre_con_menor_que_no_tumba_el_pdf(client, base_datos):
    """ReportLab interpreta mini-XML dentro de un Paragraph.

    En el documento del CLIENTE esto ya pasó: un nombre como
    "Ana <onDraw name='x'/> & Cía" dejaba el endpoint del PDF respondiendo 500 de
    forma PERMANENTE, y un "<El Bueno>" se imprimía borrado y en silencio, que es
    peor todavía: el productor recibe un documento con el nombre mutilado y no hay
    forma de saber que faltó algo. Aquí se prueban las DOS puertas por las que
    entra texto libre a este documento: el nombre del productor y el concepto del
    saldo del libro anterior.
    """
    h = auth_headers(client, "admin.a")
    nombre = "Lácteos <El Bueno> & Hnos"
    sembrar_compras(client, h, productor=nombre)
    saldo_anterior(
        client, h, tipo="pagar", tercero=nombre, fecha="2025-11-03",
        concepto="Queso <sin pesar> & saldo del 2025", valor_total="3000000",
        abonado="1000000",
    )

    r = pdf_productor(client, h, nombre)
    print("\n===== 11. TEXTO LIBRE CON '<' =====")
    print(f"  PDF={r.status_code} bytes={len(r.content)}")
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")
    impreso = texto_pdf(r.content)
    # El nombre entero, no un pedazo: ni "Lácteos & Hnos" ni "Lácteos"
    for pedazo in ("Lácteos", "<El Bueno>", "&", "Hnos"):
        assert pedazo in impreso, f"el PDF se comió parte del nombre: {pedazo}"
    # Y el concepto del libro anterior tampoco se muta
    for pedazo in ("Queso", "<sin pesar>", "saldo del 2025"):
        assert pedazo in impreso, f"el PDF se comió parte del concepto: {pedazo}"
    # Las cifras siguen cuadrando con el nombre raro (19.650.000 - 13.100.000 + 2.000.000)
    assert "$8.550.000" in impreso
    print("  el nombre y el concepto salen completos y el saldo cuadra")


def test_nombre_con_etiqueta_ondraw_no_deja_el_endpoint_caido():
    """El caso exacto que reventó el del cliente, contra el generador directo.

    Se llama al generador y no a la API porque lo que se prueba es que ninguna de
    las cadenas que entran a un Paragraph pase sin escapar: nombre de la empresa,
    ubicación, nombre del productor, período y concepto del libro. Si alguna se
    quedara cruda, esto lanzaría "Missing onDraw callback attribute".
    """
    from datetime import date

    from app.utils.export import build_estado_cuenta_productor_pdf

    veneno = "<onDraw name='x'/>"
    contenido = build_estado_cuenta_productor_pdf(
        empresa_nombre=f"Quesera {veneno}", empresa_nit=f"900{veneno}",
        empresa_ubicacion=f"Bogotá {veneno}", productor=f"Sebastián {veneno}",
        emitido="29/07/2026", periodo=f"Julio {veneno}", compras=1,
        compras_detalle=[{
            "fecha": date(2026, 7, 2), "kilos": Decimal("100"),
            "borona_kilos": Decimal("5"), "precio_kilo": Decimal("14000"),
            "valor_total": Decimal("1400000"), "abonado": Decimal("400000"),
            "saldo": Decimal("1000000"),
        }],
        pagos=[{"fecha": date(2026, 7, 3), "valor": Decimal("400000")}],
        total_kilos=Decimal("100"), total_comprado=Decimal("1400000"),
        total_pagado=Decimal("400000"), saldo=Decimal("1000000"),
        saldos_anteriores=[{
            "fecha": date(2025, 11, 3), "concepto": f"Libro {veneno}",
            "valor_total": Decimal("0"), "abonado": Decimal("0"),
            "saldo": Decimal("0"),
        }],
    )
    impreso = texto_pdf(contenido)
    print("\n===== 11b. onDraw EN TODAS LAS PUERTAS =====")
    print(f"  {len(contenido)} bytes, no lanzó")
    assert contenido.startswith(b"%PDF")
    # La etiqueta se imprime como texto, que es lo correcto: es lo que el usuario
    # escribió. Lo que no puede es ejecutarse ni borrar lo que tiene al lado.
    # Seis puertas de texto libre: nombre de la empresa, NIT, ubicación, nombre
    # del productor, período y concepto del libro. Las seis tienen que salir, y
    # salir intactas.
    assert impreso.count("onDraw") == 6, impreso
    for vecino in ("Quesera", "Bogotá", "Sebastián", "Julio", "Libro"):
        assert vecino in impreso, f"el veneno se comió el texto vecino: {vecino}"
