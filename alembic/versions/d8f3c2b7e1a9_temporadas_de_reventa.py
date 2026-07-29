"""temporadas de reventa (ciclos de compra y venta con nombre y fechas)

La tabla NO lleva ninguna columna de plata a propósito: la ganancia de la
temporada se calcula con el motor del resumen sobre fecha_inicio..fecha_fin.
Ver el comentario del modelo Temporada.

fecha_fin en NULL = temporada abierta (la que está corriendo).

Revision ID: d8f3c2b7e1a9
Revises: c7e5a1b9d3f4
Create Date: 2026-07-29 00:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8f3c2b7e1a9'
down_revision: Union[str, None] = 'c7e5a1b9d3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('temporadas',
    sa.Column('nombre', sa.String(length=80), nullable=False),
    sa.Column('fecha_inicio', sa.Date(), nullable=False),
    sa.Column('fecha_fin', sa.Date(), nullable=True),
    sa.Column('notas', sa.String(length=500), nullable=True),
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
    op.create_index(op.f('ix_temporadas_empresa_id'), 'temporadas', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_temporadas_estado'), 'temporadas', ['estado'], unique=False)
    op.create_index(op.f('ix_temporadas_fecha_fin'), 'temporadas', ['fecha_fin'], unique=False)
    op.create_index(op.f('ix_temporadas_fecha_inicio'), 'temporadas', ['fecha_inicio'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_temporadas_fecha_inicio'), table_name='temporadas')
    op.drop_index(op.f('ix_temporadas_fecha_fin'), table_name='temporadas')
    op.drop_index(op.f('ix_temporadas_estado'), table_name='temporadas')
    op.drop_index(op.f('ix_temporadas_empresa_id'), table_name='temporadas')
    op.drop_table('temporadas')
