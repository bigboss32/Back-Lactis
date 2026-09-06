"""AUDITORÍA — en esta instalación hay DOS empresas. Se mide qué alcanza el
administrador de una sobre los usuarios de la otra.

Cuando se escribió, los roles eran GLOBALES (Rol no tenía empresa_id) y el
administrador de A le ampliaba los permisos al rol de B. Ya no: los roles de
sistema son plantillas que nadie edita desde una empresa, y los roles de una
quesera solo los ve y los toca esa quesera."""
from sqlalchemy import select

from app.modules.usuarios.models import Permiso, Rol
from tests.conftest import auth_headers
from tests.test_zzaudit_puertas_por_rol import usuario_con_rol


def test_admin_de_una_empresa_no_le_amplia_los_permisos_al_usuario_de_la_otra(
    client, base_datos, db_session
):
    """El usuario de reventa de la EMPRESA B arranca sin poder ver recepciones.

    El administrador de la EMPRESA A intenta agregarle 'recepcion:consultar' al
    rol 'Reventa' —que es la plantilla compartida— y ya no puede: le responde
    403 y el usuario de la empresa B sigue exactamente igual que antes.
    """
    usuario_con_rol(db_session, base_datos["empresa_b"].id, "rev.b", "Reventa")
    hb = auth_headers(client, "rev.b")
    assert client.get("/api/v1/recepciones", headers=hb).status_code == 403

    ha = auth_headers(client, "admin.a")
    rol = db_session.scalars(select(Rol).where(Rol.nombre == "Reventa")).first()
    permisos = db_session.scalars(select(Permiso)).all()
    actuales = [str(p.id) for p in rol.permisos]
    recepcion_consultar = next(
        str(p.id) for p in permisos if (p.modulo, p.accion) == ("recepcion", "consultar")
    )
    r = client.put(
        f"/api/v1/roles/{rol.id}/permisos",
        json={"permiso_ids": actuales + [recepcion_consultar]},
        headers=ha,
    )
    print("\nA intenta ampliarle los permisos a la plantilla 'Reventa' ->",
          r.status_code, r.text[:160])
    assert r.status_code == 403, "el admin de A todavía le cambia los permisos a la plantilla"

    hb2 = auth_headers(client, "rev.b")
    despues = client.get("/api/v1/recepciones", headers=hb2)
    assert despues.status_code == 403, (
        "EL ADMIN DE LA EMPRESA A LE ABRIÓ RECEPCIÓN AL USUARIO DE LA EMPRESA B: "
        f"HTTP {despues.status_code}"
    )


def test_admin_de_una_empresa_no_toca_a_la_otra_empresa(client, base_datos):
    """Control: lo que SÍ está cerrado en empresas."""
    ha = auth_headers(client, "admin.a")
    b = str(base_datos["empresa_b"].id)
    assert client.get(f"/api/v1/empresas/{b}", headers=ha).status_code == 403
    assert client.put(f"/api/v1/empresas/{b}", json={"nombre": "Mía"}, headers=ha).status_code == 403
    assert client.delete(f"/api/v1/empresas/{b}", headers=ha).status_code == 403
    assert client.post(f"/api/v1/empresas/{b}/reiniciar",
                       json={"confirmacion": "Quesera B"}, headers=ha).status_code == 403
    assert client.put(f"/api/v1/empresas/{b}/suscripcion",
                      json={"exenta": True}, headers=ha).status_code == 403
    # tampoco la suya propia: la exención y la vigencia son del superadmin
    a = str(base_datos["empresa_a"].id)
    assert client.put(f"/api/v1/empresas/{a}/suscripcion",
                      json={"exenta": True}, headers=ha).status_code == 403
    # y el PUT normal ignora los campos de suscripción aunque se cuelen
    r = client.put(f"/api/v1/empresas/{a}",
                   json={"nombre": "Quesera A", "exenta": True,
                         "pagada_hasta": "2099-01-01", "tarifa_mensual": "0"},
                   headers=ha)
    assert r.status_code == 200, r.text
    assert r.json()["exenta"] is False
    assert r.json()["pagada_hasta"] != "2099-01-01"


def test_el_usuario_de_una_empresa_no_le_cambia_la_clave_al_de_la_otra(client, base_datos):
    ha = auth_headers(client, "admin.a")
    otro = str(base_datos["admin_b"].id)
    r = client.post(f"/api/v1/usuarios/{otro}/restablecer-password",
                    json={"password": "Nueva1234*"}, headers=ha)
    assert r.status_code == 404, (r.status_code, r.text[:200])
    r = client.put(f"/api/v1/usuarios/{otro}", json={"nombre": "Cambiado"}, headers=ha)
    assert r.status_code == 404, (r.status_code, r.text[:200])
    r = client.post(f"/api/v1/usuarios/{otro}/bloquear", json={}, headers=ha)
    assert r.status_code == 404, (r.status_code, r.text[:200])
