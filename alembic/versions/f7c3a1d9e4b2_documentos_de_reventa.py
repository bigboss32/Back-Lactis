"""la factura de reventa: varios productos en una sola compra o venta

LO QUE PIDIÓ EL DUEÑO, textual: "en reventa, en ventas, cuando se vaya a registrar que
se pudiera vender varios productos, y además que se pudiera comprar más productos de
manera dinámica, es decir no solo hacerlo manual sino la opción de comprar y vender algo
que quiera el cliente".

CÓMO ESTABA: `compras_queso` y `ventas_queso` son UNA FILA = UN PRODUCTO. Esa fila ya es
un renglón de factura completo —un producto, su cantidad, su precio, su plata y sus
abonos—; lo único que faltaba era una cabecera ENCIMA que dijera "estos tres renglones
son la misma venta del mismo día al mismo cliente".

QUÉ AGREGA ESTA MIGRACIÓN:

  · la tabla `documentos_reventa`, la cabecera: tipo (compra|venta), fecha, tercero y
    observaciones. Y NI UNA COLUMNA DE PLATA: no tiene valor_total, no tiene abonado y
    no tiene estado de pago. El total del documento es la SUMA de sus renglones,
    calculada al leer. Es el mismo criterio que ya está escrito y defendido en
    `Temporada`: dos fuentes para el mismo hecho terminan contradiciéndose, y el día que
    se contradigan el dueño —que suma la columna a mano— pierde la confianza en todo el
    tablero.

  · `documento_id` en `compras_queso` y en `ventas_queso`, ANULABLE y con
    ON DELETE SET NULL. Anulable porque el renglón se sostiene solo: fue una compra
    completa durante meses y sigue siendo la unidad que lee el FIFO, el resumen y la
    cartera. SET NULL y no CASCADE porque si algún día se borrara una cabecera sin pasar
    por el servicio, lo que NO puede pasar es que se lleve la plata por delante.

  · `orden` en las dos, NOT NULL con server_default '0'. Es el lugar del renglón en la
    factura, y no es decoración: el abono a la factura se DERRAMA sobre los renglones en
    ese orden (así que es el orden en el que el dueño ve caer su plata) y es el orden en
    el que se toman los candados FOR UPDATE, sin el cual dos abonos simultáneos a la
    misma factura se abrazarían en un deadlock.

POR QUÉ NINGUNA CIFRA SE MUEVE, que es lo que hay que poder afirmar sobre la base de un
cliente real: las columnas que agrega son un id y un entero. `documentos_reventa` no
tiene dónde escribir plata ni aunque quisiera, así que el backfill no puede tocarla. El
FIFO por lotes, el resumen, las temporadas, la cartera, los abonos, los adjuntos y los
PDF siguen leyendo filas de un producto cada una, que es lo que siempre leyeron, y el
`documento_id` les es invisible.

EL BACKFILL: UNA CABECERA POR CADA FILA QUE YA EXISTE. Cada compra y cada venta viva
queda siendo una factura de un renglón, que es lo que de verdad son. Y el documento
LLEVA EL MISMO id DE SU FILA, a propósito: así el backfill son cuatro sentencias SQL sin
generar nada en Python (más rápido, más portable y sin executemany), y queda el rastro
de cuál cabecera nació de cuál fila.

Las filas BORRADAS EN SUAVE (deleted_at no nulo) se quedan sin documento, y también es a
propósito: darles una cabecera viva las haría aparecer en la lista de facturas como
facturas vacías —la lectura filtra los renglones borrados—, y una fila borrada no se lee
por ningún lado. Para eso está el `documento_id` anulable.

EL PRE-VUELO Y EL POST-VUELO son lo que convierte "sin mover una cifra" en algo
verificable en vez de prometido. Antes de escribir se cuentan las filas y se suman
valor_total y abonado de cada tabla; después se comprueba que esas tres cifras quedaron
IDÉNTICAS, que toda fila viva tiene su documento y que hay exactamente un documento por
fila viva. Si algo no cuadra, la migración REVIENTA con un mensaje entendible y alembic
hace rollback de toda la transacción: la base del cliente se queda como estaba.

OJO: esta migración LEE datos, así que necesita una conexión de verdad y no corre en el
modo offline de alembic (`--sql`), que solo escupe SQL estático. El despliegue la corre
como toca —`alembic upgrade head` contra la base, ver start.sh—; el `--sql` sirve para
revisar el DDL a mano y se detiene en el primer SELECT del pre-vuelo, que es lo esperado.

Revision ID: f7c3a1d9e4b2
Revises: e5c2b9a1f7d3
Create Date: 2026-08-04 12:00:00.000000

"""
from decimal import Decimal
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7c3a1d9e4b2'
down_revision: Union[str, None] = 'e5c2b9a1f7d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Las dos tablas de renglones y cómo se llama en cada una el nombre del tercero.
RENGLONES = (
    ("compras_queso", "compra", "productor"),
    ("ventas_queso", "venta", "cliente"),
)


def _cifras_de_control(conn, tabla: str) -> tuple[int, Decimal, Decimal]:
    """(filas vivas, plata facturada, plata abonada) de una tabla de renglones.

    Son las tres cifras que NO PUEDEN MOVERSE. Se leen igual antes y después de
    escribir, sobre exactamente los mismos datos, así que cualquier diferencia
    significa que el backfill tocó algo que no le correspondía.

    Solo las filas VIVAS (deleted_at IS NULL): una fila borrada en suave no la lee
    ninguna pantalla ni suma en ninguna cifra, y es la que a propósito se queda sin
    documento.
    """
    fila = conn.execute(
        sa.text(
            f"SELECT COUNT(*), COALESCE(SUM(valor_total), 0), "  # noqa: S608 - nombre fijo
            f"COALESCE(SUM(abonado), 0) FROM {tabla} WHERE deleted_at IS NULL"
        )
    ).one()
    return int(fila[0]), Decimal(str(fila[1])), Decimal(str(fila[2]))


def _contar(conn, sql: str) -> int:
    return int(conn.execute(sa.text(sql)).scalar() or 0)


def backfill_documentos(conn) -> dict[str, int]:
    """Le pone su cabecera a cada compra y a cada venta que ya existe.

    Está en una función suelta y no dentro de `upgrade()` para poder EJERCITARLA EN
    UNA PRUEBA: es la única forma de comprobar que el pre-vuelo y el post-vuelo de
    verdad reventarían, porque una vez desplegada esta migración corre una sola vez
    y sobre datos que nadie puede volver a ver. Ver
    tests/test_reventa_documentos_migracion.py.

    Todo pasa por `conn` y nada por `op`, y eso es lo que la hace probable: `op`
    solo existe cuando alembic ya montó su contexto, así que una función que lo usara
    no se podría llamar desde una prueba. `upgrade()` le pasa `op.get_bind()`, que es
    la misma conexión y la misma transacción.

    Devuelve cuántas cabeceras creó por tipo, para el log del despliegue.
    """
    # ------------------------------------------------------------------ pre-vuelo
    antes = {tabla: _cifras_de_control(conn, tabla) for tabla, _, _ in RENGLONES}

    creados: dict[str, int] = {}
    for tabla, tipo, campo_tercero in RENGLONES:
        # UNA CABECERA POR FILA VIVA, con el MISMO id de la fila (ver el docstring
        # del módulo). Se copian la fecha, el nombre del tercero, la nota y la
        # autoría: la factura de un solo producto ES esa compra, así que su
        # historia tiene que ser la misma. NINGUNA COLUMNA DE PLATA SE ESCRIBE
        # —`documentos_reventa` no tiene ninguna— y por eso este INSERT no puede
        # mover una cifra ni por accidente.
        conn.execute(
            sa.text(
                f"""
                INSERT INTO documentos_reventa (
                    id, empresa_id, tipo, fecha, tercero, observaciones,
                    created_at, updated_at, created_by, updated_by, estado
                )
                SELECT
                    id, empresa_id, '{tipo}', fecha, {campo_tercero}, observaciones,
                    created_at, updated_at, created_by, updated_by, 'activo'
                FROM {tabla}
                WHERE deleted_at IS NULL
                """  # noqa: S608 - los nombres son constantes de este módulo
            )
        )
        # Y cada fila apunta a la suya. El WHERE es el MISMO que el del INSERT: si
        # se le olvidara el `deleted_at IS NULL`, una fila borrada apuntaría a una
        # cabecera que no existe y la llave foránea tumbaría la migración.
        conn.execute(
            sa.text(
                f"UPDATE {tabla} SET documento_id = id "  # noqa: S608
                f"WHERE deleted_at IS NULL"
            )
        )
        creados[tipo] = _contar(
            conn, f"SELECT COUNT(*) FROM documentos_reventa WHERE tipo = '{tipo}'"  # noqa: S608
        )

    # ----------------------------------------------------------------- post-vuelo
    for tabla, tipo, _ in RENGLONES:
        despues = _cifras_de_control(conn, tabla)
        if despues != antes[tabla]:
            raise RuntimeError(
                f"MIGRACIÓN ABORTADA: las cifras de {tabla} cambiaron al crear las "
                f"facturas, y no debían moverse ni un peso.\n"
                f"  antes:   {antes[tabla][0]} filas, facturado {antes[tabla][1]}, "
                f"abonado {antes[tabla][2]}\n"
                f"  después: {despues[0]} filas, facturado {despues[1]}, "
                f"abonado {despues[2]}\n"
                f"No se guardó nada: alembic deshace toda la transacción."
            )

        huerfanas = _contar(
            conn,
            f"SELECT COUNT(*) FROM {tabla} "  # noqa: S608
            f"WHERE deleted_at IS NULL AND documento_id IS NULL",
        )
        if huerfanas:
            raise RuntimeError(
                f"MIGRACIÓN ABORTADA: quedaron {huerfanas} filas de {tabla} sin "
                f"factura. Toda fila viva tiene que tener la suya, o la pantalla de "
                f"facturas no las mostraría.\n"
                f"No se guardó nada: alembic deshace toda la transacción."
            )

        cabeceras = _contar(
            conn,
            f"SELECT COUNT(*) FROM documentos_reventa WHERE tipo = '{tipo}'",  # noqa: S608
        )
        if cabeceras != antes[tabla][0]:
            raise RuntimeError(
                f"MIGRACIÓN ABORTADA: se crearon {cabeceras} facturas de {tipo} para "
                f"{antes[tabla][0]} filas de {tabla}. Tiene que haber exactamente "
                f"una por fila: de sobra saldrían facturas vacías en la lista, y de "
                f"menos habría renglones sin cabecera.\n"
                f"No se guardó nada: alembic deshace toda la transacción."
            )
    return creados


def upgrade() -> None:
    op.create_table(
        'documentos_reventa',
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('tercero', sa.String(length=150), nullable=False),
        sa.Column('observaciones', sa.String(length=500), nullable=True),
        sa.Column('empresa_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
        # Solo hay dos clases de factura, y `tipo` decide en CUÁL de las dos tablas
        # de renglones hay que buscar: un tercer valor dejaría un documento que
        # ninguna lectura sabría abrir. Lo exige la base y no el esquema de entrada.
        sa.CheckConstraint("tipo IN ('compra', 'venta')", name='ck_documentos_reventa_tipo'),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_documentos_reventa_empresa_id'), 'documentos_reventa', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_documentos_reventa_estado'), 'documentos_reventa', ['estado'], unique=False)
    op.create_index(op.f('ix_documentos_reventa_fecha'), 'documentos_reventa', ['fecha'], unique=False)
    op.create_index(op.f('ix_documentos_reventa_tipo'), 'documentos_reventa', ['tipo'], unique=False)

    # `batch_alter_table` y no `add_column` + `create_foreign_key` pelados: SQLite no
    # sabe agregarle una FOREIGN KEY a una tabla que ya existe, así que sin batch esto
    # no corre en local. En Postgres —que es producción— el batch se resuelve en los
    # ALTER TABLE de siempre. Las tres operaciones van en el MISMO bloque para que
    # SQLite recree la tabla una sola vez.
    #
    # `orden` NOT NULL con server_default '0' porque sin el default el ALTER TABLE
    # revienta en la primera fila que ya existe; y 0 es el valor CORRECTO para todas
    # ellas, no un relleno: cada una queda sola en su factura, así que es el renglón
    # número cero de la suya.
    for tabla in ('compras_queso', 'ventas_queso'):
        with op.batch_alter_table(tabla, schema=None) as batch_op:
            batch_op.add_column(sa.Column('documento_id', sa.Uuid(), nullable=True))
            batch_op.add_column(
                sa.Column('orden', sa.Integer(), server_default='0', nullable=False)
            )
            batch_op.create_foreign_key(
                f'fk_{tabla}_documento_id_documentos_reventa',
                'documentos_reventa',
                ['documento_id'],
                ['id'],
                ondelete='SET NULL',
            )
        # El índice va aparte del batch (crear un índice no recrea la tabla) y existe
        # porque esta columna se consulta en CADA lectura de una factura: "tráeme los
        # renglones de este documento".
        op.create_index(
            op.f(f'ix_{tabla}_documento_id'), tabla, ['documento_id'], unique=False
        )

    backfill_documentos(op.get_bind())


def downgrade() -> None:
    """AL BAJAR SE PIERDEN LAS FACTURAS DE VARIOS PRODUCTOS, y hay que saber qué queda.

    Los renglones NO SE TOCAN, y eso es lo importante: cada uno vuelve a ser lo que era
    antes de esta migración —una compra o una venta de un solo producto, con su plata y
    sus abonos intactos—, y el código viejo los lee igual que siempre. Ninguna cifra se
    mueve al bajar.

    Lo que se pierde es la AGRUPACIÓN: una venta de tres productos que se registró como
    una sola factura vuelve a verse como tres ventas separadas del mismo día al mismo
    cliente. Suman lo mismo (el resumen, la cartera y los lotes nunca supieron de
    documentos), pero el dueño ya no las ve juntas y no hay forma de volver a unirlas.
    Por eso bajar esta migración obliga a bajar también el código.
    """
    for tabla in ('compras_queso', 'ventas_queso'):
        op.drop_index(op.f(f'ix_{tabla}_documento_id'), table_name=tabla)
        with op.batch_alter_table(tabla, schema=None) as batch_op:
            batch_op.drop_constraint(
                f'fk_{tabla}_documento_id_documentos_reventa', type_='foreignkey'
            )
            batch_op.drop_column('orden')
            batch_op.drop_column('documento_id')

    op.drop_index(op.f('ix_documentos_reventa_tipo'), table_name='documentos_reventa')
    op.drop_index(op.f('ix_documentos_reventa_fecha'), table_name='documentos_reventa')
    op.drop_index(op.f('ix_documentos_reventa_estado'), table_name='documentos_reventa')
    op.drop_index(op.f('ix_documentos_reventa_empresa_id'), table_name='documentos_reventa')
    op.drop_table('documentos_reventa')
