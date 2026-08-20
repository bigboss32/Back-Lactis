"""ATAQUE, septima tanda: LA MEMORIA FANTASMA de la ruta de paso.

EL MONTAJE. Un comprobante emitido cobrando UNA sola ruta ("A fabrica", por litro
$242,76). Napoles NO aparece en ese papel.

  1. alguien le corrige la ruta al dia y lo manda a Napoles: el papel cobra Napoles por
     un momento y se le ESCRIBE una fila de memoria de Napoles con la tarifa de ese
     momento;
  2. se le devuelve la ruta al dia. La fila de memoria de Napoles QUEDA escrita, aunque
     ese papel ya no cobre ni un peso de Napoles;
  3. el dueño renegocia Napoles y la pasa a DIA FIJO $150.000 —legitimo—;
  4. y alguien vuelve a mandar el dia a Napoles.

LA REGLA ESCRITA dice que cambiarle la ruta a un dia es escoger otra tarifa A PROPOSITO
y que de una ruta que el papel no cobraba no hay nada que conservar: manda la tarifa de
HOY. Con la fila fantasma, manda la de la visita de antes.

    82,00 L x $242,76 (la fantasma) ..........  $ 19.906,32
    dia completo (la tarifa de HOY) ..........  $150.000,00
                                                -----------
                                                $130.093,68
"""
import pytest

from app.modules.liquidaciones import service as liq_service
from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    FIJO, LIQUIDACIONES, NAPOLES, TRANSPORTADORES, D, _escenario,
    _liquidar_flete, _recibir, centavos,
)
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, _ok, _put
from tests.test_transporte_memoria_ataque import (
    CERO, EL_FIJO, LITROS, POR_LITRO, RUTA, _de_la_ruta, _emitir, _mem_pinta,
    _memoria, _papel, _pinta, _tarifas,
)


def _napoles_a(client, h, esc, valor, modo):
    """Cambia SOLO la tarifa de Napoles; fabrica se queda por litro a $242,76."""
    _ok(client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": "litro"},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(valor),
             "modo_transporte": modo},
        ]},
        headers=h,
    ), "tarifa de napoles")


@pytest.mark.parametrize("sin_memoria", [False, True],
                         ids=["con_memoria_escrita", "control_sin_memoria_escrita"])
def test_u1_la_fila_fantasma_de_napoles_le_gana_a_la_tarifa_de_hoy(
    client, base_datos, db_session, monkeypatch, sin_memoria
):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _tarifas(client, h, esc, NAPOLES, "litro")
    dia = _recibir(client, h, esc, EL_DIA, "Aurelio", str(LITROS))
    liq_id = _liquidar_flete(client, h)["id"]
    print(f"\n===== U1 la fila fantasma (sin_memoria={sin_memoria}) =====")
    _pinta(_papel(client, h, liq_id), "1. emitido (solo fabrica, por litro)")
    _mem_pinta(db_session, liq_id, esc, "al emitir")

    # 1 y 2: la visita a Napoles y la vuelta
    _ok(_put(client, h, dia["id"], ruta_id=esc["napoles"]["id"]), "a napoles")
    _pinta(_papel(client, h, liq_id), "2. de visita en napoles")
    _mem_pinta(db_session, liq_id, esc, "de visita")
    _ok(_put(client, h, dia["id"], ruta_id=esc["fabrica"]["id"]), "de vuelta a fabrica")
    _pinta(_papel(client, h, liq_id), "3. de vuelta en fabrica")
    _mem_pinta(db_session, liq_id, esc, "de vuelta (queda la fantasma)")

    # 3: Napoles pasa a DIA FIJO $150.000
    _napoles_a(client, h, esc, FIJO, "dia_fijo")
    if sin_memoria:
        monkeypatch.setattr(
            liq_service, "_lo_que_el_comprobante_tiene_escrito", lambda liq: {})

    # 4: el dia vuelve a Napoles
    _ok(_put(client, h, dia["id"], ruta_id=esc["napoles"]["id"]), "a napoles otra vez")
    final = _papel(client, h, liq_id)
    _pinta(final, "4. en napoles con la tarifa de HOY en dia fijo $150.000")
    _mem_pinta(db_session, liq_id, esc, "al final")
    cobrado = _de_la_ruta(final, "Napoles")
    print(f"      Napoles cobro ${cobrado}   |   la tarifa de HOY diria ${EL_FIJO}"
          f"   |   la fantasma diria ${POR_LITRO}")
    assert cobrado == EL_FIJO, (
        f"la ruta se escogio A PROPOSITO y hoy vale ${EL_FIJO}, pero el papel cobro "
        f"${cobrado}: ${EL_FIJO - cobrado} de diferencia"
    )


@pytest.mark.parametrize("sin_memoria", [False, True],
                         ids=["con_memoria_escrita", "control_sin_memoria_escrita"])
def test_u2_al_reves_la_fantasma_dice_dia_fijo_y_hoy_es_por_litro(
    client, base_datos, db_session, monkeypatch, sin_memoria
):
    """La fantasma dice DIA FIJO $150.000 y hoy Napoles es por litro: el papel cobra
    $150.000 donde hoy vale $19.906,32."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _tarifas(client, h, esc, NAPOLES, "litro")
    _napoles_a(client, h, esc, FIJO, "dia_fijo")
    dia = _recibir(client, h, esc, EL_DIA, "Aurelio", str(LITROS))
    liq_id = _liquidar_flete(client, h)["id"]
    print(f"\n===== U2 la fantasma en dia fijo (sin_memoria={sin_memoria}) =====")
    _pinta(_papel(client, h, liq_id), "1. emitido (solo fabrica, por litro)")
    _ok(_put(client, h, dia["id"], ruta_id=esc["napoles"]["id"]), "a napoles")
    _pinta(_papel(client, h, liq_id), "2. de visita en napoles (dia fijo)")
    _mem_pinta(db_session, liq_id, esc, "de visita")
    _ok(_put(client, h, dia["id"], ruta_id=esc["fabrica"]["id"]), "de vuelta")
    _mem_pinta(db_session, liq_id, esc, "de vuelta (queda la fantasma)")
    # Napoles vuelve a POR LITRO
    _napoles_a(client, h, esc, NAPOLES, "litro")
    if sin_memoria:
        monkeypatch.setattr(
            liq_service, "_lo_que_el_comprobante_tiene_escrito", lambda liq: {})
    _ok(_put(client, h, dia["id"], ruta_id=esc["napoles"]["id"]), "a napoles otra vez")
    final = _papel(client, h, liq_id)
    _pinta(final, "3. en napoles con la tarifa de HOY por litro")
    _mem_pinta(db_session, liq_id, esc, "al final")
    cobrado = _de_la_ruta(final, "Napoles")
    print(f"      Napoles cobro ${cobrado}   |   hoy diria ${POR_LITRO}"
          f"   |   la fantasma diria ${EL_FIJO}")
    assert cobrado == POR_LITRO, (
        f"hoy Napoles vale ${POR_LITRO} y el papel cobro ${cobrado}: "
        f"${cobrado - POR_LITRO} de diferencia"
    )
