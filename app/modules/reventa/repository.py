import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, literal, select

from app.common.repository import BaseRepository
from app.modules.reventa.models import (
    DESTINO_BORONA,
    DESTINO_MERMA,
    TIPO_DOC_COMPRA,
    TIPO_DOC_VENTA,
    TIPO_MOZZARELLA,
    UNIDAD_UNIDAD,
    AdjuntoReventa,
    CompraQueso,
    ConversionBorona,
    DocumentoReventa,
    ProductoReventa,
    SaldoAnterior,
    Temporada,
    VentaQueso,
)

CERO = Decimal("0")


def se_mide_en_unidades(columna_tipo):
    """La fila se mide en unidades/barras (mozzarella, huevos, etc)."""
    return func.coalesce(columna_tipo, "").in_(
        select(ProductoReventa.clave).where(ProductoReventa.unidad == UNIDAD_UNIDAD)
    )


def se_mide_en_kilos(columna_tipo):
    """La fila se mide en KILOS (queso o borona).

    A partir de Lote 2, lee de productos_reventa. Todo lo viejo o
    con tipo en blanco se asume como kilos.
    """
    return func.coalesce(columna_tipo, "").notin_(
        select(ProductoReventa.clave).where(ProductoReventa.unidad == UNIDAD_UNIDAD)
    )


def clave_nombre(columna):
    """Clave para agrupar nombres escritos distinto: sin mayúsculas ni espacios
    de sobra. Así "Sebastián Ruiz", "sebastián ruiz" y " Sebastián Ruiz " son el
    mismo productor y no se parten los kilos ni el ranking.
    """
    return func.lower(func.trim(columna))


def saldo_pendiente(valor_total, abonado):
    """Saldo de UNA fila para los AGREGADOS de cartera, acotado en cero.

    Un tercero puede quedar con saldo NEGATIVO (pagó de más): pasa al rebajarle
    el precio a una venta ya pagada, que es un caso real y permitido —el estado
    de cuenta tiene su rótulo de "saldo a favor"—. Sumar ese negativo crudo lo
    hacía RESTAR de "Por cobrar a clientes": lo que un cliente pagó de más NO
    reduce lo que OTROS deben, así que la tarjeta mostraba menos plata cobrable
    de la que hay. Se acota fila por fila ANTES de sumar.

    Se acota con CASE WHEN y no con `greatest`: greatest existe en Postgres pero
    NO en SQLite (donde corren las pruebas), que solo tiene max() con dos
    argumentos. El CASE es el mismo SQL en las dos bases.

    OJO: el saldo a favor no se pierde ni se esconde, solo no se mezcla con la
    cartera. Sigue saliendo en el estado de cuenta del cliente, que se calcula
    fila por fila con el `saldo` del modelo (sin acotar) y con su signo propio.

    Y ojo también: TODOS los agregados que se comparan entre sí tienen que usar
    esta misma función. El detalle por productor y la tarjeta "Por pagar a
    productores" se verifican a mano con calculadora: si uno acota y el otro no,
    la columna deja de sumar la cifra grande.
    """
    pendiente = valor_total - abonado
    return case((pendiente > 0, pendiente), else_=literal(0))


class CompraQuesoRepository(BaseRepository[CompraQueso]):
    model = CompraQueso
    search_fields = ("productor",)
    default_order_by = "fecha"

    def totales_periodo(self, desde: date, hasta: date) -> tuple[Decimal, Decimal]:
        """(kilos netos, plata) de las compras EN KILOS del período.

        FILTRA LA MOZZARELLA, y la plata que devuelve es SOLO la de los kilos. Es
        la decisión que hace que el precio promedio por kilo siga significando
        algo: `precio_promedio_compra = plata / kilos`, y si la plata trajera
        adentro lo que costaron unas barras, ese promedio saldría inflado con
        pesos que no salieron de ningún kilo. Lo mismo pasa con el desglose por
        producto, que reparte esta plata entre los DESTINOS DE LOS KILOS.

        La plata de la mozzarella no se pierde: sale por `totales_periodo_barras`
        y el servicio la suma al total de compras, porque los pesos sí se suman.
        """
        fila = self.db.execute(
            select(
                func.coalesce(func.sum(CompraQueso.kilos_netos), 0),
                func.coalesce(func.sum(CompraQueso.valor_total), 0),
            ).where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
                CompraQueso.fecha.between(desde, hasta),
                se_mide_en_kilos(CompraQueso.tipo),
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1])

    def totales_periodo_barras(self, desde: date, hasta: date) -> tuple[Decimal, Decimal]:
        """(barras, plata) de las compras de MOZZARELLA del período.

        El espejo exacto de `totales_periodo`, en la otra unidad. Son dos consultas
        y no una con dos pares de columnas porque así ninguna de las dos puede
        devolver una cifra contaminada con la otra unidad: cada una filtra por su
        lado y lo que suma es homogéneo.
        """
        fila = self.db.execute(
            select(
                func.coalesce(func.sum(CompraQueso.barras), 0),
                func.coalesce(func.sum(CompraQueso.valor_total), 0),
            ).where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
                CompraQueso.fecha.between(desde, hasta),
                se_mide_en_unidades(CompraQueso.tipo),
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1])

    def pendiente_periodo(self, desde: date, hasta: date) -> Decimal:
        """Lo que falta pagar SOLO por las compras de este rango de fechas.

        Es distinto de `acumulados()[2]`, que es la cartera de siempre: para saber
        si una temporada quedó cerrada hay que mirar lo de ESA temporada. Con la
        cifra histórica, una temporada de marzo ya pagada aparecería con deuda
        solo porque la de julio todavía debe.

        Va acotado en cero fila por fila con la misma `saldo_pendiente` que los
        demás agregados: si acotara distinto, la suma de las temporadas no daría
        la cifra de la tarjeta y el usuario lo nota con la calculadora.
        """
        total = self.db.scalar(
            select(
                func.coalesce(
                    func.sum(saldo_pendiente(CompraQueso.valor_total, CompraQueso.abonado)), 0
                )
            ).where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
                CompraQueso.fecha.between(desde, hasta),
            )
        )
        return Decimal(total or 0)

    def eventos_para_lotes(self) -> list[tuple]:
        """Todas las compras vigentes en orden cronológico, para el reparto FIFO.

        Va sin filtro de fechas A PROPÓSITO. El reparto por lotes necesita TODA la
        historia: para saber qué había en inventario el 25 de julio hay que haber
        procesado lo comprado y lo vendido antes. Filtrar aquí daría un inventario
        inicial inventado, y las ventas de los primeros días se irían a "sin lote"
        sin razón. El filtro de fechas se aplica al final, a qué lotes se MUESTRAN.

        Devuelve (fecha, created_at, productor, kilos_netos, borona_kilos,
        valor_total, saldo acotado en cero, precio_kilo).

        LA MOZZARELLA NO ENTRA AQUÍ, y es a propósito. El motor de lotes está
        escrito en kilos de punta a punta: un lote son las compras de una fecha
        con UN costo por kilo (`costo_total / kilos_comprados`). Una compra de
        barras tiene kilos 0 y plata > 0, así que entraría inflando el costo por
        kilo del lote con pesos que no salieron de ningún kilo, y su plata se
        quedaría además dando vueltas en "costo de lo que sigue en inventario"
        sin kilos a los que pertenecer: justo la mezcla de unidades que este
        trabajo tiene que evitar. La ganancia de la mozzarella se ve completa en
        el Resumen, en su propio renglón y en su propia unidad.
        """
        return list(
            self.db.execute(
                select(
                    CompraQueso.fecha,
                    CompraQueso.created_at,
                    CompraQueso.productor,
                    CompraQueso.kilos_netos,
                    CompraQueso.borona_kilos,
                    CompraQueso.valor_total,
                    saldo_pendiente(CompraQueso.valor_total, CompraQueso.abonado),
                    CompraQueso.precio_kilo,
                )
                .where(
                    CompraQueso.empresa_id == self.empresa_id,
                    CompraQueso.deleted_at.is_(None),
                    CompraQueso.estado != "anulada",
                    se_mide_en_kilos(CompraQueso.tipo),
                )
                .order_by(
                    CompraQueso.fecha,
                    CompraQueso.created_at,
                    CompraQueso.orden,
                    CompraQueso.id,
                )
            ).all()
        )

    def barras_acumuladas(self) -> Decimal:
        """Barras de mozzarella compradas HISTÓRICAS (sin filtro de fechas).

        Va en un método aparte y no como una cuarta columna de `acumulados()` para
        que sea IMPOSIBLE unpackearla por descuido donde se esperan kilos. Quien
        pide barras tiene que escribir `barras_acumuladas()`, con la unidad en el
        nombre.
        """
        total = self.db.scalar(
            select(func.coalesce(func.sum(CompraQueso.barras), 0)).where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
            )
        )
        return Decimal(total or 0)

    def acumulados(self) -> tuple[Decimal, Decimal, Decimal]:
        """(kilos netos históricos, borona de compras, saldo por pagar).

        El saldo va acotado en cero fila por fila (ver `saldo_pendiente`): una
        compra pagada de más no puede rebajar lo que se les debe a los demás.

        NO LLEVA FILTRO DE TIPO, Y ESO ES CORRECTO EN LAS TRES CIFRAS:
        - los kilos y la borona de una compra de mozzarella están en CERO (lo
          exige el CHECK de la tabla), así que no pueden contaminar esas sumas
          ni por descuido;
        - el saldo SÍ tiene que incluirlas: lo que se le debe a un productor por
          unas barras es plata que se le debe igual, y la tarjeta "Por pagar a
          productores" es de pesos, no de kilos.
        Las barras se piden con `barras_acumuladas()`.
        """
        fila = self.db.execute(
            select(
                func.coalesce(func.sum(CompraQueso.kilos_netos), 0),
                func.coalesce(func.sum(CompraQueso.borona_kilos), 0),
                func.coalesce(
                    func.sum(
                        saldo_pendiente(CompraQueso.valor_total, CompraQueso.abonado)
                    ),
                    0,
                ),
            ).where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1]), Decimal(fila[2])

    def por_productor(
        self, desde: date, hasta: date
    ) -> list[tuple[str, int, Decimal, Decimal, Decimal, Decimal, Decimal]]:
        """Compras del período agrupadas por productor:
        (productor, cuántas compras, kilos, valor comprado TOTAL, saldo por pagar,
        barras, valor comprado en barras).
        Ordenadas por valor comprado de mayor a menor.

        Ojo: el valor comprado NO es lo que se le pagó (eso es `abonado`), es lo
        que valen sus compras. El saldo por pagar sí es HISTÓRICO (lo que se le
        debe hoy), para que cuadre con la tarjeta "Por pagar a productores".

        LAS DOS UNIDADES VIAJAN SEPARADAS. `kilos` y `barras` son columnas
        distintas y nunca se suman entre sí. La plata sí: el 4.º campo es TODA la
        plata que se le compró (kilos + barras) y el 7.º es el pedazo de las
        barras, para poder sacar los dos precios promedio por separado:
            precio por kilo  = (4.º - 7.º) / kilos
            precio por barra = 7.º / barras
        Los tres primeros campos y el 5.º conservan el mismo significado que
        antes de la mozzarella, así que el detalle de un productor que solo
        vende queso sale idéntico al de siempre.
        """
        clave = clave_nombre(CompraQueso.productor)
        base = [
            CompraQueso.empresa_id == self.empresa_id,
            CompraQueso.deleted_at.is_(None),
            CompraQueso.estado != "anulada",
        ]
        filas = self.db.execute(
            select(
                clave,
                func.min(CompraQueso.productor),
                func.count(),
                func.coalesce(func.sum(CompraQueso.kilos_netos), 0),
                func.coalesce(func.sum(CompraQueso.valor_total), 0),
                func.coalesce(func.sum(CompraQueso.barras), 0),
                # La plata de SUS compras de mozzarella. Con .filter() (FILTER de
                # SQL, que SQLAlchemy traduce a CASE donde no lo hay) y no con una
                # segunda consulta: así el mismo GROUP BY entrega las dos y no hay
                # forma de que una traiga productores que la otra no.
                func.coalesce(
                    func.sum(CompraQueso.valor_total).filter(
                        se_mide_en_unidades(CompraQueso.tipo)
                    ),
                    0,
                ),
            )
            .where(*base, CompraQueso.fecha.between(desde, hasta))
            .group_by(clave)
            .order_by(func.sum(CompraQueso.valor_total).desc())
        ).all()
        saldos = dict(
            self.db.execute(
                select(
                    clave,
                    func.coalesce(
                        func.sum(
                            saldo_pendiente(CompraQueso.valor_total, CompraQueso.abonado)
                        ),
                        0,
                    ),
                )
                .where(*base)
                .group_by(clave)
            ).all()
        )
        return [
            (
                fila[1],
                int(fila[2]),
                Decimal(fila[3]),
                Decimal(fila[4]),
                Decimal(saldos.get(fila[0], 0)),
                Decimal(fila[5]),
                Decimal(fila[6]),
            )
            for fila in filas
        ]

    def pendiente_por_productor(self) -> list[tuple[str, Decimal]]:
        """(nombre del productor, saldo por pagar) HISTÓRICO: sin filtro de
        fechas y agrupando por la misma clave que `por_productor`.

        Es el conjunto completo de productores a los que se les debe HOY, que NO
        es el mismo que el de los que tuvieron compras en el período: a quien se
        le compró en mayo y no se le ha pagado se le sigue debiendo en julio. El
        detalle por productor se arma con este conjunto para que la columna
        `por_pagar` sume EXACTAMENTE la tarjeta "Por pagar a productores", que
        también es histórica (viene de `acumulados`).

        Misma forma que SaldoAnteriorRepository.pendiente_por_tercero, para
        poder agrupar las dos en Python con el mismo criterio.

        El saldo va acotado en cero fila por fila, con el MISMO criterio de
        `acumulados` (ver `saldo_pendiente`): si uno acotara y el otro no, la
        columna `por_pagar` dejaría de sumar la tarjeta.
        """
        clave = clave_nombre(CompraQueso.productor)
        filas = self.db.execute(
            select(
                func.min(CompraQueso.productor),
                func.coalesce(
                    func.sum(
                        saldo_pendiente(CompraQueso.valor_total, CompraQueso.abonado)
                    ),
                    0,
                ),
            )
            .where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
            )
            .group_by(clave)
        ).all()
        return [(fila[0], Decimal(fila[1])) for fila in filas]

    def del_productor(
        self, productor: str, desde: date | None = None, hasta: date | None = None
    ) -> list[CompraQueso]:
        """Compras vigentes de UN productor, de la más antigua a la más reciente.

        Es el hermano de VentaQuesoRepository.por_cliente, para el estado de
        cuenta del productor. Se llama `del_productor` y no `por_productor`
        porque ese nombre ya lo usa el AGREGADO del período (kilos y valor
        comprado por productor), que devuelve tuplas y no filas.

        El nombre se compara NORMALIZADO (misma clave que agrupa el ranking), así
        un "sebastián ruiz " de datos viejos entra en el estado de cuenta de
        "Sebastián Ruiz" y su saldo no queda partido en dos productores.

        El nombre viaja como PARÁMETRO de la consulta (`literal`), nunca pegado al
        texto del SQL: es texto libre que escribe el usuario.

        Los espacios internos se colapsan en Python ANTES de bindear, igual que
        hace _canonizar_nombre al guardar: lower(trim(...)) recorta las puntas
        pero deja los espacios de la mitad, así que registrar
        "Sebastián  Ruiz" (guardado como "Sebastián Ruiz") y consultarlo con ese
        mismo texto daría 404.

        Sin rango de fechas devuelve todo el histórico, que es lo que muestra lo
        que de verdad se le debe. Los abonos ya vienen con lazy="selectin".
        """
        buscado = " ".join((productor or "").split())
        criterios = [
            CompraQueso.empresa_id == self.empresa_id,
            CompraQueso.deleted_at.is_(None),
            CompraQueso.estado != "anulada",
            clave_nombre(CompraQueso.productor) == clave_nombre(literal(buscado)),
        ]
        if desde:
            criterios.append(CompraQueso.fecha >= desde)
        if hasta:
            criterios.append(CompraQueso.fecha <= hasta)
        return list(
            self.db.scalars(
                select(CompraQueso)
                .where(*criterios)
                .order_by(CompraQueso.fecha, CompraQueso.created_at)
            ).all()
        )

    def nombres_productores(self) -> list[str]:
        """Nombres de productores ya usados (para autocompletar), sin repetir.
        Agrupa las variantes de escritura para no ofrecer el mismo dos veces."""
        clave = clave_nombre(CompraQueso.productor)
        rows = self.db.execute(
            select(func.min(CompraQueso.productor))
            .where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
            )
            .group_by(clave)
            .order_by(clave)
        ).scalars().all()
        return [r for r in rows if r]


class VentaQuesoRepository(BaseRepository[VentaQueso]):
    model = VentaQueso
    search_fields = ("cliente",)
    default_order_by = "fecha"

    def totales_periodo(
        self, desde: date, hasta: date, tipo: str | None = None
    ) -> tuple[Decimal, Decimal]:
        """(kilos, plata) de las ventas EN KILOS del período.

        SIN `tipo` NO SIGNIFICA "TODAS": significa todas las de kilos (queso y
        borona). La mozzarella queda fuera siempre, y no es un detalle: el resumen
        saca la borona POR DIFERENCIA (`total - queso`) para que ningún tipo nuevo
        ni ningún dato viejo con el tipo en blanco pierda su plata. Si aquí
        entrara la mozzarella, esa resta le acreditaría a la BORONA los pesos de
        las barras y la fila de borona del desglose diría una cifra que no es.
        Las barras salen por `totales_periodo_barras`.
        """
        criterios = [
            VentaQueso.empresa_id == self.empresa_id,
            VentaQueso.deleted_at.is_(None),
            VentaQueso.estado != "anulada",
            VentaQueso.fecha.between(desde, hasta),
            se_mide_en_kilos(VentaQueso.tipo),
        ]
        if tipo:
            criterios.append(VentaQueso.tipo == tipo)
        fila = self.db.execute(
            select(
                func.coalesce(func.sum(VentaQueso.kilos), 0),
                func.coalesce(func.sum(VentaQueso.valor_total), 0),
            ).where(*criterios)
        ).one()
        return Decimal(fila[0]), Decimal(fila[1])

    def totales_periodo_barras(self, desde: date, hasta: date) -> tuple[Decimal, Decimal]:
        """(barras, plata) de las ventas de MOZZARELLA del período."""
        fila = self.db.execute(
            select(
                func.coalesce(func.sum(VentaQueso.barras), 0),
                func.coalesce(func.sum(VentaQueso.valor_total), 0),
            ).where(
                VentaQueso.empresa_id == self.empresa_id,
                VentaQueso.deleted_at.is_(None),
                VentaQueso.estado != "anulada",
                VentaQueso.fecha.between(desde, hasta),
                se_mide_en_unidades(VentaQueso.tipo),
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1])

    def barras_acumuladas(self) -> Decimal:
        """Barras de mozzarella vendidas HISTÓRICAS. Método aparte y con la unidad
        en el nombre, por lo mismo que en las compras."""
        total = self.db.scalar(
            select(func.coalesce(func.sum(VentaQueso.barras), 0)).where(
                VentaQueso.empresa_id == self.empresa_id,
                VentaQueso.deleted_at.is_(None),
                VentaQueso.estado != "anulada",
            )
        )
        return Decimal(total or 0)

    def acumulados(self) -> tuple[Decimal, Decimal, Decimal]:
        """(kilos queso vendidos, kilos borona vendidos, saldo por cobrar).

        El saldo por cobrar va acotado en cero fila por fila (ver
        `saldo_pendiente`): es aquí donde más pasa: rebajarle el precio a una
        venta ya pagada deja esa venta con saldo negativo, y ese negativo restaba
        de la tarjeta "Por cobrar a clientes" como si los demás clientes debieran
        menos.

        Los kilos ya venían filtrados por tipo ('queso' y 'borona' cada uno en su
        columna), así que la mozzarella no entra en ninguno de los dos: las barras
        se piden con `barras_acumuladas()`. El saldo SÍ la incluye, y tiene que
        incluirla: lo que un cliente debe por unas barras es plata que debe igual.
        """
        fila = self.db.execute(
            select(
                func.coalesce(
                    func.sum(VentaQueso.kilos).filter(VentaQueso.tipo == "queso"), 0
                ),
                func.coalesce(
                    func.sum(VentaQueso.kilos).filter(VentaQueso.tipo == "borona"), 0
                ),
                func.coalesce(
                    func.sum(saldo_pendiente(VentaQueso.valor_total, VentaQueso.abonado)),
                    0,
                ),
            ).where(
                VentaQueso.empresa_id == self.empresa_id,
                VentaQueso.deleted_at.is_(None),
                VentaQueso.estado != "anulada",
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1]), Decimal(fila[2])

    def pendiente_periodo(self, desde: date, hasta: date) -> Decimal:
        """Lo que falta cobrar SOLO por las ventas de este rango de fechas.

        El espejo de `CompraQuesoRepository.pendiente_periodo`: sirve para decir
        si una temporada quedó cerrada sin arrastrar la cartera de las otras.
        """
        total = self.db.scalar(
            select(
                func.coalesce(
                    func.sum(saldo_pendiente(VentaQueso.valor_total, VentaQueso.abonado)), 0
                )
            ).where(
                VentaQueso.empresa_id == self.empresa_id,
                VentaQueso.deleted_at.is_(None),
                VentaQueso.estado != "anulada",
                VentaQueso.fecha.between(desde, hasta),
            )
        )
        return Decimal(total or 0)

    def eventos_para_lotes(self) -> list[tuple]:
        """Todas las ventas vigentes en orden cronológico, para el reparto FIFO.
        Sin filtro de fechas, por lo mismo que en las compras.

        Devuelve (fecha, created_at, tipo, kilos, valor_total, gasto_monto,
        cliente, precio_kilo).

        LA MOZZARELLA NO ENTRA, igual que en las compras y por la misma razón
        (ver `CompraQuesoRepository.eventos_para_lotes`). Aquí además sería peor:
        una venta de barras tiene kilos 0 y plata > 0, así que el reparto no le
        encontraría kilos de dónde salir y su plata caería toda en
        `ingreso_sin_lote` —el aviso de "falta cargar una compra"—, que empezaría
        a gritar por unas ventas que están perfectamente registradas.
        """
        return list(
            self.db.execute(
                select(
                    VentaQueso.fecha,
                    VentaQueso.created_at,
                    VentaQueso.tipo,
                    VentaQueso.kilos,
                    VentaQueso.valor_total,
                    VentaQueso.gasto_monto,
                    VentaQueso.cliente,
                    VentaQueso.precio_kilo,
                )
                .where(
                    VentaQueso.empresa_id == self.empresa_id,
                    VentaQueso.deleted_at.is_(None),
                    VentaQueso.estado != "anulada",
                    se_mide_en_kilos(VentaQueso.tipo),
                )
                .order_by(
                    VentaQueso.fecha,
                    VentaQueso.created_at,
                    VentaQueso.orden,
                    VentaQueso.id,
                )
            ).all()
        )

    def gastos_periodo(self, desde: date, hasta: date, tipo: str | None = None) -> Decimal:
        """Suma de gastos de venta (transporte, etc.) de las ventas EN KILOS del
        período. Con `tipo` solo cuenta las ventas de ese tipo ('queso' o 'borona').

        Sin `tipo` NO son todas: son las de kilos, por lo mismo que en
        `totales_periodo`. El resumen saca los gastos de la borona por diferencia,
        y con la mozzarella dentro esa resta le cargaría a la borona el flete de
        unas barras. Los de la mozzarella salen por `gastos_periodo_barras`.
        """
        criterios = [
            VentaQueso.empresa_id == self.empresa_id,
            VentaQueso.deleted_at.is_(None),
            VentaQueso.estado != "anulada",
            VentaQueso.fecha.between(desde, hasta),
            se_mide_en_kilos(VentaQueso.tipo),
        ]
        if tipo:
            criterios.append(VentaQueso.tipo == tipo)
        total = self.db.scalar(
            select(func.coalesce(func.sum(VentaQueso.gasto_monto), 0)).where(*criterios)
        )
        return Decimal(total or 0)

    def gastos_periodo_barras(self, desde: date, hasta: date) -> Decimal:
        """Gastos de venta de la MOZZARELLA del período, en PESOS.

        Ojo: lo que se suma es `gasto_monto` (pesos), no `gasto_por_barra`. El
        gasto por barra es un precio unitario y sumarlo entre ventas no daría
        nada: sumar "$500 por barra" con "$700 por barra" no son $1.200 de nada.
        """
        total = self.db.scalar(
            select(func.coalesce(func.sum(VentaQueso.gasto_monto), 0)).where(
                VentaQueso.empresa_id == self.empresa_id,
                VentaQueso.deleted_at.is_(None),
                VentaQueso.estado != "anulada",
                VentaQueso.fecha.between(desde, hasta),
                se_mide_en_unidades(VentaQueso.tipo),
            )
        )
        return Decimal(total or 0)

    def por_cliente(
        self, cliente: str, desde: date | None = None, hasta: date | None = None
    ) -> list[VentaQueso]:
        """Ventas vigentes de un cliente, de la más antigua a la más reciente.

        El nombre se compara NORMALIZADO (misma clave que agrupa el ranking), así
        un "carlos ricaute " de datos viejos entra en el estado de cuenta de
        "Carlos Ricaute" y su saldo no queda partido en dos clientes.

        El nombre viaja como PARÁMETRO de la consulta (`literal`), nunca pegado al
        texto del SQL: es texto libre que escribe el usuario.

        Los espacios internos se colapsan en Python ANTES de bindear, igual que
        hace _canonizar_nombre al guardar: lower(trim(...)) recorta las puntas
        pero deja los espacios de la mitad, así que registrar
        "Sebastián  Ruiz" (guardado como "Sebastián Ruiz") y consultarlo con ese
        mismo texto daba 404.

        Sin rango de fechas devuelve todo el histórico, que es lo que muestra la
        deuda real del cliente. Los abonos ya vienen con lazy="selectin".
        """
        buscado = " ".join((cliente or "").split())
        criterios = [
            VentaQueso.empresa_id == self.empresa_id,
            VentaQueso.deleted_at.is_(None),
            VentaQueso.estado != "anulada",
            clave_nombre(VentaQueso.cliente) == clave_nombre(literal(buscado)),
        ]
        if desde:
            criterios.append(VentaQueso.fecha >= desde)
        if hasta:
            criterios.append(VentaQueso.fecha <= hasta)
        return list(
            self.db.scalars(
                select(VentaQueso)
                .where(*criterios)
                .order_by(VentaQueso.fecha, VentaQueso.created_at)
            ).all()
        )

    def nombres_clientes(self) -> list[str]:
        """Nombres de clientes ya usados (para autocompletar), sin repetir.
        Agrupa las variantes de escritura para no ofrecer el mismo dos veces."""
        clave = clave_nombre(VentaQueso.cliente)
        rows = self.db.execute(
            select(func.min(VentaQueso.cliente))
            .where(
                VentaQueso.empresa_id == self.empresa_id,
                VentaQueso.deleted_at.is_(None),
                VentaQueso.estado != "anulada",
            )
            .group_by(clave)
            .order_by(clave)
        ).scalars().all()
        return [r for r in rows if r]


class ProductoReventaRepository(BaseRepository[ProductoReventa]):
    """El catálogo de lo que se compra y se revende.

    NO SUMA NI UN PESO, y no es un olvido: un catálogo dice qué existe, no cuánto
    valió. En este lote ninguna consulta de plata lo lee (ver el modelo).

    Lo único fino que hay aquí es que dos de sus consultas MIRAN LAS FILAS
    BORRADAS, al contrario que todo el resto del sistema. Está explicado en cada
    una: el UNIQUE de (empresa_id, clave) tampoco filtra `deleted_at`, así que una
    fila borrada en suave sigue ocupando su clave, y una consulta que no la viera
    dejaría que el INSERT se estrellara contra la base.
    """

    model = ProductoReventa
    search_fields = ("nombre", "clave")
    # El catálogo se lee en el orden en que el dueño lo puso, no por fecha.
    default_order_by = "orden"

    def catalogo(self) -> list[ProductoReventa]:
        """Todos los productos vivos de la empresa, en el orden de la pantalla."""
        return list(
            self.db.execute(
                self.base_query().order_by(
                    ProductoReventa.orden.asc(), ProductoReventa.nombre.asc()
                )
            )
            .unique()
            .scalars()
        )

    def por_clave(self, clave: str, *, incluir_borrados: bool = False):
        """El producto de esa clave en ESTA empresa, si existe.

        `incluir_borrados=True` es el modo que hay que usar ANTES DE INSERTAR, y es
        la razón de ser de este parámetro: el UNIQUE no distingue filas borradas,
        así que "no hay ninguno vivo con esta clave" no significa "la clave está
        libre". Sin esto, volver a agregar un producto que se quitó reventaría con
        un error de base de datos en vez de con un mensaje.
        """
        stmt = select(ProductoReventa).where(
            ProductoReventa.empresa_id == self.empresa_id,
            ProductoReventa.clave == clave,
        )
        if not incluir_borrados:
            stmt = stmt.where(ProductoReventa.deleted_at.is_(None))
        return self.db.execute(stmt).unique().scalars().first()

    def siguiente_orden(self) -> int:
        """El puesto que le toca a un producto nuevo: al final de la lista.

        Cuenta las filas BORRADAS también. No es por gusto: si no, quitar el último
        producto y agregar otro le daría el puesto que ya tuvo el anterior, y dos
        filas con el mismo `orden` se muestran en el orden que quiera la base.
        """
        maximo = self.db.scalar(
            select(func.max(ProductoReventa.orden)).where(
                ProductoReventa.empresa_id == self.empresa_id
            )
        )
        return int(maximo) + 1 if maximo is not None else 0

    def hijos(self, producto_id) -> list[ProductoReventa]:
        """Los productos vivos que dicen ser subproducto de este."""
        return list(
            self.db.execute(
                self.base_query().where(ProductoReventa.subproducto_de_id == producto_id)
            )
            .unique()
            .scalars()
        )

    def movimientos(self, clave: str) -> tuple[int, int]:
        """(compras, ventas) de ese producto en esta empresa.

        EL VÍNCULO ES LA CLAVE, el mismo puente que está explicado en el modelo:
        `compras_queso.tipo` y `ventas_queso.tipo` guardan justamente esa cadena.
        O sea que esto no es una aproximación mientras llega un `producto_id`: es
        exactamente el conjunto de filas que hablan de este producto, hoy.

        Se cuentan las ANULADAS también. Una compra anulada no suma plata, pero es
        historia registrada de ese producto: quitarlo del catálogo dejaría una fila
        del cuaderno hablando de algo que ya no aparece en la lista.
        """
        conteos = []
        for modelo in (CompraQueso, VentaQueso):
            conteos.append(
                int(
                    self.db.scalar(
                        select(func.count())
                        .select_from(modelo)
                        .where(
                            modelo.empresa_id == self.empresa_id,
                            modelo.deleted_at.is_(None),
                            modelo.tipo == clave,
                        )
                    )
                    or 0
                )
            )
        return conteos[0], conteos[1]


class DocumentoReventaRepository(BaseRepository[DocumentoReventa]):
    """Las facturas de reventa (la cabecera que agrupa varios renglones).

    NINGUNA CONSULTA DE ESTE REPOSITORIO SUMA PLATA, y no es un olvido: la
    cabecera no tiene columnas de plata. Lo que hace es traer los RENGLONES, que
    son las filas de siempre de `compras_queso` / `ventas_queso`, y el servicio
    suma su `valor_total` y su `abonado` al leer.
    """

    model = DocumentoReventa
    search_fields = ("tercero",)
    default_order_by = "fecha"

    def listar_paginado(
        self, params, *, tipo: str | None = None, search: str | None = None,
        desde: date | None = None, hasta: date | None = None,
    ) -> tuple[list[DocumentoReventa], int]:
        """Una página de facturas, de la más reciente a la más vieja y EN UN ORDEN
        TOTAL Y ESTABLE.

        POR QUÉ NO USA `list_paginated` DEL REPOSITORIO GENÉRICO, que solo sabe
        ordenar por UNA columna: en un mismo día se registran varias facturas, y con
        un orden que empata la base puede devolverlas en cualquier orden en cada
        consulta. La misma factura saldría en la página 1 y otra vez en la 2, o en
        ninguna de las dos, y el dueño buscaría una compra que "desapareció". Por eso
        el orden desempata por `created_at` y de últimas por `id`, que es único:
        así el orden es TOTAL y la paginación no puede perder ni repetir una fila.
        """
        stmt = self.apply_search(self.base_query(), search)
        if tipo:
            stmt = stmt.where(DocumentoReventa.tipo == tipo)
        if desde:
            stmt = stmt.where(DocumentoReventa.fecha >= desde)
        if hasta:
            stmt = stmt.where(DocumentoReventa.fecha <= hasta)
        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        stmt = (
            stmt.order_by(
                DocumentoReventa.fecha.desc(),
                DocumentoReventa.created_at.desc(),
                DocumentoReventa.id,
            )
            .offset(params.offset)
            .limit(params.page_size)
        )
        return list(self.db.scalars(stmt).all()), total

    def modelo_de_renglon(self, tipo: str):
        """En cuál de las dos tablas viven los renglones de un documento.

        Una sola función para la pregunta, porque la respuesta se necesita en
        media docena de sitios y un `if` repetido es un `if` que algún día se
        escribe al revés.
        """
        return CompraQueso if tipo == TIPO_DOC_COMPRA else VentaQueso

    def _criterios_de_renglon(self, modelo, documento_ids: list) -> list:
        """empresa_id + deleted_at IS NULL + los documentos pedidos, SIEMPRE.

        El filtro por empresa va aquí aunque el documento ya venga filtrado por
        ella: es la regla de la casa y es lo que hace que un `documento_id`
        cruzado —que hoy nada puede escribir— no pueda traer el renglón de otra
        quesera ni por un defecto futuro.
        """
        return [
            modelo.empresa_id == self.empresa_id,
            modelo.deleted_at.is_(None),
            modelo.documento_id.in_(documento_ids),
        ]

    def renglones_de(self, documentos: list[DocumentoReventa]) -> dict:
        """{documento_id: [renglones en su orden]} para una lista de documentos.

        DE UN SOLO VIAJE POR TABLA (dos consultas en total, no una por documento):
        la lista paginada trae veinte facturas y pedir los renglones de cada una
        por separado serían veinte consultas que crecen con la página.
        """
        por_documento: dict = {d.id: [] for d in documentos}
        for tipo in (TIPO_DOC_COMPRA, TIPO_DOC_VENTA):
            ids = [d.id for d in documentos if d.tipo == tipo]
            if not ids:
                continue
            modelo = self.modelo_de_renglon(tipo)
            filas = self.db.scalars(
                select(modelo)
                .where(*self._criterios_de_renglon(modelo, ids))
                .order_by(modelo.orden, modelo.id)
            ).all()
            for fila in filas:
                por_documento[fila.documento_id].append(fila)
        return por_documento

    def renglones(self, documento: DocumentoReventa) -> list:
        """Los renglones de UN documento, en su orden (orden, después id)."""
        return self.renglones_de([documento])[documento.id]

    def renglones_bloqueados(self, documento: DocumentoReventa) -> list:
        """Los renglones de un documento con FOR UPDATE y EN ORDEN ESTABLE.

        POR QUÉ EL ORDEN NO ES UN DETALLE. `_bloquear` toma UNA fila y con una
        sola fila no hay forma de trenzarse; aquí se toman N, y dos abonos
        simultáneos a la misma factura que las pidieran en orden distinto se
        abrazarían en un deadlock: el primero tendría el renglón 1 esperando el 2
        y el segundo el 2 esperando el 1. Con `ORDER BY orden, id` los dos las
        piden en el mismo orden, así que el segundo se queda esperando la primera
        y no hay abrazo. El `id` de segundo criterio no es adorno: dos renglones
        pueden compartir `orden` (por ejemplo el 0 que dejó la migración) y ahí el
        orden dejaría de estar definido.

        `populate_existing` por la misma razón que en `_bloquear`: sin él el
        candado se toma en la base pero SQLAlchemy devuelve los valores viejos que
        tenía en memoria, que es exactamente lo que el candado venía a evitar.

        SQLite descarta el FOR UPDATE en silencio, así que nada de esto lo delata
        ninguna prueba: se sostiene por lectura del código.
        """
        modelo = self.modelo_de_renglon(documento.tipo)
        return list(
            self.db.scalars(
                select(modelo)
                .where(*self._criterios_de_renglon(modelo, [documento.id]))
                .order_by(modelo.orden, modelo.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            ).all()
        )


class SaldoAnteriorRepository(BaseRepository[SaldoAnterior]):
    """Saldos traídos del sistema anterior. Solo mueven plata: no hay kilos que
    consultar aquí, por eso no tiene totales de inventario ni de período."""

    model = SaldoAnterior
    search_fields = ("tercero", "concepto")
    default_order_by = "fecha"

    def _vigentes(self, tipo: str | None = None) -> list:
        """Criterios de un saldo que todavía cuenta: de esta empresa, no borrado
        y no anulado. Todas las consultas de plata pasan por aquí."""
        criterios = [
            SaldoAnterior.empresa_id == self.empresa_id,
            SaldoAnterior.deleted_at.is_(None),
            SaldoAnterior.estado != "anulada",
        ]
        if tipo is not None:
            criterios.append(SaldoAnterior.tipo == tipo)
        return criterios

    def pendiente(self, tipo: str) -> Decimal:
        """Lo que falta por cobrar (tipo 'cobrar') o por pagar (tipo 'pagar') del
        libro anterior: suma del saldo de cada fila, histórico y sin fechas.

        Acotado en cero fila por fila (ver `saldo_pendiente`), con el mismo
        criterio de las compras y las ventas: estas dos cifras se SUMAN con las de
        ellas en las tarjetas de cartera, así que tienen que contar igual. Hoy el
        servicio no deja dejar un saldo del libro por debajo de lo abonado, pero
        el criterio no puede depender de esa validación.
        """
        total = self.db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        saldo_pendiente(SaldoAnterior.valor_total, SaldoAnterior.abonado)
                    ),
                    0,
                )
            ).where(*self._vigentes(tipo))
        )
        return Decimal(total or CERO)

    def pendiente_por_tercero(self, tipo: str) -> list[tuple[str, Decimal]]:
        """(nombre del tercero, saldo pendiente) agrupando las variantes de
        escritura, para que el detalle por productor o por cliente sume EXACTO
        lo mismo que la tarjeta de arriba.

        El saldo va acotado en cero fila por fila, igual que en `pendiente` (ver
        `saldo_pendiente`): las dos cifras se comparan entre sí, así que las dos
        tienen que contar con el mismo criterio."""
        clave = clave_nombre(SaldoAnterior.tercero)
        filas = self.db.execute(
            select(
                func.min(SaldoAnterior.tercero),
                func.coalesce(
                    func.sum(
                        saldo_pendiente(SaldoAnterior.valor_total, SaldoAnterior.abonado)
                    ),
                    0,
                ),
            )
            .where(*self._vigentes(tipo))
            .group_by(clave)
        ).all()
        return [(fila[0], Decimal(fila[1])) for fila in filas]

    def por_tercero(
        self,
        tipo: str,
        tercero: str,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> list[SaldoAnterior]:
        """Saldos vigentes de un tercero, del más antiguo al más reciente.

        Mismo trato del nombre que en VentaQuesoRepository.por_cliente: se
        compara NORMALIZADO, los espacios internos se colapsan en Python antes
        de bindear y el texto viaja como PARÁMETRO (`literal`), nunca pegado al
        SQL, porque lo escribe el usuario.
        """
        buscado = " ".join((tercero or "").split())
        criterios = [
            *self._vigentes(tipo),
            clave_nombre(SaldoAnterior.tercero) == clave_nombre(literal(buscado)),
        ]
        if desde:
            criterios.append(SaldoAnterior.fecha >= desde)
        if hasta:
            criterios.append(SaldoAnterior.fecha <= hasta)
        return list(
            self.db.scalars(
                select(SaldoAnterior)
                .where(*criterios)
                .order_by(SaldoAnterior.fecha, SaldoAnterior.created_at)
            ).all()
        )

    def nombres_terceros(self, tipo: str) -> list[str]:
        """Nombres ya usados en los saldos de ese tipo, sin repetir. Sirven para
        canonizar la escritura, igual que los de productores y clientes."""
        clave = clave_nombre(SaldoAnterior.tercero)
        rows = self.db.execute(
            select(func.min(SaldoAnterior.tercero))
            .where(*self._vigentes(tipo))
            .group_by(clave)
            .order_by(clave)
        ).scalars().all()
        return [r for r in rows if r]


class ConversionBoronaRepository(BaseRepository[ConversionBorona]):
    model = ConversionBorona
    default_order_by = "fecha"

    def _total(self, destino: str | None = None) -> Decimal:
        criterios = [
            ConversionBorona.empresa_id == self.empresa_id,
            ConversionBorona.deleted_at.is_(None),
            ConversionBorona.estado == "activo",
        ]
        if destino is not None:
            criterios.append(ConversionBorona.destino == destino)
        return Decimal(
            self.db.scalar(
                select(func.coalesce(func.sum(ConversionBorona.kilos), 0)).where(*criterios)
            )
            or CERO
        )

    def totales_periodo(self, desde: date, hasta: date) -> tuple[Decimal, Decimal]:
        """(kilos pasados a borona, kilos registrados como merma) del período.
        La merma real del negocio son SOLO estos ajustes con destino merma."""
        fila = self.db.execute(
            select(
                func.coalesce(
                    func.sum(ConversionBorona.kilos).filter(
                        ConversionBorona.destino == DESTINO_BORONA
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(ConversionBorona.kilos).filter(
                        ConversionBorona.destino == DESTINO_MERMA
                    ),
                    0,
                ),
            ).where(
                ConversionBorona.empresa_id == self.empresa_id,
                ConversionBorona.deleted_at.is_(None),
                ConversionBorona.estado == "activo",
                ConversionBorona.fecha.between(desde, hasta),
            )
        ).one()
        return Decimal(fila[0]), Decimal(fila[1])

    def eventos_para_lotes(self) -> list[tuple]:
        """Todos los ajustes vigentes en orden cronológico, para el reparto FIFO.
        Sin filtro de fechas, por lo mismo que en las compras.

        Devuelve (fecha, created_at, kilos, destino).
        """
        return list(
            self.db.execute(
                select(
                    ConversionBorona.fecha,
                    ConversionBorona.created_at,
                    ConversionBorona.kilos,
                    ConversionBorona.destino,
                )
                .where(
                    ConversionBorona.empresa_id == self.empresa_id,
                    ConversionBorona.deleted_at.is_(None),
                    ConversionBorona.estado == "activo",
                )
                .order_by(ConversionBorona.fecha, ConversionBorona.created_at, ConversionBorona.id)
            ).all()
        )

    # Los tres siguientes son HISTÓRICOS (sin filtro de fechas): sirven para el
    # inventario disponible hoy. NO usarlos en cálculos de un período: para eso
    # está totales_periodo, o se mezclan kilos de temporadas distintas.
    def total_convertido(self) -> Decimal:
        """Todo lo que sale del queso disponible (borona + merma), histórico."""
        return self._total()

    def total_a_borona(self) -> Decimal:
        """Solo lo que se pasó a borona (suma al inventario de borona), histórico."""
        return self._total(DESTINO_BORONA)


class AdjuntoReventaRepository(BaseRepository[AdjuntoReventa]):
    """Soportes de pago de compras y ventas.

    Todo pasa por `base_query()` del repositorio genérico, que ya mete
    `empresa_id = <la del contexto>` y `deleted_at IS NULL`. Eso es lo que impide
    que alguien firme un enlace de un archivo de otra empresa: el adjunto
    simplemente no aparece y sale un 404, no un 403 que confirmaría que existe.
    """

    model = AdjuntoReventa
    default_order_by = "created_at"

    def de_documento(
        self, *, compra_id: uuid.UUID | None = None, venta_id: uuid.UUID | None = None
    ) -> list[AdjuntoReventa]:
        """Los soportes de UNA compra o de UNA venta, del más viejo al más nuevo.

        Ese orden es el que espera quien subió las fotos: primero la que mandó
        primero. Ir al revés haría que "la última que subí" quedara arriba en la
        lista y abajo en la pantalla del que la recibe.
        """
        stmt = self.base_query()
        if compra_id is not None:
            stmt = stmt.where(AdjuntoReventa.compra_id == compra_id)
        else:
            stmt = stmt.where(AdjuntoReventa.venta_id == venta_id)
        return list(self.db.scalars(stmt.order_by(AdjuntoReventa.created_at)).all())

    def contar_de(
        self, *, compra_id: uuid.UUID | None = None, venta_id: uuid.UUID | None = None
    ) -> int:
        """Cuántos soportes vigentes tiene el documento (para el tope por documento)."""
        stmt = self.base_query()
        if compra_id is not None:
            stmt = stmt.where(AdjuntoReventa.compra_id == compra_id)
        else:
            stmt = stmt.where(AdjuntoReventa.venta_id == venta_id)
        return int(self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


class TemporadaRepository(BaseRepository[Temporada]):
    model = Temporada
    search_fields = ("nombre",)
    default_order_by = "fecha_inicio"

    def vigentes(self) -> list[Temporada]:
        """Todas las temporadas, de la más reciente a la más vieja.

        Ese orden es a propósito: la lista se lee de arriba para abajo y lo que
        interesa primero es la temporada que está corriendo.
        """
        return list(
            self.db.execute(
                select(Temporada)
                .where(
                    Temporada.empresa_id == self.empresa_id,
                    Temporada.deleted_at.is_(None),
                )
                .order_by(Temporada.fecha_inicio.desc())
            ).scalars()
        )

    def abierta(self, excluir_id=None) -> Temporada | None:
        """La temporada sin fecha de cierre, si hay. Solo puede haber una."""
        criterios = [
            Temporada.empresa_id == self.empresa_id,
            Temporada.deleted_at.is_(None),
            Temporada.fecha_fin.is_(None),
        ]
        if excluir_id is not None:
            criterios.append(Temporada.id != excluir_id)
        return self.db.execute(select(Temporada).where(*criterios)).scalars().first()

    def solapada(self, inicio: date, fin: date | None, excluir_id=None) -> Temporada | None:
        """Otra temporada que se cruce con el rango dado, si hay.

        Dos rangos se cruzan cuando cada uno empieza antes de que termine el otro.
        Una temporada ABIERTA (fecha_fin NULL) se trata como que llega hasta el
        infinito: es la que está corriendo, así que cualquier cosa posterior a su
        inicio se le cruza. Se resuelve con COALESCE a una fecha tope en vez de
        comparar con NULL, porque en SQL cualquier comparación contra NULL da
        NULL —ni verdadero ni falso— y el solape pasaría de largo sin avisar.
        """
        tope = date(9999, 12, 31)
        fin_propio = fin or tope
        fin_otra = func.coalesce(Temporada.fecha_fin, tope)
        criterios = [
            Temporada.empresa_id == self.empresa_id,
            Temporada.deleted_at.is_(None),
            Temporada.fecha_inicio <= fin_propio,
            fin_otra >= inicio,
        ]
        if excluir_id is not None:
            criterios.append(Temporada.id != excluir_id)
        return (
            self.db.execute(
                select(Temporada).where(*criterios).order_by(Temporada.fecha_inicio)
            )
            .scalars()
            .first()
        )

    def ultimo_cierre(self) -> date | None:
        """Fecha de cierre más reciente: sirve para proponer el inicio de la
        siguiente temporada (el día después) y que no queden huecos ni solapes."""
        return self.db.scalar(
            select(func.max(Temporada.fecha_fin)).where(
                Temporada.empresa_id == self.empresa_id,
                Temporada.deleted_at.is_(None),
            )
        )

    def fechas_con_movimiento(self) -> set[date]:
        """Días en que hubo una compra o una venta de queso.

        Sirve para avisar de los HUECOS: si hay movimientos que no caen en
        ninguna temporada, la suma de las temporadas no da el total del negocio y
        el usuario tiene que saberlo. Es un set de fechas y no un conteo porque
        cuáles son huecos depende de las temporadas, que se cruzan en Python.

        No incluye conversiones ni saldos del libro anterior: una conversión sola
        no abre temporada (mueve queso que ya se compró) y los saldos anteriores
        son de otro sistema y no pertenecen a ninguna.
        """
        fechas: set[date] = set()
        for modelo in (CompraQueso, VentaQueso):
            fechas.update(
                self.db.execute(
                    select(modelo.fecha)
                    .where(
                        modelo.empresa_id == self.empresa_id,
                        modelo.deleted_at.is_(None),
                        modelo.estado != "anulada",
                    )
                    .distinct()
                ).scalars()
            )
        return fechas
