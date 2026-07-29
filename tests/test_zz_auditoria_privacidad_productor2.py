"""Segunda vuelta de la auditoría: los bordes.

Aislamiento entre empresas, inyección de cabecera por el nombre, compras
anuladas, el 404 propio del rango, el pie de las páginas de continuación y los
abonos del libro anterior (que también traen nota interna).
"""
import io
from pathlib import Path

from pypdf import PdfReader

from tests.conftest import auth_headers
from tests.test_zz_auditoria_privacidad_productor import (
    PRODUCTOR,
    PROHIBIDAS,
    _pdf_streams,
    _sembrar,
    _texto,
    _verificar_que_el_escaner_sirve,
)

API = "/api/v1/reventa"
SALIDA = Path(__file__).resolve().parent.parent / "uploads" / "auditoria_privacidad"


def test_aislamiento_entre_empresas(client, base_datos):
    """El productor de la empresa A no existe para la empresa B."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    r = client.post(
        f"{API}/compras",
        json={
            "fecha": "2026-04-01",
            "productor": "Productor Solo De A",
            "kilos_brutos": 10,
            "precio_kilo": 9000,
            "observaciones": "NOTAINTERNADEA",
        },
        headers=ha,
    )
    assert r.status_code == 201, r.text
    for headers, esperado in ((ha, 200), (hb, 404)):
        r = client.get(
            f"{API}/estado-cuenta-productor",
            params={"productor": "Productor Solo De A"},
            headers=headers,
        )
        assert r.status_code == esperado, r.text
        r = client.get(
            f"{API}/estado-cuenta-productor/pdf",
            params={"productor": "Productor Solo De A"},
            headers=headers,
        )
        print("empresa", "A" if headers is ha else "B", "->", r.status_code)
        assert r.status_code == esperado


def test_nombre_con_comillas_y_salto_no_inyecta_cabecera(client, base_datos):
    h = auth_headers(client, "admin.a")
    veneno = 'Ru"iz\r\nX-Inyectado: si <b>x</b>'
    r = client.post(
        f"{API}/compras",
        json={
            "fecha": "2026-04-02",
            "productor": veneno,
            "kilos_brutos": 10,
            "precio_kilo": 9000,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    guardado = r.json()["productor"]
    print("nombre guardado:", repr(guardado))
    r = client.get(
        f"{API}/estado-cuenta-productor/pdf", params={"productor": guardado}, headers=h
    )
    assert r.status_code == 200, r.text
    disp = r.headers["content-disposition"]
    print("Content-Disposition:", repr(disp))
    assert "\n" not in disp and "\r" not in disp
    assert disp.count('"') == 2
    assert "X-Inyectado" not in r.headers
    ruta = SALIDA / "estado_cuenta_PRODUCTOR_nombre_veneno.pdf"
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(r.content)
    texto = _texto(r.content)
    print("texto:", texto[:400])
    # El mini-XML de reportlab no interpreta el <b>: sale literal, no en negrita.
    assert "<b>x</b>" in texto


def test_compra_anulada_no_entra(client, base_datos):
    h = auth_headers(client, "admin.a")
    r = client.post(
        f"{API}/compras",
        json={
            "fecha": "2026-04-03",
            "productor": "Anulado Pérez",
            "kilos_brutos": 10,
            "precio_kilo": 9000,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    compra_id = r.json()["id"]
    r = client.post(f"{API}/compras/{compra_id}/anular", headers=h)
    assert r.status_code in (200, 204), r.text
    r = client.get(
        f"{API}/estado-cuenta-productor", params={"productor": "Anulado Pérez"}, headers=h
    )
    print("anulada ->", r.status_code, r.text[:160])
    assert r.status_code == 404


def test_404_propio_cuando_el_rango_queda_vacio(client, base_datos):
    h = auth_headers(client, "admin.a")
    r = client.post(
        f"{API}/compras",
        json={
            "fecha": "2026-01-15",
            "productor": "Fuera De Rango",
            "kilos_brutos": 10,
            "precio_kilo": 9000,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    r = client.get(
        f"{API}/estado-cuenta-productor",
        params={"productor": "Fuera De Rango", "desde": "2026-05-01", "hasta": "2026-05-31"},
        headers=h,
    )
    print("rango vacío ->", r.status_code, r.text)
    assert r.status_code == 404
    assert "en el período consultado" in r.json()["error"]["detail"]


def test_varias_paginas_y_abonos_del_libro(client, base_datos):
    """Muchas compras (pie de continuación) y un abono al saldo del libro con nota
    interna: ni el pie ni la sección del libro pueden filtrar nada."""
    h = auth_headers(client, "admin.a")
    nombre = "Productor Con Muchas Compras Y Un Nombre Larguísimo De Verdad"
    for dia in range(1, 29):
        r = client.post(
            f"{API}/compras",
            json={
                "fecha": f"2026-05-{dia:02d}",
                "productor": nombre,
                "kilos_brutos": 30 + dia,
                "borona_kilos": 2,
                "precio_kilo": 11317,
                "observaciones": "NOTAINTERNACOMPRA lo revendo a Ricaute a 23457",
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        r = client.post(
            f"{API}/compras/{r.json()['id']}/abonos",
            json={"fecha": f"2026-06-{dia:02d}", "valor": 100000, "observaciones": "NOTAINTERNAABONO"},
            headers=h,
        )
        assert r.status_code == 200, r.text
    r = client.post(
        f"{API}/saldos-anteriores",
        json={
            "tipo": "pagar",
            "tercero": nombre,
            "fecha": "2025-08-08",
            "concepto": "Compra vieja del libro 099",
            "valor_total": 900000,
            "abonado": 0,
            "observaciones": "NOTAINTERNASALDO",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    saldo_id = r.json()["id"]
    r = client.post(
        f"{API}/saldos-anteriores/{saldo_id}/abonos",
        json={"fecha": "2025-09-09", "valor": 250000, "observaciones": "NOTAINTERNAABONOLIBRO"},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text

    p = client.get(
        f"{API}/estado-cuenta-productor/pdf", params={"productor": nombre}, headers=h
    )
    assert p.status_code == 200, p.text
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / "estado_cuenta_PRODUCTOR_multipagina.pdf"
    ruta.write_bytes(p.content)
    lector = PdfReader(io.BytesIO(p.content))
    print(f"\n=== PDF multipágina: {ruta} ({len(p.content)} bytes, {len(lector.pages)} páginas) ===")
    assert len(lector.pages) > 1
    texto = _texto(p.content)
    streams = _pdf_streams(p.content)
    _verificar_que_el_escaner_sirve(streams, ["Estado de cuenta del productor", "099", "11.317"])
    print("\n--- TEXTO ---")
    print(texto)
    for palabra in [*PROHIBIDAS, "NOTAINTERNAABONOLIBRO"]:
        assert palabra not in texto, f"FUGA en el texto: {palabra!r}"
        assert palabra not in streams, f"FUGA en los streams: {palabra!r}"
    # El pie de continuación identifica el documento y recorta el nombre largo
    assert "Estado de cuenta del productor" in texto


def test_cliente_homonimo_del_productor(client, base_datos):
    """El MISMO nombre es cliente (libro 'cobrar') y productor (compras + libro
    'pagar'). Ninguno de los dos documentos puede traer nada del otro lado."""
    h=auth_headers(client,"admin.a")
    _sembrar(client,h)
    j=client.get(f"{API}/estado-cuenta",params={"cliente":PRODUCTOR},headers=h)
    print("JSON cliente homónimo:",j.status_code)
    print(j.text)
    p=client.get(f"{API}/estado-cuenta/pdf",params={"cliente":PRODUCTOR},headers=h)
    assert p.status_code==200
    SALIDA.mkdir(parents=True,exist_ok=True)
    ruta=SALIDA/"estado_cuenta_CLIENTE_homonimo.pdf"
    ruta.write_bytes(p.content)
    t=_texto(p.content); s=_pdf_streams(p.content)
    print("PDF:",ruta)
    print(t)
    for prohibido in ["Compra vieja del libro 044","11.317","1.358.040","NOTAINTERNA","2.263.400","borona","7 kg"]:
        assert prohibido not in t, f"FUGA del lado productor en el doc del cliente: {prohibido!r}"
        assert prohibido not in s, f"FUGA (streams): {prohibido!r}"
    assert "CANARIOCOBRAR" in t and "777.777" in t
