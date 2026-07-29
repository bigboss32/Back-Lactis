"""flete (gasto de despacho) en las ventas propias

Lo que cuesta LLEVAR el despacho: el transporte a Bogotá o a donde sea. Las
ventas de reventa ya lo tenían (gasto_concepto / gasto_por_kilo / gasto_monto) y
las ventas propias no, así que el kilo puesto en destino salía más barato de lo
que es y la utilidad por lote de producción quedaba mejor de lo real.

Se llaman igual que en reventa a propósito: es el mismo concepto.

NO cambia el total que paga el cliente: es un costo de la quesera.

Revision ID: e9a4b6c2f1d7
Revises: d8f3c2b7e1a9
Create Date: 2026-07-29 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e9a4b6c2f1d7'
down_revision: Union[str, None] = 'd8f3c2b7e1a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ventas', sa.Column('gasto_concepto', sa.String(length=150), nullable=True))
    op.add_column(
        'ventas',
        sa.Column(
            'gasto_por_kilo', sa.Numeric(precision=12, scale=2),
            server_default='0', nullable=False,
        ),
    )
    op.add_column(
        'ventas',
        sa.Column(
            'gasto_monto', sa.Numeric(precision=14, scale=2),
            server_default='0', nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('ventas', 'gasto_monto')
    op.drop_column('ventas', 'gasto_por_kilo')
    op.drop_column('ventas', 'gasto_concepto')
