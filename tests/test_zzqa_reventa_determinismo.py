"""FRENTE 1: QUE NO TIEMBLE.

El reparto FIFO decide a QUÉ productor se le consumen los kilos de una venta, y
con eso a quién se le carga el costo y cuánta ganancia le queda. Si ese orden
depende de algo que cambia entre corridas —como un UUID aleatorio—, no hay línea
base: no se puede demostrar que un cambio no movió una cifra.

Estas pruebas exigen el MISMO resultado AL CENTAVO, y lo exigen de dos maneras
distintas, porque una sola no alcanza:

- REPETIR LA LECTURA sobre los mismos datos (20+ veces). Atrapa cualquier orden
  que la base pueda devolver distinto entre dos consultas iguales.
- REHACER LOS DATOS desde cero muchas veces (20+ corridas, cada una con su propia
  base y sus propios UUID nuevos) y exigir el mismo informe. ESTA ES LA QUE
  IMPORTA: los ids cambian en cada corrida, así que si el desempate del orden
  todavía dependiera del `id`, las cifras se moverían entre corridas. Repetir la
  lectura sobre una base ya escrita NUNCA lo habría detectado.

Y encima se corre el caso feo a propósito: TODAS las filas con la MISMA hora de
registro y el mismo renglón, que es como quedan las filas cargadas de una
migración o de una importación. Ahí el desempate cae en las columnas del negocio,
y es justo donde antes decidía la suerte.
"""
import json
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import auth_headers

API = "/api/v1/reventa"
PERIODO = {"desde": "2026-01-01", "hasta": "2026-12-31"}
CERO = Decimal("0")
CORRIDAS = 22  # "veinte o más"

# La hora con la que se aplastan los empates: el mismo instante para todas las
# filas, como quedan las de una migración.
HORA_APLASTADA = "2026-03-01 10:00:00.000000"


def D(v):
    return Decimal(str(v))


# ------------------------------------------------------------------ escenarios
def _compra(client, h, fecha, productor, kilos, precio, borona=0):
    r = client.post(
        f"{API}/compras",
        json={"fecha": fecha, "productor": productor, "kilos_brutos": kilos,
              "precio_kilo": precio, "borona_kilos": borona},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _venta(client, h, fecha, cliente, kilos, precio, gasto=0, tipo="queso"):
    r = client.post(
        f"{API}/ventas",
        json={"fecha": fecha, "cliente": cliente, "tipo": tipo, "kilos": kilos,
              "precio_kilo": precio, "gasto_por_kilo": gasto},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _factura_compra(client, h, fecha, productor, renglones):
    r = client.post(
        f"{API}/documentos",
        json={"tipo": "compra", "fecha": fecha, "tercero": productor,
              "renglones": renglones},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ajuste(client, h, fecha, kilos, destino):
    r = client.post(
        f"{API}/conversiones",
        json={"fecha": fecha, "kilos": kilos, "destino": destino},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def esc_dos_compras_mismo_dia_distintos(client, h):
    """DOS compras del mismo día, de DISTINTOS productores, y una venta que se
    lleva kilos de las dos. Es el caso exacto de la prueba que temblaba."""
    _compra(client, h, "2026-03-01", "Patricia", 200, 1000)
    _compra(client, h, "2026-03-01", "Sebastian", 200, 1500)
    _venta(client, h, "2026-03-05", "Don Jose", 250, 2200, gasto=50)


def esc_tres_compras_mismo_dia_distintos(client, h):
    """TRES del mismo día, tres productores, y dos ventas que las atraviesan."""
    _compra(client, h, "2026-03-01", "Patricia", 150, 1000)
    _compra(client, h, "2026-03-01", "Sebastian", 150, 1300)
    _compra(client, h, "2026-03-01", "Aurelio", 150, 1700)
    _venta(client, h, "2026-03-04", "Don Jose", 200, 2100, gasto=40)
    _venta(client, h, "2026-03-06", "Doña Rosa", 175, 2400, gasto=25)


def esc_dos_compras_mismo_dia_mismo_productor(client, h):
    """DOS del mismo día del MISMO productor a precios distintos: el orden decide
    cuál de los dos precios se le carga a la venta."""
    _compra(client, h, "2026-03-01", "Patricia", 100, 1000)
    _compra(client, h, "2026-03-01", "Patricia", 100, 2000)
    _venta(client, h, "2026-03-05", "Don Jose", 120, 3000, gasto=10)


def esc_tres_compras_mismo_dia_mismo_productor(client, h):
    _compra(client, h, "2026-03-01", "Patricia", 90, 1100)
    _compra(client, h, "2026-03-01", "Patricia", 80, 1300)
    _compra(client, h, "2026-03-01", "Patricia", 70, 1900)
    _venta(client, h, "2026-03-07", "Don Jose", 210, 2600, gasto=33)


def esc_renglones_de_la_misma_factura(client, h):
    """UNA factura con tres renglones (los tres del mismo día y de la misma hora):
    el desempate lo tiene que hacer el renglón, no la suerte."""
    _factura_compra(client, h, "2026-03-01", "Patricia", [
        {"kilos_brutos": 100, "precio_kilo": 1000},
        {"kilos_brutos": 100, "precio_kilo": 2000},
        {"kilos_brutos": 100, "precio_kilo": 3000},
    ])
    _venta(client, h, "2026-03-05", "Don Jose", 250, 3500, gasto=20)


def esc_dos_facturas_de_varios_renglones(client, h):
    """Dos facturas del MISMO día, cada una de dos renglones, de dos productores."""
    _factura_compra(client, h, "2026-03-01", "Patricia", [
        {"kilos_brutos": 60, "precio_kilo": 1000},
        {"kilos_brutos": 40, "precio_kilo": 1100},
    ])
    _factura_compra(client, h, "2026-03-01", "Sebastian", [
        {"kilos_brutos": 50, "precio_kilo": 1400},
        {"kilos_brutos": 30, "precio_kilo": 1500},
    ])
    _venta(client, h, "2026-03-08", "Don Jose", 150, 2500, gasto=15)


def esc_compras_del_mismo_dia_con_venta_en_medio(client, h):
    """Compras y ventas EL MISMO DÍA, con la venta registrada en medio de las dos
    compras. El reparto pone las compras del día antes de las ventas del día (se
    compra en la mañana y se despacha en la tarde), así que la venta tiene que
    poder tomar de las dos."""
    _compra(client, h, "2026-03-01", "Patricia", 100, 1000)
    _venta(client, h, "2026-03-01", "Don Jose", 80, 2000, gasto=30)
    _compra(client, h, "2026-03-01", "Sebastian", 100, 1600)
    # Esta se lleva lo que quedó de Patricia Y parte de Sebastián: es la que hace
    # que el orden de las dos compras del día decida a quién se le carga el costo.
    _venta(client, h, "2026-03-01", "Doña Rosa", 60, 2500, gasto=12)


def esc_compras_del_mismo_dia_sin_ventas(client, h):
    """Mismo día, sin una sola venta: todo queda en inventario y el costo de lo no
    vendido tiene que quedar igual en cada corrida."""
    _compra(client, h, "2026-03-01", "Patricia", 120, 1000, borona=7)
    _compra(client, h, "2026-03-01", "Sebastian", 130, 1450, borona=3)
    _compra(client, h, "2026-03-01", "Aurelio", 140, 1900)


def esc_gemelas(client, h):
    """DOS COMPRAS IDÉNTICAS del mismo día: mismo productor, mismos kilos, mismo
    precio. Aquí el desempate no puede salir de ninguna columna del negocio, así
    que lo decide el `id`. La afirmación que hay que probar es la que justifica
    eso: consumir una o la otra da EXACTAMENTE las mismas cifras."""
    _compra(client, h, "2026-03-01", "Patricia", 100, 1500)
    _compra(client, h, "2026-03-01", "Patricia", 100, 1500)
    _venta(client, h, "2026-03-05", "Don Jose", 150, 2400, gasto=17)


def esc_con_ajustes_del_mismo_dia(client, h):
    """Borona y merma el MISMO día, más borona que llega con el lote y se vende."""
    _compra(client, h, "2026-03-01", "Patricia", 200, 1000, borona=10)
    _compra(client, h, "2026-03-01", "Sebastian", 100, 1800, borona=5)
    _ajuste(client, h, "2026-03-03", 12, "borona")
    _ajuste(client, h, "2026-03-03", 8, "merma")
    _venta(client, h, "2026-03-04", "Don Jose", 150, 2300, gasto=20)
    _venta(client, h, "2026-03-04", "Doña Rosa", 15, 900, tipo="borona")


def esc_reparto_con_centavos(client, h):
    """Kilos y precios que NO parten en dos: el residuo del redondeo tiene que
    caer siempre en el mismo trozo, o la columna deja de sumar."""
    _compra(client, h, "2026-03-01", "Patricia", "33.33", "1111.11")
    _compra(client, h, "2026-03-01", "Sebastian", "66.67", "2222.22")
    _compra(client, h, "2026-03-01", "Aurelio", "0.01", "9999.99")
    _venta(client, h, "2026-03-05", "Don Jose", "77.77", "3333.33", gasto="7.77")


def esc_vendio_antes_de_comprar(client, h):
    """Se vendió ANTES de la primera compra: esos kilos no tienen lote de dónde
    salir y su plata se va al aviso (`kilos_sin_lote` / `ingreso_sin_lote`).

    El guardia de inventario no mira fechas —compara contra el disponible total—,
    así que esta venta pasa y el reparto sí tiene que decidir qué hacer con ella.
    Ese reparto tampoco puede temblar."""
    _compra(client, h, "2026-03-10", "Patricia", 100, 1000)
    _compra(client, h, "2026-03-10", "Sebastian", 50, 1400)
    _venta(client, h, "2026-03-01", "Don Jose", 60, 2000, gasto=10)
    _venta(client, h, "2026-03-12", "Doña Rosa", 80, 2100, gasto=5)


ESCENARIOS = {
    "dos_compras_mismo_dia_distintos": esc_dos_compras_mismo_dia_distintos,
    "tres_compras_mismo_dia_distintos": esc_tres_compras_mismo_dia_distintos,
    "dos_compras_mismo_dia_mismo_productor": esc_dos_compras_mismo_dia_mismo_productor,
    "tres_compras_mismo_dia_mismo_productor": esc_tres_compras_mismo_dia_mismo_productor,
    "renglones_de_la_misma_factura": esc_renglones_de_la_misma_factura,
    "dos_facturas_de_varios_renglones": esc_dos_facturas_de_varios_renglones,
    "mismo_dia_con_venta_en_medio": esc_compras_del_mismo_dia_con_venta_en_medio,
    "mismo_dia_sin_ventas": esc_compras_del_mismo_dia_sin_ventas,
    "gemelas": esc_gemelas,
    "con_ajustes_del_mismo_dia": esc_con_ajustes_del_mismo_dia,
    "reparto_con_centavos": esc_reparto_con_centavos,
    "vendio_antes_de_comprar": esc_vendio_antes_de_comprar,
}


# -------------------------------------------------------------------- lecturas
def informe(client, h) -> dict:
    """TODO lo que sale del reparto FIFO, junto: el panel de lotes (con el detalle
    por productor y por venta), la ganancia por día y el resumen del período. No
    hay ni un id ni una hora en estas respuestas, así que se pueden comparar tal
    cual entre dos bases distintas."""
    lotes = client.get(f"{API}/lotes", headers=h)
    assert lotes.status_code == 200, lotes.text
    dia = client.get(f"{API}/ganancia-por-dia", params=PERIODO, headers=h)
    assert dia.status_code == 200, dia.text
    res = client.get(f"{API}/resumen", params=PERIODO, headers=h)
    assert res.status_code == 200, res.text
    return {"lotes": lotes.json(), "por_dia": dia.json(), "resumen": res.json()}


def huella(informe_dict) -> str:
    return json.dumps(informe_dict, sort_keys=True, ensure_ascii=False)


def sin_lo_volatil(cuerpo):
    """La misma respuesta sin lo que TIENE que cambiar entre dos bases: los ids y
    las horas de registro. Lo que queda es el ORDEN de los renglones y sus cifras,
    que es justamente lo que el tercero compara contra el papel que ya recibió."""
    if isinstance(cuerpo, dict):
        return {
            k: sin_lo_volatil(v)
            for k, v in cuerpo.items()
            if k not in ("id", "compra_id", "venta_id", "documento_id", "empresa_id",
                         "created_at", "updated_at", "created_by", "updated_by",
                         "saldo_anterior_id")
        }
    if isinstance(cuerpo, list):
        return [sin_lo_volatil(x) for x in cuerpo]
    return cuerpo


def estados_de_cuenta(client, h, productores, clientes) -> dict:
    """El estado de cuenta de cada tercero, que es un PAPEL QUE SE LE ENTREGA. Si
    dos consultas iguales le cambian el orden de los renglones, él lo nota: está
    comparando contra el que recibió la quincena pasada."""
    salida = {}
    # Un tercero que no tiene movimientos en este escenario responde 404, y eso
    # también es una respuesta que tiene que ser la misma en cada corrida.
    for ruta, parametro, nombres in (
        ("estado-cuenta-productor", "productor", productores),
        ("estado-cuenta", "cliente", clientes),
    ):
        for nombre in nombres:
            r = client.get(f"{API}/{ruta}", params={parametro: nombre}, headers=h)
            assert r.status_code in (200, 404), r.text
            salida[f"{parametro}:{nombre}"] = (
                "sin movimientos" if r.status_code == 404 else sin_lo_volatil(r.json())
            )
    return salida


def aplastar_las_horas(db_session):
    """Deja TODAS las filas con la misma hora de registro y el mismo renglón, que
    es como quedan las filas de una migración o de una importación. Con eso el
    desempate del orden cae entero en las columnas del negocio."""
    for sql in (
        "UPDATE compras_queso SET created_at = :h, orden = 0",
        "UPDATE ventas_queso SET created_at = :h, orden = 0",
        "UPDATE conversiones_borona SET created_at = :h",
    ):
        db_session.execute(text(sql), {"h": HORA_APLASTADA})
    db_session.commit()
    # Que el aplastón SÍ ocurrió: sin esto la prueba podría estar pasando sin haber
    # empatado nada, que es la forma más fácil de creerse una garantía que no se tiene.
    distintas = db_session.execute(
        text("SELECT COUNT(DISTINCT created_at) FROM compras_queso")
    ).scalar()
    assert distintas == 1, (
        f"las compras quedaron con {distintas} horas distintas: el empate que esta "
        f"prueba necesita no se produjo"
    )


def exigir_que_cada_compra_cuadre(informe_dict, etiqueta):
    """LA REGLA DE ORO en el panel de lotes: cada peso pagado por una compra
    termina en exactamente uno de dos sitios (lo que ya salió, o lo que sigue en
    inventario), y las cifras del lote son la suma de las de sus compras."""
    for lote in informe_dict["lotes"]["lotes"]:
        suma_valor = CERO
        suma_gan = CERO
        for c in lote["detalle_compras"]:
            repartido = D(c["costo_realizado"]) + D(c["costo_sin_vender"])
            assert repartido == D(c["valor_total"]), (
                f"{etiqueta}: al productor {c['productor']} del lote {lote['fecha']} "
                f"se le pagaron {c['valor_total']} y el reparto solo explica {repartido}"
            )
            suma_valor += D(c["valor_total"])
            suma_gan += D(c["ganancia"])
        assert suma_valor == D(lote["costo_total"]), (
            f"{etiqueta}: el lote {lote['fecha']} dice que costó {lote['costo_total']} "
            f"y sus compras suman {suma_valor}"
        )
        assert suma_gan == D(lote["ganancia"]), (
            f"{etiqueta}: el lote {lote['fecha']} dice ganancia {lote['ganancia']} "
            f"y sus compras suman {suma_gan}"
        )


# ------------------------------------------------------- 1) repetir la lectura
@pytest.mark.parametrize("nombre", list(ESCENARIOS))
def test_repetir_la_lectura_no_mueve_ni_un_centavo(client, base_datos, nombre):
    """La MISMA consulta sobre los MISMOS datos, 22 veces."""
    h = auth_headers(client, "admin.a")
    ESCENARIOS[nombre](client, h)

    primero = informe(client, h)
    exigir_que_cada_compra_cuadre(primero, nombre)
    esperada = huella(primero)
    for vuelta in range(2, CORRIDAS + 1):
        otra = huella(informe(client, h))
        assert otra == esperada, (
            f"{nombre}: la lectura número {vuelta} devolvió cifras distintas sobre "
            f"los mismos datos"
        )


# ------------------------------------------- 2) rehacer los datos desde cero
# La huella de la primera corrida de cada escenario. Las siguientes se comparan
# contra ella: cada corrida trae su propia base y sus propios UUID.
_huellas: dict[str, str] = {}


@pytest.mark.parametrize("corrida", range(1, CORRIDAS + 1))
@pytest.mark.parametrize("nombre", list(ESCENARIOS))
def test_rehacer_los_datos_da_el_mismo_informe(client, base_datos, nombre, corrida):
    h = auth_headers(client, "admin.a")
    ESCENARIOS[nombre](client, h)
    actual = informe(client, h)
    exigir_que_cada_compra_cuadre(actual, f"{nombre} corrida {corrida}")
    marca = huella(actual)
    if nombre not in _huellas:
        _huellas[nombre] = marca
        return
    assert marca == _huellas[nombre], (
        f"{nombre}: la corrida {corrida} —los mismos datos, escritos otra vez en una "
        f"base nueva con otros ids— dio un informe distinto. El orden del reparto "
        f"todavía depende de algo que cambia entre corridas."
    )


# --------------------------- 3) el caso feo: todas las filas con la misma hora
_huellas_empate: dict[str, str] = {}


@pytest.mark.parametrize("corrida", range(1, CORRIDAS + 1))
@pytest.mark.parametrize("nombre", list(ESCENARIOS))
def test_con_las_horas_aplastadas_sigue_dando_lo_mismo(
    client, db_session, base_datos, nombre, corrida
):
    """Todas las filas con la MISMA hora y el MISMO renglón, y aun así el mismo
    informe en 22 bases distintas. Es la prueba de que el `id` ya no decide."""
    h = auth_headers(client, "admin.a")
    ESCENARIOS[nombre](client, h)
    aplastar_las_horas(db_session)
    actual = informe(client, h)
    exigir_que_cada_compra_cuadre(actual, f"{nombre} empatado corrida {corrida}")
    marca = huella(actual)
    if nombre not in _huellas_empate:
        _huellas_empate[nombre] = marca
        return
    assert marca == _huellas_empate[nombre], (
        f"{nombre}: con todas las horas empatadas, la corrida {corrida} dio cifras "
        f"distintas. El desempate de último recurso todavía depende del UUID."
    )


# ------------------------- 4) el estado de cuenta que se le entrega al tercero
_huellas_cuenta: dict[str, str] = {}
TERCEROS = (("Patricia", "Sebastian", "Aurelio"), ("Don Jose", "Doña Rosa"))


@pytest.mark.parametrize("corrida", range(1, CORRIDAS + 1))
@pytest.mark.parametrize(
    "nombre",
    ["dos_compras_mismo_dia_mismo_productor", "renglones_de_la_misma_factura",
     "dos_facturas_de_varios_renglones", "gemelas", "con_ajustes_del_mismo_dia"],
)
def test_el_estado_de_cuenta_sale_siempre_en_el_mismo_orden(
    client, db_session, base_datos, nombre, corrida
):
    """El estado de cuenta del productor y del cliente, con las horas aplastadas y
    en 22 bases distintas. Es un papel que se le entrega a una persona: dos
    consultas iguales no le pueden cambiar el orden de los renglones."""
    h = auth_headers(client, "admin.a")
    ESCENARIOS[nombre](client, h)
    aplastar_las_horas(db_session)
    cuentas = estados_de_cuenta(client, h, *TERCEROS)
    # Y también repetida sobre la MISMA base, que es el otro lado de la moneda
    assert huella(cuentas) == huella(estados_de_cuenta(client, h, *TERCEROS)), (
        f"{nombre}: dos consultas seguidas del estado de cuenta dieron órdenes "
        f"distintos sobre los mismos datos"
    )
    marca = huella(cuentas)
    if nombre not in _huellas_cuenta:
        _huellas_cuenta[nombre] = marca
        return
    assert marca == _huellas_cuenta[nombre], (
        f"{nombre}: el estado de cuenta de la corrida {corrida} salió distinto"
    )
