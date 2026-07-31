"""roles por empresa

usuario_roles pasa de tabla de asociación pura (usuario_id, rol_id) a
association object con id propio, empresa_id NULLABLE y created_at: una fila =
"este rol EN esta empresa"; empresa_id NULL = rol global (solo el Administrador
General). La tabla es diminuta, así que se lee, se tira, se recrea y se
reinsertan las filas con backfill empresa_id = usuarios.empresa_id del dueño
(el superadmin, sin empresa, queda NULL = global).

Verificación manual en Postgres tras migrar (debe dar 0):
    SELECT count(*) FROM usuario_roles ur JOIN usuarios u ON u.id = ur.usuario_id
    WHERE ur.empresa_id IS DISTINCT FROM u.empresa_id;

Revision ID: b8e2d4f6a3c1
Revises: f4c8a2d6e1b9
Create Date: 2026-07-31 00:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e2d4f6a3c1'
down_revision: Union[str, None] = 'f4c8a2d6e1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Backfill: cada rol existente queda anclado a la empresa del dueño.
    filas = bind.execute(
        sa.text(
            "SELECT ur.usuario_id AS usuario_id, ur.rol_id AS rol_id, "
            "u.empresa_id AS empresa_id "
            "FROM usuario_roles ur JOIN usuarios u ON u.id = ur.usuario_id"
        )
    ).mappings().all()

    op.drop_table('usuario_roles')
    tabla = op.create_table('usuario_roles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('usuario_id', sa.Uuid(), nullable=False),
    sa.Column('rol_id', sa.Uuid(), nullable=False),
    sa.Column('empresa_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.ForeignKeyConstraint(['rol_id'], ['roles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    if filas:
        op.bulk_insert(
            tabla,
            [
                {
                    "id": uuid.uuid4(),
                    "usuario_id": fila["usuario_id"],
                    "rol_id": fila["rol_id"],
                    "empresa_id": fila["empresa_id"],
                }
                for fila in filas
            ],
        )
    op.create_index(op.f('ix_usuario_roles_usuario_id'), 'usuario_roles', ['usuario_id'], unique=False)
    op.create_index(op.f('ix_usuario_roles_empresa_id'), 'usuario_roles', ['empresa_id'], unique=False)
    # Unicidad con índices únicos PARCIALES (funcionan en Postgres y SQLite):
    # un rol por empresa y, aparte, un rol global por usuario.
    op.create_index(
        'uq_usuario_rol_empresa', 'usuario_roles', ['usuario_id', 'rol_id', 'empresa_id'],
        unique=True,
        postgresql_where=sa.text('empresa_id IS NOT NULL'),
        sqlite_where=sa.text('empresa_id IS NOT NULL'),
    )
    op.create_index(
        'uq_usuario_rol_global', 'usuario_roles', ['usuario_id', 'rol_id'],
        unique=True,
        postgresql_where=sa.text('empresa_id IS NULL'),
        sqlite_where=sa.text('empresa_id IS NULL'),
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Al volver al modelo global se pierde la dimensión empresa: un mismo rol
    # en varias empresas colapsa a una sola fila (de ahí el DISTINCT).
    filas = bind.execute(
        sa.text("SELECT DISTINCT usuario_id, rol_id FROM usuario_roles")
    ).mappings().all()

    op.drop_index('uq_usuario_rol_global', table_name='usuario_roles')
    op.drop_index('uq_usuario_rol_empresa', table_name='usuario_roles')
    op.drop_index(op.f('ix_usuario_roles_empresa_id'), table_name='usuario_roles')
    op.drop_index(op.f('ix_usuario_roles_usuario_id'), table_name='usuario_roles')
    op.drop_table('usuario_roles')
    tabla = op.create_table('usuario_roles',
    sa.Column('usuario_id', sa.Uuid(), nullable=False),
    sa.Column('rol_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['rol_id'], ['roles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('usuario_id', 'rol_id')
    )
    if filas:
        op.bulk_insert(
            tabla,
            [{"usuario_id": fila["usuario_id"], "rol_id": fila["rol_id"]} for fila in filas],
        )
