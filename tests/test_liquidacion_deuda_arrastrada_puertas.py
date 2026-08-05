"""LA DEUDA QUE SE ARRASTRA — LAS PUERTAS por donde se podría entrar a moverle la deuda
por detrás, el papel del avance, y el guardia del PERÍODO QUE SE PISA.

Mismo libro de plata que las otras tandas: se reúsa el de
tests/test_liquidacion_deuda_arrastrada_plata.py, que es donde está la cuenta del dueño
(anticipos entregados + plata pagada == leche liquidada − lo que la quesera aún debe + lo
que el tercero quedó debiendo + anticipos sueltos).

La tanda 30, al final, es la que mide el hueco de los períodos montados: $500.000 saliendo
de la caja por $380.000 de leche cuando se dejaban generar, y por dónde tiene que seguir
pasando la corrida normal para que el guardia no estorbe.
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
    REC,
    _anticipo,
    _anular,
    _aprobar,
    _cuadra,
    _detalle,
    _generar,
    _leer,
    _libro,
    _pagar,
    _proveedor,
    _recalcular,
    _recepcion,
    _ruta,
    _todas,
    _transportador,
    texto_pdf,
)


def _montar(client, h, nombre="Henri C"):
    prov = _proveedor(client, h, nombre)
    dia1 = _recepcion(client, h, prov, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    dia2 = _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")
    return prov, q1, q2, ant, dia1, dia2


# ===========================================================================
# 21. LA PUERTA DEL PUT: mandar campos de plata que el schema no declara
# ===========================================================================
def test_21_el_put_de_observaciones_no_puede_mover_ni_un_peso(client, base_datos):
    """El único PUT de la liquidación es "Actualizar observaciones". Si el schema
    dejara pasar campos de más, un PUT podría poner `saldo_anterior` en cero DEJANDO
    la marca en el origen: esa deuda no la volvería a cobrar nadie nunca."""
    h = auth_headers(client, "admin.a")
    print("\n===== 21. LA PUERTA DEL PUT =====")
    prov, q1, q2, ant, dia1, dia2 = _montar(client, h)
    antes = _leer(client, h, q2["id"])

    intentos = [
        {"observaciones": "nota", "saldo_anterior": "0"},
        {"observaciones": "nota", "deuda_trasladada_a_id": None, "saldo": "250000"},
        {"observaciones": "nota", "anticipos": "9999999", "valor_total": "1"},
        {"observaciones": "nota", "pagado": "250000", "estado": "pagada"},
    ]
    for cuerpo in intentos:
        r = client.put(f"{API}/{q2['id']}", json=cuerpo, headers=h)
        print(f"      PUT {list(cuerpo)} -> {r.status_code}")
        assert r.status_code in (200, 422), r.text
        ahora = _leer(client, h, q2["id"])
        for campo in (
            "saldo_anterior",
            "saldo",
            "anticipos",
            "valor_total",
            "pagado",
            "estado",
            "neto_a_pagar",
        ):
            iguales = (
                D(ahora[campo]) == D(antes[campo])
                if campo != "estado"
                else ahora[campo] == antes[campo]
            )
            assert iguales, f"el PUT le movió «{campo}»: {antes[campo]} -> {ahora[campo]}"
        _cuadra(_libro(client, h), f"21 {list(cuerpo)}")

    # Y lo mismo por el lado del ORIGEN congelado.
    antes1 = _leer(client, h, q1["id"])
    r = client.put(
        f"{API}/{q1['id']}",
        json={"observaciones": "x", "anticipos": "0", "saldo": "180000"},
        headers=h,
    )
    print(f"      PUT sobre el origen congelado -> {r.status_code}")
    ahora1 = _leer(client, h, q1["id"])
    assert D(ahora1["saldo"]) == D(antes1["saldo"])
    assert D(ahora1["anticipos"]) == D(antes1["anticipos"])
    assert ahora1["deuda_trasladada_a_id"] == q2["id"]
    _cuadra(_libro(client, h, "tras los PUT"), "21 final")


def test_21b_no_hay_forma_de_borrar_una_liquidacion_por_la_api(client, base_datos):
    """EL BORRADO EN SUAVE DE UNA LIQUIDACIÓN NO ESTÁ EXPUESTO: `DELETE` responde 405.

    Esta prueba es EL PASADOR de una decisión que se tomó a conciencia y que está escrita
    en el servicio, encima de `validar_eliminar`: los guardias del borrado (soltar los
    días, los anticipos y las deudas que la liquidación se estaba cobrando, y rebotar si
    tiene pagos o si su deuda ya se cobró en otra) están escritos aunque HOY NO LOS CORRA
    NINGUNA PETICIÓN —el router se arma a mano y no expone DELETE—. Se conservan porque el
    día que alguien agregue la ruta, sin ellos heredaría el borrado pelado de
    `BaseService`: el anticipo de $300.000 quedaría preso de un documento que las consultas
    ya no devuelven y los $120.000 de deuda no los cobraría nadie nunca.

    Y PARA QUE NO SEA UNA DEFENSA QUE NADIE SABE QUE NO CORRE, el 405 se fija acá: el día
    que el DELETE exista, esta prueba falla y avisa que esos dos métodos pasaron a estar
    vivos y que hay que probarlos de verdad (borrar la que dejó la deuda, borrar la que la
    cobró, y medir el libro después de cada una).
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 21b. NO HAY DELETE DE LIQUIDACIONES =====")
    prov, q1, q2, ant, dia1, dia2 = _montar(client, h)
    for liq in (q1, q2):
        r = client.delete(f"{API}/{liq['id']}", headers=h)
        print(f"      DELETE {liq['periodo_inicio']} -> {r.status_code}")
        assert r.status_code == 405, (
            "ahora SÍ hay ruta de borrado de liquidaciones: `validar_eliminar` y "
            "`eliminar` del servicio pasaron a correr de verdad. Quite esta prueba y "
            f"pruebe el borrado midiendo la plata. Respuesta: {r.status_code} {r.text}"
        )
    _cuadra(_libro(client, h), "21b")


def test_21c_de_anulada_no_se_sale_por_ningun_lado(client, base_datos):
    """No existe "desanular": una vez anulada, la liquidación es terminal. Si se
    pudiera reanimar, volvería a cobrar una deuda que ya quedó libre y que otra
    liquidación pudo haberse cobrado."""
    h = auth_headers(client, "admin.a")
    print("\n===== 21c. DE ANULADA NO SE SALE =====")
    prov, q1, q2, ant, dia1, dia2 = _montar(client, h)
    assert _anular(client, h, q2["id"]).status_code == 200
    for verbo, r in (
        ("aprobar", _aprobar(client, h, q2["id"])),
        ("pagar", _pagar(client, h, q2["id"])),
        ("recalcular", _recalcular(client, h, q2["id"])),
        ("anular otra vez", _anular(client, h, q2["id"])),
        (
            "abonar",
            client.post(
                f"{API}/{q2['id']}/pagos",
                json={"fecha": "2026-07-01", "valor": "1000"},
                headers=h,
            ),
        ),
    ):
        print(f"      {verbo:18s} -> {r.status_code}")
        assert r.status_code >= 400, f"se pudo {verbo} una ANULADA"
    assert _leer(client, h, q2["id"])["estado"] == "anulada"
    assert D(_leer(client, h, q2["id"])["saldo_anterior"]) == CERO
    _cuadra(_libro(client, h, "anulada y terminal"), "21c")


# ===========================================================================
# 22. EL DÍA DE UNA QUINCENA CONGELADA POR EL LADO DEL FLETE
# ===========================================================================
def test_22_cambiar_el_transportador_de_un_dia_de_una_quincena_congelada(
    client, base_datos
):
    """La liquidación de LECHE está congelada (su deuda ya se cobró) pero el
    `transportador_id` del día solo lo traba el FLETE. Se cambia y hay que comprobar
    que a la de leche no se le movió UN PESO de su cuenta —lo único que puede cambiar
    es su columna informativa de flete—."""
    h = auth_headers(client, "admin.a")
    print("\n===== 22. CAMBIAR EL TRANSPORTADOR DE UN DÍA CONGELADO =====")
    ruta = _ruta(client, h, "Napoles")
    t1 = _transportador(client, h, "Chucho", [(ruta, "200")])
    t2 = _transportador(client, h, "Beto", [(ruta, "350")])
    prov = _proveedor(client, h, "Henri C")
    dia1 = _recepcion(client, h, prov, "2026-06-02", "100", t=t1, ruta=ruta)
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1, tipo="proveedor")[0]
    assert D(q1["le_queda_debiendo"]) == D("120000")
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500", t=t1, ruta=ruta)
    q2 = _generar(client, h, Q2, tipo="proveedor")[0]
    assert D(q2["saldo_anterior"]) == D("120000")

    antes = _leer(client, h, q1["id"])
    r = client.put(f"{REC}/{dia1['id']}", json={"transportador_id": t2["id"]}, headers=h)
    print(f"      cambiar el transportador del dia congelado -> {r.status_code}")
    ahora = _leer(client, h, q1["id"])
    print(f"      Q1: total {antes['valor_total']} -> {ahora['valor_total']}, "
          f"saldo {antes['saldo']} -> {ahora['saldo']}, "
          f"flete informativo {antes['valor_transporte']} -> {ahora['valor_transporte']}")
    assert D(ahora["valor_total"]) == D(antes["valor_total"]), "le movió el VALOR TOTAL"
    assert D(ahora["saldo"]) == D(antes["saldo"]), "le movió el SALDO"
    assert D(ahora["le_queda_debiendo"]) == D(antes["le_queda_debiendo"]), "le movió la DEUDA"
    assert D(_leer(client, h, q2["id"])["saldo_anterior"]) == D("120000")
    _cuadra(_libro(client, h, "tras cambiarle el transportador"), "22")


# ===========================================================================
# 23. EL PAPEL DEL AVANCE AVISA LA CIFRA EXACTA
# ===========================================================================
def _avance(client, h, periodo, tercero, tipo="proveedor"):
    r = client.post(
        f"{API}/previsualizar/pdf",
        json={
            "periodo_inicio": periodo[0],
            "periodo_fin": periodo[1],
            "tipo": tipo,
            "tercero_id": tercero["id"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return texto_pdf(r.content)


def test_23_el_avance_avisa_la_deuda_con_la_cifra_exacta(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 23. EL AVISO DEL AVANCE =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    _generar(client, h, Q1)  # debe 120.000

    # CASO A: el avance alcanza a cubrir la deuda -> avisa el saldo de verdad.
    _recepcion(client, h, prov, "2026-07-02", "100", precio="2500")  # 250.000
    papel = _avance(client, h, Q3, prov)
    assert "TODAVÍA NO DESCUENTA" in papel, papel
    assert "($120.000)" in papel, papel
    assert "quedar en $130.000" in papel, f"no dijo el saldo de verdad (130.000):\n{papel}"
    print("      caso A: avisa 120.000 de deuda y 130.000 de saldo de verdad  OK")

    # CASO B: la deuda se come TODO el avance -> avisa que le seguiría quedando debiendo.
    _recepcion(client, h, prov, "2026-07-18", "10", precio="2000")  # 20.000 contra 120.000
    papel_b = _avance(client, h, ("2026-07-16", "2026-07-31"), prov)
    print(f"      caso B (sin leche todavia): trae aviso={'TODAVÍA NO DESCUENTA' in papel_b}")
    assert "TODAVÍA NO DESCUENTA" in papel_b
    assert "seguiría quedando debiendo" in papel_b, papel_b
    _cuadra(_libro(client, h), "23")

    # Y cuando la deuda YA se cobró, el avance no vuelve a avisarla.
    q3 = _generar(client, h, Q3)[0]
    assert D(q3["saldo_anterior"]) == D("120000")
    papel_c = _avance(client, h, ("2026-07-16", "2026-07-31"), prov)
    print(f"      caso C (deuda ya cobrada): trae aviso="
          f"{'TODAVÍA NO DESCUENTA' in papel_c}")
    assert "TODAVÍA NO DESCUENTA" not in papel_c, (
        "el avance sigue avisando una deuda que YA se cobró"
    )
    _cuadra(_libro(client, h, "final"), "23 final")


# ===========================================================================
# 24. ABONOS PARCIALES SOBRE UNA QUINCENA QUE COBRÓ DEUDA
# ===========================================================================
def test_24_abonos_parciales_con_deuda_arrastrada_no_dejan_pagar_de_mas(
    client, base_datos
):
    """Q2 tiene neto $130.000 (=$250.000 − $120.000 de deuda vieja). El tope del abono
    tiene que ser $130.000, NO los $250.000 del valor total."""
    h = auth_headers(client, "admin.a")
    print("\n===== 24. ABONOS PARCIALES CON DEUDA VIEJA =====")
    prov, q1, q2, ant, dia1, dia2 = _montar(client, h)
    assert _aprobar(client, h, q2["id"]).status_code == 200

    r = client.post(
        f"{API}/{q2['id']}/pagos", json={"fecha": "2026-07-01", "valor": "130001"}, headers=h
    )
    print(f"      abonar 130.001 -> {r.status_code}: {r.text[:120]}")
    assert r.status_code == 422, "SE PUDO ABONAR MÁS QUE EL NETO"

    for valor, estado in (("50000", "parcial"), ("50000", "parcial"), ("30000", "pagada")):
        r = client.post(
            f"{API}/{q2['id']}/pagos", json={"fecha": "2026-07-01", "valor": valor}, headers=h
        )
        assert r.status_code == 200, r.text
        ahora = _leer(client, h, q2["id"])
        print(f"      abono {valor}: pagado={ahora['pagado']} saldo={ahora['saldo']} "
              f"estado={ahora['estado']}")
        assert ahora["estado"] == estado
        _cuadra(_libro(client, h), f"24 abono {valor}")

    r = client.post(
        f"{API}/{q2['id']}/pagos", json={"fecha": "2026-07-01", "valor": "1"}, headers=h
    )
    print(f"      un peso mas -> {r.status_code}")
    assert r.status_code == 422, "SE PAGÓ UN PESO DE MÁS"
    libro = _libro(client, h, "Q2 pagada en tres abonos")
    _cuadra(libro, "24 final")
    assert libro["pagado"] == D("130000")
    assert libro["anticipos"] + libro["pagado"] == libro["leche"] == D("430000")


# ===========================================================================
# 25. LA CADENA CON LA DEL MEDIO ANULADA Y NUNCA REGENERADA
# ===========================================================================
def test_25_la_del_medio_anulada_y_nunca_regenerada_no_pierde_la_deuda(client, base_datos):
    """Q1 debe; Q2 la cobra y vuelve a deber; Q3 cobra a Q2. Se anulan Q3 y Q2 y NO se
    regenera Q2: la deuda de Q1 tiene que quedar libre y viva, y la siguiente que se
    genere se la tiene que cobrar. Si desapareciera, el proveedor se queda con la plata.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 25. LA DEL MEDIO ANULADA Y NUNCA REGENERADA =====")
    prov = _proveedor(client, h, "Henri C")
    _anticipo(client, h, "2026-06-01", "500000", proveedor=prov)
    _recepcion(client, h, prov, "2026-06-02", "100")      # 180.000
    q1 = _generar(client, h, Q1)[0]                        # debe 320.000
    _recepcion(client, h, prov, "2026-06-20", "100")       # 180.000
    q2 = _generar(client, h, Q2)[0]                        # debe 140.000
    _recepcion(client, h, prov, "2026-07-02", "50")        # 90.000
    q3 = _generar(client, h, Q3)[0]                        # debe 50.000
    print(f"      Q1 debe={q1['le_queda_debiendo']} Q2 debe={q2['le_queda_debiendo']} "
          f"Q3 debe={q3['le_queda_debiendo']}")
    _cuadra(_libro(client, h, "cadena de tres"), "25 armada")

    assert _anular(client, h, q3["id"]).status_code == 200
    assert _anular(client, h, q2["id"]).status_code == 200
    libro = _libro(client, h, "Q3 y Q2 anuladas")
    _cuadra(libro, "25 anuladas")
    assert libro["debiendo"] == D("320000"), (
        f"la deuda de Q1 quedó en {libro['debiendo']} y tenía que ser 320.000"
    )

    # La SIGUIENTE que se genere se la cobra, aunque sea otro período.
    _recepcion(client, h, prov, "2026-08-02", "300")  # 540.000
    q5 = _generar(client, h, ("2026-08-01", "2026-08-15"))[0]
    print(f"      Q agosto: total={q5['valor_total']} vieja={q5['saldo_anterior']} "
          f"saldo={q5['saldo']} desglose={[o['periodo_texto'] for o in q5['deudas_cobradas']]}")
    assert D(q5["saldo_anterior"]) == D("320000")
    assert _aprobar(client, h, q5["id"]).status_code == 200
    assert _pagar(client, h, q5["id"]).status_code == 200
    libro = _libro(client, h, "agosto pagada")
    _cuadra(libro, "25 final")
    # leche VIVA: 180.000 (Q1) + 540.000 (agosto) = 720.000. Los días de Q2 y Q3 quedaron
    # sueltos (sus liquidaciones se anularon) y todavía no se le pagan.
    assert libro["anticipos"] + libro["pagado"] == D("500000") + D("220000")
    assert libro["leche"] == D("720000")
    assert libro["debiendo"] == CERO


# ===========================================================================
# 26. LA MISMA DEUDA, MUCHAS PUERTAS A LA VEZ
# ===========================================================================
def test_26_martillar_la_misma_deuda_por_diez_caminos_seguidos(client, base_datos):
    """Se le da a TODOS los botones, uno detrás del otro, sobre las dos puntas, y se
    mide el libro después de cada uno. Ninguna combinación puede dejar la deuda cobrada
    dos veces ni perdida."""
    h = auth_headers(client, "admin.a")
    print("\n===== 26. MARTILLAR LA MISMA DEUDA =====")
    prov, q1, q2, ant, dia1, dia2 = _montar(client, h)
    pasos = [
        ("aprobar Q1", lambda: _aprobar(client, h, q1["id"])),
        ("recalcular Q1", lambda: _recalcular(client, h, q1["id"])),
        ("aprobar Q2", lambda: _aprobar(client, h, q2["id"])),
        ("recalcular Q2", lambda: _recalcular(client, h, q2["id"])),
        ("aprobar Q2 otra vez", lambda: _aprobar(client, h, q2["id"])),
        ("pagar Q1", lambda: _pagar(client, h, q1["id"])),
        ("anular Q1", lambda: _anular(client, h, q1["id"])),
        ("generar Q2 otra vez", lambda: client.post(
            f"{API}/generar",
            json={"periodo_inicio": Q2[0], "periodo_fin": Q2[1], "tipo": "proveedor"},
            headers=h)),
        ("generar Q1 otra vez", lambda: client.post(
            f"{API}/generar",
            json={"periodo_inicio": Q1[0], "periodo_fin": Q1[1], "tipo": "proveedor"},
            headers=h)),
        ("borrar el anticipo", lambda: client.delete(f"{ANT}/{ant['id']}", headers=h)),
        ("corregir el anticipo", lambda: client.put(
            f"{ANT}/{ant['id']}", json={"valor": "10"}, headers=h)),
        ("borrar el dia de Q1", lambda: client.delete(f"{REC}/{dia1['id']}", headers=h)),
        ("aprobar Q2", lambda: _aprobar(client, h, q2["id"])),
        ("pagar Q2", lambda: _pagar(client, h, q2["id"])),
        ("anular Q2", lambda: _anular(client, h, q2["id"])),
        ("recalcular Q1", lambda: _recalcular(client, h, q1["id"])),
    ]
    for nombre, hacer in pasos:
        r = hacer()
        print(f"      {nombre:26s} -> {r.status_code}")
        libro = _libro(client, h)
        _cuadra(libro, f"26 {nombre}")
        assert libro["cobrado_de_atras"] <= D("120000"), (
            f"tras «{nombre}» se está cobrando de atrás {libro['cobrado_de_atras']}"
        )
    libro = _libro(client, h, "despues de martillar")
    _cuadra(libro, "26 final")
    salio = libro["anticipos"] + libro["pagado"]
    print(f"      SALIO {salio}; LECHE {libro['leche']}; debiendo {libro['debiendo']}; "
          f"por pagar {libro['por_pagar']}; sueltos {libro['sueltos']}")


# ===========================================================================
# 30. LA PUERTA DEL PERÍODO QUE SE PISA: NO SE GENERAN DOS QUINCENAS MONTADAS
# ===========================================================================
# EL HUECO QUE CERRÓ ESTA PUERTA, con las cifras que se midieron cuando estaba abierta:
# Henri entregaba 100 L a $1.800 el 02 de junio ($180.000) contra $300.000 de anticipo ya
# entregado, así que la quincena del 01 al 15 quedaba debiendo $120.000. Después se
# generaba una quincena "del 10 al 20" —que SE PISA con esa, y nada lo impedía—, esa
# liquidación NO le cobraba los $120.000 (la deuda solo viaja a un período que empiece
# después de que el origen TERMINE) y se le pagaban $200.000 completos: de la caja salían
# $500.000 por $380.000 de leche.
#
# El guardia que lo impide ya estaba ESCRITO Y SIN USAR (`existe_para_periodo`, código
# muerto). Se cableó al generar, se le cambió el sí/no por la liquidación de verdad —para
# poder nombrarla en el mensaje— y se le apretó el filtro: solo reserva sus fechas la
# liquidación que TODAVÍA PUEDE QUEDAR DEBIENDO (o la que YA ESTÁ DEBIENDO) Y CUYA DEUDA
# NADIE HA COBRADO. Las otras —anulada, pagada con el saldo cuadrado, o con su deuda ya
# cobrada en otra— tienen que dejarse pasar o el día que se anota tarde dentro de ese
# período no se le puede pagar NUNCA al productor (medido en 30d y 30e). Ver
# `LiquidacionRepository.solapada_para_periodo` y `_omitido_por_periodo_cruzado`.
#
# Y EL GUARDIA NO TUMBA LA CORRIDA: al tercero del cruce lo SALTA y lo REPORTA en
# `omitidas`. Antes lanzaba un error que se llevaba por delante a los terceros que no
# tenían nada que ver —$720.000 de leche de dos proveedoras, $1.080.000 por el lado del
# flete—. Esas dos cifras están medidas en
# tests/test_liquidacion_corrida_de_la_quincena.py (las pruebas b3b y b6, que primero
# midieron el hueco y hoy exigen la garantía), y el ataque completo al salto —que la
# corrida no se tumbe por ningún camino, que la plata cuadre y que el salto no deje
# basura en la base— está en tests/test_liquidacion_corrida_omitidos_adversarial.py.
def _peticion_generar(client, h, inicio, fin, tipo="proveedor", proveedor_id=None):
    cuerpo = {"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo}
    if proveedor_id:
        cuerpo["proveedor_id"] = proveedor_id
    return client.post(f"{API}/generar", json=cuerpo, headers=h)


def _corrida(client, h, inicio, fin, tipo="proveedor", proveedor_id=None):
    """La corrida completa: {"generadas": [...], "omitidas": [...]}. Exige el 200."""
    r = _peticion_generar(client, h, inicio, fin, tipo, proveedor_id)
    assert r.status_code == 200, r.text
    return r.json()


def _un_omitido(corrida):
    """El único omitido de la corrida, exigiendo que sea uno solo."""
    assert len(corrida["omitidas"]) == 1, corrida["omitidas"]
    return corrida["omitidas"][0]


def test_30_el_periodo_que_se_pisa_no_se_genera_y_lo_dice_con_nombres(
    client, base_datos
):
    """LAS CIFRAS DEL HUECO, ahora tapado: no sale un peso de la caja.

    Se monta el caso medido —Q1 (01 al 15) debiendo $120.000— y se pide la quincena "del
    10 al 20", que trae dos días nuevos por $200.000. Antes salía y se le pagaba completa:
    $500.000 de la caja por $380.000 de leche.

    AHORA NO SALE, Y AL TERCERO SE LO SALTA SIN TUMBAR LA CORRIDA: sale en `omitidas` con
    el motivo redactado, que le dice al dueño lo único que le sirve: QUIÉN, DE QUÉ CUENTA
    y DE QUÉ PERÍODO es la liquidación con la que se está cruzando —él no sabe qué es un
    "período solapado"—, y las dos salidas que tiene.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 30. EL PERÍODO QUE SE PISA SE OMITE =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    assert D(q1["le_queda_debiendo"]) == D("120000")
    antes = _libro(client, h, "solo Q1, debiendo 120.000")

    _recepcion(client, h, prov, "2026-06-12", "50", precio="2000")
    _recepcion(client, h, prov, "2026-06-18", "50", precio="2000")
    corrida = _corrida(client, h, "2026-06-10", "2026-06-20")
    print(f"      generar 10-20 -> generadas={len(corrida['generadas'])} "
          f"omitidas={len(corrida['omitidas'])}")
    assert corrida["generadas"] == []
    omitida = _un_omitido(corrida)
    print(f"      dice: {omitida['motivo']}")
    # El tercero, de qué cuenta es y el motivo en código, para que la pantalla pueda
    # mostrarlo y agruparlo sin leer palabras dentro de la frase.
    assert omitida["tipo"] == "proveedor"
    assert omitida["cuenta"] == "leche"
    assert omitida["tercero_id"] == prov["id"]
    assert omitida["tercero_nombre"] == "Henri C"
    assert omitida["motivo_codigo"] == "periodo_cruzado"
    detalle = omitida["motivo"]
    # El nombre del tercero, de qué es la liquidación y su período, tal cual lo lee el dueño.
    assert "Henri C ya tiene una liquidación de leche del 01/06/2026 al 15/06/2026" in detalle
    assert "se cruza con estas fechas (10/06/2026 al 20/06/2026)" in detalle
    # Y las dos salidas: cambiar las fechas, o anular esa liquidación.
    assert "Ajuste las fechas" in detalle
    assert "anule esa liquidación primero" in detalle

    # NO QUEDÓ NADA A MEDIO HACER y la caja no se movió: sigue habiendo una liquidación,
    # los dos días nuevos siguen sin liquidar y la deuda de Q1 sigue libre.
    despues = _libro(client, h, "tras la omisión")
    _cuadra(despues, "30 omitido")
    assert len(despues["vivas"]) == len(antes["vivas"]) == 1
    assert despues["leche"] == antes["leche"] == D("180000")
    assert despues["anticipos"] + despues["pagado"] == D("300000")
    assert despues["debiendo"] == D("120000")
    assert _leer(client, h, q1["id"])["deuda_trasladada_a_id"] is None


def test_30b_el_mismo_periodo_para_otro_tercero_y_por_proveedor_id_siguen_pasando(
    client, base_datos
):
    """LO QUE EL GUARDIA NO PUEDE ESTORBAR, que es la mitad de su trabajo.

    · generar el MISMO período para OTRO proveedor es lo normal —una quincena se le
      genera a todos— y tiene que seguir saliendo;
    · generar de a un proveedor (el filtro de la pantalla) también;
    · y el que ya tiene la quincena generada no estorba a los demás: se salta solo,
      porque sus días ya están apartados y no entra en la corrida.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 30b. EL MISMO PERÍODO PARA OTRO TERCERO =====")
    henri = _proveedor(client, h, "Henri C")
    marleny = _proveedor(client, h, "Marleny")
    aleida = _proveedor(client, h, "Aleida")
    for prov in (henri, marleny, aleida):
        _recepcion(client, h, prov, "2026-06-02", "100")
    # De a uno: Henri primero, por proveedor_id.
    corrida = _corrida(client, h, Q1[0], Q1[1], proveedor_id=henri["id"])
    assert len(corrida["generadas"]) == 1
    print(f"      Q1 de Henri por proveedor_id -> {len(corrida['generadas'])}")
    # Y ahora la corrida completa del MISMO período: salen las otras dos, y la de Henri
    # no vuelve a salir ni tumba la corrida —ni sale como omitida: sus días ya están
    # apartados, así que no entra en la corrida y no hay nada que avisar—.
    corrida = _corrida(client, h, Q1[0], Q1[1])
    print(f"      Q1 para todos -> {len(corrida['generadas'])} liquidaciones, "
          f"{len(corrida['omitidas'])} omitidas")
    assert len(corrida["generadas"]) == 2
    assert corrida["omitidas"] == []
    nombres = sorted(liq["proveedor_nombre"] for liq in corrida["generadas"])
    assert nombres == ["Aleida", "Marleny"], nombres
    # Tres liquidaciones del mismo período, una por proveedor: eso no es un cruce.
    vivas = [liq for liq in _todas(client, h) if liq["estado"] != "anulada"]
    assert len(vivas) == 3
    assert all(liq["periodo_inicio"] == Q1[0] for liq in vivas)
    _cuadra(_libro(client, h, "tres proveedores, mismo periodo"), "30b")


def test_30c_una_anulada_no_estorba_y_regenerar_despues_de_anular_sigue_igual(
    client, base_datos
):
    """UNA ANULADA NO RESERVA SUS FECHAS: regenerar después de anular es EL flujo de
    corrección del sistema, y tiene que seguir funcionando —incluso con un período
    distinto, que es lo que hace el dueño cuando se equivocó en las fechas—."""
    h = auth_headers(client, "admin.a")
    print("\n===== 30c. LA ANULADA NO ESTORBA =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _recepcion(client, h, prov, "2026-06-12", "50", precio="2000")
    q1 = _generar(client, h, Q1)[0]
    assert _anular(client, h, q1["id"]).status_code == 200

    # El MISMO período otra vez: pasa.
    q1b = _generar(client, h, Q1)[0]
    print(f"      Q1 regenerada tras anular: total={q1b['valor_total']}")
    assert D(q1b["valor_total"]) == D("280000")
    assert _anular(client, h, q1b["id"]).status_code == 200
    # Y un período DISTINTO que se pisaría con la anulada: también pasa.
    corrida = _corrida(client, h, "2026-06-10", "2026-06-20")
    print(f"      10-20 con dos anuladas encima -> {len(corrida['generadas'])}")
    assert len(corrida["generadas"]) == 1
    assert corrida["omitidas"] == []
    _cuadra(_libro(client, h, "regenerada con otro periodo"), "30c")


def test_30d_la_quincena_pagada_no_traba_el_dia_anotado_tarde(client, base_datos):
    """LA QUINCENA YA PAGADA NO RESERVA SUS FECHAS, Y ES A PROPÓSITO.

    Es la decisión más delicada de este guardia, y se tomó midiendo qué pasaba con la
    plata en los dos sentidos:

    · UNA QUINCENA PAGADA NO PUEDE ESTAR DEBIENDO NADA. "Pagar" rebota cuando el tercero
      quedó debiendo, así que una liquidación con plata entregada tiene el saldo en cero o
      a favor del tercero: nunca es origen de una deuda por cobrar, y cruzarse con ella no
      puede costar un peso. El hueco que este guardia cierra no existe por este lado.
    · Y SI RESERVARA SUS FECHAS, EL DÍA ANOTADO TARDE SE QUEDARÍA SIN LIQUIDAR PARA
      SIEMPRE: una quincena pagada NO SE PUEDE ANULAR, y cualquier rango que contenga ese
      día se pisa con ella. El productor no cobraría nunca esa leche. Hoy ese día entra en
      un segundo comprobante del mismo período y se le paga; eso se conserva.

    LAS CIFRAS: Q1 de $180.000 pagada; después aparece el día 05 de junio con 30 L a
    $1.800 ($54.000). El segundo comprobante sale por esos $54.000 y de la caja salen
    $234.000 por $234.000 de leche. Ni un peso de más, ni un litro sin pagar.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 30d. LA PAGADA NO TRABA EL DÍA TARDÍO =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    q1 = _generar(client, h, Q1)[0]
    assert _aprobar(client, h, q1["id"]).status_code == 200
    assert _pagar(client, h, q1["id"]).status_code == 200
    assert _leer(client, h, q1["id"])["estado"] == "pagada"

    # El día que se anotó tarde, DENTRO del período ya pagado.
    _recepcion(client, h, prov, "2026-06-05", "30")
    otra = _generar(client, h, Q1)
    assert len(otra) == 1, (
        "el día anotado tarde se quedó sin liquidar: la quincena PAGADA no se puede "
        "anular, así que este es el único camino que tiene esa leche para cobrarse"
    )
    print(f"      segundo comprobante del mismo periodo: total={otra[0]['valor_total']}")
    assert D(otra[0]["valor_total"]) == D("54000")
    assert D(otra[0]["saldo_anterior"]) == CERO

    # Y EL GUARDIA NO SE APAGÓ: esa segunda liquidación está en BORRADOR —todavía se
    # puede corregir y todavía puede quedar debiendo— así que ELLA SÍ reserva sus fechas.
    # Otro día tardío y otro Generar: se omite nombrándola.
    _recepcion(client, h, prov, "2026-06-07", "10")
    corrida = _corrida(client, h, Q1[0], Q1[1])
    omitida = _un_omitido(corrida)
    print(f"      con la segunda en borrador -> omitida: {omitida['motivo'][:110]}")
    assert corrida["generadas"] == []
    assert "Henri C ya tiene una liquidación de leche del 01/06/2026 al 15/06/2026" in (
        omitida["motivo"]
    )

    # Se le paga la segunda y entonces sí entra el último día, en un tercer comprobante.
    assert _aprobar(client, h, otra[0]["id"]).status_code == 200
    assert _pagar(client, h, otra[0]["id"]).status_code == 200
    tercera = _generar(client, h, Q1)
    assert len(tercera) == 1
    print(f"      tercer comprobante: total={tercera[0]['valor_total']}")
    assert D(tercera[0]["valor_total"]) == D("18000")
    assert _aprobar(client, h, tercera[0]["id"]).status_code == 200
    assert _pagar(client, h, tercera[0]["id"]).status_code == 200
    libro = _libro(client, h, "los dias tardios, pagados")
    _cuadra(libro, "30d")
    # $180.000 + $54.000 + $18.000: toda la leche cobrada, ni un peso de más.
    assert libro["anticipos"] + libro["pagado"] == libro["leche"] == D("252000")
    assert libro["debiendo"] == CERO


def test_30e_la_quincena_congelada_tampoco_traba_el_dia_anotado_tarde(client, base_datos):
    """LA OTRA QUE NO PUEDE RESERVAR SUS FECHAS: la que ya tiene su deuda cobrada.

    Es EL FLUJO NORMAL de toda esta función, y por poco queda con una leche imposible de
    cobrar: Q1 (01 al 15) queda debiendo $120.000, Q2 (16 al 30) se los cobra y se paga.
    A partir de ahí NINGUNA DE LAS DOS SE PUEDE ANULAR —Q2 está pagada, y Q1 rebota
    pidiendo que se anule primero Q2—. Si Q1 reservara sus fechas, el día olvidado del 05
    de junio no tendría por dónde entrar: ni generando (cualquier rango que lo contenga se
    cruza con Q1) ni anulando (no se deja). Esos $54.000 de leche no se le pagarían nunca
    al productor.

    Y dejarlo pasar no cuesta un peso: la deuda de Q1 YA está restada en Q2 —está marcada,
    y la consulta de deudas no vuelve a mirar a los marcados—, así que por ese lado no hay
    nada que perder.

    LAS CIFRAS: $430.000 de leche pagados exactos (anticipo $300.000 + $130.000), más los
    $54.000 del día olvidado: $484.000 de la caja por $484.000 de leche.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 30e. LA CONGELADA NO TRABA EL DÍA TARDÍO =====")
    prov, q1, q2, ant, dia1, dia2 = _montar(client, h)
    assert _aprobar(client, h, q2["id"]).status_code == 200
    assert _pagar(client, h, q2["id"]).status_code == 200
    libro = _libro(client, h, "la cadena normal, pagada")
    _cuadra(libro, "30e cadena")
    assert libro["anticipos"] + libro["pagado"] == libro["leche"] == D("430000")
    # Las dos puntas están trabadas: ni Q2 (pagada) ni Q1 (su deuda ya se cobró) se anulan.
    assert _anular(client, h, q2["id"]).status_code == 422
    assert _anular(client, h, q1["id"]).status_code == 422

    # El día olvidado, DENTRO del período de Q1.
    _recepcion(client, h, prov, "2026-06-05", "30")
    tardia = _generar(client, h, Q1)
    assert len(tardia) == 1, (
        "el día olvidado se quedó sin liquidar: ni Q1 ni Q2 se pueden anular, así que "
        "este es el único camino que tiene esa leche para cobrarse"
    )
    print(f"      segundo comprobante 01-15: total={tardia[0]['valor_total']} "
          f"deuda_vieja={tardia[0]['saldo_anterior']}")
    assert D(tardia[0]["valor_total"]) == D("54000")
    # No se le vuelve a cobrar la deuda de Q1: ya la cobró Q2 y sigue marcada.
    assert D(tardia[0]["saldo_anterior"]) == CERO
    assert _leer(client, h, q1["id"])["deuda_trasladada_a_id"] == q2["id"]
    assert _aprobar(client, h, tardia[0]["id"]).status_code == 200
    assert _pagar(client, h, tardia[0]["id"]).status_code == 200
    libro = _libro(client, h, "el dia olvidado, cobrado")
    _cuadra(libro, "30e final")
    assert libro["anticipos"] + libro["pagado"] == libro["leche"] == D("484000")
    assert libro["debiendo"] == CERO


def test_30f_el_flete_tambien_se_omite_y_la_leche_no_le_estorba_al_flete(client, base_datos):
    """EL GUARDIA VA POR TIPO Y POR TERCERO, y las dos mitades importan.

    · el período que se pisa SE OMITE IGUAL en la liquidación del transportador —su deuda
      se pierde por el mismo camino—, y el motivo dice "de flete";
    · pero la liquidación DE LECHE de una persona no le estorba la de FLETE, aunque sea la
      misma persona con las dos cuentas: son dos comprobantes distintos y cada uno tiene
      su propia deuda.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 30e. EL FLETE, Y LAS DOS CUENTAS DE LA MISMA PERSONA =====")
    ruta = _ruta(client, h, "Napoles")
    # La misma persona: proveedor Y transportador de su propia leche.
    t = _transportador(client, h, "Henri C", [(ruta, "200")])
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100", t=t, ruta=ruta)
    generadas = _generar(client, h, Q1, tipo="ambos")
    tipos = sorted(liq["tipo"] for liq in generadas)
    print(f"      Q1 'ambos' -> {tipos}")
    # Las dos cuentas de la misma persona, el mismo período: eso no es un cruce.
    assert tipos == ["proveedor", "transportador"]

    # Un período que se pisa, pidiendo SOLO el flete: se omite nombrando la de flete.
    _recepcion(client, h, prov, "2026-06-18", "40", t=t, ruta=ruta)
    corrida = _corrida(client, h, "2026-06-10", "2026-06-20", tipo="transportador")
    omitida = _un_omitido(corrida)
    print(f"      10-20 solo flete -> omitida ({omitida['cuenta']}): "
          f"{omitida['motivo'][:120]}")
    assert corrida["generadas"] == []
    assert (omitida["tipo"], omitida["cuenta"]) == ("transportador", "flete")
    assert omitida["tercero_id"] == t["id"]
    assert "Henri C ya tiene una liquidación de flete del 01/06/2026 al 15/06/2026" in (
        omitida["motivo"]
    )
    # Y pidiendo solo la leche, se omite nombrando la de leche.
    corrida = _corrida(client, h, "2026-06-10", "2026-06-20", tipo="proveedor")
    omitida = _un_omitido(corrida)
    assert (omitida["tipo"], omitida["cuenta"]) == ("proveedor", "leche")
    assert "una liquidación de leche del 01/06/2026 al 15/06/2026" in omitida["motivo"]
    _cuadra(_libro(client, h, "las dos cuentas, sin cruces"), "30e")
