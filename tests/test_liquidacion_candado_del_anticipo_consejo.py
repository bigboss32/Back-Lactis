"""EL CANDADO DEL ANTICIPO DA EL CONSEJO QUE DE VERDAD EXISTE, PARA QUIEN LO ESTÁ MIRANDO.

Cada quincena es de 100 L × $1.800 = $180.000.

EL CONSEJO SALÍA DEL ESTADO, NO DEL BOTÓN. Con un adelanto de $30.000 y abonos de $50.000 y
$20.000 ('parcial', saldo $80.000), el candado decía "ya tiene un pago registrado. Elimine
primero ese pago si de verdad hay que corregirlo". Se seguía: se borraba el de $50.000
(saldo $130.000, un pago) y el anticipo seguía trabado con el mismo texto. Un pago y sus
soportes perdidos para nada. Corregir sí cambia el valor de un anticipo y conserva los
pagos: 30.000 → 20.000 deja 180.000 − 20.000 − 70.000 = $90.000 con los dos vivos. Y el día
de esa misma quincena ya lo decía así ("use 'Corregir esta quincena' … Elimine primero
esos 2 pagos solo si …"): dos consejos distintos para la misma quincena. En la 'pagada'
(adelanto $20.000, $160.000 pagados) solo ofrecía el ajuste, y Corregir 20.000 → 30.000 la
acepta: saldo 180.000 − 30.000 − 160.000 = −$10.000.

Y NO PREGUNTABA EL PERMISO. `candado_aviso` es el tooltip de la lista de Anticipos, que leen
Supervisor, Compras, Contador y Consulta: a los cuatro les decía "Elimine primero ese
pago" o "use 'Corregir esta quincena'", y los dos botones les contestan 403. El día de esa
misma quincena ya les decía "pídale a un Administrador de la empresa". Lo mismo el 422 del
PUT de observaciones, al que Compras llega con 'editar'.

La razón (la primera frase) no cambia según quién mira; solo cambia el consejo. Y el
consejo para el Administrador de la quincena corregida (v2) se queda letra por letra.
"""
import uuid
from decimal import Decimal

from app.core.context import RequestContext
from app.modules.liquidaciones.models import Anticipo, Liquidacion, PagoLiquidacion
from app.modules.liquidaciones.service import _por_que_no_se_mueve
from tests.conftest import auth_headers
from tests.test_liquidacion_corregir_permisos import crear_usuario_con_rol
from tests.test_recepcion_candado_quincena_corregida import _corregida_sin_pagos

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
PIDA = "pídale a un Administrador de la empresa"
ROLES = ("Supervisor", "Compras", "Contador", "Consulta")
HECHO_ABONO = ("No se puede modificar ni eliminar este anticipo: la liquidación en la que se "
               "descontó ya tiene un pago registrado.")


def D(v):
    return Decimal(str(v))


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json() if r.content else None


def _detalle(r):
    return r.json()["error"]["detail"]


def _leer(client, h, liq):
    return _ok(client.get(f"{API}/{liq}", headers=h))


def _cuadra(liq):
    """La regla de oro, con calculadora."""
    neto = D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq["saldo_anterior"] or 0)
    assert D(liq["neto_a_pagar"]) == neto and D(liq["saldo"]) == neto - D(liq["pagado"]), liq


def _quincena(client, h, nombre, adelanto, abonos=(), pagar=False):
    prov = _ok(client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                                     "precio_litro": "1800"}, headers=h), 201)["id"]
    dia = _ok(client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                                     "cantidad_litros": "100"}, headers=h), 201)["id"]
    ant = _ok(client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov,
                                     "fecha": "2026-06-03", "valor": adelanto},
                          headers=h), 201)["id"]
    g = _ok(client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                                "periodo_fin": "2026-06-15",
                                                "tipo": "proveedor"}, headers=h))
    liq = next(x for x in g["generadas"] if x["proveedor_id"] == prov)["id"]
    _ok(client.post(f"{API}/{liq}/aprobar", headers=h))
    for valor in abonos:
        _ok(client.post(f"{API}/{liq}/pagos", json={"fecha": "2026-06-20", "valor": valor},
                        headers=h))
    if pagar:
        _ok(client.post(f"{API}/{liq}/pagar", headers=h))
    return dia, ant, liq


def _candado(client, h, ant):
    """El candado del GET de uno y el de la fila de la lista: el mismo texto."""
    uno = _ok(client.get(f"{ANT}/{ant}", headers=h))
    fila = next(x for x in _ok(client.get(ANT, params={"size": 100}, headers=h))["items"]
                if x["id"] == ant)
    assert uno["bloqueado"] and fila["candado_aviso"] == uno["candado_aviso"]
    return uno["candado_aviso"]


def _corregir_anticipo(client, h, liq, ant, valor):
    return client.post(f"{API}/{liq}/corregir", json={
        "motivo": "adelanto mal digitado",
        "valores_de_anticipos": [{"anticipo_id": ant, "valor": valor}]}, headers=h)


def _ctx(base_datos, *permisos):
    return RequestContext(empresa_id=base_datos["empresa_a"].id, user_id=uuid.uuid4(),
                          permisos=set(permisos))


# ---------------------------------------------------------------------------------------
def test_con_dos_abonos_manda_primero_a_corregir_y_cuenta_los_pagos(client, base_datos):
    h = auth_headers(client, "admin.a")
    dia, ant, liq = _quincena(client, h, "Dos Abonos", "30000", abonos=("50000", "20000"))
    antes = _leer(client, h, liq)
    assert (antes["estado"], antes["version"], D(antes["saldo"]), len(antes["pagos"])) == (
        "parcial", 1, D("80000"), 2)

    aviso = _candado(client, h, ant)
    put = client.put(f"{ANT}/{ant}", json={"valor": "20000"}, headers=h)
    print(f"\n  candado: {aviso}\n  PUT anticipo: {_detalle(put)}")
    consejo = (
        "Si la cifra está mala, use 'Corregir esta quincena', que conserva los pagos y sus "
        "soportes, o registre el ajuste en la quincena siguiente. Elimine primero esos 2 "
        "pagos solo si de verdad hay que cambiarlo desde aquí: con ellos se van sus "
        "soportes, que no se recuperan")
    assert aviso == f"{HECHO_ABONO} {consejo}"
    assert put.status_code == 422
    assert _detalle(put) == aviso.replace("modificar ni eliminar", "modificar")
    assert "ese pago" not in aviso

    # EL DÍA DE ESA MISMA QUINCENA DICE LO MISMO: Corregir primero y conservando los
    # pagos, y de último borrar LOS DOS, avisando que los soportes no vuelven.
    put_dia = client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h)
    assert put_dia.status_code == 422
    for pedazo in ("use 'Corregir esta quincena', que conserva los pagos y sus soportes",
                   "Elimine primero esos 2 pagos solo si de verdad hay que cambiar",
                   "con ellos se van sus soportes, que no se recuperan"):
        assert pedazo in _detalle(put_dia) and pedazo.split(" cambiar")[0] in aviso

    # SE SIGUE EL CONSEJO: Corregir 30.000 → 20.000 conserva los dos pagos.
    r = _corregir_anticipo(client, h, liq, ant, "20000")
    assert r.status_code == 200, r.text
    despues = _leer(client, h, liq)
    _cuadra(despues)
    assert (despues["estado"], despues["version"], D(despues["anticipos"]),
            D(despues["pagado"]), D(despues["saldo"]), len(despues["pagos"])) == (
        "parcial", 2, D("20000"), D("70000"), D("90000"), 2)


def test_borrar_los_dos_pagos_si_destraba_el_anticipo(client, base_datos):
    """La última salida, tal como se nombra: "esos 2 pagos". Borrando los dos la quincena
    vuelve a 'aprobada' y el anticipo se corrige: 180.000 − 20.000 = $160.000."""
    h = auth_headers(client, "admin.a")
    _, ant, liq = _quincena(client, h, "Borra Los Dos", "30000", abonos=("50000", "20000"))
    for p in _leer(client, h, liq)["pagos"]:
        _ok(client.delete(f"{API}/{liq}/pagos/{p['id']}", headers=h))
    _ok(client.put(f"{ANT}/{ant}", json={"valor": "20000"}, headers=h))
    despues = _leer(client, h, liq)
    _cuadra(despues)
    assert (despues["estado"], D(despues["anticipos"]), D(despues["saldo"])) == (
        "borrador", D("20000"), D("160000"))


def test_con_un_abono_dice_ese_pago_y_corregir_lo_conserva(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, ant, liq = _quincena(client, h, "Un Abono", "30000", abonos=("50000",))
    aviso = _candado(client, h, ant)
    assert aviso == (
        f"{HECHO_ABONO} Si la cifra está mala, use 'Corregir esta quincena', que conserva el "
        "pago y sus soportes, o registre el ajuste en la quincena siguiente. Elimine primero "
        "ese pago solo si de verdad hay que cambiarlo desde aquí: con él se van sus "
        "soportes, que no se recuperan")
    r = _corregir_anticipo(client, h, liq, ant, "20000")
    assert r.status_code == 200, r.text
    despues = _leer(client, h, liq)
    _cuadra(despues)
    assert (D(despues["saldo"]), len(despues["pagos"])) == (D("110000"), 1)


def test_la_pagada_ofrece_corregir_porque_corregir_la_acepta(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, ant, liq = _quincena(client, h, "Pagada", "20000", pagar=True)
    pagada = _leer(client, h, liq)
    assert (pagada["estado"], D(pagada["pagado"]), D(pagada["saldo"])) == (
        "pagada", D("160000"), D("0"))
    aviso = _candado(client, h, ant)
    assert aviso == (
        "No se puede modificar ni eliminar este anticipo: la liquidación en la que se "
        "descontó ya se pagó. Si la cifra está mala, use 'Corregir esta quincena', que "
        "conserva el pago y sus soportes, o registre el ajuste en la quincena siguiente")
    # En la pagada borrar el pago no se nombra: tampoco lo nombra su día.
    assert "Elimine" not in aviso
    r = _corregir_anticipo(client, h, liq, ant, "30000")
    assert r.status_code == 200, r.text
    despues = _leer(client, h, liq)
    _cuadra(despues)
    assert (D(despues["saldo"]), D(despues["le_queda_debiendo"])) == (D("-10000"), D("10000"))


def test_los_roles_sin_el_boton_leen_a_quien_pedirselo(client, base_datos, db_session):
    """(a) 'parcial' v1 con un abono de $50.000, saldo $100.000. El candado es el mismo
    para los cuatro roles sin 'administrar' ni 'eliminar', y no les nombra ningún botón."""
    h = auth_headers(client, "admin.a")
    hs = {}
    for rol in ROLES:
        crear_usuario_con_rol(db_session, base_datos["empresa_a"], rol, f"cand.{rol.lower()}")
        hs[rol] = auth_headers(client, f"cand.{rol.lower()}")
    dia, ant, liq = _quincena(client, h, "Roles Abono", "30000", abonos=("50000",))
    esperado = (
        f"{HECHO_ABONO} Si la cifra está mala, {PIDA} que use 'Corregir esta quincena', que "
        "conserva el pago y sus soportes, o registre el ajuste en la quincena siguiente. Si "
        f"de verdad hay que cambiarlo desde aquí, {PIDA} que elimine primero ese pago: con él "
        "se van sus soportes, que no se recuperan")
    for rol in ROLES:
        aviso = _candado(client, hs[rol], ant)
        assert aviso == esperado, rol
        assert "Elimine primero" not in aviso and ", use 'Corregir" not in aviso
    hc = hs["Compras"]
    put = client.put(f"{ANT}/{ant}", json={"valor": "20000"}, headers=hc)
    assert put.status_code == 422
    assert _detalle(put) == esperado.replace("modificar ni eliminar", "modificar")
    # Lo que el consejo ya no les nombra, medido: los dos botones les dan 403.
    pago = _leer(client, h, liq)["pagos"][0]
    assert client.delete(f"{API}/{liq}/pagos/{pago['id']}", headers=hc).status_code == 403
    assert client.post(f"{API}/{liq}/corregir/previsualizar", json={
        "motivo": "x", "valores_de_anticipos": [{"anticipo_id": ant, "valor": "20000"}]},
        headers=hc).status_code == 403
    # Y el día de esa quincena, para el mismo usuario, dice lo mismo.
    put_dia = client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=hc)
    for pedazo in (f"{PIDA} que use 'Corregir esta quincena', que conserva el pago y sus "
                   "soportes", f"{PIDA} que elimine primero ese pago"):
        assert pedazo in _detalle(put_dia) and pedazo in esperado
    assert D(_leer(client, h, liq)["saldo"]) == D("100000")


def test_la_corregida_y_las_observaciones_para_quien_no_puede_corregir(
    client, base_datos, db_session
):
    """(b) 'parcial' v2 de $216.000 (adelanto $180.000 + día olvidado de 20 L), saldo
    $36.000. Al Administrador se le sigue diciendo "use 'Corregir esta quincena'"."""
    h = auth_headers(client, "admin.a")
    crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Compras", "cand.v2.compras")
    hc = auth_headers(client, "cand.v2.compras")
    _, _, ant, liq = _corregida_sin_pagos(client, h, "Roles Corregida")
    hecho = ("No se puede modificar ni eliminar este anticipo: la quincena en la que se "
             "descontó ya emitió un comprobante corregido.")
    assert _candado(client, h, ant) == (
        f"{hecho} Si hay que arreglarle una cifra, use 'Corregir esta quincena'")
    assert _candado(client, hc, ant) == (
        f"{hecho} Si hay que arreglarle una cifra, {PIDA} que use 'Corregir esta quincena'")

    obs_admin = client.put(f"{API}/{liq}", json={"observaciones": "nota"}, headers=h)
    obs_compras = client.put(f"{API}/{liq}", json={"observaciones": "nota"}, headers=hc)
    print(f"\n  observaciones (Compras): {_detalle(obs_compras)}")
    assert (obs_admin.status_code, obs_compras.status_code) == (422, 422)
    assert _detalle(obs_admin).endswith(
        ". Si lo que está mal es una cifra, use 'Corregir esta quincena', que deja escrito el "
        "motivo y sube la versión del papel")
    assert _detalle(obs_compras).endswith(
        f". Si lo que está mal es una cifra, {PIDA} que use 'Corregir esta quincena', que "
        "deja escrito el motivo y sube la versión del papel")
    assert D(_leer(client, h, liq)["saldo"]) == D("36000")


def test_cada_permiso_recorta_solo_su_salida(client, base_datos, db_session):
    """Un rol propio puede tener uno sin el otro: Corregir pide 'administrar' y borrar un
    pago, 'eliminar'. Sobre la 'parcial' con un abono de $50.000."""
    h = auth_headers(client, "admin.a")
    _, ant_id, liq_id = _quincena(client, h, "Combinado", "30000", abonos=("50000",))
    anticipo = db_session.get(Anticipo, uuid.UUID(ant_id))
    liq = db_session.get(Liquidacion, uuid.UUID(liq_id))
    db_session.refresh(liq)
    verbo = "modificar"

    solo_eliminar = _por_que_no_se_mueve(
        anticipo, liq, verbo, ctx=_ctx(base_datos, ("liquidaciones", "eliminar")))
    assert f"{PIDA} que use 'Corregir esta quincena'" in solo_eliminar
    assert "Elimine primero ese pago solo si" in solo_eliminar

    solo_administrar = _por_que_no_se_mueve(
        anticipo, liq, verbo, ctx=_ctx(base_datos, ("liquidaciones", "administrar")))
    assert "Si la cifra está mala, use 'Corregir esta quincena'" in solo_administrar
    assert f"{PIDA} que elimine primero ese pago" in solo_administrar

    todos = _por_que_no_se_mueve(anticipo, liq, verbo, ctx=None)
    ninguno = _por_que_no_se_mueve(anticipo, liq, verbo, ctx=_ctx(base_datos))
    # La razón es la misma para todos; solo cambia el consejo.
    assert len({t.split(". ", 1)[0] for t in (todos, ninguno, solo_eliminar,
                                              solo_administrar)}) == 1
    assert len({todos, ninguno, solo_eliminar, solo_administrar}) == 4


def test_en_el_flete_corregir_no_se_nombra_y_borrar_lleva_su_advertencia(base_datos):
    """Sin base de datos: el anticipo del transportador en un flete 'parcial' con un abono.
    Corregir es solo para la leche, así que la única salida por dentro es borrar el pago."""
    liq = Liquidacion(id=uuid.uuid4(), tipo="transportador", estado="parcial", version=1,
                      valor_total=D("60000"), anticipos=D("10000"), saldo_anterior=D(0),
                      pagado=D("20000"), saldo=D("30000"))
    liq.pagos = [PagoLiquidacion(valor=D("20000"))]
    anticipo = Anticipo(liquidacion_id=liq.id)
    texto = _por_que_no_se_mueve(anticipo, liq, "modificar", ctx=None)
    assert texto == (
        "No se puede modificar este anticipo: la liquidación en la que se descontó ya tiene "
        "un pago registrado. Elimine primero ese pago si de verdad hay que corregirlo —con "
        "él se van sus soportes, que no se recuperan—, o registre el ajuste en la quincena "
        "siguiente")
    assert "Corregir" not in texto
    sin_eliminar = _por_que_no_se_mueve(anticipo, liq, "modificar", ctx=_ctx(base_datos))
    assert f"{PIDA} que elimine primero ese pago" in sin_eliminar
