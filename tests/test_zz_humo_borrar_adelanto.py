"""EL CALLEJON SIN SALIDA QUE SE ABRIO: el adelanto que nunca existio.

El caso real: el adelanto se digito DOS VECES. De la caja salieron $300.000, no
$600.000. El sobrante hay que borrarlo — y con la marca del adelanto soltado puesta,
antes no habia ninguna pantalla que pudiera:

  · la de Anticipos lo rebota, porque ya salio impreso;
  · "espere a la quincena siguiente" no sirve si el productor dejo de entregar leche;
  · "vuelva a incluirlo con Corregir" lo mete DENTRO de una pagada, y de ahi tampoco.

Ese fantasma se le habria descontado al productor de plata que SI era suya.
"""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
V = "/api/v1"


def test_humo_el_adelanto_que_nunca_existio_se_puede_borrar(client, base_datos):
    h = auth_headers(client, "admin.a")
    prov = client.post(f"{V}/proveedores", json={
        "nombre": "Libardo", "vereda": "El Roble", "precio_litro": "2000"}, headers=h).json()
    client.post(f"{V}/recepciones", json={
        "fecha": "2026-06-02", "proveedor_id": prov["id"], "cantidad_litros": "400"}, headers=h)
    # EL MISMO ADELANTO, DIGITADO DOS VECES. De la caja salieron $300.000.
    for _ in range(2):
        client.post(f"{V}/anticipos", json={
            "tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-06-03",
            "valor": "300000", "observaciones": "para la droga"}, headers=h)
    gen = client.post(f"{API}/generar", json={
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15",
        "tipo": "proveedor"}, headers=h).json()["generadas"]
    liq = next(x for x in gen if x["proveedor_id"] == prov["id"])
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h).json()
    print("\n=== PAGADA con el adelanto duplicado ===", "total", pagada["valor_total"],
          "anticipos", pagada["anticipos"], "neto", pagada["neto_a_pagar"])
    assert Decimal(pagada["anticipos"]) == Decimal("600000.00")
    assert Decimal(pagada["neto_a_pagar"]) == Decimal("200000.00")

    # 1) El dueno lo saca de la quincena creyendo que asi lo quita
    aplicados = client.post(f"{API}/{liq['id']}/corregir/previsualizar",
                            json={"motivo": "mirar"}, headers=h).json()["anticipos_aplicados"]
    sobrante = aplicados[0]["anticipo_id"]
    client.post(f"{API}/{liq['id']}/corregir", json={
        "motivo": "el adelanto quedo digitado dos veces",
        "anticipos_a_soltar": [sobrante]}, headers=h)

    # 2) Y la pantalla de anticipos lo rebota, con razon: ya salio impreso
    borrar = client.delete(f"{V}/anticipos/{sobrante}", headers=h)
    print("=== borrar desde Anticipos ===", borrar.status_code)
    assert borrar.status_code == 422

    # 3) LA SALIDA QUE ANTES NO EXISTIA: sale en la lista de "los que esta quincena solto"
    prev = client.post(f"{API}/{liq['id']}/corregir/previsualizar",
                       json={"motivo": "mirar"}, headers=h).json()
    soltados = prev["anticipos_soltados_por_esta"]
    print("=== los que esta quincena solto ===",
          [(a["fecha"], a["valor"]) for a in soltados])
    assert len(soltados) == 1 and soltados[0]["anticipo_id"] == sobrante
    assert soltados[0]["aviso"]

    hecho = client.post(f"{API}/{liq['id']}/corregir", json={
        "motivo": "ese adelanto nunca existio, quedo digitado dos veces",
        "anticipos_a_borrar": [sobrante]}, headers=h)
    assert hecho.status_code == 200, hecho.text
    d = hecho.json()
    print("=== BORRADO ===", "anticipos", d["anticipos"], "neto", d["neto_a_pagar"],
          "pagado", d["pagado"], "saldo", d["saldo"], "version", d["version"])
    assert Decimal(d["neto_a_pagar"]) == Decimal(d["pagado"]) + Decimal(d["saldo"])

    # 4) Y EL FANTASMA YA NO SE LE DESCUENTA A NADIE
    assert client.get(f"{V}/anticipos/{sobrante}", headers=h).status_code == 404
    sigue = client.post(f"{API}/{liq['id']}/corregir/previsualizar",
                        json={"motivo": "mirar"}, headers=h).json()
    assert sigue["anticipos_soltados_por_esta"] == []
    assert all(a["anticipo_id"] != sobrante for a in sigue["anticipos_sueltos"])

    # 5) El papel lo dice: ese adelanto se anulo
    corr = client.get(f"{API}/{liq['id']}/correcciones", headers=h).json()
    ultima = max(corr, key=lambda x: x["version_nueva"])
    print("=== RENGLON ===", ultima["anticipos_cambiados"])
    assert ultima["anticipos_cambiados"][0]["accion"] == "borrado"

    import io as _io
    from pypdf import PdfReader
    pdf = client.get(f"{API}/{liq['id']}/pdf", headers=h)
    t = "\n".join(p.extract_text() for p in PdfReader(_io.BytesIO(pdf.content)).pages)
    print("=== el papel dice que se anulo? ===", "ANUL" in t.upper())
    assert "ANUL" in t.upper()
