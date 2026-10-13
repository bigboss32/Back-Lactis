"""PRUEBAS PARA LOS MUTANTES QUE SOBREVIVIERON en la segunda vuelta de mutación del candado.

Cada prueba nombra el mutante que mata. Todas pasan con el código original.

  M20  `_bloquear` sin `lazyload(Liquidacion.deuda_trasladada_a)` / `lazyload(Liquidacion.deudas_cobradas)`:
       la petición se queda con las dos puntas de la deuda que ya había cargado, aunque otra
       persona las haya cambiado por debajo
  X03  recalcular pregunta el guardia con la variable de antes del candado y no con la que devuelve
       `_bloquear` (igual que M13 pero en Recalcular)
  X04  aprobar decide si barre los anticipos con la variable de antes del candado

SQLite descarta el FOR UPDATE en silencio, así que (igual que en
test_liquidacion_candado_sobrevivientes.py) lo que se mide es lo que el código PIDE y con qué
objeto decide, y que la petición relea lo que otro cambió por debajo, no el bloqueo en sí.
"""
import uuid
from decimal import Decimal

from sqlalchemy import update

from app.modules.liquidaciones import service as servicio
from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import LiquidacionService
from tests import test_liquidacion_anticipo_candado_deuda_cobrada as beto
from tests.conftest import auth_headers
from tests.test_liquidacion_anticipos_pendientes import _anticipo
from tests.test_liquidacion_candado_sobrevivientes import _OtraInstancia
from tests.test_liquidacion_precio_varios_dias import (
    A_1900,
    API,
    D,
    _detalle,
    _leer,
    _quincena,
)


# ============================================================ M20 · las dos puntas de la deuda también se releen
def test_bloquear_relee_las_dos_puntas_de_la_deuda_que_la_peticion_ya_tenia_cargadas(
    client, base_datos, db_session
):
    """M20. `deuda_trasladada_a` y `deudas_cobradas` NO se cargan solas con la fila (son
    autorreferenciadas: SQLAlchemy no las trae desde la raíz), pero la petición las puede tener
    ya en la mano si las tocó antes del candado. `populate_existing` por sí solo no las suelta;
    el `lazyload` de `_bloquear` sí: las deja para releerse de la base cuando alguien las pida.
    Sin él, tras esperar el turno la petición sigue viendo una deuda ya soltada: nombra en el
    aviso una quincena que ya no la cobra, o cuenta como cobrada una que otra persona liberó.

    La quincena 1 quedó debiendo $120.000 y la 2 se los cobró (las dos puntas quedan cargadas
    en memoria). Por debajo, otra persona anula la 2: la 1 vuelve a quedar sin cobrar. Con el
    candado, ninguna de las dos filas conserva la punta vieja."""
    h = auth_headers(client, "admin.a")
    prov = beto._proveedor(client, h, "Beto Dos Puntas")
    beto._recepcion(client, h, prov, "2026-06-02", "100")
    r = client.post(beto.ANT, json={"tipo": "proveedor", "proveedor_id": prov["id"],
                                    "fecha": "2026-06-01", "valor": "300000"}, headers=h)
    assert r.status_code == 201, r.text
    q1 = beto._generar(client, h, "2026-06-01", "2026-06-15", prov)
    beto._recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = beto._generar(client, h, "2026-06-16", "2026-06-30", prov)
    assert D(q2["saldo_anterior"]) == D("120000")

    db_session.expire_all()
    origen = db_session.get(Liquidacion, uuid.UUID(q1["id"]))
    destino = db_session.get(Liquidacion, uuid.UUID(q2["id"]))
    assert origen.deuda_trasladada_a is destino, "la punta de q1 debía apuntar a q2"
    assert [o.id for o in destino.deudas_cobradas] == [origen.id], "q2 debía cobrarle a q1"

    tabla = Liquidacion.__table__
    db_session.execute(update(tabla).where(tabla.c.id == origen.id).values(deuda_trasladada_a_id=None))
    db_session.commit()

    servicio._bloquear(db_session, origen)
    servicio._bloquear(db_session, destino)

    print(f"\n  tras el candado: q1.deuda_trasladada_a={origen.deuda_trasladada_a} "
          f"q2.deudas_cobradas={destino.deudas_cobradas}")
    assert origen.deuda_trasladada_a_id is None
    assert origen.deuda_trasladada_a is None, "q1 sigue creyendo que q2 le cobró la deuda"
    assert destino.deudas_cobradas == [], "q2 sigue creyendo que le cobró la deuda a q1"


# ============================================================ X03 · recalcular usa lo que devuelve el candado
def test_el_guardia_de_recalcular_pregunta_con_lo_que_devuelve_el_candado(
    client, base_datos, monkeypatch
):
    """X03. Igual que M13 pero en Recalcular: el estado que cuenta es el que `_bloquear` devuelve,
    no el que había antes de esperar el turno. Si `_bloquear` entrega la fila releída como
    'aprobada', Recalcular tiene que rebotar con ESO. Con la variable de antes del candado
    preguntaría con la foto vieja ('borrador'), seguiría y reescribiría una quincena que ya salió."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    bloquear = servicio._bloquear
    monkeypatch.setattr(
        servicio, "_bloquear", lambda db, l: _OtraInstancia(bloquear(db, l), estado="aprobada")
    )

    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    monkeypatch.undo()

    assert r.status_code == 422, r.text
    assert "'aprobada'" in _detalle(r) and "borrador" in _detalle(r)
    assert D(_leer(client, h, liq["id"])["valor_total"]) == A_1900


# ============================================================ X04 · aprobar usa lo que devuelve el candado
def test_aprobar_decide_si_barre_los_anticipos_con_lo_que_devuelve_el_candado(
    client, base_datos, monkeypatch
):
    """X04. Aprobar decide si barre los anticipos pendientes mirando el estado y la deuda de la
    fila que devolvió `_bloquear` (releída DESPUÉS de esperar el turno), y se la pasa tal cual a
    `_aplicar_anticipos_pendientes`, que escribe el neto y el saldo con el `valor_total` de ESA
    fila. Con la variable de antes del candado se barrería con la foto vieja."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    _anticipo(client, h, prov, "2026-07-20", 100000)
    bloquear = servicio._bloquear
    entregadas = []
    devueltas = []

    def _bloquear(db, liquidacion):
        devuelta = _OtraInstancia(bloquear(db, liquidacion))
        devueltas.append(devuelta)
        return devuelta

    def _espia(self, liquidacion):
        entregadas.append(liquidacion)
        return Decimal("0")

    monkeypatch.setattr(servicio, "_bloquear", _bloquear)
    monkeypatch.setattr(LiquidacionService, "_aplicar_anticipos_pendientes", _espia)
    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    monkeypatch.undo()

    assert r.status_code == 200, r.text
    assert len(entregadas) == 1 and len(devueltas) == 1
    assert entregadas[0] is devueltas[0], "aprobar barrió con la fila de antes del candado"
