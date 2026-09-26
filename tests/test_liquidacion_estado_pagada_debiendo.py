"""EL ESTADO DE LA QUINCENA EN LA QUE EL TERCERO QUEDÓ DEBIENDO.

Lo pidió el dueño, mirando la lista con dos filas marcadas "Aprobada" y "quedó debiendo ·
cobrada": "cuando quede de esta manera me gustaría que el estado quedara pagada o paga
debiendo, pero no solo aprobada o solamente pagada. Esto sucede cuando el proveedor o el
transportador queda debiendo: la quincena vale 500 pero él pidió prestado 200".

Con 500 y 200 no queda debiendo (quedan 300 por entregarle); las dos filas de la captura
sí decían "quedó debiendo", así que se tomó la captura como la regla: el adelanto pasa
del valor de la quincena. Los números de acá abajo son 500 contra 700.

Lo que se mide:

  · EL RÓTULO: "pagada · quedó debiendo" en ese caso, y el estado de siempre en todos los
    demás. En particular NO en un borrador (ni siquiera está aprobado) ni en una anulada.
  · QUE ES UN RÓTULO Y NO UN ESTADO: la columna guardada sigue diciendo lo que decía.
    Cambiarla de verdad trabaría los días de una quincena que el sistema deja corregible
    a propósito mientras la deuda no se cobra.
  · EL FILTRO: "Pagadas" las incluye y "Aprobadas" no. Si el chip dice pagada y el
    filtro la mete en aprobadas, el dueño deja de confiar en los dos.
  · EL PAPEL: el comprobante dice lo mismo que la pantalla. Es el que se le entrega al
    productor, y "APROBADA" ahí se lee como "todavía no me han pagado".
"""
import io
from decimal import Decimal

from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
V = "/api/v1"
PAGADA_DEBIENDO = "pagada · quedó debiendo"


def _quincena(client, h, *, nombre, litros, adelanto, aprobar=True, precio="2000"):
    """Una quincena de `litros` a `precio` con un adelanto de `adelanto`."""
    prov = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": precio}, headers=h).json()
    client.post(f"{V}/recepciones", json={
        "fecha": "2026-08-02", "proveedor_id": prov["id"], "cantidad_litros": litros},
        headers=h)
    if Decimal(adelanto) > 0:
        r = client.post(f"{V}/anticipos", json={
            "tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-08-03",
            "valor": adelanto}, headers=h)
        assert r.status_code == 201, r.text
    gen = client.post(f"{API}/generar", json={
        "periodo_inicio": "2026-08-01", "periodo_fin": "2026-08-15", "tipo": "proveedor"},
        headers=h).json()["generadas"]
    liq = next(x for x in gen if x["proveedor_id"] == prov["id"])
    if aprobar:
        r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
        assert r.status_code == 200, r.text
        liq = r.json()
    return liq


def test_el_caso_del_dueno_se_lee_pagada_quedo_debiendo(client, base_datos):
    """Quincena de $500.000 (250 L a $2.000) contra $700.000 de adelanto: no hay nada que
    entregarle y él quedó debiendo $200.000."""
    h = auth_headers(client, "admin.a")
    liq = _quincena(client, h, nombre="Pedro Osorio", litros="250", adelanto="700000")
    print(f"\n  guardado: {liq['estado']} · visible: {liq['estado_visible']} · "
          f"le queda debiendo: {liq['le_queda_debiendo']}")
    assert Decimal(liq["le_queda_debiendo"]) == Decimal("200000.00")
    assert liq["estado_visible"] == PAGADA_DEBIENDO
    # Y LA COLUMNA NO SE TOCÓ: sigue en 'aprobada', que es lo que usan los botones y los
    # candados. Es un rótulo, no un estado nuevo.
    assert liq["estado"] == "aprobada"


def test_la_quincena_normal_sigue_diciendo_lo_de_siempre(client, base_datos):
    """$500.000 con $200.000 de adelanto: queda $300.000 por entregarle. Nada de rótulo."""
    h = auth_headers(client, "admin.a")
    liq = _quincena(client, h, nombre="Normal", litros="250", adelanto="200000")
    assert Decimal(liq["saldo"]) == Decimal("300000.00")
    assert liq["estado_visible"] == "aprobada"

    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h).json()
    assert pagada["estado_visible"] == "pagada", (
        "una quincena pagada normal no puede decir que quedó debiendo"
    )


def test_el_saldo_exacto_en_cero_no_dice_que_quedo_debiendo(client, base_datos):
    """$500.000 contra $500.000 de adelanto: saldada exacto, NO le debe nada a nadie."""
    h = auth_headers(client, "admin.a")
    liq = _quincena(client, h, nombre="Exacto", litros="250", adelanto="500000")
    assert Decimal(liq["saldo"]) == Decimal("0.00")
    assert liq["estado_visible"] != PAGADA_DEBIENDO


def test_un_borrador_con_mas_adelantos_que_leche_no_dice_pagada(client, base_datos):
    """Decirle 'pagada' a un documento que ni siquiera se aprobó sería mentir."""
    h = auth_headers(client, "admin.a")
    liq = _quincena(client, h, nombre="Borrador", litros="250", adelanto="700000", aprobar=False)
    assert liq["estado"] == "borrador"
    assert Decimal(liq["le_queda_debiendo"]) > 0
    assert liq["estado_visible"] == "borrador"


def test_el_filtro_de_pagadas_las_incluye_y_el_de_aprobadas_no(client, base_datos):
    """La pantalla y el filtro tienen que estar de acuerdo."""
    h = auth_headers(client, "admin.a")
    debe = _quincena(client, h, nombre="Debe", litros="250", adelanto="700000")
    pendiente = _quincena(client, h, nombre="Pendiente", litros="250", adelanto="100000")

    def ids(estado):
        r = client.get(f"{API}?estado={estado}&page_size=100", headers=h)
        assert r.status_code == 200, r.text
        return {x["id"] for x in r.json()["items"]}

    aprobadas, pagadas = ids("aprobada"), ids("pagada")
    print(f"\n  aprobadas: {len(aprobadas)} · pagadas: {len(pagadas)}")
    assert pendiente["id"] in aprobadas, "la que falta pagar tiene que salir en Aprobadas"
    assert debe["id"] not in aprobadas, (
        "la que dice 'pagada · quedó debiendo' en el chip no puede salir en Aprobadas"
    )
    assert debe["id"] in pagadas, "y tiene que salir cuando se filtra Pagadas"
    assert pendiente["id"] not in pagadas


def test_el_papel_dice_lo_mismo_que_la_pantalla(client, base_datos):
    """El comprobante que se le entrega al productor decía 'Estado: APROBADA'."""
    h = auth_headers(client, "admin.a")
    liq = _quincena(client, h, nombre="Papel", litros="250", adelanto="700000")
    pdf = client.get(f"{API}/{liq['id']}/pdf", headers=h)
    assert pdf.status_code == 200
    texto = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    print(f"\n  el papel dice PAGADA · QUEDÓ DEBIENDO? {'QUEDÓ DEBIENDO' in texto.upper()}")
    assert "PAGADA · QUEDÓ DEBIENDO" in texto.upper()
    assert "ESTADO: APROBADA" not in texto.upper()


def test_la_anulada_sigue_diciendo_anulada(client, base_datos):
    """Una anulada no vale nada: no se paga ni se debe."""
    h = auth_headers(client, "admin.a")
    liq = _quincena(client, h, nombre="Anulada", litros="250", adelanto="700000")
    r = client.post(f"{API}/{liq['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["estado_visible"] == "anulada"


def test_el_dialogo_de_corregir_dice_lo_mismo_que_la_lista(client, base_datos):
    """Si la corrección deja al tercero debiendo, el diálogo no puede decir "queda en
    pagada" y un segundo después la lista pintarla "pagada · quedó debiendo"."""
    h = auth_headers(client, "admin.a")
    liq = _quincena(client, h, nombre="Corrige", litros="250", adelanto="200000")
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h).json()
    assert pagada["estado_visible"] == "pagada"

    # Aparece un adelanto olvidado de $400.000: el neto baja a -$100.000 contra $300.000
    # ya entregados. Queda debiendo.
    prov_id = pagada["proveedor_id"]
    client.post(f"{V}/anticipos", json={
        "tipo": "proveedor", "proveedor_id": prov_id, "fecha": "2026-08-10",
        "valor": "400000"}, headers=h)
    suelto = client.post(f"{API}/{liq['id']}/corregir/previsualizar",
                         json={"motivo": "mirar"}, headers=h).json()["anticipos_sueltos"][0]
    prev = client.post(f"{API}/{liq['id']}/corregir/previsualizar", json={
        "motivo": "faltaba un adelanto", "anticipos_a_incluir": [suelto["anticipo_id"]]},
        headers=h).json()
    print(f"\n  el dialogo dice: queda en {prev['estado_visible_despues']}")
    assert prev["estado_visible_despues"] == PAGADA_DEBIENDO

    hecho = client.post(f"{API}/{liq['id']}/corregir", json={
        "motivo": "faltaba un adelanto", "anticipos_a_incluir": [suelto["anticipo_id"]]},
        headers=h).json()
    assert hecho["estado_visible"] == prev["estado_visible_despues"], (
        "el diálogo prometió un estado y la lista pinta otro"
    )
