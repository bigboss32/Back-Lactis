"""UN ANTICIPO NO SE DESCUENTA DOS VECES, AUNQUE DOS PETICIONES LO LEAN A LA VEZ.

Quien COBRA anticipos —Generar una quincena, o Aprobar/Recalcular un borrador que barre los
anticipos que se registraron después— hace tres cosas seguidas: lee los anticipos sueltos
(`pendientes_de`: `liquidacion_id` en nulo), los marca con el id de la quincena y los resta
en el total. Sin candado, dos peticiones a la vez leían el MISMO anticipo como suelto y lo
restaban las dos. Medido en un Postgres de verdad (`herramientas/carrera_de_candados.py`):
Aprobar julio mientras otra persona generaba agosto, con $500.000 sin descontar → julio con
$500.000 de anticipos y agosto con otros $500.000, $1.000.000 restados por $500.000
entregados. La marca queda apuntando a la última que escribió, y de la otra no queda rastro.

El arreglo es `FOR UPDATE` en esa lectura (`para_marcar=True`), igual que la deuda trasladada
(`deudas_sin_cobrar`): la segunda petición espera, y al soltarse Postgres vuelve a evaluar el
WHERE y el anticipo ya no sale.

SQLite descarta el FOR UPDATE en silencio, así que la carrera de verdad no se puede provocar
aquí. Lo que SÍ se fija, y sin lo cual el candado no sirve o no corre:

  · la consulta que se manda a Postgres trae `FOR UPDATE`, un orden fijo (para que dos
    peticiones que piden anticipos que se traslapan no se abracen) y NINGÚN JOIN. Esto último
    importa: `proveedor`, `transportador` y `empleado` son lazy="joined" y Postgres RECHAZA un
    `SELECT ... FOR UPDATE` con un LEFT JOIN (0A000); SQLite no lo sabe, así que sin esta
    prueba el error aparecería en producción y no en la suite;
  · quien marca pide el candado y quien solo mira NO lo pide (la pre-liquidación no debe frenar
    a nadie por pintar una cifra);
  · lo que otra persona cambió mientras esta esperaba el turno se relee (`populate_existing`).
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.dialects import postgresql

from app.modules.liquidaciones.models import TIPO_PROVEEDOR, TIPO_TRANSPORTADOR, Anticipo
from app.modules.liquidaciones.repository import AnticipoRepository, LiquidacionRepository
from tests.conftest import auth_headers
from tests.test_liquidacion_anticipos_pendientes import (
    API,
    FIN,
    INICIO,
    D,
    _anticipo,
    _generar,
    _henri_con_sus_644_litros,
    _proveedor,
)


def _como_postgres(consulta) -> str:
    """La consulta tal como se la mandaría a Postgres (el motor de producción)."""
    return str(consulta.compile(dialect=postgresql.dialect()))


# ============================================================ la consulta que se manda
def test_la_consulta_para_marcar_pide_for_update_con_orden_fijo_y_sin_joins(client, base_datos, db_session):
    """Sin `FOR UPDATE` no hay candado; sin `ORDER BY` dos peticiones pueden bloquear los mismos
    anticipos en orden distinto y abrazarse; con un JOIN, Postgres rechaza la consulta entera."""
    repo = AnticipoRepository(db_session, base_datos["empresa_a"].id)
    condicion = Anticipo.proveedor_id == uuid.uuid4()

    sql = _como_postgres(repo._consulta_de_sueltos(condicion, date(2026, 7, 31), True))

    assert "FOR UPDATE" in sql
    assert "ORDER BY anticipos.fecha, anticipos.id" in sql
    assert "JOIN" not in sql, f"un FOR UPDATE con JOIN lo rechaza Postgres (0A000):\n{sql}"
    assert "anticipos.liquidacion_id IS NULL" in sql


def test_sin_los_lazyload_la_consulta_si_traeria_joins_y_postgres_la_rechazaria(client, base_datos, db_session):
    """El CONTROL de la prueba de arriba: la misma consulta sin los `lazyload` sí lleva LEFT JOIN.
    Si algún día esto dejara de ser cierto (las relaciones pasan a ser lazy), la prueba de arriba
    ya no estaría midiendo nada y habría que revisarla."""
    repo = AnticipoRepository(db_session, base_datos["empresa_a"].id)

    sin_lazyload = repo.base_query().where(Anticipo.proveedor_id == uuid.uuid4()).with_for_update()

    assert "LEFT OUTER JOIN" in _como_postgres(sin_lazyload)


def test_la_consulta_para_marcar_relee_las_filas_que_ya_estaban_en_la_sesion(client, base_datos, db_session):
    """`populate_existing` va en la consulta: si no, una fila que ya estaba en la sesión se queda con
    lo que traía y no con lo que otra persona dejó mientras esta esperaba el turno."""
    repo = AnticipoRepository(db_session, base_datos["empresa_a"].id)

    consulta = repo._consulta_de_sueltos(Anticipo.proveedor_id == uuid.uuid4(), date(2026, 7, 31), True)

    assert consulta.get_execution_options().get("populate_existing") is True


@pytest.mark.parametrize("campo", ["proveedor_id", "transportador_id"])
def test_la_consulta_para_mirar_no_bloquea_a_nadie(client, base_datos, db_session, campo):
    """La pre-liquidación y el aviso de la pantalla solo LEEN: pintar una cifra no debe frenar a
    quien está generando o aprobando."""
    repo = AnticipoRepository(db_session, base_datos["empresa_a"].id)

    sql = _como_postgres(
        repo._consulta_de_sueltos(getattr(Anticipo, campo) == uuid.uuid4(), date(2026, 7, 31), False)
    )

    assert "FOR UPDATE" not in sql


# ============================================================ quién pide el candado y quién no
@pytest.fixture
def pedidos(monkeypatch):
    """Anota con qué `para_marcar` se llama cada lectura de anticipos sueltos, y deja pasar la llamada."""
    anotados: list[tuple[str, bool]] = []
    de_proveedor = AnticipoRepository.pendientes_de
    de_transportador = AnticipoRepository.pendientes_transportador

    def proveedor(self, tercero_id, hasta, **k):
        anotados.append(("proveedor", k.get("para_marcar", False)))
        return de_proveedor(self, tercero_id, hasta, **k)

    def transportador(self, tercero_id, hasta, **k):
        anotados.append(("transportador", k.get("para_marcar", False)))
        return de_transportador(self, tercero_id, hasta, **k)

    monkeypatch.setattr(AnticipoRepository, "pendientes_de", proveedor)
    monkeypatch.setattr(AnticipoRepository, "pendientes_transportador", transportador)
    return anotados


def test_generar_la_quincena_de_un_proveedor_lee_sus_anticipos_con_candado(client, base_datos, pedidos):
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)
    _anticipo(client, h, henri, INICIO, 100000)
    pedidos.clear()

    _generar(client, h)

    assert pedidos == [("proveedor", True)]


@pytest.mark.parametrize("accion", ["aprobar", "recalcular"])
def test_aprobar_y_recalcular_un_borrador_leen_los_anticipos_nuevos_con_candado(client, base_datos, pedidos, accion):
    """Los dos barren los anticipos que se registraron después de generar, los marcan y los restan."""
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)
    liq = _generar(client, h)[0]
    _anticipo(client, h, henri, FIN, 200000)
    pedidos.clear()

    r = client.post(f"{API}/{liq['id']}/{accion}", headers=h)

    assert r.status_code == 200, r.text
    assert ("proveedor", True) in pedidos, pedidos
    assert ("proveedor", False) not in pedidos, "una lectura sin candado antes de marcar"
    assert D(r.json()["anticipos"]) == D("200000")


def test_generar_el_flete_de_un_transportador_lee_sus_anticipos_con_candado(client, base_datos, pedidos):
    h = auth_headers(client, "admin.a")
    ruta = client.post("/api/v1/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"}, headers=h).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Stella", "valor_transporte": "100", "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "100"}]},
        headers=h,
    ).json()
    proveedor = _proveedor(client, h, "Libardo")
    r = client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-07-20", "proveedor_id": proveedor["id"], "transportador_id": transportador["id"], "cantidad_litros": "500"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    pedidos.clear()

    _generar(client, h, tipo="ambos")

    assert ("transportador", True) in pedidos, pedidos
    assert ("transportador", False) not in pedidos


def test_la_pre_liquidacion_solo_mira_y_no_bloquea_los_anticipos(client, base_datos, pedidos):
    """Previsualizar no marca nada: lee sin candado, para no frenar a quien genera al mismo tiempo."""
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)
    _anticipo(client, h, henri, INICIO, 100000)
    pedidos.clear()

    r = client.post(
        f"{API}/previsualizar",
        json={"periodo_inicio": INICIO, "periodo_fin": FIN, "tipo": "proveedor", "tercero_id": henri["id"]},
        headers=h,
    )

    assert r.status_code == 200, r.text
    assert D(r.json()[0]["anticipos"]) == D("100000")
    assert pedidos and all(para_marcar is False for _, para_marcar in pedidos), pedidos


# ============================================================ lo que otro cambió mientras esperaba
def test_el_candado_relee_el_valor_que_otra_persona_cambio_mientras_esta_esperaba(client, base_datos, db_session):
    """La FOTO que deja la carrera: el anticipo ya estaba en la sesión con $500.000 y, por debajo,
    otra persona lo rebajó a $300.000 (seguía suelto). Mirar lo deja con la foto vieja; al pedirlo
    con candado se relee, y es lo que se resta."""
    h = auth_headers(client, "admin.a")
    proveedor = _proveedor(client, h, "Beto Valor")
    creado = _anticipo(client, h, proveedor, "2026-07-20", 500000)
    fila = db_session.get(Anticipo, uuid.UUID(creado["id"]))  # la foto: hay que GUARDARLA (referencia débil)
    assert fila.valor == Decimal("500000")
    tabla = Anticipo.__table__
    db_session.execute(update(tabla).where(tabla.c.id == fila.id).values(valor=Decimal("300000")))
    db_session.commit()
    repo = AnticipoRepository(db_session, base_datos["empresa_a"].id)
    prov_id = uuid.UUID(proveedor["id"])

    mirando = repo.pendientes_de(prov_id, date(2026, 7, 31))
    assert [a.valor for a in mirando] == [Decimal("500000")], "la foto no quedó vieja: la prueba no mide nada"

    marcando = repo.pendientes_de(prov_id, date(2026, 7, 31), para_marcar=True)

    assert marcando == [fila]
    assert fila.valor == Decimal("300000"), "se restaría un valor que ya no existe"


# ============================================================ que el candado ESPERE (y no falle ni se salte filas)
def test_el_candado_espera_el_turno_no_falla_ni_se_salta_los_anticipos_bloqueados(client, base_datos, db_session):
    """El `FOR UPDATE` tiene que ser el de siempre, a secas: la segunda petición ESPERA a que la
    primera termine y, al soltarse, Postgres vuelve a evaluar el WHERE y el anticipo ya no sale.

    · `NOWAIT` haría que la segunda REVENTARA al instante con un error de candado (55P03, que
      sale como un 500) en vez de esperar: dos personas que aprueban y generan a la
      vez se estorbarían entre sí sin que ninguna hubiera hecho nada mal;
    · `SKIP LOCKED` es peor: la segunda NO ESPERA y se salta justo los anticipos que la primera
      tiene bloqueados, así que cobra quincena sin ellos y se pagaría de MÁS (o el anticipo
      quedaría sin descontar en ninguna de las dos);
    · `FOR SHARE` deja que dos lean el mismo anticipo a la vez, que es justo la carrera.
    La prueba de arriba solo mira que la palabra `FOR UPDATE` aparezca en alguna parte; con
    `NOWAIT` o `SKIP LOCKED` pegados detrás seguiría apareciendo y el candado no serviría."""
    repo = AnticipoRepository(db_session, base_datos["empresa_a"].id)
    condicion = Anticipo.proveedor_id == uuid.uuid4()

    sql = _como_postgres(repo._consulta_de_sueltos(condicion, date(2026, 7, 31), True))

    assert sql.rstrip().endswith("FOR UPDATE"), f"el candado tiene que ser un FOR UPDATE a secas:\n{sql}"
    assert "NOWAIT" not in sql, "la segunda petición tiene que ESPERAR, no reventar"
    assert "SKIP LOCKED" not in sql, "la segunda petición no se puede saltar lo que la primera tiene bloqueado"


# ============================================================ que cada lectura le pase a la consulta lo que se le pidió
@pytest.fixture
def consultas(monkeypatch):
    """Anota con qué `para_marcar` llega CADA consulta de sueltos a `_consulta_de_sueltos` (lo que de
    verdad decide si se pone el candado), y deja pasar la llamada."""
    anotadas: list[bool] = []
    original = AnticipoRepository._consulta_de_sueltos

    def espia(self, tercero, hasta, para_marcar):
        anotadas.append(para_marcar)
        return original(self, tercero, hasta, para_marcar)

    monkeypatch.setattr(AnticipoRepository, "_consulta_de_sueltos", espia)
    return anotadas


@pytest.mark.parametrize("lectura", ["pendientes_de", "pendientes_transportador"])
@pytest.mark.parametrize(
    "pidiendo, debe_llegar",
    [
        pytest.param({}, False, id="sin_decir_nada_solo_mira"),
        pytest.param({"para_marcar": False}, False, id="mirando"),
        pytest.param({"para_marcar": True}, True, id="marcando"),
    ],
)
def test_cada_lectura_de_sueltos_le_pasa_a_la_consulta_exactamente_lo_que_se_le_pidio(
    client, base_datos, db_session, consultas, lectura, pidiendo, debe_llegar
):
    """La prueba de los `pedidos` (más arriba) anota lo que el SERVICIO le pide al repositorio, pero
    se queda en la puerta: si `pendientes_transportador` se tragara el `para_marcar` y mandara
    siempre `False`, el servicio seguiría pidiendo el candado, la prueba seguiría pasando y el flete
    se descontaría sin candado. Aquí se mira lo que le LLEGA a la consulta, en las dos lecturas.

    Y el valor por omisión importa tanto como el que se pasa: el aviso de la pantalla y la
    pre-liquidación llaman sin decir nada y tienen que MIRAR, no frenar a quien está generando.
    Con `True` por omisión cada vez que alguien abriera la pantalla se pondría un FOR UPDATE."""
    repo = AnticipoRepository(db_session, base_datos["empresa_a"].id)

    getattr(repo, lectura)(uuid.uuid4(), date(2026, 7, 31), **pidiendo)

    assert consultas == [debe_llegar], consultas


# ============================================================ aprobar/recalcular un FLETE también cobra con candado
@pytest.mark.parametrize("accion", ["aprobar", "recalcular"])
def test_aprobar_y_recalcular_un_borrador_de_flete_leen_los_anticipos_nuevos_con_candado(client, base_datos, pedidos, accion):
    """Lo mismo que para el productor (arriba), pero por la rama del TRANSPORTADOR de
    `_aplicar_anticipos_pendientes`: es otra rama del `if`, con su propia llamada. Si a esa llamada
    se le cae el `para_marcar=True`, aprobar el flete de la quincena de julio mientras otra persona
    genera la de agosto le descontaría dos veces la gasolina que se le adelantó al transportador, y
    ninguna de las otras pruebas lo notaría porque todas barren la rama del productor."""
    h = auth_headers(client, "admin.a")
    ruta = client.post("/api/v1/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"}, headers=h).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Stella", "valor_transporte": "100", "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "100"}]},
        headers=h,
    ).json()
    proveedor = _proveedor(client, h, "Libardo")
    r = client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-07-20", "proveedor_id": proveedor["id"], "transportador_id": transportador["id"], "cantidad_litros": "500"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    flete = {x["tipo"]: x for x in _generar(client, h, tipo="ambos")}["transportador"]
    r = client.post(
        "/api/v1/anticipos",
        json={"tipo": "transportador", "transportador_id": transportador["id"], "fecha": FIN, "valor": "20000"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    pedidos.clear()

    r = client.post(f"{API}/{flete['id']}/{accion}", headers=h)

    assert r.status_code == 200, r.text
    assert ("transportador", True) in pedidos, pedidos
    assert ("transportador", False) not in pedidos, "una lectura sin candado antes de marcar"
    assert D(r.json()["anticipos"]) == D("20000")


# ============================================================ lo que NO se barre: otra empresa y lo borrado
def test_el_candado_no_barre_anticipos_de_otra_empresa_ni_los_borrados(client, base_datos, db_session):
    """`_consulta_de_sueltos` parte de `base_query()`, que es lo que la limita a SU empresa y deja
    fuera lo borrado. Quien COBRA marca estas filas con el id de la quincena y las resta del total:
    barrer un anticipo de otra quesera (aunque por un dato mal metido apunte a un proveedor de esta)
    o uno que el dueño ya borró es restarle plata al productor por algo que no le corresponde, y
    además bloquear filas ajenas. La suite no lo delataba: ninguna prueba ponía una fila así."""
    h = auth_headers(client, "admin.a")
    proveedor = _proveedor(client, h, "Beto Ajeno")
    prov_id = uuid.UUID(proveedor["id"])
    vigente = _anticipo(client, h, proveedor, "2026-07-20", 100000)
    borrado = _anticipo(client, h, proveedor, "2026-07-21", 200000)
    assert client.delete(f"/api/v1/anticipos/{borrado['id']}", headers=h).status_code == 204
    ajeno = Anticipo(
        empresa_id=base_datos["empresa_b"].id, tipo="proveedor", proveedor_id=prov_id,
        fecha=date(2026, 7, 22), valor=Decimal("500000"),
    )
    db_session.add(ajeno)
    db_session.commit()
    repo = AnticipoRepository(db_session, base_datos["empresa_a"].id)

    for para_marcar in (False, True):
        sueltos = repo.pendientes_de(prov_id, date(2026, 7, 31), para_marcar=para_marcar)
        assert [str(a.id) for a in sueltos] == [vigente["id"]], (
            f"solo el vigente de esta empresa (para_marcar={para_marcar}): {[(a.valor, a.empresa_id) for a in sueltos]}"
        )

    # El control: la fila de la otra empresa SÍ está suelta y a la vista de SU empresa; sin esto,
    # la prueba de arriba podría estar pasando porque la fila nunca llegó a la base.
    de_la_otra = AnticipoRepository(db_session, base_datos["empresa_b"].id).pendientes_de(prov_id, date(2026, 7, 31))
    assert [a.id for a in de_la_otra] == [ajeno.id]


# ============================================================ el ORDEN de los candados al generar
@pytest.fixture
def eventos(monkeypatch):
    """La secuencia de candados que toma una petición: las deudas (liquidaciones origen) y los anticipos
    sueltos pedidos para marcar. Deja pasar cada llamada."""
    anotados: list[str] = []
    de_deudas = LiquidacionRepository.deudas_sin_cobrar
    de_sueltos = AnticipoRepository._consulta_de_sueltos

    def deudas(self, *a, **k):
        anotados.append("deudas")
        return de_deudas(self, *a, **k)

    def sueltos(self, tercero, hasta, para_marcar):
        anotados.append("anticipos+candado" if para_marcar else "anticipos")
        return de_sueltos(self, tercero, hasta, para_marcar)

    monkeypatch.setattr(LiquidacionRepository, "deudas_sin_cobrar", deudas)
    monkeypatch.setattr(AnticipoRepository, "_consulta_de_sueltos", sueltos)
    return anotados


def test_generar_toma_el_candado_de_las_deudas_antes_que_el_de_los_anticipos(client, base_datos, eventos):
    """Aprobar, Recalcular, Corregir y editar un anticipo piden PRIMERO la liquidación y después los
    anticipos. Generar hacía al revés (los anticipos, y solo después la liquidación de donde sale la
    deuda), y con una quincena anterior que ya DEBE las dos peticiones se esperaban entre sí: abrazo
    mortal de Postgres, medido con `herramientas/carrera_de_candados.py` (una salía con el 409). El
    orden se fija aquí porque SQLite no abraza a nadie."""
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)
    _anticipo(client, h, henri, INICIO, 100000)
    eventos.clear()

    _generar(client, h)

    assert eventos[:2] == ["deudas", "anticipos+candado"], eventos


def test_generar_el_flete_toma_el_candado_de_las_deudas_antes_que_el_de_los_anticipos(client, base_datos, eventos):
    h = auth_headers(client, "admin.a")
    ruta = client.post("/api/v1/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"}, headers=h).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Stella", "valor_transporte": "100", "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "100"}]},
        headers=h,
    ).json()
    proveedor = _proveedor(client, h, "Libardo")
    r = client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-07-20", "proveedor_id": proveedor["id"], "transportador_id": transportador["id"], "cantidad_litros": "500"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    eventos.clear()

    _generar(client, h, tipo="transportador")

    assert eventos[:2] == ["deudas", "anticipos+candado"], eventos


# ============================================================ corregir una pagada también cobra con candado
def _quincena_pagada_con_un_adelanto_olvidado(client, h):
    prov = client.post(
        "/api/v1/proveedores", json={"nombre": "Libardo", "vereda": "El Roble", "precio_litro": "2000"}, headers=h
    ).json()
    client.post(
        "/api/v1/recepciones", json={"fecha": "2026-06-02", "proveedor_id": prov["id"], "cantidad_litros": "250"}, headers=h
    )
    client.post(
        "/api/v1/anticipos",
        json={"tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-06-03", "valor": "100000"},
        headers=h,
    )
    liq = client.post(
        f"{API}/generar", json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"}, headers=h
    ).json()["generadas"][0]
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq['id']}/pagar", headers=h).status_code == 200
    olvidado = client.post(
        "/api/v1/anticipos",
        json={"tipo": "proveedor", "proveedor_id": prov["id"], "fecha": "2026-06-10", "valor": "150000"},
        headers=h,
    ).json()
    return liq, olvidado


def test_corregir_una_pagada_incluyendo_un_adelanto_suelto_lo_lee_con_candado(client, base_datos, pedidos):
    """Corregir una quincena pagada puede INCLUIR un adelanto suelto: lo marca y lo resta. Sin candado,
    generar la quincena siguiente al mismo tiempo lo restaba también (el mismo doble descuento de
    Aprobar/Generar). El orden es el de siempre: primero la liquidación (`_bloquear`), después el
    anticipo."""
    h = auth_headers(client, "admin.a")
    liq, olvidado = _quincena_pagada_con_un_adelanto_olvidado(client, h)
    pedidos.clear()

    r = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "faltaba el adelanto del 10", "anticipos_a_incluir": [olvidado["id"]]},
        headers=h,
    )

    assert r.status_code == 200, r.text
    assert D(r.json()["anticipos"]) == D("250000")
    assert ("proveedor", True) in pedidos, pedidos
    assert ("proveedor", False) not in pedidos, "una lectura sin candado antes de marcar"


def test_la_previsualizacion_de_la_correccion_solo_mira_y_no_bloquea(client, base_datos, pedidos):
    h = auth_headers(client, "admin.a")
    liq, olvidado = _quincena_pagada_con_un_adelanto_olvidado(client, h)
    pedidos.clear()

    r = client.post(
        f"{API}/{liq['id']}/corregir/previsualizar",
        json={"motivo": "faltaba el adelanto del 10", "anticipos_a_incluir": [olvidado["id"]]},
        headers=h,
    )

    assert r.status_code == 200, r.text
    assert pedidos and all(para_marcar is False for _, para_marcar in pedidos), pedidos


def test_corregir_una_pagada_sin_incluir_adelantos_no_bloquea_los_sueltos(client, base_datos, pedidos):
    """Si la corrección no va a incluir ningún adelanto no hay nada que marcar, y bloquear los
    anticipos sueltos del tercero solo estorbaría a quien esté generando."""
    h = auth_headers(client, "admin.a")
    liq, olvidado = _quincena_pagada_con_un_adelanto_olvidado(client, h)
    ids_de_los_dias = client.get(f"{API}/{liq['id']}", headers=h).json()["detalles"]
    assert ids_de_los_dias, "la quincena tenía que traer sus días"
    pedidos.clear()

    r = client.post(
        f"{API}/{liq['id']}/corregir",
        json={"motivo": "solo cambio el precio", "precios": [{"detalle_id": ids_de_los_dias[0]["id"], "precio_litro": "2100"}]},
        headers=h,
    )

    assert r.status_code == 200, r.text
    assert ("proveedor", True) not in pedidos, pedidos


def test_el_candado_de_las_deudas_de_generar_es_el_del_tercero_y_su_tipo_y_desde_el_inicio_del_periodo(
    client, base_datos, monkeypatch
):
    """No basta con que se pida antes: tiene que ser el candado de las liquidaciones de ESE tercero
    (por id, nunca por nombre), del tipo que se está generando (la deuda de un proveedor no se le
    cobra a un transportador) y de las que terminaron antes de que ESTE período empiece: justo las
    mismas filas que `_cobrar_deudas_anteriores` va a marcar un momento después."""
    h = auth_headers(client, "admin.a")
    ruta = client.post("/api/v1/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"}, headers=h).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Stella", "valor_transporte": "100", "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "100"}]},
        headers=h,
    ).json()
    proveedor = _proveedor(client, h, "Libardo")
    r = client.post(
        "/api/v1/recepciones",
        json={"fecha": "2026-07-20", "proveedor_id": proveedor["id"], "transportador_id": transportador["id"], "cantidad_litros": "500"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    pedidas: list[tuple] = []
    original = LiquidacionRepository.deudas_sin_cobrar

    def espia(self, tipo, tercero_id, *, antes_de, excepto=None):
        pedidas.append((tipo, str(tercero_id), antes_de, excepto))
        return original(self, tipo, tercero_id, antes_de=antes_de, excepto=excepto)

    monkeypatch.setattr(LiquidacionRepository, "deudas_sin_cobrar", espia)

    _generar(client, h, tipo="ambos")

    # Las que piden el candado por adelantado no traen `excepto` (la liquidación todavía no existe);
    # las de `_cobrar_deudas_anteriores`, un momento después, sí.
    por_adelantado = [p for p in pedidas if p[3] is None]
    assert (TIPO_PROVEEDOR, proveedor["id"], date(2026, 7, 16), None) in por_adelantado, pedidas
    assert (TIPO_TRANSPORTADOR, transportador["id"], date(2026, 7, 16), None) in por_adelantado, pedidas
    assert len(por_adelantado) == 2, pedidas
