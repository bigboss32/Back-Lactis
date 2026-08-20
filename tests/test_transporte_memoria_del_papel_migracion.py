"""LA MIGRACIÓN DE LA MEMORIA DEL PAPEL: crea la tabla, la llena leyendo los renglones,
y NO mueve un peso.

Se prueba LA MIGRACIÓN DE VERDAD —el mismo `upgrade()` que va a correr `alembic upgrade
head` sobre la base del cliente— y no una copia de su lógica: una copia se queda atrás y
entonces la prueba certifica algo que ya no es lo que se ejecuta. Hace falta porque pytest
corre sobre SQLite con `create_all` y las migraciones no se ejercitan solas.

CÓMO SE ARMA LA BASE VIEJA: se crean las tablas con los modelos de hoy y enseguida se le
BOTA la tabla `liquidacion_rutas`, así que la base queda con la forma exacta que tenía antes
de este cambio. Sobre esa base se corre el `upgrade()` con un contexto de alembic armado a
mano.

LO QUE SE MIDE, y es lo único que de verdad importa:

  · que después de subir la migración todas las cifras de plata estén IDÉNTICAS —las
    tarifas, la foto del flete de cada recepción, los renglones de los comprobantes y los
    totales de las liquidaciones—;
  · y que la memoria escrita diga EXACTAMENTE lo que dicen los renglones de cada
    comprobante, ruta por ruta. Eso es lo que hace que un papel ya firmado siga
    significando lo mismo: la memoria es la que decide con qué tarifa se rehace un día que
    se apaga y se vuelve a prender.

LAS CIFRAS DE LA BASE VIEJA, escritas a mano acá. Son cuatro comprobantes a propósito,
porque cada uno prueba una cosa distinta que la memoria tiene que saber decir:

  1. EL NORMAL, de Alex, PAGADO. Dos rutas, las dos por litro:
        16/07 Napoles     219,45 L × $242,76  =  $ 53.273,68
        17/07 Mira Valle   96,30 L × $317,50  =  $ 30.575,25
                                                 -----------
                                                 $ 83.848,93
     → memoria: Napoles ('litro', $242,76) y Mira Valle ('litro', $317,50).

  2. EL DEL DÍA FIJO, de Beto. Un renglón "día completo" y otro fijo en $0,00 porque ese
     día ya se había cobrado en otro comprobante:
        18/07 Napoles   340,00 L   Día completo  =  $150.000,00
        19/07 Napoles    12,00 L   Ya cobrado    =  $      0,00
                                                    -----------
                                                    $150.000,00
     → memoria: Napoles ('dia_fijo', fijo $150.000). El renglón en cero NO sirve de
       referencia —no dice cuánto cuesta el viaje— y por eso el fijo sale del otro.

  3. EL PARTIDO, de Carlos. Un mismo (día, ruta) con DOS tarifas, que es lo que queda
     cuando se le corrige la tarifa a una ruta después de haberle pagado el flete:
        20/07 Mira Valle   50,00 L × $317,50  =  $ 15.875,00
        20/07 Mira Valle   30,00 L × $300,00  =  $  9.000,00
                                                 -----------
                                                 $ 24.875,00
     → memoria: Mira Valle ('litro', tarifa en NULO). No existe "la tarifa con que se
       emitió", y un nulo dice eso; inventar un promedio sería peor.

  4. EL SIN RUTA, de Dalia. Su recepción quedó sin ruta y cobró la tarifa general:
        21/07 (sin ruta)   40,00 L × $200,00  =  $  8.000,00
     → memoria: una fila con `ruta_id` en NULO y ('litro', $200,00). Sin esa fila, el día
       sin ruta se quedaba con la puerta abierta.

  Y UNA LIQUIDACIÓN DE PROVEEDOR, que NO tiene rutas y NO puede quedar con memoria:
        16/07  120,00 L × $1.800  =  $216.000,00

TOTAL DE PLATA EN RENGLONES DE FLETE: $83.848,93 + $150.000 + $24.875 + $8.000 =
$266.723,93. Es la cifra que la migración no puede mover ni en un centavo.
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
    TIPO_PROVEEDOR,
    TIPO_TRANSPORTADOR,
    Liquidacion,
    LiquidacionDetalle,
    LiquidacionRuta,
)
from app.modules.proveedores.models import Proveedor
from app.modules.recepcion.models import RecepcionLeche
from app.modules.rutas.models import Ruta
from app.modules.transportadores.models import Transportador, TransportadorRuta

D = Decimal
LA_TABLA = "liquidacion_rutas"
TOTAL_DEL_FLETE = D("266723.93")


def _cargar_migracion():
    ruta = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions"
        / "c3f8a1d6b0e5_el_comprobante_guarda_como_cobro_cada_ruta.py"
    )
    spec = importlib.util.spec_from_file_location("migracion_memoria_del_papel", ruta)
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


def _tablas(engine) -> set[str]:
    with engine.connect() as conn:
        return {
            f[0]
            for f in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }


@pytest.fixture()
def base_vieja():
    """Una base con la forma de ANTES: sin la tabla `liquidacion_rutas`.

    Se crean las tablas con los modelos de hoy y se le bota la nueva. Lo que queda es la
    forma exacta que la base del cliente tiene hoy, con plata de verdad dentro y con los
    cuatro comprobantes del encabezado.
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
    sesion.add_all([napoles, mira_valle])
    sesion.flush()

    conductores = {}
    for nombre, general in (
        ("Alex", D("200")), ("Beto", D("200")), ("Carlos", D("200")), ("Dalia", D("200"))
    ):
        quien = Transportador(
            empresa_id=empresa.id, nombre=nombre, valor_transporte=general
        )
        sesion.add(quien)
        conductores[nombre] = quien
    sesion.flush()
    sesion.add_all([
        TransportadorRuta(
            transportador_id=conductores["Alex"].id, ruta_id=napoles.id,
            valor_transporte=D("242.76"),
        ),
        TransportadorRuta(
            transportador_id=conductores["Alex"].id, ruta_id=mira_valle.id,
            valor_transporte=D("317.50"),
        ),
    ])
    productor = Proveedor(
        empresa_id=empresa.id, nombre="Aurelio", precio_litro=D("1800"), ruta_id=napoles.id
    )
    sesion.add(productor)
    sesion.flush()

    def _flete(quien, total, renglones, estado="borrador"):
        liquidacion = Liquidacion(
            empresa_id=empresa.id, tipo=TIPO_TRANSPORTADOR,
            transportador_id=conductores[quien].id,
            periodo_inicio=date(2026, 7, 16), periodo_fin=date(2026, 7, 31),
            valor_transporte=total, valor_total=total, saldo=total, estado=estado,
        )
        sesion.add(liquidacion)
        sesion.flush()
        for fecha, ruta, litros_, precio, valor, modo, ya_cobrado in renglones:
            sesion.add(LiquidacionDetalle(
                liquidacion_id=liquidacion.id, fecha=fecha,
                ruta_id=(ruta.id if ruta is not None else None),
                litros=litros_, precio_litro=precio, valor=valor,
                modo_transporte=modo, dia_fijo_ya_cobrado=ya_cobrado,
            ))
        return liquidacion

    # 1. EL NORMAL, pagado, dos rutas por litro.
    alex = _flete("Alex", D("83848.93"), [
        (date(2026, 7, 16), napoles, D("219.45"), D("242.76"), D("53273.68"), "litro", False),
        (date(2026, 7, 17), mira_valle, D("96.30"), D("317.50"), D("30575.25"), "litro", False),
    ], estado="pagada")
    alex.pagado = D("83848.93")
    alex.saldo = D("0")
    # 2. EL DEL DÍA FIJO, con un renglón "ya cobrado" en $0,00.
    _flete("Beto", D("150000.00"), [
        (date(2026, 7, 18), napoles, D("340.00"), D("0"), D("150000.00"), "dia_fijo", False),
        (date(2026, 7, 19), napoles, D("12.00"), D("0"), D("0.00"), "dia_fijo", True),
    ])
    # 3. EL PARTIDO: el mismo (día, ruta) con dos tarifas.
    _flete("Carlos", D("24875.00"), [
        (date(2026, 7, 20), mira_valle, D("50.00"), D("317.50"), D("15875.00"), "litro", False),
        (date(2026, 7, 20), mira_valle, D("30.00"), D("300.00"), D("9000.00"), "litro", False),
    ])
    # 4. EL SIN RUTA: cobró la tarifa general.
    _flete("Dalia", D("8000.00"), [
        (date(2026, 7, 21), None, D("40.00"), D("200.00"), D("8000.00"), "litro", False),
    ])

    # Y UNA DE PROVEEDOR, que no cobra rutas y no puede quedar con memoria.
    de_proveedor = Liquidacion(
        empresa_id=empresa.id, tipo=TIPO_PROVEEDOR, proveedor_id=productor.id,
        periodo_inicio=date(2026, 7, 16), periodo_fin=date(2026, 7, 31),
        total_litros=D("120.00"), valor_transporte=D("0"), valor_total=D("216000.00"),
        saldo=D("216000.00"), estado="borrador",
    )
    sesion.add(de_proveedor)
    sesion.flush()
    sesion.add(LiquidacionDetalle(
        liquidacion_id=de_proveedor.id, fecha=date(2026, 7, 16), ruta_id=None,
        litros=D("120.00"), precio_litro=D("1800"), valor=D("216000.00"),
    ))

    sesion.add(RecepcionLeche(
        empresa_id=empresa.id, fecha=date(2026, 7, 16), proveedor_id=productor.id,
        transportador_id=conductores["Alex"].id, ruta_id=napoles.id,
        cantidad_litros=D("219.45"), precio_litro=D("1800"),
        valor_bruto=D("219.45") * D("1800"), valor_transporte=D("53273.68"),
        valor_neto=D("219.45") * D("1800"), liquidacion_transporte_id=alex.id,
        created_at=ahora, updated_at=ahora,
    ))
    sesion.flush()
    sesion.commit()

    # Y AHORA SE LE DA LA FORMA VIEJA: fuera la tabla nueva.
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE {LA_TABLA}"))

    try:
        yield {"engine": engine, "sesion": sesion, "napoles": napoles,
               "mira_valle": mira_valle, "alex": alex, "de_proveedor": de_proveedor}
    finally:
        sesion.close()
        Base.metadata.drop_all(bind=engine)


def _memoria(engine) -> dict:
    """La memoria escrita, leída por el ORM y con las claves legibles."""
    sesion = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        return {
            (str(f.liquidacion_id), None if f.ruta_id is None else str(f.ruta_id)): (
                f.modo_transporte,
                None if f.precio_litro is None else D(f.precio_litro),
                None if f.valor_dia_fijo is None else D(f.valor_dia_fijo),
            )
            for f in sesion.scalars(select(LiquidacionRuta)).all()
        }
    finally:
        sesion.close()


# ---------------------------------------------------------------------------
# 1. El upgrade de verdad: crea la tabla, la llena bien y no mueve un peso
# ---------------------------------------------------------------------------
def test_el_upgrade_escribe_la_memoria_de_cada_ruta_y_no_mueve_ninguna_cifra(base_vieja):
    """El pre-vuelo y el post-vuelo, con las cuatro cifras del encabezado.

    Se mide la base ANTES (con la forma vieja), se corre el `upgrade()` de verdad, y se
    comprueba que:

      · ninguna cifra de plata se haya movido: las tarifas, la foto del flete, los
        renglones ($266.723,93 en total) y los totales de las liquidaciones;
      · la memoria diga lo que dicen los renglones de cada comprobante: Napoles por litro a
        $242,76, Mira Valle por litro a $317,50, el día fijo de Beto en $150.000, el
        partido de Carlos con la tarifa en NULO, y el sin ruta de Dalia a $200.

    Lo del comprobante de Alex importa doble porque ESTÁ PAGADO: si la migración le moviera
    un peso, le estaría cambiando la cifra a un papel que el conductor ya firmó y contra el
    que ya se le entregó la plata.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]
    napoles, mira_valle = base_vieja["napoles"], base_vieja["mira_valle"]

    assert LA_TABLA not in _tablas(engine), (
        "la base de prueba tenia que empezar SIN la tabla de la memoria"
    )

    with engine.connect() as conn:
        antes = migracion.medir(conn)

    print("\n===== 1. EL PRE-VUELO (la base como esta hoy) =====")
    for tabla, medidas in antes.items():
        print(f"  {tabla:<22}" + "  ".join(f"{k}={v}" for k, v in medidas.items()))
    assert antes["liquidacion_detalles"]["valor"] == TOTAL_DEL_FLETE + D("216000.00")
    assert antes["recepciones_leche"]["valor_transporte"] == D("53273.68")

    _correr(engine, migracion.upgrade)

    with engine.connect() as conn:
        despues = migracion.medir(conn)
        escritas = migracion.exigir_la_memoria_igual_a_los_renglones(conn)

    print("\n===== 1b. EL POST-VUELO (despues del upgrade) =====")
    for tabla, medidas in despues.items():
        print(f"  {tabla:<22}" + "  ".join(f"{k}={v}" for k, v in medidas.items()))
    print(f"  filas de memoria escritas: {escritas}")

    # NINGUNA CIFRA SE MOVIÓ: el propio guardia de la migración lo dice, y además se
    # comparan los diccionarios completos.
    migracion.exigir_que_nada_se_movio(antes, despues)
    assert despues == antes, "la migracion movio una cifra"
    assert LA_TABLA in _tablas(engine)

    memoria = _memoria(engine)
    print("\n===== 1c. LO QUE QUEDO ESCRITO =====")
    for (liq_id, ruta_id), lo_que_dice in sorted(memoria.items(), key=lambda x: str(x[1])):
        print(f"  comprobante {liq_id[:8]}  ruta {(ruta_id or 'SIN RUTA')[:8]:<8} -> "
              f"{lo_que_dice}")

    # Son CINCO filas: Napoles y Mira Valle de Alex, Napoles de Beto, Mira Valle de
    # Carlos, y la sin ruta de Dalia. La de proveedor NO cuenta.
    assert escritas == 5
    assert len(memoria) == 5

    liq_alex = str(base_vieja["alex"].id)
    assert memoria[(liq_alex, str(napoles.id))] == ("litro", D("242.76"), None)
    assert memoria[(liq_alex, str(mira_valle.id))] == ("litro", D("317.50"), None)

    # El día fijo de Beto: modo 'dia_fijo' y el fijo del renglón que SÍ cobró.
    fijos = [v for v in memoria.values() if v[0] == "dia_fijo"]
    assert fijos == [("dia_fijo", None, D("150000.00"))], fijos

    # El partido de Carlos: por litro y SIN tarifa, porque tiene dos.
    partidos = [
        v for k, v in memoria.items()
        if k[1] == str(mira_valle.id) and k[0] != liq_alex
    ]
    assert partidos == [("litro", None, None)], partidos

    # El sin ruta de Dalia: la fila con `ruta_id` en nulo, a la tarifa general.
    sin_ruta = [v for k, v in memoria.items() if k[1] is None]
    assert sin_ruta == [("litro", D("200.00"), None)], sin_ruta

    # Y LA DE PROVEEDOR NO QUEDO CON MEMORIA: no cobra rutas.
    assert not [k for k in memoria if k[0] == str(base_vieja["de_proveedor"].id)]


def test_despues_del_upgrade_el_sistema_lee_la_memoria_por_el_ORM(base_vieja):
    """La tabla quedó usable: el comprobante llega a su memoria por `rutas_cobradas`.

    Es la comprobación de que la relación del modelo apunta bien, que es lo que el servicio
    usa para decidir con qué tarifa rehace un día que vuelve. Sin esto la tabla podría
    quedar perfecta y el código no verla.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]
    _correr(engine, migracion.upgrade)

    sesion = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        alex = sesion.get(Liquidacion, base_vieja["alex"].id)
        print("\n===== 2. EL COMPROBANTE PAGADO Y SU MEMORIA =====")
        print(f"  total ${D(alex.valor_transporte)}  ({alex.estado})")
        for fila in alex.rutas_cobradas:
            print(f"      {fila.modo_transporte:<9} tarifa {fila.precio_litro}  "
                  f"fijo {fila.valor_dia_fijo}")
        assert len(alex.rutas_cobradas) == 2
        assert {D(f.precio_litro) for f in alex.rutas_cobradas} == {
            D("242.76"), D("317.50")
        }
        assert all(f.modo_transporte == "litro" for f in alex.rutas_cobradas)
        assert all(f.valor_dia_fijo is None for f in alex.rutas_cobradas)
        # Y el papel sigue diciendo lo mismo: 219,45 × $242,76 = $53.273,68.
        renglones = sesion.scalars(
            select(LiquidacionDetalle).where(
                LiquidacionDetalle.liquidacion_id == alex.id
            )
        ).all()
        assert sum((D(d.valor) for d in renglones), D(0)) == D("83848.93")
    finally:
        sesion.close()


# ---------------------------------------------------------------------------
# 2. Los guardias: si algo se movió, o si la memoria miente, revientan
# ---------------------------------------------------------------------------
def test_el_post_vuelo_revienta_con_un_mensaje_entendible_si_algo_se_movio():
    """El pre-vuelo contra el post-vuelo, con una cifra cambiada a mano.

    Lo que se revisa es lo que el dueño (o quien mire el log del deploy) va a leer: qué
    tabla, qué columna, cuánto decía antes y cuánto dice ahora. Un "assertion failed"
    pelado no sirve para decidir si hay que devolver la base.
    """
    migracion = _cargar_migracion()
    antes = {
        "recepciones_leche": {"filas": D(1), "valor_transporte": D("53273.68")},
        "liquidaciones": {"filas": D(5), "valor_total": D("482723.93")},
    }
    # Un centavo. Un centavo es un defecto.
    despues = {
        "recepciones_leche": {"filas": D(1), "valor_transporte": D("53273.67")},
        "liquidaciones": {"filas": D(5), "valor_total": D("482723.93")},
    }

    print("\n===== 3. EL GUARDIA DEL POST-VUELO =====")
    migracion.exigir_que_nada_se_movio(antes, antes)  # iguales: no pasa nada
    print("  con las dos mediciones iguales no dice nada")
    with pytest.raises(RuntimeError) as error:
        migracion.exigir_que_nada_se_movio(antes, despues)
    mensaje = str(error.value)
    for linea in mensaje.splitlines():
        print(f"    {linea}")
    assert "recepciones_leche" in mensaje
    assert "53273.68" in mensaje and "53273.67" in mensaje
    assert "no se aplicó nada" in mensaje.lower()

    # Y una fila de menos también lo delata.
    with pytest.raises(RuntimeError) as error:
        migracion.exigir_que_nada_se_movio(
            antes, {**antes, "liquidaciones": {"filas": D(4), "valor_total": D("482723.93")}}
        )
    assert "filas" in str(error.value)


@pytest.mark.parametrize("daño,que_se_espera", [
    ("borrar_una_fila", "no le quedó fila de memoria"),
    ("cambiarle_el_modo", "la memoria dice"),
    ("cambiarle_la_tarifa", "la memoria dice"),
    ("plantar_una_fila_de_mas", "de una ruta que ese comprobante no cobra"),
])
def test_el_post_vuelo_revienta_si_la_memoria_no_dice_lo_que_dicen_los_renglones(
    base_vieja, daño, que_se_espera
):
    """Las cuatro formas de que la memoria mienta, y las cuatro revientan.

    La memoria es la que decide con qué tarifa se rehace un día que se apaga y se vuelve a
    prender, así que una memoria que no diga lo mismo que los renglones es un papel que va a
    cambiar de cifra solo. En el caso medido la diferencia entre las dos lecturas era de
    $130.093,68 en un solo día ($150.000 contra $19.906,32), y por eso esto se revisa fila
    por fila en vez de contar filas y ya.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]
    _correr(engine, migracion.upgrade)

    print(f"\n===== 4. LA MEMORIA MIENTE: {daño} =====")
    with engine.begin() as conn:
        if daño == "borrar_una_fila":
            conn.execute(text(f"DELETE FROM {LA_TABLA} WHERE precio_litro = 242.76"))
        elif daño == "cambiarle_el_modo":
            conn.execute(text(
                f"UPDATE {LA_TABLA} SET modo_transporte='dia_fijo' "
                "WHERE precio_litro = 242.76"
            ))
        elif daño == "cambiarle_la_tarifa":
            conn.execute(text(
                f"UPDATE {LA_TABLA} SET precio_litro = 100 WHERE precio_litro = 242.76"
            ))
        else:
            # Una fila de una ruta que ese comprobante nunca cobró: se le presta el id de
            # otra fila cambiándole la ruta, que es lo que haría un script mal escrito.
            conn.execute(text(
                f"INSERT INTO {LA_TABLA} "
                "(id, liquidacion_id, ruta_id, modo_transporte, precio_litro, "
                " valor_dia_fijo, created_at, updated_at) "
                f"SELECT lower(hex(randomblob(16))), liquidacion_id, NULL, 'dia_fijo', "
                f"NULL, 150000, created_at, updated_at FROM {LA_TABLA} "
                "WHERE precio_litro = 242.76"
            ))

    with engine.connect() as conn:
        with pytest.raises(RuntimeError) as error:
            migracion.exigir_la_memoria_igual_a_los_renglones(conn)
    mensaje = str(error.value)
    for linea in mensaje.splitlines()[:6]:
        print(f"    {linea}")
    assert que_se_espera in mensaje
    assert "130.093,68" in mensaje
    assert "no se aplicó nada" in mensaje.lower()


def test_el_post_vuelo_revienta_si_una_ruta_quedo_con_dos_filas(base_vieja):
    """Dos filas para la misma ruta: no habría manera de saber cómo cobró.

    El único de la tabla lo impide para las rutas de verdad, pero Postgres deja repetir las
    filas cuyo `ruta_id` es NULL —la de la tarifa general—, así que el guardia lo busca
    igual. Acá se planta a mano el caso del nulo, que es el que la base no puede parar.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]
    _correr(engine, migracion.upgrade)

    print("\n===== 4b. DOS FILAS PARA LA MISMA RUTA (la del nulo) =====")
    with engine.begin() as conn:
        conn.execute(text(
            f"INSERT INTO {LA_TABLA} "
            "(id, liquidacion_id, ruta_id, modo_transporte, precio_litro, valor_dia_fijo, "
            " created_at, updated_at) "
            "SELECT lower(hex(randomblob(16))), liquidacion_id, ruta_id, 'dia_fijo', NULL, "
            f"150000, created_at, updated_at FROM {LA_TABLA} WHERE ruta_id IS NULL"
        ))
    with engine.connect() as conn:
        with pytest.raises(RuntimeError) as error:
            migracion.exigir_la_memoria_igual_a_los_renglones(conn)
    mensaje = str(error.value)
    print(f"    {mensaje[:220]}")
    assert "DOS filas de memoria" in mensaje
    assert "no se aplicó nada" in mensaje.lower()


# ---------------------------------------------------------------------------
# 3. El downgrade: bota la tabla y no toca un peso
# ---------------------------------------------------------------------------
def test_el_downgrade_bota_la_memoria_y_no_mueve_ninguna_cifra(base_vieja):
    """Bajar la migración pierde la memoria —las dos puertas vuelven a abrirse— pero no
    le cuesta un peso a nadie de inmediato.

    Es distinto del downgrade de a7f2c5b8e1d4, que SÍ tiene que apagar tarifas para no
    convertir $150.000 el día en $150.000 el litro. Acá no hay ninguna cifra que
    reinterpretar: la memoria es una nota sobre el papel, no plata. Lo que se pierde es el
    arreglo, y eso se dice en el encabezado de la migración en vez de fingir que no pasa
    nada.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]
    _correr(engine, migracion.upgrade)

    with engine.connect() as conn:
        antes = migracion.medir(conn)
    _correr(engine, migracion.downgrade)
    with engine.connect() as conn:
        despues = migracion.medir(conn)

    print("\n===== 5. EL DOWNGRADE =====")
    for tabla in antes:
        print(f"  {tabla:<22}{antes[tabla]}  ->  {despues[tabla]}")
    migracion.exigir_que_nada_se_movio(antes, despues)
    assert despues == antes
    assert LA_TABLA not in _tablas(engine), "la tabla de la memoria tenia que irse"

    # Los renglones del comprobante PAGADO conservan su plata.
    with engine.connect() as conn:
        total = sum(
            (D(str(f[0])) for f in conn.execute(
                text("SELECT valor FROM liquidacion_detalles d JOIN liquidaciones l "
                     "ON l.id = d.liquidacion_id WHERE l.tipo = 'transportador'")
            )),
            D(0),
        )
    print(f"  los renglones de flete siguen sumando ${total}")
    assert total == TOTAL_DEL_FLETE


def test_subir_bajar_y_volver_a_subir_deja_la_misma_memoria(base_vieja):
    """La vuelta completa: upgrade → downgrade → upgrade. Ni un peso se mueve y la memoria
    queda IDÉNTICA.

    Es la prueba de que el downgrade no deja basura que le impida al upgrade volver a
    correr, que es lo que convierte un rollback en una base trabada. Y de que el backfill es
    idempotente en lo que importa: la memoria se vuelve a deducir de los mismos renglones,
    así que sale igual.
    """
    migracion = _cargar_migracion()
    engine = base_vieja["engine"]

    with engine.connect() as conn:
        al_principio = migracion.medir(conn)
    _correr(engine, migracion.upgrade)
    primera = _memoria(engine)
    _correr(engine, migracion.downgrade)
    _correr(engine, migracion.upgrade)
    segunda = _memoria(engine)
    with engine.connect() as conn:
        al_final = migracion.medir(conn)
        migracion.exigir_la_memoria_igual_a_los_renglones(conn)

    print("\n===== 6. UPGRADE -> DOWNGRADE -> UPGRADE =====")
    for tabla in al_principio:
        print(f"  {tabla:<22}{al_principio[tabla]}  ->  {al_final[tabla]}")
    print(f"  memoria: {len(primera)} filas la primera vez, {len(segunda)} la segunda")
    migracion.exigir_que_nada_se_movio(al_principio, al_final)
    assert al_final == al_principio
    assert segunda == primera, "la memoria salio distinta en la segunda vuelta"
