"""PRUEBAS PARA LOS MUTANTES QUE SOBREVIVIERON al manejador del 409 de concurrencia.

Cada prueba nombra el mutante que mata. Todas pasan con el código original.

  C04  el manejador lee `exc.orig.pgcode` directo, sin `getattr`: un error de base de datos SIN
       código revienta DENTRO del manejador, el 500 lo termina dando el manejador general de
       Starlette (mismo status, mismo cuerpo, por eso la prueba que miraba el status no lo notaba)
       pero la excepción se RELANZA al servidor, que la loguea otra vez como "Exception in ASGI
       application"
  C07  el texto del 409 cambia pero conserva "vuelva a intentarlo": ya no dice la verdad (que no
       se perdió ni se movió nada) y la prueba que comparaba contra la misma constante no se entera
  C08  otro error de la base de datos deja de pasar por el manejador general: sigue saliendo un 500
       con el mismo cuerpo, pero NADIE LO REGISTRA en el log y nadie se entera de que se cayó algo

Se arma igual que en test_concurrencia_409.py: SQLite no produce 40P01, así que el error se
levanta a mano con el `pgcode` que psycopg2 pone en `orig`.
"""
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core import exceptions as excepciones
from app.main import app as fastapi_app
from tests.conftest import auth_headers
from tests.test_concurrencia_409 import _ErrorDePostgres, _levantar
from tests.test_liquidacion_precio_varios_dias import API, _quincena


# ============================================================ C04 · un error sin código lo resuelve el manejador entero
def test_un_error_de_base_de_datos_sin_codigo_lo_resuelve_el_manejador_sin_relanzarlo(
    client, base_datos, monkeypatch
):
    """C04. Una conexión caída no trae `pgcode`. El manejador tiene que preguntar con `getattr`
    y pasarlo al 500 general: ahí queda RESUELTO, con el cuerpo de siempre y sin que nada suba
    al servidor. Si en cambio revienta él mismo con un AttributeError, el 500 lo termina dando
    el manejador de último recurso de Starlette —mismo status, mismo cuerpo—, pero esa capa
    RELANZA la excepción para que uvicorn la imprima otra vez como "Exception in ASGI
    application". Se usa el cliente normal (el que relanza lo que le llegue) y no el de
    `raise_server_exceptions=False`, que esconde justamente eso."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    _levantar(monkeypatch, OperationalError("SELECT 1", {}, Exception("se cayó la conexión")))

    try:
        r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    except Exception as escapo:  # noqa: BLE001 - justo lo que se busca atrapar
        pytest.fail(f"el manejador dejó escapar {type(escapo).__name__} hacia el servidor: {escapo}")

    assert r.status_code == 500, r.text
    assert r.json() == {"error": {"code": "internal_error", "detail": "Error interno del servidor"}}


# ============================================================ C07 · el texto del 409 dice la verdad
@pytest.mark.parametrize("pgcode", ["40P01", "40001"])
def test_el_409_dice_que_no_se_perdio_nada_y_que_hay_que_volver_a_intentar(
    client, base_datos, monkeypatch, pgcode
):
    """C07. La frase "No se perdió ni se movió nada" es lo que hace que el usuario vuelva a
    intentarlo sin miedo: es verdad porque Postgres revierte la transacción entera. Se compara
    contra el texto escrito y no contra `MENSAJE_DE_CONCURRENCIA`, porque comparar la constante
    contra sí misma no detecta que alguien la cambie por una frase que miente."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    _levantar(monkeypatch, OperationalError("UPDATE liquidaciones ...", {}, _ErrorDePostgres(pgcode)))

    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)

    assert r.status_code == 409, r.text
    detalle = r.json()["error"]["detail"]
    print(f"\n  detalle del 409 ({pgcode}): {detalle}")
    assert detalle.startswith("Otra persona estaba cambiando lo mismo en el mismo momento")
    assert "No se perdió ni se movió nada" in detalle
    assert detalle.endswith("vuelva a intentarlo.")


# ============================================================ C08 · el 500 de otro error queda en el log
def test_otro_error_de_la_base_de_datos_se_registra_como_error_no_controlado(
    client, base_datos, monkeypatch
):
    """C08. Un 500 sin rastro es el peor: el usuario ve "Error interno del servidor" y en el log
    no hay nada que buscar. El manejador de concurrencia no se come los demás errores de la base
    de datos: los pasa al manejador general, que los registra con `logger.exception`."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    _levantar(monkeypatch, IntegrityError("INSERT", {}, _ErrorDePostgres("23505")))

    with mock.patch.object(excepciones.logger, "exception") as registrar:
        with TestClient(fastapi_app, raise_server_exceptions=False) as sin_relanzar:
            r = sin_relanzar.post(f"{API}/{liq['id']}/aprobar", headers=h)

    assert r.status_code == 500, r.text
    registrar.assert_called_once()
    assert registrar.call_args.args[1:] == ("POST", f"{API}/{liq['id']}/aprobar")


def test_un_conflicto_de_concurrencia_se_registra_como_aviso_y_no_como_error(
    client, base_datos, monkeypatch
):
    """El otro lado de C08: el abrazo mortal NO es un error del servidor, es una carrera perdida
    que se reintenta. Queda como aviso con su código (40P01) y NO dispara el `logger.exception`
    del 500, que haría saltar las alarmas por algo que ya se resolvió solo."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    _levantar(monkeypatch, OperationalError("UPDATE", {}, _ErrorDePostgres("40P01")))

    with mock.patch.object(excepciones.logger, "exception") as registrar, mock.patch.object(
        excepciones.logger, "warning"
    ) as avisar:
        r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)

    assert r.status_code == 409, r.text
    registrar.assert_not_called()
    avisar.assert_called_once()
    assert avisar.call_args.args[1:] == ("40P01", "POST", f"{API}/{liq['id']}/aprobar")
