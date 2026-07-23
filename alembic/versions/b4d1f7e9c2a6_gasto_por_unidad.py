"""cantidad y precio unitario en gastos (flete por kilo)

Revision ID: b4d1f7e9c2a6
Revises: a3c9e5f1d8b2
Create Date: 2026-07-20 07:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4d1f7e9c2a6'
down_revision: Union[str, None] = 'a3c9e5f1d8b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('gastos', sa.Column('cantidad', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column(
        'gastos', sa.Column('precio_unitario', sa.Numeric(precision=12, scale=2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('gastos', 'precio_unitario')
    op.drop_column('gastos', 'cantidad')
