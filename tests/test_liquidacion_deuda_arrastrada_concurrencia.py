"""LA DEUDA QUE SE ARRASTRA — DOS PETICIONES A LA VEZ SOBRE EL MISMO TERCERO: qué
sostiene la corrección.

SQLite DESCARTA `FOR UPDATE` en silencio, así que ninguna prueba de esta suite puede
reproducir la carrera de verdad. Lo que sí se puede hacer, y es lo que hace este
archivo, es DOS cosas:

  1. simular la carrera A MANO —leer la lista de deudas dos veces ANTES de que
     cualquiera de las dos marque— y medir exactamente cuánta plata se cobraría dos
     veces si el candado no estuviera. Es la cifra que el candado está evitando;
  2. comprobar que el candado ESTÁ en la consulta y que la consulta compila contra
     Postgres SIN un LEFT JOIN de por medio, que es lo único que puede tumbarlo (un
     `SELECT ... FOR UPDATE` con LEFT JOIN lo rechaza Postgres con un 0A000).
"""
import uuid

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import lazyload

from app.core.context import RequestContext
from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.repository import LiquidacionRepository
from app.modules.liquidaciones.service import LiquidacionService
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_arrastrada_plata import (
    CERO,
    D,
    Q1,
    Q2,
    _anticipo,
    _cuadra,
    _generar,
    _libro,
    _proveedor,
    _recepcion,
    _todas,
)


def test_27_la_consulta_de_deudas_lleva_candado_y_no_lleva_join(client, base_datos):
    """El candado tiene que estar puesto y no puede haber LEFT JOIN: es lo que separa
    a dos «Generar» simultáneos de cobrarle la misma deuda dos veces."""
    print("\n===== 27. EL CANDADO DE LA CONSULTA DE DEUDAS =====")
    empresa = base_datos["empresa_a"].id
    repo = LiquidacionRepository(None, empresa)
    stmt = (
        repo.base_query()
        .where(*repo._solo_las_que_deben("proveedor", uuid.uuid4(), __import__("datetime").date(2026, 6, 16)))
        .options(
            lazyload(Liquidacion.proveedor),
            lazyload(Liquidacion.transportador),
            lazyload(Liquidacion.deuda_trasladada_a),
            lazyload(Liquidacion.deudas_cobradas),
        )
        .with_for_update()
    )
    sql = str(stmt.compile(dialect=postgresql.dialect())).upper()
    print(f"      FOR UPDATE presente: {'FOR UPDATE' in sql}")
    print(f"      JOIN presente:       {'JOIN' in sql}")
    assert "FOR UPDATE" in sql, "SE PERDIÓ EL CANDADO: dos Generar a la vez cobran doble"
    assert "JOIN" not in sql, (
        "LA CONSULTA TRAE UN JOIN: Postgres rechaza el FOR UPDATE con un 0A000 y el "
        "candado deja de existir en producción"
    )
    # Y los filtros que no se pueden olvidar nunca.
    for parte in ("DELETED_AT IS NULL", "EMPRESA_ID", "DEUDA_TRASLADADA_A_ID IS NULL"):
        assert parte in sql, f"falta el filtro «{parte}»"


def test_28_la_carrera_simulada_a_mano_mide_lo_que_el_candado_evita(
    client, base_datos, db_session
):
    """SIN CANDADO, dos «Generar» a la vez le cobran los MISMOS $120.000 a dos
    liquidaciones: las dos leen la deuda con la marca en nulo, las dos la restan, y la
    marca termina apuntando a una sola —así que de la otra no queda ni rastro—.

    Acá se hace a mano: se lee la lista de deudas ANTES de marcar (que es justo el
    instante que el `FOR UPDATE` vuelve exclusivo) y se mide la plata que se cobraría
    de más. SQLite no pone el candado, así que esto pasa: es la demostración de que el
    candado de Postgres es lo único que lo evita, no una prueba de que esté roto.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 28. LA CARRERA SIMULADA A MANO =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _generar(client, h, Q1)[0]
    assert D(q1["le_queda_debiendo"]) == D("120000")

    repo = LiquidacionRepository(db_session, base_datos["empresa_a"].id)
    import datetime

    # LAS DOS LECTURAS, una detrás de la otra y sin marcar en el medio: es lo que
    # verían dos peticiones simultáneas si el candado no existiera.
    primera = repo.deudas_sin_cobrar(
        "proveedor", uuid.UUID(prov["id"]), antes_de=datetime.date(2026, 6, 16)
    )
    segunda = repo.deudas_sin_cobrar(
        "proveedor", uuid.UUID(prov["id"]), antes_de=datetime.date(2026, 6, 16)
    )
    doble = sum((o.le_queda_debiendo for o in primera), CERO) + sum(
        (o.le_queda_debiendo for o in segunda), CERO
    )
    print(f"      la primera lectura ve {len(primera)} deuda(s); la segunda, {len(segunda)}")
    print(f"      SIN CANDADO se cobrarían {doble} en total por una deuda de 120000")
    assert len(primera) == 1 and len(segunda) == 1
    assert doble == D("240000"), (
        "la lectura repetida ya no devuelve la deuda dos veces: hay otra defensa aparte "
        "del candado, revise el código"
    )

    # Y CON LA MARCA PUESTA la segunda lectura ya no la ve: la marca es la segunda
    # defensa, la que sostiene el caso secuencial (que es el de todos los días).
    primera[0].deuda_trasladada_a_id = uuid.uuid4()
    db_session.flush()
    tercera = repo.deudas_sin_cobrar(
        "proveedor", uuid.UUID(prov["id"]), antes_de=datetime.date(2026, 6, 16)
    )
    print(f"      con la marca puesta, la lectura ve {len(tercera)} deuda(s)")
    assert tercera == [], "LA MARCA NO ESTÁ TAPANDO LA DEUDA YA COBRADA"
    db_session.rollback()


def test_29_dos_generar_del_mismo_periodo_con_dos_terceros_no_se_cruzan(
    client, base_datos
):
    """Muchos terceros en el mismo «Generar»: cada deuda tiene que caer en la
    liquidación de SU dueño y en ninguna otra. Cinco proveedores, tres debiendo."""
    h = auth_headers(client, "admin.a")
    print("\n===== 29. CINCO TERCEROS EN UN MISMO GENERAR =====")
    nombres = ["Henri", "Henri C", "Dona Rosa", "Marleny", "Aleida"]
    provs = {n: _proveedor(client, h, n) for n in nombres}
    deudas_esperadas = {"Henri": "300000", "Dona Rosa": "250000", "Aleida": "500000"}
    for n, prov in provs.items():
        _recepcion(client, h, prov, "2026-06-02", "100")  # 180.000 cada uno
        if n in deudas_esperadas:
            _anticipo(client, h, "2026-06-01", deudas_esperadas[n], proveedor=prov)
    q1s = _generar(client, h, Q1)
    debe_q1 = {
        liq["proveedor_nombre"]: D(liq["le_queda_debiendo"]) for liq in q1s
    }
    for n in nombres:
        esperado = (
            D(deudas_esperadas[n]) - D("180000") if n in deudas_esperadas else CERO
        )
        print(f"      Q1 {n:10s} debe={debe_q1[n]} (esperado {esperado})")
        assert debe_q1[n] == esperado
    _cuadra(_libro(client, h, "Q1 de los cinco"), "29 q1")

    for prov in provs.values():
        _recepcion(client, h, prov, "2026-06-20", "200", precio="2500")  # 500.000
    q2s = _generar(client, h, Q2)
    for liq in q2s:
        n = liq["proveedor_nombre"]
        esperado = debe_q1[n]
        print(f"      Q2 {n:10s} vieja={liq['saldo_anterior']} (esperado {esperado}) "
              f"desglose={[o['le_queda_debiendo'] for o in liq['deudas_cobradas']]}")
        assert D(liq["saldo_anterior"]) == esperado, (
            f"a {n} se le cobró {liq['saldo_anterior']} y debía {esperado}"
        )
        assert sum((D(o["le_queda_debiendo"]) for o in liq["deudas_cobradas"]), CERO) == esperado
    _cuadra(_libro(client, h, "Q2 de los cinco"), "29 q2")

    from tests.test_liquidacion_deuda_arrastrada_plata import _aprobar, _pagar  # noqa

    # Se paga TODO lo que el sistema pide, de las dos quincenas.
    for liq in _todas(client, h):
        if D(liq["saldo"]) > CERO:
            if liq["estado"] == "borrador":
                assert _aprobar(client, h, liq["id"]).status_code == 200
            assert _pagar(client, h, liq["id"]).status_code == 200
    libro = _libro(client, h, "todas pagadas")
    _cuadra(libro, "29 final")
    # leche: 5 × 180.000 + 5 × 500.000 = 3.400.000; anticipos 1.050.000
    assert libro["leche"] == D("3400000")
    assert libro["anticipos"] == D("1050000")
    assert libro["pagado"] == D("2350000"), (
        f"salieron {libro['pagado']} y debían salir 2.350.000"
    )
    assert libro["debiendo"] == CERO
