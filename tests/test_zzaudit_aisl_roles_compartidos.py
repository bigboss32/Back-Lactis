"""LOS ROLES YA NO SON DE TODA LA INSTALACION: SON DE CADA QUESERA.

Lo que media este archivo cuando se escribio: `roles` no tenia `empresa_id` y su
nombre era UNIQUE global, asi que las dos queseras compartian LA MISMA FILA. El
administrador de la quesera B le vaciaba los permisos al rol 'Ventas' y con eso
le bloqueaba el trabajo al vendedor de la quesera A, sin tocar nada de A. Y
/auditoria/logins le mostraba a cada una los ingresos y los intentos fallidos de
la otra.

Ahora se mide lo contrario, con el mismo procedimiento y por los mismos
endpoints: que la puerta esta cerrada. Los roles de SISTEMA siguen compartidos a
proposito —son la plantilla que la siembra mantiene en cada despliegue— pero ya
nadie los edita desde una empresa: se copian y se edita la copia.
"""
from tests.conftest import PASSWORD, auth_headers

V = "/api/v1"


def _rol(client, headers, nombre):
    roles = client.get(f"{V}/roles", params={"page_size": 100}, headers=headers).json()["items"]
    for r in roles:
        if r["nombre"] == nombre:
            return r
    raise AssertionError(f"no existe el rol {nombre}: {[r['nombre'] for r in roles]}")


def test_lo_unico_compartido_entre_las_dos_queseras_son_las_plantillas(client, base_datos):
    """Comparten fila los roles de SISTEMA, y solo esos."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    roles_a = client.get(f"{V}/roles", params={"page_size": 100}, headers=ha).json()["items"]
    roles_b = client.get(f"{V}/roles", params={"page_size": 100}, headers=hb).json()["items"]
    por_id_a = {r["id"]: r for r in roles_a}
    por_id_b = {r["id"]: r for r in roles_b}

    compartidos = [por_id_a[i] for i in por_id_a if i in por_id_b]
    print("\nroles con la MISMA fila en las dos queseras:", len(compartidos),
          sorted(r["nombre"] for r in compartidos))
    no_plantillas = [r["nombre"] for r in compartidos if not r["es_sistema"]]
    print("de esos, los que NO son de sistema:", no_plantillas or "ninguno")
    assert no_plantillas == [], (
        "hay roles de empresa compartidos entre las dos queseras: %s" % no_plantillas
    )
    assert all(r["empresa_id"] is None for r in compartidos)
    # Y las plantillas siguen ahi: son el catalogo del que todos cuelgan.
    assert compartidos, "se quedaron sin plantillas compartidas"


def test_b_no_le_puede_vaciar_los_permisos_al_rol_que_tambien_usa_a(client, base_datos):
    """El caso que trajo todo esto, medido por el endpoint."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    rol_a = _rol(client, ha, "Ventas")
    rol_b = _rol(client, hb, "Ventas")
    print("\nrol 'Ventas' visto por A:", rol_a["id"])
    print("rol 'Ventas' visto por B:", rol_b["id"])
    assert rol_a["id"] == rol_b["id"], "'Ventas' es de sistema: es la misma plantilla"
    print("permisos del rol antes:", len(rol_a["permisos"]))

    creado = client.post(f"{V}/usuarios", json={
        "nombre": "Vendedor", "apellido": "DeA", "correo": "vendedor.a@correo.com",
        "username": "vendedor.a", "password": PASSWORD, "rol_ids": [rol_a["id"]],
    }, headers=ha)
    assert creado.status_code == 201, creado.text

    hv = auth_headers(client, "vendedor.a")
    antes = {}
    for url in ("/clientes", "/ventas", "/inventario/productos"):
        antes[url] = client.get(f"{V}{url}", headers=hv).status_code
    print("el vendedor de A, ANTES:", antes)

    quitar = client.put(f"{V}/roles/{rol_b['id']}/permisos", json={"permiso_ids": []}, headers=hb)
    print("B intenta quitarle TODOS los permisos al rol 'Ventas' ->",
          quitar.status_code, quitar.text[:160])
    assert quitar.status_code == 403, "B todavia le puede vaciar los permisos a una plantilla"

    hv2 = auth_headers(client, "vendedor.a")
    despues = {}
    for url in ("/clientes", "/ventas", "/inventario/productos"):
        despues[url] = client.get(f"{V}{url}", headers=hv2).status_code
    print("el vendedor de A, DESPUES:", despues)
    rotos = [u for u in antes if antes[u] == 200 and despues[u] == 403]
    print("PANTALLAS QUE B LE CERRO AL VENDEDOR DE A:", rotos or "ninguna")
    assert rotos == []
    assert despues == antes
    ahora = _rol(client, ha, "Ventas")["permisos"]
    print("permisos del rol despues (visto por A):", len(ahora))
    assert len(ahora) == len(rol_a["permisos"])


def test_b_crea_un_rol_y_a_no_se_entera(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    permisos = client.get(f"{V}/permisos", params={"page_size": 5}, headers=hb).json()
    items = permisos["items"] if isinstance(permisos, dict) else permisos
    ids = [p["id"] for p in items][:2]

    r = client.post(f"{V}/roles", json={
        "nombre": "RolSECRETOdeB", "descripcion": "solo de B", "permiso_ids": ids,
    }, headers=hb)
    print("\nB crea el rol 'RolSECRETOdeB' ->", r.status_code, r.text[:140])
    assert r.status_code == 201, r.text
    rol_de_b = r.json()["id"]

    nombres = [x["nombre"] for x in
               client.get(f"{V}/roles", params={"page_size": 100}, headers=ha).json()["items"]]
    print("¿'RolSECRETOdeB' le aparece a A?", "RolSECRETOdeB" in nombres)
    assert "RolSECRETOdeB" not in nombres

    # Ni por el id directo, ni para editarlo, ni para borrarlo.
    directo = client.get(f"{V}/roles/{rol_de_b}", headers=ha)
    print("A pide el rol de B por id ->", directo.status_code)
    assert directo.status_code == 404
    assert client.put(f"{V}/roles/{rol_de_b}", json={"descripcion": "mio"},
                      headers=ha).status_code == 404
    assert client.put(f"{V}/roles/{rol_de_b}/permisos", json={"permiso_ids": []},
                      headers=ha).status_code == 404
    assert client.delete(f"{V}/roles/{rol_de_b}", headers=ha).status_code == 404

    # Y el nombre ya no es de toda la instalacion: A puede usar el mismo.
    choque = client.post(f"{V}/roles", json={"nombre": "RolSECRETOdeB"}, headers=ha)
    print("A usa ese mismo nombre para un rol suyo ->", choque.status_code, choque.text[:140])
    assert choque.status_code == 201, choque.text
    assert choque.json()["id"] != rol_de_b


def test_cada_quesera_ve_solo_sus_ingresos_de_sesion(client, base_datos):
    """/auditoria/logins ya no es de toda la instalacion.

    La tabla `login_audits` NO guarda la empresa y no se le invento una: se
    filtra por membresia (el porque esta en LoginAuditRepository).
    """
    auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    client.post(f"{V}/auth/login", data={"username": "admin.a", "password": "ClaveMala1*"})

    vistos = client.get(f"{V}/auditoria/logins", params={"page_size": 100}, headers=hb).json()
    print("\nB abre 'Ingresos' y ve:", vistos["total"],
          [i["username_intentado"] for i in vistos["items"]])
    ajenos = [i for i in vistos["items"] if i["username_intentado"] == "admin.a"]
    print("¿B VE LOS INGRESOS Y LOS INTENTOS FALLIDOS DE A?", bool(ajenos))
    assert ajenos == [], "B sigue viendo los ingresos de A"
    assert all(i["username_intentado"] == "admin.b" for i in vistos["items"])

    # Y A si ve los suyos, incluido el intento fallido.
    ha = auth_headers(client, "admin.a")
    mios = client.get(f"{V}/auditoria/logins", params={"page_size": 100}, headers=ha).json()
    print("A ve:", mios["total"], [(i["username_intentado"], i["exito"]) for i in mios["items"]])
    assert mios["total"] >= 2
    assert any(i["exito"] is False for i in mios["items"]), "A perdio su intento fallido"
    assert all(i["username_intentado"] == "admin.a" for i in mios["items"])
