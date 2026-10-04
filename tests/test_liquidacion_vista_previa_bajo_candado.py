"""LA VISTA PREVIA DE CORREGIR LEE LA QUINCENA EN UNA SOLA FOTO: UN ABONO AJENO NO INVENTA
UNA DEUDA BORRADA.

La deuda borrada por la migración es Σ(pagos) − pagado, y las dos mitades salen de dos
SELECT distintos: la fila y su colección de pagos (selectin). La vista previa no tomaba el
candado, así que un abono que otro usuario confirmara entre las dos lecturas la hacía
rebotar con el aviso de la migración sobre una quincena que nunca pasó por ella, y con
cifras falsas. Medido en Postgres (READ COMMITTED): la de agosto de $250.000 con $165.000
pagados contestaba "le borró lo que el tercero quedaba debiendo ($5.000) … El saldo dice
$90.000, pero lo que de verdad falta entregarle es $85.000"; sin la carrera, 200.

SQLite tiene una sola conexión, así que la carrera se arma a mano igual que en
tests/test_liquidacion_deuda_borrada_bajo_candado.py: el abono ajeno se escribe por debajo
del ORM, la fila en memoria se queda con su `pagado` viejo y solo se vuelven a leer los
pagos. Con `_bloquear` (FOR UPDATE + `populate_existing`) la vista previa relee las dos
mitades después del candado y contesta con las cifras de la foto buena.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_bajo_candado import (
    API,
    BORRADA,
    _abono_ajeno_en_el_medio,
    _agosto,
)


def D(v):
    return Decimal(str(v))


def test_la_vista_previa_no_ve_deuda_borrada_por_un_abono_ajeno(client, base_datos, db_session):
    """125 L × $2.000 = $250.000 en 'parcial' con pagos de $60.000 y $40.000; en el medio,
    otro usuario confirma uno de $50.000. Precio de $2.000 a $2.100: 125 × 2.100 =
    $262.500, y con $150.000 pagados quedan 262.500 − 150.000 = $112.500 por entregar."""
    h = auth_headers(client, "admin.a")
    liq = _agosto(client, h, "Agosto Vista Previa", abonos=("60000", "40000"))
    detalle = client.get(f"{API}/{liq}", headers=h).json()["detalles"][0]
    en_memoria = _abono_ajeno_en_el_medio(db_session, liq, "50000")
    r = client.post(f"{API}/{liq}/corregir/previsualizar", json={
        "motivo": "precio mal digitado",
        "precios": [{"detalle_id": detalle["id"], "precio_litro": "2100"}]}, headers=h)
    del en_memoria
    print(f"\n  vista previa -> {r.status_code}: {r.text[:300]}")
    assert r.status_code == 200, r.text
    assert BORRADA not in r.text
    previa = r.json()
    assert (D(previa["pagado"]), D(previa["saldo_antes"])) == (D("150000"), D("100000"))
    assert (D(previa["valor_total_despues"]), D(previa["saldo_despues"])) == (
        D("262500"), D("112500"))
    # La regla de oro de la foto: neto = valor − anticipos − deuda vieja; saldo = neto −
    # pagado, antes y después.
    assert D(previa["neto_antes"]) - D(previa["pagado"]) == D(previa["saldo_antes"])
    assert D(previa["neto_despues"]) - D(previa["pagado"]) == D(previa["saldo_despues"])
