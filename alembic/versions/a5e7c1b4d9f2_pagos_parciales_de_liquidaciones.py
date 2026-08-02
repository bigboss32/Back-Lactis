"""pagos parciales (abonos) en las liquidaciones

Agrega la columna `pagado` a liquidaciones y la tabla `pagos_liquidacion`, y
deja coherentes las liquidaciones que YA ESTÁN PAGADAS en la base del cliente.

Dos cuidados, porque esto corre sobre una tabla que ya tiene filas y plata de
verdad adentro:

· `pagado` es NOT NULL, así que va con server_default '0'. Sin el default, el
  ALTER TABLE revienta en la primera liquidación existente.
· Las que hoy están en 'pagada' quedaban con `pagado` en cero y `saldo` con el
  neto completo, o sea "pagada pero debiendo todo": el tablero y la contabilidad
  las habrían vuelto a contar como deuda por pagar. Se les pone pagado = neto a
  pagar (valor_total - anticipos) y saldo = 0.

NO se les inventa un renglón en `pagos_liquidacion`: no sabemos ni la fecha ni
en cuántas partes se pagaron, y un historial inventado es peor que uno vacío
para alguien que cuadra las cifras a mano contra su cuaderno. El candado de
Recepción diaria las sigue trabando por su estado 'pagada'.

Revision ID: a5e7c1b4d9f2
Revises: d1f6a3c8b2e5
Create Date: 2026-08-01 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5e7c1b4d9f2'
down_revision: Union[str, None] = 'd1f6a3c8b2e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'liquidaciones',
        sa.Column(
            'pagado', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False
        ),
    )

    op.create_table(
        'pagos_liquidacion',
        sa.Column('liquidacion_id', sa.Uuid(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('valor', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('observaciones', sa.String(length=300), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
        sa.ForeignKeyConstraint(['liquidacion_id'], ['liquidaciones.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_pagos_liquidacion_estado'), 'pagos_liquidacion', ['estado'], unique=False
    )
    op.create_index(
        op.f('ix_pagos_liquidacion_liquidacion_id'),
        'pagos_liquidacion',
        ['liquidacion_id'],
        unique=False,
    )

    # Las que ya estaban pagadas quedan cuadradas: pagado = lo que se le entregó
    # (valor_total - anticipos) y saldo = 0, que es la igualdad que ahora se
    # cumple siempre (neto a pagar = pagado + saldo). Van también las borradas
    # con soft delete: si alguna se restaura, tiene que volver coherente.
    op.execute(
        """
        UPDATE liquidaciones
           SET pagado = COALESCE(valor_total, 0) - COALESCE(anticipos, 0),
               saldo = 0
         WHERE estado = 'pagada'
        """
    )


def downgrade() -> None:
    # Al volver atrás, `saldo` recupera su sentido viejo (el neto a pagar, sin
    # descontar lo abonado); si no, las liquidaciones a medio pagar se leerían
    # como si se les debiera menos de lo que dice el comprobante viejo.
    op.execute(
        """
        UPDATE liquidaciones
           SET saldo = COALESCE(valor_total, 0) - COALESCE(anticipos, 0)
        """
    )
    op.drop_index(op.f('ix_pagos_liquidacion_liquidacion_id'), table_name='pagos_liquidacion')
    op.drop_index(op.f('ix_pagos_liquidacion_estado'), table_name='pagos_liquidacion')
    op.drop_table('pagos_liquidacion')
    op.drop_column('liquidaciones', 'pagado')
