"""quitar constraints de kilos y barras para productos por unidad

Revision ID: a4b5c6d7e8f9
Revises: a2e6b9d4c1f8
Create Date: 2026-08-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, None] = 'a2e6b9d4c1f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Quitar los checks rígidos que obligaban a que la unidad fuera estrictamente
    # 'kilos' si el tipo no era 'mozzarella', o 'barras' si era 'mozzarella'.
    # A partir del Lote 2, la unidad se define en la tabla productos_reventa.
    op.drop_constraint("ck_compras_queso_cantidad_en_su_unidad", "compras_queso", type_="check")
    op.drop_constraint("ck_ventas_queso_cantidad_en_su_unidad", "ventas_queso", type_="check")


def downgrade() -> None:
    op.create_check_constraint(
        "ck_compras_queso_cantidad_en_su_unidad",
        "compras_queso",
        "(tipo <> 'mozzarella' AND barras = 0 AND precio_barra = 0) OR (tipo = 'mozzarella' AND kilos_brutos = 0 AND kilos_netos = 0 AND merma_kilos = 0 AND borona_kilos = 0 AND precio_kilo = 0)",
    )
    op.create_check_constraint(
        "ck_ventas_queso_cantidad_en_su_unidad",
        "ventas_queso",
        "(tipo <> 'mozzarella' AND barras = 0 AND precio_barra = 0 AND gasto_por_barra = 0) OR (tipo = 'mozzarella' AND kilos = 0 AND precio_kilo = 0 AND gasto_por_kilo = 0)",
    )