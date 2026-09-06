"""¿QUIEN ENTRO? El registro de ingresos y los catalogos sembrados.

El barrido dejo tres cifras vivas en una empresa vacia: /auditoria/logins,
/tipos-queso y /categorias-gasto. Se miran una por una.
"""
from tests.conftest import auth_headers

V = "/api/v1"


def test_logins_de_la_otra_quesera(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    la = client.get(f"{V}/auditoria/logins", headers=ha).json()
    lb = client.get(f"{V}/auditoria/logins", headers=hb).json()
    print("\n--- /auditoria/logins ---")
    print("A ve total:", la["total"], [i.get("username") or i.get("usuario") for i in la["items"]])
    print("B ve total:", lb["total"], [i.get("username") or i.get("usuario") for i in lb["items"]])
    print("crudo B:", lb["items"])

    usuarios_en_b = {str(i) for i in lb["items"]}
    print("¿'admin.a' aparece en lo que ve B?", any("admin.a" in s for s in usuarios_en_b))


def test_auditoria_general_de_la_otra_quesera(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    client.post(
        f"{V}/proveedores",
        json={"nombre": "ProductorSECRETOA", "vereda": "V", "precio_litro": "1833.33"},
        headers=ha,
    )
    aud = client.get(f"{V}/auditoria", headers=hb).json()
    print("\n--- /auditoria como B ---  total:", aud["total"])
    print("¿SECRETOA en el rastro que ve B?", "SECRETOA" in str(aud))


def test_catalogos_sembrados(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    client.post(f"{V}/tipos-queso", json={"nombre": "QuesoSECRETOA"}, headers=ha)
    client.post(f"{V}/categorias-gasto", json={"nombre": "CategoriaSECRETOA"}, headers=ha)

    for url in ("/tipos-queso", "/categorias-gasto"):
        a = client.get(f"{V}{url}", params={"page_size": 100}, headers=ha).json()
        b = client.get(f"{V}{url}", params={"page_size": 100}, headers=hb).json()
        print(f"\n--- {url} ---")
        print("A:", a["total"], sorted(i["nombre"] for i in a["items"]))
        print("B:", b["total"], sorted(i["nombre"] for i in b["items"]))
        ids_a = {i["id"] for i in a["items"]}
        ids_b = {i["id"] for i in b["items"]}
        print("ids compartidos:", ids_a & ids_b)
        print("¿SECRETOA visible para B?", any("SECRETOA" in i["nombre"] for i in b["items"]))
