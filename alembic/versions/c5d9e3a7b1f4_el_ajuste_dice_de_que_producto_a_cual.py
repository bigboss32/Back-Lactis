"""el ajuste dice DE QUÉ PRODUCTO A CUÁL, y la compra a quién le llegó lo gratis

Revision ID: c5d9e3a7b1f4
Revises: b1c2d3e4f5a6
Create Date: 2026-08-11 17:10:00.000000

EL DEFECTO, Y ES EL MÁS CARO QUE HA TENIDO ESTE MÓDULO. Dos hechos de plata no
estaban guardados en ninguna parte, así que el código los ADIVINABA leyendo el
catálogo:

  1. `conversiones_borona` decía "30 kg pasaron a borona" sin decir de qué producto
     salieron ni a cuál entraron.
  2. `compras_queso.borona_kilos` decía "llegaron 18,25 kg gratis con este lote" sin
     decir a qué producto le entraban.

Los dos se adivinaban igual: "el PRIMER subproducto que se pesa, en el ORDEN del
catálogo, junto con su padre". Y ese orden es un campo de PRESENTACIÓN que la API
deja cambiar con un PUT.

LO QUE COSTABA, MEDIDO Y REPRODUCIDO: una sola llamada —crear un subproducto del
queso con `orden = 0`, o reordenar la lista— le transfería al producto nuevo TODA la
historia de la borona. La fila de la borona pasaba de 40,40 kg / $498.765,07 /
−$362.407,49 a 30,30 kg / $374.073,80 / −$237.716,22; aparecía una fila con
$498.765,07 de mercancía que nunca se compró; la existencia de borona quedaba en
−30,30 kg mientras el campo viejo decía 50,50 en la MISMA respuesta; y vender 1 kg de
borona rebotaba con un 422. El dueño no había tocado un solo movimiento.

QUÉ HACE ESTA MIGRACIÓN: le agrega a cada hecho la columna que le faltaba, y rellena
lo que ya existe con lo que esas filas SIEMPRE significaron.

  · `conversiones_borona.producto_origen`  ->  'queso'  (en todas las filas)
  · `conversiones_borona.producto_destino` ->  'borona' si destino = 'borona',
                                               NULO     si destino = 'merma'
  · `compras_queso.subproducto_tipo`       ->  'borona' donde borona_kilos > 0

POR QUÉ ESE RELLENO ES EL CORRECTO Y NO UNA SUPOSICIÓN MÁS. Hasta hoy el único
producto del que se podía convertir era el queso y el único que podía recibir era la
borona: no había pantalla, ni endpoint, ni columna para decir otra cosa. Las claves
'queso' y 'borona' son además las que SIEMBRA cada despliegue en TODA empresa (ver
`PRODUCTOS_REVENTA_DEFECTO`), así que valen igual para las dos queseras del cliente.
Estas dos sentencias no cambian lo que las filas significan: lo escriben.

POR QUÉ NO PUEDE MOVER NI UN PESO:

  · NO TOCA NINGUNA COLUMNA DE PLATA NI DE CANTIDAD. `kilos`, `precio_kilo`,
    `valor_total`, `abonado`, `kilos_netos`, `borona_kilos`, `barras` y los abonos se
    quedan byte por byte como estaban. Los tres UPDATE escriben únicamente las
    columnas nuevas, que antes de esta migración no existían.
  · NO RECALCULA NADA. Aunque una fila vieja arrastrara un desfase, aquí no se toca:
    recalcular movería plata de un cliente real, y una migración no arregla historia.
  · EL RELLENO REPRODUCE EXACTAMENTE LO QUE EL CÓDIGO ADIVINABA en la instalación del
    cliente, donde la pareja adivinada era (queso, borona). O sea que el día del
    despliegue las cifras salen idénticas; lo que cambia es que a partir de ahí ya no
    dependen de una lista que alguien puede reordenar.

Y AUN ASÍ SE MIDE, porque "no puede mover nada" es exactamente lo que se creía la vez
pasada. El pre-vuelo cuenta filas y suma kilos y plata de las dos tablas; el
post-vuelo las vuelve a contar y a sumar Y ADEMÁS exige que el relleno haya quedado
completo (tantas filas con origen como filas hay, tantas con destino como ajustes a
borona, tantas compras marcadas como compras con kilos gratis, y la misma suma de
kilos gratis del lado marcado). Si una sola cifra no coincide, la migración REVIENTA
con un mensaje que dice cuál, cuánto valía y cuánto vale, y alembic deshace toda la
transacción. El despliegue se cae antes de dejar la base en un estado que nadie midió.
"""
from decimal import Decimal
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

CENTAVOS = Decimal("0.01")

revision: str = 'c5d9e3a7b1f4'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Las claves de siempre. Van escritas aquí y no importadas de la aplicación porque una
# migración no puede depender del código de hoy: tiene que seguir haciendo lo mismo
# dentro de dos años, aunque las constantes se muevan de archivo.
CLAVE_QUESO = "queso"
CLAVE_BORONA = "borona"
DESTINO_BORONA = "borona"
ANCHO_DE_LA_CLAVE = 80


# ---------------------------------------------------------------- cifras de control
def _cifras_de_control(conexion) -> dict[str, str]:
    """Todo lo que NO puede moverse, en TEXTO.

    En texto a propósito, y con la plata pasada por `Decimal` primero: comparar el
    texto de dos Decimal es más estricto que comparar los números, porque además de la
    cifra compara la escala. Si una suma pasara de '498765.07' a '498765.070' esto lo
    dice, y eso es lo que uno quiere de un pre-vuelo: que chille por lo que no entiende
    en vez de decidir que da igual. El paso por `Decimal` es para que el texto no
    dependa del driver (Postgres devuelve Decimal en un NUMERIC; otros devuelven float,
    y un float imprime 24111767.740000002 donde la plata dice 24111767,74).
    """
    ajustes = conexion.execute(
        sa.text(
            "SELECT COUNT(*), COALESCE(SUM(kilos), 0), "
            "COUNT(CASE WHEN destino = 'borona' THEN 1 END), "
            "COALESCE(SUM(CASE WHEN destino = 'borona' THEN kilos END), 0), "
            "COUNT(CASE WHEN destino <> 'borona' THEN 1 END), "
            "COALESCE(SUM(CASE WHEN destino <> 'borona' THEN kilos END), 0), "
            "COALESCE(SUM(precio_kilo), 0) "
            "FROM conversiones_borona"
        )
    ).one()
    compras = conexion.execute(
        sa.text(
            "SELECT COUNT(*), COALESCE(SUM(borona_kilos), 0), "
            "COUNT(CASE WHEN borona_kilos > 0 THEN 1 END), "
            "COALESCE(SUM(kilos_netos), 0), COALESCE(SUM(valor_total), 0), "
            "COALESCE(SUM(abonado), 0) "
            "FROM compras_queso"
        )
    ).one()

    def plata(valor) -> str:
        return str(Decimal(str(valor)).quantize(CENTAVOS))

    return {
        "ajustes: cantidad de filas": str(int(ajustes[0])),
        "ajustes: suma de kilos": plata(ajustes[1]),
        "ajustes: cuántos van a borona": str(int(ajustes[2])),
        "ajustes: kilos que van a borona": plata(ajustes[3]),
        "ajustes: cuántos son merma": str(int(ajustes[4])),
        "ajustes: kilos de merma": plata(ajustes[5]),
        "ajustes: suma de precio_kilo": plata(ajustes[6]),
        "compras: cantidad de filas": str(int(compras[0])),
        "compras: suma de borona_kilos": plata(compras[1]),
        "compras: cuántas trajeron kilos gratis": str(int(compras[2])),
        "compras: suma de kilos_netos": plata(compras[3]),
        "compras: suma de valor_total (en pesos)": plata(compras[4]),
        "compras: suma de abonado (en pesos)": plata(compras[5]),
    }


def _cifras_del_relleno(conexion) -> dict[str, str]:
    """Cuántas filas quedaron NOMBRANDO sus productos. Solo tiene sentido DESPUÉS."""
    ajustes = conexion.execute(
        sa.text(
            "SELECT COUNT(CASE WHEN producto_origen = :queso THEN 1 END), "
            "COUNT(CASE WHEN producto_destino = :borona THEN 1 END), "
            "COUNT(CASE WHEN producto_destino IS NULL THEN 1 END) "
            "FROM conversiones_borona"
        ),
        {"queso": CLAVE_QUESO, "borona": CLAVE_BORONA},
    ).one()
    compras = conexion.execute(
        sa.text(
            "SELECT COUNT(CASE WHEN subproducto_tipo = :borona THEN 1 END), "
            "COALESCE(SUM(CASE WHEN subproducto_tipo = :borona "
            "THEN borona_kilos END), 0) "
            "FROM compras_queso"
        ),
        {"borona": CLAVE_BORONA},
    ).one()
    return {
        "ajustes con origen 'queso'": str(int(ajustes[0])),
        "ajustes con destino 'borona'": str(int(ajustes[1])),
        "ajustes sin destino (merma)": str(int(ajustes[2])),
        "compras marcadas con destinatario 'borona'": str(int(compras[0])),
        "kilos gratis con destinatario": str(
            Decimal(str(compras[1])).quantize(CENTAVOS)
        ),
    }


def _exigir_que_no_se_movio(antes: dict[str, str], despues: dict[str, str]) -> None:
    """REVIENTA si una sola cifra se movió, y dice cuál.

    El mensaje está escrito para que se entienda leyéndolo una vez y sin abrir el
    código: qué cifra, cuánto valía y cuánto vale. Es lo que va a quedar en el log del
    despliegue, y es lo único que alguien va a tener para decidir si hay que restaurar.
    """
    problemas = [
        f"  · {nombre}: pasó de {viejo} a {despues.get(nombre, '<falta>')}"
        for nombre, viejo in antes.items()
        if despues.get(nombre) != viejo
    ]
    if problemas:
        raise RuntimeError(
            "MIGRACIÓN ABORTADA: agregarle a los ajustes y a las compras la columna "
            "que dice de qué producto hablan movió datos, y no puede moverlos. Esta "
            "migración solo escribe columnas NUEVAS; ninguna cantidad y ningún peso "
            "debería cambiar. Lo que se movió:\n"
            + "\n".join(problemas)
            + "\n\nNo se aplicó nada más: alembic deshace toda la transacción. Revise "
            "la base ANTES de volver a desplegar."
        )


def _exigir_que_el_relleno_quedo_completo(
    control: dict[str, str], relleno: dict[str, str]
) -> None:
    """REVIENTA si quedó una fila sin decir de qué producto habla.

    Es el chequeo que de verdad importa, y va aparte del de arriba: que no se haya
    movido nada no sirve de nada si además no se escribió nada. Una fila de ajuste sin
    origen o una compra con kilos gratis sin destinatario es exactamente el hueco que
    esta migración viene a cerrar, y dejarla pasar sería volver a adivinar mañana.
    """
    esperado = {
        "ajustes con origen 'queso'": control["ajustes: cantidad de filas"],
        "ajustes con destino 'borona'": control["ajustes: cuántos van a borona"],
        "ajustes sin destino (merma)": control["ajustes: cuántos son merma"],
        "compras marcadas con destinatario 'borona'": control[
            "compras: cuántas trajeron kilos gratis"
        ],
        "kilos gratis con destinatario": control["compras: suma de borona_kilos"],
    }
    problemas = [
        f"  · {nombre}: quedaron {relleno.get(nombre, '<falta>')} y tenían que ser "
        f"{valor}"
        for nombre, valor in esperado.items()
        if relleno.get(nombre) != valor
    ]
    if problemas:
        raise RuntimeError(
            "MIGRACIÓN ABORTADA: el relleno quedó incompleto. Alguna fila se quedó sin "
            "decir de qué producto habla, y esas son justamente las que el sistema "
            "tendría que volver a adivinar. Lo que no cuadró:\n"
            + "\n".join(problemas)
            + "\n\nNo se aplicó nada más: alembic deshace toda la transacción."
        )


def rellenar(conexion) -> None:
    """Los tres UPDATE, y NADA más. Separado para poder probarlo (ver
    tests/test_reventa_ajustes_migracion.py): es código que corre UNA vez sobre datos
    que nadie va a poder volver a ver como estaban."""
    conexion.execute(
        sa.text("UPDATE conversiones_borona SET producto_origen = :queso"),
        {"queso": CLAVE_QUESO},
    )
    conexion.execute(
        sa.text(
            "UPDATE conversiones_borona SET producto_destino = :borona "
            "WHERE destino = :destino_borona"
        ),
        {"borona": CLAVE_BORONA, "destino_borona": DESTINO_BORONA},
    )
    conexion.execute(
        sa.text(
            "UPDATE compras_queso SET subproducto_tipo = :borona "
            "WHERE borona_kilos > 0"
        ),
        {"borona": CLAVE_BORONA},
    )


def upgrade() -> None:
    conexion = op.get_bind()
    # PRE-VUELO. En SQLite (las pruebas) las tablas se crean con `create_all` y esto
    # cuenta cero filas; en Postgres cuenta la historia del cliente.
    antes = _cifras_de_control(conexion)

    op.add_column(
        "conversiones_borona",
        sa.Column(
            "producto_origen",
            sa.String(length=ANCHO_DE_LA_CLAVE),
            nullable=False,
            server_default=CLAVE_QUESO,
        ),
    )
    op.add_column(
        "conversiones_borona",
        sa.Column(
            "producto_destino", sa.String(length=ANCHO_DE_LA_CLAVE), nullable=True
        ),
    )
    op.create_index(
        "ix_conversiones_borona_producto_origen",
        "conversiones_borona",
        ["producto_origen"],
    )
    op.add_column(
        "compras_queso",
        sa.Column(
            "subproducto_tipo", sa.String(length=ANCHO_DE_LA_CLAVE), nullable=True
        ),
    )

    rellenar(conexion)

    # POST-VUELO: las mismas cifras (no se movió nada) Y el relleno completo.
    _exigir_que_no_se_movio(antes, _cifras_de_control(conexion))
    _exigir_que_el_relleno_quedo_completo(antes, _cifras_del_relleno(conexion))


def downgrade() -> None:
    """Quita las tres columnas.

    Bajar esta migración devuelve el sistema a adivinar con el orden del catálogo, con
    todo lo que eso costaba. No hay nada que salvar de las columnas: su contenido se
    puede volver a derivar con el mismo relleno de arriba.
    """
    op.drop_column("compras_queso", "subproducto_tipo")
    op.drop_index(
        "ix_conversiones_borona_producto_origen", table_name="conversiones_borona"
    )
    op.drop_column("conversiones_borona", "producto_destino")
    op.drop_column("conversiones_borona", "producto_origen")
