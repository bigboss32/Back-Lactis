"""AUDITORÍA — quién puede hacer qué. No arregla nada: mide.

Se entra POR EL ENDPOINT, nunca por el menú: el frontend esconde botones, la
API es la que manda.
"""
import uuid as _uuid

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.modules.usuarios.models import Rol, Usuario
from tests.conftest import PASSWORD, auth_headers


def usuario_con_rol(db_session, empresa_id, username, rol_nombre):
    rol = db_session.scalars(select(Rol).where(Rol.nombre == rol_nombre)).first()
    assert rol is not None, f"no existe el rol {rol_nombre}"
    u = Usuario(
        nombre=username,
        apellido="Auditoria",
        correo=f"{username}@audit.local",
        username=username,
        hashed_password=hash_password(PASSWORD),
        empresa_id=empresa_id,
    )
    u.empresa_id = empresa_id
    u.roles = [rol]
    db_session.add(u)
    db_session.flush()
    db_session.commit()
    return u


# --------------------------------------------------------------- 1. REVENTA
# El usuario de reventa solo debe ver SU negocio y Administración (suscripción).
PUERTAS_PROHIBIDAS = [
    ("GET", "/api/v1/recepciones", None),
    ("GET", "/api/v1/recepciones/grilla/quincena?anio=2026&mes=7&quincena=1", None),
    ("POST", "/api/v1/recepciones", {"proveedor_id": str(_uuid.uuid4()), "fecha": "2026-07-01", "litros": "137.45"}),
    ("GET", "/api/v1/liquidaciones", None),
    ("POST", "/api/v1/liquidaciones/generar", {"tipo": "proveedor", "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15"}),
    ("POST", "/api/v1/liquidaciones/previsualizar", {"tipo": "proveedor", "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15"}),
    ("POST", "/api/v1/liquidaciones/previsualizar/pdf", {"tipo": "proveedor", "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15"}),
    ("GET", f"/api/v1/liquidaciones/{_uuid.uuid4()}/pdf", None),
    ("POST", f"/api/v1/liquidaciones/{_uuid.uuid4()}/aprobar", {}),
    ("POST", f"/api/v1/liquidaciones/{_uuid.uuid4()}/pagar", {}),
    ("POST", f"/api/v1/liquidaciones/{_uuid.uuid4()}/anular", {}),
    ("POST", f"/api/v1/liquidaciones/{_uuid.uuid4()}/pagos", {"valor": "242.76", "fecha": "2026-07-20"}),
    ("GET", "/api/v1/anticipos", None),
    ("GET", "/api/v1/anticipos/totales/suma", None),
    ("POST", "/api/v1/anticipos", {"tipo": "proveedor", "proveedor_id": str(_uuid.uuid4()), "fecha": "2026-07-01", "valor": "242.76"}),
    ("GET", "/api/v1/proveedores", None),
    ("GET", "/api/v1/transportadores", None),
    ("GET", "/api/v1/rutas", None),
    ("GET", "/api/v1/clientes", None),
    ("GET", "/api/v1/empleados", None),
    ("GET", "/api/v1/nomina", None),
    ("POST", "/api/v1/nomina", {"empleado_id": str(_uuid.uuid4()), "fecha": "2026-07-15", "dias_trabajados": "12.5"}),
    ("GET", f"/api/v1/nomina/{_uuid.uuid4()}/pdf", None),
    ("GET", "/api/v1/produccion", None),
    ("GET", "/api/v1/produccion/lotes", None),
    ("GET", "/api/v1/inventario/productos", None),
    ("GET", "/api/v1/inventario/productos/stock/actual", None),
    ("GET", "/api/v1/ventas", None),
    ("GET", "/api/v1/ventas/cartera", None),
    ("GET", "/api/v1/caja", None),
    ("GET", "/api/v1/bancos/cuentas", None),
    ("GET", "/api/v1/gastos", None),
    ("GET", "/api/v1/contabilidad/balance?desde=2026-07-01&hasta=2026-07-31", None),
    ("GET", "/api/v1/contabilidad/estado-resultados?desde=2026-07-01&hasta=2026-07-31", None),
    ("GET", "/api/v1/contabilidad/libro-diario?desde=2026-07-01&hasta=2026-07-31", None),
    ("GET", "/api/v1/reportes/dashboard", None),
    ("GET", "/api/v1/auditoria", None),
    ("GET", "/api/v1/auditoria/logins", None),
    ("GET", "/api/v1/usuarios", None),
    ("GET", "/api/v1/roles", None),
    ("GET", "/api/v1/permisos", None),
    ("GET", "/api/v1/empresas", None),
    ("GET", "/api/v1/sucursales", None),
    ("GET", "/api/v1/transporte/viajes", None),
    ("GET", "/api/v1/transporte/cartera", None),
    ("GET", "/api/v1/transporte/vehiculos", None),
    ("GET", "/api/v1/transporte/resumen-mensual?anio=2026&mes=7", None),
    ("POST", f"/api/v1/usuarios/{_uuid.uuid4()}/restablecer-password", {"password": "Otra1234*"}),
    ("PUT", f"/api/v1/roles/{_uuid.uuid4()}/permisos", {"permiso_ids": []}),
]


def test_reventa_no_alcanza_nada_fuera_de_su_negocio(client, base_datos, db_session):
    usuario_con_rol(db_session, base_datos["empresa_a"].id, "rev.a", "Reventa")
    h = auth_headers(client, "rev.a")
    fugas = []
    for metodo, ruta, body in PUERTAS_PROHIBIDAS:
        r = client.request(metodo, ruta, json=body, headers=h)
        if r.status_code != 403:
            fugas.append((metodo, ruta, r.status_code, r.text[:160]))
    assert not fugas, "PUERTAS ABIERTAS AL ROL REVENTA:\n" + "\n".join(map(str, fugas))


def test_reventa_si_alcanza_lo_suyo_y_la_suscripcion(client, base_datos, db_session):
    usuario_con_rol(db_session, base_datos["empresa_a"].id, "rev.a", "Reventa")
    h = auth_headers(client, "rev.a")
    for ruta in ("/api/v1/reventa/compras", "/api/v1/reventa/ventas",
                 "/api/v1/reventa/productos", "/api/v1/suscripcion",
                 "/api/v1/suscripcion/pagos", "/api/v1/notificaciones"):
        r = client.get(ruta, headers=h)
        assert r.status_code == 200, (ruta, r.status_code, r.text[:200])


def test_reventa_no_cruza_a_la_otra_empresa(client, base_datos, db_session):
    usuario_con_rol(db_session, base_datos["empresa_a"].id, "rev.a", "Reventa")
    h = auth_headers(client, "rev.a")
    r = client.get(
        "/api/v1/reventa/compras",
        headers={**h, "X-Empresa-Id": str(base_datos["empresa_b"].id)},
    )
    assert r.status_code == 403, r.text


# ------------------- 2. EL BORRADO DE UN ABONO DE FLETE PIDE 'crear'
def test_auxiliar_sin_permiso_de_eliminar_borra_un_abono_de_flete(client, base_datos, db_session):
    """El rol Auxiliar tiene transporte:crear y transporte:consultar, y NO
    transporte:eliminar. Aun así borra un abono ya recibido, porque el DELETE
    de /transporte/servicios/{id}/abonos/{id} pide 'crear'."""
    admin = auth_headers(client, "admin.a")
    veh = client.post(
        "/api/v1/transporte/vehiculos",
        json={"placa": "AUD123", "nombre": "La Turbo", "tarifa_kilo": "1200"},
        headers=admin,
    ).json()
    viaje = client.post(
        "/api/v1/transporte/viajes",
        json={"vehiculo_id": veh["id"], "fecha_salida": "2026-07-10",
              "origen": "San José", "destino": "Villavicencio"},
        headers=admin,
    ).json()
    cliente = client.post("/api/v1/clientes", json={"nombre": "Alba Ricaute"}, headers=admin).json()
    servicio = client.post(
        f"/api/v1/transporte/viajes/{viaje['id']}/servicios",
        json={"descripcion": "Carga de queso", "tipo_cobro": "precio_fijo",
              "cliente_id": cliente["id"], "valor_total": "242760.00"},
        headers=admin,
    ).json()
    r = client.post(
        f"/api/v1/transporte/servicios/{servicio['id']}/abonos",
        json={"fecha": "2026-07-12", "valor": "137450.00"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    abono_id = r.json()["abonos"][0]["id"]
    assert float(r.json()["saldo"]) == 105310.0  # 242.760,00 - 137.450,00

    usuario_con_rol(db_session, base_datos["empresa_a"].id, "aux.a", "Auxiliar")
    aux = auth_headers(client, "aux.a")
    perfil = client.get("/api/v1/auth/me", headers=aux).json()
    assert "transporte:eliminar" not in perfil["permisos"]
    assert "transporte:crear" in perfil["permisos"]

    # No puede borrar el servicio (eso sí pide 'eliminar')
    assert client.delete(
        f"/api/v1/transporte/viajes/{viaje['id']}/servicios/{servicio['id']}", headers=aux
    ).status_code == 403

    cartera_antes = client.get("/api/v1/transporte/cartera", headers=admin).json()
    borrado = client.delete(
        f"/api/v1/transporte/servicios/{servicio['id']}/abonos/{abono_id}", headers=aux
    )
    cartera_despues = client.get("/api/v1/transporte/cartera", headers=admin).json()
    print("CARTERA ANTES :", cartera_antes)
    print("CARTERA DESPUES:", cartera_despues)
    assert borrado.status_code == 403, (
        f"BORRÓ EL ABONO SIN PERMISO DE ELIMINAR: HTTP {borrado.status_code}; "
        f"saldo tras el borrado = {borrado.json().get('saldo')}"
    )


# ------------------------- 3. EL PDF DE NÓMINA NO PIDE 'imprimir'
def test_el_recibo_de_nomina_es_el_unico_pdf_que_no_pide_imprimir(client, base_datos, db_session):
    admin = auth_headers(client, "admin.a")
    emp = client.post("/api/v1/empleados",
                      json={"nombre": "Aurelio", "apellido": "Ricaute", "valor_dia": "41833.33"},
                      headers=admin).json()
    pago = client.post("/api/v1/nomina",
                       json={"empleado_id": emp["id"], "fecha": "2026-07-15",
                             "dias_trabajados": "12.5"},
                       headers=admin).json()

    usuario_con_rol(db_session, base_datos["empresa_a"].id, "solo.consulta", "Consulta")
    h = auth_headers(client, "solo.consulta")
    perfil = client.get("/api/v1/auth/me", headers=h).json()
    assert "empleados:consultar" in perfil["permisos"]
    assert "empleados:imprimir" not in perfil["permisos"]
    assert "liquidaciones:imprimir" not in perfil["permisos"]

    # El PDF de la liquidación sí exige 'imprimir'
    import uuid as _u
    assert client.get(f"/api/v1/liquidaciones/{_u.uuid4()}/pdf", headers=h).status_code == 403

    r = client.get(f"/api/v1/nomina/{pago['id']}/pdf", headers=h)
    assert r.status_code == 403, (
        f"el recibo de nómina salió con solo 'consultar': HTTP {r.status_code}"
    )


# ------------------------- 4. usuarios y roles: nadie llega sin su permiso
def test_auxiliar_no_administra_usuarios_ni_roles(client, base_datos, db_session):
    usuario_con_rol(db_session, base_datos["empresa_a"].id, "aux2.a", "Auxiliar")
    h = auth_headers(client, "aux2.a")
    otro = str(base_datos["admin_a"].id)
    for metodo, ruta, body in [
        ("GET", "/api/v1/usuarios", None),
        ("POST", f"/api/v1/usuarios/{otro}/restablecer-password", {"password": "Nueva1234*"}),
        ("POST", f"/api/v1/usuarios/{otro}/roles", {"rol_ids": []}),
        ("POST", f"/api/v1/usuarios/{otro}/bloquear", {}),
        ("PUT", f"/api/v1/usuarios/{otro}", {"nombre": "X"}),
        ("GET", "/api/v1/roles", None),
        ("PUT", f"/api/v1/roles/{_uuid.uuid4()}/permisos", {"permiso_ids": []}),
        ("GET", "/api/v1/auditoria", None),
    ]:
        r = client.request(metodo, ruta, json=body, headers=h)
        assert r.status_code == 403, (metodo, ruta, r.status_code, r.text[:150])

    # su propia clave sí
    assert client.post("/api/v1/auth/cambiar-password",
                       json={"password_actual": PASSWORD, "password_nueva": "Nueva1234*"},
                       headers=h).status_code == 200
