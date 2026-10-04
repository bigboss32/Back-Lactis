"""EL COMPROBANTE DE LA QUINCENA CABE EN UNA SOLA HOJA, TAMBIÉN EN EL CASO MÁS CARGADO.

`build_liquidacion_pdf` promete "caber SIEMPRE en una sola hoja", pero nada lo hacía
cumplir: si el contenido no cabía, seguía en la página 2 sin avisar. Y la segunda hoja
no es un detalle de imprenta: el productor firma la primera ("Recibí conforme") y la
otra se queda en la impresora, o se pierde entre las dos hojas el renglón que explica
el SALDO.

El caso más cargado que da el sistema, medido: quincena de julio con 13 días de 7 L
(91 L × $2.000 = $182.000) y $300.000 en CUATRO adelantos de $75.000, pagada con el
botón de antes y migrada (la migración le borró los $118.000 que debía). En agosto se le
metió con Corregir un día olvidado de 100 L ($200.000) —v2, con sus dos notas de
corrección— y se le registraron CINCO abonos de $10.000. Con la deuda borrada el resumen
lleva un renglón más y el papel lleva el AVISO de la posición de hoy. Antes de compactar
el papel, las combinaciones (4 adelantos, 5 abonos), (5, 4) y (6, 3) ya salían en dos
hojas solo con el renglón de la deuda borrada: las firmas quedaban solas en la segunda.
"""
import io
import uuid
from decimal import Decimal

import pytest
from pypdf import PdfReader

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import (
    _abonar,
    _antes_del_guardia,
    _corregir,
)
from tests.test_liquidacion_migrada_deuda_borrada import (
    _correr_la_migracion,
    _dia,
    _leer,
    _proveedor,
)

V = "/api/v1"
API = f"{V}/liquidaciones"


def D(v):
    return Decimal(str(v))


def _papel(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    lector = PdfReader(io.BytesIO(r.content))
    return len(lector.pages), "\n".join(p.extract_text() for p in lector.pages)


def _la_mas_cargada(client, h, db, monkeypatch, nombre, *, adelantos, abonos):
    prov = _proveedor(client, h, nombre)
    for dia in range(1, 14):
        _dia(client, h, prov, f"2026-07-{dia:02d}", "7")
    for i in range(adelantos):
        r = client.post(f"{V}/anticipos", json={
            "tipo": "proveedor", "proveedor_id": prov, "fecha": f"2026-07-{i + 1:02d}",
            "valor": str(D(300000) / adelantos), "observaciones": "Adelanto en efectivo"},
            headers=h)
        assert r.status_code == 201, r.text
    r = client.post(f"{API}/generar", json={"periodo_inicio": "2026-07-01",
                                            "periodo_fin": "2026-07-15",
                                            "tipo": "proveedor"}, headers=h)
    assert r.status_code == 200, r.text
    liq_id = next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)["id"]
    assert client.post(f"{API}/{liq_id}/aprobar", headers=h).status_code == 200
    # El botón de antes solo cambiaba el estado; después corrió la migración.
    db.get(Liquidacion, uuid.UUID(liq_id)).estado = "pagada"
    db.commit()
    _correr_la_migracion(db)
    # El código de agosto, sin el guardia: así se corrigió y se abonó en producción.
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, _dia(client, h, prov, "2026-07-14", "100"))
    for _ in range(abonos):
        _abonar(client, h, liq_id, "10000")
    monkeypatch.undo()
    return liq_id


@pytest.mark.parametrize("adelantos, abonos", [(4, 5), (5, 4), (6, 3)])
def test_la_v2_cargada_con_deuda_borrada_cabe_en_una_hoja(
        client, base_datos, db_session, monkeypatch, adelantos, abonos):
    """Las tres combinaciones que salían en dos hojas solo por el renglón de la deuda
    borrada, ahora con el AVISO y las dos marcas del encabezado encima."""
    h = auth_headers(client, "admin.a")
    liq_id = _la_mas_cargada(client, h, db_session, monkeypatch,
                             f"Hoja Cargada {adelantos}-{abonos}",
                             adelantos=adelantos, abonos=abonos)
    hoy = _leer(client, h, liq_id)
    # Con calculadora: 382.000 − 300.000 = 82.000 de neto; la migración dejó pagado
    # −118.000 y los abonos de $10.000 lo subieron; el saldo es neto − pagado, y lo que
    # de verdad falta entregar es saldo − 118.000.
    entregado = D(10000) * abonos
    assert (hoy["version"], len([d for d in hoy["detalles"] if d.get("deleted_at") is None]),
            len(hoy["pagos"])) == (2, 14, abonos)
    assert (D(hoy["valor_total"]), D(hoy["anticipos"]), D(hoy["pagado"]),
            D(hoy["deuda_borrada_por_la_migracion"])) == (
        D(382000), D(300000), D(-118000) + entregado, D(118000))
    assert D(hoy["saldo"]) == D(82000) - (D(-118000) + entregado)
    paginas, texto = _papel(client, h, liq_id)
    print(f"\n  páginas={paginas}\n{texto}")
    plano = " ".join(texto.split())
    # Lo que lo hace el más cargado está de verdad en el papel que se mide.
    assert "COMPROBANTE CORREGIDO (v2)" in plano
    assert "PENDIENTE DE REPARAR · VER EL AVISO" in plano
    assert "AVISO: esta quincena está pendiente de reparar." in plano
    assert hoy["aviso_deuda_borrada"] in plano
    assert "Este comprobante REEMPLAZA" in plano
    assert plano.count("Adelanto en efectivo") == adelantos
    # Y las firmas quedan en la misma hoja.
    assert paginas == 1
    assert "Recibí conforme" in plano
