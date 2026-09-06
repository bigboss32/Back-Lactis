"""PUERTAS LATERALES: usuarios, roles, filtros por id ajeno, kardex, PDFs
por nombre, y el superadmin.

Lo que se busca: que B pueda auto-asignarse a A, editar un rol que A comparte,
o consultar por nombre/id un tercero de A.
"""
from tests.conftest import PASSWORD, auth_headers

V = "/api/v1"


def _r(out, tag, r):
    out.append((tag, r.status_code, r.text[:200]))
    return r


def test_usuarios_y_roles(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    empresa_a = str(base_datos["empresa_a"].id)
    empresa_b = str(base_datos["empresa_b"].id)
    admin_b_id = str(base_datos["admin_b"].id)
    admin_a_id = str(base_datos["admin_a"].id)
    out = []

    _r(out, "B ve el usuario de A", client.get(f"{V}/usuarios/{admin_a_id}", headers=hb))
    _r(out, "B edita el usuario de A",
       client.put(f"{V}/usuarios/{admin_a_id}", json={"nombre": "Hackeado"}, headers=hb))
    _r(out, "B bloquea el usuario de A",
       client.post(f"{V}/usuarios/{admin_a_id}/bloquear", json={}, headers=hb))
    _r(out, "B le restablece la clave al usuario de A",
       client.post(f"{V}/usuarios/{admin_a_id}/restablecer-password", json={}, headers=hb))
    _r(out, "B crea un usuario dentro de la empresa A",
       client.post(f"{V}/usuarios", json={
           "nombre": "Colado", "apellido": "DeB", "correo": "colado@test.local",
           "username": "colado.b", "password": PASSWORD, "empresa_id": empresa_a,
       }, headers=hb))
    _r(out, "B se auto-asigna a la empresa A",
       client.put(f"{V}/usuarios/{admin_b_id}/empresas", json={
           "membresias": [
               {"empresa_id": empresa_b, "rol_ids": []},
               {"empresa_id": empresa_a, "rol_ids": []},
           ],
       }, headers=hb))
    _r(out, "B mira las empresas del usuario de A",
       client.get(f"{V}/usuarios/{admin_a_id}/empresas", headers=hb))

    roles = client.get(f"{V}/roles", params={"page_size": 100}, headers=hb).json()
    roles_a = client.get(f"{V}/roles", params={"page_size": 100}, headers=ha).json()
    print("\n--- roles ---")
    print("B ve:", roles["total"], sorted(r["nombre"] for r in roles["items"]))
    print("A ve:", roles_a["total"], sorted(r["nombre"] for r in roles_a["items"]))
    ids_a = {r["id"] for r in roles_a["items"]}
    ids_b = {r["id"] for r in roles["items"]}
    print("ids de rol COMPARTIDOS entre las dos queseras:", len(ids_a & ids_b))
    if ids_a & ids_b:
        rol = sorted(ids_a & ids_b)[0]
        _r(out, "B edita un rol que tambien usa A",
           client.put(f"{V}/roles/{rol}", json={"descripcion": "tocado por B"}, headers=hb))
        _r(out, "B le cambia los permisos a un rol que tambien usa A",
           client.put(f"{V}/roles/{rol}/permisos", json={"permiso_ids": []}, headers=hb))
        _r(out, "B borra un rol que tambien usa A",
           client.delete(f"{V}/roles/{rol}", headers=hb))

    print("\n===== USUARIOS Y ROLES =====")
    for tag, code, body in out:
        marca = "  " if code >= 400 else "!!"
        print(f"{marca} {code}  {tag}   {body[:150]}")


def test_filtros_y_consultas_por_id_o_nombre_ajeno(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    out = []

    prov_a = client.post(f"{V}/proveedores", json={
        "nombre": "ProductorSECRETOA", "vereda": "V", "precio_litro": "1833.33",
    }, headers=ha).json()
    trans_a = client.post(f"{V}/transportadores", json={
        "nombre": "TransportadorSECRETOA", "valor_transporte": "242.76",
    }, headers=ha).json()
    for dia, litros in (("2026-07-01", "137.45"), ("2026-07-02", "144.23")):
        client.post(f"{V}/recepciones", json={
            "fecha": dia, "proveedor_id": prov_a["id"], "transportador_id": trans_a["id"],
            "cantidad_litros": litros, "precio_litro": "1833.33",
        }, headers=ha)
    cliente_a = client.post(f"{V}/clientes", json={"nombre": "ClienteSECRETOA"}, headers=ha).json()
    prod_a = client.post(f"{V}/inventario/productos", json={
        "nombre": "ProductoSECRETOA", "categoria": "insumo", "unidad": "kg", "costo_unitario": "4400.23",
    }, headers=ha).json()
    client.post(f"{V}/inventario/movimientos", json={
        "producto_id": prod_a["id"], "fecha": "2026-07-02", "tipo": "entrada",
        "cantidad": "137.45", "costo_unitario": "4400.23",
    }, headers=ha)
    client.post(f"{V}/reventa/compras", json={
        "fecha": "2026-07-02", "productor": "ProductorRevSECRETOA",
        "kilos_brutos": "44.23", "precio_kilo": "12500",
    }, headers=ha)
    client.post(f"{V}/reventa/ventas", json={
        "fecha": "2026-07-03", "cliente": "ClienteRevSECRETOA", "kilos": "22.11", "precio_kilo": "15000",
    }, headers=ha)

    _r(out, "kardex del producto de A", client.get(f"{V}/inventario/productos/{prod_a['id']}/kardex", headers=hb))
    _r(out, "liquidaciones filtradas por el proveedor de A",
       client.get(f"{V}/liquidaciones", params={"proveedor_id": prov_a["id"]}, headers=hb))
    _r(out, "cartera de transporte por el cliente de A",
       client.get(f"{V}/transporte/cartera/detalle", params={"cliente_id": cliente_a["id"]}, headers=hb))
    _r(out, "cartera de transporte por el NOMBRE del cliente de A",
       client.get(f"{V}/transporte/cartera/detalle", params={"cliente_nombre": "ClienteSECRETOA"}, headers=hb))
    _r(out, "estado de cuenta de reventa por el cliente de A",
       client.get(f"{V}/reventa/estado-cuenta",
                  params={"cliente": "ClienteRevSECRETOA", "desde": "2026-07-01", "hasta": "2026-07-31"},
                  headers=hb))
    _r(out, "estado de cuenta de productor de reventa de A",
       client.get(f"{V}/reventa/estado-cuenta-productor",
                  params={"productor": "ProductorRevSECRETOA", "desde": "2026-07-01", "hasta": "2026-07-31"},
                  headers=hb))
    _r(out, "PDF del estado de cuenta de reventa del cliente de A",
       client.get(f"{V}/reventa/estado-cuenta/pdf",
                  params={"cliente": "ClienteRevSECRETOA", "desde": "2026-07-01", "hasta": "2026-07-31"},
                  headers=hb))
    _r(out, "PDF del estado de cuenta del productor de reventa de A",
       client.get(f"{V}/reventa/estado-cuenta-productor/pdf",
                  params={"productor": "ProductorRevSECRETOA", "desde": "2026-07-01", "hasta": "2026-07-31"},
                  headers=hb))
    _r(out, "previsualizar PDF de un tercero de A",
       client.post(f"{V}/liquidaciones/previsualizar/pdf", json={
           "periodo_inicio": "2026-07-01", "periodo_fin": "2026-07-15",
           "tipo": "proveedor", "tercero_id": prov_a["id"],
       }, headers=hb))
    _r(out, "busqueda por el nombre de A en recepciones",
       client.get(f"{V}/recepciones", params={"search": "SECRETOA"}, headers=hb))
    _r(out, "busqueda por el nombre de A en reventa/compras",
       client.get(f"{V}/reventa/compras", params={"search": "SECRETOA"}, headers=hb))
    _r(out, "sugerencias de reventa (autocompletado de terceros)",
       client.get(f"{V}/reventa/sugerencias", headers=hb))
    _r(out, "conductores sugeridos", client.get(f"{V}/ventas/conductores/sugerencias", headers=hb))

    print("\n===== FILTROS Y CONSULTAS POR ID/NOMBRE AJENO =====")
    for tag, code, body in out:
        fuga = code < 400 and "SECRETOA" in body
        marca = "!!" if fuga else "  "
        print(f"{marca} {code}  {tag}   {body[:170]}")


def test_superadmin(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hs = auth_headers(client, "superadmin")
    empresa_a = str(base_datos["empresa_a"].id)
    empresa_b = str(base_datos["empresa_b"].id)
    client.post(f"{V}/proveedores", json={
        "nombre": "ProductorSECRETOA", "vereda": "V", "precio_litro": "1833.33",
    }, headers=ha)
    client.post(f"{V}/recepciones", json={
        "fecha": "2026-07-01",
        "proveedor_id": client.get(f"{V}/proveedores", headers=ha).json()["items"][0]["id"],
        "cantidad_litros": "137.45", "precio_litro": "1833.33",
    }, headers=ha)

    out = []
    for url, params in (
        ("/reportes/dashboard", {}),
        ("/recepciones", {}),
        ("/proveedores", {}),
        ("/liquidaciones", {}),
        ("/contabilidad/balance", {"fecha": "2026-07-31"}),
        ("/reventa/resumen", {"desde": "2026-07-01", "hasta": "2026-07-31"}),
        ("/auditoria", {}),
    ):
        _r(out, f"superadmin SIN header {url}", client.get(f"{V}{url}", params=params, headers=hs))
    _r(out, "superadmin con header de B ve /recepciones",
       client.get(f"{V}/recepciones", headers={**hs, "X-Empresa-Id": empresa_b}))
    _r(out, "superadmin con header de A ve /recepciones",
       client.get(f"{V}/recepciones", headers={**hs, "X-Empresa-Id": empresa_a}))

    print("\n===== SUPERADMIN =====")
    for tag, code, body in out:
        fuga = code < 400 and "SECRETOA" in body and "de B" in tag
        marca = "!!" if fuga else "  "
        print(f"{marca} {code}  {tag}   {body[:160]}")
