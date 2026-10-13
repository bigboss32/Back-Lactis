"""EL PRECIO DE UN DÍA, RECALCULAR Y APROBAR TOMAN EL CANDADO DE LA FILA ANTES DE PREGUNTAR.

`actualizar_precio_varios_dias`, Pagar, Abonar, Anular y Corregir ya leían la liquidación
con FOR UPDATE y `populate_existing` (`_bloquear`). Estos tres no: leían el estado y los
totales sin candado y escribían encima de otro usuario.

  · APROBAR barre los anticipos pendientes y escribe el saldo con el `valor_total` que tiene
    en la mano (`_refrescar_saldo`). Si otra persona acaba de cambiarle el precio a un día,
    ese total está viejo y el saldo queda restado contra una cifra que ya no existe: el
    desglose deja de sumar la cifra grande, en el comprobante que enseguida se paga. Y con
    una anulación en el medio, aprobaba una quincena ya anulada.
  · RECALCULAR y el PRECIO DE UN DÍA preguntaban "¿es un borrador?" con el estado que
    leyeron primero: una aprobación en el medio las dejaba reescribir una quincena cuyo
    papel ya salió.

SQLite descarta el FOR UPDATE en silencio (ver `_bloquear`), así que la carrera no se puede
provocar de verdad con dos conexiones. Se arma la FOTO QUE DEJA LA CARRERA: la fila queda
cargada en memoria con sus valores de antes y, por debajo del ORM, "otro usuario" la cambia
en la base. Sin el candado la petición actúa con la foto vieja; con él, `populate_existing`
la relee después de esperar el turno. El FOR UPDATE en sí lo prueba la carrera real contra
Postgres de `herramientas/carrera_de_candados.py`.

Regla de oro sobre la fila al final: neto = valor_total − anticipos − saldo_anterior;
saldo = neto − pagado. Se lee de la BASE (`expire_all`), no de lo que quedó en memoria.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.core.context import system_context
from app.core.exceptions import BusinessError
from app.modules.liquidaciones import service as servicio
from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import LiquidacionService
from tests.conftest import auth_headers
from tests.test_liquidacion_anticipos_pendientes import _anticipo
from tests.test_liquidacion_precio_varios_dias import (
    A_1900,
    A_2000,
    API,
    _detalle,
    _leer,
    _quincena,
    _recepciones,
    D,
)


def _fila_vieja_con(db_session, liq_id, **cambios):
    """La liquidación como la leyó esta petición, y por debajo el cambio de OTRO usuario.

    Devuelve la fila en memoria y la prueba la tiene que GUARDAR mientras hace la petición:
    el mapa de identidad la sostiene con una referencia débil, y si nadie más la tiene se
    recoge y la petición la lee entera desde la base, sin foto vieja que medir."""
    fila = db_session.get(Liquidacion, uuid.UUID(liq_id))
    estado_en_memoria = fila.estado
    tabla = Liquidacion.__table__
    db_session.execute(update(tabla).where(tabla.c.id == fila.id).values(**cambios))
    db_session.commit()
    if "estado" in cambios:
        assert fila.estado == estado_en_memoria, "la foto no quedó vieja"
    return fila


def _de_la_base(client, h, db_session, liq_id):
    """La liquidación leída de la BASE, sin lo que haya quedado en memoria."""
    db_session.expire_all()
    return _leer(client, h, liq_id)


def _cuadra(liq):
    neto = D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq["saldo_anterior"] or 0)
    assert D(liq["saldo"]) == neto - D(liq["pagado"]), liq


def _una_recepcion(client, h, prov):
    return _recepciones(client, h, prov)[0]


# =============================================================== el precio de UN día
def test_el_precio_de_un_dia_ve_la_aprobacion_que_entro_en_el_medio_y_rebota(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    dia = _leer(client, h, liq["id"])["detalles"][0]["id"]
    vieja = _fila_vieja_con(db_session, liq["id"], estado="aprobada")

    r = client.put(f"{API}/{liq['id']}/detalles/{dia}", json={"precio_litro": "2000"}, headers=h)
    del vieja

    print(f"\n  precio de un día con la aprobación en el medio: {r.status_code} · {_detalle(r)}")
    assert r.status_code == 422
    assert "'aprobada'" in _detalle(r)
    assert all(D(x["precio_litro"]) == D("1900") for x in _recepciones(client, h, prov))
    despues = _de_la_base(client, h, db_session, liq["id"])
    assert D(despues["valor_total"]) == A_1900 and despues["estado"] == "aprobada"
    _cuadra(despues)


def test_el_precio_de_un_dia_toma_el_candado_antes_del_guardia_y_recalcula_despues(
    client, base_datos, monkeypatch
):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    dia = _leer(client, h, liq["id"])["detalles"][0]["id"]
    orden = []
    bloquear, guardia = servicio._bloquear, servicio._razon_para_no_cambiar_el_precio
    recalcular = LiquidacionService._recalcular_desde_recepciones

    def _bloquear(db, liquidacion):
        orden.append("candado")
        return bloquear(db, liquidacion)

    def _guardia(liquidacion, ctx=None):
        orden.append("guardia")
        return guardia(liquidacion, ctx)

    def _recalcular(self, liquidacion):
        orden.append("recalculo")
        return recalcular(self, liquidacion)

    monkeypatch.setattr(servicio, "_bloquear", _bloquear)
    monkeypatch.setattr(servicio, "_razon_para_no_cambiar_el_precio", _guardia)
    monkeypatch.setattr(LiquidacionService, "_recalcular_desde_recepciones", _recalcular)

    r = client.put(f"{API}/{liq['id']}/detalles/{dia}", json={"precio_litro": "2000"}, headers=h)

    assert r.status_code == 200, r.text
    assert orden == ["candado", "guardia", "recalculo"]


# ===================================================================== Recalcular
def test_recalcular_ve_la_aprobacion_que_entro_en_el_medio_y_rebota(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    vieja = _fila_vieja_con(db_session, liq["id"], estado="aprobada")

    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    del vieja

    print(f"\n  recalcular con la aprobación en el medio: {r.status_code} · {_detalle(r)}")
    assert r.status_code == 422
    assert "'aprobada'" in _detalle(r) and "borrador" in _detalle(r)
    despues = _de_la_base(client, h, db_session, liq["id"])
    assert despues["estado"] == "aprobada" and D(despues["valor_total"]) == A_1900
    _cuadra(despues)


def test_recalcular_toma_el_candado_antes_del_guardia(client, base_datos, monkeypatch):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    orden = []
    bloquear, guardia = servicio._bloquear, servicio._razon_para_no_recalcular

    def _bloquear(db, liquidacion):
        orden.append("candado")
        return bloquear(db, liquidacion)

    def _guardia(liquidacion, ctx=None):
        orden.append("guardia")
        return guardia(liquidacion, ctx)

    monkeypatch.setattr(servicio, "_bloquear", _bloquear)
    monkeypatch.setattr(servicio, "_razon_para_no_recalcular", _guardia)

    assert client.post(f"{API}/{liq['id']}/recalcular", headers=h).status_code == 200
    assert orden == ["candado", "guardia"]


def test_el_recuadre_por_un_dia_de_recepcion_sigue_funcionando_con_el_candado_dentro(
    client, base_datos, db_session
):
    """`recuadrar` llama a `recalcular`, que ahora vuelve a pedir el candado en la misma
    transacción. Lo que el PUT del día ya había escrito (litros, estado devuelto a borrador)
    está bajado a la base, así que releer no pierde nada: la aprobada vuelve a borrador y su
    total es el del día nuevo, al centavo."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    primero = _una_recepcion(client, h, prov)
    litros_nuevos = D(primero["cantidad_litros"]) + D("10")

    r = client.put(
        f"/api/v1/recepciones/{primero['id']}",
        json={"cantidad_litros": str(litros_nuevos)},
        headers=h,
    )

    assert r.status_code == 200, r.text
    assert [x["id"] for x in r.json()["liquidaciones_devueltas_a_borrador"]] == [liq["id"]]
    despues = _de_la_base(client, h, db_session, liq["id"])
    assert despues["estado"] == "borrador"
    assert D(despues["valor_total"]) == A_1900 + D("10") * D("1900")
    _cuadra(despues)


# ======================================================================== Aprobar
def test_aprobar_ve_la_anulacion_que_entro_en_el_medio_y_no_aprueba_una_anulada(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    vieja = _fila_vieja_con(db_session, liq["id"], estado="anulada")

    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    del vieja

    print(f"\n  aprobar con la anulación en el medio: {r.status_code} · {_detalle(r)}")
    assert r.status_code == 422
    assert "'anulada'" in _detalle(r) and "'aprobada'" in _detalle(r)
    assert _de_la_base(client, h, db_session, liq["id"])["estado"] == "anulada"


def test_aprobar_con_el_precio_cambiado_por_otro_deja_el_desglose_sumando_la_cifra_grande(
    client, base_datos, db_session
):
    """EL CASO QUE ROMPÍA LA REGLA DE ORO. Otro usuario le sube el precio a $2.000 (el total
    pasa de $7.679.325 a $8.083.500) mientras esta petición tiene en la mano el total de
    antes. Hay un anticipo de $500.000 sin descontar: al aprobar se barre, y el saldo se
    escribía como 7.679.325 − 500.000 contra un valor_total ya guardado en 8.083.500."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    _anticipo(client, h, prov, "2026-07-20", 500000)
    vieja = _fila_vieja_con(
        db_session,
        liq["id"],
        valor_bruto=A_2000,
        valor_total=A_2000,
        saldo=A_2000,
    )
    assert D(vieja.valor_total) == A_1900, "la foto no quedó vieja"

    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    del vieja

    assert r.status_code == 200, r.text
    despues = _de_la_base(client, h, db_session, liq["id"])
    print(
        f"\n  en la base: valor_total={despues['valor_total']} anticipos={despues['anticipos']} "
        f"saldo={despues['saldo']}"
    )
    assert despues["estado"] == "aprobada"
    assert D(despues["valor_total"]) == A_2000
    assert D(despues["anticipos"]) == D("500000")
    assert D(despues["saldo"]) == A_2000 - D("500000")
    _cuadra(despues)


def test_aprobar_toma_el_candado_antes_de_barrer_los_anticipos_y_de_cambiar_el_estado(
    client, base_datos, monkeypatch
):
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    _anticipo(client, h, prov, "2026-07-20", 100000)
    orden = []
    bloquear = servicio._bloquear
    anticipos = LiquidacionService._aplicar_anticipos_pendientes
    transicionar = LiquidacionService._transicionar

    def _bloquear(db, liquidacion):
        orden.append("candado")
        return bloquear(db, liquidacion)

    def _anticipos(self, liquidacion):
        orden.append("anticipos")
        return anticipos(self, liquidacion)

    def _transicionar(self, *args, **kwargs):
        orden.append("estado")
        return transicionar(self, *args, **kwargs)

    monkeypatch.setattr(servicio, "_bloquear", _bloquear)
    monkeypatch.setattr(LiquidacionService, "_aplicar_anticipos_pendientes", _anticipos)
    monkeypatch.setattr(LiquidacionService, "_transicionar", _transicionar)

    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    assert orden == ["candado", "anticipos", "estado"]


def test_aprobar_dos_veces_sigue_siendo_inofensivo_y_el_desglose_cuadra(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    _anticipo(client, h, prov, "2026-07-20", 250000)

    primero = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    segundo = client.post(f"{API}/{liq['id']}/aprobar", headers=h)

    assert primero.status_code == 200
    assert segundo.status_code == 422  # ya está aprobada: no se vuelve a aprobar
    despues = _de_la_base(client, h, db_session, liq["id"])
    assert D(despues["anticipos"]) == D("250000")
    assert D(despues["saldo"]) == A_1900 - D("250000")
    _cuadra(despues)


# ====================================================================== Recuadrar
def test_recuadrar_ve_el_abono_que_entro_en_el_medio_y_no_devuelve_a_borrador_una_con_pagos(
    client, base_datos, db_session
):
    """EL CASO QUE EL CANDADO DE `recalcular` NO CERRABA. Corregirle el transportador a un día
    puede meterlo a un comprobante de flete que ya cobraba ese viaje, y ese comprobante se lee
    en Recepción SIN candado. Mientras tanto otra persona le confirma un abono de $1.000. Con la
    foto vieja ('aprobada', sin pagos) `recuadrar` la devolvía a borrador encima del 'parcial' y
    la recalculaba contra plata ya entregada; con el candado puesto al empezar relee 'parcial'
    con su pago y rebota."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    vieja = _fila_vieja_con(
        db_session, liq["id"], estado="parcial", pagado=D("1000"), saldo=A_1900 - D("1000")
    )
    assert D(vieja.pagado) == 0, "la foto no quedó vieja"
    servicio_ = LiquidacionService(db_session, system_context(base_datos["empresa_a"].id))

    with pytest.raises(BusinessError) as rebote:
        servicio_.recuadrar(uuid.UUID(liq["id"]))
    del vieja

    print(f"\n  recuadrar con el abono en el medio: {rebote.value}")
    assert "pagos registrados" in str(rebote.value)
    despues = _de_la_base(client, h, db_session, liq["id"])
    assert despues["estado"] == "parcial" and D(despues["pagado"]) == D("1000")
    assert D(despues["valor_total"]) == A_1900


def test_recuadrar_toma_el_candado_antes_de_preguntar_por_la_deuda_y_el_estado(
    client, base_datos, db_session, monkeypatch
):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    orden = []
    bloquear, deuda = servicio._bloquear, servicio._exigir_deuda_no_trasladada

    def _bloquear(db, liquidacion):
        orden.append("candado")
        return bloquear(db, liquidacion)

    def _deuda(*args, **kwargs):
        orden.append("deuda")
        return deuda(*args, **kwargs)

    monkeypatch.setattr(servicio, "_bloquear", _bloquear)
    monkeypatch.setattr(servicio, "_exigir_deuda_no_trasladada", _deuda)
    servicio_ = LiquidacionService(db_session, system_context(base_datos["empresa_a"].id))

    devuelta = servicio_.recuadrar(uuid.UUID(liq["id"]))

    assert devuelta is True
    assert orden[:2] == ["candado", "deuda"]
