"""CAMINO 2 — QUE LA QUINCENA SIGUIENTE SÍ LO RECOJA, Y UNA SOLA VEZ.

El comprobante v2 que el productor tiene en la mano dice, con todas sus letras, que el
adelanto que la corrección le sacó "se le descuenta en la siguiente". Toda la marca
nueva (`Anticipo.soltado_de_liquidacion_id`) existe para respaldar esa frase: traba la
pantalla de anticipos para que nadie borre ni rebaje esa plata por la puerta de al lado.

Pero un candado solo vale si el camino que promete SÍ está abierto. Este archivo mide el
otro lado de la marca: que el adelanto soltado vuelva a aparecer como pendiente, que la
quincena siguiente se lo descuente UNA SOLA VEZ, que al recogerlo la marca deje de
estorbar, y —la prueba de verdad— que la CAJA CUADRE de punta a punta.

LAS CIFRAS ESTÁN CALCULADAS A MANO EN CADA DOCSTRING, nunca con el mismo código que se
prueba. Se escogieron redondas a propósito para poder rehacerlas en una servilleta.

LA CADENA COMPLETA que mide `test_cuadre_de_caja_...`:
    Q1: 250 L x $2.000 = $500.000, con adelanto de $300.000 ya entregado en la mano.
        neto $200.000 -> se paga.
    Se corrige Q1 y SALE el adelanto: neto $500.000, pagado $200.000, saldo $300.000
        -> se paga la diferencia.
    Q2: 200 L x $2.000 = $400.000, recoge el adelanto de $300.000 -> neto $100.000
        -> se paga.
    LECHE ENTREGADA  = $500.000 + $400.000 = $900.000
    SALIÓ DE LA CAJA = $300.000 (adelanto en la mano) + $200.000 + $300.000 + $100.000
                     = $900.000
    Las dos cifras tienen que ser IGUALES AL CENTAVO.
"""
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
V = "/api/v1"

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
Q3 = ("2026-07-01", "2026-07-15")


def D(v):
    return Decimal(str(v))


CERO = D(0)


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre="Libardo", precio="2000"):
    r = client.post(
        f"{V}/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros):
    r = client.post(
        f"{V}/recepciones",
        json={"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, prov, fecha, valor, obs="para la droga"):
    r = client.post(
        f"{V}/anticipos",
        json={
            "tipo": "proveedor",
            "proveedor_id": prov["id"],
            "fecha": fecha,
            "valor": str(valor),
            "observaciones": obs,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, periodo):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _de(generadas, prov):
    encontradas = [x for x in generadas if x["proveedor_id"] == prov["id"]]
    assert len(encontradas) == 1, f"se esperaba una sola de ese proveedor: {generadas}"
    return encontradas[0]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _aprobar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _pagar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/pagar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _recalcular(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/recalcular", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _corregir(client, h, liq_id, cuerpo):
    return client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)


def _anticipo_leido(client, h, anticipo_id):
    r = client.get(f"{V}/anticipos/{anticipo_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _anticipos_listados(client, h):
    r = client.get(f"{V}/anticipos", headers=h, params={"size": 100})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _cuadra(liq, donde=""):
    """LAS DOS RESTAS QUE EL DUEÑO HACE CON CALCULADORA SOBRE EL PAPEL."""
    neto = D(liq["neto_a_pagar"])
    assert neto == D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq["saldo_anterior"]), (
        f"{donde}: neto {neto} != total {liq['valor_total']} - anticipos "
        f"{liq['anticipos']} - saldo_anterior {liq['saldo_anterior']}"
    )
    assert neto == D(liq["pagado"]) + D(liq["saldo"]), (
        f"{donde}: neto {neto} != pagado {liq['pagado']} + saldo {liq['saldo']}"
    )


def _q1_pagada_con_adelanto(client, h, prov, *, adelanto="300000", litros="250"):
    """Q1 generada, aprobada y PAGADA con el adelanto ya descontado.

    250 L x $2.000 = $500.000 de leche, menos $300.000 de adelanto = $200.000 de neto,
    que es lo que salió de la caja al pagarla.
    """
    _recepcion(client, h, prov, "2026-06-02", litros)
    ant = _anticipo(client, h, prov, "2026-06-03", adelanto)
    liq = _de(_generar(client, h, Q1), prov)
    _aprobar(client, h, liq["id"])
    pagada = _pagar(client, h, liq["id"])
    assert D(pagada["valor_total"]) == D("500000.00"), pagada["valor_total"]
    assert D(pagada["anticipos"]) == D(adelanto), pagada["anticipos"]
    assert pagada["estado"] == "pagada", pagada["estado"]
    _cuadra(pagada, "Q1 recién pagada")
    return ant, pagada


def _soltar(client, h, liq_id, anticipo_id, motivo="el adelanto no iba en esta quincena"):
    r = _corregir(client, h, liq_id, {"motivo": motivo, "anticipos_a_soltar": [anticipo_id]})
    assert r.status_code == 200, r.text
    return r.json()


# ===========================================================================
# 1 — EL SOLTADO VUELVE A LA FILA: aparece pendiente y la siguiente lo recoge
# ===========================================================================
def test_el_adelanto_soltado_lo_recoge_la_quincena_siguiente_al_generarla(client, base_datos):
    """La promesa impresa, medida.

    Q1 (01–15/06): 250 L x $2.000 = $500.000, adelanto del 03/06 por $300.000.
        neto $200.000, pagada.
    Se corrige Q1 y SALE el adelanto:
        VALOR TOTAL $500.000, anticipos $0, neto $500.000, pagado $200.000,
        saldo $300.000, estado 'parcial'.
    Q2 (16–30/06): 200 L x $2.000 = $400.000. Al GENERARLA tiene que recoger el
    adelanto suelto:
        VALOR TOTAL $400.000, anticipos $300.000, neto $100.000.

    `pendientes_de` filtra por `liquidacion_id IS NULL` y NO mira la marca nueva: esta
    prueba es la que dice si esa decisión deja la promesa en pie.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    ant, pagada = _q1_pagada_con_adelanto(client, h, prov)

    corregida = _soltar(client, h, pagada["id"], ant["id"])
    print("\n===== Q1 CORREGIDA (el adelanto salió) =====")
    print(f"  total={corregida['valor_total']} anticipos={corregida['anticipos']} "
          f"neto={corregida['neto_a_pagar']} pagado={corregida['pagado']} "
          f"saldo={corregida['saldo']} estado={corregida['estado']}")
    assert D(corregida["anticipos"]) == CERO
    assert D(corregida["neto_a_pagar"]) == D("500000.00")
    assert D(corregida["saldo"]) == D("300000.00")
    _cuadra(corregida, "Q1 corregida")

    suelto = _anticipo_leido(client, h, ant["id"])
    assert suelto["liquidacion_id"] is None, "el soltado tiene que quedar sin liquidación"
    assert suelto["bloqueado"] is True, (
        "mientras está suelto por una corrección la pantalla lo tiene que mostrar trabado"
    )

    # Y AHORA LA QUINCENA SIGUIENTE.
    _recepcion(client, h, prov, "2026-06-20", "200")
    q2 = _de(_generar(client, h, Q2), prov)
    print("===== Q2 RECIÉN GENERADA =====")
    print(f"  total={q2['valor_total']} anticipos={q2['anticipos']} "
          f"neto={q2['neto_a_pagar']} saldo_anterior={q2['saldo_anterior']}")
    assert D(q2["valor_total"]) == D("400000.00"), q2["valor_total"]
    assert D(q2["anticipos"]) == D("300000.00"), (
        "LA PROMESA DEL PAPEL: el adelanto soltado tiene que aparecer descontado en la "
        f"quincena siguiente, y quedó en {q2['anticipos']}"
    )
    assert D(q2["neto_a_pagar"]) == D("100000.00"), q2["neto_a_pagar"]
    _cuadra(q2, "Q2 generada")

    recogido = _anticipo_leido(client, h, ant["id"])
    assert recogido["liquidacion_id"] == q2["id"], "quedó marcado en la quincena que lo recogió"


# ===========================================================================
# 2 — UNA SOLA VEZ: ni el recálculo ni la quincena de más lo vuelven a cobrar
# ===========================================================================
def test_el_soltado_se_descuenta_una_sola_vez_aunque_se_recalcule_y_haya_tercera_quincena(
    client, base_datos
):
    """Un adelanto es plata entregada UNA vez; descontarla dos veces es robarle al
    productor $300.000.

    Después de que Q2 lo recoge ($400.000 − $300.000 = $100.000), se oprime Recalcular
    dos veces seguidas —el botón doble, el reintento del navegador— y la cifra tiene
    que quedar clavada en $300.000. Y la quincena Q3 (01–15/07), con 50 L x $2.000 =
    $100.000, tiene que salir con ANTICIPOS $0.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    ant, pagada = _q1_pagada_con_adelanto(client, h, prov)
    _soltar(client, h, pagada["id"], ant["id"])
    _pagar(client, h, pagada["id"])  # se paga la diferencia de Q1

    _recepcion(client, h, prov, "2026-06-20", "200")
    q2 = _de(_generar(client, h, Q2), prov)
    assert D(q2["anticipos"]) == D("300000.00")

    for vuelta in (1, 2):
        q2 = _recalcular(client, h, q2["id"])
        print(f"  recálculo {vuelta}: anticipos={q2['anticipos']} neto={q2['neto_a_pagar']}")
        assert D(q2["anticipos"]) == D("300000.00"), (
            f"el recálculo {vuelta} descontó el adelanto otra vez: {q2['anticipos']}"
        )
        assert D(q2["neto_a_pagar"]) == D("100000.00"), q2["neto_a_pagar"]
        _cuadra(q2, f"Q2 recalculada {vuelta}")

    _aprobar(client, h, q2["id"])
    _pagar(client, h, q2["id"])

    _recepcion(client, h, prov, "2026-07-05", "50")
    q3 = _de(_generar(client, h, Q3), prov)
    print(f"  Q3: total={q3['valor_total']} anticipos={q3['anticipos']} neto={q3['neto_a_pagar']}")
    assert D(q3["valor_total"]) == D("100000.00"), q3["valor_total"]
    assert D(q3["anticipos"]) == CERO, (
        f"Q3 volvió a descontar un adelanto que Q2 ya cobró: {q3['anticipos']}"
    )
    _cuadra(q3, "Q3")


# ===========================================================================
# 3 — EL BORRADOR QUE YA EXISTÍA: `_aplicar_anticipos_pendientes` al recalcular
# ===========================================================================
def test_el_borrador_ya_generado_recoge_el_soltado_al_recalcular(client, base_datos):
    """El orden real de la finca: la quincena siguiente ya estaba generada en borrador
    CUANDO el dueño se dio cuenta del error y corrigió la anterior.

    Q2 se genera ANTES de soltar: 200 L x $2.000 = $400.000, anticipos $0, neto $400.000.
    Después se corrige Q1 y sale el adelanto de $300.000.
    Se oprime Recalcular en Q2 y tiene que quedar: anticipos $300.000, neto $100.000.

    Si esto no pasara, el adelanto quedaría trabado por la marca nueva en la pantalla de
    anticipos y sin nadie que lo recoja: plata muerta.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    ant, pagada = _q1_pagada_con_adelanto(client, h, prov)

    _recepcion(client, h, prov, "2026-06-20", "200")
    q2 = _de(_generar(client, h, Q2), prov)
    assert D(q2["valor_total"]) == D("400000.00")
    assert D(q2["anticipos"]) == CERO, "todavía no se ha soltado nada"

    _soltar(client, h, pagada["id"], ant["id"])

    q2 = _recalcular(client, h, q2["id"])
    print(f"  Q2 recalculada: anticipos={q2['anticipos']} neto={q2['neto_a_pagar']}")
    assert D(q2["anticipos"]) == D("300000.00"), (
        f"el borrador que ya existía no recogió el adelanto soltado: {q2['anticipos']}"
    )
    assert D(q2["neto_a_pagar"]) == D("100000.00")
    _cuadra(q2, "Q2 recalculada")


# ===========================================================================
# 4 — AL RECOGERLO, LA MARCA DEJA DE ESTORBAR
# ===========================================================================
def test_recogido_por_la_siguiente_el_adelanto_vuelve_a_comportarse_normal(client, base_datos):
    """La marca vieja no puede seguir mandando después de que el adelanto ya volvió a
    estar dentro de un comprobante.

    Q2 lo recoge en BORRADOR y sin un peso pagado: en ese estado el candado normal de los
    anticipos SÍ deja corregirlo (es la regla de `_exigir_no_pagado`: se traba cuando ya
    salió plata, no cuando apenas se generó). Entonces:
      · la pantalla lo tiene que mostrar DESTRABADO (`bloqueado` en falso);
      · corregirle el valor de $300.000 a $250.000 tiene que pasar, y Q2 tiene que quedar
        recuadrada: $400.000 − $250.000 = $150.000 de neto.

    Y después, al PAGAR Q2, vuelve a trabarse —pero por SU liquidación, con el mensaje de
    la quincena pagada, no con el de la marca vieja—.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    ant, pagada = _q1_pagada_con_adelanto(client, h, prov)
    _soltar(client, h, pagada["id"], ant["id"])
    _pagar(client, h, pagada["id"])

    _recepcion(client, h, prov, "2026-06-20", "200")
    q2 = _de(_generar(client, h, Q2), prov)
    assert D(q2["anticipos"]) == D("300000.00")

    recogido = _anticipo_leido(client, h, ant["id"])
    print(f"\n  recogido: liquidacion_id={recogido['liquidacion_id']} "
          f"estado={recogido['liquidacion_estado']} bloqueado={recogido['bloqueado']}")
    assert recogido["bloqueado"] is False, (
        "recogido por un borrador sin pagos, el adelanto tiene que quedar destrabado: la "
        "marca vieja no puede seguir escondiéndole los botones al dueño"
    )
    # Y la LISTA (que es la pantalla de verdad) dice lo mismo que el detalle.
    en_lista = [a for a in _anticipos_listados(client, h) if a["id"] == ant["id"]]
    assert en_lista and en_lista[0]["bloqueado"] is False, en_lista

    r = client.put(
        f"{V}/anticipos/{ant['id']}",
        json={"valor": "250000"},
        headers=h,
    )
    assert r.status_code == 200, (
        f"la marca vieja siguió trabando un adelanto que ya está dentro de un borrador: {r.text}"
    )

    q2 = _leer(client, h, q2["id"])
    print(f"  Q2 recuadrada: anticipos={q2['anticipos']} neto={q2['neto_a_pagar']}")
    assert D(q2["anticipos"]) == D("250000.00"), q2["anticipos"]
    assert D(q2["neto_a_pagar"]) == D("150000.00"), q2["neto_a_pagar"]
    _cuadra(q2, "Q2 con el adelanto corregido")

    # Y AL PAGARLA, se traba otra vez: pero por SU liquidación, no por la marca vieja.
    _aprobar(client, h, q2["id"])
    _pagar(client, h, q2["id"])
    trabado = _anticipo_leido(client, h, ant["id"])
    assert trabado["bloqueado"] is True
    r = client.delete(f"{V}/anticipos/{ant['id']}", headers=h)
    assert r.status_code == 422, r.text
    detalle = r.json()["error"]["detail"]
    print(f"  mensaje al borrarlo ya pagado: {detalle}")
    assert "ya se pagó" in detalle or "ya se pago" in detalle, detalle


# ===========================================================================
# 5 — EL CUADRE DE CAJA DE LA CADENA COMPLETA (la prueba de verdad)
# ===========================================================================
def test_cuadre_de_caja_de_punta_a_punta_la_leche_es_igual_a_lo_que_salio_de_la_caja(
    client, base_datos
):
    """LA CUENTA ENTERA, sumada como la suma el dueño con la calculadora.

    LO QUE EL PRODUCTOR ENTREGÓ EN LECHE
        02/06  250 L x $2.000 ......... $500.000
        20/06  200 L x $2.000 ......... $400.000
        ------------------------------------------
        TOTAL LECHE .................... $900.000

    LO QUE SALIÓ DE LA CAJA, en el orden en que salió
        03/06  adelanto en la mano ..... $300.000
               pago de Q1 (neto) ....... $200.000   ($500.000 − $300.000)
        -- se corrige Q1 y el adelanto SALE de ese comprobante --
               diferencia de Q1 ........ $300.000   (el saldo que quedó en 'parcial')
        -- Q2 recoge el adelanto --
               pago de Q2 .............. $100.000   ($400.000 − $300.000)
        ------------------------------------------
        TOTAL CAJA ..................... $900.000

    LAS DOS CIFRAS TIENEN QUE SER IGUALES AL CENTAVO. Si el adelanto no lo recogiera
    la quincena siguiente, la caja habría entregado $1.200.000 por $900.000 de leche; si
    lo recogiera dos veces, $600.000. Esta es la prueba que dice si la promesa del papel
    se cumple con plata y no solo con palabras.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)

    adelanto = D("300000.00")
    ant, q1 = _q1_pagada_con_adelanto(client, h, prov)
    caja = adelanto + D(q1["pagado"])
    print("\n===== CADENA COMPLETA =====")
    print(f"  adelanto en la mano ... {adelanto}")
    print(f"  pago de Q1 ............ {q1['pagado']}")
    assert D(q1["pagado"]) == D("200000.00"), q1["pagado"]

    # Se corrige Q1: el adelanto no iba ahí. Sube el neto y queda un saldo por pagar.
    q1 = _soltar(client, h, q1["id"], ant["id"])
    assert D(q1["neto_a_pagar"]) == D("500000.00"), q1["neto_a_pagar"]
    assert D(q1["saldo"]) == D("300000.00"), q1["saldo"]
    assert q1["estado"] == "parcial", q1["estado"]
    _cuadra(q1, "Q1 corregida")
    antes = D(q1["pagado"])
    q1 = _pagar(client, h, q1["id"])
    diferencia = D(q1["pagado"]) - antes
    caja += diferencia
    print(f"  diferencia de Q1 ...... {diferencia}")
    assert diferencia == D("300000.00"), diferencia
    assert D(q1["saldo"]) == CERO and q1["estado"] == "pagada", q1
    _cuadra(q1, "Q1 pagada del todo")

    # La quincena siguiente recoge el adelanto: esa es la promesa del comprobante v2.
    _recepcion(client, h, prov, "2026-06-20", "200")
    q2 = _de(_generar(client, h, Q2), prov)
    assert D(q2["valor_total"]) == D("400000.00"), q2["valor_total"]
    assert D(q2["anticipos"]) == adelanto, q2["anticipos"]
    assert D(q2["saldo_anterior"]) == CERO, (
        "Q1 cerró en $0: no hay deuda arrastrada que confunda el cuadre"
    )
    _aprobar(client, h, q2["id"])
    q2 = _pagar(client, h, q2["id"])
    caja += D(q2["pagado"])
    print(f"  pago de Q2 ............ {q2['pagado']}")
    assert D(q2["pagado"]) == D("100000.00"), q2["pagado"]
    _cuadra(q2, "Q2 pagada")

    leche = D(q1["valor_total"]) + D(q2["valor_total"])
    print(f"  ----------------------------------")
    print(f"  LECHE ENTREGADA ....... {leche}")
    print(f"  SALIÓ DE LA CAJA ...... {caja}")
    assert leche == D("900000.00"), leche
    assert caja == leche, (
        f"LA CAJA NO CUADRA: la leche valió {leche} y de la caja salieron {caja} "
        f"(diferencia {caja - leche})"
    )

    # Y el adelanto quedó descontado UNA sola vez, en Q2 y en ninguna otra.
    final = _anticipo_leido(client, h, ant["id"])
    assert final["liquidacion_id"] == q2["id"], final


# ===========================================================================
# 6 — EL CASO FEO: el adelanto soltado quedó con fecha ANTERIOR al período
# ===========================================================================
def test_el_soltado_con_fecha_muy_anterior_al_periodo_de_la_siguiente_si_lo_recoge(
    client, base_datos
):
    """`pendientes_de` filtra `fecha <= hasta` y NO tiene cota por abajo.

    El adelanto del 03/06 es de la primera quincena de junio; la quincena que lo tiene
    que recoger es la de JULIO (01–15/07), o sea casi un mes después y con el período
    entero por delante. Si hubiera cota por abajo —`fecha >= periodo_inicio`—, el
    adelanto se quedaría suelto para siempre, trabado por la marca nueva y sin nadie
    que lo cobre: $300.000 muertos.

    Las cifras: Q3 son 300 L x $2.000 = $600.000 menos el adelanto de $300.000 =
    $300.000 de neto.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    ant, pagada = _q1_pagada_con_adelanto(client, h, prov)
    _soltar(client, h, pagada["id"], ant["id"])
    _pagar(client, h, pagada["id"])

    # No hay leche en la segunda quincena de junio: esa quincena no existe.
    generadas = _generar(client, h, Q2)
    assert not [x for x in generadas if x["proveedor_id"] == prov["id"]], (
        "sin recepciones no se genera quincena, y el adelanto sigue esperando"
    )
    suelto = _anticipo_leido(client, h, ant["id"])
    assert suelto["liquidacion_id"] is None
    assert suelto["bloqueado"] is True, "sigue trabado mientras nadie lo recoge"

    # Un mes después, la de julio.
    _recepcion(client, h, prov, "2026-07-05", "300")
    q3 = _de(_generar(client, h, Q3), prov)
    print(f"\n  Q3 (01–15/07): total={q3['valor_total']} anticipos={q3['anticipos']} "
          f"neto={q3['neto_a_pagar']}")
    assert D(q3["valor_total"]) == D("600000.00"), q3["valor_total"]
    assert D(q3["anticipos"]) == D("300000.00"), (
        f"el adelanto del 03/06 se quedó sin recoger en la quincena de julio: "
        f"{q3['anticipos']}"
    )
    assert D(q3["neto_a_pagar"]) == D("300000.00"), q3["neto_a_pagar"]
    _cuadra(q3, "Q3")


# ===========================================================================
# 7 — EL CALLEJÓN SIN SALIDA: la corrección manda a una puerta que la marca cerró
# ===========================================================================
def test_la_salida_que_promete_la_correccion_esta_cerrada_por_la_marca_nueva(
    client, base_datos
):
    """EL ADELANTO ESTABA MAL DIGITADO Y ADEMÁS NO IBA EN ESA QUINCENA.

    Las cifras: al productor se le dieron $30.000 el 03/06, pero la secretaria tecleó
    $300.000, y el adelanto tampoco correspondía a esa quincena. Q1: 250 L x $2.000 =
    $500.000 − $300.000 = $200.000 de neto, pagada.

    EL DUEÑO INTENTA ARREGLARLO EN UN SOLO PASO —sacarlo y corregirle el valor— y la
    corrección lo rebota con una instrucción textual:

        «sáquelo, y corríjalo desde la pantalla de anticipos»

    HACE EXACTAMENTE LO QUE LE DICEN: lo saca con la corrección. Y entonces la pantalla
    de anticipos lo rebota con 422 por la marca nueva.

    Queda encerrado: la única puerta que la corrección le nombró está cerrada con llave
    por la marca. Un candado que deja al dueño sin camino es tan malo como uno que falta,
    y aquí el propio sistema lo mandó a estrellarse contra él.

    ESTA PRUEBA PASA A VERDE CUANDO: o el mensaje de la corrección nombra una salida que
    de verdad existe, o `corregir_pagada` deja sacar y corregir el valor en la misma
    operación (que es lo que el dueño pidió), o la pantalla de anticipos deja corregir el
    VALOR del soltado (el borrado y la fecha son los que roban plata; rebajarlo de
    $300.000 a los $30.000 reales es justo lo que hay que poder hacer).
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    ant, pagada = _q1_pagada_con_adelanto(client, h, prov)

    # (1) EN UN SOLO PASO: sacarlo y dejarlo en los $30.000 reales.
    r = _corregir(
        client,
        h,
        pagada["id"],
        {
            "motivo": "el adelanto fue de $30.000 y no iba en esta quincena",
            "anticipos_a_soltar": [ant["id"]],
            "valores_de_anticipos": [{"anticipo_id": ant["id"], "valor": "30000"}],
        },
    )
    print(f"\n  (1) sacar y corregir a la vez -> {r.status_code}")
    # EN UN SOLO PASO, que es lo que el dueño pidió. Antes rebotaba con «sáquelo, y
    # corríjalo desde la pantalla de anticipos» — y esa pantalla lo rebotaba a su vez por
    # la marca del adelanto ya impreso: el sistema lo mandaba a estrellarse contra una
    # puerta que él mismo había cerrado. Esta prueba lo midió, y por eso se abrió.
    assert r.status_code == 200, r.text

    # LA QUINCENA: el adelanto salió, así que el neto sube a los $500.000 completos.
    q1 = _leer(client, h, pagada["id"])
    print(f"      Q1: anticipos {q1['anticipos']} . neto {q1['neto_a_pagar']} "
          f". pagado {q1['pagado']} . saldo {q1['saldo']} . v{q1['version']}")
    assert D(q1["anticipos"]) == CERO
    _cuadra(q1, "Q1 tras sacar y corregir")

    # Y EL ADELANTO QUEDÓ EN LOS $30.000 QUE DE VERDAD SE ENTREGARON: suelto, y trabado
    # contra la pantalla de anticipos —que es lo correcto, ya salió impreso—.
    suelto = _anticipo_leido(client, h, ant["id"])
    print(f"      el adelanto: {suelto['valor']} . bloqueado {suelto.get('bloqueado')}")
    assert D(suelto["valor"]) == D("30000"), "no quedó en los $30.000 que se entregaron"
    assert suelto.get("liquidacion_id") is None

    # Y LA QUINCENA SIGUIENTE LE DESCUENTA LOS $30.000 REALES, no los $300.000 tecleados.
    # Ese es el punto entero: la corrección arregla la cifra ANTES de que otra quincena
    # se la cobre al productor.
    _recepcion(client, h, prov, "2026-06-20", "250")
    q2 = _de(_generar(client, h, ("2026-06-16", "2026-06-30")), prov)
    print(f"      Q2: anticipos {q2['anticipos']} . neto {q2['neto_a_pagar']}")
    assert D(q2["anticipos"]) == D("30000"), (
        "la quincena siguiente descontó una cifra distinta a la corregida"
    )


# ===========================================================================
# 8 — SI LA QUE LO RECOGIÓ SE ANULA, EL ADELANTO NO SE PIERDE NI SE COBRA DOS VECES
# ===========================================================================
def test_anular_la_quincena_que_lo_recogio_lo_devuelve_a_la_fila_sin_perder_un_peso(
    client, base_datos
):
    """`_soltar_lo_apartado` corre al anular y NO toca la marca nueva: hay que medir qué
    queda.

    Q2 (200 L x $2.000 = $400.000) recoge el adelanto de $300.000 y se ANULA antes de
    pagar nada. El adelanto vuelve a quedar suelto, y lo que tiene que pasar es:
      · sigue TRABADO en la pantalla de anticipos —la promesa del comprobante v1 de Q1
        sigue impresa y nadie le ha descontado esa plata todavía—;
      · y la quincena SIGUIENTE (Q3, 50 L x $2.000 = $100.000) se lo vuelve a descontar,
        una sola vez: anticipos $300.000, neto −$200.000 y el productor queda debiendo
        $200.000, que es la verdad.

    Si al anular el adelanto quedara destrabado, se podría borrar por la pantalla de
    anticipos y se perderían $300.000; si no lo recogiera nadie, se perderían igual.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    ant, pagada = _q1_pagada_con_adelanto(client, h, prov)
    _soltar(client, h, pagada["id"], ant["id"])
    _pagar(client, h, pagada["id"])

    _recepcion(client, h, prov, "2026-06-20", "200")
    q2 = _de(_generar(client, h, Q2), prov)
    assert D(q2["anticipos"]) == D("300000.00")

    r = client.post(f"{API}/{q2['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    print(f"\n  Q2 anulada -> estado={r.json()['estado']}")

    suelto = _anticipo_leido(client, h, ant["id"])
    print(f"  el adelanto: liquidacion_id={suelto['liquidacion_id']} "
          f"bloqueado={suelto['bloqueado']}")
    assert suelto["liquidacion_id"] is None, "la anulación lo tiene que soltar"
    assert suelto["bloqueado"] is True, (
        "quedó suelto otra vez y sin descontar: si la pantalla lo destraba, esos "
        "$300.000 se borran con un clic"
    )
    borrar = client.delete(f"{V}/anticipos/{ant['id']}", headers=h)
    assert borrar.status_code == 422, (
        f"se pudo BORRAR un adelanto de $300.000 ya entregado: {borrar.status_code}"
    )

    _recepcion(client, h, prov, "2026-07-05", "50")
    q3 = _de(_generar(client, h, Q3), prov)
    print(f"  Q3: total={q3['valor_total']} anticipos={q3['anticipos']} "
          f"neto={q3['neto_a_pagar']} debe={q3.get('le_queda_debiendo')}")
    assert D(q3["valor_total"]) == D("100000.00"), q3["valor_total"]
    assert D(q3["anticipos"]) == D("300000.00"), (
        f"la quincena siguiente no volvió a recoger el adelanto: {q3['anticipos']}"
    )
    assert D(q3["neto_a_pagar"]) == D("-200000.00"), q3["neto_a_pagar"]
    _cuadra(q3, "Q3 después de anular Q2")


# ===========================================================================
# 9 — LA OTRA SALIDA QUE NOMBRA EL MENSAJE SÍ EXISTE (y hasta dónde llega)
# ===========================================================================
def test_la_salida_de_volver_a_incluirlo_con_la_correccion_si_funciona(client, base_datos):
    """El mensaje de la marca nueva nombra dos salidas. Esta prueba mide la primera.

    Después de soltar el adelanto de $300.000 de Q1, el dueño lo vuelve a incluir con
    'Corregir esta quincena' Y de paso le corrige el valor a los $30.000 que de verdad
    entregó. La cuenta de Q1 queda:
        VALOR TOTAL $500.000 − anticipos $30.000 = neto $470.000
        pagado $200.000 -> saldo $270.000, estado 'parcial'.
    Y la marca tiene que quedar LIMPIA: el adelanto vuelve a estar dentro de un
    comprobante y lo protege el candado normal.

    Sirve para acotar el defecto de la prueba 7: el dueño no queda encerrado del todo,
    pero la única puerta abierta lo obliga a meter el adelanto DE VUELTA en la quincena
    de la que acababa de sacarlo —lo contrario de lo que quería— o a esperar a que la
    siguiente lo recoja. La puerta que el sistema le NOMBRÓ sigue cerrada.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h)
    ant, pagada = _q1_pagada_con_adelanto(client, h, prov)
    _soltar(client, h, pagada["id"], ant["id"])

    r = _corregir(
        client,
        h,
        pagada["id"],
        {
            "motivo": "el adelanto sí era de esta quincena pero fue de $30.000",
            "anticipos_a_incluir": [ant["id"]],
            "valores_de_anticipos": [{"anticipo_id": ant["id"], "valor": "30000"}],
        },
    )
    assert r.status_code == 200, r.text
    q1 = r.json()
    print(f"\n  Q1 v{q1['version']}: total={q1['valor_total']} anticipos={q1['anticipos']} "
          f"neto={q1['neto_a_pagar']} pagado={q1['pagado']} saldo={q1['saldo']}")
    assert D(q1["anticipos"]) == D("30000.00"), q1["anticipos"]
    assert D(q1["neto_a_pagar"]) == D("470000.00"), q1["neto_a_pagar"]
    assert D(q1["saldo"]) == D("270000.00"), q1["saldo"]
    _cuadra(q1, "Q1 con el adelanto de vuelta y corregido")

    vuelto = _anticipo_leido(client, h, ant["id"])
    assert vuelto["liquidacion_id"] == pagada["id"], vuelto
    assert D(vuelto["valor"]) == D("30000.00"), vuelto["valor"]
    assert vuelto["bloqueado"] is True, "dentro de una quincena con pagos, trabado"
    # Y la marca quedó limpia: lo traba SU liquidación, con SU mensaje, no la marca vieja.
    r = client.delete(f"{V}/anticipos/{ant['id']}", headers=h)
    assert r.status_code == 422, r.text
    detalle = r.json()["error"]["detail"]
    print(f"  mensaje del candado: {detalle}")
    assert "comprobante corregido" in detalle or "ya tiene un pago" in detalle, detalle
    assert "YA SE IMPRIMIÓ" not in detalle, (
        f"la marca de 'soltado' se quedó puesta después de volver a incluirlo: {detalle}"
    )
