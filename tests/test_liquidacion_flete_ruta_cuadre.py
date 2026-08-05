"""AUDITORÍA ADVERSARIAL DEL FLETE POR RUTA: buscando el peso que no cuadra.

No prueba que la funcionalidad sirva (eso ya lo hacen test_transportador_rutas.py y
test_liquidacion_flete_por_ruta.py). Lo que busca es lo contrario: un renglón que no
se reproduzca con calculadora, una columna que no sume el total, o un comprobante
cuyo total dejó de ser la suma de las fotos del flete que le entraron.

LA INVARIANTE que se persigue en TODOS los escenarios:
  1. en cada renglón, litros × precio_litro == valor, exacto al centavo;
  2. la suma de los valores de los renglones == liquidacion.valor_transporte;
  3. y ese total == la suma de los recepciones_leche.valor_transporte que entraron.

Las cifras son feas a propósito: tarifas con centavos ($242,76 · $317,50 ·
$1.833,33 · $1,01) y litros con decimales (44,23 · 82,48 · 137,45 · 103,75).
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQUIDACIONES = "/api/v1/liquidaciones"

NAPOLES = Decimal("242.76")
MIRA_VALLE = Decimal("317.50")
GENERAL = Decimal("200")


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _crear(client, h, url, payload):
    r = client.post(url, json=payload, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _escenario(client, h, general=GENERAL, napoles=NAPOLES, mira_valle=MIRA_VALLE):
    """Alex con las dos rutas a tarifas distintas, y un proveedor SIN ruta."""
    nap = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    mv = _crear(client, h, RUTAS, {"nombre": "Mira Valle", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo",
        "valor_transporte": str(general),
        "rutas": [
            {"ruta_id": nap["id"], "valor_transporte": str(napoles)},
            {"ruta_id": mv["id"], "valor_transporte": str(mira_valle)},
        ],
    })
    prov = {}
    for nombre, ruta in (("Aurelio", nap), ("Marleny", nap), ("Gilberto", mv)):
        prov[nombre] = _crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "x", "precio_litro": "1800", "ruta_id": ruta["id"]})
    # Sin ruta: su flete tiene que salir de la tarifa GENERAL.
    prov["Nohora"] = _crear(client, h, PROVEEDORES, {
        "nombre": "Nohora", "vereda": "x", "precio_litro": "1800"})
    return {"napoles": nap, "mira_valle": mv, "alex": alex, "prov": prov}


def _recibir(client, h, esc, fecha, quien, litros, **extra):
    payload = {
        "fecha": fecha,
        "proveedor_id": esc["prov"][quien]["id"],
        "transportador_id": esc["alex"]["id"],
        "cantidad_litros": litros,
    }
    payload.update(extra)
    return _crear(client, h, RECEPCIONES, payload)


def _liquidar(client, h, inicio="2026-07-16", fin="2026-07-31"):
    r = client.post(f"{LIQUIDACIONES}/generar",
                    json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": "transportador"},
                    headers=h)
    assert r.status_code in (200, 201), r.text
    assert r.json()["generadas"], "no se generó liquidación de flete"
    return r.json()["generadas"][0]


def _leer(client, h, liq_id):
    r = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _fotos_de(db_session, liq_id):
    """La suma de los recepciones_leche.valor_transporte apartados por ese comprobante."""
    filas = db_session.scalars(
        select(RecepcionLeche).where(
            RecepcionLeche.liquidacion_transporte_id == uuid.UUID(liq_id),
            RecepcionLeche.deleted_at.is_(None),
        )
    ).all()
    return sum((D(r.valor_transporte) for r in filas), D(0)), filas


def _renglones(liq):
    return sorted(liq["detalles"], key=lambda d: (d["fecha"], d["ruta_nombre"] or "", d["precio_litro"]))


def _invariante(liq, fotos=None, etiqueta=""):
    """Las tres partes, con las cifras exactas en el mensaje de fallo."""
    suma = D(0)
    fallas = []
    for r in _renglones(liq):
        l_, p, v = D(r["litros"]), D(r["precio_litro"]), D(r["valor"])
        if centavos(l_ * p) != v:
            fallas.append(f"    {r['fecha']} {r['ruta_nombre'] or '—'}: {l_} L x ${p} = "
                          f"${centavos(l_ * p)} pero el renglón dice ${v} "
                          f"(diferencia ${centavos(l_ * p) - v})")
        suma += v
    assert not fallas, f"[{etiqueta}] renglones que no se reproducen a mano:\n" + "\n".join(fallas)
    total = D(liq["valor_transporte"])
    assert suma == total, (f"[{etiqueta}] los renglones suman ${suma} y el comprobante dice "
                           f"${total} (diferencia ${suma - total})")
    if fotos is not None:
        assert total == fotos, (f"[{etiqueta}] el comprobante dice ${total} y las fotos guardadas "
                                f"suman ${fotos} (diferencia ${total - fotos})")
    litros_renglones = sum((D(r["litros"]) for r in liq["detalles"]), D(0))
    assert litros_renglones == D(liq["total_litros"]), (
        f"[{etiqueta}] los litros de los renglones suman {litros_renglones} y el comprobante "
        f"dice {D(liq['total_litros'])}")
    return suma


def _pagar(client, h, liq_id):
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    r = client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _cambiar_tarifa(client, h, esc, ruta_key, tarifa):
    """Le corrige al transportador la tarifa de UNA ruta, dejándole la otra igual."""
    r = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={"rutas": [
        {"ruta_id": esc["napoles"]["id"],
         "valor_transporte": str(tarifa if ruta_key == "napoles" else NAPOLES)},
        {"ruta_id": esc["mira_valle"]["id"],
         "valor_transporte": str(tarifa if ruta_key == "mira_valle" else MIRA_VALLE)},
    ]}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ===========================================================================
# 1. EL COMPROBANTE YA PAGADO Y LA FOTO QUE SE MUEVE POR DEBAJO
# ===========================================================================
def test_qa_comprobante_pagado_y_tarifa_nueva_al_tocar_un_campo_de_leche(
    client, base_datos, db_session
):
    """El flete YA SE PAGÓ. Se le sube la tarifa a la ruta y después se le corrige
    una BONIFICACIÓN de la leche a ese mismo día (un campo que el candado deja
    libre, porque la leche todavía no se ha liquidado).

    Nadie tocó los litros, ni la fecha, ni el transportador, ni la ruta. Y el
    candado deja pasar el guardado, con razón. Pero `_completar_y_calcular`
    recalcula SIEMPRE `valor_transporte` con la tarifa DE HOY, así que la foto del
    flete de un día ya pagado se reescribe sola.

    El comprobante pagado no se recalcula (bien: `_recuadrar` se lo salta), así que
    queda diciendo una cifra y las recepciones que lo componen sumando otra.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    recepcion = _recibir(client, h, esc, "2026-07-16", "Aurelio", "82")
    liq_id = _liquidar(client, h)["id"]
    pagada = _pagar(client, h, liq_id)

    antes_fotos, _ = _fotos_de(db_session, liq_id)
    print("\n===== 1. COMPROBANTE PAGADO =====")
    print(f"  82,00 L x $242,76 = ${antes_fotos}   (comprobante ${D(pagada['valor_transporte'])})")
    assert antes_fotos == D("19906.32") == D(pagada["valor_transporte"])

    _cambiar_tarifa(client, h, esc, "napoles", "300.00")
    r = client.put(f"{RECEPCIONES}/{recepcion['id']}",
                   json={"bonificaciones": "5000"}, headers=h)
    print(f"  se le sube Napoles a $300 y se le pone una bonificacion de leche: {r.status_code}")
    assert r.status_code == 200, r.text
    db_session.expire_all()

    foto_nueva = D(r.json()["valor_transporte"])
    despues_fotos, _ = _fotos_de(db_session, liq_id)
    liq = _leer(client, h, liq_id)
    print(f"  la foto del flete de ese dia paso de $19906.32 a ${foto_nueva}")
    print(f"  el comprobante PAGADO sigue diciendo ${D(liq['valor_transporte'])}")
    print(f"  las fotos que lo componen ahora suman ${despues_fotos}")
    print(f"  DESCUADRE: ${despues_fotos - D(liq['valor_transporte'])}")

    assert foto_nueva == D("19906.32"), (
        f"la foto del flete de un dia PAGADO se movio sola: era $19906.32 y quedo "
        f"${foto_nueva} (82 L x $300). Nadie cambio litros, fecha, ruta ni "
        f"transportador: solo una bonificacion de la leche")
    _invariante(liq, despues_fotos, "pagado + tarifa nueva")


# ===========================================================================
# 2. EN BORRADOR: la leche no mueve el flete, y la TARIFA nueva solo entra
#    cuando alguien oprime RECALCULAR
# ===========================================================================
def test_qa_borrador_la_leche_no_mueve_el_flete_y_la_tarifa_si(
    client, base_datos, db_session
):
    """Igual que el anterior pero con el flete en BORRADOR. Tres cosas distintas, y
    conviene no confundirlas:

      · corregir el PRECIO DE LA LECHE de un día NO le mueve el flete. Son dos platas
        de dos personas distintas, y el flete no depende del precio de la leche;
      · corregir LA TARIFA del transportador tampoco se lo mueve por sí sola, ni
        aunque enseguida se guarde un campo de la leche de ese mismo día: el RECUADRE
        en cascada vuelve a SUMAR y a repartir centavos, pero NO re-precifica;
      · lo que sí re-precifica es RECALCULAR, el botón que el dueño oprime a
        propósito. Es lo que pidió cuando tecleó $100 en vez de $242,76 y el sistema
        le siguió cobrando $100 para siempre: mientras por ese flete no haya salido
        plata, la tarifa que manda es la que está viva en el sistema.

    POR QUÉ CAMBIÓ LA EXPECTATIVA DEL TRAMO (b) —lo que hay que leer antes de volverla
    a cambiar—. Este tramo va por su tercera versión:

      1. la primera exigía que el total del flete no se moviera NUNCA. Estaba escrita
         cuando la foto del flete era la única memoria de la tarifa y no había forma de
         corregirla, así que dejaba clavado el defecto en vez de la garantía;
      2. la segunda —la que este comentario reemplaza— exigía que guardar un campo de
         la LECHE re-precificara el flete con la tarifa de hoy. Se midió y era un
         defecto peor: escribir una OBSERVACIÓN en un día (un campo que no le mueve la
         cuenta a nadie y que el candado deja libre a propósito) le subía el
         comprobante APROBADO del transportador de $30.760,12 a $38.013,00 y además le
         quitaba el visto bueno. $7.252,88 de cambio, sin causa visible, por haber
         escrito una nota;
      3. la de ahora: el recuadre automático no re-deriva tarifas, y GENERAR y
         RECALCULAR sí. Así el comprobante solo cambia de cifra cuando alguien lo
         pidió, y la tarifa corregida sigue teniendo por dónde llegar a los días ya
         liquidados. La regla está escrita en
         `LiquidacionService._recalcular_transporte_desde_recepciones`.

    Lo demás del tramo se conserva igual: la invariante en cada paso, la cuenta a mano
    de 219,45 L × $300 y la exigencia de que el comprobante no se quede con una tarifa
    que ya no existe en ninguna pantalla. Lo único que se movió es CUÁNDO entra.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    recepcion = _recibir(client, h, esc, "2026-07-16", "Aurelio", "82")
    _recibir(client, h, esc, "2026-07-17", "Marleny", "137.45")
    liq_id = _liquidar(client, h)["id"]
    antes = _leer(client, h, liq_id)
    fotos_antes, _ = _fotos_de(db_session, liq_id)
    _invariante(antes, fotos_antes, "borrador antes")

    print("\n===== 2. BORRADOR =====")
    # (a) SOLO la leche: el flete no se puede mover ni un peso.
    r = client.put(f"{RECEPCIONES}/{recepcion['id']}", json={"precio_litro": "1850"}, headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    solo_leche = _leer(client, h, liq_id)
    fotos_leche, _ = _fotos_de(db_session, liq_id)
    print(f"  total del flete antes   ${D(antes['valor_transporte'])}")
    print(f"  tras corregir el PRECIO DE LA LECHE del 16/07: "
          f"${D(solo_leche['valor_transporte'])}")
    _invariante(solo_leche, fotos_leche, "borrador tras la leche")
    assert D(solo_leche["valor_transporte"]) == D(antes["valor_transporte"]), (
        f"corregir el precio de la LECHE le movio el total del FLETE: de "
        f"${D(antes['valor_transporte'])} a ${D(solo_leche['valor_transporte'])}")

    # (b) La TARIFA de Napoles sube a $300 y se vuelve a guardar un campo de la
    # LECHE. El recuadre en cascada NO puede re-precificar: el comprobante se queda
    # con $242,76, que es la tarifa con la que se armó.
    _cambiar_tarifa(client, h, esc, "napoles", "300.00")
    r = client.put(f"{RECEPCIONES}/{recepcion['id']}", json={"precio_litro": "1850"}, headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    tras_recuadre = _leer(client, h, liq_id)
    fotos_recuadre, _ = _fotos_de(db_session, liq_id)
    print(f"  tras subir la TARIFA de Napoles a $300 y guardar la leche: "
          f"${D(tras_recuadre['valor_transporte'])} (el recuadre no re-precifica)")
    _invariante(tras_recuadre, fotos_recuadre, "borrador tras la tarifa, sin recalcular")
    assert D(tras_recuadre["valor_transporte"]) == D(antes["valor_transporte"]), (
        f"guardar un campo de la LECHE re-precifico el flete con la tarifa nueva: de "
        f"${D(antes['valor_transporte'])} a ${D(tras_recuadre['valor_transporte'])}. "
        f"Por ese camino, escribir una observacion en un dia le cambia la cifra a un "
        f"comprobante que nadie pidio re-precificar")
    assert {D(d["precio_litro"]) for d in tras_recuadre["detalles"]} == {NAPOLES}, (
        f"los renglones quedaron con una tarifa que nadie pidio aplicar: "
        f"{[str(d['precio_litro']) for d in tras_recuadre['detalles']]}")

    # (c) Y AHORA SÍ, el botón: RECALCULAR re-deriva con la tarifa de hoy y los dos
    # días de Napoles se van a $300.
    r = client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    despues = _leer(client, h, liq_id)
    fotos_despues, _ = _fotos_de(db_session, liq_id)
    print(f"  tras oprimir RECALCULAR: ${D(despues['valor_transporte'])}")
    for d in _renglones(despues):
        print(f"    {d['fecha']}  {d['ruta_nombre'] or '—':<11}{D(d['litros']):>9} L x "
              f"${D(d['precio_litro']):>9} = ${D(d['valor']):>11}")
    _invariante(despues, fotos_despues, "borrador tras recalcular")
    a_mano = centavos((D("82") + D("137.45")) * D("300.00"))
    assert D(despues["valor_transporte"]) == a_mano, (
        f"con Napoles a $300 la cuenta del dueño es 219,45 L x $300 = ${a_mano} y el "
        f"comprobante dice ${D(despues['valor_transporte'])}")
    assert {D(d["precio_litro"]) for d in despues["detalles"]} == {D("300.00")}, (
        "el comprobante quedó con una tarifa que ya no existe en ninguna pantalla")


# ===========================================================================
# 3. CAMBIARLE LA RUTA AL DÍA (la ruta ahora entra en la plata)
# ===========================================================================
def test_qa_cambiar_la_ruta_del_dia_recalcula_y_cuadra(client, base_datos, db_session):
    """El 16/07 Aurelio se pasa de Nápoles ($242,76) a Mira Valle ($317,50).

      82,00 L x $242,76 = $19.906,32   ->   82,00 L x $317,50 = $26.035,00

    El comprobante en borrador tiene que quedar con el renglón en la ruta nueva y
    seguir cuadrando al centavo.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    recepcion = _recibir(client, h, esc, "2026-07-16", "Aurelio", "82")
    _recibir(client, h, esc, "2026-07-16", "Gilberto", "96.30")
    liq_id = _liquidar(client, h)["id"]
    _invariante(_leer(client, h, liq_id), _fotos_de(db_session, liq_id)[0], "antes de mover la ruta")

    r = client.put(f"{RECEPCIONES}/{recepcion['id']}",
                   json={"ruta_id": esc["mira_valle"]["id"]}, headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    print("\n===== 3. LA RUTA DEL DIA CAMBIA =====")
    print(f"  foto nueva del 16/07 Aurelio: ${D(r.json()['valor_transporte'])} (82 L x $317,50)")
    assert D(r.json()["valor_transporte"]) == D("26035.00")

    liq = _leer(client, h, liq_id)
    fotos, _ = _fotos_de(db_session, liq_id)
    for d in _renglones(liq):
        print(f"    {d['fecha']}  {d['ruta_nombre'] or '—':<11}{D(d['litros']):>9} L x "
              f"${D(d['precio_litro']):>9} = ${D(d['valor']):>11}")
    _invariante(liq, fotos, "ruta movida")
    # Los dos quedaron en Mira Valle el mismo día: UN solo renglón de 178,30 L.
    assert len(liq["detalles"]) == 1, [(d["ruta_nombre"], d["litros"]) for d in liq["detalles"]]
    assert D(liq["detalles"][0]["litros"]) == D("178.30")
    assert D(liq["valor_transporte"]) == centavos(D("178.30") * MIRA_VALLE)


# ===========================================================================
# 4. BORRAR UN DÍA Y RECALCULAR
# ===========================================================================
def test_qa_borrar_un_dia_del_comprobante_recalcula_y_cuadra(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    a = _recibir(client, h, esc, "2026-07-16", "Aurelio", "82")
    _recibir(client, h, esc, "2026-07-16", "Gilberto", "96.30")
    _recibir(client, h, esc, "2026-07-17", "Marleny", "137.45")
    liq_id = _liquidar(client, h)["id"]

    r = client.delete(f"{RECEPCIONES}/{a['id']}", headers=h)
    assert r.status_code in (200, 204), r.text
    db_session.expire_all()

    liq = _leer(client, h, liq_id)
    fotos, filas = _fotos_de(db_session, liq_id)
    print("\n===== 4. UN DIA BORRADO =====")
    print(f"  quedan {len(filas)} recepciones, fotos ${fotos}, comprobante "
          f"${D(liq['valor_transporte'])}, {len(liq['detalles'])} renglones")
    for d in _renglones(liq):
        print(f"    {d['fecha']}  {d['ruta_nombre'] or '—':<11}{D(d['litros']):>9} L x "
              f"${D(d['precio_litro']):>9} = ${D(d['valor']):>11}")
    _invariante(liq, fotos, "dia borrado")
    assert D(liq["valor_transporte"]) == centavos(D("96.30") * MIRA_VALLE) + centavos(
        D("137.45") * NAPOLES)


# ===========================================================================
# 5. QUITARLE EL TRANSPORTADOR AL DÍA
# ===========================================================================
def test_qa_quitarle_el_transportador_al_dia_deja_el_comprobante_cuadrado(
    client, base_datos, db_session
):
    """El día se le suelta al transportador: su comprobante se recalcula sin él y no
    puede quedar ni con el renglón ni con la plata de ese día."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    a = _recibir(client, h, esc, "2026-07-16", "Aurelio", "82")
    _recibir(client, h, esc, "2026-07-17", "Gilberto", "103.75")
    liq_id = _liquidar(client, h)["id"]

    r = client.put(f"{RECEPCIONES}/{a['id']}", json={"transportador_id": None}, headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()

    liq = _leer(client, h, liq_id)
    fotos, filas = _fotos_de(db_session, liq_id)
    print("\n===== 5. SIN TRANSPORTADOR =====")
    print(f"  el dia suelto queda con flete ${D(r.json()['valor_transporte'])}")
    print(f"  al comprobante le quedan {len(filas)} recepciones (${fotos}) y "
          f"{len(liq['detalles'])} renglones por ${D(liq['valor_transporte'])}")
    _invariante(liq, fotos, "transportador quitado")
    assert D(liq["valor_transporte"]) == centavos(D("103.75") * MIRA_VALLE)


# ===========================================================================
# 6. MOVER EL DÍA FUERA DEL PERÍODO DEL COMPROBANTE
# ===========================================================================
def test_qa_mover_el_dia_fuera_del_periodo(client, base_datos, db_session):
    """Se corrige la fecha del 16/07 al 05/08, fuera de la quincena liquidada.

    El renglón se va con la fecha nueva, y el comprobante queda con un renglón de un
    día que no pertenece a su período. Lo que no puede pasar es que descuadre.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    a = _recibir(client, h, esc, "2026-07-16", "Aurelio", "82")
    _recibir(client, h, esc, "2026-07-17", "Gilberto", "103.75")
    liq_id = _liquidar(client, h)["id"]

    r = client.put(f"{RECEPCIONES}/{a['id']}", json={"fecha": "2026-08-05"}, headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    liq = _leer(client, h, liq_id)
    fotos, _ = _fotos_de(db_session, liq_id)
    print("\n===== 6. EL DIA SE VA DE LA QUINCENA =====")
    print(f"  periodo {liq['periodo_inicio']} a {liq['periodo_fin']}")
    for d in _renglones(liq):
        print(f"    {d['fecha']}  {d['ruta_nombre'] or '—':<11}{D(d['litros']):>9} L x "
              f"${D(d['precio_litro']):>9} = ${D(d['valor']):>11}")
    _invariante(liq, fotos, "dia fuera del periodo")


# ===========================================================================
# 7. UN DÍA CON LAS DOS RUTAS Y UNA TERCERA RECEPCIÓN SIN RUTA
# ===========================================================================
def test_qa_dia_con_dos_rutas_y_una_sin_ruta_con_cifras_feas(client, base_datos, db_session):
    """16/07: Nápoles ($242,76), Mira Valle ($317,50) y un día SIN ruta (general
    $1.833,33, a propósito enorme y con centavos para que se note si se cuela).

      Napoles     44,23 L x $  242,76 = $   10.737,27   (10.737,2748)
      Napoles     82,48 L x $  242,76 = $   20.022,84   (20.022,8448)
      Mira Valle 103,75 L x $  317,50 = $   32.940,63   (32.940,625, medio centavo)
      sin ruta     7,77 L x $1.833,33 = $   14.244,97   (14.244,9741)

    Las cuatro cifras de arriba son las fotos EN EL MOMENTO DE RECIBIR la leche, y
    así se quedan escritas. Al liquidar manda el comprobante: los dos días de
    Nápoles se juntan en UN renglón de 126,71 L × $242,76 = $30.760,12 (uno más que
    los $30.760,11 que suman las dos fotos redondeadas por separado) y ese centavo
    SE REPARTE, así que después de liquidar la foto de Aurelio queda en $10.737,28.
    Por eso la invariante se revisa contra las fotos LEÍDAS DE LA BASE y no contra
    las que devolvió el POST: el comprobante manda, y las fotos lo siguen.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, general="1833.33")
    entregas = [("Aurelio", "44.23"), ("Marleny", "82.48"),
                ("Gilberto", "103.75"), ("Nohora", "7.77")]
    al_recibir = D(0)
    print("\n===== 7. DOS RUTAS Y UNA SIN RUTA, EL MISMO DIA =====")
    for quien, litros in entregas:
        rec = _recibir(client, h, esc, "2026-07-16", quien, litros)
        print(f"  {quien:<9}{litros:>8} L -> ${D(rec['valor_transporte']):>12}")
        al_recibir += D(rec["valor_transporte"])
    assert al_recibir == (D("10737.27") + D("20022.84") + D("32940.63") + D("14244.97"))

    liq = _leer(client, h, _liquidar(client, h)["id"])
    fotos, _ = _fotos_de(db_session, liq["id"])
    for d in _renglones(liq):
        print(f"    {d['fecha']}  {d['ruta_nombre'] or '—':<11}{D(d['litros']):>9} L x "
              f"${D(d['precio_litro']):>9} = ${D(d['valor']):>12}")
    print(f"  fotos al recibir ${al_recibir} · fotos ya repartidas ${fotos} · "
          f"comprobante ${D(liq['valor_transporte'])}")
    _invariante(liq, fotos, "dos rutas + sin ruta")
    # El centavo que se movió es EXACTAMENTE el del renglón de Nápoles, y no más.
    assert fotos - al_recibir == D("0.01"), (
        f"el reparto movió ${fotos - al_recibir} y solo tenía que mover el centavo "
        f"del renglón de Napoles")
    assert D(liq["valor_transporte"]) == centavos(D("126.71") * NAPOLES) + centavos(
        D("103.75") * MIRA_VALLE) + centavos(D("7.77") * D("1833.33"))
    # El renglón sin ruta tiene que existir y llevar la tarifa GENERAL.
    sin_ruta = [d for d in liq["detalles"] if d["ruta_id"] is None]
    assert len(sin_ruta) == 1 and D(sin_ruta[0]["precio_litro"]) == D("1833.33"), sin_ruta
    # Y los dos de Nápoles del mismo día, a la misma tarifa, no pueden salir como
    # dos renglones distintos: el dueño ve dos líneas iguales sin saber por qué.
    napoles = [d for d in liq["detalles"] if d["ruta_nombre"] == "Napoles"]
    assert len(napoles) == 1, (
        "el mismo dia y la misma ruta a la MISMA tarifa salio partido en "
        f"{len(napoles)} renglones: " + str([(d["litros"], d["precio_litro"], d["valor"])
                                             for d in napoles]))


# ===========================================================================
# 8. QUINCENA COMPLETA CON CIFRAS FEAS (15 días, 4 proveedores)
# ===========================================================================
def test_qa_quincena_completa_con_cifras_feas(client, base_datos, db_session):
    """15 días, 4 proveedores (dos rutas y uno sin ruta), litros con decimales y
    tarifas con centavos. 60 recepciones: el sitio donde el redondeo renglón por
    renglón se puede separar del redondeo del total.

    Las fotos se leen DE LA BASE después de liquidar, no de la respuesta del POST:
    al armar el comprobante, el renglón de un (día, ruta) se calcula una sola vez
    (litros sumados × tarifa) y su plata se reparte entre las recepciones de ese
    grupo, así que unas cuantas fotos quedan un centavo distintas de como se
    tomaron. Lo que tiene que cuadrar es el comprobante contra lo que hay guardado.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, general="1833.33", napoles="242.76", mira_valle="317.50")
    litros_feos = ["44.23", "82.48", "137.45", "103.75", "44.50", "82.75",
                   "0.33", "199.99", "7.07", "66.66"]
    al_recibir = D(0)
    n = 0
    for dia in range(16, 31):
        for i, quien in enumerate(("Aurelio", "Marleny", "Gilberto", "Nohora")):
            litros = litros_feos[(dia + i) % len(litros_feos)]
            rec = _recibir(client, h, esc, f"2026-07-{dia}", quien, litros)
            al_recibir += D(rec["valor_transporte"])
            n += 1
    liq = _leer(client, h, _liquidar(client, h)["id"])
    fotos, _ = _fotos_de(db_session, liq["id"])
    print("\n===== 8. QUINCENA COMPLETA =====")
    print(f"  {n} recepciones · fotos al recibir ${al_recibir} · fotos repartidas ${fotos} "
          f"(el reparto movió ${fotos - al_recibir}) · comprobante "
          f"${D(liq['valor_transporte'])} · {len(liq['detalles'])} renglones")
    suma = _invariante(liq, fotos, "quincena completa")
    # El reparto mueve CENTAVOS, no pesos: a lo sumo uno por recepción.
    assert abs(fotos - al_recibir) <= D("0.01") * n, (
        f"el reparto movió ${fotos - al_recibir} entre {n} recepciones: son más "
        f"centavos de los que el doble redondeo puede explicar")
    # Y el cuadre a mano del dueño, día por día y ruta por ruta.
    for d in liq["detalles"]:
        assert centavos(D(d["litros"]) * D(d["precio_litro"])) == D(d["valor"])
    print(f"  suma de los renglones ${suma}")
    # Un renglón por (día, ruta): 15 días x 3 grupos (Nápoles, Mira Valle, sin ruta).
    print(f"  renglones esperados 45, obtenidos {len(liq['detalles'])}")
    partidos = {}
    for d in liq["detalles"]:
        partidos.setdefault((d["fecha"], d["ruta_nombre"]), []).append(d)
    repetidos = {k: v for k, v in partidos.items() if len(v) > 1}
    for (fecha, ruta), ds in repetidos.items():
        print(f"    PARTIDO {fecha} {ruta}: " + " | ".join(
            f"{D(x['litros'])} L x ${D(x['precio_litro'])} = ${D(x['valor'])}" for x in ds))
    assert not repetidos, (
        f"{len(repetidos)} (dia, ruta) salieron partidos en varios renglones a la MISMA "
        f"tarifa; el dueño ve dos lineas iguales el mismo dia sin explicacion")


# ===========================================================================
# 9. EL MEDIO CENTAVO: tarifa que siempre cae en .xx5
# ===========================================================================
def test_qa_el_medio_centavo_de_la_tarifa_a_317_50(client, base_datos, db_session):
    """$317,50 el litro con litros de centavo impar da SIEMPRE medio centavo:

      103,75 L x $317,50 = $32.940,625  -> $32.940,63  (medio centavo arriba)
       96,31 L x $317,50 = $30.578,425  -> $30.578,43  (medio centavo arriba)

    Sumadas, las dos fotos dan $63.519,06. Pero 200,06 L x $317,50 = $63.519,05:
    un peso... un CENTAVO de diferencia entre redondear renglón por renglón y
    redondear el total.

    OJO CON ESTA PRUEBA: en su primera versión exigía lo contrario —"el comprobante
    tiene que quedar del lado de las fotos"— y esa expectativa era la equivocada. El
    dueño cuadra el comprobante A MANO: junta los litros de la ruta del día
    (200,06 L), los multiplica por la tarifa ($317,50) y eso es lo que el papel
    TIENE que decir. Si el comprobante dijera $63.519,06, la cuenta del dueño no le
    daría y él tendría razón. Así que MANDA EL COMPROBANTE ($63.519,05) y el centavo
    se le quita a una de las dos fotos, para que las fotos sigan sumando el total.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    a = _recibir(client, h, esc, "2026-07-16", "Gilberto", "103.75")
    # Segundo proveedor de Mira Valle para poder tener dos recepciones el mismo día.
    esc["prov"]["Ovidio"] = _crear(client, h, PROVEEDORES, {
        "nombre": "Ovidio", "vereda": "x", "precio_litro": "1800",
        "ruta_id": esc["mira_valle"]["id"]})
    b = _recibir(client, h, esc, "2026-07-16", "Ovidio", "96.31")
    al_recibir = D(a["valor_transporte"]) + D(b["valor_transporte"])
    print("\n===== 9. EL MEDIO CENTAVO =====")
    print(f"  103,75 L -> ${D(a['valor_transporte'])}   96,31 L -> ${D(b['valor_transporte'])}")
    print(f"  suma de las fotos al recibir ${al_recibir}")
    print(f"  200,06 L x $317,50           ${centavos(D('200.06') * MIRA_VALLE)}  <- manda esta")
    assert al_recibir == D("63519.06")
    assert centavos(D("200.06") * MIRA_VALLE) == D("63519.05")

    liq = _leer(client, h, _liquidar(client, h)["id"])
    fotos, filas = _fotos_de(db_session, liq["id"])
    for d in _renglones(liq):
        print(f"    {d['fecha']}  {d['ruta_nombre'] or '—':<11}{D(d['litros']):>9} L x "
              f"${D(d['precio_litro']):>9} = ${D(d['valor']):>11}")
    print(f"  fotos ya repartidas: {[str(D(f.valor_transporte)) for f in filas]} = ${fotos}")
    _invariante(liq, fotos, "medio centavo")
    # UN renglón, la cuenta del dueño exacta, y el centavo salió de una sola foto.
    assert len(liq["detalles"]) == 1, [(d["litros"], d["valor"]) for d in liq["detalles"]]
    assert D(liq["valor_transporte"]) == D("63519.05")
    assert al_recibir - fotos == D("0.01")


# ===========================================================================
# 10. LA TARIFA CAMBIADA A MITAD DE QUINCENA, CON LOS DOS ÓRDENES
# ===========================================================================
def test_qa_tarifa_cambiada_a_mitad_de_quincena_en_el_mismo_dia(client, base_datos, db_session):
    """Le corrigen la tarifa de Nápoles entre la una y la otra recepción del MISMO día.

    Cada foto se toma con la tarifa que estaba puesta en ese momento:

      44,23 L x $242,76 = $10.737,27   (la vieja)
      82,48 L x $300,00 = $24.744,00   (la nueva)

    PERO AL LIQUIDAR MANDA LA TARIFA VIVA, que es la única que el dueño puede ver en
    la pantalla del transportador y la única con la que puede reproducir la columna:

      126,71 L x $300,00 = $38.013,00

    y las dos fotos se vuelven a derivar a $300 para que sigan sumando el total. Antes
    esto salía en DOS renglones —uno a $242,76 y otro a $300— y el dueño no encontraba
    de dónde había salido el primero, porque esa tarifa ya no existía en ninguna parte;
    era el mismo defecto que le dejaba una tarifa mal tecleada cobrándose para siempre.

    La tarifa vieja solo sobrevive donde tiene que sobrevivir: en un flete YA PAGADO,
    que no se toca ni por un centavo (eso lo cuida el escenario 1 de este archivo).
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    a = _recibir(client, h, esc, "2026-07-16", "Aurelio", "44.23")
    _cambiar_tarifa(client, h, esc, "napoles", "300.00")
    b = _recibir(client, h, esc, "2026-07-16", "Marleny", "82.48")
    print("\n===== 10. TARIFA CAMBIADA A MITAD DE QUINCENA =====")
    print(f"  44,23 L -> ${D(a['valor_transporte'])}   82,48 L -> ${D(b['valor_transporte'])}")
    assert D(a["valor_transporte"]) == D("10737.27")
    assert D(b["valor_transporte"]) == D("24744.00")

    liq = _leer(client, h, _liquidar(client, h)["id"])
    db_session.expire_all()
    # Las fotos se leen DESPUÉS de liquidar: el comprobante las volvió a derivar con
    # la tarifa viva, y la invariante que importa es que sumen el total del papel.
    fotos, filas = _fotos_de(db_session, liq["id"])
    for d in _renglones(liq):
        print(f"    {d['fecha']}  {d['ruta_nombre'] or '—':<11}{D(d['litros']):>9} L x "
              f"${D(d['precio_litro']):>9} = ${D(d['valor']):>11}")
    print(f"  fotos re-derivadas: {[str(D(f.valor_transporte)) for f in filas]} = ${fotos}")
    _invariante(liq, fotos, "tarifa a mitad de quincena")
    a_mano = centavos((D("44.23") + D("82.48")) * D("300.00"))
    assert len(liq["detalles"]) == 1, [(d["precio_litro"], d["valor"]) for d in liq["detalles"]]
    assert D(liq["valor_transporte"]) == a_mano == D("38013.00")


# ===========================================================================
# 11. LA PRE-LIQUIDACIÓN CONTRA EL COMPROBANTE, CON CIFRAS FEAS
# ===========================================================================
def test_qa_preliquidacion_y_comprobante_dicen_lo_mismo(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, general="1833.33")
    for quien, litros in (("Aurelio", "44.23"), ("Marleny", "82.48"),
                          ("Gilberto", "103.75"), ("Nohora", "0.33")):
        _recibir(client, h, esc, "2026-07-16", quien, litros)
    for quien, litros in (("Aurelio", "137.45"), ("Gilberto", "96.31")):
        _recibir(client, h, esc, "2026-07-17", quien, litros)

    pre = client.post(f"{LIQUIDACIONES}/previsualizar", json={
        "periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
        "tipo": "transportador", "tercero_id": esc["alex"]["id"]}, headers=h)
    assert pre.status_code in (200, 201), pre.text
    avance = pre.json()[0] if isinstance(pre.json(), list) else pre.json()

    print("\n===== 11. PRE-LIQUIDACION vs COMPROBANTE =====")
    suma_pre = D(0)
    for d in sorted(avance["detalles"], key=lambda x: (x["fecha"], x["ruta_nombre"] or "")):
        l_, p, v = D(d["litros"]), D(d["precio_litro"]), D(d["valor"])
        print(f"    {d['fecha']}  {d['ruta_nombre'] or '—':<11}{l_:>9} L x ${p:>9} = ${v:>11}")
        assert centavos(l_ * p) == v, (
            f"renglon de la PRE-liquidacion que no cuadra: {l_} x {p} = {centavos(l_ * p)} "
            f"y dice {v}")
        suma_pre += v
    assert suma_pre == D(avance["valor_transporte"]), (
        f"los renglones de la pre-liquidacion suman ${suma_pre} y dice "
        f"${D(avance['valor_transporte'])}")

    liq = _leer(client, h, _liquidar(client, h)["id"])
    fotos, _ = _fotos_de(db_session, liq["id"])
    _invariante(liq, fotos, "comprobante")
    print(f"  pre ${D(avance['valor_transporte'])} · comprobante ${D(liq['valor_transporte'])}")
    assert D(avance["valor_transporte"]) == D(liq["valor_transporte"])
    assert [(d["fecha"], d["ruta_nombre"], D(d["litros"]), D(d["precio_litro"]), D(d["valor"]))
            for d in sorted(avance["detalles"], key=lambda x: (x["fecha"], x["ruta_nombre"] or ""))
            ] == [
        (d["fecha"], d["ruta_nombre"], D(d["litros"]), D(d["precio_litro"]), D(d["valor"]))
        for d in sorted(liq["detalles"], key=lambda x: (x["fecha"], x["ruta_nombre"] or ""))]


# ===========================================================================
# 12. LA VALIDACIÓN DE LAS RUTAS (una ruta de otra empresa es plata ajena)
# ===========================================================================
def test_qa_rutas_ajenas_repetidas_y_borradas(client, base_datos):
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    esc = _escenario(client, h_a)
    ruta_b = _crear(client, h_b, RUTAS, {"nombre": "Ajena", "municipio": "X"})

    print("\n===== 12. VALIDACION DE RUTAS =====")
    ajena = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "rutas": [{"ruta_id": ruta_b["id"], "valor_transporte": "999"}]}, headers=h_a)
    print(f"  ruta de otra empresa: {ajena.status_code}")
    assert ajena.status_code == 422, ajena.text

    repetida = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={"rutas": [
        {"ruta_id": esc["napoles"]["id"], "valor_transporte": "242.76"},
        {"ruta_id": esc["napoles"]["id"], "valor_transporte": "300"}]}, headers=h_a)
    print(f"  la misma ruta dos veces: {repetida.status_code}")
    assert repetida.status_code == 422, repetida.text

    # Nada de eso pudo dejarle las tarifas a medias.
    quedo = client.get(f"{TRANSPORTADORES}/{esc['alex']['id']}", headers=h_a).json()
    tarifas = {r["nombre"]: D(r["valor_transporte"]) for r in quedo["rutas"]}
    print(f"  las tarifas siguen intactas: {tarifas}")
    assert tarifas == {"Napoles": NAPOLES, "Mira Valle": MIRA_VALLE}

    # Y el PATCH que no habla de rutas no las puede borrar.
    solo_tel = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}",
                          json={"telefono": "3110000000"}, headers=h_a)
    assert solo_tel.status_code == 200, solo_tel.text
    assert len(solo_tel.json()["rutas"]) == 2, solo_tel.json()


# ===========================================================================
# 13. UNA RUTA CON TARIFA CERO: cero puesto a mano, no cero por descuido
# ===========================================================================
def test_qa_ruta_con_tarifa_cero_no_cae_en_la_general(client, base_datos, db_session):
    """Mira Valle en $0 y la general en $1.833,33: el día de Mira Valle tiene que
    salir en cero, no a la general. Y el comprobante tiene que seguir cuadrando con
    un renglón en cero adentro.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, general="1833.33", mira_valle="0")
    a = _recibir(client, h, esc, "2026-07-16", "Gilberto", "103.75")
    b = _recibir(client, h, esc, "2026-07-16", "Aurelio", "44.23")
    print("\n===== 13. TARIFA EN CERO =====")
    print(f"  Mira Valle 103,75 L -> ${D(a['valor_transporte'])}  "
          f"Napoles 44,23 L -> ${D(b['valor_transporte'])}")
    assert D(a["valor_transporte"]) == D("0.00"), (
        "la ruta con tarifa 0 cayo en la general: el flete de ese dia salio en "
        f"${D(a['valor_transporte'])} en vez de $0")
    liq = _leer(client, h, _liquidar(client, h)["id"])
    fotos, _ = _fotos_de(db_session, liq["id"])
    for d in _renglones(liq):
        print(f"    {d['fecha']}  {d['ruta_nombre'] or '—':<11}{D(d['litros']):>9} L x "
              f"${D(d['precio_litro']):>9} = ${D(d['valor']):>11}")
    _invariante(liq, fotos, "tarifa cero")


# ===========================================================================
# 14. EL GUARDADO QUE NO CAMBIA NADA Y MUEVE LA PLATA DE UNA QUINCENA PAGADA
# ===========================================================================
def test_qa_guardar_sin_cambiar_nada_mueve_el_flete_pagado(client, base_datos, db_session):
    """El diálogo manda TODO el formulario en cada guardado (así lo dice
    `_cambios_reales` en recepcion/service.py). Se abre un día de una quincena de
    flete YA PAGADA y se presiona Guardar sin tocar nada.

    `_cambios_reales` da vacío, el candado deja pasar —y hace bien, no cambió nada—
    pero `_completar_y_calcular` recalcula el flete con la tarifa de HOY. Si entre
    tanto le subieron la tarifa a la ruta, la foto del flete de ese día pagado se
    reescribe sin que nadie lo haya pedido.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    rec = _recibir(client, h, esc, "2026-07-16", "Aurelio", "82.48")
    liq_id = _liquidar(client, h)["id"]
    pagada = _pagar(client, h, liq_id)
    assert D(pagada["valor_transporte"]) == D("20022.84")

    _cambiar_tarifa(client, h, esc, "napoles", "300.00")
    # EL MISMO FORMULARIO, tal cual estaba: ni un dato distinto.
    r = client.put(f"{RECEPCIONES}/{rec['id']}", json={
        "fecha": "2026-07-16",
        "cantidad_litros": "82.48",
        "transportador_id": esc["alex"]["id"],
        "ruta_id": esc["napoles"]["id"],
        "precio_litro": rec["precio_litro"],
    }, headers=h)
    print("\n===== 14. GUARDAR SIN CAMBIAR NADA =====")
    print(f"  respuesta {r.status_code}")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    fotos, _ = _fotos_de(db_session, liq_id)
    liq = _leer(client, h, liq_id)
    print(f"  flete guardado del dia: era $20022.84, quedo ${D(r.json()['valor_transporte'])}")
    print(f"  comprobante PAGADO: ${D(liq['valor_transporte'])} · fotos ${fotos} · "
          f"descuadre ${fotos - D(liq['valor_transporte'])}")
    assert D(r.json()["valor_transporte"]) == D("20022.84"), (
        f"un guardado que no cambio ni un dato movio el flete de una quincena PAGADA: "
        f"de $20022.84 a ${D(r.json()['valor_transporte'])}")


# ===========================================================================
# 15. EL CUADRE DEL DUEÑO POR RUTA: litros de la ruta x tarifa de la ruta
# ===========================================================================
def test_qa_los_litros_de_la_ruta_por_su_tarifa_no_dan_el_valor_de_la_ruta(
    client, base_datos, db_session
):
    """Así cuadra el dueño: junta los litros de Nápoles del día, los multiplica por
    $242,76 y compara con la suma de los renglones de Nápoles de ese día.

      44,23 L + 82,48 L = 126,71 L
      126,71 L x $242,76 = $30.760,1196  ->  $30.760,12
      pero los dos renglones suman        $10.737,27 + $20.022,84 = $30.760,11

    Un centavo. Y encima el comprobante muestra DOS líneas del mismo día, la misma
    ruta y la MISMA tarifa, sin nada que explique por qué está partido.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, "2026-07-16", "Aurelio", "44.23")
    _recibir(client, h, esc, "2026-07-16", "Marleny", "82.48")
    liq = _leer(client, h, _liquidar(client, h)["id"])
    fotos, _ = _fotos_de(db_session, liq["id"])
    _invariante(liq, fotos, "cuadre por ruta")

    print("\n===== 15. EL CUADRE POR RUTA =====")
    por_ruta = {}
    for d in liq["detalles"]:
        clave = (d["fecha"], d["ruta_nombre"], D(d["precio_litro"]))
        acum = por_ruta.setdefault(clave, [D(0), D(0), 0])
        acum[0] += D(d["litros"])
        acum[1] += D(d["valor"])
        acum[2] += 1
    fallas = []
    for (fecha, ruta, tarifa), (litros_, valor, cuantos) in sorted(por_ruta.items(), key=str):
        a_mano = centavos(litros_ * tarifa)
        print(f"  {fecha} {ruta or '—':<11}{litros_:>9} L x ${tarifa} = ${a_mano} a mano · "
              f"${valor} en el comprobante ({cuantos} renglon/es) · diferencia "
              f"${a_mano - valor}")
        if a_mano != valor:
            fallas.append(f"    {fecha} {ruta}: {litros_} L x ${tarifa} = ${a_mano} a mano, "
                          f"${valor} en el comprobante, diferencia ${a_mano - valor} "
                          f"(repartido en {cuantos} renglones)")
    assert not fallas, ("el dueño suma los litros de la ruta, los multiplica por la tarifa y "
                        "no le da:\n" + "\n".join(fallas))


# ===========================================================================
# 16. EL PRECIO PROMEDIO NO SE REDONDEA COMO TODO LO DEMÁS
# ===========================================================================
def test_qa_precio_promedio_del_flete_usa_otro_redondeo(client, base_datos, db_session):
    """Nápoles a $2,50 y Mira Valle a $2,51, 100 L en cada una:

      $250,00 + $251,00 = $501,00 sobre 200,00 L = $2,505 el litro

    El proyecto redondea el medio centavo PARA ARRIBA (`_centavos`, ROUND_HALF_UP)
    en toda la plata. `precio_promedio` usa `.quantize(Decimal("0.01"))` sin decir el
    modo, así que cae en el ROUND_HALF_EVEN de Python y da $2,50 en vez de $2,51.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h, napoles="2.50", mira_valle="2.51")
    _recibir(client, h, esc, "2026-07-16", "Aurelio", "100")
    _recibir(client, h, esc, "2026-07-16", "Gilberto", "100")
    liq = _leer(client, h, _liquidar(client, h)["id"])
    fotos, _ = _fotos_de(db_session, liq["id"])
    _invariante(liq, fotos, "precio promedio")
    print("\n===== 16. EL PRECIO PROMEDIO =====")
    print(f"  ${D(liq['valor_transporte'])} / {D(liq['total_litros'])} L = "
          f"{D(liq['valor_transporte']) / D(liq['total_litros'])}")
    print(f"  el comprobante dice ${D(liq['precio_promedio'])} · al centavo del proyecto "
          f"${centavos(D(liq['valor_transporte']) / D(liq['total_litros']))}")
    assert D(liq["precio_promedio"]) == centavos(
        D(liq["valor_transporte"]) / D(liq["total_litros"])), (
        f"el precio promedio se redondeo con otro criterio: dice "
        f"${D(liq['precio_promedio'])} y al centavo del proyecto (medio para arriba) es "
        f"${centavos(D(liq['valor_transporte']) / D(liq['total_litros']))}")


# ===========================================================================
# 17. EL ÚNICO CAMPO QUE EL CÓDIGO PROMETE QUE ES SEGURO: LAS OBSERVACIONES
# ===========================================================================
def test_qa_escribir_una_observacion_mueve_el_flete_pagado(client, base_datos, db_session):
    """recepcion/service.py lo dice con estas palabras: "Lo que NO traba nadie: la
    sucursal y las observaciones. Son el único dato de clasificación y la anotación
    libre; no entran en ninguna liquidación. Así, con las DOS liquidaciones pagadas
    todavía se puede dejar escrito qué pasó ese día."

    Se hace exactamente eso: flete PAGADO, y se deja escrita una nota. El flete de
    ese día se reescribe con la tarifa nueva de la ruta.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    rec = _recibir(client, h, esc, "2026-07-16", "Aurelio", "137.45")
    liq_id = _liquidar(client, h)["id"]
    pagada = _pagar(client, h, liq_id)
    assert D(pagada["valor_transporte"]) == D("33367.36")

    _cambiar_tarifa(client, h, esc, "napoles", "242.75")  # un centavo menos
    r = client.put(f"{RECEPCIONES}/{rec['id']}",
                   json={"observaciones": "llego tarde por el derrumbe"}, headers=h)
    print("\n===== 17. SOLO UNA OBSERVACION =====")
    print(f"  respuesta {r.status_code}")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    fotos, _ = _fotos_de(db_session, liq_id)
    liq = _leer(client, h, liq_id)
    print(f"  el flete del dia: era $33367.36, quedo ${D(r.json()['valor_transporte'])}")
    print(f"  comprobante PAGADO ${D(liq['valor_transporte'])} · fotos ${fotos} · "
          f"descuadre ${fotos - D(liq['valor_transporte'])}")
    assert D(r.json()["valor_transporte"]) == D("33367.36"), (
        f"dejar escrita una observacion movio el flete de un dia PAGADO: de $33367.36 a "
        f"${D(r.json()['valor_transporte'])} (137,45 L x $242,75 en vez de x $242,76)")


# ===========================================================================
# 18. UNA RUTA BORRADA DEJA AL TRANSPORTADOR IMPOSIBLE DE EDITAR
# ===========================================================================
def test_qa_ruta_borrada_traba_la_edicion_del_transportador(client, base_datos):
    """Se borra la ruta Mira Valle (soft delete). El transportador le sigue teniendo
    su tarifa colgada y `TransportadorRead.rutas` la sigue mostrando, pero volver a
    guardar el transportador con la lista que el API acaba de devolver rebota: la
    ruta borrada no pasa la validación. Queda un transportador que no se puede
    editar sin adivinar cuál fila quitar.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    borrada = client.delete(f"{RUTAS}/{esc['mira_valle']['id']}", headers=h)
    print("\n===== 18. RUTA BORRADA =====")
    print(f"  borrar la ruta Mira Valle: {borrada.status_code}")
    if borrada.status_code not in (200, 204):
        print(f"  no se dejo borrar, nada que revisar: {borrada.text[:120]}")
        return

    quedo = client.get(f"{TRANSPORTADORES}/{esc['alex']['id']}", headers=h).json()
    print(f"  el API sigue devolviendo {len(quedo['rutas'])} rutas: "
          f"{[(r['nombre'], r['valor_transporte']) for r in quedo['rutas']]}")
    reenvio = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={
        "telefono": "3110000000",
        "rutas": [{"ruta_id": r["ruta_id"], "valor_transporte": r["valor_transporte"]}
                  for r in quedo["rutas"]]}, headers=h)
    print(f"  reenviar esa MISMA lista: {reenvio.status_code} "
          f"{reenvio.json().get('error', {}).get('detail', '')[:110]}")
    assert reenvio.status_code == 200, (
        "el transportador quedo imposible de editar: el API devuelve una ruta borrada en "
        "`rutas` y despues rechaza esa misma lista")


# ===========================================================================
# 19. CUATRO PROVEEDORES EN LA MISMA RUTA EL MISMO DÍA: CUATRO RENGLONES
# ===========================================================================
def test_qa_cuatro_proveedores_en_una_ruta_parten_el_dia_en_cuatro_renglones(
    client, base_datos, db_session
):
    """Nápoles con cuatro proveedores el 16/07, todos a $242,76:

      44,23 L x $242,76 = $ 10.737,2748  ->  $ 10.737,27
      82,48 L x $242,76 = $ 20.022,8448  ->  $ 20.022,84
     137,23 L x $242,76 = $ 33.313,9548  ->  $ 33.313,95
     103,73 L x $242,76 = $ 25.174,5948  ->  $ 25.174,59
                                             ------------
                                             $ 89.248,65

    y 367,67 L x $242,76 = $89.248,6692 -> $89.248,67: DOS centavos de diferencia
    entre redondear renglón por renglón y redondear el día. `_renglones_del_grupo`
    resuelve el descuadre partiendo el renglón una vez por recepción, así que el día
    sale en CUATRO líneas idénticas en fecha, ruta y tarifa.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    for i, litros in enumerate(("44.23", "82.48", "137.23", "103.73")):
        esc["prov"][f"P{i}"] = _crear(client, h, PROVEEDORES, {
            "nombre": f"Productor {i}", "vereda": "x", "precio_litro": "1800",
            "ruta_id": esc["napoles"]["id"]})
        _recibir(client, h, esc, "2026-07-16", f"P{i}", litros)

    liq = _leer(client, h, _liquidar(client, h)["id"])
    fotos, _ = _fotos_de(db_session, liq["id"])
    _invariante(liq, fotos, "cuatro en una ruta")
    print("\n===== 19. CUATRO PROVEEDORES EN NAPOLES =====")
    for d in _renglones(liq):
        print(f"    {d['fecha']}  {d['ruta_nombre']:<11}{D(d['litros']):>9} L x "
              f"${D(d['precio_litro']):>9} = ${D(d['valor']):>11}")
    a_mano = centavos(D("367.67") * NAPOLES)
    print(f"  el dueño suma: 367,67 L x $242,76 = ${a_mano}")
    print(f"  el comprobante dice ${D(liq['valor_transporte'])} · "
          f"diferencia ${a_mano - D(liq['valor_transporte'])}")
    assert len(liq["detalles"]) == 1, (
        f"un dia y una ruta a UNA sola tarifa salio en {len(liq['detalles'])} renglones "
        f"identicos en fecha, ruta y precio; y el cuadre a mano (367,67 L x $242,76 = "
        f"${a_mano}) se separa ${a_mano - D(liq['valor_transporte'])} del comprobante "
        f"(${D(liq['valor_transporte'])})")


# ===========================================================================
# 20. UNA TARIFA DE TRES DECIMALES: el flete se calcula con una cifra que la
#     columna no puede guardar
# ===========================================================================
def test_qa_tarifa_de_tres_decimales_calcula_el_flete_con_lo_que_no_se_guarda(
    client, base_datos, db_session
):
    """`TransportadorRutaIn.valor_transporte` es `Decimal = Field(ge=0)`: no limita
    los decimales. La columna es Numeric(12,2). Con $242,765:

      · el flete que se calcula HOY sale del Decimal en memoria ($242,765):
        82,48 L x $242,765 = $20.023,2572 -> foto de $20.023,26;
      · en producción (Postgres) la columna guarda $242,77, así que al releer la
        tarifa da 82,48 L x $242,77 = $20.023,6696 -> $20.023,67.

    $0,41 de diferencia en un solo día por un solo proveedor, y aparece SOLO cuando
    se vuelve a leer la tarifa: el comprobante cambia sin que nadie toque nada.
    OJO: pytest corre sobre SQLite, que NO recorta la escala, así que acá lo único
    que se demuestra es que el API acepta y devuelve tres decimales para una columna
    de dos. El recorte en sí hay que verlo en Postgres.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    r = client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", json={"rutas": [
        {"ruta_id": esc["napoles"]["id"], "valor_transporte": "242.765"},
        {"ruta_id": esc["mira_valle"]["id"], "valor_transporte": str(MIRA_VALLE)},
    ]}, headers=h)
    print("\n===== 20. TARIFA DE TRES DECIMALES =====")
    print(f"  guardar $242,765 en una columna Numeric(12,2): {r.status_code}")
    if r.status_code != 200:
        print(f"  rebotado: {r.json().get('error', {}).get('detail', '')[:120]}")
        return
    guardada = {x["nombre"]: x["valor_transporte"] for x in r.json()["rutas"]}
    print(f"  el API responde: {guardada}")
    rec = _recibir(client, h, esc, "2026-07-16", "Aurelio", "82.48")
    print(f"  82,48 L -> flete guardado ${D(rec['valor_transporte'])}")
    print(f"    con $242,765 seria ${centavos(D('82.48') * D('242.765'))}")
    print(f"    con $242,77  seria ${centavos(D('82.48') * D('242.77'))}")
    assert D(guardada["Napoles"]) == centavos(D("242.765")), (
        f"la tarifa se acepto con tres decimales y el API la devuelve como "
        f"${guardada['Napoles']}, pero la columna es Numeric(12,2): en Postgres queda "
        f"$242,77 y el flete que se acaba de calcular "
        f"(${D(rec['valor_transporte'])}) no se va a poder reproducir")
