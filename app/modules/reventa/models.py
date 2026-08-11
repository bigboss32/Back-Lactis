"""Compra y venta de queso (reventa): negocio paralelo a la producción propia.

Se compra queso a productores (con merma y borona), se les abona por partes,
y se revende a un precio mayor. Esta contabilidad es INDEPENDIENTE del libro
de la quesera: contabilidad/estado de resultados no leen estas tablas.
"""
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import expression

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

# ------------------------------------------------- la hora que ordena los hechos
_UN_MICROSEGUNDO = timedelta(microseconds=1)
_candado_de_la_hora = threading.Lock()
_ultima_hora_entregada: datetime | None = None


def _hora_de_registro() -> datetime:
    """La hora de registro de una fila: UTC, con microsegundos y ESTRICTAMENTE
    CRECIENTE dentro del proceso.

    Lo de "estrictamente creciente" es la parte que importa y no es paranoia: el
    reloj de pared NO avanza en cada llamada. En Windows (donde se corren las
    pruebas) `datetime.now()` se mueve cada uno o dos milisegundos, así que cinco
    llamadas seguidas devuelven la MISMA hora; en Linux es más fino pero tampoco
    hay garantía. Y aquí la hora ES la llave de orden del reparto FIFO: si dos
    filas distintas empataran, el desempate volvería a caer en criterios que no
    son "cuál se registró primero" (ver `HoraDeRegistroMixin`). Cuando el reloj no
    alcanza a moverse se avanza UN MICROSEGUNDO sobre la última hora entregada:
    la hora sigue siendo la real con un error de microsegundos —que a ninguna
    cifra del negocio le cambia nada, porque los informes van por fecha— y en
    cambio el orden de registro queda siendo un hecho.

    El candado es porque las peticiones sincrónicas de FastAPI corren en un pool
    de hilos: dos a la vez podrían leer y escribir `_ultima_hora_entregada`
    entrelazadas y entregar la misma hora, que es justo lo que esto evita.

    Ojo con lo que NO garantiza: si producción corre con varios procesos, cada uno
    lleva su propia última hora, así que dos filas de procesos distintos escritas
    en el mismo microsegundo todavía podrían empatar. Para eso está el resto de la
    llave de orden del repositorio, que desempata con datos del negocio y no con
    el `id`.
    """
    global _ultima_hora_entregada
    with _candado_de_la_hora:
        ahora = datetime.now(timezone.utc)
        if _ultima_hora_entregada is not None and ahora <= _ultima_hora_entregada:
            ahora = _ultima_hora_entregada + _UN_MICROSEGUNDO
        _ultima_hora_entregada = ahora
        return ahora


class HoraDeRegistroMixin:
    """`created_at` lo escribe LA APLICACIÓN, no el reloj de la base.

    POR QUÉ ESTO NO ES UN DETALLE TÉCNICO. En las tres tablas que usan este mixin
    —compras, ventas y ajustes— el ORDEN de las filas del mismo día decide PLATA:
    el reparto FIFO consume el inventario en ese orden, y de ahí sale a qué
    productor se le carga el costo de una venta y cuánta ganancia le queda (ver
    `app/modules/reventa/lotes.py` y la llave de orden de
    `app/modules/reventa/repository.py`). "Se vende primero lo que se compró
    primero" necesita, entonces, poder decir cuál se registró primero.

    El `server_default=func.now()` que traía el AuditMixin NO alcanza para eso, y
    por dos razones distintas en cada motor:

    - En SQLite —donde corren las pruebas— CURRENT_TIMESTAMP tiene resolución de UN
      SEGUNDO. Dos compras registradas seguidas caen en el mismo segundo y empatan.
    - En Postgres —que es producción— `now()` es la hora de la TRANSACCIÓN, así que
      todas las filas escritas en la misma petición traen el mismo instante.

    Empatar ahí obligaba a desempatar más abajo, y el último criterio era el `id`,
    que es un UUID ALEATORIO: a cuál productor se le consumían los kilos primero lo
    decidía la suerte, y una misma base podía dar dos respuestas distintas. Con la
    hora puesta desde la aplicación, cada fila tiene su propio instante con
    microsegundos, el orden de registro es un hecho y NO una suposición, y da lo
    mismo en los dos motores.

    NO CAMBIA EL ESQUEMA: la columna es idéntica (mismo tipo, mismo
    `server_default`, misma restricción). Lo único que cambia es que el valor viaja
    en el INSERT en vez de dejárselo a la base, así que no hay migración que correr
    y las filas que ya existen se siguen leyendo igual. El `server_default` se
    conserva a propósito: es la red por si algún día una fila entra por SQL crudo.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_hora_de_registro,
        nullable=False,
    )


# Estado de pago (columna estado del AuditMixin)
ESTADO_PENDIENTE = "pendiente"
ESTADO_PARCIAL = "parcial"
ESTADO_PAGADA = "pagada"
ESTADO_ANULADA = "anulada"

# ------------------------------------------------------ qué es un documento
# Una factura de reventa: se le compró a un productor o se le vendió a un cliente.
TIPO_DOC_COMPRA = "compra"
TIPO_DOC_VENTA = "venta"

# ------------------------------------------------- qué se comercia y en qué unidad
# El queso y la borona se pesan (kilos). La mozzarella se cuenta: entra como BARRA
# y sale como BARRA, y el peso de la barra no hace falta para ninguna cuenta.
TIPO_QUESO = "queso"
TIPO_BORONA = "borona"
TIPO_MOZZARELLA = "mozzarella"

UNIDAD_KILO = "kg"
UNIDAD_BARRA = "barra"


def unidad_de(fila: Any) -> str:
    """La unidad en la que se mide esta fila.
    A partir de Lote 2, se deduce de qué columna tiene datos en vez del nombre del tipo.
    """
    if getattr(fila, "tipo", "") == "mozzarella":
        return UNIDAD_BARRA
    if getattr(fila, "kilos", 0) > 0 or getattr(fila, "kilos_brutos", 0) > 0:
        return UNIDAD_KILO
    if getattr(fila, "barras", 0) > 0:
        return UNIDAD_UNIDAD
    # Fallback
    return UNIDAD_KILO


# ---------------------------------------------------- el catálogo de productos
# La unidad de lo que SE CUENTA por piezas completas. El valor es "unidad" —el
# mismo que ya usa `inventario.Producto`— y no "barra" a propósito: "barra" es
# como se le dice a la pieza de LA MOZZARELLA (es el rótulo que devuelve
# `unidad_de` para sus renglones), no la clase de medida. Si algún día entra algo
# que se venda por bolsa o por canasta, sigue siendo "se cuenta"; lo que cambia
# es el rótulo, y el rótulo es el nombre del producto.
UNIDAD_UNIDAD = "unidad"


class ProductoReventa(TenantMixin, AuditMixin, Base):
    """QUÉ SE COMERCIA EN REVENTA, como DATO y no como una lista en el código.

    Hoy el módulo maneja tres productos —queso, borona y mozzarella— escritos a
    mano en las constantes de arriba, y cada fila de compra o de venta lleva el
    suyo pegado en su columna `tipo`. El dueño pidió poder "comprar y vender algo
    que quiera el cliente": esta tabla es ese "algo".

    LA CLAVE ES EL PUENTE, y es lo que hace que esto no cueste una migración de
    datos. `clave` es la MISMA cadena que ya vive en `compras_queso.tipo` y en
    `ventas_queso.tipo`: los tres productos que se siembran llevan las claves
    'queso', 'borona' y 'mozzarella', que son exactamente los valores que las
    filas del cliente ya tienen guardados. Así, el día que las compras y las
    ventas empiecen a mirar el catálogo, cada fila que ya existe encuentra su
    producto sin que haya que tocar ni una columna. Y por eso la clave NO CAMBIA
    NUNCA, ni cuando se renombra el producto: es la identidad, no el rótulo.

    EN ESTE LOTE NINGUNA CUENTA DE PLATA MIRA ESTA TABLA: ni las compras, ni las
    ventas, ni el resumen, ni las temporadas, ni los lotes, ni el FIFO. Es un
    catálogo y nada más. De ahí sale la única afirmación que importa sobre la base
    de un cliente real: no puede mover una cifra, porque no hay ninguna consulta
    de plata que lo lea.

    TRES DECISIONES QUE PARECEN DETALLE Y NO LO SON
    ----------------------------------------------

    1) `decimales` EN VEZ DE DOS JUEGOS DE COLUMNAS. Lo que de verdad distingue
       hoy un kilo de una barra son los decimales: los kilos viven en
       Numeric(12, 2) y las barras en Numeric(12, 0) más el validador que rechaza
       "8,5 barras". Todo lo demás —tener columnas de cantidad y de precio
       separadas para cada unidad— es la consecuencia de no haber tenido dónde
       guardar ese dato. Aquí es UNA COLUMNA, UN HECHO: cuántos decimales admite
       la cantidad de este producto. Un producto nuevo no obliga a agregarle dos
       columnas más a las tablas de renglones.

    2) `subproducto_de_id` ES LO QUE DEJA DE HACER DE LA BORONA UN CASO ESPECIAL.
       El motor FIFO de `lotes.py` YA implementa esta relación completa: la cola
       de borona con costo CERO cuando llega junto con la compra (no se paga), y
       con el costo HEREDADO del queso cuando sale de desmenuzarlo (si no, pasar
       queso a borona haría desaparecer plata). Eso hoy está cableado con el
       nombre 'borona' adentro del código. Con esta columna pasa a ser un dato del
       catálogo, y la relación queda dicha donde se puede leer.

       Es de UN SOLO NIVEL a propósito (lo valida el servicio): el subproducto de
       un subproducto no existe en el motor de reparto, y admitir la cadena sería
       ofrecer algo que el costeo no sabe calcular.

    3) `admite_ajustes` SE DEDUCE DE LA UNIDAD, NO SE PREGUNTA. Lo que se pesa
       admite merma: se compran 800 kg y al venderlos la báscula marca menos,
       porque el queso se seca. Una barra no pierde peso, porque no se está
       pesando: entra como barra y sale como barra. Preguntárselo al usuario
       sería dejarle marcar "la mozzarella pierde peso", que es una casilla que
       solo puede producir un ajuste que no significa nada. Es el mismo argumento
       que ya está escrito en `ConversionBorona`.

    Y COMO SE DEDUCE, NO PUEDE HABER DOS FUENTES QUE SE CONTRADIGAN: el servicio
    calcula `decimales` y `admite_ajustes` a partir de `unidad`, y el CHECK de la
    tabla exige que concuerden. La garantía la da la base y no la disciplina de
    quien escriba el próximo INSERT, igual que con el CHECK de kilos y barras de
    `compras_queso`: lo que tiene que ser verdad siempre, se exige en la tabla.
    """

    __tablename__ = "productos_reventa"
    __table_args__ = (
        # LA CLAVE ES ÚNICA POR EMPRESA, y esto es lo que hace que la siembra se
        # pueda correr en cada despliegue sin duplicar nada. Es por (empresa_id,
        # clave) y no global porque las dos queseras son negocios distintos: cada
        # una tiene su 'queso'.
        #
        # OJO, NO FILTRA `deleted_at`: una fila borrada en suave SIGUE ocupando su
        # clave. Es la misma situación de `roles.nombre` y se trata igual (ver
        # `ProductoReventaService.crear`): la siembra no la resucita, pero si el
        # dueño vuelve a agregar ese producto a mano, se le devuelve la MISMA fila
        # con su mismo id y su misma clave, que es lo que deja que los movimientos
        # viejos sigan cuadrando con él.
        UniqueConstraint("empresa_id", "clave", name="uq_productos_reventa_empresa_clave"),
        CheckConstraint(
            f"unidad IN ('{UNIDAD_KILO}', '{UNIDAD_UNIDAD}')",
            name="ck_productos_reventa_unidad",
        ),
        # El techo es el de las columnas de cantidad de los renglones
        # (Numeric(12, 2)): más de dos decimales no se podrían guardar, y ya nos
        # costó caro una vez que un tercer decimal se redondeara en silencio.
        CheckConstraint(
            "decimales >= 0 AND decimales <= 2", name="ck_productos_reventa_decimales"
        ),
        # LA COHERENCIA DE LA UNIDAD, exigida por la base. Lo que se cuenta va en
        # piezas enteras (decimales 0) y no admite merma; lo que se pesa sí la
        # admite. Escrito con booleanos pelados (`admite_ajustes` / `NOT
        # admite_ajustes`) porque es lo único que significa lo mismo en Postgres
        # —que es producción— y en SQLite —que es donde corren las pruebas—.
        CheckConstraint(
            f"(unidad = '{UNIDAD_KILO}' AND admite_ajustes) "
            f"OR (unidad = '{UNIDAD_UNIDAD}' AND decimales = 0 AND NOT admite_ajustes)",
            name="ck_productos_reventa_unidad_coherente",
        ),
        # Nada es subproducto de sí mismo. Sin esto, una fila apuntándose a sí
        # misma dejaría al reparto de costos girando sobre el mismo producto.
        CheckConstraint(
            "subproducto_de_id IS NULL OR subproducto_de_id <> id",
            name="ck_productos_reventa_subproducto_no_es_si_mismo",
        ),
    )

    # Cómo lo llama el dueño. SE PUEDE RENOMBRAR SIEMPRE y sin riesgo, porque no
    # es la identidad de nada: ni el id ni la clave se mueven, así que ninguna
    # fila de compra ni de venta se entera. Importa porque el dueño va a querer
    # que "Queso" diga "Queso costeño".
    nombre: Mapped[str] = mapped_column(String(80), nullable=False)
    # La identidad. Se calcula del nombre la PRIMERA vez y no vuelve a cambiar
    # (ver el docstring: es la misma cadena que `compras_queso.tipo`).
    clave: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    # 'kg' (se pesa) o 'unidad' (se cuenta).
    unidad: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UNIDAD_KILO, server_default=UNIDAD_KILO
    )
    # Cuántos decimales admite la cantidad: 2 si se pesa, 0 si se cuenta. Lo
    # deriva el servicio de `unidad` y lo amarra el CHECK.
    decimales: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    # De qué producto es subproducto (la borona lo es del queso). ON DELETE SET
    # NULL: si el padre desapareciera, el subproducto se queda —es un producto
    # completo que se vende— y lo que se pierde es la relación, no la fila.
    subproducto_de_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("productos_reventa.id", ondelete="SET NULL"), index=True, default=None
    )
    # Si su cantidad puede corregirse con un ajuste (merma o paso a borona).
    # Deducido de `unidad`, ver el docstring.
    admite_ajustes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=expression.true()
    )
    # En qué orden se le muestran al dueño. Es SOLO presentación: el orden de una
    # lista de selección, no una prioridad de negocio y no un dato de ninguna
    # cuenta.
    orden: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # El producto del que este es subproducto. `remote_side` es lo que le dice al
    # ORM cuál de las dos puntas de la llave es el padre en una tabla que se
    # apunta a sí misma.
    subproducto_de: Mapped["ProductoReventa | None"] = relationship(
        "ProductoReventa", remote_side="ProductoReventa.id", lazy="joined"
    )

    @property
    def subproducto_de_nombre(self) -> str | None:
        """El nombre del padre, para que la pantalla lo muestre sin tener que
        cruzar la lista contra sí misma."""
        return self.subproducto_de.nombre if self.subproducto_de else None

    @property
    def se_pesa(self) -> bool:
        """Si se mide en kilos. Es la misma pregunta que `admite_ajustes` y que
        `decimales > 0`, y las tres tienen que dar lo mismo: el CHECK de la tabla
        lo exige. Existe con nombre propio porque es así como se lee en el
        negocio: "esto se pesa" o "esto se cuenta"."""
        return self.unidad == UNIDAD_KILO


def derivados_de_unidad(unidad: str) -> tuple[int, bool]:
    """(decimales, admite_ajustes) que le corresponden a una unidad.

    UNA SOLA IMPLEMENTACIÓN de la deducción, y vive acá —al lado del CHECK que la
    exige— porque tiene DOS clientes: el servicio, cuando el dueño agrega un
    producto, y la siembra de cada despliegue. Si cada uno la escribiera por su
    cuenta, un día la siembra dejaría filas que el servicio no habría aceptado.

    Lo que se pesa lleva dos decimales y admite merma; lo que se cuenta va entero y
    no la admite. El porqué está en el docstring de `ProductoReventa`.
    """
    se_pesa = unidad == UNIDAD_KILO
    return (2 if se_pesa else 0), se_pesa


# Los productos con los que arranca TODA empresa: son exactamente los tres que el
# módulo ya maneja hoy, con las claves que ya están guardadas en las filas del
# cliente ('queso', 'borona', 'mozzarella').
#
#     (clave, nombre, unidad, clave del producto del que es subproducto)
#
# NI `decimales` NI `admite_ajustes` VAN EN LA TUPLA, aunque el queso lleve dos
# decimales y la mozzarella cero: los deduce `derivados_de_unidad` de la unidad. Una
# lista que los repitiera sería un segundo sitio donde se pueden desordenar, y es
# exactamente el defecto que este diseño evita. El `orden` tampoco va: es la
# posición en esta misma lista.
#
# La usan la siembra de cada despliegue (`app/seeds/seed.py`) y la migración
# (`alembic/versions/*_catalogo_de_productos_de_reventa.py`, que no puede importar de
# la aplicación y la lleva copiada). Una prueba exige que las dos digan lo mismo.
PRODUCTOS_REVENTA_DEFECTO: tuple[tuple[str, str, str, str | None], ...] = (
    (TIPO_QUESO, "Queso", UNIDAD_KILO, None),
    (TIPO_BORONA, "Borona", UNIDAD_KILO, TIPO_QUESO),
    (TIPO_MOZZARELLA, "Mozzarella", UNIDAD_UNIDAD, None),
)


class DocumentoReventa(TenantMixin, AuditMixin, Base):
    """LA FACTURA de reventa: una compra o una venta con VARIOS productos.

    POR QUÉ ESTA TABLA NO TIENE NI UNA COLUMNA DE PLATA, que es LA decisión de
    todo este trabajo. La fila de `compras_queso` / `ventas_queso` que ya existía
    ES un renglón de factura: un producto, su cantidad, su precio, su plata y sus
    abonos. Lo único que faltaba era una cabecera ENCIMA que dijera "estos tres
    renglones son la misma venta del mismo día al mismo cliente". Así que la
    cabecera es SOLO eso: quién, cuándo y una nota.

    El total del documento es la SUMA de sus renglones, calculada AL LEER, nunca
    guardada. Es el mismo criterio que ya está escrito y defendido en `Temporada`:
    dos fuentes para el mismo hecho terminan contradiciéndose, y el día que se
    contradigan el dueño —que suma la columna a mano— pierde la confianza en todo
    el tablero. Si el total viviera aquí, editarle el precio a un renglón dejaría
    esta cifra vieja; peor todavía, `valor_total` y `abonado` viven en los
    renglones porque los abonos viven en los renglones.

    Y DE AHÍ SALE LO IMPORTANTE: el FIFO por lotes, el resumen, las temporadas, la
    cartera, los abonos, los adjuntos y los PDF NO SE TOCARON. Todos siguen
    leyendo filas de un producto cada una, que es lo que siempre leyeron; el
    `documento_id` les es invisible. Una factura de tres renglones da EXACTAMENTE
    las mismas cifras que esos tres productos vendidos por separado (lo fija
    tests/test_reventa_documentos_neutralidad.py).

    LA COLUMNA `estado` (que viene del AuditMixin) NO ES EL ESTADO DE PAGO. El
    estado de pago del documento es DERIVADO: se deduce del estado de sus
    renglones al leer. Aquí `estado` es lo que es en todas las tablas del
    proyecto: el ciclo de vida de la fila ('activo' / 'inactivo' al borrarse).
    """

    __tablename__ = "documentos_reventa"
    __table_args__ = (
        # Solo hay dos clases de factura, y la base lo exige en vez de confiarlo
        # al esquema de entrada: `tipo` decide en CUÁL de las dos tablas de
        # renglones hay que buscar, así que un tercer valor dejaría un documento
        # que ninguna lectura sabría abrir.
        CheckConstraint(
            "tipo IN ('compra', 'venta')", name="ck_documentos_reventa_tipo"
        ),
    )

    tipo: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # El productor (si es compra) o el cliente (si es venta). Se llama `tercero`
    # como en `SaldoAnterior`, y por lo mismo: es el mismo hecho de las dos
    # tablas, con la misma canonización de nombres.
    #
    # OJO, EL NOMBRE Y LA FECHA SE COPIAN AL RENGLÓN. No es redundancia por
    # descuido: el resumen, la cartera y el estado de cuenta agrupan por
    # `ventas_queso.cliente` y filtran por `ventas_queso.fecha`, y tenían que
    # seguir haciéndolo sin aprender de documentos. El servicio propaga los dos
    # campos de la cabecera a TODOS sus renglones en cada escritura, así que no
    # pueden quedar diciendo cosas distintas.
    tercero: Mapped[str] = mapped_column(String(150), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(500))


class CompraQueso(HoraDeRegistroMixin, TenantMixin, AuditMixin, Base):
    # La hora de registro con microsegundos (ver `HoraDeRegistroMixin`): en esta
    # tabla el orden del mismo día decide a quién se le consumen los kilos.
    __tablename__ = "compras_queso"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    productor: Mapped[str] = mapped_column(String(150), nullable=False)
    # ------------------------------------------------ a qué factura pertenece
    # ANULABLE, y con ON DELETE SET NULL, por la misma razón: esta fila se
    # sostiene sola. Fue una compra completa durante meses y sigue siendo la
    # unidad que lee el FIFO, el resumen y la cartera. El documento es una
    # cabecera que la AGRUPA, no su dueño: si algún día se borrara una cabecera
    # sin pasar por el servicio, lo que NO puede pasar es que se lleve la plata
    # por delante con un CASCADE. Queda el renglón, suelto, contando lo mismo
    # que contaba antes de que existieran los documentos.
    #
    # En nulo significa exactamente eso: una compra de un solo producto de las
    # de antes (la migración le puso su propia cabecera a cada una, así que en
    # la práctica solo quedan en nulo las filas ya borradas en suave).
    documento_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documentos_reventa.id", ondelete="SET NULL"), index=True, default=None
    )
    # En qué lugar de la factura va este renglón (0, 1, 2...). NO es decoración:
    # el abono al documento se DERRAMA sobre los renglones EN ESTE ORDEN, así que
    # es el orden en el que el dueño ve caer su plata, y es el orden en el que se
    # toman los candados FOR UPDATE (ver `_bloquear_renglones`: sin un orden fijo,
    # dos abonos simultáneos a la misma factura se abrazan en un deadlock).
    orden: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # Qué se le compró: queso (se pesa) o mozzarella (se cuenta por barras).
    # server_default 'queso' a propósito: TODAS las filas que ya existen son de
    # kilos, y así quedan marcadas como tal y no en un estado ambiguo.
    tipo: Mapped[str] = mapped_column(
        String(20), default=TIPO_QUESO, server_default=TIPO_QUESO, index=True
    )
    kilos_brutos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Merma en la compra quedó obsoleta: al comprar se paga por todo lo que se
    # recibe. La merma real se ve al vender (se pesa menos). Se conserva la
    # columna (siempre 0) por compatibilidad con datos históricos.
    merma_kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # Borona que llega con el lote: no se paga, pero entra al inventario
    # de borona para venderse como subproducto
    borona_kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # Kilos por los que se paga (= kilos_brutos ahora que no hay merma)
    kilos_netos: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    precio_kilo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # ------------------------------------------------------------- mozzarella
    # Barras compradas y lo que costó CADA BARRA. Escala 0 (sin decimales) a
    # propósito: una barra es una barra, no hay media barra, y una columna que
    # admitiera decimales dejaría entrar "8,5 barras" —que no existe— o
    # redondearía en silencio como ya pasó con los kilos de tres decimales.
    # En las compras de kilos van en CERO, y el CHECK de arriba lo exige.
    barras: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), default=Decimal("0"), server_default="0"
    )
    precio_barra: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    abonado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    abonos: Mapped[list["AbonoCompraQueso"]] = relationship(
        back_populates="compra", lazy="selectin", cascade="all, delete-orphan",
        order_by="AbonoCompraQueso.fecha",
    )
    # Soportes de pago (fotos de las transferencias). viewonly y con el filtro de
    # borrados DENTRO del join: los adjuntos se borran en suave, y sin el filtro
    # aquí la lista seguiría contando los que ya se borraron. Es de solo lectura
    # para que el ORM no intente cascadas: borrar un adjunto tiene que pasar por
    # el servicio, que además borra el objeto en R2.
    adjuntos: Mapped[list["AdjuntoReventa"]] = relationship(
        primaryjoin=(
            "and_(CompraQueso.id == AdjuntoReventa.compra_id, "
            "AdjuntoReventa.deleted_at.is_(None))"
        ),
        viewonly=True,
        lazy="selectin",
        order_by="AdjuntoReventa.created_at",
    )

    @property
    def saldo(self) -> Decimal:
        return max(Decimal("0"), self.valor_total - self.abonado)

    @property
    def saldo_a_favor(self) -> Decimal:
        return max(Decimal("0"), self.abonado - self.valor_total)

    @property
    def unidad(self) -> str:
        """En qué se mide esta compra: 'kg' o 'unidad'. Se deduce de las columnas (ver
        `unidad_de`). Viaja en la respuesta para que la pantalla ponga el rótulo
        correcto sin tener que conocer la regla."""
        return unidad_de(self)

    @property
    def adjuntos_count(self) -> int:
        """Cuántos soportes tiene. Va en la lista para que se vea de un vistazo
        cuáles compras tienen respaldo del pago y cuáles no."""
        return len(self.adjuntos)


class AbonoCompraQueso(AuditMixin, Base):
    __tablename__ = "abonos_compra_queso"

    compra_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compras_queso.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    compra: Mapped[CompraQueso] = relationship(back_populates="abonos")


# Nombres viejos del tipo de venta. Se conservan porque están usados en todo el
# módulo y en las pruebas; son los mismos valores de TIPO_QUESO/TIPO_BORONA.
TIPO_VENTA_QUESO = TIPO_QUESO
TIPO_VENTA_BORONA = TIPO_BORONA
TIPO_VENTA_MOZZARELLA = TIPO_MOZZARELLA


class VentaQueso(HoraDeRegistroMixin, TenantMixin, AuditMixin, Base):
    # Misma hora de registro con microsegundos que en la compra, y por lo mismo:
    # el orden de las ventas del día decide de qué compra sale cada kilo.
    __tablename__ = "ventas_queso"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cliente: Mapped[str] = mapped_column(String(150), nullable=False)
    # A qué factura pertenece este renglón, y en qué lugar de ella. Mismo trato
    # que en la compra y por las mismas razones: ver los comentarios largos en
    # CompraQueso.documento_id y CompraQueso.orden.
    documento_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documentos_reventa.id", ondelete="SET NULL"), index=True, default=None
    )
    orden: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # Qué se vende: queso entero (kg), borona (kg, subproducto más barato) o
    # mozzarella (barras). La unidad se deduce del tipo, ver `unidad_de`.
    tipo: Mapped[str] = mapped_column(
        String(20), default=TIPO_VENTA_QUESO, server_default=TIPO_VENTA_QUESO, index=True
    )
    kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_kilo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # ------------------------------------------------------------- mozzarella
    # Barras vendidas y precio de CADA BARRA. Escala 0 por lo mismo que en la
    # compra: no existe media barra. En las ventas de kilos van en cero.
    barras: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), default=Decimal("0"), server_default="0"
    )
    precio_barra: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Gastos que conlleva vender el lote (ej. transporte por kilo). NO cambian lo
    # que paga el cliente (valor_total); solo reducen la ganancia de la reventa.
    gasto_concepto: Mapped[str | None] = mapped_column(String(150))
    gasto_por_kilo: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    # El mismo gasto pero POR BARRA, para las ventas de mozzarella. Va en columna
    # aparte y no reutilizando `gasto_por_kilo` porque un valor "por barra"
    # guardado en una columna que se llama por_kilo es exactamente la confusión
    # que este trabajo tiene que evitar: el día que alguien sume esa columna
    # creyendo que son pesos por kilo, la cifra que saque no significa nada.
    gasto_por_barra: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    # Total del gasto, en PESOS: gasto_por_kilo * kilos en las ventas de kilos y
    # gasto_por_barra * barras en las de mozzarella (lo calcula el servicio).
    # Es una sola columna porque los pesos SÍ se suman entre unidades.
    gasto_monto: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )
    abonado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    abonos: Mapped[list["AbonoVentaQueso"]] = relationship(
        back_populates="venta", lazy="selectin", cascade="all, delete-orphan",
        order_by="AbonoVentaQueso.fecha",
    )
    # Soportes de pago de la venta. Mismo trato que en la compra: ver allá.
    adjuntos: Mapped[list["AdjuntoReventa"]] = relationship(
        primaryjoin=(
            "and_(VentaQueso.id == AdjuntoReventa.venta_id, "
            "AdjuntoReventa.deleted_at.is_(None))"
        ),
        viewonly=True,
        lazy="selectin",
        order_by="AdjuntoReventa.created_at",
    )

    @property
    def saldo(self) -> Decimal:
        return max(Decimal("0"), self.valor_total - self.abonado)

    @property
    def saldo_a_favor(self) -> Decimal:
        return max(Decimal("0"), self.abonado - self.valor_total)

    @property
    def unidad(self) -> str:
        """'kg' o 'unidad' (ver `unidad_de`)."""
        return unidad_de(self)

    @property
    def adjuntos_count(self) -> int:
        return len(self.adjuntos)


# Destino de un ajuste del queso disponible de reventa
DESTINO_BORONA = "borona"  # pasa a borona (subproducto vendible)
DESTINO_MERMA = "merma"  # pérdida (no se vende ni suma a ningún inventario)


class ConversionBorona(HoraDeRegistroMixin, TenantMixin, AuditMixin, Base):
    """Ajuste que reduce el queso disponible de reventa. Según `destino`:
    - borona: el queso se pasa a borona (devuelto o ya no vendible como entero)
      y suma al inventario de borona para venderse como subproducto.
    - merma: pérdida de peso (se pesó menos al vender); no suma a ningún lado.

    SOLO APLICA AL QUESO EN KILOS, y por eso no tiene ni tipo ni barras: pasar a
    borona es desmenuzar queso, y la merma es peso que se perdió. Una barra de
    mozzarella no se desmenuza ni pierde peso en el camino —entra como barra y
    sale como barra—, así que la mozzarella no participa en estos ajustes. Si
    algún día una barra se daña, eso es otro concepto (una baja de unidades) y
    tendría que ser su propio movimiento, nunca kilos metidos en esta tabla.
    """

    __tablename__ = "conversiones_borona"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kilos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    destino: Mapped[str] = mapped_column(
        String(20), default=DESTINO_BORONA, server_default=DESTINO_BORONA, index=True
    )
    # Precio por kilo de la borona (solo aplica cuando destino = borona; la merma
    # es pérdida sin valor). Sirve para valorar la borona generada.
    precio_kilo: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0"
    )
    observaciones: Mapped[str | None] = mapped_column(String(300))


class AbonoVentaQueso(AuditMixin, Base):
    __tablename__ = "abonos_venta_queso"

    venta_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ventas_queso.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    venta: Mapped[VentaQueso] = relationship(back_populates="abonos")


# Saldos que vienen del sistema anterior ("el libro viejo")
TIPO_SALDO_COBRAR = "cobrar"  # un cliente le quedó debiendo una venta vieja
TIPO_SALDO_PAGAR = "pagar"  # él le quedó debiendo una compra vieja a un productor


class SaldoAnterior(TenantMixin, AuditMixin, Base):
    """Cuenta a medio pagar heredada del sistema que usaba antes el cliente.

    Es un concepto APARTE de las compras y las ventas a propósito. Suma en
    "por cobrar a clientes" o en "por pagar a productores", acepta abonos y
    sale en el estado de cuenta, pero NO toca el inventario, NI los kilos, NI
    la ganancia: aquí nunca se compró ni se vendió ese queso, así que cargarlo
    como compra o venta obligaría a inventar kilos y rompería la
    reconciliación del desglose de la ganancia con la cifra del período.

    El estado (columna del AuditMixin) usa las mismas constantes de pago que
    las compras y las ventas: pendiente / parcial / pagada / anulada.
    """

    __tablename__ = "saldos_anteriores"

    tipo: Mapped[str] = mapped_column(
        String(20), default=TIPO_SALDO_COBRAR, server_default=TIPO_SALDO_COBRAR, index=True
    )
    # Nombre del cliente (si el tipo es 'cobrar') o del productor (si es 'pagar')
    tercero: Mapped[str] = mapped_column(String(150), nullable=False)
    # Fecha ORIGINAL del documento en el libro viejo, no la de la carga: es la
    # que el tercero reconoce cuando se le manda el estado de cuenta.
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # De qué era la deuda: "Venta 120 kg del 3 de mayo", "Factura 045"
    concepto: Mapped[str] = mapped_column(String(200), nullable=False)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    abonado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    abonos: Mapped[list["AbonoSaldoAnterior"]] = relationship(
        back_populates="saldo", lazy="selectin", cascade="all, delete-orphan",
        order_by="AbonoSaldoAnterior.fecha",
    )

    @property
    def saldo(self) -> Decimal:
        return max(Decimal("0"), self.valor_total - self.abonado)

    @property
    def saldo_a_favor(self) -> Decimal:
        return max(Decimal("0"), self.abonado - self.valor_total)


class AbonoSaldoAnterior(AuditMixin, Base):
    __tablename__ = "abonos_saldo_anterior"

    saldo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("saldos_anteriores.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    saldo: Mapped[SaldoAnterior] = relationship(back_populates="abonos")


class Temporada(TenantMixin, AuditMixin, Base):
    """Un ciclo de compra y reventa con nombre y fechas: "Semana Santa 2026".

    Es SOLO un rango de fechas con nombre. No tiene columnas de plata a
    propósito, y esa es la decisión de diseño importante: la ganancia de la
    temporada NO se guarda, se calcula cada vez con el mismo motor del resumen
    (`ReventaResumenService.resumen`) sobre `fecha_inicio..fecha_fin`.

    Se hizo así por dos razones:

    1. Sirve HACIA ATRÁS. Como las cifras salen de los movimientos que ya están
       cargados, se puede registrar hoy una temporada de marzo y su ganancia
       aparece de inmediato. Con una cifra congelada habría que esperar a cerrar
       la próxima para empezar a tener historia.
    2. NO SE DESACTUALIZA. Si mañana se le corrige el precio a una compra de esa
       temporada, la ganancia se mueve con ella. Una cifra congelada quedaría
       distinta de la que muestra el Resumen filtrado a las mismas fechas, y el
       usuario cuadra estos números a mano: dos cifras que no coinciden para el
       mismo rango es exactamente lo que le haría perder la confianza en todo el
       tablero.

    La contra es que "cerrar" una temporada no congela nada, pero eso es honesto:
    lo que se cierra es el ciclo del queso, no el libro.

    Las temporadas de una empresa NO SE PUEDEN SOLAPAR (lo valida el servicio).
    Si se solaparan, el mismo kilo y el mismo peso caerían en dos temporadas y la
    suma de las ganancias por temporada no daría la ganancia total.

    `fecha_fin` en NULL significa TEMPORADA ABIERTA (la que está corriendo), y
    solo puede haber una así. El estado del AuditMixin no se usa para esto: la
    única fuente de verdad de si está abierta es `fecha_fin`, porque dos fuentes
    para el mismo hecho terminan contradiciéndose.
    """

    __tablename__ = "temporadas"

    nombre: Mapped[str] = mapped_column(String(80), nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fecha_fin: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    notas: Mapped[str | None] = mapped_column(String(500))

    @property
    def abierta(self) -> bool:
        return self.fecha_fin is None


class AdjuntoReventa(TenantMixin, AuditMixin, Base):
    """Un soporte de pago (foto de la transferencia) de una compra O de una venta.

    POR QUÉ NO SE GUARDA NINGUNA URL. Solo se guarda `object_key`, la llave del
    objeto dentro del bucket privado de Cloudflare R2. La URL para verlo se firma
    en el momento en que alguien la pide y caduca sola (ver app/core/storage.py).
    Una URL guardada en una columna es un permiso permanente: quien la viera —en
    un backup, en un log, en un export— vería el soporte de pago para siempre,
    con el nombre, la cuenta y el monto de una transferencia real.

    POR QUÉ LA LLAVE LLEVA EL empresa_id ADENTRO. El formato es
    `{empresa_id}/reventa/compras/{compra_id}/{uuid}.jpg`. Además del filtro por
    empresa en cada consulta, la llave misma queda amarrada a la empresa dueña:
    si algún día una consulta se escapara sin filtro, la llave que se firmaría
    seguiría siendo la de un archivo de OTRA empresa y se notaría de inmediato
    en la auditoría; y como la llave lleva un uuid aleatorio, tampoco se puede
    adivinar la de nadie.

    UN ADJUNTO CUELGA DE UNA COMPRA O DE UNA VENTA, NUNCA DE LAS DOS NI DE
    NINGUNA. Lo garantiza un CHECK en la tabla y no solo el servicio: una fila
    con las dos en NULL sería un archivo huérfano pagando almacenamiento sin que
    nadie pueda verlo ni borrarlo desde la interfaz.
    """

    __tablename__ = "adjuntos_reventa"
    __table_args__ = (
        CheckConstraint(
            "(compra_id IS NOT NULL AND venta_id IS NULL) "
            "OR (compra_id IS NULL AND venta_id IS NOT NULL)",
            name="ck_adjuntos_reventa_un_solo_dueno",
        ),
        # Con nombre explícito y no con `unique=True` en la columna: así el
        # nombre es el MISMO que el de la migración y un `alembic revision
        # --autogenerate` futuro no propone borrarla y volverla a crear.
        UniqueConstraint("object_key", name="uq_adjuntos_reventa_object_key"),
    )

    compra_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compras_queso.id", ondelete="CASCADE"), index=True, default=None
    )
    venta_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ventas_queso.id", ondelete="CASCADE"), index=True, default=None
    )
    # Llave del objeto en R2. Única (ver __table_args__): dos filas apuntando al
    # mismo archivo harían que borrar una dejara a la otra señalando un objeto
    # que ya no existe.
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # Nombre con el que llegó el archivo, para mostrarlo y para nombrar la
    # descarga. NO se usa para armar la llave: el nombre lo escribe quien sube.
    nombre_archivo: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    tamano_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Quién lo subió: el id va en `created_by` (AuditMixin), que es la única
    # fuente de ese dato. Aquí se guarda solo el NOMBRE tal como estaba al subir,
    # que es un hecho distinto: si mañana el usuario se borra o se le cambia el
    # nombre, el soporte tiene que seguir diciendo quién lo aportó.
    subido_por_nombre: Mapped[str | None] = mapped_column(String(150), default=None)

    @property
    def es_imagen(self) -> bool:
        return (self.content_type or "").startswith("image/")
