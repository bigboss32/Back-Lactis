"""LA DEUDA QUE SE ARRASTRA — ¿SALE PLATA DE MÁS DE LA CAJA? (la cuenta del dueño)

Este archivo NO prueba que el arreglo funcione (eso ya está en
tests/test_liquidacion_saldo_anterior.py). Este archivo INTENTA romperlo: busca un
camino por donde salga plata de más, o por donde una deuda se pierda.

ES EL PRIMERO DE CUATRO, y el que tiene las herramientas: `_libro` (todas las cifras de
plata del tenant, leídas por la API igual que las lee el dueño) y `_cuadra` (la igualdad
de abajo, medida hasta el peso). Los otros tres las importan de acá:
tests/test_liquidacion_deuda_arrastrada_bordes.py (los bordes),
tests/test_liquidacion_deuda_arrastrada_puertas.py (las puertas y el guardia del período
que se pisa) y tests/test_liquidacion_deuda_arrastrada_concurrencia.py (dos peticiones a
la vez).

LA CUENTA QUE HACE EL DUEÑO, y la que se mide en cada prueba:

    anticipos entregados + plata pagada
        == leche liquidada − lo que la quesera todavía le debe
                          + lo que el tercero le quedó debiendo

Al derecho: toda la plata que salió de la caja tiene que estar explicada por leche
recibida, menos lo que falta por pagar, más lo que el tercero quedó debiendo. Si esa
igualdad se rompe por un peso, se creó o se perdió plata.
"""
import io
import re
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
ANT = "/api/v1/anticipos"


def D(v):
    return Decimal(str(v))


CERO = D(0)

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
Q3 = ("2026-07-01", "2026-07-15")
Q4 = ("2026-07-16", "2026-07-31")
Q5 = ("2026-08-01", "2026-08-15")


# ------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="1800"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ruta(client, h, nombre):
    r = client.post(
        "/api/v1/rutas", json={"nombre": nombre, "municipio": "Granada"}, headers=h
    )
    assert r.status_code == 201, r.text
    return r.json()


def _transportador(client, h, nombre, rutas):
    r = client.post(
        "/api/v1/transportadores",
        json={
            "nombre": nombre,
            "valor_transporte": "0",
            "rutas": [{"ruta_id": ru["id"], "valor_transporte": str(v)} for ru, v in rutas],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros, *, precio=None, t=None, ruta=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)}
    if precio is not None:
        cuerpo["precio_litro"] = str(precio)
    if t:
        cuerpo["transportador_id"] = t["id"]
    if ruta:
        cuerpo["ruta_id"] = ruta["id"]
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, fecha, valor, *, proveedor=None, transportador=None):
    cuerpo = {"fecha": fecha, "valor": str(valor)}
    if proveedor:
        cuerpo.update(tipo="proveedor", proveedor_id=proveedor["id"])
    else:
        cuerpo.update(tipo="transportador", transportador_id=transportador["id"])
    r = client.post(ANT, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _correr(client, h, periodo, tipo="proveedor"):
    """La respuesta COMPLETA de la corrida: {"generadas": [...], "omitidas": [...]}."""
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": tipo},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _generar(client, h, periodo, tipo="proveedor"):
    """Solo las liquidaciones que SALIERON.

    La respuesta trae también `omitidas` —los terceros que se saltaron y por qué—;
    quien las necesite mirar usa `_correr`.
    """
    return _correr(client, h, periodo, tipo)["generadas"]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _aprobar(client, h, liq_id):
    return client.post(f"{API}/{liq_id}/aprobar", headers=h)


def _pagar(client, h, liq_id):
    return client.post(f"{API}/{liq_id}/pagar", headers=h)


def _anular(client, h, liq_id):
    return client.post(f"{API}/{liq_id}/anular", headers=h)


def _recalcular(client, h, liq_id):
    return client.post(f"{API}/{liq_id}/recalcular", headers=h)


def _detalle(r) -> str:
    """El mensaje de error tal como lo lee el dueño en la pantalla.

    Las respuestas de error de esta API vienen como {"error": {"code":…, "detail":…}}, y
    lo que se comprueba en varias pruebas es el TEXTO: un mensaje que no nombra la
    liquidación con la que se cruza, o que no dice el orden en que hay que regenerar, deja
    al dueño atascado igual que un "no se puede" a secas.
    """
    cuerpo = r.json()
    return str(cuerpo.get("error", {}).get("detail", "")) if isinstance(cuerpo, dict) else ""


def _todas(client, h):
    r = client.get(f"{API}?page=1&page_size=200", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _anticipos(client, h):
    r = client.get(f"{ANT}?page=1&page_size=200", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _recepciones(client, h):
    r = client.get(f"{REC}/filtrar/avanzado?page=1&page_size=200", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ===========================================================================
# EL LIBRO DE LA PLATA: la cuenta que hace el dueño, medida en cada escenario
# ===========================================================================
def _libro(client, h, titulo=""):
    """Todas las cifras de plata del tenant, leídas por la API igual que el dueño.

    · LECHE LIQUIDADA: la suma de los `valor_total` de las liquidaciones vivas (no
      anuladas). Es la leche que el negocio reconoció por escrito.
    · ANTICIPOS ENTREGADOS: TODOS los anticipos que existen, aplicados o no. Esa
      plata salió de la caja en el momento en que se entregó en la mano.
    · PAGADO: la suma de `pagado` de las liquidaciones vivas.
    · POR PAGAR: la suma de los saldos POSITIVOS (lo que la quesera todavía debe).
    · LE QUEDAN DEBIENDO: la suma de los saldos NEGATIVOS que nadie ha cobrado
      todavía (sin `deuda_trasladada_a_id`), en positivo.
    """
    vivas = [liq for liq in _todas(client, h) if liq["estado"] != "anulada"]
    leche = sum((D(liq["valor_total"]) for liq in vivas), CERO)
    pagado = sum((D(liq["pagado"]) for liq in vivas), CERO)
    por_pagar = sum((D(liq["saldo"]) for liq in vivas if D(liq["saldo"]) > CERO), CERO)
    debiendo = sum(
        (
            -D(liq["saldo"])
            for liq in vivas
            if D(liq["saldo"]) < CERO and liq.get("deuda_trasladada_a_id") is None
        ),
        CERO,
    )
    todos_anticipos = _anticipos(client, h)
    anticipos = sum((D(a["valor"]) for a in todos_anticipos), CERO)
    # LOS ANTICIPOS QUE TODAVÍA NO ESTÁN EN NINGUNA LIQUIDACIÓN: esa plata salió de la
    # caja igual (se entregó en la mano) pero ninguna quincena la ha descontado, así que
    # es otra forma de "el tercero le queda debiendo" y entra en la cuenta por su lado.
    vivas_ids = {liq["id"] for liq in vivas}
    sueltos = sum(
        (
            D(a["valor"])
            for a in todos_anticipos
            if a["liquidacion_id"] is None or a["liquidacion_id"] not in vivas_ids
        ),
        CERO,
    )
    # Y los que quedaron pegados a una liquidación ANULADA (o borrada): la anulación los
    # suelta, y si alguno se quedara pegado sería plata presa que nadie va a descontar
    # nunca —`pendientes_de` solo recoge los que tienen `liquidacion_id` en nulo—.
    presos = sum(
        (
            D(a["valor"])
            for a in todos_anticipos
            if a["liquidacion_id"] is not None and a["liquidacion_id"] not in vivas_ids
        ),
        CERO,
    )
    # Lo que las liquidaciones vivas dicen estar cobrando de atrás, y lo que los
    # orígenes marcados dicen deber: tienen que ser la misma plata.
    cobrado_de_atras = sum((D(liq["saldo_anterior"]) for liq in vivas), CERO)
    marcados = sum(
        (
            D(liq["le_queda_debiendo"])
            for liq in vivas
            if liq.get("deuda_trasladada_a_id") is not None
        ),
        CERO,
    )
    libro = {
        "leche": leche,
        "anticipos": anticipos,
        "sueltos": sueltos,
        "presos": presos,
        "pagado": pagado,
        "por_pagar": por_pagar,
        "debiendo": debiendo,
        "cobrado_de_atras": cobrado_de_atras,
        "marcados": marcados,
        "vivas": vivas,
    }
    if titulo:
        print(f"\n  --- {titulo} ---")
        print(f"      leche liquidada .......... {leche}")
        print(f"      anticipos entregados ..... {anticipos}  (sueltos: {sueltos}, "
              f"presos en anuladas: {presos})")
        print(f"      plata pagada ............. {pagado}")
        print(f"      la quesera aun debe ...... {por_pagar}")
        print(f"      el tercero quedo debiendo  {debiendo}")
        print(f"      cobrado de atras ......... {cobrado_de_atras}  (marcados: {marcados})")
        print(f"      SALIO DE LA CAJA ......... {anticipos + pagado}")
    return libro


def _cuadra(libro, donde):
    """LA REGLA DE ORO DE ESTE ARCHIVO. Si esto se rompe, se creó o se perdió plata."""
    salio = libro["anticipos"] + libro["pagado"]
    esperado = (
        libro["leche"] - libro["por_pagar"] + libro["debiendo"] + libro["sueltos"]
    )
    assert salio == esperado, (
        f"[{donde}] LA PLATA NO CUADRA: salió de la caja {salio} pero la leche menos lo "
        f"que falta por pagar más lo que el tercero quedó debiendo (y los anticipos "
        f"sueltos) da {esperado} (diferencia {salio - esperado}). Libro: "
        + str({k: v for k, v in libro.items() if k != "vivas"})
    )
    assert libro["presos"] == CERO, (
        f"[{donde}] hay {libro['presos']} en anticipos pegados a una liquidación ANULADA: "
        "esa plata no la va a descontar nadie"
    )
    # Cada liquidación por dentro: saldo = total - anticipos - deuda vieja - pagado.
    for liq in libro["vivas"]:
        esperado_saldo = (
            D(liq["valor_total"])
            - D(liq["anticipos"])
            - D(liq["saldo_anterior"])
            - D(liq["pagado"])
        )
        assert D(liq["saldo"]) == esperado_saldo, (
            f"[{donde}] la liquidación {liq['periodo_inicio']}..{liq['periodo_fin']} "
            f"({liq['estado']}) dice saldo {liq['saldo']} y su propia resta da "
            f"{esperado_saldo}"
        )
        assert D(liq["neto_a_pagar"]) == D(liq["valor_total"]) - D(liq["anticipos"]) - D(
            liq["saldo_anterior"]
        ), f"[{donde}] el neto no es total - anticipos - deuda vieja en {liq['id']}"
        # El desglose de la deuda cobrada suma EXACTO la cifra grande.
        suma_desglose = sum(
            (D(o["le_queda_debiendo"]) for o in liq.get("deudas_cobradas") or []), CERO
        )
        assert suma_desglose == D(liq["saldo_anterior"]), (
            f"[{donde}] el desglose de «lo que quedó debiendo» suma {suma_desglose} y el "
            f"renglón dice {liq['saldo_anterior']} en {liq['id']}"
        )
    # Lo cobrado de atrás == lo que deben los orígenes marcados: ni un peso cobrado dos
    # veces, ni un peso de deuda huérfano.
    assert libro["cobrado_de_atras"] == libro["marcados"], (
        f"[{donde}] se está cobrando de atrás {libro['cobrado_de_atras']} pero los "
        f"orígenes marcados solo deben {libro['marcados']}"
    )


# ------------------------------------------------------- lectura del papel
_CIFRA = re.compile(r"(-?)\s*\$\s*(-?)([\d.]+(?:,\d{2})?)")


def texto_pdf(contenido):
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    return texto_pdf(r.content)


def renglon(papel, rotulo):
    inicio = papel.find(rotulo)
    assert inicio >= 0, f"el comprobante no trae el renglón «{rotulo}»:\n{papel}"
    resto = papel[inicio + len(rotulo):]
    encontrado = _CIFRA.search(resto)
    assert encontrado, f"el renglón «{rotulo}» salió sin cifra:\n{resto[:120]}"
    signo, interno, cifra = encontrado.groups()
    valor = D(cifra.replace(".", "").replace(",", "."))
    return -valor if (signo == "-" or interno == "-") else valor


# ===========================================================================
# 1. LA BASE: el caso del dueño, con el libro medido paso a paso
# ===========================================================================
def test_1_caso_base_la_plata_que_sale_es_la_leche_menos_los_anticipos(client, base_datos):
    """Q1: 100 L a $1.800 = $180.000 contra $300.000 de anticipo -> debe $120.000.
    Q2: 100 L a $2.500 = $250.000 - $120.000 = $130.000 a pagar.
    LECHE $430.000 = ANTICIPOS $300.000 + PAGADO $130.000. Ni un peso de más.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 1. CASO BASE =====")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _generar(client, h, Q1)[0]
    _cuadra(_libro(client, h, "solo Q1"), "solo Q1")

    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")
    assert D(q2["neto_a_pagar"]) == D("130000")
    _cuadra(_libro(client, h, "Q1 + Q2 generadas"), "Q1+Q2")

    assert _aprobar(client, h, q2["id"]).status_code == 200
    assert _pagar(client, h, q2["id"]).status_code == 200
    libro = _libro(client, h, "Q2 pagada")
    _cuadra(libro, "Q2 pagada")
    assert libro["anticipos"] + libro["pagado"] == D("430000")
    assert libro["leche"] == D("430000")
    # y la deuda de Q1 no quedó viva por ningún lado
    assert libro["debiendo"] == CERO
    assert _leer(client, h, q1["id"])["deuda_trasladada_a_id"] == q2["id"]


# ===========================================================================
# 2. TOCAR EL ORIGEN POR TODOS LOS CAMPOS Y EN TODOS LOS ESTADOS
# ===========================================================================
def _montar_par(client, h, nombre="Henri C", aprobar_q1=False):
    """Q1 debiendo $120.000 (marcada por Q2, que la cobra). Devuelve (prov, q1, q2, ant)."""
    prov = _proveedor(client, h, nombre)
    dia1 = _recepcion(client, h, prov, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    if aprobar_q1:
        assert _aprobar(client, h, q1["id"]).status_code == 200
    dia2 = _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")
    return prov, q1, q2, ant, dia1, dia2


def test_2_el_origen_marcado_esta_congelado_por_todos_los_caminos(client, base_datos):
    """Con la deuda ya cobrada, NINGÚN camino le puede mover las cifras al origen.

    Cada uno de estos, si pasara, dejaría a Q2 cobrando una deuda distinta a la que Q1
    dice deber: el desglose del comprobante de Q2 no sumaría, y saldría plata de más o
    de menos.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 2. EL ORIGEN CONGELADO (borrador y aprobada) =====")
    for aprobada in (False, True):
        nombre = "Henri C" if not aprobada else "Henri D"
        prov, q1, q2, ant, dia1, dia2 = _montar_par(client, h, nombre, aprobar_q1=aprobada)
        estado = _leer(client, h, q1["id"])["estado"]
        print(f"\n  Q1 en '{estado}', marcada por Q2:")

        pruebas = {
            "recalcular": _recalcular(client, h, q1["id"]),
            "anular": _anular(client, h, q1["id"]),
            "pagar": _pagar(client, h, q1["id"]),
            "precio del dia": client.put(
                f"{API}/{q1['id']}/detalles/{_leer(client, h, q1['id'])['detalles'][0]['id']}",
                json={"precio_litro": "2000"},
                headers=h,
            ),
            "abonar": client.post(
                f"{API}/{q1['id']}/pagos",
                json={"fecha": "2026-06-16", "valor": "1000"},
                headers=h,
            ),
            "corregir el anticipo": client.put(
                f"{ANT}/{ant['id']}", json={"valor": "200000"}, headers=h
            ),
            "borrar el anticipo": client.delete(f"{ANT}/{ant['id']}", headers=h),
            "editar los litros del dia": client.put(
                f"{REC}/{dia1['id']}", json={"cantidad_litros": "50"}, headers=h
            ),
            "editar el precio del dia": client.put(
                f"{REC}/{dia1['id']}", json={"precio_litro": "2000"}, headers=h
            ),
            "borrar el dia": client.delete(f"{REC}/{dia1['id']}", headers=h),
            "apagar el dia": client.put(
                f"{REC}/{dia1['id']}", json={"estado": "inactivo"}, headers=h
            ),
            "moverle la fecha al dia": client.put(
                f"{REC}/{dia1['id']}", json={"fecha": "2026-07-02"}, headers=h
            ),
            "mover el anticipo al periodo siguiente": client.put(
                f"{ANT}/{ant['id']}", json={"fecha": "2026-06-20"}, headers=h
            ),
        }
        for que, r in pruebas.items():
            print(f"      {que:42s} -> {r.status_code}")
            assert r.status_code >= 400, (
                f"CON Q1 EN '{estado}' SE PUDO {que.upper()} (HTTP {r.status_code}): "
                f"{r.text[:400]}"
            )
        _cuadra(_libro(client, h, f"tras los intentos con Q1 {estado}"), f"congelado {estado}")
        # y la cifra siguió exacta
        q1_ahora = _leer(client, h, q1["id"])
        assert D(q1_ahora["le_queda_debiendo"]) == D("120000")
        assert D(_leer(client, h, q2["id"])["saldo_anterior"]) == D("120000")


def test_2b_antes_de_que_la_deuda_se_cobre_si_se_puede_tocar_y_la_plata_cuadra(
    client, base_datos
):
    """Antes de que nadie le cobre la deuda, Q1 sí se toca — y el libro cuadra en cada
    paso. Se le corrige el anticipo a $250.000 (deuda $70.000) y después el día a 120 L
    (leche $216.000, deuda $34.000)."""
    h = auth_headers(client, "admin.a")
    print("\n===== 2b. TOCAR EL ORIGEN ANTES DE QUE SE COBRE =====")
    prov = _proveedor(client, h, "Henri C")
    dia = _recepcion(client, h, prov, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    _cuadra(_libro(client, h, "Q1 recien generada"), "2b generada")

    assert client.put(f"{ANT}/{ant['id']}", json={"valor": "250000"}, headers=h).status_code == 200
    assert D(_leer(client, h, q1["id"])["le_queda_debiendo"]) == D("70000")
    _cuadra(_libro(client, h, "anticipo corregido a 250.000"), "2b anticipo")

    assert client.put(
        f"{REC}/{dia['id']}", json={"cantidad_litros": "120"}, headers=h
    ).status_code == 200
    q1_ahora = _leer(client, h, q1["id"])
    assert D(q1_ahora["valor_total"]) == D("216000")
    assert D(q1_ahora["le_queda_debiendo"]) == D("34000")
    _cuadra(_libro(client, h, "dia corregido a 120 L"), "2b dia")

    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("34000")
    assert _aprobar(client, h, q2["id"]).status_code == 200
    assert _pagar(client, h, q2["id"]).status_code == 200
    libro = _libro(client, h, "Q2 pagada")
    _cuadra(libro, "2b final")
    # leche 216.000 + 250.000 = 466.000; anticipos 250.000; pagado 216.000
    assert libro["leche"] == D("466000")
    assert libro["anticipos"] + libro["pagado"] == D("466000")


# ===========================================================================
# 3. ANULAR, BORRAR Y REGENERAR EN TODAS LAS COMBINACIONES DE LAS DOS PUNTAS
# ===========================================================================
def test_3_anular_la_que_cobro_y_regenerar_no_cobra_la_deuda_dos_veces(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 3. ANULAR LA QUE COBRÓ Y REGENERAR =====")
    prov, q1, q2, ant, dia1, dia2 = _montar_par(client, h)

    assert _anular(client, h, q2["id"]).status_code == 200
    q1_libre = _leer(client, h, q1["id"])
    assert q1_libre["deuda_trasladada_a_id"] is None, "la deuda no volvió a quedar libre"
    q2_anulada = _leer(client, h, q2["id"])
    assert D(q2_anulada["saldo_anterior"]) == CERO
    _cuadra(_libro(client, h, "Q2 anulada"), "3 anulada")

    # Y se vuelve a generar: la deuda se cobra UNA sola vez.
    q2b = _generar(client, h, Q2)[0]
    assert D(q2b["saldo_anterior"]) == D("120000"), "la deuda no se volvió a cobrar"
    assert _aprobar(client, h, q2b["id"]).status_code == 200
    assert _pagar(client, h, q2b["id"]).status_code == 200
    libro = _libro(client, h, "Q2 regenerada y pagada")
    _cuadra(libro, "3 final")
    assert libro["anticipos"] + libro["pagado"] == D("430000")
    assert libro["leche"] == D("430000")


def test_3b_el_flujo_que_recomienda_el_mensaje_de_error(client, base_datos):
    """El mensaje dice: «Anule primero esa liquidación —así esta deuda vuelve a quedar
    libre— y vuelva a intentarlo». Se sigue al pie de la letra y se mide la plata.

    Y EL MENSAJE TIENE QUE DECIR EL ORDEN, CON LAS FECHAS. Este mismo consejo, hecho al
    revés —regenerando primero la quincena nueva—, saca $50.000 de más de la caja (está
    medido en la prueba 3c): el anticipo viejo se va a la quincena nueva y a la vieja se
    le paga completa. El hueco es de ORDEN, así que el mensaje nombra las dos quincenas y
    dice por cuál empezar. Haciéndole caso, de la caja salen los $430.000 de la leche y
    nadie queda debiendo un peso.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 3b. EL FLUJO QUE RECOMIENDA EL ERROR =====")
    prov, q1, q2, ant, dia1, dia2 = _montar_par(client, h)
    r = _recalcular(client, h, q1["id"])
    assert r.status_code == 422
    print(f"      el error dice: {r.text[:420]}")
    assert "Anule primero" in r.text
    # EL ORDEN, con las dos fechas concretas y en su sitio: primero la vieja.
    assert "empiece por la más vieja —la del 01/06/2026 al 15/06/2026—" in r.text, r.text
    assert "siga con la del 16/06/2026 al 30/06/2026" in r.text, r.text

    assert _anular(client, h, q2["id"]).status_code == 200
    assert _recalcular(client, h, q1["id"]).status_code == 200
    assert _anular(client, h, q1["id"]).status_code == 200
    _cuadra(_libro(client, h, "las dos anuladas"), "3b anuladas")

    # Se regeneran EN ORDEN (primero la vieja) y todo vuelve a su sitio.
    q1b = _generar(client, h, Q1)[0]
    assert D(q1b["le_queda_debiendo"]) == D("120000")
    q2b = _generar(client, h, Q2)[0]
    assert D(q2b["saldo_anterior"]) == D("120000")
    assert _aprobar(client, h, q2b["id"]).status_code == 200
    assert _pagar(client, h, q2b["id"]).status_code == 200
    libro = _libro(client, h, "regeneradas en orden y pagadas")
    _cuadra(libro, "3b final")
    # LA CIFRA EXACTA DE HACERLE CASO AL MENSAJE: sale de la caja la leche y nada más.
    assert libro["anticipos"] + libro["pagado"] == libro["leche"] == D("430000")
    assert libro["debiendo"] == CERO


def test_3c_regenerar_al_reves_el_anticipo_viejo_se_lo_lleva_la_quincena_nueva(
    client, base_datos
):
    """MISMO FLUJO, REGENERANDO AL REVÉS (primero la quincena nueva): POR QUÉ EL MENSAJE
    TIENE QUE DECIR EL ORDEN.

    Es el orden que sale solo cuando el usuario está parado en la pantalla de la quincena
    que acaba de anular. LAS CIFRAS: el anticipo de $300.000 (fechado el 01 de junio) lo
    recoge la quincena NUEVA —`pendientes_de` mira solo `fecha <= periodo_fin`, y el 01 de
    junio también es <= 30 de junio—, así que la quincena vieja queda SIN anticipo y con
    $180.000 por pagar, que se le pagan completos. De la caja salen $480.000 por $430.000
    de leche y el proveedor queda debiendo $50.000.

    La plata no se pierde —queda registrada como deuda, y la cuenta del dueño cuadra— pero
    salieron $50.000 que ya se le habían adelantado, y si el productor no vuelve a entregar
    leche no vuelven. ES UN HUECO DE ORDEN, y por eso el mensaje que manda a anular ahora
    dice por cuál quincena empezar, con las fechas (se comprueba en la prueba 3b, y acá se
    comprueba que el consejo también sale por el lado de Recepción diaria, que es donde el
    dueño se lo encuentra primero).
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 3c. REGENERAR AL REVÉS =====")
    prov, q1, q2, ant, dia1, dia2 = _montar_par(client, h)
    # El dueño intenta corregirle el día a la quincena vieja y lee el consejo COMPLETO:
    # qué anular, y en qué orden volver a generarlas.
    r = client.put(f"{REC}/{dia1['id']}", json={"cantidad_litros": "90"}, headers=h)
    assert r.status_code == 422
    print(f"      el consejo: ...{r.text[-320:]}")
    assert "anule primero esa liquidación" in r.text.lower(), r.text
    assert "empiece por la más vieja —la del 01/06/2026 al 15/06/2026—" in r.text, r.text
    assert "siga con la del 16/06/2026 al 30/06/2026" in r.text, r.text

    assert _anular(client, h, q2["id"]).status_code == 200
    assert _anular(client, h, q1["id"]).status_code == 200

    q2b = _generar(client, h, Q2)[0]
    q1b = _generar(client, h, Q1)[0]
    print(f"      Q2 nueva: total={q2b['valor_total']} anticipos={q2b['anticipos']} "
          f"saldo={q2b['saldo']} debe={q2b['le_queda_debiendo']}")
    print(f"      Q1 nueva: total={q1b['valor_total']} anticipos={q1b['anticipos']} "
          f"saldo={q1b['saldo']}")
    # El anticipo viejo se fue a la quincena NUEVA y la vieja quedó sin nada que descontar.
    assert D(q2b["anticipos"]) == D("300000")
    assert D(q1b["anticipos"]) == CERO
    assert D(q1b["saldo"]) == D("180000")
    libro = _libro(client, h, "regeneradas al reves")
    _cuadra(libro, "3c generadas")

    # Se paga lo que el sistema dice que hay que pagar, y se mide contra la leche.
    for liq in libro["vivas"]:
        if D(liq["saldo"]) > CERO:
            assert _aprobar(client, h, liq["id"]).status_code == 200
            assert _pagar(client, h, liq["id"]).status_code == 200
    libro = _libro(client, h, "pagado lo que el sistema pide")
    _cuadra(libro, "3c pagado")
    salio = libro["anticipos"] + libro["pagado"]
    print(f"      SALIO {salio} por LECHE {libro['leche']}; el tercero quedó debiendo "
          f"{libro['debiendo']}")
    assert libro["leche"] == D("430000")
    # LAS CIFRAS DEL HUECO DE ORDEN, tal como se midieron: $480.000 por $430.000.
    assert salio == D("480000")
    assert libro["debiendo"] == D("50000")
    # No se pierde plata: lo que salió de más queda como deuda por cobrar.
    assert salio - libro["leche"] == libro["debiendo"]


def test_3d_anular_la_que_dejo_la_deuda_antes_de_que_se_cobre(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 3d. ANULAR EL ORIGEN ANTES DE QUE SE COBRE =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    assert _anular(client, h, q1["id"]).status_code == 200
    _cuadra(_libro(client, h, "Q1 anulada, anticipo suelto"), "3d anulada")
    # La deuda de una anulada NO se le cobra a nadie.
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    print(f"      Q2: total={q2['valor_total']} anticipos={q2['anticipos']} "
          f"saldo_anterior={q2['saldo_anterior']} saldo={q2['saldo']}")
    assert D(q2["saldo_anterior"]) == CERO, "se le cobró la deuda de una ANULADA"
    _cuadra(_libro(client, h, "Q2 generada"), "3d final")


def test_3e_anular_dos_veces_no_suelta_ni_cobra_nada_de_mas(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 3e. ANULAR DOS VECES =====")
    prov, q1, q2, ant, dia1, dia2 = _montar_par(client, h)
    assert _anular(client, h, q2["id"]).status_code == 200
    r = _anular(client, h, q2["id"])
    print(f"      segundo anular -> {r.status_code}")
    assert r.status_code >= 400
    _cuadra(_libro(client, h, "tras el doble anular"), "3e")
    assert _leer(client, h, q1["id"])["deuda_trasladada_a_id"] is None


# ===========================================================================
# 4. CADENAS DE TRES, CUATRO Y CINCO QUINCENAS (Y UNA ANULADA EN EL MEDIO)
# ===========================================================================
def _cadena(client, h, prov, periodos_y_litros, precio="1800"):
    generadas = []
    for periodo, dia, litros in periodos_y_litros:
        _recepcion(client, h, prov, dia, litros, precio=precio)
        generadas.append(_generar(client, h, periodo)[0])
    return generadas


def test_4_cadena_de_cinco_quincenas_cuadra_al_centavo(client, base_datos):
    """Cinco quincenas seguidas con un anticipo enorme al principio.

    litros a $1.800:  Q1 10 L, Q2 20 L, Q3 30 L, Q4 40 L, Q5 400 L
    leche: 18.000 + 36.000 + 54.000 + 72.000 + 720.000 = 900.000
    anticipo entregado: 500.000
    -> al final tienen que salir 900.000 - 500.000 = 400.000 y ni un peso más.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 4. CADENA DE CINCO =====")
    prov = _proveedor(client, h, "Henri C")
    _anticipo(client, h, "2026-06-01", "500000", proveedor=prov)
    pasos = [
        (Q1, "2026-06-02", "10"),
        (Q2, "2026-06-20", "20"),
        (Q3, "2026-07-02", "30"),
        (Q4, "2026-07-20", "40"),
        (Q5, "2026-08-02", "400"),
    ]
    for i, (periodo, dia, litros) in enumerate(pasos, start=1):
        _recepcion(client, h, prov, dia, litros)
        liq = _generar(client, h, periodo)[0]
        print(f"      Q{i} total={liq['valor_total']} ant={liq['anticipos']} "
              f"deuda_vieja={liq['saldo_anterior']} saldo={liq['saldo']}")
        _cuadra(_libro(client, h, f"tras Q{i}"), f"cadena Q{i}")
        if D(liq["saldo"]) > CERO:
            assert _aprobar(client, h, liq["id"]).status_code == 200
            assert _pagar(client, h, liq["id"]).status_code == 200
        else:
            # No se puede marcar pagada la que nadie pagó.
            assert _aprobar(client, h, liq["id"]).status_code == 200
            r = _pagar(client, h, liq["id"])
            assert r.status_code == 422, f"se marcó pagada sin plata: {r.text[:200]}"
        _cuadra(_libro(client, h, f"tras cobrar Q{i}"), f"cadena pago Q{i}")

    libro = _libro(client, h, "cadena completa")
    _cuadra(libro, "cadena final")
    assert libro["leche"] == D("900000")
    assert libro["anticipos"] == D("500000")
    assert libro["pagado"] == D("400000"), f"salió {libro['pagado']} y debía salir 400000"
    assert libro["debiendo"] == CERO


def test_4b_cadena_con_una_anulada_en_el_medio(client, base_datos):
    """Cadena Q1->Q2->Q3->Q4 y se anula la del MEDIO por el único camino posible:
    primero la que le cobró la deuda (Q4, Q3) y después Q2. La plata tiene que cuadrar
    en cada paso, y al regenerar la deuda tiene que recorrer la cadena UNA vez."""
    h = auth_headers(client, "admin.a")
    print("\n===== 4b. UNA ANULADA EN EL MEDIO =====")
    prov = _proveedor(client, h, "Henri C")
    _anticipo(client, h, "2026-06-01", "500000", proveedor=prov)
    liqs = []
    for periodo, dia, litros in (
        (Q1, "2026-06-02", "10"),
        (Q2, "2026-06-20", "20"),
        (Q3, "2026-07-02", "30"),
        (Q4, "2026-07-20", "40"),
    ):
        _recepcion(client, h, prov, dia, litros)
        liqs.append(_generar(client, h, periodo)[0])
    q1, q2, q3, q4 = liqs
    _cuadra(_libro(client, h, "cadena de cuatro"), "4b armada")

    # La del medio no se puede anular de una: hay que ir de la punta hacia atrás.
    assert _anular(client, h, q2["id"]).status_code == 422
    assert _anular(client, h, q4["id"]).status_code == 200
    _cuadra(_libro(client, h, "Q4 anulada"), "4b q4")
    assert _anular(client, h, q3["id"]).status_code == 200
    _cuadra(_libro(client, h, "Q3 anulada"), "4b q3")
    assert _anular(client, h, q2["id"]).status_code == 200
    libro = _libro(client, h, "Q2 anulada: solo queda Q1")
    _cuadra(libro, "4b q2")
    assert libro["debiendo"] == D("482000"), (
        f"la deuda de Q1 (18.000 de leche contra 500.000) quedó en {libro['debiendo']}"
    )

    # Se regenera de atrás hacia adelante y la deuda vuelve a recorrer la cadena.
    for periodo in (Q2, Q3, Q4):
        liq = _generar(client, h, periodo)[0]
        print(f"      regenerada {periodo[0]}: deuda_vieja={liq['saldo_anterior']} "
              f"saldo={liq['saldo']}")
        _cuadra(_libro(client, h, f"regenerada {periodo[0]}"), f"4b regen {periodo[0]}")
    libro = _libro(client, h, "cadena rehecha")
    _cuadra(libro, "4b final")
    # leche 18+36+54+72 = 180.000 contra 500.000 de anticipo -> debe 320.000
    assert libro["leche"] == D("180000")
    assert libro["debiendo"] == D("320000")
    assert libro["anticipos"] + libro["pagado"] == D("500000")


# ===========================================================================
# 5. FECHAS REVUELTAS, PERÍODOS QUE SE PISAN, UN SOLO DÍA, GENERADA DOS VECES
# ===========================================================================
def test_5_generar_al_reves_la_deuda_no_viaja_al_pasado(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 5. FECHAS REVUELTAS =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    # Primero la NUEVA: se lleva el anticipo (fecha <= periodo_fin) y queda debiendo.
    q2 = _generar(client, h, Q2)[0]
    print(f"      Q2 (16-30) primero: total={q2['valor_total']} ant={q2['anticipos']} "
          f"deuda_vieja={q2['saldo_anterior']} debe={q2['le_queda_debiendo']}")
    assert D(q2["saldo_anterior"]) == CERO
    q1 = _generar(client, h, Q1)[0]
    print(f"      Q1 (01-15) despues: total={q1['valor_total']} ant={q1['anticipos']} "
          f"deuda_vieja={q1['saldo_anterior']} saldo={q1['saldo']}")
    assert D(q1["saldo_anterior"]) == CERO, "la deuda de una quincena POSTERIOR viajó al pasado"
    _cuadra(_libro(client, h, "generadas al reves"), "5 revuelto")

    assert _aprobar(client, h, q1["id"]).status_code == 200
    assert _pagar(client, h, q1["id"]).status_code == 200
    libro = _libro(client, h, "pagada la vieja completa")
    _cuadra(libro, "5 final")
    salio = libro["anticipos"] + libro["pagado"]
    print(f"      SALIO {salio} por LECHE {libro['leche']}; queda debiendo "
          f"{libro['debiendo']}")
    assert salio - libro["leche"] == libro["debiendo"]


def test_5b_periodos_que_se_pisan_y_la_misma_quincena_dos_veces(client, base_datos):
    """El período que SE PISA con otro NO SE GENERA —al tercero se lo SALTA Y LO DICE—, y
    la misma quincena otra vez no saca nada.

    LO QUE COSTABA, medido cuando esto se dejaba: la quincena "del 10 al 20" traía días
    nuevos por $200.000 y NO le cobraba los $120.000 que Henri debía de la del 01 al 15
    —la deuda solo viaja a un período que empiece después de que el origen TERMINE—, así
    que de la caja salían $500.000 por $380.000 de leche.

    AHORA NO REBOTA LA CORRIDA: el tercero del cruce sale en `omitidas` con su motivo y
    los demás quedan con su comprobante (el porqué, en `_omitido_por_periodo_cruzado`; el
    detalle del guardia, en la tanda 30 de
    tests/test_liquidacion_deuda_arrastrada_puertas.py). Para el tercero omitido el
    resultado en plata es el mismo que cuando rebotaba: no sale un peso.

    Lo que el dueño hace en su lugar es generar el período QUE NO SE MONTA (del 16 al 20),
    y ahí sí se le cobra la deuda: es el mismo camino de siempre, con las fechas bien.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 5b. PERÍODOS QUE SE PISAN Y REPETIDA =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    assert D(q1["le_queda_debiendo"]) == D("120000")

    # La MISMA quincena otra vez: no puede salir una segunda liquidación ni cobrarse
    # la deuda de nadie. Ni siquiera llega al guardia del cruce —no queda un día sin
    # liquidar en ese período, así que no hay a quién generarle nada—.
    otra_vez = _generar(client, h, Q1)
    print(f"      generar Q1 otra vez -> {len(otra_vez)} liquidaciones")
    assert otra_vez == [], f"se generó una segunda liquidación del mismo período: {otra_vez}"

    # Un período que SE PISA con Q1 y trae días nuevos: NO SE GENERA, y lo DICE.
    _recepcion(client, h, prov, "2026-06-12", "50", precio="2000")
    _recepcion(client, h, prov, "2026-06-18", "50", precio="2000")
    corrida = _correr(client, h, ("2026-06-10", "2026-06-20"))
    print(f"      generar 10-20 (se pisa con Q1) -> generadas={len(corrida['generadas'])} "
          f"omitidas={len(corrida['omitidas'])}")
    assert corrida["generadas"] == []
    omitida = corrida["omitidas"][0]
    print(f"      omitida: {omitida['tercero_nombre']} ({omitida['cuenta']}) "
          f"{omitida['motivo'][:120]}")
    assert (omitida["tercero_nombre"], omitida["cuenta"], omitida["motivo_codigo"]) == (
        "Henri C", "leche", "periodo_cruzado"
    )
    assert "Henri C ya tiene una liquidación de leche del 01/06/2026 al 15/06/2026" in (
        omitida["motivo"]
    )
    # Y NO QUEDÓ NADA A MEDIO HACER: sigue habiendo una sola liquidación.
    assert len([liq for liq in _todas(client, h) if liq["estado"] != "anulada"]) == 1
    _cuadra(_libro(client, h, "el periodo que se pisa se omitió"), "5b omitido")

    # Con las fechas bien —del 16 al 20, que no se monta— sí sale, y le cobra la deuda.
    # El día 12 se queda sin liquidar: está dentro de la quincena que ya se cerró, y para
    # meterlo hay que anular esa quincena y volver a generarla.
    buena = _generar(client, h, ("2026-06-16", "2026-06-20"))
    assert len(buena) == 1
    print(f"      16-20: total={buena[0]['valor_total']} "
          f"deuda_vieja={buena[0]['saldo_anterior']} saldo={buena[0]['saldo']}")
    assert D(buena[0]["valor_total"]) == D("100000")
    assert D(buena[0]["saldo_anterior"]) == D("120000"), "no le cobró la deuda de Q1"
    assert D(buena[0]["le_queda_debiendo"]) == D("20000")
    _cuadra(_libro(client, h, "con el periodo que no se monta"), "5b 16-20")

    # Un período de UN SOLO DÍA.
    _recepcion(client, h, prov, "2026-07-05", "10", precio="2000")
    un_dia = _generar(client, h, ("2026-07-05", "2026-07-05"))
    for liq in un_dia:
        print(f"      un solo dia: total={liq['valor_total']} "
              f"deuda_vieja={liq['saldo_anterior']} saldo={liq['saldo']}")
    _cuadra(_libro(client, h, "con un periodo de un solo dia"), "5b un dia")

    # Y se paga todo lo que el sistema pida: la plata tiene que cuadrar.
    for liq in _todas(client, h):
        if liq["estado"] == "borrador" and D(liq["saldo"]) > CERO:
            assert _aprobar(client, h, liq["id"]).status_code == 200
            assert _pagar(client, h, liq["id"]).status_code == 200
    libro = _libro(client, h, "todo pagado")
    _cuadra(libro, "5b final")
    salio = libro["anticipos"] + libro["pagado"]
    assert salio - libro["leche"] == libro["debiendo"] - libro["por_pagar"], (
        f"salió {salio}, leche {libro['leche']}, debiendo {libro['debiendo']}, "
        f"por pagar {libro['por_pagar']}"
    )


# ===========================================================================
# 6. EL ANTICIPO: NUEVO, CORREGIDO, BORRADO, CON FECHA VIEJA, MÁS GRANDE QUE TODO
# ===========================================================================
def test_6_anticipo_nuevo_despues_de_que_la_deuda_se_cobro(client, base_datos):
    """Un anticipo NUEVO fechado en el período de Q1, con Q1 ya marcada.

    No puede entrar a Q1 (le cambiaría la deuda a un comprobante ya emitido) y no
    puede perderse: lo tiene que recoger la próxima liquidación.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 6. ANTICIPO NUEVO CON LA DEUDA YA COBRADA =====")
    prov, q1, q2, ant, dia1, dia2 = _montar_par(client, h)
    nuevo = _anticipo(client, h, "2026-06-05", "50000", proveedor=prov)
    q1_ahora = _leer(client, h, q1["id"])
    print(f"      Q1 tras el anticipo nuevo: ant={q1_ahora['anticipos']} "
          f"debe={q1_ahora['le_queda_debiendo']}")
    assert D(q1_ahora["anticipos"]) == D("300000"), "el anticipo nuevo se metió a Q1"
    # aprobar Q1 tampoco lo barre
    assert _aprobar(client, h, q1["id"]).status_code == 200
    q1_ahora = _leer(client, h, q1["id"])
    assert D(q1_ahora["anticipos"]) == D("300000"), "aprobar barrió el anticipo nuevo"
    _cuadra(_libro(client, h, "anticipo nuevo suelto"), "6 suelto")

    # Q2 sí lo recoge al aprobar: no se pierde.
    assert _aprobar(client, h, q2["id"]).status_code == 200
    q2_ahora = _leer(client, h, q2["id"])
    print(f"      Q2 al aprobar: ant={q2_ahora['anticipos']} "
          f"deuda_vieja={q2_ahora['saldo_anterior']} saldo={q2_ahora['saldo']}")
    assert D(q2_ahora["anticipos"]) == D("50000"), "el anticipo nuevo se perdió"
    assert D(q2_ahora["saldo"]) == D("80000")
    assert _pagar(client, h, q2["id"]).status_code == 200
    libro = _libro(client, h, "todo pagado")
    _cuadra(libro, "6 final")
    # leche 430.000 = anticipos 350.000 + pagado 80.000
    assert libro["leche"] == D("430000")
    assert libro["anticipos"] + libro["pagado"] == D("430000")
    assert nuevo["id"] is not None


def test_6b_anticipo_mas_grande_que_todo_y_borrado(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 6b. ANTICIPO GIGANTE, CORREGIDO Y BORRADO =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    ant = _anticipo(client, h, "2026-06-01", "9000000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    assert D(q1["le_queda_debiendo"]) == D("8820000")
    _cuadra(_libro(client, h, "anticipo gigante"), "6b gigante")

    # Se borra antes de que nadie le cobre la deuda: la liquidación se recuadra.
    assert client.delete(f"{ANT}/{ant['id']}", headers=h).status_code == 204
    q1_ahora = _leer(client, h, q1["id"])
    print(f"      tras borrar el anticipo: ant={q1_ahora['anticipos']} "
          f"saldo={q1_ahora['saldo']}")
    assert D(q1_ahora["anticipos"]) == CERO
    assert D(q1_ahora["saldo"]) == D("180000")
    _cuadra(_libro(client, h, "anticipo borrado"), "6b borrado")

    # Y con la deuda ya cobrada, el borrado rebota (no puede descuadrar dos papeles).
    ant2 = _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    assert _recalcular(client, h, q1["id"]).status_code == 200
    assert D(_leer(client, h, q1["id"])["le_queda_debiendo"]) == D("120000")
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    assert D(q2["saldo_anterior"]) == D("120000")
    r = client.delete(f"{ANT}/{ant2['id']}", headers=h)
    print(f"      borrar el anticipo con la deuda cobrada -> {r.status_code}")
    assert r.status_code >= 400
    _cuadra(_libro(client, h, "final"), "6b final")


def test_6c_anticipo_fechado_en_el_periodo_viejo_de_una_quincena_ya_cerrada(
    client, base_datos
):
    """Un anticipo con fecha VIEJA registrado cuando la quincena vieja ya está pagada.

    No puede entrar a la pagada, y no puede perderse: `pendientes_de` mira
    `fecha <= periodo_fin`, así que lo recoge la siguiente.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 6c. ANTICIPO CON FECHA VIEJA =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    q1 = _generar(client, h, Q1)[0]
    assert _aprobar(client, h, q1["id"]).status_code == 200
    assert _pagar(client, h, q1["id"]).status_code == 200
    _cuadra(_libro(client, h, "Q1 pagada"), "6c q1")

    _anticipo(client, h, "2026-06-03", "300000", proveedor=prov)
    assert D(_leer(client, h, q1["id"])["anticipos"]) == CERO, "entró a una PAGADA"
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[0]
    print(f"      Q2: total={q2['valor_total']} ant={q2['anticipos']} saldo={q2['saldo']}")
    assert D(q2["anticipos"]) == D("300000"), "el anticipo con fecha vieja se perdió"
    _cuadra(_libro(client, h, "Q2 generada"), "6c final")


# ===========================================================================
# 7. DOS PETICIONES A LA VEZ SOBRE EL MISMO TERCERO
# ===========================================================================
def test_7_dos_generar_seguidos_no_cobran_la_misma_deuda_dos_veces(client, base_datos):
    """Dos «Generar» sobre el mismo tercero y el mismo período: la deuda una sola vez.

    (Sobre SQLite el FOR UPDATE se descarta en silencio, así que esto no mide la
    carrera de verdad; mide que la marca sea suficiente cuando las dos llegan.)
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 7. DOS GENERAR SEGUIDOS =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    _generar(client, h, Q1)
    _recepcion(client, h, prov, "2026-06-20", "60", precio="2500")
    _recepcion(client, h, prov, "2026-06-21", "40", precio="2500")
    primera = _generar(client, h, Q2)
    segunda = _generar(client, h, Q2)
    print(f"      primera {len(primera)} liquidacion(es), segunda {len(segunda)}")
    assert segunda == []
    libro = _libro(client, h, "tras los dos generar")
    _cuadra(libro, "7")
    assert libro["cobrado_de_atras"] == D("120000")

    # Y dos períodos distintos, uno detrás del otro, tampoco se reparten la misma deuda.
    _recepcion(client, h, prov, "2026-07-02", "10", precio="2000")
    _recepcion(client, h, prov, "2026-07-20", "10", precio="2000")
    a = _generar(client, h, Q3)
    b = _generar(client, h, Q4)
    for liq in a + b:
        print(f"      {liq['periodo_inicio']}: deuda_vieja={liq['saldo_anterior']}")
    _cuadra(_libro(client, h, "dos periodos mas"), "7b")


# ===========================================================================
# 8. QUE NO SE COBRE DOS VECES POR NINGÚN CAMINO NUEVO
# ===========================================================================
def test_8_la_deuda_no_se_cobra_dos_veces_por_ningun_camino(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 8. NI DOS VECES, NI A OTRO, NI DE OTRO TIPO =====")
    henri = _proveedor(client, h, "Henri")
    henri_c = _proveedor(client, h, "Henri C")
    ruta = _ruta(client, h, "Napoles")
    t = _transportador(client, h, "Henri", [(ruta, "200")])

    # Henri (proveedor) queda debiendo; Henri C y el transportador Henri no.
    _recepcion(client, h, henri, "2026-06-02", "100", t=t, ruta=ruta)
    _recepcion(client, h, henri_c, "2026-06-03", "100", t=t, ruta=ruta)
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    _generar(client, h, Q1, tipo="ambos")
    _cuadra(_libro(client, h, "Q1 de los tres"), "8 q1")

    _recepcion(client, h, henri, "2026-06-20", "100", precio="2500", t=t, ruta=ruta)
    _recepcion(client, h, henri_c, "2026-06-21", "100", precio="2500", t=t, ruta=ruta)
    q2s = _generar(client, h, Q2, tipo="ambos")
    for liq in q2s:
        quien = liq.get("proveedor_nombre") or liq.get("transportador_nombre")
        print(f"      {liq['tipo']:14s} {quien:10s} deuda_vieja={liq['saldo_anterior']}")
    de_henri = [
        liq for liq in q2s if liq["tipo"] == "proveedor" and liq["proveedor_nombre"] == "Henri"
    ]
    assert len(de_henri) == 1
    assert D(de_henri[0]["saldo_anterior"]) == D("120000")
    otros = [liq for liq in q2s if liq is not de_henri[0]]
    for liq in otros:
        assert D(liq["saldo_anterior"]) == CERO, (
            f"la deuda de Henri (proveedor) se le cobró a {liq['tipo']} "
            f"{liq.get('proveedor_nombre') or liq.get('transportador_nombre')}"
        )
    libro = _libro(client, h, "Q2 de los tres")
    _cuadra(libro, "8 q2")
    assert libro["cobrado_de_atras"] == D("120000")

    # Tercera quincena: la deuda ya cobrada no vuelve a salir.
    _recepcion(client, h, henri, "2026-07-02", "100", precio="2500", t=t, ruta=ruta)
    q3s = _generar(client, h, Q3, tipo="ambos")
    for liq in q3s:
        assert D(liq["saldo_anterior"]) == CERO, "la deuda se cobró por segunda vez"
    _cuadra(_libro(client, h, "Q3"), "8 q3")


# ===========================================================================
# 9. EL PAPEL: OFICIAL Y PRELIMINAR, Y LA MISMA CIFRA EN LA API
# ===========================================================================
def test_9_el_desglose_suma_exacto_en_el_papel_oficial_y_en_el_preliminar(
    client, base_datos
):
    h = auth_headers(client, "admin.a")
    print("\n===== 9. EL PAPEL SUMA DE ARRIBA ABAJO =====")
    prov, q1, q2, ant, dia1, dia2 = _montar_par(client, h)

    papel = _pdf(client, h, q2["id"])
    bruto = renglon(papel, "Valor bruto")
    bonif = renglon(papel, "Bonificaciones")
    desc = renglon(papel, "Descuentos")
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    deuda = renglon(papel, "Lo que quedó debiendo de la quincena pasada")
    saldo = renglon(papel, "SALDO A PAGAR")
    print(f"      OFICIAL Q2: bruto={bruto} bonif={bonif} desc={desc} TOTAL={total} "
          f"ant={anticipos} deuda={deuda} SALDO={saldo}")
    assert bruto + bonif + desc == total, "bruto + bonificaciones - descuentos != VALOR TOTAL"
    assert total + anticipos + deuda == saldo, "TOTAL - anticipos - deuda != SALDO"
    api = _leer(client, h, q2["id"])
    assert saldo == D(api["saldo"]), "el papel y la API dicen distinto"
    assert -deuda == D(api["saldo_anterior"])
    assert total == D(api["valor_total"])

    # El origen: su papel dice LE QUEDA DEBIENDO y nombra en cuál se le cobró.
    papel1 = _pdf(client, h, q1["id"])
    total1 = renglon(papel1, "VALOR TOTAL")
    ant1 = renglon(papel1, "Anticipos aplicados")
    debe1 = renglon(papel1, "LE QUEDA DEBIENDO")
    print(f"      OFICIAL Q1: TOTAL={total1} ant={ant1} DEBE={debe1}")
    assert total1 + ant1 == -debe1, "en el origen la resta no da la deuda"
    assert debe1 == D(_leer(client, h, q1["id"])["le_queda_debiendo"])
    assert "16/06/2026 al 30/06/2026" in papel1, "el papel no dice dónde se le cobró"

    # EL PRELIMINAR de la quincena siguiente: avisa de la deuda que falta por cobrar.
    _recepcion(client, h, prov, "2026-07-02", "100", precio="2500")
    r = client.post(
        f"{API}/previsualizar/pdf",
        json={
            "periodo_inicio": Q3[0],
            "periodo_fin": Q3[1],
            "tipo": "proveedor",
            "tercero_id": prov["id"],
        },
        headers=h,
    )
    assert r.status_code == 200
    avance = texto_pdf(r.content)
    tot_av = renglon(avance, "VALOR TOTAL")
    ant_av = renglon(avance, "Anticipos aplicados")
    saldo_av = renglon(avance, "SALDO ESTIMADO")
    print(f"      PRELIMINAR Q3: TOTAL={tot_av} ant={ant_av} SALDO={saldo_av}")
    assert tot_av + ant_av == saldo_av, "el preliminar no suma de arriba abajo"
    pre = client.post(
        f"{API}/previsualizar",
        json={
            "periodo_inicio": Q3[0],
            "periodo_fin": Q3[1],
            "tipo": "proveedor",
            "tercero_id": prov["id"],
        },
        headers=h,
    ).json()
    assert saldo_av == D(pre[0]["saldo"]), "el preliminar y la pantalla dicen distinto"


def test_9b_el_papel_del_medio_de_la_cadena_suma_con_las_dos_puntas(client, base_datos):
    """La quincena que COBRÓ una deuda y volvió a quedar debiendo: su papel tiene que
    sumar igual, con el renglón de la deuda vieja y el rótulo LE QUEDA DEBIENDO."""
    h = auth_headers(client, "admin.a")
    print("\n===== 9b. EL PAPEL DEL MEDIO DE LA CADENA =====")
    prov = _proveedor(client, h, "Henri C")
    _anticipo(client, h, "2026-06-01", "500000", proveedor=prov)
    _recepcion(client, h, prov, "2026-06-02", "100")   # 180.000
    _generar(client, h, Q1)                            # debe 320.000
    _recepcion(client, h, prov, "2026-06-20", "100")   # 180.000
    q2 = _generar(client, h, Q2)[0]                    # 180.000 - 320.000 = -140.000
    print(f"      Q2: total={q2['valor_total']} deuda_vieja={q2['saldo_anterior']} "
          f"debe={q2['le_queda_debiendo']}")
    assert D(q2["saldo_anterior"]) == D("320000")
    assert D(q2["le_queda_debiendo"]) == D("140000")
    papel = _pdf(client, h, q2["id"])
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    deuda = renglon(papel, "Lo que quedó debiendo de la quincena pasada")
    debe = renglon(papel, "LE QUEDA DEBIENDO")
    print(f"      papel: TOTAL={total} ant={anticipos} deuda={deuda} DEBE={debe}")
    assert total + anticipos + deuda == -debe, "el papel del medio no suma"
    _cuadra(_libro(client, h, "cadena de dos en negativo"), "9b")


# ===========================================================================
# 10. EL TABLERO Y EL BALANCE DICEN LA MISMA PLATA QUE LAS LIQUIDACIONES
# ===========================================================================
def test_10_el_tablero_y_el_balance_cuadran_con_las_liquidaciones(client, base_datos):
    h = auth_headers(client, "admin.a")
    print("\n===== 10. TABLERO Y BALANCE =====")
    prov = _proveedor(client, h, "Henri C")
    otro = _proveedor(client, h, "Dona Rosa")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _recepcion(client, h, otro, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    _generar(client, h, Q1)
    libro = _libro(client, h, "una debiendo y una por pagar")
    _cuadra(libro, "10")

    tablero = client.get("/api/v1/reportes/dashboard", headers=h).json()
    balance = client.get("/api/v1/contabilidad/balance", headers=h).json()
    print(f"      tablero por_pagar={tablero['liquidaciones_por_pagar']} "
          f"debiendo={tablero['terceros_le_quedan_debiendo']}")
    print(f"      balance por_pagar={balance['liquidaciones_por_pagar']} "
          f"debiendo={balance['terceros_le_quedan_debiendo']}")
    assert D(tablero["liquidaciones_por_pagar"]) == libro["por_pagar"]
    assert D(tablero["terceros_le_quedan_debiendo"]) == libro["debiendo"]
    assert D(balance["liquidaciones_por_pagar"]) == libro["por_pagar"]
    assert D(balance["terceros_le_quedan_debiendo"]) == libro["debiendo"]

    # Y cuando la deuda se cobra, deja de contarse como por cobrar.
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")
    _generar(client, h, Q2)
    libro = _libro(client, h, "deuda ya cobrada")
    _cuadra(libro, "10b")
    tablero = client.get("/api/v1/reportes/dashboard", headers=h).json()
    assert D(tablero["terceros_le_quedan_debiendo"]) == libro["debiendo"] == CERO
    assert D(tablero["liquidaciones_por_pagar"]) == libro["por_pagar"]
