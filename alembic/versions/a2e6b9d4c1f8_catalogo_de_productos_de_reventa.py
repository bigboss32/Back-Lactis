"""el catálogo de productos de reventa: qué se compra y se revende, como dato

LO QUE PIDIÓ EL DUEÑO, textual: "en reventa, en ventas, cuando se vaya a registrar que
se pudiera vender varios productos, y además que se pudiera comprar más productos de
manera dinámica, es decir no solo hacerlo manual sino la opción de comprar y vender algo
que quiera el cliente". Esa última parte —"algo que quiera el cliente"— es esta tabla.

CÓMO ESTABA: los productos de reventa son TRES y están escritos a mano en el código
(`TIPO_QUESO`, `TIPO_BORONA`, `TIPO_MOZZARELLA` en app/modules/reventa/models.py), y cada
fila de `compras_queso` / `ventas_queso` lleva el suyo pegado en su columna `tipo`. Para
agregar un cuarto había que tocar código y desplegar.

QUÉ AGREGA ESTA MIGRACIÓN: la tabla `productos_reventa` y, enseguida, los TRES productos
que ya existen, en TODAS las empresas vivas.

POR QUÉ NO PUEDE MOVER NI UNA CIFRA, que es lo que hay que poder afirmar sobre la base de
un cliente real con plata de verdad adentro:

  · NO ALTERA NINGUNA TABLA QUE YA EXISTA. Ni un ALTER, ni un UPDATE, ni un DELETE:
    `compras_queso`, `ventas_queso`, `abonos_*`, `conversiones_borona`, `temporadas`,
    `saldos_anteriores`, `adjuntos_reventa` y `documentos_reventa` se quedan byte por
    byte como estaban. Lo único que hace es CREATE TABLE y después INSERT en la tabla
    que acabó de crear.

  · Y LA TABLA NUEVA NO LA LEE NADIE TODAVÍA. En este lote ninguna consulta de compras,
    de ventas, del resumen, de las temporadas, de los lotes ni del FIFO mira
    `productos_reventa`. O sea que no hay por dónde: una tabla que ninguna cuenta
    consulta no puede cambiarle el resultado a ninguna cuenta.

LA CLAVE ES EL PUENTE, y es la decisión que hace que esto no cueste una migración de
datos ni ahora ni después. `clave` es la MISMA cadena que ya está guardada en
`compras_queso.tipo` y `ventas_queso.tipo`: los tres productos se siembran con las claves
'queso', 'borona' y 'mozzarella', que son exactamente los valores que las filas del
cliente ya tienen. El día que las compras y las ventas empiecen a mirar el catálogo, cada
fila que ya existe encuentra su producto sin que haya que tocar ni una columna.

LA SIEMBRA VA EN TODAS LAS EMPRESAS VIVAS, no solo en las que usan reventa, para que las
dos queseras queden iguales: un catálogo de tres filas no le cuesta nada a una empresa que
no lo use, y evita que mañana una tenga la tabla poblada y la otra no.

Y VA DOS VECES, ACÁ Y EN LA SIEMBRA DE CADA DESPLIEGUE (app/seeds/seed.py::
ensure_catalogos_empresas, que start.sh corre justo después de `alembic upgrade head`).
No es duplicado por descuido: acá cubre a las empresas que YA EXISTEN, y allá cubre a las
que se creen DESPUÉS, porque `EmpresaService.crear` no siembra catálogos —nunca lo ha
hecho, y ese `ensure_*` es el mecanismo que ya cubre `tipos_queso` y `categorias_gasto`—.
Las dos son idempotentes y las dos se guardan con el mismo UNIQUE, así que correrlas
cuantas veces sea no duplica nada.

OJO: esta migración LEE datos (la lista de empresas), así que necesita una conexión de
verdad y no corre en el modo offline de alembic (`--sql`), igual que la de documentos. El
despliegue la corre como toca, contra la base.

Revision ID: a2e6b9d4c1f8
Revises: f7c3a1d9e4b2
Create Date: 2026-08-04 15:00:00.000000

"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2e6b9d4c1f8'
down_revision: Union[str, None] = 'f7c3a1d9e4b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Los tres productos que ya maneja el módulo, con las claves que las filas del cliente ya
# tienen guardadas en su columna `tipo`.
#
#     (clave, nombre, unidad, clave del producto del que es subproducto)
#
# VA COPIADA Y NO IMPORTADA de app/modules/reventa/models.py, y es a propósito: una
# migración no puede depender del código de la aplicación, que cambia. Si mañana se le
# agrega un cuarto producto a esa lista, esta migración tiene que seguir sembrando los
# tres que había el día que se escribió, porque es lo que ya corrió en la base del
# cliente. Lo que sí se exige es que HOY digan lo mismo, y eso lo mide
# tests/test_reventa_productos_siembra.py comparando las dos listas.
#
# `decimales` y `admite_ajustes` no van en la lista: se deducen de la unidad (dos
# decimales y admite merma si se pesa; entero y sin merma si se cuenta), que es la misma
# deducción que hace `derivados_de_unidad` en el modelo y que el CHECK de la tabla exige.
PRODUCTOS = (
    ('queso', 'Queso', 'kg', None),
    ('borona', 'Borona', 'kg', 'queso'),
    ('mozzarella', 'Mozzarella', 'unidad', None),
)


def _derivados(unidad: str) -> tuple[int, bool]:
    se_pesa = unidad == 'kg'
    return (2 if se_pesa else 0), se_pesa


# Las dos tablas declaradas AL MÍNIMO y con sus tipos, que es la forma portable de
# escribirlas desde una migración.
#
# No es adorno: con `sa.text("INSERT ... VALUES (:id, ...)")` y un uuid.UUID de Python,
# SQLite revienta con "type 'UUID' is not supported" —no tiene adaptador— y Postgres sí lo
# acepta. O sea que el SQL pelado corría en producción y NO en las pruebas, que es
# exactamente al revés de lo que sirve. Con las columnas tipadas, SQLAlchemy convierte el
# UUID a lo que cada motor guarda (CHAR(32) en SQLite, UUID nativo en Postgres) y las dos
# puntas leen y escriben lo mismo.
#
# Se declaran acá y no se importan de la aplicación por lo de siempre: una migración no
# puede depender de un modelo que mañana cambie de columnas.
def _tabla_productos() -> sa.Table:
    return sa.Table(
        'productos_reventa',
        sa.MetaData(),
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('empresa_id', sa.Uuid()),
        sa.Column('nombre', sa.String(length=80)),
        sa.Column('clave', sa.String(length=80)),
        sa.Column('unidad', sa.String(length=20)),
        sa.Column('decimales', sa.Integer()),
        sa.Column('subproducto_de_id', sa.Uuid()),
        sa.Column('admite_ajustes', sa.Boolean()),
        sa.Column('orden', sa.Integer()),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('estado', sa.String(length=30)),
    )


def _tabla_empresas() -> sa.Table:
    return sa.Table(
        'empresas',
        sa.MetaData(),
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
    )


def sembrar_productos(conn) -> dict[str, int]:
    """Le pone su catálogo de tres productos a cada empresa viva.

    Está en una función suelta y todo pasa por `conn` —nada por `op`— para poder
    EJERCITARLA EN UNA PRUEBA. Es el mismo camino de la migración de documentos: `op`
    solo existe cuando alembic ya montó su contexto, así que una función que lo usara no
    se podría llamar desde una prueba, y esta migración corre una sola vez sobre datos que
    nadie puede volver a ver. `upgrade()` le pasa `op.get_bind()`, que es la misma conexión
    y la misma transacción.

    LOS UUID SE GENERAN EN PYTHON, no en SQL. `gen_random_uuid()` es de Postgres y en
    SQLite no existe, así que un INSERT ... SELECT que los generara no correría en las
    pruebas. Acá el volumen es de tres filas por empresa: hacerlo en Python no cuesta nada
    y corre igual en los dos motores.

    ES IDEMPOTENTE POR CLAVE: si la empresa ya tiene un producto con esa clave —vivo o
    borrado en suave— no se toca. Tiene que contar también los borrados porque el UNIQUE
    de (empresa_id, clave) no filtra `deleted_at`, así que una fila borrada sigue ocupando
    su clave y un segundo INSERT se estrellaría contra la base.

    Devuelve {clave: cuántas se crearon}, para el log del despliegue.
    """
    ahora = datetime.now(timezone.utc)
    productos, empresas_tbl = _tabla_productos(), _tabla_empresas()
    empresas = list(
        conn.execute(
            sa.select(empresas_tbl.c.id).where(empresas_tbl.c.deleted_at.is_(None))
        ).scalars()
    )

    creados = {clave: 0 for clave, _, _, _ in PRODUCTOS}
    for empresa_id in empresas:
        # Lo que la empresa ya tiene, borrados incluidos: {clave: id}.
        ya_tiene = dict(
            conn.execute(
                sa.select(productos.c.clave, productos.c.id).where(
                    productos.c.empresa_id == empresa_id
                )
            ).all()
        )
        for orden, (clave, nombre, unidad, clave_padre) in enumerate(PRODUCTOS):
            if clave in ya_tiene:
                continue
            decimales, admite_ajustes = _derivados(unidad)
            nuevo_id = uuid.uuid4()
            conn.execute(
                sa.insert(productos).values(
                    id=nuevo_id,
                    empresa_id=empresa_id,
                    nombre=nombre,
                    clave=clave,
                    unidad=unidad,
                    decimales=decimales,
                    # El padre se busca en lo que la empresa ya tiene, que incluye lo que
                    # se acabó de insertar: el queso va antes que la borona en la lista,
                    # así que cuando le toca a la borona su padre ya tiene id.
                    subproducto_de_id=ya_tiene.get(clave_padre) if clave_padre else None,
                    admite_ajustes=admite_ajustes,
                    orden=orden,
                    created_at=ahora,
                    updated_at=ahora,
                    estado='activo',
                )
            )
            ya_tiene[clave] = nuevo_id
            creados[clave] += 1

    # ----------------------------------------------------------------- post-vuelo
    # Un chequeo que nunca se ha visto fallar no es un chequeo. Si la siembra quedó a
    # medias, la migración REVIENTA con un mensaje entendible y alembic deshace toda la
    # transacción: la base del cliente se queda como estaba.
    faltantes = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM empresas e
            WHERE e.deleted_at IS NULL
              AND (
                SELECT COUNT(*) FROM productos_reventa p WHERE p.empresa_id = e.id
              ) < :cuantos
            """
        ),
        {"cuantos": len(PRODUCTOS)},
    ).scalar()
    if faltantes:
        raise RuntimeError(
            f"MIGRACIÓN ABORTADA: quedaron {faltantes} empresa(s) con menos de "
            f"{len(PRODUCTOS)} productos de reventa. Todas tienen que quedar con el "
            f"mismo catálogo, o una quesera vería la lista de productos vacía.\n"
            f"No se guardó nada: alembic deshace toda la transacción."
        )

    # Y la borona tiene que quedar colgada del queso DE SU MISMA EMPRESA. Si apuntara a
    # la de otra, el reparto de costos le heredaría el costo del queso equivocado el día
    # que el FIFO empiece a leer esta columna.
    cruzadas = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM productos_reventa h
            JOIN productos_reventa p ON p.id = h.subproducto_de_id
            WHERE h.empresa_id <> p.empresa_id
            """
        )
    ).scalar()
    if cruzadas:
        raise RuntimeError(
            f"MIGRACIÓN ABORTADA: {cruzadas} subproducto(s) quedaron colgados de un "
            f"producto de OTRA empresa.\n"
            f"No se guardó nada: alembic deshace toda la transacción."
        )
    return creados


def upgrade() -> None:
    op.create_table(
        'productos_reventa',
        sa.Column('nombre', sa.String(length=80), nullable=False),
        # La identidad: la misma cadena que ya vive en `compras_queso.tipo`.
        sa.Column('clave', sa.String(length=80), nullable=False),
        sa.Column('unidad', sa.String(length=20), server_default='kg', nullable=False),
        sa.Column('decimales', sa.Integer(), server_default='2', nullable=False),
        sa.Column('subproducto_de_id', sa.Uuid(), nullable=True),
        sa.Column('admite_ajustes', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('orden', sa.Integer(), server_default='0', nullable=False),
        sa.Column('empresa_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
        sa.CheckConstraint("unidad IN ('kg', 'unidad')", name='ck_productos_reventa_unidad'),
        # El techo es el de las columnas de cantidad de los renglones (Numeric(12, 2)):
        # más de dos decimales no se podrían guardar.
        sa.CheckConstraint(
            'decimales >= 0 AND decimales <= 2', name='ck_productos_reventa_decimales'
        ),
        # LA COHERENCIA DE LA UNIDAD, exigida por la base y no por la disciplina de quien
        # escriba el próximo INSERT: lo que se cuenta va en piezas enteras y no admite
        # merma; lo que se pesa sí la admite. Con booleanos pelados porque es lo único
        # que significa lo mismo en Postgres y en SQLite.
        sa.CheckConstraint(
            "(unidad = 'kg' AND admite_ajustes) "
            "OR (unidad = 'unidad' AND decimales = 0 AND NOT admite_ajustes)",
            name='ck_productos_reventa_unidad_coherente',
        ),
        sa.CheckConstraint(
            'subproducto_de_id IS NULL OR subproducto_de_id <> id',
            name='ck_productos_reventa_subproducto_no_es_si_mismo',
        ),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        # ON DELETE SET NULL: si el padre desapareciera, el subproducto se queda —es un
        # producto completo que se vende— y lo que se pierde es la relación, no la fila.
        sa.ForeignKeyConstraint(
            ['subproducto_de_id'], ['productos_reventa.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
        # LA CLAVE ES ÚNICA POR EMPRESA: es lo que hace que la siembra se pueda correr en
        # cada despliegue sin duplicar nada. Por (empresa_id, clave) y no global porque
        # las dos queseras son negocios distintos y cada una tiene su 'queso'.
        sa.UniqueConstraint('empresa_id', 'clave', name='uq_productos_reventa_empresa_clave'),
    )
    op.create_index(op.f('ix_productos_reventa_clave'), 'productos_reventa', ['clave'], unique=False)
    op.create_index(op.f('ix_productos_reventa_empresa_id'), 'productos_reventa', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_productos_reventa_estado'), 'productos_reventa', ['estado'], unique=False)
    op.create_index(op.f('ix_productos_reventa_subproducto_de_id'), 'productos_reventa', ['subproducto_de_id'], unique=False)

    sembrar_productos(op.get_bind())


def downgrade() -> None:
    """Bajar esto NO PIERDE NI UN PESO, y es la ventaja de que el catálogo todavía no lo
    lea ninguna cuenta: se borra una tabla que nadie consulta. Las compras, las ventas,
    los abonos, los lotes y el resumen siguen leyendo el `tipo` de cada fila, que es lo
    que han leído siempre, y dan exactamente las mismas cifras.

    Lo que se pierde son los productos que el dueño hubiera agregado él. Los tres de
    fábrica vuelven solos con la siguiente siembra.
    """
    op.drop_index(op.f('ix_productos_reventa_subproducto_de_id'), table_name='productos_reventa')
    op.drop_index(op.f('ix_productos_reventa_estado'), table_name='productos_reventa')
    op.drop_index(op.f('ix_productos_reventa_empresa_id'), table_name='productos_reventa')
    op.drop_index(op.f('ix_productos_reventa_clave'), table_name='productos_reventa')
    op.drop_table('productos_reventa')
