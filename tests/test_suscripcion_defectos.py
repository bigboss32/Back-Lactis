"""Los defectos que encontró la revisión adversarial del módulo de suscripción.

Cada prueba de aquí FALLABA antes del arreglo. Están juntas y no repartidas por
módulo a propósito: son la constancia de qué se rompió, por qué, y qué lo
protege ahora.

El más grave de todos no lo veía NINGUNA prueba porque las pruebas corren sobre
SQLite y el defecto solo existe en Postgres (ver
test_bloquear_el_pago_no_arrastra_el_join). Esa es la lección que hay que
recordar de este lote: cuando el código depende de la semántica de la base
—bloqueos, tipos, funciones—, SQLite no sirve de testigo.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import lazyload

from tests.conftest import auth_headers


def D(valor):
    return Decimal(str(valor))


# ---------------------------------------------------------------------------
# 1. CRÍTICO: FOR UPDATE sobre el lado nullable de un LEFT OUTER JOIN
# ---------------------------------------------------------------------------
def test_bloquear_el_pago_no_arrastra_el_join():
    """EL DEFECTO MÁS GRAVE DEL LOTE, y el que ninguna prueba veía.

    `PagoSuscripcion.fuente_pago` es lazy="joined" sobre una FK NULLABLE, así que
    SQLAlchemy le mete un LEFT OUTER JOIN a todo select del pago. Postgres
    rechaza `FOR UPDATE` sobre el lado exterior de un LEFT JOIN (SQLSTATE 0A000),
    y `aplicar_resultado` es el ÚNICO punto que acredita un pago: en producción
    la tarjeta se debitaba y la suscripción no se activaba NUNCA.

    SQLite no lo delata porque descarta FOR UPDATE en silencio, así que la suite
    entera pasaba con el defecto puesto. Por eso esta prueba no ejecuta nada:
    COMPILA la sentencia contra el dialecto de Postgres y mira el SQL.

    El mismo problema ya se había resuelto en ventas/service.py; aquí se había
    omitido la mitigación.
    """
    from app.modules.suscripcion.models import PagoSuscripcion

    def sql_de(consulta):
        return str(consulta.compile(dialect=postgresql.dialect()))

    # Cómo estaba: el join se cuela y Postgres lo rechazaría
    sin_arreglo = sql_de(
        select(PagoSuscripcion).where(PagoSuscripcion.id == None).with_for_update()  # noqa: E711
    )
    print("\n===== 1. FOR UPDATE Y EL JOIN =====")
    print(f"  sin la mitigación, ¿hay LEFT OUTER JOIN?  {'LEFT OUTER JOIN' in sin_arreglo}")
    assert "LEFT OUTER JOIN" in sin_arreglo, (
        "si esto falla es que fuente_pago dejó de ser lazy='joined': revise si la "
        "mitigación de service.py sigue haciendo falta"
    )

    # Cómo quedó: sin join, el FOR UPDATE es válido en Postgres
    con_arreglo = sql_de(
        select(PagoSuscripcion)
        .where(PagoSuscripcion.id == None)  # noqa: E711
        .options(lazyload(PagoSuscripcion.fuente_pago))
        .with_for_update()
    )
    print(f"  con la mitigación, ¿hay LEFT OUTER JOIN?  {'LEFT OUTER JOIN' in con_arreglo}")
    print(f"  ¿conserva el FOR UPDATE?                  {'FOR UPDATE' in con_arreglo}")
    assert "LEFT OUTER JOIN" not in con_arreglo
    assert "FOR UPDATE" in con_arreglo


def test_el_servicio_usa_la_mitigacion():
    """Que la mitigación esté DONDE se usa, no solo que exista.

    Se mira el código porque el efecto solo se ve en Postgres: una prueba de
    comportamiento sobre SQLite pasaría igual con y sin el arreglo.
    """
    import inspect

    from app.modules.suscripcion.service import SuscripcionService

    fuente = inspect.getsource(SuscripcionService.aplicar_resultado)
    print("\n===== 2. LA MITIGACIÓN ESTÁ PUESTA =====")
    print(f"  aplicar_resultado usa lazyload: {'lazyload(PagoSuscripcion.fuente_pago)' in fuente}")
    assert "lazyload(PagoSuscripcion.fuente_pago)" in fuente, (
        "aplicar_resultado bloquea el pago con FOR UPDATE y DEBE excluir el join "
        "de fuente_pago, o Postgres aborta con 0A000 y ningún pago se acredita"
    )


# ---------------------------------------------------------------------------
# 3. El paywall tiene que CERRAR cuando no puede establecer que está al día
# ---------------------------------------------------------------------------
def test_la_empresa_borrada_no_queda_liberada_del_cobro(client, base_datos, db_session):
    """Antes, borrar la empresa le quitaba el PAYWALL en vez del acceso.

    El camino no era rebuscado: es lo que hace un superadmin con un cliente
    moroso —borrarlo de la lista—, y el efecto era el contrario del que busca.
    """
    empresa = base_datos["empresa_a"]
    # Vencida hace rato: con la empresa viva esto ya está bloqueado
    empresa.pagada_hasta = date.today() - timedelta(days=90)
    db_session.commit()
    h = auth_headers(client, "admin.a")
    vencida = client.get("/api/v1/proveedores", headers=h)
    print("\n===== 3. LA EMPRESA BORRADA =====")
    print(f"  vencida y viva:    {vencida.status_code} ({vencida.json().get('error', {}).get('code')})")
    assert vencida.status_code == 403
    assert vencida.json()["error"]["code"] == "suscripcion_vencida"

    # Y ahora borrada: NO puede quedar mejor que antes
    empresa.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    borrada = client.get("/api/v1/proveedores", headers=h)
    print(f"  vencida y borrada: {borrada.status_code} ({borrada.json().get('error', {}).get('code')})")
    assert borrada.status_code == 403, (
        "la empresa borrada entró a un módulo de negocio: el paywall falló ABIERTO"
    )
    assert borrada.json()["error"]["code"] == "empresa_no_disponible"


def test_solo_el_superadmin_borra_empresas(client, base_datos, db_session):
    """Empresa NO tiene columna empresa_id, así que el repositorio no recorta por
    tenant: sin validar_eliminar, quien tuviera el permiso borraba la empresa de
    OTRO cliente. Y encadenado con el paywall, borrar la propia era la forma de
    dejar de pagar."""
    from app.core.security import hash_password
    from app.modules.usuarios.models import Permiso, Rol, Usuario
    from tests.conftest import PASSWORD

    # Un rol a la medida CON el permiso de eliminar empresas
    rol = Rol(nombre="Borra empresas", descripcion="prueba", es_sistema=False)
    db_session.add(rol)
    db_session.flush()
    permiso = db_session.scalars(
        select(Permiso).where(Permiso.modulo == "empresas", Permiso.accion == "eliminar")
    ).first()
    assert permiso is not None
    rol.permisos = [permiso]
    usuario = Usuario(
        nombre="Con", apellido="Permiso", correo="borra@test.local", username="borra.empresas",
        hashed_password=hash_password(PASSWORD), empresa_id=base_datos["empresa_a"].id,
    )
    usuario.roles = [rol]
    db_session.add(usuario)
    db_session.commit()

    h = auth_headers(client, "borra.empresas")
    ajena = client.delete(f"/api/v1/empresas/{base_datos['empresa_b'].id}", headers=h)
    propia = client.delete(f"/api/v1/empresas/{base_datos['empresa_a'].id}", headers=h)
    print("\n===== 4. BORRAR EMPRESAS =====")
    print(f"  con permiso, empresa AJENA:  {ajena.status_code}")
    print(f"  con permiso, empresa PROPIA: {propia.status_code}")
    assert ajena.status_code == 403, "se borró la empresa de otro cliente"
    assert propia.status_code == 403, "se borró la propia empresa (y así se deja de pagar)"

    # El superadmin sí puede
    hs = auth_headers(client, "superadmin")
    ok = client.delete(f"/api/v1/empresas/{base_datos['empresa_b'].id}", headers=hs)
    print(f"  superadmin:                  {ok.status_code}")
    assert ok.status_code == 204, ok.text


# ---------------------------------------------------------------------------
# 5. compare_digest con texto no ASCII: 500 público en vez de 400/403
# ---------------------------------------------------------------------------
def test_un_checksum_con_tilde_no_tumba_el_webhook(client, base_datos):
    """El webhook es PÚBLICO. Un checksum con un carácter fuera de ASCII hacía
    que hmac.compare_digest lanzara TypeError y el endpoint devolviera 500 en vez
    del 400 que le dice a Wompi que reintente."""
    from app.core.config import settings

    settings.WOMPI_EVENT_SECRET = "secreto-de-prueba"
    try:
        r = client.post(
            "/api/v1/suscripcion/webhook",
            json={"timestamp": 1, "signature": {"checksum": "é-con-tilde", "properties": []},
                  "data": {}},
        )
        print("\n===== 5. CHECKSUM CON TILDE =====")
        print(f"  webhook con checksum no ASCII: {r.status_code}")
        assert r.status_code != 500, "el webhook público se cae con un carácter no ASCII"
        assert r.status_code == 400
    finally:
        settings.WOMPI_EVENT_SECRET = ""


def test_el_secreto_de_cron_rechaza_sin_caerse(client, base_datos):
    """El endpoint de cron con un secreto equivocado tiene que dar 403.

    OJO con el alcance: por HTTP el header NO puede traer caracteres fuera de
    ASCII —el propio cliente lo rechaza antes de salir—, así que el TypeError de
    compare_digest no era alcanzable por esta puerta. La comparación en bytes se
    dejó igual porque es lo correcto, pero la prueba no puede afirmar que
    arreglaba un 500 que no existía: aquí solo se comprueba el 403.

    Donde el defecto SÍ era alcanzable es en el webhook, cuyo checksum viaja en
    el CUERPO y no en un header (ver test_un_checksum_con_tilde_no_tumba_el_webhook).
    """
    from app.core.config import settings

    settings.SUSCRIPCION_CRON_SECRET = "secreto-de-prueba"
    try:
        malo = client.post(
            "/api/v1/suscripcion/cobrar-vencidas", headers={"X-Cron-Secret": "clave-mala"}
        )
        sin_header = client.post("/api/v1/suscripcion/cobrar-vencidas")
        print("\n===== 6. SECRETO DE CRON =====")
        print(f"  con secreto equivocado: {malo.status_code}")
        print(f"  sin el header:          {sin_header.status_code}")
        assert malo.status_code == 403
        assert sin_header.status_code == 403
    finally:
        settings.SUSCRIPCION_CRON_SECRET = ""


def test_comparar_el_secreto_de_cron_nunca_lanza():
    """La comparación en sí, con texto no ASCII: devuelve False, no explota.

    No llega por HTTP, pero el secreto sale de una variable de entorno y esas sí
    pueden traer una tilde.
    """
    import hmac

    for secreto, recibido in [("clavé", "clave"), ("clave", "clavé"), ("ñ", "n")]:
        assert (
            hmac.compare_digest(secreto.encode("utf-8"), recibido.encode("utf-8")) is False
        )


@pytest.mark.parametrize(
    "checksum",
    ["ñ", "é-con-tilde", "ÁBCDEF0123456789", "🙂"],
    ids=["ene", "tilde", "mayusculas-acentuadas", "emoji"],
)
def test_la_validacion_del_checksum_nunca_lanza(checksum):
    """La función en sí: devuelve False, no explota, con cualquier texto."""
    from app.modules.suscripcion.wompi import validar_checksum_evento

    payload = {"timestamp": 1, "signature": {"checksum": checksum, "properties": []}, "data": {}}
    assert validar_checksum_evento(payload, "un-secreto") is False


# ---------------------------------------------------------------------------
# 7. El checksum tiene que cubrir lo que decide el resultado
# ---------------------------------------------------------------------------
def _firmar(propiedades, datos, timestamp, secreto):
    """Arma un evento con el checksum que Wompi calcularía para esas rutas."""
    import hashlib

    partes = []
    for ruta in propiedades:
        valor = datos
        for clave in ruta.split("."):
            valor = valor[clave]
        partes.append(str(valor))
    cadena = "".join(partes) + str(timestamp) + secreto
    return {
        "timestamp": timestamp,
        "data": datos,
        "signature": {
            "properties": list(propiedades),
            "checksum": hashlib.sha256(cadena.encode("utf-8")).hexdigest(),
        },
    }


TRES = ("transaction.id", "transaction.status", "transaction.amount_in_cents")
DATOS = {
    "transaction": {
        "id": "12345-1700000000-98765",
        "status": "APPROVED",
        "amount_in_cents": 8000000,
        "reference": "susc-abc123-def456789012",
    }
}


def test_un_evento_bien_firmado_se_acepta():
    """Primero lo obvio: el arreglo no puede romper el caso bueno."""
    from app.modules.suscripcion.wompi import validar_checksum_evento

    evento = _firmar(TRES, DATOS, 1700000000, "secreto")
    print("\n===== 7. EVENTO LEGÍTIMO =====")
    print(f"  firmado con las tres propiedades: {validar_checksum_evento(evento, 'secreto')}")
    assert validar_checksum_evento(evento, "secreto") is True
    # Y con el secreto equivocado, no
    assert validar_checksum_evento(evento, "otro-secreto") is False


def test_sin_propiedades_el_checksum_no_autentica_nada():
    """EL AGUJERO: con `properties: []` la cadena firmada era solo el timestamp
    y el secreto, así que el MISMO checksum valía para cualquier transacción,
    cualquier importe y cualquier estado. Quien viera un solo evento legítimo
    podía acreditarse la suscripción que quisiera."""
    from app.modules.suscripcion.wompi import validar_checksum_evento

    vacio = _firmar((), DATOS, 1700000000, "secreto")
    print("\n===== 8. SIN PROPIEDADES FIRMADAS =====")
    print(f"  checksum correcto para properties=[]: se acepta? "
          f"{validar_checksum_evento(vacio, 'secreto')}")
    assert validar_checksum_evento(vacio, "secreto") is False, (
        "un evento sin campos firmados no autentica nada de data y no puede pasar"
    )

    # Y el mismo checksum servía para OTRA transacción: esa es la gravedad
    otros_datos = {
        "transaction": {
            "id": "99999-0000000000-11111",
            "status": "APPROVED",
            "amount_in_cents": 1,
            "reference": "susc-victima-000000000000",
        }
    }
    reciclado = dict(vacio, data=otros_datos)
    print(f"  el mismo checksum sobre OTRA transacción: se acepta? "
          f"{validar_checksum_evento(reciclado, 'secreto')}")
    assert validar_checksum_evento(reciclado, "secreto") is False


@pytest.mark.parametrize(
    "quitar",
    ["transaction.status", "transaction.amount_in_cents", "transaction.id"],
)
def test_no_se_puede_dejar_fuera_un_campo_que_decide(quitar):
    """Aunque el checksum cuadre con las propiedades declaradas, dejar fuera el
    estado o el importe permitiría reciclar la firma cambiándolos."""
    from app.modules.suscripcion.wompi import validar_checksum_evento

    propiedades = tuple(p for p in TRES if p != quitar)
    evento = _firmar(propiedades, DATOS, 1700000000, "secreto")
    assert validar_checksum_evento(evento, "secreto") is False, (
        f"se aceptó un evento que no firma {quitar}"
    )
