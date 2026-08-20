"""SEGUNDA TANDA DE SONDAS: el día SIN RUTA, generar tres veces, y el ciclo
liquidar → anular → liquidar → anular → liquidar sobre el mismo día fijo.
"""
import io

from pypdf import PdfReader

from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    FIJO,
    LIQUIDACIONES,
    NAPOLES,
    PROVEEDORES,
    TRANSPORTADORES,
    D,
    _crear,
    _escenario,
    _liquidar_flete,
    _recibir,
    centavos,
)
from tests.test_transporte_dia_fijo_auditoria import EL_DIA, Medida, _medir, _ok

CERO = D(0)


def _texto_pdf(contenido: bytes) -> str:
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


# ---------------------------------------------------------------------------
# SONDA 6 — EL DÍA SIN RUTA, con la tarifa GENERAL en día fijo
# ---------------------------------------------------------------------------
def test_sonda_el_dia_sin_ruta_con_la_general_en_dia_fijo(
    client, base_datos, db_session
):
    """Un proveedor sin ruta: su día no tiene ruta y le aplica la tarifa GENERAL.
    Con la general en día fijo, ese (día, SIN ruta) es un grupo tan válido como
    los demás y vale $120.000 completos, repartidos entre sus recepciones.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _ok(client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"valor_transporte": "120000", "modo_transporte": "dia_fijo",
              "rutas": [{"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(FIJO),
                         "modo_transporte": "dia_fijo"}]},
        headers=h,
    ), "general en dia fijo")
    for nombre in ("SinRutaUno", "SinRutaDos", "SinRutaTres"):
        esc["proveedores"][nombre] = _crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "El Monte", "precio_litro": "1800"})

    print("\n===== SONDA 6: el dia SIN RUTA con la general en dia fijo =====")
    _recibir(client, h, esc, EL_DIA, "SinRutaUno", "40.00")
    _recibir(client, h, esc, EL_DIA, "SinRutaDos", "75.50")
    _recibir(client, h, esc, EL_DIA, "SinRutaTres", "84.50")
    # También un día CON ruta, para que los dos grupos convivan.
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")

    m = Medida(db_session, esc["alex"]["id"], EL_DIA, None)
    print(f"  sin ruta   {m}")
    print("  a mano: $120.000 entre 200,00 L -> 40,00/75,50/84,50 L")
    assert m.fotos == D("120000"), f"el dia sin ruta vale ${m.fotos}"
    _medir(db_session, esc, "y el dia CON ruta sigue en su fijo")

    liq = client.get(
        f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h
    ).json()
    print(f"  comprobante: ${D(liq['valor_transporte'])}  (a mano 120.000 + 150.000)")
    for d in sorted(liq["detalles"], key=lambda x: str(x["ruta_nombre"])):
        print(f"    {d['fecha']} {str(d['ruta_nombre']):<12} {d['litros']:>8} L  "
              f"[{d['modo_transporte']}] ${d['valor']}")
    assert D(liq["valor_transporte"]) == D("270000"), liq["valor_transporte"]
    m = Medida(db_session, esc["alex"]["id"], EL_DIA, None)
    assert m.fotos == D("120000")
    _medir(db_session, esc, "tras generar, el dia con ruta")

    texto = _texto_pdf(
        client.get(f"{LIQUIDACIONES}/{liq['id']}/pdf", headers=h).content
    )
    print(f"  el PDF dice «Dia completo»: {'Día completo' in texto}")
    print(f"  el PDF trae $270.000: {'$270.000' in texto}")
    assert "Día completo" in texto


# ---------------------------------------------------------------------------
# SONDA 7 — GENERAR tres veces seguidas sobre el mismo período
# ---------------------------------------------------------------------------
def test_sonda_generar_tres_veces_seguidas(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== SONDA 7: generar tres veces seguidas =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, "2026-07-18", "Henri", "219.45")
    for i in range(3):
        r = client.post(
            f"{LIQUIDACIONES}/generar",
            json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
                  "tipo": "transportador"},
            headers=h,
        )
        assert r.status_code in (200, 201), r.text
        cuerpo = r.json()
        print(f"  pasada {i + 1}: generadas={len(cuerpo['generadas'])} "
              f"omitidas={[o['motivo_codigo'] for o in cuerpo['omitidas']]}")
        _medir(db_session, esc, f"tras generar {i + 1}/3")
        _medir(db_session, esc, f"y napoles {i + 1}/3",
               vale=centavos(D("219.45") * NAPOLES),
               fecha="2026-07-18", ruta="napoles")


# ---------------------------------------------------------------------------
# SONDA 8 — liquidar → anular → liquidar → anular → liquidar
# ---------------------------------------------------------------------------
def test_sonda_el_ciclo_de_liquidar_y_anular_tres_vueltas(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    print("\n===== SONDA 8: liquidar y anular, tres vueltas =====")
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    for vuelta in range(3):
        liq_id = _liquidar_flete(client, h)["id"]
        m = _medir(db_session, esc, f"vuelta {vuelta + 1}: liquidado")
        assert len(m.renglones) == 1, m.renglones
        if vuelta < 2:
            _ok(client.post(f"{LIQUIDACIONES}/{liq_id}/anular", headers=h), "anular")
            _medir(db_session, esc, f"vuelta {vuelta + 1}: anulado")
    _medir(db_session, esc, "final")
