import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, literal, select

from app.common.repository import BaseRepository
from app.modules.reventa.models import (
    DESTINO_BORONA,
    DESTINO_MERMA,
    AdjuntoReventa,
    CompraQueso,
    ConversionBorona,
    SaldoAnterior,
    Temporada,
    VentaQueso,
)

CERO = Decimal("0")


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
        fila = self.db.execute(
            select(
                func.coalesce(func.sum(CompraQueso.kilos_netos), 0),
                func.coalesce(func.sum(CompraQueso.valor_total), 0),
            ).where(
                CompraQueso.empresa_id == self.empresa_id,
                CompraQueso.deleted_at.is_(None),
                CompraQueso.estado != "anulada",
                CompraQueso.fecha.between(desde, hasta),
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
                )
                .order_by(CompraQueso.fecha, CompraQueso.created_at)
            ).all()
        )

    def acumulados(self) -> tuple[Decimal, Decimal, Decimal]:
        """(kilos netos históricos, borona de compras, saldo por pagar).

        El saldo va acotado en cero fila por fila (ver `saldo_pendiente`): una
        compra pagada de más no puede rebajar lo que se les debe a los demás.
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
    ) -> list[tuple[str, int, Decimal, Decimal, Decimal]]:
        """Compras del período agrupadas por productor:
        (productor, cuántas compras, kilos, valor comprado, saldo por pagar).
        Ordenadas por valor comprado de mayor a menor.

        Ojo: el valor comprado NO es lo que se le pagó (eso es `abonado`), es lo
        que valen sus compras. El saldo por pagar sí es HISTÓRICO (lo que se le
        debe hoy), para que cuadre con la tarjeta "Por pagar a productores".
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
        criterios = [
            VentaQueso.empresa_id == self.empresa_id,
            VentaQueso.deleted_at.is_(None),
            VentaQueso.estado != "anulada",
            VentaQueso.fecha.between(desde, hasta),
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

    def acumulados(self) -> tuple[Decimal, Decimal, Decimal]:
        """(kilos queso vendidos, kilos borona vendidos, saldo por cobrar).

        El saldo por cobrar va acotado en cero fila por fila (ver
        `saldo_pendiente`): es aquí donde más pasa: rebajarle el precio a una
        venta ya pagada deja esa venta con saldo negativo, y ese negativo restaba
        de la tarjeta "Por cobrar a clientes" como si los demás clientes debieran
        menos.
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
                )
                .order_by(VentaQueso.fecha, VentaQueso.created_at)
            ).all()
        )

    def gastos_periodo(self, desde: date, hasta: date, tipo: str | None = None) -> Decimal:
        """Suma de gastos de venta (transporte, etc.) del período. Con `tipo`
        solo cuenta las ventas de ese tipo ('queso' o 'borona')."""
        criterios = [
            VentaQueso.empresa_id == self.empresa_id,
            VentaQueso.deleted_at.is_(None),
            VentaQueso.estado != "anulada",
            VentaQueso.fecha.between(desde, hasta),
        ]
        if tipo:
            criterios.append(VentaQueso.tipo == tipo)
        total = self.db.scalar(
            select(func.coalesce(func.sum(VentaQueso.gasto_monto), 0)).where(*criterios)
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
                .order_by(ConversionBorona.fecha, ConversionBorona.created_at)
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
