import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, PlainSerializer, field_serializer, model_validator

from app.common.schemas import BaseSchema, TenantRead, a_dos_decimales

CERO = Decimal("0")
DOS_DECIMALES = Decimal("0.01")
SIN_DECIMALES = Decimal("1")


# --------------------------------------------- cómo se escribe una cifra en la salida
# EL CERO TENÍA DOS CARAS EN LA MISMA RESPUESTA, y aunque no movía un peso, había que
# cerrarlo. Una cifra que sale de una suma de SQL llega con la escala de su columna
# ('0.00') y una que sale de sumar en Python llega pelada ('0'), así que en un mismo
# resumen la fila del queso decía `kilos: "0"` y la de la borona `kilos: "0.00"`, y
# `ingreso: "0"` convivía con `costo: "0.00"`. Nadie pierde plata con eso, pero sí
# cuesta: quien compara dos respuestas para verificar un despliegue tiene que decidir
# cada vez si esa diferencia importa, y una alarma que hay que interpretar es una
# alarma que se deja de mirar.
#
# LA REGLA DE AHORA, Y ES UNA SOLA: la escala la decide LA CLASE DE CIFRA y no de dónde
# salió la suma. La plata y los kilos van con dos decimales SIEMPRE, y lo que se cuenta
# por unidades enteras va sin decimales SIEMPRE. Es solo la forma de escribirla: el
# valor no se toca, no se redondea nada que no estuviera ya en centavos, y la prueba de
# no movimiento (que compara como NÚMERO) sigue dando exactamente lo mismo.
def _con_dos_decimales(valor: Decimal | None) -> str | None:
    if valor is None:
        return None
    return str(Decimal(valor).quantize(DOS_DECIMALES))


def _sin_decimales(valor: Decimal | None) -> str | None:
    """Lo que se cuenta por piezas completas. Se cuantiza solo si de verdad es
    entero: una fila rara con media unidad tiene que verse, no redondearse en
    silencio (es el mismo criterio de `_barras_enteras` al entrar)."""
    if valor is None:
        return None
    numero = Decimal(valor)
    if numero != numero.to_integral_value():
        return str(numero)
    return str(numero.quantize(SIN_DECIMALES))


# La plata y los kilos: dos decimales. Las unidades: ninguno.
Plata = Annotated[Decimal, PlainSerializer(_con_dos_decimales, return_type=str, when_used="json")]
EnKilos = Annotated[Decimal, PlainSerializer(_con_dos_decimales, return_type=str, when_used="json")]
EnUnidades = Annotated[Decimal, PlainSerializer(_sin_decimales, return_type=str, when_used="json")]


# Kilos: siempre dos decimales, los mismos que caben en la base (las columnas de
# kilos son Numeric(12, 2)). El redondeo es el compartido `a_dos_decimales`: era
# una COPIA de la misma función que hay en recepción, y el defecto del `except`
# —el redondeo que se rendía y devolvía el valor CRUDO, así que 1E+30 se guardaba
# sin redondear— estaba en las dos copias. Ver su docstring.
Kilos = Annotated[Decimal, BeforeValidator(a_dos_decimales)]


def _barras_enteras(valor: Any) -> Any:
    """Las barras se cuentan por unidades COMPLETAS: nada de decimales.

    Aquí NO se redondea, se RECHAZA. Es la diferencia con los kilos, y es a
    propósito: 10,005 kg es un pesaje real que hay que llevar a 10,01, pero
    "8,5 barras" no es media barra mal medida, es un dato equivocado —el dueño
    pidió "barras completas POR UNIDAD"—. Redondearlo en silencio le guardaría
    9 barras a quien escribió 8,5 y la cuenta de la plata saldría distinta de la
    que él hizo con la calculadora.

    La columna es Numeric(12,0), así que si esto no rechazara, Postgres
    redondearía por su cuenta y la fila quedaría contradiciéndose sola (el mismo
    defecto que ya nos costó caro con los kilos de tres decimales).
    """
    if valor is None or isinstance(valor, bool):
        return valor
    try:
        numero = Decimal(str(valor))
    except (ArithmeticError, TypeError, ValueError):
        # Que lo rechace Pydantic con su mensaje, no un error raro desde aquí.
        return valor
    if numero != numero.to_integral_value():
        raise ValueError(
            "Las barras se cuentan por unidades completas: no acepta decimales"
        )
    return numero


# Barras: unidades completas de mozzarella (una barra es una barra).
Barras = Annotated[Decimal, BeforeValidator(_barras_enteras)]

# Cómo se mide cada tipo, para los rótulos de la pantalla y de los documentos.
UNIDAD_KILO = "kg"
UNIDAD_BARRA = "barra"


# ------------------------------------------------------- catálogo de productos
# QUÉ SE PUEDE ESCRIBIR Y QUÉ NO, que es todo el contrato de estas tres piezas.
#
# Al crear se pide lo MÍNIMO: el nombre y si se pesa o se cuenta. Ni `clave`, ni
# `decimales`, ni `admite_ajustes` se preguntan, y no es por ahorrarle campos al
# formulario: los tres se DEDUCEN (la clave del nombre, los otros dos de la
# unidad), y un campo que se puede deducir y además se pregunta es una segunda
# fuente para el mismo hecho — el día que las dos se contradigan no hay manera de
# saber cuál creer. El porqué de cada uno está en el modelo `ProductoReventa`.
class ProductoReventaCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=80)
    # 'kg' se pesa, 'unidad' se cuenta. EN ESTE CORTE SOLO PASA 'kg': un producto
    # por unidad exige tumbar los CHECK de `compras_queso` y `ventas_queso` que hoy
    # obligan a que las barras vivan en sus propias columnas, y eso es el lote
    # siguiente. El servicio lo rechaza con un mensaje que lo explica. Se admite en
    # el esquema para poder dar ESE mensaje: si el campo no existiera, un producto
    # por unidad se guardaría en silencio como si se pesara.
    unidad: Literal["kg", "unidad"] = "kg"
    # De qué producto es subproducto (la borona lo es del queso). Opcional y de un
    # solo nivel: ver el modelo.
    subproducto_de_id: uuid.UUID | None = None
    # Sin `orden` va al final de la lista, que es lo que uno espera al agregar.
    orden: int | None = Field(default=None, ge=0)


class ProductoReventaUpdate(BaseSchema):
    """Lo que se puede corregir de un producto ya creado.

    NO SE PUEDE CAMBIAR NI LA CLAVE NI LA UNIDAD, y por eso no están aquí. La clave
    es la identidad —es la misma cadena que ya está guardada en las filas de
    compras y de ventas—, así que cambiarla desconectaría al producto de su propia
    historia. Y la unidad decide la forma de la cantidad: pasar a 'unidad' un
    producto con kilos registrados dejaría esos kilos contados como piezas. Si se
    creó mal y no tiene movimientos, se quita y se vuelve a crear.

    RENOMBRAR SÍ, Y SIEMPRE. Es la razón de que la clave exista aparte: "Queso"
    puede pasar a decir "Queso costeño" sin que ninguna fila se entere.
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=80)
    # Mandarlo en null explícito significa "ya no es subproducto de nadie". Solo se
    # puede mover mientras el producto no tenga movimientos: de quién hereda el
    # costo un subproducto es una cuenta de plata (ver `lotes.py`), y cambiarla con
    # movimientos encima recostearía historia que el dueño ya cuadró.
    subproducto_de_id: uuid.UUID | None = None
    orden: int | None = Field(default=None, ge=0)
    estado: str | None = None


class ProductoReventaRead(TenantRead):
    nombre: str
    clave: str
    unidad: str
    decimales: int
    subproducto_de_id: uuid.UUID | None
    subproducto_de_nombre: str | None
    admite_ajustes: bool
    se_pesa: bool
    orden: int


class AbonoRead(BaseSchema):
    id: uuid.UUID
    fecha: date
    valor: Plata
    observaciones: str | None


class AbonoCreate(BaseSchema):
    fecha: date
    valor: Decimal = Field(gt=0)
    observaciones: str | None = None


# ----------------------------------------------------------------- compras
# EL TIPO MANDA Y NO SE EDITA. Una compra nace de queso (kilos) o de mozzarella
# (barras) y se queda así: cambiarle el tipo a una compra que ya tiene ventas
# encima movería queso vendido a una cola de barras y al revés, y el reparto por
# lotes quedaría contando cantidades que nunca existieron. Si se registró mal, se
# elimina o se anula y se vuelve a registrar. Es el mismo criterio que ya tenía
# la venta: `VentaQuesoUpdate` nunca ha aceptado `tipo`.
class RenglonCompraCreate(BaseSchema):
    """UN PRODUCTO de una compra: qué, cuánto y a qué precio. Sin fecha ni
    productor, que son de la factura y no del renglón.

    Es la MISMA pieza que valida el payload plano de un solo producto: por eso
    `CompraQuesoCreate` hereda de aquí en vez de repetir los campos y el
    validador. Una sola implementación de "qué necesita un renglón de compra",
    dos puertas para escribirlo.

    Los campos de las dos unidades son OPCIONALES en el esquema y obligatorios
    según el tipo; lo exige `_exigir_campos_de_la_unidad`. No se pueden poner
    obligatorios de entrada porque una compra de mozzarella no tiene kilos que
    informar, y una de queso no tiene barras.
    """

    tipo: str = "queso"
    # --- si se pesa
    kilos_brutos: Kilos | None = Field(default=None, gt=0)
    borona_kilos: Kilos = Field(default=Decimal("0"), ge=0)
    # A QUÉ PRODUCTO LE ENTRAN ESOS KILOS QUE LLEGARON GRATIS (su clave). Opcional: la
    # pantalla de hoy no lo manda y el servicio lo resuelve con el catálogo sin mirar
    # el orden (ver `CatalogoReventa._quien_recibe`): el único subproducto que se pese,
    # y si no, la clave de siempre. La compra NUNCA se queda sin poder registrarse por
    # un problema de la lista de productos.
    subproducto_tipo: str | None = None
    precio_kilo: Decimal | None = Field(default=None, gt=0)
    # --- si se cuenta
    barras: Barras | None = Field(default=None, gt=0)
    precio_barra: Decimal | None = Field(default=None, gt=0)
    observaciones: str | None = None

    @model_validator(mode="after")
    def _exigir_campos_de_la_unidad(self) -> "RenglonCompraCreate":
        """Exige la cantidad y el precio de LA UNIDAD DEL TIPO, y deja la otra en
        cero. No es solo una validación de formulario: es lo que hace que la fila
        cumpla que las barras no puedan colarse en un total de kilos.
        """
        tiene_barras = getattr(self, "barras", 0) and getattr(self, "barras", 0) > 0 and getattr(self, "precio_barra", 0) and getattr(self, "precio_barra", 0) > 0
        tiene_kilos = getattr(self, "kilos_brutos", 0) and getattr(self, "kilos_brutos", 0) > 0 and getattr(self, "precio_kilo", 0) and getattr(self, "precio_kilo", 0) > 0

        if tiene_barras and not tiene_kilos:
            self.kilos_brutos = CERO
            self.precio_kilo = CERO
            self.borona_kilos = CERO
            # Sin kilos gratis no hay a quién nombrar: lo que se cuenta por unidades
            # no trae nada encima.
            self.subproducto_tipo = None
        elif tiene_kilos and not tiene_barras:
            self.barras = CERO
            self.precio_barra = CERO
        elif tiene_barras and tiene_kilos:
            raise ValueError("No se pueden mandar kilos y barras en el mismo renglón")
        else:
            raise ValueError("El renglón debe tener kilos y precio_kilo, o barras y precio_barra")
        return self


class CompraQuesoCreate(RenglonCompraCreate):
    """Una compra a un productor DE UN SOLO PRODUCTO, en kilos (queso) o en barras
    (mozzarella). El payload plano de siempre, intacto.

    SIGUE VIVO Y NO ES POR COMPATIBILIDAD NOSTÁLGICA: hay miles de líneas de
    pruebas escritas sobre este payload y son la única prueba de que la plata
    sigue cuadrando. Cambiar el modelo y la regla que lo mide en el mismo commit
    es quedarse sin red. Por dentro arma un documento de un renglón (ver
    `CompraQuesoService.crear`), así que las dos puertas escriben con el MISMO
    código.
    """

    fecha: date
    productor: str = Field(min_length=2, max_length=150)


class CompraQuesoUpdate(BaseSchema):
    """Edición de una compra. NO lleva `tipo`: ver la nota de arriba.

    Los campos de la unidad que no corresponde se ignoran en el servicio (que
    conoce el tipo de la fila guardada); aquí no se pueden validar contra el tipo
    porque el esquema no lo recibe.
    """

    fecha: date | None = None
    productor: str | None = Field(default=None, min_length=2, max_length=150)
    kilos_brutos: Kilos | None = Field(default=None, gt=0)
    borona_kilos: Kilos | None = Field(default=None, ge=0)
    precio_kilo: Decimal | None = Field(default=None, gt=0)
    barras: Barras | None = Field(default=None, gt=0)
    precio_barra: Decimal | None = Field(default=None, gt=0)
    observaciones: str | None = None


class CompraQuesoRead(TenantRead):
    fecha: date
    productor: str
    # A qué factura pertenece este renglón y en qué lugar de ella. Viajan para que
    # la pantalla pueda agrupar la lista por documento y para que el orden de los
    # renglones sea el que el usuario escribió (que es el orden en el que se
    # derrama el abono). En nulo: una compra de las de antes, sin cabecera.
    documento_id: uuid.UUID | None = None
    orden: int = 0
    tipo: str
    # En qué se mide: 'kg' o 'unidad'. Se deduce del tipo (ver models.unidad_de) y
    # viaja aquí para que la pantalla ponga el rótulo correcto sin repetir la regla.
    unidad: str
    kilos_brutos: EnKilos
    borona_kilos: EnKilos
    # A qué producto le entraron esos kilos gratis. En nulo cuando no llegó nada.
    subproducto_tipo: str | None = None
    kilos_netos: EnKilos
    precio_kilo: Plata
    # Barras y precio por barra. En una compra de kilos van en CERO: nunca hay que
    # elegir "el que no sea cero", el `tipo` dice cuál de los dos mirar.
    barras: EnUnidades
    precio_barra: Plata
    valor_total: Plata
    abonado: Plata
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")
    observaciones: str | None
    abonos: list[AbonoRead] = []
    # Cuántos soportes de pago tiene. Solo el número: los adjuntos con sus
    # enlaces se piden aparte, porque firmar una URL por cada foto de cada fila
    # de la lista sería firmar decenas de enlaces que casi nadie va a abrir.
    adjuntos_count: int = 0


# ------------------------------------------------------------------ ventas
class RenglonVentaCreate(BaseSchema):
    """UN PRODUCTO de una venta: queso o borona en KILOS, o mozzarella en BARRAS.
    Sin fecha ni cliente, que son de la factura.

    Igual que en la compra, `VentaQuesoCreate` hereda de aquí: una sola
    implementación de "qué necesita un renglón de venta".
    """

    tipo: str = "queso"
    # --- si se pesa
    kilos: Kilos | None = Field(default=None, gt=0)
    precio_kilo: Decimal | None = Field(default=None, gt=0)
    gasto_por_kilo: Decimal = Field(default=Decimal("0"), ge=0)
    # --- si se cuenta
    barras: Barras | None = Field(default=None, gt=0)
    precio_barra: Decimal | None = Field(default=None, gt=0)
    gasto_por_barra: Decimal = Field(default=Decimal("0"), ge=0)
    gasto_concepto: str | None = Field(default=None, max_length=150)
    observaciones: str | None = None

    @model_validator(mode="after")
    def _exigir_campos_de_la_unidad(self) -> "RenglonVentaCreate":
        """Igual que en la compra: la cantidad y el precio de la unidad del tipo,
        y la otra unidad en cero para que la fila cumpla la regla."""
        tiene_barras = getattr(self, "barras", 0) and getattr(self, "barras", 0) > 0 and getattr(self, "precio_barra", 0) and getattr(self, "precio_barra", 0) > 0
        tiene_kilos = getattr(self, "kilos", 0) and getattr(self, "kilos", 0) > 0 and getattr(self, "precio_kilo", 0) and getattr(self, "precio_kilo", 0) > 0

        if tiene_barras and not tiene_kilos:
            self.kilos = CERO
            self.precio_kilo = CERO
            self.gasto_por_kilo = CERO
        elif tiene_kilos and not tiene_barras:
            self.barras = CERO
            self.precio_barra = CERO
            self.gasto_por_barra = CERO
        elif tiene_barras and tiene_kilos:
            raise ValueError("No se pueden mandar kilos y barras en el mismo renglón")
        else:
            raise ValueError("El renglón debe tener kilos y precio_kilo, o barras y precio_barra")
        return self


class VentaQuesoCreate(RenglonVentaCreate):
    """Una venta a un cliente DE UN SOLO PRODUCTO. El payload plano de siempre.

    Sigue vivo por la misma razón que el de la compra (ver `CompraQuesoCreate`) y
    por dentro arma un documento de un renglón.
    """

    fecha: date
    cliente: str = Field(min_length=2, max_length=150)
    # Pago inmediato: registra la venta ya pagada por completo
    pagada_de_contado: bool = False


class VentaQuesoUpdate(BaseSchema):
    """Edición de una venta. NO lleva `tipo` (nunca lo ha llevado): el tipo define
    de qué inventario sale la mercancía y cambiarlo movería cantidades de una cola
    del reparto FIFO a otra."""

    fecha: date | None = None
    cliente: str | None = Field(default=None, min_length=2, max_length=150)
    kilos: Kilos | None = Field(default=None, gt=0)
    precio_kilo: Decimal | None = Field(default=None, gt=0)
    barras: Barras | None = Field(default=None, gt=0)
    precio_barra: Decimal | None = Field(default=None, gt=0)
    gasto_concepto: str | None = Field(default=None, max_length=150)
    gasto_por_kilo: Decimal | None = Field(default=None, ge=0)
    gasto_por_barra: Decimal | None = Field(default=None, ge=0)
    observaciones: str | None = None


class VentaQuesoRead(TenantRead):
    fecha: date
    cliente: str
    # Mismo criterio que en la compra: ver CompraQuesoRead.
    documento_id: uuid.UUID | None = None
    orden: int = 0
    tipo: str
    unidad: str  # 'kg' | 'barra', deducida del tipo
    kilos: EnKilos
    precio_kilo: Plata
    # En una venta de kilos van en cero, y al contrario (ver el CHECK de la tabla).
    barras: EnUnidades
    precio_barra: Plata
    valor_total: Plata
    gasto_concepto: str | None
    gasto_por_kilo: Plata
    gasto_por_barra: Plata
    gasto_monto: Plata  # el gasto en PESOS, ya sea por kilo o por barra
    abonado: Plata
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")
    observaciones: str | None
    abonos: list[AbonoRead] = []
    # Mismo criterio que en la compra: solo el número (ver CompraQuesoRead).
    adjuntos_count: int = 0


# ----------------------------------- documentos (la factura de varios renglones)
# CUÁNTOS RENGLONES CABEN EN UNA FACTURA. Cincuenta es muchísimo más de lo que un
# día de reventa da (el cliente maneja tres o cuatro productos), y el tope existe
# porque cada renglón de venta cuesta una validación de existencias y una fila: un
# POST con diez mil renglones sería una forma barata de tumbar el servidor. Y
# porque una "factura" de cien productos es un error de digitación, no un negocio.
MAX_RENGLONES = 50


class DocumentoCompraCreate(BaseSchema):
    """UNA COMPRA con varios productos: la cabecera y sus renglones.

    La cabecera NO TIENE NI UNA CIFRA DE PLATA, a propósito y por contrato: el
    total es la suma de los renglones, calculada al leer. Ver el docstring de
    `DocumentoReventa`.
    """

    tipo: Literal["compra"]
    fecha: date
    # El productor. Se canoniza contra los nombres ya usados con la MISMA regla
    # del payload plano, así que "yeferson" y "Yeferson" siguen siendo el mismo
    # señor y su cartera no se parte en dos.
    tercero: str = Field(min_length=2, max_length=150)
    # Nota de la FACTURA. Cada renglón tiene además la suya (lo que le pasó a ESE
    # producto), y las dos son hechos distintos: no se propaga la una a la otra.
    #
    # SIN `max_length`, igual que en el payload plano, y a propósito: la puerta
    # plana arma por dentro un documento con esta misma nota, así que un tope aquí
    # que allá no exista convertiría una nota larga en un error raro desde el
    # servicio en vez del comportamiento de siempre. Las dos puertas tienen que
    # aceptar y rechazar exactamente lo mismo.
    observaciones: str | None = None
    renglones: list[RenglonCompraCreate] = Field(min_length=1, max_length=MAX_RENGLONES)


class DocumentoVentaCreate(BaseSchema):
    """UNA VENTA con varios productos: la cabecera y sus renglones."""

    tipo: Literal["venta"]
    fecha: date
    tercero: str = Field(min_length=2, max_length=150)
    # Sin `max_length`, por lo mismo que en DocumentoCompraCreate.
    observaciones: str | None = None
    renglones: list[RenglonVentaCreate] = Field(min_length=1, max_length=MAX_RENGLONES)
    # Pago inmediato de TODA la factura. Se registra como un abono al documento,
    # o sea que se DERRAMA sobre los renglones (ver el derrame en el servicio):
    # cada renglón queda con su abono entero y la suma da el total, exacta.
    pagada_de_contado: bool = False


# La cabecera dice de qué clase es la factura y ESO decide la forma de sus
# renglones: `discriminator="tipo"` hace que pydantic valide contra el esquema
# correcto y que el error de un renglón de venta mandado como compra sea un 422
# entendible en vez de "no coincide con ninguna de las dos opciones".
DocumentoReventaCreate = Annotated[
    DocumentoCompraCreate | DocumentoVentaCreate, Field(discriminator="tipo")
]


class DocumentoCompraUpdate(BaseSchema):
    """Edición de una factura de compra.

    `tipo` ES OBLIGATORIO aunque el id ya diga cuál es la factura, y no es un
    descuido: es el discriminador que le dice a pydantic con qué forma validar los
    renglones. El servicio comprueba que coincida con el de la factura guardada y
    rechaza el cambio de tipo, que movería renglones de una tabla a la otra.

    `renglones` en NULO significa "no me toques los productos" (edición de solo
    cabecera). Mandar la lista significa REHACERLOS, y eso solo se permite si la
    factura no tiene abonos: ver el candado en el servicio.
    """

    tipo: Literal["compra"]
    fecha: date | None = None
    tercero: str | None = Field(default=None, min_length=2, max_length=150)
    # Sin `max_length`, por lo mismo que en DocumentoCompraCreate.
    observaciones: str | None = None
    renglones: list[RenglonCompraCreate] | None = Field(
        default=None, min_length=1, max_length=MAX_RENGLONES
    )


class DocumentoVentaUpdate(BaseSchema):
    """Edición de una factura de venta. Mismo criterio que en la compra."""

    tipo: Literal["venta"]
    fecha: date | None = None
    tercero: str | None = Field(default=None, min_length=2, max_length=150)
    # Sin `max_length`, por lo mismo que en DocumentoCompraCreate.
    observaciones: str | None = None
    renglones: list[RenglonVentaCreate] | None = Field(
        default=None, min_length=1, max_length=MAX_RENGLONES
    )


DocumentoReventaUpdate = Annotated[
    DocumentoCompraUpdate | DocumentoVentaUpdate, Field(discriminator="tipo")
]


class DocumentoReventaRead(TenantRead):
    """Una factura con sus renglones y su total CALCULADO.

    NINGUNA DE LAS CIFRAS DE AQUÍ ESTÁ GUARDADA. Todas salen de sumar los
    renglones en el momento de leer, y por eso no pueden desactualizarse ni
    contradecir la lista que van al lado.

    Y LA IGUALDAD QUE EL DUEÑO PUEDE VERIFICAR A MANO, que es la razón de que
    `total_anulado` exista y no se esconda:

        total + total_anulado == la suma del valor_total de TODOS los renglones
                                 que salen en esta respuesta
        saldo               == total - abonado

    O sea: el desglose que se ve en la pantalla suma EXACTO la cifra grande, sin
    que sobre ni falte un renglón. Si un renglón se anuló, su plata no desaparece
    de la vista: se va a `total_anulado`, que es la única forma honesta de que la
    columna siga cerrando.
    """

    tipo: str  # 'compra' | 'venta'
    fecha: date
    tercero: str
    observaciones: str | None
    # ------------------------------------------------------------ lo calculado
    # Suma del valor_total de los renglones QUE CUENTAN (los no anulados).
    total: Plata
    # Suma de lo abonado en esos mismos renglones (la suma de sus abonos, exacta:
    # el derrame no divide nada, así que no hay redondeo que la desvíe).
    abonado: Plata
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")
    # La plata de los renglones ANULADOS, aparte. Ver la igualdad del docstring.
    total_anulado: Plata
    # DERIVADO de los renglones, no una columna: 'pendiente' si ninguno tiene
    # abonos, 'pagada' si todos están pagados, 'parcial' en el medio y 'anulada'
    # si no queda ni un renglón vivo sin anular.
    estado_pago: str
    cantidad_renglones: int
    # Los renglones tal como los devuelven /compras y /ventas: son las MISMAS
    # filas, con los mismos campos. `tipo` de arriba dice cuál de las dos formas
    # viene. Van en su orden (orden, después id).
    renglones: list[CompraQuesoRead] | list[VentaQuesoRead] = []


# ------------------------------------------- saldos de la cuenta anterior
class SaldoAnteriorCreate(BaseSchema):
    """Una cuenta a medio pagar traída del sistema anterior.

    `abonado` es lo que el tercero ya había pagado en el libro viejo: casi
    ninguna cuenta llega en ceros. Se guarda además como el primer abono del
    historial para que el detalle cuadre con el total abonado.
    """

    tipo: Literal["cobrar", "pagar"]
    tercero: str = Field(min_length=2, max_length=150)
    fecha: date
    concepto: str = Field(min_length=2, max_length=200)
    valor_total: Decimal = Field(gt=0)
    abonado: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = Field(default=None, max_length=500)


class SaldoAnteriorUpdate(BaseSchema):
    """`abonado` NO se edita aquí: solo se mueve registrando o eliminando abonos,
    igual que en las compras y en las ventas."""

    tipo: Literal["cobrar", "pagar"] | None = None
    tercero: str | None = Field(default=None, min_length=2, max_length=150)
    fecha: date | None = None
    concepto: str | None = Field(default=None, min_length=2, max_length=200)
    valor_total: Decimal | None = Field(default=None, gt=0)
    observaciones: str | None = Field(default=None, max_length=500)


class SaldoAnteriorRead(TenantRead):
    tipo: str
    tercero: str
    fecha: date
    concepto: str
    valor_total: Plata
    abonado: Plata
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")
    observaciones: str | None
    abonos: list[AbonoRead] = []


# ------------------------------------ adjuntos (soportes de transferencia)
class AdjuntoRead(BaseSchema):
    """Un soporte de pago, con un enlace TEMPORAL para verlo.

    `url` NO está guardada en ninguna parte: se firma cada vez que se pide esta
    lista y se muere sola a los pocos minutos. Por eso viene siempre acompañada
    de `url_expira`: si la pantalla se queda abierta media hora, los enlaces que
    tiene en memoria ya no sirven y hay que volver a pedir la lista.

    Es `None` cuando el almacenamiento no está configurado: en ese caso la fila
    igual se muestra (nombre, tamaño, quién lo subió) pero sin poder abrirla.
    """

    id: uuid.UUID
    compra_id: uuid.UUID | None
    venta_id: uuid.UUID | None
    nombre_archivo: str
    content_type: str
    tamano_bytes: int
    es_imagen: bool
    subido_por_nombre: str | None
    created_at: datetime
    url: str | None = None
    url_expira: datetime | None = None


class AdjuntosLista(BaseSchema):
    """Los soportes de una compra o de una venta.

    `disponible` en false significa que el almacenamiento no está configurado en
    este servidor. Se responde 200 con el aviso y no un error, porque no es una
    falla de quien pregunta y el resto de la pantalla tiene que seguir usable.
    """

    disponible: bool
    mensaje: str | None = None
    # Cuántos soportes más caben (el tope por documento menos los que ya hay)
    cupo_restante: int = 0
    adjuntos: list[AdjuntoRead] = []


class EnlaceCompartido(BaseSchema):
    """Enlace de MÁS duración para mandar UNA imagen por fuera (WhatsApp).

    `expira_texto` viene armado desde el backend, en hora de Colombia y en
    cristiano ("hasta el martes 5 de agosto a las 3:00 p. m."), porque quien
    reparte el enlace tiene que saber hasta cuándo sirve lo que está repartiendo.
    """

    url: str
    nombre_archivo: str
    expira: datetime
    expira_texto: str
    dias: int


# ------------------------------------------------------------ conversiones
class ConversionCreate(BaseSchema):
    """Un ajuste: kilos que salen del inventario de un producto.

    LOS DOS PRODUCTOS SON OPCIONALES EN LA ENTRADA Y OBLIGATORIOS EN LA FILA. La
    pantalla de hoy no los manda —el ajuste era "queso a borona" y nada más—, así que
    exigirlos aquí dejaría al dueño sin poder registrar un ajuste hasta que el
    frontend cambie. El servicio los resuelve y los GUARDA (ver
    `ConversionBoronaService.crear`): el origen es el producto de siempre y el destino
    lo contesta el catálogo sin mirar el orden —el único subproducto que se pese, y si
    no, la clave de siempre—. Lo que nunca vuelve a pasar es que los adivine cada
    lectura, y lo que nunca pasa es que un problema de la lista de productos deje al
    dueño sin poder registrar el ajuste del día.

    Se mandan por CLAVE y no por id porque la clave es la identidad del producto en
    todo este módulo: es la misma cadena que va en `tipo` de compras y ventas.
    """

    fecha: date
    kilos: Kilos = Field(gt=0)
    destino: Literal["borona", "merma"] = "borona"
    # De qué producto salen los kilos (su clave en el catálogo).
    producto_origen: str | None = None
    # A cuál le entran. Se ignora cuando el destino es la merma: ahí no le entran a
    # nadie, y el servicio lo deja en nulo.
    producto_destino: str | None = None
    precio_kilo: Decimal = Field(default=Decimal("0"), ge=0)
    observaciones: str | None = None


class ConversionRead(TenantRead):
    fecha: date
    kilos: EnKilos
    destino: str
    # Los dos productos VIAJAN EN LA RESPUESTA para que la pantalla pueda mostrar de
    # qué a qué fue el ajuste. Hasta ahora decía "queso -> borona" porque era lo único
    # que podía ser; ahora lo dice la fila.
    producto_origen: str
    producto_destino: str | None = None
    precio_kilo: Plata
    observaciones: str | None


# ----------------------------------------------------------------- resumen
class GananciaProducto(BaseSchema):
    """Una línea del desglose de la ganancia del período: a dónde fue lo que se
    compró (vendido como queso, pasado a borona, perdido como merma, vendido como
    mozzarella o todavía en inventario) y cuánta plata dejó cada destino.

    CADA RENGLÓN TIENE SU PROPIA UNIDAD y las cantidades NO se suman entre
    renglones de unidades distintas. La forma de garantizarlo aquí es la misma que
    en las tablas: los kilos viajan en los campos de kilos y las barras en los de
    barras, y en un renglón de barras los campos de kilos van en CERO. Así, si
    alguna pantalla suma la columna `kilos` de todos los renglones, lo que le sale
    son kilos de verdad y no una cifra sin significado.

    `unidad` dice cuál de los dos pares mirar. Los pesos (ingreso, costo, gastos,
    ganancia) sí son comparables y sumables entre todos los renglones.
    """

    producto: str  # 'queso'|'borona'|'merma'|'pendiente'|'anterior'|'mozzarella'|'mozzarella_pendiente'
    etiqueta: str  # texto listo para mostrar en la UI
    nota: str  # sub-texto explicativo corto
    unidad: str  # 'kg' o 'barra': en qué se mide ESTE renglón
    # Kilos DEL LOTE COMPRADO que fueron a este destino (siempre >= 0). Los cuatro
    # destinos en kilos suman exactamente kilos_comprados. En los renglones de
    # mozzarella va 0.
    kilos: EnKilos
    # Kilos realmente VENDIDOS de este producto. En el queso es igual a `kilos`;
    # en la borona puede diferir (se puede vender borona convertida en otro
    # período, o la que llegó gratis con el lote). En merma/residuo es 0.
    kilos_vendidos: EnKilos
    # Barras compradas que fueron a este destino, y barras vendidas. Solo tienen
    # valor en los renglones de mozzarella; en los de kilos van en 0.
    barras: EnUnidades = Decimal("0")
    barras_vendidas: EnUnidades = Decimal("0")
    ingreso: Plata
    costo: Plata  # negativo solo en la fila 'anterior' (se pagó en otro período)
    gastos: Plata
    ganancia: Plata  # ingreso - costo - gastos
    precio_venta_kilo: Plata  # ingreso / kilos_vendidos (0 si no se vendió)
    costo_kilo: Plata  # precio promedio de compra del período, por kilo
    # Los mismos dos precios pero POR BARRA. Van en campos aparte y no
    # reutilizando los de arriba porque un precio por barra guardado en un campo
    # que se llama "por kilo" es la confusión que hay que evitar.
    precio_venta_barra: Plata = Decimal("0")
    costo_barra: Plata = Decimal("0")


class GananciaProductor(BaseSchema):
    """Ganancia ESTIMADA de lo comprado a un productor en el período.

    El reparto se hace POR UNIDAD y por separado: el neto que dejaron las ventas
    en kilos se reparte entre los kilos comprados, y el de las ventas en barras
    entre las barras compradas. No hay otra forma de que cuadre: si se repartiera
    todo el neto entre los kilos, a un productor que solo vendió barras le saldría
    ganancia cero y su plata se le acreditaría a los de kilos.

    Las dos partes se SUMAN en `ganancia_estimada` porque son pesos, y la suma de
    la columna sigue dando la ganancia neta del período. Las cantidades (`kilos` y
    `barras`) van en columnas separadas y nunca se suman entre sí.
    """

    productor: str
    compras: int  # cuántas compras en el período
    kilos: EnKilos
    barras: EnUnidades = Decimal("0")
    total_comprado: Plata  # valor de TODAS sus compras (NO es lo que se le ha pagado)
    # De ese total, lo que corresponde a las compras de mozzarella. Va aparte para
    # poder verificar a mano que precio_promedio_barra = esto / barras.
    total_comprado_barras: Plata = Decimal("0")
    precio_promedio: Plata  # (total comprado en kilos) / kilos
    precio_promedio_barra: Plata = Decimal("0")  # total_comprado_barras / barras
    por_pagar: Plata  # saldo pendiente con ese productor (histórico)
    margen_por_kilo: Plata  # valor realizado por kilo - su precio promedio por kilo
    margen_por_barra: Plata = Decimal("0")  # el mismo margen, por barra
    ganancia_estimada: Plata  # la de kilos MÁS la de barras (pesos con pesos)


class ExistenciaProducto(BaseSchema):
    """LO QUE HAY EN BODEGA DE UN PRODUCTO, en su unidad.

    POR QUÉ ESTA LISTA EXISTE. El resumen traía tres cifras de inventario con el
    nombre del producto pegado en el campo: `kilos_disponibles` (el queso),
    `borona_disponible` y `barras_disponibles` (la mozzarella). Eran los únicos tres
    productos que el módulo entendía, y cualquier producto que el dueño agregara al
    catálogo no tenía dónde salir: su mercancía no aparecía en ninguna pantalla y se
    podía despachar sin límite.

    Esta lista trae UNA FILA POR PRODUCTO del catálogo, siempre —aunque esté en cero,
    porque "no hay" es una respuesta que el dueño necesita ver— y es de donde la
    pantalla tiene que leer el inventario de aquí en adelante. Los tres campos de
    siempre siguen viajando en el resumen y siguen diciendo exactamente lo que
    decían, para no romper la pantalla que ya los lee.

    `disponible` está en la unidad de SU producto y no se suma con la de otro: kilos
    con kilos y unidades con unidades, y solo del mismo producto.
    """

    # La clave del producto (la misma que llevan las filas de compra y de venta).
    producto: str
    # El nombre que el dueño le puso en el catálogo, listo para mostrar.
    etiqueta: str
    unidad: str  # 'kg' (se pesa) o 'unidad' (se cuenta)
    disponible: Decimal

    @field_serializer("disponible", when_used="json")
    def _escribir_disponible(self, valor: Decimal) -> str:
        """Con dos decimales si se pesa y sin ninguno si se cuenta.

        Es el único campo del módulo cuya escala depende de OTRO campo de la misma
        fila, y por eso lleva serializador propio en vez de una anotación: "0,00
        huevos" no se lee, y "3 kg" de un producto que se pesa oculta los gramos.
        """
        if self.unidad == UNIDAD_KILO:
            return _con_dos_decimales(valor)
        return _sin_decimales(valor)


class ResumenReventa(BaseSchema):
    """El resumen del período.

    CÓMO LEER LAS CANTIDADES: los campos que dicen `kilos_*` son kilos y los que
    dicen `barras_*` son barras, y NUNCA hay un campo que pueda ser lo uno o lo
    otro. No existe ni existirá un "total de unidades" que las junte: 20 kg de
    queso y 8 barras de mozzarella no son 28 de nada.

    LA PLATA SÍ SE SUMA. `total_compras`, `total_ventas`, `total_gastos` y
    `ganancia_estimada` incluyen TODOS los productos y las dos unidades, porque los
    pesos son pesos.

    QUÉ ES `por_producto` Y POR QUÉ ES LA CIFRA QUE MANDA. Es el desglose de la
    ganancia con UNA FILA POR PRODUCTO, armado solo a partir del catálogo, y cumple la
    regla de la casa por construcción:

        Σ costo de por_producto    = total_compras
        Σ ingreso de por_producto  = total_ventas
        Σ gastos de por_producto   = total_gastos
        Σ ganancia de por_producto = ganancia_estimada

    Esas cuatro igualdades son EXACTAS, sin un centavo de diferencia, y son las que el
    dueño verifica con la calculadora. Si algún peso no cupiera en ninguna fila de
    producto, sale en una fila propia llamada 'sin_producto' en vez de desaparecer.

    QUÉ SIGNIFICAN LOS CAMPOS `barras_*` Y `*_mozzarella`: el producto por unidad DE
    SIEMPRE, o sea LA MOZZARELLA, nombrada por su clave y no por su puesto en la
    lista (agregar una panela por unidad y ponerla de primera le cambiaba a estas
    tarjetas las barras compradas, las vendidas y su ganancia). NO son la
    suma de todos los productos por unidad, y es a propósito: sumar panelas con barras
    de mozzarella daría una cantidad que no significa nada y un "precio promedio por
    barra" que promedia $3.000 con $12.000. La plata de los demás productos por unidad
    sí entra en los totales de arriba y cada uno tiene su fila en `por_producto`.

    Los campos en kilos (`kilos_comprados`, `kilos_vendidos`, `kilos_pendientes`) SÍ
    suman todos los productos que se pesan, porque los kilos son kilos. Lo que se
    vendió de cada uno sale en su fila de `por_producto`.
    """

    desde: date
    hasta: date
    # Del período (queso)
    kilos_comprados: EnKilos
    total_compras: Plata  # TODA la plata comprada: kilos + barras
    kilos_vendidos: EnKilos  # solo ventas tipo queso
    total_ventas: Plata  # queso + borona + mozzarella (pesos con pesos)
    precio_promedio_compra: Plata  # por KILO: (compras en kilos) / kilos_comprados
    precio_promedio_venta: Plata  # solo queso
    total_gastos: Plata  # gastos de venta del período (transporte, etc.)
    ganancia_estimada: Plata  # ventas totales - compras del período - gastos
    # Ganancia neta por kilo vendido (queso + borona). Solo mira la plata de las
    # ventas en KILOS: meterle la de la mozzarella daría pesos por kilo inflados
    # con plata que no salió de ningún kilo.
    margen_por_kilo: Plata
    # Lo neto que dejó cada kilo comprado en el período: (ventas en kilos - gastos
    # de esas ventas) / kilos comprados. Es la base para repartir la ganancia de
    # los kilos entre los productores.
    valor_realizado_kilo: Plata
    # Del período (borona)
    kilos_borona_vendidos: EnKilos
    total_ventas_borona: Plata
    # Del período (MOZZARELLA, en barras: su propio renglón de punta a punta)
    barras_compradas: EnUnidades = Decimal("0")
    total_compras_mozzarella: Plata = Decimal("0")
    barras_vendidas: EnUnidades = Decimal("0")
    total_ventas_mozzarella: Plata = Decimal("0")
    total_gastos_mozzarella: Plata = Decimal("0")
    precio_promedio_compra_barra: Plata = Decimal("0")
    precio_promedio_venta_barra: Plata = Decimal("0")
    margen_por_barra: Plata = Decimal("0")  # ganancia de la mozzarella / barras vendidas
    valor_realizado_barra: Plata = Decimal("0")
    # Residuo CON SIGNO de las barras: compradas - vendidas en el período.
    barras_pendientes: EnUnidades = Decimal("0")
    # Del período (ajustes del inventario de queso; la mozzarella no participa)
    kilos_a_borona: EnKilos  # queso pasado a borona
    kilos_merma: EnKilos  # LA MERMA REAL: ajustes con destino merma
    # Residuo CON SIGNO del lote comprado: comprado - vendido como queso -
    # pasado a borona - merma. Negativo = se movió queso de otra temporada.
    kilos_pendientes: EnKilos
    # Desgloses de la ganancia del período
    por_producto: list[GananciaProducto] = []
    por_productor: list[GananciaProductor] = []
    # Acumulados (histórico, sin filtro de fechas)
    # LOS TRES DE SIEMPRE, cada uno el inventario de SU producto y nombrado por su
    # CLAVE ('queso', 'borona', 'mozzarella'), que es como se llaman estos tres campos.
    # No son "el primero del catálogo que se pesa" ni "el primero que se cuenta": eso
    # dependía del ORDEN, que es presentación, y agregar un producto y ponerlo de
    # primero le cambiaba a estas tarjetas lo que dicen. Siguen aquí porque la pantalla
    # los lee. El inventario de CUALQUIER producto sale en `existencias`.
    kilos_disponibles: EnKilos  # queso: comprados netos - vendidos - ajustados
    borona_disponible: EnKilos  # de compras + conversiones - vendida
    # Unidades disponibles del producto por unidad de siempre: compradas - vendidas.
    # Su propio renglón, con su propia unidad, jamás sumada con las dos de arriba.
    barras_disponibles: EnUnidades = Decimal("0")
    # EL INVENTARIO POR PRODUCTO, uno por fila y en la unidad de cada uno. Es de aquí
    # de donde la pantalla tiene que leer las existencias: los tres campos de arriba
    # solo hablan de los tres productos con los que nació el módulo.
    existencias: list[ExistenciaProducto] = []
    # Las dos cifras de cartera INCLUYEN los saldos de la cuenta anterior: es lo
    # que de verdad se debe cobrar y pagar hoy. Los dos campos de abajo son ese
    # pedazo por separado, para poder mostrar el desglose y que se vea de dónde
    # sale la suma. Ojo: los saldos anteriores NO tocan kilos ni ganancia.
    por_pagar_productores: Plata
    por_cobrar_clientes: Plata
    por_cobrar_libro_anterior: Plata = Decimal("0")
    por_pagar_libro_anterior: Plata = Decimal("0")


class SugerenciasReventa(BaseSchema):
    """Nombres ya registrados para autocompletar al crear compras/ventas."""

    productores: list[str]
    clientes: list[str]


# ------------------------------------------------------- estado de cuenta
# CONFIDENCIALIDAD: el estado de cuenta SE LE ENTREGA AL CLIENTE (se le manda por
# WhatsApp). Por eso estos esquemas NO llevan gasto_concepto, gasto_por_kilo,
# gasto_monto, "venta libre", costos de compra, productores, márgenes ni las
# observaciones de la venta o del abono: serían los números internos de la
# quesera y le revelarían su ganancia al cliente.
class EstadoCuentaVenta(BaseSchema):
    """Una compra que le hicimos al cliente, con lo que lleva abonado.

    La cantidad va en el campo de SU unidad (kilos o barras) y el otro en cero, y
    `unidad` dice cuál mirar. Le importa al cliente: si su fila de mozzarella
    dijera "0 kg" no reconocería su propia compra, y si dijera "100 kg" por 100
    barras el documento estaría mintiendo sobre lo que se le despachó.
    """

    fecha: date
    tipo: str  # 'queso' | 'borona' | 'mozzarella'
    producto: str  # 'Queso' | 'Borona' | 'Mozzarella' (listo para mostrar)
    unidad: str  # 'kg' | 'barra'
    kilos: EnKilos
    precio_kilo: Plata
    barras: EnUnidades = Decimal("0")
    precio_barra: Plata = Decimal("0")
    valor_total: Plata
    abonado: Plata
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")
    estado: str  # pendiente | parcial | pagada


class EstadoCuentaPago(BaseSchema):
    """Un abono recibido del cliente (sin importar a qué venta se aplicó).

    NO lleva `observaciones` A PROPÓSITO: la observación del abono es la nota
    INTERNA que la quesera se escribe a sí misma ("le rebajé el flete", "a tal
    productor le pagamos tanto el kilo"), y este esquema se le entrega al
    cliente. Se filtraba el flete, el nombre del productor y el precio de compra.
    Si algún día se quiere mostrarle al cliente una referencia del pago (número
    de consignación, banco), va en un campo NUEVO pensado para eso y llenado
    para él, nunca reutilizando el interno.
    """

    fecha: date
    valor: Plata


class EstadoCuentaSaldoAnterior(BaseSchema):
    """Una cuenta a medio pagar que el cliente traía del sistema anterior.

    Solo lleva lo que el cliente reconoce de su propia deuda: la fecha del
    documento viejo, de qué era, cuánto valía, cuánto abonó y cuánto queda. Las
    `observaciones` del saldo NO salen: son la nota interna de la quesera, igual
    que en EstadoCuentaPago.
    """

    fecha: date
    concepto: str
    valor_total: Plata
    abonado: Plata
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")


class EstadoCuentaCliente(BaseSchema):
    cliente: str
    desde: date | None
    hasta: date | None
    emitido: date  # fecha de generación
    compras: int  # cuántas ventas se le hicieron (las del sistema, no las del libro)
    # LAS DOS CANTIDADES VAN SEPARADAS y no existe un total que las junte: si un
    # cliente compró 40 kg de queso y 8 barras, "48" no es nada. `total_kilos` NO
    # incluye barras y `total_barras` no incluye kilos, así el cliente reconoce en
    # el documento exactamente lo que le despacharon.
    total_kilos: EnKilos
    total_barras: EnUnidades = Decimal("0")
    # `total_facturado` y `total_abonado` son SOLO del sistema; lo que venía del
    # libro anterior va aparte en los tres campos libro_anterior_*.
    total_facturado: Plata
    total_abonado: Plata
    # TODO lo que el cliente debe hoy, que es la única cifra que le importa:
    #   (total_facturado - total_abonado) + libro_anterior_saldo = saldo
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")
    ventas: list[EstadoCuentaVenta] = []
    pagos: list[EstadoCuentaPago] = []
    # Lo que traía debiendo del sistema anterior (vacío para casi todos)
    saldos_anteriores: list[EstadoCuentaSaldoAnterior] = []
    libro_anterior_total: Plata = Decimal("0")
    libro_anterior_abonado: Plata = Decimal("0")
    libro_anterior_saldo: Plata = Decimal("0")


# --------------------------------------- estado de cuenta DEL PRODUCTOR
# CONFIDENCIALIDAD, AL REVÉS QUE EN EL DEL CLIENTE: este documento SE LE ENTREGA
# AL PRODUCTOR para cuadrar cuentas con él. Por eso estos esquemas NO llevan
# NADA del lado de la venta: ni a qué precio se revendió su queso, ni
# total_ventas, ni precio_promedio_venta, ni valor_realizado_kilo, ni márgenes,
# ni ganancia, ni los gastos de venta (flete), ni el nombre de ningún CLIENTE.
# Tampoco llevan los saldos del libro anterior de tipo 'cobrar': esos son deudas
# de CLIENTES con la quesera y no tienen nada que ver con un productor.
# Solo va lo que es suyo: lo que le compraron, lo que le pagaron y lo que se le
# debe.
#
# OJO CON LOS SIGNOS, que van al contrario del documento del cliente: allá un
# saldo positivo significa que ÉL DEBE; aquí un saldo positivo significa que LA
# QUESERA LE DEBE A ÉL. Siempre va rotulado ("saldo a favor del productor"), o
# se lee invertido.
class EstadoCuentaCompra(BaseSchema):
    """Una compra que se le hizo al productor, con lo que lleva abonado.

    La cantidad va en el campo de SU unidad y el otro en cero, igual que en el
    documento del cliente: `unidad` dice cuál mirar. Al productor le importa igual
    o más que al cliente: si su fila de mozzarella dijera "0 kg" no reconocería la
    entrega que él mismo hizo, y cuadrar cuentas con él terminaría en discusión.
    """

    fecha: date
    tipo: str = "queso"  # 'queso' (kg) | 'mozzarella' (barras)
    unidad: str = "kg"  # 'kg' | 'barra'
    kilos: EnKilos  # kilos_netos: los que se le pagan
    borona_kilos: EnKilos  # la borona que vino con el lote (no se paga); 0 si no hubo
    precio_kilo: Plata
    # En una compra de kilos van en CERO (lo exige el CHECK de la tabla).
    barras: EnUnidades = Decimal("0")
    precio_barra: Plata = Decimal("0")
    valor_total: Plata
    abonado: Plata
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")
    estado: str  # pendiente | parcial | pagada


class EstadoCuentaPagoProductor(BaseSchema):
    """Un pago que se le hizo al productor (sin importar a qué compra se aplicó).

    NO lleva `observaciones` A PROPÓSITO, por la misma razón que
    EstadoCuentaPago: la observación del abono es la nota INTERNA que la quesera
    se escribe a sí misma, y este esquema se le entrega al productor. Ya hubo un
    incidente por esto en el documento del cliente y se quitó; no se repite.
    """

    fecha: date
    valor: Plata


class EstadoCuentaProductor(BaseSchema):
    productor: str
    desde: date | None
    hasta: date | None
    emitido: date  # fecha de generación
    compras: int  # cuántas compras se le hicieron (las del sistema, no las del libro)
    total_kilos: EnKilos  # kilos netos, los que se le pagan (NO incluye barras)
    total_barras: EnUnidades = Decimal("0")  # barras de mozzarella, su propio total
    # `total_comprado` y `total_pagado` son SOLO del sistema; lo que venía del
    # libro anterior va aparte en los tres campos libro_anterior_*.
    total_comprado: Plata  # lo que valen sus compras
    total_pagado: Plata  # lo que se le ha abonado
    # Lo que traía a medio pagar del sistema anterior: SOLO los saldos de tipo
    # 'pagar' (los de tipo 'cobrar' son deudas de clientes y no entran aquí).
    saldos_anteriores: list[EstadoCuentaSaldoAnterior] = []
    libro_anterior_total: Plata = Decimal("0")
    libro_anterior_abonado: Plata = Decimal("0")
    libro_anterior_saldo: Plata = Decimal("0")
    # TODO lo que se le debe hoy, que es la única cifra que le importa a él:
    #   (total_comprado - total_pagado) + libro_anterior_saldo = saldo
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")
    compras_detalle: list[EstadoCuentaCompra] = []
    pagos: list[EstadoCuentaPagoProductor] = []


# -------------------------------------------------------------- temporadas
class TemporadaCreate(BaseSchema):
    """`fecha_fin` en null crea la temporada ABIERTA (la que está corriendo)."""

    nombre: str = Field(min_length=2, max_length=80)
    fecha_inicio: date
    fecha_fin: date | None = None
    notas: str | None = Field(default=None, max_length=500)


class TemporadaUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=80)
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    notas: str | None = Field(default=None, max_length=500)


class TemporadaCerrar(BaseSchema):
    """Cerrar la temporada es ponerle fecha de fin. Por defecto, hoy."""

    fecha_fin: date | None = None


class TemporadaRead(TenantRead):
    nombre: str
    fecha_inicio: date
    fecha_fin: date | None
    notas: str | None
    abierta: bool


class TemporadaResumen(BaseSchema):
    """Una temporada con sus cifras, calculadas con el MISMO motor del resumen.

    Ojo con de dónde sale cada cifra, que es lo que hace que se pueda cuadrar:

    - Todo lo de plata y de kilos es DEL PERÍODO de la temporada, sacado de
      `ReventaResumenService.resumen(fecha_inicio, fecha_fin)`. Por eso `ganancia`
      es exactamente la misma cifra que muestra el Resumen si se filtra a esas
      fechas: es la misma función, no una copia.
    - `por_cobrar` y `por_pagar` son también SOLO de los documentos de esas
      fechas, no la cartera de siempre. Si no, una temporada vieja ya cobrada
      aparecería con deuda por culpa de la que está corriendo.
    - `por_cobrar`/`por_pagar` NO incluyen el libro anterior: esas cuentas vienen
      de otro sistema, no tienen kilos y no pertenecen a ninguna temporada.
    """

    id: uuid.UUID
    nombre: str
    fecha_inicio: date
    # En la temporada abierta es la fecha con la que se calculó (hoy), para que en
    # pantalla se vea hasta dónde llegan las cifras y no parezcan de todo el año.
    fecha_fin: date
    abierta: bool
    dias: int
    notas: str | None
    # Kilos
    kilos_comprados: EnKilos
    kilos_vendidos: EnKilos
    kilos_borona_vendidos: EnKilos
    kilos_a_borona: EnKilos
    kilos_merma: EnKilos
    kilos_pendientes: EnKilos
    # Barras (mozzarella): su propio renglón, nunca sumado con los kilos de arriba
    barras_compradas: EnUnidades = Decimal("0")
    barras_vendidas: EnUnidades = Decimal("0")
    barras_pendientes: EnUnidades = Decimal("0")
    # Plata (incluye las dos unidades: los pesos son pesos)
    total_compras: Plata
    total_ventas: Plata
    total_gastos: Plata
    ganancia: Plata
    margen_por_kilo: Plata
    precio_promedio_compra: Plata
    precio_promedio_venta: Plata
    # Los precios promedio de la mozzarella, por BARRA
    precio_promedio_compra_barra: Plata = Decimal("0")
    precio_promedio_venta_barra: Plata = Decimal("0")
    # Lo que falta de ESTA temporada
    por_cobrar: Plata
    por_pagar: Plata
    # Si ya no falta nada: sin queso pendiente, sin cobrar y sin pagar
    cerrada_de_verdad: bool


class TemporadasPanel(BaseSchema):
    """Lo que necesita la pantalla de temporadas en una sola llamada.

    Los totales son la SUMA de las temporadas listadas, no el histórico completo:
    lo que está fuera de toda temporada no puede aparecer sumado aquí porque
    entonces la lista no daría el total y ese es justo el desglose que el usuario
    revisa con calculadora. `dias_sin_temporada` avisa de esos huecos.
    """

    temporadas: list[TemporadaResumen] = []
    # Suma de las temporadas de la lista
    total_ganancia: Plata
    total_kilos_comprados: EnKilos
    total_ventas: Plata
    total_compras: Plata
    # La mejor y la peor por ganancia (null si no hay ninguna temporada)
    mejor: str | None = None
    peor: str | None = None
    # Días con movimientos que no caen en ninguna temporada
    dias_sin_temporada: int = 0
    # Inicio que se propone para la próxima (día siguiente al último cierre)
    proximo_inicio: date | None = None


# -------------------------------------------------------------------- lotes
class CompraDelLoteRead(BaseSchema):
    """Una compra dentro del lote, con lo que dejaron SUS kilos.

    La ganancia es EXACTA, no la del lote repartida a prorrata: son los kilos de
    este productor costeados al precio que se le pagó a él. Por eso dos productores
    del mismo lote pueden tener margen distinto, y por eso la suma de estas
    ganancias da la del lote sin sobrar ni faltar un peso.
    """

    productor: str
    kilos: EnKilos
    borona_recibida: EnKilos
    precio_kilo: Plata
    valor_total: Plata
    saldo: Plata
    saldo_a_favor: Plata = Decimal("0")  # lo que falta pagarle por esta compra
    # A dónde fueron SUS kilos (los cuatro suman `kilos`)
    kilos_vendidos: EnKilos
    kilos_a_borona: EnKilos
    kilos_merma: EnKilos
    kilos_sin_vender: EnKilos
    borona_vendida: EnKilos
    borona_sin_vender: EnKilos
    # Plata
    ingresos: Plata
    gastos: Plata
    costo_realizado: Plata  # lo que costó lo que ya salió (vendido + merma)
    costo_sin_vender: Plata
    ganancia: Plata
    margen_kilo: Plata


class GananciaDia(BaseSchema):
    """Lo que dejó un día concreto: lo vendido ese día menos lo que costó."""

    fecha: date
    kilos: EnKilos
    ingresos: Plata
    costo: Plata  # lo que había costado ESE queso (reparto FIFO, no promedio)
    gastos: Plata  # fletes de los despachos de ese día
    ganancia: Plata


class GananciaPorDia(BaseSchema):
    """Ganancia real entre dos fechas, día por día.

    NO es la del resumen del período ("ventas menos compras"), que sale negativa
    cuando se compra mucho y se vende poco aunque no se haya perdido nada. Aquí
    las compras no restan: comprar es cambiar plata por queso, no gastarla.

    Los días SUMAN los totales: el total se calcula sumándolos, no aparte.
    """

    desde: date
    hasta: date
    dias: list[GananciaDia] = []
    kilos: EnKilos
    ingresos: Plata
    costo: Plata
    gastos: Plata
    ganancia: Plata


class VentaDelLoteRead(BaseSchema):
    """Una venta que se llevó kilos de este lote.

    `kilos` son los que salieron de ESTE lote y `kilos_venta` los de la venta
    completa: una venta grande se parte entre varios lotes, y mostrar solo los
    primeros haría creer que la venta fue más pequeña de lo que fue.
    """

    fecha: date
    cliente: str
    tipo: str  # 'queso' | 'borona'
    kilos: EnKilos
    kilos_venta: EnKilos
    precio_kilo: Plata
    ingreso: Plata  # la parte de la venta que le corresponde a este lote
    gasto: Plata
    costo: Plata
    ganancia: Plata
    partida: bool  # la venta se repartió con otros lotes


class LoteResumen(BaseSchema):
    """Un lote de compra: todas las compras de queso de una misma fecha.

    Así lo ve el usuario: "la compra del 25" es un lote y "las compras del 18" es
    otro, aunque cada uno tenga varios productores.

    Las ventas no dicen de qué lote salió el queso, así que se reparten FIFO: el
    queso se vende del lote más viejo primero, que es lo que pasa en la bodega
    porque el queso es perecedero. Cada lote tiene su propio costo por kilo, y a
    una venta que se lleva K kilos de un lote se le carga K x ese costo.

    Ojo con `ganancia`: es la de lo que YA se realizó (vendido y perdido), y NO le
    resta el costo de lo que sigue en inventario. Restárselo haría que un lote
    recién comprado apareciera con una pérdida enorme el mismo día, cuando lo que
    pasa es que todavía no se ha vendido. Lo que sí se le resta es la merma, que
    es plata perdida de ese lote.
    """

    fecha: date
    productores: list[str] = []
    compras: int
    # Lo comprado
    kilos_comprados: EnKilos
    costo_total: Plata
    costo_kilo: Plata  # costo_total / kilos_comprados
    por_pagar: Plata  # lo que falta pagarles a los productores de ESTE lote
    borona_recibida: EnKilos  # la que llegó con el lote y no se paga
    # A dónde fue el queso del lote (los cuatro suman kilos_comprados)
    kilos_vendidos: EnKilos
    kilos_a_borona: EnKilos
    kilos_merma: EnKilos
    kilos_sin_vender: EnKilos
    # Borona del lote: la recibida gratis más la que salió de su propio queso
    borona_vendida: EnKilos
    borona_sin_vender: EnKilos
    # Plata
    ingreso_queso: Plata
    ingreso_borona: Plata
    ingresos: Plata  # queso + borona
    gastos: Plata
    costo_vendido: Plata
    costo_borona_vendida: Plata  # solo la borona que venía de queso (la gratis cuesta 0)
    costo_merma: Plata
    costo_sin_vender: Plata
    ganancia: Plata  # ingresos - costo_vendido - costo_borona_vendida - costo_merma - gastos
    margen_kilo: Plata  # ganancia / kilos vendidos (queso + borona)
    precio_venta_kilo: Plata  # ingreso_queso / kilos_vendidos
    # Si ya no queda nada del lote por vender
    cerrado: bool
    # El detalle: quién aportó qué y a quién se le vendió. Van en la misma
    # respuesta y no en otro endpoint porque el reparto FIFO ya los calculó: pedirlos
    # aparte obligaría a repetir el reparto completo, que es la parte costosa.
    detalle_compras: list[CompraDelLoteRead] = []
    detalle_ventas: list[VentaDelLoteRead] = []


class LotesPanel(BaseSchema):
    """Los lotes con lo que dejó cada uno.

    Los totales son la suma EXACTA de los lotes listados. `kilos_sin_lote` avisa
    de los kilos vendidos que no encontraron lote de dónde salir: se vendió más de
    lo comprado o se vendió antes de la primera compra registrada. No se esconden,
    porque significan que falta cargar una compra y que la cuenta está incompleta.

    ESTE PANEL ES SOLO DE KILOS. La mozzarella no entra en el reparto por lotes (el
    motor cuesta en kilos de punta a punta; ver
    `CompraQuesoRepository.eventos_para_lotes`), así que ninguna cifra de aquí la
    incluye. Eso NO se esconde: `barras_fuera_del_reparto` dice cuántas barras se
    han comprado que no están contadas en este panel, para que la pantalla lo
    advierta y mande a leer la ganancia de la mozzarella en el Resumen, que la
    tiene completa y en su propia unidad. Un cero significa que no hay nada afuera
    y el panel cubre todo el negocio.
    """

    lotes: list[LoteResumen] = []
    total_ganancia: Plata
    total_kilos_comprados: EnKilos
    total_costo: Plata
    total_ingresos: Plata
    total_por_pagar: Plata
    # Todavía sin vender, de todos los lotes juntos
    total_kilos_sin_vender: EnKilos
    total_costo_sin_vender: Plata
    mejor: date | None = None
    peor: date | None = None
    # Kilos vendidos o ajustados sin lote de origen (falta cargar una compra)
    kilos_sin_lote: EnKilos
    borona_sin_lote: EnKilos
    ingreso_sin_lote: Plata
    # Barras compradas (histórico) que NO están contadas en este panel. No es un
    # error como `kilos_sin_lote`: es un aviso de alcance. Ver el docstring.
    barras_fuera_del_reparto: EnUnidades = Decimal("0")
