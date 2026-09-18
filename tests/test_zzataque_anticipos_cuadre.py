"""ATAQUE 3 A LA CORRECCIÓN DE UNA QUINCENA PAGADA, POR EL LADO DE LOS ANTICIPOS:
QUE EL DIÁLOGO PROMETA UNA CIFRA Y EL BOTÓN ESCRIBA OTRA.

Ya pasó una vez en esta misma pantalla, por el lado de los días: un día repetido en
`recepciones_a_incluir` hacía que la previsualización anunciara VALOR TOTAL $860.000 con
$360.000 por entregar, mientras el botón escribía $680.000 con $180.000 —porque el
recálculo relee de la base y una fila marcada dos veces sigue siendo una fila—. El dueño
aprobaba una cifra y salía a buscar $360.000 en efectivo para un saldo de $180.000.

Los anticipos abren las MISMAS puertas, y además una propia: un anticipo es plata que YA
SE LE ENTREGÓ EN LA MANO al productor, así que una cifra de anticipos inflada por un id
repetido no es un error de cálculo, es cobrarle dos veces la misma entrega.

Cada prueba de aquí manda EL MISMO cuerpo a `/corregir/previsualizar` y a `/corregir`, y
compara CAMPO POR CAMPO: valor_total, anticipos, neto_a_pagar, saldo y estado. Y en cada
una se verifican las dos igualdades que el dueño comprueba con calculadora:

    neto = valor_total - anticipos - saldo_anterior
    neto = pagado + saldo

Y una tercera, la del soporte de la corrección: el DESGLOSE del renglón
(`anticipos_cambiados`) tiene que sumar EXACTO la diferencia de la cifra grande:

    sum(entró) - sum(salió) + sum(valor_después - valor_antes) == anticipos_despues - anticipos_antes

TODAS LAS CIFRAS ESTÁN CALCULADAS A MANO en el docstring de cada prueba, nunca con el
mismo código que se está probando.
"""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
V = "/api/v1"


def D(v):
    return Decimal(str(v))


CERO = D(0)


# --------------------------------------------------------------------- montaje
def _montar(client, h):
    """LA QUINCENA DE PARTIDA, con cifras redondas para poder sumarlas de cabeza.

    Productor a $2.000 el litro.
      · 02/06  250 L x $2.000 ............ $500.000   -> VALOR TOTAL  $500.000
      · Anticipo A1 del 03/06 ........... -$100.000   -> ANTICIPOS    $100.000
                                                         NETO         $400.000
    Se aprueba y se paga completa: pagado $400.000, saldo $0, estado 'pagada'.

    Y DESPUÉS DE PAGADA aparecen las dos cosas que el dueño olvidó anotar:
      · A2, anticipo del 10/06 por $150.000 (dentro del período, queda SUELTO)
      · el día del 12/06, 90 L x $2.000 = $180.000 (queda SUELTO)
    """
    prov = client.post(
        f"{V}/proveedores",
        json={"nombre": "Libardo", "vereda": "El Roble", "precio_litro": "2000"},
        headers=h,
    ).json()
    client.post(
        f"{V}/recepciones",
        json={"fecha": "2026-06-02", "proveedor_id": prov["id"], "cantidad_litros": "250"},
        headers=h,
    )
    a1 = client.post(
        f"{V}/anticipos",
        json={
            "tipo": "proveedor",
            "proveedor_id": prov["id"],
            "fecha": "2026-06-03",
            "valor": "100000",
            "observaciones": "para la droga",
        },
        headers=h,
    ).json()
    generadas = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"},
        headers=h,
    ).json()["generadas"]
    liq = next(x for x in generadas if x["proveedor_id"] == prov["id"])
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h).json()

    # La quincena de partida, verificada a mano antes de atacarla.
    assert D(pagada["valor_total"]) == D("500000.00"), pagada["valor_total"]
    assert D(pagada["anticipos"]) == D("100000.00"), pagada["anticipos"]
    assert D(pagada["neto_a_pagar"]) == D("400000.00"), pagada["neto_a_pagar"]
    assert D(pagada["pagado"]) == D("400000.00"), pagada["pagado"]
    assert D(pagada["saldo"]) == CERO, pagada["saldo"]
    assert pagada["estado"] == "pagada", pagada["estado"]
    return prov, liq, a1


def _a2_suelto(client, h, prov):
    """El adelanto del 10/06 por $150.000, registrado DESPUÉS de pagar la quincena."""
    r = client.post(
        f"{V}/anticipos",
        json={
            "tipo": "proveedor",
            "proveedor_id": prov["id"],
            "fecha": "2026-06-10",
            "valor": "150000",
            "observaciones": "el del mercado",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _dia_suelto(client, h, prov):
    """El día del 12/06: 90 L x $2.000 = $180.000, anotado DESPUÉS de pagar."""
    r = client.post(
        f"{V}/recepciones",
        json={"fecha": "2026-06-12", "proveedor_id": prov["id"], "cantidad_litros": "90"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _previsualizar(client, h, liq_id, cuerpo):
    return client.post(f"{API}/{liq_id}/corregir/previsualizar", json=cuerpo, headers=h)


def _corregir(client, h, liq_id, cuerpo):
    return client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)


def _correccion(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/correcciones", headers=h)
    assert r.status_code == 200, r.text
    filas = r.json()
    assert filas, "la corrección no dejó renglón"
    return filas[-1]


# --------------------------------------------------- las igualdades de la casa
def _cuadra_la_liquidacion(liq, donde=""):
    """LAS DOS RESTAS QUE EL DUEÑO HACE CON CALCULADORA SOBRE EL PAPEL.

    neto = valor_total - anticipos - saldo_anterior   (los renglones del comprobante)
    neto = pagado + saldo                             (lo entregado + lo que falta)
    """
    neto = D(liq["neto_a_pagar"])
    assert neto == D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq["saldo_anterior"]), (
        f"{donde}: neto {neto} != total {liq['valor_total']} - anticipos "
        f"{liq['anticipos']} - saldo_anterior {liq['saldo_anterior']}"
    )
    assert neto == D(liq["pagado"]) + D(liq["saldo"]), (
        f"{donde}: neto {neto} != pagado {liq['pagado']} + saldo {liq['saldo']}"
    )


def _el_dialogo_no_mintio(prev, liq, donde=""):
    """CAMPO POR CAMPO: lo que el diálogo prometió contra lo que quedó escrito.

    Es la comparación entera de la pantalla: si una sola de estas cinco cifras se
    despega, el dueño aprobó una cosa y el sistema escribió otra.
    """
    assert D(prev["valor_total_despues"]) == D(liq["valor_total"]), (
        f"{donde}: el diálogo prometió VALOR TOTAL {prev['valor_total_despues']} y quedó "
        f"{liq['valor_total']}"
    )
    assert D(prev["anticipos_despues"]) == D(liq["anticipos"]), (
        f"{donde}: el diálogo prometió ANTICIPOS {prev['anticipos_despues']} y quedaron "
        f"{liq['anticipos']}"
    )
    assert D(prev["neto_despues"]) == D(liq["neto_a_pagar"]), (
        f"{donde}: el diálogo prometió NETO {prev['neto_despues']} y quedó "
        f"{liq['neto_a_pagar']}"
    )
    assert D(prev["saldo_despues"]) == D(liq["saldo"]), (
        f"{donde}: el diálogo prometió SALDO {prev['saldo_despues']} y quedó {liq['saldo']}"
    )
    assert prev["estado_despues"] == liq["estado"], (
        f"{donde}: el diálogo prometió estado '{prev['estado_despues']}' y quedó "
        f"'{liq['estado']}'"
    )


def _desglose_de_anticipos(corr):
    """LO QUE DICE EL DESGLOSE, sumado como lo sumaría el dueño renglón por renglón.

    entró suma, salió resta, y un valor corregido mueve solo la DIFERENCIA.
    """
    total = CERO
    for cambio in corr["anticipos_cambiados"] or []:
        if cambio["accion"] == "entro":
            total += D(cambio["valor"])
        elif cambio["accion"] == "salio":
            total -= D(cambio["valor"])
        elif cambio["accion"] == "valor":
            total += D(cambio["valor"]) - D(cambio["valor_antes"])
        else:
            raise AssertionError(f"acción desconocida en el desglose: {cambio}")
    return total


def _cuadra_el_desglose(corr, donde=""):
    """LA REGLA DE LA CASA DENTRO DEL SOPORTE: el desglose suma la cifra grande."""
    esperado = D(corr["anticipos_despues"]) - D(corr["anticipos_antes"])
    obtenido = _desglose_de_anticipos(corr)
    assert obtenido == esperado, (
        f"{donde}: el desglose de anticipos suma {obtenido} pero la cifra grande se movió "
        f"{esperado} ({corr['anticipos_antes']} -> {corr['anticipos_despues']}). "
        f"Renglones: {corr['anticipos_cambiados']}"
    )


# ============================================================================
# 1. EL MISMO ANTICIPO REPETIDO EN `anticipos_a_incluir`
# ============================================================================
def test_un_anticipo_repetido_en_incluir_no_descuenta_trescientos_mil_en_vez_de_ciento_cincuenta(
    client, base_datos
):
    """A2 mandado TRES veces no puede descontar $450.000 por $150.000 entregados.

    Un doble clic, un reintento del navegador o una lista que se duplica al reabrir el
    diálogo mandan el mismo id varias veces. Si la cuenta los sumara uno por uno:

        ANTICIPOS = 100.000 + 150.000 x 3 = $550.000  (MENTIRA: se le entregaron 250.000)

    Lo correcto, con A1 y A2 una sola vez cada uno:
        VALOR TOTAL ....... $500.000   (no entró ningún día)
        ANTICIPOS ......... $250.000   (100.000 + 150.000)
        NETO .............. $250.000
        PAGADO ............ $400.000   (no se toca)
        SALDO ............. -$150.000  (se le pagó de más)
        ESTADO ............ 'pagada'
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)
    a2 = _a2_suelto(client, h, prov)

    cuerpo = {
        "motivo": "faltaba el adelanto del 10",
        "anticipos_a_incluir": [a2["id"], a2["id"], a2["id"]],
    }
    prev = _previsualizar(client, h, liq["id"], cuerpo)
    assert prev.status_code == 200, prev.text
    p = prev.json()
    print("\n=== DIÁLOGO (id repetido x3) ===", "anticipos", p["anticipos_antes"], "->",
          p["anticipos_despues"], "| neto", p["neto_antes"], "->", p["neto_despues"],
          "| saldo", p["saldo_antes"], "->", p["saldo_despues"], "|", p["estado_despues"])
    assert D(p["anticipos_despues"]) == D("250000.00"), (
        f"el diálogo contó el mismo adelanto varias veces: {p['anticipos_despues']}"
    )

    hecho = _corregir(client, h, liq["id"], cuerpo)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== ESCRITO ===", "total", d["valor_total"], "anticipos", d["anticipos"],
          "neto", d["neto_a_pagar"], "pagado", d["pagado"], "saldo", d["saldo"], d["estado"])

    _el_dialogo_no_mintio(p, d, "anticipo repetido en incluir")
    _cuadra_la_liquidacion(d, "anticipo repetido en incluir")
    assert D(d["valor_total"]) == D("500000.00")
    assert D(d["anticipos"]) == D("250000.00")
    assert D(d["neto_a_pagar"]) == D("250000.00")
    assert D(d["saldo"]) == D("-150000.00")
    assert d["estado"] == "pagada"

    corr = _correccion(client, h, liq["id"])
    print("=== DESGLOSE ===", corr["anticipos_antes"], "->", corr["anticipos_despues"],
          corr["anticipos_cambiados"])
    _cuadra_el_desglose(corr, "anticipo repetido en incluir")
    # Y el desglose nombra el adelanto UNA sola vez: tres renglones iguales le harían
    # creer al dueño que se le entregaron tres adelantos de $150.000.
    entradas = [c for c in corr["anticipos_cambiados"] if c["accion"] == "entro"]
    assert len(entradas) == 1, f"el desglose repite el mismo adelanto: {entradas}"


# ============================================================================
# 2. EL MISMO ANTICIPO REPETIDO EN `anticipos_a_soltar`
# ============================================================================
def test_un_anticipo_repetido_en_soltar_no_le_devuelve_doscientos_mil_por_cien_mil(
    client, base_datos
):
    """A1 mandado DOS veces a soltar devuelve $100.000, no $200.000.

    Soltar A1 quita el descuento de $100.000: esa plata hay que entregársela.
        VALOR TOTAL ....... $500.000
        ANTICIPOS ......... $0
        NETO .............. $500.000
        PAGADO ............ $400.000
        SALDO ............. $100.000   -> estado 'parcial'

    Si el id repetido restara dos veces, la cifra de anticipos se iría a -$100.000 y el
    neto a $600.000: $200.000 de efectivo por una sola entrega de $100.000.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)

    cuerpo = {
        "motivo": "ese adelanto no era de esta quincena",
        "anticipos_a_soltar": [a1["id"], a1["id"]],
    }
    prev = _previsualizar(client, h, liq["id"], cuerpo)
    assert prev.status_code == 200, prev.text
    p = prev.json()
    print("\n=== DIÁLOGO (soltar repetido x2) ===", "anticipos", p["anticipos_antes"], "->",
          p["anticipos_despues"], "| neto", p["neto_despues"], "| saldo", p["saldo_despues"],
          "|", p["estado_despues"])
    assert D(p["anticipos_despues"]) == CERO, p["anticipos_despues"]

    hecho = _corregir(client, h, liq["id"], cuerpo)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== ESCRITO ===", "total", d["valor_total"], "anticipos", d["anticipos"],
          "neto", d["neto_a_pagar"], "pagado", d["pagado"], "saldo", d["saldo"], d["estado"])

    _el_dialogo_no_mintio(p, d, "anticipo repetido en soltar")
    _cuadra_la_liquidacion(d, "anticipo repetido en soltar")
    assert D(d["anticipos"]) == CERO
    assert D(d["neto_a_pagar"]) == D("500000.00")
    assert D(d["saldo"]) == D("100000.00")
    assert d["estado"] == "parcial"

    corr = _correccion(client, h, liq["id"])
    print("=== DESGLOSE ===", corr["anticipos_antes"], "->", corr["anticipos_despues"],
          corr["anticipos_cambiados"])
    _cuadra_el_desglose(corr, "anticipo repetido en soltar")
    salidas = [c for c in corr["anticipos_cambiados"] if c["accion"] == "salio"]
    assert len(salidas) == 1, f"el desglose repite la salida: {salidas}"

    # EL QUE SALE NO SE BORRA, SE SUELTA: esa plata se entregó y la quincena SIGUIENTE
    # se la descuenta. Una sola vez.
    ant = client.get(f"{V}/anticipos/{a1['id']}", headers=h)
    assert ant.status_code == 200, ant.text
    assert ant.json().get("liquidacion_id") is None
    assert D(ant.json()["valor"]) == D("100000.00")


# ============================================================================
# 3. EL MISMO ANTICIPO EN LAS DOS LISTAS
# ============================================================================
def test_el_mismo_anticipo_en_las_dos_listas_rebota_y_no_escribe_un_peso(client, base_datos):
    """Incluir y soltar el mismo adelanto a la vez: 422, y la quincena intacta.

    Sin este guardia el resultado dependería del orden en que se aplicaran las dos
    listas —$250.000 si entra después de salir, $100.000 al revés— y el dueño vería una
    cifra distinta según de dónde saliera la cuenta.

    Después del rebote, la quincena tiene que seguir EXACTA: anticipos $100.000,
    neto $400.000, saldo $0, versión 1.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)
    a2 = _a2_suelto(client, h, prov)

    for etiqueta, ids in (("el aplicado A1", a1["id"]), ("el suelto A2", a2["id"])):
        cuerpo = {
            "motivo": "a ver que pasa",
            "anticipos_a_incluir": [ids],
            "anticipos_a_soltar": [ids],
        }
        prev = _previsualizar(client, h, liq["id"], cuerpo)
        hecho = _corregir(client, h, liq["id"], cuerpo)
        print(f"\n=== CRUZADO ({etiqueta}) ===", "previsualizar", prev.status_code,
              "| corregir", hecho.status_code, "|", hecho.text[:160])
        # Es un error de NEGOCIO, no un 404: el anticipo existe, lo que no existe es la
        # operación de meterlo y sacarlo a la vez.
        assert prev.status_code == 422, prev.text
        assert hecho.status_code == 422, hecho.text

    quedo = client.get(f"{API}/{liq['id']}", headers=h).json()
    print("=== DESPUÉS DEL REBOTE ===", "anticipos", quedo["anticipos"], "neto",
          quedo["neto_a_pagar"], "saldo", quedo["saldo"], "versión", quedo["version"])
    _cuadra_la_liquidacion(quedo, "después del rebote")
    assert D(quedo["anticipos"]) == D("100000.00")
    assert D(quedo["neto_a_pagar"]) == D("400000.00")
    assert D(quedo["saldo"]) == CERO
    assert quedo["version"] == 1, "un rebote subió la versión del comprobante"
    assert client.get(f"{API}/{liq['id']}/correcciones", headers=h).json() == []


# ============================================================================
# 4. DOS VALORES DISTINTOS PARA EL MISMO ANTICIPO
# ============================================================================
def test_dos_valores_distintos_para_el_mismo_anticipo_escriben_lo_que_prometio_el_dialogo(
    client, base_datos
):
    """`valores_de_anticipos` con A1 a $80.000 Y a $70.000 en la misma lista.

    Es lo que manda una pantalla que no deduplica: el dueño tecleó, se arrepintió y
    volvió a teclear. Lo que NO puede pasar es que el diálogo resuelva el empate de una
    forma y el botón de otra —$80.000 arriba, $70.000 abajo—, porque son $10.000 de
    diferencia sobre plata ya entregada.

    Con el último valor mandando ($70.000):
        VALOR TOTAL ....... $500.000
        ANTICIPOS ......... $70.000
        NETO .............. $430.000
        PAGADO ............ $400.000
        SALDO ............. $30.000    -> 'parcial'
    Y el desglose: un solo renglón 'valor' de $100.000 a $70.000, diferencia -$30.000,
    que es exactamente lo que se movió la cifra grande (100.000 -> 70.000).
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)

    cuerpo = {
        "motivo": "el adelanto estaba mal digitado",
        "valores_de_anticipos": [
            {"anticipo_id": a1["id"], "valor": "80000"},
            {"anticipo_id": a1["id"], "valor": "70000"},
        ],
    }
    prev = _previsualizar(client, h, liq["id"], cuerpo)
    assert prev.status_code == 200, prev.text
    p = prev.json()
    print("\n=== DIÁLOGO (dos valores para el mismo) ===", "anticipos",
          p["anticipos_antes"], "->", p["anticipos_despues"], "| neto", p["neto_despues"],
          "| saldo", p["saldo_despues"], "|", p["estado_despues"])

    hecho = _corregir(client, h, liq["id"], cuerpo)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== ESCRITO ===", "total", d["valor_total"], "anticipos", d["anticipos"],
          "neto", d["neto_a_pagar"], "pagado", d["pagado"], "saldo", d["saldo"], d["estado"])

    # LO QUE MANDA: que las dos cuentas den lo mismo. Cuál de los dos valores gana es
    # una decisión; que el diálogo y el botón discrepen es un defecto.
    _el_dialogo_no_mintio(p, d, "dos valores para el mismo anticipo")
    _cuadra_la_liquidacion(d, "dos valores para el mismo anticipo")

    # Y el anticipo en la base quedó con la MISMA cifra que se le restó al comprobante:
    # si no, el papel dice una cosa y la pantalla de anticipos otra.
    ant = client.get(f"{V}/anticipos/{a1['id']}", headers=h).json()
    print("=== EL ANTICIPO EN LA BASE ===", ant["valor"], "liquidacion_id",
          ant.get("liquidacion_id"))
    assert D(ant["valor"]) == D(d["anticipos"]), (
        f"el comprobante descuenta {d['anticipos']} y el anticipo dice {ant['valor']}"
    )
    assert D(d["anticipos"]) == D("70000.00"), "no ganó el último valor mandado"
    assert D(d["neto_a_pagar"]) == D("430000.00")
    assert D(d["saldo"]) == D("30000.00")
    assert d["estado"] == "parcial"

    corr = _correccion(client, h, liq["id"])
    print("=== DESGLOSE ===", corr["anticipos_antes"], "->", corr["anticipos_despues"],
          corr["anticipos_cambiados"])
    _cuadra_el_desglose(corr, "dos valores para el mismo anticipo")
    cambios = [c for c in corr["anticipos_cambiados"] if c["accion"] == "valor"]
    assert len(cambios) == 1, f"el desglose trae el mismo anticipo dos veces: {cambios}"


# ============================================================================
# 5. EL CASO REAL: ANTICIPOS + DÍAS + PRECIOS EN LA MISMA CORRECCIÓN
# ============================================================================
def test_anticipos_dias_y_precios_juntos_cuadran_campo_por_campo(client, base_datos):
    """Se le olvidó un día, se le olvidó un adelanto Y tecleó mal un precio.

    Es el caso real, y es el que más puede despegarse: los días y los precios pasan por
    el recálculo desde recepciones, y los anticipos se escriben DESPUÉS y a mano. Si las
    dos mitades no se encuentran, la cifra grande queda entre las dos.

    Las cuentas, a mano:
      · el día del 02/06 se liquidó a $2.000 y era $1.800: 250 L x $1.800 = $450.000
      · entra el día del 12/06: 90 L x $2.000 = $180.000
        VALOR TOTAL = 450.000 + 180.000 .......... $630.000
      · entra A2: ANTICIPOS = 100.000 + 150.000 .. $250.000
        NETO = 630.000 - 250.000 - 0 ............. $380.000
        PAGADO ................................... $400.000
        SALDO = 380.000 - 400.000 ................ -$20.000   -> 'pagada'
        LE QUEDA DEBIENDO ........................  $20.000
      Y el desglose de anticipos: entró $150.000 = 250.000 - 100.000.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)
    a2 = _a2_suelto(client, h, prov)
    _dia_suelto(client, h, prov)

    mirar = _previsualizar(client, h, liq["id"], {"motivo": "mirar"}).json()
    dia = next(x for x in mirar["dias_sueltos"] if x["fecha"] == "2026-06-12")
    assert D(dia["valor"]) == D("180000.00"), dia

    detalle = next(
        x for x in client.get(f"{API}/{liq['id']}", headers=h).json()["detalles"]
        if x["fecha"] == "2026-06-02"
    )

    cuerpo = {
        "motivo": "faltaba el dia 12, el adelanto del 10 y el precio del 2 estaba mal",
        "recepciones_a_incluir": [dia["recepcion_id"]],
        "precios": [{"detalle_id": detalle["id"], "precio_litro": 1800}],
        "anticipos_a_incluir": [a2["id"]],
    }
    prev = _previsualizar(client, h, liq["id"], cuerpo)
    assert prev.status_code == 200, prev.text
    p = prev.json()
    print("\n=== DIÁLOGO (todo junto) ===", "total", p["valor_total_antes"], "->",
          p["valor_total_despues"], "| anticipos", p["anticipos_antes"], "->",
          p["anticipos_despues"], "| neto", p["neto_antes"], "->", p["neto_despues"],
          "| saldo", p["saldo_antes"], "->", p["saldo_despues"], "|", p["estado_despues"])

    hecho = _corregir(client, h, liq["id"], cuerpo)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== ESCRITO ===", "total", d["valor_total"], "anticipos", d["anticipos"],
          "neto", d["neto_a_pagar"], "pagado", d["pagado"], "saldo", d["saldo"],
          "debiendo", d["le_queda_debiendo"], d["estado"])

    _el_dialogo_no_mintio(p, d, "todo junto")
    _cuadra_la_liquidacion(d, "todo junto")
    assert D(d["valor_total"]) == D("630000.00")
    assert D(d["anticipos"]) == D("250000.00")
    assert D(d["neto_a_pagar"]) == D("380000.00")
    assert D(d["pagado"]) == D("400000.00")
    assert D(d["saldo"]) == D("-20000.00")
    assert D(d["le_queda_debiendo"]) == D("20000.00")
    assert d["estado"] == "pagada"

    # LA COLUMNA VALOR SUMA EL VALOR TOTAL: es la primera suma que hace el dueño.
    suma = sum((D(x["valor"]) for x in d["detalles"]), CERO)
    print("=== columna Valor ===", suma, "vs VALOR TOTAL", d["valor_total"])
    assert suma == D(d["valor_total"])

    corr = _correccion(client, h, liq["id"])
    print("=== DESGLOSE ===", corr["anticipos_antes"], "->", corr["anticipos_despues"],
          corr["anticipos_cambiados"])
    _cuadra_el_desglose(corr, "todo junto")
    assert D(corr["anticipos_antes"]) == D("100000.00")
    assert D(corr["anticipos_despues"]) == D("250000.00")


# ============================================================================
# 6. ENTRAR Y CORREGIRLE EL VALOR AL MISMO ANTICIPO
# ============================================================================
def test_un_anticipo_que_entra_con_el_valor_corregido_deja_el_desglose_cuadrado(
    client, base_datos
):
    """A2 entra Y se le corrige el valor de $150.000 a $90.000, en una sola corrección.

    Es lo que hace el dueño cuando se acuerda del adelanto y de que lo anotó mal: lo
    marca y le arregla la cifra sin salir del diálogo.

        ANTICIPOS = 100.000 (A1) + 90.000 (A2 corregido) ..... $190.000
        VALOR TOTAL .......................................... $500.000
        NETO = 500.000 - 190.000 ............................. $310.000
        PAGADO ............................................... $400.000
        SALDO = 310.000 - 400.000 ............................ -$90.000  -> 'pagada'

    LA CIFRA GRANDE SE MOVIÓ $90.000 (100.000 -> 190.000), así que el DESGLOSE tiene que
    sumar $90.000 y ni un peso más. Un renglón 'entró $90.000' MÁS un renglón
    'valor: 150.000 -> 90.000' suman 90.000 + (-60.000) = $30.000: el soporte de la
    corrección contradice la cifra que él mismo respalda, y es justo el papel que el
    dueño lee cuando le pregunta al sistema por qué su quincena cambió.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)
    a2 = _a2_suelto(client, h, prov)

    cuerpo = {
        "motivo": "faltaba el adelanto del 10 y eran 90 mil, no 150",
        "anticipos_a_incluir": [a2["id"]],
        "valores_de_anticipos": [{"anticipo_id": a2["id"], "valor": "90000"}],
    }
    prev = _previsualizar(client, h, liq["id"], cuerpo)
    assert prev.status_code == 200, prev.text
    p = prev.json()
    print("\n=== DIÁLOGO (entra y se corrige) ===", "anticipos", p["anticipos_antes"],
          "->", p["anticipos_despues"], "| neto", p["neto_despues"], "| saldo",
          p["saldo_despues"], "|", p["estado_despues"])

    hecho = _corregir(client, h, liq["id"], cuerpo)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== ESCRITO ===", "total", d["valor_total"], "anticipos", d["anticipos"],
          "neto", d["neto_a_pagar"], "pagado", d["pagado"], "saldo", d["saldo"], d["estado"])

    # La cifra grande sí llega bien: el diálogo y el botón dicen lo mismo.
    _el_dialogo_no_mintio(p, d, "entra y se le corrige el valor")
    _cuadra_la_liquidacion(d, "entra y se le corrige el valor")
    assert D(d["anticipos"]) == D("190000.00")
    assert D(d["neto_a_pagar"]) == D("310000.00")
    assert D(d["saldo"]) == D("-90000.00")

    corr = _correccion(client, h, liq["id"])
    print("=== DESGLOSE ===", corr["anticipos_antes"], "->", corr["anticipos_despues"],
          corr["anticipos_cambiados"])
    print("=== el desglose suma ===", _desglose_de_anticipos(corr),
          "y la cifra grande se movió",
          D(corr["anticipos_despues"]) - D(corr["anticipos_antes"]))
    _cuadra_el_desglose(corr, "entra y se le corrige el valor")


# ============================================================================
# 7. CORREGIRLE EL VALOR A UN ANTICIPO SUELTO QUE NO SE ESTÁ INCLUYENDO
# ============================================================================
def test_corregirle_el_valor_a_un_suelto_sin_incluirlo_no_puede_reventar_despues_del_dialogo(
    client, base_datos
):
    """`valores_de_anticipos` con A2 (suelto) y `anticipos_a_incluir` VACÍO.

    Es un clic de más en la pantalla: el dueño le arregla la cifra al adelanto del 10 y
    después se arrepiente de marcarlo (o la pantalla manda el valor tecleado aunque la
    casilla esté sin marcar).

    La previsualización lo acepta y anuncia ANTICIPOS $100.000 —A2 no entra, así que la
    cifra no se mueve—. Con esa promesa en pantalla, el botón tiene que terminar en algo
    que el dueño pueda entender: o hace exactamente lo que el diálogo dijo, o lo rebota
    con un 422 explicando que hay que marcar el adelanto. Lo que NO puede es reventar en
    500: ahí el dueño no sabe si quedó escrito o no sobre una quincena ya pagada.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)
    a2 = _a2_suelto(client, h, prov)

    cuerpo = {
        "motivo": "eran 90 mil, no 150",
        "valores_de_anticipos": [{"anticipo_id": a2["id"], "valor": "90000"}],
    }
    prev = _previsualizar(client, h, liq["id"], cuerpo)
    print("\n=== DIÁLOGO (valor de un suelto sin incluirlo) ===", prev.status_code,
          prev.text[:200])

    if prev.status_code != 200:
        # Si el diálogo ya lo rebota, el botón tiene que rebotarlo igual: no hay promesa
        # que romper.
        hecho = _corregir(client, h, liq["id"], cuerpo)
        print("=== BOTÓN ===", hecho.status_code, hecho.text[:200])
        assert hecho.status_code == prev.status_code, (
            f"el diálogo respondió {prev.status_code} y el botón {hecho.status_code}"
        )
        return

    p = prev.json()
    print("=== el diálogo promete ===", "anticipos", p["anticipos_antes"], "->",
          p["anticipos_despues"], "| neto", p["neto_despues"], "| saldo", p["saldo_despues"])

    hecho = _corregir(client, h, liq["id"], cuerpo)
    print("=== BOTÓN ===", hecho.status_code, hecho.text[:300])
    assert hecho.status_code != 500, (
        "el diálogo prometió ANTICIPOS "
        f"{p['anticipos_despues']} y el botón reventó con 500 sobre una quincena ya "
        f"pagada: {hecho.text[:300]}"
    )
    if hecho.status_code == 422:
        # Rebote explicado: la quincena tiene que haber quedado intacta.
        quedo = client.get(f"{API}/{liq['id']}", headers=h).json()
        _cuadra_la_liquidacion(quedo, "tras el rebote del suelto")
        assert D(quedo["anticipos"]) == D("100000.00")
        assert quedo["version"] == 1
        return

    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== ESCRITO ===", "anticipos", d["anticipos"], "neto", d["neto_a_pagar"],
          "saldo", d["saldo"], d["estado"])
    _el_dialogo_no_mintio(p, d, "valor de un suelto sin incluirlo")
    _cuadra_la_liquidacion(d, "valor de un suelto sin incluirlo")
    _cuadra_el_desglose(_correccion(client, h, liq["id"]), "valor de un suelto sin incluirlo")


# ============================================================================
# 8. TODA LA MATRIZ EN UNA SOLA CORRECCIÓN: entra uno, sale otro, día y precio
# ============================================================================
def test_entra_uno_sale_otro_con_dia_y_precio_y_el_desglose_sigue_cuadrando(
    client, base_datos
):
    """Las CUATRO cosas a la vez, que es donde las dos mitades del cálculo se separan.

    Los días y el precio pasan por el recálculo desde recepciones; los anticipos se
    escriben DESPUÉS y a mano. Con uno entrando Y otro saliendo en la misma petición, un
    orden mal puesto deja la cifra grande entre las dos mitades.

    Las cuentas, a mano:
      · el 02/06 pasa de $2.000 a $1.800: 250 L x $1.800 = $450.000
      · entra el 12/06: 90 L x $2.000 = $180.000
        VALOR TOTAL = 450.000 + 180.000 .............. $630.000
      · sale A1 ($100.000) y entra A2 corregido a $90.000
        ANTICIPOS = 90.000 ........................... $90.000
        NETO = 630.000 - 90.000 - 0 .................. $540.000
        PAGADO ....................................... $400.000
        SALDO = 540.000 - 400.000 .................... $140.000  -> 'parcial'
      · DESGLOSE: salió 100.000, entró 90.000 => -$10.000
        y la cifra grande se movió 90.000 - 100.000 = -$10.000. Tiene que dar igual.
      · Y A1 queda SUELTO: esa plata se entregó y la quincena siguiente se la descuenta.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)
    a2 = _a2_suelto(client, h, prov)
    _dia_suelto(client, h, prov)

    mirar = _previsualizar(client, h, liq["id"], {"motivo": "mirar"}).json()
    dia = next(x for x in mirar["dias_sueltos"] if x["fecha"] == "2026-06-12")
    detalle = next(
        x for x in client.get(f"{API}/{liq['id']}", headers=h).json()["detalles"]
        if x["fecha"] == "2026-06-02"
    )

    cuerpo = {
        "motivo": "el adelanto del 3 no era de aqui, el del 10 si y eran 90 mil",
        "recepciones_a_incluir": [dia["recepcion_id"]],
        "precios": [{"detalle_id": detalle["id"], "precio_litro": 1800}],
        "anticipos_a_incluir": [a2["id"]],
        "anticipos_a_soltar": [a1["id"]],
        "valores_de_anticipos": [{"anticipo_id": a2["id"], "valor": "90000"}],
    }
    prev = _previsualizar(client, h, liq["id"], cuerpo)
    assert prev.status_code == 200, prev.text
    p = prev.json()
    print("\n=== DIÁLOGO (la matriz entera) ===", "total", p["valor_total_despues"],
          "| anticipos", p["anticipos_antes"], "->", p["anticipos_despues"],
          "| neto", p["neto_despues"], "| saldo", p["saldo_despues"], "|",
          p["estado_despues"])

    hecho = _corregir(client, h, liq["id"], cuerpo)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== ESCRITO ===", "total", d["valor_total"], "anticipos", d["anticipos"],
          "neto", d["neto_a_pagar"], "pagado", d["pagado"], "saldo", d["saldo"], d["estado"])

    _el_dialogo_no_mintio(p, d, "la matriz entera")
    _cuadra_la_liquidacion(d, "la matriz entera")
    assert D(d["valor_total"]) == D("630000.00")
    assert D(d["anticipos"]) == D("90000.00")
    assert D(d["neto_a_pagar"]) == D("540000.00")
    assert D(d["saldo"]) == D("140000.00")
    assert d["estado"] == "parcial"
    assert sum((D(x["valor"]) for x in d["detalles"]), CERO) == D(d["valor_total"])

    corr = _correccion(client, h, liq["id"])
    print("=== DESGLOSE ===", corr["anticipos_antes"], "->", corr["anticipos_despues"],
          corr["anticipos_cambiados"])
    _cuadra_el_desglose(corr, "la matriz entera")

    # A1 quedó suelto y con su cifra intacta; A2 quedó marcado y con la cifra corregida.
    uno = client.get(f"{V}/anticipos/{a1['id']}", headers=h).json()
    dos = client.get(f"{V}/anticipos/{a2['id']}", headers=h).json()
    print("=== A1 ===", uno["valor"], uno.get("liquidacion_id"))
    print("=== A2 ===", dos["valor"], dos.get("liquidacion_id"))
    assert uno.get("liquidacion_id") is None and D(uno["valor"]) == D("100000.00")
    assert dos.get("liquidacion_id") == liq["id"] and D(dos["valor"]) == D("90000.00")


# ============================================================================
# 9. EL DOBLE CLIC EN EL BOTÓN: la misma corrección mandada dos veces
# ============================================================================
def test_la_misma_correccion_mandada_dos_veces_no_descuenta_el_adelanto_dos_veces(
    client, base_datos
):
    """Mandar la MISMA petición dos veces (el botón oprimido dos veces, o un reintento).

    La primera deja ANTICIPOS en $250.000. La segunda no puede dejarlos en $400.000: eso
    sería cobrarle al productor dos veces el mismo adelanto de $150.000 que se le entregó
    una sola vez.

    Y como el diálogo ya no lo dejaría pasar (el adelanto dejó de estar suelto), el botón
    tiene que rebotar igual: 422, sin subir la versión y sin dejar un segundo renglón de
    corrección.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)
    a2 = _a2_suelto(client, h, prov)

    cuerpo = {"motivo": "faltaba el adelanto del 10", "anticipos_a_incluir": [a2["id"]]}
    primera = _corregir(client, h, liq["id"], cuerpo)
    assert primera.status_code == 200, primera.text
    uno = primera.json()
    print("\n=== PRIMERA ===", "anticipos", uno["anticipos"], "neto", uno["neto_a_pagar"],
          "saldo", uno["saldo"], "versión", uno["version"])
    assert D(uno["anticipos"]) == D("250000.00")
    assert uno["version"] == 2

    prev = _previsualizar(client, h, liq["id"], cuerpo)
    segunda = _corregir(client, h, liq["id"], cuerpo)
    print("=== SEGUNDA ===", "previsualizar", prev.status_code, "| corregir",
          segunda.status_code, "|", segunda.text[:180])
    assert prev.status_code == segunda.status_code, (
        f"el diálogo dice {prev.status_code} y el botón {segunda.status_code}"
    )
    assert segunda.status_code == 422, segunda.text

    quedo = client.get(f"{API}/{liq['id']}", headers=h).json()
    print("=== DESPUÉS ===", "anticipos", quedo["anticipos"], "neto", quedo["neto_a_pagar"],
          "saldo", quedo["saldo"], "versión", quedo["version"])
    _cuadra_la_liquidacion(quedo, "tras el doble clic")
    assert D(quedo["anticipos"]) == D("250000.00"), "el adelanto se descontó dos veces"
    assert quedo["version"] == 2, "el rebote subió la versión del comprobante"
    assert len(client.get(f"{API}/{liq['id']}/correcciones", headers=h).json()) == 1


# ============================================================================
# 10. EL CERO DE MÁS: un anticipo corregido a una cifra mayor que la quincena
# ============================================================================
def test_un_cero_de_mas_en_el_valor_del_anticipo_no_separa_el_dialogo_del_boton(
    client, base_datos
):
    """A1 corregido de $100.000 a $1.000.000 en una quincena de $500.000.

    Es el error de dedo clásico (un cero de más) y deja la resta en negativo:
        VALOR TOTAL ....... $500.000
        ANTICIPOS ......... $1.000.000
        NETO = 500.000 - 1.000.000 ...... -$500.000
        PAGADO ............ $400.000
        SALDO = -500.000 - 400.000 ...... -$900.000  (le queda debiendo $900.000)

    Lo que se exige aquí no es que el sistema lo permita o lo rechace —eso es una
    decisión— sino que el DIÁLOGO Y EL BOTÓN hagan lo mismo. Un neto negativo que la
    pantalla anuncia y el botón rechaza (o al revés) es la clase de diferencia que el
    dueño descubre con el productor al frente.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)

    cuerpo = {
        "motivo": "se le fue un cero",
        "valores_de_anticipos": [{"anticipo_id": a1["id"], "valor": "1000000"}],
    }
    prev = _previsualizar(client, h, liq["id"], cuerpo)
    hecho = _corregir(client, h, liq["id"], cuerpo)
    print("\n=== CERO DE MÁS ===", "previsualizar", prev.status_code, "| corregir",
          hecho.status_code)
    assert prev.status_code == hecho.status_code, (
        f"el diálogo respondió {prev.status_code} y el botón {hecho.status_code}: "
        f"{prev.text[:160]} || {hecho.text[:160]}"
    )

    if hecho.status_code != 200:
        quedo = client.get(f"{API}/{liq['id']}", headers=h).json()
        print("=== REBOTADO, la quincena intacta ===", quedo["anticipos"], quedo["version"])
        _cuadra_la_liquidacion(quedo, "tras rebotar el cero de más")
        assert D(quedo["anticipos"]) == D("100000.00")
        assert quedo["version"] == 1
        return

    p, d = prev.json(), hecho.json()
    print("=== DIÁLOGO ===", "anticipos", p["anticipos_despues"], "neto",
          p["neto_despues"], "saldo", p["saldo_despues"], p["estado_despues"])
    print("=== ESCRITO ===", "anticipos", d["anticipos"], "neto", d["neto_a_pagar"],
          "saldo", d["saldo"], "debiendo", d["le_queda_debiendo"], d["estado"])
    _el_dialogo_no_mintio(p, d, "cero de más")
    _cuadra_la_liquidacion(d, "cero de más")
    assert D(d["anticipos"]) == D("1000000.00")
    assert D(d["neto_a_pagar"]) == D("-500000.00")
    assert D(d["saldo"]) == D("-900000.00")
    _cuadra_el_desglose(_correccion(client, h, liq["id"]), "cero de más")


# ============================================================================
# 11. LA TERCERA RESTA: mover anticipos en una quincena que ARRASTRA una deuda
# ============================================================================
def test_mover_anticipos_con_saldo_anterior_no_le_mueve_un_peso_a_la_deuda_arrastrada(
    client, base_datos
):
    """`neto = valor_total - anticipos - saldo_anterior`, con los TRES términos vivos.

    Hasta aquí `saldo_anterior` fue siempre $0, así que la resta tenía dos términos y no
    tres. Este es el caso en que la corrección puede confundirse: la quincena de julio le
    está COBRANDO al productor una deuda que quedó de junio, y mover un anticipo de julio
    no puede tocar esa columna congelada —es plata de OTRO papel, el que el productor ya
    tiene guardado—.

    El montaje, a mano:
      · JUNIO: 250 L x $2.000 = $500.000, anticipo A1 $100.000 -> neto $400.000, pagado
        $400.000. Se corrige metiéndole el adelanto olvidado de $150.000: anticipos
        $250.000, neto $250.000, y como ya se le habían entregado $400.000 queda
        DEBIENDO $150.000.
      · JULIO: 200 L x $2.000 = $400.000, con un anticipo A3 del 02/07 por $50.000.
        Al generarla cobra la deuda de junio: saldo_anterior = $150.000.
        NETO = 400.000 - 50.000 - 150.000 = $200.000. Se paga completa.

    LA CORRECCIÓN: se suelta A3 porque ese adelanto no era de julio.
        ANTICIPOS = $0
        NETO = 400.000 - 0 - 150.000 ......... $250.000
        PAGADO ............................... $200.000
        SALDO = 250.000 - 200.000 ............ $50.000   -> 'parcial'
        Y SALDO ANTERIOR SIGUE EN $150.000: ni un peso.
    """
    h = auth_headers(client, "admin.a")
    prov, junio, a1 = _montar(client, h)
    a2 = _a2_suelto(client, h, prov)

    # Junio se corrige y queda debiendo $150.000.
    jun = _corregir(client, h, junio["id"], {
        "motivo": "faltaba el adelanto del 10", "anticipos_a_incluir": [a2["id"]]}).json()
    print("\n=== JUNIO CORREGIDA ===", "anticipos", jun["anticipos"], "neto",
          jun["neto_a_pagar"], "saldo", jun["saldo"], "debiendo", jun["le_queda_debiendo"])
    assert D(jun["le_queda_debiendo"]) == D("150000.00"), jun["le_queda_debiendo"]

    # Julio: la leche y el adelanto del 02/07.
    client.post(f"{V}/recepciones", json={
        "fecha": "2026-07-05", "proveedor_id": prov["id"], "cantidad_litros": "200"},
        headers=h)
    a3 = client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-07-02",
        "valor": "50000", "observaciones": "el de julio"}, headers=h).json()

    generadas = client.post(f"{API}/generar", json={
        "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15",
        "tipo": "proveedor"}, headers=h).json()["generadas"]
    julio = next(x for x in generadas if x["proveedor_id"] == prov["id"])
    client.post(f"{API}/{julio['id']}/aprobar", headers=h)
    pagada = client.post(f"{API}/{julio['id']}/pagar", headers=h).json()
    print("=== JULIO PAGADA ===", "total", pagada["valor_total"], "anticipos",
          pagada["anticipos"], "saldo_anterior", pagada["saldo_anterior"], "neto",
          pagada["neto_a_pagar"], "pagado", pagada["pagado"], "saldo", pagada["saldo"])
    _cuadra_la_liquidacion(pagada, "julio recién pagada")
    assert D(pagada["valor_total"]) == D("400000.00")
    assert D(pagada["anticipos"]) == D("50000.00")
    assert D(pagada["saldo_anterior"]) == D("150000.00")
    assert D(pagada["neto_a_pagar"]) == D("200000.00")

    cuerpo = {"motivo": "ese adelanto no era de julio", "anticipos_a_soltar": [a3["id"]]}
    prev = _previsualizar(client, h, julio["id"], cuerpo)
    assert prev.status_code == 200, prev.text
    p = prev.json()
    print("=== DIÁLOGO (con deuda arrastrada) ===", "anticipos", p["anticipos_antes"],
          "->", p["anticipos_despues"], "| neto", p["neto_antes"], "->", p["neto_despues"],
          "| saldo", p["saldo_antes"], "->", p["saldo_despues"], "|", p["estado_despues"])

    hecho = _corregir(client, h, julio["id"], cuerpo)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== ESCRITO ===", "total", d["valor_total"], "anticipos", d["anticipos"],
          "saldo_anterior", d["saldo_anterior"], "neto", d["neto_a_pagar"], "pagado",
          d["pagado"], "saldo", d["saldo"], d["estado"])

    _el_dialogo_no_mintio(p, d, "con deuda arrastrada")
    _cuadra_la_liquidacion(d, "con deuda arrastrada")
    # LA COLUMNA CONGELADA NO SE MUEVE: es el renglón que le cobra la deuda de junio, y
    # ese papel ya está en la mano del productor.
    assert D(d["saldo_anterior"]) == D("150000.00"), (
        f"la corrección de un anticipo le movió la deuda arrastrada: {d['saldo_anterior']}"
    )
    assert D(d["anticipos"]) == CERO
    assert D(d["neto_a_pagar"]) == D("250000.00")
    assert D(d["saldo"]) == D("50000.00")
    assert d["estado"] == "parcial"

    corr = _correccion(client, h, julio["id"])
    print("=== DESGLOSE ===", corr["anticipos_antes"], "->", corr["anticipos_despues"],
          corr["anticipos_cambiados"])
    _cuadra_el_desglose(corr, "con deuda arrastrada")

    # Y JUNIO SIGUE EXACTA: la deuda ya viajó, así que julio no puede reescribirla.
    quedo_junio = client.get(f"{API}/{junio['id']}", headers=h).json()
    print("=== JUNIO DESPUÉS ===", "anticipos", quedo_junio["anticipos"], "saldo",
          quedo_junio["saldo"], "debiendo", quedo_junio["le_queda_debiendo"])
    _cuadra_la_liquidacion(quedo_junio, "junio después de corregir julio")
    assert D(quedo_junio["anticipos"]) == D("250000.00")
    assert D(quedo_junio["saldo"]) == D("-150000.00")


# ============================================================================
# 12. UN ADELANTO QUE NO ES DE ESTE PRODUCTOR
# ============================================================================
def test_el_adelanto_de_otro_productor_no_se_le_puede_descontar_a_esta_quincena(
    client, base_datos
):
    """A Pedro se le adelantaron $300.000. Esa plata no puede salir del papel de Libardo.

    Es el error de un clic en una lista mal filtrada, y no deja rastro visible: la
    quincena de Libardo bajaría de NETO $400.000 a $100.000 con un renglón de anticipos
    de $400.000 —y Pedro seguiría debiendo los $300.000 que ya se le entregaron, porque
    su adelanto quedaría marcado contra el papel de otro—.

    Se exige tres cosas: que el diálogo NI SIQUIERA LO OFREZCA, que el botón lo rebote, y
    que rebote IGUAL que el diálogo. Y lo mismo con un id inventado.
    """
    h = auth_headers(client, "admin.a")
    prov, liq, a1 = _montar(client, h)

    pedro = client.post(f"{V}/proveedores", json={
        "nombre": "Pedro", "vereda": "La Cumbre", "precio_litro": "2000"}, headers=h).json()
    ajeno = client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": pedro["id"], "fecha": "2026-06-08",
        "valor": "300000", "observaciones": "para Pedro"}, headers=h).json()

    mirar = _previsualizar(client, h, liq["id"], {"motivo": "mirar"}).json()
    ofrecidos = {a["anticipo_id"] for a in mirar["anticipos_sueltos"]}
    print("\n=== LO QUE OFRECE EL DIÁLOGO ===",
          [(a["fecha"], a["valor"]) for a in mirar["anticipos_sueltos"]])
    assert ajeno["id"] not in ofrecidos, "el diálogo ofrece el adelanto de otro productor"

    inventado = "11111111-2222-3333-4444-555555555555"
    for etiqueta, anticipo_id in (("el de Pedro", ajeno["id"]), ("uno inventado", inventado)):
        cuerpo = {"motivo": "un clic en la lista mal filtrada",
                  "anticipos_a_incluir": [anticipo_id]}
        prev = _previsualizar(client, h, liq["id"], cuerpo)
        hecho = _corregir(client, h, liq["id"], cuerpo)
        print(f"=== AJENO ({etiqueta}) ===", "previsualizar", prev.status_code,
              "| corregir", hecho.status_code, "|", hecho.text[:140])
        assert prev.status_code == hecho.status_code, (
            f"{etiqueta}: el diálogo dice {prev.status_code} y el botón {hecho.status_code}"
        )
        assert hecho.status_code in (404, 422), hecho.text

        # Y lo mismo por la puerta de soltarlo y por la de corregirle el valor.
        for campo, valor in (
            ("anticipos_a_soltar", [anticipo_id]),
            ("valores_de_anticipos", [{"anticipo_id": anticipo_id, "valor": "1000"}]),
        ):
            cuerpo2 = {"motivo": "por la otra puerta", campo: valor}
            p2 = _previsualizar(client, h, liq["id"], cuerpo2)
            h2 = _corregir(client, h, liq["id"], cuerpo2)
            print(f"  · {campo}: previsualizar {p2.status_code} | corregir {h2.status_code}")
            assert p2.status_code == h2.status_code, (
                f"{etiqueta}/{campo}: diálogo {p2.status_code} vs botón {h2.status_code}"
            )
            assert h2.status_code in (404, 422), h2.text

    # LA QUINCENA DE LIBARDO, INTACTA, y el adelanto de Pedro todavía suyo y suelto.
    quedo = client.get(f"{API}/{liq['id']}", headers=h).json()
    print("=== LIBARDO DESPUÉS ===", "anticipos", quedo["anticipos"], "neto",
          quedo["neto_a_pagar"], "versión", quedo["version"])
    _cuadra_la_liquidacion(quedo, "tras los rebotes del ajeno")
    assert D(quedo["anticipos"]) == D("100000.00")
    assert D(quedo["neto_a_pagar"]) == D("400000.00")
    assert quedo["version"] == 1
    assert client.get(f"{API}/{liq['id']}/correcciones", headers=h).json() == []

    de_pedro = client.get(f"{V}/anticipos/{ajeno['id']}", headers=h).json()
    print("=== EL DE PEDRO ===", de_pedro["valor"], "liquidacion_id",
          de_pedro.get("liquidacion_id"))
    assert de_pedro.get("liquidacion_id") is None
    assert D(de_pedro["valor"]) == D("300000.00")
