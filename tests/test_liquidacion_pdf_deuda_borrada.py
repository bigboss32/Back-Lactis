"""EL PAPEL DE LA QUINCENA CON LA DEUDA BORRADA CIERRA IGUAL QUE LA PANTALLA.

En esas filas `pagado` lleva metida, sin renglón, la deuda que borró la migración
(borrada = Σ pagos − pagado). La pantalla ya pinta "Pagado" = Σ pagos y un renglón
"Deuda borrada por la migración" en positivo, y así su columna cierra. El PDF imprimía
"Pagado" = `pagado` solo si era > 0 y sin ese renglón:

  · PLAIN (90 L × $2.000 = $180.000 contra $300.000, migrada): "VALOR TOTAL $180.000,
    Anticipos − $300.000, SALDO A PAGAR $0", sin nada que explique los $120.000;
  · MAS-CINCUENTA (corregida con 25 L = $50.000 y pagada, antes del guardia): neto
    −$70.000, pagado −$70.000 y un pago de $50.000. El papel no traía "Pagado" y bajaba
    de −$70.000 a $0 sin decir por dónde.

Ahora el papel imprime lo que la pantalla y se suma con calculadora de arriba abajo:
VALOR TOTAL − anticipos − lo que quedó debiendo − Σ pagos + deuda borrada = SALDO.
Las quincenas normales no cambian.
"""
import io
from decimal import Decimal

import pytest
from pypdf import PdfReader

from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import (
    _antes_del_guardia,
    _corregir,
    _entregado,
    _migradas,
)
from tests.test_liquidacion_migrada_deuda_borrada import _dia, _julio_aprobada, _leer

API = "/api/v1/liquidaciones"
CIERRES = ("SALDO A PAGAR", "LE QUEDA DEBIENDO", "SE LE PAGÓ DE MÁS")
BORRADA = "Deuda borrada por la migración"


def _resumen(client, h, liq_id):
    """Los renglones del resumen del papel, [(rótulo, texto de la cifra)], en orden."""
    pdf = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert pdf.status_code == 200, pdf.text
    texto = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    lineas = [linea.strip() for linea in texto.splitlines()]
    i = lineas.index("Resumen de liquidación") + 1
    filas = []
    while True:
        filas.append((lineas[i], lineas[i + 1]))
        i += 2
        if filas[-1][0] in CIERRES:
            return filas


def _cifra(texto):
    """'- $300.000' → (−1, 300000); '$180.000' → (0, 180000)."""
    signo = {"+": 1, "-": -1}.get(texto[0], 0)
    limpio = texto.lstrip("+- ").lstrip("$").replace(".", "").replace(",", ".")
    return signo, Decimal(limpio)


def _cierra(filas):
    """La cuenta del dueño: desde VALOR TOTAL, sumando y restando renglón por renglón
    en el orden impreso, se cae EXACTO en el renglón destacado del final."""
    rotulos = [r for r, _ in filas]
    desde = rotulos.index("VALOR TOTAL")
    _, cuenta = _cifra(filas[desde][1])
    for rotulo, texto in filas[desde + 1:-1]:
        signo, valor = _cifra(texto)
        assert signo != 0, rotulo
        cuenta += signo * valor
    _, final = _cifra(filas[-1][1])
    assert filas[-1][0] == "SALDO A PAGAR"
    assert cuenta == final, filas


def _plain(client, h, db, monkeypatch):
    (_, liq_id), = _migradas(client, h, db, "Papel Plain").values()
    return liq_id


def _mas_cincuenta(client, h, db, monkeypatch):
    (prov, liq_id), = _migradas(client, h, db, "Papel Mas Cincuenta").values()
    olvidado = _dia(client, h, prov, "2026-07-08", "25")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, olvidado)
    assert client.post(f"{API}/{liq_id}/pagar", headers=h).status_code == 200
    monkeypatch.undo()
    return liq_id


@pytest.mark.parametrize("forma, entregado", [(_plain, "0"), (_mas_cincuenta, "50000")])
def test_el_papel_dice_lo_que_la_pantalla_y_cierra(
        client, base_datos, db_session, monkeypatch, forma, entregado):
    h = auth_headers(client, "admin.a")
    liq_id = forma(client, h, db_session, monkeypatch)
    hoy = _leer(client, h, liq_id)
    assert _entregado(hoy) == Decimal(entregado)
    assert Decimal(hoy["deuda_borrada_por_la_migracion"]) == Decimal("120000")
    filas = _resumen(client, h, liq_id)
    print(f"\n  {forma.__name__}: {filas}")
    rotulos = dict(filas)
    # LA REGLA DE LA PANTALLA (G1): "Pagado" = Σ pagos, solo si hay; la deuda borrada
    # en su propio renglón, en positivo.
    if _entregado(hoy) > 0:
        assert _cifra(rotulos["Pagado"]) == (-1, _entregado(hoy))
    else:
        assert "Pagado" not in rotulos
    assert _cifra(rotulos[BORRADA]) == (1, Decimal("120000"))
    assert [r for r, _ in filas][-2:] == [BORRADA, "SALDO A PAGAR"]
    assert _cifra(rotulos["SALDO A PAGAR"])[1] == Decimal(hoy["saldo"])
    _cierra(filas)


def test_la_quincena_normal_no_cambia(client, base_datos, db_session):
    """Control: 100 L × $2.000 = $200.000 − $50.000 de adelanto, con un abono de
    $30.000. "Pagado − $30.000" (el `pagado` de siempre), sin renglón de deuda borrada,
    y la columna cierra en $120.000."""
    h = auth_headers(client, "admin.a")
    _, liq = _julio_aprobada(client, h, "Papel Normal", "100", "50000")
    assert client.post(f"{API}/{liq['id']}/pagos", json={"fecha": "2026-07-20",
                                                         "valor": "30000"},
                       headers=h).status_code == 200
    filas = _resumen(client, h, liq["id"])
    assert filas[-4:] == [("VALOR TOTAL", "$200.000"), ("Anticipos aplicados", "- $50.000"),
                          ("Pagado", "- $30.000"), ("SALDO A PAGAR", "$120.000")]
    assert BORRADA not in dict(filas)
    _cierra(filas)
