"""AUDITORÍA: la regla multiempresa sobre el camino de la plata de la leche.

Las DOS queseras del cliente viven en la misma instalación. Acá se mide que nada
del recorrido de la leche —recepciones, anticipos, liquidaciones, deuda arrastrada,
pagos— se pueda apuntar a un tercero de la otra empresa ni leer desde la otra.
"""
import pytest

from tests.conftest import auth_headers
from tests.test_zzaudit_plata_de_la_leche import (
    ANT,
    API,
    CERO,
    D,
    Q1,
    REC,
    _anticipo,
    _aprobar,
    _de,
    _generar,
    _leer,
    _proveedor,
    _recepcion,
    cuadra_el_comprobante,
)


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_29_no_se_le_cuelga_un_anticipo_al_productor_de_la_otra_quesera(
    client, base_datos
):
    """La Quesera A intenta registrarle un anticipo de $120.000,55 a un productor
    que es de la Quesera B. Si entra, ese anticipo queda con el NOMBRE del productor
    ajeno en la pantalla de A (fuga de un dato de la otra empresa) y suma en la
    cifra grande de anticipos de A sin pertenecerle a nadie."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prov_b = _proveedor(client, hb, "Ovidio T")

    r = client.post(ANT, json={
        "fecha": "2026-06-05", "valor": "120000.55", "tipo": "proveedor",
        "proveedor_id": prov_b["id"],
    }, headers=ha)
    print("ANTICIPO A CON PRODUCTOR DE B ->", r.status_code, r.text[:300])
    if r.status_code < 400:
        creado = client.get(f"{ANT}/{r.json()['id']}", headers=ha).json()
        print("SE LEE DESDE A COMO:", {
            k: creado.get(k) for k in ("valor", "proveedor_id", "proveedor_nombre",
                                       "tercero_nombre", "aplicado", "liquidacion_id")})
        print("SUMA DE ANTICIPOS DE A:", client.get(f"{ANT}/totales/suma", headers=ha).json())
        # Y no se le puede descontar a nadie: A liquida a SUS productores, y este
        # anticipo cuelga de uno que A no tiene. Queda suelto para siempre.
        prov_a = _proveedor(client, ha, "Ovidio T")
        _recepcion(client, ha, prov_a, "2026-06-02", "137.45", precio="1833.33")
        liq = _leer(client, ha, _de(_generar(client, ha, Q1), "Ovidio T")["id"])
        print("LIQUIDACION DE A — anticipos aplicados:", liq["anticipos"],
              "neto:", liq["neto_a_pagar"])
        suelto = client.get(f"{ANT}/{r.json()['id']}", headers=ha).json()
        print("EL ANTICIPO SIGUE SUELTO:", suelto["liquidacion_id"], suelto["aplicado"])

    # Lo mismo por el lado del transportador.
    rt = client.post("/api/v1/transportadores", json={
        "nombre": "Camión de B", "valor_transporte": "242.76"}, headers=hb)
    assert rt.status_code == 201, rt.text
    r2 = client.post(ANT, json={
        "fecha": "2026-06-05", "valor": "44230.07", "tipo": "transportador",
        "transportador_id": rt.json()["id"],
    }, headers=ha)
    print("ANTICIPO A CON TRANSPORTADOR DE B ->", r2.status_code)

    assert r.status_code >= 400 and r2.status_code >= 400, (
        "la Quesera A pudo colgarle anticipos ($120.000,55 al productor y $44.230,07 "
        "al transportador) a terceros de la Quesera B: la plata queda registrada como "
        "entregada, suma en la cifra grande de A y no se le descuenta nunca a nadie"
    )


def test_zzaudit_30_no_se_le_anota_leche_al_productor_de_la_otra_quesera(client, base_datos):
    """Lo mismo con la recepción diaria: 137,45 L x $1.833,33 = $251.991,21
    anotados por la Quesera A a un productor de la Quesera B."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prov_b = _proveedor(client, hb, "Ovidio T")
    r = client.post(REC, json={
        "fecha": "2026-06-02", "proveedor_id": prov_b["id"],
        "cantidad_litros": "137.45", "precio_litro": "1833.33",
    }, headers=ha)
    print("RECEPCION A CON PRODUCTOR DE B ->", r.status_code, r.text[:300])
    if r.status_code < 400:
        generadas = _generar(client, ha, Q1)
        print("LIQUIDACIONES DE A:", [
            (x.get("proveedor_nombre"), x["valor_total"]) for x in generadas])
    assert r.status_code >= 400, (
        "la Quesera A pudo anotarle 137,45 L a un productor de la Quesera B"
    )


def test_zzaudit_31_una_quesera_no_le_paga_ni_le_lee_la_liquidacion_a_la_otra(
    client, base_datos
):
    """Comprobante de $251.991,21 en la Quesera A: la B no lo ve, no lo aprueba,
    no lo paga, no le borra pagos y no lo anula."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prov_a = _proveedor(client, ha, "Henri C")
    _recepcion(client, ha, prov_a, "2026-06-02", "137.45", precio="1833.33")
    _anticipo(client, ha, "2026-06-03", "44230.07", proveedor=prov_a)
    liq = _de(_generar(client, ha, Q1), "Henri C")
    antes = _leer(client, ha, liq["id"])
    assert D(antes["neto_a_pagar"]) == D("207761.14"), antes["neto_a_pagar"]
    lid = liq["id"]

    puertas = {
        "leer": lambda: client.get(f"{API}/{lid}", headers=hb),
        "aprobar": lambda: client.post(f"{API}/{lid}/aprobar", headers=hb),
        "pagar": lambda: client.post(f"{API}/{lid}/pagar", headers=hb),
        "abonar": lambda: client.post(
            f"{API}/{lid}/pagos", json={"fecha": "2026-06-16", "valor": "1000.11"}, headers=hb
        ),
        "recalcular": lambda: client.post(f"{API}/{lid}/recalcular", headers=hb),
        "anular": lambda: client.post(f"{API}/{lid}/anular", headers=hb),
        "pdf": lambda: client.get(f"{API}/{lid}/pdf", headers=hb),
        "corregir observaciones": lambda: client.put(
            f"{API}/{lid}", json={"observaciones": "de la otra quesera"}, headers=hb
        ),
    }
    abiertas = []
    for nombre, disparo in puertas.items():
        resp = disparo()
        if resp.status_code < 400:
            abiertas.append(f"{nombre} -> {resp.status_code}")
    despues = _leer(client, ha, lid)
    for campo in ("estado", "valor_total", "anticipos", "neto_a_pagar", "pagado", "saldo"):
        assert despues[campo] == antes[campo], f"la B movió {campo} de la liquidación de A"
    cuadra_el_comprobante(despues, "tras el ataque de la otra quesera")
    assert not abiertas, f"la Quesera B alcanzó la liquidación de la A: {abiertas}"


def test_zzaudit_32_la_deuda_no_cruza_de_quesera(client, base_datos):
    """El productor de A queda debiendo $218.912,58. El productor del MISMO nombre
    en B no puede heredar ni un peso de esa deuda, ni al revés."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prov_a = _proveedor(client, ha, "Marleny R")
    prov_b = _proveedor(client, hb, "Marleny R")
    _recepcion(client, ha, prov_a, "2026-06-04", "44.23", precio="1833.33")
    _anticipo(client, ha, "2026-06-04", "300000.77", proveedor=prov_a)
    a1 = _leer(client, ha, _de(_generar(client, ha, Q1), "Marleny R")["id"])
    assert D(a1["le_queda_debiendo"]) == D("218912.58")
    _aprobar(client, ha, a1["id"])

    _recepcion(client, hb, prov_b, "2026-06-22", "137.45", precio="1833.33")
    b = _leer(client, hb, _de(
        _generar(client, hb, ("2026-06-16", "2026-06-30")), "Marleny R")["id"])
    assert D(b["saldo_anterior"]) == CERO, b["saldo_anterior"]
    assert D(b["neto_a_pagar"]) == D("251991.21"), b["neto_a_pagar"]
    assert b["deudas_cobradas"] == []
    cuadra_el_comprobante(b, "quincena de B")

    # El avance de B tampoco puede anunciar la deuda del productor de A.
    r = client.post(f"{API}/previsualizar", json={
        "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15",
        "tipo": "proveedor", "tercero_id": prov_b["id"],
    }, headers=hb)
    assert r.status_code == 200, r.text
    for avance in r.json():
        assert D(avance["deuda_pendiente"]) == CERO, avance["deuda_pendiente"]
