"""destinatario en pagos de liquidacion

Revision ID: a5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-08-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5c6d7e8f9a0'
down_revision: Union[str, None] = 'c5d9e3a7b1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pagos_liquidacion', sa.Column('destinatario', sa.String(length=150), nullable=True))


def downgrade() -> None:
    op.drop_column('pagos_liquidacion', 'destinatario')