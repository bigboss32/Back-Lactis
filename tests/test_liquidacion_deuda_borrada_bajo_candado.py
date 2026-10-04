"""LA MARCA «DEUDA BORRADA» SE LEE CON EL CANDADO PUESTO: UN ABONO AJENO NO LA INVENTA.

La deuda borrada es Σ(pagos) − pagado, y las dos mitades salen de dos SELECT distintos: la
fila de la liquidación y su colección de pagos (selectin). En Postgres cada sentencia ve
su propia foto. Si otro usuario confirma un abono ENTRE las dos, la resta da el valor de
ese abono y una quincena normal rebota con el aviso de la migración.

El caso, con cifras redondas: quincena de agosto de 125 L × $2.000 = $250.000 en
'parcial', pagado $100.000 con pagos de $60.000 y $40.000. El usuario 1 oprime Pagar; en
el medio, el usuario 2 confirma un abono de $50.000. La petición 1 veía $150.000 en pagos
contra $100.000 de pagado y decía "le borró lo que el tercero quedaba debiendo
($50.000)". Esa quincena nunca pasó por la migración.

SQLite tiene una sola conexión, así que la carrera se arma a mano: el abono del usuario 2
se escribe por debajo del ORM —la fila que ya está en memoria se queda con su `pagado`
viejo— y solo se vuelve a leer la colección de pagos. Es exactamente la foto mezclada que
deja la carrera. Pagar, Anular y el PUT de observaciones toman ahora el candado
(`_bloquear`, con `populate_existing`) ANTES de preguntar, y leen las dos mitades frescas.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import insert, update

from app.modules.liquidaciones.models import Liquidacion, PagoLiquidacion
from tests.conftest import auth_headers

V = "/api/v1"
API = f"{V}/liquidaciones"
BORRADA = "antes de que existieran los abonos"


def D(v):
    return Decimal(str(v))


def _detalle(r):
    return r.json()["error"]["detail"]


def _agosto(client, h, nombre, abonos=()):
    """125 L × $2.000 = $250.000, aprobada, con los abonos que se pidan."""
    r = client.post(f"{V}/proveedores", json={"nombre": nombre, "vereda": "X",
                                              "precio_litro": "2000"}, headers=h)
    prov = r.json()["id"]
    r = client.post(f"{V}/recepciones", json={"fecha": "2026-08-04", "proveedor_id": prov,
                                              "cantidad_litros": "125"}, headers=h)
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/generar", json={"periodo_inicio": "2026-08-01",
                                            "periodo_fin": "2026-08-15",
                                            "tipo": "proveedor"}, headers=h)
    liq = next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    for valor in abonos:
        r = client.post(f"{API}/{liq['id']}/pagos",
                        json={"fecha": "2026-08-16", "valor": valor}, headers=h)
        assert r.status_code == 200, r.text
    return liq["id"]


def _abono_ajeno_en_el_medio(db_session, liq_id, valor):
    """El abono que confirma OTRO usuario entre el SELECT de la fila y el de sus pagos.

    Devuelve la fila en memoria y la prueba la tiene que GUARDAR mientras hace la
    petición: el mapa de identidad de la sesión la sostiene con una referencia débil, y si
    nadie más la tiene se recoge y la petición la lee entera desde la base, sin foto
    mezclada que medir."""
    fila = db_session.get(Liquidacion, uuid.UUID(liq_id))
    pagado = Decimal(fila.pagado) + D(valor)
    db_session.execute(insert(PagoLiquidacion).values(
        id=uuid.uuid4(), liquidacion_id=fila.id, fecha=date(2026, 8, 16), valor=D(valor)))
    db_session.execute(
        update(Liquidacion).where(Liquidacion.id == fila.id)
        .values(pagado=pagado, saldo=fila.neto_a_pagar - pagado, estado="parcial")
        .execution_options(synchronize_session=False))
    db_session.commit()
    # La fila en memoria se queda con el `pagado` de antes del abono; los pagos se
    # vuelven a leer y ya lo traen. Esa es la foto mezclada.
    db_session.expire(fila, ["pagos"])
    assert fila.deuda_borrada_por_la_migracion == D(valor), "la foto no quedó mezclada"
    # Y se deja la colección otra vez sin leer: es la petición la que hace el segundo
    # SELECT, como en la carrera.
    db_session.expire(fila, ["pagos"])
    return fila


def test_pagar_no_ve_deuda_borrada_por_un_abono_ajeno(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    liq = _agosto(client, h, "Agosto Pagar", abonos=("60000", "40000"))
    en_memoria = _abono_ajeno_en_el_medio(db_session, liq, "50000")
    r = client.post(f"{API}/{liq}/pagar", headers=h)
    del en_memoria
    print(f"\n  pagar -> {r.status_code}: {r.text[:200]}")
    assert r.status_code == 200, r.text
    pagada = r.json()
    assert pagada["estado"] == "pagada" and D(pagada["saldo"]) == 0
    # Pagó lo que de verdad faltaba: 250.000 − (60.000 + 40.000 + 50.000) = $100.000.
    assert sorted(D(p["valor"]) for p in pagada["pagos"]) == [
        D("40000"), D("50000"), D("60000"), D("100000")]
    assert D(pagada["pagado"]) == D("250000")


def test_anular_dice_la_razon_de_verdad_y_no_la_migracion(client, base_datos, db_session):
    """Aprobada sin pagos; otro usuario le registra un abono de $50.000 en el medio. Lo
    cierto es que ya tiene un pago, no que la migración le borró una deuda."""
    h = auth_headers(client, "admin.a")
    liq = _agosto(client, h, "Agosto Anular")
    en_memoria = _abono_ajeno_en_el_medio(db_session, liq, "50000")
    r = client.post(f"{API}/{liq}/anular", headers=h)
    del en_memoria
    print(f"\n  anular -> {r.status_code}: {_detalle(r)}")
    assert r.status_code == 422
    assert BORRADA not in _detalle(r)
    assert "No se puede anular una liquidación con pagos registrados" in _detalle(r)


def test_las_observaciones_dicen_la_razon_de_verdad(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    liq = _agosto(client, h, "Agosto Notas")
    en_memoria = _abono_ajeno_en_el_medio(db_session, liq, "50000")
    r = client.put(f"{API}/{liq}", json={"observaciones": "nota"}, headers=h)
    del en_memoria
    print(f"\n  PUT observaciones -> {r.status_code}: {_detalle(r)}")
    assert r.status_code == 422
    assert BORRADA not in _detalle(r)
    assert _detalle(r).startswith("Esta quincena ya tiene pagos registrados")
