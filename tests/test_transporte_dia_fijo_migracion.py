"""LA MIGRACIÓN DEL MODO DE LA TARIFA: agrega la columna y NO mueve un peso.

Se prueba LA MIGRACIÓN DE VERDAD —el mismo `upgrade()` que va a correr `alembic upgrade
head` sobre la base del cliente— y no una copia de su lógica: una copia se queda atrás y
entonces la prueba certifica algo que ya no es lo que se ejecuta. Hace falta porque
pytest corre sobre SQLite con `create_all` y las migraciones no se ejercitan solas.

CÓMO SE ARMA LA BASE VIEJA: se crean las tablas con los modelos de hoy y enseguida se le
BOTAN las tres columnas `modo_transporte`, así que la base queda con la forma exacta que
tenía antes de este cambio. Sobre esa base se corre el `upgrade()` con un contexto de
alembic armado a mano.

LO QUE SE MIDE, y es lo único que de verdad importa: que después de subir la migración
todas las cifras de plata estén IDÉNTICAS —la tarifa general, la tarifa de cada ruta, la
foto del flete de cada recepción, los renglones de los comprobantes y los totales de las
liquidaciones— y que las tres columnas nuevas hayan quedado en 'litro' en TODAS las
filas. 'litro' es lo que esas tarifas y esos renglones significaban desde que existen,
así que después de la migración el sistema calcula la misma plata y los comprobantes ya
firmados siguen diciendo lo mismo.

LAS CIFRAS DE LA BASE VIEJA, escritas a mano acá:

    Alex Agudelo   tarifa general        $   200,00 por litro
                   ruta Napoles          $   242,76 por litro
                   ruta Mira Valle       $   317,50 por litro
    recepciones    16/07 Aurelio  82,00 L  →  flete $ 19.906,32
                   16/07 Marleny 137,45 L  →  flete $ 33.367,36
                   17/07 Gilberto 96,30 L  →  flete $ 30.575,25
                                              -----------
                                              $ 83.848,93
    comprobante    dos renglones            $ 53.273,68 + $30.575,25 = $83.848,93
"""
import importlib.util
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models_registry  # noqa: F401  (registra todas las tablas en Base)
from app.core.database import Base
from app.modules.empresas.models import Empresa
from app.modules.liquidaciones.models import (
    TIPO_TRANSPORTADOR,
    Liquidacion,
    LiquidacionDetalle,
)
from app.modules.proveedores.models import Proveedor
from app.modules.recepcion.models import RecepcionLeche
from app.modules.rutas.models import Ruta
from app.modules.transportadores.models import Transportador, TransportadorRuta

D = Decimal
TABLAS_CON_MODO = ("transportadores", "transportador_rutas", "liquidacion_detalles")
# La cuarta columna que agrega la migración, y va en una sola tabla: POR QUÉ un renglón de
# día fijo vale $0,00. Ver `LiquidacionDetalle.dia_fijo_ya_cobrado`.
TABLA_DEL_YA_COBRADO = "liquidacion_detalles"
COLUMNA_YA_COBRADO = "dia_fijo_ya_cobrado"


def _cargar_migracion():
    ruta = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "a7f2c5b8e1d4_tarifa_de_transporte_por_dia_fijo.py"
    )
    spec = importlib.util.spec_from_file_location("migracion_dia_fijo", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _correr(engine, funcion) -> None:
    """Corre `upgrade`/`downgrade` de la migración con un contexto de alembic de verdad.

    `Operations.context(...)` instala el proxy `alembic.op` que la migración usa, así que
    lo que se ejecuta es el archivo tal cual, sin tocarle una línea.
    """
    with engine.begin() as conn:
        contexto = MigrationContext.configure(conn)
        with Operations.context(contexto):
            funcion()


@pytest.fixture()
def base_vieja():
    """Una base con la forma de ANTES: sin las tres columnas `modo_transporte`.

    Se crean las tablas con los modelos de hoy y se les botan las columnas nuevas, que en
    SQLite es un DROP COLUMN normal. Lo que queda es la forma exacta que la base del
    cliente tiene hoy, con datos de plata dentro.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    sesion = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    ahora = datetime.now(timezone.utc)
    empresa = Empresa(nombre="Quesera Vieja", nit="900V")
    sesion.add(empresa)
    sesion.flush()
    napoles = Ruta(empresa_id=empresa.id, nombre="Napoles", municipio="Granada")
    mira_valle = Ruta(empresa_id=empresa.id, nombre="Mira Valle", municipio="Granada")
    alex = Transportador(
        empresa_id=empresa.id, nombre="Alex Agudelo", valor_transporte=D("200")
    )
    sesion.add_all([napoles, mira_valle, alex])
    sesion.flush()
    sesion.add_all([
        TransportadorRuta(
            transportador_id=alex.id, ruta_id=napoles.id, valor_transporte=D("242.76")
        ),
        TransportadorRuta(
            transportador_id=alex.id, ruta_id=mira_valle.id, valor_transporte=D("317.50")
        ),
    ])
    proveedores = {}
    for nombre, ruta in (("Aurelio", napoles), ("Marleny", napoles), ("Gilberto", mira_valle)):
        proveedor = Proveedor(
            empresa_id=empresa.id, nombre=nombre, precio_litro=D("1800"), ruta_id=ruta.id
        )
        sesion.add(proveedor)
        proveedores[nombre] = proveedor
    sesion.flush()

    flete = Liquidacion(
        empresa_id=empresa.id, tipo=TIPO_TRANSPORTADOR, transportador_id=alex.id,
        periodo_inicio=date(2026, 7, 16), periodo_fin=date(2026, 7, 31),
        total_litros=D("315.75"), valor_transporte=D("83848.93"),
        valor_total=D("83848.93"), saldo=D("83848.93"), estado="pagada",
        pagado=D("83848.93"),
    )
    sesion.add(flete)
    sesion.flush()
    sesion.add_all([
        LiquidacionDetalle(
            liquidacion_id=flete.id, fecha=date(2026, 7, 16), ruta_id=napoles.id,
            litros=D("219.45"), precio_litro=D("242.76"), valor=D("53273.68"),
        ),
        LiquidacionDetalle(
            liquidacion_id=flete.id, fecha=date(2026, 7, 17), ruta_id=mira_valle.id,
            litros=D("96.30"), precio_litro=D("317.50"), valor=D("30575.25"),
        ),
    ])
    for dia, quien, litros_, flete_dia in (
        (date(2026, 7, 16), "Aurelio", D("82.00"), D("19906.32")),
        (date(2026, 7, 16), "Marleny", D("137.45"), D("33367.36")),
        (date(2026, 7, 17), "Gilberto", D("96.30"), D("30575.25")),
    ):
        proveedor = proveedores[quien]
        sesion.add(RecepcionLeche(
            empresa_id=empresa.id, fecha=dia, proveedor_id=proveedor.id,
            transportador_id=alex.id, ruta_id=proveedor.ruta_id,
            cantidad_litros=litros_, precio_litro=D("1800"),
            valor_bruto=litros_ * D("1800"), valor_transporte=flete_dia,
            valor_neto=litros_ * D("1800"), liquidacion_transporte_id=flete.id,
            created_at=ahora, updated_at=ahora,
        ))
    sesion.flush()
    sesion.commit()

    # Y AHORA SE LE DA LA FORMA VIEJA: fuera las cuatro columnas nuevas.
    with engine.begin() as conn:
        for tabla in TABLAS_CON_MODO:
            conn.execute(text(f"ALTER TABLE {tabla} DROP COLUMN modo_transporte"))
        conn.execute(
            text(f"ALTER TABLE {TABLA_DEL_YA_COBRADO} DROP COLUMN {COLUMNA_YA_COBRADO}")
        )

    try:
        yield {"engine": engine, "sesion": sesion, "alex": alex, "flete": flete}
    finally:
        sesion.close()
        Base.metadata.drop_all(bind=engine)


def _columnas(engine, tabla) -> set[str]:
    with engine.connect() as conn:
        return {fila[1] for fila in conn.execute(text(f"PRAGMA table_info({tabla})"))}


# ---------------------------------------------------------------------------
# 1. El upgrade de verdad: agrega la columna en 'litro' y no mueve un peso
# ---------------------------------------------------------------------------
def test_el_upgrade_agrega_el_modo_en_litro_y_no_mueve_ninguna_cifra(base_vieja):
    """El pre-vuelo y el post-vuelo, con las cifras del encabezado.

    Se mide la base ANTES (con la forma vieja), se corre el `upgrade()` de verdad, y se
    comprueba que:

      · las tres columnas existan y estén en 'litro' en TODAS las filas;
      · ninguna cifra de plata se haya movido: la tarifa general ($200), las dos tarifas
        por ruta ($242,76 y $317,50), las tres fotos del flete ($19.906,32 + $33.367,36 +
        $30.575,25 = $83.848,93), los dos renglones del comprobante ($53.273,68 +
        $30.575,25) y el total de la liquidación ($83.848,93).

    Lo del comprobante importa doble porque ESTÁ PAGADO: si la migración le moviera un
    peso, le estaría cambiando la cifra a un papel que el conductor ya firmó y contra el
    que ya se le entregó la plata.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]

    for tabla in TABLAS_CON_MODO:
        assert "modo_transporte" not in _columnas(engine, tabla), (
            f"la base de prueba tenia que empezar SIN la columna en {tabla}"
        )
    assert COLUMNA_YA_COBRADO not in _columnas(engine, TABLA_DEL_YA_COBRADO)

    with engine.connect() as conn:
        antes = migracion.medir(conn)

    print("\n===== 1. EL PRE-VUELO (la base como esta hoy) =====")
    for tabla, medidas in antes.items():
        print(f"  {tabla:<22}" + "  ".join(f"{k}={v}" for k, v in medidas.items()))

    assert antes["transportadores"]["valor_transporte"] == D("200")
    assert antes["transportador_rutas"]["valor_transporte"] == D("242.76") + D("317.50")
    assert antes["recepciones_leche"]["valor_transporte"] == D("83848.93")
    assert antes["liquidacion_detalles"]["valor"] == D("83848.93")
    assert antes["liquidaciones"]["valor_total"] == D("83848.93")

    _correr(engine, migracion.upgrade)

    with engine.connect() as conn:
        despues = migracion.medir(conn)
        revisadas = migracion.exigir_todo_por_litro(conn)
        # Y la cuarta columna: ningún renglón viejo puede haber quedado marcado como "ese
        # día ya se cobró en otro comprobante". El día fijo no existía.
        sin_cobrar = migracion.exigir_ningun_dia_fijo_ya_cobrado(conn)

    print("\n===== 1b. EL POST-VUELO (despues del upgrade) =====")
    for tabla, medidas in despues.items():
        print(f"  {tabla:<22}" + "  ".join(f"{k}={v}" for k, v in medidas.items()))
    print(f"  filas revisadas y todas en 'litro': {revisadas}")
    print(f"  renglones revisados y ninguno 'ya cobrado': {sin_cobrar}")
    assert sin_cobrar == 2

    # NINGUNA CIFRA SE MOVIÓ: el propio guardia de la migración lo dice, y además se
    # comparan los diccionarios completos.
    migracion.exigir_que_nada_se_movio(antes, despues)
    assert despues == antes, "la migracion movio una cifra"

    for tabla in TABLAS_CON_MODO:
        assert "modo_transporte" in _columnas(engine, tabla)
    assert COLUMNA_YA_COBRADO in _columnas(engine, TABLA_DEL_YA_COBRADO)
    assert revisadas == {
        "transportadores": 1, "transportador_rutas": 2, "liquidacion_detalles": 2
    }

    # Y las filas de verdad quedaron en 'litro', leídas una por una.
    with engine.connect() as conn:
        for tabla in TABLAS_CON_MODO:
            modos = [f[0] for f in conn.execute(text(f"SELECT modo_transporte FROM {tabla}"))]
            print(f"  {tabla:<22}{modos}")
            assert modos and set(modos) == {"litro"}


def test_el_upgrade_es_idempotente_en_lo_que_importa_y_deja_la_base_usable(base_vieja):
    """Después del upgrade, la lectura por el ORM ve el modo y el sistema sigue calculando.

    Es la comprobación de que la columna quedó bien puesta y no solo presente: el ORM la
    lee sin nulos, `tarifa_de_transporte` devuelve las tarifas de siempre EN MODO LITRO, y
    los renglones del comprobante pagado siguen siendo por litro y siguen cuadrando
    (219,45 × $242,76 = $53.273,68).
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]
    _correr(engine, migracion.upgrade)

    from app.modules.transportadores.tarifas import (
        MODO_POR_LITRO,
        tarifa_de_transporte,
        valor_del_grupo,
    )

    sesion = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        alex = sesion.scalars(select(Transportador)).one()
        print("\n===== 2. LA BASE SIGUE USABLE, Y TODO ES POR LITRO =====")
        print(f"  tarifa general: ${D(alex.valor_transporte)} [{alex.modo_transporte}]")
        assert alex.modo_transporte == MODO_POR_LITRO
        for fila in alex.rutas:
            tarifa = tarifa_de_transporte(alex, fila.ruta_id)
            print(f"  {fila.nombre:<12} ${tarifa.valor} [{tarifa.modo}]")
            assert tarifa.modo == MODO_POR_LITRO
            assert not tarifa.es_dia_fijo
        # La cuenta de siempre: 219,45 L en Nápoles a $242,76.
        napoles = [f for f in alex.rutas if f.nombre == "Napoles"][0]
        cuenta = valor_del_grupo(tarifa_de_transporte(alex, napoles.ruta_id), D("219.45"))
        print(f"  219,45 L en Napoles: ${cuenta}")
        assert cuenta == D("53273.68")

        renglones = sesion.scalars(select(LiquidacionDetalle)).all()
        for detalle in renglones:
            assert detalle.modo_transporte == MODO_POR_LITRO
            assert not detalle.es_dia_fijo
            assert detalle.dia_fijo_ya_cobrado is False
        assert sum((D(d.valor) for d in renglones), D(0)) == D("83848.93")
    finally:
        sesion.close()


# ---------------------------------------------------------------------------
# 2. El guardia: si algo se movió, revienta con un mensaje entendible
# ---------------------------------------------------------------------------
def test_el_post_vuelo_revienta_con_un_mensaje_entendible_si_algo_se_movio():
    """El pre-vuelo contra el post-vuelo, con una cifra cambiada a mano.

    Lo que se revisa es lo que el dueño (o quien mire el log del deploy) va a leer: qué
    tabla, qué columna, cuánto decía antes y cuánto dice ahora. Un "assertion failed"
    pelado no sirve para decidir si hay que devolver la base.
    """
    migracion = _cargar_migracion()
    antes = {
        "recepciones_leche": {"filas": D(3), "valor_transporte": D("83848.93")},
        "liquidaciones": {"filas": D(1), "valor_total": D("83848.93")},
    }
    # Un centavo. Un centavo es un defecto.
    despues = {
        "recepciones_leche": {"filas": D(3), "valor_transporte": D("83848.92")},
        "liquidaciones": {"filas": D(1), "valor_total": D("83848.93")},
    }

    print("\n===== 3. EL GUARDIA DEL POST-VUELO =====")
    migracion.exigir_que_nada_se_movio(antes, antes)  # iguales: no pasa nada
    print("  con las dos mediciones iguales no dice nada")
    with pytest.raises(RuntimeError) as error:
        migracion.exigir_que_nada_se_movio(antes, despues)
    mensaje = str(error.value)
    print("  con un centavo de diferencia:")
    for linea in mensaje.splitlines():
        print(f"    {linea}")
    assert "recepciones_leche" in mensaje
    assert "valor_transporte" in mensaje
    assert "83848.93" in mensaje and "83848.92" in mensaje
    assert "no se aplicó nada" in mensaje.lower()

    # Y una fila de menos también lo delata.
    with pytest.raises(RuntimeError) as error:
        migracion.exigir_que_nada_se_movio(
            antes, {**antes, "recepciones_leche": {"filas": D(2), "valor_transporte": D("83848.93")}}
        )
    assert "filas" in str(error.value)


def test_el_post_vuelo_revienta_si_una_fila_no_quedo_por_litro(base_vieja):
    """Una fila en 'dia_fijo' recién nacida es una tarifa cobrando de otra forma sin permiso.

    En una ruta a $242,76 eso pasaría de cobrar $242,76 EL LITRO a cobrar $242,76 EL DÍA:
    el transportador trabajando casi gratis. Es lo único que la migración de verdad podría
    dañar, así que se revisa explícitamente.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]
    _correr(engine, migracion.upgrade)

    print("\n===== 3b. UNA FILA QUE NO QUEDO EN 'litro' =====")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE transportador_rutas SET modo_transporte = 'dia_fijo' "
                 "WHERE valor_transporte = 242.76")
        )
    with engine.connect() as conn:
        with pytest.raises(RuntimeError) as error:
            migracion.exigir_todo_por_litro(conn)
    mensaje = str(error.value)
    print(f"  {mensaje[:200]}")
    assert "transportador_rutas" in mensaje
    assert "1 de las 2 filas" in mensaje

    # Y UN NULO NO SE PUEDE NI PLANTAR, que es mejor todavía: la columna quedó NOT NULL,
    # así que la base misma rechaza una tarifa sin modo. Una tarifa sin modo son $150.000
    # que se pueden leer como el litro, y el guardia del post-vuelo la buscaría igual (por
    # si un motor no aplicara el server_default), pero acá no hay por dónde meterla.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as reventon:
        with engine.begin() as conn:
            conn.execute(text("UPDATE transportador_rutas SET modo_transporte = NULL"))
    print(f"  y un nulo no se puede ni plantar: {str(reventon.value).splitlines()[0][:90]}")
    assert "NOT NULL" in str(reventon.value)


def test_el_post_vuelo_revienta_si_un_renglon_viejo_quedo_marcado_ya_cobrado(base_vieja):
    """Un «Ya cobrado» recién nacido le afirma al conductor un pago que nunca ocurrió.

    Es lo mismo que el guardia de arriba pero sobre la cuarta columna: ninguno de los
    renglones que existen hoy se emitió por un día ya cobrado en otro comprobante —el día
    fijo no existía— así que un TRUE ahí solo puede venir de que algo salió mal, y lo que
    produce es un papel que le dice al conductor que ese día ya se le pagó.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]
    _correr(engine, migracion.upgrade)

    print("\n===== 3c. UN RENGLON VIEJO MARCADO COMO 'YA COBRADO' =====")
    with engine.connect() as conn:
        # Recién subida la migración, ninguno lo está.
        assert migracion.exigir_ningun_dia_fijo_ya_cobrado(conn) == 2
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {TABLA_DEL_YA_COBRADO} SET {COLUMNA_YA_COBRADO} = 1 "
                 "WHERE valor = 30575.25")
        )
    with engine.connect() as conn:
        with pytest.raises(RuntimeError) as error:
            migracion.exigir_ningun_dia_fijo_ya_cobrado(conn)
    mensaje = str(error.value)
    print(f"  {mensaje[:220]}")
    assert TABLA_DEL_YA_COBRADO in mensaje
    assert "1 de las 2 filas" in mensaje
    assert "Ya cobrado" in mensaje
    assert "no se aplicó nada" in mensaje.lower()


# ---------------------------------------------------------------------------
# 3. El downgrade: apaga los fijos en vez de cobrarlos por litro
# ---------------------------------------------------------------------------
def test_el_downgrade_apaga_las_tarifas_de_dia_fijo_en_vez_de_cobrarlas_por_litro(base_vieja):
    """Bajar la migración deja en CERO las tarifas que estaban en día fijo.

    LA CUENTA DE POR QUÉ, y es la que decide: si al bajar se dejara la cifra, una tarifa de
    $150.000 POR DÍA se volvería $150.000 POR LITRO. En un día de 300 litros eso son
    $45.000.000 de flete que la quesera le pagaría a un conductor. Con la tarifa en cero el
    comprobante le sale en $0 y el sistema YA avisa que hay que ponerle la tarifa. Un aviso
    en la pantalla contra $45 millones no es una decisión difícil.

    Y las tarifas que YA eran por litro no se tocan: $242,76 sigue siendo $242,76.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]
    _correr(engine, migracion.upgrade)

    # El dueño pone la ruta Mira Valle en día fijo, a $150.000.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE transportador_rutas SET modo_transporte='dia_fijo', "
                 "valor_transporte=150000 WHERE valor_transporte = 317.5")
        )

    print("\n===== 4. EL DOWNGRADE =====")
    with engine.connect() as conn:
        antes = [
            (f[0], str(D(str(f[1]))), f[2])
            for f in conn.execute(
                text("SELECT ruta_id, valor_transporte, modo_transporte "
                     "FROM transportador_rutas ORDER BY valor_transporte")
            )
        ]
    print(f"  antes de bajar: {[(v, m) for _, v, m in antes]}")

    _correr(engine, migracion.downgrade)

    with engine.connect() as conn:
        despues = sorted(
            str(D(str(f[0]))) for f in
            conn.execute(text("SELECT valor_transporte FROM transportador_rutas"))
        )
    print(f"  despues de bajar: {despues}")
    print(f"  (el $150.000 del dia fijo quedo en 0; el $242,76 por litro no se toco)")

    assert "242.76" in despues, "una tarifa que YA era por litro no se toca"
    assert any(D(v) == 0 for v in despues), "la de dia fijo tenia que quedar en cero"
    assert not any(D(v) == D("150000") for v in despues), (
        "$150.000 el dia NO puede quedar como $150.000 el litro"
    )

    # Y las columnas se fueron, o sea que la base volvió a la forma vieja.
    for tabla in TABLAS_CON_MODO:
        assert "modo_transporte" not in _columnas(engine, tabla)
    assert COLUMNA_YA_COBRADO not in _columnas(engine, TABLA_DEL_YA_COBRADO)

    # Los renglones del comprobante PAGADO conservan su plata: lo que se pierde al bajar
    # es la etiqueta del modo, nunca la cifra que se le pagó a alguien.
    with engine.connect() as conn:
        total = sum(
            (D(str(f[0])) for f in conn.execute(text("SELECT valor FROM liquidacion_detalles"))),
            D(0),
        )
    print(f"  los renglones del comprobante pagado siguen sumando ${total}")
    assert total == D("83848.93")


def test_subir_bajar_y_volver_a_subir_deja_todo_por_litro_otra_vez(base_vieja):
    """La vuelta completa: upgrade → downgrade → upgrade. Sin tarifas de día fijo de por
    medio no se pierde ni un peso, y la base termina exactamente como en la primera vuelta.

    Es la prueba de que el downgrade no deja basura que le impida al upgrade volver a
    correr, que es lo que convierte un rollback en una base trabada.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]

    with engine.connect() as conn:
        al_principio = migracion.medir(conn)
    _correr(engine, migracion.upgrade)
    _correr(engine, migracion.downgrade)
    _correr(engine, migracion.upgrade)
    with engine.connect() as conn:
        al_final = migracion.medir(conn)
        migracion.exigir_todo_por_litro(conn)
        migracion.exigir_ningun_dia_fijo_ya_cobrado(conn)

    print("\n===== 5. UPGRADE -> DOWNGRADE -> UPGRADE =====")
    for tabla in al_principio:
        print(f"  {tabla:<22}{al_principio[tabla]}  ->  {al_final[tabla]}")
    migracion.exigir_que_nada_se_movio(al_principio, al_final)
    assert al_final == al_principio
