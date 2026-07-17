"""anticipos polimórficos (proveedor/transportador/empleado) + anticipos en nómina

Revision ID: d4e7b1a0f9c3
Revises: c3f8a2e1d9b4
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e7b1a0f9c3'
down_revision: Union[str, None] = 'c3f8a2e1d9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # anticipos: pasa a ser polimórfico
    op.add_column(
        'anticipos',
        sa.Column('tipo', sa.String(length=20), server_default='proveedor', nullable=False),
    )
    op.add_column('anticipos', sa.Column('transportador_id', sa.Uuid(), nullable=True))
    op.add_column('anticipos', sa.Column('empleado_id', sa.Uuid(), nullable=True))
    op.add_column('anticipos', sa.Column('pago_empleado_id', sa.Uuid(), nullable=True))
    op.alter_column('anticipos', 'proveedor_id', existing_type=sa.Uuid(), nullable=True)
    op.create_index(op.f('ix_anticipos_tipo'), 'anticipos', ['tipo'], unique=False)
    op.create_index(op.f('ix_anticipos_transportador_id'), 'anticipos', ['transportador_id'], unique=False)
    op.create_index(op.f('ix_anticipos_empleado_id'), 'anticipos', ['empleado_id'], unique=False)
    op.create_index(op.f('ix_anticipos_pago_empleado_id'), 'anticipos', ['pago_empleado_id'], unique=False)
    op.create_foreign_key('fk_anticipos_transportador', 'anticipos', 'transportadores', ['transportador_id'], ['id'])
    op.create_foreign_key('fk_anticipos_empleado', 'anticipos', 'empleados', ['empleado_id'], ['id'])
    op.create_foreign_key('fk_anticipos_pago_empleado', 'anticipos', 'pagos_empleado', ['pago_empleado_id'], ['id'])

    # nómina: registra los anticipos descontados en el pago
    op.add_column(
        'pagos_empleado',
        sa.Column('anticipos', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('pagos_empleado', 'anticipos')
    op.drop_constraint('fk_anticipos_pago_empleado', 'anticipos', type_='foreignkey')
    op.drop_constraint('fk_anticipos_empleado', 'anticipos', type_='foreignkey')
    op.drop_constraint('fk_anticipos_transportador', 'anticipos', type_='foreignkey')
    op.drop_index(op.f('ix_anticipos_pago_empleado_id'), table_name='anticipos')
    op.drop_index(op.f('ix_anticipos_empleado_id'), table_name='anticipos')
    op.drop_index(op.f('ix_anticipos_transportador_id'), table_name='anticipos')
    op.drop_index(op.f('ix_anticipos_tipo'), table_name='anticipos')
    op.alter_column('anticipos', 'proveedor_id', existing_type=sa.Uuid(), nullable=False)
    op.drop_column('anticipos', 'pago_empleado_id')
    op.drop_column('anticipos', 'empleado_id')
    op.drop_column('anticipos', 'transportador_id')
    op.drop_column('anticipos', 'tipo')
