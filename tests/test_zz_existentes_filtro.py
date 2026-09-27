"""LAS QUINCENAS QUE YA ESTÁN EN PRODUCCIÓN, vistas por el FILTRO DE ESTADO, las TARJETAS
del listado y el TABLERO, después del rótulo "pagada · quedó debiendo".

Las filas se escriben DIRECTO en la tabla, con las formas que ya existen en la base del
cliente (incluida la 'pagada' con saldo negativo y $0 pagados que dejaba el botón Pagar de
antes, y las deudas ya trasladadas, "quedó debiendo · cobrada"). No pasan por Generar a
propósito: una fila vieja no nació con el código de hoy, y lo que se mide es cómo LEE el
código de hoy lo que ya está guardado.

Siempre con el cuadre neto_a_pagar = pagado + saldo y
neto_a_pagar = valor_total - anticipos - saldo_anterior.
"""
import itertools
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql

from app.core.pagination import PageParams
from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.repository import LiquidacionRepository
from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
V = "/api/v1"
PD = "pagada · quedó debiendo"
ESTADOS = ("borrador", "aprobada", "parcial", "pagada", "anulada")
# Lo que el chip puede decir para cada opción del filtro de estado.
CHIP_DEL_FILTRO = {
    "borrador": {"borrador"},
    "aprobada": {"aprobada"},
    "parcial": {"parcial"},
    "pagada": {"pagada", PD},
    "anulada": {"anulada"},
}

Q1 = (date(2026, 7, 1), date(2026, 7, 15))
Q2 = (date(2026, 7, 16), date(2026, 7, 31))
Q3 = (date(2026, 8, 1), date(2026, 8, 15))


def _fila(db, empresa_id, *, clave, tipo, tercero_id, periodo, estado, valor_total,
          anticipos="0", saldo_anterior="0", pagado="0", version=1, borrada=False):
    vt, an, sa, pg = (Decimal(x) for x in (valor_total, anticipos, saldo_anterior, pagado))
    liq = Liquidacion(
        empresa_id=empresa_id,
        tipo=tipo,
        proveedor_id=tercero_id if tipo == "proveedor" else None,
        transportador_id=tercero_id if tipo == "transportador" else None,
        periodo_inicio=periodo[0],
        periodo_fin=periodo[1],
        total_litros=Decimal("100"),
        valor_bruto=vt,
        valor_total=vt,
        anticipos=an,
        saldo_anterior=sa,
        pagado=pg,
        saldo=vt - an - sa - pg,
        estado=estado,
        version=version,
        observaciones=clave,
    )
    if borrada:
        from datetime import datetime, timezone
        liq.deleted_at = datetime.now(timezone.utc)
    db.add(liq)
    db.flush()
    assert liq.neto_a_pagar == liq.pagado + liq.saldo  # el cuadre de siempre
    return liq


def _montar(client, db, base_datos):
    """El catálogo de formas que ya existen en la base del cliente."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id

    def prov(nombre):
        r = client.post(f"{V}/proveedores", json={
            "nombre": nombre, "vereda": "El Roble", "precio_litro": "2000"}, headers=h)
        assert r.status_code == 201, r.text
        return uuid.UUID(r.json()["id"])

    t = client.post(f"{V}/transportadores", json={
        "nombre": "Alex", "valor_transporte": "100"}, headers=h)
    assert t.status_code == 201, t.text
    alex = uuid.UUID(t.json()["id"])
    rosa, henri, pedro, lucia, mario = (prov(n) for n in ("Rosa", "Henri", "Pedro", "Lucia", "Mario"))

    f = {}
    P, T = "proveedor", "transportador"
    # --- borradores
    f["borr_normal"] = _fila(db, emp, clave="borr_normal", tipo=P, tercero_id=rosa, periodo=Q3,
                             estado="borrador", valor_total="400000", anticipos="100000")
    f["borr_debe"] = _fila(db, emp, clave="borr_debe", tipo=P, tercero_id=henri, periodo=Q3,
                           estado="borrador", valor_total="180000", anticipos="300000")
    # --- aprobadas
    f["apr_por_pagar"] = _fila(db, emp, clave="apr_por_pagar", tipo=P, tercero_id=pedro, periodo=Q2,
                               estado="aprobada", valor_total="500000", anticipos="200000")
    f["apr_cero"] = _fila(db, emp, clave="apr_cero", tipo=P, tercero_id=lucia, periodo=Q2,
                          estado="aprobada", valor_total="500000", anticipos="500000")
    f["apr_debe"] = _fila(db, emp, clave="apr_debe", tipo=P, tercero_id=mario, periodo=Q2,
                          estado="aprobada", valor_total="500000", anticipos="700000")
    # La de la captura del dueño: aprobada, quedó debiendo, y YA SE LA COBRÓ la siguiente.
    f["apr_debe_cobrada"] = _fila(db, emp, clave="apr_debe_cobrada", tipo=P, tercero_id=henri,
                                  periodo=Q1, estado="aprobada", valor_total="180000",
                                  anticipos="300000")
    f["apr_cobra_anterior"] = _fila(db, emp, clave="apr_cobra_anterior", tipo=P, tercero_id=henri,
                                    periodo=Q2, estado="aprobada", valor_total="400000",
                                    saldo_anterior="120000")
    f["apr_debe_cobrada"].deuda_trasladada_a_id = f["apr_cobra_anterior"].id
    # --- parciales
    f["parc_normal"] = _fila(db, emp, clave="parc_normal", tipo=P, tercero_id=rosa, periodo=Q2,
                             estado="parcial", valor_total="600000", anticipos="100000",
                             pagado="200000")
    # Forma defensiva: 'parcial' con saldo por debajo de cero (hoy no la produce ningún
    # camino, pero la columna la admite).
    f["parc_debe"] = _fila(db, emp, clave="parc_debe", tipo=P, tercero_id=lucia, periodo=Q1,
                           estado="parcial", valor_total="300000", anticipos="250000",
                           pagado="100000", version=2)
    # --- pagadas
    f["pag_normal"] = _fila(db, emp, clave="pag_normal", tipo=P, tercero_id=rosa, periodo=Q1,
                            estado="pagada", valor_total="450000", anticipos="50000",
                            pagado="400000")
    # La del botón Pagar de antes: marcada pagada sin salir un peso, con saldo negativo.
    f["pag_vieja_debe"] = _fila(db, emp, clave="pag_vieja_debe", tipo=P, tercero_id=pedro,
                                periodo=Q1, estado="pagada", valor_total="180000",
                                anticipos="300000")
    f["pag_vieja_debe_cobrada"] = _fila(db, emp, clave="pag_vieja_debe_cobrada", tipo=P,
                                        tercero_id=mario, periodo=Q1, estado="pagada",
                                        valor_total="100000", anticipos="150000")
    f["pag_vieja_debe_cobrada"].deuda_trasladada_a_id = f["apr_debe"].id  # cualquiera posterior
    # Corregida después de pagada: se le entregó de más.
    f["pag_corregida_debe"] = _fila(db, emp, clave="pag_corregida_debe", tipo=P, tercero_id=lucia,
                                    periodo=Q3, estado="pagada", valor_total="400000",
                                    pagado="500000", version=2)
    # --- anuladas
    f["anu_normal"] = _fila(db, emp, clave="anu_normal", tipo=P, tercero_id=mario, periodo=Q3,
                            estado="anulada", valor_total="300000")
    f["anu_debe"] = _fila(db, emp, clave="anu_debe", tipo=P, tercero_id=pedro, periodo=Q3,
                          estado="anulada", valor_total="100000", anticipos="400000")
    # --- transportador
    f["tr_apr_debe"] = _fila(db, emp, clave="tr_apr_debe", tipo=T, tercero_id=alex, periodo=Q2,
                             estado="aprobada", valor_total="90000", anticipos="150000")
    f["tr_pag"] = _fila(db, emp, clave="tr_pag", tipo=T, tercero_id=alex, periodo=Q1,
                        estado="pagada", valor_total="120000", pagado="120000")
    f["tr_borr"] = _fila(db, emp, clave="tr_borr", tipo=T, tercero_id=alex, periodo=Q3,
                         estado="borrador", valor_total="80000")
    # --- lo que NO puede salir nunca: borrada, y de la otra empresa
    _fila(db, emp, clave="borrada", tipo=P, tercero_id=rosa, periodo=Q2, estado="aprobada",
          valor_total="500000", anticipos="900000", borrada=True)
    _fila(db, base_datos["empresa_b"].id, clave="otra_empresa", tipo=P, tercero_id=rosa,
          periodo=Q2, estado="aprobada", valor_total="500000", anticipos="900000")
    db.commit()
    return h, f, {"rosa": rosa, "henri": henri, "pedro": pedro, "lucia": lucia,
                  "mario": mario, "alex": alex}


def _items(client, h, **q):
    q = {k: v for k, v in q.items() if v is not None}
    q.setdefault("page_size", 200)
    r = client.get(API, params=q, headers=h)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["total"] == len(cuerpo["items"]), "con 200 por página no puede quedar nada afuera"
    return cuerpo["items"]


# ---------------------------------------------------------------------------------------
def test_catalogo_cada_fila_existente_su_rotulo_y_su_filtro(client, db_session, base_datos):
    """Por cada forma que ya existe en la base: qué dice el chip y en qué filtro cae."""
    h, f, _ = _montar(client, db_session, base_datos)
    en_filtro = {e: {x["id"] for x in _items(client, h, estado=e)} for e in ESTADOS}
    todas = {x["id"]: x for x in _items(client, h)}

    esperado = {
        "borr_normal": ("borrador", "borrador"),
        "borr_debe": ("borrador", "borrador"),
        "apr_por_pagar": ("aprobada", "aprobada"),
        "apr_cero": ("aprobada", "aprobada"),
        "apr_debe": (PD, "pagada"),
        "apr_debe_cobrada": (PD, "pagada"),
        "apr_cobra_anterior": ("aprobada", "aprobada"),
        "parc_normal": ("parcial", "parcial"),
        "parc_debe": (PD, "pagada"),
        "pag_normal": ("pagada", "pagada"),
        "pag_vieja_debe": (PD, "pagada"),
        "pag_vieja_debe_cobrada": (PD, "pagada"),
        "pag_corregida_debe": (PD, "pagada"),
        "anu_normal": ("anulada", "anulada"),
        "anu_debe": ("anulada", "anulada"),
        "tr_apr_debe": (PD, "pagada"),
        "tr_pag": ("pagada", "pagada"),
        "tr_borr": ("borrador", "borrador"),
    }
    print()
    for clave, liq in f.items():
        fila = todas[str(liq.id)]
        cae_en = [e for e in ESTADOS if str(liq.id) in en_filtro[e]]
        print(f"  {clave:24} guardado={fila['estado']:9} saldo={Decimal(fila['saldo']):>12} "
              f"cobrada={bool(fila['deuda_trasladada_a_id'])!s:5} chip={fila['estado_visible']:26} "
              f"filtro={cae_en}")
        chip, filtro = esperado[clave]
        assert fila["estado_visible"] == chip, clave
        assert cae_en == [filtro], clave
    assert len(todas) == len(f), "ni la borrada ni la de la otra empresa pueden salir"


COMBOS_TIPO = (None, "proveedor", "transportador")
COMBOS_PERIODO = ((None, None), ("2026-07-16", "2026-07-31"), ("2026-07-10", None),
                  (None, "2026-07-15"), ("2026-08-01", "2026-08-15"))


@pytest.mark.parametrize("tipo", COMBOS_TIPO)
def test_la_union_de_los_cinco_filtros_es_la_lista_completa(client, db_session, base_datos, tipo):
    """Con cualquier combinación de tipo, período y proveedor: ninguna fila se pierde y
    ninguna sale en dos estados a la vez."""
    h, f, terceros = _montar(client, db_session, base_datos)
    proveedores = (None, str(terceros["henri"]), str(terceros["lucia"]), str(terceros["alex"]))
    revisadas = 0
    for (desde, hasta), prov in itertools.product(COMBOS_PERIODO, proveedores):
        base = dict(tipo=tipo, desde=desde, hasta=hasta, proveedor_id=prov)
        todas = {x["id"]: x for x in _items(client, h, **base)}
        vistas: dict[str, str] = {}
        for e in ESTADOS:
            for x in _items(client, h, estado=e, **base):
                assert x["id"] not in vistas, (
                    f"{base} la fila {x['observaciones']} sale en {vistas[x['id']]} y en {e}")
                vistas[x["id"]] = e
                assert x["estado_visible"] in CHIP_DEL_FILTRO[e], (
                    f"{base} filtro {e} trae una fila cuyo chip dice {x['estado_visible']}")
        assert set(vistas) == set(todas), (
            f"{base} se pierden: {[todas[i]['observaciones'] for i in set(todas) - set(vistas)]}")
        revisadas += 1
    print(f"\n  tipo={tipo}: {revisadas} combinaciones, cero filas perdidas o repetidas")


def test_tarjetas_del_listado_contra_el_tablero_y_el_balance(client, db_session, base_datos):
    """Reproduce `cargarResumen` (liquidacion-list.page.ts:357-393) sobre las filas
    existentes y lo compara con el tablero y el balance, que el dueño mira al lado."""
    h, f, _ = _montar(client, db_session, base_datos)
    todas = _items(client, h)
    por = {e: _items(client, h, estado=e, page_size=200) for e in ("borrador", "aprobada", "parcial", "pagada")}

    aprobadas = len(por["aprobada"])
    saldo_aprobadas = sum(max(Decimal(0), Decimal(x["saldo"])) for x in por["aprobada"])
    saldo_parciales = sum(max(Decimal(0), Decimal(x["saldo"])) for x in por["parcial"])
    pagadas = len(por["pagada"])
    deben = [x for e in por for x in por[e]
             if Decimal(x["le_queda_debiendo"]) > 0 and not x["deuda_trasladada_a_id"]]
    le_deben = sum(Decimal(x["le_queda_debiendo"]) for x in deben)

    # Lo que el chip dice, contado sobre la lista completa.
    chips = [x["estado_visible"] for x in todas]
    assert aprobadas == chips.count("aprobada")
    assert pagadas == chips.count("pagada") + chips.count(PD)
    assert len(por["parcial"]) == chips.count("parcial")
    # Ninguna fila con chip "pagada · quedó debiendo" suma en "Aprobadas por pagar".
    assert all(x["estado_visible"] == "aprobada" for x in por["aprobada"])

    tablero = client.get(f"{V}/reportes/dashboard", headers=h)
    assert tablero.status_code == 200, tablero.text
    tablero = tablero.json()
    balance = client.get(f"{V}/contabilidad/balance", headers=h)
    assert balance.status_code == 200, balance.text
    balance = balance.json()
    print(f"\n  tarjetas: aprobadas={aprobadas} ${saldo_aprobadas} · parciales ${saldo_parciales}"
          f" · pagadas={pagadas} · le quedaron debiendo ${le_deben} en {len(deben)}")
    print(f"  tablero: por pagar ${tablero['liquidaciones_por_pagar']} · le deben "
          f"${tablero['terceros_le_quedan_debiendo']}")
    print(f"  balance: por pagar ${balance['liquidaciones_por_pagar']} · le deben "
          f"${balance['terceros_le_quedan_debiendo']}")

    # LO QUE LE DEBEN: la tarjeta, el tablero y el balance dicen la misma cifra.
    assert le_deben == Decimal(tablero["terceros_le_quedan_debiendo"])
    assert le_deben == Decimal(balance["terceros_le_quedan_debiendo"])
    # POR PAGAR: el tablero incluye los borradores con saldo positivo; las tarjetas de
    # la lista no (borr_normal $300.000 + tr_borr $80.000). Se mide la diferencia exacta.
    borradores_positivos = sum(max(Decimal(0), Decimal(x["saldo"])) for x in por["borrador"])
    assert Decimal(tablero["liquidaciones_por_pagar"]) == (
        saldo_aprobadas + saldo_parciales + borradores_positivos)
    assert Decimal(balance["liquidaciones_por_pagar"]) == Decimal(tablero["liquidaciones_por_pagar"])


def test_la_tarjeta_de_deudas_se_pierde_una_deuda_vieja_pasadas_200_pagadas(
        client, db_session, base_datos):
    """La tarjeta "Le quedaron debiendo a la quesera" suma las FILAS (no el total) de las
    cuatro consultas de 200. Desde el cambio, una aprobada que quedó debiendo viaja a la
    consulta de 'pagada', que es la que crece sin parar con el tiempo."""
    h = auth_headers(client, "admin.a")
    emp = base_datos["empresa_a"].id
    r = client.post(f"{V}/proveedores", json={
        "nombre": "Se fue", "vereda": "X", "precio_litro": "2000"}, headers=h)
    se_fue = uuid.UUID(r.json()["id"])
    r = client.post(f"{V}/proveedores", json={
        "nombre": "Constante", "vereda": "X", "precio_litro": "2000"}, headers=h)
    constante = uuid.UUID(r.json()["id"])
    # Un productor que quedó debiendo $120.000 y NO volvió a entregar leche: la deuda no la
    # cobra nadie y es justo la que el dueño tiene que ver en la tarjeta.
    vieja = _fila(db_session, emp, clave="deuda_vieja", tipo="proveedor", tercero_id=se_fue,
                  periodo=(date(2025, 1, 1), date(2025, 1, 15)), estado="aprobada",
                  valor_total="180000", anticipos="300000")
    # 200 quincenas pagadas normales DESPUÉS de esa.
    inicio = date(2025, 1, 16)
    for i in range(200):
        ini = inicio + timedelta(days=i * 2)
        _fila(db_session, emp, clave=f"p{i}", tipo="proveedor", tercero_id=constante,
              periodo=(ini, ini + timedelta(days=1)), estado="pagada",
              valor_total="100000", pagado="100000")
    db_session.commit()

    r = client.get(API, params={"estado": "pagada", "page_size": 200}, headers=h).json()
    ids_pagada = {x["id"] for x in r["items"]}
    r_apr = client.get(API, params={"estado": "aprobada", "page_size": 200}, headers=h).json()
    ids_aprobada = {x["id"] for x in r_apr["items"]}
    # Lo que hacía el listado ANTES del cambio: `list_paginated(estado='aprobada')` pelado.
    antes, _ = LiquidacionRepository(db_session, emp).list_paginated(
        PageParams(page=1, page_size=200), estado="aprobada")
    tablero = client.get(f"{V}/reportes/dashboard", headers=h).json()
    print(f"\n  estado=pagada: total={r['total']} filas={len(r['items'])} · la deuda vieja "
          f"viene? {str(vieja.id) in ids_pagada} · en aprobada? {str(vieja.id) in ids_aprobada}"
          f" · antes del cambio venía en aprobada? {vieja.id in {x.id for x in antes}}")
    print(f"  tablero le deben: ${tablero['terceros_le_quedan_debiendo']}")
    assert r["total"] == 201
    assert str(vieja.id) not in ids_pagada, "la fila 201 queda fuera de la página de 200"
    assert str(vieja.id) not in ids_aprobada
    assert vieja.id in {x.id for x in antes}, "antes la traía la consulta de aprobadas"
    # => la tarjeta suma $0 y el tablero dice $120.000.
    assert Decimal(tablero["terceros_le_quedan_debiendo"]) == Decimal("120000.00")


def test_el_sql_del_filtro_compilado_para_postgres(client, db_session, base_datos, monkeypatch):
    """Sin Docker no hay Postgres donde correrlo; al menos se mira el SQL que SQLAlchemy le
    mandaría a Postgres (paréntesis del OR y la comparación de Numeric contra 0)."""
    h, f, terceros = _montar(client, db_session, base_datos)
    capturadas = []
    original = db_session.scalars

    def espia(stmt, *a, **k):
        capturadas.append(stmt)
        return original(stmt, *a, **k)

    monkeypatch.setattr(db_session, "scalars", espia)
    r = client.get(API, params={"estado": "pagada", "tipo": "proveedor",
                                "proveedor_id": str(terceros["henri"]), "desde": "2026-07-01"},
                   headers=h)
    assert r.status_code == 200, r.text
    stmt = next(s for s in capturadas if "liquidaciones" in str(s) and "LIMIT" in str(s).upper())
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    where = sql[sql.upper().index("WHERE"):]
    print("\n  " + where.replace("\n", " "))
    assert ("(liquidaciones.estado = 'pagada' OR liquidaciones.estado IN "
            "('aprobada', 'parcial', 'pagada') AND liquidaciones.saldo < 0)") in where
    assert "ORDER BY liquidaciones.periodo_inicio DESC" in where
