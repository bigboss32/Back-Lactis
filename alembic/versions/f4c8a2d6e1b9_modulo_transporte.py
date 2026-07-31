"""módulo de transporte (la turbo)

Vehículos, viajes con servicios de flete (cartera y abonos por servicio),
gastos del vehículo (por viaje o generales), mantenimientos y documentos
legales con fecha de vencimiento. Libro independiente del contable de la
quesera, como el precedente del módulo reventa.

Revision ID: f4c8a2d6e1b9
Revises: e9a4b6c2f1d7
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4c8a2d6e1b9'
down_revision: Union[str, None] = 'e9a4b6c2f1d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('vehiculos',
    sa.Column('placa', sa.String(length=10), nullable=False),
    sa.Column('nombre', sa.String(length=80), nullable=True),
    sa.Column('marca', sa.String(length=80), nullable=True),
    sa.Column('linea', sa.String(length=80), nullable=True),
    sa.Column('anio', sa.Integer(), nullable=True),
    sa.Column('capacidad_kg', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('tarifa_kilo', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
    sa.Column('odometro_actual', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
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
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('empresa_id', 'placa', name='uq_vehiculo_placa')
    )
    op.create_index(op.f('ix_vehiculos_empresa_id'), 'vehiculos', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_vehiculos_estado'), 'vehiculos', ['estado'], unique=False)

    op.create_table('viajes',
    sa.Column('numero', sa.Integer(), nullable=False),
    sa.Column('vehiculo_id', sa.Uuid(), nullable=False),
    sa.Column('fecha_salida', sa.Date(), nullable=False),
    sa.Column('fecha_regreso', sa.Date(), nullable=True),
    sa.Column('origen', sa.String(length=120), nullable=False),
    sa.Column('destino', sa.String(length=120), nullable=False),
    sa.Column('conductor_nombre', sa.String(length=150), nullable=True),
    sa.Column('pago_conductor', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('odometro_salida', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('odometro_regreso', sa.Numeric(precision=12, scale=2), nullable=True),
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
    sa.ForeignKeyConstraint(['vehiculo_id'], ['vehiculos.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('empresa_id', 'numero', name='uq_viaje_numero')
    )
    op.create_index(op.f('ix_viajes_empresa_id'), 'viajes', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_viajes_estado'), 'viajes', ['estado'], unique=False)
    op.create_index(op.f('ix_viajes_fecha_salida'), 'viajes', ['fecha_salida'], unique=False)
    op.create_index(op.f('ix_viajes_vehiculo_id'), 'viajes', ['vehiculo_id'], unique=False)

    op.create_table('viaje_servicios',
    sa.Column('viaje_id', sa.Uuid(), nullable=False),
    sa.Column('sentido', sa.String(length=10), server_default='ida', nullable=False),
    sa.Column('tipo_cobro', sa.String(length=15), server_default='por_kilo', nullable=False),
    sa.Column('es_interno', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('cliente_id', sa.Uuid(), nullable=True),
    sa.Column('cliente_nombre', sa.String(length=150), nullable=True),
    sa.Column('descripcion', sa.String(length=200), nullable=False),
    sa.Column('kilos', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('tarifa_kilo', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('valor_total', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('abonado', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('observaciones', sa.String(length=500), nullable=True),
    sa.Column('empresa_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
    sa.ForeignKeyConstraint(['cliente_id'], ['clientes.id'], ),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.ForeignKeyConstraint(['viaje_id'], ['viajes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_viaje_servicios_cliente_id'), 'viaje_servicios', ['cliente_id'], unique=False)
    op.create_index(op.f('ix_viaje_servicios_empresa_id'), 'viaje_servicios', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_viaje_servicios_es_interno'), 'viaje_servicios', ['es_interno'], unique=False)
    op.create_index(op.f('ix_viaje_servicios_estado'), 'viaje_servicios', ['estado'], unique=False)
    op.create_index(op.f('ix_viaje_servicios_viaje_id'), 'viaje_servicios', ['viaje_id'], unique=False)

    op.create_table('abonos_flete',
    sa.Column('servicio_id', sa.Uuid(), nullable=False),
    sa.Column('fecha', sa.Date(), nullable=False),
    sa.Column('valor', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('metodo', sa.String(length=30), server_default='efectivo', nullable=False),
    sa.Column('referencia', sa.String(length=100), nullable=True),
    sa.Column('observaciones', sa.String(length=300), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
    sa.ForeignKeyConstraint(['servicio_id'], ['viaje_servicios.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_abonos_flete_estado'), 'abonos_flete', ['estado'], unique=False)
    op.create_index(op.f('ix_abonos_flete_servicio_id'), 'abonos_flete', ['servicio_id'], unique=False)

    op.create_table('vehiculo_gastos',
    sa.Column('vehiculo_id', sa.Uuid(), nullable=False),
    sa.Column('viaje_id', sa.Uuid(), nullable=True),
    sa.Column('fecha', sa.Date(), nullable=False),
    sa.Column('categoria', sa.String(length=30), nullable=False),
    sa.Column('concepto', sa.String(length=200), nullable=True),
    sa.Column('valor', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('odometro', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('adjunto_url', sa.String(length=300), nullable=True),
    sa.Column('empresa_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.ForeignKeyConstraint(['vehiculo_id'], ['vehiculos.id'], ),
    sa.ForeignKeyConstraint(['viaje_id'], ['viajes.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_vehiculo_gastos_categoria'), 'vehiculo_gastos', ['categoria'], unique=False)
    op.create_index(op.f('ix_vehiculo_gastos_empresa_id'), 'vehiculo_gastos', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_vehiculo_gastos_estado'), 'vehiculo_gastos', ['estado'], unique=False)
    op.create_index(op.f('ix_vehiculo_gastos_fecha'), 'vehiculo_gastos', ['fecha'], unique=False)
    op.create_index(op.f('ix_vehiculo_gastos_vehiculo_id'), 'vehiculo_gastos', ['vehiculo_id'], unique=False)
    op.create_index(op.f('ix_vehiculo_gastos_viaje_id'), 'vehiculo_gastos', ['viaje_id'], unique=False)

    op.create_table('vehiculo_mantenimientos',
    sa.Column('vehiculo_id', sa.Uuid(), nullable=False),
    sa.Column('fecha', sa.Date(), nullable=False),
    sa.Column('tipo', sa.String(length=20), server_default='preventivo', nullable=False),
    sa.Column('descripcion', sa.String(length=200), nullable=False),
    sa.Column('taller', sa.String(length=150), nullable=True),
    sa.Column('odometro', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('valor', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('proximo_odometro', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('proxima_fecha', sa.Date(), nullable=True),
    sa.Column('adjunto_url', sa.String(length=300), nullable=True),
    sa.Column('empresa_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.ForeignKeyConstraint(['vehiculo_id'], ['vehiculos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_vehiculo_mantenimientos_empresa_id'), 'vehiculo_mantenimientos', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_vehiculo_mantenimientos_estado'), 'vehiculo_mantenimientos', ['estado'], unique=False)
    op.create_index(op.f('ix_vehiculo_mantenimientos_fecha'), 'vehiculo_mantenimientos', ['fecha'], unique=False)
    op.create_index(op.f('ix_vehiculo_mantenimientos_vehiculo_id'), 'vehiculo_mantenimientos', ['vehiculo_id'], unique=False)

    op.create_table('vehiculo_documentos',
    sa.Column('vehiculo_id', sa.Uuid(), nullable=False),
    sa.Column('tipo', sa.String(length=20), nullable=False),
    sa.Column('descripcion', sa.String(length=200), nullable=True),
    sa.Column('numero', sa.String(length=50), nullable=True),
    sa.Column('fecha_expedicion', sa.Date(), nullable=True),
    sa.Column('fecha_vencimiento', sa.Date(), nullable=False),
    sa.Column('valor', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('adjunto_url', sa.String(length=300), nullable=True),
    sa.Column('empresa_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.ForeignKeyConstraint(['vehiculo_id'], ['vehiculos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_vehiculo_documentos_empresa_id'), 'vehiculo_documentos', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_vehiculo_documentos_estado'), 'vehiculo_documentos', ['estado'], unique=False)
    op.create_index(op.f('ix_vehiculo_documentos_fecha_vencimiento'), 'vehiculo_documentos', ['fecha_vencimiento'], unique=False)
    op.create_index(op.f('ix_vehiculo_documentos_tipo'), 'vehiculo_documentos', ['tipo'], unique=False)
    op.create_index(op.f('ix_vehiculo_documentos_vehiculo_id'), 'vehiculo_documentos', ['vehiculo_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_vehiculo_documentos_vehiculo_id'), table_name='vehiculo_documentos')
    op.drop_index(op.f('ix_vehiculo_documentos_tipo'), table_name='vehiculo_documentos')
    op.drop_index(op.f('ix_vehiculo_documentos_fecha_vencimiento'), table_name='vehiculo_documentos')
    op.drop_index(op.f('ix_vehiculo_documentos_estado'), table_name='vehiculo_documentos')
    op.drop_index(op.f('ix_vehiculo_documentos_empresa_id'), table_name='vehiculo_documentos')
    op.drop_table('vehiculo_documentos')
    op.drop_index(op.f('ix_vehiculo_mantenimientos_vehiculo_id'), table_name='vehiculo_mantenimientos')
    op.drop_index(op.f('ix_vehiculo_mantenimientos_fecha'), table_name='vehiculo_mantenimientos')
    op.drop_index(op.f('ix_vehiculo_mantenimientos_estado'), table_name='vehiculo_mantenimientos')
    op.drop_index(op.f('ix_vehiculo_mantenimientos_empresa_id'), table_name='vehiculo_mantenimientos')
    op.drop_table('vehiculo_mantenimientos')
    op.drop_index(op.f('ix_vehiculo_gastos_viaje_id'), table_name='vehiculo_gastos')
    op.drop_index(op.f('ix_vehiculo_gastos_vehiculo_id'), table_name='vehiculo_gastos')
    op.drop_index(op.f('ix_vehiculo_gastos_fecha'), table_name='vehiculo_gastos')
    op.drop_index(op.f('ix_vehiculo_gastos_estado'), table_name='vehiculo_gastos')
    op.drop_index(op.f('ix_vehiculo_gastos_empresa_id'), table_name='vehiculo_gastos')
    op.drop_index(op.f('ix_vehiculo_gastos_categoria'), table_name='vehiculo_gastos')
    op.drop_table('vehiculo_gastos')
    op.drop_index(op.f('ix_abonos_flete_servicio_id'), table_name='abonos_flete')
    op.drop_index(op.f('ix_abonos_flete_estado'), table_name='abonos_flete')
    op.drop_table('abonos_flete')
    op.drop_index(op.f('ix_viaje_servicios_viaje_id'), table_name='viaje_servicios')
    op.drop_index(op.f('ix_viaje_servicios_estado'), table_name='viaje_servicios')
    op.drop_index(op.f('ix_viaje_servicios_es_interno'), table_name='viaje_servicios')
    op.drop_index(op.f('ix_viaje_servicios_empresa_id'), table_name='viaje_servicios')
    op.drop_index(op.f('ix_viaje_servicios_cliente_id'), table_name='viaje_servicios')
    op.drop_table('viaje_servicios')
    op.drop_index(op.f('ix_viajes_vehiculo_id'), table_name='viajes')
    op.drop_index(op.f('ix_viajes_fecha_salida'), table_name='viajes')
    op.drop_index(op.f('ix_viajes_estado'), table_name='viajes')
    op.drop_index(op.f('ix_viajes_empresa_id'), table_name='viajes')
    op.drop_table('viajes')
    op.drop_index(op.f('ix_vehiculos_estado'), table_name='vehiculos')
    op.drop_index(op.f('ix_vehiculos_empresa_id'), table_name='vehiculos')
    op.drop_table('vehiculos')
