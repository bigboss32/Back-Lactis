"""AUDITORÍA (segunda pasada): los caminos raros de la plata de la leche.

Acá no se mide el flujo feliz —eso está en test_zzaudit_plata_de_la_leche.py— sino
lo que le pasa a la plata cuando el día se apaga, se muda de quincena, se anota
tarde, la quincena se genera al revés, o el comprobante se anula.

Cada prueba trae sus cifras calculadas a mano en el docstring.
"""
import pytest

from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_zzaudit_plata_de_la_leche import (
    ANT,
    API,
    CERO,
    D,
    Q1,
    Q2,
    Q3,
    REC,
    _abonar,
    _anticipo,
    _aprobar,
    _de,
    _generar,
    _leer,
    _pagar,
    _pdf,
    _proveedor,
    _recepcion,
    cuadra_el_comprobante,
    renglon,
)


def _grilla(client, h, desde, hasta):
    r = client.get(f"{REC}/grilla/quincena", params={"desde": desde, "hasta": hasta}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _avance(client, h, periodo, prov):
    r = client.post(
        f"{API}/previsualizar",
        json={
            "periodo_inicio": periodo[0],
            "periodo_fin": periodo[1],
            "tipo": "proveedor",
            "tercero_id": prov["id"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _todas(client, h):
    r = client.get(f"{API}?limit=200", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ===========================================================================
# 11. EL AVANCE ("¿cómo voy?") TIENE QUE DECIR LO MISMO QUE EL COMPROBANTE
# ===========================================================================
def test_zzaudit_11_el_avance_dice_lo_mismo_que_el_comprobante(client, base_datos):
    """Marleny queda debiendo $218.912,58 en Q1. En Q2 entrega 137,45 L a $1.833,33
    = $251.991,21 y no tiene anticipos nuevos.

        el avance dice:  saldo $251.991,21 y deuda pendiente $218.912,58
        lo que de verdad va a salir de la caja:
                         251.991,21 - 218.912,58 = $33.078,63
        y eso tiene que ser EXACTO el neto del comprobante que se genere.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")
    _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)
    q1 = _de(_generar(client, h, Q1), "Marleny R")
    _aprobar(client, h, q1["id"])

    _recepcion(client, h, prov, "2026-06-20", "137.45", precio="1833.33")
    avance = _avance(client, h, Q2, prov)
    assert len(avance) == 1, avance
    a = avance[0]
    assert D(a["valor_bruto"]) + D(a["bonificaciones"]) - D(a["descuentos"]) == D(a["valor_total"])
    suma_dias = sum((D(d["valor"]) for d in a["detalles"]), CERO)
    assert suma_dias == D(a["valor_total"]), f"el avance: los días suman {suma_dias} vs {a['valor_total']}"
    assert D(a["valor_total"]) - D(a["anticipos"]) == D(a["saldo"])
    assert D(a["deuda_pendiente"]) == D("218912.58"), a["deuda_pendiente"]
    va_a_salir = D(a["saldo"]) - D(a["deuda_pendiente"])
    assert va_a_salir == D("33078.63"), va_a_salir

    q2 = _leer(client, h, _de(_generar(client, h, Q2), "Marleny R")["id"])
    assert D(q2["neto_a_pagar"]) == va_a_salir, (
        f"el avance prometió {va_a_salir} y el comprobante dice {q2['neto_a_pagar']}"
    )
    cuadra_el_comprobante(q2, "Q2 tras el avance")


# ===========================================================================
# 12. UN DÍA QUE SE MUDA DE QUINCENA NO PIERDE NI DUPLICA PLATA
# ===========================================================================
def test_zzaudit_12_un_dia_que_se_muda_de_quincena(client, base_datos):
    """Q1 con dos días; a uno le corrigen la fecha y se pasa a Q2.

        02/06  137,45 L x 1.833,33 = $251.991,21
        05/06   44,23 L x 1.922,77 =  $85.044,12
        Q1 antes de mover  = $337.035,33
        se mueve el 05/06 al 20/06:
        Q1 después         = $251.991,21
        Q2 (nueva)         =  $85.044,12
        Q1 + Q2            = $337.035,33  — ni un peso perdido ni repetido
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    r2 = _recepcion(client, h, prov, "2026-06-05", "44.23", precio="1922.77")
    q1 = _de(_generar(client, h, Q1), "Henri C")
    assert D(_leer(client, h, q1["id"])["valor_total"]) == D("337035.33")

    mover = client.put(f"{REC}/{r2['id']}", json={"fecha": "2026-06-20"}, headers=h)
    assert mover.status_code == 200, mover.text
    q1 = _leer(client, h, q1["id"])
    assert D(q1["valor_total"]) == D("251991.21"), q1["valor_total"]
    assert len(q1["detalles"]) == 1
    cuadra_el_comprobante(q1, "Q1 sin el día mudado")

    q2 = _leer(client, h, _de(_generar(client, h, Q2), "Henri C")["id"])
    assert D(q2["valor_total"]) == D("85044.12"), q2["valor_total"]
    cuadra_el_comprobante(q2, "Q2 con el día mudado")
    assert D(q1["valor_total"]) + D(q2["valor_total"]) == D("337035.33")


# ===========================================================================
# 13. UN DÍA APAGADO EN BORRADOR Y DESPUÉS PAGADO: LA LECHE QUEDA PRESA
# ===========================================================================
# HALLAZGO — ESTA PRUEBA FALLA A PROPÓSITO: es la reproducción del defecto.
@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_13_dia_apagado_en_borrador_queda_preso_y_dice_pagado(client, base_datos):
    """Q1 con dos días; se APAGA uno y después se aprueba y se paga la quincena.

        02/06  137,45 L x 1.833,33 = $251.991,21   (se queda)
        05/06   44,23 L x 1.922,77 =  $85.044,12   (se apaga ANTES de aprobar)

        VALOR TOTAL pagado          = $251.991,21
        leche anotada en la grilla  = $337.035,33
        diferencia sin papel        =  $85.044,12

    MEDIDO CONTRA LA API: apagar un día NO le suelta la marca `liquidacion_id`, así
    que cuando esa liquidación se paga, el día queda:
      · con `leche_pagada: true` y `liquidacion_estado_leche: 'pagada'`, y el aviso
        "La leche de este día ya se le pagó a Henri C" — y no salió un peso por él;
      · con `estado` entre los `campos_bloqueados`, o sea que NO SE PUEDE VOLVER A
        ENCENDER (422): apagar un día por error antes de pagar es irreversible;
      · invisible para "Generar" (`sin_liquidar` salta a los que ya tienen
        `liquidacion_id`): la segunda corrida de Q1 devuelve [] y esos $85.044,12
        no entran en ningún comprobante ni aparecen en ninguna advertencia.

    SÍ HAY SALIDA, y se mide abajo para no exagerar el hallazgo: borrando el pago
    (permiso 'eliminar') la liquidación vuelve a 'aprobada', el día se puede
    encender (200) y el comprobante se recuadra en $337.035,33. Lo que está mal es
    que el sistema AFIRMA que esa leche ya se pagó —y el mensaje manda a corregirlo
    "por fuera del sistema", que es justo lo que no hay que hacer—.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    r2 = _recepcion(client, h, prov, "2026-06-05", "44.23", precio="1922.77")
    q1 = _de(_generar(client, h, Q1), "Henri C")

    assert client.put(f"{REC}/{r2['id']}", json={"estado": "inactivo"}, headers=h).status_code == 200
    q1 = _leer(client, h, q1["id"])
    assert D(q1["valor_total"]) == D("251991.21"), q1["valor_total"]
    _aprobar(client, h, q1["id"])
    _pagar(client, h, q1["id"])

    # El día apagado: ¿sigue apuntando a la liquidación PAGADA?
    dia = client.get(f"{REC}/{r2['id']}", headers=h).json()
    print("DIA APAGADO:", {k: dia[k] for k in (
        "estado", "liquidacion_id", "liquidacion_estado", "liquidacion_estado_leche",
        "leche_pagada", "campos_bloqueados", "candado_aviso")})

    encender = client.put(f"{REC}/{r2['id']}", json={"estado": "activo"}, headers=h)
    print("ENCENDER ->", encender.status_code, encender.text[:400])

    otra_corrida = _generar(client, h, Q1)
    print("SEGUNDA CORRIDA:", [(x["proveedor_nombre"], x["valor_total"]) for x in otra_corrida])

    vivas = [x for x in _todas(client, h) if x["estado"] != "anulada"]
    total_liquidado = sum((D(x["valor_total"]) for x in vivas), CERO)
    print("TOTAL LIQUIDADO:", total_liquidado, "vs leche anotada 337035.33")

    # ¿HAY SALIDA? Borrando el pago la liquidación vuelve a 'aprobada' y el día
    # tendría que poder encenderse. Se mide, para no exagerar el hallazgo.
    pagada = _leer(client, h, q1["id"])
    borrar = client.delete(f"{API}/{q1['id']}/pagos/{pagada['pagos'][0]['id']}", headers=h)
    print("BORRAR EL PAGO ->", borrar.status_code)
    encender2 = client.put(f"{REC}/{r2['id']}", json={"estado": "activo"}, headers=h)
    print("ENCENDER TRAS BORRAR EL PAGO ->", encender2.status_code, encender2.text[:250])
    print("LIQUIDACION TRAS ENCENDER:", _leer(client, h, q1["id"])["valor_total"])

    assert not dia["leche_pagada"], (
        "la leche del 05/06 ($85.044,12) NUNCA se pagó —el comprobante salió por "
        f"$251.991,21— y el sistema la marca como pagada: {dia['candado_aviso']}"
    )
    assert total_liquidado == D("337035.33"), (
        f"la leche anotada suma $337.035,33 y en los comprobantes hay {total_liquidado}"
    )


# ===========================================================================
# 14. GENERAR LA QUINCENA NUEVA ANTES QUE LA VIEJA (el orden que advierte el sistema)
# ===========================================================================
def test_zzaudit_14_generar_al_reves_cuanta_plata_sale_de_la_caja(client, base_datos):
    """El anticipo del 04/06 con las dos quincenas generadas AL REVÉS.

        Q1 01-15/06:  44,23 L x 1.833,33 = $81.088,19
        Q2 16-30/06: 137,45 L x 1.833,33 = $251.991,21
        anticipo 04/06 = $300.000,77   (leche total $333.079,40)

        Generando Q2 PRIMERO, el anticipo (fecha 04/06 <= 30/06) se le aplica a Q2:
            Q2 neto = 251.991,21 - 300.000,77 = -$48.009,56  (queda debiendo)
            Q1 neto =  81.088,19 -          0 =  $81.088,19  (se le paga completa)

        LA CUENTA DEL DUEÑO tiene que seguir cerrando igual:
            anticipos entregados + plata pagada - lo que el productor queda debiendo
            == leche liquidada
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")
    _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _recepcion(client, h, prov, "2026-06-20", "137.45", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)

    q2 = _leer(client, h, _de(_generar(client, h, Q2), "Marleny R")["id"])
    q1 = _leer(client, h, _de(_generar(client, h, Q1), "Marleny R")["id"])
    print("Q2 primero:", q2["valor_total"], q2["anticipos"], q2["neto_a_pagar"])
    print("Q1 después:", q1["valor_total"], q1["anticipos"], q1["neto_a_pagar"])
    cuadra_el_comprobante(q1, "Q1 al revés")
    cuadra_el_comprobante(q2, "Q2 al revés")

    _aprobar(client, h, q1["id"])
    _pagar(client, h, q1["id"])
    _aprobar(client, h, q2["id"])

    q1 = _leer(client, h, q1["id"])
    q2 = _leer(client, h, q2["id"])
    leche = D(q1["valor_total"]) + D(q2["valor_total"])
    anticipos = D(q1["anticipos"]) + D(q2["anticipos"])
    pagado = D(q1["pagado"]) + D(q2["pagado"])
    debe = D(q1["le_queda_debiendo"]) + D(q2["le_queda_debiendo"])
    assert leche == D("333079.40"), leche
    assert anticipos + pagado - debe == leche, (
        f"anticipos {anticipos} + pagado {pagado} - debe {debe} != leche {leche}"
    )

    # Y la deuda que quedó viva se le cobra en la siguiente, una sola vez.
    _recepcion(client, h, prov, "2026-07-03", "219.45", precio="1755.11")
    q3 = _leer(client, h, _de(_generar(client, h, Q3), "Marleny R")["id"])
    assert D(q3["saldo_anterior"]) == debe, f"{q3['saldo_anterior']} != {debe}"
    cuadra_el_comprobante(q3, "Q3 cobrando la deuda")


# ===========================================================================
# 15. EL COMPROBANTE ANULADO SIGUE DESCONTANDO UN ANTICIPO QUE YA SOLTÓ
# ===========================================================================
# HALLAZGO — ESTA PRUEBA FALLA A PROPÓSITO: es la reproducción del defecto.
@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_15_el_comprobante_anulado_descuenta_un_anticipo_que_solto(client, base_datos):
    """Q1: $81.088,19 de leche (44,23 L x $1.833,33) con anticipo de $300.000,77.
    Neto -$218.912,58, o sea que el productor le queda debiendo eso. Se ANULA.

    Al anular, `_soltar_lo_apartado` SUELTA el anticipo (`liquidacion_id` a nulo) y
    `anular` pone `saldo_anterior` en cero justamente "para que el resumen vuelva a
    cuadrar de arriba abajo". A la columna `anticipos` NO se le hace lo mismo, así
    que el papel anulado sigue imprimiendo:

        VALOR TOTAL            $81.088,19
        Anticipos aplicados  - $300.000,77   <- descuento sin tabla de detalle
        LE QUEDA DEBIENDO     $218.912,58    <- una deuda que ya no vive acá

    Y como el anticipo quedó suelto, el comprobante NUEVO del mismo período lo vuelve
    a descontar: el mismo $300.000,77 sale descontado en dos papeles y el mismo
    productor sale debiendo $218.912,58 en dos papeles.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Marleny R")
    _recepcion(client, h, prov, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, h, "2026-06-04", "300000.77", proveedor=prov)
    q1 = _de(_generar(client, h, Q1), "Marleny R")
    assert D(q1["anticipos"]) == D("300000.77")

    assert client.post(f"{API}/{q1['id']}/anular", headers=h).status_code == 200
    anulada = _leer(client, h, q1["id"])
    print("ANULADA:", {k: anulada[k] for k in (
        "estado", "valor_total", "anticipos", "saldo_anterior", "neto_a_pagar",
        "saldo", "le_queda_debiendo")})
    papel = _pdf(client, h, q1["id"])
    print("PAPEL ANULADO — Anticipos aplicados:", renglon(papel, "Anticipos aplicados"))
    print("PAPEL ANULADO — LE QUEDA DEBIENDO:",
          renglon(papel, "LE QUEDA DEBIENDO") if "LE QUEDA DEBIENDO" in papel else "(no sale)")
    # ANTES de anular el papel trae DOS veces $300.000,77 (el renglón del resumen y
    # la fila de la tabla "Anticipos aplicados"). Después de anular trae UNA sola:
    # queda el descuento y desaparece la tabla que lo explicaba.
    veces = papel.count("300.000,77")
    print("PAPEL ANULADO — veces que aparece $300.000,77:", veces)
    assert veces == 2, (
        "el papel anulado imprime «Anticipos aplicados - $300.000,77» pero ya no trae "
        "la tabla de anticipos que lo explica: es un descuento huérfano"
    )

    # La misma quincena se vuelve a generar y el anticipo reaparece allá.
    q1b = _leer(client, h, _de(_generar(client, h, Q1), "Marleny R")["id"])
    assert D(q1b["anticipos"]) == D("300000.77"), q1b["anticipos"]

    # NO CUESTA PLATA: la deuda se cobra UNA sola vez, porque `deudas_sin_cobrar`
    # salta a las anuladas. Se mide, para no exagerar el hallazgo.
    _aprobar(client, h, q1b["id"])
    _recepcion(client, h, prov, "2026-06-20", "137.45", precio="1833.33")
    q2 = _leer(client, h, _de(_generar(client, h, Q2), "Marleny R")["id"])
    assert D(q2["saldo_anterior"]) == D("218912.58"), (
        f"la deuda se cobró {q2['saldo_anterior']} y no una sola vez"
    )
    cuadra_el_comprobante(q2, "Q2 tras la anulada")

    # PERO EL PAPEL MIENTE: el anticipo aparece descontado en dos comprobantes.
    descontado = D(anulada["anticipos"]) + D(q1b["anticipos"])
    assert descontado == D("300000.77"), (
        f"el anticipo de $300.000,77 aparece descontado {descontado} entre el "
        f"comprobante anulado ({anulada['anticipos']}) y el nuevo ({q1b['anticipos']}), "
        f"y el anulado dice LE QUEDA DEBIENDO {anulada['le_queda_debiendo']} con la "
        "tabla de anticipos vacía"
    )


# ===========================================================================
# 16. BORRAR UN ABONO DEVUELVE EL SALDO EXACTO
# ===========================================================================
def test_zzaudit_16_borrar_un_abono_devuelve_el_saldo_exacto(client, base_datos):
    """Neto $251.991,21 abonado en tres tandas feas y con una corrección:

        abono 1  $44.230,07  -> saldo $207.761,14
        abono 2  $80.000,33  -> saldo $127.760,81
        abono 3  $12.345,67  -> saldo $115.415,14
        se borra el abono 2  -> pagado $56.575,74  saldo $195.415,47
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    liq = _de(_generar(client, h, Q1), "Henri C")
    _aprobar(client, h, liq["id"])
    lid = liq["id"]

    _abonar(client, h, lid, "2026-06-16", "44230.07")
    liq = _abonar(client, h, lid, "2026-06-17", "80000.33")
    pago2 = [p for p in liq["pagos"] if D(p["valor"]) == D("80000.33")][0]
    liq = _abonar(client, h, lid, "2026-06-18", "12345.67")
    assert D(liq["saldo"]) == D("115415.14"), liq["saldo"]
    cuadra_el_comprobante(_leer(client, h, lid), "tres abonos")

    r = client.delete(f"{API}/{lid}/pagos/{pago2['id']}", headers=h)
    assert r.status_code == 200, r.text
    tras = _leer(client, h, lid)
    assert D(tras["pagado"]) == D("56575.74"), tras["pagado"]
    assert D(tras["saldo"]) == D("195415.47"), tras["saldo"]
    assert tras["estado"] == "parcial"
    cuadra_el_comprobante(tras, "tras borrar un abono")

    # Y borrando TODOS los abonos vuelve a 'aprobada' con el neto entero.
    for pago in list(tras["pagos"]):
        assert client.delete(f"{API}/{lid}/pagos/{pago['id']}", headers=h).status_code == 200
    tras = _leer(client, h, lid)
    assert D(tras["pagado"]) == CERO
    assert D(tras["saldo"]) == D("251991.21")
    assert tras["estado"] == "aprobada", tras["estado"]
    cuadra_el_comprobante(tras, "sin abonos")


# ===========================================================================
# 17. MULTIEMPRESA: la plata de una quesera no se mezcla con la de la otra
# ===========================================================================
def test_zzaudit_17_dos_queseras_no_se_prestan_anticipos_ni_deudas(client, base_datos):
    """Las dos empresas de la misma instalación, con un productor del mismo nombre.

        Quesera A: 44,23 L x 1.833,33 = $81.088,19 con anticipo de $300.000,77
        Quesera B: 44,23 L x 1.833,33 = $81.088,19 SIN anticipos

    La de B no puede quedar con el anticipo de A, ni cobrarle a su productor la
    deuda que el de A dejó.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prov_a = _proveedor(client, ha, "Henri C")
    prov_b = _proveedor(client, hb, "Henri C")
    _recepcion(client, ha, prov_a, "2026-06-04", "44.23", precio="1833.33")
    _recepcion(client, hb, prov_b, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, ha, "2026-06-04", "300000.77", proveedor=prov_a)

    a1 = _leer(client, ha, _de(_generar(client, ha, Q1), "Henri C")["id"])
    b1 = _leer(client, hb, _de(_generar(client, hb, Q1), "Henri C")["id"])
    assert D(a1["anticipos"]) == D("300000.77")
    assert D(b1["anticipos"]) == CERO, b1["anticipos"]
    assert D(b1["neto_a_pagar"]) == D("81088.19")
    cuadra_el_comprobante(a1, "A")
    cuadra_el_comprobante(b1, "B")

    _aprobar(client, ha, a1["id"])
    _aprobar(client, hb, b1["id"])
    _pagar(client, hb, b1["id"])

    _recepcion(client, ha, prov_a, "2026-06-20", "137.45", precio="1833.33")
    _recepcion(client, hb, prov_b, "2026-06-20", "137.45", precio="1833.33")
    a2 = _leer(client, ha, _de(_generar(client, ha, Q2), "Henri C")["id"])
    b2 = _leer(client, hb, _de(_generar(client, hb, Q2), "Henri C")["id"])
    assert D(a2["saldo_anterior"]) == D("218912.58"), a2["saldo_anterior"]
    assert D(b2["saldo_anterior"]) == CERO, (
        f"la quesera B le cobró una deuda que no es suya: {b2['saldo_anterior']}"
    )
    # Y ninguna de las dos ve la liquidación de la otra.
    assert client.get(f"{API}/{a1['id']}", headers=hb).status_code == 404
    assert client.get(f"{API}/{b1['id']}", headers=ha).status_code == 404


# ===========================================================================
# 18. LA SUMA DE ANTICIPOS QUE MUESTRA LA PANTALLA
# ===========================================================================
def test_zzaudit_18_la_suma_de_anticipos_cuadra_con_el_listado(client, base_datos):
    """Siete anticipos con centavos feos. La cifra grande de la pantalla tiene que
    ser EXACTAMENTE la suma de los renglones del listado.

        1.833,33 + 242,76 + 44.230,07 + 120.000,55 + 95.000,45
        + 12.345,67 + 137,45 = $273.790,28
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    valores = ["1833.33", "242.76", "44230.07", "120000.55", "95000.45", "12345.67", "137.45"]
    for i, v in enumerate(valores):
        _anticipo(client, h, f"2026-06-{i + 1:02d}", v, proveedor=prov)

    listado = client.get(f"{ANT}?limit=100", headers=h).json()
    renglones = sum((D(a["valor"]) for a in listado["items"]), CERO)
    assert renglones == D("273790.28"), renglones

    r = client.get(f"{ANT}/totales/suma", headers=h)
    assert r.status_code == 200, r.text
    grande = D(repr(r.json()) if isinstance(r.json(), float) else r.json())
    print("SUMA QUE DEVUELVE LA API:", r.text)
    assert grande == D("273790.28"), (
        f"la cifra grande dice {grande} y los renglones suman {renglones}"
    )


# ===========================================================================
# 19. EL DÍA ANOTADO TARDE DENTRO DE UNA QUINCENA YA PAGADA
# ===========================================================================
def test_zzaudit_19_dia_anotado_tarde_en_una_quincena_ya_pagada(client, base_datos):
    """Q1 pagada por $251.991,21. Aparece un día olvidado del 07/06:

        07/06  44,23 L x 1.922,77 = $85.044,12

    Esa leche se le tiene que poder pagar al productor por algún camino, y una
    sola vez.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    q1 = _de(_generar(client, h, Q1), "Henri C")
    _aprobar(client, h, q1["id"])
    _pagar(client, h, q1["id"])

    _recepcion(client, h, prov, "2026-06-07", "44.23", precio="1922.77")
    generadas = _generar(client, h, Q1)
    print("SEGUNDA CORRIDA DE Q1:", [(x["proveedor_nombre"], x["valor_total"]) for x in generadas])
    assert len(generadas) == 1, "el día olvidado se quedó sin comprobante"
    nueva = _leer(client, h, generadas[0]["id"])
    assert D(nueva["valor_total"]) == D("85044.12"), nueva["valor_total"]
    cuadra_el_comprobante(nueva, "el día olvidado")

    vivas = [x for x in _todas(client, h) if x["estado"] != "anulada"]
    total = sum((D(x["valor_total"]) for x in vivas), CERO)
    assert total == D("337035.33"), f"la leche se contó {total}"


# ===========================================================================
# 20. LOS ANTICIPOS DEL COMPROBANTE SUMAN EL RENGLÓN "ANTICIPOS APLICADOS"
# ===========================================================================
def test_zzaudit_20_la_tabla_de_anticipos_suma_el_renglon(client, base_datos):
    """Tres anticipos feos contra una quincena; la tabla del papel tiene que sumar
    exacto el renglón del resumen.

        120.000,55 + 95.000,45 + 1.833,33 = $216.834,33
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "137.45", precio="1833.33")
    _recepcion(client, h, prov, "2026-06-11", "219.45", precio="1755.11")
    for fecha, valor in (("2026-06-03", "120000.55"), ("2026-06-08", "95000.45"),
                         ("2026-06-14", "1833.33")):
        _anticipo(client, h, fecha, valor, proveedor=prov)
    liq = _leer(client, h, _de(_generar(client, h, Q1), "Henri C")["id"])
    assert D(liq["anticipos"]) == D("216834.33"), liq["anticipos"]
    cuadra_el_comprobante(liq, "tres anticipos")

    papel = _pdf(client, h, liq["id"])
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    saldo = renglon(papel, "SALDO A PAGAR")
    assert total + anticipos == saldo, f"papel: {total} {anticipos} {saldo}"
    # Los tres renglones de la tabla de anticipos tienen que estar impresos.
    for texto in ("120.000,55", "95.000,45", "1.833,33"):
        assert texto in papel, f"el papel no trae el anticipo de {texto}"
