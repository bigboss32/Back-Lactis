"""EL ANTICIPO DE B A UN TERCERO DE A.

POST /anticipos no mira la empresa del proveedor/transportador/empleado.
Se prueba: (1) ¿que ve B?, (2) ¿el anticipo de B le baja plata a la
liquidacion de A?, (3) ¿sale el nombre ajeno en la suma y en el listado?
"""
import io
from decimal import Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

V = "/api/v1"


def D(x):
    return Decimal(str(x))


def _escenario(client, ha):
    prov = client.post(
        f"{V}/proveedores",
        json={"nombre": "ProductorSECRETOA", "vereda": "El Retorno", "precio_litro": "1833.33"},
        headers=ha,
    ).json()
    trans = client.post(
        f"{V}/transportadores",
        json={"nombre": "TransportadorSECRETOA", "valor_transporte": "242.76"},
        headers=ha,
    ).json()
    for dia, litros in (("2026-07-01", "137.45"), ("2026-07-02", "144.23")):
        client.post(f"{V}/recepciones", json={
            "fecha": dia, "proveedor_id": prov["id"], "transportador_id": trans["id"],
            "cantidad_litros": litros, "precio_litro": "1833.33",
        }, headers=ha)
    return prov, trans


def test_anticipo_de_b_a_un_tercero_de_a(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    prov_a, trans_a = _escenario(client, ha)

    # --- B crea el anticipo apuntando al productor de A
    r = client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": prov_a["id"], "fecha": "2026-07-02",
        "valor": "242760.55", "observaciones": "AnticipoDeB",
    }, headers=hb)
    print("POST /anticipos con proveedor de A ->", r.status_code)
    assert r.status_code == 201, r.text
    anticipo_b = r.json()
    print("  empresa_id del anticipo:", anticipo_b["empresa_id"])
    print("  tercero_nombre que le muestra a B:", anticipo_b["tercero_nombre"])
    print("  proveedor_nombre que le muestra a B:", anticipo_b["proveedor_nombre"])

    # --- El listado de B
    lista = client.get(f"{V}/anticipos", headers=hb).json()
    nombres = [i["tercero_nombre"] for i in lista["items"]]
    print("Listado de anticipos de B ->", lista["total"], nombres)
    print("¿el nombre de A esta en la pantalla de B?", any(n and "SECRETOA" in n for n in nombres))

    # --- La busqueda por nombre: ¿B puede buscar por el nombre ajeno?
    busq = client.get(f"{V}/anticipos", params={"search": "SECRETOA"}, headers=hb)
    print("Busqueda 'SECRETOA' en anticipos de B ->", busq.status_code,
          busq.json().get("total") if busq.status_code == 200 else busq.text[:120])

    suma_b = client.get(f"{V}/anticipos/totales/suma", headers=hb).json()
    print("Suma de anticipos de B:", suma_b)

    # --- ¿La liquidacion de A se come el anticipo de B?
    gen = client.post(f"{V}/liquidaciones/generar", json={
        "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15", "tipo": "proveedor",
    }, headers=ha)
    assert gen.status_code == 200, gen.text
    generadas = gen.json()["generadas"]
    assert generadas, gen.text
    liq = client.get(f"{V}/liquidaciones/{generadas[0]['id']}", headers=ha).json()
    print("\nLiquidacion de A -> llaves:", sorted(liq.keys()))
    campos_anticipo = {k: v for k, v in liq.items() if "anticipo" in k.lower()}
    print("  campos de anticipo:", campos_anticipo)
    print("  cifras:", {k: v for k, v in liq.items()
                        if isinstance(v, (str, int, float)) and any(
                            t in k for t in ("total", "neto", "bruto", "valor", "saldo"))})
    contaminada = any(
        D(v or 0) != 0 for v in campos_anticipo.values() if isinstance(v, (str, int, float))
    )
    print("¿LA LIQUIDACION DE A DESCONTO EL ANTICIPO DE B?", contaminada)

    pdf = client.get(f"{V}/liquidaciones/{generadas[0]['id']}/pdf", headers=ha)
    texto = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf.content)).pages)
    print("¿'AnticipoDeB' impreso en el comprobante de A?", "AnticipoDeB" in texto)
    print("¿'242.760,55' impreso en el comprobante de A?", "242.760,55" in texto)

    # --- El anticipo de B sigue en B, sin liquidacion
    lista2 = client.get(f"{V}/anticipos", headers=hb).json()
    print("anticipo de B tras generar A -> aplicado:", lista2["items"][0]["aplicado"],
          "liquidacion_id:", lista2["items"][0]["liquidacion_id"])


def test_recepcion_con_sucursal_ajena(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    suc_a = client.post(f"{V}/sucursales", json={"nombre": "SucursalSECRETOA"}, headers=ha).json()
    prov_b = client.post(f"{V}/proveedores", json={
        "nombre": "ProdB", "vereda": "V", "precio_litro": "1700",
    }, headers=hb).json()
    r = client.post(f"{V}/recepciones", json={
        "fecha": "2026-07-05", "proveedor_id": prov_b["id"], "cantidad_litros": "137.45",
        "precio_litro": "1700", "sucursal_id": suc_a["id"],
    }, headers=hb)
    print("\nPOST /recepciones con sucursal de A ->", r.status_code)
    assert r.status_code == 201, r.text
    rec = r.json()
    print("  sucursal_id guardada:", rec.get("sucursal_id"), "== la de A:", rec.get("sucursal_id") == suc_a["id"])
    # ¿sale el nombre por algun lado?
    det = client.get(f"{V}/recepciones/{rec['id']}", headers=hb).text
    print("  ¿'SucursalSECRETOA' en el detalle que ve B?", "SECRETOA" in det)
    grilla = client.get(f"{V}/recepciones/grilla/quincena",
                        params={"desde": "2026-07-01", "hasta": "2026-07-15"}, headers=hb).text
    print("  ¿'SucursalSECRETOA' en la grilla de B?", "SECRETOA" in grilla)
    # ¿la ve A en sus propias consultas filtradas por sucursal?
    filtrado = client.get(f"{V}/recepciones", params={"sucursal_id": suc_a["id"]}, headers=ha)
    print("  A filtra sus recepciones por SU sucursal ->", filtrado.status_code,
          filtrado.json().get("total") if filtrado.status_code == 200 else filtrado.text[:120])


def test_producto_de_b_apuntando_a_un_tipo_de_queso_de_a(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    tipo_a = client.post(f"{V}/tipos-queso", json={"nombre": "QuesoSECRETOA"}, headers=ha).json()
    r = client.post(f"{V}/inventario/productos", json={
        "nombre": "ProductoDeB", "categoria": "producto_terminado", "unidad": "kg",
        "tipo_queso_id": tipo_a["id"], "costo_unitario": "4400.23",
    }, headers=hb)
    print("\nPOST /inventario/productos con tipo_queso de A ->", r.status_code)
    assert r.status_code == 201, r.text
    prod_b = r.json()
    print("  tipo_queso_id guardado:", prod_b.get("tipo_queso_id"), "== el de A:",
          prod_b.get("tipo_queso_id") == tipo_a["id"])
    listado = client.get(f"{V}/inventario/productos", headers=hb).text
    print("  ¿'QuesoSECRETOA' en el listado de productos de B?", "SECRETOA" in listado)
    lotes = client.get(f"{V}/produccion/lotes", headers=hb)
    print("  /produccion/lotes de B ->", lotes.status_code,
          "SECRETOA" in lotes.text if lotes.status_code == 200 else lotes.text[:120])
