"""corregir también los anticipos de una quincena ya pagada

LO QUE PIDIÓ EL DUEÑO: "que cuando le dé corregir también me permita corregir los
anticipos; cuando ya se cerró, entonces toca corregir también los anticipos".

Y tenía razón por partida doble: el mensaje que bloquea un anticipo de una quincena
corregida ya decía "use Corregir esta quincena", pero esa pantalla solo sabía de días.
Prometía algo que no existía.

TRES COLUMNAS, NINGUNA DESTRUCTIVA. No toca ni una columna de plata existente, no mueve
una cifra y no tiene backfill que hacer.

`anticipos_antes` y `anticipos_despues` NACEN ANULABLES Y SIN DEFAULT, a propósito. Las
correcciones que ya están guardadas se hicieron cuando los anticipos no se podían tocar,
así que esa cifra no se registró: ponerles `0` sería AFIRMAR que los anticipos eran cero,
y en una quincena con $300.000 de adelanto eso es una mentira en el único renglón que
explica por qué el papel del productor dice otra cifra. El nulo dice lo que de verdad
pasa: no se sabe, y no se sabía.

Se guardan aunque se puedan deducir (neto = valor_total − anticipos − saldo_anterior)
porque esa deducción necesita el `saldo_anterior` de ese momento, que no vive en este
renglón. Es la lección que este proyecto ya pagó varias veces: un hecho no se deduce de
lo que sobrevive, se guarda cuando se sabe.

Revision ID: e4a8c1f7b2d9
Revises: d7f2a9c4e8b3
Create Date: 2026-09-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4a8c1f7b2d9'
down_revision: Union[str, None] = 'd7f2a9c4e8b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'liquidaciones_correcciones',
        sa.Column('anticipos_antes', sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        'liquidaciones_correcciones',
        sa.Column('anticipos_despues', sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        'liquidaciones_correcciones',
        sa.Column('anticipos_cambiados', sa.JSON(), nullable=True),
    )
    # DE QUÉ QUINCENA LO SACÓ UNA CORRECCIÓN. Anulable, y nulo en todos los anticipos que
    # ya existen — que es la verdad: ninguno salió de una corrección, porque hasta ahora
    # no se podía. Es lo que distingue el adelanto "recién registrado" del adelanto "YA
    # IMPRESO en un comprobante entregado", que hasta hoy se veían iguales: los dos con
    # `liquidacion_id` en nulo. Por eso el segundo se podía borrar desde la pantalla de
    # anticipos y $300.000 ya entregados dejaban de cobrarse. Ver el modelo `Anticipo`.
    op.add_column(
        'anticipos',
        sa.Column('soltado_de_liquidacion_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        'fk_anticipos_soltado_de_liquidacion',
        'anticipos', 'liquidaciones', ['soltado_de_liquidacion_id'], ['id'],
    )
    op.create_index(
        op.f('ix_anticipos_soltado_de_liquidacion_id'),
        'anticipos', ['soltado_de_liquidacion_id'], unique=False,
    )


def downgrade() -> None:
    # Al bajar se pierde QUÉ ANTICIPO se movió y a cuánto — plata que se entregó en la
    # mano. Las quincenas conservan su columna `anticipos` con la cifra corregida (esa
    # plata se movió de verdad), pero el renglón deja de poder explicarla. Si hay que
    # revertir con correcciones de anticipos ya hechas, exportar la tabla primero.
    # `batch_alter_table` también en anticipos: en SQLite, quitar una columna nombrada en
    # una llave foránea exige rehacer la tabla entera. En Postgres es un ALTER normal.
    with op.batch_alter_table('anticipos') as batch:
        batch.drop_index(op.f('ix_anticipos_soltado_de_liquidacion_id'))
        batch.drop_constraint('fk_anticipos_soltado_de_liquidacion', type_='foreignkey')
        batch.drop_column('soltado_de_liquidacion_id')
    with op.batch_alter_table('liquidaciones_correcciones') as batch:
        batch.drop_column('anticipos_cambiados')
        batch.drop_column('anticipos_despues')
        batch.drop_column('anticipos_antes')
