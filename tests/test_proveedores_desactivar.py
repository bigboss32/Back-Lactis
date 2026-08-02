"""Desactivar y reactivar proveedores de leche.

Lo pidió el dueño con estas palabras: «quiero que se pueda desactivar los
proveedores; el eliminar elimina todo». Tenía razón en el síntoma. La caneca
hace un borrado lógico, pero como el filtro `deleted_at IS NULL` está en toda
consulta, el proveedor eliminado se le desaparecía de la pantalla —incluso
filtrando por Estado: Inactivo— y no había manera de devolverlo. Para él eso es
"se eliminó".

Lo que fijan estas pruebas es el trato que él pidió para el que se retira:
apartarlo SIN perderle la historia. Cada una imprime las cifras para que se
puedan cuadrar a mano contra lo que muestra la pantalla.
"""
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import auth_headers

PROV = "/api/v1/proveedores"
RECEP = "/api/v1/recepciones"
LIQ = "/api/v1/liquidaciones"


def D(v):
    return Decimal(str(v))


def crear_proveedor(client, h, nombre="Don José", precio="1800"):
    r = client.post(PROV, json={"nombre": nombre, "precio_litro": precio}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def registrar_leche(client, h, proveedor_id, litros, dias_atras):
    return client.post(
        RECEP,
        json={
            "proveedor_id": proveedor_id,
            "fecha": str(date.today() - timedelta(days=dias_atras)),
            "cantidad_litros": str(litros),
        },
        headers=h,
    )


def detalle(respuesta):
    return respuesta.json().get("error", {}).get("detail", "")


# ---------------------------------------------------------------------------
# (a) Desactivar NO le toca la historia: ni las recepciones, ni la liquidación,
#     ni la plata que se le queda debiendo.
# ---------------------------------------------------------------------------
def test_desactivar_deja_intacta_la_historia_del_proveedor(client, base_datos):
    """Lo que más le importa al dueño: apartar al que se fue no puede borrarle
    lo que entregó ni lo que se le debe.

    Se le reciben 3 días de leche, se le genera la liquidación de la quincena y
    ENTONCES se desactiva. Después de desactivarlo tienen que seguir igualitos:
    las 3 recepciones, la liquidación con su valor y su saldo, y el renglón de
    "liquidaciones por pagar" del tablero. Si desactivar borrara la deuda, la
    quesera creería que no le debe nada al que se retiró.
    """
    h = auth_headers(client, "admin.a")
    # Sin tilde a propósito, y no porque el sistema no las aguante: más abajo se
    # descarga el comprobante PDF, y el nombre del tercero va en el nombre del
    # archivo (cabecera Content-Disposition). Esa cabecera hoy manda el nombre
    # sin escapar, así que con "Don José" el nombre del archivo sale mal escrito.
    # Es un defecto aparte, del módulo de liquidaciones, y quedó reportado; aquí
    # se esquiva para que esta prueba mida lo suyo (que al inactivo se le sigue
    # pudiendo imprimir el comprobante) y no se caiga por algo de otro lado.
    prov = crear_proveedor(client, h, "Don Jose")
    for i in range(3):
        assert registrar_leche(client, h, prov["id"], 100, i).status_code == 201

    g = client.post(
        f"{LIQ}/generar",
        json={
            "periodo_inicio": str(date.today() - timedelta(days=5)),
            "periodo_fin": str(date.today()),
            "tipo": "proveedor",
        },
        headers=h,
    )
    assert g.status_code == 200, g.text
    liq_antes = g.json()[0]

    def foto():
        recs = client.get(f"{RECEP}/filtrar/avanzado?proveedor_id={prov['id']}", headers=h).json()
        liq = client.get(f"{LIQ}/{liq_antes['id']}", headers=h).json()
        tablero = client.get("/api/v1/reportes/dashboard", headers=h).json()
        return {
            "recepciones": recs["total"],
            "litros": sum(D(r["cantidad_litros"]) for r in recs["items"]),
            "liq_valor_total": D(liq["valor_total"]),
            "liq_saldo": D(liq["saldo"]),
            "por_pagar": D(tablero["liquidaciones_por_pagar"]),
        }

    antes = foto()
    r = client.post(f"{PROV}/{prov['id']}/desactivar", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "inactivo"
    despues = foto()

    print("\n===== (a) DESACTIVAR NO TOCA LA HISTORIA =====")
    print(f"  {'':22} {'antes':>14} {'después':>14}")
    for clave in antes:
        print(f"  {clave:22} {str(antes[clave]):>14} {str(despues[clave]):>14}")
    assert despues == antes, "desactivar le movió algo de la historia o de la plata"

    # Y el desglose sigue cuadrando: 300 litros a $1.800 = $540.000
    assert antes["litros"] == D("300.00")
    assert antes["liq_valor_total"] == D("300") * D("1800")
    print(f"  cuadre: 300 L x $1.800 = ${antes['liq_valor_total']:,.2f} · "
          f"saldo por pagar ${antes['liq_saldo']:,.2f}")

    # Sigue apareciendo en la pantalla de Proveedores, ahora como inactivo: si no
    # saliera ni con el filtro, sería lo mismo que la caneca de antes.
    lst = client.get(f"{PROV}/filtrar/avanzado?estado=inactivo", headers=h).json()
    print(f"  filtro Estado=Inactivo: {lst['total']} -> {[p['nombre'] for p in lst['items']]}")
    assert [p["nombre"] for p in lst["items"]] == ["Don Jose"]

    # Y su liquidación se sigue pudiendo imprimir para pagarle lo que quedó.
    pdf = client.get(f"{LIQ}/{liq_antes['id']}/pdf", headers=h)
    print(f"  comprobante PDF del inactivo: {pdf.status_code}")
    assert pdf.status_code == 200


# ---------------------------------------------------------------------------
# (b) Al inactivo no se le registra leche nueva — la guarda está en el backend.
# ---------------------------------------------------------------------------
def test_al_proveedor_inactivo_no_se_le_registra_leche_nueva(client, base_datos):
    """La guarda tiene que estar en el servidor, no en la pantalla.

    Esconder al inactivo del selector no alcanza: el id sigue siendo válido y
    quien le pegue a la API directo (o tenga la pantalla abierta desde antes de
    que lo desactivaran) le seguiría metiendo litros al que ya se retiró. Aquí
    se le pega directo a la API, que es el caso que el dueño pidió cubrir.
    """
    h = auth_headers(client, "admin.a")
    prov = crear_proveedor(client, h, "Doña Rosa")
    assert registrar_leche(client, h, prov["id"], 80, 3).status_code == 201

    client.post(f"{PROV}/{prov['id']}/desactivar", headers=h)
    r = registrar_leche(client, h, prov["id"], 80, 1)

    print("\n===== (b) LECHE NUEVA A UN INACTIVO =====")
    print(f"  POST /recepciones -> {r.status_code}")
    print(f"  mensaje: {detalle(r)}")
    assert r.status_code == 422, "se le pudo registrar leche a un proveedor inactivo"
    assert "inactivo" in detalle(r)
    assert "Reactivar" in detalle(r), "el mensaje no dice cómo devolverse"

    # El PUT tampoco es una puerta trasera: antes se podía mandar
    # {"estado": "inactivo"} (y hasta texto basura) y no lo hacía cumplir nadie.
    r2 = client.put(f"{PROV}/{prov['id']}", json={"estado": "activo"}, headers=h)
    vigente = client.get(f"{PROV}/{prov['id']}", headers=h).json()["estado"]
    print(f"  PUT con estado=activo -> {r2.status_code}; estado que quedó: {vigente}")
    assert vigente == "inactivo", "el PUT reactivó al proveedor saltándose el endpoint"

    r3 = client.put(f"{PROV}/{prov['id']}", json={"estado": "cualquier_cosa"}, headers=h)
    vigente = client.get(f"{PROV}/{prov['id']}", headers=h).json()["estado"]
    print(f"  PUT con estado basura -> {r3.status_code}; estado que quedó: {vigente}")
    assert vigente == "inactivo", "el PUT metió un estado que no existe"

    # Lo que YA tenía sí se puede seguir corrigiendo: la última quincena del que
    # se retiró todavía hay que cuadrarla y liquidársela.
    recs = client.get(f"{RECEP}/filtrar/avanzado?proveedor_id={prov['id']}", headers=h).json()
    correccion = client.put(
        f"{RECEP}/{recs['items'][0]['id']}", json={"cantidad_litros": "85"}, headers=h
    )
    print(f"  corregir una recepción que ya tenía -> {correccion.status_code}")
    assert correccion.status_code == 200, "no se pudo cuadrar la última quincena del retirado"


# ---------------------------------------------------------------------------
# (c) Reactivarlo lo vuelve a habilitar.
# ---------------------------------------------------------------------------
def test_reactivar_vuelve_a_habilitar_al_proveedor(client, base_datos):
    """Desactivar es reversible: el que se fue y volvió no hay que crearlo de
    nuevo (crearlo otra vez le partiría la historia en dos fichas)."""
    h = auth_headers(client, "admin.a")
    prov = crear_proveedor(client, h, "Don Efraín")

    client.post(f"{PROV}/{prov['id']}/desactivar", headers=h)
    bloqueada = registrar_leche(client, h, prov["id"], 90, 4)

    r = client.post(f"{PROV}/{prov['id']}/activar", headers=h)
    assert r.status_code == 200, r.text
    permitida = registrar_leche(client, h, prov["id"], 90, 4)

    print("\n===== (c) REACTIVAR =====")
    print(f"  estando inactivo: {bloqueada.status_code} · {detalle(bloqueada)[:60]}…")
    print(f"  estado tras activar: {r.json()['estado']}")
    print(f"  ya reactivado:    {permitida.status_code} · "
          f"{permitida.json().get('cantidad_litros')} litros")
    assert bloqueada.status_code == 422
    assert r.json()["estado"] == "activo"
    assert permitida.status_code == 201

    # Idempotente: darle dos veces al botón no puede reventar ni cambiar nada.
    otra = client.post(f"{PROV}/{prov['id']}/activar", headers=h)
    print(f"  activar de nuevo (doble clic): {otra.status_code} · {otra.json()['estado']}")
    assert otra.status_code == 200
    assert otra.json()["estado"] == "activo"


# ---------------------------------------------------------------------------
# (d) Multiempresa: nadie desactiva al proveedor de otra quesera.
# ---------------------------------------------------------------------------
def test_no_se_puede_desactivar_el_proveedor_de_otra_empresa(client, base_datos):
    """El aislamiento por empresa no se puede quedar por fuera del endpoint nuevo.

    El admin de la Quesera B sabe el id del proveedor de la Quesera A (aquí se le
    pasa a mano) y le pega directo. Tiene que rebotar en 404 y —lo importante— el
    proveedor de A tiene que seguir activo.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prov_a = crear_proveedor(client, ha, "Finca de la A")

    r = client.post(f"{PROV}/{prov_a['id']}/desactivar", headers=hb)
    estado_a = client.get(f"{PROV}/{prov_a['id']}", headers=ha).json()["estado"]

    print("\n===== (d) EL PROVEEDOR DE OTRA EMPRESA =====")
    print(f"  admin.b desactiva al proveedor de A -> {r.status_code} · {detalle(r)}")
    print(f"  estado del proveedor de A: {estado_a}")
    assert r.status_code == 404, "un admin desactivó al proveedor de otra quesera"
    assert estado_a == "activo"

    # Y al revés con activar, por si acaso.
    client.post(f"{PROV}/{prov_a['id']}/desactivar", headers=ha)
    r2 = client.post(f"{PROV}/{prov_a['id']}/activar", headers=hb)
    estado_a = client.get(f"{PROV}/{prov_a['id']}", headers=ha).json()["estado"]
    print(f"  admin.b lo reactiva -> {r2.status_code}; estado: {estado_a}")
    assert r2.status_code == 404
    assert estado_a == "inactivo"


# ---------------------------------------------------------------------------
# La caneca, ahora protegida.
# ---------------------------------------------------------------------------
def test_la_caneca_no_se_lleva_al_proveedor_con_historia(client, base_datos):
    """La caneca era el problema de fondo que reportó el dueño.

    Borra en lógico, pero como `deleted_at IS NULL` está en toda consulta, el
    proveedor eliminado se le desaparecía de la pantalla —hasta con el filtro
    Estado: Inactivo— y no había cómo devolverlo. Ahora, si tiene leche recibida
    o liquidaciones, rebota y lo manda a desactivar.
    """
    h = auth_headers(client, "admin.a")
    prov = crear_proveedor(client, h, "Don Ramiro")
    assert registrar_leche(client, h, prov["id"], 120, 2).status_code == 201

    r = client.delete(f"{PROV}/{prov['id']}", headers=h)
    print("\n===== CANECA PROTEGIDA =====")
    print(f"  DELETE con 1 recepción -> {r.status_code}")
    print(f"  mensaje: {detalle(r)}")
    assert r.status_code == 422
    assert "Desactivar" in detalle(r), "el mensaje no ofrece la salida buena"

    sigue = client.get(f"{PROV}/{prov['id']}", headers=h)
    print(f"  el proveedor sigue ahí: {sigue.status_code} · {sigue.json()['estado']}")
    assert sigue.status_code == 200

    # El creado por error, sin nada colgando, sí se puede eliminar: ahí no hay
    # historia que perder y obligar a desactivarlo dejaría basura en la lista.
    vacio = crear_proveedor(client, h, "Creado por error")
    r2 = client.delete(f"{PROV}/{vacio['id']}", headers=h)
    print(f"  DELETE de uno recién creado y sin historia -> {r2.status_code}")
    assert r2.status_code == 204
