"""ATAQUE A LA MEMORIA, cuarta tanda: el candado, dos queseras, idempotencia y el
comprobante de proveedor."""
import uuid

import pytest
from sqlalchemy import select

from app.modules.liquidaciones.models import Liquidacion, LiquidacionRuta
from app.modules.recepcion.models import RecepcionLeche
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
from tests.test_transporte_memoria_ataque_3 import _montar


# ===========================================================================
# Z1 - EL CANDADO sobre el escenario del renglon "ya cobrado"
# ===========================================================================
@pytest.mark.parametrize("estado", ["pagada", "abono"])
def test_z1_el_candado_tapa_el_escenario_del_ya_cobrado(
    client, base_datos, db_session, estado
):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    liq1, liq2, otro = _montar(client, h, esc)
    _ok(client.post(f"{LIQUIDACIONES}/{liq2}/aprobar", headers=h), "aprobar 2")
    if estado == "pagada":
        _ok(client.post(f"{LIQUIDACIONES}/{liq2}/pagar", headers=h), "pagar 2")
    else:
        _ok(client.post(f"{LIQUIDACIONES}/{liq2}/pagos",
                        json={"fecha": EL_DIA, "valor": "1000.00"}, headers=h), "abono")
    antes = _papel(client, h, liq2)
    mem_antes = _memoria(db_session, liq2)
    print(f"\n===== Z1 candado con el papel 2 en {estado} =====")
    _pinta(antes, "1. antes")
    _tarifas(client, h, esc, NAPOLES, "litro")
    r1 = _put(client, h, otro["id"], estado="inactivo")
    r2 = _put(client, h, otro["id"], estado="activo")
    print(f"      apagar: {r1.status_code}   prender: {r2.status_code}")
    despues = _papel(client, h, liq2)
    _pinta(despues, "2. despues")
    assert r1.status_code == 422, "con plata afuera apagar tenia que rebotar"
    assert D(despues["valor_transporte"]) == D(antes["valor_transporte"])
    assert despues["estado"] == antes["estado"]
    assert _memoria(db_session, liq2) == mem_antes, "la memoria se movio con plata afuera"


# ===========================================================================
# Z2 - RECALCULAR ES IDEMPOTENTE despues del salto
# ===========================================================================
def test_z2_recalcular_tres_veces_despues_del_salto(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    liq1, liq2, otro = _montar(client, h, esc)
    _tarifas(client, h, esc, NAPOLES, "litro")
    _ok(_put(client, h, otro["id"], estado="inactivo"), "apagar")
    _ok(_put(client, h, otro["id"], estado="activo"), "prender")
    print("\n===== Z2 recalcular tres veces despues del salto =====")
    _pinta(_papel(client, h, liq2), "tras el salto")
    formas = []
    for i in (1, 2, 3):
        _ok(client.post(f"{LIQUIDACIONES}/{liq2}/recalcular", headers=h), "recalcular")
        papel = _papel(client, h, liq2)
        _pinta(papel, f"recalculo {i}")
        _mem_pinta(db_session, liq2, esc, f"recalculo {i}")
        formas.append(sorted(
            (d["fecha"], d["ruta_nombre"], d["modo_transporte"], D(d["litros"]),
             D(d["precio_litro"]), D(d["valor"])) for d in papel["detalles"]))
        suma = sum((D(d["valor"]) for d in papel["detalles"]), CERO)
        assert suma == D(papel["valor_transporte"])
    assert formas[0] == formas[1] == formas[2], "recalcular no es idempotente"
    # el comprobante 1 (PAGADO) sigue quieto
    assert D(_papel(client, h, liq1)["valor_transporte"]) == EL_FIJO


# ===========================================================================
# Z3 - DOS QUESERAS: la memoria de una no le contesta a la otra
# ===========================================================================
def test_z3_dos_queseras(client, base_datos, db_session):
    print("\n===== Z3 dos queseras =====")
    resultados = {}
    for usuario, modo in (("admin.a", "dia_fijo"), ("admin.b", "litro")):
        h = auth_headers(client, usuario)
        esc = _escenario(client, h)
        dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
        antes = _papel(client, h, liq_id)
        _pinta(antes, f"{usuario} emitido {modo}")
        _ok(_put(client, h, dia["id"], estado="inactivo"), "apagar")
        _ok(_put(client, h, dia["id"], estado="activo"), "prender")
        despues = _papel(client, h, liq_id)
        _pinta(despues, f"{usuario} de vuelta")
        resultados[usuario] = (D(antes["valor_transporte"]), D(despues["valor_transporte"]),
                               emitido, _de_la_ruta(despues, RUTA))
        _mem_pinta(db_session, liq_id, esc, usuario)
    for usuario, (a, b, emitido, de_la_ruta) in resultados.items():
        print(f"      {usuario}: ${a} -> ${b}   (la ruta: ${de_la_ruta}, emitida ${emitido})")
        assert a == b, f"{usuario} se movio"
        assert de_la_ruta == emitido


# ===========================================================================
# Z4 - EL COMPROBANTE DE PROVEEDOR no puede quedar con memoria de rutas
# ===========================================================================
def test_z4_el_comprobante_de_proveedor_no_lleva_memoria(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _emitir(client, h, esc, "dia_fijo")
    r = client.post(f"{LIQUIDACIONES}/generar",
                    json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
                          "tipo": "proveedor"}, headers=h)
    assert r.status_code in (200, 201), r.text
    print("\n===== Z4 el comprobante de proveedor =====")
    db_session.expire_all()
    for liq in db_session.scalars(select(Liquidacion)).all():
        filas = db_session.scalars(
            select(LiquidacionRuta).where(LiquidacionRuta.liquidacion_id == liq.id)).all()
        print(f"      {liq.tipo:<14} {liq.estado:<9} filas de memoria: {len(filas)}")
        if liq.tipo == "proveedor":
            assert not filas, "una liquidacion de proveedor quedo con memoria de rutas"


# ===========================================================================
# Z5 - LA FILA SIN RUTA no se puede duplicar
# ===========================================================================
def test_z5_la_fila_sin_ruta_no_se_duplica(client, base_datos, db_session):
    from tests.test_transporte_dia_fijo import PROVEEDORES, _crear
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    esc["proveedores"]["SinRuta"] = _crear(client, h, PROVEEDORES, {
        "nombre": "SinRuta", "vereda": "Ninguna", "precio_litro": "1800"})
    esc["proveedores"]["SinRuta2"] = _crear(client, h, PROVEEDORES, {
        "nombre": "SinRuta2", "vereda": "Ninguna", "precio_litro": "1800"})
    _tarifas(client, h, esc, FIJO, "dia_fijo")
    a = _recibir(client, h, esc, EL_DIA, "SinRuta", "40.00")
    b = _recibir(client, h, esc, "2026-07-17", "SinRuta2", "30.00")
    liq_id = _liquidar_flete(client, h)["id"]
    print("\n===== Z5 la fila SIN RUTA =====")
    _pinta(_papel(client, h, liq_id), "emitido")
    _mem_pinta(db_session, liq_id, esc, "al emitir")
    for _ in range(3):
        _ok(_put(client, h, a["id"], estado="inactivo"), "apagar a")
        _ok(_put(client, h, b["id"], estado="inactivo"), "apagar b")
        _ok(_put(client, h, a["id"], estado="activo"), "prender a")
        _ok(_put(client, h, b["id"], estado="activo"), "prender b")
    _pinta(_papel(client, h, liq_id), "tras tres vueltas")
    db_session.expire_all()
    filas = db_session.scalars(
        select(LiquidacionRuta).where(
            LiquidacionRuta.liquidacion_id == uuid.UUID(liq_id))).all()
    print(f"      filas de memoria: {[(f.ruta_id, f.modo_transporte, f.precio_litro, f.valor_dia_fijo) for f in filas]}")
    assert len(filas) == 1, "la fila SIN RUTA se duplico"
