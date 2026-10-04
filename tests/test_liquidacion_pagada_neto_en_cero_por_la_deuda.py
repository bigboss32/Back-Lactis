"""LA 'PAGADA' DEL NETO EN CERO POR LA DEUDA ARRASTRADA NO DICE "YA SE PAGÓ".

Quincena 1: 90 L × $2.000 = $180.000 contra $300.000 de adelanto → debe $120.000.
Quincena 2: 100 L × $2.000 = $200.000 − los $120.000 que se cobra de la 1 = neto $80.000,
y se paga con Pagar.

A ESTA 'PAGADA' NO SE LLEGA POR PAGAR: su guardia (`_no_sale_un_peso_por_la_deuda`) nació
en el mismo cambio que `saldo_anterior` (04/08/2026) y la rebota desde el primer día —se
comprueba abajo—. La versión anterior de esta prueba la armaba escribiendo 'pagada' en la
base, una fila que en producción no existe. El camino real es corregir la quincena ya
pagada y borrarle después el pago: como va en la v2, `eliminar_pago` la deja 'pagada' con
pagado $0 y ningún pago. Son dos formas, y las dos se miden aquí:

  · PURA: Corregir le baja el precio del día a $1.200 (100 L = $120.000). Queda
    $120.000 − $120.000 de la deuda = $0. No hubo adelanto ni pago: "sin que saliera un
    peso, porque lo que debía de la quincena pasada se llevó el neto" es la verdad.
  · MIXTA: los $80.000 eran un adelanto entregado en la mano; se registra, se incluye con
    Corregir y se borra el pago mal registrado. Queda 200.000 − 80.000 − 120.000 = 0, y
    esos $80.000 SÍ salieron de la caja: ahí "sin que saliera un peso" sería falso, justo
    en el candado de ese adelanto. El texto nombra las dos cifras que cubrieron el valor,
    y suman exacto con calculadora: $80.000 + $120.000 = $200.000.

No queda debiendo nada, así que `pagada_sin_que_saliera_un_peso` —que solo miraba
`le_queda_debiendo`— no la veía: sus días, sus observaciones y su anticipo decían "ya se
pagó". La pregunta incluye la de Pagar y el porqué sale de `por_que_no_salio_un_peso`. Y
la pantalla lee esa misma frase en `LiquidacionRead.cerrada_sin_pago`, en vez de decir "El
pago quedó registrado" sobre una tabla de pagos vacía.
"""
import uuid
from decimal import Decimal

import pytest

from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import pagada_sin_que_saliera_un_peso
from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _dia, _leer, _proveedor

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"

SE_LLEVO_EL_NETO = "quedó debiendo de la quincena pasada ($120.000) se llevó el neto"
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


def _q2_pagada_que_cobro_la_deuda(client, h, nombre):
    """Q1 deja $120.000 de deuda; Q2 ($200.000) se los cobra y se paga su neto de $80.000."""
    prov = _proveedor(client, h, nombre)
    _dia(client, h, prov, "2026-06-02", "90")
    _adelanto(client, h, prov, "2026-06-01", "300000")
    q1 = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{q1}/aprobar", headers=h).status_code == 200
    dia = _dia(client, h, prov, "2026-06-17", "100")
    q2 = _generar(client, h, prov, "2026-06-16", "2026-06-30")
    assert client.post(f"{API}/{q2}/aprobar", headers=h).status_code == 200
    aprobada = _leer(client, h, q2)
    assert (D(aprobada["valor_total"]), D(aprobada["saldo_anterior"]),
            D(aprobada["saldo"])) == (D(200000), D(120000), D(80000))
    pagada = client.post(f"{API}/{q2}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    (pago,) = pagada.json()["pagos"]
    assert D(pago["valor"]) == D(80000)
    return prov, dia, q2, pago["id"]


def _borrar_el_pago(client, h, q2, pago_id):
    r = client.delete(f"{API}/{q2}/pagos/{pago_id}", headers=h)
    assert r.status_code == 200, r.text
    fila = _leer(client, h, q2)
    assert (fila["estado"], fila["version"], D(fila["pagado"]), D(fila["saldo"]),
            fila["pagos"], D(fila["le_queda_debiendo"])) == ("pagada", 2, D(0), D(0), [], D(0))
    # La regla de oro con calculadora: valor − anticipos − lo que quedó debiendo = neto.
    assert D(fila["valor_total"]) - D(fila["anticipos"]) - D(fila["saldo_anterior"]) == \
        D(fila["neto_a_pagar"]) == D(0)
    return fila


def _pura(client, h, nombre):
    """Corregir le baja el precio a $1.200: 100 L = $120.000, cubiertos por la deuda."""
    prov, dia, q2, pago_id = _q2_pagada_que_cobro_la_deuda(client, h, nombre)
    detalle = [d for d in _leer(client, h, q2)["detalles"] if d.get("deleted_at") is None][0]
    r = client.post(f"{API}/{q2}/corregir", json={
        "motivo": "el precio del 17/06 se digitó mal",
        "precios": [{"detalle_id": detalle["id"], "precio_litro": "1200"}]}, headers=h)
    assert r.status_code == 200, r.text
    fila = _borrar_el_pago(client, h, q2, pago_id)
    assert (D(fila["valor_total"]), D(fila["anticipos"]), D(fila["saldo_anterior"])) == (
        D(120000), D(0), D(120000))
    return prov, dia, q2, fila


def _mixta(client, h, nombre):
    """Los $80.000 eran un adelanto en la mano: se incluye con Corregir y se borra el pago."""
    prov, dia, q2, pago_id = _q2_pagada_que_cobro_la_deuda(client, h, nombre)
    ant = _adelanto(client, h, prov, "2026-06-20", "80000")
    r = client.post(f"{API}/{q2}/corregir", json={
        "motivo": "los $80.000 fueron un adelanto en la mano, no un pago",
        "anticipos_a_incluir": [ant]}, headers=h)
    assert r.status_code == 200, r.text
    fila = _borrar_el_pago(client, h, q2, pago_id)
    assert (D(fila["valor_total"]), D(fila["anticipos"]), D(fila["saldo_anterior"])) == (
        D(200000), D(80000), D(120000))
    return prov, dia, q2, ant, fila


def _superficies(client, h, prov, dia, q2):
    """El diálogo del día, la celda de la grilla, los dos 422 del día y el de las
    observaciones. La celda dice EXACTO lo del diálogo."""
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
    return {
        "dialogo": dialogo["candado_aviso"],
        "put": _detalle(put),
        "delete": _detalle(delete),
        "observaciones": _detalle(obs),
    }


@pytest.mark.parametrize(
    "litros, adelanto, valor, que_se_trabaria",
    [
        # PURA: 60 L × $2.000 = $120.000 contra los $120.000 de la deuda, sin adelanto.
        ("60", None, "120000", "los días"),
        # MIXTA: 100 L × $2.000 = $200.000 = $80.000 de su adelanto + $120.000 de la deuda.
        ("100", "80000", "200000", "los días y los anticipos"),
    ],
    ids=["pura", "mixta"],
)
def test_pagar_rebota_el_neto_en_cero_por_la_deuda(
    litros, adelanto, valor, que_se_trabaria, client, base_datos, db_session
):
    """Por qué la prueba ya no escribe 'pagada' en la base: Pagar la rebota. Q1 deja
    $120.000 de deuda y Q2 queda en $0 clavado, en las dos formas: la pura ($120.000 −
    $120.000) y la mixta ($200.000 − $80.000 de su adelanto − $120.000 de la deuda).

    Y EL PORQUÉ DEL REBOTE ES EL DE HOY. Decía que marcarla pagada la trabaría "con un
    aviso que no es cierto", y ese aviso ya es cierto: la pagada con las cifras de la mixta
    dice que la cerraron los anticipos ($80.000) y lo que quedó debiendo ($120.000), y
    80.000 + 120.000 = 200.000. Lo cierto es que Pagar no le entrega un peso a nadie y que
    en 'aprobada' sus días —y su adelanto, si tiene— todavía se corrigen. La pura no tiene
    adelanto: "los días y los anticipos" le nombraba unos anticipos que no existen. Abonar
    dice lo mismo, y la pantalla lo lee tal cual (`aviso_sin_un_peso_por_la_deuda`)."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, f"Neto Cero Pagar {litros}")
    _dia(client, h, prov, "2026-06-02", "90")
    _adelanto(client, h, prov, "2026-06-01", "300000")
    q1 = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{q1}/aprobar", headers=h).status_code == 200
    dia = _dia(client, h, prov, "2026-06-17", litros)
    ant = _adelanto(client, h, prov, "2026-06-16", adelanto) if adelanto else None
    q2 = _generar(client, h, prov, "2026-06-16", "2026-06-30")
    assert client.post(f"{API}/{q2}/aprobar", headers=h).status_code == 200
    leida = _leer(client, h, q2)
    assert (D(leida["valor_total"]), D(leida["anticipos"]), D(leida["saldo_anterior"]),
            D(leida["saldo"])) == (D(valor), D(adelanto or 0), D("120000"), D("0"))
    assert D(leida["anticipos"]) + D(leida["saldo_anterior"]) == D(leida["valor_total"])
    pagar = client.post(f"{API}/{q2}/pagar", headers=h)
    assert pagar.status_code == 422
    assert _detalle(pagar) == (
        "Esta liquidación no hay que pagarla: no queda un peso por entregar —lo que el "
        "tercero quedó debiendo de la quincena pasada ($120.000) se llevó lo que faltaba "
        "del neto—. Déjela en 'aprobada': marcarla pagada no le entrega un peso a nadie y "
        f"le trabaría {que_se_trabaria}, que en 'aprobada' todavía se pueden corregir")
    assert "no es cierto" not in _detalle(pagar)
    abonar = client.post(f"{API}/{q2}/pagos", json={"fecha": "2026-07-01", "valor": "1000"},
                         headers=h)
    assert abonar.status_code == 422 and _detalle(abonar) == _detalle(pagar)
    leida = _leer(client, h, q2)
    assert leida["estado"] == "aprobada"
    assert leida["aviso_sin_un_peso_por_la_deuda"] == _detalle(pagar)
    # Lo que el porqué afirma, medido: en 'aprobada' su día y su adelanto no tienen candado.
    if ant is not None:
        assert client.get(f"{ANT}/{ant}", headers=h).json()["candado_aviso"] is None
    assert client.get(f"{REC}/{dia}", headers=h).json()["candado_aviso"] is None


def test_la_pregunta_la_ve_en_las_dos_formas(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, _, pura, _ = _pura(client, h, "Neto Cero Pregunta Pura")
    _, _, mixta, _, _ = _mixta(client, h, "Neto Cero Pregunta Mixta")
    for q2 in (pura, mixta):
        assert pagada_sin_que_saliera_un_peso(db_session.get(Liquidacion, uuid.UUID(q2)))


def test_la_pura_dice_la_deuda_que_se_llevo_el_neto(client, base_datos, db_session):
    """$120.000 de leche contra $120.000 de deuda, sin adelanto ni pago: no salió un peso."""
    h = auth_headers(client, "admin.a")
    prov, dia, q2, fila = _pura(client, h, "Neto Cero Puro")
    textos = _superficies(client, h, prov, dia, q2)
    for donde, texto in textos.items():
        print(f"\n  {donde}: {texto}")
        assert "cerrada como pagada sin que saliera un peso" in texto, donde
        assert SE_LLEVO_EL_NETO in texto, donde
        for frase in FALSO:
            assert frase not in texto, (donde, frase)
    # El día nombra a quién, igual en el aviso y en los dos rebotes.
    frase = ("la quincena de la leche de este día quedó cerrada como pagada sin que "
             "saliera un peso, porque lo que neto cero puro quedó debiendo de la "
             "quincena pasada ($120.000) se llevó el neto")
    for donde in ("dialogo", "put", "delete"):
        assert frase in textos[donde].lower(), donde
    # La pantalla lee la MISMA frase con que rebotan las observaciones.
    assert fila["cerrada_sin_pago"] == (
        "Esta quincena quedó cerrada como pagada sin que saliera un peso, porque lo que "
        f"el tercero {SE_LLEVO_EL_NETO}")
    assert textos["observaciones"].startswith(f"{fila['cerrada_sin_pago']}:")
    # Nada se movió.
    despues = _leer(client, h, q2)
    assert (despues["estado"], D(despues["saldo"]), despues["observaciones"]) == (
        "pagada", D("0"), None)


def test_la_mixta_nombra_el_adelanto_que_si_salio(client, base_datos, db_session):
    """$200.000 cubiertos por el adelanto de $80.000 —que salió de la caja— y los $120.000
    de la deuda vieja. Ni "sin que saliera un peso" ni "se llevó el neto": las dos cifras,
    que suman exacto el valor."""
    h = auth_headers(client, "admin.a")
    prov, dia, q2, ant, fila = _mixta(client, h, "Neto Cero Mixto")
    assert D(fila["anticipos"]) + D(fila["saldo_anterior"]) == D(fila["valor_total"])
    textos = _superficies(client, h, prov, dia, q2)
    textos["anticipo"] = client.get(f"{ANT}/{ant}", headers=h).json()["candado_aviso"]
    put_anticipo = client.put(f"{ANT}/{ant}", json={"valor": "70000"}, headers=h)
    assert put_anticipo.status_code == 422
    textos["put anticipo"] = _detalle(put_anticipo)
    cubrieron = ("sin saldo por entregar, porque los anticipos que se le aplicaron "
                 "($80.000) y lo que {quien} quedó debiendo de la quincena pasada "
                 "($120.000) cubrieron exacto su valor ($200.000)")
    for donde, texto in textos.items():
        print(f"\n  {donde}: {texto}")
        assert "sin que saliera un peso" not in texto, donde
        assert "se llevó el neto" not in texto, donde
        assert "cerrada como pagada sin saldo por entregar" in texto, donde
        for frase in FALSO:
            assert frase not in texto, (donde, frase)
    for donde in ("dialogo", "put", "delete"):
        assert cubrieron.format(quien="neto cero mixto") in textos[donde].lower(), donde
    for donde in ("observaciones", "anticipo", "put anticipo"):
        assert cubrieron.format(quien="el tercero") in textos[donde], donde
    assert fila["cerrada_sin_pago"] == (
        f"Esta quincena quedó cerrada como pagada {cubrieron.format(quien='el tercero')}")
    assert textos["observaciones"].startswith(f"{fila['cerrada_sin_pago']}:")
    despues = _leer(client, h, q2)
    assert (despues["estado"], D(despues["anticipos"]), D(despues["saldo"])) == (
        "pagada", D(80000), D(0))


def test_la_que_cubrio_su_propio_adelanto_si_se_pago(client, base_datos, db_session):
    """Control: 90 L × $2.000 = $180.000 cubiertos EXACTO por su adelanto de $180.000,
    sin deuda arrastrada. Ahí sí salió plata —el adelanto, en la mano— y 'pagada' es la
    verdad: la pregunta no la ve, el día sigue diciendo "ya se le pagó" y la pantalla no
    recibe ninguna frase de "cerrada sin pago"."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Adelanto Exacto")
    dia = _dia(client, h, prov, "2026-06-02", "90")
    _adelanto(client, h, prov, "2026-06-01", "180000")
    liq = _generar(client, h, prov, "2026-06-01", "2026-06-15")
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    fila = _leer(client, h, liq)
    assert (fila["estado"], D(fila["pagado"]), D(fila["saldo"])) == ("pagada", D(0), D(0))
    assert fila["cerrada_sin_pago"] is None
    assert not pagada_sin_que_saliera_un_peso(db_session.get(Liquidacion, uuid.UUID(liq)))
    aviso = client.get(f"{REC}/{dia}", headers=h).json()["candado_aviso"]
    assert aviso.startswith("La leche de este día ya se le pagó a Adelanto Exacto:")


def test_el_porque_nombra_cifras_que_suman_exacto_en_cada_rama():
    """Las cinco formas de la 'pagada' sin pago, sin base de datos. Solo las dos sin
    adelantos propios dicen "sin que saliera un peso"; las otras nombran las cifras, y la
    cuenta da exacto con calculadora (aquí pagado es $0)."""
    from app.modules.liquidaciones.service import cerrada_sin_pago, por_que_no_salio_un_peso

    def fila(valor, anticipos, arrastrada):
        liq = Liquidacion(estado="pagada", valor_total=D(valor), anticipos=D(anticipos),
                          saldo_anterior=D(arrastrada), pagado=D(0))
        liq.saldo = liq.neto_a_pagar
        assert pagada_sin_que_saliera_un_peso(liq)
        return liq

    casos = [
        # 120.000 − 0 − 120.000 = 0: la deuda sola se llevó el neto.
        (fila(120000, 0, 120000),
         "sin que saliera un peso, porque lo que el tercero quedó debiendo de la quincena "
         "pasada ($120.000) se llevó el neto"),
        # 100.000 − 0 − 120.000 = −20.000: la deuda pasó del valor.
        (fila(100000, 0, 120000),
         "sin que saliera un peso, porque el tercero le quedó debiendo $20.000"),
        # 80.000 + 120.000 = 200.000.
        (fila(200000, 80000, 120000),
         "sin saldo por entregar, porque los anticipos que se le aplicaron ($80.000) y lo "
         "que el tercero quedó debiendo de la quincena pasada ($120.000) cubrieron exacto "
         "su valor ($200.000)"),
        # 300.000 − 180.000 = 120.000.
        (fila(180000, 300000, 0),
         "sin saldo por entregar, porque los anticipos que se le aplicaron ($300.000) "
         "pasaron de su valor ($180.000) y el tercero le quedó debiendo $120.000"),
        # 100.000 + 50.000 − 100.000 = 50.000.
        (fila(100000, 100000, 50000),
         "sin saldo por entregar, porque los anticipos que se le aplicaron ($100.000) y lo "
         "que el tercero quedó debiendo de la quincena pasada ($50.000) pasaron de su valor "
         "($100.000), y el tercero le quedó debiendo $50.000"),
    ]
    for liq, esperado in casos:
        assert por_que_no_salio_un_peso(liq) == esperado
        assert cerrada_sin_pago(liq) == f"Esta quincena quedó cerrada como pagada {esperado}"
