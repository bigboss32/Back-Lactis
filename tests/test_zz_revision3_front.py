"""zz REVISIÓN 3 (revisor del FRONT) — el PDF de las quincenas con la deuda borrada.

Tras G1 la pantalla pinta, para esas filas, "Pagado" = Σ pagos y un renglón propio
"Deuda borrada por la migración" (+), y así la columna cierra. El PDF —el papel que se
comparte con el tercero y que el dueño pone al lado de la pantalla— sigue imprimiendo
"Pagado" = `pagado` solo si es > 0 y sin el renglón de la deuda borrada. Cada prueba
afirma LA VERDAD: falla mientras el papel no diga lo mismo que la pantalla.
"""
import io
import re
from decimal import Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import (
    _antes_del_guardia,
    _corregir,
    _entregado,
    _migradas,
)
from tests.test_liquidacion_migrada_deuda_borrada import _dia, _leer

API = "/api/v1/liquidaciones"


def _papel(client, h, liq_id):
    pdf = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert pdf.status_code == 200, pdf.text
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)


def _pesos(texto, rotulo):
    """La cifra del renglón `rotulo` del resumen del papel, en Decimal (None si no está)."""
    m = re.search(rf"{rotulo}\s*[-−+]?\s*\$\s?([\d.]+(?:,\d+)?)", texto)
    if not m:
        return None
    return Decimal(m.group(1).replace(".", "").replace(",", "."))


def test_plain_el_papel_cierra_como_la_pantalla(client, base_datos, db_session):
    """PLAIN: $180.000 − $300.000 = SALDO A PAGAR $0 en el papel, sin renglón que lo
    explique. La pantalla lee 180.000 − 300.000 + 120.000 (deuda borrada) = 0."""
    h = auth_headers(client, "admin.a")
    (_, liq_id), = _migradas(client, h, db_session, "Plain Papel").values()
    texto = _papel(client, h, liq_id)
    print("\n" + texto)
    assert "Deuda borrada" in texto, "el papel no trae el renglón que la pantalla sí"


def test_paso_de_cero_el_pagado_del_papel_es_el_de_la_tabla(
        client, base_datos, db_session, monkeypatch):
    """PASO-DE-CERO: se entregaron $200.000 (un pago), y el papel imprime "Pagado
    − $80.000" (el `pagado` que lleva metida la deuda borrada). La pantalla dice
    "Pagado − $ 200.000" y "Deuda borrada + $ 120.000"."""
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Paso Papel").values()
    grande = _dia(client, h, prov, "2026-07-08", "100")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, grande)
    assert client.post(f"{API}/{liq_id}/pagar", headers=h).status_code == 200
    monkeypatch.undo()
    hoy = _leer(client, h, liq_id)
    assert _entregado(hoy) == Decimal("200000")
    texto = _papel(client, h, liq_id)
    print("\n" + texto)
    assert _pesos(texto, "Pagado") == _entregado(hoy)
