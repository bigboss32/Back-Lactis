"""LA QUINCENA DE JULIO A LA QUE LA MIGRACIÓN DE LOS ABONOS LE BORRÓ LA DEUDA.

La migración a5e7c1b4d9f2 (01/08/2026, ya corrida en producción) les escribió a las
'pagada' de antes pagado = valor_total − anticipos y saldo = 0. Con $180.000 de leche
(90 L a $2.000) contra $300.000 de adelanto eso dejó pagado = −$120.000 y saldo en cero:
el tercero debe $120.000 y la fila no lo dice por ningún lado.

Sobre esa fila "Corregir esta quincena" calcula saldo = neto − pagado con el pagado
negativo, así que la deuda se SUMA: con un día olvidado de $50.000 la verdad es que
sigue debiendo $70.000, y el sistema decía "hay que oprimir Pagar" por $50.000. Pagados,
la fila volvía a 'aprobada' y de la caja salían $50.000 hacia quien debe.

Aquí se mide el guardia: corregir, previsualizar, pagar y abonar rebotan en esa fila
con un mensaje que dice cuánto se borró, y las quincenas sanas siguen igual. No se
repara ningún dato: eso es otro trabajo.
"""
import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers

V = "/api/v1"
API = f"{V}/liquidaciones"
MIGRACION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "a5e7c1b4d9f2_pagos_parciales_de_liquidaciones.py"
)


def D(v):
    return Decimal(str(v))


def _correr_la_migracion(db):
    """El `upgrade()` REAL de a5e7c1b4d9f2. Las columnas ya existen (create_all), así que
    el `op` de mentiras solo ejecuta su UPDATE de datos."""
    spec = importlib.util.spec_from_file_location("a5e7_deuda_borrada", MIGRACION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    class Op:
        def __getattr__(self, nombre):
            if nombre == "execute":
                return lambda sentencia: db.execute(text(sentencia))
            if nombre == "f":
                return lambda n: n
            return lambda *a, **k: None

    modulo.op = Op()
    modulo.upgrade()
    db.commit()
    db.expire_all()


def _proveedor(client, h, nombre):
    r = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "La Loma", "precio_litro": "2000"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _dia(client, h, prov_id, fecha, litros):
    r = client.post(f"{V}/recepciones", json={
        "fecha": fecha, "proveedor_id": prov_id, "cantidad_litros": litros}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _julio_aprobada(client, h, nombre, litros, adelanto):
    prov = _proveedor(client, h, nombre)
    _dia(client, h, prov, "2026-07-04", litros)
    r = client.post(f"{V}/anticipos", json={"tipo": "proveedor", "proveedor_id": prov,
                                            "fecha": "2026-07-06", "valor": adelanto},
                    headers=h)
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/generar", json={"periodo_inicio": "2026-07-01",
                                            "periodo_fin": "2026-07-15", "tipo": "proveedor"},
                    headers=h)
    assert r.status_code == 200, r.text
    liq = next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)
    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    return prov, r.json()


def _migrada(client, h, db, nombre="Julio Migrada"):
    """90 L × $2.000 = $180.000 contra $300.000: 'pagada' con el botón de antes (solo
    cambiaba el estado) y después la migración."""
    prov, liq = _julio_aprobada(client, h, nombre, "90", "300000")
    fila = db.get(Liquidacion, uuid.UUID(liq["id"]))
    fila.estado = "pagada"
    db.commit()
    _correr_la_migracion(db)
    leida = _leer(client, h, liq["id"])
    assert D(leida["pagado"]) == D("-120000") and D(leida["saldo"]) == 0
    assert D(leida["neto_a_pagar"]) == D(leida["pagado"]) + D(leida["saldo"])
    return prov, leida


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _detalle(r):
    return r.json()["error"]["detail"]


def _foto(liq):
    plata = {k: D(liq[k]) for k in ("valor_total", "anticipos", "pagado", "saldo")}
    return plata | {"estado": liq["estado"], "version": liq["version"],
                    "pagos": len(liq["pagos"])}


# ---------------------------------------------------------------------------------------
def test_la_migrada_dice_cuanto_le_borraron(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, migrada = _migrada(client, h, db_session)
    assert D(migrada["deuda_borrada_por_la_migracion"]) == D("120000")


def test_corregir_la_migrada_rebota_y_no_mueve_nada(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, migrada = _migrada(client, h, db_session)
    olvidado = _dia(client, h, prov, "2026-07-08", "25")          # $50.000
    cuerpo = {"motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}

    prev = client.post(f"{API}/{migrada['id']}/corregir/previsualizar", json=cuerpo,
                       headers=h)
    print(f"\n  previsualizar -> {prev.status_code} {_detalle(prev)!r}")
    assert prev.status_code == 422, prev.text
    assert "antes de que existieran los abonos" in _detalle(prev)
    assert "($120.000)" in _detalle(prev)
    assert "repararla" in _detalle(prev)

    hecho = client.post(f"{API}/{migrada['id']}/corregir", json=cuerpo, headers=h)
    assert hecho.status_code == 422, hecho.text
    assert _detalle(hecho) == _detalle(prev)

    despues = _leer(client, h, migrada["id"])
    assert _foto(despues) == _foto(migrada)
    dia = client.get(f"{V}/recepciones/{olvidado}", headers=h).json()
    assert dia["liquidacion_id"] is None, "el día olvidado sigue suelto"


def test_pagar_y_abonar_rebotan_sobre_la_que_ya_se_corrigio_con_el_error(
        client, base_datos, db_session):
    """La forma que dejó en producción una corrección hecha ANTES de este guardia:
    parcial, v2, $230.000 − $300.000 = neto −$70.000, pagado −$120.000, saldo +$50.000.
    Pagar esos $50.000 es entregarle plata a quien debe $70.000."""
    h = auth_headers(client, "admin.a")
    _, migrada = _migrada(client, h, db_session, "Corregida Antes")
    fila = db_session.get(Liquidacion, uuid.UUID(migrada["id"]))
    fila.estado, fila.version = "parcial", 2
    fila.valor_bruto = fila.valor_total = D("230000")
    fila.saldo = D("50000")
    db_session.commit()
    antes = _leer(client, h, migrada["id"])
    assert D(antes["neto_a_pagar"]) == D(antes["pagado"]) + D(antes["saldo"])  # −70.000

    pagar = client.post(f"{API}/{migrada['id']}/pagar", headers=h)
    abono = client.post(f"{API}/{migrada['id']}/pagos",
                        json={"fecha": "2026-08-10", "valor": "10000"}, headers=h)
    print(f"\n  pagar -> {pagar.status_code} {_detalle(pagar)!r}")
    print(f"  abonar -> {abono.status_code} {_detalle(abono)!r}")
    assert pagar.status_code == 422, pagar.text
    assert abono.status_code == 422, abono.text
    assert "($120.000)" in _detalle(pagar) and "($120.000)" in _detalle(abono)
    assert _foto(_leer(client, h, migrada["id"])) == _foto(antes)


def test_las_pagadas_sanas_se_siguen_corrigiendo(client, base_datos, db_session):
    """Controles: la pagada que el botón de ANTES dejó con saldo −$120.000 y pagado $0
    (esa deuda sí se ve) y una pagada normal. Las dos sin deuda borrada, y las dos se
    corrigen como siempre."""
    h = auth_headers(client, "admin.a")
    prov_v, ventana = _julio_aprobada(client, h, "Ventana Agosto", "90", "300000")
    fila = db_session.get(Liquidacion, uuid.UUID(ventana["id"]))
    fila.estado = "pagada"
    db_session.commit()
    prov_n, normal = _julio_aprobada(client, h, "Pagada Normal", "200", "100000")
    assert client.post(f"{API}/{normal['id']}/pagar", headers=h).status_code == 200

    for prov, liq, saldo_despues in ((prov_v, ventana, "-70000"), (prov_n, normal, "50000")):
        assert D(_leer(client, h, liq["id"])["deuda_borrada_por_la_migracion"]) == 0
        olvidado = _dia(client, h, prov, "2026-07-08", "25")      # $50.000
        cuerpo = {"motivo": "día olvidado", "recepciones_a_incluir": [olvidado]}
        prev = client.post(f"{API}/{liq['id']}/corregir/previsualizar", json=cuerpo,
                           headers=h)
        assert prev.status_code == 200, prev.text
        assert D(prev.json()["saldo_despues"]) == D(saldo_despues)
        hecho = client.post(f"{API}/{liq['id']}/corregir", json=cuerpo, headers=h)
        assert hecho.status_code == 200, hecho.text
        assert D(hecho.json()["saldo"]) == D(saldo_despues)
