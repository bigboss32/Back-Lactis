"""Reventa de queso: compras a productores con merma y abonos, ventas a
clientes y resumen de ganancia. Contabilidad separada del libro de la quesera.
"""
import os
import re
import unicodedata
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.models import ESTADO_ACTIVO, ESTADO_INACTIVO
from app.common.nombres import canonizar_nombre, clave_de_tercero, unir_nombres
from app.common.service import BaseService, serialize_entity
from app.core.config import settings
from app.core.exceptions import BusinessError, ConflictError, NotFoundError
from app.core.logging_config import get_logger
from app.core.pagination import PageParams
from app.core.storage import (
    MENSAJE_NO_CONFIGURADO,
    R2Client,
    caducidad_utc,
    r2_configurado,
    texto_caducidad,
)
from app.modules.empresas.repository import EmpresaRepository
from app.modules.reventa.models import (
    DESTINO_MERMA,
    ESTADO_ANULADA,
    ESTADO_PAGADA,
    ESTADO_PARCIAL,
    ESTADO_PENDIENTE,
    TIPO_DOC_COMPRA,
    TIPO_DOC_VENTA,
    TIPO_MOZZARELLA,
    TIPO_SALDO_COBRAR,
    TIPO_SALDO_PAGAR,
    TIPO_VENTA_BORONA,
    TIPO_VENTA_MOZZARELLA,
    TIPO_VENTA_QUESO,
    UNIDAD_BARRA,
    UNIDAD_KILO,
    AbonoCompraQueso,
    AbonoSaldoAnterior,
    AbonoVentaQueso,
    AdjuntoReventa,
    CompraQueso,
    ConversionBorona,
    DocumentoReventa,
    ProductoReventa,
    SaldoAnterior,
    Temporada,
    VentaQueso,
    derivados_de_unidad,
    unidad_de,
)
from app.modules.reventa.lotes import (
    AjusteEvento,
    CompraEvento,
    LoteCalculado,
    VentaEvento,
    repartir_lotes,
)
from app.modules.reventa.repository import (
    AdjuntoReventaRepository,
    CompraQuesoRepository,
    ConversionBoronaRepository,
    DocumentoReventaRepository,
    ProductoReventaRepository,
    SaldoAnteriorRepository,
    TemporadaRepository,
    VentaQuesoRepository,
)
from app.modules.reventa.schemas import (
    AdjuntoRead,
    AdjuntosLista,
    CompraQuesoRead,
    DocumentoCompraCreate,
    DocumentoReventaRead,
    DocumentoVentaCreate,
    EnlaceCompartido,
    RenglonCompraCreate,
    RenglonVentaCreate,
    VentaQuesoRead,
    EstadoCuentaCliente,
    GananciaDia,
    GananciaPorDia,
    EstadoCuentaCompra,
    EstadoCuentaPago,
    EstadoCuentaPagoProductor,
    EstadoCuentaProductor,
    EstadoCuentaSaldoAnterior,
    EstadoCuentaVenta,
    GananciaProducto,
    GananciaProductor,
    ResumenReventa,
    CompraDelLoteRead,
    LoteResumen,
    LotesPanel,
    VentaDelLoteRead,
    SugerenciasReventa,
    TemporadaResumen,
    TemporadasPanel,
)
from app.utils.export import (
    build_estado_cuenta_pdf,
    build_estado_cuenta_productor_pdf,
    pesos,
)

CERO = Decimal("0")
DOS_DECIMALES = Decimal("0.01")


def _dinero(valor: Decimal) -> Decimal:
    """Redondea a centavos. Se usa al FINAL, nunca antes de multiplicar: en el
    detalle por productor, cuantizar antes de multiplicar dejaba la columna
    cinco pesos por debajo de la cifra grande."""
    return Decimal(valor).quantize(DOS_DECIMALES)

# Textos del desglose por producto (los ve el usuario final tal cual)
ETIQUETAS_PRODUCTO = {
    "queso": "Vendido como queso",
    "borona": "Vendido como borona",
    "merma": "Merma (pérdida real)",
    "pendiente": "Aún en inventario",
    # Ojo: el residuo negativo NO significa "vendido". Puede ser queso de una
    # temporada anterior que se vendió, se pasó a borona o se perdió. El texto
    # es neutro a propósito: afirmar una venta sería la misma mentira que se
    # arregló con la merma.
    "anterior": "Salió de inventario anterior",
    # Los dos de la mozzarella dicen BARRAS en la etiqueta misma, no solo en la
    # unidad: el dueño lee el renglón de un vistazo y tiene que ver ahí que esa
    # cantidad no son kilos, sin tener que cruzarla con otra columna.
    "mozzarella": "Mozzarella vendida (barras)",
    "mozzarella_pendiente": "Mozzarella aún en inventario (barras)",
    "mozzarella_anterior": "Barras salidas de inventario anterior",
}
NOTAS_PRODUCTO = {
    "queso": "vendido como queso entero",
    "borona": "subproducto vendido más barato",
    "merma": "se pagó y no se vendió: pérdida",
    "pendiente": "plata invertida, aún sin vender",
    "anterior": "se compró en un período anterior",
    "mozzarella": "se compra y se vende por barra completa",
    "mozzarella_pendiente": "barras compradas y todavía sin vender",
    "mozzarella_anterior": "barras compradas en un período anterior",
}
# Los renglones que se miden en BARRAS. Se listan en un solo sitio para que
# ninguna parte del código tenga que acordarse de la regla por su cuenta.
PRODUCTOS_EN_BARRAS = frozenset(
    {"mozzarella", "mozzarella_pendiente", "mozzarella_anterior"}
)
# Cuando unos kilos no tienen costo porque la compra cayó fuera del período, no
# se puede hablar de pérdida en pesos: se dice de dónde vienen.
NOTA_SIN_COSTO = "se compró en un período anterior: aquí no lleva costo"

# Nombre del producto listo para mostrarle al cliente en su estado de cuenta
NOMBRE_PRODUCTO = {
    TIPO_VENTA_QUESO: "Queso",
    TIPO_VENTA_BORONA: "Borona",
    TIPO_VENTA_MOZZARELLA: "Mozzarella",
}


def _nombre_archivo_cliente(cliente: str) -> str:
    """Nombre de archivo seguro para el estado de cuenta.

    El nombre del cliente es texto libre: si se colara una comilla o un salto de
    línea en el header Content-Disposition sería una inyección de cabecera HTTP.
    Se quitan los acentos (para que "Sebastián" siga siendo legible) y se borra
    todo lo que no sea alfanumérico, guion o guion bajo.
    """
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", cliente) if not unicodedata.combining(c)
    )
    limpio = re.sub(r"[^A-Za-z0-9_-]", "", "_".join(sin_acentos.split()))
    return f"estado_cuenta_{limpio or 'cliente'}.pdf"


def _nombre_archivo_productor(productor: str) -> str:
    """Nombre de archivo seguro para el estado de cuenta del productor.

    Mismo saneamiento que _nombre_archivo_cliente, y se deja aparte a propósito
    para no tocar el camino del cliente, que ya está desplegado y verificado. El
    nombre del productor es texto libre: si se colara una comilla o un salto de
    línea en el header Content-Disposition sería una inyección de cabecera HTTP.
    Se quitan los acentos (para que "Sebastián" siga siendo legible) y se borra
    todo lo que no sea alfanumérico, guion o guion bajo.
    """
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", productor) if not unicodedata.combining(c)
    )
    limpio = re.sub(r"[^A-Za-z0-9_-]", "", "_".join(sin_acentos.split()))
    return f"estado_cuenta_productor_{limpio or 'productor'}.pdf"


# La canonización de nombres de terceros vive ahora en app/common/nombres.py: el
# flete por tramos necesitó exactamente la misma regla para el nombre del
# CONDUCTOR, y dos copias habrían significado que el mismo señor se unifica en
# una pantalla y se parte en dos en la otra. Se conservan estos nombres locales
# con guion bajo para no tocar las llamadas de este archivo, que ya están
# probadas y desplegadas.
_canonizar_nombre = canonizar_nombre
_clave_tercero = clave_de_tercero
_unir_nombres = unir_nombres


def _agrupar_pendientes(
    filas: list[tuple[str, Decimal]],
) -> dict[str, tuple[str, Decimal]]:
    """Agrupa filas (nombre del tercero, saldo) por tercero:
    clave normalizada -> (nombre como está escrito, saldo sumado).

    Las variantes de escritura que la base pudo dejar en grupos distintos se
    SUMAN en una sola entrada (el lower() de SQLite no baja acentos y el de
    Postgres sí). Los saldos en cero se descartan: no son plata pendiente y no
    merecen una fila propia en el detalle.
    """
    agrupados: dict[str, tuple[str, Decimal]] = {}
    for nombre, saldo in filas:
        if not saldo:
            continue
        clave = _clave_tercero(nombre)
        primero, acumulado = agrupados.get(clave, (nombre, CERO))
        agrupados[clave] = (primero, acumulado + saldo)
    return agrupados


def _estado_pago(valor_total: Decimal, abonado: Decimal) -> str:
    if abonado <= CERO:
        return ESTADO_PENDIENTE
    return ESTADO_PAGADA if abonado >= valor_total else ESTADO_PARCIAL


def _estado_pago_documento(renglones: list[Any]) -> str:
    """El estado de pago de una FACTURA, DEDUCIDO del de sus renglones.

    NO ES UNA COLUMNA, y esa es la decisión: `documentos_reventa` no guarda ni
    plata ni estado de pago. Se deduce cada vez, así que no puede quedar diciendo
    "pagada" cuando alguien le borró un abono a uno de sus renglones.

    Se deduce de los ESTADOS y no de comparar el abonado contra el total, y la
    diferencia se nota en un caso real: si a un renglón ya pagado se le rebaja el
    precio queda con saldo a favor, y ahí la suma de lo abonado puede alcanzar el
    total de la factura mientras otro renglón sigue debiendo. Mirar los estados
    dice la verdad ("parcial", todavía hay un producto sin pagar); mirar las sumas
    diría "pagada" y el dueño dejaría de cobrar una plata que le deben.

    Una factura sin ningún renglón vivo sin anular está ANULADA: no queda nada que
    cobrar ni que pagar.
    """
    vivos = [r for r in renglones if r.estado != ESTADO_ANULADA]
    if not vivos:
        return ESTADO_ANULADA
    estados = {r.estado for r in vivos}
    if estados == {ESTADO_PAGADA}:
        return ESTADO_PAGADA
    if estados == {ESTADO_PENDIENTE}:
        return ESTADO_PENDIENTE
    return ESTADO_PARCIAL


def _bloquear(db: Session, entidad: Any) -> Any:
    """Relee la fila con FOR UPDATE antes de tocarle la plata.

    Sin esto, dos abonos a la vez sobre la misma deuda se pisan: los dos leen
    `abonado` viejo, los dos validan contra el mismo saldo y el segundo escribe
    encima del primero. Se pierde un pago —el productor reclama y en el sistema
    no está— y la cartera deja de cuadrar.

    Dos detalles que ya nos costaron caro antes en este proyecto:

    - `populate_existing`: sin él, el FOR UPDATE bloquea la fila en la base pero
      SQLAlchemy devuelve el objeto que ya tenía en memoria, CON LOS VALORES
      VIEJOS. Y es peor de lo que suena: quien llega segundo se queda esperando
      el candado justo mientras el primero escribe, así que al soltarse tiene en
      la mano exactamente los datos de antes.
    - Las relaciones de abonos son lazy="selectin", no "joined". Importa: con un
      LEFT JOIN de por medio, Postgres rechaza el FOR UPDATE con 0A000.

    SQLite descarta el FOR UPDATE en silencio, así que la suite no delata nada
    de esto. La corrección se sostiene por lectura del código, no por la prueba.
    """
    return db.execute(
        select(type(entidad))
        .where(type(entidad).id == entidad.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).scalar_one()


def _campos_del_renglon(renglon: Any, esquema: type) -> dict[str, Any]:
    """Los campos DEL RENGLÓN y ni uno más, venga de donde venga.

    POR QUÉ SE FILTRA Y NO SE VUELVE A VALIDAR. La puerta plana entrega su propio
    payload como renglón —`CompraQuesoCreate` ES un `RenglonCompraCreate`, hereda
    de él—, y ese payload trae además la fecha, el nombre del tercero y
    `pagada_de_contado`, que son de la FACTURA. Si esos campos se colaran en el
    renglón, la fecha del renglón dejaría de venir de la cabecera (que es lo que
    mantiene los dos lados diciendo lo mismo) y `pagada_de_contado` reventaría el
    constructor del modelo, que no tiene esa columna.

    Y VOLVER A VALIDAR NO ES UNA OPCIÓN, aunque sea lo primero que uno escribe: el
    validador del renglón ya dejó en CERO la cantidad y el precio de la unidad que
    no aplica (barras en cero en una compra de kilos), y esos campos son `gt=0`.
    Validar de nuevo un payload ya validado lo rechazaría por los ceros que él
    mismo puso.
    """
    campos = set(esquema.model_fields)
    if isinstance(renglon, dict):
        return {k: v for k, v in renglon.items() if k in campos}
    return renglon.model_dump(include=campos)


def _borrar_cabecera_vacia(servicio: Any, documento_id: uuid.UUID | None) -> None:
    """Se lleva la cabecera cuando se le fue el ÚLTIMO renglón.

    Sin esto, la pantalla de facturas se llenaría de fantasmas: cabeceras sin
    renglones, con total en cero, que el dueño no puede abrir ni entender y que
    no sabría cómo quitar. Y pasaría todo el tiempo, no en un caso raro: borrar una
    compra mal registrada por la pantalla de siempre (`DELETE /reventa/compras/{id}`)
    deja exactamente eso, porque toda compra suelta es una factura de un renglón.

    Se borra EN SUAVE, como todo en el sistema, y queda en la auditoría.

    OJO CON LA DIFERENCIA ENTRE BORRAR Y ANULAR: una factura con todos sus renglones
    ANULADOS no se borra. Anular no es borrar —la plata anulada sigue saliendo en
    `total_anulado` para que la cuenta cierre—, y el dueño tiene que poder abrir esa
    factura y ver qué fue lo que anuló.
    """
    if documento_id is None:
        return
    documentos = DocumentoReventaService(servicio.db, servicio.ctx)
    documento = documentos.repo.get(documento_id)
    if documento is None or documentos.repo.renglones(documento):
        return
    antes = serialize_entity(documento)
    documentos.repo.soft_delete(documento, deleted_by=servicio.ctx.user_id)
    documentos._audit("eliminar", documento.id, antes, serialize_entity(documento))


def _cuidar_cabecera(
    servicio: Any, fila: Any, data: dict[str, Any], *, campo_tercero: str, que: str
) -> None:
    """Que un renglón y su factura nunca queden diciendo fechas o nombres distintos.

    EL PROBLEMA QUE RESUELVE. La fecha y el nombre del tercero viven en los DOS
    lados: en la cabecera (que es lo que el usuario ve en la lista de facturas) y
    copiados en cada renglón (que es de donde los leen el resumen, la cartera, los
    lotes y el estado de cuenta). Editar un renglón por la puerta plana
    —`PUT /reventa/compras/{id}`, que sigue viva— podía dejar los dos lados
    contradiciéndose: la factura diciendo "3 de mayo, Yeferson" y su renglón
    diciendo "10 de mayo, Marlion".

    LAS DOS SALIDAS, según cuántos renglones tenga la factura:

    - UN SOLO RENGLÓN (el caso de todo lo que se registra por la puerta plana y de
      todo lo que existía antes): se le cambia también a la cabecera. Por fuera no
      se nota nada, que es justo lo que se quiere: la puerta plana sigue
      comportándose igual que siempre.
    - VARIOS RENGLONES: se rechaza con un mensaje que dice qué hacer. Cambiarle la
      fecha a UN renglón de una factura de tres partiría la factura en dos fechas,
      y el dueño la vería en la pantalla de facturas con una fecha y en el resumen
      del día con otra. La cantidad y el precio de ese renglón SÍ se pueden editar:
      lo único que se protege es lo que es de la factura entera.

    Una fila con `documento_id` en nulo (borrada en suave, o histórica que la
    migración no alcanzó) no tiene cabecera que cuidar y sigue de largo.
    """
    nueva_fecha = data.get("fecha")
    cambia_fecha = nueva_fecha is not None and nueva_fecha != fila.fecha
    nuevo_tercero = data.get(campo_tercero)
    cambia_tercero = nuevo_tercero is not None and nuevo_tercero != getattr(
        fila, campo_tercero
    )
    if not (cambia_fecha or cambia_tercero) or fila.documento_id is None:
        return
    repo = DocumentoReventaRepository(servicio.db, servicio.ctx.empresa_id)
    documento = repo.get(fila.documento_id)
    if documento is None:
        return
    hermanos = [r for r in repo.renglones(documento) if r.id != fila.id]
    if hermanos:
        raise BusinessError(
            f"Esta {que} es uno de los {len(hermanos) + 1} renglones de una "
            f"factura: la fecha y el nombre se cambian en la factura, no en el "
            f"renglón. Aquí puede cambiar la cantidad y el precio"
        )
    if cambia_fecha:
        documento.fecha = nueva_fecha
    if cambia_tercero:
        documento.tercero = nuevo_tercero
    documento.updated_by = servicio.ctx.user_id
    servicio.db.flush()


# ------------------------------------------------------ catálogo de productos
def clave_de_producto(nombre: str | None) -> str:
    """La CLAVE de un producto a partir de su nombre: minúsculas, sin acentos y con
    guion bajo donde había espacios. "Queso Costeño" -> "queso_costeno".

    POR QUÉ SÍ SE QUITAN LOS ACENTOS ACÁ, cuando `clave_de_tercero` —la de los
    nombres de personas, en app/common/nombres.py— a propósito NO los quita. Son
    dos problemas distintos, y por eso son dos funciones:

    · allá la clave agrupa PERSONAS, y "Munoz" y "Muñoz" son dos apellidos
      distintos: unificarlos sería el sistema adivinando el apellido de alguien;
    · acá la clave es el IDENTIFICADOR con el que las filas de compras y de ventas
      nombran al producto —'queso', 'borona', 'mozzarella', todas ASCII, ver
      `ProductoReventa`—. Tiene que quedar igual escrita desde cualquier teclado y
      comparada igual en SQLite y en Postgres, porque el `lower()` de SQLite no baja
      los acentos y el de Postgres sí: una clave con tilde compararía distinto en
      las pruebas que en la base del cliente.

    Devuelve "" si el nombre no traía ni una letra ni un número; quien llame decide
    qué hacer con eso (el servicio lo rechaza con un mensaje).
    """
    sin_acentos = "".join(
        c
        for c in unicodedata.normalize("NFKD", nombre or "")
        if not unicodedata.combining(c)
    )
    # El recorte a 80 es el ancho de la columna. Que corte no importa: es una clave,
    # no un texto que alguien lea.
    return re.sub(r"[^a-z0-9]+", "_", sin_acentos.lower()).strip("_")[:80]


class ProductoReventaService(BaseService[ProductoReventa]):
    """EL CATÁLOGO: qué se compra y se revende, como dato y no como código.

    Lo que hace este servicio, en una frase: recibe un NOMBRE y deduce todo lo
    demás. La clave sale del nombre; los decimales y `admite_ajustes` salen de la
    unidad (ver `ProductoReventa`, donde está el porqué de cada uno). Al usuario se
    le pregunta solo lo que únicamente él sabe.

    NO TOCA NI UNA CIFRA DE PLATA, y es la afirmación importante sobre la base de un
    cliente real: en este lote ninguna consulta de compras, de ventas, del resumen,
    de las temporadas, de los lotes o del FIFO lee esta tabla. El catálogo existe,
    se administra, y nada más lo mira todavía.

    Y DE AHÍ SALE EL LÍMITE DE ESTE CORTE: solo se pueden agregar productos QUE SE
    PESEN. Un producto en kilos pasa por los CheckConstraints que ya tienen
    `compras_queso` y `ventas_queso` —los que obligan a que las barras vivan en sus
    propias columnas— y lo reconoce `se_mide_en_kilos`, que es lo que suma los kilos
    en el resumen. Uno por unidad exige tumbar esos CHECK, y eso es el lote
    siguiente. Se rechaza con un mensaje que lo dice, en vez de guardarlo y dejar que
    reviente después contra la base.
    """

    repository_cls = ProductoReventaRepository
    modulo = "reventa"

    # ---------------------------------------------------------------- deducir
    def _nombre_y_clave(self, data: dict[str, Any]) -> None:
        """Normaliza el nombre y le calcula la clave. Idempotente a propósito: se
        llama desde `crear` —para decidir si hay una fila dormida con esa clave— y
        otra vez desde `validar_crear`."""
        nombre = " ".join((data.get("nombre") or "").split())
        clave = clave_de_producto(nombre)
        if not clave:
            raise BusinessError(
                "El nombre del producto tiene que tener por lo menos una letra o un "
                f"número: '{nombre}' no deja con qué identificarlo"
            )
        data["nombre"] = nombre
        data["clave"] = clave

    def _unidad_y_derivados(self, data: dict[str, Any]) -> None:
        unidad = data.get("unidad") or UNIDAD_KILO
        data["unidad"] = unidad
        # La deducción vive en el modelo, al lado del CHECK que la exige, porque la
        # comparte con la siembra de cada despliegue (ver `derivados_de_unidad`).
        data["decimales"], data["admite_ajustes"] = derivados_de_unidad(unidad)

    def _padre(
        self, padre_id: uuid.UUID, propio_id: uuid.UUID | None = None
    ) -> ProductoReventa:
        """El producto del que otro sería subproducto, validado.

        `self.repo.get` ya filtra por empresa y por borrados, así que un id de OTRA
        empresa sale como "no existe" y no como un 403: a nadie se le confirma que
        ese producto existe en otra parte.

        LA CADENA SE CORTA EN UN NIVEL, en las dos direcciones. El motor FIFO de
        `lotes.py` implementa exactamente una relación padre-subproducto (queso ->
        borona, con el costo heredado); un subproducto de un subproducto no tendría
        cómo costearse, y ofrecerlo sería prometer una cuenta que no existe.
        """
        if propio_id is not None and padre_id == propio_id:
            raise BusinessError("Un producto no puede ser subproducto de sí mismo")
        padre = self.repo.get(padre_id)
        if padre is None:
            raise NotFoundError(
                "El producto del que este sería subproducto no existe en esta empresa"
            )
        if padre.subproducto_de_id is not None:
            raise BusinessError(
                f"'{padre.nombre}' ya es subproducto de otro producto, así que no "
                "puede tener subproductos propios: la cadena solo llega a un nivel, "
                "que es lo que el reparto de costos sabe calcular"
            )
        if propio_id is not None and self.repo.hijos(propio_id):
            raise BusinessError(
                "Este producto ya tiene subproductos propios, así que no puede "
                "volverse subproducto de otro: la cadena solo llega a un nivel"
            )
        return padre

    def _validar_nombre_libre(
        self, nombre: str, propio_id: uuid.UUID | None = None
    ) -> None:
        """Que no queden dos productos llamándose igual.

        Se comparan por la MISMA normalización de la clave, así que "Queso costeño" y
        "QUESO COSTEÑO" son el mismo nombre. No es cosmética: en la lista de
        selección con la que se va a registrar una compra, dos renglones que dicen lo
        mismo y apuntan a claves distintas son plata anotada en el producto
        equivocado.

        Y ojo con lo que este chequeo NO impide: renombrar. "Queso" -> "Queso
        costeño" pasa derecho, que es justo el caso que el dueño va a pedir.
        """
        clave_vista = clave_de_producto(nombre)
        for otro in self.repo.catalogo():
            if otro.id == propio_id:
                continue
            if clave_de_producto(otro.nombre) == clave_vista:
                raise ConflictError(
                    f"Ya hay un producto que se llama '{otro.nombre}'. Póngale un "
                    "nombre que se distinga, para no anotarle plata al equivocado"
                )

    # ------------------------------------------------------------------ crear
    def crear(self, payload: Any) -> ProductoReventa:
        """Agrega un producto, o REVIVE el que se había quitado con esa misma clave.

        POR QUÉ REVIVIR Y NO INSERTAR OTRO. El UNIQUE de (empresa_id, clave) no
        filtra `deleted_at`, así que una fila borrada en suave sigue ocupando su
        clave: insertar reventaría contra la base con un error que el dueño no
        entiende, y rechazarlo dejaría 'queso' inutilizable para siempre solo porque
        alguien lo quitó una vez. Se le devuelve LA MISMA FILA, con su mismo id y su
        misma clave, que además es lo único que deja que sus movimientos viejos —si
        los hubiera— sigan cuadrando con él.

        AL REVIVIR NO SE REDEFINE: vuelve con la unidad, los decimales y el
        `admite_ajustes` que tenía. Se le actualizan el nombre, el orden y el
        subproducto, que es lo que el usuario acabó de escribir. Por eso reactivar
        una mozzarella dormida SÍ se puede aunque este corte no permita crear
        productos por unidad: no se está creando nada nuevo, se está devolviendo lo
        que ya existía y que las tablas ya saben manejar.

        Y ES LO CONTRARIO DE LO QUE HACE LA SIEMBRA, a propósito: la siembra de cada
        despliegue NO resucita nada (ver `ensure_catalogos_empresas`), porque haber
        quitado un producto fue la decisión de una persona y un despliegue no la
        deshace. Acá es esa misma persona pidiéndolo otra vez.
        """
        data = (
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, BaseModel)
            else dict(payload)
        )
        self._nombre_y_clave(data)
        dormido = self.repo.por_clave(data["clave"], incluir_borrados=True)
        if dormido is not None and dormido.deleted_at is not None:
            return self._revivir(dormido, data)
        return super().crear(data)

    def validar_crear(self, data: dict[str, Any]) -> None:
        self._nombre_y_clave(data)
        self._unidad_y_derivados(data)
        if data.get("orden") is None:
            data["orden"] = self.repo.siguiente_orden()
        # Si hay una fila con esta clave, acá solo puede estar VIVA: a las dormidas
        # las atendió `crear`.
        ocupada = self.repo.por_clave(data["clave"], incluir_borrados=True)
        if ocupada is not None:
            raise ConflictError(
                f"Ya existe el producto '{ocupada.nombre}' con la clave "
                f"'{ocupada.clave}'"
            )
        self._validar_nombre_libre(data["nombre"])
        if data.get("subproducto_de_id"):
            self._padre(data["subproducto_de_id"])

    def _revivir(
        self, dormido: ProductoReventa, data: dict[str, Any]
    ) -> ProductoReventa:
        self._validar_nombre_libre(data["nombre"], propio_id=dormido.id)
        padre_id = data.get("subproducto_de_id")
        if padre_id:
            self._padre(padre_id, propio_id=dormido.id)
        antes = serialize_entity(dormido)
        dormido.nombre = data["nombre"]
        dormido.subproducto_de_id = padre_id
        dormido.orden = (
            data["orden"]
            if data.get("orden") is not None
            else self.repo.siguiente_orden()
        )
        dormido.deleted_at = None
        dormido.estado = ESTADO_ACTIVO
        dormido.updated_by = self.ctx.user_id
        self.db.flush()
        # Se audita como CREAR y no como editar, porque es lo que el usuario hizo:
        # agregó un producto. El `antes` deja ver en la auditoría que la fila venía
        # borrada, que es la parte que no se podría adivinar después.
        self._audit("crear", dormido.id, antes, serialize_entity(dormido))
        return dormido

    # ----------------------------------------------------------------- editar
    def validar_actualizar(self, obj: ProductoReventa, data: dict[str, Any]) -> None:
        """Renombrar siempre; mover el subproducto solo mientras no haya movimientos.

        LA CLAVE NO SE TOCA NUNCA, ni cuando cambia el nombre. No es un olvido: es el
        puente con las filas de compras y de ventas, que guardan justamente esa
        cadena en su columna `tipo` (ver el modelo). Recalcularla al renombrar
        desconectaría al producto de toda su historia, y ese es exactamente el
        defecto que este diseño evita.

        La unidad y los decimales no llegan hasta acá: no están en
        `ProductoReventaUpdate`. Lo que sí hay que atajar son los campos que el
        esquema declara opcionales y que la columna tiene NOT NULL: un
        `{"nombre": null}` explícito los pondría en nulo.
        """
        for campo in ("nombre", "orden", "estado"):
            if campo in data and data[campo] is None:
                data.pop(campo)

        if "nombre" in data:
            nombre = " ".join(data["nombre"].split())
            if not clave_de_producto(nombre):
                raise BusinessError(
                    "El nombre del producto tiene que tener por lo menos una letra o "
                    "un número"
                )
            data["nombre"] = nombre
            self._validar_nombre_libre(nombre, propio_id=obj.id)

        if "estado" in data and data["estado"] not in (ESTADO_ACTIVO, ESTADO_INACTIVO):
            raise BusinessError(
                f"El estado de un producto es '{ESTADO_ACTIVO}' o '{ESTADO_INACTIVO}'"
            )

        if (
            "subproducto_de_id" in data
            and data["subproducto_de_id"] != obj.subproducto_de_id
        ):
            compras, ventas = self.repo.movimientos(obj.clave)
            if compras or ventas:
                raise BusinessError(
                    f"'{obj.nombre}' ya tiene "
                    f"{self._texto_movimientos(compras, ventas)}, así que no se le "
                    "puede cambiar de qué producto es subproducto: de ahí hereda el "
                    "costo lo que se venda de él, y cambiarlo recostearía cuentas "
                    "que usted ya cuadró"
                )
            if data["subproducto_de_id"] is not None:
                self._padre(data["subproducto_de_id"], propio_id=obj.id)

    # ----------------------------------------------------------------- quitar
    @staticmethod
    def _texto_movimientos(compras: int, ventas: int) -> str:
        partes = []
        if compras:
            partes.append(f"{compras} compra{'s' if compras != 1 else ''}")
        if ventas:
            partes.append(f"{ventas} venta{'s' if ventas != 1 else ''}")
        return " y ".join(partes)

    def validar_eliminar(self, obj: ProductoReventa) -> None:
        """Solo sale del catálogo lo que nunca se movió.

        Es la misma regla del resto del ERP, y acá tiene su razón propia: la clave
        del producto es lo que las filas de compras y de ventas tienen guardado. Un
        producto quitado con movimientos encima dejaría filas del cuaderno hablando
        de algo que ya no aparece en ninguna lista, y el dueño no tendría cómo saber
        qué fue lo que compró.

        Para eso está DESACTIVARLO, que es la salida real cuando ya se movió: deja de
        ofrecerse al registrar y su historia se queda completa. Se le dice en el
        mensaje, porque un rechazo sin salida es un rechazo a medias.
        """
        compras, ventas = self.repo.movimientos(obj.clave)
        if compras or ventas:
            raise BusinessError(
                "Solo se puede quitar un producto que no tenga movimientos: "
                f"'{obj.nombre}' ya tiene {self._texto_movimientos(compras, ventas)}. "
                "Si ya no lo maneja, desactívelo: deja de aparecer al registrar y su "
                "historia se queda completa"
            )
        hijos = self.repo.hijos(obj.id)
        if hijos:
            nombres = ", ".join(f"'{h.nombre}'" for h in hijos)
            raise BusinessError(
                f"No se puede quitar '{obj.nombre}' porque {nombres} "
                f"{'es subproducto suyo' if len(hijos) == 1 else 'son subproductos suyos'}. "
                "Quite primero el subproducto, o desligúelo"
            )

    # ----------------------------------------------------------------- listar
    def listar(
        self,
        params: PageParams,
        *,
        search: str | None = None,
        estado: str | None = None,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[list[ProductoReventa], int]:
        """El catálogo en el orden en que el dueño lo puso, no por fecha.

        Hay que decirlo explícitamente porque el repositorio genérico ordena
        DESCENDENTE por su `default_order_by`, que es lo correcto para los
        movimientos —lo último registrado va arriba— y justo al revés de lo que
        necesita una lista de selección.
        """
        return self.repo.list_paginated(
            params,
            search=search,
            estado=estado,
            filters=filters,
            order_by=ProductoReventa.orden.asc(),
        )


class CompraQuesoService(BaseService[CompraQueso]):
    repository_cls = CompraQuesoRepository
    modulo = "reventa"

    @staticmethod
    def _calcular(data: dict[str, Any], actual: CompraQueso | None = None) -> dict[str, Any]:
        """Deja la fila con la cantidad y el precio de SU unidad, y la otra unidad
        en cero (que es lo que exige el CHECK de la tabla).

        El tipo se toma del payload al crear y de la FILA GUARDADA al editar: la
        edición no acepta `tipo` a propósito (ver CompraQuesoUpdate), así que una
        compra nace de kilos o de barras y se queda así.
        """
        tipo = data.get("tipo") or (actual.tipo if actual else TIPO_VENTA_QUESO)
        data["tipo"] = tipo
        if tipo == TIPO_MOZZARELLA:
            barras = Decimal(data.get("barras") or (actual.barras if actual else CERO))
            precio_barra = Decimal(
                data.get("precio_barra") or (actual.precio_barra if actual else CERO)
            )
            data["barras"] = barras
            data["precio_barra"] = precio_barra
            # Todo lo que se mide en kilos queda en cero, y se escribe AQUÍ y no
            # solo en el esquema de entrada: por PUT llega un payload parcial y sin
            # esto una compra de barras podría quedar con kilos de un intento
            # anterior. El CHECK de la tabla la rechazaría, pero un 500 de la base
            # no le dice nada al dueño; mejor que nunca llegue a pasar.
            data["kilos_brutos"] = CERO
            data["kilos_netos"] = CERO
            data["merma_kilos"] = CERO
            data["borona_kilos"] = CERO
            data["precio_kilo"] = CERO
            # La plata: barras × lo que costó cada barra. Los pesos son pesos, así
            # que esta columna se suma con la de las compras en kilos sin problema.
            data["valor_total"] = (barras * precio_barra).quantize(DOS_DECIMALES)
            return data

        brutos = Decimal(data.get("kilos_brutos") or (actual.kilos_brutos if actual else CERO))
        precio = Decimal(data.get("precio_kilo") or (actual.precio_kilo if actual else CERO))
        # Ya no hay merma en la compra: se paga por todo lo recibido. La merma
        # real se refleja al vender (se pesa menos). Se guarda merma 0.
        data["merma_kilos"] = CERO
        data["kilos_netos"] = brutos
        data["barras"] = CERO
        data["precio_barra"] = CERO
        data["valor_total"] = (brutos * precio).quantize(DOS_DECIMALES)
        return data

    def _nombres_para_canonizar(self) -> list[str]:
        """Contra qué lista se canoniza el nombre del productor: los que ya usan
        las compras MÁS los terceros del libro anterior de tipo 'pagar'.

        Un productor que por ahora SOLO existe en el libro anterior ya tiene una
        escritura guardada, y la primera compra que se le registre tiene que
        adoptarla: si no, su deuda vieja y la nueva quedan en dos productores
        distintos y el detalle muestra dos filas de la misma persona.

        Primero van los de las compras: si el nombre está en los dos lados manda
        la escritura del sistema, que es la que agrupa el detalle por productor.
        """
        return self.repo.nombres_productores() + SaldoAnteriorRepository(
            self.db, self.ctx.empresa_id
        ).nombres_terceros(TIPO_SALDO_PAGAR)

    def canonizar_tercero(self, nombre: str | None) -> str | None:
        """El nombre del productor, escrito como YA está escrito en el sistema.

        Método público porque lo llaman las DOS puertas: el payload plano por
        `_canonizar` y la cabecera del documento por `DocumentoReventaService`. Si
        la factura canonizara distinto que la compra suelta, el mismo señor
        quedaría partido en dos según por dónde se registró y su cartera mostraría
        dos filas. Una sola implementación de la regla.
        """
        if not nombre:
            return nombre
        return _canonizar_nombre(nombre, self._nombres_para_canonizar())

    def _canonizar(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("productor"):
            data["productor"] = self.canonizar_tercero(data["productor"])
        return data

    # ------------------------------------------- renglones de un documento
    # Las tres piezas de abajo son el ÚNICO camino por el que se escriben
    # renglones de compra, y están separadas a propósito: primero se calcula
    # TODO (sin tocar la base), después se valida el conjunto COMPLETO y solo
    # entonces se escribe. Si estuvieran juntas, un documento de tres renglones
    # podría dejar el primero escrito y reventar en el tercero.
    def preparar_renglones(self, renglones: list[Any]) -> list[dict[str, Any]]:
        """La plata de cada renglón, SIN escribir nada y SIN consultar nada.

        Cada renglón pasa por el mismo `_calcular` de siempre, que es el que deja
        la cantidad en la columna de SU unidad y la otra en cero (lo que exige el
        CHECK de la tabla).
        """
        datos: list[dict[str, Any]] = []
        
        # Validar la unidad contra el catálogo de productos_reventa
        from sqlalchemy import select
        from app.modules.reventa.models import ProductoReventa
        claves = {r.tipo if hasattr(r, "tipo") and r.tipo else (r.get("tipo") if isinstance(r, dict) and r.get("tipo") else TIPO_QUESO) for r in renglones}
        productos = {p.clave: p.unidad for p in self.db.execute(select(ProductoReventa).where(ProductoReventa.clave.in_(claves))).scalars()}
        
        for orden, renglon in enumerate(renglones):
            data = _campos_del_renglon(renglon, RenglonCompraCreate)
            tipo = data.get("tipo") or TIPO_QUESO
            data["tipo"] = tipo
            unidad = productos.get(tipo, "kg") # Fallback a kg
            
            # Revalidar que los campos enviados coincidan con la unidad del catálogo
            if unidad == "unidad" and (data.get("kilos_brutos") or data.get("precio_kilo")):
                raise BusinessError(f"Una compra de {tipo} necesita las barras y el precio por barra, no kilos.")
            if unidad == "kg" and (data.get("barras") or data.get("precio_barra")):
                raise BusinessError(f"Una compra de {tipo} necesita los kilos y el precio por kilo, no barras.")

            data = self._calcular(data)
            data["orden"] = orden
            data["estado"] = ESTADO_PENDIENTE
            datos.append(data)
        return datos

    def exigir_cantidades(
        self, datos: list[dict[str, Any]], *, devolviendo: list[CompraQueso] = ()
    ) -> None:
        """AL REHACER los renglones de una compra no se pueden quitar cantidades
        que ya salieron vendidas.

        Es el mismo guardia que ya tenía `actualizar` fila por fila, medido sobre
        el CONJUNTO: lo que importa es cuánto trae la factura ENTERA contra cuánto
        traía antes. Sin mirar el conjunto, una factura de 100 + 200 kg editada a
        300 + 0 kg pasaría dos veces por un guardia que compara renglón contra
        renglón y no vería que el total no cambió.

        Bajar una compra de 100 kg a 10 cuando ya se vendieron 80 deja el
        inventario en -70: a partir de ahí NINGUNA venta pasa el control de
        existencias y el dueño se queda sin poder trabajar sin entender por qué.

        Al CREAR no hay nada que exigir y por eso `devolviendo` viene vacío:
        comprar SUMA inventario.
        """
        nuevos_kilos = sum((Decimal(d.get("kilos_netos") or CERO) for d in datos), CERO)
        nuevas_barras = sum((Decimal(d.get("barras") or CERO) for d in datos), CERO)
        viejos_kilos = CERO
        viejas_barras = CERO
        for fila in devolviendo:
            if fila.estado == ESTADO_ANULADA:
                # Una compra anulada no está sosteniendo ningún inventario, así
                # que quitarla no le quita kilos a nadie.
                continue
            viejos_kilos += Decimal(fila.kilos_netos)
            viejas_barras += Decimal(fila.barras)
        if nuevos_kilos < viejos_kilos:
            disponible = ReventaResumenService.queso_disponible(self.db, self.ctx)
            if (nuevos_kilos - viejos_kilos) + disponible < CERO:
                raise BusinessError(
                    f"No se pueden quitar tantos kilos: de esta compra ya salieron "
                    f"vendidos. Solo quedan {disponible} kg sin vender"
                )
        if nuevas_barras < viejas_barras:
            disponibles = ReventaResumenService.barras_disponibles(self.db, self.ctx)
            if (nuevas_barras - viejas_barras) + disponibles < CERO:
                raise BusinessError(
                    f"No se pueden quitar tantas barras: de esta compra ya "
                    f"salieron vendidas. Solo quedan {disponibles} barras sin "
                    f"vender"
                )

    def escribir_renglones(
        self, documento: DocumentoReventa, datos: list[dict[str, Any]]
    ) -> list[CompraQueso]:
        """Escribe los renglones ya calculados y ya validados.

        LA FECHA Y EL PRODUCTOR SE COPIAN DE LA CABECERA, siempre, y ahí está la
        pieza que hace que nada más del módulo tuviera que cambiar: el resumen, la
        cartera, los lotes y el estado de cuenta agrupan por `productor` y filtran
        por `fecha` DE LA FILA. Si el renglón no los llevara, todos ellos tendrían
        que aprender a saltar a la cabecera; llevándolos, no se enteran de que los
        documentos existen.
        """
        filas = []
        for data in datos:
            fila = dict(data)
            fila["documento_id"] = documento.id
            fila["fecha"] = documento.fecha
            fila["productor"] = documento.tercero
            filas.append(super().crear(fila))
        return filas

    def crear(self, payload: Any) -> CompraQueso:
        """LA PUERTA PLANA de un solo producto: por dentro arma un documento de UN
        renglón y devuelve ese renglón, que es lo que siempre devolvió.

        Que las dos puertas escriban con el MISMO código es el punto: hay miles de
        líneas de pruebas montadas sobre este payload y son la única red que
        comprueba que la plata sigue cuadrando. Si la factura tuviera su propio
        camino de escritura, esas pruebas dejarían de medir el camino nuevo justo
        cuando más falta hacen.
        """
        # El payload plano SE ENTREGA TAL CUAL como renglón: `CompraQuesoCreate`
        # hereda de `RenglonCompraCreate`, así que ya ES uno. Ver
        # `_campos_del_renglon` para por qué no se vuelve a validar.
        _, filas = DocumentoReventaService(self.db, self.ctx).crear_con_renglones(
            DocumentoCompraCreate(
                tipo=TIPO_DOC_COMPRA,
                fecha=payload.fecha,
                tercero=payload.productor,
                # La nota viaja a los DOS lados: al renglón (donde se guardó
                # siempre, y por eso la respuesta plana no cambia) y a la
                # cabecera, porque en una factura de un solo producto la nota del
                # producto ES la nota de la factura y la lista de facturas no
                # tiene por qué salir muda.
                observaciones=payload.observaciones,
                renglones=[payload],
            )
        )
        return filas[0]

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> CompraQueso:
        actual = self.repo.get_or_fail(entity_id)
        if actual.estado == ESTADO_ANULADA:
            raise BusinessError("No se puede modificar una compra anulada")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        data = self._calcular(data, actual)
        # Se puede editar aunque tenga abonos (incluida una pagada): se recalcula el
        # estado con los abonos ya registrados y el saldo queda al día. Lo que NO
        # se permite es dejar el total por debajo de lo ya abonado: el saldo se
        # vuelve NEGATIVO y ese negativo RESTA de la tarjeta "Por pagar a
        # productores", que mostraría menos deuda de la que el negocio tiene.
        if data["valor_total"] < Decimal(actual.abonado):
            raise BusinessError(
                f"El total no puede quedar por debajo de lo ya abonado "
                f"({pesos(actual.abonado)}); elimine primero los abonos que sobren"
            )
        # Y tampoco se pueden quitar cantidades que ya salieron vendidas, EN LA
        # UNIDAD DE LA COMPRA. Es el MISMO guardia que valida el conjunto de una
        # factura, llamado con un renglón y devolviendo el que se está reemplazando:
        # la regla se escribe UNA vez, y así editar una compra suelta y rehacer los
        # renglones de una factura no pueden empezar a opinar distinto. (La de
        # barras no es un extra: ya nos pasó que un guardia estaba solo al crear y
        # se podían inventar kilos editando.)
        self.exigir_cantidades([data], devolviendo=[actual])
        data["estado"] = _estado_pago(data["valor_total"], actual.abonado)
        data = self._canonizar(data)
        # La fecha y el productor son de la FACTURA: si esta compra es el único
        # renglón de la suya, se le cambian también allá; si tiene hermanos, se
        # rechaza. Ver `_cuidar_cabecera`.
        _cuidar_cabecera(self, actual, data, campo_tercero="productor", que="compra")
        return super().actualizar(entity_id, data)

    def validar_eliminar(self, obj: CompraQueso) -> None:
        if obj.abonado > CERO:
            raise BusinessError(
                "No se puede eliminar una compra con abonos; elimine primero los abonos o anúlela"
            )

    def eliminar(self, entity_id: uuid.UUID, *, cuidar_cabecera: bool = True) -> None:
        """Borra la compra Y se lleva sus soportes de pago.

        Se valida PRIMERO y se limpian los soportes después: si se limpiaran
        antes, una compra con abonos —que no se puede borrar— perdería sus fotos
        por un borrado que al final no ocurre. Sin esta limpieza, los archivos
        quedaban en el bucket sin ningún documento que los nombre.

        Y SI ERA EL ÚLTIMO RENGLÓN DE SU FACTURA, la factura se va con él (ver
        `_borrar_cabecera_vacia`). `cuidar_cabecera=False` lo apaga cuando el que
        está borrando ES el servicio de la factura, que ya se encarga de la cabecera
        por su cuenta: si no, al borrar el último renglón la cabecera desaparecería
        en medio de la operación y lo que viene después no la encontraría.
        """
        compra = self.repo.get_or_fail(entity_id)
        self.validar_eliminar(compra)
        documento_id = compra.documento_id
        AdjuntoReventaService(self.db, self.ctx).limpiar_de_documento(compra_id=entity_id)
        super().eliminar(entity_id)
        if cuidar_cabecera:
            _borrar_cabecera_vacia(self, documento_id)

    def registrar_abono(self, compra_id: uuid.UUID, payload: Any) -> CompraQueso:
        compra = self.repo.get_or_fail(compra_id)
        compra = _bloquear(self.db, compra)
        if compra.estado == ESTADO_ANULADA:
            raise BusinessError("La compra está anulada")
        valor = Decimal(payload.valor)
        if valor > compra.saldo:
            # pesos() y no "{:,.0f}": el formato con coma es gringo y "$1,200,000"
            # en Colombia se lee como un peso con veinte centavos.
            raise BusinessError(
                f"El abono ({pesos(valor)}) supera el saldo ({pesos(compra.saldo)})"
            )
        self.db.add(
            AbonoCompraQueso(
                compra_id=compra.id,
                fecha=payload.fecha,
                valor=valor,
                observaciones=payload.observaciones,
                created_by=self.ctx.user_id,
            )
        )
        compra.abonado += valor
        compra.estado = _estado_pago(compra.valor_total, compra.abonado)
        compra.updated_by = self.ctx.user_id
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(compra, ["abonos"])
        self._audit("editar", compra.id, None, {"abono": float(valor), "estado": compra.estado})
        return compra

    def eliminar_abono(self, compra_id: uuid.UUID, abono_id: uuid.UUID) -> CompraQueso:
        """Elimina un abono mal registrado: baja el abonado y recalcula el estado."""
        compra = self.repo.get_or_fail(compra_id)
        compra = _bloquear(self.db, compra)
        abono = next((a for a in compra.abonos if a.id == abono_id), None)
        if abono is None:
            raise NotFoundError("Abono no encontrado")
        valor = Decimal(abono.valor)
        compra.abonado = max(compra.abonado - valor, CERO)
        compra.estado = _estado_pago(compra.valor_total, compra.abonado)
        compra.updated_by = self.ctx.user_id
        self.db.delete(abono)
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(compra, ["abonos"])
        self._audit(
            "editar", compra.id, None,
            {"abono_eliminado": float(valor), "estado": compra.estado},
        )
        return compra

    def anular(self, compra_id: uuid.UUID) -> CompraQueso:
        compra = self.repo.get_or_fail(compra_id)
        compra = _bloquear(self.db, compra)
        if compra.abonado > CERO:
            raise BusinessError(
                "No se puede anular una compra con abonos registrados"
            )
        # Si el queso de esta compra YA SE VENDIÓ, no se anula. Anularla borraría
        # de la cuenta un queso que salió de verdad: el inventario se iría a
        # negativo y, con el inventario en negativo, ninguna venta vuelve a pasar
        # el control de existencias — el dueño se queda sin poder trabajar sin
        # entender por qué. Lo que hay que hacer en ese caso es corregir la
        # compra (editarla) o anular primero las ventas que se llevaron ese queso.
        #
        # Cada tipo se mira contra SU inventario: anular una compra de barras no
        # puede consultar los kilos disponibles (siempre pasaría el control, y las
        # barras quedarían en negativo), ni al contrario.
        if compra.tipo == TIPO_MOZZARELLA:
            disponibles = ReventaResumenService.barras_disponibles(self.db, self.ctx)
            if disponibles - Decimal(compra.barras) < CERO:
                raise BusinessError(
                    f"No se puede anular: la mozzarella de esta compra ya se "
                    f"vendió. Solo quedan {disponibles} barras sin vender de las "
                    f"{compra.barras} que trajo. Anule primero las ventas que se "
                    f"las llevaron, o corrija la compra en vez de anularla"
                )
            antes = compra.estado
            compra.estado = ESTADO_ANULADA
            compra.updated_by = self.ctx.user_id
            self.db.flush()
            self._audit("editar", compra.id, {"estado": antes}, {"estado": ESTADO_ANULADA})
            return compra
        disponible = ReventaResumenService.queso_disponible(self.db, self.ctx)
        if disponible - Decimal(compra.kilos_netos) < CERO:
            raise BusinessError(
                f"No se puede anular: el queso de esta compra ya se vendió. "
                f"Solo quedan {disponible} kg sin vender de los "
                f"{compra.kilos_netos} kg que trajo. Anule primero las ventas "
                f"que se lo llevaron, o corrija la compra en vez de anularla"
            )
        antes = compra.estado
        compra.estado = ESTADO_ANULADA
        compra.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", compra.id, {"estado": antes}, {"estado": ESTADO_ANULADA})
        return compra

    def listar_filtrado(
        self, params: PageParams, *, search: str | None, estado: str | None,
        desde: date | None, hasta: date | None,
    ) -> tuple[list[CompraQueso], int]:
        extra = []
        if desde:
            extra.append(CompraQueso.fecha >= desde)
        if hasta:
            extra.append(CompraQueso.fecha <= hasta)
        return self.repo.list_paginated(params, search=search, estado=estado, extra_criteria=extra)


class VentaQuesoService(BaseService[VentaQueso]):
    repository_cls = VentaQuesoRepository
    modulo = "reventa"

    def _nombres_para_canonizar(self) -> list[str]:
        """Contra qué lista se canoniza el nombre del cliente: los que ya usan
        las ventas MÁS los terceros del libro anterior de tipo 'cobrar'.

        Un cliente que por ahora SOLO existe en el libro anterior es justo el
        caso que motivó esa pantalla: su primera venta aquí tiene que adoptar la
        escritura ya guardada, o su deuda vieja y la nueva quedan partidas en dos
        clientes y el estado de cuenta no muestra todo lo que debe.

        Primero van los de las ventas: si el nombre está en los dos lados manda
        la escritura del sistema, que es la que agrupa el estado de cuenta.
        """
        return self.repo.nombres_clientes() + SaldoAnteriorRepository(
            self.db, self.ctx.empresa_id
        ).nombres_terceros(TIPO_SALDO_COBRAR)

    def canonizar_tercero(self, nombre: str | None) -> str | None:
        """El nombre del cliente, escrito como YA está escrito en el sistema.
        Público porque lo llaman las dos puertas: ver
        `CompraQuesoService.canonizar_tercero`."""
        if not nombre:
            return nombre
        return _canonizar_nombre(nombre, self._nombres_para_canonizar())

    def _canonizar(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("cliente"):
            data["cliente"] = self.canonizar_tercero(data["cliente"])
        return data

    @staticmethod
    def _inventario_de(tipo: str) -> tuple[Any, str, str]:
        """(de dónde se lee el disponible, cómo se llama, en qué unidad).

        Los tres inventarios son SEPARADOS y cada uno se compara solo con el suyo:
        tener 500 kg de queso no autoriza a despachar una barra de mozzarella que
        no se compró.
        """
        if tipo == TIPO_VENTA_MOZZARELLA:
            return ReventaResumenService.barras_disponibles, "mozzarella", "barras"
        if tipo == TIPO_VENTA_BORONA:
            return ReventaResumenService.borona_disponible, "borona", "kg"
        return ReventaResumenService.queso_disponible, "queso", "kg"

    @staticmethod
    def _cantidad_de(fila: VentaQueso) -> Decimal:
        """Cuánto tiene apartado esta venta, EN SU UNIDAD."""
        if (fila.tipo or TIPO_VENTA_QUESO) == TIPO_VENTA_MOZZARELLA:
            return Decimal(fila.barras)
        return Decimal(fila.kilos)

    def exigir_cantidades(
        self,
        datos: list[dict[str, Any]],
        *,
        devolviendo: list[VentaQueso] = (),
    ) -> None:
        """No se puede vender más de lo que hay, SUMANDO LOS RENGLONES DEL MISMO
        PRODUCTO y validando el documento COMPLETO antes de escribir nada.

        LO DE SUMAR PRIMERO ES EL PUNTO, y sin eso los documentos serían un hueco
        nuevo: con 400 kg en bodega, dos renglones de 300 kg cada uno pasan uno por
        uno —cada 300 es menor que 400— y la factura despacha 600 kg que no
        existen. El inventario queda en -200, y con el inventario en negativo
        NINGUNA venta vuelve a pasar el control: el dueño se queda sin poder
        trabajar sin entender por qué.

        Y LO DE VALIDAR ANTES DE ESCRIBIR TAMPOCO ES ADORNO: si se escribiera
        renglón por renglón validando de a uno, el disponible bajaría con cada
        fila escrita y la factura quedaría a medias cuando reventara la última.

        `devolviendo` son los renglones que esta misma escritura va a REEMPLAZAR:
        hay que devolverle al inventario lo que ya tenían apartado, o editar una
        factura de 100 kg a 100 kg fallaría por comparar contra un disponible del
        que esos kilos ya están descontados. Es el mismo `actual` de antes, en
        plural.

        Cada disponible se consulta UNA sola vez y solo si hace falta (`cache`):
        son tres sumas contra la base y una factura de queso no tiene por qué
        pagar las de la mozzarella.
        """
        cache: dict[str, Decimal] = {}

        from app.modules.empresas.models import Empresa
        self.db.execute(
            select(Empresa.id)
            .where(Empresa.id == self.ctx.empresa_id)
            .with_for_update()
        )

        def disponible_de(tipo: str) -> Decimal:
            if tipo not in cache:
                fuente, _, _ = self._inventario_de(tipo)
                cache[tipo] = fuente(self.db, self.ctx)
            return cache[tipo]

        # Lo pedido, POR TIPO y en el orden en que aparecen los renglones: así el
        # mensaje de error habla del primer producto que no alcanza, que es el que
        # el usuario tiene que corregir.
        pedido: dict[str, Decimal] = {}
        renglones_por_tipo: dict[str, int] = {}
        for data in datos:
            tipo = data.get("tipo") or TIPO_VENTA_QUESO
            cantidad = (
                Decimal(data.get("barras") or CERO)
                if tipo == TIPO_VENTA_MOZZARELLA
                else Decimal(data.get("kilos") or CERO)
            )
            pedido[tipo] = pedido.get(tipo, CERO) + cantidad
            renglones_por_tipo[tipo] = renglones_por_tipo.get(tipo, 0) + 1

        # Lo que se devuelve al inventario, cada cantidad al inventario DE SU TIPO:
        # devolver kilos a un inventario de barras dejaría al guardia comparando
        # contra una cifra inventada.
        for fila in devolviendo:
            if fila.estado == ESTADO_ANULADA:
                # Una venta anulada no tiene nada apartado: ya se le devolvió.
                continue
            tipo = fila.tipo or TIPO_VENTA_QUESO
            cache[tipo] = disponible_de(tipo) + self._cantidad_de(fila)

        for tipo, cantidad in pedido.items():
            disponible = disponible_de(tipo)
            if cantidad > disponible:
                _, que, unidad = self._inventario_de(tipo)
                detalle = ""
                if renglones_por_tipo[tipo] > 1:
                    # Con el producto repetido en varios renglones, el mensaje
                    # tiene que decir que la cuenta es la SUMA: si no, el usuario
                    # ve "solo hay 400" al lado de un renglón de 300 y cree que el
                    # sistema está equivocado.
                    detalle = (
                        f", y en esta factura se están vendiendo {cantidad} "
                        f"{unidad} entre {renglones_por_tipo[tipo]} renglones"
                    )
                raise BusinessError(
                    f"Solo hay {disponible} {unidad} de {que} disponibles{detalle}"
                )

    def _exigir_existencias(
        self, tipo: str, cantidad: Decimal, actual: VentaQueso | None = None
    ) -> None:
        """El guardia de UNA venta suelta, escrito sobre el del conjunto.

        Sigue existiendo porque `actualizar` edita una fila a la vez, y es un
        envoltorio de una línea a propósito: la regla se escribe UNA vez. Antes
        estaba solo al crear y `actualizar` no la hacía —se creaba una venta de 1 kg
        y se editaba a 500, y pasaba—, y el resumen quedaba con kilos negativos y
        una ganancia que no era la real, distinta además de la del desglose por
        lote, que es justo lo que el dueño ve al cuadrar a mano.
        """
        clave = "barras" if tipo == TIPO_VENTA_MOZZARELLA else "kilos"
        self.exigir_cantidades(
            [{"tipo": tipo, clave: cantidad}],
            devolviendo=[actual] if actual is not None else [],
        )

    # ------------------------------------------- renglones de un documento
    # Mismas tres piezas que en las compras y por las mismas razones: calcular
    # todo, validar el conjunto completo, y solo entonces escribir.
    def preparar_renglones(self, renglones: list[Any]) -> list[dict[str, Any]]:
        """La plata de cada renglón de venta, SIN escribir ni consultar nada."""
        datos: list[dict[str, Any]] = []
        
        # Validar la unidad contra el catálogo de productos_reventa
        from sqlalchemy import select
        from app.modules.reventa.models import ProductoReventa
        claves = {r.tipo if hasattr(r, "tipo") and r.tipo else (r.get("tipo") if isinstance(r, dict) and r.get("tipo") else TIPO_VENTA_QUESO) for r in renglones}
        productos = {p.clave: p.unidad for p in self.db.execute(select(ProductoReventa).where(ProductoReventa.clave.in_(claves))).scalars()}

        for orden, renglon in enumerate(renglones):
            data = _campos_del_renglon(renglon, RenglonVentaCreate)
            tipo = data.get("tipo") or TIPO_VENTA_QUESO
            data["tipo"] = tipo
            unidad = productos.get(tipo, "kg") # Fallback a kg
            
            # Revalidar que los campos enviados coincidan con la unidad del catálogo
            if unidad == "unidad" and (data.get("kilos") or data.get("precio_kilo")):
                raise BusinessError(f"Una venta de {tipo} necesita las barras y el precio por barra, no kilos.")
            if unidad == "kg" and (data.get("barras") or data.get("precio_barra")):
                raise BusinessError(f"Una venta de {tipo} necesita los kilos y el precio por kilo, no barras.")

            if unidad == "unidad":
                barras = Decimal(data.get("barras") or CERO)
                data["valor_total"] = (
                    barras * Decimal(data.get("precio_barra") or CERO)
                ).quantize(DOS_DECIMALES)
                # El gasto se cobra POR BARRA, y el monto en pesos sale de
                # multiplicar por las barras. Esa columna (`gasto_monto`) es la
                # única que se suma con la de las ventas en kilos, porque es la
                # única que está en pesos.
                por_barra = Decimal(data.get("gasto_por_barra") or CERO)
                data["gasto_monto"] = (por_barra * barras).quantize(DOS_DECIMALES)
            else:
                kilos = Decimal(data.get("kilos") or CERO)
                data["valor_total"] = (
                    kilos * Decimal(data.get("precio_kilo") or CERO)
                ).quantize(DOS_DECIMALES)
                # Gasto de venta por kilo (ej. transporte): el total es
                # por_kilo * kilos.
                por_kilo = Decimal(data.get("gasto_por_kilo") or CERO)
                data["gasto_monto"] = (por_kilo * kilos).quantize(DOS_DECIMALES)
            data["orden"] = orden
            data["estado"] = ESTADO_PENDIENTE
            datos.append(data)
        return datos

    def escribir_renglones(
        self, documento: DocumentoReventa, datos: list[dict[str, Any]]
    ) -> list[VentaQueso]:
        """Escribe los renglones ya calculados y ya validados.

        LA FECHA Y EL CLIENTE SE COPIAN DE LA CABECERA: ver el porqué largo en
        `CompraQuesoService.escribir_renglones`.
        """
        filas = []
        for data in datos:
            fila = dict(data)
            fila["documento_id"] = documento.id
            fila["fecha"] = documento.fecha
            fila["cliente"] = documento.tercero
            filas.append(super().crear(fila))
        return filas

    def crear(self, payload: Any) -> VentaQueso:
        """LA PUERTA PLANA de un solo producto: por dentro arma un documento de UN
        renglón. Ver el porqué en `CompraQuesoService.crear`.

        `pagada_de_contado` sigue haciendo exactamente lo mismo que hacía: en una
        factura de un renglón, derramar el total sobre "los renglones" es ponerle
        el abono completo a esa única fila, con la fecha de la venta y la nota
        "Pago de contado".
        """
        # El payload plano se entrega tal cual como renglón: ver
        # `CompraQuesoService.crear` y `_campos_del_renglon`.
        _, filas = DocumentoReventaService(self.db, self.ctx).crear_con_renglones(
            DocumentoVentaCreate(
                tipo=TIPO_DOC_VENTA,
                fecha=payload.fecha,
                tercero=payload.cliente,
                # La nota va al renglón y a la cabecera: ver CompraQuesoService.crear.
                observaciones=payload.observaciones,
                renglones=[payload],
                pagada_de_contado=bool(getattr(payload, "pagada_de_contado", False)),
            )
        )
        return filas[0]

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> VentaQueso:
        actual = self.repo.get_or_fail(entity_id)
        if actual.estado == ESTADO_ANULADA:
            raise BusinessError("No se puede modificar una venta anulada")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        # El tipo NO se edita (VentaQuesoUpdate no lo recibe): manda el de la fila
        # guardada, que es el que dice en qué unidad está esta venta.
        tipo = actual.tipo
        if tipo == TIPO_VENTA_MOZZARELLA:
            barras = Decimal(data.get("barras") or actual.barras)
            precio_barra = Decimal(data.get("precio_barra") or actual.precio_barra)
            # La MISMA comprobación que al crear, en barras. Sin esto se registra
            # una venta de una barra y se edita a las que sea: es el defecto que ya
            # nos pasó con los kilos, y no se repite en la unidad nueva.
            self._exigir_existencias(tipo, barras, actual=actual)
            data["valor_total"] = (barras * precio_barra).quantize(DOS_DECIMALES)
            por_barra = Decimal(
                data["gasto_por_barra"]
                if data.get("gasto_por_barra") is not None
                else actual.gasto_por_barra
            )
            data["gasto_monto"] = (por_barra * barras).quantize(DOS_DECIMALES)
            # Lo de la otra unidad no puede colarse por un payload parcial: la
            # pantalla manda el objeto completo y `kilos` llegaría en cero o en
            # nulo, pero si algún día llegara con un número, el CHECK de la tabla
            # tumbaría la edición con un 500 en vez de un mensaje entendible.
            data.pop("kilos", None)
            data.pop("precio_kilo", None)
            data.pop("gasto_por_kilo", None)
        else:
            kilos = Decimal(data.get("kilos") or actual.kilos)
            precio = Decimal(data.get("precio_kilo") or actual.precio_kilo)
            # La MISMA comprobación que al crear. Sin esto el guardia de la creación
            # es de adorno: se crea la venta con un kilo y se edita a los que sea.
            self._exigir_existencias(tipo, kilos, actual=actual)
            data["valor_total"] = (kilos * precio).quantize(DOS_DECIMALES)
            # Recalcula el gasto total (por_kilo * kilos) si cambió cualquiera de los dos.
            por_kilo = Decimal(
                data["gasto_por_kilo"]
                if data.get("gasto_por_kilo") is not None
                else actual.gasto_por_kilo
            )
            data["gasto_monto"] = (por_kilo * kilos).quantize(DOS_DECIMALES)
            data.pop("barras", None)
            data.pop("precio_barra", None)
            data.pop("gasto_por_barra", None)
        # Se puede editar aunque tenga abonos (incluida una pagada): se recalcula el estado.
        # OJO: aquí NO va el guardia de "el total no puede quedar por debajo de lo
        # abonado" que sí tienen las compras y los saldos de la cuenta anterior.
        # Rebajarle una venta ya pagada deja SALDO A FAVOR del cliente, que es un
        # caso contemplado a propósito en el estado de cuenta (rótulo y signo
        # propios) y cubierto por test_estado_cuenta_saldo_a_favor.
        # Ese saldo negativo tampoco se le resta a la cartera: los agregados suman
        # el saldo de cada fila ACOTADO EN CERO (ver saldo_pendiente en el
        # repositorio), porque lo que un cliente pagó de más no reduce lo que le
        # deben los OTROS. Por eso se puede permitir la edición sin que la tarjeta
        # "Por cobrar a clientes" mienta.
        data["estado"] = _estado_pago(data["valor_total"], actual.abonado)
        data = self._canonizar(data)
        # Mismo cuidado que en la compra: la fecha y el cliente son de la factura.
        _cuidar_cabecera(self, actual, data, campo_tercero="cliente", que="venta")
        return super().actualizar(entity_id, data)

    def validar_eliminar(self, obj: VentaQueso) -> None:
        if obj.abonado > CERO:
            raise BusinessError(
                "No se puede eliminar una venta con abonos; elimine primero los abonos o anúlela"
            )

    def eliminar(self, entity_id: uuid.UUID, *, cuidar_cabecera: bool = True) -> None:
        """Borra la venta Y se lleva sus soportes de pago, y si era el último renglón
        de su factura, también la factura. Mismo orden y mismas razones que en la
        compra: ver CompraQuesoService.eliminar."""
        venta = self.repo.get_or_fail(entity_id)
        self.validar_eliminar(venta)
        documento_id = venta.documento_id
        AdjuntoReventaService(self.db, self.ctx).limpiar_de_documento(venta_id=entity_id)
        super().eliminar(entity_id)
        if cuidar_cabecera:
            _borrar_cabecera_vacia(self, documento_id)

    def registrar_abono(self, venta_id: uuid.UUID, payload: Any) -> VentaQueso:
        venta = self.repo.get_or_fail(venta_id)
        venta = _bloquear(self.db, venta)
        if venta.estado == ESTADO_ANULADA:
            raise BusinessError("La venta está anulada")
        valor = Decimal(payload.valor)
        if valor > venta.saldo:
            # Mismo motivo que en el abono de compra: los miles se separan con
            # punto y los decimales con coma (formato colombiano).
            raise BusinessError(
                f"El abono ({pesos(valor)}) supera el saldo ({pesos(venta.saldo)})"
            )
        self.db.add(
            AbonoVentaQueso(
                venta_id=venta.id, fecha=payload.fecha, valor=valor,
                observaciones=payload.observaciones, created_by=self.ctx.user_id,
            )
        )
        venta.abonado += valor
        venta.estado = _estado_pago(venta.valor_total, venta.abonado)
        venta.updated_by = self.ctx.user_id
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(venta, ["abonos"])
        self._audit("editar", venta.id, None, {"abono": float(valor), "estado": venta.estado})
        return venta

    def eliminar_abono(self, venta_id: uuid.UUID, abono_id: uuid.UUID) -> VentaQueso:
        """Elimina un abono mal registrado: baja el abonado y recalcula el estado."""
        venta = self.repo.get_or_fail(venta_id)
        venta = _bloquear(self.db, venta)
        abono = next((a for a in venta.abonos if a.id == abono_id), None)
        if abono is None:
            raise NotFoundError("Abono no encontrado")
        valor = Decimal(abono.valor)
        venta.abonado = max(venta.abonado - valor, CERO)
        venta.estado = _estado_pago(venta.valor_total, venta.abonado)
        venta.updated_by = self.ctx.user_id
        self.db.delete(abono)
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(venta, ["abonos"])
        self._audit(
            "editar", venta.id, None,
            {"abono_eliminado": float(valor), "estado": venta.estado},
        )
        return venta

    def anular(self, venta_id: uuid.UUID) -> VentaQueso:
        venta = self.repo.get_or_fail(venta_id)
        venta = _bloquear(self.db, venta)
        if venta.abonado > CERO:
            raise BusinessError(
                "No se puede anular una venta con abonos registrados"
            )
        antes = venta.estado
        venta.estado = ESTADO_ANULADA
        venta.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", venta.id, {"estado": antes}, {"estado": ESTADO_ANULADA})
        return venta

    def listar_filtrado(
        self, params: PageParams, *, search: str | None, estado: str | None,
        desde: date | None, hasta: date | None,
    ) -> tuple[list[VentaQueso], int]:
        extra = []
        if desde:
            extra.append(VentaQueso.fecha >= desde)
        if hasta:
            extra.append(VentaQueso.fecha <= hasta)
        return self.repo.list_paginated(params, search=search, estado=estado, extra_criteria=extra)


class DocumentoReventaService(BaseService[DocumentoReventa]):
    """LA FACTURA de reventa: una compra o una venta con VARIOS productos.

    ES EL ÚNICO CAMINO POR EL QUE SE ESCRIBEN RENGLONES, y las dos puertas pasan
    por aquí: la factura de N productos y el payload plano de siempre, que arma un
    documento de un renglón. Una implementación, dos puertas.

    NO GUARDA NI UNA CIFRA DE PLATA (ver `DocumentoReventa`). Todo lo que suena a
    total sale de sumar los renglones al leer.
    """

    repository_cls = DocumentoReventaRepository
    modulo = "reventa"

    # ------------------------------------------------------------ herramientas
    def _servicio_de_renglones(self, tipo: str) -> Any:
        """Quién sabe escribir los renglones de esta clase de factura.

        Las reglas de un renglón de compra (la merma, la borona, el CHECK de la
        unidad) y las de un renglón de venta (las existencias, el gasto por kilo o
        por barra) siguen viviendo en su servicio de siempre. Este servicio no las
        vuelve a escribir: las llama.
        """
        if tipo == TIPO_DOC_COMPRA:
            return CompraQuesoService(self.db, self.ctx)
        return VentaQuesoService(self.db, self.ctx)

    @staticmethod
    def _modelo_de_abono(tipo: str) -> Any:
        return AbonoCompraQueso if tipo == TIPO_DOC_COMPRA else AbonoVentaQueso

    @staticmethod
    def _campo_de_abono(tipo: str) -> str:
        return "compra_id" if tipo == TIPO_DOC_COMPRA else "venta_id"

    # ------------------------------------------------------------------ crear
    def crear_con_renglones(self, payload: Any) -> tuple[DocumentoReventa, list[Any]]:
        """Escribe la cabecera y sus N renglones EN UNA SOLA TRANSACCIÓN.

        EL ORDEN DE LOS PASOS ES LA GARANTÍA, no una preferencia de estilo:

        1. se calcula la plata de TODOS los renglones, sin tocar la base;
        2. se valida el CONJUNTO COMPLETO contra las existencias —sumando los
           renglones del mismo producto, que es lo que impide que dos renglones de
           300 kg pasen contra 400 kg disponibles—;
        3. y solo entonces se escribe: primero la cabecera (con el nombre del
           tercero canonizado con la MISMA regla del payload plano) y después sus
           renglones, que le copian la fecha y el nombre.

        Si algo falla en el 2, no se escribió ni la cabecera: la sesión de FastAPI
        hace rollback de toda la petición, así que no queda una factura a medias
        —lo fija test_dos_renglones_del_mismo_producto_se_suman_contra_el_disponible,
        que después del rechazo exige que el inventario no se haya movido—.
        """
        servicio = self._servicio_de_renglones(payload.tipo)
        datos = servicio.preparar_renglones(payload.renglones)
        servicio.exigir_cantidades(datos)

        documento = super().crear(
            {
                "tipo": payload.tipo,
                "fecha": payload.fecha,
                "tercero": servicio.canonizar_tercero(payload.tercero),
                "observaciones": payload.observaciones,
            }
        )
        filas = servicio.escribir_renglones(documento, datos)

        if getattr(payload, "pagada_de_contado", False):
            # Se paga TODA la factura de una. Va por el mismo derrame que un abono
            # normal: en una factura de un renglón eso es ponerle el abono completo
            # a esa fila, que es exactamente lo que hacía el payload plano.
            self._derramar(
                documento,
                filas,
                valor=sum((Decimal(f.valor_total) for f in filas), CERO),
                fecha=documento.fecha,
                observaciones="Pago de contado",
            )
        return documento, filas

    def crear(self, payload: Any) -> DocumentoReventa:
        documento, _ = self.crear_con_renglones(payload)
        return documento

    # --------------------------------------------------------------- el abono
    def _derramar(
        self,
        documento: DocumentoReventa,
        renglones: list[Any],
        *,
        valor: Decimal,
        fecha: date,
        observaciones: str | None,
    ) -> None:
        """EL ABONO A LA FACTURA SE DERRAMA, NO SE DIVIDE.

        Se aplica a los renglones en su orden y a cada uno le entra
        `min(lo que queda del abono, el saldo del renglón)`: el primero se llena,
        después el segundo, y así. Cuando el abono se acaba, los que siguen quedan
        intactos.

        POR QUÉ NO SE REPARTE PROPORCIONALMENTE, que es lo que uno escribiría
        primero. Un reparto proporcional divide, y dividir plata en pesos
        colombianos casi nunca da exacto: $100.000 entre tres renglones de
        $333.333, $333.333 y $333.334 deja centavos que hay que "acomodar" en
        alguno, y ahí nace el descuadre. Con el derrame NO HAY NINGUNA DIVISIÓN:
        cada cuota es una resta, la suma de las cuotas es el abono por
        construcción, y cada abono queda siendo UNA CIFRA ENTERA que el dueño puede
        señalar con el dedo y reconocer ("estos $500.000 se los abonó al queso").
        Un reparto proporcional aquí sería la forma más fácil de descuadrar la
        cartera.

        Se valida ANTES de escribir nada: el abono no puede pasarse del saldo de la
        factura. El saldo se cuenta acotando en cero el de cada renglón, con el
        mismo criterio de `saldo_pendiente` del repositorio: un renglón que quedó
        con saldo a favor (se le rebajó el precio después de pagarlo) no aumenta ni
        disminuye lo que la factura puede recibir, simplemente no recibe nada.
        """
        valor = Decimal(valor)
        vivos = [r for r in renglones if r.estado != ESTADO_ANULADA]
        capacidad = sum((max(Decimal(r.saldo), CERO) for r in vivos), CERO)
        if valor > capacidad:
            # pesos() y no "{:,.0f}": el formato con coma es gringo y "$1.200.000"
            # es como se lee en Colombia.
            raise BusinessError(
                f"El abono ({pesos(valor)}) supera el saldo de la factura "
                f"({pesos(capacidad)})"
            )
        modelo = self._modelo_de_abono(documento.tipo)
        campo = self._campo_de_abono(documento.tipo)
        restante = valor
        for renglon in vivos:
            if restante <= CERO:
                break
            cuota = min(restante, max(Decimal(renglon.saldo), CERO))
            if cuota <= CERO:
                continue
            self.db.add(
                modelo(
                    **{campo: renglon.id},
                    fecha=fecha,
                    valor=cuota,
                    observaciones=observaciones,
                    created_by=self.ctx.user_id,
                )
            )
            renglon.abonado = Decimal(renglon.abonado) + cuota
            renglon.estado = _estado_pago(renglon.valor_total, renglon.abonado)
            renglon.updated_by = self.ctx.user_id
            restante -= cuota
        self.db.flush()
        self._audit(
            "editar",
            documento.id,
            None,
            {"abono": float(valor), "renglones": len(vivos)},
        )

    def registrar_abono(self, documento_id: uuid.UUID, payload: Any) -> DocumentoReventa:
        """Un abono a la factura entera, que se derrama sobre sus renglones.

        Los renglones se toman con FOR UPDATE y EN ORDEN ESTABLE (ver
        `renglones_bloqueados`): sin candado, dos abonos a la vez leen el mismo
        `abonado` viejo y el segundo escribe encima del primero —se pierde un pago,
        el cliente reclama y en el sistema no está—; y sin un orden fijo, dos abonos
        a la MISMA factura se abrazarían en un deadlock.
        """
        documento = self.repo.get_or_fail(documento_id)
        renglones = self.repo.renglones_bloqueados(documento)
        if not [r for r in renglones if r.estado != ESTADO_ANULADA]:
            raise BusinessError("La factura está anulada")
        self._derramar(
            documento,
            renglones,
            valor=Decimal(payload.valor),
            fecha=payload.fecha,
            observaciones=payload.observaciones,
        )
        return documento

    # ------------------------------------------------------------- actualizar
    def actualizar(self, documento_id: uuid.UUID, payload: Any) -> DocumentoReventa:
        """Edita la cabecera y, si vienen, REHACE los renglones."""
        documento = self.repo.get_or_fail(documento_id)
        if payload.tipo != documento.tipo:
            raise BusinessError(
                "No se puede convertir una compra en venta ni al contrario: "
                "anule la factura y regístrela de nuevo"
            )
        data = payload.model_dump(exclude_unset=True)
        data.pop("tipo", None)
        nuevos = data.pop("renglones", None)
        servicio = self._servicio_de_renglones(documento.tipo)

        if data.get("tercero"):
            data["tercero"] = servicio.canonizar_tercero(data["tercero"])
        
        # LA CABECERA PRIMERO. Actualizar la cabecera antes de bloquear los renglones
        # garantiza el orden de candados Cabecera -> Renglón, evitando el deadlock
        # con la edición por renglón que toma los candados en ese mismo orden.
        documento = super().actualizar(documento_id, data)
        
        renglones = self.repo.renglones_bloqueados(documento)

        if nuevos is not None:
            vivos = [r for r in renglones if r.estado != ESTADO_ANULADA]
            if not vivos and renglones:
                raise BusinessError("No se pueden modificar los productos de una factura anulada")

            abonado = sum((Decimal(r.abonado) for r in renglones), CERO)
            if abonado > CERO:
                raise BusinessError(
                    f"Esta factura ya tiene abonos registrados ({pesos(abonado)}): "
                    f"para cambiar los productos hay que anularla y rehacerla"
                )

        # La cabecera que va a quedar, que es la que se les copia a los renglones.
        fecha = documento.fecha
        tercero = documento.tercero

        if nuevos is not None:
            datos = servicio.preparar_renglones(nuevos)
            # Se valida ANTES de borrar nada, devolviéndole al inventario lo que
            # tienen apartado los renglones que se van.
            servicio.exigir_cantidades(datos, devolviendo=vivos)
            for renglon in vivos:
                servicio.eliminar(renglon.id, cuidar_cabecera=False)
            servicio.escribir_renglones(documento, datos)
        else:
            for renglon in renglones:
                renglon.fecha = fecha
                setattr(
                    renglon,
                    "productor" if documento.tipo == TIPO_DOC_COMPRA else "cliente",
                    tercero,
                )
                renglon.updated_by = self.ctx.user_id
            self.db.flush()
        return documento

    # ------------------------------------------------------- anular y eliminar
    def anular(self, documento_id: uuid.UUID) -> DocumentoReventa:
        """Anula la factura anulando TODOS sus renglones, uno por uno y por el
        camino de siempre.

        Delegar en `anular` de cada renglón no es pereza: ahí vive el guardia que
        impide anular una compra cuyo queso YA SE VENDIÓ (anularla dejaría el
        inventario en negativo y desde ahí ninguna venta volvería a pasar el
        control de existencias). Ese guardia se recalcula renglón por renglón, así
        que una factura de tres compras se anula solo si las tres se pueden anular.
        """
        documento = self.repo.get_or_fail(documento_id)
        renglones = self.repo.renglones_bloqueados(documento)
        abonados = [r for r in renglones if Decimal(r.abonado) > CERO]
        if abonados:
            raise BusinessError(
                "No se puede anular una factura con abonos registrados: elimine "
                "primero los abonos de sus renglones"
            )
        servicio = self._servicio_de_renglones(documento.tipo)
        for renglon in renglones:
            if renglon.estado != ESTADO_ANULADA:
                servicio.anular(renglon.id)
        self._audit("editar", documento.id, None, {"anulada": True})
        return documento

    def eliminar(self, documento_id: uuid.UUID) -> None:
        """Borra la factura y sus renglones (en suave, como todo en el sistema).

        Cada renglón se va por su propio `eliminar`, que valida que no tenga abonos
        y se lleva sus soportes de pago del almacenamiento.
        """
        documento = self.repo.get_or_fail(documento_id)
        servicio = self._servicio_de_renglones(documento.tipo)
        renglones = self.repo.renglones_bloqueados(documento)
        # Se validan TODOS antes de borrar el primero: si el tercero tuviera abonos,
        # una factura de tres renglones quedaría con el primero borrado y los otros
        # dos vivos, o sea partida en dos.
        for renglon in renglones:
            servicio.validar_eliminar(renglon)
        # `cuidar_cabecera=False` porque la cabecera la borra este método, dos
        # líneas más abajo: si el último renglón se la llevara, el `get_or_fail` de
        # `super().eliminar` no la encontraría y esto respondería un 404 después de
        # haber borrado todo.
        for renglon in renglones:
            servicio.eliminar(renglon.id, cuidar_cabecera=False)
        super().eliminar(documento_id)

    # --------------------------------------------------------------- lecturas
    def _a_read(self, documento: DocumentoReventa, renglones: list[Any]) -> DocumentoReventaRead:
        """Arma la respuesta SUMANDO los renglones. Ninguna de estas cifras está
        guardada, y por eso ninguna puede desactualizarse.

        LA IGUALDAD QUE EL DUEÑO VERIFICA A MANO:
        `total + total_anulado` es exactamente la suma del `valor_total` de todos
        los renglones que van en la respuesta. Un renglón anulado no se esconde ni
        se le resta a nada: su plata sale aparte, que es la única forma de que la
        columna siga cerrando cuando algo se anula.
        """
        vivos = [r for r in renglones if r.estado != ESTADO_ANULADA]
        total = sum((Decimal(r.valor_total) for r in vivos), CERO)
        abonado = sum((Decimal(r.abonado) for r in vivos), CERO)
        saldo_doc = sum((Decimal(r.saldo) for r in vivos), CERO)
        saldo_a_favor_doc = sum((Decimal(r.saldo_a_favor) for r in vivos), CERO)
        anulado = sum(
            (Decimal(r.valor_total) for r in renglones if r.estado == ESTADO_ANULADA),
            CERO,
        )
        esquema = CompraQuesoRead if documento.tipo == TIPO_DOC_COMPRA else VentaQuesoRead
        return DocumentoReventaRead(
            id=documento.id,
            empresa_id=documento.empresa_id,
            estado=documento.estado,
            created_at=documento.created_at,
            updated_at=documento.updated_at,
            tipo=documento.tipo,
            fecha=documento.fecha,
            tercero=documento.tercero,
            observaciones=documento.observaciones,
            total=_dinero(total),
            abonado=_dinero(abonado),
            saldo=_dinero(saldo_doc),
            saldo_a_favor=_dinero(saldo_a_favor_doc),
            total_anulado=_dinero(anulado),
            estado_pago=_estado_pago_documento(renglones),
            cantidad_renglones=len(renglones),
            renglones=[esquema.model_validate(r) for r in renglones],
        )

    def leer(self, documento_id: uuid.UUID) -> DocumentoReventaRead:
        documento = self.repo.get_or_fail(documento_id)
        return self._a_read(documento, self.repo.renglones(documento))

    def listar_filtrado(
        self, params: PageParams, *, tipo: str | None, search: str | None,
        desde: date | None, hasta: date | None,
    ) -> tuple[list[DocumentoReventaRead], int]:
        documentos, total = self.repo.listar_paginado(
            params, tipo=tipo, search=search, desde=desde, hasta=hasta
        )
        # Los renglones de TODA la página de un solo viaje por tabla: pedirlos
        # documento por documento serían veinte consultas que crecen con la página.
        por_documento = self.repo.renglones_de(documentos)
        return [self._a_read(d, por_documento[d.id]) for d in documentos], total


class SaldoAnteriorService(BaseService[SaldoAnterior]):
    """Saldos a medio pagar traídos del sistema anterior.

    Hermano de las compras y las ventas en todo lo que es plata (estado de
    pago, abonos, anulación), pero SIN kilos: no toca inventario, no mueve la
    ganancia y no aparece en el desglose por producto. Solo suma en lo que se
    debe cobrar y en lo que se debe pagar.
    """

    repository_cls = SaldoAnteriorRepository
    modulo = "reventa"

    def _nombres_del_tipo(self, tipo: str) -> list[str]:
        """Contra qué lista se canoniza el nombre del tercero: un saldo por
        'cobrar' es de un CLIENTE y uno por 'pagar' es de un PRODUCTOR.

        Primero van los nombres que ya usan las ventas o las compras, para
        adoptar esa escritura: es la que agrupa el estado de cuenta y el detalle
        por productor, y así "carlos ricaute" no queda como un tercero distinto
        de "Carlos Ricaute" y su deuda no sale partida en dos.
        """
        if tipo == TIPO_SALDO_PAGAR:
            del_modulo = CompraQuesoRepository(
                self.db, self.ctx.empresa_id
            ).nombres_productores()
        else:
            del_modulo = VentaQuesoRepository(self.db, self.ctx.empresa_id).nombres_clientes()
        return del_modulo + self.repo.nombres_terceros(tipo)

    def _canonizar(self, data: dict[str, Any], tipo: str) -> dict[str, Any]:
        if data.get("tercero"):
            data["tercero"] = _canonizar_nombre(data["tercero"], self._nombres_del_tipo(tipo))
        return data

    def crear(self, payload: Any) -> SaldoAnterior:
        data = payload.model_dump(exclude_unset=True)
        tipo = data.get("tipo") or TIPO_SALDO_COBRAR
        data["tipo"] = tipo
        valor_total = Decimal(data["valor_total"])
        abonado = Decimal(data.get("abonado") or CERO)
        if abonado > valor_total:
            raise BusinessError(
                f"Lo abonado ({pesos(abonado)}) supera el valor del saldo "
                f"({pesos(valor_total)})"
            )
        data["abonado"] = abonado
        data["estado"] = _estado_pago(valor_total, abonado)
        saldo = super().crear(self._canonizar(data, tipo))
        if abonado > CERO:
            # Lo que ya venía abonado en el libro viejo queda también como abono,
            # con la fecha del documento: si no, el historial sale vacío y el
            # detalle de abonos no cuadra con la columna "abonado".
            saldo.abonos.append(
                AbonoSaldoAnterior(
                    fecha=saldo.fecha,
                    valor=abonado,
                    observaciones="Abonado en el libro anterior",
                    created_by=self.ctx.user_id,
                )
            )
            self.db.flush()
        return saldo

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> SaldoAnterior:
        actual = self.repo.get_or_fail(entity_id)
        if actual.estado == ESTADO_ANULADA:
            raise BusinessError("No se puede modificar un saldo anulado")
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        # El TIPO no se cambia después de creado: un saldo por 'cobrar' es de un
        # CLIENTE y uno por 'pagar' de un PRODUCTOR, y el nombre del tercero ya
        # quedó canonizado contra la lista de ese lado. Cambiarlo por PUT dejaba a
        # una clienta convertida en fila del detalle por productor y le sacaba su
        # deuda del estado de cuenta. Re-canonizar el nombre no alcanza: habría
        # que mover de lado también los abonos y el historial, así que se pide
        # anular y cargar de nuevo, que además queda en la bitácora. Mandar el
        # mismo tipo que ya tiene sí se acepta (el formulario envía todo el
        # objeto).
        tipo = data.get("tipo") or actual.tipo
        if tipo != actual.tipo:
            raise BusinessError(
                f"No se puede cambiar el tipo de un saldo de la cuenta anterior "
                f"(está cargado como '{actual.tipo}'): anúlelo y cargue uno nuevo "
                f"del tipo correcto"
            )
        data["tipo"] = tipo
        valor_total = Decimal(data.get("valor_total") or actual.valor_total)
        data["valor_total"] = valor_total
        # Se puede editar aunque tenga abonos: se recalcula el estado con lo ya
        # abonado y el saldo queda al día (igual que en compras y ventas). Lo que
        # NO se permite es dejar el total por debajo de lo ya abonado: el saldo se
        # vuelve NEGATIVO y ese negativo RESTA de la cartera, así que la tarjeta
        # mostraría menos plata sin cobrar de la que hay y el estado de cuenta le
        # rebajaría la deuda al cliente.
        if valor_total < Decimal(actual.abonado):
            raise BusinessError(
                f"El total no puede quedar por debajo de lo ya abonado "
                f"({pesos(actual.abonado)}); elimine primero los abonos que sobren"
            )
        data["estado"] = _estado_pago(valor_total, actual.abonado)
        return super().actualizar(entity_id, self._canonizar(data, tipo))

    def validar_eliminar(self, obj: SaldoAnterior) -> None:
        if obj.abonado > CERO:
            raise BusinessError(
                "No se puede eliminar un saldo con abonos; elimine primero los abonos o anúlelo"
            )

    def registrar_abono(self, saldo_id: uuid.UUID, payload: Any) -> SaldoAnterior:
        saldo = self.repo.get_or_fail(saldo_id)
        saldo = _bloquear(self.db, saldo)
        if saldo.estado == ESTADO_ANULADA:
            raise BusinessError("El saldo está anulado")
        valor = Decimal(payload.valor)
        if valor > saldo.saldo:
            # pesos() y no "{:,.0f}": el formato con coma es gringo y "$1,200,000"
            # en Colombia se lee como un peso con veinte centavos.
            raise BusinessError(
                f"El abono ({pesos(valor)}) supera el saldo ({pesos(saldo.saldo)})"
            )
        # Se agrega A LA RELACIÓN, no con db.add(): la colección ya viene cargada
        # (lazy="selectin") y un db.add() suelto la dejaría desactualizada, así
        # que la respuesta saldría sin el abono que se acaba de registrar.
        saldo.abonos.append(
            AbonoSaldoAnterior(
                fecha=payload.fecha, valor=valor,
                observaciones=payload.observaciones, created_by=self.ctx.user_id,
            )
        )
        saldo.abonado += valor
        saldo.estado = _estado_pago(saldo.valor_total, saldo.abonado)
        saldo.updated_by = self.ctx.user_id
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(saldo, ["abonos"])
        self._audit("editar", saldo.id, None, {"abono": float(valor), "estado": saldo.estado})
        return saldo

    def eliminar_abono(self, saldo_id: uuid.UUID, abono_id: uuid.UUID) -> SaldoAnterior:
        """Elimina un abono mal registrado: baja el abonado y recalcula el estado."""
        saldo = self.repo.get_or_fail(saldo_id)
        saldo = _bloquear(self.db, saldo)
        abono = next((a for a in saldo.abonos if a.id == abono_id), None)
        if abono is None:
            raise NotFoundError("Abono no encontrado")
        valor = Decimal(abono.valor)
        saldo.abonado = max(saldo.abonado - valor, CERO)
        saldo.estado = _estado_pago(saldo.valor_total, saldo.abonado)
        saldo.updated_by = self.ctx.user_id
        # Se saca de la relación (el cascade delete-orphan borra la fila): así la
        # colección en memoria queda igual que la base y la respuesta ya no lo trae.
        saldo.abonos.remove(abono)
        self.db.flush()
        # Se refresca la lista de abonos antes de devolverla. La relación es
        # lazy="selectin": ya venía cargada de la lectura anterior, así que sin
        # esto la respuesta sale con el `abonado` nuevo pero SIN el abono en la
        # lista (o con el que se acaba de borrar todavía dentro). La pantalla
        # pinta las dos cosas juntas y se contradicen a la vista.
        self.db.refresh(saldo, ["abonos"])
        self._audit(
            "editar", saldo.id, None,
            {"abono_eliminado": float(valor), "estado": saldo.estado},
        )
        return saldo

    def anular(self, saldo_id: uuid.UUID) -> SaldoAnterior:
        saldo = self.repo.get_or_fail(saldo_id)
        saldo = _bloquear(self.db, saldo)
        if saldo.abonado > CERO:
            raise BusinessError("No se puede anular un saldo con abonos registrados")
        antes = saldo.estado
        saldo.estado = ESTADO_ANULADA
        saldo.updated_by = self.ctx.user_id
        self.db.flush()
        self._audit("editar", saldo.id, {"estado": antes}, {"estado": ESTADO_ANULADA})
        return saldo

    def listar_filtrado(
        self, params: PageParams, *, tipo: str | None, search: str | None,
        estado: str | None, desde: date | None, hasta: date | None,
    ) -> tuple[list[SaldoAnterior], int]:
        extra = []
        if desde:
            extra.append(SaldoAnterior.fecha >= desde)
        if hasta:
            extra.append(SaldoAnterior.fecha <= hasta)
        return self.repo.list_paginated(
            params, search=search, estado=estado, filters={"tipo": tipo},
            extra_criteria=extra,
        )


class ConversionBoronaService(BaseService[ConversionBorona]):
    """Pasar queso del inventario de reventa a borona."""

    repository_cls = ConversionBoronaRepository
    modulo = "reventa"

    def crear(self, payload: Any) -> ConversionBorona:
        data = payload.model_dump(exclude_unset=True)
        disponible = ReventaResumenService.queso_disponible(self.db, self.ctx)
        if Decimal(data["kilos"]) > disponible:
            raise BusinessError(f"Solo hay {disponible} kg de queso disponibles")
        # La merma es pérdida sin valor: no lleva precio.
        if data.get("destino") == DESTINO_MERMA:
            data["precio_kilo"] = CERO
        return super().crear(data)


# ----------------------------------- adjuntos (soportes de transferencia)
logger_adjuntos = get_logger("reventa.adjuntos")

# Qué se acepta y con qué extensión se guarda en el bucket.
#
# SE ACEPTA PDF, y es una decisión, no un descuido: los bancos colombianos
# (Bancolombia, Nequi, Davivienda) entregan el comprobante de una transferencia
# como PDF descargable, y ese PDF ES el soporte bueno — más que una foto de la
# pantalla. Rechazarlo obligaría al dueño a tomarle una foto al comprobante que
# ya tenía, que es peor soporte y trabajo de más.
#
# HEIC/HEIF entran porque es lo que produce un iPhone por defecto. El navegador
# a veces no sabe dibujar la miniatura, pero rechazar la foto de un iPhone con
# un "tipo no permitido" sería inexplicable para quien la está mandando.
#
# No entran videos ni ofimática: esto es el respaldo de que se pagó, no un
# archivador. Cada tipo que se abre es un tipo más que hay que servir con un
# enlace firmado, y un .html o un .svg firmados serían código ejecutándose en el
# navegador de quien reciba el enlace.
TIPOS_ADJUNTO_PERMITIDOS: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
}

TIPOS_EN_CRISTIANO = "fotos JPG, PNG, WEBP o HEIC, y comprobantes en PDF"

# Marcas HEIF/HEIC: los primeros bytes son el tamaño de la caja, luego 'ftyp' y
# luego la marca. Se listan las que usan las cámaras de los teléfonos.
MARCAS_HEIC = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"hevs"}
MARCAS_HEIF = {b"mif1", b"msf1"}


def _detectar_tipo(cabeza: bytes) -> str | None:
    """Qué es el archivo DE VERDAD, mirándole los primeros bytes.

    No se confía en el Content-Type que manda el navegador ni en la extensión
    del nombre: los dos los pone quien sube y los dos se cambian solos. Y aquí
    importa de verdad, porque de estos objetos se reparten enlaces firmados que
    se abren en el navegador de otra persona: un .html disfrazado de .jpg sería
    una página que corre en el dominio del almacenamiento con un enlace que el
    dueño repartió de buena fe por WhatsApp.

    Devuelve el tipo reconocido, o None si no es ninguno de los permitidos.
    """
    if cabeza.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if cabeza.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if cabeza[:4] == b"RIFF" and cabeza[8:12] == b"WEBP":
        return "image/webp"
    if cabeza[4:8] == b"ftyp":
        marca = cabeza[8:12]
        if marca in MARCAS_HEIC:
            return "image/heic"
        if marca in MARCAS_HEIF:
            return "image/heif"
    if cabeza.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _tamano_legible(bytes_: int) -> str:
    """"4,2 MB" — con coma decimal, que es como se escribe en Colombia."""
    if bytes_ < 1024:
        return f"{bytes_} bytes"
    if bytes_ < 1024 * 1024:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_ / (1024 * 1024):.1f}".replace(".", ",") + " MB"


class AdjuntoReventaService(BaseService[AdjuntoReventa]):
    """Soportes de pago de compras y ventas de reventa, guardados en R2.

    TRES CAMINOS DISTINTOS PARA MIRAR UN ARCHIVO, a propósito:

    - VER (`listar`): enlaces de minutos, para la pantalla. Se firman de nuevo
      cada vez que se abre el detalle.
    - COMPARTIR (`compartir`): un enlace de días para UNA imagen, para mandarlo
      por WhatsApp. Sale con la fecha de caducidad escrita en cristiano y queda
      registrado en la auditoría: es información de pago saliendo del sistema.
    - BORRAR (`eliminar_adjunto`): quita la fila Y el objeto en R2.

    Los tres empiezan por comprobar que la compra o la venta sea DE LA EMPRESA
    de quien pregunta. Esa comprobación no está en un `if` suelto: se hace
    buscando el documento con su propio repositorio, que ya filtra por
    `empresa_id` y `deleted_at IS NULL`. Si no es suyo, no aparece, y sale un
    404 antes de que se firme absolutamente nada.
    """

    repository_cls = AdjuntoReventaRepository
    modulo = "reventa"

    # ------------------------------------------------------------- utilidades
    @property
    def _max_bytes(self) -> int:
        return settings.ADJUNTOS_MAX_MB * 1024 * 1024

    def _documento(
        self, *, compra_id: uuid.UUID | None = None, venta_id: uuid.UUID | None = None
    ) -> CompraQueso | VentaQueso:
        """La compra o la venta, SIEMPRE por el repositorio con filtro de empresa.

        Es el candado multiempresa de todo el módulo de adjuntos: el documento de
        otra empresa no existe para esta consulta y `get_or_fail` levanta 404.
        """
        if compra_id is not None:
            return CompraQuesoRepository(self.db, self.ctx.empresa_id).get_or_fail(compra_id)
        return VentaQuesoRepository(self.db, self.ctx.empresa_id).get_or_fail(venta_id)

    def _adjunto(self, adjunto_id: uuid.UUID) -> AdjuntoReventa:
        """El adjunto, con el mismo candado: repositorio con filtro de empresa."""
        return self.repo.get_or_fail(adjunto_id)

    def _clave(self, *, carpeta: str, documento_id: uuid.UUID, extension: str) -> str:
        """`{empresa_id}/reventa/{compras|ventas}/{documento_id}/{uuid}{ext}`.

        El empresa_id va DENTRO de la llave a propósito: aunque alguien adivinara
        el resto, la llave de un archivo de otra empresa empieza por un uuid que
        no es el suyo. Y el nombre del archivo NO entra en la llave: lo escribe
        quien sube, y un nombre con `../` o con caracteres raros terminaría
        creando objetos donde no van.
        """
        return (
            f"{self.ctx.empresa_id}/reventa/{carpeta}/{documento_id}/"
            f"{uuid.uuid4().hex}{extension}"
        )

    def _leer_y_validar(self, archivo: Any) -> tuple[bytes, str, str, str]:
        """(contenido, tipo real, extension, nombre) — o BusinessError legible.

        Se mide ANTES de leer: un archivo de 400 MB no se carga en memoria solo
        para después decir que no cabe. En el campo la señal es mala y una subida
        equivocada se nota tarde; lo que no puede pasar es que tumbe el servidor.
        """
        nombre = (getattr(archivo, "filename", "") or "").strip() or "soporte"
        nombre = nombre[:255]

        origen = archivo.file
        try:
            origen.seek(0, os.SEEK_END)
            tamano = origen.tell()
            origen.seek(0)
        except (AttributeError, OSError):  # pragma: no cover - flujo no medible
            tamano = -1

        if tamano == 0:
            raise BusinessError(f"El archivo «{nombre}» está vacío")
        if tamano > self._max_bytes:
            raise BusinessError(
                f"«{nombre}» pesa {_tamano_legible(tamano)} y el máximo son "
                f"{settings.ADJUNTOS_MAX_MB} MB. Tome la foto en menor calidad "
                f"o mande el comprobante en PDF"
            )

        contenido = origen.read()
        # Segunda medición, por si la de arriba no se pudo hacer.
        if len(contenido) > self._max_bytes:
            raise BusinessError(
                f"«{nombre}» pesa {_tamano_legible(len(contenido))} y el máximo son "
                f"{settings.ADJUNTOS_MAX_MB} MB"
            )
        if not contenido:
            raise BusinessError(f"El archivo «{nombre}» está vacío")

        tipo = _detectar_tipo(contenido[:64])
        if tipo is None or tipo not in TIPOS_ADJUNTO_PERMITIDOS:
            raise BusinessError(
                f"«{nombre}» no es una imagen ni un PDF. Solo se aceptan "
                f"{TIPOS_EN_CRISTIANO}"
            )
        return contenido, tipo, TIPOS_ADJUNTO_PERMITIDOS[tipo], nombre

    def _nombre_de_quien_sube(self) -> str | None:
        usuario = getattr(self.ctx, "user", None)
        if usuario is None:
            return None
        completo = f"{getattr(usuario, 'nombre', '') or ''} {getattr(usuario, 'apellido', '') or ''}"
        return completo.strip()[:150] or None

    def _a_read(self, adjunto: AdjuntoReventa, cliente: R2Client | None) -> AdjuntoRead:
        """Fila lista para la pantalla, con enlace corto si hay almacenamiento."""
        url = None
        expira = None
        if cliente is not None:
            segundos = max(60, settings.R2_URL_VER_MINUTOS * 60)
            url = cliente.enlace_firmado(
                clave=adjunto.object_key,
                segundos=segundos,
                nombre_descarga=adjunto.nombre_archivo,
            )
            expira = caducidad_utc(segundos)
        return AdjuntoRead(
            id=adjunto.id,
            compra_id=adjunto.compra_id,
            venta_id=adjunto.venta_id,
            nombre_archivo=adjunto.nombre_archivo,
            content_type=adjunto.content_type,
            tamano_bytes=adjunto.tamano_bytes,
            es_imagen=adjunto.es_imagen,
            subido_por_nombre=adjunto.subido_por_nombre,
            created_at=adjunto.created_at,
            url=url,
            url_expira=expira,
        )

    # ------------------------------------------------------------------- ver
    def listar(
        self, *, compra_id: uuid.UUID | None = None, venta_id: uuid.UUID | None = None
    ) -> AdjuntosLista:
        """Los soportes del documento, cada uno con su enlace de CORTA duración.

        Sin R2 configurado responde 200 con `disponible: false` en vez de un
        error: no es culpa de quien pregunta y el resto de la pantalla tiene que
        poder seguir usándose. Las filas igual salen (nombre, peso, quién lo
        subió), solo que sin enlace para abrirlas.
        """
        self._documento(compra_id=compra_id, venta_id=venta_id)
        filas = self.repo.de_documento(compra_id=compra_id, venta_id=venta_id)
        cupo = max(0, settings.ADJUNTOS_MAX_POR_DOCUMENTO - len(filas))
        if not r2_configurado():
            return AdjuntosLista(
                disponible=False,
                mensaje=MENSAJE_NO_CONFIGURADO,
                cupo_restante=0,
                adjuntos=[self._a_read(f, None) for f in filas],
            )
        cliente = R2Client()
        return AdjuntosLista(
            disponible=True,
            cupo_restante=cupo,
            adjuntos=[self._a_read(f, cliente) for f in filas],
        )

    # ----------------------------------------------------------------- subir
    def subir(
        self,
        archivos: list[Any],
        *,
        compra_id: uuid.UUID | None = None,
        venta_id: uuid.UUID | None = None,
    ) -> AdjuntosLista:
        """Sube N soportes a una compra o a una venta.

        SE VALIDAN TODOS ANTES DE SUBIR NINGUNO. Si la tercera foto no sirve, no
        tiene sentido que las dos primeras ya estén en el bucket: el dueño
        corrige y vuelve a mandar las tres, y quedarían duplicadas.

        Y si R2 falla a mitad de camino, se borran los objetos que alcanzaron a
        subir. La excepción hace rollback de la sesión, así que las filas
        desaparecen; sin este barrido los archivos quedarían en el bucket sin
        ninguna fila que los nombre — invisibles, imborrables y cobrando.
        """
        documento = self._documento(compra_id=compra_id, venta_id=venta_id)
        if not archivos:
            raise BusinessError("No se recibió ningún archivo")
        if not r2_configurado():
            raise BusinessError(MENSAJE_NO_CONFIGURADO, code="r2_no_configurado")

        # Una compra o una venta anulada es un documento muerto: no se le siguen
        # colgando soportes de pago, igual que no se le registran abonos.
        if getattr(documento, "estado", "") == ESTADO_ANULADA:
            raise BusinessError(
                "El documento está anulado: no se le pueden agregar soportes"
            )

        ya_tiene = self.repo.contar_de(compra_id=compra_id, venta_id=venta_id)
        tope = settings.ADJUNTOS_MAX_POR_DOCUMENTO
        if ya_tiene + len(archivos) > tope:
            raise BusinessError(
                f"Caben máximo {tope} soportes por documento. Ya hay {ya_tiene} "
                f"y está mandando {len(archivos)}"
            )

        validados = [self._leer_y_validar(a) for a in archivos]

        carpeta = "compras" if compra_id is not None else "ventas"
        documento_id = compra_id if compra_id is not None else venta_id
        cliente = R2Client()
        subidas: list[str] = []
        quien = self._nombre_de_quien_sube()
        try:
            for contenido, tipo, extension, nombre in validados:
                clave = self._clave(
                    carpeta=carpeta, documento_id=documento_id, extension=extension
                )
                cliente.subir(clave=clave, contenido=contenido, content_type=tipo)
                subidas.append(clave)
                adjunto = self.repo.create(
                    self._prepare_create_data(
                        {
                            "compra_id": compra_id,
                            "venta_id": venta_id,
                            "object_key": clave,
                            "nombre_archivo": nombre,
                            "content_type": tipo,
                            "tamano_bytes": len(contenido),
                            "subido_por_nombre": quien,
                        }
                    )
                )
                self._audit("crear", adjunto.id, None, serialize_entity(adjunto))
        except Exception:
            for clave in subidas:
                try:
                    cliente.borrar(clave)
                except Exception:  # pragma: no cover - barrido de mejor esfuerzo
                    logger_adjuntos.warning(
                        "Quedó un objeto huérfano en R2 tras una subida fallida: %s", clave
                    )
            raise

        return self.listar(compra_id=compra_id, venta_id=venta_id)

    # ------------------------------------------------------------- compartir
    def compartir(self, adjunto_id: uuid.UUID) -> EnlaceCompartido:
        """Enlace de MÁS duración para UNA imagen, para mandarla por fuera.

        Queda en la auditoría con su caducidad: es un soporte de pago —con
        nombres, cuentas y montos— saliendo del sistema hacia un enlace que
        cualquiera que lo reciba puede reenviar. Que quede escrito quién lo
        repartió y hasta cuándo sirve.
        """
        adjunto = self._adjunto(adjunto_id)
        if not r2_configurado():
            raise BusinessError(MENSAJE_NO_CONFIGURADO, code="r2_no_configurado")

        dias = max(1, min(settings.R2_URL_COMPARTIR_DIAS, 7))
        segundos = dias * 24 * 60 * 60
        url = R2Client().enlace_firmado(
            clave=adjunto.object_key,
            segundos=segundos,
            nombre_descarga=adjunto.nombre_archivo,
        )
        expira = caducidad_utc(segundos)
        # Se audita el HECHO de compartir, nunca la URL: la URL lleva la firma
        # dentro, así que guardarla en la auditoría sería guardar el acceso.
        self._audit(
            "compartir",
            adjunto.id,
            None,
            {
                "nombre_archivo": adjunto.nombre_archivo,
                "compra_id": str(adjunto.compra_id) if adjunto.compra_id else None,
                "venta_id": str(adjunto.venta_id) if adjunto.venta_id else None,
                "expira": expira.isoformat(),
                "dias": dias,
            },
        )
        return EnlaceCompartido(
            url=url,
            nombre_archivo=adjunto.nombre_archivo,
            expira=expira,
            expira_texto=texto_caducidad(expira),
            dias=dias,
        )

    # ---------------------------------------------------------------- borrar
    def limpiar_de_documento(
        self, *, compra_id: uuid.UUID | None = None, venta_id: uuid.UUID | None = None
    ) -> int:
        """Se lleva los soportes cuando se borra la compra o la venta entera.

        Sin esto, borrar una compra dejaba sus fotos en el bucket para siempre:
        el documento ya no existe, así que nadie las puede ver ni borrar desde la
        aplicación, y el dueño sigue pagando ese almacenamiento sin saberlo.

        EN R2 ES DE MEJOR ESFUERZO, al revés que en `eliminar_adjunto`. Ahí el
        fallo tiene que detener la operación porque borrar el soporte ES la
        operación; aquí la operación es borrar la compra, y dejarla a medias
        —o negarla— porque el bucket no respondió sería peor: el dueño quedaría
        sin poder corregir un registro equivocado por un problema de red. Lo que
        no se pudo borrar queda en el log.

        Solo lo llaman los servicios de compra y venta, DESPUÉS de haber validado
        que el documento sí se puede borrar: si no, unos soportes se perderían
        por un borrado que al final no ocurre.
        """
        filas = self.repo.de_documento(compra_id=compra_id, venta_id=venta_id)
        if not filas:
            return 0
        cliente = R2Client() if r2_configurado() else None
        for fila in filas:
            if cliente is not None:
                try:
                    cliente.borrar(fila.object_key)
                except Exception:
                    logger_adjuntos.warning(
                        "Quedó un objeto en R2 al borrar el documento: %s", fila.object_key
                    )
            self.repo.soft_delete(fila, deleted_by=self.ctx.user_id)
        return len(filas)

    def eliminar_adjunto(self, adjunto_id: uuid.UUID) -> None:
        """Borra el soporte: PRIMERO el objeto en R2 y después la fila.

        En ese orden a propósito. Si R2 falla, se propaga el error y la fila
        sobrevive: el dueño ve que no se borró y vuelve a intentar. Al revés
        —fila primero— un fallo de R2 dejaría el archivo en el bucket sin nada
        que lo nombre: nadie podría verlo, nadie podría borrarlo, y se seguiría
        pagando su almacenamiento para siempre.
        """
        adjunto = self._adjunto(adjunto_id)
        if not r2_configurado():
            raise BusinessError(MENSAJE_NO_CONFIGURADO, code="r2_no_configurado")
        R2Client().borrar(adjunto.object_key)
        antes = serialize_entity(adjunto)
        self.repo.soft_delete(adjunto, deleted_by=self.ctx.user_id)
        self._audit("eliminar", adjunto.id, antes, serialize_entity(adjunto))


class ReventaResumenService:
    """Resumen del negocio de reventa (independiente de contabilidad)."""

    def __init__(self, db, ctx):
        self.db = db
        self.ctx = ctx
        self.compras = CompraQuesoRepository(db, ctx.empresa_id)
        self.ventas = VentaQuesoRepository(db, ctx.empresa_id)
        self.conversiones = ConversionBoronaRepository(db, ctx.empresa_id)
        self.saldos = SaldoAnteriorRepository(db, ctx.empresa_id)

    @staticmethod
    def queso_disponible(db, ctx) -> Decimal:
        compras = CompraQuesoRepository(db, ctx.empresa_id)
        ventas = VentaQuesoRepository(db, ctx.empresa_id)
        conversiones = ConversionBoronaRepository(db, ctx.empresa_id)
        kilos_comprados, _, _ = compras.acumulados()
        kilos_queso_vendidos, _, _ = ventas.acumulados()
        return kilos_comprados - kilos_queso_vendidos - conversiones.total_convertido()

    @staticmethod
    def borona_disponible(db, ctx) -> Decimal:
        compras = CompraQuesoRepository(db, ctx.empresa_id)
        ventas = VentaQuesoRepository(db, ctx.empresa_id)
        conversiones = ConversionBoronaRepository(db, ctx.empresa_id)
        _, borona_de_compras, _ = compras.acumulados()
        _, borona_vendida, _ = ventas.acumulados()
        return borona_de_compras + conversiones.total_a_borona() - borona_vendida

    @staticmethod
    def barras_disponibles(db, ctx) -> Decimal:
        """Barras de mozzarella en bodega: compradas − vendidas, histórico.

        LA CUENTA MÁS CORTA DE LAS TRES, Y NO ES UN OLVIDO: no le resta
        conversiones porque la mozzarella no participa en ellas. La barra entra
        como barra y sale como barra: no se desmenuza para pasarla a borona (eso es
        desmenuzar queso) y no pierde peso en el camino, porque no se está pesando.
        Si algún día una barra se daña, eso es una BAJA DE UNIDADES, un movimiento
        propio que hoy no existe, y nunca kilos metidos en la tabla de ajustes.

        Es el hermano de `queso_disponible` y `borona_disponible`, en su unidad, y
        el que usan los tres guardias de existencias de la mozzarella (crear venta,
        editar venta y anular compra).
        """
        compras = CompraQuesoRepository(db, ctx.empresa_id)
        ventas = VentaQuesoRepository(db, ctx.empresa_id)
        return compras.barras_acumuladas() - ventas.barras_acumuladas()

    @staticmethod
    def _costo_de(kilos: Decimal, kilos_comprados: Decimal, total_compras: Decimal) -> Decimal:
        """Costo de esas UNIDADES al precio promedio de compra del período.
        Divide sin redondear antes de multiplicar para no acumular error.

        Sirve para las dos unidades —se le pasan kilos con kilos y barras con
        barras—, y por eso los nombres de los parámetros hablan de kilos: es el uso
        original. Lo que NO puede hacerse nunca es mezclar: pasarle barras con un
        total de plata de kilos daría un costo por barra inventado.
        """
        if not kilos_comprados:
            return CERO
        return (kilos * total_compras / kilos_comprados).quantize(DOS_DECIMALES)

    @classmethod
    def _fila_producto(
        cls,
        producto: str,
        kilos: Decimal,
        ingreso: Decimal,
        costo: Decimal,
        gastos: Decimal,
        costo_kilo: Decimal,
        kilos_vendidos: Decimal | None = None,
    ) -> GananciaProducto:
        """`kilos` = kilos del lote comprado que fueron a este destino.
        `kilos_vendidos` = kilos realmente vendidos (solo difiere en la borona).
        """
        vendidos = kilos if kilos_vendidos is None else kilos_vendidos
        # Sin costo pero con kilos = la compra quedó fuera del período; decirlo
        # en vez de mostrar $0 de pérdida como si no hubiera costado nada.
        nota = NOTAS_PRODUCTO[producto]
        if kilos > CERO and costo == CERO and producto in ("merma", "pendiente"):
            nota = NOTA_SIN_COSTO
        return GananciaProducto(
            producto=producto,
            etiqueta=ETIQUETAS_PRODUCTO[producto],
            nota=nota,
            unidad=UNIDAD_KILO,
            kilos=kilos,
            kilos_vendidos=vendidos,
            ingreso=ingreso,
            costo=costo,
            gastos=gastos,
            ganancia=(ingreso - costo - gastos).quantize(DOS_DECIMALES),
            precio_venta_kilo=(
                (ingreso / vendidos).quantize(DOS_DECIMALES) if vendidos else CERO
            ),
            costo_kilo=costo_kilo,
        )

    @classmethod
    def _fila_barras(
        cls,
        producto: str,
        barras: Decimal,
        ingreso: Decimal,
        costo: Decimal,
        gastos: Decimal,
        costo_barra: Decimal,
        barras_vendidas: Decimal | None = None,
    ) -> GananciaProducto:
        """Un renglón del desglose MEDIDO EN BARRAS.

        Es un método aparte de `_fila_producto` y no un parámetro `unidad` de él, y
        esa es la decisión: así los campos de kilos quedan en CERO por construcción
        y no porque alguien se acuerde de pasarlos en cero. Un renglón de barras que
        pudiera traer kilos distintos de cero es exactamente lo que haría que la
        columna `kilos` del desglose deje de ser kilos.

        Los campos de plata (ingreso, costo, gastos, ganancia) son los MISMOS que en
        los renglones de kilos, sin sufijo de unidad: los pesos son pesos y esa
        columna sí se suma de arriba abajo.
        """
        vendidas = barras if barras_vendidas is None else barras_vendidas
        return GananciaProducto(
            producto=producto,
            etiqueta=ETIQUETAS_PRODUCTO[producto],
            nota=NOTAS_PRODUCTO[producto],
            unidad=UNIDAD_BARRA,
            # Los dos campos de kilos en cero: este renglón no tiene kilos.
            kilos=CERO,
            kilos_vendidos=CERO,
            barras=barras,
            barras_vendidas=vendidas,
            ingreso=ingreso,
            costo=costo,
            gastos=gastos,
            ganancia=(ingreso - costo - gastos).quantize(DOS_DECIMALES),
            # Y los dos precios por kilo también en cero, por lo mismo: los de
            # barras van en sus propios campos.
            precio_venta_kilo=CERO,
            costo_kilo=CERO,
            precio_venta_barra=(
                (ingreso / vendidas).quantize(DOS_DECIMALES) if vendidas else CERO
            ),
            costo_barra=costo_barra,
        )

    @classmethod
    def _filas_por_producto(
        cls,
        *,
        kilos_comprados: Decimal,
        total_compras: Decimal,
        costo_kilo: Decimal,
        kilos_queso: Decimal,
        ventas_queso: Decimal,
        gastos_queso: Decimal,
        kilos_a_borona: Decimal,
        kilos_borona_vendidos: Decimal,
        ventas_borona: Decimal,
        gastos_borona: Decimal,
        kilos_merma: Decimal,
        kilos_pendientes: Decimal,
        barras_compradas: Decimal = CERO,
        compras_mozzarella: Decimal = CERO,
        barras_vendidas: Decimal = CERO,
        ventas_mozzarella: Decimal = CERO,
        gastos_mozzarella: Decimal = CERO,
    ) -> list[GananciaProducto]:
        """Desglose de la ganancia del período en cuatro filas: queso, borona,
        merma y el residuo (lo que quedó en inventario, o lo que salió de un
        inventario anterior si se movió más de lo comprado en el período).

        CLAVE: el costo se reparte entre los kilos DEL LOTE COMPRADO y sus cuatro
        destinos reales (vendido como queso, pasado a borona, merma, inventario).
        La fila de borona se cuesta por los kilos CONVERTIDOS (kilos_a_borona),
        NO por los vendidos: la borona vendida sale del inventario de borona, que
        también se alimenta de la borona que llega gratis con el lote y de
        conversiones de temporadas anteriores. Costear los kilos vendidos
        inventaba costos que nunca se pagaron.

        Las tres primeras filas cargan su costo al precio promedio de compra y el
        residuo se lleva la diferencia, así la suma de los cuatro costos es
        EXACTAMENTE total_compras y la suma de las ganancias es exactamente
        ganancia_estimada (invariante del resumen).

        LA MOZZARELLA AGREGA DOS FILAS PROPIAS AL FINAL, en barras, y NO se mete en
        el reparto de arriba. `total_compras` que llega aquí es SOLO la plata de las
        compras en kilos (ver `CompraQuesoRepository.totales_periodo`), así que el
        costo de las barras no se le puede repartir a ningún kilo ni al contrario:
        cada unidad reparte su propia plata entre sus propios destinos. Las dos
        filas nuevas solo aparecen si hubo mozzarella en el período; si no hubo, el
        desglose sale con las mismas cuatro filas de siempre, idénticas.

        Y el invariante sigue en pie, ahora por partida doble:
            suma de costos de las filas de kilos  = plata de las compras en kilos
            suma de costos de las filas de barras = plata de las compras de barras
            suma de TODAS las ganancias           = ganancia_estimada
        """
        costo_queso = cls._costo_de(kilos_queso, kilos_comprados, total_compras)
        costo_borona = cls._costo_de(kilos_a_borona, kilos_comprados, total_compras)
        costo_merma = cls._costo_de(kilos_merma, kilos_comprados, total_compras)
        costo_residuo = total_compras - (costo_queso + costo_borona + costo_merma)
        # Residuo positivo: queso comprado que todavía no se ha movido. Negativo:
        # se movió queso de una temporada anterior, así que su costo es un
        # crédito (ya se pagó antes) y por eso sale negativo.
        producto_residuo = "pendiente" if kilos_pendientes >= CERO else "anterior"

        filas = [
            cls._fila_producto(
                "queso", kilos_queso, ventas_queso, costo_queso, gastos_queso, costo_kilo
            ),
            cls._fila_producto(
                "borona",
                kilos_a_borona,
                ventas_borona,
                costo_borona,
                gastos_borona,
                costo_kilo,
                kilos_vendidos=kilos_borona_vendidos,
            ),
            cls._fila_producto("merma", kilos_merma, CERO, costo_merma, CERO, costo_kilo),
            cls._fila_producto(
                producto_residuo, abs(kilos_pendientes), CERO, costo_residuo, CERO, costo_kilo
            ),
        ]

        # ------------------------------------------------------ mozzarella
        # Solo si hubo movimiento de barras en el período. Sin esta guarda, todos
        # los períodos del cliente —que hoy son de puro queso— estrenarían dos
        # filas en ceros que solo estorban en una pantalla que ya está apretada.
        if barras_compradas or barras_vendidas:
            costo_barra = (
                (compras_mozzarella / barras_compradas).quantize(DOS_DECIMALES)
                if barras_compradas
                else CERO
            )
            costo_vendidas = cls._costo_de(
                barras_vendidas, barras_compradas, compras_mozzarella
            )
            # El residuo de las barras se lleva la diferencia, igual que el de los
            # kilos: así los dos costos suman EXACTO la plata de las compras de
            # mozzarella y el dueño lo puede verificar con la calculadora.
            costo_residuo_barras = compras_mozzarella - costo_vendidas
            barras_pendientes = barras_compradas - barras_vendidas
            producto_residuo_barras = (
                "mozzarella_pendiente" if barras_pendientes >= CERO else "mozzarella_anterior"
            )
            filas.append(
                cls._fila_barras(
                    "mozzarella",
                    barras_vendidas,
                    ventas_mozzarella,
                    costo_vendidas,
                    gastos_mozzarella,
                    costo_barra,
                )
            )
            filas.append(
                cls._fila_barras(
                    producto_residuo_barras,
                    abs(barras_pendientes),
                    CERO,
                    costo_residuo_barras,
                    CERO,
                    costo_barra,
                    # El residuo no se vendió: sin barras vendidas no hay precio de
                    # venta que mostrar (si no, saldría $0 "por barra" como si se
                    # hubiera regalado).
                    barras_vendidas=CERO,
                )
            )
        return filas

    def _filas_por_productor(
        self,
        desde: date,
        hasta: date,
        *,
        valor_realizado_kilo: Decimal,
        neto_periodo: Decimal,
        kilos_comprados: Decimal,
        valor_realizado_barra: Decimal = CERO,
        neto_barras: Decimal = CERO,
        barras_compradas: Decimal = CERO,
    ) -> list[GananciaProductor]:
        """Ganancia ESTIMADA por productor (la UI debe decir que es estimación).

        Reparte el valor neto que dejaron las ventas del período (`neto_periodo`
        = ventas − gastos) entre los kilos que se le compraron a cada uno. Quien
        vendió más barato dejó más margen. Como el divisor son los kilos
        COMPRADOS, la suma de las filas cuadra con la ganancia neta del período:
        no hay forma de que el ranking contradiga la tarjeta de arriba.

        El reparto NO quantiza el valor por kilo antes de multiplicarlo (se hace
        kilos × neto / kilos_comprados y se redondea al final) y la diferencia de
        centavos se le da a la ÚLTIMA fila, igual que `costo_residuo` en
        _filas_por_producto: repartir con el `valor_realizado_kilo` ya redondeado
        a dos decimales desviaba la columna unos pesos de la ganancia del período.

        Las filas salen del conjunto HISTÓRICO de productores a los que se les
        debe, no solo de los que tuvieron compras EN EL PERÍODO: a quien se le
        compró en mayo y no se le ha pagado se le sigue debiendo en julio, y sin
        su fila la columna `por_pagar` no sumaba la tarjeta "Por pagar a
        productores", que sí es histórica. Esas filas van con kilos 0 y comprado
        0 —no tuvieron compras en el período, así que no inventan plata en el
        desglose— pero con TODA su deuda: la de las compras del sistema más la
        del libro anterior.

        La columna `por_pagar` INCLUYE el saldo del libro anterior de cada
        productor, porque la tarjeta "Por pagar a productores" también lo
        incluye: si solo lo sumara la tarjeta, la columna dejaría de cuadrar con
        ella. Los kilos, el costo y la ganancia NO se tocan: los saldos
        anteriores no son compras de este sistema.

        SIN COMPRAS EN EL PERÍODO no hay a quién repartirle y las filas se quedan
        con ganancia 0, que es lo correcto: a esa gente no se le compró nada este
        período. La ganancia salió de queso comprado ANTES (eso lo dice la fila
        "Salió de inventario anterior" del desglose por producto), así que
        repartirla entre los deudores históricos sería inventarles un negocio que
        no hicieron. La consecuencia hay que asumirla de frente: en ese caso la
        columna "Ganancia estimada" NO suma la tarjeta del período, y la pantalla
        tiene que decirlo en vez de prometer un cuadre que no existe.

        Cómo lo detecta el frontend: `kilos_comprados == 0` en el mismo resumen.
        NO se agrega un campo nuevo a propósito: sería un segundo nombre para un
        hecho que ya viaja en la respuesta y que además es EL MISMO divisor del
        reparto (`neto_periodo / kilos_comprados`), así que no puede desincronizarse
        del cálculo. Un `bool` aparte sí podría quedar en desacuerdo con los kilos
        el día que alguien toque una de las dos ramas.

        EL REPARTO SE HACE DOS VECES, UNA POR UNIDAD, y esa es la única forma de
        que cuadre: el neto que dejaron las ventas EN KILOS se reparte entre los
        KILOS comprados y el de las ventas EN BARRAS entre las BARRAS compradas.
        Si se repartiera todo el neto entre los kilos, a un productor que solo
        vendió barras le saldría ganancia cero y su plata se les acreditaría a los
        de kilos: el ranking diría que el mejor negocio lo hizo alguien que no
        vendió una sola barra. Las dos partes se SUMAN en `ganancia_estimada`
        porque son pesos, y la columna sigue sumando la ganancia del período:
            (neto_kilos − comprado_kilos) + (neto_barras − comprado_barras)
            = total_ventas − total_gastos − total_compras = ganancia
        """
        # Deuda HISTÓRICA por productor, agrupada en Python con el mismo criterio
        # las dos: la de las compras de este sistema y la que quedó del libro
        # anterior. Se van sacando con pop() a medida que se emiten las filas del
        # período, así ningún saldo se reparte dos veces y lo que sobra al final
        # es exactamente lo que falta por mostrar.
        pendiente_sistema = _agrupar_pendientes(self.compras.pendiente_por_productor())
        pendiente_libro = _agrupar_pendientes(
            self.saldos.pendiente_por_tercero(TIPO_SALDO_PAGAR)
        )

        del_periodo = self.compras.por_productor(desde, hasta)
        # Valor neto realizado que le corresponde a los kilos de cada productor.
        # A la última fila se le suma la diferencia de redondeo para que el
        # reparto sume EXACTAMENTE neto_periodo (los kilos de las filas suman
        # kilos_comprados, así que sin compras en el período la lista va vacía).
        realizados = [
            (kilos * neto_periodo / kilos_comprados).quantize(DOS_DECIMALES)
            if kilos_comprados
            else CERO
            for _, _, kilos, _, _, _, _ in del_periodo
        ]
        # El mismo reparto, EN BARRAS y con su propio neto. Dos listas separadas y
        # no una sola cifra por fila: si se sumaran antes de ajustar el residuo, la
        # diferencia de redondeo de una unidad se le cargaría a la otra.
        realizados_barras = [
            (barras * neto_barras / barras_compradas).quantize(DOS_DECIMALES)
            if barras_compradas
            else CERO
            for _, _, _, _, _, barras, _ in del_periodo
        ]
        # El ajuste del residuo solo tiene sentido si de verdad hubo kilos que
        # repartir: sin `kilos_comprados` el reparto es todo ceros y sumarle la
        # diferencia le daría TODO el neto del período a la última fila, que es
        # justo la plata que no le corresponde a nadie de la lista.
        #
        # Y el residuo se le da a la última fila QUE TENGA DE ESA UNIDAD. Antes de
        # la mozzarella daba igual (todas las filas tenían kilos), pero ahora un
        # productor de solo barras tiene kilos 0: darle a él los centavos del
        # reparto de los kilos le inventaría una ganancia en una unidad que no
        # vendió, y su fila diría "0 kg" con plata al lado.
        def _ajustar(valores: list[Decimal], cantidades: list[Decimal], neto: Decimal) -> None:
            ultimo = next(
                (i for i in range(len(cantidades) - 1, -1, -1) if cantidades[i] > CERO), None
            )
            if ultimo is not None:
                valores[ultimo] += neto - sum(valores, CERO)

        if kilos_comprados:
            _ajustar(realizados, [fila[2] for fila in del_periodo], neto_periodo)
        if barras_compradas:
            _ajustar(realizados_barras, [fila[5] for fila in del_periodo], neto_barras)

        filas: list[GananciaProductor] = []
        # El 5.º campo de por_productor (su saldo por grupo de SQL) NO se usa
        # aquí: la deuda sale de `pendiente_sistema`, que agrupa las variantes de
        # escritura en Python y así ninguna queda por fuera ni contada dos veces.
        for (
            (productor, compras, kilos, total_comprado, _, barras, comprado_barras),
            realizado,
            realizado_barras,
        ) in zip(del_periodo, realizados, realizados_barras):
            # El precio promedio POR KILO se saca de la plata de los kilos: al
            # total de sus compras se le quita el pedazo de las barras. Si no, a un
            # productor que vendió 10 kg y 100 barras le saldría un precio por kilo
            # que incluye toda la plata de la mozzarella.
            comprado_kilos = total_comprado - comprado_barras
            precio_promedio = (
                (comprado_kilos / kilos).quantize(DOS_DECIMALES) if kilos else CERO
            )
            precio_promedio_barra = (
                (comprado_barras / barras).quantize(DOS_DECIMALES) if barras else CERO
            )
            clave = _clave_tercero(productor)
            _, del_sistema = pendiente_sistema.pop(clave, ("", CERO))
            _, del_libro = pendiente_libro.pop(clave, ("", CERO))
            filas.append(
                GananciaProductor(
                    productor=productor,
                    compras=compras,
                    kilos=kilos,
                    barras=barras,
                    total_comprado=total_comprado,
                    total_comprado_barras=comprado_barras,
                    precio_promedio=precio_promedio,
                    precio_promedio_barra=precio_promedio_barra,
                    por_pagar=del_sistema + del_libro,
                    margen_por_kilo=(
                        valor_realizado_kilo - precio_promedio if kilos else CERO
                    ),
                    margen_por_barra=(
                        valor_realizado_barra - precio_promedio_barra if barras else CERO
                    ),
                    # Las dos ganancias se SUMAN porque son pesos. Cada una es lo
                    # que su unidad realizó menos lo que su unidad costó.
                    ganancia_estimada=(realizado - comprado_kilos)
                    + (realizado_barras - comprado_barras),
                )
            )
        # Productores a los que se les debe pero que NO tuvieron compras en el
        # período: de una compra vieja del sistema, del libro anterior, o de las
        # dos. Es el caso normal de quien acaba de migrar, y sin estas filas la
        # columna no sumaría lo que dice la tarjeta.
        sobrantes = list(pendiente_sistema) + [
            clave for clave in pendiente_libro if clave not in pendiente_sistema
        ]
        for clave in sobrantes:
            nombre_sistema, del_sistema = pendiente_sistema.get(clave, ("", CERO))
            nombre_libro, del_libro = pendiente_libro.get(clave, ("", CERO))
            filas.append(
                GananciaProductor(
                    # Manda la escritura de las compras: es la que ve el usuario
                    # en el resto del módulo.
                    productor=nombre_sistema or nombre_libro,
                    compras=0,
                    kilos=CERO,
                    total_comprado=CERO,
                    precio_promedio=CERO,
                    por_pagar=del_sistema + del_libro,
                    # Sin compras en el período no hay precio con qué comparar:
                    # poner el valor realizado sería inventarle un margen.
                    margen_por_kilo=CERO,
                    ganancia_estimada=CERO,
                )
            )
        filas.sort(key=lambda fila: fila.ganancia_estimada, reverse=True)
        return filas

    def resumen(self, desde: date, hasta: date) -> ResumenReventa:
        """El resumen del período, CON LAS DOS UNIDADES SEPARADAS.

        La regla que manda sobre todo lo demás: los kilos y las barras nunca se
        suman en una misma cifra ("20 kg + 8 barras" no es un número). La plata sí:
        los pesos son pesos, vengan de kilos o de barras.

        Cómo se sostiene eso aquí: cada consulta del repositorio ya viene filtrada
        por unidad (las de kilos excluyen la mozzarella y al contrario), así que
        NINGUNA de las variables de abajo puede traer las dos mezcladas. Las
        cantidades se llevan en variables con la unidad en el nombre (`kilos_*` y
        `barras_*`) y solo las de PLATA se suman entre unidades, con la suma escrita
        de frente para que se vea que es a propósito.
        """
        # --------------------------------------------------- lo que se mide en kilos
        kilos_comprados, compras_kilos = self.compras.totales_periodo(desde, hasta)
        kilos_queso, ventas_queso = self.ventas.totales_periodo(
            desde, hasta, tipo=TIPO_VENTA_QUESO
        )
        # El total sale de la consulta SIN filtro de tipo (que ya excluye la
        # mozzarella: son las ventas en kilos) y la borona por diferencia: si algún
        # día hay otro tipo de venta en kilos, o un dato viejo con el tipo en
        # blanco, su plata NO desaparece del resumen.
        kilos_todos, ventas_kilos = self.ventas.totales_periodo(desde, hasta)
        kilos_borona = kilos_todos - kilos_queso
        ventas_borona = ventas_kilos - ventas_queso
        gastos_kilos = self.ventas.gastos_periodo(desde, hasta)
        # Los gastos de la borona se sacan por diferencia (solo hay dos tipos de
        # venta en kilos), así queso + borona siempre suma EXACTO el total de
        # gastos de los kilos.
        gastos_queso = self.ventas.gastos_periodo(desde, hasta, tipo=TIPO_VENTA_QUESO)
        gastos_borona = gastos_kilos - gastos_queso
        # Ajustes del período: lo que se pasó a borona y LA MERMA REAL.
        kilos_a_borona, kilos_merma = self.conversiones.totales_periodo(desde, hasta)

        # ------------------------------------------------- lo que se cuenta en barras
        # Su propio renglón de punta a punta: barras compradas, barras vendidas y la
        # plata de cada lado. Ninguna de estas cifras entra en las de arriba.
        barras_compradas, compras_mozzarella = self.compras.totales_periodo_barras(
            desde, hasta
        )
        barras_vendidas, ventas_mozzarella = self.ventas.totales_periodo_barras(desde, hasta)
        gastos_mozzarella = self.ventas.gastos_periodo_barras(desde, hasta)

        # ---------------------------------------------- la plata, que SÍ se suma
        total_compras = compras_kilos + compras_mozzarella
        total_ventas = ventas_kilos + ventas_mozzarella
        total_gastos = gastos_kilos + gastos_mozzarella

        kilos_hist_comprados, borona_de_compras, por_pagar = self.compras.acumulados()
        hist_queso_vendido, hist_borona_vendida, por_cobrar = self.ventas.acumulados()
        # Cartera heredada del sistema anterior. Solo entra en lo que se debe
        # cobrar y pagar: NO tiene kilos, no se compró ni se vendió aquí, así que
        # no toca el inventario, ni los totales del período, ni la ganancia.
        por_cobrar_libro = self.saldos.pendiente(TIPO_SALDO_COBRAR)
        por_pagar_libro = self.saldos.pendiente(TIPO_SALDO_PAGAR)
        # `convertido` = todo lo que salió del queso disponible (borona + merma);
        # `a_borona` = solo lo que se pasó a borona (suma al inventario de borona).
        convertido = self.conversiones.total_convertido()
        a_borona = self.conversiones.total_a_borona()

        # El promedio POR KILO divide la plata DE LOS KILOS entre los kilos. Ojo con
        # no ponerle `total_compras` (que ya incluye las barras): saldría un "precio
        # por kilo" inflado con pesos que no salieron de ningún kilo, y el dueño lo
        # cruza a mano con lo que le pagó al productor.
        precio_prom_compra = (
            (compras_kilos / kilos_comprados).quantize(DOS_DECIMALES) if kilos_comprados else CERO
        )
        precio_prom_venta = (
            (ventas_queso / kilos_queso).quantize(DOS_DECIMALES) if kilos_queso else CERO
        )
        # Los mismos dos promedios en la otra unidad: plata de las barras entre
        # barras. Nunca se cruzan con los de arriba.
        precio_prom_compra_barra = (
            (compras_mozzarella / barras_compradas).quantize(DOS_DECIMALES)
            if barras_compradas
            else CERO
        )
        precio_prom_venta_barra = (
            (ventas_mozzarella / barras_vendidas).quantize(DOS_DECIMALES)
            if barras_vendidas
            else CERO
        )
        # Kilos que de verdad se vendieron: queso + borona. Es la base de los
        # promedios por kilo vendido (antes solo contaba el queso, y daba 0
        # cuando el período solo tuvo ventas de borona).
        kilos_vendidos_total = kilos_queso + kilos_borona
        # Residuo CON SIGNO del LOTE COMPRADO: lo que no se vendió como queso, no
        # se pasó a borona y no se perdió. Se resta la borona CONVERTIDA (que sí
        # salió del queso comprado), NO la borona vendida: esa sale del inventario
        # de borona, que además recibe la borona que llega gratis con el lote. Así
        # este residuo coincide con `kilos_disponibles` cuando el período abarca
        # todo el histórico, y las dos cifras no se contradicen en pantalla.
        kilos_pendientes = kilos_comprados - kilos_queso - kilos_a_borona - kilos_merma
        # El residuo de las barras, CON SIGNO y en su propia unidad: compradas −
        # vendidas. Negativo significa que se vendieron barras compradas antes del
        # período, igual que en los kilos. No lleva conversiones ni merma: la
        # mozzarella no participa en esos ajustes (ver `barras_disponibles`).
        barras_pendientes = barras_compradas - barras_vendidas
        # Ganancia neta EXACTA del período = lo que se vendió − lo que se compró
        # − los gastos de venta. Al restar TODA la compra (no solo el costo de lo
        # vendido) queda contado lo que no se alcanzó a vender y la merma real.
        # Suma las dos unidades porque son PESOS: es la cifra que el dueño espera
        # ver como "lo que dejó el negocio", no "lo que dejó el queso".
        ganancia = (total_ventas - total_compras - total_gastos).quantize(DOS_DECIMALES)
        # El margen POR KILO solo mira la plata de los kilos, y esto es lo delicado:
        # dividir la ganancia TOTAL entre los kilos vendidos daría un "peso por
        # kilo" que lleva adentro lo que dejaron las barras. Con 20 kg y 500 barras
        # esa cifra no diría nada del queso.
        ganancia_kilos = (ventas_kilos - compras_kilos - gastos_kilos).quantize(DOS_DECIMALES)
        margen = (
            (ganancia_kilos / kilos_vendidos_total).quantize(DOS_DECIMALES)
            if kilos_vendidos_total
            else CERO
        )
        # Lo mismo por BARRA VENDIDA, con la plata de las barras y nada más.
        ganancia_mozzarella = (
            ventas_mozzarella - compras_mozzarella - gastos_mozzarella
        ).quantize(DOS_DECIMALES)
        margen_barra = (
            (ganancia_mozzarella / barras_vendidas).quantize(DOS_DECIMALES)
            if barras_vendidas
            else CERO
        )
        # Lo neto que dejó cada kilo COMPRADO en el período. El divisor son los
        # kilos comprados (no los vendidos) a propósito: así repartirlo entre los
        # productores suma exactamente la ganancia neta del período.
        # El numerador es el neto DE LOS KILOS: si trajera la plata de las barras,
        # repartirlo entre los kilos les acreditaría a los productores de queso una
        # ganancia que salió de la mozzarella.
        valor_realizado_kilo = (
            ((ventas_kilos - gastos_kilos) / kilos_comprados).quantize(DOS_DECIMALES)
            if kilos_comprados
            else CERO
        )
        valor_realizado_barra = (
            ((ventas_mozzarella - gastos_mozzarella) / barras_compradas).quantize(
                DOS_DECIMALES
            )
            if barras_compradas
            else CERO
        )

        return ResumenReventa(
            desde=desde,
            hasta=hasta,
            kilos_comprados=kilos_comprados,
            total_compras=total_compras,
            kilos_vendidos=kilos_queso,
            total_ventas=total_ventas,
            precio_promedio_compra=precio_prom_compra,
            precio_promedio_venta=precio_prom_venta,
            total_gastos=total_gastos,
            ganancia_estimada=ganancia,
            margen_por_kilo=margen,
            valor_realizado_kilo=valor_realizado_kilo,
            kilos_borona_vendidos=kilos_borona,
            total_ventas_borona=ventas_borona,
            # ------------------------------------------------------ mozzarella
            barras_compradas=barras_compradas,
            total_compras_mozzarella=compras_mozzarella,
            barras_vendidas=barras_vendidas,
            total_ventas_mozzarella=ventas_mozzarella,
            total_gastos_mozzarella=gastos_mozzarella,
            precio_promedio_compra_barra=precio_prom_compra_barra,
            precio_promedio_venta_barra=precio_prom_venta_barra,
            margen_por_barra=margen_barra,
            valor_realizado_barra=valor_realizado_barra,
            barras_pendientes=barras_pendientes,
            kilos_a_borona=kilos_a_borona,
            kilos_merma=kilos_merma,
            kilos_pendientes=kilos_pendientes,
            por_producto=self._filas_por_producto(
                kilos_comprados=kilos_comprados,
                # LA PLATA DE LOS KILOS, no `total_compras`: el desglose reparte
                # este costo entre los DESTINOS DE LOS KILOS (vendido, borona,
                # merma, inventario), y meterle lo que costaron unas barras le
                # cargaría a esos destinos una plata que no es suya.
                total_compras=compras_kilos,
                costo_kilo=precio_prom_compra,
                kilos_queso=kilos_queso,
                ventas_queso=ventas_queso,
                gastos_queso=gastos_queso,
                kilos_a_borona=kilos_a_borona,
                kilos_borona_vendidos=kilos_borona,
                ventas_borona=ventas_borona,
                gastos_borona=gastos_borona,
                kilos_merma=kilos_merma,
                kilos_pendientes=kilos_pendientes,
                # La mozzarella con su plata y sus barras, para sus dos renglones.
                barras_compradas=barras_compradas,
                compras_mozzarella=compras_mozzarella,
                barras_vendidas=barras_vendidas,
                ventas_mozzarella=ventas_mozzarella,
                gastos_mozzarella=gastos_mozzarella,
            ),
            por_productor=self._filas_por_productor(
                desde,
                hasta,
                valor_realizado_kilo=valor_realizado_kilo,
                # El reparto se hace con el neto SIN redondear por kilo, así la
                # columna suma exacto la ganancia del período. Y con el neto DE LOS
                # KILOS: el de las barras va por su propio lado, abajo.
                neto_periodo=ventas_kilos - gastos_kilos,
                kilos_comprados=kilos_comprados,
                valor_realizado_barra=valor_realizado_barra,
                neto_barras=ventas_mozzarella - gastos_mozzarella,
                barras_compradas=barras_compradas,
            ),
            kilos_disponibles=kilos_hist_comprados - hist_queso_vendido - convertido,
            borona_disponible=borona_de_compras + a_borona - hist_borona_vendida,
            # El tercer inventario, en su propia unidad y jamás sumado con los dos
            # de arriba. Sale del mismo cálculo que usan los guardias de
            # existencias, para que la pantalla y el guardia no se contradigan.
            barras_disponibles=self.compras.barras_acumuladas()
            - self.ventas.barras_acumuladas(),
            # Las dos tarjetas de cartera suman el sistema MÁS el libro anterior
            # (es la plata que de verdad se debe hoy), y enseguida va ese pedazo
            # por separado para poder mostrar el desglose.
            por_pagar_productores=por_pagar + por_pagar_libro,
            por_cobrar_clientes=por_cobrar + por_cobrar_libro,
            por_cobrar_libro_anterior=por_cobrar_libro,
            por_pagar_libro_anterior=por_pagar_libro,
        )

    def sugerencias(self) -> SugerenciasReventa:
        """Nombres ya usados de productores y clientes, para autocompletar.

        Incluye los terceros del LIBRO ANTERIOR, cada uno de SU lado: los de tipo
        'cobrar' son clientes y los de tipo 'pagar' productores (los dos lados no
        se mezclan). Un cliente que por ahora solo existe en el libro es justo el
        caso que motivó esa pantalla: sin él, el autocompletado no lo ofrecía, se
        volvía a teclear el nombre a mano y la deuda vieja quedaba separada de la
        nueva. Las dos listas van sin repetir el mismo tercero escrito de otra
        forma (ver _unir_nombres).
        """
        return SugerenciasReventa(
            productores=_unir_nombres(
                self.compras.nombres_productores(),
                self.saldos.nombres_terceros(TIPO_SALDO_PAGAR),
            ),
            clientes=_unir_nombres(
                self.ventas.nombres_clientes(),
                self.saldos.nombres_terceros(TIPO_SALDO_COBRAR),
            ),
        )

    # -------------------------------------------------------- estado de cuenta
    def estado_cuenta(
        self, cliente: str, desde: date | None = None, hasta: date | None = None
    ) -> EstadoCuentaCliente:
        """Cómo va la facturación de un cliente: sus compras, sus pagos y el saldo.

        CONFIDENCIALIDAD: esto se le entrega AL CLIENTE. De cada venta solo salen
        fecha, producto, kilos, precio por kilo, total, abonado y saldo, y de cada
        abono solo fecha y valor. Los gastos de venta (transporte), la "venta
        libre", los costos de compra, los márgenes y las observaciones (tanto de
        la venta como del abono) se quedan adentro: son los números de la quesera.

        Sin rango de fechas cubre todo el histórico, que es el saldo real que debe.

        El `saldo` es TODO lo que el cliente debe: lo del sistema MÁS lo que
        traía del libro anterior, porque esa es la única cifra que le importa a
        él. `total_facturado` y `total_abonado` siguen siendo solo del sistema y
        lo del libro va aparte, así que el documento cuadra:
            (total_facturado - total_abonado) + libro_anterior_saldo = saldo
        """
        ventas = self.ventas.por_cliente(cliente, desde, hasta)
        # Los saldos del libro anterior se filtran por el MISMO rango que las
        # ventas, con la fecha original del documento viejo.
        saldos = self.saldos.por_tercero(TIPO_SALDO_COBRAR, cliente, desde, hasta)
        # Un cliente que solo arrastra deuda vieja SÍ tiene estado de cuenta: es
        # justo el caso de quien viene del sistema anterior y todavía no le ha
        # comprado nada aquí.
        if not ventas and not saldos:
            # Si tiene movimientos pero fuera del rango pedido, decirlo tal cual:
            # no es lo mismo que no ser cliente.
            if (desde or hasta) and (
                self.ventas.por_cliente(cliente)
                or self.saldos.por_tercero(TIPO_SALDO_COBRAR, cliente)
            ):
                raise NotFoundError(
                    "El cliente no tiene ventas ni saldos de la cuenta anterior "
                    "vigentes en el período consultado (lo anulado no cuenta)"
                )
            # Se aclara lo de las anuladas porque el usuario puede estar viendo en
            # pantalla una venta anulada de ese cliente y no entender el error.
            raise NotFoundError(
                "El cliente no tiene ventas registradas ni saldos de la cuenta "
                "anterior (lo anulado no cuenta para el estado de cuenta)"
            )

        filas: list[EstadoCuentaVenta] = []
        pagos: list[EstadoCuentaPago] = []
        total_kilos = CERO
        # El total de barras se acumula APARTE del de kilos. Sumarlos daría una
        # cifra que el cliente no podría reconocer en ninguna entrega.
        total_barras = CERO
        total_facturado = CERO
        total_abonado = CERO
        for venta in ventas:
            kilos = Decimal(venta.kilos)
            barras = Decimal(venta.barras or CERO)
            valor = Decimal(venta.valor_total)
            abonado = Decimal(venta.abonado)
            total_kilos += kilos
            total_barras += barras
            total_facturado += valor
            total_abonado += abonado
            tipo = venta.tipo or TIPO_VENTA_QUESO
            filas.append(
                EstadoCuentaVenta(
                    fecha=venta.fecha,
                    tipo=tipo,
                    producto=NOMBRE_PRODUCTO.get(tipo, tipo.capitalize()),
                    unidad=unidad_de(venta),
                    kilos=kilos,
                    precio_kilo=Decimal(venta.precio_kilo),
                    barras=barras,
                    precio_barra=Decimal(venta.precio_barra or CERO),
                    valor_total=valor,
                    abonado=abonado,
                    saldo=valor - abonado,
                    estado=venta.estado,
                )
            )
            # Los pagos son TODOS los abonos de TODAS sus ventas, juntos: el
            # cliente paga "a la cuenta", no le interesa a qué venta se aplicó.
            # Del abono solo salen fecha y valor: sus `observaciones` son la nota
            # interna de la quesera y NO se copian aquí (ver EstadoCuentaPago).
            pagos += [
                EstadoCuentaPago(fecha=abono.fecha, valor=Decimal(abono.valor))
                for abono in venta.abonos
            ]
        pagos.sort(key=lambda pago: pago.fecha)

        # Lo que traía debiendo del sistema anterior. Del saldo solo salen fecha,
        # concepto, valor, abonado y saldo: sus `observaciones` son la nota
        # interna de la quesera y no se copian (igual que en los abonos).
        filas_libro: list[EstadoCuentaSaldoAnterior] = []
        libro_total = CERO
        libro_abonado = CERO
        for saldo_anterior in saldos:
            valor = Decimal(saldo_anterior.valor_total)
            abonado = Decimal(saldo_anterior.abonado)
            libro_total += valor
            libro_abonado += abonado
            filas_libro.append(
                EstadoCuentaSaldoAnterior(
                    fecha=saldo_anterior.fecha,
                    concepto=saldo_anterior.concepto,
                    valor_total=valor,
                    abonado=abonado,
                    saldo=valor - abonado,
                )
            )
        libro_saldo = libro_total - libro_abonado

        return EstadoCuentaCliente(
            # El nombre que se muestra es el GUARDADO (el de su primera venta, o
            # el del saldo viejo si solo tiene deuda del libro), no el que llegó
            # por query: así sale bien escrito aunque se haya consultado en
            # minúsculas.
            cliente=ventas[0].cliente if ventas else saldos[0].tercero,
            desde=desde,
            hasta=hasta,
            emitido=date.today(),
            compras=len(filas),
            total_kilos=total_kilos,
            total_barras=total_barras,
            total_facturado=total_facturado,
            total_abonado=total_abonado,
            # TODO lo que debe: lo del sistema más lo del libro anterior.
            saldo=(total_facturado - total_abonado) + libro_saldo,
            ventas=filas,
            pagos=pagos,
            saldos_anteriores=filas_libro,
            libro_anterior_total=libro_total,
            libro_anterior_abonado=libro_abonado,
            libro_anterior_saldo=libro_saldo,
        )

    def _auditar_exportacion(self, datos: EstadoCuentaCliente) -> None:
        """Deja registrada la SALIDA de datos: exportar la cartera histórica de un
        cliente es entregar información afuera y tiene que quedar en la bitácora
        (quién la sacó, de qué cliente y de qué rango).

        Se escribe la fila a mano porque ReventaResumenService no extiende
        BaseService (no tiene un repositorio único: cruza compras, ventas y
        conversiones), así que no hereda el helper _audit.
        """
        from app.modules.auditoria.models import Auditoria

        self.db.add(
            Auditoria(
                empresa_id=self.ctx.empresa_id,
                usuario_id=self.ctx.user_id,
                ip=self.ctx.ip,
                modulo="reventa",
                accion="exportar",
                entidad="EstadoCuentaCliente",
                entidad_id=None,
                antes=None,
                despues={
                    "documento": "estado_cuenta_pdf",
                    "cliente": datos.cliente,
                    "desde": datos.desde.isoformat() if datos.desde else None,
                    "hasta": datos.hasta.isoformat() if datos.hasta else None,
                    "compras": datos.compras,
                    "saldo": float(datos.saldo),
                },
            )
        )

    def estado_cuenta_pdf(
        self, cliente: str, desde: date | None = None, hasta: date | None = None
    ) -> tuple[bytes, str]:
        """Estado de cuenta en PDF, listo para mandárselo al cliente."""
        datos = self.estado_cuenta(cliente, desde, hasta)
        self._auditar_exportacion(datos)
        empresa = EmpresaRepository(self.db).get(self.ctx.empresa_id)
        nombre_empresa = empresa.nombre if empresa else "Quesera"
        nit = empresa.nit if empresa else None
        ubicacion = (
            (", ".join(p for p in [empresa.ciudad, empresa.departamento] if p) or None)
            if empresa
            else None
        )
        if datos.desde and datos.hasta:
            periodo = (
                f"{datos.desde.strftime('%d/%m/%Y')} al {datos.hasta.strftime('%d/%m/%Y')}"
            )
        elif datos.desde:
            periodo = f"Desde el {datos.desde.strftime('%d/%m/%Y')}"
        elif datos.hasta:
            periodo = f"Hasta el {datos.hasta.strftime('%d/%m/%Y')}"
        else:
            periodo = "Todo el histórico"

        pdf = build_estado_cuenta_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            cliente=datos.cliente,
            emitido=datos.emitido.strftime("%d/%m/%Y"),
            periodo=periodo,
            compras=datos.compras,
            # Se arman los campos uno por uno a propósito: lo que no se nombre
            # aquí NO puede llegar al PDF que ve el cliente.
            ventas=[
                {
                    "fecha": v.fecha,
                    "producto": v.producto,
                    # La unidad viaja para que el PDF rotule la cantidad como lo
                    # que es. Sin ella, una venta de 8 barras se imprimiría "0 kg".
                    "unidad": v.unidad,
                    "kilos": v.kilos,
                    "precio_kilo": v.precio_kilo,
                    "barras": v.barras,
                    "precio_barra": v.precio_barra,
                    "valor_total": v.valor_total,
                    "abonado": v.abonado,
                    "saldo": v.saldo,
                }
                for v in datos.ventas
            ],
            pagos=[{"fecha": p.fecha, "valor": p.valor} for p in datos.pagos],
            # Los saldos del libro anterior, campo por campo: la observación del
            # saldo es interna y no puede llegar al documento del cliente.
            saldos_anteriores=[
                {
                    "fecha": s.fecha,
                    "concepto": s.concepto,
                    "valor_total": s.valor_total,
                    "abonado": s.abonado,
                    "saldo": s.saldo,
                }
                for s in datos.saldos_anteriores
            ],
            total_kilos=datos.total_kilos,
            total_barras=datos.total_barras,
            total_facturado=datos.total_facturado,
            total_abonado=datos.total_abonado,
            libro_anterior_total=datos.libro_anterior_total,
            libro_anterior_abonado=datos.libro_anterior_abonado,
            libro_anterior_saldo=datos.libro_anterior_saldo,
            saldo=datos.saldo,
        )
        return pdf, _nombre_archivo_cliente(datos.cliente)

    # ------------------------------------- estado de cuenta DEL PRODUCTOR
    def estado_cuenta_productor(
        self, productor: str, desde: date | None = None, hasta: date | None = None
    ) -> EstadoCuentaProductor:
        """Cómo va la cuenta con un productor: lo que se le compró, lo que se le
        pagó y lo que se le debe. Es el espejo de `estado_cuenta`.

        CONFIDENCIALIDAD, AL CONTRARIO QUE EN EL DEL CLIENTE: esto se le entrega
        AL PRODUCTOR. De cada compra solo salen fecha, kilos netos, la borona que
        vino con el lote, precio por kilo, total, abonado y saldo, y de cada abono
        solo fecha y valor. NADA del lado de la venta (a cuánto se revendió su
        queso, el total de ventas, el precio promedio de venta, los márgenes, la
        ganancia, los gastos de venta ni los nombres de los clientes) sale por
        aquí, ni los saldos del libro de tipo 'cobrar', que son deudas de clientes.

        Sin rango de fechas cubre todo el histórico, que es lo que de verdad se le
        debe (el caso normal).

        OJO CON EL SIGNO: `saldo` positivo significa que LA QUESERA LE DEBE A ÉL.
        Es TODO lo que se le debe hoy: lo del sistema MÁS lo que se le venía
        debiendo del libro anterior. `total_comprado` y `total_pagado` siguen
        siendo solo del sistema y lo del libro va aparte, así que el documento
        cuadra:
            (total_comprado - total_pagado) + libro_anterior_saldo = saldo
        """
        compras = self.compras.del_productor(productor, desde, hasta)
        # Los saldos del libro anterior se filtran por el MISMO rango que las
        # compras, con la fecha original del documento viejo. SOLO los de tipo
        # 'pagar': los de tipo 'cobrar' son deudas de CLIENTES con la quesera y no
        # tienen nada que ver con un productor, ni siquiera si un tercero se llama
        # igual.
        saldos = self.saldos.por_tercero(TIPO_SALDO_PAGAR, productor, desde, hasta)
        # Un productor al que solo se le arrastra deuda vieja SÍ tiene estado de
        # cuenta: es justo el caso de quien viene del sistema anterior y todavía no
        # le ha vendido nada aquí.
        if not compras and not saldos:
            # Si tiene movimientos pero fuera del rango pedido, decirlo tal cual:
            # no es lo mismo que no haberle comprado nunca.
            if (desde or hasta) and (
                self.compras.del_productor(productor)
                or self.saldos.por_tercero(TIPO_SALDO_PAGAR, productor)
            ):
                raise NotFoundError(
                    "El productor no tiene compras ni saldos de la cuenta anterior "
                    "vigentes en el período consultado (lo anulado no cuenta)"
                )
            # Se aclara lo de las anuladas porque el usuario puede estar viendo en
            # pantalla una compra anulada de ese productor y no entender el error.
            raise NotFoundError(
                "El productor no tiene compras registradas ni saldos de la cuenta "
                "anterior (lo anulado no cuenta para el estado de cuenta)"
            )

        filas: list[EstadoCuentaCompra] = []
        pagos: list[EstadoCuentaPagoProductor] = []
        total_kilos = CERO
        # Las barras que se le compraron, en su propio total (ver EstadoCuentaCompra).
        total_barras = CERO
        total_comprado = CERO
        total_pagado = CERO
        for compra in compras:
            # Los kilos que salen son los NETOS: son los que se le pagan. La
            # borona va en su propio campo, sin sumar al total ni al valor.
            kilos = Decimal(compra.kilos_netos)
            barras = Decimal(compra.barras or CERO)
            valor = Decimal(compra.valor_total)
            abonado = Decimal(compra.abonado)
            total_kilos += kilos
            total_barras += barras
            total_comprado += valor
            total_pagado += abonado
            tipo = compra.tipo or TIPO_VENTA_QUESO
            filas.append(
                EstadoCuentaCompra(
                    fecha=compra.fecha,
                    tipo=tipo,
                    unidad=unidad_de(compra),
                    kilos=kilos,
                    borona_kilos=Decimal(compra.borona_kilos or CERO),
                    precio_kilo=Decimal(compra.precio_kilo),
                    barras=barras,
                    precio_barra=Decimal(compra.precio_barra or CERO),
                    valor_total=valor,
                    abonado=abonado,
                    saldo=valor - abonado,
                    estado=compra.estado,
                )
            )
            # Los pagos son TODOS los abonos de TODAS sus compras, juntos: al
            # productor se le paga "a la cuenta", no le interesa a qué compra se
            # aplicó cada abono. Del abono solo salen fecha y valor: sus
            # `observaciones` son la nota interna de la quesera y NO se copian
            # aquí (ver EstadoCuentaPagoProductor).
            pagos += [
                EstadoCuentaPagoProductor(fecha=abono.fecha, valor=Decimal(abono.valor))
                for abono in compra.abonos
            ]
        pagos.sort(key=lambda pago: pago.fecha)

        # Lo que se le venía debiendo del sistema anterior. Del saldo solo salen
        # fecha, concepto, valor, abonado y saldo: sus `observaciones` son la nota
        # interna de la quesera y no se copian (igual que en los abonos).
        filas_libro: list[EstadoCuentaSaldoAnterior] = []
        libro_total = CERO
        libro_abonado = CERO
        for saldo_anterior in saldos:
            valor = Decimal(saldo_anterior.valor_total)
            abonado = Decimal(saldo_anterior.abonado)
            libro_total += valor
            libro_abonado += abonado
            filas_libro.append(
                EstadoCuentaSaldoAnterior(
                    fecha=saldo_anterior.fecha,
                    concepto=saldo_anterior.concepto,
                    valor_total=valor,
                    abonado=abonado,
                    saldo=valor - abonado,
                )
            )
        libro_saldo = libro_total - libro_abonado

        return EstadoCuentaProductor(
            # El nombre que se muestra es el GUARDADO (el de su primera compra, o
            # el del saldo viejo si solo tiene deuda del libro), no el que llegó
            # por query: así sale bien escrito aunque se haya consultado en
            # minúsculas.
            productor=compras[0].productor if compras else saldos[0].tercero,
            desde=desde,
            hasta=hasta,
            emitido=date.today(),
            compras=len(filas),
            total_kilos=total_kilos,
            total_barras=total_barras,
            total_comprado=total_comprado,
            total_pagado=total_pagado,
            saldos_anteriores=filas_libro,
            libro_anterior_total=libro_total,
            libro_anterior_abonado=libro_abonado,
            libro_anterior_saldo=libro_saldo,
            # TODO lo que se le debe: lo del sistema más lo del libro anterior.
            saldo=(total_comprado - total_pagado) + libro_saldo,
            compras_detalle=filas,
            pagos=pagos,
        )

    def _auditar_exportacion_productor(self, datos: EstadoCuentaProductor) -> None:
        """Deja registrada la SALIDA de datos, igual que en el del cliente:
        exportar la cuenta histórica de un productor es entregar información
        afuera y tiene que quedar en la bitácora (quién la sacó, de qué productor
        y de qué rango).
        """
        from app.modules.auditoria.models import Auditoria

        self.db.add(
            Auditoria(
                empresa_id=self.ctx.empresa_id,
                usuario_id=self.ctx.user_id,
                ip=self.ctx.ip,
                modulo="reventa",
                accion="exportar",
                entidad="EstadoCuentaProductor",
                entidad_id=None,
                antes=None,
                despues={
                    "documento": "estado_cuenta_productor_pdf",
                    "productor": datos.productor,
                    "desde": datos.desde.isoformat() if datos.desde else None,
                    "hasta": datos.hasta.isoformat() if datos.hasta else None,
                    "compras": datos.compras,
                    "saldo": float(datos.saldo),
                },
            )
        )

    def estado_cuenta_productor_pdf(
        self, productor: str, desde: date | None = None, hasta: date | None = None
    ) -> tuple[bytes, str]:
        """Estado de cuenta del productor en PDF, para entregárselo y cuadrar
        cuentas con él. Es un documento INTERNO: sin numeración consecutiva, sin
        resolución de la DIAN y sin IVA (no es una factura)."""
        datos = self.estado_cuenta_productor(productor, desde, hasta)
        self._auditar_exportacion_productor(datos)
        empresa = EmpresaRepository(self.db).get(self.ctx.empresa_id)
        nombre_empresa = empresa.nombre if empresa else "Quesera"
        nit = empresa.nit if empresa else None
        ubicacion = (
            (", ".join(p for p in [empresa.ciudad, empresa.departamento] if p) or None)
            if empresa
            else None
        )
        if datos.desde and datos.hasta:
            periodo = (
                f"{datos.desde.strftime('%d/%m/%Y')} al {datos.hasta.strftime('%d/%m/%Y')}"
            )
        elif datos.desde:
            periodo = f"Desde el {datos.desde.strftime('%d/%m/%Y')}"
        elif datos.hasta:
            periodo = f"Hasta el {datos.hasta.strftime('%d/%m/%Y')}"
        else:
            periodo = "Todo el histórico"

        pdf = build_estado_cuenta_productor_pdf(
            empresa_nombre=nombre_empresa,
            empresa_nit=nit,
            empresa_ubicacion=ubicacion,
            productor=datos.productor,
            emitido=datos.emitido.strftime("%d/%m/%Y"),
            periodo=periodo,
            compras=datos.compras,
            # Se arman los campos uno por uno a propósito: lo que no se nombre
            # aquí NO puede llegar al PDF que ve el productor.
            compras_detalle=[
                {
                    "fecha": c.fecha,
                    # Igual que en el del cliente: la unidad decide cómo se rotula
                    # la cantidad, para que una compra de barras no diga "0 kg".
                    "unidad": c.unidad,
                    "kilos": c.kilos,
                    "borona_kilos": c.borona_kilos,
                    "precio_kilo": c.precio_kilo,
                    "barras": c.barras,
                    "precio_barra": c.precio_barra,
                    "valor_total": c.valor_total,
                    "abonado": c.abonado,
                    "saldo": c.saldo,
                }
                for c in datos.compras_detalle
            ],
            pagos=[{"fecha": p.fecha, "valor": p.valor} for p in datos.pagos],
            # Los saldos del libro anterior, campo por campo: la observación del
            # saldo es interna y no puede llegar al documento del productor.
            saldos_anteriores=[
                {
                    "fecha": s.fecha,
                    "concepto": s.concepto,
                    "valor_total": s.valor_total,
                    "abonado": s.abonado,
                    "saldo": s.saldo,
                }
                for s in datos.saldos_anteriores
            ],
            total_kilos=datos.total_kilos,
            total_barras=datos.total_barras,
            total_comprado=datos.total_comprado,
            total_pagado=datos.total_pagado,
            libro_anterior_total=datos.libro_anterior_total,
            libro_anterior_abonado=datos.libro_anterior_abonado,
            libro_anterior_saldo=datos.libro_anterior_saldo,
            saldo=datos.saldo,
        )
        return pdf, _nombre_archivo_productor(datos.productor)


# --------------------------------------------------------------- temporadas
class TemporadaService(BaseService[Temporada]):
    """Temporadas: ciclos de compra y reventa con nombre y fechas.

    NO guarda ninguna cifra de plata. Las cifras de cada temporada salen de
    `ReventaResumenService.resumen(fecha_inicio, fecha_fin)`, o sea del mismo
    código que pinta el Resumen. Es la decisión central de este módulo y está
    explicada en el modelo `Temporada`: así funciona hacia atrás (se registra hoy
    una temporada de marzo y sus cifras aparecen solas) y nunca queda una cifra
    guardada distinta de la que muestra el Resumen para las mismas fechas.

    Las dos reglas que se validan, y por qué las dos son de plata y no de forma:

    - NO SE SOLAPAN. Si dos temporadas se cruzaran, los mismos kilos y la misma
      plata caerían en las dos y la suma de las ganancias por temporada no daría
      la ganancia del negocio.
    - SOLO UNA ABIERTA. La abierta es la que está corriendo y se calcula hasta
      hoy; con dos abiertas las dos llegarían hasta hoy y se cruzarían por
      definición.
    """

    repository_cls = TemporadaRepository
    modulo = "reventa"

    # ------------------------------------------------------------ validación
    def _validar_rango(
        self, inicio: date, fin: date | None, excluir_id: uuid.UUID | None = None
    ) -> None:
        if fin is not None and fin < inicio:
            raise BusinessError(
                "La temporada no puede terminar antes de empezar: empieza el "
                f"{inicio.strftime('%d/%m/%Y')} y terminaría el "
                f"{fin.strftime('%d/%m/%Y')}"
            )
        if fin is None:
            otra_abierta = self.repo.abierta(excluir_id=excluir_id)
            if otra_abierta is not None:
                raise BusinessError(
                    f"Ya hay una temporada abierta: {otra_abierta.nombre}. "
                    "Ciérrela antes de abrir otra."
                )
        cruzada = self.repo.solapada(inicio, fin, excluir_id=excluir_id)
        if cruzada is not None:
            hasta = (
                cruzada.fecha_fin.strftime("%d/%m/%Y") if cruzada.fecha_fin else "sin cerrar"
            )
            raise BusinessError(
                f"Se cruza con la temporada {cruzada.nombre} "
                f"({cruzada.fecha_inicio.strftime('%d/%m/%Y')} - {hasta}). "
                "Las temporadas no se pueden solapar, porque los mismos kilos y "
                "la misma plata quedarían contados en las dos."
            )

    def validar_crear(self, data: dict[str, Any]) -> None:
        self._validar_rango(data["fecha_inicio"], data.get("fecha_fin"))

    def validar_actualizar(self, obj: Temporada, data: dict[str, Any]) -> None:
        # Con exclude_unset, lo que no venga en el payload se queda como está: hay
        # que validar el rango RESULTANTE, no solo lo que llegó. Cambiar solo el
        # inicio también puede provocar un cruce.
        inicio = data.get("fecha_inicio", obj.fecha_inicio)
        fin = data["fecha_fin"] if "fecha_fin" in data else obj.fecha_fin
        self._validar_rango(inicio, fin, excluir_id=obj.id)

    # -------------------------------------------------------- abrir y cerrar
    def cerrar(self, entity_id: uuid.UUID, fecha_fin: date | None = None) -> Temporada:
        """Le pone fecha de cierre. Cerrar NO congela las cifras: lo que se cierra
        es el ciclo del queso, no el libro (ver el modelo)."""
        temporada = self.repo.get_or_fail(entity_id)
        if temporada.fecha_fin is not None:
            raise BusinessError(
                f"La temporada {temporada.nombre} ya está cerrada "
                f"({temporada.fecha_fin.strftime('%d/%m/%Y')})"
            )
        return self.actualizar(entity_id, {"fecha_fin": fecha_fin or date.today()})

    def reabrir(self, entity_id: uuid.UUID) -> Temporada:
        """Quita la fecha de cierre, para cuando se cerró por equivocación.

        Solo se puede si no hay otra abierta y si al quedar hasta hoy no se cruza
        con ninguna posterior: eso lo comprueba la validación normal.
        """
        temporada = self.repo.get_or_fail(entity_id)
        if temporada.fecha_fin is None:
            raise BusinessError(f"La temporada {temporada.nombre} ya está abierta")
        return self.actualizar(entity_id, {"fecha_fin": None})

    # ---------------------------------------------------------------- panel
    def _resumen_de(
        self,
        temporada: Temporada,
        hoy: date,
        resumenes: "ReventaResumenService",
        compras: CompraQuesoRepository,
        ventas: VentaQuesoRepository,
    ) -> TemporadaResumen:
        # La abierta se calcula hasta HOY. Si además empezara en el futuro (se
        # puede dejar programada), el rango quedaría al revés y las consultas
        # devolverían ceros mudos: se acota al propio inicio, que da un día con
        # todo en cero, que es la verdad.
        fin = temporada.fecha_fin or max(hoy, temporada.fecha_inicio)
        r = resumenes.resumen(temporada.fecha_inicio, fin)
        por_cobrar = ventas.pendiente_periodo(temporada.fecha_inicio, fin)
        por_pagar = compras.pendiente_periodo(temporada.fecha_inicio, fin)
        return TemporadaResumen(
            id=temporada.id,
            nombre=temporada.nombre,
            fecha_inicio=temporada.fecha_inicio,
            fecha_fin=fin,
            abierta=temporada.fecha_fin is None,
            dias=(fin - temporada.fecha_inicio).days + 1,
            notas=temporada.notas,
            kilos_comprados=r.kilos_comprados,
            kilos_vendidos=r.kilos_vendidos,
            kilos_borona_vendidos=r.kilos_borona_vendidos,
            kilos_a_borona=r.kilos_a_borona,
            kilos_merma=r.kilos_merma,
            kilos_pendientes=r.kilos_pendientes,
            # La mozzarella de la temporada, en barras y aparte de los kilos.
            barras_compradas=r.barras_compradas,
            barras_vendidas=r.barras_vendidas,
            barras_pendientes=r.barras_pendientes,
            total_compras=r.total_compras,
            total_ventas=r.total_ventas,
            total_gastos=r.total_gastos,
            ganancia=r.ganancia_estimada,
            margen_por_kilo=r.margen_por_kilo,
            precio_promedio_compra=r.precio_promedio_compra,
            precio_promedio_venta=r.precio_promedio_venta,
            precio_promedio_compra_barra=r.precio_promedio_compra_barra,
            precio_promedio_venta_barra=r.precio_promedio_venta_barra,
            por_cobrar=por_cobrar,
            por_pagar=por_pagar,
            # Los kilos pendientes pueden salir NEGATIVOS (se vendió queso que
            # venía de una temporada anterior): eso no es queso por vender, así
            # que "cerrada de verdad" mira que no SOBRE nada, no que dé cero justo.
            #
            # Y MIRA LAS DOS UNIDADES. Una temporada con 8 barras sin vender no está
            # cerrada, aunque no le quede un gramo de queso: sin la condición de las
            # barras la pantalla diría "cerrada de verdad" con mercancía todavía en
            # la bodega, que es exactamente la clase de mentira que este trabajo
            # tiene que evitar. Son dos condiciones separadas y no una suma: los
            # kilos con los kilos y las barras con las barras.
            cerrada_de_verdad=(
                r.kilos_pendientes <= CERO
                and r.barras_pendientes <= CERO
                and por_cobrar <= CERO
                and por_pagar <= CERO
            ),
        )

    def panel(self) -> TemporadasPanel:
        hoy = date.today()
        resumenes = ReventaResumenService(self.db, self.ctx)
        compras = CompraQuesoRepository(self.db, self.ctx.empresa_id)
        ventas = VentaQuesoRepository(self.db, self.ctx.empresa_id)
        temporadas = self.repo.vigentes()
        filas = [self._resumen_de(t, hoy, resumenes, compras, ventas) for t in temporadas]

        # Los totales son la SUMA EXACTA de las filas de la lista. Se suman las
        # filas ya calculadas y no se vuelve a consultar el rango completo: si se
        # consultara aparte, con huecos entre temporadas el total daría más que la
        # suma de la lista y el desglose dejaría de cuadrar. Los huecos se avisan
        # por separado con dias_sin_temporada.
        mejor = peor = None
        if filas:
            mejor = max(filas, key=lambda f: f.ganancia).nombre
            peor = min(filas, key=lambda f: f.ganancia).nombre

        # Huecos: días con compras o ventas que no caen en ninguna temporada.
        cubiertos = [
            (t.fecha_inicio, t.fecha_fin or max(hoy, t.fecha_inicio)) for t in temporadas
        ]
        sin_temporada = sum(
            1
            for f in self.repo.fechas_con_movimiento()
            if not any(inicio <= f <= fin for inicio, fin in cubiertos)
        )

        ultimo = self.repo.ultimo_cierre()
        return TemporadasPanel(
            temporadas=filas,
            total_ganancia=sum((f.ganancia for f in filas), CERO),
            total_kilos_comprados=sum((f.kilos_comprados for f in filas), CERO),
            total_ventas=sum((f.total_ventas for f in filas), CERO),
            total_compras=sum((f.total_compras for f in filas), CERO),
            mejor=mejor,
            peor=peor,
            dias_sin_temporada=sin_temporada,
            proximo_inicio=(ultimo + timedelta(days=1)) if ultimo else None,
        )


# -------------------------------------------------------------------- lotes
class LoteService:
    """Ganancia por LOTE de compra: qué dejó cada tanda de queso que se compró.

    Un lote son todas las compras de queso de una misma fecha. Toda la mecánica
    del reparto FIFO y del costeo está en `app.modules.reventa.lotes`, que es una
    función pura sin base de datos para poder probarla con casos armados a mano.
    Aquí solo se leen los eventos, se llama al reparto y se arma la respuesta.

    OJO: el reparto se hace SIEMPRE sobre toda la historia, aunque se pidan solo
    los lotes de un mes. Para saber qué había en inventario el 25 de julio hay que
    haber procesado lo de antes; si se filtrara la consulta, el inventario inicial
    sería inventado y las ventas de los primeros días quedarían "sin lote". El
    filtro de fechas se aplica al final, a qué lotes se muestran.
    """

    modulo = "reventa"

    def __init__(self, db, ctx):
        self.db = db
        self.ctx = ctx
        self.compras = CompraQuesoRepository(db, ctx.empresa_id)
        self.ventas = VentaQuesoRepository(db, ctx.empresa_id)
        self.ajustes = ConversionBoronaRepository(db, ctx.empresa_id)

    def _reparto(self):
        """El reparto FIFO completo, que es la base de todo lo de aquí abajo.

        Se calcula SIEMPRE sobre toda la historia, nunca sobre un filtro: para
        saber qué había en bodega un día hay que haber procesado lo de antes.
        """
        compras = [
            CompraEvento(
                fecha=fila[0], orden=indice, productor=fila[2],
                kilos=Decimal(fila[3] or 0), borona_kilos=Decimal(fila[4] or 0),
                precio_kilo=Decimal(fila[7] or 0),
                valor_total=Decimal(fila[5] or 0), saldo=Decimal(fila[6] or 0),
            )
            for indice, fila in enumerate(self.compras.eventos_para_lotes())
        ]
        ventas = [
            VentaEvento(
                fecha=fila[0], orden=indice, cliente=fila[6], tipo=fila[2],
                kilos=Decimal(fila[3] or 0), precio_kilo=Decimal(fila[7] or 0),
                valor_total=Decimal(fila[4] or 0), gasto_monto=Decimal(fila[5] or 0),
            )
            for indice, fila in enumerate(self.ventas.eventos_para_lotes())
        ]
        ajustes = [
            AjusteEvento(
                fecha=fila[0], orden=indice, kilos=Decimal(fila[2] or 0), destino=fila[3]
            )
            for indice, fila in enumerate(self.ajustes.eventos_para_lotes())
        ]

        return repartir_lotes(compras, ventas, ajustes)

    def panel(self, desde: date | None = None, hasta: date | None = None) -> LotesPanel:
        reparto = self._reparto()

        # El filtro recorta lo que se MUESTRA, no lo que se calculó
        visibles = [
            lote
            for lote in reparto.lotes
            if (desde is None or lote.fecha >= desde) and (hasta is None or lote.fecha <= hasta)
        ]

        filas = [self._fila(lote) for lote in visibles]
        mejor = peor = None
        if visibles:
            mejor = max(visibles, key=lambda l: l.ganancia).fecha
            peor = min(visibles, key=lambda l: l.ganancia).fecha

        return LotesPanel(
            lotes=filas,
            total_ganancia=sum((f.ganancia for f in filas), CERO),
            total_kilos_comprados=sum((f.kilos_comprados for f in filas), CERO),
            total_costo=sum((f.costo_total for f in filas), CERO),
            total_ingresos=sum((f.ingresos for f in filas), CERO),
            total_por_pagar=sum((f.por_pagar for f in filas), CERO),
            total_kilos_sin_vender=sum((f.kilos_sin_vender for f in filas), CERO),
            total_costo_sin_vender=sum((f.costo_sin_vender for f in filas), CERO),
            mejor=mejor,
            peor=peor,
            # Estos tres son del reparto COMPLETO y no del filtro: son un aviso de
            # que falta cargar una compra, y esconderlo al cambiar de mes sería
            # justo lo contrario de lo que se busca.
            kilos_sin_lote=reparto.kilos_sin_lote,
            borona_sin_lote=reparto.borona_sin_lote,
            ingreso_sin_lote=reparto.ingreso_sin_lote,
            # Cuántas barras de mozzarella hay compradas que NO están contadas en
            # este panel. No es un error como los tres de arriba: es el alcance del
            # panel dicho de frente. Este panel es de kilos, la mozzarella no entra
            # al reparto FIFO (ver eventos_para_lotes) y su ganancia se lee completa
            # en el Resumen. Callarlo dejaría al dueño creyendo que
            # `total_ganancia` es todo lo que dejó el negocio.
            barras_fuera_del_reparto=self.compras.barras_acumuladas(),
        )

    def ganancia_por_dia(self, desde: date, hasta: date) -> GananciaPorDia:
        """Lo que se ganó DE VERDAD entre dos fechas, día por día.

        Ojo con no confundirlo con la ganancia del resumen, que hace "ventas del
        período menos compras del período". Eso mezcla dos cosas distintas: un
        mes en que se compró mucho y se vendió poco sale en pérdida aunque no se
        haya perdido nada — el queso está en la bodega, no desaparecido.

        Esto es otra cuenta: de cada venta hecha en esos días se toma lo que
        entró, lo que había costado ESE queso en concreto (el reparto FIFO ya lo
        sabe, no es un promedio) y el flete que se pagó por despacharlo. Eso es
        lo que se ganó ese día, y por eso los días suman el total sin sobrar ni
        faltar un peso.

        Las compras de esos días no restan aquí: comprar no es gastar, es
        cambiar plata por queso. Se ve aparte, en la cartera y en el inventario.
        """
        reparto = self._reparto()
        por_dia: dict[date, dict[str, Decimal]] = {}
        for lote in reparto.lotes:
            for v in lote.detalle_ventas:
                if v.fecha < desde or v.fecha > hasta:
                    continue
                d = por_dia.setdefault(
                    v.fecha,
                    {"kilos": CERO, "ingresos": CERO, "costo": CERO, "gastos": CERO},
                )
                d["kilos"] += v.kilos
                d["ingresos"] += v.ingreso
                d["costo"] += v.costo
                d["gastos"] += v.gasto

        dias = [
            GananciaDia(
                fecha=fecha,
                kilos=v["kilos"],
                ingresos=v["ingresos"],
                costo=v["costo"],
                gastos=v["gastos"],
                ganancia=v["ingresos"] - v["costo"] - v["gastos"],
            )
            for fecha, v in sorted(por_dia.items())
        ]
        return GananciaPorDia(
            desde=desde,
            hasta=hasta,
            dias=dias,
            kilos=sum((d.kilos for d in dias), CERO),
            ingresos=sum((d.ingresos for d in dias), CERO),
            costo=sum((d.costo for d in dias), CERO),
            gastos=sum((d.gastos for d in dias), CERO),
            # El total es la SUMA de los días, no una cuenta aparte: así el
            # desglose cuadra por construcción y no por casualidad.
            ganancia=sum((d.ganancia for d in dias), CERO),
        )

    @staticmethod
    def _fila(lote: LoteCalculado) -> LoteResumen:
        kilos_vendidos_totales = lote.kilos_vendidos + lote.borona_vendida
        return LoteResumen(
            fecha=lote.fecha,
            productores=lote.productores,
            compras=lote.compras,
            kilos_comprados=lote.kilos_comprados,
            costo_total=_dinero(lote.costo_total),
            costo_kilo=(
                _dinero(lote.costo_total / lote.kilos_comprados)
                if lote.kilos_comprados > CERO
                else CERO
            ),
            por_pagar=_dinero(lote.por_pagar),
            borona_recibida=lote.borona_recibida,
            kilos_vendidos=lote.kilos_vendidos,
            kilos_a_borona=lote.kilos_a_borona,
            kilos_merma=lote.kilos_merma,
            kilos_sin_vender=lote.kilos_sin_vender,
            borona_vendida=lote.borona_vendida,
            borona_sin_vender=lote.borona_sin_vender,
            ingreso_queso=_dinero(lote.ingreso_queso),
            ingreso_borona=_dinero(lote.ingreso_borona),
            ingresos=_dinero(lote.ingresos),
            gastos=_dinero(lote.gastos),
            costo_vendido=_dinero(lote.costo_vendido),
            costo_borona_vendida=_dinero(lote.costo_borona_vendida),
            costo_merma=_dinero(lote.costo_merma),
            costo_sin_vender=_dinero(lote.costo_sin_vender),
            ganancia=_dinero(lote.ganancia),
            margen_kilo=(
                _dinero(lote.ganancia / kilos_vendidos_totales)
                if kilos_vendidos_totales > CERO
                else CERO
            ),
            precio_venta_kilo=(
                _dinero(lote.ingreso_queso / lote.kilos_vendidos)
                if lote.kilos_vendidos > CERO
                else CERO
            ),
            cerrado=lote.cerrado,
            detalle_compras=[
                CompraDelLoteRead(
                    productor=c.productor,
                    kilos=c.kilos,
                    borona_recibida=c.borona_recibida,
                    precio_kilo=_dinero(c.precio_kilo),
                    valor_total=_dinero(c.valor_total),
                    saldo=_dinero(c.saldo),
                    kilos_vendidos=c.kilos_vendidos,
                    kilos_a_borona=c.kilos_a_borona,
                    kilos_merma=c.kilos_merma,
                    kilos_sin_vender=c.kilos_sin_vender,
                    borona_vendida=c.borona_vendida,
                    borona_sin_vender=c.borona_sin_vender,
                    ingresos=_dinero(c.ingresos),
                    gastos=_dinero(c.gastos),
                    costo_realizado=_dinero(c.costo_realizado),
                    costo_sin_vender=_dinero(c.costo_sin_vender),
                    ganancia=_dinero(c.ganancia),
                    margen_kilo=(
                        _dinero(c.ganancia / (c.kilos_vendidos + c.borona_vendida))
                        if (c.kilos_vendidos + c.borona_vendida) > CERO
                        else CERO
                    ),
                )
                for c in lote.detalle_compras
            ],
            detalle_ventas=[
                VentaDelLoteRead(
                    fecha=v.fecha,
                    cliente=v.cliente,
                    tipo=v.tipo,
                    kilos=v.kilos,
                    kilos_venta=v.kilos_venta,
                    precio_kilo=_dinero(v.precio_kilo),
                    ingreso=_dinero(v.ingreso),
                    gasto=_dinero(v.gasto),
                    costo=_dinero(v.costo),
                    ganancia=_dinero(v.ganancia),
                    partida=v.partida,
                )
                for v in lote.detalle_ventas
            ],
        )
