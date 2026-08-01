"""Pago de la suscripción por PSE (débito desde el banco).

LA DIFERENCIA QUE MANDA con la tarjeta: PSE NO es recurrente. Cada débito exige
que una persona entre al portal de su banco y lo apruebe, así que sirve para
pagar ESTE mes y nada más. El cobro automático del cron tiene que seguir siendo
solo de tarjetas, y eso es lo que más se prueba aquí: si PSE se colara en el
cobro automático, el sistema intentaría debitar una cuenta sin que nadie lo
autorice y todos esos cobros fallarían mes tras mes.

Lo que SÍ se reutiliza es el webhook: acredita por referencia, así que un pago
PSE aprobado extiende la vigencia por el mismo camino que uno con tarjeta, sin
código nuevo. Eso también se prueba.

Wompi se reemplaza con dobles: no se llama a la pasarela de verdad.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/suscripcion"


def D(valor):
    return Decimal(str(valor))


BANCOS = [
    {"financial_institution_code": "1022", "financial_institution_name": "BANCO DE PRUEBA"},
    {"financial_institution_code": "1040", "financial_institution_name": "BANCO AGRARIO"},
]

ACEPTACION = {
    "presigned_acceptance": {"acceptance_token": "tok-aceptacion", "permalink": "https://x/t"},
    "presigned_personal_data_auth": {"acceptance_token": "tok-datos", "permalink": "https://x/d"},
}


class WompiFalso:
    """Doble del cliente de Wompi. Registra lo que se le pidió."""

    llamadas: list = []
    respuesta_pse: dict = {}
    revienta: Exception | None = None

    def __init__(self):
        pass

    def tokens_aceptacion(self):
        return ACEPTACION

    def bancos_pse(self):
        return BANCOS

    def crear_transaccion_pse(self, **kwargs):
        WompiFalso.llamadas.append(("pse", kwargs))
        if WompiFalso.revienta:
            raise WompiFalso.revienta
        return WompiFalso.respuesta_pse

    def crear_transaccion(self, **kwargs):
        WompiFalso.llamadas.append(("tarjeta", kwargs))
        return {"id": "tx-tarjeta", "status": "APPROVED"}

    def consultar_transaccion(self, transaction_id):
        return {"id": transaction_id, "status": "PENDING"}


@pytest.fixture()
def wompi(monkeypatch):
    """Reemplaza el cliente en TODOS los sitios donde se instancia."""
    WompiFalso.llamadas = []
    WompiFalso.revienta = None
    WompiFalso.respuesta_pse = {
        "id": "tx-pse-1",
        "status": "PENDING",
        "payment_method": {
            "type": "PSE",
            "extra": {"async_payment_url": "https://banco.example/pagar/abc"},
        },
    }
    import app.modules.suscripcion.service as servicio

    monkeypatch.setattr(servicio, "WompiClient", WompiFalso)
    return WompiFalso


def preparar(client, db_session, base_datos, tarifa="80000", vencida_hace=10):
    """Empresa vencida con tarifa, lista para pagar.

    Con correo y teléfono: los dos se los exige PSE a la pasarela (van en
    `customer_data`) y sin ellos el pago ni se intenta. Que falten se prueba
    aparte, en test_suscripcion_defectos_pse.py.
    """
    empresa = base_datos["empresa_a"]
    empresa.tarifa_mensual = D(tarifa)
    empresa.pagada_hasta = date.today() - timedelta(days=vencida_hace)
    empresa.correo = "quesera@ejemplo.co"
    empresa.telefono = "3107650926"
    db_session.commit()
    return empresa


# ---------------------------------------------------------------------------
# 1. El camino feliz: se crea el pago y se devuelve el banco
# ---------------------------------------------------------------------------
def test_pagar_por_pse_devuelve_la_url_del_banco(client, db_session, base_datos, wompi):
    empresa = preparar(client, db_session, base_datos)
    h = auth_headers(client, "admin.a")
    r = client.post(
        f"{API}/pse/pagar",
        json={"banco": "1022", "tipo_persona": "0", "tipo_documento": "CC",
              "documento": "1094123456"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    print("\n===== 1. PAGO POR PSE =====")
    print(f"  método:      {d['pago']['metodo']}")
    print(f"  estado:      {d['pago']['estado_transaccion']}")
    print(f"  monto:       {d['pago']['monto']}")
    print(f"  url_banco:   {d['url_banco']}")
    assert d["pago"]["metodo"] == "PSE"
    # PSE nace PENDING: el resultado lo trae el webhook cuando el banco responde
    assert d["pago"]["estado_transaccion"] == "PENDING"
    assert D(d["pago"]["monto"]) == 80_000
    assert d["url_banco"] == "https://banco.example/pagar/abc"
    # Y NO quedó atado a ninguna fuente de pago: PSE no se guarda para reusar.
    # Se mira en la base porque la respuesta no expone ese campo (es interno).
    from sqlalchemy import select

    from app.modules.suscripcion.models import PagoSuscripcion

    fila = db_session.scalars(
        select(PagoSuscripcion).where(PagoSuscripcion.empresa_id == empresa.id)
    ).one()
    print(f"  fuente_pago: {fila.fuente_pago_id}")
    assert fila.fuente_pago_id is None

    # Lo que se le mandó a Wompi
    tipo, enviado = wompi.llamadas[-1]
    print(f"  a Wompi:     banco={enviado['banco']} doc={enviado['tipo_documento']}"
          f" {enviado['documento']} persona={enviado['tipo_persona']}")
    assert tipo == "pse"
    assert enviado["banco"] == "1022"
    assert enviado["documento"] == "1094123456"
    # El monto va en CENTAVOS y entero: Wompi no admite decimales
    assert enviado["monto_en_centavos"] == 8_000_000
    assert isinstance(enviado["monto_en_centavos"], int)
    # Y con el correo de la empresa
    assert enviado["customer_email"] == "quesera@ejemplo.co"


def test_los_bancos_salen_de_wompi(client, db_session, base_datos, wompi):
    preparar(client, db_session, base_datos)
    h = auth_headers(client, "admin.a")
    r = client.get(f"{API}/pse/bancos", headers=h)
    assert r.status_code == 200, r.text
    print("\n===== 2. LISTA DE BANCOS =====")
    for b in r.json():
        print(f"  {b['financial_institution_code']}  {b['financial_institution_name']}")
    assert len(r.json()) == 2
    assert r.json()[0]["financial_institution_code"] == "1022"


# ---------------------------------------------------------------------------
# 3. LO QUE MANDA: PSE no entra en el cobro automático
# ---------------------------------------------------------------------------
def test_pse_no_se_cobra_solo_el_mes_siguiente(client, db_session, base_datos, wompi):
    """PSE exige que una persona apruebe en el banco. Si se colara en el cron, el
    sistema intentaría debitar sin autorización y fallaría mes tras mes.

    El cron busca una FUENTE de pago activa, y un pago PSE no crea ninguna: por
    eso la empresa que pagó por PSE queda 'omitida', no 'cobrada'.
    """
    from app.core.config import settings

    empresa = preparar(client, db_session, base_datos)
    h = auth_headers(client, "admin.a")
    client.post(
        f"{API}/pse/pagar",
        json={"banco": "1022", "documento": "1094123456"},
        headers=h,
    )
    # El pago PSE se aprueba (como haría el webhook)
    from app.modules.suscripcion.models import PagoSuscripcion
    from sqlalchemy import select

    pago = db_session.scalars(
        select(PagoSuscripcion).where(PagoSuscripcion.empresa_id == empresa.id)
    ).one()
    assert pago.metodo == "PSE"
    assert pago.fuente_pago_id is None

    # Ahora el cron: no hay tarjeta guardada, así que no puede cobrar sola
    settings.SUSCRIPCION_CRON_SECRET = "secreto-cron"
    try:
        r = client.post(
            f"{API}/cobrar-vencidas", headers={"X-Cron-Secret": "secreto-cron"}
        )
        assert r.status_code == 200, r.text
        contadores = r.json()
        print("\n===== 3. EL CRON NO COBRA POR PSE =====")
        print(f"  {contadores}")
        assert contadores["cobradas"] == 0, (
            "el cron cobró algo sin tarjeta guardada: PSE no puede ser recurrente"
        )
    finally:
        settings.SUSCRIPCION_CRON_SECRET = ""

    # Y no se le pidió a Wompi ninguna transacción de tarjeta
    tipos = [t for t, _ in wompi.llamadas]
    print(f"  llamadas a Wompi: {tipos}")
    assert "tarjeta" not in tipos


# ---------------------------------------------------------------------------
# 4. El webhook acredita igual, sin código nuevo
# ---------------------------------------------------------------------------
def test_el_webhook_acredita_un_pago_pse(client, db_session, base_datos, wompi):
    """Es lo que hace que PSE sea barato de sostener: la vigencia se extiende por
    el mismo camino que con tarjeta, porque el webhook resuelve por referencia."""
    import hashlib

    from app.core.config import settings
    from app.modules.suscripcion.models import PagoSuscripcion
    from sqlalchemy import select

    empresa = preparar(client, db_session, base_datos)
    antes = empresa.pagada_hasta
    h = auth_headers(client, "admin.a")
    client.post(f"{API}/pse/pagar", json={"banco": "1022", "documento": "1094111"}, headers=h)
    pago = db_session.scalars(
        select(PagoSuscripcion).where(PagoSuscripcion.empresa_id == empresa.id)
    ).one()

    # El evento que mandaría Wompi cuando el banco aprueba
    settings.WOMPI_EVENT_SECRET = "secreto-eventos"
    try:
        datos = {
            "transaction": {
                "id": "tx-pse-1",
                "status": "APPROVED",
                "amount_in_cents": 8_000_000,
                "reference": pago.referencia,
            }
        }
        propiedades = ["transaction.id", "transaction.status", "transaction.amount_in_cents"]
        cadena = "".join(
            str(datos["transaction"][p.split(".")[1]]) for p in propiedades
        ) + "1700000000" + "secreto-eventos"
        evento = {
            "event": "transaction.updated",
            "timestamp": 1700000000,
            "data": datos,
            "signature": {
                "properties": propiedades,
                "checksum": hashlib.sha256(cadena.encode()).hexdigest(),
            },
        }
        r = client.post(f"{API}/webhook", json=evento)
        assert r.status_code == 200, r.text
    finally:
        settings.WOMPI_EVENT_SECRET = ""

    db_session.expire_all()
    pago = db_session.scalars(
        select(PagoSuscripcion).where(PagoSuscripcion.empresa_id == empresa.id)
    ).one()
    empresa_ahora = db_session.get(type(empresa), empresa.id)
    print("\n===== 4. EL WEBHOOK ACREDITA EL PAGO PSE =====")
    print(f"  estado del pago: {pago.estado_transaccion}")
    print(f"  pagada_hasta:    {antes} -> {empresa_ahora.pagada_hasta}")
    assert pago.estado_transaccion == "APPROVED"
    # Estaba vencida, así que el mes cuenta desde HOY (no se cobran días perdidos)
    assert empresa_ahora.pagada_hasta > date.today()
    assert empresa_ahora.pagada_hasta > antes


# ---------------------------------------------------------------------------
# 5. Bordes
# ---------------------------------------------------------------------------
def test_no_se_arranca_un_segundo_pse_con_uno_en_curso(client, db_session, base_datos, wompi):
    """El candado de PENDING evita el doble débito: uno solo a la vez.

    La respuesta NO trae la URL del banco a propósito. Se probó devolverla aquí
    y no la leía nadie: quien retoma el pago lo hace desde el aviso de la
    pantalla, que sale de la lista de pagos. Un campo que ningún cliente usa
    solo sirve para que alguien crea que sí.
    """
    empresa = preparar(client, db_session, base_datos)
    h = auth_headers(client, "admin.a")
    primero = client.post(f"{API}/pse/pagar", json={"banco": "1022", "documento": "1094111"}, headers=h)
    assert primero.status_code == 200
    segundo = client.post(f"{API}/pse/pagar", json={"banco": "1040", "documento": "1094222"}, headers=h)
    print("\n===== 5. SEGUNDO PSE CON UNO EN CURSO =====")
    print(f"  {segundo.status_code} · {segundo.json()['error']['detail']}")
    assert segundo.status_code == 422
    assert segundo.json()["error"]["code"] == "pago_pendiente"

    # Y no se creó un segundo pago: sigue habiendo uno solo
    from sqlalchemy import func, select

    from app.modules.suscripcion.models import PagoSuscripcion

    cuantos = db_session.scalar(
        select(func.count())
        .select_from(PagoSuscripcion)
        .where(PagoSuscripcion.empresa_id == empresa.id)
    )
    print(f"  pagos en la base: {cuantos}")
    assert cuantos == 1

    # Lo que SÍ tiene que poder hacer la persona: retomarlo desde la pantalla.
    # La URL viaja en la lista de pagos, que es de donde la saca el aviso.
    lista = client.get(f"{API}/pagos", headers=h).json()["items"]
    print(f"  url en el historial: {lista[0]['url_banco']}")
    assert lista[0]["url_banco"] == "https://banco.example/pagar/abc"


def test_si_wompi_falla_no_queda_el_candado_puesto(client, db_session, base_datos, wompi):
    """Si la transacción no se crea, el pago PENDING no puede quedarse trabado: la
    empresa no podría volver a intentar hasta que expirara a las 24 horas."""
    from app.core.exceptions import BusinessError
    from app.modules.suscripcion.models import PagoSuscripcion
    from sqlalchemy import select

    empresa = preparar(client, db_session, base_datos)
    wompi.revienta = BusinessError("La pasarela rechazó la operación", code="wompi_error")
    h = auth_headers(client, "admin.a")
    r = client.post(f"{API}/pse/pagar", json={"banco": "1022", "documento": "1094111"}, headers=h)
    print("\n===== 6. WOMPI FALLA AL CREAR =====")
    print(f"  respuesta: {r.status_code}")
    assert r.status_code == 422

    db_session.expire_all()
    pago = db_session.scalars(
        select(PagoSuscripcion).where(PagoSuscripcion.empresa_id == empresa.id)
    ).one()
    print(f"  el pago quedó en: {pago.estado_transaccion}")
    assert pago.estado_transaccion == "ERROR", (
        "el pago quedó PENDING sin nada detrás: la empresa no puede reintentar"
    )

    # Y se puede volver a intentar de una
    wompi.revienta = None
    otra = client.post(f"{API}/pse/pagar", json={"banco": "1022", "documento": "1094111"}, headers=h)
    print(f"  reintento: {otra.status_code}")
    assert otra.status_code == 200, otra.text


def test_la_empresa_exenta_no_paga_por_pse(client, db_session, base_datos, wompi):
    empresa = preparar(client, db_session, base_datos)
    empresa.exenta = True
    db_session.commit()
    h = auth_headers(client, "admin.a")
    r = client.post(f"{API}/pse/pagar", json={"banco": "1022", "documento": "1094111"}, headers=h)
    print("\n===== 7. EMPRESA EXENTA =====")
    print(f"  {r.status_code} · {r.json()['error']['code']}")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "empresa_exenta"


def test_sin_correo_en_la_empresa_se_usa_el_de_quien_paga(client, db_session, base_datos, wompi):
    """Wompi exige un correo para PSE. Si la empresa no lo tiene, se usa el de
    quien está pagando en vez de fallar: la empresa recién creada normalmente no
    lo tiene cargado y no tiene sentido bloquearle el pago por eso.

    Ojo con el alcance: el caso de quedarse SIN ningún correo no es alcanzable
    por la API porque usuarios.correo es NOT NULL, así que el respaldo siempre
    existe. La guarda de `sin_correo` se deja igual porque es barata y protege el
    día en que esa columna deje de ser obligatoria, pero no se puede probar por
    aquí: se prueba llamando al helper (ver abajo).
    """
    empresa = preparar(client, db_session, base_datos)
    empresa.correo = None
    db_session.commit()

    h = auth_headers(client, "admin.a")
    r = client.post(
        f"{API}/pse/pagar", json={"banco": "1022", "documento": "1094111"}, headers=h
    )
    assert r.status_code == 200, r.text
    _, enviado = wompi.llamadas[-1]
    print("\n===== 8. SIN CORREO EN LA EMPRESA =====")
    print(f"  se usó el de quien paga: {enviado['customer_email']}")
    assert "@" in enviado["customer_email"]


def test_sin_ningun_correo_el_helper_lo_dice_claro(db_session, base_datos):
    """El respaldo del respaldo, probado donde sí se puede: sin correo en la
    empresa Y sin usuario en el contexto, se explica qué falta en vez de dejar
    que Wompi rechace con un mensaje que no dice nada."""
    from app.core.context import RequestContext
    from app.core.exceptions import BusinessError
    from app.modules.suscripcion.service import SuscripcionService

    empresa = base_datos["empresa_a"]
    empresa.correo = None
    db_session.commit()
    ctx = RequestContext(empresa_id=empresa.id, user_id=None)
    servicio = SuscripcionService(db_session, ctx)
    print("\n===== 8b. SIN NINGÚN CORREO =====")
    with pytest.raises(BusinessError) as exc:
        servicio._correo_de_cobro(empresa)
    print(f"  {exc.value.code}: {exc.value.detail}")
    assert exc.value.code == "sin_correo"


def test_el_pago_por_pse_exige_permiso(client, db_session, base_datos, wompi):
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.modules.usuarios.models import Rol, Usuario
    from tests.conftest import PASSWORD

    preparar(client, db_session, base_datos)
    rol = db_session.scalars(select(Rol).where(Rol.nombre == "Consulta")).one()
    mirona = Usuario(
        nombre="Solo", apellido="Mira", correo="mira.pse@test.local", username="mira.pse",
        hashed_password=hash_password(PASSWORD), empresa_id=base_datos["empresa_a"].id,
    )
    mirona.roles = [rol]
    db_session.add(mirona)
    db_session.commit()

    h = auth_headers(client, "mira.pse")
    bancos = client.get(f"{API}/pse/bancos", headers=h)
    pagar = client.post(f"{API}/pse/pagar", json={"banco": "1022", "documento": "1094111"}, headers=h)
    print("\n===== 9. PERMISOS =====")
    print(f"  con 'consultar': ver bancos={bancos.status_code} pagar={pagar.status_code}")
    assert bancos.status_code == 200, bancos.text
    assert pagar.status_code == 403, pagar.text
