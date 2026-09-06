"""AUDITORÍA DEL FLETE — sondas 35 y 36: la leche anotada TARDE sobre un viaje
fijo cuyo comprobante todavía NO ha movido plata (borrador y aprobado), y el
flujo de corrección de siempre: anular y volver a generar.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_zzaudit_flete_transportador import (
    D,
    DIA_1,
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
from tests.test_zzaudit_flete_cuatro import transporte_de_la_grilla

import pytest


@pytest.mark.parametrize("estado", ["borrador", "aprobado"])
def test_zzaudit_leche_tarde_sin_plata_salida_y_anular(
    client, base_datos, db_session, estado
):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h)
    a = recibir(client, h, esc, DIA_1, "Aurelio", "44.23")
    liq_a = generar(client, h)["generadas"][0]["id"]
    if estado == "aprobado":
        _post(client, h, f"{LIQ}/{liq_a}/aprobar", {})
    pinta(leer(client, h, liq_a), f"S35 comprobante en {estado}")

    tarde = recibir(client, h, esc, DIA_1, "Marleny", "137.45")
    print(f"    leche anotada tarde (137,45 L): foto ${D(tarde['valor_transporte'])}")
    print(f"    la grilla dice ${transporte_de_la_grilla(client, h)} de flete "
          f"(el viaje vale ${FIJO})")
    gen = generar(client, h)
    print(f"    Generar: generadas={len(gen['generadas'])} "
          f"omitidas={[o['motivo_codigo'] for o in gen.get('omitidas', [])]}")

    # EL FLUJO DE CORRECCION DE SIEMPRE: anular y volver a generar
    r = client.post(f"{LIQ}/{liq_a}/anular", headers=h)
    print(f"    anular -> {r.status_code}")
    assert r.status_code == 200, r.text
    print(f"    tras anular: fotos ${foto(db_session, a['id'])} + "
          f"${foto(db_session, tarde['id'])}")
    gen = generar(client, h)
    assert gen["generadas"], gen
    liq = leer(client, h, gen["generadas"][0]["id"])
    pinta(liq, "S35 rehecho")
    _, fot = fotos_del_comprobante(db_session, liq["id"])
    t = cuadre(liq, fot, "S35")
    cuadre_por_renglon(db_session, liq, "S35")
    f_a, f_m = foto(db_session, a["id"]), foto(db_session, tarde["id"])
    grilla = transporte_de_la_grilla(client, h)
    cobrado = cobrado_del_viaje(todas(client, h), DIA_1, "A fabrica")
    print(f"    viaje cobrado ${cobrado}; fotos ${f_a} + ${f_m} = ${f_a + f_m}; "
          f"grilla ${grilla}")
    assert t == FIJO
    assert cobrado == FIJO
    assert f_a + f_m == FIJO
    assert grilla == FIJO
