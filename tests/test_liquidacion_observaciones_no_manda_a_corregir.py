"""LAS OBSERVACIONES DE UN COMPROBANTE EMITIDO NO MANDAN A 'CORREGIR ESTA QUINCENA'.

100 L × $1.800 = $180.000 − $50.000 de adelanto = $130.000, pagada con Pagar. El dueño
quiere arreglar un error de digitación en la nota del comprobante. El 422 le decía "Use
'Corregir esta quincena'", y Corregir no trae observaciones: con solo el motivo rebota
"no hay nada que corregir". Era un consejo que siempre falla.

Ahora dice lo que es —la nota de un comprobante emitido no se cambia— y nombra Corregir
solo para cuando lo que está mal es una cifra, y solo si Corregir la acepta.
"""
from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _leer

V = "/api/v1"
API = f"{V}/liquidaciones"
REC = f"{V}/recepciones"


def _pagada(client, h, nombre):
    prov = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                                 "precio_litro": "1800"},
                       headers=h).json()["id"]
    assert client.post(REC, json={"fecha": "2026-06-02", "proveedor_id": prov,
                                  "cantidad_litros": "100"}, headers=h).status_code == 201
    assert client.post(f"{V}/anticipos", json={"tipo": "proveedor", "proveedor_id": prov,
                                               "fecha": "2026-06-01", "valor": "50000"},
                       headers=h).status_code == 201
    g = client.post(f"{API}/generar", json={"periodo_inicio": "2026-06-01",
                                            "periodo_fin": "2026-06-15",
                                            "tipo": "proveedor"}, headers=h).json()
    liq = next(x for x in g["generadas"] if x["proveedor_id"] == prov)["id"]
    assert client.post(f"{API}/{liq}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq}/pagar", headers=h).status_code == 200
    return prov, liq


def test_la_nota_no_se_cambia_y_corregir_solo_se_nombra_para_una_cifra(
        client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, liq = _pagada(client, h, "Nota Mala")
    obs = client.put(f"{API}/{liq}", json={"observaciones": "nota corregida"}, headers=h)
    texto = _detalle(obs)
    print(f"\n  PUT observaciones -> {obs.status_code} {texto!r}")
    assert obs.status_code == 422
    assert texto == (
        "Esta quincena ya se pagó: sus observaciones se imprimen en el comprobante ya "
        "emitido, y las de un comprobante emitido no se cambian. Si lo que está mal es "
        "una cifra, use 'Corregir esta quincena', que deja escrito el motivo y sube la "
        "versión del papel")
    # Ya no manda a Corregir PARA LA NOTA: con solo el motivo, Corregir rebota.
    assert "Use 'Corregir esta quincena'" not in texto
    solo_motivo = client.post(f"{API}/{liq}/corregir", json={
        "motivo": "la nota del comprobante quedó mal escrita"}, headers=h)
    assert solo_motivo.status_code == 422
    assert "no hay nada que corregir" in _detalle(solo_motivo)
    # Y para UNA CIFRA la salida existe: un día olvidado se deja previsualizar.
    olvidado = client.post(REC, json={"fecha": "2026-06-05", "proveedor_id": prov,
                                      "cantidad_litros": "20"}, headers=h).json()["id"]
    prev = client.post(f"{API}/{liq}/corregir/previsualizar", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}, headers=h)
    assert prev.status_code == 200, prev.text
    assert _leer(client, h, liq)["observaciones"] is None
