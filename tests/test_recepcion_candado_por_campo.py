"""El candado de un día de recepción es POR CAMPO, no de toda la fila.

EL CASO REAL DEL DUEÑO, tal como lo reportó: "no se deja editar, y lo que
necesito es solo poder editar el transportador, porque el transportador no se ha
liquidado".

Su recepción del 29/07 (Patricia Laguna, 44 L a $2.050 = $90.200) tenía la
liquidación del PROVEEDOR en estado Pagada, y por eso la fila entera quedaba con
candado. Pero la liquidación del TRANSPORTADOR de ese día NO se había hecho: se
equivocaron al anotar quién recogió la leche y necesitaban corregirlo. No había
ninguna razón para trabarlo — esa plata todavía no ha salido de la caja.

La causa: un día vive en DOS liquidaciones independientes y de dos personas
distintas —la leche al proveedor (`liquidacion_id`) y el flete al transportador
(`liquidacion_transporte_id`)— y bastaba con que UNA estuviera pagada para trabar
todo. La regla correcta va por campo, según a quién le mueve la plata cada uno:

  (a) LECHE PAGADA, flete sin liquidar → el transportador SÍ se corrige, y los
      litros, el precio y la fecha rebotan. Es el caso del dueño.
  (b) FLETE PAGADO, leche sin pagar    → el transportador rebota, y el precio por
      litro (que es plata del proveedor, no del transportador) se corrige.
  (c) LAS DOS PAGADAS                  → solo quedan la sucursal y las
      observaciones. Ningún campo de plata. (LA RUTA YA NO ESTÁ EN ESA LISTA:
      desde que cada ruta tiene su propia tarifa por litro, cambiarla recalcula
      el flete guardado, así que es plata y el flete pagado la traba.)
  (d) FLETE EN BORRADOR                → cambiar el transportador suelta el día de
      esa liquidación y la recalcula sin él, como ya hacía antes.
  (e) Multiempresa: la Quesera B no le corrige el transportador a un día de la
      Quesera A ni con el id en la mano.

Y el cuadre que el dueño verifica a mano tiene que quedar intacto: la liquidación
pagada NO se puede mover ni un peso por corregir el transportador.
"""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
RECEPCIONES = "/api/v1/recepciones"


def D(v):
    return Decimal(str(v))


def _transportador(client, h, nombre, tarifa):
    return client.post(
        "/api/v1/transportadores",
        json={"nombre": nombre, "valor_transporte": str(tarifa)},
        headers=h,
    ).json()


def _montar_el_dia_del_dueno(client, h, *, con_flete=True, nombre="Patricia Laguna"):
    """El 29/07 de Patricia Laguna: 44 L a $2.050, recogidos por Stella a $100/L.

    Devuelve (recepcion, liquidaciones_por_tipo, stella).
    """
    stella = _transportador(client, h, f"Stella {nombre}", "100") if con_flete else None
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": "2050"},
        headers=h,
    ).json()

    cuerpo = {
        "fecha": "2026-07-29",
        "proveedor_id": proveedor["id"],
        "cantidad_litros": "44",
    }
    if stella:
        cuerpo["transportador_id"] = stella["id"]
    recepcion = client.post(RECEPCIONES, json=cuerpo, headers=h)
    assert recepcion.status_code == 201, recepcion.text
    recepcion = recepcion.json()

    liquidaciones = client.post(
        f"{API}/generar",
        json={
            "periodo_inicio": "2026-07-16",
            "periodo_fin": "2026-07-31",
            "tipo": "ambos" if con_flete else "proveedor",
        },
        headers=h,
    ).json()
    por_tipo = {liq["tipo"]: liq for liq in liquidaciones}
    return recepcion, por_tipo, stella


def _pagar(client, h, liquidacion):
    assert client.post(f"{API}/{liquidacion['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liquidacion['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    return pagada.json()


def _detalle(respuesta):
    return respuesta.json().get("error", {}).get("detail", "")


# ---------------------------------------------------------------------------
# (a) EL CASO DEL DUEÑO: leche pagada, flete sin liquidar
# ---------------------------------------------------------------------------
def test_con_la_leche_pagada_y_el_flete_sin_liquidar_si_se_cambia_el_transportador(
    client, base_datos
):
    """El caso exacto que reportó el dueño, y el que antes era imposible.

    A Patricia ya se le pagaron sus $90.200 de leche, así que los litros, el
    precio y la fecha quedan en firme: son la cifra que ella recibió. Pero el
    flete de ese día todavía no se ha liquidado —a nadie se le ha pagado por
    recoger esa leche— y anotaron mal quién la recogió. Cambiar el transportador
    no le mueve un peso a Patricia, así que tiene que dejarse.
    """
    h = auth_headers(client, "admin.a")
    # Sin flete: la leche se liquida y se paga, y el transportador se anota después
    recepcion, liqs, _ = _montar_el_dia_del_dueno(client, h, con_flete=False)
    leche = _pagar(client, h, liqs["proveedor"])
    efrain = _transportador(client, h, "Efraín", "120")

    print("\n===== (a) EL CASO DEL DUEÑO =====")
    print(f"  el día 29/07 · 44 L × $2.050 = {leche['valor_total']} · leche {leche['estado']}")
    assert D(leche["valor_total"]) == D(44 * 2050) == D("90200")
    assert leche["estado"] == "pagada"

    # Lo que el backend le cuenta a la pantalla ANTES de intentar nada
    antes = client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h).json()
    print(f"  leche_pagada={antes['leche_pagada']} · flete_pagado={antes['flete_pagado']}")
    print(f"  trabados : {antes['campos_bloqueados']}")
    print(f"  editables: {antes['campos_editables']}")
    print(f"  aviso    : {antes['candado_aviso']}")
    assert antes["leche_pagada"] is True
    assert antes["flete_pagado"] is False
    assert "transportador_id" in antes["campos_editables"], "es lo que el dueño necesita"
    assert set(antes["campos_bloqueados"]) == {
        "fecha", "proveedor_id", "cantidad_litros", "precio_litro",
        "bonificaciones", "descuentos", "estado",
    }
    assert "Patricia Laguna" in antes["candado_aviso"], "el aviso tiene que decir a quién se le pagó"
    assert "el transportador" in antes["candado_aviso"]

    # Y AHORA SÍ: se le corrige el transportador
    r = client.put(
        f"{RECEPCIONES}/{recepcion['id']}", json={"transportador_id": efrain["id"]}, headers=h
    )
    print(f"  cambiar el transportador a Efraín: {r.status_code} {_detalle(r)}")
    assert r.status_code == 200, r.text
    assert r.json()["transportador_id"] == efrain["id"]
    # El flete se recalcula con la tarifa del que sí recogió: 44 L × $120
    print(f"  el flete del día queda en {r.json()['valor_transporte']} (44 L × $120)")
    assert D(r.json()["valor_transporte"]) == D(44 * 120) == D("5280")

    # LO QUE NO SE PUDO MOVER: la plata que ya se le pagó a Patricia
    intacta = client.get(f"{API}/{leche['id']}", headers=h).json()
    print(f"  la liquidación de Patricia sigue en {intacta['valor_total']} "
          f"({intacta['estado']}) · pagado {intacta['pagado']} · saldo {intacta['saldo']}")
    assert intacta["estado"] == "pagada"
    assert D(intacta["valor_total"]) == D("90200"), "corregir el flete le movió la plata a Patricia"
    assert D(intacta["pagado"]) == D("90200")
    assert D(intacta["saldo"]) == D(0)
    suma_dias = sum((D(d["valor"]) for d in intacta["detalles"]), D(0))
    print(f"  y sus renglones siguen sumando {suma_dias} = {intacta['valor_total']}")
    assert suma_dias == D(intacta["valor_total"]), "el desglose dejó de sumar la cifra grande"


def test_con_la_leche_pagada_los_litros_el_precio_y_la_fecha_rebotan(client, base_datos):
    """La otra mitad de la regla: aflojar el candado del transportador no puede
    abrir la puerta a cambiar la cifra que ya se le pagó.

    Los tres campos que rebotan son los que arman los $90.200 del comprobante que
    Patricia ya recibió: si se movieran, el papel que ella tiene en la mano y el
    sistema dirían cosas distintas.
    """
    h = auth_headers(client, "admin.a")
    recepcion, liqs, _ = _montar_el_dia_del_dueno(client, h, con_flete=False)
    leche = _pagar(client, h, liqs["proveedor"])

    print("\n===== (a2) CON LA LECHE PAGADA, LA PLATA DE LA LECHE NO SE TOCA =====")
    for campo, valor, etiqueta in (
        ("cantidad_litros", "999", "los litros"),
        ("precio_litro", "3000", "el precio por litro"),
        ("fecha", "2026-07-20", "la fecha"),
        ("bonificaciones", "50000", "las bonificaciones"),
        ("descuentos", "10000", "los descuentos"),
    ):
        r = client.put(f"{RECEPCIONES}/{recepcion['id']}", json={campo: valor}, headers=h)
        print(f"  {etiqueta:<22} → {r.status_code} · {_detalle(r)[:95]}")
        assert r.status_code == 422, f"{campo} tenía que rebotar: es plata ya pagada"
        assert "ya se pagó" in _detalle(r)
        # El mensaje dice QUÉ se puede corregir, que es lo que el dueño no sabía
        assert "Sí se puede corregir" in _detalle(r)

    # Nada quedó a medias
    sigue = client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h).json()
    intacta = client.get(f"{API}/{leche['id']}", headers=h).json()
    print(f"  el día sigue en {sigue['cantidad_litros']} L × ${sigue['precio_litro']} "
          f"el {sigue['fecha']} · la liquidación en {intacta['valor_total']}")
    assert D(sigue["cantidad_litros"]) == D(44)
    assert D(sigue["precio_litro"]) == D(2050)
    assert sigue["fecha"] == "2026-07-29"
    assert D(intacta["valor_total"]) == D("90200")


def test_los_campos_que_llegan_iguales_no_rebotan(client, base_datos):
    """El diálogo manda TODO el formulario en cada guardado, no solo lo que cambió.

    Si el guardia mirara la simple presencia del campo, corregirle el
    transportador a este día rebotaría por culpa de unos litros que llegaron
    idénticos a los guardados: el usuario no los tocó. Se compara contra lo que
    está en la base, y '44' y '44.00' son la misma leche.
    """
    h = auth_headers(client, "admin.a")
    recepcion, liqs, _ = _montar_el_dia_del_dueno(client, h, con_flete=False)
    _pagar(client, h, liqs["proveedor"])
    efrain = _transportador(client, h, "Efraín", "120")

    print("\n===== (a3) EL FORMULARIO COMPLETO, CON LOS MISMOS VALORES =====")
    formulario_completo = {
        "fecha": "2026-07-29",          # igual
        "cantidad_litros": "44.00",     # igual, escrito con decimales
        "precio_litro": "2050.00",      # igual
        "bonificaciones": "0",          # igual
        "descuentos": "0",              # igual
        "transportador_id": efrain["id"],  # LO ÚNICO que cambia
        "observaciones": "Lo recogió Efraín, no Stella",
    }
    r = client.put(f"{RECEPCIONES}/{recepcion['id']}", json=formulario_completo, headers=h)
    print(f"  guardar el formulario entero: {r.status_code} · {_detalle(r)[:90]}")
    assert r.status_code == 200, r.text
    print(f"  transportador → Efraín · observaciones → {r.json()['observaciones']!r}")
    assert r.json()["transportador_id"] == efrain["id"]
    assert r.json()["observaciones"] == "Lo recogió Efraín, no Stella"


# ---------------------------------------------------------------------------
# (b) FLETE PAGADO: rebota el transportador, no el precio de la leche
# ---------------------------------------------------------------------------
def test_con_el_flete_pagado_el_transportador_rebota(client, base_datos):
    """El espejo del caso del dueño. Si a Stella ya se le pagó el flete de esa
    quincena, pasarle el día a Efraín le quitaría a Stella un día que ya cobró:
    su comprobante quedaría diciendo una cifra que sus recepciones ya no
    respaldan.

    Y al revés de lo que pasaba antes, el flete pagado NO traba el precio por
    litro de la leche: ese es plata del proveedor y a él todavía no se le ha
    pagado nada.
    """
    h = auth_headers(client, "admin.a")
    recepcion, liqs, stella = _montar_el_dia_del_dueno(client, h, con_flete=True)
    flete = _pagar(client, h, liqs["transportador"])
    efrain = _transportador(client, h, "Efraín", "120")

    print("\n===== (b) FLETE PAGADO, LECHE EN BORRADOR =====")
    print(f"  flete de Stella {flete['valor_total']} ({flete['estado']}) · "
          f"leche {liqs['proveedor']['estado']}")
    assert D(flete["valor_total"]) == D(44 * 100) == D("4400")

    estado = client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h).json()
    print(f"  leche_pagada={estado['leche_pagada']} · flete_pagado={estado['flete_pagado']}")
    print(f"  trabados : {estado['campos_bloqueados']}")
    print(f"  editables: {estado['campos_editables']}")
    print(f"  aviso    : {estado['candado_aviso']}")
    assert estado["flete_pagado"] is True
    assert estado["leche_pagada"] is False
    # `ruta_id` entró a esta lista cuando la tarifa pasó a ser POR RUTA: la ruta
    # del día escoge cuánto se le paga por litro, así que con el flete ya pagado
    # cambiarla movería una cifra que ya salió de la caja.
    assert set(estado["campos_bloqueados"]) == {
        "cantidad_litros", "transportador_id", "ruta_id", "fecha", "estado"
    }
    # El proveedor no se ofrece NUNCA, ni cuando nada lo traba: no existe en
    # RecepcionUpdate, así que anunciarlo mandaría al usuario a intentar algo
    # imposible. Aquí se nota porque el flete no lo traba y sin esta regla habría
    # salido en la lista de "sí se puede corregir".
    assert "proveedor_id" not in estado["campos_editables"]
    assert "el proveedor" not in estado["candado_aviso"]

    # El transportador rebota: ese flete ya se pagó
    r = client.put(
        f"{RECEPCIONES}/{recepcion['id']}", json={"transportador_id": efrain["id"]}, headers=h
    )
    print(f"  cambiar el transportador: {r.status_code} · {_detalle(r)[:95]}")
    assert r.status_code == 422
    assert "el flete ya se pagó" in _detalle(r)

    # Los litros también: mueven las DOS cuentas (el flete se cobra por litro)
    litros = client.put(f"{RECEPCIONES}/{recepcion['id']}", json={"cantidad_litros": "60"}, headers=h)
    print(f"  cambiar los litros:       {litros.status_code} · {_detalle(litros)[:95]}")
    assert litros.status_code == 422
    assert "el flete ya se pagó" in _detalle(litros)

    # PERO el precio por litro NO: es plata del proveedor, que sigue sin pagar
    precio = client.put(f"{RECEPCIONES}/{recepcion['id']}", json={"precio_litro": "2100"}, headers=h)
    print(f"  cambiar el precio/litro:  {precio.status_code} · nuevo bruto "
          f"{precio.json().get('valor_bruto')}")
    assert precio.status_code == 200, precio.text
    assert D(precio.json()["valor_bruto"]) == D(44 * 2100)

    # Y el flete de Stella no se movió ni un peso
    sigue = client.get(f"{API}/{flete['id']}", headers=h).json()
    print(f"  el flete de Stella sigue en {sigue['valor_total']} ({sigue['estado']}) · "
          f"pagado {sigue['pagado']}")
    assert sigue["estado"] == "pagada"
    assert D(sigue["valor_total"]) == D("4400")
    assert D(sigue["pagado"]) == D("4400")


# ---------------------------------------------------------------------------
# (c) LAS DOS PAGADAS: no queda ningún campo de plata
# ---------------------------------------------------------------------------
def test_con_las_dos_pagadas_solo_quedan_la_sucursal_y_las_observaciones(
    client, base_datos
):
    """Cuando las dos platas ya salieron no queda NINGÚN campo de plata por tocar.

    Lo que sí se deja son la sucursal y las observaciones: son el dato de
    clasificación y la anotación libre, no entran en ninguna liquidación y no le
    mueven un peso a nadie. Se decidió dejarlos editables a propósito, porque poder
    escribir "este día lo recogió Efraín, quedó mal anotado" es justamente lo que
    salva la historia cuando la cifra ya no se puede corregir.

    LA RUTA SALIÓ DE ESTA LISTA, y es el cambio a fijarse: antes era una etiqueta
    más, pero desde que cada ruta tiene su propia tarifa por litro ("cada ruta
    puede tener un valor diferente de litro por leche") la ruta del día es la que
    escoge cuánto se le paga al transportador. Con el flete ya pagado, cambiarla
    recalcularía el flete guardado y el comprobante que el transportador tiene en
    la mano dejaría de cuadrar contra sus recepciones.
    """
    h = auth_headers(client, "admin.a")
    recepcion, liqs, _ = _montar_el_dia_del_dueno(client, h, con_flete=True)
    leche = _pagar(client, h, liqs["proveedor"])
    flete = _pagar(client, h, liqs["transportador"])

    print("\n===== (c) LAS DOS PAGADAS =====")
    print(f"  leche {leche['valor_total']} ({leche['estado']}) · "
          f"flete {flete['valor_total']} ({flete['estado']})")

    estado = client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h).json()
    print(f"  trabados : {estado['campos_bloqueados']}")
    print(f"  editables: {estado['campos_editables']}")
    print(f"  aviso    : {estado['candado_aviso']}")
    assert estado["leche_pagada"] is True
    assert estado["flete_pagado"] is True
    assert sorted(estado["campos_editables"]) == ["observaciones", "sucursal_id"]
    # El aviso menciona a los DOS, porque son dos platas de dos personas
    aviso = estado["candado_aviso"].lower()
    assert "la leche de este día ya se le pagó" in aviso
    assert "el flete de este día ya se le pagó" in aviso
    assert "sí se puede corregir la sucursal y las observaciones" in aviso
    assert "no se puede cambiar" in aviso and "la ruta" in aviso

    # Todo lo que es plata rebota. La ruta va en la lista desde que escoge la
    # tarifa: es plata del transportador, igual que el transportador mismo.
    for campo, valor in (
        ("cantidad_litros", "60"),
        ("precio_litro", "2100"),
        ("transportador_id", _transportador(client, h, "Efraín", "120")["id"]),
        ("ruta_id", client.post(
            "/api/v1/rutas", json={"nombre": "Ruta Nueva"}, headers=h
        ).json()["id"]),
        ("fecha", "2026-07-20"),
    ):
        r = client.put(f"{RECEPCIONES}/{recepcion['id']}", json={campo: valor}, headers=h)
        print(f"  {campo:<18} → {r.status_code}")
        assert r.status_code == 422, f"{campo} tenía que rebotar con las dos pagadas"

    # Las observaciones sí: es lo único que queda para dejar constancia
    nota = client.put(
        f"{RECEPCIONES}/{recepcion['id']}",
        json={"observaciones": "Quedó mal anotado quién recogió; ya se pagó todo"},
        headers=h,
    )
    print(f"  observaciones      → {nota.status_code} · {nota.json().get('observaciones')!r}")
    assert nota.status_code == 200, nota.text
    assert "mal anotado" in nota.json()["observaciones"]

    # Y borrar el día sigue prohibido: eso lo saca de las DOS liquidaciones
    borrar = client.delete(f"{RECEPCIONES}/{recepcion['id']}", headers=h)
    print(f"  borrar el día      → {borrar.status_code} · {_detalle(borrar)[:80]}")
    assert borrar.status_code == 422

    # Las dos liquidaciones intactas, con sus desgloses cuadrando
    for nombre, liq in (("leche", leche), ("flete", flete)):
        ahora = client.get(f"{API}/{liq['id']}", headers=h).json()
        suma = sum((D(d["valor"]) for d in ahora["detalles"]), D(0))
        print(f"  {nombre}: {ahora['valor_total']} ({ahora['estado']}) · "
              f"renglones suman {suma}")
        assert ahora["estado"] == "pagada"
        assert D(ahora["valor_total"]) == D(liq["valor_total"])
        assert suma == D(ahora["valor_total"])


# ---------------------------------------------------------------------------
# (d) FLETE EN BORRADOR: se suelta y se recalcula, como ya hacía
# ---------------------------------------------------------------------------
def test_con_la_leche_pagada_y_el_flete_en_borrador_el_dia_se_suelta_y_recalcula(
    client, base_datos
):
    """Lo que ya funcionaba y no se podía romper, ahora combinado con el caso nuevo.

    Aquí la leche está PAGADA y el flete está en BORRADOR. Antes esta corrección
    era imposible (la fila entera estaba trabada por la leche). Ahora se hace, y
    tiene que seguir pasando lo de siempre: el día se suelta de la liquidación de
    flete de Stella —que se recalcula sin él— y queda libre para liquidárselo a
    Efraín, que fue el que de verdad recogió.

    El detalle fino: al recuadrar hay que saltarse la liquidación PAGADA de la
    leche. Si se intentara recalcular, rebotaría con un error y se llevaría por
    delante una corrección legítima que ni la tocaba.
    """
    h = auth_headers(client, "admin.a")
    # Dos días: el 29 es el que se corrige, el 30 se queda con Stella para que la
    # liquidación de ella no se quede vacía y se pueda ver el recálculo.
    stella = _transportador(client, h, "Stella", "100")
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Patricia Laguna", "vereda": "El Roble", "precio_litro": "2050"},
        headers=h,
    ).json()
    ids = {}
    for fecha, litros in (("2026-07-29", "44"), ("2026-07-30", "50")):
        ids[fecha] = client.post(
            RECEPCIONES,
            json={
                "fecha": fecha,
                "proveedor_id": proveedor["id"],
                "cantidad_litros": litros,
                "transportador_id": stella["id"],
            },
            headers=h,
        ).json()["id"]
    liqs = {
        liq["tipo"]: liq
        for liq in client.post(
            f"{API}/generar",
            json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31", "tipo": "ambos"},
            headers=h,
        ).json()
    }
    leche = _pagar(client, h, liqs["proveedor"])  # la leche SÍ se paga
    flete_stella = liqs["transportador"]          # el flete se queda en borrador
    efrain = _transportador(client, h, "Efraín", "120")

    print("\n===== (d) LECHE PAGADA + FLETE EN BORRADOR =====")
    print(f"  leche {leche['valor_total']} ({leche['estado']}) · "
          f"flete de Stella {flete_stella['valor_total']} ({flete_stella['estado']}) "
          f"= 94 L × $100")
    assert D(leche["valor_total"]) == D((44 + 50) * 2050)
    assert D(flete_stella["valor_total"]) == D(94 * 100)
    assert flete_stella["estado"] == "borrador"

    # El aviso tiene que explicar que al cambiarlo se mueve la liquidación de flete
    estado = client.get(f"{RECEPCIONES}/{ids['2026-07-29']}", headers=h).json()
    print(f"  aviso: {estado['candado_aviso']}")
    assert "se suelta de esa liquidación" in estado["candado_aviso"]

    r = client.put(
        f"{RECEPCIONES}/{ids['2026-07-29']}", json={"transportador_id": efrain["id"]}, headers=h
    )
    print(f"  el 29/07 pasa a Efraín: {r.status_code} · {_detalle(r)[:90]}")
    assert r.status_code == 200, r.text

    # La liquidación de Stella se recalculó SIN el día 29
    stella_ahora = client.get(f"{API}/{flete_stella['id']}", headers=h).json()
    fechas = sorted(d["fecha"] for d in stella_ahora["detalles"])
    suma = sum((D(d["valor"]) for d in stella_ahora["detalles"]), D(0))
    print(f"  Stella queda con {fechas} · {stella_ahora['total_litros']} L · "
          f"{stella_ahora['valor_total']} · renglones suman {suma}")
    assert fechas == ["2026-07-30"], "el día que ya no recogió Stella se quedó en su comprobante"
    assert D(stella_ahora["valor_total"]) == D(50 * 100)
    assert suma == D(stella_ahora["valor_total"])

    # Y el flete del 29 queda libre: se le liquida a Efraín
    nuevas = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31", "tipo": "transportador"},
        headers=h,
    ).json()
    de_efrain = next(x for x in nuevas if x["transportador_id"] == efrain["id"])
    print(f"  a Efraín: {de_efrain['total_litros']} L × $120 = {de_efrain['valor_total']}")
    assert D(de_efrain["valor_total"]) == D(44 * 120)

    # Y la liquidación PAGADA de la leche no se movió: es lo que el recuadre saltó
    intacta = client.get(f"{API}/{leche['id']}", headers=h).json()
    print(f"  la leche de Patricia sigue en {intacta['valor_total']} ({intacta['estado']}) · "
          f"pagado {intacta['pagado']} · saldo {intacta['saldo']}")
    assert intacta["estado"] == "pagada"
    assert D(intacta["valor_total"]) == D((44 + 50) * 2050)
    assert D(intacta["pagado"]) == D(intacta["valor_total"])
    assert D(intacta["saldo"]) == D(0)


# ---------------------------------------------------------------------------
# (e) Multiempresa
# ---------------------------------------------------------------------------
def test_no_se_cruzan_las_empresas(client, base_datos):
    """El candado por campo abrió una puerta nueva (cambiar el transportador de un
    día con la leche pagada) y esa puerta también tiene que estar cerrada para el
    vecino: el admin de la Quesera B no le corrige el transportador a un día de la
    Quesera A ni con el id en la mano. Es plata de un competidor.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    recepcion, liqs, _ = _montar_el_dia_del_dueno(client, h_a, con_flete=False)
    leche = _pagar(client, h_a, liqs["proveedor"])
    # Un transportador de la empresa B, para intentar el cruce completo
    de_b = _transportador(client, h_b, "Transportador de B", "500")

    print("\n===== (e) OTRA EMPRESA =====")
    editar = client.put(
        f"{RECEPCIONES}/{recepcion['id']}", json={"transportador_id": de_b["id"]}, headers=h_b
    )
    ver = client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h_b)
    borrar = client.delete(f"{RECEPCIONES}/{recepcion['id']}", headers=h_b)
    print(f"  admin.b cambiando el transportador del día de A: {editar.status_code}")
    print(f"  admin.b mirando el día de A:                     {ver.status_code}")
    print(f"  admin.b borrando el día de A:                    {borrar.status_code}")
    assert editar.status_code == 404
    assert ver.status_code == 404
    assert borrar.status_code == 404

    # Y tampoco se le puede meter un transportador de B a un día de A desde A
    cruce = client.put(
        f"{RECEPCIONES}/{recepcion['id']}", json={"transportador_id": de_b["id"]}, headers=h_a
    )
    print(f"  admin.a poniéndole el transportador de B:        {cruce.status_code}")
    assert cruce.status_code == 404, "un transportador de otra empresa no existe para A"

    sigue = client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h_a).json()
    intacta = client.get(f"{API}/{leche['id']}", headers=h_a).json()
    print(f"  el día de A sigue con transportador {sigue['transportador_id']} · "
          f"la liquidación en {intacta['valor_total']} ({intacta['estado']})")
    assert sigue["transportador_id"] is None
    assert D(intacta["valor_total"]) == D("90200")
    assert intacta["estado"] == "pagada"


# ---------------------------------------------------------------------------
# La grilla y la lista tienen que decir la verdad nueva
# ---------------------------------------------------------------------------
def test_la_grilla_distingue_cual_de_las_dos_platas_esta_pagada(client, base_datos):
    """El tooltip de la celda decía "Pagada — no editable" en cuanto CUALQUIERA de
    las dos liquidaciones tenía pagos. Con la regla nueva eso sería mentira en el
    caso más común: con la leche pagada y el flete sin liquidar, el día sí se
    puede corregir.

    La celda ahora trae las dos platas por separado para que la pantalla pueda
    decir qué se puede cambiar y qué no.
    """
    h = auth_headers(client, "admin.a")
    # Un día con la LECHE pagada y el flete sin liquidar
    recepcion_leche, liqs_leche, _ = _montar_el_dia_del_dueno(
        client, h, con_flete=False, nombre="Solo leche pagada"
    )
    _pagar(client, h, liqs_leche["proveedor"])

    # Otro día, otro proveedor, con el FLETE pagado y la leche en borrador
    _, liqs_flete, _ = _montar_el_dia_del_dueno(client, h, nombre="Solo flete pagado")
    _pagar(client, h, liqs_flete["transportador"])

    grilla = client.get(
        f"{RECEPCIONES}/grilla/quincena?desde=2026-07-16&hasta=2026-07-31", headers=h
    ).json()
    celdas = {
        fila["proveedor_nombre"]: celda
        for fila in grilla["filas"]
        for celda in fila["celdas"].values()
    }

    print("\n===== LA GRILLA =====")
    for nombre, celda in celdas.items():
        print(f"  {nombre:<20} · pagada={celda['pagada']} · "
              f"leche_pagada={celda['leche_pagada']} · flete_pagado={celda['flete_pagado']} · "
              f"estado={celda['liquidacion_estado']}")

    leche = celdas["Solo leche pagada"]
    assert leche["leche_pagada"] is True
    assert leche["flete_pagado"] is False, "no hay liquidación de flete: nada que pagar"

    flete = celdas["Solo flete pagado"]
    assert flete["flete_pagado"] is True
    assert flete["leche_pagada"] is False, "la leche sigue en borrador"

    # `pagada` se conserva para el ícono de candado: los dos tienen campos trabados
    assert leche["pagada"] is True and flete["pagada"] is True
