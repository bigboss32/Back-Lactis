"""pago de la suscripción por PSE (débito desde el banco)

Dos columnas en pagos_suscripcion:

- `metodo`: con qué se pagó, 'CARD' o 'PSE'. Los pagos que ya existen quedan en
  'CARD', que es lo que eran: hasta ahora solo se podía pagar con tarjeta.
- `url_banco`: solo en PSE, el portal del banco al que hay que mandar a la
  persona. Se guarda para poder RETOMAR el pago si cerró la pestaña.

Revision ID: d1f6a3c8b2e5
Revises: c9d2e8f4a7b1
Create Date: 2026-08-01 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1f6a3c8b2e5'
down_revision: Union[str, None] = 'c9d2e8f4a7b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'pagos_suscripcion',
        sa.Column('metodo', sa.String(length=10), server_default='CARD', nullable=False),
    )
    op.add_column('pagos_suscripcion', sa.Column('url_banco', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('pagos_suscripcion', 'url_banco')
    op.drop_column('pagos_suscripcion', 'metodo')
