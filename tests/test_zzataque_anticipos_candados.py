"""ATAQUE 2 — QUE EL CANDADO DE LOS ANTICIPOS SIGA CERRADO POR LA PUERTA DE AL LADO.

"Corregir esta quincena" ahora también mueve los ANTICIPOS, y para hacerlo se le pasa
POR ENCIMA a `AnticipoService._exigir_no_pagado` a propósito. Eso está bien: viene con
cinco protecciones —permiso de un solo rol, motivo escrito, la cifra a la vista antes de
escribir, la versión del comprobante que sube, y el renglón que deja qué anticipo se
movió—. Lo que NO puede pasar es que la pantalla de anticipos (`PUT`/`DELETE`
`/anticipos/{id}`) permita exactamente lo mismo SIN ninguna de las cinco.

Un anticipo es plata que YA se le entregó en la mano al productor, y el comprobante se la
resta:

    neto_a_pagar = valor_total - anticipos - saldo_anterior
    saldo        = neto_a_pagar - pagado

Así que mover un anticipo de una quincena ya cerrada cambia el NETO de un papel que el
productor tiene guardado. Aquí se mide, con plata montada de verdad, que el candado
aguanta en las tres familias:

  (a) PAGADA de las normales —$300.000 entregados contra un neto de $300.000—;
  (b) CORREGIDA (`version > 1`), con el comprobante -v2 ya emitido;
  (c) PAGADA CON `pagado = $0`: la que los anticipos cubrieron EXACTO y se cerró por la
      rama `pendiente <= CERO` de `pagar`. Ahí `tiene_pagos` es False y el candado se
      sostiene solo del estado;
  (d) y la trampa de las trampas: (c) DESPUÉS DE CORREGIRLA. Ahí el estado ya no es
      'pagada' (pasa a 'parcial'), `tiene_pagos` sigue en False, y lo ÚNICO que queda
      cerrando la puerta es `version > 1`. Si esa pregunta se cayera, al anticipo de
      $180.000 se le podría mover la cifra desde la pantalla y el neto del papel ya
      entregado cambiaría solo.

Y aparte, dos cosas que no son el candado pero lo sostienen:

  · LA PANTALLA TIENE QUE DECIR LO MISMO QUE EL SERVIDOR. El campo `bloqueado` de
    `AnticipoRead` se compara, estado por estado, contra lo que de verdad hace el `PUT`.
    Si la pantalla dijera "se puede" y el servidor rebotara, el dueño oprimiría un botón
    que siempre falla; si dijera "no se puede" donde sí se puede, escondería el arreglo.
  · EL PERMISO. Corregir los anticipos por la puerta de la corrección exige
    `liquidaciones:administrar`, que tiene un solo rol. COMPRAS —que sí tiene
    `liquidaciones:editar`, y se demuestra usándolo— no puede.

Las cifras son todas del mismo tamaño que las del dueño y se imprimen en cada paso para
que el desglose se pueda sumar a mano contra la cifra grande.
"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.usuarios.models import Rol
from tests.conftest import PASSWORD, auth_headers

API = "/api/v1/liquidaciones"
ANTICIPOS = "/api/v1/anticipos"

INICIO = "2026-06-01"
FIN = "2026-06-15"


def D(v):
    return Decimal(str(v))


def _detalle(r):
    """El texto del error de negocio, que es lo que lee el dueño en la pantalla."""
    try:
        return r.json().get("error", {}).get("detail", "")
    except Exception:  # pragma: no cover - respuestas sin cuerpo (204)
        return ""


def _mostrar(titulo, liq):
    print(
        f"  {titulo:<14}. estado {liq['estado']:<9}. v{liq.get('version', '?')} "
        f". total {liq['valor_total']} . anticipos {liq['anticipos']} "
        f". neto {liq['neto_a_pagar']} . pagado {liq['pagado']} . saldo {liq['saldo']}"
    )


def _cuadra(liq) -> bool:
    """LA REGLA DE LA CASA, la que el dueño verifica con calculadora.

    Los días sueltos suman el total; el neto es el total menos los anticipos menos la
    deuda vieja; y lo entregado más lo que falta es el neto.
    """
    suma_dias = sum((D(d["valor"]) for d in liq["detalles"]), D(0))
    neto = D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq.get("saldo_anterior") or 0)
    return (
        suma_dias == D(liq["valor_total"])
        and D(liq["neto_a_pagar"]) == neto
        and D(liq["neto_a_pagar"]) == D(liq["pagado"]) + D(liq["saldo"])
    )


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio):
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


def _generar(client, h, proveedor_id):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": INICIO, "periodo_fin": FIN, "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == proveedor_id)


def quincena_pagada_con_plata(client, h, nombre="Libardo"):
    """250 L a $2.000 = $500.000, menos $200.000 de adelanto: se le entregan $300.000.

    Es la familia normal: queda `pagada` CON un pago de $300.000 registrado.
    """
    prov = _proveedor(client, h, nombre, 2000)
    _dia(client, h, prov["id"], "2026-06-02", 250)
    ant = _anticipo(client, h, prov["id"], "2026-06-03", 200000, "para la droga")
    liq = _generar(client, h, prov["id"])
    assert D(liq["valor_total"]) == D(500000)
    assert D(liq["anticipos"]) == D(200000)
    assert D(liq["neto_a_pagar"]) == D(300000)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    pagada = pagada.json()
    assert pagada["estado"] == "pagada"
    assert D(pagada["pagado"]) == D(300000), "el pago de $300.000 no quedó registrado"
    assert pagada["version"] == 1
    return prov, ant, pagada


def quincena_cubierta_exacto(client, h, nombre="Amparo"):
    """100 L a $1.800 = $180.000, y un adelanto de $180.000 que se la come EXACTA.

    Esta es la familia rara y peligrosa: `pagar` se va por la rama `pendiente <= CERO`,
    no registra ningún pago, y la quincena queda 'pagada' con `pagado = $0`. Esa plata
    SÍ salió —en la mano, como adelanto— pero `tiene_pagos` dice que no hay pagos.
    """
    prov = _proveedor(client, h, nombre, 1800)
    _dia(client, h, prov["id"], "2026-06-02", 100)
    ant = _anticipo(client, h, prov["id"], "2026-06-03", 180000, "adelanto del 3")
    liq = _generar(client, h, prov["id"])
    assert D(liq["valor_total"]) == D(180000)
    assert D(liq["anticipos"]) == D(180000)
    assert D(liq["neto_a_pagar"]) == D(0), "el adelanto tenía que comerse la quincena"
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h).json()
    assert pagada["estado"] == "pagada"
    assert D(pagada["pagado"]) == D(0), "esta familia se cierra SIN registrar un pago"
    assert pagada["pagos"] == [], "no tenía que haber ningún pago que mirar"
    return prov, ant, pagada


def _corregir_metiendo_el_dia(client, h, liq_id, motivo):
    """Corrige la quincena metiéndole el día que quedó suelto. Devuelve la liquidación."""
    prev = client.post(
        f"{API}/{liq_id}/corregir/previsualizar", json={"motivo": motivo}, headers=h
    )
    assert prev.status_code == 200, prev.text
    sueltos = prev.json()["dias_sueltos"]
    assert len(sueltos) == 1, sueltos
    r = client.post(
        f"{API}/{liq_id}/corregir",
        json={"motivo": motivo, "recepciones_a_incluir": [sueltos[0]["recepcion_id"]]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ===========================================================================
# (a) PAGADA de las normales: ni corregir ni borrar el anticipo
# ===========================================================================
def test_en_una_quincena_pagada_la_pantalla_de_anticipos_sigue_rebotando(client, base_datos):
    """La familia de todos los días. Se le entregaron $300.000 contra un neto de
    $300.000 que ya tenía restados los $200.000 del adelanto.

    Si por `PUT /anticipos/{id}` se pudiera bajar ese adelanto a $1, el neto del papel
    que el productor guardó pasaría de $300.000 a $499.999 y el sistema diría que se le
    quedaron debiendo $199.999 de una quincena que se pagó completa. Tiene que rebotar
    con 422, y NADA puede quedar movido.
    """
    h = auth_headers(client, "admin.a")
    _, ant, liq = quincena_pagada_con_plata(client, h)

    print("\n===== (a) PAGADA CON PAGO REGISTRADO =====")
    _mostrar("antes", liq)
    assert _cuadra(liq)

    editar = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "1"}, headers=h)
    borrar = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  PUT    /anticipos/id -> {editar.status_code} . {_detalle(editar)}")
    print(f"  DELETE /anticipos/id -> {borrar.status_code} . {_detalle(borrar)}")
    assert editar.status_code == 422, editar.text
    assert borrar.status_code == 422, borrar.text
    # El mensaje tiene que mandarlo a la puerta correcta, no dejarlo adivinando.
    assert "ya se pagó" in _detalle(editar)
    assert "ya se pagó" in _detalle(borrar)

    despues = client.get(f"{API}/{liq['id']}", headers=h).json()
    sigue = client.get(f"{ANTICIPOS}/{ant['id']}", headers=h).json()
    _mostrar("después", despues)
    print(f"  el adelanto sigue en {sigue['valor']} . bloqueado={sigue['bloqueado']}")
    assert D(sigue["valor"]) == D(200000), "le movieron la cifra al adelanto"
    assert D(despues["anticipos"]) == D(200000)
    assert D(despues["neto_a_pagar"]) == D(300000)
    assert D(despues["pagado"]) == D(300000)
    assert D(despues["saldo"]) == D(0)
    assert despues["estado"] == "pagada"
    assert _cuadra(despues)


# ===========================================================================
# (b) CORREGIDA (version > 1): el hueco que ya se tapó
# ===========================================================================
def test_en_una_quincena_ya_corregida_el_anticipo_tampoco_se_toca(client, base_datos):
    """Se corrigió la quincena metiéndole el día olvidado de $180.000 y salió el -v2.

    Después de corregir, el comprobante dice: total $680.000 - $200.000 de adelanto =
    $480.000 de neto, de los cuales ya se entregaron $300.000 y faltan $180.000. Tocar
    el adelanto desde la pantalla movería el neto de una hoja que YA se imprimió dos
    veces. Y el mensaje tiene que mandarlo a 'Corregir esta quincena', que es la única
    puerta con motivo, versión y rastro.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, liq = quincena_pagada_con_plata(client, h)
    _dia(client, h, prov["id"], "2026-06-12", 90)  # 90 L x $2.000 = $180.000
    corregida = _corregir_metiendo_el_dia(client, h, liq["id"], "se olvidó el día 12")

    print("\n===== (b) QUINCENA CORREGIDA (v2) =====")
    _mostrar("corregida", corregida)
    assert corregida["version"] == 2
    assert D(corregida["valor_total"]) == D(680000)
    assert D(corregida["anticipos"]) == D(200000)
    assert D(corregida["neto_a_pagar"]) == D(480000)
    assert D(corregida["pagado"]) == D(300000)
    assert D(corregida["saldo"]) == D(180000)
    assert _cuadra(corregida)

    editar = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "50000"}, headers=h)
    borrar = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  PUT    /anticipos/id -> {editar.status_code} . {_detalle(editar)}")
    print(f"  DELETE /anticipos/id -> {borrar.status_code} . {_detalle(borrar)}")
    assert editar.status_code == 422, editar.text
    assert borrar.status_code == 422, borrar.text
    assert "comprobante corregido" in _detalle(editar), _detalle(editar)
    assert "Corregir esta quincena" in _detalle(editar), _detalle(editar)
    assert "comprobante corregido" in _detalle(borrar), _detalle(borrar)

    despues = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("después", despues)
    assert D(despues["anticipos"]) == D(200000), "el adelanto del -v2 se movió"
    assert D(despues["neto_a_pagar"]) == D(480000)
    assert D(despues["saldo"]) == D(180000)
    assert despues["version"] == 2, "la versión se movió sin pasar por la corrección"
    assert _cuadra(despues)


# ===========================================================================
# (c) PAGADA CON pagado = $0: la que los anticipos cubrieron exacto
# ===========================================================================
def test_la_pagada_que_el_anticipo_cubrio_exacto_tampoco_suelta_el_anticipo(client, base_datos):
    """$180.000 de quincena y $180.000 de adelanto: el neto cayó en cero y `pagar` se fue
    por la rama que NO registra ningún pago. `tiene_pagos` es False.

    Aquí el candado se sostiene solo del estado 'pagada'. Si se cayera, bajarle el
    adelanto a $80.000 dejaría al sistema diciendo que hay que entregarle $100.000 de una
    quincena que se saldó el día que se le dio la plata en la mano.
    """
    h = auth_headers(client, "admin.a")
    _, ant, liq = quincena_cubierta_exacto(client, h)

    print("\n===== (c) PAGADA CON pagado = $0 =====")
    _mostrar("antes", liq)
    assert liq["estado"] == "pagada"
    assert D(liq["pagado"]) == D(0)
    assert _cuadra(liq)

    editar = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "80000"}, headers=h)
    borrar = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  PUT    /anticipos/id -> {editar.status_code} . {_detalle(editar)}")
    print(f"  DELETE /anticipos/id -> {borrar.status_code} . {_detalle(borrar)}")
    assert editar.status_code == 422, editar.text
    assert borrar.status_code == 422, borrar.text
    assert "ya se pagó" in _detalle(editar)

    despues = client.get(f"{API}/{liq['id']}", headers=h).json()
    sigue = client.get(f"{ANTICIPOS}/{ant['id']}", headers=h).json()
    _mostrar("después", despues)
    print(f"  el adelanto sigue en {sigue['valor']} . bloqueado={sigue['bloqueado']}")
    assert D(sigue["valor"]) == D(180000)
    assert D(despues["anticipos"]) == D(180000)
    assert D(despues["neto_a_pagar"]) == D(0)
    assert D(despues["saldo"]) == D(0)
    assert _cuadra(despues)


# ===========================================================================
# (d) LA TRAMPA: (c) después de corregirla. Solo queda `version > 1`
# ===========================================================================
def test_corregida_y_sin_pagos_el_candado_se_sostiene_solo_de_la_version(client, base_datos):
    """El caso donde las otras DOS preguntas caen a la vez y solo queda una.

    Se parte de la quincena cubierta exacto ($180.000 de leche, $180.000 de adelanto,
    `pagado = $0`, 'pagada') y se le mete el día olvidado de $90.000. Después de corregir:

        total     $270.000
        anticipos -$180.000
        neto       $90.000
        pagado         $0      <- sigue sin un solo pago registrado
        saldo      $90.000     <- estado 'parcial', NO 'pagada'

    Ahí `tiene_pagos` es False y `estado == 'pagada'` es False. Lo único que cierra la
    puerta es `version > 1`. Si esa pregunta faltara, desde la pantalla se podría borrar
    el adelanto de $180.000 y el neto del comprobante -v2 que el productor tiene en la
    mano pasaría de $90.000 a $270.000: $180.000 que el sistema mandaría a entregar por
    segunda vez.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, liq = quincena_cubierta_exacto(client, h)
    _dia(client, h, prov["id"], "2026-06-12", 50)  # 50 L x $1.800 = $90.000
    corregida = _corregir_metiendo_el_dia(client, h, liq["id"], "faltó el día 12")

    print("\n===== (d) CORREGIDA, SIN PAGOS Y SIN ESTADO 'pagada' =====")
    _mostrar("corregida", corregida)
    assert corregida["version"] == 2
    assert corregida["estado"] == "parcial", "el estado que apaga los otros dos guardias"
    assert D(corregida["valor_total"]) == D(270000)
    assert D(corregida["anticipos"]) == D(180000)
    assert D(corregida["neto_a_pagar"]) == D(90000)
    assert D(corregida["pagado"]) == D(0), "la trampa: sigue sin un solo pago"
    assert corregida["pagos"] == []
    assert D(corregida["saldo"]) == D(90000)
    assert _cuadra(corregida)

    editar = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "1"}, headers=h)
    borrar = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  PUT    /anticipos/id -> {editar.status_code} . {_detalle(editar)}")
    print(f"  DELETE /anticipos/id -> {borrar.status_code} . {_detalle(borrar)}")
    assert editar.status_code == 422, (
        "SE ABRIÓ LA PUERTA: con pagado = $0 y estado 'parcial', el único guardia que "
        "quedaba era version > 1 y no aguantó"
    )
    assert borrar.status_code == 422, borrar.text
    assert "comprobante corregido" in _detalle(editar), _detalle(editar)
    assert "comprobante corregido" in _detalle(borrar), _detalle(borrar)

    despues = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("después", despues)
    assert D(despues["anticipos"]) == D(180000), "el adelanto del -v2 se movió"
    assert D(despues["neto_a_pagar"]) == D(90000), "el neto del papel entregado cambió"
    assert D(despues["saldo"]) == D(90000)
    assert _cuadra(despues)


# ===========================================================================
# El candado que VE LA PANTALLA tiene que decir lo mismo que el servidor
# ===========================================================================
def test_el_campo_bloqueado_dice_exactamente_lo_que_hace_el_servidor(client, base_datos):
    """`AnticipoRead.bloqueado` es lo que la pantalla usa para poner o quitar el candado.

    Si dijera "se puede" donde el servidor rebota, el dueño oprimiría un botón que
    siempre falla. Si dijera "no se puede" donde sí se puede, escondería un arreglo
    legítimo. Se recorre la vida entera de un adelanto de $180.000 y en CADA estado se
    compara `bloqueado` contra lo que de verdad hace el `PUT`.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Rosalba", 1800)
    _dia(client, h, prov["id"], "2026-06-02", 100)
    ant = _anticipo(client, h, prov["id"], "2026-06-03", 180000)

    print("\n===== (e) LA PANTALLA CONTRA EL SERVIDOR =====")
    print(f"  {'momento':<28}{'estado liq':<12}{'bloqueado':>10}{'PUT pasa':>10}")

    filas = []

    def medir(momento, estado_esperado):
        """Lee `bloqueado` y enseguida intenta el PUT con el MISMO valor que ya tiene.

        Se manda el valor idéntico a propósito: lo único que se está midiendo es si la
        puerta abre, sin mover un peso.

        Y SE EXIGE EL ESTADO DE LA QUINCENA ANTES DE MEDIR, porque el propio sondeo
        tiene efecto: corregir el anticipo de una APROBADA la devuelve a 'borrador' (es
        la regla que ya existía, el visto bueno se cae cuando cambian las cifras). Sin
        este `assert` la medición se corría sola —se sondeaba 'pagada' sobre una
        quincena que en realidad había vuelto a borrador— y la prueba pasaba midiendo
        otra cosa. Pasó de verdad escribiendo esta prueba.
        """
        visto = client.get(f"{ANTICIPOS}/{ant['id']}", headers=h).json()
        assert visto["liquidacion_estado"] == estado_esperado, (
            f"la medición se corrió: en '{momento}' la quincena está en "
            f"'{visto['liquidacion_estado']}' y no en '{estado_esperado}'"
        )
        r = client.put(
            f"{ANTICIPOS}/{ant['id']}", json={"valor": str(visto["valor"])}, headers=h
        )
        deja_entrar = r.status_code == 200
        print(
            f"  {momento:<28}{str(visto['liquidacion_estado']):<12}"
            f"{str(visto['bloqueado']):>10}{str(deja_entrar):>10}"
        )
        filas.append((momento, visto["bloqueado"], deja_entrar))
        return visto

    medir("suelto, sin quincena", None)

    liq = _generar(client, h, prov["id"])
    medir("quincena en borrador", "borrador")

    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    medir("quincena aprobada", "aprobada")
    # El sondeo de arriba la devolvió a borrador —se puede corregir el anticipo de una
    # aprobada, y por eso mismo el visto bueno se cae—. Se vuelve a aprobar para seguir.
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200

    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    assert D(pagada.json()["pagado"]) == D(0), "esta familia se cierra sin registrar pago"
    medir("pagada con pagado = $0", "pagada")

    _dia(client, h, prov["id"], "2026-06-12", 50)
    corregida = _corregir_metiendo_el_dia(client, h, liq["id"], "faltó el día 12")
    assert corregida["version"] == 2 and corregida["estado"] == "parcial"
    medir("corregida (v2), sin pagos", "parcial")

    for momento, bloqueado, deja_entrar in filas:
        assert bloqueado != deja_entrar, (
            f"la pantalla y el servidor se contradicen en '{momento}': "
            f"bloqueado={bloqueado} pero el PUT {'pasó' if deja_entrar else 'rebotó'}"
        )


# ===========================================================================
# El que SALE de la quincena queda libre de verdad, y la cifra cuadra
# ===========================================================================
def test_el_anticipo_que_se_suelta_al_corregir_queda_libre_y_la_cifra_cuadra(client, base_datos):
    """Soltar un adelanto en la corrección NO lo borra: lo deja libre para la quincena
    siguiente. Esa plata se entregó; lo que se dice es "no iba en esta quincena".

    Se mide lo que al dueño le importa: (1) al soltarlo, el neto de ESTA quincena sube
    exacto los $200.000 y el desglose sigue cuadrando; (2) el adelanto queda libre de
    verdad —`bloqueado` en False y la pantalla lo deja corregir, porque ya no cuelga de
    ningún comprobante—; y (3) el candado del que SE QUEDÓ no se aflojó de paso.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Hermelinda", 2000)
    _dia(client, h, prov["id"], "2026-06-02", 250)  # $500.000
    sale = _anticipo(client, h, prov["id"], "2026-06-03", 200000, "el que no iba aquí")
    queda = _anticipo(client, h, prov["id"], "2026-06-04", 50000, "este sí iba")
    liq = _generar(client, h, prov["id"])
    assert D(liq["anticipos"]) == D(250000), "los dos adelantos tenían que entrar"
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h).json()

    print("\n===== (f) EL QUE SALE QUEDA LIBRE =====")
    _mostrar("pagada", pagada)
    assert D(pagada["neto_a_pagar"]) == D(250000)
    assert D(pagada["pagado"]) == D(250000)

    corregida = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "ese adelanto era de julio", "anticipos_a_soltar": [sale["id"]]},
        headers=h,
    )
    assert corregida.status_code == 200, corregida.text
    corregida = corregida.json()
    _mostrar("corregida", corregida)
    assert D(corregida["anticipos"]) == D(50000), "quedó descontando el que salió"
    assert D(corregida["neto_a_pagar"]) == D(450000), "el neto tenía que subir $200.000"
    assert D(corregida["pagado"]) == D(250000)
    assert D(corregida["saldo"]) == D(200000)
    assert _cuadra(corregida)

    libre = client.get(f"{ANTICIPOS}/{sale['id']}", headers=h).json()
    preso = client.get(f"{ANTICIPOS}/{queda['id']}", headers=h).json()
    print(f"  el que salió    . liquidacion_id={libre['liquidacion_id']} "
          f". aplicado={libre['aplicado']} . bloqueado={libre['bloqueado']}")
    print(f"  el que se quedó . aplicado={preso['aplicado']} . bloqueado={preso['bloqueado']}")
    assert libre["liquidacion_id"] is None, "no quedó libre: sigue colgado del comprobante"
    assert libre["aplicado"] is False
    # Y por lo mismo: el candado que ve la pantalla tiene que decir lo mismo que el
    # servidor. Antes decía False y el servidor dejaba pasar; ahora los dos traban.
    assert libre["bloqueado"] is True
    assert preso["bloqueado"] is True, "al soltar uno se aflojó el candado del otro"

    # ESTAS DOS LÍNEAS MEDÍAN UN DEFECTO, y lo destapó otro ataque con cifras: el
    # adelanto que la corrección suelta NO es "uno suelto como cualquier otro". YA SALIÓ
    # IMPRESO —el comprobante v2 dice, con todas sus letras, "se le descuenta en la
    # siguiente"— y dejarlo editable permitía borrarlo o rebajarlo a $1 desde la pantalla
    # de anticipos: $300.000 ya entregados que no cobra nadie. Ver
    # test_zzataque_anticipos_dos_veces.py::test_el_adelanto_soltado_no_deberia_poderse_borrar_desde_la_pantalla_de_anticipos
    r = client.put(f"{ANTICIPOS}/{sale['id']}", json={"valor": "210000"}, headers=h)
    print(f"  PUT sobre el que salió    -> {r.status_code}")
    assert r.status_code == 422, r.text
    # Y el que se quedó NO.
    r2 = client.put(f"{ANTICIPOS}/{queda['id']}", json={"valor": "1"}, headers=h)
    print(f"  PUT sobre el que se quedó -> {r2.status_code} . {_detalle(r2)}")
    assert r2.status_code == 422, r2.text

    # Y mover el suelto no le movió un peso al comprobante ya corregido.
    final = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("final", final)
    assert D(final["anticipos"]) == D(50000)
    assert D(final["neto_a_pagar"]) == D(450000)
    assert D(final["saldo"]) == D(200000)
    assert _cuadra(final)


# ===========================================================================
# EL PERMISO: corregir anticipos exige 'administrar'. Compras no.
# ===========================================================================
def crear_usuario_con_rol(db_session, empresa, nombre_rol, username):
    from app.core.security import hash_password
    from app.modules.usuarios.models import Usuario, UsuarioRol

    rol = db_session.scalars(select(Rol).where(Rol.nombre == nombre_rol)).one()
    usuario = Usuario(
        nombre=username.title(),
        apellido="Prueba",
        correo=f"{username}@test.local",
        username=username,
        hashed_password=hash_password(PASSWORD),
        empresa_id=empresa.id,
    )
    db_session.add(usuario)
    db_session.flush()
    db_session.add(UsuarioRol(usuario_id=usuario.id, rol_id=rol.id, empresa_id=empresa.id))
    db_session.commit()
    return usuario


def test_compras_no_corrige_los_anticipos_de_una_quincena_cerrada(client, db_session, base_datos):
    """COMPRAS tiene `liquidaciones:editar` —y por eso el `PUT /anticipos/{id}` le abre
    la puerta: se demuestra corrigiéndole un adelanto suelto de $200.000 a $150.000—.

    Justamente por eso la corrección de la quincena NO se colgó de ese permiso. Si
    Compras pudiera mover los anticipos de una quincena ya pagada, el neto de un
    comprobante entregado cambiaría sin que intervenga nadie con permiso de plata. Aquí
    se mide que las dos puertas de la corrección le responden 403, y que la del dueño
    —Administrador Empresa, el único rol con `liquidaciones:administrar`— sí mueve la
    plata por el mismo camino.
    """
    h_admin = auth_headers(client, "admin.a")
    crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Compras", "rol.compras")
    h_compras = auth_headers(client, "rol.compras")

    print("\n===== (g) COMPRAS CONTRA LA PUERTA DE LA CORRECCIÓN =====")

    # 1) Compras SÍ puede tocar un adelanto suelto: el permiso 'editar' lo tiene.
    prov = _proveedor(client, h_admin, "Nohora", 2000)
    suelto = _anticipo(client, h_admin, prov["id"], "2026-06-03", 200000)
    r = client.put(f"{ANTICIPOS}/{suelto['id']}", json={"valor": "150000"}, headers=h_compras)
    print(f"  Compras corrige un adelanto SUELTO -> {r.status_code}")
    assert r.status_code == 200, (
        "si esto no pasa, la prueba de abajo no demuestra nada: habría que revisar si "
        "Compras sigue teniendo 'liquidaciones:editar'"
    )
    assert D(r.json()["valor"]) == D(150000)

    # 2) La quincena pagada, con ese adelanto ya descontado.
    _dia(client, h_admin, prov["id"], "2026-06-02", 250)  # $500.000
    liq = _generar(client, h_admin, prov["id"])
    assert D(liq["anticipos"]) == D(150000)
    client.post(f"{API}/{liq['id']}/aprobar", headers=h_admin)
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h_admin).json()
    _mostrar("pagada", pagada)
    assert D(pagada["neto_a_pagar"]) == D(350000)
    assert D(pagada["pagado"]) == D(350000)

    # 3) Compras contra las dos puertas de la corrección: 403 en las dos.
    cuerpo = {"motivo": "quiero soltarle el adelanto", "anticipos_a_soltar": [suelto["id"]]}
    prev = client.post(f"{API}/{liq['id']}/corregir/previsualizar", json=cuerpo, headers=h_compras)
    hecho = client.post(f"{API}/{liq['id']}/corregir", json=cuerpo, headers=h_compras)
    print(f"  Compras previsualizar -> {prev.status_code}")
    print(f"  Compras corregir      -> {hecho.status_code}")
    assert prev.status_code == 403, prev.text
    assert hecho.status_code == 403, hecho.text
    # Y por la pantalla de anticipos tampoco: ahí lo para el candado, no el permiso.
    por_al_lado = client.put(
        f"{ANTICIPOS}/{suelto['id']}", json={"valor": "1"}, headers=h_compras
    )
    print(f"  Compras por la pantalla de anticipos -> {por_al_lado.status_code} "
          f". {_detalle(por_al_lado)}")
    assert por_al_lado.status_code == 422, por_al_lado.text

    intacta = client.get(f"{API}/{liq['id']}", headers=h_admin).json()
    _mostrar("sin tocar", intacta)
    assert D(intacta["anticipos"]) == D(150000), "Compras movió el adelanto de una pagada"
    assert D(intacta["neto_a_pagar"]) == D(350000)
    assert intacta["version"] == 1
    assert _cuadra(intacta)

    # 4) El dueño sí, por el mismo camino y con la misma petición.
    hecho_admin = client.post(f"{API}/{liq['id']}/corregir", json=cuerpo, headers=h_admin)
    print(f"  Administrador Empresa corregir -> {hecho_admin.status_code}")
    assert hecho_admin.status_code == 200, hecho_admin.text
    corregida = hecho_admin.json()
    _mostrar("corregida", corregida)
    assert corregida["version"] == 2
    assert D(corregida["anticipos"]) == D(0), "el adelanto tenía que salir"
    assert D(corregida["neto_a_pagar"]) == D(500000)
    assert D(corregida["saldo"]) == D(150000), "el neto subió los $150.000 del adelanto"
    assert _cuadra(corregida)


# ===========================================================================
# EL ADELANTO ES DE UN PRODUCTOR: no se le descuenta a otro
# ===========================================================================
def test_no_se_le_descuenta_a_un_productor_el_adelanto_de_otro(client, base_datos):
    """El adelanto de $500.000 que se le dio a AMPARO no puede aparecer restado en el
    comprobante de LIBARDO. Sería cobrarle a un productor plata que recibió otro, y el
    que la recibió se la quedaría sin descontar.

    Se atacan las dos listas —incluir y soltar— con el id del adelanto ajeno en la mano,
    y también desde la OTRA EMPRESA, que ni siquiera debería ver la quincena.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    _, ant_propio, liq = quincena_pagada_con_plata(client, h_a, nombre="Libardo")

    ajeno = _proveedor(client, h_a, "Amparo", 1800)
    ant_ajeno = _anticipo(client, h_a, ajeno["id"], "2026-06-05", 500000, "de Amparo")

    print("\n===== (h) EL ADELANTO DE OTRO PRODUCTOR =====")
    _mostrar("Libardo", liq)
    print(f"  adelanto de Amparo: $500.000, suelto")

    incluir = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "meter el adelanto de Amparo", "anticipos_a_incluir": [ant_ajeno["id"]]},
        headers=h_a,
    )
    soltar = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "soltar el adelanto de Amparo", "anticipos_a_soltar": [ant_ajeno["id"]]},
        headers=h_a,
    )
    print(f"  incluirlo en la quincena de Libardo -> {incluir.status_code} . {_detalle(incluir)}")
    print(f"  soltarlo de la quincena de Libardo  -> {soltar.status_code} . {_detalle(soltar)}")
    assert incluir.status_code == 422, incluir.text
    assert "no está suelto" in _detalle(incluir), _detalle(incluir)
    assert soltar.status_code == 404, soltar.text

    # Y desde la Quesera B, con los dos ids en la mano: ni la quincena existe para ella.
    desde_b = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "desde la otra empresa", "anticipos_a_soltar": [ant_propio["id"]]},
        headers=h_b,
    )
    print(f"  la Quesera B corrigiendo la quincena de A -> {desde_b.status_code}")
    assert desde_b.status_code == 404, desde_b.text

    intacta = client.get(f"{API}/{liq['id']}", headers=h_a).json()
    sigue_ajeno = client.get(f"{ANTICIPOS}/{ant_ajeno['id']}", headers=h_a).json()
    _mostrar("sin tocar", intacta)
    print(f"  el adelanto de Amparo sigue suelto: liquidacion_id={sigue_ajeno['liquidacion_id']}")
    assert intacta["version"] == 1, "la versión subió con una corrección que rebotó"
    assert D(intacta["anticipos"]) == D(200000), "se le metió el adelanto de Amparo"
    assert D(intacta["neto_a_pagar"]) == D(300000)
    assert sigue_ajeno["liquidacion_id"] is None, "el adelanto de Amparo quedó preso en Libardo"
    assert _cuadra(intacta)


# ===========================================================================
# EL QUE SE SOLTÓ Y SE LO LLEVÓ LA QUINCENA SIGUIENTE NO VUELVE A ENTRAR
# ===========================================================================
def test_el_adelanto_soltado_que_recogio_la_siguiente_no_se_descuenta_dos_veces(client, base_datos):
    """La vuelta completa del adelanto que sale, y el descuento doble que la cerraría.

    Junio: $500.000 de leche menos $200.000 de adelanto = $300.000 entregados. Se corrige
    SOLTANDO el adelanto: junio queda en $500.000 de neto con $200.000 por entregar, y el
    adelanto queda libre. Julio se genera y —como debe— se lo descuenta: $600.000 menos
    $200.000 = $400.000.

    Y AHÍ ESTÁ LA TRAMPA: si ahora se pudiera volver a corregir junio para meterlo otra
    vez, los mismos $200.000 quedarían restados en DOS comprobantes. El productor
    entregó la plata una vez y se la cobrarían dos. Tiene que rebotar, y la cuenta final
    es que el adelanto se descuenta EXACTAMENTE UNA VEZ entre las dos quincenas.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, liq = quincena_pagada_con_plata(client, h, nombre="Evangelina")

    print("\n===== (i) EL ADELANTO QUE SALE Y LO RECOGE LA SIGUIENTE =====")
    _mostrar("junio pagada", liq)

    junio = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "ese adelanto era de la otra quincena", "anticipos_a_soltar": [ant["id"]]},
        headers=h,
    )
    assert junio.status_code == 200, junio.text
    junio = junio.json()
    _mostrar("junio v2", junio)
    assert D(junio["anticipos"]) == D(0)
    assert D(junio["neto_a_pagar"]) == D(500000)
    assert D(junio["saldo"]) == D(200000), "le quedan debiendo los $200.000 del adelanto"
    assert _cuadra(junio)

    # Julio: 300 L a $2.000 = $600.000, y el adelanto libre se lo descuenta ella.
    client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-07-02", "proveedor_id": prov["id"], "cantidad_litros": "300"},
        headers=h,
    )
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    julio = next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov["id"])
    _mostrar("julio", julio)
    assert D(julio["valor_total"]) == D(600000)
    assert D(julio["anticipos"]) == D(200000), "julio no recogió el adelanto que junio soltó"
    assert D(julio["neto_a_pagar"]) == D(400000)
    assert _cuadra(julio)

    # LA TRAMPA: volver a meterlo en junio.
    otra_vez = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "pensándolo bien sí iba aquí", "anticipos_a_incluir": [ant["id"]]},
        headers=h,
    )
    print(f"  volver a meterlo en junio -> {otra_vez.status_code} . {_detalle(otra_vez)}")
    assert otra_vez.status_code == 422, (
        "SE DESCONTÓ DOS VECES: los mismos $200.000 quedarían restados en junio y en julio"
    )
    assert "no está suelto" in _detalle(otra_vez), _detalle(otra_vez)

    junio_final = client.get(f"{API}/{liq['id']}", headers=h).json()
    julio_final = client.get(f"{API}/{julio['id']}", headers=h).json()
    _mostrar("junio final", junio_final)
    _mostrar("julio final", julio_final)
    descontado = D(junio_final["anticipos"]) + D(julio_final["anticipos"])
    print(f"  el adelanto de $200.000 quedó descontado en total: {descontado}")
    assert descontado == D(200000), "el adelanto se descontó más de una vez"
    assert junio_final["version"] == 2, "la versión subió con una corrección que rebotó"
    assert _cuadra(junio_final)
    assert _cuadra(julio_final)


# ===========================================================================
# EL DESGLOSE DE LA CORRECCIÓN SUMA EXACTO LA DIFERENCIA (regla de la casa)
# ===========================================================================
def test_los_tres_movimientos_a_la_vez_y_el_desglose_suma_la_diferencia(client, base_datos):
    """Las TRES cosas que se le pueden hacer a un anticipo, en una sola corrección, y el
    renglón que queda escrito tiene que sumar exacto lo que se movió la cifra grande.

    La quincena: 250 L a $2.000 = $500.000, con dos adelantos descontados —A $200.000 y
    B $50.000— y $250.000 ya entregados. Después aparece un tercero, C por $80.000, que
    quedó suelto.

    En una sola corrección: SALE A, ENTRA C, y a B se le corrige la cifra de $50.000 a
    $70.000.

        anticipos antes                        $250.000
          − A que sale                        −$200.000
          + C que entra                        +$80.000
          + lo que le subió B                  +$20.000
          ────────────────────────────────────────────
        anticipos después                      $150.000

    Y el neto: $500.000 − $150.000 = $350.000, de los cuales ya se entregaron $250.000,
    así que quedan $100.000 por entregar. El desglose que queda guardado se suma renglón
    por renglón contra esa diferencia: si no cuadrara, el soporte de la corrección
    estaría rompiendo la misma regla que la corrección existe para cuidar.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Tulia", 2000)
    _dia(client, h, prov["id"], "2026-06-02", 250)  # $500.000
    a = _anticipo(client, h, prov["id"], "2026-06-03", 200000, "A, el que sale")
    b = _anticipo(client, h, prov["id"], "2026-06-04", 50000, "B, al que se le corrige")
    liq = _generar(client, h, prov["id"])
    assert D(liq["anticipos"]) == D(250000)
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h).json()

    print("\n===== (j) LOS TRES MOVIMIENTOS EN UNA SOLA CORRECCIÓN =====")
    _mostrar("pagada", pagada)
    assert D(pagada["neto_a_pagar"]) == D(250000)
    assert D(pagada["pagado"]) == D(250000)

    # C aparece después: nace suelto y no lo recogió nadie.
    c = _anticipo(client, h, prov["id"], "2026-06-05", 80000, "C, el que entra")

    # La previsualización primero: es la cifra que el dueño compara con su papel.
    cuerpo = {
        "motivo": "A era de julio, C faltaba, y a B le sobraba un cero",
        "anticipos_a_soltar": [a["id"]],
        "anticipos_a_incluir": [c["id"]],
        "valores_de_anticipos": [{"anticipo_id": b["id"], "valor": "70000"}],
    }
    prev = client.post(f"{API}/{liq['id']}/corregir/previsualizar", json=cuerpo, headers=h)
    assert prev.status_code == 200, prev.text
    prev = prev.json()
    print(f"  previsualización . anticipos {prev['anticipos_antes']} -> {prev['anticipos_despues']} "
          f". neto {prev['neto_antes']} -> {prev['neto_despues']} "
          f". saldo {prev['saldo_antes']} -> {prev['saldo_despues']}")
    assert D(prev["anticipos_antes"]) == D(250000)
    assert D(prev["anticipos_despues"]) == D(150000)
    assert D(prev["neto_despues"]) == D(350000)
    assert D(prev["saldo_despues"]) == D(100000)
    # Y el diálogo tiene que MOSTRAR los tres para que el dueño los reconozca por fecha.
    assert {x["anticipo_id"] for x in prev["anticipos_aplicados"]} == {a["id"], b["id"]}
    assert {x["anticipo_id"] for x in prev["anticipos_sueltos"]} == {c["id"]}

    hecha = client.post(f"{API}/{liq['id']}/corregir", json=cuerpo, headers=h)
    assert hecha.status_code == 200, hecha.text
    corregida = hecha.json()
    _mostrar("corregida", corregida)
    # EL BOTÓN ESCRIBIÓ LO MISMO QUE PROMETIÓ EL DIÁLOGO.
    assert D(corregida["anticipos"]) == D(prev["anticipos_despues"]) == D(150000)
    assert D(corregida["neto_a_pagar"]) == D(350000)
    assert D(corregida["pagado"]) == D(250000)
    assert D(corregida["saldo"]) == D(100000)
    assert corregida["version"] == 2
    assert _cuadra(corregida)

    # Cada anticipo quedó donde debía.
    visto_a = client.get(f"{ANTICIPOS}/{a['id']}", headers=h).json()
    visto_b = client.get(f"{ANTICIPOS}/{b['id']}", headers=h).json()
    visto_c = client.get(f"{ANTICIPOS}/{c['id']}", headers=h).json()
    print(f"  A . liq={visto_a['liquidacion_id']} valor={visto_a['valor']} bloqueado={visto_a['bloqueado']}")
    print(f"  B . liq={visto_b['liquidacion_id']} valor={visto_b['valor']} bloqueado={visto_b['bloqueado']}")
    print(f"  C . liq={visto_c['liquidacion_id']} valor={visto_c['valor']} bloqueado={visto_c['bloqueado']}")
    assert visto_a["liquidacion_id"] is None, "A no se soltó"
    assert D(visto_a["valor"]) == D(200000), "al que sale no se le toca la cifra: esa plata se entregó"
    assert D(visto_b["valor"]) == D(70000)
    assert visto_c["liquidacion_id"] == liq["id"], "C no entró"
    # EL QUE ENTRA POR LA CORRECCIÓN QUEDA TRABADO EN EL ACTO. Si no, el dueño podría
    # meterlo con motivo y versión y enseguida cambiarle la cifra por la pantalla, sin
    # ninguna de las cinco protecciones.
    assert visto_c["bloqueado"] is True, "el adelanto que acaba de entrar quedó destrabado"
    assert visto_b["bloqueado"] is True
    por_al_lado = client.put(f"{ANTICIPOS}/{c['id']}", json={"valor": "1"}, headers=h)
    print(f"  PUT sobre C recién entrado -> {por_al_lado.status_code} . {_detalle(por_al_lado)}")
    assert por_al_lado.status_code == 422, por_al_lado.text

    # ------------------------------------------------- el desglose que quedó escrito
    corrs = client.get(f"{API}/{liq['id']}/correcciones", headers=h)
    assert corrs.status_code == 200, corrs.text
    corr = corrs.json()[0]
    print(f"  renglón . anticipos {corr['anticipos_antes']} -> {corr['anticipos_despues']}")
    for mov in corr["anticipos_cambiados"]:
        print(f"    {mov}")
    assert D(corr["anticipos_antes"]) == D(250000)
    assert D(corr["anticipos_despues"]) == D(150000)

    # LA REGLA DE LA CASA DENTRO DEL PROPIO SOPORTE: los renglones suman la diferencia.
    delta = D(0)
    for mov in corr["anticipos_cambiados"]:
        if mov["accion"] == "salio":
            delta -= D(mov["valor"])
        elif mov["accion"] == "entro":
            delta += D(mov["valor"])
        elif mov["accion"] == "valor":
            delta += D(mov["valor"]) - D(mov["valor_antes"])
        else:  # pragma: no cover - acción nueva sin contar
            raise AssertionError(f"acción desconocida en el desglose: {mov}")
    esperado = D(corr["anticipos_despues"]) - D(corr["anticipos_antes"])
    print(f"  los renglones suman {delta} y la cifra grande se movió {esperado}")
    assert delta == esperado == D(-100000), (
        "el desglose de la corrección no suma la diferencia: el dueño lo verifica con "
        "calculadora contra la hoja vieja"
    )
    # Y cada movimiento está nombrado una sola vez: C entra, no entra Y cambia de valor.
    assert len(corr["anticipos_cambiados"]) == 3, corr["anticipos_cambiados"]


# ===========================================================================
# EL DOBLE CLIC: la misma corrección repetida no descuenta dos veces
# ===========================================================================
def test_repetir_la_correccion_de_los_anticipos_no_descuenta_dos_veces(client, base_datos):
    """El dueño oprime el botón, la pantalla se queda pensando, y él vuelve a oprimir.

    La cifra de anticipos se vuelve a sumar DESDE CERO con los que quedan marcados —no
    se le suma ni se le resta a la guardada— justamente para que esto no mueva un peso.
    Se repiten las tres listas y se mide la plata después de cada repetición:

      · SOLTAR repetido: la segunda vez ese adelanto ya no está en la quincena -> 404, y
        el neto no sube $200.000 dos veces (serían $700.000 de neto sobre $500.000 de
        leche).
      · INCLUIR repetido: la segunda vez ya no está suelto -> 422, y no se descuenta dos
        veces.
      · CORREGIR EL VALOR repetido con la MISMA cifra: pasa, pero no mueve la plata ni
        ensucia el desglose con un renglón que no dice nada.
    """
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Custodio", 2000)
    _dia(client, h, prov["id"], "2026-06-02", 250)  # $500.000
    a = _anticipo(client, h, prov["id"], "2026-06-03", 200000, "el que sale")
    b = _anticipo(client, h, prov["id"], "2026-06-04", 50000, "el del valor")
    liq = _generar(client, h, prov["id"])
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h).json()
    c = _anticipo(client, h, prov["id"], "2026-06-05", 80000, "el que entra")

    print("\n===== (k) EL DOBLE CLIC =====")
    _mostrar("pagada", pagada)
    assert D(pagada["anticipos"]) == D(250000)

    def repetir(titulo, cuerpo, esperado_segunda, anticipos_esperados, neto_esperado):
        primera = client.post(f"{API}/{liq['id']}/corregir", json=cuerpo, headers=h)
        assert primera.status_code == 200, primera.text
        segunda = client.post(f"{API}/{liq['id']}/corregir", json=cuerpo, headers=h)
        estado = client.get(f"{API}/{liq['id']}", headers=h).json()
        print(f"  {titulo}: 1a -> {primera.status_code}, 2a -> {segunda.status_code} "
              f". {_detalle(segunda)}")
        _mostrar(titulo, estado)
        assert segunda.status_code == esperado_segunda, segunda.text
        assert D(estado["anticipos"]) == anticipos_esperados, (
            f"{titulo}: la repetición movió la cifra de anticipos"
        )
        assert D(estado["neto_a_pagar"]) == neto_esperado
        assert _cuadra(estado), f"{titulo}: dejó de cuadrar"
        return estado

    # $250.000 − $200.000 de A que sale = $50.000. Neto $450.000.
    repetir(
        "soltar A",
        {"motivo": "A era de julio", "anticipos_a_soltar": [a["id"]]},
        404,
        D(50000),
        D(450000),
    )
    # $50.000 + $80.000 de C que entra = $130.000. Neto $370.000.
    repetir(
        "incluir C",
        {"motivo": "faltaba C", "anticipos_a_incluir": [c["id"]]},
        422,
        D(130000),
        D(370000),
    )
    # B pasa de $50.000 a $70.000: $150.000. Neto $350.000.
    #
    # LA REPETICIÓN ESPERABA 200 Y ESO ERA DEJAR PASAR UN DEFECTO, que destapó otro
    # ataque: volver a mandar el mismo valor no cambia una sola cifra, pero subía la
    # versión igual — o sea que salía un comprobante con folio '-v2', la banda
    # COMPROBANTE CORREGIDO y la nota de que reemplaza al anterior, idéntico al que el
    # productor ya tiene. Un viaje a la finca a cambiar un papel por otro igual. Esta
    # misma prueba ya lo olía: abajo comprobaba que el renglón no dijera que algo
    # cambió. Ahora rebota antes de escribir nada. Ver
    # test_zzataque_anticipos_bordes.py::test_una_correccion_que_no_cambia_una_sola_cifra...
    version_antes = client.get(f"{API}/{liq['id']}", headers=h).json()["version"]
    ultimo = repetir(
        "valor de B",
        {"motivo": "a B le sobraba un cero", "valores_de_anticipos": [{"anticipo_id": b["id"], "valor": "70000"}]},
        422,
        D(150000),
        D(350000),
    )

    # Y LO QUE IMPORTA: no quedó ninguna versión nueva ni ningún renglón de corrección
    # por una repetición que no movió un peso.
    despues = client.get(f"{API}/{liq['id']}", headers=h).json()
    print(f"  la repetición del valor dejó la versión en v{despues['version']} "
          f"(estaba en v{version_antes})")
    # UNA sola versión nueva: la de la corrección que SÍ movió el valor. La repetición
    # no dejó ninguna. (`version_antes` se leyó antes de las DOS llamadas de `repetir`.)
    assert despues["version"] == version_antes + 1, (
        "la repetición emitió un comprobante nuevo sin cambiar una sola cifra"
    )
    correcciones = client.get(f"{API}/{liq['id']}/correcciones", headers=h).json()
    ultima = max(correcciones, key=lambda x: x["version_nueva"])
    assert ultima["anticipos_cambiados"], (
        "la última corrección guardada tiene que ser la que SÍ movió el valor"
    )
    assert D(ultimo["saldo"]) == D(350000) - D(250000)


# ===========================================================================
# DEFECTO: al adelanto que la corrección soltó se le puede dar borrar, y esos
#          $200.000 que ya están en la mano del productor no los cobra nadie
# ===========================================================================
def test_al_adelanto_que_la_correccion_solto_no_deberia_poder_darsele_borrar(client, base_datos):
    """Lo que la corrección promete al soltar un adelanto, con todas sus letras: "esa
    plata se entregó; lo que se dice no es que no existió, sino que no iba en esta
    quincena, y la quincena SIGUIENTE se lo descuenta". Por eso no lo borra: lo suelta.

    Pero apenas queda suelto, la pantalla de anticipos lo deja BORRAR. Y ahí la promesa
    se cae:

        junio: $500.000 de leche, $200.000 de adelanto, se le entregan $300.000
        se corrige soltando el adelanto -> junio queda en $500.000 de neto, le faltan
          $200.000 por entregar, y el adelanto queda libre esperando a julio
        alguien le da BORRAR al adelanto            <- 204, sin motivo ni versión
        julio: $600.000 de leche, anticipos $0, neto $600.000

    Suma final: el productor recibe $500.000 de junio + $600.000 de julio = $1.100.000,
    MÁS los $200.000 que ya tenía en la mano desde el 03/06. Se le pagaron $200.000 de
    más y no hay ningún documento que los reclame. El renglón de la corrección sigue
    diciendo "salió el adelanto del 03/06 por $200.000" y ese adelanto ya no existe.

    Lo que se esperaría: que el `DELETE` rebote mientras ese adelanto siga siendo la
    contrapartida de una corrección —igual que rebota el de una quincena pagada— o, como
    mínimo, que julio siga descontándolo. Hoy no pasa ninguna de las dos.

    NOTA: mientras el adelanto estaba DENTRO de la quincena pagada estaba trabado. Es la
    corrección la que lo saca de detrás del candado, así que la puerta la abre esta
    función nueva.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, liq = quincena_pagada_con_plata(client, h, nombre="Sixta")

    print("\n===== (l) EL ADELANTO SOLTADO QUE SE PUEDE BORRAR =====")
    _mostrar("junio pagada", liq)

    junio = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "ese adelanto era de julio", "anticipos_a_soltar": [ant["id"]]},
        headers=h,
    ).json()
    _mostrar("junio v2", junio)
    assert D(junio["anticipos"]) == D(0)
    assert D(junio["saldo"]) == D(200000), "junio le quedó debiendo los $200.000"

    borrado = client.delete(f"{ANTICIPOS}/{ant['id']}", headers=h)
    print(f"  DELETE del adelanto que la corrección soltó -> {borrado.status_code} "
          f". {_detalle(borrado)}")

    # Julio: 300 L a $2.000 = $600.000. Tendría que descontarle los $200.000.
    client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-07-02", "proveedor_id": prov["id"], "cantidad_litros": "300"},
        headers=h,
    )
    julio = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "proveedor"},
        headers=h,
    ).json()["generadas"]
    julio = next(x for x in julio if x["proveedor_id"] == prov["id"])
    _mostrar("julio", julio)

    corr = client.get(f"{API}/{liq['id']}/correcciones", headers=h).json()[0]
    print(f"  el rastro sigue diciendo: {corr['anticipos_cambiados']}")

    junio_final = client.get(f"{API}/{liq['id']}", headers=h).json()
    le_toca = D(junio_final["saldo"]) + D(julio["neto_a_pagar"])
    print(f"  le falta cobrar junio {junio_final['saldo']} + julio {julio['neto_a_pagar']} "
          f"= {le_toca}, y ya tiene $200.000 en la mano desde el 03/06")

    assert D(julio["anticipos"]) == D(200000), (
        "los $200.000 que la corrección soltó para que los cobrara julio se perdieron al "
        "borrar el adelanto: nadie los descuenta"
    )
