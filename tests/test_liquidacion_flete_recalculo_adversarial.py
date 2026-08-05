"""ATAQUE ADVERSARIAL AL BOTÓN "RECALCULAR" DE LA LIQUIDACIÓN DEL TRANSPORTADOR.

Estas pruebas NO documentan el diseño: intentan romperlo. El objetivo es uno solo,
y es el que le importa al dueño: que recalcular no pueda mover un peso de plata ya
entregada, que no toque lo que no es de esa liquidación, y que después del recálculo
el desglose siga sumando la cifra grande y cada renglón se pueda reproducir a mano
con litros x tarifa.

Cada aserción trae las cifras en el mensaje: si falla, el mensaje tiene que servir
para explicarle el defecto al dueño sin volver a leer el código.
"""
import random
from decimal import ROUND_HALF_UP, Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
ANT = "/api/v1/anticipos"
AUD = "/api/v1/auditoria"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


CERO = D(0)


# ---------------------------------------------------------------- utilidades
def leer(client, h, rec_id):
    res = client.get(f"{REC}/{rec_id}", headers=h)
    assert res.status_code == 200, res.text
    return res.json()


def leer_liq(client, h, liq_id):
    res = client.get(f"{API}/{liq_id}", headers=h)
    assert res.status_code == 200, res.text
    return res.json()


def papel(liq):
    """La foto del comprobante que el transportador firma: total + renglones."""
    return (
        str(liq["valor_transporte"]),
        str(liq["valor_total"]),
        str(liq["total_litros"]),
        str(liq["saldo"]),
        sorted(
            (str(d["fecha"]), str(d["litros"]), str(d["precio_litro"]), str(d["valor"]))
            for d in liq["detalles"]
        ),
    )


def fotos_de(client, h, recs):
    return {r["id"]: str(leer(client, h, r["id"])["valor_transporte"]) for r in recs}


def revisar_cuadre(liq, fotos, contexto=""):
    """LA REGLA DE ORO, en tres partes, con las cifras en el mensaje.

    1. cada renglón se reproduce a mano: litros x tarifa = valor;
    2. la columna Valor suma EXACTO la cifra grande;
    3. y esa cifra grande es la suma de las fotos del flete de las recepciones.
    """
    renglones = [
        (str(d["fecha"]), D(d["litros"]), D(d["precio_litro"]), D(d["valor"]))
        for d in liq["detalles"]
    ]
    for fecha, litros, tarifa, valor in renglones:
        a_mano = centavos(litros * tarifa)
        assert a_mano == valor, (
            f"{contexto}: el renglón del {fecha} dice {valor} y a mano "
            f"{litros} L x ${tarifa} = {a_mano} (diferencia {valor - a_mano})"
        )
    suma = sum((v for _, _, _, v in renglones), CERO)
    assert suma == D(liq["valor_transporte"]), (
        f"{contexto}: la columna Valor suma {suma} y el comprobante dice "
        f"{liq['valor_transporte']} (diferencia {D(liq['valor_transporte']) - suma})"
    )
    assert D(liq["valor_total"]) == D(liq["valor_transporte"]), (
        f"{contexto}: valor_total {liq['valor_total']} != flete {liq['valor_transporte']}"
    )
    suma_litros = sum((l for _, l, _, _ in renglones), CERO)
    assert suma_litros == D(liq["total_litros"]), (
        f"{contexto}: los renglones suman {suma_litros} L y el encabezado dice "
        f"{liq['total_litros']} L"
    )
    suma_fotos = sum((D(f) for f in fotos.values()), CERO)
    assert suma_fotos == D(liq["valor_transporte"]), (
        f"{contexto}: las recepciones suman {suma_fotos} y el comprobante "
        f"{liq['valor_transporte']} (diferencia "
        f"{D(liq['valor_transporte']) - suma_fotos}). Fotos: {fotos}"
    )
    neto = D(liq["valor_total"]) - D(liq["anticipos"])
    assert D(liq["saldo"]) == neto - D(liq["pagado"]), (
        f"{contexto}: saldo {liq['saldo']} != neto {neto} - pagado {liq['pagado']}"
    )


# ------------------------------------------------------------------- montaje
def crear_ruta(client, h, nombre):
    res = client.post(
        "/api/v1/rutas", json={"nombre": nombre, "municipio": "Granada"}, headers=h
    )
    assert res.status_code == 201, res.text
    return res.json()


def crear_transportador(client, h, nombre, general, rutas=None):
    cuerpo = {"nombre": nombre, "valor_transporte": str(general)}
    if rutas:
        cuerpo["rutas"] = [
            {"ruta_id": r["id"], "valor_transporte": str(v)} for r, v in rutas
        ]
    res = client.post("/api/v1/transportadores", json=cuerpo, headers=h)
    assert res.status_code == 201, res.text
    return res.json()


def crear_proveedor(client, h, nombre, ruta):
    res = client.post(
        "/api/v1/proveedores",
        json={
            "nombre": nombre,
            "vereda": "El Roble",
            "precio_litro": "1800",
            "ruta_id": ruta["id"],
        },
        headers=h,
    )
    assert res.status_code == 201, res.text
    return res.json()


def recibir(client, h, t, prov, fecha, litros, ruta):
    res = client.post(
        REC,
        json={
            "fecha": fecha,
            "proveedor_id": prov["id"],
            "transportador_id": t["id"],
            "ruta_id": ruta["id"],
            "cantidad_litros": str(litros),
        },
        headers=h,
    )
    assert res.status_code == 201, res.text
    return res.json()


def poner_tarifa_ruta(client, h, t, pares):
    res = client.put(
        f"/api/v1/transportadores/{t['id']}",
        json={"rutas": [{"ruta_id": r["id"], "valor_transporte": str(v)} for r, v in pares]},
        headers=h,
    )
    assert res.status_code == 200, res.text


def generar(client, h, tipo="transportador", inicio="2026-06-01", fin="2026-06-15"):
    res = client.post(
        f"{API}/generar",
        json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo},
        headers=h,
    )
    assert res.status_code == 200, res.text
    return res.json()["generadas"]


def recalcular(client, h, liq_id):
    return client.post(f"{API}/{liq_id}/recalcular", headers=h)


BUENA = D("242.76")
MALA = D("100")


def escenario(client, h, *, tarifa=MALA, litros=("44.23", "82.48"), general="0"):
    """Alex en Nápoles, dos proveedores el mismo día, con la tarifa MAL puesta."""
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", general, rutas=[(ruta, tarifa)])
    recs = []
    for i, l in enumerate(litros):
        prov = crear_proveedor(client, h, f"Prov{i}", ruta)
        recs.append(recibir(client, h, t, prov, "2026-06-02", l, ruta))
    return ruta, t, recs


# ===========================================================================
# 1. LA PLATA YA ENTREGADA NO SE MUEVE, POR NINGÚN CAMINO
# ===========================================================================
def _pagar_todo(client, h, liq_id):
    assert client.post(f"{API}/{liq_id}/aprobar", headers=h).status_code == 200
    res = client.post(f"{API}/{liq_id}/pagar", headers=h)
    assert res.status_code == 200, res.text
    return res.json()


def _abonar(client, h, liq_id, valor="1000"):
    assert client.post(f"{API}/{liq_id}/aprobar", headers=h).status_code == 200
    res = client.post(
        f"{API}/{liq_id}/pagos", json={"fecha": "2026-06-16", "valor": valor}, headers=h
    )
    assert res.status_code == 200, res.text
    assert res.json()["estado"] == "parcial", res.text
    return res.json()


@pytest.mark.parametrize("como", ["pagada", "abonada"])
def test_recalcular_directo_no_toca_una_liquidacion_con_plata_entregada(
    client, base_datos, como
):
    """El camino directo: POST /recalcular sobre una que ya movió plata."""
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    if como == "pagada":
        _pagar_todo(client, h, liq["id"])
    else:
        _abonar(client, h, liq["id"])

    antes_liq = leer_liq(client, h, liq["id"])
    antes_fotos = fotos_de(client, h, recs)
    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])

    res = recalcular(client, h, liq["id"])
    assert res.status_code in (400, 422), (
        f"recalcular una liquidación '{antes_liq['estado']}' devolvió "
        f"{res.status_code}: {res.text}"
    )
    despues_liq = leer_liq(client, h, liq["id"])
    assert papel(despues_liq) == papel(antes_liq), (
        f"el comprobante {como} se movió.\nANTES:   {papel(antes_liq)}\n"
        f"DESPUÉS: {papel(despues_liq)}"
    )
    assert fotos_de(client, h, recs) == antes_fotos, "se movió la foto de un flete pagado"


@pytest.mark.parametrize("como", ["pagada", "abonada"])
@pytest.mark.parametrize(
    "campo,valor",
    [
        ("cantidad_litros", "50.00"),
        ("ruta_id", None),          # se rellena con otra ruta en el cuerpo
        ("fecha", "2026-06-05"),
        ("estado", "inactivo"),
        ("observaciones", "una nota cualquiera"),
        ("precio_litro", "1900"),
    ],
)
def test_cascada_por_la_recepcion_no_mueve_el_flete_con_plata_entregada(
    client, base_datos, como, campo, valor
):
    """El camino en cascada: tocar la recepción para que recuadre la liquidación.

    Con `observaciones` y `precio_litro` el PUT tiene que PASAR (no tocan el flete),
    pero el flete pagado no se puede mover ni por ese lado. Con los demás campos el
    PUT tiene que REBOTAR.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    otra = crear_ruta(client, h, "Mira Valle")
    liq = generar(client, h)[0]
    if como == "pagada":
        _pagar_todo(client, h, liq["id"])
    else:
        _abonar(client, h, liq["id"])

    antes_liq = leer_liq(client, h, liq["id"])
    antes_fotos = fotos_de(client, h, recs)
    # La tarifa cambia a espaldas del comprobante: si el recuadre llegara, el
    # comprobante pagado se re-derivaría con la nueva.
    poner_tarifa_ruta(client, h, t, [(ruta, BUENA), (otra, "317.50")])

    cuerpo = {campo: otra["id"] if campo == "ruta_id" else valor}
    res = client.put(f"{REC}/{recs[0]['id']}", json=cuerpo, headers=h)
    if campo in ("observaciones", "precio_litro"):
        assert res.status_code == 200, (
            f"corregir '{campo}' no toca el flete y debería pasar: {res.text}"
        )
    else:
        assert res.status_code in (400, 422), (
            f"cambiar '{campo}' de un día con el flete {como} devolvió "
            f"{res.status_code}: {res.text}"
        )

    despues_liq = leer_liq(client, h, liq["id"])
    assert papel(despues_liq) == papel(antes_liq), (
        f"tocando '{campo}' se movió el comprobante {como}.\n"
        f"ANTES:   {papel(antes_liq)}\nDESPUÉS: {papel(despues_liq)}"
    )
    assert fotos_de(client, h, recs) == antes_fotos, (
        f"tocando '{campo}' se movió la foto de un flete {como}: "
        f"{antes_fotos} -> {fotos_de(client, h, recs)}"
    )


@pytest.mark.parametrize("como", ["pagada", "abonada"])
@pytest.mark.parametrize("accion", ["editar", "eliminar"])
def test_cascada_por_el_anticipo_no_mueve_el_flete_con_plata_entregada(
    client, base_datos, como, accion
):
    """El otro camino en cascada: mover un anticipo aplicado a esa liquidación."""
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    res = client.post(
        ANT,
        json={
            "fecha": "2026-06-01",
            "valor": "1000",
            "tipo": "transportador",
            "transportador_id": t["id"],
        },
        headers=h,
    )
    assert res.status_code == 201, res.text
    anticipo = res.json()

    liq = generar(client, h)[0]
    assert D(liq["anticipos"]) == D("1000"), f"el anticipo no entró: {liq['anticipos']}"
    if como == "pagada":
        _pagar_todo(client, h, liq["id"])
    else:
        _abonar(client, h, liq["id"])

    antes_liq = leer_liq(client, h, liq["id"])
    antes_fotos = fotos_de(client, h, recs)
    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])

    if accion == "editar":
        res = client.put(f"{ANT}/{anticipo['id']}", json={"valor": "500"}, headers=h)
    else:
        res = client.delete(f"{ANT}/{anticipo['id']}", headers=h)
    assert res.status_code in (400, 422), (
        f"{accion} un anticipo de una liquidación {como} devolvió "
        f"{res.status_code}: {res.text}"
    )

    despues_liq = leer_liq(client, h, liq["id"])
    assert papel(despues_liq) == papel(antes_liq), (
        f"{accion} el anticipo movió el comprobante {como}.\n"
        f"ANTES:   {papel(antes_liq)}\nDESPUÉS: {papel(despues_liq)}"
    )
    assert fotos_de(client, h, recs) == antes_fotos


@pytest.mark.parametrize("como", ["pagada", "abonada"])
def test_cambiar_la_tarifa_no_mueve_el_comprobante_con_plata_entregada(
    client, base_datos, como
):
    """La tarifa sola: corregirla no puede llegar a un flete ya pagado."""
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    if como == "pagada":
        _pagar_todo(client, h, liq["id"])
    else:
        _abonar(client, h, liq["id"])
    antes_liq = leer_liq(client, h, liq["id"])
    antes_fotos = fotos_de(client, h, recs)

    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    # Y también quitándola del todo, y borrando la ruta del catálogo.
    poner_tarifa_ruta(client, h, t, [])
    assert client.delete(f"/api/v1/rutas/{ruta['id']}", headers=h).status_code in (200, 204)

    despues_liq = leer_liq(client, h, liq["id"])
    assert papel(despues_liq) == papel(antes_liq), (
        f"la tarifa movió el comprobante {como}.\nANTES:   {papel(antes_liq)}\n"
        f"DESPUÉS: {papel(despues_liq)}"
    )
    assert fotos_de(client, h, recs) == antes_fotos


@pytest.mark.parametrize("como", ["pagada", "abonada"])
def test_regenerar_el_periodo_no_mueve_el_comprobante_con_plata_entregada(
    client, base_datos, como
):
    """Volver a darle a "Generar" sobre el mismo período, con la tarifa corregida."""
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    if como == "pagada":
        _pagar_todo(client, h, liq["id"])
    else:
        _abonar(client, h, liq["id"])
    antes_liq = leer_liq(client, h, liq["id"])
    antes_fotos = fotos_de(client, h, recs)

    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    nuevas = generar(client, h)
    assert nuevas == [], f"se generó una liquidación de días ya liquidados: {nuevas}"

    despues_liq = leer_liq(client, h, liq["id"])
    assert papel(despues_liq) == papel(antes_liq), (
        f"regenerar movió el comprobante {como}.\nANTES:   {papel(antes_liq)}\n"
        f"DESPUÉS: {papel(despues_liq)}"
    )
    assert fotos_de(client, h, recs) == antes_fotos


# ===========================================================================
# 2. NO TOCA LO QUE NO ES DE ESA LIQUIDACIÓN
# ===========================================================================
def test_recalcular_no_toca_las_recepciones_de_otra_liquidacion(client, base_datos):
    """Dos quincenas del MISMO transportador y la MISMA ruta.

    Recalcular la de junio no puede tocar ni una cifra de la de julio.
    """
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(ruta, MALA)])
    prov = crear_proveedor(client, h, "Patricia", ruta)
    junio = [recibir(client, h, t, prov, "2026-06-02", "44.23", ruta)]
    julio = [recibir(client, h, t, prov, "2026-07-02", "82.48", ruta)]

    liq_junio = generar(client, h, inicio="2026-06-01", fin="2026-06-15")[0]
    liq_julio = generar(client, h, inicio="2026-07-01", fin="2026-07-15")[0]

    antes_julio = papel(leer_liq(client, h, liq_julio["id"]))
    antes_fotos_julio = fotos_de(client, h, julio)

    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    assert recalcular(client, h, liq_junio["id"]).status_code == 200

    assert papel(leer_liq(client, h, liq_julio["id"])) == antes_julio, (
        "recalcular junio movió el comprobante de julio.\n"
        f"ANTES:   {antes_julio}\nDESPUÉS: {papel(leer_liq(client, h, liq_julio['id']))}"
    )
    assert fotos_de(client, h, julio) == antes_fotos_julio, (
        "recalcular junio movió la foto del flete de un día de julio"
    )
    # Y la de junio sí quedó con la tarifa buena.
    j = leer_liq(client, h, liq_junio["id"])
    assert D(j["valor_transporte"]) == centavos(D("44.23") * BUENA)


def test_recalcular_no_toca_un_dia_sin_liquidacion_de_flete(client, base_datos):
    """Un día del mismo transportador que NO entró en el comprobante.

    Su foto no la puede tocar el recálculo: ese día todavía no es de nadie y su
    cifra se vuelve a derivar cuando se genere su propia quincena.
    """
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(ruta, MALA)])
    prov = crear_proveedor(client, h, "Patricia", ruta)
    dentro = recibir(client, h, t, prov, "2026-06-02", "44.23", ruta)
    liq = generar(client, h)[0]
    # Este día se recibe DESPUÉS de generar: queda sin liquidación de flete.
    fuera = recibir(client, h, t, prov, "2026-06-03", "82.48", ruta)
    assert leer(client, h, fuera["id"])["liquidacion_transporte_id"] is None

    antes_fuera = leer(client, h, fuera["id"])["valor_transporte"]
    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    assert recalcular(client, h, liq["id"]).status_code == 200

    assert leer(client, h, fuera["id"])["valor_transporte"] == antes_fuera, (
        f"recalcular tocó un día que no es de esa liquidación: {antes_fuera} -> "
        f"{leer(client, h, fuera['id'])['valor_transporte']}"
    )
    assert D(leer(client, h, dentro["id"])["valor_transporte"]) == centavos(
        D("44.23") * BUENA
    )


def test_recalcular_no_cruza_de_empresa(client, base_datos):
    """La liquidación de la Quesera B no existe para el admin de la Quesera A."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    ruta_b = crear_ruta(client, hb, "Napoles B")
    t_b = crear_transportador(client, hb, "Alex B", "0", rutas=[(ruta_b, MALA)])
    prov_b = crear_proveedor(client, hb, "Patricia B", ruta_b)
    rec_b = recibir(client, hb, t_b, prov_b, "2026-06-02", "44.23", ruta_b)
    liq_b = generar(client, hb)[0]

    antes_b = papel(leer_liq(client, hb, liq_b["id"]))
    antes_foto = leer(client, hb, rec_b["id"])["valor_transporte"]

    res = recalcular(client, ha, liq_b["id"])
    assert res.status_code == 404, (
        f"el admin de otra empresa recalculó una liquidación ajena: {res.status_code} "
        f"{res.text}"
    )
    assert papel(leer_liq(client, hb, liq_b["id"])) == antes_b
    assert leer(client, hb, rec_b["id"])["valor_transporte"] == antes_foto


def test_una_tarifa_de_otra_empresa_no_fija_plata_en_esta(client, base_datos, db_session):
    """Fila cruzada plantada a mano: la ruta es de la Quesera B, el transportador de A.

    Es el cinturón de `tarifa_por_litro`. Con la fila cruzada la cuenta tiene que
    caer en la tarifa GENERAL, no en la tarifa ajena.
    """
    from app.modules.transportadores.models import Transportador, TransportadorRuta

    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    ruta_b = crear_ruta(client, hb, "Napoles B")

    ruta_a = crear_ruta(client, ha, "Napoles A")
    t_a = crear_transportador(client, ha, "Alex", "130")  # general $130
    prov = crear_proveedor(client, ha, "Patricia", ruta_a)
    rec = recibir(client, ha, t_a, prov, "2026-06-02", "44.23", ruta_a)
    liq = generar(client, ha)[0]

    # Se planta la fila cruzada: la ruta de B con una tarifa jugosa, y además se le
    # cambia el id de ruta al de la recepción para que "coincida".
    import uuid as _uuid

    trans = db_session.get(Transportador, _uuid.UUID(t_a["id"]))
    db_session.add(
        TransportadorRuta(
            transportador_id=trans.id,
            ruta_id=_uuid.UUID(ruta_b["id"]),
            valor_transporte=D("9999"),
        )
    )
    db_session.flush()
    db_session.expire_all()

    assert recalcular(client, ha, liq["id"]).status_code == 200
    j = leer_liq(client, ha, liq["id"])
    esperado = centavos(D("44.23") * D("130"))
    assert D(j["valor_transporte"]) == esperado, (
        f"con una fila de tarifa cruzada el comprobante salió en "
        f"{j['valor_transporte']}; con la tarifa general de esta empresa son {esperado}"
    )
    revisar_cuadre(j, fotos_de(client, ha, [rec]), "fila de tarifa cruzada")


# ===========================================================================
# 3. EL CUADRE DESPUÉS DE RECALCULAR (la regla de oro)
# ===========================================================================
LITROS_FEOS = ["44.23", "82.48", "0.01", "126.71", "7.77", "999.99", "1.05", "63.33"]


@pytest.mark.parametrize("semilla", range(12))
def test_fuzz_recalcular_deja_el_desglose_sumando_la_cifra_grande(
    client, base_datos, semilla
):
    """Cifras feas al azar, dos rutas, tarifa corregida, y a recalcular tres veces."""
    rnd = random.Random(semilla)
    h = auth_headers(client, "admin.a")
    napoles = crear_ruta(client, h, "Napoles")
    mira = crear_ruta(client, h, "Mira Valle")
    t = crear_transportador(
        client, h, "Alex", "0", rutas=[(napoles, "100"), (mira, "100")]
    )
    provs = [crear_proveedor(client, h, f"P{i}", napoles) for i in range(3)]

    recs = []
    for dia in ("2026-06-02", "2026-06-03", "2026-06-04"):
        for prov in provs:
            if rnd.random() < 0.35:
                continue
            ruta = rnd.choice([napoles, mira])
            recs.append(recibir(client, h, t, prov, dia, rnd.choice(LITROS_FEOS), ruta))
    if not recs:
        pytest.skip("el azar no dejó recepciones")

    liq = generar(client, h)[0]
    # Se corrigen las DOS tarifas, con centavos y distintas entre sí.
    tarifa_n = D(rnd.choice(["242.76", "317.50", "0.01", "1.33", "130.07"]))
    tarifa_m = D(rnd.choice(["300.00", "242.77", "99.99", "2.05"]))
    poner_tarifa_ruta(client, h, t, [(napoles, tarifa_n), (mira, tarifa_m)])

    papeles = []
    for vuelta in range(3):
        res = recalcular(client, h, liq["id"])
        assert res.status_code == 200, res.text
        j = leer_liq(client, h, liq["id"])
        revisar_cuadre(j, fotos_de(client, h, recs), f"semilla {semilla} vuelta {vuelta}")
        papeles.append((papel(j), fotos_de(client, h, recs)))

        # Y la cuenta que hace el dueño A MANO: los litros del día en esa ruta por
        # la tarifa de esa ruta.
        for d in j["detalles"]:
            tarifa = tarifa_n if str(d["ruta_id"]) == napoles["id"] else tarifa_m
            assert D(d["precio_litro"]) == tarifa, (
                f"el renglón del {d['fecha']} imprime ${d['precio_litro']} y la tarifa "
                f"que hay hoy en el sistema para esa ruta es ${tarifa}"
            )
    assert papeles[0] == papeles[1] == papeles[2], (
        f"semilla {semilla}: el papel se movió entre recálculos.\n"
        + "\n".join(str(p) for p in papeles)
    )


def test_generar_y_recalcular_dan_el_mismo_papel(client, base_datos):
    """El mismo dato, dos caminos: generar de cero o generar y recalcular."""
    h = auth_headers(client, "admin.a")

    def montar_todo(headers, sufijo):
        ruta = crear_ruta(client, headers, f"Napoles{sufijo}")
        t = crear_transportador(client, headers, f"Alex{sufijo}", "0", rutas=[(ruta, BUENA)])
        recs = []
        for i, l in enumerate(["44.23", "82.48", "7.77"]):
            prov = crear_proveedor(client, headers, f"P{sufijo}{i}", ruta)
            recs.append(recibir(client, headers, t, prov, "2026-06-02", l, ruta))
        return ruta, t, recs

    # Camino A: la tarifa buena desde el principio, se genera y ya.
    ruta_a, t_a, recs_a = montar_todo(h, "A")
    # Camino B: la tarifa mala, se genera, se corrige y se recalcula.
    ruta_b, t_b, recs_b = montar_todo(h, "B")
    poner_tarifa_ruta(client, h, t_b, [(ruta_b, MALA)])

    liqs = generar(client, h)
    por_transportador = {l["transportador_id"]: l for l in liqs}
    liq_a = por_transportador[t_a["id"]]
    liq_b = por_transportador[t_b["id"]]

    poner_tarifa_ruta(client, h, t_b, [(ruta_b, BUENA)])
    assert recalcular(client, h, liq_b["id"]).status_code == 200

    pa = papel(leer_liq(client, h, liq_a["id"]))
    pb = papel(leer_liq(client, h, liq_b["id"]))
    assert pa == pb, f"generar y recalcular dieron papeles distintos.\nA: {pa}\nB: {pb}"
    assert fotos_de(client, h, recs_a).values().__len__() == 3
    assert sorted(fotos_de(client, h, recs_a).values()) == sorted(
        fotos_de(client, h, recs_b).values()
    ), "las fotos quedaron distintas entre generar y recalcular"


# ===========================================================================
# 4. LOS BORDES DE LA TARIFA
# ===========================================================================
def test_quitarle_la_tarifa_de_la_ruta_cae_en_la_general(client, base_datos):
    """Le quitan la ruta de la lista del transportador: manda la tarifa GENERAL."""
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "130", rutas=[(ruta, BUENA)])
    prov = crear_proveedor(client, h, "Patricia", ruta)
    rec = recibir(client, h, t, prov, "2026-06-02", "44.23", ruta)
    liq = generar(client, h)[0]
    assert D(liq["valor_transporte"]) == centavos(D("44.23") * BUENA)

    poner_tarifa_ruta(client, h, t, [])
    assert recalcular(client, h, liq["id"]).status_code == 200
    j = leer_liq(client, h, liq["id"])
    esperado = centavos(D("44.23") * D("130"))
    assert D(j["valor_transporte"]) == esperado, (
        f"sin tarifa de ruta el comprobante quedó en {j['valor_transporte']}; con la "
        f"general ($130) son {esperado}"
    )
    assert [D(d["precio_litro"]) for d in j["detalles"]] == [D("130")]
    revisar_cuadre(j, fotos_de(client, h, [rec]), "sin tarifa de ruta")


def test_borrar_la_ruta_del_catalogo_conserva_su_tarifa(client, base_datos):
    """La ruta se bota del catálogo: su tarifa sigue mandando (es historia)."""
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "130", rutas=[(ruta, BUENA)])
    prov = crear_proveedor(client, h, "Patricia", ruta)
    rec = recibir(client, h, t, prov, "2026-06-02", "44.23", ruta)
    liq = generar(client, h)[0]

    assert client.delete(f"/api/v1/rutas/{ruta['id']}", headers=h).status_code in (200, 204)
    res = recalcular(client, h, liq["id"])
    assert res.status_code == 200, f"recalcular con la ruta borrada rebotó: {res.text}"
    j = leer_liq(client, h, liq["id"])
    esperado = centavos(D("44.23") * BUENA)
    assert D(j["valor_transporte"]) == esperado, (
        f"con la ruta borrada el comprobante quedó en {j['valor_transporte']}; la "
        f"tarifa de esa ruta sigue siendo ${BUENA} y son {esperado}"
    )
    revisar_cuadre(j, fotos_de(client, h, [rec]), "ruta borrada")


def test_borrar_el_transportador_no_cambia_su_tarifa(client, base_datos):
    """El transportador se bota: su tarifa sigue siendo la que cobra."""
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "130", rutas=[(ruta, BUENA)])
    prov = crear_proveedor(client, h, "Patricia", ruta)
    rec = recibir(client, h, t, prov, "2026-06-02", "44.23", ruta)
    liq = generar(client, h)[0]
    antes = papel(leer_liq(client, h, liq["id"]))

    assert client.delete(f"/api/v1/transportadores/{t['id']}", headers=h).status_code in (
        200,
        204,
    )
    res = recalcular(client, h, liq["id"])
    assert res.status_code == 200, f"recalcular con el transportador borrado: {res.text}"
    j = leer_liq(client, h, liq["id"])
    assert papel(j) == antes, (
        f"borrar el transportador movió su comprobante.\nANTES:   {antes}\n"
        f"DESPUÉS: {papel(j)}"
    )
    revisar_cuadre(j, fotos_de(client, h, [rec]), "transportador borrado")


def test_tarifa_en_cero_al_recalcular(client, base_datos):
    """La tarifa queda en cero (a mano, o porque le quitaron la ruta y la general es 0).

    El comprobante queda en $0. Se documenta lo que pasa con la cifra grande y con
    las fotos: no puede quedar un desglose que no sume.
    """
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(ruta, BUENA)])
    prov = crear_proveedor(client, h, "Patricia", ruta)
    rec = recibir(client, h, t, prov, "2026-06-02", "44.23", ruta)
    liq = generar(client, h)[0]
    assert D(liq["valor_transporte"]) == centavos(D("44.23") * BUENA)

    poner_tarifa_ruta(client, h, t, [(ruta, "0")])
    res = recalcular(client, h, liq["id"])
    assert res.status_code == 200, res.text
    j = leer_liq(client, h, liq["id"])
    revisar_cuadre(j, fotos_de(client, h, [rec]), "tarifa en cero")
    assert D(j["valor_transporte"]) == CERO, (
        f"con la tarifa en cero el comprobante quedó en {j['valor_transporte']}"
    )
    assert D(leer(client, h, rec["id"])["valor_transporte"]) == CERO

    # Y se recupera: se corrige la tarifa y se recalcula otra vez.
    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    assert recalcular(client, h, liq["id"]).status_code == 200
    j2 = leer_liq(client, h, liq["id"])
    assert D(j2["valor_transporte"]) == centavos(D("44.23") * BUENA), (
        f"no se pudo recuperar de la tarifa en cero: quedó en {j2['valor_transporte']}"
    )


def test_saldo_negativo_cuando_la_tarifa_baja_por_debajo_del_anticipo(client, base_datos):
    """La tarifa corregida deja el comprobante debajo del anticipo ya entregado.

    Las cifras: 44,23 L a $242,76 = $10.737,27 con $5.000 de anticipo YA ENTREGADO
    (saldo $5.737,27). Se descubre que la tarifa estaba mal y se corrige a $1: el flete
    cae a $44,23 y el anticipo ya no cabe. Saldo -$4.955,77.

    POR QUÉ ESTA PRUEBA CAMBIÓ DE EXPECTATIVA, y es la decisión del dueño:

    ANTES exigía `saldo >= 0`, y para cumplirla se había puesto el sistema a SOLTAR el
    anticipo que no cupiera (dejarlo sin aplicar, con `liquidacion_id` en nulo, para
    descontárselo a la siguiente quincena). Eso hacía SALIR LA PLATA DOS VECES, y se
    reprodujo: un proveedor con 100 L a $1.800 —$180.000— y un anticipo de $300.000 ya
    entregado; el comprobante salía con "Anticipos aplicados $0,00" y saldo $180.000, y
    el dueño le pagaba esos $180.000 encima de los $300.000 que ya le había dado.

    LA REGLA QUE QUEDÓ: los anticipos se aplican COMPLETOS, siempre. Un saldo por debajo
    de cero no es un descuadre, es LA VERDAD —el tercero le quedó debiendo al negocio—, y
    esconderla detrás de un anticipo "pendiente" le tapa al dueño justo el dato con el que
    tiene que ir a cobrar. Lo que se arregló en su lugar es la LECTURA: la API expone
    `le_queda_debiendo` en positivo y el comprobante cambia el rótulo por "LE QUEDA
    DEBIENDO" (ver tests/test_liquidacion_saldo_negativo.py).

    Lo que esta prueba conserva: que recalcular NO se rebote —la tarifa corregida es un
    dato bueno y el dueño la corrigió a propósito— y que el desglose siga cuadrando.
    """
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(ruta, BUENA)])
    prov = crear_proveedor(client, h, "Patricia", ruta)
    rec = recibir(client, h, t, prov, "2026-06-02", "44.23", ruta)
    res = client.post(
        ANT,
        json={"fecha": "2026-06-01", "valor": "5000", "tipo": "transportador",
              "transportador_id": t["id"]},
        headers=h,
    )
    assert res.status_code == 201
    anticipo = res.json()
    liq = generar(client, h)[0]
    assert D(liq["saldo"]) == D("5737.27"), liq["saldo"]

    poner_tarifa_ruta(client, h, t, [(ruta, "1")])
    assert recalcular(client, h, liq["id"]).status_code == 200
    j = leer_liq(client, h, liq["id"])
    revisar_cuadre(j, fotos_de(client, h, [rec]), "saldo negativo")

    assert D(j["valor_transporte"]) == D("44.23"), j["valor_transporte"]
    assert D(j["anticipos"]) == D("5000"), (
        f"el anticipo se soltó al recalcular: quedó en {j['anticipos']} de $5.000 que ya "
        "se le entregaron. Por ahí se le paga la quincena entera encima del anticipo"
    )
    assert D(j["saldo"]) == D("-4955.77"), j["saldo"]
    # Y el saldo negativo se lee al derecho: es el TERCERO el que debe.
    assert D(j["le_queda_debiendo"]) == D("4955.77"), (
        f"el saldo quedó en {j['saldo']} y el sistema no lo dice en palabras: "
        f"le_queda_debiendo = {j['le_queda_debiendo']}"
    )
    # El anticipo sigue APLICADO contra esta liquidación, no suelto esperando otra.
    guardado = client.get(f"{ANT}/{anticipo['id']}", headers=h).json()
    assert guardado["liquidacion_id"] == liq["id"], guardado


def test_generar_con_la_tarifa_en_cero_no_borra_el_flete_de_los_dias(client, base_datos):
    """Generar con la tarifa en cero: ¿deja las cifras del día en cero y sin comprobante?

    Es el caso del dueño que quita la ruta de la lista del transportador y su tarifa
    general está en cero (el valor por omisión). Oprime Generar, no sale comprobante
    —y las cifras de flete de esos días se van a cero de todos modos—.

    Y AHORA EL SALTO SE AVISA: el transportador sale en `omitidas` con su motivo. Antes
    no salía en la respuesta, igual que quien no trajo leche, y esos 44,23 L quedaban sin
    comprobante y sin nadie a quien preguntarle.
    """
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(ruta, BUENA)])
    prov = crear_proveedor(client, h, "Patricia", ruta)
    rec = recibir(client, h, t, prov, "2026-06-02", "44.23", ruta)
    antes = leer(client, h, rec["id"])["valor_transporte"]
    assert D(antes) == centavos(D("44.23") * BUENA)

    poner_tarifa_ruta(client, h, t, [])  # cae en la general, que es 0
    r = client.post(
        f"{API}/generar",
        json={
            "periodo_inicio": "2026-06-01",
            "periodo_fin": "2026-06-15",
            "tipo": "transportador",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    corrida = r.json()
    liqs = corrida["generadas"]
    assert liqs == [], f"con la tarifa en cero no debería salir comprobante: {liqs}"
    omitida = corrida["omitidas"][0]
    print(f"\n  omitido por tarifa en cero: {omitida['motivo']}")
    assert omitida["motivo_codigo"] == "flete_sin_tarifa"
    assert omitida["tercero_nombre"] == "Alex"
    assert "44,23 L" in omitida["motivo"], omitida["motivo"]
    despues = leer(client, h, rec["id"])["valor_transporte"]
    assert despues == antes, (
        f"'Generar' sin producir comprobante le borró el flete del día: {antes} -> "
        f"{despues}. El día sigue sin liquidar y su cifra quedó en cero"
    )


# ===========================================================================
# 5. ESTADO Y BITÁCORA
# ===========================================================================
def _auditoria(client, h, accion="editar"):
    res = client.get(
        AUD, params={"modulo": "liquidaciones", "accion": accion, "size": 100}, headers=h
    )
    assert res.status_code == 200, res.text
    return res.json()["items"]


def test_la_bitacora_registra_el_antes_y_el_despues_del_recalculo(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    total_antes = D(liq["valor_transporte"])
    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    assert recalcular(client, h, liq["id"]).status_code == 200
    j = leer_liq(client, h, liq["id"])

    entradas = [
        e
        for e in _auditoria(client, h)
        if e["entidad_id"] == liq["id"] and (e["despues"] or {}).get("recalculo")
    ]
    assert entradas, "el recálculo no dejó entrada de bitácora con el antes y el después"
    r = entradas[-1]["despues"]["recalculo"]
    assert D(str(r["valor_transporte_antes"])) == total_antes, (
        f"la bitácora dice que antes valía {r['valor_transporte_antes']} y valía "
        f"{total_antes}"
    )
    assert D(str(r["valor_transporte_despues"])) == D(j["valor_transporte"]), (
        f"la bitácora dice que quedó en {r['valor_transporte_despues']} y quedó en "
        f"{j['valor_transporte']}"
    )
    assert r["dias_con_flete_recalculado"] == len(recs), (
        f"cambiaron {len(recs)} días de flete y la bitácora anota "
        f"{r['dias_con_flete_recalculado']}"
    )


def test_la_bitacora_no_anota_dias_cambiados_cuando_no_cambio_nada(client, base_datos):
    """Recalcular dos veces seguidas: la segunda no cambió ni un peso.

    La bitácora no puede anotar "le cambió el flete a N días" en un recálculo que no
    movió nada: es la respuesta a "¿por qué el comprobante cambió de cifra?" y si
    miente, el dueño va a buscar un cambio que no existe.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    liq = generar(client, h)[0]
    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    assert recalcular(client, h, liq["id"]).status_code == 200
    primero = papel(leer_liq(client, h, liq["id"]))
    fotos_primero = fotos_de(client, h, recs)

    assert recalcular(client, h, liq["id"]).status_code == 200
    assert papel(leer_liq(client, h, liq["id"])) == primero, "el papel se movió"
    assert fotos_de(client, h, recs) == fotos_primero, "las fotos se movieron"

    entradas = [
        e
        for e in _auditoria(client, h)
        if e["entidad_id"] == liq["id"] and (e["despues"] or {}).get("recalculo")
    ]
    ultima = entradas[-1]["despues"]["recalculo"]
    assert ultima["valor_transporte_antes"] == ultima["valor_transporte_despues"], (
        f"la bitácora del segundo recálculo: {ultima}"
    )
    assert ultima["dias_con_flete_recalculado"] == 0, (
        "el segundo recálculo no movió ni un peso (el papel y las fotos quedaron "
        f"idénticos) y la bitácora anota que le cambió el flete a "
        f"{ultima['dias_con_flete_recalculado']} día(s)"
    )


def test_aprobada_vuelve_a_borrador_y_queda_en_la_bitacora(client, base_datos):
    """Recuadre en cascada sobre una APROBADA: baja a borrador y se anota el motivo.

    Y NO RE-PRECIFICA, aunque la tarifa haya cambiado en el camino. Es lo único que
    esta prueba cambió de expectativa, y vale la pena dejar escrito por qué:

    ANTES exigía que mover el ANTICIPO re-derivara el flete con la tarifa de hoy. Se
    escribió cuando el recuadre en cascada re-precificaba todo, y con esa regla puesta
    se midió el defecto que la tumbó: editar un campo que no le mueve la cuenta a nadie
    —una observación del día, el valor de un anticipo— le cambiaba la cifra a un
    comprobante ya emitido. En el caso medido, $30.760,12 -> $38.013,00 por escribir
    una nota: $7.252,88 sin causa visible.

    HOY la regla es: el recuadre vuelve a SUMAR y a repartir centavos con las fotos
    como están, y solo GENERAR y el botón RECALCULAR re-derivan con la tarifa de hoy.
    Así que acá el flete se queda con la tarifa MALA con la que se armó, y la BUENA
    entra cuando el dueño oprime el botón —que es lo que se comprueba al final, para
    que la tarifa corregida siga teniendo por dónde llegar—.

    Todo lo demás que esta prueba ya exigía se conserva: que la aprobada baje a
    borrador, que el desglose siga cuadrando y que la bajada quede explicada en la
    bitácora nombrando el anticipo.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h)
    res = client.post(
        ANT,
        json={"fecha": "2026-06-01", "valor": "1000", "tipo": "transportador",
              "transportador_id": t["id"]},
        headers=h,
    )
    assert res.status_code == 201
    anticipo = res.json()
    liq = generar(client, h)[0]
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200

    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    assert client.put(f"{ANT}/{anticipo['id']}", json={"valor": "500"}, headers=h).status_code == 200

    j = leer_liq(client, h, liq["id"])
    assert j["estado"] == "borrador", (
        f"la aprobada quedó en '{j['estado']}' después de que le movieran el anticipo"
    )
    revisar_cuadre(j, fotos_de(client, h, recs), "aprobada -> borrador")
    # NO se re-derivó: el cambio fue en el ANTICIPO, y el recuadre no re-precifica.
    con_la_mala = centavos((D("44.23") + D("82.48")) * MALA)
    assert D(j["valor_transporte"]) == con_la_mala, (
        f"mover el ANTICIPO re-precificó el comprobante: quedó en "
        f"{j['valor_transporte']} y con la tarifa que se usó para armarlo son "
        f"{con_la_mala} (con la tarifa nueva serían "
        f"{centavos((D('44.23') + D('82.48')) * BUENA)})"
    )
    # Y el anticipo nuevo sí se recogió completo: $500, no los $1.000 de antes.
    assert D(j["anticipos"]) == D("500"), (
        f"el recuadre no recogió el anticipo corregido: {j['anticipos']}"
    )
    entradas = _auditoria(client, h)
    motivos = [
        (e["despues"] or {}).get("motivo")
        for e in entradas
        if e["entidad_id"] == liq["id"]
    ]
    assert any(m and "anticipo" in m for m in motivos), (
        f"la bajada a borrador no quedó explicada en la bitácora: {motivos}"
    )

    # LA OTRA MITAD DE LA REGLA: la tarifa corregida sí entra, pero por el botón.
    assert recalcular(client, h, liq["id"]).status_code == 200
    j2 = leer_liq(client, h, liq["id"])
    revisar_cuadre(j2, fotos_de(client, h, recs), "tras recalcular")
    con_la_buena = centavos((D("44.23") + D("82.48")) * BUENA)
    assert D(j2["valor_transporte"]) == con_la_buena, (
        f"el botón Recalcular no aplicó la tarifa de hoy: quedó en "
        f"{j2['valor_transporte']} y a mano son {con_la_buena}"
    )


def test_recalcular_el_flete_deja_al_dia_la_columna_del_proveedor_pagado(
    client, base_datos
):
    """El día tiene la LECHE PAGADA y el FLETE en borrador (candado por campo).

    Recalcular el flete le mueve la foto del flete a ese día. La liquidación del
    proveedor —PAGADA— guarda en su columna informativa `valor_transporte` la suma
    del flete de sus días, y esa columna tiene que quedar al día: es lo mismo que se
    arregló con `refrescar_transporte_informativo` cuando el cambio entraba por la
    pantalla de Recepción diaria. Si se queda vieja, la cifra se corrige sola —sin
    que nadie toque nada— la próxima vez que se recuadre.
    """
    h = auth_headers(client, "admin.a")
    ruta = crear_ruta(client, h, "Napoles")
    t = crear_transportador(client, h, "Alex", "0", rutas=[(ruta, MALA)])
    prov = crear_proveedor(client, h, "Patricia", ruta)
    rec = recibir(client, h, t, prov, "2026-06-02", "44.23", ruta)

    liqs = generar(client, h, tipo="ambos")
    leche = next(l for l in liqs if l["tipo"] == "proveedor")
    flete = next(l for l in liqs if l["tipo"] == "transportador")
    # La LECHE se paga; el FLETE se queda en borrador.
    _pagar_todo(client, h, leche["id"])
    leche = leer_liq(client, h, leche["id"])
    assert leche["estado"] == "pagada"

    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    assert recalcular(client, h, flete["id"]).status_code == 200

    foto = D(leer(client, h, rec["id"])["valor_transporte"])
    assert foto == centavos(D("44.23") * BUENA), f"la foto quedó en {foto}"
    leche2 = leer_liq(client, h, leche["id"])
    # Lo que NO se puede haber movido de la pagada:
    assert D(leche2["valor_total"]) == D(leche["valor_total"])
    assert D(leche2["saldo"]) == D(leche["saldo"])
    assert leche2["estado"] == "pagada"
    # Y la columna informativa, que sí tiene que seguir la foto:
    assert D(leche2["valor_transporte"]) == foto, (
        f"la liquidación PAGADA del proveedor dice que el flete de sus días es "
        f"{leche2['valor_transporte']} y sus recepciones suman {foto} (diferencia "
        f"{foto - D(leche2['valor_transporte'])}): la cifra se va a corregir sola en "
        "el próximo recuadre, sin causa visible"
    )


def test_tocar_las_observaciones_de_un_dia_no_deberia_reprecificar_el_flete_aprobado(
    client, base_datos
):
    """Un campo LIBRE de la recepción, con el flete ya APROBADO y la tarifa cambiada.

    `observaciones` no toca la plata de nadie: no está en los campos que traban ni la
    leche ni el flete. Pero el recuadre en cascada baja la aprobada a borrador y
    vuelve a derivar TODO el flete con la tarifa de hoy, así que una nota escrita en
    un día re-precifica el comprobante entero.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h, tarifa=BUENA)
    liq = generar(client, h)[0]
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    aprobado = papel(leer_liq(client, h, liq["id"]))

    # Alguien sube la tarifa (un cambio legítimo para la quincena SIGUIENTE).
    poner_tarifa_ruta(client, h, t, [(ruta, "300.00")])
    res = client.put(
        f"{REC}/{recs[0]['id']}", json={"observaciones": "el tarro venía mal tapado"},
        headers=h,
    )
    assert res.status_code == 200, res.text

    j = leer_liq(client, h, liq["id"])
    revisar_cuadre(j, fotos_de(client, h, recs), "observaciones + tarifa nueva")
    assert papel(j) == aprobado, (
        "escribir una observación en un día re-precificó el comprobante APROBADO y lo "
        f"bajó a '{j['estado']}'.\nAPROBADO: {aprobado}\nDESPUÉS:  {papel(j)}\n"
        f"(126.71 L x $242.76 = {centavos(D('126.71') * BUENA)} contra "
        f"126.71 L x $300 = {centavos(D('126.71') * D('300'))})"
    )


def test_un_dia_apagado_conserva_la_tarifa_vieja_al_prenderlo(client, base_datos):
    """Se apaga un día del borrador, se corrige la tarifa, se recalcula, se prende.

    Al apagarse sale del comprobante y su foto NO se re-deriva. Cuando se prenda
    otra vez tiene que volver con la tarifa de hoy, no con la que tenía guardada.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h, tarifa=MALA)
    liq = generar(client, h)[0]
    assert client.put(f"{REC}/{recs[0]['id']}", json={"estado": "inactivo"}, headers=h).status_code == 200

    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    assert recalcular(client, h, liq["id"]).status_code == 200
    j = leer_liq(client, h, liq["id"])
    solo_activa = centavos(D("82.48") * BUENA)
    assert D(j["valor_transporte"]) == solo_activa, (
        f"con un día apagado el comprobante quedó en {j['valor_transporte']}; el día "
        f"activo son 82.48 L x ${BUENA} = {solo_activa}"
    )
    revisar_cuadre(j, {recs[1]["id"]: leer(client, h, recs[1]["id"])["valor_transporte"]},
                   "un día apagado")

    # Se prende otra vez: tiene que volver con la tarifa de HOY.
    assert client.put(f"{REC}/{recs[0]['id']}", json={"estado": "activo"}, headers=h).status_code == 200
    j2 = leer_liq(client, h, liq["id"])
    esperado = centavos((D("44.23") + D("82.48")) * BUENA)
    assert D(j2["valor_transporte"]) == esperado, (
        f"al prender el día el comprobante quedó en {j2['valor_transporte']} y a mano "
        f"son {esperado} (diferencia {esperado - D(j2['valor_transporte'])})"
    )
    revisar_cuadre(j2, fotos_de(client, h, recs), "día prendido otra vez")


# ===========================================================================
# 6. EL LADO BUENO: corregir una tarifa mal puesta SÍ se arregla recalculando
# ===========================================================================
def test_corregir_la_tarifa_y_recalcular_deja_de_imprimir_la_vieja(client, base_datos):
    """Lo que pidió el dueño, con cifras y también en el PDF."""
    h = auth_headers(client, "admin.a")
    ruta, t, recs = escenario(client, h, tarifa=MALA)
    liq = generar(client, h)[0]
    assert D(liq["valor_transporte"]) == centavos((D("44.23") + D("82.48")) * MALA)

    poner_tarifa_ruta(client, h, t, [(ruta, BUENA)])
    assert recalcular(client, h, liq["id"]).status_code == 200
    j = leer_liq(client, h, liq["id"])

    a_mano = centavos((D("44.23") + D("82.48")) * BUENA)
    assert D(j["valor_transporte"]) == a_mano, (
        f"el comprobante quedó en {j['valor_transporte']} y la cuenta del dueño "
        f"(126.71 L x ${BUENA}) da {a_mano}"
    )
    tarifas = sorted({D(d["precio_litro"]) for d in j["detalles"]})
    assert tarifas == [BUENA], (
        f"el comprobante todavía imprime {[str(x) for x in tarifas]} y la única tarifa "
        f"viva es ${BUENA}"
    )
    revisar_cuadre(j, fotos_de(client, h, recs), "tarifa corregida")

    pdf = client.get(f"{API}/{liq['id']}/pdf", headers=h)
    assert pdf.status_code == 200, pdf.text
    assert pdf.content[:4] == b"%PDF", "el comprobante no salió como PDF"
