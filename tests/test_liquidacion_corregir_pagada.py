"""CORREGIR UNA QUINCENA QUE YA SE PAGÓ, MEDIDA AL CENTAVO Y LEÍDA EN EL PAPEL.

Lo pidió el dueño con estas palabras: "que si soy administrador de empresa pueda editar
la liquidación que ya está pagada, es que se le olvidó un detalle y tiene que editarla".
Y entre las dos formas de hacerlo escogió explícitamente la de UN SOLO COMPROBANTE
CORREGIDO (la v2 del mismo folio) en vez de dos papeles sueltos del mismo período.

Este es el archivo principal de esa función: el que se abre para entender qué hace y
para confiar en las cifras. Mide LOS DOS ESCENARIOS DE PLATA que el dueño describió, con
sus cifras, y después LEE EL PAPEL renglón por renglón —que es lo que él suma con
calculadora—:

  A) SUBE. Quincena de $500.000 pagada con $500.000. Se le había olvidado anotar un día
     de $180.000. Al corregirla: valor total $680.000, PAGADO SIGUE EN $500.000 (no se
     borra ni se toca un solo pago), saldo $180.000, estado 'parcial', versión 2. Y
     después se oprime Pagar y cierra en 'pagada' con saldo $0 — y el pago viejo y su
     soporte (la foto de la transferencia) siguen ahí.

  B) BAJA. El precio de un día estaba mal tecleado y el total baja a $400.000 contra
     $500.000 que ya se le entregaron. Saldo −$100.000, le_queda_debiendo $100.000,
     estado 'pagada' (que es la verdad: salió toda la plata, y $100.000 de más). Y la
     QUINCENA SIGUIENTE se lo descuenta sola —una sola vez— por el mecanismo que ya
     existía (`_solo_las_que_deben` / `saldo_anterior`).

LA REGLA DE LA CASA, que manda sobre todo lo demás: TODO DESGLOSE SUMA EXACTO LA CIFRA
GRANDE, porque el dueño lo verifica a mano. En particular tiene que cumplirse SIEMPRE,
antes y después de corregir:

    neto_a_pagar = pagado + saldo

TODAS LAS CIFRAS DE ESTE ARCHIVO ESTÁN CALCULADAS A MANO en el docstring de cada prueba,
no con el mismo código que se está probando. Los números se escogieron redondos a
propósito (250 L a $2.000 = $500.000) para que cualquiera pueda rehacer la cuenta en una
servilleta y ver si el papel miente.

EL PAPEL SE LEE, NO SE SUPONE: las pruebas del comprobante extraen el texto del PDF y
suman la columna de arriba abajo, igual que en tests/test_liquidacion_saldo_anterior.py,
que es el molde. Cuatro de estas pruebas nacieron en rojo —la banda de CORREGIDO en el
encabezado, las dos horas del papel en hora de Colombia, y el renglón que llamaba "pago
total" a un abono parcial— y hoy están en verde: el papel dice las cuatro cosas.
"""
import io
import re
from decimal import Decimal

import pytest
from pypdf import PdfReader

from tests.ayudas_imagenes import JPEG
from tests.ayudas_r2 import enchufar
from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"

Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
Q3 = ("2026-07-01", "2026-07-15")


def D(v):
    return Decimal(str(v))


CERO = D(0)


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre="Henri C", precio="2000"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros, *, precio=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)}
    if precio is not None:
        cuerpo["precio_litro"] = str(precio)
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, periodo):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _de(generadas, proveedor):
    encontradas = [x for x in generadas if x["proveedor_id"] == proveedor["id"]]
    assert len(encontradas) == 1, f"se esperaba una sola de ese proveedor: {generadas}"
    return encontradas[0]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _aprobar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _pagar(client, h, liq_id):
    r = client.post(f"{API}/{liq_id}/pagar", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _quincena_pagada(client, h, proveedor, *, fecha="2026-06-02", litros="250"):
    """Un día anotado, la quincena generada, aprobada y PAGADA de un solo golpe.

    Es el punto de partida de los dos escenarios: el papel ya salió y la plata ya se
    entregó. Todo lo que esta función deja montado es lo que el productor tiene en la
    mano cuando el dueño se da cuenta del error.
    """
    _recepcion(client, h, proveedor, fecha, litros)
    liq = _de(_generar(client, h, Q1), proveedor)
    _aprobar(client, h, liq["id"])
    return _pagar(client, h, liq["id"])


def _previsualizar(client, h, liq_id, cuerpo):
    r = client.post(f"{API}/{liq_id}/corregir/previsualizar", json=cuerpo, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _corregir(client, h, liq_id, cuerpo):
    r = client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _correcciones(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/correcciones", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------- el papel
def texto_pdf(contenido):
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    return texto_pdf(r.content)


# El renglón del resumen del comprobante, CON SU SIGNO: "- $500.000,00" vale -500000.00.
# Se lee del papel impreso y no de la API a propósito: lo que el dueño suma a mano son
# los caracteres que salieron en la hoja. Copiado del molde
# (tests/test_liquidacion_saldo_anterior.py) para que las dos pruebas lean el mismo papel
# de la misma manera.
_CIFRA = re.compile(r"(-?)\s*\$\s*(-?)([\d.]+(?:,\d{2})?)")


def renglon(papel, rotulo):
    inicio = papel.find(rotulo)
    assert inicio >= 0, f"el comprobante no trae el renglón «{rotulo}»:\n{papel}"
    resto = papel[inicio + len(rotulo):]
    encontrado = _CIFRA.search(resto)
    assert encontrado, f"el renglón «{rotulo}» salió sin cifra:\n{resto[:120]}"
    signo, signo_interno, cifra = encontrado.groups()
    valor = D(cifra.replace(".", "").replace(",", "."))
    return -valor if (signo == "-" or signo_interno == "-") else valor


def folio_v1(liq_id):
    """El N.º con que se nombró el papel que el productor ya tiene guardado."""
    return liq_id[:8].upper()


# ===========================================================================
# ESCENARIO A — SUBE: el día que se le olvidó anotar
# ===========================================================================
def test_escenario_a_el_dia_olvidado_sube_el_total_y_no_toca_un_peso_de_lo_ya_pagado(
    client, base_datos
):
    """Las cifras del dueño, calculadas a mano:

    ANTES (el papel que el productor tiene en la mano, v1):
        02/06 · 250,00 L a $2.000,00           $500.000
        VALOR TOTAL                            $500.000
        pagado                                 $500.000   <- ya salió de la caja
        saldo                                        $0   -> estado 'pagada'

    SE LE OLVIDÓ EL DÍA DEL 12/06: 90,00 L a $2.000,00 = $180.000.

    DESPUÉS (la v2 del mismo comprobante):
        02/06                                  $500.000
        12/06                                  $180.000
        VALOR TOTAL                            $680.000
        anticipos                                    $0
        neto a pagar                           $680.000
        PAGADO (NO SE MOVIÓ)                  -$500.000
        saldo                                  $180.000   -> estado 'parcial'

    Y la regla de la casa, que el dueño comprueba con calculadora:
        neto $680.000 = pagado $500.000 + saldo $180.000. Exacto, sin un centavo suelto.

    LO QUE ESTA PRUEBA VIGILA DE VERDAD es la línea `pagado`. Corregir hacia arriba una
    quincena pagada es la operación en la que sería fácil "volver a empezar" el
    comprobante; si eso pasara, `pagado` volvería a $0 y el sistema le pediría al dueño
    entregar otra vez los $500.000 que ya entregó.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    pagada = _quincena_pagada(client, h, henri)

    print("\n===== ESCENARIO A · ANTES (v1) =====")
    print(f"  valor_total={pagada['valor_total']} neto={pagada['neto_a_pagar']} "
          f"pagado={pagada['pagado']} saldo={pagada['saldo']} "
          f"estado={pagada['estado']} version={pagada['version']}")
    assert D(pagada["valor_total"]) == D("500000")
    assert D(pagada["neto_a_pagar"]) == D("500000")
    assert D(pagada["pagado"]) == D("500000")
    assert D(pagada["saldo"]) == CERO
    assert pagada["estado"] == "pagada"
    assert pagada["version"] == 1, "un comprobante que nunca se corrigió es la v1"
    assert D(pagada["neto_a_pagar"]) == D(pagada["pagado"]) + D(pagada["saldo"])

    # EL DÍA OLVIDADO. Se anota después de que la quincena ya se pagó, que es
    # exactamente lo que pasó en la finca: el productor trajo la planilla tarde.
    _recepcion(client, h, henri, "2026-06-12", "90")

    # (1) LA CALCULADORA EN LA PANTALLA, antes de que se mueva un peso.
    vista = _previsualizar(client, h, pagada["id"], {"motivo": "se anotó tarde el 12/06"})
    print("  días sueltos ofrecidos:",
          [(d["fecha"], d["valor"], d["nota_flete"]) for d in vista["dias_sueltos"]])
    assert len(vista["dias_sueltos"]) == 1, (
        "el día del 12/06 tiene que aparecer como candidato: es el único del período "
        "que no está en ningún comprobante"
    )
    dia = vista["dias_sueltos"][0]
    assert dia["fecha"] == "2026-06-12"
    assert D(dia["valor"]) == D("180000"), "90 L x $2.000 = $180.000"

    previa = _previsualizar(
        client,
        h,
        pagada["id"],
        {"motivo": "se anotó tarde el 12/06", "recepciones_a_incluir": [dia["recepcion_id"]]},
    )
    print(f"  PREVIA · total {previa['valor_total_antes']} -> {previa['valor_total_despues']} · "
          f"saldo {previa['saldo_antes']} -> {previa['saldo_despues']} · "
          f"estado {previa['estado_antes']} -> {previa['estado_despues']}")
    assert D(previa["valor_total_antes"]) == D("500000")
    assert D(previa["valor_total_despues"]) == D("680000")
    assert D(previa["pagado"]) == D("500000")
    assert D(previa["saldo_despues"]) == D("180000")
    assert D(previa["queda_por_entregar"]) == D("180000")
    assert D(previa["se_le_pago_de_mas"]) == CERO
    assert previa["estado_despues"] == "parcial"

    # (2) LA CORRECCIÓN DE VERDAD. Y tiene que escribir EXACTAMENTE lo que mostró el
    # diálogo: si la pantalla dijera $680.000 y el botón escribiera otra cosa, el dueño
    # lo descubriría con la calculadora cuando ya fuera tarde.
    corregida = _corregir(
        client,
        h,
        pagada["id"],
        {"motivo": "se anotó tarde el 12/06", "recepciones_a_incluir": [dia["recepcion_id"]]},
    )
    print("===== ESCENARIO A · DESPUÉS (v2) =====")
    print(f"  valor_total={corregida['valor_total']} neto={corregida['neto_a_pagar']} "
          f"pagado={corregida['pagado']} saldo={corregida['saldo']} "
          f"estado={corregida['estado']} version={corregida['version']}")

    assert D(corregida["valor_total"]) == D("680000"), "$500.000 + $180.000"
    assert D(corregida["valor_bruto"]) == D("680000")
    assert D(corregida["total_litros"]) == D("340.00"), "250 L + 90 L"
    assert D(corregida["anticipos"]) == CERO
    assert D(corregida["saldo_anterior"]) == CERO
    assert D(corregida["neto_a_pagar"]) == D("680000")
    assert D(corregida["pagado"]) == D("500000"), (
        "LOS $500.000 YA ENTREGADOS NO SE PUEDEN MOVER: si esto vuelve a cero, el "
        "sistema le está pidiendo al dueño que pague dos veces la misma quincena"
    )
    assert D(corregida["saldo"]) == D("180000")
    assert D(corregida["le_queda_debiendo"]) == CERO, "acá debe el negocio, no el productor"
    assert corregida["estado"] == "parcial"
    assert corregida["version"] == 2, "el folio del papel nuevo tiene que llamarse distinto"

    # LA REGLA DE LA CASA, medida sobre las cifras que devolvió el servidor.
    assert D(corregida["neto_a_pagar"]) == D(corregida["pagado"]) + D(corregida["saldo"]), (
        "neto = pagado + saldo tiene que cuadrar al centavo también después de corregir"
    )

    # Y EL DESGLOSE DE LOS DÍAS SUMA EL VALOR TOTAL: el dueño suma la columna Valor.
    dias = [d for d in corregida["detalles"] if d.get("deleted_at") is None]
    suma_dias = sum((D(d["valor"]) for d in dias), CERO)
    print("  días del comprobante:", [(d["fecha"], d["valor"]) for d in dias])
    assert len(dias) == 2
    assert suma_dias == D("680000"), (
        f"la columna Valor suma {suma_dias} y el VALOR TOTAL dice "
        f"{corregida['valor_total']}: el papel no cuadraría"
    )

    # (3) EL RENGLÓN DE CORRECCIÓN: la memoria de por qué el papel viejo dice otra cifra.
    correcciones = _correcciones(client, h, pagada["id"])
    assert len(correcciones) == 1
    c = correcciones[0]
    print(f"  corrección · v{c['version_nueva']} · «{c['motivo']}» · "
          f"{c['valor_total_antes']} -> {c['valor_total_despues']} · "
          f"pagado al momento {c['pagado_al_momento']}")
    assert c["version_nueva"] == 2
    assert c["motivo"] == "se anotó tarde el 12/06"
    assert D(c["valor_total_antes"]) == D("500000")
    assert D(c["valor_total_despues"]) == D("680000")
    assert D(c["pagado_al_momento"]) == D("500000")
    assert D(c["saldo_antes"]) == CERO
    assert D(c["saldo_despues"]) == D("180000")
    assert c["estado_antes"] == "pagada"
    assert c["estado_despues"] == "parcial"
    assert len(c["dias_agregados"]) == 1
    assert c["dias_agregados"][0]["fecha"] == "2026-06-12"
    assert D(c["dias_agregados"][0]["valor"]) == D("180000")
    # Y LA DIFERENCIA DE LAS DOS CIFRAS GRANDES ES EXACTAMENTE EL DÍA QUE ENTRÓ: si no
    # lo fuera, la corrección movió algo más de lo que dice que movió.
    assert D(c["valor_total_despues"]) - D(c["valor_total_antes"]) == D(
        c["dias_agregados"][0]["valor"]
    )


def test_escenario_a_despues_de_corregir_se_oprime_pagar_y_el_pago_viejo_sigue_ahi(
    client, base_datos, monkeypatch
):
    """Se corrigió hacia arriba y quedaron $180.000 por entregar. Se oprime Pagar.

    Las cifras, a mano:
        neto $680.000 − pagado $500.000 = saldo $180.000  (antes de oprimir Pagar)
        se entregan los $180.000 que faltaban
        pagado $500.000 + $180.000 = $680.000 ; saldo $0 -> 'pagada'

    Y LO QUE NO PUEDE PASAR, que es la mitad de esta prueba: el pago viejo de $500.000
    Y SU SOPORTE —la foto de la transferencia que el dueño le mandó al productor por
    WhatsApp— tienen que seguir ahí después de corregir. Un soporte de pago que
    desaparece es la prueba de una entrega de plata que se borró sola; el día que un
    productor reclame, no habría con qué responderle.

    Al final quedan DOS pagos que suman la cifra grande:
        $500.000 + $180.000 = $680.000 = VALOR TOTAL.
    """
    import app.modules.liquidaciones.service as servicio

    r2 = enchufar(monkeypatch, servicio)

    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    pagada = _quincena_pagada(client, h, henri)

    # LA FOTO DE LA TRANSFERENCIA, colgada del pago de $500.000 que ya se hizo.
    pago_viejo = pagada["pagos"][0]
    assert D(pago_viejo["valor"]) == D("500000")
    subida = client.post(
        f"{API}/{pagada['id']}/pagos/{pago_viejo['id']}/adjuntos",
        files=[("files", ("transferencia.jpg", JPEG, "image/jpeg"))],
        headers=h,
    )
    assert subida.status_code == 201, subida.text
    assert len(r2.objetos) == 1, "el soporte tiene que haber quedado en el bucket"

    _recepcion(client, h, henri, "2026-06-12", "90")
    vista = _previsualizar(client, h, pagada["id"], {"motivo": "faltó el 12/06"})
    corregida = _corregir(
        client,
        h,
        pagada["id"],
        {"motivo": "faltó el 12/06",
         "recepciones_a_incluir": [vista["dias_sueltos"][0]["recepcion_id"]]},
    )
    print("\n===== ESCENARIO A · PAGAR EL RESTO =====")
    print(f"  antes de Pagar · pagado={corregida['pagado']} saldo={corregida['saldo']} "
          f"estado={corregida['estado']}")
    assert D(corregida["saldo"]) == D("180000")

    # EL PAGO VIEJO Y SU SOPORTE SIGUEN AHÍ, intactos, después de corregir.
    assert len(corregida["pagos"]) == 1, "corregir NO borra pagos"
    sigue = corregida["pagos"][0]
    assert sigue["id"] == pago_viejo["id"]
    assert D(sigue["valor"]) == D("500000")
    assert sigue["adjuntos_count"] == 1, (
        "el soporte de la transferencia de $500.000 desapareció al corregir"
    )
    assert r2.borrados == [], "no se puede borrar del bucket ninguna foto al corregir"
    soportes = client.get(
        f"{API}/{pagada['id']}/pagos/{pago_viejo['id']}/adjuntos", headers=h
    )
    assert soportes.status_code == 200, soportes.text
    assert len(soportes.json()["adjuntos"]) == 1

    # Y AHORA SÍ, EL BOTÓN PAGAR: la puerta de siempre, sin nada nuevo que aprender.
    cerrada = _pagar(client, h, pagada["id"])
    print(f"  después de Pagar · pagado={cerrada['pagado']} saldo={cerrada['saldo']} "
          f"estado={cerrada['estado']} pagos={len(cerrada['pagos'])}")
    assert cerrada["estado"] == "pagada"
    assert D(cerrada["saldo"]) == CERO
    assert D(cerrada["pagado"]) == D("680000")
    assert D(cerrada["neto_a_pagar"]) == D(cerrada["pagado"]) + D(cerrada["saldo"])

    # DOS PAGOS QUE SUMAN LA CIFRA GRANDE, y el viejo con su foto todavía puesta.
    valores = sorted(D(p["valor"]) for p in cerrada["pagos"])
    print("  pagos:", [str(v) for v in valores])
    assert valores == [D("180000"), D("500000")]
    assert sum(valores, CERO) == D(cerrada["valor_total"])
    assert next(p for p in cerrada["pagos"] if p["id"] == pago_viejo["id"])["adjuntos_count"] == 1


def test_escenario_a_el_papel_corregido_suma_de_arriba_abajo_y_no_se_contradice(
    client, base_datos
):
    """EL COMPROBANTE v2, LEÍDO RENGLÓN POR RENGLÓN COMO LO LEE EL DUEÑO.

    La columna del resumen, de arriba abajo, tiene que caer exacto:

        Valor bruto                 $680.000
        Bonificaciones            + $      0
        Descuentos                − $      0
        VALOR TOTAL                 $680.000   <- 680.000 + 0 − 0
        Anticipos aplicados       − $      0
        Pagado                    − $500.000
        SALDO A PAGAR               $180.000   <- 680.000 − 0 − 500.000

    Y el papel tiene que poder EMPAREJARSE con el que el productor guardó: folio con el
    sufijo de versión, la frase que dice a cuál reemplaza, la cifra vieja escrita, y el
    motivo. Sin eso hay dos hojas con el mismo nombre diciendo cifras distintas y nadie
    sabe cuál manda.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    pagada = _quincena_pagada(client, h, henri)

    # EL PAPEL v1 SALE PRIMERO: es el que el productor se lleva. Y al salir queda
    # anotada la fecha de primera impresión, que es lo que después permite nombrarlo.
    papel_v1 = _pdf(client, h, pagada["id"])
    print("\n===== ESCENARIO A · EL PAPEL v1 =====")
    print(f"  N.º {folio_v1(pagada['id'])} · VALOR TOTAL {renglon(papel_v1, 'VALOR TOTAL')}")
    assert folio_v1(pagada["id"]) in papel_v1
    assert f"{folio_v1(pagada['id'])}-v" not in papel_v1, (
        "un comprobante que nunca se corrigió no puede salir con sufijo de versión"
    )
    assert renglon(papel_v1, "VALOR TOTAL") == D("500000")
    assert _leer(client, h, pagada["id"])["fecha_primera_impresion"] is not None

    _recepcion(client, h, henri, "2026-06-12", "90")
    vista = _previsualizar(client, h, pagada["id"], {"motivo": "faltó anotar el 12/06"})
    _corregir(
        client,
        h,
        pagada["id"],
        {"motivo": "faltó anotar el 12/06",
         "recepciones_a_incluir": [vista["dias_sueltos"][0]["recepcion_id"]]},
    )

    papel = _pdf(client, h, pagada["id"])
    print("===== ESCENARIO A · EL PAPEL v2 =====")

    bruto = renglon(papel, "Valor bruto")
    bonificaciones = renglon(papel, "Bonificaciones")
    descuentos = renglon(papel, "Descuentos")
    valor_total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    pagado = renglon(papel, "Pagado")
    saldo = renglon(papel, "SALDO A PAGAR")
    for rotulo, cifra in [
        ("Valor bruto", bruto), ("Bonificaciones", bonificaciones),
        ("Descuentos", descuentos), ("VALOR TOTAL", valor_total),
        ("Anticipos aplicados", anticipos), ("Pagado", pagado),
        ("SALDO A PAGAR", saldo),
    ]:
        print(f"  {rotulo:<22} {cifra}")

    assert bruto == D("680000")
    assert bonificaciones == CERO
    assert descuentos == CERO
    assert valor_total == D("680000")
    assert anticipos == CERO
    assert pagado == D("-500000"), (
        "el renglón Pagado tiene que salir RESTANDO: si sale en positivo, la columna no "
        "llega al saldo y el dueño busca un error que no existe"
    )
    assert saldo == D("180000")

    # LA COLUMNA SUMA DE ARRIBA ABAJO, que es la regla de la casa aplicada al papel.
    assert bruto + bonificaciones - descuentos == valor_total
    assert valor_total + anticipos + pagado == saldo, (
        f"la columna del papel da {valor_total + anticipos + pagado} y el renglón "
        f"destacado dice {saldo}"
    )

    # EL PAPEL NO SE CONTRADICE: si dice SALDO A PAGAR, no puede decir además que el
    # productor le quedó debiendo o que se le pagó de más.
    assert "LE QUEDA DEBIENDO" not in papel
    assert "SE LE PAGÓ DE MÁS" not in papel

    # Y SE PUEDE EMPAREJAR CON LA HOJA VIEJA.
    print(f"  folio v2: {folio_v1(pagada['id'])}-v2 en el papel ->",
          f"{folio_v1(pagada['id'])}-v2" in papel)
    assert f"{folio_v1(pagada['id'])}-v2" in papel, (
        "sin el sufijo, la hoja nueva y la que el productor guardó se llaman igual"
    )
    assert "REEMPLAZA" in papel.upper()
    assert folio_v1(pagada["id"]) in papel, "tiene que nombrar el papel que reemplaza"
    # LAS DOS CIFRAS, EN LA MISMA FRASE: la vieja para emparejar con la hoja guardada y
    # la nueva para que se vea el cambio. Separadas no sirven — el productor tendría que
    # buscarlas en dos sitios del papel.
    assert "que decía VALOR TOTAL $500.000" in papel, (
        f"la nota no nombra la cifra del papel viejo:\n{papel}"
    )
    assert "El VALOR TOTAL pasó de $500.000 a $680.000" in papel, (
        f"la nota no muestra el antes y el después de la cifra grande:\n{papel}"
    )
    assert "faltó anotar el 12/06" in papel, "el motivo escrito va impreso"
    assert "12/06/2026" in papel, "y qué día entró"


# ===========================================================================
# ESCENARIO B — BAJA: el precio mal tecleado
# ===========================================================================
def test_escenario_b_el_precio_corregido_hacia_abajo_deja_al_productor_debiendo(
    client, base_datos
):
    """Las cifras del dueño, calculadas a mano:

    ANTES (v1, ya pagada):
        02/06 · 250,00 L a $2.000,00           $500.000
        VALOR TOTAL                            $500.000
        pagado                                 $500.000   <- salió de la caja, en efectivo
        saldo                                        $0

    EL PRECIO ESTABA MAL: eran $1.600 el litro, no $2.000.
        250,00 L a $1.600,00                   $400.000

    DESPUÉS (v2):
        VALOR TOTAL                            $400.000
        neto a pagar                           $400.000
        pagado                                 $500.000
        saldo                     400.000 − 500.000 = −$100.000
        LE QUEDA DEBIENDO                      $100.000   -> estado 'pagada'

    'PAGADA' Y NO 'PARCIAL', y es la verdad: no queda un peso por entregar; al revés,
    salieron $100.000 de más. Ese estado es además lo que mantiene trabados los días de
    la quincena en Recepción diaria, incluidos los que le sirven al comprobante del
    TRANSPORTADOR, que es papel de otra persona.

    La regla de la casa aguanta el signo negativo:
        neto $400.000 = pagado $500.000 + saldo (−$100.000).
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    pagada = _quincena_pagada(client, h, henri)
    assert D(pagada["valor_total"]) == D("500000")
    assert D(pagada["pagado"]) == D("500000")

    detalle = [d for d in pagada["detalles"] if d.get("deleted_at") is None][0]
    assert D(detalle["precio_litro"]) == D("2000")

    cuerpo = {
        "motivo": "el precio del 02/06 se digitó en $2.000 y eran $1.600",
        "precios": [{"detalle_id": detalle["id"], "precio_litro": "1600"}],
    }

    previa = _previsualizar(client, h, pagada["id"], cuerpo)
    print("\n===== ESCENARIO B · LA PREVIA =====")
    print(f"  total {previa['valor_total_antes']} -> {previa['valor_total_despues']} · "
          f"saldo {previa['saldo_antes']} -> {previa['saldo_despues']} · "
          f"se le pagó de más {previa['se_le_pago_de_mas']} · "
          f"estado {previa['estado_antes']} -> {previa['estado_despues']}")
    assert D(previa["valor_total_despues"]) == D("400000")
    assert D(previa["saldo_despues"]) == D("-100000")
    assert D(previa["se_le_pago_de_mas"]) == D("100000")
    assert D(previa["queda_por_entregar"]) == CERO
    assert previa["estado_despues"] == "pagada"
    # EL AVISO QUE EVITA LA LLAMADA A SOPORTE: esa plata no se pierde, se cobra en la
    # quincena siguiente. Si el diálogo no lo dice, el dueño cree que regaló $100.000.
    assert any("siguiente" in a for a in previa["avisos"]), previa["avisos"]

    corregida = _corregir(client, h, pagada["id"], cuerpo)
    print("===== ESCENARIO B · DESPUÉS (v2) =====")
    print(f"  valor_total={corregida['valor_total']} neto={corregida['neto_a_pagar']} "
          f"pagado={corregida['pagado']} saldo={corregida['saldo']} "
          f"le_queda_debiendo={corregida['le_queda_debiendo']} "
          f"estado={corregida['estado']} version={corregida['version']}")

    assert D(corregida["valor_total"]) == D("400000"), "250 L x $1.600"
    assert D(corregida["total_litros"]) == D("250.00"), "los litros NO cambian: solo el precio"
    assert D(corregida["precio_promedio"]) == D("1600")
    assert D(corregida["neto_a_pagar"]) == D("400000")
    assert D(corregida["pagado"]) == D("500000"), "la plata ya entregada no se mueve"
    assert D(corregida["saldo"]) == D("-100000")
    assert D(corregida["le_queda_debiendo"]) == D("100000")
    assert corregida["estado"] == "pagada", (
        "no queda nada por entregar; 'parcial' aquí mentiría y además soltaría los "
        "candados de la Recepción diaria de todo el período"
    )
    assert corregida["version"] == 2
    assert D(corregida["neto_a_pagar"]) == D(corregida["pagado"]) + D(corregida["saldo"])

    # El renglón de corrección deja el antes y el después del precio, con las dos cifras.
    c = _correcciones(client, h, pagada["id"])[0]
    print(f"  corrección · «{c['motivo']}» · {c['valor_total_antes']} -> "
          f"{c['valor_total_despues']} · {c['precios_corregidos']}")
    assert len(c["precios_corregidos"]) == 1
    cambio = c["precios_corregidos"][0]
    assert D(cambio["precio_antes"]) == D("2000")
    assert D(cambio["precio_despues"]) == D("1600")
    assert D(cambio["valor_antes"]) == D("500000")
    assert D(cambio["valor_despues"]) == D("400000")
    assert D(c["valor_total_antes"]) - D(c["valor_total_despues"]) == D("100000")

    # Y NO SALE UN PESO MÁS POR EL BOTÓN PAGAR. Es lo único que importa acá: la
    # quincena quedó en 'pagada' con saldo negativo, y si Pagar la dejara pasar
    # registraría un abono sobre una cuenta en la que ya se entregó de más.
    #
    # (El mensaje que devuelve es el genérico "No se puede pasar de 'pagada' a
    # 'pagada'" y no el que nombra la salida —"ese saldo se le cobra en la próxima
    # liquidación"—, porque el guardia de estado se dispara antes que el de la deuda.
    # No mueve plata, así que acá solo se mide que rebote y que nada se haya movido.)
    rebote = client.post(f"{API}/{pagada['id']}/pagar", headers=h)
    print(f"  Pagar otra vez -> {rebote.status_code}: {rebote.json()['error']['detail']}")
    assert rebote.status_code == 422
    despues_del_rebote = _leer(client, h, pagada["id"])
    assert D(despues_del_rebote["pagado"]) == D("500000"), "el rebote movió plata"
    assert D(despues_del_rebote["saldo"]) == D("-100000")
    assert len(despues_del_rebote["pagos"]) == 1


def test_escenario_b_la_quincena_siguiente_le_descuenta_la_deuda_una_sola_vez(
    client, base_datos
):
    """LO QUE SE LE PAGÓ DE MÁS SE RECUPERA SOLO, y una sola vez. Las cifras a mano:

    QUINCENA 1 (01–15 jun), corregida hacia abajo:
        VALOR TOTAL                            $400.000
        pagado (ya entregado)                  $500.000
        saldo                                 −$100.000  -> le quedó debiendo $100.000

    QUINCENA 2 (16–30 jun) — 150,00 L a $2.000,00:
        VALOR TOTAL                            $300.000
        anticipos                                    $0
        lo que quedó debiendo la pasada       −$100.000
        neto a pagar                           $200.000  <- ESTO ES LO QUE SE LE PAGA
        pagado                                −$200.000
        saldo                                        $0

    QUINCENA 3 (01–15 jul) — 100,00 L a $2.000,00:
        saldo anterior                               $0  <- LA DEUDA NO SE COBRA DOS VECES

    LAS DOS HOJAS SUMAN, que es la cuenta que el dueño hace de memoria:
        leche entregada   $400.000 + $300.000 = $700.000
        plata que salió   $500.000 + $200.000 = $700.000
    Ni un peso de más ni de menos, aunque la primera hoja se haya pagado antes de saber
    cuánto valía de verdad.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    q1 = _quincena_pagada(client, h, henri)

    detalle = [d for d in q1["detalles"] if d.get("deleted_at") is None][0]
    q1 = _corregir(
        client,
        h,
        q1["id"],
        {"motivo": "el precio del 02/06 eran $1.600, no $2.000",
         "precios": [{"detalle_id": detalle["id"], "precio_litro": "1600"}]},
    )
    print("\n===== ESCENARIO B · LA CADENA DE QUINCENAS =====")
    print(f"  Q1 corregida · total={q1['valor_total']} pagado={q1['pagado']} "
          f"saldo={q1['saldo']} debe={q1['le_queda_debiendo']}")
    assert D(q1["saldo"]) == D("-100000")

    # LA QUINCENA SIGUIENTE, por la puerta de siempre: Generar.
    _recepcion(client, h, henri, "2026-06-20", "150")
    q2 = _de(_generar(client, h, Q2), henri)
    print(f"  Q2 · total={q2['valor_total']} saldo_anterior={q2['saldo_anterior']} "
          f"neto={q2['neto_a_pagar']}")

    assert D(q2["valor_total"]) == D("300000"), "150 L x $2.000"
    assert D(q2["anticipos"]) == CERO
    assert D(q2["saldo_anterior"]) == D("100000"), (
        "los $100.000 que se le pagaron de más no se cobraron: el dueño le entregaría "
        "$300.000 teniendo $100.000 a favor"
    )
    assert D(q2["neto_a_pagar"]) == D("200000")
    assert D(q2["neto_a_pagar"]) == D(q2["pagado"]) + D(q2["saldo"])

    # LAS DOS PUNTAS DE LA DEUDA SE VEN, que es lo que permite explicar el descuento.
    assert q2["deudas_cobradas"] and q2["deudas_cobradas"][0]["id"] == q1["id"]
    q1_despues = _leer(client, h, q1["id"])
    assert q1_despues["deuda_trasladada_a_id"] == q2["id"], (
        "sin esta marca, la misma deuda se cobraría otra vez en la quincena siguiente"
    )

    _aprobar(client, h, q2["id"])
    q2 = _pagar(client, h, q2["id"])
    print(f"  Q2 pagada · pagado={q2['pagado']} saldo={q2['saldo']} estado={q2['estado']}")
    assert D(q2["pagado"]) == D("200000")
    assert D(q2["saldo"]) == CERO
    assert q2["estado"] == "pagada"

    # LA TERCERA QUINCENA NO VUELVE A COBRAR NADA: la deuda ya viajó.
    _recepcion(client, h, henri, "2026-07-02", "100")
    q3 = _de(_generar(client, h, Q3), henri)
    print(f"  Q3 · total={q3['valor_total']} saldo_anterior={q3['saldo_anterior']} "
          f"neto={q3['neto_a_pagar']}")
    assert D(q3["saldo_anterior"]) == CERO, (
        "se le cobró DOS VECES la misma deuda de $100.000: eso es plata que el "
        "productor entregó en leche y nunca le llega"
    )
    assert D(q3["valor_total"]) == D("200000")
    assert D(q3["neto_a_pagar"]) == D("200000")

    # LA CUENTA GRANDE DE LAS DOS HOJAS, la que el dueño hace de memoria.
    leche = D(q1["valor_total"]) + D(q2["valor_total"])
    plata = D(_leer(client, h, q1["id"])["pagado"]) + D(q2["pagado"])
    print(f"  leche entregada={leche} · plata que salió={plata}")
    assert leche == D("700000")
    assert plata == D("700000"), (
        f"las dos hojas no suman: la leche vale {leche} y de la caja salieron {plata}"
    )


def test_escenario_b_el_papel_dice_que_se_le_pago_de_mas_y_la_otra_hoja_lo_cobra(
    client, base_datos
):
    """LOS DOS PAPELES, LEÍDOS UNO AL LADO DEL OTRO como los pone el dueño en la mesa.

    HOJA 1 (la v2 de la quincena corregida hacia abajo):
        Valor bruto                 $400.000
        Bonificaciones            + $      0
        Descuentos                − $      0
        VALOR TOTAL                 $400.000
        Anticipos aplicados       − $      0
        Pagado                    − $500.000
        SE LE PAGÓ DE MÁS           $100.000   <- en POSITIVO, y el rótulo dice de quién

        400.000 − 0 − 500.000 = −100.000, o sea $100.000 pagados de más. El rótulo
        cambia justamente para que el renglón destacado no diga "SALDO A PAGAR
        −$100.000", que se lee como si hubiera que entregar una cifra negativa.

    HOJA 2 (la quincena siguiente):
        Valor bruto                                     $300.000
        VALOR TOTAL                                     $300.000
        Anticipos aplicados                           − $      0
        Lo que quedó debiendo de la quincena pasada    − $100.000
        Pagado                                        − $200.000
        SALDO A PAGAR                                   $      0

    Y LAS DOS HOJAS TIENEN QUE HABLAR ENTRE ELLAS: la segunda nombra a la primera por su
    folio, y ese folio ya lleva el sufijo -v2, porque el papel que manda es el corregido.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    q1 = _quincena_pagada(client, h, henri)
    detalle = [d for d in q1["detalles"] if d.get("deleted_at") is None][0]
    _corregir(
        client,
        h,
        q1["id"],
        {"motivo": "el precio del 02/06 eran $1.600, no $2.000",
         "precios": [{"detalle_id": detalle["id"], "precio_litro": "1600"}]},
    )

    hoja1 = _pdf(client, h, q1["id"])
    print("\n===== ESCENARIO B · HOJA 1 (v2) =====")
    bruto = renglon(hoja1, "Valor bruto")
    valor_total = renglon(hoja1, "VALOR TOTAL")
    anticipos = renglon(hoja1, "Anticipos aplicados")
    pagado = renglon(hoja1, "Pagado")
    de_mas = renglon(hoja1, "SE LE PAGÓ DE MÁS")
    for rotulo, cifra in [("Valor bruto", bruto), ("VALOR TOTAL", valor_total),
                          ("Anticipos aplicados", anticipos), ("Pagado", pagado),
                          ("SE LE PAGÓ DE MÁS", de_mas)]:
        print(f"  {rotulo:<22} {cifra}")

    assert bruto == D("400000")
    assert valor_total == D("400000")
    assert anticipos == CERO
    assert pagado == D("-500000")
    assert de_mas == D("100000"), "la cifra va en POSITIVO, con el rótulo diciendo de quién es"
    # LA COLUMNA CUADRA: lo que sobra de la resta es exactamente lo que dice el rótulo.
    assert valor_total + anticipos + pagado == -de_mas
    assert "SALDO A PAGAR" not in hoja1, (
        "no puede salir el rótulo de 'hay que pagar' en una hoja donde ya se pagó de más"
    )
    assert "pasó de $2.000 a $1.600 el litro" in hoja1, (
        f"la nota tiene que traer el precio viejo y el nuevo, que es justo lo que el "
        f"productor va a discutir:\n{hoja1}"
    )
    assert "El VALOR TOTAL pasó de $500.000 a $400.000" in hoja1
    assert f"{folio_v1(q1['id'])}-v2" in hoja1

    # LA HOJA 2, la que cobra la deuda.
    _recepcion(client, h, henri, "2026-06-20", "150")
    q2 = _de(_generar(client, h, Q2), henri)
    _aprobar(client, h, q2["id"])
    q2 = _pagar(client, h, q2["id"])

    hoja2 = _pdf(client, h, q2["id"])
    print("===== ESCENARIO B · HOJA 2 =====")
    total2 = renglon(hoja2, "VALOR TOTAL")
    anticipos2 = renglon(hoja2, "Anticipos aplicados")
    deuda2 = renglon(hoja2, "Lo que quedó debiendo de la quincena pasada")
    pagado2 = renglon(hoja2, "Pagado")
    saldo2 = renglon(hoja2, "SALDO A PAGAR")
    for rotulo, cifra in [("VALOR TOTAL", total2), ("Anticipos", anticipos2),
                          ("Deuda pasada", deuda2), ("Pagado", pagado2),
                          ("SALDO A PAGAR", saldo2)]:
        print(f"  {rotulo:<22} {cifra}")

    assert total2 == D("300000")
    assert deuda2 == D("-100000"), "el descuento sale RESTANDO y con la cifra exacta"
    assert pagado2 == D("-200000")
    assert saldo2 == CERO
    assert total2 + anticipos2 + deuda2 + pagado2 == saldo2, (
        "la columna de la hoja 2 no llega a la cifra grande"
    )

    # LAS DOS HOJAS SE NOMBRAN: la segunda dice de dónde salió el descuento, y nombra la
    # versión que manda —la corregida—, no el papel viejo de $500.000.
    print(f"  ¿la hoja 2 nombra a la hoja 1 corregida? -> "
          f"{folio_v1(q1['id']) + '-v2' in hoja2}")
    assert folio_v1(q1["id"]) in hoja2, (
        "la hoja 2 descuenta $100.000 sin decir de qué comprobante salieron"
    )
    assert f"{folio_v1(q1['id'])}-v2" in hoja2, (
        "la hoja 2 tiene que nombrar la versión CORREGIDA de la hoja 1: si nombra el "
        "folio pelado, manda a buscar el papel de $500.000, que es el que ya no vale"
    )
    # Y LA CIFRA DE LA NOTA ES LA MISMA DEL RENGLÓN: si la letra chica dijera otra
    # cosa, el papel se contradiría solo en la mesa donde se está discutiendo.
    assert "donde quedó debiendo $100.000" in hoja2, (
        f"la nota al pie no repite la cifra del descuento:\n{hoja2}"
    )


# ===========================================================================
# LO QUE EL PAPEL TODAVÍA NO DICE — trabajo que sigue
# ===========================================================================
def test_el_papel_corregido_lleva_la_banda_de_corregido_en_el_encabezado(client, base_datos):
    """La marca que se ve sin leer: este papel reemplaza a otro.

    Se exige en el ENCABEZADO —antes del 'Detalle diario', que es donde arranca el
    cuerpo— porque es ahí donde alguien mira para saber qué hoja tiene en la mano. Es la
    misma pieza que ya pidió el dueño en otras partes del sistema: que el papel diga lo
    que es sin obligar a leerlo entero.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    pagada = _quincena_pagada(client, h, henri)
    _recepcion(client, h, henri, "2026-06-12", "90")
    vista = _previsualizar(client, h, pagada["id"], {"motivo": "faltó el 12/06"})
    _corregir(
        client,
        h,
        pagada["id"],
        {"motivo": "faltó el 12/06",
         "recepciones_a_incluir": [vista["dias_sueltos"][0]["recepcion_id"]]},
    )

    papel = _pdf(client, h, pagada["id"])
    encabezado = papel[: papel.find("Detalle diario")]
    print("\n===== LA BANDA DE CORREGIDO =====")
    print(f"  encabezado: {encabezado[:300]}")
    assert "CORREGIDO" in encabezado.upper(), (
        "el encabezado no dice en ninguna parte que este comprobante es una corrección"
    )


def test_la_hora_que_nombra_el_papel_corregido_es_la_que_lee_el_productor(client, base_datos):
    """La nota dice «reemplaza al comprobante emitido el ...». Esa hora tiene que ser
    la que el productor ve impresa en la hoja que tiene guardada.

    Se mide contra HORA DE COLOMBIA y no contra la hora local de la máquina a propósito:
    el papel se lee en una finca de Granada, y el proyecto ya escribió esa decisión una
    vez —`HORA_COLOMBIA` en app/core/storage.py, para decirle a quien comparte un
    soporte hasta cuándo sirve el enlace—. Medirlo así hace que la prueba diga lo mismo
    corra donde corra.
    """
    from datetime import datetime, timezone as tz

    from app.core.storage import HORA_COLOMBIA

    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    pagada = _quincena_pagada(client, h, henri)

    # LA HOJA v1 SALE Y SE VA CON EL PRODUCTOR. Esta es la hora que él tiene impresa.
    papel_v1 = _pdf(client, h, pagada["id"])
    inicio = papel_v1.find("Emitido:")
    hora_impresa_en_la_v1 = papel_v1[inicio + len("Emitido:"): inicio + 26].strip()

    guardada = _leer(client, h, pagada["id"])["fecha_primera_impresion"]
    momento = datetime.fromisoformat(guardada.replace("Z", "+00:00"))
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=tz.utc)
    esperada = momento.astimezone(HORA_COLOMBIA).strftime("%d/%m/%Y %H:%M")

    _recepcion(client, h, henri, "2026-06-12", "90")
    vista = _previsualizar(client, h, pagada["id"], {"motivo": "faltó el 12/06"})
    _corregir(
        client,
        h,
        pagada["id"],
        {"motivo": "faltó el 12/06",
         "recepciones_a_incluir": [vista["dias_sueltos"][0]["recepcion_id"]]},
    )
    papel_v2 = _pdf(client, h, pagada["id"])
    donde = papel_v2.find("REEMPLAZA")

    print("\n===== LA HORA DE LAS DOS HOJAS =====")
    print(f"  la v1 salió impresa con   : Emitido: {hora_impresa_en_la_v1}")
    print(f"  en hora de Colombia es    : {esperada}")
    print(f"  la v2 le pide al productor: {papel_v2[donde:donde + 90]}")
    assert f"emitido el {esperada}" in papel_v2, (
        f"la hoja nueva nombra una hora que no está impresa en ninguna hoja: pide el "
        f"comprobante «emitido el ...» pero el papel que el productor tiene dice "
        f"«Emitido: {hora_impresa_en_la_v1}»"
    )


def test_si_nunca_se_imprimio_el_papel_viejo_la_correccion_no_puede_nombrar_su_hora(
    client, base_datos
):
    """El dueño se dio cuenta del error ANTES de imprimir. No hay papel que recoger.

    Es un caso corriente: la quincena se genera, se aprueba y se paga por transferencia
    el mismo día, y el comprobante se imprime cuando el productor pasa por la quesera —a
    veces días después—. Si en ese intervalo el dueño corrige, la hoja que sale es
    directamente la v2 y no hay ninguna v1 impresa en el mundo.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    pagada = _quincena_pagada(client, h, henri)
    assert _leer(client, h, pagada["id"])["fecha_primera_impresion"] is None, (
        "el montaje de esta prueba exige que NADIE haya impreso todavía"
    )

    _recepcion(client, h, henri, "2026-06-12", "90")
    vista = _previsualizar(client, h, pagada["id"], {"motivo": "faltó el 12/06"})
    _corregir(
        client,
        h,
        pagada["id"],
        {"motivo": "faltó el 12/06",
         "recepciones_a_incluir": [vista["dias_sueltos"][0]["recepcion_id"]]},
    )

    # La primera descarga es ya la de la v2 — y es la que estampa la fecha.
    _pdf(client, h, pagada["id"])
    papel = _pdf(client, h, pagada["id"])
    donde = papel.find("REEMPLAZA")
    print("\n===== LA IMPRESIÓN QUE NUNCA EXISTIÓ =====")
    print(f"  {papel[donde:donde + 110]}")
    assert "emitido el" not in papel, (
        "el papel manda a buscar una hoja «emitida» a una hora en la que no había "
        "salido ningún papel: la que se estampó es la hora en que se imprimió ESTA"
    )


def test_el_papel_corregido_no_puede_llamar_pago_total_a_un_abono_parcial(client, base_datos):
    """Las cifras: valor total $680.000, entregado en dos veces ($500.000 + $180.000).

    Ninguno de los dos renglones es el pago total, y sin embargo los dos lo dicen. Es la
    clase de renglón por el que el dueño pierde la confianza en el papel: suma la
    columna de pagos, le da $680.000, y arriba lee dos veces que ya se había pagado todo.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h)
    pagada = _quincena_pagada(client, h, henri)
    _recepcion(client, h, henri, "2026-06-12", "90")
    vista = _previsualizar(client, h, pagada["id"], {"motivo": "faltó el 12/06"})
    _corregir(
        client,
        h,
        pagada["id"],
        {"motivo": "faltó el 12/06",
         "recepciones_a_incluir": [vista["dias_sueltos"][0]["recepcion_id"]]},
    )

    a_medias = _pdf(client, h, pagada["id"])
    seccion = a_medias[a_medias.find("Pagos y giros realizados"):]
    print("\n===== LA TABLA DE PAGOS DEL PAPEL CORREGIDO =====")
    print(f"  v2 con saldo pendiente: {seccion[:160]}")
    assert seccion.count("Pago total de la liquidación") == 0, (
        "un abono de $500.000 sobre un total de $680.000 no es el «pago total»: el "
        "mismo papel dice al lado SALDO A PAGAR $180.000"
    )

    cerrada = _pagar(client, h, pagada["id"])
    assert D(cerrada["valor_total"]) == D("680000")
    papel = _pdf(client, h, pagada["id"])
    seccion = papel[papel.find("Pagos y giros realizados"):]
    print(f"  v2 ya saldada         : {seccion[:200]}")
    assert seccion.count("Pago total de la liquidación") <= 1, (
        "el papel trae DOS renglones que dicen los dos ser el pago total de la misma "
        "quincena, y ninguno de los dos vale lo que vale la quincena"
    )
