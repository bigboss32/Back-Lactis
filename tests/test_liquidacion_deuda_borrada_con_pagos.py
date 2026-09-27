"""LA DEUDA QUE BORRÓ LA MIGRACIÓN, MEDIDA CONTRA LOS PAGOS DE VERDAD.

La migración a5e7c1b4d9f2 (01/08/2026) les escribió a las 'pagada' de antes pagado =
valor_total − anticipos, sin renglón de pago. La de julio —90 L × $2.000 = $180.000
contra $300.000 de adelanto— quedó con pagado −$120.000 y saldo 0: el tercero debe
$120.000 y la fila no lo dice.

La primera versión de `deuda_borrada_por_la_migracion` era "−pagado si es negativo", y
mentía en cuanto la fila recibía un pago real. En producción hubo dos meses con
Corregir y Pagar abiertos sobre esas filas, así que aquí se rehacen por el camino real
—corregir y pagar con el guardia apagado SOLO en ese paso— y se mide:

  · la cifra es Σ(pagos) − pagado, y da $120.000 con y sin pagos encima;
  · lo que de verdad falta entregar es siempre saldo − esa cifra, también después de
    borrar un pago;
  · las quincenas sanas dan cero, incluida la 'pagada' de antes que SÍ tenía neto;
  · la versión SQL (la de las tarjetas) da lo mismo que la de Python, fila por fila;
  · las tarjetas no prometen "por pagar" una plata que el servidor no deja pagar.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.modules.liquidaciones.models import Liquidacion, PagoLiquidacion
from tests.conftest import auth_headers
from tests.test_liquidacion_migrada_deuda_borrada import (
    _correr_la_migracion,
    _detalle,
    _dia,
    _julio_aprobada,
    _leer,
    _proveedor,
)
from tests.test_liquidacion_resumen_de_las_tarjetas import (
    _como_numeros,
    _resumen,
    _tarjetas_como_las_armaba_la_pantalla,
)

V = "/api/v1"
API = f"{V}/liquidaciones"


def D(v):
    return Decimal(str(v))


def _entregado(liq):
    return sum((D(p["valor"]) for p in liq["pagos"]), D(0))


def _cuadra(liq):
    """Las dos cuentas que el dueño hace con calculadora sobre una fila migrada:
    la regla de oro (neto = pagado + saldo) y la verdad (neto − lo entregado, que
    negativo es lo que el tercero debe) = saldo − la deuda borrada."""
    neto = D(liq["neto_a_pagar"])
    assert neto == D(liq["pagado"]) + D(liq["saldo"])
    assert neto - _entregado(liq) == D(liq["saldo"]) - D(liq["deuda_borrada_por_la_migracion"])


def _migradas(client, h, db, *nombres):
    """Una quincena de julio por nombre, TODAS 'pagada' con el botón de antes, y después
    UNA corrida de la migración. Van juntas porque la migración reescribe toda 'pagada':
    correrla después de pagar algo le pasaría por encima."""
    filas = {}
    for nombre in nombres:
        prov, liq = _julio_aprobada(client, h, nombre, "90", "300000")
        filas[nombre] = (prov, liq["id"])
    for _, liq_id in filas.values():
        db.get(Liquidacion, uuid.UUID(liq_id)).estado = "pagada"
    db.commit()
    _correr_la_migracion(db)
    for _, liq_id in filas.values():
        leida = _leer(client, h, liq_id)
        assert D(leida["pagado"]) == D("-120000") and D(leida["saldo"]) == 0
    return filas


def _antes_del_guardia(monkeypatch):
    """El código de agosto, sin `_exigir_sin_deuda_borrada`: así se corrigieron y
    pagaron esas filas en producción. Se vuelve al código de hoy con
    `monkeypatch.undo()` (y si la prueba se cae antes, lo deshace el fixture)."""
    import app.modules.liquidaciones.service as servicio

    monkeypatch.setattr(servicio, "_exigir_sin_deuda_borrada", lambda *a, **k: None)


def _corregir(client, h, liq_id, dia_id):
    r = client.post(f"{API}/{liq_id}/corregir",
                    json={"motivo": "día olvidado", "recepciones_a_incluir": [dia_id]},
                    headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _abonar(client, h, liq_id, valor):
    r = client.post(f"{API}/{liq_id}/pagos", json={"fecha": "2026-08-10", "valor": valor},
                    headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------------------
def test_corregida_con_50000_y_pagada_sigue_diciendo_los_120000(
        client, base_datos, db_session, monkeypatch):
    """Día olvidado de 25 L × $2.000 = $50.000: neto 230.000 − 300.000 = −70.000, saldo
    −70.000 − (−120.000) = +50.000, y Pagar entregó esos $50.000: pagado −70.000.
    Con calculadora: debe $70.000 del neto y se le entregaron $50.000 → debe $120.000."""
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Mas Cincuenta").values()
    olvidado = _dia(client, h, prov, "2026-07-08", "25")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, olvidado)
    assert client.post(f"{API}/{liq_id}/pagar", headers=h).status_code == 200
    monkeypatch.undo()

    hoy = _leer(client, h, liq_id)
    print(f"\n  estado={hoy['estado']} v{hoy['version']} neto={hoy['neto_a_pagar']} "
          f"pagado={hoy['pagado']} entregado={_entregado(hoy)} "
          f"borrada={hoy['deuda_borrada_por_la_migracion']}")
    assert (D(hoy["neto_a_pagar"]), D(hoy["pagado"]), _entregado(hoy)) == (
        D("-70000"), D("-70000"), D("50000"))
    assert D(hoy["deuda_borrada_por_la_migracion"]) == D("120000")
    _cuadra(hoy)

    otro = _dia(client, h, prov, "2026-07-09", "5")
    intentos = {
        "abonar": client.post(f"{API}/{liq_id}/pagos",
                              json={"fecha": "2026-08-10", "valor": "1000"}, headers=h),
        "pagar": client.post(f"{API}/{liq_id}/pagar", headers=h),
        "previsualizar": client.post(f"{API}/{liq_id}/corregir/previsualizar", json={
            "motivo": "otro día", "recepciones_a_incluir": [otro]}, headers=h),
    }
    for nombre, r in intentos.items():
        print(f"  {nombre} -> {r.status_code} {_detalle(r)!r}")
        assert r.status_code == 422, (nombre, r.text)
        assert "($120.000)" in _detalle(r) and "($70.000)" not in _detalle(r), nombre


def test_corregida_con_200000_y_pagada_ya_no_se_le_escapa_al_guardia(
        client, base_datos, db_session, monkeypatch):
    """Día olvidado de 100 L = $200.000: neto 80.000, saldo 80.000 − (−120.000) =
    200.000, y Pagar entregó $200.000: pagado $80.000, 'pagada'. `pagado` ya no es
    negativo, pero la caja entregó $200.000 contra un neto de $80.000: debe $120.000.
    Con la cifra vieja (el signo de `pagado`) esto daba $0 y la siguiente corrección
    volvía a mandar a Pagar."""
    h = auth_headers(client, "admin.a")
    (prov, liq_id), = _migradas(client, h, db_session, "Paso De Cero").values()
    grande = _dia(client, h, prov, "2026-07-08", "100")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, liq_id, grande)
    assert client.post(f"{API}/{liq_id}/pagar", headers=h).status_code == 200
    monkeypatch.undo()

    hoy = _leer(client, h, liq_id)
    assert hoy["estado"] == "pagada"
    assert (D(hoy["pagado"]), _entregado(hoy)) == (D("80000"), D("200000"))
    assert D(hoy["deuda_borrada_por_la_migracion"]) == D("120000")
    _cuadra(hoy)

    otro = _dia(client, h, prov, "2026-07-10", "25")               # $50.000
    cuerpo = {"motivo": "otro día", "recepciones_a_incluir": [otro]}
    prev = client.post(f"{API}/{liq_id}/corregir/previsualizar", json=cuerpo, headers=h)
    hecho = client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)
    print(f"\n  previsualizar -> {prev.status_code} {_detalle(prev)!r}")
    # Antes: 200 y "faltan $50.000 por entregar", a quien debe $70.000 después del día.
    assert prev.status_code == 422 and "repararla" in _detalle(prev)
    assert "($120.000)" in _detalle(prev)
    assert hecho.status_code == 422 and _detalle(hecho) == _detalle(prev)
    assert _leer(client, h, liq_id)["version"] == hoy["version"]


def test_al_borrar_un_pago_la_cifra_sigue_diciendo_la_verdad(
        client, base_datos, db_session, monkeypatch):
    """En la fila con deuda borrada `eliminar_pago` ya NO recorta a cero: pagado =
    pagado − valor. Así Σ(pagos) − pagado sigue siendo lo que borró la migración
    ($120.000) toda la vida de la fila, y el saldo se sigue recalculando como siempre
    (neto − pagado). Con el recorte, Dos-abonos quedaba en "borrada $50.000", que no era
    ni lo que se borró ni lo que se debe.

      · Borra-50: la de $50.000 de arriba, y se borra ese pago. −70.000 − 50.000 =
        pagado −120.000; saldo −70.000 − (−120.000) = +50.000; borrada 0 − (−120.000) =
        $120.000. Verdad: neto −70.000 sin nada entregado = debe $70.000 = saldo
        50.000 − borrada 120.000.
      · Dos-abonos: la de $200.000, pagada en dos abonos de $150.000 y $50.000, y se
        borra el de $150.000. 80.000 − 150.000 = pagado −70.000; saldo 80.000 −
        (−70.000) = $150.000; pagos $50.000; borrada 50.000 − (−70.000) = $120.000.
        Verdad: neto 80.000 − 50.000 entregados = le faltan $30.000 = saldo 150.000 −
        borrada 120.000.
    """
    h = auth_headers(client, "admin.a")
    filas = _migradas(client, h, db_session, "Borra Cincuenta", "Dos Abonos")
    (p1, borra), (p2, dos) = filas["Borra Cincuenta"], filas["Dos Abonos"]
    d1 = _dia(client, h, p1, "2026-07-08", "25")
    d2 = _dia(client, h, p2, "2026-07-08", "100")
    _antes_del_guardia(monkeypatch)
    _corregir(client, h, borra, d1)
    assert client.post(f"{API}/{borra}/pagar", headers=h).status_code == 200
    _corregir(client, h, dos, d2)
    _abonar(client, h, dos, "150000")
    _abonar(client, h, dos, "50000")
    monkeypatch.undo()

    for liq_id, valor in ((borra, "50000"), (dos, "150000")):
        antes = _leer(client, h, liq_id)
        assert D(antes["deuda_borrada_por_la_migracion"]) == D("120000")
        pago = next(p for p in antes["pagos"] if D(p["valor"]) == D(valor))
        r = client.delete(f"{API}/{liq_id}/pagos/{pago['id']}", headers=h)
        assert r.status_code == 200, r.text
        # La respuesta del DELETE y la lectura dicen lo mismo.
        assert D(r.json()["deuda_borrada_por_la_migracion"]) == D(
            _leer(client, h, liq_id)["deuda_borrada_por_la_migracion"])

    uno, otro = _leer(client, h, borra), _leer(client, h, dos)
    print(f"\n  borra-50: estado={uno['estado']} pagado={uno['pagado']} saldo={uno['saldo']} "
          f"debe={uno['le_queda_debiendo']} borrada={uno['deuda_borrada_por_la_migracion']}")
    print(f"  dos-abonos: estado={otro['estado']} pagado={otro['pagado']} "
          f"saldo={otro['saldo']} pagos={_entregado(otro)} "
          f"borrada={otro['deuda_borrada_por_la_migracion']}")
    assert (D(uno["pagado"]), D(uno["saldo"]), _entregado(uno)) == (
        D("-120000"), D("50000"), D("0"))
    assert D(uno["deuda_borrada_por_la_migracion"]) == D("120000")
    assert (D(otro["pagado"]), D(otro["saldo"]), _entregado(otro)) == (
        D("-70000"), D("150000"), D("50000"))
    assert D(otro["deuda_borrada_por_la_migracion"]) == D("120000")
    # El saldo, recalculado como siempre: neto − pagado (y la verdad, saldo − borrada).
    _cuadra(uno)
    _cuadra(otro)
    # Las dos son versión 2 y les queda saldo: 'parcial', como deja cualquier corregida.
    assert uno["estado"] == otro["estado"] == "parcial"
    # Y el guardia sigue la cifra y dice la posición de hoy: Borra-50 debe $70.000; a
    # Dos-abonos le faltan $30.000 y pagarle el saldo le entregaría $120.000 de más.
    r = client.post(f"{API}/{borra}/pagar", headers=h)
    assert r.status_code == 422 and "($120.000)" in _detalle(r)
    assert "todavía le debe $70.000 a la quesera" in _detalle(r)
    r = client.post(f"{API}/{dos}/pagar", headers=h)
    assert r.status_code == 422 and "($120.000)" in _detalle(r)
    assert "falta entregarle es $30.000" in _detalle(r)
    assert "$120.000 de más" in _detalle(r) and "todavía debe" not in _detalle(r)


def test_las_sanas_dan_cero_incluida_la_pagada_de_antes_que_si_tenia_neto(
        client, base_datos, db_session):
    """La migración también les escribió pagado = neto a las 'pagada' de antes que SÍ
    tenían algo por entregar: 200 L × $2.000 = $400.000 − $100.000 de adelanto → pagado
    $300.000 sin renglón. Eso se pagó por fuera del sistema y no se borró nada: 0 − 300.000
    es negativo, cero. Y se sigue corrigiendo como siempre."""
    h = auth_headers(client, "admin.a")
    prov_v, vieja = _julio_aprobada(client, h, "Vieja Con Neto", "200", "100000")
    db_session.get(Liquidacion, uuid.UUID(vieja["id"])).estado = "pagada"
    db_session.commit()
    _correr_la_migracion(db_session)
    vieja = _leer(client, h, vieja["id"])
    assert (D(vieja["pagado"]), D(vieja["saldo"]), vieja["pagos"]) == (D("300000"), 0, [])

    # Las de siempre, generadas después de la migración (agosto).
    sanas = {}
    for nombre, litros, adelanto in (("Pagada Normal", "200", "100000"),
                                     ("Parcial Normal", "200", "0"),
                                     ("Aprobada Normal", "150", "0")):
        prov = _proveedor(client, h, nombre)
        _dia(client, h, prov, "2026-08-04", litros)
        if adelanto != "0":
            r = client.post(f"{V}/anticipos", json={"tipo": "proveedor", "proveedor_id": prov,
                                                    "fecha": "2026-08-02", "valor": adelanto},
                            headers=h)
            assert r.status_code == 201, r.text
        sanas[nombre] = prov
    r = client.post(f"{API}/generar", json={"periodo_inicio": "2026-08-01",
                                            "periodo_fin": "2026-08-15", "tipo": "proveedor"},
                    headers=h)
    ids = {n: next(x["id"] for x in r.json()["generadas"] if x["proveedor_id"] == p)
           for n, p in sanas.items()}
    for liq_id in ids.values():
        assert client.post(f"{API}/{liq_id}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{ids['Pagada Normal']}/pagar", headers=h).status_code == 200
    _abonar(client, h, ids["Parcial Normal"], "100000")

    for liq_id in (vieja["id"], *ids.values()):
        leida = _leer(client, h, liq_id)
        assert D(leida["deuda_borrada_por_la_migracion"]) == 0, leida["estado"]
    olvidado = _dia(client, h, prov_v, "2026-07-08", "25")
    prev = client.post(f"{API}/{vieja['id']}/corregir/previsualizar", json={
        "motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}, headers=h)
    assert prev.status_code == 200, prev.text
    assert D(prev.json()["saldo_despues"]) == D("50000")


# ---------------------------------------------------------------------------------------
def _escribir(db, empresa_id, tercero_id, *, estado, valor_total, anticipos="0",
              pagado="0", pagos=(), version=1, tipo="proveedor", borrada=False,
              periodo=(date(2026, 7, 1), date(2026, 7, 15))):
    """Una fila escrita directo en la tabla, con sus renglones de pago. Cumple siempre
    neto = pagado + saldo."""
    vt, an, pg = D(valor_total), D(anticipos), D(pagado)
    liq = Liquidacion(
        empresa_id=empresa_id, tipo=tipo,
        proveedor_id=tercero_id if tipo == "proveedor" else None,
        transportador_id=tercero_id if tipo == "transportador" else None,
        periodo_inicio=periodo[0], periodo_fin=periodo[1], total_litros=D("100"),
        valor_bruto=vt, valor_total=vt, anticipos=an, saldo_anterior=D(0), pagado=pg,
        saldo=vt - an - pg, estado=estado, version=version,
    )
    if borrada:
        liq.deleted_at = datetime.now(timezone.utc)
    db.add(liq)
    db.flush()
    for valor in pagos:
        db.add(PagoLiquidacion(liquidacion_id=liq.id, fecha=date(2026, 8, 10),
                               valor=D(valor)))
    db.flush()
    return liq


def test_la_cifra_en_sql_es_la_de_python_fila_por_fila(
        client, base_datos, db_session, monkeypatch):
    """UNA regla, dos idiomas: la propiedad que lee cada fila (y el guardia) y la
    expresión SQL que suman las tarjetas. Se comparan sobre todas las formas de arriba
    y unas escritas a mano con centavos, por la API, por el ORM y por SQL."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    filas = _migradas(client, h, db_session, "Sin Tocar", "Mas 50", "Mas 200", "Borra")
    _antes_del_guardia(monkeypatch)
    for nombre, litros in (("Mas 50", "25"), ("Mas 200", "100"), ("Borra", "25")):
        prov, liq_id = filas[nombre]
        _corregir(client, h, liq_id, _dia(client, h, prov, "2026-07-08", litros))
        assert client.post(f"{API}/{liq_id}/pagar", headers=h).status_code == 200
    monkeypatch.undo()
    borra = _leer(client, h, filas["Borra"][1])
    assert client.delete(f"{API}/{borra['id']}/pagos/{borra['pagos'][0]['id']}",
                         headers=h).status_code == 200

    # "Borra" también da $120.000: borrarle el pago ya no recorta `pagado` a cero.
    esperado = {filas["Sin Tocar"][1]: D("120000"), filas["Mas 50"][1]: D("120000"),
                filas["Mas 200"][1]: D("120000"), filas["Borra"][1]: D("120000")}
    t = client.post(f"{V}/transportadores", json={"nombre": "Alex", "valor_transporte": "100"},
                    headers=h).json()["id"]
    p = uuid.UUID(_proveedor(client, h, "A Mano"))
    a_mano = {
        # centavos: pagos 10.000,10 + 0,25 = 10.000,35 contra pagado −50.000,35
        # → 10.000,35 − (−50.000,35) = 60.000,70
        _escribir(db_session, emp, p, estado="parcial", version=2, valor_total="100000",
                  anticipos="150000.35", pagado="-50000.35", pagos=("10000.10", "0.25")):
            D("60000.70"),
        # del flete, migrada sin tocar
        _escribir(db_session, emp, uuid.UUID(t), tipo="transportador", estado="pagada",
                  valor_total="90000", anticipos="150000", pagado="-60000"): D("60000"),
        # pagada normal con su renglón, parcial normal, aprobada y borrador sin pagos
        _escribir(db_session, emp, p, estado="pagada", valor_total="300000",
                  pagado="300000", pagos=("300000",),
                  periodo=(date(2026, 8, 1), date(2026, 8, 15))): D("0"),
        _escribir(db_session, emp, p, estado="parcial", valor_total="300000",
                  pagado="100000", pagos=("60000", "40000"),
                  periodo=(date(2026, 8, 16), date(2026, 8, 31))): D("0"),
        _escribir(db_session, emp, p, estado="aprobada", valor_total="200000",
                  periodo=(date(2026, 9, 1), date(2026, 9, 15))): D("0"),
        _escribir(db_session, emp, p, estado="borrador", valor_total="200000",
                  anticipos="260000", periodo=(date(2026, 9, 16), date(2026, 9, 30))): D("0"),
        # la 'pagada' de antes con neto: pagado = neto, sin renglones
        _escribir(db_session, emp, p, estado="pagada", valor_total="400000",
                  anticipos="100000", pagado="300000",
                  periodo=(date(2026, 6, 1), date(2026, 6, 15))): D("0"),
    }
    db_session.commit()
    esperado |= {str(liq.id): cifra for liq, cifra in a_mano.items()}

    db_session.expire_all()
    en_sql = {str(i): D(v) for i, v in db_session.execute(
        select(Liquidacion.id, Liquidacion.deuda_borrada_por_la_migracion)
        .where(Liquidacion.empresa_id == emp)).all()}
    assert set(en_sql) >= set(esperado)
    for liq_id, cifra in esperado.items():
        orm = db_session.get(Liquidacion, uuid.UUID(liq_id)).deuda_borrada_por_la_migracion
        api = D(_leer(client, h, liq_id)["deuda_borrada_por_la_migracion"])
        print(f"\n  {liq_id[:8]}: esperado={cifra} sql={en_sql[liq_id]} orm={orm} api={api}")
        assert en_sql[liq_id] == orm == api == cifra, liq_id
    # Y sobre TODAS las filas de la empresa, no solo las de la tabla de arriba.
    for liq_id, cifra in en_sql.items():
        assert cifra == db_session.get(
            Liquidacion, uuid.UUID(liq_id)).deuda_borrada_por_la_migracion, liq_id


def test_las_tarjetas_no_prometen_plata_que_ningun_boton_entrega(
        client, base_datos, db_session):
    """La corregida antes del guardia (parcial v2, $230.000 − $300.000, pagado −$120.000)
    tiene saldo +$50.000, y el servidor rebota Pagar y abonar: la tarjeta "Parciales" los
    sumaba. Ahora sigue CONTANDO la fila (la lista la muestra al tocar la tarjeta) pero
    no suma su plata, y las filas por reparar van aparte.

      parciales:  la corregida (+50.000, no suma) y una normal 600.000 − 200.000 = 400.000
                  abonada 100.000 → faltan 300.000                  → 2, $300.000
      aprobadas:  una normal de 250.000 y la de "Pagar" antes del guardia (v2, saldo 0,
                  pagado −70.000 con un pago de 50.000)              → 2, $250.000
      por reparar: la corregida (120.000), la de Pagar (50.000 + 70.000 = 120.000) y la
                  migrada sin tocar (pagada, 120.000)                → 3, $360.000
      no cuentan: la anulada y la borrada, las dos con pagado −120.000.
    """
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    p = uuid.UUID(_proveedor(client, h, "Tarjetas"))
    corregida = _escribir(db_session, emp, p, estado="parcial", version=2,
                          valor_total="230000", anticipos="300000", pagado="-120000")
    _escribir(db_session, emp, p, estado="parcial", valor_total="600000",
              anticipos="200000", pagado="100000", pagos=("100000",),
              periodo=(date(2026, 7, 16), date(2026, 7, 31)))
    _escribir(db_session, emp, p, estado="aprobada", valor_total="250000",
              periodo=(date(2026, 8, 1), date(2026, 8, 15)))
    _escribir(db_session, emp, p, estado="aprobada", version=2, valor_total="230000",
              anticipos="300000", pagado="-70000", pagos=("50000",),
              periodo=(date(2026, 8, 16), date(2026, 8, 31)))
    _escribir(db_session, emp, p, estado="pagada", valor_total="180000",
              anticipos="300000", pagado="-120000",
              periodo=(date(2026, 9, 1), date(2026, 9, 15)))
    _escribir(db_session, emp, p, estado="anulada", valor_total="180000",
              anticipos="300000", pagado="-120000",
              periodo=(date(2026, 9, 16), date(2026, 9, 30)))
    _escribir(db_session, emp, p, estado="pagada", valor_total="180000",
              anticipos="300000", pagado="-120000", borrada=True,
              periodo=(date(2026, 10, 1), date(2026, 10, 15)))
    db_session.commit()
    assert corregida.saldo == D("50000")

    r = _como_numeros(_resumen(client, h))
    print(f"\n  resumen: {r}")
    assert (r["parciales"], r["saldo_parciales"]) == (2, D("300000"))
    assert (r["aprobadas"], r["saldo_aprobadas"]) == (2, D("250000"))
    assert (r["por_reparar"], r["deuda_borrada"]) == (3, D("360000"))
    assert r == _tarjetas_como_las_armaba_la_pantalla(client, h)
    # La plata que la tarjeta ya no promete es justo la que el servidor no entrega.
    pagar = client.post(f"{API}/{corregida.id}/pagar", headers=h)
    abono = client.post(f"{API}/{corregida.id}/pagos",
                        json={"fecha": "2026-08-10", "valor": "10000"}, headers=h)
    assert pagar.status_code == abono.status_code == 422
    # Con el filtro de fechas, las por reparar también se filtran.
    julio = _como_numeros(_resumen(client, h, desde="2026-07-01", hasta="2026-07-15"))
    assert (julio["por_reparar"], julio["deuda_borrada"]) == (1, D("120000"))
    assert julio == _tarjetas_como_las_armaba_la_pantalla(
        client, h, desde="2026-07-01", hasta="2026-07-15")


def test_el_listado_trae_los_pagos_de_toda_la_pagina_en_una_consulta(
        client, base_datos, db_session):
    """La cifra suma `pagos` en Python, así que cada fila de la lista los necesita. Con
    `selectin` salen en UNA consulta por página; con carga diferida serían una por fila
    (aquí seis) cada vez que el dueño abre el listado."""
    from sqlalchemy import event

    from tests.conftest import engine

    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    p = uuid.UUID(_proveedor(client, h, "Seis Quincenas"))
    for i in range(6):
        ini = date(2026, 1 + i, 1)
        _escribir(db_session, emp, p, estado="parcial", valor_total="300000",
                  pagado="100000", pagos=("60000", "40000"),
                  periodo=(ini, date(ini.year, ini.month, 15)))
    db_session.commit()
    db_session.expire_all()

    consultas = []

    def contar(conn, cursor, sentencia, *a):
        # Las que TRAEN pagos. La de los soportes también nombra la tabla (la cruza
        # para llegar a sus fotos), pero esa es de `adjuntos` y va aparte.
        if sentencia.lstrip().startswith("SELECT pagos_liquidacion."):
            consultas.append(sentencia)

    event.listen(engine, "before_cursor_execute", contar)
    try:
        r = client.get(API, params={"page_size": 200}, headers=h)
    finally:
        event.remove(engine, "before_cursor_execute", contar)
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 6
    assert all(D(x["deuda_borrada_por_la_migracion"]) == 0 for x in r.json()["items"])
    print(f"\n  consultas a pagos_liquidacion: {len(consultas)}")
    assert len(consultas) == 1
