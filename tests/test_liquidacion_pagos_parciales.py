"""Pagos parciales (abonos) sobre una liquidación.

Lo pidió el dueño con estas palabras: "el pagado no siempre es pagado
definitivo; a un proveedor se le puede pagar y quedar debiendo otra parte".
Hasta ahora "Pagar" era todo o nada: cambiaba el estado a 'pagada' y listo.

Lo que se fija aquí es que abonar por partes no abra ningún hueco por donde se
descuadre la plata —que es lo que el dueño detecta cuadrando a mano contra el
cuaderno—:

  (a) Dos abonos dejan `pagado` y `saldo` exactos, y el estado pasa solo de
      parcial a pagada. En todo momento: neto a pagar = pagado + saldo.
  (b) No se puede abonar más que el saldo. Ni de a poquitos: el tercer abono que
      se pasa por un peso rebota.
  (c) En BORRADOR no se paga: esas cifras todavía no están en firme y el
      borrador se recalcula solo cuando cambian las recepciones.
  (d) Borrar un pago mal registrado devuelve el saldo y el estado.
  (e) Borrar un pago exige el permiso 'liquidaciones:eliminar', NO 'crear': con
      'crear' cualquiera que anota pagos podría además borrarlos y tapar una
      entrega de plata.
  (f) Con UN pago registrado (aunque falte la mitad), la recepción de esa
      liquidación ya no se puede editar ni borrar: ese abono se hizo contra unas
      cifras que no pueden cambiar debajo.
  (g) Multiempresa: la Quesera B no le paga ni le borra pagos a una liquidación
      de la Quesera A ni con el id en la mano.
"""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"


def D(v):
    return Decimal(str(v))


def _montar(client, headers, dias, precio="1800", nombre="Libardo"):
    """Un proveedor con sus días anotados y la liquidación de la quincena APROBADA.

    Devuelve (recepciones_por_fecha, liquidacion).
    """
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=headers,
    ).json()

    recepciones = {}
    for fecha, litros in dias:
        r = client.post(
            "/api/v1/recepciones",
            json={"fecha": fecha, "proveedor_id": proveedor["id"], "cantidad_litros": str(litros)},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        recepciones[fecha] = r.json()

    generadas = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"},
        headers=headers,
    ).json()["generadas"]
    liq = next(x for x in generadas if x["proveedor_id"] == proveedor["id"])
    return recepciones, liq


def _aprobar(client, headers, liq):
    r = client.post(f"{API}/{liq['id']}/aprobar", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _pagar(client, headers, liq_id, fecha, valor, observaciones=None):
    return client.post(
        f"{API}/{liq_id}/pagos",
        json={"fecha": fecha, "valor": str(valor), "observaciones": observaciones},
        headers=headers,
    )


def _cuadra(liq) -> bool:
    """La igualdad que el dueño verifica a mano: lo pagado más lo que falta da
    exacto lo que se le tenía que entregar."""
    return D(liq["pagado"]) + D(liq["saldo"]) == D(liq["neto_a_pagar"])


# ---------------------------------------------------------------------------
# (a) Dos abonos: las cifras quedan exactas y el estado se mueve solo
# ---------------------------------------------------------------------------
def test_dos_pagos_parciales_dejan_pagado_y_saldo_exactos(client, base_datos):
    """El caso que pidió el dueño, tal cual: se le paga una parte al proveedor y
    se le queda debiendo el resto, y días después se le completa.

    El estado no se escribe a mano en ningún lado: sale de las cifras. Mientras
    deba algo la liquidación está 'parcial'; cuando el saldo llega a cero pasa a
    'pagada' sola. Si el estado y las cifras pudieran contradecirse, el dueño se
    encontraría una liquidación "pagada" a la que todavía le debe.
    """
    h = auth_headers(client, "admin.a")
    _, liq = _montar(client, h, [("2026-06-01", "100"), ("2026-06-02", "150")])
    aprobada = _aprobar(client, h, liq)

    print("\n===== (a) DOS PAGOS PARCIALES =====")
    print(f"  la liquidación aprobada · 250 L × $1.800 · valor total "
          f"{aprobada['valor_total']} · neto a pagar {aprobada['neto_a_pagar']} · "
          f"estado {aprobada['estado']}")
    assert D(aprobada["neto_a_pagar"]) == D(250 * 1800)  # 450.000
    assert D(aprobada["pagado"]) == D(0)
    assert D(aprobada["saldo"]) == D(450000)

    primero = _pagar(client, h, liq["id"], "2026-06-16", "200000", "Abono en efectivo")
    assert primero.status_code == 200, primero.text
    parcial = primero.json()
    print(f"  1er pago $200.000 → pagado {parcial['pagado']} · saldo {parcial['saldo']} · "
          f"estado {parcial['estado']}")
    assert parcial["estado"] == "parcial", "debiendo todavía, no puede estar 'pagada'"
    assert D(parcial["pagado"]) == D(200000)
    assert D(parcial["saldo"]) == D(250000)
    assert _cuadra(parcial), "pagado + saldo dejó de dar el neto a pagar"
    assert len(parcial["pagos"]) == 1, "el pago recién hecho no salió en el historial"
    # El nombre del tercero tiene que seguir viajando en la respuesta. Importa
    # porque para poder bloquear la fila (FOR UPDATE) hay que apagar la carga
    # anticipada de proveedor/transportador: si esa carga diferida no se
    # resolviera, la pantalla mostraría un guion donde va el nombre del productor.
    assert parcial["proveedor_nombre"] == "Libardo"

    segundo = _pagar(client, h, liq["id"], "2026-06-20", "250000", "Se completa")
    assert segundo.status_code == 200, segundo.text
    pagada = segundo.json()
    print(f"  2do pago $250.000 → pagado {pagada['pagado']} · saldo {pagada['saldo']} · "
          f"estado {pagada['estado']}")
    assert pagada["estado"] == "pagada", "sin saldo pendiente tiene que quedar pagada sola"
    assert D(pagada["pagado"]) == D(450000)
    assert D(pagada["saldo"]) == D(0)
    assert _cuadra(pagada)

    # El historial queda completo y en orden, que es lo que se le muestra al
    # proveedor cuando pregunta "¿usted qué me ha pagado?"
    historial = [(p["fecha"], p["valor"], p["observaciones"]) for p in pagada["pagos"]]
    for fecha, valor, nota in historial:
        print(f"     · {fecha} · {valor} · {nota}")
    assert [f for f, _, _ in historial] == ["2026-06-16", "2026-06-20"]
    assert sum((D(v) for _, v, _ in historial), D(0)) == D(pagada["pagado"]), \
        "la suma del historial no da la columna 'pagado'"

    # Y lo que ve la lista es lo mismo que devolvió el pago
    desde_la_lista = client.get(f"{API}/{liq['id']}", headers=h).json()
    assert D(desde_la_lista["pagado"]) == D(450000)
    assert D(desde_la_lista["saldo"]) == D(0)
    assert desde_la_lista["estado"] == "pagada"


# ---------------------------------------------------------------------------
# (b) No se puede abonar más que el saldo
# ---------------------------------------------------------------------------
def test_no_se_puede_pagar_mas_que_el_saldo(client, base_datos):
    """Un pago de más deja el saldo NEGATIVO, y ese negativo resta de la tarjeta
    "liquidaciones por pagar" del tablero: mostraría menos deuda de la que la
    quesera tiene. Se rebota, y se rebota también el que se pasa por un peso
    después de dos abonos buenos.
    """
    h = auth_headers(client, "admin.a")
    _, liq = _montar(client, h, [("2026-06-01", "100")])  # 100 L × 1800 = 180.000
    _aprobar(client, h, liq)

    print("\n===== (b) NO SE ABONA MÁS QUE EL SALDO =====")
    de_una = _pagar(client, h, liq["id"], "2026-06-16", "180001")
    print(f"  de una, $180.001 sobre $180.000: {de_una.status_code} · "
          f"{de_una.json().get('error', {}).get('detail', '')}")
    assert de_una.status_code == 422
    assert "supera el saldo" in de_una.json()["error"]["detail"]

    # Y de a poquitos tampoco: 100.000 + 79.000 pasan, el que se pasa por $1 no
    assert _pagar(client, h, liq["id"], "2026-06-16", "100000").status_code == 200
    assert _pagar(client, h, liq["id"], "2026-06-17", "79000").status_code == 200
    ultimo = _pagar(client, h, liq["id"], "2026-06-18", "1001")
    print(f"  de a poquitos, $1.001 sobre $1.000 de saldo: {ultimo.status_code} · "
          f"{ultimo.json().get('error', {}).get('detail', '')}")
    assert ultimo.status_code == 422

    intacta = client.get(f"{API}/{liq['id']}", headers=h).json()
    print(f"  la liquidación sigue en · pagado {intacta['pagado']} · saldo {intacta['saldo']} · "
          f"estado {intacta['estado']} · {len(intacta['pagos'])} pagos")
    assert D(intacta["pagado"]) == D(179000), "el pago rechazado no podía dejar rastro"
    assert D(intacta["saldo"]) == D(1000)
    assert intacta["estado"] == "parcial"
    assert len(intacta["pagos"]) == 2
    assert _cuadra(intacta)

    # El peso que falta sí entra, y ahí queda pagada
    exacto = _pagar(client, h, liq["id"], "2026-06-18", "1000")
    assert exacto.status_code == 200, exacto.text
    print(f"  el saldo exacto $1.000: estado {exacto.json()['estado']} · "
          f"saldo {exacto.json()['saldo']}")
    assert exacto.json()["estado"] == "pagada"
    assert D(exacto.json()["saldo"]) == D(0)

    # Y sobre una pagada ya no se abona más
    de_mas = _pagar(client, h, liq["id"], "2026-06-19", "1")
    print(f"  un peso más sobre la pagada: {de_mas.status_code} · "
          f"{de_mas.json().get('error', {}).get('detail', '')}")
    assert de_mas.status_code == 422


# ---------------------------------------------------------------------------
# (c) En borrador no se paga
# ---------------------------------------------------------------------------
def test_una_liquidacion_en_borrador_no_se_puede_pagar(client, base_datos):
    """Un borrador se recalcula solo cuando cambian las recepciones o entra un
    anticipo. Si se pudiera abonar ahí, el total contra el que se abonó cambiaría
    debajo del pago y el abono quedaría descuadrado sin que nadie lo note.

    El guardia está en el BACKEND: quien conozca la dirección del endpoint entra
    igual aunque la pantalla no muestre el botón.
    """
    h = auth_headers(client, "admin.a")
    _, liq = _montar(client, h, [("2026-06-01", "100")])

    print("\n===== (c) EN BORRADOR NO SE PAGA =====")
    assert liq["estado"] == "borrador"
    r = _pagar(client, h, liq["id"], "2026-06-16", "50000")
    print(f"  abonar a un borrador: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "aprobada" in r.json()["error"]["detail"]

    intacta = client.get(f"{API}/{liq['id']}", headers=h).json()
    print(f"  sigue en borrador · pagado {intacta['pagado']} · {len(intacta['pagos'])} pagos")
    assert intacta["estado"] == "borrador"
    assert D(intacta["pagado"]) == D(0)
    assert intacta["pagos"] == []

    # Aprobada sí, y es lo único que cambia
    _aprobar(client, h, liq)
    ok = _pagar(client, h, liq["id"], "2026-06-16", "50000")
    print(f"  y aprobada sí: {ok.status_code} · estado {ok.json()['estado']}")
    assert ok.status_code == 200, ok.text
    assert ok.json()["estado"] == "parcial"

    # Una anulada tampoco
    _, otra = _montar(client, h, [("2026-06-02", "80")], nombre="Anulado")
    client.post(f"{API}/{otra['id']}/anular", headers=h)
    anulada = _pagar(client, h, otra["id"], "2026-06-16", "1000")
    print(f"  abonar a una anulada: {anulada.status_code}")
    assert anulada.status_code == 422


# ---------------------------------------------------------------------------
# (d) Borrar un pago devuelve el saldo y el estado
# ---------------------------------------------------------------------------
def test_borrar_un_pago_devuelve_el_saldo_y_el_estado(client, base_datos):
    """Un pago se puede anotar mal (dos veces el mismo, o la cifra equivocada).
    Al borrarlo, el saldo tiene que volver EXACTO a donde estaba y el estado
    retroceder: de pagada a parcial, y de parcial a aprobada cuando se borra el
    último. Si el estado se quedara en 'pagada', esa deuda desaparecería del
    tablero.
    """
    h = auth_headers(client, "admin.a")
    _, liq = _montar(client, h, [("2026-06-01", "100"), ("2026-06-02", "150")])
    _aprobar(client, h, liq)  # neto a pagar: 450.000

    print("\n===== (d) BORRAR UN PAGO MAL REGISTRADO =====")
    _pagar(client, h, liq["id"], "2026-06-16", "200000")
    completa = _pagar(client, h, liq["id"], "2026-06-20", "250000").json()
    print(f"  con los dos pagos · pagado {completa['pagado']} · saldo {completa['saldo']} · "
          f"estado {completa['estado']}")
    assert completa["estado"] == "pagada"

    segundo_id = next(p["id"] for p in completa["pagos"] if D(p["valor"]) == D(250000))
    r = client.delete(f"{API}/{liq['id']}/pagos/{segundo_id}", headers=h)
    assert r.status_code == 200, r.text
    devuelta = r.json()
    print(f"  se borra el de $250.000 → pagado {devuelta['pagado']} · "
          f"saldo {devuelta['saldo']} · estado {devuelta['estado']} · "
          f"{len(devuelta['pagos'])} pagos en la lista")
    assert devuelta["estado"] == "parcial", "vuelve a deber: no puede seguir pagada"
    assert D(devuelta["pagado"]) == D(200000)
    assert D(devuelta["saldo"]) == D(250000)
    assert len(devuelta["pagos"]) == 1, "la lista se quedó con el pago borrado adentro"
    assert devuelta["proveedor_nombre"] == "Libardo", "se perdió el nombre del tercero"
    assert _cuadra(devuelta)

    # Y al borrar el último, la liquidación vuelve a 'aprobada' sin deber nada pago
    primero_id = devuelta["pagos"][0]["id"]
    limpia = client.delete(f"{API}/{liq['id']}/pagos/{primero_id}", headers=h).json()
    print(f"  se borra el de $200.000 → pagado {limpia['pagado']} · saldo {limpia['saldo']} · "
          f"estado {limpia['estado']}")
    assert limpia["estado"] == "aprobada"
    assert D(limpia["pagado"]) == D(0)
    assert D(limpia["saldo"]) == D(450000), "el saldo no volvió a donde estaba"
    assert limpia["pagos"] == []
    assert _cuadra(limpia)

    # Un pago que no existe no puede devolver 200 con la liquidación tocada
    fantasma = client.delete(
        f"{API}/{liq['id']}/pagos/00000000-0000-0000-0000-000000000000", headers=h
    )
    print(f"  borrar un pago inexistente: {fantasma.status_code}")
    assert fantasma.status_code == 404


# ---------------------------------------------------------------------------
# (e) Borrar un pago exige 'eliminar', no 'crear'
# ---------------------------------------------------------------------------
def test_borrar_un_pago_exige_el_permiso_eliminar(client, base_datos, db_session):
    """En reventa esto estaba con 'crear' y dejaba borrar abonos a quien solo
    podía anotarlos. Borrar un pago le devuelve la deuda al sistema: es la puerta
    para tapar una entrega de plata, así que va con 'eliminar'.

    El rol Compras sirve de conejillo: tiene 'liquidaciones:crear' y 'editar',
    pero ni 'administrar' ni 'eliminar'.
    """
    from app.core.security import hash_password
    from app.modules.usuarios.models import Rol, Usuario
    from tests.conftest import PASSWORD

    h_admin = auth_headers(client, "admin.a")
    empresa_a = base_datos["empresa_a"]
    rol_compras = db_session.query(Rol).filter(Rol.nombre == "Compras").first()
    comprador = Usuario(
        nombre="Compras", apellido="Prueba", correo="compras@test.local",
        username="compras.a", hashed_password=hash_password(PASSWORD),
        empresa_id=empresa_a.id,
    )
    comprador.roles = [rol_compras]
    db_session.add(comprador)
    db_session.commit()
    h_compras = auth_headers(client, "compras.a")

    _, liq = _montar(client, h_admin, [("2026-06-01", "100")])
    _aprobar(client, h_admin, liq)
    pago_id = _pagar(client, h_admin, liq["id"], "2026-06-16", "50000").json()["pagos"][0]["id"]

    print("\n===== (e) BORRAR UN PAGO PIDE 'eliminar' =====")
    # Con 'crear' no basta ni para anotar el pago (entregar plata es 'administrar')
    anotar = _pagar(client, h_compras, liq["id"], "2026-06-17", "10000")
    print(f"  Compras (crear/editar) registrando un pago: {anotar.status_code}")
    assert anotar.status_code == 403

    borrar = client.delete(f"{API}/{liq['id']}/pagos/{pago_id}", headers=h_compras)
    print(f"  Compras borrando el pago:                   {borrar.status_code}")
    assert borrar.status_code == 403, "con 'crear' no se puede borrar un pago"

    intacta = client.get(f"{API}/{liq['id']}", headers=h_admin).json()
    print(f"  el pago sigue ahí · pagado {intacta['pagado']} · "
          f"{len(intacta['pagos'])} pagos · estado {intacta['estado']}")
    assert D(intacta["pagado"]) == D(50000)
    assert len(intacta["pagos"]) == 1

    # El administrador de empresa, que sí tiene 'eliminar', sí puede
    ok = client.delete(f"{API}/{liq['id']}/pagos/{pago_id}", headers=h_admin)
    print(f"  el administrador borrándolo:                {ok.status_code}")
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# (f) Con un pago registrado, la recepción de esa liquidación se traba
# ---------------------------------------------------------------------------
def test_con_un_pago_registrado_el_dia_ya_no_se_puede_editar(client, base_datos):
    """El candado de Recepción diaria se corre: antes solo trababa las 'pagada' y
    ahora traba en cuanto hay CUALQUIER pago. Si ya salió plata de la caja contra
    esas cifras, cambiar los litros deja el abono descuadrado —el proveedor
    tendría un recibo por una cantidad y el sistema diría otra—.

    Aprobada sin pagos sigue editándose: eso no cambió.
    """
    h = auth_headers(client, "admin.a")
    recepciones, liq = _montar(client, h, [("2026-06-01", "100"), ("2026-06-02", "150")])
    _aprobar(client, h, liq)
    recepcion_id = recepciones["2026-06-01"]["id"]

    print("\n===== (f) UN ABONO TRABA EL DÍA =====")
    # Aprobada y SIN pagos: el día todavía se corrige (la liquidación se recuadra)
    antes = client.put(
        f"/api/v1/recepciones/{recepcion_id}", json={"cantidad_litros": "110"}, headers=h
    )
    print(f"  aprobada sin pagos, editar el día: {antes.status_code} "
          f"(la liquidación vuelve a {antes.json().get('liquidacion_estado')})")
    assert antes.status_code == 200, antes.text

    # Se vuelve a aprobar y se le abona apenas una parte
    _aprobar(client, h, liq)
    parcial = _pagar(client, h, liq["id"], "2026-06-16", "100000").json()
    print(f"  se le abona $100.000 de {parcial['neto_a_pagar']} → estado {parcial['estado']} · "
          f"saldo {parcial['saldo']}")
    assert parcial["estado"] == "parcial"

    editar = client.put(
        f"/api/v1/recepciones/{recepcion_id}", json={"cantidad_litros": "999"}, headers=h
    )
    print(f"  editar el día: {editar.status_code} · "
          f"{editar.json().get('error', {}).get('detail', '')}")
    assert editar.status_code == 422
    assert "pago" in editar.json()["error"]["detail"]

    borrar = client.delete(f"/api/v1/recepciones/{recepcion_id}", headers=h)
    print(f"  borrar el día: {borrar.status_code} · "
          f"{borrar.json().get('error', {}).get('detail', '')}")
    assert borrar.status_code == 422

    # Nada quedó a medias
    recepcion = client.get(f"/api/v1/recepciones/{recepcion_id}", headers=h).json()
    intacta = client.get(f"{API}/{liq['id']}", headers=h).json()
    print(f"  el día sigue en {recepcion['cantidad_litros']} L · la liquidación en "
          f"{intacta['estado']} con saldo {intacta['saldo']}")
    assert D(recepcion["cantidad_litros"]) == D(110)
    assert intacta["estado"] == "parcial"
    assert _cuadra(intacta)

    # Y la grilla le pone el candado, igual que a una pagada del todo
    grilla = client.get(
        "/api/v1/recepciones/grilla/quincena?desde=2026-06-01&hasta=2026-06-15", headers=h
    ).json()
    celda = next(
        c for fila in grilla["filas"] for c in fila["celdas"].values()
        if c["recepcion_id"] == recepcion_id
    )
    print(f"  la grilla · pagada={celda['pagada']} · estado={celda['liquidacion_estado']}")
    assert celda["pagada"] is True, "un día con abono tiene que salir con candado"
    assert celda["liquidacion_estado"] == "parcial"

    # Al borrar el pago, el día vuelve a ser corregible: el candado no es una
    # trampa sin salida, es un aviso de que primero hay que deshacer el pago.
    pago_id = intacta["pagos"][0]["id"]
    client.delete(f"{API}/{liq['id']}/pagos/{pago_id}", headers=h)
    otra_vez = client.put(
        f"/api/v1/recepciones/{recepcion_id}", json={"cantidad_litros": "120"}, headers=h
    )
    print(f"  se borra el pago y el día vuelve a editarse: {otra_vez.status_code}")
    assert otra_vez.status_code == 200, otra_vez.text


# ---------------------------------------------------------------------------
# (g) Multiempresa
# ---------------------------------------------------------------------------
def test_los_pagos_no_cruzan_empresas(client, base_datos):
    """Multiempresa por fila. El admin de la Quesera B no le abona ni le borra
    pagos a una liquidación de la Quesera A ni teniendo el id a la mano: es plata
    de un competidor.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    _, liq_a = _montar(client, h_a, [("2026-06-01", "100")], nombre="DeLaA")
    _aprobar(client, h_a, liq_a)
    pago_id = _pagar(client, h_a, liq_a["id"], "2026-06-16", "50000").json()["pagos"][0]["id"]

    print("\n===== (g) OTRA EMPRESA =====")
    abonar = _pagar(client, h_b, liq_a["id"], "2026-06-17", "10000")
    borrar = client.delete(f"{API}/{liq_a['id']}/pagos/{pago_id}", headers=h_b)
    ver = client.get(f"{API}/{liq_a['id']}", headers=h_b)
    print(f"  admin.b abonando a la liquidación de A: {abonar.status_code}")
    print(f"  admin.b borrando el pago de A:          {borrar.status_code}")
    print(f"  admin.b mirando la liquidación de A:    {ver.status_code}")
    assert abonar.status_code == 404
    assert borrar.status_code == 404
    assert ver.status_code == 404

    intacta = client.get(f"{API}/{liq_a['id']}", headers=h_a).json()
    print(f"  la de A sigue · pagado {intacta['pagado']} · saldo {intacta['saldo']} · "
          f"estado {intacta['estado']} · {len(intacta['pagos'])} pagos")
    assert D(intacta["pagado"]) == D(50000)
    assert len(intacta["pagos"]) == 1
    assert intacta["estado"] == "parcial"
    assert _cuadra(intacta)


# ---------------------------------------------------------------------------
# El botón "Pagar" de siempre sigue funcionando, y ahora deja constancia
# ---------------------------------------------------------------------------
def test_el_boton_pagar_de_siempre_salda_la_liquidacion_y_queda_en_el_historial(
    client, base_datos
):
    """"Pagar" hacía una sola cosa: dejar la liquidación en 'pagada' (no movía
    caja ni bancos, y eso se conserva). Sigue haciendo exactamente eso, pero
    ahora registra el pago por todo el saldo, así que una pagada de un solo golpe
    y una pagada en tres abonos se cuentan igual y las dos tienen historial.

    Y funciona también DESPUÉS de un abono: salda lo que falte, no el total.
    """
    h = auth_headers(client, "admin.a")
    _, liq = _montar(client, h, [("2026-06-01", "100")])  # 180.000
    _aprobar(client, h, liq)

    print("\n===== EL BOTÓN 'PAGAR' DE SIEMPRE =====")
    _pagar(client, h, liq["id"], "2026-06-16", "80000")
    r = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert r.status_code == 200, r.text
    pagada = r.json()
    print(f"  abono de $80.000 y luego 'Pagar' → pagado {pagada['pagado']} · "
          f"saldo {pagada['saldo']} · estado {pagada['estado']} · "
          f"{len(pagada['pagos'])} pagos")
    assert pagada["estado"] == "pagada"
    assert D(pagada["pagado"]) == D(180000)
    assert D(pagada["saldo"]) == D(0)
    assert len(pagada["pagos"]) == 2, "el pago del botón también va al historial"
    assert D(pagada["pagos"][1]["valor"]) == D(100000), "'Pagar' salda lo que falta, no el total"
    assert _cuadra(pagada)

    # Y una pagada no se anula: primero hay que borrarle los pagos
    anular = client.post(f"{API}/{liq['id']}/anular", headers=h)
    print(f"  anular una pagada: {anular.status_code} · "
          f"{anular.json().get('error', {}).get('detail', '')}")
    assert anular.status_code == 422


def test_una_parcial_no_se_anula_mientras_tenga_pagos(client, base_datos):
    """Anular suelta las recepciones y los anticipos para volver a liquidar el
    período. Con un abono hecho eso dejaría un pago colgando de un documento que
    ya no representa nada, y la plata entregada se perdería de vista.
    """
    h = auth_headers(client, "admin.a")
    _, liq = _montar(client, h, [("2026-06-01", "100")])
    _aprobar(client, h, liq)
    parcial = _pagar(client, h, liq["id"], "2026-06-16", "50000").json()

    print("\n===== ANULAR UNA PARCIAL =====")
    r = client.post(f"{API}/{liq['id']}/anular", headers=h)
    print(f"  con un abono de $50.000 encima: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "pagos" in r.json()["error"]["detail"]

    # Se borra el pago y ahí sí se puede anular
    client.delete(f"{API}/{liq['id']}/pagos/{parcial['pagos'][0]['id']}", headers=h)
    ok = client.post(f"{API}/{liq['id']}/anular", headers=h)
    print(f"  se borra el pago y se anula: {ok.status_code} · estado {ok.json()['estado']}")
    assert ok.status_code == 200
    assert ok.json()["estado"] == "anulada"
