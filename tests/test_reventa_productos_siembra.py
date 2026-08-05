"""LA SIEMBRA del catálogo de productos de reventa, por sus dos caminos.

El catálogo tiene que aparecer solo, en las DOS queseras, sin que nadie lo cargue a
mano. Y llega por dos puertas que hacen falta las dos:

- LA MIGRACIÓN cubre a las empresas que YA EXISTEN, sobre la base del cliente real, en
  el próximo despliegue.
- LA SIEMBRA DE CADA DESPLIEGUE (`ensure_catalogos_empresas`, que start.sh corre justo
  después de `alembic upgrade head`) cubre a las que se creen DESPUÉS, porque
  `EmpresaService.crear` no siembra catálogos. Es el mismo mecanismo por el que ya
  llegan `tipos_queso` y `categorias_gasto`.

Lo que estas pruebas fijan:

- las dos siembran EXACTAMENTE lo mismo (se comparan las dos listas: la migración lleva
  la suya copiada porque no puede importar de la aplicación);
- son IDEMPOTENTES: correrlas dos veces no duplica nada, que es lo que hace que puedan
  vivir en el arranque de cada despliegue;
- y NO RESUCITAN lo que el dueño quitó, que es el criterio que ya está escrito y
  defendido en `seed_roles`: un despliegue no deshace en silencio la decisión de una
  persona.
"""
import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.empresas.models import Empresa
from app.modules.reventa.models import (
    PRODUCTOS_REVENTA_DEFECTO,
    ProductoReventa,
    derivados_de_unidad,
)
from app.seeds.seed import ensure_catalogos_empresas, sembrar_productos_reventa

MIGRACION = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "a2e6b9d4c1f8_catalogo_de_productos_de_reventa.py"
)

AHORA = datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)


def _cargar_migracion():
    """Carga el archivo de la migración como módulo para llamarle la siembra.

    Es el mismo camino de test_reventa_documentos_migracion: la única forma de probar
    código que corre UNA vez y sobre datos que nadie puede volver a ver.
    """
    spec = importlib.util.spec_from_file_location("mig_productos", MIGRACION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _catalogo(db, empresa_id) -> list[ProductoReventa]:
    return list(
        db.scalars(
            select(ProductoReventa)
            .where(
                ProductoReventa.empresa_id == empresa_id,
                ProductoReventa.deleted_at.is_(None),
            )
            .order_by(ProductoReventa.orden)
        ).unique()
    )


# ============================ la siembra de cada despliegue ============================
def test_la_siembra_cae_en_las_dos_empresas_y_es_idempotente(db_session, base_datos):
    """LAS DOS QUESERAS QUEDAN IGUALES, y correr la siembra otra vez no duplica.

    Se recorren TODAS las empresas vivas y no solo las que usan reventa: un catálogo de
    tres filas no le cuesta nada a la que no lo use, y evita que mañana una tenga la
    tabla poblada y la otra no.
    """
    empresa_a, empresa_b = base_datos["empresa_a"], base_datos["empresa_b"]

    ensure_catalogos_empresas(db_session)
    db_session.flush()

    print("\n===== PRIMERA PASADA =====")
    for empresa in (empresa_a, empresa_b):
        productos = _catalogo(db_session, empresa.id)
        print(f"  {empresa.nombre}: {[p.clave for p in productos]}")
        assert [p.clave for p in productos] == ["queso", "borona", "mozzarella"]
        # La borona cuelga del queso DE SU MISMA EMPRESA. Si apuntara a la de la otra,
        # el reparto le heredaría el costo del queso equivocado el día que el FIFO
        # empiece a leer esta columna.
        queso, borona, mozzarella = productos
        assert borona.subproducto_de_id == queso.id
        assert borona.subproducto_de.empresa_id == empresa.id
        assert queso.subproducto_de_id is None and mozzarella.subproducto_de_id is None
        # Y lo derivado quedó como manda la unidad.
        for producto in productos:
            esperados = derivados_de_unidad(producto.unidad)
            assert (producto.decimales, producto.admite_ajustes) == esperados

    # SEGUNDA Y TERCERA PASADA: es lo que corre en cada despliegue.
    ensure_catalogos_empresas(db_session)
    ensure_catalogos_empresas(db_session)
    db_session.flush()

    total = db_session.scalar(select(func.count()).select_from(ProductoReventa))
    print("===== TRES PASADAS DESPUÉS =====")
    print(f"  filas en total: {total} (2 empresas x 3 productos)")
    assert total == 6, "la siembra duplicó: no es idempotente"
    for empresa in (empresa_a, empresa_b):
        assert [p.clave for p in _catalogo(db_session, empresa.id)] == [
            "queso", "borona", "mozzarella",
        ]


def test_la_siembra_no_resucita_lo_que_el_dueno_quito(db_session, base_datos):
    """UN DESPLIEGUE NO DESHACE LA DECISIÓN DE UNA PERSONA.

    Es el mismo criterio de `seed_roles` con los roles borrados, y acá tiene además una
    razón mecánica: el UNIQUE de (empresa_id, clave) no filtra `deleted_at`, así que la
    fila quitada sigue ocupando su clave y un INSERT nuevo se estrellaría contra la base
    en mitad de un despliegue.

    Si el dueño lo quiere de vuelta, lo agrega él —y le vuelve la MISMA fila, ver
    test_reventa_productos.py—.
    """
    empresa_a = base_datos["empresa_a"]
    ensure_catalogos_empresas(db_session)
    db_session.flush()

    mozzarella = [
        p for p in _catalogo(db_session, empresa_a.id) if p.clave == "mozzarella"
    ][0]
    id_original = mozzarella.id
    mozzarella.deleted_at = AHORA
    mozzarella.estado = "inactivo"
    db_session.flush()

    ensure_catalogos_empresas(db_session)
    db_session.flush()

    vivos = [p.clave for p in _catalogo(db_session, empresa_a.id)]
    todas = list(
        db_session.scalars(
            select(ProductoReventa).where(
                ProductoReventa.empresa_id == empresa_a.id,
                ProductoReventa.clave == "mozzarella",
            )
        ).unique()
    )
    print("\n===== LA SIEMBRA NO RESUCITA =====")
    print(f"  vivos: {vivos}")
    print(f"  filas con clave 'mozzarella': {len(todas)} "
          f"(borrada={todas[0].deleted_at is not None})")
    assert vivos == ["queso", "borona"], "la siembra resucitó lo que el dueño quitó"
    assert len(todas) == 1, "la siembra insertó otra fila con la misma clave"
    assert todas[0].id == id_original


def test_una_empresa_nueva_recibe_su_catalogo_en_la_siguiente_siembra(
    db_session, base_datos
):
    """El caso que cubre este camino y que la migración no puede cubrir: la empresa que
    se crea DESPUÉS del despliegue. `EmpresaService.crear` no siembra catálogos."""
    ensure_catalogos_empresas(db_session)
    nueva = Empresa(nombre="Quesera C", nit="900C")
    db_session.add(nueva)
    db_session.flush()
    assert _catalogo(db_session, nueva.id) == [], "al crearse no debería traer catálogo"

    creados = sembrar_productos_reventa(db_session, nueva.id)
    print("\n===== EMPRESA NUEVA =====")
    print(f"  sembrados: {[p.clave for p in creados]}")
    assert [p.clave for p in _catalogo(db_session, nueva.id)] == [
        "queso", "borona", "mozzarella",
    ]


# ================================== la migración ==================================
def test_la_migracion_siembra_lo_mismo_que_la_aplicacion():
    """LAS DOS LISTAS TIENEN QUE DECIR LO MISMO.

    La migración lleva su lista COPIADA porque no puede importar de la aplicación (una
    migración no puede depender de código que cambia). El precio de esa copia es que
    puede quedar diciendo otra cosa, así que se compara acá: la única forma de que la
    copia sea segura es que una prueba la vigile.
    """
    migracion = _cargar_migracion()
    print("\n===== LAS DOS LISTAS =====")
    print(f"  modelo:    {PRODUCTOS_REVENTA_DEFECTO}")
    print(f"  migración: {migracion.PRODUCTOS}")
    assert migracion.PRODUCTOS == PRODUCTOS_REVENTA_DEFECTO
    # Y la deducción de decimales/admite_ajustes también está copiada.
    for _, _, unidad, _ in PRODUCTOS_REVENTA_DEFECTO:
        assert migracion._derivados(unidad) == derivados_de_unidad(unidad)


@pytest.fixture()
def base_sin_catalogo():
    """Una base con el esquema nuevo y los datos como los deja el sistema viejo: dos
    empresas vivas, una borrada, y la tabla del catálogo vacía."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    empresa_a, empresa_b, empresa_muerta = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        for eid, nombre, nit, borrada in (
            (empresa_a, "Quesera A", "900A", None),
            (empresa_b, "Quesera B", "900B", None),
            (empresa_muerta, "Quesera cerrada", "900Z", AHORA),
        ):
            conn.execute(
                insert(Empresa.__table__).values(
                    id=eid, nombre=nombre, nit=nit, created_at=AHORA,
                    updated_at=AHORA, deleted_at=borrada,
                )
            )
        conn.execute(text("DELETE FROM productos_reventa"))
    try:
        yield engine, empresa_a, empresa_b, empresa_muerta
    finally:
        Base.metadata.drop_all(bind=engine)


def _filas(conn, empresa_id):
    """Las filas del catálogo de una empresa, leídas con las columnas TIPADAS.

    Con `text()` y un uuid.UUID de Python, SQLite revienta con "type 'UUID' is not
    supported" —no tiene adaptador— y Postgres sí lo acepta: el mismo motivo por el que la
    migración declara sus tablas con tipos en vez de escribir SQL pelado.
    """
    tabla = ProductoReventa.__table__
    return conn.execute(
        select(
            tabla.c.clave, tabla.c.nombre, tabla.c.unidad, tabla.c.decimales,
            tabla.c.admite_ajustes, tabla.c.orden, tabla.c.subproducto_de_id, tabla.c.id,
        )
        .where(tabla.c.empresa_id == empresa_id)
        .order_by(tabla.c.orden)
    ).all()


def test_la_migracion_le_pone_el_catalogo_a_cada_empresa_viva(base_sin_catalogo):
    """Y correrla dos veces no duplica: la guarda el UNIQUE, y el chequeo por clave la
    hace pasar de largo."""
    engine, empresa_a, empresa_b, empresa_muerta = base_sin_catalogo
    migracion = _cargar_migracion()

    with engine.begin() as conn:
        creados = migracion.sembrar_productos(conn)
        print("\n===== LA MIGRACIÓN SEMBRÓ =====")
        print(f"  {creados}")
        assert creados == {"queso": 2, "borona": 2, "mozzarella": 2}

        for empresa, nombre in ((empresa_a, "A"), (empresa_b, "B")):
            filas = _filas(conn, empresa)
            print(f"  Quesera {nombre}:")
            for f in filas:
                print(f"    {f[5]}  {f[1]:12} clave={f[0]:12} unidad={f[2]:7} "
                      f"decimales={f[3]} ajustes={bool(f[4])}")
            assert [f[0] for f in filas] == ["queso", "borona", "mozzarella"]
            queso, borona, mozzarella = filas
            # Lo derivado, igual que en la aplicación.
            assert (queso[3], bool(queso[4])) == derivados_de_unidad("kg")
            assert (mozzarella[3], bool(mozzarella[4])) == derivados_de_unidad("unidad")
            # La borona cuelga del queso DE SU EMPRESA.
            assert borona[6] == queso[7]

        # La empresa BORRADA no recibe catálogo: no la lee ninguna pantalla.
        assert _filas(conn, empresa_muerta) == []

        # SEGUNDA PASADA: ni una fila más.
        assert migracion.sembrar_productos(conn) == {"queso": 0, "borona": 0, "mozzarella": 0}
        total = conn.execute(text("SELECT COUNT(*) FROM productos_reventa")).scalar()
        print(f"  filas después de dos pasadas: {total}")
        assert total == 6


def test_la_migracion_no_toca_ninguna_tabla_de_plata(base_sin_catalogo):
    """LO QUE HAY QUE PODER AFIRMAR SOBRE LA BASE DE UN CLIENTE REAL.

    La migración solo hace CREATE TABLE e INSERT en la tabla que acabó de crear. Acá se
    mide sobre las tablas donde vive la plata: si alguna cambiara de conteo, la siembra
    estaría escribiendo donde no le toca.
    """
    engine, *_ = base_sin_catalogo
    migracion = _cargar_migracion()
    tablas = (
        "compras_queso", "ventas_queso", "abonos_compra_queso", "abonos_venta_queso",
        "conversiones_borona", "saldos_anteriores", "abonos_saldo_anterior",
        "temporadas", "documentos_reventa", "adjuntos_reventa",
    )
    with engine.begin() as conn:
        antes = {t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tablas}
        migracion.sembrar_productos(conn)
        despues = {t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tablas}
    print("\n===== LAS TABLAS DE PLATA =====")
    for tabla in tablas:
        print(f"  {tabla:24} antes {antes[tabla]}  después {despues[tabla]}")
    assert antes == despues


class ConexionSaboteada:
    """Una conexión que se TRAGA el INSERT del catálogo y deja pasar todo lo demás.

    Es la forma de comprobar que el post-vuelo de verdad se da cuenta: un chequeo que
    nunca se ha visto fallar no es un chequeo, es un comentario.
    """

    def __init__(self, real):
        self._real = real

    def execute(self, sentencia, *args, **kwargs):
        if "INSERT INTO productos_reventa" in str(sentencia):
            return None
        return self._real.execute(sentencia, *args, **kwargs)


def test_el_post_vuelo_revienta_si_una_empresa_queda_sin_catalogo(base_sin_catalogo):
    """Sin este chequeo, una quesera se quedaría con la lista de productos vacía y nada
    lo avisaría: no habría error, simplemente no habría productos."""
    engine, *_ = base_sin_catalogo
    migracion = _cargar_migracion()
    with engine.begin() as conn:
        with pytest.raises(RuntimeError) as excinfo:
            migracion.sembrar_productos(ConexionSaboteada(conn))
    mensaje = str(excinfo.value)
    print("\n===== EL POST-VUELO REVENTÓ, COMO TENÍA QUE SER =====")
    print("  " + mensaje.replace("\n", "\n  "))
    assert "MIGRACIÓN ABORTADA" in mensaje
    assert "2 empresa(s)" in mensaje
    assert "alembic deshace toda la transacción" in mensaje


def test_el_post_vuelo_revienta_si_un_subproducto_queda_en_otra_empresa(
    base_sin_catalogo,
):
    """El otro chequeo: la borona tiene que colgar del queso DE SU EMPRESA.

    No se puede exigir con un CHECK (compara dos filas), así que se comprueba después
    de escribir. Si apuntara a la otra, el reparto de costos le heredaría el costo del
    queso equivocado el día que el FIFO lea esta columna, y esa cifra saldría mal sin
    que nada se quejara.
    """
    engine, empresa_a, empresa_b, _ = base_sin_catalogo
    migracion = _cargar_migracion()
    queso_de_b = uuid.uuid4()
    with engine.begin() as conn:
        # Se le siembra a mano el catálogo cruzado a la empresa A: su borona colgada de
        # un queso de la empresa B.
        conn.execute(
            insert(ProductoReventa.__table__).values(
                id=queso_de_b, empresa_id=empresa_b, nombre="Queso", clave="queso",
                unidad="kg", decimales=2, admite_ajustes=True, orden=0,
                created_at=AHORA, updated_at=AHORA, estado="activo",
            )
        )
        for clave, nombre, padre in (
            ("queso", "Queso", None), ("borona", "Borona", queso_de_b),
        ):
            conn.execute(
                insert(ProductoReventa.__table__).values(
                    id=uuid.uuid4(), empresa_id=empresa_a, nombre=nombre, clave=clave,
                    unidad="kg", decimales=2, admite_ajustes=True, orden=0,
                    subproducto_de_id=padre, created_at=AHORA, updated_at=AHORA,
                    estado="activo",
                )
            )
        with pytest.raises(RuntimeError) as excinfo:
            migracion.sembrar_productos(conn)
    mensaje = str(excinfo.value)
    print("\n===== SUBPRODUCTO CRUZADO DE EMPRESA =====")
    print("  " + mensaje.replace("\n", "\n  "))
    assert "MIGRACIÓN ABORTADA" in mensaje
    assert "de OTRA empresa" in mensaje
