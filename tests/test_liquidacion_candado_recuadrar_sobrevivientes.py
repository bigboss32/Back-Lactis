"""PRUEBAS PARA LOS MUTANTES QUE SOBREVIVIERON a las pruebas del candado de `recuadrar`.

Cada prueba nombra el mutante que mata. Todas pasan con el código original.

  R04  `recuadrar` pide el candado pero tira lo que devuelve y sigue con la variable de antes
  R05  `recuadrar` bloquea con el id pelado ANTES de `get_or_fail` (sin filtro de empresa)
  R07  `recuadrar` sin su guardia de 'pagada': la pagada sin ningún pago registrado ya no rebota
       ahí sino un renglón más abajo, en `recalcular`, con otro texto

SQLite descarta el FOR UPDATE en silencio, así que (igual que en
test_liquidacion_candado_sobrevivientes.py) la carrera se arma con la FOTO que deja: la fila
queda cargada en memoria con sus valores de antes y, por debajo del ORM, "otro usuario" la
cambia en la base; o se fuerza la diferencia entre lo que devuelve `_bloquear` y lo que ya
había en memoria.
"""
import uuid

import pytest

from app.core.context import system_context
from app.core.exceptions import BusinessError, NotFoundError
from app.modules.liquidaciones import service as servicio
from app.modules.liquidaciones.service import LiquidacionService
from tests.conftest import auth_headers
from tests.test_liquidacion_anticipos_pendientes import _anticipo
from tests.test_liquidacion_candado_precio_recalcular_aprobar import _de_la_base
from tests.test_liquidacion_candado_sobrevivientes import _OtraInstancia
from tests.test_liquidacion_precio_varios_dias import A_1900, API, D, _quincena


# ============================================================ R04 · el guardia usa lo que devuelve el candado
def test_recuadrar_pregunta_con_lo_que_devuelve_el_candado_y_no_con_lo_que_tenia(
    client, base_datos, db_session, monkeypatch
):
    """R04. El estado que cuenta es el que `_bloquear` devuelve, no el que había antes de esperar
    el turno. Si `_bloquear` entrega la fila releída como 'pagada', `recuadrar` tiene que
    rebotar con ESO y no tocar nada. (En producción es LA MISMA instancia —el mapa de identidad
    más `populate_existing` la refrescan en el sitio— y por eso usar la variable vieja pasa
    desapercibido; aquí se fuerza la diferencia, como en el guardia del precio.)"""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    bloquear = servicio._bloquear
    monkeypatch.setattr(
        servicio, "_bloquear", lambda db, l: _OtraInstancia(bloquear(db, l), estado="pagada")
    )
    servicio_ = LiquidacionService(db_session, system_context(base_datos["empresa_a"].id))

    with pytest.raises(BusinessError) as rebote:
        servicio_.recuadrar(uuid.UUID(liq["id"]))
    monkeypatch.undo()

    print(f"\n  recuadrar con el candado entregando 'pagada': {rebote.value}")
    assert "ya está pagada" in str(rebote.value)
    despues = _de_la_base(client, h, db_session, liq["id"])
    assert despues["estado"] == "aprobada", "se la devolvió a borrador con la foto vieja"
    assert D(despues["valor_total"]) == A_1900


# ============================================================ R05 · bloquear con filtro de empresa
def test_recuadrar_no_bloquea_nada_si_la_liquidacion_no_existe_o_es_de_otra_empresa(
    client, base_datos, db_session, monkeypatch
):
    """R05. `_bloquear` relee por id y NO filtra por empresa: quien lo llama tiene que haber pasado
    antes por `get_or_fail`, que sí lo hace. Bloqueando primero con el id pelado, `recuadrar` de
    otra empresa tomaba el FOR UPDATE de una fila AJENA antes de rebotar con 404 (y ponía a
    esperar a quien sí es dueño de la fila), y con un id que no existe reventaba con
    NoResultFound (500) en vez de NotFoundError (404)."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    candados = []
    bloquear = servicio._bloquear

    def _espia(db, liquidacion):
        candados.append(liquidacion.id)
        return bloquear(db, liquidacion)

    monkeypatch.setattr(servicio, "_bloquear", _espia)
    de_la_otra_empresa = LiquidacionService(db_session, system_context(base_datos["empresa_b"].id))
    de_esta_empresa = LiquidacionService(db_session, system_context(base_datos["empresa_a"].id))

    with pytest.raises(NotFoundError):
        de_la_otra_empresa.recuadrar(uuid.UUID(liq["id"]))
    with pytest.raises(NotFoundError):
        de_esta_empresa.recuadrar(uuid.uuid4())
    monkeypatch.undo()

    assert candados == [], "se tomó el candado antes de comprobar que la fila es de esta empresa"
    assert _de_la_base(client, h, db_session, liq["id"])["estado"] == "borrador"


# ============================================================ R07 · la pagada rebota en su propio guardia
def test_recuadrar_una_pagada_sin_ningun_pago_rebota_por_pagada_y_no_por_recalcular(
    client, base_datos, db_session
):
    """R07. La quincena que los anticipos cubrieron EXACTO queda con saldo cero: `pagar` solo
    cambia el estado a 'pagada' y NO hay ningún pago registrado (`tiene_pagos` es falso). Lo
    único que la frena en `recuadrar` es su guardia de 'pagada'. Sin él seguía de largo hasta
    `recalcular`, que rebotaba con otra frase ("solo se puede recalcular mientras sea un
    borrador") y lejos de lo que `recuadrar` promete: que de una pagada no se toca nada."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    _anticipo(client, h, prov, "2026-07-20", int(A_1900))
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    assert pagada.json()["estado"] == "pagada" and D(pagada.json()["pagado"]) == D("0")
    servicio_ = LiquidacionService(db_session, system_context(base_datos["empresa_a"].id))

    with pytest.raises(BusinessError) as rebote:
        servicio_.recuadrar(uuid.UUID(liq["id"]))

    print(f"\n  recuadrar una pagada sin pagos: {rebote.value}")
    assert str(rebote.value) == "Esta liquidación ya está pagada: sus días no se pueden modificar"
    despues = _de_la_base(client, h, db_session, liq["id"])
    assert despues["estado"] == "pagada" and D(despues["valor_total"]) == A_1900
