"""mozzarella por barras en reventa: compra y venta por unidad

Revision ID: a2f7c1d4b8e6
Revises: a1c4e8b7d2f6
Create Date: 2026-08-03 09:00:00.000000

LO QUE HACE Y POR QUÉ CORRE SOBRE TABLAS CON FILAS
--------------------------------------------------
La mozzarella se comercia por BARRAS: entra como barra y sale como barra. Las
barras NO pueden vivir en las columnas de kilos, porque entonces cualquier
`sum(kilos_netos)` sumaría peras con manzanas. Así que se agregan columnas
propias (`barras`, `precio_barra`, `gasto_por_barra`) y un discriminador `tipo`
en las compras (las ventas ya lo tenían).

TODAS LAS FILAS QUE YA EXISTEN SON DE KILOS y quedan marcadas como tal, no en un
estado ambiguo:
  - `compras_queso.tipo` entra con server_default 'queso', así que las compras
    viejas quedan diciendo explícitamente que son de queso en kilos.
  - las columnas de barras entran con server_default '0': una compra o una venta
    vieja tiene CERO barras, que es la verdad, y no NULL (que obligaría a
    coalesce en cada consulta y dejaría la puerta abierta a un NULL sumado).

Los dos CHECK del final son la pieza que hace que la regla se sostenga sola: en
una fila de kilos las columnas de barras están en cero y al contrario. Se crean
DESPUÉS de agregar las columnas con su default, así que las filas existentes ya
lo cumplen cuando se validan (tipo 'queso' + barras 0 + precio_barra 0).

Se usa op.create_check_constraint directo (Postgres, que es donde corre esto en
producción). Las pruebas montan el esquema con create_all, así que allá los CHECK
llegan desde el modelo y también se verifican.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2f7c1d4b8e6'
down_revision: Union[str, None] = 'a1c4e8b7d2f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------- compras
    op.add_column(
        'compras_queso',
        sa.Column('tipo', sa.String(length=20), server_default='queso', nullable=False),
    )
    op.create_index(op.f('ix_compras_queso_tipo'), 'compras_queso', ['tipo'], unique=False)
    # Escala 0: una barra es una barra. Sin decimales ni en la base.
    op.add_column(
        'compras_queso',
        sa.Column(
            'barras', sa.Numeric(precision=12, scale=0), server_default='0', nullable=False
        ),
    )
    op.add_column(
        'compras_queso',
        sa.Column(
            'precio_barra',
            sa.Numeric(precision=12, scale=2),
            server_default='0',
            nullable=False,
        ),
    )

    # -------------------------------------------------------------- ventas
    op.add_column(
        'ventas_queso',
        sa.Column(
            'barras', sa.Numeric(precision=12, scale=0), server_default='0', nullable=False
        ),
    )
    op.add_column(
        'ventas_queso',
        sa.Column(
            'precio_barra',
            sa.Numeric(precision=12, scale=2),
            server_default='0',
            nullable=False,
        ),
    )
    op.add_column(
        'ventas_queso',
        sa.Column(
            'gasto_por_barra',
            sa.Numeric(precision=12, scale=2),
            server_default='0',
            nullable=False,
        ),
    )

    # ------------------------- la garantía: cada cantidad en SU columna
    op.create_check_constraint(
        'ck_compras_queso_cantidad_en_su_unidad',
        'compras_queso',
        "(tipo <> 'mozzarella' AND barras = 0 AND precio_barra = 0) "
        "OR (tipo = 'mozzarella' AND kilos_brutos = 0 AND kilos_netos = 0 "
        "AND merma_kilos = 0 AND borona_kilos = 0 AND precio_kilo = 0)",
    )
    op.create_check_constraint(
        'ck_ventas_queso_cantidad_en_su_unidad',
        'ventas_queso',
        "(tipo <> 'mozzarella' AND barras = 0 AND precio_barra = 0 "
        "AND gasto_por_barra = 0) "
        "OR (tipo = 'mozzarella' AND kilos = 0 AND precio_kilo = 0 "
        "AND gasto_por_kilo = 0)",
    )


def downgrade() -> None:
    op.drop_constraint('ck_ventas_queso_cantidad_en_su_unidad', 'ventas_queso', type_='check')
    op.drop_constraint(
        'ck_compras_queso_cantidad_en_su_unidad', 'compras_queso', type_='check'
    )
    op.drop_column('ventas_queso', 'gasto_por_barra')
    op.drop_column('ventas_queso', 'precio_barra')
    op.drop_column('ventas_queso', 'barras')
    op.drop_column('compras_queso', 'precio_barra')
    op.drop_column('compras_queso', 'barras')
    op.drop_index(op.f('ix_compras_queso_tipo'), table_name='compras_queso')
    op.drop_column('compras_queso', 'tipo')
