"""soportes de los pagos de liquidación (la foto de la transferencia, en R2)

LO QUE PIDIÓ EL DUEÑO: "que se le puedan agregar los comprobantes a los pagos de
los proveedores". Hasta hoy solo reventa tenía adjuntos; las liquidaciones de
leche y de flete no, y son justo las que mueven la plata de los productores y de
los transportadores.

TABLA NUEVA Y NADA MÁS. No toca ninguna tabla existente, no agrega ni quita
columnas, no mueve una sola cifra y no tiene backfill que hacer: hasta hoy no
había soportes de pagos de liquidación que migrar. Corre igual sobre una base con
miles de liquidaciones cargadas y sobre una vacía, y es lo que se puede afirmar
sin ninguna letra menuda sobre la base de un cliente real: ninguna liquidación
cambia de estado, de saldo ni de pagado por esto.

SIRVE PARA LAS DOS CLASES DE LIQUIDACIÓN. Cuelga de `pagos_liquidacion`, que es la
tabla que usan tanto la liquidación de LECHE (al productor) como la de FLETE (al
transportador), así que con una sola tabla quedan cubiertas las dos.

NO SE GUARDA NINGUNA URL, solo `object_key`: las URLs se firman en el momento de
pedirlas y caducan solas. Ver el docstring del modelo `AdjuntoPagoLiquidacion`,
que es donde está escrito el porqué completo — una URL guardada en una columna es
un permiso permanente sobre el comprobante de una transferencia real.

LA LLAVE LLEVA EL empresa_id ADENTRO
(`{empresa_id}/liquidaciones/pagos/{pago_id}/{uuid}.jpg`) y la tabla lleva ADEMÁS
su propia columna `empresa_id`, aunque su padre —el pago— no la tenga. La razón
está en el modelo: al soporte se entra directo por su id para compartirlo y para
borrarlo, sin ningún padre en la ruta, así que sin la columna el aislamiento entre
las dos queseras dependería de acordarse de escribir un JOIN en cada consulta.

ON DELETE CASCADE contra el pago: es la red de abajo. El servicio borra las filas
Y los archivos del bucket antes de borrar el pago, porque una fila que se va sin su
archivo deja el archivo cobrando almacenamiento sin que nadie pueda verlo ni
borrarlo.

Revision ID: b3d9f6c2a8e1
Revises: f1b7d3a9c5e4
Create Date: 2026-09-05 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3d9f6c2a8e1'
down_revision: Union[str, None] = 'f1b7d3a9c5e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'adjuntos_pago_liquidacion',
        sa.Column('pago_id', sa.Uuid(), nullable=False),
        sa.Column('object_key', sa.String(length=500), nullable=False),
        sa.Column('nombre_archivo', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        # El peso de lo que QUEDÓ en el bucket, ya comprimido: es lo que se paga.
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
        sa.ForeignKeyConstraint(['pago_id'], ['pagos_liquidacion.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.PrimaryKeyConstraint('id'),
        # Único: dos filas apuntando al mismo objeto harían que borrar una dejara
        # a la otra señalando un archivo que ya no existe.
        sa.UniqueConstraint('object_key', name='uq_adjuntos_pago_liq_object_key'),
    )
    op.create_index(
        op.f('ix_adjuntos_pago_liquidacion_pago_id'),
        'adjuntos_pago_liquidacion', ['pago_id'], unique=False,
    )
    op.create_index(
        op.f('ix_adjuntos_pago_liquidacion_empresa_id'),
        'adjuntos_pago_liquidacion', ['empresa_id'], unique=False,
    )
    op.create_index(
        op.f('ix_adjuntos_pago_liquidacion_estado'),
        'adjuntos_pago_liquidacion', ['estado'], unique=False,
    )


def downgrade() -> None:
    # OJO al bajar: esto borra las FILAS, no los archivos del bucket. Si algún día
    # hay que revertir con soportes ya subidos, hay que anotarse las `object_key`
    # antes — si no, quedan objetos en R2 que nadie va a poder nombrar ni borrar.
    op.drop_index(
        op.f('ix_adjuntos_pago_liquidacion_estado'), table_name='adjuntos_pago_liquidacion'
    )
    op.drop_index(
        op.f('ix_adjuntos_pago_liquidacion_empresa_id'), table_name='adjuntos_pago_liquidacion'
    )
    op.drop_index(
        op.f('ix_adjuntos_pago_liquidacion_pago_id'), table_name='adjuntos_pago_liquidacion'
    )
    op.drop_table('adjuntos_pago_liquidacion')
