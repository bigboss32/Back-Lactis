"""LA VISTA PREVIA DE CORREGIR DICE LA DEUDA CON EL RÓTULO DEL PAPEL.

"Se le pagó de más" es una frase con plata adentro: "esa plata ya salió de la caja en
efectivo". La vista previa la decía siempre que el saldo quedaba negativo, y el PDF de la
misma quincena, ya corregida, decía otra cosa:

  · «LAS DOS». 250 L × $2.000 = $500.000 con $300.000 de anticipo, pagada con $200.000.
    Se corrige el precio a $1.000: valor $250.000, neto −$50.000, pagado $200.000, saldo
    −$250.000. El papel cierra en "LE QUEDA DEBIENDO $250.000"; la vista previa mandaba
    `se_le_pago_de_mas` = $250.000 y "esa plata ya salió de la caja en efectivo", cuando
    en efectivo salieron $200.000. Los otros $50.000 son anticipo que pasó del valor.
  · «ANTICIPOS». 90 L × $2.000 = $180.000 contra $180.000 de anticipo, cerrada con Pagar
    sin un peso de efectivo. Corregida a $1.500: valor $135.000, neto −$45.000, pagado $0.
    Ahí no salió ni un peso por pagos, y la vista previa decía igual "se le pagó de más".

La pregunta es ahora una sola para el papel y la vista previa (`lo_que_se_le_pago_de_mas`),
y la deuda que no es efectivo va en su propio campo (`le_queda_debiendo`).

Regla de oro en cada fila: neto = valor_total − anticipos − saldo_anterior;
saldo = neto − pagado.
"""
import io
from decimal import Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

V = "/api/v1"
API = f"{V}/liquidaciones"
EFECTIVO = "Esa plata ya salió de la caja en efectivo"
OMITIDAS = "Mientras esa deuda no se le cobre"


def D(v):
    return Decimal(str(v))


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def _cuadra(fila):
    neto = D(fila["valor_total"]) - D(fila["anticipos"]) - D(fila["saldo_anterior"] or 0)
    assert D(fila["neto_a_pagar"]) == neto
    assert D(fila["saldo"]) == neto - D(fila["pagado"])
    return fila


def _papel(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
    return " ".join(crudo.split())


def _pagada(client, h, nombre, litros, anticipo, pagar_con=None):
    """Genera, aprueba y cierra la quincena del 01/06 al 15/06 de un proveedor a $2.000."""
    prov = _ok(client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": "2000"}, headers=h), 201)
    _ok(client.post(f"{V}/recepciones", json={
        "fecha": "2026-06-02", "proveedor_id": prov["id"], "cantidad_litros": litros},
        headers=h), 201)
    _ok(client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-06-03",
        "valor": anticipo}, headers=h), 201)
    generadas = _ok(client.post(f"{API}/generar", json={
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"},
        headers=h))["generadas"]
    liq = next(x for x in generadas if x["proveedor_id"] == prov["id"])
    _ok(client.post(f"{API}/{liq['id']}/aprobar", headers=h))
    pagada = _cuadra(_ok(client.post(f"{API}/{liq['id']}/pagar", headers=h)))
    assert pagada["estado"] == "pagada"
    return pagada


def _cuerpo(pagada, precio):
    detalle = [d for d in pagada["detalles"] if d.get("deleted_at") is None][0]
    return {"motivo": f"el precio era ${precio} el litro",
            "precios": [{"detalle_id": detalle["id"], "precio_litro": precio}]}


def test_las_dos_la_vista_previa_dice_le_queda_debiendo_como_el_papel(client, base_datos):
    h = auth_headers(client, "admin.a")
    pagada = _pagada(client, h, "Las Dos", "250", "300000")
    # 500.000 − 300.000 = 200.000 de neto, entregados en efectivo.
    assert (D(pagada["valor_total"]), D(pagada["neto_a_pagar"]), D(pagada["pagado"])) == (
        D(500000), D(200000), D(200000))

    cuerpo = _cuerpo(pagada, "1000")
    previa = _ok(client.post(f"{API}/{pagada['id']}/corregir/previsualizar", json=cuerpo,
                             headers=h))
    print(f"\n  avisos: {previa['avisos']}")
    # 250 L × $1.000 = 250.000 − 300.000 = −50.000 de neto; −50.000 − 200.000 = −250.000.
    assert (D(previa["valor_total_despues"]), D(previa["neto_despues"]),
            D(previa["saldo_despues"])) == (D(250000), D(-50000), D(-250000))
    assert D(previa["le_queda_debiendo"]) == D(250000)
    assert D(previa["se_le_pago_de_mas"]) == D(0)
    assert D(previa["queda_por_entregar"]) == D(0)
    # Los tres campos cuadran con el saldo: 0 − 0 − 250.000 = −250.000.
    assert (D(previa["queda_por_entregar"]) - D(previa["se_le_pago_de_mas"])
            - D(previa["le_queda_debiendo"])) == D(previa["saldo_despues"])
    # El aviso nombra el efectivo que de verdad salió y lo que pusieron los anticipos:
    # 200.000 + 50.000 = 250.000, y 300.000 − 250.000 = 50.000.
    assert previa["avisos"][0] == (
        "Le queda debiendo $250.000: ya se le habían entregado $200.000, y además los "
        "anticipos aplicados ($300.000) pasan en $50.000 del valor total de la quincena "
        "corregida ($250.000). Esa deuda se le descuenta de la quincena siguiente; si el "
        "productor deja de entregar leche, no vuelve")
    assert not any(EFECTIVO in a or "pagó de más" in a for a in previa["avisos"])
    assert any(a.startswith(OMITIDAS) for a in previa["avisos"])

    # Se corrige de verdad: el papel dice el mismo rótulo, por la misma cifra.
    corregida = _cuadra(_ok(client.post(f"{API}/{pagada['id']}/corregir", json=cuerpo,
                                        headers=h)))
    assert D(corregida["le_queda_debiendo"]) == D(previa["le_queda_debiendo"])
    papel = _papel(client, h, pagada["id"])
    assert "LE QUEDA DEBIENDO $250.000" in papel
    assert "SE LE PAGÓ DE MÁS" not in papel


def test_solo_anticipos_tampoco_dice_que_salio_efectivo(client, base_datos):
    h = auth_headers(client, "admin.a")
    pagada = _pagada(client, h, "Solo Anticipos", "90", "180000")
    # 180.000 − 180.000 = 0: Pagar la cerró sin un peso de efectivo.
    assert (D(pagada["neto_a_pagar"]), D(pagada["pagado"])) == (D(0), D(0))

    previa = _ok(client.post(f"{API}/{pagada['id']}/corregir/previsualizar",
                             json=_cuerpo(pagada, "1500"), headers=h))
    # 90 L × $1.500 = 135.000 − 180.000 = −45.000, sin efectivo encima.
    assert (D(previa["neto_despues"]), D(previa["saldo_despues"])) == (D(-45000), D(-45000))
    assert (D(previa["le_queda_debiendo"]), D(previa["se_le_pago_de_mas"])) == (
        D(45000), D(0))
    assert previa["avisos"][0] == (
        "Le queda debiendo $45.000: los anticipos aplicados ($180.000) pasan del valor "
        "total de la quincena corregida ($135.000). Esa deuda se le descuenta de la "
        "quincena siguiente; si el productor deja de entregar leche, no vuelve")
    assert not any(EFECTIVO in a for a in previa["avisos"])


def test_control_el_efectivo_entregado_de_mas_sigue_siendo_se_le_pago_de_mas(
        client, base_datos):
    """250 L × $2.000 = $500.000 con $100.000 de anticipo, pagada con $400.000 en
    efectivo; corregida a $1.600: $400.000 − $100.000 = $300.000 de neto, y se le
    entregaron $400.000. Los $100.000 de más salieron TODOS de la caja."""
    h = auth_headers(client, "admin.a")
    pagada = _pagada(client, h, "De Mas", "250", "100000")
    # 250 L × $2.000 = 500.000 − 100.000 = 400.000 entregados.
    assert D(pagada["pagado"]) == D(400000)

    cuerpo = _cuerpo(pagada, "1600")
    previa = _ok(client.post(f"{API}/{pagada['id']}/corregir/previsualizar", json=cuerpo,
                             headers=h))
    # 250 × 1.600 = 400.000 − 100.000 = 300.000 de neto; 300.000 − 400.000 = −100.000.
    assert (D(previa["neto_despues"]), D(previa["saldo_despues"])) == (D(300000), D(-100000))
    assert (D(previa["se_le_pago_de_mas"]), D(previa["le_queda_debiendo"])) == (
        D(100000), D(0))
    assert previa["avisos"][0].startswith(f"Se le pagó de más. {EFECTIVO}")

    _cuadra(_ok(client.post(f"{API}/{pagada['id']}/corregir", json=cuerpo, headers=h)))
    papel = _papel(client, h, pagada["id"])
    assert "SE LE PAGÓ DE MÁS $100.000" in papel
    assert "LE QUEDA DEBIENDO" not in papel
