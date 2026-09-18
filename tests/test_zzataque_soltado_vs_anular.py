"""ATAQUE — LA MARCA NUEVA (`Anticipo.soltado_de_liquidacion_id`) CONTRA ANULAR Y BORRAR.

La marca nace de "Corregir esta quincena": cuando una corrección SACA un adelanto de un
comprobante ya entregado, ese adelanto queda con `liquidacion_id = None` —idéntico a uno
recién registrado— y el candado de la pantalla de anticipos se abría sin preguntar. La
marca cierra esa puerta: mientras el adelanto esté suelto Y marcado, `PUT` y `DELETE`
rebotan con 422.

LO QUE SE MIDE AQUÍ ES EL OTRO LADO: `_soltar_lo_apartado`, que corre al ANULAR (y al
borrar en suave) una liquidación, pone `liquidacion_id = None` en todos sus anticipos y
NO mira la marca nueva. Y `_aplicar_anticipos_pendientes` / `pendientes_de`, que recogen
los sueltos al generar, filtran por `liquidacion_id IS NULL` y TAMPOCO la miran ni la
limpian. Son tres caminos que mueven la misma columna con tres criterios distintos, y la
pregunta con plata es si entre ellos queda un hueco por donde se escape un adelanto —o,
al revés, un cepo donde el dueño quede trabado sin camino.

LAS TRES SECUENCIAS DEL ENCARGO, con las cifras montadas de verdad:

  (a) La quincena 1 suelta un adelanto por corrección (queda marcado), la quincena 2 lo
      recoge, y se ANULA la quincena 2. MEDIDO: el adelanto vuelve a quedar suelto CON LA
      MARCA PUESTA —`_aplicar_anticipos_pendientes` nunca la limpió—, sigue rebotando el
      `PUT` y el `DELETE`, la pantalla lo muestra `bloqueado`, y la quincena 3 lo vuelve a
      recoger una sola vez. El dueño NO queda trabado: la salida que promete el mensaje
      —devolverlo con 'Corregir esta quincena'— sigue abierta después de anular.
  (b) Un adelanto normal, que nunca pasó por una corrección, en una quincena que se
      ANULA: sigue editable y borrable. La marca no lo tocó y no tenía por qué.
  (c) Una liquidación CORREGIDA no se puede anular (`version > 1`). Sigue rebotando —bien—
      PERO EL MENSAJE QUE LE SALE AL DUEÑO ES EL EQUIVOCADO, y eso cuesta plata: ver el
      xfail de abajo.

DOS DEFECTOS REPRODUCIDOS, los dos marcados `xfail(strict=True)` y ninguno arreglado:

  1. EL MURO CON EL LETRERO CAMBIADO (`anular`, orden de los guardias). En una quincena
     corregida con un pago encima, el primer guardia que se dispara es `tiene_pagos`, que
     dice "elimine primero los pagos" —una salida con nombre—. El dueño la sigue, borra un
     pago de $200.000 CON SUS SOPORTES (las fotos de la transferencia se van del bucket y
     no vuelven), y al volver a oprimir Anular choca con el muro de verdad: `version > 1`,
     que no se abre nunca. Queda peor que como empezó: la quincena dice `pagado $0` y
     `saldo $500.000` cuando de la caja ya salieron $200.000.
  2. EL ADELANTO QUE NO TIENE DÓNDE CAER. El productor entregó su última leche en la
     quincena 1; una corrección le sacó el adelanto de $300.000. Las dos salidas que
     nombra el mensaje se cierran a la vez: "Generar" no produce nada (sin leche no hay
     quincena siguiente) y devolverlo a la quincena 1 lo deja adentro de una PAGADA, donde
     el candado normal vuelve a rebotar el `DELETE`. Antes de la marca ese adelanto suelto
     se borraba; ahora no hay pantalla que lo borre.

LA REGLA DE LA CASA se comprueba en cada paso con `_cuadra`: los días suman el valor
total, `neto = valor_total − anticipos − saldo_anterior`, y `neto = pagado + saldo`.
"""
import uuid as _uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.liquidaciones.models import Anticipo
from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
ANTICIPOS = "/api/v1/anticipos"

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
Q3 = ("2026-07-01", "2026-07-15")


def D(v):
    return Decimal(str(v))


CERO = D(0)


def _detalle(r):
    """El texto del error que lee el dueño en la pantalla."""
    try:
        return r.json().get("error", {}).get("detail", "")
    except Exception:  # pragma: no cover - respuestas sin cuerpo
        return ""


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": str(precio)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _dia(client, h, prov_id, fecha, litros):
    r = client.post(
        "/api/v1/recepciones",
        json={"fecha": fecha, "proveedor_id": prov_id, "cantidad_litros": str(litros)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, prov_id, fecha, valor, obs=None):
    r = client.post(
        ANTICIPOS,
        json={
            "tipo": "proveedor",
            "proveedor_id": prov_id,
            "fecha": fecha,
            "valor": str(valor),
            "observaciones": obs,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, prov_id, periodo):
    """Genera el período y devuelve la liquidación de ese proveedor, o None si no salió."""
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    hallada = [x for x in r.json()["generadas"] if x["proveedor_id"] == prov_id]
    assert len(hallada) <= 1, hallada
    return hallada[0] if hallada else None


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _ver_anticipo(client, h, ant_id):
    r = client.get(f"{ANTICIPOS}/{ant_id}", headers=h)
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


def _anular(client, h, liq_id):
    return client.post(f"{API}/{liq_id}/anular", headers=h)


def _corregir(client, h, liq_id, cuerpo):
    return client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)


def _soltar_el_adelanto(client, h, liq_id, ant_id, motivo):
    r = _corregir(client, h, liq_id, {"motivo": motivo, "anticipos_a_soltar": [ant_id]})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------- la lupa
def _mostrar(titulo, liq):
    print(
        f"  {titulo:<26}. estado {liq['estado']:<9}. v{liq.get('version', '?')} "
        f". total {liq['valor_total']} . anticipos {liq['anticipos']} "
        f". deuda vieja {liq.get('saldo_anterior')} . neto {liq['neto_a_pagar']} "
        f". pagado {liq['pagado']} . saldo {liq['saldo']}"
    )


def _cuadra(liq) -> bool:
    """LA REGLA DE LA CASA, la que el dueño verifica con calculadora."""
    suma_dias = sum((D(d["valor"]) for d in liq["detalles"]), CERO)
    neto = D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq.get("saldo_anterior") or 0)
    return (
        suma_dias == D(liq["valor_total"])
        and D(liq["neto_a_pagar"]) == neto
        and D(liq["neto_a_pagar"]) == D(liq["pagado"]) + D(liq["saldo"])
    )


def _marca(db_session, ant_id):
    """Las DOS columnas crudas del adelanto: dónde está aplicado y de dónde lo soltaron.

    Se leen de la fila y no de la API porque `soltado_de_liquidacion_id` no se expone en
    `AnticipoRead`: lo que la pantalla ve de ella es el booleano `bloqueado`, y parte de
    lo que se mide aquí es justamente que los dos digan lo mismo.
    """
    fila = db_session.scalars(
        select(Anticipo).where(Anticipo.id == _uuid.UUID(ant_id))
    ).first()
    assert fila is not None
    db_session.refresh(fila)
    return fila.liquidacion_id, fila.soltado_de_liquidacion_id


def _intentar_tocar(client, h, ant_id):
    """Las dos puertas de la pantalla de anticipos, tal cual las oprime el dueño."""
    editar = client.put(f"{ANTICIPOS}/{ant_id}", json={"valor": "1"}, headers=h)
    borrar = client.delete(f"{ANTICIPOS}/{ant_id}", headers=h)
    print(f"    PUT    /anticipos/id -> {editar.status_code} . {_detalle(editar)[:90]}")
    print(f"    DELETE /anticipos/id -> {borrar.status_code} . {_detalle(borrar)[:90]}")
    return editar, borrar


# --------------------------------------------------------- montajes compartidos
def _q1_pagada_con_adelanto(client, h, nombre="Henri"):
    """250 L a $2.000 = $500.000, menos $300.000 de adelanto: se le entregan $200.000.

    Es el papel que el productor tiene en la mano cuando empieza cada prueba:
        VALOR TOTAL   $500.000
        anticipos    -$300.000
        NETO          $200.000  = pagado $200.000 + saldo $0   -> 'pagada'
    """
    prov = _proveedor(client, h, nombre, 2000)
    _dia(client, h, prov["id"], "2026-06-02", 250)
    ant = _anticipo(client, h, prov["id"], "2026-06-03", 300000, "adelanto del 3")
    liq = _generar(client, h, prov["id"], Q1)
    assert D(liq["valor_total"]) == D(500000)
    assert D(liq["anticipos"]) == D(300000)
    assert D(liq["neto_a_pagar"]) == D(200000)
    _aprobar(client, h, liq["id"])
    pagada = _pagar(client, h, liq["id"])
    assert pagada["estado"] == "pagada"
    assert D(pagada["pagado"]) == D(200000)
    assert _cuadra(pagada)
    return prov, ant, pagada


def _q1_soltando_el_adelanto(client, h, nombre="Henri"):
    """La quincena 1 pagada, y después corregida para SACARLE el adelanto.

        VALOR TOTAL   $500.000
        anticipos           $0   <- el adelanto salió: va en la siguiente
        NETO          $500.000  = pagado $200.000 + saldo $300.000  -> 'parcial', v2

    El adelanto queda suelto y MARCADO: `liquidacion_id = None`,
    `soltado_de_liquidacion_id = <la quincena 1>`.
    """
    prov, ant, pagada = _q1_pagada_con_adelanto(client, h, nombre)
    q1v2 = _soltar_el_adelanto(
        client, h, pagada["id"], ant["id"], "el adelanto era de la quincena siguiente"
    )
    assert q1v2["version"] == 2
    assert D(q1v2["anticipos"]) == CERO
    assert D(q1v2["valor_total"]) == D(500000)
    assert D(q1v2["neto_a_pagar"]) == D(500000)
    assert D(q1v2["pagado"]) == D(200000)
    assert D(q1v2["saldo"]) == D(300000)
    assert _cuadra(q1v2)
    return prov, ant, q1v2


# ===========================================================================
# (a) ANULAR LA QUINCENA QUE RECOGIÓ EL ADELANTO MARCADO
# ===========================================================================
def test_anular_la_quincena_que_lo_recogio_devuelve_el_adelanto_todavia_trabado(
    client, base_datos, db_session
):
    """La secuencia completa del encargo, con plata montada:

    Q1 (01–15/06): 250 L a $2.000 = $500.000 − $300.000 de adelanto = $200.000 pagados.
    Se corrige Q1 sacándole el adelanto -> el papel v2 dice "se le descuenta en la
    siguiente" y el adelanto queda suelto y marcado.
    Q2 (16–30/06): 250 L a $2.000 = $500.000 − $300.000 (lo recogió) = $200.000 de neto.
    Se ANULA Q2.

    LO QUE SE MIDE: `_soltar_lo_apartado` le pone `liquidacion_id = None` y NO toca la
    marca —y como `_aplicar_anticipos_pendientes` tampoco la había limpiado al recogerlo,
    la marca sigue apuntando a Q1—. El resultado es el correcto y por eso esta prueba
    está en verde: el adelanto vuelve al mismo estado de antes de que Q2 existiera
    —suelto, trabado, con la promesa del papel v2 todavía sin cumplir— y no queda una
    ventana en la que los $300.000 se puedan borrar o rebajar a $1.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, q1v2 = _q1_soltando_el_adelanto(client, h)

    print("\n===== (a) ANULAR LA QUINCENA QUE LO RECOGIÓ =====")
    _mostrar("Q1 v2 (soltó el adelanto)", q1v2)
    assert _marca(db_session, ant["id"]) == (None, _uuid.UUID(q1v2["id"])), (
        "al soltarlo, la corrección tiene que dejar escrito de qué quincena salió"
    )

    # Q2 lo recoge: 250 L a $2.000 = $500.000 - $300.000 = $200.000.
    _dia(client, h, prov["id"], "2026-06-20", 250)
    q2 = _generar(client, h, prov["id"], Q2)
    _mostrar("Q2 borrador (lo recogió)", q2)
    assert D(q2["valor_total"]) == D(500000)
    assert D(q2["anticipos"]) == D(300000), "la quincena siguiente tenía que recogerlo"
    assert D(q2["neto_a_pagar"]) == D(200000)
    assert _cuadra(q2)
    aplicado, marcado = _marca(db_session, ant["id"])
    print(f"    marca tras recogerlo: liquidacion_id={aplicado} soltado_de={marcado}")
    assert aplicado == _uuid.UUID(q2["id"])
    assert marcado == _uuid.UUID(q1v2["id"]), (
        "`_aplicar_anticipos_pendientes` no limpia la marca al recogerlo"
    )

    # ANULAR Q2.
    anulada = _anular(client, h, q2["id"])
    print(f"    ANULAR Q2 -> {anulada.status_code} {_detalle(anulada)}")
    assert anulada.status_code == 200, anulada.text
    assert anulada.json()["estado"] == "anulada"

    aplicado, marcado = _marca(db_session, ant["id"])
    print(f"    marca tras anular:    liquidacion_id={aplicado} soltado_de={marcado}")
    assert aplicado is None, "anular tenía que soltar el adelanto"
    assert marcado == _uuid.UUID(q1v2["id"]), (
        "y la marca tiene que seguir puesta: la promesa del papel v2 de Q1 sigue sin "
        "cumplirse, así que el adelanto no puede volver a quedar borrable"
    )

    # La pantalla y el servidor dicen lo mismo.
    visto = _ver_anticipo(client, h, ant["id"])
    print(f"    la pantalla lo ve: bloqueado={visto['bloqueado']} "
          f"liquidacion_id={visto['liquidacion_id']} aplicado={visto['aplicado']}")
    assert visto["liquidacion_id"] is None
    assert visto["bloqueado"] is True, (
        "si la pantalla dijera que se puede, el dueño oprimiría un botón que el "
        "servidor rebota"
    )

    editar, borrar = _intentar_tocar(client, h, ant["id"])
    assert editar.status_code == 422, editar.text
    assert borrar.status_code == 422, borrar.text
    assert "YA SE IMPRIMIÓ" in _detalle(editar)
    assert "Corregir esta quincena" in _detalle(borrar), (
        "el mensaje tiene que nombrar la salida, no dejarlo adivinando"
    )

    # Y NO SE MOVIÓ UN PESO: los $300.000 siguen enteros.
    assert D(_ver_anticipo(client, h, ant["id"])["valor"]) == D(300000)
    assert _cuadra(_leer(client, h, q1v2["id"]))


def test_despues_de_anular_la_quincena_siguiente_lo_vuelve_a_recoger_una_sola_vez(
    client, base_datos, db_session
):
    """Anulada la Q2, el adelanto no se pierde: la Q3 se lo descuenta, y UNA SOLA VEZ.

    Q3 (01–15/07): 250 L a $2.000 = $500.000 − $300.000 = $200.000 de neto. Si la marca
    hubiera dejado el adelanto fuera del filtro de `pendientes_de`, esos $300.000 ya
    entregados no los cobraría nadie y el productor se llevaría $500.000 por $500.000 de
    leche MÁS los $300.000 que ya tenía en el bolsillo.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, q1v2 = _q1_soltando_el_adelanto(client, h)
    _dia(client, h, prov["id"], "2026-06-20", 250)
    q2 = _generar(client, h, prov["id"], Q2)
    assert _anular(client, h, q2["id"]).status_code == 200

    print("\n===== (a) LA QUINCENA 3 LO RECOGE =====")
    _dia(client, h, prov["id"], "2026-07-02", 250)
    q3 = _generar(client, h, prov["id"], Q3)
    _mostrar("Q3 borrador", q3)
    assert D(q3["valor_total"]) == D(500000)
    assert D(q3["anticipos"]) == D(300000), (
        "los $300.000 ya entregados tienen que salir de alguna quincena"
    )
    assert D(q3["neto_a_pagar"]) == D(200000)
    assert _cuadra(q3)
    aplicado, marcado = _marca(db_session, ant["id"])
    print(f"    marca: liquidacion_id={aplicado} soltado_de={marcado}")
    assert aplicado == _uuid.UUID(q3["id"])

    # UNA SOLA VEZ: volver a oprimir Generar no lo descuenta dos veces.
    otra = _generar(client, h, prov["id"], Q3)
    assert otra is None, "el período ya está liquidado: no puede salir una segunda hoja"
    otra_vez = _leer(client, h, q3["id"])
    assert D(otra_vez["anticipos"]) == D(300000), "el adelanto se descontó dos veces"
    assert _cuadra(otra_vez)

    # Y ahí adentro, en un borrador sin pagos, el dueño SÍ puede corregirlo: es la
    # segunda salida que nombra el mensaje ("corríjalo ahí antes de pagarla").
    visto = _ver_anticipo(client, h, ant["id"])
    print(f"    dentro de Q3 borrador: bloqueado={visto['bloqueado']}")
    assert visto["bloqueado"] is False


def test_despues_de_anular_la_salida_que_promete_el_mensaje_sigue_abierta(
    client, base_datos, db_session
):
    """El candado deja al dueño con camino: la salida 1 del mensaje sigue funcionando.

    Después de anular la Q2, el mensaje del 422 dice "vuelva a incluirlo con 'Corregir
    esta quincena'". Se hace, y la cuenta vuelve a quedar como el papel v1:
        VALOR TOTAL $500.000 − anticipos $300.000 = NETO $200.000 = pagado $200.000.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, q1v2 = _q1_soltando_el_adelanto(client, h)
    _dia(client, h, prov["id"], "2026-06-20", 250)
    q2 = _generar(client, h, prov["id"], Q2)
    assert _anular(client, h, q2["id"]).status_code == 200

    print("\n===== (a) LA SALIDA DEL MENSAJE, DESPUÉS DE ANULAR =====")
    previa = client.post(
        f"{API}/{q1v2['id']}/corregir/previsualizar",
        json={"motivo": "devolverlo a la quincena de la que salió"},
        headers=h,
    )
    assert previa.status_code == 200, previa.text
    sueltos = previa.json()["anticipos_sueltos"]
    print("    adelantos que ofrece la corrección:",
          [(a["fecha"], a["valor"]) for a in sueltos])
    assert [a["anticipo_id"] for a in sueltos] == [ant["id"]], (
        "el adelanto suelto tiene que aparecer como candidato, o la salida no existe"
    )

    r = _corregir(
        client, h, q1v2["id"],
        {"motivo": "devolverlo a la quincena de la que salió",
         "anticipos_a_incluir": [ant["id"]]},
    )
    print(f"    CORREGIR (salida 1) -> {r.status_code} {_detalle(r)}")
    assert r.status_code == 200, r.text
    q1v3 = r.json()
    _mostrar("Q1 v3 (lo devolvió)", q1v3)
    assert q1v3["version"] == 3
    assert D(q1v3["anticipos"]) == D(300000)
    assert D(q1v3["neto_a_pagar"]) == D(200000)
    assert D(q1v3["pagado"]) == D(200000)
    assert D(q1v3["saldo"]) == CERO
    assert _cuadra(q1v3)

    aplicado, marcado = _marca(db_session, ant["id"])
    print(f"    marca: liquidacion_id={aplicado} soltado_de={marcado}")
    assert aplicado == _uuid.UUID(q1v2["id"])
    assert marcado is None, (
        "vuelto a su quincena, la marca sobra: lo protege el candado normal"
    )


def test_anular_la_que_cobraba_la_deuda_y_tenia_el_adelanto_lo_deja_todo_libre(
    client, base_datos, db_session
):
    """El caso compuesto, que es donde se cruzan los tres caminos que sueltan cosas.

    Q1 (01–15/06): 250 L a $1.800 = $450.000, con DOS adelantos ya entregados:
        A del 03/06  $100.000
        B del 04/06  $300.000
        anticipos $400.000 -> NETO $50.000, que se le pagan. Queda 'pagada'.
    Se corrige Q1 con las dos cosas a la vez —sacarle A, y bajar el precio del 02/06 de
    $1.800 a $1.000 porque estaba mal digitado—:
        VALOR TOTAL  $250.000   (250 L a $1.000)
        anticipos   -$300.000   (solo B)
        NETO         -$50.000
        pagado        $50.000
        saldo       -$100.000 -> EL PRODUCTOR LE QUEDÓ DEBIENDO $100.000, y es la verdad:
                                 recibió $300.000 de adelanto y $50.000 en la mano por
                                 $250.000 de leche.
        Y A QUEDA SUELTO Y MARCADO.
    Q2 (16–30/06): 250 L a $1.800 = $450.000, y recoge LAS DOS COSAS:
        $450.000 − A $100.000 − deuda vieja $100.000 = NETO $250.000.
    Se ANULA Q2, que suelta a la vez el adelanto y la deuda.

    LO QUE SE MIDE: que anular no deje ni el adelanto preso de un documento tachado ni la
    deuda de $100.000 cobrándose desde una liquidación anulada, y que Q1 vuelva a ser
    corregible —si su `deuda_trasladada_a_id` se quedara puesta, el dueño se quedaría sin
    NINGUNA de las dos salidas del mensaje—.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Amparo", 1800)
    _dia(client, h, prov["id"], "2026-06-02", 250)
    a = _anticipo(client, h, prov["id"], "2026-06-03", 100000, "adelanto A")
    _anticipo(client, h, prov["id"], "2026-06-04", 300000, "adelanto B")
    q1 = _generar(client, h, prov["id"], Q1)

    print("\n===== (a) ANULAR LA QUE COBRABA LA DEUDA Y TENÍA EL ADELANTO =====")
    _mostrar("Q1 borrador", q1)
    assert D(q1["valor_total"]) == D(450000)
    assert D(q1["anticipos"]) == D(400000)
    assert D(q1["neto_a_pagar"]) == D(50000)
    _aprobar(client, h, q1["id"])
    q1 = _pagar(client, h, q1["id"])
    _mostrar("Q1 pagada", q1)
    assert D(q1["pagado"]) == D(50000)
    assert _cuadra(q1)

    dia_id = q1["detalles"][0]["id"]
    r = _corregir(
        client, h, q1["id"],
        {
            "motivo": "el adelanto A no iba aquí y el precio del 02/06 estaba mal",
            "anticipos_a_soltar": [a["id"]],
            "precios": [{"detalle_id": dia_id, "precio_litro": "1000"}],
        },
    )
    assert r.status_code == 200, r.text
    q1v2 = r.json()
    _mostrar("Q1 v2 (soltó A, bajó precio)", q1v2)
    assert D(q1v2["valor_total"]) == D(250000), "250 L a $1.000"
    assert D(q1v2["anticipos"]) == D(300000), "solo queda el adelanto B"
    assert D(q1v2["neto_a_pagar"]) == D(-50000)
    assert D(q1v2["pagado"]) == D(50000)
    assert D(q1v2["saldo"]) == D(-100000)
    assert D(q1v2["le_queda_debiendo"]) == D(100000)
    assert _cuadra(q1v2)
    assert _marca(db_session, a["id"]) == (None, _uuid.UUID(q1v2["id"]))

    _dia(client, h, prov["id"], "2026-06-20", 250)
    q2 = _generar(client, h, prov["id"], Q2)
    _mostrar("Q2 (cobra deuda y recoge A)", q2)
    assert D(q2["valor_total"]) == D(450000)
    assert D(q2["anticipos"]) == D(100000), "tenía que recoger el adelanto A"
    assert D(q2["saldo_anterior"]) == D(100000), "y cobrar la deuda vieja"
    assert D(q2["neto_a_pagar"]) == D(250000), "450.000 − 100.000 − 100.000"
    assert _cuadra(q2)

    anulada = _anular(client, h, q2["id"])
    print(f"    ANULAR Q2 -> {anulada.status_code} {_detalle(anulada)}")
    assert anulada.status_code == 200, anulada.text
    tachada = anulada.json()
    _mostrar("Q2 anulada", tachada)
    assert D(tachada["saldo_anterior"]) == CERO, (
        "una liquidación tachada no le cobra a nadie la deuda de otra"
    )
    assert _cuadra(tachada)

    aplicado, marcado = _marca(db_session, a["id"])
    print(f"    marca de A: liquidacion_id={aplicado} soltado_de={marcado}")
    assert aplicado is None
    assert marcado == _uuid.UUID(q1v2["id"])
    editar, borrar = _intentar_tocar(client, h, a["id"])
    assert (editar.status_code, borrar.status_code) == (422, 422)
    assert D(_ver_anticipo(client, h, a["id"])["valor"]) == D(100000)

    # Y EL DUEÑO NO QUEDA TRABADO: la deuda quedó libre y Q1 se puede volver a corregir.
    previa = client.post(
        f"{API}/{q1v2['id']}/corregir/previsualizar",
        json={"motivo": "revisar la quincena otra vez"}, headers=h,
    )
    print(f"    PREVISUALIZAR corrección de Q1 -> {previa.status_code} {_detalle(previa)}")
    assert previa.status_code == 200, (
        "si la deuda se quedara marcada, Q1 no se podría corregir y las dos salidas "
        "del mensaje quedarían cerradas a la vez"
    )
    assert [s["anticipo_id"] for s in previa.json()["anticipos_sueltos"]] == [a["id"]]

    # Y la Q3 vuelve a recoger las dos cosas, sin duplicar un peso.
    _dia(client, h, prov["id"], "2026-07-02", 250)
    q3 = _generar(client, h, prov["id"], Q3)
    _mostrar("Q3 (recoge otra vez)", q3)
    assert D(q3["anticipos"]) == D(100000)
    assert D(q3["saldo_anterior"]) == D(100000)
    assert D(q3["neto_a_pagar"]) == D(250000)
    assert _cuadra(q3)


# ===========================================================================
# (b) EL ADELANTO NORMAL, EL QUE NUNCA PASÓ POR UNA CORRECCIÓN
# ===========================================================================
def test_un_adelanto_normal_sigue_editable_y_borrable_despues_de_anular(
    client, base_datos, db_session
):
    """Lo de todos los días, que la marca NO puede haber trabado.

    Q1 (01–15/06): 250 L a $2.000 = $500.000 con un adelanto de $300.000. Se genera, se
    ANULA el borrador —el dueño se equivocó de período— y el adelanto vuelve a quedar
    suelto. Como nunca salió de un comprobante entregado, no tiene marca: la pantalla
    tiene que dejar corregirlo y borrarlo igual que antes.

    Si la marca lo hubiera trabado, el dueño no podría ni arreglar un adelanto que tecleó
    con un cero de más el mismo día que lo registró.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Libardo", 2000)
    _dia(client, h, prov["id"], "2026-06-02", 250)
    ant = _anticipo(client, h, prov["id"], "2026-06-03", 300000, "adelanto del 3")
    q1 = _generar(client, h, prov["id"], Q1)

    print("\n===== (b) EL ADELANTO NORMAL, DESPUÉS DE ANULAR =====")
    _mostrar("Q1 borrador", q1)
    assert D(q1["anticipos"]) == D(300000)
    assert _anular(client, h, q1["id"]).status_code == 200

    aplicado, marcado = _marca(db_session, ant["id"])
    print(f"    marca tras anular: liquidacion_id={aplicado} soltado_de={marcado}")
    assert aplicado is None
    assert marcado is None, "anular no puede inventarle una marca a quien no la tenía"

    visto = _ver_anticipo(client, h, ant["id"])
    print(f"    la pantalla lo ve: bloqueado={visto['bloqueado']}")
    assert visto["bloqueado"] is False, (
        "la pantalla estaría escondiendo un arreglo que el servidor sí permite"
    )

    editar = client.put(
        f"{ANTICIPOS}/{ant['id']}", json={"valor": "30000"}, headers=h
    )
    print(f"    PUT /anticipos/id (300.000 -> 30.000) -> {editar.status_code} "
          f"{_detalle(editar)[:80]}")
    assert editar.status_code == 200, editar.text
    assert D(editar.json()["valor"]) == D(30000)

    borrar = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"    DELETE /anticipos/id -> {borrar.status_code} {_detalle(borrar)[:80]}")
    assert borrar.status_code == 204, borrar.text


def test_el_adelanto_normal_de_otra_quincena_no_se_traba_por_el_marcado_del_vecino(
    client, base_datos, db_session
):
    """Dos adelantos del mismo proveedor: uno marcado y otro no. La marca es por FILA.

    Q1 pagada: $500.000 − adelanto A de $300.000 = $200.000. Se corrige y sale A (marcado).
    Después se registra el adelanto C del 18/06 por $80.000, que nunca estuvo en ningún
    comprobante. C tiene que seguir editable y borrable aunque su vecino esté trabado.
    """
    h = auth_headers(client, "admin.a")
    prov, ant_a, q1v2 = _q1_soltando_el_adelanto(client, h)
    ant_c = _anticipo(client, h, prov["id"], "2026-06-18", 80000, "adelanto suelto nuevo")

    print("\n===== (b) EL VECINO SIN MARCA =====")
    for nombre, el in (("A (marcado)", ant_a), ("C (limpio)", ant_c)):
        aplicado, marcado = _marca(db_session, el["id"])
        visto = _ver_anticipo(client, h, el["id"])
        print(f"    {nombre:<12} valor {visto['valor']} . liquidacion_id={aplicado} "
              f". soltado_de={marcado} . bloqueado={visto['bloqueado']}")

    assert _ver_anticipo(client, h, ant_c["id"])["bloqueado"] is False
    editar = client.put(f"{ANTICIPOS}/{ant_c['id']}", json={"valor": "8000"}, headers=h)
    assert editar.status_code == 200, editar.text
    assert D(editar.json()["valor"]) == D(8000)
    assert client.delete(f"{ANTICIPOS}/{ant_c['id']}", headers=h).status_code == 204
    # Y el marcado sigue trabado y entero.
    editar_a, borrar_a = _intentar_tocar(client, h, ant_a["id"])
    assert (editar_a.status_code, borrar_a.status_code) == (422, 422)
    assert D(_ver_anticipo(client, h, ant_a["id"])["valor"]) == D(300000)


# ===========================================================================
# (c) LA QUINCENA CORREGIDA NO SE ANULA
# ===========================================================================
def test_una_quincena_corregida_no_se_anula(client, base_datos, db_session):
    """El guardia de `version > 1` sigue en pie, y es el que evita cobrar dos veces.

    Q1 quedó en v2 con $500.000 de total y el adelanto afuera. Si se pudiera anular,
    `_soltar_lo_apartado` soltaría sus 250 L y la próxima corrida de "Generar" volvería a
    cobrar los $500.000 de un período del que ya salieron DOS papeles y $200.000.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, q1v2 = _q1_soltando_el_adelanto(client, h)

    print("\n===== (c) ANULAR UNA CORREGIDA =====")
    _mostrar("Q1 v2", q1v2)
    r = _anular(client, h, q1v2["id"])
    print(f"    ANULAR -> {r.status_code} {_detalle(r)}")
    assert r.status_code == 422, r.text

    despues = _leer(client, h, q1v2["id"])
    assert despues["estado"] == "parcial", "no puede quedar tachada a medias"
    assert D(despues["valor_total"]) == D(500000)
    assert D(despues["pagado"]) == D(200000)
    assert _cuadra(despues)
    assert len(despues["detalles"]) == 1, "sus días no se soltaron"
    assert _marca(db_session, ant["id"]) == (None, _uuid.UUID(q1v2["id"]))


def test_el_mensaje_de_anular_no_puede_mandar_a_borrar_un_pago_que_no_abre_nada(
    client, base_datos, db_session
):
    """Lo que el dueño hace cuando lee «elimine primero los pagos», paso por paso.

    Q1 quedó corregida (v2), con $500.000 de total, el adelanto afuera y $200.000 ya
    entregados. Quiere anularla y rehacerla.

      1. Anular -> 422 «elimine primero los pagos». ESA ES LA CUENTA QUE SE MIDE: en una
         quincena corregida ese consejo no lleva a ninguna parte, así que el mensaje
         tiene que nombrar el muro de verdad (los comprobantes ya emitidos).
      2. Él borra el pago de $200.000 —y con él las fotos de la transferencia—.
      3. Anular otra vez -> 422 «ya salieron 2 comprobantes». El muro estaba ahí desde el
         principio, detrás del letrero equivocado.

    Y la plata queda peor de como estaba: la quincena pasa a decir `pagado $0` y `saldo
    $500.000` sobre un período del que ya salieron $200.000 de la caja. Si el dueño ahora
    oprime Pagar, entrega $500.000 encima de los $200.000: $700.000 por $500.000 de leche.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, q1v2 = _q1_soltando_el_adelanto(client, h)

    print("\n===== (c) EL MURO CON EL LETRERO CAMBIADO =====")
    _mostrar("Q1 v2 (antes)", q1v2)
    primer_intento = _anular(client, h, q1v2["id"])
    print(f"    (1) ANULAR -> {primer_intento.status_code} {_detalle(primer_intento)}")
    assert primer_intento.status_code == 422

    # LO QUE PASA SI SE SIGUE EL CONSEJO, para que el costo quede medido en el informe.
    pago_id = q1v2["pagos"][0]["id"]
    borrado = client.delete(f"{API}/{q1v2['id']}/pagos/{pago_id}", headers=h)
    print(f"    (2) DELETE el pago de $200.000 -> {borrado.status_code}")
    assert borrado.status_code == 200, borrado.text
    sin_pago = borrado.json()
    _mostrar("Q1 v2 (sin el pago)", sin_pago)
    assert D(sin_pago["pagado"]) == CERO
    assert D(sin_pago["saldo"]) == D(500000), (
        "de la caja salieron $200.000 y el documento ya no los recuerda"
    )
    segundo_intento = _anular(client, h, sin_pago["id"])
    print(f"    (3) ANULAR otra vez -> {segundo_intento.status_code} "
          f"{_detalle(segundo_intento)}")
    assert segundo_intento.status_code == 422
    assert "comprobantes" in _detalle(segundo_intento), (
        "el muro de verdad era este, y estaba desde el paso (1)"
    )

    # LA PRUEBA DE VERDAD, la que está en rojo: el PRIMER mensaje —el que se lee con el
    # pago todavía puesto— tiene que nombrar el muro que no se abre, no mandar a destruir
    # un pago y sus soportes para nada.
    detalle = _detalle(primer_intento)
    assert "comprobante" in detalle, (
        "en una quincena ya corregida, «elimine primero los pagos» es un letrero que "
        f"manda a un camino cerrado. Lo que salió: {detalle!r}"
    )


# ===========================================================================
# EL CALLEJÓN SIN SALIDA: UN ADELANTO QUE NO TIENE DÓNDE CAER
# ===========================================================================
def test_un_adelanto_registrado_por_error_se_puede_borrar_aunque_no_haya_mas_quincenas(
    client, base_datos, db_session
):
    """El adelanto que nunca existió, en el proveedor que entregó su última leche.

    Q1 (01–15/06): 250 L a $2.000 = $500.000 y un adelanto de $300.000 que se digitó
    DOS VECES —de la caja salieron $300.000, no $600.000—. La quincena se paga por
    $200.000 y después el dueño se da cuenta del duplicado: corrige la quincena para
    sacar el sobrante, y ahora quiere BORRAR la fila que nunca debió existir.

    Las dos salidas que le ofrece el mensaje:
      · «espere a que la quincena siguiente lo recoja»: el productor ya no entrega leche,
        así que «Generar» devuelve la lista vacía. No hay quincena siguiente ni la habrá.
      · «vuelva a incluirlo con 'Corregir esta quincena'»: funciona, pero lo devuelve
        adentro de una quincena PAGADA, donde el candado de siempre rebota otra vez el
        DELETE. El adelanto fantasma de $300.000 queda en la lista de anticipos para
        siempre.

    La prueba pide lo único que el dueño necesita: poder borrar esa fila desde alguna
    pantalla. Hoy no hay ninguna.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, q1v2 = _q1_soltando_el_adelanto(client, h, nombre="Rosalba")

    print("\n===== CALLEJÓN SIN SALIDA: EL ADELANTO QUE NO TIENE DÓNDE CAER =====")
    _mostrar("Q1 v2", q1v2)
    editar, borrar = _intentar_tocar(client, h, ant["id"])
    assert (editar.status_code, borrar.status_code) == (422, 422)

    # SALIDA 2: no hay quincena siguiente. El proveedor no entregó más leche.
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": Q2[0], "periodo_fin": Q2[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    print(f"    GENERAR la quincena siguiente -> generadas={r.json()['generadas']} "
          f"omitidas={r.json()['omitidas']}")
    assert r.json()["generadas"] == [], "sin leche no hay quincena que lo recoja"

    # SALIDA 1: devolverlo. Funciona, pero lo deja adentro de una PAGADA.
    devuelto = _corregir(
        client, h, q1v2["id"],
        {"motivo": "devolverlo para poder arreglarlo", "anticipos_a_incluir": [ant["id"]]},
    )
    print(f"    CORREGIR (salida 1) -> {devuelto.status_code} {_detalle(devuelto)}")
    assert devuelto.status_code == 200, devuelto.text
    q1v3 = devuelto.json()
    _mostrar("Q1 v3 (lo devolvió)", q1v3)
    assert q1v3["estado"] == "pagada"
    assert _cuadra(q1v3)

    final = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"    DELETE después de la salida 1 -> {final.status_code} {_detalle(final)}")
    # Sigue rebotando, y está bien: ese adelanto ya salió impreso y borrarlo por la
    # pantalla de anticipos no dejaría ni motivo ni versión nueva del comprobante.
    assert final.status_code == 422

    # LA SALIDA QUE ESTE ATAQUE ABRIÓ. Las dos que nombraba el mensaje se cerraban a la
    # vez cuando el productor no vuelve a entregar leche, y el adelanto fantasma se
    # quedaba vivo para descontársele de plata que sí es suya. Ahora la corrección de la
    # quincena QUE LO IMPRIMIÓ lo puede anular, con motivo y versión nueva del papel.
    #
    # Se vuelve a sacar primero —la salida 1 lo había devuelto adentro— y se anula.
    _corregir(client, h, q1v2["id"], {
        "motivo": "se saca para poder anularlo: nunca se entregó",
        "anticipos_a_soltar": [ant["id"]],
    })
    anulado = _corregir(client, h, q1v2["id"], {
        "motivo": "ese adelanto se registró por equivocación: nunca salió de la caja",
        "anticipos_a_borrar": [ant["id"]],
    })
    print(f"    corregir borrándolo -> {anulado.status_code}")
    assert anulado.status_code == 200, anulado.text

    fue = client.get(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"    el adelanto fantasma después -> {fue.status_code} (404 = ya no existe)")
    assert fue.status_code == 404, (
        "un adelanto de $300.000 que se registró por equivocación tiene que poder "
        "borrarse desde alguna pantalla, o se le descuenta al productor de plata suya"
    )
