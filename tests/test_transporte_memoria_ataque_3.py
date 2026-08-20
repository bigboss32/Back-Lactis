"""EL HALLAZGO: el renglon "ya cobrado" en $0,00 le BORRA el fijo a la memoria.

EL MONTAJE, y es un camino que el proyecto ya tiene escrito y probado:

  1. la ruta "A fabrica" cobra DIA FIJO $150.000. El 16/07 Alex recoge la leche de
     Aurelio (82,00 L) -> comprobante 1 por $150.000, se aprueba y SE PAGA;
  2. alguien anota TARDE la leche de Marleny del MISMO 16/07 (137,45 L). Ese viaje ya
     esta cobrado, asi que entra en $0,00 ("Ya cobrado"), que es lo correcto;
  3. y hay un dia nuevo, el 17/07 con Gilberto (96,30 L) -> $150.000;
  4. comprobante 2 = renglon del 16/07 en $0,00 (ya cobrado) + renglon del 17/07 en
     $150.000. TOTAL EMITIDO $150.000,00. Su memoria dice ('dia_fijo', fijo $150.000).

EL ATAQUE: se apaga el dia 17/07 y se vuelve a prender. Al apagarlo, al comprobante 2 le
queda UN renglon de esa ruta —el de $0,00— y `_guardar_como_cobro_cada_ruta` (con
reemplazar=False) ACTUALIZA la fila de memoria desde ese renglon: un renglon fijo en $0,00
no dice cuanto cuesta el viaje, asi que `valor_dia_fijo` queda en NULO. La memoria se
borro sin que se borrara la fila.

Al prender el dia, la memoria ya no tiene la cifra y manda la tarifa de HOY.
"""
import io
import uuid

import pytest
from sqlalchemy import select

from app.modules.liquidaciones import service as liq_service
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    FIJO, LIQUIDACIONES, NAPOLES, TRANSPORTADORES, D, _escenario,
    _liquidar_flete, _recibir, centavos,
)
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, _ok, _put
from tests.test_transporte_memoria_ataque import (
    CERO, EL_FIJO, LITROS, RUTA, _de_la_ruta, _mem_pinta, _memoria, _papel,
    _pinta, _tarifas,
)

EL_DIA_2 = "2026-07-17"
LITROS_TARDE = D("137.45")
LITROS_OTRO = D("96.30")


def _montar(client, h, esc):
    """Deja armado el comprobante 2 con el renglon en $0,00 y el de $150.000."""
    _tarifas(client, h, esc, FIJO, "dia_fijo")
    _recibir(client, h, esc, EL_DIA, "Aurelio", str(LITROS))
    liq1 = _liquidar_flete(client, h)["id"]
    _ok(client.post(f"{LIQUIDACIONES}/{liq1}/aprobar", headers=h), "aprobar 1")
    _ok(client.post(f"{LIQUIDACIONES}/{liq1}/pagar", headers=h), "pagar 1")
    _recibir(client, h, esc, EL_DIA, "Marleny", str(LITROS_TARDE))
    otro = _recibir(client, h, esc, EL_DIA_2, "Gilberto", str(LITROS_OTRO))
    liq2 = _liquidar_flete(client, h)["id"]
    return liq1, liq2, otro


LAS_TARIFAS_DE_HOY = [
    pytest.param(NAPOLES, "litro", CERO, id="hoy_por_litro__el_dia_vuelve_en_CERO"),
    pytest.param(D("200000"), "dia_fijo", D("200000"), id="hoy_fijo_200k__el_dia_vuelve_en_200k"),
    pytest.param(FIJO, "dia_fijo", EL_FIJO, id="hoy_igual__control_sin_salto"),
]


@pytest.mark.parametrize("tarifa_hoy,modo_hoy,lo_que_vuelve", LAS_TARIFAS_DE_HOY)
def test_y1_apagar_y_prender_con_el_renglon_ya_cobrado(
    client, base_datos, db_session, tarifa_hoy, modo_hoy, lo_que_vuelve
):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    liq1, liq2, otro = _montar(client, h, esc)
    print(f"\n===== Y1 hoy={modo_hoy} ${tarifa_hoy} =====")
    antes = _papel(client, h, liq2)
    _pinta(antes, "1. comprobante 2 emitido")
    _mem_pinta(db_session, liq2, esc, "al emitir")
    emitido = D(antes["valor_transporte"])
    assert emitido == EL_FIJO, f"el montaje tenia que emitir $150.000 y dio ${emitido}"

    _tarifas(client, h, esc, tarifa_hoy, modo_hoy)
    _ok(_put(client, h, otro["id"], estado="inactivo"), "apagar el 17/07")
    medio = _papel(client, h, liq2)
    _pinta(medio, "2. con el 17/07 apagado")
    _mem_pinta(db_session, liq2, esc, "tras apagar")
    _ok(_put(client, h, otro["id"], estado="activo"), "prender el 17/07")
    despues = _papel(client, h, liq2)
    _pinta(despues, "3. con el 17/07 de vuelta")
    _mem_pinta(db_session, liq2, esc, "tras prender")
    quedo = D(despues["valor_transporte"])
    print(f"      emitido ${emitido}  ->  quedo ${quedo}   (salto ${quedo - emitido})")
    # el comprobante 1, que YA SE PAGO, no se puede haber movido
    p1 = _papel(client, h, liq1)
    assert D(p1["valor_transporte"]) == EL_FIJO, "el comprobante PAGADO se movio"
    assert quedo == emitido, (
        f"el comprobante 2 se emitio en ${emitido} y quedo en ${quedo}: "
        f"${quedo - emitido} de diferencia para el conductor"
    )


def test_y2_el_control_sin_la_memoria_escrita_hace_lo_mismo(
    client, base_datos, db_session, monkeypatch
):
    """CONTROL: apagando la memoria escrita (o sea, el codigo de ANTES de este cambio)
    la cifra salta igual. Sirve para decir si el defecto es nuevo o viejo."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    liq1, liq2, otro = _montar(client, h, esc)
    emitido = D(_papel(client, h, liq2)["valor_transporte"])
    print("\n===== Y2 el control: SIN memoria escrita (codigo de antes) =====")
    monkeypatch.setattr(liq_service, "_lo_que_el_comprobante_tiene_escrito", lambda liq: {})
    _tarifas(client, h, esc, NAPOLES, "litro")
    _ok(_put(client, h, otro["id"], estado="inactivo"), "apagar")
    _ok(_put(client, h, otro["id"], estado="activo"), "prender")
    quedo = D(_papel(client, h, liq2)["valor_transporte"])
    _pinta(_papel(client, h, liq2), "sin memoria escrita")
    print(f"      emitido ${emitido} -> quedo ${quedo}  (salto ${quedo - emitido})")


def test_y3_la_otra_puerta_la_ruta_ida_y_vuelta(client, base_datos, db_session):
    """La misma perdida por la OTRA puerta: corregirle la ruta al dia y devolverla."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    liq1, liq2, otro = _montar(client, h, esc)
    antes = _papel(client, h, liq2)
    emitido = D(antes["valor_transporte"])
    print("\n===== Y3 la otra puerta: la ruta ida y vuelta =====")
    _pinta(antes, "1. emitido")
    _mem_pinta(db_session, liq2, esc, "al emitir")
    _tarifas(client, h, esc, NAPOLES, "litro")
    _ok(_put(client, h, otro["id"], ruta_id=esc["napoles"]["id"]), "a napoles")
    _pinta(_papel(client, h, liq2), "2. en napoles")
    _mem_pinta(db_session, liq2, esc, "en napoles")
    _ok(_put(client, h, otro["id"], ruta_id=esc["fabrica"]["id"]), "de vuelta")
    despues = _papel(client, h, liq2)
    _pinta(despues, "3. de vuelta")
    _mem_pinta(db_session, liq2, esc, "de vuelta")
    quedo = D(despues["valor_transporte"])
    print(f"      emitido ${emitido} -> quedo ${quedo}  (salto ${quedo - emitido})")
    assert quedo == emitido, (
        f"emitido ${emitido}, quedo ${quedo}: ${quedo - emitido} de diferencia"
    )


def test_y4_aprobado_se_vuelve_a_aprobar_y_se_paga_la_cifra_nueva(
    client, base_datos, db_session
):
    """El comprobante APROBADO se cae a borrador, se vuelve a aprobar y se paga $0,00."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    liq1, liq2, otro = _montar(client, h, esc)
    _ok(client.post(f"{LIQUIDACIONES}/{liq2}/aprobar", headers=h), "aprobar 2")
    antes = _papel(client, h, liq2)
    emitido = D(antes["valor_transporte"])
    print("\n===== Y4 el papel APROBADO =====")
    _pinta(antes, "1. aprobado")
    _tarifas(client, h, esc, NAPOLES, "litro")
    _ok(_put(client, h, otro["id"], estado="inactivo"), "apagar")
    _ok(_put(client, h, otro["id"], estado="activo"), "prender")
    despues = _papel(client, h, liq2)
    _pinta(despues, "2. de vuelta")
    quedo = D(despues["valor_transporte"])
    print(f"      emitido ${emitido} ({antes['estado']}) -> quedo ${quedo} "
          f"({despues['estado']})")
    r = client.post(f"{LIQUIDACIONES}/{liq2}/aprobar", headers=h)
    print(f"      se vuelve a aprobar: {r.status_code}")
    r = client.post(f"{LIQUIDACIONES}/{liq2}/pagar", headers=h)
    print(f"      se paga: {r.status_code} -> {_papel(client, h, liq2)['estado']}")
    # EL PDF que le queda al conductor
    from pypdf import PdfReader
    contenido = client.get(f"{LIQUIDACIONES}/{liq2}/pdf", headers=h).content
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    junto = " ".join(crudo.split())
    print("      PDF: " + junto[junto.find("Detalle diario"):junto.find("Entregu")][:400])
    assert quedo == emitido, (
        f"emitido ${emitido}, quedo ${quedo}: ${quedo - emitido} de diferencia"
    )


def test_y5_las_fotos_y_el_cuadre_despues_del_salto(client, base_datos, db_session):
    """Aunque la cifra salte, el desglose sigue cuadrando: ninguna red de cuadre lo ve."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    liq1, liq2, otro = _montar(client, h, esc)
    _tarifas(client, h, esc, NAPOLES, "litro")
    _ok(_put(client, h, otro["id"], estado="inactivo"), "apagar")
    _ok(_put(client, h, otro["id"], estado="activo"), "prender")
    papel = _papel(client, h, liq2)
    _pinta(papel, "tras el salto")
    db_session.expire_all()
    fotos = {
        r.proveedor.nombre: D(r.valor_transporte or 0)
        for r in db_session.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.liquidacion_transporte_id == uuid.UUID(liq2),
                RecepcionLeche.deleted_at.is_(None),
            )
        ).all()
    }
    print(f"\n      las fotos: {fotos}  suman ${sum(fotos.values(), CERO)}")
    renglones = sum((D(d["valor"]) for d in papel["detalles"]), CERO)
    print(f"      los renglones suman ${renglones}, el total dice "
          f"${papel['valor_transporte']}")
    assert renglones == D(papel["valor_transporte"])
    assert sum(fotos.values(), CERO) == D(papel["valor_transporte"])
