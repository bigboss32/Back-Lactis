"""ATAQUE 6 - EL PAPEL DEL TRANSPORTADOR, que es de OTRA PERSONA.

Un dia de leche alimenta DOS comprobantes de dos personas distintas: el del PRODUCTOR
(marca `liquidacion_id`) y el del TRANSPORTADOR (marca `liquidacion_transporte_id`).
Corregir la quincena de LECHE es un boton que solo ve el dueno de la quesera, y el
transportador ni se entera: su papel puede estar firmado, pagado y guardado en una
carpeta. Este archivo intenta moverle un peso desde el otro lado.

LAS CIFRAS DEL ESCENARIO DE DIA FIJO, escritas a mano aca y no calculadas por el codigo
que se esta probando. Alex Agudelo cobra la ruta "A fabrica" a $150.000 POR DIA COMPLETO
-la tarifa dice "el viaje a fabrica vale 150k, sean 82 litros o 500"-:

    16/07/2026  ruta A fabrica   Aurelio  82,00 L    ->  EL VIAJE VALE $150.000,00
    17/07/2026  ruta A fabrica   Marleny 137,45 L    ->  EL VIAJE VALE $150.000,00
                                                        ------------------------
                       comprobante de flete de Alex, PAGADO   $300.000,00

    Marleny, leche a $1.800 el litro:  137,45 L x 1.800 = $247.410,00, PAGADA completa.

Y entonces aparecen DOS dias olvidados de Marleny, que son los dos casos del flete:

    16/07  100 L = $180.000 de leche  ->  ese viaje YA SE PAGO. Su flete vale $0,00.
                                          Cobrarlo otra vez seria pagar dos veces el
                                          MISMO viaje de $150.000.
    20/07   50 L =  $90.000 de leche  ->  viaje NUEVO. Su flete son $150.000 y entra
                                          normal en la proxima corrida de Alex.

    leche de Marleny despues de corregir: 247.410 + 180.000 + 90.000 = $517.410,00
    pagado (no se toca):                                               $247.410,00
    saldo:                                                             $270.000,00

    flete de Alex despues de todo:  $300.000 + $150.000 = $450.000,00
    y el viaje del 16/07 sumado en TODOS los comprobantes:              $150.000,00
    (no $300.000: ese es el error que este archivo hace imposible)
"""
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
RUTAS = "/api/v1/rutas"
PROV = "/api/v1/proveedores"
TRANS = "/api/v1/transportadores"

FIJO = Decimal("150000.00")
NAPOLES = Decimal("242.76")


def D(v):
    return Decimal(str(v))


def _post(client, h, url, body):
    r = client.post(url, json=body, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _leer_liq(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _leer_rec(client, h, rec_id):
    r = client.get(f"{REC}/{rec_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _generar(client, h, tipo, inicio="2026-07-16", fin="2026-07-31"):
    return _post(client, h, f"{API}/generar",
                 {"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo})


def _aprobar_y_pagar(client, h, liq_id):
    _post(client, h, f"{API}/{liq_id}/aprobar", {})
    return _post(client, h, f"{API}/{liq_id}/pagar", {})


def _retrato_del_flete(liq):
    """Todo lo que el transportador tiene firmado, en una sola estructura comparable.

    Si UNA de estas cifras cambia despues de que el dueno corrige la quincena de la
    leche, el papel de Alex dejo de decir lo que decia cuando se le entrego la plata.
    """
    return {
        "estado": liq["estado"],
        "valor_total": D(liq["valor_total"]),
        "valor_transporte": D(liq["valor_transporte"]),
        "pagado": D(liq["pagado"]),
        "saldo": D(liq["saldo"]),
        "renglones": sorted(
            (d["fecha"], d["ruta_nombre"] or "", D(d["litros"]), D(d["valor"]),
             d.get("modo_transporte") or "")
            for d in liq["detalles"]
        ),
    }


# ---------------------------------------------------------------------------
# EL MONTAJE
# ---------------------------------------------------------------------------
def _escenario_dia_fijo(client, h):
    """Alex con "A fabrica" en DIA FIJO $150.000, y dos productores en esa ruta."""
    fabrica = _post(client, h, RUTAS, {"nombre": "A fabrica", "municipio": "Granada"})
    alex = _post(client, h, TRANS, {
        "nombre": "Alex Agudelo",
        # La general queda POR LITRO y distinta: si el codigo leyera la que no es, las
        # cifras saldrian disparatadas en vez de parecidas.
        "valor_transporte": "200", "modo_transporte": "litro",
        "rutas": [{"ruta_id": fabrica["id"], "valor_transporte": str(FIJO),
                   "modo_transporte": "dia_fijo"}]})
    aurelio = _post(client, h, PROV, {"nombre": "Aurelio", "vereda": "La Vega",
                                      "precio_litro": "1800", "ruta_id": fabrica["id"]})
    marleny = _post(client, h, PROV, {"nombre": "Marleny", "vereda": "La Vega",
                                      "precio_litro": "1800", "ruta_id": fabrica["id"]})

    dia_de_aurelio = _post(client, h, REC, {
        "fecha": "2026-07-16", "proveedor_id": aurelio["id"],
        "transportador_id": alex["id"], "cantidad_litros": "82.00"})
    dia_de_marleny = _post(client, h, REC, {
        "fecha": "2026-07-17", "proveedor_id": marleny["id"],
        "transportador_id": alex["id"], "cantidad_litros": "137.45"})

    # La quincena de LECHE de Marleny, pagada completa: $247.410,00.
    leche = next(x for x in _generar(client, h, "proveedor")["generadas"]
                 if x["proveedor_id"] == marleny["id"])
    leche = _aprobar_y_pagar(client, h, leche["id"])
    assert D(leche["valor_total"]) == D("247410.00"), leche["valor_total"]
    assert leche["estado"] == "pagada"

    # El comprobante de FLETE de Alex, pagado: dos viajes de $150.000.
    flete = _generar(client, h, "transportador")["generadas"][0]
    flete = _aprobar_y_pagar(client, h, flete["id"])
    assert D(flete["valor_total"]) == D("300000.00"), flete["valor_total"]
    assert flete["estado"] == "pagada"

    return {"fabrica": fabrica, "alex": alex, "aurelio": aurelio, "marleny": marleny,
            "leche": leche, "flete": flete,
            "dia_de_aurelio": dia_de_aurelio, "dia_de_marleny": dia_de_marleny}


def _los_dos_dias_olvidados(client, h, esc):
    """El del viaje YA COBRADO (16/07) y el del viaje NUEVO (20/07)."""
    ya_cobrado = _post(client, h, REC, {
        "fecha": "2026-07-16", "proveedor_id": esc["marleny"]["id"],
        "transportador_id": esc["alex"]["id"], "cantidad_litros": "100"})
    nuevo = _post(client, h, REC, {
        "fecha": "2026-07-20", "proveedor_id": esc["marleny"]["id"],
        "transportador_id": esc["alex"]["id"], "cantidad_litros": "50"})
    return ya_cobrado, nuevo


# ---------------------------------------------------------------------------
# 1. CORREGIR LA LECHE NO LE MUEVE UN PESO AL PAPEL DEL TRANSPORTADOR
# ---------------------------------------------------------------------------
def test_corregir_la_leche_no_le_mueve_un_peso_al_comprobante_pagado_del_flete(
    client, base_datos
):
    """El comprobante de Alex vale $300.000 antes y $300.000 despues, renglon a renglon.

    Es el ataque central: el dueno de la quesera corrige la quincena de Marleny -le
    entran $270.000 mas de leche- y el papel de Alex, que es de otra persona y ni
    aparece en esa pantalla, tiene que quedar identico: mismo estado, mismo total,
    mismos dos renglones de $150.000 y las MISMAS fotos en las recepciones que lo
    componen. Si una sola de esas cifras se moviera, la plata que ya se le entrego a
    Alex quedaria contra un total que dejo de existir.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_dia_fijo(client, h)
    antes = _retrato_del_flete(esc["flete"])
    fotos_antes = {
        nombre: D(_leer_rec(client, h, esc[clave]["id"])["valor_transporte"])
        for nombre, clave in (("Aurelio 16/07", "dia_de_aurelio"),
                              ("Marleny 17/07", "dia_de_marleny"))
    }
    assert fotos_antes == {"Aurelio 16/07": FIJO, "Marleny 17/07": FIJO}, fotos_antes
    # LA REGLA DE LA CASA en el papel del transportador: sus fotos suman EXACTO su total.
    assert sum(fotos_antes.values()) == antes["valor_total"] == D("300000.00")

    ya_cobrado, nuevo = _los_dos_dias_olvidados(client, h, esc)
    hecho = client.post(
        f"{API}/{esc['leche']['id']}/corregir",
        json={"motivo": "se anotaron tarde el 16 y el 20",
              "recepciones_a_incluir": [ya_cobrado["id"], nuevo["id"]]},
        headers=h)
    assert hecho.status_code == 200, hecho.text
    corregida = hecho.json()
    # La leche SI se movio: 247.410 + 180.000 + 90.000 = 517.410, y el pagado quieto.
    assert D(corregida["valor_total"]) == D("517410.00"), corregida["valor_total"]
    assert D(corregida["pagado"]) == D("247410.00"), corregida["pagado"]
    assert D(corregida["saldo"]) == D("270000.00"), corregida["saldo"]
    assert D(corregida["neto_a_pagar"]) == D(corregida["pagado"]) + D(corregida["saldo"])

    despues = _retrato_del_flete(_leer_liq(client, h, esc["flete"]["id"]))
    assert despues == antes, (
        "el comprobante de flete de Alex cambio cuando se corrigio la quincena de leche "
        f"de Marleny:\n  antes  {antes}\n  despues {despues}")

    fotos_despues = {
        nombre: D(_leer_rec(client, h, esc[clave]["id"])["valor_transporte"])
        for nombre, clave in (("Aurelio 16/07", "dia_de_aurelio"),
                              ("Marleny 17/07", "dia_de_marleny"))
    }
    assert fotos_despues == fotos_antes, (
        "las fotos del flete que componen el comprobante pagado de Alex se movieron: "
        f"{fotos_antes} -> {fotos_despues}")

    # Y LA COLUMNA INFORMATIVA de la quincena de leche sigue cuadrando con sus dias:
    # es lo unico del flete que esa hoja imprime, y si dejara de ser la suma de las
    # fotos de sus propios dias el dueno tendria dos cifras distintas del mismo flete.
    #     17/07 $150.000  +  16/07 $0 (viaje ya cobrado)  +  20/07 $150.000 = $300.000
    fotos_de_la_leche = sum(
        (D(_leer_rec(client, h, rec_id)["valor_transporte"])
         for rec_id in (esc["dia_de_marleny"]["id"], ya_cobrado["id"], nuevo["id"])),
        D(0))
    assert D(corregida["valor_transporte"]) == fotos_de_la_leche == D("300000.00"), (
        f"la quincena corregida dice flete ${corregida['valor_transporte']} y sus dias "
        f"suman ${fotos_de_la_leche}")


# ---------------------------------------------------------------------------
# 2. EL DIA AGREGADO: marcado para la leche, LIBRE para el flete
# ---------------------------------------------------------------------------
def test_el_dia_agregado_queda_marcado_para_la_leche_y_suelto_para_el_flete(
    client, base_datos
):
    """`liquidacion_id` puesto y `liquidacion_transporte_id` en NULO.

    Son las dos marcas de dos personas distintas y esta operacion solo puede tocar una.
    Si la correccion pusiera tambien la del flete, el dia entraria al comprobante PAGADO
    de Alex sin que su renglon lo cobre: $150.000 de viaje del 20/07 que no se le pagan
    a nadie. Y si NO pusiera la de la leche, la correccion seria un no-op silencioso que
    sube la version sin cobrarle a nadie los $180.000 del dia.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_dia_fijo(client, h)
    ya_cobrado, nuevo = _los_dos_dias_olvidados(client, h, esc)

    for r in (ya_cobrado, nuevo):
        assert r["liquidacion_id"] is None, "el dia olvidado nace suelto para la leche"
        assert r["liquidacion_transporte_id"] is None, "y suelto para el flete"

    _post(client, h, f"{API}/{esc['leche']['id']}/corregir",
          {"motivo": "se anotaron tarde",
           "recepciones_a_incluir": [ya_cobrado["id"], nuevo["id"]]})

    for r in (ya_cobrado, nuevo):
        despues = _leer_rec(client, h, r["id"])
        assert despues["liquidacion_id"] == esc["leche"]["id"], (
            f"el dia {despues['fecha']} no quedo marcado en la quincena corregida: sus "
            "litros quedan sueltos para que otra corrida se los vuelva a cobrar")
        assert despues["liquidacion_transporte_id"] is None, (
            f"el dia {despues['fecha']} quedo pegado a un comprobante de flete: la "
            "correccion de la LECHE no puede tocar la marca del TRANSPORTADOR")


# ---------------------------------------------------------------------------
# 3. TARIFA FIJA: el viaje del 16/07 se paga UNA vez, $150.000 en total
# ---------------------------------------------------------------------------
def test_tarifa_fija_el_dia_agregado_vale_cero_y_el_viaje_no_se_paga_dos_veces(
    client, base_datos
):
    """El viaje del 16/07 vale $150.000 UNA vez, sumando TODOS los comprobantes de Alex.

    El 16/07 Alex ya hizo el viaje a fabrica y ya le pagaron los $150.000. Que se anote
    tarde la leche de un productor mas de ESE MISMO dia no le suma un peso: la tarifa
    dice que el dia vale 150k, no que cada productor vale 150k. Si el dia agregado
    entrara con $150.000, Alex cobraria $300.000 por un solo viaje.

    El 20/07, en cambio, es un viaje que de verdad hizo y que nadie le ha pagado: ese SI
    vale $150.000 completos. Las dos mitades van juntas a proposito - dejar el flete en
    cero siempre "resolveria" el doble cobro robandole al transportador un viaje real.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_dia_fijo(client, h)
    ya_cobrado, nuevo = _los_dos_dias_olvidados(client, h, esc)

    assert D(ya_cobrado["valor_transporte"]) == D("0"), (
        "el dia agregado de un viaje YA COBRADO nace con flete "
        f"${ya_cobrado['valor_transporte']}: ese viaje ya se pago")
    assert D(nuevo["valor_transporte"]) == FIJO, (
        f"el dia de un viaje NUEVO nace con flete ${nuevo['valor_transporte']} y el "
        f"viaje vale ${FIJO}")

    _post(client, h, f"{API}/{esc['leche']['id']}/corregir",
          {"motivo": "se anotaron tarde",
           "recepciones_a_incluir": [ya_cobrado["id"], nuevo["id"]]})

    # Y SE QUEDA ASI: corregir la leche no re-deriva el flete de nadie.
    assert D(_leer_rec(client, h, ya_cobrado["id"])["valor_transporte"]) == D("0.00"), (
        "despues de corregir la leche, el dia del viaje ya cobrado dejo de valer $0,00")
    assert D(_leer_rec(client, h, nuevo["id"])["valor_transporte"]) == FIJO

    # La corrida siguiente de Alex, que es donde el doble cobro se veria.
    segunda = _generar(client, h, "transportador")
    assert segunda["generadas"], f"Alex se quedo sin cobrar el viaje del 20/07: {segunda}"
    segundo_papel = segunda["generadas"][0]
    assert D(segundo_papel["valor_total"]) == FIJO, (
        f"el segundo comprobante de Alex vale ${segundo_papel['valor_total']} y el unico "
        f"viaje nuevo que hizo (20/07) vale ${FIJO}")

    # EL CUADRE QUE IMPORTA: el viaje del 16/07 sumado en TODOS los papeles de Alex.
    del_16 = D(0)
    total_de_alex = D(0)
    for papel in (_leer_liq(client, h, esc["flete"]["id"]), segundo_papel):
        total_de_alex += D(papel["valor_total"])
        suma_renglones = sum((D(d["valor"]) for d in papel["detalles"]), D(0))
        assert suma_renglones == D(papel["valor_total"]), (
            "los renglones del comprobante de flete dejaron de sumar su total")
        del_16 += sum((D(d["valor"]) for d in papel["detalles"]
                       if d["fecha"] == "2026-07-16"), D(0))

    assert del_16 == FIJO, (
        f"el viaje del 16/07 quedo cobrado ${del_16} entre todos los comprobantes de "
        f"Alex y ese dia vale ${FIJO}: se le esta pagando dos veces el mismo viaje")
    assert total_de_alex == D("450000.00"), (
        f"a Alex se le pago ${total_de_alex} por tres viajes de $150.000")


# ---------------------------------------------------------------------------
# 4. LA NOTA DEL FLETE, ANTES de confirmar
# ---------------------------------------------------------------------------
def test_la_nota_del_flete_le_dice_al_dueno_cual_de_los_dos_casos_es(client, base_datos):
    """La previsualizacion tiene que separar el viaje ya cobrado del viaje nuevo.

    El dueno suma a mano y va a preguntar por que el dia del 16/07 no le suma flete
    mientras el del 20/07 si. La respuesta va en el dialogo, ANTES de confirmar, no en
    soporte tres dias despues. Son $150.000 de diferencia entre un dia y el otro.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_dia_fijo(client, h)
    _los_dos_dias_olvidados(client, h, esc)

    prev = client.post(f"{API}/{esc['leche']['id']}/corregir/previsualizar",
                       json={"motivo": "revisando"}, headers=h)
    assert prev.status_code == 200, prev.text
    notas = {d["fecha"]: d["nota_flete"] for d in prev.json()["dias_sueltos"]}
    assert set(notas) == {"2026-07-16", "2026-07-20"}, notas

    assert "0,00" in (notas["2026-07-16"] or ""), (
        "el 16/07 es un viaje YA COBRADO y su flete vale $0,00; la nota dice: "
        f"{notas['2026-07-16']!r}")
    assert "0,00" not in (notas["2026-07-20"] or ""), (
        "el 20/07 es un viaje NUEVO de $150.000 y la nota le esta diciendo al dueno que "
        f"vale $0,00: {notas['2026-07-20']!r}")


# ---------------------------------------------------------------------------
# 5. EL DEFECTO: la nota dice $0,00 cuando el flete se cobra POR LITRO
# ---------------------------------------------------------------------------
def _escenario_por_litro(client, h):
    """La misma historia pero con la ruta cobrada POR LITRO a $242,76.

        16/07  Henri  82,00 L x 242,76 = $19.906,32   -+ comprobante de flete
        17/07  Rosa  137,45 L x 242,76 = $33.367,36   -+ PAGADO  $53.273,68

    y el dia olvidado de Rosa: 16/07, 100 L, que por litro vale
        100 x 242,76 = $24.276,00   -flete NUEVO y real, que Alex si se va a cobrar.
    """
    napoles = _post(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = _post(client, h, TRANS, {
        "nombre": "Alex Agudelo", "valor_transporte": "200", "modo_transporte": "litro",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": str(NAPOLES),
                   "modo_transporte": "litro"}]})
    henri = _post(client, h, PROV, {"nombre": "Henri", "vereda": "Napoles",
                                    "precio_litro": "1800", "ruta_id": napoles["id"]})
    rosa = _post(client, h, PROV, {"nombre": "Rosa", "vereda": "Napoles",
                                   "precio_litro": "1800", "ruta_id": napoles["id"]})
    _post(client, h, REC, {"fecha": "2026-07-16", "proveedor_id": henri["id"],
                           "transportador_id": alex["id"], "cantidad_litros": "82.00"})
    _post(client, h, REC, {"fecha": "2026-07-17", "proveedor_id": rosa["id"],
                           "transportador_id": alex["id"], "cantidad_litros": "137.45"})

    leche = next(x for x in _generar(client, h, "proveedor")["generadas"]
                 if x["proveedor_id"] == rosa["id"])
    leche = _aprobar_y_pagar(client, h, leche["id"])
    flete = _generar(client, h, "transportador")["generadas"][0]
    flete = _aprobar_y_pagar(client, h, flete["id"])
    assert D(flete["valor_total"]) == D("53273.68"), flete["valor_total"]

    olvidado = _post(client, h, REC, {"fecha": "2026-07-16", "proveedor_id": rosa["id"],
                                      "transportador_id": alex["id"],
                                      "cantidad_litros": "100"})
    return {"alex": alex, "rosa": rosa, "leche": leche, "flete": flete,
            "olvidado": olvidado}


def test_por_litro_el_dia_agregado_si_le_suma_flete_nuevo_al_transportador(
    client, base_datos
):
    """La plata, que esta bien: por litro, la leche anotada tarde SI cuesta flete.

    Va de primero porque es lo que hace que la prueba de abajo sea un defecto y no una
    opinion: el flete de ese dia son $24.276,00 de verdad, cobrados de verdad en la
    corrida siguiente de Alex.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_por_litro(client, h)
    assert D(esc["olvidado"]["valor_transporte"]) == D("24276.00"), (
        "100 L x $242,76 = $24.276,00 y la foto dice "
        f"${esc['olvidado']['valor_transporte']}")

    antes = _retrato_del_flete(esc["flete"])
    _post(client, h, f"{API}/{esc['leche']['id']}/corregir",
          {"motivo": "se anoto tarde el 16",
           "recepciones_a_incluir": [esc["olvidado"]["id"]]})
    # El papel pagado de Alex, intacto tambien en modo por litro.
    assert _retrato_del_flete(_leer_liq(client, h, esc["flete"]["id"])) == antes

    segunda = _generar(client, h, "transportador")
    assert segunda["generadas"], f"Alex se quedo sin cobrar ese flete: {segunda}"
    assert D(segunda["generadas"][0]["valor_total"]) == D("24276.00"), (
        "la corrida siguiente de Alex tiene que cobrarle los $24.276,00 del dia que se "
        f"anoto tarde y dice ${segunda['generadas'][0]['valor_total']}")


def test_la_nota_del_flete_no_puede_prometer_cero_cuando_la_ruta_se_cobra_por_litro(
    client, base_datos
):
    """$24.276,00 de flete anunciados como $0,00, en el dialogo donde el dueno decide.

    `viajes_ya_cobrados` esta escrita para el DIA FIJO -su propio comentario lo dice:
    "SOLO LA MIRA QUIEN COBRA POR DIA FIJO... Por litro nadie pregunta esto"- y reserva
    el (dia, ruta) tambien cuando el renglon anterior fue POR LITRO, a proposito. Pero
    `_nota_del_flete` la consulta sin preguntar antes por el modo de la tarifa de hoy,
    y entonces confunde "ese dia ya tuvo un renglon" con "ese dia ya esta pago completo".

    En este escenario el 16/07 tuvo un renglon por litro de $19.906,32 (los 82 L de
    Henri). El dia olvidado de Rosa son 100 L mas del mismo dia y la misma ruta: por
    litro eso son $24.276,00 de flete NUEVO, y la prueba de arriba demuestra que Alex
    los cobra. La nota, en cambio, dice que vale $0,00 y que asi se queda.

    Lo que el dueno hace con esa nota: confirma la correccion creyendo que el
    transportador no le suma nada, y arma la plata de la quincena de Alex sin esos
    $24.276. Es exactamente la clase de diferencia que descubre con la calculadora
    cuando ya firmo el comprobante.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_por_litro(client, h)

    prev = client.post(f"{API}/{esc['leche']['id']}/corregir/previsualizar",
                       json={"motivo": "revisando"}, headers=h)
    assert prev.status_code == 200, prev.text
    nota = next(d["nota_flete"] for d in prev.json()["dias_sueltos"]
                if d["fecha"] == "2026-07-16")

    assert "0,00" not in (nota or ""), (
        "la ruta se cobra POR LITRO y el flete de ese dia son $24.276,00 -Alex los "
        f"cobra en su corrida siguiente- pero la nota le promete al dueno: {nota!r}")


# ---------------------------------------------------------------------------
# 6. EL ORDEN INVERTIDO: Alex cobra el dia olvidado ANTES de que se corrija la leche
# ---------------------------------------------------------------------------
def _alex_cobra_primero(client, h, esc):
    """Alex se lleva los dos dias olvidados en un segundo comprobante, y lo cobra.

    Es el orden que se da solo en la practica: el dia se anota tarde, la corrida del
    transportador es semanal y sale antes de que el dueno se acuerde de corregir la
    quincena del productor. Cuando llega a corregirla, esos dos dias YA tienen puesta la
    marca del flete y su plata YA salio:

        16/07  viaje ya cobrado antes  ->  $0,00
        20/07  viaje nuevo             ->  $150.000,00
                                           -----------
                        segundo comprobante de Alex, PAGADO  $150.000,00
    """
    ya_cobrado, nuevo = _los_dos_dias_olvidados(client, h, esc)
    segundo = _generar(client, h, "transportador")["generadas"][0]
    segundo = _aprobar_y_pagar(client, h, segundo["id"])
    assert D(segundo["valor_total"]) == FIJO, segundo["valor_total"]
    return ya_cobrado, nuevo, segundo


def test_corregir_la_leche_no_toca_el_flete_que_esos_mismos_dias_ya_cobraron(
    client, base_datos
):
    """Los dias agregados ya estan en un comprobante de flete PAGADO. No se mueven.

    Aca la marca del transportador ya esta puesta cuando la correccion llega, asi que la
    correccion agrega el dia a la quincena de leche por encima de un dia cuyo flete ya
    se pago. Lo que no puede pasar: que le suelte la marca -el dia quedaria suelto y la
    corrida siguiente le cobraria a Alex otros $150.000 por el viaje del 20/07 que ya le
    pagaron- ni que le mueva la foto contra la que se le entrego la plata.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_dia_fijo(client, h)
    ya_cobrado, nuevo, segundo = _alex_cobra_primero(client, h, esc)
    antes = _retrato_del_flete(segundo)

    hecho = client.post(
        f"{API}/{esc['leche']['id']}/corregir",
        json={"motivo": "se anotaron tarde, Alex ya cobro",
              "recepciones_a_incluir": [ya_cobrado["id"], nuevo["id"]]}, headers=h)
    assert hecho.status_code == 200, hecho.text
    corregida = hecho.json()
    assert D(corregida["valor_total"]) == D("517410.00"), corregida["valor_total"]
    assert D(corregida["saldo"]) == D("270000.00"), corregida["saldo"]
    assert D(corregida["neto_a_pagar"]) == D(corregida["pagado"]) + D(corregida["saldo"])

    assert _retrato_del_flete(_leer_liq(client, h, segundo["id"])) == antes, (
        "el segundo comprobante pagado de Alex se movio al corregir la leche")
    for r, foto in ((ya_cobrado, D("0.00")), (nuevo, FIJO)):
        despues = _leer_rec(client, h, r["id"])
        assert despues["liquidacion_transporte_id"] == segundo["id"], (
            f"al dia {despues['fecha']} se le solto la marca del flete: la corrida "
            "siguiente le volveria a cobrar a Alex un viaje que ya le pagaron")
        assert D(despues["valor_transporte"]) == foto, (
            f"la foto del flete del dia {despues['fecha']} se movio: era ${foto} y dice "
            f"${despues['valor_transporte']}")

    # Y Alex no tiene nada nuevo que cobrar: los dos viajes ya estan pagos.
    tercera = _generar(client, h, "transportador")
    assert not tercera["generadas"], (
        f"se le genero a Alex un tercer comprobante por viajes ya pagados: {tercera}")


def test_la_nota_del_flete_no_puede_decir_cero_de_un_dia_que_ya_se_le_pago_al_transportador(
    client, base_datos
):
    """$150.000 ya entregados a Alex, anunciados como $0,00 en el mismo dialogo.

    El 20/07 es un viaje que Alex hizo, que le facturaron en $150.000 y que ya cobro: la
    prueba de arriba lo verifica contra su comprobante pagado. Cuando el dueno abre la
    correccion de la quincena de Marleny para meter ese dia, la nota del dia 20/07 le
    dice que el flete "vale $0,00 y asi se queda".

    Es la misma raiz que el defecto de la ruta por litro: `viajes_ya_cobrados` responde
    "ese (dia, ruta) ya tiene un renglon en algun papel" y `_nota_del_flete` lo lee como
    "ese dia no le cuesta flete a la quesera". Aca la diferencia es todavia mas gruesa,
    porque el renglon que reserva el viaje es EL DEL PROPIO DIA que se esta agregando.

    Lo que el dueno hace con eso: cuadra el costo de la quincena creyendo que esos dos
    dias de leche no traen flete, cuando uno de ellos ya le costo $150.000.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_dia_fijo(client, h)
    _alex_cobra_primero(client, h, esc)

    prev = client.post(f"{API}/{esc['leche']['id']}/corregir/previsualizar",
                       json={"motivo": "revisando"}, headers=h)
    assert prev.status_code == 200, prev.text
    nota = next(d["nota_flete"] for d in prev.json()["dias_sueltos"]
                if d["fecha"] == "2026-07-20")

    assert "0,00" not in (nota or ""), (
        "el viaje del 20/07 le costo a la quesera $150.000 y ya se le pagaron a Alex, "
        f"pero la nota del dialogo dice: {nota!r}")


# ---------------------------------------------------------------------------
# 7. UNA LIQUIDACION DE FLETE NO SE CORRIGE, NI SE ROZA
# ---------------------------------------------------------------------------
def test_una_liquidacion_de_flete_no_se_puede_corregir_ni_previsualizar(
    client, base_datos
):
    """El boton es solo de la quincena de LECHE, y el rebote no deja rastro.

    En el comprobante del transportador el renglon es (dia, ruta) y esos renglones son
    LA MEMORIA de que viaje ya se cobro (`viajes_ya_cobrados` los lee). Rearmarlos
    -que es lo que hace cualquier recalculo- borraria esa memoria y el mismo viaje de
    $150.000 se podria cobrar otra vez. Por eso aca no se corrige: se genera un segundo
    comprobante del periodo, que es lo que el mensaje tiene que decirle al dueno.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_dia_fijo(client, h)
    _los_dos_dias_olvidados(client, h, esc)
    antes = _retrato_del_flete(esc["flete"])

    for url in (f"{API}/{esc['flete']['id']}/corregir/previsualizar",
                f"{API}/{esc['flete']['id']}/corregir"):
        r = client.post(url, json={"motivo": "quiero meterle el dia del 20"}, headers=h)
        assert r.status_code == 422, (
            f"{url} respondio {r.status_code}: una liquidacion de flete no se corrige "
            f"por este camino. {r.text[:300]}")
        assert "leche" in r.text.lower(), r.text[:300]

    assert _retrato_del_flete(_leer_liq(client, h, esc["flete"]["id"])) == antes, (
        "el intento rebotado de corregir el flete le dejo cifras movidas al comprobante")

    corr = client.get(f"{API}/{esc['flete']['id']}/correcciones", headers=h)
    assert corr.status_code == 200, corr.text
    assert corr.json() == [], corr.json()


# ---------------------------------------------------------------------------
# 8. LAS FOTOS CONGELADAS SIGUEN CONGELADAS
# ---------------------------------------------------------------------------
def test_las_fotos_congeladas_del_flete_pagado_siguen_congeladas_tras_la_correccion(
    client, base_datos
):
    """Despues de corregir la leche, el dia del flete pagado sigue sin poder tocarse.

    La foto del flete de un dia que ya esta en un comprobante PAGADO es la cifra contra
    la que se le entrego la plata a Alex, y esta congelada. Corregir la quincena de la
    leche es un camino NUEVO que llega a esas mismas recepciones: si al pasar por ahi
    aflojara el candado, el PUT de la recepcion podria mover los $150.000 del 17/07
    despues de pagados.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario_dia_fijo(client, h)
    ya_cobrado, nuevo = _los_dos_dias_olvidados(client, h, esc)
    _post(client, h, f"{API}/{esc['leche']['id']}/corregir",
          {"motivo": "se anotaron tarde",
           "recepciones_a_incluir": [ya_cobrado["id"], nuevo["id"]]})

    dia = _leer_rec(client, h, esc["dia_de_marleny"]["id"])
    assert dia["flete_pagado"] is True, dia
    assert D(dia["valor_transporte"]) == FIJO

    # El PUT que intentaria mover los litros -y con ellos el reparto del fijo- rebota.
    intento = client.put(f"{REC}/{esc['dia_de_marleny']['id']}", json={
        "fecha": "2026-07-17", "proveedor_id": esc["marleny"]["id"],
        "transportador_id": esc["alex"]["id"], "cantidad_litros": "500"}, headers=h)
    assert intento.status_code == 422, (
        "se pudieron cambiar los litros de un dia con el flete pagado: "
        f"{intento.text[:300]}")
    despues = _leer_rec(client, h, esc["dia_de_marleny"]["id"])
    assert D(despues["cantidad_litros"]) == D("137.45")
    assert D(despues["valor_transporte"]) == FIJO, (
        "la foto congelada del flete de Alex se movio tras el intento rebotado")
    assert D(_leer_liq(client, h, esc["flete"]["id"])["valor_total"]) == D("300000.00")
