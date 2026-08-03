import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, TenantRead


class TipoQuesoCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=100)
    descripcion: str | None = None
    precio_referencia: Decimal = Field(default=Decimal("0"), ge=0)


class TipoQuesoUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=100)
    descripcion: str | None = None
    precio_referencia: Decimal | None = Field(default=None, ge=0)
    estado: str | None = None


class TipoQuesoRead(TenantRead):
    nombre: str
    descripcion: str | None
    precio_referencia: Decimal


class ProduccionCreate(BaseSchema):
    fecha: date
    tipo_queso_id: uuid.UUID
    sucursal_id: uuid.UUID | None = None
    cantidad: Decimal = Field(default=Decimal("0"), ge=0)
    peso_kg: Decimal = Field(gt=0)
    litros_usados: Decimal = Field(default=Decimal("0"), ge=0)
    merma: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None


class ProduccionUpdate(BaseSchema):
    fecha: date | None = None
    tipo_queso_id: uuid.UUID | None = None
    sucursal_id: uuid.UUID | None = None
    cantidad: Decimal | None = Field(default=None, ge=0)
    peso_kg: Decimal | None = Field(default=None, gt=0)
    litros_usados: Decimal | None = Field(default=None, ge=0)
    merma: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = None
    estado: str | None = None


class ProduccionRead(TenantRead):
    fecha: date
    tipo_queso_id: uuid.UUID
    tipo_queso_nombre: str | None = None
    sucursal_id: uuid.UUID | None
    cantidad: Decimal
    peso_kg: Decimal
    litros_usados: Decimal
    rendimiento: Decimal
    merma: Decimal
    observaciones: str | None


# ------------------------------------------- utilidad por lote de producción
class LecheDelLoteRead(BaseSchema):
    """De qué proveedor vino la leche que usó este lote y cuánto costó.

    Es lo que hace que el costo del lote sea real y no un promedio del mes: son
    las recepciones concretas que se consumieron, con el precio de cada una.
    """

    proveedor: str
    fecha_recepcion: date
    litros: Decimal
    costo_leche: Decimal
    costo_transporte: Decimal
    costo: Decimal  # leche + transporte


class VentaDelLoteProduccionRead(BaseSchema):
    """Una venta que se llevó kilos de este lote.

    `kilos` son los que salieron de ESTE lote y `kilos_venta` los del renglón
    completo: un despacho grande se reparte entre varios lotes.
    """

    fecha: date
    cliente: str
    producto: str
    kilos: Decimal
    kilos_venta: Decimal
    precio_kilo: Decimal
    ingreso: Decimal
    costo: Decimal
    # La parte del flete de ese despacho que le toca a este lote
    gasto: Decimal = Decimal("0")
    # Lo que costó el kilo PUESTO en el destino: el queso más el flete
    costo_puesto_kilo: Decimal = Decimal("0")
    utilidad: Decimal
    partida: bool


class LoteProduccionRead(BaseSchema):
    """Una producción con lo que costó y lo que dejó.

    OJO con `utilidad`: es la de lo que YA se vendió del lote, y NO le resta el
    costo del queso que sigue en bodega. Restárselo es justo el error que hace que
    el estado de resultados del mes salga negativo cuando el negocio va bien: la
    plata de la leche está ahí, convertida en queso, esperando venderse.
    """

    fecha: date
    tipo_queso: str
    # 'produccion' = se hizo aquí, con su leche detrás.
    # 'existencia' = ya estaba en bodega y se cargó a mano; su costo es el que se
    # cargó y no tiene leche. Es el caso normal al empezar a usar el sistema.
    origen: str
    referencia: str | None = None
    # Existencia cargada SIN costo: sus kilos salen como si hubieran costado cero,
    # así que hacen ver la utilidad mejor de lo que es.
    sin_costo: bool = False
    litros_usados: Decimal
    kilos_producidos: Decimal
    merma: Decimal
    rendimiento: Decimal  # kilos de queso por litro de leche
    # Lo que costó
    costo_leche: Decimal
    costo_transporte: Decimal
    costo_total: Decimal
    costo_kilo: Decimal
    # A dónde fueron los kilos (los tres suman kilos_producidos)
    kilos_vendidos: Decimal
    # Ajustes de inventario hacia abajo: se dañó o se corrigió un sobrante. Sí se
    # le resta a la utilidad, porque es plata que salió sin ingreso.
    kilos_de_baja: Decimal = Decimal("0")
    # DE `kilos_de_baja`, la parte que es merma de un CIERRE DE CICLO: queso que se
    # secó entre que se pesó al hacerlo y se pesó al venderlo. Es un SUBCONJUNTO y
    # no un cuarto destino: no entra otra vez en la suma vendidos + baja + bodega,
    # que ya lo lleva dentro de la baja. Va aparte para que el dueño distinga lo
    # normal del oficio (se secó) de lo que sí hay que ir a mirar (se dañó).
    kilos_merma_ciclo: Decimal = Decimal("0")
    kilos_en_bodega: Decimal
    # Plata
    ingresos: Decimal
    # Fletes de los despachos, en la parte que le toca a este lote. Sí se le restan
    # a la utilidad: es plata que salió para poder vender.
    gastos: Decimal = Decimal("0")
    # Lo que costó el kilo PUESTO en el destino (queso + flete), sobre los kilos
    # VENDIDOS: el flete solo se pagó por los que se despacharon.
    costo_puesto_kilo: Decimal = Decimal("0")
    costo_vendido: Decimal
    costo_de_baja: Decimal = Decimal("0")
    # Subconjunto de `costo_de_baja`: lo que valía el queso que se secó
    costo_merma_ciclo: Decimal = Decimal("0")
    costo_en_bodega: Decimal
    # ingresos - costo_vendido - costo_de_baja. Lo de bodega NO se resta: ese queso
    # está ahí, no se ha perdido.
    utilidad: Decimal
    precio_venta_kilo: Decimal
    vendido_completo: bool
    # Litros que se usaron sin leche registrada que los respalde
    litros_sin_recepcion: Decimal
    detalle_leche: list[LecheDelLoteRead] = []
    detalle_ventas: list[VentaDelLoteProduccionRead] = []


class LotesProduccionPanel(BaseSchema):
    """Los lotes de producción con lo que dejó cada uno.

    Los totales son la suma EXACTA de los lotes listados. Los tres avisos del
    final no se esconden nunca: significan que falta cargar algo y que la cuenta
    está incompleta.

    Hay DOS desgloses y los dos tienen que cuadrar al peso, porque el usuario los
    suma a mano en la pantalla:

    1. De dónde sale la utilidad (todo del rango pedido):
           total_ingresos − total_costo_vendido − total_costo_de_baja
           − total_gastos = total_utilidad
    2. Dónde está la plata de la leche de esos lotes (foto de HOY):
           total_costo_vendido + total_costo_de_baja + total_costo_en_bodega
           = total_costo

    `total_costo_vendido` es la bisagra: es lo que se le resta a la utilidad y a
    la vez el pedazo del costo del lote que ya salió de la bodega. Sin esa cifra
    las tarjetas de la pantalla no se pueden encadenar y quedan como cinco
    números sueltos, que es justo lo que el usuario reclamó.
    """

    lotes: list[LoteProduccionRead] = []
    total_utilidad: Decimal
    total_litros: Decimal
    total_kilos: Decimal
    total_costo: Decimal
    total_ingresos: Decimal
    total_gastos: Decimal = Decimal("0")
    # Lo que costó el queso que YA salió del lote: es lo que se le resta a la
    # utilidad y, a la vez, el pedazo del costo del lote que ya no está en bodega.
    total_costo_vendido: Decimal = Decimal("0")
    total_costo_de_baja: Decimal = Decimal("0")
    total_kilos_vendidos: Decimal = Decimal("0")
    total_kilos_de_baja: Decimal = Decimal("0")
    # De las bajas, la parte que es merma de cierre de ciclo (queso que se secó).
    # SUBCONJUNTO de las dos de arriba: no se vuelve a sumar en ningún desglose.
    total_kilos_merma_ciclo: Decimal = Decimal("0")
    total_costo_merma_ciclo: Decimal = Decimal("0")
    total_kilos_en_bodega: Decimal
    total_costo_en_bodega: Decimal
    mejor: date | None = None
    peor: date | None = None
    # Queso vendido (o dado de baja) que no salió de ningún lote registrado
    kilos_sin_lote: Decimal
    # Existencia cargada a mano sin costo: hace ver la utilidad mejor de lo que es
    kilos_existencia_sin_costo: Decimal = Decimal("0")
    ingreso_sin_lote: Decimal
    # Litros usados en producciones sin leche registrada que los respalde
    litros_sin_recepcion: Decimal
    # Leche recibida que todavía no se ha usado en ninguna producción
    litros_sin_usar: Decimal
    costo_litros_sin_usar: Decimal


class CifrasDelPeriodo(BaseSchema):
    """Lo que el estado de resultados necesita de la cadena de lotes.

    Todo va cortado al período pedido, con estos criterios:

    - `costo_queso_vendido`, `transporte_despachos` y `queso_danado` son de los
      DOCUMENTOS con fecha en el período: las ventas que se despacharon y los
      ajustes que se hicieron en esas fechas. Son lo que entra en la utilidad.
    - `leche_sin_usar` y `queso_en_bodega` son informativos y se miden HOY: de lo
      que se compró o se hizo en el período, cuánto sigue sin venderse. No entran
      en la utilidad porque no son pérdida: la plata está ahí.

    Ojo con las bases, que son distintas a propósito: la leche sin usar se cuenta
    por la fecha en que LLEGÓ, y el queso en bodega por la fecha en que SE HIZO.
    Un lote del 1 de julio hecho con leche del 30 de junio cuenta en el queso de
    julio aunque su leche fuera de junio. Como estas dos cifras no entran en la
    utilidad, esa mezcla no descuadra nada, pero hay que decirla.
    """

    # Entran en la utilidad
    queso_vendido: Decimal  # ingresos de los renglones de queso del período
    costo_queso_vendido: Decimal
    transporte_despachos: Decimal
    queso_danado: Decimal
    # Informativos, medidos hoy
    leche_sin_usar: Decimal
    queso_en_bodega: Decimal
    # Avisos: plata que no se puede costear
    queso_vendido_sin_costo: Decimal  # se vendió queso que no salió de ningún lote
    # De qué producciones salió el queso que se vendió: la suma de sus costos ES
    # `costo_queso_vendido`, así que el usuario puede seguir la cuenta renglón por
    # renglón hasta la cifra que se resta arriba.
    origen_del_costo: list["OrigenDelCosto"] = []


class OrigenDelCosto(BaseSchema):
    """Una producción de la que salió parte del queso vendido en el período.

    Es la respuesta a "¿y la leche, se resta o no?": el costo que se resta en el
    estado de resultados es la suma de estas producciones, y cada una se puede
    abrir en la pantalla de lotes para ver de qué proveedor vino su leche.

    Se prefiere esto a un puente que parta de la leche comprada en el mes: ese
    puente necesita un renglón de ajuste por el queso hecho con leche de otro mes,
    y ese ajuste puede salir enorme y no es algo que el usuario pueda señalar. Esta
    lista, en cambio, suma exacto la cifra y cada renglón es un documento real.
    """

    fecha: date
    tipo_queso: str
    origen: str  # 'produccion' | 'existencia'
    kilos: Decimal  # kilos de ese lote que se vendieron en el período
    costo: Decimal  # lo que costaron esos kilos


# ----------------------------------------------- cierre de ciclo de despacho
class MermaDelTipoRead(BaseSchema):
    """La cuenta de la merma para UN tipo de queso dentro del ciclo.

    Va por tipo y no en un solo total porque no se puede compensar el doble
    crema que faltó con el campesino que sobró: son dos productos con
    rendimientos y colas de inventario distintas, y mezclarlos escondería que a
    uno le falta queso mientras al otro le sobra.

    LA CUENTA, renglón por renglón, es la que el dueño lee antes de aceptar:

        producido − vendido − ya bajado a mano = MERMA

    `kilos_ajuste_manual` es el renglón que evita cobrar la merma dos veces: si
    el dueño ya anotó "se perdieron 3 kg" dentro del ciclo, esos kilos ya
    salieron de la bodega y ya se le restaron al lote.
    """

    tipo_queso_id: uuid.UUID
    tipo_queso: str
    kilos_producidos: Decimal
    kilos_vendidos: Decimal
    kilos_ajuste_manual: Decimal
    # Queso cargado a mano HACIA ARRIBA dentro del ciclo (una existencia que se
    # subió al inventario sin ser una tanda). No entra en la cuenta —no es una
    # tanda del ciclo— pero se muestra porque puede explicar una merma rara.
    kilos_entrada_manual: Decimal = Decimal("0")
    # producido - vendido - ajuste manual. Puede salir NEGATIVO: se vendió más de
    # lo que se produjo. Eso no es merma, es un aviso, y por eso se muestra tal
    # cual en vez de recortarlo a cero calladamente.
    kilos_merma: Decimal
    # Qué porcentaje de lo producido se secó. Es la cifra que dice si la merma es
    # creíble: un 4% es queso secándose, un 40% es una venta sin anotar.
    porcentaje: Decimal


class MermaDelLoteRead(BaseSchema):
    """La parte de la merma que le toca a UNA tanda del ciclo.

    La suma de `kilos_merma` de estas filas es EXACTAMENTE la merma del ciclo, y
    la de `costo_merma`, exactamente su costo. El dueño suma esta columna a mano.
    """

    produccion_id: uuid.UUID
    fecha: date
    tipo_queso: str
    kilos_producidos: Decimal
    kilos_merma: Decimal
    costo_merma: Decimal


class CicloPropuesta(BaseSchema):
    """La cuenta de un ciclo ANTES de cerrarlo: lo que el dueño va a aceptar.

    No escribe nada. Es la pantalla de "se produjeron X kg, salieron Y, la
    diferencia son Z kg que valen $W", con el desglose por tipo de queso y por
    tanda, para que se vea qué se está dando por perdido antes de darlo.

    `advertencias` no está vacía cuando la cuenta huele mal: merma negativa (se
    vendió más de lo que se produjo), merma desproporcionada (más del 10% de lo
    producido), o queso cargado a mano dentro del ciclo. Con advertencias el
    cierre NO pasa sin que alguien las acepte explícitamente: puede ser una venta
    sin anotar y no queso secándose, y registrarla callada la volvería invisible.
    """

    fecha_inicio: date
    fecha_fin: date
    dias: int
    nombre_sugerido: str
    # Totales del ciclo: la suma exacta de los renglones de `por_tipo`
    kilos_producidos: Decimal
    kilos_vendidos: Decimal
    kilos_ajuste_manual: Decimal
    kilos_merma: Decimal
    costo_merma: Decimal
    porcentaje: Decimal
    por_tipo: list[MermaDelTipoRead] = []
    por_lote: list[MermaDelLoteRead] = []
    advertencias: list[str] = []
    # Si ya pasaron los días del ciclo (siete por defecto) desde el último cierre.
    # Es lo que hace que el sistema PROPONGA en vez de esperar a que se acuerden.
    toca_cerrar: bool = False
    # Días corridos desde el día siguiente al último cierre hasta hoy
    dias_desde_ultimo_cierre: int = 0
    # Si no hay nada que cerrar (ni tandas ni ventas en el rango)
    vacio: bool = False


class CicloDespachoRead(TenantRead):
    """Un ciclo de despacho con la cuenta que se aceptó al cerrarlo."""

    nombre: str
    fecha_inicio: date
    fecha_fin: date
    notas: str | None
    cerrado: bool
    cerrado_at: datetime | None
    # La FOTO de lo que se aceptó ese día. Ver el modelo `CicloDespacho`: aquí sí
    # se guardan cifras, al revés que en las temporadas de reventa, porque cerrar
    # un ciclo escribe ajustes de inventario y hay que poder auditar qué se aceptó.
    kilos_producidos: Decimal
    kilos_vendidos: Decimal
    kilos_ajuste_manual: Decimal
    kilos_merma: Decimal
    costo_merma: Decimal
    porcentaje: Decimal
    advertencias: list[str] = []
    dias: int
    por_lote: list[MermaDelLoteRead] = []


class CiclosPanel(BaseSchema):
    """Lo que necesita la pantalla de ciclos en una sola llamada.

    Los totales son la SUMA EXACTA de los ciclos listados, no un recálculo del
    histórico: si se consultara aparte, los días que no caen en ningún ciclo
    harían que el total diera más que la suma de la lista y el desglose dejaría
    de cuadrar, que es justo lo que el dueño revisa con calculadora.
    """

    ciclos: list[CicloDespachoRead] = []
    total_kilos_producidos: Decimal = Decimal("0")
    total_kilos_merma: Decimal = Decimal("0")
    total_costo_merma: Decimal = Decimal("0")
    # El ciclo que el sistema propone cerrar ahora, con su cuenta ya hecha. Es
    # null solo si no hay absolutamente nada que cerrar.
    propuesta: CicloPropuesta | None = None


class CicloCerrar(BaseSchema):
    """Cerrar un ciclo: se aceptan las fechas y la merma que salga de ellas.

    `aceptar_advertencias` es a propósito un campo aparte y no un `force`
    genérico: obliga a que quien cierre haya visto la cuenta rara y decida
    igual. Es plata que se da por perdida.
    """

    fecha_inicio: date
    fecha_fin: date
    nombre: str | None = Field(default=None, max_length=80)
    notas: str | None = Field(default=None, max_length=500)
    aceptar_advertencias: bool = False
