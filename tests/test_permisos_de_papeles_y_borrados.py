"""Que cada endpoint exija la acción que de verdad hace.

Dos defectos que el dueño se encontró trabajando:

  1. GET /api/v1/nomina/{id}/pdf pedía 'empleados:consultar' en vez de
     'empleados:imprimir'. Era el ÚNICO PDF del sistema que no pedía 'imprimir',
     así que el rol 'Consulta' —que tiene el consultar de todos los módulos— se
     bajaba el sueldo de todo el mundo.
  2. DELETE /api/v1/transporte/servicios/{id}/abonos/{id} pedía 'transporte:crear'
     en vez de 'transporte:eliminar', así que un Auxiliar borraba plata ya
     recibida.

Lo que se prueba acá no es la línea arreglada: es que después del arreglo NADIE
que necesite el papel se quedó sin él, y que el barrido de los 186 endpoints no
dejó otro con el verbo cambiado. Se entra siempre POR EL ENDPOINT: el frontend
esconde botones, la API es la que manda.
"""
import ast
import pathlib
import uuid as _uuid

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.modules.usuarios.models import Rol, Usuario
from tests.conftest import PASSWORD, auth_headers


def usuario_con_rol(db_session, empresa_id, username, rol_nombre):
    rol = db_session.scalars(select(Rol).where(Rol.nombre == rol_nombre)).first()
    assert rol is not None, f"no existe el rol {rol_nombre}"
    usuario = Usuario(
        nombre=username,
        apellido="Prueba",
        correo=f"{username}@test.local",
        username=username,
        hashed_password=hash_password(PASSWORD),
        empresa_id=empresa_id,
    )
    usuario.roles = [rol]
    db_session.add(usuario)
    db_session.flush()
    db_session.commit()
    return usuario


def un_pago_de_nomina(client, admin):
    empleado = client.post(
        "/api/v1/empleados",
        json={"nombre": "Aurelio", "apellido": "Ricaute", "valor_dia": "41833.33"},
        headers=admin,
    ).json()
    respuesta = client.post(
        "/api/v1/nomina",
        json={"empleado_id": empleado["id"], "fecha": "2026-07-15", "dias_trabajados": "12.5"},
        headers=admin,
    )
    assert respuesta.status_code in (200, 201), respuesta.text
    return respuesta.json()


# ---------------------------------------------------------------------------
# 1. EL RECIBO DE NÓMINA
# ---------------------------------------------------------------------------
def test_el_rol_consulta_ya_no_se_baja_el_sueldo_de_todo_el_mundo(client, base_datos, db_session):
    """Este es el defecto: 'Consulta' es de solo mirar y bajaba la nómina."""
    admin = auth_headers(client, "admin.a")
    pago = un_pago_de_nomina(client, admin)

    usuario_con_rol(db_session, base_datos["empresa_a"].id, "solo.consulta", "Consulta")
    h = auth_headers(client, "solo.consulta")
    permisos = client.get("/api/v1/auth/me", headers=h).json()["permisos"]
    assert "empleados:consultar" in permisos
    assert "empleados:imprimir" not in permisos

    respuesta = client.get(f"/api/v1/nomina/{pago['id']}/pdf", headers=h)
    assert respuesta.status_code == 403, (
        f"el recibo de nómina salió con solo 'consultar': HTTP {respuesta.status_code}"
    )


@pytest.mark.parametrize("rol", ["Administrador Empresa", "Contador", "Supervisor"])
def test_quien_debe_imprimir_la_nomina_sigue_imprimiendola(client, base_datos, db_session, rol):
    """LO QUE NO PUEDE PASAR: que el arreglo le quite el recibo a quien lo necesita.

    Estos tres roles son los que la siembra deja con 'empleados:imprimir': el
    Administrador Empresa porque tiene todo, el Contador porque paga y
    contabiliza la nómina, y el Supervisor porque le entrega el recibo al
    trabajador y ya imprime los comprobantes de liquidación.
    """
    admin = auth_headers(client, "admin.a")
    pago = un_pago_de_nomina(client, admin)

    usuario = f"imprime.{rol.split()[0].lower()}"
    usuario_con_rol(db_session, base_datos["empresa_a"].id, usuario, rol)
    h = auth_headers(client, usuario)
    assert "empleados:imprimir" in client.get("/api/v1/auth/me", headers=h).json()["permisos"]

    respuesta = client.get(f"/api/v1/nomina/{pago['id']}/pdf", headers=h)
    assert respuesta.status_code == 200, (
        f"el rol '{rol}' PERDIÓ el recibo de nómina que hoy imprime: "
        f"HTTP {respuesta.status_code} {respuesta.text[:200]}"
    )
    assert respuesta.content[:4] == b"%PDF", "lo que salió no es un PDF"


def test_los_demas_roles_no_pierden_nada_porque_nunca_vieron_la_nomina(
    client, base_datos, db_session
):
    """Auxiliar, Producción, Compras, Ventas y Reventa no tienen ni
    'empleados:consultar', así que hoy tampoco alcanzaban el recibo: el arreglo
    no les cambia nada. Se comprueba para poder decirlo rol por rol y no de
    memoria."""
    admin = auth_headers(client, "admin.a")
    pago = un_pago_de_nomina(client, admin)

    for rol in ("Auxiliar", "Producción", "Compras", "Ventas", "Reventa"):
        usuario = "nom." + rol.lower().replace("ó", "o")
        usuario_con_rol(db_session, base_datos["empresa_a"].id, usuario, rol)
        h = auth_headers(client, usuario)
        permisos = client.get("/api/v1/auth/me", headers=h).json()["permisos"]
        assert "empleados:consultar" not in permisos, (
            f"'{rol}' sí consulta empleados: revise si el recibo le hace falta"
        )
        assert client.get(f"/api/v1/nomina/{pago['id']}/pdf", headers=h).status_code == 403


# ---------------------------------------------------------------------------
# 2. EL BORRADO DE UN ABONO DE FLETE
# ---------------------------------------------------------------------------
def un_flete_con_abono(client, admin):
    vehiculo = client.post(
        "/api/v1/transporte/vehiculos",
        json={"placa": "PRB123", "nombre": "La Turbo", "tarifa_kilo": "1200"},
        headers=admin,
    ).json()
    viaje = client.post(
        "/api/v1/transporte/viajes",
        json={
            "vehiculo_id": vehiculo["id"],
            "fecha_salida": "2026-07-10",
            "origen": "San José",
            "destino": "Villavicencio",
        },
        headers=admin,
    ).json()
    cliente = client.post(
        "/api/v1/clientes", json={"nombre": "Alba Ricaute"}, headers=admin
    ).json()
    servicio = client.post(
        f"/api/v1/transporte/viajes/{viaje['id']}/servicios",
        json={
            "descripcion": "Carga de queso",
            "tipo_cobro": "precio_fijo",
            "cliente_id": cliente["id"],
            "valor_total": "242760.00",
        },
        headers=admin,
    ).json()
    respuesta = client.post(
        f"/api/v1/transporte/servicios/{servicio['id']}/abonos",
        json={"fecha": "2026-07-12", "valor": "137450.00"},
        headers=admin,
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    # El desglose cuadra: 242.760,00 - 137.450,00 = 105.310,00
    assert float(cuerpo["saldo"]) == 105310.00
    return viaje, servicio, cuerpo["abonos"][0]["id"]


def test_el_auxiliar_ya_no_borra_plata_ya_recibida(client, base_datos, db_session):
    """El Auxiliar tiene transporte:crear (registra fletes y abonos) y NO
    transporte:eliminar. El borrado pedía 'crear', así que borraba un abono
    cobrado y le devolvía el saldo al cliente."""
    admin = auth_headers(client, "admin.a")
    viaje, servicio, abono_id = un_flete_con_abono(client, admin)

    usuario_con_rol(db_session, base_datos["empresa_a"].id, "aux.abono", "Auxiliar")
    aux = auth_headers(client, "aux.abono")
    permisos = client.get("/api/v1/auth/me", headers=aux).json()["permisos"]
    assert "transporte:crear" in permisos
    assert "transporte:eliminar" not in permisos

    borrado = client.delete(
        f"/api/v1/transporte/servicios/{servicio['id']}/abonos/{abono_id}", headers=aux
    )
    assert borrado.status_code == 403, (
        f"BORRÓ EL ABONO SIN PERMISO DE ELIMINAR: HTTP {borrado.status_code}"
    )

    # Y la plata sigue ahí: el saldo no se movió.
    despues = client.get(f"/api/v1/transporte/viajes/{viaje['id']}", headers=admin).json()
    servicio_ahora = next(s for s in despues["servicios"] if s["id"] == servicio["id"])
    assert float(servicio_ahora["saldo"]) == 105310.00
    assert len(servicio_ahora["abonos"]) == 1


def test_quien_sí_tiene_eliminar_todavia_corrige_un_abono_mal_registrado(
    client, base_datos, db_session
):
    """El arreglo no puede cerrarle la puerta a quien tiene que corregir: el
    abono mal digitado se sigue pudiendo borrar con 'transporte:eliminar'."""
    admin = auth_headers(client, "admin.a")
    viaje, servicio, abono_id = un_flete_con_abono(client, admin)

    borrado = client.delete(
        f"/api/v1/transporte/servicios/{servicio['id']}/abonos/{abono_id}", headers=admin
    )
    assert borrado.status_code == 200, borrado.text
    # Se fue el abono y el saldo vuelve al valor completo del flete.
    assert float(borrado.json()["saldo"]) == 242760.00
    assert borrado.json()["abonos"] == []


def test_el_auxiliar_sigue_registrando_abonos_que_es_lo_suyo(client, base_datos, db_session):
    """Se le quitó el borrado, no el registro: 'crear' es lo que el Auxiliar
    necesita para su trabajo normal y lo conserva."""
    admin = auth_headers(client, "admin.a")
    _viaje, servicio, _abono = un_flete_con_abono(client, admin)

    usuario_con_rol(db_session, base_datos["empresa_a"].id, "aux.crea", "Auxiliar")
    aux = auth_headers(client, "aux.crea")
    respuesta = client.post(
        f"/api/v1/transporte/servicios/{servicio['id']}/abonos",
        json={"fecha": "2026-07-13", "valor": "5310.00"},
        headers=aux,
    )
    assert respuesta.status_code == 200, respuesta.text
    # 242.760,00 - 137.450,00 - 5.310,00 = 100.000,00
    assert float(respuesta.json()["saldo"]) == 100000.00


# ---------------------------------------------------------------------------
# 3. EL BARRIDO: que no quede otro endpoint con el verbo cambiado
# ---------------------------------------------------------------------------
RAIZ_MODULOS = pathlib.Path(__file__).resolve().parents[1] / "app" / "modules"

# Un DELETE puede pedir 'eliminar' (borra la fila) o 'administrar' (la anula, que
# es más fuerte). Cualquier otra cosa es un borrado disfrazado de otra acción.
ACCIONES_DE_BORRADO = {"eliminar", "administrar"}
# Un papel (PDF/Excel) se imprime o se exporta; 'consultar' no alcanza, porque el
# papel sale de la pantalla y se lleva datos que la pantalla pagina y filtra.
ACCIONES_DE_PAPEL = {"imprimir", "exportar", "administrar"}


def _endpoints():
    """(modulo, metodo, ruta, permiso, funcion, linea) de todos los routers."""
    for archivo in sorted(RAIZ_MODULOS.glob("*/router.py")):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in nodo.decorator_list:
                if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                    continue
                if deco.func.attr not in {"get", "post", "put", "patch", "delete"}:
                    continue
                ruta = (
                    deco.args[0].value
                    if deco.args and isinstance(deco.args[0], ast.Constant)
                    else ""
                )
                yield (
                    archivo.parent.name,
                    deco.func.attr.upper(),
                    ruta,
                    _permiso_de(nodo),
                    nodo.name,
                    nodo.lineno,
                )


def _permiso_de(func):
    """La acción que exige el Depends(require_permission(...)) de la firma."""
    for defecto in list(func.args.defaults) + list(func.args.kw_defaults):
        if defecto is None:
            continue
        for nodo in ast.walk(defecto):
            if not isinstance(nodo, ast.Call):
                continue
            nombre = (
                nodo.func.id
                if isinstance(nodo.func, ast.Name)
                else getattr(nodo.func, "attr", "")
            )
            if nombre in ("require_permission", "require_any_permission"):
                acciones = [a.value for a in nodo.args[1:] if isinstance(a, ast.Constant)]
                modulo = nodo.args[0].value if isinstance(nodo.args[0], ast.Constant) else "?"
                return (modulo, tuple(acciones))
    return None


def test_ningun_borrado_se_conforma_con_un_permiso_mas_flojo():
    """Recorre TODOS los routers: si mañana alguien copia una firma y deja un
    DELETE pidiendo 'crear' —que es exactamente lo que pasó con el abono de
    flete—, esta prueba lo caza antes de que llegue a producción."""
    culpables = []
    for modulo, metodo, ruta, permiso, funcion, linea in _endpoints():
        if metodo != "DELETE":
            continue
        if permiso is None:
            culpables.append(f"{modulo}{ruta} ({funcion} L{linea}) no exige ningún permiso")
            continue
        _mod, acciones = permiso
        if not set(acciones) & ACCIONES_DE_BORRADO:
            culpables.append(
                f"{modulo}{ruta} ({funcion} L{linea}) borra pero exige {acciones}"
            )
    assert not culpables, "borrados con el permiso equivocado:\n  " + "\n  ".join(culpables)


def test_ningun_papel_sale_con_solo_consultar():
    """Todo PDF/Excel exige 'imprimir' o 'exportar'. El recibo de nómina era el
    único que se conformaba con 'consultar'."""
    culpables = []
    for modulo, metodo, ruta, permiso, funcion, linea in _endpoints():
        texto = f"{ruta} {funcion}".lower()
        if not any(p in texto for p in ("pdf", "excel", "xlsx", "exportar", "export")):
            continue
        if permiso is None:
            culpables.append(f"{modulo}{ruta} ({funcion} L{linea}) no exige ningún permiso")
            continue
        _mod, acciones = permiso
        if not set(acciones) & ACCIONES_DE_PAPEL:
            culpables.append(f"{modulo}{ruta} ({funcion} L{linea}) exige {acciones}")
    assert not culpables, "papeles con el permiso equivocado:\n  " + "\n  ".join(culpables)


def test_el_barrido_de_verdad_recorrio_los_routers():
    """Si el parser se rompe y no encuentra nada, las dos pruebas de arriba
    pasarían en vacío y no protegerían nada. Esta las respalda."""
    todos = list(_endpoints())
    assert len(todos) > 150, f"solo se leyeron {len(todos)} endpoints: el barrido está ciego"
    assert sum(1 for e in todos if e[1] == "DELETE") > 15
