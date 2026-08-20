"""el comprobante GUARDA cómo cobró cada ruta, en vez de deducirlo

EL DEFECTO QUE CIERRA, con las cifras medidas. Un comprobante de flete sabe cómo cobró
cada ruta —por litro a $242,76, o el día completo a $150.000— y eso se DEDUCÍA de los
renglones que le quedaran. Mientras al papel le quedara AL MENOS UN renglón de esa ruta,
la deducción acertaba; cuando el comprobante se quedaba SIN NINGÚN RENGLÓN de esa ruta se
quedaba sin de dónde deducir, y el siguiente recuadre lo re-precificaba con la tarifa de
HOY. Dos caminos lo lograban y NINGUNO de los dos oprime Recalcular:

  · apagar el día y volver a prenderlo;
  · corregirle la ruta al día y devolvérsela.

El salto medido, sobre un día de 82,00 L en la ruta "A fábrica": el comprobante emitido
por DÍA COMPLETO en $150.000,00 amanecía en $19.906,32 (82 L × $242,76), y el emitido POR
LITRO en $19.906,32 amanecía en $150.000,00. Son $130.093,68 que se le pagan de menos o de
más al conductor, y el PDF cambiaba de «Día completo $150.000» a «82 L × $242,76 =
$19.906,32». El comprobante se caía a borrador, lo cual MITIGA pero NO CIERRA: se vuelve a
aprobar y se paga la cifra nueva.

QUÉ HACE ESTA MIGRACIÓN, y es lo más chico que cierra eso: crea UNA tabla donde el
comprobante deja ESCRITO cómo cobró cada ruta en el momento en que se emite.

  · `liquidacion_rutas` — una fila por (comprobante, ruta) con el modo ('litro' o
    'dia_fijo') y, según el modo, LA TARIFA con que se emitió (`precio_litro`) o LA CIFRA
    del día completo (`valor_dia_fijo`). Ver `LiquidacionRuta` en
    app/modules/liquidaciones/models.py, que es donde está escrita la regla completa.

`ruta_id` VA ANULABLE a propósito: esa fila es la de la TARIFA GENERAL, la que cobra la
recepción que quedó sin ruta. Su día también se puede apagar y prender, así que sin la
fila nula ese caso se quedaba con la puerta abierta.

EL BACKFILL: LOS COMPROBANTES QUE YA EXISTEN TIENEN QUE SEGUIR SIGNIFICANDO LO MISMO.
En la base del cliente no hay ni un comprobante con esta memoria, y dejarla vacía sería
dejar el defecto abierto para todo lo que ya está emitido. Así que se les ESCRIBE, y se
escribe leyendo SUS PROPIOS RENGLONES, que es de donde el código la deducía:

  · el modo: 'dia_fijo' si alguno de los renglones de ese (comprobante, ruta) es de día
    fijo, y 'litro' si no. Hoy todo lo que existe es 'litro' —el día fijo acaba de nacer
    en la migración anterior, que verificó una por una que TODAS las filas quedaran en
    'litro'—, así que en la base del cliente esta cuenta va a escribir 'litro' en todas.
    Se escribe la cuenta completa igual, y no un 'litro' pelado, porque es la misma regla
    que usa el código (`_deducir_de_los_renglones`) y porque una migración tiene que
    seguir siendo correcta si la corre alguien con datos que no son los de hoy;
  · la tarifa: la ÚNICA tarifa por litro que aparece en los renglones de ese (comprobante,
    ruta). Si aparecen dos distintas —líneas partidas de un flete que ya se pagó— se deja
    en NULO, que es exactamente lo que el código concluye: ahí no existe "la tarifa con
    que se emitió" y no hay nada que heredar. Inventar un promedio sería peor que un nulo;
  · el fijo: la cifra más alta de los renglones de día fijo que de verdad cobraron algo.
    Un renglón fijo en $0,00 no dice cuánto cuesta el viaje, así que no sirve de
    referencia.

O sea que después de subir esta migración el sistema calcula LA MISMA PLATA, imprime LOS
MISMOS PAPELES y muestra LAS MISMAS PANTALLAS. Lo único que cambia es que ahora la memoria
está escrita y ninguna puerta se la puede borrar.

NO SE TOCA NI UNA CIFRA DE LAS QUE YA EXISTEN. Esta migración solo crea una tabla y le
mete filas nuevas; no hace un solo UPDATE sobre plata. Aun así el pre-vuelo y el
post-vuelo miden las cinco tablas del flete de punta a punta, porque "solo agrega una
tabla" es justo lo que uno cree hasta que algo se mueve.

EL PRE-VUELO Y EL POST-VUELO. Antes de tocar nada se CUENTAN las filas y se SUMAN todas
las cifras de plata que esta migración podría llegar a rozar; después se vuelve a medir lo
mismo y se comparan. Si una sola cifra cambió, la migración REVIENTA con un mensaje que
dice qué tabla, qué columna, cuánto decía antes y cuánto dice ahora, y la transacción se va
para atrás completa. Y se revisa además lo único que sí es nuevo: que la memoria escrita
diga EXACTAMENTE lo que dicen los renglones de cada comprobante, comprobante por
comprobante y ruta por ruta. Una memoria que diga otra cosa es un papel que va a cambiar de
cifra solo, que es el defecto con otro disfraz.

EL PRE-VUELO SE SALTA EN MODO OFFLINE (`alembic upgrade --sql`), y no es una excepción
caprichosa: allá no hay conexión —`op.get_bind()` devuelve una de mentiras cuyo `execute`
no devuelve filas— así que no hay nada que medir. EL BACKFILL SÍ SALE EN EL SCRIPT, y por
eso es un INSERT ... SELECT y no un recorrido en Python: la sentencia se escribe entera en
el archivo .sql que el DBA revisa, y dos corridas de `alembic upgrade --sql` dan el mismo
script. Es la misma decisión (y por la misma razón) que ya está tomada en c6b1e4a8d3f7.

EL `id` DE CADA FILA NUEVA ES EL DE UNO DE SUS RENGLONES, el menor del grupo. Suena raro y
es a propósito, igual que en c6b1e4a8d3f7: cada grupo (comprobante, ruta) escoge un renglón
distinto, los ids de los renglones son únicos y la tabla acaba de nacer vacía, así que no
se repiten. A cambio, la sentencia no necesita generar uuids en Python —lo que la obligaría
a leer las filas una por una y la dejaría sin funcionar en modo offline— y queda
DETERMINISTA. Se toma el mínimo SOBRE EL TEXTO del id y no sobre el uuid pelado porque
`min()` sobre texto existe en los dos motores sin depender de la versión; cuál de los ids
del grupo salga elegido no le importa a nadie, lo único que hace falta es que sea siempre
el mismo.

EL DOWNGRADE BOTA LA TABLA Y NO MUEVE UN PESO. Lo que se pierde al bajar es la memoria, o
sea que las dos puertas vuelven a abrirse: la deducción de siempre sigue funcionando
mientras al comprobante le quede otro renglón de esa ruta, y cuando no le quede vuelve a
re-precificar con la tarifa de hoy. Ninguna tarifa se toca y ningún renglón cambia de
cifra, así que bajar no le cuesta plata a nadie de inmediato —a diferencia del downgrade de
a7f2c5b8e1d4, que sí tiene que apagar tarifas—: deja el sistema como estaba antes de este
arreglo.

Revision ID: c3f8a1d6b0e5
Revises: a7f2c5b8e1d4
Create Date: 2026-08-19 10:00:00.000000

"""
from decimal import Decimal
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f8a1d6b0e5'
down_revision: Union[str, None] = 'a7f2c5b8e1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Los dos modos, congelados acá y NO importados de
# `app.modules.transportadores.models`: una migración tiene que seguir haciendo lo mismo
# dentro de un año, aunque el modelo cambie de nombres. Es la misma decisión de
# a7f2c5b8e1d4.
MODO_POR_LITRO = 'litro'
MODO_DIA_FIJO = 'dia_fijo'
MODOS = (MODO_POR_LITRO, MODO_DIA_FIJO)

# Solo los comprobantes DEL TRANSPORTADOR cobran rutas. Los del proveedor tienen un
# renglón por día y `ruta_id` en nulo siempre: escribirles memoria sería llenar la tabla
# de filas que nadie va a leer.
TIPO_TRANSPORTADOR = 'transportador'

LA_TABLA = 'liquidacion_rutas'

# QUÉ SE MIDE ANTES Y DESPUÉS: por cada tabla, las columnas de plata (y de litros) que
# esta migración no puede mover ni en un centavo. Además de estas se cuentan las filas.
#
# Son las cinco tablas que tocan el flete de punta a punta, las mismas de a7f2c5b8e1d4 y
# por la misma razón: el descuadre que hay que poder descartar es justamente el que NO se
# ve en la tabla que uno tocó. Acá no se tocó ninguna —la tabla nueva nace vacía— así que
# cualquier diferencia es un defecto.
_A_MEDIR: dict[str, tuple[str, ...]] = {
    'transportadores': ('valor_transporte',),
    'transportador_rutas': ('valor_transporte',),
    'liquidacion_detalles': ('litros', 'precio_litro', 'valor'),
    'recepciones_leche': ('cantidad_litros', 'valor_transporte'),
    'liquidaciones': ('total_litros', 'valor_transporte', 'valor_total', 'saldo'),
}


# ---------------------------------------------------------------------------
# Las tablas, en la forma mínima que estas sentencias necesitan
# ---------------------------------------------------------------------------
_liquidaciones = sa.table(
    'liquidaciones',
    sa.column('id', sa.Uuid()),
    sa.column('tipo', sa.String()),
)
_detalles = sa.table(
    'liquidacion_detalles',
    sa.column('id', sa.Uuid()),
    sa.column('liquidacion_id', sa.Uuid()),
    sa.column('ruta_id', sa.Uuid()),
    sa.column('precio_litro', sa.Numeric(12, 2)),
    sa.column('valor', sa.Numeric(14, 2)),
    sa.column('modo_transporte', sa.String()),
    sa.column('deleted_at', sa.DateTime(timezone=True)),
)
_memoria = sa.table(
    LA_TABLA,
    sa.column('id', sa.Uuid()),
    sa.column('liquidacion_id', sa.Uuid()),
    sa.column('ruta_id', sa.Uuid()),
    sa.column('modo_transporte', sa.String()),
    sa.column('precio_litro', sa.Numeric(12, 2)),
    sa.column('valor_dia_fijo', sa.Numeric(12, 2)),
    # Las dos van declaradas aunque nadie las lea: el INSERT ... SELECT las nombra, y una
    # `sa.table()` sin la columna deja la sentencia sin saber a dónde escribir.
    sa.column('created_at', sa.DateTime(timezone=True)),
    sa.column('updated_at', sa.DateTime(timezone=True)),
)


def _cifra(valor) -> Decimal:
    """Una suma leída de la base, como Decimal y nunca como float.

    SQLite devuelve las sumas de una columna NUMERIC como float, y comparar dos floats de
    nueve dígitos es como se pierde un centavo sin que nadie lo note. Pasando por `str` la
    cifra queda exacta en los dos motores.
    """
    return Decimal(str(valor)) if valor is not None else Decimal('0')


def _cifra_o_nada(valor) -> Decimal | None:
    """Lo mismo, pero un NULO se queda NULO.

    Acá la diferencia entre un nulo y un cero es toda la información: `precio_litro` en
    nulo quiere decir "este papel no tiene UNA tarifa con que se emitió" (líneas partidas
    de un flete pagado), y en cero querría decir "se emitió a $0,00 el litro". Confundirlas
    haría que un día que vuelve naciera cobrando cero.
    """
    return None if valor is None else Decimal(str(valor))


def medir(conn) -> dict[str, dict[str, Decimal]]:
    """Cuenta las filas y suma las cifras de las cinco tablas del flete.

    Devuelve {tabla: {'filas': n, columna: suma, ...}}, listo para comparar contra otra
    medición igual. Es el PRE-VUELO y el POST-VUELO: la misma función las dos veces, porque
    dos formas de medir es como se explica una diferencia que no existe.
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

    El mensaje dice tabla, columna, antes y después, en ese orden, porque es lo que hace
    falta para saber si hay que devolver la base o si el susto era otro. Se lanza un
    RuntimeError y la migración va dentro de una transacción, así que el CREATE TABLE y el
    backfill se van para atrás con él: la base queda como estaba.
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
            "MIGRACIÓN DETENIDA (c3f8a1d6b0e5, el comprobante guarda cómo cobró cada "
            "ruta): esta migración solo crea la tabla 'liquidacion_rutas' y le escribe "
            "filas nuevas, y NO PUEDE mover ninguna cifra ni ninguna fila de las que ya "
            "existen, pero algo se movió:\n"
            + "\n".join(problemas)
            + "\nNo se aplicó nada: la transacción se devolvió completa. Revise la base "
            "antes de volver a intentarlo."
        )


# ---------------------------------------------------------------------------
# LA CUENTA DE LA MEMORIA: qué dicen los renglones de cada (comprobante, ruta)
# ---------------------------------------------------------------------------
def _columnas_de_la_memoria() -> tuple[Any, Any, Any]:
    """Las tres expresiones que leen el modo, la tarifa y el fijo de un grupo agrupado.

    Es LA MISMA regla que aplica el código sobre los renglones vivos
    (`_deducir_de_los_renglones` en app/modules/liquidaciones/service.py), escrita en SQL
    para que el backfill pueda correr en una sola sentencia —y por lo tanto salir entera en
    el script del modo offline—. Que sean dos escrituras de la misma regla es el precio de
    poder revisar el .sql; el post-vuelo compara lo escrito contra los renglones justamente
    para que las dos no se puedan separar en silencio.
    """
    # EL MODO: día fijo si ALGUNO de los renglones del grupo es de día fijo. Un fijo no se
    # puede repartir en líneas por litro sin inventarle una tarifa a cada trozo, así que
    # basta uno para que el grupo entero sea fijo.
    es_fijo = sa.case((_detalles.c.modo_transporte == MODO_DIA_FIJO, 1), else_=0)
    modo = sa.case(
        (sa.func.max(es_fijo) == 1, sa.literal(MODO_DIA_FIJO)),
        else_=sa.literal(MODO_POR_LITRO),
    ).label('modo_transporte')

    # LA TARIFA POR LITRO: solo la de los renglones que NO son de día fijo, y solo si
    # todos dicen la misma. Dos tarifas distintas para la misma ruta son un papel partido
    # (un flete que ya se pagó) y no una tarifa que heredar: ahí va NULO.
    #
    # `coalesce(precio_litro, 0)` porque la columna es NOT NULL con cero por omisión y el
    # código la lee igual (`Decimal(precio_litro or 0)`): un nulo plantado a mano tiene que
    # contar como el cero que el código va a leer, no desaparecer del COUNT DISTINCT.
    solo_por_litro = sa.case(
        (
            _detalles.c.modo_transporte != MODO_DIA_FIJO,
            sa.func.coalesce(_detalles.c.precio_litro, 0),
        ),
        else_=None,
    )
    precio = sa.case(
        (
            sa.func.count(sa.distinct(solo_por_litro)) == 1,
            sa.func.max(solo_por_litro),
        ),
        else_=None,
    ).label('precio_litro')

    # EL FIJO: la cifra más alta de los renglones de día fijo QUE DE VERDAD COBRARON algo.
    # Un renglón fijo en $0,00 —el día ya cobrado en otro comprobante, o una tarifa fija de
    # cero— no dice cuánto cuesta el viaje, así que como referencia no sirve.
    fijo = sa.func.max(
        sa.case(
            (
                sa.and_(
                    _detalles.c.modo_transporte == MODO_DIA_FIJO,
                    _detalles.c.valor > 0,
                ),
                _detalles.c.valor,
            ),
            else_=None,
        )
    ).label('valor_dia_fijo')
    return modo, precio, fijo


def _grupos_de_la_memoria() -> sa.sql.Select:
    """El SELECT agrupado: un (comprobante, ruta) por fila, con su modo, tarifa y fijo.

    Solo comprobantes DEL TRANSPORTADOR y solo renglones VIVOS (`deleted_at IS NULL`), que
    es lo mismo que mira el código.

    NO FILTRA `empresa_id` y no puede: agrupa por `liquidacion_id`, así que cada fila
    resultante sale de UNA sola liquidación y por lo tanto de UNA sola empresa. No hay
    forma de que dos queseras se mezclen en la misma fila. El aislamiento de la LECTURA lo
    pone después el ORM, que llega a estas filas únicamente por
    `liquidacion.rutas_cobradas` (ver `LiquidacionRuta`).
    """
    modo, precio, fijo = _columnas_de_la_memoria()
    return (
        sa.select(
            _detalles.c.liquidacion_id.label('liquidacion_id'),
            _detalles.c.ruta_id.label('ruta_id'),
            modo,
            precio,
            fijo,
        )
        .select_from(
            _detalles.join(
                _liquidaciones, _liquidaciones.c.id == _detalles.c.liquidacion_id
            )
        )
        .where(
            _liquidaciones.c.tipo == TIPO_TRANSPORTADOR,
            _detalles.c.deleted_at.is_(None),
        )
        .group_by(_detalles.c.liquidacion_id, _detalles.c.ruta_id)
    )


def sentencia_backfill_de_la_memoria() -> sa.sql.Insert:
    """El INSERT ... SELECT que le escribe la memoria a los comprobantes que ya existen.

    Devuelve la sentencia SIN ejecutarla, para que la corra `op.execute` (que sirve online
    y offline) o una conexión de prueba.

    El `id` de cada fila es el del renglón menor del grupo, tomado sobre el TEXTO del id.
    Ver el encabezado del archivo para el porqué: sale determinista, no necesita generar
    uuids en Python y por lo tanto la sentencia entera cabe en el script del modo offline.
    """
    modo, precio, fijo = _columnas_de_la_memoria()
    ahora = sa.func.now()
    origen = (
        sa.select(
            sa.cast(sa.func.min(sa.cast(_detalles.c.id, sa.Text)), sa.Uuid()).label('id'),
            _detalles.c.liquidacion_id.label('liquidacion_id'),
            _detalles.c.ruta_id.label('ruta_id'),
            modo,
            precio,
            fijo,
            # `sa.func.now()` y no `sa.text('now()')`: el texto se escribe crudo en el DDL
            # y `now()` no existe en SQLite. Es la misma nota de c6b1e4a8d3f7.
            ahora.label('created_at'),
            ahora.label('updated_at'),
        )
        .select_from(
            _detalles.join(
                _liquidaciones, _liquidaciones.c.id == _detalles.c.liquidacion_id
            )
        )
        .where(
            _liquidaciones.c.tipo == TIPO_TRANSPORTADOR,
            _detalles.c.deleted_at.is_(None),
        )
        .group_by(_detalles.c.liquidacion_id, _detalles.c.ruta_id)
    )
    return sa.insert(_memoria).from_select(
        [
            'id',
            'liquidacion_id',
            'ruta_id',
            'modo_transporte',
            'precio_litro',
            'valor_dia_fijo',
            'created_at',
            'updated_at',
        ],
        origen,
    )


def backfill_de_la_memoria(conn) -> int:
    """Corre el backfill sobre `conn` y devuelve cuántas filas escribió.

    Es el camino de las PRUEBAS, que necesitan la cuenta para verificarla. El `upgrade`
    usa `op.execute(sentencia_backfill_de_la_memoria())`, que es la misma sentencia y
    además funciona en modo offline.
    """
    return conn.execute(sentencia_backfill_de_la_memoria()).rowcount or 0


def _clave(liquidacion_id, ruta_id) -> tuple[str, str | None]:
    """(comprobante, ruta) como texto, para poder comparar los dos lados.

    Como texto y no como uuid porque SQLite devuelve el id como cadena de 32 caracteres y
    Postgres como uuid: comparando los objetos pelados, los dos lados de la comparación
    podrían no encontrarse nunca y el post-vuelo diría que todo está bien.
    """
    return (str(liquidacion_id), None if ruta_id is None else str(ruta_id))


def memoria_que_dicen_los_renglones(conn) -> dict[tuple[str, str | None], tuple]:
    """Lo que la memoria TIENE que decir, leído de los renglones de cada comprobante."""
    return {
        _clave(fila.liquidacion_id, fila.ruta_id): (
            fila.modo_transporte,
            _cifra_o_nada(fila.precio_litro),
            _cifra_o_nada(fila.valor_dia_fijo),
        )
        for fila in conn.execute(_grupos_de_la_memoria())
    }


def memoria_escrita(conn) -> dict[tuple[str, str | None], tuple]:
    """Lo que la memoria DICE, leído de la tabla nueva."""
    escrita: dict[tuple[str, str | None], tuple] = {}
    for fila in conn.execute(
        sa.select(
            _memoria.c.liquidacion_id,
            _memoria.c.ruta_id,
            _memoria.c.modo_transporte,
            _memoria.c.precio_litro,
            _memoria.c.valor_dia_fijo,
        )
    ):
        clave = _clave(fila.liquidacion_id, fila.ruta_id)
        if clave in escrita:
            raise RuntimeError(
                "MIGRACIÓN DETENIDA (c3f8a1d6b0e5, el comprobante guarda cómo cobró cada "
                f"ruta): el comprobante {clave[0]} quedó con DOS filas de memoria para la "
                f"misma ruta ({clave[1]}). Con dos, no hay manera de saber cómo cobró esa "
                "ruta. No se aplicó nada: la transacción se devolvió completa."
            )
        escrita[clave] = (
            fila.modo_transporte,
            _cifra_o_nada(fila.precio_litro),
            _cifra_o_nada(fila.valor_dia_fijo),
        )
    return escrita


def exigir_la_memoria_igual_a_los_renglones(conn) -> int:
    """La memoria escrita tiene que decir EXACTAMENTE lo que dicen los renglones.

    ES LA MITAD QUE DE VERDAD IMPORTA del post-vuelo. Que ninguna cifra se haya movido es
    necesario pero no alcanza: lo que esta migración promete es que TODO LO QUE YA EXISTE
    SIGA SIGNIFICANDO EXACTAMENTE LO MISMO, y una memoria que diga otra cosa que los
    renglones es un comprobante que va a cambiar de cifra solo —el defecto con otro
    disfraz—. Los tres modos de romperlo:

      · una ruta SIN fila: el comprobante se queda sin memoria de esa ruta y vuelve a
        quedar expuesto a las dos puertas;
      · una fila que dice OTRO MODO: el papel emitido por litro amanecería cobrando el día
        completo, o al revés. Son $130.093,68 en el caso medido;
      · una fila que dice OTRA TARIFA: el día que vuelve nacería a una tarifa que ese
        comprobante nunca cobró.

    Y también se revisa que el modo sea uno de los dos que existen: un modo raro se leería
    como 'litro' (así lo hace `tarifas._modo_de`, a propósito), y una ruta que se cobró por
    día completo leída por litro cobra $150.000 el litro.

    Devuelve cuántas filas quedaron escritas, que es lo que el log del deploy debería
    mostrar.
    """
    esperada = memoria_que_dicen_los_renglones(conn)
    escrita = memoria_escrita(conn)

    problemas: list[str] = []
    for clave, debia in esperada.items():
        dice = escrita.get(clave)
        if dice is None:
            problemas.append(
                f"  · comprobante {clave[0]}, ruta {clave[1]}: no le quedó fila de "
                f"memoria, y sus renglones dicen {debia}"
            )
        elif dice != debia:
            problemas.append(
                f"  · comprobante {clave[0]}, ruta {clave[1]}: sus renglones dicen "
                f"{debia} y la memoria dice {dice}"
            )
    for clave, dice in escrita.items():
        if clave not in esperada:
            problemas.append(
                f"  · comprobante {clave[0]}, ruta {clave[1]}: le quedó una fila de "
                f"memoria ({dice}) de una ruta que ese comprobante no cobra"
            )
        elif dice[0] not in MODOS:
            problemas.append(
                f"  · comprobante {clave[0]}, ruta {clave[1]}: el modo quedó en "
                f"'{dice[0]}', que no es ni '{MODO_POR_LITRO}' ni '{MODO_DIA_FIJO}'"
            )
    if problemas:
        raise RuntimeError(
            "MIGRACIÓN DETENIDA (c3f8a1d6b0e5, el comprobante guarda cómo cobró cada "
            "ruta): la memoria que quedó escrita NO dice lo mismo que los renglones de "
            "esos comprobantes, y esa memoria es la que decide con qué tarifa se rehace un "
            "día que vuelve. En el caso medido la diferencia entre las dos lecturas era de "
            "$130.093,68 en un solo día:\n"
            + "\n".join(problemas)
            + "\nNo se aplicó nada: la transacción se devolvió completa."
        )
    return len(escrita)


# ---------------------------------------------------------------------------
def _conexion_para_medir():
    """La conexión de la que se puede LEER, o None si no hay ninguna.

    Se pregunta por `op.get_context().as_sql` —el modo offline de alembic, o sea `alembic
    upgrade --sql`— y no por `alembic.context.is_offline_mode()`, por lo mismo que en
    a7f2c5b8e1d4: `as_sql` es un atributo del contexto que existe siempre, y
    `alembic.context` es un proxy que solo está instalado cuando la migración corre dentro
    de `env.py`. Con `op` la migración se puede correr —y probar— con un contexto armado a
    mano, que es lo que hace tests/test_transporte_memoria_del_papel_migracion.py: prueba
    EL upgrade de verdad, el mismo que va a correr en producción.
    """
    return None if op.get_context().as_sql else op.get_bind()


def upgrade() -> None:
    # PRE-VUELO. En modo offline no hay conexión de la que leer, así que no se mide: el
    # script sale con el CREATE TABLE y el INSERT ... SELECT, que es lo que el DBA revisa.
    conexion = _conexion_para_medir()
    antes = medir(conexion) if conexion is not None else None

    op.create_table(
        LA_TABLA,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('liquidacion_id', sa.Uuid(), nullable=False),
        # ANULABLE a propósito: la fila nula es la de la TARIFA GENERAL, la que cobra la
        # recepción que quedó sin ruta. Ver el encabezado.
        sa.Column('ruta_id', sa.Uuid(), nullable=True),
        sa.Column(
            'modo_transporte',
            sa.String(length=10),
            server_default=MODO_POR_LITRO,
            nullable=False,
        ),
        # LAS DOS ANULABLES, y el nulo es información: cada modo usa una sola, y hay
        # papeles de los que no se puede afirmar ninguna (líneas partidas de un flete ya
        # pagado). Un cero ahí querría decir "se emitió a $0,00", que es otra cosa.
        sa.Column('precio_litro', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('valor_dia_fijo', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['liquidacion_id'], ['liquidaciones.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['ruta_id'], ['rutas.id'], ),
        sa.PrimaryKeyConstraint('id'),
        # Una ruta no puede aparecer dos veces en el mismo comprobante: si apareciera con
        # dos modos, no habría manera de saber cómo se cobró. OJO: Postgres deja repetir
        # las filas cuyo `ruta_id` es NULL, así que a la fila de la tarifa general la
        # protege el código que las escribe y no este único. Está explicado en
        # `LiquidacionRuta`.
        sa.UniqueConstraint('liquidacion_id', 'ruta_id', name='uq_liquidacion_ruta'),
    )
    op.create_index(
        op.f('ix_liquidacion_rutas_liquidacion_id'),
        LA_TABLA,
        ['liquidacion_id'],
        unique=False,
    )

    # EL BACKFILL: los comprobantes que ya existen tienen que seguir significando lo mismo.
    #
    # `op.execute` y no `op.get_bind().execute`: en modo offline NO HAY CONEXIÓN y
    # `op.execute` es lo único que sabe escribir la sentencia en el script en vez de
    # ejecutarla.
    op.execute(sentencia_backfill_de_la_memoria())

    if conexion is None:
        return
    # POST-VUELO: ninguna cifra de las que ya existían se movió, y la memoria que quedó
    # escrita dice EXACTAMENTE lo que dicen los renglones de cada comprobante.
    exigir_que_nada_se_movio(antes, medir(conexion))
    exigir_la_memoria_igual_a_los_renglones(conexion)


def downgrade() -> None:
    # Se bota la tabla y no se toca ni una cifra. Lo que se pierde es la memoria, o sea que
    # las dos puertas vuelven a abrirse; ver el encabezado. No hay nada que salvar antes:
    # la memoria se puede volver a deducir de los renglones el día que se vuelva a subir,
    # que es justamente lo que hace el backfill del upgrade.
    op.drop_index(op.f('ix_liquidacion_rutas_liquidacion_id'), table_name=LA_TABLA)
    op.drop_table(LA_TABLA)
