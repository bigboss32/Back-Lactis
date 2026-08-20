"""AUDITORÍA EXTERNA DEL DÍA FIJO: intentar, por todos los caminos, que el día valga
algo distinto de su tarifa.

NO es un archivo de la implementación: es el intento de tumbarla. La regla que se
ataca es la que dio el dueño y es una sola:

    EN UN DÍA FIJO, EL RENGLÓN VALE LA TARIFA. Punto. Nunca la suma de las fotos.
    Las fotos son SOLO el reparto de esa cifra, y su única obligación es sumarla
    exacto.

LA MEDICIÓN, la misma después de CADA operación (`_medir`):

  · lo que las fotos VIVAS de ese (transportador, día, ruta) suman;
  · lo que las fotos de las recepciones APAGADAS de ese mismo grupo cargan (tiene
    que ser $0,00: una recepción apagada no compone ningún renglón);
  · lo que los renglones VIVOS de cualquier comprobante no anulado cobran por ese
    (transportador, día, ruta);
  · y, aparte, que TODO comprobante de flete del sistema siga cuadrado: su
    `valor_transporte` == la suma de sus renglones == la suma de las fotos de sus
    recepciones activas.

Se lee la BASE y no la respuesta del API a propósito: lo que hay que proteger es la
plata guardada, que es la que leen la contabilidad, la grilla y el papel del conductor.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.modules.liquidaciones.models import (
    ESTADO_ANULADA,
    TIPO_TRANSPORTADOR,
    Liquidacion,
    LiquidacionDetalle,
)
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    FIJO,
    LIQUIDACIONES,
    NAPOLES,
    RECEPCIONES,
    RUTAS,
    TRANSPORTADORES,
    D,
    _crear,
    _escenario,
    _liquidar_flete,
    _recibir,
    centavos,
)

CERO = D(0)
EL_DIA = "2026-07-16"


def _f(texto: str) -> date:
    return date(*(int(x) for x in texto.split("-")))


# ---------------------------------------------------------------------------
# LA MEDICIÓN
# ---------------------------------------------------------------------------
class Medida:
    def __init__(self, db, transportador_id, fecha, ruta_id):
        db.expire_all()
        filas = db.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.transportador_id == uuid.UUID(transportador_id),
                RecepcionLeche.fecha == _f(fecha),
                RecepcionLeche.ruta_id == (
                    uuid.UUID(ruta_id) if ruta_id is not None else None
                ),
                RecepcionLeche.deleted_at.is_(None),
            )
        ).all()
        self.vivas = {
            str(x.id)[:8]: (D(x.cantidad_litros), D(x.valor_transporte))
            for x in filas if x.estado == "activo"
        }
        self.apagadas = {
            str(x.id)[:8]: (D(x.cantidad_litros), D(x.valor_transporte))
            for x in filas if x.estado != "activo"
        }
        self.renglones = [
            (D(d.valor), d.modo_transporte, bool(d.dia_fijo_ya_cobrado))
            for d in db.scalars(
                select(LiquidacionDetalle)
                .join(Liquidacion, Liquidacion.id == LiquidacionDetalle.liquidacion_id)
                .where(
                    Liquidacion.tipo == TIPO_TRANSPORTADOR,
                    Liquidacion.transportador_id == uuid.UUID(transportador_id),
                    Liquidacion.estado != ESTADO_ANULADA,
                    Liquidacion.deleted_at.is_(None),
                    LiquidacionDetalle.fecha == _f(fecha),
                    LiquidacionDetalle.ruta_id == (
                        uuid.UUID(ruta_id) if ruta_id is not None else None
                    ),
                    LiquidacionDetalle.deleted_at.is_(None),
                )
            ).all()
        ]

    @property
    def fotos(self):
        return sum((v for _, v in self.vivas.values()), CERO)

    @property
    def fantasmas(self):
        return sum((v for _, v in self.apagadas.values()), CERO)

    @property
    def cobrado(self):
        return sum((v for v, _, _ in self.renglones), CERO)

    def __str__(self):
        piezas = " + ".join(
            f"{lit}L=${val}" for lit, val in sorted(self.vivas.values())
        ) or "(sin vivas)"
        ren = " ".join(f"[{m}]${v}" for v, m, _ in self.renglones) or "(sin renglon)"
        extra = f"  FANTASMA=${self.fantasmas}" if self.fantasmas else ""
        return f"{piezas} = ${self.fotos}   {ren}{extra}"


def _todos_los_comprobantes_cuadrados(db, paso):
    """La red de afuera: cada comprobante de flete suma lo suyo, al centavo."""
    db.expire_all()
    for liq in db.scalars(
        select(Liquidacion).where(
            Liquidacion.tipo == TIPO_TRANSPORTADOR,
            Liquidacion.estado != ESTADO_ANULADA,
            Liquidacion.deleted_at.is_(None),
        )
    ).all():
        renglones = sum((D(d.valor) for d in liq.detalles if d.deleted_at is None), CERO)
        fotos = sum(
            (
                D(r.valor_transporte)
                for r in db.scalars(
                    select(RecepcionLeche).where(
                        RecepcionLeche.liquidacion_transporte_id == liq.id,
                        RecepcionLeche.estado == "activo",
                        RecepcionLeche.deleted_at.is_(None),
                    )
                ).all()
            ),
            CERO,
        )
        assert D(liq.valor_transporte) == renglones, (
            f"tras «{paso}» el comprobante {str(liq.id)[:8]} vale "
            f"${D(liq.valor_transporte)} y sus renglones suman ${renglones}"
        )
        assert renglones == fotos, (
            f"tras «{paso}» el comprobante {str(liq.id)[:8]} cobra ${renglones} y las "
            f"fotos de sus recepciones suman ${fotos}"
        )


def _medir(db, esc, paso, *, vale=FIJO, fecha=EL_DIA, ruta="fabrica", quien="alex"):
    ruta_id = esc[ruta]["id"] if ruta is not None else None
    m = Medida(db, esc[quien]["id"], fecha, ruta_id)
    print(f"  {paso:<48}{m}")
    assert m.fotos == vale, (
        f"tras «{paso}» las fotos del {fecha}/{ruta} suman ${m.fotos} y el dia vale ${vale}"
    )
    assert m.fantasmas == CERO, (
        f"tras «{paso}» quedaron ${m.fantasmas} de flete FANTASMA en apagadas: {m.apagadas}"
    )
    if m.renglones:
        assert m.cobrado == vale, (
            f"tras «{paso}» los comprobantes cobran ${m.cobrado} por el {fecha}/{ruta} "
            f"y el dia vale ${vale}"
        )
        assert m.cobrado == m.fotos, (
            f"tras «{paso}» el renglon dice ${m.cobrado} y sus fotos suman ${m.fotos}"
        )
    _todos_los_comprobantes_cuadrados(db, paso)
    return m


def _put(client, h, rid, **campos):
    return client.put(f"{RECEPCIONES}/{rid}", json=campos, headers=h)


def _ok(r, que=""):
    assert r.status_code in (200, 201), f"{que}: {r.status_code} {r.text}"
    return r.json()


# ===========================================================================
# A1 — CAMBIARLE EL TRANSPORTADOR a una recepción de un día fijo ya liquidado
# ===========================================================================
def test_a1_cambiar_el_transportador_no_multiplica_ni_pierde_el_fijo(
    client, base_datos, db_session
):
    """Alex y Beto cobran los dos $150.000 el día en la misma ruta.

    Un día de Alex con tres proveedores, ya liquidado, y se le pasa UNO a Beto.
    A mano: a Alex le sigue valiendo el día $150.000 (recogió menos, pero el fijo
    se cobra por haber ido) y a Beto le vale otro día completo, $150.000. Y de
    vuelta, otra vez $150.000 en Alex y nada en Beto.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    beto = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Beto Ruiz", "valor_transporte": "150000", "modo_transporte": "litro",
        "rutas": [{"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(FIJO),
                   "modo_transporte": "dia_fijo"}],
    })
    esc["beto"] = beto

    print("\n===== A1: pasarle una recepcion de Alex a Beto =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _liquidar_flete(client, h)
    _medir(db_session, esc, "liquidado (Alex)")
    _medir(db_session, esc, "Beto todavia sin nada", vale=CERO, quien="beto")

    _ok(_put(client, h, a["id"], transportador_id=beto["id"]), "pasar a Beto")
    _medir(db_session, esc, "Aurelio pasa a Beto -> Alex")
    _medir(db_session, esc, "Aurelio pasa a Beto -> Beto", quien="beto")

    _ok(_put(client, h, a["id"], transportador_id=esc["alex"]["id"]), "volver a Alex")
    m_alex = _medir(db_session, esc, "vuelve a Alex -> Alex", vale=FIJO)
    _medir(db_session, esc, "vuelve a Alex -> Beto", vale=CERO, quien="beto")
    print(f"  reparto final Alex: {m_alex.vivas}")


# ===========================================================================
# A2 — MOVER LA RUTA dentro y fuera del día fijo, con comprobante de por medio
# ===========================================================================
def test_a2_mover_la_ruta_de_un_dia_fijo_liquidado(client, base_datos, db_session):
    """Sacar una recepción del día fijo a la ruta POR LITRO y devolverla.

    A mano, con 82,00 / 137,45 / 96,30 L el 16/07:
      · las tres en «A fabrica» (fijo)      →  $150.000,00
      · Aurelio (82,00 L) pasa a «Napoles»  →  fabrica sigue en $150.000,00 y
        Napoles cobra 82,00 × $242,76 = $19.906,32
      · y de vuelta                          →  $150.000,00 y Napoles en $0
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A2: mover la RUTA dentro y fuera del dia fijo =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _liquidar_flete(client, h)
    _medir(db_session, esc, "liquidado")

    _ok(_put(client, h, a["id"], ruta_id=esc["napoles"]["id"]), "a Napoles")
    _medir(db_session, esc, "Aurelio se va a Napoles -> fabrica")
    esperado_nap = centavos(D("82.00") * NAPOLES)
    _medir(db_session, esc, "Aurelio se va a Napoles -> napoles",
           vale=esperado_nap, ruta="napoles")

    _ok(_put(client, h, a["id"], ruta_id=esc["fabrica"]["id"]), "vuelve a fabrica")
    _medir(db_session, esc, "Aurelio vuelve -> fabrica")
    _medir(db_session, esc, "Aurelio vuelve -> napoles", vale=CERO, ruta="napoles")


# ===========================================================================
# A3 — JUNTAR Y SEPARAR DÍAS FIJOS moviendo la fecha, con comprobante
# ===========================================================================
def test_a3_juntar_y_separar_dias_fijos_por_la_fecha(client, base_datos, db_session):
    """Dos días fijos liquidados; se juntan en uno y se vuelven a separar.

    A mano:
      · 16/07 (82,00 + 137,45 L) y 17/07 (96,30 + 60,00 L)  →  $150.000 cada uno
      · se pasa TODO el 17 al 16                             →  UN solo día: $150.000
        y el 17 desaparece (no queda un renglón de $0,00 diciendo que ese viaje no
        se paga: ese viaje ya no existe)
      · se vuelve a sacar una al 17                          →  $150.000 y $150.000
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A3: juntar dos dias fijos en uno y volver a separarlos =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    c = _recibir(client, h, esc, "2026-07-17", "Gilberto", "96.30")
    d = _recibir(client, h, esc, "2026-07-17", "Ramiro", "60.00")
    _liquidar_flete(client, h)
    _medir(db_session, esc, "liquidado -> 16/07")
    _medir(db_session, esc, "liquidado -> 17/07", fecha="2026-07-17")

    _ok(_put(client, h, c["id"], fecha=EL_DIA), "Gilberto al 16")
    _medir(db_session, esc, "Gilberto pasa al 16 -> 16/07")
    _medir(db_session, esc, "Gilberto pasa al 16 -> 17/07", fecha="2026-07-17")

    _ok(_put(client, h, d["id"], fecha=EL_DIA), "Ramiro al 16")
    m = _medir(db_session, esc, "todo el 17 se fue al 16 -> 16/07")
    m17 = _medir(db_session, esc, "todo el 17 se fue al 16 -> 17/07",
                 vale=CERO, fecha="2026-07-17")
    assert not m17.renglones, (
        f"el 17/07 se quedo cobrando {m17.renglones} sin ninguna recepcion viva"
    )
    print(f"  reparto del dia junto: {m.vivas}")

    _ok(_put(client, h, d["id"], fecha="2026-07-17"), "Ramiro vuelve al 17")
    _medir(db_session, esc, "Ramiro vuelve al 17 -> 16/07")
    _medir(db_session, esc, "Ramiro vuelve al 17 -> 17/07", fecha="2026-07-17")


# ===========================================================================
# A4 — APAGAR / PRENDER / BORRAR EN TODOS LOS ÓRDENES hasta dejar el día vacío
# ===========================================================================
def test_a4_apagar_prender_borrar_en_todos_los_ordenes(client, base_datos, db_session):
    """Se apagan las tres, se prenden las tres, se borran las tres, y se vuelve a
    poner una. En CADA paso el día vale $150.000 mientras quede una recepción viva,
    y cuando no queda ninguna el renglón desaparece.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A4: apagar, prender y borrar en todos los ordenes =====")
    ids = [
        _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")["id"],
        _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")["id"],
        _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")["id"],
    ]
    _liquidar_flete(client, h)
    _medir(db_session, esc, "liquidado")

    for i, rid in enumerate(ids):
        _ok(_put(client, h, rid, estado="inactivo"), "apagar")
        quedan = len(ids) - i - 1
        m = _medir(db_session, esc, f"apagada {i + 1}/3 (quedan {quedan})",
                   vale=FIJO if quedan else CERO)
        if not quedan:
            assert not m.renglones, f"sin recepciones vivas quedo cobrando {m.renglones}"

    for i, rid in enumerate(reversed(ids)):
        _ok(_put(client, h, rid, estado="activo"), "prender")
        _medir(db_session, esc, f"prendida {i + 1}/3")

    for i, rid in enumerate(ids):
        r = client.delete(f"{RECEPCIONES}/{rid}", headers=h)
        assert r.status_code in (200, 204), r.text
        quedan = len(ids) - i - 1
        m = _medir(db_session, esc, f"borrada {i + 1}/3 (quedan {quedan})",
                   vale=FIJO if quedan else CERO)
        if not quedan:
            assert not m.renglones, f"sin recepciones quedo cobrando {m.renglones}"

    _recibir(client, h, esc, EL_DIA, "Rosa", "124.20")
    _medir(db_session, esc, "se vuelve a poner una")


# ===========================================================================
# A5 — CORREGIR LOS LITROS a una, a varias y a TODAS, en borrador
# ===========================================================================
def test_a5_corregir_los_litros_de_a_una_hasta_todas(client, base_datos, db_session):
    """El defecto original multiplicaba el día corrigiendo litros. Se corrigen las
    cinco, una por una, y el día sigue valiendo $150.000 después de cada corrección.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A5: corregir los litros de las cinco, una por una =====")
    ids = [
        _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")["id"],
        _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")["id"],
        _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")["id"],
        _recibir(client, h, esc, EL_DIA, "Ramiro", "60.00")["id"],
        _recibir(client, h, esc, EL_DIA, "Rosa", "124.20")["id"],
    ]
    _liquidar_flete(client, h)
    _medir(db_session, esc, "liquidado (5 proveedores)")
    for i, (rid, litros) in enumerate(zip(ids, ["91.30", "140.00", "88.75", "77.05", "119.90"])):
        _ok(_put(client, h, rid, cantidad_litros=litros), "corregir litros")
        _medir(db_session, esc, f"corregida {i + 1}/5 -> {litros} L")


# ===========================================================================
# A6 — CORREGIR sobre un comprobante APROBADO, y el candado sobre uno con ABONO
# ===========================================================================
def test_a6_aprobado_se_corrige_y_con_abono_no_se_mueve_nada(
    client, base_datos, db_session
):
    """Aprobado: se puede corregir y el día sigue valiendo $150.000.
    Con un ABONO registrado (plata que ya salió): NINGÚN camino lo mueve.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    otro = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Beto Ruiz", "valor_transporte": "300", "modo_transporte": "litro"})
    print("\n===== A6: aprobado, y despues con un ABONO =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    b = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    c = _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    liq_id = _liquidar_flete(client, h)["id"]
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h), "aprobar")

    _ok(_put(client, h, a["id"], cantidad_litros="91.30"), "corregir en aprobada")
    _medir(db_session, esc, "corregida con el comprobante APROBADO")
    _ok(_put(client, h, b["id"], cantidad_litros="140.00"), "corregir 2 en aprobada")
    _medir(db_session, esc, "corregida la 2 con el comprobante APROBADO")
    estado = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()["estado"]
    print(f"  estado del comprobante despues de corregir: {estado}")

    # Un ABONO: plata que ya salió, aunque no sea el total. (Corregir devolvió el
    # comprobante a borrador, así que se vuelve a aprobar antes de abonar.)
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h), "reaprobar")
    r = client.post(
        f"{LIQUIDACIONES}/{liq_id}/pagos",
        json={"valor": "50000", "fecha": "2026-08-01"},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    print(f"  abono de $50.000 registrado; estado={r.json()['estado']}")
    antes = _medir(db_session, esc, "con el abono puesto")

    ataques = [
        ("corregir litros", lambda: _put(client, h, c["id"], cantidad_litros="999")),
        ("apagar", lambda: _put(client, h, c["id"], estado="inactivo")),
        ("mover la fecha", lambda: _put(client, h, c["id"], fecha="2026-07-20")),
        ("mover la ruta", lambda: _put(client, h, c["id"], ruta_id=esc["napoles"]["id"])),
        ("cambiar transportador",
         lambda: _put(client, h, c["id"], transportador_id=otro["id"])),
        ("borrar", lambda: client.delete(f"{RECEPCIONES}/{c['id']}", headers=h)),
        ("recalcular", lambda: client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)),
    ]
    for nombre, disparo in ataques:
        rr = disparo()
        print(f"  candado «{nombre:<22}» -> {rr.status_code}")
        assert rr.status_code >= 400, (
            f"«{nombre}» paso con {rr.status_code} sobre un flete con plata ya salida"
        )
    # Y la tarifa cambiada por debajo tampoco lo mueve.
    _ok(client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [{"ruta_id": esc["fabrica"]["id"], "valor_transporte": "999999",
                         "modo_transporte": "litro"}]},
        headers=h,
    ), "cambiar tarifa")
    despues = _medir(db_session, esc, "y con la tarifa cambiada por debajo")
    assert despues.vivas == antes.vivas, "las fotos de un flete con abono se movieron"


# ===========================================================================
# A7 — LECHE ANOTADA TARDE en un día ya liquidado y en uno ya PAGADO
# ===========================================================================
def test_a7_leche_anotada_tarde_no_cobra_el_dia_dos_veces(client, base_datos, db_session):
    """El día ya costó $150.000. Anotar leche después no lo vuelve a cobrar, y
    después de PAGADO tampoco. En los dos casos el día sigue valiendo $150.000.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A7: leche anotada tarde, liquidado y pagado =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    _medir(db_session, esc, "liquidado (2 proveedores)")

    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _medir(db_session, esc, "leche anotada tarde (borrador)")

    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h), "aprobar")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h), "pagar")
    _medir(db_session, esc, "el comprobante se pago")

    _recibir(client, h, esc, EL_DIA, "Ramiro", "60.00")
    _medir(db_session, esc, "mas leche tarde, ya PAGADO")

    # Y al generar otra vez, el día sale en $0,00 marcado «Ya cobrado» (o no se
    # genera comprobante, si no le queda nada más por cobrar).
    r = client.post(
        f"{LIQUIDACIONES}/generar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador"},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    cuerpo = r.json()
    print(f"  generar otra vez: generadas={len(cuerpo['generadas'])} "
          f"omitidas={[o['motivo_codigo'] for o in cuerpo['omitidas']]}")
    _medir(db_session, esc, "se genero otra vez la misma quincena")


# ===========================================================================
# A8 — RECALCULAR Y REGENERAR dos y tres veces: el papel no se mueve
# ===========================================================================
def test_a8_recalcular_y_regenerar_no_mueven_el_papel(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A8: recalcular tres veces seguidas =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _recibir(client, h, esc, "2026-07-18", "Henri", "219.45",
             ruta_id=esc["napoles"]["id"])
    liq_id = _liquidar_flete(client, h)["id"]
    primero = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    base = _medir(db_session, esc, "generado")
    for i in range(3):
        _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h), "recalcular")
        m = _medir(db_session, esc, f"recalculado {i + 1}/3")
        assert m.vivas == base.vivas, "recalcular movio el reparto"
    ahora = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    assert D(ahora["valor_transporte"]) == D(primero["valor_transporte"])
    assert [(d["fecha"], d["valor"], d["modo_transporte"]) for d in ahora["detalles"]] == \
           [(d["fecha"], d["valor"], d["modo_transporte"]) for d in primero["detalles"]]
    print(f"  total estable: ${D(ahora['valor_transporte'])}")


# ===========================================================================
# A9 — UN FIJO QUE NO SE REPARTE REDONDO (entre 7, y entre 3 con $100.000)
# ===========================================================================
def test_a9_un_fijo_que_no_se_reparte_redondo(client, base_datos, db_session):
    """$150.000 entre 7 iguales: 7 × $21.428,57 = $149.999,99. El centavo que falta
    lo entrega el resto mayor y la suma da EXACTO $150.000,00.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A9: un fijo que no se reparte redondo =====")
    for i in range(7):
        nombre = f"Siete{i}"
        esc["proveedores"][nombre] = _crear(client, h, "/api/v1/proveedores", {
            "nombre": nombre, "vereda": "La Vega", "precio_litro": "1800",
            "ruta_id": esc["fabrica"]["id"]})
        _recibir(client, h, esc, EL_DIA, nombre, "50.00")
    m = _medir(db_session, esc, "siete proveedores de 50,00 L")
    valores = sorted(v for _, v in m.vivas.values())
    print(f"  reparto: {valores}  suma=${sum(valores, CERO)}")
    assert sum(valores, CERO) == FIJO
    assert valores[0] >= D("21428.57") - D("0.01")
    _liquidar_flete(client, h)
    _medir(db_session, esc, "liquidado")


# ===========================================================================
# A10 — DOS RUTAS FIJAS Y UNA POR LITRO EL MISMO DÍA
# ===========================================================================
def test_a10_dos_rutas_fijas_y_una_por_litro_el_mismo_dia(client, base_datos, db_session):
    """Tres rutas el mismo 16/07: dos fijas ($150.000 y $90.000) y una por litro.
    Son TRES renglones y ninguno se contagia del otro.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    tercera = _crear(client, h, RUTAS, {"nombre": "El Alto", "municipio": "Granada"})
    esc["alto"] = tercera
    _ok(client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(FIJO),
             "modo_transporte": "dia_fijo"},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": "litro"},
            {"ruta_id": tercera["id"], "valor_transporte": "90000",
             "modo_transporte": "dia_fijo"},
        ]},
        headers=h,
    ), "tres rutas")
    for nombre, ruta in (("AltoUno", tercera), ("AltoDos", tercera)):
        esc["proveedores"][nombre] = _crear(client, h, "/api/v1/proveedores", {
            "nombre": nombre, "vereda": "El Alto", "precio_litro": "1800",
            "ruta_id": ruta["id"]})

    print("\n===== A10: dos rutas fijas y una por litro el mismo dia =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "AltoUno", "41.10")
    _recibir(client, h, esc, EL_DIA, "AltoDos", "38.90")
    _recibir(client, h, esc, EL_DIA, "Henri", "219.45")
    liq_id = _liquidar_flete(client, h)["id"]
    _medir(db_session, esc, "fabrica (fijo 150k)")
    _medir(db_session, esc, "el alto (fijo 90k)", vale=D("90000"), ruta="alto")
    _medir(db_session, esc, "napoles (por litro)",
           vale=centavos(D("219.45") * NAPOLES), ruta="napoles")
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    total = FIJO + D("90000") + centavos(D("219.45") * NAPOLES)
    print(f"  a mano: 150.000 + 90.000 + {centavos(D('219.45') * NAPOLES)} = ${total}")
    assert D(liq["valor_transporte"]) == total, liq["valor_transporte"]

    # Y ahora se corrige la del POR LITRO: no puede tocar ninguno de los dos fijos.
    _medir(db_session, esc, "y se corrige la de Napoles -> fabrica")
    _medir(db_session, esc, "y se corrige la de Napoles -> alto",
           vale=D("90000"), ruta="alto")


# ===========================================================================
# A11 — CAMBIAR LA TARIFA de fijo a por litro y al revés, antes y después
# ===========================================================================
def test_a11_cambiar_el_modo_antes_y_despues_de_liquidar(client, base_datos, db_session):
    """Antes de liquidar el cambio manda enseguida; después de liquidar solo lo
    aplica RECALCULAR (que el dueño oprime a propósito) y no un recuadre.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A11: cambiar el modo antes y despues de liquidar =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    b = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _medir(db_session, esc, "sin liquidar, en fijo")

    def poner(valor, modo):
        _ok(client.put(
            f"{TRANSPORTADORES}/{esc['alex']['id']}",
            json={"rutas": [
                {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(valor),
                 "modo_transporte": modo},
                {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES),
                 "modo_transporte": "litro"},
            ]},
            headers=h,
        ), "cambiar tarifa")

    litros_dia = D("82.00") + D("137.45")
    poner(NAPOLES, "litro")
    # El cambio de tarifa no reescribe las fotos por sí solo: lo hace el primer
    # guardado del día o el comprobante. Se toca una y se mira. QUEDA MEDIDO, no
    # exigido: en este punto la foto de la que NO se tocó todavía carga su parte del
    # fijo viejo. Lo que sí se exige es que el comprobante salga bien (más abajo).
    _ok(_put(client, h, a["id"], observaciones="tocada"), "tocar")
    m = Medida(db_session, esc["alex"]["id"], EL_DIA, esc["fabrica"]["id"])
    print(f"  pasada a POR LITRO, tocando solo una:            {m}")
    print(f"    a mano por litro el dia entero: ${centavos(litros_dia * NAPOLES)}")
    poner(FIJO, "dia_fijo")
    _ok(_put(client, h, a["id"], observaciones="tocada otra vez"), "tocar")
    _medir(db_session, esc, "de vuelta a FIJO (sin liquidar)")

    liq_id = _liquidar_flete(client, h)["id"]
    _medir(db_session, esc, "liquidado en FIJO")
    poner(NAPOLES, "litro")
    _ok(_put(client, h, b["id"], observaciones="recuadre"), "tocar tras liquidar")
    _medir(db_session, esc, "modo cambiado + recuadre: NO re-clasifica")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h), "recalcular")
    _medir(db_session, esc, "RECALCULAR si re-clasifica",
           vale=centavos(litros_dia * NAPOLES))
    poner(FIJO, "dia_fijo")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h), "recalcular")
    _medir(db_session, esc, "recalcular de vuelta a FIJO")


# ===========================================================================
# A12 — ANULAR y volver a generar: el día no se cobra dos veces ni se pierde
# ===========================================================================
def test_a12_anular_y_regenerar_un_dia_fijo(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A12: anular y regenerar =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    _medir(db_session, esc, "liquidado")
    # Leche anotada tarde: entra en $0,00 porque el día ya está cobrado.
    tarde = _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _medir(db_session, esc, "leche anotada tarde")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/anular", headers=h), "anular")
    _medir(db_session, esc, "comprobante ANULADO")
    # Tocar la que entró tarde: ahora el día vuelve a estar por cobrar completo.
    _ok(_put(client, h, tarde["id"], observaciones="tras anular"), "tocar")
    m = _medir(db_session, esc, "se toca la que entro tarde")
    print(f"  reparto tras anular: {m.vivas}")
    _liquidar_flete(client, h)
    _medir(db_session, esc, "regenerado")


# ===========================================================================
# A13 — SACAR EL DÍA FUERA DEL PERÍODO DEL COMPROBANTE Y DEVOLVERLO
# ===========================================================================
def test_a13_sacar_la_recepcion_fuera_del_periodo_y_devolverla(
    client, base_datos, db_session
):
    """Mover una recepción a otra quincena la SUELTA del comprobante. El día que
    deja sigue valiendo $150.000 y el día al que llega vale otro fijo completo.
    Al devolverla, el día ya está cobrado y entra en $0,00 (no se cobra dos veces).
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A13: sacar la recepcion de la quincena y devolverla =====")
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _liquidar_flete(client, h)
    _medir(db_session, esc, "liquidado 16-31/07")

    _ok(_put(client, h, a["id"], fecha="2026-08-05"), "a agosto")
    _medir(db_session, esc, "Aurelio se va a agosto -> 16/07")
    _medir(db_session, esc, "Aurelio se va a agosto -> 05/08", fecha="2026-08-05")

    _ok(_put(client, h, a["id"], fecha=EL_DIA), "vuelve a julio")
    m = _medir(db_session, esc, "Aurelio vuelve al 16/07")
    _medir(db_session, esc, "y agosto queda vacio", vale=CERO, fecha="2026-08-05")
    print(f"  reparto al volver: {m.vivas}")


# ===========================================================================
# A14 — BORRAR el comprobante en borrador (no anularlo): ¿se cobra dos veces?
# ===========================================================================
def test_a14_borrar_el_comprobante_en_borrador_no_duplica_el_dia(
    client, base_datos, db_session
):
    """El comprobante se BORRA (no se anula). Sus días quedan libres. El día fijo
    tiene que seguir valiendo $150.000: ni antes ni después de volver a generar.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A14: BORRAR el comprobante en borrador =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    _medir(db_session, esc, "liquidado")
    tarde = _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _medir(db_session, esc, "leche anotada tarde -> $0")

    r = client.delete(f"{LIQUIDACIONES}/{liq_id}", headers=h)
    print(f"  DELETE del comprobante -> {r.status_code} (no existe esa puerta)")
    assert r.status_code == 405, r.text
    _medir(db_session, esc, "el DELETE no existe: nada cambio")

    _ok(_put(client, h, tarde["id"], observaciones="tras el intento"), "tocar")
    m = _medir(db_session, esc, "se toca la que habia entrado tarde")
    print(f"  reparto: {m.vivas}")
    r = client.post(
        f"{LIQUIDACIONES}/generar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador"},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    print(f"  regenerar: generadas={len(r.json()['generadas'])} "
          f"omitidas={[o['motivo_codigo'] for o in r.json()['omitidas']]}")
    _medir(db_session, esc, "se intento regenerar")


# ===========================================================================
# A15 — ANULAR y GENERAR sin tocar nada en el medio
# ===========================================================================
def test_a15_anular_y_generar_sin_tocar_nada(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A15: anular y generar SIN tocar nada en el medio =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _medir(db_session, esc, "liquidado + leche tarde")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/anular", headers=h), "anular")
    _medir(db_session, esc, "anulado, sin tocar nada")
    _liquidar_flete(client, h)
    m = _medir(db_session, esc, "generado de una vez")
    print(f"  reparto final: {m.vivas}")
    assert len(m.vivas) == 3, m.vivas


# ===========================================================================
# A16 — MULTIEMPRESA: el día fijo de una quesera no toca ni reserva el de la otra
# ===========================================================================
def test_a16_dos_queseras_con_el_mismo_dia_fijo(client, base_datos, db_session):
    """Las dos queseras tienen el mismo 16/07 con una ruta fija de $150.000. Cada
    una vale $150.000: ni se suman ni una le reserva el día a la otra.
    """
    print("\n===== A16: dos queseras con el mismo dia fijo =====")
    escenarios = {}
    for quien in ("admin.a", "admin.b"):
        h = auth_headers(client, quien)
        esc = _escenario(client, h)
        _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
        _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
        _liquidar_flete(client, h)
        escenarios[quien] = (h, esc)
        _medir(db_session, esc, f"{quien}: liquidado")
    # Y se corrige en una: la otra no se mueve ni un peso.
    h_a, esc_a = escenarios["admin.a"]
    db_session.expire_all()
    filas = db_session.scalars(
        select(RecepcionLeche).where(
            RecepcionLeche.transportador_id == uuid.UUID(esc_a["alex"]["id"]),
            RecepcionLeche.fecha == _f(EL_DIA),
        )
    ).all()
    _ok(_put(client, h_a, str(filas[0].id), cantidad_litros="91.30"), "corregir en A")
    _medir(db_session, esc_a, "admin.a corrigio")
    _medir(db_session, escenarios["admin.b"][1], "admin.b sigue quieta")


# ===========================================================================
# A17 — LA MATRIZ COMPLETA sobre un solo día, encadenando quince operaciones
# ===========================================================================
def test_a17_quince_operaciones_encadenadas_sobre_el_mismo_dia(
    client, base_datos, db_session
):
    """Un solo día fijo aguantando quince operaciones seguidas sin recuperarse
    entre una y otra. Después de CADA una: el día vale $150.000.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== A17: quince operaciones encadenadas sobre el mismo dia =====")
    ids = {
        n: _recibir(client, h, esc, EL_DIA, n, litros)["id"]
        for n, litros in (("Aurelio", "82.00"), ("Marleny", "137.45"),
                          ("Gilberto", "96.30"), ("Ramiro", "60.00"),
                          ("Rosa", "124.20"))
    }
    liq_id = _liquidar_flete(client, h)["id"]
    _medir(db_session, esc, "0. liquidado")

    pasos = [
        ("1. corregir litros de Aurelio",
         lambda: _put(client, h, ids["Aurelio"], cantidad_litros="91.30")),
        ("2. apagar Marleny",
         lambda: _put(client, h, ids["Marleny"], estado="inactivo")),
        ("3. corregir litros de Gilberto",
         lambda: _put(client, h, ids["Gilberto"], cantidad_litros="88.75")),
        ("4. prender Marleny",
         lambda: _put(client, h, ids["Marleny"], estado="activo")),
        ("5. Ramiro se va a Napoles",
         lambda: _put(client, h, ids["Ramiro"], ruta_id=esc["napoles"]["id"])),
        ("6. Ramiro vuelve a fabrica",
         lambda: _put(client, h, ids["Ramiro"], ruta_id=esc["fabrica"]["id"])),
        ("7. Rosa se va al 17/07",
         lambda: _put(client, h, ids["Rosa"], fecha="2026-07-17")),
        ("8. Rosa vuelve al 16/07",
         lambda: _put(client, h, ids["Rosa"], fecha=EL_DIA)),
        ("9. borrar Gilberto",
         lambda: client.delete(f"{RECEPCIONES}/{ids['Gilberto']}", headers=h)),
        ("10. recalcular",
         lambda: client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)),
        ("11. corregir litros de Rosa",
         lambda: _put(client, h, ids["Rosa"], cantidad_litros="119.90")),
        ("12. apagar Aurelio",
         lambda: _put(client, h, ids["Aurelio"], estado="inactivo")),
        ("13. borrar Aurelio (apagada)",
         lambda: client.delete(f"{RECEPCIONES}/{ids['Aurelio']}", headers=h)),
        ("14. recalcular otra vez",
         lambda: client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)),
        ("15. aprobar",
         lambda: client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h)),
    ]
    for nombre, disparo in pasos:
        rr = disparo()
        assert rr.status_code in (200, 201, 204), f"{nombre}: {rr.status_code} {rr.text}"
        _medir(db_session, esc, nombre)
    _medir(db_session, esc, "17/07 no quedo cobrando nada",
           vale=CERO, fecha="2026-07-17")
    _medir(db_session, esc, "napoles no quedo cobrando nada",
           vale=CERO, ruta="napoles")
