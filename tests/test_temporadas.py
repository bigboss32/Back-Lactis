"""Temporadas: ciclos de compra y reventa con nombre, fechas y su ganancia.

Una temporada NO guarda plata: es un rango de fechas con nombre, y sus cifras se
calculan con el mismo motor del resumen. De ahí salen las dos pruebas que de
verdad importan:

- PARIDAD: la ganancia de la temporada tiene que ser EXACTAMENTE la que muestra
  el Resumen filtrado a esas mismas fechas. Si difirieran, el usuario vería dos
  cifras distintas para lo mismo y dejaría de creerle al tablero.
- NO SOLAPE: si dos temporadas se cruzaran, los mismos kilos y la misma plata
  caerían en las dos y la suma de las temporadas no daría la ganancia total.

Los números se imprimen porque el usuario cuadra los desgloses a mano.
"""
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/reventa"


def D(valor):
    return Decimal(str(valor))


def detalle(r) -> str:
    """El detalle de un BusinessError. Llega como 422 y anidado dentro de
    "error", no en la raíz de la respuesta."""
    cuerpo = r.json()
    return cuerpo.get("error", cuerpo).get("detail", "")


def compra(client, headers, **datos):
    r = client.post(f"{API}/compras", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def venta(client, headers, **datos):
    r = client.post(f"{API}/ventas", json=datos, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def abonar_todo(client, headers, compra_json, fecha):
    """Paga una compra completa. Las compras NO tienen "pagada de contado":
    siempre nacen por pagar y se abonan aparte, igual que en la vida real.
    """
    r = client.post(
        f"{API}/compras/{compra_json['id']}/abonos",
        json={"fecha": fecha, "valor": str(compra_json["valor_total"])},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def crear_temporada(client, headers, **datos):
    return client.post(f"{API}/temporadas", json=datos, headers=headers)


def panel(client, headers):
    r = client.get(f"{API}/temporadas", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def resumen(client, headers, desde, hasta):
    r = client.get(
        f"{API}/resumen", params={"desde": desde, "hasta": hasta}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


def sembrar_marzo_y_julio(client, h):
    """Dos ciclos separados, con cifras distintas para que no se confundan.

    Marzo: 500 kg a 18.000 = 9.000.000 comprados; se venden 480 kg a 21.000 =
    10.080.000, con 150.000 de flete. Ganancia = 10.080.000 - 9.000.000 - 150.000
    = 930.000, y quedan 20 kg de residuo.

    Julio: 800 kg a 17.000 = 13.600.000 comprados; se venden 800 kg a 20.000 =
    16.000.000 sin gastos. Ganancia = 2.400.000 y no queda residuo.
    """
    compra(client, h, fecha="2026-03-04", productor="Hacienda Santa Bárbara",
           kilos_brutos="500", precio_kilo="18000")
    venta(client, h, fecha="2026-03-20", cliente="Depósito El Trébol", kilos="480",
          precio_kilo="21000", gasto_concepto="Flete", gasto_por_kilo="312.5",
          pagada_de_contado=True)
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="800", precio_kilo="17000")
    venta(client, h, fecha="2026-07-18", cliente="Alba Nieto", kilos="800",
          precio_kilo="20000", pagada_de_contado=True)


# ---------------------------------------------------------------------------
# 1. PARIDAD: la temporada dice lo mismo que el Resumen para esas fechas
# ---------------------------------------------------------------------------
def test_la_ganancia_de_la_temporada_es_la_del_resumen(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar_marzo_y_julio(client, h)
    r = crear_temporada(client, h, nombre="Temporada de marzo",
                        fecha_inicio="2026-03-01", fecha_fin="2026-03-31")
    assert r.status_code == 201, r.text

    fila = panel(client, h)["temporadas"][0]
    del_resumen = resumen(client, h, "2026-03-01", "2026-03-31")

    print("\n===== 1. PARIDAD CON EL RESUMEN =====")
    print(f"  temporada: ganancia={fila['ganancia']} comprado={fila['total_compras']}"
          f" vendido={fila['total_ventas']} gastos={fila['total_gastos']}")
    print(f"  resumen:   ganancia={del_resumen['ganancia_estimada']}"
          f" comprado={del_resumen['total_compras']}"
          f" vendido={del_resumen['total_ventas']} gastos={del_resumen['total_gastos']}")

    # Dígito por dígito, campo por campo: es el mismo motor, no una copia
    assert D(fila["ganancia"]) == D(del_resumen["ganancia_estimada"])
    assert D(fila["total_compras"]) == D(del_resumen["total_compras"])
    assert D(fila["total_ventas"]) == D(del_resumen["total_ventas"])
    assert D(fila["total_gastos"]) == D(del_resumen["total_gastos"])
    assert D(fila["kilos_comprados"]) == D(del_resumen["kilos_comprados"])
    assert D(fila["kilos_vendidos"]) == D(del_resumen["kilos_vendidos"])
    assert D(fila["kilos_pendientes"]) == D(del_resumen["kilos_pendientes"])
    assert D(fila["margen_por_kilo"]) == D(del_resumen["margen_por_kilo"])
    # Y la cuenta a mano: 10.080.000 - 9.000.000 - 150.000
    assert D(fila["ganancia"]) == 930_000
    assert D(fila["kilos_pendientes"]) == 20
    assert fila["dias"] == 31


def test_la_ganancia_sigue_a_los_movimientos_si_se_corrige_un_precio(client, base_datos):
    """La ganancia NO está congelada, y eso es a propósito: si mañana se le
    corrige el precio a una compra de esa temporada, la cifra se mueve con ella.
    Una cifra guardada quedaría distinta de la del Resumen para el mismo rango."""
    h = auth_headers(client, "admin.a")
    c = compra(client, h, fecha="2026-03-04", productor="Hacienda Santa Bárbara",
               kilos_brutos="500", precio_kilo="18000")
    venta(client, h, fecha="2026-03-20", cliente="Depósito El Trébol", kilos="500",
          precio_kilo="21000", pagada_de_contado=True)
    crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                    fecha_fin="2026-03-31")
    antes = D(panel(client, h)["temporadas"][0]["ganancia"])

    # Se le corrige el precio: eran 17.500, no 18.000 (250.000 menos de costo)
    r = client.put(f"{API}/compras/{c['id']}", json={"precio_kilo": "17500"}, headers=h)
    assert r.status_code == 200, r.text
    despues = D(panel(client, h)["temporadas"][0]["ganancia"])

    print("\n===== 2. LA GANANCIA NO ESTÁ CONGELADA =====")
    print(f"  antes={antes} después={despues} diferencia={despues - antes}")
    assert antes == 1_500_000  # 10.500.000 - 9.000.000
    assert despues == 1_750_000  # 10.500.000 - 8.750.000
    # Y sigue coincidiendo con el Resumen
    assert despues == D(resumen(client, h, "2026-03-01", "2026-03-31")["ganancia_estimada"])


# ---------------------------------------------------------------------------
# 3. No se solapan: los mismos kilos no pueden caer en dos temporadas
# ---------------------------------------------------------------------------
def test_no_se_pueden_solapar_de_ninguna_forma(client, base_datos):
    h = auth_headers(client, "admin.a")
    assert crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                           fecha_fin="2026-03-31").status_code == 201

    print("\n===== 3. SOLAPES RECHAZADOS =====")
    casos = [
        ("idéntica", "2026-03-01", "2026-03-31"),
        ("contenida", "2026-03-10", "2026-03-20"),
        ("la contiene", "2026-02-01", "2026-04-30"),
        ("pisa el final", "2026-03-25", "2026-04-10"),
        ("pisa el inicio", "2026-02-15", "2026-03-05"),
        ("un solo día en común", "2026-03-31", "2026-04-15"),
    ]
    for etiqueta, desde, hasta in casos:
        r = crear_temporada(client, h, nombre=f"Cruce {etiqueta}",
                            fecha_inicio=desde, fecha_fin=hasta)
        print(f"  {etiqueta:22} {desde} -> {hasta}: {r.status_code}")
        assert r.status_code == 422, f"{etiqueta} se dejó crear: {r.text}"
        assert "cruza" in detalle(r).lower()

    # Pegadas SÍ: el 1 de abril empieza la siguiente y no hay día compartido
    r = crear_temporada(client, h, nombre="Abril", fecha_inicio="2026-04-01",
                        fecha_fin="2026-04-30")
    print(f"  {'pegada (sin cruce)':22} 2026-04-01 -> 2026-04-30: {r.status_code}")
    assert r.status_code == 201, r.text
    assert len(panel(client, h)["temporadas"]) == 2


def test_una_abierta_bloquea_lo_que_venga_despues(client, base_datos):
    """Una temporada ABIERTA llega hasta hoy y más allá: es la que está
    corriendo. La trampa es de SQL: si el solape se comparara contra un
    fecha_fin en NULL, la comparación daría NULL —ni verdadero ni falso— y el
    cruce pasaría de largo sin avisar. Por eso el repositorio usa COALESCE.
    """
    h = auth_headers(client, "admin.a")
    assert crear_temporada(client, h, nombre="La que corre",
                           fecha_inicio="2026-07-01").status_code == 201

    print("\n===== 4. LA ABIERTA NO DEJA CRUZARSE =====")
    # Otra abierta: no
    r = crear_temporada(client, h, nombre="Otra abierta", fecha_inicio="2026-08-01")
    print(f"  otra abierta:            {r.status_code} | {detalle(r)[:52]}")
    assert r.status_code == 422
    assert "abierta" in detalle(r).lower()
    # Una cerrada POSTERIOR al inicio de la abierta: tampoco, se cruzan
    r = crear_temporada(client, h, nombre="Agosto", fecha_inicio="2026-08-01",
                        fecha_fin="2026-08-31")
    print(f"  cerrada posterior:       {r.status_code} | {detalle(r)[:52]}")
    assert r.status_code == 422
    assert "cruza" in detalle(r).lower()
    # Una ANTERIOR que no la toca: sí
    r = crear_temporada(client, h, nombre="Junio", fecha_inicio="2026-06-01",
                        fecha_fin="2026-06-30")
    print(f"  anterior sin tocarla:    {r.status_code}")
    assert r.status_code == 201, r.text


def test_no_puede_terminar_antes_de_empezar(client, base_datos):
    h = auth_headers(client, "admin.a")
    r = crear_temporada(client, h, nombre="Al revés", fecha_inicio="2026-03-31",
                        fecha_fin="2026-03-01")
    print("\n===== 5. RANGO AL REVÉS =====")
    print(f"  {r.status_code} | {detalle(r)[:70]}")
    assert r.status_code == 422
    assert "antes de empezar" in detalle(r)


def test_editar_tambien_valida_el_cruce(client, base_datos):
    """Editar solo el inicio o solo el fin puede provocar un cruce igual que
    crear. Con exclude_unset hay que validar el rango RESULTANTE, no lo que
    llegó en el payload."""
    h = auth_headers(client, "admin.a")
    marzo = crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                            fecha_fin="2026-03-31").json()
    abril = crear_temporada(client, h, nombre="Abril", fecha_inicio="2026-04-01",
                            fecha_fin="2026-04-30").json()

    print("\n===== 6. EDITAR VALIDA EL CRUCE =====")
    # Estirar marzo hasta abril: cruce (solo se manda fecha_fin)
    r = client.put(f"{API}/temporadas/{marzo['id']}",
                   json={"fecha_fin": "2026-04-15"}, headers=h)
    print(f"  estirar el fin de marzo:   {r.status_code}")
    assert r.status_code == 422 and "cruza" in detalle(r).lower()
    # Correr el inicio de abril hacia atrás: cruce (solo se manda fecha_inicio)
    r = client.put(f"{API}/temporadas/{abril['id']}",
                   json={"fecha_inicio": "2026-03-20"}, headers=h)
    print(f"  correr el inicio de abril: {r.status_code}")
    assert r.status_code == 422 and "cruza" in detalle(r).lower()
    # Cambiar SOLO el nombre no se cruza consigo misma
    r = client.put(f"{API}/temporadas/{marzo['id']}",
                   json={"nombre": "Temporada de marzo"}, headers=h)
    print(f"  cambiar solo el nombre:    {r.status_code}")
    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == "Temporada de marzo"


# ---------------------------------------------------------------------------
# 7. Abrir, cerrar y reabrir
# ---------------------------------------------------------------------------
def test_la_abierta_se_calcula_hasta_hoy(client, base_datos):
    h = auth_headers(client, "admin.a")
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    compra(client, h, fecha=ayer.isoformat(), productor="Sebastián Ruiz",
           kilos_brutos="100", precio_kilo="18000")
    venta(client, h, fecha=hoy.isoformat(), cliente="Alba Nieto", kilos="100",
          precio_kilo="21000", pagada_de_contado=True)
    inicio = hoy - timedelta(days=10)
    crear_temporada(client, h, nombre="La que corre", fecha_inicio=inicio.isoformat())

    fila = panel(client, h)["temporadas"][0]
    print("\n===== 7. LA TEMPORADA ABIERTA =====")
    print(f"  abierta={fila['abierta']} calculada hasta {fila['fecha_fin']}"
          f" ({fila['dias']} días) ganancia={fila['ganancia']}")
    assert fila["abierta"] is True
    # El fin que reporta es HOY: así en pantalla se ve hasta dónde llegan las
    # cifras y no parecen ser de todo el año
    assert fila["fecha_fin"] == hoy.isoformat()
    assert fila["dias"] == 11
    assert D(fila["ganancia"]) == 300_000  # 2.100.000 - 1.800.000


def test_cerrar_reabrir_y_no_cerrar_dos_veces(client, base_datos):
    h = auth_headers(client, "admin.a")
    t = crear_temporada(client, h, nombre="La que corre",
                        fecha_inicio="2026-07-01").json()
    assert t["abierta"] is True

    print("\n===== 8. CERRAR Y REABRIR =====")
    r = client.post(f"{API}/temporadas/{t['id']}/cerrar",
                    json={"fecha_fin": "2026-07-25"}, headers=h)
    assert r.status_code == 200, r.text
    print(f"  cerrada el {r.json()['fecha_fin']} | abierta={r.json()['abierta']}")
    assert r.json()["fecha_fin"] == "2026-07-25"
    assert r.json()["abierta"] is False

    # Dos veces no
    r = client.post(f"{API}/temporadas/{t['id']}/cerrar", json={}, headers=h)
    print(f"  cerrar de nuevo: {r.status_code} | {detalle(r)[:48]}")
    assert r.status_code == 422 and "ya está cerrada" in detalle(r)

    # Reabrir (se cerró por equivocación)
    r = client.post(f"{API}/temporadas/{t['id']}/reabrir", headers=h)
    print(f"  reabierta: {r.status_code} | abierta={r.json()['abierta']}")
    assert r.status_code == 200, r.text
    assert r.json()["fecha_fin"] is None
    # Y reabrir dos veces tampoco
    r = client.post(f"{API}/temporadas/{t['id']}/reabrir", headers=h)
    assert r.status_code == 422 and "ya está abierta" in detalle(r)


def test_cerrar_sin_fecha_usa_hoy(client, base_datos):
    h = auth_headers(client, "admin.a")
    t = crear_temporada(client, h, nombre="La que corre",
                        fecha_inicio="2026-07-01").json()
    r = client.post(f"{API}/temporadas/{t['id']}/cerrar", json={}, headers=h)
    assert r.status_code == 200, r.text
    print("\n===== 9. CERRAR SIN FECHA =====")
    print(f"  fecha_fin={r.json()['fecha_fin']} (hoy={date.today().isoformat()})")
    assert r.json()["fecha_fin"] == date.today().isoformat()


def test_no_se_puede_reabrir_si_hay_otra_abierta(client, base_datos):
    h = auth_headers(client, "admin.a")
    junio = crear_temporada(client, h, nombre="Junio", fecha_inicio="2026-06-01",
                            fecha_fin="2026-06-30").json()
    crear_temporada(client, h, nombre="La que corre", fecha_inicio="2026-07-01")
    r = client.post(f"{API}/temporadas/{junio['id']}/reabrir", headers=h)
    print("\n===== 10. REABRIR CON OTRA ABIERTA =====")
    print(f"  {r.status_code} | {detalle(r)[:60]}")
    assert r.status_code == 422
    assert "abierta" in detalle(r).lower()


# ---------------------------------------------------------------------------
# 11. El panel: los totales son la SUMA EXACTA de las filas
# ---------------------------------------------------------------------------
def test_los_totales_suman_las_filas(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar_marzo_y_julio(client, h)
    crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                    fecha_fin="2026-03-31")
    crear_temporada(client, h, nombre="Julio", fecha_inicio="2026-07-01",
                    fecha_fin="2026-07-31")

    p = panel(client, h)
    filas = p["temporadas"]
    print("\n===== 11. EL PANEL CUADRA =====")
    for f in filas:
        print(f"  {f['nombre']:8} {f['fecha_inicio']} -> {f['fecha_fin']}"
              f" comprado={f['total_compras']:>12} vendido={f['total_ventas']:>12}"
              f" ganancia={f['ganancia']:>10}")
    print(f"  {'TOTAL':8} {'':25} comprado={p['total_compras']:>12}"
          f" vendido={p['total_ventas']:>12} ganancia={p['total_ganancia']:>10}")

    # Orden: la más reciente arriba
    assert [f["nombre"] for f in filas] == ["Julio", "Marzo"]
    # Los totales son la suma exacta de las filas listadas
    assert D(p["total_ganancia"]) == sum(D(f["ganancia"]) for f in filas)
    assert D(p["total_compras"]) == sum(D(f["total_compras"]) for f in filas)
    assert D(p["total_ventas"]) == sum(D(f["total_ventas"]) for f in filas)
    assert D(p["total_kilos_comprados"]) == sum(D(f["kilos_comprados"]) for f in filas)
    # Y a mano: 930.000 de marzo + 2.400.000 de julio
    assert D(p["total_ganancia"]) == 3_330_000
    assert p["mejor"] == "Julio" and p["peor"] == "Marzo"
    # Sin huecos: todos los movimientos caen dentro de una temporada
    assert p["dias_sin_temporada"] == 0
    # Propone el 1 de agosto para la siguiente (día después del último cierre)
    assert p["proximo_inicio"] == "2026-08-01"


def test_avisa_de_los_dias_que_quedan_fuera(client, base_datos):
    """Si hay movimientos fuera de toda temporada, la suma de las temporadas no
    da el total del negocio. El panel no puede callárselo."""
    h = auth_headers(client, "admin.a")
    sembrar_marzo_y_julio(client, h)
    # Solo se registra la de marzo: los dos días de julio quedan por fuera
    crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                    fecha_fin="2026-03-31")

    p = panel(client, h)
    print("\n===== 12. DÍAS FUERA DE TODA TEMPORADA =====")
    print(f"  temporadas={len(p['temporadas'])} ganancia listada={p['total_ganancia']}"
          f" días sueltos={p['dias_sin_temporada']}")
    assert p["dias_sin_temporada"] == 2  # el 02/07 (compra) y el 18/07 (venta)
    # El total sigue siendo la suma de lo listado, no del histórico: si trajera el
    # histórico, la lista no daría el total y el desglose no cuadraría
    assert D(p["total_ganancia"]) == 930_000


def test_cerrada_de_verdad_solo_cuando_no_falta_nada(client, base_datos):
    h = auth_headers(client, "admin.a")
    # 500 kg comprados a crédito, 300 vendidos a crédito: falta de todo
    c = compra(client, h, fecha="2026-03-04", productor="Sebastián Ruiz",
               kilos_brutos="500", precio_kilo="18000")
    v = venta(client, h, fecha="2026-03-20", cliente="Alba Nieto", kilos="300",
              precio_kilo="21000")
    crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                    fecha_fin="2026-03-31")

    fila = panel(client, h)["temporadas"][0]
    print("\n===== 13. CERRADA DE VERDAD =====")
    print(f"  falta: {fila['kilos_pendientes']} kg | cobrar {fila['por_cobrar']}"
          f" | pagar {fila['por_pagar']} -> cerrada={fila['cerrada_de_verdad']}")
    assert D(fila["kilos_pendientes"]) == 200
    assert D(fila["por_cobrar"]) == 6_300_000
    assert D(fila["por_pagar"]) == 9_000_000
    assert fila["cerrada_de_verdad"] is False

    # Se vende el resto, se cobra y se paga
    venta(client, h, fecha="2026-03-28", cliente="Alba Nieto", kilos="200",
          precio_kilo="21000", pagada_de_contado=True)
    client.post(f"{API}/ventas/{v['id']}/abonos",
                json={"fecha": "2026-03-29", "valor": "6300000"}, headers=h)
    client.post(f"{API}/compras/{c['id']}/abonos",
                json={"fecha": "2026-03-30", "valor": "9000000"}, headers=h)

    fila = panel(client, h)["temporadas"][0]
    print(f"  ya: {fila['kilos_pendientes']} kg | cobrar {fila['por_cobrar']}"
          f" | pagar {fila['por_pagar']} -> cerrada={fila['cerrada_de_verdad']}")
    assert D(fila["kilos_pendientes"]) == 0
    assert D(fila["por_cobrar"]) == 0 and D(fila["por_pagar"]) == 0
    assert fila["cerrada_de_verdad"] is True


def test_el_pendiente_es_del_periodo_no_la_cartera_de_siempre(client, base_datos):
    """Una temporada vieja ya cobrada NO puede aparecer con deuda por culpa de la
    que está corriendo. Es el error de usar la cartera histórica: la tarjeta
    "Por cobrar" del Resumen es de siempre, la de la temporada es de sus fechas.
    """
    h = auth_headers(client, "admin.a")
    # Marzo: se compra, se paga y se vende de contado -> queda en ceros
    marzo = compra(client, h, fecha="2026-03-04", productor="Sebastián Ruiz",
                   kilos_brutos="100", precio_kilo="18000")
    abonar_todo(client, h, marzo, "2026-03-05")
    venta(client, h, fecha="2026-03-20", cliente="Alba Nieto", kilos="100",
          precio_kilo="21000", pagada_de_contado=True)
    # Julio: a crédito los dos lados
    compra(client, h, fecha="2026-07-02", productor="Sebastián Ruiz",
           kilos_brutos="200", precio_kilo="17000")
    venta(client, h, fecha="2026-07-18", cliente="Alba Nieto", kilos="200",
          precio_kilo="20000")
    crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                    fecha_fin="2026-03-31")
    crear_temporada(client, h, nombre="Julio", fecha_inicio="2026-07-01",
                    fecha_fin="2026-07-31")

    por_nombre = {f["nombre"]: f for f in panel(client, h)["temporadas"]}
    print("\n===== 14. PENDIENTE DEL PERÍODO =====")
    for nombre in ("Marzo", "Julio"):
        f = por_nombre[nombre]
        print(f"  {nombre:6} cobrar={f['por_cobrar']:>10} pagar={f['por_pagar']:>10}"
              f" cerrada={f['cerrada_de_verdad']}")
    assert D(por_nombre["Marzo"]["por_cobrar"]) == 0
    assert D(por_nombre["Marzo"]["por_pagar"]) == 0
    assert por_nombre["Marzo"]["cerrada_de_verdad"] is True
    assert D(por_nombre["Julio"]["por_cobrar"]) == 4_000_000
    assert D(por_nombre["Julio"]["por_pagar"]) == 3_400_000
    assert por_nombre["Julio"]["cerrada_de_verdad"] is False


def test_el_libro_anterior_no_entra_en_ninguna_temporada(client, base_datos):
    """Los saldos del libro anterior vienen de otro sistema, no tienen kilos y no
    pertenecen a ninguna temporada. Si sumaran en el "por cobrar" de la temporada
    de su fecha, una temporada cerrada aparecería con deuda ajena."""
    h = auth_headers(client, "admin.a")
    pagada = compra(client, h, fecha="2026-03-04", productor="Sebastián Ruiz",
                    kilos_brutos="100", precio_kilo="18000")
    abonar_todo(client, h, pagada, "2026-03-05")
    venta(client, h, fecha="2026-03-20", cliente="Alba Nieto", kilos="100",
          precio_kilo="21000", pagada_de_contado=True)
    r = client.post(f"{API}/saldos-anteriores", headers=h, json={
        "tipo": "cobrar", "tercero": "Carlos Ricaute", "fecha": "2026-03-15",
        "concepto": "Factura 045 del sistema viejo", "valor_total": "5000000",
    })
    assert r.status_code == 201, r.text
    crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                    fecha_fin="2026-03-31")

    fila = panel(client, h)["temporadas"][0]
    print("\n===== 15. EL LIBRO ANTERIOR NO ES DE NINGUNA TEMPORADA =====")
    print(f"  cobrar de la temporada={fila['por_cobrar']} (hay 5.000.000 en el libro"
          f" viejo con fecha de marzo) cerrada={fila['cerrada_de_verdad']}")
    assert D(fila["por_cobrar"]) == 0
    assert fila["cerrada_de_verdad"] is True
    # Y el Resumen sí lo muestra, en su propio campo
    assert D(resumen(client, h, "2026-03-01", "2026-03-31")["por_cobrar_libro_anterior"]) == 5_000_000


# ---------------------------------------------------------------------------
# 16. Aislamiento entre empresas
# ---------------------------------------------------------------------------
def test_no_cruza_empresas(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    sembrar_marzo_y_julio(client, ha)
    crear_temporada(client, ha, nombre="Marzo de A", fecha_inicio="2026-03-01",
                    fecha_fin="2026-03-31")

    # La empresa B no ve nada, y puede usar las MISMAS fechas sin que le digan
    # que se cruza: el solape es por empresa
    pb = panel(client, hb)
    print("\n===== 16. AISLAMIENTO =====")
    print(f"  A: {len(panel(client, ha)['temporadas'])} temporadas |"
          f" B: {len(pb['temporadas'])} temporadas")
    assert pb["temporadas"] == []
    assert D(pb["total_ganancia"]) == 0
    r = crear_temporada(client, hb, nombre="Marzo de B", fecha_inicio="2026-03-01",
                        fecha_fin="2026-03-31")
    print(f"  B crea el mismo rango: {r.status_code}")
    assert r.status_code == 201, r.text
    # Y las cifras de B siguen en cero: los movimientos son de A
    fila = panel(client, hb)["temporadas"][0]
    assert D(fila["ganancia"]) == 0 and D(fila["kilos_comprados"]) == 0
    # A no perdió la suya
    assert len(panel(client, ha)["temporadas"]) == 1


def test_borrar_una_temporada_no_borra_los_movimientos(client, base_datos):
    h = auth_headers(client, "admin.a")
    sembrar_marzo_y_julio(client, h)
    t = crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                        fecha_fin="2026-03-31").json()
    antes = resumen(client, h, "2026-03-01", "2026-03-31")["ganancia_estimada"]

    r = client.delete(f"{API}/temporadas/{t['id']}", headers=h)
    assert r.status_code == 204, r.text
    print("\n===== 17. BORRAR LA TEMPORADA =====")
    print(f"  temporadas={len(panel(client, h)['temporadas'])}"
          f" | ganancia de marzo antes={antes}"
          f" después={resumen(client, h, '2026-03-01', '2026-03-31')['ganancia_estimada']}")
    assert panel(client, h)["temporadas"] == []
    # Las compras y las ventas siguen ahí: la temporada solo las agrupaba
    assert D(resumen(client, h, "2026-03-01", "2026-03-31")["ganancia_estimada"]) == D(antes)
    # Y el rango queda libre para volver a usarlo
    assert crear_temporada(client, h, nombre="Marzo otra vez",
                           fecha_inicio="2026-03-01",
                           fecha_fin="2026-03-31").status_code == 201


def test_reiniciar_la_empresa_borra_temporadas_y_libro_anterior(client, base_datos):
    """Reiniciar una empresa la deja en ceros. Los saldos del libro anterior son
    PLATA (suman en por cobrar y por pagar): si sobrevivieran, la cartera de una
    empresa recién reiniciada seguiría mostrando deuda. Y una temporada sin
    movimientos es un rango con nombre que ya no significa nada.
    """
    h = auth_headers(client, "admin.a")
    empresa_id = base_datos["empresa_a"].id
    nombre_empresa = base_datos["empresa_a"].nombre
    sembrar_marzo_y_julio(client, h)
    client.post(f"{API}/saldos-anteriores", headers=h, json={
        "tipo": "cobrar", "tercero": "Carlos Ricaute", "fecha": "2026-03-15",
        "concepto": "Factura 045", "valor_total": "5000000",
    })
    crear_temporada(client, h, nombre="Marzo", fecha_inicio="2026-03-01",
                    fecha_fin="2026-03-31")

    hs = auth_headers(client, "superadmin")
    r = client.post(
        f"/api/v1/empresas/{empresa_id}/reiniciar",
        json={"confirmacion": nombre_empresa}, headers=hs,
    )
    assert r.status_code == 200, r.text
    borrados = r.json().get("borrados", r.json())
    print("\n===== 18. REINICIAR LA EMPRESA =====")
    print(f"  temporadas={borrados.get('temporadas')}"
          f" saldos_anteriores={borrados.get('saldos_anteriores')}"
          f" compras={borrados.get('compras_queso')}")
    assert borrados.get("temporadas") == 1
    assert borrados.get("saldos_anteriores") == 1

    p = panel(client, h)
    assert p["temporadas"] == []
    # Y la cartera queda de verdad en ceros, sin deuda del libro viejo
    despues = resumen(client, h, "2026-01-01", "2026-12-31")
    print(f"  cartera después: cobrar={despues['por_cobrar_clientes']}"
          f" libro={despues['por_cobrar_libro_anterior']}")
    assert D(despues["por_cobrar_clientes"]) == 0
    assert D(despues["por_cobrar_libro_anterior"]) == 0


def test_las_temporadas_exigen_permiso(client, base_datos, db_session):
    """Ver el panel pide 'reventa:consultar'; crear pide 'reventa:crear'.

    Se usa el rol "Consulta" que ya trae la siembra, que es exactamente el caso
    real: la persona que solo mira los números no puede abrir ni cerrar ciclos.
    """
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.modules.usuarios.models import Rol, Usuario
    from tests.conftest import PASSWORD

    rol = db_session.scalars(select(Rol).where(Rol.nombre == "Consulta")).one()
    mirona = Usuario(
        nombre="Solo", apellido="Mira", correo="mira.temporadas@test.local",
        username="mira.temporadas", hashed_password=hash_password(PASSWORD),
        empresa_id=base_datos["empresa_a"].id,
    )
    mirona.roles = [rol]
    db_session.add(mirona)
    db_session.commit()

    h = auth_headers(client, "mira.temporadas")
    r_ver = client.get(f"{API}/temporadas", headers=h)
    r_crear = crear_temporada(client, h, nombre="No debería", fecha_inicio="2026-03-01")
    r_cerrar = client.post(f"{API}/temporadas/{'0' * 8}-0000-0000-0000-{'0' * 12}/cerrar",
                           json={}, headers=h)
    print("\n===== 19. PERMISOS =====")
    print(f"  solo con 'consultar': ver={r_ver.status_code}"
          f" crear={r_crear.status_code} cerrar={r_cerrar.status_code}")
    assert r_ver.status_code == 200, r_ver.text
    assert r_crear.status_code == 403, r_crear.text
    # Cerrar pide 'editar': tiene que dar 403 y no 404, o sea rechazarlo ANTES de
    # mirar si la temporada existe
    assert r_cerrar.status_code == 403, r_cerrar.text
