"""REVISIÓN ADVERSARIAL 2, lado backend (RB1-RB5 sobre la ronda 1). Solo mide; no arregla.

Las `test_d*` REPRODUCEN UN DEFECTO y pasan mientras el defecto siga ahí (igual que los
demás archivos zz); las `test_ok*` son controles de lo que quedó bien.

  D1  La migrada que se corrigió HACIA ARRIBA antes del guardia y no se ha pagado: la
      quesera le debe $80.000 al productor, y el guardia dice que él "todavía debe".
  D2  Dos abonos y se borra uno: la cifra "que el tercero quedaba debiendo" pasa a
      $50.000 (la migración borró $120.000) y el productor ya no debe: se le deben $30.000.
  D3  El tablero y el balance siguen prometiendo como "por pagar" la plata que las
      tarjetas ya no cuentan porque el servidor no deja pagarla.
  D4  La misma 'pagada' sin un peso entregado: el anticipo dice "sin que saliera un peso"
      y el día de esa quincena dice "ya se le pagó".
"""
import uuid
from datetime import date
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import (
    _abonar,
    _antes_del_guardia,
    _corregir,
    _entregado,
    _migradas,
)
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _dia, _leer

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"


def D(v):
    return Decimal(str(v))


def _resumen(client, h, **q):
    r = client.get(f"{API}/resumen", params=q, headers=h)
    assert r.status_code == 200, r.text
    return {k: (D(v) if isinstance(v, str) else v) for k, v in r.json().items()}


def _anticipo_de(client, h, liq_id):
    lista = client.get(ANT, params={"page_size": 200}, headers=h).json()["items"]
    return next(a for a in lista if a["liquidacion_id"] == liq_id)


# =====================================================================================
# D1. Corregida hacia arriba ($200.000) antes del guardia y SIN pagar: el guardia dice
#     que el productor "todavía debe" cuando es la quesera la que le debe $80.000.
# =====================================================================================
def test_d1_corregida_hacia_arriba_sin_pagar_el_guardia_dice_que_debe_quien_cobra(
        client, base_datos, db_session, monkeypatch):
    """90 L × $2.000 = $180.000 contra $300.000 (debe $120.000), migrada. Antes del
    guardia se le corrigió un día olvidado de 100 L = $200.000 y NO se pagó:
    valor 380.000 − anticipos 300.000 = neto $80.000, sin un solo pago. Con calculadora:
    la quesera le debe $80.000 al productor. El sistema: saldo $200.000 (los $120.000 de
    la migración van sumados), deuda borrada $120.000, Pagar y abonar rebotan."""
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Arriba Sin Pagar").values()
    grande = _dia(client, h, prov, "2026-07-08", "100")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, grande)
    monkeypatch.undo()

    hoy = _leer(client, h, liq_id)
    neto, entregado = D(hoy["neto_a_pagar"]), _entregado(hoy)
    le_debe_la_quesera = neto - entregado
    pagar = client.post(f"{API}/{liq_id}/pagar", headers=h)
    abono = client.post(f"{API}/{liq_id}/pagos", json={"fecha": "2026-08-10",
                                                       "valor": "80000"}, headers=h)
    aviso = _anticipo_de(client, h, liq_id)["candado_aviso"]
    r = _resumen(client, h)
    print(f"\n  estado={hoy['estado']} v{hoy['version']} neto={neto} pagado={hoy['pagado']} "
          f"saldo={hoy['saldo']} pagos={entregado} "
          f"borrada={hoy['deuda_borrada_por_la_migracion']} "
          f"le_queda_debiendo={hoy['le_queda_debiendo']}")
    print(f"  la quesera le debe al productor: {le_debe_la_quesera}")
    print(f"  pagar -> {pagar.status_code} {_detalle(pagar)!r}")
    print(f"  anticipo candado_aviso: {aviso!r}")
    print(f"  resumen: {r}")
    assert (neto, entregado, le_debe_la_quesera) == (D("80000"), D("0"), D("80000"))
    assert D(hoy["deuda_borrada_por_la_migracion"]) == D("120000")
    assert D(hoy["le_queda_debiendo"]) == 0
    # EL DEFECTO: el 422 de Pagar, el de abonar y el candado del anticipo dicen que al
    # productor se le pagaría "a alguien que todavía debe"; no debe nada, se le deben $80.000.
    assert pagar.status_code == 422 and abono.status_code == 422
    for texto in (_detalle(pagar), _detalle(abono), aviso):
        assert "a alguien que todavía debe" in texto, texto
    # Y los $80.000 que la quesera de verdad debe no salen en ninguna tarjeta de plata:
    # la fila no suma a "por pagar" y la única cifra que se ve es "deuda borrada
    # $120.000", rotulada como lo que el tercero quedaba debiendo.
    assert r["parciales"] == 1 and r["saldo_parciales"] == 0
    assert r["le_quedaron_debiendo"] == 0
    assert (r["por_reparar"], r["deuda_borrada"]) == (1, D("120000"))
    # Y el tablero, que no conoce la deuda borrada, dice $200.000 por pagar: tres
    # cifras para la misma fila ($0 tarjetas, $200.000 tablero, $80.000 la verdad).
    tablero = client.get(f"{V}/reportes/dashboard", headers=h).json()
    print(f"  tablero liquidaciones_por_pagar={tablero['liquidaciones_por_pagar']}")
    assert D(tablero["liquidaciones_por_pagar"]) == D("200000")


# =====================================================================================
# D2. Dos abonos y se borra uno: la cifra deja de ser "lo que la migración borró".
# =====================================================================================
def test_d2_tras_borrar_un_abono_la_cifra_ya_no_es_la_deuda_que_borro_la_migracion(
        client, base_datos, db_session, monkeypatch):
    """La de $200.000 pagada en dos abonos ($150.000 + $50.000) antes del guardia; hoy se
    borra el de $150.000 (mal registrado). `eliminar_pago` hace max(80.000 − 150.000, 0)
    = 0: pagado 0, un pago vivo de $50.000, saldo $80.000, borrada 50.000 − 0 = $50.000.
    Con calculadora: neto 80.000 − 50.000 entregados = la quesera le debe $30.000. El
    mensaje dice que la migración le borró $50.000 "que el tercero quedaba debiendo"
    (borró $120.000) y que pagarle es pagarle "a alguien que todavía debe"."""
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Dos Abonos Revision").values()
    grande = _dia(client, h, prov, "2026-07-08", "100")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, grande)
    _abonar(client, h, liq_id, "150000")
    _abonar(client, h, liq_id, "50000")
    monkeypatch.undo()
    antes = _leer(client, h, liq_id)
    assert D(antes["deuda_borrada_por_la_migracion"]) == D("120000")
    pago = next(p for p in antes["pagos"] if D(p["valor"]) == D("150000"))
    assert client.delete(f"{API}/{liq_id}/pagos/{pago['id']}", headers=h).status_code == 200

    hoy = _leer(client, h, liq_id)
    le_debe_la_quesera = D(hoy["neto_a_pagar"]) - _entregado(hoy)
    pagar = client.post(f"{API}/{liq_id}/pagar", headers=h)
    print(f"\n  pagado={hoy['pagado']} saldo={hoy['saldo']} pagos={_entregado(hoy)} "
          f"borrada={hoy['deuda_borrada_por_la_migracion']} le debe la quesera="
          f"{le_debe_la_quesera}")
    print(f"  pagar -> {pagar.status_code} {_detalle(pagar)!r}")
    assert le_debe_la_quesera == D("30000")
    assert D(hoy["deuda_borrada_por_la_migracion"]) == D("50000")
    # EL DEFECTO: $50.000 no es lo que borró la migración ($120.000) ni lo que el
    # productor debe (nada: se le deben $30.000), y el mensaje afirma las dos cosas.
    assert pagar.status_code == 422
    assert "quedaba debiendo ($50.000)" in _detalle(pagar)
    assert "a alguien que todavía debe" in _detalle(pagar)


# =====================================================================================
# D3. El tablero y el balance siguen contando la plata que las tarjetas ya no prometen.
# =====================================================================================
def test_d3_tablero_y_balance_prometen_lo_que_las_tarjetas_ya_no(
        client, base_datos, db_session):
    """La corregida antes del guardia (parcial v2, $230.000 − $300.000, pagado −$120.000,
    saldo +$50.000; la verdad: debe $70.000). Tras RB2 la tarjeta "Parciales" ya no suma
    esos $50.000 —el servidor no deja pagarlos—, pero el tablero y el balance sí."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    r = client.post(f"{V}/proveedores", json={"nombre": "Tablero Revision", "vereda": "X",
                                              "precio_litro": "2000"}, headers=h)
    assert r.status_code == 201, r.text
    liq = Liquidacion(
        empresa_id=emp, tipo="proveedor", proveedor_id=uuid.UUID(r.json()["id"]),
        periodo_inicio=date(2026, 7, 1), periodo_fin=date(2026, 7, 15),
        total_litros=D("115"), valor_bruto=D("230000"), valor_total=D("230000"),
        anticipos=D("300000"), saldo_anterior=D("0"), pagado=D("-120000"),
        saldo=D("50000"), estado="parcial", version=2)
    db_session.add(liq)
    db_session.commit()

    tarjetas = _resumen(client, h)
    tablero = client.get(f"{V}/reportes/dashboard", headers=h)
    balance = client.get(f"{V}/contabilidad/balance", headers=h)
    assert tablero.status_code == 200 and balance.status_code == 200, (tablero.text,
                                                                         balance.text)
    pagar = client.post(f"{API}/{liq.id}/pagar", headers=h)
    print(f"\n  tarjetas: parciales={tarjetas['parciales']} "
          f"saldo_parciales={tarjetas['saldo_parciales']}")
    print(f"  tablero liquidaciones_por_pagar={tablero.json()['liquidaciones_por_pagar']}")
    print(f"  balance liquidaciones_por_pagar={balance.json()['liquidaciones_por_pagar']}")
    print(f"  pagar -> {pagar.status_code}")
    assert pagar.status_code == 422
    assert tarjetas["saldo_parciales"] == 0
    # EL DEFECTO: $50.000 "por pagar" en dos pantallas, que ningún botón entrega y que la
    # tarjeta del listado ya no dice.
    assert D(tablero.json()["liquidaciones_por_pagar"]) == D("50000")
    assert D(balance.json()["liquidaciones_por_pagar"]) == D("50000")


# =====================================================================================
# D4. La misma quincena, dos pantallas, dos verdades distintas.
# =====================================================================================
def test_d4_la_pagada_sin_un_peso_el_anticipo_y_el_dia_dicen_cosas_distintas(
        client, base_datos, db_session):
    """La 'pagada' que dejó el botón Pagar de antes con el tercero debiendo: 100 L ×
    $1.800 = $180.000 contra $300.000, pagado $0, saldo −$120.000. RB4 cambió el texto
    del anticipo a "sin que saliera un peso"; el día de esa quincena sigue diciendo "ya
    se le pagó"."""
    h = auth_headers(client, "admin.a")
    r = client.post(f"{V}/proveedores", json={"nombre": "Dos Verdades", "vereda": "X",
                                              "precio_litro": "1800"}, headers=h)
    assert r.status_code == 201, r.text
    prov = r.json()["id"]
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

    fila = _leer(client, h, liq)
    anticipo = client.get(f"{ANT}/{ant}", headers=h).json()
    recepcion = client.get(f"{REC}/{dia}", headers=h).json()
    put_dia = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    print(f"\n  pagado={fila['pagado']} pagos={fila['pagos']} "
          f"le_queda_debiendo={fila['le_queda_debiendo']}")
    print(f"  anticipo: {anticipo['candado_aviso']!r}")
    print(f"  día:      {recepcion.get('candado_aviso')!r}")
    print(f"  PUT día -> {put_dia.status_code} {_detalle(put_dia)!r}")
    assert D(fila["pagado"]) == 0 and fila["pagos"] == []
    assert "sin que saliera un peso" in anticipo["candado_aviso"]
    # EL DEFECTO: el día de la MISMA quincena dice que ya se le pagó.
    assert "ya se le pagó" in recepcion["candado_aviso"]
    assert put_dia.status_code == 422 and "la leche ya se pagó" in _detalle(put_dia)


# =====================================================================================
# OK. Controles de lo que sí quedó bien.
# =====================================================================================
def test_ok_la_marca_del_anticipo_y_el_guardia_coinciden_en_todas_las_formas_migradas(
        client, base_datos, db_session, monkeypatch):
    """Sobre cuatro formas migradas (sin tocar, +50k pagada, +200k pagada, +200k sin
    pagar): la marca del anticipo trae el mismo texto del 422 y el PUT no mueve nada."""
    h = auth_headers(client, "admin.a")
    filas = _migradas(client, h, db_session, "F Sin Tocar", "F Mas 50", "F Mas 200",
                      "F Arriba")
    _antes_del_guardia(monkeypatch)
    for nombre, litros, pagar in (("F Mas 50", "25", True), ("F Mas 200", "100", True),
                                  ("F Arriba", "100", False)):
        prov, liq_id = filas[nombre]
        _corregir(client, h, liq_id, _dia(client, h, prov, "2026-07-08", litros))
        if pagar:
            assert client.post(f"{API}/{liq_id}/pagar", headers=h).status_code == 200
    monkeypatch.undo()
    campos = ("pagado", "saldo", "anticipos", "valor_total", "estado", "version")
    for nombre, (_, liq_id) in filas.items():
        antes = _leer(client, h, liq_id)
        ant = _anticipo_de(client, h, liq_id)
        put = client.put(f"{ANT}/{ant['id']}", json={"valor": "1000"}, headers=h)
        delete = client.delete(f"{ANT}/{ant['id']}", headers=h)
        assert ant["bloqueado"] is True, nombre
        assert put.status_code == 422 and delete.status_code == 422, nombre
        assert ant["candado_aviso"].replace("modificar ni eliminar", "modificar") == \
            _detalle(put), nombre
        assert ant["candado_aviso"].replace("modificar ni eliminar", "eliminar") == \
            _detalle(delete), nombre
        assert D(antes["deuda_borrada_por_la_migracion"]) == D("120000"), nombre
        despues = _leer(client, h, liq_id)
        assert {k: despues[k] for k in campos} == {k: antes[k] for k in campos}, nombre


def test_ok_resumen_filtra_por_tipo_las_por_reparar(client, base_datos, db_session):
    """Una migrada de leche: `tipo` la separa igual que al listado."""
    h = auth_headers(client, "admin.a")
    _migradas(client, h, db_session, "Tipo Leche")
    todas = _resumen(client, h)
    solo_flete = _resumen(client, h, tipo="transportador")
    solo_leche = _resumen(client, h, tipo="proveedor")
    assert (todas["por_reparar"], todas["deuda_borrada"]) == (1, D("120000"))
    assert (solo_flete["por_reparar"], solo_flete["deuda_borrada"]) == (0, D("0"))
    assert solo_leche == todas


# =====================================================================================
# D5. RB5 a medias: otro guardia que, sobre la fila con deuda borrada, manda a
#     "Corregir esta quincena" (que ahí rebota siempre) y dice "ya se pagó".
# =====================================================================================
def test_d5_observaciones_de_la_migrada_mandan_a_corregir_que_siempre_rebota(
        client, base_datos, db_session, monkeypatch):
    h = auth_headers(client, "admin.a")
    filas = _migradas(client, h, db_session, "Obs Sin Tocar", "Obs Arriba")
    prov, arriba = filas["Obs Arriba"]
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, arriba, _dia(client, h, prov, "2026-07-08", "100"))
    monkeypatch.undo()
    for nombre, (prov, liq_id) in filas.items():
        leida = _leer(client, h, liq_id)
        obs = client.put(f"{API}/{liq_id}", json={"observaciones": "nota"}, headers=h)
        det = leida["detalles"][0]["id"]
        precio = client.put(f"{API}/{liq_id}/detalles/{det}", json={"precio_litro": "2100"},
                            headers=h)
        recalcular = client.post(f"{API}/{liq_id}/recalcular", headers=h)
        corregir = client.post(f"{API}/{liq_id}/corregir/previsualizar", json={
            "motivo": "nota", "recepciones_a_incluir": [
                _dia(client, h, prov, "2026-07-10", "1")]}, headers=h)
        print(f"\n  {nombre} ({leida['estado']} v{leida['version']}, pagos={leida['pagos']})")
        for n, r in (("observaciones", obs), ("precio", precio), ("recalcular", recalcular),
                     ("corregir", corregir)):
            print(f"    {n} -> {r.status_code} {_detalle(r)!r}")
        # EL DEFECTO: el PUT de observaciones manda a "Corregir esta quincena", y
        # corregir rebota por la deuda borrada: un callejón. Y afirma "ya se pagó" en
        # quincenas donde no se le entregó un peso (pagos = []).
        assert obs.status_code == 422 and "Corregir esta" in _detalle(obs)
        assert "ya se pagó" in _detalle(obs) and leida["pagos"] == []
        assert corregir.status_code == 422 and "repararla" in _detalle(corregir)
