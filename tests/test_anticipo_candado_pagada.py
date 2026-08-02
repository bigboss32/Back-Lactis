"""El anticipo tampoco se congela hasta que se PAGUE.

Es exactamente el mismo problema que se acababa de resolver con las recepciones,
y por eso la regla tiene que ser la misma. Antes, `AnticipoService` rebotaba con
"No se puede modificar un anticipo ya aplicado" en cuanto el anticipo tenía
`liquidacion_id`: o sea, desde el instante en que se GENERABA la quincena. Con la
plata todavía en la caja y el comprobante sin entregar, el dueño ya no podía
corregirle un cero de más a un adelanto que él mismo acababa de anotar.

Lo que se fija aquí es la regla nueva y —sobre todo— que aflojar el candado no
abra un descuadre silencioso, que es lo que el dueño detectaría cuadrando el
comprobante a mano:

  (a) BORRADOR  → se corrige y se borra el anticipo, y la liquidación queda
                  RECALCULADA: los anticipos aplicados y el neto a pagar bajan o
                  suben exacto por la diferencia.
  (b) APROBADA  → se puede, pero la liquidación VUELVE A BORRADOR y se recalcula,
                  y el retroceso queda en la bitácora con su motivo. Aprobar es
                  un visto bueno sobre unas cifras: si las cifras cambian, el
                  visto bueno ya no vale.
  (c) CON UN PAGO → rebota, aunque sea un abono de mil pesos sobre un millón.
                  Esa plata ya salió contra ese neto. Y la pagada del todo,
                  también.
  (d) Multiempresa: la Quesera B no toca un anticipo de la Quesera A ni con el
                  id en la mano.

Y aparte: la NÓMINA sigue trabada como antes. Un pago de empleado no tiene
estados ni pagos parciales —existe = ya se le pagó al empleado con el anticipo ya
descontado—, así que ahí no hay ningún "todavía sin pagar" que aflojar.

El cuadre que se verifica en cada caso es el que el dueño hace a mano:

    neto a pagar = valor total - anticipos       y      neto = pagado + saldo
"""
import uuid
from decimal import Decimal

from sqlalchemy import select

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
ANTICIPOS = "/api/v1/anticipos"

INICIO = "2026-06-01"
FIN = "2026-06-15"


def D(v):
    return Decimal(str(v))


# ---------------------------------------------------------------------------
# Montaje: un proveedor con su quincena y un anticipo ya descontado
# ---------------------------------------------------------------------------
def _montar(client, h, *, litros="500", precio="1800", anticipo="200000", nombre="Libardo"):
    """Un proveedor con un día anotado, un anticipo, y la quincena ya generada.

    Devuelve (proveedor, anticipo, liquidación). Al generar, el anticipo queda
    descontado: es justo el estado en el que el candado viejo se cerraba.
    """
    proveedor = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    ).json()
    assert client.post(
        "/api/v1/recepciones",
        json={"fecha": INICIO, "proveedor_id": proveedor["id"], "cantidad_litros": litros},
        headers=h,
    ).status_code == 201

    ant = client.post(
        ANTICIPOS,
        json={
            "tipo": "proveedor",
            "proveedor_id": proveedor["id"],
            "fecha": INICIO,
            "valor": anticipo,
        },
        headers=h,
    )
    assert ant.status_code == 201, ant.text

    liquidaciones = client.post(
        f"{API}/generar",
        json={"periodo_inicio": INICIO, "periodo_fin": FIN, "tipo": "proveedor"},
        headers=h,
    ).json()
    liq = next(x for x in liquidaciones if x["proveedor_id"] == proveedor["id"])
    assert D(liq["anticipos"]) == D(anticipo), "el anticipo no quedó descontado al generar"
    return proveedor, ant.json(), liq


def _cuadra(liq) -> bool:
    """Los dos cuadres que el dueño verifica a mano en el comprobante."""
    suma_dias = sum((D(d["valor"]) for d in liq["detalles"]), D(0))
    neto = D(liq["valor_total"]) - D(liq["anticipos"])
    return (
        suma_dias == D(liq["valor_total"])
        and D(liq["neto_a_pagar"]) == neto
        and D(liq["neto_a_pagar"]) == D(liq["pagado"]) + D(liq["saldo"])
    )


def _mostrar(titulo, liq):
    print(
        f"  {titulo:<9}· estado {liq['estado']:<9}· total {liq['valor_total']} "
        f"· anticipos {liq['anticipos']} · neto {liq['neto_a_pagar']} "
        f"· pagado {liq['pagado']} · saldo {liq['saldo']}"
    )


# ---------------------------------------------------------------------------
# (a) BORRADOR: se corrige y se borra, y la liquidación se recalcula sola
# ---------------------------------------------------------------------------
def test_corregir_un_anticipo_en_borrador_recalcula_la_liquidacion(client, base_datos):
    """El caso de todos los días: se generó la quincena y después el dueño ve que
    el adelanto no eran $200.000 sino $120.000. Un borrador todavía no es plata
    entregada, así que se corrige y la liquidación se vuelve a cuadrar sola.

    Lo que NO puede pasar es que la liquidación se quede con el anticipo viejo:
    ahí el comprobante le descontaría al productor $80.000 que nunca recibió.
    """
    h = auth_headers(client, "admin.a")
    _, anticipo, liq = _montar(client, h)

    print("\n===== (a) CORREGIR UN ANTICIPO EN BORRADOR =====")
    _mostrar("antes", liq)
    assert liq["estado"] == "borrador"
    assert D(liq["valor_total"]) == D(500 * 1800)
    assert D(liq["neto_a_pagar"]) == D(900000) - D(200000)

    r = client.put(f"{ANTICIPOS}/{anticipo['id']}", json={"valor": "120000"}, headers=h)
    print(f"  corregir el anticipo de $200.000 a $120.000: {r.status_code}")
    assert r.status_code == 200, r.text
    print(f"  el anticipo responde · liquidacion_estado={r.json()['liquidacion_estado']} "
          f"· bloqueado={r.json()['bloqueado']} · aplicado={r.json()['aplicado']}")
    assert D(r.json()["valor"]) == D(120000)

    actualizada = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("después", actualizada)
    assert actualizada["estado"] == "borrador", "un borrador corregido sigue siendo borrador"
    assert D(actualizada["valor_total"]) == D(900000), "el total de la leche no se tenía que mover"
    assert D(actualizada["anticipos"]) == D(120000)
    assert D(actualizada["neto_a_pagar"]) == D(900000) - D(120000)
    assert D(actualizada["saldo"]) == D(780000)
    assert _cuadra(actualizada), "las partes dejaron de sumar la cifra grande"


def test_borrar_un_anticipo_en_borrador_le_devuelve_la_plata_al_neto(client, base_datos):
    """Borrar un anticipo que ya está descontado tiene el mismo problema que
    corregirlo: si la liquidación se quedara con la cifra, le estaría reteniendo
    al productor un adelanto que —según el sistema— nunca existió.

    Al borrarlo, el neto a pagar tiene que SUBIR exacto en ese valor.
    """
    h = auth_headers(client, "admin.a")
    _, anticipo, liq = _montar(client, h)

    print("\n===== (a2) BORRAR UN ANTICIPO EN BORRADOR =====")
    _mostrar("antes", liq)

    r = client.delete(f"{ANTICIPOS}/{anticipo['id']}", headers=h)
    print(f"  borrar el anticipo de $200.000: {r.status_code}")
    assert r.status_code == 204, r.text

    actualizada = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("después", actualizada)
    assert D(actualizada["anticipos"]) == D(0), "la liquidación se quedó con el anticipo borrado"
    assert D(actualizada["neto_a_pagar"]) == D(900000), "el neto tenía que subir los $200.000"
    assert D(actualizada["saldo"]) == D(900000)
    assert _cuadra(actualizada)

    # Y el anticipo ya no aparece en la lista: se fue de verdad
    lista = client.get(f"{ANTICIPOS}?page_size=100", headers=h).json()
    print(f"  anticipos que quedan en la lista: {len(lista['items'])}")
    assert all(x["id"] != anticipo["id"] for x in lista["items"])


# ---------------------------------------------------------------------------
# (b) APROBADA: se puede, pero vuelve a borrador y queda auditado
# ---------------------------------------------------------------------------
def test_corregir_un_anticipo_de_una_aprobada_la_devuelve_a_borrador(client, db_session, base_datos):
    """Aprobar es dar el visto bueno sobre unas cifras. Si cambia el anticipo, el
    neto a pagar cambia, y el visto bueno ya no vale: la liquidación vuelve a
    borrador para que el dueño la revise y la apruebe otra vez.

    Y ese retroceso tiene que quedar en la bitácora DICIENDO QUE FUE POR UN
    ANTICIPO: si el libro dijera "cambiaron las recepciones", estaría señalando
    hacia el lado equivocado el día que alguien vaya a averiguar qué pasó.
    """
    from app.modules.auditoria.models import Auditoria

    h = auth_headers(client, "admin.a")
    _, anticipo, liq = _montar(client, h)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200

    print("\n===== (b) CORREGIR UN ANTICIPO DE UNA LIQUIDACIÓN APROBADA =====")
    aprobada = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("antes", aprobada)
    assert aprobada["estado"] == "aprobada"

    r = client.put(f"{ANTICIPOS}/{anticipo['id']}", json={"valor": "350000"}, headers=h)
    print(f"  corregir el anticipo de $200.000 a $350.000: {r.status_code}")
    assert r.status_code == 200, r.text
    # El anticipo devuelve el estado NUEVO de su liquidación: la pantalla lo usa
    # para avisar que hay que volver a aprobarla.
    print(f"  el anticipo responde liquidacion_estado = {r.json()['liquidacion_estado']}")
    assert r.json()["liquidacion_estado"] == "borrador"

    actualizada = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("después", actualizada)
    assert actualizada["estado"] == "borrador", "una aprobada con cifras nuevas no puede seguir aprobada"
    assert D(actualizada["anticipos"]) == D(350000)
    assert D(actualizada["neto_a_pagar"]) == D(900000) - D(350000)
    assert D(actualizada["saldo"]) == D(550000)
    assert _cuadra(actualizada)

    # Y el retroceso quedó escrito, con el porqué correcto
    registros = db_session.scalars(
        select(Auditoria).where(
            Auditoria.entidad == "Liquidacion",
            Auditoria.entidad_id == uuid.UUID(liq["id"]),
        )
    ).all()
    retrocesos = [
        a for a in registros
        if (a.antes or {}).get("estado") == "aprobada"
        and (a.despues or {}).get("estado") == "borrador"
    ]
    for a in retrocesos:
        print(f"  auditoría · {a.modulo}/{a.accion} · {a.antes} -> {a.despues}")
    assert len(retrocesos) == 1, "el paso de aprobada a borrador no quedó en la bitácora"
    assert "anticipo" in retrocesos[0].despues["motivo"], "el motivo apunta al lado equivocado"

    # Se puede volver a aprobar: el arreglo no puede volverse un estorbo
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200


def test_borrar_un_anticipo_de_una_aprobada_la_devuelve_a_borrador(client, base_datos):
    """El mismo camino que corregirlo, por la puerta de borrar. Aquí el descuadre
    sería al revés y a favor del productor: se le pagaría de más el valor entero
    del adelanto si la liquidación se quedara aprobada con la cifra vieja.
    """
    h = auth_headers(client, "admin.a")
    _, anticipo, liq = _montar(client, h)
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)

    print("\n===== (b2) BORRAR UN ANTICIPO DE UNA LIQUIDACIÓN APROBADA =====")
    _mostrar("antes", client.get(f"{API}/{liq['id']}", headers=h).json())

    r = client.delete(f"{ANTICIPOS}/{anticipo['id']}", headers=h)
    print(f"  borrar el anticipo: {r.status_code}")
    assert r.status_code == 204, r.text

    actualizada = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("después", actualizada)
    assert actualizada["estado"] == "borrador"
    assert D(actualizada["anticipos"]) == D(0)
    assert D(actualizada["neto_a_pagar"]) == D(900000)
    assert _cuadra(actualizada)


# ---------------------------------------------------------------------------
# (c) CON UN PAGO REGISTRADO: rebota. Esa plata ya salió.
# ---------------------------------------------------------------------------
def test_con_un_solo_abono_el_anticipo_queda_trabado(client, base_datos):
    """El candado que sí tiene que quedar, y basta UN abono para cerrarlo: el neto
    a pagar contra el que se registró ese abono dejaría de existir si el anticipo
    cambia, y el comprobante que se le entregó al productor diría otra cosa.

    Se abona $100.000 de un neto de $700.000 —queda 'parcial', debiendo la mayor
    parte— y aun así el anticipo no se toca. El guardia está en el BACKEND, no en
    la pantalla: quien conozca la dirección del endpoint entra igual.
    """
    h = auth_headers(client, "admin.a")
    _, anticipo, liq = _montar(client, h)
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    pago = client.post(
        f"{API}/{liq['id']}/pagos",
        json={"fecha": INICIO, "valor": "100000", "observaciones": "primer abono"},
        headers=h,
    )
    assert pago.status_code == 200, pago.text

    print("\n===== (c) LIQUIDACIÓN CON UN ABONO =====")
    _mostrar("antes", pago.json())
    assert pago.json()["estado"] == "parcial"

    editar = client.put(f"{ANTICIPOS}/{anticipo['id']}", json={"valor": "1"}, headers=h)
    print(f"  corregir: {editar.status_code} · {editar.json().get('error', {}).get('detail', '')}")
    assert editar.status_code == 422
    assert "ya tiene un pago registrado" in editar.json()["error"]["detail"]

    borrar = client.delete(f"{ANTICIPOS}/{anticipo['id']}", headers=h)
    print(f"  borrar:   {borrar.status_code} · {borrar.json().get('error', {}).get('detail', '')}")
    assert borrar.status_code == 422
    assert "ya tiene un pago registrado" in borrar.json()["error"]["detail"]

    # Y nada quedó a medias
    intacta = client.get(f"{API}/{liq['id']}", headers=h).json()
    sigue = client.get(f"{ANTICIPOS}/{anticipo['id']}", headers=h).json()
    _mostrar("después", intacta)
    print(f"  el anticipo sigue en {sigue['valor']} · bloqueado={sigue['bloqueado']} "
          f"· liquidacion_estado={sigue['liquidacion_estado']}")
    assert intacta["estado"] == "parcial"
    assert D(intacta["anticipos"]) == D(200000)
    assert D(sigue["valor"]) == D(200000)
    assert sigue["bloqueado"] is True, "la pantalla tiene que ver el candado puesto"
    assert sigue["liquidacion_estado"] == "parcial"
    assert _cuadra(intacta)


def test_con_la_liquidacion_pagada_el_anticipo_queda_trabado(client, base_datos):
    """Pagada del todo: ni corregir ni borrar. El mensaje es distinto al del abono
    a propósito —del abono todavía hay salida (se borra el pago, se corrige y se
    vuelve a abonar); de la pagada no hay nada que hacer por dentro—.
    """
    h = auth_headers(client, "admin.a")
    _, anticipo, liq = _montar(client, h)
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    client.post(f"{API}/{liq['id']}/pagar", headers=h)

    print("\n===== (c2) LIQUIDACIÓN PAGADA =====")
    pagada = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("estado", pagada)
    assert pagada["estado"] == "pagada"

    editar = client.put(f"{ANTICIPOS}/{anticipo['id']}", json={"valor": "1"}, headers=h)
    borrar = client.delete(f"{ANTICIPOS}/{anticipo['id']}", headers=h)
    print(f"  corregir: {editar.status_code} · {editar.json().get('error', {}).get('detail', '')}")
    print(f"  borrar:   {borrar.status_code} · {borrar.json().get('error', {}).get('detail', '')}")
    assert editar.status_code == 422
    assert "ya se pagó" in editar.json()["error"]["detail"]
    assert borrar.status_code == 422
    assert "ya se pagó" in borrar.json()["error"]["detail"]

    intacta = client.get(f"{API}/{liq['id']}", headers=h).json()
    assert D(intacta["anticipos"]) == D(200000)
    assert D(intacta["saldo"]) == D(0)
    assert _cuadra(intacta)


def test_al_borrar_el_pago_el_anticipo_se_vuelve_a_poder_corregir(client, base_datos):
    """La salida que promete el mensaje de error tiene que existir de verdad: se
    borra el abono mal registrado, la liquidación vuelve a quedar sin pagos, y
    entonces sí se corrige el anticipo. Si no, el mensaje sería una burla.
    """
    h = auth_headers(client, "admin.a")
    _, anticipo, liq = _montar(client, h)
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    pago = client.post(
        f"{API}/{liq['id']}/pagos", json={"fecha": INICIO, "valor": "100000"}, headers=h
    ).json()

    print("\n===== (c3) SE BORRA EL ABONO Y EL CANDADO SE ABRE =====")
    _mostrar("con abono", pago)
    trabado = client.put(f"{ANTICIPOS}/{anticipo['id']}", json={"valor": "120000"}, headers=h)
    print(f"  con el abono puesto, corregir: {trabado.status_code}")
    assert trabado.status_code == 422

    borrado = client.delete(
        f"{API}/{liq['id']}/pagos/{pago['pagos'][0]['id']}", headers=h
    )
    assert borrado.status_code == 200, borrado.text
    _mostrar("sin abono", borrado.json())
    assert borrado.json()["estado"] == "aprobada"

    ahora = client.put(f"{ANTICIPOS}/{anticipo['id']}", json={"valor": "120000"}, headers=h)
    print(f"  sin el abono, corregir:        {ahora.status_code}")
    assert ahora.status_code == 200, ahora.text

    final = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("final", final)
    assert final["estado"] == "borrador", "quedó aprobada con cifras nuevas"
    assert D(final["anticipos"]) == D(120000)
    assert D(final["neto_a_pagar"]) == D(780000)
    assert _cuadra(final)


# ---------------------------------------------------------------------------
# (d) Multiempresa: el anticipo de la Quesera A no se toca desde la B
# ---------------------------------------------------------------------------
def test_no_se_cruzan_las_empresas(client, base_datos):
    """Multiempresa por fila. El admin de la Quesera B no puede corregir ni borrar
    un anticipo de la Quesera A ni con el id en la mano, y —lo que abre este
    cambio— tampoco puede hacerle retroceder a borrador una liquidación aprobada
    de A por esa vía. Es plata de un competidor.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    _, anticipo_a, liq_a = _montar(client, h_a, nombre="DeLaA")
    client.post(f"{API}/{liq_a['id']}/aprobar", headers=h_a)

    print("\n===== (d) OTRA EMPRESA =====")
    editar = client.put(f"{ANTICIPOS}/{anticipo_a['id']}", json={"valor": "1"}, headers=h_b)
    borrar = client.delete(f"{ANTICIPOS}/{anticipo_a['id']}", headers=h_b)
    ver = client.get(f"{ANTICIPOS}/{anticipo_a['id']}", headers=h_b)
    print(f"  admin.b corrigiendo el anticipo de A: {editar.status_code}")
    print(f"  admin.b borrando el anticipo de A:    {borrar.status_code}")
    print(f"  admin.b mirando el anticipo de A:     {ver.status_code}")
    assert editar.status_code == 404
    assert borrar.status_code == 404
    assert ver.status_code == 404

    intacta = client.get(f"{API}/{liq_a['id']}", headers=h_a).json()
    sigue = client.get(f"{ANTICIPOS}/{anticipo_a['id']}", headers=h_a).json()
    _mostrar("A sigue", intacta)
    print(f"  el anticipo de A sigue en {sigue['valor']}")
    assert intacta["estado"] == "aprobada", "la aprobada de A no podía retroceder por culpa de B"
    assert D(intacta["anticipos"]) == D(200000)
    assert D(sigue["valor"]) == D(200000)
    assert _cuadra(intacta)


# ---------------------------------------------------------------------------
# La NÓMINA es otro camino y no se toca
# ---------------------------------------------------------------------------
def test_el_anticipo_descontado_en_nomina_sigue_trabado(client, base_datos):
    """Un anticipo también se le puede dar a un EMPLEADO, y ahí se descuenta en el
    pago de nómina, no en una liquidación. Ese camino no tiene estados ni pagos
    parciales: el pago de nómina existe = ya se le entregó la plata al empleado
    con el anticipo ya restado. No hay ningún "todavía sin pagar" que aflojar, así
    que ahí el candado se queda igual que siempre.

    Se prueba explícitamente para que aflojar el de liquidaciones no haya abierto
    de paso una puerta en nómina.
    """
    h = auth_headers(client, "admin.a")
    empleado = client.post(
        "/api/v1/empleados",
        json={"nombre": "Marina", "apellido": "Ocampo", "valor_dia": "60000"},
        headers=h,
    ).json()
    anticipo = client.post(
        ANTICIPOS,
        json={
            "tipo": "empleado",
            "empleado_id": empleado["id"],
            "fecha": INICIO,
            "valor": "150000",
        },
        headers=h,
    ).json()

    print("\n===== (e) ANTICIPO DE EMPLEADO, DESCONTADO EN NÓMINA =====")
    suelto = client.get(f"{ANTICIPOS}/{anticipo['id']}", headers=h).json()
    print(f"  antes del pago · aplicado={suelto['aplicado']} · bloqueado={suelto['bloqueado']}")
    assert suelto["bloqueado"] is False
    # Mientras esté suelto sí se corrige: el candado de nómina no puede volverse
    # un candado permanente sobre todos los anticipos de empleado.
    assert client.put(
        f"{ANTICIPOS}/{anticipo['id']}", json={"valor": "150000"}, headers=h
    ).status_code == 200

    pago = client.post(
        "/api/v1/nomina",
        json={
            "empleado_id": empleado["id"],
            "fecha": FIN,
            "dias_trabajados": "10",
            "periodo": "1 al 15 de junio",
        },
        headers=h,
    )
    assert pago.status_code == 201, pago.text
    print(f"  nómina · 10 días × $60.000 = {pago.json()['dias_trabajados']} días "
          f"· anticipos {pago.json()['anticipos']} · total {pago.json()['total']}")
    assert D(pago.json()["anticipos"]) == D(150000)
    assert D(pago.json()["total"]) == D(600000) - D(150000)

    editar = client.put(f"{ANTICIPOS}/{anticipo['id']}", json={"valor": "1"}, headers=h)
    borrar = client.delete(f"{ANTICIPOS}/{anticipo['id']}", headers=h)
    print(f"  corregir: {editar.status_code} · {editar.json().get('error', {}).get('detail', '')}")
    print(f"  borrar:   {borrar.status_code} · {borrar.json().get('error', {}).get('detail', '')}")
    assert editar.status_code == 422
    assert "nómina" in editar.json()["error"]["detail"]
    assert borrar.status_code == 422
    assert "nómina" in borrar.json()["error"]["detail"]

    despues = client.get(f"{ANTICIPOS}/{anticipo['id']}", headers=h).json()
    print(f"  después del pago · aplicado={despues['aplicado']} · bloqueado={despues['bloqueado']}")
    assert despues["bloqueado"] is True
    assert D(despues["valor"]) == D(150000)


# ---------------------------------------------------------------------------
# Corregirle la fecha lo saca de la quincena que no le corresponde
# ---------------------------------------------------------------------------
def test_mover_el_anticipo_a_otra_quincena_lo_suelta_de_la_liquidacion(client, base_datos):
    """Poder corregir el anticipo abre una puerta que antes estaba cerrada:
    cambiarle la FECHA. Si se pasa del fin del período, ese adelanto pertenece a
    otra quincena y no puede quedarse descontado en un comprobante de junio.

    Se suelta —la liquidación de junio se recuadra sin él y le devuelve el valor
    al neto— y queda disponible para la quincena que sí le toca.
    """
    h = auth_headers(client, "admin.a")
    proveedor, anticipo, liq = _montar(client, h)

    print("\n===== (f) MOVER EL ANTICIPO A OTRA QUINCENA =====")
    _mostrar("antes", liq)

    r = client.put(f"{ANTICIPOS}/{anticipo['id']}", json={"fecha": "2026-07-03"}, headers=h)
    assert r.status_code == 200, r.text
    print(f"  el anticipo del 01/06 pasa al 03/07 · aplicado={r.json()['aplicado']} "
          f"· liquidacion_estado={r.json()['liquidacion_estado']}")
    assert r.json()["aplicado"] is False, "el anticipo quedó suelto, sin liquidación"

    junio = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("junio", junio)
    assert D(junio["anticipos"]) == D(0), "junio se quedó descontando un anticipo de julio"
    assert D(junio["neto_a_pagar"]) == D(900000)
    assert _cuadra(junio)

    # Y en julio sí entra: se le anota un día y se genera la quincena siguiente
    client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-07-02", "proveedor_id": proveedor["id"], "cantidad_litros": "400"},
        headers=h,
    )
    julio = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "proveedor"},
        headers=h,
    ).json()[0]
    _mostrar("julio", julio)
    assert D(julio["anticipos"]) == D(200000), "el anticipo no lo recogió la quincena que le toca"
    assert D(julio["neto_a_pagar"]) == D(400 * 1800) - D(200000)
    assert _cuadra(julio)

    # Una fecha DENTRO del período no suelta nada: solo recuadra
    otro = client.post(
        ANTICIPOS,
        json={
            "tipo": "proveedor",
            "proveedor_id": proveedor["id"],
            "fecha": INICIO,
            "valor": "50000",
        },
        headers=h,
    ).json()
    client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    movido = client.put(f"{ANTICIPOS}/{otro['id']}", json={"fecha": "2026-06-10"}, headers=h)
    assert movido.status_code == 200, movido.text
    print(f"  mover otro anticipo del 01/06 al 10/06 (misma quincena) · "
          f"aplicado={movido.json()['aplicado']}")
    assert movido.json()["aplicado"] is True, "un cambio dentro del período no tenía que soltarlo"

    junio = client.get(f"{API}/{liq['id']}", headers=h).json()
    _mostrar("junio 2", junio)
    assert D(junio["anticipos"]) == D(50000)
    assert _cuadra(junio)
