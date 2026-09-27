"""EL CANDADO DE UN ANTICIPO DICE POR QUÉ, CON EL MISMO TEXTO QUE EL SERVIDOR.

La pantalla de Anticipos explicaba el candado con su propia copia, escogiendo el texto
por `liquidacion_estado`. Al adelanto de $300.000 de Beto —quincena 'aprobada' cuyos
$120.000 de deuda ya se cobró la siguiente, sin un solo pago— le decía "ya tiene un pago
registrado. Elimine primero ese pago", y ese pago no existe.

Ahora `AnticipoRead.candado_aviso` sale de `_por_que_no_se_mueve`, la MISMA función que
escribe el 422 de modificar y de eliminar; `bloqueado` es "hay aviso". Aquí se mide cada
razón que el guardia distingue: la marca (en el GET de uno y en la lista) trae el
texto del rebote, palabra por palabra, y el rebote no mueve un peso.

Y LA DEUDA BORRADA POR LA MIGRACIÓN SE DICE DE PRIMERA, en el anticipo, en Anular y en
Corregir: las otras razones mandan a "Corregir esta quincena" o a anular la que cobró la
deuda, y sobre esa fila corregir rebota siempre.
"""
import uuid
from datetime import date
from decimal import Decimal

from app.modules.liquidaciones.models import Anticipo, Liquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import _migrada

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
LA_SALIDA_QUE_REBOTA = "Corregir esta quincena"


def D(v):
    return Decimal(str(v))


def _detalle(r):
    return r.json()["error"]["detail"]


def _proveedor(client, h, nombre, precio="1800"):
    r = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": precio}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _dia(client, h, prov, fecha, litros, precio=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov, "cantidad_litros": litros}
    if precio:
        cuerpo["precio_litro"] = precio
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _adelanto(client, h, prov, fecha, valor):
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov, "fecha": fecha,
                               "valor": valor}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _generar(client, h, periodo, prov):
    r = client.post(f"{API}/generar", json={
        "periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)["id"]


def _aprobar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _plata(client, h, liq_id):
    x = client.get(f"{API}/{liq_id}", headers=h).json()
    plata = {k: D(x[k]) for k in ("valor_total", "anticipos", "pagado", "saldo")}
    return plata | {k: x[k] for k in ("estado", "version", "deuda_trasladada_a_id")} | {
        "pagos": len(x["pagos"])}


def _marca(client, h, anticipo_id):
    """La marca por los DOS caminos que usa la pantalla, que tienen que coincidir."""
    uno = client.get(f"{ANT}/{anticipo_id}", headers=h)
    assert uno.status_code == 200, uno.text
    lista = client.get(ANT, params={"page_size": 200}, headers=h)
    assert lista.status_code == 200, lista.text
    en_lista = next(a for a in lista.json()["items"] if a["id"] == anticipo_id)
    uno = uno.json()
    for campo in ("bloqueado", "candado_aviso", "liquidacion_estado"):
        assert uno[campo] == en_lista[campo], campo
    return uno


def _trabado(client, h, anticipo_id, liq_id=None):
    """Trabado en pantalla Y en el servidor, con el mismo texto; nada se movió.
    Devuelve el aviso que ve el dueño."""
    marca = _marca(client, h, anticipo_id)
    aviso = marca["candado_aviso"]
    antes = _plata(client, h, liq_id) if liq_id else None
    put = client.put(f"{ANT}/{anticipo_id}", json={"valor": "1000"}, headers=h)
    delete = client.delete(f"{ANT}/{anticipo_id}", headers=h)
    print(f"\n  aviso: {aviso!r}")
    assert marca["bloqueado"] is True and aviso
    assert put.status_code == 422 and delete.status_code == 422
    # EL MISMO TEXTO: solo cambia el verbo ("modificar ni eliminar" en la pantalla).
    assert aviso.replace("modificar ni eliminar", "modificar") == _detalle(put)
    assert aviso.replace("modificar ni eliminar", "eliminar") == _detalle(delete)
    if liq_id:
        assert _plata(client, h, liq_id) == antes
    assert client.get(f"{ANT}/{anticipo_id}", headers=h).status_code == 200
    return aviso


def _libre(client, h, anticipo_id):
    marca = _marca(client, h, anticipo_id)
    assert marca["bloqueado"] is False and marca["candado_aviso"] is None
    put = client.put(f"{ANT}/{anticipo_id}", json={"valor": marca["valor"]}, headers=h)
    assert put.status_code == 200, put.text
    assert put.json()["candado_aviso"] is None


def _quincena(client, h, nombre, litros, adelanto):
    """litros × $1.800 en junio contra un adelanto; aprobada."""
    prov = _proveedor(client, h, nombre)
    _dia(client, h, prov, "2026-06-02", litros)
    ant = _adelanto(client, h, prov, "2026-06-01", adelanto)
    liq = _generar(client, h, Q1, prov)
    _aprobar(client, h, liq)
    return prov, ant, liq


# ---------------------------------------------------------------------------------------
def test_suelto_y_en_quincena_abierta_no_hay_candado_ni_aviso(client, base_datos):
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Suelto")
    suelto = _adelanto(client, h, prov, "2026-06-01", "50000")
    _libre(client, h, suelto)
    _, ant, _ = _quincena(client, h, "Abierta", "100", "50000")   # aprobada, sin pagos
    _libre(client, h, ant)


def test_deuda_cobrada_en_otra_quincena_dice_donde_y_no_inventa_un_pago(client, base_datos):
    """El caso de Beto (auditoría D1): 100 L × $1.800 = $180.000 contra $300.000, debe
    $120.000; la segunda de junio (100 L × $2.500 = $250.000) se los cobra."""
    h = auth_headers(client, "admin.a")
    prov, ant, q1 = _quincena(client, h, "Beto", "100", "300000")
    _dia(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2, prov)
    assert client.get(f"{API}/{q1}", headers=h).json()["deuda_trasladada_a_id"] == q2
    aviso = _trabado(client, h, ant, q1)
    assert "($120.000) ya se le cobró en la liquidación del 16/06/2026 al 30/06/2026" in aviso
    assert "pago registrado" not in aviso and "ya se pagó" not in aviso


def test_pagada_con_plata_entregada_dice_ya_se_pago(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, ant, liq = _quincena(client, h, "Nora", "100", "50000")    # neto 130.000
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    aviso = _trabado(client, h, ant, liq)
    assert "ya se pagó" in aviso


def test_pagada_sin_que_saliera_un_peso_no_dice_ya_se_pago(client, base_datos, db_session):
    """La que el botón Pagar de antes dejó 'pagada' con el tercero debiendo: $180.000
    contra $300.000, pagado $0, saldo −$120.000. No se le entregó nada a nadie."""
    h = auth_headers(client, "admin.a")
    _, ant, liq = _quincena(client, h, "Pagada Debiendo", "100", "300000")
    db_session.get(Liquidacion, uuid.UUID(liq)).estado = "pagada"
    db_session.commit()
    aviso = _trabado(client, h, ant, liq)
    assert "sin que saliera un peso" in aviso and "debiendo $120.000" in aviso
    assert "ya se pagó" not in aviso


def test_quincena_ya_corregida_manda_a_corregir(client, base_datos):
    """100 L = $180.000 − $50.000 = $130.000, pagada; se le corrige un día de $36.000
    (20 L): parcial v2. Corregir sí se puede aquí, así que la salida es verdad."""
    h = auth_headers(client, "admin.a")
    prov, ant, liq = _quincena(client, h, "Corregida", "100", "50000")
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    olvidado = _dia(client, h, prov, "2026-06-05", "20")
    r = client.post(f"{API}/{liq}/corregir", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}, headers=h)
    assert r.status_code == 200 and r.json()["version"] == 2, r.text
    aviso = _trabado(client, h, ant, liq)
    assert "comprobante corregido" in aviso and LA_SALIDA_QUE_REBOTA in aviso


def test_con_un_abono_dice_que_hay_un_pago_y_de_verdad_lo_hay(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, ant, liq = _quincena(client, h, "Con Abono", "100", "50000")
    r = client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20", "valor": "30000"},
                    headers=h)
    assert r.status_code == 200 and r.json()["estado"] == "parcial", r.text
    assert len(r.json()["pagos"]) == 1
    aviso = _trabado(client, h, ant, liq)
    assert "ya tiene un pago registrado" in aviso


def test_el_que_solto_una_correccion_nombra_las_dos_salidas(client, base_datos):
    """250 L × $2.000 = $500.000 − $200.000, pagada; la corrección suelta el adelanto."""
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Libardo", precio="2000")
    _dia(client, h, prov, "2026-06-02", "250")
    ant = _adelanto(client, h, prov, "2026-06-03", "200000")
    liq = _generar(client, h, Q1, prov)
    _aprobar(client, h, liq)
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    r = client.post(f"{API}/{liq}/corregir", json={
        "motivo": "no era de esta quincena", "anticipos_a_soltar": [ant]}, headers=h)
    assert r.status_code == 200, r.text
    marca = _marca(client, h, ant)
    assert marca["liquidacion_id"] is None and marca["liquidacion_estado"] is None
    aviso = _trabado(client, h, ant)
    assert "una corrección lo sacó" in aviso and "YA SE IMPRIMIÓ" in aviso


def test_el_descontado_en_nomina(client, base_datos):
    h = auth_headers(client, "admin.a")
    r = client.post(f"{V}/empleados", json={"nombre": "Aurelio", "apellido": "Marin",
                                             "cargo": "Quesero", "valor_dia": "40000"},
                    headers=h)
    assert r.status_code == 201, r.text
    emp = r.json()["id"]
    r = client.post(ANT, json={"tipo": "empleado", "empleado_id": emp,
                               "fecha": "2026-07-03", "valor": "100000"}, headers=h)
    assert r.status_code == 201, r.text
    ant = r.json()["id"]
    r = client.post(f"{V}/nomina", json={"empleado_id": emp, "fecha": "2026-07-15",
                                         "dias_trabajados": "12"}, headers=h)
    assert r.status_code == 201, r.text
    aviso = _trabado(client, h, ant)
    assert "pago de nómina" in aviso


def test_el_que_apunta_a_una_liquidacion_que_no_se_ve(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov = _proveedor(client, h, "Cruzado")
    ant = _adelanto(client, h, prov, "2026-06-01", "50000")
    ajena = Liquidacion(
        empresa_id=base_datos["empresa_b"].id, tipo="proveedor",
        proveedor_id=uuid.UUID(prov), periodo_inicio=date(2026, 6, 1),
        periodo_fin=date(2026, 6, 15), total_litros=D("100"), valor_bruto=D("180000"),
        valor_total=D("180000"), anticipos=D("0"), saldo_anterior=D("0"), pagado=D("0"),
        saldo=D("180000"), estado="aprobada")
    db_session.add(ajena)
    db_session.flush()
    db_session.get(Anticipo, uuid.UUID(ant)).liquidacion_id = ajena.id
    db_session.commit()
    aviso = _trabado(client, h, ant)
    assert "no se puede consultar" in aviso


# ---------------------------------------------------------------------------------------
# LA DEUDA BORRADA POR LA MIGRACIÓN, DE PRIMERA (auditoría D3).
# ---------------------------------------------------------------------------------------
def _anticipo_de(client, h, liq_id):
    lista = client.get(ANT, params={"page_size": 200}, headers=h).json()["items"]
    return next(a["id"] for a in lista if a["liquidacion_id"] == liq_id)


def _como_la_dejo_corregir_antes_del_guardia(db, liq_id):
    """Parcial, v2, $230.000 − $300.000 = neto −$70.000, pagado −$120.000, saldo
    +$50.000: la forma que dejó en producción corregirla antes del guardia."""
    fila = db.get(Liquidacion, uuid.UUID(liq_id))
    fila.estado, fila.version = "parcial", 2
    fila.valor_bruto = fila.valor_total = D("230000")
    fila.saldo = D("50000")
    db.commit()


def _es_la_deuda_borrada(texto):
    return ("antes de que existieran los abonos" in texto and "($120.000)" in texto
            and "repararla" in texto and LA_SALIDA_QUE_REBOTA not in texto
            and "ya se pagó" not in texto)


def test_el_anticipo_de_la_migrada_dice_la_razon_de_verdad(client, base_datos, db_session):
    """La migrada sin tocar ('pagada', pagado −$120.000, sin pagos) decía "ya se pagó",
    y no se le pagó nada; la corregida (v2) mandaba a "Corregir esta quincena", que en
    ella rebota siempre. Las dos dicen ahora la deuda borrada."""
    h = auth_headers(client, "admin.a")
    _, sin_tocar = _migrada(client, h, db_session, "Migrada Sin Tocar")
    _, corregida = _migrada(client, h, db_session, "Migrada Corregida")
    _como_la_dejo_corregir_antes_del_guardia(db_session, corregida["id"])
    for liq in (sin_tocar, corregida):
        aviso = _trabado(client, h, _anticipo_de(client, h, liq["id"]), liq["id"])
        assert aviso.startswith("No se puede modificar ni eliminar el anticipo de esta quincena")
        assert _es_la_deuda_borrada(aviso), aviso


def test_anular_la_migrada_dice_la_razon_de_verdad(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, sin_tocar = _migrada(client, h, db_session, "Anular Sin Tocar")
    _, corregida = _migrada(client, h, db_session, "Anular Corregida")
    _como_la_dejo_corregir_antes_del_guardia(db_session, corregida["id"])
    for liq in (sin_tocar, corregida):
        antes = _plata(client, h, liq["id"])
        r = client.post(f"{API}/{liq['id']}/anular", headers=h)
        print(f"\n  anular v{antes['version']} -> {r.status_code} {_detalle(r)!r}")
        assert r.status_code == 422
        assert _detalle(r).startswith("No se puede anular esta quincena")
        assert _es_la_deuda_borrada(_detalle(r)), _detalle(r)
        assert _plata(client, h, liq["id"]) == antes


def test_con_deuda_borrada_y_deuda_cobrada_se_nombra_primero_la_borrada(
        client, base_datos, db_session):
    """Una migrada que se corrigió hacia ABAJO antes del guardia (valor $100.000: neto
    −$200.000, saldo −200.000 − (−120.000) = −$80.000) y cuyos $80.000 visibles ya los
    cobró otra quincena. Mandar primero a "anule esa liquidación" es anular un
    comprobante para nada: después chocaría con la deuda borrada, que no tiene salida."""
    h = auth_headers(client, "admin.a")
    prov, migrada = _migrada(client, h, db_session, "Borrada Y Cobrada")
    fila = db_session.get(Liquidacion, uuid.UUID(migrada["id"]))
    fila.estado, fila.version = "pagada", 2
    fila.valor_bruto = fila.valor_total = D("100000")
    fila.saldo = D("-80000")
    cobra = Liquidacion(
        empresa_id=fila.empresa_id, tipo="proveedor", proveedor_id=fila.proveedor_id,
        periodo_inicio=date(2026, 7, 16), periodo_fin=date(2026, 7, 31),
        total_litros=D("100"), valor_bruto=D("200000"), valor_total=D("200000"),
        anticipos=D("0"), saldo_anterior=D("80000"), pagado=D("0"), saldo=D("120000"),
        estado="aprobada")
    db_session.add(cobra)
    db_session.flush()
    fila.deuda_trasladada_a_id = cobra.id
    db_session.commit()
    leida = client.get(f"{API}/{migrada['id']}", headers=h).json()
    assert D(leida["neto_a_pagar"]) == D(leida["pagado"]) + D(leida["saldo"])
    assert D(leida["deuda_borrada_por_la_migracion"]) == D("120000")

    olvidado = _dia(client, h, prov, "2026-07-09", "5")
    respuestas = {
        "anticipo": _trabado(client, h, _anticipo_de(client, h, migrada["id"]),
                             migrada["id"]),
        "anular": _detalle(client.post(f"{API}/{migrada['id']}/anular", headers=h)),
        "corregir": _detalle(client.post(f"{API}/{migrada['id']}/corregir/previsualizar",
                                         json={"motivo": "día olvidado",
                                               "recepciones_a_incluir": [olvidado]},
                                         headers=h)),
    }
    for nombre, texto in respuestas.items():
        print(f"\n  {nombre}: {texto!r}")
        assert _es_la_deuda_borrada(texto), (nombre, texto)
        assert "Anule primero" not in texto, nombre
