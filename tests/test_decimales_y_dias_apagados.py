"""LOS DECIMALES QUE LA BASE NO GUARDA Y LOS DÍAS QUE NO DEBERÍAN CONTAR.

Nació como una auditoría adversarial que solo AFIRMABA los defectos; ya están
cerrados y ahora cada prueba EXIGE lo correcto. Sigue imprimiendo las cifras a
propósito: son las que el dueño cuadra a mano.

Cubre dos familias de defectos que se destaparon juntas:

  1. LA PLATA CON MÁS DECIMALES —o más dígitos— DE LOS QUE CABEN EN LA COLUMNA.
     Todas las columnas de plata y de cantidad son Numeric(_, 2). Un schema sin
     `max_digits`/`decimal_places` aceptaba cifras que la columna no guarda, y eso
     no da un error: da una cifra distinta y callada (la pantalla decía una y al
     recargar salía otra), o un 22003 de Postgres, o sea un 500.

  2. LOS DÍAS APAGADOS ('inactivo') QUE SE COLABAN EN LAS LECTURAS. El día que la
     quesera decidió no contar sale de los dos comprobantes, de la grilla y de
     contabilidad; el resumen del período, el tablero y la alerta de "sin
     liquidar" no lo sacaban, y contradecían a contabilidad sobre la misma leche.

CÓMO SE DETECTA EL DECIMAL QUE NO CABE, porque no es obvio: la base de pruebas es
SQLite y SQLAlchemy le aplica la ESCALA de la columna al LEER (Numeric(_, 2) ->
dos decimales), así que la fila releída siempre trae dos. Lo que se mide entonces
es LA CONTRADICCIÓN: la cifra que el API respondió contra la cifra que quedó
guardada. Y donde el motor cambia el resultado —el pago parcial— se reproduce a
mano el redondeo de Postgres sobre las cifras que el API devolvió, en vez de
creerle a la fila de SQLite, que tapa el descuadre pasando por float.
"""
import uuid as _uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.modules.liquidaciones.models import Liquidacion
from app.modules.proveedores.models import Proveedor
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers

RECEPCIONES = "/api/v1/recepciones"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
LIQUIDACIONES = "/api/v1/liquidaciones"
ANTICIPOS = "/api/v1/anticipos"
NOTIFICACIONES = "/api/v1/notificaciones"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def U(v):
    """El id como UUID: el JSON trae texto y la columna es Uuid."""
    return v if isinstance(v, _uuid.UUID) else _uuid.UUID(str(v))


def decimales(v) -> int:
    exp = D(v).as_tuple().exponent
    return -exp if exp < 0 else 0


def cabe_en_la_columna(v) -> bool:
    """Si Postgres la guardaría IDÉNTICA (Numeric(_, 2))."""
    return decimales(v) <= 2


def _post(client, h, url, payload, ok=(200, 201)):
    r = client.post(url, json=payload, headers=h)
    assert r.status_code in ok, f"{url} -> {r.status_code} {r.text}"
    return r.json()


def _detalle(r) -> str:
    """El mensaje de error, recortado, para que el print diga qué rebotó."""
    try:
        cuerpo = r.json()
    except ValueError:
        return ""
    error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
    if isinstance(error, dict):
        return str(error.get("detail", ""))[:120]
    return str(cuerpo)[:120]


def _escenario(client, h, tarifa="242.76", precio="1800", sufijo=""):
    ruta = _post(client, h, "/api/v1/rutas",
                 {"nombre": f"Napoles{sufijo}", "municipio": "Norte"})
    trans = _post(client, h, TRANSPORTADORES, {
        "nombre": f"Alex{sufijo}", "valor_transporte": tarifa,
        "rutas": [{"ruta_id": ruta["id"], "valor_transporte": tarifa}],
    })
    prov = _post(client, h, PROVEEDORES, {
        "nombre": f"Patricia Laguna{sufijo}", "vereda": "Norte",
        "precio_litro": precio, "ruta_id": ruta["id"],
    })
    return ruta, trans, prov


# ===========================================================================
# BLOQUE 1 — LITROS Y PRECIOS CON 3, 4 Y 5 DECIMALES, AL CREAR Y AL EDITAR
# ===========================================================================
DECIMALES_FEOS = ["44.235", "44.2354", "44.23546"]


def test_1a_crear_con_litros_de_3_4_y_5_decimales(client, base_datos, db_session):
    """Los litros se redondean en la ENTRADA y la foto del flete cuadra con ellos."""
    h = auth_headers(client, "admin.a")
    _, trans, prov = _escenario(client, h)

    print("\n===== 1a. CREAR con litros de 3/4/5 decimales =====")
    for i, litros in enumerate(DECIMALES_FEOS):
        r = _post(client, h, RECEPCIONES, {
            "fecha": f"2026-06-0{i + 1}",
            "proveedor_id": prov["id"], "transportador_id": trans["id"],
            "cantidad_litros": litros,
        })
        fila = db_session.get(RecepcionLeche, U(r["id"]))
        esperado = centavos(D(fila.cantidad_litros) * D("242.76"))
        print(f"  enviado {litros:>10} L -> API {r['cantidad_litros']:>7}"
              f" -> guardado {fila.cantidad_litros}  flete {fila.valor_transporte}"
              f" (a mano {esperado})")
        assert cabe_en_la_columna(fila.cantidad_litros)
        assert cabe_en_la_columna(fila.valor_transporte)
        assert cabe_en_la_columna(fila.valor_bruto)
        assert cabe_en_la_columna(fila.valor_neto)
        assert D(r["cantidad_litros"]) == D(fila.cantidad_litros), "responde una cifra y guarda otra"
        assert D(fila.valor_transporte) == esperado


def test_1b_editar_con_litros_y_precio_de_3_4_y_5_decimales(client, base_datos, db_session):
    """El PUT también redondea antes de calcular: nada de tres decimales."""
    h = auth_headers(client, "admin.a")
    _, trans, prov = _escenario(client, h)
    r = _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "44",
    })

    print("\n===== 1b. EDITAR con litros y precio de 3/4/5 decimales =====")
    for litros, precio in zip(DECIMALES_FEOS, ["1800.005", "1800.0054", "1800.00546"]):
        resp = client.put(f"{RECEPCIONES}/{r['id']}",
                          json={"cantidad_litros": litros, "precio_litro": precio}, headers=h)
        assert resp.status_code == 200, resp.text
        fila = db_session.get(RecepcionLeche, U(r["id"]))
        db_session.refresh(fila)
        print(f"  {litros:>10} L x ${precio:<11} -> guardado {fila.cantidad_litros} L"
              f" x ${fila.precio_litro} = ${fila.valor_bruto}")
        assert cabe_en_la_columna(fila.cantidad_litros)
        assert cabe_en_la_columna(fila.precio_litro)
        assert cabe_en_la_columna(fila.valor_bruto)
        assert D(resp.json()["cantidad_litros"]) == D(fila.cantidad_litros)
        assert D(resp.json()["precio_litro"]) == D(fila.precio_litro)
        assert D(fila.valor_bruto) == centavos(D(fila.cantidad_litros) * D(fila.precio_litro))


def test_1c_bonificaciones_y_descuentos_con_decimales_feos(client, base_datos, db_session):
    """Los otros dos campos de plata de la recepción: también en la entrada."""
    h = auth_headers(client, "admin.a")
    _, trans, prov = _escenario(client, h)
    r = _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "44",
        "bonificaciones": "1000.005", "descuentos": "500.0049",
    })
    fila = db_session.get(RecepcionLeche, U(r["id"]))
    print("\n===== 1c. BONIFICACIONES / DESCUENTOS =====")
    print(f"  bonif 1000.005 -> {fila.bonificaciones}   desc 500.0049 -> {fila.descuentos}")
    print(f"  valor_neto = {fila.valor_neto}")
    assert cabe_en_la_columna(fila.bonificaciones)
    assert cabe_en_la_columna(fila.descuentos)
    assert cabe_en_la_columna(fila.valor_neto)
    # Medio centavo PARA ARRIBA, no banquero: 1000,005 -> 1000,01
    assert D(fila.bonificaciones) == D("1000.01"), "el medio centavo tiene que subir"
    assert D(fila.descuentos) == D("500.00")


def test_1d_el_redondeo_es_medio_arriba_y_no_banquero(client, base_datos, db_session):
    """0,005 SUBE siempre, aunque el dígito anterior sea par (banquero bajaría)."""
    h = auth_headers(client, "admin.a")
    _, trans, prov = _escenario(client, h)
    print("\n===== 1d. MEDIO CENTAVO ARRIBA, NO BANQUERO =====")
    casos = [("44.225", "44.23"), ("44.235", "44.24"), ("44.245", "44.25"), ("44.255", "44.26")]
    for i, (enviado, esperado) in enumerate(casos):
        r = _post(client, h, RECEPCIONES, {
            "fecha": f"2026-07-0{i + 1}", "proveedor_id": prov["id"],
            "transportador_id": trans["id"], "cantidad_litros": enviado,
        })
        fila = db_session.get(RecepcionLeche, U(r["id"]))
        banquero = D(enviado).quantize(D("0.01"))  # ROUND_HALF_EVEN por defecto
        print(f"  {enviado} -> {fila.cantidad_litros}   (banquero daría {banquero})")
        assert D(fila.cantidad_litros) == D(esperado)


def test_1e_el_precio_por_litro_del_comprobante_tambien_sube_el_medio_centavo(
    client, base_datos, db_session
):
    """El otro camino que escribe `recepciones.precio_litro`: corregir el precio de
    un día desde el comprobante (`PUT /liquidaciones/{id}/detalles/{id}`)."""
    h = auth_headers(client, "admin.a")
    _, trans, prov = _escenario(client, h)
    rec = _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "137.45"})
    gen = _post(client, h, f"{LIQUIDACIONES}/generar", {
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"})
    liq_id = gen[0]["id"]
    detalle_id = gen[0]["detalles"][0]["id"]

    r = client.put(f"{LIQUIDACIONES}/{liq_id}/detalles/{detalle_id}",
                   json={"precio_litro": "1800.005"}, headers=h)
    assert r.status_code == 200, r.text
    fila = db_session.get(RecepcionLeche, U(rec["id"]))
    db_session.refresh(fila)
    d = r.json()["detalles"][0]
    print("\n===== 1e. CORREGIR EL PRECIO DEL DÍA DESDE EL COMPROBANTE =====")
    print(f"  enviado 1800.005 -> recepcion.precio_litro = {fila.precio_litro}"
          f"  (banquero daría 1800.00)")
    print(f"  renglón: {d['litros']} L x ${d['precio_litro']} = ${d['valor']}")
    assert D(fila.precio_litro) == D("1800.01"), "el medio centavo tiene que subir"
    assert cabe_en_la_columna(fila.valor_bruto)
    assert D(fila.valor_bruto) == centavos(D("137.45") * D("1800.01"))
    assert D(d["valor"]) == D(r.json()["valor_total"])


def test_1f_la_recepcion_tiene_TECHO_y_el_redondeo_no_se_rinde(client, base_datos):
    """EL TECHO DE LOS CUATRO CAMPOS DE LA RECEPCIÓN, que era lo que faltaba.

    `_a_dos_decimales` cuidaba los DECIMALES y nadie cuidaba los DÍGITOS: el
    schema solo ponía `gt=0` / `ge=0`, sin el `max_digits` que sí tiene la tarifa
    del transportador. Con eso:

      · `cantidad_litros = "1e20"` entraba con un 201 y en Postgres el INSERT moría
        con 22003 (numeric field overflow) — un 500 en la cara del usuario;
      · y `bonificaciones = 1E+30` se guardaba CRUDA, sin redondear, porque el
        `except ArithmeticError` del redondeo devolvía el valor tal cual "para que
        lo rechace Pydantic" y no había ningún constraint que lo rechazara.

    Ahora las dos cosas rebotan con un 422, que es un mensaje y no un 500. Y el
    redondeo ya no se rinde: la precisión se estira a la medida del número (ver
    `app/common/schemas.py::a_dos_decimales`).
    """
    h = auth_headers(client, "admin.a")
    _, trans, prov = _escenario(client, h)
    print("\n===== 1f. LA RECEPCIÓN TIENE TECHO =====")

    # cantidad_litros y precio_litro son Numeric(12,2): hasta 9.999.999.999,99.
    for campo, malo in [
        ("cantidad_litros", "1e20"),
        ("cantidad_litros", "10000000000"),
        ("cantidad_litros", "99999999999999999.99"),
        ("bonificaciones", "1E+30"),
        ("descuentos", "1e20"),
    ]:
        payload = {"fecha": "2026-06-01", "proveedor_id": prov["id"],
                   "transportador_id": trans["id"], "cantidad_litros": "44.23"}
        payload[campo] = malo
        r = client.post(RECEPCIONES, json=payload, headers=h)
        print(f"  {campo}={malo:<24} -> {r.status_code}  {_detalle(r)[:70]}")
        assert r.status_code == 422, f"{campo}={malo} entró: en Postgres es un 22003 -> 500"

    # Y el tope de cordura del precio por litro, el mismo que ya tenía la
    # corrección desde el comprobante: el litro anda por los $1.800.
    r = client.post(RECEPCIONES, json={
        "fecha": "2026-06-01", "proveedor_id": prov["id"],
        "cantidad_litros": "44.23", "precio_litro": "1000000.01"}, headers=h)
    print(f"  precio_litro=1000000.01 (tope 1.000.000) -> {r.status_code}")
    assert r.status_code == 422

    # Y una cifra grande de verdad sigue entrando, con todo y techo: el día más
    # absurdo que un teclado puede escribir sin equivocarse de columna. Se revisan
    # también las cifras DERIVADAS, que son las que tienen que caber en Numeric(14,2):
    # el techo de la entrada no sirve de nada si el producto no cabe.
    ok = _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-02", "proveedor_id": prov["id"],
        "cantidad_litros": "99999.99", "precio_litro": "1000000"})
    bruto = D(ok["valor_bruto"])
    print(f"  99.999,99 L x $1.000.000 -> 201, valor_bruto={bruto}"
          f" ({len(bruto.as_tuple().digits)} dígitos, la columna aguanta 14)")
    assert D(ok["cantidad_litros"]) == D("99999.99")
    assert bruto == centavos(D("99999.99") * D("1000000"))
    assert len(bruto.as_tuple().digits) <= 14, "el valor derivado no cabe en su columna"

    # El molde de donde salió todo esto: la tarifa del transportador.
    r3 = client.post(TRANSPORTADORES,
                     json={"nombre": "Enorme", "valor_transporte": "1e20"}, headers=h)
    print(f"  (el molde) transportadores.valor_transporte=1e20 -> {r3.status_code}")
    assert r3.status_code == 422


def test_1g_el_redondeo_no_se_rinde_con_un_numero_gigante(client, base_datos, db_session):
    """EL `except` QUE SE RENDÍA, medido aparte y sin pasar por el techo.

    Se prueba la función directamente porque es la que tenía el defecto, y se
    compara CARÁCTER POR CARÁCTER (`str`) y no por valor: `Decimal("1E+30")` y
    `Decimal("1000...000.00")` son iguales en valor, así que comparar números no
    distingue la cifra redondeada de la que salió cruda.

    Antes, toda cifra que no cupiera en la precisión del contexto (28 dígitos por
    omisión) salía SIN REDONDEAR. Hoy se redondea hasta 40 dígitos enteros —más del
    triple de la columna más ancha— y solo las absurdas por encima de eso salen tal
    cual, para que las rechace el techo del campo con un 422 en vez de un 500 de
    Postgres.
    """
    from app.common.schemas import a_dos_decimales

    print("\n===== 1g. EL REDONDEO YA NO SE RINDE =====")
    casos = [
        ("44.235", "44.24"),
        ("0.005", "0.01"),
        ("12345678901234567890.125", "12345678901234567890.13"),
        # Estas dos son las que la versión anterior devolvía CRUDAS.
        ("1E+30", "1" + "0" * 30 + ".00"),
        ("1E+39", "1" + "0" * 39 + ".00"),
    ]
    for enviado, esperado in casos:
        salida = a_dos_decimales(enviado)
        print(f"  {enviado:>26} -> {str(salida)[:46]}")
        assert str(salida) == esperado, "salió sin redondear"
    # Y lo absurdo sale tal cual, para que lo pare el techo del campo con un 422:
    # no hay columna donde quepa y redondearlo sería gastar memoria de gusto.
    for absurda, tal_cual in [
        ("1E+40", "1E+40"),
        ("1E+400", "1E+400"),
        ("nan", "NaN"),
        ("inf", "Infinity"),
        ("no soy un numero", "no soy un numero"),
    ]:
        salida = a_dos_decimales(absurda)
        print(f"  {absurda:>26} -> {salida}  (la para el techo)")
        assert str(salida) == tal_cual


# ===========================================================================
# BLOQUE 2 — EL PRECIO DEL PROVEEDOR: LA PLATA DEL PRODUCTOR
# ===========================================================================
def test_2a_el_precio_del_proveedor_rechaza_el_tercer_decimal(client, base_datos, db_session):
    """`proveedores.precio_litro` es Numeric(12,2) y ahora el schema lo cuida.

    Antes era un `Decimal = Field(ge=0)` pelado: $1.800,005 entraba, el POST
    respondía $1.800,005 y la columna guardaba $1.800,01. La pantalla del
    proveedor mostraba un precio por litro que NO es el que se le va a pagar, y al
    recargar salía otro. Y es la plata del productor: este precio es el que hereda
    la recepción del día.

    ACÁ SE RECHAZA Y NO SE REDONDEA, como en la tarifa del transportador: un peso
    mal tecleado en un precio no es un pesaje que haya que ajustar, es un dato
    equivocado, y el usuario tiene que verlo.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 2a. EL PRECIO DEL PROVEEDOR RECHAZA EL TERCER DECIMAL =====")
    for malo in ["1800.005", "1800.0054", "1800.00546"]:
        r = client.post(PROVEEDORES, json={"nombre": "Marleny", "precio_litro": malo}, headers=h)
        print(f"  precio_litro={malo:<12} -> {r.status_code}  {_detalle(r)[:70]}")
        assert r.status_code == 422, "se colaron decimales que la columna no guarda"

    # Y lo que cabe entra, responde y guarda LA MISMA cifra.
    creado = _post(client, h, PROVEEDORES, {"nombre": "Marleny", "precio_litro": "1800.01"})
    fila = db_session.get(Proveedor, U(creado["id"]))
    releido = client.get(f"{PROVEEDORES}/{creado['id']}", headers=h).json()
    print(f"  precio_litro=1800.01 -> el POST responde {creado['precio_litro']},"
          f" la columna guarda {fila.precio_litro}, al releer {releido['precio_litro']}")
    assert D(creado["precio_litro"]) == D(fila.precio_litro) == D(releido["precio_litro"])


def test_2b_el_PUT_del_proveedor_tambien(client, base_datos):
    """El caso real es EDITARLE el precio al proveedor, no crearlo."""
    h = auth_headers(client, "admin.a")
    creado = _post(client, h, PROVEEDORES, {"nombre": "Marleny", "precio_litro": "1800"})
    print("\n===== 2b. EL PUT DEL PROVEEDOR =====")
    r = client.put(f"{PROVEEDORES}/{creado['id']}", json={"precio_litro": "1800.0054"}, headers=h)
    print(f"  PUT precio_litro=1800.0054 -> {r.status_code}  {_detalle(r)[:70]}")
    assert r.status_code == 422

    r = client.put(f"{PROVEEDORES}/{creado['id']}", json={"precio_litro": "1850.50"}, headers=h)
    assert r.status_code == 200, r.text
    releido = client.get(f"{PROVEEDORES}/{creado['id']}", headers=h).json()
    print(f"  PUT precio_litro=1850.50   -> responde {r.json()['precio_litro']}"
          f" -> al releer {releido['precio_litro']}")
    assert D(r.json()["precio_litro"]) == D(releido["precio_litro"]) == D("1850.50")


def test_2c_el_precio_del_proveedor_tiene_techo(client, base_datos):
    """`Numeric(12,2)` aguanta $9.999.999.999,99 y nada más.

    En Postgres un INSERT por encima revienta con 22003 (numeric field overflow) y
    el usuario ve un 500 en vez de un mensaje. Es el mismo agujero que en la tarifa
    del transportador se cerró con `max_digits=12`. Y el tope de cordura —un millón
    por litro— es el que `LiquidacionDetallePrecioUpdate` ya declaraba para esta
    misma cifra.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 2c. EL PRECIO DEL PROVEEDOR TIENE TECHO =====")
    for i, malo in enumerate(["1e20", "99999999999999999.99", "1000000.01"]):
        r = client.post(PROVEEDORES, json={"nombre": f"Grande{i}", "precio_litro": malo}, headers=h)
        print(f"  precio_litro={malo:<24} -> {r.status_code} (antes: 201 y un 22003 en Postgres)")
        assert r.status_code == 422
    r = client.post(TRANSPORTADORES, json={"nombre": "Alex", "valor_transporte": "1e20"}, headers=h)
    print(f"  (el molde) transportadores.valor_transporte=1e20 -> {r.status_code}")
    assert r.status_code == 422


def test_2d_el_precio_que_hereda_la_recepcion_sale_de_la_base_no_del_payload(
    client, base_datos, db_session
):
    """LO QUE SÍ ESTÁ BIEN, y conviene dejarlo clavado: la recepción sin
    `precio_litro` lo copia del proveedor LEÍDO DE LA BASE, así que hereda la
    cifra ya recortada a dos decimales y `valor_bruto` cuadra con ella.

    El precio de tres decimales se PLANTA a mano en la fila, porque por el API ya
    no entra (ver 2a): así queda medido que un proveedor sucio de antes del arreglo
    —o de una carga por fuera del API— tampoco descuadra la cuenta del día.

    Se escribe y se EXPIRA el objeto para que la lectura vuelva a la base, que es
    lo que hace una petición nueva: la fila releída trae la cifra con la escala de
    la columna (dos decimales), igual que en Postgres.
    """
    h = auth_headers(client, "admin.a")
    ruta = _post(client, h, "/api/v1/rutas", {"nombre": "Napoles", "municipio": "Norte"})
    prov = _post(client, h, PROVEEDORES, {
        "nombre": "Marleny", "precio_litro": "1800", "ruta_id": ruta["id"]})
    sucio = db_session.get(Proveedor, U(prov["id"]))
    sucio.precio_litro = D("1800.005")
    db_session.flush()
    db_session.expire(sucio)

    r = _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": prov["id"], "cantidad_litros": "137.45"})
    fila = db_session.get(RecepcionLeche, U(r["id"]))
    print("\n===== 2d. LA RECEPCIÓN HEREDA LA CIFRA YA RECORTADA =====")
    print(f"  proveedor con 1800.005 plantado a mano -> recepcion.precio_litro ="
          f" {fila.precio_litro}")
    print(f"  137,45 x {fila.precio_litro} = {fila.valor_bruto}")
    assert cabe_en_la_columna(fila.precio_litro)
    assert D(fila.valor_bruto) == centavos(D("137.45") * D(fila.precio_litro))


# ===========================================================================
# BLOQUE 3 — LA PLATA DE OTROS MÓDULOS QUE CAE EN EL MISMO COMPROBANTE
# ===========================================================================
def test_3a_el_anticipo_rechaza_el_tercer_decimal_y_tiene_techo(client, base_datos):
    """`anticipos.valor` es Numeric(14,2) y era un `Field(gt=0)` pelado.

    El anticipo es un renglón del comprobante ("Anticipos aplicados") y le resta
    al SALDO A PAGAR, la cifra grande que el productor recibe: el POST respondía
    $500.000,005 y la columna guardaba $500.000,01.
    """
    h = auth_headers(client, "admin.a")
    _, _, prov = _escenario(client, h)
    print("\n===== 3a. EL VALOR DEL ANTICIPO =====")
    for malo in ["500000.005", "1e20", "99999999999999999.99"]:
        r = client.post(ANTICIPOS, json={
            "tipo": "proveedor", "proveedor_id": prov["id"],
            "fecha": "2026-06-05", "valor": malo}, headers=h)
        print(f"  valor={malo:<24} -> {r.status_code}  {_detalle(r)[:60]}")
        assert r.status_code == 422

    a = _post(client, h, ANTICIPOS, {
        "tipo": "proveedor", "proveedor_id": prov["id"],
        "fecha": "2026-06-05", "valor": "500000.01"})
    releido = client.get(f"{ANTICIPOS}/{a['id']}", headers=h).json()
    print(f"  valor=500000.01 -> el POST responde {a['valor']} -> al releer {releido['valor']}")
    assert D(a["valor"]) == D(releido["valor"]) == D("500000.01")


def test_3b_el_pago_parcial_rechaza_el_tercer_decimal(client, base_datos):
    """`pagos_liquidacion.valor` es Numeric(14,2) y era un `Field(gt=0)` pelado.

    Con $100.000,005 la respuesta decía pagado = $100.000,005 y saldo =
    $979.999,995: la pantalla del pago decía una cifra y al recargar decía otra, y
    en Postgres se descuadraba el comprobante (ver 3c).
    """
    h = auth_headers(client, "admin.a")
    _, _, prov = _escenario(client, h)
    _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": prov["id"], "cantidad_litros": "600"})
    gen = _post(client, h, f"{LIQUIDACIONES}/generar", {
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"})
    liq_id = gen[0]["id"]
    _post(client, h, f"{LIQUIDACIONES}/{liq_id}/aprobar", {})

    print("\n===== 3b. EL VALOR DEL PAGO PARCIAL =====")
    for malo in ["100000.005", "1e20"]:
        r = client.post(f"{LIQUIDACIONES}/{liq_id}/pagos",
                        json={"fecha": "2026-06-20", "valor": malo}, headers=h)
        print(f"  valor={malo:<12} -> {r.status_code}  {_detalle(r)[:60]}")
        assert r.status_code == 422
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  no quedó ningún pago registrado: pagos={liq['pagos']} estado={liq['estado']}")
    assert liq["pagos"] == []
    assert liq["estado"] == "aprobada"

    respuesta = _post(client, h, f"{LIQUIDACIONES}/{liq_id}/pagos", {
        "fecha": "2026-06-20", "valor": "100000.01"})
    releido = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  valor=100000.01 -> el POST responde pago={respuesta['pagos'][0]['valor']}"
          f" pagado={respuesta['pagado']} saldo={respuesta['saldo']}")
    print(f"  al releer                  pago={releido['pagos'][0]['valor']}"
          f" pagado={releido['pagado']} saldo={releido['saldo']}")
    assert cabe_en_la_columna(respuesta["pagado"]) and cabe_en_la_columna(respuesta["saldo"])
    assert D(releido["pagado"]) == D(respuesta["pagado"])
    assert D(releido["saldo"]) == D(respuesta["saldo"])


def test_3c_neto_es_pagado_mas_saldo_EXACTO_en_los_dos_motores(client, base_datos):
    """LA REGLA DE ORO EN EL PAGO PARCIAL: `neto_a_pagar = pagado + saldo`, exacto.

    Es lo que el comentario de `Liquidacion.saldo` promete y lo que el dueño resta a
    mano en el comprobante (VALOR TOTAL, Pagado, SALDO A PAGAR). Con un pago de tres
    decimales `pagado` y `saldo` se redondeaban cada uno por su lado y la resta
    dejaba de dar: en Postgres —numeric de verdad, medio para arriba en los dos— las
    dos subían y el papel quedaba UN CENTAVO largo ($1.080.000,01 contra un neto de
    $1.080.000,00). SQLite lo tapaba pasando por float, y por eso la suite no lo
    delataba; acá se reproduce a mano el redondeo de Postgres sobre las cifras que
    el API devolvió, en vez de creerle a la fila.
    """
    h = auth_headers(client, "admin.a")
    _, _, prov = _escenario(client, h)
    _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": prov["id"], "cantidad_litros": "600"})
    gen = _post(client, h, f"{LIQUIDACIONES}/generar", {
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"})
    liq_id = gen[0]["id"]
    _post(client, h, f"{LIQUIDACIONES}/{liq_id}/aprobar", {})

    print("\n===== 3c. neto == pagado + saldo, EXACTO =====")
    rechazado = client.post(f"{LIQUIDACIONES}/{liq_id}/pagos",
                            json={"fecha": "2026-06-20", "valor": "100000.005"}, headers=h)
    print(f"  el pago de tres decimales ni entra: {rechazado.status_code}")
    assert rechazado.status_code == 422

    respuesta = _post(client, h, f"{LIQUIDACIONES}/{liq_id}/pagos", {
        "fecha": "2026-06-20", "valor": "100000.01"})
    neto = D(respuesta["neto_a_pagar"])
    pagado, saldo = D(respuesta["pagado"]), D(respuesta["saldo"])
    # Lo que Postgres guardaría en Numeric(14,2): medio centavo para arriba. Con
    # cifras de dos decimales no cambia nada, y eso es justo lo que se está midiendo.
    pagado_pg, saldo_pg = centavos(pagado), centavos(saldo)
    print(f"  VALOR TOTAL / neto_a_pagar = {neto}")
    print(f"  pagado = {pagado}   saldo = {saldo}   suma = {pagado + saldo}")
    print(f"  como los guarda Postgres: {pagado_pg} + {saldo_pg} = {pagado_pg + saldo_pg}")
    assert pagado + saldo == neto, "el desglose no suma la cifra grande"
    assert pagado_pg + saldo_pg == neto, "en Postgres el papel quedaría descuadrado"

    # Y al releer sigue cuadrando: la cifra guardada es la misma que se respondió.
    releido = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  al releer: {releido['pagado']} + {releido['saldo']}"
          f" = {D(releido['pagado']) + D(releido['saldo'])}")
    assert D(releido["pagado"]) + D(releido["saldo"]) == D(releido["neto_a_pagar"])


def test_3d_un_pago_de_un_milesimo_no_traba_la_liquidacion(client, base_datos, db_session):
    """EL BORDE DEL MISMO HUECO, que dejaba la liquidación TRABADA.

    Con `gt=0` pelado, $0,001 pasaba: la fila del pago se guardaba en $0,00 y
    `pagado` también, así que `tiene_pagos` decía False —no hay plata— pero el
    estado había quedado en 'parcial'. Resultado: recalcular la rebotaba (exige
    borrador) y el candado de las recepciones no la veía pagada. Nadie podía
    destrabarla.

    Con el tercer decimal rechazado el pago no entra, el estado no se mueve y las
    dos señas vuelven a decir lo mismo. Y la liquidación sigue viva: un pago de
    verdad entra y la deja en 'parcial' con plata adentro.
    """
    h = auth_headers(client, "admin.a")
    _, _, prov = _escenario(client, h)
    _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": prov["id"], "cantidad_litros": "600"})
    gen = _post(client, h, f"{LIQUIDACIONES}/generar", {
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"})
    liq_id = gen[0]["id"]
    _post(client, h, f"{LIQUIDACIONES}/{liq_id}/aprobar", {})

    print("\n===== 3d. UN PAGO DE $0,001 NO TRABA LA LIQUIDACIÓN =====")
    r = client.post(f"{LIQUIDACIONES}/{liq_id}/pagos",
                    json={"fecha": "2026-06-20", "valor": "0.001"}, headers=h)
    print(f"  POST /pagos valor=0.001 -> {r.status_code}  {_detalle(r)[:70]}")
    assert r.status_code == 422

    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    fila = db_session.get(Liquidacion, U(liq_id))
    db_session.refresh(fila)
    print(f"  estado={liq['estado']}  pagos={len(liq['pagos'])}  pagado={liq['pagado']}"
          f"  saldo={liq['saldo']}  tiene_pagos={fila.tiene_pagos}")
    assert liq["estado"] == "aprobada", "el estado se movió por un pago que no entró"
    assert liq["pagos"] == []
    assert D(liq["pagado"]) == D("0.00")
    assert fila.tiene_pagos is False
    assert D(liq["pagado"]) + D(liq["saldo"]) == D(liq["neto_a_pagar"])

    # Y un pago de verdad sí entra: la liquidación no quedó trabada.
    bueno = _post(client, h, f"{LIQUIDACIONES}/{liq_id}/pagos", {
        "fecha": "2026-06-20", "valor": "0.01"})
    db_session.refresh(fila)
    print(f"  después de un pago de $0,01: estado={bueno['estado']}"
          f" pagado={bueno['pagado']} tiene_pagos={fila.tiene_pagos}")
    assert bueno["estado"] == "parcial"
    assert D(bueno["pagado"]) == D("0.01")
    assert fila.tiene_pagos is True, "un 'parcial' con plata: el candado lo tiene que ver"


def test_3e_corregir_un_anticipo_a_tres_decimales_rebota_y_el_comprobante_cuadra(
    client, base_datos
):
    """El PUT del anticipo, que es el camino real (le corrigen la cifra).

    Antes respondía $500.000,005 y guardaba $500.000,01. La liquidación NO se
    descuadraba —`_aplicar_anticipos_pendientes` vuelve a SUMAR desde la base, y
    ahí el valor ya venía recortado—, pero la pantalla del anticipo decía una cifra
    y al recargar otra. Ahora rebota, y la corrección buena sigue cuadrando el
    comprobante.
    """
    h = auth_headers(client, "admin.a")
    _, _, prov = _escenario(client, h)
    a = _post(client, h, ANTICIPOS, {
        "tipo": "proveedor", "proveedor_id": prov["id"],
        "fecha": "2026-06-05", "valor": "400000"})
    _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": prov["id"], "cantidad_litros": "600"})
    gen = _post(client, h, f"{LIQUIDACIONES}/generar", {
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"})
    liq_id = gen[0]["id"]

    print("\n===== 3e. CORREGIR UN ANTICIPO =====")
    r = client.put(f"{ANTICIPOS}/{a['id']}", json={"valor": "500000.005"}, headers=h)
    print(f"  PUT valor=500000.005 -> {r.status_code}  {_detalle(r)[:60]}")
    assert r.status_code == 422

    r = client.put(f"{ANTICIPOS}/{a['id']}", json={"valor": "500000.01"}, headers=h)
    assert r.status_code == 200, r.text
    releido_anticipo = client.get(f"{ANTICIPOS}/{a['id']}", headers=h).json()
    despues = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  PUT valor=500000.01  -> responde {r.json()['valor']}"
          f" -> al releer {releido_anticipo['valor']}")
    print(f"  anticipos aplicados en el comprobante = {despues['anticipos']}")
    print(f"  SALDO A PAGAR                         = {despues['saldo']}")
    assert D(r.json()["valor"]) == D(releido_anticipo["valor"]) == D("500000.01")
    assert cabe_en_la_columna(despues["anticipos"]) and cabe_en_la_columna(despues["saldo"])
    assert D(despues["saldo"]) == D(despues["valor_total"]) - D(despues["anticipos"])


def test_3f_la_tarifa_del_transportador_tambien_rechaza_los_decimales_de_mas(
    client, base_datos
):
    """EL MOLDE de todo el bloque: `decimal_places=2` -> 422."""
    h = auth_headers(client, "admin.a")
    print("\n===== 3f. EL MOLDE: LA TARIFA DEL TRANSPORTADOR =====")
    for malo in ["242.765", "242.7654"]:
        r = client.post(TRANSPORTADORES,
                        json={"nombre": "Alex", "valor_transporte": malo}, headers=h)
        print(f"  valor_transporte={malo} -> {r.status_code}")
        assert r.status_code == 422, r.text


# ===========================================================================
# BLOQUE 4 — DÍAS QUE NO DEBERÍAN CONTAR
# ===========================================================================
def _quincena_completa(client, h, tarifa="242.76", sufijo=""):
    ruta, trans, prov = _escenario(client, h, tarifa=tarifa, sufijo=sufijo)
    ids = {}
    for fecha, litros in [("2026-06-01", "44.23"), ("2026-06-02", "82.48"),
                          ("2026-06-03", "126.71")]:
        ids[fecha] = _post(client, h, RECEPCIONES, {
            "fecha": fecha, "proveedor_id": prov["id"],
            "transportador_id": trans["id"], "cantidad_litros": litros})["id"]
    return ruta, trans, prov, ids


def _liqs(client, h, tipo="ambos", inicio="2026-06-01", fin="2026-06-15"):
    return _post(client, h, f"{LIQUIDACIONES}/generar",
                 {"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo})


def test_4a_dia_apagado_sale_de_los_dos_comprobantes_al_generar(client, base_datos):
    """Un día 'inactivo' NO entra ni en el del productor ni en el del conductor."""
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    r = client.put(f"{RECEPCIONES}/{ids['2026-06-02']}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text

    liqs = _liqs(client, h)
    print("\n===== 4a. DÍA APAGADO, AL GENERAR =====")
    for liq in liqs:
        d = client.get(f"{LIQUIDACIONES}/{liq['id']}", headers=h).json()
        fechas = sorted({x["fecha"] for x in d["detalles"]})
        litros = sum(D(x["litros"]) for x in d["detalles"])
        print(f"  {d['tipo']:<14} litros={d['total_litros']:>10}  días={fechas}")
        assert "2026-06-02" not in fechas, "el día apagado se está cobrando"
        assert D(d["total_litros"]) == D("44.23") + D("126.71")
        assert litros == D(d["total_litros"])


def test_4b_dia_apagado_sale_de_los_dos_comprobantes_al_RECALCULAR(client, base_datos):
    """Se genera primero y se apaga después: el recuadre lo tiene que sacar."""
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    liqs = _liqs(client, h)
    print("\n===== 4b. DÍA APAGADO DESPUÉS DE GENERAR (recuadre) =====")
    for liq in liqs:
        print(f"  antes   {liq['tipo']:<14} litros={liq['total_litros']} total={liq['valor_total']}")

    r = client.put(f"{RECEPCIONES}/{ids['2026-06-02']}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    for liq in liqs:
        d = client.get(f"{LIQUIDACIONES}/{liq['id']}", headers=h).json()
        fechas = sorted({x["fecha"] for x in d["detalles"]})
        suma = sum(D(x["valor"]) for x in d["detalles"])
        print(f"  después {d['tipo']:<14} litros={d['total_litros']:>10}"
              f" total={d['valor_total']:>12} suma renglones={suma} días={fechas}")
        assert "2026-06-02" not in fechas, "el día apagado sigue en el comprobante"
        assert D(d["total_litros"]) == D("44.23") + D("126.71")
        assert suma == D(d["valor_total"]), "el desglose no suma el total"


def test_4c_dia_borrado_en_suave_sale_de_los_dos_comprobantes(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    liqs = _liqs(client, h)
    r = client.delete(f"{RECEPCIONES}/{ids['2026-06-02']}", headers=h)
    assert r.status_code == 204, r.text
    print("\n===== 4c. DÍA BORRADO EN SUAVE =====")
    for liq in liqs:
        d = client.get(f"{LIQUIDACIONES}/{liq['id']}", headers=h).json()
        fechas = sorted({x["fecha"] for x in d["detalles"]})
        suma = sum(D(x["valor"]) for x in d["detalles"])
        print(f"  {d['tipo']:<14} litros={d['total_litros']:>10} total={d['valor_total']:>12}"
              f" suma={suma} días={fechas}")
        assert "2026-06-02" not in fechas
        assert D(d["total_litros"]) == D("44.23") + D("126.71")
        assert suma == D(d["valor_total"])


def test_4d_dia_movido_de_periodo_sale_del_comprobante_viejo(client, base_datos):
    """La fecha se sale de la quincena: el día se suelta y el papel se recuadra."""
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    liqs = _liqs(client, h)
    r = client.put(f"{RECEPCIONES}/{ids['2026-06-02']}", json={"fecha": "2026-06-20"}, headers=h)
    assert r.status_code == 200, r.text
    print("\n===== 4d. DÍA MOVIDO A OTRO PERÍODO =====")
    for liq in liqs:
        d = client.get(f"{LIQUIDACIONES}/{liq['id']}", headers=h).json()
        fechas = sorted({x["fecha"] for x in d["detalles"]})
        suma = sum(D(x["valor"]) for x in d["detalles"])
        print(f"  {d['tipo']:<14} litros={d['total_litros']:>10} total={d['valor_total']:>12}"
              f" días={fechas}")
        assert "2026-06-02" not in fechas and "2026-06-20" not in fechas
        assert D(d["total_litros"]) == D("44.23") + D("126.71")
        assert suma == D(d["valor_total"])
    nuevas = _liqs(client, h, inicio="2026-06-16", fin="2026-06-30")
    print(f"  la quincena siguiente lo recoge: "
          f"{[(n['tipo'], n['total_litros']) for n in nuevas]}")
    assert all(D(n["total_litros"]) == D("82.48") for n in nuevas)


def test_4e_dia_de_una_liquidacion_ANULADA_no_se_cobra_dos_veces(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    liqs = _liqs(client, h)
    print("\n===== 4e. LIQUIDACIÓN ANULADA =====")
    for liq in liqs:
        r = client.post(f"{LIQUIDACIONES}/{liq['id']}/anular", headers=h)
        assert r.status_code == 200, r.text
        print(f"  anulada {liq['tipo']}: estado={r.json()['estado']}")
    nuevas = _liqs(client, h)
    for n in nuevas:
        d = client.get(f"{LIQUIDACIONES}/{n['id']}", headers=h).json()
        suma = sum(D(x["valor"]) for x in d["detalles"])
        print(f"  regenerada {d['tipo']:<14} litros={d['total_litros']}"
              f" total={d['valor_total']} suma={suma}")
        assert D(d["total_litros"]) == D("44.23") + D("82.48") + D("126.71")
        assert suma == D(d["valor_total"])


def test_4f_un_dia_no_se_puede_apagar_DESPUES_de_pagado(client, base_datos, db_session):
    """El candado del `estado`: con plata entregada, apagar el día rebota."""
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    liqs = _liqs(client, h)
    for liq in liqs:
        _post(client, h, f"{LIQUIDACIONES}/{liq['id']}/aprobar", {})
        _post(client, h, f"{LIQUIDACIONES}/{liq['id']}/pagar", {})

    print("\n===== 4f. APAGAR UN DÍA YA PAGADO =====")
    r = client.put(f"{RECEPCIONES}/{ids['2026-06-02']}", json={"estado": "inactivo"}, headers=h)
    print(f"  PUT estado=inactivo -> {r.status_code}")
    print(f"  {_detalle(r)}")
    assert r.status_code in (400, 409, 422), "se dejó apagar un día pagado"
    fila = db_session.get(RecepcionLeche, U(ids["2026-06-02"]))
    db_session.refresh(fila)
    assert fila.estado == "activo"

    r2 = client.delete(f"{RECEPCIONES}/{ids['2026-06-02']}", headers=h)
    print(f"  DELETE -> {r2.status_code}")
    assert r2.status_code in (400, 409, 422)

    for liq in liqs:
        d = client.get(f"{LIQUIDACIONES}/{liq['id']}", headers=h).json()
        suma = sum(D(x["valor"]) for x in d["detalles"])
        print(f"  {d['tipo']:<14} estado={d['estado']:<8} total={d['valor_total']} suma={suma}")
        assert suma == D(d["valor_total"])
        assert D(d["pagado"]) + D(d["saldo"]) == D(d["valor_total"]) - D(d["anticipos"])


def test_4g_un_dia_apagado_por_fuera_del_API_no_descuadra_la_PAGADA(
    client, base_datos, db_session
):
    """Por si se apaga por fuera del API: la pagada conserva su cifra y no se
    puede recalcular."""
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    liqs = _liqs(client, h)
    for liq in liqs:
        _post(client, h, f"{LIQUIDACIONES}/{liq['id']}/aprobar", {})
        _post(client, h, f"{LIQUIDACIONES}/{liq['id']}/pagar", {})

    fila = db_session.get(RecepcionLeche, U(ids["2026-06-02"]))
    fila.estado = "inactivo"
    db_session.flush()
    print("\n===== 4g. APAGADO POR FUERA DEL API, LIQUIDACIÓN PAGADA =====")
    for liq in liqs:
        d = client.get(f"{LIQUIDACIONES}/{liq['id']}", headers=h).json()
        suma = sum(D(x["valor"]) for x in d["detalles"])
        print(f"  {d['tipo']:<14} total={d['valor_total']} suma renglones={suma}"
              f" renglones={len(d['detalles'])}")
        assert suma == D(d["valor_total"]), "el papel pagado dejó de cuadrar"
        r = client.post(f"{LIQUIDACIONES}/{liq['id']}/recalcular", headers=h)
        print(f"    recalcular -> {r.status_code}")
        assert r.status_code in (400, 409, 422)


def test_4h_prender_de_nuevo_el_dia_lo_devuelve_a_sus_dos_comprobantes(client, base_datos):
    """El camino de vuelta: apagar y volver a prender no pierde ni gana un peso."""
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    liqs = _liqs(client, h)
    antes = {x["tipo"]: (D(x["total_litros"]), D(x["valor_total"])) for x in liqs}

    client.put(f"{RECEPCIONES}/{ids['2026-06-02']}", json={"estado": "inactivo"}, headers=h)
    r = client.put(f"{RECEPCIONES}/{ids['2026-06-02']}", json={"estado": "activo"}, headers=h)
    assert r.status_code == 200, r.text

    print("\n===== 4h. APAGAR Y VOLVER A PRENDER =====")
    for liq in liqs:
        d = client.get(f"{LIQUIDACIONES}/{liq['id']}", headers=h).json()
        suma = sum(D(x["valor"]) for x in d["detalles"])
        print(f"  {d['tipo']:<14} antes={antes[d['tipo']]}  después="
              f"({d['total_litros']}, {d['valor_total']})  suma renglones={suma}")
        assert (D(d["total_litros"]), D(d["valor_total"])) == antes[d["tipo"]]
        assert suma == D(d["valor_total"])


def test_4i_en_todos_los_casos_cada_renglon_del_flete_cuadra_a_mano(client, base_datos):
    """LA REGLA DE ORO, renglón por renglón: litros x precio == valor, EXACTO.

    Se recorren los cuatro casos del bloque —apagar, borrar en suave, mover de
    período y anular— y en cada uno se hace la cuenta que el conductor hace con
    calculadora sobre CADA línea del comprobante, además de la suma contra el
    total. Con tarifa de $242,76 y litros de dos decimales, que es donde el doble
    redondeo muerde.
    """
    print("\n===== 4i. CADA RENGLÓN DEL FLETE CUADRA A MANO =====")
    for caso in ("apagar", "borrar", "mover", "anular"):
        h = auth_headers(client, "admin.a")
        _, trans, prov, ids = _quincena_completa(client, h, sufijo=f" {caso}")
        liqs = [x for x in _liqs(client, h)
                if x["transportador_id"] == trans["id"] or x["proveedor_id"] == prov["id"]]
        objetivo = ids["2026-06-02"]
        if caso == "apagar":
            client.put(f"{RECEPCIONES}/{objetivo}", json={"estado": "inactivo"}, headers=h)
        elif caso == "borrar":
            client.delete(f"{RECEPCIONES}/{objetivo}", headers=h)
        elif caso == "mover":
            client.put(f"{RECEPCIONES}/{objetivo}", json={"fecha": "2026-06-20"}, headers=h)
        else:
            for liq in liqs:
                client.post(f"{LIQUIDACIONES}/{liq['id']}/anular", headers=h)
            liqs = [x for x in _liqs(client, h)
                    if x["transportador_id"] == trans["id"] or x["proveedor_id"] == prov["id"]]

        flete = [x for x in liqs if x["tipo"] == "transportador"][0]
        d = client.get(f"{LIQUIDACIONES}/{flete['id']}", headers=h).json()
        suma = D("0")
        print(f"  --- caso: {caso} ---")
        for x in d["detalles"]:
            a_mano = centavos(D(x["litros"]) * D(x["precio_litro"]))
            print(f"    {x['fecha']}  {x['litros']:>8} L x ${x['precio_litro']:<8}"
                  f" = ${x['valor']:>10}  (a mano ${a_mano})")
            assert D(x["valor"]) == a_mano, f"{caso}: el renglón {x['fecha']} no cuadra"
            assert cabe_en_la_columna(x["litros"]) and cabe_en_la_columna(x["precio_litro"])
            suma += D(x["valor"])
        print(f"    suma={suma}  total={d['valor_transporte']}")
        assert suma == D(d["valor_transporte"]) == D(d["valor_total"])


# ===========================================================================
# BLOQUE 5 — CONTABILIDAD, GRILLA, RESUMEN, TABLERO Y ALERTAS
# ===========================================================================
def test_5a_grilla_y_comprobantes_cuentan_lo_mismo(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    client.put(f"{RECEPCIONES}/{ids['2026-06-02']}", json={"estado": "inactivo"}, headers=h)
    liqs = _liqs(client, h)

    grilla = client.get(f"{RECEPCIONES}/grilla/quincena",
                        params={"desde": "2026-06-01", "hasta": "2026-06-15"}, headers=h).json()
    prov_liq = [x for x in liqs if x["tipo"] == "proveedor"][0]
    trans_liq = [x for x in liqs if x["tipo"] == "transportador"][0]

    print("\n===== 5a. GRILLA vs COMPROBANTES =====")
    print(f"  grilla            litros={grilla['total_litros']}"
          f" neto={grilla['total_valor_neto']} transporte={grilla['total_transporte']}")
    print(f"  liq.proveedor     litros={prov_liq['total_litros']} total={prov_liq['valor_total']}")
    print(f"  liq.transportador litros={trans_liq['total_litros']} total={trans_liq['valor_total']}")
    assert D(grilla["total_litros"]) == D(prov_liq["total_litros"]) == D(trans_liq["total_litros"])
    assert D(grilla["total_valor_neto"]) == D(prov_liq["valor_total"])
    assert D(grilla["total_transporte"]) == D(trans_liq["valor_total"])


def test_5b_contabilidad_cuenta_lo_mismo_que_el_comprobante(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    client.put(f"{RECEPCIONES}/{ids['2026-06-02']}", json={"estado": "inactivo"}, headers=h)
    liqs = _liqs(client, h)
    prov_liq = [x for x in liqs if x["tipo"] == "proveedor"][0]
    trans_liq = [x for x in liqs if x["tipo"] == "transportador"][0]

    er = client.get("/api/v1/contabilidad/estado-resultados",
                    params={"desde": "2026-06-01", "hasta": "2026-06-15"}, headers=h)
    assert er.status_code == 200, er.text
    er = er.json()
    print("\n===== 5b. CONTABILIDAD vs COMPROBANTES =====")
    print(f"  costo_leche      = {er['costo_leche']}     (liq. productor {prov_liq['valor_total']})")
    print(f"  costo_transporte = {er['costo_transporte']} (liq. conductor {trans_liq['valor_total']})")
    assert D(er["costo_leche"]) == D(prov_liq["valor_total"])
    assert D(er["costo_transporte"]) == D(trans_liq["valor_total"])


def test_5c_el_resumen_del_periodo_NO_cuenta_el_dia_apagado(client, base_datos):
    """`RecepcionRepository.resumen_por_dia` (GET /recepciones/resumen/periodo).

    La grilla, la contabilidad y los dos comprobantes filtran estado='activo'; este
    resumen no lo hacía, así que mostraba litros y plata de un día que la quesera
    decidió no contar: 253,42 L y $61.520,23 de flete contra 170,94 L y $41.497,39
    de todas las demás pantallas. Son 82,48 L de diferencia en una quincena de un
    SOLO proveedor, y el dueño cuadra estas dos pantallas a mano.
    """
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    client.put(f"{RECEPCIONES}/{ids['2026-06-02']}", json={"estado": "inactivo"}, headers=h)
    liqs = _liqs(client, h)
    trans_liq = [x for x in liqs if x["tipo"] == "transportador"][0]

    resumen = client.get(f"{RECEPCIONES}/resumen/periodo",
                         params={"desde": "2026-06-01", "hasta": "2026-06-15"}, headers=h).json()
    grilla = client.get(f"{RECEPCIONES}/grilla/quincena",
                        params={"desde": "2026-06-01", "hasta": "2026-06-15"}, headers=h).json()
    activos = D("44.23") + D("126.71")
    print("\n===== 5c. EL RESUMEN DEL PERÍODO NO CUENTA EL DÍA APAGADO =====")
    print(f"  litros activos (44,23 + 126,71) = {activos}")
    print(f"  grilla                          = {grilla['total_litros']}")
    print(f"  resumen/periodo                 = {resumen['total_litros']}")
    print(f"  días que lista el resumen       = {[d['fecha'] for d in resumen['dias']]}")
    print(f"  transporte del resumen          = {resumen['valor_transporte']}"
          f"  (grilla {grilla['total_transporte']}, comprobante {trans_liq['valor_total']})")
    assert D(grilla["total_litros"]) == activos
    assert D(resumen["total_litros"]) == activos, "el resumen cuenta el día apagado"
    assert "2026-06-02" not in [d["fecha"] for d in resumen["dias"]]
    assert D(resumen["valor_transporte"]) == D(grilla["total_transporte"])
    assert D(resumen["valor_transporte"]) == D(trans_liq["valor_total"])
    # Y el desglose por día suma el total del período, como todo desglose.
    assert sum(D(d["total_litros"]) for d in resumen["dias"]) == D(resumen["total_litros"])
    assert sum(D(d["valor_neto"]) for d in resumen["dias"]) == D(resumen["valor_neto"])


def test_5d_el_tablero_NO_cuenta_el_dia_apagado(client, base_datos):
    """`ReporteService.dashboard`: sus SEIS consultas de recepciones.

    Litros de hoy, litros de la quincena, valor de la leche, la quincena anterior
    del comparativo, la serie de 30 días y el top de proveedores incluían los días
    apagados. Es la PRIMERA pantalla que ve el dueño, y le decía 100 litros de una
    leche que contabilidad y las dos liquidaciones cuentan en cero.
    """
    h = auth_headers(client, "admin.a")
    ruta, trans, prov = _escenario(client, h)
    hoy = date.today().isoformat()
    a = _post(client, h, RECEPCIONES, {
        "fecha": hoy, "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "100"})

    prendido = client.get("/api/v1/reportes/dashboard", headers=h).json()
    r = client.put(f"{RECEPCIONES}/{a['id']}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    tablero = client.get("/api/v1/reportes/dashboard", headers=h)
    assert tablero.status_code == 200, tablero.text
    tablero = tablero.json()

    print("\n===== 5d. EL TABLERO NO CUENTA EL DÍA APAGADO =====")
    print("  el único día del período (100 L) se apaga")
    for campo in ("litros_hoy", "litros_quincena", "valor_leche_quincena"):
        print(f"  {campo:<22} prendido={prendido[campo]:>10} apagado={tablero[campo]}")
        assert D(prendido[campo]) > 0, "el tablero no está contando el día prendido"
        assert D(tablero[campo]) == D("0"), f"{campo} sigue contando el día apagado"
    print(f"  serie de 30 días       prendido={len(prendido['litros_por_dia'])} punto(s)"
          f" apagado={len(tablero['litros_por_dia'])}")
    print(f"  top_proveedores        prendido="
          f"{[(x['etiqueta'], x['valor']) for x in prendido['top_proveedores']]}"
          f" apagado={[(x['etiqueta'], x['valor']) for x in tablero['top_proveedores']]}")
    assert tablero["litros_por_dia"] == []
    assert tablero["top_proveedores"] == []

    # Y al volver a prenderlo el tablero vuelve a contarlo: no se perdió nada.
    client.put(f"{RECEPCIONES}/{a['id']}", json={"estado": "activo"}, headers=h)
    otra_vez = client.get("/api/v1/reportes/dashboard", headers=h).json()
    print(f"  al volver a prender    litros_hoy={otra_vez['litros_hoy']}")
    assert D(otra_vez["litros_hoy"]) == D("100")


def test_5e_la_casilla_del_dia_apagado_queda_libre(client, base_datos, db_session):
    """`existe_registro_dia`: la casilla (proveedor, fecha) la reserva SOLO un día
    que cuenta.

    Sin el filtro de estado, un día apagado seguía reservando la casilla: no había
    forma de anotar la leche buena de ese día (409 "Edite el registro existente", y
    el registro existente es justo el que se decidió no contar). La casilla quedaba
    muerta, porque el apagado no entra en ninguna liquidación.

    Y NO SE ABRE NINGUNA PUERTA A CONTAR DOS VECES, que es de lo que protege la
    regla: dos días ACTIVOS en la misma casilla siguen siendo imposibles.
    """
    h = auth_headers(client, "admin.a")
    _, trans, prov, ids = _quincena_completa(client, h)
    viejo = ids["2026-06-02"]
    client.put(f"{RECEPCIONES}/{viejo}", json={"estado": "inactivo"}, headers=h)

    print("\n===== 5e. LA CASILLA DEL DÍA APAGADO QUEDA LIBRE =====")
    r = client.post(RECEPCIONES, json={
        "fecha": "2026-06-02", "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "90"}, headers=h)
    print(f"  POST del mismo (proveedor, 2026-06-02) -> {r.status_code}  {_detalle(r)[:70]}")
    assert r.status_code == 201, "la casilla del día apagado sigue reservada"
    nuevo = r.json()["id"]

    # La leche que cuenta es la del día nuevo, una sola vez.
    grilla = client.get(f"{RECEPCIONES}/grilla/quincena",
                        params={"desde": "2026-06-01", "hasta": "2026-06-15"}, headers=h).json()
    esperado = D("44.23") + D("90") + D("126.71")
    print(f"  grilla litros = {grilla['total_litros']}  (44,23 + 90 + 126,71 = {esperado})")
    assert D(grilla["total_litros"]) == esperado
    liqs = _liqs(client, h)
    for liq in liqs:
        d = client.get(f"{LIQUIDACIONES}/{liq['id']}", headers=h).json()
        renglones = sorted(x["fecha"] for x in d["detalles"])
        suma = sum(D(x["valor"]) for x in d["detalles"])
        print(f"  {d['tipo']:<14} litros={d['total_litros']} días={renglones} suma={suma}")
        assert D(d["total_litros"]) == esperado
        assert renglones.count("2026-06-02") == 1, "el día se está cobrando dos veces"
        assert suma == D(d["valor_total"])

    # Volver a prender el viejo, con la casilla ya ocupada por el nuevo, REBOTA.
    r2 = client.put(f"{RECEPCIONES}/{viejo}", json={"estado": "activo"}, headers=h)
    print(f"  volver a prender el día viejo -> {r2.status_code}  {_detalle(r2)[:70]}")
    assert r2.status_code == 409, "quedaron dos días activos del mismo proveedor y fecha"
    fila = db_session.get(RecepcionLeche, U(viejo))
    db_session.refresh(fila)
    assert fila.estado == "inactivo"
    assert nuevo != viejo


def test_5f_la_alerta_de_sin_liquidar_no_se_queda_prendida_por_un_dia_apagado(
    client, base_datos
):
    """`_alertas_sin_liquidar` no miraba el estado.

    Un día apagado no entra en ninguna liquidación —sale de los dos comprobantes a
    propósito—, así que su `liquidacion_id` se queda en nulo PARA SIEMPRE y la
    alerta seguía diciendo "hay recepciones de hace más de 20 días sin liquidar" de
    algo que jamás va a liquidarse. La única salida era volver a prender el día y
    pagarlo, o sea que la alerta empujaba a cobrar leche que la quesera decidió no
    contar. Una alerta que no se puede apagar es una alerta que el dueño ignora, y
    con ella las demás.
    """
    h = auth_headers(client, "admin.a")
    _, trans, prov = _escenario(client, h)
    vieja = (date.today() - timedelta(days=40)).isoformat()
    rec = _post(client, h, RECEPCIONES, {
        "fecha": vieja, "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "120"})

    print("\n===== 5f. LA ALERTA DE 'SIN LIQUIDAR' SE PUEDE APAGAR =====")
    clave = "proveedores_sin_liquidar"
    con_dia = _post(client, h, f"{NOTIFICACIONES}/generar-alertas", {})
    print(f"  con el día prendido: {clave}={con_dia['detalle'][clave]}")
    assert con_dia["detalle"][clave] == 1, "la alerta no se está emitiendo"

    # Se apaga el día y se limpian las notificaciones ya emitidas, para volver a
    # correr la regla desde cero (`_emitir` no repite una alerta pendiente).
    client.put(f"{RECEPCIONES}/{rec['id']}", json={"estado": "inactivo"}, headers=h)
    client.post(f"{NOTIFICACIONES}/leer-todas", headers=h)
    apagado = _post(client, h, f"{NOTIFICACIONES}/generar-alertas", {})
    print(f"  con el día apagado : {clave}={apagado['detalle'][clave]}")
    assert apagado["detalle"][clave] == 0, \
        "la alerta se queda prendida sobre un día que nunca se va a liquidar"


# ===========================================================================
# BLOQUE 6 — EL PAPEL: LOS FORMATEADORES DEL PDF
# ===========================================================================
def test_6a_el_pdf_redondea_el_medio_centavo_PARA_ARRIBA():
    """Los formateadores usaban el redondeo POR OMISIÓN de Python, que es el del
    banquero (el medio va al dígito par), y no el medio para arriba del resto del
    proyecto.

    El PDF es el papel que se le entrega a un tercero —al productor, al conductor,
    al cliente— y que después se compara contra la pantalla. `pesos(1800.005)`
    imprimía $1.800,00 cuando la columna guarda $1.800,01: si el papel dice una
    cifra distinta de la pantalla, la discusión la pierde el dueño.
    """
    from app.utils.export import kilogramos, litros, pesos

    print("\n===== 6a. EL PDF REDONDEA MEDIO PARA ARRIBA =====")
    casos_pesos = [
        ("2.505", "$2,51"),      # el banquero daba $2,50
        ("2.515", "$2,52"),
        ("1800.005", "$1.800,01"),  # el banquero daba $1.800,00
        ("2.5049", "$2,50"),
        ("-2.505", "-$2,51"),
        ("18525000", "$18.525.000"),
        ("1950050.50", "$1.950.050,50"),
    ]
    for valor, esperado in casos_pesos:
        salida = pesos(D(valor))
        banquero = D(valor).quantize(D("0.01"))
        print(f"  pesos({valor:>12}) = {salida:>16}   (banquero: {banquero})")
        assert salida == esperado

    for valor, esperado in [("44.235", "44,24 L"), ("227.5", "227,5 L"), ("250", "250 L")]:
        print(f"  litros({valor:>8}) = {litros(D(valor))}")
        assert litros(D(valor)) == esperado
    for valor, esperado in [("10.005", "10,01 kg"), ("10.34", "10,34 kg"), ("100", "100 kg")]:
        print(f"  kilogramos({valor:>8}) = {kilogramos(D(valor))}")
        assert kilogramos(D(valor)) == esperado


def test_6b_el_pdf_imprime_la_misma_cifra_que_guarda_la_columna(client, base_datos, db_session):
    """La prueba de fuego del 6a, sobre el comprobante de verdad: la cifra impresa
    tiene que ser la de la columna, carácter por carácter."""
    from app.utils.export import litros as fmt_litros, pesos

    h = auth_headers(client, "admin.a")
    _, trans, prov = _escenario(client, h)
    rec = _post(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "44.235"})
    gen = _post(client, h, f"{LIQUIDACIONES}/generar", {
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "transportador"})
    liq_id = gen[0]["id"]
    d = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    fila = db_session.get(RecepcionLeche, U(rec["id"]))

    pdf = client.get(f"{LIQUIDACIONES}/{liq_id}/pdf", headers=h)
    assert pdf.status_code == 200, pdf.text
    print("\n===== 6b. EL PAPEL DICE LO MISMO QUE LA COLUMNA =====")
    print(f"  columna: {fila.cantidad_litros} L x ${fila.valor_transporte}")
    print(f"  renglón del comprobante: {d['detalles'][0]['litros']} L"
          f" x ${d['detalles'][0]['precio_litro']} = ${d['detalles'][0]['valor']}")
    print(f"  impreso: {fmt_litros(D(d['detalles'][0]['litros']))}"
          f"  {pesos(D(d['detalles'][0]['valor']))}   ({len(pdf.content)} bytes de PDF)")
    assert D(d["detalles"][0]["litros"]) == D(fila.cantidad_litros) == D("44.24")
    assert D(d["detalles"][0]["valor"]) == D(fila.valor_transporte)
    assert fmt_litros(D(d["detalles"][0]["litros"])) == "44,24 L"
    assert pesos(D(d["detalles"][0]["valor"])) == pesos(D(fila.valor_transporte))
