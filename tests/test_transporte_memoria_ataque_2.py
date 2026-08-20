"""ATAQUE A LA MEMORIA DEL PAPEL, segunda tanda. Sondas: se imprime y se mide."""
import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.modules.liquidaciones.models import Liquidacion, LiquidacionRuta
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    FIJO, GENERAL, LIQUIDACIONES, NAPOLES, PROVEEDORES, RECEPCIONES, RUTAS,
    TRANSPORTADORES, D, _crear, _escenario, _liquidar_flete, _recibir, centavos,
)
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, _ok, _put
from tests.test_transporte_memoria_ataque import (
    _de_la_ruta, _emitir, _mem_pinta, _memoria, _papel, _pinta, _tarifas,
    CERO, EL_FIJO, LITROS, POR_LITRO, RUTA,
)


# ===========================================================================
# X7 - LAS DOS RUTAS se quedan sin renglones y vuelven, en los dos ordenes
# ===========================================================================
@pytest.mark.parametrize("orden", ["fabrica_primero", "napoles_primero"])
def test_x7_las_dos_rutas_apagadas_y_prendidas_en_los_dos_ordenes(
    client, base_datos, db_session, orden
):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, "dia_fijo")
    nap_id = str(db_session.scalars(
        select(RecepcionLeche).where(
            RecepcionLeche.ruta_id == uuid.UUID(esc["napoles"]["id"]))
    ).all()[0].id)
    print(f"\n===== X7 las dos rutas, orden {orden} =====")
    antes = _papel(client, h, liq_id)
    _pinta(antes, "1. emitido")
    _ok(_put(client, h, dia["id"], estado="inactivo"), "apagar fabrica")
    _ok(_put(client, h, nap_id, estado="inactivo"), "apagar napoles")
    _pinta(_papel(client, h, liq_id), "2. vacio")
    _mem_pinta(db_session, liq_id, esc, "vacio")
    if orden == "fabrica_primero":
        _ok(_put(client, h, dia["id"], estado="activo"), "prender fabrica")
        _ok(_put(client, h, nap_id, estado="activo"), "prender napoles")
    else:
        _ok(_put(client, h, nap_id, estado="activo"), "prender napoles")
        _ok(_put(client, h, dia["id"], estado="activo"), "prender fabrica")
    despues = _papel(client, h, liq_id)
    _pinta(despues, "3. de vuelta")
    _mem_pinta(db_session, liq_id, esc, "de vuelta")
    assert D(despues["valor_transporte"]) == D(antes["valor_transporte"]), (
        f"el papel se emitio en ${antes['valor_transporte']} y quedo en "
        f"${despues['valor_transporte']}"
    )


# ===========================================================================
# X8 - DOS PAPELES DEL MISMO TRANSPORTADOR, cada uno con su memoria
# ===========================================================================
def test_x8_dos_papeles_del_mismo_transportador(client, base_datos, db_session):
    """Julio por DIA FIJO y agosto POR LITRO. Cada papel conserva SU modo."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _tarifas(client, h, esc, FIJO, "dia_fijo")
    julio = _recibir(client, h, esc, EL_DIA, "Aurelio", str(LITROS))
    liq_julio = _liquidar_flete(client, h)["id"]
    # ahora la ruta pasa a POR LITRO y se liquida agosto
    _tarifas(client, h, esc, NAPOLES, "litro")
    agosto = _recibir(client, h, esc, "2026-08-05", "Marleny", "100.00")
    liq_agosto = _liquidar_flete(client, h, "2026-08-01", "2026-08-31")["id"]
    print("\n===== X8 dos papeles del mismo transportador =====")
    _pinta(_papel(client, h, liq_julio), "julio (dia fijo)")
    _mem_pinta(db_session, liq_julio, esc, "julio")
    _pinta(_papel(client, h, liq_agosto), "agosto (por litro)")
    _mem_pinta(db_session, liq_agosto, esc, "agosto")
    j_antes = D(_papel(client, h, liq_julio)["valor_transporte"])
    a_antes = D(_papel(client, h, liq_agosto)["valor_transporte"])
    # se cruzan las puertas en los dos papeles
    for rid in (julio["id"], agosto["id"]):
        _ok(_put(client, h, rid, estado="inactivo"), "apagar")
        _ok(_put(client, h, rid, estado="activo"), "prender")
    j_desp = _papel(client, h, liq_julio)
    a_desp = _papel(client, h, liq_agosto)
    _pinta(j_desp, "julio de vuelta")
    _pinta(a_desp, "agosto de vuelta")
    assert D(j_desp["valor_transporte"]) == j_antes, "julio se movio"
    assert D(a_desp["valor_transporte"]) == a_antes, "agosto se movio"
    assert j_antes == EL_FIJO
    assert a_antes == centavos(D(100) * NAPOLES)


# ===========================================================================
# X9 - UN COMPROBANTE DE LOS QUE YA EXISTIAN (sin memoria) y el backfill
# ===========================================================================
def _migracion():
    ruta = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions"
        / "c3f8a1d6b0e5_el_comprobante_guarda_como_cobro_cada_ruta.py"
    )
    spec = importlib.util.spec_from_file_location("mig_memoria", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.parametrize("modo", ["dia_fijo", "litro"])
@pytest.mark.parametrize("con_backfill", [False, True])
def test_x9_comprobante_viejo_sin_memoria(
    client, base_datos, db_session, modo, con_backfill
):
    """Se le BORRA la memoria al papel (como los que ya existen en la base del cliente).

    Con el backfill de la migración tiene que comportarse igual que uno nuevo.
    Sin backfill se ve lo que la red de abajo (la deducción) alcanza a salvar.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
    print(f"\n===== X9 papel viejo, emitido {modo}, backfill={con_backfill} =====")
    antes = _papel(client, h, liq_id)
    _pinta(antes, "1. emitido")
    # LA BASE VIEJA: se le bota la memoria
    db_session.query(LiquidacionRuta).delete()
    db_session.flush()
    db_session.commit()
    print(f"      memoria borrada: {_memoria(db_session, liq_id)}")
    if con_backfill:
        mig = _migracion()
        filas = mig.backfill_de_la_memoria(db_session.connection())
        db_session.commit()
        print(f"      backfill escribio {filas} filas")
        _mem_pinta(db_session, liq_id, esc, "tras el backfill")
    # y ahora las dos puertas
    _ok(_put(client, h, dia["id"], estado="inactivo"), "apagar")
    _ok(_put(client, h, dia["id"], estado="activo"), "prender")
    despues = _papel(client, h, liq_id)
    _pinta(despues, "2. tras apagar y prender")
    print(f"      emitido ${emitido} | hoy ${hoy} | quedo ${_de_la_ruta(despues, RUTA)}")
    if con_backfill:
        assert _de_la_ruta(despues, RUTA) == emitido, (
            f"con el backfill el papel viejo tenia que seguir en ${emitido}"
        )


# ===========================================================================
# X10 - ANULAR Y REGENERAR con el modo cambiado en medio
# ===========================================================================
@pytest.mark.parametrize("modo", ["dia_fijo", "litro"])
def test_x10_anular_y_regenerar_con_el_modo_cambiado(client, base_datos, db_session, modo):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
    print(f"\n===== X10 anular y regenerar, emitido {modo} =====")
    _pinta(_papel(client, h, liq_id), "1. emitido")
    _mem_pinta(db_session, liq_id, esc, "al emitir")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/anular", headers=h), "anular")
    anulado = _papel(client, h, liq_id)
    _pinta(anulado, "2. anulado")
    _mem_pinta(db_session, liq_id, esc, "anulado")
    nuevo_id = _liquidar_flete(client, h)["id"]
    nuevo = _papel(client, h, nuevo_id)
    _pinta(nuevo, "3. regenerado")
    _mem_pinta(db_session, nuevo_id, esc, "regenerado")
    print(f"      emitido ${emitido} | hoy ${hoy} | regenerado "
          f"${_de_la_ruta(nuevo, RUTA)}")
    assert _de_la_ruta(nuevo, RUTA) == hoy, "regenerar cobra lo de HOY"
    # y la puerta sobre el papel nuevo conserva lo nuevo
    _ok(_put(client, h, dia["id"], estado="inactivo"), "apagar")
    _ok(_put(client, h, dia["id"], estado="activo"), "prender")
    final = _papel(client, h, nuevo_id)
    _pinta(final, "4. tras la puerta")
    assert _de_la_ruta(final, RUTA) == hoy
    # el papel ANULADO no se movio
    assert D(_papel(client, h, liq_id)["valor_transporte"]) == D(anulado["valor_transporte"])


# ===========================================================================
# X11 - LA DEGRADACION: el renglon "ya cobrado" en $0 le borra el fijo a la memoria
# ===========================================================================
def test_x11_el_renglon_ya_cobrado_en_cero_le_borra_el_fijo_a_la_memoria(
    client, base_datos, db_session
):
    """EL ATAQUE. Un papel con DOS renglones de la misma ruta fija: uno de $150.000 y
    uno de $0,00 ("ya cobrado" en otro comprobante). Se apaga el que vale plata: al
    papel le queda solo el de $0,00, y la memoria se re-escribe DESDE ESE renglon.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _tarifas(client, h, esc, FIJO, "dia_fijo")
    # PAPEL 1: el 16/07 con Aurelio, se aprueba y SE PAGA -> el viaje del 16/07 queda cobrado
    _recibir(client, h, esc, EL_DIA, "Aurelio", str(LITROS))
    liq1 = _liquidar_flete(client, h)["id"]
    _ok(client.post(f"{LIQUIDACIONES}/{liq1}/aprobar", headers=h), "aprobar 1")
    _ok(client.post(f"{LIQUIDACIONES}/{liq1}/pagar", headers=h), "pagar 1")
    print("\n===== X11 el renglon 'ya cobrado' en $0 =====")
    _pinta(_papel(client, h, liq1), "papel 1 (pagado)")
    # LECHE ANOTADA TARDE del MISMO 16/07 (el viaje ya se cobro) + un dia nuevo 17/07
    tarde = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    otro = _recibir(client, h, esc, "2026-07-17", "Gilberto", "96.30")
    liq2 = _liquidar_flete(client, h)["id"]
    p2 = _papel(client, h, liq2)
    _pinta(p2, "papel 2 (recien emitido)")
    _mem_pinta(db_session, liq2, esc, "papel 2 al emitir")
    total_antes = D(p2["valor_transporte"])

    # LA TARIFA DE HOY PASA A POR LITRO (legitimo: el dueño renegocio)
    _tarifas(client, h, esc, NAPOLES, "litro")
    # SE APAGA EL DIA QUE VALE $150.000. Al papel 2 le queda solo el renglon de $0,00.
    _ok(_put(client, h, otro["id"], estado="inactivo"), "apagar el 17/07")
    medio = _papel(client, h, liq2)
    _pinta(medio, "papel 2 con el 17/07 apagado")
    _mem_pinta(db_session, liq2, esc, "tras apagar")
    # Y SE VUELVE A PRENDER
    _ok(_put(client, h, otro["id"], estado="activo"), "prender el 17/07")
    despues = _papel(client, h, liq2)
    _pinta(despues, "papel 2 con el 17/07 de vuelta")
    _mem_pinta(db_session, liq2, esc, "tras prender")
    print(f"      el papel 2 se emitio en ${total_antes} y quedo en "
          f"${despues['valor_transporte']}")
    assert D(despues["valor_transporte"]) == total_antes, (
        f"el papel 2 se emitio en ${total_antes} y quedo en "
        f"${despues['valor_transporte']}: la memoria perdio el fijo"
    )


# ===========================================================================
# X12 - RECALCULAR DESPUES DE CADA COSA: lleva al modo de hoy y cuadra
# ===========================================================================
@pytest.mark.parametrize("modo", ["dia_fijo", "litro"])
def test_x12_recalcular_despues_de_cada_cosa(client, base_datos, db_session, modo):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
    nap_id = str(db_session.scalars(
        select(RecepcionLeche).where(
            RecepcionLeche.ruta_id == uuid.UUID(esc["napoles"]["id"]))
    ).all()[0].id)
    print(f"\n===== X12 recalcular tras cada cosa, emitido {modo} =====")
    pasos = [
        ("apagar", lambda: _put(client, h, dia["id"], estado="inactivo")),
        ("prender", lambda: _put(client, h, dia["id"], estado="activo")),
        ("a napoles", lambda: _put(client, h, dia["id"], ruta_id=esc["napoles"]["id"])),
        ("de vuelta", lambda: _put(client, h, dia["id"], ruta_id=esc["fabrica"]["id"])),
        ("mover fecha", lambda: _put(client, h, dia["id"], fecha="2026-07-19")),
        ("volver fecha", lambda: _put(client, h, dia["id"], fecha=EL_DIA)),
        ("mas litros", lambda: _put(client, h, dia["id"], cantidad_litros="91.30")),
    ]
    for nombre, accion in pasos:
        _ok(accion(), nombre)
        _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h), "recalcular")
        papel = _papel(client, h, liq_id)
        _pinta(papel, f"tras «{nombre}» + recalcular")
        suma = sum((D(d["valor"]) for d in papel["detalles"]), CERO)
        assert suma == D(papel["valor_transporte"]), (
            f"tras «{nombre}» los renglones suman ${suma} y el total dice "
            f"${papel['valor_transporte']}"
        )
        db_session.expire_all()
        fotos = sum(
            (D(r.valor_transporte or 0) for r in db_session.scalars(
                select(RecepcionLeche).where(
                    RecepcionLeche.liquidacion_transporte_id == uuid.UUID(liq_id),
                    RecepcionLeche.deleted_at.is_(None),
                    RecepcionLeche.estado == "activo",
                )).all()),
            CERO,
        )
        assert fotos == D(papel["valor_transporte"]), (
            f"tras «{nombre}» las fotos suman ${fotos} y el papel dice "
            f"${papel['valor_transporte']}"
        )
    final = _papel(client, h, liq_id)
    litros_hoy = D("91.30")
    esperado = EL_FIJO if hoy == EL_FIJO else centavos(litros_hoy * NAPOLES)
    print(f"      al final la ruta vale ${_de_la_ruta(final, RUTA)} y hoy diria ${esperado}")
    assert _de_la_ruta(final, RUTA) == esperado


# ===========================================================================
# X13 - EL CANDADO: con plata ya salida nada se mueve; bonificaciones SI
# ===========================================================================
@pytest.mark.parametrize("modo", ["dia_fijo", "litro"])
def test_x13_el_candado_con_plata_salida(client, base_datos, db_session, modo):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia, liq_id, emitido, hoy = _emitir(client, h, esc, modo)
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h), "aprobar")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h), "pagar")
    antes = _papel(client, h, liq_id)
    mem_antes = _memoria(db_session, liq_id)
    print(f"\n===== X13 candado, emitido {modo} =====")
    _pinta(antes, "1. pagado")
    for nombre, campos in (
        ("apagar", {"estado": "inactivo"}),
        ("cambiar la ruta", {"ruta_id": esc["napoles"]["id"]}),
        ("cambiar los litros", {"cantidad_litros": "91.30"}),
        ("cambiar la fecha", {"fecha": "2026-07-19"}),
        ("cambiar el transportador", {"transportador_id": None}),
    ):
        r = _put(client, h, dia["id"], **campos)
        print(f"      {nombre}: {r.status_code}")
        assert r.status_code == 422, f"«{nombre}» tenia que rebotar y dio {r.status_code}"
    # BONIFICACIONES SI SE PUEDEN, y es correcto: son plata de la LECHE
    r = _put(client, h, dia["id"], bonificaciones="5000")
    print(f"      bonificaciones: {r.status_code}")
    assert r.status_code == 200, f"las bonificaciones si se pueden: {r.text}"
    despues = _papel(client, h, liq_id)
    _pinta(despues, "2. tras la bonificacion")
    assert D(despues["valor_transporte"]) == D(antes["valor_transporte"])
    assert despues["estado"] == antes["estado"]
    assert _memoria(db_session, liq_id) == mem_antes, "la memoria del papel pagado se movio"
    # y recalcular sobre un pagado tiene que rebotar
    r = client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)
    print(f"      recalcular un pagado: {r.status_code}")
