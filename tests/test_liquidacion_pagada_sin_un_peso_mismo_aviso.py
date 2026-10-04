"""LA MISMA QUINCENA DICE LO MISMO EN EL ANTICIPO Y EN SUS DÍAS.

La 'pagada' que dejó el botón Pagar de antes con el tercero debiendo —100 L × $1.800 =
$180.000 contra $300.000 de adelanto, pagado $0, saldo −$120.000— no registró un solo
pago: lo único que salió fue el adelanto. El candado del anticipo ya lo decía; el día de
esa misma quincena seguía diciendo "ya se le pagó", en el diálogo, en la celda de la
grilla y en el 422 del PUT. Ahora las cuatro superficies salen de la misma pregunta,
`pagada_sin_que_saliera_un_peso`, y dicen el mismo porqué (`por_que_no_salio_un_peso`):
como el adelanto sí salió de la caja, nombra las cifras en vez de "sin que saliera un
peso".

Y la migrada a la que le borraron la deuda (saldo 0, pagado −$120.000, sin pagos) tampoco
dice "ya se le pagó" en sus días: dice la deuda borrada, igual que su anticipo.
"""
import uuid
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import _migradas
from tests.test_liquidacion_migrada_deuda_borrada import _detalle

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"


def D(v):
    return Decimal(str(v))


def _celda(client, h, prov, fecha, desde, hasta):
    r = client.get(f"{REC}/grilla/quincena", params={"desde": desde, "hasta": hasta},
                   headers=h)
    assert r.status_code == 200, r.text
    fila = next(f for f in r.json()["filas"] if f["proveedor_id"] == prov)
    return fila["celdas"][fecha]


def _las_superficies_del_dia(client, h, prov, dia_id, fecha, desde, hasta):
    """El diálogo del día, la celda de la grilla y los dos 422 (cambiar y borrar)."""
    dialogo = client.get(f"{REC}/{dia_id}", headers=h).json()
    celda = _celda(client, h, prov, fecha, desde, hasta)
    otros_litros = str(D(dialogo["cantidad_litros"]) + 5)
    put = client.put(f"{REC}/{dia_id}", json={"cantidad_litros": otros_litros}, headers=h)
    delete = client.delete(f"{REC}/{dia_id}", headers=h)
    assert put.status_code == 422 and delete.status_code == 422, (put.text, delete.text)
    assert dialogo["leche_pagada"] is True and celda["pagada"] is True
    # La celda trae el MISMO texto que el diálogo.
    assert celda["candado_aviso"] == dialogo["candado_aviso"]
    return {"dialogo": dialogo["candado_aviso"], "put": _detalle(put),
            "delete": _detalle(delete)}


def test_la_pagada_sin_un_peso_dice_lo_mismo_en_el_anticipo_y_en_el_dia(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov = client.post(f"{V}/proveedores", json={"nombre": "Una Verdad", "vereda": "X",
                                                 "precio_litro": "1800"},
                       headers=h).json()["id"]
    dia = client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                                 "cantidad_litros": "100"}, headers=h).json()["id"]
    ant = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                 "fecha": "2026-06-01", "valor": "300000"},
                      headers=h).json()["id"]
    g = client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                            "periodo_fin": "2026-06-15",
                                            "tipo": "proveedor"}, headers=h).json()
    liq = next(x for x in g["generadas"] if x["proveedor_id"] == prov)["id"]
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    db_session.get(Liquidacion, uuid.UUID(liq)).estado = "pagada"
    db_session.commit()
    fila = client.get(f"{API}/{liq}", headers=h).json()
    assert (D(fila["pagado"]), fila["pagos"], D(fila["saldo"])) == (D("0"), [], D("-120000"))

    textos = _las_superficies_del_dia(client, h, prov, dia, "2026-06-02",
                                      "2026-06-01", "2026-06-15")
    textos["anticipo"] = client.get(f"{ANT}/{ant}", headers=h).json()["candado_aviso"]
    for donde, texto in textos.items():
        print(f"\n  {donde}: {texto}")
        # El adelanto de $300.000 salió de la caja: no "sin que saliera un peso".
        assert "cerrada como pagada sin saldo por entregar" in texto, donde
        assert "sin que saliera un peso" not in texto, donde
        assert "le quedó debiendo $120.000" in texto, donde
        assert "ya se pagó" not in texto and "ya se le pagó" not in texto, donde
    # El día nombra a quién, con la misma frase en el diálogo (que la pone de primera,
    # con mayúscula) y en los dos rebotes.
    frase = ("la quincena de la leche de este día quedó cerrada como pagada sin saldo por "
             "entregar, porque los anticipos que se le aplicaron ($300.000) pasaron de su "
             "valor ($180.000) y una verdad le quedó debiendo $120.000")
    for donde in ("dialogo", "put", "delete"):
        assert frase in textos[donde].lower(), donde
    # Y nada se movió: el PUT rebotó antes de tocar la quincena.
    despues = client.get(f"{API}/{liq}", headers=h).json()
    assert (despues["estado"], D(despues["saldo"])) == ("pagada", D("-120000"))


def test_la_migrada_con_deuda_borrada_no_dice_ya_se_le_pago_en_sus_dias(
        client, base_datos, db_session):
    """$180.000 contra $300.000, migrada: 'pagada', saldo 0, pagado −$120.000, sin pagos.
    No se le pagó nada: él debe $120.000."""
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Dia Migrada").values()
    r = client.get(REC, params={"page_size": 200}, headers=h)
    (dia,) = [x for x in r.json()["items"]
              if x["proveedor_id"] == prov and x["fecha"] == "2026-07-04"]

    textos = _las_superficies_del_dia(client, h, prov, dia["id"], "2026-07-04",
                                      "2026-07-01", "2026-07-15")
    lista = client.get(ANT, params={"page_size": 200}, headers=h).json()["items"]
    textos["anticipo"] = next(a for a in lista if a["liquidacion_id"] == liq_id)[
        "candado_aviso"]
    for donde, texto in textos.items():
        print(f"\n  {donde}: {texto}")
        assert "antes de que existieran los abonos" in texto, donde
        assert "($120.000)" in texto and "repararla" in texto, donde
        assert "ya se le pagó" not in texto and "ya se pagó" not in texto, donde
