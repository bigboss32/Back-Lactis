"""La tarifa del transportador con DECIMALES: $242,76 por litro.

En Colombia el separador decimal es la coma. El dueño tiene un transportador a
242,76 por litro y el campo no se lo aceptaba: la pantalla botaba la coma y
"242,76" se convertía en 24.276, o sea CIEN VECES la tarifa. Eso no es un
detalle de formato, es pagarle cien veces más a alguien.

El arreglo grande es del frontend (la directiva appMiles), pero antes de tocarlo
hay que dejar clavado por acá que el backend sí aguanta los decimales y que la
plata sigue cuadrando. Lo que se prueba:

  1. una tarifa de dos decimales se guarda y se vuelve a leer IDÉNTICA, sin
     perder los centavos ni redondear a 242 ni a 243;
  2. el flete de una recepción con esa tarifa da el valor exacto, y la
     liquidación del transportador cuadra: el desglose por día suma el total;
  3. las tarifas de pesos enteros que ya existen dan exactamente lo mismo que
     antes de este cambio;
  4. un valor que no es un número se rechaza con mensaje claro (422), no con un
     500 ni guardando un cero callado.

Hay un cliente real con datos vivos: las cifras que se imprimen acá son las que
el dueño revisa a mano.
"""
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers

TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQUIDACIONES = "/api/v1/liquidaciones"


def D(v):
    return Decimal(str(v))


def centavos(valor):
    """La misma regla del backend: al centavo, con el medio centavo para arriba."""
    return D(valor).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _crear(client, headers, url, payload):
    r = client.post(url, json=payload, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _escenario(client, h, tarifa, precio_litro="1800"):
    """Una ruta, un transportador con la tarifa dada y un proveedor."""
    ruta = _crear(client, h, "/api/v1/rutas", {"nombre": "Ruta Norte", "municipio": "Norte"})
    transportador = _crear(
        client, h, TRANSPORTADORES,
        {"nombre": "Stella", "ruta_id": ruta["id"], "valor_transporte": tarifa},
    )
    proveedor = _crear(
        client, h, "/api/v1/proveedores",
        {"nombre": "Alberto", "vereda": "Norte", "precio_litro": precio_litro, "ruta_id": ruta["id"]},
    )
    return ruta, transportador, proveedor


# ---------------------------------------------------------------------------
# 1. La tarifa con dos decimales sobrevive la ida y la vuelta
# ---------------------------------------------------------------------------
def test_tarifa_con_dos_decimales_no_pierde_los_centavos(client, base_datos):
    """242,76 se guarda 242,76 y se relee 242,76.

    La columna es Numeric(12, 2), o sea que aguanta los dos decimales sin
    migración. Lo que hay que garantizar es que nadie por el camino la redondee:
    ni a 242 (truncar) ni a 243 (redondear), porque las dos cosas le cambian la
    plata al transportador en cada litro que recoge.
    """
    h = auth_headers(client, "admin.a")
    creado = _crear(client, h, TRANSPORTADORES, {"nombre": "Stella", "valor_transporte": "242.76"})

    print("\n===== 1. LA TARIFA NO PIERDE LOS CENTAVOS =====")
    print(f"  enviado         : 242.76")
    print(f"  al crear devuelve: {creado['valor_transporte']}")
    assert D(creado["valor_transporte"]) == D("242.76")

    releido = client.get(f"{TRANSPORTADORES}/{creado['id']}", headers=h).json()
    print(f"  al releer devuelve: {releido['valor_transporte']}")
    assert D(releido["valor_transporte"]) == D("242.76")

    # Y en la lista, que es de donde el frontend llena el diálogo al reabrirlo.
    en_lista = [
        t for t in client.get(TRANSPORTADORES, headers=h).json()["items"] if t["id"] == creado["id"]
    ][0]
    print(f"  en la lista      : {en_lista['valor_transporte']}")
    assert D(en_lista["valor_transporte"]) == D("242.76")
    assert D(en_lista["valor_transporte"]) not in (D("242"), D("243"))


def test_editar_la_tarifa_a_dos_decimales_tambien_los_guarda(client, base_datos):
    """El caso del dueño es EDITAR uno que ya existe, no crearlo de cero."""
    h = auth_headers(client, "admin.a")
    creado = _crear(client, h, TRANSPORTADORES, {"nombre": "Stella", "valor_transporte": "238"})

    r = client.put(
        f"{TRANSPORTADORES}/{creado['id']}", json={"valor_transporte": "242.76"}, headers=h
    )
    assert r.status_code == 200, r.text

    print("\n===== 1b. EDITAR LA TARIFA =====")
    print(f"  antes: 238  ->  después: {r.json()['valor_transporte']}")
    assert D(r.json()["valor_transporte"]) == D("242.76")

    releido = client.get(f"{TRANSPORTADORES}/{creado['id']}", headers=h).json()
    assert D(releido["valor_transporte"]) == D("242.76")


# ---------------------------------------------------------------------------
# 2. El flete con tarifa de 242,76 da exacto y la liquidación cuadra
# ---------------------------------------------------------------------------
def test_flete_con_tarifa_de_dos_decimales_da_exacto(client, base_datos):
    """227 L × $242,76 = $55.106,52. Ni un centavo de más ni de menos.

    Es la cuenta que multiplica la tarifa por los litros de CADA recepción y la
    que después se le liquida al transportador.
    """
    h = auth_headers(client, "admin.a")
    _, transportador, proveedor = _escenario(client, h, "242.76")

    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01",
        "proveedor_id": proveedor["id"],
        "transportador_id": transportador["id"],
        "cantidad_litros": "227",
    })

    esperado = D("227") * D("242.76")
    print("\n===== 2. EL FLETE DA EXACTO =====")
    print(f"  227 L × $242,76 = ${esperado}")
    print(f"  el API devuelve : ${recepcion['valor_transporte']}")
    assert D(recepcion["valor_transporte"]) == esperado == D("55106.52")


def test_flete_con_litros_y_tarifa_decimales_cuadra_con_lo_guardado(client, base_datos):
    """El caso feo: litros CON decimales × tarifa CON decimales = 4 decimales.

    227,55 L × $242,76 da 55.240,038, y la columna es Numeric(14,2): solo caben
    dos decimales. Antes de este arreglo la cuenta no se redondeaba en Python, y
    como la sesión no expira los objetos al hacer commit, al guardar se devolvía
    55.240,038 mientras en la base quedaba 55.240,04. O sea: la pantalla mostraba
    una cifra, y al recargar salía otra.

    Con tarifas de pesos enteros esto no se notaba nunca (litros de 2 decimales
    por una tarifa entera da 2 decimales justos). Se destapa apenas la tarifa
    lleva centavos, que es justo lo que el dueño necesita.
    """
    h = auth_headers(client, "admin.a")
    _, transportador, proveedor = _escenario(client, h, "242.76")

    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-02",
        "proveedor_id": proveedor["id"],
        "transportador_id": transportador["id"],
        "cantidad_litros": "227.55",
    })

    exacto = D("227.55") * D("242.76")
    devuelto = D(recepcion["valor_transporte"])
    print("\n===== 2b. LO DEVUELTO ES LO GUARDADO =====")
    print(f"  227,55 L × $242,76 = ${exacto}  (3 decimales, no caben)")
    print(f"  el API devuelve    = ${devuelto}")

    # Se redondea al centavo, con el medio centavo para arriba (como Postgres).
    assert exacto == D("55240.038")
    assert devuelto == D("55240.04")

    # Y lo que quedó GUARDADO es el mismo número, no otro.
    guardado = client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h).json()
    print(f"  al releer          = ${guardado['valor_transporte']}")
    assert D(guardado["valor_transporte"]) == devuelto


def test_liquidacion_del_transportador_cuadra_con_tarifa_decimal(client, base_datos):
    """El desglose por día tiene que SUMAR el total. El dueño lo revisa a mano.

    Tres días con litros que llevan decimales y una tarifa de $242,76. Se compara
    día por día contra la cuenta hecha aparte, y la suma de los días contra el
    total grande de la liquidación.
    """
    h = auth_headers(client, "admin.a")
    _, transportador, proveedor = _escenario(client, h, "242.76")

    dias = {"2026-06-01": "227.55", "2026-06-02": "198.40", "2026-06-03": "203.25"}
    for fecha, litros in dias.items():
        _crear(client, h, RECEPCIONES, {
            "fecha": fecha,
            "proveedor_id": proveedor["id"],
            "transportador_id": transportador["id"],
            "cantidad_litros": litros,
        })

    generadas = client.post(
        f"{LIQUIDACIONES}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "transportador"},
        headers=h,
    )
    assert generadas.status_code in (200, 201), generadas.text
    liq_id = [
        liq for liq in generadas.json()
        if liq.get("transportador_id") == transportador["id"]
    ][0]["id"]
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()

    print("\n===== 2c. LA LIQUIDACIÓN CUADRA =====")
    print(f"  tarifa: $242,76 /L")
    suma_dias = D("0")
    for detalle in liq["detalles"]:
        esperado_dia = centavos(D(dias[detalle["fecha"]]) * D("242.76"))
        valor_dia = D(detalle["valor"])
        print(f"    {detalle['fecha']}  {detalle['litros']:>9} L × $242,76 = ${valor_dia:>12}"
              f"   (a mano: ${esperado_dia})")
        assert valor_dia == esperado_dia, f"el día {detalle['fecha']} no da"
        suma_dias += valor_dia

    total = D(liq["valor_transporte"])
    print(f"  ------------------------------------------------")
    print(f"  suma de los días : ${suma_dias}")
    print(f"  total de la liq. : ${total}")
    assert suma_dias == total, "el desglose NO suma el total: eso es lo que el dueño cacha"
    assert D(liq["valor_total"]) == total
    assert D(liq["saldo"]) == total

    # El precio por litro que se imprime en el desglose es la tarifa, con centavos.
    for detalle in liq["detalles"]:
        assert D(detalle["precio_litro"]) == D("242.76")


# ---------------------------------------------------------------------------
# 3. Las tarifas que ya existen no se mueven ni un peso
# ---------------------------------------------------------------------------
def test_las_tarifas_enteras_de_hoy_siguen_dando_lo_mismo(client, base_datos):
    """Regresión: con tarifa entera el flete es el mismo de siempre.

    El cliente real tiene tarifas de pesos enteros ($100, $130, $238). Este
    cambio agrega el redondeo al centavo en la cuenta del flete, y hay que
    demostrar que sobre esas tarifas el redondeo NO hace nada: litros con dos
    decimales por una tarifa entera da dos decimales justos.
    """
    h = auth_headers(client, "admin.a")
    _, transportador, proveedor = _escenario(client, h, "130")

    print("\n===== 3. LAS TARIFAS DE HOY NO SE MUEVEN =====")
    casos = [("2026-06-01", "227", D("29510")), ("2026-06-02", "227.55", D("29581.50"))]
    for fecha, litros, esperado in casos:
        recepcion = _crear(client, h, RECEPCIONES, {
            "fecha": fecha,
            "proveedor_id": proveedor["id"],
            "transportador_id": transportador["id"],
            "cantidad_litros": litros,
        })
        obtenido = D(recepcion["valor_transporte"])
        print(f"  {litros:>7} L × $130 = ${obtenido:>10}   (esperado ${esperado})")
        assert obtenido == esperado == D(litros) * D("130")

    # Y el valor bruto al proveedor tampoco: 227,55 × 1800 = 409.590 exacto.
    ultima = client.get(RECEPCIONES, headers=h).json()["items"]
    bruto = [r for r in ultima if r["fecha"] == "2026-06-02"][0]["valor_bruto"]
    print(f"  227,55 L × $1.800 = ${bruto}  (bruto al proveedor)")
    assert D(bruto) == D("227.55") * D("1800") == D("409590.00")


def test_tarifa_entera_guardada_se_lee_sin_decimales_de_mas(client, base_datos):
    """Una tarifa de 238 no se vuelve 238,00x ni pierde nada al releerla."""
    h = auth_headers(client, "admin.a")
    creado = _crear(client, h, TRANSPORTADORES, {"nombre": "Efraín", "valor_transporte": "238"})
    releido = client.get(f"{TRANSPORTADORES}/{creado['id']}", headers=h).json()
    print("\n===== 3b. TARIFA ENTERA =====")
    print(f"  enviado 238 -> guardado {releido['valor_transporte']}")
    assert D(releido["valor_transporte"]) == D("238")


# ---------------------------------------------------------------------------
# 4. Lo que no es un número se rechaza con mensaje claro, no con 500
# ---------------------------------------------------------------------------
def test_una_tarifa_que_no_es_numero_se_rechaza_con_mensaje_claro(client, base_datos):
    """Ni 500 ni un cero callado: 422 y un mensaje que se entiende.

    El cero callado es el peligro de verdad: si el campo se guarda en 0 porque no
    se pudo leer lo que escribieron, el transportador trabaja gratis y nadie se
    da cuenta hasta la liquidación.

    Ojo con la COMA: "242,76" es válido para una persona en Colombia, pero por
    JSON el backend recibe números con punto. Que lo rechace acá está bien; lo
    que NO puede pasar es que el frontend mande la coma o se la coma. De eso se
    encarga la directiva appMiles, que convierte antes de enviar.
    """
    h = auth_headers(client, "admin.a")

    print("\n===== 4. LO QUE NO ES NÚMERO SE RECHAZA =====")
    for i, malo in enumerate(["abc", "", "242,76", "12 pesos", None]):
        r = client.post(
            TRANSPORTADORES, json={"nombre": f"Malo{i}", "valor_transporte": malo}, headers=h
        )
        detalle = r.json().get("error", {}).get("detail", "") if r.status_code != 201 else ""
        print(f"  valor_transporte={malo!r:12} -> {r.status_code}  {detalle}")
        assert r.status_code == 422, f"{malo!r} debería rechazarse, no aceptarse"
        assert r.status_code != 500
        assert "valor_transporte" in detalle, "el mensaje tiene que decir cuál campo es"

    # Y nada de esto quedó guardado con un cero.
    items = client.get(TRANSPORTADORES, headers=h).json()["items"]
    print(f"  transportadores guardados: {len(items)} (ninguno)")
    assert items == []


def test_una_tarifa_negativa_se_rechaza(client, base_datos):
    """Nadie le paga negativo a un transportador."""
    h = auth_headers(client, "admin.a")
    r = client.post(
        TRANSPORTADORES, json={"nombre": "Negativo", "valor_transporte": "-242.76"}, headers=h
    )
    print("\n===== 4b. TARIFA NEGATIVA =====")
    print(f"  -242.76 -> {r.status_code}")
    assert r.status_code == 422, r.text


def test_el_flete_no_se_recalcula_solo_al_cambiar_la_tarifa(client, base_datos):
    """La recepción guarda su flete: cambiar la tarifa NO reescribe lo ya recibido.

    Esto no es un defecto, es la foto del día: lo que ya se recogió se pagó a la
    tarifa de ese momento. Se deja clavado para que se sepa —si el dueño corrige
    la tarifa esperando que se recalcule la quincena vieja, se va a extrañar—, y
    porque es lo que hace que el desglose siga cuadrando contra lo guardado.
    """
    h = auth_headers(client, "admin.a")
    _, transportador, proveedor = _escenario(client, h, "238")
    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01",
        "proveedor_id": proveedor["id"],
        "transportador_id": transportador["id"],
        "cantidad_litros": "227",
    })
    antes = D(recepcion["valor_transporte"])

    client.put(f"{TRANSPORTADORES}/{transportador['id']}", json={"valor_transporte": "242.76"}, headers=h)
    despues = D(client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h).json()["valor_transporte"])

    print("\n===== 4c. EL FLETE VIEJO NO SE MUEVE =====")
    print(f"  flete con tarifa $238  : ${antes}")
    print(f"  tras subirla a $242,76 : ${despues}  (la recepción vieja no cambia)")
    assert antes == despues == D("227") * D("238")


def test_una_recepcion_nueva_si_usa_la_tarifa_nueva(client, base_datos):
    """Y al contrario: lo que se reciba DESPUÉS sí va a la tarifa corregida."""
    h = auth_headers(client, "admin.a")
    _, transportador, proveedor = _escenario(client, h, "238")
    client.put(f"{TRANSPORTADORES}/{transportador['id']}", json={"valor_transporte": "242.76"}, headers=h)

    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-05",
        "proveedor_id": proveedor["id"],
        "transportador_id": transportador["id"],
        "cantidad_litros": "227",
    })
    print("\n===== 4d. LA RECEPCIÓN NUEVA USA LA TARIFA NUEVA =====")
    print(f"  227 L × $242,76 = ${recepcion['valor_transporte']}")
    assert D(recepcion["valor_transporte"]) == D("55106.52")


def test_lo_guardado_en_la_base_tiene_dos_decimales(client, base_datos, db_session):
    """Mira la columna directamente: dos decimales, ni más ni menos."""
    h = auth_headers(client, "admin.a")
    _, transportador, proveedor = _escenario(client, h, "242.76")
    _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01",
        "proveedor_id": proveedor["id"],
        "transportador_id": transportador["id"],
        "cantidad_litros": "227.55",
    })

    fila = db_session.execute(select(RecepcionLeche)).scalars().first()
    print("\n===== 5. LA COLUMNA =====")
    print(f"  227,55 L × $242,76 exacto          = {D('227.55') * D('242.76')}")
    print(f"  recepciones_leche.valor_transporte = {fila.valor_transporte!r}")
    assert D(fila.valor_transporte) == centavos(D("227.55") * D("242.76")) == D("55240.04")
    # Dos decimales, ni más ni menos: la cifra tiene que caber en la columna.
    assert D(fila.valor_transporte).as_tuple().exponent >= -2
