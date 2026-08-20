"""LA MATRIZ DEL CRUCE: cambiarle el MODO a una ruta que YA TIENE COMPROBANTE EMITIDO.

EL DEFECTO QUE ESTE ARCHIVO CIERRA, Y ES UNA SOLA RAÍZ: la recepción escribía su foto
del flete con el modo de HOY mientras el comprobante estaba armado con el modo de AYER,
y nadie los conciliaba.

LA REGLA QUE LO CIERRA, y es la que este archivo sostiene operación por operación:

    LA FOTO DE UNA RECEPCIÓN QUE YA ESTÁ EN UN COMPROBANTE LA MANDA ESE COMPROBANTE,
    NO LA TARIFA DE HOY.

El camino de escritura de la recepción no le toca la foto: se la deja al recuadre del
comprobante, que es el único que sabe en qué modo y a qué tarifa se armó ese papel, y
que conserva lo que corresponde según el modo —EN DÍA FIJO la cifra (el viaje vale lo
que costó), POR LITRO la tarifa (y el renglón se vuelve a derivar de los litros de
hoy)—. Re-precificar con la tarifa de hoy es lo que hace RECALCULAR, que el dueño
oprime a propósito.

LAS CIFRAS QUE YA NO PASAN, escritas acá para que queden fijas. La ruta "A fabrica"
era POR LITRO a $242,76; el 16/07 Aurelio 82,00 L y Marleny 137,45 L:

    comprobante emitido:  219,45 L × $242,76 = $53.273,68
                          (fotos $19.906,32 y $33.367,36)

y el dueño le pone DÍA FIJO $150.000 a esa ruta. Entonces:

  · CORREGIRLE A AURELIO 82,00 → 91,30 L dejaba el comprobante en $33.367,36 —esa
    recepción quedaba en $0,00 y el PDF le imprimía al conductor "91,3 L $0 $0"—. Se
    aprobaba y se pagaba: le faltaban $22.163,99 contra su propia cuenta por litro y
    $116.632,64 contra el fijo de hoy. Y el desglose seguía cuadrando (fotos ==
    renglones == total), así que ninguna red de cuadre lo veía.
        AHORA: 228,75 L × $242,76 = $55.531,35, en el modo en que se emitió.
  · MOVERLE LA FECHA a un día de ese comprobante lo INFLABA a $183.367,36
    (+$130.093,68): le inyectaba un día fijo completo sin que nadie oprimiera
    Recalcular.
        AHORA: sigue en $53.273,68, repartido en dos renglones por litro
        ($33.367,36 del 16 y $19.906,32 del 17).

LO QUE LA MATRIZ REVISA DESPUÉS DE CADA OPERACIÓN, y son cuatro cosas a la vez:

  1. el comprobante EMITIDO no se movió ni un peso, salvo lo que legítimamente le
     corresponde por haber cambiado los litros, Y EN EL MODO EN QUE SE EMITIÓ;
  2. las fotos de sus recepciones suman EXACTO sus renglones;
  3. ninguna foto quedó en $0,00 con leche viva, ni con una cifra rancia (una que no la
     explica ni el papel ni la tarifa de hoy);
  4. y RECALCULAR —el único que re-precifica— lo lleva al modo de HOY y sigue cuadrando.

LA MATRIZ: los cruces (por litro → fijo, fijo → por litro, y de vuelta en las dos
direcciones) por los cinco estados del comprobante (sin comprobante, borrador, aprobado,
pagado, con abono) por las NUEVE cosas que se le pueden hacer a una recepción de ese día:

    corregir los litros · apagar · apagar y volver a prender · borrar ·
    mover la fecha · mover la ruta · mover el transportador ·
    mover el transportador y devolverlo · anotar otra recepción del mismo día

Cada casilla arranca de un escenario NUEVO: lo que se prueba es cada operación contra
un papel recién emitido, no una cadena. La cadena larga ya está en
test_transporte_dia_fijo_la_regla.py.
"""
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import select

from app.modules.liquidaciones.models import (
    ESTADO_ANULADA,
    TIPO_TRANSPORTADOR,
    Liquidacion,
)
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQUIDACIONES = "/api/v1/liquidaciones"

# LAS TARIFAS, todas distintas a propósito: si el código leyera la que no es, las cifras
# salen disparatadas en vez de parecidas y la prueba lo grita.
FIJO = Decimal("150000")        # "A fabrica" cuando se cobra por DÍA
POR_LITRO = Decimal("242.76")   # "A fabrica" cuando se cobra por LITRO
NAPOLES = Decimal("180.00")     # la otra ruta, SIEMPRE por litro
GENERAL = Decimal("95.00")      # la general de Alex: no debe aparecer en ninguna cifra
BETO = Decimal("310.15")        # el otro transportador, por litro y sin rutas propias

EL_DIA = "2026-07-16"
OTRO_DIA = "2026-07-17"         # del mismo período: mover la fecha no suelta el día
AURELIO = Decimal("82.00")
MARLENY = Decimal("137.45")
CORREGIDO = Decimal("91.30")    # 82,00 → 91,30, la corrección del caso del dueño
GILBERTO = Decimal("96.30")     # el que se anota tarde

LITRO, DIA_FIJO = "litro", "dia_fijo"
CERO = Decimal("0")


def D(v):
    return Decimal(str(v))


def centavos(valor):
    """La misma regla del backend: al centavo, con el medio centavo para arriba."""
    return D(valor).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _post(client, h, url, cuerpo):
    r = client.post(url, json=cuerpo, headers=h)
    assert r.status_code in (200, 201), f"{url}: {r.status_code} {r.text}"
    return r.json()


def _fecha(texto: str) -> date:
    return date(*(int(x) for x in texto.split("-")))


# ===========================================================================
# EL ESCENARIO
# ===========================================================================
def _escenario(client, h, modo_fabrica, sufijo=""):
    """Alex con "A fabrica" en `modo_fabrica`, Nápoles por litro, y Beto aparte.

    `sufijo` le da nombres propios a cada casilla de la matriz: rutas, transportadores y
    proveedores son únicos por nombre dentro de una quesera, y cada operación se corre
    sobre un escenario NUEVO —lo que se prueba es cada operación contra un papel recién
    emitido, no una cadena—.
    """
    fabrica = _post(client, h, RUTAS,
                    {"nombre": f"A fabrica{sufijo}", "municipio": "Granada"})
    napoles = _post(client, h, RUTAS,
                    {"nombre": f"Napoles{sufijo}", "municipio": "Granada"})
    alex = _post(client, h, TRANSPORTADORES, {
        "nombre": f"Alex Agudelo{sufijo}",
        "valor_transporte": str(GENERAL),
        "modo_transporte": LITRO,
        "rutas": [
            {"ruta_id": fabrica["id"], "modo_transporte": modo_fabrica,
             "valor_transporte": str(FIJO if modo_fabrica == DIA_FIJO else POR_LITRO)},
            {"ruta_id": napoles["id"], "modo_transporte": LITRO,
             "valor_transporte": str(NAPOLES)},
        ],
    })
    beto = _post(client, h, TRANSPORTADORES, {
        "nombre": f"Beto Rico{sufijo}", "valor_transporte": str(BETO),
        "modo_transporte": LITRO,
    })
    proveedores = {
        nombre: _post(client, h, PROVEEDORES, {
            "nombre": f"{nombre}{sufijo}", "vereda": "La Vega", "precio_litro": "1800",
            "ruta_id": fabrica["id"]})
        for nombre in ("Aurelio", "Marleny", "Gilberto")
    }
    return {"fabrica": fabrica, "napoles": napoles, "alex": alex, "beto": beto,
            "proveedores": proveedores}


def _ponerle_el_modo(client, h, esc, modo):
    """Le cambia el MODO (y la tarifa que le corresponde) a la ruta "A fabrica"."""
    r = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "valor_transporte": str(GENERAL),
        "modo_transporte": LITRO,
        "rutas": [
            {"ruta_id": esc["fabrica"]["id"], "modo_transporte": modo,
             "valor_transporte": str(FIJO if modo == DIA_FIJO else POR_LITRO)},
            {"ruta_id": esc["napoles"]["id"], "modo_transporte": LITRO,
             "valor_transporte": str(NAPOLES)},
        ]}, headers=h)
    assert r.status_code == 200, r.text


def _recibir(client, h, esc, fecha, quien, litros_):
    return _post(client, h, RECEPCIONES, {
        "fecha": fecha,
        "proveedor_id": esc["proveedores"][quien]["id"],
        "transportador_id": esc["alex"]["id"],
        "cantidad_litros": str(litros_),
    })


def _liquidar(client, h, esc):
    """Genera el flete de la quincena y devuelve EL comprobante de ESTE Alex.

    Se escoge por el id del transportador y no "el primero que salga": la matriz corre
    varias casillas seguidas sobre la misma quesera y una corrida puede sacar de paso el
    comprobante de otro transportador (el día que se le pasó a Beto en la casilla
    anterior, por ejemplo). Colgar de la posición haría que la prueba midiera otro papel.
    """
    r = client.post(f"{LIQUIDACIONES}/generar", headers=h, json={
        "periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
        "tipo": "transportador"})
    assert r.status_code in (200, 201), r.text
    generadas = [g for g in r.json()["generadas"]
                 if g.get("transportador_id") == esc["alex"]["id"]]
    assert generadas, f"no salió comprobante de flete de Alex: {r.json()}"
    return generadas[0]


def _dejar_en(client, h, liq_id, estado):
    """Deja el comprobante en el estado que pide la casilla de la matriz."""
    if estado == "borrador":
        return
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    if estado == "aprobado":
        return
    if estado == "pagado":
        assert client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h).status_code == 200
        return
    if estado == "con abono":
        r = client.post(f"{LIQUIDACIONES}/{liq_id}/pagos", headers=h,
                        json={"fecha": EL_DIA, "valor": "1000"})
        assert r.status_code in (200, 201), r.text
        return
    raise AssertionError(f"estado desconocido: {estado}")


# ===========================================================================
# LA RADIOGRAFÍA: lo que hay que mirar después de cada operación
# ===========================================================================
class Papel:
    """El comprobante y las fotos de SUS días, leídos de la base.

    Se lee de la base y no de la respuesta del API a propósito: lo que esta prueba
    protege es la PLATA GUARDADA, que es la que después leen la contabilidad, la grilla
    y el PDF del conductor. Una respuesta puede salir buena y la base quedar mala.
    """

    def __init__(self, db_session, liq_id):
        db_session.expire_all()
        self.liq = db_session.get(Liquidacion, uuid.UUID(liq_id))
        self.renglones = [
            (d.fecha, d.ruta_id, D(d.litros), D(d.precio_litro), D(d.valor),
             d.modo_transporte, bool(d.dia_fijo_ya_cobrado))
            for d in self.liq.detalles if d.deleted_at is None
        ]
        self.fotos = {
            r.id: D(r.valor_transporte)
            for r in db_session.scalars(select(RecepcionLeche).where(
                RecepcionLeche.liquidacion_transporte_id == self.liq.id,
                RecepcionLeche.estado == "activo",
                RecepcionLeche.deleted_at.is_(None),
            )).all()
        }

    @property
    def total(self):
        return D(self.liq.valor_transporte)

    @property
    def suma_renglones(self):
        return sum((v for _, _, _, _, v, _, _ in self.renglones), CERO)

    @property
    def suma_fotos(self):
        return sum(self.fotos.values(), CERO)

    @property
    def modos(self):
        return sorted({m for _, _, _, _, _, m, _ in self.renglones})

    def __str__(self):
        líneas = " | ".join(
            f"{f} {li}L×${p}=${v} [{m}]" for f, _, li, p, v, m, _ in sorted(
                self.renglones, key=lambda x: (x[0], x[4]))
        )
        return f"${self.total} = {líneas or '(sin renglones)'}"


def _revisar_el_papel(db_session, liq_id, paso, *, vale, modo=None, fabrica_id=None):
    """LAS DOS PRIMERAS EXIGENCIAS: el comprobante vale lo que tiene que valer y cuadra.

    · su cifra grande es EXACTO la suma de sus renglones (la regla de oro);
    · y EXACTO la suma de las fotos de sus días vivos (el desglose de abajo);
    · y esa cifra es la que corresponde: la emitida, o la emitida corrida por los litros
      que de verdad cambiaron —nunca por haberle cambiado el modo a la ruta—;
    · y los renglones siguen siendo del MODO en que se emitió el papel: un comprobante
      que decía "Día completo" no amanece convertido en líneas por litro, ni al revés.
    """
    p = Papel(db_session, liq_id)
    print(f"    {paso:<46}{p}")
    assert p.total == vale, (
        f"tras «{paso}» el comprobante dice ${p.total} y tenía que decir ${vale}"
    )
    assert p.suma_renglones == vale, (
        f"tras «{paso}» los renglones suman ${p.suma_renglones} y el comprobante dice "
        f"${p.total}"
    )
    assert p.suma_fotos == vale, (
        f"tras «{paso}» las fotos de sus días suman ${p.suma_fotos} y sus renglones "
        f"${p.suma_renglones}: el desglose dejó de sumar la cifra grande"
    )
    if modo is not None:
        # EL MODO SE REVISA POR RUTA y no sobre el papel entero: si un día se movió a
        # Nápoles, el comprobante trae legítimamente los dos modos, uno por ruta. Lo que
        # se exige es que los renglones de la ruta que se cruzó sigan en el modo en que
        # se emitió el papel.
        de_fabrica = sorted({
            m for _, ruta_id, _, _, _, m, _ in p.renglones
            if fabrica_id is None or str(ruta_id) == fabrica_id
        })
        assert de_fabrica in ([], [modo]), (
            f"tras «{paso}» los renglones de «A fabrica» salieron en modo {de_fabrica} y "
            f"el papel se emitió en '{modo}': un papel no puede cambiar de forma solo "
            "porque alguien tocó una tarifa"
        )
    return p


def _revisar_las_fotos_vivas(client, h, db_session, esc, paso):
    """LA TERCERA EXIGENCIA: ninguna foto en $0,00 con leche viva, ni rancia.

    Una foto solo puede valer $0,00 si su viaje ya está cobrado en otro papel —ese día
    ya costó $150.000 y recoger un proveedor más no cuesta más— o si la tarifa que le
    aplica es de $0,00. Cualquier otro cero es leche que se recogió y que nadie va a
    pagar.

    Y "rancia" es lo contrario del cero: una cifra que no la explica NI el papel que la
    cobra NI la tarifa de hoy. Se revisa sobre las recepciones SUELTAS —las que no están
    en ningún comprobante—, porque las que están en uno ya quedaron revisadas contra su
    renglón. Una suelta tiene que valer exactamente lo que la tarifa de HOY dice: es
    justo lo que se rompía al cambiarle el modo a una ruta y dejar fotos de un fijo que
    ya no existe.
    """
    from app.modules.liquidaciones.repository import LiquidacionRepository
    from app.modules.transportadores.models import Transportador
    from app.modules.transportadores.tarifas import (
        reparto_entre_las_fotos,
        tarifa_de_transporte,
        valor_del_grupo,
    )

    db_session.expire_all()
    sueltas = list(db_session.scalars(select(RecepcionLeche).where(
        RecepcionLeche.liquidacion_transporte_id.is_(None),
        RecepcionLeche.estado == "activo",
        RecepcionLeche.deleted_at.is_(None),
    )).all())
    empresa_id = db_session.get(
        Transportador, uuid.UUID(esc["alex"]["id"])
    ).empresa_id
    repo = LiquidacionRepository(db_session, empresa_id)
    grupos: dict[tuple, list[RecepcionLeche]] = {}
    for r in sueltas:
        grupos.setdefault((r.transportador_id, r.fecha, r.ruta_id), []).append(r)
    for (transportador_id, fecha, ruta_id), grupo in grupos.items():
        transportador = db_session.get(Transportador, transportador_id)
        tarifa = tarifa_de_transporte(transportador, ruta_id)
        ya_cobrado = (fecha, ruta_id) in repo.viajes_ya_cobrados(transportador_id)
        total = valor_del_grupo(
            tarifa,
            sum((D(r.cantidad_litros) for r in grupo), CERO),
            ya_cobrado=ya_cobrado,
        )
        esperado = reparto_entre_las_fotos(
            tarifa, [(r.id, D(r.cantidad_litros)) for r in grupo], total
        )
        for r in grupo:
            foto = D(r.valor_transporte)
            assert foto == esperado[r.id], (
                f"tras «{paso}» el día suelto del {fecha} ({r.cantidad_litros} L) tiene "
                f"una foto rancia de ${foto}: con la tarifa de hoy le corresponden "
                f"${esperado[r.id]}"
            )
            if foto == CERO and D(r.cantidad_litros) > CERO:
                assert ya_cobrado or tarifa.valor == CERO, (
                    f"tras «{paso}» la recepción del {fecha} tiene {r.cantidad_litros} L "
                    "de leche viva y el flete en $0,00, y ese viaje no está cobrado en "
                    "ninguna parte: es leche que se recogió y que nadie va a pagar"
                )


def _revisar_recalcular(client, h, db_session, esc, liq_id, paso, *, modo_de_hoy):
    """LA CUARTA EXIGENCIA: Recalcular —el único que re-precifica— lleva al modo de HOY.

    Es la otra mitad de la regla: el recuadre automático conserva el papel, y el botón
    que el dueño oprime a propósito lo re-precifica. Si Recalcular no llevara al modo de
    hoy, cambiar una tarifa no tendría por dónde llegarle nunca a un día ya liquidado.

    Recalcular exige borrador (una liquidación aprobada, pagada o con abonos rebota), así
    que en esas casillas lo que se exige es EXACTAMENTE ESO: que rebote y no toque nada.
    """
    db_session.expire_all()
    estado = db_session.get(Liquidacion, uuid.UUID(liq_id)).estado
    r = client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)
    if estado != "borrador":
        assert r.status_code == 422, (
            f"tras «{paso}» Recalcular sobre una liquidación en '{estado}' respondió "
            f"{r.status_code}: solo se puede recalcular un borrador"
        )
        return
    assert r.status_code == 200, r.text
    p = Papel(db_session, liq_id)
    print(f"      {'└ RECALCULAR → el modo de hoy':<44}{p}")
    assert p.suma_renglones == p.total, (
        f"tras «{paso}» + Recalcular los renglones suman ${p.suma_renglones} y el "
        f"comprobante dice ${p.total}"
    )
    assert p.suma_fotos == p.total, (
        f"tras «{paso}» + Recalcular las fotos suman ${p.suma_fotos} y los renglones "
        f"${p.suma_renglones}"
    )
    # El modo se revisa POR RUTA y no sobre el papel entero: después de mover un día a
    # Nápoles el comprobante trae legítimamente los dos modos, uno por ruta. Lo que se
    # exige es que los renglones de "A fabrica" —la ruta que se cruzó— hayan quedado en
    # el modo que esa ruta tiene HOY.
    de_fabrica = sorted({
        m for _, ruta_id, _, _, _, m, _ in p.renglones
        if str(ruta_id) == esc["fabrica"]["id"]
    })
    if de_fabrica:
        assert de_fabrica == [modo_de_hoy], (
            f"tras «{paso}» + Recalcular los renglones de «A fabrica» quedaron en modo "
            f"{de_fabrica} y hoy esa ruta se cobra en '{modo_de_hoy}': Recalcular es el "
            "que re-precifica"
        )
    # Y dos veces seguidas no puede mover un peso.
    antes = (p.total, sorted((str(x) for x in p.renglones)))
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h).status_code == 200
    q = Papel(db_session, liq_id)
    assert (q.total, sorted((str(x) for x in q.renglones))) == antes, (
        f"tras «{paso}» Recalcular dos veces movió el papel: {antes} → "
        f"{(q.total, sorted(str(x) for x in q.renglones))}"
    )


# ===========================================================================
# LAS OPERACIONES: qué se le hace al día, y cuánto tiene que valer el papel después
# ===========================================================================
def _emitido(modo):
    """Lo que vale el comprobante recién emitido, en el modo en que se emitió."""
    return FIJO if modo == DIA_FIJO else centavos((AURELIO + MARLENY) * POR_LITRO)


def _solo_marleny(modo):
    """Lo que vale ese (día, ruta) cuando Aurelio ya no está: el fijo no se mueve."""
    return FIJO if modo == DIA_FIJO else centavos(MARLENY * POR_LITRO)


# Cada operación: qué hace, y cuánto tiene que valer el comprobante EMITIDO después.
# `vale` recibe el modo EN QUE SE EMITIÓ el papel, nunca el de hoy: ese es el punto.
def _op_corregir_litros(client, h, esc, ids):
    r = client.put(f"{RECEPCIONES}/{ids['Aurelio']}",
                   json={"cantidad_litros": str(CORREGIDO)}, headers=h)
    assert r.status_code == 200, r.text


def _vale_corregir_litros(modo):
    # DÍA FIJO: el viaje cuesta lo mismo así traiga 9 litros más.
    # POR LITRO: el renglón se rehace con los litros de hoy A LA TARIFA EMITIDA.
    #   228,75 L × $242,76 = $55.531,35  (y no los $33.367,36 del defecto)
    return FIJO if modo == DIA_FIJO else centavos((CORREGIDO + MARLENY) * POR_LITRO)


def _op_apagar(client, h, esc, ids):
    r = client.put(f"{RECEPCIONES}/{ids['Aurelio']}", json={"estado": "inactivo"},
                   headers=h)
    assert r.status_code == 200, r.text


def _op_apagar_y_prender(client, h, esc, ids):
    _op_apagar(client, h, esc, ids)
    r = client.put(f"{RECEPCIONES}/{ids['Aurelio']}", json={"estado": "activo"},
                   headers=h)
    assert r.status_code == 200, r.text


def _op_borrar(client, h, esc, ids):
    r = client.delete(f"{RECEPCIONES}/{ids['Aurelio']}", headers=h)
    assert r.status_code in (200, 204), r.text


def _op_mover_fecha(client, h, esc, ids):
    r = client.put(f"{RECEPCIONES}/{ids['Aurelio']}", json={"fecha": OTRO_DIA}, headers=h)
    assert r.status_code == 200, r.text


def _vale_mover_fecha(modo):
    # DÍA FIJO: son dos días y son dos viajes → dos fijos. Ya estaba probado y no se
    #   movió: el papel sube a $300.000 porque el camión salió dos veces.
    # POR LITRO: el papel NO se mueve —$53.273,68 repartido en dos renglones—, que es lo
    #   que antes se inflaba a $183.367,36 metiéndole un día fijo entero.
    return FIJO * 2 if modo == DIA_FIJO else _emitido(LITRO)


def _op_mover_ruta(client, h, esc, ids):
    r = client.put(f"{RECEPCIONES}/{ids['Aurelio']}",
                   json={"ruta_id": esc["napoles"]["id"]}, headers=h)
    assert r.status_code == 200, r.text


def _vale_mover_ruta(modo):
    # La ruta es la que ESCOGE la tarifa, así que cambiarla es escoger otra a propósito:
    # el día se va a Nápoles con la tarifa de Nápoles ($180 el litro) y lo que queda del
    # (día, "A fabrica") se rehace en el modo en que se emitió.
    return _solo_marleny(modo) + centavos(AURELIO * NAPOLES)


def _op_mover_transportador(client, h, esc, ids):
    r = client.put(f"{RECEPCIONES}/{ids['Aurelio']}",
                   json={"transportador_id": esc["beto"]["id"]}, headers=h)
    assert r.status_code == 200, r.text


def _op_mover_transportador_y_volver(client, h, esc, ids):
    _op_mover_transportador(client, h, esc, ids)
    r = client.put(f"{RECEPCIONES}/{ids['Aurelio']}",
                   json={"transportador_id": esc["alex"]["id"]}, headers=h)
    assert r.status_code == 200, r.text


def _op_agregar_otra(client, h, esc, ids):
    _recibir(client, h, esc, EL_DIA, "Gilberto", GILBERTO)


# (nombre, qué hace, cuánto vale después el papel emitido, si la operación toca el flete)
_OPERACIONES = (
    ("corregir litros 82,00 → 91,30", _op_corregir_litros, _vale_corregir_litros, True),
    ("apagar el día", _op_apagar, _solo_marleny, True),
    ("apagar y volver a prender", _op_apagar_y_prender, _emitido, True),
    ("borrar el día", _op_borrar, _solo_marleny, True),
    ("mover la fecha al 17/07", _op_mover_fecha, _vale_mover_fecha, True),
    ("mover la ruta a Nápoles", _op_mover_ruta, _vale_mover_ruta, True),
    ("mover el transportador a Beto", _op_mover_transportador, _solo_marleny, True),
    ("mover el transportador y volver", _op_mover_transportador_y_volver, _emitido, True),
    ("anotar leche NUEVA del mismo día", _op_agregar_otra, _emitido, False),
)

# Los cruces. Los dos últimos son el "y de vuelta": el papel emitido en un modo, la ruta
# pasada al otro y devuelta al de origen. El papel tiene que quedar exactamente como
# salió, y se hace en las dos direcciones porque no son simétricas —del fijo se conserva
# la cifra y del por litro la tarifa—.
_CRUCES = (
    ("por litro → fijo", LITRO, (DIA_FIJO,)),
    ("fijo → por litro", DIA_FIJO, (LITRO,)),
    ("por litro → fijo → por litro", LITRO, (DIA_FIJO, LITRO)),
    ("fijo → por litro → fijo", DIA_FIJO, (LITRO, DIA_FIJO)),
)

_ESTADOS = ("borrador", "aprobado", "pagado", "con abono")


@pytest.mark.parametrize("cruce,modo_emitido,pasos", _CRUCES, ids=[c[0] for c in _CRUCES])
@pytest.mark.parametrize("estado", _ESTADOS)
def test_la_matriz_del_cruce(client, base_datos, db_session, cruce, modo_emitido, pasos,
                             estado):
    """Cada casilla de la matriz: un papel emitido, la ruta cruzada, y las ocho cosas.

    El comprobante se emite en `modo_emitido`, se deja en `estado`, y la ruta se pasa por
    `pasos` (el cruce). Después se le hace a una recepción de ese día CADA una de las
    nueve cosas —cada una desde un escenario nuevo— y se exigen las cuatro cosas.

    LAS CASILLAS DE UN PAPEL PAGADO (o con abono) SON LAS MÁS IMPORTANTES y no exigen
    menos, exigen otra cosa: ahí el candado tiene que REBOTAR la operación con un 422 y
    no mover ni un peso. Esa plata ya salió de la caja contra esas cifras.
    """
    print(f"\n===== {cruce}  ·  comprobante {estado.upper()} =====")
    for numero, (etiqueta, hacer, vale, toca_el_flete) in enumerate(_OPERACIONES, 1):
        h = auth_headers(client, "admin.a")
        esc = _escenario(client, h, modo_emitido, sufijo=f" {numero}")
        ids = {
            "Aurelio": _recibir(client, h, esc, EL_DIA, "Aurelio", AURELIO)["id"],
            "Marleny": _recibir(client, h, esc, EL_DIA, "Marleny", MARLENY)["id"],
        }
        liq = _liquidar(client, h, esc)
        emitido = _emitido(modo_emitido)
        _revisar_el_papel(db_session, liq["id"], f"[{etiqueta}] emitido",
                          vale=emitido, modo=modo_emitido, fabrica_id=esc["fabrica"]["id"])
        _dejar_en(client, h, liq["id"], estado)
        for modo in pasos:
            _ponerle_el_modo(client, h, esc, modo)
        # Cruzarle el modo a la ruta NO puede mover un papel ya emitido, ni un peso, ni
        # de forma. Es la primera cosa que se revisa y ya era falsa antes del arreglo.
        _revisar_el_papel(db_session, liq["id"], f"[{etiqueta}] cruzado el modo",
                          vale=emitido, modo=modo_emitido, fabrica_id=esc["fabrica"]["id"])

        trabado = estado in ("pagado", "con abono") and toca_el_flete
        if trabado:
            # EL CANDADO: con plata entregada los siete caminos rebotan y nada se mueve.
            r = client.put(f"{RECEPCIONES}/{ids['Aurelio']}",
                           json={"cantidad_litros": str(CORREGIDO)}, headers=h)
            assert r.status_code == 422, (
                f"[{etiqueta}] con el flete {estado} el guardado tenía que rebotar y "
                f"respondió {r.status_code}: {r.text}"
            )
            _revisar_el_papel(db_session, liq["id"], f"[{etiqueta}] REBOTÓ (422)",
                              vale=emitido, modo=modo_emitido, fabrica_id=esc["fabrica"]["id"])
        else:
            hacer(client, h, esc, ids)
            _revisar_el_papel(db_session, liq["id"], f"[{etiqueta}]",
                              vale=vale(modo_emitido), modo=modo_emitido, fabrica_id=esc["fabrica"]["id"])
        _revisar_las_fotos_vivas(client, h, db_session, esc, etiqueta)
        _revisar_recalcular(client, h, db_session, esc, liq["id"], etiqueta,
                            modo_de_hoy=pasos[-1])


# ===========================================================================
# LA FILA "SIN COMPROBANTE" DE LA MATRIZ
# ===========================================================================
@pytest.mark.parametrize("cruce,modo_inicial,pasos", _CRUCES, ids=[c[0] for c in _CRUCES])
def test_la_matriz_sin_comprobante(client, base_datos, db_session, cruce, modo_inicial,
                                   pasos):
    """El mismo cruce y las mismas nueve operaciones, sobre días que no están en ningún papel.

    Es la fila que falta de la matriz y la que manda distinto: SIN comprobante no hay
    nada que conservar, así que acá SÍ manda la tarifa de HOY —es el caso 2 de "cuándo se
    vuelve a calcular la cifra del flete"—. Lo que se exige después de cada operación es
    que TODA la leche viva tenga exactamente la foto que la tarifa de hoy le da: ni un
    cero con leche viva, ni una cifra rancia de una tarifa que ya no existe.

    Es la red del punto 4: pasar la ruta de fijo a por litro dejaba fotos que eran la
    parte de un fijo que ya no existe, y la grilla las seguía sumando.
    """
    print()
    print(f"===== {cruce}  ·  SIN COMPROBANTE =====")
    for numero, (etiqueta, hacer, _, _) in enumerate(_OPERACIONES, 1):
        h = auth_headers(client, "admin.a")
        esc = _escenario(client, h, modo_inicial, sufijo=f" s{numero}")
        ids = {
            "Aurelio": _recibir(client, h, esc, EL_DIA, "Aurelio", AURELIO)["id"],
            "Marleny": _recibir(client, h, esc, EL_DIA, "Marleny", MARLENY)["id"],
        }
        for modo in pasos:
            _ponerle_el_modo(client, h, esc, modo)
        _revisar_las_fotos_vivas(client, h, db_session, esc, f"{etiqueta} · cruzado")
        hacer(client, h, esc, ids)
        _revisar_las_fotos_vivas(client, h, db_session, esc, etiqueta)
        # Y la cuenta a mano del día, que es lo que el dueño mira en la grilla.
        db_session.expire_all()
        vivas = [
            r for r in db_session.scalars(select(RecepcionLeche).where(
                RecepcionLeche.fecha == _fecha(EL_DIA),
                RecepcionLeche.ruta_id == uuid.UUID(esc["fabrica"]["id"]),
                RecepcionLeche.transportador_id == uuid.UUID(esc["alex"]["id"]),
                RecepcionLeche.estado == "activo",
                RecepcionLeche.deleted_at.is_(None),
            )).all()
        ]
        litros_ = sum((D(r.cantidad_litros) for r in vivas), CERO)
        a_mano = (
            FIJO if (pasos[-1] == DIA_FIJO and vivas)
            else centavos(litros_ * POR_LITRO) if pasos[-1] == LITRO
            else CERO
        )
        del_dia = sum((D(r.valor_transporte) for r in vivas), CERO)
        print(f"    {etiqueta:<46}{litros_} L → ${del_dia}   (a mano ${a_mano})")
        assert del_dia == a_mano, (
            f"tras «{etiqueta}» el día suelto del {EL_DIA} vale ${del_dia} y con la "
            f"tarifa de hoy son ${a_mano}"
        )


# ===========================================================================
# LAS CIFRAS DEL ENUNCIADO, escritas una por una para que queden fijas
# ===========================================================================
def test_las_cifras_del_defecto_critico_ya_no_pasan(client, base_datos, db_session):
    """$53.273,68 → $33.367,36 con la recepción en $0,00: la que se aprobaba y se pagaba.

    A MANO, y es la cuenta que el dueño hace:

        emitido POR LITRO   219,45 L × $242,76 = $53.273,68
                            (Aurelio $19.906,32 + Marleny $33.367,36)

        la ruta pasa a DÍA FIJO $150.000 y a Aurelio se le corrigen 82,00 → 91,30 L

        ANTES:  la recepción de Aurelio quedaba en $0,00 (foto escrita con el modo de
                HOY, que es fijo) y el comprobante —armado POR LITRO— caía a $33.367,36.
                El PDF le imprimía al conductor "91,3 L $0 $0". Le faltaban $22.163,99
                contra su cuenta por litro y $116.632,64 contra el fijo de hoy.
        AHORA:  228,75 L × $242,76 = $55.531,35, y las fotos $22.163,99 + $33.367,36.
                El renglón se movió SOLO por los litros y SOLO en el modo en que se
                emitió; la tarifa del papel sigue siendo la que el conductor firmó.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, LITRO)
    aurelio = _recibir(client, h, esc, EL_DIA, "Aurelio", AURELIO)
    _recibir(client, h, esc, EL_DIA, "Marleny", MARLENY)
    liq = _liquidar(client, h, esc)

    print("\n===== EL DEFECTO CRÍTICO, con sus cifras =====")
    p = _revisar_el_papel(db_session, liq["id"], "emitido por litro",
                          vale=D("53273.68"), modo=LITRO, fabrica_id=esc["fabrica"]["id"])
    assert sorted(p.fotos.values()) == [D("19906.32"), D("33367.36")]

    _ponerle_el_modo(client, h, esc, DIA_FIJO)
    r = client.put(f"{RECEPCIONES}/{aurelio['id']}",
                   json={"cantidad_litros": str(CORREGIDO)}, headers=h)
    assert r.status_code == 200, r.text

    p = _revisar_el_papel(db_session, liq["id"], "corregidos 82,00 → 91,30 L",
                          vale=D("55531.35"), modo=LITRO, fabrica_id=esc["fabrica"]["id"])
    assert sorted(p.fotos.values()) == [D("22163.99"), D("33367.36")], (
        f"las fotos quedaron en {sorted(str(x) for x in p.fotos.values())}"
    )
    assert D("33367.36") not in (p.total,), "el defecto vuelve: el papel cayó a $33.367,36"
    assert CERO not in p.fotos.values(), (
        "una recepción con 91,30 L de leche viva quedó en $0,00: es la foto que el PDF "
        "imprimía como «91,3 L $0 $0»"
    )
    # Y el renglón se verifica multiplicando, que es como se verifica un renglón por litro.
    fecha, _, litros_, precio, valor, modo, _ = p.renglones[0]
    assert litros_ == CORREGIDO + MARLENY
    assert precio == POR_LITRO
    assert centavos(litros_ * precio) == valor == D("55531.35")


def test_las_cifras_del_defecto_de_la_fecha_ya_no_pasan(client, base_datos, db_session):
    """$53.273,68 → $183.367,36 (+$130.093,68): el día fijo que se inyectaba solo.

    A MANO:

        emitido POR LITRO   219,45 L × $242,76 = $53.273,68
        la ruta pasa a DÍA FIJO $150.000 y a Aurelio se le corrige la FECHA al 17/07

        ANTES:  al 17/07 le nacía un renglón "Día completo $150.000" —el modo de HOY—
                dentro de un papel armado por litro: $183.367,36, o sea $130.093,68 de
                más, sin que nadie oprimiera Recalcular.
        AHORA:  el papel sigue valiendo $53.273,68 y se parte en los dos días que de
                verdad tiene, los dos POR LITRO y a la tarifa con que se emitió:
                    16/07  137,45 L × $242,76 = $33.367,36
                    17/07   82,00 L × $242,76 = $19.906,32
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, LITRO)
    aurelio = _recibir(client, h, esc, EL_DIA, "Aurelio", AURELIO)
    _recibir(client, h, esc, EL_DIA, "Marleny", MARLENY)
    liq = _liquidar(client, h, esc)

    print("\n===== EL DEFECTO DE LA FECHA, con sus cifras =====")
    _revisar_el_papel(db_session, liq["id"], "emitido por litro",
                      vale=D("53273.68"), modo=LITRO, fabrica_id=esc["fabrica"]["id"])
    _ponerle_el_modo(client, h, esc, DIA_FIJO)
    r = client.put(f"{RECEPCIONES}/{aurelio['id']}", json={"fecha": OTRO_DIA}, headers=h)
    assert r.status_code == 200, r.text

    p = _revisar_el_papel(db_session, liq["id"], "movida la fecha al 17/07",
                          vale=D("53273.68"), modo=LITRO, fabrica_id=esc["fabrica"]["id"])
    assert p.total != D("183367.36"), "el defecto vuelve: el papel se infló a $183.367,36"
    por_dia = {f: (li, pr, v) for f, _, li, pr, v, _, _ in p.renglones}
    assert por_dia[_fecha(EL_DIA)] == (MARLENY, POR_LITRO, D("33367.36")), por_dia
    assert por_dia[_fecha(OTRO_DIA)] == (AURELIO, POR_LITRO, D("19906.32")), por_dia


def test_el_dia_que_sale_y_vuelve_no_regresa_en_cero(client, base_datos, db_session):
    """El viaje fijo ya cobrado: 137,45 L = $150.000 y 82,00 L = $0,00. Ya no.

    A MANO:

        el 16/07 en la ruta fija: Aurelio 82,00 L y Marleny 137,45 L
        el viaje vale $150.000, repartido $56.049,21 / $93.950,79

        a Aurelio le cambian el transportador (anotaron mal quién recogió) y se lo
        devuelven un minuto después

        ANTES:  Aurelio volvía SUELTO. Su viaje ya estaba cobrado, así que su foto
                quedaba en $0,00 con la leche viva, y Marleny cargaba el fijo entero:
                la grilla decía que recoger 137,45 L costó $150.000 y recoger 82,00 L
                no costó nada. Y por el camino, mientras estuvo con Beto —que cobra
                $310,15 POR LITRO— su foto era de $150.000: un viaje fijo de un
                transportador que no cobra por viaje.
        AHORA:  con Beto vale 82,00 × $310,15 = $25.432,30, y al volver entra otra vez
                al papel que cobra ese viaje: $56.049,21 / $93.950,79 y el viaje sigue
                valiendo $150.000. No se cobra un peso más; se reparte entre quienes de
                verdad vinieron en él.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, DIA_FIJO)
    aurelio = _recibir(client, h, esc, EL_DIA, "Aurelio", AURELIO)
    _recibir(client, h, esc, EL_DIA, "Marleny", MARLENY)
    liq = _liquidar(client, h, esc)

    print("\n===== EL DÍA QUE SALE Y VUELVE =====")
    p = _revisar_el_papel(db_session, liq["id"], "emitido por día fijo",
                          vale=FIJO, modo=DIA_FIJO, fabrica_id=esc["fabrica"]["id"])
    assert sorted(p.fotos.values()) == [D("56049.21"), D("93950.79")]

    assert client.put(f"{RECEPCIONES}/{aurelio['id']}",
                      json={"transportador_id": esc["beto"]["id"]},
                      headers=h).status_code == 200
    _revisar_el_papel(db_session, liq["id"], "Aurelio se va con Beto",
                      vale=FIJO, modo=DIA_FIJO, fabrica_id=esc["fabrica"]["id"])
    db_session.expire_all()
    con_beto = D(db_session.get(RecepcionLeche, uuid.UUID(aurelio["id"])).valor_transporte)
    assert con_beto == centavos(AURELIO * BETO) == D("25432.30"), (
        f"con Beto el día vale ${con_beto} y la cuenta a mano da "
        f"{AURELIO} × ${BETO} = ${centavos(AURELIO * BETO)}. Un fijo de $150.000 acá "
        "sería el viaje de OTRO transportador"
    )

    assert client.put(f"{RECEPCIONES}/{aurelio['id']}",
                      json={"transportador_id": esc["alex"]["id"]},
                      headers=h).status_code == 200
    p = _revisar_el_papel(db_session, liq["id"], "y vuelve con Alex",
                          vale=FIJO, modo=DIA_FIJO, fabrica_id=esc["fabrica"]["id"])
    assert sorted(p.fotos.values()) == [D("56049.21"), D("93950.79")], (
        f"al volver las fotos quedaron en {sorted(str(x) for x in p.fotos.values())}: "
        "82,00 L en $0,00 con la leche viva es el defecto"
    )
    assert len(p.renglones) == 1 and p.renglones[0][2] == AURELIO + MARLENY


def test_cambiarle_el_modo_a_la_ruta_rehace_las_fotos_de_los_dias_sueltos(
    client, base_datos, db_session
):
    """Las fotos rancias de la grilla: $150.000 en pantalla y $53.273,68 por cobrar.

    A MANO, con dos días SUELTOS (sin comprobante todavía):

        con la ruta en DÍA FIJO $150.000:  82,00 L → $56.049,21 y 137,45 L → $93.950,79
        el dueño la pasa a POR LITRO $242,76

        ANTES:  las fotos se quedaban con la parte de un fijo que ya no existe, y la
                grilla de la quincena seguía sumando $150.000 hasta que alguien generara
                o recalculara: $96.726,32 de más sobre los $53.273,68 que de verdad se
                van a cobrar.
        AHORA:  se rehacen enseguida —82,00 × $242,76 = $19.906,32 y 137,45 × $242,76 =
                $33.367,36— y la grilla dice $53.273,68.

    Y SOLO CON EL MODO: corregir la CIFRA de una tarifa por litro sigue llegando por
    donde siempre (guardar el día, generar, recalcular), que es comportamiento probado.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, DIA_FIJO)
    _recibir(client, h, esc, EL_DIA, "Aurelio", AURELIO)
    _recibir(client, h, esc, EL_DIA, "Marleny", MARLENY)

    def grilla():
        r = client.get(f"{RECEPCIONES}/grilla/quincena", headers=h,
                       params={"desde": "2026-07-16", "hasta": "2026-07-31"})
        assert r.status_code == 200, r.text
        return D(r.json()["total_transporte"])

    print("\n===== LAS FOTOS RANCIAS DE LA GRILLA =====")
    print(f"  con el día fijo la grilla dice ${grilla()}")
    assert grilla() == FIJO

    _ponerle_el_modo(client, h, esc, LITRO)
    a_mano = centavos((AURELIO + MARLENY) * POR_LITRO)
    print(f"  pasada a por litro dice     ${grilla()}   (a mano: "
          f"{AURELIO + MARLENY} L × ${POR_LITRO} = ${a_mano})")
    assert grilla() == a_mano == D("53273.68"), (
        f"la grilla quedó en ${grilla()} y con la tarifa de hoy son ${a_mano}: "
        f"${grilla() - a_mano} de fotos rancias"
    )
    db_session.expire_all()
    fotos = sorted(D(r.valor_transporte) for r in db_session.scalars(
        select(RecepcionLeche).where(RecepcionLeche.deleted_at.is_(None))).all())
    assert fotos == [D("19906.32"), D("33367.36")], fotos

    # Y de vuelta al fijo: el viaje vuelve a valer $150.000, repartido.
    _ponerle_el_modo(client, h, esc, DIA_FIJO)
    print(f"  y de vuelta al día fijo     ${grilla()}")
    assert grilla() == FIJO
    db_session.expire_all()
    fotos = sorted(D(r.valor_transporte) for r in db_session.scalars(
        select(RecepcionLeche).where(RecepcionLeche.deleted_at.is_(None))).all())
    assert fotos == [D("56049.21"), D("93950.79")], fotos


def test_el_viaje_ya_cobrado_por_litro_no_se_vuelve_a_cobrar_como_fijo(
    client, base_datos, db_session
):
    """$53.273,68 + $150.000 = $203.273,68 por UN viaje. La quinta cara del mismo cruce.

    A MANO:

        emitido POR LITRO   219,45 L × $242,76 = $53.273,68
        la ruta pasa a DÍA FIJO $150.000
        y alguien anota TARDE la leche de un tercer proveedor de ESE MISMO día (96,30 L)

        ANTES:  esa leche formaba un grupo fijo suelto que valía el viaje completo,
                $150.000, y el mismo viaje del 16/07 quedaba cobrado $203.273,68 entre
                los dos papeles. Un renglón POR LITRO no reservaba el día.
        AHORA:  el viaje es el mismo lo hayan cobrado por litro o por día: ya está en un
                papel, así que la leche anotada tarde entra en $0,00 —recoger un
                proveedor más en un viaje que ya se hizo no cuesta más— y el comprobante
                emitido no se mueve.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, LITRO)
    _recibir(client, h, esc, EL_DIA, "Aurelio", AURELIO)
    _recibir(client, h, esc, EL_DIA, "Marleny", MARLENY)
    liq = _liquidar(client, h, esc)

    print("\n===== EL VIAJE COBRADO POR LITRO NO SE VUELVE A COBRAR COMO FIJO =====")
    _revisar_el_papel(db_session, liq["id"], "emitido por litro",
                      vale=D("53273.68"), modo=LITRO, fabrica_id=esc["fabrica"]["id"])
    _ponerle_el_modo(client, h, esc, DIA_FIJO)
    tarde = _recibir(client, h, esc, EL_DIA, "Gilberto", GILBERTO)

    _revisar_el_papel(db_session, liq["id"], "anotada leche tarde",
                      vale=D("53273.68"), modo=LITRO, fabrica_id=esc["fabrica"]["id"])
    db_session.expire_all()
    suelta = db_session.get(RecepcionLeche, uuid.UUID(tarde["id"]))
    assert D(suelta.valor_transporte) == CERO, (
        f"la leche anotada tarde entró con ${D(suelta.valor_transporte)} de flete: el "
        f"viaje del {EL_DIA} ya está cobrado en un papel y cobrarlo otra vez como día "
        f"fijo lo dejaría en ${D('53273.68') + FIJO}"
    )
    r = client.get(f"{RECEPCIONES}/grilla/quincena", headers=h,
                   params={"desde": "2026-07-16", "hasta": "2026-07-31"})
    total = D(r.json()["total_transporte"])
    print(f"  el viaje completo cuesta ${total} (y no ${D('53273.68') + FIJO})")
    assert total == D("53273.68")


def test_ningun_comprobante_de_flete_queda_descuadrado_en_la_matriz(
    client, base_datos, db_session
):
    """LA RED DE MÁS AFUERA: cada comprobante suma lo suyo, pase lo que pase.

    Corre los cuatro cruces seguidos sobre la misma quesera —sin limpiar entre uno y
    otro, que es como pasa de verdad— y al final exige de CADA comprobante de flete vivo
    que su cifra grande sea exacto la suma de sus renglones y exacto la suma de las fotos
    de sus días vivos. Un arreglo que deje el 16/07 perfecto y descuadre un total no es
    un arreglo.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, LITRO)
    ids = {
        "Aurelio": _recibir(client, h, esc, EL_DIA, "Aurelio", AURELIO)["id"],
        "Marleny": _recibir(client, h, esc, EL_DIA, "Marleny", MARLENY)["id"],
    }
    liq = _liquidar(client, h, esc)
    print("\n===== LA RED DE MÁS AFUERA =====")
    for modo in (DIA_FIJO, LITRO, DIA_FIJO, LITRO):
        _ponerle_el_modo(client, h, esc, modo)
        for etiqueta, hacer, _, _ in _OPERACIONES:
            if etiqueta in ("borrar el día", "anotar leche NUEVA del mismo día"):
                continue
            hacer(client, h, esc, ids)
            _exigir_todos_cuadrados(db_session, f"{modo} · {etiqueta}")
            _revisar_las_fotos_vivas(client, h, db_session, esc, f"{modo} · {etiqueta}")
        assert client.post(f"{LIQUIDACIONES}/{liq['id']}/recalcular",
                           headers=h).status_code == 200
        _exigir_todos_cuadrados(db_session, f"{modo} · recalcular")


def _exigir_todos_cuadrados(db_session, paso):
    db_session.expire_all()
    for liq in db_session.scalars(select(Liquidacion).where(
        Liquidacion.tipo == TIPO_TRANSPORTADOR,
        Liquidacion.estado != ESTADO_ANULADA,
        Liquidacion.deleted_at.is_(None),
    )).all():
        renglones = sum((D(d.valor) for d in liq.detalles if d.deleted_at is None), CERO)
        fotos = sum((D(r.valor_transporte) for r in db_session.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.liquidacion_transporte_id == liq.id,
                RecepcionLeche.estado == "activo",
                RecepcionLeche.deleted_at.is_(None),
            )).all()), CERO)
        assert D(liq.valor_transporte) == renglones, (
            f"tras «{paso}» el comprobante {str(liq.id)[:8]} dice "
            f"${D(liq.valor_transporte)} y sus renglones suman ${renglones}"
        )
        assert renglones == fotos, (
            f"tras «{paso}» los renglones del comprobante {str(liq.id)[:8]} suman "
            f"${renglones} y las fotos de sus días ${fotos}"
        )
