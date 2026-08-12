"""la clave del producto tiene que CABER en la columna donde se guarda

Revision ID: b1c2d3e4f5a6
Revises: a4b5c6d7e8f9
Create Date: 2026-08-11 15:00:00.000000

EL DEFECTO, Y ES DE LOS QUE SOLO SE VEN EN PRODUCCIÓN. La clave de un producto del
catálogo (`productos_reventa.clave`) es varchar(80), y es la MISMA cadena que se guarda
en `compras_queso.tipo` y `ventas_queso.tipo`, que son varchar(20).

O sea que el dueño puede crear un producto cuyo nombre genere una clave de más de 20
caracteres —"Queso costeño artesanal" da 'queso_costeno_artesanal', 23— y esa clave NO
CABE en la columna donde hay que guardarla. En SQLite, que es donde corren las pruebas,
el ancho no se valida y todo pasa; en POSTGRES, que es la base del cliente, el INSERT se
cae con 'value too long for type character varying(20)' y el dueño ve un 500 al registrar
la compra. Un producto que se puede crear pero no se puede comprar ni vender.

QUÉ HACE ESTA MIGRACIÓN: ensanchar las dos columnas de varchar(20) a varchar(80), que es
el ancho de la clave. Nada más.

POR QUÉ NO PUEDE MOVER NI UN PESO, que es lo único que hay que poder afirmar sobre la base
de un cliente real:

  · ES UN ENSANCHE, no una conversión. Un varchar(20) cabe entero en un varchar(80): no
    hay truncamiento posible, no hay valor que se pueda reinterpretar, y Postgres no
    reescribe la tabla para agrandar el límite de un varchar (desde 9.2). Ninguna fila se
    lee, ninguna se escribe.
  · NO TOCA NINGUNA COLUMNA DE PLATA. `valor_total`, `abonado`, `kilos_netos`, `barras`,
    `precio_kilo`, `precio_barra`, `gasto_monto` y los abonos se quedan byte por byte como
    estaban. Esta migración no tiene ni un UPDATE.
  · NO RECALCULA NADA. Aunque una fila vieja arrastrara un centavo de desfase entre su
    cantidad y su plata, aquí no se toca: recalcular movería plata de un cliente real, y
    una migración no arregla la historia.

Y AUN ASÍ SE MIDE, porque "no puede mover nada" es exactamente lo que se creía la vez
pasada. El pre-vuelo cuenta las filas y suma `valor_total` y `abonado` de las dos tablas;
el post-vuelo las vuelve a contar y a sumar, y si una sola cifra no coincide la migración
REVIENTA con un mensaje que dice cuál fue, en pesos, antes y después. Con eso el
despliegue se cae antes de dejar la base en un estado que nadie midió, en vez de terminar
en verde y que el dueño encuentre la diferencia con la calculadora tres semanas después.
"""
from decimal import Decimal
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

CENTAVOS = Decimal("0.01")

revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Las dos tablas de renglones y el ancho que tiene que tener su columna `tipo`: el de
# `productos_reventa.clave`, porque es la misma cadena.
TABLAS = ("compras_queso", "ventas_queso")
ANCHO_DE_LA_CLAVE = 80
ANCHO_ANTERIOR = 20


def _cifras_de_control(conexion) -> dict[str, tuple[str, str, str]]:
    """(cuántas filas, suma de valor_total, suma de abonado) de cada tabla, en TEXTO.

    En texto a propósito, y con la plata pasada por `Decimal` primero: comparar el texto
    de dos Decimal es más estricto que comparar los números, porque además de la cifra
    compara la escala. Si una suma pasara de '11733150.00' a '11733150.0' esto lo dice,
    y eso es lo que uno quiere de un pre-vuelo: que chille por lo que no entiende en vez
    de decidir que da igual. El paso por `Decimal` es para que el texto no dependa del
    driver (Postgres devuelve Decimal en un NUMERIC; otros devuelven float, y un float
    imprime 24111767.740000002 donde la plata dice 24111767.74).
    """
    cifras: dict[str, tuple[str, str, str]] = {}
    for tabla in TABLAS:
        fila = conexion.execute(
            sa.text(
                f"SELECT COUNT(*), "
                f"COALESCE(SUM(valor_total), 0), "
                f"COALESCE(SUM(abonado), 0) "
                f"FROM {tabla}"
            )
        ).one()
        cifras[tabla] = (
            str(int(fila[0])),
            str(Decimal(str(fila[1])).quantize(CENTAVOS)),
            str(Decimal(str(fila[2])).quantize(CENTAVOS)),
        )
    return cifras


# Cómo se llama cada cifra de control en el mensaje de error. Van con el nombre completo
# porque ese texto es lo único que va a tener quien lea el log del despliegue.
NOMBRES_DE_LAS_CIFRAS = (
    "cantidad de filas",
    "suma de valor_total (en pesos)",
    "suma de abonado (en pesos)",
)


def _exigir_que_cuadre(antes: dict, despues: dict) -> None:
    """REVIENTA si una sola cifra se movió, y dice cuál.

    El mensaje está escrito para que se entienda leyéndolo una vez y sin abrir el código:
    qué tabla, qué cifra, cuánto valía y cuánto vale. Es lo que va a quedar en el log del
    despliegue, y es lo único que alguien va a tener para decidir si hay que restaurar.
    """
    problemas = []
    for tabla in TABLAS:
        for nombre, viejo, nuevo in zip(
            NOMBRES_DE_LAS_CIFRAS, antes[tabla], despues[tabla]
        ):
            if viejo != nuevo:
                problemas.append(
                    f"  · {tabla}: la {nombre} pasó de {viejo} a {nuevo}"
                )
    if problemas:
        raise RuntimeError(
            "MIGRACIÓN ABORTADA: ensanchar la columna `tipo` movió datos, y no puede "
            "moverlos. Esta migración solo agranda el límite de un varchar; ninguna "
            "fila y ningún peso debería cambiar. Lo que se movió:\n"
            + "\n".join(problemas)
            + "\n\nNO se aplicó nada más. Revise la base ANTES de volver a desplegar."
        )


def upgrade() -> None:
    conexion = op.get_bind()
    # PRE-VUELO. En SQLite (las pruebas) las tablas se crean con `create_all` y esto
    # simplemente cuenta cero filas; en Postgres cuenta la historia del cliente.
    antes = _cifras_de_control(conexion)

    for tabla in TABLAS:
        op.alter_column(
            tabla,
            "tipo",
            existing_type=sa.String(length=ANCHO_ANTERIOR),
            type_=sa.String(length=ANCHO_DE_LA_CLAVE),
            # `existing_nullable` y `existing_server_default` van explícitos porque
            # `alter_column` los REESCRIBE si no se los dan: sin esto, ensanchar la
            # columna le quitaría el default 'queso' y su NOT NULL, y la próxima fila
            # que entrara sin tipo quedaría en nulo.
            existing_nullable=True,
            existing_server_default="queso",
        )

    # POST-VUELO: las mismas tres cifras por tabla, y si una se movió, revienta.
    _exigir_que_cuadre(antes, _cifras_de_control(conexion))


def downgrade() -> None:
    """Vuelve las columnas a varchar(20).

    OJO, Y NO ES TEÓRICO: si mientras la columna estuvo ancha se registró un movimiento
    de un producto con clave de más de 20 caracteres, este downgrade NO PUEDE correr —
    Postgres se niega a truncar— y va a fallar. Eso es lo correcto: bajar la migración no
    puede llevarse por delante el tipo de una compra que ya está registrada, porque esa
    fila quedaría hablando de un producto que no es. Si hay que bajar, primero hay que
    decidir a mano qué se hace con esos movimientos.
    """
    for tabla in TABLAS:
        op.alter_column(
            tabla,
            "tipo",
            existing_type=sa.String(length=ANCHO_DE_LA_CLAVE),
            type_=sa.String(length=ANCHO_ANTERIOR),
            existing_nullable=True,
            existing_server_default="queso",
        )
