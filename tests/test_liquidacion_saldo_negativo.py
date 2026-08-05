"""CUANDO EL SALDO QUEDA POR DEBAJO DE CERO, EL SISTEMA LO DICE CON PALABRAS.

Un saldo negativo quiere decir UNA sola cosa: los anticipos que ya se le entregaron
al tercero suman más que lo que produjo la quincena, o sea que ÉL le quedó debiendo
al negocio. Es la verdad de la cuenta y hay que mostrarla, no esconderla.

POR QUÉ ESTAS PRUEBAS EXISTEN, con las cifras del caso que las obligó: hubo un
intento de "arreglar" el saldo negativo soltando el anticipo que no cupiera en la
quincena (dejarlo sin aplicar para descontárselo a la siguiente). Eso hacía SALIR LA
PLATA DOS VECES:

  Henri C, 100 L a $1.800 = $180.000 de quincena, con un anticipo de $300.000 QUE YA
  SE LE ENTREGÓ. El comprobante salía con "Anticipos aplicados $0,00" y SALDO A PAGAR
  $180.000, y el dueño le pagaba esos $180.000 ENCIMA de los $300.000 de la semana
  anterior. $300.000 perdidos y ni un renglón que lo explicara.

Así que los anticipos se aplican COMPLETOS, siempre, y lo que se arregla es LA
LECTURA: donde el papel decía "SALDO A PAGAR -$120.000,00" —que en un renglón
destacado se lee como si hubiera que pagarlo— ahora dice "LE QUEDA DEBIENDO
$120.000,00", y la API expone la misma cifra en positivo (`le_queda_debiendo`) para
que la pantalla pueda decirlo como lo diría el dueño: "Henri le queda debiendo
$120.000,00".
"""
import io
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
ANT = "/api/v1/anticipos"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


CERO = D(0)


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="1800"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ruta(client, h, nombre):
    r = client.post(
        "/api/v1/rutas", json={"nombre": nombre, "municipio": "Granada"}, headers=h
    )
    assert r.status_code == 201, r.text
    return r.json()


def _transportador(client, h, nombre, rutas):
    r = client.post(
        "/api/v1/transportadores",
        json={
            "nombre": nombre,
            "valor_transporte": "0",
            "rutas": [{"ruta_id": ru["id"], "valor_transporte": str(v)} for ru, v in rutas],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _tarifa(client, h, t, pares):
    r = client.put(
        f"/api/v1/transportadores/{t['id']}",
        json={"rutas": [{"ruta_id": ru["id"], "valor_transporte": str(v)} for ru, v in pares]},
        headers=h,
    )
    assert r.status_code == 200, r.text


def _recepcion(client, h, prov, fecha, litros, t=None, ruta=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)}
    if t:
        cuerpo["transportador_id"] = t["id"]
    if ruta:
        cuerpo["ruta_id"] = ruta["id"]
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, fecha, valor, *, proveedor=None, transportador=None):
    cuerpo = {"fecha": fecha, "valor": str(valor)}
    if proveedor:
        cuerpo.update(tipo="proveedor", proveedor_id=proveedor["id"])
    else:
        cuerpo.update(tipo="transportador", transportador_id=transportador["id"])
    r = client.post(ANT, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, tipo="proveedor", inicio="2026-06-01", fin="2026-06-15"):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def texto_pdf(contenido):
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    return texto_pdf(r.content)


# ===========================================================================
# 1. EL ANTICIPO MÁS GRANDE QUE LA QUINCENA SE APLICA COMPLETO
# ===========================================================================
def test_el_anticipo_mas_grande_que_la_quincena_se_aplica_completo(client, base_datos):
    """El caso que costaba $300.000: no se puede soltar el anticipo que no cabe.

    Si el anticipo se suelta, el comprobante sale con "Anticipos aplicados $0,00" y
    el dueño paga la quincena ENTERA encima de la plata que ya entregó. Se aplica
    completo, el saldo queda negativo, y el negativo se explica.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")  # 100 L x $1.800 = $180.000
    ant = _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)

    liq = _generar(client, h)[0]
    print("\n===== 1. EL ANTICIPO DE $300.000 CONTRA UNA QUINCENA DE $180.000 =====")
    print(f"  valor_total={liq['valor_total']}  anticipos={liq['anticipos']}  "
          f"neto={liq['neto_a_pagar']}  saldo={liq['saldo']}  "
          f"le_queda_debiendo={liq['le_queda_debiendo']}")

    assert D(liq["valor_total"]) == D("180000")
    assert D(liq["anticipos"]) == D("300000"), (
        "el anticipo que no cabe NO se puede soltar: es plata que ya se entregó y "
        "dejarla sin aplicar hace que el dueño la pague dos veces"
    )
    assert D(liq["neto_a_pagar"]) == D("-120000")
    assert D(liq["saldo"]) == D("-120000")
    # Y LA CIFRA CON LA QUE SE LEE: la misma, en positivo y con su nombre.
    assert D(liq["le_queda_debiendo"]) == D("120000")

    # El anticipo quedó APLICADO contra esta liquidación, no suelto esperando otra.
    guardado = client.get(f"{ANT}/{ant['id']}", headers=h).json()
    print(f"  el anticipo quedó · aplicado={guardado['aplicado']} · "
          f"liquidacion_id={guardado['liquidacion_id'] == liq['id']}")
    assert guardado["aplicado"] is True
    assert guardado["liquidacion_id"] == liq["id"]


def test_el_comprobante_dice_LE_QUEDA_DEBIENDO_y_no_un_saldo_negativo(client, base_datos):
    """El papel. El renglón destacado no puede decir "SALDO A PAGAR -$120.000,00".

    Es el renglón que el proveedor firma y el que el dueño lee para saber cuánto
    entregar: un menos pegado a un total destacado es justo lo que se lee mal.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    liq = _generar(client, h)[0]

    papel = _pdf(client, h, liq["id"])
    print("\n===== 2. EL COMPROBANTE =====")
    print(f"  ...{papel[papel.find('VALOR TOTAL'):][:90]}...")
    assert "LE QUEDA DEBIENDO" in papel, (
        f"el comprobante no dice quién le debe a quién:\n{papel}"
    )
    assert "$120.000" in papel
    assert "SALDO A PAGAR" not in papel, (
        "el papel sigue diciendo 'SALDO A PAGAR' sobre una cifra que nadie va a pagar"
    )
    # El renglón de anticipos sigue diciendo la verdad: se aplicaron los $300.000.
    assert "$300.000" in papel


def test_pagar_una_liquidacion_con_saldo_negativo_rebota_y_la_deja_en_aprobada(
    client, base_datos
):
    """El botón "Pagar" REBOTA cuando el tercero es el que quedó debiendo.

    POR QUÉ ESTA PRUEBA CAMBIÓ DE EXPECTATIVA. Antes exigía que "Pagar" devolviera 200
    y dejara la liquidación en 'pagada' con pagado $0,00 —lo importante era que no le
    saliera plata—, y eso se conserva: no sale un peso. Lo que se corrigió es el
    ESTADO, y son tres defectos de uno:

      · el papel y la pantalla decían "PAGADA" al lado de "LE QUEDA DEBIENDO
        $120.000". Las dos cosas no pueden ser ciertas a la vez;
      · 'pagada' TRABA los días de esa quincena en Recepción diaria, así que un litro
        mal anotado quedaba imposible de corregir para siempre sin que hubiera salido
        plata contra esas cifras;
      · y de 'pagada' no se puede anular, o sea que tampoco quedaba la salida de
        rehacer la quincena.

    Se queda en APROBADA, que es lo que de verdad es: unas cifras en firme por las que
    no hay que entregar plata. La quincena se cierra cuando su deuda SE COBRA en la
    siguiente (ver tests/test_liquidacion_saldo_anterior.py), y ese es el momento en
    que sus días quedan trabados.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    liq = _generar(client, h)[0]

    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    r = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    print("\n===== 3. PAGAR =====")
    print(f"  respuesta={r.status_code} · {r.json()['error']['detail']}")
    assert r.status_code == 422, r.text
    assert "le quedó debiendo" in r.json()["error"]["detail"]

    despues = _leer(client, h, liq["id"])
    print(f"  estado={despues['estado']}  pagado={despues['pagado']}  "
          f"saldo={despues['saldo']}  le_queda_debiendo={despues['le_queda_debiendo']}")
    assert despues["estado"] == "aprobada", (
        "quedó marcada pagada sin que saliera un peso: eso traba los días de la "
        "quincena para siempre y de 'pagada' no se puede anular"
    )
    assert D(despues["pagado"]) == CERO, "no se le debía nada y salió plata"
    assert D(despues["saldo"]) == D("-120000")
    assert D(despues["le_queda_debiendo"]) == D("120000")

    # Un abono contra ella rebota: no hay saldo pendiente que abonar.
    r = client.post(
        f"{API}/{liq['id']}/pagos", json={"fecha": "2026-06-16", "valor": "1000"}, headers=h
    )
    assert r.status_code == 422, r.text


# ===========================================================================
# 2. EL CASO DEL FLETE: la tarifa corregida deja la quincena por debajo del anticipo
# ===========================================================================
def test_la_tarifa_corregida_puede_dejar_al_transportador_debiendo(client, base_datos):
    """El caso medido, con las cifras del dueño:

      44,23 L en Nápoles a $242,76 = $10.737,27, con un anticipo de $5.000 ya
      entregado -> saldo $5.737,27 a favor del transportador.

      Se descubre que la tarifa estaba mal y se corrige a $1: el flete cae a $44,23
      y el anticipo de $5.000 ya no cabe. Saldo -$4.955,77, o sea: ALEX LE QUEDA
      DEBIENDO $4.955,77.

    El recálculo NO se rebota y el anticipo NO se suelta: la tarifa corregida es un
    dato bueno, y la deuda del transportador es el resultado honesto de corregirla.
    """
    h = auth_headers(client, "admin.a")
    napoles = _ruta(client, h, "Napoles")
    alex = _transportador(client, h, "Alex", [(napoles, "242.76")])
    prov = _proveedor(client, h, "Patricia")
    _recepcion(client, h, prov, "2026-06-02", "44.23", t=alex, ruta=napoles)
    _anticipo(client, h, "2026-06-01", "5000", transportador=alex)

    liq = _generar(client, h, tipo="transportador")[0]
    print("\n===== 4. LA TARIFA MAL TECLEADA =====")
    print(f"  con $242,76 · flete={liq['valor_transporte']} anticipos={liq['anticipos']} "
          f"saldo={liq['saldo']}")
    assert D(liq["valor_transporte"]) == centavos(D("44.23") * D("242.76"))
    assert D(liq["valor_transporte"]) == D("10737.27")
    assert D(liq["saldo"]) == D("5737.27")
    assert D(liq["le_queda_debiendo"]) == CERO

    # Se corrige la tarifa y se oprime Recalcular (el botón del dueño).
    _tarifa(client, h, alex, [(napoles, "1")])
    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    assert r.status_code == 200, r.text
    despues = _leer(client, h, liq["id"])
    print(f"  con $1,00    · flete={despues['valor_transporte']} "
          f"anticipos={despues['anticipos']} saldo={despues['saldo']} "
          f"le_queda_debiendo={despues['le_queda_debiendo']}")

    assert D(despues["valor_transporte"]) == D("44.23")
    assert D(despues["anticipos"]) == D("5000"), (
        "el anticipo se soltó al recalcular: esos $5.000 ya se le entregaron"
    )
    assert D(despues["saldo"]) == D("-4955.77")
    assert D(despues["le_queda_debiendo"]) == D("4955.77")
    # La igualdad que el dueño verifica a mano sigue exacta.
    assert D(despues["saldo"]) == D(despues["neto_a_pagar"]) - D(despues["pagado"])
    assert D(despues["neto_a_pagar"]) == D(despues["valor_total"]) - D(despues["anticipos"])

    papel = _pdf(client, h, liq["id"])
    assert "LE QUEDA DEBIENDO" in papel, papel
    assert "$4.955,77" in papel, papel


# ===========================================================================
# 3. LO NORMAL NO CAMBIA
# ===========================================================================
def test_la_liquidacion_normal_sigue_diciendo_SALDO_A_PAGAR(client, base_datos):
    """El caso de todos los días: el rótulo y la cifra de siempre, sin tocar nada.

    Si esta prueba falla, el arreglo del saldo negativo se llevó por delante el
    comprobante que sale el 99% de las veces.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri")
    _recepcion(client, h, henri, "2026-06-02", "100")  # $180.000
    _anticipo(client, h, "2026-06-01", "50000", proveedor=henri)
    liq = _generar(client, h)[0]

    print("\n===== 5. LA LIQUIDACIÓN NORMAL =====")
    print(f"  valor_total={liq['valor_total']} anticipos={liq['anticipos']} "
          f"saldo={liq['saldo']} le_queda_debiendo={liq['le_queda_debiendo']}")
    assert D(liq["saldo"]) == D("130000")
    assert D(liq["le_queda_debiendo"]) == CERO, (
        "una liquidación que SÍ hay que pagar no puede decir que el proveedor debe"
    )
    papel = _pdf(client, h, liq["id"])
    assert "SALDO A PAGAR" in papel
    assert "LE QUEDA DEBIENDO" not in papel
    assert "$130.000" in papel


def test_el_saldo_en_cero_no_es_una_deuda_del_tercero(client, base_datos):
    """El borde exacto: anticipo IGUAL a la quincena. Saldo $0, y nadie le debe nada.

    Se prueba porque el corte es `saldo < 0` y un `<=` mal puesto haría que una
    liquidación saldada le dijera al dueño que el proveedor le queda debiendo $0.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri")
    _recepcion(client, h, henri, "2026-06-02", "100")  # $180.000
    _anticipo(client, h, "2026-06-01", "180000", proveedor=henri)
    liq = _generar(client, h)[0]

    print("\n===== 6. EL BORDE: ANTICIPO IGUAL A LA QUINCENA =====")
    print(f"  saldo={liq['saldo']} le_queda_debiendo={liq['le_queda_debiendo']}")
    assert D(liq["saldo"]) == CERO
    assert D(liq["le_queda_debiendo"]) == CERO
    papel = _pdf(client, h, liq["id"])
    assert "LE QUEDA DEBIENDO" not in papel
    assert "SALDO A PAGAR" in papel
