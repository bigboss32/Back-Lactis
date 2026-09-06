"""corregir una quincena ya pagada: versión del comprobante y renglón de corrección

LO QUE PIDIÓ EL DUEÑO: "que si soy administrador de empresa pueda editar la liquidación
que ya está pagada, es que se le olvidó un detalle y tiene que editarla".

Corregir una quincena pagada tiene una consecuencia que no es de software: EL PRODUCTOR
YA TIENE UN PAPEL EN LA MANO con la cifra vieja. Todo lo que agrega esta migración
existe para que el papel nuevo se pueda distinguir del viejo y para que quede escrito
quién cambió qué, cuándo y por qué. Sin eso, corregir una pagada sería exactamente la
operación que sirve para tapar plata.

NO TOCA NI UNA COLUMNA DE PLATA EXISTENTE, no mueve una sola cifra, no cambia el estado
de ningún documento y no tiene backfill que hacer. Agrega dos columnas anulables o con
default y una tabla nueva vacía. Corre igual sobre la base del cliente con miles de
liquidaciones cargadas y sobre una vacía.

LAS TRES PIEZAS:

1) `liquidaciones.version` — INTEGER NOT NULL, arranca en 1 con server_default. TODAS
   las filas que ya existen quedan en la v1 y siguen imprimiendo su folio pelado
   ('A3F2B1C9'), así que NINGÚN comprobante ya entregado cambia de nombre por esto. Es
   la pieza que hace posible el resto: desde la v2 el folio sale 'A3F2B1C9-v2' y las dos
   hojas dejan de llamarse igual.

2) `liquidaciones.fecha_primera_impresion` — anulable. Hoy el "Emitido" del comprobante
   es la hora en que se baja el PDF y no se guarda: dos reimpresiones del MISMO
   comprobante intacto ya salen con horas distintas, así que no hay forma de escribir
   "reemplaza al comprobante emitido el 15/06/2026 09:32". El nulo también dice algo: si
   nunca se imprimió, no hay papel que recoger.

3) `liquidaciones_correcciones` — una fila por corrección. Las cifras van en columnas
   Numeric(14,2) y NO dentro del JSON, a propósito: son las que el dueño suma con
   calculadora contra la hoja vieja y las que un reporte va a querer leer en SQL. En el
   JSON van solo los desgloses de largo variable (qué días entraron, qué precios se
   corrigieron). El `motivo` es NOT NULL y SIN default: una corrección sin motivo escrito
   no se distingue de un error.

ON DELETE CASCADE contra la liquidación, igual que los soportes de pago: es la red de
abajo. Borrar una liquidación pagada ya rebota por dos guardias distintos, así que en la
práctica no debería llegar ahí nunca.

Revision ID: d7f2a9c4e8b3
Revises: c8e4a2b7d9f1
Create Date: 2026-09-06 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7f2a9c4e8b3'
down_revision: Union[str, None] = 'c8e4a2b7d9f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `server_default='1'` y no solo el default de Python: sin él, las filas que YA
    # existen quedarían en NULL y el modelo —que la declara NOT NULL— reventaría en
    # TODAS las lecturas de liquidaciones, no solo en las corregidas.
    op.add_column(
        'liquidaciones',
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    )
    op.add_column(
        'liquidaciones',
        sa.Column('fecha_primera_impresion', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'liquidaciones_correcciones',
        sa.Column('liquidacion_id', sa.Uuid(), nullable=False),
        sa.Column('version_nueva', sa.Integer(), nullable=False),
        # Sin default: el servicio lo exige no vacío tras strip, y una corrección sin
        # motivo no debe poder existir ni por una inserción a mano.
        sa.Column('motivo', sa.String(length=500), nullable=False),
        sa.Column('corregido_por_nombre', sa.String(length=150), nullable=True),
        # La foto de la plata, antes y después. En columnas y no en el JSON.
        sa.Column('valor_total_antes', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('valor_total_despues', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('neto_antes', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('neto_despues', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('pagado_al_momento', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('saldo_antes', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('saldo_despues', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('estado_antes', sa.String(length=20), nullable=False),
        sa.Column('estado_despues', sa.String(length=20), nullable=False),
        # Los desgloses de largo variable.
        sa.Column('dias_agregados', sa.JSON(), nullable=True),
        sa.Column('precios_corregidos', sa.JSON(), nullable=True),
        sa.Column('empresa_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
        sa.ForeignKeyConstraint(['liquidacion_id'], ['liquidaciones.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_liquidaciones_correcciones_liquidacion_id'),
        'liquidaciones_correcciones', ['liquidacion_id'], unique=False,
    )
    op.create_index(
        op.f('ix_liquidaciones_correcciones_empresa_id'),
        'liquidaciones_correcciones', ['empresa_id'], unique=False,
    )
    op.create_index(
        op.f('ix_liquidaciones_correcciones_estado'),
        'liquidaciones_correcciones', ['estado'], unique=False,
    )


def downgrade() -> None:
    # OJO AL BAJAR: esto borra la MEMORIA de por qué un comprobante dice una cifra
    # distinta a la del papel que el productor tiene en la mano. Las liquidaciones
    # corregidas se quedan con sus cifras nuevas —que es lo correcto, esa plata se
    # movió— pero pierden el motivo, quién lo hizo y las cifras de antes. Si hay que
    # revertir con correcciones ya hechas, hay que exportar la tabla primero.
    op.drop_index(
        op.f('ix_liquidaciones_correcciones_estado'),
        table_name='liquidaciones_correcciones',
    )
    op.drop_index(
        op.f('ix_liquidaciones_correcciones_empresa_id'),
        table_name='liquidaciones_correcciones',
    )
    op.drop_index(
        op.f('ix_liquidaciones_correcciones_liquidacion_id'),
        table_name='liquidaciones_correcciones',
    )
    op.drop_table('liquidaciones_correcciones')
    # `batch_alter_table` para que SQLite también pueda bajar: allá un DROP COLUMN
    # rehace la tabla entera. En Postgres es un ALTER normal.
    with op.batch_alter_table('liquidaciones') as batch:
        batch.drop_column('fecha_primera_impresion')
        batch.drop_column('version')
