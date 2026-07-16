"""nómina empleados: valor_dia + pagos_empleado

Revision ID: c3f8a2e1d9b4
Revises: b7d4f1a9c3e2
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f8a2e1d9b4'
down_revision: Union[str, None] = 'b7d4f1a9c3e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('empleados', sa.Column('valor_dia', sa.Numeric(precision=14, scale=2), nullable=True))
    op.create_table(
        'pagos_empleado',
        sa.Column('empleado_id', sa.Uuid(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('periodo', sa.String(length=100), nullable=True),
        sa.Column('dias_trabajados', sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column('valor_dia', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('total', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('observaciones', sa.String(length=300), nullable=True),
        sa.Column('empresa_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
        sa.ForeignKeyConstraint(['empleado_id'], ['empleados.id'], ),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pagos_empleado_empleado_id'), 'pagos_empleado', ['empleado_id'], unique=False)
    op.create_index(op.f('ix_pagos_empleado_empresa_id'), 'pagos_empleado', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_pagos_empleado_estado'), 'pagos_empleado', ['estado'], unique=False)
    op.create_index(op.f('ix_pagos_empleado_fecha'), 'pagos_empleado', ['fecha'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pagos_empleado_fecha'), table_name='pagos_empleado')
    op.drop_index(op.f('ix_pagos_empleado_estado'), table_name='pagos_empleado')
    op.drop_index(op.f('ix_pagos_empleado_empresa_id'), table_name='pagos_empleado')
    op.drop_index(op.f('ix_pagos_empleado_empleado_id'), table_name='pagos_empleado')
    op.drop_table('pagos_empleado')
    op.drop_column('empleados', 'valor_dia')
