from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, literal, select

from app.common.repository import BaseRepository
from app.modules.reventa.models import (
    DESTINO_BORONA,
    DESTINO_MERMA,
    CompraQueso,
    ConversionBorona,
    SaldoAnterior,
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

    # Los tres siguientes son HISTÓRICOS (sin filtro de fechas): sirven para el
    # inventario disponible hoy. NO usarlos en cálculos de un período: para eso
    # está totales_periodo, o se mezclan kilos de temporadas distintas.
    def total_convertido(self) -> Decimal:
        """Todo lo que sale del queso disponible (borona + merma), histórico."""
        return self._total()

    def total_a_borona(self) -> Decimal:
        """Solo lo que se pasó a borona (suma al inventario de borona), histórico."""
        return self._total(DESTINO_BORONA)
