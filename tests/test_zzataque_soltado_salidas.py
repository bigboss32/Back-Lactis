"""CAMINO 4 — ¿EL DUEÑO TIENE SALIDA? El candado nuevo, medido desde el lado del que
tiene que trabajar con él.

El candado nuevo (`Anticipo.soltado_de_liquidacion_id` mirada por
`AnticipoService._exigir_no_pagado`) rebota con un 422 que NOMBRA DOS SALIDAS:

    "...Hay dos salidas: vuelva a incluirlo con 'Corregir esta quincena' —que deja
     escrito el motivo—, o espere a que la quincena siguiente lo recoja y corríjalo ahí
     antes de pagarla"

Un candado que nombra una salida que no existe es PEOR que un "no se puede" a secas: el
dueño da vueltas buscando una puerta pintada en la pared. Así que aquí no se prueba que
el candado cierre —eso lo miden los otros caminos—: se prueba QUE LAS DOS SALIDAS
EXISTAN DE VERDAD, con plata montada, y se cuentan los pasos que le toma al dueño llegar
al final de cada una.

LO QUE SE MIDE, en orden:

  · SALIDA 1 — volver a incluirlo con la misma corrección. Funciona, la marca se limpia,
    y en la MISMA operación se le puede corregir el valor.
  · SALIDA 2 — dejar que la quincena siguiente lo recoja, y ahí sí corregirlo o
    BORRARLO desde la pantalla de anticipos, en borrador y en aprobada-sin-pagos.
  · EL CASO QUE PREOCUPA — el adelanto que NUNCA EXISTIÓ (el dueño lo anotó por error) y
    que por lo tanto hay que BORRAR, no mover. Ahí se cuenta el camino completo.

LA REGLA DE LA CASA se comprueba en cada paso y en cada liquidación que se toca:

    suma de los días = valor_total
    neto = valor_total - anticipos - saldo_anterior
    neto = pagado + saldo

LAS CIFRAS ESTÁN CALCULADAS A MANO en el docstring de cada prueba, con los mismos
tamaños del dueño: 250 L a $2.000 = $500.000 de quincena, $200.000 de adelanto,
$300.000 entregados.
"""
import uuid
from decimal import Decimal

import pytest

from app.modules.liquidaciones.models import Anticipo
from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
ANTICIPOS = "/api/v1/anticipos"

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")


def D(v):
    return Decimal(str(v))


CERO = D(0)


def _detalle(r):
    """El texto del error, que es lo que de verdad lee el dueño en la pantalla."""
    try:
        return r.json().get("error", {}).get("detail", "")
    except Exception:  # pragma: no cover - respuestas sin cuerpo (204)
        return ""


def _mostrar(titulo, liq):
    print(
        f"  {titulo:<26}. estado {liq['estado']:<9}. v{liq.get('version', '?')} "
        f". total {liq['valor_total']} . anticipos {liq['anticipos']} "
        f". neto {liq['neto_a_pagar']} . pagado {liq['pagado']} . saldo {liq['saldo']}"
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


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre="Libardo", precio=2000):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": str(precio)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _dia(client, h, proveedor_id, fecha, litros):
    r = client.post(
        "/api/v1/recepciones",
        json={"fecha": fecha, "proveedor_id": proveedor_id, "cantidad_litros": str(litros)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, proveedor_id, fecha, valor, obs=None):
    r = client.post(
        ANTICIPOS,
        json={
            "tipo": "proveedor",
            "proveedor_id": proveedor_id,
            "fecha": fecha,
            "valor": str(valor),
            "observaciones": obs,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, proveedor_id, periodo):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return [x for x in r.json()["generadas"] if x["proveedor_id"] == proveedor_id]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _ver_anticipo(client, h, ant_id):
    r = client.get(f"{ANTICIPOS}/{ant_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _marca(db, ant_id):
    """La columna nueva, leída de la base: no sale en `AnticipoRead`."""
    db.expire_all()
    fila = db.get(Anticipo, uuid.UUID(str(ant_id)))
    assert fila is not None
    return fila.soltado_de_liquidacion_id


def _corregir(client, h, liq_id, cuerpo):
    return client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)


def quincena_pagada_con_adelanto(client, h, nombre="Libardo"):
    """250 L a $2.000 = $500.000, menos $200.000 de adelanto: se le entregan $300.000.

        02/06 · 250,00 L a $2.000,00        $500.000
        VALOR TOTAL                         $500.000
        anticipos                          -$200.000
        NETO A PAGAR                        $300.000
        pagado                              $300.000
        saldo                                     $0  -> 'pagada', v1

    Este es el papel que el productor tiene guardado cuando empieza cada prueba.
    """
    prov = _proveedor(client, h, nombre)
    _dia(client, h, prov["id"], "2026-06-02", 250)
    ant = _anticipo(client, h, prov["id"], "2026-06-03", 200000, "para la droga")
    liq = _generar(client, h, prov["id"], Q1)[0]
    assert D(liq["valor_total"]) == D(500000)
    assert D(liq["anticipos"]) == D(200000)
    assert D(liq["neto_a_pagar"]) == D(300000)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    pagada = pagada.json()
    assert pagada["estado"] == "pagada"
    assert D(pagada["pagado"]) == D(300000)
    assert pagada["version"] == 1
    assert _cuadra(pagada)
    return prov, ant, pagada


def soltar_el_adelanto(client, h, liq, ant, motivo="el adelanto no era de esta quincena"):
    """La corrección que crea el problema: el adelanto sale del comprobante ya pagado.

        VALOR TOTAL                         $500.000
        anticipos                                 $0   <- salió
        NETO A PAGAR                        $500.000
        pagado (no se toca)                 $300.000
        saldo                               $200.000  -> 'parcial', v2

    Y el adelanto queda SUELTO pero MARCADO: `liquidacion_id` en nulo,
    `soltado_de_liquidacion_id` apuntando a esta quincena.
    """
    r = _corregir(client, h, liq["id"], {"motivo": motivo, "anticipos_a_soltar": [ant["id"]]})
    assert r.status_code == 200, r.text
    corregida = r.json()
    assert D(corregida["anticipos"]) == CERO
    assert D(corregida["neto_a_pagar"]) == D(500000)
    assert D(corregida["pagado"]) == D(300000)
    assert D(corregida["saldo"]) == D(200000)
    assert corregida["estado"] == "parcial"
    assert corregida["version"] == 2
    assert _cuadra(corregida)
    return corregida


# ===========================================================================
# El punto de partida: el candado de verdad está cerrado
# ===========================================================================
def test_el_adelanto_soltado_queda_trabado_y_el_mensaje_nombra_las_dos_salidas(
    client, base_datos, db_session
):
    """Antes de medir las salidas hay que ver la pared. Y hay que LEER el letrero.

    Se juzga el texto como lo leería una persona no técnica que maneja una quesera:
    ¿dice qué pasó, por qué, y qué tiene que hacer ahora?
    """
    h = auth_headers(client, "admin.a")
    _, ant, liq = quincena_pagada_con_adelanto(client, h)
    corregida = soltar_el_adelanto(client, h, liq, ant)

    print("\n===== EL PUNTO DE PARTIDA =====")
    _mostrar("quincena corregida", corregida)
    assert _marca(db_session, ant["id"]) is not None, "la marca tenía que quedar puesta"

    editar = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "1"}, headers=h)
    borrar = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  PUT    /anticipos/id -> {editar.status_code}")
    print(f"  DELETE /anticipos/id -> {borrar.status_code}")
    assert editar.status_code == 422, editar.text
    assert borrar.status_code == 422, borrar.text

    texto = _detalle(borrar)
    print("\n  EL LETRERO, tal cual lo ve el dueño:")
    print(f"    «{texto}»")

    # Lo que el letrero tiene que traer para servirle a alguien:
    assert "YA SE IMPRIMIÓ" in texto, "no dice POR QUÉ está trabado"
    assert "Corregir esta quincena" in texto, "no nombra la SALIDA 1"
    assert "quincena siguiente" in texto, "no nombra la SALIDA 2"

    # Y la pantalla tiene que decir lo mismo que el servidor, o el dueño oprime un
    # botón que siempre falla.
    visto = _ver_anticipo(client, h, ant["id"])
    print(f"  la pantalla dice bloqueado={visto['bloqueado']}")
    assert visto["bloqueado"] is True
    assert D(visto["valor"]) == D(200000), "no le podían haber movido la cifra"


# ===========================================================================
# SALIDA 1 — volver a incluirlo con 'Corregir esta quincena'
# ===========================================================================
def test_salida_1_volver_a_incluirlo_con_la_misma_correccion_y_la_marca_se_limpia(
    client, base_datos, db_session
):
    """SALIDA 1, completa. Las cifras, a mano:

    DESPUÉS DE SOLTARLO (v2):   total $500.000, anticipos $0,   neto $500.000,
                                pagado $300.000, saldo $200.000  -> 'parcial'
    AL VOLVERLO A INCLUIR (v3): total $500.000, anticipos $200.000, neto $300.000,
                                pagado $300.000, saldo $0        -> 'pagada'

    Queda EXACTAMENTE como antes de empezar, que es lo que la salida promete. Y la
    marca `soltado_de_liquidacion_id` tiene que quedar LIMPIA: si se quedara puesta,
    el adelanto arrastraría una traba que ya no le corresponde.
    """
    h = auth_headers(client, "admin.a")
    _, ant, liq = quincena_pagada_con_adelanto(client, h)
    soltar_el_adelanto(client, h, liq, ant)

    print("\n===== SALIDA 1 =====")
    # Paso único: la misma corrección, al revés.
    r = _corregir(
        client,
        h,
        liq["id"],
        {"motivo": "sí era de esta quincena, se devuelve", "anticipos_a_incluir": [ant["id"]]},
    )
    print(f"  POST /corregir (incluir) -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 200, r.text
    vuelta = r.json()
    _mostrar("vuelta a la quincena", vuelta)

    assert D(vuelta["valor_total"]) == D(500000)
    assert D(vuelta["anticipos"]) == D(200000)
    assert D(vuelta["neto_a_pagar"]) == D(300000)
    assert D(vuelta["pagado"]) == D(300000)
    assert D(vuelta["saldo"]) == CERO
    assert vuelta["estado"] == "pagada"
    assert vuelta["version"] == 3
    assert _cuadra(vuelta)

    assert _marca(db_session, ant["id"]) is None, (
        "la marca quedó puesta después de volver a entrar: el adelanto ya está dentro "
        "de un comprobante y lo protege el candado normal"
    )
    visto = _ver_anticipo(client, h, ant["id"])
    print(f"  el adelanto: valor {visto['valor']} . liquidacion_id={visto['liquidacion_id']}"
          f" . bloqueado={visto['bloqueado']}")
    assert visto["liquidacion_id"] == liq["id"]
    assert D(visto["valor"]) == D(200000)
    # Sigue trabado, pero AHORA por el candado normal (quincena pagada y corregida),
    # que es lo correcto.
    assert visto["bloqueado"] is True


def test_salida_1_ademas_le_corrige_el_valor_en_la_misma_operacion(
    client, base_datos, db_session
):
    """La otra mitad de la pregunta: ¿se le puede corregir el valor al volverlo a meter?

    El adelanto era de $200.000 pero de verdad fueron $150.000. Al volverlo a incluir
    con el valor corregido:

        VALOR TOTAL                         $500.000
        anticipos                          -$150.000
        NETO A PAGAR                        $350.000
        pagado                              $300.000
        saldo                                $50.000  -> 'parcial', v3

    350.000 = 300.000 + 50.000. Cuadra al peso.
    """
    h = auth_headers(client, "admin.a")
    _, ant, liq = quincena_pagada_con_adelanto(client, h)
    soltar_el_adelanto(client, h, liq, ant)

    print("\n===== SALIDA 1 + VALOR =====")
    r = _corregir(
        client,
        h,
        liq["id"],
        {
            "motivo": "era de esta quincena y eran $150.000, no $200.000",
            "anticipos_a_incluir": [ant["id"]],
            "valores_de_anticipos": [{"anticipo_id": ant["id"], "valor": "150000"}],
        },
    )
    print(f"  POST /corregir (incluir + valor) -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 200, r.text
    vuelta = r.json()
    _mostrar("con el valor corregido", vuelta)

    assert D(vuelta["anticipos"]) == D(150000)
    assert D(vuelta["neto_a_pagar"]) == D(350000)
    assert D(vuelta["pagado"]) == D(300000)
    assert D(vuelta["saldo"]) == D(50000)
    assert vuelta["estado"] == "parcial"
    assert _cuadra(vuelta)

    assert _marca(db_session, ant["id"]) is None
    assert D(_ver_anticipo(client, h, ant["id"])["valor"]) == D(150000)


# ===========================================================================
# SALIDA 2 — que la quincena siguiente lo recoja
# ===========================================================================
def _montar_salida_2(client, h, nombre="Libardo"):
    """Suelta el adelanto y genera la quincena SIGUIENTE, que se lo debe llevar.

    Q2 (16/06 al 30/06): 100 L a $2.000 = $200.000 de leche, menos los $200.000 del
    adelanto que quedó suelto = neto $0.
    """
    prov, ant, liq = quincena_pagada_con_adelanto(client, h, nombre)
    soltar_el_adelanto(client, h, liq, ant)
    _dia(client, h, prov["id"], "2026-06-20", 100)
    q2 = _generar(client, h, prov["id"], Q2)
    assert len(q2) == 1, f"la quincena siguiente no se generó: {q2}"
    q2 = q2[0]
    assert D(q2["valor_total"]) == D(200000)
    assert D(q2["anticipos"]) == D(200000), (
        "la quincena siguiente NO recogió el adelanto suelto: ahí muere la salida 2"
    )
    assert _cuadra(q2)
    return prov, ant, liq, q2


def test_salida_2_la_siguiente_lo_recoge_y_en_borrador_se_le_corrige_el_valor(
    client, base_datos, db_session
):
    """Las cifras de Q2, a mano:

        20/06 · 100,00 L a $2.000,00        $200.000
        anticipos                          -$200.000
        NETO A PAGAR                              $0   -> borrador

    Se le corrige el adelanto a $150.000 desde la PANTALLA DE ANTICIPOS (que es lo que
    el mensaje promete), y Q2 queda:

        VALOR TOTAL                         $200.000
        anticipos                          -$150.000
        NETO A PAGAR                         $50.000
    """
    h = auth_headers(client, "admin.a")
    _, ant, _, q2 = _montar_salida_2(client, h)

    print("\n===== SALIDA 2 · BORRADOR · CORREGIR EL VALOR =====")
    _mostrar("Q2 recién generada", q2)
    print(f"  la marca sigue puesta en la base: {_marca(db_session, ant['id']) is not None}")

    visto = _ver_anticipo(client, h, ant["id"])
    print(f"  la pantalla dice bloqueado={visto['bloqueado']} "
          f"(estado de su quincena: {visto['liquidacion_estado']})")
    assert visto["bloqueado"] is False, (
        "la pantalla lo muestra trabado cuando el servidor sí deja: el dueño no vería "
        "el botón que el mensaje le mandó a oprimir"
    )

    r = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "150000"}, headers=h)
    print(f"  PUT /anticipos/id (150.000) -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 200, r.text

    despues = _leer(client, h, q2["id"])
    _mostrar("Q2 después", despues)
    assert D(despues["anticipos"]) == D(150000)
    assert D(despues["valor_total"]) == D(200000)
    assert D(despues["neto_a_pagar"]) == D(50000)
    assert _cuadra(despues)


def test_salida_2_borrarlo_desde_la_pantalla_con_la_siguiente_en_borrador(
    client, base_datos, db_session
):
    """La salida que de verdad hace falta cuando el adelanto NUNCA EXISTIÓ: BORRARLO.

    Q2 antes:  total $200.000 - anticipos $200.000 = neto $0
    Q2 después: total $200.000 - anticipos $0      = neto $200.000

    Los $200.000 vuelven a ser del productor, que es lo correcto si el adelanto nunca
    salió de la caja.
    """
    h = auth_headers(client, "admin.a")
    _, ant, _, q2 = _montar_salida_2(client, h)

    print("\n===== SALIDA 2 · BORRADOR · BORRAR =====")
    _mostrar("Q2 antes", q2)
    r = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  DELETE /anticipos/id -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 204, r.text

    despues = _leer(client, h, q2["id"])
    _mostrar("Q2 después", despues)
    assert D(despues["anticipos"]) == CERO
    assert D(despues["neto_a_pagar"]) == D(200000)
    assert _cuadra(despues)
    assert client.get(f"{ANTICIPOS}/{ant['id']}", headers=h).status_code == 404


def test_salida_2_tambien_con_la_siguiente_aprobada_y_sin_pagos(client, base_datos):
    """El mensaje dice "corríjalo ahí ANTES DE PAGARLA" — o sea que aprobada también
    tiene que dejar. Aprobar es un visto bueno; si las cifras cambian, la quincena
    vuelve a borrador y se vuelve a aprobar. Eso es lo que se mide.
    """
    h = auth_headers(client, "admin.a")
    _, ant, _, q2 = _montar_salida_2(client, h)

    aprobada = client.post(f"{API}/{q2['id']}/aprobar", headers=h)
    assert aprobada.status_code == 200, aprobada.text
    print("\n===== SALIDA 2 · APROBADA SIN PAGOS =====")
    _mostrar("Q2 aprobada", aprobada.json())
    assert aprobada.json()["estado"] == "aprobada"

    r = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  DELETE /anticipos/id -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 204, r.text

    despues = _leer(client, h, q2["id"])
    _mostrar("Q2 después", despues)
    assert D(despues["anticipos"]) == CERO
    assert D(despues["neto_a_pagar"]) == D(200000)
    assert despues["estado"] == "borrador", (
        "al cambiarle las cifras, el visto bueno ya no vale"
    )
    assert _cuadra(despues)


# ===========================================================================
# EL CASO QUE PREOCUPA — el adelanto que NUNCA EXISTIÓ
# ===========================================================================
def test_la_salida_1_no_sirve_para_borrar_el_adelanto_que_nunca_existio(
    client, base_datos, db_session
):
    """El mensaje le ofrece DOS salidas al que oprimió BORRAR. La primera no lo lleva
    a borrar NADA: lo deja peor.

    El dueño anotó por error un adelanto de $200.000 que nunca entregó. Lo soltó de la
    quincena corregida pensando en borrarlo. Sigue la SALIDA 1 que el mensaje le nombra
    —volver a incluirlo con 'Corregir esta quincena'— y entonces intenta borrarlo:

        PASO 1  DELETE /anticipos/{id}                  -> 422 (nombra las dos salidas)
        PASO 2  POST /corregir (anticipos_a_incluir)    -> 200, vuelve a la quincena
        PASO 3  DELETE /anticipos/{id}                  -> 422 (ahora por 'ya se pagó')

    Después de seguir la salida que el papel le nombró, sigue sin poder borrarlo y ya
    quemó una versión más del comprobante del productor.
    """
    h = auth_headers(client, "admin.a")
    _, ant, liq = quincena_pagada_con_adelanto(client, h)
    soltar_el_adelanto(client, h, liq, ant, motivo="ese adelanto nunca se le entregó")

    print("\n===== EL ADELANTO QUE NUNCA EXISTIÓ · POR LA SALIDA 1 =====")
    paso1 = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  paso 1 · DELETE -> {paso1.status_code} . {_detalle(paso1)}")
    assert paso1.status_code == 422

    paso2 = _corregir(
        client, h, liq["id"],
        {"motivo": "se devuelve para poder borrarlo", "anticipos_a_incluir": [ant["id"]]},
    )
    print(f"  paso 2 · POST /corregir (incluir) -> {paso2.status_code}")
    assert paso2.status_code == 200, paso2.text
    _mostrar("la quincena, v3", paso2.json())

    paso3 = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  paso 3 · DELETE -> {paso3.status_code} . {_detalle(paso3)}")
    assert paso3.status_code == 422, (
        "si esto pasara, la salida 1 sí serviría para borrar"
    )
    # Y el letrero del paso 3 ya NO nombra ninguna salida: "registre el ajuste en la
    # quincena siguiente" es justo lo que el dueño NO quiere —ese adelanto no se ajusta,
    # no existió—. La salida 1 lo devolvió a una pared sin puerta.
    assert "ya se pagó" in _detalle(paso3)

    # Y el adelanto de $200.000 que nunca existió sigue vivo y descontado.
    visto = _ver_anticipo(client, h, ant["id"])
    print(f"  el adelanto que nunca existió sigue en {visto['valor']}, "
          f"descontado en la quincena")
    assert D(visto["valor"]) == D(200000)


def test_bajar_a_cero_el_adelanto_que_nunca_existio_tampoco_se_puede(client, base_datos):
    """Si no se puede borrar, ¿al menos se puede dejar en $0 por la corrección?

    No: `ValorDeUnAnticipo.valor` exige `gt=0`. Lo más cerca que llega el dueño es UN
    CENTAVO, y ese centavo le descuadra el comprobante contra la calculadora para
    siempre.
    """
    h = auth_headers(client, "admin.a")
    _, ant, liq = quincena_pagada_con_adelanto(client, h)
    soltar_el_adelanto(client, h, liq, ant, motivo="ese adelanto nunca se le entregó")

    print("\n===== ¿Y DEJARLO EN $0? =====")
    r = _corregir(
        client, h, liq["id"],
        {
            "motivo": "nunca se le entregó: queda en cero",
            "anticipos_a_incluir": [ant["id"]],
            "valores_de_anticipos": [{"anticipo_id": ant["id"], "valor": "0"}],
        },
    )
    print(f"  POST /corregir (valor 0) -> {r.status_code} . {_detalle(r)}")
    assert r.status_code == 422, r.text


def test_el_adelanto_que_nunca_existio_y_el_productor_no_volvio_a_entregar_leche(
    client, base_datos, db_session
):
    """EL CALLEJÓN SIN SALIDA, con el camino completo recorrido.

    Las dos salidas que el mensaje nombra dependen de algo que el mensaje no dice:

      · la SALIDA 1 lo mete de vuelta en la quincena vieja, donde ya no se puede borrar;
      · la SALIDA 2 necesita que HAYA una quincena siguiente, y una quincena de leche
        solo nace si el productor entregó leche. `_generar_proveedores` arranca de
        `recepciones_sin_liquidar`: sin días, no hay comprobante, y sin comprobante
        nadie recoge el adelanto suelto.

    El productor que se retiró —o que se murió la vaca, o que pasó dos meses sin
    entregar— deja un adelanto de $200.000 que NUNCA EXISTIÓ, trabado para siempre:

        PASO 1  DELETE /anticipos/{id}                     -> 422
        PASO 2  PUT /anticipos/{id} (bajarlo)              -> 422
        PASO 3  generar la quincena siguiente (sin leche)  -> no se genera nada
        PASO 4  DELETE /anticipos/{id} otra vez            -> 422

    Y esos $200.000 quedan esperando: el día que el productor vuelva a entregar leche,
    la primera quincena que se le genere se los va a descontar de plata que sí es suya.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, liq = quincena_pagada_con_adelanto(client, h)
    soltar_el_adelanto(client, h, liq, ant, motivo="ese adelanto nunca se le entregó")

    print("\n===== EL CALLEJÓN =====")
    paso1 = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  paso 1 · DELETE                    -> {paso1.status_code}")
    assert paso1.status_code == 422

    paso2 = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "1"}, headers=h)
    print(f"  paso 2 · PUT (bajarlo a $1)        -> {paso2.status_code}")
    assert paso2.status_code == 422

    # PASO 3: el productor no entregó un litro en la quincena siguiente.
    generadas = _generar(client, h, prov["id"], Q2)
    print(f"  paso 3 · generar Q2 sin leche      -> {len(generadas)} comprobantes")
    assert generadas == [], "sin días no hay comprobante, y sin comprobante no hay salida 2"

    paso4 = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  paso 4 · DELETE                    -> {paso4.status_code} . {_detalle(paso4)}")
    assert paso4.status_code == 422

    # Sigue vivo, suelto, marcado y trabado. No hay quinto paso que lo suelte.
    visto = _ver_anticipo(client, h, ant["id"])
    print(f"  el adelanto: valor {visto['valor']} . liquidacion_id={visto['liquidacion_id']}"
          f" . bloqueado={visto['bloqueado']}")
    assert visto["liquidacion_id"] is None
    assert visto["bloqueado"] is True
    assert _marca(db_session, ant["id"]) is not None
    assert D(visto["valor"]) == D(200000)


def test_la_unica_forma_de_llegar_a_borrarlo_es_inventando_un_dia_de_leche(
    client, base_datos, db_session
):
    """¿Cuántos pasos le toma llegar a borrarlo? Se recorre el camino completo.

    Como la SALIDA 2 necesita una quincena siguiente, y una quincena siguiente necesita
    leche, al dueño le queda UN camino: ANOTAR UN DÍA DE LECHE QUE NO HUBO para que el
    sistema le genere el comprobante que recoge el adelanto. Los litros no pueden ir en
    cero (`cantidad_litros` exige `gt=0`), así que el día inventado es plata de verdad
    en el libro: 0,01 L a $2.000 = $20,00.

        PASO 1  anotar una recepción que no existió (0,01 L)
        PASO 2  generar la quincena siguiente
        PASO 3  DELETE /anticipos/{id}                 -> 204  (por fin)
        PASO 4  borrar la recepción inventada
        PASO 5  borrar la liquidación que sobró

    Cinco pasos, y dos de ellos son ESCRIBIR EN EL LIBRO UN DÍA DE LECHE QUE NO HUBO.
    Esto no es una salida: es lo que hace la gente cuando no hay salida.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, liq = quincena_pagada_con_adelanto(client, h)
    soltar_el_adelanto(client, h, liq, ant, motivo="ese adelanto nunca se le entregó")

    print("\n===== EL CAMINO QUE SÍ LLEGA (inventando leche) =====")
    assert client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h).status_code == 422

    inventada = client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-06-20", "proveedor_id": prov["id"], "cantidad_litros": "0.01"},
        headers=h,
    )
    print(f"  paso 1 · recepción inventada de 0,01 L -> {inventada.status_code}")
    assert inventada.status_code == 201, inventada.text

    q2 = _generar(client, h, prov["id"], Q2)
    print(f"  paso 2 · generar Q2 -> {len(q2)} comprobante(s)")
    assert len(q2) == 1
    q2 = q2[0]
    _mostrar("Q2 inventada", q2)
    assert D(q2["valor_total"]) == D("20.00"), "0,01 L x $2.000 = $20,00"
    assert D(q2["anticipos"]) == D(200000), "solo así el adelanto se deja alcanzar"

    borrado = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  paso 3 · DELETE /anticipos/id -> {borrado.status_code}")
    assert borrado.status_code == 204, borrado.text

    # Y ahora hay que deshacer la mentira, que son dos pasos más y dejan rastro.
    p4 = client.delete(f"/api/v1/recepciones/{inventada.json()['id']}", headers=h)
    print(f"  paso 4 · borrar la recepción inventada -> {p4.status_code}")
    assert p4.status_code == 204, p4.text
    # La liquidación inventada NO SE PUEDE BORRAR: no hay DELETE en liquidaciones. Lo
    # más que se puede es ANULARLA, o sea que el comprobante fantasma de $20,00 se queda
    # en el libro para siempre, tachado.
    sin_delete = client.delete(f"{API}/{q2['id']}", headers=h)
    print(f"  paso 5 · DELETE de la liquidación -> {sin_delete.status_code} (no existe)")
    assert sin_delete.status_code == 405
    p5 = client.post(f"{API}/{q2['id']}/anular", headers=h)
    print(f"  paso 5 · anular la liquidación inventada -> {p5.status_code}")
    assert p5.status_code == 200, p5.text
    print(f"           queda en el libro, tachada: estado {p5.json()['estado']}")
    print("  RESULTADO: el adelanto que nunca existió por fin no le descuenta a nadie,")
    print("             pero el dueño tuvo que escribir en el libro leche que no hubo.")
    assert client.get(f"{ANTICIPOS}/{ant['id']}", headers=h).status_code == 404


def test_defecto_el_dueno_tiene_que_poder_borrar_el_adelanto_que_nunca_existio(
    client, base_datos, db_session
):
    """LO QUE EL DUEÑO NECESITA, escrito como debería funcionar.

    El adelanto de $200.000 se anotó por error: esa plata NUNCA salió de la caja. No hay
    nada que "descontar en la siguiente" —el comprobante v2 que el productor tiene en la
    mano ya no lo menciona, porque la corrección lo sacó— y dejarlo vivo le va a costar
    $200.000 al productor en su próxima quincena.

    Se recorren, sin dar por hecho que alguna sirva, TODAS las puertas que el sistema le
    ofrece a un dueño que no tiene otra quincena a la vista:

      A) borrarlo desde la pantalla de anticipos;
      B) bajarlo a $1 desde la pantalla de anticipos;
      C) volverlo a incluir con 'Corregir esta quincena' y borrarlo ahí;
      D) volverlo a incluir dejándolo en $0 por la corrección.

    Al final SE EXIGE EL RESULTADO, no el mecanismo: el adelanto que nunca existió no
    puede seguir pendiente de descontarle $200.000 a nadie. Hoy sigue.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, liq = quincena_pagada_con_adelanto(client, h)
    soltar_el_adelanto(client, h, liq, ant, motivo="ese adelanto nunca se le entregó")

    print("\n===== TODAS LAS PUERTAS, UNA POR UNA =====")
    a = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  A · DELETE /anticipos/id                     -> {a.status_code}")

    b = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "1"}, headers=h)
    print(f"  B · PUT /anticipos/id (a $1)                 -> {b.status_code}")

    d = _corregir(
        client, h, liq["id"],
        {
            "motivo": "nunca se le entregó: que quede en cero",
            "anticipos_a_incluir": [ant["id"]],
            "valores_de_anticipos": [{"anticipo_id": ant["id"], "valor": "0"}],
        },
    )
    print(f"  D · corregir incluyéndolo en $0              -> {d.status_code}")

    c1 = _corregir(
        client, h, liq["id"],
        {"motivo": "se devuelve para poder borrarlo", "anticipos_a_incluir": [ant["id"]]},
    )
    print(f"  C · corregir incluyéndolo                    -> {c1.status_code}")
    c2 = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  C · DELETE /anticipos/id (ya incluido)       -> {c2.status_code}"
          f" . {_detalle(c2)}")

    # E) LA PUERTA QUE ABRIÓ ESTE ATAQUE. Las cuatro de arriba estaban cerradas —con
    # razón: ese adelanto ya salió impreso y borrarlo por la pantalla de anticipos no
    # dejaría rastro—, pero entre las cuatro no quedaba ninguna salida, y un adelanto
    # que nunca existió terminaba descontándosele al productor. Ahora la corrección de
    # la quincena QUE LO IMPRIMIÓ lo puede anular, con motivo, versión nueva del
    # comprobante y un renglón que lo dice.
    e = _corregir(
        client, h, liq["id"],
        {"motivo": "ese adelanto nunca existió: se anula", "anticipos_a_borrar": [ant["id"]]},
    )
    print(f"  E · corregir borrándolo                      -> {e.status_code}"
          f" . {_detalle(e)}")

    sigue = client.get(f"{ANTICIPOS}/{ant['id']}", headers=h)
    if sigue.status_code == 404:
        print("  el adelanto que nunca existió YA NO ESTÁ: hay salida.")
        return
    visto = sigue.json()
    print(f"  el adelanto que nunca existió sigue vivo en {visto['valor']}")
    assert sigue.status_code == 404 or D(visto["valor"]) == CERO, (
        "el dueño no tiene ninguna puerta para borrar un adelanto que anotó por error "
        "y que ya soltó de la quincena: los $200.000 se le van a descontar al productor "
        "en la primera quincena que se le genere"
    )
