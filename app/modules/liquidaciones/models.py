import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base
from app.modules.transportadores.models import MODO_DIA_FIJO, MODO_POR_LITRO

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
    # LO QUE EL TERCERO QUEDÓ DEBIENDO DE QUINCENAS PASADAS Y SE LE COBRA EN ESTA.
    #
    # Lo pidió el dueño con estas palabras: "en la liquidación, a los que quedaron en
    # negativo, ese saldo que se queda debiendo —el proveedor a la quesera— se cobre
    # en la siguiente liquidación". Hasta ahora la deuda se DECÍA (el rótulo "LE QUEDA
    # DEBIENDO" del comprobante) pero nada la cobraba: quedaba escrita en un papel y
    # se perdía.
    #
    # ES UN DESCUENTO DEL NETO, igual que los anticipos, y por la misma razón: los dos
    # son plata que ya salió de la caja y que no se le puede volver a entregar. La
    # diferencia es de dónde viene cada uno —el anticipo se le dio en la mano en ESTA
    # quincena; esto es el sobrante de anticipos de una quincena ANTERIOR— y por eso
    # van en renglones separados del comprobante: el dueño tiene que poder ver de
    # dónde salió cada resta.
    #
    # NO SE DEDUCE DE NADA, y de ahí que sea columna y no propiedad (al contrario de
    # `neto_a_pagar`): no sale de las recepciones ni de los anticipos de este período,
    # es una plata que se ARRASTRA de otro documento. Por eso tampoco se recalcula
    # cuando se recalcula la liquidación: ver `recalcular` en el servicio.
    saldo_anterior: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )
    # LA MARCA QUE HACE IMPOSIBLE COBRAR LA MISMA DEUDA DOS VECES.
    #
    # Va en la liquidación que DEJÓ la deuda y apunta a la que SE LA COBRÓ. Es el
    # mismo idioma que ya usa el proyecto para las recepciones y los anticipos (el
    # origen se marca con el id del documento que lo consumió: `liquidacion_id`,
    # `liquidacion_transporte_id`), y la razón es idéntica: mientras la marca esté
    # puesta, la búsqueda de deudas por cobrar no lo vuelve a encontrar.
    #
    # Anulable porque la enorme mayoría de las liquidaciones no dejan ninguna deuda.
    # Y se SUELTA (vuelve a nulo) cuando la que se la cobró se anula o se borra: si no,
    # el tercero quedaría debiendo una plata que ya nadie va a cobrar nunca.
    deuda_trasladada_a_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("liquidaciones.id"), index=True
    )

    # Lo que ya se le entregó al tercero, sumando todos los pagos parciales.
    # Se guarda como columna (en vez de sumar `pagos` cada vez) por lo mismo que
    # `abonado` en reventa: el tablero y la contabilidad suman esta cifra en SQL
    # sobre cientos de filas y no pueden cargar el historial de cada una.
    pagado: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0"
    )
    # Lo que TODAVÍA se le debe = neto_a_pagar - pagado, o sea
    # (valor_total - anticipos - saldo_anterior) - pagado.
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
    # LAS DOS PUNTAS DE LA DEUDA TRASLADADA, para que el dueño vea el hilo completo:
    # en la liquidación que dejó la deuda, EN CUÁL se le cobró; en la que la cobró, DE
    # DÓNDE vino ese descuento. Sin las dos puntas el comprobante muestra una resta
    # que nadie sabe explicar.
    #
    # `selectin` y NUNCA `joined`, por lo mismo que se explica en `_bloquear` del
    # servicio: son FK anulables y un LEFT JOIN de por medio hace que Postgres rechace
    # el `SELECT ... FOR UPDATE` con un 0A000, que es el candado que evita que dos
    # pagos simultáneos se pisen. Con selectin son consultas aparte, y en el listado
    # SQLAlchemy las resuelve en UNA sola por página (no una por fila).
    deuda_trasladada_a: Mapped["Liquidacion | None"] = relationship(
        "Liquidacion",
        remote_side="Liquidacion.id",
        foreign_keys="Liquidacion.deuda_trasladada_a_id",
        back_populates="deudas_cobradas",
        lazy="selectin",
    )
    # Las liquidaciones cuya deuda SE COBRÓ en esta. Ordenadas por período (y por id
    # para desempatar) porque el comprobante las imprime: dos impresiones del mismo
    # documento no pueden salir con los renglones en distinto orden.
    deudas_cobradas: Mapped[list["Liquidacion"]] = relationship(
        "Liquidacion",
        remote_side="Liquidacion.deuda_trasladada_a_id",
        foreign_keys="Liquidacion.deuda_trasladada_a_id",
        back_populates="deuda_trasladada_a",
        lazy="selectin",
        order_by="Liquidacion.periodo_inicio, Liquidacion.id",
    )
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
    # LA MEMORIA DEL PAPEL: cómo cobró este comprobante CADA RUTA. Ver
    # `LiquidacionRuta`, que es donde está escrito el porqué con las cifras.
    #
    # lazy="select" (carga diferida) y NO "selectin", que es lo que usan los
    # renglones y los pagos. La razón es que esto no lo lee ninguna pantalla: lo
    # lee UN camino, el que rearma el comprobante del flete. Con selectin, cada
    # lectura de CADA liquidación —incluidas las de proveedor, que nunca tienen
    # rutas— dispararía una consulta más en la lista, el detalle y el tablero, para
    # traer filas que nadie va a mirar. Y como no es "joined", tampoco le agrega un
    # LEFT JOIN a la consulta del candado (`SELECT ... FOR UPDATE`), que es lo que
    # Postgres rechaza con un 0A000; ni se carga sola mientras el candado está
    # puesto (ver `_bloquear` en el servicio).
    rutas_cobradas: Mapped[list["LiquidacionRuta"]] = relationship(
        back_populates="liquidacion", lazy="select", cascade="all, delete-orphan",
        order_by="LiquidacionRuta.ruta_id",
    )

    @property
    def neto_a_pagar(self) -> Decimal:
        """Lo que hay que entregarle al tercero por esta quincena.

        Es la cifra grande contra la que se abona:

            neto_a_pagar = valor_total − anticipos − saldo_anterior

        Las dos restas son plata QUE YA SALIÓ DE LA CAJA y que no se le puede volver a
        entregar: los anticipos que se le adelantaron en esta quincena, y el sobrante
        de los anticipos de una quincena anterior (`saldo_anterior`, lo que quedó
        debiendo). No se guarda porque se deduce de tres columnas que sí están, y dos
        fuentes para el mismo hecho terminan contradiciéndose.

        SI LA RESTA VUELVE A QUEDAR NEGATIVA, esta liquidación deja SU propio
        remanente para la siguiente, y ese remanente YA INCLUYE la deuda vieja (está
        restada acá arriba): por eso la cadena de quincenas no cobra dos veces lo
        mismo. Está medido en tests/test_liquidacion_saldo_anterior.py con tres
        quincenas seguidas en negativo.
        """
        return (
            Decimal(self.valor_total or 0)
            - Decimal(self.anticipos or 0)
            - Decimal(self.saldo_anterior or 0)
        )

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

    @property
    def periodo_texto(self) -> str:
        """'01/06/2026 al 15/06/2026' — el período como lo lee una persona.

        Vive en el modelo y no en cada pantalla porque lo usan tres cosas que tienen
        que decir lo mismo: el comprobante, la API y los mensajes de error que nombran
        a OTRA liquidación ("su deuda ya se le cobró en la del 16/06 al 30/06"). Con la
        fecha en formato colombiano: el dueño no lee 2026-06-01.
        """
        return (
            f"{self.periodo_inicio.strftime('%d/%m/%Y')} al "
            f"{self.periodo_fin.strftime('%d/%m/%Y')}"
        )

    @property
    def orden_para_volver_a_generar(self) -> str:
        """El consejo del ORDEN cuando hay que rehacer ESTA quincena y la que le cobró
        su deuda. Cadena vacía si no hay otra a la que nombrar.

        POR QUÉ HACE FALTA, con las cifras medidas. Cuando el dueño quiere corregirle un
        día a la quincena que quedó debiendo, el sistema le dice "anule primero esa
        liquidación y vuelva a intentarlo"... y no le dice EN QUÉ ORDEN volver a
        generarlas. Si las genera parado en la que acabó de anular —la NUEVA primero— el
        anticipo viejo se va a la quincena nueva (los anticipos pendientes se recogen por
        `fecha <= periodo_fin`, y el 01 de junio también es <= 30 de junio), la vieja
        queda sin anticipo y se le paga completa: de la caja salen $480.000 por $430.000
        de leche. Generándolas de la más vieja a la más nueva da exacto: $430.000.
        La plata no se pierde —lo de más queda registrado como deuda— pero sale de la
        caja una plata que ya se le había adelantado, y si el productor no vuelve a
        entregar leche no vuelve.
        Es un hueco de ORDEN, así que el arreglo es DECIR EL ORDEN, con las fechas
        concretas: "empiece por la del 01/06/2026 al 15/06/2026".

        ESTA es siempre la vieja y `deuda_trasladada_a` la nueva, sin necesidad de
        compararlas: una deuda solo viaja hacia adelante en el tiempo —el origen tiene
        que haber terminado antes de que la otra empiece (ver `deudas_sin_cobrar`)—.

        Vive en el modelo, al lado de `periodo_texto` y por lo mismo: lo usan TRES
        mensajes distintos que tienen que decir lo mismo (anular/recalcular una
        liquidación, corregir un día en Recepción diaria y borrar ese día). Una sola
        redacción para una sola regla; con tres copias, mañana dos de ellas dicen otra
        cosa.
        """
        otra = self.deuda_trasladada_a
        if otra is None:
            return ""
        return (
            "Y si le toca volver a generar las dos, empiece por la más vieja —la del "
            f"{self.periodo_texto}— y siga con la del {otra.periodo_texto}: al revés, el "
            "anticipo viejo se va a la quincena nueva y a la vieja se le paga completa"
        )

    @property
    def tiene_dias_fijos(self) -> bool:
        """Si este comprobante trae algún día cobrado POR DÍA COMPLETO (tarifa fija).

        Existe para que el `precio_promedio` no mienta. Ese promedio es informativo
        —"a cómo le salió el litro"— y con un día fijo mezclado no reproduce nada: en un
        comprobante de $150.000 por el día y $53.273,68 por otro día a $242,76 el litro,
        dividir el total entre los litros da una cifra que no es la tarifa de ningún
        renglón y que no se puede verificar con calculadora. Cuando esto es True el
        promedio se guarda en CERO y la pantalla, viendo esta bandera, escribe "—" en vez
        de "$0,00 el litro" (que sería la otra forma de mentir).

        Se DEDUCE de los renglones y no se guarda en una columna: dos fuentes para el
        mismo hecho terminan contradiciéndose, y los renglones son la verdad de este
        documento.
        """
        return any(
            (detalle.modo_transporte or MODO_POR_LITRO) == MODO_DIA_FIJO
            for detalle in self.detalles
            if detalle.deleted_at is None
        )

    @property
    def deuda_ya_cobrada(self) -> bool:
        """Si lo que esta liquidación dejó debiendo YA se le cobró en otra.

        Cuando es True, sus cifras están congeladas y no se pueden mover por ningún
        lado: cambiarle el valor total le cambiaría la deuda a un segundo comprobante
        que ya está emitido, y quedarían descuadrados los dos de una. Quien lo hace
        cumplir es `_exigir_deuda_no_trasladada` en el servicio, y el candado de
        Recepción diaria por el lado de los días.
        """
        return self.deuda_trasladada_a_id is not None


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
      · si se cobró POR LITRO: litros × precio_litro == valor, exacto al centavo;
        si se cobró POR DÍA FIJO: valor == el fijo de ese día en esa ruta, y los
        litros van AL LADO como información (multiplicarlos no reproduce nada);
      · la suma de los `valor` de los renglones == liquidacion.valor_transporte;
      · y ese total == la suma de los `recepciones_leche.valor_transporte` que
        entraron (las fotos que se guardaron el día que se recibió la leche).
    Quien la hace cumplir es `_reparto_del_flete` en el servicio.
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
    # CÓMO SE COBRÓ ESTE RENGLÓN: 'litro' o 'dia_fijo'. Se guarda en el renglón y no
    # se deduce, y hay tres razones para eso:
    #
    #   · un renglón fijo NO se puede reconocer mirando las cifras. "litros × precio no
    #     da el valor" también le pasa a una fila corregida a mano en la base, y las dos
    #     hay que tratarlas al revés: la fija se imprime tal cual, la corrupta se parte
    #     (ver `_renglones_de_ultimo_recurso`). Adivinar es cómo se imprime un
    #     comprobante que no cuadra;
    #   · el PAPEL tiene que decirlo. En un renglón fijo la columna Precio/L no lleva
    #     una tarifa —no existe— sino la palabra "Día completo", y eso es lo que le
    #     permite al conductor verificar la línea: "el día vale $150.000". Sin este
    #     campo, la única forma de escribirlo sería inventar una tarifa por litro;
    #   · UN COMPROBANTE VIEJO SIGUE SIGNIFICANDO LO MISMO PARA SIEMPRE. Si mañana el
    #     dueño le pone día fijo a esa ruta, los papeles ya emitidos guardan su 'litro'
    #     y se siguen leyendo (e imprimiendo) como el día que se firmaron.
    #
    # Por omisión 'litro': todos los renglones que ya existen se cobraron por litro,
    # así que la migración no le cambia el sentido a ninguno.
    modo_transporte: Mapped[str] = mapped_column(
        String(10), nullable=False, default=MODO_POR_LITRO, server_default=MODO_POR_LITRO
    )
    # POR QUÉ UN RENGLÓN DE DÍA FIJO VALE $0,00. Hay DOS razones y no son la misma:
    #
    #   · el día completo YA SE COBRÓ en OTRO comprobante (leche anotada tarde de un día
    #     que ya se liquidó y se pagó). Esta bandera queda en True y el papel escribe
    #     «Ya cobrado»: es un hecho sobre otro documento;
    #   · la TARIFA FIJA de esa ruta es de $0,00 —el dueño decidió no cobrar ese viaje—.
    #     La bandera queda en False y el papel escribe «Día completo», que es lo que es.
    #
    # SE GUARDA Y NO SE DEDUCE, por lo mismo que el modo, y con una razón más: mirando la
    # fila no hay forma de distinguirlas (las dos son un renglón fijo en cero), y
    # preguntárselo a la base al imprimir haría que EL PAPEL CAMBIARA SOLO —si mañana se
    # anula el otro comprobante, el mismo renglón dejaría de decir «Ya cobrado» sin que
    # nadie hubiera tocado este documento—. Guardado, dice para siempre lo que era cierto
    # el día que se emitió.
    #
    # Y decirlo mal no es un detalle de redacción: «Ya cobrado» le afirma al conductor, en
    # el papel que se le entrega, que ese día ya se le pagó. Sobre un fijo de $0,00 que
    # nunca se cobró, eso es falso.
    #
    # Por omisión False: un renglón por litro nunca la usa, y ninguno de los que ya
    # existen se emitió por un día ya cobrado.
    dia_fijo_ya_cobrado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

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

    @property
    def es_dia_fijo(self) -> bool:
        """Si este renglón se cobró por DÍA COMPLETO y no por litro.

        La pregunta la hacen tres sitios que tienen que contestarla igual: el PDF (para
        escribir "Día completo" en vez de una tarifa), la pantalla (por
        `LiquidacionDetalleRead.modo_transporte`) y el propio recálculo. El `or` cubre la
        fila vieja cuya columna todavía viene en nulo si alguien la insertó saltándose el
        server_default.
        """
        return (self.modo_transporte or MODO_POR_LITRO) == MODO_DIA_FIJO


class LiquidacionRuta(Base):
    """CÓMO COBRÓ ESTE COMPROBANTE UNA RUTA: el modo, y la cifra o la tarifa.

    ES LA MEMORIA DEL PAPEL, y existe por una sola razón: que ningún camino pueda
    BORRARLE a un comprobante emitido la forma en que se emitió.

    EL DEFECTO QUE CIERRA, con las cifras medidas. Un comprobante de flete sabe cómo
    cobró cada ruta —por litro a $242,76, o el día completo a $150.000— y eso se
    DEDUCÍA de los renglones que le quedaran (`_ComoCobroLaRuta` en el servicio).
    Mientras al papel le quedara un renglón de esa ruta, la deducción acertaba. Pero
    cuando el comprobante se quedaba SIN NINGÚN RENGLÓN de esa ruta, la deducción se
    quedaba sin de dónde deducir y el siguiente recuadre re-precificaba con la tarifa
    de HOY. Dos caminos lo lograban, y NINGUNO de los dos oprime Recalcular:

      · apagar el día y volver a prenderlo;
      · corregirle la ruta al día y devolvérsela.

    El salto medido, sobre un día de 82,00 L en la ruta "A fábrica": el comprobante
    emitido por DÍA COMPLETO en $150.000,00 amanecía en $19.906,32 (82 L × $242,76)
    —o al revés, el emitido POR LITRO en $19.906,32 amanecía en $150.000,00—. Son
    $130.093,68 que se le pagan de menos o de más al conductor, y el PDF cambiaba de
    «Día completo $150.000» a «82 L × $242,76 = $19.906,32» sin que nadie hubiera
    pedido re-precificar nada. El comprobante se caía a borrador, lo cual MITIGA pero
    NO CIERRA: se vuelve a aprobar y se paga la cifra nueva.

    LA CAUSA, que es la de siempre en este proyecto: SE DEDUCÍA UN HECHO DE LO QUE
    SOBREVIVE en vez de GUARDARLO CUANDO SE SABE. El comprobante sabe cómo cobró cada
    ruta en el momento en que se emite; escrito, ninguna puerta se lo puede borrar.

    QUÉ GUARDA CADA FILA, y por qué son dos columnas de plata y no una:

      · `modo_transporte` — 'litro' o 'dia_fijo'. Sin él, las otras dos cifras no se
        pueden leer: $150.000 leídos por litro son millones en una quincena;
      · `precio_litro` — LA TARIFA con que se emitió, y solo aplica POR LITRO. Ahí lo
        que se conserva es la tarifa, no la cifra: si mañana se le corrige un litro a
        un proveedor, el renglón TIENE que moverse (es lo que se le paga al conductor
        por litro recogido), pero a la tarifa con que se firmó el papel;
      · `valor_dia_fijo` — LA CIFRA con que se emitió, y solo aplica en DÍA FIJO. Ahí
        se conserva la cifra: el viaje vale lo que costó y corregirle los litros a un
        proveedor no lo cambia.

    Las dos son ANULABLES porque cada modo usa una sola, y porque hay papeles de los
    que no se puede afirmar ninguna: una ruta cuyos renglones quedaron a dos tarifas
    distintas (líneas partidas de un flete ya pagado) no tiene "la tarifa con que se
    emitió", y un nulo ahí dice exactamente eso —no hay nada que heredar— en vez de
    inventar un promedio.

    `ruta_id` ES ANULABLE, y esa fila nula es la de la TARIFA GENERAL: la recepción
    que quedó sin ruta cobra la tarifa general del transportador, y su día también se
    puede apagar y prender. Sin la fila nula, el día sin ruta se quedaba con la puerta
    abierta.

    CUÁNDO SE ESCRIBE, que es toda la regla y son tres frases:

      · SOLO SE ESCRIBE CUANDO EL PAPEL SE EMITE O SE RECALCULA A PROPÓSITO —generar y
        Recalcular, que es un botón que el dueño oprime—, y ahí se REEMPLAZA COMPLETA.
        Después de esos dos caminos el comprobante cobra lo de hoy y su memoria dice lo
        de hoy;
      · EL RECUADRE AUTOMÁTICO (la cascada que se dispara al corregir una recepción o
        un anticipo) NO LA TOCA. NUNCA. Ni una ruta, ni una fila, ni para
        "actualizarla" desde un renglón. Un papel emitido recuerda lo que decía cuando
        se emitió, y punto;
      · AL ESCRIBIRLA se escriben SOLO LAS RUTAS QUE ESE PAPEL DE VERDAD COBRA: una
        ruta de paso no deja fila, y al reemplazar completa desaparecen las filas de
        rutas que ya no cobra.

    POR QUÉ ASÍ Y NO "EL RECUADRE LA REFRESCA SIN BORRAR NADA", que fue el primer
    intento y se cayó en dos sitios, los dos por la misma raíz —la memoria se seguía
    RE-ESCRIBIENDO desde los renglones que sobreviven, cuando tiene que ser inmutable—:

      · EL RENGLÓN «YA COBRADO» EN $0,00 LE BORRABA EL FIJO. El 16/07 es un día fijo ya
        pagado en otro comprobante, así que la leche anotada tarde de ese mismo día
        entra en $0,00 (lo correcto); el 17/07 vale $150.000. El papel se emite en
        $150.000,00 con memoria ('dia_fijo', $150.000,00). Se apaga el 17/07: al papel
        le queda SOLO el renglón de $0,00 —que no dice cuánto cuesta el viaje— y el
        recuadre "refrescaba" la memoria desde él, dejándola en ('dia_fijo', None). Al
        prender otra vez mandaba la tarifa de hoy: por litro un fijo vale CERO y el
        papel quedaba en $0,00. SALTO DE −$150.000,00 (y sin cruzar el modo también, con
        el fijo renegociado a $200.000: +$50.000). 13 de 13 secuencias reversibles daban
        el mismo salto; el PDF pasaba de «Día completo $150.000» a «Día completo $0» y
        «SALDO A PAGAR $0», el papel aprobado se caía a borrador, se volvía a aprobar y
        se PAGABA en $0,00 con 233,75 L de leche viva. Y el desglose seguía cuadrando,
        así que ninguna red de cuadre lo veía;
      · LA FILA FANTASMA DE LA RUTA DE PASO. Un papel que cobra solo «A fábrica». Se le
        manda un día a Nápoles un momento y se le devuelve: la fila de memoria de
        Nápoles QUEDABA escrita aunque el papel ya no cobrara ni un peso de Nápoles.
        Después el dueño renegociaba Nápoles a día fijo $150.000 y el día que volvía
        cobraba $19.906,32 (la fantasma: 82,00 L × $242,76) en vez de $150.000,00,
        −$130.093,68. Al revés, +$130.093,68.

    Con las tres frases de arriba ninguno de los dos puede pasar: la memoria no se
    re-escribe desde un renglón de $0,00 —el recuadre no la escribe—, y no queda fila de
    una ruta que el papel no cobra.

    NO LLEVA `empresa_id` NI SOFT DELETE, las dos cosas a propósito y por lo mismo que
    `TransportadorRuta`:

    · el aislamiento por empresa lo da la LIQUIDACIÓN, que sí es multi-tenant y es el
      único camino por el que se llega a estas filas (se leen y se escriben desde
      `liquidacion.rutas_cobradas`). Repetir `empresa_id` acá sería un dato más que se
      puede desincronizar del padre;
    · el soft delete pelearía con el único de (liquidacion_id, ruta_id): al volver a
      cobrar una ruta que se había dejado de cobrar, la fila borrada en suave seguiría
      ocupando la pareja y el índice reventaría. La lista se maneja con
      delete-orphan, o sea borrado de verdad, y el rastro queda en la auditoría de la
      liquidación.

    OJO CON EL ÚNICO Y EL NULO: Postgres deja repetir filas cuyo `ruta_id` es NULL, así
    que el único NO protege la fila de la tarifa general. Quien la protege es el código
    que las escribe (`_guardar_como_cobro_cada_ruta` en el servicio), que trae la
    colección completa y la indexa por `ruta_id`: una ruta que ya tiene fila se
    ACTUALIZA, nunca se agrega de nuevo. El único se queda igual porque sí protege a
    todas las demás, que son la enorme mayoría.

    NO SALE EN NINGUNA PANTALLA NI EN EL PDF, y tampoco hace falta: lo que el dueño y
    el conductor leen son los RENGLONES, que ya dicen el modo y la tarifa de cada día.
    Esto es la nota que el comprobante se guarda para poder rehacer un día que vuelve.
    """

    __tablename__ = "liquidacion_rutas"
    __table_args__ = (
        # Una ruta no puede aparecer dos veces en el mismo comprobante: si apareciera
        # con dos modos, no habría manera de saber cómo se cobró. Ver el párrafo del
        # nulo arriba.
        UniqueConstraint("liquidacion_id", "ruta_id", name="uq_liquidacion_ruta"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    liquidacion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("liquidaciones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ruta_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rutas.id"))
    modo_transporte: Mapped[str] = mapped_column(
        String(10), nullable=False, default=MODO_POR_LITRO, server_default=MODO_POR_LITRO
    )
    # Numeric(12, 2) igual que las tarifas de las que salen: el dueño tiene una ruta a
    # $242,76 el litro y los centavos no se pueden perder, y le caben de sobra los
    # $150.000 de un día fijo.
    precio_litro: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    valor_dia_fijo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    liquidacion: Mapped[Liquidacion] = relationship(back_populates="rutas_cobradas")


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
    destinatario: Mapped[str | None] = mapped_column(String(150))
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
