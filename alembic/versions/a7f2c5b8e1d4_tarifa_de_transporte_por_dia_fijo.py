"""el flete se puede cobrar POR LITRO o POR DÍA FIJO

Lo pidió el dueño así, textual: "en el transporte hay un nuevo requerimiento: que sea
por litro o que sea por día fijo, es decir, el transporte de leche a fábrica vale 150k
independientemente de los litros".

QUÉ HACE, y es lo más chico que se podía hacer: le agrega UNA columna de texto a las
tres tablas donde vive "cómo se cobra este flete", con el valor por omisión 'litro':

  · `transportadores.modo_transporte`      → el modo de la TARIFA GENERAL;
  · `transportador_rutas.modo_transporte`  → el modo de la tarifa DE CADA RUTA, que es
    lo que le deja al dueño tener Nápoles a $242,76 el litro y "a fábrica" a $150.000
    el día CON EL MISMO transportador;
  · `liquidacion_detalles.modo_transporte` → cómo se cobró CADA RENGLÓN de un
    comprobante ya emitido. Va acá y no se deduce de las cifras para que un papel viejo
    siga significando lo mismo para siempre: si mañana esa ruta pasa a día fijo, los
    comprobantes ya firmados guardan su 'litro' y se siguen imprimiendo igual.

Y UNA CUARTA COLUMNA, esta booleana y en una sola tabla:

  · `liquidacion_detalles.dia_fijo_ya_cobrado` → POR QUÉ un renglón de día fijo vale
    $0,00. Son dos razones distintas —el día ya se cobró completo en OTRO comprobante, o
    la tarifa fija de esa ruta es de $0,00 porque el dueño decidió no cobrar ese viaje—
    y el papel que se le entrega al conductor no las puede confundir: sobre la primera
    dice «Ya cobrado» y sobre la segunda «Día completo». Mirando las cifras las dos se
    ven igual (un renglón fijo en cero), así que la razón se GUARDA. Entra en FALSE en
    todas las filas que ya existen, que es la verdad: ninguna se emitió por un día ya
    cobrado —el día fijo no existía—.

'litro' Y FALSE EN TODAS LAS FILAS QUE YA EXISTEN, Y ESO ES TODO EL BACKFILL. No hay ninguna
cifra que mover: 'litro' es exactamente lo que esas tarifas y esos renglones
significaban desde que existen, así que después de subir esta migración el sistema
calcula la misma plata, imprime los mismos papeles y muestra las mismas pantallas. El
día fijo empieza a existir cuando el dueño lo escoja en una tarifa, no antes.

Por eso las columnas van NOT NULL con `server_default` ('litro' las tres de texto, falso
la booleana): el server_default es el que se lo pone a las filas viejas en el mismo ALTER
TABLE, sin un UPDATE aparte que pudiera quedarse a medias.

EL PRE-VUELO Y EL POST-VUELO. Esta migración corre en producción sobre la base de un
cliente real con plata de verdad, y aunque "solo agrega columnas", eso es justo lo que
uno cree hasta que algo se mueve. Así que antes de tocar nada se CUENTAN las filas y se
SUMAN todas las cifras de plata que esta migración podría llegar a rozar; después se
vuelve a medir lo mismo y se comparan. Si una sola cifra cambió, la migración REVIENTA
con un mensaje que dice qué tabla, qué columna, cuánto decía antes y cuánto dice ahora,
y la transacción se va para atrás completa. Y se revisa además lo único que sí es nuevo:
que las tres columnas hayan quedado en 'litro' en TODAS las filas y sin un solo nulo,
porque un nulo ahí es una tarifa sin modo, y una tarifa sin modo es $150.000 que se
pueden leer como el litro.

EL PRE-VUELO SE SALTA EN MODO OFFLINE (`alembic upgrade --sql`), y no es una excepción
caprichosa: allá no hay conexión —`op.get_bind()` devuelve una de mentiras cuyo
`execute` no devuelve filas— así que no hay nada que medir. El script SQL que sale sigue
llevando los tres ALTER TABLE, que es lo que el DBA revisa. Es el mismo límite que ya
está documentado en c6b1e4a8d3f7.

Las funciones de medición están aparte para poder probarlas:
tests/test_transporte_dia_fijo_migracion.py las corre contra una base con la forma
vieja (pytest corre sobre SQLite con `create_all`, así que las migraciones no se
ejercitan solas).

EL DOWNGRADE PIERDE EL MODO, Y ESO CUESTA PLATA SI NO SE HACE NADA. Al bajar, la
columna se va y una tarifa de $150.000 POR DÍA se vuelve una tarifa de $150.000 POR
LITRO: en un día de 300 litros son $45 millones de flete. Al revés —dejarla en cero— el
transportador queda sin tarifa, el comprobante le saldría en $0 y el código viejo YA
sabe avisarlo ("no tiene tarifa de flete —o quedó en cero—... póngale la tarifa y genere
otra vez", ver `_omitido_por_flete_sin_tarifa`): un aviso en la pantalla contra
$45 millones de más. Así que el downgrade DEJA EN CERO las tarifas que estaban en día
fijo y dice cuántas fueron. Hay que volver a anotarlas a mano, y es lo correcto: no
existe ninguna tarifa por litro que signifique lo mismo que "el día vale $150.000".

Revision ID: a7f2c5b8e1d4
Revises: a5c6d7e8f9a0
Create Date: 2026-08-17 09:00:00.000000

"""
from decimal import Decimal
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7f2c5b8e1d4'
down_revision: Union[str, None] = 'a5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Los dos únicos valores que la columna puede tener. Se escriben acá, congelados, y no
# se importan de `app.modules.transportadores.models`: una migración tiene que seguir
# haciendo lo mismo dentro de un año, aunque el modelo cambie de nombres.
MODO_POR_LITRO = 'litro'
MODO_DIA_FIJO = 'dia_fijo'

# LAS TRES TABLAS QUE GANAN LA COLUMNA `modo_transporte`.
_COLUMNAS_NUEVAS = (
    'transportadores',
    'transportador_rutas',
    'liquidacion_detalles',
)

# LA CUARTA COLUMNA, en una sola tabla: por qué un renglón de día fijo vale $0,00. Ver el
# encabezado. Es booleana y entra en falso, así que no le cambia el sentido a ninguna fila
# vieja: el día fijo no existía y ninguna se emitió por un día ya cobrado.
_TABLA_DEL_YA_COBRADO = 'liquidacion_detalles'
_COLUMNA_YA_COBRADO = 'dia_fijo_ya_cobrado'

# QUÉ SE MIDE ANTES Y DESPUÉS: por cada tabla, las columnas de plata (y de litros) que
# esta migración no puede mover ni en un centavo. Además de estas se cuentan las filas.
#
# Se miran las cinco tablas que tocan el flete de punta a punta y no solo las tres que
# ganan columna, porque el descuadre que hay que poder descartar es justamente el que
# NO se ve en la tabla que uno tocó: si algo moviera una foto del flete o el total de un
# comprobante, esto lo delata antes de que el dueño lo encuentre con la calculadora.
_A_MEDIR: dict[str, tuple[str, ...]] = {
    'transportadores': ('valor_transporte',),
    'transportador_rutas': ('valor_transporte',),
    'liquidacion_detalles': ('litros', 'precio_litro', 'valor'),
    'recepciones_leche': ('cantidad_litros', 'valor_transporte'),
    'liquidaciones': ('total_litros', 'valor_transporte', 'valor_total', 'saldo'),
}


def _cifra(valor) -> Decimal:
    """Una suma leída de la base, como Decimal y nunca como float.

    SQLite devuelve las sumas de una columna NUMERIC como float, y comparar dos floats
    de nueve dígitos es como se pierde un centavo sin que nadie lo note. Pasando por
    `str` la cifra queda exacta en los dos motores.
    """
    return Decimal(str(valor)) if valor is not None else Decimal('0')


def medir(conn) -> dict[str, dict[str, Decimal]]:
    """Cuenta las filas y suma las cifras de las cinco tablas del flete.

    Devuelve {tabla: {'filas': n, columna: suma, ...}}, listo para comparar contra
    otra medición igual. Es el PRE-VUELO y el POST-VUELO: la misma función las dos
    veces, porque dos formas de medir es como se explica una diferencia que no existe.
    """
    medido: dict[str, dict[str, Decimal]] = {}
    for tabla, columnas in _A_MEDIR.items():
        seleccion = [sa.func.count().label('filas')]
        seleccion += [
            sa.func.sum(sa.column(nombre)).label(nombre) for nombre in columnas
        ]
        fila = conn.execute(sa.select(*seleccion).select_from(sa.table(tabla))).one()
        medido[tabla] = {'filas': Decimal(fila.filas or 0)}
        for nombre in columnas:
            medido[tabla][nombre] = _cifra(getattr(fila, nombre))
    return medido


def exigir_que_nada_se_movio(
    antes: dict[str, dict[str, Decimal]], despues: dict[str, dict[str, Decimal]]
) -> None:
    """Revienta si una sola fila o un solo centavo cambió. Con el mensaje entendible.

    El mensaje dice tabla, columna, antes y después, en ese orden, porque es lo que
    hace falta para saber si hay que devolver la base o si el susto era otro. Se lanza
    un RuntimeError y la migración va dentro de una transacción, así que el ALTER TABLE
    se va para atrás con él: la base queda como estaba.
    """
    problemas: list[str] = []
    for tabla, medidas in antes.items():
        for columna, valor in medidas.items():
            ahora = despues.get(tabla, {}).get(columna)
            if ahora is None or ahora != valor:
                que = 'filas' if columna == 'filas' else f'la suma de {columna}'
                problemas.append(
                    f"  · {tabla}: {que} decía {valor} y ahora dice {ahora}"
                )
    if problemas:
        raise RuntimeError(
            "MIGRACIÓN DETENIDA (a7f2c5b8e1d4, modo de la tarifa de transporte): esta "
            "migración solo agrega columnas de texto con el valor por omisión 'litro' y "
            "NO PUEDE mover ninguna cifra ni ninguna fila, pero algo se movió:\n"
            + "\n".join(problemas)
            + "\nNo se aplicó nada: la transacción se devolvió completa. Revise la base "
            "antes de volver a intentarlo."
        )


def exigir_todo_por_litro(conn) -> dict[str, int]:
    """Revisa que las tres columnas nuevas quedaran en 'litro' en TODAS las filas.

    ES LA MITAD QUE DE VERDAD IMPORTA del post-vuelo. Que ninguna cifra se haya movido
    es necesario, pero no alcanza: lo que esta migración promete es que TODO LO QUE YA
    EXISTE SIGA SIGNIFICANDO EXACTAMENTE LO MISMO, y eso se rompe de dos formas
    silenciosas:

      · un NULO en la columna → una tarifa sin modo. `tarifas._modo_de` la leería como
        'litro' (y por eso el sistema no se caería), pero la columna es NOT NULL: un
        nulo ahí significa que el server_default no se aplicó, o sea que el motor hizo
        algo distinto de lo que este archivo cree;
      · un 'dia_fijo' recién nacido → una tarifa por litro convertida en día fijo sin
        que nadie lo pidiera. En una ruta a $242,76 eso pasaría de cobrar $242,76 el
        litro a cobrar $242,76 el DÍA: el transportador trabajando casi gratis.

    Devuelve cuántas filas se revisaron en cada tabla, que es lo que el log del deploy
    debería mostrar.
    """
    revisadas: dict[str, int] = {}
    for tabla in _COLUMNAS_NUEVAS:
        objeto = sa.table(tabla, sa.column('modo_transporte'))
        raros = conn.execute(
            sa.select(sa.func.count()).select_from(objeto).where(
                sa.or_(
                    objeto.c.modo_transporte.is_(None),
                    objeto.c.modo_transporte != MODO_POR_LITRO,
                )
            )
        ).scalar_one()
        total = conn.execute(
            sa.select(sa.func.count()).select_from(sa.table(tabla))
        ).scalar_one()
        if raros:
            raise RuntimeError(
                "MIGRACIÓN DETENIDA (a7f2c5b8e1d4, modo de la tarifa de transporte): "
                f"después de agregar la columna, {raros} de las {total} filas de "
                f"'{tabla}' NO quedaron en '{MODO_POR_LITRO}'. Todo lo que existía antes "
                "de este cambio se cobraba por litro, así que esas filas estarían "
                "cobrando de otra forma sin que nadie lo pidiera —una tarifa de $242,76 "
                "por litro pasaría a valer $242,76 el día completo—. No se aplicó nada: "
                "la transacción se devolvió completa."
            )
        revisadas[tabla] = int(total)
    return revisadas


def exigir_ningun_dia_fijo_ya_cobrado(conn) -> int:
    """Revisa que la columna nueva del "ya cobrado" quedara en FALSO en TODAS las filas.

    Es la misma idea que `exigir_todo_por_litro`, y por la misma razón: lo que esta
    migración promete es que ningún papel ya emitido cambie de significado. Un TRUE recién
    nacido acá haría que un comprobante viejo imprimiera «Ya cobrado» sobre un renglón,
    o sea que le AFIRMARÍA AL CONDUCTOR que ese día ya se le pagó en otra parte. Ninguna
    de las filas que existen hoy puede estar en ese caso: el día fijo no existía antes de
    esta migración.

    Devuelve cuántas filas se revisaron, que es lo que el log del deploy debería mostrar.
    """
    objeto = sa.table(_TABLA_DEL_YA_COBRADO, sa.column(_COLUMNA_YA_COBRADO))
    raros = conn.execute(
        sa.select(sa.func.count()).select_from(objeto).where(
            sa.or_(
                objeto.c[_COLUMNA_YA_COBRADO].is_(None),
                objeto.c[_COLUMNA_YA_COBRADO].is_(True),
            )
        )
    ).scalar_one()
    total = conn.execute(
        sa.select(sa.func.count()).select_from(sa.table(_TABLA_DEL_YA_COBRADO))
    ).scalar_one()
    if raros:
        raise RuntimeError(
            "MIGRACIÓN DETENIDA (a7f2c5b8e1d4, modo de la tarifa de transporte): "
            f"después de agregar la columna, {raros} de las {total} filas de "
            f"'{_TABLA_DEL_YA_COBRADO}' NO quedaron con {_COLUMNA_YA_COBRADO} en falso. "
            "Esa marca hace que el comprobante le imprima «Ya cobrado» al conductor, o "
            "sea que le afirme que ese día ya se le pagó en otro comprobante; ninguno de "
            "los renglones que existen hoy puede estar en ese caso. No se aplicó nada: la "
            "transacción se devolvió completa."
        )
    return int(total)


def apagar_las_tarifas_de_dia_fijo(conn) -> int:
    """EL DOWNGRADE: deja en CERO las tarifas que estaban en día fijo. Devuelve cuántas.

    Sin esto, bajar la migración le pone precio por litro a una cifra que era el precio
    del día: $150.000 el día se vuelven $150.000 el litro, y en un día de 300 L eso son
    $45 millones de flete que la quesera le pagaría a un conductor. Con la tarifa en
    cero el comprobante le sale en $0 y el código viejo YA avisa que hay que ponerle la
    tarifa (ver `_omitido_por_flete_sin_tarifa`): un aviso en la pantalla es un problema
    mucho más chico que $45 millones de más.

    Se apagan las DOS tarifas —la general del transportador y la de cada ruta— porque
    las dos pueden estar en día fijo, y solo esas: una tarifa que ya era por litro no se
    toca ni un peso.

    NO se toca `liquidacion_detalles`: sus renglones son papeles ya emitidos y su
    `valor` es la plata que se cobró. Perder la etiqueta del modo hace que un renglón de
    día fijo se imprima con "$0,00" en la columna Precio/L —feo, pero cierto: no había
    tarifa por litro— y el total del comprobante sigue siendo el mismo. Cambiarle el
    valor a un comprobante firmado sería mucho peor que imprimirlo raro.
    """
    apagadas = 0
    for tabla in ('transportadores', 'transportador_rutas'):
        objeto = sa.table(
            tabla,
            sa.column('valor_transporte', sa.Numeric(12, 2)),
            sa.column('modo_transporte'),
        )
        resultado = conn.execute(
            sa.update(objeto)
            .where(objeto.c.modo_transporte == MODO_DIA_FIJO)
            .values(valor_transporte=0)
        )
        apagadas += resultado.rowcount or 0
    return apagadas


def _conexion_para_medir():
    """La conexión de la que se puede LEER, o None si no hay ninguna.

    Se pregunta por `op.get_context().as_sql` —el modo offline de alembic, o sea
    `alembic upgrade --sql`— y no por `alembic.context.is_offline_mode()`, y hay dos
    razones: `as_sql` es un atributo del contexto de la migración que existe siempre, y
    `alembic.context` es un proxy que solo está instalado cuando la migración corre
    dentro de `env.py`. Con `op` la migración se puede correr —y probar— con un contexto
    armado a mano, que es lo que hace
    tests/test_transporte_dia_fijo_migracion.py: prueba EL upgrade de verdad, el mismo
    que va a correr en producción, y no una copia de su lógica.
    """
    return None if op.get_context().as_sql else op.get_bind()


def upgrade() -> None:
    # PRE-VUELO. En modo offline (`alembic upgrade --sql`) no hay conexión de la que
    # leer, así que no se mide: el script sale con los tres ALTER TABLE y nada más.
    conexion = _conexion_para_medir()
    antes = medir(conexion) if conexion is not None else None

    for tabla in _COLUMNAS_NUEVAS:
        # NOT NULL con server_default='litro': el mismo ALTER TABLE se lo pone a todas
        # las filas que ya existen, sin un UPDATE aparte que pudiera quedarse a medias.
        # Y el server_default SE QUEDA en la columna a propósito: si mañana entra una
        # fila por un camino que no sepa del modo (un script, un INSERT a mano), cae en
        # 'litro', que es el significado de siempre y el único que no cobra de más.
        op.add_column(
            tabla,
            sa.Column(
                'modo_transporte',
                sa.String(length=10),
                nullable=False,
                server_default=MODO_POR_LITRO,
            ),
        )

    # LA CUARTA: por qué un renglón de día fijo vale $0,00. NOT NULL con server_default
    # falso, por lo mismo que arriba: el ALTER TABLE se lo pone a todas las filas viejas y
    # la columna queda con el default puesto, así que una fila que entre por un camino que
    # no sepa de esto cae en "no es un día ya cobrado", que es lo único que no le afirma
    # nada falso al conductor.
    op.add_column(
        _TABLA_DEL_YA_COBRADO,
        sa.Column(
            _COLUMNA_YA_COBRADO,
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    if conexion is None:
        return
    # POST-VUELO: ninguna cifra se movió, las tres columnas quedaron en 'litro' y ningún
    # renglón viejo quedó marcado como "ya cobrado en otro comprobante".
    exigir_que_nada_se_movio(antes, medir(conexion))
    exigir_todo_por_litro(conexion)
    exigir_ningun_dia_fijo_ya_cobrado(conexion)


def downgrade() -> None:
    # Primero se apagan las tarifas de día fijo y SOLO DESPUÉS se botan las columnas:
    # al revés no habría con qué saber cuáles eran. Ver
    # `apagar_las_tarifas_de_dia_fijo` para el porqué de dejarlas en cero.
    conexion = _conexion_para_medir()
    if conexion is not None:
        apagar_las_tarifas_de_dia_fijo(conexion)

    # `batch_alter_table` y no `op.drop_column` pelado, por lo mismo que en
    # c6b1e4a8d3f7: en Postgres (producción) el batch se resuelve en el mismo ALTER
    # TABLE DROP COLUMN de siempre, y en SQLite recrea la tabla, que es lo único que
    # funciona en las versiones viejas. Sin esto, bajar la migración en local dejaba la
    # base a medio camino.
    #
    # La del "ya cobrado" se va primero, en el orden inverso al que entró. Perderla no
    # cuesta plata —el `valor` del renglón no se toca— y lo único que pasa es que un
    # renglón fijo en cero deja de poder explicar POR QUÉ está en cero; el código viejo
    # tampoco sabía preguntarlo.
    with op.batch_alter_table(_TABLA_DEL_YA_COBRADO, schema=None) as batch_op:
        batch_op.drop_column(_COLUMNA_YA_COBRADO)
    for tabla in reversed(_COLUMNAS_NUEVAS):
        with op.batch_alter_table(tabla, schema=None) as batch_op:
            batch_op.drop_column('modo_transporte')
