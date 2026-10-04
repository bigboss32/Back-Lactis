"""RECEPCIÓN DIARIA SOLO DICE "CON ABONO" SI SALIÓ PLATA POR PAGOS.

La columna Liquidación de Recepción diaria pintaba "🔒 Con abono" con
`liquidacion_estado == 'parcial'`. Pero la quincena de 100 L × $1.800 = $180.000
cubierta EXACTO por un adelanto de $180.000, cerrada con Pagar y corregida con un día
olvidado de 20 L ($36.000), queda 'parcial' v2 con pagado $0 y ningún pago: se le deben
$36.000 y no se le ha abonado un peso. Lo mismo la que se pagó con $180.000, se corrigió
y se le borró ese pago (saldo $216.000, el valor entero).

`RecepcionRead.liquidacion_con_abono` es la respuesta del servidor: la liquidación que
manda el estado está en 'parcial' y salió plata por pagos (`Liquidacion.con_abonos`).
"""
import uuid
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import _corregir
from tests.test_liquidacion_migrada_deuda_borrada import (
    _correr_la_migracion,
    _julio_aprobada,
    _leer,
)
from tests.test_recepcion_candado_quincena_corregida import _corregida_sin_pagos

V = "/api/v1"
API = f"{V}/liquidaciones"
REC = f"{V}/recepciones"


def D(v):
    return Decimal(str(v))


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _el_dia_en_las_tres_lecturas(client, h, dia):
    """El GET del día, la lista paginada y la del filtro avanzado: las tres dicen igual."""
    uno = client.get(f"{REC}/{dia}", headers=h).json()
    lecturas = [uno]
    for ruta in (REC, f"{REC}/filtrar/avanzado"):
        r = client.get(ruta, params={"page": 1, "page_size": 200}, headers=h)
        assert r.status_code == 200, r.text
        lecturas.append(next(x for x in r.json()["items"] if x["id"] == dia))
    claves = ("liquidacion_estado", "liquidacion_con_abono", "candado_aviso")
    assert len({tuple(x[c] for c in claves) for x in lecturas}) == 1, lecturas
    return uno


def _aprobada_de_180(client, h, nombre):
    prov = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                                 "precio_litro": "1800"},
                       headers=h).json()["id"]
    dia = client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                                 "cantidad_litros": "100"}, headers=h).json()["id"]
    g = _ok(client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                                "periodo_fin": "2026-06-15",
                                                "tipo": "proveedor"}, headers=h))
    liq = next(x for x in g["generadas"] if x["proveedor_id"] == prov)["id"]
    _ok(client.post(f"{API}/{liq}/aprobar", headers=h))
    return prov, dia, liq


def test_la_parcial_v2_del_adelanto_exacto_no_tiene_abono(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, dia, _, liq = _corregida_sin_pagos(client, h, "Chip Adelanto Exacto")
    fila = _el_dia_en_las_tres_lecturas(client, h, dia)
    print(f"\n  {fila['liquidacion_estado']} con_abono={fila['liquidacion_con_abono']}")
    assert fila["liquidacion_estado"] == "parcial"
    assert fila["liquidacion_con_abono"] is False
    assert "abon" not in fila["candado_aviso"]
    assert _leer(client, h, liq)["con_abonos"] is False


def test_la_corregida_a_la_que_se_le_borro_el_pago_no_tiene_abono(client, base_datos):
    """Pagada con un pago de $180.000, corregida con el día de $36.000 ('parcial' v2 con
    $180.000 entregados: ahí sí hay abono) y borrado ese pago, mal registrado: 'parcial'
    v2, pagado $0, saldo $216.000."""
    h = auth_headers(client, "admin.a")
    prov, dia, liq = _aprobada_de_180(client, h, "Chip Pago Borrado")
    _ok(client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20", "valor": "180000"},
                    headers=h))
    olvidado = client.post(REC, json={"fecha": "2026-06-05", "proveedor_id": prov,
                                      "cantidad_litros": "20"}, headers=h).json()["id"]
    _corregir(client, h, liq, olvidado)
    con_el_pago = _el_dia_en_las_tres_lecturas(client, h, dia)
    assert (con_el_pago["liquidacion_estado"], con_el_pago["liquidacion_con_abono"]) == (
        "parcial", True)

    (pago,) = _leer(client, h, liq)["pagos"]
    _ok(client.delete(f"{API}/{liq}/pagos/{pago['id']}", headers=h))
    hoy = _leer(client, h, liq)
    assert (hoy["estado"], hoy["version"], D(hoy["pagado"]), D(hoy["saldo"])) == (
        "parcial", 2, D(0), D(216000))
    fila = _el_dia_en_las_tres_lecturas(client, h, dia)
    assert (fila["liquidacion_estado"], fila["liquidacion_con_abono"]) == ("parcial", False)


def test_el_abono_de_verdad_si_se_marca(client, base_datos):
    """Control: $180.000 con un abono de $50.000, 'parcial' v1 con saldo $130.000."""
    h = auth_headers(client, "admin.a")
    _, dia, liq = _aprobada_de_180(client, h, "Chip Abono De Verdad")
    _ok(client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20", "valor": "50000"},
                    headers=h))
    fila = _el_dia_en_las_tres_lecturas(client, h, dia)
    assert (fila["liquidacion_estado"], fila["liquidacion_con_abono"]) == ("parcial", True)
    assert "ya se le abonó" in fila["candado_aviso"]


def test_la_pagada_no_es_con_abono(client, base_datos):
    """La pagada del todo no lleva la marca del abono: su estado ya lo dice."""
    h = auth_headers(client, "admin.a")
    _, dia, liq = _aprobada_de_180(client, h, "Chip Pagada")
    _ok(client.post(f"{API}/{liq}/pagar", headers=h))
    fila = _el_dia_en_las_tres_lecturas(client, h, dia)
    assert (fila["liquidacion_estado"], fila["liquidacion_con_abono"]) == ("pagada", False)


def test_la_pagada_de_antes_corregida_hacia_arriba_si_tiene_abono(
        client, base_datos, db_session):
    """100 L × $2.000 = $200.000 − $50.000 de adelanto, 'pagada' con el botón de antes y
    migrada: pagado $150.000 SIN renglón (se pagó por fuera). Corregida con un día de 10 L
    ($20.000) queda 'parcial' con esos $150.000 entregados: ahí sí hubo abono."""
    h = auth_headers(client, "admin.a")
    prov, liq = _julio_aprobada(client, h, "Chip De Antes", "100", "50000")
    db_session.get(Liquidacion, uuid.UUID(liq["id"])).estado = "pagada"
    db_session.commit()
    _correr_la_migracion(db_session)
    olvidado = client.post(REC, json={"fecha": "2026-07-08", "proveedor_id": prov,
                                      "cantidad_litros": "10"}, headers=h).json()["id"]
    _corregir(client, h, liq["id"], olvidado)
    hoy = _leer(client, h, liq["id"])
    assert (hoy["estado"], D(hoy["pagado"]), hoy["pagos"], D(hoy["saldo"])) == (
        "parcial", D(150000), [], D(20000))
    r = client.get(REC, params={"page_size": 200}, headers=h)
    (dia,) = [x["id"] for x in r.json()["items"]
              if x["proveedor_id"] == prov and x["fecha"] == "2026-07-04"]
    fila = _el_dia_en_las_tres_lecturas(client, h, dia)
    assert (fila["liquidacion_estado"], fila["liquidacion_con_abono"]) == ("parcial", True)
