"""EL GUARDADO DE UN DÍA DICE QUÉ LIQUIDACIONES MANDÓ DE VUELTA A BORRADOR.

`LiquidacionService.recuadrar` ya contestaba True cuando una APROBADA volvía a borrador
por el día que se tocó, y Recepción diaria botaba ese dato: la pantalla tenía que
adivinar si avisar "vuelva a aprobarla". Ahora el POST y el PUT del día traen
`liquidaciones_devueltas_a_borrador: [{id, tipo}]` (no es columna; vacía si no pasó). El
DELETE contesta 204, sin cuerpo, y no lo trae.

  · litros de un día en una quincena APROBADA → esa quincena, que queda en borrador;
  · otra vez sobre la misma, ya en borrador → vacía: esta vez no se devolvió nada;
  · solo las observaciones de un día TRABADO (quincena pagada) → vacía, y sigue pagada;
  · el transportador de un día que ENTRA al viaje fijo ya cobrado de un comprobante de
    flete APROBADO (`_volver_al_viaje_ya_cobrado`) → ese comprobante.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_transporte_cruce_de_modos import (
    AURELIO,
    DIA_FIJO,
    EL_DIA,
    MARLENY,
    _escenario,
    _liquidar,
    _recibir,
)

V = "/api/v1"
API = f"{V}/liquidaciones"
REC = f"{V}/recepciones"
CAMPO = "liquidaciones_devueltas_a_borrador"


def D(v):
    return Decimal(str(v))


def _quincena_de_leche(client, h, nombre):
    """100 L × $1.800 = $180.000, aprobada."""
    prov = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                                 "precio_litro": "1800"},
                       headers=h).json()["id"]
    r = client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                               "cantidad_litros": "100"}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()[CAMPO] == []
    dia = r.json()["id"]
    g = client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                            "periodo_fin": "2026-06-15",
                                            "tipo": "proveedor"}, headers=h).json()
    liq = next(x for x in g["generadas"] if x["proveedor_id"] == prov)["id"]
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    return dia, liq


def _estado(client, h, liq_id):
    return client.get(f"{API}/{liq_id}", headers=h).json()["estado"]


def test_cambiar_los_litros_de_una_aprobada_la_nombra(client, base_datos):
    h = auth_headers(client, "admin.a")
    dia, liq = _quincena_de_leche(client, h, "Devuelta Litros")
    r = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()[CAMPO] == [{"id": liq, "tipo": "proveedor"}]
    assert _estado(client, h, liq) == "borrador"
    # Ya está en borrador: el siguiente guardado no devuelve nada, y no lo dice.
    r = client.put(f"{REC}/{dia}", json={"cantidad_litros": "80"}, headers=h)
    assert r.status_code == 200 and r.json()[CAMPO] == [], r.text
    # Es un hecho del guardado, no de la lectura.
    assert CAMPO not in client.get(f"{REC}/{dia}", headers=h).json()


def test_las_observaciones_de_un_dia_trabado_no_devuelven_nada(client, base_datos):
    h = auth_headers(client, "admin.a")
    dia, liq = _quincena_de_leche(client, h, "Devuelta Trabada")
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    r = client.put(f"{REC}/{dia}", json={"observaciones": "tarro sucio"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["leche_pagada"] is True and r.json()[CAMPO] == []
    assert _estado(client, h, liq) == "pagada"


def test_el_dia_que_entra_al_viaje_ya_cobrado_nombra_ese_comprobante(
        client, base_datos, db_session):
    """El 16/07 en la ruta fija ($150.000): Aurelio 82,00 L y Marleny 137,45 L, flete de
    Alex APROBADO. Aurelio se va con Beto: el comprobante se recuadra sin él y vuelve a
    borrador (lo dice esa respuesta). Se aprueba otra vez. Aurelio vuelve con Alex:
    entra otra vez a ese viaje, el comprobante se recuadra con él y vuelve a borrador, y
    esta respuesta lo dice aunque el día llegó suelto."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, DIA_FIJO, sufijo=" Devuelta")
    aurelio = _recibir(client, h, esc, EL_DIA, "Aurelio", AURELIO)
    _recibir(client, h, esc, EL_DIA, "Marleny", MARLENY)
    flete = _liquidar(client, h, esc)["id"]
    assert client.post(f"{API}/{flete}/aprobar", headers=h).status_code == 200
    devuelta = [{"id": flete, "tipo": "transportador"}]

    se_va = client.put(f"{REC}/{aurelio['id']}",
                       json={"transportador_id": esc["beto"]["id"]}, headers=h)
    assert se_va.status_code == 200, se_va.text
    assert se_va.json()["liquidacion_transporte_id"] is None
    assert se_va.json()[CAMPO] == devuelta
    assert _estado(client, h, flete) == "borrador"

    assert client.post(f"{API}/{flete}/aprobar", headers=h).status_code == 200
    vuelve = client.put(f"{REC}/{aurelio['id']}",
                        json={"transportador_id": esc["alex"]["id"]}, headers=h)
    assert vuelve.status_code == 200, vuelve.text
    assert vuelve.json()["liquidacion_transporte_id"] == flete
    assert vuelve.json()[CAMPO] == devuelta
    assert _estado(client, h, flete) == "borrador"
    # El viaje sigue valiendo $150.000, repartido entre los dos.
    assert D(client.get(f"{API}/{flete}", headers=h).json()["valor_total"]) == D("150000")
