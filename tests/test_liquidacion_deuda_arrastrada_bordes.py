"""LA DEUDA QUE SE ARRASTRA — LOS BORDES donde la plata se puede escapar.

Sigue midiendo la misma cuenta del dueño que tests/test_liquidacion_deuda_arrastrada_plata.py
(se reúsa su libro), pero ahora sobre los caminos raros: el flete con centavos, dos
deudas cobradas de una, el abono borrado y el día movido, el neto que cae justo en cero,
las dos empresas, y el transportador.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_arrastrada_plata import (
    ANT,
    API,
    CERO,
    D,
    Q1,
    Q2,
    Q3,
    Q4,
    REC,
    _anticipo,
    _anular,
    _aprobar,
    _cuadra,
    _generar,
    _leer,
    _libro,
    _pagar,
    _pdf,
    _proveedor,
    _recalcular,
    _recepcion,
    _ruta,
    _todas,
    _transportador,
    renglon,
)


# ===========================================================================
# 11. EL FLETE: LA MISMA CADENA POR EL LADO DEL TRANSPORTADOR, CON CENTAVOS
# ===========================================================================
def test_11_la_deuda_del_transportador_cuadra_al_centavo(client, base_datos):
    """Tarifa con decimales para que la deuda arrastrada TENGA centavos.

    3 proveedores × 41,57 L en la ruta Nápoles a $242,76/L:
        124,71 L × $242,76 = $30.276,60 (redondeando el reparto al centavo)
    Anticipo al transportador: $50.000 -> queda debiendo lo que sobre.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 11. EL FLETE CON CENTAVOS =====")
    ruta = _ruta(client, h, "Napoles")
    t = _transportador(client, h, "Don Chucho", [(ruta, "242.76")])
    p1 = _proveedor(client, h, "Marleny")
    p2 = _proveedor(client, h, "Aleida")
    p3 = _proveedor(client, h, "Henri C")
    for prov in (p1, p2, p3):
        _recepcion(client, h, prov, "2026-06-02", "41.57", t=t, ruta=ruta)
    _anticipo(client, h, "2026-06-01", "50000", transportador=t)
    q1 = [liq for liq in _generar(client, h, Q1, tipo="ambos") if liq["tipo"] == "transportador"][0]
    print(f"      Q1 flete: total={q1['valor_total']} ant={q1['anticipos']} "
          f"saldo={q1['saldo']} debe={q1['le_queda_debiendo']}")
    assert D(q1["le_queda_debiendo"]) > CERO
    deuda = D(q1["le_queda_debiendo"])
    assert deuda != deuda.to_integral_value(), f"se quería una deuda con centavos: {deuda}"

    for prov in (p1, p2, p3):
        _recepcion(client, h, prov, "2026-06-20", "60.13", t=t, ruta=ruta)
    q2 = [liq for liq in _generar(client, h, Q2, tipo="ambos") if liq["tipo"] == "transportador"][0]
    print(f"      Q2 flete: total={q2['valor_total']} deuda_vieja={q2['saldo_anterior']} "
          f"saldo={q2['saldo']}")
    assert D(q2["saldo_anterior"]) == deuda, "la deuda con centavos no viajó exacta"
    assert D(q2["saldo"]) == D(q2["valor_total"]) - deuda

    # El papel del flete suma de arriba abajo, al centavo.
    papel = _pdf(client, h, q2["id"])
    transporte = renglon(papel, "Valor transporte")
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    vieja = renglon(papel, "Lo que quedó debiendo de la quincena pasada")
    saldo = renglon(papel, "SALDO A PAGAR")
    print(f"      papel: transporte={transporte} TOTAL={total} ant={anticipos} "
          f"vieja={vieja} SALDO={saldo}")
    assert transporte == total
    assert total + anticipos + vieja == saldo
    assert saldo == D(_leer(client, h, q2["id"])["saldo"])

    assert _aprobar(client, h, q2["id"]).status_code == 200
    assert _pagar(client, h, q2["id"]).status_code == 200
    libro = _libro(client, h, "flete pagado")
    _cuadra(libro, "11")
    # Toda la plata del flete: los dos comprobantes menos el anticipo.
    total_flete = sum(
        (D(liq["valor_total"]) for liq in libro["vivas"] if liq["tipo"] == "transportador"),
        CERO,
    )
    pagado_flete = sum(
        (D(liq["pagado"]) for liq in libro["vivas"] if liq["tipo"] == "transportador"), CERO
    )
    print(f"      flete total={total_flete} anticipo=50000 pagado={pagado_flete}")
    assert pagado_flete + D("50000") == total_flete, (
        "la plata del transportador no da: flete "
        f"{total_flete} contra {pagado_flete + D('50000')} entregados"
    )


# ===========================================================================
# 12. DOS DEUDAS COBRADAS DE UNA SOLA VEZ
# ===========================================================================
def test_12_dos_deudas_de_dos_quincenas_se_cobran_juntas_y_el_desglose_suma(
    client, base_datos
):
    """DOS quincenas debiendo A LA VEZ, y una tercera que las cobra JUNTAS.

    A (01 al 05, día 02): 10 L × $1.800 = $18.000 contra $100.000 -> debe  $82.000
    B (06 al 10, día 07): 10 L × $1.800 = $18.000 contra  $50.000 -> debe  $32.000
    C (11 al 20, día 15): 200 L × $1.800 = $360.000, cobra $114.000 -> neto $246.000

    leche $396.000 = anticipos $150.000 + pagado $246.000.

    CÓMO SE CONSIGUE QUE HAYA DOS DEBIENDO A LA VEZ, que tiene su truco: B empieza
    DESPUÉS de que A termine, así que si se generaran en orden, B se cobraría la deuda de
    A y nunca habría dos. Se generan AL REVÉS —primero la nueva (B) y después la vieja
    (A)—, que es un camino que el sistema permite a propósito: la deuda solo viaja HACIA
    ADELANTE, así que la vieja no puede cobrarle nada a la nueva y las dos quedan
    debiendo, esperando a la primera quincena que empiece después de las dos.

    Cada anticipo se registra JUSTO ANTES de generar su quincena, y no todos al principio:
    los anticipos pendientes se recogen por `fecha <= periodo_fin` sin tope por abajo, así
    que si el de $100.000 ya existiera al generar B, B se lo llevaría también y no quedaría
    la cifra que se quiere medir.

    (Antes esto se montaba con dos períodos que SE PISABAN —01 al 10 y 05 al 10, para que
    terminaran el mismo día— y hoy eso no se deja generar: dos liquidaciones montadas una
    sobre la otra dejaban sin cobrar la deuda de la primera. Ver la tanda 30 de
    tests/test_liquidacion_deuda_arrastrada_puertas.py.)
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 12. DOS DEUDAS DE UNA =====")
    prov = _proveedor(client, h, "Henri C")
    a = ("2026-06-01", "2026-06-05")
    b = ("2026-06-06", "2026-06-10")
    c = ("2026-06-11", "2026-06-20")
    # La NUEVA primero, con su propio anticipo.
    _recepcion(client, h, prov, "2026-06-07", "10")
    _anticipo(client, h, "2026-06-06", "50000", proveedor=prov)
    qb = _generar(client, h, b)[0]
    # Y después la VIEJA: no le puede cobrar nada a la nueva.
    _recepcion(client, h, prov, "2026-06-02", "10")
    _anticipo(client, h, "2026-06-01", "100000", proveedor=prov)
    qa = _generar(client, h, a)[0]
    assert D(qa["saldo_anterior"]) == CERO, "la deuda de la NUEVA viajó al pasado"
    print(f"      A debe={qa['le_queda_debiendo']}  B debe={qb['le_queda_debiendo']}")
    assert D(qa["le_queda_debiendo"]) == D("82000")
    assert D(qb["le_queda_debiendo"]) == D("32000")
    _cuadra(_libro(client, h, "dos debiendo"), "12 armada")

    _recepcion(client, h, prov, "2026-06-15", "200")  # 360.000
    qc = _generar(client, h, c)[0]
    print(f"      C total={qc['valor_total']} deuda_vieja={qc['saldo_anterior']} "
          f"saldo={qc['saldo']} desglose={[o['le_queda_debiendo'] for o in qc['deudas_cobradas']]}")
    assert D(qc["saldo_anterior"]) == D("114000"), "no cobró las dos deudas"
    assert len(qc["deudas_cobradas"]) == 2
    assert sum((D(o["le_queda_debiendo"]) for o in qc["deudas_cobradas"]), CERO) == D("114000")
    papel = _pdf(client, h, qc["id"])
    assert "01/06/2026 al 05/06/2026" in papel
    assert "06/06/2026 al 10/06/2026" in papel
    assert _aprobar(client, h, qc["id"]).status_code == 200
    assert _pagar(client, h, qc["id"]).status_code == 200
    libro = _libro(client, h, "C pagada")
    _cuadra(libro, "12 final")
    # leche 18.000 + 18.000 + 360.000 = 396.000; anticipos 150.000; pagado 246.000
    assert libro["leche"] == D("396000")
    assert libro["anticipos"] + libro["pagado"] == D("396000")

    # Y al anular C, las DOS deudas vuelven a quedar libres (ninguna se pierde).
    # Primero hay que borrarle el pago.
    pago = _leer(client, h, qc["id"])["pagos"][0]
    assert client.delete(f"{API}/{qc['id']}/pagos/{pago['id']}", headers=h).status_code == 200
    assert _anular(client, h, qc["id"]).status_code == 200
    libro = _libro(client, h, "C anulada: las dos deudas libres")
    _cuadra(libro, "12 anulada")
    assert libro["debiendo"] == D("114000"), "una de las dos deudas se perdió al anular"


# ===========================================================================
# 13. EL ABONO BORRADO Y EL DÍA MOVIDO: la que cobró se queda sin días
# ===========================================================================
def test_13_borrar_el_abono_y_mover_el_dia_de_la_que_cobro(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 13. ABONO BORRADO Y DÍA MOVIDO =====")
    prov = _proveedor(client, h, "Henri C")
    dia1 = _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    dia2 = _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    assert _aprobar(client, h, q2["id"]).status_code == 200
    r = client.post(
        f"{API}/{q2['id']}/pagos", json={"fecha": "2026-07-01", "valor": "50000"}, headers=h
    )
    assert r.status_code == 200, r.text
    _cuadra(_libro(client, h, "Q2 con un abono de 50.000"), "13 abono")
    # con el abono hecho, el día de Q2 queda trabado
    assert client.put(
        f"{REC}/{dia2['id']}", json={"cantidad_litros": "50"}, headers=h
    ).status_code >= 400

    pago = _leer(client, h, q2["id"])["pagos"][0]
    assert client.delete(f"{API}/{q2['id']}/pagos/{pago['id']}", headers=h).status_code == 200
    _cuadra(_libro(client, h, "abono borrado"), "13 sin abono")

    # Y ahora se le mueve el día a JULIO: Q2 se queda sin leche pero SIGUE cobrando la
    # deuda vieja, así que pasa a deber ella misma.
    r = client.put(f"{REC}/{dia2['id']}", json={"fecha": "2026-07-05"}, headers=h)
    print(f"      mover el dia de Q2 a julio -> {r.status_code}")
    assert r.status_code == 200, r.text
    q2_ahora = _leer(client, h, q2["id"])
    print(f"      Q2: total={q2_ahora['valor_total']} deuda_vieja={q2_ahora['saldo_anterior']} "
          f"saldo={q2_ahora['saldo']} debe={q2_ahora['le_queda_debiendo']}")
    _cuadra(_libro(client, h, "Q2 sin dias"), "13 sin dias")

    # La cadena sigue: julio cobra lo que Q2 quedó debiendo, UNA sola vez.
    q3 = _generar(client, h, Q3)[0]
    print(f"      Q3: total={q3['valor_total']} deuda_vieja={q3['saldo_anterior']} "
          f"saldo={q3['saldo']}")
    assert _aprobar(client, h, q3["id"]).status_code == 200
    if D(q3["saldo"]) > CERO:
        assert _pagar(client, h, q3["id"]).status_code == 200
    libro = _libro(client, h, "Q3 pagada")
    _cuadra(libro, "13 final")
    # leche real: 180.000 (Q1) + 250.000 (el dia movido, ahora en Q3) = 430.000
    assert libro["leche"] == D("430000")
    assert libro["anticipos"] + libro["pagado"] == D("430000")


# ===========================================================================
# 14. EL NETO QUE CAE JUSTO EN CERO POR LA DEUDA ARRASTRADA
# ===========================================================================
def test_14_el_neto_en_cero_por_la_deuda_no_se_marca_pagada_ni_traba_los_dias(
    client, base_datos
):
    """Q1 deja debiendo $120.000; Q2 vale EXACTO $120.000 -> neto $0,00 clavado.

    No hay un peso que entregar, así que 'Pagar' rebota y la quincena se queda en
    'aprobada': ni se dice pagada sin plata, ni se traban sus días.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 14. NETO JUSTO EN CERO =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    _generar(client, h, Q1)
    dia2 = _recepcion(client, h, prov, "2026-06-20", "100", precio="1200")  # 120.000
    q2 = _generar(client, h, Q2)[0]
    print(f"      Q2: total={q2['valor_total']} deuda_vieja={q2['saldo_anterior']} "
          f"saldo={q2['saldo']} debe={q2['le_queda_debiendo']}")
    assert D(q2["saldo"]) == CERO
    assert D(q2["le_queda_debiendo"]) == CERO
    assert _aprobar(client, h, q2["id"]).status_code == 200
    r = _pagar(client, h, q2["id"])
    print(f"      pagar -> {r.status_code}: {r.text[:150]}")
    assert r.status_code == 422, "se marcó PAGADA una quincena por la que no salió un peso"
    assert _leer(client, h, q2["id"])["estado"] == "aprobada"
    r = client.post(
        f"{API}/{q2['id']}/pagos", json={"fecha": "2026-07-01", "valor": "1000"}, headers=h
    )
    assert r.status_code == 422, "se abonó a una liquidación sin saldo"
    # y sus días siguen corregibles: por ellos no salió plata
    r = client.put(f"{REC}/{dia2['id']}", json={"cantidad_litros": "150"}, headers=h)
    print(f"      corregir el dia de Q2 -> {r.status_code}")
    assert r.status_code == 200, f"trabó un día por el que no salió plata: {r.text[:200]}"
    q2_ahora = _leer(client, h, q2["id"])
    print(f"      Q2 corregida: total={q2_ahora['valor_total']} saldo={q2_ahora['saldo']}")
    _cuadra(_libro(client, h, "Q2 corregida"), "14")
    assert _aprobar(client, h, q2["id"]).status_code == 200
    assert _pagar(client, h, q2["id"]).status_code == 200
    libro = _libro(client, h, "Q2 pagada de verdad")
    _cuadra(libro, "14 final")
    # leche 180.000 + 150 L × 1.200 = 180.000 + 180.000 = 360.000
    assert libro["leche"] == D("360000")
    assert libro["anticipos"] + libro["pagado"] == D("360000")


# ===========================================================================
# 15. LAS DOS EMPRESAS: la deuda no cruza de tenant
# ===========================================================================
def test_15_la_deuda_no_cruza_de_empresa(client, base_datos):
    print("\n===== 15. DOS EMPRESAS =====")
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    # Mismo nombre en las dos empresas, para que solo el id los distinga.
    pa = _proveedor(client, ha, "Henri C")
    pb = _proveedor(client, hb, "Henri C")
    _recepcion(client, ha, pa, "2026-06-02", "100")
    _recepcion(client, hb, pb, "2026-06-02", "100")
    _anticipo(client, ha, "2026-06-01", "300000", proveedor=pa)
    q1a = _generar(client, ha, Q1)[0]
    q1b = _generar(client, hb, Q1)[0]
    print(f"      A debe={q1a['le_queda_debiendo']}  B debe={q1b['le_queda_debiendo']}")
    assert D(q1a["le_queda_debiendo"]) == D("120000")
    assert D(q1b["le_queda_debiendo"]) == CERO

    _recepcion(client, ha, pa, "2026-06-20", "100", precio="2500")
    _recepcion(client, hb, pb, "2026-06-20", "100", precio="2500")
    q2a = _generar(client, ha, Q2)[0]
    q2b = _generar(client, hb, Q2)[0]
    print(f"      A deuda_vieja={q2a['saldo_anterior']}  B deuda_vieja={q2b['saldo_anterior']}")
    assert D(q2a["saldo_anterior"]) == D("120000")
    assert D(q2b["saldo_anterior"]) == CERO, "la deuda de la empresa A se le cobró a la B"
    _cuadra(_libro(client, ha, "empresa A"), "15 A")
    _cuadra(_libro(client, hb, "empresa B"), "15 B")
    # La empresa B no ve ni puede tocar la liquidación de la A.
    assert client.get(f"{API}/{q1a['id']}", headers=hb).status_code == 404


# ===========================================================================
# 16. LA CADENA MÁS LARGA, PAGANDO SIEMPRE LO QUE EL SISTEMA PIDE
# ===========================================================================
def test_16_cinco_quincenas_con_anticipos_en_todas(client, base_datos):
    """Anticipos en TODAS las quincenas y leche desigual, para que la deuda entre y
    salga varias veces. Al final se mide la caja contra la leche.

    litros a $1.800 y anticipos:
        Q1  10 L =  18.000   anticipo 100.000
        Q2  20 L =  36.000   anticipo  50.000
        Q3 300 L = 540.000   anticipo  20.000
        Q4   5 L =   9.000   anticipo 400.000
        Q5 500 L = 900.000   anticipo       0
    leche 1.503.000; anticipos 570.000 -> tienen que salir 933.000 en pagos.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 16. CINCO QUINCENAS CON ANTICIPOS EN TODAS =====")
    prov = _proveedor(client, h, "Henri C")
    pasos = [
        (Q1, "2026-06-02", "10", "100000", "2026-06-01"),
        (Q2, "2026-06-20", "20", "50000", "2026-06-17"),
        (Q3, "2026-07-02", "300", "20000", "2026-07-02"),
        (Q4, "2026-07-20", "5", "400000", "2026-07-18"),
        (("2026-08-01", "2026-08-15"), "2026-08-02", "500", None, None),
    ]
    for i, (periodo, dia, litros, anticipo, fecha_ant) in enumerate(pasos, start=1):
        if anticipo:
            _anticipo(client, h, fecha_ant, anticipo, proveedor=prov)
        _recepcion(client, h, prov, dia, litros)
        liq = _generar(client, h, periodo)[0]
        print(f"      Q{i}: total={liq['valor_total']} ant={liq['anticipos']} "
              f"vieja={liq['saldo_anterior']} saldo={liq['saldo']}")
        _cuadra(_libro(client, h, f"tras generar Q{i}"), f"16 gen Q{i}")
        assert _aprobar(client, h, liq["id"]).status_code == 200
        r = _pagar(client, h, liq["id"])
        if D(liq["saldo"]) > CERO:
            assert r.status_code == 200, r.text
        else:
            assert r.status_code == 422, f"se marcó pagada sin plata: {r.text[:200]}"
        _cuadra(_libro(client, h, f"tras pagar Q{i}"), f"16 pago Q{i}")

    libro = _libro(client, h, "cadena de cinco con anticipos")
    _cuadra(libro, "16 final")
    assert libro["leche"] == D("1503000")
    assert libro["anticipos"] == D("570000")
    assert libro["pagado"] == D("933000"), (
        f"salieron {libro['pagado']} en pagos y debían salir 933000"
    )
    assert libro["debiendo"] == CERO
    assert libro["por_pagar"] == CERO


# ===========================================================================
# 17. GENERAR SOLO PARA UN PROVEEDOR (el filtro de la pantalla)
# ===========================================================================
def test_17_generar_de_a_un_proveedor_no_le_cobra_la_deuda_a_otro(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 17. GENERAR DE A UNO =====")
    henri = _proveedor(client, h, "Henri")
    rosa = _proveedor(client, h, "Dona Rosa")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _recepcion(client, h, rosa, "2026-06-03", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    _generar(client, h, Q1)
    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    _recepcion(client, h, rosa, "2026-06-21", "100", precio="2500")
    # Solo la de Doña Rosa
    r = client.post(
        f"{API}/generar",
        json={
            "periodo_inicio": Q2[0],
            "periodo_fin": Q2[1],
            "tipo": "proveedor",
            "proveedor_id": rosa["id"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    solo_rosa = r.json()["generadas"]
    assert len(solo_rosa) == 1
    print(f"      Rosa: deuda_vieja={solo_rosa[0]['saldo_anterior']}")
    assert D(solo_rosa[0]["saldo_anterior"]) == CERO, "le cobró a Rosa la deuda de Henri"
    _cuadra(_libro(client, h, "solo Rosa"), "17 rosa")
    # y ahora la de Henri
    q2h = _generar(client, h, Q2)[0]
    print(f"      Henri: deuda_vieja={q2h['saldo_anterior']}")
    assert D(q2h["saldo_anterior"]) == D("120000")
    _cuadra(_libro(client, h, "y Henri"), "17 henri")


# ===========================================================================
# 18. EL ANTICIPO SE MUEVE DE PERÍODO: la deuda tiene que seguirlo
# ===========================================================================
def test_18_mover_el_anticipo_al_periodo_siguiente_antes_de_que_se_cobre(
    client, base_datos
):
    h = auth_headers(client, "admin.a")
    print("\n===== 18. MOVER EL ANTICIPO DE PERÍODO =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    assert D(q1["le_queda_debiendo"]) == D("120000")

    # Se le corrige la fecha al anticipo y se pasa del fin del período: se suelta.
    r = client.put(f"{ANT}/{ant['id']}", json={"fecha": "2026-06-20"}, headers=h)
    assert r.status_code == 200, r.text
    q1_ahora = _leer(client, h, q1["id"])
    print(f"      Q1 tras mover el anticipo: ant={q1_ahora['anticipos']} "
          f"saldo={q1_ahora['saldo']}")
    assert D(q1_ahora["anticipos"]) == CERO
    assert D(q1_ahora["saldo"]) == D("180000")
    _cuadra(_libro(client, h, "anticipo movido a la quincena siguiente"), "18 movido")

    _recepcion(client, h, prov, "2026-06-25", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    print(f"      Q2: total={q2['valor_total']} ant={q2['anticipos']} "
          f"vieja={q2['saldo_anterior']} saldo={q2['saldo']}")
    assert D(q2["anticipos"]) == D("300000"), "el anticipo movido se perdió"
    assert D(q2["saldo_anterior"]) == CERO
    _cuadra(_libro(client, h, "Q2 con el anticipo movido"), "18 q2")
    assert _aprobar(client, h, q1["id"]).status_code == 200
    assert _pagar(client, h, q1["id"]).status_code == 200
    libro = _libro(client, h, "Q1 pagada")
    _cuadra(libro, "18 final")
    # leche 430.000; salieron 300.000 de anticipo + 180.000 de Q1 = 480.000, y el tercero
    # queda debiendo 50.000 (250.000 - 300.000).
    assert libro["leche"] == D("430000")
    assert libro["anticipos"] + libro["pagado"] - libro["leche"] == libro["debiendo"]


# ===========================================================================
# 19. RECALCULAR Y RECUADRAR LA QUE COBRÓ NO LE MUEVE LA DEUDA VIEJA
# ===========================================================================
def test_19_recalcular_la_que_cobro_conserva_la_deuda_vieja(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 19. RECALCULAR LA QUE COBRÓ =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    _generar(client, h, Q1)
    dia2 = _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]

    for vuelta in range(3):
        assert _recalcular(client, h, q2["id"]).status_code == 200
        q2_ahora = _leer(client, h, q2["id"])
        print(f"      vuelta {vuelta}: vieja={q2_ahora['saldo_anterior']} "
              f"saldo={q2_ahora['saldo']}")
        assert D(q2_ahora["saldo_anterior"]) == D("120000"), (
            "recalcular le movió la deuda vieja"
        )
        _cuadra(_libro(client, h, f"recalculada {vuelta}"), f"19 v{vuelta}")

    # Y el recuadre que entra por corregir un día tampoco.
    assert client.put(
        f"{REC}/{dia2['id']}", json={"cantidad_litros": "120"}, headers=h
    ).status_code == 200
    q2_ahora = _leer(client, h, q2["id"])
    print(f"      tras corregir el dia: total={q2_ahora['valor_total']} "
          f"vieja={q2_ahora['saldo_anterior']} saldo={q2_ahora['saldo']}")
    assert D(q2_ahora["saldo_anterior"]) == D("120000")
    assert D(q2_ahora["valor_total"]) == D("300000")
    assert D(q2_ahora["saldo"]) == D("180000")
    _cuadra(_libro(client, h, "dia corregido"), "19 final")
    assert _aprobar(client, h, q2["id"]).status_code == 200
    assert _pagar(client, h, q2["id"]).status_code == 200
    libro = _libro(client, h, "pagada")
    _cuadra(libro, "19 pagada")
    assert libro["anticipos"] + libro["pagado"] == libro["leche"]


# ===========================================================================
# 20. AGREGARLE UN DÍA NUEVO A UNA QUINCENA CUYA DEUDA YA SE COBRÓ
# ===========================================================================
def test_20_un_dia_nuevo_en_el_periodo_de_una_quincena_congelada(client, base_datos):
    """Se le anota una recepción con fecha del período de Q1 DESPUÉS de que Q1 quedó
    congelada. Ese día no puede entrar a Q1 (le cambiaría la deuda), y hay que ver
    si queda huérfano —leche recibida que ningún comprobante paga—."""
    h = auth_headers(client, "admin.a")
    print("\n===== 20. DÍA NUEVO EN UNA QUINCENA CONGELADA =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    nuevo = _recepcion(client, h, prov, "2026-06-10", "50", precio="1800")  # 90.000
    q1_ahora = _leer(client, h, q1["id"])
    print(f"      Q1 tras el dia nuevo: total={q1_ahora['valor_total']} "
          f"dias={len(q1_ahora['detalles'])}")
    assert D(q1_ahora["valor_total"]) == D("180000"), "el día nuevo entró a la congelada"
    # y recalcular para recogerlo rebota
    r = _recalcular(client, h, q1["id"])
    print(f"      recalcular Q1 -> {r.status_code}")
    assert r.status_code == 422

    # ¿Ese día queda huérfano? Se mide: leche recibida contra leche liquidada.
    todas = _todas(client, h)
    liquidada = sum((D(l["valor_total"]) for l in todas if l["estado"] != "anulada"), CERO)
    r = client.get(f"{REC}/filtrar/avanzado?page=1&page_size=200", headers=h)
    recibida = sum((D(x["valor_neto"]) for x in r.json()["items"]), CERO)
    huerfana = sum(
        (D(x["valor_neto"]) for x in r.json()["items"] if x["liquidacion_id"] is None), CERO
    )
    print(f"      leche RECIBIDA={recibida} LIQUIDADA={liquidada} SIN LIQUIDAR={huerfana}")
    assert liquidada + huerfana == recibida
    print(f"      (el dia nuevo de {huerfana} queda esperando: no se le paga hasta que "
          "una liquidacion lo recoja)")
    _cuadra(_libro(client, h, "con un dia sin liquidar"), "20")

    # Se libera Q1 (anulando Q2) y se recalcula: HOY EL DÍA NUEVO NO ENTRA —recalcular
    # solo vuelve a sumar los días que la liquidación ya tenía apartados—, así que la
    # única salida es anular Q1 y volver a generar el período.
    assert _anular(client, h, q2["id"]).status_code == 200
    assert _recalcular(client, h, q1["id"]).status_code == 200
    q1_ahora = _leer(client, h, q1["id"])
    print(f"      Q1 recalculada: total={q1_ahora['valor_total']} (el dia nuevo sigue afuera)")
    assert D(q1_ahora["valor_total"]) == D("180000")
    assert _anular(client, h, q1["id"]).status_code == 200
    q1b = _generar(client, h, Q1)[0]
    print(f"      Q1 regenerada: total={q1b['valor_total']}")
    assert D(q1b["valor_total"]) == D("270000"), "el día nuevo no entró ni regenerando"
    _cuadra(_libro(client, h, "Q1 con el dia nuevo"), "20 final")
