"""EL COMPROBANTE DEL TRANSPORTADOR CON UN RENGLÓN POR DÍA Y RUTA.

Segunda mitad de lo que pidió el dueño: "ahora el transportador puede tener varias
rutas, por ejemplo este tuvo que hacer las dos... pero cada ruta puede tener un
valor diferente de litro por leche". La primera mitad (la tarifa por ruta y el
flete de cada día) ya está en test_transportador_rutas.py. Lo de acá es EL PAPEL
QUE FIRMA EL CONDUCTOR: si el renglón siguiera siendo el día, el día en que hizo
las dos rutas saldría un renglón con una sola tarifa y litros × precio no daría el
valor. El conductor suma esa columna a mano.

LA INVARIANTE, que es lo que se prueba y no se negocia:
  1. en cada renglón, litros × precio_litro == valor, EXACTO al centavo;
  2. la suma de los valores de los renglones == valor_transporte del comprobante;
  3. y ese total == la suma de los recepciones_leche.valor_transporte que entraron
     (las FOTOS que se guardaron el día que se recibió la leche).

LAS CIFRAS DEL CUADRE, escritas a mano acá y no calculadas por el código que se
está probando. Alex Agudelo, quincena del 16 al 31 de julio de 2026, cobrando
$242,76 el litro en Nápoles y $317,50 en Mira Valle (su tarifa GENERAL es $200 y
no tiene que aparecer en ninguna parte: solo aplica donde no hay ruta):

  fotos del flete, una por recepción
    16/07  Nápoles     Aurelio    82,00 L × $242,76 = $ 19.906,32
           (242,76 × 80 = 19.420,80  +  242,76 × 2 = 485,52)
    16/07  Nápoles     Marleny   137,45 L × $242,76 = $ 33.367,36   (33.367,362)
    16/07  Mira Valle  Gilberto   96,30 L × $317,50 = $ 30.575,25
    17/07  Nápoles     Aurelio    78,90 L × $242,76 = $ 19.153,76   (19.153,764)
    17/07  Nápoles     Marleny   141,10 L × $242,76 = $ 34.253,44   (34.253,436)
    18/07  Mira Valle  Gilberto  103,75 L × $317,50 = $ 32.940,63   (32.940,625)

  los CUATRO renglones del comprobante (día y ruta, en el orden en que se imprimen:
  por fecha y, dentro del día, por nombre de ruta)
    16/07  Mira Valle   96,30 L × $317,50 = $ 30.575,25
    16/07  Nápoles     219,45 L × $242,76 = $ 53.273,68   (19.906,32 + 33.367,36)
    17/07  Nápoles     220,00 L × $242,76 = $ 53.407,20   (19.153,76 + 34.253,44)
    18/07  Mira Valle  103,75 L × $317,50 = $ 32.940,63
                       -----------------------------------------------
                       639,50 L                        $170.196,76

Lo que hacía el código viejo con ese mismo 16 de julio: UN renglón de 315,75 L a la
tarifa del transportador ($200) = $63.150, contra $83.848,93 de flete de verdad ese
día. Casi $21.000 de diferencia en un día, y el renglón no se podía reproducir a
mano ni con calculadora.
"""
import importlib.util
import io
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models_registry  # noqa: F401  (registra todas las tablas en Base)
from app.core.database import Base
from app.modules.empresas.models import Empresa
from app.modules.liquidaciones.models import (
    TIPO_PROVEEDOR,
    TIPO_TRANSPORTADOR,
    Liquidacion,
    LiquidacionDetalle,
)
from app.modules.proveedores.models import Proveedor
from app.modules.recepcion.models import RecepcionLeche
from app.modules.rutas.models import Ruta
from app.modules.transportadores.models import Transportador
from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQUIDACIONES = "/api/v1/liquidaciones"

NAPOLES = Decimal("242.76")
MIRA_VALLE = Decimal("317.50")
GENERAL = Decimal("200")


def D(v):
    return Decimal(str(v))


def centavos(valor):
    """La misma regla del backend: al centavo, con el medio centavo para arriba."""
    return D(valor).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _crear(client, headers, url, payload):
    r = client.post(url, json=payload, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _escenario(client, h):
    """Alex Agudelo con las dos rutas a tarifas distintas y tres proveedores.

    La tarifa GENERAL queda en $200 a propósito, distinta de las dos de ruta: si el
    código se equivocara y volviera a leer la general (que es lo que hacía antes),
    las cifras saldrían disparatadas en vez de parecidas, y la prueba lo grita.
    """
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    mira_valle = _crear(client, h, RUTAS, {"nombre": "Mira Valle", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo",
        "valor_transporte": str(GENERAL),
        "rutas": [
            {"ruta_id": napoles["id"], "valor_transporte": str(NAPOLES)},
            {"ruta_id": mira_valle["id"], "valor_transporte": str(MIRA_VALLE)},
        ],
    })
    proveedores = {
        "Aurelio": _crear(client, h, PROVEEDORES, {
            "nombre": "Aurelio", "vereda": "Napoles", "precio_litro": "1800",
            "ruta_id": napoles["id"]}),
        "Marleny": _crear(client, h, PROVEEDORES, {
            "nombre": "Marleny", "vereda": "Napoles", "precio_litro": "1800",
            "ruta_id": napoles["id"]}),
        "Gilberto": _crear(client, h, PROVEEDORES, {
            "nombre": "Gilberto", "vereda": "Mira Valle", "precio_litro": "1800",
            "ruta_id": mira_valle["id"]}),
    }
    return {"napoles": napoles, "mira_valle": mira_valle, "alex": alex, "proveedores": proveedores}


def _recibir(client, h, escenario, fecha, proveedor, litros_):
    return _crear(client, h, RECEPCIONES, {
        "fecha": fecha,
        "proveedor_id": escenario["proveedores"][proveedor]["id"],
        "transportador_id": escenario["alex"]["id"],
        "cantidad_litros": litros_,
    })


def _liquidar_flete(client, h, inicio="2026-07-16", fin="2026-07-31"):
    generadas = client.post(
        f"{LIQUIDACIONES}/generar",
        json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": "transportador"},
        headers=h,
    )
    assert generadas.status_code in (200, 201), generadas.text
    assert generadas.json()["generadas"], "no se generó ninguna liquidación de flete"
    return generadas.json()["generadas"][0]


def _renglones(liq):
    """Los renglones en el orden en que se leen: por fecha y por nombre de ruta."""
    return sorted(liq["detalles"], key=lambda d: (d["fecha"], d["ruta_nombre"] or ""))


def _revisar_invariante(liq, fotos_esperadas=None):
    """Las tres partes de la invariante, de una vez. Devuelve la suma de renglones.

    Se usa en todas las pruebas de este archivo: cada escenario nuevo pasa por acá
    además de por sus propias cifras.
    """
    suma = D(0)
    for renglon in _renglones(liq):
        litros_ = D(renglon["litros"])
        precio = D(renglon["precio_litro"])
        valor = D(renglon["valor"])
        assert centavos(litros_ * precio) == valor, (
            f"el renglón {renglon['fecha']} / {renglon['ruta_nombre']} no cuadra: "
            f"{litros_} × {precio} = {centavos(litros_ * precio)} y dice {valor}"
        )
        suma += valor
    assert suma == D(liq["valor_transporte"]), "los renglones no suman el valor del flete"
    assert D(liq["valor_total"]) == D(liq["valor_transporte"])
    if fotos_esperadas is not None:
        assert suma == fotos_esperadas, "el total no es la suma de las fotos guardadas"
    return suma


# ---------------------------------------------------------------------------
# 1. EL CUADRE A MANO: el día en que hizo las dos rutas
# ---------------------------------------------------------------------------
def test_cuadre_a_mano_del_dia_en_que_hizo_las_dos_rutas(client, base_datos, db_session):
    """Las cifras del encabezado de este archivo, renglón por renglón.

      16/07  Mira Valle   96,30 L × $317,50 = $ 30.575,25
      16/07  Nápoles     219,45 L × $242,76 = $ 53.273,68
      17/07  Nápoles     220,00 L × $242,76 = $ 53.407,20
      18/07  Mira Valle  103,75 L × $317,50 = $ 32.940,63
                                              -----------
                         639,50 L             $170.196,76

    Y las tres partes de la invariante: cada renglón se reproduce con calculadora,
    los cuatro suman el total del comprobante, y ese total es exactamente la suma de
    los seis fletes que quedaron guardados en las recepciones.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)

    entregas = [
        ("2026-07-16", "Aurelio", "82"),
        ("2026-07-16", "Marleny", "137.45"),
        ("2026-07-16", "Gilberto", "96.30"),
        ("2026-07-17", "Aurelio", "78.90"),
        ("2026-07-17", "Marleny", "141.10"),
        ("2026-07-18", "Gilberto", "103.75"),
    ]
    fotos = D(0)
    print("\n===== 1. LAS FOTOS DEL FLETE (una por recepción) =====")
    for fecha, quien, litros_ in entregas:
        recepcion = _recibir(client, h, esc, fecha, quien, litros_)
        foto = D(recepcion["valor_transporte"])
        fotos += foto
        print(f"  {fecha}  {quien:<9}{litros_:>7} L  ->  ${foto:>10}")
    print(f"  suma de las fotos guardadas: ${fotos}")
    assert fotos == D("170196.76")

    liq = client.get(
        f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h
    ).json()

    # LAS CIFRAS ESCRITAS A MANO, en el orden en que se leen en el comprobante.
    esperados = [
        ("2026-07-16", "Mira Valle", D("96.30"), MIRA_VALLE, D("30575.25")),
        ("2026-07-16", "Napoles", D("219.45"), NAPOLES, D("53273.68")),
        ("2026-07-17", "Napoles", D("220.00"), NAPOLES, D("53407.20")),
        ("2026-07-18", "Mira Valle", D("103.75"), MIRA_VALLE, D("32940.63")),
    ]

    print("\n===== 1b. LOS RENGLONES DEL COMPROBANTE =====")
    print(f"  {'fecha':<12}{'ruta':<12}{'litros':>10}{'precio/L':>11}{'valor':>13}   a mano")
    renglones = _renglones(liq)
    assert len(renglones) == 4, f"tenían que ser 4 renglones y son {len(renglones)}"
    for renglon, (fecha, ruta, litros_, precio, valor) in zip(renglones, esperados):
        print(f"  {renglon['fecha']:<12}{renglon['ruta_nombre'] or '—':<12}"
              f"{D(renglon['litros']):>10}{D(renglon['precio_litro']):>11}"
              f"{D(renglon['valor']):>13}   ${valor}")
        assert renglon["fecha"] == fecha
        assert renglon["ruta_nombre"] == ruta, "el renglón tiene que decir de qué ruta es"
        assert D(renglon["litros"]) == litros_
        assert D(renglon["precio_litro"]) == precio
        assert D(renglon["valor"]) == valor
        # litros × precio == valor, con calculadora
        assert centavos(litros_ * precio) == valor

    suma = _revisar_invariante(liq, fotos)
    print(f"  {'':36}{'':11}{'-' * 13}")
    print(f"  suma de los renglones{'':15}{'':11}{suma:>13}")
    print(f"  valor_transporte del comprobante{'':4}{'':11}{D(liq['valor_transporte']):>13}")
    print(f"  suma de las fotos guardadas{'':9}{'':11}{fotos:>13}")
    assert suma == D("170196.76") == fotos
    assert D(liq["total_litros"]) == D("639.50")

    # La tarifa GENERAL ($200) no aparece en ningún renglón: solo aplica donde no
    # hay ruta de dónde sacar la tarifa, y acá todas las recepciones tienen ruta.
    assert GENERAL not in [D(r["precio_litro"]) for r in renglones]

    # Y las fotos en la base son las que se sumaron, no una versión recalculada.
    guardadas = db_session.scalars(
        select(RecepcionLeche).where(RecepcionLeche.liquidacion_transporte_id == uuid.UUID(liq["id"]))
    ).all()
    assert sum((D(r.valor_transporte) for r in guardadas), D(0)) == fotos


# ---------------------------------------------------------------------------
# 2. La tarifa cambiada a mitad de quincena: manda la tarifa VIVA
# ---------------------------------------------------------------------------
def test_la_tarifa_cambiada_a_mitad_de_quincena_manda_la_tarifa_viva(client, base_datos, db_session):
    """Dos recepciones del MISMO día y la MISMA ruta, recibidas a tarifas distintas.

    Pasa de verdad: el dueño recibe la leche de Aurelio, después se da cuenta de que
    la tarifa de Nápoles estaba mal y la corrige, y ese mismo día entra la de Marleny.
    Cada foto se toma con la tarifa que estaba puesta en ese momento:

        20/07  Nápoles  100 L × $242,76 = $24.276,00   (antes de corregir)
        20/07  Nápoles  120 L × $250,00 = $30.000,00   (después)

    PERO AL LIQUIDAR MANDA LA TARIFA VIVA —la única que el dueño tiene en pantalla y
    la única con la que puede reproducir la columna—, así que el día es UN renglón:

        20/07  Nápoles  220 L × $250,00 = $55.000,00

    y las dos fotos se vuelven a derivar a $250 para que sigan sumando ese total.

    ESTA PRUEBA EXIGÍA LO CONTRARIO (dos renglones, uno por tarifa) y la exigencia
    estaba mal: se escribió cuando la foto era la ÚNICA memoria de la tarifa, y con
    ella clavada el dueño no podía corregir una tarifa mal tecleada —el sistema le
    seguía cobrando la vieja para siempre y el comprobante la imprimía, aunque ya no
    existiera en ninguna pantalla—. La tarifa vieja sobrevive donde debe sobrevivir:
    en un flete YA PAGADO, que no se toca ni por un centavo, y ahí el renglón sí se
    parte (ver `test_liquidacion_flete_reparto_centavos.py`, la foto congelada).

    Con el día en un solo renglón el conductor sigue pudiendo reproducirlo a mano, que
    es lo único que este archivo nunca negocia: 220 × 250 = 55.000, exacto.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)

    antes = _recibir(client, h, esc, "2026-07-20", "Aurelio", "100")
    assert D(antes["valor_transporte"]) == D("24276.00")

    # A MITAD DE QUINCENA le corrigen la tarifa de Nápoles (Mira Valle no se toca)
    r = client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": "250"},
            {"ruta_id": esc["mira_valle"]["id"], "valor_transporte": str(MIRA_VALLE)},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text

    despues = _recibir(client, h, esc, "2026-07-20", "Marleny", "120")
    assert D(despues["valor_transporte"]) == D("30000.00")

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    renglones = _renglones(liq)
    db_session.expire_all()
    # Las fotos se leen DESPUÉS de liquidar: el comprobante las volvió a derivar con
    # la tarifa viva y lo que importa es que sumen el total del papel.
    fotos = sum(
        (D(x.valor_transporte) for x in db_session.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.liquidacion_transporte_id == uuid.UUID(liq["id"]))
        ).all()),
        D(0),
    )

    print("\n===== 2. LA TARIFA CAMBIÓ A MITAD DE QUINCENA =====")
    print(f"  al recibir: la foto de Aurelio quedó a $242,76 y la de Marleny a $250,00")
    for renglon in renglones:
        print(f"    {renglon['fecha']}  {renglon['ruta_nombre']:<11}"
              f"{D(renglon['litros']):>8} L × ${D(renglon['precio_litro'])} = "
              f"${D(renglon['valor'])}")
    print(f"  total del comprobante: ${D(liq['valor_transporte'])}  ·  fotos: ${fotos}")

    assert len(renglones) == 1, (
        f"el día y la ruta tenían que quedar en UN renglón a la tarifa viva: {renglones}")
    unico = renglones[0]
    assert unico["fecha"] == "2026-07-20"
    assert unico["ruta_nombre"] == "Napoles"
    assert D(unico["litros"]) == D("220.00")
    assert D(unico["precio_litro"]) == D("250.00")
    assert D(unico["valor"]) == centavos(D("220") * D("250")) == D("55000.00")

    assert _revisar_invariante(liq, fotos) == D("55000.00")


# ---------------------------------------------------------------------------
# 3. Recalcular: se corrigen los litros de un día y sigue cuadrando
# ---------------------------------------------------------------------------
def test_recalcular_al_corregir_los_litros_de_un_dia_sigue_cuadrando(client, base_datos):
    """Al 16/07 le corrigen los litros de Aurelio: 82 L pasan a 90 L.

    El recálculo tiene que dar EL MISMO comprobante que daría generarlo de cero, con
    el día partido igual por ruta. Las cifras nuevas, a mano:

        foto de Aurelio      90,00 L × $242,76 = $ 21.848,40
        16/07  Mira Valle     96,30 L × $317,50 = $ 30.575,25
        16/07  Nápoles      227,45 L × $242,76 = $ 55.215,76  (21.848,40 + 33.367,36)
        17/07  Nápoles       220,00 L × $242,76 = $ 53.407,20
        18/07  Mira Valle    103,75 L × $317,50 = $ 32.940,63
                                                  -----------
                             647,50 L              $172.138,84
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    recepciones = {}
    for fecha, quien, litros_ in [
        ("2026-07-16", "Aurelio", "82"),
        ("2026-07-16", "Marleny", "137.45"),
        ("2026-07-16", "Gilberto", "96.30"),
        ("2026-07-17", "Aurelio", "78.90"),
        ("2026-07-17", "Marleny", "141.10"),
        ("2026-07-18", "Gilberto", "103.75"),
    ]:
        recepciones[(fecha, quien)] = _recibir(client, h, esc, fecha, quien, litros_)

    liq_id = _liquidar_flete(client, h)["id"]
    antes = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print("\n===== 3. SE CORRIGEN LOS LITROS DE UN DÍA =====")
    print(f"  antes: {len(antes['detalles'])} renglones · {D(antes['total_litros'])} L · "
          f"${D(antes['valor_transporte'])}")
    assert D(antes["valor_transporte"]) == D("170196.76")

    r = client.put(
        f"{RECEPCIONES}/{recepciones[('2026-07-16', 'Aurelio')]['id']}",
        json={"cantidad_litros": "90"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    print(f"  la recepción del 16/07 de Aurelio pasa a 90 L -> flete ${D(r.json()['valor_transporte'])}")
    assert D(r.json()["valor_transporte"]) == D("21848.40")

    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    renglones = _renglones(liq)
    print(f"  {'fecha':<12}{'ruta':<12}{'litros':>10}{'precio/L':>11}{'valor':>13}")
    for renglon in renglones:
        print(f"  {renglon['fecha']:<12}{renglon['ruta_nombre'] or '—':<12}"
              f"{D(renglon['litros']):>10}{D(renglon['precio_litro']):>11}"
              f"{D(renglon['valor']):>13}")

    esperados = [
        ("2026-07-16", "Mira Valle", D("96.30"), MIRA_VALLE, D("30575.25")),
        ("2026-07-16", "Napoles", D("227.45"), NAPOLES, D("55215.76")),
        ("2026-07-17", "Napoles", D("220.00"), NAPOLES, D("53407.20")),
        ("2026-07-18", "Mira Valle", D("103.75"), MIRA_VALLE, D("32940.63")),
    ]
    assert len(renglones) == 4, "el recálculo tenía que dejar los mismos 4 renglones"
    for renglon, (fecha, ruta, litros_, precio, valor) in zip(renglones, esperados):
        assert (renglon["fecha"], renglon["ruta_nombre"]) == (fecha, ruta)
        assert D(renglon["litros"]) == litros_
        assert D(renglon["precio_litro"]) == precio
        assert D(renglon["valor"]) == valor

    suma = _revisar_invariante(liq)
    print(f"  suma de los renglones: ${suma} · total ${D(liq['valor_transporte'])} · "
          f"{D(liq['total_litros'])} L")
    assert suma == D("172138.84")
    assert D(liq["total_litros"]) == D("647.50")
    # Y el total sigue siendo la suma de las fotos guardadas, con la nueva incluida.
    fotos = (D("21848.40") + D("33367.36") + D("30575.25")
             + D("19153.76") + D("34253.44") + D("32940.63"))
    assert suma == fotos == D("172138.84")


def test_recalcular_y_generar_de_cero_dan_el_mismo_comprobante(client, base_datos):
    """Anular y volver a generar tiene que dar renglón por renglón lo mismo que
    recalcular. Si no, el mismo flete daría dos papeles distintos según por dónde
    se pasó, y el dueño no tendría manera de saber cuál creer.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    for fecha, quien, litros_ in [
        ("2026-07-16", "Aurelio", "82"),
        ("2026-07-16", "Gilberto", "96.30"),
        ("2026-07-17", "Marleny", "141.10"),
    ]:
        _recibir(client, h, esc, fecha, quien, litros_)

    liq_id = _liquidar_flete(client, h)["id"]
    recalculada = client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)
    assert recalculada.status_code == 200, recalculada.text
    despues_de_recalcular = [
        (d["fecha"], d["ruta_nombre"], D(d["litros"]), D(d["precio_litro"]), D(d["valor"]))
        for d in _renglones(client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json())
    ]

    assert client.post(f"{LIQUIDACIONES}/{liq_id}/anular", headers=h).status_code == 200
    nueva = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    de_cero = [
        (d["fecha"], d["ruta_nombre"], D(d["litros"]), D(d["precio_litro"]), D(d["valor"]))
        for d in _renglones(nueva)
    ]

    print("\n===== 3b. RECALCULAR == GENERAR DE CERO =====")
    for recalc, cero in zip(despues_de_recalcular, de_cero):
        print(f"  {recalc}  ==  {cero}")
    assert despues_de_recalcular == de_cero
    _revisar_invariante(nueva)


# ---------------------------------------------------------------------------
# 4. El centavo del redondeo: se REPARTE, y el renglón queda uno solo
# ---------------------------------------------------------------------------
def test_el_centavo_del_redondeo_se_reparte_y_el_renglon_queda_uno(
    client, base_datos, db_session
):
    """Dos fotos de la misma ruta y el mismo día que, sumadas, no dan la
    multiplicación de los litros juntos. Un centavo, pero un centavo es un defecto.

    Las cuentas, a mano:
        100,01 L × $242,76 = $24.278,4276  ->  se guardó $24.278,43  (subió)
        100,02 L × $242,76 = $24.280,8552  ->  se guardó $24.280,86  (subió)
                                               -----------
        suma de las fotos al recibir           $48.559,29
        juntos: 200,03 L × $242,76 = $48.559,2828 -> $48.559,28   (BAJÓ)

    ESTA PRUEBA CAMBIÓ DE EXIGENCIA, y vale la pena decir por qué. Antes exigía que
    el renglón SE PARTIERA en dos (una línea por recepción) para no tocar las fotos,
    y el comprobante quedaba en $48.559,29. Pero así el dueño veía dos líneas
    idénticas en fecha, ruta y tarifa —sin nada que explicara por qué— y su cuadre a
    mano (200,03 L × $242,76 = $48.559,28) no le daba.

    La decisión del dueño es que MANDA EL COMPROBANTE: el (día, ruta) es UN renglón
    de 200,03 L × $242,76 = $48.559,28, redondeado UNA sola vez, y ese centavo se le
    quita a una de las fotos —a la que quedó más lejos de su siguiente centavo, o sea
    a la de 100,02 L— para que las fotos sigan sumando el total:

        100,01 L -> $24.278,43   (se queda, su fracción era 0,0076)
        100,02 L -> $24.280,85   (baja un centavo, su fracción era 0,0052)
                     -----------
                     $48.559,28  == el renglón
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    a = _recibir(client, h, esc, "2026-07-22", "Aurelio", "100.01")
    b = _recibir(client, h, esc, "2026-07-22", "Marleny", "100.02")
    assert D(a["valor_transporte"]) == D("24278.43")
    assert D(b["valor_transporte"]) == D("24280.86")
    al_recibir = D("24278.43") + D("24280.86")

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    renglones = _renglones(liq)
    guardadas = db_session.scalars(
        select(RecepcionLeche).where(
            RecepcionLeche.liquidacion_transporte_id == uuid.UUID(liq["id"]),
            RecepcionLeche.deleted_at.is_(None),
        )
    ).all()
    fotos = sum((D(r.valor_transporte) for r in guardadas), D(0))

    print("\n===== 4. EL CENTAVO DEL REDONDEO SE REPARTE =====")
    print(f"  fotos al recibir: $24.278,43 + $24.280,86 = ${al_recibir}")
    print(f"  el cuadre del dueño: 200,03 L × $242,76 = $48.559,2828 -> $48.559,28")
    for renglon in renglones:
        print(f"    {renglon['fecha']}  {renglon['ruta_nombre']:<11}"
              f"{D(renglon['litros']):>8} L × ${D(renglon['precio_litro'])} = "
              f"${D(renglon['valor'])}")
    print("  fotos después del reparto: "
          + " + ".join(f"${D(r.valor_transporte)}" for r in sorted(
              guardadas, key=lambda r: r.cantidad_litros))
          + f" = ${fotos}")

    assert len(renglones) == 1, (
        "un día y una ruta a UNA sola tarifa es UN renglón: "
        f"salieron {len(renglones)}")
    assert D(renglones[0]["litros"]) == D("200.03")
    assert D(renglones[0]["precio_litro"]) == NAPOLES
    assert D(renglones[0]["valor"]) == centavos(D("200.03") * NAPOLES) == D("48559.28")
    assert _revisar_invariante(liq, fotos) == D("48559.28")
    assert D(liq["total_litros"]) == D("200.03")
    # El centavo salió de UNA foto —la de 100,02 L— y de ninguna otra.
    por_litros = {D(r.cantidad_litros): D(r.valor_transporte) for r in guardadas}
    assert por_litros == {D("100.01"): D("24278.43"), D("100.02"): D("24280.85")}, por_litros
    assert al_recibir - fotos == D("0.01")


def test_recalcular_despues_de_repartir_los_centavos_no_mueve_el_papel(client, base_datos):
    """EL PAPEL NO SE PUEDE MOVER SOLO, y este es el sitio exacto donde podría.

    El reparto de centavos le deja a una foto un centavo más (o menos) de lo que da
    su propia multiplicación. Al volver a armar el comprobante hay que reconocer que
    ese grupo sigue siendo de UNA tarifa: si el código exigiera que cada foto
    reprodujera la tarifa EXACTA, la foto repartida no la reproduciría, el grupo se
    partiría solo y el conductor recibiría un papel distinto cada vez que alguien
    oprime "Recalcular" sin haber cambiado ni un dato.

    Se prueba con los dos casos que reparten centavos:

      · CUATRO proveedores en Nápoles el 16/07, todos a $242,76: las cuatro fotos
        redondeadas suman dos centavos menos que 367,67 L × $242,76 = $89.255,57;
      · y el 17/07 con las tarifas MEZCLADAS (le cambiaron la tarifa a mitad de
        quincena), donde los DOS montones necesitan reparto a la vez.

    Y se recalcula tres veces, porque un cálculo que se corre solo casi nunca se
    corre una vez sola.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)

    def nuevo_proveedor(nombre):
        esc["proveedores"][nombre] = _crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "Napoles", "precio_litro": "1800",
            "ruta_id": esc["napoles"]["id"]})

    def papel(liq_id):
        """Todo lo que el conductor ve, para poder compararlo tal cual."""
        j = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
        return D(j["valor_transporte"]), [
            (d["fecha"], d["ruta_nombre"], D(d["litros"]), D(d["precio_litro"]), D(d["valor"]))
            for d in _renglones(j)
        ]

    print("\n===== 4b. RECALCULAR DESPUÉS DEL REPARTO =====")
    for i, litros_ in enumerate(("44.23", "82.48", "137.23", "103.73")):
        nuevo_proveedor(f"Cuatro {i}")
        _recibir(client, h, esc, "2026-07-16", f"Cuatro {i}", litros_)
    # El período se cierra EL MISMO 16, y no el 31, porque más abajo se liquida el día
    # 17 aparte: dos liquidaciones del mismo transportador con los períodos montados ya
    # no se dejan generar (ver `_exigir_periodo_sin_cruce` en el servicio: por ese camino
    # la deuda de la primera se quedaba sin cobrar). Las cifras que mide esta prueba son
    # las del día, así que no cambia nada.
    liq_id = _liquidar_flete(client, h, fin="2026-07-16")["id"]
    antes = papel(liq_id)
    print(f"  generado    ${antes[0]}  {[str(r[4]) for r in antes[1]]}")
    assert antes[0] == centavos(D("367.67") * NAPOLES) == D("89255.57")
    for vuelta in (1, 2, 3):
        assert client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h).status_code == 200
        ahora = papel(liq_id)
        print(f"  recálculo {vuelta} ${ahora[0]}  {[str(r[4]) for r in ahora[1]]}")
        assert ahora == antes, f"el papel se movió al recalcular: {antes} -> {ahora}"

    # Ahora el día en que la tarifa cambió en medio: cuatro fotos, dos tomadas a
    # $242,76 y dos a $317,51. Al liquidar manda la tarifa VIVA ($317,51), así que el
    # día queda en UN renglón de 326,77 L y el reparto tiene que acomodar los cuatro
    # centavos entre las cuatro fotos —y no moverlos en los recálculos siguientes—.
    for i, litros_ in enumerate(("44.23", "82.48")):
        nuevo_proveedor(f"Vieja {i}")
        _recibir(client, h, esc, "2026-07-17", f"Vieja {i}", litros_)
    assert client.put(f"{TRANSPORTADORES}/{esc['alex']['id']}", headers=h, json={"rutas": [
        {"ruta_id": esc["napoles"]["id"], "valor_transporte": "317.51"},
        {"ruta_id": esc["mira_valle"]["id"], "valor_transporte": str(MIRA_VALLE)},
    ]}).status_code == 200
    for i, litros_ in enumerate(("103.75", "96.31")):
        nuevo_proveedor(f"Nueva {i}")
        _recibir(client, h, esc, "2026-07-17", f"Nueva {i}", litros_)

    liq2 = _liquidar_flete(client, h, inicio="2026-07-17")["id"]
    antes2 = papel(liq2)
    print(f"  mezcla generada ${antes2[0]}")
    for fecha, ruta, litros_, precio, valor in antes2[1]:
        print(f"    {fecha}  {ruta:<11}{litros_:>9} L × ${precio} = ${valor}")
    assert len(antes2[1]) == 1, (
        f"el día tenía que quedar en UN renglón a la tarifa viva: {antes2[1]}")
    litros_del_dia = D("44.23") + D("82.48") + D("103.75") + D("96.31")
    assert litros_del_dia == D("326.77")
    assert antes2[0] == centavos(litros_del_dia * D("317.51")) == D("103752.74")
    for vuelta in (1, 2, 3):
        assert client.post(f"{LIQUIDACIONES}/{liq2}/recalcular", headers=h).status_code == 200
        ahora2 = papel(liq2)
        print(f"  recálculo {vuelta} ${ahora2[0]}")
        assert ahora2 == antes2, f"el papel de la mezcla se movió: {antes2} -> {ahora2}"
    _revisar_invariante(client.get(f"{LIQUIDACIONES}/{liq2}", headers=h).json())


# ---------------------------------------------------------------------------
# 5. Una recepción sin ruta: la tarifa general, y el renglón dice que no hay ruta
# ---------------------------------------------------------------------------
def test_el_dia_sin_ruta_va_en_su_propio_renglon_con_la_tarifa_general(client, base_datos):
    """Un proveedor sin ruta el mismo día que uno de Nápoles.

    El flete del que no tiene ruta sale de la tarifa GENERAL ($200), porque no hay
    ruta de dónde sacarla, y va en su propio renglón: juntarlo con el de Nápoles
    daría un renglón con dos tarifas.

        60 L × $200,00   = $12.000,00   (sin ruta)
        82 L × $242,76   = $19.906,32   (Nápoles)
                           -----------
                           $31.906,32
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    sin_ruta = _crear(client, h, PROVEEDORES, {
        "nombre": "Ramiro", "vereda": "El Alto", "precio_litro": "1800"})
    esc["proveedores"]["Ramiro"] = sin_ruta

    huerfano = _recibir(client, h, esc, "2026-07-19", "Ramiro", "60")
    napoles = _recibir(client, h, esc, "2026-07-19", "Aurelio", "82")
    assert D(huerfano["valor_transporte"]) == D("12000.00")
    assert D(napoles["valor_transporte"]) == D("19906.32")

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    renglones = _renglones(liq)

    print("\n===== 5. EL DÍA SIN RUTA =====")
    for renglon in renglones:
        print(f"  {renglon['fecha']}  {(renglon['ruta_nombre'] or '(sin ruta)'):<12}"
              f"{D(renglon['litros']):>7} L × ${D(renglon['precio_litro'])} = "
              f"${D(renglon['valor'])}")

    assert len(renglones) == 2
    sin = [r for r in renglones if r["ruta_id"] is None][0]
    con = [r for r in renglones if r["ruta_id"] is not None][0]
    assert sin["ruta_nombre"] is None
    assert D(sin["precio_litro"]) == GENERAL
    assert D(sin["valor"]) == D("12000.00")
    assert con["ruta_nombre"] == "Napoles"
    assert D(con["precio_litro"]) == NAPOLES
    assert _revisar_invariante(liq, D("31906.32")) == D("31906.32")


# ---------------------------------------------------------------------------
# 6. EL PDF: el conductor tiene que poder distinguir los dos renglones del día
# ---------------------------------------------------------------------------
def texto_pdf(contenido: bytes) -> str:
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def test_el_pdf_del_transportador_muestra_la_ruta_de_cada_renglon(client, base_datos):
    """El papel que firma el conductor, con la columna Ruta.

    Es lo único que le permite entender por qué el 16 de julio le aparece dos veces
    con cifras distintas. Se revisa que el PDF traiga el encabezado, los nombres de
    las dos rutas y las cuatro cifras de los renglones tal como se imprimen en
    pesos colombianos.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    for fecha, quien, litros_ in [
        ("2026-07-16", "Aurelio", "82"),
        ("2026-07-16", "Marleny", "137.45"),
        ("2026-07-16", "Gilberto", "96.30"),
        ("2026-07-17", "Aurelio", "78.90"),
        ("2026-07-17", "Marleny", "141.10"),
        ("2026-07-18", "Gilberto", "103.75"),
    ]:
        _recibir(client, h, esc, fecha, quien, litros_)
    liq_id = _liquidar_flete(client, h)["id"]

    r = client.get(f"{LIQUIDACIONES}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")
    impreso = texto_pdf(r.content)

    print("\n===== 6. EL PDF DEL TRANSPORTADOR =====")
    print(f"  {len(r.content)} bytes · encabezados: "
          f"{'Ruta' in impreso and 'Precio/L' in impreso}")
    for esperado in [
        "Fecha", "Ruta", "Litros", "Precio/L", "Valor",
        "Napoles", "Mira Valle",
        "$53.273,68", "$30.575,25", "$53.407,20", "$32.940,63",
        "$170.196,76",
        "242,76", "317,50",
    ]:
        assert esperado in impreso, f"el comprobante no imprime {esperado!r}"
        print(f"  imprime {esperado!r}")

    # Y en la tabla del detalle el día de las dos rutas sale DOS VECES, una por ruta.
    # Se mira solo ese trozo del documento porque el 16/07 también aparece arriba, en
    # el período de la quincena.
    tabla = impreso.split("Detalle diario", 1)[1].split("Resumen de liquidación", 1)[0]
    print(f"  la tabla del detalle: {tabla.strip()[:120]}...")
    assert tabla.count("16/07/2026") == 2, "el día de las dos rutas tenía que salir dos veces"
    assert "Mira Valle" in tabla and "Napoles" in tabla


def test_el_pdf_del_proveedor_no_cambia(client, base_datos):
    """El comprobante del PROVEEDOR se queda como estaba: sin columna Ruta.

    Ahí el renglón es el día de ese productor y la ruta no tiene nada que decir. Se
    deja clavado porque los dos comprobantes salen de la misma función y es fácil
    que un cambio en uno se cuele en el otro.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, "2026-07-16", "Aurelio", "82")

    generadas = client.post(
        f"{LIQUIDACIONES}/generar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31", "tipo": "proveedor"},
        headers=h,
    ).json()["generadas"]
    liq = client.get(f"{LIQUIDACIONES}/{generadas[0]['id']}", headers=h).json()

    print("\n===== 6b. EL COMPROBANTE DEL PROVEEDOR =====")
    print(f"  renglones: {[(d['fecha'], d['ruta_id'], d['valor']) for d in liq['detalles']]}")
    assert all(d["ruta_id"] is None for d in liq["detalles"]), (
        "los renglones del proveedor no llevan ruta"
    )
    assert all(d["ruta_nombre"] is None for d in liq["detalles"])
    # 82 L × $1.800 = $147.600 de leche, que es lo que ese comprobante siempre dijo
    assert D(liq["detalles"][0]["valor"]) == D("147600.00")
    assert D(liq["valor_total"]) == D("147600.00")

    impreso = texto_pdf(client.get(f"{LIQUIDACIONES}/{liq['id']}/pdf", headers=h).content)
    tabla = impreso.split("Detalle diario", 1)[1].split("Resumen de liquidación", 1)[0]
    print(f"  la tabla del detalle: {tabla.strip()}")
    # Las mismas CUATRO columnas de siempre, sin Ruta. (Ojo: "Ruta / vereda" sí sale
    # más arriba, en el bloque de datos del proveedor, y eso ya salía antes; lo que se
    # revisa acá es la tabla del detalle.)
    assert "Fecha Litros Precio/L Valor" in tabla
    assert "Ruta" not in tabla
    assert "Napoles" not in tabla
    assert "$147.600" in tabla


# ---------------------------------------------------------------------------
# 7. La pre-liquidación agrupa igual que el comprobante
# ---------------------------------------------------------------------------
def test_la_preliquidacion_agrupa_igual_que_el_comprobante(client, base_datos):
    """El "¿cómo voy?" tiene que mostrar los mismos renglones que el papel oficial.

    Si el avance agrupara por día y el comprobante por día y ruta, el transportador
    vería una cifra en la pantalla y otra en el documento que firma.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    for fecha, quien, litros_ in [
        ("2026-07-16", "Aurelio", "82"),
        ("2026-07-16", "Gilberto", "96.30"),
    ]:
        _recibir(client, h, esc, fecha, quien, litros_)

    r = client.post(
        f"{LIQUIDACIONES}/previsualizar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador", "tercero_id": esc["alex"]["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    pre = r.json()[0]

    print("\n===== 7. LA PRE-LIQUIDACIÓN =====")
    for d in pre["detalles"]:
        print(f"  {d['fecha']}  {d['ruta_nombre']:<11}{D(d['litros']):>7} L × "
              f"${D(d['precio_litro'])} = ${D(d['valor'])}")
    assert len(pre["detalles"]) == 2
    por_ruta = {d["ruta_nombre"]: d for d in pre["detalles"]}
    assert D(por_ruta["Napoles"]["valor"]) == D("19906.32")
    assert D(por_ruta["Mira Valle"]["valor"]) == D("30575.25")
    suma = sum((D(d["valor"]) for d in pre["detalles"]), D(0))
    assert suma == D(pre["valor_transporte"]) == D("50481.57")
    for d in pre["detalles"]:
        assert centavos(D(d["litros"]) * D(d["precio_litro"])) == D(d["valor"])

    # Y el PDF preliminar también trae la columna Ruta
    rp = client.post(
        f"{LIQUIDACIONES}/previsualizar/pdf",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador", "tercero_id": esc["alex"]["id"]},
        headers=h,
    )
    assert rp.status_code == 200, rp.text
    impreso = texto_pdf(rp.content)
    print(f"  el PDF preliminar imprime las dos rutas: "
          f"{'Napoles' in impreso and 'Mira Valle' in impreso}")
    assert "Ruta" in impreso and "Napoles" in impreso and "Mira Valle" in impreso
    assert "$50.481,57" in impreso


# ---------------------------------------------------------------------------
# 8. El comprobante se paga y las cifras se congelan
# ---------------------------------------------------------------------------
def test_pagado_el_comprobante_los_renglones_quedan_trabados(client, base_datos):
    """Con el flete pagado, corregir los litros del día rebota: los renglones de un
    comprobante que ya se pagó no se pueden mover.

    Es el candado que ya existía; se prueba acá porque ahora el renglón es (día,
    ruta) y un recálculo silencioso podría partir en dos un renglón de un papel que
    el conductor ya firmó.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    recepcion = _recibir(client, h, esc, "2026-07-16", "Aurelio", "82")
    _recibir(client, h, esc, "2026-07-16", "Gilberto", "96.30")
    liq_id = _liquidar_flete(client, h)["id"]

    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h).status_code == 200
    pagada = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()

    r = client.put(f"{RECEPCIONES}/{recepcion['id']}", json={"cantidad_litros": "90"}, headers=h)
    print("\n===== 8. EL COMPROBANTE PAGADO =====")
    print(f"  estado {pagada['estado']} · corregir los litros: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')[:80]}")
    assert r.status_code == 422, r.text

    igual = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    assert [(d["fecha"], d["ruta_nombre"], d["valor"]) for d in _renglones(igual)] == [
        (d["fecha"], d["ruta_nombre"], d["valor"]) for d in _renglones(pagada)
    ]
    _revisar_invariante(igual, D("50481.57"))


# ---------------------------------------------------------------------------
# 8b. La red de seguridad: una foto que ninguna tarifa explica
# ---------------------------------------------------------------------------
class _RecepcionFalsa:
    """Una recepción con el flete guardado A MANO, para probar la red de seguridad.

    No se puede llegar a este estado por el API: el flete siempre se calcula como
    litros × tarifa. Se arma acá a mano porque sí se puede llegar por una corrección
    directa en la base, y en ese caso el comprobante tiene que seguir cuadrando en
    vez de imprimir un renglón que no se reproduce con calculadora.
    """

    def __init__(self, litros_, flete):
        self.id = uuid.uuid4()
        self.fecha = date(2026, 7, 16)
        self.ruta_id = None
        self.ruta = None
        self.transportador = None
        self.cantidad_litros = D(litros_)
        self.valor_transporte = D(flete)


def _renglones_de_fotos(recepciones):
    """Los renglones que salen de unas FOTOS tal como están guardadas.

    Se llama a `_reparto_del_flete` y no a `_renglones_de_transporte` a propósito, y
    conviene saber por qué: desde que un flete no pagado se vuelve a derivar de la
    tarifa viva, el camino del avance (`_renglones_de_transporte`) ya no mira la foto
    guardada —la recalcula— y por ahí NO se puede llegar a una foto rara. La red de
    seguridad de abajo sigue viva donde importa: en un flete YA PAGADO, cuyas fotos
    están congeladas y hay que imprimir tal como están. Eso es lo que se prueba acá.
    """
    from app.modules.liquidaciones.service import _reparto_del_flete

    congeladas = frozenset(r.id for r in recepciones)
    return _reparto_del_flete(recepciones, congeladas).renglones


def test_una_foto_que_ninguna_tarifa_explica_igual_cuadra():
    """3 L con $10,00 de flete: NO existe tarifa de dos decimales que lo dé.

        3 L × $3,33 = $ 9,99   (un centavo de menos)
        3 L × $3,34 = $10,02   (dos centavos de más)

    Así que el renglón se parte, y las dos partes suman exacto lo que dice la base:

        2,99 L × $3,33 = $ 9,96
        0,01 L × $4,00 = $ 0,04
                         -------
        3,00 L           $10,00
    """
    renglones = _renglones_de_fotos([_RecepcionFalsa("3", "10.00")])

    print("\n===== 8b. UNA FOTO QUE NINGUNA TARIFA EXPLICA =====")
    for renglon in renglones:
        print(f"  {D(renglon['litros']):>6} L × ${D(renglon['precio_litro'])} = "
              f"${D(renglon['valor'])}")
        assert centavos(D(renglon["litros"]) * D(renglon["precio_litro"])) == D(renglon["valor"])
    print(f"  suma: {sum(D(r['litros']) for r in renglones)} L · "
          f"${sum(D(r['valor']) for r in renglones)}")

    assert len(renglones) == 2
    assert sum((D(r["litros"]) for r in renglones), D(0)) == D("3.00")
    assert sum((D(r["valor"]) for r in renglones), D(0)) == D("10.00")

    # Media docena de fotos raras más: la invariante se cumple en todas, y ninguna
    # pierde ni gana un peso contra lo que dice la base.
    for litros_, flete in [("0.50", "3.33"), ("100", "1.00"), ("7", "23.45"), ("0.03", "1.00")]:
        renglones = _renglones_de_fotos([_RecepcionFalsa(litros_, flete)])
        assert sum((D(r["litros"]) for r in renglones), D(0)) == D(litros_)
        assert sum((D(r["valor"]) for r in renglones), D(0)) == D(flete)
        for renglon in renglones:
            assert centavos(D(renglon["litros"]) * D(renglon["precio_litro"])) == D(renglon["valor"])
        print(f"  {litros_:>6} L con ${flete:<7} -> {len(renglones)} renglón(es), cuadra")


def test_un_flete_sin_litros_se_rebota_con_mensaje_claro():
    """$5.000 de flete con CERO litros no se puede escribir en ningún renglón.

    No es un descuido: no hay litros × precio que dé $5.000 si los litros son cero.
    Antes que imprimir un comprobante que no cuadra, rebota diciendo qué día revisar.
    """
    from app.core.exceptions import BusinessError

    with pytest.raises(BusinessError) as error:
        _renglones_de_fotos([_RecepcionFalsa("0", "5000")])

    print("\n===== 8c. FLETE SIN LITROS =====")
    print(f"  {error.value.detail}")
    assert "16/07/2026" in error.value.detail, "el mensaje tiene que decir qué día es"


# ---------------------------------------------------------------------------
# 9. LA MIGRACIÓN: le pone la ruta a los renglones viejos, sin mover un peso
# ---------------------------------------------------------------------------
def _cargar_migracion():
    """Carga el módulo de la migración por ruta.

    Se prueba LA MIGRACIÓN DE VERDAD, la misma función que corre `alembic upgrade`,
    y no una copia de su lógica: una copia se queda atrás y entonces la prueba
    certifica algo que ya no es lo que se ejecuta. Hace falta porque pytest corre
    sobre SQLite con `create_all` y las migraciones no se ejercitan solas.
    """
    ruta = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "d3b8f4c1a7e6_ruta_en_los_renglones_del_flete.py"
    )
    spec = importlib.util.spec_from_file_location("migracion_ruta_en_renglones", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture()
def comprobante_viejo():
    """Un comprobante de flete como los que el cliente YA TIENE guardados.

    Renglones POR DÍA, sin ruta (la columna acaba de nacer y está en nulo), con las
    recepciones detrás apuntándole por `liquidacion_transporte_id`. Tres días a
    propósito:

      · el 16/07 con DOS proveedores de la MISMA ruta  -> se le puede poner Nápoles;
      · el 17/07 con dos proveedores de rutas DISTINTAS -> se queda en nulo, porque
        ese renglón viejo junta las dos y no hay una sola ruta que sea la verdad;
      · el 18/07 con una recepción SIN ruta            -> se queda en nulo.

    Y un comprobante de PROVEEDOR con un renglón del mismo día, para comprobar que
    el backfill no lo toca.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    sesion = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    ahora = datetime.now(timezone.utc)
    empresa = Empresa(nombre="Quesera Vieja", nit="900V")
    sesion.add(empresa)
    sesion.flush()
    napoles = Ruta(empresa_id=empresa.id, nombre="Napoles", municipio="Granada")
    mira_valle = Ruta(empresa_id=empresa.id, nombre="Mira Valle", municipio="Granada")
    alex = Transportador(empresa_id=empresa.id, nombre="Alex Agudelo",
                         valor_transporte=Decimal("100"))
    sesion.add_all([napoles, mira_valle, alex])
    sesion.flush()
    proveedores = {}
    for nombre, ruta in (("Aurelio", napoles), ("Marleny", napoles),
                         ("Gilberto", mira_valle), ("Ramiro", None)):
        proveedor = Proveedor(
            empresa_id=empresa.id, nombre=nombre, precio_litro=Decimal("1800"),
            ruta_id=ruta.id if ruta else None,
        )
        sesion.add(proveedor)
        proveedores[nombre] = proveedor
    sesion.flush()

    flete = Liquidacion(
        empresa_id=empresa.id, tipo=TIPO_TRANSPORTADOR, transportador_id=alex.id,
        periodo_inicio=date(2026, 7, 16), periodo_fin=date(2026, 7, 31),
        total_litros=Decimal("400"), valor_transporte=Decimal("40000"),
        valor_total=Decimal("40000"), saldo=Decimal("40000"), estado="borrador",
    )
    leche = Liquidacion(
        empresa_id=empresa.id, tipo=TIPO_PROVEEDOR, proveedor_id=proveedores["Aurelio"].id,
        periodo_inicio=date(2026, 7, 16), periodo_fin=date(2026, 7, 31),
        total_litros=Decimal("100"), valor_bruto=Decimal("180000"),
        valor_total=Decimal("180000"), saldo=Decimal("180000"), estado="borrador",
    )
    sesion.add_all([flete, leche])
    sesion.flush()

    # Los renglones VIEJOS: uno por día y sin ruta.
    renglones = {}
    for dia, litros_, valor in (
        (date(2026, 7, 16), Decimal("180"), Decimal("18000")),
        (date(2026, 7, 17), Decimal("160"), Decimal("16000")),
        (date(2026, 7, 18), Decimal("60"), Decimal("6000")),
    ):
        detalle = LiquidacionDetalle(
            liquidacion_id=flete.id, fecha=dia, litros=litros_,
            precio_litro=Decimal("100"), valor=valor,
        )
        sesion.add(detalle)
        renglones[dia] = detalle
    renglon_leche = LiquidacionDetalle(
        liquidacion_id=leche.id, fecha=date(2026, 7, 16), litros=Decimal("100"),
        precio_litro=Decimal("1800"), valor=Decimal("180000"),
    )
    sesion.add(renglon_leche)

    for dia, quien, litros_ in (
        (date(2026, 7, 16), "Aurelio", Decimal("100")),
        (date(2026, 7, 16), "Marleny", Decimal("80")),
        (date(2026, 7, 17), "Aurelio", Decimal("100")),
        (date(2026, 7, 17), "Gilberto", Decimal("60")),
        (date(2026, 7, 18), "Ramiro", Decimal("60")),
    ):
        proveedor = proveedores[quien]
        sesion.add(RecepcionLeche(
            empresa_id=empresa.id, fecha=dia, proveedor_id=proveedor.id,
            transportador_id=alex.id, ruta_id=proveedor.ruta_id,
            cantidad_litros=litros_, precio_litro=Decimal("1800"),
            valor_bruto=litros_ * Decimal("1800"),
            valor_transporte=litros_ * Decimal("100"),
            valor_neto=litros_ * Decimal("1800"),
            liquidacion_transporte_id=flete.id,
            liquidacion_id=leche.id if quien == "Aurelio" and dia == date(2026, 7, 16) else None,
            created_at=ahora, updated_at=ahora,
        ))
    sesion.flush()
    sesion.commit()
    try:
        yield {
            "engine": engine, "sesion": sesion, "renglones": renglones,
            "renglon_leche": renglon_leche, "napoles": napoles, "mira_valle": mira_valle,
        }
    finally:
        sesion.close()
        Base.metadata.drop_all(bind=engine)


def test_la_migracion_rotula_solo_los_dias_que_tenian_una_sola_ruta(comprobante_viejo):
    """El backfill le pone la ruta a los renglones viejos donde no hay duda, deja en
    nulo los ambiguos, NO toca los del proveedor y NO mueve ninguna cifra.

    Lo de "no mover ninguna cifra" es lo importante: hay comprobantes ya pagados. Si
    la migración recalculara los renglones "bien", les cambiaría la plata a papeles
    que ya están firmados. Lo único que escribe es la etiqueta de la ruta.
    """
    sesion = comprobante_viejo["sesion"]
    renglones = comprobante_viejo["renglones"]
    migracion = _cargar_migracion()

    antes = {
        dia: (Decimal(d.litros), Decimal(d.precio_litro), Decimal(d.valor))
        for dia, d in renglones.items()
    }
    assert all(d.ruta_id is None for d in renglones.values()), "ya venían rotulados"

    with comprobante_viejo["engine"].begin() as conn:
        rotulados = migracion.backfill_ruta_de_los_renglones(conn)

    sesion.expire_all()
    print("\n===== 9. LA MIGRACIÓN DE LOS RENGLONES VIEJOS =====")
    print(f"  renglones rotulados: {rotulados}")
    nombres = {
        comprobante_viejo["napoles"].id: "Napoles",
        comprobante_viejo["mira_valle"].id: "Mira Valle",
    }
    for dia, detalle in sorted(renglones.items()):
        sesion.refresh(detalle)
        etiqueta = nombres.get(detalle.ruta_id, "(en nulo)")
        print(f"  {dia}  {etiqueta:<12}{Decimal(detalle.litros):>8} L × "
              f"${Decimal(detalle.precio_litro)} = ${Decimal(detalle.valor)}")

    # 16/07: los dos proveedores eran de Nápoles -> se rotula
    assert renglones[date(2026, 7, 16)].ruta_id == comprobante_viejo["napoles"].id
    # 17/07: Nápoles y Mira Valle en el mismo renglón -> no hay una sola verdad
    assert renglones[date(2026, 7, 17)].ruta_id is None
    # 18/07: la recepción no tenía ruta -> tampoco
    assert renglones[date(2026, 7, 18)].ruta_id is None
    assert rotulados == 1

    # NINGUNA CIFRA SE MOVIÓ
    for dia, detalle in renglones.items():
        assert (Decimal(detalle.litros), Decimal(detalle.precio_litro),
                Decimal(detalle.valor)) == antes[dia], f"al renglón del {dia} le movieron cifras"

    # Y el renglón del comprobante del PROVEEDOR se quedó sin ruta, como debe ser
    renglon_leche = comprobante_viejo["renglon_leche"]
    sesion.refresh(renglon_leche)
    print(f"  el renglón del comprobante del proveedor: ruta_id={renglon_leche.ruta_id}")
    assert renglon_leche.ruta_id is None

    # Correrlo dos veces da lo mismo (rotula lo mismo y no daña nada)
    with comprobante_viejo["engine"].begin() as conn:
        assert migracion.backfill_ruta_de_los_renglones(conn) == 1
