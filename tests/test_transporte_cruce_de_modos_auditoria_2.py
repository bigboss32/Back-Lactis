"""SEGUNDA VUELTA DE AUDITORÍA AL CRUCE DE MODOS. Nació para medir; hoy es la red.

Ataques que la matriz que ya existe NO cubre, todos midiendo LAS TRES CIFRAS a la vez
—el comprobante emitido, la suma de las fotos de sus recepciones y lo que imprime el
PDF— contra la cuenta hecha a mano:

  · el 7.º camino del candado (bonificaciones) con el FLETE pagado: responde 200 porque
    es un campo de la LECHE; lo que hay que exigir es que no le mueva un peso al flete;
  · apagar una recepción de un papel FIJO cuando hoy la ruta es POR LITRO: la foto de la
    apagada;
  · apagar TODAS y volver a prender, y apagar y prender LA ÚNICA: ¿el papel se
    re-precifica sin Recalcular?
  · mover la ruta a una que el papel NO cobraba, con el modo de hoy cruzado, y sacar y
    devolver el único día por sus tres caminos (la ruta, la fecha y el transportador);
  · leche anotada tarde en los dos cruces;
  · borrar una recepción con los modos cruzados;
  · quitar y devolver el transportador (la vuelta al viaje ya cobrado) cruzado;
  · el reparto de centavos de un fijo emitido entre 7 proveedores, con el modo cruzado;
  · Recalcular dos y tres veces seguidas (idempotencia);
  · dos queseras a la vez.

LO QUE ESTE ARCHIVO ENCONTRÓ, y quedó cerrado: el papel se re-precificaba solo cuando se
quedaba SIN NINGÚN RENGLÓN de una ruta —apagando y prendiendo el único día de esa ruta, o
cambiándole la ruta y devolviéndosela—. $150.000,00 amanecían en $19.906,32 (u82 L ×
$242,76) y al revés: $130.093,68 para el conductor, sin que nadie oprimiera Recalcular. Se
cerró GUARDANDO cómo cobró cada ruta en el momento de emitir el papel, en vez de deducirlo
de los renglones que sobrevivan (ver `LiquidacionRuta` y
tests/test_transporte_memoria_del_papel.py).

Y DOS COSAS QUE ESTE ARCHIVO MIDIÓ Y NO ERAN DEFECTOS, con su porqué escrito en cada
prueba, porque son las dos que se confunden con lo de arriba:

  · SACARLE LA FECHA DEL PERÍODO o CAMBIARLE EL TRANSPORTADOR le SUELTAN el día al
    comprobante a propósito, y devolverlo no se lo vuelve a pegar: el papel queda vacío y
    el día vuelve suelto, pendiente del siguiente Generar. El control con el mismo modo
    hace exactamente lo mismo, o sea que no lo trae el cruce: es la semántica de soltar,
    igual desde antes de que el día fijo existiera. Ver `SUELTAN_EL_DIA`;
  · LA FOTO DE UNA RECEPCIÓN APAGADA se deriva con la tarifa de HOY —cero en día fijo,
    litros × tarifa por litro— porque un día apagado no compone ningún renglón y ningún
    comprobante la suma. Ver `test_apagar_una_recepcion_del_papel_con_el_modo_cruzado`.
"""
import uuid as _uuid

import pytest
from sqlalchemy import select

from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers
from tests.test_transporte_cruce_de_modos_auditoria import (
    CERO,
    DIA_FIJO,
    EL_DIA,
    FIJO,
    GENERAL_LITRO,
    LITRO,
    LIQUIDACIONES,
    NAPOLES,
    OTRO_DIA,
    POR_LITRO,
    RECEPCIONES,
    D,
    Papel,
    cent,
    cuadra,
    escenario,
    liquidar,
    pdf_de,
    poner,
    poner_tarifas,
    recibir,
    sueltas_de,
)

EMITIDO_LITRO = cent(D("219.45") * POR_LITRO)  # 82,00 + 137,45 = 219,45 L


def todas_las_fotos(db, esc=None):
    """TODAS las fotos del flete, vivas y apagadas, sueltas y liquidadas."""
    db.expire_all()
    return {
        r.id: (str(r.fecha), D(r.cantidad_litros), D(r.valor_transporte), r.estado,
               r.liquidacion_transporte_id)
        for r in db.scalars(
            select(RecepcionLeche).where(RecepcionLeche.deleted_at.is_(None))
        ).all()
    }


# ===========================================================================
# 1. EL 7.º CAMINO DEL CANDADO: bonificaciones con el FLETE pagado
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
@pytest.mark.parametrize("estado", ["pagado", "con abono"])
def test_bonificaciones_con_el_flete_pagado_no_le_mueve_un_peso_al_flete(
        client, base_datos, db_session, emitido, hoy, estado):
    """`bonificaciones` es campo de la LECHE: con el FLETE pagado responde 200.

    Eso es correcto (la leche no está pagada). Lo que NO puede pasar es que ese guardado
    le mueva un peso al comprobante del flete ya pagado, ni con el modo cruzado.
    """
    h = auth_headers(client, "admin.a")
    suf = f" b7{emitido[0]}{hoy[0]}{estado[0]}"
    esc = escenario(client, h, suf, modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    liq_id = liq["id"]
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    if estado == "pagado":
        assert client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h).status_code == 200
    else:
        r = client.post(f"{LIQUIDACIONES}/{liq_id}/pagos", headers=h,
                        json={"fecha": EL_DIA, "valor": "1000"})
        assert r.status_code in (200, 201), r.text
    antes = Papel(db_session, liq_id)
    fotos_antes = todas_las_fotos(db_session)
    pdf_antes = pdf_de(client, h, liq_id)
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    r = poner(client, h, a["id"], bonificaciones="500")
    print(f"\n  {emitido}->{hoy} / {estado}: bonificaciones -> {r.status_code}")
    assert r.status_code == 200, (
        "bonificaciones es campo de la leche y la leche no esta pagada: deberia pasar")
    despues = Papel(db_session, liq_id)
    print(f"    flete antes  {antes}")
    print(f"    flete despues{despues}")
    assert antes.total == despues.total, (
        f"corregir bonificaciones movio el flete PAGADO de ${antes.total} a "
        f"${despues.total}")
    assert antes.renglones == despues.renglones, "se movieron los renglones del flete pagado"
    assert todas_las_fotos(db_session) == fotos_antes, (
        "se movio alguna foto del flete con el comprobante pagado")
    assert pdf_de(client, h, liq_id) == pdf_antes, "cambio el PDF del flete pagado"


# ===========================================================================
# 2. APAGAR CON LOS MODOS CRUZADOS: la foto de la apagada
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(DIA_FIJO, LITRO), (LITRO, DIA_FIJO)])
def test_apagar_una_recepcion_del_papel_con_el_modo_cruzado(
        client, base_datos, db_session, emitido, hoy):
    """Apagar un día que está en el papel, con el modo de hoy al revés del emitido.

    LO QUE MANDA, Y ES LO PRIMERO QUE SE MIDE: el papel no se re-precifica. Emitido por
    DÍA COMPLETO sigue valiendo $150.000 con Aurelio apagado —el viaje se hizo y cuesta lo
    mismo—, y emitido POR LITRO se vuelve a derivar de los litros que quedaron
    (137,45 L × $242,76). `cuadra` exige además la regla de oro: los renglones suman exacto
    el total y exacto lo que suman las fotos VIVAS.

    LA FOTO QUE LE QUEDA A LA APAGADA SE DERIVA CON LA TARIFA DE HOY, no con la del papel,
    y eso es deliberado: una recepción apagada NO COMPONE NINGÚN RENGLÓN, así que el
    comprobante no la mira —el recuadre solo recorre las activas— y no puede ponerle foto
    ni aunque quisiera. Está decidido y explicado en `_hay_que_rederivar_el_flete`
    (recepcion/service.py). Con la tarifa de hoy eso da:

      · en DÍA FIJO, $0,00 — en un fijo la recepción no tiene cifra propia, solo una PARTE
        del día, y a un día apagado no le toca ninguna. Es lo que evita el fijo fantasma de
        $150.000 colgado en una fila apagada;
      · POR LITRO, litros × tarifa — que es lo que ese día vale suelto, y es la cuenta de
        siempre.

    POR QUÉ NO SE LE EXIGE $0,00 EN LOS DOS MODOS, que era lo que esta prueba pedía: esa
    cifra NO ES PLATA DE NINGÚN DOCUMENTO. Ni el comprobante del flete ni el del proveedor
    suman las apagadas —los dos filtran `estado == 'activo'`, ver `_recepciones_de` y
    `_recepciones_transporte_de`—, así que ningún desglose se mueve por ella; y en el
    momento en que el día se vuelve a prender, el recuadre la rehace con LA MEMORIA del
    papel (medido en tests/test_transporte_memoria_del_papel.py). Exigirle cero también al
    modo POR LITRO sería cambiarle la cuenta al por litro, que es justo la que no se puede
    mover ni un centavo.

    Y AL FINAL SE PRENDE OTRA VEZ, que es lo que de verdad protege la plata: el papel tiene
    que volver, al peso, a la cifra con que se emitió.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" ap{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    inicial = cuadra(db_session, liq["id"], f"emitido {emitido}")
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    assert poner(client, h, a["id"], estado="inactivo").status_code == 200
    p = cuadra(db_session, liq["id"], f"apagada Aurelio ({emitido}->{hoy})")
    foto_apagada = D(db_session.get(RecepcionLeche, _uuid.UUID(a["id"]))
                     .valor_transporte)
    print(f"      emitido ${inicial.total} -> ${p.total}; foto de la APAGADA ${foto_apagada}")
    esperado = FIJO if emitido == DIA_FIJO else cent(D("137.45") * POR_LITRO)
    assert p.total == esperado, (
        f"apagar un dia dejo el papel {emitido} en ${p.total}; por su regla vale ${esperado}")
    # La foto de la apagada, con la tarifa de HOY: cero en día fijo, litros × tarifa por
    # litro. Ver el docstring para el porqué de que no sea cero en los dos.
    esperada_apagada = CERO if hoy == DIA_FIJO else cent(D("82.00") * POR_LITRO)
    assert foto_apagada == esperada_apagada, (
        f"la recepcion APAGADA quedo con una foto de ${foto_apagada}; con la tarifa de HOY "
        f"({hoy}) vale ${esperada_apagada}")
    # Y LO QUE PROTEGE LA PLATA: prenderla otra vez devuelve el papel emitido, al peso.
    assert poner(client, h, a["id"], estado="activo").status_code == 200
    q = cuadra(db_session, liq["id"], "prendida otra vez")
    assert q.total == inicial.total and q.modos == inicial.modos, (
        f"prender el dia otra vez dejo el papel en ${q.total} {q.modos}; se emitio en "
        f"${inicial.total} {inicial.modos}")


@pytest.mark.parametrize("emitido,hoy", [(DIA_FIJO, LITRO), (LITRO, DIA_FIJO)])
def test_apagar_todas_y_volver_a_prender_no_reprecifica_el_papel(
        client, base_datos, db_session, emitido, hoy):
    """Apagar las DOS y volver a prenderlas: el papel tiene que volver a lo emitido.

    En medio el comprobante se queda sin renglones (correcto: no hay viaje). Lo que se
    exige es que al prenderlas otra vez NO se re-precifique con la tarifa de hoy, que es
    lo que hace Recalcular y que el dueño oprime a propósito.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" at{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    b = recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    inicial = cuadra(db_session, liq["id"], f"emitido {emitido}")
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    for quien in (a, b):
        assert poner(client, h, quien["id"], estado="inactivo").status_code == 200
    vacio = cuadra(db_session, liq["id"], "las dos apagadas")
    assert vacio.total == CERO, f"sin dias vivos el papel vale ${vacio.total}"
    for quien in (a, b):
        assert poner(client, h, quien["id"], estado="activo").status_code == 200
    p = cuadra(db_session, liq["id"], "las dos prendidas otra vez")
    print(f"      emitido ${inicial.total} -> vacio ${vacio.total} -> ${p.total} "
          f"(modos {p.modos})")
    assert p.total == inicial.total, (
        f"apagar las dos y volver a prenderlas re-precifico el papel: de ${inicial.total} "
        f"({emitido}) quedo en ${p.total} ({p.modos}), que es la tarifa de HOY ({hoy}) "
        "sin que nadie oprimiera Recalcular")
    assert p.modos == [emitido], f"el papel cambio de forma: {p.modos}"


# ===========================================================================
# 3. MOVER LA RUTA A UNA QUE EL PAPEL NO COBRABA
# ===========================================================================
def test_mover_la_ruta_a_una_que_el_papel_no_cobraba_e_inyecta_un_fijo(
        client, base_datos, db_session):
    """Papel emitido POR LITRO en 'A fábrica'; Nápoles hoy es DÍA FIJO y no está en él.

    Se le corrige la ruta a un día y el papel emitido gana un renglón "Día completo"
    entero sin que nadie oprimiera Recalcular. Se mide cuánto.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " mr1", modo_fabrica=LITRO)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    inicial = cuadra(db_session, liq["id"], "emitido por litro (solo A fabrica)")
    assert inicial.total == EMITIDO_LITRO == D("53273.68")
    poner_tarifas(client, h, esc, modo_fabrica=LITRO, modo_napoles=DIA_FIJO,
                  valor_napoles=D("90000"))
    assert poner(client, h, a["id"], ruta_id=esc["napoles"]["id"]).status_code == 200
    p = cuadra(db_session, liq["id"], "Aurelio se muda a Napoles (fijo hoy)")
    print(f"      ${inicial.total} -> ${p.total}  (delta ${p.total - inicial.total})")
    impreso = pdf_de(client, h, liq["id"])
    for f, _, li, pr, v, m, yc in p.renglones:
        if m == LITRO:
            assert cent(li * pr) == v, f"linea por litro no verificable: {li}x{pr}!={v}"
        assert v > CERO or yc, f"linea en ${v} sin marca de ya cobrado"
    assert "Día completo" in impreso or p.modos == [LITRO]
    # No se afirma cuál es la respuesta correcta: se deja la cifra escrita.
    assert p.total == D("33367.36") + D("90000"), (
        f"la cuenta esperada era Marleny 137,45 L x $242,76 = $33.367,36 mas el fijo de "
        f"Napoles $90.000; el papel dice ${p.total}")


# ===========================================================================
# 4. LECHE ANOTADA TARDE CON EL MODO CRUZADO
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
def test_leche_anotada_tarde_en_un_dia_ya_cobrado_con_el_modo_cruzado(
        client, base_datos, db_session, emitido, hoy):
    """Se anota tarde la leche de un día que ya está cobrado, con el modo cruzado.

    Por FIJO ese viaje ya se pagó y la leche nueva entra en $0,00. Por LITRO cada litro
    se paga aparte y la leche nueva SÍ suma flete. Lo que no puede pasar es que el mismo
    viaje quede cobrado dos veces.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" lt{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    inicial = cuadra(db_session, liq["id"], f"emitido {emitido}")
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    g = recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    db_session.expire_all()
    tarde = db_session.get(RecepcionLeche, _uuid.UUID(g["id"]))
    foto = D(tarde.valor_transporte)
    p = cuadra(db_session, liq["id"], "tras la leche anotada tarde")
    print(f"      papel ${inicial.total} -> ${p.total}; la anotada tarde (96,30 L) "
          f"quedo en ${foto}")
    assert p.total == inicial.total, (
        f"anotar leche tarde movio el comprobante ya emitido de ${inicial.total} a "
        f"${p.total}")
    if hoy == DIA_FIJO:
        assert foto == CERO, (
            f"el viaje del 16/07 ya esta cobrado en un papel (${inicial.total}) y la leche "
            f"anotada tarde entro con flete de ${foto}: el mismo viaje quedaria cobrado "
            f"${inicial.total + foto}")
    else:
        assert foto == cent(D("96.30") * POR_LITRO), (
            f"por litro la leche nueva se paga aparte: 96,30 L x $242,76 = "
            f"${cent(D('96.30') * POR_LITRO)}, y quedo en ${foto}")


# ===========================================================================
# 5. BORRAR UNA RECEPCIÓN CON LOS MODOS CRUZADOS
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
def test_borrar_una_recepcion_del_papel_con_el_modo_cruzado(
        client, base_datos, db_session, emitido, hoy):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" br{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    inicial = cuadra(db_session, liq["id"], f"emitido {emitido}")
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    assert client.delete(f"{RECEPCIONES}/{a['id']}", headers=h).status_code in (200, 204)
    p = cuadra(db_session, liq["id"], f"borrada Aurelio ({emitido}->{hoy})")
    esperado = FIJO if emitido == DIA_FIJO else cent(D("137.45") * POR_LITRO)
    print(f"      ${inicial.total} -> ${p.total} (esperado ${esperado})")
    assert p.total == esperado, (
        f"borrar un dia dejo el papel {emitido} en ${p.total}; por su regla vale "
        f"${esperado}")
    assert p.modos == [emitido], f"el papel cambio de forma: {p.modos}"
    for f, _, li, pr, v, m, yc in p.renglones:
        if m == LITRO:
            assert cent(li * pr) == v, f"linea no verificable: {li} x {pr} != {v}"


# ===========================================================================
# 6. QUITAR Y DEVOLVER EL TRANSPORTADOR, CRUZADO
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
def test_quitar_y_devolver_el_transportador_con_el_modo_cruzado(
        client, base_datos, db_session, emitido, hoy):
    """Se le cambia el transportador a un día y se lo devuelven: el papel vuelve igual."""
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" tr{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    inicial = cuadra(db_session, liq["id"], f"emitido {emitido}")
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    assert poner(client, h, a["id"], transportador_id=esc["beto"]["id"]).status_code == 200
    medio = cuadra(db_session, liq["id"], "Aurelio se fue con Beto")
    assert poner(client, h, a["id"], transportador_id=esc["alex"]["id"]).status_code == 200
    p = cuadra(db_session, liq["id"], "Aurelio volvio con Alex")
    print(f"      ${inicial.total} -> ${medio.total} -> ${p.total}")
    assert p.total == inicial.total, (
        f"quitar y devolver el transportador dejo el papel en ${p.total}; estaba en "
        f"${inicial.total}")
    assert p.fotos == inicial.fotos, (
        "las fotos no volvieron a como estaban: "
        f"{sorted(inicial.fotos.values())} -> {sorted(p.fotos.values())}")
    db_session.expire_all()
    assert not [r for r in sueltas_de(db_session, esc)
                if r.transportador_id == _uuid.UUID(esc["alex"]["id"])], (
        "quedo una recepcion de Alex suelta despues de devolverla al viaje")


# ===========================================================================
# 7. EL REPARTO DE CENTAVOS DE UN FIJO EMITIDO, CON EL MODO CRUZADO
# ===========================================================================
def test_reparto_al_centavo_de_un_fijo_emitido_cuando_hoy_la_ruta_es_por_litro(
        client, base_datos, db_session):
    """$150.000 emitidos entre 4 proveedores; hoy la ruta es POR LITRO.

    Se le corrige los litros a uno: el renglón sigue en $150.000 (lo pactado) y las
    cuatro fotos tienen que sumar EXACTO $150.000. Un centavo es un defecto.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " ct1", modo_fabrica=DIA_FIJO)
    ids = {}
    for quien, litros in (("Aurelio", "33.33"), ("Marleny", "33.33"),
                          ("Gilberto", "33.33"), ("Rosa", "0.01")):
        ids[quien] = recibir(client, h, esc, EL_DIA, quien, litros)
    liq = liquidar(client, h, esc)
    p = cuadra(db_session, liq["id"], "emitido fijo entre 4")
    assert p.total == FIJO, p.total
    poner_tarifas(client, h, esc, modo_fabrica=LITRO)
    assert poner(client, h, ids["Aurelio"]["id"], cantidad_litros="99.99").status_code == 200
    q = cuadra(db_session, liq["id"], "fijo->litro + corregir litros")
    partes = sorted(v for _, v, e in q.fotos.values() if e == "activo")
    print(f"      renglon ${q.total}; fotos {partes} suman ${sum(partes, CERO)}")
    assert q.total == FIJO, (
        f"el renglon fijo emitido quedo en ${q.total}; el conductor cobro ${FIJO}")
    assert sum(partes, CERO) == FIJO, (
        f"el desglose no cuadra: las fotos suman ${sum(partes, CERO)} y el renglon dice "
        f"${FIJO}")
    impreso = pdf_de(client, h, liq["id"])
    assert "Día completo" in impreso, "el papel fijo dejo de decir 'Día completo'"
    assert "150.000" in impreso, "el papel fijo dejo de imprimir los $150.000"


# ===========================================================================
# 8. RECALCULAR VARIAS VECES SEGUIDAS: IDEMPOTENCIA
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
def test_recalcular_tres_veces_con_el_modo_cruzado_da_siempre_lo_mismo(
        client, base_datos, db_session, emitido, hoy):
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" rc{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    recibir(client, h, esc, OTRO_DIA, "Gilberto", "96.30")
    liq = liquidar(client, h, esc)
    cuadra(db_session, liq["id"], f"emitido {emitido}")
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    assert poner(client, h, a["id"], cantidad_litros="91.30").status_code == 200
    cuadra(db_session, liq["id"], "corregido, sin recalcular")
    huellas = []
    for vuelta in (1, 2, 3):
        r = client.post(f"{LIQUIDACIONES}/{liq['id']}/recalcular", headers=h)
        assert r.status_code == 200, r.text
        p = cuadra(db_session, liq["id"], f"RECALCULAR #{vuelta}")
        huellas.append((p.total, sorted(p.renglones), sorted(p.fotos.values())))
    assert huellas[0] == huellas[1] == huellas[2], (
        "Recalcular no es idempotente con los modos cruzados: "
        f"{[h_[0] for h_ in huellas]}")
    p = Papel(db_session, liq["id"])
    assert p.modos == [hoy], f"Recalcular dejo {p.modos} y hoy la ruta es '{hoy}'"
    esperado = (FIJO * 2 if hoy == DIA_FIJO
                else cent(D("228.75") * POR_LITRO) + cent(D("96.30") * POR_LITRO))
    assert p.total == esperado, f"Recalcular dio ${p.total}, a mano da ${esperado}"


# ===========================================================================
# 9. EL DÍA SIN RUTA Y LA RUTA, EL MISMO DÍA, CON LOS MODOS CRUZADOS
# ===========================================================================
def test_el_dia_sin_ruta_y_la_ruta_el_mismo_dia_con_los_modos_cruzados(
        client, base_datos, db_session):
    """Dos grupos el mismo día: uno con ruta 'A fábrica' y otro SIN ruta (la general)."""
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " sr2", modo_fabrica=DIA_FIJO, modo_general=LITRO)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    g = recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    assert poner(client, h, g["id"], ruta_id=None).status_code == 200
    liq = liquidar(client, h, esc)
    p = cuadra(db_session, liq["id"], "emitido: fabrica fija + sin ruta por litro")
    esperado = FIJO + cent(D("96.30") * GENERAL_LITRO)
    assert p.total == esperado, f"${p.total} != ${esperado}"
    # se cruzan los dos: fabrica a litro, general a fijo
    poner_tarifas(client, h, esc, modo_fabrica=LITRO, modo_general=DIA_FIJO)
    assert poner(client, h, a["id"], cantidad_litros="91.30").status_code == 200
    q = cuadra(db_session, liq["id"], "los dos modos cruzados + corregir")
    print(f"      ${p.total} -> ${q.total}")
    assert q.total == esperado, (
        f"cruzar los dos modos movio el papel emitido de ${esperado} a ${q.total}")
    assert sorted(q.modos) == sorted({DIA_FIJO, LITRO}), q.modos
    impreso = pdf_de(client, h, liq["id"])
    for f, _, li, pr, v, m, yc in q.renglones:
        assert v > CERO or yc, f"linea del {f} en ${v} sin marca de ya cobrado"
        if m == LITRO:
            assert cent(li * pr) == v, f"linea no verificable: {li} x {pr} != {v}"
    assert "Día completo" in impreso


# ===========================================================================
# 10. DOS QUESERAS A LA VEZ
# ===========================================================================
def test_las_dos_queseras_con_los_modos_cruzados_no_se_pisan(client, base_datos, db_session):
    """La misma historia en A y en B, con el cruce al revés en cada una."""
    resultados = {}
    for usuario, emitido, hoy in (("admin.a", LITRO, DIA_FIJO), ("admin.b", DIA_FIJO, LITRO)):
        h = auth_headers(client, usuario)
        esc = escenario(client, h, f" 2q{usuario[-1]}", modo_fabrica=emitido)
        a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
        recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
        liq = liquidar(client, h, esc)
        inicial = cuadra(db_session, liq["id"], f"{usuario} emitido {emitido}")
        poner_tarifas(client, h, esc, modo_fabrica=hoy)
        assert poner(client, h, a["id"], cantidad_litros="91.30").status_code == 200
        p = cuadra(db_session, liq["id"], f"{usuario} corregido")
        resultados[usuario] = (inicial.total, p.total, p.modos)
    print(f"\n  {resultados}")
    # A: emitida por litro, sigue por litro con los litros de hoy
    assert resultados["admin.a"][1] == cent(D("228.75") * POR_LITRO)
    assert resultados["admin.a"][2] == [LITRO]
    # B: emitida fija, sigue valiendo el fijo pactado
    assert resultados["admin.b"][1] == FIJO
    assert resultados["admin.b"][2] == [DIA_FIJO]


# ===========================================================================
# 11. EL MODO CAMBIADO ANTES Y DESPUÉS, CON RECALCULAR EN MEDIO
# ===========================================================================
def test_cambiar_el_modo_recalcular_cambiarlo_otra_vez_y_corregir(
        client, base_datos, db_session):
    """litro -> fijo -> RECALCULAR -> litro -> corregir un dato.

    Después de Recalcular el papel está EMITIDO EN FIJO. Volver la ruta a por litro y
    corregir un dato no puede re-precificarlo otra vez.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " mx1", modo_fabrica=LITRO)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    cuadra(db_session, liq["id"], "emitido por litro")
    poner_tarifas(client, h, esc, modo_fabrica=DIA_FIJO)
    assert client.post(f"{LIQUIDACIONES}/{liq['id']}/recalcular",
                       headers=h).status_code == 200
    fijo = cuadra(db_session, liq["id"], "RECALCULAR -> fijo")
    assert fijo.total == FIJO and fijo.modos == [DIA_FIJO], str(fijo)
    poner_tarifas(client, h, esc, modo_fabrica=LITRO)
    assert poner(client, h, a["id"], cantidad_litros="91.30").status_code == 200
    p = cuadra(db_session, liq["id"], "vuelta a litro + corregir")
    print(f"      ${fijo.total} -> ${p.total} ({p.modos})")
    assert p.modos == [DIA_FIJO], f"el papel recalculado en fijo cambio de forma: {p.modos}"
    assert p.total == FIJO, (
        f"el papel que Recalcular dejo en ${FIJO} (dia fijo) quedo en ${p.total} por "
        "corregirle los litros a un dia")


# ===========================================================================
# 12. UNA RECEPCIÓN QUE SE VA A OTRA QUINCENA Y VUELVE, CON DOS PAPELES
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(LITRO, DIA_FIJO), (DIA_FIJO, LITRO)])
def test_dos_papeles_del_mismo_transportador_y_un_dia_que_salta_entre_ellos(
        client, base_datos, db_session, emitido, hoy):
    """Dos quincenas liquidadas del mismo transportador; un día salta de una a otra."""
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" 2p{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    # quincena 1 (01-15) y quincena 2 (16-31)
    v = recibir(client, h, esc, "2026-07-10", "Gilberto", "50.00")
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq1 = liquidar(client, h, esc, inicio="2026-07-01", fin="2026-07-15")
    liq2 = liquidar(client, h, esc, inicio="2026-07-16", fin="2026-07-31")
    p1 = cuadra(db_session, liq1["id"], f"Q1 emitida {emitido}")
    p2 = cuadra(db_session, liq2["id"], f"Q2 emitida {emitido}")
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    # Aurelio se va al 10/07: sale de Q2 y cae en el dia que Q1 ya cobra
    assert poner(client, h, a["id"], fecha="2026-07-10").status_code == 200
    q1 = cuadra(db_session, liq1["id"], "Q1 con Aurelio adentro")
    q2 = cuadra(db_session, liq2["id"], "Q2 sin Aurelio")
    print(f"      Q1 ${p1.total} -> ${q1.total} ; Q2 ${p2.total} -> ${q2.total}")
    # el viaje del 10/07 no puede cobrarse dos veces
    esperado_q1 = FIJO if emitido == DIA_FIJO else cent(D("132.00") * POR_LITRO)
    assert q1.total == esperado_q1, (
        f"Q1 quedo en ${q1.total}; con {emitido} y 50,00+82,00 L vale ${esperado_q1}")
    # y de vuelta
    assert poner(client, h, a["id"], fecha=EL_DIA).status_code == 200
    r1 = cuadra(db_session, liq1["id"], "Q1 sin Aurelio otra vez")
    r2 = cuadra(db_session, liq2["id"], "Q2 con Aurelio otra vez")
    assert r1.total == p1.total, f"Q1 no volvio: ${p1.total} -> ${r1.total}"
    assert r2.total == p2.total, f"Q2 no volvio: ${p2.total} -> ${r2.total}"
    assert r1.fotos == p1.fotos and r2.fotos == p2.fotos, "las fotos no volvieron"
    del v


# ===========================================================================
# 13. EL AGUJERO MEDIDO CON CIFRAS SNAPSHOT (no con objetos vivos)
# ===========================================================================
def _snap(db, liq_id):
    """Cifras COPIADAS, no un objeto vivo: Papel lee del ORM y expire_all lo refresca."""
    p = Papel(db, liq_id)
    return {
        "total": D(p.total),
        "renglones": sorted((str(f), str(li), str(pr), str(v), m, yc)
                            for f, _, li, pr, v, m, yc in p.renglones),
        "modos": list(p.modos),
        "fotos": sorted((str(li), str(v), e) for li, v, e in p.fotos.values()),
    }


@pytest.mark.parametrize("estado_papel", ["borrador", "aprobada"])
@pytest.mark.parametrize("emitido,hoy", [(DIA_FIJO, LITRO), (LITRO, DIA_FIJO)])
def test_UNA_recepcion_apagada_y_prendida_NO_reprecifica_el_papel_emitido(
        client, base_datos, db_session, emitido, hoy, estado_papel):
    """UN día, UNA recepción: apagarla y prenderla NO re-precifica el papel.

    ERA EL CAMINO MÁS CORTO DEL AGUJERO, y es la primera de las dos puertas: al apagar la
    última recepción viva de un (día, ruta) el renglón DESAPARECE —correcto, ese viaje ya no
    existe— y con él se iba la memoria de cómo se había emitido, porque el modo y la tarifa
    de la ruta se DEDUCÍAN de los renglones que sobrevivieran. Al prenderla otra vez el
    recuadre no tenía qué conservar y preguntaba la tarifa de HOY: $150.000,00 quedaban en
    $19.906,32 (u$82 L × $242,76), o al revés, sin que nadie hubiera oprimido Recalcular.

    Ya no: el comprobante GUARDA cómo cobró cada ruta cuando se emite
    (`LiquidacionRuta`), así que no hay nada que deducir y el día que vuelve nace como ese
    papel lo cobraba.

    EL SELLO DEL ESTADO SÍ CAMBIA CUANDO EL PAPEL ESTABA APROBADO, y es correcto: le
    apagaron y le prendieron un día, así que el visto bueno hay que darlo otra vez y el
    recuadre lo devuelve a BORRADOR (ver `recuadrar`). Por eso el PDF se compara por su
    DETALLE DIARIO —los renglones, que es lo que el conductor verifica— y no byte por byte:
    lo que no puede cambiar es la plata y la forma de cada línea, no el sello de si ya está
    aprobado.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" u1{emitido[0]}{hoy[0]}{estado_papel[0]}",
                    modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    liq = liquidar(client, h, esc)
    liq_id = liq["id"]
    if estado_papel == "aprobada":
        assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    antes = _snap(db_session, liq_id)
    pdf_antes = pdf_de(client, h, liq_id)
    esperado_emitido = FIJO if emitido == DIA_FIJO else cent(D("82.00") * POR_LITRO)
    assert antes["total"] == esperado_emitido, antes
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    assert poner(client, h, a["id"], estado="inactivo").status_code == 200
    vacio = _snap(db_session, liq_id)
    assert poner(client, h, a["id"], estado="activo").status_code == 200
    despues = _snap(db_session, liq_id)
    print(f"\n  {emitido}->{hoy} / {estado_papel}")
    print(f"    emitido  ${antes['total']}  {antes['modos']}  {antes['renglones']}")
    print(f"    apagada  ${vacio['total']}  {vacio['modos']}")
    print(f"    prendida ${despues['total']}  {despues['modos']}  {despues['renglones']}")
    print(f"    delta    ${despues['total'] - antes['total']}")
    assert despues["modos"] == antes["modos"], (
        f"el papel emitido en '{emitido}' salio en '{despues['modos']}' despues de "
        "apagar y prender un dia: cambio de forma sin Recalcular")
    assert despues["total"] == antes["total"], (
        f"apagar y prender un dia re-precifico el papel: ${antes['total']} -> "
        f"${despues['total']} (delta ${despues['total'] - antes['total']}), con la tarifa "
        f"de HOY ({hoy}) y sin que nadie oprimiera Recalcular")
    def _detalle(texto):
        """El detalle diario del PDF: las líneas que el conductor verifica una por una."""
        return texto.split("Detalle diario", 1)[-1].split("Resumen", 1)[0]

    pdf_despues = pdf_de(client, h, liq_id)
    print(f"    detalle del PDF: {_detalle(pdf_despues)}")
    assert _detalle(pdf_despues) == _detalle(pdf_antes), (
        "cambio el detalle diario del PDF del papel emitido")
    if estado_papel == "aprobada":
        # Lo único que cambia, y tiene que cambiar: el visto bueno se pide otra vez.
        assert "BORRADOR" in pdf_despues and "APROBADA" in pdf_antes, (
            "un papel aprobado al que le tocan los dias tiene que volver a borrador")


def test_otro_dia_vivo_de_la_misma_ruta_salva_la_memoria_del_papel(
        client, base_datos, db_session):
    """El mismo ataque pero con OTRO día de la misma ruta vivo en el papel.

    Sirve para acotar el agujero: si al papel le queda algún renglón de esa ruta, el día
    que vuelve hereda el modo de ese renglón (`_ComoCobroLaRuta`) y el papel no se
    re-precifica. El agujero es quedarse SIN ningún renglón de la ruta.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " sm1", modo_fabrica=LITRO)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, OTRO_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    antes = _snap(db_session, liq["id"])
    poner_tarifas(client, h, esc, modo_fabrica=DIA_FIJO)
    assert poner(client, h, a["id"], estado="inactivo").status_code == 200
    assert poner(client, h, a["id"], estado="activo").status_code == 200
    despues = _snap(db_session, liq["id"])
    print(f"\n  con otro dia vivo: ${antes['total']} -> ${despues['total']} "
          f"{despues['modos']}")
    assert despues["modos"] == [LITRO], despues["modos"]
    assert despues["total"] == antes["total"], (
        f"${antes['total']} -> ${despues['total']}")


# ===========================================================================
# 14. LOS OTROS CAMINOS QUE DEJAN AL PAPEL SIN RENGLÓN DE ESA RUTA
# ===========================================================================
# Los tres caminos borran el único renglón del papel, pero NO son la misma cosa, y la
# diferencia es de quién queda dueño de ese día:
#
#   · CAMBIARLE LA RUTA no le suelta la marca del comprobante (`liquidacion_transporte_id`):
#     el día sigue siendo de ese papel, solo cambió de grupo. Devolvérsela tiene que
#     devolver la cifra emitida al peso, y es LA SEGUNDA PUERTA del defecto que se cerró
#     guardando la memoria de cada ruta (ver `LiquidacionRuta`);
#   · SACARLE LA FECHA DEL PERÍODO o CAMBIARLE EL TRANSPORTADOR sí le SUELTAN la marca, a
#     propósito: ese día dejó de pertenecerle a ese comprobante —se lo llevó otra quincena u
#     otro conductor— y el papel se queda sin renglones. Devolverlo NO se lo vuelve a pegar:
#     vuelve SUELTO, pendiente, y lo recoge el siguiente Generar con la tarifa de hoy. No es
#     el cruce de modos: el control con el mismo modo hace exactamente lo mismo (ver
#     `test_control_los_mismos_caminos_sin_tocar_el_modo`), y es la semántica de soltar, que
#     es igual desde antes de que el día fijo existiera.
SUELTAN_EL_DIA = ("fecha fuera y vuelve", "transportador y vuelve")


@pytest.mark.parametrize("emitido,hoy", [(DIA_FIJO, LITRO), (LITRO, DIA_FIJO)])
@pytest.mark.parametrize("camino", ["fecha fuera y vuelve", "ruta y vuelve",
                                    "transportador y vuelve"])
def test_sacar_la_unica_recepcion_del_papel_y_devolverla(
        client, base_datos, db_session, emitido, hoy, camino):
    """El papel tiene UN día con UNA recepción. Se la saca y se la devuelve.

    Los tres caminos borran el único renglón del papel, y lo que se exige al devolverla no
    es el mismo en los tres: ver el bloque de arriba (`SUELTAN_EL_DIA`). En una línea:
    devolverle LA RUTA devuelve la cifra emitida al peso; devolverle la FECHA o el
    TRANSPORTADOR deja el día suelto y el papel vacío, que es lo que significa haberle
    quitado ese día a ese conductor.

    LO QUE NINGUNO DE LOS TRES PUEDE HACER, y es lo que se mide al final: perder plata. El
    día que queda suelto tiene que quedar PENDIENTE, o sea que el siguiente Generar le saca
    comprobante; un día sin marca y sin papel sería una entrega de leche que nadie le paga
    al conductor.
    """
    h = auth_headers(client, "admin.a")
    suf = f" sc{emitido[0]}{hoy[0]}{camino[0]}{camino[-1]}"
    esc = escenario(client, h, suf, modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    liq = liquidar(client, h, esc)
    liq_id = liq["id"]
    antes = _snap(db_session, liq_id)
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    if camino == "fecha fuera y vuelve":
        ida, vuelta = dict(fecha="2026-07-02"), dict(fecha=EL_DIA)
    elif camino == "ruta y vuelve":
        ida = dict(ruta_id=esc["napoles"]["id"])
        vuelta = dict(ruta_id=esc["fabrica"]["id"])
    else:
        ida = dict(transportador_id=esc["beto"]["id"])
        vuelta = dict(transportador_id=esc["alex"]["id"])
    assert poner(client, h, a["id"], **ida).status_code == 200
    medio = _snap(db_session, liq_id)
    assert poner(client, h, a["id"], **vuelta).status_code == 200
    despues = _snap(db_session, liq_id)
    db_session.expire_all()
    fila = db_session.get(RecepcionLeche, _uuid.UUID(a["id"]))
    print(f"\n  {camino} / {emitido}->{hoy}")
    print(f"    emitido  ${antes['total']} {antes['modos']}")
    print(f"    afuera   ${medio['total']} {medio['modos']}")
    print(f"    devuelta ${despues['total']} {despues['modos']}  "
          f"foto ${fila.valor_transporte}  en el papel: "
          f"{fila.liquidacion_transporte_id is not None}")
    print(f"    delta    ${despues['total'] - antes['total']}")
    if camino in SUELTAN_EL_DIA:
        # El día dejó de ser de ese papel: vuelve SUELTO y el papel se queda vacío.
        assert fila.liquidacion_transporte_id is None, (
            f"'{camino}' le solto la marca del comprobante al dia, asi que devolverlo no se "
            "lo puede volver a pegar")
        assert despues["total"] == CERO and despues["renglones"] == [], (
            f"'{camino}' le solto el unico dia al papel, asi que el papel queda sin "
            f"renglones y en $0,00; quedo en ${despues['total']} {despues['renglones']}")
        # Y LA PLATA NO SE PIERDE: el día suelto lo recoge el siguiente Generar. Primero
        # hay que ANULAR el papel vacío, y no es un tropiezo de la prueba: es el mismo
        # guardia del cruce de períodos que ya existía —dos liquidaciones montadas una sobre
        # la otra dejan sin cobrar lo que el tercero quedó debiendo— y es exactamente lo que
        # el dueño hace con un comprobante que se quedó sin días.
        assert client.post(
            f"{LIQUIDACIONES}/{liq_id}/anular", headers=h).status_code == 200
        otro = liquidar(client, h, esc)
        nuevo = _snap(db_session, otro["id"])
        esperado_hoy = FIJO if hoy == DIA_FIJO else cent(D("82.00") * POR_LITRO)
        print(f"    el dia suelto se vuelve a liquidar: ${nuevo['total']} {nuevo['modos']} "
              f"(con la tarifa de hoy vale ${esperado_hoy})")
        assert nuevo["total"] == esperado_hoy, (
            f"el dia que quedo suelto se volvio a liquidar en ${nuevo['total']} y con la "
            f"tarifa de hoy ({hoy}) vale ${esperado_hoy}")
        return
    assert despues["total"] == antes["total"] and despues["modos"] == antes["modos"], (
        f"'{camino}' dejo el papel emitido en '{emitido}' por ${antes['total']} en "
        f"${despues['total']} ({despues['modos']}), delta "
        f"${despues['total'] - antes['total']}")


def test_la_foto_de_la_apagada_no_la_suma_ningun_comprobante(
        client, base_datos, db_session):
    """La foto que le queda a la apagada NO la suma el comprobante del proveedor.

    Era la sospecha de esta prueba y hay que descartarla midiéndola, porque si fuera cierta
    sí sería plata mal puesta en un documento: papel de flete emitido DÍA FIJO, hoy la ruta
    es POR LITRO, se apaga el día. El renglón del transportador sigue en $150.000 y la
    apagada se queda con una foto por litro ($19.906,32) que no compone ningún renglón.

    LO QUE SE MIDE ES SI ESA CIFRA APARECE EN ALGÚN LADO, y no aparece: la columna
    informativa `valor_transporte` de la liquidación del PROVEEDOR es la suma de las fotos
    de sus días ACTIVOS (`_recepciones_de` filtra `estado == 'activo'`), así que el día
    apagado no entra —ni con su foto vieja ni con la nueva—. El comprobante del proveedor
    de Aurelio, que es el dueño del día apagado, sale sin un peso de flete.

    Y la foto se rehace en el momento en que el día se prende otra vez, con la memoria del
    papel; ver `test_apagar_una_recepcion_del_papel_con_el_modo_cruzado`.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, " fp1", modo_fabrica=DIA_FIJO)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq = liquidar(client, h, esc)
    p = _snap(db_session, liq["id"])
    assert p["total"] == FIJO
    poner_tarifas(client, h, esc, modo_fabrica=LITRO)
    assert poner(client, h, a["id"], estado="inactivo").status_code == 200
    db_session.expire_all()
    fila = db_session.get(RecepcionLeche, _uuid.UUID(a["id"]))
    q = _snap(db_session, liq["id"])
    print(f"\n  renglon del transportador ${q['total']} ; foto de la APAGADA "
          f"${fila.valor_transporte} (82,00 L x $242,76 = "
          f"${cent(D('82.00') * POR_LITRO)})")
    fantasma = cent(D("82.00") * POR_LITRO)
    assert D(fila.valor_transporte) == fantasma, (
        f"la apagada quedo con ${fila.valor_transporte}; con la tarifa de HOY (por litro) "
        f"vale ${fantasma}")
    assert q["total"] == FIJO, "el papel de flete sigue valiendo el dia completo"

    # Y AHORA LA PREGUNTA QUE IMPORTA: ¿esa cifra llega al comprobante del PROVEEDOR?
    r = client.post(f"{LIQUIDACIONES}/generar", headers=h, json={
        "periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31", "tipo": "proveedor"})
    assert r.status_code in (200, 201), r.text
    de_aurelio = [
        g for g in r.json()["generadas"]
        if g.get("proveedor_id") == esc["provs"]["Aurelio"]["id"]
    ]
    print(f"    comprobantes de proveedor generados: {len(r.json()['generadas'])}; "
          f"de Aurelio (el del dia apagado): {len(de_aurelio)}")
    for g in de_aurelio:
        print(f"      flete en la columna del proveedor: ${g['valor_transporte']}")
        assert D(g["valor_transporte"]) == CERO, (
            f"el comprobante del proveedor se llevo ${g['valor_transporte']} de flete de un "
            f"dia APAGADO: la foto fantasma de ${fantasma} llego a un documento")


# ===========================================================================
# 15. EL CONTROL: LOS MISMOS CAMINOS SIN CRUZAR EL MODO (viejo vs nuevo)
# ===========================================================================
@pytest.mark.parametrize("modo", [LITRO, DIA_FIJO])
@pytest.mark.parametrize("camino", ["apagar y prender", "ruta y vuelve",
                                    "fecha fuera y vuelve", "transportador y vuelve"])
def test_control_los_mismos_caminos_sin_tocar_el_modo(
        client, base_datos, db_session, modo, camino):
    """EL CONTROL. Los mismos caminos SIN cambiarle el modo a nada.

    Sirve para saber qué es del cruce de modos y qué no: lo que acá se comporta igual que
    allá no lo trajo el cruce.

    Y ES LO QUE SEPARA LOS CUATRO CAMINOS EN DOS GRUPOS. Apagar y prender, y cambiarle la
    ruta y devolverla, dejan el papel EXACTO en los dos escenarios: el día nunca dejó de
    ser de ese comprobante. Sacarle la fecha del período o cambiarle el transportador le
    SUELTAN la marca —el día se lo llevó otra quincena u otro conductor— y el papel se
    queda vacío en los dos escenarios, con el mismo modo y con el modo cruzado: o sea que
    eso no es el cruce de modos, es la semántica de soltar, y es la misma desde antes de que
    el día fijo existiera. Ver el bloque `SUELTAN_EL_DIA`.
    """
    h = auth_headers(client, "admin.a")
    suf = f" ct{modo[0]}{camino[0]}{camino[-1]}"
    esc = escenario(client, h, suf, modo_fabrica=modo)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    liq = liquidar(client, h, esc)
    antes = _snap(db_session, liq["id"])
    pasos = {
        "apagar y prender": (dict(estado="inactivo"), dict(estado="activo")),
        "ruta y vuelve": (dict(ruta_id=esc["napoles"]["id"]),
                          dict(ruta_id=esc["fabrica"]["id"])),
        "fecha fuera y vuelve": (dict(fecha="2026-07-02"), dict(fecha=EL_DIA)),
        "transportador y vuelve": (dict(transportador_id=esc["beto"]["id"]),
                                   dict(transportador_id=esc["alex"]["id"])),
    }[camino]
    for cuerpo in pasos:
        assert poner(client, h, a["id"], **cuerpo).status_code == 200
    despues = _snap(db_session, liq["id"])
    print(f"\n  CONTROL {modo} / {camino}: ${antes['total']} {antes['modos']} -> "
          f"${despues['total']} {despues['modos']} (delta "
          f"${despues['total'] - antes['total']})")
    if camino in SUELTAN_EL_DIA:
        # Igual que con el modo cruzado: el papel se queda vacío porque el día ya no es
        # suyo. Que pase idéntico acá es la prueba de que no lo trae el cruce.
        assert despues["total"] == CERO and despues["renglones"] == [], (
            f"'{camino}' le solta la marca al dia, asi que el papel queda vacio tambien sin "
            f"cruzar el modo; quedo en ${despues['total']} {despues['renglones']}")
        return
    assert despues["total"] == antes["total"] and despues["modos"] == antes["modos"], (
        f"SIN cruzar el modo, '{camino}' movio el papel de ${antes['total']} a "
        f"${despues['total']}")


# ===========================================================================
# 16. LA PLATA QUE DE VERDAD SALE DE LA CAJA
# ===========================================================================
@pytest.mark.parametrize("emitido,hoy", [(DIA_FIJO, LITRO), (LITRO, DIA_FIJO)])
def test_al_conductor_se_le_paga_lo_reprecificado_y_el_pdf_lo_imprime(
        client, base_datos, db_session, emitido, hoy):
    """Hasta el final: aprobar, re-precificar sin querer, PAGAR y leer el PDF.

    Mide la plata que sale de la caja contra la que el comprobante decía el día que se
    aprobó, y deja escrito lo que el PDF le imprime al conductor.
    """
    h = auth_headers(client, "admin.a")
    esc = escenario(client, h, f" pl{emitido[0]}{hoy[0]}", modo_fabrica=emitido)
    a = recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    liq = liquidar(client, h, esc)
    liq_id = liq["id"]
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    aprobado = _snap(db_session, liq_id)
    pdf_aprobado = pdf_de(client, h, liq_id)
    print(f"\n  APROBADO ${aprobado['total']} {aprobado['modos']}")
    # el dueño le cambia el modo a la ruta y alguien apaga y prende el día
    poner_tarifas(client, h, esc, modo_fabrica=hoy)
    assert poner(client, h, a["id"], estado="inactivo").status_code == 200
    assert poner(client, h, a["id"], estado="activo").status_code == 200
    listo = _snap(db_session, liq_id)
    db_session.expire_all()
    estado = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()["estado"]
    print(f"  estado tras apagar y prender: {estado}")
    if estado != "aprobada":
        assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar",
                           headers=h).status_code == 200
    r = client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    pagado = _snap(db_session, liq_id)
    pdf_pagado = pdf_de(client, h, liq_id)
    linea = pdf_pagado.split("Detalle diario", 1)[-1].split("Resumen", 1)[0].strip()
    print(f"  antes de pagar ${listo['total']} {listo['modos']}")
    print(f"  PAGADO         ${pagado['total']}")
    print(f"  PDF aprobado: {pdf_aprobado.split('Detalle diario', 1)[-1].split('Resumen', 1)[0].strip()}")
    print(f"  PDF pagado  : {linea}")
    assert pagado["total"] == aprobado["total"], (
        f"al conductor se le pago ${pagado['total']} y el comprobante que se le aprobo "
        f"decia ${aprobado['total']}: diferencia de "
        f"${pagado['total'] - aprobado['total']}")
