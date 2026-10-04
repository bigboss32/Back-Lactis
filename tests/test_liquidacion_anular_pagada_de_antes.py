"""ANULAR LA 'PAGADA' DE ANTES DE LOS ABONOS NO MANDA A BORRAR UN PAGO QUE NO EXISTE.

La migración a5e7c1b4d9f2 (01/08/2026, ya corrida en producción) les escribió a las
'pagada' de antes pagado = valor_total − anticipos, SIN renglón de pago. La de 100 L ×
$2.000 = $200.000 con $50.000 de adelanto quedó con pagado $150.000, saldo $0 y `pagos`
vacío. POST /anular le contestaba "elimine primero los pagos … use 'Corregir esta
quincena': eso conserva los pagos y sus soportes", y no hay ningún pago de $150.000 que
eliminar ni soporte que conservar.

Sigue sin poder anularse —esa plata salió, por fuera del sistema—, pero el porqué es el
cierto, y Corregir se nombra porque de verdad la acepta (se mide siguiéndolo). La 'pagada'
de hoy, con su renglón, sigue diciendo "elimine primero los pagos", que ahí es verdad y
destraba. Y en el flete, que Corregir no recibe, ninguno de estos textos lo nombra.
"""
import uuid
from decimal import Decimal

from app.core.context import RequestContext
from app.modules.liquidaciones.models import Liquidacion, PagoLiquidacion
from app.modules.liquidaciones.service import por_que_no_se_anula
from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import (
    API,
    _correr_la_migracion,
    _detalle,
    _dia,
    _julio_aprobada,
    _leer,
)


def D(v):
    return Decimal(str(v))


def _pagada_de_antes(client, h, db_session, nombre):
    """100 L × $2.000 − $50.000: 'pagada' con el botón de antes (solo cambiaba el estado) y
    después la migración, que le escribió pagado = $150.000 sin renglón."""
    prov, liq = _julio_aprobada(client, h, nombre, "100", "50000")
    fila = db_session.get(Liquidacion, uuid.UUID(liq["id"]))
    fila.estado = "pagada"
    db_session.commit()
    _correr_la_migracion(db_session)
    leida = _leer(client, h, liq["id"])
    assert (leida["estado"], leida["version"], D(leida["valor_total"]), D(leida["anticipos"]),
            D(leida["pagado"]), D(leida["saldo"]), leida["pagos"]) == (
        "pagada", 1, D("200000"), D("50000"), D("150000"), D(0), [])
    assert D(leida["deuda_borrada_por_la_migracion"]) == 0
    return prov, leida


def test_la_pagada_de_antes_dice_que_sus_pagos_no_tienen_renglon(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, liq = _pagada_de_antes(client, h, db_session, "Anular De Antes")
    r = client.post(f"{API}/{liq['id']}/anular", headers=h)
    print(f"\n  anular -> {r.status_code}: {_detalle(r)}")
    assert r.status_code == 422
    assert _detalle(r) == (
        "No se puede anular esta liquidación: sus pagos ($150.000) quedaron anotados antes "
        "de que existieran los abonos, sin un renglón de pago que se pueda borrar, así que "
        "no hay cómo dejarla sin pagos. Si lo que necesita es arreglarle una cifra, use "
        "'Corregir esta quincena'")
    assert "elimine primero los pagos" not in _detalle(r)
    assert "soportes" not in _detalle(r)
    assert _leer(client, h, liq["id"])["estado"] == "pagada"

    # SE SIGUE EL CONSEJO: un día olvidado de 10 L ($20.000). 220.000 − 50.000 − 150.000 =
    # $20.000 por entregar, y los $150.000 de antes siguen ahí.
    olvidado = _dia(client, h, prov, "2026-07-08", "10")
    r = client.post(f"{API}/{liq['id']}/corregir", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}, headers=h)
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert (corregida["estado"], corregida["version"], D(corregida["valor_total"]),
            D(corregida["pagado"]), D(corregida["saldo"])) == (
        "parcial", 2, D("220000"), D("150000"), D("20000"))
    assert D(corregida["valor_total"]) - D(corregida["anticipos"]) - D(corregida["pagado"]) \
        == D(corregida["saldo"])


def test_la_pagada_de_hoy_con_su_renglon_sigue_igual(client, base_datos):
    """Control: Pagar deja un renglón de $150.000. Ahí "elimine primero los pagos" es
    verdad y destraba: se borra el pago (200) y anular pasa (200)."""
    h = auth_headers(client, "admin.a")
    _, liq = _julio_aprobada(client, h, "Anular De Hoy", "100", "50000")
    r = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert r.status_code == 200, r.text
    (pago,) = r.json()["pagos"]
    r = client.post(f"{API}/{liq['id']}/anular", headers=h)
    assert r.status_code == 422
    assert _detalle(r) == (
        "No se puede anular una liquidación con pagos registrados: elimine primero los "
        "pagos. Si lo único que necesita es arreglarle una cifra —un día que faltó o un "
        "precio mal digitado— use 'Corregir esta quincena': eso conserva los pagos y sus "
        "soportes")
    assert client.delete(f"{API}/{liq['id']}/pagos/{pago['id']}", headers=h).status_code == 200
    r = client.post(f"{API}/{liq['id']}/anular", headers=h)
    assert r.status_code == 200 and r.json()["estado"] == "anulada", r.text


def test_en_el_flete_no_se_nombra_corregir(base_datos):
    """Sin base de datos. Corregir es solo para la leche ("solo se puede corregir una
    quincena de leche"): el rebote de Anular de un flete no puede mandar ahí."""
    con_abono = Liquidacion(tipo="transportador", estado="parcial", version=1,
                            valor_total=D("60000"), anticipos=D(0), saldo_anterior=D(0),
                            pagado=D("20000"), saldo=D("40000"))
    con_abono.pagos = [PagoLiquidacion(valor=D("20000"))]
    assert por_que_no_se_anula(con_abono) == (
        "No se puede anular una liquidación con pagos registrados: elimine primero los pagos")
    pagada = Liquidacion(tipo="transportador", estado="pagada", version=1,
                         valor_total=D("60000"), anticipos=D("60000"), saldo_anterior=D(0),
                         pagado=D(0), saldo=D(0))
    assert por_que_no_se_anula(pagada) == "No se puede anular una liquidación ya pagada"

    # Y borrar un pago pide 'eliminar', que Anular ('administrar') no trae consigo.
    solo_administrar = RequestContext(empresa_id=base_datos["empresa_a"].id,
                                      user_id=uuid.uuid4(),
                                      permisos={("liquidaciones", "administrar")})
    assert por_que_no_se_anula(con_abono, solo_administrar) == (
        "No se puede anular una liquidación con pagos registrados: pídale a un "
        "Administrador de la empresa que elimine primero los pagos")
