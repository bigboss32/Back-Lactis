"""AUDITORÍA DEL GUARDIA DEL PERÍODO QUE SE PISA — ¿tumba la corrida de la quincena?

DOS MITADES:
  A. que el guardia NO rebote una generación legítima (lo que el dueño hace todos los
     días). Si rebota algo de eso, le tumba la corrida de la quincena.
  B. que la plata siga cuadrando (la cuenta del dueño), medida con el mismo libro que
     ya se usó en los 45 escenarios anteriores.

Se importa el instrumental de tests/test_liquidacion_deuda_arrastrada_plata.py para medir
con la MISMA regla.

EL CONTRATO QUE MIDE ESTE ARCHIVO, y por qué cambió. Este archivo se escribió MIDIENDO el
comportamiento de antes, y lo que midió fue que UN SOLO TERCERO con un período cruzado
TUMBABA la corrida de la quincena entera, dejando sin comprobante a los que no tenían nada
que ver. Lo midió en cuatro formas, con estas cifras:

  · por el lado de la leche, $720.000 de dos proveedoras que nunca tuvieron liquidación de
    ese período (b3b), y la corrida completa rebotando también en el caso de a uno (b3);
  · por el lado del flete, $1.080.000 de tres proveedores, que es peor porque un
    transportador recoge la leche de MUCHOS y el flete va primero en la corrida (b6), y lo
    mismo pidiendo de a un proveedor con tipo="ambos", que es el valor por omisión (a2c);
  · y aparte, un punto ciego del guardia que caía justo sobre las filas que la migración
    `e5c2b9a1f7d3` dice que hay en la base del cliente —'pagada' con saldo negativo—, por
    donde salían $500.000 por $380.000 de leche (b5).

TODO ESO YA ESTÁ ARREGLADO. `POST /liquidaciones/generar` devuelve ahora un sobre
—{"generadas": [...], "omitidas": [{tipo, cuenta, tercero_id, tercero_nombre, motivo,
motivo_codigo}]}— y al tercero del cruce SE LO SALTA Y LO REPORTA en vez de rebotar; y la
fila vieja con saldo negativo sí reserva sus fechas. El porqué está en
`_omitido_por_periodo_cruzado` y en `LiquidacionRepository.solapada_para_periodo`.

Así que las pruebas que documentaban el defecto están DADAS LA VUELTA: exigen la garantía
nueva —los sanos salen liquidados, el del cruce sale reportado— conservando las MISMAS
cifras con que se midió el hueco. Están marcadas con «GARANTÍA» en el nombre.

El ataque completo al salto —que la corrida no se tumbe por ningún camino, que la plata
cuadre y que el salto no deje basura en la base— está en
tests/test_liquidacion_corrida_omitidos_adversarial.py.
"""
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_arrastrada_plata import (
    API,
    CERO,
    D,
    Q1,
    Q2,
    Q3,
    Q4,
    Q5,
    _anticipo,
    _anular,
    _aprobar,
    _cuadra,
    _detalle,
    _generar,
    _leer,
    _libro,
    _pagar,
    _pdf,
    _proveedor,
    _recepcion,
    _recepciones,
    _ruta,
    _todas,
    _transportador,
    renglon,
)


def _pedir(client, h, inicio, fin, tipo="proveedor", proveedor_id=None):
    cuerpo = {"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo}
    if proveedor_id:
        cuerpo["proveedor_id"] = proveedor_id
    return client.post(f"{API}/generar", json=cuerpo, headers=h)


def _corrida(client, h, inicio, fin, tipo="proveedor", proveedor_id=None):
    """La corrida COMPLETA: {"generadas": [...], "omitidas": [...]}, exigiendo el 200.

    La corrida NO SE TUMBA NUNCA por un cruce: al tercero se lo salta y lo reporta. Si
    esto devuelve cualquier cosa distinta de 200, es que al dueño le rebotaron la quincena
    y no le puede pagar a nadie ese día.
    """
    r = _pedir(client, h, inicio, fin, tipo, proveedor_id)
    assert r.status_code == 200, (
        f"LA CORRIDA DE LA QUINCENA REBOTÓ ({inicio} al {fin}, tipo={tipo}): "
        f"{r.status_code} — {_detalle(r)}"
    )
    return r.json()


def _ok(client, h, inicio, fin, tipo="proveedor", proveedor_id=None):
    """Las liquidaciones que SALIERON, exigiendo que no se quedara NADIE por fuera.

    Dos exigencias, y las dos son la prueba:
      · la corrida no rebota (lo mira `_corrida`);
      · y `omitidas` viene VACÍA. Eso último es a propósito: ahora que un tercero se puede
        saltar sin tumbar la corrida, una prueba que solo cuente las generadas dejaría
        pasar un omitido en silencio —justo el hecho que hay que ver—. Quien SÍ espera
        omitidos usa `_corrida` y los asserta con nombre y motivo.
    """
    corrida = _corrida(client, h, inicio, fin, tipo, proveedor_id)
    assert corrida["omitidas"] == [], (
        f"la corrida ({inicio} al {fin}, tipo={tipo}) dejó terceros SIN COMPROBANTE: "
        + "; ".join(
            f"{o['tercero_nombre']} ({o['cuenta']}, {o['motivo_codigo']})"
            for o in corrida["omitidas"]
        )
    )
    return corrida["generadas"]


def _un_omitido(corrida):
    """El único omitido de la corrida, exigiendo que sea uno solo y no dos."""
    assert len(corrida["omitidas"]) == 1, corrida["omitidas"]
    return corrida["omitidas"][0]


# ===========================================================================
# MITAD A — LO QUE EL DUEÑO HACE TODOS LOS DÍAS TIENE QUE SEGUIR SALIENDO
# ===========================================================================
def test_a1_la_corrida_de_la_quincena_para_ocho_terceros_a_la_vez(client, base_datos):
    """LA CORRIDA NORMAL: un solo Generar del período, ocho proveedores, dos quincenas.

    Es el caso que más costaría: si el guardia rebota acá, el dueño no puede liquidar
    la quincena de nadie.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== A1. OCHO TERCEROS, UN SOLO GENERAR =====")
    provs = [_proveedor(client, h, f"Prov {i}") for i in range(8)]
    for i, p in enumerate(provs):
        _recepcion(client, h, p, "2026-06-02", str(90 + i))
        _recepcion(client, h, p, "2026-06-20", str(80 + i), precio="2000")
    # Anticipos a los pares, para que la mitad quede debiendo en Q1.
    for p in provs[::2]:
        _anticipo(client, h, "2026-06-01", "300000", proveedor=p)

    g1 = _ok(client, h, *Q1)
    print(f"      Q1 -> {len(g1)} liquidaciones")
    assert len(g1) == 8
    _cuadra(_libro(client, h, "Q1 de ocho"), "A1 Q1")

    g2 = _ok(client, h, *Q2)
    print(f"      Q2 -> {len(g2)} liquidaciones")
    assert len(g2) == 8
    # Los cuatro que quedaron debiendo en Q1 tienen su deuda cobrada en Q2.
    cobrando = [liq for liq in g2 if D(liq["saldo_anterior"]) > CERO]
    print(f"      cobran deuda de atrás: {len(cobrando)}")
    assert len(cobrando) == 4
    _cuadra(_libro(client, h, "Q1+Q2 de ocho"), "A1 Q2")


def test_a2_de_a_un_proveedor_uno_por_uno_sobre_el_mismo_periodo(client, base_datos):
    """DE A UNO, por proveedor_id, sobre el MISMO período — con los tres tipos.

    El filtro de la pantalla: el dueño liquida a Henri, revisa, liquida a Marleny…
    Ninguno de los ya liquidados puede estorbar al siguiente.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== A2. DE A UNO SOBRE EL MISMO PERÍODO =====")
    provs = [_proveedor(client, h, f"Prov {i}") for i in range(5)]
    for p in provs:
        _recepcion(client, h, p, "2026-06-02", "100")
    for i, p in enumerate(provs):
        salieron = _ok(client, h, *Q1, proveedor_id=p["id"])
        print(f"      {i}: {p['nombre']} -> {len(salieron)}")
        assert len(salieron) == 1
        assert salieron[0]["proveedor_id"] == p["id"]
    assert len([liq for liq in _todas(client, h) if liq["estado"] != "anulada"]) == 5
    _cuadra(_libro(client, h, "cinco de a uno"), "A2")


def test_a2b_de_a_un_proveedor_con_tipo_ambos_que_es_el_valor_por_omision(
    client, base_datos
):
    """DE A UNO CON tipo="ambos", que es EL VALOR POR OMISIÓN del endpoint.

    `proveedor_id` solo filtra la mitad de proveedor: el flete se genera para TODOS.
    Así que la primera llamada de a uno ya crea la de flete del transportador, y las
    siguientes tienen que pasar por encima de ella sin rebotar.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== A2b. DE A UNO CON tipo=ambos =====")
    ruta = _ruta(client, h, "Napoles")
    t = _transportador(client, h, "Don Chucho", [(ruta, "200")])
    provs = [_proveedor(client, h, f"Prov {i}") for i in range(4)]
    for p in provs:
        _recepcion(client, h, p, "2026-06-02", "100", t=t, ruta=ruta)

    for i, p in enumerate(provs):
        salieron = _ok(client, h, *Q1, tipo="ambos", proveedor_id=p["id"])
        tipos = sorted(liq["tipo"] for liq in salieron)
        print(f"      {i}: {p['nombre']} -> {tipos}")
    vivas = [liq for liq in _todas(client, h) if liq["estado"] != "anulada"]
    de_flete = [liq for liq in vivas if liq["tipo"] == "transportador"]
    de_leche = [liq for liq in vivas if liq["tipo"] == "proveedor"]
    print(f"      total: {len(de_leche)} de leche, {len(de_flete)} de flete")
    assert len(de_leche) == 4
    assert len(de_flete) == 1
    _cuadra(_libro(client, h, "de a uno con ambos"), "A2b")


def test_a2c_GARANTIA_de_a_uno_con_ambos_no_lo_tumba_el_transportador(client, base_datos):
    """GARANTÍA: de a uno con tipo="ambos" (EL VALOR POR OMISIÓN) la leche del proveedor
    que el dueño pidió SÍ SALE, aunque el flete del mismo período se cruce.

    EL HALLAZGO QUE ESTA PRUEBA MIDIÓ, y que ya está arreglado: `proveedor_id` NO filtra la
    mitad del flete, así que la primera llamada le crea la liquidación de flete a Don Chucho
    por todo el período. Cuando entra el día de leche de Marleny —que Don Chucho también
    trajo—, la segunda llamada vuelve a encontrar flete pendiente del MISMO transportador en
    el MISMO período. Antes eso REBOTABA la llamada completa: el dueño pedía "liquidar a
    Marleny", la pantalla le contestaba hablándole de Don Chucho y la leche de Marleny se
    quedaba sin comprobante.

    AHORA la leche de Marleny sale y el flete se REPORTA en `omitidas` con su motivo: el
    dueño ve las dos cosas —el comprobante que pidió y el aviso de lo que le falta— en la
    misma respuesta, y no tiene que saber cambiarle el tipo a la petición para salir del
    atasco. Y el flete no se pierde: sus litros siguen pendientes y entran completos en
    cuanto se arregla el cruce (se mide abajo).
    """
    h = auth_headers(client, "admin.a")
    print("\n===== A2c. DE A UNO CON ambos: EL TRANSPORTADOR NO LA TUMBA =====")
    ruta = _ruta(client, h, "Napoles")
    t = _transportador(client, h, "Don Chucho", [(ruta, "200")])
    henri = _proveedor(client, h, "Henri C")
    marleny = _proveedor(client, h, "Marleny")
    _recepcion(client, h, henri, "2026-06-02", "100", t=t, ruta=ruta)

    primera = _ok(client, h, *Q1, tipo="ambos", proveedor_id=henri["id"])
    print(f"      Henri: {sorted(liq['tipo'] for liq in primera)}")
    assert sorted(liq["tipo"] for liq in primera) == ["proveedor", "transportador"]

    # Ahora se anota el día de Marleny, que TAMBIÉN lo trajo Don Chucho.
    _recepcion(client, h, marleny, "2026-06-03", "80", t=t, ruta=ruta)
    corrida = _corrida(client, h, *Q1, tipo="ambos", proveedor_id=marleny["id"])
    omitida = _un_omitido(corrida)
    print(f"      Marleny (tipo=ambos) -> generadas={len(corrida['generadas'])} "
          f"omitida: {omitida['tercero_nombre']} ({omitida['cuenta']})")

    # LA LECHE DE MARLENY SÍ SALIÓ: 80 L a $1.800.
    assert len(corrida["generadas"]) == 1
    salio = corrida["generadas"][0]
    assert salio["proveedor_id"] == marleny["id"]
    assert D(salio["valor_total"]) == D("144000")
    # Y EL FLETE SE REPORTA, con nombre, cuenta y motivo en código para la pantalla.
    assert (omitida["tercero_id"], omitida["cuenta"], omitida["motivo_codigo"]) == (
        t["id"], "flete", "periodo_cruzado"
    )
    assert "Don Chucho ya tiene una liquidación de flete" in omitida["motivo"]
    _cuadra(_libro(client, h, "la leche salió y el flete se reportó"), "A2c")

    # EL FLETE NO SE PERDIÓ: arreglando el cruce (anulando el de Don Chucho) entra
    # COMPLETO, los 180 L de los dos días a $200 = $36.000.
    flete_viejo = next(
        liq for liq in _todas(client, h) if liq["tipo"] == "transportador"
    )
    assert _anular(client, h, flete_viejo["id"]).status_code == 200
    rescatado = _ok(client, h, *Q1, tipo="ambos")
    print(f"      tras anular el flete -> {[liq['tipo'] for liq in rescatado]} "
          f"total={rescatado[0]['valor_total']}")
    assert len(rescatado) == 1 and rescatado[0]["tipo"] == "transportador"
    assert D(rescatado[0]["valor_total"]) == D("36000")
    libro = _libro(client, h, "el flete entró completo")
    _cuadra(libro, "A2c final")
    # $180.000 (Henri) + $144.000 (Marleny) + $36.000 de flete: ni un litro sin papel.
    assert libro["leche"] == D("360000")


def test_a3_regenerar_despues_de_anular_con_una_con_las_dos_y_con_varias_encima(
    client, base_datos
):
    """EL FLUJO DE CORRECCIÓN, en las tres formas en que el dueño lo hace."""
    h = auth_headers(client, "admin.a")
    print("\n===== A3. ANULAR Y REGENERAR =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    # (a) SOLO LA SEGUNDA: se anula y se regenera. Vuelve a cobrar la misma deuda.
    assert _anular(client, h, q2["id"]).status_code == 200
    q2b = _ok(client, h, *Q2)[0]
    print(f"      (a) Q2 regenerada: deuda_vieja={q2b['saldo_anterior']}")
    assert D(q2b["saldo_anterior"]) == D("120000")
    _cuadra(_libro(client, h, "(a) solo Q2 regenerada"), "A3a")

    # (b) LAS DOS: es el orden que recomienda el mensaje de error.
    assert _anular(client, h, q2b["id"]).status_code == 200
    assert _anular(client, h, q1["id"]).status_code == 200
    q1c = _ok(client, h, *Q1)[0]
    q2c = _ok(client, h, *Q2)[0]
    print(f"      (b) Q1={q1c['le_queda_debiendo']} deuda_vieja de Q2={q2c['saldo_anterior']}")
    assert D(q2c["saldo_anterior"]) == D("120000")
    _cuadra(_libro(client, h, "(b) las dos regeneradas"), "A3b")

    # (c) VARIAS ANULADAS ENCIMA: cuatro vueltas del mismo período.
    ultima = q2c
    for vuelta in range(4):
        assert _anular(client, h, ultima["id"]).status_code == 200
        ultima = _ok(client, h, *Q2)[0]
        print(f"      (c) vuelta {vuelta + 1}: deuda_vieja={ultima['saldo_anterior']}")
        assert D(ultima["saldo_anterior"]) == D("120000")
    anuladas = [liq for liq in _todas(client, h) if liq["estado"] == "anulada"]
    print(f"      anuladas acumuladas: {len(anuladas)}")
    _cuadra(_libro(client, h, "(c) con cinco anuladas encima"), "A3c")


def test_a4_leche_y_flete_del_mismo_tercero_en_el_mismo_periodo(client, base_datos):
    """LAS DOS CUENTAS DE LA MISMA PERSONA, en tres caminos: de una, y por separado en
    los dos órdenes. Son dos liquidaciones distintas y las dos tienen que existir."""
    h = auth_headers(client, "admin.a")
    print("\n===== A4. LECHE Y FLETE DEL MISMO TERCERO =====")
    ruta = _ruta(client, h, "Napoles")
    # Camino 1: de una, con tipo="ambos".
    t = _transportador(client, h, "Henri C", [(ruta, "200")])
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100", t=t, ruta=ruta)
    de_una = _ok(client, h, *Q1, tipo="ambos")
    print(f"      Q1 ambos -> {sorted(liq['tipo'] for liq in de_una)}")
    assert sorted(liq["tipo"] for liq in de_una) == ["proveedor", "transportador"]

    # Camino 2: primero el flete, después la leche (Q2).
    _recepcion(client, h, prov, "2026-06-20", "100", t=t, ruta=ruta)
    f2 = _ok(client, h, *Q2, tipo="transportador")
    l2 = _ok(client, h, *Q2, tipo="proveedor")
    print(f"      Q2 flete->leche: {len(f2)} + {len(l2)}")
    assert len(f2) == 1 and len(l2) == 1

    # Camino 3: primero la leche, después el flete (Q3).
    _recepcion(client, h, prov, "2026-07-02", "100", t=t, ruta=ruta)
    l3 = _ok(client, h, *Q3, tipo="proveedor")
    f3 = _ok(client, h, *Q3, tipo="transportador")
    print(f"      Q3 leche->flete: {len(l3)} + {len(f3)}")
    assert len(l3) == 1 and len(f3) == 1
    _cuadra(_libro(client, h, "las dos cuentas por tres caminos"), "A4")


def test_a5_periodos_que_se_tocan_por_un_dia_exacto_en_los_dos_ordenes(client, base_datos):
    """EL 15 Y EL 16: el borde exacto. `periodo_inicio <= fin AND periodo_fin >= inicio`
    NO se cumple cuando una termina el 15 y la otra empieza el 16, así que la quincena
    contigua tiene que pasar — en los DOS órdenes."""
    h = auth_headers(client, "admin.a")
    print("\n===== A5. EL 15 Y EL 16 =====")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-15", "100")
    _recepcion(client, h, henri, "2026-06-16", "100")
    a = _ok(client, h, "2026-06-01", "2026-06-15")[0]
    b = _ok(client, h, "2026-06-16", "2026-06-30")[0]
    print(f"      adelante: {a['periodo_fin']} luego {b['periodo_inicio']}")

    # Al revés: primero la de arriba, después la de abajo.
    marleny = _proveedor(client, h, "Marleny")
    _recepcion(client, h, marleny, "2026-06-15", "100")
    _recepcion(client, h, marleny, "2026-06-16", "100")
    c = _ok(client, h, "2026-06-16", "2026-06-30", proveedor_id=marleny["id"])[0]
    d = _ok(client, h, "2026-06-01", "2026-06-15", proveedor_id=marleny["id"])[0]
    print(f"      al revés: {c['periodo_inicio']} luego {d['periodo_fin']}")
    assert D(d["saldo_anterior"]) == CERO, "la deuda no viaja al pasado"
    _cuadra(_libro(client, h, "quincenas contiguas"), "A5")


def test_a6_periodos_de_un_solo_dia_seguidos(client, base_datos):
    """PERÍODOS DE UN SOLO DÍA, uno tras otro. inicio == fin: el rango más corto que hay,
    y donde un `<=` de más convertiría dos días seguidos en un cruce."""
    h = auth_headers(client, "admin.a")
    print("\n===== A6. PERÍODOS DE UN SOLO DÍA =====")
    prov = _proveedor(client, h, "Henri C")
    dias = [f"2026-06-{n:02d}" for n in range(1, 8)]
    for dia in dias:
        _recepcion(client, h, prov, dia, "50")
    for dia in dias:
        salieron = _ok(client, h, dia, dia)
        assert len(salieron) == 1, f"el día {dia} no salió"
        print(f"      {dia} -> total={salieron[0]['valor_total']} "
              f"deuda_vieja={salieron[0]['saldo_anterior']}")
    vivas = [liq for liq in _todas(client, h) if liq["estado"] != "anulada"]
    assert len(vivas) == 7
    # Y un rango de dos días que TOCA uno de esos días sí es un cruce: al tercero se lo
    # SALTA Y SE LO REPORTA —la corrida no rebota— y el día 08 se queda esperando.
    _recepcion(client, h, prov, "2026-06-08", "50")
    corrida = _corrida(client, h, "2026-06-07", "2026-06-08")
    omitida = _un_omitido(corrida)
    print(f"      07-08 sobre el día 07 ya generado -> generadas="
          f"{len(corrida['generadas'])}, omitida: {omitida['motivo'][:110]}")
    assert corrida["generadas"] == []
    assert (omitida["tercero_id"], omitida["cuenta"], omitida["motivo_codigo"]) == (
        prov["id"], "leche", "periodo_cruzado"
    )
    # El período de la que se cruza es el día suelto del 07, nombrado con sus dos fechas.
    assert "del 07/06/2026 al 07/06/2026" in omitida["motivo"], omitida["motivo"]
    # el día 08 sí sale solo
    solo08 = _ok(client, h, "2026-06-08", "2026-06-08")
    print(f"      08-08 solo -> {len(solo08)}")
    assert len(solo08) == 1
    _cuadra(_libro(client, h, "ocho dias sueltos"), "A6")


def test_a7_veinticuatro_quincenas_contiguas_de_todo_un_ano(client, base_datos):
    """UN AÑO ENTERO, quincena por quincena, con deuda arrastrándose. 24 corridas
    seguidas: si el guardia tuviera el borde corrido por un día, rebotaría en la 2."""
    h = auth_headers(client, "admin.a")
    print("\n===== A7. UN AÑO DE QUINCENAS =====")
    ultimos = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
               7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
    prov = _proveedor(client, h, "Henri C")
    quincenas = []
    for mes in range(1, 13):
        quincenas.append((f"2027-{mes:02d}-01", f"2027-{mes:02d}-15"))
        quincenas.append((f"2027-{mes:02d}-16", f"2027-{mes:02d}-{ultimos[mes]:02d}"))
    for inicio, fin in quincenas:
        _recepcion(client, h, prov, inicio, "100")
    # Un anticipo grande en la primera para que la deuda tenga que viajar.
    _anticipo(client, h, "2027-01-01", "300000", proveedor=prov)
    arrastres = 0
    for i, (inicio, fin) in enumerate(quincenas):
        salieron = _ok(client, h, inicio, fin)
        assert len(salieron) == 1, f"la quincena {inicio}..{fin} no salió"
        if D(salieron[0]["saldo_anterior"]) > CERO:
            arrastres += 1
            print(f"      {i + 1:2d}. {inicio}..{fin} cobró de atrás "
                  f"{salieron[0]['saldo_anterior']}")
    print(f"      24 quincenas generadas, {arrastres} con deuda arrastrada")
    assert len([liq for liq in _todas(client, h) if liq["estado"] != "anulada"]) == 24
    _cuadra(_libro(client, h, "un ano de quincenas"), "A7")


def test_a8_un_periodo_que_contiene_a_otro(client, base_datos):
    """EL PERÍODO QUE CONTIENE A OTRO (el mes completo sobre una quincena ya generada).

    Contener es cruzarse, así que al tercero SE LO SALTA Y LO REPORTA —la corrida no
    rebota— en los dos órdenes. Y lo que de verdad importa se mide igual: esa leche NO se
    queda sin camino, porque la quincena de arriba (16-30), que no se monta con nada, sí
    sale y se la lleva completa.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== A8. EL MES QUE CONTIENE A LA QUINCENA =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2000")
    q1 = _ok(client, h, *Q1)[0]
    print(f"      Q1 (01-15) generada: total={q1['valor_total']}")

    corrida = _corrida(client, h, "2026-06-01", "2026-06-30")
    omitida = _un_omitido(corrida)
    print(f"      el mes completo 01-30 -> generadas={len(corrida['generadas'])}, "
          f"omitida: {omitida['motivo'][:150]}")
    assert corrida["generadas"] == []
    assert (omitida["tercero_id"], omitida["motivo_codigo"]) == (
        prov["id"], "periodo_cruzado"
    )
    assert "del 01/06/2026 al 15/06/2026" in omitida["motivo"]

    # LO IMPORTANTE: el día 20 no puede quedarse sin camino. La quincena de arriba sí sale.
    q2 = _ok(client, h, *Q2)[0]
    print(f"      Q2 (16-30) sí sale: total={q2['valor_total']}")
    assert D(q2["valor_total"]) == D("200000")
    libro = _libro(client, h, "el mes omitido, la quincena sale")
    _cuadra(libro, "A8")
    assert libro["leche"] == D("380000"), "toda la leche quedó liquidada"
    # Y al contrario: el mes primero, y después la quincena de adentro. La contenida se
    # omite igual, nombrando el mes con el que se cruza.
    marleny = _proveedor(client, h, "Marleny")
    _recepcion(client, h, marleny, "2026-06-02", "100")
    _recepcion(client, h, marleny, "2026-06-20", "100")
    mes = _ok(client, h, "2026-06-01", "2026-06-30", proveedor_id=marleny["id"])[0]
    print(f"      Marleny: mes completo primero, total={mes['valor_total']}")
    _recepcion(client, h, marleny, "2026-06-05", "40")
    corrida = _corrida(client, h, *Q1, proveedor_id=marleny["id"])
    omitida = _un_omitido(corrida)
    print(f"      la quincena de adentro después -> generadas="
          f"{len(corrida['generadas'])}, omitida: {omitida['motivo'][:150]}")
    assert corrida["generadas"] == []
    assert omitida["tercero_id"] == marleny["id"]
    assert "del 01/06/2026 al 30/06/2026" in omitida["motivo"]
    _cuadra(_libro(client, h, "contenido en los dos ordenes"), "A8b")


def test_a9_un_tercero_sin_recepciones_y_un_periodo_sin_nada(client, base_datos):
    """EL QUE NO ENTREGÓ LECHE Y EL PERÍODO VACÍO: no hay nada que liquidar, y eso no es
    un error — el guardia no puede rebotar por alguien a quien no se le iba a generar."""
    h = auth_headers(client, "admin.a")
    print("\n===== A9. SIN RECEPCIONES Y PERÍODO VACÍO =====")
    henri = _proveedor(client, h, "Henri C")
    quieto = _proveedor(client, h, "El Quieto")
    _recepcion(client, h, henri, "2026-06-02", "100")
    # Un anticipo al que no entregó nada: plata suelta que no debe crear liquidación.
    _anticipo(client, h, "2026-06-01", "50000", proveedor=quieto)

    salieron = _ok(client, h, *Q1)
    print(f"      Q1 -> {len(salieron)} (solo el que entregó)")
    assert len(salieron) == 1
    assert salieron[0]["proveedor_id"] == henri["id"]

    # El que no entregó, pedido de a uno: no sale nada y no rebota.
    vacio = _ok(client, h, *Q1, proveedor_id=quieto["id"])
    print(f"      el quieto de a uno -> {len(vacio)}")
    assert vacio == []

    # Un período entero sin nada, y dos veces seguidas.
    for _ in range(2):
        nada = _ok(client, h, "2026-09-01", "2026-09-15", tipo="ambos")
        assert nada == []
    print("      período vacío dos veces -> []")
    libro = _libro(client, h, "sin recepciones")
    _cuadra(libro, "A9")
    assert libro["sueltos"] == D("50000"), "el anticipo del que no entregó sigue suelto"


def test_a10_el_cruce_es_por_empresa_y_no_traba_a_la_otra_quesera(client, base_datos):
    """EL CRUCE ES POR EMPRESA. Si se filtrara mal, la quincena de una quesera tumbaría
    la de la otra —dos queseras liquidan el mismo período todos los meses—."""
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    print("\n===== A10. EL CRUCE ES POR EMPRESA =====")
    pa = _proveedor(client, h_a, "Henri C")
    pb = _proveedor(client, h_b, "Henri C")
    _recepcion(client, h_a, pa, "2026-06-02", "100")
    _recepcion(client, h_b, pb, "2026-06-02", "100")
    la = _ok(client, h_a, *Q1)
    lb = _ok(client, h_b, *Q1)
    print(f"      Quesera A -> {len(la)}   Quesera B -> {len(lb)}")
    assert len(la) == 1 and len(lb) == 1
    _cuadra(_libro(client, h_a, "Quesera A"), "A10 A")
    _cuadra(_libro(client, h_b, "Quesera B"), "A10 B")


# ===========================================================================
# MITAD B — LA PLATA SIGUE CUADRANDO
# ===========================================================================
def test_b1_la_cadena_de_cinco_quincenas_cuadra_al_peso(client, base_datos):
    """LA CADENA DE CINCO, medida quincena por quincena con el guardia puesto."""
    h = auth_headers(client, "admin.a")
    print("\n===== B1. CINCO QUINCENAS =====")
    prov = _proveedor(client, h, "Henri C")
    plan = [
        (Q1, "2026-06-02", "100", "1800", "300000"),
        (Q2, "2026-06-20", "100", "2000", "250000"),
        (Q3, "2026-07-02", "120", "1900", "300000"),
        (Q4, "2026-07-20", "80", "2100", "100000"),
        (Q5, "2026-08-02", "150", "1700", "0"),
    ]
    for quincena, dia, litros, precio, anticipo in plan:
        _recepcion(client, h, prov, dia, litros, precio=precio)
        if anticipo != "0":
            _anticipo(client, h, dia, anticipo, proveedor=prov)
        liq = _ok(client, h, *quincena)[0]
        print(f"      {quincena[0]}..{quincena[1]}: total={liq['valor_total']} "
              f"anticipos={liq['anticipos']} deuda_vieja={liq['saldo_anterior']} "
              f"saldo={liq['saldo']}")
        _cuadra(_libro(client, h, f"tras {quincena[0]}"), f"B1 {quincena[0]}")
    libro = _libro(client, h, "las cinco")
    _cuadra(libro, "B1 final")
    # Y pagando todo lo que se pueda pagar, la cuenta sigue.
    for liq in list(libro["vivas"]):
        if D(liq["saldo"]) > CERO:
            assert _aprobar(client, h, liq["id"]).status_code == 200
            assert _pagar(client, h, liq["id"]).status_code == 200
    final = _libro(client, h, "las cinco, pagadas")
    _cuadra(final, "B1 pagadas")
    print(f"      SALIÓ DE LA CAJA {final['anticipos'] + final['pagado']} "
          f"POR {final['leche']} DE LECHE")


def test_b2_anular_y_regenerar_en_los_dos_ordenes_cuadra(client, base_datos):
    """ANULAR Y REGENERAR, en los DOS órdenes, midiendo en cada paso."""
    h = auth_headers(client, "admin.a")
    print("\n===== B2. LOS DOS ÓRDENES =====")
    for nombre, primero_la_vieja in (("Henri C", True), ("Marleny", False)):
        prov = _proveedor(client, h, nombre)
        _recepcion(client, h, prov, "2026-06-02", "100")
        _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
        q1 = _ok(client, h, *Q1, proveedor_id=prov["id"])[0]
        _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
        q2 = _ok(client, h, *Q2, proveedor_id=prov["id"])[0]
        assert D(q2["saldo_anterior"]) == D("120000")
        # Q1 está congelada: hay que anular Q2 primero, y el mensaje lo dice.
        r = _anular(client, h, q1["id"])
        print(f"      {nombre}: anular Q1 con la deuda cobrada -> {r.status_code}")
        assert r.status_code == 422
        assert _anular(client, h, q2["id"]).status_code == 200
        assert _anular(client, h, q1["id"]).status_code == 200
        _cuadra(_libro(client, h, f"{nombre}: las dos anuladas"), f"B2 {nombre} anuladas")
        orden = (Q1, Q2) if primero_la_vieja else (Q2, Q1)
        print(f"      {nombre}: regenerando {orden[0][0]} y luego {orden[1][0]}")
        for quincena in orden:
            liq = _ok(client, h, *quincena, proveedor_id=prov["id"])[0]
            print(f"        {quincena[0]}: total={liq['valor_total']} "
                  f"deuda_vieja={liq['saldo_anterior']} saldo={liq['saldo']}")
            _cuadra(
                _libro(client, h, f"{nombre}: {quincena[0]} regenerada"),
                f"B2 {nombre} {quincena[0]}",
            )
    libro = _libro(client, h, "los dos ordenes, final")
    _cuadra(libro, "B2 final")
    # Al derecho la deuda se cobra; al revés se queda libre y el anticipo se lo lleva la
    # que se regeneró primero. Las dos formas cuadran, y se imprime lo que quedó.
    for liq in libro["vivas"]:
        print(f"      {liq['proveedor_nombre']} {liq['periodo_inicio']}: "
              f"anticipos={liq['anticipos']} deuda_vieja={liq['saldo_anterior']} "
              f"saldo={liq['saldo']}")


def test_b3_el_omitido_no_mueve_un_peso_y_los_otros_siete_si_salen(client, base_datos):
    """LA OMISIÓN NO PUEDE DEJAR NADA A MEDIO HACER, Y NO PUEDE ARRASTRAR A LOS DEMÁS.

    Ocho proveedores, uno de ellos con una liquidación que se cruza. Antes esto medía el
    REBOTE de la corrida completa —los ocho se quedaban sin comprobante por el cruce de
    uno—; ahora mide las dos mitades del arreglo, que es más exigente que lo de antes:

      · LOS SIETE SANOS SALEN LIQUIDADOS, cada uno con su comprobante;
      · Y EL OMITIDO NO MUEVE UN PESO: ni una liquidación a medio crear, ni su día marcado
        con una liquidación que no existe, ni su anticipo apartado, ni su deuda cobrada.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== B3. EL OMITIDO NO MUEVE UN PESO, LOS DEMÁS SÍ SALEN =====")
    provs = [_proveedor(client, h, f"Prov {i}") for i in range(8)]
    for p in provs:
        _recepcion(client, h, p, "2026-06-02", "100")
        _anticipo(client, h, "2026-06-01", "300000", proveedor=p)
    # Solo al último se le genera la quincena 01-15: queda debiendo $120.000.
    culpable = provs[-1]
    q1 = _ok(client, h, *Q1, proveedor_id=culpable["id"])[0]
    assert D(q1["le_queda_debiendo"]) == D("120000")
    antes = _libro(client, h, "antes de la corrida")
    _cuadra(antes, "B3 antes")

    # La corrida del período que se pisa, para TODOS: el último se cruza, los otros no.
    for p in provs:
        _recepcion(client, h, p, "2026-06-12", "50", precio="2000")
    corrida = _corrida(client, h, "2026-06-10", "2026-06-20")
    omitida = _un_omitido(corrida)
    print(f"      corrida 10-20 para ocho -> generadas={len(corrida['generadas'])}, "
          f"omitida: {omitida['tercero_nombre']}")
    # LOS SIETE SANOS, con su comprobante: $100.000 de leche cada uno.
    assert len(corrida["generadas"]) == 7
    salieron = {liq["proveedor_id"] for liq in corrida["generadas"]}
    assert salieron == {p["id"] for p in provs[:-1]}
    assert all(D(liq["valor_total"]) == D("100000") for liq in corrida["generadas"])
    # Y EL DEL CRUCE, REPORTADO con nombre y motivo: no saltado en silencio.
    assert (omitida["tercero_id"], omitida["cuenta"], omitida["motivo_codigo"]) == (
        culpable["id"], "leche", "periodo_cruzado"
    )

    despues = _libro(client, h, "despues de la corrida")
    _cuadra(despues, "B3 despues")
    # LOS SIETE SALIERON: siete liquidaciones nuevas encima de la única que había.
    assert len(despues["vivas"]) == len(antes["vivas"]) + 7 == 8
    assert despues["leche"] == antes["leche"] + D("700000")

    # EL OMITIDO QUEDÓ INTACTO, mirado por sus tres lados:
    #   · no le nació ninguna liquidación nueva (sigue teniendo solo su Q1),
    del_culpable = [
        liq for liq in _todas(client, h) if liq.get("proveedor_id") == culpable["id"]
    ]
    assert [liq["id"] for liq in del_culpable] == [q1["id"]], del_culpable
    #   · su día del 12 sigue SIN liquidar (nadie lo marcó con una liquidación que no
    #     existe) mientras los de los otros siete sí quedaron marcados,
    del_12 = [r for r in _recepciones(client, h) if r["fecha"] == "2026-06-12"]
    sin_marcar = [r for r in del_12 if r["liquidacion_id"] is None]
    print(f"      días del 12: {len(del_12)}, sin liquidar: {len(sin_marcar)}")
    assert len(del_12) == 8
    assert [r["proveedor_id"] for r in sin_marcar] == [culpable["id"]]
    #   · y su anticipo y su deuda siguen exactamente donde estaban.
    q1_ahora = _leer(client, h, q1["id"])
    assert D(q1_ahora["anticipos"]) == D("300000")
    assert D(q1_ahora["le_queda_debiendo"]) == D("120000")
    assert q1_ahora["deuda_trasladada_a_id"] is None, "le cobraron la deuda al omitido"
    print(f"      la caja no se movió: {despues['anticipos'] + despues['pagado']}")
    assert despues["anticipos"] + despues["pagado"] == antes["anticipos"]


def test_b3b_GARANTIA_un_tercero_con_cruce_no_tumba_la_corrida_de_la_quincena(
    client, base_datos
):
    """GARANTÍA, Y ERA UN HALLAZGO CRÍTICO: UN tercero con una liquidación del período NO
    TUMBA LA CORRIDA. Los demás salen liquidados y él sale REPORTADO.

    EL CASO, que es de todos los días:
      · el 15 se corre la quincena 01-15 y a Henri le sale su comprobante (borrador, sin
        aprobar todavía);
      · el 16 se anota un día olvidado de Henri con fecha del 05 (pasa todo el tiempo) y se
        registran los días de Marleny y Aleida, que entraron después;
      · el dueño vuelve a oprimir Generar de la quincena 01-15 —lo único que sabe hacer—.

    LO QUE COSTABA, con las cifras exactas que esta prueba midió: la corrida ENTERA rebotaba
    culpando a Henri, y MARLENY Y ALEIDA —que NUNCA tuvieron una liquidación de ese período—
    se quedaban sin comprobante: $720.000 de leche afuera por un cruce que no era suyo. La
    salida existía (de a uno por `proveedor_id`) pero el dueño no la ve en el mensaje, que
    solo le hablaba de Henri, y la pantalla no tiene ese botón.

    AHORA esos mismos $720.000 SALEN LIQUIDADOS en la corrida que el dueño ya sabe hacer, y
    Henri sale en `omitidas` con su motivo redactado —no saltado en silencio, que sería
    peor: el dueño cerraría la pantalla creyendo que ya liquidó a todos—. Es la misma
    decisión que ya estaba tomada en `_generar_transportadores` para el transportador sin
    tarifa: "tumbar la corrida entera sería peor".
    """
    h = auth_headers(client, "admin.a")
    print("\n===== B3b. UN TERCERO CON CRUCE NO TUMBA LA CORRIDA =====")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    q1_henri = _ok(client, h, *Q1)[0]
    print(f"      corrida del 15: Henri {q1_henri['valor_total']} (borrador)")

    # El día olvidado de Henri, y los días de las dos que entraron después.
    _recepcion(client, h, henri, "2026-06-05", "30")            # 54.000
    marleny = _proveedor(client, h, "Marleny")
    aleida = _proveedor(client, h, "Aleida")
    for p in (marleny, aleida):
        _recepcion(client, h, p, "2026-06-03", "100")           # 180.000 cada una
        _recepcion(client, h, p, "2026-06-10", "100")           # 180.000 cada una

    corrida = _corrida(client, h, *Q1)
    omitida = _un_omitido(corrida)
    print(f"      la corrida completa 01-15 -> generadas={len(corrida['generadas'])}, "
          f"omitida: {omitida['motivo'][:160]}")

    # LAS DOS QUE NO TIENEN NADA QUE VER SALEN LIQUIDADAS: $360.000 cada una, los mismos
    # $720.000 que antes se quedaban afuera.
    assert len(corrida["generadas"]) == 2
    por_nombre = {liq["proveedor_nombre"]: liq for liq in corrida["generadas"]}
    assert sorted(por_nombre) == ["Aleida", "Marleny"], sorted(por_nombre)
    for nombre, liq in por_nombre.items():
        print(f"      salió {nombre} -> total={liq['valor_total']}")
        assert D(liq["valor_total"]) == D("360000")
    leche_rescatada = D("360000") * 2
    print(f"      LECHE QUE ANTES SE QUEDABA SIN COMPROBANTE Y HOY SALE: {leche_rescatada}")

    # Y HENRI, EL DEL CRUCE, REPORTADO: nombre, cuenta, motivo en código y el texto que
    # el dueño lee, con la liquidación con la que se cruza y sus dos salidas.
    assert (omitida["tercero_id"], omitida["cuenta"], omitida["motivo_codigo"]) == (
        henri["id"], "leche", "periodo_cruzado"
    )
    assert (
        "Henri C ya tiene una liquidación de leche del 01/06/2026 al 15/06/2026"
        in omitida["motivo"]
    )
    assert "Ajuste las fechas" in omitida["motivo"]
    assert "anule esa liquidación primero" in omitida["motivo"]

    # El día olvidado de Henri sigue sin entrar mientras Q1 esté viva —eso no cambió, y es
    # a propósito: dos comprobantes montados dejarían sin cobrar lo que quedó debiendo—.
    # Su leche no se pierde: entra anulando Q1 y volviendo a generar, que es EL flujo de
    # corrección, y ahí sale la quincena completa (los dos días de Henri, $234.000).
    del_henri = [liq for liq in _todas(client, h) if liq.get("proveedor_id") == henri["id"]]
    assert [liq["id"] for liq in del_henri] == [q1_henri["id"]]
    assert _anular(client, h, q1_henri["id"]).status_code == 200
    rescatado = _ok(client, h, *Q1, proveedor_id=henri["id"])
    print(f"      tras anular Q1, Henri completo -> total={rescatado[0]['valor_total']}")
    assert len(rescatado) == 1
    assert D(rescatado[0]["valor_total"]) == D("234000")   # 180.000 + 54.000
    libro = _libro(client, h, "la corrida completa, sin dejar a nadie afuera")
    _cuadra(libro, "B3b")
    assert libro["leche"] == D("234000") + leche_rescatada


def test_b4_el_papel_oficial_y_el_preliminar_suman_exacto(client, base_datos):
    """EL PAPEL QUE FIRMA EL DUEÑO: de arriba abajo, cada renglón suma la cifra grande.

    Y EL AVANCE DICE LO MISMO QUE EL PAPEL DEL AVANCE. Esta prueba medía un hueco por ese
    lado —«EL JSON DEL AVANCE NO TRAE LA DEUDA: solo el PDF avisa»—, o sea que la pantalla
    decía "saldo $250.000" y el papel del MISMO avance decía que de la caja salen $130.000.
    Dos cifras para el mismo hecho, y el dueño manda el papel mirando la pantalla. Ya está
    cerrado: el avance trae `deuda_pendiente` con la misma cifra del papel, así que acá se
    exige lo contrario de lo que se medía —que el campo esté y que cuadre con el papel—.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== B4. EL PAPEL =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _ok(client, h, *Q1)[0]
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")

    # El PRELIMINAR (el avance) antes de generar: tiene que avisar la deuda.
    r = client.post(
        f"{API}/previsualizar",
        json={
            "periodo_inicio": Q2[0],
            "periodo_fin": Q2[1],
            "tipo": "proveedor",
            "tercero_id": prov["id"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    avance = r.json()[0]
    print(f"      avance JSON: total={avance['valor_total']} saldo={avance['saldo']} "
          f"campos de deuda={[k for k in avance if 'deuda' in k]}")
    # EL JSON DEL AVANCE SÍ TRAE LA DEUDA, con la misma cifra que el papel. El `saldo`
    # sigue SIN descontarla a propósito —el avance no marca ni aparta nada— así que la
    # pantalla la muestra como aviso aparte y la resta es la del papel:
    #     saldo − deuda_pendiente = lo que va a salir de verdad de la caja
    assert "deuda_pendiente" in avance, (
        "la pantalla diría saldo $250.000 y el papel del mismo avance $130.000"
    )
    assert D(avance["saldo"]) == D("250000")
    assert D(avance["deuda_pendiente"]) == D("120000")
    assert D(avance["saldo"]) - D(avance["deuda_pendiente"]) == D("130000")

    r = client.post(
        f"{API}/previsualizar/pdf",
        json={
            "periodo_inicio": Q2[0],
            "periodo_fin": Q2[1],
            "tipo": "proveedor",
            "tercero_id": prov["id"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    from tests.test_liquidacion_deuda_arrastrada_plata import texto_pdf

    preliminar = texto_pdf(r.content)
    assert "120.000" in preliminar, "el papel preliminar no avisa la deuda"

    q2 = _ok(client, h, *Q2)[0]
    papel = _pdf(client, h, q2["id"])
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    vieja = renglon(papel, "quedó debiendo de la quincena pasada")
    saldo = renglon(papel, "SALDO A PAGAR")
    print(f"      papel de Q2: VALOR TOTAL={total} anticipos={anticipos} "
          f"deuda_vieja={vieja} SALDO A PAGAR={saldo}")
    # DE ARRIBA ABAJO: total - anticipos - deuda vieja == saldo a pagar. Exacto.
    assert total - abs(anticipos) - abs(vieja) == saldo, f"el papel no suma:\n{papel}"
    assert (total, abs(vieja), saldo) == (D("250000"), D("120000"), D("130000"))
    # y el papel dice DE DÓNDE viene la deuda, con período y número
    assert "01/06/2026 al 15/06/2026" in papel and "quedó debiendo $120.000" in papel

    # EL PAPEL DEL ORIGEN: el que quedó debiendo también tiene que sumar exacto.
    papel_q1 = _pdf(client, h, q1["id"])
    t1 = renglon(papel_q1, "VALOR TOTAL")
    a1 = renglon(papel_q1, "Anticipos aplicados")
    s1 = renglon(papel_q1, "QUEDA DEBIENDO") if "QUEDA DEBIENDO" in papel_q1 else None
    print(f"      papel de Q1: VALOR TOTAL={t1} anticipos={a1} queda_debiendo={s1}")
    assert t1 == D("180000") and abs(a1) == D("300000")
    assert s1 == D("120000"), f"el papel del origen no dice la deuda:\n{papel_q1}"

    _cuadra(_libro(client, h, "el papel"), "B4")
    assert _leer(client, h, q1["id"])["deuda_trasladada_a_id"] == q2["id"]


def test_b5_GARANTIA_la_pagada_vieja_con_saldo_negativo_si_reserva_sus_fechas(
    client, base_datos, db_session
):
    """GARANTÍA SOBRE LOS DATOS QUE HAY EN LA BASE DEL CLIENTE: la 'pagada' vieja con saldo
    negativo SÍ reserva sus fechas, así que el período montado no se genera encima de ella.

    La migración `e5c2b9a1f7d3` lo dice con estas palabras: «Tampoco se les toca el estado
    a las que el botón "Pagar" de antes dejó en 'pagada' con saldo negativo: esa deuda
    sigue contando y se cobra igual (la consulta acepta 'aprobada' y 'pagada')». O sea: en
    la base del cliente HAY liquidaciones 'pagada' con saldo negativo, y son orígenes de
    deuda válidos.

    EL PUNTO CIEGO QUE ESTA PRUEBA MIDIÓ: `solapada_para_periodo` dejaba pasar TODA 'pagada'
    (y todo `pagado > 0`) con este argumento —«una quincena pagada no puede estar debiendo
    nada, porque `pagar` rebota cuando el tercero quedó debiendo»—. Es cierto para los datos
    NUEVOS (se verifica en el paso 1, que se conserva tal cual) y FALSO para las filas viejas,
    que nacieron antes de ese guardia. Sobre ellas el hueco original seguía abierto con sus
    mismas cifras: salía el período montado, no le cobraba la deuda —solo viaja hacia
    adelante— y de la caja salían $500.000 por $380.000 de leche.

    HOY LA CONDICIÓN ES OTRA (ver `solapada_para_periodo`): reserva sus fechas la que
    todavía no tiene plata entregada O la que YA TIENE EL SALDO POR DEBAJO DE CERO, sin
    importar el estado. Así que la fila vieja se cruza, al tercero se lo salta y se lo
    reporta, y de la caja NO sale un peso por ese período.

    Y LA RESERVA TIENE SALIDA, que es la otra mitad y sin ella esta leche quedaría presa:
    se le genera la quincena que NO se monta, esa cobra la deuda y le pone la marca, y desde
    ese momento el día de adentro entra. Medido de punta a punta: salen $400.000 por
    $380.000 de leche con $20.000 que el tercero queda debiendo — $100.000 menos de la caja
    que por el hueco de antes, y esos $100.000 son exactamente lo que ya se le había
    adelantado y se le volvía a pagar.

    Este estado se construye a mano en la BD a propósito: por la API ya no se puede llegar
    (`pagar` rebota, `recuadrar`/`recalcular` rebotan con pagos), y es exactamente lo que
    la migración dice que está en producción.
    """
    from app.modules.liquidaciones.models import Liquidacion

    h = auth_headers(client, "admin.a")
    print("\n===== B5. LA 'PAGADA' VIEJA CON SALDO NEGATIVO =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _ok(client, h, *Q1)[0]
    assert D(q1["le_queda_debiendo"]) == D("120000")

    # 1) POR LA API NO SE LLEGA: queda constancia de que el argumento viejo del guardia es
    #    cierto para los datos nuevos.
    assert _aprobar(client, h, q1["id"]).status_code == 200
    r = _pagar(client, h, q1["id"])
    print(f"      pagar una que quedó debiendo -> {r.status_code}")
    assert r.status_code == 422

    # 2) SE FABRICA LA FILA VIEJA: 'pagada' con saldo negativo, como la dejó el botón
    #    "Pagar" de antes. Nada más se toca.
    fila = db_session.get(Liquidacion, __import__("uuid").UUID(q1["id"]))
    fila.estado = "pagada"
    db_session.flush()
    db_session.commit()
    print(f"      fila vieja simulada: estado={fila.estado} saldo={fila.saldo} "
          f"pagado={fila.pagado}")
    antes = _libro(client, h, "con la pagada vieja debiendo 120.000")
    _cuadra(antes, "B5 antes")
    assert antes["debiendo"] == D("120000")

    # 3) EL PERÍODO MONTADO: EL GUARDIA LO PARA, y sin tumbar la corrida.
    _recepcion(client, h, prov, "2026-06-12", "50", precio="2000")   # 100.000
    _recepcion(client, h, prov, "2026-06-18", "50", precio="2000")   # 100.000
    corrida = _corrida(client, h, "2026-06-10", "2026-06-20")
    omitida = _un_omitido(corrida)
    print(f"      generar 10-20 sobre la pagada vieja -> generadas="
          f"{len(corrida['generadas'])}, omitida: {omitida['motivo'][:130]}")
    assert corrida["generadas"] == [], (
        "salió el período montado encima de la 'pagada' vieja que está debiendo: esa "
        "liquidación no le cobra la deuda y se le vuelve a pagar lo ya adelantado"
    )
    assert (omitida["tercero_id"], omitida["cuenta"], omitida["motivo_codigo"]) == (
        prov["id"], "leche", "periodo_cruzado"
    )
    assert "del 01/06/2026 al 15/06/2026" in omitida["motivo"]
    # NI UN PESO SE MOVIÓ, y los dos días nuevos siguen esperando.
    despues = _libro(client, h, "el montado se omitió: la caja quieta")
    _cuadra(despues, "B5 omitido")
    assert despues["leche"] == antes["leche"] == D("180000")
    assert despues["anticipos"] + despues["pagado"] == D("300000")
    assert despues["debiendo"] == D("120000")

    # 4) LA SALIDA DE LA RESERVA: la quincena que NO se monta (16 al 20) sí sale, y ELLA
    #    SÍ le cobra los $120.000 —la deuda viaja hacia adelante, que es su único
    #    sentido—. Al cobrarla, la fila vieja queda marcada y deja de reservar sus fechas.
    buena = _ok(client, h, "2026-06-16", "2026-06-20")[0]
    print(f"      16-20: total={buena['valor_total']} deuda_vieja={buena['saldo_anterior']} "
          f"saldo={buena['saldo']}")
    assert D(buena["valor_total"]) == D("100000")
    assert D(buena["saldo_anterior"]) == D("120000"), "la deuda de la pagada vieja se PERDIÓ"
    assert D(buena["le_queda_debiendo"]) == D("20000")
    assert _leer(client, h, q1["id"])["deuda_trasladada_a_id"] == buena["id"]
    _cuadra(_libro(client, h, "la deuda cobrada por la que no se monta"), "B5 16-20")

    # 5) Y EL DÍA DE ADENTRO YA TIENE CAMINO: con la fila vieja marcada, el rango que la
    #    contiene (10 al 15) pasa y se lleva el día 12. La leche no queda presa.
    adentro = _ok(client, h, "2026-06-10", "2026-06-15")[0]
    print(f"      10-15 con la vieja ya marcada: total={adentro['valor_total']} "
          f"deuda_vieja={adentro['saldo_anterior']}")
    assert D(adentro["valor_total"]) == D("100000")
    assert _aprobar(client, h, adentro["id"]).status_code == 200
    assert _pagar(client, h, adentro["id"]).status_code == 200
    final = _libro(client, h, "todo cobrado")
    _cuadra(final, "B5 final")
    print(f"      SALIÓ DE LA CAJA {final['anticipos'] + final['pagado']} "
          f"POR {final['leche']} DE LECHE, y el tercero queda debiendo {final['debiendo']}")
    # LAS CIFRAS DEL HUECO CERRADO: por el mismo camino salían $500.000 por $380.000 de
    # leche y el tercero no quedaba debiendo nada. Ahora salen $400.000 por los mismos
    # $380.000 y quedan $20.000 anotados como deuda: $100.000 que ya no salen dos veces.
    assert final["leche"] == D("380000")
    assert final["anticipos"] + final["pagado"] == D("400000")
    assert final["debiendo"] == D("20000")
    assert final["anticipos"] + final["pagado"] - final["leche"] == final["debiendo"]


def test_b6_GARANTIA_un_transportador_con_cruce_no_tumba_la_corrida_ambos(
    client, base_datos
):
    """GARANTÍA, Y ERA EL HALLAZGO DE MAYOR ALCANCE: UN transportador con su flete en
    borrador NO tumba la corrida tipo="ambos" —la que manda la pantalla— de TODA la quincena.

    POR QUÉ ES EL DE MAYOR ALCANCE: la pantalla ("Generar quincena") manda exactamente
    {periodo_inicio, periodo_fin, tipo} y NUNCA `proveedor_id` (ver
    Front-Lactis/src/app/features/liquidaciones/generar-quincena.dialog.ts), así que la
    salida "de a uno" que sí existe en la API NO ESTÁ EN NINGÚN BOTÓN. Y un transportador
    recoge la leche de MUCHOS proveedores: su cruce se llevaba por delante toda la corrida.
    Encima el flete va PRIMERO en `generar`, así que rebotaba antes de que a la leche le
    tocara turno y ninguno de los proveedores nuevos alcanzaba a salir: $1.080.000 de leche
    de tres proveedores sin comprobante por un cruce que era del transportador.

    AHORA esos $1.080.000 SALEN LIQUIDADOS en la misma corrida que manda la pantalla, y el
    flete de Don Chucho sale REPORTADO en `omitidas`. El flete tampoco se pierde: sus
    litros siguen pendientes y entran completos al anular el cruce, y el estado final en
    plata es EL MISMO que ya se medía por ese camino ($1.400.000 de leche y flete).
    """
    h = auth_headers(client, "admin.a")
    print("\n===== B6. UN TRANSPORTADOR CON CRUCE NO TUMBA LA CORRIDA 'ambos' =====")
    ruta = _ruta(client, h, "Napoles")
    t = _transportador(client, h, "Don Chucho", [(ruta, "200")])
    viejo = _proveedor(client, h, "Henri C")
    _recepcion(client, h, viejo, "2026-06-02", "100", t=t, ruta=ruta)
    primera = _ok(client, h, *Q1, tipo="ambos")
    print(f"      corrida del 15 -> {sorted(liq['tipo'] for liq in primera)}")
    assert sorted(liq["tipo"] for liq in primera) == ["proveedor", "transportador"]

    # Entran tres proveedores nuevos, todos con la leche que recogió Don Chucho.
    nuevos = [_proveedor(client, h, f"Nuevo {i}") for i in range(3)]
    for p in nuevos:
        _recepcion(client, h, p, "2026-06-04", "100", t=t, ruta=ruta)
        _recepcion(client, h, p, "2026-06-11", "100", t=t, ruta=ruta)

    # El payload EXACTO de la pantalla: sin `proveedor_id`.
    corrida = _corrida(client, h, *Q1, tipo="ambos")
    omitida = _un_omitido(corrida)
    print(f"      la corrida de la pantalla -> generadas={len(corrida['generadas'])}, "
          f"omitida: {omitida['motivo'][:170]}")

    # LOS TRES PROVEEDORES NUEVOS SALEN LIQUIDADOS: $360.000 cada uno, los $1.080.000 que
    # antes se quedaban sin comprobante por un cruce que no era suyo.
    de_leche = [liq for liq in corrida["generadas"] if liq["tipo"] == "proveedor"]
    assert len(de_leche) == 3
    assert {liq["proveedor_id"] for liq in de_leche} == {p["id"] for p in nuevos}
    assert all(D(liq["valor_total"]) == D("360000") for liq in de_leche)
    leche_rescatada = sum((D(liq["valor_total"]) for liq in de_leche), CERO)
    print(f"      LECHE QUE ANTES SE QUEDABA SIN COMPROBANTE Y HOY SALE: {leche_rescatada}")
    assert leche_rescatada == D("1080000")
    # Y EL FLETE DE DON CHUCHO, REPORTADO: no rebotó nada y no se saltó en silencio.
    assert (omitida["tercero_id"], omitida["cuenta"], omitida["motivo_codigo"]) == (
        t["id"], "flete", "periodo_cruzado"
    )
    assert "Don Chucho ya tiene una liquidación de flete" in omitida["motivo"]
    libro = _libro(client, h, "los tres salieron, el flete se reportó")
    _cuadra(libro, "B6")
    # $180.000 (Henri) + $20.000 (su flete) + $1.080.000 de los tres nuevos.
    assert libro["leche"] == D("200000") + leche_rescatada

    # EL FLETE NO SE PERDIÓ: anulando el de Don Chucho entra completo, los 700 L del
    # período a $200. Los tres proveedores ya están liquidados, así que solo sale el flete.
    vivas = [liq for liq in _todas(client, h) if liq["estado"] != "anulada"]
    flete = next(liq for liq in vivas if liq["tipo"] == "transportador")
    assert _anular(client, h, flete["id"]).status_code == 200
    salieron = _ok(client, h, *Q1, tipo="ambos")
    print(f"      tras anular el flete -> {len(salieron)} liquidaciones "
          f"({sorted(set(liq['tipo'] for liq in salieron))}) "
          f"total={salieron[0]['valor_total']}")
    assert [liq["tipo"] for liq in salieron] == ["transportador"]
    assert D(salieron[0]["valor_total"]) == D("140000")   # 700 L a $200
    final = _libro(client, h, "el flete entró completo")
    _cuadra(final, "B6 final")
    # LA MISMA CIFRA FINAL QUE SE MEDÍA POR ESTE CAMINO, y ahora sin dejar a nadie afuera:
    # 180.000 (Henri) + 3 x 360.000 (los nuevos) + el flete de los 700 L a $200.
    assert final["leche"] == D("180000") + D("1080000") + D("140000")
