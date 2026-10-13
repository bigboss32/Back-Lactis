"""UN ABRAZO MORTAL O UN FALLO DE SERIALIZACIÓN ES UN 409 QUE DICE QUÉ HACER, NO UN 500.

Con candados de fila (`_bloquear`) dos peticiones que toman lo mismo en orden contrario pueden
esperarse entre sí; Postgres tumba a una con 40P01 y revierte su transacción entera. Antes salía
un "Error interno del servidor" (500). Ahora sale un 409 con un texto verdadero —no se perdió ni
se movió nada— y la pantalla lo muestra tal cual. Cualquier OTRO error de la base de datos sigue
siendo el 500 de siempre: no se esconde un error de verdad detrás de un "vuelva a intentar".

SQLite no produce 40P01, así que el error se arma a mano con el `pgcode` que psycopg2 pone en
`orig`.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.exceptions import MENSAJE_DE_CONCURRENCIA
from app.main import app as fastapi_app
from app.modules.liquidaciones.service import LiquidacionService
from tests.conftest import auth_headers
from tests.test_liquidacion_precio_varios_dias import API, _quincena


class _ErrorDePostgres(Exception):
    def __init__(self, pgcode):
        super().__init__(f"error de postgres {pgcode}")
        self.pgcode = pgcode


def _levantar(monkeypatch, error):
    def _aprobar(self, entity_id):
        raise error

    monkeypatch.setattr(LiquidacionService, "aprobar", _aprobar)


@pytest.mark.parametrize("pgcode", ["40P01", "40001"])
def test_un_abrazo_mortal_o_un_fallo_de_serializacion_es_un_409_que_dice_que_reintente(
    client, base_datos, monkeypatch, pgcode
):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    _levantar(monkeypatch, OperationalError("UPDATE liquidaciones ...", {}, _ErrorDePostgres(pgcode)))

    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)

    assert r.status_code == 409, r.text
    assert r.json() == {"error": {"code": "concurrency_conflict", "detail": MENSAJE_DE_CONCURRENCIA}}
    assert "vuelva a intentarlo" in r.json()["error"]["detail"]


def test_la_transaccion_se_revierte_y_la_quincena_queda_como_estaba(client, base_datos, monkeypatch, db_session):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    _levantar(monkeypatch, OperationalError("UPDATE", {}, _ErrorDePostgres("40P01")))

    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 409

    monkeypatch.undo()
    db_session.expire_all()
    assert client.get(f"{API}/{liq['id']}", headers=h).json()["estado"] == "borrador"


def test_otro_error_de_la_base_de_datos_sigue_siendo_un_500_y_no_un_vuelva_a_intentar(
    client, base_datos, monkeypatch
):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    _levantar(monkeypatch, IntegrityError("INSERT", {}, _ErrorDePostgres("23505")))

    with TestClient(fastapi_app, raise_server_exceptions=False) as sin_relanzar:
        r = sin_relanzar.post(f"{API}/{liq['id']}/aprobar", headers=h)

    assert r.status_code == 500, r.text
    assert r.json()["error"]["code"] == "internal_error"
    assert "vuelva a intentar" not in r.text


def test_un_error_de_base_de_datos_sin_codigo_tampoco_se_disfraza_de_conflicto(
    client, base_datos, monkeypatch
):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    _levantar(monkeypatch, OperationalError("SELECT 1", {}, Exception("se cayó la conexión")))

    with TestClient(fastapi_app, raise_server_exceptions=False) as sin_relanzar:
        r = sin_relanzar.post(f"{API}/{liq['id']}/aprobar", headers=h)

    assert r.status_code == 500, r.text
