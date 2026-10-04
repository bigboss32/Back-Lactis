"""EL DÍA Y EL ADELANTO DE LA MISMA QUINCENA DAN EL MISMO CONSEJO, PARA CADA ROL.

El candado del adelanto (`_por_que_no_se_mueve` → `_consejo_del_candado`) y el 422 del día
en Recepción diaria (`_por_que_esta_trabada` → `_consejo_del_abono`) son dos puertas a la
misma quincena trabada por sus abonos. Esta ronda los dejó con el mismo orden (Corregir si
el botón la acepta, el ajuste, y de último borrar los pagos), con dos diferencias:

  · EL FLETE CON UN ABONO. Corregir no recibe el flete, así que borrar el pago es la única
    salida por dentro. Medido con el flete de Stella, 100 L × $100 = $10.000, adelanto de
    $2.000 y un abono de $5.000 ('parcial', saldo 10.000 − 2.000 − 5.000 = $3.000): el
    candado del adelanto decía "Elimine primero ese pago si de verdad hay que corregirlo
    —con él se van sus soportes, que no se recuperan—"; el PUT de los litros de ese mismo
    día, la misma salida SIN la advertencia. Se seguía el del día y el soporte del pago se
    iba sin que nadie lo hubiera dicho.
  · CUÁNDO SE OFRECE BORRAR. El adelanto pregunta `_pagos_que_se_pueden_borrar` (borrarlos
    todos deja la quincena sin pagos); el día contaba renglones. Ahora el día importa la
    misma pregunta.

Regla de oro en cada fila que se lee: neto = valor_total − anticipos − saldo_anterior;
saldo = neto − pagado.
"""
import uuid
from decimal import Decimal

from app.modules.liquidaciones.models import Anticipo, Liquidacion, PagoLiquidacion
from app.modules.liquidaciones.service import _por_que_no_se_mueve
from app.modules.recepcion.service import _por_que_esta_trabada
from tests.conftest import auth_headers
from tests.test_liquidacion_corregir_permisos import crear_usuario_con_rol

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
PIDA = "pídale a un Administrador de la empresa"
SOPORTES = "—con él se van sus soportes, que no se recuperan—, o registre el ajuste en la quincena siguiente"


def D(v):
    return Decimal(str(v))


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json() if r.content else None


def _detalle(r):
    return r.json()["error"]["detail"]


def _leer(client, h, liq):
    fila = _ok(client.get(f"{API}/{liq}", headers=h))
    neto = D(fila["valor_total"]) - D(fila["anticipos"]) - D(fila["saldo_anterior"] or 0)
    assert D(fila["neto_a_pagar"]) == neto and D(fila["saldo"]) == neto - D(fila["pagado"])
    return fila


def _candado(client, h, ant):
    uno = _ok(client.get(f"{ANT}/{ant}", headers=h))
    assert uno["bloqueado"], uno
    return uno["candado_aviso"]


def _flete_con_un_abono(client, h):
    """Stella lleva 100 L de Beto el 02/06 a $100: flete $10.000, adelanto $2.000, abono
    $5.000. Solo se genera el flete: la leche de ese día no traba nada."""
    stella = _ok(client.post(f"{V}/transportadores", json={
        "nombre": "Stella Abono", "valor_transporte": "100"}, headers=h), 201)["id"]
    beto = _ok(client.post(f"{V}/proveedores", json={
        "nombre": "Beto Abono", "vereda": "X", "precio_litro": "1800"}, headers=h), 201)["id"]
    dia = _ok(client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": beto,
                                     "transportador_id": stella, "cantidad_litros": "100"},
                          headers=h), 201)["id"]
    ant = _ok(client.post(ANT, json={"tipo": "transportador", "transportador_id": stella,
                                     "fecha": "2026-06-01", "valor": "2000"}, headers=h),
              201)["id"]
    generadas = _ok(client.post(f"{API}/generar", json={
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15",
        "tipo": "transportador"}, headers=h))["generadas"]
    flete = next(x for x in generadas if x["transportador_id"] == stella)["id"]
    _ok(client.post(f"{API}/{flete}/aprobar", headers=h))
    _ok(client.post(f"{API}/{flete}/pagos", json={"fecha": "2026-06-20", "valor": "5000"},
                    headers=h))
    fila = _leer(client, h, flete)
    assert (fila["estado"], D(fila["valor_total"]), D(fila["anticipos"]), D(fila["pagado"]),
            D(fila["saldo"]), len(fila["pagos"])) == (
        "parcial", D(10000), D(2000), D(5000), D(3000), 1)
    return dia, ant, flete


# ---------------------------------------------------------------------------------------
def test_el_flete_con_un_abono_avisa_los_soportes_en_el_dia_y_en_el_adelanto(
    client, base_datos
):
    h = auth_headers(client, "admin.a")
    dia, ant, flete = _flete_con_un_abono(client, h)

    adelanto = _candado(client, h, ant)
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    print(f"\n  adelanto: {adelanto}\n  día: {_detalle(put)}")
    assert put.status_code == 422
    assert adelanto == (
        "No se puede modificar ni eliminar este anticipo: la liquidación en la que se "
        f"descontó ya tiene un pago registrado. Elimine primero ese pago si de verdad hay que "
        f"corregirlo {SOPORTES}")
    assert _detalle(put).startswith(
        "No se puede cambiar los litros de este día: el flete ya tiene un pago registrado "
        "en una liquidación.")
    assert _detalle(put).endswith(
        f"Elimine primero ese pago si de verdad hay que corregir la cifra {SOPORTES}")
    # Ninguno de los dos nombra Corregir: sobre el flete rebota siempre.
    assert "Corregir" not in adelanto and "Corregir" not in _detalle(put)

    # SE SIGUE EL CONSEJO, con la advertencia ya dicha: se borra el pago y el día se suelta.
    pago = _leer(client, h, flete)["pagos"][0]["id"]
    _ok(client.delete(f"{API}/{flete}/pagos/{pago}", headers=h))
    _ok(client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h))
    despues = _leer(client, h, flete)
    # 90 L × $100 = $9.000 − $2.000 de adelanto = $7.000, sin pagos.
    assert (D(despues["valor_total"]), D(despues["pagado"]), D(despues["saldo"])) == (
        D(9000), D(0), D(7000))


def test_a_compras_los_dos_le_dicen_a_quien_pedirselo(client, base_datos, db_session):
    """Compras corrige días ('recepcion:editar') y ve el candado de Anticipos, pero no puede
    borrar un pago: los dos textos le dicen quién lo hace, con la misma advertencia."""
    h = auth_headers(client, "admin.a")
    crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Compras", "igual.compras")
    hc = auth_headers(client, "igual.compras")
    dia, ant, flete = _flete_con_un_abono(client, h)

    adelanto = _candado(client, hc, ant)
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=hc)
    assert put.status_code == 422
    pedazo = f"{PIDA} que elimine primero ese pago {SOPORTES}"
    assert adelanto.endswith(f"Si de verdad hay que corregirlo, {pedazo}")
    assert _detalle(put).endswith(f"Si de verdad hay que corregir la cifra, {pedazo}")
    assert "Elimine primero" not in adelanto and "Elimine primero" not in _detalle(put)
    # Lo que no se le nombra, medido: el botón le contesta 403.
    pago = _leer(client, h, flete)["pagos"][0]["id"]
    assert client.delete(f"{API}/{flete}/pagos/{pago}", headers=hc).status_code == 403
    assert D(_leer(client, h, flete)["saldo"]) == D(3000)


def test_sin_renglones_que_borrar_ninguno_de_los_dos_manda_a_borrar(base_datos):
    """Sin base de datos: un flete 'parcial' v1 con $5.000 pagados y un solo renglón de
    $2.000. Borrar ese renglón deja $3.000 pagados y la quincena sigue trabada: el adelanto
    ya no lo ofrecía (`_pagos_que_se_pueden_borrar` da cero) y el día decía "Elimine primero
    ese pago". Ahora los dos dan el ajuste, y el día no promete que el paso lo suelta."""
    liq = Liquidacion(id=uuid.uuid4(), tipo="transportador", estado="parcial", version=1,
                      valor_total=D("10000"), anticipos=D("2000"), saldo_anterior=D(0),
                      pagado=D("5000"), saldo=D("3000"))
    liq.pagos = [PagoLiquidacion(valor=D("2000"))]
    ajuste = "Si la cifra está mala, registre el ajuste en la quincena siguiente"

    razon = _por_que_esta_trabada(liq, None)
    adelanto = _por_que_no_se_mueve(Anticipo(liquidacion_id=liq.id), liq, "modificar")
    assert razon.consejo == ajuste and not razon.se_suelta
    assert adelanto.endswith(ajuste) and "Elimine" not in adelanto
