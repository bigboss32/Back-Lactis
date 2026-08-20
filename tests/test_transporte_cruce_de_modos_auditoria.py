"""AUDITORÍA EXTERNA DEL CRUCE DE MODOS: pagarle de menos o de más al conductor.

No arregla nada: mide. Después de cada operación se miran las TRES cifras a la vez —el
comprobante emitido, la suma de las fotos de sus recepciones, y lo que imprime el PDF—
y se compara contra la cuenta hecha a mano.

Lo que se ataca acá y que la matriz que ya existe no cubre:
  · cambiar el modo DOS y TRES veces seguidas, antes y después de emitir;
  · cambiarle el modo a la tarifa GENERAL mientras la ruta tiene la suya, y al revés;
  · QUITARLE la tarifa propia a la ruta para que caiga en la general, con los modos
    cruzados;
  · una recepción que sale del día y vuelve, y una que se va a otra quincena y vuelve;
  · dos rutas con modos distintos el mismo día, y el día sin ruta;
  · anular y regenerar con el modo cambiado en medio;
  · Recalcular después de cada cosa;
  · y el candado, con plata ya salida.
"""
import io
import uuid
from decimal import ROUND_HALF_UP, Decimal

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.modules.liquidaciones.models import Liquidacion
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQUIDACIONES = "/api/v1/liquidaciones"

FIJO = Decimal("150000")
POR_LITRO = Decimal("242.76")
NAPOLES = Decimal("180.00")
GENERAL_LITRO = Decimal("95.00")
GENERAL_FIJO = Decimal("77000")

EL_DIA = "2026-07-16"
OTRO_DIA = "2026-07-17"
FUERA = "2026-07-02"          # otra quincena
LITRO, DIA_FIJO = "litro", "dia_fijo"
CERO = Decimal("0")


def D(v):
    return Decimal(str(v))


def cent(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _post(client, h, url, cuerpo):
    r = client.post(url, json=cuerpo, headers=h)
    assert r.status_code in (200, 201), f"{url}: {r.status_code} {r.text}"
    return r.json()


def texto_pdf(contenido: bytes) -> str:
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


# ---------------------------------------------------------------- el escenario
def escenario(client, h, sufijo, *, modo_fabrica=LITRO, modo_general=LITRO,
              con_tarifa_de_ruta=True):
    fabrica = _post(client, h, RUTAS, {"nombre": f"A fabrica{sufijo}", "municipio": "Granada"})
    napoles = _post(client, h, RUTAS, {"nombre": f"Napoles{sufijo}", "municipio": "Granada"})
    rutas = [{"ruta_id": napoles["id"], "modo_transporte": LITRO,
              "valor_transporte": str(NAPOLES)}]
    if con_tarifa_de_ruta:
        rutas.insert(0, {"ruta_id": fabrica["id"], "modo_transporte": modo_fabrica,
                         "valor_transporte": str(FIJO if modo_fabrica == DIA_FIJO else POR_LITRO)})
    alex = _post(client, h, TRANSPORTADORES, {
        "nombre": f"Alex{sufijo}",
        "valor_transporte": str(GENERAL_FIJO if modo_general == DIA_FIJO else GENERAL_LITRO),
        "modo_transporte": modo_general,
        "rutas": rutas,
    })
    beto = _post(client, h, TRANSPORTADORES, {
        "nombre": f"Beto{sufijo}", "valor_transporte": "310.15", "modo_transporte": LITRO})
    provs = {
        n: _post(client, h, PROVEEDORES, {
            "nombre": f"{n}{sufijo}", "vereda": "La Vega", "precio_litro": "1800",
            "ruta_id": fabrica["id"]})
        for n in ("Aurelio", "Marleny", "Gilberto", "Rosa")
    }
    return {"fabrica": fabrica, "napoles": napoles, "alex": alex, "beto": beto,
            "provs": provs, "sufijo": sufijo}


def poner_tarifas(client, h, esc, *, modo_fabrica=None, valor_fabrica=None,
                  modo_general=LITRO, valor_general=None, con_fabrica=True,
                  modo_napoles=LITRO, valor_napoles=NAPOLES):
    rutas = []
    if con_fabrica:
        rutas.append({"ruta_id": esc["fabrica"]["id"], "modo_transporte": modo_fabrica,
                      "valor_transporte": str(
                          valor_fabrica if valor_fabrica is not None
                          else (FIJO if modo_fabrica == DIA_FIJO else POR_LITRO))})
    rutas.append({"ruta_id": esc["napoles"]["id"], "modo_transporte": modo_napoles,
                  "valor_transporte": str(valor_napoles)})
    if valor_general is None:
        valor_general = GENERAL_FIJO if modo_general == DIA_FIJO else GENERAL_LITRO
    r = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", headers=h, json={
        "valor_transporte": str(valor_general),
        "modo_transporte": modo_general,
        "rutas": rutas})
    assert r.status_code == 200, r.text
    return r.json()


def recibir(client, h, esc, fecha, quien, litros, ruta_id=None, transportador=None):
    cuerpo = {"fecha": fecha, "proveedor_id": esc["provs"][quien]["id"],
              "transportador_id": transportador or esc["alex"]["id"],
              "cantidad_litros": str(litros)}
    if ruta_id is not None:
        cuerpo["ruta_id"] = ruta_id
    return _post(client, h, RECEPCIONES, cuerpo)


def poner(client, h, rec_id, **campos):
    r = client.put(f"{RECEPCIONES}/{rec_id}", json=campos, headers=h)
    return r


def liquidar(client, h, esc, inicio="2026-07-16", fin="2026-07-31"):
    r = client.post(f"{LIQUIDACIONES}/generar", headers=h, json={
        "periodo_inicio": inicio, "periodo_fin": fin, "tipo": "transportador"})
    assert r.status_code in (200, 201), r.text
    mias = [g for g in r.json()["generadas"] if g.get("transportador_id") == esc["alex"]["id"]]
    assert mias, f"sin comprobante de Alex: {r.json()}"
    return mias[0]


# ---------------------------------------------------------------- radiografía
class Papel:
    def __init__(self, db, liq_id):
        db.expire_all()
        self.liq = db.get(Liquidacion, uuid.UUID(str(liq_id)))
        self.renglones = [
            (d.fecha, d.ruta_id, D(d.litros), D(d.precio_litro), D(d.valor),
             d.modo_transporte, bool(d.dia_fijo_ya_cobrado))
            for d in self.liq.detalles if d.deleted_at is None]
        self.fotos = {
            r.id: (D(r.cantidad_litros), D(r.valor_transporte), r.estado)
            for r in db.scalars(select(RecepcionLeche).where(
                RecepcionLeche.liquidacion_transporte_id == self.liq.id,
                RecepcionLeche.deleted_at.is_(None))).all()}

    @property
    def total(self):
        return D(self.liq.valor_transporte)

    @property
    def suma_renglones(self):
        return sum((v for *_, v, _, _ in ((r[0], r[1], r[2], r[3], r[4], r[5], r[6])
                                          for r in self.renglones)), CERO)

    @property
    def suma_fotos(self):
        return sum((v for _, v, e in self.fotos.values() if e == "activo"), CERO)

    @property
    def modos(self):
        return sorted({r[5] for r in self.renglones})

    def __str__(self):
        ls = " | ".join(f"{f} {li}L x ${p} = ${v} [{m}{' YA' if yc else ''}]"
                        for f, _, li, p, v, m, yc in sorted(self.renglones,
                                                            key=lambda x: (x[0], x[4])))
        return f"${self.total} = {ls or '(vacio)'}"


def cuadra(db, liq_id, paso):
    """La regla de oro: total == suma de renglones == suma de fotos vivas."""
    p = Papel(db, liq_id)
    sr = sum((r[4] for r in p.renglones), CERO)
    print(f"    {paso:<52}{p}")
    assert p.total == sr, f"[{paso}] total ${p.total} != renglones ${sr}"
    assert p.suma_fotos == sr, f"[{paso}] fotos ${p.suma_fotos} != renglones ${sr}"
    return p


def sueltas_de(db, esc):
    db.expire_all()
    return list(db.scalars(select(RecepcionLeche).where(
        RecepcionLeche.liquidacion_transporte_id.is_(None),
        RecepcionLeche.deleted_at.is_(None))).all())


def pdf_de(client, h, liq_id):
    r = client.get(f"{LIQUIDACIONES}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    return texto_pdf(r.content)


# ===========================================================================
# 1. EL MODO CAMBIADO DOS Y TRES VECES SEGUIDAS
# ===========================================================================
@pytest.mark.parametrize("secuencia", [
    [LITRO, DIA_FIJO],
    [LITRO, DIA_FIJO, LITRO],
    [LITRO, DIA_FIJO, LITRO, DIA_FIJO],
    [DIA_FIJO, LITRO, DIA_FIJO],
])
def test_modo_cambiado_varias_veces_antes_de_emitir(client, base_datos, db_session, secuencia):
    """Cambiar el modo N veces ANTES de emitir: manda el ÚLTIMO, ni más ni menos."""
    h = auth_headers(client, "admin.a")
    suf = " s" + "".join(m[0] for m in secuencia)
    esc = escenario(client, h, suf, modo_fabrica=secuencia[0])
    recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    for modo in secuencia[1:]:
        poner_tarifas(client, h, esc, modo_fabrica=modo)
    final = secuencia[-1]
    esperado = FIJO if final == DIA_FIJO else cent(D("219.45") * POR_LITRO)
    # las fotos sueltas, ya rehechas por el cambio de modo
    db_session.expire_all()
    fotos = sum((D(r.valor_transporte) for r in sueltas_de(db_session, esc)), CERO)
    print(f"\n  secuencia {secuencia} -> fotos sueltas ${fotos} (esperado ${esperado})")
    assert fotos == esperado, f"las fotos sueltas suman ${fotos}, el modo de hoy dice ${esperado}"
    liq = liquidar(client, h, esc)
    p = cuadra(db_session, liq["id"], f"emitido tras {secuencia}")
    assert p.total == esperado
    assert p.modos == [final]


@pytest.mark.parametrize("secuencia", [
    [DIA_FIJO],
    [DIA_FIJO, LITRO],
    [DIA_FIJO, LITRO, DIA_FIJO],
])
def test_modo_cambiado_varias_veces_despues_de_emitir(client, base_datos, db_session, secuencia):
    """Emitido POR LITRO; se le cambia el modo N veces y después se corrige un dato.

    El papel emitido tiene que seguir diciendo lo suyo, en el modo en que se emitió, con
    los litros de hoy. Recalcular es el único que lo lleva al modo de hoy.
    """
    h = auth_headers(client, "admin.a")
    suf = " d" + "".join(m[0] for m in secuencia)
    esc = escenario(client, h, suf, modo_fabrica=LITRO)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    emitido = cent(D("219.45") * POR_LITRO)
    p = cuadra(db_session, liq["id"], "emitido por litro")
    assert p.total == emitido == D("53273.68")

    for modo in secuencia:
        poner_tarifas(client, h, esc, modo_fabrica=modo)
    r = poner(client, h, a["id"], cantidad_litros="91.30")
    assert r.status_code == 200, r.text
    p = cuadra(db_session, liq["id"], f"tras {secuencia} + corregir litros")
    esperado = cent(D("228.75") * POR_LITRO)
    assert p.modos == [LITRO], f"el papel cambio de forma: {p.modos}"
    assert p.total == esperado, (
        f"el papel emitido por litro quedo en ${p.total} y por sus litros de hoy "
        f"(228,75 L x $242,76) vale ${esperado}")
    impreso = pdf_de(client, h, liq["id"])
    assert "242,76" in impreso, "el PDF no imprime la tarifa con que se emitio"

    # Recalcular lleva al modo de hoy
    rr = client.post(f"{LIQUIDACIONES}/{liq['id']}/recalcular", headers=h)
    assert rr.status_code == 200, rr.text
    q = cuadra(db_session, liq["id"], "+ RECALCULAR")
    hoy = secuencia[-1]
    assert q.modos == [hoy], f"Recalcular dejo {q.modos} y hoy la ruta es '{hoy}'"
    assert q.total == (FIJO if hoy == DIA_FIJO else cent(D("228.75") * POR_LITRO))


# ===========================================================================
# 2. LA TARIFA GENERAL Y LA DE LA RUTA, CRUZADAS
# ===========================================================================
def test_cambiar_el_modo_general_no_toca_los_dias_de_una_ruta_con_tarifa_propia(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " gen1", modo_fabrica=LITRO, modo_general=LITRO)
    recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    antes = {r.id: D(r.valor_transporte) for r in sueltas_de(db_session, esc)}
    poner_tarifas(client, h, esc, modo_fabrica=LITRO, modo_general=DIA_FIJO)
    despues = {r.id: D(r.valor_transporte) for r in sueltas_de(db_session, esc)}
    print(f"\n  general litro->fijo con la ruta propia por litro: {list(antes.values())} "
          f"-> {list(despues.values())}")
    assert antes == despues, (
        "cambiarle el modo a la tarifa GENERAL le movio el flete a dias de una ruta que "
        "tiene tarifa propia")


def test_el_dia_sin_ruta_sigue_a_la_general_al_cruzar_el_modo(client, base_datos, db_session):
    """Un día SIN ruta se cobra con la general. Emitido por litro y la general pasa a fijo."""
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " sr1", modo_fabrica=LITRO, modo_general=LITRO)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00", ruta_id=None)
    # el proveedor trae la ruta; se la quitamos explicitamente
    r = poner(client, h, a["id"], ruta_id=None)
    assert r.status_code == 200, r.text
    b = recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    poner(client, h, b["id"], ruta_id=None)
    db_session.expire_all()
    fotos = sum((D(x.valor_transporte) for x in sueltas_de(db_session, esc)), CERO)
    assert fotos == cent(D("219.45") * GENERAL_LITRO), fotos
    liq = liquidar(client, h, esc)
    p = cuadra(db_session, liq_id := liq["id"], "emitido sin ruta, general por litro")
    assert p.total == cent(D("219.45") * GENERAL_LITRO)
    assert p.modos == [LITRO]

    poner_tarifas(client, h, esc, modo_fabrica=LITRO, modo_general=DIA_FIJO)
    r = poner(client, h, a["id"], cantidad_litros="91.30")
    assert r.status_code == 200, r.text
    p = cuadra(db_session, liq_id, "general a fijo + corregir litros")
    assert p.modos == [LITRO], f"el papel sin ruta cambio de forma: {p.modos}"
    assert p.total == cent(D("228.75") * GENERAL_LITRO), p.total
    rr = client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)
    assert rr.status_code == 200, rr.text
    q = cuadra(db_session, liq_id, "+ RECALCULAR")
    assert q.modos == [DIA_FIJO] and q.total == GENERAL_FIJO, str(q)


def test_quitarle_la_tarifa_propia_a_la_ruta_la_manda_a_la_general_con_otro_modo(
        client, base_datos, db_session):
    """La ruta era DÍA FIJO $150.000; se le quita la fila y cae en la general POR LITRO."""
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " qt1", modo_fabrica=DIA_FIJO, modo_general=LITRO)
    recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    db_session.expire_all()
    assert sum((D(x.valor_transporte) for x in sueltas_de(db_session, esc)), CERO) == FIJO
    poner_tarifas(client, h, esc, con_fabrica=False, modo_general=LITRO)
    db_session.expire_all()
    fotos = sum((D(x.valor_transporte) for x in sueltas_de(db_session, esc)), CERO)
    esperado = cent(D("219.45") * GENERAL_LITRO)
    print(f"\n  quitada la tarifa de ruta (fijo) -> general por litro: fotos ${fotos} "
          f"(esperado ${esperado})")
    assert fotos == esperado, (
        f"quitarle la tarifa propia a una ruta de dia fijo dejo fotos rancias de ${fotos}: "
        f"con la general de hoy valen ${esperado}")
    liq = liquidar(client, h, esc)
    p = cuadra(db_session, liq["id"], "emitido tras quitarle la tarifa")
    assert p.total == esperado and p.modos == [LITRO], str(p)


def test_quitarle_la_tarifa_propia_con_comprobante_emitido(client, base_datos, db_session):
    """Emitido DÍA FIJO; se le quita la tarifa propia a la ruta (cae en la general por litro)."""
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " qt2", modo_fabrica=DIA_FIJO, modo_general=LITRO)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    p = cuadra(db_session, liq["id"], "emitido dia fijo")
    assert p.total == FIJO
    poner_tarifas(client, h, esc, con_fabrica=False, modo_general=LITRO)
    r = poner(client, h, a["id"], cantidad_litros="91.30")
    assert r.status_code == 200, r.text
    p = cuadra(db_session, liq["id"], "sin tarifa propia + corregir litros")
    assert p.modos == [DIA_FIJO], f"el papel fijo cambio de forma: {p.modos}"
    assert p.total == FIJO, f"el dia fijo emitido quedo en ${p.total}"
    impreso = pdf_de(client, h, liq["id"])
    assert "Día completo" in impreso
    rr = client.post(f"{LIQUIDACIONES}/{liq['id']}/recalcular", headers=h)
    assert rr.status_code == 200, rr.text
    q = cuadra(db_session, liq["id"], "+ RECALCULAR")
    assert q.modos == [LITRO] and q.total == cent(D("228.75") * GENERAL_LITRO), str(q)


# ===========================================================================
# 3. LA RECEPCIÓN QUE SALE Y VUELVE
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
def test_sale_del_dia_y_vuelve_con_el_modo_cruzado(client, base_datos, db_session, emitido, hoy):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" sv{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    inicial = cuadra(db_session, liq["id"], f"emitido {emitido}")
    valor_inicial = inicial.total
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    # se va al otro dia del mismo periodo
    assert poner(client, h, a["id"], fecha=OTRO_DIA).status_code == 200
    cuadra(db_session, liq["id"], "movido al 17")
    # y vuelve
    assert poner(client, h, a["id"], fecha=EL_DIA).status_code == 200
    p = cuadra(db_session, liq["id"], "devuelto al 16")
    print(f"      inicial ${valor_inicial} -> final ${p.total}")
    assert p.total == valor_inicial, (
        f"sacar el dia y devolverlo dejo el comprobante en ${p.total}; estaba en "
        f"${valor_inicial}")
    assert p.modos == [emitido], f"el papel cambio de forma: {p.modos}"


@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
def test_se_va_a_otra_quincena_y_vuelve_con_el_modo_cruzado(
        client, base_datos, db_session, emitido, hoy):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" oq{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    inicial = cuadra(db_session, liq["id"], f"emitido {emitido}")
    valor_inicial = inicial.total
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    assert poner(client, h, a["id"], fecha=FUERA).status_code == 200
    cuadra(db_session, liq["id"], "fuera del periodo")
    db_session.expire_all()
    fuera = [r for r in sueltas_de(db_session, esc) if str(r.fecha) == FUERA]
    assert len(fuera) == 1
    print(f"      la que se fue: {fuera[0].cantidad_litros} L -> ${fuera[0].valor_transporte}")
    assert poner(client, h, a["id"], fecha=EL_DIA).status_code == 200
    p = cuadra(db_session, liq["id"], "devuelta a la quincena")
    print(f"      inicial ${valor_inicial} -> final ${p.total}")
    assert p.total == valor_inicial, (
        f"la vuelta de otra quincena dejo el comprobante en ${p.total}; estaba en "
        f"${valor_inicial}")
    assert p.modos == [emitido], f"el papel cambio de forma: {p.modos}"


# ===========================================================================
# 4. DOS RUTAS CON MODOS DISTINTOS EL MISMO DÍA
# ===========================================================================
def test_dos_rutas_modos_distintos_el_mismo_dia_y_una_recepcion_que_se_muda(
        client, base_datos, db_session):
    """A fábrica DÍA FIJO, Nápoles POR LITRO, el mismo día; después se cruza el modo."""
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " 2r1", modo_fabrica=DIA_FIJO)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    g = recibir(client, h, esc, EL_DIA, "Gilberto", "96.30", ruta_id=esc["napoles"]["id"])
    liq = liquidar(client, h, esc)
    p = cuadra(db_session, liq["id"], "emitido: fijo + napoles")
    esperado = FIJO + cent(D("96.30") * NAPOLES)
    assert p.total == esperado, p.total
    # cruzo los dos modos: fabrica pasa a litro, napoles a dia fijo
    poner_tarifas(client, h, esc, modo_fabrica=LITRO, modo_napoles=DIA_FIJO,
                  valor_napoles=D("90000"))
    assert poner(client, h, a["id"], cantidad_litros="91.30").status_code == 200
    p = cuadra(db_session, liq["id"], "cruzados los dos modos + corregir")
    assert p.total == esperado, (
        f"cruzar los dos modos movio el papel emitido de ${esperado} a ${p.total}")
    # y ahora se muda de ruta: la ruta ESCOGE la tarifa, ahi si re-deriva
    assert poner(client, h, g["id"], ruta_id=esc["fabrica"]["id"]).status_code == 200
    cuadra(db_session, liq["id"], "Gilberto se muda a fabrica")
    rr = client.post(f"{LIQUIDACIONES}/{liq['id']}/recalcular", headers=h)
    assert rr.status_code == 200, rr.text
    cuadra(db_session, liq["id"], "+ RECALCULAR")


# ===========================================================================
# 5. ANULAR Y REGENERAR CON EL MODO CAMBIADO EN MEDIO
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
def test_anular_y_regenerar_con_el_modo_cambiado_en_medio(
        client, base_datos, db_session, emitido, hoy):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" ar{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    cuadra(db_session, liq["id"], f"emitido {emitido}")
    assert client.post(f"{LIQUIDACIONES}/{liq['id']}/anular", headers=h,
                       json={"motivo": "prueba"}).status_code in (200, 201)
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    db_session.expire_all()
    fotos = sum((D(x.valor_transporte) for x in sueltas_de(db_session, esc)), CERO)
    esperado = FIJO if hoy == DIA_FIJO else cent(D("219.45") * POR_LITRO)
    print(f"\n  anulado + modo {hoy}: fotos sueltas ${fotos} (esperado ${esperado})")
    assert fotos == esperado, (
        f"tras anular y cambiar el modo las fotos sueltas suman ${fotos} y con la tarifa "
        f"de hoy valen ${esperado}")
    liq2 = liquidar(client, h, esc)
    p = cuadra(db_session, liq2["id"], "regenerado")
    assert p.total == esperado and p.modos == [hoy], str(p)


# ===========================================================================
# 6. EL CANDADO: CON PLATA YA SALIDA NADA SE MUEVE
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
@pytest.mark.parametrize("estado", ["pagado", "con abono"])
def test_el_candado_con_los_modos_cruzados(client, base_datos, db_session, emitido, hoy, estado):
    """Con plata ya salida por el flete, nada que le mueva la cuenta del flete pasa.

    SEIS CAMINOS REBOTAN CON 422 Y UNO RESPONDE 200, y ese 200 es lo correcto: EL CANDADO
    DE ESTE PROYECTO ES POR CAMPO. `bonificaciones` es plata DE LA LECHE —lo que se le
    reconoce de más al productor— y no entra en la cuenta del flete por ningún lado, así
    que un flete pagado no la puede trabar: el dueño tiene que poder corregirle una
    bonificación a un productor sin que se lo impida un comprobante DEL TRANSPORTADOR que
    ya se pagó. Trabarla sería trabar la quincena de la leche por un documento que no la
    toca. Los campos que SÍ mueven el flete —los litros, la fecha, la ruta, el
    transportador, apagar el día y borrarlo— son los seis que rebotan, y están todos acá.

    LO QUE VALE, y es lo que se mide al final, son las tres comprobaciones de que ese 200 no
    le movió un peso al comprobante pagado: su total, sus fotos y sus renglones quedan
    idénticos. Eso es lo que protege la plata que ya salió de la caja. Exigirle un 422 a un
    campo de la leche no protegía plata: protegía la expectativa de la prueba.
    """
    h = auth_headers(client, "admin.a")
    suf = f" cd{emitido[0]}{hoy[0]}{estado[0]}"
    esc = escenario(client, h, suf, modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    liq_id = liq["id"]
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    if estado == "pagado":
        assert client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h).status_code == 200
    else:
        r = client.post(f"{LIQUIDACIONES}/{liq_id}/pagos", headers=h,
                        json={"fecha": EL_DIA, "valor": "1000"})
        assert r.status_code in (200, 201), r.text
    antes = Papel(db_session, liq_id)
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    # Cada intento lleva EL ESTADO QUE SE ESPERA, porque no son todos el mismo: seis mueven
    # la cuenta del flete y rebotan; la bonificación es plata de la leche y pasa. Ver el
    # docstring.
    intentos = [
        ("corregir litros", dict(cantidad_litros="91.30"), 422),
        ("apagar", dict(estado="inactivo"), 422),
        ("mover la fecha", dict(fecha=OTRO_DIA), 422),
        ("mover la ruta", dict(ruta_id=esc["napoles"]["id"]), 422),
        ("quitar la ruta", dict(ruta_id=None), 422),
        ("mover el transportador", dict(transportador_id=esc["beto"]["id"]), 422),
        ("bonificaciones (de la LECHE)", dict(bonificaciones="500"), 200),
    ]
    print(f"\n  candado {emitido}->{hoy} / {estado}")
    for nombre, cuerpo, esperado in intentos:
        r = poner(client, h, a["id"], **cuerpo)
        print(f"    {nombre:<30}{r.status_code}  (esperado {esperado})")
        assert r.status_code == esperado, (
            f"{nombre} respondio {r.status_code}, se esperaba {esperado}")
    assert client.delete(f"{RECEPCIONES}/{a['id']}", headers=h).status_code == 422
    despues = Papel(db_session, liq_id)
    assert antes.total == despues.total, "el comprobante pagado se movio"
    assert antes.fotos == despues.fotos, "las fotos de un comprobante pagado se movieron"
    assert antes.renglones == despues.renglones, "los renglones de un pagado se movieron"


# ===========================================================================
# 7. LO QUE IMPRIME EL PDF DEBE SER VERIFICABLE
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
def test_el_pdf_no_imprime_una_linea_que_el_conductor_no_pueda_verificar(
        client, base_datos, db_session, emitido, hoy):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" pdf{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    assert poner(client, h, a["id"], cantidad_litros="91.30").status_code == 200
    p = cuadra(db_session, liq["id"], f"{emitido}->{hoy} + corregir")
    impreso = pdf_de(client, h, liq["id"])
    print(f"      renglones: {p.renglones}")
    for f, _, li, pr, v, m, yc in p.renglones:
        assert v > CERO or yc, (
            f"el PDF le imprime al conductor una linea del {f} con {li} L en ${v} y ese "
            "viaje no esta marcado como ya cobrado")
        if m == LITRO:
            assert cent(li * pr) == v, (
                f"la linea por litro del {f} no se puede verificar: {li} L x ${pr} = "
                f"${cent(li * pr)}, y el papel imprime ${v}")
    assert "$0,00" not in impreso.split("Detalle diario", 1)[-1].split("Resumen", 1)[0]
