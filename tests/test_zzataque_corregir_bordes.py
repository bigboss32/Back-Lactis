"""ATAQUE A LOS BORDES DE «CORREGIR UNA QUINCENA YA PAGADA»: DATOS MALOS.

QUÉ SE ATACA. La corrección de una quincena pagada es la única operación del sistema
que le mueve las cifras a un comprobante QUE EL PRODUCTOR YA TIENE EN LA MANO. El
dueño la va a usar con la calculadora al lado, comparando el papel viejo con la
pantalla. Por eso acá no se prueba el camino feliz (ese ya está probado): se prueba
que TODO lo que un cliente puede mandar y está mal REBOTA SIN ESCRIBIR NADA.

LAS CIFRAS SON LAS DEL DUEÑO, y son las mismas en todo el archivo:

    · la quincena vale $500.000 y se pagó con $500.000 (saldo $0, estado 'pagada');
    · quedó suelto el día del 12/06 por $180.000 (90 L × $2.000);
    · si ese día entra bien, el comprobante queda en $680.000 con $500.000 pagados y
      $180.000 por entregar. Eso es lo que mide `test_el_dia_olvidado_entra...`.

Y HAY UN DÍA CON DESCUENTO adentro de la quincena a propósito: el 05/06, 10 L de
$2.000 = $20.000 de bruto con $20.000 de descuento, o sea $0 netos. No cambia la cifra
grande ($500.000 sigue siendo $500.000) y es el único día con el que se puede probar
que un precio nuevo que deja el día EN ROJO rebota.

LA MEDIDA DE "NO ESCRIBIÓ NADA" ES SIEMPRE LA MISMA (`_intacta`), y es la que importa:
mismo valor_total, mismo pagado, mismo saldo, misma versión, CERO renglones de
corrección y el día suelto todavía suelto. Un rebote que igual dejó marcado el día
sería peor que no rebotar: el día quedaría preso, fuera de este comprobante y fuera de
todos los siguientes, y nadie se lo pagaría al productor nunca.
"""
import uuid
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")


def D(v):
    return Decimal(str(v))


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="2000"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros, *, desc=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)}
    if desc is not None:
        cuerpo["descuentos"] = str(desc)
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, periodo):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _de(liquidaciones, nombre):
    encontradas = [liq for liq in liquidaciones if liq.get("proveedor_nombre") == nombre]
    assert len(encontradas) == 1, f"se esperaba una sola de {nombre}: {liquidaciones}"
    return encontradas[0]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _leer_recepcion(client, h, rec_id):
    r = client.get(f"{REC}/{rec_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _aprobar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _pagar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/pagar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _previsualizar(client, h, liq_id, **payload):
    payload.setdefault("motivo", "se le olvidó un detalle")
    return client.post(f"{API}/{liq_id}/corregir/previsualizar", json=payload, headers=h)


def _corregir(client, h, liq_id, **payload):
    payload.setdefault("motivo", "se le olvidó un detalle")
    return client.post(f"{API}/{liq_id}/corregir", json=payload, headers=h)


def _mensaje(respuesta):
    """El texto del rebote. Los errores de negocio salen como
    {"error": {"code": "business_rule", "detail": "..."}} y no como el `detail` pelado
    de FastAPI: leerlo mal haría que estas pruebas "pasaran" sin mirar el mensaje."""
    cuerpo = respuesta.json()
    return (cuerpo.get("error") or {}).get("detail", "")


def _correcciones(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/correcciones", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _escenario(client, h):
    """La quincena de $500.000 pagada con $500.000, y el día suelto de $180.000.

    Devuelve (proveedor, liquidación pagada, recepción suelta del 12/06).
    """
    prov = _proveedor(client, h, "Henri C")
    # 250 L × $2.000 = $500.000
    _recepcion(client, h, prov, "2026-06-02", 250)
    # 10 L × $2.000 = $20.000 de bruto − $20.000 de descuento = $0 netos: no mueve la
    # cifra grande y es el día con el que se prueba el precio que deja el día en rojo.
    _recepcion(client, h, prov, "2026-06-05", 10, desc=20000)
    liq = _de(_generar(client, h, Q1), "Henri C")
    _aprobar(client, h, liq["id"])
    liq = _pagar(client, h, liq["id"])
    assert D(liq["valor_total"]) == D(500000), liq
    assert D(liq["pagado"]) == D(500000) and D(liq["saldo"]) == D(0)
    assert liq["estado"] == "pagada" and liq["version"] == 1

    # EL DÍA OLVIDADO, anotado DESPUÉS de pagar: 90 L × $2.000 = $180.000.
    suelto = _recepcion(client, h, prov, "2026-06-12", 90)
    assert suelto["liquidacion_id"] is None
    return prov, liq, suelto


def _dia_con_descuento(client, h, liq):
    """El renglón del 05/06 (el que tiene $20.000 de descuento)."""
    detalle = next(d for d in liq["detalles"] if d["fecha"] == "2026-06-05")
    return detalle


def _intacta(client, h, liq_id, *, suelto_id=None):
    """Que el rebote no dejó ni un peso escrito. Es la medida de todo el archivo."""
    liq = _leer(client, h, liq_id)
    assert D(liq["valor_total"]) == D(500000), f"le movieron el total: {liq['valor_total']}"
    assert D(liq["pagado"]) == D(500000), f"le movieron lo pagado: {liq['pagado']}"
    assert D(liq["saldo"]) == D(0), f"le movieron el saldo: {liq['saldo']}"
    assert liq["estado"] == "pagada", f"le movieron el estado: {liq['estado']}"
    assert liq["version"] == 1, f"le subieron la versión: {liq['version']}"
    assert _correcciones(client, h, liq_id) == [], "quedó un renglón de corrección"
    if suelto_id is not None:
        rec = _leer_recepcion(client, h, suelto_id)
        assert rec["liquidacion_id"] is None, (
            "el día suelto quedó MARCADO por un intento que rebotó: queda preso, "
            "fuera de este comprobante y de todos los siguientes"
        )
    return liq


# ------------------------------------------------- la línea de base (camino feliz)
def test_el_dia_olvidado_entra_y_la_cuenta_cuadra_al_centavo(client, base_datos):
    """$500.000 + el día de $180.000 = $680.000, con $500.000 ya entregados.

    Está acá como línea de base de todo el ataque: si esta no pasa, los rebotes de
    abajo no prueban nada. Y mide LA REGLA DE LA CASA sobre el resultado:
    neto_a_pagar = pagado + saldo, exacto, que es la resta que el dueño hace a mano
    entre el papel viejo ($500.000) y el nuevo ($680.000).
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)

    r = _corregir(client, h, liq["id"], recepciones_a_incluir=[suelto["id"]])
    assert r.status_code == 200, r.text
    despues = r.json()

    assert D(despues["valor_total"]) == D(680000)
    assert D(despues["pagado"]) == D(500000), "un pago ya entregado no se puede mover"
    assert D(despues["saldo"]) == D(180000)
    assert D(despues["neto_a_pagar"]) == D(despues["pagado"]) + D(despues["saldo"])
    assert despues["estado"] == "parcial"
    assert despues["version"] == 2


# ------------------------------------------------------------ ids que no son de acá
def test_un_detalle_de_otra_quincena_no_le_cambia_el_precio_a_esta(client, base_datos):
    """Corregir el precio apuntando a un renglón de OTRO comprobante.

    Si esto pasara, se le cambiaría el precio a un día de otro proveedor —y en otro
    período— desde la pantalla de esta quincena, y las dos quedarían mal: la de acá
    con la versión subida y la de allá con una cifra que nadie pidió.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)

    # Otra quincena, de otro proveedor, en otro período: 100 L × $2.000 = $200.000.
    otro = _proveedor(client, h, "Otro Productor")
    _recepcion(client, h, otro, "2026-06-20", 100)
    ajena = _de(_generar(client, h, Q2), "Otro Productor")
    detalle_ajeno = ajena["detalles"][0]

    r = _corregir(
        client,
        h,
        liq["id"],
        precios=[{"detalle_id": detalle_ajeno["id"], "precio_litro": "1500"}],
    )
    assert r.status_code == 404, r.text
    _intacta(client, h, liq["id"], suelto_id=suelto["id"])
    # Y la quincena ajena tampoco se movió: $200.000 con su precio de $2.000.
    sigue = _leer(client, h, ajena["id"])
    assert D(sigue["valor_total"]) == D(200000)
    assert D(sigue["detalles"][0]["precio_litro"]) == D(2000)


def test_una_recepcion_inventada_rebota_y_no_deja_nada_escrito(client, base_datos):
    """Un `recepcion_id` que no existe en ninguna parte.

    Es el caso de la pantalla vieja o del cliente que reintenta con un id de otra
    sesión. Tiene que rebotar ANTES de tocar la primera cifra.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)

    r = _corregir(client, h, liq["id"], recepciones_a_incluir=[str(uuid.uuid4())])
    assert r.status_code == 422, r.text
    _intacta(client, h, liq["id"], suelto_id=suelto["id"])


def test_el_dia_suelto_de_otro_proveedor_no_se_puede_colar(client, base_datos):
    """Un día suelto del período, pero DE OTRO PROVEEDOR.

    Es plata de otra persona: si entrara, este productor cobraría la leche que
    entregó su vecino, y el vecino se quedaría sin ese día en su propio comprobante.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)

    vecino = _proveedor(client, h, "El Vecino")
    # 40 L × $2.000 = $80.000, dentro del MISMO período y sin liquidar.
    dia_del_vecino = _recepcion(client, h, vecino, "2026-06-10", 40)

    r = _corregir(client, h, liq["id"], recepciones_a_incluir=[dia_del_vecino["id"]])
    assert r.status_code == 422, r.text
    _intacta(client, h, liq["id"], suelto_id=suelto["id"])
    assert _leer_recepcion(client, h, dia_del_vecino["id"])["liquidacion_id"] is None


# ------------------------------------------------------------------ ids repetidos
def test_el_mismo_dia_dos_veces_no_puede_inflar_la_previsualizacion(client, base_datos):
    """El mismo día mandado dos veces en `recepciones_a_incluir`.

    Un doble clic, una lista que se duplica al reabrir el diálogo, o un reintento: el
    día sigue siendo UNO SOLO y vale $180.000. La previsualización es literalmente la
    calculadora del dueño puesta en la pantalla —él aprueba mirando esa cifra—, así que
    tiene que decir $680.000 y no $860.000.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)

    r = _previsualizar(
        client, h, liq["id"], recepciones_a_incluir=[suelto["id"], suelto["id"]]
    )
    assert r.status_code in (200, 422), r.text
    if r.status_code == 422:
        # También es una salida válida: rebotar el repetido es rechazar el dato malo.
        _intacta(client, h, liq["id"], suelto_id=suelto["id"])
        return
    previo = r.json()
    assert D(previo["valor_total_despues"]) == D(680000), (
        f"el día de $180.000 se contó dos veces: {previo['valor_total_despues']}"
    )
    assert D(previo["queda_por_entregar"]) == D(180000)


def test_el_mismo_dia_dos_veces_deja_el_desglose_de_la_correccion_sin_cuadrar(
    client, base_datos
):
    """LA REGLA DE LA CASA sobre el papel de la corrección: el desglose suma la cifra.

    `dias_agregados` es lo que le explica al productor por qué su papel dice otra
    cifra. Si trae el día del 12/06 repetido, la suma del desglose da $360.000 contra
    un total que subió $180.000: el dueño resta con calculadora y no le da.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)

    r = _corregir(
        client, h, liq["id"], recepciones_a_incluir=[suelto["id"], suelto["id"]]
    )
    if r.status_code == 422:
        # Rebotar el repetido también es correcto: entonces nada quedó escrito.
        _intacta(client, h, liq["id"], suelto_id=suelto["id"])
        return
    assert r.status_code == 200, r.text

    correcciones = _correcciones(client, h, liq["id"])
    assert len(correcciones) == 1, correcciones
    correccion = correcciones[0]
    subio = D(correccion["valor_total_despues"]) - D(correccion["valor_total_antes"])
    desglose = sum((D(d["valor"]) for d in correccion["dias_agregados"]), D(0))
    assert subio == D(180000), f"el total no subió los $180.000 del día: {subio}"
    assert desglose == subio, (
        f"el desglose de días agregados suma {desglose} y el total subió {subio}"
    )


# ------------------------------------------------------------------- precios malos
def test_un_precio_que_deja_el_dia_en_rojo_rebota_y_no_toca_la_recepcion(
    client, base_datos
):
    """El 05/06 tiene $20.000 de descuento: a $1 el litro el día queda en −$19.990.

    10 L × $1 = $10 de bruto − $20.000 de descuento = −$19.990. Un día negativo le
    resta plata al comprobante por una tecla mal puesta, y además dejaría la recepción
    escrita con un precio que nadie quiso. Tiene que rebotar antes de escribir.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)
    detalle = _dia_con_descuento(client, h, liq)

    r = _corregir(
        client, h, liq["id"], precios=[{"detalle_id": detalle["id"], "precio_litro": "1"}]
    )
    assert r.status_code == 422, r.text
    assert "negativo" in _mensaje(r).lower(), r.text
    liq_despues = _intacta(client, h, liq["id"], suelto_id=suelto["id"])
    sigue = next(d for d in liq_despues["detalles"] if d["fecha"] == "2026-06-05")
    assert D(sigue["precio_litro"]) == D(2000), "le quedó escrito el precio rechazado"


@pytest.mark.parametrize(
    "precio",
    [
        "0",          # gratis: la leche siempre se paga
        "-1800",      # negativo: le restaría al comprobante
        "1000000.01", # un peso por encima del tope
        "1e20",       # el clásico que revienta la columna Numeric(14,2) en Postgres
    ],
)
def test_los_precios_imposibles_rebotan_en_la_puerta(client, base_datos, precio):
    """Ninguno de estos llega al servicio: los para el esquema, con 422 y no con 500.

    El 1e20 importa aparte: en Postgres un `numeric field overflow` sale como un 500 en
    la cara del dueño, y con el candado FOR UPDATE ya tomado. El tope de $1.000.000 es
    el mismo del otro camino que corrige precios, y está para el que teclea "1800000"
    en vez de "1800": esa tecla convierte una quincena de $500.000 en una de cientos de
    millones.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)
    detalle = _dia_con_descuento(client, h, liq)

    r = _corregir(
        client,
        h,
        liq["id"],
        precios=[{"detalle_id": detalle["id"], "precio_litro": precio}],
    )
    assert r.status_code == 422, f"precio {precio} respondió {r.status_code}: {r.text}"
    _intacta(client, h, liq["id"], suelto_id=suelto["id"])


# ---------------------------------------------------------------- estados que no son
def _quincena_en(client, h, nombre, litros=100):
    """Una quincena de Q2 recién generada (queda en 'borrador')."""
    prov = _proveedor(client, h, nombre)
    _recepcion(client, h, prov, "2026-06-20", litros)
    return _de(_generar(client, h, Q2), nombre)


def test_no_se_corrige_un_borrador_y_el_mensaje_dice_por_donde(client, base_datos):
    """Un borrador se edita por el camino normal; la corrección es solo para lo pagado.

    Si dejara pasar, el borrador subiría de versión y el productor recibiría un
    comprobante "v2" de un papel que nunca se imprimió.
    """
    h = auth_headers(client, "admin.a")
    borrador = _quincena_en(client, h, "Don Borrador")

    r = _corregir(client, h, borrador["id"], recepciones_a_incluir=[])
    assert r.status_code == 422, r.text
    assert "borrador" in _mensaje(r), r.text
    assert _leer(client, h, borrador["id"])["version"] == 1


def test_no_se_corrige_una_aprobada_que_todavia_no_se_ha_pagado(client, base_datos):
    """Aprobada y sin un peso entregado: no hay papel viejo que contradecir.

    Ahí todavía se puede recalcular por el camino de siempre, que sí vuelve a aplicar
    anticipos. Corregirla por esta puerta se los saltaría.
    """
    h = auth_headers(client, "admin.a")
    aprobada = _quincena_en(client, h, "Don Aprobado")
    _aprobar(client, h, aprobada["id"])

    r = _corregir(client, h, aprobada["id"], recepciones_a_incluir=[])
    assert r.status_code == 422, r.text
    assert "aprobada" in _mensaje(r), r.text
    assert _leer(client, h, aprobada["id"])["version"] == 1


def test_no_se_corrige_una_anulada(client, base_datos):
    """Una anulada es un documento muerto: corregirla lo revive con una cifra nueva."""
    h = auth_headers(client, "admin.a")
    anulada = _quincena_en(client, h, "Don Anulado")
    r = client.post(f"{API}/{anulada['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "anulada"

    r = _corregir(client, h, anulada["id"], recepciones_a_incluir=[])
    assert r.status_code == 422, r.text
    assert "anulada" in _mensaje(r), r.text
    assert _leer(client, h, anulada["id"])["version"] == 1


# --------------------------------------------------------------- motivo y vacío
def test_un_motivo_de_puros_espacios_no_alcanza(client, base_datos):
    """"   " pasa el `min_length` del esquema y no explica nada.

    El motivo es lo único que después le dice a alguien por qué el papel que el
    productor guardó dice otra cifra. Sin motivo, una corrección no se distingue de
    un error.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)

    r = _corregir(
        client, h, liq["id"], motivo="   ", recepciones_a_incluir=[suelto["id"]]
    )
    assert r.status_code == 422, r.text
    _intacta(client, h, liq["id"], suelto_id=suelto["id"])


def test_una_correccion_sin_dias_ni_precios_no_sube_la_version(client, base_datos):
    """Confirmar el diálogo sin marcar nada no puede emitir una versión nueva.

    Sería un papel "v2" con las MISMAS cifras que el v1: el productor tendría dos
    hojas distintas del mismo dinero y no sabría cuál vale.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)

    r = _corregir(client, h, liq["id"])
    assert r.status_code == 422, r.text
    _intacta(client, h, liq["id"], suelto_id=suelto["id"])


# ------------------------------------- el día que otro proceso se llevó en el medio
def test_el_dia_que_otro_se_llevo_entre_la_previsualizacion_y_la_confirmacion(
    client, base_datos, db_session
):
    """La carrera real: el dueño abre el diálogo y alguien más se lleva el día.

    Entre la previsualización (que ve el 12/06 suelto por $180.000) y el botón,
    otra corrida marca ese día contra OTRA liquidación. Si la corrección lo tomara
    igual, el mismo día de leche quedaría cobrado en dos comprobantes: $180.000
    pagados dos veces. Tiene que rebotar con un mensaje que mande a reabrir el
    diálogo, y no dejar nada escrito.
    """
    from app.modules.recepcion.models import RecepcionLeche

    h = auth_headers(client, "admin.a")
    _prov, liq, suelto = _escenario(client, h)

    previo = _previsualizar(client, h, liq["id"], recepciones_a_incluir=[suelto["id"]])
    assert previo.status_code == 200, previo.text
    assert D(previo.json()["valor_total_despues"]) == D(680000)

    # OTRO PROCESO SE LLEVA EL DÍA (una corrida que corrió mientras el diálogo estaba
    # abierto): queda marcado contra una liquidación distinta.
    otra = _quincena_en(client, h, "Otra Corrida")
    recepcion = db_session.get(RecepcionLeche, uuid.UUID(suelto["id"]))
    recepcion.liquidacion_id = uuid.UUID(otra["id"])
    db_session.commit()

    r = _corregir(client, h, liq["id"], recepciones_a_incluir=[suelto["id"]])
    assert r.status_code == 422, r.text
    detalle = _mensaje(r)
    assert "suelto" in detalle.lower() or "vuelva a abrir" in detalle.lower(), detalle

    liq_despues = _leer(client, h, liq["id"])
    assert D(liq_despues["valor_total"]) == D(500000)
    assert D(liq_despues["pagado"]) == D(500000)
    assert D(liq_despues["saldo"]) == D(0)
    assert liq_despues["version"] == 1
    assert _correcciones(client, h, liq["id"]) == []
    # Y el día se quedó donde lo dejó el otro proceso: la corrección no se lo robó.
    assert _leer_recepcion(client, h, suelto["id"])["liquidacion_id"] == otra["id"]


def test_un_dia_de_otra_quincena_no_se_puede_meter_en_esta(client, base_datos):
    """Un día suelto del MISMO proveedor pero FUERA del período (16/06 al 30/06).

    Si entrara, la leche de la quincena siguiente quedaría cobrada en la anterior: el
    comprobante de junio-1 diría $680.000 con un día que el papel de junio-2 también
    va a traer, y el productor cobraría dos veces el mismo día — o ninguna.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, suelto = _escenario(client, h)
    # 70 L × $2.000 = $140.000, pero del 20/06: es de la quincena SIGUIENTE.
    de_la_otra = _recepcion(client, h, prov, "2026-06-20", 70)

    r = _corregir(client, h, liq["id"], recepciones_a_incluir=[de_la_otra["id"]])
    assert r.status_code == 422, r.text
    _intacta(client, h, liq["id"], suelto_id=suelto["id"])
    assert _leer_recepcion(client, h, de_la_otra["id"])["liquidacion_id"] is None


def test_el_mismo_dia_con_dos_precios_distintos_deja_una_sola_verdad(client, base_datos):
    """El mismo `detalle_id` mandado dos veces con $1.500 y con $1.800.

    Es un payload contradictorio y no hay forma de saber cuál quiso el dueño, pero
    pase lo que pase el comprobante NO puede quedar aplicando los dos: 250 L a $1.500
    son $375.000 y a $1.800 son $450.000, y aplicarlos encadenados daría cualquier
    otra cifra. Lo que se mide es que la quincena quede valiendo EXACTO 250 × el
    precio que quedó escrito, y que el desglose de la corrección lo diga con un solo
    renglón: es el papel que el dueño compara con la hoja vieja.
    """
    h = auth_headers(client, "admin.a")
    _prov, liq, _suelto = _escenario(client, h)
    detalle = next(d for d in liq["detalles"] if d["fecha"] == "2026-06-02")

    r = _corregir(
        client,
        h,
        liq["id"],
        precios=[
            {"detalle_id": detalle["id"], "precio_litro": "1500"},
            {"detalle_id": detalle["id"], "precio_litro": "1800"},
        ],
    )
    if r.status_code == 422:
        # Rebotar la contradicción también es una respuesta correcta.
        _intacta(client, h, liq["id"])
        return
    assert r.status_code == 200, r.text
    despues = r.json()

    renglon = next(d for d in despues["detalles"] if d["fecha"] == "2026-06-02")
    precio_final = D(renglon["precio_litro"])
    assert precio_final in (D(1500), D(1800)), f"quedó un precio inventado: {precio_final}"
    # El día del 05/06 sigue valiendo $0 netos ($20.000 − $20.000), así que la cifra
    # grande es exactamente los 250 L al precio que quedó.
    assert D(despues["valor_total"]) == D(250) * precio_final
    assert D(despues["neto_a_pagar"]) == D(despues["pagado"]) + D(despues["saldo"])

    correcciones = _correcciones(client, h, liq["id"])
    assert len(correcciones) == 1, correcciones
    corregidos = correcciones[0]["precios_corregidos"]
    assert len(corregidos) == 1, f"el desglose repite el mismo día: {corregidos}"
    assert D(corregidos[0]["precio_despues"]) == precio_final
    assert D(corregidos[0]["valor_despues"]) == D(250) * precio_final
