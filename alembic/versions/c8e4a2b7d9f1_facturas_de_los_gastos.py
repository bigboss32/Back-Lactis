"""las facturas de los gastos, guardadas en el bucket privado (R2)

LO QUE PIDIÓ EL DUEÑO: "en gastos que se le pueda subir la factura". Ya se podía
—había un botón—, pero la factura se guardaba en la carpeta `uploads/` del propio
servidor, que está publicada como archivos estáticos en `/uploads` SIN NINGUNA
CLAVE: la dirección de la factura de una quesera se abría desde cualquier
navegador, sin entrar al sistema. Además solo cabía UNA por gasto —la segunda
página de una factura pisaba la primera— y se guardaba tal como salía del celular,
de varios megas. Esta tabla la mueve al mismo mecanismo que ya usan los soportes de
pago de las liquidaciones y los de reventa: bucket privado, enlaces firmados que
caducan solos, varias por gasto y comprimidas al entrar.

TABLA NUEVA Y NADA MÁS. No toca ninguna tabla existente, no agrega ni quita
columnas, no mueve una sola cifra y no tiene backfill que hacer. Corre igual sobre
una base con miles de gastos cargados y sobre una vacía: ningún gasto cambia de
valor, de categoría ni de estado por esto.

LA COLUMNA VIEJA `gastos.adjunto_url` SE QUEDA, Y A PROPÓSITO. Ahí están las
facturas que el dueño ya subió al servidor. La aplicación no vuelve a escribir esa
columna —el botón nuevo sube al bucket— pero la pantalla la sigue mostrando cuando
trae algo: borrarla ahora sería esconderle facturas que él ya cargó. Lo que quede
en `uploads/` se lo puede volver a subir cuando quiera; mientras tanto no se le
quita nada de lo que ve hoy.

NO SE GUARDA NINGUNA URL, solo `object_key`: las URLs se firman en el momento de
pedirlas y caducan solas. Ver el docstring del modelo `AdjuntoGasto`, que es donde
está escrito el porqué completo — una URL guardada en una columna es un permiso
permanente sobre un documento con el NIT del proveedor y el valor pagado.

LA LLAVE LLEVA EL empresa_id ADENTRO (`{empresa_id}/gastos/{gasto_id}/{uuid}.jpg`)
y la tabla lleva ADEMÁS su propia columna `empresa_id`, aunque su padre —el gasto—
ya la tenga. La razón está en el modelo: a la factura se entra directo por su id
para compartirla y para borrarla, sin el gasto en la ruta, así que sin la columna el
aislamiento entre las dos queseras dependería de acordarse de escribir un JOIN en
cada consulta.

ON DELETE CASCADE contra el gasto: es la red de abajo. El servicio borra las filas Y
los archivos del bucket antes de borrar el gasto, porque una fila que se va sin su
archivo deja el archivo cobrando almacenamiento sin que nadie pueda verlo ni
borrarlo.

Revision ID: c8e4a2b7d9f1
Revises: b3d9f6c2a8e1
Create Date: 2026-09-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8e4a2b7d9f1'
down_revision: Union[str, None] = 'b3d9f6c2a8e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'adjuntos_gasto',
        sa.Column('gasto_id', sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(['gasto_id'], ['gastos.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.PrimaryKeyConstraint('id'),
        # Único: dos filas apuntando al mismo objeto harían que borrar una dejara
        # a la otra señalando un archivo que ya no existe.
        sa.UniqueConstraint('object_key', name='uq_adjuntos_gasto_object_key'),
    )
    op.create_index(
        op.f('ix_adjuntos_gasto_gasto_id'), 'adjuntos_gasto', ['gasto_id'], unique=False,
    )
    op.create_index(
        op.f('ix_adjuntos_gasto_empresa_id'), 'adjuntos_gasto', ['empresa_id'], unique=False,
    )
    op.create_index(
        op.f('ix_adjuntos_gasto_estado'), 'adjuntos_gasto', ['estado'], unique=False,
    )


def downgrade() -> None:
    # OJO al bajar: esto borra las FILAS, no los archivos del bucket. Si algún día
    # hay que revertir con facturas ya subidas, hay que anotarse las `object_key`
    # antes — si no, quedan objetos en R2 que nadie va a poder nombrar ni borrar.
    op.drop_index(op.f('ix_adjuntos_gasto_estado'), table_name='adjuntos_gasto')
    op.drop_index(op.f('ix_adjuntos_gasto_empresa_id'), table_name='adjuntos_gasto')
    op.drop_index(op.f('ix_adjuntos_gasto_gasto_id'), table_name='adjuntos_gasto')
    op.drop_table('adjuntos_gasto')
