"""precio por kilo en conversiones a borona

Revision ID: a3c9e5f1d8b2
Revises: f2b3d8a1c6e7
Create Date: 2026-07-20 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3c9e5f1d8b2'
down_revision: Union[str, None] = 'f2b3d8a1c6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'conversiones_borona',
        sa.Column(
            'precio_kilo',
            sa.Numeric(precision=12, scale=2),
            server_default='0',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('conversiones_borona', 'precio_kilo')
