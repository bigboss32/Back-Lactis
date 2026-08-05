"""EL REPARTO DEL FLETE AL CENTAVO, y los tres defectos que salieron persiguiéndolo.

La regla dice: el renglón de un (día, ruta) es UNO —litros del día × tarifa,
redondeado UNA sola vez— y esa plata se REPARTE entre las recepciones del día para
que las fotos sumen exacto el renglón.

Estas pruebas NO comprueban que la funcionalidad sirva (eso ya lo hacen
test_liquidacion_flete_por_ruta.py y test_liquidacion_flete_ruta_cuadre.py). Buscan lo
contrario: el peso que el dueño no puede reproducir con la calculadora. Y
persiguiéndolo salieron tres defectos que ya están corregidos y que estas pruebas
dejan clavados:

  1. los DECIMALES de la recepción: los litros y el precio por litro entraban con
     tres decimales en columnas que guardan dos, y la plata se calculaba con el valor
     crudo (bloques 1 y 2);
  2. un día APAGADO seguía cobrándose en los dos comprobantes (bloque 4);
  3. y el flete informativo del comprobante del PROVEEDOR quedaba con las fotos de
     antes del reparto (bloque 3).

Cifras feas a propósito: tarifas $242,76 · $317,53 · $1.833,33 y litros 44,23 ·
82,48 · 137,45 · 103,73 · 0,5.
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import _reparto_del_flete
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQUIDACIONES = "/api/v1/liquidaciones"

NAPOLES = Decimal("242.76")
MIRA_VALLE = Decimal("317.53")
GENERAL = Decimal("1833.33")
CENT = Decimal("0.01")


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(CENT, rounding=ROUND_HALF_UP)


def _crear(client, h, url, payload):
    r = client.post(url, json=payload, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _escenario(client, h):
    nap = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    mv = _crear(client, h, RUTAS, {"nombre": "Mira Valle", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo",
        "valor_transporte": str(GENERAL),
        "rutas": [
            {"ruta_id": nap["id"], "valor_transporte": str(NAPOLES)},
            {"ruta_id": mv["id"], "valor_transporte": str(MIRA_VALLE)},
        ],
    })
    prov = {}
    for nombre, ruta in (("Aurelio", nap), ("Marleny", nap), ("Gilberto", nap),
                         ("Rosalba", nap), ("Hernando", mv)):
        prov[nombre] = _crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "x", "precio_litro": "1800", "ruta_id": ruta["id"]})
    return {"nap": nap, "mv": mv, "alex": alex, "prov": prov}


def _recibir(client, h, esc, fecha, quien, litros, **extra):
    payload = {
        "fecha": fecha,
        "proveedor_id": esc["prov"][quien]["id"],
        "transportador_id": esc["alex"]["id"],
        "cantidad_litros": litros,
    }
    payload.update(extra)
    return _crear(client, h, RECEPCIONES, payload)


def _generar(client, h, tipo="transportador", inicio="2026-07-16", fin="2026-07-31"):
    r = client.post(f"{LIQUIDACIONES}/generar",
                    json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo},
                    headers=h)
    assert r.status_code in (200, 201), r.text
    assert r.json()["generadas"], "no se genero liquidacion"
    return r.json()["generadas"]


def _leer(client, h, liq_id):
    r = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _recepciones_del_flete(db_session, liq_id):
    return db_session.scalars(
        select(RecepcionLeche).where(
            RecepcionLeche.liquidacion_transporte_id == uuid.UUID(str(liq_id)),
            RecepcionLeche.deleted_at.is_(None),
        )
    ).all()


def _suma_fotos(db_session, liq_id):
    return sum((D(f.valor_transporte) for f in _recepciones_del_flete(db_session, liq_id)), D(0))


# ===========================================================================
# 1. LITROS DE TRES DECIMALES: lo guardado y lo calculado tienen que ser lo mismo
# ===========================================================================
def test_litros_de_tres_decimales_se_redondean_a_los_que_caben_en_la_columna(client, base_datos, db_session):
    """Entran 44,235 L en una columna Numeric(12,2): la foto del flete tiene que
    seguir siendo litros GUARDADOS × tarifa.

    El defecto que esto cierra: `cantidad_litros` era `Field(gt=0)` sin
    `decimal_places`, así que la columna guardaba 44,23 y el flete se calculaba con
    44,235 -> $10.738,49, cuando 44,23 × 242,76 = $10.737,27. $1,22 en un solo día, y
    el dueño multiplica a mano.

    Y el daño de rebote era peor que el peso: como ninguna tarifa de dos decimales
    explicaba esa foto, el comprobante caía en `_renglones_de_ultimo_recurso` e
    imprimía una tarifa inventada más una línea de 0,01 L, cifras que el dueño nunca
    ha visto (el que sigue).

    El arreglo es el patrón `Kilos` de reventa: un BeforeValidator que redondea la
    entrada a dos decimales (ROUND_HALF_UP), así lo validado == lo guardado == lo
    calculado. La tarifa ya estaba protegida así (`transportadores/schemas.py`); era
    la recepción la que había quedado sin candado.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    creada = _recibir(client, h, esc, "2026-07-20", "Aurelio", "44.235")
    db_session.expire_all()
    fila = db_session.get(RecepcionLeche, uuid.UUID(creada["id"]))

    litros_guardados = D(fila.cantidad_litros)
    a_mano = centavos(litros_guardados * NAPOLES)
    assert D(fila.valor_transporte) == a_mano, (
        f"la recepcion guarda {litros_guardados} L (le entraron "
        f"{creada['cantidad_litros']}) y el flete quedo en ${fila.valor_transporte}; "
        f"a mano {litros_guardados} L x ${NAPOLES} = ${a_mano} "
        f"(dif ${D(fila.valor_transporte) - a_mano})"
    )


def test_litros_de_tres_decimales_no_ensucian_el_comprobante_con_tarifas_inventadas(
    client, base_datos, db_session
):
    """El rebote del defecto anterior, en el papel que firma el conductor."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, "2026-07-20", "Aurelio", "44.235")
    liq = _leer(client, h, _generar(client, h)[0]["id"])
    renglones = [(d["litros"], d["precio_litro"], d["valor"]) for d in liq["detalles"]]

    assert len(liq["detalles"]) == 1, (
        f"un solo dia y una sola ruta tenian que dar UN renglon y salieron "
        f"{len(liq['detalles'])}: {renglones}"
    )
    assert D(liq["detalles"][0]["precio_litro"]) == NAPOLES, (
        f"el comprobante imprime la tarifa ${liq['detalles'][0]['precio_litro']} "
        f"cuando la de Napoles es ${NAPOLES}; renglones: {renglones}"
    )


# ===========================================================================
# 2. PRECIO POR LITRO DE TRES DECIMALES: el renglón del PROVEEDOR no cuadra
# ===========================================================================
def test_precio_litro_de_tres_decimales_no_rompe_el_renglon_del_proveedor(
    client, base_datos, db_session
):
    """El mismo hueco del schema, ahora en la plata de la leche.

    `precio_litro` tampoco limitaba los decimales: la columna guardaba $1.800,01 y el
    valor bruto se calculaba con $1.800,005, así que el renglón decía 137,45 ×
    $1.800,01 y el valor era $247.410,69 en vez de $247.411,37. Litros × precio TIENE
    que dar el valor del renglón: es exactamente la cuenta que el productor hace con
    la calculadora. Mismo arreglo (redondeo en la entrada), y también en las
    bonificaciones y los descuentos, que tenían el mismo agujero."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, "2026-07-20", "Aurelio", "137.45", precio_litro="1800.005")
    liq = _leer(client, h, _generar(client, h, tipo="proveedor")[0]["id"])

    fallas = []
    for d in liq["detalles"]:
        l_, p, v = D(d["litros"]), D(d["precio_litro"]), D(d["valor"])
        if centavos(l_ * p) != v:
            fallas.append(
                f"{d['fecha']}: {l_} L x ${p} = ${centavos(l_ * p)} pero el renglon "
                f"dice ${v} (dif ${centavos(l_ * p) - v})"
            )
    assert not fallas, "el renglon del proveedor no se reproduce a mano:\n" + "\n".join(fallas)


# ===========================================================================
# 3. EL FLETE INFORMATIVO DEL COMPROBANTE DEL PROVEEDOR
# ===========================================================================
def test_el_flete_del_comprobante_del_proveedor_es_la_suma_de_sus_dias(
    client, base_datos, db_session
):
    """Generar "ambos" tiene que dejar el flete del proveedor cuadrado con sus días.

    `liquidaciones.valor_transporte` en la del PROVEEDOR es informativa (no entra en
    el valor total ni en el PDF) pero sale en la pantalla. El defecto: se armaba
    ANTES de las de transportador, y el reparto del flete MUEVE las fotos
    (`recepciones.valor_transporte`), así que la columna quedaba con las fotos de
    antes del reparto y se corregía sola —sin que nadie tocara nada— en el siguiente
    recuadre. Una cifra que se mueve sin causa visible.

    Se arregló invirtiendo el orden: el flete se genera primero y la leche después
    (la respuesta sigue saliendo con las de proveedor primero).

    Cifras: 44,23 L + 82,48 L en Napoles a $242,76 (fotos $10.737,27 + $20.022,84 =
    $30.760,11; el renglón, 126,71 L × $242,76 = $30.760,12, así que el reparto le
    mueve un centavo a la foto de Marleny: $20.022,85).
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, "2026-07-20", "Aurelio", "44.23")
    _recibir(client, h, esc, "2026-07-20", "Marleny", "82.48")
    _generar(client, h, tipo="ambos")
    db_session.expire_all()

    fallas = []
    for liq in db_session.scalars(
        select(Liquidacion).where(
            Liquidacion.tipo == "proveedor", Liquidacion.deleted_at.is_(None)
        )
    ).all():
        recs = db_session.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.liquidacion_id == liq.id, RecepcionLeche.deleted_at.is_(None)
            )
        ).all()
        suma = sum((D(r.valor_transporte) for r in recs), D(0))
        if D(liq.valor_transporte) != suma:
            fallas.append(
                f"{liq.proveedor.nombre}: el comprobante dice flete "
                f"${liq.valor_transporte} pero sus recepciones suman ${suma} "
                f"(dif ${D(liq.valor_transporte) - suma})"
            )
    assert not fallas, (
        "el flete del comprobante del proveedor no es la suma de sus dias:\n"
        + "\n".join(fallas)
    )


# ===========================================================================
# 4. UN DÍA APAGADO NO SE LE COBRA A NADIE
# ===========================================================================
def test_dia_apagado_sale_del_comprobante_del_transportador(
    client, base_datos, db_session
):
    """Apagar un día cuyo flete está en borrador lo saca del comprobante.

    El defecto: `_generar_transportadores` filtra `estado == "activo"` pero el
    recálculo (`_recepciones_transporte_de`) NO lo filtraba. Apagar el día lo sacaba
    de la grilla de recepciones y del costo de transporte de contabilidad (que sí
    filtra activo) y lo dejaba cobrado en el comprobante del transportador: se le
    seguían pagando $10.737,27 de un día que nadie más contaba.

    Un día apagado DESPUÉS de pagado no llega acá: `estado` está en los campos que
    traba el candado, así que el PUT rebota con 422 (ver
    test_liquidacion_flete_pagado.py). Una liquidación pagada conserva su día y su
    cifra, que es lo correcto: esa plata ya salió."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    apagado = _recibir(client, h, esc, "2026-07-20", "Aurelio", "44.23")
    _recibir(client, h, esc, "2026-07-21", "Marleny", "82.48")
    liq_id = _generar(client, h)[0]["id"]

    r = client.put(f"{RECEPCIONES}/{apagado['id']}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()

    liq = _leer(client, h, liq_id)
    activos = sum(
        (D(f.valor_transporte) for f in _recepciones_del_flete(db_session, liq_id)
         if f.estado == "activo"),
        D(0),
    )
    fechas = sorted(d["fecha"] for d in liq["detalles"])
    assert D(liq["valor_transporte"]) == activos, (
        f"el comprobante cobra ${liq['valor_transporte']} pero los dias ACTIVOS "
        f"suman ${activos} (dif ${D(liq['valor_transporte']) - activos}); los "
        f"renglones son {fechas} e incluyen el dia apagado 2026-07-20"
    )


def test_dia_apagado_sale_del_comprobante_del_proveedor(
    client, base_datos, db_session
):
    """La misma raíz, en la plata del productor: `_recepciones_de` tampoco filtraba
    `estado == "activo"`, aunque `sin_liquidar` —el que genera— sí.

    Apagar un día ya liquidado dejaba al productor cobrando 126,71 L cuando solo le
    quedaban 82,48 L activos: $79.614 de leche que la grilla ya no muestra."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    apagado = _recibir(client, h, esc, "2026-07-20", "Aurelio", "44.23")
    _recibir(client, h, esc, "2026-07-21", "Aurelio", "82.48")
    liq_id = _generar(client, h, tipo="proveedor")[0]["id"]

    r = client.put(f"{RECEPCIONES}/{apagado['id']}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()

    liq = _leer(client, h, liq_id)
    activos = db_session.scalars(
        select(RecepcionLeche).where(
            RecepcionLeche.liquidacion_id == uuid.UUID(liq_id),
            RecepcionLeche.estado == "activo",
            RecepcionLeche.deleted_at.is_(None),
        )
    ).all()
    litros_activos = sum((D(f.cantidad_litros) for f in activos), D(0))
    neto_activos = sum((D(f.valor_neto) for f in activos), D(0))
    assert D(liq["total_litros"]) == litros_activos and D(liq["valor_total"]) == neto_activos, (
        f"el comprobante paga {liq['total_litros']} L por ${liq['valor_total']} pero "
        f"los dias ACTIVOS son {litros_activos} L por ${neto_activos}; los renglones "
        f"son {sorted(d['fecha'] for d in liq['detalles'])} e incluyen el dia apagado"
    )


def test_prender_otra_vez_el_dia_lo_devuelve_a_su_comprobante(client, base_datos):
    """El día apagado NO se suelta de su liquidación, y por eso vuelve solo.

    Es la otra mitad de la decisión: al apagarlo se le deja la marca
    (`liquidacion_transporte_id`) puesta. Si se le soltara, el día quedaría suelto y
    una generación posterior lo volvería a liquidar —cobrándoselo dos veces si la
    primera ya se pagó—. Con la marca puesta, prenderlo lo devuelve a SU comprobante
    y el total queda otra vez en la cuenta del dueño.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia = _recibir(client, h, esc, "2026-07-20", "Aurelio", "44.23")
    _recibir(client, h, esc, "2026-07-21", "Marleny", "82.48")
    liq_id = _generar(client, h)[0]["id"]
    completo = centavos(D("44.23") * NAPOLES) + centavos(D("82.48") * NAPOLES)
    assert D(_leer(client, h, liq_id)["valor_transporte"]) == completo

    assert client.put(
        f"{RECEPCIONES}/{dia['id']}", json={"estado": "inactivo"}, headers=h
    ).status_code == 200
    solo_uno = _leer(client, h, liq_id)
    assert D(solo_uno["valor_transporte"]) == centavos(D("82.48") * NAPOLES)
    assert len(solo_uno["detalles"]) == 1

    assert client.put(
        f"{RECEPCIONES}/{dia['id']}", json={"estado": "activo"}, headers=h
    ).status_code == 200
    de_vuelta = _leer(client, h, liq_id)
    assert D(de_vuelta["valor_transporte"]) == completo, (
        f"al prender el dia otra vez el comprobante quedo en "
        f"{de_vuelta['valor_transporte']} y tenia que volver a ${completo}"
    )
    assert len(de_vuelta["detalles"]) == 2
    # Y no se generó una segunda liquidación por el día que estuvo apagado.
    otra = client.post(
        f"{LIQUIDACIONES}/generar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador"},
        headers=h,
    )
    assert otra.json()["generadas"] == [], (
        f"el dia apagado se volvio a liquidar aparte: {otra.json()}"
    )


def test_un_estado_que_no_existe_no_apaga_el_dia_en_silencio(client, base_datos):
    """El estado del día solo acepta 'activo' e 'inactivo'.

    Desde que apagar un día lo saca de los DOS comprobantes, un estado que no sea
    exactamente 'activo' es un día apagado. Con un `str` pelado en el schema, un
    "Activo" con mayúscula —o texto basura por la dirección del endpoint— le sacaba la
    leche de la liquidación sin decir nada, y el dueño solo lo iba a notar cuadrando a
    mano. Tiene que rebotar con 422 y dejar el día como estaba.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    dia = _recibir(client, h, esc, "2026-07-20", "Aurelio", "44.23")
    liq_id = _generar(client, h)[0]["id"]
    total = D(_leer(client, h, liq_id)["valor_transporte"])

    for basura in ("Activo", "apagado", "cualquier_cosa", ""):
        r = client.put(f"{RECEPCIONES}/{dia['id']}", json={"estado": basura}, headers=h)
        assert r.status_code == 422, f"paso el estado {basura!r} con {r.status_code}"
    vigente = client.get(f"{RECEPCIONES}/{dia['id']}", headers=h).json()
    assert vigente["estado"] == "activo"
    assert D(_leer(client, h, liq_id)["valor_transporte"]) == total


# ===========================================================================
# 5. RECALCULAR VARIAS VECES: el papel no se puede mover
# ===========================================================================
def test_recalcular_tres_veces_no_corre_el_reparto(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    for quien, l in (("Aurelio", "44.23"), ("Marleny", "82.48"),
                     ("Gilberto", "137.45"), ("Rosalba", "103.73")):
        _recibir(client, h, esc, "2026-07-20", quien, l)
    _recibir(client, h, esc, "2026-07-20", "Hernando", "82.48")
    liq_id = _generar(client, h)[0]["id"]

    def papel():
        liq = _leer(client, h, liq_id)
        return (
            liq["valor_transporte"],
            liq["total_litros"],
            sorted((d["fecha"], d["ruta_nombre"], d["litros"], d["precio_litro"], d["valor"])
                   for d in liq["detalles"]),
        )

    def fotos():
        return {str(f.id): str(f.valor_transporte)
                for f in _recepciones_del_flete(db_session, liq_id)}

    esperado_papel, esperado_fotos = papel(), fotos()
    for vuelta in (1, 2, 3):
        r = client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)
        assert r.status_code in (200, 201), r.text
        db_session.expire_all()
        assert papel() == esperado_papel, f"el papel se movio en la vuelta {vuelta}"
        assert fotos() == esperado_fotos, f"las fotos se movieron en la vuelta {vuelta}"


# ===========================================================================
# 6. MUCHAS RECEPCIONES CHIQUITAS: los centavos sueltos del reparto
# ===========================================================================
def test_muchas_recepciones_chiquitas_el_reparto_deja_todo_sumando(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    quienes = ["Aurelio", "Marleny", "Gilberto", "Rosalba"]
    por_dia = {
        "2026-07-20": ["44.23", "82.48", "137.45", "103.73"],
        "2026-07-21": ["0.01", "0.02", "0.03", "0.07"],
        "2026-07-22": ["7.77", "1.01", "12.50", "0.99"],
    }
    for fecha, lits in por_dia.items():
        for quien, l in zip(quienes, lits):
            _recibir(client, h, esc, fecha, quien, l)
    liq_id = _generar(client, h)[0]["id"]
    liq = _leer(client, h, liq_id)

    fallas = []
    total = D(0)
    for d in liq["detalles"]:
        l_, p, v = D(d["litros"]), D(d["precio_litro"]), D(d["valor"])
        total += v
        if centavos(l_ * p) != v:
            fallas.append(f"{d['fecha']}: {l_} L x ${p} = ${centavos(l_ * p)} != ${v}")
    if total != D(liq["valor_transporte"]):
        fallas.append(f"la columna suma ${total} y el total dice ${liq['valor_transporte']}")
    db_session.expire_all()
    suma = _suma_fotos(db_session, liq_id)
    if suma != D(liq["valor_transporte"]):
        fallas.append(f"las fotos suman ${suma} y el total dice ${liq['valor_transporte']}")
    assert not fallas, "\n".join(fallas)


# ===========================================================================
# 7. TARIFA CAMBIADA A MITAD DE QUINCENA: dos líneas legítimas que cuadran
# ===========================================================================
def test_tarifa_cambiada_a_mitad_de_quincena_deja_dos_lineas_que_cuadran(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, "2026-07-20", "Aurelio", "44.23")
    _recibir(client, h, esc, "2026-07-20", "Marleny", "82.48")
    r = client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["nap"]["id"], "valor_transporte": str(MIRA_VALLE)},
            {"ruta_id": esc["mv"]["id"], "valor_transporte": str(MIRA_VALLE)},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    _recibir(client, h, esc, "2026-07-20", "Gilberto", "137.45")
    _recibir(client, h, esc, "2026-07-20", "Rosalba", "103.73")
    liq_id = _generar(client, h)[0]["id"]
    liq = _leer(client, h, liq_id)

    fallas = []
    total = D(0)
    for d in liq["detalles"]:
        l_, p, v = D(d["litros"]), D(d["precio_litro"]), D(d["valor"])
        total += v
        if centavos(l_ * p) != v:
            fallas.append(f"{d['fecha']} ${p}: {l_} L x ${p} = ${centavos(l_ * p)} != ${v}")
    precios = sorted(str(d["precio_litro"]) for d in liq["detalles"])
    if len(set(precios)) != len(precios):
        fallas.append(f"dos renglones con la MISMA tarifa el mismo dia y ruta: {precios}")
    if total != D(liq["valor_transporte"]):
        fallas.append(f"la columna suma ${total} y el total dice ${liq['valor_transporte']}")
    db_session.expire_all()
    suma = _suma_fotos(db_session, liq_id)
    if suma != D(liq["valor_transporte"]):
        fallas.append(f"las fotos suman ${suma} y el total dice ${liq['valor_transporte']}")
    assert not fallas, "\n".join(fallas)


# ===========================================================================
# 8. MEDIO CENTAVO EXACTO: 0,5 L × $317,53 = $158,765
# ===========================================================================
def test_medio_centavo_exacto_el_reparto_coloca_los_centavos_sueltos(
    client, base_datos, db_session
):
    """Cuatro días de 0,5 L a $317,53: cada uno vale $158,765 EXACTO, o sea medio
    centavo. Sumados uno por uno serían $635,08 ($158,77 × 4); el renglón dice
    2,00 L × $317,53 = $635,06. El reparto tiene que dejar dos fotos en $158,76 y
    dos en $158,77 para que la suma dé el renglón."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    # Napoles a $317,53: es la unica tarifa de dos decimales con la que unos litros
    # de dos decimales dan medio centavo EXACTO (0,50 · 1,50 · 2,50 L). Con $242,76
    # no existen esos litros (76k nunca cae en 50 modulo 100).
    r = client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["nap"]["id"], "valor_transporte": str(MIRA_VALLE)},
            {"ruta_id": esc["mv"]["id"], "valor_transporte": str(MIRA_VALLE)},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    for quien in ("Aurelio", "Marleny", "Gilberto", "Rosalba"):
        _recibir(client, h, esc, "2026-07-20", quien, "0.5")
    liq_id = _generar(client, h)[0]["id"]
    liq = _leer(client, h, liq_id)

    fallas = []
    total = sum((D(d["valor"]) for d in liq["detalles"]), D(0))
    for d in liq["detalles"]:
        l_, p, v = D(d["litros"]), D(d["precio_litro"]), D(d["valor"])
        if centavos(l_ * p) != v:
            fallas.append(f"{l_} L x ${p} = ${centavos(l_ * p)} pero el renglon dice ${v}")
    if total != D(liq["valor_transporte"]):
        fallas.append(f"la columna suma ${total} y el total dice ${liq['valor_transporte']}")
    db_session.expire_all()
    suma = _suma_fotos(db_session, liq_id)
    if suma != D(liq["valor_transporte"]):
        fotos = [str(f.valor_transporte) for f in _recepciones_del_flete(db_session, liq_id)]
        fallas.append(
            f"las fotos suman ${suma} y el total dice ${liq['valor_transporte']}; fotos {fotos}"
        )
    assert not fallas, "\n".join(fallas)


# ===========================================================================
# 9. UNA FOTO CONGELADA DENTRO DE UN GRUPO QUE HAY QUE REPARTIR
# ===========================================================================
def test_foto_congelada_en_el_grupo_no_se_toca_pero_repite_lineas_iguales(
    client, base_datos, db_session
):
    """La salida de emergencia: si el reparto tendría que moverle un centavo a una
    foto ya pagada, el renglón se PARTE en una línea por recepción.

    Se llama `_reparto_del_flete` de frente porque por la API no se llega: el único
    que arma `congeladas` es `_fotos_congeladas`, que congela TODAS o NINGUNA, y
    `recuadrar`/`recalcular` rebotan antes si la liquidación movió plata.

    Lo que la prueba fija: la foto congelada NO se mueve y la columna sigue sumando
    el total. Lo que documenta: vuelven las DOS líneas idénticas en fecha, ruta y
    tarifa que la regla nueva existía para eliminar.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    a = _recibir(client, h, esc, "2026-07-20", "Aurelio", "44.23")
    b = _recibir(client, h, esc, "2026-07-20", "Marleny", "82.48")
    db_session.expire_all()
    recs = [db_session.get(RecepcionLeche, uuid.UUID(x["id"])) for x in (a, b)]
    antes = {r.id: D(r.valor_transporte) for r in recs}

    # el reparto SIN congelar mueve una foto un centavo ($30.760,11 -> $30.760,12)
    libre = _reparto_del_flete(recs)
    assert libre.fotos, "el escenario tenia que necesitar reparto y no lo necesito"
    movida = next(iter(libre.fotos))

    congelado = _reparto_del_flete(recs, frozenset({movida}))
    assert movida not in congelado.fotos, "movio una foto CONGELADA"

    total = sum((D(r["valor"]) for r in congelado.renglones), D(0))
    fotos = sum((congelado.fotos.get(r.id, antes[r.id]) for r in recs), D(0))
    assert total == fotos, (
        f"con la foto congelada la columna suma ${total} y las fotos ${fotos}"
    )
    for r in congelado.renglones:
        assert centavos(D(r["litros"]) * D(r["precio_litro"])) == D(r["valor"]), (
            f"renglon que no se reproduce: {r['litros']} L x ${r['precio_litro']}"
        )
