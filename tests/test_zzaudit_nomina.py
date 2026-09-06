"""AUDITORÍA — NÓMINA: lo que entró sin revisar (recibo en PDF, anticipos)."""
import io
import re

from pypdf import PdfReader

from tests.conftest import auth_headers

NOM = "/api/v1/nomina"


def empleado(client, h, valor_dia=None, nombre="Aurelio", apellido="Marin"):
    body = {"nombre": nombre, "apellido": apellido, "documento": "1122334455", "cargo": "Quesero"}
    if valor_dia is not None:
        body["valor_dia"] = valor_dia
    r = client.post("/api/v1/empleados", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def texto_pdf(contenido: bytes) -> str:
    reader = PdfReader(io.BytesIO(contenido))
    return "\n".join(p.extract_text() or "" for p in reader.pages), len(reader.pages)


def a_numero(texto: str):
    """'$522.916,63' -> 522916.63"""
    limpio = texto.replace("$", "").replace(".", "").replace(",", ".").strip()
    return float(limpio)


# ------------------------------------------------------ el papel tiene que cuadrar
def test_recibo_pdf_desglose_suma_exacto_el_total(client, base_datos):
    h = auth_headers(client, "admin.a")
    emp = empleado(client, h, valor_dia="41833.33")
    client.post("/api/v1/anticipos", json={
        "tipo": "empleado", "empleado_id": emp["id"], "fecha": "2026-07-03",
        "valor": "242.76", "observaciones": "Adelanto tienda"}, headers=h)
    pago = client.post(NOM, json={
        "empleado_id": emp["id"], "fecha": "2026-07-15",
        "dias_trabajados": "12.5", "periodo": "1ra quincena julio"}, headers=h).json()

    # 12,5 × 41.833,33 = 522.916,625 EXACTO. La regla de la casa (0,005 SUBE,
    # app/utils/export.py::_medio_arriba y app/common/schemas.py::a_dos_decimales)
    # dice 522.916,63, y la nómina ya redondea igual
    # (app/modules/empleados/service.py::_centavos). ANTES usaba el quantize por
    # omisión de Python —el del banquero— y guardaba 522.916,62: el papel imprimía
    # "Subtotal devengado $522.916,63" y "TOTAL PAGADO $522.673,86", y al dueño la
    # resta a mano le daba $522.673,87. Un peso, en el papel que firma el empleado.
    assert float(pago["anticipos"]) == 242.76
    assert float(pago["total"]) == 522673.87, pago  # 522.916,63 - 242,76

    res = client.get(f"{NOM}/{pago['id']}/pdf", headers=h)
    assert res.status_code == 200
    texto, hojas = texto_pdf(res.content)
    assert hojas == 1

    bruto = a_numero(re.search(r"Subtotal devengado\s*(\$[\d\.,]+)", texto).group(1))
    desc = a_numero(re.search(r"Descuento anticipos\s*-\s*(\$[\d\.,]+)", texto).group(1))
    total = a_numero(re.search(r"TOTAL PAGADO\s*(\$[\d\.,]+)", texto).group(1))
    assert round(bruto - desc, 2) == total, (
        f"EL PAPEL NO CUADRA: {bruto} - {desc} = {round(bruto-desc,2)} y dice {total}"
    )
    assert total == float(pago["total"])
    # y el renglón del anticipo suma exacto el descuento
    filas = [a_numero(m) for m in re.findall(r"03/07/2026\s*(\$[\d\.,]+)", texto)]
    assert round(sum(filas), 2) == desc, (filas, desc)


def test_recibo_pdf_no_cruza_empresas(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    emp = empleado(client, ha, valor_dia="50000")
    pago = client.post(NOM, json={
        "empleado_id": emp["id"], "fecha": "2026-07-15", "dias_trabajados": "5"},
        headers=ha).json()
    r = client.get(f"{NOM}/{pago['id']}/pdf", headers=hb)
    assert r.status_code == 404, (r.status_code, r.text[:200])


# ---------------------------------------- el anticipo que se quedaba sin dueño
def test_borrar_el_pago_de_nomina_suelta_el_anticipo(client, base_datos):
    """Borrar el pago de nómina DEVUELVE el adelanto a la lista de pendientes.

    LAS CIFRAS, que son las del dueño. Al empleado (jornal $41.833,33) se le
    adelantan $242.760,00 el 3 de julio. Se le paga la quincena de 12,5 jornales:

        12,5 × $41.833,33 = $522.916,63   (bruto)
              -  anticipo = $242.760,00
              ------------------------
              se le entrega = $280.156,63

    El pago estaba malo y se borra. El adelanto tiene que volver a quedar
    disponible, y el pago rehecho tiene que salir por los MISMOS $280.156,63.

    ANTES el adelanto se quedaba marcado en un pago que ya no existe —preso, porque
    `pendientes_empleado` solo recoge los que tienen `pago_empleado_id` en nulo— y
    el pago rehecho salía por $522.916,63: al empleado se le entregaron $242.760,00
    en efectivo que nadie le descontó nunca. Esa es la plata que le faltaba a la
    caja. La otra punta del mismo hueco: si el dueño, viendo que el adelanto no se
    descontó, le registraba otro por el mismo valor, se lo cobraba dos veces.
    """
    h = auth_headers(client, "admin.a")
    emp = empleado(client, h, valor_dia="41833.33")
    ant = client.post("/api/v1/anticipos", json={
        "tipo": "empleado", "empleado_id": emp["id"], "fecha": "2026-07-03",
        "valor": "242760.00"}, headers=h).json()

    pago = client.post(NOM, json={
        "empleado_id": emp["id"], "fecha": "2026-07-15", "dias_trabajados": "12.5"},
        headers=h).json()
    assert float(pago["anticipos"]) == 242760.0
    assert float(pago["total"]) == 280156.63  # 522.916,63 - 242.760,00

    # El pago estaba mal: se borra, y el adelanto queda suelto otra vez
    assert client.delete(f"{NOM}/{pago['id']}", headers=h).status_code == 204
    assert client.get(f"{NOM}", headers=h).json()["total"] == 0
    suelto = client.get(f"/api/v1/anticipos/{ant['id']}", headers=h).json()
    assert suelto["pago_empleado_id"] is None, suelto
    assert suelto["bloqueado"] is False, (
        "el adelanto siguió trabado apuntando a un pago que ya no existe"
    )

    # Se rehace el pago correcto: el adelanto se descuenta UNA vez, la misma
    rehecho = client.post(NOM, json={
        "empleado_id": emp["id"], "fecha": "2026-07-15", "dias_trabajados": "12.5"},
        headers=h).json()
    assert float(rehecho["anticipos"]) == 242760.0, (
        "EL ADELANTO SE PERDIÓ: el pago rehecho descontó "
        f"${float(rehecho['anticipos']):,.2f} en vez de $242.760,00 y quedó en "
        f"${float(rehecho['total']):,.2f}"
    )
    assert float(rehecho["total"]) == 280156.63

    # Y vuelve a quedar trabado, que es lo correcto: ya se le descontó al empleado
    detalle = client.get(f"/api/v1/anticipos/{ant['id']}", headers=h).json()
    assert detalle["bloqueado"] is True
    assert detalle["pago_empleado_id"] == rehecho["id"]
    assert client.delete(f"/api/v1/anticipos/{ant['id']}", headers=h).status_code == 422
    assert client.put(f"/api/v1/anticipos/{ant['id']}",
                      json={"valor": "100000.00"}, headers=h).status_code == 422


def test_recibo_pdf_con_apellido_con_tilde(client, base_datos):
    """El recibo se puede bajar con un apellido normal de la región ('Marín').

    El nombre del empleado iba CRUDO al Content-Disposition (a diferencia de
    reventa, que lo sanea en `_nombre_archivo_cliente`), así que con un apellido
    con tilde la cabecera llevaba un byte 0xED. El navegador la decodifica como
    ISO-8859-1 y el nombre le vuelve bien, pero el TestClient del propio proyecto
    la decodifica como UTF-8 y reventaba: este endpoint NO SE PODÍA PROBAR con un
    nombre real de la región. Ahora `_nombre_archivo_empleado` le quita los acentos
    y todo lo que no sea alfanumérico —lo mismo que hace reventa—, así que además
    una comilla o un salto de línea en el apellido ya no puede inyectar cabeceras.
    """
    h = auth_headers(client, "admin.a")
    emp = empleado(client, h, valor_dia="50000", apellido="Marín")
    pago = client.post(NOM, json={
        "empleado_id": emp["id"], "fecha": "2026-07-15", "dias_trabajados": "5"},
        headers=h).json()
    try:
        r = client.get(f"{NOM}/{pago['id']}/pdf", headers=h)
        estado = r.status_code
    except UnicodeDecodeError as exc:
        estado = f"UnicodeDecodeError: {exc}"
    assert estado == 200, f"el PDF con apellido con tilde no se pudo leer: {estado}"
    assert r.headers["content-disposition"] == (
        'attachment; filename="recibo_nomina_Aurelio_Marin_2026-07-15.pdf"'
    )
