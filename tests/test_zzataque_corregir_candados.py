"""ATAQUE 2 — ¿corregir una quincena pagada aflojó algún candado sin querer?

La corrección de una quincena ya pagada mueve dos cosas peligrosas a la vez: le sube la
VERSIÓN al comprobante y le vuelve a DEDUCIR EL ESTADO. Y el estado es justo la pregunta
que hacen todos los candados del sistema. Así que lo que se mide aquí no es que la
corrección funcione —eso lo mide otra prueba— sino que DESPUÉS de corregir no se haya
abierto ninguna puerta que antes estaba cerrada.

LA FAMILIA PELIGROSA, y es el caso central de todo este trabajo: la quincena que los
ANTICIPOS CUBRIERON EXACTO. $180.000 de leche contra $180.000 ya adelantados en la mano:
neto $0,00, el botón Pagar la marca 'pagada' SIN registrar un solo peso, y queda con
`pagado = $0`. Ahí los tres candados del sistema se sostienen en un solo hilo:

    _ya_salio_plata(liq) = tiene_pagos  or  estado == 'pagada'  or  version > 1
                              ↑ falso        ↑ deja de serlo         ↑ lo único que queda
                              (pagado = $0)  (corregir la manda
                                              a 'parcial')

Corregirla apaga las DOS primeras de un solo golpe. Si la tercera no estuviera, todos los
días de esa quincena quedarían editables —incluidos los que componen el comprobante del
TRANSPORTADOR, que es de otra persona y ni siquiera sale en esa pantalla—.

Se ataca eso, y de paso las otras cinco puertas: recalcular, recuadrar, corregir el
precio de un día, anular, el borrado, y el PUT de observaciones. Más los dos rebotes de
la corrección misma: la deuda que ya viajó, y el comprobante de flete.
"""
import uuid as _uuid
from decimal import Decimal

import pytest

from app.core.context import RequestContext
from app.core.exceptions import BusinessError
from app.modules.liquidaciones.service import LiquidacionService
from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
RECEPCIONES = "/api/v1/recepciones"
ANTICIPOS = "/api/v1/anticipos"

INICIO = "2026-06-01"
FIN = "2026-06-15"


def D(v):
    return Decimal(str(v))


def _cuadra(liq) -> bool:
    """LA REGLA DE LA CASA: neto a pagar = pagado + saldo, al centavo."""
    return D(liq["neto_a_pagar"]) == D(liq["pagado"]) + D(liq["saldo"])


def _mostrar(titulo, liq):
    print(
        f"  {titulo:<26}· estado {liq['estado']:<9}· v{liq['version']} "
        f"· total {liq['valor_total']:>12} · anticipos {liq['anticipos']:>10} "
        f"· neto {liq['neto_a_pagar']:>12} · pagado {liq['pagado']:>12} "
        f"· saldo {liq['saldo']:>12}"
    )


# ---------------------------------------------------------------------------
# Montajes
# ---------------------------------------------------------------------------
def _montar_con_flete(client, h, nombre="Libardo"):
    """Un proveedor y un transportador que recoge en su ruta.

    El transportador va SIEMPRE porque su comprobante es la otra mitad del riesgo: la
    misma fila de recepciones compone los dos papeles, y quien corrige la leche no ve el
    del flete por ninguna parte.
    """
    ruta = client.post(
        "/api/v1/rutas",
        json={"nombre": f"Ruta {nombre}", "municipio": "Granada"},
        headers=h,
    ).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={
            "nombre": f"Stella {nombre}",
            "valor_transporte": "100",
            "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "100"}],
        },
        headers=h,
    ).json()
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": "1800"},
        headers=h,
    ).json()
    return proveedor, transportador


def _anotar(client, h, proveedor, transportador, fecha, litros):
    cuerpo = {
        "fecha": fecha,
        "proveedor_id": proveedor["id"],
        "cantidad_litros": str(litros),
    }
    if transportador is not None:
        cuerpo["transportador_id"] = transportador["id"]
    r = client.post(RECEPCIONES, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, proveedor, tipo="ambos", inicio=INICIO, fin=FIN):
    gen = client.post(
        f"{API}/generar",
        json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo},
        headers=h,
    )
    assert gen.status_code in (200, 201), gen.text
    generadas = gen.json()["generadas"]
    leche = next(
        (
            x
            for x in generadas
            if x["tipo"] == "proveedor" and x["proveedor_id"] == proveedor["id"]
        ),
        None,
    )
    flete = next((x for x in generadas if x["tipo"] == "transportador"), None)
    return leche, flete


def _quincena_que_los_anticipos_cubrieron_exacto(client, h, nombre="Libardo"):
    """LA FAMILIA PELIGROSA, montada de verdad y no simulada.

    100 L × $1.800 = $180.000 de leche, y $180.000 de anticipo ya entregados en la mano.
    El neto queda en $0,00 clavado, así que "Pagar" la cierra por la rama
    `pendiente <= CERO`: 'pagada' con `pagado = $0` y sin un solo registro de pago.

    Devuelve (proveedor, transportador, recepcion del día, liq de leche, liq de flete).
    """
    proveedor, transportador = _montar_con_flete(client, h, nombre)
    dia = _anotar(client, h, proveedor, transportador, "2026-06-02", "100")

    ant = client.post(
        ANTICIPOS,
        json={
            "tipo": "proveedor",
            "proveedor_id": proveedor["id"],
            "fecha": "2026-06-02",
            "valor": "180000",
        },
        headers=h,
    )
    assert ant.status_code == 201, ant.text

    leche, flete = _generar(client, h, proveedor)
    assert client.post(f"{API}/{leche['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{leche['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    return proveedor, transportador, dia, pagada.json(), flete


# ---------------------------------------------------------------------------
# (0) La familia peligrosa existe de verdad: pagada con pagado = $0
# ---------------------------------------------------------------------------
def test_la_quincena_que_el_anticipo_cubrio_exacto_queda_pagada_con_pagado_en_cero(
    client, base_datos
):
    """Sin esto, todo lo que sigue mediría un caso imaginario.

    $180.000 de leche contra $180.000 de anticipo: el neto es $0,00 y la quincena se
    cierra sin registrar un peso. Ese `pagado = $0` es lo que apaga `tiene_pagos`, y por
    eso el único hilo que sostiene los candados después de corregir es la versión.
    """
    h = auth_headers(client, "admin.a")
    _, _, _, leche, _ = _quincena_que_los_anticipos_cubrieron_exacto(client, h)

    print("\n===== (0) PAGADA CON PAGADO = $0 =====")
    _mostrar("recién pagada", leche)

    assert leche["estado"] == "pagada"
    assert D(leche["valor_total"]) == D(180000)
    assert D(leche["anticipos"]) == D(180000)
    assert D(leche["neto_a_pagar"]) == D(0)
    assert D(leche["pagado"]) == D(0), "si aquí hubiera un pago, el ataque no aplica"
    assert D(leche["saldo"]) == D(0)
    assert leche["version"] == 1
    assert _cuadra(leche)


# ---------------------------------------------------------------------------
# (1) EL CASO CENTRAL: corregida esa quincena, sus días SIGUEN trabados
# ---------------------------------------------------------------------------
def test_corregida_la_pagada_con_pagado_cero_sus_dias_siguen_trabados(client, base_datos):
    """Se corrige la quincena de la familia peligrosa y se le mide el candado.

    Después de entrar el día olvidado del 12/06 ($90.000), la quincena queda en 'parcial'
    con `pagado = $0`: `tiene_pagos` es falso Y el estado ya no es 'pagada'. Si los días
    quedaran sueltos, cualquiera podría cambiarle los litros al día del 02/06 —el que ya
    se saldó con $180.000 de anticipo entregado en la mano— y el comprobante que el
    productor tiene guardado dejaría de ser la suma de sus días.

    Se miden las TRES puertas de Recepción diaria: los litros (que traban las dos
    liquidaciones), el precio por litro (que traba solo la leche) y el borrado del día.
    """
    h = auth_headers(client, "admin.a")
    prov, transp, dia_02, leche, flete = _quincena_que_los_anticipos_cubrieron_exacto(
        client, h
    )
    dia_12 = _anotar(client, h, prov, transp, "2026-06-12", "50")

    print("\n===== (1) CANDADOS DESPUÉS DE CORREGIR LA PAGADA CON PAGADO = $0 =====")
    _mostrar("antes de corregir", leche)

    hecho = client.post(
        f"{API}/{leche['id']}/corregir",
        json={
            "motivo": "se anotó tarde el día 12",
            "recepciones_a_incluir": [dia_12["id"]],
        },
        headers=h,
    )
    assert hecho.status_code == 200, hecho.text
    corregida = hecho.json()
    _mostrar("corregida", corregida)

    # El escenario que hace peligroso el ataque: ni pago, ni estado 'pagada'.
    assert corregida["estado"] == "parcial"
    assert corregida["version"] == 2
    assert D(corregida["pagado"]) == D(0), "el hilo de `tiene_pagos` está cortado"
    assert D(corregida["valor_total"]) == D(270000)
    assert D(corregida["saldo"]) == D(90000)
    assert _cuadra(corregida)

    # (a) LOS LITROS del día que ya se saldó con el anticipo.
    r = client.put(
        f"{RECEPCIONES}/{dia_02['id']}", json={"cantidad_litros": "999"}, headers=h
    )
    print(f"  cambiar los litros del 02/06 a 999 L: {r.status_code} · {r.text[:150]}")
    assert r.status_code == 422, "SE ABRIÓ EL CANDADO: los litros de una quincena "\
        "corregida se dejaron cambiar"

    # (b) EL PRECIO POR LITRO, que solo traba la liquidación de la leche: si el candado
    # de la leche se hubiera caído, este pasaría aunque el del flete siguiera puesto.
    r = client.put(
        f"{RECEPCIONES}/{dia_02['id']}", json={"precio_litro": "9999"}, headers=h
    )
    print(f"  cambiar el precio del 02/06 a $9.999: {r.status_code} · {r.text[:150]}")
    assert r.status_code == 422, "SE ABRIÓ EL CANDADO DE LA LECHE: el precio por litro "\
        "de una quincena corregida se dejó cambiar"

    # (c) BORRAR EL DÍA, que se lo saca a las DOS liquidaciones de un golpe.
    r = client.delete(f"{RECEPCIONES}/{dia_02['id']}", headers=h)
    print(f"  borrar el día 02/06: {r.status_code} · {r.text[:150]}")
    assert r.status_code == 422, "SE ABRIÓ EL CANDADO: un día de una quincena corregida "\
        "se dejó borrar"

    # (d) Y EL DÍA QUE ACABA DE ENTRAR queda igual de trabado: desde este segundo hace
    # parte del comprobante v2 y vale $90.000 de los $270.000.
    r = client.put(
        f"{RECEPCIONES}/{dia_12['id']}", json={"cantidad_litros": "777"}, headers=h
    )
    print(f"  cambiar los litros del 12/06 (el que entró) a 777 L: {r.status_code}")
    assert r.status_code == 422, "el día que entró con la corrección quedó suelto"

    # Y la quincena no se movió un peso por ninguno de los cuatro intentos.
    final = client.get(f"{API}/{leche['id']}", headers=h).json()
    _mostrar("tras los 4 ataques", final)
    assert D(final["valor_total"]) == D(270000)
    assert D(final["saldo"]) == D(90000)
    assert _cuadra(final)


# ---------------------------------------------------------------------------
# (2) El comprobante del TRANSPORTADOR, que es de otra persona
# ---------------------------------------------------------------------------
def test_corregir_la_leche_no_deja_tocar_los_dias_del_comprobante_del_transportador(
    client, base_datos
):
    """El daño colateral que nadie vería: el papel del flete.

    La liquidación del transportador se queda en BORRADOR a propósito, o sea que ELLA no
    traba nada. El único candado sobre esos días es el de la leche. Si corregir la
    quincena de leche lo apagara, cambiarle los litros al 02/06 pasaría — y al pasar,
    el recuadre en cascada le movería el total al comprobante del transportador, que es
    plata de otra persona y no sale en la pantalla de quien corrigió.

    Se mide con la cifra: el flete del 02/06 son 100 L × $100 = $10.000, y tiene que
    seguir valiendo $10.000 después de todo.
    """
    h = auth_headers(client, "admin.a")
    prov, transp, dia_02, leche, flete = _quincena_que_los_anticipos_cubrieron_exacto(
        client, h
    )
    dia_12 = _anotar(client, h, prov, transp, "2026-06-12", "50")

    print("\n===== (2) EL COMPROBANTE DEL TRANSPORTADOR =====")
    assert flete is not None, "no se generó la liquidación del flete"
    print(
        f"  flete · estado {flete['estado']} · total {flete['valor_total']} "
        f"· transporte {flete['valor_transporte']}"
    )
    assert flete["estado"] == "borrador", "este ataque exige que el flete NO trabe nada"
    total_flete_antes = D(flete["valor_total"])
    assert total_flete_antes == D(10000), total_flete_antes

    assert (
        client.post(
            f"{API}/{leche['id']}/corregir",
            json={"motivo": "el día 12 se anotó tarde", "recepciones_a_incluir": [dia_12["id"]]},
            headers=h,
        ).status_code
        == 200
    )

    r = client.put(
        f"{RECEPCIONES}/{dia_02['id']}", json={"cantidad_litros": "500"}, headers=h
    )
    print(f"  con el flete en borrador, cambiar 100 L → 500 L: {r.status_code}")
    print(f"  {r.text[:220]}")
    assert r.status_code == 422, "SE ABRIÓ EL CANDADO: el día se dejó editar y con él se "\
        "movió el comprobante del transportador"

    flete_despues = client.get(f"{API}/{flete['id']}", headers=h).json()
    print(f"  flete después · total {flete_despues['valor_total']}")
    assert D(flete_despues["valor_total"]) == total_flete_antes, (
        "el comprobante del transportador se movió por una corrección de la leche"
    )


# ---------------------------------------------------------------------------
# (3) Las puertas de la propia liquidación: recalcular, precio de un día, recuadrar
# ---------------------------------------------------------------------------
def test_recalcular_y_corregir_el_precio_de_un_dia_rebotan_en_una_corregida(
    client, base_datos
):
    """Los dos endpoints que exigen borrador, contra una quincena corregida.

    Los dos piden permiso 'editar' —que también tiene el rol Compras— mientras que
    corregir pide 'administrar'. Si alguno se dejara entrar sobre una corregida, Compras
    tendría por la puerta de atrás lo que se le negó por la del frente: mover el precio
    de un comprobante que el productor ya tiene en la mano.
    """
    h = auth_headers(client, "admin.a")
    prov, transp, _, leche, _ = _quincena_que_los_anticipos_cubrieron_exacto(client, h)
    dia_12 = _anotar(client, h, prov, transp, "2026-06-12", "50")
    corregida = client.post(
        f"{API}/{leche['id']}/corregir",
        json={"motivo": "entró el día 12", "recepciones_a_incluir": [dia_12["id"]]},
        headers=h,
    ).json()

    print("\n===== (3) RECALCULAR Y PRECIO DE UN DÍA =====")
    _mostrar("corregida", corregida)

    r = client.post(f"{API}/{leche['id']}/recalcular", headers=h)
    print(f"  POST /recalcular: {r.status_code} · {r.text[:160]}")
    assert r.status_code == 422, "recalcular una corregida barrería los anticipos "\
        "pendientes contra un comprobante ya entregado"

    detalle = next(d for d in corregida["detalles"] if d["fecha"] == "2026-06-02")
    r = client.put(
        f"{API}/{leche['id']}/detalles/{detalle['id']}",
        json={"precio_litro": "9999"},
        headers=h,
    )
    print(f"  PUT /detalles/{{id}} a $9.999: {r.status_code} · {r.text[:160]}")
    assert r.status_code == 422, "el precio por litro de una corregida se dejó cambiar "\
        "por la puerta de 'editar'"

    final = client.get(f"{API}/{leche['id']}", headers=h).json()
    _mostrar("después", final)
    assert D(final["valor_total"]) == D(270000), "algo le movió el total"
    assert final["version"] == 2, "la versión se movió sin pasar por la corrección"
    assert _cuadra(final)


def test_recuadrar_una_corregida_rebota_y_no_la_devuelve_a_borrador(
    client, db_session, base_datos
):
    """`recuadrar` es el camino automático: lo dispara el guardado de una recepción.

    Hoy no llega —el candado de Recepción diaria rebota antes—, pero es el que corre sin
    que nadie lo oprima, así que si un día se afloja el de arriba este queda de última
    defensa. Lo que NO puede pasar es que devuelva la quincena a 'borrador': ahí quedaría
    un comprobante entregado, con versión 2 y $90.000 pendientes, abierto para que
    cualquier recálculo se le trague un anticipo nuevo.
    """
    h = auth_headers(client, "admin.a")
    prov, transp, _, leche, _ = _quincena_que_los_anticipos_cubrieron_exacto(client, h)
    dia_12 = _anotar(client, h, prov, transp, "2026-06-12", "50")
    assert (
        client.post(
            f"{API}/{leche['id']}/corregir",
            json={"motivo": "entró el día 12", "recepciones_a_incluir": [dia_12["id"]]},
            headers=h,
        ).status_code
        == 200
    )

    print("\n===== (3b) RECUADRAR POR DENTRO =====")
    empresa = base_datos["empresa_a"]
    servicio = LiquidacionService(db_session, RequestContext(empresa_id=empresa.id))
    with pytest.raises(BusinessError) as error:
        servicio.recuadrar(_uuid.UUID(leche["id"]))
    print(f"  recuadrar → {error.value}")

    final = client.get(f"{API}/{leche['id']}", headers=h).json()
    _mostrar("después", final)
    assert final["estado"] == "parcial", "el recuadre la devolvió a borrador"
    assert D(final["valor_total"]) == D(270000)
    assert _cuadra(final)


# ---------------------------------------------------------------------------
# (4) Anular
# ---------------------------------------------------------------------------
def test_anular_una_corregida_rebota_aunque_no_tenga_un_solo_pago(client, base_datos):
    """Anular suelta los días Y LOS ANTICIPOS para volver a liquidar el período.

    Sobre esta quincena eso vale $180.000: el anticipo que la saldó quedaría suelto y la
    próxima corrida de "Generar" volvería a cobrar los mismos días. Y el guardia de
    `anular` que mira 'pagada' NO alcanza aquí, porque corregir la dejó en 'parcial' y
    `tiene_pagos` es falso con `pagado = $0`: lo único que la salva es que de 'parcial'
    no hay transición a 'anulada'.
    """
    h = auth_headers(client, "admin.a")
    prov, transp, _, leche, _ = _quincena_que_los_anticipos_cubrieron_exacto(client, h)
    dia_12 = _anotar(client, h, prov, transp, "2026-06-12", "50")
    assert (
        client.post(
            f"{API}/{leche['id']}/corregir",
            json={"motivo": "entró el día 12", "recepciones_a_incluir": [dia_12["id"]]},
            headers=h,
        ).status_code
        == 200
    )

    print("\n===== (4) ANULAR UNA CORREGIDA =====")
    r = client.post(f"{API}/{leche['id']}/anular", headers=h)
    print(f"  POST /anular: {r.status_code} · {r.text[:200]}")
    assert r.status_code == 422, "SE ANULÓ una quincena corregida: el anticipo de "\
        "$180.000 quedó suelto y sus días se vuelven a cobrar"

    final = client.get(f"{API}/{leche['id']}", headers=h).json()
    _mostrar("después", final)
    assert final["estado"] == "parcial"
    assert D(final["anticipos"]) == D(180000), "el anticipo se soltó"
    assert _cuadra(final)


# ---------------------------------------------------------------------------
# (5) Las observaciones, que se imprimen en el papel
# ---------------------------------------------------------------------------
def test_las_observaciones_de_una_pagada_y_de_una_corregida_no_se_reescriben(
    client, base_datos
):
    """El guardia nuevo de `validar_actualizar`.

    Las observaciones SE IMPRIMEN en el comprobante. Reescribirlas sobre un papel ya
    entregado hace que la reimpresión diga algo distinto sin que ninguna cifra
    descuadre — el descuadre más difícil de encontrar, porque no es una cifra.
    """
    h = auth_headers(client, "admin.a")
    prov, transp, _, leche, _ = _quincena_que_los_anticipos_cubrieron_exacto(client, h)

    print("\n===== (5) OBSERVACIONES =====")
    r = client.put(
        f"{API}/{leche['id']}", json={"observaciones": "otra cosa"}, headers=h
    )
    print(f"  PUT sobre la PAGADA: {r.status_code} · {r.text[:170]}")
    assert r.status_code == 422, "se reescribieron las observaciones de una pagada"

    dia_12 = _anotar(client, h, prov, transp, "2026-06-12", "50")
    assert (
        client.post(
            f"{API}/{leche['id']}/corregir",
            json={"motivo": "entró el día 12", "recepciones_a_incluir": [dia_12["id"]]},
            headers=h,
        ).status_code
        == 200
    )

    r = client.put(
        f"{API}/{leche['id']}", json={"observaciones": "y otra más"}, headers=h
    )
    print(f"  PUT sobre la CORREGIDA (parcial): {r.status_code} · {r.text[:170]}")
    assert r.status_code == 422, "se reescribieron las observaciones de una corregida"


# ---------------------------------------------------------------------------
# (6) El borrado: no hay ruta, pero el guardia tiene que estar
# ---------------------------------------------------------------------------
def test_no_hay_ruta_de_borrado_de_liquidaciones(client, base_datos):
    """La primera defensa: DELETE /liquidaciones/{id} no existe."""
    h = auth_headers(client, "admin.a")
    _, _, _, leche, _ = _quincena_que_los_anticipos_cubrieron_exacto(client, h)

    print("\n===== (6a) LA RUTA DE BORRADO =====")
    r = client.delete(f"{API}/{leche['id']}", headers=h)
    print(f"  DELETE {API}/{{id}}: {r.status_code}")
    assert r.status_code == 405, "apareció un DELETE de liquidaciones"


def test_validar_eliminar_rebota_la_pagada_con_pagado_en_cero(client, db_session, base_datos):
    """El guardia que sí quedó puesto: la 'pagada' con `pagado = $0` no se borra.

    Borrarla suelta sus días y su anticipo de $180.000, y la próxima corrida de "Generar"
    vuelve a cobrar los mismos $180.000 de leche que ya se saldaron. `tiene_pagos` no la
    ataja (es `pagado > 0`), así que lo que la ataja es el renglón del estado.
    """
    h = auth_headers(client, "admin.a")
    _, _, _, leche, _ = _quincena_que_los_anticipos_cubrieron_exacto(client, h)

    print("\n===== (6b) validar_eliminar SOBRE LA PAGADA CON PAGADO = $0 =====")
    empresa = base_datos["empresa_a"]
    servicio = LiquidacionService(db_session, RequestContext(empresa_id=empresa.id))
    obj = servicio.repo.get_or_fail(_uuid.UUID(leche["id"]))
    print(f"  estado {obj.estado} · pagado {obj.pagado} · tiene_pagos {obj.tiene_pagos}")
    with pytest.raises(BusinessError) as error:
        servicio.validar_eliminar(obj)
    print(f"  validar_eliminar → {error.value}")


def test_validar_eliminar_deberia_rebotar_tambien_la_corregida(
    client, db_session, base_datos
):
    """El mismo guardia, sobre la quincena ya corregida.

    Es exactamente el hueco que el comentario de `validar_eliminar` dice haber tapado
    —"hay una familia de quincenas 'pagada' con `pagado = $0`… borrar una suelta sus días
    y sus anticipos"— solo que la corrección le abrió una salida nueva: esa misma
    quincena ya no dice 'pagada', dice 'parcial'. La plata en juego es la misma:
    $180.000 de anticipo sueltos y $270.000 de leche listos para cobrarse otra vez.
    """
    h = auth_headers(client, "admin.a")
    prov, transp, _, leche, _ = _quincena_que_los_anticipos_cubrieron_exacto(client, h)
    dia_12 = _anotar(client, h, prov, transp, "2026-06-12", "50")
    corregida = client.post(
        f"{API}/{leche['id']}/corregir",
        json={"motivo": "entró el día 12", "recepciones_a_incluir": [dia_12["id"]]},
        headers=h,
    ).json()

    print("\n===== (6c) validar_eliminar SOBRE LA CORREGIDA =====")
    _mostrar("corregida", corregida)
    empresa = base_datos["empresa_a"]
    servicio = LiquidacionService(db_session, RequestContext(empresa_id=empresa.id))
    obj = servicio.repo.get_or_fail(_uuid.UUID(leche["id"]))
    print(
        f"  estado {obj.estado} · pagado {obj.pagado} · version {obj.version} "
        f"· tiene_pagos {obj.tiene_pagos}"
    )
    with pytest.raises(BusinessError) as error:
        servicio.validar_eliminar(obj)
    print(f"  validar_eliminar → {error.value}")


# ---------------------------------------------------------------------------
# (7) La deuda que ya viajó cierra la ventana de la corrección
# ---------------------------------------------------------------------------
def test_cuando_la_deuda_ya_viajo_la_correccion_rebota_y_nombra_la_otra_quincena(
    client, base_datos
):
    """El escenario B del dueño, y después la puerta cerrándose sola.

    Quincena del 01 al 15: 250 L × $2.000 = $500.000, pagada con $500.000. El precio
    estaba mal —era $1.600— y la corrección baja el total a $400.000: saldo −$100.000, el
    productor le quedó debiendo $100.000. La quincena SIGUIENTE se los cobra sola.

    Desde ese instante la primera quedó congelada por los dos lados: sus $100.000 están
    restados en un papel que el productor puede tener en la mano, así que una segunda
    corrección descuadraría LOS DOS comprobantes de un solo golpe. El mensaje tiene que
    nombrar la otra quincena, porque lo que el dueño necesita saber es qué anular primero.
    """
    h = auth_headers(client, "admin.a")
    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta B", "municipio": "Granada"}, headers=h
    ).json()
    transp = client.post(
        "/api/v1/transportadores",
        json={
            "nombre": "Stella B",
            "valor_transporte": "100",
            "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "100"}],
        },
        headers=h,
    ).json()
    prov = client.post(
        "/api/v1/proveedores",
        json={"nombre": "Henri", "vereda": "El Roble", "precio_litro": "2000"},
        headers=h,
    ).json()
    dia_02 = _anotar(client, h, prov, transp, "2026-06-02", "250")

    leche, _ = _generar(client, h, prov)
    assert D(leche["valor_total"]) == D(500000), leche["valor_total"]
    assert client.post(f"{API}/{leche['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{leche['id']}/pagar", headers=h).json()

    print("\n===== (7) LA DEUDA VIAJA Y CIERRA LA VENTANA =====")
    _mostrar("pagada", pagada)
    assert D(pagada["pagado"]) == D(500000)

    detalle = next(d for d in pagada["detalles"] if d["fecha"] == "2026-06-02")
    baja = client.post(
        f"{API}/{leche['id']}/corregir",
        json={
            "motivo": "el precio del día 02 era $1.600 y se liquidó a $2.000",
            "precios": [{"detalle_id": detalle["id"], "precio_litro": "1600"}],
        },
        headers=h,
    )
    assert baja.status_code == 200, baja.text
    corregida = baja.json()
    _mostrar("corregida (BAJA)", corregida)
    assert D(corregida["valor_total"]) == D(400000)
    assert D(corregida["pagado"]) == D(500000), "la corrección tocó un pago"
    assert D(corregida["saldo"]) == D(-100000)
    assert D(corregida["le_queda_debiendo"]) == D(100000)
    assert corregida["estado"] == "pagada"
    assert _cuadra(corregida)

    # La quincena siguiente le cobra sola los $100.000: 100 L × $2.000 = $200.000 de
    # leche menos $100.000 de deuda arrastrada = $100.000 de neto.
    _anotar(client, h, prov, transp, "2026-06-20", "100")
    siguiente, _ = _generar(client, h, prov, inicio="2026-06-16", fin="2026-06-30")
    _mostrar("siguiente quincena", siguiente)
    assert D(siguiente["valor_total"]) == D(200000)
    assert D(siguiente["saldo_anterior"]) == D(100000), "la deuda no viajó"
    assert D(siguiente["neto_a_pagar"]) == D(100000)
    assert _cuadra(siguiente)

    # Y ahora la primera está cerrada: una segunda corrección rebota nombrando la otra.
    dia_10 = _anotar(client, h, prov, transp, "2026-06-10", "10")
    r = client.post(
        f"{API}/{leche['id']}/corregir",
        json={"motivo": "faltaba el día 10", "recepciones_a_incluir": [dia_10["id"]]},
        headers=h,
    )
    print(f"  segunda corrección: {r.status_code}")
    print(f"  {r.text[:400]}")
    assert r.status_code == 422, "SE CORRIGIÓ una quincena cuya deuda ya se cobró: los "\
        "$100.000 restados en el otro comprobante dejaron de existir"
    assert "ya se le cobró" in r.text
    assert "16/06/2026" in r.text or "16/06" in r.text, (
        "el mensaje no nombra la quincena que se cobró la deuda"
    )

    # La previsualización rebota igual: no puede mostrar un camino que el botón niega.
    prev = client.post(
        f"{API}/{leche['id']}/corregir/previsualizar",
        json={"motivo": "faltaba el día 10"},
        headers=h,
    )
    print(f"  previsualizar: {prev.status_code}")
    assert prev.status_code == 422, "la previsualización ofrece corregir lo que el botón "\
        "rebota"

    # Ni un peso se movió en ninguno de los dos comprobantes.
    uno = client.get(f"{API}/{leche['id']}", headers=h).json()
    dos = client.get(f"{API}/{siguiente['id']}", headers=h).json()
    _mostrar("la primera, después", uno)
    _mostrar("la segunda, después", dos)
    assert D(uno["valor_total"]) == D(400000)
    assert D(uno["le_queda_debiendo"]) == D(100000)
    assert D(dos["saldo_anterior"]) == D(100000)
    assert uno["version"] == 2, "la versión subió sin que la corrección pasara"

    # Y sus días quedaron trabados por el otro lado también.
    r = client.put(
        f"{RECEPCIONES}/{dia_02['id']}", json={"cantidad_litros": "1"}, headers=h
    )
    print(f"  editar el día 02/06 de la primera: {r.status_code} · {r.text[:170]}")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# (8) El comprobante del FLETE no entra por esta puerta
# ---------------------------------------------------------------------------
def test_una_liquidacion_de_flete_no_se_puede_corregir(client, base_datos):
    """En el comprobante del transportador el renglón es (día, ruta), no (día).

    El recálculo hace `detalles.clear()` y esos renglones son la memoria de qué viaje ya
    se cobró: rearmarlos por esta puerta haría que el mismo viaje se pague dos veces. Por
    eso las de flete rebotan, y el mensaje dice la salida de verdad —generar un segundo
    comprobante del período—.
    """
    h = auth_headers(client, "admin.a")
    prov, transp = _montar_con_flete(client, h, nombre="Marta")
    _anotar(client, h, prov, transp, "2026-06-02", "100")
    _, flete = _generar(client, h, prov)
    assert flete is not None

    assert client.post(f"{API}/{flete['id']}/aprobar", headers=h).status_code == 200
    pagado = client.post(f"{API}/{flete['id']}/pagar", headers=h)
    assert pagado.status_code == 200, pagado.text

    print("\n===== (8) CORREGIR UN COMPROBANTE DE FLETE =====")
    _mostrar("flete pagado", pagado.json())
    assert pagado.json()["estado"] == "pagada"

    _anotar(client, h, prov, transp, "2026-06-11", "40")
    r = client.post(
        f"{API}/{flete['id']}/corregir",
        json={"motivo": "faltó el viaje del día 11"},
        headers=h,
    )
    print(f"  POST /corregir sobre el flete: {r.status_code} · {r.text[:250]}")
    assert r.status_code == 422, "SE CORRIGIÓ un comprobante de flete: el mismo viaje "\
        "puede quedar cobrado dos veces"
    assert "quincena de leche" in r.text

    prev = client.post(
        f"{API}/{flete['id']}/corregir/previsualizar",
        json={"motivo": "faltó el viaje del día 11"},
        headers=h,
    )
    print(f"  previsualizar sobre el flete: {prev.status_code}")
    assert prev.status_code == 422

    final = client.get(f"{API}/{flete['id']}", headers=h).json()
    _mostrar("flete después", final)
    assert D(final["valor_total"]) == D(pagado.json()["valor_total"])
    assert final["version"] == 1


# ---------------------------------------------------------------------------
# (9) EL CAMINO DE VUELTA: borrar el pago que la corrección hizo posible
# ---------------------------------------------------------------------------
# Este es el ataque que sí encontró algo, y hay que leerlo entero porque son tres
# guardias que se caen en fila.
#
# Antes de este trabajo, la quincena de la familia peligrosa —'pagada' con `pagado = $0`
# porque el anticipo la cubrió exacto— NO TENÍA NINGÚN PAGO QUE BORRAR: su saldo era
# $0,00 y el botón Pagar no registraba un peso. La corrección le abre un saldo de
# $90.000, ese saldo se paga (correcto, es el escenario A del dueño), y a partir de ahí
# EXISTE un pago. Borrarlo —que es una operación legítima: "un pago mal registrado no se
# puede quedar sin poder corregir"— es lo que destapa la fila:
#
#   1. `eliminar_pago` deduce el estado con `_estado_pago`, que devuelve APROBADA cuando
#      `pagado <= 0`. O sea que el comprobante v2 RETROCEDE a 'aprobada'. Es exactamente
#      lo que `_estado_tras_corregir` dice que no puede pasar nunca ("aquí el estado no
#      va nunca para atrás"), solo que esa regla vive en la corrección y no aquí.
#   2. En 'aprobada' y con `pagado = $0`, `AnticipoService._exigir_no_pagado` DEJA PASAR:
#      pregunta `estado == 'pagada'` y `tiene_pagos`, y no pregunta por la versión —que
#      es justo el renglón que `_ya_salio_plata` sí agregó del lado de Recepción diaria—.
#   3. Y `recuadrar` sobre una 'aprobada' la DEVUELVE A BORRADOR y la recalcula, que es
#      literalmente lo que `corregir_pagada` se negó a hacer.
#
# La plata, medida: el anticipo de $180.000 que saldó la quincena pasa a $10.000 y el
# comprobante v2 —que el productor ya tiene en la mano, y del que además tiene la v1—
# amanece en 'borrador' diciendo que se le deben $260.000 en vez de $90.000. $170.000 de
# diferencia sobre un papel entregado.


def _corregida_a_la_que_le_borran_el_pago(client, h):
    """La familia peligrosa: corregida, pagada la diferencia, y el pago borrado.

    Devuelve (proveedor, transportador, día del 02, liquidación tal como quedó).
    """
    prov, transp, dia_02, leche, _ = _quincena_que_los_anticipos_cubrieron_exacto(client, h)
    dia_12 = _anotar(client, h, prov, transp, "2026-06-12", "50")
    corregida = client.post(
        f"{API}/{leche['id']}/corregir",
        json={"motivo": "se anotó tarde el día 12", "recepciones_a_incluir": [dia_12["id"]]},
        headers=h,
    ).json()
    _mostrar("corregida (v2)", corregida)
    assert corregida["estado"] == "parcial" and D(corregida["saldo"]) == D(90000)

    pagada = client.post(f"{API}/{leche['id']}/pagar", headers=h).json()
    _mostrar("pagados los $90.000", pagada)
    assert pagada["estado"] == "pagada" and D(pagada["pagado"]) == D(90000)

    pago_id = pagada["pagos"][0]["id"]
    borrado = client.delete(f"{API}/{leche['id']}/pagos/{pago_id}", headers=h)
    assert borrado.status_code == 200, borrado.text
    tras = client.get(f"{API}/{leche['id']}", headers=h).json()
    _mostrar("borrado el pago", tras)
    return prov, transp, dia_02, tras


def test_borrar_el_ultimo_pago_de_una_corregida_no_deberia_devolverla_a_aprobada(
    client, base_datos
):
    """El estado de un comprobante corregido no puede ir para atrás.

    La quincena está en versión 2 y debe $90.000: 'parcial' es lo que es. 'aprobada'
    significa "cifras en firme por las que no ha salido plata", y de esta SÍ salió
    —$180.000 de anticipo, entregados en la mano—.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== (9a) EL ESTADO SE FUE PARA ATRÁS =====")
    _, _, _, tras = _corregida_a_la_que_le_borran_el_pago(client, h)
    assert tras["version"] == 2
    assert D(tras["saldo"]) == D(90000)
    assert _cuadra(tras)
    assert tras["estado"] != "aprobada", (
        "un comprobante en versión 2 volvió a 'aprobada': desde ahí el anticipo y "
        "Anular quedan abiertos otra vez"
    )


def test_el_anticipo_de_una_corregida_no_se_deberia_poder_mover(client, base_datos):
    """$180.000 de anticipo contra un comprobante entregado dos veces.

    Ese anticipo es LA PLATA QUE SALIÓ: se le entregó al productor en la mano y es lo
    que dejó el neto de la quincena en $0,00. Moverlo a $10.000 reescribe la única
    cifra que la corrección juró no tocar —"anticipos y saldo_anterior se quedan como
    están, porque ya quedaron aplicados cuando se generó la quincena"—.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== (9b) EL ANTICIPO DE $180.000 =====")
    _, _, _, tras = _corregida_a_la_que_le_borran_el_pago(client, h)

    ant = client.get(f"{ANTICIPOS}?page=1&size=50", headers=h).json()["items"][0]
    print(
        f"  el anticipo · valor {ant['valor']} · bloqueado={ant['bloqueado']} "
        f"· liquidacion_estado={ant['liquidacion_estado']}"
    )

    r = client.put(f"{ANTICIPOS}/{ant['id']}", json={"valor": "10000"}, headers=h)
    print(f"  bajar el anticipo de $180.000 a $10.000: {r.status_code}")
    despues = client.get(f"{API}/{tras['id']}", headers=h).json()
    _mostrar("el comprobante v2", despues)
    assert r.status_code == 422, (
        f"SE MOVIÓ EL ANTICIPO de un comprobante en versión 2: quedó en "
        f"'{despues['estado']}' con anticipos {despues['anticipos']} y neto "
        f"{despues['neto_a_pagar']} (era $90.000)"
    )


def test_una_corregida_no_se_deberia_poder_anular_ni_despues_de_borrarle_el_pago(
    client, base_datos
):
    """Anular un comprobante del que ya salieron DOS hojas.

    El productor tiene la v1 ($180.000, saldada con el anticipo) y la v2 ($270.000, con
    su motivo escrito). Anular las dos de un plumazo suelta el anticipo y los días para
    que la próxima corrida los vuelva a liquidar — y ahí el papel viejo y el sistema ya
    no se pueden cuadrar contra nada.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== (9c) ANULAR UNA CORREGIDA =====")
    _, _, dia_02, tras = _corregida_a_la_que_le_borran_el_pago(client, h)

    r = client.post(f"{API}/{tras['id']}/anular", headers=h)
    print(f"  POST /anular: {r.status_code} · {r.text[:170]}")
    if r.status_code == 200:
        edit = client.put(
            f"{RECEPCIONES}/{dia_02['id']}", json={"cantidad_litros": "999"}, headers=h
        )
        print(f"  y editar los litros del 02/06 después de anular: {edit.status_code}")
    assert r.status_code == 422, (
        "SE ANULÓ un comprobante en versión 2: el anticipo de $180.000 quedó suelto y "
        "sus días volvieron a estar editables"
    )
