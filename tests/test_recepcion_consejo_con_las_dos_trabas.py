"""SI LA LECHE Y EL FLETE TRABAN LA MISMA ACCIÓN, NINGÚN CONSEJO PROMETE QUE UN PASO LA SUELTA.

Los litros, la fecha y el estado de un día los traban la leche Y el flete, y borrar el día
también. El 422 culpaba solo a la leche —la que el candado nombra primero— y le daba su
consejo, que solo mira la leche. Medido:

  · El 02/06 Beto Flete entrega 100 L × $1.800 = $180.000 contra $300.000 de adelanto: Q1
    en borrador, −$120.000, cobrados en la Q2 ($250.000 − $120.000 = $130.000, borrador).
    El flete de Stella del mismo día, 100 L × $100 = $10.000, ya se pagó. El PUT de los
    litros a 90 y el DELETE decían "Anule primero esa liquidación … y vuelva a
    intentarlo". Se anulaba la Q2 y los dos volvían a rebotar, ahora por el flete pagado:
    un comprobante anulado para nada.
  · Con las dos deudas cobradas (la leche y el flete, cada una en su Q2), el consejo
    mandaba a anular la Q2 de la leche "y vuelva a intentarlo"; anulada, rebotaba por la
    Q2 del flete. Esa anulación sí hacía falta, pero no bastaba, y el mensaje decía que sí.
  · Con un abono en la leche y el flete pagado: "Elimine primero ese pago", y el pago se
    iba con sus soportes para volver a rebotar por el flete.

Cada prueba sigue el consejo hasta el final: si el 422 dice "anule", se anula y la acción
tiene que pasar; si no pasa, el 422 no lo dice. Y la regla de oro en cada fila: neto =
valor_total − anticipos − saldo_anterior; saldo = neto − pagado.
"""
from decimal import Decimal

from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import _detalle, _leer

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")

ANULE = "nule primero esa liquidación"  # "Anule …" y "que anule …" (Supervisor)
VUELVA = "vuelva a intentarlo"
FLETE_PAGADO = (
    "el flete ya se pagó en una liquidación. Sí se puede corregir la sucursal y las "
    "observaciones. Si la cifra está mala, corríjala por fuera del sistema o registre el "
    "ajuste en la quincena siguiente"
)
NO_LA_DESTRABARIA = "Y anular esa liquidación no la destrabaría, porque "


def D(v):
    return Decimal(str(v))


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _cuadra(liq):
    neto = D(liq["valor_total"]) - D(liq["anticipos"]) - D(liq["saldo_anterior"] or 0)
    assert D(liq["saldo"]) == neto - D(liq["pagado"]), liq


def _cifras(client, h, liq):
    leida = _leer(client, h, liq)
    _cuadra(leida)
    return (leida["estado"], D(leida["valor_total"]), D(leida["saldo"]))


def _tercero(client, h, sufijo):
    stella = client.post(f"{V}/transportadores", json={
        "nombre": f"Stella {sufijo}", "valor_transporte": "100"}, headers=h).json()["id"]
    prov = client.post(f"{V}/proveedores", json={
        "nombre": f"Beto Flete {sufijo}", "vereda": "X", "precio_litro": "1800"},
        headers=h).json()["id"]
    return stella, prov


def _dia(client, h, prov, fecha, litros, stella=None, precio=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov, "cantidad_litros": litros}
    if stella:
        cuerpo["transportador_id"] = stella
    if precio:
        cuerpo["precio_litro"] = precio
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _adelanto(client, h, valor, *, prov=None, stella=None):
    cuerpo = ({"tipo": "proveedor", "proveedor_id": prov} if prov
              else {"tipo": "transportador", "transportador_id": stella})
    r = client.post(ANT, json=cuerpo | {"fecha": "2026-06-01", "valor": valor}, headers=h)
    assert r.status_code == 201, r.text


def _generar(client, h, tipo, periodo):
    return _ok(client.post(f"{API}/generar", json={
        "periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": tipo},
        headers=h))["generadas"]


def _de(generadas, campo, tercero):
    return next(x for x in generadas if x[campo] == tercero)["id"]


def _flete_pagado_y_leche_cobrada(client, h, sufijo):
    """El caso medido: la leche de Q1 debe $120.000 cobrados en Q2 (las dos en borrador)
    y el flete del mismo día ya se le pagó a Stella."""
    stella, prov = _tercero(client, h, sufijo)
    dia = _dia(client, h, prov, "2026-06-02", "100", stella)
    _adelanto(client, h, "300000", prov=prov)
    q1 = _de(_generar(client, h, "proveedor", Q1), "proveedor_id", prov)
    flete = _de(_generar(client, h, "transportador", Q1), "transportador_id", stella)
    _ok(client.post(f"{API}/{flete}/aprobar", headers=h))
    _ok(client.post(f"{API}/{flete}/pagar", headers=h))
    _dia(client, h, prov, "2026-06-20", "100", precio="2500")
    q2 = _de(_generar(client, h, "proveedor", Q2), "proveedor_id", prov)
    # 180.000 − 300.000 = −120.000 · 250.000 − 120.000 = 130.000 · 100 L × $100 = 10.000
    assert _cifras(client, h, q1) == ("borrador", D(180000), D(-120000))
    assert _cifras(client, h, q2) == ("borrador", D(250000), D(130000))
    assert _cifras(client, h, flete) == ("pagada", D(10000), D(0))
    assert _leer(client, h, q1)["deuda_trasladada_a_id"] == q2
    return dia, q1, q2, flete


# ---------------------------------------------------------------------------------------
def test_con_el_flete_pagado_no_manda_a_anular_la_que_cobro_la_deuda(client, base_datos):
    h = auth_headers(client, "admin.a")
    dia, q1, q2, flete = _flete_pagado_y_leche_cobrada(client, h, "Dos Trabas")

    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    borrar = client.delete(f"{REC}/{dia}", headers=h)
    print(f"\n  PUT litros: {_detalle(put)}\n  DELETE: {_detalle(borrar)}")
    assert put.status_code == 422 and borrar.status_code == 422
    # Lo que queda después de cualquier paso es el flete pagado: esa es la razón, y su
    # consejo no promete nada.
    assert _detalle(put) == f"No se puede cambiar los litros de este día: {FLETE_PAGADO}"
    assert _detalle(borrar) == (
        "No se puede eliminar este día: el flete ya se pagó en una liquidación. Si la cifra "
        "está mala, corríjala por fuera del sistema o registre el ajuste en la quincena "
        "siguiente")
    for r in (put, borrar):
        assert ANULE not in _detalle(r) and VUELVA not in _detalle(r)
    # El aviso del día sigue nombrando las dos razones, sin consejo.
    aviso = client.get(f"{REC}/{dia}", headers=h).json()["candado_aviso"]
    assert "ya se le cobró en la del 16/06/2026 al 30/06/2026" in aviso
    assert "el flete de este día ya se le pagó a Stella Dos Trabas" in aviso

    # CONTROL, y aquí el consejo SÍ es cierto: el precio solo lo traba la leche, así que
    # anular la Q2 lo suelta. Se sigue hasta el final.
    precio = client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h)
    print(f"  PUT precio: {_detalle(precio)}")
    assert precio.status_code == 422
    assert "Anule primero esa liquidación" in _detalle(precio) and VUELVA in _detalle(precio)
    assert _ok(client.post(f"{API}/{q2}/anular", headers=h))["estado"] == "anulada"
    _ok(client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h))
    # 100 L × $1.700 = $170.000 − $300.000 = −$130.000; el flete pagado no se movió.
    assert _cifras(client, h, q1) == ("borrador", D(170000), D(-130000))
    assert _leer(client, h, q1)["deuda_trasladada_a_id"] is None
    assert _cifras(client, h, flete) == ("pagada", D(10000), D(0))
    # Y los litros siguen trabados por el flete, con la MISMA razón y el mismo consejo que
    # antes de anular; solo crece lo que sí se puede corregir (el precio ya se soltó).
    otra_vez = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    assert otra_vez.status_code == 422
    assert _detalle(otra_vez) == (
        "No se puede cambiar los litros de este día: el flete ya se pagó en una liquidación. "
        "Sí se puede corregir el precio por litro, las bonificaciones, los descuentos, la "
        "sucursal y las observaciones. Si la cifra está mala, corríjala por fuera del sistema "
        "o registre el ajuste en la quincena siguiente")


def test_al_supervisor_tampoco_le_pide_que_un_administrador_anule(
    client, base_datos, db_session
):
    from tests.test_liquidacion_corregir_permisos import crear_usuario_con_rol

    h = auth_headers(client, "admin.a")
    crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Supervisor", "sup.dos.trabas")
    hs = auth_headers(client, "sup.dos.trabas")
    dia, *_ = _flete_pagado_y_leche_cobrada(client, h, "Dos Trabas Sup")
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=hs)
    print(f"\n  PUT litros (Supervisor): {_detalle(put)}")
    assert put.status_code == 422
    assert _detalle(put) == f"No se puede cambiar los litros de este día: {FLETE_PAGADO}"
    assert "Administrador" not in _detalle(put)


def test_dos_deudas_cobradas_no_promete_que_una_anulacion_basta(client, base_datos):
    """Leche y flete de Q1 en borrador debiendo, cada uno cobrado en su Q2. Los litros
    los traban las dos; el precio, solo la leche; el transportador, solo el flete."""
    h = auth_headers(client, "admin.a")
    stella, prov = _tercero(client, h, "Dos Deudas")
    otro = client.post(f"{V}/transportadores", json={
        "nombre": "Otro Dos Deudas", "valor_transporte": "100"}, headers=h).json()["id"]
    dia = _dia(client, h, prov, "2026-06-02", "100", stella)
    _adelanto(client, h, "300000", prov=prov)
    _adelanto(client, h, "30000", stella=stella)
    g1 = _generar(client, h, "ambos", Q1)
    q1, f1 = _de(g1, "proveedor_id", prov), _de(g1, "transportador_id", stella)
    _dia(client, h, prov, "2026-06-20", "100", stella, precio="2500")
    g2 = _generar(client, h, "ambos", Q2)
    q2, f2 = _de(g2, "proveedor_id", prov), _de(g2, "transportador_id", stella)
    # 180.000 − 300.000 = −120.000 · 10.000 − 30.000 = −20.000
    # 250.000 − 120.000 = 130.000 · 10.000 − 20.000 = −10.000
    assert _cifras(client, h, q1) == ("borrador", D(180000), D(-120000))
    assert _cifras(client, h, f1) == ("borrador", D(10000), D(-20000))
    assert _cifras(client, h, q2) == ("borrador", D(250000), D(130000))
    assert _cifras(client, h, f2) == ("borrador", D(10000), D(-10000))

    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    borrar = client.delete(f"{REC}/{dia}", headers=h)
    print(f"\n  PUT litros: {_detalle(put)}\n  DELETE: {_detalle(borrar)}")
    del_flete = (
        "lo que Stella Dos Deudas quedó debiendo en la quincena del flete de este día ya se "
        "le cobró en la del 16/06/2026 al 30/06/2026"
    )
    for r in (put, borrar):
        assert r.status_code == 422
        assert ANULE not in _detalle(r) and VUELVA not in _detalle(r)
        assert f"{NO_LA_DESTRABARIA}{del_flete}." in _detalle(r)
        assert _detalle(r).endswith("Si la cifra está mala, registre el ajuste en la quincena "
                                    "siguiente")
    # Por acción: el precio solo lo traba la leche y el transportador solo el flete, así
    # que ahí el consejo de anular la Q2 de cada uno sigue saliendo.
    precio = client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h)
    cambio = client.put(f"{REC}/{dia}", json={"transportador_id": otro}, headers=h)
    print(f"  PUT precio: {_detalle(precio)}\n  PUT transportador: {_detalle(cambio)}")
    for r in (precio, cambio):
        assert r.status_code == 422 and "Anule primero esa liquidación" in _detalle(r)

    # Se sigue: anular la Q2 de la leche suelta el precio…
    _ok(client.post(f"{API}/{q2}/anular", headers=h))
    _ok(client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h))
    assert _cifras(client, h, q1) == ("borrador", D(170000), D(-130000))
    # …pero no los litros, y ahora que la única traba es el flete, el 422 sí manda a anular
    # su Q2.
    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    print(f"  PUT litros tras anular la Q2 de la leche: {_detalle(put)}")
    assert put.status_code == 422
    assert "esta quincena del flete ya se le cobró" in _detalle(put)
    assert "Anule primero esa liquidación" in _detalle(put) and VUELVA in _detalle(put)
    _ok(client.post(f"{API}/{f2}/anular", headers=h))
    _ok(client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h))
    # 90 L × $1.700 = $153.000 − $300.000 = −$147.000 · 90 L × $100 = $9.000 − $30.000
    assert _cifras(client, h, q1) == ("borrador", D(153000), D(-147000))
    assert _cifras(client, h, f1) == ("borrador", D(9000), D(-21000))


def test_abono_en_la_leche_y_flete_pagado_no_manda_a_borrar_el_pago(client, base_datos):
    """Aprobada de 100 L × $1.800 = $180.000 con un abono de $50.000 (saldo $130.000) y el
    flete del mismo día pagado. Borrar el abono no suelta los litros: el flete los sigue
    trabando. El precio sí: ahí el consejo de Corregir sigue saliendo."""
    h = auth_headers(client, "admin.a")
    stella, prov = _tercero(client, h, "Abono Flete")
    dia = _dia(client, h, prov, "2026-06-02", "100", stella)
    g = _generar(client, h, "ambos", Q1)
    leche, flete = _de(g, "proveedor_id", prov), _de(g, "transportador_id", stella)
    for liq in (leche, flete):
        _ok(client.post(f"{API}/{liq}/aprobar", headers=h))
    _ok(client.post(f"{API}/{leche}/pagos", json={"fecha": "2026-06-16", "valor": "50000"},
                    headers=h))
    _ok(client.post(f"{API}/{flete}/pagar", headers=h))
    assert _cifras(client, h, leche) == ("parcial", D(180000), D(130000))
    assert _cifras(client, h, flete) == ("pagada", D(10000), D(0))

    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    borrar = client.delete(f"{REC}/{dia}", headers=h)
    print(f"\n  PUT litros: {_detalle(put)}\n  DELETE: {_detalle(borrar)}")
    assert _detalle(put) == f"No se puede cambiar los litros de este día: {FLETE_PAGADO}"
    for r in (put, borrar):
        assert r.status_code == 422 and "Elimine primero" not in _detalle(r)
    # El precio solo lo traba la leche: Corregir lo arregla conservando el abono.
    precio = client.put(f"{REC}/{dia}", json={"precio_litro": "1700"}, headers=h)
    assert precio.status_code == 422
    assert "use 'Corregir esta quincena', que conserva el pago" in _detalle(precio)


def test_abono_en_la_leche_y_abono_en_el_flete_dice_que_borrar_uno_no_basta(
    client, base_datos
):
    """Los dos con un abono: borrar el de la leche no suelta los litros, porque el flete
    también tiene el suyo. Se dice, en vez de mandar a botar unos soportes."""
    h = auth_headers(client, "admin.a")
    stella, prov = _tercero(client, h, "Dos Abonos")
    dia = _dia(client, h, prov, "2026-06-02", "100", stella)
    g = _generar(client, h, "ambos", Q1)
    leche, flete = _de(g, "proveedor_id", prov), _de(g, "transportador_id", stella)
    for liq in (leche, flete):
        _ok(client.post(f"{API}/{liq}/aprobar", headers=h))
    _ok(client.post(f"{API}/{leche}/pagos", json={"fecha": "2026-06-16", "valor": "50000"},
                    headers=h))
    _ok(client.post(f"{API}/{flete}/pagos", json={"fecha": "2026-06-16", "valor": "4000"},
                    headers=h))
    assert _cifras(client, h, leche) == ("parcial", D(180000), D(130000))
    assert _cifras(client, h, flete) == ("parcial", D(10000), D(6000))

    put = client.put(f"{REC}/{dia}", json={"cantidad_litros": "90"}, headers=h)
    print(f"\n  PUT litros: {_detalle(put)}")
    assert put.status_code == 422
    assert _detalle(put).startswith(
        "No se puede cambiar los litros de este día: la leche ya tiene un pago registrado")
    assert "Elimine primero" not in _detalle(put)
    assert _detalle(put).endswith(
        "Y borrar ese pago no destrabaría el día, porque el flete de este día ya se le abonó "
        "a Stella Dos Abonos")
