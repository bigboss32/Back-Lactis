"""gastos de venta en reventa

Revision ID: e1a2c7f0b3d5
Revises: d4e7b1a0f9c3
Create Date: 2026-07-19 05:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1a2c7f0b3d5'
down_revision: Union[str, None] = 'd4e7b1a0f9c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ventas_queso',
        sa.Column('gasto_concepto', sa.String(length=150), nullable=True),
    )
    op.add_column(
        'ventas_queso',
        sa.Column(
            'gasto_por_kilo',
            sa.Numeric(precision=12, scale=2),
            server_default='0',
            nullable=False,
        ),
    )
    op.add_column(
        'ventas_queso',
        sa.Column(
            'gasto_monto',
            sa.Numeric(precision=14, scale=2),
            server_default='0',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('ventas_queso', 'gasto_monto')
    op.drop_column('ventas_queso', 'gasto_por_kilo')
    op.drop_column('ventas_queso', 'gasto_concepto')
