"""el transportador hace VARIAS rutas, y cobra distinto en cada una

Lo pidió el dueño en dos mensajes: "ahora el transportador puede tener varias
rutas, por ejemplo este tuvo que hacer las dos" y "pero cada ruta puede tener un
valor diferente de litro por leche". O sea que la ruta dejó de ser una etiqueta y
ENTRÓ EN LA PLATA: es la que escoge la tarifa por litro del flete.

QUÉ HACE:

  · crea `transportador_rutas`, la tabla puente con la tarifa encima (una fila por
    ruta que hace el transportador, con lo que cobra por litro en ella), única por
    (transportador_id, ruta_id);
  · COPIA a esa tabla el par (ruta_id, valor_transporte) que cada transportador ya
    tenía, y solo DESPUÉS borra la columna `transportadores.ruta_id`;
  · deja `transportadores.valor_transporte` donde está: pasa a ser la TARIFA
    GENERAL, la que aplica cuando el día no tiene ruta o cuando esa ruta no tiene
    tarifa propia.

EL BACKFILL ES LA PARTE DELICADA. El cliente ya tiene transportadores con ruta y
tarifa cargadas, y sus recepciones viejas guardan el flete como una FOTO del
momento (`recepciones_leche.valor_transporte`), así que esta migración no puede
mover ninguna cifra vieja: lo único que hace es dejar la tarifa de hoy escrita
donde el código nuevo la va a buscar. Si no se copiara, el primer día que se
reciba leche después de desplegar, la tarifa por ruta saldría de la general —y
para un transportador cuya tarifa vivía en su única ruta eso podría ser un cero
callado, con el señor trabajando gratis hasta que alguien cuadre la quincena—.

Se copian TODOS los transportadores que tengan ruta, incluidos los INACTIVOS y
los BORRADOS EN SUAVE, y las rutas BORRADAS EN SUAVE también. No es por
completitud: una liquidación vieja de un transportador retirado se puede recuadrar
todavía (el módulo lo permite mientras no esté pagada), y si su tarifa por ruta no
estuviera copiada ese recálculo la sacaría de la general y le cambiaría el
comprobante.

LO QUE NO SE COPIA ES LO CRUZADO ENTRE EMPRESAS, y esto es una corrección: el
backfill filtraba solo `ruta_id IS NOT NULL`, sin mirar de qué empresa era la
ruta. La columna vieja la escribía el endpoint genérico, que tampoco lo miraba, así
que el dato del cliente PUEDE estar cruzado; y una ruta de la Quesera B copiada
encima de un transportador de la A se leía después como propia, con nombre y tarifa
de la otra, porque la lectura tampoco filtraba (eso se cerró en
transportadores/schemas.py y models.py). Ahora el backfill hace un JOIN con `rutas`
exigiendo `rutas.empresa_id = transportadores.empresa_id`: la fila cruzada NO se
copia y el transportador se queda con su TARIFA GENERAL, que es la única cifra de
él que se puede afirmar. Perder una tarifa que nunca fue suya es lo correcto;
copiarla habría dejado plata de una quesera decidiendo la cuenta de la otra.

El valor se COPIA tal cual, no se recalcula ni se normaliza: la columna nueva es
Numeric(12,2) igual que la vieja, así que $242,76 sigue siendo $242,76.

TODO EL DATO SE MUEVE CON SENTENCIAS DE CONJUNTO (un INSERT ... SELECT y un
UPDATE), no leyendo filas a Python y devolviéndolas. Además de ser más rápido, es
lo único que funciona con `alembic upgrade --sql` (modo offline), que este env.py
soporta: ahí `op.get_bind()` devuelve una conexión de mentiras cuyo `execute` no
devuelve nada, así que el `.all()` del backfill reventaba con un
"AttributeError: 'NoneType' object has no attribute 'all'" y el script no se podía
generar. Con `op.execute` la misma sentencia se ejecuta online o se escribe en el
script SQL, sin dos caminos que se puedan desincronizar.

HASTA DÓNDE LLEGA EL MODO OFFLINE, dicho para que nadie se lleve una sorpresa: el
`--sql` funciona contra POSTGRES, que es producción y el único sitio donde se usa
(generar el script para que un DBA lo revise antes de correrlo). Contra SQLite NO,
y no por algo de este archivo: `batch_alter_table` necesita una conexión viva para
reflejar la tabla, y en `--sql` no hay ninguna. Se podría evitar pasándole
`copy_from` con un `Table` congelado de `transportadores`, y a propósito no se hace:
ese Table tendría que repetir las doce columnas de la tabla a mano, y si una se
escribiera mal el batch la BORRARÍA sin decir nada —también en el SQLite de
verdad—. Cambiar un modo que nadie usa por un riesgo de pérdida de columnas en el
que sí se usa no es un trato.

Las sentencias están en funciones aparte para poder probarlas:
tests/test_transportador_rutas.py las corre contra una base con la forma vieja y
comprueba fila por fila que ninguna tarifa se movió (pytest corre sobre SQLite con
create_all, así que las migraciones no se ejercitan solas).

Revision ID: c6b1e4a8d3f7
Revises: a2f7c1d4b8e6
Create Date: 2026-08-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c6b1e4a8d3f7'
down_revision: Union[str, None] = 'a2f7c1d4b8e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Descripción mínima de las tablas que toca el backfill. Se declara aquí,
# congelada, en vez de importar los modelos de la aplicación: una migración tiene
# que seguir haciendo lo mismo dentro de un año, aunque el modelo cambie. (Y en
# este caso el modelo YA no tiene la columna `ruta_id` que esto lee.)
_transportadores = sa.table(
    'transportadores',
    sa.column('id', sa.Uuid()),
    sa.column('empresa_id', sa.Uuid()),
    sa.column('ruta_id', sa.Uuid()),
    sa.column('valor_transporte', sa.Numeric(12, 2)),
)

# Solo las dos columnas que hacen falta para comprobar la empresa de la ruta. No
# se filtra `deleted_at`: una ruta borrada en suave SÍ se copia, es historia del
# transportador y su tarifa todavía cobra al recuadrar una quincena vieja (y desde
# el arreglo de la escritura, una ruta borrada ya no traba la edición).
_rutas = sa.table(
    'rutas',
    sa.column('id', sa.Uuid()),
    sa.column('empresa_id', sa.Uuid()),
)

_transportador_rutas = sa.table(
    'transportador_rutas',
    sa.column('id', sa.Uuid()),
    sa.column('transportador_id', sa.Uuid()),
    sa.column('ruta_id', sa.Uuid()),
    sa.column('valor_transporte', sa.Numeric(12, 2)),
    sa.column('created_at', sa.DateTime(timezone=True)),
    sa.column('updated_at', sa.DateTime(timezone=True)),
)


def sentencia_backfill_rutas() -> sa.sql.Insert:
    """El INSERT ... SELECT que copia (ruta_id, valor_transporte) a la tabla puente.

    Devuelve la sentencia SIN ejecutarla, para que la corra `op.execute` (que sirve
    online y offline) o una conexión de prueba.

    DOS COSAS QUE VALE LA PENA LEER DOS VECES:

    · EL JOIN CON `rutas` ES EL FILTRO DE EMPRESA. Un transportador de la Quesera A
      apuntando a una ruta de la B no aparece en el JOIN y no se copia: se queda con
      su tarifa general, que es la única cifra suya que se puede afirmar. Y el JOIN
      no mira `rutas.deleted_at`, así que las borradas en suave sí se copian.

    · EL `id` DE LA FILA PUENTE ES EL `id` DEL TRANSPORTADOR. Suena raro y es a
      propósito: la migración crea A LO SUMO UNA fila por transportador (la columna
      vieja cabía una sola ruta) y la tabla acaba de nacer vacía, así que ese uuid es
      único ahí. A cambio, la sentencia no necesita generar uuids en Python —lo que
      la obligaría a leer las filas una por una y la dejaría sin funcionar en modo
      offline— y queda DETERMINISTA: dos corridas de `alembic upgrade --sql` dan
      exactamente el mismo script, que es lo que un DBA necesita para revisarlo.
    """
    ahora = sa.func.now()
    # Las dos primeras columnas son la misma (`transportadores.id`) y van con
    # `.label()` distinto a propósito: un `select()` deduplica expresiones
    # idénticas y se quedaría con cinco columnas en vez de seis.
    origen = (
        sa.select(
            _transportadores.c.id.label('id'),
            _transportadores.c.id.label('transportador_id'),
            _transportadores.c.ruta_id.label('ruta_id'),
            # La tarifa se COPIA, no se recalcula. El coalesce solo cubre el NULL:
            # la columna vieja era NOT NULL, pero un cero explícito es mejor que
            # arriesgar un NULL en una columna que no lo admite.
            sa.func.coalesce(_transportadores.c.valor_transporte, 0).label(
                'valor_transporte'
            ),
            ahora.label('created_at'),
            ahora.label('updated_at'),
        )
        .select_from(
            _transportadores.join(
                _rutas,
                sa.and_(
                    _rutas.c.id == _transportadores.c.ruta_id,
                    _rutas.c.empresa_id == _transportadores.c.empresa_id,
                ),
            )
        )
        .where(_transportadores.c.ruta_id.is_not(None))
    )
    return sa.insert(_transportador_rutas).from_select(
        [
            'id',
            'transportador_id',
            'ruta_id',
            'valor_transporte',
            'created_at',
            'updated_at',
        ],
        origen,
    )


def backfill_rutas_de_transportadores(conn) -> int:
    """Corre el backfill sobre `conn` y devuelve cuántas filas copió.

    Es el camino de las PRUEBAS, que necesitan la cuenta para verificarla. El
    `upgrade` usa `op.execute(sentencia_backfill_rutas())`, que es la misma
    sentencia y además funciona en modo offline.
    """
    return conn.execute(sentencia_backfill_rutas()).rowcount or 0


def sentencia_restaurar_una_ruta() -> sa.sql.Update:
    """El UPDATE del DOWNGRADE: devuelve UNA sola ruta a la columna vieja.

    La columna `transportadores.ruta_id` solo cabe una, así que al bajar la
    migración un transportador que hacía Nápoles Y Mira Valle PIERDE una de las
    dos: no hay dónde guardarla. Eso no se puede evitar, solo dejarlo dicho.

    LO QUE SÍ SE PUEDE EVITAR ES PAGARLE DE MENOS, y antes no se evitaba. El código
    escogía la ruta de la tarifa MÁS ALTA para el rótulo, pero solo escribía
    `valor_transporte` cuando la general estaba en cero —y el upgrade nunca la deja
    en cero—, así que el rótulo y la cifra quedaban peleados: la vuelta
    upgrade → downgrade → upgrade dejaba la ruta más cara cobrando la tarifa
    general, que es la más baja. En 82 litros de un solo día eso eran miles de pesos
    de menos, y de menos es el lado que se le pierde a la gente.

    LA REGLA AHORA, y es una sola frase: al bajar, el transportador queda con la
    ruta de su tarifa más alta Y CON ESA TARIFA, salvo que su general ya fuera
    mayor, caso en que se respeta la general.

        valor_transporte = el mayor entre (la general, la tarifa más alta de sus rutas)
        ruta_id          = la ruta de esa tarifa más alta

    O sea que la general PUEDE SUBIR al bajar la migración, y es deliberado: con la
    columna vieja el código tiene UN solo número para cobrar todos los litros, de
    todas las rutas. El único número que no le paga de menos a nadie es el mayor de
    todos. Pagar de más se nota en la quincena y se corrige; pagar de menos se le
    pierde al transportador y nadie lo reclama. La otra mitad de la regla —que la
    general nunca BAJA— es la que protege al que tenía una general de verdad.

    Empates de tarifa se rompen por el id de ruta más alto, para que dos corridas
    den el mismo resultado. Es solo cosmético: con la misma tarifa, la plata sale
    igual con cualquiera de las dos.
    """
    tr = _transportador_rutas.alias('tr')
    suyas = tr.c.transportador_id == _transportadores.c.id
    # La tarifa más alta de sus rutas y la ruta a la que pertenece. Van en dos
    # subconsultas y no en una porque la columna vieja necesita las dos cifras en
    # sitios distintos (una en `ruta_id`, la otra en `valor_transporte`).
    mas_alta = sa.select(sa.func.max(tr.c.valor_transporte)).where(suyas).scalar_subquery()
    ruta_escogida = (
        sa.select(tr.c.ruta_id)
        .where(suyas)
        .order_by(tr.c.valor_transporte.desc(), tr.c.ruta_id.desc())
        .limit(1)
        .scalar_subquery()
    )
    general = sa.func.coalesce(_transportadores.c.valor_transporte, 0)
    return (
        sa.update(_transportadores)
        # Solo los que tienen filas puente. Al que no tenía ninguna no se le toca ni
        # la ruta ni la tarifa.
        .where(sa.exists(sa.select(sa.literal_column('1')).where(suyas)))
        .values(
            ruta_id=ruta_escogida,
            # `case` y no una función de dos argumentos: `GREATEST` es de Postgres y
            # `max(a, b)` de SQLite, y esta migración tiene que correr en los dos.
            valor_transporte=sa.case((mas_alta > general, mas_alta), else_=general),
        )
    )


def restaurar_una_ruta_por_transportador(conn) -> int:
    """Corre el restaurador sobre `conn`; devuelve cuántos quedaron con ruta.

    Es el camino de las PRUEBAS. El `downgrade` usa
    `op.execute(sentencia_restaurar_una_ruta())`.
    """
    return conn.execute(sentencia_restaurar_una_ruta()).rowcount or 0


def upgrade() -> None:
    op.create_table(
        'transportador_rutas',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('transportador_id', sa.Uuid(), nullable=False),
        sa.Column('ruta_id', sa.Uuid(), nullable=False),
        sa.Column('valor_transporte', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
        # `sa.func.now()` y no `sa.text('now()')`: el texto se escribe crudo en el
        # DDL y `now()` no existe en SQLite, así que el CREATE TABLE reventaba con
        # un "syntax error" antes de llegar a nada. `func.now()` lo resuelve cada
        # motor —`now()` en Postgres, `CURRENT_TIMESTAMP` en SQLite—, así que en
        # producción el DDL sale IDÉNTICO al de antes y en local por fin corre. Es lo
        # mismo que ya usan los modelos (transportadores/models.py).
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['transportador_id'], ['transportadores.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['ruta_id'], ['rutas.id'], ),
        sa.PrimaryKeyConstraint('id'),
        # Una ruta no puede aparecer dos veces para el mismo transportador: si
        # apareciera con dos tarifas, no habría manera de saber cuál cobra.
        sa.UniqueConstraint('transportador_id', 'ruta_id', name='uq_transportador_ruta'),
    )
    op.create_index(
        op.f('ix_transportador_rutas_transportador_id'),
        'transportador_rutas',
        ['transportador_id'],
        unique=False,
    )

    # PRIMERO se salva el dato y solo DESPUÉS se bota la columna.
    #
    # `op.execute` y no `op.get_bind().execute`: en modo offline (`alembic upgrade
    # --sql`) NO HAY CONEXIÓN, `op.get_bind()` devuelve None, y `op.execute` es lo
    # único que sabe escribir la sentencia en el script en vez de ejecutarla.
    op.execute(sentencia_backfill_rutas())

    # `batch_alter_table` y no `op.drop_column` pelado: `ruta_id` está nombrada en
    # una FOREIGN KEY, y SQLite no sabe botar una columna así. En Postgres
    # (producción) el batch se resuelve en el mismo ALTER TABLE DROP COLUMN de
    # siempre, así que allá no cambia nada; en SQLite recrea la tabla y la migración
    # por fin corre. Importa aunque producción sea Postgres: sin esto, cualquiera que
    # levante el proyecto local en SQLite se estrellaba A MEDIO CAMINO —con
    # `transportador_rutas` ya creada— y el reintento moría con "table already
    # exists", o sea que la base quedaba trabada y había que borrarla a mano.
    with op.batch_alter_table('transportadores', schema=None) as batch_op:
        batch_op.drop_column('ruta_id')


def downgrade() -> None:
    # Se devuelve la columna vieja y UNA ruta por transportador. Lo que se pierde
    # —y no hay forma de no perderlo— son las rutas de más y sus tarifas: la
    # columna `transportadores.ruta_id` cabe una sola, así que del transportador
    # que hacía Nápoles y Mira Valle queda una de las dos y la otra hay que volver
    # a anotarla a mano. Ver `sentencia_restaurar_una_ruta` para cuál se escoge, qué
    # tarifa le queda y por qué.
    #
    # LA FK SE LLAMA `transportadores_ruta_id_fkey` Y NO ES UN NOMBRE CUALQUIERA: en
    # el esquema inicial (a3b2e63208bd) la restricción se creó SIN nombre, y Postgres
    # las autonombra `<tabla>_<columna>_fkey`. El downgrade la rebautizaba
    # `fk_transportadores_ruta_id_rutas`, así que después de bajar y volver a subir
    # la base tenía el mismo esquema con otro nombre de constraint: los diffs de
    # alembic y cualquier script que la busque por nombre quedaban mintiendo.
    with op.batch_alter_table('transportadores', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ruta_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            'transportadores_ruta_id_fkey', 'rutas', ['ruta_id'], ['id']
        )

    op.execute(sentencia_restaurar_una_ruta())

    op.drop_index(op.f('ix_transportador_rutas_transportador_id'), table_name='transportador_rutas')
    op.drop_table('transportador_rutas')
