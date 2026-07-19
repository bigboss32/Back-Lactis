"""destino (borona/merma) en conversiones de reventa

Revision ID: f2b3d8a1c6e7
Revises: e1a2c7f0b3d5
Create Date: 2026-07-19 07:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2b3d8a1c6e7'
down_revision: Union[str, None] = 'e1a2c7f0b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'conversiones_borona',
        sa.Column(
            'destino', sa.String(length=20), server_default='borona', nullable=False
        ),
    )
    op.create_index(
        op.f('ix_conversiones_borona_destino'),
        'conversiones_borona',
        ['destino'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_conversiones_borona_destino'), table_name='conversiones_borona')
    op.drop_column('conversiones_borona', 'destino')
