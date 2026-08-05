import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import lazyload

from app.common.repository import BaseRepository
from app.modules.liquidaciones.models import (
    ESTADO_ANULADA,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    TIPO_PROVEEDOR,
    Anticipo,
    Liquidacion,
)


class LiquidacionRepository(BaseRepository[Liquidacion]):
    model = Liquidacion
    default_order_by = "periodo_inicio"

    def deudas_sin_cobrar(
        self,
        tipo: str,
        tercero_id: uuid.UUID,
        *,
        antes_de: date,
        excepto: uuid.UUID | None = None,
    ) -> list[Liquidacion]:
        """Las liquidaciones de ESE tercero que dejaron una deuda que nadie ha cobrado.

        Es la lista que la liquidación nueva se va a cobrar en su renglón
        `saldo_anterior`. Cada filtro está por una razón concreta:

        · `tipo` Y el id del tercero: la deuda de un PROVEEDOR no se le cobra a un
          TRANSPORTADOR aunque sean la misma persona (son dos cuentas distintas, con
          dos comprobantes distintos), y va POR ID y nunca por nombre: Henri y Henri C
          son dos productores, y cobrarle a uno lo del otro es plata mal cobrada.
        · `saldo < 0`: solo debe quien quedó por debajo de cero. El saldo en cero no
          es una deuda (ver `le_queda_debiendo`).
        · `deuda_trasladada_a_id IS NULL`: LA MARCA QUE EVITA COBRAR DOS VECES. En
          cuanto una liquidación se la cobra, el origen queda marcado y esta consulta
          no lo vuelve a ver.
        · `periodo_fin < antes_de`: LA DEUDA SOLO VIAJA HACIA ADELANTE EN EL TIEMPO, y
          esto faltaba. Sin este filtro, generar primero la quincena del 16 al 30 y
          después la del 01 al 15 hacía que el comprobante VIEJO cobrara "lo que quedó
          debiendo de la quincena pasada" de una quincena que todavía no había
          empezado, y lo decía por escrito con el período de la otra. `antes_de` es el
          inicio del período de la que está cobrando: el origen tiene que haber
          TERMINADO antes de que la nueva empiece, así que dos quincenas que se pisan
          —o la misma dos veces— no se prestan deuda.

          Y ESTE FILTRO SE QUEDA COMO ESTÁ, que fue una decisión y no un olvido. Por acá
          se escapaba plata: una quincena que se PISA con la que dejó debiendo no le
          encontraba la deuda ($120.000 medidos, saliendo de la caja otra vez). La
          tentación es relajarlo —dejar entrar al origen que EMPIEZA antes que esta,
          aunque termine después— y no se hizo, por tres razones:
            · el hueco se cierra EN LA PUERTA: desde `_exigir_periodo_sin_cruce` ya no se
              puede GENERAR una liquidación cuyo período se pise con otra viva del mismo
              tercero, así que el caso que este filtro dejaba pasar ya no existe;
            · el comprobante imprime "lo que quedó debiendo DE LA QUINCENA PASADA", y
              nombrar ahí una quincena que empieza el mismo día que esta es una frase que
              el dueño no puede verificar leyendo las fechas. "Terminó antes de que esta
              empezara" sí la puede verificar;
            · y relajarlo tocaría los datos que YA ESTÁN en producción: si por lo viejo
              quedaron dos períodos montados, empezarían a prestarse deuda de un día para
              otro y cambiarían cifras de comprobantes ya impresos. Con el filtro estricto
              esa deuda no se pierde —sigue libre y se la cobra la primera quincena que
              empiece después de que el origen termine—: llega tarde, no se pierde.
        · ESTADO: entra CUALQUIERA menos 'anulada' (y las borradas en suave, que las
          descarta `base_query`, igual que el filtro de empresa). BORRADOR INCLUIDO.

        POR QUÉ VIAJA LA DEUDA DE UN BORRADOR —esto se decidió al revés primero y
        estaba mal—. Exigir 'aprobada' o 'pagada' PERDÍA LA DEUDA, medido con las
        cifras del dueño: Henri C queda debiendo $120.000 en la quincena 1 y se
        aprueba; alguien le escribe una OBSERVACIÓN a un día de esa quincena —un campo
        que no mueve un peso y que a propósito quedó editable—, el recuadre la devuelve
        a 'borrador', y la quincena 2 sale con $0 de deuda arrastrada y neto $250.000.
        Se paga y de la caja salen $550.000 por $430.000 de leche: $120.000 de más. El
        mismo hueco por el flujo que el propio mensaje de error recomienda (anular las
        dos y regenerar: la regenerada queda en borrador debiendo, y la siguiente le
        cobra $0).

        LA DEUDA ES PLATA QUE EXISTE DE VERDAD —el anticipo ya salió de la caja, en la
        mano— y eso no depende de si el dueño alcanzó a revisar las cifras: el estado
        dice si aprobó LAS CIFRAS, no si la deuda existe. Lo que hace segura la
        cobranza no es exigir aprobación, son las otras dos cosas, que ya están: al
        cobrarla el origen queda CONGELADO (`deuda_trasladada_a_id`, y de ahí en
        adelante recalcular, anular, editarle un día o su anticipo rebotan nombrando la
        otra), y si el dueño se equivocó tiene salida (anula la que la cobró y la deuda
        vuelve a quedar libre). Y 'anulada' sí queda por fuera porque una liquidación
        anulada no dice nada: no es un documento, es un documento tachado.

        `excepto` es la liquidación que se está generando: no puede cobrarse su propia
        deuda a sí misma, o el neto quedaría al doble. El filtro de fechas ya lo tapa
        —ningún período termina antes de empezar él mismo—, pero se conserva porque es
        el que dice la intención en una línea y no depende de cómo queden las fechas.

        Ordenadas por período: es el orden en que se imprimen en el comprobante y en
        que quedan en la bitácora, y tiene que ser el mismo en cada corrida.

        VA CON CANDADO (`FOR UPDATE`) porque lo que sigue a esta consulta es marcar
        estas filas, y sin él dos "Generar" a la vez le cobran la MISMA deuda a dos
        liquidaciones: las dos leen `deuda_trasladada_a_id` en nulo, las dos suman
        $120.000 y al proveedor se le descuenta dos veces (la marca queda apuntando a
        una sola, así que después no hay ni rastro de la otra). Con el candado, la
        segunda espera y al soltarse Postgres vuelve a evaluar el WHERE: ya no cumple
        `IS NULL` y esa deuda no vuelve a salir.

        Los `lazyload` son obligatorios y no un adorno, y esto ya costó caro en este
        módulo (ver `_bloquear` en el servicio): `proveedor` y `transportador` son
        lazy="joined" sobre FK anulables, y Postgres RECHAZA un `SELECT ... FOR UPDATE`
        que traiga un LEFT JOIN, con un 0A000. Las dos puntas de la deuda también se
        apagan: son selectin —no meten JOIN— pero dispararían consultas de más
        mientras se tiene el candado puesto, y un candado se suelta rápido o no sirve.
        SQLite descarta el FOR UPDATE en silencio, así que la suite no delata nada de
        esto: la corrección se sostiene por lectura del código.
        """
        stmt = (
            self.base_query()
            .where(*self._solo_las_que_deben(tipo, tercero_id, antes_de))
            .order_by(Liquidacion.periodo_inicio, Liquidacion.id)
            .options(
                lazyload(Liquidacion.proveedor),
                lazyload(Liquidacion.transportador),
                lazyload(Liquidacion.deuda_trasladada_a),
                lazyload(Liquidacion.deudas_cobradas),
            )
            .with_for_update()
        )
        if excepto is not None:
            stmt = stmt.where(Liquidacion.id != excepto)
        return list(self.db.scalars(stmt).all())

    @staticmethod
    def _solo_las_que_deben(tipo: str, tercero_id: uuid.UUID, antes_de: date) -> list[Any]:
        """Los filtros de "este tercero quedó debiendo y nadie se lo ha cobrado".

        Están en un solo sitio porque los usan DOS consultas que tienen que mirar
        exactamente el mismo universo: la que cobra la deuda (`deudas_sin_cobrar`, con
        candado, al generar) y la que solo la SUMA para avisar (`deuda_pendiente_de`,
        sin candado, para el papel del avance). Si una mirara un filtro distinto, el
        avance le prometería al proveedor una cifra y el comprobante le cobraría otra.
        El porqué de cada filtro está en `deudas_sin_cobrar`.
        """
        campo = (
            Liquidacion.proveedor_id if tipo == TIPO_PROVEEDOR else Liquidacion.transportador_id
        )
        return [
            Liquidacion.tipo == tipo,
            campo == tercero_id,
            Liquidacion.saldo < Decimal("0"),
            Liquidacion.deuda_trasladada_a_id.is_(None),
            Liquidacion.estado != ESTADO_ANULADA,
            Liquidacion.periodo_fin < antes_de,
        ]

    def deuda_pendiente_de(self, tipo: str, tercero_id: uuid.UUID, antes_de: date) -> Decimal:
        """Cuánto debe ese tercero de quincenas anteriores, en POSITIVO. Solo para MIRAR.

        Es la misma pregunta que `deudas_sin_cobrar` PERO SIN CANDADO: la usa el PDF del
        avance —un papel informativo que no cobra nada— para poder advertir que su
        "SALDO ESTIMADO" todavía no tiene esta resta adentro. Va sin `FOR UPDATE` a
        propósito: poner candado sobre filas de plata para imprimir un papel que no las
        toca es dejar esperando al que sí las va a cobrar.

        La suma se hace en Python y no en SQL para poder pasar por `base_query`, que es la
        que pone el filtro por empresa y el de borrados —y rebota si falta el contexto de
        empresa—: son dos o tres filas en el peor caso (un tercero no acumula deudas sin
        cobrar), y el filtro que no se puede olvidar vale más que la consulta corta.
        """
        stmt = self.base_query().where(*self._solo_las_que_deben(tipo, tercero_id, antes_de))
        return sum(
            (-Decimal(liq.saldo) for liq in self.db.scalars(stmt).all()), Decimal("0")
        )

    def cobradas_por(self, liquidacion_id: uuid.UUID) -> list[Liquidacion]:
        """Las liquidaciones cuya deuda se cobró en esta. Para SOLTARLAS.

        Va por consulta y no por la relación `deudas_cobradas` del modelo a propósito:
        esto se usa al anular o borrar, o sea justo cuando hay escrituras a medio
        camino en la sesión, y una colección cargada antes puede venir vieja. Además
        pasa por `base_query`, así que el filtro por empresa y el de borrados no se
        pueden olvidar.
        """
        stmt = self.base_query().where(Liquidacion.deuda_trasladada_a_id == liquidacion_id)
        return list(self.db.scalars(stmt).all())

    def solapada_para_periodo(
        self, tipo: str, tercero_id: uuid.UUID, inicio: date, fin: date
    ) -> Liquidacion | None:
        """La liquidación del MISMO tercero y MISMO tipo cuyo período se pisa con este
        rango, si hay alguna. Devuelve el documento y no un sí/no.

        Antes esto era `existe_para_periodo`, devolvía un booleano y NO LO LLAMABA NADIE:
        era código muerto. Se cableó al generar (ver `_exigir_periodo_sin_cruce` en el
        servicio) y por el camino hubo que corregirle tres cosas:

        · DEVUELVE LA LIQUIDACIÓN, no un booleano. El mensaje que ve el dueño tiene que
          decirle CUÁL es y de qué período —"Henri C ya tiene una liquidación de leche
          del 01/06/2026 al 15/06/2026, que se cruza con estas fechas"—, y con un sí/no
          lo único que se puede escribir es "no se puede", que lo deja atascado.
        · El tipo y el estado iban con la cadena pelada ('proveedor', 'anulada') en vez
          de las constantes del módulo: el día que una de las dos cambie de texto, este
          filtro deja de encontrar nada y el guardia se apaga en silencio.
        · Y EL FILTRO DE ESTADOS ERA DEMASIADO ANCHO. Ver abajo, que es plata.

        QUÉ SE CONSIDERA "SE PISA": dos rangos se cruzan cuando cada uno empieza antes de
        que termine el otro (`periodo_inicio <= fin AND periodo_fin >= inicio`), la misma
        cuenta que usan las temporadas de reventa y los ciclos de despacho. Incluye el
        caso de la MISMA quincena dos veces, que es el cruce más fuerte que hay.

        SOLO ESTORBA LA QUE TODAVÍA PUEDE QUEDAR DEBIENDO Y CUYA DEUDA NADIE HA COBRADO,
        que es exactamente el universo de `_solo_las_que_deben` sin el `saldo < 0`: una en
        borrador con el saldo a favor todavía puede voltearse (entra un anticipo y queda
        debiendo), así que también reserva sus fechas. Y las tres que se dejan pasar, cada
        una por su razón, que en las tres es la misma en el fondo —NO PUEDEN SER EL ORIGEN
        DE UNA DEUDA SIN COBRAR, así que cruzarse con ellas no cuesta un peso—:

        · ANULADA: no es un documento, es un documento tachado. Anular y volver a
          generar es EL flujo de corrección del sistema, y si la anulada estorbara no se
          podría corregir nada.
        · PAGADA y PARCIAL (o cualquiera con `pagado > 0`) CUYO SALDO NO ESTÁ EN NEGATIVO:
          ahí ya salió plata contra esas cifras, y por eso no pueden estar debiendo nada
          —`pagar` y `registrar_pago` rebotan cuando el tercero quedó debiendo, así que una
          liquidación con pagos NACIDA HOY tiene el saldo en cero o a favor del tercero—.
          El "cuyo saldo no está en negativo" es la corrección de un punto ciego que caía
          justo sobre los datos del cliente; ver abajo.
        · LA QUE YA TIENE SU DEUDA COBRADA EN OTRA (`deuda_trasladada_a_id`): esa deuda ya
          está restada en un segundo comprobante y `deudas_sin_cobrar` no la vuelve a
          mirar. No hay nada que perder por ese lado.

        EL PUNTO CIEGO QUE HABÍA, Y QUE CAÍA SOBRE LA BASE DEL CLIENTE. Dejar pasar TODA
        'pagada' se razonaba con que "una pagada no puede estar debiendo nada". Eso es
        cierto DE AHORA EN ADELANTE, y es falso para las filas que YA EXISTEN: la propia
        migración `e5c2b9a1f7d3` lo dice —«Tampoco se les toca el estado a las que el botón
        "Pagar" de antes dejó en 'pagada' con saldo negativo: esa deuda sigue contando y se
        cobra igual»—. O sea que en producción hay 'pagada' con saldo NEGATIVO, son orígenes
        de deuda válidos, y sobre ellas el hueco original seguía abierto con sus mismas
        cifras: se generaba el período montado, esa liquidación no le cobraba la deuda
        (solo viaja hacia adelante) y de la caja salían $500.000 por $380.000 de leche.

        POR ESO LA CONDICIÓN NO ES "SIN PLATA ENTREGADA" SINO CUALQUIERA DE LAS DOS:
          · o todavía no ha salido plata contra ella (y entonces todavía puede quedar
            debiendo: entra un anticipo y se voltea, aunque hoy esté a favor del tercero);
          · o YA TIENE EL SALDO POR DEBAJO DE CERO, sin importar el estado ni los abonos:
            eso no es una posibilidad, es una deuda que está ahí escrita.
        Y las dos ramas siguen pasando por `deuda_trasladada_a_id IS NULL`, que es el filtro
        que de verdad manda: en cuanto alguien se cobra esa deuda, la fila deja de reservar
        sus fechas. O sea que LA RESERVA DE LA FILA VIEJA ES TEMPORAL y tiene salida sin
        tocar la base a mano: se le genera la quincena SIGUIENTE —que se cobra la deuda y le
        pone la marca— y desde ese momento el período montado vuelve a poder generarse y el
        día anotado tarde entra. La leche no se queda sin camino.

        LO QUE ESTO NO CAMBIÓ, y es la parte que estaba bien razonada y medida (pruebas 30d
        y 30e): la PAGADA NORMAL —saldo en cero o a favor del tercero— y la que YA TIENE SU
        DEUDA COBRADA siguen sin estorbar. Si estorbaran, el día que se anota tarde dentro
        de ese período no tendría por dónde entrar NUNCA —ninguna de las dos se puede
        anular— y esa leche no se le pagaría jamás al productor.

        Y LAS DOS ÚLTIMAS HAY QUE DEJARLAS PASAR O SE PIERDE LECHE, que es la parte que
        costó descubrir: NINGUNA DE LAS DOS SE PUEDE ANULAR —de una pagada no se sale, y la
        que tiene la deuda cobrada rebota pidiendo que se anule primero la que se la
        cobró... que puede estar PAGADA—. Si reservaran sus fechas, el día que se anota
        tarde dentro de ese período NO TENDRÍA POR DÓNDE ENTRAR NUNCA: ni generando (porque
        cualquier rango que lo contenga se cruza), ni anulando (porque no se deja). El caso
        completo, que es el flujo normal de esta función: Henri queda debiendo $120.000 en
        la quincena del 01 al 15, la del 16 al 30 se los cobra y se paga; aparece un día
        olvidado del 05 de junio y esos $54.000 de leche no se los podría cobrar jamás.
        Hoy ese día entra en un segundo comprobante del mismo período y se le paga: eso se
        conserva, y en el peor de los casos lo que hay son dos comprobantes de períodos
        montados donde ya no hay ninguna deuda que se pueda perder.

        Ordenada por período (y por id para desempatar) porque el mensaje nombra a la
        primera que salga: dos corridas iguales tienen que culpar a la misma.

        `base_query` pone el filtro por empresa y el de borrados, que en este proyecto no
        se pueden olvidar: el cruce es POR EMPRESA —dos queseras pueden liquidar el mismo
        período— y una liquidación borrada en suave no reserva nada.
        """
        campo = (
            Liquidacion.proveedor_id if tipo == TIPO_PROVEEDOR else Liquidacion.transportador_id
        )
        stmt = (
            self.base_query()
            .where(
                Liquidacion.tipo == tipo,
                campo == tercero_id,
                # cada rango empieza antes de que termine el otro = se pisan
                Liquidacion.periodo_inicio <= fin,
                Liquidacion.periodo_fin >= inicio,
                # solo estorba la que todavía puede quedar debiendo, o la que YA está
                # debiendo, y en las dos la deuda tiene que estar sin cobrar: sin tachar
                # y sin la marca
                Liquidacion.estado != ESTADO_ANULADA,
                Liquidacion.deuda_trasladada_a_id.is_(None),
                or_(
                    # todavía no ha salido plata contra ella: todavía se puede voltear
                    and_(
                        Liquidacion.estado.not_in((ESTADO_PAGADA, ESTADO_PARCIAL)),
                        Liquidacion.pagado <= Decimal("0"),
                    ),
                    # o ya está debiendo, y entonces no importa el estado ni los abonos:
                    # es la fila vieja del cliente ('pagada' con saldo negativo)
                    Liquidacion.saldo < Decimal("0"),
                ),
            )
            .order_by(Liquidacion.periodo_inicio, Liquidacion.id)
        )
        return self.db.scalars(stmt).first()


class AnticipoRepository(BaseRepository[Anticipo]):
    model = Anticipo
    default_order_by = "fecha"

    def pendientes_de(self, proveedor_id: uuid.UUID, hasta: date) -> list[Anticipo]:
        stmt = self.base_query().where(
            Anticipo.proveedor_id == proveedor_id,
            Anticipo.liquidacion_id.is_(None),
            Anticipo.fecha <= hasta,
        )
        return list(self.db.scalars(stmt).all())

    def pendientes_transportador(self, transportador_id: uuid.UUID, hasta: date) -> list[Anticipo]:
        stmt = self.base_query().where(
            Anticipo.transportador_id == transportador_id,
            Anticipo.liquidacion_id.is_(None),
            Anticipo.fecha <= hasta,
        )
        return list(self.db.scalars(stmt).all())

    def pendientes_empleado(self, empleado_id: uuid.UUID, hasta: date) -> list[Anticipo]:
        stmt = self.base_query().where(
            Anticipo.empleado_id == empleado_id,
            Anticipo.pago_empleado_id.is_(None),
            Anticipo.fecha <= hasta,
        )
        return list(self.db.scalars(stmt).all())
