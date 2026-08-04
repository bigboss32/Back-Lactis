"""UNA TARIFA MAL TECLEADA SE CORRIGE, Y LA CORRECCIÓN LLEGA A LA PLATA.

EL OTRO LADO de test_liquidacion_flete_pagado.py, y son las dos mitades de lo mismo:
allá se prueba que un flete YA PAGADO no se mueve ni por un centavo; acá, que mientras
NO se haya pagado la corrección sí llega. Sin esta mitad, la protección de allá se
convierte en una cárcel: el dueño teclea $100 por litro en vez de $242,76 y el sistema
le sigue cobrando $100 para siempre.

EL DEFECTO QUE ESTAS PRUEBAS CIERRAN, con las cifras del caso: la tarifa entra en la
plata pero NO es un campo de la recepción —vive en el transportador, o en su fila de la
ruta—, así que corregirla no llegaba a las cifras ya calculadas. Un día de 44 L en
Nápoles quedaba en $4.400 cuando la tarifa buena da $10.681,44: $6.281,44 de diferencia
en un solo día. Y el comprobante IMPRIMÍA la tarifa vieja —$100 el litro—, que ya no
existía en ninguna pantalla del sistema, porque el renglón la deriva de la cifra
guardada. El dueño no tenía cómo explicar de dónde había salido.

LA REGLA QUE QUEDÓ: la cifra del flete de un día es derivada (litros × tarifa) y se
vuelve a derivar de la tarifa VIVA en los tres momentos en que se le puede llegar
—guardar el día, generar el comprobante y recalcularlo—, con una sola excepción: que
por ese flete ya haya salido plata. Así la tarifa que el dueño ve en la pantalla del
transportador es siempre la que el papel imprime y la que el conductor puede reproducir
con calculadora.

Lo que esto cuesta, dicho de frente: el sistema guarda UNA tarifa por (transportador,
ruta), sin fechas de vigencia. O sea que corregirla vale para TODO lo que no se haya
pagado, incluidas las quincenas viejas que quedaron sin liquidar. Es lo mismo que ya
pasaba con cualquier guardado del día, y es explicable en una frase; la alternativa
—tarifas con fecha de vigencia— es otra funcionalidad y no está pedida.
"""
from decimal import ROUND_HALF_UP, Decimal

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
AUDITORIA = "/api/v1/auditoria"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def leer(client, h, rec_id):
    res = client.get(f"{REC}/{rec_id}", headers=h)
    assert res.status_code == 200, res.text
    return res.json()


def leer_liq(client, h, liq_id):
    res = client.get(f"{API}/{liq_id}", headers=h)
    assert res.status_code == 200, res.text
    return res.json()


def montar(client, h, *, tarifa_ruta, tarifa_general="100", litros="44.00", con_ruta=True):
    """Alex recogiéndole a Patricia en Nápoles, con la tarifa MAL puesta."""
    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "Napoles", "municipio": "Granada"}, headers=h
    ).json()
    cuerpo = {"nombre": "Alex", "valor_transporte": tarifa_general}
    if con_ruta:
        cuerpo["rutas"] = [{"ruta_id": ruta["id"], "valor_transporte": tarifa_ruta}]
    t = client.post("/api/v1/transportadores", json=cuerpo, headers=h).json()
    prov = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Patricia", "vereda": "El Roble", "precio_litro": "1800",
              "ruta_id": ruta["id"]},
        headers=h,
    ).json()
    rec = client.post(
        REC,
        json={"fecha": "2026-06-02", "proveedor_id": prov["id"],
              "transportador_id": t["id"], "ruta_id": ruta["id"],
              "cantidad_litros": litros},
        headers=h,
    )
    assert rec.status_code == 201, rec.text
    return ruta, t, prov, rec.json()


def otro_proveedor(client, h, nombre, ruta):
    return client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": "1800",
              "ruta_id": ruta["id"]},
        headers=h,
    ).json()


def recibir(client, h, t, prov, fecha, litros, ruta):
    r = client.post(
        REC,
        json={"fecha": fecha, "proveedor_id": prov["id"], "transportador_id": t["id"],
              "ruta_id": ruta["id"], "cantidad_litros": litros},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def corregir_tarifa_ruta(client, h, t, ruta, valor):
    res = client.put(
        f"/api/v1/transportadores/{t['id']}",
        json={"rutas": [{"ruta_id": ruta["id"], "valor_transporte": valor}]},
        headers=h,
    )
    assert res.status_code == 200, res.text


def generar_flete(client, h, inicio="2026-06-01", fin="2026-06-15"):
    res = client.post(
        f"{API}/generar",
        json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": "transportador"},
        headers=h,
    )
    assert res.status_code == 200, res.text
    assert res.json(), "no se generó liquidación de flete"
    return res.json()[0]


BUENA = D("242.76")
CUARENTA_Y_CUATRO = centavos(D("44.00") * BUENA)  # $10.681,44


# ===========================================================================
# 1. GUARDAR EL DÍA: la corrección llega por ahí
# ===========================================================================
def test_corregir_la_tarifa_de_la_ruta_y_guardar_el_dia_arregla_la_foto(client, base_datos):
    """Tecleó $100 en vez de $242,76. Corrige la tarifa y vuelve a guardar el día."""
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100")
    assert D(rec["valor_transporte"]) == D("4400.00")
    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))
    assert client.put(
        f"{REC}/{rec['id']}", json={"observaciones": "corregi la tarifa"}, headers=h
    ).status_code == 200
    despues = leer(client, h, rec["id"])
    assert D(despues["valor_transporte"]) == CUARENTA_Y_CUATRO, (
        f"la foto quedó en {despues['valor_transporte']}; con la tarifa buena son "
        f"{CUARENTA_Y_CUATRO} (diferencia "
        f"{CUARENTA_Y_CUATRO - D(despues['valor_transporte'])})"
    )


def test_corregir_la_tarifa_general_y_guardar_el_dia_arregla_la_foto(client, base_datos):
    """Lo mismo pero con la tarifa GENERAL (transportador sin tarifa por ruta)."""
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta=None, tarifa_general="100", con_ruta=False)
    assert D(rec["valor_transporte"]) == D("4400.00")
    res = client.put(
        f"/api/v1/transportadores/{t['id']}", json={"valor_transporte": str(BUENA)}, headers=h
    )
    assert res.status_code == 200, res.text
    assert client.put(
        f"{REC}/{rec['id']}", json={"observaciones": "corregi"}, headers=h
    ).status_code == 200
    despues = leer(client, h, rec["id"])
    assert D(despues["valor_transporte"]) == CUARENTA_Y_CUATRO, (
        f"la foto quedó en {despues['valor_transporte']}; con la general buena son "
        f"{CUARENTA_Y_CUATRO}"
    )


def test_reenviar_el_formulario_completo_trae_la_tarifa_corregida(client, base_datos):
    """El diálogo manda TODO el formulario en cada guardado: por ahí también llega."""
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100")
    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))
    formulario = {
        "fecha": rec["fecha"],
        "transportador_id": rec["transportador_id"],
        "ruta_id": rec["ruta_id"],
        "cantidad_litros": rec["cantidad_litros"],
        "precio_litro": rec["precio_litro"],
        "bonificaciones": rec["bonificaciones"],
        "descuentos": rec["descuentos"],
        "observaciones": "corregi la tarifa",
    }
    assert client.put(f"{REC}/{rec['id']}", json=formulario, headers=h).status_code == 200
    despues = leer(client, h, rec["id"])
    assert D(despues["valor_transporte"]) == CUARENTA_Y_CUATRO


def test_mover_los_litros_y_devolverlos_deja_la_foto_con_la_tarifa_viva(client, base_datos):
    """El rodeo que el dueño tenía que hacer antes: mentirle dos veces a los litros.

    Ya no hace falta (los tres de arriba lo prueban), pero el rodeo tiene que seguir
    dando lo mismo: dos guardados que se cancelan no pueden dejar una cifra distinta
    de un guardado solo.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100")
    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))
    client.put(f"{REC}/{rec['id']}", json={"cantidad_litros": "44.01"}, headers=h)
    client.put(f"{REC}/{rec['id']}", json={"cantidad_litros": "44.00"}, headers=h)
    despues = leer(client, h, rec["id"])
    assert D(despues["valor_transporte"]) == CUARENTA_Y_CUATRO


# ===========================================================================
# 2. GENERAR EL COMPROBANTE: imprime la tarifa que existe hoy
# ===========================================================================
def test_el_comprobante_generado_imprime_la_tarifa_que_existe_hoy(client, base_datos):
    """La tarifa $100 ya NO está en el sistema: el papel no puede seguir imprimiéndola.

    Es la queja más concreta del defecto: el renglón deriva la tarifa de la cifra
    guardada, así que el comprobante salía a $100 el litro y esa tarifa no aparecía en
    ninguna pantalla. El dueño no tenía dónde ir a buscarla.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100")
    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))
    liq = generar_flete(client, h)

    tarifas_impresas = sorted({D(d["precio_litro"]) for d in liq["detalles"]})
    tarifa_viva = D(
        client.get(f"/api/v1/transportadores/{t['id']}", headers=h).json()["rutas"][0][
            "valor_transporte"
        ]
    )
    assert tarifas_impresas == [tarifa_viva] == [BUENA], (
        f"el comprobante imprime {[str(x) for x in tarifas_impresas]} y la única tarifa "
        f"que existe hoy en el sistema es {tarifa_viva}"
    )
    # Y la cuenta del dueño: 44,00 L × $242,76
    assert D(liq["valor_transporte"]) == CUARENTA_Y_CUATRO, (
        f"el comprobante salió en {liq['valor_transporte']} y la cuenta a mano da "
        f"{CUARENTA_Y_CUATRO}"
    )
    assert sum((D(d["valor"]) for d in liq["detalles"]), D(0)) == D(liq["valor_transporte"])


def test_el_avance_muestra_la_misma_tarifa_que_el_comprobante(client, base_datos):
    """La PRE-liquidación ("¿cómo voy?") no puede decir una tarifa y el papel otra.

    El avance no escribe nada, así que tiene que derivar la cifra del flete sobre
    copias en memoria. Si sumara las cifras guardadas, el dueño vería $100 en la
    pantalla del avance y $242,76 en el comprobante generado un minuto después.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100")
    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))

    pre = client.post(
        f"{API}/previsualizar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15",
              "tipo": "transportador", "tercero_id": t["id"]},
        headers=h,
    )
    assert pre.status_code in (200, 201), pre.text
    avance = pre.json()[0]
    assert D(avance["valor_transporte"]) == CUARENTA_Y_CUATRO
    assert [D(d["precio_litro"]) for d in avance["detalles"]] == [BUENA]

    # Y el avance no escribió nada: la cifra guardada sigue siendo la vieja hasta que
    # se genere el comprobante (una consulta no puede mover plata).
    assert D(leer(client, h, rec["id"])["valor_transporte"]) == D("4400.00")

    liq = generar_flete(client, h)
    assert D(liq["valor_transporte"]) == D(avance["valor_transporte"])
    assert [D(d["precio_litro"]) for d in liq["detalles"]] == [
        D(d["precio_litro"]) for d in avance["detalles"]
    ]


def test_dos_dias_de_la_misma_ruta_salen_con_una_sola_tarifa(client, base_datos):
    """Corrigen la tarifa a mitad de quincena: los dos días salen con la corregida.

    Antes el día viejo se quedaba con la vieja, el comprobante salía con DOS tarifas
    para la misma ruta, y el dueño no encontraba de dónde había salido la primera.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100", litros="44.00")
    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))
    libardo = otro_proveedor(client, h, "Libardo", ruta)
    recibir(client, h, t, libardo, "2026-06-03", "44.00", ruta)

    liq = generar_flete(client, h)
    tarifas = sorted({D(d["precio_litro"]) for d in liq["detalles"]})
    assert tarifas == [BUENA], (
        f"el comprobante de la ruta Napoles trae {len(tarifas)} tarifas distintas "
        f"({[str(x) for x in tarifas]}) y el dueño solo puso una: ${BUENA}"
    )
    assert len(liq["detalles"]) == 2, "son dos días distintos: dos renglones"
    assert D(liq["valor_transporte"]) == CUARENTA_Y_CUATRO * 2


def test_dos_recepciones_del_mismo_dia_y_ruta_quedan_en_un_renglon(client, base_datos):
    """Dos proveedores, mismo día y misma ruta, tarifa corregida en medio.

    El dueño suma los litros del día en esa ruta y los multiplica por la tarifa: eso
    TIENE que ser el total del comprobante. 44,23 + 82,48 = 126,71 L × $242,76.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, prov_a, rec_a = montar(client, h, tarifa_ruta="100", litros="44.23")
    # Le corrigen la tarifa ANTES de recibir la leche del segundo.
    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))
    prov_b = otro_proveedor(client, h, "Segundo", ruta)
    rec_b = recibir(client, h, t, prov_b, "2026-06-02", "82.48", ruta)

    liq = generar_flete(client, h)
    litros_dia = D("44.23") + D("82.48")
    a_mano = centavos(litros_dia * BUENA)
    fotos = [
        D(leer(client, h, rec_a["id"])["valor_transporte"]),
        D(leer(client, h, rec_b["id"])["valor_transporte"]),
    ]
    renglones = [(str(d["litros"]), str(d["precio_litro"]), str(d["valor"]))
                 for d in liq["detalles"]]
    # Primero las invariantes internas
    assert sum((D(d["valor"]) for d in liq["detalles"]), D(0)) == D(liq["valor_transporte"])
    assert sum(fotos, D(0)) == D(liq["valor_transporte"])
    # Y ahora la cuenta del dueño
    assert len(liq["detalles"]) == 1, f"un día y una ruta son UN renglón: {renglones}"
    assert D(liq["valor_transporte"]) == a_mano, (
        f"el dueño suma {litros_dia} L × ${BUENA} = {a_mano} y el comprobante dice "
        f"{liq['valor_transporte']}. Renglones: {renglones}. Fotos: {[str(f) for f in fotos]}"
    )


# ===========================================================================
# 3. RECALCULAR: el botón que el dueño pidió
# ===========================================================================
def test_recalcular_el_borrador_trae_la_tarifa_corregida(client, base_datos):
    """"Que se pueda recalcular la liquidación del transportador", con cifras.

    El comprobante ya está generado a la tarifa mala. El dueño corrige la tarifa en la
    pantalla del transportador y oprime Recalcular: el papel tiene que quedar con la
    tarifa buena, porque por ese flete todavía no ha salido un peso.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100")
    liq = generar_flete(client, h)
    assert D(liq["valor_transporte"]) == D("4400.00")

    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))
    res = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    assert res.status_code == 200, res.text

    liq2 = leer_liq(client, h, liq["id"])
    renglones = [(str(d["litros"]), str(d["precio_litro"]), str(d["valor"]))
                 for d in liq2["detalles"]]
    assert D(liq2["valor_transporte"]) == CUARENTA_Y_CUATRO, (
        f"recalcular dejó el comprobante en {liq2['valor_transporte']}; la cuenta a mano "
        f"da {CUARENTA_Y_CUATRO}. Renglones: {renglones}"
    )
    assert [D(d["precio_litro"]) for d in liq2["detalles"]] == [BUENA]
    assert D(liq2["valor_total"]) == D(liq2["valor_transporte"])
    assert D(liq2["saldo"]) == D(liq2["valor_total"]) - D(liq2["anticipos"])
    # La cifra guardada del día también quedó al día: el comprobante y sus recepciones
    # tienen que seguir diciendo lo mismo.
    assert D(leer(client, h, rec["id"])["valor_transporte"]) == CUARENTA_Y_CUATRO
    # Y sigue en borrador: recalcular no aprueba nada.
    assert liq2["estado"] == "borrador"


def test_recalcular_dos_veces_no_mueve_el_papel(client, base_datos):
    """Recalcular es idempotente: la segunda vuelta no puede cambiar una cifra."""
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100", litros="44.23")
    prov_b = otro_proveedor(client, h, "Segundo", ruta)
    recibir(client, h, t, prov_b, "2026-06-02", "82.48", ruta)
    liq = generar_flete(client, h)
    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))

    papeles = []
    for _ in range(3):
        assert client.post(f"{API}/{liq['id']}/recalcular", headers=h).status_code == 200
        j = leer_liq(client, h, liq["id"])
        papeles.append((
            j["valor_transporte"],
            sorted((d["fecha"], d["litros"], d["precio_litro"], d["valor"])
                   for d in j["detalles"]),
        ))
    assert papeles[0] == papeles[1] == papeles[2], f"el papel se movió: {papeles}"


def test_la_liquidacion_del_proveedor_no_deriva_su_precio(client, base_datos):
    """La plata de la LECHE sale del precio que el usuario ESCRIBIÓ, no de un catálogo.

    Recalcular la del transportador vuelve a derivar el flete de la tarifa viva; la del
    proveedor NO puede hacer lo mismo con el precio del litro, porque ese precio se
    escribe día por día en la recepción y el del catálogo del proveedor es apenas el
    valor por omisión con el que se propone. Si el recálculo lo "derivara", le
    reescribiría al dueño un precio que él puso a mano.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta=str(BUENA))
    # El día se recibió a $1.900, distinto del $1.800 del catálogo del proveedor.
    assert client.put(
        f"{REC}/{rec['id']}", json={"precio_litro": "1900"}, headers=h
    ).status_code == 200
    liq = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15",
              "tipo": "proveedor"},
        headers=h,
    ).json()[0]
    esperado = centavos(D("44.00") * D("1900"))
    assert D(liq["valor_total"]) == esperado

    # Le cambian el precio del CATÁLOGO del proveedor y recalculan: el día no se mueve.
    assert client.put(
        f"/api/v1/proveedores/{prov['id']}", json={"precio_litro": "1700"}, headers=h
    ).status_code == 200
    assert client.post(f"{API}/{liq['id']}/recalcular", headers=h).status_code == 200
    despues = leer_liq(client, h, liq["id"])
    assert D(despues["valor_total"]) == esperado, (
        f"recalcular la liquidación del proveedor le reescribió el precio del día: "
        f"${despues['valor_total']} contra ${esperado}"
    )
    assert D(despues["detalles"][0]["precio_litro"]) == D("1900")


def test_la_bitacora_del_recalculo_deja_el_antes_y_el_despues(client, base_datos):
    """Esto mueve dinero: mañana alguien va a preguntar por qué el papel cambió.

    La bitácora tiene que poder responderlo sin tener que restar dos volcados de
    treinta columnas: el total de antes, el de después y cuántos días cambiaron.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100", litros="44.23")
    prov_b = otro_proveedor(client, h, "Segundo", ruta)
    recibir(client, h, t, prov_b, "2026-06-02", "82.48", ruta)
    liq = generar_flete(client, h)
    total_antes = D(liq["valor_transporte"])

    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))
    assert client.post(f"{API}/{liq['id']}/recalcular", headers=h).status_code == 200
    total_despues = D(leer_liq(client, h, liq["id"])["valor_transporte"])
    assert total_despues != total_antes

    registros = client.get(
        f"{AUDITORIA}?modulo=liquidaciones&accion=editar", headers=h
    ).json()["items"]
    con_recalculo = [
        r for r in registros
        if r["entidad_id"] == liq["id"] and (r["despues"] or {}).get("recalculo")
    ]
    assert con_recalculo, "el recálculo del flete no quedó en la bitácora"
    huella = con_recalculo[0]["despues"]["recalculo"]
    print("\n===== LA BITÁCORA DEL RECÁLCULO =====")
    print(f"  {huella}")
    assert D(huella["valor_total_antes"]) == total_antes
    assert D(huella["valor_total_despues"]) == total_despues
    assert D(huella["valor_transporte_antes"]) == total_antes
    assert D(huella["valor_transporte_despues"]) == total_despues
    assert huella["dias_con_flete_recalculado"] == 2, (
        f"cambiaron las cifras de dos recepciones y la bitácora dice "
        f"{huella['dias_con_flete_recalculado']}"
    )


def test_recalcular_un_flete_pagado_rebota_y_no_mueve_nada(client, base_datos):
    """El límite: pagada no se recalcula, ni con la tarifa corregida encima.

    Es el mismo candado de siempre y acá se vuelve a medir desde este lado, porque
    todo lo de arriba consiste en aflojar la mano y este es el sitio donde no se
    afloja. El detalle de los ataques está en test_liquidacion_flete_pagado.py.
    """
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, tarifa_ruta="100")
    liq = generar_flete(client, h)
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert client.post(f"{API}/{liq['id']}/pagar", headers=h).status_code == 200

    corregir_tarifa_ruta(client, h, t, ruta, str(BUENA))
    res = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    assert res.status_code == 422, res.text
    despues = leer_liq(client, h, liq["id"])
    assert D(despues["valor_transporte"]) == D("4400.00")
    assert D(leer(client, h, rec["id"])["valor_transporte"]) == D("4400.00")
    assert sum((D(d["valor"]) for d in despues["detalles"]), D(0)) == D("4400.00")
