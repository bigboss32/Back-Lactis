"""LOS SOPORTES: ¿quien puede ver la foto del gasto de la otra quesera?

Los adjuntos de gastos, logos y fotos se guardan en uploads/<empresa_id>/... y
`/uploads` esta montado como archivos estaticos. Se mide: (1) ¿se puede adjuntar
a un documento ajeno?, (2) ¿el enlace pide sesion?
"""
import io

from tests.conftest import auth_headers

V = "/api/v1"


def _archivo(nombre="soporte.png"):
    # PNG minimo valido
    datos = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return {"file": (nombre, io.BytesIO(datos), "image/png")}


def test_adjuntos_a_documentos_ajenos(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    empresa_a = str(base_datos["empresa_a"].id)
    admin_a = str(base_datos["admin_a"].id)

    cat_a = client.post(f"{V}/categorias-gasto", json={"nombre": "CatA"}, headers=ha).json()
    gasto_a = client.post(f"{V}/gastos", json={
        "fecha": "2026-07-05", "categoria_id": cat_a["id"], "concepto": "GastoSECRETOA",
        "valor": "242760.75",
    }, headers=ha).json()
    veh_a = client.post(f"{V}/transporte/vehiculos", json={
        "placa": "AAA111", "nombre": "CamionA",
    }, headers=ha).json()
    vg_a = client.post(f"{V}/transporte/gastos", json={
        "fecha": "2026-07-02", "categoria": "combustible", "valor": "242760.50",
        "vehiculo_id": veh_a["id"],
    }, headers=ha).json()

    out = []
    for tag, url in (
        ("adjuntar a un gasto de A", f"/gastos/{gasto_a['id']}/adjunto"),
        ("adjuntar a un gasto de transporte de A", f"/transporte/gastos/{vg_a['id']}/adjunto"),
        ("cambiarle la foto al usuario de A", f"/usuarios/{admin_a}/foto"),
        ("cambiarle el logo a la empresa A", f"/empresas/{empresa_a}/logo"),
    ):
        r = client.post(f"{V}{url}", files=_archivo(), headers=hb)
        out.append((tag, r.status_code, r.text[:150]))

    print("\n===== ADJUNTAR A DOCUMENTOS AJENOS (como B) =====")
    for tag, code, body in out:
        marca = "  " if code >= 400 else "!!"
        print(f"{marca} {code}  {tag}   {body}")


def test_el_enlace_del_soporte_no_pide_sesion(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    cat_a = client.post(f"{V}/categorias-gasto", json={"nombre": "CatA"}, headers=ha).json()
    gasto_a = client.post(f"{V}/gastos", json={
        "fecha": "2026-07-05", "categoria_id": cat_a["id"], "concepto": "GastoSECRETOA",
        "valor": "242760.75",
    }, headers=ha).json()
    sub = client.post(f"{V}/gastos/{gasto_a['id']}/adjunto", files=_archivo(), headers=ha)
    print("\nA sube el soporte de su gasto ->", sub.status_code, sub.text[:200])
    if sub.status_code >= 400:
        print("no se pudo subir; sin evidencia")
        return
    url = sub.json().get("adjunto_url")
    print("adjunto_url guardada:", url)
    if not url:
        print("la respuesta no trae adjunto_url; sin evidencia")
        return

    ruta = url if url.startswith("/") else f"/uploads/{url}"
    sin_sesion = client.get(ruta)
    print(f"GET {ruta} SIN token ->", sin_sesion.status_code, len(sin_sesion.content), "bytes")
    como_b = client.get(ruta, headers=hb)
    print(f"GET {ruta} como la quesera B ->", como_b.status_code, len(como_b.content), "bytes")
    print("¿EL SOPORTE DE A SE BAJA SIN SESION?", sin_sesion.status_code == 200)
    print("¿Y LA RUTA LLEVA EL empresa_id DE A?", str(base_datos["empresa_a"].id) in ruta)
