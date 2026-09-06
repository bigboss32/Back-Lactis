"""AUDITORIA DEL NOMBRE DEL ARCHIVO QUE VIAJA EN LA CABECERA HTTP.

Los cuatro endpoints de PDF ponen el nombre del tercero en el header
`Content-Disposition`. DOS de ellos lo sanean y DOS no:

    SANEA   GET /reventa/estado-cuenta/pdf              (_nombre_archivo_cliente)
    SANEA   GET /reventa/estado-cuenta-productor/pdf    (_nombre_archivo_productor)
    NO      GET /liquidaciones/{id}/pdf                 (LiquidacionService.generar_pdf)
    NO      GET /nomina/{id}/pdf                        (PagoEmpleadoService.generar_pdf)

Los dos que sanean tienen escrito en su propio docstring por que:
"si se colara una comilla o un salto de linea en el header Content-Disposition
seria una inyeccion de cabecera HTTP".

Aca se mide, contra la API, que le pasa a los dos que NO lo hacen.
"""
import pytest

import re

from tests.conftest import auth_headers

PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQ = "/api/v1/liquidaciones"
NOMINA = "/api/v1/nomina"
EMPLEADOS = "/api/v1/empleados"


def crear(client, h, url, payload):
    r = client.post(url, json=payload, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


def cabecera_de_liquidacion(client, h, nombre_proveedor):
    prov = crear(client, h, PROVEEDORES, {
        "nombre": nombre_proveedor, "vereda": "La Vega", "precio_litro": "1833.33"})
    trans = crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "242.76"})
    crear(client, h, RECEPCIONES, {
        "fecha": "2026-07-16", "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "137.45"})
    r = client.post(f"{LIQ}/generar",
                    json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
                          "tipo": "proveedor"}, headers=h)
    assert r.json()["generadas"], r.json()
    liq = r.json()["generadas"][0]
    rr = client.get(f"{LIQ}/{liq['id']}/pdf", headers=h)
    return rr.headers.get("content-disposition", "")


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_cabecera_la_comilla_del_nombre_parte_el_content_disposition(client, base_datos):
    """Un nombre con una COMILLA DOBLE cierra el `filename="..."` antes de tiempo:
    lo que va despues de la comilla el navegador lo lee como OTRO parametro de la
    cabecera, no como parte del nombre. Es el escenario que los dos endpoints de
    reventa dicen estar evitando."""
    h = auth_headers(client, "admin.a")
    atacante = 'Ana"; filename="cuenta_de_cobro'
    cabecera = cabecera_de_liquidacion(client, h, atacante)
    print(f"\n  nombre del proveedor : {atacante!r}")
    print(f"  Content-Disposition  : {cabecera!r}")
    comillas = cabecera.count('"')
    print(f"  comillas dobles en la cabecera: {comillas} (deberian ser 2)")
    assert comillas == 2, (
        f"el nombre del tercero metio comillas extra en la cabecera: {cabecera!r}")
    assert cabecera.count("filename=") == 1, (
        f"la cabecera quedo con dos parametros filename: {cabecera!r}")


def test_zzaudit_cabecera_nomina_misma_puerta(client, base_datos):
    """Lo mismo por el lado del recibo de nomina."""
    h = auth_headers(client, "admin.a")
    emp = crear(client, h, EMPLEADOS, {
        "nombre": 'Ana"; filename="nomina_falsa', "apellido": "Prueba",
        "valor_dia": "61733"})
    pago = crear(client, h, NOMINA, {
        "empleado_id": emp["id"], "fecha": "2026-07-31", "dias_trabajados": "12.50"})
    r = client.get(f"{NOMINA}/{pago['id']}/pdf", headers=h)
    cabecera = r.headers.get("content-disposition", "")
    print(f"\n  Content-Disposition: {cabecera!r}")
    assert cabecera.count('"') == 2, (
        f"el nombre del empleado metio comillas extra en la cabecera: {cabecera!r}")


def test_zzaudit_cabecera_reventa_si_sanea(client, base_datos):
    """Y la contraprueba: los dos de reventa aguantan el mismo nombre."""
    h = auth_headers(client, "admin.a")
    atacante = 'Ana"; filename="cuenta_de_cobro'
    crear(client, h, "/api/v1/reventa/compras", {
        "fecha": "2026-02-03", "productor": atacante, "kilos_brutos": "820.53",
        "precio_kilo": "11317.45"})
    crear(client, h, "/api/v1/reventa/ventas", {
        "fecha": "2026-02-12", "cliente": atacante, "tipo": "queso",
        "kilos": "100.37", "precio_kilo": "23457.76"})
    r1 = client.get("/api/v1/reventa/estado-cuenta-productor/pdf",
                    params={"productor": atacante}, headers=h)
    r2 = client.get("/api/v1/reventa/estado-cuenta/pdf",
                    params={"cliente": atacante}, headers=h)
    d1 = r1.headers.get("content-disposition", "")
    d2 = r2.headers.get("content-disposition", "")
    print(f"\n  productor -> {d1!r}\n  cliente   -> {d2!r}")
    assert d1.count('"') == 2 and d2.count('"') == 2, (d1, d2)


def test_zzaudit_cabecera_el_salto_de_linea(client, base_datos):
    """Y la punta grave: un SALTO DE LINEA en el nombre.

    MEDIDO CONTRA UVICORN DE VERDAD (no contra el TestClient, que pasa los
    headers como tuplas de ASGI y no los serializa): con un
    Content-Disposition que lleva \r\n, uvicorn levanta
        RuntimeError: Invalid HTTP header value.
    y cierra la conexion SIN mandar ni una linea de respuesta. O sea que ese
    comprobante queda imposible de descargar para siempre, y el navegador solo
    muestra un error de red sin mensaje.

    Aca solo se comprueba que el nombre entra a la base sin ninguna validacion
    que lo pare y que llega crudo a la cabecera.
    """
    h = auth_headers(client, "admin.a")
    atacante = "Ana\r\nX-Inyectado: si"
    r = client.post(PROVEEDORES, json={
        "nombre": atacante, "vereda": "La Vega", "precio_litro": "1833.33"},
        headers=h)
    print(f"\n  crear proveedor con salto de linea -> {r.status_code}")
    if r.status_code >= 400:
        print(f"  lo rechazo la validacion: {r.text[:300]}")
        return
    prov = r.json()
    trans = crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "242.76"})
    crear(client, h, RECEPCIONES, {
        "fecha": "2026-07-16", "proveedor_id": prov["id"],
        "transportador_id": trans["id"], "cantidad_litros": "137.45"})
    rr = client.post(f"{LIQ}/generar",
                     json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
                           "tipo": "proveedor"}, headers=h)
    liq = rr.json()["generadas"][0]
    fallo = None
    try:
        resp = client.get(f"{LIQ}/{liq['id']}/pdf", headers=h)
        print(f"  status={resp.status_code}")
        print(f"  content-disposition={resp.headers.get('content-disposition')!r}")
        print(f"  x-inyectado={resp.headers.get('x-inyectado')!r}")
        assert resp.headers.get("x-inyectado") is None, "SE PARTIO LA RESPUESTA HTTP"
    except Exception as e:  # noqa: BLE001
        fallo = e
        print(f"  la descarga fallo: {type(e).__name__}: {e}")
    assert fallo is None, f"la descarga del comprobante revento: {fallo}"
