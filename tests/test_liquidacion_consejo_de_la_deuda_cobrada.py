"""«ANULE PRIMERO ESA LIQUIDACIÓN» SOLO CUANDO ES VERDAD, Y SOLO A QUIEN PUEDE HACERLO.

Cuando lo que una quincena quedó debiendo ya se le cobró en otra, sus cifras quedan
congeladas y cada botón que las movería rebota nombrando la otra. El consejo era siempre
el mismo —"anule primero esa liquidación y vuelva a intentarlo"— y medido era falso en
tres casos. Las cifras son las del dueño:

  · Beto: 100 L × $1.800 = $180.000 contra $300.000 de adelanto, debe $120.000. La del
    16/06 (100 L × $2.500 = $250.000 − $120.000 = $130.000) se los cobra.
  · Carla: 100 L × $1.800 = $180.000 cubiertos exacto por el adelanto, pagada; corregida a
    $1.500 queda en $150.000 y debe $30.000, que se cobra la siguiente.

Cada prueba sigue el consejo que da el servidor y mide que lleve a donde dice. Y la
pantalla lee el mismo texto (`avisos_deuda_cobrada`): se compara contra el 422 de cada
botón, palabra por palabra.
"""
import uuid
from decimal import Decimal

from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import por_que_no_se_anula, por_que_no_se_corrige
from tests.conftest import auth_headers
from tests.test_liquidacion_corregir_permisos import crear_usuario_con_rol

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
Q3 = ("2026-07-01", "2026-07-15")
DEUDA_EN_Q2 = "ya se le cobró en la liquidación del 16/06/2026 al 30/06/2026"
ANULE = (
    "Anule primero esa liquidación —así esta deuda vuelve a quedar libre— y vuelva a "
    "intentarlo."
)
ORDEN = "empiece por la más vieja —la del 01/06/2026 al 15/06/2026—"


def D(v):
    return Decimal(str(v))


def _detalle(r):
    return r.json()["error"]["detail"]


def _prov(client, h, nombre, precio="1800"):
    r = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                              "precio_litro": precio}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _dia(client, h, prov, fecha, litros, precio=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov, "cantidad_litros": litros}
    if precio:
        cuerpo["precio_litro"] = precio
    r = client.post(f"{V}/recepciones", json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _adelanto(client, h, prov, valor, fecha="2026-06-01"):
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                               "fecha": fecha, "valor": valor}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _gen(client, h, periodo, prov):
    r = client.post(f"{API}/generar", json={"periodo_inicio": periodo[0],
                                            "periodo_fin": periodo[1],
                                            "tipo": "proveedor"}, headers=h)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _beto(client, h, nombre="Beto"):
    """Q1 en borrador debiendo $120.000; Q2 ($250.000) se los cobra y queda en borrador."""
    prov = _prov(client, h, nombre)
    _dia(client, h, prov, "2026-06-02", "100")
    adelanto = _adelanto(client, h, prov, "300000")
    q1 = _gen(client, h, Q1, prov)
    assert q1["estado"] == "borrador" and D(q1["saldo"]) == D("-120000")
    _dia(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _gen(client, h, Q2, prov)
    assert D(q2["saldo_anterior"]) == D("120000") and D(q2["saldo"]) == D("130000")
    return prov, adelanto, q1, q2


def _los_422(client, h, q1):
    """El 422 de cada botón del detalle sobre Q1, por la clave de `avisos_deuda_cobrada`."""
    dia = q1["detalles"][0]["id"]
    return {
        "anular": _detalle(client.post(f"{API}/{q1['id']}/anular", headers=h)),
        "corregir": _detalle(client.post(f"{API}/{q1['id']}/corregir/previsualizar",
                                         json={"motivo": "mirar"}, headers=h)),
        "recalcular": _detalle(client.post(f"{API}/{q1['id']}/recalcular", headers=h)),
        "precio": _detalle(client.put(f"{API}/{q1['id']}/detalles/{dia}",
                                      json={"precio_litro": "1900"}, headers=h)),
    }


# ---------------------------------------------------------------------------------------
def test_con_la_otra_en_borrador_el_consejo_sigue_y_es_cierto(client, base_datos, db_session):
    """El caso donde el consejo de siempre SÍ sirve se queda igual, con el orden para
    volver a generarlas. Y la pantalla recibe, botón por botón, el mismo 422."""
    h = auth_headers(client, "admin.a")
    _, _, q1, q2 = _beto(client, h)
    leida = _leer(client, h, q1["id"])
    avisos = leida["avisos_deuda_cobrada"]
    rebotes = _los_422(client, h, leida)
    print(f"\n  recalcular: {rebotes['recalcular']}")
    assert set(avisos) == {"anular", "corregir", "recalcular", "precio"}
    assert avisos == rebotes
    for accion in ("anular", "recalcular", "precio"):
        assert DEUDA_EN_Q2 in rebotes[accion] and ANULE in rebotes[accion], accion
        assert ORDEN in rebotes[accion], accion
    # Corregir es solo para las pagadas: anular la otra no lo abre en un borrador, y el
    # texto no lo promete.
    assert "nule primero" not in rebotes["corregir"]
    assert "la corrección es solo para las que ya se pagaron" in rebotes["corregir"]
    # La pregunta pública es la misma del guardia.
    fila = db_session.get(Liquidacion, uuid.UUID(q1["id"]))
    assert por_que_no_se_anula(fila) == rebotes["anular"]
    assert por_que_no_se_corrige(fila) == rebotes["corregir"]

    # Se sigue el consejo: anular la otra y recalcular. Pasa.
    _ok(client.post(f"{API}/{q2['id']}/anular", headers=h))
    assert _ok(client.post(f"{API}/{q1['id']}/recalcular", headers=h))["estado"] == "borrador"
    assert _leer(client, h, q1["id"])["avisos_deuda_cobrada"] == {}


def test_la_otra_corregida_no_se_deja_anular_y_el_consejo_no_manda_ahi(
    client, base_datos, db_session
):
    """Beto sin salida: la del 16/06 se pagó ($130.000) y se corrigió con un día olvidado de
    10 L × $2.500 (versión 2). Anularla rebota siempre, aunque se le borre el pago."""
    h = auth_headers(client, "admin.a")
    prov, adelanto, q1, q2 = _beto(client, h, "Beto Sinsalida")
    _ok(client.post(f"{API}/{q2['id']}/aprobar", headers=h))
    _ok(client.post(f"{API}/{q2['id']}/pagar", headers=h))
    olvidado = _dia(client, h, prov, "2026-06-25", "10", precio="2500")
    q2 = _ok(client.post(f"{API}/{q2['id']}/corregir", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}, headers=h))
    assert (q2["estado"], q2["version"]) == ("parcial", 2)

    r = client.post(f"{API}/{q1['id']}/recalcular", headers=h)
    texto = _detalle(r)
    print(f"\n  recalcular Q1 -> {r.status_code}: {texto}")
    assert r.status_code == 422 and DEUDA_EN_Q2 in texto
    assert "nule primero" not in texto and ORDEN not in texto
    assert ("Y esa liquidación no se puede anular, porque de ella ya salieron 2 comprobantes "
            "(el original y sus correcciones): por ahí no hay salida dentro del sistema. "
            "Si la cifra está mala, registre el ajuste en la quincena siguiente") in texto
    assert _leer(client, h, q1["id"])["avisos_deuda_cobrada"]["recalcular"] == texto

    # El adelanto de $300.000 dice lo mismo, sin mandar a anular.
    aviso = client.get(f"{ANT}/{adelanto}", headers=h).json()["candado_aviso"]
    print(f"  adelanto: {aviso}")
    assert "nule primero" not in aviso and "por ahí no hay salida" in aviso

    # Y es cierto: la otra no se deja anular, y su 422 es la pregunta pública.
    an = client.post(f"{API}/{q2['id']}/anular", headers=h)
    assert an.status_code == 422
    assert _detalle(an) == por_que_no_se_anula(db_session.get(Liquidacion, uuid.UUID(q2["id"])))


def test_si_anular_la_otra_no_destraba_esta_no_se_aconseja(client, base_datos):
    """Carla: pagada (el adelanto cubrió exacto) y corregida a $150.000, debe $30.000; la
    siguiente (aprobada) se los cobra. Anular la siguiente NO suelta el adelanto ni Anular
    de Carla —sigue pagada y corregida—, pero SÍ le abre Corregir."""
    h = auth_headers(client, "admin.a")
    prov = _prov(client, h, "Carla Cobrada")
    _dia(client, h, prov, "2026-06-03", "100")
    adelanto = _adelanto(client, h, prov, "180000")
    q1 = _gen(client, h, Q1, prov)
    _ok(client.post(f"{API}/{q1['id']}/aprobar", headers=h))
    _ok(client.post(f"{API}/{q1['id']}/pagar", headers=h))
    dia = _leer(client, h, q1["id"])["detalles"][0]["id"]
    q1 = _ok(client.post(f"{API}/{q1['id']}/corregir", json={
        "motivo": "precio mal digitado",
        "precios": [{"detalle_id": dia, "precio_litro": "1500"}]}, headers=h))
    assert (q1["estado"], q1["version"], D(q1["saldo"])) == ("pagada", 2, D("-30000"))
    _dia(client, h, prov, "2026-06-20", "100")
    q2 = _gen(client, h, Q2, prov)
    _ok(client.post(f"{API}/{q2['id']}/aprobar", headers=h))
    assert D(q2["saldo_anterior"]) == D("30000")

    aviso = client.get(f"{ANT}/{adelanto}", headers=h).json()["candado_aviso"]
    put = client.put(f"{ANT}/{adelanto}", json={"valor": "150000"}, headers=h)
    print(f"\n  adelanto: {aviso}")
    assert "nule primero" not in aviso
    assert ("Y anular esa liquidación no la destrabaría, porque esta quincena ya quedó "
            "cerrada como pagada. Si la cifra está mala, registre el ajuste en la quincena "
            "siguiente") in aviso
    assert put.status_code == 422
    assert _detalle(put) == aviso.replace("modificar ni eliminar", "modificar")

    anular = _detalle(client.post(f"{API}/{q1['id']}/anular", headers=h))
    assert "nule primero" not in anular
    assert "de esta quincena ya salieron 2 comprobantes" in anular

    precio = {"motivo": "precio", "precios": [{"detalle_id": dia, "precio_litro": "1600"}]}
    corregir = client.post(f"{API}/{q1['id']}/corregir/previsualizar", json=precio, headers=h)
    assert corregir.status_code == 422 and ANULE in _detalle(corregir)
    # Pero sin "vuelva a generar las dos": Carla, corregida, ya no se puede anular.
    assert "volver a generar las dos" not in _detalle(corregir)
    assert _leer(client, h, q1["id"])["avisos_deuda_cobrada"]["corregir"] == _detalle(corregir)

    # Se sigue el consejo de Corregir: anular la siguiente. Corregir pasa; el adelanto
    # sigue trabado, como dijo su aviso.
    _ok(client.post(f"{API}/{q2['id']}/anular", headers=h))
    assert client.post(f"{API}/{q1['id']}/corregir/previsualizar", json=precio,
                       headers=h).status_code == 200
    assert client.get(f"{ANT}/{adelanto}", headers=h).json()["bloqueado"] is True


def test_si_la_otra_tiene_pagos_lo_dice_con_la_cifra_y_el_camino_es_cierto(client, base_datos):
    """La del 16/06 pagada normal con $130.000. El consejo dice la cifra, avisa que los
    soportes no vuelven, ofrece primero el ajuste y, si el pago estaba mal registrado, el
    camino largo: borrarlo, anularla y recalcular. Se sigue y pasa."""
    h = auth_headers(client, "admin.a")
    _, _, q1, q2 = _beto(client, h, "Beto Pago")
    _ok(client.post(f"{API}/{q2['id']}/aprobar", headers=h))
    q2 = _ok(client.post(f"{API}/{q2['id']}/pagar", headers=h))
    texto = _detalle(client.post(f"{API}/{q1['id']}/recalcular", headers=h))
    print(f"\n  recalcular Q1: {texto}")
    assert "nule primero" not in texto
    assert ("Esa liquidación ya tiene un pago registrado por $130.000: para anularla habría "
            "que borrárselo antes, y con él sus soportes, que no se recuperan.") in texto
    assert "no la anule y registre el ajuste en la quincena siguiente" in texto
    assert "si ese pago quedó mal registrado, bórrelo, anule esa liquidación" in texto
    assert ORDEN in texto
    _ok(client.delete(f"{API}/{q2['id']}/pagos/{q2['pagos'][0]['id']}", headers=h))
    _ok(client.post(f"{API}/{q2['id']}/anular", headers=h))
    _ok(client.post(f"{API}/{q1['id']}/recalcular", headers=h))


def test_la_aprobada_rebota_recalcular_por_el_estado_y_no_por_la_deuda(client, base_datos):
    """Q1 APROBADA, la siguiente se le cobra. "Anule primero esa y vuelva a intentarlo"
    era falso: anulada la otra, Recalcular volvía a rebotar por no ser borrador. Rebota por
    el estado, como lo pregunta la pantalla, y lo mismo el lápiz del precio."""
    h = auth_headers(client, "admin.a")
    _, _, q1, q2 = _beto(client, h, "Beto Aprobada")
    _ok(client.post(f"{API}/{q1['id']}/aprobar", headers=h))
    leida = _leer(client, h, q1["id"])
    rebotes = _los_422(client, h, leida)
    print(f"\n  recalcular: {rebotes['recalcular']}")
    assert rebotes["recalcular"] == (
        "Esta liquidación está en 'aprobada': solo se puede recalcular mientras sea un "
        "borrador"
    )
    assert "solo se puede corregir el precio mientras sea un borrador" in rebotes["precio"]
    avisos = leida["avisos_deuda_cobrada"]
    assert "recalcular" not in avisos and "precio" not in avisos
    # Anular la aprobada sí rebota por la deuda, y ahí anular la otra la destraba.
    assert avisos["anular"] == rebotes["anular"] and ANULE in rebotes["anular"]
    _ok(client.post(f"{API}/{q2['id']}/anular", headers=h))
    _ok(client.post(f"{API}/{q1['id']}/anular", headers=h))


def test_a_quien_no_puede_anular_le_dice_que_lo_pida(client, base_datos, db_session):
    """Compras recalcula y corrige el precio ('editar') pero no anula ('administrar') ni
    borra pagos ('eliminar'): el consejo no le nombra un botón que el servidor le niega, y
    su pantalla solo recibe los avisos de los botones que tiene."""
    h = auth_headers(client, "admin.a")
    _, adelanto, q1, _ = _beto(client, h, "Beto Compras")
    crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Compras", "rol.compras")
    hc = auth_headers(client, "rol.compras")

    leida = _leer(client, hc, q1["id"])
    assert set(leida["avisos_deuda_cobrada"]) == {"recalcular", "precio"}
    r = client.post(f"{API}/{q1['id']}/recalcular", headers=hc)
    texto = _detalle(r)
    print(f"\n  recalcular (Compras): {texto}")
    assert r.status_code == 422 and texto == leida["avisos_deuda_cobrada"]["recalcular"]
    assert ("Pídale a un Administrador de la empresa que anule primero esa liquidación —así "
            "esta deuda vuelve a quedar libre— y después vuelva a intentarlo.") in texto
    assert ANULE not in texto and ORDEN in texto
    aviso = client.get(f"{ANT}/{adelanto}", headers=hc).json()["candado_aviso"]
    assert "Pídale a un Administrador de la empresa" in aviso and ANULE not in aviso
    # Al administrador, el mismo adelanto le dice que la anule él.
    assert ANULE in client.get(f"{ANT}/{adelanto}", headers=h).json()["candado_aviso"]


def test_la_cadena_no_manda_a_anular_una_que_tampoco_se_deja(client, base_datos):
    """Q1 debe $120.000; Q2 (50 L × $1.800 = $90.000) se los cobra y queda debiendo
    $30.000; Q3 (100 L × $1.800 = $180.000) se cobra esos $30.000. Anular Q2 rebota porque
    SU deuda ya se cobró en Q3: el consejo de Q1 lo dice y no manda a anularla."""
    h = auth_headers(client, "admin.a")
    prov = _prov(client, h, "Cadena")
    _dia(client, h, prov, "2026-06-02", "100")
    _adelanto(client, h, prov, "300000")
    q1 = _gen(client, h, Q1, prov)
    _dia(client, h, prov, "2026-06-20", "50")
    q2 = _gen(client, h, Q2, prov)
    assert D(q2["saldo"]) == D("-30000")
    _dia(client, h, prov, "2026-07-05", "100")
    q3 = _gen(client, h, Q3, prov)
    assert D(q3["saldo_anterior"]) == D("30000")

    texto = _detalle(client.post(f"{API}/{q1['id']}/recalcular", headers=h))
    print(f"\n  recalcular Q1: {texto}")
    assert "nule primero" not in texto and ORDEN not in texto
    assert ("Y esa liquidación tampoco se puede anular: lo que ella quedó debiendo ya se le "
            "cobró en la del 01/07/2026 al 15/07/2026.") in texto
    assert client.post(f"{API}/{q2['id']}/anular", headers=h).status_code == 422
