import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base

TIPO_PROVEEDOR = "proveedor"
TIPO_TRANSPORTADOR = "transportador"

# Flujo de estados de una liquidación (usa la columna estado del AuditMixin)
ESTADO_BORRADOR = "borrador"
ESTADO_APROBADA = "aprobada"
# Se le abonó algo pero todavía queda debiendo. Lo pidió el dueño con estas
# palabras: "el pagado no siempre es pagado definitivo; a un proveedor se le
# puede pagar y quedar debiendo otra parte". Se llama igual que en reventa
# (pendiente/parcial/pagada) para que el sistema se lea igual en todas partes.
ESTADO_PARCIAL = "parcial"
ESTADO_PAGADA = "pagada"
ESTADO_ANULADA = "anulada"


class Liquidacion(TenantMixin, AuditMixin, Base):
    __tablename__ = "liquidaciones"
    __table_args__ = (
        Index("ix_liquidacion_periodo", "empresa_id", "periodo_inicio", "periodo_fin"),
    )

    tipo: Mapped[str] = mapped_column(String(20), default=TIPO_PROVEEDOR)
    proveedor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proveedores.id"), index=True)
    transportador_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transportadores.id"), index=True
    )
    periodo_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    periodo_fin: Mapped[date] = mapped_column(Date, nullable=False)

    total_litros: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    precio_promedio: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    valor_bruto: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    bonificaciones: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    descuentos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    valor_transporte: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    anticipos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Lo que ya se le entregó al tercero, sumando todos los pagos parciales.
    # Se guarda como columna (en vez de sumar `pagos` cada vez) por lo mismo que
    # `abonado` en reventa: el tablero y la contabilidad suman esta cifra en SQL
    # sobre cientos de filas y no pueden cargar el historial de cada una.
    pagado: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )
    # Lo que TODAVÍA se le debe = (valor_total - anticipos) - pagado.
    #
    # OJO con el cambio de sentido: antes de los pagos parciales esta columna era
    # el "neto a pagar" y nunca se movía. Ahora baja con cada pago hasta llegar a
    # cero, que es como el dueño lee la palabra "saldo" ("¿cuánto le debo?") y lo
    # que hace que la cuenta cuadre exacta: neto_a_pagar = pagado + saldo.
    # Mientras no haya ningún pago vale lo mismo que antes, así que la lista, el
    # comprobante y las tarjetas del tablero siguen mostrando la misma cifra.
    saldo: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    proveedor = relationship("Proveedor", lazy="joined")
    transportador = relationship("Transportador", lazy="joined")
    # Se ordena por fecha Y RUTA porque desde que el comprobante del transportador
    # lleva un renglón por (día, ruta) un mismo día puede traer dos renglones, y sin
    # el segundo criterio el orden de esos dos quedaba a lo que devolviera la base:
    # el comprobante se veía distinto en dos impresiones seguidas del mismo
    # documento. Es por `ruta_id` y no por el nombre de la ruta porque el order_by
    # de una relación solo puede mirar columnas de su propia tabla; el nombre se usa
    # para ordenar al armar el PDF, que es donde una persona lo lee.
    detalles: Mapped[list["LiquidacionDetalle"]] = relationship(
        back_populates="liquidacion", lazy="selectin", cascade="all, delete-orphan",
        order_by="LiquidacionDetalle.fecha, LiquidacionDetalle.ruta_id",
    )
    # selectin y NO joined, igual que los abonos de reventa: con un LEFT JOIN de
    # por medio Postgres rechaza el SELECT ... FOR UPDATE con un 0A000, y ese
    # candado es justo lo que evita que dos pagos simultáneos se pisen.
    pagos: Mapped[list["PagoLiquidacion"]] = relationship(
        back_populates="liquidacion", lazy="selectin", cascade="all, delete-orphan",
        order_by="PagoLiquidacion.fecha",
    )

    @property
    def neto_a_pagar(self) -> Decimal:
        """Lo que hay que entregarle al tercero por esta quincena.

        Es la cifra grande contra la que se abona: el valor total menos los
        anticipos que ya se le habían adelantado. No se guarda porque se deduce
        de dos columnas que sí están, y dos fuentes para el mismo hecho terminan
        contradiciéndose.
        """
        return Decimal(self.valor_total or 0) - Decimal(self.anticipos or 0)

    @property
    def le_queda_debiendo(self) -> Decimal:
        """Cuánto le quedó debiendo EL TERCERO al negocio, en POSITIVO. Cero si nada.

        Es la vuelta al saldo cuando queda por debajo de cero, y pasa de verdad: los
        anticipos que se le entregaron al proveedor (o al transportador) suman más que
        lo que produjo la quincena. El caso medido: $180.000 de leche contra $300.000
        de anticipo ya entregado -> el saldo queda en -$120.000.

        POR QUÉ EXISTE ESTA CIFRA Y NO SE MUESTRA EL SALDO PELADO: "saldo -$4.955,77"
        es un número que el dueño tiene que interpretar al revés, y en un renglón que
        dice "SALDO A PAGAR" se lee como si hubiera que pagarlo. Con esta cifra la
        pantalla y el comprobante pueden decirlo como lo diría él —"Henri le queda
        debiendo $4.955,77"— sin que nadie tenga que voltearle el signo a mano ni
        repetir la resta en cada pantalla.

        Se deduce del saldo y no se guarda, por lo mismo que `neto_a_pagar`: dos
        fuentes para el mismo hecho terminan contradiciéndose. Y el saldo NO se
        recorta a cero en la columna: esconder la deuda del tercero es justo lo que
        costó plata dos veces (ver `_aplicar_anticipos_pendientes` en el servicio).
        """
        saldo = Decimal(self.saldo or 0)
        return -saldo if saldo < Decimal("0") else Decimal("0")

    @property
    def tiene_pagos(self) -> bool:
        """Si ya salió plata contra esta liquidación (aunque sea un abono).

        Es la pregunta que manda para trabar las recepciones del período: con un
        solo pago hecho, cambiar los litros deja ese pago descuadrado.
        """
        return Decimal(self.pagado or 0) > Decimal("0")


class LiquidacionDetalle(AuditMixin, Base):
    """Un renglón del comprobante: en la del proveedor un DÍA, en la del
    transportador un DÍA Y UNA RUTA.

    La diferencia nace de lo que pidió el dueño: "ahora el transportador puede
    tener varias rutas, por ejemplo este tuvo que hacer las dos... pero cada ruta
    puede tener un valor diferente de litro por leche". Si el renglón siguiera
    siendo solo el día, el día en que Alex hizo Nápoles a $242,76 y Mira Valle a
    $317,50 daría UN renglón con una sola tarifa, y ese renglón no cuadraría:
    litros × precio no sería el valor. El conductor suma la columna a mano.

    La invariante que este renglón tiene que cumplir SIEMPRE:
      · litros × precio_litro == valor, exacto al centavo;
      · la suma de los `valor` de los renglones == liquidacion.valor_transporte;
      · y ese total == la suma de los `recepciones_leche.valor_transporte` que
        entraron (las fotos que se guardaron el día que se recibió la leche).
    Quien la hace cumplir es `_renglones_de_transporte` en el servicio.
    """

    __tablename__ = "liquidacion_detalles"

    liquidacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("liquidaciones.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    # LA RUTA DEL RENGLÓN. Anulable, y por tres razones distintas que conviene no
    # confundir:
    #   · los renglones del comprobante del PROVEEDOR no tienen ruta y nunca la van
    #     a tener: ahí el renglón es el día de ese productor;
    #   · una recepción puede haber quedado sin ruta (el proveedor no la tenía), y
    #     entonces su flete sale de la tarifa general del transportador;
    #   · los comprobantes de flete que ya estaban impresos antes de este cambio
    #     traían un renglón por día sin ruta. Se les completa la ruta en la
    #     migración SOLO cuando ese día tenía una sola, que es el único caso en que
    #     se sabe sin adivinar; el resto se quedan en nulo y el PDF los muestra con
    #     un guion, que es la verdad de lo que ese papel decía.
    ruta_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rutas.id"))
    litros: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    precio_litro: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))

    liquidacion: Mapped[Liquidacion] = relationship(back_populates="detalles")
    # selectin y NUNCA joined, por lo mismo que se explica en el encabezado del
    # servicio: es una FK anulable, y un lazy="joined" sobre una FK anulable ya
    # rompió un `SELECT ... FOR UPDATE` con un 0A000 de Postgres. Como `detalles`
    # ya viene por selectin, esto son dos consultas aparte y ninguna le agrega un
    # JOIN a la consulta que toma el candado.
    ruta = relationship("Ruta", lazy="selectin")

    @property
    def ruta_nombre(self) -> str | None:
        """El nombre de la ruta del renglón, para el comprobante y la pantalla.

        Va en `LiquidacionDetalleRead`: es lo que le permite al conductor
        distinguir los dos renglones del día en que hizo las dos rutas. Sin
        nombre, el papel le mostraría dos líneas con la misma fecha y cifras
        distintas, que es exactamente lo que hace desconfiar de un recibo.
        """
        return self.ruta.nombre if self.ruta is not None else None


class PagoLiquidacion(AuditMixin, Base):
    """Un pago (abono) contra una liquidación aprobada.

    Copia el patrón de AbonoCompraQueso: fecha, valor y observaciones, colgado
    del documento por una FK con ondelete CASCADE y SIN empresa_id propio. La
    empresa la pone la liquidación padre, que es por donde se entra siempre; una
    segunda copia del tenant en el hijo es una fuente más que se puede
    desincronizar y que hay que acordarse de filtrar.

    No lleva medio de pago porque el "Pagar" que había tampoco lo llevaba: no
    mueve caja ni bancos, solo deja constancia de cuánto se entregó y cuándo.
    """

    __tablename__ = "pagos_liquidacion"

    liquidacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("liquidaciones.id", ondelete="CASCADE"), index=True
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    liquidacion: Mapped[Liquidacion] = relationship(back_populates="pagos")


class Anticipo(TenantMixin, AuditMixin, Base):
    """Anticipo a un proveedor, transportador o empleado. Se descuenta en su
    próxima liquidación (proveedor/transportador) o pago de nómina (empleado)."""

    __tablename__ = "anticipos"

    # Beneficiario: uno de los tres según 'tipo'
    tipo: Mapped[str] = mapped_column(
        String(20), default=TIPO_PROVEEDOR, server_default=TIPO_PROVEEDOR, index=True
    )
    proveedor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("proveedores.id"), index=True)
    transportador_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transportadores.id"), index=True
    )
    empleado_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("empleados.id"), index=True)

    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(String(300))

    # Marcas de aplicado: liquidación (proveedor/transportador) o nómina (empleado)
    liquidacion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("liquidaciones.id"), index=True
    )
    pago_empleado_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pagos_empleado.id"), index=True
    )

    proveedor = relationship("Proveedor", lazy="joined")
    transportador = relationship("Transportador", lazy="joined")
    empleado = relationship("Empleado", lazy="joined")

    @property
    def aplicado(self) -> bool:
        return self.liquidacion_id is not None or self.pago_empleado_id is not None

    @property
    def tercero_nombre(self) -> str | None:
        if self.tipo == TIPO_TRANSPORTADOR:
            return self.transportador.nombre if self.transportador else None
        if self.tipo == "empleado":
            return (
                f"{self.empleado.nombre} {self.empleado.apellido}".strip()
                if self.empleado
                else None
            )
        return self.proveedor.nombre if self.proveedor else None

    @property
    def proveedor_nombre(self) -> str | None:
        # Se expone en AnticipoRead. La relación carga el proveedor aunque esté
        # eliminado (soft delete), por lo que el nombre se conserva en el listado.
        return self.proveedor.nombre if self.proveedor else None
