"""El candado de Recepción diaria ahora se traba solo cuando ya se PAGÓ.

Lo pidió el dueño con estas palabras: "necesito que solo el candado esté cuando
esté pagado". Antes, un día se bloqueaba apenas su recepción tenía liquidación,
sin mirar el estado: desde que generaba la quincena se quedaba sin poder
corregir un día, aunque todavía no le hubiera pagado a nadie.

Lo que se fija aquí es la regla nueva y —sobre todo— que aflojar el candado no
abra un descuadre silencioso, que es lo que el dueño detectaría cuadrando a mano
contra el cuaderno:

  (a) BORRADOR  → se edita y la liquidación se RECALCULA sola; las partes
                  siguen sumando exacto la cifra grande.
  (b) APROBADA  → se edita, pero la liquidación VUELVE A BORRADOR y se
                  recalcula, y el cambio de estado queda en la auditoría.
                  Aprobar es un visto bueno sobre unas cifras: si las cifras
                  cambian, el visto bueno ya no vale.
  (c) PAGADA    → rebota. Eso ya se le pagó a alguien.
  (d) Multiempresa: la Quesera B no toca un día de la Quesera A ni con el id
                  en la mano.

Y además: borrar un día también recuadra la liquidación, y la del FLETE (que es
otra liquidación, de otra persona) se recuadra igual que la de la leche.
"""
import uuid
from decimal import Decimal

from sqlalchemy import select

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"


def D(v):
    return Decimal(str(v))


def _montar(client, headers, dias, precio="1800", nombre="Libardo", con_flete=False):
    """Un proveedor con sus días anotados y la liquidación de la quincena generada.

    Devuelve (proveedor, recepciones_por_fecha, liquidaciones_por_tipo).
    """
    transportador = None
    if con_flete:
        ruta = client.post(
            "/api/v1/rutas", json={"nombre": f"Ruta {nombre}", "municipio": "Granada"},
            headers=headers,
        ).json()
        transportador = client.post(
            "/api/v1/transportadores",
            json={"nombre": f"Stella {nombre}", "ruta_id": ruta["id"], "valor_transporte": "100"},
            headers=headers,
        ).json()
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=headers,
    ).json()

    recepciones = {}
    for fecha, litros in dias:
        cuerpo = {"fecha": fecha, "proveedor_id": proveedor["id"], "cantidad_litros": str(litros)}
        if transportador:
            cuerpo["transportador_id"] = transportador["id"]
        r = client.post("/api/v1/recepciones", json=cuerpo, headers=headers)
        assert r.status_code == 201, r.text
        recepciones[fecha] = r.json()

    liquidaciones = client.post(
        f"{API}/generar",
        json={
            "periodo_inicio": "2026-06-01",
            "periodo_fin": "2026-06-15",
            "tipo": "ambos" if con_flete else "proveedor",
        },
        headers=headers,
    ).json()
    por_tipo = {
        liq["tipo"]: liq
        for liq in liquidaciones
        if liq["tipo"] != "proveedor" or liq["proveedor_id"] == proveedor["id"]
    }
    return proveedor, recepciones, por_tipo


def _cuadra(liq) -> bool:
    """Los dos cuadres que el dueño verifica a mano en el comprobante."""
    suma_dias = sum((D(d["valor"]) for d in liq["detalles"]), D(0))
    desglose = D(liq["valor_bruto"]) + D(liq["bonificaciones"]) - D(liq["descuentos"])
    total = D(liq["valor_total"])
    return suma_dias == total and desglose == total


# ---------------------------------------------------------------------------
# (a) BORRADOR: se edita y la liquidación se recalcula sola
# ---------------------------------------------------------------------------
def test_editar_un_dia_en_borrador_recalcula_la_liquidacion(client, base_datos):
    """El caso de todos los días: se generó la quincena y después el dueño ve que
    a un día le quedaron mal los litros. Un borrador todavía no es plata
    entregada, así que se corrige y la liquidación se vuelve a cuadrar sola.

    Lo que NO puede pasar es que la liquidación quede diciendo la cifra vieja:
    ahí quedaría un descuadre que nadie ve hasta que se paga de más.
    """
    h = auth_headers(client, "admin.a")
    _, recepciones, liqs = _montar(client, h, [("2026-06-01", "100"), ("2026-06-02", "150")])
    liq = liqs["proveedor"]

    print("\n===== (a) EDITAR UN DÍA EN BORRADOR =====")
    print(f"  antes  · estado {liq['estado']} · 250 L · total {liq['valor_total']}")
    assert liq["estado"] == "borrador"
    assert D(liq["valor_total"]) == D(250 * 1800)

    # El día 01 no eran 100 L sino 120
    r = client.put(
        f"/api/v1/recepciones/{recepciones['2026-06-01']['id']}",
        json={"cantidad_litros": "120"},
        headers=h,
    )
    print(f"  editar el día 01 de 100 a 120 L: {r.status_code}")
    assert r.status_code == 200, r.text

    actualizada = client.get(f"{API}/{liq['id']}", headers=h).json()
    esperado = D((120 + 150) * 1800)
    dia_01 = next(d for d in actualizada["detalles"] if d["fecha"] == "2026-06-01")
    dia_02 = next(d for d in actualizada["detalles"] if d["fecha"] == "2026-06-02")
    print(f"  día 01 · {dia_01['litros']} L × ${dia_01['precio_litro']} = {dia_01['valor']}")
    print(f"  día 02 · {dia_02['litros']} L × ${dia_02['precio_litro']} = {dia_02['valor']} (sin tocar)")
    print(f"  después · estado {actualizada['estado']} · {actualizada['total_litros']} L · "
          f"total {actualizada['valor_total']} · saldo {actualizada['saldo']}")

    assert actualizada["estado"] == "borrador", "un borrador editado sigue siendo borrador"
    assert D(actualizada["total_litros"]) == D(270)
    assert D(dia_01["litros"]) == D(120)
    assert D(dia_01["valor"]) == D(120 * 1800)
    assert D(dia_02["valor"]) == D(150 * 1800), "el otro día no se tenía que mover"
    assert D(actualizada["valor_total"]) == esperado
    assert _cuadra(actualizada), "las partes dejaron de sumar la cifra grande"


def test_borrar_un_dia_en_borrador_le_quita_el_renglon_a_la_liquidacion(client, base_datos):
    """Borrar un día que ya está en una liquidación tiene el mismo problema que
    editarlo: si el renglón se quedara, la columna Valor sumaría más que el
    VALOR TOTAL y el comprobante mentiría por el valor de un día entero.
    """
    h = auth_headers(client, "admin.a")
    _, recepciones, liqs = _montar(
        client, h, [("2026-06-01", "100"), ("2026-06-02", "150"), ("2026-06-03", "80")]
    )
    liq = liqs["proveedor"]

    print("\n===== (a2) BORRAR UN DÍA EN BORRADOR =====")
    print(f"  antes · {len(liq['detalles'])} renglones · total {liq['valor_total']}")

    r = client.delete(f"/api/v1/recepciones/{recepciones['2026-06-02']['id']}", headers=h)
    print(f"  borrar el día 02 (150 L): {r.status_code}")
    assert r.status_code == 204, r.text

    actualizada = client.get(f"{API}/{liq['id']}", headers=h).json()
    fechas = sorted(d["fecha"] for d in actualizada["detalles"])
    print(f"  después · renglones {fechas} · {actualizada['total_litros']} L · "
          f"total {actualizada['valor_total']}")
    assert fechas == ["2026-06-01", "2026-06-03"], "el renglón del día borrado se quedó"
    assert D(actualizada["total_litros"]) == D(180)
    assert D(actualizada["valor_total"]) == D(180 * 1800)
    assert _cuadra(actualizada)


# ---------------------------------------------------------------------------
# (b) APROBADA: se edita, pero vuelve a borrador y queda auditado
# ---------------------------------------------------------------------------
def test_editar_un_dia_aprobado_devuelve_la_liquidacion_a_borrador(client, db_session, base_datos):
    """Aprobar es dar el visto bueno sobre unas cifras. Si las cifras cambian, el
    visto bueno ya no vale: la liquidación tiene que volver a borrador para que
    el dueño la revise y la apruebe otra vez, y ese retroceso tiene que quedar en
    la bitácora (si no, mañana nadie sabría por qué una aprobada amaneció en
    borrador).
    """
    from app.modules.auditoria.models import Auditoria

    h = auth_headers(client, "admin.a")
    _, recepciones, liqs = _montar(client, h, [("2026-06-01", "100"), ("2026-06-02", "150")])
    liq = liqs["proveedor"]
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200

    print("\n===== (b) EDITAR UN DÍA DE UNA LIQUIDACIÓN APROBADA =====")
    aprobada = client.get(f"{API}/{liq['id']}", headers=h).json()
    print(f"  antes · estado {aprobada['estado']} · total {aprobada['valor_total']}")
    assert aprobada["estado"] == "aprobada"

    r = client.put(
        f"/api/v1/recepciones/{recepciones['2026-06-02']['id']}",
        json={"cantidad_litros": "200"},
        headers=h,
    )
    print(f"  editar el día 02 de 150 a 200 L: {r.status_code}")
    assert r.status_code == 200, r.text
    # La recepción devuelve el estado NUEVO de su liquidación: la pantalla lo usa
    # para avisar que hay que volver a aprobarla.
    print(f"  la recepción responde liquidacion_estado = {r.json()['liquidacion_estado']}")
    assert r.json()["liquidacion_estado"] == "borrador"

    actualizada = client.get(f"{API}/{liq['id']}", headers=h).json()
    esperado = D((100 + 200) * 1800)
    print(f"  después · estado {actualizada['estado']} · {actualizada['total_litros']} L · "
          f"total {actualizada['valor_total']}")
    assert actualizada["estado"] == "borrador", "una aprobada con cifras nuevas no puede seguir aprobada"
    assert D(actualizada["total_litros"]) == D(300)
    assert D(actualizada["valor_total"]) == esperado
    assert _cuadra(actualizada)

    # Y el retroceso quedó escrito, con el porqué
    registros = db_session.scalars(
        select(Auditoria).where(
            Auditoria.entidad == "Liquidacion",
            Auditoria.entidad_id == uuid.UUID(liq["id"]),
        )
    ).all()
    retrocesos = [
        a for a in registros
        if (a.antes or {}).get("estado") == "aprobada"
        and (a.despues or {}).get("estado") == "borrador"
    ]
    for a in retrocesos:
        print(f"  auditoría · {a.modulo}/{a.accion} · {a.antes} -> {a.despues}")
    assert len(retrocesos) == 1, "el paso de aprobada a borrador no quedó en la bitácora"
    assert "cambiaron las recepciones" in retrocesos[0].despues["motivo"]

    # Se puede volver a aprobar: el arreglo no puede volverse un estorbo
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200


# ---------------------------------------------------------------------------
# (c) PAGADA: rebota, y el guardia vive en el backend
# ---------------------------------------------------------------------------
def test_un_dia_de_una_liquidacion_pagada_no_se_toca(client, base_datos):
    """El único candado que queda. Esa plata ya salió: cambiar los litros ahora
    dejaría el comprobante que se le entregó al productor diciendo una cosa y el
    sistema otra.

    El guardia está en el BACKEND, no en la pantalla: quien conozca la dirección
    del endpoint entra igual.
    """
    h = auth_headers(client, "admin.a")
    _, recepciones, liqs = _montar(client, h, [("2026-06-01", "100")])
    liq = liqs["proveedor"]
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    client.post(f"{API}/{liq['id']}/pagar", headers=h)

    print("\n===== (c) LIQUIDACIÓN PAGADA =====")
    recepcion_id = recepciones["2026-06-01"]["id"]
    editar = client.put(
        f"/api/v1/recepciones/{recepcion_id}", json={"cantidad_litros": "999"}, headers=h
    )
    print(f"  editar: {editar.status_code} · {editar.json().get('error', {}).get('detail', '')}")
    assert editar.status_code == 422
    assert "ya se pagó" in editar.json()["error"]["detail"]

    borrar = client.delete(f"/api/v1/recepciones/{recepcion_id}", headers=h)
    print(f"  borrar: {borrar.status_code} · {borrar.json().get('error', {}).get('detail', '')}")
    assert borrar.status_code == 422
    assert "ya se pagó" in borrar.json()["error"]["detail"]

    # Y nada quedó a medias
    intacta = client.get(f"{API}/{liq['id']}", headers=h).json()
    recepcion = client.get(f"/api/v1/recepciones/{recepcion_id}", headers=h).json()
    print(f"  la liquidación sigue en {intacta['valor_total']} ({intacta['estado']}) · "
          f"la recepción sigue en {recepcion['cantidad_litros']} L")
    assert intacta["estado"] == "pagada"
    assert D(intacta["valor_total"]) == D(100 * 1800)
    assert D(recepcion["cantidad_litros"]) == D(100)


def test_la_grilla_pone_el_candado_solo_en_las_pagadas(client, base_datos):
    """Lo que ve el dueño: el candado solo en el día pagado. El día que está en
    una liquidación sin pagar queda editable, pero marcado —si no llevara ninguna
    seña, el usuario no sabría que al tocarlo va a mover una liquidación ya
    generada—.
    """
    h = auth_headers(client, "admin.a")
    _, _, liqs_pagada = _montar(client, h, [("2026-06-01", "100")], nombre="Pagado")
    _, _, liqs_borrador = _montar(client, h, [("2026-06-02", "90")], nombre="Enborrador")
    _, _, _ = _montar(client, h, [("2026-06-03", "70")], nombre="Sinliquidar")
    # El tercero se anota DESPUÉS de generar, para que quede sin liquidación
    sin_liquidar = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Suelto", "vereda": "El Roble", "precio_litro": "1800"},
        headers=h,
    ).json()
    client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-06-04", "proveedor_id": sin_liquidar["id"], "cantidad_litros": "60"},
        headers=h,
    )

    client.post(f"{API}/{liqs_pagada['proveedor']['id']}/aprobar", headers=h)
    client.post(f"{API}/{liqs_pagada['proveedor']['id']}/pagar", headers=h)

    grilla = client.get(
        "/api/v1/recepciones/grilla/quincena?desde=2026-06-01&hasta=2026-06-15", headers=h
    ).json()
    celdas = {
        fila["proveedor_nombre"]: celda
        for fila in grilla["filas"]
        for celda in fila["celdas"].values()
    }

    print("\n===== (c2) LA GRILLA =====")
    for nombre in ("Pagado", "Enborrador", "Suelto"):
        c = celdas[nombre]
        print(f"  {nombre:<12} · liquidada={c['liquidada']} · pagada={c['pagada']} · "
              f"estado={c['liquidacion_estado']}")

    assert celdas["Pagado"]["pagada"] is True, "el día pagado tiene que llevar candado"
    assert celdas["Pagado"]["liquidacion_estado"] == "pagada"

    assert celdas["Enborrador"]["pagada"] is False, "un borrador no lleva candado"
    assert celdas["Enborrador"]["liquidada"] is True, "pero sí lleva la seña de 'ya está en una liquidación'"
    assert celdas["Enborrador"]["liquidacion_estado"] == "borrador"

    assert celdas["Suelto"]["liquidada"] is False
    assert celdas["Suelto"]["pagada"] is False
    assert celdas["Suelto"]["liquidacion_estado"] is None


# ---------------------------------------------------------------------------
# (d) Multiempresa: la quincena de la Quesera A no se toca desde la B
# ---------------------------------------------------------------------------
def test_no_se_cruzan_las_empresas(client, base_datos):
    """Multiempresa por fila. El admin de la Quesera B no puede corregir un día de
    la Quesera A ni con el id en la mano, y —lo que abre este cambio— tampoco
    puede hacerle retroceder una liquidación aprobada. Es plata de un competidor.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    _, recepciones_a, liqs_a = _montar(client, h_a, [("2026-06-01", "100")], nombre="DeLaA")
    liq_a = liqs_a["proveedor"]
    client.post(f"{API}/{liq_a['id']}/aprobar", headers=h_a)

    print("\n===== (d) OTRA EMPRESA =====")
    recepcion_id = recepciones_a["2026-06-01"]["id"]
    editar = client.put(
        f"/api/v1/recepciones/{recepcion_id}", json={"cantidad_litros": "500"}, headers=h_b
    )
    borrar = client.delete(f"/api/v1/recepciones/{recepcion_id}", headers=h_b)
    ver = client.get(f"/api/v1/recepciones/{recepcion_id}", headers=h_b)
    print(f"  admin.b editando el día de A:  {editar.status_code}")
    print(f"  admin.b borrando el día de A:  {borrar.status_code}")
    print(f"  admin.b mirando el día de A:   {ver.status_code}")
    assert editar.status_code == 404
    assert borrar.status_code == 404
    assert ver.status_code == 404

    intacta = client.get(f"{API}/{liq_a['id']}", headers=h_a).json()
    recepcion = client.get(f"/api/v1/recepciones/{recepcion_id}", headers=h_a).json()
    print(f"  la liquidación de A sigue {intacta['estado']} en {intacta['valor_total']} · "
          f"la recepción sigue en {recepcion['cantidad_litros']} L")
    assert intacta["estado"] == "aprobada", "la aprobada de A no podía retroceder por culpa de B"
    assert D(intacta["valor_total"]) == D(100 * 1800)
    assert D(recepcion["cantidad_litros"]) == D(100)


# ---------------------------------------------------------------------------
# El flete es OTRA liquidación, de otra persona: también hay que recuadrarla
# ---------------------------------------------------------------------------
def test_editar_los_litros_recuadra_tambien_la_liquidacion_del_flete(client, base_datos):
    """Un mismo día está en DOS liquidaciones: la leche al proveedor y el flete al
    transportador, que cobra por litro recogido. Si al corregir los litros solo se
    recuadrara la de la leche, el transportador quedaría con un comprobante que ya
    no corresponde a lo que recogió.
    """
    h = auth_headers(client, "admin.a")
    _, recepciones, liqs = _montar(
        client, h, [("2026-06-01", "100"), ("2026-06-02", "150")], con_flete=True
    )
    liq_leche = liqs["proveedor"]
    liq_flete = liqs["transportador"]

    print("\n===== (e) LA LIQUIDACIÓN DEL FLETE =====")
    print(f"  antes · leche {liq_leche['valor_total']} · flete {liq_flete['valor_total']} "
          f"(250 L × $100)")
    assert D(liq_flete["valor_total"]) == D(250 * 100)

    r = client.put(
        f"/api/v1/recepciones/{recepciones['2026-06-01']['id']}",
        json={"cantidad_litros": "120"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    flete = client.get(f"{API}/{liq_flete['id']}", headers=h).json()
    suma_dias = sum((D(d["valor"]) for d in flete["detalles"]), D(0))
    print(f"  después · {flete['total_litros']} L · flete {flete['valor_total']} · "
          f"suma de los días {suma_dias} · saldo {flete['saldo']}")
    assert D(flete["total_litros"]) == D(270)
    assert D(flete["valor_total"]) == D(270 * 100)
    assert suma_dias == D(flete["valor_total"]), "la columna Valor del flete no suma el total"
    assert D(flete["saldo"]) == D(flete["valor_total"]) - D(flete["anticipos"])


def test_cambiar_el_transportador_suelta_el_dia_de_la_liquidacion_del_flete(client, base_datos):
    """Poder editar un día abre una puerta que antes estaba cerrada: cambiarle el
    transportador. El flete de ese día pasa a ser de OTRA persona, así que no
    puede quedarse en el comprobante del que lo tenía apartado —le pagaríamos a
    quien no recogió—. Se suelta, esa liquidación se recuadra sin el día, y el
    flete queda libre para liquidárselo al que sí lo recogió.
    """
    h = auth_headers(client, "admin.a")
    _, recepciones, liqs = _montar(
        client, h, [("2026-06-01", "100"), ("2026-06-02", "150")], con_flete=True
    )
    liq_flete = liqs["transportador"]
    otro = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Efraín", "valor_transporte": "120"},
        headers=h,
    ).json()

    print("\n===== (g) LE CAMBIAN EL TRANSPORTADOR A UN DÍA =====")
    print(f"  antes · flete de Stella {liq_flete['valor_total']} (250 L × $100)")

    r = client.put(
        f"/api/v1/recepciones/{recepciones['2026-06-01']['id']}",
        json={"transportador_id": otro["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    print(f"  el día 01 pasa a Efraín · liquidacion_estado = {r.json()['liquidacion_estado']}")

    flete = client.get(f"{API}/{liq_flete['id']}", headers=h).json()
    fechas = sorted(d["fecha"] for d in flete["detalles"])
    print(f"  la liquidación de Stella queda con {fechas} · {flete['total_litros']} L · "
          f"{flete['valor_total']}")
    assert fechas == ["2026-06-02"], "el día que ya no recogió Stella se quedó en su comprobante"
    assert D(flete["valor_total"]) == D(150 * 100)
    assert sum((D(d["valor"]) for d in flete["detalles"]), D(0)) == D(flete["valor_total"])

    # Y el flete del día 01 vuelve a quedar disponible: se le liquida a Efraín
    nuevas = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "transportador"},
        headers=h,
    ).json()
    de_efrain = next(x for x in nuevas if x["transportador_id"] == otro["id"])
    print(f"  se le genera a Efraín: {de_efrain['total_litros']} L × $120 = "
          f"{de_efrain['valor_total']}")
    assert D(de_efrain["valor_total"]) == D(100 * 120)


def test_mover_un_dia_a_otra_quincena_lo_suelta_de_la_liquidacion(client, base_datos):
    """Si a un día liquidado le corrigen la fecha y se sale del período, esa leche
    pertenece a otra quincena: se suelta de la liquidación vieja (que se recuadra
    sin él) y queda disponible para la quincena que le toca. Si se quedara, el
    comprobante de junio traería un renglón de julio.
    """
    h = auth_headers(client, "admin.a")
    _, recepciones, liqs = _montar(client, h, [("2026-06-01", "100"), ("2026-06-02", "150")])
    liq = liqs["proveedor"]

    print("\n===== (h) MOVER UN DÍA A OTRA QUINCENA =====")
    r = client.put(
        f"/api/v1/recepciones/{recepciones['2026-06-01']['id']}",
        json={"fecha": "2026-07-03"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    print(f"  el día 01/06 pasa al 03/07 · liquidacion_estado = {r.json()['liquidacion_estado']}")
    assert r.json()["liquidacion_estado"] is None, "el día quedó suelto, sin liquidación"

    junio = client.get(f"{API}/{liq['id']}", headers=h).json()
    fechas = sorted(d["fecha"] for d in junio["detalles"])
    print(f"  la liquidación 01–15/06 queda con {fechas} · {junio['total_litros']} L · "
          f"{junio['valor_total']}")
    assert fechas == ["2026-06-02"]
    assert D(junio["valor_total"]) == D(150 * 1800)
    assert _cuadra(junio)

    # Y en julio sí entra
    julio = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "proveedor"},
        headers=h,
    ).json()[0]
    print(f"  la liquidación de julio: {julio['total_litros']} L · {julio['valor_total']}")
    assert D(julio["valor_total"]) == D(100 * 1800)

    # Un cambio de fecha DENTRO del mismo período no suelta nada: solo recuadra
    otra = client.put(
        f"/api/v1/recepciones/{recepciones['2026-06-02']['id']}",
        json={"fecha": "2026-06-05"},
        headers=h,
    )
    assert otra.status_code == 200, otra.text
    junio = client.get(f"{API}/{liq['id']}", headers=h).json()
    print(f"  mover el 02/06 al 05/06 (misma quincena) · renglones "
          f"{[d['fecha'] for d in junio['detalles']]} · total {junio['valor_total']}")
    assert [d["fecha"] for d in junio["detalles"]] == ["2026-06-05"]
    assert D(junio["valor_total"]) == D(150 * 1800)
    assert _cuadra(junio)


def test_con_el_flete_pagado_el_dia_queda_trabado_aunque_la_leche_no(client, base_datos):
    """El candado lo pone CUALQUIERA de las dos liquidaciones. Si al transportador
    ya se le pagó el flete de esa quincena, cambiar los litros de un día le
    cambiaría lo que ya cobró, aunque al proveedor todavía no se le haya pagado
    la leche.
    """
    h = auth_headers(client, "admin.a")
    _, recepciones, liqs = _montar(client, h, [("2026-06-01", "100")], con_flete=True)
    liq_flete = liqs["transportador"]
    client.post(f"{API}/{liq_flete['id']}/aprobar", headers=h)
    client.post(f"{API}/{liq_flete['id']}/pagar", headers=h)

    print("\n===== (f) FLETE PAGADO, LECHE EN BORRADOR =====")
    print(f"  leche: {liqs['proveedor']['estado']} · flete: pagada")
    r = client.put(
        f"/api/v1/recepciones/{recepciones['2026-06-01']['id']}",
        json={"cantidad_litros": "120"},
        headers=h,
    )
    print(f"  editar el día: {r.status_code} · {r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "el flete ya se pagó" in r.json()["error"]["detail"]
