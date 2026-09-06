"""QUIÉN PUEDE CORREGIR UNA QUINCENA QUE YA SE PAGÓ.

El dueño lo pidió con nombre propio: "que si soy ADMINISTRADOR DE EMPRESA pueda editar
la liquidación que ya está pagada". La implementación no inventó un mecanismo nuevo: le
colgó a la corrección el permiso `liquidaciones:administrar`, que en el seed tiene
exactamente ese rol y ningún otro. Esta prueba mide que eso sea cierto HOY y que siga
siéndolo mañana:

  (a) en el catálogo de siembra (`ROLES_PERMISOS`) y en las filas que quedan en la base:
      si alguien le regala 'liquidaciones:administrar' a otro rol, aquí se canta;
  (b) puerta por puerta y rol por rol —previsualizar, corregir y leer las correcciones—
      con la quincena de $500.000 del dueño y el día olvidado de $180.000 montados de
      verdad, para que un permiso que se afloje MUEVA PLATA y la prueba lo vea;
  (c) el caso de COMPRAS, que es el que trajo toda esta discusión: tiene
      'liquidaciones:editar' —y se demuestra usándolo, corrigiéndole el precio a un
      borrador— pero NO puede tocar el precio por litro de una quincena pagada. Ese es
      literalmente el motivo por el que la corrección no se colgó de `PUT /{id}` ni de
      `PUT /{id}/detalles/{detalle_id}`;
  (d) sin token no sale ni una cifra: 401, y el cuerpo no trae los $500.000.

Las cifras son las del dueño: 250 L a $2.000 = $500.000 pagados, y el día del 12/06 con
90 L a $2.000 = $180.000 esperando afuera.
"""
from decimal import Decimal

from sqlalchemy import select

from app.core.permissions import ROL_SUPERADMIN
from app.modules.usuarios.models import Rol
from app.seeds.seed import ROLES_PERMISOS, TODOS_LOS_ROLES
from tests.conftest import PASSWORD, auth_headers

API = "/api/v1/liquidaciones"

D = Decimal

# La quincena del dueño, al peso.
LITROS = "250"
PRECIO = "2000"
VALOR_QUINCENA = D("500000.00")
LITROS_OLVIDADOS = "90"
VALOR_DIA_OLVIDADO = D("180000.00")

# LO QUE ESTA PRUEBA AFIRMA, ESCRITO A MANO Y NO DEDUCIDO DEL SEED. Si se dedujera del
# seed, un cambio en el seed se auto-aprobaría y la prueba seguiría verde mientras el
# precio por litro de una quincena pagada le queda abierto a otro rol.
ROL_QUE_CORRIGE = "Administrador Empresa"

# Los que sí pueden LEER el historial de correcciones: leerlo pide 'liquidaciones:
# consultar', que es el mismo permiso con el que ya ven la quincena entera.
ROLES_QUE_LEEN_CORRECCIONES = {
    "Administrador Empresa",
    "Contador",
    "Supervisor",
    "Compras",
    "Consulta",
}

# Usuario por rol: los nombres de rol llevan tilde y el username no.
USUARIO_DE_ROL = {
    "Administrador Empresa": "rol.admin",
    "Contador": "rol.contador",
    "Supervisor": "rol.supervisor",
    "Auxiliar": "rol.auxiliar",
    "Producción": "rol.produccion",
    "Compras": "rol.compras",
    "Ventas": "rol.ventas",
    "Consulta": "rol.consulta",
    "Reventa": "rol.reventa",
}

# Todos los roles que el seed le puede asignar a un usuario DE UNA EMPRESA. El
# superadmin queda por fuera de la matriz porque no lleva filas de permisos: su chequeo
# es implícito y se mide aparte.
ROLES_DE_EMPRESA = tuple(r for r in TODOS_LOS_ROLES if r != ROL_SUPERADMIN)


# --------------------------------------------------------------------- montaje
def crear_usuario_con_rol(db_session, empresa, nombre_rol, username):
    """Un usuario de la empresa con uno de los roles que siembra el sistema."""
    from app.core.security import hash_password
    from app.modules.usuarios.models import Usuario, UsuarioRol

    rol = db_session.scalars(select(Rol).where(Rol.nombre == nombre_rol)).one()
    usuario = Usuario(
        nombre=username.title(),
        apellido="Prueba",
        correo=f"{username}@test.local",
        username=username,
        hashed_password=hash_password(PASSWORD),
        empresa_id=empresa.id,
    )
    db_session.add(usuario)
    db_session.flush()
    db_session.add(UsuarioRol(usuario_id=usuario.id, rol_id=rol.id, empresa_id=empresa.id))
    db_session.commit()
    return usuario


def _proveedor(client, h, nombre="Henri"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": PRECIO},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, proveedor_id):
    r = client.post(
        f"{API}/generar",
        json={
            "periodo_inicio": "2026-06-01",
            "periodo_fin": "2026-06-15",
            "tipo": "proveedor",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == proveedor_id)


def quincena_pagada(client, h, nombre="Henri"):
    """250 L a $2.000 el 02/06 = $500.000, aprobada y PAGADA completa."""
    prov = _proveedor(client, h, nombre)
    r = client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-06-02", "proveedor_id": prov["id"], "cantidad_litros": LITROS},
        headers=h,
    )
    assert r.status_code == 201, r.text
    liq = _generar(client, h, prov["id"])
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    pagada = pagada.json()
    assert pagada["estado"] == "pagada"
    assert D(pagada["valor_total"]) == VALOR_QUINCENA, pagada["valor_total"]
    assert D(pagada["pagado"]) == VALOR_QUINCENA
    assert pagada["version"] == 1
    return prov, pagada


def dia_olvidado(client, h, prov, liq_id):
    """El día del 12/06 por $180.000, todavía suelto. Devuelve su recepcion_id."""
    r = client.post(
        "/api/v1/recepciones",
        json={
            "fecha": "2026-06-12",
            "proveedor_id": prov["id"],
            "cantidad_litros": LITROS_OLVIDADOS,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    prev = client.post(
        f"{API}/{liq_id}/corregir/previsualizar", json={"motivo": "montaje"}, headers=h
    )
    assert prev.status_code == 200, prev.text
    sueltos = prev.json()["dias_sueltos"]
    assert len(sueltos) == 1, sueltos
    assert D(sueltos[0]["valor"]) == VALOR_DIA_OLVIDADO, sueltos[0]["valor"]
    return sueltos[0]["recepcion_id"]


def _cifras(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    return D(d["valor_total"]), D(d["pagado"]), D(d["saldo"]), d["estado"], d["version"]


# ===========================================================================
# a) El seed: 'liquidaciones:administrar' es de UN solo rol
# ===========================================================================
def test_el_permiso_de_corregir_lo_tiene_exactamente_un_rol_del_seed(client, base_datos, db_session):
    """La puerta de la corrección es 'liquidaciones:administrar'. Si mañana alguien se lo
    regala a Compras o a Supervisor, ESO ES EL DEFECTO y hay que verlo aquí, no cuando
    una quincena de $500.000 amanezca en otra cifra.

    Se mide dos veces: en el catálogo que se declara (ROLES_PERMISOS) y en las filas que
    de verdad quedaron en la base después de sembrar, que son las que el chequeo lee.
    """
    del client  # el cliente solo está para levantar la app; aquí se mira la BD

    print("\n===== a) QUIÉN TIENE 'liquidaciones:administrar' EN EL SEED =====")
    declarados = {
        rol for rol, claves in ROLES_PERMISOS.items() if ("liquidaciones", "administrar") in claves
    }
    print(f"  declarado en ROLES_PERMISOS: {sorted(declarados)}")
    assert declarados == {ROL_QUE_CORRIGE}, (
        f"'liquidaciones:administrar' abre la corrección de una quincena pagada; "
        f"el seed se lo declara a {sorted(declarados)}"
    )

    en_la_base = {
        rol.nombre
        for rol in db_session.scalars(select(Rol)).all()
        if any(p.modulo == "liquidaciones" and p.accion == "administrar" for p in rol.permisos)
    }
    print(f"  filas en la base:            {sorted(en_la_base)}")
    assert en_la_base == {ROL_QUE_CORRIGE}, en_la_base

    # Y el contraste que le da sentido a la puerta propia: Compras SÍ tiene 'editar'.
    compras = ROLES_PERMISOS["Compras"]
    print(f"  Compras tiene 'editar':      {('liquidaciones', 'editar') in compras}")
    print(f"  Compras tiene 'administrar': {('liquidaciones', 'administrar') in compras}")
    assert ("liquidaciones", "editar") in compras
    assert ("liquidaciones", "administrar") not in compras


# ===========================================================================
# b) La matriz: rol por rol, puerta por puerta, con la plata montada
# ===========================================================================
def test_ningun_rol_fuera_del_administrador_empresa_corrige_la_quincena_pagada(
    client, base_datos, db_session
):
    """Los ocho roles que NO son Administrador Empresa chocan contra la puerta.

    La petición que se les manda NO es de mentira: lleva el recepcion_id del día del
    12/06 por $180.000. Si a alguno se le abriera la puerta, la quincena pasaría de
    $500.000 a $680.000 y el productor tendría dos papeles distintos de la misma
    quincena. Por eso al final se vuelven a leer las cifras: la prueba no se conforma
    con el 403, comprueba que la plata quedó donde estaba.
    """
    empresa = base_datos["empresa_a"]
    ha = auth_headers(client, "admin.a")
    prov, pagada = quincena_pagada(client, ha)
    liq_id = pagada["id"]
    rec_id = dia_olvidado(client, ha, prov, liq_id)

    for nombre_rol in ROLES_DE_EMPRESA:
        crear_usuario_con_rol(db_session, empresa, nombre_rol, USUARIO_DE_ROL[nombre_rol])

    print("\n===== b) CORREGIR UNA QUINCENA PAGADA, ROL POR ROL =====")
    print(f"  la quincena está en ${VALOR_QUINCENA:,} y afuera espera un día de "
          f"${VALOR_DIA_OLVIDADO:,}")
    payload = {"motivo": "se anoto tarde el dia 12", "recepciones_a_incluir": [rec_id]}

    for nombre_rol in ROLES_DE_EMPRESA:
        if nombre_rol == ROL_QUE_CORRIGE:
            continue
        h = auth_headers(client, USUARIO_DE_ROL[nombre_rol])
        prev = client.post(f"{API}/{liq_id}/corregir/previsualizar", json=payload, headers=h)
        hecho = client.post(f"{API}/{liq_id}/corregir", json=payload, headers=h)
        print(f"  {nombre_rol:22} previsualizar={prev.status_code}  corregir={hecho.status_code}")
        assert prev.status_code == 403, f"{nombre_rol} previsualizó: {prev.text}"
        assert hecho.status_code == 403, f"{nombre_rol} CORRIGIÓ: {hecho.text}"
        # El mensaje nombra el permiso que falta: el dueño tiene que poder saber a quién
        # pedirle que lo haga.
        assert "administrar" in hecho.json()["error"]["detail"], hecho.text
        # Y el rechazo no le enseña la cifra a quien no la puede tocar.
        assert "500000" not in prev.text and "680000" not in prev.text, prev.text

    valor, pagado, saldo, estado, version = _cifras(client, ha, liq_id)
    print(f"  después de los 8 intentos: valor_total ${valor:,} · pagado ${pagado:,} · "
          f"saldo ${saldo:,} · {estado} · v{version}")
    assert valor == VALOR_QUINCENA
    assert pagado == VALOR_QUINCENA
    assert saldo == D("0.00")
    assert estado == "pagada"
    assert version == 1

    # Y AHORA EL QUE SÍ: el rol del dueño pasa por la misma puerta y la plata se mueve.
    h_admin = auth_headers(client, USUARIO_DE_ROL[ROL_QUE_CORRIGE])
    prev = client.post(f"{API}/{liq_id}/corregir/previsualizar", json=payload, headers=h_admin)
    assert prev.status_code == 200, prev.text
    hecho = client.post(f"{API}/{liq_id}/corregir", json=payload, headers=h_admin)
    assert hecho.status_code == 200, hecho.text
    c = hecho.json()
    print(f"  {ROL_QUE_CORRIGE:22} previsualizar=200  corregir=200  ->  "
          f"${D(c['valor_total']):,} · saldo ${D(c['saldo']):,} · {c['estado']} · v{c['version']}")
    assert D(c["valor_total"]) == VALOR_QUINCENA + VALOR_DIA_OLVIDADO
    assert D(c["pagado"]) == VALOR_QUINCENA
    assert D(c["saldo"]) == VALOR_DIA_OLVIDADO
    assert c["estado"] == "parcial"
    assert c["version"] == 2
    # LA REGLA DE LA CASA: neto = pagado + saldo, al centavo.
    assert D(c["neto_a_pagar"]) == D(c["pagado"]) + D(c["saldo"])


# ===========================================================================
# c) Leer las correcciones pide 'consultar', no 'administrar'
# ===========================================================================
def test_leer_las_correcciones_pide_consultar_y_no_administrar(client, base_datos, db_session):
    """El historial es un dato de la quincena, no una operación de plata.

    Quien ya ve el comprobante entero (Contador, Supervisor, Compras, Consulta) tiene que
    poder leer POR QUÉ cambió de $500.000 a $680.000 —si no, el papel viejo y el nuevo no
    se explican—; y quien no tiene ni 'liquidaciones:consultar' (Auxiliar, Producción,
    Ventas, Reventa) no ve ni el motivo ni las cifras.
    """
    empresa = base_datos["empresa_a"]
    ha = auth_headers(client, "admin.a")
    prov, pagada = quincena_pagada(client, ha)
    liq_id = pagada["id"]
    rec_id = dia_olvidado(client, ha, prov, liq_id)
    hecho = client.post(
        f"{API}/{liq_id}/corregir",
        json={"motivo": "se anoto tarde el dia 12", "recepciones_a_incluir": [rec_id]},
        headers=ha,
    )
    assert hecho.status_code == 200, hecho.text

    for nombre_rol in ROLES_DE_EMPRESA:
        crear_usuario_con_rol(db_session, empresa, nombre_rol, USUARIO_DE_ROL[nombre_rol])

    print("\n===== c) LEER EL HISTORIAL DE CORRECCIONES, ROL POR ROL =====")
    for nombre_rol in ROLES_DE_EMPRESA:
        h = auth_headers(client, USUARIO_DE_ROL[nombre_rol])
        r = client.get(f"{API}/{liq_id}/correcciones", headers=h)
        puede = nombre_rol in ROLES_QUE_LEEN_CORRECCIONES
        cuantas = len(r.json()) if r.status_code == 200 else "-"
        print(f"  {nombre_rol:22} {r.status_code}  correcciones={cuantas}")
        if puede:
            assert r.status_code == 200, f"{nombre_rol}: {r.text}"
            filas = r.json()
            assert len(filas) == 1
            assert D(filas[0]["valor_total_antes"]) == VALOR_QUINCENA
            assert D(filas[0]["valor_total_despues"]) == VALOR_QUINCENA + VALOR_DIA_OLVIDADO
            assert filas[0]["motivo"] == "se anoto tarde el dia 12"
        else:
            assert r.status_code == 403, f"{nombre_rol} leyó el historial: {r.text}"
            assert "500000" not in r.text and "680000" not in r.text, r.text
            assert "se anoto tarde" not in r.text, r.text


# ===========================================================================
# d) COMPRAS: el conejillo. Tiene 'editar' y NO se le abre el precio de la pagada
# ===========================================================================
def test_compras_edita_borradores_pero_no_toca_el_precio_de_una_quincena_pagada(
    client, base_datos, db_session
):
    """ESTE ES EL MOTIVO DE QUE LA CORRECCIÓN TENGA PUERTA PROPIA.

    Compras tiene 'liquidaciones:editar', y aquí se DEMUESTRA usándolo: le cambia el
    precio a un día de un BORRADOR y la cifra se mueve de verdad. Si la corrección se
    hubiera colgado de `PUT /{id}/detalles/{detalle_id}` aflojándole la guarda de estado,
    ese mismo usuario podría subirle el precio por litro a la quincena de $500.000 que ya
    se pagó, sin que intervenga nadie con permiso de plata.

    Se le prueban las TRES puertas sobre la quincena pagada: la de la corrección
    (403, no tiene 'administrar'), y las dos de 'editar' que sí puede abrir en un
    borrador (422, la quincena pagada no se edita por el camino normal). Ninguna le
    mueve un peso.
    """
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Compras", "rol.compras")
    ha = auth_headers(client, "admin.a")
    hc = auth_headers(client, "rol.compras")

    # (1) Compras SÍ edita un borrador: 100 L a $2.000 = $200.000, y a $2.500 = $250.000.
    prov_b = _proveedor(client, ha, "Marlion")
    assert client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-06-03", "proveedor_id": prov_b["id"], "cantidad_litros": "100"},
        headers=ha,
    ).status_code == 201
    borrador = _generar(client, ha, prov_b["id"])
    detalle = borrador["detalles"][0]
    print("\n===== d) COMPRAS =====")
    print(f"  borrador de Marlion: ${D(borrador['valor_total']):,}")
    assert D(borrador["valor_total"]) == D("200000.00")

    cambiado = client.put(
        f"{API}/{borrador['id']}/detalles/{detalle['id']}",
        json={"precio_litro": "2500"},
        headers=hc,
    )
    print(f"  Compras le cambia el precio al borrador: {cambiado.status_code} -> "
          f"${D(cambiado.json()['valor_total']):,}")
    assert cambiado.status_code == 200, cambiado.text
    assert D(cambiado.json()["valor_total"]) == D("250000.00")

    # (2) La misma persona, contra la quincena PAGADA de $500.000.
    prov, pagada = quincena_pagada(client, ha)
    liq_id = pagada["id"]
    detalle_pagado = _detalles_de(client, ha, liq_id)[0]
    rec_id = dia_olvidado(client, ha, prov, liq_id)

    puerta_correccion = client.post(
        f"{API}/{liq_id}/corregir",
        json={"motivo": "subir el precio por la puerta de atras", "recepciones_a_incluir": [rec_id]},
        headers=hc,
    )
    print(f"  Compras corrige la pagada:               {puerta_correccion.status_code} · "
          f"{puerta_correccion.json()['error']['detail']}")
    assert puerta_correccion.status_code == 403
    assert "administrar" in puerta_correccion.json()["error"]["detail"]

    precio_de_la_pagada = client.put(
        f"{API}/{liq_id}/detalles/{detalle_pagado['id']}",
        json={"precio_litro": "2500"},
        headers=hc,
    )
    print(f"  Compras le sube el precio por litro:     {precio_de_la_pagada.status_code} · "
          f"{precio_de_la_pagada.json()['error']['detail']}")
    assert precio_de_la_pagada.status_code == 422, precio_de_la_pagada.text

    observaciones = client.put(
        f"{API}/{liq_id}", json={"observaciones": "toco por la puerta de atras"}, headers=hc
    )
    print(f"  Compras edita las observaciones:        {observaciones.status_code} · "
          f"{observaciones.json().get('error', {}).get('detail')}")
    assert observaciones.status_code == 422, observaciones.text

    # (3) NI UN PESO SE MOVIÓ. 250 L a $2.500 habrían sido $625.000: $125.000 de más
    #     entregados por alguien que no tiene permiso de plata.
    valor, pagado, saldo, estado, version = _cifras(client, ha, liq_id)
    print(f"  la quincena sigue en: ${valor:,} · pagado ${pagado:,} · saldo ${saldo:,} · "
          f"{estado} · v{version}")
    assert valor == VALOR_QUINCENA, f"Compras movió la quincena a {valor}"
    assert pagado == VALOR_QUINCENA
    assert saldo == D("0.00")
    assert estado == "pagada"
    assert version == 1


def _detalles_de(client, h, liq_id):
    """Los renglones (días) de la quincena."""
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    detalles = r.json().get("detalles")
    assert detalles, "la liquidación no devolvió sus renglones"
    return detalles


# ===========================================================================
# e) Sin token no sale ni una cifra
# ===========================================================================
def test_sin_token_las_tres_puertas_responden_401_y_no_sueltan_la_cifra(client, base_datos):
    """Un anónimo no corrige, y tampoco averigua cuánto se le pagó a Henri.

    Se mide con y sin un token basura: las dos formas de llegar sin credencial válida.
    """
    ha = auth_headers(client, "admin.a")
    prov, pagada = quincena_pagada(client, ha)
    liq_id = pagada["id"]
    rec_id = dia_olvidado(client, ha, prov, liq_id)
    payload = {"motivo": "sin credencial", "recepciones_a_incluir": [rec_id]}

    print("\n===== e) SIN TOKEN =====")
    intentos = [
        ("previsualizar", lambda h: client.post(f"{API}/{liq_id}/corregir/previsualizar", json=payload, headers=h)),
        ("corregir", lambda h: client.post(f"{API}/{liq_id}/corregir", json=payload, headers=h)),
        ("correcciones", lambda h: client.get(f"{API}/{liq_id}/correcciones", headers=h)),
    ]
    for etiqueta, llamar in intentos:
        for como, headers in (("sin header", {}), ("token basura", {"Authorization": "Bearer no-soy-un-token"})):
            r = llamar(headers)
            print(f"  {etiqueta:14} {como:14} {r.status_code}")
            assert r.status_code in (401, 403), f"{etiqueta} {como}: {r.status_code} {r.text}"
            assert "500000" not in r.text and "680000" not in r.text, r.text
            assert "Henri" not in r.text, r.text

    valor, _pagado, _saldo, estado, version = _cifras(client, ha, liq_id)
    print(f"  la quincena sigue en ${valor:,} · {estado} · v{version}")
    assert valor == VALOR_QUINCENA
    assert version == 1


# ===========================================================================
# f) El administrador DE LA OTRA quesera tiene el permiso y aun así no entra
# ===========================================================================
def test_el_administrador_de_la_otra_quesera_no_corrige_esta_quincena(client, base_datos):
    """El permiso viaja con la empresa, no con la persona.

    admin.b tiene 'liquidaciones:administrar' —es Administrador Empresa de la Quesera
    B— y esa es exactamente la credencial que abre la corrección. Contra la quincena de
    la Quesera A no le sirve, y el rechazo tampoco le confirma que exista ni cuánto vale.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prov, pagada = quincena_pagada(client, ha)
    liq_id = pagada["id"]
    rec_id = dia_olvidado(client, ha, prov, liq_id)
    payload = {"motivo": "corregir la quincena del vecino", "recepciones_a_incluir": [rec_id]}

    print("\n===== f) LA OTRA QUESERA =====")
    for etiqueta, r in (
        ("previsualizar", client.post(f"{API}/{liq_id}/corregir/previsualizar", json=payload, headers=hb)),
        ("corregir", client.post(f"{API}/{liq_id}/corregir", json=payload, headers=hb)),
        ("correcciones", client.get(f"{API}/{liq_id}/correcciones", headers=hb)),
    ):
        print(f"  admin.b {etiqueta:14} {r.status_code}")
        assert r.status_code == 404, f"{etiqueta}: {r.status_code} {r.text}"
        assert "500000" not in r.text and "680000" not in r.text, r.text
        assert "Henri" not in r.text, r.text

    valor, _pagado, _saldo, _estado, version = _cifras(client, ha, liq_id)
    print(f"  la quincena de la Quesera A sigue en ${valor:,} · v{version}")
    assert valor == VALOR_QUINCENA
    assert version == 1


# ===========================================================================
# g) El superadmin: el chequeo implícito, medido y no supuesto
# ===========================================================================
def test_el_superadmin_corrige_por_el_chequeo_implicito_y_queda_firmado(client, base_datos):
    """El Administrador General no lleva filas de permisos: `tiene_permiso` lo aprueba
    siempre. Esto NO es un hallazgo, es el diseño del RBAC del proyecto —el mismo con el
    que ya aprueba y paga—, pero queda medido: si mañana se decide que ni él corrige
    plata ajena, esta es la prueba que hay que cambiar a propósito.

    Se exige, eso sí, que la corrección quede FIRMADA con su nombre: una corrección sin
    autor no se distingue de un error.
    """
    ha = auth_headers(client, "admin.a")
    prov, pagada = quincena_pagada(client, ha)
    liq_id = pagada["id"]
    rec_id = dia_olvidado(client, ha, prov, liq_id)

    hs = auth_headers(client, "superadmin")
    hs["X-Empresa-Id"] = str(base_datos["empresa_a"].id)
    r = client.post(
        f"{API}/{liq_id}/corregir",
        json={"motivo": "el soporte entro a arreglarlo", "recepciones_a_incluir": [rec_id]},
        headers=hs,
    )
    print("\n===== g) SUPERADMIN =====")
    print(f"  corregir: {r.status_code}")
    assert r.status_code == 200, r.text
    c = r.json()
    print(f"  ${D(c['valor_total']):,} · pagado ${D(c['pagado']):,} · saldo ${D(c['saldo']):,} · "
          f"{c['estado']} · v{c['version']}")
    assert D(c["valor_total"]) == VALOR_QUINCENA + VALOR_DIA_OLVIDADO
    assert D(c["saldo"]) == VALOR_DIA_OLVIDADO
    assert D(c["neto_a_pagar"]) == D(c["pagado"]) + D(c["saldo"])

    fila = client.get(f"{API}/{liq_id}/correcciones", headers=ha).json()[0]
    print(f"  firmada por: {fila.get('corregido_por_nombre')}")
    assert fila.get("corregido_por_nombre"), f"la corrección quedó sin autor: {fila}"


# ===========================================================================
# h) Lo más lejos que llega un rol SIN 'administrar': quitarle el día a la
#    corrección con "Generar", que es su trabajo de todos los días
# ===========================================================================
def test_compras_puede_llevarse_el_dia_suelto_a_un_borrador_pero_no_entregar_la_plata(
    client, base_datos, db_session
):
    """El día del 12/06 vale $180.000 y está suelto. Compras no puede corregir la
    quincena, pero SÍ puede oprimir "Generar" —es su oficio— y ese día se va a un
    comprobante NUEVO, que es justo la opción de DOS PAPELES que el dueño descartó.

    Se mide qué tan grave es, que es lo que importa:

      · la quincena pagada NO SE MUEVE: sigue en $500.000, pagado $500.000, saldo $0,
        'pagada' y versión 1. Compras no le tocó un peso a lo que ya se entregó;
      · la corrección del administrador NO SE ROMPE EN SILENCIO: el día deja de salir
        como suelto y, si él ya lo tenía escogido en la pantalla, el sistema rebota con
        un mensaje que nombra lo que pasó ("ya no está suelto... otra liquidación se lo
        haya llevado") en vez de subir la versión sin cambiar la cifra;
      · y el papel nuevo NO LE ENTREGA LA PLATA a nadie: nace en borrador y aprobarlo y
        pagarlo siguen pidiendo 'administrar'. Los $180.000 no salen de la caja sin que
        pase por las manos del dueño.

    El desenlace es recuperable: el administrador borra o anula ese borrador y vuelve a
    correr la corrección. Por eso esto se documenta y no se marca como defecto.
    """
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Compras", "rol.compras")
    ha = auth_headers(client, "admin.a")
    hc = auth_headers(client, "rol.compras")
    prov, pagada = quincena_pagada(client, ha)
    liq_id = pagada["id"]
    rec_id = dia_olvidado(client, ha, prov, liq_id)

    print("\n===== h) COMPRAS OPRIME 'GENERAR' CON EL DÍA SUELTO AFUERA =====")
    generado = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"},
        headers=hc,
    )
    assert generado.status_code == 200, generado.text
    nuevas = generado.json()["generadas"]
    print(f"  generó {len(nuevas)} comprobante(s): "
          f"{[(D(x['valor_total']), x['estado']) for x in nuevas]}")
    assert len(nuevas) == 1
    nueva = nuevas[0]
    assert D(nueva["valor_total"]) == VALOR_DIA_OLVIDADO
    assert nueva["estado"] == "borrador"
    assert nueva["id"] != liq_id

    # La corrección del administrador ya no encuentra el día, Y LO DICE.
    prev = client.post(
        f"{API}/{liq_id}/corregir/previsualizar", json={"motivo": "el dia 12"}, headers=ha
    )
    assert prev.status_code == 200, prev.text
    print(f"  días sueltos que le quedan al administrador: {prev.json()['dias_sueltos']}")
    assert prev.json()["dias_sueltos"] == []

    rebote = client.post(
        f"{API}/{liq_id}/corregir",
        json={"motivo": "el dia 12 se anoto tarde", "recepciones_a_incluir": [rec_id]},
        headers=ha,
    )
    print(f"  la corrección rebota: {rebote.status_code} · {rebote.json()['error']['detail']}")
    assert rebote.status_code == 422, rebote.text
    assert "ya no está suelto" in rebote.json()["error"]["detail"]

    # NI UN PESO EN LA QUINCENA PAGADA.
    valor, pagado, saldo, estado, version = _cifras(client, ha, liq_id)
    print(f"  la quincena pagada: ${valor:,} · pagado ${pagado:,} · saldo ${saldo:,} · "
          f"{estado} · v{version}")
    assert (valor, pagado, saldo, estado, version) == (
        VALOR_QUINCENA, VALOR_QUINCENA, D("0.00"), "pagada", 1
    )

    # Y LOS $180.000 DEL PAPEL NUEVO NO SALEN DE LA CAJA SIN EL DUEÑO.
    aprobar = client.post(f"{API}/{nueva['id']}/aprobar", headers=hc)
    pagar = client.post(f"{API}/{nueva['id']}/pagar", headers=hc)
    print(f"  Compras aprueba el borrador nuevo: {aprobar.status_code} · "
          f"paga: {pagar.status_code}")
    assert aprobar.status_code == 403, aprobar.text
    assert pagar.status_code == 403, pagar.text
    sigue = client.get(f"{API}/{nueva['id']}", headers=ha).json()
    assert sigue["estado"] == "borrador"
    assert D(sigue["pagado"]) == D("0.00")


# ===========================================================================
# i) Después de corregir, la quincena queda en 'parcial': esa NO es una rendija
# ===========================================================================
def test_la_quincena_corregida_en_parcial_sigue_cerrada_para_el_rol_de_editar(
    client, base_datos, db_session
):
    """La corrección deja la quincena en 'parcial', y 'parcial' es un estado nuevo para
    estas guardas: hasta ahora una liquidación quedaba en parcial solo por un abono.

    Si alguna de las tres puertas de 'editar' —observaciones, precio por litro y
    recalcular— mirara únicamente 'pagada', el rol Compras tendría, JUSTO DESPUÉS DE LA
    CORRECCIÓN, la ventana que la puerta propia existía para negarle: 250 L del 02/06 de
    $2.000 a $2.500 son $125.000 de más en un comprobante del que ya salieron $500.000.
    """
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Compras", "rol.compras")
    ha = auth_headers(client, "admin.a")
    hc = auth_headers(client, "rol.compras")
    prov, pagada = quincena_pagada(client, ha)
    liq_id = pagada["id"]
    rec_id = dia_olvidado(client, ha, prov, liq_id)
    corregida = client.post(
        f"{API}/{liq_id}/corregir",
        json={"motivo": "el dia 12 se anoto tarde", "recepciones_a_incluir": [rec_id]},
        headers=ha,
    )
    assert corregida.status_code == 200, corregida.text
    corregida = corregida.json()
    assert corregida["estado"] == "parcial" and corregida["version"] == 2

    print("\n===== i) LA CORREGIDA, EN 'PARCIAL', CONTRA EL ROL COMPRAS =====")
    detalle_02 = next(d for d in corregida["detalles"] if d["fecha"] == "2026-06-02")
    intentos = {
        "observaciones": client.put(
            f"{API}/{liq_id}", json={"observaciones": "por la rendija"}, headers=hc
        ),
        "precio por litro": client.put(
            f"{API}/{liq_id}/detalles/{detalle_02['id']}",
            json={"precio_litro": "2500"},
            headers=hc,
        ),
        "recalcular": client.post(f"{API}/{liq_id}/recalcular", headers=hc),
    }
    for etiqueta, r in intentos.items():
        print(f"  {etiqueta:18} {r.status_code} · {r.json()['error']['detail'][:80]}")
        assert r.status_code == 422, f"{etiqueta}: {r.status_code} {r.text}"

    valor, pagado, saldo, estado, version = _cifras(client, ha, liq_id)
    print(f"  sigue en: ${valor:,} · pagado ${pagado:,} · saldo ${saldo:,} · {estado} · v{version}")
    assert (valor, pagado, saldo, estado, version) == (
        VALOR_QUINCENA + VALOR_DIA_OLVIDADO, VALOR_QUINCENA, VALOR_DIA_OLVIDADO, "parcial", 2
    )
    assert D(corregida["neto_a_pagar"]) == D(corregida["pagado"]) + D(corregida["saldo"])


# ===========================================================================
# j) El permiso viaja con la EMPRESA ACTIVA, y un usuario bloqueado no entra
# ===========================================================================
def test_el_permiso_de_corregir_no_se_trae_de_la_otra_quesera_ni_sobrevive_al_bloqueo(
    client, base_datos, db_session
):
    """Dos formas de llegar con la credencial "casi" buena, y las dos se miden con plata.

    (1) EL CONTRATISTA DE DOS QUESERAS: la misma persona es Compras en la Quesera A y
        Administrador Empresa en la Quesera B. Tiene 'liquidaciones:administrar' —en B—.
        Contra la quincena de A no le sirve ni sin header, ni pidiendo empresa activa A
        (ahí es Compras y le falta el permiso), ni pidiendo empresa activa B (ahí tiene
        el permiso pero la quincena no existe). Si los permisos se acumularan entre
        empresas, este usuario le movería los $500.000 de un productor de otra quesera.

    (2) EL USUARIO BLOQUEADO: entró con el rol correcto y le bloquearon la cuenta
        después. El token que ya tiene en la mano no le sirve: el chequeo se hace en
        cada petición y no en el login.
    """
    from app.modules.usuarios.models import Usuario, UsuarioRol

    empresa_a = base_datos["empresa_a"]
    empresa_b = base_datos["empresa_b"]
    contratista = crear_usuario_con_rol(db_session, empresa_a, "Compras", "contratista")
    rol_admin = db_session.scalars(select(Rol).where(Rol.nombre == ROL_QUE_CORRIGE)).one()
    db_session.add(
        UsuarioRol(usuario_id=contratista.id, rol_id=rol_admin.id, empresa_id=empresa_b.id)
    )
    db_session.commit()

    ha = auth_headers(client, "admin.a")
    prov, pagada = quincena_pagada(client, ha)
    liq_id = pagada["id"]
    rec_id = dia_olvidado(client, ha, prov, liq_id)
    payload = {"motivo": "en la otra quesera si soy el administrador",
               "recepciones_a_incluir": [rec_id]}

    print("\n===== j1) EL CONTRATISTA DE DOS QUESERAS =====")
    h = auth_headers(client, "contratista")
    casos = {
        "sin header (activa = A)": dict(h),
        "X-Empresa-Id = A": {**h, "X-Empresa-Id": str(empresa_a.id)},
        "X-Empresa-Id = B": {**h, "X-Empresa-Id": str(empresa_b.id)},
    }
    esperado = {"sin header (activa = A)": 403, "X-Empresa-Id = A": 403, "X-Empresa-Id = B": 404}
    for etiqueta, headers in casos.items():
        r = client.post(f"{API}/{liq_id}/corregir", json=payload, headers=headers)
        print(f"  {etiqueta:24} {r.status_code} · {r.json()['error']['detail'][:70]}")
        assert r.status_code == esperado[etiqueta], f"{etiqueta}: {r.status_code} {r.text}"
        assert "500000" not in r.text and "680000" not in r.text, r.text

    print("===== j2) EL USUARIO BLOQUEADO DESPUÉS DE ENTRAR =====")
    crear_usuario_con_rol(db_session, empresa_a, ROL_QUE_CORRIGE, "admin.bloqueado")
    h_bloqueado = auth_headers(client, "admin.bloqueado")  # el token sale ANTES del bloqueo
    usuario = db_session.scalars(
        select(Usuario).where(Usuario.username == "admin.bloqueado")
    ).one()
    usuario.bloqueado = True
    db_session.commit()
    r = client.post(f"{API}/{liq_id}/corregir", json=payload, headers=h_bloqueado)
    print(f"  con el token que ya tenía: {r.status_code} · {r.json()['error']['detail']}")
    assert r.status_code == 403, r.text

    valor, pagado, saldo, estado, version = _cifras(client, ha, liq_id)
    print(f"  la quincena sigue en ${valor:,} · pagado ${pagado:,} · saldo ${saldo:,} · "
          f"{estado} · v{version}")
    assert (valor, pagado, saldo, estado, version) == (
        VALOR_QUINCENA, VALOR_QUINCENA, D("0.00"), "pagada", 1
    )
