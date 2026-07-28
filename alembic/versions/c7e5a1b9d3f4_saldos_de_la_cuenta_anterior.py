"""saldos de la cuenta anterior (libro del sistema viejo)

Revision ID: c7e5a1b9d3f4
Revises: b4d1f7e9c2a6
Create Date: 2026-07-27 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7e5a1b9d3f4'
down_revision: Union[str, None] = 'b4d1f7e9c2a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('saldos_anteriores',
    sa.Column('tipo', sa.String(length=20), server_default='cobrar', nullable=False),
    sa.Column('tercero', sa.String(length=150), nullable=False),
    sa.Column('fecha', sa.Date(), nullable=False),
    sa.Column('concepto', sa.String(length=200), nullable=False),
    sa.Column('valor_total', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('abonado', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('observaciones', sa.String(length=500), nullable=True),
    sa.Column('empresa_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_saldos_anteriores_empresa_id'), 'saldos_anteriores', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_saldos_anteriores_estado'), 'saldos_anteriores', ['estado'], unique=False)
    op.create_index(op.f('ix_saldos_anteriores_fecha'), 'saldos_anteriores', ['fecha'], unique=False)
    op.create_index(op.f('ix_saldos_anteriores_tipo'), 'saldos_anteriores', ['tipo'], unique=False)
    op.create_table('abonos_saldo_anterior',
    sa.Column('saldo_id', sa.Uuid(), nullable=False),
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
    sa.ForeignKeyConstraint(['saldo_id'], ['saldos_anteriores.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_abonos_saldo_anterior_estado'), 'abonos_saldo_anterior', ['estado'], unique=False)
    op.create_index(op.f('ix_abonos_saldo_anterior_saldo_id'), 'abonos_saldo_anterior', ['saldo_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_abonos_saldo_anterior_saldo_id'), table_name='abonos_saldo_anterior')
    op.drop_index(op.f('ix_abonos_saldo_anterior_estado'), table_name='abonos_saldo_anterior')
    op.drop_table('abonos_saldo_anterior')
    op.drop_index(op.f('ix_saldos_anteriores_tipo'), table_name='saldos_anteriores')
    op.drop_index(op.f('ix_saldos_anteriores_fecha'), table_name='saldos_anteriores')
    op.drop_index(op.f('ix_saldos_anteriores_estado'), table_name='saldos_anteriores')
    op.drop_index(op.f('ix_saldos_anteriores_empresa_id'), table_name='saldos_anteriores')
    op.drop_table('saldos_anteriores')
