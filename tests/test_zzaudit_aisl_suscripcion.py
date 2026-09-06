"""SUSCRIPCION: ¿la quesera B ve o mueve el cobro de la quesera A?"""
from tests.conftest import auth_headers

V = "/api/v1"


def test_suscripcion_entre_queseras(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    empresa_a = str(base_datos["empresa_a"].id)
    out = []

    def _r(tag, r):
        out.append((tag, r.status_code, r.text[:180]))

    _r("B ve SU suscripcion", client.get(f"{V}/suscripcion", headers=hb))
    _r("A ve SU suscripcion", client.get(f"{V}/suscripcion", headers=ha))
    _r("B lista pagos de suscripcion", client.get(f"{V}/suscripcion/pagos", headers=hb))
    _r("B ve la config de cobro", client.get(f"{V}/suscripcion/config", headers=hb))
    _r("B fuerza el recalculo de estado", client.post(f"{V}/suscripcion/actualizar-estado", json={}, headers=hb))
    _r("B dispara el cobro de TODAS las vencidas",
       client.post(f"{V}/suscripcion/cobrar-vencidas", json={}, headers=hb))
    _r("B pide la suscripcion con el header de la empresa A",
       client.get(f"{V}/suscripcion", headers={**hb, "X-Empresa-Id": empresa_a}))
    _r("B intenta pagar con el header de la empresa A",
       client.post(f"{V}/suscripcion/pagar", json={}, headers={**hb, "X-Empresa-Id": empresa_a}))

    print("\n===== SUSCRIPCION =====")
    for tag, code, body in out:
        print(f"   {code}  {tag}   {body}")
