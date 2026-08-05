"""la deuda del tercero se cobra en la liquidación siguiente

LO QUE PIDIÓ EL DUEÑO, textual: "necesito que en la liquidación, a los que quedaron
en negativo, ese saldo que se queda debiendo —es decir, el proveedor a la quesera— se
cobre en la siguiente liquidación".

DE DÓNDE SALE ESE NEGATIVO: los anticipos que se le entregaron en la mano suman más
que lo que valió su quincena. El caso real, con sus cifras: $180.000 de leche contra
$300.000 de anticipo ya entregado -> el proveedor le quedó debiendo $120.000. Hasta
hoy eso solo se DECÍA (el rótulo "LE QUEDA DEBIENDO" del comprobante): no había nada
que lo cobrara después, y la plata se quedaba escrita en un papel viejo.

QUÉ AGREGA ESTA MIGRACIÓN, dos columnas a `liquidaciones`:

  · `saldo_anterior` Numeric(14,2), NOT NULL con server_default '0': lo que el tercero
    quedó debiendo de quincenas pasadas y SE LE COBRA EN ESTA. Es un descuento del
    neto, del mismo tipo que los anticipos —plata que ya salió de la caja— y por eso la
    cuenta pasa a ser:

        neto_a_pagar = valor_total - anticipos - saldo_anterior

  · `deuda_trasladada_a_id` Uuid anulable, FK a `liquidaciones.id`: se pone en la
    liquidación que DEJÓ la deuda y apunta a la que se la cobró. Es el mismo idioma que
    el proyecto ya usa para marcar el origen consumido (`recepciones_leche.liquidacion_id`,
    `anticipos.liquidacion_id`), y es lo que hace IMPOSIBLE cobrar la misma deuda dos
    veces: la consulta que busca deudas por cobrar salta a las que ya están marcadas.

LAS DOS NACEN SIN OBLIGAR A TOCAR NADA DE LO QUE YA ESTÁ EN LA BASE DEL CLIENTE:
`saldo_anterior` con server_default '0' (sin él, el ALTER TABLE NOT NULL revienta en la
primera liquidación existente) y la FK anulable, que es lo normal —la enorme mayoría de
las liquidaciones no dejan ninguna deuda—. Ninguna liquidación vieja cambia de cifra:
con saldo_anterior en 0, su neto sigue siendo exactamente valor_total - anticipos, que
es lo que dice el comprobante que ya se imprimió.

NO HAY BACKFILL, Y ES A PROPÓSITO. Si hoy hay liquidaciones con saldo negativo en la
base del cliente, su deuda queda DISPONIBLE: `deuda_trasladada_a_id` en nulo significa
"nadie se la ha cobrado", así que la próxima liquidación que se le genere a ese tercero
se la cobra sola. Es justo lo que el dueño quiere, y es la razón de que no haya que
inventar nada: la deuda vieja no se pierde ni se cobra dos veces. Tampoco se les toca
el estado a las que el botón "Pagar" de antes dejó en 'pagada' con saldo negativo: esa
deuda sigue contando y se cobra igual (la consulta acepta 'aprobada' y 'pagada'), y
cambiarle el estado a un documento que alguien ya imprimió sería peor.

Revision ID: e5c2b9a1f7d3
Revises: d3b8f4c1a7e6
Create Date: 2026-08-04 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5c2b9a1f7d3'
down_revision: Union[str, None] = 'd3b8f4c1a7e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `batch_alter_table` y no `op.add_column` + `op.create_foreign_key` pelados:
    # SQLite no sabe agregarle una FOREIGN KEY a una tabla que ya existe (no hay ALTER
    # TABLE ADD CONSTRAINT), así que sin batch esto no corre en local. En Postgres
    # —que es producción— el batch se resuelve en los ALTER TABLE de siempre y no
    # cambia nada. Las tres operaciones van en el MISMO bloque para que SQLite recree
    # la tabla una sola vez.
    with op.batch_alter_table('liquidaciones', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'saldo_anterior',
                sa.Numeric(precision=14, scale=2),
                server_default='0',
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column('deuda_trasladada_a_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            'fk_liquidaciones_deuda_trasladada_a_id_liquidaciones',
            'liquidaciones',
            ['deuda_trasladada_a_id'],
            ['id'],
        )

    # El índice va aparte del batch (crear un índice no necesita recrear la tabla) y
    # existe porque esta columna se consulta en cada generación de liquidaciones: hay
    # que preguntar "¿cuáles de este tercero tienen deuda sin cobrar?" y eso filtra por
    # `deuda_trasladada_a_id IS NULL`, y al anular hay que encontrar las que apuntan a
    # una liquidación dada.
    op.create_index(
        op.f('ix_liquidaciones_deuda_trasladada_a_id'),
        'liquidaciones',
        ['deuda_trasladada_a_id'],
        unique=False,
    )


def downgrade() -> None:
    # AL BAJAR SE PIERDE EL COBRO DE LAS DEUDAS, y hay que saber qué queda: las
    # liquidaciones que ya se cobraron un `saldo_anterior` vuelven a tener un neto MÁS
    # ALTO que el del comprobante impreso (por la cifra que se les descontaba), y la
    # marca de las que dejaron la deuda desaparece, así que el código viejo las vería
    # otra vez como "deuda disponible"... salvo que el código viejo no sabe cobrarlas.
    # O sea: bajar esta migración obliga a bajar también el código, y a revisar a mano
    # las liquidaciones que hayan cobrado una deuda entre las dos.
    #
    # No se les toca el `saldo` a las que ya lo tenían cuadrado con la columna: dejar
    # el saldo tal como está es lo que conserva la igualdad que el dueño verifica
    # (pagado + saldo) contra el papel que tiene en la mano.
    op.drop_index(op.f('ix_liquidaciones_deuda_trasladada_a_id'), table_name='liquidaciones')
    with op.batch_alter_table('liquidaciones', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_liquidaciones_deuda_trasladada_a_id_liquidaciones', type_='foreignkey'
        )
        batch_op.drop_column('deuda_trasladada_a_id')
        batch_op.drop_column('saldo_anterior')
