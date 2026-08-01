"""Un defecto por prueba, de los que encontró la revisión adversarial del PSE.

Casi todos son de DINERO y casi ninguno se ve mirando el código de PSE solo: se
ven cuando se pregunta qué pasa si el webhook se pierde, si dos conexiones
llegan a la vez, o si alguien reenvía un evento legítimo con un campo cambiado.

La lección que se repite —y ya iba una vez, en el lote anterior— es que aquí el
reloj propio no manda: manda la pasarela. Todo lo que este archivo protege es
alguna variante de "no decidas sin preguntarle a quien sabe".
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.suscripcion.models import PagoSuscripcion
from tests.conftest import auth_headers

API = "/api/v1/suscripcion"


def D(v):
    return Decimal(str(v))


class WompiMudo:
    """No contesta nada: simula la pasarela caída o inalcanzable."""

    def __init__(self):
        pass

    def consultar_transaccion(self, transaction_id):
        from app.core.exceptions import BusinessError

        raise BusinessError("La pasarela no responde", code="wompi_error")


class WompiQueAprueba:
    """La transacción que nosotros creímos perdida, allá está APROBADA."""

    consultas: list = []

    def __init__(self):
        pass

    def consultar_transaccion(self, transaction_id):
        WompiQueAprueba.consultas.append(transaction_id)
        return {
            "id": transaction_id,
            "status": "APPROVED",
            "payment_method": {"type": "PSE", "extra": {}},
        }


def empresa_con_pse_pendiente(db_session, base_datos, *, edad_horas, con_tx=True):
    """Empresa vencida con un pago PSE pendiente de la edad que se pida."""
    empresa = base_datos["empresa_a"]
    empresa.tarifa_mensual = D(80000)
    empresa.pagada_hasta = date.today() - timedelta(days=3)
    db_session.flush()
    pago = PagoSuscripcion(
        empresa_id=empresa.id,
        metodo="PSE",
        referencia=f"susc-{empresa.id.hex}-viejo",
        monto=D(80000),
        moneda="COP",
        estado_transaccion="PENDING",
        origen="manual",
        wompi_transaction_id="tx-perdida" if con_tx else None,
        url_banco="https://banco/x",
    )
    db_session.add(pago)
    db_session.flush()
    # created_at lo pone la base; se retrasa a mano para envejecer el pago
    pago.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=edad_horas
    )
    db_session.commit()
    return empresa, pago


# ---------------------------------------------------------------------------
# 1. CRÍTICO: un PSE viejo NO se da por muerto sin preguntarle a Wompi
# ---------------------------------------------------------------------------
def test_un_pse_viejo_se_resuelve_preguntando_no_matandolo(
    client, db_session, base_datos, monkeypatch
):
    """El defecto: con más de 24 horas se marcaba ERROR sin consultar la
    pasarela. Con PSE eso es plata debitada y mes no acreditado, porque el
    pendiente largo es lo normal y el webhook se puede haber perdido."""
    import app.modules.suscripcion.service as servicio

    WompiQueAprueba.consultas = []
    monkeypatch.setattr(servicio, "WompiClient", WompiQueAprueba)
    empresa, pago = empresa_con_pse_pendiente(db_session, base_datos, edad_horas=63)
    antes = empresa.pagada_hasta

    r = client.get(API, headers=auth_headers(client, "admin.a"))
    assert r.status_code == 200, r.text

    db_session.expire_all()
    pago = db_session.get(PagoSuscripcion, pago.id)
    empresa = db_session.get(type(empresa), empresa.id)
    print("\n===== 1. PSE DE 63 HORAS =====")
    print(f"  se le preguntó a Wompi: {WompiQueAprueba.consultas}")
    print(f"  estado del pago:        {pago.estado_transaccion}")
    print(f"  pagada_hasta:           {antes} -> {empresa.pagada_hasta}")
    assert WompiQueAprueba.consultas == ["tx-perdida"]
    assert pago.estado_transaccion == "APPROVED"
    assert empresa.pagada_hasta > date.today()


def test_con_la_pasarela_muda_el_pago_sigue_pendiente(
    client, db_session, base_datos, monkeypatch
):
    """Y si Wompi no contesta, tampoco se mata: se deja pendiente y se reintenta.
    Matarlo libera el candado del PENDING y habilita pagar dos veces el mes."""
    import app.modules.suscripcion.service as servicio

    monkeypatch.setattr(servicio, "WompiClient", WompiMudo)
    empresa, pago = empresa_con_pse_pendiente(db_session, base_datos, edad_horas=63)

    r = client.get(API, headers=auth_headers(client, "admin.a"))
    assert r.status_code == 200

    db_session.expire_all()
    pago = db_session.get(PagoSuscripcion, pago.id)
    print("\n===== 2. PASARELA MUDA =====")
    print(f"  estado: {pago.estado_transaccion} (debe seguir PENDING)")
    assert pago.estado_transaccion == "PENDING"


def test_sin_transaccion_en_la_pasarela_si_expira(client, db_session, base_datos, monkeypatch):
    """La otra cara: un pendiente que nunca llegó a crearse allá SÍ hay que
    cerrarlo, o el candado deja a la empresa sin poder pagar nunca más."""
    import app.modules.suscripcion.service as servicio

    monkeypatch.setattr(servicio, "WompiClient", WompiMudo)
    empresa, pago = empresa_con_pse_pendiente(
        db_session, base_datos, edad_horas=30, con_tx=False
    )

    client.get(API, headers=auth_headers(client, "admin.a"))
    db_session.expire_all()
    pago = db_session.get(PagoSuscripcion, pago.id)
    print("\n===== 3. SIN TRANSACCIÓN EN LA PASARELA =====")
    print(f"  estado: {pago.estado_transaccion} · {pago.detalle}")
    assert pago.estado_transaccion == "ERROR"


# ---------------------------------------------------------------------------
# 2. ALTO: el webhook resuelve por el id FIRMADO, no por la referencia
# ---------------------------------------------------------------------------
def evento_firmado(datos_tx, secreto="secreto-eventos", timestamp=1700000000):
    import hashlib

    props = ["transaction.id", "transaction.status", "transaction.amount_in_cents"]
    cadena = (
        "".join(str(datos_tx[p.split(".")[1]]) for p in props) + str(timestamp) + secreto
    )
    return {
        "event": "transaction.updated",
        "timestamp": timestamp,
        "data": {"transaction": datos_tx},
        "signature": {
            "properties": props,
            "checksum": hashlib.sha256(cadena.encode()).hexdigest(),
        },
    }


def test_un_evento_legitimo_con_la_referencia_cambiada_no_acredita_a_otro(
    client, db_session, base_datos
):
    """EL DEFECTO GORDO DE SEGURIDAD. El checksum solo firma id, estado e
    importe: la `reference` viaja SIN firmar. Quien tuviera un evento legítimo
    de aprobación podía cambiarle la referencia por la de otra empresa y
    reenviarlo — checksum válido, mes regalado."""
    from app.core.config import settings

    empresa_a = base_datos["empresa_a"]
    empresa_b = base_datos["empresa_b"]
    for e in (empresa_a, empresa_b):
        e.tarifa_mensual = D(80000)
        e.pagada_hasta = date.today() - timedelta(days=3)

    # A tiene una transacción de verdad; B tiene su propio pendiente
    pago_a = PagoSuscripcion(
        empresa_id=empresa_a.id, metodo="PSE", referencia="ref-de-A", monto=D(80000),
        moneda="COP", estado_transaccion="PENDING", origen="manual",
        wompi_transaction_id="tx-de-A",
    )
    pago_b = PagoSuscripcion(
        empresa_id=empresa_b.id, metodo="PSE", referencia="ref-de-B", monto=D(80000),
        moneda="COP", estado_transaccion="PENDING", origen="manual",
        wompi_transaction_id="tx-de-B",
    )
    db_session.add_all([pago_a, pago_b])
    db_session.commit()
    hasta_b = empresa_b.pagada_hasta

    settings.WOMPI_EVENT_SECRET = "secreto-eventos"
    try:
        # El evento REAL de A, con la referencia cambiada por la de B.
        # Los tres campos firmados se dejan intactos: el checksum sigue válido.
        evento = evento_firmado(
            {
                "id": "tx-de-A",
                "status": "APPROVED",
                "amount_in_cents": 8_000_000,
                "reference": "ref-de-B",  # <-- lo único manipulado
            }
        )
        r = client.post(f"{API}/webhook", json=evento)
        assert r.status_code == 200, r.text
    finally:
        settings.WOMPI_EVENT_SECRET = ""

    db_session.expire_all()
    pago_a = db_session.get(PagoSuscripcion, pago_a.id)
    pago_b = db_session.get(PagoSuscripcion, pago_b.id)
    empresa_b = db_session.get(type(empresa_b), empresa_b.id)
    print("\n===== 4. REFERENCIA CAMBIADA =====")
    print(f"  pago de A: {pago_a.estado_transaccion}  (el id firmado era el suyo)")
    print(f"  pago de B: {pago_b.estado_transaccion}  (no le tocaba nada)")
    print(f"  vigencia de B: {hasta_b} -> {empresa_b.pagada_hasta}")
    # El evento acredita a quien dice el ID FIRMADO, no la referencia
    assert pago_a.estado_transaccion == "APPROVED"
    assert pago_b.estado_transaccion == "PENDING"
    assert empresa_b.pagada_hasta == hasta_b


def test_un_evento_con_importe_que_no_cuadra_se_descarta(client, db_session, base_datos):
    """El importe también va firmado. Si no es el del pago, el evento no habla
    de este pago por mucho que el id coincida."""
    from app.core.config import settings

    empresa = base_datos["empresa_a"]
    empresa.tarifa_mensual = D(80000)
    empresa.pagada_hasta = date.today() - timedelta(days=3)
    pago = PagoSuscripcion(
        empresa_id=empresa.id, metodo="PSE", referencia="ref-x", monto=D(80000),
        moneda="COP", estado_transaccion="PENDING", origen="manual",
        wompi_transaction_id="tx-x",
    )
    db_session.add(pago)
    db_session.commit()

    settings.WOMPI_EVENT_SECRET = "secreto-eventos"
    try:
        r = client.post(
            f"{API}/webhook",
            json=evento_firmado(
                {"id": "tx-x", "status": "APPROVED",
                 "amount_in_cents": 100, "reference": "ref-x"},
            ),
        )
        assert r.status_code == 200
        print("\n===== 5. IMPORTE QUE NO CUADRA =====")
        print(f"  respuesta del webhook: {r.json()['detail']}")
        assert r.json()["detail"] == "desconocida"
    finally:
        settings.WOMPI_EVENT_SECRET = ""

    db_session.expire_all()
    assert db_session.get(PagoSuscripcion, pago.id).estado_transaccion == "PENDING"


# ---------------------------------------------------------------------------
# 3. MEDIO: un PSE fallido no puede apagar el cobro automático de la tarjeta
# ---------------------------------------------------------------------------
def test_un_pse_rechazado_no_frena_el_cobro_con_tarjeta(client, db_session, base_datos):
    """El cooldown existe para no machacar una tarjeta que el emisor acaba de
    rechazar. Un PSE abandonado en el portal del banco no dice nada sobre la
    tarjeta, y contarlo dejaba a la empresa bloqueada teniendo con qué pagar."""
    from app.core.context import RequestContext
    from app.modules.suscripcion.service import SuscripcionService

    empresa = base_datos["empresa_a"]
    db_session.add(
        PagoSuscripcion(
            empresa_id=empresa.id,
            metodo="PSE",
            referencia="pse-abandonado",
            monto=D(80000),
            moneda="COP",
            estado_transaccion="DECLINED",
            origen="manual",
        )
    )
    db_session.commit()

    servicio = SuscripcionService(db_session, RequestContext(empresa_id=empresa.id))
    print("\n===== 6. PSE RECHAZADO Y LA TARJETA =====")
    print(f"  ¿la tarjeta queda en cooldown? {servicio._en_cooldown(empresa.id)}")
    assert servicio._en_cooldown(empresa.id) is False

    # Con una TARJETA rechazada sí, que para eso está
    db_session.add(
        PagoSuscripcion(
            empresa_id=empresa.id, metodo="CARD", referencia="tarjeta-rechazada",
            monto=D(80000), moneda="COP", estado_transaccion="DECLINED", origen="cron",
        )
    )
    db_session.commit()
    print(f"  con una TARJETA rechazada:     {servicio._en_cooldown(empresa.id)}")
    assert servicio._en_cooldown(empresa.id) is True


# ---------------------------------------------------------------------------
# 4. ALTO: el mismo evento dos veces no acredita dos meses
# ---------------------------------------------------------------------------
def test_el_mismo_evento_repetido_no_regala_un_segundo_mes(client, db_session, base_datos):
    """Wompi reintenta el webhook. La idempotencia se apoya en releer el pago ya
    bloqueado; si se leyera el objeto rancio de la sesión, la segunda entrega
    volvería a extender la vigencia."""
    from app.core.config import settings

    empresa = base_datos["empresa_a"]
    empresa.tarifa_mensual = D(80000)
    empresa.pagada_hasta = date.today() - timedelta(days=3)
    pago = PagoSuscripcion(
        empresa_id=empresa.id, metodo="PSE", referencia="ref-rep", monto=D(80000),
        moneda="COP", estado_transaccion="PENDING", origen="manual",
        wompi_transaction_id="tx-rep",
    )
    db_session.add(pago)
    db_session.commit()

    evento = evento_firmado(
        {"id": "tx-rep", "status": "APPROVED",
         "amount_in_cents": 8_000_000, "reference": "ref-rep"}
    )
    settings.WOMPI_EVENT_SECRET = "secreto-eventos"
    try:
        r1 = client.post(f"{API}/webhook", json=evento)
        db_session.expire_all()
        tras_uno = db_session.get(type(empresa), empresa.id).pagada_hasta
        r2 = client.post(f"{API}/webhook", json=evento)
    finally:
        settings.WOMPI_EVENT_SECRET = ""

    db_session.expire_all()
    tras_dos = db_session.get(type(empresa), empresa.id).pagada_hasta
    print("\n===== 7. EVENTO REPETIDO =====")
    print(f"  primera entrega:  {r1.json()['detail']} -> {tras_uno}")
    print(f"  segunda entrega:  {r2.json()['detail']} -> {tras_dos}")
    assert r1.json()["detail"] == "aplicado"
    assert r2.json()["detail"] == "repetido"
    assert tras_uno == tras_dos


# ---------------------------------------------------------------------------
# 5. ALTO: el cron relee cada empresa antes de decidir
# ---------------------------------------------------------------------------
def test_el_cron_no_cobra_a_quien_se_puso_al_dia_durante_el_barrido(
    client, db_session, base_datos, monkeypatch
):
    """El barrido tarda —una llamada a la pasarela por empresa— y decidía con la
    foto del principio. Si el PSE de una empresa se acreditaba a mitad de
    camino, igual se le debitaba la tarjeta por un mes ya pagado."""
    from app.core.config import settings
    from app.modules.suscripcion.models import FuentePagoSuscripcion

    # DOS empresas vencidas y con tarjeta. A se pondrá al día a mitad del
    # barrido; B no. Hace falta B o la prueba pasaría igual con el cron roto
    # del todo: sin nadie a quien cobrar, "no cobró de más" no dice nada.
    empresa = base_datos["empresa_a"]
    otra = base_datos["empresa_b"]
    for i, e in enumerate((empresa, otra)):
        e.tarifa_mensual = D(80000)
        e.pagada_hasta = date.today() - timedelta(days=3)
        db_session.add(
            FuentePagoSuscripcion(
                empresa_id=e.id, wompi_payment_source_id=101 + i,
                customer_email="x@y.co", marca="VISA", ultimos4="4242",
            )
        )
    db_session.commit()

    cobros = []

    class WompiEspia:
        def __init__(self):
            pass

        def crear_transaccion(self, **kwargs):
            cobros.append(kwargs.get("referencia"))
            return {"id": "tx-cron", "status": "APPROVED"}

        def consultar_transaccion(self, transaction_id):
            return {"id": transaction_id, "status": "PENDING"}

    import app.modules.suscripcion.service as servicio

    monkeypatch.setattr(servicio, "WompiClient", WompiEspia)

    # Alguien (el webhook del PSE) pone la empresa al día por OTRA conexión.
    # Se simula escribiendo la fila directo y dejando el objeto viejo en sesión.
    from sqlalchemy import update

    from app.modules.empresas.models import Empresa

    db_session.execute(
        update(Empresa)
        .where(Empresa.id == empresa.id)
        .values(pagada_hasta=date.today() + timedelta(days=27))
    )
    db_session.commit()

    settings.SUSCRIPCION_CRON_SECRET = "secreto-cron"
    try:
        r = client.post(f"{API}/cobrar-vencidas", headers={"X-Cron-Secret": "secreto-cron"})
    finally:
        settings.SUSCRIPCION_CRON_SECRET = ""

    print("\n===== 8. EL CRON RELEE =====")
    print(f"  contadores: {r.json()}")
    print(f"  a quién se le cobró: {cobros}")
    assert r.status_code == 200
    # A la que se puso al día, NO. A la otra, SÍ: el barrido sigue funcionando.
    assert not any(empresa.id.hex in ref for ref in cobros), (
        "se le cobró a una empresa que ya estaba al día"
    )
    assert any(otra.id.hex in ref for ref in cobros), (
        "el barrido no le cobró a la que sí estaba vencida"
    )


# ---------------------------------------------------------------------------
# 6. Higiene: la URL del banco solo se acepta si es http(s)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url,vale",
    [
        ("https://banco.co/pagar", True),
        ("http://banco.co/pagar", True),
        ("javascript:alert(1)", False),
        ("  ", False),
        (None, False),
    ],
)
def test_la_url_del_banco_solo_puede_ser_un_enlace(url, vale):
    """Esa URL termina en un href del navegador. Hoy viene de Wompi y es de
    fiar; el filtro cuesta una línea y no depende de que siga siéndolo."""
    from app.modules.suscripcion.wompi import url_del_banco

    datos = {"payment_method": {"type": "PSE", "extra": {"async_payment_url": url}}}
    resultado = url_del_banco(datos)
    assert (resultado is not None) == vale
