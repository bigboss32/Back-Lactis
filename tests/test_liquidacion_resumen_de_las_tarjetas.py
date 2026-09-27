"""LAS TARJETAS DEL LISTADO DE LIQUIDACIONES, sumadas en la base y sin tope de filas.

La pantalla armaba las tarjetas pidiendo cada estado con 200 filas y sumando lo que le
llegaba. Desde que la quincena en firme que quedó debiendo se filtra como 'pagada' (el
filtro que crece sin parar), la deuda de la fila 201 no llegaba nunca: la tarjeta "Le
quedaron debiendo a la quesera" decía $0 con el tablero diciendo $120.000 al lado.

`GET /liquidaciones/resumen` cuenta y suma en SQL con los MISMOS criterios del listado.
Lo que se mide aquí:

  · cada conteo es el `total` de `GET /liquidaciones?estado=<ese>` con el mismo tipo y
    las mismas fechas, en todas las combinaciones;
  · las cifras de plata son las que el dueño saca con calculadora de esas mismas filas;
  · con más de 200 pagadas, la deuda vieja sigue contando;
  · una quesera no ve las filas de la otra, ni las borradas.

Las filas se escriben directo en la tabla, con las formas que existen en la base del
cliente, y todas cumplen neto_a_pagar = pagado + saldo.
"""
import itertools
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.dialects import postgresql

from app.modules.liquidaciones.models import Liquidacion
from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
V = "/api/v1"
ESTADOS = ("borrador", "aprobada", "parcial", "pagada")

Q1 = (date(2026, 7, 1), date(2026, 7, 15))
Q2 = (date(2026, 7, 16), date(2026, 7, 31))
Q3 = (date(2026, 8, 1), date(2026, 8, 15))


def D(v):
    return Decimal(str(v))


def _fila(db, empresa_id, *, tipo, tercero_id, periodo, estado, valor_total,
          anticipos="0", saldo_anterior="0", pagado="0", borrada=False, clave=""):
    vt, an, sa, pg = (D(x) for x in (valor_total, anticipos, saldo_anterior, pagado))
    liq = Liquidacion(
        empresa_id=empresa_id,
        tipo=tipo,
        proveedor_id=tercero_id if tipo == "proveedor" else None,
        transportador_id=tercero_id if tipo == "transportador" else None,
        periodo_inicio=periodo[0],
        periodo_fin=periodo[1],
        total_litros=D("100"),
        valor_bruto=vt,
        valor_total=vt,
        anticipos=an,
        saldo_anterior=sa,
        pagado=pg,
        saldo=vt - an - sa - pg,
        estado=estado,
        observaciones=clave,
    )
    if borrada:
        liq.deleted_at = datetime.now(timezone.utc)
    db.add(liq)
    db.flush()
    assert liq.neto_a_pagar == liq.pagado + liq.saldo
    return liq


def _proveedor(client, h, nombre):
    r = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": "2000"}, headers=h)
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


def _montar(client, db, base_datos):
    """Una fila de cada forma. Las cifras de cada una van en el comentario."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    rosa, henri, pedro, lucia, mario = (
        _proveedor(client, h, n) for n in ("Rosa", "Henri", "Pedro", "Lucia", "Mario"))
    t = client.post(f"{V}/transportadores", json={
        "nombre": "Alex", "valor_transporte": "100"}, headers=h)
    assert t.status_code == 201, t.text
    alex = uuid.UUID(t.json()["id"])

    P, T = "proveedor", "transportador"
    f = {}
    # borrador por pagar: 400.000 - 100.000 = 300.000
    f["borr"] = _fila(db, emp, tipo=P, tercero_id=rosa, periodo=Q3, estado="borrador",
                      valor_total="400000", anticipos="100000")
    # borrador que quedó debiendo: 180.000 - 300.000 = -120.000 (la deuda nace aquí)
    f["borr_debe"] = _fila(db, emp, tipo=P, tercero_id=henri, periodo=Q3, estado="borrador",
                           valor_total="180000", anticipos="300000")
    # aprobada por pagar: 500.000 - 200.000 = 300.000
    f["apr_pend"] = _fila(db, emp, tipo=P, tercero_id=pedro, periodo=Q2, estado="aprobada",
                          valor_total="500000", anticipos="200000")
    # aprobada que quedó debiendo: 500.000 - 700.000 = -200.000 -> se lee "pagada"
    f["apr_debe"] = _fila(db, emp, tipo=P, tercero_id=mario, periodo=Q2, estado="aprobada",
                          valor_total="500000", anticipos="700000")
    # aprobada que quedó debiendo -120.000 y YA SE LA COBRÓ la siguiente (apr_cobra)
    f["apr_debe_cobrada"] = _fila(db, emp, tipo=P, tercero_id=henri, periodo=Q1,
                                  estado="aprobada", valor_total="180000", anticipos="300000")
    # la que se la cobró: 400.000 - 120.000 de la quincena pasada = 280.000
    f["apr_cobra"] = _fila(db, emp, tipo=P, tercero_id=henri, periodo=Q2, estado="aprobada",
                           valor_total="400000", saldo_anterior="120000")
    f["apr_debe_cobrada"].deuda_trasladada_a_id = f["apr_cobra"].id
    # parcial: 600.000 - 100.000 = 500.000, abonado 200.000, faltan 300.000
    f["parc"] = _fila(db, emp, tipo=P, tercero_id=rosa, periodo=Q2, estado="parcial",
                      valor_total="600000", anticipos="100000", pagado="200000")
    # pagada normal: 450.000 - 50.000 = 400.000, pagado 400.000
    f["pag"] = _fila(db, emp, tipo=P, tercero_id=rosa, periodo=Q1, estado="pagada",
                     valor_total="450000", anticipos="50000", pagado="400000")
    # pagada que quedó debiendo (el botón Pagar de antes): 180.000 - 300.000 = -120.000
    f["pag_debe"] = _fila(db, emp, tipo=P, tercero_id=pedro, periodo=Q1, estado="pagada",
                          valor_total="180000", anticipos="300000")
    # anulada que debía -300.000: no se cobra en ninguna parte y no cuenta
    f["anu_debe"] = _fila(db, emp, tipo=P, tercero_id=lucia, periodo=Q3, estado="anulada",
                          valor_total="100000", anticipos="400000")
    # transportador: aprobada debiendo 90.000 - 150.000 = -60.000
    f["tr_apr_debe"] = _fila(db, emp, tipo=T, tercero_id=alex, periodo=Q2, estado="aprobada",
                             valor_total="90000", anticipos="150000")
    # transportador: pagada normal 120.000
    f["tr_pag"] = _fila(db, emp, tipo=T, tercero_id=alex, periodo=Q1, estado="pagada",
                        valor_total="120000", pagado="120000")
    # transportador: parcial 200.000, abonado 50.000, faltan 150.000
    f["tr_parc"] = _fila(db, emp, tipo=T, tercero_id=alex, periodo=Q3, estado="parcial",
                         valor_total="200000", pagado="50000")
    # LO QUE NO PUEDE CONTAR NUNCA: la borrada y la de la otra quesera, las dos debiendo.
    _fila(db, emp, tipo=P, tercero_id=lucia, periodo=Q2, estado="aprobada",
          valor_total="500000", anticipos="900000", borrada=True)
    _fila(db, base_datos["empresa_b"].id, tipo=P, tercero_id=lucia, periodo=Q2,
          estado="aprobada", valor_total="500000", anticipos="900000")
    db.commit()
    return h, f


def _resumen(client, h, **q):
    r = client.get(f"{API}/resumen", params={k: v for k, v in q.items() if v is not None},
                   headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _lista(client, h, **q):
    q = {k: v for k, v in q.items() if v is not None}
    r = client.get(API, params={**q, "page_size": 200}, headers=h)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["total"] == len(cuerpo["items"]), "aquí todo cabe en una página"
    return cuerpo


def _tarjetas_como_las_armaba_la_pantalla(client, h, **filtros):
    """`cargarResumen` de liquidacion-list.page.ts, fila por fila (cabe todo en 200).

    Con lo que se le sumó después: la plata por pagar deja por fuera la fila con deuda
    borrada por la migración (el servidor no la deja pagar), y esas filas se cuentan
    aparte. Los cuatro filtros de estado cubren todas las no anuladas sin repetir."""
    por = {e: _lista(client, h, estado=e, **filtros) for e in ESTADOS}
    filas = [x for e in ESTADOS for x in por[e]["items"]]
    deben = [x for x in filas
             if D(x["le_queda_debiendo"]) > 0 and not x["deuda_trasladada_a_id"]]
    por_reparar = [x for x in filas if D(x["deuda_borrada_por_la_migracion"]) > 0]

    def por_pagar(estado):
        return sum((max(D(0), D(x["saldo"])) for x in por[estado]["items"]
                    if D(x["deuda_borrada_por_la_migracion"]) == 0), D(0))

    return {
        "borradores": por["borrador"]["total"],
        "aprobadas": por["aprobada"]["total"],
        "saldo_aprobadas": por_pagar("aprobada"),
        "parciales": por["parcial"]["total"],
        "saldo_parciales": por_pagar("parcial"),
        "pagadas": por["pagada"]["total"],
        "le_quedaron_debiendo": sum((D(x["le_queda_debiendo"]) for x in deben), D(0)),
        "liquidaciones_que_deben": len(deben),
        "por_reparar": len(por_reparar),
        "deuda_borrada": sum((D(x["deuda_borrada_por_la_migracion"]) for x in por_reparar),
                             D(0)),
    }


def _como_numeros(resumen):
    return {k: (D(v) if isinstance(v, str) else v) for k, v in resumen.items()}


# ---------------------------------------------------------------------------------------
def test_las_cifras_de_la_calculadora_sin_filtros(client, db_session, base_datos):
    """Las cifras sacadas a mano de los comentarios de `_montar`."""
    h, _ = _montar(client, db_session, base_datos)
    r = _como_numeros(_resumen(client, h))
    print(f"\n  resumen: {r}")
    assert r == {
        "borradores": 2,                       # borr, borr_debe
        "aprobadas": 2,                        # apr_pend, apr_cobra
        "saldo_aprobadas": D("580000"),        # 300.000 + 280.000
        "parciales": 2,                        # parc, tr_parc
        "saldo_parciales": D("450000"),        # 300.000 + 150.000
        "pagadas": 6,                          # apr_debe, apr_debe_cobrada, pag, pag_debe,
                                               # tr_apr_debe, tr_pag
        "le_quedaron_debiendo": D("500000"),   # 120.000 + 200.000 + 120.000 + 60.000
        "liquidaciones_que_deben": 4,          # borr_debe, apr_debe, pag_debe, tr_apr_debe
        "por_reparar": 0,                      # ninguna trae deuda borrada por la migración
        "deuda_borrada": D("0"),
    }


def test_las_cifras_de_la_calculadora_con_tipo_y_con_fechas(client, db_session, base_datos):
    h, _ = _montar(client, db_session, base_datos)
    solo_leche = _como_numeros(_resumen(client, h, tipo="proveedor"))
    assert solo_leche == {
        "borradores": 2, "aprobadas": 2, "saldo_aprobadas": D("580000"),
        "parciales": 1, "saldo_parciales": D("300000"), "pagadas": 4,
        "le_quedaron_debiendo": D("440000"),   # 120.000 + 200.000 + 120.000
        "liquidaciones_que_deben": 3, "por_reparar": 0, "deuda_borrada": D("0"),
    }
    # Solo la segunda de julio: apr_pend, apr_debe, apr_cobra, parc y tr_apr_debe.
    segunda = _como_numeros(_resumen(client, h, desde="2026-07-16", hasta="2026-07-31"))
    assert segunda == {
        "borradores": 0, "aprobadas": 2, "saldo_aprobadas": D("580000"),
        "parciales": 1, "saldo_parciales": D("300000"), "pagadas": 2,
        "le_quedaron_debiendo": D("260000"),   # 200.000 + 60.000
        "liquidaciones_que_deben": 2, "por_reparar": 0, "deuda_borrada": D("0"),
    }


def test_cada_tarjeta_es_lo_que_da_la_lista_con_ese_filtro(client, db_session, base_datos):
    """En todas las combinaciones de tipo y fechas: la tarjeta dice lo mismo que las
    cuatro consultas de la lista sumadas fila por fila."""
    h, _ = _montar(client, db_session, base_datos)
    tipos = (None, "proveedor", "transportador")
    periodos = ((None, None), ("2026-07-16", "2026-07-31"), ("2026-07-10", None),
                (None, "2026-07-15"), ("2026-08-01", "2026-08-15"), ("2026-09-01", None))
    for tipo, (desde, hasta) in itertools.product(tipos, periodos):
        filtros = {"tipo": tipo, "desde": desde, "hasta": hasta}
        resumen = _como_numeros(_resumen(client, h, **filtros))
        esperado = _tarjetas_como_las_armaba_la_pantalla(client, h, **filtros)
        assert resumen == esperado, (filtros, resumen, esperado)


def test_con_mas_de_200_pagadas_la_deuda_vieja_sigue_contando(client, db_session, base_datos):
    """"Se fue" debe $120.000 desde enero de 2025 y no volvió a entregar leche. Detrás
    vienen 205 quincenas pagadas de otro productor: la fila de la deuda queda fuera de
    la página de 200 del filtro 'pagada', que es de donde la pantalla la sumaba."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    se_fue, constante = _proveedor(client, h, "Se fue"), _proveedor(client, h, "Constante")
    vieja = _fila(db_session, emp, tipo="proveedor", tercero_id=se_fue,
                  periodo=(date(2025, 1, 1), date(2025, 1, 15)), estado="aprobada",
                  valor_total="180000", anticipos="300000")
    for i in range(205):
        ini = date(2025, 1, 16) + timedelta(days=i * 2)
        _fila(db_session, emp, tipo="proveedor", tercero_id=constante,
              periodo=(ini, ini + timedelta(days=1)), estado="pagada",
              valor_total="100000", pagado="100000")
    db_session.commit()

    pagina = client.get(API, params={"estado": "pagada", "page_size": 200}, headers=h).json()
    assert pagina["total"] == 206
    assert str(vieja.id) not in {x["id"] for x in pagina["items"]}, (
        "la deuda es la fila 206: la página de 200 no la trae")

    r = _como_numeros(_resumen(client, h))
    print(f"\n  resumen con 206 pagadas: {r}")
    assert r["pagadas"] == 206
    assert r["le_quedaron_debiendo"] == D("120000")
    assert r["liquidaciones_que_deben"] == 1
    tablero = client.get(f"{V}/reportes/dashboard", headers=h).json()
    assert D(tablero["terceros_le_quedan_debiendo"]) == r["le_quedaron_debiendo"]


def test_una_quesera_no_ve_las_cifras_de_la_otra(client, db_session, base_datos):
    h_a, _ = _montar(client, db_session, base_datos)
    h_b = auth_headers(client, "admin.b")
    b = _como_numeros(_resumen(client, h_b))
    # La única fila de la quesera B: aprobada, 500.000 - 900.000 = -400.000.
    assert b == {
        "borradores": 0, "aprobadas": 0, "saldo_aprobadas": D("0"),
        "parciales": 0, "saldo_parciales": D("0"), "pagadas": 1,
        "le_quedaron_debiendo": D("400000"), "liquidaciones_que_deben": 1,
        "por_reparar": 0, "deuda_borrada": D("0"),
    }
    # Y la de A no suma ni esos 400.000 ni los 400.000 de su propia fila borrada.
    assert D(_resumen(client, h_a)["le_quedaron_debiendo"]) == D("500000")


def test_la_consulta_es_una_sola_y_compila_para_postgres(client, db_session, base_datos,
                                                        monkeypatch):
    """Sin Docker no hay Postgres: se mira el SQL que le llegaría. Una sola consulta,
    sin LIMIT (no hay tope) y con el filtro de empresa y el de borradas."""
    h, _ = _montar(client, db_session, base_datos)
    capturadas = []
    original = db_session.execute

    def espia(stmt, *a, **k):
        capturadas.append(stmt)
        return original(stmt, *a, **k)

    monkeypatch.setattr(db_session, "execute", espia)
    _resumen(client, h, tipo="proveedor", desde="2026-07-01")
    consultas = [s for s in capturadas if "CASE WHEN" in str(s)]
    assert len(consultas) == 1
    sql = str(consultas[0].compile(dialect=postgresql.dialect(),
                                   compile_kwargs={"literal_binds": True}))
    print("\n  " + " ".join(sql.split()))
    assert "LIMIT" not in sql.upper()
    assert "liquidaciones.deleted_at IS NULL" in sql
    assert "liquidaciones.empresa_id = " in sql
    assert "JOIN" not in sql.upper()
