"""EL ADELANTO QUE SOLTÓ UNA QUINCENA CON DEUDA BORRADA NO MANDA A «CORREGIR ESTA QUINCENA».

La quincena de julio: 90 L × $2.000 = $180.000 contra un adelanto de $300.000, pagada con
el botón de antes y migrada (pagado −$120.000, saldo $0, deuda borrada $120.000). En
producción se pudo corregir soltando el adelanto antes de que Corregir tuviera su guardia:
quedó 'parcial' v2, neto $180.000, pagado −$120.000, saldo $300.000.

El candado de ese adelanto decía "Hay dos salidas: vuelva a incluirlo con 'Corregir esta
quincena' … o espere a que la quincena siguiente lo recoja", y Corregir sobre esa fila
rebota siempre: la primera salida no existe. Ahora el aviso nombra la deuda borrada de la
quincena de la que salió y deja solo la salida que sí sirve, que se prueba siguiéndola.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import _antes_del_guardia
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _dia, _leer, _migrada

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"


def D(v):
    return Decimal(str(v))


def _adelanto_de(client, h, liq_id):
    lista = client.get(ANT, params={"page_size": 200}, headers=h).json()["items"]
    return next(a["id"] for a in lista if a["liquidacion_id"] == liq_id)


def _soltar(client, h, liq_id, adelanto):
    r = client.post(f"{API}/{liq_id}/corregir", json={
        "motivo": "el adelanto no iba en esta quincena",
        "anticipos_a_soltar": [adelanto]}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_el_candado_no_manda_a_corregir_la_quincena_que_no_se_deja(
    client, base_datos, db_session, monkeypatch
):
    h = auth_headers(client, "admin.a")
    prov, migrada = _migrada(client, h, db_session, "Soltado Borrada")
    adelanto = _adelanto_de(client, h, migrada["id"])
    _antes_del_guardia(monkeypatch)        # como se corrigió en producción
    fila = _soltar(client, h, migrada["id"], adelanto)
    monkeypatch.undo()
    assert (fila["estado"], fila["version"]) == ("parcial", 2)
    assert D(fila["saldo"]) == D("300000")
    assert D(fila["deuda_borrada_por_la_migracion"]) == D("120000")

    marca = client.get(f"{ANT}/{adelanto}", headers=h).json()
    aviso = marca["candado_aviso"]
    print(f"\n  aviso: {aviso}")
    assert marca["bloqueado"] is True
    assert "Corregir esta quincena" not in aviso
    assert "YA SE IMPRIMIÓ" in aviso
    assert ("La quincena de la que salió (la del 01/07/2026 al 15/07/2026) no se puede "
            "corregir: viene de antes de que existieran los abonos, y el sistema de esa "
            "época le borró lo que el tercero quedaba debiendo ($120.000).") in aviso
    assert aviso.endswith("Queda una salida: espere a que la quincena siguiente lo recoja "
                          "y corríjalo ahí antes de pagarla")
    # El mismo texto en la lista y en los dos rebotes.
    en_lista = next(x for x in client.get(ANT, params={"page_size": 200}, headers=h)
                    .json()["items"] if x["id"] == adelanto)
    assert en_lista["candado_aviso"] == aviso
    put = client.put(f"{ANT}/{adelanto}", json={"valor": "1000"}, headers=h)
    dele = client.delete(f"{ANT}/{adelanto}", headers=h)
    assert put.status_code == 422 and dele.status_code == 422
    assert _detalle(put) == aviso.replace("modificar ni eliminar", "modificar")
    assert _detalle(dele) == aviso.replace("modificar ni eliminar", "eliminar")
    # Y es verdad que la quincena de la que salió no se deja corregir.
    r = client.post(f"{API}/{migrada['id']}/corregir/previsualizar", json={
        "motivo": "vuelve el adelanto", "anticipos_a_incluir": [adelanto]}, headers=h)
    assert r.status_code == 422

    # La salida que queda sí sirve: la siguiente lo recoge y ahí se corrige.
    _dia(client, h, prov, "2026-07-20", "100")
    r = client.post(f"{API}/generar", json={"periodo_inicio": "2026-07-16",
                                            "periodo_fin": "2026-07-31",
                                            "tipo": "proveedor"}, headers=h)
    assert r.status_code == 200, r.text
    siguiente = next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)
    marca = client.get(f"{ANT}/{adelanto}", headers=h).json()
    assert marca["liquidacion_id"] == siguiente["id"] and marca["bloqueado"] is False
    assert client.put(f"{ANT}/{adelanto}", json={"valor": "250000"},
                      headers=h).status_code == 200


def test_si_la_quincena_se_deja_corregir_las_dos_salidas_siguen(client, base_datos):
    """La de siempre: quincena sana de $500.000 con $300.000 de adelanto, pagada; una
    corrección suelta el adelanto. Corregir esa quincena sí funciona, y el texto lo ofrece."""
    h = auth_headers(client, "admin.a")
    r = client.post(f"{V}/proveedores", json={"nombre": "Soltado Sano", "vereda": "X",
                                              "precio_litro": "2000"}, headers=h)
    prov = r.json()["id"]
    _dia(client, h, prov, "2026-07-04", "250")
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                               "fecha": "2026-07-06", "valor": "300000"}, headers=h)
    adelanto = r.json()["id"]
    r = client.post(f"{API}/generar", json={"periodo_inicio": "2026-07-01",
                                            "periodo_fin": "2026-07-15",
                                            "tipo": "proveedor"}, headers=h)
    liq = next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq['id']}/pagar", headers=h).status_code == 200
    _soltar(client, h, liq["id"], adelanto)
    aviso = client.get(f"{ANT}/{adelanto}", headers=h).json()["candado_aviso"]
    assert aviso.endswith(
        "Hay dos salidas: vuelva a incluirlo con 'Corregir esta quincena' —que deja escrito "
        "el motivo—, o espere a que la quincena siguiente lo recoja y corríjalo ahí antes "
        "de pagarla"
    )
    assert _leer(client, h, liq["id"])["version"] == 2
    r = client.post(f"{API}/{liq['id']}/corregir/previsualizar", json={
        "motivo": "vuelve el adelanto", "anticipos_a_incluir": [adelanto]}, headers=h)
    assert r.status_code == 200, r.text
