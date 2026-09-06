"""LOS SOPORTES: ¿quien puede ver la foto del gasto de la otra quesera?

Los adjuntos de gastos de vehiculo, logos y fotos se guardan en
uploads/<empresa_id>/... y `/uploads` esta montado como archivos estaticos. Se
mide: (1) ¿se puede adjuntar a un documento ajeno?, (2) ¿el enlace pide sesion?

LA FACTURA DEL GASTO YA NO ESTA EN ESA LISTA. Cuando se escribio este archivo si
estaba, y era el caso mas grave: la factura trae el NIT del proveedor y el valor.
Se movio al bucket privado (ver AdjuntoGasto y
tests/test_gastos_total_y_facturas.py), asi que aqui quedan LOS TRES QUE SIGUEN
ABIERTOS, y el primer test comprueba ademas que la puerta vieja de gastos no
volvio a aparecer.
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


def _gasto_de_vehiculo(client, ha):
    veh_a = client.post(f"{V}/transporte/vehiculos", json={
        "placa": "AAA111", "nombre": "CamionA",
    }, headers=ha).json()
    return client.post(f"{V}/transporte/gastos", json={
        "fecha": "2026-07-02", "categoria": "combustible", "valor": "242760.50",
        "vehiculo_id": veh_a["id"],
    }, headers=ha).json()


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
    vg_a = _gasto_de_vehiculo(client, ha)

    out = []
    for tag, url in (
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

    # LA PUERTA VIEJA DE GASTOS TIENE QUE SEGUIR CERRADA. Guardaba la factura en
    # `uploads/`, publicada sin clave. Si algun dia reaparece, este renglon lo
    # canta antes de que vuelva a haber facturas bajandose desde cualquier
    # navegador. Se prueba como el dueño de A —ni siquiera para el existe—.
    vieja = client.post(f"{V}/gastos/{gasto_a['id']}/adjunto", files=_archivo(), headers=ha)
    print(f"\nla subida INSEGURA de la factura del gasto -> {vieja.status_code} "
          f"(404/405 = cerrada)")
    assert vieja.status_code in (404, 405), (
        "volvio a aparecer POST /gastos/{id}/adjunto: la factura se esta guardando "
        "otra vez en la carpeta publica"
    )


def test_el_enlace_del_soporte_no_pide_sesion(client, base_datos):
    """EL HUECO QUE SIGUE ABIERTO, medido en el gasto de un VEHICULO.

    Es el mismo mecanismo que tenia la factura del gasto antes de moverla: el
    archivo cae en `uploads/<empresa_id>/...` y `/uploads` esta montado como
    archivos estaticos, sin token de por medio.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    vg_a = _gasto_de_vehiculo(client, ha)

    sub = client.post(
        f"{V}/transporte/gastos/{vg_a['id']}/adjunto", files=_archivo(), headers=ha
    )
    print("\nA sube el soporte de su gasto de vehiculo ->", sub.status_code, sub.text[:200])
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


def test_la_factura_del_gasto_ya_no_se_baja_sin_sesion(client, base_datos):
    """EL HUECO QUE SE CERRO, medido: la factura del gasto no tiene URL publica.

    Antes la respuesta traia `adjunto_url` y esa direccion se abria sin token. Hoy
    la factura vive en el bucket privado: en la fila del gasto no queda ninguna
    URL, y la unica forma de verla es un enlace firmado que caduca solo.
    """
    ha = auth_headers(client, "admin.a")
    cat_a = client.post(f"{V}/categorias-gasto", json={"nombre": "CatA"}, headers=ha).json()
    gasto_a = client.post(f"{V}/gastos", json={
        "fecha": "2026-07-05", "categoria_id": cat_a["id"], "concepto": "GastoSECRETOA",
        "valor": "242760.75",
    }, headers=ha).json()

    # Un gasto recien creado no tiene ninguna direccion publica que ofrecer.
    fila = client.get(f"{V}/gastos/{gasto_a['id']}", headers=ha).json()
    print("\nadjunto_url de un gasto nuevo:", fila.get("adjunto_url"))
    assert fila.get("adjunto_url") is None

    # Y las facturas se piden por su propia ruta, que SI exige sesion.
    sin_sesion = client.get(f"{V}/gastos/{gasto_a['id']}/adjuntos")
    print("GET /gastos/{id}/adjuntos SIN token ->", sin_sesion.status_code)
    assert sin_sesion.status_code in (401, 403)
