"""UN TRANSPORTADOR, VARIAS RUTAS, Y UNA TARIFA POR LITRO DISTINTA EN CADA UNA.

Lo pidió el dueño en dos mensajes seguidos: "ahora el transportador puede tener
varias rutas, por ejemplo este tuvo que hacer las dos" (su Alex Agudelo hizo
Nápoles Y Mira Valle el mismo día) y "pero cada ruta puede tener un valor
diferente de litro por leche". Lo segundo es lo que cambia todo: LA RUTA DEJÓ DE
SER UNA ETIQUETA Y ENTRÓ EN LA PLATA, porque es la que escoge cuánto se le paga al
transportador por cada litro que recoge.

LAS CIFRAS, hechas a mano, que son las que el dueño cuadra en el cuaderno:

  Alex Agudelo cobra $242,76 por litro en Nápoles y $300 en Mira Valle.

    Nápoles     ·  82 L × $242,76  =  $19.906,32
                   (242,76 × 80 = 19.420,80  +  242,76 × 2 = 485,52)
    Mira Valle  ·  95 L × $300,00  =  $28.500,00
                   ------------------------------------------------
                   el día completo  =  $48.406,32

Si el sistema le aplicara UNA sola tarifa a los dos, como hacía antes, Nápoles a
$300 le daría $24.600 en vez de $19.906,32: $4.693,68 de más en un solo día, en un
solo proveedor. Por eso esto se prueba con las cifras escritas y no "que corra".

LO QUE SE CUIDA ACÁ:

  1. las dos rutas dan cada una su cifra exacta, al centavo;
  2. una recepción SIN ruta y una ruta SIN tarifa propia caen en la tarifa
     GENERAL del transportador —nunca en un cero callado, que sería el señor
     trabajando gratis hasta que alguien cuadre la quincena—;
  3. una ruta DE OTRA EMPRESA se rechaza (el hueco que más importa: es plata de
     otra quesera metida en la nuestra);
  4. una ruta borrada se rechaza, y la misma ruta repetida en la lista también
     —si mandó Nápoles con dos tarifas, no se sabe cuál quiere—;
  5. el PUT es parcial de verdad: guardar el teléfono NO le borra las tarifas, y
     mandar [] sí se las quita;
  6. corregir una tarifa HOY no le mueve el flete a las recepciones ya guardadas:
     son FOTOS del momento, y es lo que hace que un comprobante viejo siga
     cuadrando;
  7. el cambio de rutas y tarifas queda en la auditoría (si no, cambiar una
     tarifa de $242,76 a $300 sería un cambio de plata sin rastro);
  8. la MIGRACIÓN copia la ruta y la tarifa que el cliente ya tiene cargadas, sin
     mover ni un centavo.
"""
import importlib.util
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models_registry  # noqa: F401  (registra todas las tablas en Base)
from app.core.database import Base
from app.modules.empresas.models import Empresa
from app.modules.rutas.models import Ruta
from app.modules.transportadores.models import Transportador, TransportadorRuta
from tests.conftest import auth_headers

TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
AUDITORIA = "/api/v1/auditoria"

# Las tarifas del caso del dueño.
NAPOLES = "242.76"
MIRA_VALLE = "300"


def D(v):
    return Decimal(str(v))


def _crear(client, headers, url, payload):
    r = client.post(url, json=payload, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _detalle(respuesta):
    return respuesta.json().get("error", {}).get("detail", "")


def _rutas_por_nombre(transportador):
    """Las rutas que devuelve el API, indexadas por el NOMBRE que ella trae."""
    return {fila["nombre"]: fila for fila in transportador["rutas"]}


def _escenario_dos_rutas(client, h, general="0"):
    """Alex Agudelo con Nápoles a $242,76 y Mira Valle a $300, y un proveedor en cada una."""
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    mira_valle = _crear(client, h, RUTAS, {"nombre": "Mira Valle", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo",
        "valor_transporte": general,
        "rutas": [
            {"ruta_id": napoles["id"], "valor_transporte": NAPOLES},
            {"ruta_id": mira_valle["id"], "valor_transporte": MIRA_VALLE},
        ],
    })
    libardo = _crear(client, h, PROVEEDORES, {
        "nombre": "Libardo", "vereda": "Napoles", "precio_litro": "1800",
        "ruta_id": napoles["id"],
    })
    carmen = _crear(client, h, PROVEEDORES, {
        "nombre": "Carmen", "vereda": "Mira Valle", "precio_litro": "1800",
        "ruta_id": mira_valle["id"],
    })
    return {
        "napoles": napoles, "mira_valle": mira_valle,
        "alex": alex, "libardo": libardo, "carmen": carmen,
    }


# ---------------------------------------------------------------------------
# 1. LAS DOS RUTAS, CADA UNA CON SU CIFRA
# ---------------------------------------------------------------------------
def test_las_dos_rutas_del_mismo_dia_dan_cada_una_su_cifra(client, base_datos):
    """82 L en Nápoles a $242,76 = $19.906,32 · 95 L en Mira Valle a $300 = $28.500.

    El caso literal del dueño: Alex hizo LAS DOS rutas el mismo día. Cada recepción
    tiene que cobrarse a la tarifa de SU ruta, y la ruta de la recepción se hereda
    del proveedor. Si las dos salieran a la misma tarifa, una de las dos cifras
    estaría mala —y con $300 en Nápoles serían $4.693,68 de más en un solo día—.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h)

    en_napoles = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": d["libardo"]["id"],
        "transportador_id": d["alex"]["id"], "cantidad_litros": "82",
    })
    en_mira_valle = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": d["carmen"]["id"],
        "transportador_id": d["alex"]["id"], "cantidad_litros": "95",
    })

    print("\n===== 1. ALEX HIZO LAS DOS RUTAS EL MISMO DÍA =====")
    print(f"  Napoles     ·  82 L × ${NAPOLES:>7} = ${D(en_napoles['valor_transporte']):>12,.2f}"
          f"   (a mano: $19.906,32)")
    print(f"  Mira Valle  ·  95 L × ${MIRA_VALLE:>7} = ${D(en_mira_valle['valor_transporte']):>12,.2f}"
          f"   (a mano: $28.500,00)")
    total = D(en_napoles["valor_transporte"]) + D(en_mira_valle["valor_transporte"])
    print(f"  ------------------------------------------------------")
    print(f"  el día completo                = ${total:>12,.2f}   (a mano: $48.406,32)")

    assert D(en_napoles["valor_transporte"]) == D("82") * D(NAPOLES) == D("19906.32")
    assert D(en_mira_valle["valor_transporte"]) == D("95") * D(MIRA_VALLE) == D("28500.00")
    assert total == D("48406.32")

    # Y la ruta de cada día es la del proveedor, que es de donde sale la tarifa.
    assert en_napoles["ruta_id"] == d["napoles"]["id"]
    assert en_mira_valle["ruta_id"] == d["mira_valle"]["id"]

    # Lo GUARDADO es lo mismo que se devolvió (nada se redondea distinto al releer)
    for recepcion, esperado in ((en_napoles, "19906.32"), (en_mira_valle, "28500.00")):
        releida = client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h).json()
        assert D(releida["valor_transporte"]) == D(esperado)


def test_las_rutas_se_leen_con_su_nombre_y_su_tarifa(client, base_datos):
    """La pantalla necesita "Napoles — $242,76" sin ir a buscar el catálogo aparte.

    Por eso `rutas` en la lectura trae el NOMBRE de la ruta y no solo el id: el
    diálogo del transportador se llena con una sola llamada.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h)

    releido = client.get(f"{TRANSPORTADORES}/{d['alex']['id']}", headers=h).json()
    por_nombre = _rutas_por_nombre(releido)

    print("\n===== 1b. LAS RUTAS SE LEEN CON NOMBRE =====")
    for nombre, fila in sorted(por_nombre.items()):
        print(f"  {nombre:<12} → ${D(fila['valor_transporte']):>9,.2f}")

    assert set(por_nombre) == {"Napoles", "Mira Valle"}
    assert D(por_nombre["Napoles"]["valor_transporte"]) == D("242.76")
    assert D(por_nombre["Mira Valle"]["valor_transporte"]) == D("300")
    assert por_nombre["Napoles"]["ruta_id"] == d["napoles"]["id"]

    # También en la lista, que es de donde el frontend llena la tabla
    en_lista = [
        t for t in client.get(TRANSPORTADORES, headers=h).json()["items"]
        if t["id"] == d["alex"]["id"]
    ][0]
    assert len(en_lista["rutas"]) == 2
    assert D(_rutas_por_nombre(en_lista)["Napoles"]["valor_transporte"]) == D("242.76")

    # Y `ruta_id` ya no existe en el transportador: la ruta no es una sola.
    print(f"  el transportador ya no trae ruta_id: {'ruta_id' not in releido}")
    assert "ruta_id" not in releido


# ---------------------------------------------------------------------------
# 2. CUANDO NO HAY RUTA DE DÓNDE SACAR LA TARIFA: LA GENERAL
# ---------------------------------------------------------------------------
def test_una_recepcion_sin_ruta_usa_la_tarifa_general(client, base_datos):
    """Un proveedor sin ruta deja el día sin ruta: ahí manda la tarifa general.

    82 L × $130 = $10.660. La general no es un dato duplicado: es el ÚNICO valor
    posible cuando no hay ruta de dónde sacarlo. Sin ella el flete quedaría en cero
    y el transportador trabajaría gratis sin que nadie lo note.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")
    # Sin ruta: ni el proveedor la tiene, así que la recepción tampoco
    suelto = _crear(client, h, PROVEEDORES, {"nombre": "Moisés", "precio_litro": "1800"})

    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-02", "proveedor_id": suelto["id"],
        "transportador_id": d["alex"]["id"], "cantidad_litros": "82",
    })

    print("\n===== 2. DÍA SIN RUTA → TARIFA GENERAL =====")
    print(f"  ruta del día : {recepcion['ruta_id']}")
    print(f"  82 L × $130  = ${D(recepcion['valor_transporte']):,.2f}   (a mano: $10.660,00)")
    assert recepcion["ruta_id"] is None
    assert D(recepcion["valor_transporte"]) == D("82") * D("130") == D("10660.00")


def test_una_ruta_sin_tarifa_propia_usa_la_general(client, base_datos):
    """Alex hace Nápoles a $242,76; en La Esperanza, que no le pusieron tarifa,
    cobra la general de $130.

    82 L × $130 = $10.660. Es el caso de todos los días: se agrega una ruta nueva y
    a nadie se le anota todavía cuánto cobra en ella. Que caiga en la general es lo
    que evita el cero callado.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")
    esperanza = _crear(client, h, RUTAS, {"nombre": "La Esperanza", "municipio": "Granada"})
    aurelio = _crear(client, h, PROVEEDORES, {
        "nombre": "Aurelio", "precio_litro": "1800", "ruta_id": esperanza["id"],
    })

    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-03", "proveedor_id": aurelio["id"],
        "transportador_id": d["alex"]["id"], "cantidad_litros": "82",
    })

    print("\n===== 2b. RUTA SIN TARIFA PROPIA → TARIFA GENERAL =====")
    print(f"  Alex tiene tarifa en Napoles y Mira Valle, no en La Esperanza")
    print(f"  82 L × $130 = ${D(recepcion['valor_transporte']):,.2f}   (a mano: $10.660,00)")
    assert recepcion["ruta_id"] == esperanza["id"]
    assert D(recepcion["valor_transporte"]) == D("10660.00")


def test_la_tarifa_de_la_ruta_le_gana_a_la_general(client, base_datos):
    """Con las dos puestas, la de la ruta MANDA. Es el orden de la regla.

    General $130, Nápoles $242,76: 82 L en Nápoles son $19.906,32 y no $10.660.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")

    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-04", "proveedor_id": d["libardo"]["id"],
        "transportador_id": d["alex"]["id"], "cantidad_litros": "82",
    })
    print("\n===== 2c. LA DE LA RUTA LE GANA A LA GENERAL =====")
    print(f"  general $130 · Napoles $242,76")
    print(f"  82 L en Napoles = ${D(recepcion['valor_transporte']):,.2f}  (no $10.660,00)")
    assert D(recepcion["valor_transporte"]) == D("19906.32")


# ---------------------------------------------------------------------------
# 3. LA RUTA DE OTRA EMPRESA: EL HUECO QUE MÁS IMPORTA
# ---------------------------------------------------------------------------
def test_una_ruta_de_otra_empresa_se_rechaza(client, base_datos):
    """La Quesera A no le puede colgar una ruta de la Quesera B a su transportador.

    Es lo más grave que podía entrar por acá: la tarifa por ruta es plata, y una
    ruta ajena significaría que la cuenta de una quesera depende de un dato que otra
    puede cambiar o borrar. Se rebota con 422 y un mensaje que se entiende, ni con
    un 500 ni guardándola callada.

    Se prueba en las DOS puertas —crear y editar—, porque cerrar solo una deja la
    otra abierta y con el id en la mano cualquiera la encuentra.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    ajena = _crear(client, hb, RUTAS, {"nombre": "Ruta de la B", "municipio": "Otra"})
    propia = _crear(client, ha, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})

    print("\n===== 3. LA RUTA DE OTRA EMPRESA =====")
    r = client.post(TRANSPORTADORES, headers=ha, json={
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [{"ruta_id": ajena["id"], "valor_transporte": NAPOLES}],
    })
    print(f"  al CREAR con la ruta de la B : {r.status_code} · {_detalle(r)[:80]}")
    assert r.status_code == 422, r.text
    assert "no existe en esta empresa" in _detalle(r)

    # Y nada quedó guardado a medias: ni el transportador
    assert client.get(TRANSPORTADORES, headers=ha).json()["items"] == []

    # Ahora por la puerta de editar, con un transportador que sí existe
    alex = _crear(client, ha, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [{"ruta_id": propia["id"], "valor_transporte": NAPOLES}],
    })
    r = client.put(f"{TRANSPORTADORES}/{alex['id']}", headers=ha, json={
        "rutas": [
            {"ruta_id": propia["id"], "valor_transporte": NAPOLES},
            {"ruta_id": ajena["id"], "valor_transporte": MIRA_VALLE},
        ],
    })
    print(f"  al EDITAR con la ruta de la B: {r.status_code} · {_detalle(r)[:80]}")
    assert r.status_code == 422, r.text

    # Y la ruta buena que ya tenía NO se perdió por el intento fallido
    sigue = client.get(f"{TRANSPORTADORES}/{alex['id']}", headers=ha).json()
    print(f"  sus rutas siguen siendo      : {[f['nombre'] for f in sigue['rutas']]}")
    assert len(sigue["rutas"]) == 1
    assert D(sigue["rutas"][0]["valor_transporte"]) == D(NAPOLES)


def test_una_ruta_borrada_se_rechaza(client, base_datos):
    """A una ruta que ya se botó no se le puede poner tarifa.

    Si se dejara, el transportador quedaría cobrando por un recorrido que ya no
    existe y la tarifa se volvería un dato huérfano imposible de mostrar (la
    pantalla no tendría ni el nombre para escribir).
    """
    h = auth_headers(client, "admin.a")
    vieja = _crear(client, h, RUTAS, {"nombre": "Ruta Vieja", "municipio": "Granada"})
    assert client.delete(f"{RUTAS}/{vieja['id']}", headers=h).status_code == 204

    r = client.post(TRANSPORTADORES, headers=h, json={
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [{"ruta_id": vieja["id"], "valor_transporte": NAPOLES}],
    })
    print("\n===== 3b. LA RUTA BORRADA =====")
    print(f"  ruta eliminada → {r.status_code} · {_detalle(r)[:80]}")
    assert r.status_code == 422, r.text
    assert "está eliminada" in _detalle(r)


def test_una_ruta_que_no_existe_se_rechaza(client, base_datos):
    """Un id inventado tampoco entra: 422 con mensaje, no un 500 del FK."""
    h = auth_headers(client, "admin.a")
    r = client.post(TRANSPORTADORES, headers=h, json={
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [{"ruta_id": str(uuid.uuid4()), "valor_transporte": NAPOLES}],
    })
    print("\n===== 3c. UNA RUTA INVENTADA =====")
    print(f"  id que no existe → {r.status_code} · {_detalle(r)[:80]}")
    assert r.status_code == 422, r.text
    assert r.status_code != 500


def test_la_misma_ruta_dos_veces_se_rechaza(client, base_datos):
    """Nápoles con $242,76 Y con $300: no se colapsa en silencio, se rebota.

    Quedarse con una de las dos sería adivinar cuál quiso, y adivinar acá es
    escoger a mano cuánta plata se le paga. El mensaje dice el NOMBRE de la ruta
    repetida para que el usuario sepa cuál arreglar.
    """
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})

    r = client.post(TRANSPORTADORES, headers=h, json={
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [
            {"ruta_id": napoles["id"], "valor_transporte": NAPOLES},
            {"ruta_id": napoles["id"], "valor_transporte": MIRA_VALLE},
        ],
    })
    print("\n===== 3d. LA MISMA RUTA DOS VECES =====")
    print(f"  Napoles a $242,76 y a $300 → {r.status_code} · {_detalle(r)[:110]}")
    assert r.status_code == 422, r.text
    assert "Napoles" in _detalle(r), "el mensaje tiene que decir cuál ruta está repetida"
    assert client.get(TRANSPORTADORES, headers=h).json()["items"] == []

    # Repetida con la MISMA tarifa también: no hay razón para mandarla dos veces y
    # aceptarla escondería un error del formulario.
    r = client.post(TRANSPORTADORES, headers=h, json={
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [
            {"ruta_id": napoles["id"], "valor_transporte": NAPOLES},
            {"ruta_id": napoles["id"], "valor_transporte": NAPOLES},
        ],
    })
    print(f"  Napoles dos veces a $242,76 → {r.status_code}")
    assert r.status_code == 422


def test_una_tarifa_negativa_por_ruta_se_rechaza(client, base_datos):
    """Nadie le paga negativo a un transportador, ni en una ruta ni en la general."""
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    r = client.post(TRANSPORTADORES, headers=h, json={
        "nombre": "Alex Agudelo",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "-242.76"}],
    })
    print("\n===== 3e. TARIFA NEGATIVA POR RUTA =====")
    print(f"  -242,76 en Napoles → {r.status_code}")
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 4. EL PUT ES PARCIAL: NO MANDAR RUTAS NO ES BORRARLAS
# ---------------------------------------------------------------------------
def test_actualizar_sin_mandar_rutas_no_las_toca(client, base_datos):
    """Guardar el teléfono NO le puede borrar las tarifas.

    Es el defecto que se evita con el tercer estado del campo: el diálogo que solo
    editó el teléfono no manda `rutas`, y si "no viene" se tratara como "déjalo sin
    ninguna", el próximo día de leche saldría a la tarifa general y nadie sabría por
    qué la quincena dio distinto.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")

    r = client.put(f"{TRANSPORTADORES}/{d['alex']['id']}",
                   json={"telefono": "3115557788"}, headers=h)
    assert r.status_code == 200, r.text

    print("\n===== 4. GUARDAR SIN MANDAR RUTAS =====")
    print(f"  se guardó el teléfono: {r.json()['telefono']}")
    print(f"  y sus rutas siguen   : "
          f"{sorted((f['nombre'], str(D(f['valor_transporte']))) for f in r.json()['rutas'])}")
    por_nombre = _rutas_por_nombre(r.json())
    assert set(por_nombre) == {"Napoles", "Mira Valle"}
    assert D(por_nombre["Napoles"]["valor_transporte"]) == D("242.76")

    # Y en la base también, no solo en la respuesta
    releido = client.get(f"{TRANSPORTADORES}/{d['alex']['id']}", headers=h).json()
    assert len(releido["rutas"]) == 2


def test_mandar_una_lista_vacia_le_quita_todas_las_rutas(client, base_datos):
    """`rutas: []` sí es una orden: déjalo sin ninguna ruta.

    Después de eso el transportador cobra su tarifa general en todo lado, que es
    exactamente como funcionaba el sistema antes de este cambio.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")

    r = client.put(f"{TRANSPORTADORES}/{d['alex']['id']}", json={"rutas": []}, headers=h)
    assert r.status_code == 200, r.text

    print("\n===== 4b. MANDAR [] LAS QUITA =====")
    print(f"  rutas después de []: {r.json()['rutas']}")
    assert r.json()["rutas"] == []
    assert client.get(f"{TRANSPORTADORES}/{d['alex']['id']}", headers=h).json()["rutas"] == []

    # Y el flete de un día nuevo en Nápoles sale ahora a la general: 82 × 130
    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-05", "proveedor_id": d["libardo"]["id"],
        "transportador_id": d["alex"]["id"], "cantidad_litros": "82",
    })
    print(f"  un día en Napoles ahora: 82 L × $130 = ${D(recepcion['valor_transporte']):,.2f}")
    assert D(recepcion["valor_transporte"]) == D("10660.00")


def test_mandar_rutas_reemplaza_la_lista_completa(client, base_datos):
    """La lista que llega es LA lista: lo que no venga se va.

    Se deja clavado porque es la semántica que la pantalla asume (el diálogo manda
    la tabla completa de rutas cada vez que se guarda), y porque hace falta poder
    QUITARLE una ruta a alguien que dejó de hacerla.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")

    r = client.put(f"{TRANSPORTADORES}/{d['alex']['id']}", headers=h, json={
        "rutas": [{"ruta_id": d["mira_valle"]["id"], "valor_transporte": "310"}],
    })
    assert r.status_code == 200, r.text

    print("\n===== 4c. LA LISTA QUE LLEGA ES LA LISTA =====")
    print(f"  antes: Napoles $242,76 · Mira Valle $300")
    print(f"  ahora: {[(f['nombre'], str(D(f['valor_transporte']))) for f in r.json()['rutas']]}")
    por_nombre = _rutas_por_nombre(r.json())
    assert set(por_nombre) == {"Mira Valle"}
    assert D(por_nombre["Mira Valle"]["valor_transporte"]) == D("310")

    # Y volver a agregar Nápoles funciona: el único de (transportador, ruta) no se
    # queda ocupado por la fila que se quitó (se borra de verdad, no en suave).
    r = client.put(f"{TRANSPORTADORES}/{d['alex']['id']}", headers=h, json={
        "rutas": [
            {"ruta_id": d["napoles"]["id"], "valor_transporte": NAPOLES},
            {"ruta_id": d["mira_valle"]["id"], "valor_transporte": "310"},
        ],
    })
    print(f"  y volver a agregar Napoles: {r.status_code} · "
          f"{sorted(f['nombre'] for f in r.json()['rutas'])}")
    assert r.status_code == 200, r.text
    assert len(r.json()["rutas"]) == 2


# ---------------------------------------------------------------------------
# 5. LAS FOTOS: CAMBIAR LA TARIFA HOY NO MUEVE LO YA RECIBIDO
# ---------------------------------------------------------------------------
def test_cambiar_la_tarifa_de_una_ruta_no_le_mueve_la_plata_a_lo_ya_guardado(
    client, base_datos
):
    """El flete de cada día es una FOTO del momento, y así tiene que quedarse.

    82 L en Nápoles a $242,76 dieron $19.906,32. Si mañana Alex sube Nápoles a $300,
    ese día NO se puede volver $24.600: lo que ya se recogió se pagó a la tarifa de
    ese momento, y es justo lo que hace que un comprobante viejo siga cuadrando
    contra sus recepciones. Lo que se reciba DESPUÉS sí va a la tarifa nueva.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")

    vieja = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": d["libardo"]["id"],
        "transportador_id": d["alex"]["id"], "cantidad_litros": "82",
    })
    antes = D(vieja["valor_transporte"])

    subida = client.put(f"{TRANSPORTADORES}/{d['alex']['id']}", headers=h, json={
        "rutas": [
            {"ruta_id": d["napoles"]["id"], "valor_transporte": MIRA_VALLE},
            {"ruta_id": d["mira_valle"]["id"], "valor_transporte": MIRA_VALLE},
        ],
    })
    assert subida.status_code == 200, subida.text

    despues = D(client.get(f"{RECEPCIONES}/{vieja['id']}", headers=h).json()["valor_transporte"])
    nueva = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-10", "proveedor_id": d["libardo"]["id"],
        "transportador_id": d["alex"]["id"], "cantidad_litros": "82",
    })

    print("\n===== 5. LA FOTO NO SE MUEVE =====")
    print(f"  el día del 01/06 con Napoles a $242,76 : ${antes:,.2f}")
    print(f"  tras subir Napoles a $300, ese día sigue: ${despues:,.2f}  (la foto)")
    print(f"  y un día nuevo del 10/06 sí va a $300  : ${D(nueva['valor_transporte']):,.2f}")
    assert antes == despues == D("19906.32")
    assert D(nueva["valor_transporte"]) == D("82") * D("300") == D("24600.00")


# ---------------------------------------------------------------------------
# 6. LA AUDITORÍA VE EL CAMBIO DE TARIFA
# ---------------------------------------------------------------------------
def test_la_auditoria_registra_las_rutas_y_sus_tarifas(client, base_datos):
    """Subir Nápoles de $242,76 a $300 tiene que dejar rastro.

    `serialize_entity` solo mira columnas, y las tarifas por ruta viven en otra
    tabla: sin agregarlas a mano, la auditoría mostraría un "editar" con el antes
    idéntico al después. Sería un cambio de PLATA sin rastro, y es justo el que hay
    que poder reconstruir cuando el dueño pregunte por qué la quincena dio distinto.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")

    client.put(f"{TRANSPORTADORES}/{d['alex']['id']}", headers=h, json={
        "rutas": [{"ruta_id": d["napoles"]["id"], "valor_transporte": MIRA_VALLE}],
    })

    registros = client.get(
        f"{AUDITORIA}?modulo=transportadores&accion=editar", headers=h
    ).json()["items"]
    assert registros, "el cambio de tarifas no quedó auditado"
    cambio = registros[0]

    tarifas_antes = {f["nombre"]: f["valor_transporte"] for f in cambio["antes"]["rutas"]}
    tarifas_despues = {f["nombre"]: f["valor_transporte"] for f in cambio["despues"]["rutas"]}
    print("\n===== 6. LA AUDITORÍA =====")
    print(f"  antes  : {tarifas_antes}")
    print(f"  después: {tarifas_despues}")
    assert tarifas_antes == {"Napoles": 242.76, "Mira Valle": 300.0}
    assert tarifas_despues == {"Napoles": 300.0}
    assert cambio["antes"]["rutas"] != cambio["despues"]["rutas"]

    # Y al crear también queda la lista con la que nació
    creado = client.get(f"{AUDITORIA}?modulo=transportadores&accion=crear", headers=h).json()["items"]
    nacimiento = [reg for reg in creado if reg["entidad_id"] == d["alex"]["id"]][0]
    print(f"  al nacer: {[f['nombre'] for f in nacimiento['despues']['rutas']]}")
    assert len(nacimiento["despues"]["rutas"]) == 2
    assert nacimiento["antes"] is None


# ---------------------------------------------------------------------------
# 7. EL CANDADO: CON EL FLETE PAGADO, LA RUTA YA NO SE TOCA
# ---------------------------------------------------------------------------
def test_con_el_flete_pagado_la_ruta_del_dia_queda_trabada(client, base_datos):
    """La ruta entró en la plata, así que el flete pagado la traba.

    Antes la ruta era una etiqueta y se dejaba corregir aunque las dos
    liquidaciones estuvieran pagadas. Ahora la ruta escoge la tarifa: cambiarla
    recalcularía el flete guardado y el comprobante que el transportador tiene en la
    mano dejaría de cuadrar contra sus recepciones. Se prueba desde el API para que
    quede claro que el guardia está en el backend y no en la pantalla.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")
    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": d["libardo"]["id"],
        "transportador_id": d["alex"]["id"], "cantidad_litros": "82",
    })

    generadas = client.post("/api/v1/liquidaciones/generar", headers=h, json={
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "transportador",
    })
    assert generadas.status_code in (200, 201), generadas.text
    flete = generadas.json()[0]
    assert client.post(
        f"/api/v1/liquidaciones/{flete['id']}/aprobar", headers=h
    ).status_code == 200
    pago = client.post(f"/api/v1/liquidaciones/{flete['id']}/pagar", headers=h)
    assert pago.status_code == 200, pago.text

    estado = client.get(f"{RECEPCIONES}/{recepcion['id']}", headers=h).json()
    print("\n===== 7. CON EL FLETE PAGADO, LA RUTA SE TRABA =====")
    print(f"  flete de Alex: ${D(flete['valor_total']):,.2f} (pagado)")
    print(f"  trabados : {estado['campos_bloqueados']}")
    print(f"  editables: {estado['campos_editables']}")
    assert D(flete["valor_total"]) == D("19906.32")
    assert "ruta_id" in estado["campos_bloqueados"]

    r = client.put(f"{RECEPCIONES}/{recepcion['id']}", headers=h,
                   json={"ruta_id": d["mira_valle"]["id"]})
    print(f"  pasarlo a Mira Valle: {r.status_code} · {_detalle(r)[:90]}")
    assert r.status_code == 422, r.text

    # Y el flete pagado no se movió ni un peso
    sigue = client.get(f"/api/v1/liquidaciones/{flete['id']}", headers=h).json()
    print(f"  el flete sigue en ${D(sigue['valor_total']):,.2f} ({sigue['estado']})")
    assert D(sigue["valor_total"]) == D("19906.32")


# ---------------------------------------------------------------------------
# 8. LA MIGRACIÓN DE LO QUE EL CLIENTE YA TIENE CARGADO
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
        / "alembic" / "versions" / "c6b1e4a8d3f7_tarifa_por_ruta_del_transportador.py"
    )
    spec = importlib.util.spec_from_file_location("migracion_tarifa_por_ruta", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# (nombre, ruta, tarifa, estado) tal como pueden estar HOY en la base del cliente.
TRANSPORTADORES_VIEJOS = [
    ("Alex Agudelo", "Napoles", "242.76", "activo"),
    ("Stella", "Mira Valle", "130", "activo"),
    # Inactivo y borrado en suave: TAMBIÉN se migran. Una liquidación vieja de un
    # transportador retirado todavía se puede recuadrar, y sin su tarifa copiada
    # ese recálculo la sacaría de la general y le cambiaría el comprobante.
    ("Yoiner", "Napoles", "94.03", "inactivo"),
    ("Eduin", "Mira Valle", "0", "activo"),
]


@pytest.fixture()
def base_vieja():
    """Una base con la FORMA VIEJA: `transportadores.ruta_id` y una sola tarifa.

    Es el estado exacto de la base del cliente el minuto antes de migrar. La
    columna se agrega a mano porque el modelo ya no la tiene: `create_all` arma la
    tabla nueva y el ALTER la devuelve al estado anterior.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE transportadores ADD COLUMN ruta_id CHAR(32)"))

    sesion = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    empresa = Empresa(nombre="Quesera Vieja", nit="900V")
    sesion.add(empresa)
    sesion.flush()
    rutas = {}
    for nombre in ("Napoles", "Mira Valle"):
        ruta = Ruta(empresa_id=empresa.id, nombre=nombre, municipio="Granada")
        sesion.add(ruta)
        rutas[nombre] = ruta
    sesion.flush()
    sesion.commit()

    # Las filas viejas se insertan por SQL, no por el ORM: el modelo ya no tiene
    # `ruta_id` y no sabría escribirla.
    viejos = []
    tabla = Transportador.__table__
    with engine.begin() as conn:
        for nombre, ruta_nombre, tarifa, estado in TRANSPORTADORES_VIEJOS:
            trans_id = uuid.uuid4()
            conn.execute(
                insert(tabla).values(
                    id=trans_id, empresa_id=empresa.id, nombre=nombre,
                    valor_transporte=Decimal(tarifa), estado=estado,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    deleted_at=(
                        datetime.now(timezone.utc) if nombre == "Eduin" else None
                    ),
                )
            )
            conn.execute(
                text("UPDATE transportadores SET ruta_id = :ruta WHERE id = :id").bindparams(
                    ruta=rutas[ruta_nombre].id.hex, id=trans_id.hex
                )
            )
            viejos.append((nombre, ruta_nombre, Decimal(tarifa), trans_id))
    try:
        yield {"engine": engine, "sesion": sesion, "viejos": viejos, "rutas": rutas}
    finally:
        sesion.close()
        Base.metadata.drop_all(bind=engine)


def test_la_migracion_copia_la_ruta_y_la_tarifa_sin_mover_un_centavo(base_vieja):
    """Cada transportador que ya tenía ruta queda con su fila en la tabla puente,
    con la MISMA tarifa, incluidos el inactivo y el borrado en suave.

    Si no se copiara, el primer día que se reciba leche después de desplegar la
    tarifa saldría de la general —y para un transportador cuya tarifa vivía en su
    única ruta eso podría ser un cero callado, el señor trabajando gratis hasta que
    alguien cuadre la quincena—.
    """
    engine = base_vieja["engine"]
    sesion = base_vieja["sesion"]
    migracion = _cargar_migracion()

    assert sesion.scalar(select(TransportadorRuta)) is None, "la base ya tenía filas puente"

    with engine.begin() as conn:
        creadas = migracion.backfill_rutas_de_transportadores(conn)

    sesion.expire_all()
    print("\n===== 8. LA MIGRACIÓN DE LAS TARIFAS QUE YA EXISTEN =====")
    print(f"  filas creadas: {creadas}")
    print(f"  {'transportador':<16}{'ruta':<13}{'tarifa vieja':>14}{'tarifa nueva':>15}")

    nombres_de_ruta = {r.id: r.nombre for r in base_vieja["rutas"].values()}
    assert creadas == len(TRANSPORTADORES_VIEJOS)

    for nombre, ruta_nombre, tarifa_vieja, trans_id in base_vieja["viejos"]:
        filas = sesion.scalars(
            select(TransportadorRuta).where(TransportadorRuta.transportador_id == trans_id)
        ).all()
        assert len(filas) == 1, f"{nombre} tenía que quedar con UNA fila"
        fila = filas[0]
        print(f"  {nombre:<16}{nombres_de_ruta[fila.ruta_id]:<13}"
              f"{tarifa_vieja:>14,.2f}{Decimal(fila.valor_transporte):>15,.2f}")
        assert nombres_de_ruta[fila.ruta_id] == ruta_nombre
        # LA TARIFA SE COPIA, NO SE RECALCULA: ni un centavo de diferencia.
        assert Decimal(fila.valor_transporte) == tarifa_vieja

        # Y la tarifa general del transportador tampoco se movió
        transportador = sesion.get(Transportador, trans_id)
        assert Decimal(transportador.valor_transporte) == tarifa_vieja

    # Correr el backfill sobre una base sin la columna llena no crea nada
    with engine.begin() as conn:
        conn.execute(text("UPDATE transportadores SET ruta_id = NULL"))
        assert migracion.backfill_rutas_de_transportadores(conn) == 0


def test_el_downgrade_devuelve_una_ruta_y_avisa_de_las_que_se_pierden(base_vieja):
    """Al bajar la migración, un transportador con DOS rutas queda con UNA.

    No hay forma de evitarlo: la columna `transportadores.ruta_id` cabe una sola. Lo
    que sí se cuida es cuál se devuelve —la de la tarifa MÁS ALTA— y que el
    transportador no quede con tarifa cero: si toda su tarifa vivía en las rutas y
    la general estaba en cero, se le escribe la de la ruta escogida. Dejarlo en cero
    sería mandarlo a trabajar gratis.
    """
    engine = base_vieja["engine"]
    sesion = base_vieja["sesion"]
    migracion = _cargar_migracion()
    napoles = base_vieja["rutas"]["Napoles"]
    mira_valle = base_vieja["rutas"]["Mira Valle"]

    # Alex hace LAS DOS rutas, y su tarifa general quedó en cero porque el dueño
    # solo llenó las de las rutas. Ese es el caso peligroso del downgrade.
    _, _, _, alex_id = base_vieja["viejos"][0]
    with engine.begin() as conn:
        conn.execute(text("UPDATE transportadores SET ruta_id = NULL, valor_transporte = 0"))
        conn.execute(
            insert(TransportadorRuta.__table__).values(
                id=uuid.uuid4(), transportador_id=alex_id, ruta_id=napoles.id,
                valor_transporte=Decimal("242.76"),
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
        )
        conn.execute(
            insert(TransportadorRuta.__table__).values(
                id=uuid.uuid4(), transportador_id=alex_id, ruta_id=mira_valle.id,
                valor_transporte=Decimal("300"),
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
        )

    with engine.begin() as conn:
        devueltos = migracion.restaurar_una_ruta_por_transportador(conn)
        fila = conn.execute(
            text("SELECT ruta_id, valor_transporte FROM transportadores WHERE id = :id")
            .bindparams(id=alex_id.hex)
        ).one()

    sesion.expire_all()
    print("\n===== 8b. EL DOWNGRADE =====")
    print(f"  Alex hacía Napoles $242,76 y Mira Valle $300 (general en $0)")
    print(f"  transportadores devueltos a una ruta: {devueltos}")
    print(f"  le quedó la ruta {'Mira Valle' if fila[0] == mira_valle.id.hex else 'Napoles'} "
          f"y la tarifa ${Decimal(str(fila[1])):,.2f}")
    print(f"  SE PIERDE la otra ruta: la columna vieja cabe una sola")
    assert devueltos == 1
    assert fila[0] == mira_valle.id.hex, "tenía que quedarse con la tarifa MÁS ALTA"
    assert Decimal(str(fila[1])) == Decimal("300"), "no puede quedar en cero: trabajaría gratis"


# ---------------------------------------------------------------------------
# 9. EL HELPER, probado directo (lo van a usar recepción Y liquidaciones)
# ---------------------------------------------------------------------------
def test_el_helper_de_tarifa_resuelve_los_cuatro_casos(client, base_datos, db_session):
    """`tarifa_por_litro` es la ÚNICA cuenta de qué tarifa aplica, y la comparten
    recepción y liquidaciones.

    Se prueba directo porque de esta función cuelgan las dos hojas del sistema que
    manejan el flete: antes la fórmula estaba repetida en cuatro sitios y así se
    desincronizan. Los cuatro casos son los de la regla, en orden.
    """
    from app.modules.transportadores.tarifas import tarifa_por_litro

    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h, general="130")
    otra_ruta = _crear(client, h, RUTAS, {"nombre": "La Esperanza"})

    alex = db_session.get(Transportador, uuid.UUID(d["alex"]["id"]))
    casos = [
        ("la ruta con tarifa propia (Napoles)", uuid.UUID(d["napoles"]["id"]), D("242.76")),
        ("la otra ruta con tarifa (Mira Valle)", uuid.UUID(d["mira_valle"]["id"]), D("300")),
        ("una ruta sin tarifa propia", uuid.UUID(otra_ruta["id"]), D("130")),
        ("sin ruta", None, D("130")),
    ]
    print("\n===== 9. EL HELPER DE TARIFA =====")
    for etiqueta, ruta_id, esperado in casos:
        obtenido = tarifa_por_litro(alex, ruta_id)
        print(f"  {etiqueta:<38} → ${obtenido:>9,.2f}   (esperado ${esperado:,.2f})")
        assert obtenido == esperado
        assert isinstance(obtenido, Decimal), "esto se multiplica por litros: Decimal, no float"

    # Sin transportador no hay flete que cobrar
    print(f"  {'sin transportador':<38} → ${tarifa_por_litro(None, None):>9,.2f}")
    assert tarifa_por_litro(None, uuid.UUID(d["napoles"]["id"])) == D("0")


def test_el_flete_de_la_grilla_y_del_resumen_suma_las_dos_rutas(client, base_datos):
    """El resumen del período suma las dos rutas: $19.906,32 + $28.500 = $48.406,32.

    La grilla de la quincena y el resumen son lo que el dueño mira primero, y suman
    lo GUARDADO en cada recepción. Se comprueba que el total grande sea exactamente
    la suma de los dos días a tarifas distintas: si el resumen usara una sola
    tarifa, acá se vería.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario_dos_rutas(client, h)
    for proveedor, litros in ((d["libardo"], "82"), (d["carmen"], "95")):
        _crear(client, h, RECEPCIONES, {
            "fecha": "2026-06-01", "proveedor_id": proveedor["id"],
            "transportador_id": d["alex"]["id"], "cantidad_litros": litros,
        })

    grilla = client.get(
        "/api/v1/recepciones/grilla/quincena?desde=2026-06-01&hasta=2026-06-15", headers=h
    ).json()
    print("\n===== 10. EL RESUMEN SUMA LAS DOS RUTAS =====")
    print(f"  total transporte de la quincena: ${D(grilla['total_transporte']):,.2f}"
          f"   (a mano: $48.406,32)")
    assert D(grilla["total_transporte"]) == D("48406.32")

    # Y lo guardado en la base, fila por fila
    lista = client.get(RECEPCIONES, headers=h).json()["items"]
    suma = sum((D(r["valor_transporte"]) for r in lista), D(0))
    for r in lista:
        print(f"    {r['fecha']}  {D(r['cantidad_litros']):>6,.2f} L → "
              f"${D(r['valor_transporte']):>12,.2f}")
    print(f"  suma de las recepciones        : ${suma:,.2f}")
    assert suma == D("48406.32")
