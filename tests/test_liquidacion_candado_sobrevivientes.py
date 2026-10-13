"""PRUEBAS PARA LOS MUTANTES QUE SOBREVIVIERON a la suite del candado de la liquidación.

Cada prueba nombra el mutante que mata. Todas pasan con el código original.

  M06  aprobar toma el candado DENTRO del `if estado == borrador` (después de leer el estado)
  M10  `_bloquear` sin `lazyload(Liquidacion.proveedor)`: LEFT JOIN + FOR UPDATE = 0A000
  M11  aprobar ya no pregunta `deuda_ya_cobrada` antes de barrer los anticipos
  M12  recalcular bloquea ANTES de `get_or_fail`, con el id pelado (sin filtro de empresa)
  M13  el guardia del precio usa la variable de antes del candado y no la que devuelve `_bloquear`
  M16  registrar_pago sin candado
  M17  anular sin candado
  M18  pagar sin candado

SQLite descarta el FOR UPDATE en silencio, así que (igual que en
test_liquidacion_candado_precio_recalcular_aprobar.py) la carrera se arma con la FOTO que deja:
la fila queda cargada en memoria con sus valores de antes y, por debajo del ORM, "otro usuario"
la cambia en la base.
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.modules.liquidaciones import service as servicio
from app.modules.liquidaciones.models import Liquidacion, PagoLiquidacion
from tests import test_liquidacion_anticipo_candado_deuda_cobrada as beto
from tests.conftest import auth_headers
from tests.test_liquidacion_anticipos_pendientes import _anticipo
from tests.test_liquidacion_candado_precio_recalcular_aprobar import (
    _cuadra,
    _de_la_base,
    _fila_vieja_con,
)
from tests.test_liquidacion_precio_varios_dias import (
    A_1900,
    API,
    D,
    _detalle,
    _leer,
    _quincena,
    _recepciones,
)


# ============================================================ M06 · aprobar relee SIEMPRE
def test_aprobar_relee_la_fila_y_aprueba_la_que_otro_devolvio_a_borrador(
    client, base_datos, db_session
):
    """M06. La petición tenía en memoria la quincena 'aprobada'; mientras esperaba, el recuadre
    de otra persona (editó una recepción) la devolvió a 'borrador' en la base. Con el candado
    PRIMERO la relee y la aprueba (200). Si el candado va después de leer el estado, decide con
    la foto vieja ('aprobada') y rebota con "No se puede pasar de 'aprobada' a 'aprobada'"."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    vieja = _fila_vieja_con(db_session, liq["id"], estado="borrador")
    assert vieja.estado == "aprobada", "la foto no quedó vieja"

    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    del vieja

    assert r.status_code == 200, r.text
    despues = _de_la_base(client, h, db_session, liq["id"])
    assert despues["estado"] == "aprobada"
    _cuadra(despues)


def test_aprobar_pide_el_candado_aunque_el_guardia_vaya_a_rebotar(client, base_datos, monkeypatch):
    """M06, por el orden de las llamadas: sobre una quincena que ya no es borrador el candado se
    toma igual y ANTES de que rebote (así rebota con el estado releído, no con el de la foto)."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    orden = []
    bloquear = servicio._bloquear

    def _bloquear(db, liquidacion):
        orden.append("candado")
        return bloquear(db, liquidacion)

    monkeypatch.setattr(servicio, "_bloquear", _bloquear)
    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)

    assert r.status_code == 422, r.text
    assert orden == ["candado"]


# ============================================================ M10 · _bloquear sin JOIN
def test_bloquear_no_le_mete_ningun_join_a_la_consulta_con_for_update():
    """M10. Con un LEFT OUTER JOIN de por medio Postgres rechaza el FOR UPDATE (0A000), y
    SQLite lo tolera —descarta el FOR UPDATE— así que ninguna prueba contra SQLite lo ve.
    `proveedor` y `transportador` son lazy="joined": sin su `lazyload` la consulta de
    `_bloquear` sale con dos LEFT JOIN. Se compila la sentencia para Postgres, sin base."""
    capturadas = []

    class _Resultado:
        def scalar_one(self):
            return "fila"

    class _Db:
        def execute(self, sentencia):
            capturadas.append(sentencia)
            return _Resultado()

    servicio._bloquear(_Db(), SimpleNamespace(id=uuid.uuid4()))

    sql = str(capturadas[0].compile(dialect=postgresql.dialect()))
    print(f"\n  SQL de _bloquear en Postgres:\n{sql}")
    assert "FOR UPDATE" in sql.upper()
    assert "JOIN" not in sql.upper(), sql


# ============================================================ M11 · aprobar y la deuda cobrada
def test_aprobar_un_borrador_cuya_deuda_ya_se_cobro_no_le_barre_el_anticipo(
    client, base_datos
):
    """M11. La quincena 1 queda en borrador debiendo $120.000 (leche $180.000 contra un adelanto
    de $300.000) y la quincena 2 YA se los cobró. Después entra otro anticipo de $50.000. Aprobar
    la 1 no puede barrerlo: su deuda pasaría a $170.000 y el comprobante de la 2, ya emitido,
    cobraría $50.000 de menos. El anticipo sigue suelto para la próxima liquidación."""
    h = auth_headers(client, "admin.a")
    prov = beto._proveedor(client, h, "Beto Borrador Cobrado")
    beto._recepcion(client, h, prov, "2026-06-02", "100")
    r = client.post(beto.ANT, json={"tipo": "proveedor", "proveedor_id": prov["id"],
                                    "fecha": "2026-06-01", "valor": "300000"}, headers=h)
    assert r.status_code == 201, r.text
    q1 = beto._generar(client, h, "2026-06-01", "2026-06-15", prov)
    assert q1["estado"] == "borrador" and D(q1["saldo"]) == D("-120000")
    beto._recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = beto._generar(client, h, "2026-06-16", "2026-06-30", prov)
    assert D(q2["saldo_anterior"]) == D("120000")
    assert client.get(f"{API}/{q1['id']}", headers=h).json()["deuda_trasladada_a_id"] == q2["id"]
    r = client.post(beto.ANT, json={"tipo": "proveedor", "proveedor_id": prov["id"],
                                    "fecha": "2026-06-10", "valor": "50000"}, headers=h)
    assert r.status_code == 201, r.text
    suelto = r.json()

    r = client.post(f"{API}/{q1['id']}/aprobar", headers=h)

    assert r.status_code == 200, r.text
    aprobada = client.get(f"{API}/{q1['id']}", headers=h).json()
    print(f"\n  q1 aprobada · anticipos={aprobada['anticipos']} "
          f"le_queda_debiendo={aprobada['le_queda_debiendo']}")
    assert aprobada["estado"] == "aprobada"
    assert D(aprobada["anticipos"]) == D("300000"), "se le barrió un anticipo a una deuda ya cobrada"
    assert D(aprobada["le_queda_debiendo"]) == D("120000")
    nuevo = client.get(f"{beto.ANT}/{suelto['id']}", headers=h).json()
    assert nuevo["aplicado"] is False and nuevo["liquidacion_id"] is None
    assert D(client.get(f"{API}/{q2['id']}", headers=h).json()["saldo_anterior"]) == D("120000")


# ============================================================ M12 · bloquear con filtro de empresa
@pytest.mark.parametrize(
    "metodo, ruta, cuerpo",
    [
        ("POST", "/{id}/recalcular", None),
        ("POST", "/{id}/aprobar", None),
        ("PUT", "/{id}/detalles/{dia}", {"precio_litro": "2000"}),
    ],
)
def test_el_candado_no_se_pone_sobre_la_fila_de_otra_empresa(
    client, base_datos, db_session, monkeypatch, metodo, ruta, cuerpo
):
    """M12. `_bloquear` relee por id y NO filtra por empresa: quien lo llama tiene que haber
    pasado antes por `get_or_fail`, que sí lo hace. Bloqueando primero con el id pelado, la
    petición de otra empresa tomaba el FOR UPDATE de una fila AJENA antes de rebotar con 404
    (y le pone a esperar a quien sí es dueño de la fila)."""
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    _, liq = _quincena(client, h_a)
    dia = _leer(client, h_a, liq["id"])["detalles"][0]["id"]
    candados = []
    bloquear = servicio._bloquear

    def _espia(db, liquidacion):
        candados.append(liquidacion.id)
        return bloquear(db, liquidacion)

    monkeypatch.setattr(servicio, "_bloquear", _espia)
    r = client.request(metodo, API + ruta.format(id=liq["id"], dia=dia), json=cuerpo, headers=h_b)
    monkeypatch.undo()

    print(f"\n  {metodo} {ruta} desde otra empresa -> {r.status_code}")
    assert r.status_code == 404, r.text
    assert candados == [], "se tomó el candado antes de comprobar que la fila es de esta empresa"
    assert _de_la_base(client, h_a, db_session, liq["id"])["estado"] == "borrador"


@pytest.mark.parametrize(
    "metodo, ruta, cuerpo",
    [
        ("POST", "/{id}/recalcular", None),
        ("POST", "/{id}/aprobar", None),
        ("PUT", "/{id}/detalles/{dia}", {"precio_litro": "2000"}),
    ],
)
def test_un_id_que_no_existe_es_404_y_no_se_intenta_bloquear(
    client, base_datos, monkeypatch, metodo, ruta, cuerpo
):
    """M12, el otro lado: con el candado antes de `get_or_fail`, un id que no existe reventaba
    con NoResultFound (500) en vez de 404."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    dia = _leer(client, h, liq["id"])["detalles"][0]["id"]
    candados = []
    bloquear = servicio._bloquear

    def _espia(db, liquidacion):
        candados.append(liquidacion.id)
        return bloquear(db, liquidacion)

    monkeypatch.setattr(servicio, "_bloquear", _espia)
    r = client.request(metodo, API + ruta.format(id=uuid.uuid4(), dia=dia), json=cuerpo, headers=h)
    monkeypatch.undo()

    assert r.status_code == 404, r.text
    assert candados == []


# ============================================================ M13 · el guardia usa lo que devuelve el candado
class _OtraInstancia:
    """Lo que devolvería `_bloquear` si entregara una instancia distinta de la que esta petición
    tenía en memoria. En producción es LA MISMA (el mapa de identidad + `populate_existing` la
    refresca en el sitio), y por eso usar la variable vieja pasa desapercibido; aquí se fuerza
    la diferencia. Delega todo en la fila real salvo lo que se le sobrescribe."""

    def __init__(self, real, **cambios):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_cambios", cambios)

    def __getattr__(self, nombre):
        cambios = object.__getattribute__(self, "_cambios")
        if nombre in cambios:
            return cambios[nombre]
        return getattr(object.__getattribute__(self, "_real"), nombre)


def test_el_guardia_del_precio_pregunta_con_lo_que_devuelve_el_candado(
    client, base_datos, monkeypatch
):
    """M13. El estado que cuenta es el que `_bloquear` devuelve, no el que había antes de
    esperar el turno. Si `_bloquear` entrega la fila releída como 'aprobada', el lápiz del día
    tiene que rebotar con ESO."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    dia = _leer(client, h, liq["id"])["detalles"][0]["id"]
    bloquear = servicio._bloquear
    monkeypatch.setattr(
        servicio, "_bloquear", lambda db, l: _OtraInstancia(bloquear(db, l), estado="aprobada")
    )

    r = client.put(f"{API}/{liq['id']}/detalles/{dia}", json={"precio_litro": "2000"}, headers=h)
    monkeypatch.undo()

    assert r.status_code == 422, r.text
    assert "'aprobada'" in _detalle(r) and "borrador" in _detalle(r)
    assert all(D(x["precio_litro"]) == D("1900") for x in _recepciones(client, h, prov))
    assert D(_leer(client, h, liq["id"])["valor_total"]) == A_1900


# ============================================================ M16 · registrar_pago
def test_abonar_ve_la_anulacion_que_entro_en_el_medio_y_no_le_paga_a_una_anulada(
    client, base_datos, db_session
):
    """M16. La petición tiene en memoria la quincena 'aprobada'; otro usuario la anuló por
    debajo. Con el candado, el abono relee 'anulada' y rebota; sin él, registraría un pago de
    plata sobre un comprobante que ya se dio de baja."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    vieja = _fila_vieja_con(db_session, liq["id"], estado="anulada")

    r = client.post(
        f"{API}/{liq['id']}/pagos", json={"fecha": "2026-07-31", "valor": "1000"}, headers=h
    )
    del vieja

    print(f"\n  abonar con la anulación en el medio: {r.status_code} · {_detalle(r) if r.status_code != 200 else ''}")
    assert r.status_code == 422, r.text
    assert "'anulada'" in _detalle(r)
    db_session.expire_all()
    assert db_session.scalars(
        select(PagoLiquidacion).where(PagoLiquidacion.liquidacion_id == uuid.UUID(liq["id"]))
    ).all() == []
    despues = _de_la_base(client, h, db_session, liq["id"])
    assert despues["estado"] == "anulada" and D(despues["pagado"]) == D("0")


# ============================================================ M17 · anular
def test_anular_ve_el_pago_que_entro_en_el_medio_y_no_anula_una_pagada(
    client, base_datos, db_session
):
    """M17. La petición tiene en memoria la quincena 'aprobada'; otro usuario la pagó por
    debajo. Con el candado, anular relee 'pagada' y rebota; sin él, anula una quincena cuya
    plata ya salió (y suelta sus días y anticipos para volver a cobrarlos)."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    vieja = _fila_vieja_con(db_session, liq["id"], estado="pagada")

    r = client.post(f"{API}/{liq['id']}/anular", headers=h)
    del vieja

    print(f"\n  anular con el pago en el medio: {r.status_code}")
    assert r.status_code == 422, r.text
    assert "ya pagada" in _detalle(r)
    assert _de_la_base(client, h, db_session, liq["id"])["estado"] == "pagada"


# ============================================================ M18 · pagar
def test_pagar_con_saldo_en_cero_ve_la_anulacion_del_medio_y_no_la_pasa_a_pagada(
    client, base_datos, db_session
):
    """M18. El camino de `pagar` que NO pasa por `registrar_pago` (y por tanto por su candado):
    los anticipos se comieron EXACTO la quincena, el saldo es cero y solo se cambia el estado.
    La petición tiene en memoria 'aprobada'; otro usuario la anuló por debajo. Con el candado
    de `pagar`, el guardia relee 'anulada' y rebota; sin él, `_transicionar` escribe 'pagada'
    ENCIMA de la anulación (una quincena dada de baja, con sus días ya sueltos, queda pagada)."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    _anticipo(client, h, prov, "2026-07-20", int(A_1900))
    aprobada = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert aprobada.status_code == 200, aprobada.text
    assert D(aprobada.json()["saldo"]) == D("0"), "el anticipo debía cubrir exacto la quincena"
    vieja = _fila_vieja_con(db_session, liq["id"], estado="anulada")

    r = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    del vieja

    print(f"\n  pagar con la anulación en el medio: {r.status_code} · {_detalle(r) if r.status_code != 200 else ''}")
    assert r.status_code == 422, r.text
    assert "No se puede pasar de 'anulada' a 'pagada'" in _detalle(r)
    assert _de_la_base(client, h, db_session, liq["id"])["estado"] == "anulada"
