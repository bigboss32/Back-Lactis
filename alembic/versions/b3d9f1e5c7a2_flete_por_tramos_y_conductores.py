"""flete del despacho por TRAMOS, con conductor, y pagos a conductores

Lo pidió el dueño así: "necesito que el flete pueda ser varios; ejemplo puede
ser de la quesera a San Vicente 400 y de San Vicente a Bogotá 600, y el nombre
del conductor, porque necesito saber cuánto se le tiene que pagar".

DOS TABLAS NUEVAS Y NINGUNA COLUMNA NUEVA en tablas existentes. `ventas` no se
toca: sus `gasto_concepto` / `gasto_por_kilo` / `gasto_monto` siguen ahí y
siguen significando lo mismo (el flete COMPLETO del despacho), solo que ahora
son el resumen de los tramos. Eso es a propósito: todo lo que hoy lee
`gasto_monto` —la utilidad por lote de producción, el estado de resultados, la
pantalla de lotes— sigue leyendo exactamente la misma columna y no se entera
del cambio.

EL BACKFILL ES LA PARTE DELICADA. Cada venta que ya tenía flete queda con UN
tramo equivalente. La regla que lo hace seguro es que el tramo COPIA el
`gasto_monto` que ya estaba guardado; NO lo recalcula multiplicando por los
kilos. Recalcular parece más "correcto" pero es justo lo peligroso: si por un
redondeo viejo, una edición o un dato cargado a mano el monto guardado no fuera
exactamente por_kilo × kilos, recalcular movería la cifra y la utilidad de una
venta ya cerrada cambiaría sola. Copiándolo, la suma de los tramos da SIEMPRE
el mismo peso que había antes.

Por lo mismo el filtro es `gasto_por_kilo <> 0 OR gasto_monto <> 0` y no solo el
primero: si alguna venta quedó con monto y sin por-kilo, igual tiene que quedar
representada, o su desglose sumaría cero contra una cifra grande que no lo es.

Se migran TODAS las ventas, incluidas las anuladas y las borradas en suave: el
tramo es el espejo de lo que la fila ya dice. Filtrar aquí dejaría a esas filas
con un desglose vacío el día que alguien las consulte o las restaure.

`conductor` queda en NULL en lo migrado, y así se muestra ("Sin conductor"):
esos viajes viejos no tienen a quién atribuirse y ponerles un nombre sería
inventarlo. `destino` se llena con el `gasto_concepto` que hubiera ("Transporte
a Bogotá"), recortado a 120 caracteres, que es lo más cerca que hay del dato.

La función del backfill está separada y recibe la conexión para poder probarla:
tests/test_ventas_fletes_por_tramos.py la corre contra una base con ventas
viejas cargadas y comprueba peso por peso que ninguna cifra se movió.

Revision ID: b3d9f1e5c7a2
Revises: a7f2c4e9b3d1
Create Date: 2026-08-02 12:00:00.000000

"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3d9f1e5c7a2'
down_revision: Union[str, None] = 'a7f2c4e9b3d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Descripción mínima de las dos tablas que toca el backfill. Se declara aquí,
# congelada, en vez de importar los modelos de la aplicación: una migración
# tiene que seguir haciendo lo mismo dentro de un año, aunque el modelo cambie.
_ventas = sa.table(
    'ventas',
    sa.column('id', sa.Uuid()),
    sa.column('gasto_concepto', sa.String()),
    sa.column('gasto_por_kilo', sa.Numeric(12, 2)),
    sa.column('gasto_monto', sa.Numeric(14, 2)),
)

_tramos = sa.table(
    'venta_tramos_flete',
    sa.column('id', sa.Uuid()),
    sa.column('venta_id', sa.Uuid()),
    sa.column('orden', sa.Integer()),
    sa.column('origen', sa.String()),
    sa.column('destino', sa.String()),
    sa.column('conductor', sa.String()),
    sa.column('conductor_clave', sa.String()),
    sa.column('valor_por_kilo', sa.Numeric(12, 2)),
    sa.column('valor_total', sa.Numeric(14, 2)),
    sa.column('created_at', sa.DateTime(timezone=True)),
    sa.column('updated_at', sa.DateTime(timezone=True)),
    sa.column('estado', sa.String()),
)


def backfill_tramos_de_flete(conn) -> int:
    """Convierte en UN tramo el flete de cada venta que ya lo tenía.

    Devuelve cuántos tramos creó. Copia el monto guardado tal cual (ver el
    encabezado del archivo): la suma de los tramos de una venta tiene que dar
    exactamente el `gasto_monto` que esa venta ya tenía, sin mover un peso.
    """
    viejas = conn.execute(
        sa.select(
            _ventas.c.id,
            _ventas.c.gasto_concepto,
            _ventas.c.gasto_por_kilo,
            _ventas.c.gasto_monto,
        ).where(
            sa.or_(
                _ventas.c.gasto_por_kilo != 0,
                _ventas.c.gasto_monto != 0,
            )
        )
    ).all()

    if not viejas:
        return 0

    ahora = datetime.now(timezone.utc)
    filas = [
        {
            'id': uuid.uuid4(),
            'venta_id': venta_id,
            'orden': 1,
            'origen': None,
            # Lo único que se sabe del recorrido es lo que el dueño escribió en
            # "A dónde va". Se recorta al ancho de la columna nueva (120) porque
            # la vieja admitía 150.
            'destino': (concepto or None) and concepto[:120],
            'conductor': None,
            'conductor_clave': None,
            'valor_por_kilo': por_kilo or 0,
            # EL MONTO SE COPIA, NO SE RECALCULA. Es lo que garantiza que
            # ninguna cifra vieja se mueva.
            'valor_total': monto or 0,
            'created_at': ahora,
            'updated_at': ahora,
            'estado': 'activo',
        }
        for venta_id, concepto, por_kilo, monto in viejas
    ]
    conn.execute(sa.insert(_tramos), filas)
    return len(filas)


def upgrade() -> None:
    op.create_table(
        'venta_tramos_flete',
        sa.Column('venta_id', sa.Uuid(), nullable=False),
        sa.Column('orden', sa.Integer(), server_default='1', nullable=False),
        sa.Column('origen', sa.String(length=120), nullable=True),
        sa.Column('destino', sa.String(length=120), nullable=True),
        sa.Column('conductor', sa.String(length=150), nullable=True),
        sa.Column('conductor_clave', sa.String(length=150), nullable=True),
        sa.Column('valor_por_kilo', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
        sa.Column('valor_total', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
        sa.ForeignKeyConstraint(['venta_id'], ['ventas.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_venta_tramos_flete_venta_id'), 'venta_tramos_flete', ['venta_id'], unique=False)
    op.create_index(op.f('ix_venta_tramos_flete_estado'), 'venta_tramos_flete', ['estado'], unique=False)
    op.create_index('ix_venta_tramo_conductor', 'venta_tramos_flete', ['conductor_clave'], unique=False)

    op.create_table(
        'pagos_conductor',
        sa.Column('conductor', sa.String(length=150), nullable=False),
        sa.Column('conductor_clave', sa.String(length=150), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('valor', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('observaciones', sa.String(length=300), nullable=True),
        sa.Column('empresa_id', sa.Uuid(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('estado', sa.String(length=30), server_default='activo', nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pagos_conductor_empresa_id'), 'pagos_conductor', ['empresa_id'], unique=False)
    op.create_index(op.f('ix_pagos_conductor_fecha'), 'pagos_conductor', ['fecha'], unique=False)
    op.create_index(op.f('ix_pagos_conductor_estado'), 'pagos_conductor', ['estado'], unique=False)
    op.create_index(
        'ix_pago_conductor_empresa_clave', 'pagos_conductor', ['empresa_id', 'conductor_clave'], unique=False
    )

    backfill_tramos_de_flete(op.get_bind())


def downgrade() -> None:
    # Se pueden botar las dos tablas sin perder plata: el flete de cada venta
    # sigue guardado en las columnas `gasto_*` de `ventas`, que esta migración
    # nunca tocó. Lo que sí se pierde son los conductores, los tramos partidos y
    # los pagos que se les hayan registrado.
    op.drop_index('ix_pago_conductor_empresa_clave', table_name='pagos_conductor')
    op.drop_index(op.f('ix_pagos_conductor_estado'), table_name='pagos_conductor')
    op.drop_index(op.f('ix_pagos_conductor_fecha'), table_name='pagos_conductor')
    op.drop_index(op.f('ix_pagos_conductor_empresa_id'), table_name='pagos_conductor')
    op.drop_table('pagos_conductor')

    op.drop_index('ix_venta_tramo_conductor', table_name='venta_tramos_flete')
    op.drop_index(op.f('ix_venta_tramos_flete_estado'), table_name='venta_tramos_flete')
    op.drop_index(op.f('ix_venta_tramos_flete_venta_id'), table_name='venta_tramos_flete')
    op.drop_table('venta_tramos_flete')
