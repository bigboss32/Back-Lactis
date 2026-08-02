"""El flete del despacho partido en VARIOS TRAMOS, con el nombre del conductor.

Lo pidió el dueño así: "necesito que el flete pueda ser varios; ejemplo puede
ser de la quesera a San Vicente 400 y de San Vicente a Bogotá 600, y el nombre
del conductor, porque necesito saber cuánto se le tiene que pagar".

Hay un cliente real usando esto con dinero de verdad, así que lo que estas
pruebas cuidan no es que el código corra sino que NINGUNA CIFRA SE MUEVA:

 - el flete de un despacho con dos tramos tiene que dar lo mismo que el flete de
   un solo valor por la misma plata, y la utilidad del lote no se puede enterar;
 - las ventas que YA tenían flete tienen que quedar migradas peso por peso;
 - lo que se le debe a un conductor tiene que bajar cuando se le paga, y no se
   le puede pagar de más;
 - y nada de esto puede cruzarse entre empresas.

Las cifras se imprimen porque el dueño las cuadra a mano.
"""
import importlib.util
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models_registry  # noqa: F401  (registra todas las tablas en Base)
from app.core.database import Base
from app.modules.clientes.models import Cliente
from app.modules.empresas.models import Empresa
from app.modules.ventas.models import Venta, VentaTramoFlete
from tests.conftest import PASSWORD, auth_headers
from tests.test_lotes_produccion import (
    cliente_nuevo,
    montar_leche,
    panel,
    producir,
    producto_de,
    recibir,
    tipo_queso,
)

API = "/api/v1/ventas"
CONDUCTORES = f"{API}/conductores"


def D(valor):
    return Decimal(str(valor))


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------
def _preparar_bodega(client, h, kilos="500"):
    """Un queso con existencias y un cliente, sin pasar por producción."""
    producto = client.post(
        "/api/v1/inventario/productos",
        json={"nombre": "Queso Doble Crema", "categoria": "producto_terminado",
              "unidad": "kg", "stock_minimo": "5"},
        headers=h,
    ).json()
    client.post(
        "/api/v1/inventario/movimientos",
        json={"producto_id": producto["id"], "fecha": "2026-06-01", "tipo": "entrada",
              "cantidad": kilos, "costo_unitario": "12000"},
        headers=h,
    )
    cliente = client.post(
        "/api/v1/clientes", json={"nombre": "Tienda La 33"}, headers=h
    ).json()
    return producto, cliente


def _vender(client, h, producto, cliente, *, kilos="100", precio="20000",
            tramos=None, fecha="2026-06-10", esperado=201):
    cuerpo = {
        "cliente_id": cliente["id"], "fecha": fecha,
        "detalles": [{"producto_id": producto["id"], "cantidad": kilos,
                      "precio_unitario": precio}],
    }
    if tramos is not None:
        cuerpo["tramos"] = tramos
    r = client.post(API, json=cuerpo, headers=h)
    assert r.status_code == esperado, r.text
    return r.json()


def _panel_conductores(client, h, **params):
    r = client.get(CONDUCTORES, params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _fila(panel_json, nombre):
    for fila in panel_json["conductores"]:
        if fila["conductor"] == nombre:
            return fila
    return None


# ---------------------------------------------------------------------------
# (a) DOS TRAMOS SUMAN EL FLETE, Y LA UTILIDAD NO SE ENTERA
# ---------------------------------------------------------------------------
def test_dos_tramos_suman_exacto_el_flete_del_despacho(client, base_datos):
    """El ejemplo del dueño, tal cual: de la quesera a San Vicente 400 y de San
    Vicente a Bogotá 600, sobre 100 kg.

    Lo que se comprueba es el CUADRE: cada tramo vale lo suyo, los dos suman el
    flete del despacho, y el "por kilo" del despacho es la suma de los dos
    (1.000/kg). Si el desglose no sumara la cifra grande, el dueño lo encuentra
    a la primera.
    """
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)

    venta = _vender(
        client, h, producto, cliente, kilos="100", precio="20000",
        tramos=[
            {"origen": "Quesera", "destino": "San Vicente",
             "conductor": "Jose Lavado", "valor_por_kilo": "400"},
            {"origen": "San Vicente", "destino": "Bogotá",
             "conductor": "Marta Ruiz", "valor_por_kilo": "600"},
        ],
    )

    print("\n===== (a) EL FLETE POR TRAMOS =====")
    print(f"  despacho de 100 kg, total al cliente {D(venta['total']):,.0f}")
    for t in venta["tramos_flete"]:
        print(f"    {t['origen']} → {t['destino']:<12} {t['conductor']:<12} "
              f"{D(t['valor_por_kilo']):>6,.0f}/kg = {D(t['valor_total']):>10,.0f}")
    suma = sum(D(t["valor_total"]) for t in venta["tramos_flete"])
    print(f"    {'suma de los tramos':<40} {suma:>10,.0f}")
    print(f"    {'flete del despacho (gasto_monto)':<40} "
          f"{D(venta['gasto_monto']):>10,.0f}")

    assert len(venta["tramos_flete"]) == 2
    assert D(venta["tramos_flete"][0]["valor_total"]) == 40_000
    assert D(venta["tramos_flete"][1]["valor_total"]) == 60_000
    # EL DESGLOSE SUMA EXACTO LA CIFRA GRANDE
    assert suma == D(venta["gasto_monto"]) == 100_000
    # El "por kilo" del despacho es la suma de los tramos: 400 + 600
    assert D(venta["gasto_por_kilo"]) == 1_000
    # Y la ruta queda legible de corrido, sin repetir San Vicente
    assert venta["gasto_concepto"] == "Quesera → San Vicente → Bogotá"
    # El flete NO se le cobra al cliente
    assert D(venta["total"]) == 2_000_000


def _cadena_de_produccion(client, h):
    """Leche → producción de 100 kg → el producto terminado y un cliente."""
    transportador, prov = montar_leche(client, h)
    tipo = tipo_queso(client, h, "Queso campesino")
    recibir(client, h, "2026-07-01", prov["Libardo"], 1000, transportador)
    producir(client, h, "2026-07-01", tipo, litros=1000, kilos=100)
    return producto_de(client, h, tipo), cliente_nuevo(client, h)


def test_la_utilidad_del_lote_no_cambia_con_tramos(client, base_datos):
    """Dos tramos de 400 y 600 tienen que dejar la utilidad EXACTAMENTE igual que
    un flete único de 1.000, hasta el último peso.

    Es la prueba que de verdad importa: la utilidad por lote, el kilo puesto en
    destino y el estado de resultados leen `gasto_monto`, y si al partir el flete
    esa cifra se moviera aunque fuera un peso, la utilidad mentiría. Se monta la
    misma cadena de leche y producción en las DOS empresas y se comparan las dos
    pantallas.
    """
    h_uno = auth_headers(client, "admin.a")   # flete de siempre: 1.000/kg
    h_dos = auth_headers(client, "admin.b")   # el mismo flete en dos tramos

    producto_a, cliente_a = _cadena_de_produccion(client, h_uno)
    producto_b, cliente_b = _cadena_de_produccion(client, h_dos)

    _vender(client, h_uno, producto_a, cliente_a, kilos="100", precio="25000",
            fecha="2026-07-20",
            tramos=[{"destino": "Bogotá", "valor_por_kilo": "1000"}])
    _vender(client, h_dos, producto_b, cliente_b, kilos="100", precio="25000",
            fecha="2026-07-20",
            tramos=[
                {"origen": "Quesera", "destino": "San Vicente",
                 "conductor": "Jose Lavado", "valor_por_kilo": "400"},
                {"origen": "San Vicente", "destino": "Bogotá",
                 "conductor": "Marta Ruiz", "valor_por_kilo": "600"},
            ])

    lote_uno = panel(client, h_uno)["lotes"][0]
    lote_dos = panel(client, h_dos)["lotes"][0]

    print("\n===== (a2) LA UTILIDAD NO SE ENTERA =====")
    print(f"  {'':<24}{'flete único':>16}{'dos tramos':>16}")
    for etiqueta, campo in [
        ("flete del lote", "gastos"),
        ("costo puesto por kilo", "costo_puesto_kilo"),
        ("costo del vendido", "costo_vendido"),
        ("ingresos", "ingresos"),
        ("UTILIDAD", "utilidad"),
    ]:
        print(f"  {etiqueta:<24}{D(lote_uno[campo]):>16,.2f}{D(lote_dos[campo]):>16,.2f}")

    for campo in ("gastos", "costo_puesto_kilo", "costo_vendido", "ingresos", "utilidad"):
        assert D(lote_uno[campo]) == D(lote_dos[campo]), (
            f"partir el flete movió '{campo}': {lote_uno[campo]} vs {lote_dos[campo]}"
        )
    assert D(lote_dos["gastos"]) == 100_000

    # Y el estado de resultados, que es lo que el dueño mira al cerrar el mes
    def resultados(headers):
        r = client.get(
            "/api/v1/contabilidad/estado-resultados",
            params={"desde": "2026-07-01", "hasta": "2026-07-31"}, headers=headers,
        )
        assert r.status_code == 200, r.text
        return r.json()

    uno, dos = resultados(h_uno), resultados(h_dos)
    print(f"  transporte de despachos  {D(uno['transporte_despachos']):>16,.2f}"
          f"{D(dos['transporte_despachos']):>16,.2f}")
    print(f"  utilidad bruta           {D(uno['utilidad_bruta']):>16,.2f}"
          f"{D(dos['utilidad_bruta']):>16,.2f}")
    assert D(uno["transporte_despachos"]) == D(dos["transporte_despachos"]) == 100_000
    assert D(uno["utilidad_bruta"]) == D(dos["utilidad_bruta"])


def test_cambiar_los_kilos_reparte_el_flete_de_nuevo(client, base_datos):
    """Si se editan los renglones, cada tramo tiene que recalcular su total.

    Sin esto quedaría el monto viejo y el kilo puesto en destino saldría mal —
    era el mismo defecto que ya se había corregido con el flete de un solo valor.
    """
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    venta = _vender(
        client, h, producto, cliente, kilos="100",
        tramos=[{"destino": "San Vicente", "valor_por_kilo": "400"},
                {"origen": "San Vicente", "destino": "Bogotá", "valor_por_kilo": "600"}],
    )
    print("\n===== (a3) SE CAMBIAN LOS KILOS =====")
    print(f"  100 kg → flete {D(venta['gasto_monto']):,.0f}")

    r = client.put(
        f"{API}/{venta['id']}",
        json={"detalles": [{"producto_id": producto["id"], "cantidad": "60",
                            "precio_unitario": "20000"}]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    despues = r.json()
    print(f"   60 kg → flete {D(despues['gasto_monto']):,.0f}")
    for t in despues["tramos_flete"]:
        print(f"    {t['destino']:<12} {D(t['valor_por_kilo']):>6,.0f}/kg = "
              f"{D(t['valor_total']):>9,.0f}")

    assert D(despues["tramos_flete"][0]["valor_total"]) == 24_000   # 60 x 400
    assert D(despues["tramos_flete"][1]["valor_total"]) == 36_000   # 60 x 600
    assert D(despues["gasto_monto"]) == 60_000
    assert sum(D(t["valor_total"]) for t in despues["tramos_flete"]) == D(despues["gasto_monto"])


def test_el_flete_de_un_solo_valor_sigue_funcionando(client, base_datos):
    """La forma vieja de mandar el flete tiene que seguir sirviendo: es la que
    usa el formulario que está desplegado hoy, y queda guardada como UN tramo."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    r = client.post(
        API,
        json={"cliente_id": cliente["id"], "fecha": "2026-06-10",
              "gasto_concepto": "Transporte a Bogotá", "gasto_por_kilo": "1200",
              "detalles": [{"producto_id": producto["id"], "cantidad": "100",
                            "precio_unitario": "20000"}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    venta = r.json()
    print("\n===== (a4) EL FLETE DE UN SOLO VALOR =====")
    print(f"  gasto_concepto {venta['gasto_concepto']!r} · "
          f"{D(venta['gasto_por_kilo']):,.0f}/kg = {D(venta['gasto_monto']):,.0f}")
    print(f"  quedó como {len(venta['tramos_flete'])} tramo(s)")
    assert D(venta["gasto_monto"]) == 120_000
    assert venta["gasto_concepto"] == "Transporte a Bogotá"
    assert len(venta["tramos_flete"]) == 1
    assert D(venta["tramos_flete"][0]["valor_total"]) == 120_000
    assert venta["tramos_flete"][0]["conductor"] is None


def test_no_se_aplastan_varios_tramos_con_un_valor_suelto(client, base_datos):
    """Mandar el flete a la vieja usanza sobre un despacho que ya tiene VARIOS
    tramos borraría en silencio a los conductores y con ellos lo que se les debe.
    Se rechaza y se dice qué hacer."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    venta = _vender(
        client, h, producto, cliente,
        tramos=[{"destino": "San Vicente", "conductor": "Jose Lavado",
                 "valor_por_kilo": "400"},
                {"origen": "San Vicente", "destino": "Bogotá",
                 "conductor": "Marta Ruiz", "valor_por_kilo": "600"}],
    )
    r = client.put(f"{API}/{venta['id']}", json={"gasto_por_kilo": "1000"}, headers=h)
    print("\n===== (a5) NO SE APLASTAN LOS TRAMOS =====")
    print(f"  PUT con un valor suelto: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "tramos" in r.json()["error"]["detail"]

    sigue = client.get(f"{API}/{venta['id']}", headers=h).json()
    print(f"  el despacho sigue con {len(sigue['tramos_flete'])} tramos y flete "
          f"{D(sigue['gasto_monto']):,.0f}")
    assert len(sigue["tramos_flete"]) == 2
    assert D(sigue["gasto_monto"]) == 100_000


# ---------------------------------------------------------------------------
# (b) LA MIGRACIÓN DE LOS FLETES QUE YA EXISTÍAN
# ---------------------------------------------------------------------------
def _cargar_migracion():
    """Carga el módulo de la migración por ruta.

    Se prueba LA MIGRACIÓN DE VERDAD, la misma función que corre `alembic
    upgrade`, y no una copia de su lógica: una copia se puede quedar atrás y
    entonces la prueba certificaría algo que ya no es lo que se ejecuta.
    """
    ruta = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "b3d9f1e5c7a2_flete_por_tramos_y_conductores.py"
    )
    spec = importlib.util.spec_from_file_location("migracion_tramos", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# (concepto, gasto_por_kilo, gasto_monto) tal como pueden estar HOY en la base.
FLETES_VIEJOS = [
    ("Transporte a Bogotá", "1200", "120000"),
    # Sin flete: no debe crear ningún tramo.
    (None, "0", "0"),
    # El caso que obliga a COPIAR el monto en vez de recalcularlo: 333,33/kg por
    # 100,01 kg daría 33.336,63, pero lo guardado son 33.333,33. Recalcular
    # movería la cifra de una venta ya cerrada.
    ("Flete a Villavicencio", "333.33", "33333.33"),
    # Fila rara pero posible: monto sin valor por kilo. Igual tiene que quedar
    # representada, o su desglose sumaría cero contra una cifra que no lo es.
    ("Flete de una vez", "0", "5000"),
    # Un concepto largísimo: la columna nueva admite 120 y la vieja 150.
    ("T" * 150, "500", "50000"),
]


@pytest.fixture()
def base_vieja():
    """Una base con ventas que YA tienen flete y todavía no tienen tramos.

    Es el estado exacto de la base del cliente el minuto antes de migrar.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    sesion = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    empresa = Empresa(nombre="Quesera Vieja", nit="900V")
    sesion.add(empresa)
    sesion.flush()
    cliente = Cliente(nombre="Tienda La 33", empresa_id=empresa.id)
    sesion.add(cliente)
    sesion.flush()

    creadas = []
    for numero, (concepto, por_kilo, monto) in enumerate(FLETES_VIEJOS, start=1):
        venta = Venta(
            empresa_id=empresa.id, numero=numero, cliente_id=cliente.id,
            fecha=date(2026, 6, 1), subtotal=D("2000000"), descuento=D("0"),
            total=D("2000000"), pagado=D("0"),
            gasto_concepto=concepto, gasto_por_kilo=D(por_kilo), gasto_monto=D(monto),
        )
        sesion.add(venta)
        creadas.append(venta)
    # Una anulada y una borrada en suave: también tienen que quedar migradas, o
    # el día que alguien las consulte su desglose saldría vacío.
    creadas[0].estado = "anulada"
    sesion.flush()
    sesion.commit()
    try:
        yield {"engine": engine, "sesion": sesion, "ventas": creadas}
    finally:
        sesion.close()
        Base.metadata.drop_all(bind=engine)


def test_la_migracion_no_mueve_ni_un_peso(base_vieja):
    """Cada venta que ya tenía flete queda con un tramo que vale EXACTAMENTE lo
    mismo, y el `gasto_monto` de la venta no se toca.

    Es lo más delicado de todo el encargo: si la migración recalculara el monto
    en vez de copiarlo, la utilidad de ventas ya cerradas cambiaría sola.
    """
    engine = base_vieja["engine"]
    sesion = base_vieja["sesion"]
    migracion = _cargar_migracion()

    antes = {
        v.numero: (v.gasto_concepto, D(v.gasto_por_kilo), D(v.gasto_monto))
        for v in sesion.scalars(select(Venta)).all()
    }
    assert sesion.scalar(select(VentaTramoFlete)) is None, "la base ya tenía tramos"

    with engine.begin() as conn:
        creados = migracion.backfill_tramos_de_flete(conn)

    sesion.expire_all()
    print("\n===== (b) LA MIGRACIÓN DE LOS FLETES VIEJOS =====")
    print(f"  tramos creados: {creados}")
    print(f"  {'venta':<7}{'antes':>14}{'suma de tramos':>18}{'ahora':>14}  ruta")

    con_flete = 0
    for venta in sesion.scalars(select(Venta).order_by(Venta.numero)).all():
        concepto_antes, por_kilo_antes, monto_antes = antes[venta.numero]
        tramos = sesion.scalars(
            select(VentaTramoFlete).where(VentaTramoFlete.venta_id == venta.id)
        ).all()
        suma = sum((D(t.valor_total) for t in tramos), D(0))
        ruta = tramos[0].destino if tramos else "—"
        print(f"  #{venta.numero:<6}{monto_antes:>14,.2f}{suma:>18,.2f}"
              f"{D(venta.gasto_monto):>14,.2f}  {str(ruta)[:32]}")

        # 1. NINGUNA cifra de la venta se movió
        assert D(venta.gasto_monto) == monto_antes
        assert D(venta.gasto_por_kilo) == por_kilo_antes
        assert venta.gasto_concepto == concepto_antes
        # 2. El desglose SUMA EXACTO lo que ya valía el flete
        assert suma == monto_antes, (
            f"venta #{venta.numero}: los tramos suman {suma} y el flete era {monto_antes}"
        )
        if monto_antes or por_kilo_antes:
            con_flete += 1
            assert len(tramos) == 1
            tramo = tramos[0]
            # El monto se COPIÓ, no se recalculó
            assert D(tramo.valor_total) == monto_antes
            assert D(tramo.valor_por_kilo) == por_kilo_antes
            # Sin conductor: no hay a quién atribuirle esos viajes viejos
            assert tramo.conductor is None and tramo.conductor_clave is None
            assert tramo.orden == 1 and tramo.origen is None
            if concepto_antes:
                assert tramo.destino == concepto_antes[:120]
        else:
            assert tramos == [], "una venta sin flete no puede quedar con tramos"

    assert creados == con_flete == 4
    # La venta anulada también quedó migrada
    anulada = sesion.scalars(select(Venta).where(Venta.estado == "anulada")).one()
    assert sesion.scalars(
        select(VentaTramoFlete).where(VentaTramoFlete.venta_id == anulada.id)
    ).all(), "la venta anulada se quedó sin desglose"


def test_la_migracion_es_idempotente_en_una_base_sin_fletes(base_vieja):
    """Sobre una base donde ninguna venta tiene flete no hay nada que hacer y no
    puede reventar: `upgrade()` corre sobre tablas que ya tienen filas."""
    engine = base_vieja["engine"]
    sesion = base_vieja["sesion"]
    for venta in sesion.scalars(select(Venta)).all():
        venta.gasto_por_kilo = D("0")
        venta.gasto_monto = D("0")
    sesion.commit()

    migracion = _cargar_migracion()
    with engine.begin() as conn:
        creados = migracion.backfill_tramos_de_flete(conn)
    print("\n===== (b2) BASE SIN FLETES =====")
    print(f"  tramos creados: {creados} (no había nada que migrar)")
    assert creados == 0


# ---------------------------------------------------------------------------
# (c) EL NOMBRE DEL CONDUCTOR SE CANONIZA
# ---------------------------------------------------------------------------
def test_el_conductor_se_canoniza_y_no_se_parte_en_dos(client, base_datos):
    """El dueño escribe "Jose Lavado" hoy y "  JOSE   LAVADO " mañana. Tiene que
    ser el mismo señor: si se partiera, su deuda saldría en dos filas y él le
    pagaría dos veces la mitad sin darse cuenta.
    """
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)

    _vender(client, h, producto, cliente, kilos="100", fecha="2026-06-10",
            tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                     "valor_por_kilo": "400"}])
    segunda = _vender(client, h, producto, cliente, kilos="50", fecha="2026-06-15",
                      tramos=[{"destino": "Bogotá", "conductor": "  JOSE   LAVADO ",
                               "valor_por_kilo": "400"}])

    print("\n===== (c) LA CANONIZACIÓN DEL CONDUCTOR =====")
    print(f"  se escribió '  JOSE   LAVADO ' y quedó "
          f"{segunda['tramos_flete'][0]['conductor']!r}")
    assert segunda["tramos_flete"][0]["conductor"] == "Jose Lavado"

    p = _panel_conductores(client, h)
    nombres = [c["conductor"] for c in p["conductores"]]
    print(f"  conductores en la pantalla: {nombres}")
    assert nombres == ["Jose Lavado"], "el mismo señor salió partido en dos"

    fila = _fila(p, "Jose Lavado")
    print(f"  100 kg × 400 = {D(fila['tramos'][0]['valor']):,.0f}")
    print(f"   50 kg × 400 = {D(fila['tramos'][1]['valor']):,.0f}")
    print(f"  se le debe:    {D(fila['saldo']):,.0f}")
    assert D(fila["saldo"]) == 60_000  # 40.000 + 20.000
    # El desglose suma exacto lo acumulado
    assert sum(D(t["valor"]) for t in fila["tramos"]) == D(fila["acumulado_periodo"])


def test_dos_tramos_del_mismo_senor_en_un_despacho(client, base_datos):
    """Si el mismo conductor hace los dos tramos escritos distinto, el segundo
    adopta la escritura del primero sin esperar a que se guarde."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    venta = _vender(
        client, h, producto, cliente, kilos="100",
        tramos=[{"destino": "San Vicente", "conductor": "Jose Lavado",
                 "valor_por_kilo": "400"},
                {"origen": "San Vicente", "destino": "Bogotá",
                 "conductor": "jose lavado", "valor_por_kilo": "600"}],
    )
    nombres = [t["conductor"] for t in venta["tramos_flete"]]
    print("\n===== (c2) EL MISMO SEÑOR EN LOS DOS TRAMOS =====")
    print(f"  conductores del despacho: {nombres}")
    assert nombres == ["Jose Lavado", "Jose Lavado"]

    fila = _fila(_panel_conductores(client, h), "Jose Lavado")
    print(f"  se le debe todo el flete: {D(fila['saldo']):,.0f}")
    assert D(fila["saldo"]) == 100_000


# ---------------------------------------------------------------------------
# (d) LO QUE SE LE DEBE BAJA AL PAGAR, Y NO SE PUEDE PAGAR DE MÁS
# ---------------------------------------------------------------------------
def _pagar(client, h, conductor, valor, fecha="2026-06-20", esperado=201):
    r = client.post(
        f"{CONDUCTORES}/pagos",
        json={"conductor": conductor, "fecha": fecha, "valor": str(valor)},
        headers=h,
    )
    assert r.status_code == esperado, r.text
    return r


def test_lo_que_se_le_debe_baja_al_pagarle(client, base_datos):
    """El "se le debe" tiene que ser real y no un acumulado que nunca baja."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    _vender(client, h, producto, cliente, kilos="100",
            tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                     "valor_por_kilo": "1000"}])

    print("\n===== (d) SE LE PAGA AL CONDUCTOR =====")
    fila = _fila(_panel_conductores(client, h), "Jose Lavado")
    print(f"  acumulado {D(fila['total_acumulado']):>10,.0f} · "
          f"pagado {D(fila['total_pagado']):>10,.0f} · "
          f"se le debe {D(fila['saldo']):>10,.0f}")
    assert D(fila["saldo"]) == 100_000

    _pagar(client, h, "Jose Lavado", 40_000)
    fila = _fila(_panel_conductores(client, h), "Jose Lavado")
    print(f"  se le abonan 40.000 →")
    print(f"  acumulado {D(fila['total_acumulado']):>10,.0f} · "
          f"pagado {D(fila['total_pagado']):>10,.0f} · "
          f"se le debe {D(fila['saldo']):>10,.0f}")
    assert D(fila["total_pagado"]) == 40_000
    assert D(fila["saldo"]) == 60_000
    # acumulado − pagado = saldo, exacto
    assert D(fila["total_acumulado"]) - D(fila["total_pagado"]) == D(fila["saldo"])
    # Y el pago queda en el historial
    assert len(fila["pagos"]) == 1
    assert D(fila["pagos"][0]["valor"]) == 40_000


def test_no_se_le_puede_pagar_mas_de_lo_que_se_le_debe(client, base_datos):
    """Pagarle de más dejaría un saldo negativo que le rebajaría la deuda a los
    demás conductores en el total de la pantalla."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    _vender(client, h, producto, cliente, kilos="100",
            tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                     "valor_por_kilo": "1000"}])

    print("\n===== (d2) NO SE PUEDE PAGAR DE MÁS =====")
    r = _pagar(client, h, "Jose Lavado", 100_001, esperado=422)
    print(f"  pagarle 100.001 de 100.000: {r.status_code} · "
          f"{r.json()['error']['detail']}")
    assert "supera" in r.json()["error"]["detail"]

    # Justo lo que se le debe, sí
    _pagar(client, h, "Jose Lavado", 100_000)
    fila = _fila(_panel_conductores(client, h), "Jose Lavado")
    print(f"  pagarle los 100.000 exactos: queda en {D(fila['saldo']):,.0f}")
    assert D(fila["saldo"]) == 0

    # Y ya no se le puede pagar más
    r = _pagar(client, h, "Jose Lavado", 1, esperado=422)
    print(f"  intentar un peso más: {r.status_code} · {r.json()['error']['detail']}")
    assert "no se le debe nada" in r.json()["error"]["detail"].lower()


def test_al_pagar_tambien_se_canoniza_el_nombre(client, base_datos):
    """Si al pagar se escribe distinto, el pago tiene que llegarle al mismo señor
    y no crear un conductor nuevo con la plata colgada."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    _vender(client, h, producto, cliente, kilos="100",
            tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                     "valor_por_kilo": "1000"}])
    _pagar(client, h, "  jose   LAVADO  ", 30_000)

    p = _panel_conductores(client, h)
    print("\n===== (d3) EL PAGO ENCUENTRA AL MISMO SEÑOR =====")
    print(f"  conductores: {[c['conductor'] for c in p['conductores']]}")
    assert len(p["conductores"]) == 1
    fila = p["conductores"][0]
    print(f"  se le debe {D(fila['saldo']):,.0f} de {D(fila['total_acumulado']):,.0f}")
    assert fila["conductor"] == "Jose Lavado"
    assert D(fila["saldo"]) == 70_000


def test_los_totales_de_la_pantalla_suman_las_filas(client, base_datos):
    """La cifra grande tiene que ser la suma de las filas, exacta: el dueño la
    cuadra a mano."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    _vender(client, h, producto, cliente, kilos="100", fecha="2026-06-10",
            tramos=[{"destino": "San Vicente", "conductor": "Jose Lavado",
                     "valor_por_kilo": "400"},
                    {"origen": "San Vicente", "destino": "Bogotá",
                     "conductor": "Marta Ruiz", "valor_por_kilo": "600"}])
    _vender(client, h, producto, cliente, kilos="50", fecha="2026-06-12",
            tramos=[{"destino": "Villavicencio", "conductor": "Pedro Nel",
                     "valor_por_kilo": "700"}])
    _pagar(client, h, "Marta Ruiz", 25_000)

    p = _panel_conductores(client, h, desde="2026-06-01", hasta="2026-06-30")
    print("\n===== (d4) EL CUADRE DE LA PANTALLA =====")
    print(f"  {'conductor':<14}{'acumulado':>12}{'pagado':>10}{'se le debe':>12}")
    for fila in p["conductores"]:
        print(f"  {fila['conductor']:<14}{D(fila['acumulado_periodo']):>12,.0f}"
              f"{D(fila['pagado_periodo']):>10,.0f}{D(fila['saldo']):>12,.0f}")
    print(f"  {'TOTAL':<14}{D(p['total_acumulado_periodo']):>12,.0f}"
          f"{D(p['total_pagado_periodo']):>10,.0f}{D(p['total_saldo']):>12,.0f}")

    assert sum(D(c["acumulado_periodo"]) for c in p["conductores"]) == D(
        p["total_acumulado_periodo"]
    )
    assert sum(D(c["pagado_periodo"]) for c in p["conductores"]) == D(
        p["total_pagado_periodo"]
    )
    assert sum(D(c["saldo"]) for c in p["conductores"]) == D(p["total_saldo"])
    # 40.000 + 60.000 + 35.000
    assert D(p["total_acumulado_periodo"]) == 135_000
    assert D(p["total_saldo"]) == 110_000

    # Y cada fila cuadra con su propio desglose de tramos
    for fila in p["conductores"]:
        assert sum(D(t["valor"]) for t in fila["tramos"]) == D(fila["acumulado_periodo"])


def test_el_filtro_de_fechas_no_cambia_lo_que_se_le_debe(client, base_datos):
    """Mover el filtro puede cambiar lo que se ve del período, pero NO lo que se
    le debe a una persona: con esa cifra el dueño paga."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    _vender(client, h, producto, cliente, kilos="100", fecha="2026-05-10",
            tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                     "valor_por_kilo": "1000"}])

    print("\n===== (d5) EL FILTRO NO ESCONDE LA DEUDA =====")
    p = _panel_conductores(client, h, desde="2026-06-01", hasta="2026-06-30")
    fila = _fila(p, "Jose Lavado")
    print(f"  mirando junio (el viaje fue en mayo):")
    print(f"    acumulado del período {D(fila['acumulado_periodo']):>10,.0f}")
    print(f"    se le debe            {D(fila['saldo']):>10,.0f}")
    assert D(fila["acumulado_periodo"]) == 0
    assert D(fila["saldo"]) == 100_000, "la deuda desapareció al mover el filtro"


def test_anular_la_venta_le_quita_el_viaje_al_conductor(client, base_datos):
    """Al anular se reintegra el inventario: ese despacho no salió, y no se le
    puede seguir debiendo el viaje a nadie."""
    h = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h)
    venta = _vender(client, h, producto, cliente, kilos="100",
                    tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                             "valor_por_kilo": "1000"}])
    antes = _fila(_panel_conductores(client, h), "Jose Lavado")
    r = client.post(f"{API}/{venta['id']}/anular", headers=h)
    assert r.status_code == 200, r.text

    p = _panel_conductores(client, h)
    print("\n===== (d6) SE ANULA LA VENTA =====")
    print(f"  antes se le debían {D(antes['saldo']):,.0f}")
    print(f"  después: {[c['conductor'] for c in p['conductores']] or 'nadie'}")
    assert D(antes["saldo"]) == 100_000
    assert p["conductores"] == []


# ---------------------------------------------------------------------------
# (e) BORRAR UN PAGO EXIGE `eliminar`, NO `crear`
# ---------------------------------------------------------------------------
def _usuario_de_ventas(client, base_datos):
    """Un usuario con el rol 'Ventas': puede crear y editar, pero NO eliminar."""
    h_super = auth_headers(client, "superadmin")
    empresa_a = str(base_datos["empresa_a"].id)
    roles = {
        r["nombre"]: r["id"]
        for r in client.get(
            "/api/v1/roles", params={"page_size": 100}, headers=h_super
        ).json()["items"]
    }
    creado = client.post(
        "/api/v1/usuarios",
        json={"nombre": "Vendedora", "apellido": "Prueba",
              "correo": "vendedora@pruebas.com", "username": "vendedora",
              "password": PASSWORD, "rol_ids": [roles["Ventas"]]},
        headers={**h_super, "X-Empresa-Id": empresa_a},
    )
    assert creado.status_code == 201, creado.text
    return auth_headers(client, "vendedora")


def test_borrar_un_pago_exige_el_permiso_de_eliminar(client, base_datos):
    """Borrar un pago SUBE lo que se le debe al conductor: es tan delicado como
    registrarlo. Quien solo puede crear no puede deshacer.

    (Ya nos pasó al revés en otro módulo: el borrado pedía 'crear'.)
    """
    h_admin = auth_headers(client, "admin.a")
    producto, cliente = _preparar_bodega(client, h_admin)
    _vender(client, h_admin, producto, cliente, kilos="100",
            tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                     "valor_por_kilo": "1000"}])

    h_ventas = _usuario_de_ventas(client, base_datos)
    # El rol Ventas SÍ puede registrar el pago
    pago = _pagar(client, h_ventas, "Jose Lavado", 40_000).json()
    print("\n===== (e) BORRAR UN PAGO EXIGE 'eliminar' =====")
    print(f"  el rol Ventas registra el pago: 201")

    r = client.delete(f"{CONDUCTORES}/pagos/{pago['id']}", headers=h_ventas)
    print(f"  el rol Ventas intenta borrarlo: {r.status_code} (no tiene 'eliminar')")
    assert r.status_code == 403

    # El pago sigue ahí y la deuda no se movió
    fila = _fila(_panel_conductores(client, h_admin), "Jose Lavado")
    print(f"  la deuda sigue en {D(fila['saldo']):,.0f}")
    assert D(fila["saldo"]) == 60_000

    # El administrador, que sí tiene 'eliminar', lo borra y la deuda vuelve a subir
    r = client.delete(f"{CONDUCTORES}/pagos/{pago['id']}", headers=h_admin)
    print(f"  el administrador lo borra:       {r.status_code}")
    assert r.status_code == 204
    fila = _fila(_panel_conductores(client, h_admin), "Jose Lavado")
    print(f"  la deuda vuelve a {D(fila['saldo']):,.0f}")
    assert D(fila["saldo"]) == 100_000
    assert D(fila["total_pagado"]) == 0


# ---------------------------------------------------------------------------
# (f) NO SE CRUZAN LAS EMPRESAS
# ---------------------------------------------------------------------------
def test_los_conductores_no_se_cruzan_entre_empresas(client, base_datos):
    """Cada quesera ve SOLO sus conductores, sus tramos y sus pagos. Dos empresas
    pueden tener un conductor que se llame igual y son dos cuentas distintas."""
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")

    producto_a, cliente_a = _preparar_bodega(client, h_a)
    producto_b, cliente_b = _preparar_bodega(client, h_b)

    _vender(client, h_a, producto_a, cliente_a, kilos="100",
            tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                     "valor_por_kilo": "1000"}])
    _vender(client, h_b, producto_b, cliente_b, kilos="50",
            tramos=[{"destino": "Cali", "conductor": "Jose Lavado",
                     "valor_por_kilo": "800"}])

    p_a = _panel_conductores(client, h_a)
    p_b = _panel_conductores(client, h_b)
    print("\n===== (f) LAS EMPRESAS NO SE CRUZAN =====")
    print(f"  empresa A: se le debe a Jose Lavado "
          f"{D(_fila(p_a, 'Jose Lavado')['saldo']):,.0f}")
    print(f"  empresa B: se le debe a Jose Lavado "
          f"{D(_fila(p_b, 'Jose Lavado')['saldo']):,.0f}")
    assert D(_fila(p_a, "Jose Lavado")["saldo"]) == 100_000
    assert D(_fila(p_b, "Jose Lavado")["saldo"]) == 40_000
    assert len(p_a["conductores"]) == len(p_b["conductores"]) == 1
    # Y el detalle de A no trae ningún despacho de B
    assert all(t["cliente"] == "Tienda La 33" for t in _fila(p_a, "Jose Lavado")["tramos"])
    assert len(_fila(p_a, "Jose Lavado")["tramos"]) == 1

    # A paga; a B no se le mueve nada
    _pagar(client, h_a, "Jose Lavado", 100_000)
    p_b = _panel_conductores(client, h_b)
    print(f"  A le paga 100.000 → B sigue debiendo "
          f"{D(_fila(p_b, 'Jose Lavado')['saldo']):,.0f}")
    assert D(_fila(p_b, "Jose Lavado")["saldo"]) == 40_000


def test_una_empresa_no_borra_el_pago_de_la_otra(client, base_datos):
    """El pago de otra empresa no existe para esta consulta: 404 antes de tocar
    nada, porque se busca por el repositorio que ya filtra por empresa."""
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    producto_a, cliente_a = _preparar_bodega(client, h_a)
    _vender(client, h_a, producto_a, cliente_a, kilos="100",
            tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                     "valor_por_kilo": "1000"}])
    pago = _pagar(client, h_a, "Jose Lavado", 50_000).json()

    r = client.delete(f"{CONDUCTORES}/pagos/{pago['id']}", headers=h_b)
    print("\n===== (f2) NI BORRAR EL PAGO DE LA OTRA =====")
    print(f"  la empresa B intenta borrar un pago de A: {r.status_code}")
    assert r.status_code == 404

    fila = _fila(_panel_conductores(client, h_a), "Jose Lavado")
    print(f"  el pago de A sigue ahí: se le debe {D(fila['saldo']):,.0f}")
    assert D(fila["total_pagado"]) == 50_000


def test_las_sugerencias_son_solo_de_la_empresa(client, base_datos):
    """El autocompletado no puede soplar los nombres de la otra quesera."""
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    producto_a, cliente_a = _preparar_bodega(client, h_a)
    _vender(client, h_a, producto_a, cliente_a, kilos="100",
            tramos=[{"destino": "Bogotá", "conductor": "Jose Lavado",
                     "valor_por_kilo": "1000"}])

    sug_a = client.get(f"{CONDUCTORES}/sugerencias", headers=h_a).json()["conductores"]
    sug_b = client.get(f"{CONDUCTORES}/sugerencias", headers=h_b).json()["conductores"]
    print("\n===== (f3) LAS SUGERENCIAS =====")
    print(f"  empresa A: {sug_a}")
    print(f"  empresa B: {sug_b}")
    assert sug_a == ["Jose Lavado"]
    assert sug_b == []
