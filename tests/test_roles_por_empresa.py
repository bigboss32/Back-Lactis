"""Los roles son de cada quesera; las plantillas de sistema, de la instalación.

Lo que se protege acá es QUIÉN ENTRA A QUÉ. Antes `roles` no tenía empresa_id y
su nombre era único en toda la base: las dos queseras compartían la misma fila y
el administrador de una le cambiaba las pantallas a los usuarios de la otra.

La forma que quedó:
  · rol de SISTEMA (empresa_id NULL): compartido a propósito —es la plantilla que
    la siembra mantiene— pero NADIE lo edita desde la aplicación. Se copia.
  · rol de una empresa (empresa_id con valor): solo esa empresa lo ve y lo toca.
  · el nombre es único POR EMPRESA.

Y lo que más importa de todo: que nadie —ni el de reventa ni el de consulta—
pierda acceso a lo que hoy tiene.
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.permissions import ACCIONES, MODULOS
from app.modules.usuarios.models import Rol
from app.seeds.seed import TODOS_LOS_ROLES, seed_permisos, seed_roles
from tests.conftest import PASSWORD, auth_headers

V = "/api/v1"


def _roles(client, headers):
    r = client.get(f"{V}/roles", params={"page_size": 200}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _rol(client, headers, nombre):
    for r in _roles(client, headers):
        if r["nombre"] == nombre:
            return r
    raise AssertionError(f"no existe el rol '{nombre}'")


def _permiso_id(client, headers, modulo, accion):
    r = client.get(f"{V}/permisos", params={"page_size": 200, "modulo": modulo}, headers=headers)
    for p in r.json()["items"]:
        if p["accion"] == accion:
            return p["id"]
    raise AssertionError(f"no existe el permiso {modulo}:{accion}")


def _crear_usuario(client, headers, username, rol_ids, esperado=201):
    r = client.post(f"{V}/usuarios", json={
        "nombre": username.title(), "apellido": "Prueba",
        "correo": f"{username}@pruebas.com", "username": username,
        "password": PASSWORD, "rol_ids": rol_ids,
    }, headers=headers)
    assert r.status_code == esperado, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1. Cada quesera ve lo suyo
# ---------------------------------------------------------------------------
def test_el_admin_de_a_no_ve_ni_toca_los_roles_de_b(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    de_b = client.post(f"{V}/roles", json={
        "nombre": "Bodega de B", "descripcion": "solo de B",
        "permiso_ids": [_permiso_id(client, hb, "inventario", "consultar")],
    }, headers=hb)
    assert de_b.status_code == 201, de_b.text
    rol_b = de_b.json()
    assert rol_b["empresa_id"] == str(base_datos["empresa_b"].id)
    assert rol_b["es_sistema"] is False

    assert "Bodega de B" not in [r["nombre"] for r in _roles(client, ha)]
    assert client.get(f"{V}/roles/{rol_b['id']}", headers=ha).status_code == 404
    assert client.put(f"{V}/roles/{rol_b['id']}", json={"nombre": "Robado"},
                      headers=ha).status_code == 404
    assert client.put(f"{V}/roles/{rol_b['id']}/permisos", json={"permiso_ids": []},
                      headers=ha).status_code == 404
    assert client.delete(f"{V}/roles/{rol_b['id']}", headers=ha).status_code == 404
    assert client.post(f"{V}/roles/{rol_b['id']}/copiar", json={},
                       headers=ha).status_code == 404

    # Y B lo sigue teniendo entero.
    todavia = client.get(f"{V}/roles/{rol_b['id']}", headers=hb)
    assert todavia.status_code == 200
    assert len(todavia.json()["permisos"]) == 1


def test_las_dos_queseras_pueden_llamar_igual_a_sus_roles(client, base_datos):
    """El nombre es único POR EMPRESA, no en toda la instalación."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    a = client.post(f"{V}/roles", json={"nombre": "Bodega"}, headers=ha)
    b = client.post(f"{V}/roles", json={"nombre": "Bodega"}, headers=hb)
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text
    assert a.json()["id"] != b.json()["id"]
    assert a.json()["empresa_id"] != b.json()["empresa_id"]

    # Repetirlo dentro de la MISMA empresa sí choca.
    otra_vez = client.post(f"{V}/roles", json={"nombre": "Bodega"}, headers=ha)
    assert otra_vez.status_code == 409, otra_vez.text
    assert "Bodega" in otra_vez.json()["error"]["detail"]


def test_un_rol_de_empresa_no_puede_llamarse_como_una_plantilla(client, base_datos):
    """No es exigencia de la base: es para que el dueño no vea dos 'Ventas'."""
    ha = auth_headers(client, "admin.a")
    r = client.post(f"{V}/roles", json={"nombre": "Ventas"}, headers=ha)
    assert r.status_code == 409, r.text


def test_no_se_le_puede_asignar_a_un_usuario_el_rol_de_la_otra_quesera(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    rol_b = client.post(f"{V}/roles", json={"nombre": "Caja de B"}, headers=hb).json()

    # Ni al crear el usuario...
    _crear_usuario(client, ha, "colado.a", [rol_b["id"]], esperado=404)
    # ...ni asignándoselo después.
    usuario = _crear_usuario(client, ha, "normal.a", [_rol(client, ha, "Consulta")["id"]])
    r = client.post(f"{V}/usuarios/{usuario['id']}/roles",
                    json={"rol_ids": [rol_b["id"]]}, headers=ha)
    assert r.status_code == 404, r.text


def test_el_superadmin_no_cruza_roles_entre_empresas_al_dar_membresias(client, base_datos):
    hs = auth_headers(client, "superadmin")
    ha = auth_headers(client, "admin.a")
    rol_a = client.post(f"{V}/roles", json={"nombre": "Solo de A"}, headers=ha).json()

    usuario = _crear_usuario(
        client, ha, "multi.a", [_rol(client, ha, "Consulta")["id"]]
    )
    r = client.put(f"{V}/usuarios/{usuario['id']}/empresas", json={"membresias": [
        {"empresa_id": str(base_datos["empresa_a"].id), "rol_ids": [rol_a["id"]]},
        {"empresa_id": str(base_datos["empresa_b"].id), "rol_ids": [rol_a["id"]]},
    ]}, headers=hs)
    assert r.status_code == 422, r.text
    assert "otra empresa" in r.json()["error"]["detail"]


# ---------------------------------------------------------------------------
# 2. Las plantillas de sistema: no se editan, se copian
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("nombre", ["Ventas", "Consulta", "Reventa"])
def test_un_admin_de_empresa_no_edita_una_plantilla_de_sistema(client, base_datos, nombre):
    ha = auth_headers(client, "admin.a")
    rol = _rol(client, ha, nombre)
    assert rol["empresa_id"] is None
    assert rol["es_sistema"] is True

    for respuesta in (
        client.put(f"{V}/roles/{rol['id']}", json={"descripcion": "mío"}, headers=ha),
        client.put(f"{V}/roles/{rol['id']}/permisos", json={"permiso_ids": []}, headers=ha),
        client.delete(f"{V}/roles/{rol['id']}", headers=ha),
    ):
        assert respuesta.status_code == 403, respuesta.text
        assert "rol de sistema" in respuesta.json()["error"]["detail"]

    # Ni un permiso se movió.
    assert len(_rol(client, ha, nombre)["permisos"]) == len(rol["permisos"])


def test_tampoco_el_superadmin_edita_una_plantilla(client, base_datos):
    """La siembra le devolvería el permiso quitado en el siguiente despliegue, y
    lo agregado se le quedaría a las DOS queseras. Se cierra parejo."""
    hs = auth_headers(client, "superadmin")
    con_a = {**hs, "X-Empresa-Id": str(base_datos["empresa_a"].id)}
    rol = _rol(client, con_a, "Consulta")
    r = client.put(f"{V}/roles/{rol['id']}/permisos", json={"permiso_ids": []}, headers=con_a)
    assert r.status_code == 403, r.text


def test_el_dueno_copia_la_plantilla_y_edita_la_copia(client, base_datos):
    """El camino que reemplaza a 'editar el rol de sistema'."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    plantilla = _rol(client, ha, "Ventas")

    copia = client.post(f"{V}/roles/{plantilla['id']}/copiar", json={}, headers=ha)
    assert copia.status_code == 201, copia.text
    copia = copia.json()
    print("\ncopia creada:", copia["nombre"], "con", len(copia["permisos"]), "permisos")
    assert copia["nombre"] == "Ventas (copia)"
    assert copia["empresa_id"] == str(base_datos["empresa_a"].id)
    assert copia["es_sistema"] is False
    # Se lleva EXACTAMENTE los mismos permisos.
    assert {p["id"] for p in copia["permisos"]} == {p["id"] for p in plantilla["permisos"]}

    # Y ahora sí se puede editar, sin tocar la plantilla ni a la otra quesera.
    solo_uno = [_permiso_id(client, ha, "clientes", "consultar")]
    r = client.put(f"{V}/roles/{copia['id']}/permisos", json={"permiso_ids": solo_uno}, headers=ha)
    assert r.status_code == 200, r.text
    assert len(r.json()["permisos"]) == 1
    assert len(_rol(client, ha, "Ventas")["permisos"]) == len(plantilla["permisos"])
    assert len(_rol(client, hb, "Ventas")["permisos"]) == len(plantilla["permisos"])
    assert "Ventas (copia)" not in [x["nombre"] for x in _roles(client, hb)]


def test_copiar_dos_veces_no_choca_de_nombre(client, base_datos):
    ha = auth_headers(client, "admin.a")
    plantilla = _rol(client, ha, "Consulta")
    primera = client.post(f"{V}/roles/{plantilla['id']}/copiar", json={}, headers=ha)
    segunda = client.post(f"{V}/roles/{plantilla['id']}/copiar", json={}, headers=ha)
    assert primera.status_code == 201, primera.text
    assert segunda.status_code == 201, segunda.text
    assert primera.json()["nombre"] == "Consulta (copia)"
    assert segunda.json()["nombre"] == "Consulta (copia 2)"

    # Y con nombre propio también.
    tercera = client.post(f"{V}/roles/{plantilla['id']}/copiar",
                          json={"nombre": "Solo mirar"}, headers=ha)
    assert tercera.status_code == 201, tercera.text
    assert tercera.json()["nombre"] == "Solo mirar"


def test_el_rol_de_superadmin_no_se_copia(client, base_datos):
    """Sus permisos son implícitos: la copia quedaría vacía y engañaría."""
    hs = auth_headers(client, "superadmin")
    con_a = {**hs, "X-Empresa-Id": str(base_datos["empresa_a"].id)}
    rol = _rol(client, con_a, "Administrador General")
    r = client.post(f"{V}/roles/{rol['id']}/copiar", json={}, headers=con_a)
    assert r.status_code == 422, r.text


def test_el_rol_borrado_sigue_ocupando_el_nombre_y_lo_dice_claro(client, base_datos):
    """Antes esto reventaba contra el índice único: 500 en la cara del dueño."""
    ha = auth_headers(client, "admin.a")
    rol = client.post(f"{V}/roles", json={"nombre": "Temporal"}, headers=ha).json()
    assert client.delete(f"{V}/roles/{rol['id']}", headers=ha).status_code == 204

    r = client.post(f"{V}/roles", json={"nombre": "Temporal"}, headers=ha)
    assert r.status_code == 409, r.text
    assert "ya se borró" in r.json()["error"]["detail"]


# ---------------------------------------------------------------------------
# 3. NADIE PIERDE ACCESO
# ---------------------------------------------------------------------------
PUERTAS_DE_REVENTA = ("/reventa/compras", "/reventa/ventas", "/reventa/productos")
PUERTAS_DE_CONSULTA = ("/proveedores", "/clientes", "/recepciones", "/inventario/productos")


def test_el_usuario_de_reventa_conserva_sus_pantallas(client, base_datos):
    ha = auth_headers(client, "admin.a")
    _crear_usuario(client, ha, "rev.a", [_rol(client, ha, "Reventa")["id"]])
    h = auth_headers(client, "rev.a")

    perfil = client.get(f"{V}/auth/me", headers=h).json()
    print("\nel usuario de reventa entra con", len(perfil["permisos"]), "permisos")
    assert perfil["roles"] == ["Reventa"]
    for accion in ACCIONES:
        assert f"reventa:{accion}" in perfil["permisos"]
    for accion in ("consultar", "crear", "administrar"):
        assert f"suscripcion:{accion}" in perfil["permisos"]

    abiertas = {url: client.get(f"{V}{url}", headers=h).status_code for url in PUERTAS_DE_REVENTA}
    print("sus pantallas:", abiertas)
    assert all(codigo == 200 for codigo in abiertas.values()), abiertas


def test_el_usuario_de_consulta_conserva_sus_pantallas(client, base_datos):
    ha = auth_headers(client, "admin.a")
    _crear_usuario(client, ha, "cons.a", [_rol(client, ha, "Consulta")["id"]])
    h = auth_headers(client, "cons.a")

    perfil = client.get(f"{V}/auth/me", headers=h).json()
    print("\nel usuario de consulta entra con", len(perfil["permisos"]), "permisos")
    for modulo in MODULOS:
        assert f"{modulo}:consultar" in perfil["permisos"], modulo

    abiertas = {url: client.get(f"{V}{url}", headers=h).status_code for url in PUERTAS_DE_CONSULTA}
    print("sus pantallas:", abiertas)
    assert all(codigo == 200 for codigo in abiertas.values()), abiertas


def test_el_admin_de_empresa_sigue_administrando_sus_roles_y_usuarios(client, base_datos):
    ha = auth_headers(client, "admin.a")
    assert client.get(f"{V}/roles", headers=ha).status_code == 200
    assert client.get(f"{V}/permisos", headers=ha).status_code == 200
    assert client.get(f"{V}/usuarios", headers=ha).status_code == 200
    creado = client.post(f"{V}/roles", json={"nombre": "Mi Rol"}, headers=ha)
    assert creado.status_code == 201, creado.text
    assert client.put(f"{V}/roles/{creado.json()['id']}",
                      json={"descripcion": "cambiada"}, headers=ha).status_code == 200
    assert client.delete(f"{V}/roles/{creado.json()['id']}", headers=ha).status_code == 204


def test_un_usuario_con_rol_propio_de_su_empresa_entra_normal(client, base_datos):
    """Un rol creado por la quesera funciona igual que una plantilla."""
    ha = auth_headers(client, "admin.a")
    rol = client.post(f"{V}/roles", json={
        "nombre": "Solo Clientes",
        "permiso_ids": [_permiso_id(client, ha, "clientes", "consultar")],
    }, headers=ha).json()
    _crear_usuario(client, ha, "propio.a", [rol["id"]])

    h = auth_headers(client, "propio.a")
    perfil = client.get(f"{V}/auth/me", headers=h).json()
    assert perfil["roles"] == ["Solo Clientes"]
    assert perfil["permisos"] == ["clientes:consultar"]
    assert client.get(f"{V}/clientes", headers=h).status_code == 200
    assert client.get(f"{V}/proveedores", headers=h).status_code == 403


# ---------------------------------------------------------------------------
# 4. La siembra, que corre en CADA despliegue
# ---------------------------------------------------------------------------
def test_la_siembra_solo_maneja_plantillas_y_no_depende_de_cuantas_empresas_haya(db_session):
    permisos = seed_permisos(db_session)
    roles = seed_roles(db_session, permisos)

    assert set(roles) == set(TODOS_LOS_ROLES)
    for nombre, rol in roles.items():
        assert rol.empresa_id is None, f"la siembra le puso empresa a '{nombre}'"
        assert rol.es_sistema is True

    # Ninguna plantilla se duplicó por empresa.
    filas = db_session.scalars(select(Rol).where(Rol.empresa_id.is_(None))).all()
    assert len(filas) == len(TODOS_LOS_ROLES)


def test_la_siembra_es_idempotente_con_las_dos_queseras_montadas(client, base_datos, db_session):
    """base_datos ya sembró y creó dos empresas: volver a sembrar no mueve nada."""
    antes = {
        r.id: (r.nombre, r.empresa_id, r.es_sistema, frozenset((p.modulo, p.accion) for p in r.permisos))
        for r in db_session.scalars(select(Rol)).all()
    }
    permisos = seed_permisos(db_session)
    seed_roles(db_session, permisos)
    seed_roles(db_session, permisos)
    db_session.flush()

    despues = {
        r.id: (r.nombre, r.empresa_id, r.es_sistema, frozenset((p.modulo, p.accion) for p in r.permisos))
        for r in db_session.scalars(select(Rol)).all()
    }
    assert antes == despues, "la siembra repetida cambió los roles"


def test_la_siembra_ignora_un_rol_de_empresa_que_se_llame_como_una_plantilla(
    client, base_datos, db_session
):
    """El rol del cliente vive en OTRO espacio de nombres: ni lo estorba ni lo tocan.

    Antes esto obligaba a omitir la siembra del rol de sistema entero, porque el
    nombre era único en toda la base.
    """
    ha = auth_headers(client, "admin.a")
    empresa_a = base_datos["empresa_a"].id
    propio = Rol(nombre="Reventa", descripcion="el mío, solo mirar", es_sistema=False,
                 empresa_id=empresa_a)
    propio.permisos = [
        p for p in db_session.scalars(select(Rol).where(Rol.nombre == "Reventa",
                                                        Rol.empresa_id.is_(None))).one().permisos
        if (p.modulo, p.accion) == ("reventa", "consultar")
    ]
    db_session.add(propio)
    db_session.flush()
    propio_id = propio.id

    permisos = seed_permisos(db_session)
    roles = seed_roles(db_session, permisos)
    db_session.flush()

    # El rol del cliente quedó EXACTAMENTE igual: ni un permiso de más.
    quedo = db_session.get(Rol, propio_id)
    assert {(p.modulo, p.accion) for p in quedo.permisos} == {("reventa", "consultar")}
    assert quedo.es_sistema is False
    assert quedo.empresa_id == empresa_a
    # Y la plantilla sigue siendo la plantilla, con sus permisos completos.
    assert roles["Reventa"].empresa_id is None
    assert len(roles["Reventa"].permisos) > 1

    # Cada quesera ve lo que le toca: A ve los dos 'Reventa', B solo la plantilla.
    ha_roles = [r for r in _roles(client, ha) if r["nombre"] == "Reventa"]
    assert len(ha_roles) == 2, [r["empresa_id"] for r in ha_roles]
    hb_roles = [r for r in _roles(client, auth_headers(client, "admin.b"))
                if r["nombre"] == "Reventa"]
    assert len(hb_roles) == 1
    assert hb_roles[0]["empresa_id"] is None


# ---------------------------------------------------------------------------
# 5. El registro de ingresos
# ---------------------------------------------------------------------------
def test_los_intentos_contra_un_usuario_inexistente_no_son_de_nadie(client, base_datos):
    """usuario_id NULL: no hay membresía a la que atribuirlos. Solo el superadmin."""
    ha = auth_headers(client, "admin.a")
    client.post(f"{V}/auth/login", data={"username": "no.existe", "password": "Loquesea1*"})

    de_a = client.get(f"{V}/auditoria/logins", params={"page_size": 100}, headers=ha).json()
    print("\nA ve:", [i["username_intentado"] for i in de_a["items"]])
    assert not any(i["username_intentado"] == "no.existe" for i in de_a["items"])

    hs = auth_headers(client, "superadmin")
    del_super = client.get(f"{V}/auditoria/logins", params={"page_size": 100}, headers=hs).json()
    print("el superadmin ve:", [i["username_intentado"] for i in del_super["items"]])
    assert any(i["username_intentado"] == "no.existe" for i in del_super["items"])


def test_quien_trabaja_en_las_dos_queseras_aparece_en_las_dos(client, base_datos):
    """Es verdad y se dice: esa persona entró, y las dos la tienen contratada."""
    hs = auth_headers(client, "superadmin")
    ha = auth_headers(client, "admin.a")
    usuario = _crear_usuario(client, ha, "dos.aguas", [_rol(client, ha, "Consulta")["id"]])
    consulta = _rol(client, ha, "Consulta")["id"]
    r = client.put(f"{V}/usuarios/{usuario['id']}/empresas", json={"membresias": [
        {"empresa_id": str(base_datos["empresa_a"].id), "rol_ids": [consulta]},
        {"empresa_id": str(base_datos["empresa_b"].id), "rol_ids": [consulta]},
    ]}, headers=hs)
    assert r.status_code == 200, r.text
    auth_headers(client, "dos.aguas")

    hb = auth_headers(client, "admin.b")
    for etiqueta, headers in (("A", ha), ("B", hb)):
        vistos = client.get(f"{V}/auditoria/logins", params={"page_size": 100},
                            headers=headers).json()
        usuarios = {i["username_intentado"] for i in vistos["items"]}
        print(f"\n{etiqueta} ve:", sorted(usuarios))
        assert "dos.aguas" in usuarios


def test_el_filtro_por_usuario_ajeno_no_es_una_puerta_lateral(client, base_datos):
    hb = auth_headers(client, "admin.b")
    ajeno = base_datos["admin_a"].id
    r = client.get(f"{V}/auditoria/logins", params={"usuario_id": str(ajeno)}, headers=hb)
    assert r.status_code == 200
    assert r.json()["total"] == 0, r.json()


# ---------------------------------------------------------------------------
# 6. La red de seguridad de la migración
# ---------------------------------------------------------------------------
def _migracion():
    """Carga el módulo de la migración sin correrla (no toca ninguna base)."""
    import importlib.util
    from pathlib import Path

    ruta = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "f1b7d3a9c5e4_roles_de_cada_quesera.py"
    )
    spec = importlib.util.spec_from_file_location("mig_roles_por_empresa", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_el_post_vuelo_de_la_migracion_revienta_si_alguien_pierde_un_permiso():
    """La migración se compara consigo misma; acá se comprueba que la comparación
    de verdad revienta, que es lo único que evita un despliegue que deje gente
    por fuera del sistema."""
    mig = _migracion()
    usuario, empresa = uuid.uuid4(), uuid.uuid4()
    permisos = frozenset({uuid.uuid4(), uuid.uuid4(), uuid.uuid4()})

    mig._comparar({(usuario, empresa): permisos}, {(usuario, empresa): permisos})

    perdio = frozenset(list(permisos)[:2])
    with pytest.raises(RuntimeError, match="MIGRACIÓN ABORTADA"):
        mig._comparar({(usuario, empresa): permisos}, {(usuario, empresa): perdio})
    # Y también si alguien GANA uno que no tenía.
    with pytest.raises(RuntimeError, match="MIGRACIÓN ABORTADA"):
        mig._comparar({(usuario, empresa): perdio}, {(usuario, empresa): permisos})
    # Y si un usuario desaparece del mapa.
    with pytest.raises(RuntimeError, match="MIGRACIÓN ABORTADA"):
        mig._comparar({(usuario, empresa): permisos}, {})


def test_la_migracion_se_niega_a_correr_fuera_de_postgres():
    """La cadena entera es de Postgres desde antes; se dice en vez de fingir."""
    mig = _migracion()

    class _Dialecto:
        name = "sqlite"

    class _Conexion:
        dialect = _Dialecto()

    with pytest.raises(RuntimeError, match="PostgreSQL"):
        mig._exigir_postgres(_Conexion())
