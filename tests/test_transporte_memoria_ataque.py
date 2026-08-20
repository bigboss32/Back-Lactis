"""ATAQUE A LA MEMORIA DEL PAPEL. Sondas: se imprime, no se exige (todavia)."""
import uuid

import pytest
from sqlalchemy import select

from app.modules.liquidaciones.models import Liquidacion, LiquidacionRuta
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    FIJO, GENERAL, LIQUIDACIONES, NAPOLES, PROVEEDORES, RECEPCIONES,
    TRANSPORTADORES, D, _crear, _escenario, _liquidar_flete, _recibir, centavos,
)
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, _ok, _put

CERO = D(0)
LITROS = D("82.00")
OTRO_DIA = "2026-07-20"
LITROS_NAP = D("50.00")
POR_LITRO = D("19906.32")
EL_FIJO = D("150000")
RUTA = "A fabrica"


def _tarifas(client, h, esc, valor_fabrica, modo_fabrica):
    _ok(client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(valor_fabrica),
             "modo_transporte": modo_fabrica},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": "litro"},
        ]},
        headers=h,
    ), "tarifa")


def _emitir(client, h, esc, modo, con_napoles=True):
    if modo == "dia_fijo":
        _tarifas(client, h, esc, FIJO, "dia_fijo")
        emitido = EL_FIJO
    else:
        _tarifas(client, h, esc, NAPOLES, "litro")
        emitido = POR_LITRO
    dia = _recibir(client, h, esc, EL_DIA, "Aurelio", str(LITROS))
    if con_napoles:
        _recibir(client, h, esc, OTRO_DIA, "Henri", str(LITROS_NAP))
    liq_id = _liquidar_flete(client, h)["id"]
    if modo == "dia_fijo":
        _tarifas(client, h, esc, NAPOLES, "litro")
        hoy = POR_LITRO
    else:
        _tarifas(client, h, esc, FIJO, "dia_fijo")
        hoy = EL_FIJO
    return dia, liq_id, emitido, hoy


def _papel(client, h, liq_id):
    r = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _de_la_ruta(papel, nombre):
    return sum((D(d["valor"]) for d in papel["detalles"] if d["ruta_nombre"] == nombre), CERO)


def _pinta(papel, paso):
    print(f"  {paso}: total ${papel['valor_transporte']}  ({papel['estado']})")
    for d in sorted(papel["detalles"], key=lambda x: (x["fecha"], x["ruta_nombre"] or "")):
        marca = "  YA COBRADO" if d.get("dia_fijo_ya_cobrado") else ""
        print(f"      {d['fecha']}  {(d['ruta_nombre'] or '-'):<10} [{d['modo_transporte']}]"
              f"  {d['litros']} L x ${d['precio_litro']} = ${d['valor']}{marca}")


def _memoria(db_session, liq_id):
    db_session.expire_all()
    return {
        (None if f.ruta_id is None else str(f.ruta_id)): (
            f.modo_transporte,
            None if f.precio_litro is None else D(f.precio_litro),
            None if f.valor_dia_fijo is None else D(f.valor_dia_fijo),
        )
        for f in db_session.scalars(
            select(LiquidacionRuta).where(LiquidacionRuta.liquidacion_id == uuid.UUID(liq_id))
        ).all()
    }


def _mem_pinta(db_session, liq_id, esc, paso):
    nombres = {esc["fabrica"]["id"]: "fabrica", esc["napoles"]["id"]: "napoles", None: "SIN RUTA"}
    print(f"      MEMORIA {paso}:")
    for rid, dice in _memoria(db_session, liq_id).items():
        print(f"        {nombres.get(rid, str(rid)[:8]):<9} -> {dice}")


# ===========================================================================
# X1 - LA FECHA, dentro y fuera del periodo
# ===========================================================================
@pytest.mark.parametrize("modo", ["dia_fijo", "litro"])
@pytest.mark.parametrize("destino,etiqueta", [
    ("2026-07-18", "dentro_del_periodo"),
    ("2026-08-05", "fuera_del_periodo"),
])
def test_x1_la_fecha_ida_y_vuelta(client, base_datos, db_session, modo, destino, etiqueta):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
    print(f"\n===== X1 fecha -> {etiqueta} ({destino}), emitido {modo} =====")
    antes = _papel(client, h, liq_id)
    _pinta(antes, "1. emitido")
    _mem_pinta(db_session, liq_id, esc, "al emitir")

    r1 = _put(client, h, dia["id"], fecha=destino)
    print(f"      ida:    {r1.status_code}")
    medio = _papel(client, h, liq_id)
    _pinta(medio, "2. a mitad")
    _mem_pinta(db_session, liq_id, esc, "a mitad")
    r2 = _put(client, h, dia["id"], fecha=EL_DIA)
    print(f"      vuelta: {r2.status_code}")
    despues = _papel(client, h, liq_id)
    _pinta(despues, "3. de vuelta")
    _mem_pinta(db_session, liq_id, esc, "de vuelta")
    print(f"      emitido ${emitido} | hoy diria ${hoy} | quedo ${_de_la_ruta(despues, RUTA)}")
    db_session.expire_all()
    rec = db_session.get(RecepcionLeche, uuid.UUID(dia["id"]))
    print(f"      la foto de ese dia: ${rec.valor_transporte}   "
          f"comprobante={rec.liquidacion_transporte_id}")


# ===========================================================================
# X2 - EL TRANSPORTADOR, quitado y devuelto (un solo dia de esa ruta)
# ===========================================================================
@pytest.mark.parametrize("modo", ["dia_fijo", "litro"])
def test_x2_transportador_quitado_y_devuelto(client, base_datos, db_session, modo):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    beto = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Beto", "valor_transporte": "100", "modo_transporte": "litro"})
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
    print(f"\n===== X2 transportador ida y vuelta, emitido {modo} =====")
    _pinta(_papel(client, h, liq_id), "1. emitido")
    _mem_pinta(db_session, liq_id, esc, "al emitir")
    print(f"      ida:    {_put(client, h, dia['id'], transportador_id=beto['id']).status_code}")
    _pinta(_papel(client, h, liq_id), "2. a mitad")
    _mem_pinta(db_session, liq_id, esc, "a mitad")
    print(f"      vuelta: {_put(client, h, dia['id'], transportador_id=esc['alex']['id']).status_code}")
    despues = _papel(client, h, liq_id)
    _pinta(despues, "3. de vuelta")
    _mem_pinta(db_session, liq_id, esc, "de vuelta")
    db_session.expire_all()
    rec = db_session.get(RecepcionLeche, uuid.UUID(dia["id"]))
    print(f"      emitido ${emitido} | hoy ${hoy} | quedo ${_de_la_ruta(despues, RUTA)}"
          f" | foto ${rec.valor_transporte} | comprobante={rec.liquidacion_transporte_id}")


# ===========================================================================
# X3 - EL PAPEL SIN NINGUN RENGLON DE NADA, y le vuelve uno
# ===========================================================================
@pytest.mark.parametrize("modo", ["dia_fijo", "litro"])
def test_x3_papel_sin_ningun_renglon(client, base_datos, db_session, modo):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
    nap = db_session.scalars(
        select(RecepcionLeche).where(RecepcionLeche.ruta_id == uuid.UUID(esc["napoles"]["id"]))
    ).all()
    nap_id = str(nap[0].id)
    print(f"\n===== X3 el papel se queda VACIO, emitido {modo} =====")
    _pinta(_papel(client, h, liq_id), "1. emitido")
    _ok(_put(client, h, dia["id"], estado="inactivo"), "apagar fabrica")
    _ok(_put(client, h, nap_id, estado="inactivo"), "apagar napoles")
    vacio = _papel(client, h, liq_id)
    _pinta(vacio, "2. vacio")
    _mem_pinta(db_session, liq_id, esc, "con el papel vacio")
    _ok(_put(client, h, dia["id"], estado="activo"), "prender fabrica")
    uno = _papel(client, h, liq_id)
    _pinta(uno, "3. vuelve fabrica")
    _ok(_put(client, h, nap_id, estado="activo"), "prender napoles")
    dos = _papel(client, h, liq_id)
    _pinta(dos, "4. vuelve napoles")
    _mem_pinta(db_session, liq_id, esc, "al final")
    print(f"      emitido ${emitido} | hoy ${hoy} | quedo ${_de_la_ruta(dos, RUTA)}")


# ===========================================================================
# X4 - BORRAR la recepcion
# ===========================================================================
@pytest.mark.parametrize("modo", ["dia_fijo", "litro"])
def test_x4_borrar_la_recepcion(client, base_datos, db_session, modo):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
    print(f"\n===== X4 borrar la recepcion, emitido {modo} =====")
    _pinta(_papel(client, h, liq_id), "1. emitido")
    r = client.delete(f"{RECEPCIONES}/{dia['id']}", headers=h)
    print(f"      delete: {r.status_code} {r.text[:120]}")
    _pinta(_papel(client, h, liq_id), "2. borrada")
    _mem_pinta(db_session, liq_id, esc, "despues de borrar")
    db_session.expire_all()
    rec = db_session.get(RecepcionLeche, uuid.UUID(dia["id"]))
    print(f"      la recepcion: deleted_at={rec.deleted_at} "
          f"comprobante={rec.liquidacion_transporte_id} foto=${rec.valor_transporte}")


# ===========================================================================
# X5 - LA MEMORIA FANTASMA de la ruta de paso
# ===========================================================================
def test_x5_memoria_fantasma_de_la_ruta_de_paso(client, base_datos, db_session):
    """El dia pasa por Napoles y vuelve: que quedo escrito de Napoles?"""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, "litro", con_napoles=False)
    print("\n===== X5 la ruta de paso =====")
    _pinta(_papel(client, h, liq_id), "1. emitido (solo fabrica)")
    _mem_pinta(db_session, liq_id, esc, "al emitir")
    _ok(_put(client, h, dia["id"], ruta_id=esc["napoles"]["id"]), "a napoles")
    _pinta(_papel(client, h, liq_id), "2. en napoles")
    _mem_pinta(db_session, liq_id, esc, "en napoles")
    _ok(_put(client, h, dia["id"], ruta_id=esc["fabrica"]["id"]), "de vuelta")
    _pinta(_papel(client, h, liq_id), "3. de vuelta")
    _mem_pinta(db_session, liq_id, esc, "de vuelta")
    print(f"      emitido ${emitido} | hoy ${hoy}")


# ===========================================================================
# X6 - EL DIA SIN RUTA (la tarifa general)
# ===========================================================================
def test_x6_el_dia_sin_ruta(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    sin_ruta = _crear(client, h, PROVEEDORES, {
        "nombre": "SinRuta", "vereda": "Ninguna", "precio_litro": "1800"})
    esc["proveedores"]["SinRuta"] = sin_ruta
    _tarifas(client, h, esc, FIJO, "dia_fijo")
    dia = _recibir(client, h, esc, EL_DIA, "SinRuta", "40.00")
    liq_id = _liquidar_flete(client, h)["id"]
    print("\n===== X6 el dia SIN RUTA (tarifa general $200/L) =====")
    _pinta(_papel(client, h, liq_id), "1. emitido")
    _mem_pinta(db_session, liq_id, esc, "al emitir")
    _ok(client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}",
                   json={"valor_transporte": "77000", "modo_transporte": "dia_fijo"},
                   headers=h), "general a dia fijo")
    _ok(_put(client, h, dia["id"], estado="inactivo"), "apagar")
    _pinta(_papel(client, h, liq_id), "2. apagado")
    _mem_pinta(db_session, liq_id, esc, "apagado")
    _ok(_put(client, h, dia["id"], estado="activo"), "prender")
    despues = _papel(client, h, liq_id)
    _pinta(despues, "3. prendido")
    _mem_pinta(db_session, liq_id, esc, "prendido")
    print(f"      emitido 40 L x $200 = ${centavos(D(40) * GENERAL)} | hoy diria $77.000")
