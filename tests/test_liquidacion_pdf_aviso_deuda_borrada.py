"""EL PAPEL DE LA FILA CON DEUDA BORRADA DICE LO QUE DE VERDAD QUEDA, CON LA FRASE DEL 422.

Desde que el PDF lleva el renglón "Deuda borrada por la migración", su columna cierra
exacto... sobre el saldo guardado, que es justo la cifra que el propio servidor declara
falsa. La pantalla lo compensaba con su aviso rojo; el papel —el que lleva "Entregué
conforme / Recibí conforme"— no traía nada:

  · PLAIN (90 L × $2.000 = $180.000 contra $300.000, migrada): "Estado: PAGADA, SALDO A
    PAGAR $0", y el 422 dice que el tercero todavía debe $120.000;
  · MAS-CINCUENTA (corregida con 25 L = $50.000, sin pagar): "SALDO A PAGAR $50.000"
    a quien todavía debe $70.000;
  · ARRIBA (corregida con 100 L = $200.000, sin pagar): "SALDO A PAGAR $200.000", y lo que
    de verdad falta entregarle son $80.000 —pagarle el papel le entrega $120.000 de más—;
  · CERO (corregida con 60 L = $120.000): "SALDO A PAGAR $120.000" y no falta nada;
  · BAJA PRECIO COBRADA (el precio bajó a $1.500 y la quincena siguiente le cobró los
    $45.000): "LE QUEDA DEBIENDO $45.000" y la nota "no hay que volver a cobrarlo", sin una
    palabra de los $120.000 que todavía debe.

Ahora el papel trae, como PRIMERA nota, un AVISO con la posición de hoy escrita por la
MISMA función que el 422 y que `LiquidacionRead.aviso_deuda_borrada` (`posicion_de_hoy`),
y una marca en el encabezado que no tapa la de "COMPROBANTE CORREGIDO". Los renglones y el
cierre no se tocan: la columna se sigue sumando con calculadora hasta el renglón destacado.
"""
import io
import uuid
from decimal import Decimal

from pypdf import PdfReader

from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import pagada_sin_que_saliera_un_peso
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_borrada_con_pagos import (
    _antes_del_guardia,
    _corregir,
    _migradas,
)
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _dia, _julio_aprobada, _leer
from tests.test_liquidacion_pdf_deuda_borrada import _cierra, _cifra, _resumen

API = "/api/v1/liquidaciones"
MARCA = "PENDIENTE DE REPARAR · VER EL AVISO"
AVISO = "AVISO: esta quincena está pendiente de reparar."
DE_DONDE = ("Viene de antes de que existieran los abonos, y el sistema de esa época le "
            "borró lo que el tercero quedaba debiendo ($120.000).")


def D(v):
    return Decimal(str(v))


def _papel(client, h, liq_id):
    """(páginas, texto del PDF en una sola línea): pypdf parte las notas largas en
    renglones, y la frase se busca entera."""
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    lector = PdfReader(io.BytesIO(r.content))
    texto = "\n".join(p.extract_text() for p in lector.pages)
    return len(lector.pages), " ".join(texto.split())


def _las_cinco(client, h, db, monkeypatch):
    nombres = ("Aviso Plain", "Aviso Mas50", "Aviso Arriba", "Aviso Cero",
               "Aviso Baja Cobrada")
    filas = _migradas(client, h, db, *nombres)
    # El código de agosto, sin el guardia: así se corrigieron esas filas en producción.
    _antes_del_guardia(monkeypatch)
    for nombre, litros in (("Aviso Mas50", "25"), ("Aviso Arriba", "100"),
                           ("Aviso Cero", "60")):
        prov, liq_id = filas[nombre]
        _corregir(client, h, liq_id, _dia(client, h, prov, "2026-07-08", litros))
    prov, liq_id = filas["Aviso Baja Cobrada"]
    detalle = _leer(client, h, liq_id)["detalles"][0]["id"]
    r = client.post(f"{API}/{liq_id}/corregir", json={
        "motivo": "precio mal digitado",
        "precios": [{"detalle_id": detalle, "precio_litro": "1500"}]}, headers=h)
    assert r.status_code == 200, r.text
    monkeypatch.undo()
    # La quincena siguiente le cobra los $45.000 que quedó debiendo con el precio nuevo.
    _dia(client, h, prov, "2026-07-20", "10")
    r = client.post(f"{API}/generar", json={"periodo_inicio": "2026-07-16",
                                            "periodo_fin": "2026-07-31",
                                            "tipo": "proveedor"}, headers=h)
    assert r.status_code == 200, r.text
    return {nombre: liq_id for nombre, (_, liq_id) in filas.items()}


# La posición de hoy de cada una, con las cifras del dueño. Con calculadora: lo que de
# verdad queda es saldo − borrada (negativo: lo que él debe).
POSICION = {
    # saldo 0 − 120.000 = −120.000
    "Aviso Plain": "Hoy el tercero todavía le debe $120.000 a la quesera",
    # saldo 50.000 − 120.000 = −70.000
    "Aviso Mas50": "Hoy el tercero todavía le debe $70.000 a la quesera",
    # saldo 200.000 − 120.000 = 80.000
    "Aviso Arriba": ("El saldo dice $200.000, pero lo que de verdad falta entregarle es "
                     "$80.000: los otros $120.000 son la deuda que borró la migración"),
    # saldo 120.000 − 120.000 = 0
    # El saldo entero es lo borrado: "esos", no "los otros" (no hay unos primeros).
    "Aviso Cero": ("El saldo dice $120.000, pero de verdad no falta entregarle nada: esos "
                   "$120.000 son la deuda que borró la migración"),
    # saldo −45.000 − 120.000 = −165.000, de los que $45.000 ya se cobraron en otra
    "Aviso Baja Cobrada": ("Hoy el tercero todavía le debe $120.000 a la quesera, aparte "
                           "de los $45.000 que ya se le cobraron en otra quincena"),
}
CIERRE = {
    "Aviso Plain": ("SALDO A PAGAR", D(0)),
    "Aviso Mas50": ("SALDO A PAGAR", D(50000)),
    "Aviso Arriba": ("SALDO A PAGAR", D(200000)),
    "Aviso Cero": ("SALDO A PAGAR", D(120000)),
    "Aviso Baja Cobrada": ("LE QUEDA DEBIENDO", D(45000)),
}


def test_la_pantalla_el_422_y_el_papel_dicen_la_misma_posicion_de_hoy(
        client, base_datos, db_session, monkeypatch):
    h = auth_headers(client, "admin.a")
    filas = _las_cinco(client, h, db_session, monkeypatch)
    for nombre, liq_id in filas.items():
        hoy = _leer(client, h, liq_id)
        assert D(hoy["deuda_borrada_por_la_migracion"]) == D(120000), nombre
        posicion = POSICION[nombre]
        # 1) La pantalla recibe la frase escrita por el servidor.
        assert hoy["aviso_deuda_borrada"] == posicion, nombre
        # 2) Es la misma del 422 de Pagar, no una copia.
        pagar = client.post(f"{API}/{liq_id}/pagar", headers=h)
        assert pagar.status_code == 422 and posicion in _detalle(pagar), nombre
        # 3) El papel la trae en el AVISO, que es la primera nota, en una sola hoja.
        paginas, texto = _papel(client, h, liq_id)
        print(f"\n  {nombre} (v{hoy['version']}): {texto[texto.find(AVISO):][:420]}")
        assert paginas == 1, nombre
        rotulo, cifra = CIERRE[nombre]
        aviso = (f"{AVISO} {DE_DONDE} Por eso el resumen lleva el renglón «Deuda borrada "
                 f"por la migración» y el «{rotulo}» de arriba no es lo que queda. "
                 f"{posicion}.")
        assert aviso in texto, nombre
        assert texto.index(aviso) > texto.index(rotulo), nombre
        for otra_nota in ("Este comprobante REEMPLAZA", "ya se le cobró en la liquidación"):
            if otra_nota in texto:
                assert texto.index(aviso) < texto.index(otra_nota), (nombre, otra_nota)
        # 4) La marca del encabezado, sin tapar la de la versión.
        assert MARCA in texto, nombre
        if hoy["version"] > 1:
            assert f"COMPROBANTE CORREGIDO (v{hoy['version']})" in texto, nombre
        # 5) Los renglones y el cierre siguen iguales: la columna cierra exacto.
        renglones = _resumen(client, h, liq_id)
        assert renglones[-1][0] == rotulo and _cifra(renglones[-1][1])[1] == cifra, nombre
        assert _cifra(dict(renglones)["Deuda borrada por la migración"]) == (1, D(120000))
        if rotulo == "SALDO A PAGAR":
            _cierra(renglones)
        # 6) Ninguna recibe la frase de "cerrada como pagada sin pago": el servidor nunca
        #    la dice de una fila con deuda borrada, y sus cifras no cuadrarían.
        assert hoy["cerrada_sin_pago"] is None, nombre
    # La de precio bajado quedó 'pagada' debiendo sin un pago: la pregunta la ve, y aun
    # así la frase no sale, porque la deuda borrada manda.
    baja = db_session.get(Liquidacion, uuid.UUID(filas["Aviso Baja Cobrada"]))
    db_session.refresh(baja)
    assert baja.estado == "pagada" and pagada_sin_que_saliera_un_peso(baja)


def test_la_cobrada_no_deja_sola_la_nota_de_no_volver_a_cobrar(
        client, base_datos, db_session, monkeypatch):
    """"No hay que volver a cobrarlo" habla de los $45.000; el AVISO va antes y dice que
    los $120.000 borrados todavía se deben."""
    h = auth_headers(client, "admin.a")
    liq_id = _las_cinco(client, h, db_session, monkeypatch)["Aviso Baja Cobrada"]
    assert _leer(client, h, liq_id)["deuda_trasladada_a_id"] is not None
    _, texto = _papel(client, h, liq_id)
    nota = "Lo que quedó debiendo en esta quincena ($45.000) ya se le cobró en la liquidación"
    assert nota in texto
    assert texto.index(POSICION["Aviso Baja Cobrada"]) < texto.index(nota)


def test_la_quincena_normal_y_la_anulada_no_llevan_aviso(
        client, base_datos, db_session, monkeypatch):
    """Control: 100 L × $2.000 = $200.000 − $50.000 de adelanto, sin deuda borrada. Y la
    anulada con deuda borrada, que no vale nada: no se paga ni se debe, así que decirle
    "hoy el tercero todavía le debe" sería falso (mismo universo que "por reparar")."""
    h = auth_headers(client, "admin.a")
    _, normal = _julio_aprobada(client, h, "Aviso Normal", "100", "50000")
    (_, anulada), = _migradas(client, h, db_session, "Aviso Anulada").values()
    db_session.get(Liquidacion, uuid.UUID(anulada)).estado = "anulada"
    db_session.commit()
    for liq_id in (normal["id"], anulada):
        hoy = _leer(client, h, liq_id)
        assert hoy["aviso_deuda_borrada"] is None
        paginas, texto = _papel(client, h, liq_id)
        assert paginas == 1
        assert AVISO not in texto and MARCA not in texto
