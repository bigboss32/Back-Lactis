"""adjuntos de reventa (soportes de transferencia en Cloudflare R2)

Tabla NUEVA y nada más: no toca ninguna tabla existente, así que corre igual
sobre una base con miles de compras y ventas cargadas. No hay backfill que
hacer porque hasta hoy no había adjuntos.

NO se guarda ninguna URL a propósito, solo `object_key`: las URLs se firman al
momento de pedirlas y caducan solas. Ver el docstring del modelo AdjuntoReventa.

El CHECK obliga a que el adjunto cuelgue de una compra O de una venta, nunca de
las dos ni de ninguna: una fila sin dueño sería un archivo huérfano ocupando
almacenamiento sin que nadie pueda verlo ni borrarlo desde la aplicación.

Revision ID: a7f2c4e9b3d1
Revises: a5e7c1b4d9f2
Create Date: 2026-08-02 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7f2c4e9b3d1'
down_revision: Union[str, None] = 'a5e7c1b4d9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'adjuntos_reventa',
        sa.Column('compra_id', sa.Uuid(), nullable=True),
        sa.Column('venta_id', sa.Uuid(), nullable=True),
        sa.Column('object_key', sa.String(length=500), nullable=False),
        sa.Column('nombre_archivo', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('tamano_bytes', sa.Integer(), nullable=False),
        sa.Column('subido_por_nombre', sa.String(length=150), nullable=True),
        sa.Column('empresa_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
        sa.CheckConstraint(
            '(compra_id IS NOT NULL AND venta_id IS NULL) '
            'OR (compra_id IS NULL AND venta_id IS NOT NULL)',
            name='ck_adjuntos_reventa_un_solo_dueno',
        ),
        sa.ForeignKeyConstraint(['compra_id'], ['compras_queso.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['venta_id'], ['ventas_queso.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.PrimaryKeyConstraint('id'),
        # Único: dos filas apuntando al mismo objeto harían que borrar una
        # dejara a la otra señalando un archivo que ya no existe.
        sa.UniqueConstraint('object_key', name='uq_adjuntos_reventa_object_key'),
    )
    op.create_index(op.f('ix_adjuntos_reventa_compra_id'), 'adjuntos_reventa', ['compra_id'], unique=False)
    op.create_index(op.f('ix_adjuntos_reventa_venta_id'), 'adjuntos_reventa', ['venta_id'], unique=False)
    op.create_index(op.f('ix_adjuntos_reventa_empresa_id'), 'adjuntos_reventa', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_adjuntos_reventa_estado'), 'adjuntos_reventa', ['estado'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_adjuntos_reventa_estado'), table_name='adjuntos_reventa')
    op.drop_index(op.f('ix_adjuntos_reventa_empresa_id'), table_name='adjuntos_reventa')
    op.drop_index(op.f('ix_adjuntos_reventa_venta_id'), table_name='adjuntos_reventa')
    op.drop_index(op.f('ix_adjuntos_reventa_compra_id'), table_name='adjuntos_reventa')
    op.drop_table('adjuntos_reventa')
