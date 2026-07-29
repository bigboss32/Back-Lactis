"""AUDITORÍA DE PRIVACIDAD del estado de cuenta DEL PRODUCTOR.

Prueba adversarial e independiente: siembra datos con "canarios" (valores y
palabras únicas del lado de la venta y de las notas internas), genera el PDF
REAL y lo revisa por tres caminos:
  1. texto extraído con pypdf,
  2. TODOS los objetos del PDF, descomprimiendo los streams con zlib,
  3. metadatos (title/author/subject/producer) y nombre del archivo.

Y al revés: comprueba que el documento del CLIENTE sigue sin nada del lado del
productor.
"""
import base64
import io
import re
import zlib
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/reventa"
SALIDA = Path(__file__).resolve().parent.parent / "uploads" / "auditoria_privacidad"

# --------------------------------------------------------------- canarios
PRODUCTOR = "Sebastián Ruiz"
CLIENTE = "Carlos Ricaute"
PRECIO_COMPRA = 11317          # lo suyo: SÍ puede salir
PRECIO_VENTA = 23457           # PROHIBIDO en el documento del productor
GASTO_KILO = 317               # flete: PROHIBIDO
OBS_COMPRA = "NOTAINTERNACOMPRA lo revendo a Ricaute a 23457"
OBS_ABONO = "NOTAINTERNAABONO le rebaje el flete de 250000"
OBS_SALDO_PAGAR = "NOTAINTERNASALDO cuadre a ojo del libro viejo"
CONCEPTO_PAGAR = "Compra vieja del libro 044"
CONCEPTO_COBRAR = "CANARIOCOBRAR venta vieja al cliente"
VALOR_COBRAR = 777777          # PROHIBIDO: deuda de CLIENTE


def _pdf_streams(contenido: bytes) -> str:
    """Todo el contenido de los streams del PDF, DESCOMPRIMIDO.

    Los streams de reportlab van con /Filter [ /ASCII85Decode /FlateDecode ]: hay
    que deshacer el ASCII85 y después el zlib, o buscar la palabra prohibida en
    los bytes crudos no prueba nada (que es justo el error fácil aquí). Se
    intentan las dos combinaciones y, de últimas, los bytes tal cual.

    Además se 'desescapan' los octales de las cadenas PDF (\\351 = é) para poder
    buscar texto con acentos.
    """
    partes: list[str] = []
    for bruto in re.findall(rb"stream\r?\n(.*?)endstream", contenido, re.S):
        limpio = bruto.strip(b"\r\n")
        datos = None
        for descomprimir in (
            lambda b: zlib.decompress(base64.a85decode(b, adobe=True)),
            lambda b: zlib.decompress(b),
            lambda b: base64.a85decode(b, adobe=True),
        ):
            try:
                datos = descomprimir(limpio)
                break
            except Exception:  # noqa: BLE001 - se prueba el siguiente filtro
                continue
        partes.append((datos if datos is not None else limpio).decode("latin-1"))
    texto = "\n".join(partes)
    # \351 -> é, etc.: reportlab escapa los no-ASCII en octal dentro de los Tj.
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), texto)


def _verificar_que_el_escaner_sirve(streams: str, presentes: list[str]) -> None:
    """El escáner de streams TIENE QUE tener dientes: si la descompresión falla en
    silencio, todas las aserciones de 'no aparece' pasarían vacías. Se comprueba
    con cosas que SÍ están en el documento."""
    for esperado in presentes:
        assert esperado in streams, (
            f"El escáner de streams no sirve: no encontró {esperado!r}, que SÍ está "
            "en el documento. Las demás aserciones serían vacías."
        )


def _texto(contenido: bytes) -> str:
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def _sembrar(client, h):
    """Compras, abonos, ventas y los DOS saldos del libro con el MISMO nombre."""
    c1 = client.post(
        f"{API}/compras",
        json={
            "fecha": "2026-03-02",
            "productor": PRODUCTOR,
            "kilos_brutos": 120,
            "borona_kilos": 7,
            "precio_kilo": PRECIO_COMPRA,
            "observaciones": OBS_COMPRA,
        },
        headers=h,
    )
    assert c1.status_code == 201, c1.text
    c1 = c1.json()
    c2 = client.post(
        f"{API}/compras",
        json={
            "fecha": "2026-03-19",
            "productor": "  sebastián   ruiz ",  # escrito distinto a propósito
            "kilos_brutos": 80,
            "borona_kilos": 0,
            "precio_kilo": PRECIO_COMPRA,
            "observaciones": OBS_COMPRA,
        },
        headers=h,
    )
    assert c2.status_code == 201, c2.text
    c2 = c2.json()
    r = client.post(
        f"{API}/compras/{c1['id']}/abonos",
        json={"fecha": "2026-03-10", "valor": 500000, "observaciones": OBS_ABONO},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # Ventas cargadas: es lo que tiene que poder filtrarse y no debe aparecer.
    v = client.post(
        f"{API}/ventas",
        json={
            "fecha": "2026-03-05",
            "cliente": CLIENTE,
            "tipo": "queso",
            "kilos": 60,
            "precio_kilo": PRECIO_VENTA,
            "gasto_concepto": "Flete CANARIOFLETE",
            "gasto_por_kilo": GASTO_KILO,
            "observaciones": "NOTAINTERNAVENTA margen gordo",
        },
        headers=h,
    )
    assert v.status_code == 201, v.text
    v = v.json()
    r = client.post(
        f"{API}/ventas/{v['id']}/abonos",
        json={"fecha": "2026-03-06", "valor": 300000, "observaciones": "NOTAINTERNAABONOVENTA"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # El libro anterior, con el MISMO nombre en los dos tipos: es la trampa.
    sp = client.post(
        f"{API}/saldos-anteriores",
        json={
            "tipo": "pagar",
            "tercero": PRODUCTOR,
            "fecha": "2025-11-04",
            "concepto": CONCEPTO_PAGAR,
            "valor_total": 400000,
            "abonado": 100000,
            "observaciones": OBS_SALDO_PAGAR,
        },
        headers=h,
    )
    assert sp.status_code == 201, sp.text
    sc = client.post(
        f"{API}/saldos-anteriores",
        json={
            "tipo": "cobrar",
            "tercero": PRODUCTOR,  # un cliente que se llama IGUAL que el productor
            "fecha": "2025-10-01",
            "concepto": CONCEPTO_COBRAR,
            "valor_total": VALOR_COBRAR,
            "abonado": 0,
            "observaciones": "NOTAINTERNACOBRAR",
        },
        headers=h,
    )
    assert sc.status_code == 201, sc.text
    return {"c1": c1, "c2": c2, "venta": v, "saldo_pagar": sp.json(), "saldo_cobrar": sc.json()}


PROHIBIDAS = [
    # palabras
    "NOTAINTERNACOMPRA", "NOTAINTERNAABONO", "NOTAINTERNASALDO", "NOTAINTERNAVENTA",
    "NOTAINTERNAABONOVENTA", "NOTAINTERNACOBRAR", "CANARIOFLETE", "CANARIOCOBRAR",
    "Ricaute", "Carlos", "flete", "Flete", "margen", "Margen", "ganancia", "Ganancia",
    "IVA", "Resolución", "consecutivo",
    # cifras del lado de la venta / del libro 'cobrar'
    "23.457", "23457", "1.407.420", "1407420", "19.020", "19020", "777.777", "777777",
]


def test_auditoria_privacidad_estado_cuenta_productor(client, base_datos):
    h = auth_headers(client, "admin.a")
    datos = _sembrar(client, h)
    SALIDA.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- JSON
    r = client.get(
        f"{API}/estado-cuenta-productor", params={"productor": PRODUCTOR}, headers=h
    )
    assert r.status_code == 200, r.text
    ec = r.json()
    print("\n=== JSON estado-cuenta-productor ===")
    import json as _json
    print(_json.dumps(ec, indent=2, ensure_ascii=False))

    crudo = _json.dumps(ec, ensure_ascii=False)
    for palabra in PROHIBIDAS:
        assert palabra not in crudo, f"FUGA en el JSON: {palabra!r}"
    assert "observaciones" not in crudo, "FUGA: observaciones en el JSON"
    assert CONCEPTO_COBRAR not in crudo
    assert ec["saldos_anteriores"] and all(
        s["concepto"] == CONCEPTO_PAGAR for s in ec["saldos_anteriores"]
    ), "el libro 'cobrar' del homónimo entró al documento del productor"

    # ------------------------------------------------- el desglose cuadra
    tc, tp = Decimal(ec["total_comprado"]), Decimal(ec["total_pagado"])
    la = Decimal(ec["libro_anterior_saldo"])
    print(f"\n(total_comprado {tc}) - (total_pagado {tp}) + (libro {la}) = {tc - tp + la}")
    print(f"saldo devuelto = {Decimal(ec['saldo'])}")
    assert (tc - tp) + la == Decimal(ec["saldo"])
    assert sum(Decimal(c["valor_total"]) for c in ec["compras_detalle"]) == tc
    assert sum(Decimal(p["valor"]) for p in ec["pagos"]) == tp
    assert Decimal(ec["libro_anterior_total"]) - Decimal(
        ec["libro_anterior_abonado"]
    ) == la
    assert ec["productor"] == PRODUCTOR, ec["productor"]

    # ----------------------------------------------------------------- PDF
    p = client.get(
        f"{API}/estado-cuenta-productor/pdf", params={"productor": PRODUCTOR}, headers=h
    )
    assert p.status_code == 200, p.text
    contenido = p.content
    disposicion = p.headers["content-disposition"]
    ruta = SALIDA / "estado_cuenta_PRODUCTOR.pdf"
    ruta.write_bytes(contenido)
    print(f"\n=== PDF productor: {ruta} ({len(contenido)} bytes) ===")
    print(f"Content-Disposition: {disposicion}")

    texto = _texto(contenido)
    streams = _pdf_streams(contenido)
    # El escáner de streams tiene dientes: encuentra lo que SÍ está impreso.
    _verificar_que_el_escaner_sirve(
        streams,
        ["PRODUCTOR", "Sebasti", "11.317", "2.063.400", "borona", "044", "SALDO A FAVOR"],
    )
    meta = PdfReader(io.BytesIO(contenido)).metadata
    print("\n--- TEXTO EXTRAÍDO (pypdf) ---")
    print(texto)
    print("\n--- METADATOS ---")
    print(dict(meta or {}))

    for palabra in PROHIBIDAS:
        assert palabra not in texto, f"FUGA en el texto del PDF: {palabra!r}"
        assert palabra not in streams, f"FUGA en los streams del PDF: {palabra!r}"
        assert palabra not in disposicion, f"FUGA en el nombre del archivo: {palabra!r}"
        for clave, valor in dict(meta or {}).items():
            assert palabra not in str(valor), f"FUGA en el metadato {clave}: {palabra!r}"

    # "factura" solo puede aparecer para NEGARLO
    assert "no es una factura" in texto
    assert len(re.findall(r"[Ff]actura", texto)) == 1, re.findall(r"[Ff]actura", texto)

    # Lo suyo SÍ está, y con el rótulo del signo correcto
    assert "ESTADO DE CUENTA DEL PRODUCTOR" in texto
    assert "SALDO A FAVOR DEL PRODUCTOR" in texto
    assert PRODUCTOR in texto
    assert CONCEPTO_PAGAR in texto
    assert "borona" in texto


def test_documento_del_cliente_sigue_sin_datos_del_productor(client, base_datos):
    """El espejo: el estado de cuenta del CLIENTE no puede haberse contaminado."""
    h = auth_headers(client, "admin.a")
    _sembrar(client, h)
    SALIDA.mkdir(parents=True, exist_ok=True)

    p = client.get(
        f"{API}/estado-cuenta/pdf", params={"cliente": CLIENTE}, headers=h
    )
    assert p.status_code == 200, p.text
    ruta = SALIDA / "estado_cuenta_CLIENTE.pdf"
    ruta.write_bytes(p.content)
    texto = _texto(p.content)
    streams = _pdf_streams(p.content)
    _verificar_que_el_escaner_sirve(streams, ["ESTADO DE CUENTA", "Ricaute", "23.457", "1.407.420"])
    print(f"\n=== PDF cliente: {ruta} ({len(p.content)} bytes) ===")
    print(f"Content-Disposition: {p.headers['content-disposition']}")
    print("\n--- TEXTO EXTRAÍDO (pypdf) ---")
    print(texto)

    for palabra in [
        "Sebastián", "Ruiz", "11.317", "11317", "1.357.___",  # precio de compra
        "NOTAINTERNACOMPRA", "NOTAINTERNAABONO", "NOTAINTERNAVENTA", "CANARIOFLETE",
        "NOTAINTERNAABONOVENTA", "Flete", "flete", "margen", "ganancia",
        "Compra vieja del libro", "CANARIOCOBRAR",
    ]:
        assert palabra not in texto, f"FUGA en el PDF del cliente: {palabra!r}"
        assert palabra not in streams, f"FUGA en los streams del PDF del cliente: {palabra!r}"


def test_productor_solo_con_libro_anterior_y_homonimo(client, base_datos):
    """Un tercero que SOLO tiene libro 'cobrar' no puede tener documento de
    productor: sería entregarle la deuda de un cliente."""
    h = auth_headers(client, "admin.a")
    r = client.post(
        f"{API}/saldos-anteriores",
        json={
            "tipo": "cobrar",
            "tercero": "Homónimo Pérez",
            "fecha": "2025-09-09",
            "concepto": CONCEPTO_COBRAR,
            "valor_total": VALOR_COBRAR,
            "abonado": 0,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    r = client.get(
        f"{API}/estado-cuenta-productor",
        params={"productor": "Homónimo Pérez"},
        headers=h,
    )
    print("\n=== solo libro 'cobrar' -> ", r.status_code, r.text[:200])
    assert r.status_code == 404, r.text
    r = client.get(
        f"{API}/estado-cuenta-productor/pdf",
        params={"productor": "Homónimo Pérez"},
        headers=h,
    )
    assert r.status_code == 404
