"""AUDITORÍA — el paywall: con la mensualidad vencida y pasada la gracia, ¿qué
sigue abierto? Y con la suscripción al día, ¿se le cobra a quien no debe?"""
from datetime import date, timedelta

from tests.conftest import auth_headers
from tests.test_zzaudit_puertas_por_rol import PUERTAS_PROHIBIDAS, usuario_con_rol


def vencer(db_session, empresa, dias_vencidos):
    empresa.pagada_hasta = date.today() - timedelta(days=dias_vencidos)
    db_session.commit()


DEBE_SEGUIR_ABIERTO = [
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/suscripcion"),
    ("GET", "/api/v1/suscripcion/pagos"),
]


def test_bloqueada_cierra_todo_menos_la_puerta_de_pagar(client, base_datos, db_session):
    ha = auth_headers(client, "admin.a")
    vencer(db_session, base_datos["empresa_a"], 60)  # gracia por defecto muy menor

    for metodo, ruta in DEBE_SEGUIR_ABIERTO:
        r = client.request(metodo, ruta, headers=ha)
        assert r.status_code == 200, (ruta, r.status_code, r.text[:200])

    abiertas = []
    for metodo, ruta, body in PUERTAS_PROHIBIDAS:
        r = client.request(metodo, ruta, json=body, headers=ha)
        if r.status_code == 403 and r.json().get("error", {}).get("code") == "suscripcion_vencida":
            continue
        abiertas.append((metodo, ruta, r.status_code,
                         r.json().get("error", {}).get("code") if r.headers.get("content-type", "").startswith("application/json") else ""))
    assert not abiertas, "CON LA SUSCRIPCIÓN VENCIDA SIGUEN PASANDO:\n" + "\n".join(map(str, abiertas))


def test_bloqueada_tampoco_deja_entrar_al_de_reventa_a_su_modulo(client, base_datos, db_session):
    usuario_con_rol(db_session, base_datos["empresa_a"].id, "rev.a", "Reventa")
    vencer(db_session, base_datos["empresa_a"], 60)
    h = auth_headers(client, "rev.a")
    r = client.get("/api/v1/reventa/compras", headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "suscripcion_vencida", r.text
    # pero sí a pagar y a cambiarse la clave
    assert client.get("/api/v1/suscripcion", headers=h).status_code == 200
    assert client.post("/api/v1/auth/cambiar-password",
                       json={"password_actual": "Clave1234*", "password_nueva": "Clave5678*"},
                       headers=h).status_code == 200


def test_en_gracia_todavia_trabaja(client, base_datos, db_session):
    """Vencida por poco: se avisa, no se cierra. Si esto cerrara, el dueño
    perdería la recepción del día por un retraso de un día en el pago."""
    ha = auth_headers(client, "admin.a")
    vencer(db_session, base_datos["empresa_a"], 1)
    assert client.get("/api/v1/recepciones", headers=ha).status_code == 200
    assert client.get("/api/v1/liquidaciones", headers=ha).status_code == 200
