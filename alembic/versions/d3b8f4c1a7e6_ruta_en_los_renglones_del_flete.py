"""la RUTA en los renglones del comprobante del transportador

Segunda mitad de lo que pidió el dueño ("este tuvo que hacer las dos [rutas]...
pero cada ruta puede tener un valor diferente de litro por leche"). La primera
—la tabla de tarifas por ruta— la trae la migración c6b1e4a8d3f7, de la que esta
cuelga.

QUÉ HACE: le agrega `ruta_id` (anulable) a `liquidacion_detalles`, y le completa
la ruta a los renglones de flete VIEJOS que se pueden identificar sin adivinar.

POR QUÉ HACE FALTA LA COLUMNA. El renglón del comprobante del transportador pasó
de ser POR DÍA a ser POR DÍA Y RUTA, porque en cada ruta cobra distinto: el día
que Alex hizo Nápoles a $242,76 y Mira Valle a $317,50 da DOS renglones, y sin
una columna que diga cuál es cuál el conductor recibiría dos líneas con la misma
fecha, litros distintos y precios distintos, sin manera de saber qué es cada una.

QUEDA ANULABLE, y son tres situaciones distintas que conviene no confundir:
  · los renglones del comprobante del PROVEEDOR no tienen ruta y nunca la van a
    tener (ahí el renglón es el día de ese productor);
  · una recepción puede haber quedado sin ruta, y su flete sale entonces de la
    tarifa general del transportador;
  · los renglones que ya estaban guardados eran por día, sin ruta.

EL BACKFILL NO MUEVE NI UN PESO, y eso es a propósito y es lo importante: solo
escribe la etiqueta `ruta_id`. No toca `litros`, ni `precio_litro`, ni `valor`, ni
los totales de la liquidación. Hay comprobantes ya pagados; recalcularlos "bien"
les cambiaría la cifra a papeles que ya están firmados y en la mano de alguien.
Si un comprobante viejo quedó con un renglón que junta dos rutas a tarifas
distintas —el defecto que este cambio corrige— ese renglón se queda como está: lo
que ese día se pagó fue eso, y la corrección aplica de aquí en adelante.

Y SOLO SE ESCRIBE CUANDO NO HAY DUDA: la ruta se le pone al renglón únicamente si
TODAS las recepciones de ese día en esa liquidación de flete eran de la misma
ruta, que es el caso de todos los transportadores de hoy (tenían una sola). Si el
día tenía dos rutas, el renglón viejo las junta y no hay una sola ruta que sea la
verdad: se queda en nulo y el comprobante lo imprime con un guion, que es
exactamente lo que ese papel decía cuando se emitió. Adivinar cuál poner sería
ponerle a un documento de plata un dato que nadie escribió.

EL BACKFILL ES UNA SOLA SENTENCIA DE CONJUNTO (un UPDATE con subconsultas
correlacionadas) y no un `.all()` a Python con un UPDATE por grupo. Además de ser
más rápido, es lo único que funciona con `alembic upgrade --sql` (modo offline),
que este env.py soporta: ahí `op.get_bind()` devuelve una conexión de mentiras cuyo
`execute` no devuelve nada, así que el `.all()` reventaba con un "AttributeError:
'NoneType' object has no attribute 'all'". Con `op.execute` la misma sentencia se
ejecuta online o se escribe en el script SQL. El `--sql` funciona contra Postgres,
que es producción; contra SQLite no, porque `batch_alter_table` necesita conexión
viva para reflejar la tabla (ver la nota larga en c6b1e4a8d3f7).

La sentencia está en una función aparte para poder probarla:
tests/test_liquidacion_flete_por_ruta.py la corre contra una base cargada con la
forma vieja y comprueba que las cifras no se movieron (pytest monta el esquema con
create_all, así que las migraciones no se ejercitan solas).

Revision ID: d3b8f4c1a7e6
Revises: c6b1e4a8d3f7
Create Date: 2026-08-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3b8f4c1a7e6'
down_revision: Union[str, None] = 'c6b1e4a8d3f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Descripción mínima de las dos tablas que toca el backfill. Se declara acá,
# congelada, en vez de importar los modelos de la aplicación: una migración tiene
# que seguir haciendo lo mismo dentro de un año, aunque el modelo cambie.
_detalles = sa.table(
    'liquidacion_detalles',
    sa.column('id', sa.Uuid()),
    sa.column('liquidacion_id', sa.Uuid()),
    sa.column('fecha', sa.Date()),
    sa.column('ruta_id', sa.Uuid()),
)

_recepciones = sa.table(
    'recepciones_leche',
    sa.column('id', sa.Uuid()),
    sa.column('fecha', sa.Date()),
    sa.column('ruta_id', sa.Uuid()),
    sa.column('liquidacion_transporte_id', sa.Uuid()),
)


def sentencia_backfill_ruta_de_los_renglones() -> sa.sql.Update:
    """El UPDATE que le pone la ruta a los renglones de flete viejos de UNA sola ruta.

    Devuelve la sentencia SIN ejecutarla, para que la corra `op.execute` (que sirve
    online y offline) o una conexión de prueba.

    Solo alcanza a los renglones de liquidaciones DE FLETE, sin necesidad de
    filtrar por tipo: se cruza por `recepciones_leche.liquidacion_transporte_id`,
    que únicamente apunta a las de transportador. Los renglones de las de proveedor
    no aparecen en el cruce y se quedan en nulo, que es lo correcto.

    NO filtra por estado ni por `deleted_at`: una liquidación anulada, o una borrada
    en suave que mañana alguien consulte, tiene el mismo derecho a que su
    comprobante se lea bien. Y como esto no mueve cifras, rotular de más no puede
    dañar ninguna cuenta.

    LAS DOS CONDICIONES SON "NO HAY DUDA", escritas en SQL:

      · `count(distinct ruta_id) = 1` — ese día, en ese comprobante, todas las
        recepciones con ruta traen LA MISMA;
      · y ninguna recepción de ese día quedó SIN ruta. Hace falta aparte porque
        `count(distinct)` no cuenta los nulos: sin esta segunda condición, un día con
        una recepción en Nápoles y otra sin ruta se rotularía "Nápoles" entero, y ese
        renglón viejo junta las dos. Adivinar el rótulo de un documento de plata es
        exactamente lo que no se puede hacer.

    Cumplidas las dos, cualquier recepción del día sirve para leer la ruta: todas
    tienen la misma, así que el `LIMIT 1` sin `ORDER BY` es determinista en el VALOR
    (y se usa en vez de `max(ruta_id)` porque no todos los motores agregan uuids).
    """
    rec = _recepciones.alias('rec')
    del_dia = sa.and_(
        rec.c.liquidacion_transporte_id == _detalles.c.liquidacion_id,
        rec.c.fecha == _detalles.c.fecha,
    )
    cuantas_rutas = (
        sa.select(sa.func.count(sa.distinct(rec.c.ruta_id))).where(del_dia).scalar_subquery()
    )
    alguna_sin_ruta = sa.exists(
        sa.select(sa.literal_column('1')).where(del_dia, rec.c.ruta_id.is_(None))
    )
    la_ruta = sa.select(rec.c.ruta_id).where(del_dia).limit(1).scalar_subquery()
    return (
        sa.update(_detalles)
        .where(cuantas_rutas == 1, sa.not_(alguna_sin_ruta))
        .values(ruta_id=la_ruta)
    )


def backfill_ruta_de_los_renglones(conn) -> int:
    """Corre el backfill sobre `conn` y devuelve cuántos renglones rotuló.

    Es el camino de las PRUEBAS, que necesitan la cuenta para verificarla. El
    `upgrade` usa `op.execute(sentencia_backfill_ruta_de_los_renglones())`, que es la
    misma sentencia y además funciona en modo offline.
    """
    return conn.execute(sentencia_backfill_ruta_de_los_renglones()).rowcount or 0


def upgrade() -> None:
    # `batch_alter_table` y no `op.create_foreign_key` pelado: SQLite no sabe
    # agregarle una FOREIGN KEY a una tabla que ya existe (no hay ALTER TABLE ADD
    # CONSTRAINT), así que sin batch esta migración no corre en local. En Postgres
    # (producción) el batch se resuelve en el ALTER TABLE de siempre y no cambia
    # nada. Las dos operaciones van en el MISMO bloque para que SQLite recree la
    # tabla una sola vez.
    with op.batch_alter_table('liquidacion_detalles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ruta_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            'fk_liquidacion_detalles_ruta_id_rutas', 'rutas', ['ruta_id'], ['id']
        )

    # `op.execute` y no `op.get_bind().execute`: en modo offline (`alembic upgrade
    # --sql`) NO HAY CONEXIÓN, `op.get_bind()` devuelve None, y `op.execute` es lo
    # único que sabe escribir la sentencia en el script en vez de ejecutarla.
    op.execute(sentencia_backfill_ruta_de_los_renglones())


def downgrade() -> None:
    # Se pierde LA ETIQUETA de la ruta de cada renglón, no plata: los litros, el
    # precio y el valor de cada renglón se quedan intactos, y la suma sigue dando el
    # mismo total.
    #
    # Lo que queda raro al bajar es otra cosa, y conviene saberlo: un comprobante
    # emitido con esta versión puede tener DOS renglones del mismo día (uno por
    # ruta), y sin la columna esos dos renglones se ven como dos líneas con la misma
    # fecha y precios distintos, sin nada que los distinga. Siguen sumando bien. Y el
    # código viejo, al recalcular ese comprobante, volvería a juntar el día en un
    # solo renglón con una sola tarifa: ahí sí volvería el descuadre que este cambio
    # corrige. O sea que bajar esta migración obliga a bajar también el código.
    #
    # Va en batch por lo mismo que el upgrade: SQLite no sabe botar una restricción
    # ni una columna nombrada en una FK sin recrear la tabla. En Postgres son los dos
    # ALTER TABLE de siempre.
    with op.batch_alter_table('liquidacion_detalles', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_liquidacion_detalles_ruta_id_rutas', type_='foreignkey'
        )
        batch_op.drop_column('ruta_id')
