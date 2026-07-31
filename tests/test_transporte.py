"""El negocio paralelo de la turbo: viajes que llevan queso propio (ingreso
"interno" valorado a tarifa, sin cartera) y carga de terceros por kilo o a
precio fijo, con abonos por servicio. Los egresos del camión (gastos,
mantenimientos, documentos legales) van en su propio libro, aparte de la
contabilidad de la quesera, como el precedente de reventa.
"""
from datetime import date, timedelta
from uuid import uuid4

from tests.conftest import auth_headers

API = "/api/v1/transporte"


def crear_vehiculo(client, headers, placa="ABC123", **campos):
    payload = {"placa": placa, "nombre": "La Turbo", "tarifa_kilo": "1200", **campos}
    r = client.post(f"{API}/vehiculos", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def crear_viaje(client, headers, vehiculo_id, **campos):
    payload = {
        "vehiculo_id": vehiculo_id, "fecha_salida": "2026-07-10",
        "origen": "San José del Guaviare", "destino": "Villavicencio", **campos,
    }
    r = client.post(f"{API}/viajes", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def intentar_servicio(client, headers, viaje_id, **campos):
    """Agrega un flete al viaje y devuelve la respuesta cruda (sirve para
    comprobar tanto los creados como los rechazados)."""
    payload = {"descripcion": "Carga de queso", **campos}
    return client.post(f"{API}/viajes/{viaje_id}/servicios", json=payload, headers=headers)


def crear_servicio(client, headers, viaje_id, **campos):
    r = intentar_servicio(client, headers, viaje_id, **campos)
    assert r.status_code == 201, r.text
    return r.json()


def crear_cliente(client, headers, nombre="Alba Ricaute"):
    r = client.post("/api/v1/clientes", json={"nombre": nombre}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def abonar(client, headers, servicio_id, **campos):
    return client.post(f"{API}/servicios/{servicio_id}/abonos", json=campos, headers=headers)


def detalle_error(r) -> str:
    return r.json()["error"]["detail"].lower()


# ============================================================ vehículos y viajes
def test_placa_unica_por_empresa(client, base_datos):
    """La placa identifica el vehículo DENTRO de la empresa: escrita distinto
    sigue chocando, pero en otra empresa la misma placa es válida."""
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")

    vehiculo = crear_vehiculo(client, headers_a, placa="abc 123")
    # Se guarda normalizada: sin espacios y en mayúsculas
    assert vehiculo["placa"] == "ABC123"

    r = client.post(f"{API}/vehiculos", json={"placa": "ABC 123"}, headers=headers_a)
    assert r.status_code == 409, r.text
    assert "abc123" in detalle_error(r)

    # La restricción es por empresa, no global
    r = client.post(f"{API}/vehiculos", json={"placa": "ABC123"}, headers=headers_b)
    assert r.status_code == 201, r.text


def test_consecutivo_de_viajes_por_empresa(client, base_datos):
    headers_a = auth_headers(client, "admin.a")
    headers_b = auth_headers(client, "admin.b")
    vehiculo_a = crear_vehiculo(client, headers_a)
    vehiculo_b = crear_vehiculo(client, headers_b, placa="XYZ789")

    viaje_1 = crear_viaje(client, headers_a, vehiculo_a["id"])
    viaje_2 = crear_viaje(client, headers_a, vehiculo_a["id"], fecha_salida="2026-07-15")
    assert (viaje_1["numero"], viaje_2["numero"]) == (1, 2)
    # Todo viaje nace en curso
    assert viaje_1["estado"] == "en_curso"
    lista = client.get(f"{API}/viajes?estado=en_curso", headers=headers_a).json()
    assert lista["total"] == 2

    # La otra empresa arranca en 1: es SU consecutivo
    viaje_b = crear_viaje(client, headers_b, vehiculo_b["id"])
    assert viaje_b["numero"] == 1


# ==================================================================== servicios
def test_servicio_por_kilo_toma_la_tarifa_del_vehiculo(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)  # tarifa base $1.200
    cliente = crear_cliente(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"])

    # Sin tarifa en el servicio se cobra la base del vehículo: 38 kg × $1.200
    servicio = crear_servicio(client, headers, viaje["id"], cliente_id=cliente["id"], kilos="38")
    assert float(servicio["tarifa_kilo"]) == 1_200
    assert float(servicio["valor_total"]) == 45_600
    assert servicio["estado"] == "pendiente"
    assert float(servicio["saldo"]) == 45_600

    # Con tarifa propia manda la del servicio: 20 kg × $1.500
    servicio = crear_servicio(
        client, headers, viaje["id"], cliente_id=cliente["id"], kilos="20", tarifa_kilo="1500",
    )
    assert float(servicio["valor_total"]) == 30_000

    # Por kilo sin kilos no hay qué cobrar
    r = intentar_servicio(client, headers, viaje["id"], cliente_id=cliente["id"])
    assert r.status_code == 422
    assert "kilo" in detalle_error(r)

    # Y si NI el servicio NI el vehículo tienen tarifa, se exige definirla
    pelado = crear_vehiculo(client, headers, placa="SIN001", tarifa_kilo="0")
    viaje_pelado = crear_viaje(client, headers, pelado["id"])
    r = intentar_servicio(client, headers, viaje_pelado["id"], cliente_id=cliente["id"], kilos="10")
    assert r.status_code == 422
    assert "tarifa" in detalle_error(r)


def test_viaje_mixto_y_rentabilidad_del_detalle(client, base_datos):
    """En un mismo viaje conviven queso propio, carga por kilo y un trasteo a
    precio fijo en el regreso; el detalle del viaje ES el reporte de
    rentabilidad: servicios, gastos y totales en una sola respuesta."""
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    cliente = crear_cliente(client, headers)
    viaje = crear_viaje(
        client, headers, vehiculo["id"],
        conductor_nombre="Yojan", pago_conductor="200000", odometro_salida="1000",
    )

    # Ida: queso propio (20 kg a la tarifa) + carga de un tercero (38 kg)
    crear_servicio(client, headers, viaje["id"], es_interno=True, kilos="20",
                   descripcion="Queso propio")
    crear_servicio(client, headers, viaje["id"], cliente_id=cliente["id"], kilos="38")
    # Regreso: trasteo a precio fijo pagado de contado por un ocasional
    crear_servicio(
        client, headers, viaje["id"], sentido="regreso", tipo_cobro="precio_fijo",
        cliente_nombre="Don Pedro", valor_total="300000",
        descripcion="Trasteo", pagado_de_contado=True,
    )
    # Precio fijo sin valor acordado no existe
    r = intentar_servicio(
        client, headers, viaje["id"], tipo_cobro="precio_fijo",
        cliente_nombre="Otro", pagado_de_contado=True,
    )
    assert r.status_code == 422
    assert "precio fijo" in detalle_error(r)

    for categoria, valor in (("combustible", "150000"), ("peajes", "30000")):
        r = client.post(
            f"{API}/viajes/{viaje['id']}/gastos",
            json={"fecha": "2026-07-10", "categoria": categoria, "valor": valor},
            headers=headers,
        )
        assert r.status_code == 201, r.text

    detalle = client.get(f"{API}/viajes/{viaje['id']}", headers=headers).json()
    assert len(detalle["servicios"]) == 3
    assert len(detalle["gastos"]) == 2
    # Ingresos: 24.000 interno + 45.600 por kilo + 300.000 fijo = 369.600
    assert float(detalle["ingresos_internos"]) == 24_000
    assert float(detalle["ingresos_terceros"]) == 345_600
    assert float(detalle["total_ingresos"]) == 369_600
    # Gastos: 150.000 + 30.000 + 200.000 del conductor = 380.000
    assert float(detalle["total_gastos_viaje"]) == 380_000
    assert float(detalle["utilidad"]) == -10_400
    # De la cartera del viaje solo queda debiendo el flete a crédito
    assert float(detalle["saldo_cartera"]) == 45_600


def test_servicio_interno_sin_cliente_ni_cartera(client, base_datos):
    """El queso propio se valora a tarifa para medir la rentabilidad real del
    viaje, pero NO es plata que entre: sin cliente, sin abonos, sin cartera."""
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"])

    r = intentar_servicio(client, headers, viaje["id"], es_interno=True,
                          kilos="20", cliente_nombre="Alba")
    assert r.status_code == 422
    assert "cliente" in detalle_error(r)
    r = intentar_servicio(client, headers, viaje["id"], es_interno=True,
                          kilos="20", pagado_de_contado=True)
    assert r.status_code == 422
    assert "contado" in detalle_error(r)

    servicio = crear_servicio(client, headers, viaje["id"], es_interno=True, kilos="20")
    assert servicio["estado"] == "interno"
    assert servicio["cliente_id"] is None
    assert float(servicio["valor_total"]) == 24_000
    assert float(servicio["saldo"]) == 0

    # No recibe abonos ni aparece en la cartera de fletes
    r = abonar(client, headers, servicio["id"], fecha="2026-07-11", valor="1000")
    assert r.status_code == 422
    assert "interno" in detalle_error(r)
    assert client.get(f"{API}/cartera", headers=headers).json() == []

    # Pero SÍ vale en el resumen, como ingreso interno
    resumen = client.get(
        f"{API}/resumen-mensual?desde=2026-07-01&hasta=2026-07-31", headers=headers
    ).json()
    assert float(resumen["ingresos_internos"]) == 24_000
    assert float(resumen["ingresos_terceros"]) == 0
    assert float(resumen["por_cobrar"]) == 0


def test_credito_exige_cliente_del_directorio(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"])

    # Sin cliente no hay a quién cobrarle
    r = intentar_servicio(client, headers, viaje["id"], kilos="10")
    assert r.status_code == 422
    assert "cliente" in detalle_error(r)

    # Un ocasional (texto libre) NO puede quedar debiendo
    r = intentar_servicio(client, headers, viaje["id"], kilos="10",
                          cliente_nombre="Fulano de Tal")
    assert r.status_code == 422
    assert "contado" in detalle_error(r)

    # Con cliente del directorio el crédito sí camina, y el nombre queda
    # copiado del directorio para mostrarlo sin otra consulta
    cliente = crear_cliente(client, headers, nombre="Alba Ricaute")
    servicio = crear_servicio(client, headers, viaje["id"], cliente_id=cliente["id"], kilos="10")
    assert servicio["estado"] == "pendiente"
    assert servicio["cliente_nombre"] == "Alba Ricaute"

    # Un cliente que no existe da 404
    r = intentar_servicio(client, headers, viaje["id"], kilos="10", cliente_id=str(uuid4()))
    assert r.status_code == 404


def test_contado_deja_pagada_con_abono_automatico(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"])  # sale el 2026-07-10

    servicio = crear_servicio(
        client, headers, viaje["id"], tipo_cobro="precio_fijo",
        cliente_nombre="Don Pedro", valor_total="250000",
        descripcion="Trasteo", pagado_de_contado=True,
    )
    assert servicio["estado"] == "pagada"
    assert float(servicio["abonado"]) == 250_000
    assert float(servicio["saldo"]) == 0
    # El abono automático lleva la fecha de salida del viaje, en efectivo
    assert len(servicio["abonos"]) == 1
    abono = servicio["abonos"][0]
    assert abono["fecha"] == "2026-07-10"
    assert abono["metodo"] == "efectivo"
    assert float(abono["valor"]) == 250_000
    # Y un pagado de contado no aparece en la cartera
    assert client.get(f"{API}/cartera", headers=headers).json() == []


# ======================================================================= abonos
def test_abonos_parcial_completo_sobre_saldo_y_eliminar(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    cliente = crear_cliente(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"])
    servicio = crear_servicio(client, headers, viaje["id"],
                              cliente_id=cliente["id"], kilos="38")  # $45.600

    # Abono parcial
    r = abonar(client, headers, servicio["id"], fecha="2026-07-12", valor="20000")
    assert r.status_code == 200, r.text
    servicio = r.json()
    assert servicio["estado"] == "parcial"
    assert float(servicio["abonado"]) == 20_000
    assert float(servicio["saldo"]) == 25_600
    abono_parcial_id = servicio["abonos"][0]["id"]

    # Un abono mayor al saldo se rechaza
    r = abonar(client, headers, servicio["id"], fecha="2026-07-13", valor="99999999")
    assert r.status_code == 422
    assert "saldo" in detalle_error(r)

    # Completar el pago
    r = abonar(client, headers, servicio["id"], fecha="2026-07-15", valor="25600",
               metodo="transferencia", referencia="TX-99")
    assert r.status_code == 200, r.text
    servicio = r.json()
    assert servicio["estado"] == "pagada"
    assert float(servicio["saldo"]) == 0
    assert len(servicio["abonos"]) == 2

    # Pagado no recibe más plata
    r = abonar(client, headers, servicio["id"], fecha="2026-07-16", valor="1")
    assert r.status_code == 422

    # Eliminar un abono mal registrado recalcula lo abonado y el estado
    r = client.delete(
        f"{API}/servicios/{servicio['id']}/abonos/{abono_parcial_id}", headers=headers
    )
    assert r.status_code == 200, r.text
    servicio = r.json()
    assert servicio["estado"] == "parcial"
    assert float(servicio["abonado"]) == 25_600
    assert float(servicio["saldo"]) == 20_000
    assert len(servicio["abonos"]) == 1

    # Un abono que no existe da 404
    r = client.delete(f"{API}/servicios/{servicio['id']}/abonos/{uuid4()}", headers=headers)
    assert r.status_code == 404


def test_editar_servicio_respeta_lo_abonado(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    cliente = crear_cliente(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"])
    servicio = crear_servicio(client, headers, viaje["id"],
                              cliente_id=cliente["id"], kilos="38")  # $45.600
    r = abonar(client, headers, servicio["id"], fecha="2026-07-12", valor="40000")
    assert r.status_code == 200, r.text

    ruta = f"{API}/viajes/{viaje['id']}/servicios/{servicio['id']}"
    # Bajar los kilos dejaría el total por debajo de lo ya recibido
    r = client.put(ruta, json={"kilos": "30"}, headers=headers)  # $36.000 < $40.000
    assert r.status_code == 422
    assert "abonado" in detalle_error(r)

    # Subirlos recalcula el valor y el estado con lo ya abonado
    r = client.put(ruta, json={"kilos": "40"}, headers=headers)
    assert r.status_code == 200, r.text
    servicio = r.json()
    assert float(servicio["valor_total"]) == 48_000
    assert servicio["estado"] == "parcial"
    assert float(servicio["saldo"]) == 8_000

    # Y con plata recibida no se puede volver interno: borraría cartera real
    r = client.put(ruta, json={"es_interno": True}, headers=headers)
    assert r.status_code == 422
    assert "abonos" in detalle_error(r)


# ======================================================== ciclo de vida del viaje
def test_finalizar_valida_el_regreso_y_reabrir_permite_corregir(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers, odometro_actual="1000")
    cliente = crear_cliente(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"], odometro_salida="1000")
    servicio = crear_servicio(client, headers, viaje["id"], cliente_id=cliente["id"], kilos="38")

    ruta_finalizar = f"{API}/viajes/{viaje['id']}/finalizar"
    # Regresar antes de salir o con el odómetro devuelto es un dato mal digitado
    r = client.post(ruta_finalizar, json={"fecha_regreso": "2026-07-09"}, headers=headers)
    assert r.status_code == 422
    assert "regreso" in detalle_error(r)
    r = client.post(ruta_finalizar, json={"odometro_regreso": "900"}, headers=headers)
    assert r.status_code == 422
    assert "odómetro" in detalle_error(r)

    r = client.post(
        ruta_finalizar,
        json={"fecha_regreso": "2026-07-12", "odometro_regreso": "1350"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "finalizado"
    assert r.json()["fecha_regreso"] == "2026-07-12"
    # El odómetro del vehículo avanza con el regreso
    r = client.get(f"{API}/vehiculos/{vehiculo['id']}", headers=headers)
    assert float(r.json()["odometro_actual"]) == 1_350

    # Finalizado: no entran más servicios ni gastos, y no se edita sin reabrir
    r = intentar_servicio(client, headers, viaje["id"], cliente_id=cliente["id"], kilos="5")
    assert r.status_code == 422
    assert "finalizado" in detalle_error(r)
    r = client.post(
        f"{API}/viajes/{viaje['id']}/gastos",
        json={"fecha": "2026-07-12", "categoria": "combustible", "valor": "50000"},
        headers=headers,
    )
    assert r.status_code == 422
    r = client.put(f"{API}/viajes/{viaje['id']}", json={"origen": "Otra parte"}, headers=headers)
    assert r.status_code == 422
    assert "reábralo" in detalle_error(r)

    # Pero los abonos SIGUEN abiertos: la cartera se cobra después del viaje
    r = abonar(client, headers, servicio["id"], fecha="2026-07-20", valor="45600")
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "pagada"

    # No se finaliza dos veces
    r = client.post(ruta_finalizar, json={}, headers=headers)
    assert r.status_code == 422

    # Reabrir vuelve a en curso y deja corregir
    r = client.post(f"{API}/viajes/{viaje['id']}/reabrir", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "en_curso"
    crear_servicio(client, headers, viaje["id"], es_interno=True, kilos="10")

    # Reabrir un viaje en curso no significa nada
    r = client.post(f"{API}/viajes/{viaje['id']}/reabrir", headers=headers)
    assert r.status_code == 422


def test_anular_viaje_saca_su_plata_de_los_reportes(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    cliente = crear_cliente(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"])
    servicio = crear_servicio(client, headers, viaje["id"],
                              cliente_id=cliente["id"], kilos="38")  # $45.600
    r = client.post(
        f"{API}/viajes/{viaje['id']}/gastos",
        json={"fecha": "2026-07-10", "categoria": "combustible", "valor": "100000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    rango = "desde=2026-07-01&hasta=2026-07-31"
    resumen = client.get(f"{API}/resumen-mensual?{rango}", headers=headers).json()
    assert float(resumen["total_ingresos"]) == 45_600
    assert float(resumen["total_gastos"]) == 100_000
    assert float(resumen["por_cobrar"]) == 45_600

    # Con plata recibida no se anula: primero se devuelve (se elimina el abono)
    r = abonar(client, headers, servicio["id"], fecha="2026-07-12", valor="10000")
    assert r.status_code == 200, r.text
    abono_id = r.json()["abonos"][0]["id"]
    r = client.post(f"{API}/viajes/{viaje['id']}/anular", headers=headers)
    assert r.status_code == 422
    assert "abono" in detalle_error(r)

    r = client.delete(f"{API}/servicios/{servicio['id']}/abonos/{abono_id}", headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/viajes/{viaje['id']}/anular", headers=headers)
    assert r.status_code == 200, r.text
    viaje_anulado = r.json()
    assert viaje_anulado["estado"] == "anulado"
    # Los servicios caen en cascada
    assert [s["estado"] for s in viaje_anulado["servicios"]] == ["anulada"]

    # El viaje anulado desaparece de los números: ni ingresos, ni sus gastos,
    # ni cartera
    resumen = client.get(f"{API}/resumen-mensual?{rango}", headers=headers).json()
    assert resumen["viajes_realizados"] == 0
    assert float(resumen["total_ingresos"]) == 0
    assert float(resumen["total_gastos"]) == 0
    assert float(resumen["por_cobrar"]) == 0
    assert client.get(f"{API}/cartera", headers=headers).json() == []

    # Y no se toca más
    r = intentar_servicio(client, headers, viaje["id"], cliente_id=cliente["id"], kilos="5")
    assert r.status_code == 422
    assert "anulado" in detalle_error(r)
    r = client.post(f"{API}/viajes/{viaje['id']}/anular", headers=headers)
    assert r.status_code == 422


def test_anular_un_servicio_lo_saca_de_los_totales(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    cliente = crear_cliente(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"])
    crear_servicio(client, headers, viaje["id"], cliente_id=cliente["id"], kilos="38")
    anulable = crear_servicio(client, headers, viaje["id"], cliente_id=cliente["id"], kilos="10")

    # Con abonos no se anula
    r = abonar(client, headers, anulable["id"], fecha="2026-07-11", valor="5000")
    assert r.status_code == 200, r.text
    abono_id = r.json()["abonos"][0]["id"]
    ruta_anular = f"{API}/viajes/{viaje['id']}/servicios/{anulable['id']}/anular"
    r = client.post(ruta_anular, headers=headers)
    assert r.status_code == 422
    assert "abono" in detalle_error(r)

    client.delete(f"{API}/servicios/{anulable['id']}/abonos/{abono_id}", headers=headers)
    r = client.post(ruta_anular, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "anulada"
    assert float(r.json()["saldo"]) == 0

    # El detalle del viaje ya no lo suma
    detalle = client.get(f"{API}/viajes/{viaje['id']}", headers=headers).json()
    assert float(detalle["total_ingresos"]) == 45_600
    assert float(detalle["saldo_cartera"]) == 45_600

    # Anulado no recibe abonos ni se edita
    r = abonar(client, headers, anulable["id"], fecha="2026-07-12", valor="1000")
    assert r.status_code == 422
    r = client.put(f"{API}/viajes/{viaje['id']}/servicios/{anulable['id']}",
                   json={"kilos": "12"}, headers=headers)
    assert r.status_code == 422


def test_eliminar_servicio_viaje_y_vehiculo_con_sus_guardas(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    cliente = crear_cliente(client, headers)
    viaje = crear_viaje(client, headers, vehiculo["id"])
    servicio = crear_servicio(client, headers, viaje["id"], cliente_id=cliente["id"], kilos="38")
    r = abonar(client, headers, servicio["id"], fecha="2026-07-11", valor="10000")
    assert r.status_code == 200, r.text
    abono_id = r.json()["abonos"][0]["id"]

    ruta_servicio = f"{API}/viajes/{viaje['id']}/servicios/{servicio['id']}"
    # Con abonos ni el servicio ni el viaje se eliminan: es plata recibida
    assert client.delete(ruta_servicio, headers=headers).status_code == 422
    assert client.delete(f"{API}/viajes/{viaje['id']}", headers=headers).status_code == 422

    client.delete(f"{API}/servicios/{servicio['id']}/abonos/{abono_id}", headers=headers)
    assert client.delete(ruta_servicio, headers=headers).status_code == 204
    detalle = client.get(f"{API}/viajes/{viaje['id']}", headers=headers).json()
    assert detalle["servicios"] == []
    assert float(detalle["total_ingresos"]) == 0

    # Con un viaje registrado el vehículo no se borra: protege el histórico
    r = client.delete(f"{API}/vehiculos/{vehiculo['id']}", headers=headers)
    assert r.status_code == 422
    assert "viajes" in detalle_error(r)

    assert client.delete(f"{API}/viajes/{viaje['id']}", headers=headers).status_code == 204
    assert client.get(f"{API}/viajes/{viaje['id']}", headers=headers).status_code == 404


# ===================================================================== reportes
def test_resumen_mensual_cuadra_buckets_y_serie(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    cliente = crear_cliente(client, headers)

    # JUNIO: viaje finalizado con 400 km y un trasteo de contado
    viaje_junio = crear_viaje(
        client, headers, vehiculo["id"], fecha_salida="2026-06-10",
        pago_conductor="100000", odometro_salida="1000",
    )
    crear_servicio(
        client, headers, viaje_junio["id"], tipo_cobro="precio_fijo",
        cliente_nombre="Don Pedro", valor_total="200000",
        descripcion="Trasteo", pagado_de_contado=True,
    )
    r = client.post(
        f"{API}/viajes/{viaje_junio['id']}/gastos",
        json={"fecha": "2026-06-10", "categoria": "combustible", "valor": "80000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/viajes/{viaje_junio['id']}/finalizar",
        json={"fecha_regreso": "2026-06-12", "odometro_regreso": "1400"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    # JULIO: viaje en curso con queso propio y un flete a crédito
    viaje_julio = crear_viaje(
        client, headers, vehiculo["id"], fecha_salida="2026-07-05", pago_conductor="150000",
    )
    crear_servicio(client, headers, viaje_julio["id"], es_interno=True, kilos="20")
    crear_servicio(client, headers, viaje_julio["id"], cliente_id=cliente["id"], kilos="38")
    r = client.post(
        f"{API}/viajes/{viaje_julio['id']}/gastos",
        json={"fecha": "2026-07-05", "categoria": "peajes", "valor": "30000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    # Gasto general (sin viaje), mantenimiento y documentos: buckets aparte
    r = client.post(
        f"{API}/gastos",
        json={"vehiculo_id": vehiculo["id"], "fecha": "2026-06-20",
              "categoria": "combustible", "valor": "50000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/mantenimientos",
        json={"vehiculo_id": vehiculo["id"], "fecha": "2026-07-08",
              "descripcion": "Cambio de aceite", "valor": "500000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/documentos",
        json={"vehiculo_id": vehiculo["id"], "tipo": "soat",
              "fecha_expedicion": "2026-07-01", "fecha_vencimiento": "2027-07-01",
              "valor": "600000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    # Un documento SIN fecha de expedición no suma en ningún período
    r = client.post(
        f"{API}/documentos",
        json={"vehiculo_id": vehiculo["id"], "tipo": "tecnomecanica",
              "fecha_vencimiento": "2027-03-01", "valor": "999999"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    resumen = client.get(
        f"{API}/resumen-mensual?desde=2026-06-01&hasta=2026-07-31", headers=headers
    ).json()
    assert resumen["viajes_realizados"] == 2
    assert float(resumen["kilos_transportados"]) == 58     # 20 propios + 38 de terceros
    assert float(resumen["kilometros"]) == 400             # solo el viaje con ambos odómetros
    assert float(resumen["ingresos_terceros"]) == 245_600  # 200.000 + 45.600
    assert float(resumen["ingresos_internos"]) == 24_000
    assert float(resumen["total_ingresos"]) == 269_600
    assert float(resumen["total_pago_conductores"]) == 250_000
    assert float(resumen["gastos_por_categoria"]["combustible"]) == 130_000  # 80.000 + 50.000
    assert float(resumen["gastos_por_categoria"]["peajes"]) == 30_000
    assert float(resumen["total_gastos"]) == 160_000
    assert float(resumen["total_mantenimientos"]) == 500_000
    assert float(resumen["total_documentos"]) == 600_000   # el sin expedición no cuenta
    # Operativa: 269.600 - 160.000 - 250.000; la neta además resta
    # mantenimientos y documentos (buckets propios, sin doble conteo)
    assert float(resumen["utilidad_operativa"]) == -140_400
    assert float(resumen["utilidad_neta"]) == -1_240_400
    assert float(resumen["por_cobrar"]) == 45_600

    # La serie mensual usa los MISMOS buckets: cuadra con la utilidad neta
    assert [m["mes"] for m in resumen["serie_mensual"]] == ["2026-06", "2026-07"]
    junio, julio = resumen["serie_mensual"]
    assert float(junio["ingresos"]) == 200_000
    assert float(junio["gastos"]) == 230_000    # 80.000 + 50.000 + 100.000 conductor
    assert float(junio["utilidad"]) == -30_000
    assert float(julio["ingresos"]) == 69_600
    assert float(julio["gastos"]) == 1_280_000  # 30.000 + 150.000 + 500.000 + 600.000
    assert float(julio["utilidad"]) == -1_210_400
    assert sum(float(m["utilidad"]) for m in resumen["serie_mensual"]) == float(
        resumen["utilidad_neta"]
    )

    # Con rango de julio los totales bajan, pero el por_cobrar es HISTÓRICO
    solo_julio = client.get(
        f"{API}/resumen-mensual?desde=2026-07-01&hasta=2026-07-31", headers=headers
    ).json()
    assert solo_julio["viajes_realizados"] == 1
    assert float(solo_julio["total_ingresos"]) == 69_600
    assert float(solo_julio["por_cobrar"]) == 45_600

    # Y los meses sin movimiento salen en cero: la gráfica necesita el eje completo
    con_mayo = client.get(
        f"{API}/resumen-mensual?desde=2026-05-01&hasta=2026-07-31", headers=headers
    ).json()
    assert [m["mes"] for m in con_mayo["serie_mensual"]] == ["2026-05", "2026-06", "2026-07"]
    assert float(con_mayo["serie_mensual"][0]["ingresos"]) == 0


def test_cartera_agrupa_por_cliente_y_ordena_por_saldo(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    alba = crear_cliente(client, headers, nombre="Alba Ricaute")

    # Alba (directorio): dos fletes a crédito en viajes distintos y un abono
    viaje_1 = crear_viaje(client, headers, vehiculo["id"], fecha_salida="2026-07-05")
    crear_servicio(client, headers, viaje_1["id"], cliente_id=alba["id"], kilos="38")  # 45.600
    viaje_2 = crear_viaje(client, headers, vehiculo["id"], fecha_salida="2026-07-12")
    credito = crear_servicio(client, headers, viaje_2["id"], cliente_id=alba["id"],
                             tipo_cobro="precio_fijo", valor_total="300000")
    r = abonar(client, headers, credito["id"], fecha="2026-07-13", valor="100000")
    assert r.status_code == 200, r.text

    # Don Pedro (ocasional): pagó de contado pero el abono estaba mal digitado
    # y se eliminó, así que quedó debiendo — escrito de dos formas distintas
    for viaje_id, nombre, valor in (
        (viaje_1["id"], "Don Pedro", "250000"),
        (viaje_2["id"], "don pedro", "150000"),
    ):
        servicio = crear_servicio(
            client, headers, viaje_id, tipo_cobro="precio_fijo",
            cliente_nombre=nombre, valor_total=valor,
            descripcion="Trasteo", pagado_de_contado=True,
        )
        r = client.delete(
            f"{API}/servicios/{servicio['id']}/abonos/{servicio['abonos'][0]['id']}",
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == "pendiente"

    cartera = client.get(f"{API}/cartera", headers=headers).json()
    assert len(cartera) == 2
    # Don Pedro debe más y va primero; sus dos escrituras son UNA sola fila
    pedro, fila_alba = cartera
    assert pedro["cliente_id"] is None
    assert pedro["cliente_nombre"] == "Don Pedro"
    assert pedro["servicios_pendientes"] == 2
    assert float(pedro["saldo"]) == 400_000
    assert fila_alba["cliente_id"] == alba["id"]
    assert fila_alba["servicios_pendientes"] == 2
    assert float(fila_alba["total_facturado"]) == 345_600
    assert float(fila_alba["total_abonado"]) == 100_000
    assert float(fila_alba["saldo"]) == 245_600

    # Detalle del cliente del directorio: sus fletes del más viejo al más nuevo
    detalle = client.get(
        f"{API}/cartera/detalle", params={"cliente_id": alba["id"]}, headers=headers
    ).json()
    assert detalle["cliente_nombre"] == "Alba Ricaute"
    assert [s["viaje_numero"] for s in detalle["servicios"]] == [1, 2]
    assert float(detalle["saldo"]) == 245_600

    # Detalle del ocasional: se encuentra como sea que se escriba
    detalle = client.get(
        f"{API}/cartera/detalle", params={"cliente_nombre": "  DON pedro "}, headers=headers
    ).json()
    assert len(detalle["servicios"]) == 2
    assert float(detalle["saldo"]) == 400_000

    # Sin decir el cliente no hay detalle que dar
    r = client.get(f"{API}/cartera/detalle", headers=headers)
    assert r.status_code == 422


def test_alertas_de_documentos_por_dias(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)
    hoy = date.today()

    def crear_documento(tipo, vence_en_dias):
        r = client.post(
            f"{API}/documentos",
            json={"vehiculo_id": vehiculo["id"], "tipo": tipo,
                  "fecha_vencimiento": (hoy + timedelta(days=vence_en_dias)).isoformat()},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        return r.json()

    crear_documento("soat", 10)
    crear_documento("seguro", 60)         # lejos: no alerta con el umbral de 30
    crear_documento("tecnomecanica", -5)  # ya vencida

    alertas = client.get(f"{API}/alertas", headers=headers).json()
    # De la más urgente a la menos: la vencida primero
    assert [d["tipo"] for d in alertas["documentos"]] == ["tecnomecanica", "soat"]
    vencida, soat = alertas["documentos"]
    assert vencida["dias_restantes"] == -5
    assert vencida["estado"] == "vencido"
    assert soat["dias_restantes"] == 10
    assert soat["estado"] == "por_vencer"
    assert soat["vehiculo_placa"] == "ABC123"

    # Con más días de umbral el seguro también asoma
    lejos = client.get(f"{API}/alertas?dias=90", headers=headers).json()
    assert {d["tipo"] for d in lejos["documentos"]} == {"tecnomecanica", "soat", "seguro"}

    # Renovar el SOAT es un registro NUEVO: la alerta solo mira el más reciente
    crear_documento("soat", 365)
    alertas = client.get(f"{API}/alertas", headers=headers).json()
    assert [d["tipo"] for d in alertas["documentos"]] == ["tecnomecanica"]


def test_alertas_de_mantenimiento_por_fecha_y_odometro(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers, odometro_actual="10000")
    hoy = date.today()

    def crear_mantenimiento(**campos):
        r = client.post(
            f"{API}/mantenimientos",
            json={"vehiculo_id": vehiculo["id"], "fecha": hoy.isoformat(),
                  "descripcion": "Revisión", **campos},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        return r.json()

    # Preventivo: el registro VIEJO anunciaba un próximo ya pasado, pero el más
    # reciente lo corrió; solo cuenta el último por (vehículo, tipo)
    crear_mantenimiento(fecha=(hoy - timedelta(days=60)).isoformat(), proximo_odometro="9900")
    crear_mantenimiento(fecha=(hoy - timedelta(days=10)).isoformat(), proximo_odometro="10300")
    # Correctivo: anuncia el próximo por fecha, a 5 días
    crear_mantenimiento(tipo="correctivo", proxima_fecha=(hoy + timedelta(days=5)).isoformat())

    alertas = client.get(f"{API}/alertas", headers=headers).json()
    por_tipo = {m["tipo"]: m for m in alertas["mantenimientos"]}
    assert set(por_tipo) == {"preventivo", "correctivo"}
    # Quedan 10.300 - 10.000 = 300 km, por debajo del umbral de 500
    assert float(por_tipo["preventivo"]["km_restantes"]) == 300
    assert por_tipo["preventivo"]["estado"] == "por_vencer"
    assert por_tipo["correctivo"]["dias_restantes"] == 5
    assert por_tipo["correctivo"]["estado"] == "por_vencer"

    # Con umbral de 100 km el preventivo todavía no suena
    cerca = client.get(f"{API}/alertas?umbral_km=100", headers=headers).json()
    assert {m["tipo"] for m in cerca["mantenimientos"]} == {"correctivo"}

    # Si el camión ya pasó el odómetro anunciado, el próximo está VENCIDO
    r = client.put(f"{API}/vehiculos/{vehiculo['id']}",
                   json={"odometro_actual": "10400"}, headers=headers)
    assert r.status_code == 200, r.text
    alertas = client.get(f"{API}/alertas", headers=headers).json()
    por_tipo = {m["tipo"]: m for m in alertas["mantenimientos"]}
    assert float(por_tipo["preventivo"]["km_restantes"]) == -100
    assert por_tipo["preventivo"]["estado"] == "vencido"


# ===================================================================== adjuntos
def test_adjuntos_de_gasto_mantenimiento_y_documento(client, base_datos):
    headers = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers)

    r = client.post(
        f"{API}/gastos",
        json={"vehiculo_id": vehiculo["id"], "fecha": "2026-07-10",
              "categoria": "combustible", "valor": "80000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    gasto = r.json()
    assert gasto["adjunto_url"] is None

    r = client.post(
        f"{API}/gastos/{gasto['id']}/adjunto",
        files={"file": ("factura.png", b"png de mentira", "image/png")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    adjunto_url = r.json()["adjunto_url"]
    assert adjunto_url and "transporte/" in adjunto_url
    assert adjunto_url.endswith(".png")

    # Un ejecutable no es un soporte
    r = client.post(
        f"{API}/gastos/{gasto['id']}/adjunto",
        files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
        headers=headers,
    )
    assert r.status_code == 422

    r = client.post(
        f"{API}/mantenimientos",
        json={"vehiculo_id": vehiculo["id"], "fecha": "2026-07-10",
              "descripcion": "Cambio de aceite", "valor": "200000"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/mantenimientos/{r.json()['id']}/adjunto",
        files={"file": ("factura.pdf", b"%PDF de mentira", "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["adjunto_url"].endswith(".pdf")

    r = client.post(
        f"{API}/documentos",
        json={"vehiculo_id": vehiculo["id"], "tipo": "soat",
              "fecha_vencimiento": "2027-07-01"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"{API}/documentos/{r.json()['id']}/adjunto",
        files={"file": ("soat.pdf", b"%PDF de mentira", "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["adjunto_url"].endswith(".pdf")


# ===================================================================== permisos
def test_transporte_exige_permisos_por_accion(client, base_datos, db_session):
    """El rol 'Consulta' de la siembra solo mira; 'Supervisor' crea y edita
    pero no anula ni elimina (anular pide 'administrar')."""
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.modules.usuarios.models import Rol, Usuario
    from tests.conftest import PASSWORD

    def usuario_con_rol(nombre_rol, username):
        rol = db_session.scalars(select(Rol).where(Rol.nombre == nombre_rol)).one()
        usuario = Usuario(
            nombre=username.title(), apellido="Prueba",
            correo=f"{username}@test.local", username=username,
            hashed_password=hash_password(PASSWORD),
            empresa_id=base_datos["empresa_a"].id,
        )
        usuario.roles = [rol]
        db_session.add(usuario)

    usuario_con_rol("Consulta", "mira.transporte")
    usuario_con_rol("Supervisor", "supervisa.transporte")
    db_session.commit()

    headers_admin = auth_headers(client, "admin.a")
    vehiculo = crear_vehiculo(client, headers_admin)

    # Consulta: ve todo, no toca nada
    h = auth_headers(client, "mira.transporte")
    assert client.get(f"{API}/viajes", headers=h).status_code == 200
    assert client.get(f"{API}/vehiculos", headers=h).status_code == 200
    assert client.get(f"{API}/cartera", headers=h).status_code == 200
    assert client.get(f"{API}/alertas", headers=h).status_code == 200
    r = client.post(
        f"{API}/viajes",
        json={"vehiculo_id": vehiculo["id"], "fecha_salida": "2026-07-10",
              "origen": "San José", "destino": "Villavicencio"},
        headers=h,
    )
    assert r.status_code == 403, r.text
    assert client.post(f"{API}/vehiculos", json={"placa": "NOP111"}, headers=h).status_code == 403

    # Supervisor: crea y edita, pero anular y eliminar no son lo suyo
    h = auth_headers(client, "supervisa.transporte")
    viaje = crear_viaje(client, h, vehiculo["id"])
    r = client.put(f"{API}/viajes/{viaje['id']}", json={"origen": "Calamar"}, headers=h)
    assert r.status_code == 200, r.text
    # El 403 llega ANTES de mirar si el viaje existe
    assert client.post(f"{API}/viajes/{viaje['id']}/anular", headers=h).status_code == 403
    assert client.delete(f"{API}/viajes/{viaje['id']}", headers=h).status_code == 403
