"""AUDITORÍA DEL FLETE — sonda 34: ¿hay salida? Qué tiene que hacer el dueño
para volver a cobrar un viaje fijo que se quedó sin cobrar.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_zzaudit_flete_transportador import (
    D,
    DIA_1,
    DIA_2,
    FIJO,
    LIQ,
    cuadre,
    cuadre_por_renglon,
    escenario,
    fotos_del_comprobante,
    foto,
    generar,
    leer,
    pinta,
    recibir,
    _post,
)
from tests.test_zzaudit_flete_dos import cobrado_del_viaje, todas


def test_zzaudit_la_salida_del_viaje_perdido(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_a = generar(client, h)["generadas"][0]["id"]
    _post(client, h, f"{LIQ}/{liq_a}/aprobar", {})
    _post(client, h, f"{LIQ}/{liq_a}/pagos",
          {"fecha": DIA_1, "valor": "50000.00", "destinatario": "Alex"})
    tarde = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    recibir(client, h, esc, DIA_2, "Ramiro", "96.31", ruta_id=esc["napoles"]["id"])
    liq_b = generar(client, h)["generadas"][0]["id"]

    pagos = leer(client, h, liq_a)["pagos"]
    client.delete(f"{LIQ}/{liq_a}/pagos/{pagos[0]['id']}", headers=h)
    client.post(f"{LIQ}/{liq_a}/anular", headers=h)

    print("\n  == el viaje quedo sin cobrar ==")
    liqs = todas(client, h)
    print(f"    cobrado del viaje: ${cobrado_del_viaje(liqs, DIA_1, 'A fabrica')}")
    gen = generar(client, h)
    print(f"    Generar: {[o['motivo_codigo'] for o in gen.get('omitidas', [])]}")

    print("  == el dueno hace caso al aviso y ANULA TAMBIEN el otro comprobante ==")
    r = client.post(f"{LIQ}/{liq_b}/anular", headers=h)
    print(f"    anular B -> {r.status_code}")
    gen = generar(client, h)
    print(f"    Generar: generadas={len(gen['generadas'])} "
          f"omitidas={[o['motivo_codigo'] for o in gen.get('omitidas', [])]}")
    assert gen["generadas"], gen
    liq = leer(client, h, gen["generadas"][0]["id"])
    pinta(liq, "S34 el comprobante rehecho")
    _, fot = fotos_del_comprobante(db_session, liq["id"])
    t = cuadre(liq, fot, "S34")
    cuadre_por_renglon(db_session, liq, "S34")
    liqs = todas(client, h)
    cobrado = cobrado_del_viaje(liqs, DIA_1, "A fabrica")
    f_a, f_m = foto(db_session, a["id"]), foto(db_session, tarde["id"])
    print(f"    viaje cobrado ${cobrado}; fotos ${f_a} + ${f_m} = ${f_a + f_m}")
    assert cobrado == FIJO
    assert f_a + f_m == FIJO
