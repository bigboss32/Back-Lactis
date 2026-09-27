"""LA 'PAGADA' DEL NETO EN CERO POR LA DEUDA ARRASTRADA NO DICE "YA SE PAGÓ".

Quincena 1: 90 L × $2.000 = $180.000 contra $300.000 de adelanto → debe $120.000.
Quincena 2: 85 L × $2.000 = $170.000 − $50.000 de su propio adelanto − los $120.000 que
se cobra de la 1 = neto $0,00 clavado, sin un solo pago. Pagar HOY rebota ("un aviso que
no es cierto"), pero el botón de antes sí la marcaba 'pagada' (pagado $0), y esas filas
siguen en producción.

No queda debiendo nada, así que `pagada_sin_que_saliera_un_peso` —que solo miraba
`le_queda_debiendo`— no la veía: sus días, sus observaciones y su anticipo decían "ya se
pagó". Ahora la pregunta incluye la de Pagar (`_no_sale_un_peso_por_la_deuda`) y el
porqué sale de `por_que_no_salio_un_peso`, con la cifra de la deuda de la quincena pasada
y no "le quedó debiendo $0".
"""
import uuid
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import pagada_sin_que_saliera_un_peso
from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _dia, _leer, _proveedor

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"

EL_PORQUE = "quedó debiendo de la quincena pasada ($120.000) se llevó el neto"
FALSO = ("ya se pagó", "ya se le pagó", "le quedó debiendo $0", "pago registrado",
         "abonó")


def D(v):
    return Decimal(str(v))


def _generar(client, h, prov, ini, fin):
    r = client.post(f"{API}/generar", json={"periodo_inicio": ini, "periodo_fin": fin,
                                            "tipo": "proveedor"}, headers=h)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)["id"]


def _adelanto(client, h, prov, fecha, valor):
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov, "fecha": fecha,
                               "valor": valor}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _neto_en_cero_pagada_con_el_boton_de_antes(client, h, db, nombre):
    prov = _proveedor(client, h, nombre)
    _dia(client, h, prov, "2026-06-02", "90")
    _adelanto(client, h, prov, "2026-06-01", "300000")
    q1 = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{q1}/aprobar", headers=h).status_code == 200
    dia = _dia(client, h, prov, "2026-06-17", "85")
    ant = _adelanto(client, h, prov, "2026-06-16", "50000")
    q2 = _generar(client, h, prov, "2026-06-16", "2026-06-30")
    assert client.post(f"{API}/{q2}/aprobar", headers=h).status_code == 200
    leida = _leer(client, h, q2)
    assert (D(leida["valor_total"]), D(leida["anticipos"]), D(leida["saldo_anterior"]),
            D(leida["saldo"])) == (D("170000"), D("50000"), D("120000"), D("0"))
    # Hoy Pagar no la deja pasar: es la regla de donde sale la pregunta.
    pagar = client.post(f"{API}/{q2}/pagar", headers=h)
    assert pagar.status_code == 422 and "un aviso que no es cierto" in _detalle(pagar)
    # Lo que dejó el botón de antes.
    db.get(Liquidacion, uuid.UUID(q2)).estado = "pagada"
    db.commit()
    fila = _leer(client, h, q2)
    assert (fila["estado"], D(fila["pagado"]), fila["pagos"], D(fila["le_queda_debiendo"])) \
        == ("pagada", D("0"), [], D("0"))
    return prov, dia, ant, q2


def test_la_pregunta_la_ve(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, _, _, q2 = _neto_en_cero_pagada_con_el_boton_de_antes(
        client, h, db_session, "Neto Cero Pregunta")
    assert pagada_sin_que_saliera_un_peso(db_session.get(Liquidacion, uuid.UUID(q2)))


def test_el_dia_las_observaciones_y_el_anticipo_dicen_la_deuda_que_se_llevo_el_neto(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, dia, ant, q2 = _neto_en_cero_pagada_con_el_boton_de_antes(
        client, h, db_session, "Neto Cero Todo")
    dialogo = client.get(f"{REC}/{dia}", headers=h).json()
    r = client.get(f"{REC}/grilla/quincena",
                   params={"desde": "2026-06-16", "hasta": "2026-06-30"}, headers=h)
    celda = next(f for f in r.json()["filas"] if f["proveedor_id"] == prov)["celdas"][
        "2026-06-17"]
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "80"}, headers=h)
    delete = client.delete(f"{REC}/{dia}", headers=h)
    obs = client.put(f"{API}/{q2}", json={"observaciones": "nota"}, headers=h)
    assert (put.status_code, delete.status_code, obs.status_code) == (422, 422, 422)
    assert celda["candado_aviso"] == dialogo["candado_aviso"]
    textos = {
        "dialogo": dialogo["candado_aviso"],
        "put": _detalle(put),
        "delete": _detalle(delete),
        "observaciones": _detalle(obs),
        "anticipo": client.get(f"{ANT}/{ant}", headers=h).json()["candado_aviso"],
    }
    for donde, texto in textos.items():
        print(f"\n  {donde}: {texto}")
        assert "cerrada como pagada sin que saliera un peso" in texto, donde
        assert EL_PORQUE in texto, donde
        for frase in FALSO:
            assert frase not in texto, (donde, frase)
    # El día nombra a quién, igual en el aviso y en los dos rebotes.
    frase = ("la quincena de la leche de este día quedó cerrada como pagada sin que "
             "saliera un peso, porque lo que neto cero todo quedó debiendo de la "
             "quincena pasada ($120.000) se llevó el neto")
    for donde in ("dialogo", "put", "delete"):
        assert frase in textos[donde].lower(), donde
    assert textos["observaciones"].startswith(
        "Esta quincena quedó cerrada como pagada sin que saliera un peso, porque lo que "
        f"el tercero {EL_PORQUE}")
    # Nada se movió.
    despues = _leer(client, h, q2)
    assert (despues["estado"], D(despues["saldo"]), despues["observaciones"]) == (
        "pagada", D("0"), None)


def test_la_que_cubrio_su_propio_adelanto_si_se_pago(client, base_datos, db_session):
    """Control: 90 L × $2.000 = $180.000 cubiertos EXACTO por su adelanto de $180.000,
    sin deuda arrastrada. Ahí sí salió plata —el adelanto, en la mano— y 'pagada' es la
    verdad: la pregunta no la ve y el día sigue diciendo "ya se le pagó"."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Adelanto Exacto")
    dia = _dia(client, h, prov, "2026-06-02", "90")
    _adelanto(client, h, prov, "2026-06-01", "180000")
    liq = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    fila = _leer(client, h, liq)
    assert (fila["estado"], D(fila["pagado"]), D(fila["saldo"])) == ("pagada", D(0), D(0))
    assert not pagada_sin_que_saliera_un_peso(db_session.get(Liquidacion, uuid.UUID(liq)))
    aviso = client.get(f"{REC}/{dia}", headers=h).json()["candado_aviso"]
    assert aviso.startswith("La leche de este día ya se le pagó a Adelanto Exacto:")
