"""cierre de ciclo de despacho (la merma del queso que se seca)

El queso se pesa dos veces: al hacerlo y al venderlo. Entre las dos se seca y
pierde peso. Como en Bogotá se vende por kilos sin saber de qué tanda salieron,
esa diferencia se quedaba en la cola FIFO como queso en bodega que no existe,
con su costo, inflando el inventario y la utilidad.

Estas dos tablas guardan el CIERRE DE CICLO: la cuenta que el dueño aceptó
(producido − vendido − lo ya bajado a mano = merma) y cómo se repartió esa merma
entre las tandas del ciclo. La merma en sí se registra como ajustes de
inventario normales, así que no hay columnas nuevas en ninguna tabla existente.

`ciclos_despacho` SÍ lleva columnas de plata, al revés que `temporadas`: cerrar
un ciclo escribe ajustes de inventario, y hay que poder auditar qué se aceptó y
cuándo. Ver el comentario del modelo CicloDespacho.

Migración aditiva: solo crea tablas. No toca ninguna fila existente, así que
corre igual sobre una base con datos del cliente.

Revision ID: a1c4e8b7d2f6
Revises: b3d9f1e5c7a2
Create Date: 2026-08-02 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c4e8b7d2f6'
down_revision: Union[str, None] = 'b3d9f1e5c7a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ciclos_despacho',
    sa.Column('nombre', sa.String(length=80), nullable=False),
    sa.Column('fecha_inicio', sa.Date(), nullable=False),
    sa.Column('fecha_fin', sa.Date(), nullable=False),
    # NULL = ciclo abierto (existe con su rango, pero sin merma aplicada). Es el
    # estado en que queda uno que se reabrió.
    sa.Column('cerrado_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notas', sa.String(length=500), nullable=True),
    # La foto de la cuenta que se aceptó al cerrar
    sa.Column('kilos_producidos', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('kilos_vendidos', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('kilos_ajuste_manual', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('kilos_merma', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('costo_merma', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('advertencias', sa.String(length=1000), nullable=True),
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
    op.create_index(op.f('ix_ciclos_despacho_empresa_id'), 'ciclos_despacho', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_ciclos_despacho_estado'), 'ciclos_despacho', ['estado'], unique=False)
    op.create_index(op.f('ix_ciclos_despacho_fecha_inicio'), 'ciclos_despacho', ['fecha_inicio'], unique=False)
    op.create_index(op.f('ix_ciclos_despacho_fecha_fin'), 'ciclos_despacho', ['fecha_fin'], unique=False)
    op.create_index(op.f('ix_ciclos_despacho_cerrado_at'), 'ciclos_despacho', ['cerrado_at'], unique=False)

    op.create_table('ciclos_despacho_lotes',
    sa.Column('ciclo_id', sa.Uuid(), nullable=False),
    sa.Column('produccion_id', sa.Uuid(), nullable=False),
    sa.Column('tipo_queso_id', sa.Uuid(), nullable=False),
    # El ajuste de inventario que bajó estos kilos. Se guarda el vínculo, y no
    # solo el texto de la referencia, para que reabrir deshaga exactamente los
    # mismos movimientos y ninguno más.
    sa.Column('movimiento_id', sa.Uuid(), nullable=True),
    sa.Column('fecha_produccion', sa.Date(), nullable=False),
    sa.Column('kilos_producidos', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('kilos_merma', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('costo_merma', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('empresa_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
    sa.ForeignKeyConstraint(['ciclo_id'], ['ciclos_despacho.id'], ),
    sa.ForeignKeyConstraint(['produccion_id'], ['producciones.id'], ),
    sa.ForeignKeyConstraint(['tipo_queso_id'], ['tipos_queso.id'], ),
    sa.ForeignKeyConstraint(['movimiento_id'], ['movimientos_inventario.id'], ),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ciclos_despacho_lotes_empresa_id'), 'ciclos_despacho_lotes', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_ciclos_despacho_lotes_estado'), 'ciclos_despacho_lotes', ['estado'], unique=False)
    op.create_index(op.f('ix_ciclos_despacho_lotes_ciclo_id'), 'ciclos_despacho_lotes', ['ciclo_id'], unique=False)
    op.create_index(op.f('ix_ciclos_despacho_lotes_produccion_id'), 'ciclos_despacho_lotes', ['produccion_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ciclos_despacho_lotes_produccion_id'), table_name='ciclos_despacho_lotes')
    op.drop_index(op.f('ix_ciclos_despacho_lotes_ciclo_id'), table_name='ciclos_despacho_lotes')
    op.drop_index(op.f('ix_ciclos_despacho_lotes_estado'), table_name='ciclos_despacho_lotes')
    op.drop_index(op.f('ix_ciclos_despacho_lotes_empresa_id'), table_name='ciclos_despacho_lotes')
    op.drop_table('ciclos_despacho_lotes')
    op.drop_index(op.f('ix_ciclos_despacho_cerrado_at'), table_name='ciclos_despacho')
    op.drop_index(op.f('ix_ciclos_despacho_fecha_fin'), table_name='ciclos_despacho')
    op.drop_index(op.f('ix_ciclos_despacho_fecha_inicio'), table_name='ciclos_despacho')
    op.drop_index(op.f('ix_ciclos_despacho_estado'), table_name='ciclos_despacho')
    op.drop_index(op.f('ix_ciclos_despacho_empresa_id'), table_name='ciclos_despacho')
    op.drop_table('ciclos_despacho')
