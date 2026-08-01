"""suscripcion wompi

Cobro mensual por empresa con Wompi: columnas de suscripción en empresas
(tarifa propia, exención y vigencia pagada), fuentes de pago tokenizadas y
pagos de suscripción.

Backfill: las empresas existentes reciben pagada_hasta = hoy + 30 días para
que NADIE amanezca bloqueado al desplegar; a partir de ahí el ciclo normal de
cobro toma el control.

El índice único PARCIAL de pagos_suscripcion (un solo PENDING por empresa) es
el candado real contra el doble cobro: dos cobros concurrentes chocan en el
INSERT, no en una guarda de aplicación. Mismo patrón postgresql_where +
sqlite_where de la migración de roles por empresa.

Revision ID: c9d2e8f4a7b1
Revises: b8e2d4f6a3c1
Create Date: 2026-07-31 00:00:00.000000

"""
from datetime import date, timedelta
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d2e8f4a7b1'
down_revision: Union[str, None] = 'b8e2d4f6a3c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- empresas: tarifa propia (NULL = tarifa global), exención y vigencia ---
    op.add_column('empresas', sa.Column('tarifa_mensual', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column('empresas', sa.Column('exenta', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('empresas', sa.Column('pagada_hasta', sa.Date(), nullable=True))
    op.execute(
        sa.text("UPDATE empresas SET pagada_hasta = :limite WHERE pagada_hasta IS NULL").bindparams(
            limite=date.today() + timedelta(days=30)
        )
    )

    # --- fuentes de pago (tarjeta tokenizada en Wompi; el PAN nunca se guarda) ---
    op.create_table('fuentes_pago_suscripcion',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('empresa_id', sa.Uuid(), nullable=False),
    sa.Column('wompi_payment_source_id', sa.BigInteger(), nullable=False),
    sa.Column('marca', sa.String(length=30), nullable=True),
    sa.Column('ultimos4', sa.String(length=4), nullable=True),
    sa.Column('exp_mes', sa.String(length=2), nullable=True),
    sa.Column('exp_anio', sa.String(length=2), nullable=True),
    sa.Column('customer_email', sa.String(length=150), nullable=True),
    sa.Column('detalle', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_fuentes_pago_suscripcion_empresa_id'), 'fuentes_pago_suscripcion', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_fuentes_pago_suscripcion_estado'), 'fuentes_pago_suscripcion', ['estado'], unique=False)

    # --- pagos de suscripción ---
    op.create_table('pagos_suscripcion',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('empresa_id', sa.Uuid(), nullable=False),
    sa.Column('fuente_pago_id', sa.Uuid(), nullable=True),
    sa.Column('referencia', sa.String(length=100), nullable=False),
    sa.Column('wompi_transaction_id', sa.String(length=100), nullable=True),
    sa.Column('monto', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('moneda', sa.String(length=3), server_default='COP', nullable=False),
    sa.Column('estado_transaccion', sa.String(length=20), server_default='PENDING', nullable=False),
    sa.Column('origen', sa.String(length=20), nullable=False),
    sa.Column('periodo_desde', sa.Date(), nullable=True),
    sa.Column('periodo_hasta', sa.Date(), nullable=True),
    sa.Column('detalle', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.ForeignKeyConstraint(['fuente_pago_id'], ['fuentes_pago_suscripcion.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('referencia'),
    sa.UniqueConstraint('wompi_transaction_id')
    )
    op.create_index(op.f('ix_pagos_suscripcion_empresa_id'), 'pagos_suscripcion', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_pagos_suscripcion_fuente_pago_id'), 'pagos_suscripcion', ['fuente_pago_id'], unique=False)
    op.create_index(op.f('ix_pagos_suscripcion_estado_transaccion'), 'pagos_suscripcion', ['estado_transaccion'], unique=False)
    op.create_index(op.f('ix_pagos_suscripcion_estado'), 'pagos_suscripcion', ['estado'], unique=False)
    # Candado anti doble cobro: UN solo pago PENDING por empresa
    op.create_index(
        'uq_pago_suscripcion_pending', 'pagos_suscripcion', ['empresa_id'],
        unique=True,
        postgresql_where=sa.text("estado_transaccion = 'PENDING'"),
        sqlite_where=sa.text("estado_transaccion = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index('uq_pago_suscripcion_pending', table_name='pagos_suscripcion')
    op.drop_index(op.f('ix_pagos_suscripcion_estado'), table_name='pagos_suscripcion')
    op.drop_index(op.f('ix_pagos_suscripcion_estado_transaccion'), table_name='pagos_suscripcion')
    op.drop_index(op.f('ix_pagos_suscripcion_fuente_pago_id'), table_name='pagos_suscripcion')
    op.drop_index(op.f('ix_pagos_suscripcion_empresa_id'), table_name='pagos_suscripcion')
    op.drop_table('pagos_suscripcion')
    op.drop_index(op.f('ix_fuentes_pago_suscripcion_estado'), table_name='fuentes_pago_suscripcion')
    op.drop_index(op.f('ix_fuentes_pago_suscripcion_empresa_id'), table_name='fuentes_pago_suscripcion')
    op.drop_table('fuentes_pago_suscripcion')
    op.drop_column('empresas', 'pagada_hasta')
    op.drop_column('empresas', 'exenta')
    op.drop_column('empresas', 'tarifa_mensual')
