"""LA PLATA DE UN COMPROBANTE DE FLETE YA PAGADO NO SE MUEVE. Tres rondas de ataque.

La regla: la CIFRA del flete de una recepción (`valor_transporte`) es derivada —litros
× tarifa— y se vuelve a derivar cada vez que se guarda el día, con UNA sola excepción:
que por ese flete ya haya salido plata (liquidación pagada o con un solo abono). Ahí no
se toca ni por un centavo, porque esa plata salió contra ESA cifra y el papel que el
transportador tiene firmado tiene que seguir siendo la suma de sus recepciones.

Acá se intenta romper esa excepción por todos los lados. Son las tres rondas que se
hicieron por separado, juntas porque atacan lo mismo y compartían la mitad del montaje:

  · RONDA 1 — el PUT de la recepción de frente: los campos sueltos que no deberían
    mover el flete, el formulario completo reenviado, la tarifa subida antes de editar,
    y los campos que TIENEN que rebotar con un 422;
  · RONDA 2 — los caminos de al lado: el centavo que el REPARTO le movió a una foto y
    que un guardado cualquiera podría devolver a su sitio, la sucursal, el
    `valor_transporte` informativo de la liquidación pagada del PROVEEDOR, y borrar el
    pago para mover el día y volver a pagar;
  · RONDA 3 — borrar el catálogo debajo del comprobante: la tarifa de la ruta, la ruta
    del catálogo, y el transportador entero.

EL OTRO LADO —que una tarifa mal tecleada SÍ se pueda corregir mientras no se haya
pagado— está en test_liquidacion_flete_tarifa_corregida.py: son las dos mitades de lo
mismo y separarlas es lo que evita congelar de más.

LAS CIFRAS: Alex cobra $242,76 el litro en Nápoles. 227,55 L = $55.239,98
(55.239,978 redondeado). Y para el reparto, 44,23 L + 82,48 L = 126,71 L × $242,76 =
$30.760,12, cuando las dos fotos redondeadas por separado suman $30.760,11: ese
centavo es el que el reparto tiene que mover y después no volver a tocar.
"""

from decimal import ROUND_HALF_UP, Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def leer(client, headers, rec_id):
    res = client.get(f"{REC}/{rec_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def leer_liq(client, headers, liq_id):
    res = client.get(f"{API}/{liq_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def cuadra(liq, fotos):
    """Las tres cifras que el dueño verifica a mano."""
    suma_renglones = sum((D(d["valor"]) for d in liq["detalles"]), D(0))
    suma_fotos = sum((D(f) for f in fotos), D(0))
    return {
        "total": D(liq["valor_transporte"]),
        "suma_renglones": suma_renglones,
        "suma_fotos": suma_fotos,
        "valor_total": D(liq["valor_total"]),
    }


def generar_flete(client, headers, tipo="transportador", inicio="2026-06-01", fin="2026-06-15"):
    res = client.post(
        f"{API}/generar",
        json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


def generar_y_pagar(client, h):
    """El comprobante del flete, aprobado y pagado del todo."""
    liq = generar_flete(client, h)[0]
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert client.post(f"{API}/{liq['id']}/pagar", headers=h).status_code == 200
    return leer_liq(client, h, liq["id"])

# --------------------------------------------------------------------- montaje
def montar(
    client,
    headers,
    *,
    tarifa_ruta="242.76",
    tarifa_general="100",
    litros="227.55",
    fecha="2026-06-02",
    precio_leche="1800",
    sufijo="",
):
    ruta = client.post(
        "/api/v1/rutas",
        json={"nombre": f"Napoles{sufijo}", "municipio": "Granada"},
        headers=headers,
    ).json()
    cuerpo_t = {"nombre": f"Alex{sufijo}", "valor_transporte": tarifa_general}
    if tarifa_ruta is not None:
        cuerpo_t["rutas"] = [{"ruta_id": ruta["id"], "valor_transporte": tarifa_ruta}]
    t = client.post("/api/v1/transportadores", json=cuerpo_t, headers=headers)
    assert t.status_code == 201, t.text
    transportador = t.json()
    prov = client.post(
        "/api/v1/proveedores",
        json={
            "nombre": f"Patricia{sufijo}",
            "vereda": "El Roble",
            "precio_litro": precio_leche,
            "ruta_id": ruta["id"],
        },
        headers=headers,
    )
    assert prov.status_code == 201, prov.text
    proveedor = prov.json()
    r = client.post(
        REC,
        json={
            "fecha": fecha,
            "proveedor_id": proveedor["id"],
            "transportador_id": transportador["id"],
            "ruta_id": ruta["id"],
            "cantidad_litros": litros,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return ruta, transportador, proveedor, r.json()

def montar_dos_del_mismo_dia(client, h, tarifa="242.76"):
    """Dos proveedores, mismo día, misma ruta, mismo transportador.

    44,23 L + 82,48 L a $242,76 es el caso del comentario del código: la suma de
    las fotos redondeadas ($30.760,11) NO da lo mismo que redondear el total
    ($30.760,12), así que el reparto tiene que moverle un centavo a una foto.
    """
    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "Napoles", "municipio": "Granada"}, headers=h
    ).json()
    t = client.post(
        "/api/v1/transportadores",
        json={
            "nombre": "Alex",
            "valor_transporte": "100",
            "rutas": [{"ruta_id": ruta["id"], "valor_transporte": tarifa}],
        },
        headers=h,
    ).json()
    recs = []
    for nombre, litros in (("Patricia", "44.23"), ("Libardo", "82.48")):
        prov = client.post(
            "/api/v1/proveedores",
            json={"nombre": nombre, "vereda": "El Roble", "precio_litro": "1800",
                  "ruta_id": ruta["id"]},
            headers=h,
        ).json()
        r = client.post(
            REC,
            json={"fecha": "2026-06-02", "proveedor_id": prov["id"],
                  "transportador_id": t["id"], "ruta_id": ruta["id"],
                  "cantidad_litros": litros},
            headers=h,
        )
        assert r.status_code == 201, r.text
        recs.append(r.json())
    return ruta, t, recs


# ===========================================================================
# RONDA 1 · EL PUT DE LA RECEPCIÓN, DE FRENTE
# ===========================================================================


@pytest.fixture()
def pagado(client, base_datos):
    """Un flete ya PAGADO del todo, con su foto y su comprobante."""
    headers = auth_headers(client, "admin.a")
    ruta, transportador, proveedor, rec = montar(client, headers)
    liqs = generar_flete(client, headers)
    assert len(liqs) == 1
    liq = liqs[0]
    client.post(f"{API}/{liq['id']}/aprobar", headers=headers)
    res = client.post(f"{API}/{liq['id']}/pagar", headers=headers)
    assert res.status_code == 200, res.text
    rec = leer(client, headers, rec["id"])
    liq = leer_liq(client, headers, liq["id"])
    assert liq["estado"] == "pagada"
    return {
        "headers": headers,
        "ruta": ruta,
        "transportador": transportador,
        "proveedor": proveedor,
        "rec": rec,
        "liq": liq,
    }


CAMPOS_QUE_NO_DEBERIAN_MOVER_EL_FLETE = [
    ("observaciones", {"observaciones": "llovio y llego tarde"}),
    ("bonificaciones", {"bonificaciones": "5000"}),
    ("descuentos", {"descuentos": "1200"}),
    ("precio_litro", {"precio_litro": "2050"}),
]


@pytest.mark.parametrize("nombre,cambio", CAMPOS_QUE_NO_DEBERIAN_MOVER_EL_FLETE)
def test_editar_campo_libre_no_mueve_el_flete_pagado(client, pagado, nombre, cambio):
    h, rec, liq = pagado["headers"], pagado["rec"], pagado["liq"]
    foto_antes = D(rec["valor_transporte"])
    total_antes = D(liq["valor_transporte"])
    res = client.put(f"{REC}/{rec['id']}", json=cambio, headers=h)
    assert res.status_code == 200, res.text
    despues = leer(client, h, rec["id"])
    liq_despues = leer_liq(client, h, liq["id"])
    c = cuadra(liq_despues, [despues["valor_transporte"]])
    assert D(despues["valor_transporte"]) == foto_antes, (
        f"{nombre}: la foto del flete PAGADO se movio de {foto_antes} a "
        f"{despues['valor_transporte']}"
    )
    assert c["total"] == total_antes
    assert c["suma_renglones"] == c["total"] == c["suma_fotos"]


def test_reenviar_el_formulario_completo_sin_cambiar_nada(client, pagado):
    """El diálogo manda TODO el formulario en cada guardado. Que no mueva nada."""
    h, rec, liq = pagado["headers"], pagado["rec"], pagado["liq"]
    formulario = {
        "fecha": rec["fecha"],
        "transportador_id": rec["transportador_id"],
        "ruta_id": rec["ruta_id"],
        "sucursal_id": rec["sucursal_id"],
        "cantidad_litros": rec["cantidad_litros"],
        "precio_litro": rec["precio_litro"],
        "bonificaciones": rec["bonificaciones"],
        "descuentos": rec["descuentos"],
        "observaciones": rec["observaciones"],
        "estado": rec["estado"],
    }
    res = client.put(f"{REC}/{rec['id']}", json=formulario, headers=h)
    assert res.status_code == 200, res.text
    despues = leer(client, h, rec["id"])
    liq_despues = leer_liq(client, h, liq["id"])
    c = cuadra(liq_despues, [despues["valor_transporte"]])
    assert D(despues["valor_transporte"]) == D(rec["valor_transporte"])
    assert c["suma_renglones"] == c["total"] == c["suma_fotos"]
    assert liq_despues["estado"] == "pagada"


def test_subir_la_tarifa_de_la_ruta_y_despues_tocar_la_recepcion(client, pagado):
    """El caso que más plata mueve: le suben la tarifa y después editan el día."""
    h, rec, liq, ruta, t = (
        pagado["headers"], pagado["rec"], pagado["liq"], pagado["ruta"], pagado["transportador"]
    )
    foto_antes = D(rec["valor_transporte"])
    total_antes = D(liq["valor_transporte"])
    res = client.put(
        f"/api/v1/transportadores/{t['id']}",
        json={"rutas": [{"ruta_id": ruta["id"], "valor_transporte": "500"}]},
        headers=h,
    )
    assert res.status_code == 200, res.text
    for _, cambio in CAMPOS_QUE_NO_DEBERIAN_MOVER_EL_FLETE:
        assert client.put(f"{REC}/{rec['id']}", json=cambio, headers=h).status_code == 200
    despues = leer(client, h, rec["id"])
    liq_despues = leer_liq(client, h, liq["id"])
    c = cuadra(liq_despues, [despues["valor_transporte"]])
    assert D(despues["valor_transporte"]) == foto_antes, (
        f"la foto de un flete PAGADO paso de {foto_antes} a {despues['valor_transporte']} "
        f"al subirle la tarifa a la ruta y tocar la recepcion"
    )
    assert c["total"] == total_antes
    assert c["suma_renglones"] == c["total"] == c["suma_fotos"]


CAMPOS_QUE_DEBEN_REBOTAR = [
    ("litros", {"cantidad_litros": "300"}),
    ("ruta", {"ruta_id": None}),
    ("transportador", {"transportador_id": None}),
    ("fecha_mismo_periodo", {"fecha": "2026-06-05"}),
    ("fecha_otro_periodo", {"fecha": "2026-07-05"}),
    ("estado", {"estado": "inactivo"}),
]


@pytest.mark.parametrize("nombre,cambio", CAMPOS_QUE_DEBEN_REBOTAR)
def test_lo_que_determina_el_flete_pagado_rebota(client, pagado, nombre, cambio):
    h, rec, liq = pagado["headers"], pagado["rec"], pagado["liq"]
    res = client.put(f"{REC}/{rec['id']}", json=cambio, headers=h)
    assert res.status_code == 422, f"{nombre}: paso con {res.status_code}: {res.text}"
    despues = leer(client, h, rec["id"])
    liq_despues = leer_liq(client, h, liq["id"])
    assert D(despues["valor_transporte"]) == D(rec["valor_transporte"])
    c = cuadra(liq_despues, [despues["valor_transporte"]])
    assert c["suma_renglones"] == c["total"] == c["suma_fotos"]


def test_borrar_el_dia_con_flete_pagado_rebota(client, pagado):
    h, rec = pagado["headers"], pagado["rec"]
    res = client.delete(f"{REC}/{rec['id']}", headers=h)
    assert res.status_code == 422, res.text


def test_quitar_y_volver_a_poner_el_transportador(client, pagado):
    h, rec, liq, t = pagado["headers"], pagado["rec"], pagado["liq"], pagado["transportador"]
    quitar = client.put(f"{REC}/{rec['id']}", json={"transportador_id": None}, headers=h)
    assert quitar.status_code == 422, f"se dejo quitar el transportador de un flete pagado: {quitar.text}"
    volver = client.put(
        f"{REC}/{rec['id']}", json={"transportador_id": t["id"]}, headers=h
    )
    assert volver.status_code == 200, volver.text  # mismo valor: no es cambio
    despues = leer(client, h, rec["id"])
    assert D(despues["valor_transporte"]) == D(rec["valor_transporte"])
    assert despues["liquidacion_transporte_id"] == liq["id"]


# =========================================================== ABONO PARCIAL
@pytest.fixture()
def abonado(client, base_datos):
    """Un flete con UN ABONO (estado parcial): la plata ya salió a medias."""
    headers = auth_headers(client, "admin.a")
    ruta, transportador, proveedor, rec = montar(client, headers, sufijo="P")
    liq = generar_flete(client, headers)[0]
    client.post(f"{API}/{liq['id']}/aprobar", headers=headers)
    res = client.post(
        f"{API}/{liq['id']}/pagos",
        json={"fecha": "2026-06-16", "valor": "10000"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["estado"] == "parcial"
    rec = leer(client, headers, rec["id"])
    return {
        "headers": headers, "ruta": ruta, "transportador": transportador,
        "rec": rec, "liq": leer_liq(client, headers, liq["id"]),
    }


@pytest.mark.parametrize("nombre,cambio", CAMPOS_QUE_NO_DEBERIAN_MOVER_EL_FLETE)
def test_abono_parcial_campo_libre_no_mueve_el_flete(client, abonado, nombre, cambio):
    h, rec, liq = abonado["headers"], abonado["rec"], abonado["liq"]
    res = client.put(f"{REC}/{rec['id']}", json=cambio, headers=h)
    assert res.status_code == 200, res.text
    despues = leer(client, h, rec["id"])
    liq_despues = leer_liq(client, h, liq["id"])
    c = cuadra(liq_despues, [despues["valor_transporte"]])
    assert D(despues["valor_transporte"]) == D(rec["valor_transporte"]), (
        f"{nombre}: la foto de un flete con ABONO paso de {rec['valor_transporte']} a "
        f"{despues['valor_transporte']}"
    )
    assert c["suma_renglones"] == c["total"] == c["suma_fotos"]
    assert D(liq_despues["saldo"]) == c["total"] - D(liq_despues["pagado"])


def test_abono_parcial_tarifa_nueva_y_edicion(client, abonado):
    h, rec, liq, ruta, t = (
        abonado["headers"], abonado["rec"], abonado["liq"], abonado["ruta"],
        abonado["transportador"],
    )
    client.put(
        f"/api/v1/transportadores/{t['id']}",
        json={"rutas": [{"ruta_id": ruta["id"], "valor_transporte": "500"}]},
        headers=h,
    )
    assert client.put(f"{REC}/{rec['id']}", json={"observaciones": "x"}, headers=h).status_code == 200
    despues = leer(client, h, rec["id"])
    liq_despues = leer_liq(client, h, liq["id"])
    c = cuadra(liq_despues, [despues["valor_transporte"]])
    assert D(despues["valor_transporte"]) == D(rec["valor_transporte"])
    assert c["suma_renglones"] == c["total"] == c["suma_fotos"]


# ===========================================================================
# RONDA 2 · LOS CAMINOS DE AL LADO
# ===========================================================================


@pytest.fixture()
def centavo_repartido(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta, t, recs = montar_dos_del_mismo_dia(client, h)
    fotos_al_recibir = [D(r["valor_transporte"]) for r in recs]
    liq = generar_flete(client, h)[0]
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert client.post(f"{API}/{liq['id']}/pagar", headers=h).status_code == 200
    liq = leer_liq(client, h, liq["id"])
    fotos_pagadas = [D(leer(client, h, r["id"])["valor_transporte"]) for r in recs]
    return {
        "h": h, "ruta": ruta, "t": t, "recs": recs, "liq": liq,
        "fotos_al_recibir": fotos_al_recibir, "fotos_pagadas": fotos_pagadas,
    }


def test_el_reparto_de_verdad_movio_un_centavo(centavo_repartido):
    """Si esto falla, el resto de la ronda no está probando lo que cree."""
    c = centavo_repartido
    a_mano = centavos((D("44.23") + D("82.48")) * D("242.76"))
    assert D(c["liq"]["valor_transporte"]) == a_mano == D("30760.12")
    assert sum(c["fotos_al_recibir"], D(0)) == D("30760.11")
    assert sum(c["fotos_pagadas"], D(0)) == D("30760.12")
    assert c["fotos_pagadas"] != c["fotos_al_recibir"]


CAMPOS_SUELTOS = [
    ("observaciones", {"observaciones": "el puente estaba cerrado"}),
    ("bonificaciones", {"bonificaciones": "3000"}),
    ("descuentos", {"descuentos": "500"}),
    ("precio_litro", {"precio_litro": "2100"}),
]


@pytest.mark.parametrize("nombre,cambio", CAMPOS_SUELTOS)
def test_editar_no_devuelve_el_centavo_repartido(client, centavo_repartido, nombre, cambio):
    """Guardar un campo suelto NO puede devolver la foto a litros × tarifa.

    Si lo hiciera, el comprobante PAGADO ($30.760,12) dejaría de ser la suma de
    sus recepciones ($30.760,11): un centavo de descuadre en plata ya entregada.
    """
    c = centavo_repartido
    h, liq = c["h"], c["liq"]
    for r in c["recs"]:
        res = client.put(f"{REC}/{r['id']}", json=cambio, headers=h)
        assert res.status_code == 200, res.text
    fotos = [D(leer(client, h, r["id"])["valor_transporte"]) for r in c["recs"]]
    liq2 = leer_liq(client, h, liq["id"])
    assert fotos == c["fotos_pagadas"], (
        f"{nombre}: las fotos pasaron de {[str(f) for f in c['fotos_pagadas']]} a "
        f"{[str(f) for f in fotos]}"
    )
    assert sum(fotos, D(0)) == D(liq2["valor_transporte"]) == D("30760.12")
    assert sum((D(d["valor"]) for d in liq2["detalles"]), D(0)) == D("30760.12")


def test_sucursal_no_mueve_el_centavo_repartido(client, centavo_repartido):
    c = centavo_repartido
    h = c["h"]
    suc = client.post(
        "/api/v1/sucursales", json={"nombre": "Centro de acopio 2"}, headers=h
    )
    assert suc.status_code == 201, suc.text
    for r in c["recs"]:
        res = client.put(
            f"{REC}/{r['id']}", json={"sucursal_id": suc.json()["id"]}, headers=h
        )
        assert res.status_code == 200, res.text
    fotos = [D(leer(client, h, r["id"])["valor_transporte"]) for r in c["recs"]]
    liq2 = leer_liq(client, h, c["liq"]["id"])
    assert fotos == c["fotos_pagadas"]
    assert sum(fotos, D(0)) == D(liq2["valor_transporte"])


def test_reenviar_el_formulario_completo_no_devuelve_el_centavo(client, centavo_repartido):
    c = centavo_repartido
    h = c["h"]
    for r in c["recs"]:
        actual = leer(client, h, r["id"])
        formulario = {
            k: actual[k]
            for k in (
                "fecha", "transportador_id", "ruta_id", "sucursal_id", "cantidad_litros",
                "precio_litro", "bonificaciones", "descuentos", "observaciones", "estado",
            )
        }
        assert client.put(f"{REC}/{r['id']}", json=formulario, headers=h).status_code == 200
    fotos = [D(leer(client, h, r["id"])["valor_transporte"]) for r in c["recs"]]
    liq2 = leer_liq(client, h, c["liq"]["id"])
    assert fotos == c["fotos_pagadas"]
    assert sum(fotos, D(0)) == D(liq2["valor_transporte"]) == D("30760.12")


def test_tarifa_nueva_y_edicion_no_mueven_el_comprobante_pagado(client, centavo_repartido):
    c = centavo_repartido
    h = c["h"]
    client.put(
        f"/api/v1/transportadores/{c['t']['id']}",
        json={"rutas": [{"ruta_id": c["ruta"]["id"], "valor_transporte": "317.50"}]},
        headers=h,
    )
    for r in c["recs"]:
        assert client.put(
            f"{REC}/{r['id']}", json={"observaciones": "tarifa nueva"}, headers=h
        ).status_code == 200
    fotos = [D(leer(client, h, r["id"])["valor_transporte"]) for r in c["recs"]]
    liq2 = leer_liq(client, h, c["liq"]["id"])
    assert fotos == c["fotos_pagadas"], (
        f"con la tarifa a $317,50 las fotos de un flete PAGADO pasaron a "
        f"{[str(f) for f in fotos]}"
    )
    assert D(liq2["valor_transporte"]) == D("30760.12")
    assert sum(fotos, D(0)) == D("30760.12")


# ============ EL valor_transporte DE LA LIQUIDACIÓN PAGADA DEL PROVEEDOR =====
def test_la_liquidacion_pagada_del_proveedor_sigue_sumando_sus_recepciones(
    client, base_datos
):
    """La leche YA PAGADA; el flete todavía en borrador.

    La liquidación del proveedor guarda `valor_transporte` = suma de las fotos
    del flete de sus días, y el frontend la muestra. Si se le corrige la ruta al
    día (permitido: la ruta solo traba el flete) la foto cambia y esa cifra del
    comprobante PAGADO queda diciendo otra cosa.
    """
    h = auth_headers(client, "admin.a")
    napoles = client.post(
        "/api/v1/rutas", json={"nombre": "Napoles", "municipio": "Granada"}, headers=h
    ).json()
    mira = client.post(
        "/api/v1/rutas", json={"nombre": "Mira Valle", "municipio": "Granada"}, headers=h
    ).json()
    t = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Alex", "valor_transporte": "100",
              "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "242.76"},
                        {"ruta_id": mira["id"], "valor_transporte": "317.50"}]},
        headers=h,
    ).json()
    prov = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Patricia", "vereda": "El Roble", "precio_litro": "1800",
              "ruta_id": napoles["id"]},
        headers=h,
    ).json()
    rec = client.post(
        REC,
        json={"fecha": "2026-06-02", "proveedor_id": prov["id"],
              "transportador_id": t["id"], "ruta_id": napoles["id"],
              "cantidad_litros": "44.00"},
        headers=h,
    ).json()
    # Solo la de la LECHE, y se paga.
    leche = generar_flete(client, h, tipo="proveedor")[0]
    client.post(f"{API}/{leche['id']}/aprobar", headers=h)
    assert client.post(f"{API}/{leche['id']}/pagar", headers=h).status_code == 200
    leche = leer_liq(client, h, leche["id"])
    transporte_en_el_comprobante = D(leche["valor_transporte"])
    assert transporte_en_el_comprobante == D(rec["valor_transporte"]) == D("10681.44")

    # La ruta del día estaba mal: era Mira Valle. La leche ya se pagó, pero la
    # ruta solo la traba el flete y el flete no está liquidado.
    res = client.put(f"{REC}/{rec['id']}", json={"ruta_id": mira["id"]}, headers=h)
    assert res.status_code == 200, res.text
    despues = leer(client, h, rec["id"])
    assert D(despues["valor_transporte"]) == centavos(D("44.00") * D("317.50"))
    leche2 = leer_liq(client, h, leche["id"])
    assert leche2["estado"] == "pagada"
    assert D(leche2["valor_transporte"]) == D(despues["valor_transporte"]), (
        f"la liquidacion PAGADA de la leche sigue diciendo transporte "
        f"{leche2['valor_transporte']} y sus recepciones ya suman "
        f"{despues['valor_transporte']}"
    )


# ================== BORRAR EL PAGO, MOVER EL DÍA, VOLVER A PAGAR =============
def test_borrar_el_pago_mover_el_dia_y_volver_a_pagar_cuadra(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta, t, recs = montar_dos_del_mismo_dia(client, h)
    liq = generar_flete(client, h)[0]
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    client.post(f"{API}/{liq['id']}/pagar", headers=h)
    liq = leer_liq(client, h, liq["id"])
    pago_id = liq["pagos"][0]["id"]
    assert client.delete(f"{API}/{liq['id']}/pagos/{pago_id}", headers=h).status_code == 200
    # Ahora sí se puede corregir el día
    res = client.put(f"{REC}/{recs[0]['id']}", json={"cantidad_litros": "50.00"}, headers=h)
    assert res.status_code == 200, res.text
    liq2 = leer_liq(client, h, liq["id"])
    fotos = [D(leer(client, h, r["id"])["valor_transporte"]) for r in recs]
    a_mano = centavos((D("50.00") + D("82.48")) * D("242.76"))
    assert sum((D(d["valor"]) for d in liq2["detalles"]), D(0)) == D(liq2["valor_transporte"])
    assert sum(fotos, D(0)) == D(liq2["valor_transporte"])
    assert D(liq2["valor_transporte"]) == a_mano, (
        f"tras corregir los litros el comprobante dice {liq2['valor_transporte']} y la "
        f"cuenta a mano da {a_mano}"
    )


# ================== ¿SE PUEDE COBRAR EL MISMO DÍA DOS VECES? =================
def test_un_dia_pagado_no_vuelve_a_entrar_en_otra_liquidacion(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta, t, recs = montar_dos_del_mismo_dia(client, h)
    liq = generar_flete(client, h)[0]
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    client.post(f"{API}/{liq['id']}/pagar", headers=h)
    # Se vuelve a generar el mismo período
    otras = generar_flete(client, h)
    assert otras == [], f"se genero otra liquidacion de flete sobre dias ya pagados: {otras}"
    # Y anular la pagada no se deja
    assert client.post(f"{API}/{liq['id']}/anular", headers=h).status_code == 422


# ===========================================================================
# RONDA 3 · BORRAR EL CATÁLOGO DEBAJO DEL COMPROBANTE
# ===========================================================================


def test_quitarle_la_ruta_al_transportador_no_mueve_el_comprobante_pagado(client, base_datos):
    """PUT con `rutas: []` le borra la tarifa por ruta. El pagado no se mueve."""
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, litros="44.00")
    liq = generar_y_pagar(client, h)
    antes = D(liq["valor_transporte"])
    assert antes == centavos(D("44.00") * D("242.76")) == D("10681.44")
    res = client.put(f"/api/v1/transportadores/{t['id']}", json={"rutas": []}, headers=h)
    assert res.status_code == 200, res.text
    # Ahora la única tarifa que existe es la general ($100). El comprobante pagado
    # no puede moverse ni un peso.
    assert client.put(
        f"{REC}/{rec['id']}", json={"observaciones": "sin tarifa de ruta"}, headers=h
    ).status_code == 200
    despues = leer(client, h, rec["id"])
    liq2 = leer_liq(client, h, liq["id"])
    assert D(despues["valor_transporte"]) == D("10681.44"), (
        f"al quitarle la tarifa de la ruta, la foto de un flete PAGADO paso a "
        f"{despues['valor_transporte']} (con la general de $100 serian 4400.00)"
    )
    assert D(liq2["valor_transporte"]) == antes
    assert sum((D(d["valor"]) for d in liq2["detalles"]), D(0)) == antes


def test_borrar_la_ruta_del_catalogo_no_borra_el_renglon_pagado(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, litros="44.00")
    liq = generar_y_pagar(client, h)
    borrar = client.delete(f"/api/v1/rutas/{ruta['id']}", headers=h)
    assert borrar.status_code in (200, 204, 422), borrar.text
    if borrar.status_code == 422:
        pytest.skip(f"no se deja borrar la ruta: {borrar.text}")
    liq2 = leer_liq(client, h, liq["id"])
    assert D(liq2["valor_transporte"]) == D(liq["valor_transporte"])
    assert sum((D(d["valor"]) for d in liq2["detalles"]), D(0)) == D(liq["valor_transporte"])
    nombres = [d["ruta_nombre"] for d in liq2["detalles"]]
    assert nombres == ["Napoles"], (
        f"el comprobante PAGADO perdio el nombre de la ruta: {nombres}"
    )


def test_borrar_el_transportador_no_deja_el_dia_sin_poderse_editar(client, base_datos):
    """Borrado el transportador, el día con su flete PAGADO todavía se anota."""
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, litros="44.00")
    liq = generar_y_pagar(client, h)
    borrar = client.delete(f"/api/v1/transportadores/{t['id']}", headers=h)
    if borrar.status_code == 422:
        pytest.skip(f"no se deja borrar el transportador: {borrar.text}")
    assert borrar.status_code in (200, 204), borrar.text
    res = client.put(f"{REC}/{rec['id']}", json={"observaciones": "sin transportador"}, headers=h)
    assert res.status_code == 200, f"el dia quedo sin poderse anotar: {res.text}"
    despues = leer(client, h, rec["id"])
    liq2 = leer_liq(client, h, liq["id"])
    assert D(despues["valor_transporte"]) == D(rec["valor_transporte"])
    assert sum((D(d["valor"]) for d in liq2["detalles"]), D(0)) == D(liq2["valor_transporte"])


def test_borrar_el_transportador_con_flete_sin_liquidar_no_congela_el_dia(client, base_datos):
    """Sin liquidación de flete: borrado el transportador, corregir litros debe pasar."""
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, litros="44.00")
    borrar = client.delete(f"/api/v1/transportadores/{t['id']}", headers=h)
    if borrar.status_code == 422:
        pytest.skip(f"no se deja borrar el transportador: {borrar.text}")
    res = client.put(f"{REC}/{rec['id']}", json={"cantidad_litros": "50.00"}, headers=h)
    assert res.status_code == 200, (
        f"con el transportador borrado, corregir los litros del dia devuelve "
        f"{res.status_code}: {res.text}"
    )


def test_abono_y_luego_pagar_el_resto_no_mueve_nada(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta, t, prov, rec = montar(client, h, litros="44.00")
    liq = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15",
              "tipo": "transportador"},
        headers=h,
    ).json()[0]
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    client.post(f"{API}/{liq['id']}/pagos", json={"fecha": "2026-06-16", "valor": "5000"}, headers=h)
    # Entre el abono y el pago final, le suben la tarifa y editan el dia
    client.put(
        f"/api/v1/transportadores/{t['id']}",
        json={"rutas": [{"ruta_id": ruta["id"], "valor_transporte": "500"}]},
        headers=h,
    )
    assert client.put(f"{REC}/{rec['id']}", json={"observaciones": "x"}, headers=h).status_code == 200
    res = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert res.status_code == 200, res.text
    liq2 = leer_liq(client, h, liq["id"])
    despues = leer(client, h, rec["id"])
    assert liq2["estado"] == "pagada"
    assert D(liq2["valor_transporte"]) == D("10681.44")
    assert D(despues["valor_transporte"]) == D("10681.44")
    assert D(liq2["pagado"]) == D("10681.44")
    assert D(liq2["saldo"]) == D("0")
    assert sum((D(d["valor"]) for d in liq2["detalles"]), D(0)) == D("10681.44")
