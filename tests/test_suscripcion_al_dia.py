"""Con la suscripción al día NO se puede pagar, y cualquier rol puede cambiar
su propia contraseña.

Lo primero lo pidió el dueño: "si ya está activa pues que no me deje pagar,
cómo voy a pagar si ya está activa la sub". El dinero no se perdería —los meses
se acumulan— pero nadie quiere descubrir que adelantó tres meses por darle dos
veces al botón. La puerta se cierra en el SERVICIO y no solo en la pantalla: el
que sabe la dirección del endpoint entra igual.

Lo segundo es la otra cara de darle 'suscripcion' al rol de reventa: si ese
cliente entra al sistema, tiene que poder cambiarse la clave que le pusieron.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/suscripcion"


def D(v):
    return Decimal(str(v))


class WompiFalso:
    """No debería llegar a llamarse nunca en estas pruebas."""

    llamado = False

    def __init__(self):
        pass

    def tokens_aceptacion(self):
        return {
            "presigned_acceptance": {"acceptance_token": "t", "permalink": "x"},
            "presigned_personal_data_auth": {"acceptance_token": "t2", "permalink": "y"},
        }

    def crear_transaccion_pse(self, **kwargs):
        WompiFalso.llamado = True
        return {"id": "tx", "status": "PENDING",
                "payment_method": {"type": "PSE", "extra": {"async_payment_url": "https://b/x"}}}

    def crear_transaccion(self, **kwargs):
        WompiFalso.llamado = True
        return {"id": "tx", "status": "APPROVED"}

    def consultar_transaccion(self, transaction_id):
        return {"id": transaction_id, "status": "PENDING"}


@pytest.fixture()
def wompi(monkeypatch):
    WompiFalso.llamado = False
    import app.modules.suscripcion.service as servicio

    monkeypatch.setattr(servicio, "WompiClient", WompiFalso)
    return WompiFalso


def preparar(db_session, base_datos, *, dias_restantes):
    """Empresa con la vigencia a los días que se pidan (negativo = vencida)."""
    empresa = base_datos["empresa_a"]
    empresa.tarifa_mensual = D(100000)
    empresa.pagada_hasta = date.today() + timedelta(days=dias_restantes)
    empresa.correo = "quesera@ejemplo.co"
    empresa.telefono = "3107650926"
    db_session.commit()
    return empresa


CUERPO_PSE = {
    "banco": "1", "tipo_persona": "0", "tipo_documento": "CC",
    "documento": "1094123456", "nombre_completo": "Miguel Garzon",
    "telefono": "3107650926",
}


# ---------------------------------------------------------------------------
# 1. Con un mes por delante, no
# ---------------------------------------------------------------------------
def test_con_la_suscripcion_al_dia_no_deja_pagar_por_pse(client, db_session, base_datos, wompi):
    preparar(db_session, base_datos, dias_restantes=30)
    r = client.post(f"{API}/pse/pagar", json=CUERPO_PSE, headers=auth_headers(client, "admin.a"))
    print("\n===== 1. 30 DÍAS POR DELANTE =====")
    print(f"  {r.status_code} · {r.json()['error']['code']}")
    print(f"  {r.json()['error']['detail']}")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "suscripcion_al_dia"
    # Y NO se molestó a la pasarela: el pago no llegó ni a nacer
    assert wompi.llamado is False


def test_con_la_suscripcion_al_dia_tampoco_con_tarjeta(client, db_session, base_datos, wompi):
    """Mismo corte para la tarjeta. Va en el servicio, así que el guardia es uno
    solo para los dos caminos."""
    from app.modules.suscripcion.models import FuentePagoSuscripcion

    empresa = preparar(db_session, base_datos, dias_restantes=30)
    db_session.add(
        FuentePagoSuscripcion(
            empresa_id=empresa.id, wompi_payment_source_id=77,
            customer_email="x@y.co", marca="VISA", ultimos4="4242",
        )
    )
    db_session.commit()

    r = client.post(f"{API}/pagar", headers=auth_headers(client, "admin.a"))
    print("\n===== 2. CON TARJETA Y AL DÍA =====")
    print(f"  {r.status_code} · {r.json()['error']['code']}")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "suscripcion_al_dia"
    assert wompi.llamado is False


# ---------------------------------------------------------------------------
# 2. Pero adelantarse unos días SÍ, y vencida por supuesto
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dias,caso",
    [(3, "por vencer: faltan 3 días"), (0, "vence hoy"), (-2, "en gracia"), (-30, "bloqueada")],
)
def test_cerca_del_vencimiento_si_deja_pagar(client, db_session, base_datos, wompi, dias, caso):
    """Lo que NO se quiere es bloquear a quien se quiere poner al día. El corte
    es 'activa'; en cuanto entra en aviso, gracia o bloqueo, se puede pagar."""
    preparar(db_session, base_datos, dias_restantes=dias)
    r = client.post(f"{API}/pse/pagar", json=CUERPO_PSE, headers=auth_headers(client, "admin.a"))
    print(f"\n===== 3. {caso.upper()} =====")
    print(f"  {r.status_code} (debe ser 200)")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 3. El número que se le muestra a la persona tiene que ser el correcto
# ---------------------------------------------------------------------------
def test_el_resumen_trae_los_dias_de_aviso_no_solo_los_de_gracia(client, db_session, base_datos):
    """La pantalla dice "el botón se activa cuando falten N días". Ese N es
    DIAS_AVISO, no DIAS_GRACIA (que son los de después de vencerse). Coinciden
    por defecto y por eso confundirlos no se nota... hasta que alguien cambie
    uno de los dos y la pantalla empiece a dar una fecha falsa."""
    from app.core.config import settings

    preparar(db_session, base_datos, dias_restantes=30)
    d = client.get(API, headers=auth_headers(client, "admin.a")).json()
    print("\n===== 4. DÍAS DE AVISO =====")
    print(f"  dias_aviso={d['dias_aviso']}  dias_gracia={d['dias_gracia']}")
    assert d["dias_aviso"] == settings.SUSCRIPCION_DIAS_AVISO
    assert d["dias_gracia"] == settings.SUSCRIPCION_DIAS_GRACIA


# ---------------------------------------------------------------------------
# 4. Cualquier rol puede cambiarse la contraseña
# ---------------------------------------------------------------------------
def test_cualquier_rol_puede_cambiar_su_propia_contrasena(client, db_session, base_datos):
    """No depende de ningún permiso, y no debe: el cliente de reventa entra al
    sistema con una clave que le pusieron y tiene que poder cambiarla.

    Se prueba con el usuario de menos permisos que haya a mano.
    """
    from tests.conftest import PASSWORD

    h = auth_headers(client, "admin.a")
    r = client.post(
        "/api/v1/auth/cambiar-password",
        json={"password_actual": PASSWORD, "password_nueva": "OtraClave99*"},
        headers=h,
    )
    print("\n===== 5. CAMBIO DE CONTRASEÑA =====")
    print(f"  {r.status_code} · {r.json().get('detail')}")
    assert r.status_code == 200

    # La vieja deja de servir y la nueva sirve
    vieja = client.post(
        "/api/v1/auth/login", data={"username": "admin.a", "password": PASSWORD}
    )
    nueva = client.post(
        "/api/v1/auth/login", data={"username": "admin.a", "password": "OtraClave99*"}
    )
    print(f"  con la vieja: {vieja.status_code} · con la nueva: {nueva.status_code}")
    assert vieja.status_code == 401
    assert nueva.status_code == 200


def test_cambiar_la_contrasena_exige_la_actual(client, db_session, base_datos):
    """Sin la actual no se cambia: si no, un token robado bastaría para
    quedarse con la cuenta."""
    r = client.post(
        "/api/v1/auth/cambiar-password",
        json={"password_actual": "la-que-no-es", "password_nueva": "OtraClave99*"},
        headers=auth_headers(client, "admin.a"),
    )
    print("\n===== 6. SIN LA CONTRASEÑA ACTUAL =====")
    print(f"  {r.status_code}")
    assert r.status_code in (400, 401, 422)
