"""LA PLATA DEL DÍA NO SE BORRA: el defecto que estaba, con cifras, y los cuatro campos
que lo disparaban. Hoy son la red que lo mantiene cerrado.

EN UNA LÍNEA, LO QUE PASABA: si a una ruta se le pasaba la tarifa de POR LITRO a DÍA FIJO
cuando ya había un comprobante emitido de esa ruta, la primera corrección que se le hiciera
a cualquier día de ese comprobante LE BORRABA LA PLATA A ESA RECEPCIÓN y el comprobante
salía más barato — con una línea que le decía al conductor «91,30 L × $0 = $0,00».

Está cerrado: el recuadre conserva el modo y la tarifa con que se emitió el papel. Estas
pruebas siguen midiendo lo mismo, y lo que exigen ahora es que la cifra sea una de las dos
correctas —la cuenta por litro con la tarifa emitida, o el día completo si se re-precificó
a propósito— y nunca la tercera.

LA CADENA QUE LO PRODUCÍA, con nombres de archivo:

  1. `recepcion/service.py::_completar_y_calcular` escribe CERO en la foto de la
     recepción SIEMPRE que la tarifa DE HOY sea `dia_fijo`, porque en un fijo la
     recepción no tiene cifra propia y espera que el reparto la llene.
  2. NADIE la llena. `_repartir_el_fijo_del_dia` solo reparte las recepciones con
     `liquidacion_transporte_id IS NULL`, y esta está DENTRO de un comprobante.
  3. El comprobante la rearma por RECUADRE, que conserva el modo de antes
     (`_RenglonDeAntes.modo == 'litro'`) y por lo tanto entra por el camino POR
     LITRO de `_renglones_del_dia_y_ruta`, donde la tarifa se lee con
     `_por_litro_de_hoy` — que devuelve CERO cuando hoy se cobra por día fijo.
  4. Con la foto en cero y la tarifa en cero, `_tarifa_de_la_foto` concluye que
     "la tarifa 0 explica esta foto", el renglón se parte, y esa recepción sale
     cobrando $0,00.

EL DESGLOSE SIGUE CUADRANDO (fotos == renglones == total), así que ninguna de las
redes de cuadre lo ve: simplemente se le paga menos al conductor.

LAS CIFRAS DEL CASO, ruta "A fabrica", 16/07/2026:

    Aurelio    82,00 L                        Marleny   137,45 L
    a $242,76 el litro  →  219,45 L × $242,76 = $53.273,68   (comprobante emitido)

    se le pone DÍA FIJO $150.000 a la ruta
    se le corrige a Aurelio 82,00 → 91,30 L

    lo que debería decir el comprobante:
      · si se respeta el modo de antes (por litro):  228,75 L × $242,76 = $55.531,35
      · si se re-precifica con el modo de hoy:       EL DÍA COMPLETO = $150.000,00
    lo que dice:                                     $33.367,36

    O SEA: entre $22.163,99 y $116.632,64 que el conductor deja de cobrar.
"""
import pytest

from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    FIJO,
    LIQUIDACIONES,
    NAPOLES,
    RECEPCIONES,
    TRANSPORTADORES,
    D,
    _escenario,
    _liquidar_flete,
    _recibir,
    centavos,
)
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, Medida, _ok, _put

CERO = D(0)
LITROS = (D("82.00"), D("137.45"))
POR_LITRO_EMITIDO = centavos(sum(LITROS, CERO) * NAPOLES)          # $53.273,68


def _poner(client, h, esc, valor, modo):
    _ok(client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(valor),
             "modo_transporte": modo},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": "litro"},
        ]},
        headers=h,
    ), "tarifa")


def _foto(client, h, db_session, esc, liq_id, paso):
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    m = Medida(db_session, esc["alex"]["id"], EL_DIA, esc["fabrica"]["id"])
    print(f"  {paso}")
    print(f"      comprobante ${liq['valor_transporte']}  ({liq['estado']})")
    for d in liq["detalles"]:
        print(f"      renglon [{d['modo_transporte']}] {d['litros']} L x "
              f"${d['precio_litro']} = ${d['valor']}")
    print(f"      fotos: {m}")
    return liq, m


def _armar(client, h, esc):
    """El comprobante POR LITRO ya emitido, y después la ruta pasada a DÍA FIJO."""
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", str(LITROS[0]))
    b = _recibir(client, h, esc, EL_DIA, "Marleny", str(LITROS[1]))
    liq_id = _liquidar_flete(client, h)["id"]
    _poner(client, h, esc, FIJO, "dia_fijo")
    return a, b, liq_id


# ---------------------------------------------------------------------------
def test_defecto_corregir_los_litros_le_borra_la_plata_a_la_recepcion(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _poner(client, h, esc, NAPOLES, "litro")
    print("\n===== DEFECTO: corregir litros tras pasar la ruta a DIA FIJO =====")
    a, _, liq_id = _armar(client, h, esc)
    liq, _ = _foto(client, h, db_session, esc, liq_id, "1. emitido POR LITRO")
    assert D(liq["valor_transporte"]) == POR_LITRO_EMITIDO

    _ok(_put(client, h, a["id"], cantidad_litros="91.30"), "corregir litros")
    liq, m = _foto(client, h, db_session, esc, liq_id,
                   "2. corregidos 82,00 -> 91,30 L (RECUADRE)")
    por_litro = centavos((D("91.30") + LITROS[1]) * NAPOLES)
    print(f"      a mano por litro con 228,75 L: ${por_litro}")
    print(f"      a mano si se re-precificara:   ${FIJO} (el dia completo)")
    print(f"      PERDIDA: ${por_litro - D(liq['valor_transporte'])} contra por litro, "
          f"${FIJO - D(liq['valor_transporte'])} contra el dia fijo de hoy")
    # El desglose SIGUE cuadrando: por eso ninguna red de cuadre lo ve.
    assert m.fotos == D(liq["valor_transporte"])
    assert D(liq["valor_transporte"]) in (por_litro, FIJO), (
        f"el comprobante quedo en ${liq['valor_transporte']}: no es ni la cuenta por "
        f"litro (${por_litro}) ni el dia fijo de hoy (${FIJO})"
    )


# ---------------------------------------------------------------------------
@pytest.mark.parametrize("campo,valor", [
    ("cantidad_litros", "91.30"),
    ("fecha", "2026-07-17"),
    ("estado", "inactivo"),
    ("observaciones", "solo una nota"),
])
def test_defecto_por_cada_campo_que_reescribe_la_foto(
    client, base_datos, db_session, campo, valor
):
    """Cualquier campo de `_CAMPOS_DEL_FLETE` lo dispara; `observaciones` no."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _poner(client, h, esc, NAPOLES, "litro")
    a, _, liq_id = _armar(client, h, esc)
    antes = D(client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()["valor_transporte"])
    r = _put(client, h, a["id"], **{campo: valor})
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    despues = D(liq["valor_transporte"])
    ceros = [d for d in liq["detalles"] if D(d["valor"]) == CERO and D(d["litros"]) > CERO]
    print(f"\n  campo {campo:<18} -> {r.status_code}   ${antes} -> ${despues}")
    for d in liq["detalles"]:
        print(f"      [{d['modo_transporte']}] {d['litros']} L x ${d['precio_litro']} "
              f"= ${d['valor']}")
    assert not ceros, (
        f"corregir «{campo}» dejo {len(ceros)} renglon(es) con litros cobrados en $0,00: "
        f"{[(d['litros'], d['valor']) for d in ceros]}"
    )


# ---------------------------------------------------------------------------
def test_defecto_la_cifra_rebajada_se_puede_aprobar_y_pagar(
    client, base_datos, db_session
):
    """La cifra rebajada NO rebota: se aprueba, se paga, y queda congelada."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _poner(client, h, esc, NAPOLES, "litro")
    print("\n===== DEFECTO: la cifra rebajada se aprueba y se paga =====")
    a, _, liq_id = _armar(client, h, esc)
    _ok(_put(client, h, a["id"], cantidad_litros="91.30"), "corregir")
    liq, _ = _foto(client, h, db_session, esc, liq_id, "tras corregir")
    rebajado = D(liq["valor_transporte"])
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h), "aprobar")
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h), "pagar")
    liq, m = _foto(client, h, db_session, esc, liq_id, "APROBADO Y PAGADO")
    por_litro = centavos((D("91.30") + LITROS[1]) * NAPOLES)
    print(f"      se le pago ${rebajado}; le correspondian ${por_litro} por litro "
          f"o ${FIJO} por el dia completo")
    assert liq["estado"] == "pagada"
    assert rebajado in (por_litro, FIJO), (
        f"se pago ${rebajado}, que no es ni ${por_litro} ni ${FIJO}"
    )


# ---------------------------------------------------------------------------
def test_defecto_el_papel_le_dice_al_conductor_que_sus_litros_valen_cero(
    client, base_datos, db_session
):
    """El PDF que se le entrega al conductor trae la línea «91,30 L × $0 = $0,00»."""
    import io

    from pypdf import PdfReader

    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _poner(client, h, esc, NAPOLES, "litro")
    print("\n===== DEFECTO: lo que dice el PAPEL del conductor =====")
    a, _, liq_id = _armar(client, h, esc)
    _ok(_put(client, h, a["id"], cantidad_litros="91.30"), "corregir")
    contenido = client.get(f"{LIQUIDACIONES}/{liq_id}/pdf", headers=h).content
    texto = " ".join(
        "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
        .split()
    )
    print(f"  {texto[texto.find('Detalle diario'):texto.find('Resumen')]}")
    assert "$0" not in texto or "91,3" not in texto, (
        "el papel del conductor trae una linea de 91,30 L cobrada en $0"
    )


# ---------------------------------------------------------------------------
def test_defecto_sobre_un_comprobante_APROBADO(client, base_datos, db_session):
    """El mismo camino sobre un comprobante ya APROBADO: le tumba el visto bueno y
    además lo deja rebajado."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _poner(client, h, esc, NAPOLES, "litro")
    print("\n===== DEFECTO: sobre un comprobante APROBADO =====")
    a, _, liq_id = _armar(client, h, esc)
    # (se aprueba antes de cambiarle el modo a la ruta)
    _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h), "aprobar")
    liq, _ = _foto(client, h, db_session, esc, liq_id, "aprobado")
    aprobado = D(liq["valor_transporte"])
    _ok(_put(client, h, a["id"], cantidad_litros="91.30"), "corregir")
    liq, m = _foto(client, h, db_session, esc, liq_id, "corregido tras aprobar")
    por_litro = centavos((D("91.30") + LITROS[1]) * NAPOLES)
    print(f"      aprobado en ${aprobado}; a mano ahora ${por_litro} (o ${FIJO} fijo)")
    assert D(liq["valor_transporte"]) in (por_litro, FIJO), (
        f"el comprobante APROBADO quedo en ${liq['valor_transporte']}"
    )
