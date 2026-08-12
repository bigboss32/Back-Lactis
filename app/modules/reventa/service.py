"""Reventa de queso: compras a productores con merma y abonos, ventas a
clientes y resumen de ganancia. Contabilidad separada del libro de la quesera.
"""
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
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
    TIPO_BORONA,
    TIPO_DOC_COMPRA,
    TIPO_DOC_VENTA,
    TIPO_MOZZARELLA,
    TIPO_QUESO,
    TIPO_SALDO_COBRAR,
    TIPO_SALDO_PAGAR,
    TIPO_VENTA_BORONA,
    TIPO_VENTA_MOZZARELLA,
    TIPO_VENTA_QUESO,
    UNIDAD_BARRA,
    UNIDAD_KILO,
    UNIDAD_UNIDAD,
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
from app.common.dinero import repartir_al_resto_mayor
from app.modules.reventa.catalogo import (
    CLAVE_SIN_IDENTIFICAR,
    CatalogoReventa,
    GrupoDeCosteo,
)
from app.modules.reventa.existencias import ExistenciasReventa
from app.modules.reventa.lotes import (
    AjusteEvento,
    CompraEvento,
    LoteCalculado,
    VentaEvento,
    kilos_que_salen_de_lo_pagado,
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
    ExistenciaProducto,
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


@dataclass(frozen=True)
class _MovimientoDeProducto:
    """Lo que se compró y se vendió DE UN PRODUCTO en el período, en su unidad.

    Es el ladrillo del desglose: una de estas por producto y por unidad, y el resumen
    entero se arma sumándolas y repartiendo su plata. Va congelado (`frozen`) porque
    estas cifras salen de la base y de ahí en adelante solo se leen: una que se pudiera
    modificar a mitad del cálculo dejaría dos filas del desglose contando distinto.

    `comprado` y `vendido` son kilos en un movimiento que se pesa y unidades en uno que
    se cuenta, y NUNCA las dos cosas: las dos clases viajan en diccionarios separados
    (ver `_movimientos_del_periodo`), que es lo que impide que "20 kg + 8 barras"
    llegue a ser un número.
    """

    comprado: Decimal = CERO
    comprado_plata: Decimal = CERO
    # Lo que llegó GRATIS ENCIMA de las compras DE ESTE producto (la columna
    # `borona_kilos`). No suma al pozo del costo —no se pagó— y no dice a quién le
    # entró: eso lo dice cada compra en `subproducto_tipo`, y quien lo necesita lo
    # pide agrupado por DESTINATARIO (`gratis_periodo_por_subproducto`). Este campo se
    # queda por si alguna pantalla quiere mostrar "cuánto llegó gratis con el queso".
    gratis: Decimal = CERO
    vendido: Decimal = CERO
    vendido_plata: Decimal = CERO
    gastos: Decimal = CERO


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
    # La fila de la red de seguridad (ver `_cuadrar_desglose`). El texto no acusa a
    # nadie ni inventa una explicación: dice qué es —plata que no quedó en ninguna
    # de las filas de arriba— y deja al dueño con algo que preguntar.
    "sin_producto": "Sin producto (plata sin clasificar)",
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
    "sin_producto": "revise el producto de esos movimientos: no quedó en ninguna unidad",
}
# Cuando unos kilos no tienen costo porque la compra cayó fuera del período, no
# se puede hablar de pérdida en pesos: se dice de dónde vienen.
NOTA_SIN_COSTO = "se compró en un período anterior: aquí no lleva costo"

# ---------------------------------------------------------- los nombres YA IMPRESOS
# LA CLAVE DE LA FILA DEL RESIDUO de cada grupo de costeo. El desglose ahora se arma
# solo, producto por producto, y la regla general para nombrar la fila del residuo de
# un grupo es "{clave del grupo}_pendiente". Pero los dos grupos que el cliente ya
# tiene llevan meses saliendo con OTROS nombres —'pendiente' y 'mozzarella_pendiente'—
# y esos nombres no son decoración: son el campo `producto` que la pantalla usa para
# decidir cómo pintar cada renglón, y el rótulo que está impreso en los comprobantes
# que el dueño ya archivó.
#
# ESTO ES UNA TABLA DE NOMBRES, NO UNA REGLA DE PLATA, y ahí está toda la diferencia
# con lo que había antes. Ninguna cifra se decide aquí: el costo, el ingreso y la
# cantidad de esas filas salen del catálogo igual que los de cualquier otro producto.
# Lo único que esta tabla conserva es CÓMO SE LLAMA la fila, para no renombrarle al
# dueño dos renglones que ya conoce.
CLAVES_DE_RESIDUO_HISTORICAS: dict[str, tuple[str, str]] = {
    TIPO_QUESO: ("pendiente", "anterior"),
    TIPO_MOZZARELLA: ("mozzarella_pendiente", "mozzarella_anterior"),
}
# Lo mismo para la fila de la MERMA. La regla general es "{clave del producto}_merma",
# pero la merma del queso lleva meses saliendo con la clave pelada 'merma'.
CLAVES_DE_MERMA_HISTORICAS: dict[str, str] = {TIPO_QUESO: "merma"}


# ------------------------------------- las claves que el desglose se reserva
# EL PROBLEMA, Y LO ENCONTRÓ UNA REVISIÓN ADVERSARIAL: el desglose tiene filas que NO
# son de un producto —la merma, lo que quedó en inventario, lo que salió de inventario
# anterior, la plata sin clasificar— y cada una viaja con su propia clave en el campo
# `producto`, que es con lo que la pantalla decide cómo pintar el renglón. Si el dueño
# creaba un producto llamado "Merma", su clave era 'merma': su renglón salía rotulado
# "Merma (pérdida real)" con una VENTA de $200.000 adentro, y además había DOS filas
# con la clave 'merma' en la misma respuesta.
#
# LA REGLA, Y ES UNA SOLA: ninguna clave de producto puede ser una de las calculadas ni
# terminar como terminan las calculadas. Se exige al CREAR el producto (ver
# `clave_sin_chocar_con_el_desglose`), así que el catálogo no puede llegar a tener una,
# y se vuelve a aplicar al armar la fila para atajar una que hubiera quedado guardada
# de antes. El dueño no se entera: le pone al producto el nombre que quiera y lo que
# cambia es la clave interna, que él nunca ve.
CLAVES_CALCULADAS_DEL_DESGLOSE = frozenset(
    {"merma", "pendiente", "anterior", "sin_producto", CLAVE_SIN_IDENTIFICAR}
)
# Y los tres sufijos con los que se arman las calculadas de los demás grupos. Reservar
# el sufijo entero —y no cada clave concreta— es lo que hace que esto sea hermético:
# la fila calculada de CUALQUIER producto futuro tiene la forma "{clave}_pendiente", y
# si ninguna clave puede terminar así, ninguna puede chocar con ella.
SUFIJOS_CALCULADOS_DEL_DESGLOSE = ("_pendiente", "_anterior", "_merma")
# Lo que se le agrega a una clave que chocaba. Va al final para que la clave se siga
# leyendo ('merma_producto' se entiende), y no puede volver a chocar porque no es
# ninguno de los tres sufijos reservados.
SUFIJO_PARA_NO_CHOCAR = "_producto"


def choca_con_una_fila_calculada(clave: str) -> bool:
    """Si esta clave le quitaría el renglón a una fila calculada del desglose."""
    return clave in CLAVES_CALCULADAS_DEL_DESGLOSE or clave.endswith(
        SUFIJOS_CALCULADOS_DEL_DESGLOSE
    )


def clave_sin_chocar_con_el_desglose(clave: str) -> str:
    """La clave de un producto, corrida si chocaba con una fila calculada."""
    return f"{clave}{SUFIJO_PARA_NO_CHOCAR}" if choca_con_una_fila_calculada(clave) else clave


def _clave_de_fila(clave: str) -> str:
    """La clave con la que sale la fila de un producto en la respuesta.

    Es la del producto, con dos atajos:

    · CUANDO EL `tipo` DE LAS FILAS ESTÁ EN BLANCO la respuesta llevaría una cadena
      vacía en el campo con el que la pantalla decide cómo pintar el renglón, que no
      le dice nada a nadie. Se le pone un nombre.
    · CUANDO LA CLAVE CHOCA con una de las filas calculadas se corre, para que las dos
      puedan salir en la misma respuesta sin pisarse (ver arriba).
    """
    if not clave:
        return CLAVE_SIN_IDENTIFICAR
    return clave_sin_chocar_con_el_desglose(clave)


def _claves_de_residuo(clave_del_grupo: str) -> tuple[str, str]:
    """(clave si sobró, clave si faltó) de la fila del residuo de un pozo."""
    if clave_del_grupo in CLAVES_DE_RESIDUO_HISTORICAS:
        return CLAVES_DE_RESIDUO_HISTORICAS[clave_del_grupo]
    base = _clave_de_fila(clave_del_grupo)
    return f"{base}_pendiente", f"{base}_anterior"


def _clave_de_merma(clave_del_producto: str) -> str:
    """La clave de la fila donde se reporta la merma DE ESE PRODUCTO.

    Es una fila por producto de origen y no una sola del período: un ajuste dice de
    qué producto salieron los kilos (ver `ConversionBorona`), y juntarlos todos en un
    renglón obligaría a repartir después una plata que ya se sabe de quién es.
    """
    if clave_del_producto in CLAVES_DE_MERMA_HISTORICAS:
        return CLAVES_DE_MERMA_HISTORICAS[clave_del_producto]
    return f"{_clave_de_fila(clave_del_producto)}_merma"


def _etiqueta_y_nota(
    clave_fila: str, nombre: str, *, papel: str, se_pesa: bool
) -> tuple[str, str]:
    """El rótulo y el sub-texto de una fila del desglose, LISTOS PARA LA PANTALLA.

    Primero se busca en las tablas de textos de siempre: si la fila es una de las que
    el dueño ya conoce, sale con el texto exacto con el que siempre salió. Si es de un
    producto que él agregó, el texto se arma con SU NOMBRE —el que le puso en el
    catálogo, no la clave interna—, siguiendo el mismo patrón de las de siempre.

    `papel` dice qué es la fila: 'vendido' (lo que salió vendido de este producto),
    'merma' (lo que se perdió de él), 'pendiente' (lo que quedó en bodega) o
    'anterior' (lo que salió de un inventario de antes del período).

    EL `papel` MANDA SOBRE LA TABLA DE TEXTOS y por eso se pregunta primero: un
    producto que el dueño llame "Merma" tiene una fila de VENTAS, y sacarla con el
    rótulo "Merma (pérdida real)" —solo porque su clave se parece— le mostraría una
    pérdida donde hubo una venta. La tabla de textos solo contesta por las filas que
    de verdad son las de siempre.
    """
    if papel == "merma":
        if clave_fila in ETIQUETAS_PRODUCTO:
            return ETIQUETAS_PRODUCTO[clave_fila], NOTAS_PRODUCTO[clave_fila]
        return f"Merma de {nombre} (pérdida real)", NOTAS_PRODUCTO["merma"]
    if clave_fila in ETIQUETAS_PRODUCTO:
        return ETIQUETAS_PRODUCTO[clave_fila], NOTAS_PRODUCTO[clave_fila]
    if papel == "pendiente":
        return (
            f"{nombre} aún en inventario" + ("" if se_pesa else " (unidades)"),
            "plata invertida, aún sin vender",
        )
    if papel == "anterior":
        return (
            f"{nombre} salido de inventario anterior"
            + ("" if se_pesa else " (unidades)"),
            "se compró en un período anterior",
        )
    if se_pesa:
        return f"Vendido como {nombre}", "producto del catálogo, vendido por kilo"
    return f"{nombre} vendido (unidades)", "producto del catálogo, vendido por unidad"

# Nombre del producto listo para mostrarle al cliente en su estado de cuenta, para
# cuando la clave NO ESTÁ en el catálogo (una fila vieja, una importada). Con el
# producto en el catálogo manda SU NOMBRE, que es el que el dueño le puso.
#
# POR QUÉ IMPORTA QUE MANDE EL DEL CATÁLOGO: este documento SE LE ENTREGA AL CLIENTE.
# Antes el rótulo salía de `clave.capitalize()`, así que un producto llamado "Queso
# costeño artesanal" llegaba al cliente como 'Queso_costeno_artesanal' —con guiones
# bajos y sin tilde— y renombrarlo no cambiaba nada de lo que él leía. Los tres de
# siempre se llaman igual en el catálogo que aquí, así que para el cliente actual el
# documento sale idéntico.
NOMBRE_PRODUCTO = {
    TIPO_VENTA_QUESO: "Queso",
    TIPO_VENTA_BORONA: "Borona",
    TIPO_VENTA_MOZZARELLA: "Mozzarella",
}


def _nombre_para_el_cliente(catalogo: CatalogoReventa, clave: str) -> str:
    """Cómo se le nombra el producto al cliente en su estado de cuenta."""
    producto = catalogo.de(clave)
    if producto.del_catalogo:
        return producto.nombre
    return NOMBRE_PRODUCTO.get(clave, clave.capitalize())


def _exigir_destino(
    destino: str | None,
    catalogo: CatalogoReventa,
    clave_padre: str,
    propuesto: str | None,
    *,
    de_donde: str,
) -> str:
    """Devuelve el destinatario ya resuelto, o revienta con un mensaje que dice QUÉ
    HACER.

    El mensaje es el trabajo de esta función. Un rechazo que solo dice "no se puede"
    deja al dueño mirando la pantalla sin saber qué le falta.

    Y AQUÍ LLEGAN POCOS CASOS, A PROPÓSITO: el catálogo ya resuelve todo lo que se
    puede resolver sin adivinar (ver `CatalogoReventa._quien_recibe`, la regla de
    "registrar siempre gana"), así que un destinatario en nulo significa que de verdad
    hace falta que una persona decida. Son dos situaciones y cada una tiene su salida:
    nombró un producto que no puede recibir kilos, o el producto de siempre existe pero
    se cuenta por unidades.

    `de_donde` es cómo se llaman esos kilos en el texto ("esos kilos que llegaron
    gratis" / "estos kilos"): el mensaje tiene que hablar de lo que él acaba de
    escribir.
    """
    if destino is not None:
        return destino

    producto = catalogo.de(clave_padre)
    if propuesto:
        elegido = catalogo.de(propuesto)
        if elegido.del_catalogo:
            raise BusinessError(
                f"{elegido.nombre} se cuenta por unidades, así que no puede recibir "
                f"{de_donde}: una barra no se pesa. Escoja un producto que se pese, o "
                "cámbiele la unidad en el catálogo"
            )
        raise BusinessError(
            f"'{propuesto}' no es un producto de esta empresa, así que no puede "
            f"recibir {de_donde}. Revise el nombre, o agréguelo al catálogo como un "
            "producto que se pesa"
        )
    # Nadie lo nombró y el producto de siempre no puede recibir kilos: es el único
    # camino que queda sin destinatario, y la salida es decir a cuál le entran.
    candidatos = [p for p in catalogo.subproductos_de(producto.clave) if p.se_pesa]
    nombres = ", ".join(f"'{p.nombre}'" for p in candidatos)
    de_siempre = catalogo.de(TIPO_BORONA)
    raise BusinessError(
        f"Diga a qué producto le entran {de_donde}: '{de_siempre.nombre}' se cuenta "
        "por unidades y no puede recibir kilos"
        + (f". Puede ser {nombres}" if candidatos else "")
    )


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
        # SI LA CLAVE CHOCA CON UNA FILA CALCULADA DEL DESGLOSE, SE CORRE. Un producto
        # llamado "Merma" generaba la clave 'merma', que es la de la fila de la pérdida:
        # su renglón salía rotulado "Merma (pérdida real)" con una VENTA adentro y
        # además quedaban dos filas con la misma clave en la misma respuesta, que es
        # con lo que la pantalla decide cómo pintar cada renglón. Se le cambia la clave
        # y no el nombre: el nombre es del dueño y sale tal cual en todas partes.
        data["clave"] = clave_sin_chocar_con_el_desglose(clave)

    def _unidad_y_derivados(self, data: dict[str, Any]) -> None:
        unidad = data.get("unidad") or UNIDAD_KILO
        data["unidad"] = unidad
        # La deducción vive en el modelo, al lado del CHECK que la exige, porque la
        # comparte con la siembra de cada despliegue (ver `derivados_de_unidad`).
        data["decimales"], data["admite_ajustes"] = derivados_de_unidad(unidad)

    def _padre(
        self,
        padre_id: uuid.UUID,
        propio_id: uuid.UUID | None = None,
        *,
        unidad: str | None = None,
        nombre: str = "",
    ) -> ProductoReventa:
        """El producto del que otro sería subproducto, validado.

        `self.repo.get` ya filtra por empresa y por borrados, así que un id de OTRA
        empresa sale como "no existe" y no como un 403: a nadie se le confirma que
        ese producto existe en otra parte.

        LA CADENA SE CORTA EN UN NIVEL, en las dos direcciones. El motor FIFO de
        `lotes.py` implementa exactamente una relación padre-subproducto (queso ->
        borona, con el costo heredado); un subproducto de un subproducto no tendría
        cómo costearse, y ofrecerlo sería prometer una cuenta que no existe.

        Y EL PADRE Y EL SUBPRODUCTO SE MIDEN IGUAL, los dos en kilos o los dos por
        unidades. No es una manía de coherencia: el grupo de costeo tiene UN pozo y ese
        pozo está en la unidad de la raíz (ver `GrupoDeCosteo`), así que un subproducto
        que se pesa colgado de un padre que se cuenta no tiene de dónde heredar costo
        —no se reparten barras entre kilos—. Medido: colgar la borona (kg) de la
        mozzarella (unidades) sacaba el desglose con TRES claves repetidas —el grupo
        salía impreso dos veces, una en la vuelta de los kilos y otra en la de las
        unidades— y le acreditaba $30.000 de neto al productor equivocado.
        """
        if propio_id is not None and padre_id == propio_id:
            raise BusinessError("Un producto no puede ser subproducto de sí mismo")
        padre = self.repo.get(padre_id)
        if padre is None:
            raise NotFoundError(
                "El producto del que este sería subproducto no existe en esta empresa"
            )
        if unidad is not None and padre.unidad != unidad:
            propio = f"'{nombre}'" if nombre else "Este producto"
            se_pesa, se_cuenta = (
                (propio, f"'{padre.nombre}'")
                if unidad == UNIDAD_KILO
                else (f"'{padre.nombre}'", propio)
            )
            raise BusinessError(
                f"{se_pesa} se pesa y {se_cuenta} se cuenta por unidades, así que uno "
                "no puede ser subproducto del otro: lo que llega gratis hereda el costo "
                "de su padre, y los kilos no se reparten entre barras. Póngales la "
                "misma unidad, o déjelo como producto independiente"
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

    def _exigir_que_todavia_pueda_tener_padre(self, clave: str, nombre: str) -> None:
        """AGREGAR UN PRODUCTO TAMPOCO PUEDE MOVER PLATA YA REGISTRADA.

        Es el mismo candado que `validar_actualizar` y `validar_eliminar` ya ponían en
        el PUT y en el DELETE, en la puerta que faltaba: la de CREAR (y la de revivir,
        que es la misma puerta). Ahí estaba el hueco, y no era teórico:

        · una clave puede tener plata anotada SIN estar en el catálogo —una fila vieja,
          una importada, un tipo escrito de otra forma, o una empresa a la que todavía
          no le han sembrado la lista—, y esos kilos se leen como los de un producto
          RAÍZ, con su pozo y su fila propios;
        · el día que alguien la agrega a la lista marcándola subproducto de otro, el
          grupo de costeo de esas filas VIEJAS cambia, y el reparto de la ganancia se
          rehace sobre compras y ventas que ya estaban cuadradas. Medido: Patricia
          Rojas pasaba de $340.000,00 a -$373.333,33 y Sebastián Ruiz de $50.000,00 a
          $763.333,33 sin que nadie tocara una compra.

        Agregarlo SIN padre sí se puede, y por eso el mensaje manda para allá: como
        producto independiente es exactamente como sus kilos ya están contados, así que
        no mueve una sola cifra. Lo que no se puede es meterlo al grupo de otro cuando
        ya tiene historia propia.
        """
        compras, ventas, recibidas = self.repo.movimientos(clave)
        ajustes = self.repo.ajustes(clave)
        if compras or ventas or ajustes or recibidas:
            raise BusinessError(
                f"'{nombre}' ya tiene "
                f"{self._texto_movimientos(compras, ventas, ajustes, recibidas)} a "
                "nombre suyo, así que no se puede agregar como subproducto de otro "
                "producto: esa plata ya está contada como la de un producto "
                "independiente, y meterla al grupo de otro recostearía cuentas que "
                "usted ya cuadró. Agréguelo sin marcarlo como subproducto"
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
            self._exigir_que_todavia_pueda_tener_padre(data["clave"], data["nombre"])
            self._padre(
                data["subproducto_de_id"],
                unidad=data["unidad"],
                nombre=data["nombre"],
            )

    def _revivir(
        self, dormido: ProductoReventa, data: dict[str, Any]
    ) -> ProductoReventa:
        self._validar_nombre_libre(data["nombre"], propio_id=dormido.id)
        padre_id = data.get("subproducto_de_id")
        if padre_id:
            # MIENTRAS ESTUVO FUERA DE LA LISTA PUDO RECIBIR PLATA, y esos kilos se
            # leyeron como los de un producto raíz (la fila que los nombra lo hace con
            # su clave, esté o no en el catálogo). Por eso acá se pregunta por los
            # movimientos aunque la fila ya viniera colgada de ese mismo padre antes de
            # quitarla: lo que cambia el grupo de costeo es volver a estar en la lista
            # marcada como subproducto, no el valor que tenga guardado la columna.
            self._exigir_que_todavia_pueda_tener_padre(dormido.clave, data["nombre"])
            self._padre(
                padre_id,
                propio_id=dormido.id,
                unidad=dormido.unidad,
                nombre=data["nombre"],
            )
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
            compras, ventas, recibidas = self.repo.movimientos(obj.clave)
            # LOS AJUSTES CUENTAN COMO MOVIMIENTOS PARA ESTO. Un ajuste guarda de qué
            # producto salieron los kilos y a cuál le entraron; re-colgar el producto
            # dejaría esos ajustes cruzando dos grupos de costeo, y el desglose le
            # cargaría a un grupo el costo de kilos que salieron de otro. Es
            # exactamente la clase de cambio del catálogo que no puede mover plata ya
            # registrada.
            #
            # Y LAS COMPRAS QUE LE TRAJERON KILOS GRATIS CUENTAN IGUAL, por la misma
            # razón y con más fuerza: esos kilos están en la bodega a nombre suyo, y
            # colgarlo de otro padre los pondría a heredar el costo de un producto del
            # que nunca salieron.
            ajustes = self.repo.ajustes(obj.clave)
            if compras or ventas or ajustes or recibidas:
                raise BusinessError(
                    f"'{obj.nombre}' ya tiene "
                    f"{self._texto_movimientos(compras, ventas, ajustes, recibidas)}, "
                    "así que no se le puede cambiar de qué producto es subproducto: de "
                    "ahí hereda el costo lo que se venda de él, y cambiarlo recostearía "
                    "cuentas que usted ya cuadró"
                )
            if data["subproducto_de_id"] is not None:
                self._padre(
                    data["subproducto_de_id"],
                    propio_id=obj.id,
                    unidad=obj.unidad,
                    nombre=obj.nombre,
                )

    # ----------------------------------------------------------------- quitar
    @staticmethod
    def _texto_movimientos(
        compras: int, ventas: int, ajustes: int = 0, recibidas: int = 0
    ) -> str:
        """Qué historia tiene el producto, dicho para que se pueda ir a buscarla.

        LAS COMPRAS QUE LE TRAJERON KILOS SE DICEN APARTE Y CON ESAS PALABRAS. Sumarlas
        a las suyas dejaría un mensaje que manda a buscar lo que no hay: "'Borona' ya
        tiene 2 compras" y el dueño abre la lista de compras de borona y no encuentra
        ninguna, porque las dos son compras de QUESO que le trajeron kilos encima.
        """
        partes = []
        if compras:
            partes.append(f"{compras} compra{'s' if compras != 1 else ''}")
        if ventas:
            partes.append(f"{ventas} venta{'s' if ventas != 1 else ''}")
        if ajustes:
            partes.append(f"{ajustes} ajuste{'s' if ajustes != 1 else ''}")
        if recibidas:
            partes.append(
                f"kilos que le llegaron en {recibidas} "
                f"compra{'s' if recibidas != 1 else ''} de otro producto"
            )
        return " y ".join(partes)

    def validar_eliminar(self, obj: ProductoReventa) -> None:
        """Solo sale del catálogo lo que nunca se movió.

        Es la misma regla del resto del ERP, y acá tiene su razón propia: la clave
        del producto es lo que las filas de compras y de ventas tienen guardado. Un
        producto quitado con movimientos encima dejaría filas del cuaderno hablando
        de algo que ya no aparece en ninguna lista, y el dueño no tendría cómo saber
        qué fue lo que compró.

        Y "MOVERSE" INCLUYE HABER RECIBIDO KILOS GRATIS EN LA COMPRA DE OTRO PRODUCTO.
        Ahí estaba el hueco: un producto que solo hubiera recibido lo que llegó encima
        de un lote no tenía compras ni ventas propias, así que esta puerta lo dejaba
        pasar y se quitaba del catálogo un producto con 25,36 kg en la bodega —los
        mismos que el resumen seguía reportando en las existencias—.

        Para eso está DESACTIVARLO, que es la salida real cuando ya se movió: deja de
        ofrecerse al registrar y su historia se queda completa. Se le dice en el
        mensaje, porque un rechazo sin salida es un rechazo a medias.
        """
        compras, ventas, recibidas = self.repo.movimientos(obj.clave)
        ajustes = self.repo.ajustes(obj.clave)
        if compras or ventas or ajustes or recibidas:
            raise BusinessError(
                "Solo se puede quitar un producto que no tenga movimientos: "
                f"'{obj.nombre}' ya tiene "
                f"{self._texto_movimientos(compras, ventas, ajustes, recibidas)}. "
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

    @property
    def productos(self) -> ProductoReventaRepository:
        """El catálogo DE ESTA EMPRESA. Se arma con `self.ctx.empresa_id` y por eso
        ninguna consulta suya puede ver los productos de la otra quesera: es la
        única puerta por la que el módulo pregunta en qué unidad está un producto.
        """
        return ProductoReventaRepository(self.db, self.ctx.empresa_id)

    @property
    def catalogo(self) -> CatalogoReventa:
        """El catálogo resuelto, que es QUIEN MANDA sobre la unidad de un renglón.

        Se arma nuevo en cada acceso a propósito: una escritura puede crear un
        producto y usarlo en la misma petición, y un catálogo cacheado en el servicio
        contestaría con la foto de antes.
        """
        return CatalogoReventa(self.db, self.ctx.empresa_id)

    @staticmethod
    def _calcular(
        data: dict[str, Any],
        actual: CompraQueso | None = None,
        *,
        unidad: str = UNIDAD_KILO,
    ) -> dict[str, Any]:
        """Deja la fila con la cantidad y el precio de SU unidad, y la otra en cero.

        LA UNIDAD LA MANDA QUIEN LLAMA, Y VIENE DEL CATÁLOGO. Antes esta función
        preguntaba `tipo == 'mozzarella'`, y ahí estaba el defecto más caro de todos:
        cualquier otro producto POR UNIDAD —una panela, un huevo, lo que el dueño
        agregara— caía en la rama de los kilos, que pone `barras = 0` y
        `precio_barra = 0` y calcula la plata como kilos × precio por kilo. Y como el
        esquema de entrada RECHAZA que una compra por unidad traiga kilos, esos dos
        factores eran cero por obligación: valor_total = 0 × 0 = 0. La compra se
        aceptaba con 201 y se guardaba en ceros: la plata desaparecía entera.

        `unidad` es obligatoria de hecho aunque tenga valor por defecto: el defecto
        por defecto son los KILOS, que es lo que era todo antes de que existiera el
        catálogo y lo mismo que hace la clasificación de lectura con una clave que no
        reconoce (`se_mide_en_kilos`). Así una fila no puede entrar calculada en una
        unidad y quedar leída en la otra.

        El tipo se toma del payload al crear y de la FILA GUARDADA al editar: la
        edición no acepta `tipo` a propósito (ver CompraQuesoUpdate), así que una
        compra nace de kilos o de unidades y se queda así.
        """
        tipo = data.get("tipo") or (actual.tipo if actual else TIPO_VENTA_QUESO)
        data["tipo"] = tipo
        if unidad == UNIDAD_UNIDAD:
            barras = Decimal(data.get("barras") or (actual.barras if actual else CERO))
            precio_barra = Decimal(
                data.get("precio_barra") or (actual.precio_barra if actual else CERO)
            )
            data["barras"] = barras
            data["precio_barra"] = precio_barra
            # Todo lo que se mide en kilos queda en cero, y se escribe AQUÍ y no
            # solo en el esquema de entrada: por PUT llega un payload parcial y sin
            # esto una compra por unidad podría quedar con kilos de un intento
            # anterior. Y AHORA IMPORTA MÁS QUE ANTES: la tabla ya no tiene el CHECK
            # que rechazaba una fila con kilos Y unidades, así que esta es la única
            # cosa que impide que exista. Una fila así se clasificaría por lo que
            # trae (ver `se_mide_en_unidades`) y su plata quedaría contada en una
            # unidad mientras sus kilos entran al reparto de la otra.
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
        """La plata de cada renglón, SIN escribir nada.

        Cada renglón pasa por el mismo `_calcular` de siempre, que es el que deja la
        cantidad en la columna de SU unidad y la otra en cero. Ya no lo exige ningún
        CHECK de la tabla —se quitaron al abrir el catálogo—, así que esto es lo único
        que impide una fila con kilos Y unidades, que se contaría en las dos canastas.

        LA UNIDAD LA MANDA EL CATÁLOGO DE ESTA EMPRESA (ver `CatalogoReventa`, que se
        carga con `empresa_id` y filtrando borrados). Antes se recorrían los productos
        de TODAS las queseras y dos claves iguales se pisaban entre sí, así que la
        unidad de la compra la podía estar poniendo el catálogo del vecino.
        """
        datos: list[dict[str, Any]] = []
        catalogo = self.catalogo

        for orden, renglon in enumerate(renglones):
            data = _campos_del_renglon(renglon, RenglonCompraCreate)
            tipo = data.get("tipo") or TIPO_QUESO
            data["tipo"] = tipo
            # Lo que no está en el catálogo de esta empresa se pesa, que es como lo
            # lee el resumen (ver `se_mide_en_kilos`).
            unidad = catalogo.unidad_de(tipo)
            nombre = catalogo.de(tipo).nombre

            # Revalidar que los campos enviados coincidan con la unidad del catálogo.
            # El mensaje habla del NOMBRE que el dueño le puso al producto y no de la
            # clave: la clave es la identidad interna, y decirle "una compra de
            # queso_costeno" es hablarle en un idioma que no es el suyo.
            if unidad == UNIDAD_UNIDAD and (data.get("kilos_brutos") or data.get("precio_kilo")):
                raise BusinessError(
                    f"Una compra de {nombre} se cuenta por unidades: necesita las "
                    f"unidades y el precio de cada una, no kilos."
                )
            if unidad == UNIDAD_KILO and (data.get("barras") or data.get("precio_barra")):
                raise BusinessError(
                    f"Una compra de {nombre} se pesa: necesita los kilos y el precio "
                    f"por kilo, no unidades."
                )

            # LA UNIDAD DEL CATÁLOGO ES LA QUE CALCULA LA PLATA, y este es el arreglo
            # del defecto: antes `_calcular` decidía por su cuenta preguntando si el
            # tipo era literalmente 'mozzarella', así que la compra de cualquier otro
            # producto por unidad se guardaba en ceros.
            data = self._calcular(data, unidad=unidad)
            data["subproducto_tipo"] = self._destinatario_de_lo_gratis(
                catalogo, tipo, data.get("borona_kilos"), data.get("subproducto_tipo")
            )
            data["orden"] = orden
            data["estado"] = ESTADO_PENDIENTE
            datos.append(data)
        return datos

    @staticmethod
    def _destinatario_de_lo_gratis(
        catalogo: CatalogoReventa,
        tipo: str,
        gratis: Any,
        propuesto: str | None,
    ) -> str | None:
        """A qué producto le entran los kilos que llegaron GRATIS con esta compra.

        SE DECIDE AL ESCRIBIR Y SE GUARDA EN LA FILA (`CompraQueso.subproducto_tipo`).
        Antes no se guardaba: cada vez que alguien pedía el inventario, esos kilos se
        le acreditaban al subproducto que el catálogo tuviera de PRIMERO en su orden de
        presentación. Crear un producto con `orden = 0` le vaciaba a la borona todo lo
        que había recibido gratis en meses, y desde ahí sus ventas legítimas rebotaban
        con "Solo hay 0,00 kg". Guardarlo cierra eso de raíz.

        Sin kilos gratis no hay a quién nombrar y queda en nulo, que significa
        exactamente eso: esta compra no trajo nada encima.
        """
        if not gratis or Decimal(gratis) <= CERO:
            return None
        return _exigir_destino(
            catalogo.quien_recibe_lo_gratis(tipo, propuesto),
            catalogo,
            tipo,
            propuesto,
            de_donde="esos kilos que llegaron gratis",
        )

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

        Y SE MIDE PRODUCTO POR PRODUCTO, no en dos canastas de kilos y de barras.
        Antes se comparaba contra el disponible del queso y contra el de la
        mozzarella, así que bajarle los kilos a una compra de panela se validaba
        contra el queso: con queso en bodega se podía dejar el inventario de la panela
        en negativo, y desde ahí ninguna venta de panela volvía a pasar el control.
        """
        existencias = ExistenciasReventa(self.db, self.ctx)
        # Lo que la factura va a dejar de cada producto, y lo que le quita. Las dos
        # cuentas van POR CLAVE: los kilos de un producto no compensan los de otro.
        nuevas: dict[str, Decimal] = {}
        for data in datos:
            clave = data.get("tipo") or TIPO_QUESO
            cantidad = (
                Decimal(data.get("barras") or CERO)
                if not existencias.catalogo.se_pesa(clave)
                else Decimal(data.get("kilos_netos") or CERO)
            )
            nuevas[clave] = nuevas.get(clave, CERO) + cantidad
        viejas: dict[str, Decimal] = {}
        for fila in devolviendo:
            if fila.estado == ESTADO_ANULADA:
                # Una compra anulada no está sosteniendo ningún inventario, así
                # que quitarla no le quita kilos a nadie.
                continue
            clave = fila.tipo or TIPO_QUESO
            cantidad = (
                Decimal(fila.barras)
                if not existencias.catalogo.se_pesa(clave)
                else Decimal(fila.kilos_netos)
            )
            viejas[clave] = viejas.get(clave, CERO) + cantidad

        for clave in sorted(set(nuevas) | set(viejas), key=lambda c: (c or "")):
            quita = viejas.get(clave, CERO) - nuevas.get(clave, CERO)
            if quita <= CERO:
                continue
            disponible = existencias.disponible(clave)
            if disponible - quita < CERO:
                unidad = existencias.rotulo_de_unidad(clave)
                raise BusinessError(
                    f"No se pueden quitar tantas cantidades de "
                    f"{existencias.nombre(clave)}: de esta compra ya salieron "
                    f"vendidas. Solo quedan {disponible} {unidad} sin vender"
                )

    def escribir_renglones(
        self,
        documento: DocumentoReventa,
        datos: list[dict[str, Any]],
        *,
        hora_de_registro: datetime | None = None,
    ) -> list[CompraQueso]:
        """Escribe los renglones ya calculados y ya validados.

        LA FECHA Y EL PRODUCTOR SE COPIAN DE LA CABECERA, siempre, y ahí está la
        pieza que hace que nada más del módulo tuviera que cambiar: el resumen, la
        cartera, los lotes y el estado de cuenta agrupan por `productor` y filtran
        por `fecha` DE LA FILA. Si el renglón no los llevara, todos ellos tendrían
        que aprender a saltar a la cabecera; llevándolos, no se enteran de que los
        documentos existen.

        `hora_de_registro` ES EL PUESTO DE LA FACTURA EN EL REPARTO, y solo lo manda
        quien REHACE los renglones de una factura que ya existía. Ver el porqué
        completo en `DocumentoReventaService._hora_de_la_factura`.
        """
        filas = []
        for data in datos:
            fila = dict(data)
            fila["documento_id"] = documento.id
            fila["fecha"] = documento.fecha
            fila["productor"] = documento.tercero
            if hora_de_registro is not None:
                fila["created_at"] = hora_de_registro
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
        # LA UNIDAD SALE DEL CATÁLOGO TAMBIÉN POR AQUÍ. Esta puerta no pasa por
        # `preparar_renglones`, así que era la única del módulo que nunca miraba el
        # catálogo: a una compra de un producto POR UNIDAD se le podían meter kilos
        # por PUT y quedaba con kilos > 0 y una clave que la lectura clasifica por
        # unidades. Con la unidad del catálogo, `_calcular` deja en cero lo de la otra
        # unidad y esa fila mestiza no se puede volver a escribir.
        catalogo = self.catalogo
        unidad = catalogo.unidad_de(actual.tipo)
        nombre = catalogo.de(actual.tipo).nombre
        if unidad == UNIDAD_UNIDAD and (data.get("kilos_brutos") or data.get("precio_kilo")):
            raise BusinessError(
                f"Una compra de {nombre} se cuenta por unidades: necesita las "
                f"unidades y el precio de cada una, no kilos."
            )
        if unidad == UNIDAD_KILO and (data.get("barras") or data.get("precio_barra")):
            raise BusinessError(
                f"Una compra de {nombre} se pesa: necesita los kilos y el precio por "
                f"kilo, no unidades."
            )
        data = self._calcular(data, actual, unidad=unidad)
        # A QUIÉN LE ENTRA LO QUE LLEGÓ GRATIS, también por esta puerta. Se recalcula
        # con la cifra que va a quedar guardada —la del payload si vino, y si no la que
        # ya tenía la fila—, porque editar una compra para AGREGARLE kilos gratis tiene
        # que dejarle su destinatario igual que si se hubiera registrado así.
        gratis = data.get("borona_kilos")
        if gratis is None:
            gratis = actual.borona_kilos
        if Decimal(gratis or CERO) > CERO and actual.subproducto_tipo:
            # SI LA FILA YA NOMBRÓ A SU PRODUCTO, SE RESPETA TAL CUAL. La edición no
            # acepta cambiar el destinatario (no está en `CompraQuesoUpdate`), así que
            # esto nunca fue "lo que pidió el usuario": es lo que la fila decidió el
            # día que se registró, y ningún cambio posterior del catálogo lo mueve.
            #
            # Y ES LO QUE IMPIDE QUE EL CATÁLOGO BLOQUEE UNA EDICIÓN: pasar el valor
            # guardado como si fuera una propuesta lo hacía validar otra vez contra el
            # catálogo de HOY, así que corregirle el precio a una compra vieja
            # rebotaba con 422 si su destinatario ya no estaba en la lista. Es el
            # mismo error de siempre —decidir con lo que el catálogo dice hoy sobre
            # kilos que la fila ya nombró—, esta vez en la puerta de editar.
            data["subproducto_tipo"] = actual.subproducto_tipo
        else:
            data["subproducto_tipo"] = self._destinatario_de_lo_gratis(
                catalogo, actual.tipo, gratis, None
            )
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
        # Cada PRODUCTO se mira contra SU inventario: anular una compra de unidades
        # no puede consultar los kilos disponibles (siempre pasaría el control, y las
        # unidades quedarían en negativo), ni al contrario. Y ya no son tres
        # inventarios con sus nombres escritos aquí: es el de SU producto, sea el
        # queso de siempre o uno que el dueño haya agregado (ver `ExistenciasReventa`).
        existencias = ExistenciasReventa(self.db, self.ctx)
        clave = compra.tipo or TIPO_QUESO
        se_pesa = existencias.catalogo.se_pesa(clave)
        cantidad = Decimal(compra.kilos_netos) if se_pesa else Decimal(compra.barras)
        disponible = existencias.disponible(clave)
        if disponible - cantidad < CERO:
            unidad = existencias.rotulo_de_unidad(clave)
            raise BusinessError(
                f"No se puede anular: {existencias.nombre(clave)} de esta compra ya "
                f"se vendió. Solo quedan {disponible} {unidad} sin vender de "
                f"{cantidad} que trajo. Anule primero las ventas que se lo llevaron, "
                f"o corrija la compra en vez de anularla"
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

    @property
    def productos(self) -> ProductoReventaRepository:
        """El catálogo DE ESTA EMPRESA (mismo porqué que en
        `CompraQuesoService.productos`)."""
        return ProductoReventaRepository(self.db, self.ctx.empresa_id)

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

    @property
    def catalogo(self) -> CatalogoReventa:
        """El catálogo resuelto (mismo porqué que en `CompraQuesoService.catalogo`)."""
        return CatalogoReventa(self.db, self.ctx.empresa_id)

    @staticmethod
    def _cantidad_de(fila: VentaQueso, catalogo: CatalogoReventa) -> Decimal:
        """Cuánto tiene apartado esta venta, EN LA UNIDAD DE SU PRODUCTO.

        La unidad sale del catálogo y no de preguntar si el tipo es 'mozzarella': una
        venta de un producto por unidad que no fuera la mozzarella devolvía sus KILOS
        —que en una venta por unidad son cero—, así que devolverle al inventario lo que
        tenía apartado le devolvía nada.
        """
        return (
            Decimal(fila.kilos)
            if catalogo.se_pesa(fila.tipo or TIPO_VENTA_QUESO)
            else Decimal(fila.barras)
        )

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

        Y CADA PRODUCTO SE COMPARA CONTRA EL SUYO, que es el arreglo de los dos
        defectos más caros de este lado. Antes había TRES inventarios con sus nombres
        escritos en el código, y todo producto caía en uno de los tres:

        · lo que se pesaba y no se llamaba 'queso' se comparaba contra el disponible
          del queso, que su venta no bajaba nunca: se compraban 100 kg y se podían
          despachar 100 kg cuantas veces se quisiera;
        · lo que se contaba y no era la mozzarella se comparaba contra el inventario
          de queso EN KILOS, y contra `kilos` de la venta, que en una venta por unidad
          es cero: pasaban 5.000 panelas sin haber comprado una.

        Los disponibles se cargan UNA vez para toda la validación (ver
        `ExistenciasReventa`): si cada renglón volviera a sumar la base, dos renglones
        del mismo producto se compararían cada uno contra el disponible completo.
        """
        existencias = ExistenciasReventa(self.db, self.ctx)
        catalogo = existencias.catalogo
        cache: dict[str, Decimal] = {}

        from app.modules.empresas.models import Empresa
        self.db.execute(
            select(Empresa.id)
            .where(Empresa.id == self.ctx.empresa_id)
            .with_for_update()
        )

        def disponible_de(clave: str) -> Decimal:
            if clave not in cache:
                cache[clave] = existencias.disponible(clave)
            return cache[clave]

        # Lo pedido, POR PRODUCTO y en el orden en que aparecen los renglones: así el
        # mensaje de error habla del primer producto que no alcanza, que es el que
        # el usuario tiene que corregir.
        pedido: dict[str, Decimal] = {}
        renglones_por_tipo: dict[str, int] = {}
        for data in datos:
            tipo = data.get("tipo") or TIPO_VENTA_QUESO
            cantidad = (
                Decimal(data.get("kilos") or CERO)
                if catalogo.se_pesa(tipo)
                else Decimal(data.get("barras") or CERO)
            )
            pedido[tipo] = pedido.get(tipo, CERO) + cantidad
            renglones_por_tipo[tipo] = renglones_por_tipo.get(tipo, 0) + 1

        # Lo que se devuelve al inventario, cada cantidad al inventario DE SU
        # PRODUCTO: devolverle kilos al inventario de otro dejaría al guardia
        # comparando contra una cifra inventada.
        for fila in devolviendo:
            if fila.estado == ESTADO_ANULADA:
                # Una venta anulada no tiene nada apartado: ya se le devolvió.
                continue
            tipo = fila.tipo or TIPO_VENTA_QUESO
            cache[tipo] = disponible_de(tipo) + self._cantidad_de(fila, catalogo)

        for tipo, cantidad in pedido.items():
            disponible = disponible_de(tipo)
            if cantidad > disponible:
                unidad = existencias.rotulo_de_unidad(tipo)
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
                    f"Solo hay {disponible} {unidad} de {existencias.nombre(tipo)} "
                    f"disponibles{detalle}"
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
        clave = "kilos" if self.catalogo.se_pesa(tipo) else "barras"
        self.exigir_cantidades(
            [{"tipo": tipo, clave: cantidad}],
            devolviendo=[actual] if actual is not None else [],
        )

    # ------------------------------------------- renglones de un documento
    # Mismas tres piezas que en las compras y por las mismas razones: calcular
    # todo, validar el conjunto completo, y solo entonces escribir.
    def preparar_renglones(self, renglones: list[Any]) -> list[dict[str, Any]]:
        """La plata de cada renglón de venta, SIN escribir nada.

        La unidad se valida contra el catálogo DE ESTA EMPRESA, igual que en las
        compras y por lo mismo (ver `CompraQuesoService.preparar_renglones`). Aquí
        pesa todavía más: la unidad no solo valida, también decide CÓMO se calcula
        la plata del renglón (barras × precio por barra, o kilos × precio por kilo).
        """
        datos: list[dict[str, Any]] = []
        catalogo = self.catalogo

        for orden, renglon in enumerate(renglones):
            data = _campos_del_renglon(renglon, RenglonVentaCreate)
            tipo = data.get("tipo") or TIPO_VENTA_QUESO
            data["tipo"] = tipo
            unidad = catalogo.unidad_de(tipo)
            nombre = catalogo.de(tipo).nombre

            # Revalidar que los campos enviados coincidan con la unidad del catálogo.
            # El mensaje habla del NOMBRE del producto, que es como el dueño lo llama.
            if unidad == UNIDAD_UNIDAD and (data.get("kilos") or data.get("precio_kilo")):
                raise BusinessError(
                    f"Una venta de {nombre} se cuenta por unidades: necesita las "
                    f"unidades y el precio de cada una, no kilos."
                )
            if unidad == UNIDAD_KILO and (data.get("barras") or data.get("precio_barra")):
                raise BusinessError(
                    f"Una venta de {nombre} se pesa: necesita los kilos y el precio "
                    f"por kilo, no unidades."
                )

            if unidad == UNIDAD_UNIDAD:
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
        self,
        documento: DocumentoReventa,
        datos: list[dict[str, Any]],
        *,
        hora_de_registro: datetime | None = None,
    ) -> list[VentaQueso]:
        """Escribe los renglones ya calculados y ya validados.

        LA FECHA Y EL CLIENTE SE COPIAN DE LA CABECERA, y `hora_de_registro` conserva
        el puesto de la factura en el reparto cuando se rehacen sus renglones: ver el
        porqué largo en `CompraQuesoService.escribir_renglones` y en
        `DocumentoReventaService._hora_de_la_factura`.
        """
        filas = []
        for data in datos:
            fila = dict(data)
            fila["documento_id"] = documento.id
            fila["fecha"] = documento.fecha
            fila["cliente"] = documento.tercero
            if hora_de_registro is not None:
                fila["created_at"] = hora_de_registro
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
        # guardada. Y LA UNIDAD LA MANDA EL CATÁLOGO, no el literal del tipo: esta
        # puerta no pasa por `preparar_renglones`, así que era la que dejaba editar
        # una venta por unidad como si fuera de kilos.
        tipo = actual.tipo
        if not self.catalogo.se_pesa(tipo):
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
    @staticmethod
    def _hora_de_la_factura(renglones: list[Any]) -> datetime | None:
        """LA HORA EN QUE SE REGISTRÓ ESTA FACTURA: la más vieja de sus renglones.

        POR QUÉ ESTO ES PLATA Y NO UN DETALLE TÉCNICO. La llave de orden del reparto
        FIFO es (fecha, hora de registro, renglón), y de ese orden sale A QUÉ PRODUCTOR
        se le consumen los kilos de una venta, con su costo y su ganancia. Rehacer los
        renglones de una factura los BORRA y los CREA de nuevo, así que nacían con la
        hora de HOY y la factura se iba al FINAL del orden de su día.

        Lo que eso significaba en la práctica: se le corrige un dato a la factura de la
        mañana —hasta mandando los renglones EXACTAMENTE IGUALES, mismos kilos y mismo
        precio— y la venta de la tarde dejaba de consumir los kilos baratos del primer
        productor y empezaba por los caros del segundo. La ganancia cambiaba de dueño y
        el costo del inventario que queda en bodega también, sin que ninguna cifra del
        negocio hubiera cambiado. En Postgres era seguro que pasara: `now()` es la hora
        de la transacción, así que la fila rehecha queda de última siempre.

        LA DECISIÓN: el puesto en el reparto es DE LA FACTURA, no de sus renglones.
        Corregirle un renglón no la vuelve a registrar; sigue siendo la compra de esa
        mañana. Así que los renglones nuevos heredan la hora de registro de la factura,
        que es la MÁS VIEJA de las de sus renglones —la de cuando se escribió el
        primero— y el `orden` del renglón sigue desempatando dentro de ella, que es lo
        que ya hace la llave.

        Se toma la más vieja y no la del primer renglón por si acaso: si a una factura
        se le agregó un renglón un día después, su hora es más nueva, y lo que hay que
        conservar es cuándo entró la factura al negocio. Y se miran TODOS los renglones
        (también los anulados): un renglón anulado no suma plata, pero es prueba de
        cuándo se registró esta factura, y es el único que queda si se anularon todos.

        Devuelve `None` cuando no hay ningún renglón de dónde sacarla; ahí no hay nada
        que conservar y la hora nueva es la correcta.
        """
        horas = [r.created_at for r in renglones if r.created_at is not None]
        return min(horas) if horas else None

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
            # EL PUESTO EN EL REPARTO SE CONSERVA: los renglones nuevos nacen con la
            # hora de registro que tenía la factura, no con la de hoy.
            hora = self._hora_de_la_factura(renglones)
            for renglon in vivos:
                servicio.eliminar(renglon.id, cuidar_cabecera=False)
            servicio.escribir_renglones(documento, datos, hora_de_registro=hora)
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
    """Pasar kilos de un producto a un subproducto suyo (o perderlos como merma).

    EL AJUSTE NOMBRA SUS DOS PRODUCTOS Y SE RESUELVE AQUÍ, UNA SOLA VEZ. Es toda la
    diferencia con lo que había: antes la fila no decía de qué producto salían los
    kilos ni a cuál entraban, así que CADA LECTURA lo adivinaba mirando el ORDEN del
    catálogo —un campo de presentación que la API deja cambiar con un PUT—, y
    reordenar la lista le transfería a otro producto meses de historia de la borona
    (el detalle, con cifras, está en el docstring de `ConversionBorona`).

    Ahora se decide al ESCRIBIR, contra el catálogo del momento, y queda guardado en
    la fila. De ahí sale la garantía que el dueño necesita: crear, reordenar,
    renombrar o desactivar productos no le mueve un kilo ni un peso a lo ya anotado.

    QUÉ PASA SI LA PANTALLA NO MANDA LOS PRODUCTOS (que es lo que hace hoy). El origen
    es el producto de siempre —'queso', la constante, no "el primero de la lista"— y
    el destino lo resuelve el catálogo sin mirar el orden: si ese producto tiene UN
    solo subproducto que se pese, es ese, porque no hay a quién más darle; si no, la
    clave de siempre ('borona'), que es lo que estos ajustes han significado desde que
    existen (ver `CatalogoReventa._quien_recibe`).

    Y REGISTRAR SIEMPRE GANA: UN PROBLEMA DEL CATÁLOGO NO PARA EL AJUSTE DEL DÍA. Está
    medido contra el código desplegado: con una empresa recién creada —su catálogo no
    se siembra hasta el despliegue siguiente—, con la borona fuera de la lista o
    descolgada de su padre, este ajuste rebotaba con 422 donde antes respondía 201, y
    nombrar el origen y el destino a mano tampoco salvaba al dueño. Un ajuste es él
    anotando que unos kilos ya dejaron de ser una cosa y pasaron a ser otra: eso ya
    pasó en la bodega, y una lista de productos no puede decirle que no.

    LOS RECHAZOS QUE QUEDAN SON LOS QUE NECESITAN QUE UNA PERSONA DECIDA, y ninguno es
    un problema de la lista: sacar kilos de un producto que se cuenta por unidades (una
    barra no se desmenuza), convertir un producto en sí mismo, mandarle los kilos a un
    producto que se cuenta o a un nombre que nadie conoce, y no tener esos kilos en la
    bodega. Todos con un mensaje que dice la salida.
    """

    repository_cls = ConversionBoronaRepository
    modulo = "reventa"

    def crear(self, payload: Any) -> ConversionBorona:
        data = (
            payload.model_dump(exclude_unset=True)
            if not isinstance(payload, dict)
            else dict(payload)
        )
        existencias = ExistenciasReventa(self.db, self.ctx)
        catalogo = existencias.catalogo

        # ------------------------------------------------- de qué producto salen
        # NO SE LE PREGUNTA A LA LISTA SI EL ORIGEN ESTÁ EN ELLA, se le pregunta A LA
        # BODEGA si esos kilos existen (más abajo), que es la pregunta que de verdad
        # protege la plata. Una clave que no está en el catálogo se pesa y es su propio
        # producto —la regla del final del docstring de `catalogo.py`—, así que sus
        # kilos están contados. Exigir el catálogo dejaba a una empresa recién creada
        # sin poder registrar el ajuste de todos los días hasta el despliegue siguiente.
        origen = data.get("producto_origen") or TIPO_QUESO
        producto = catalogo.de(origen)
        if not producto.se_pesa:
            raise BusinessError(
                f"{producto.nombre} se cuenta por unidades: una unidad no se desmenuza "
                "ni pierde peso, así que no admite estos ajustes"
            )
        data["producto_origen"] = producto.clave

        # ------------------------------------------------- a qué producto le entran
        if data.get("destino") == DESTINO_MERMA:
            # La merma es pérdida sin valor: no lleva precio y no le entra a nadie.
            data["precio_kilo"] = CERO
            data["producto_destino"] = None
        else:
            propuesto = data.get("producto_destino")
            destino = _exigir_destino(
                catalogo.subproducto_que_recibe(producto.clave, propuesto),
                catalogo,
                producto.clave,
                propuesto,
                de_donde="estos kilos",
            )
            if destino == producto.clave:
                # UN PRODUCTO NO SE CONVIERTE EN SÍ MISMO. La fila diría que los mismos
                # kilos salieron y entraron al mismo inventario, y el desglose sacaría
                # dos renglones de la misma clave hablando del mismo movimiento. Es un
                # error de dedo, no un ajuste, y por eso se dice en vez de guardarlo.
                raise BusinessError(
                    f"Los kilos que salen de {producto.nombre} tienen que entrarle a "
                    f"OTRO producto: {producto.nombre} no se convierte en sí mismo. "
                    "Diga a cuál le entran, o regístrelo como merma si se perdieron"
                )
            data["producto_destino"] = destino

        disponible = existencias.disponible(producto.clave)
        if Decimal(data["kilos"]) > disponible:
            raise BusinessError(
                f"Solo hay {disponible} kg de {producto.nombre} disponibles"
            )
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
        self.catalogo = CatalogoReventa(db, ctx.empresa_id)

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
        etiqueta: str | None = None,
        nota: str | None = None,
        sin_costo_es_de_antes: bool = False,
    ) -> GananciaProducto:
        """`kilos` = kilos del lote comprado que fueron a este destino.
        `kilos_vendidos` = kilos realmente vendidos (solo difiere en la borona).

        `etiqueta` y `nota` llegan armados desde `_etiqueta_y_nota` cuando la fila es
        de un producto del catálogo; sin ellos se usan los textos de siempre, que es
        como la llaman los pocos sitios que arman una fila suelta.
        """
        vendidos = kilos if kilos_vendidos is None else kilos_vendidos
        etiqueta = etiqueta if etiqueta is not None else ETIQUETAS_PRODUCTO[producto]
        nota = nota if nota is not None else NOTAS_PRODUCTO[producto]
        # Sin costo pero con kilos = la compra quedó fuera del período; decirlo
        # en vez de mostrar $0 de pérdida como si no hubiera costado nada.
        if (
            kilos > CERO
            and costo == CERO
            and (sin_costo_es_de_antes or producto == "merma")
        ):
            nota = NOTA_SIN_COSTO
        return GananciaProducto(
            producto=producto,
            etiqueta=etiqueta,
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
        etiqueta: str | None = None,
        nota: str | None = None,
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
            etiqueta=etiqueta if etiqueta is not None else ETIQUETAS_PRODUCTO[producto],
            nota=nota if nota is not None else NOTAS_PRODUCTO[producto],
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

    def _movimientos_del_periodo(
        self, desde: date, hasta: date
    ) -> tuple[dict[str, _MovimientoDeProducto], dict[str, _MovimientoDeProducto]]:
        """(lo que se pesó, lo que se contó) del período, ABIERTO POR PRODUCTO.

        Son DOS diccionarios y no uno con la unidad adentro, y esa es la garantía que
        importa: ninguna cifra de kilos puede terminar sumada con una de unidades,
        porque viven en estructuras distintas de punta a punta. La suma de todo el
        primero es exactamente lo que devuelven las consultas de kilos del período, y
        la del segundo lo que devuelven las de unidades: es la misma plata de siempre,
        abierta por producto.

        UN PRODUCTO PUEDE APARECER EN LOS DOS, y no es un caso hipotético que sobre:
        una fila de un producto por unidad a la que alguien le hubiera metido kilos
        —lo que se podía hacer por PUT antes de que esa puerta mirara el catálogo— se
        clasifica por lo que la fila TRAE (ver `se_mide_en_unidades`), así que caería
        en el primer diccionario. Que pueda estar en los dos es lo que hace que su
        plata salga en una fila del desglose en vez de perderse.
        """
        kilos: dict[str, _MovimientoDeProducto] = {}
        unidades: dict[str, _MovimientoDeProducto] = {}

        for clave, cantidad, plata, gratis in self.compras.totales_periodo_por_tipo(
            desde, hasta
        ):
            kilos[clave] = _MovimientoDeProducto(
                comprado=cantidad, comprado_plata=plata, gratis=gratis
            )
        for clave, cantidad, plata, gastos in self.ventas.totales_periodo_por_tipo(
            desde, hasta
        ):
            actual = kilos.get(clave, _MovimientoDeProducto())
            kilos[clave] = replace(
                actual, vendido=cantidad, vendido_plata=plata, gastos=gastos
            )

        for clave, cantidad, plata in self.compras.totales_periodo_barras_por_tipo(
            desde, hasta
        ):
            unidades[clave] = _MovimientoDeProducto(
                comprado=cantidad, comprado_plata=plata
            )
        for clave, cantidad, plata, gastos in self.ventas.totales_periodo_barras_por_tipo(
            desde, hasta
        ):
            actual = unidades.get(clave, _MovimientoDeProducto())
            unidades[clave] = replace(
                actual, vendido=cantidad, vendido_plata=plata, gastos=gastos
            )
        return kilos, unidades

    def _filas_por_producto(
        self,
        *,
        kilos: dict[str, "_MovimientoDeProducto"],
        unidades: dict[str, "_MovimientoDeProducto"],
        ajustes: list[tuple[str, str | None, Decimal]],
        gratis: dict[str, Decimal],
    ) -> list[GananciaProducto]:
        """EL DESGLOSE DE LA GANANCIA, UNA FILA POR PRODUCTO Y ARMADO SOLO.

        QUÉ ERA ESTO ANTES Y POR QUÉ HABÍA QUE CAMBIARLO. Eran cuatro filas fijas
        —queso, borona, merma y el residuo— más dos de mozzarella, con los nombres
        escritos aquí. El costo se repartía entre "los kilos comprados" y "los kilos
        vendidos como queso", que en realidad eran los kilos de TODO lo que se pesara.
        Con un segundo producto por kilo en el catálogo eso se rompía por los dos
        lados: la venta de la panela no tenía fila, así que su plata caía en la de la
        borona (que se sacaba por diferencia), y su costo se lo cargaba al pozo del
        queso, que quedaba diciendo que tenía en bodega un queso que no compró.

        LA REGLA DE AHORA, Y ES UNA SOLA: cada GRUPO DE COSTEO —un producto raíz con
        sus subproductos, ver `GrupoDeCosteo`— tiene su propio pozo, que es la plata
        de SUS compras, y ese pozo se reparte entre SUS destinos:

            · lo que se vendió de cada miembro del grupo;
            · lo que se pasó al subproducto (los kilos convertidos, no los vendidos:
              la borona vendida sale del inventario de borona, que además recibe la
              que llega gratis con el lote — costear los vendidos inventaba costos
              que nunca se pagaron);
            · la merma;
            · y el RESIDUO, que es lo que quedó en bodega (o, en negativo, lo que
              salió de un inventario de antes del período).

        Y LA CUENTA CIERRA POR CONSTRUCCIÓN, no por un ajuste al final: las cantidades
        de los destinos suman EXACTAMENTE la cantidad comprada del grupo —el residuo
        es justamente lo que falta para eso—, así que repartir el pozo entre ellos con
        el reparto por resto mayor (`repartir_al_resto_mayor`) da una suma de costos
        que es EXACTAMENTE la plata comprada del grupo. Sumando los grupos, la columna
        del costo suma exacto las compras del período, que es lo que el dueño verifica
        con la calculadora.

        POR QUÉ EL RESTO MAYOR Y NO "EL ÚLTIMO SE LLEVA LA DIFERENCIA". Porque con las
        filas armándose solas ya no hay un "último" que signifique nada: la lista
        depende de cuántos productos tenga el catálogo. Con el resto mayor cada fila
        queda a lo sumo un centavo de su propia multiplicación —que es la que el dueño
        rehace a mano— y los centavos que faltan caen donde de verdad se generaron.
        Es la misma implementación que reparte los fletes de las liquidaciones
        (`app/common/dinero.py`), para que dos pantallas del sistema no repartan los
        mismos centavos de dos formas distintas.

        ESTO MUEVE HASTA DOS CENTAVOS DE FILA A FILA CONTRA EL CÓDIGO ANTERIOR, ES A
        PROPÓSITO Y NO SE DEBE "ARREGLAR" DE VUELTA. Está medido: 625 cifras en 40
        escenarios, y la diferencia máxima entre una fila de antes y la misma fila de
        hoy es de $0,02. Lo que NO se movió es nada de lo que el dueño cuadra:

          · los encabezados (`total_compras`, `total_ventas`, `total_gastos`,
            `ganancia_estimada`) dan exactamente lo mismo;
          · todos los desgloses siguen sumando EXACTO su cifra grande;
          · los PDF salen idénticos.

        Y lo que se ganó es lo que hacía falta: cada fila queda reproducible con su
        propia multiplicación. Con "el último se lleva la diferencia" salían renglones
        de "0 kg, $0,01" que el dueño no podía explicar con ninguna cuenta, porque esos
        centavos no eran de esa fila: eran los sobrantes de todas las demás.

        LAS DOS UNIDADES NUNCA SE MEZCLAN. Un grupo que se pesa reparte kilos con
        kilos y sale con filas de kilos; uno que se cuenta reparte unidades con
        unidades y sale con filas de unidades. Los pesos sí se suman entre todas,
        porque los pesos son pesos.

        CUÁNDO APARECE UN GRUPO. Cuando tuvo movimiento en el período, y siempre en el
        caso del grupo del producto DE SIEMPRE: sus filas son donde se reporta la merma
        y de dónde salió lo que se movió de un inventario anterior, y eso puede existir
        sin una sola compra en el período. Es exactamente lo que se veía antes (las
        cuatro filas de kilos salían siempre, las de mozzarella solo si hubo barras).

        Y ESE "DE SIEMPRE" ES UNA CONSTANTE Y NO UNA CONSULTA AL CATÁLOGO. Antes era
        "el grupo de la pareja de ajustes", o sea el primer subproducto que se pesara
        EN EL ORDEN del catálogo: crear un producto y ponerlo de primero le quitaba al
        queso sus cuatro renglones de siempre y se los estrenaba a otro. Igual que
        `CLAVES_DE_RESIDUO_HISTORICAS`, esto es una decisión de RÓTULOS —cuáles filas
        se imprimen aunque vayan en cero— y ninguna cifra se decide aquí.
        """
        vacio = _MovimientoDeProducto()
        # Los ajustes del período, abiertos POR EL PRODUCTO DEL QUE SALIERON. Cada uno
        # dice a cuál le entró (o nulo si fue merma), y de ahí sale a qué fila del
        # desglose van esos kilos, sin preguntarle nada al orden del catálogo.
        por_origen: dict[str, dict[str | None, Decimal]] = {}
        for origen, destino, cantidad in ajustes:
            hacia = por_origen.setdefault(origen, {})
            hacia[destino] = hacia.get(destino, CERO) + cantidad

        filas: list[GananciaProducto] = []
        # LAS CLAVES DE LO QUE LLEGÓ GRATIS ENTRAN TAMBIÉN, y no es un adorno: son
        # productos que recibieron mercancía de verdad sin tener una sola compra ni una
        # sola venta a nombre propio. Sin ellas, un producto que solo hubiera recibido
        # kilos gratis y que NO estuviera en el catálogo (una base vieja, una fila que
        # nombra un producto que ya no está) se quedaba sin grupo y sin renglón: el
        # desglose no lo mencionaba mientras las existencias de la MISMA respuesta
        # reportaban sus 25,36 kg. Es el mismo olvido de `movimientos()`, en la puerta
        # de leer: la fila ya nombró a su producto en `subproducto_tipo`.
        for grupo in self.catalogo.grupos([*kilos, *unidades, *por_origen, *gratis]):
            for en_kilos, movimientos in ((True, kilos), (False, unidades)):
                mios = {c: movimientos.get(c, vacio) for c in grupo.claves}
                # Los ajustes son kilos de punta a punta (ver `ConversionBorona`), así
                # que solo entran en la vuelta de lo que se pesa.
                suyos = (
                    {c: por_origen[c] for c in grupo.claves if c in por_origen}
                    if en_kilos
                    else {}
                )
                # Recibir kilos gratis ES movimiento del producto que los recibió, por
                # lo mismo de arriba. Solo cuenta en la vuelta de los kilos: lo que
                # llega gratis se pesa.
                recibio_gratis = en_kilos and any(gratis.get(c) for c in grupo.claves)
                hubo_movimiento = bool(suyos) or recibio_gratis or any(
                    m.comprado or m.vendido or m.comprado_plata or m.vendido_plata
                    for m in mios.values()
                )
                imprime_siempre = en_kilos and grupo.clave == TIPO_QUESO
                if not hubo_movimiento and not imprime_siempre:
                    continue
                filas += self._filas_de_un_grupo(
                    grupo,
                    mios,
                    en_kilos=en_kilos,
                    imprime_siempre=imprime_siempre,
                    ajustes=suyos,
                    gratis=gratis if en_kilos else {},
                )
        return filas

    def _filas_de_un_grupo(
        self,
        grupo: GrupoDeCosteo,
        movimientos: dict[str, "_MovimientoDeProducto"],
        *,
        en_kilos: bool,
        imprime_siempre: bool,
        ajustes: dict[str, dict[str | None, Decimal]],
        gratis: dict[str, Decimal],
    ) -> list[GananciaProducto]:
        """Las filas de UN grupo: una por miembro, las mermas y los residuos.

        UN POZO POR PRODUCTO QUE HAYA COMPRADO, Y NO UNO SOLO POR GRUPO. Esta es la
        pieza que arregla el segundo defecto: comprar borona directamente. El pozo del
        grupo era la plata de TODAS sus compras, y sus destinos eran "lo vendido de la
        raíz" y "lo convertido al subproducto"; una compra hecha DIRECTAMENTE al
        subproducto entraba al pozo pero no tenía ningún destino que la consumiera, así
        que los $50.000 de esa borona se iban enteros a la fila "Aún en inventario"
        —como si fueran queso que nadie compró— y la borona vendida salía con ganancia
        PURA de $100.000. El panel de lotes, que sí le lleva una cola a cada producto,
        decía otra cosa: dos pantallas del mismo sistema con dos costos de la misma
        venta.

        La regla de ahora, y son dos frases:

        · LO QUE LLEGA GRATIS CON SU PADRE HEREDA EL COSTO DEL PADRE. Eso es lo que
          significa la marca del catálogo, y por eso el subproducto consume del pozo de
          su padre EXACTAMENTE los kilos que se le convirtieron —no los que vendió: lo
          vendido puede salir de lo que llegó gratis o de conversiones de otro período,
          y eso no se pagó en este—.
        · LO QUE SE COMPRA DIRECTAMENTE TIENE SU PROPIO COSTO. Las compras de un
          subproducto arman SU pozo, con SU precio por kilo, y de ahí sale el costo de
          lo que se venda de él y de lo que quede en bodega. Las dos cosas conviven en
          el mismo producto y en el mismo período.

        Y LA CUENTA SIGUE CERRANDO POR CONSTRUCCIÓN: cada pozo reparte su plata entre
        sus destinos, el residuo es justamente lo que falta para que las cantidades
        sumen lo comprado de ESE producto, y la suma de todos los pozos del grupo es la
        plata comprada del grupo. Sumando los grupos, la columna del costo suma exacto
        las compras del período, que es lo que el dueño verifica con la calculadora.
        """
        vacio = _MovimientoDeProducto()
        # Cuántos kilos le ENTRARON a cada miembro por ajustes dentro del grupo. Son
        # kilos que ya vienen costeados del pozo de quien los soltó, así que no
        # consumen el pozo propio del que los recibe.
        entra_por_ajustes: dict[str, Decimal] = {}
        for hacia in ajustes.values():
            for destino, cantidad in hacia.items():
                if destino:
                    entra_por_ajustes[destino] = (
                        entra_por_ajustes.get(destino, CERO) + cantidad
                    )

        def consumo_de_lo_vendido(producto: Any, mov: "_MovimientoDeProducto") -> Decimal:
            """De lo VENDIDO de un producto, cuánto sale de SUS PROPIAS COMPRAS.

            LA CUENTA NO ESTÁ AQUÍ: está en `kilos_que_salen_de_lo_pagado`, en
            `lotes.py`, y es LA MISMA que usa el panel de lotes para servir esa venta de
            la cola de inventario. Lo que se hace aquí es juntar los dos orígenes que
            este producto tuvo sin pagar —lo que le llegó GRATIS con la compra de su
            padre (cuesta cero) y lo que le entró CONVERTIDO desde él (ya se costeó
            contra el pozo del padre, en la fila de este mismo producto)— y preguntarle
            a esa función. Cargarle esos kilos otra vez al pozo propio los cobraría dos
            veces.

            LAS DOS PANTALLAS TIENEN QUE DECIR EL MISMO COSTO DE LA MISMA VENTA, y por
            eso la cuenta se escribe una sola vez: cuando cada una ordenaba a su manera,
            el desglose cobraba $225.000 por un despacho de borona que el panel de lotes
            valoraba en $35.000. El detalle, con las cifras, está en el docstring de esa
            función.

            Para un producto que no recibe nada gratis ni convertido —o sea todos los
            que se compran y se venden y ya— esto es exactamente `mov.vendido`, que es
            lo que este renglón ha hecho siempre.
            """
            sin_pagar = gratis.get(producto.clave, CERO) + entra_por_ajustes.get(
                producto.clave, CERO
            )
            return kilos_que_salen_de_lo_pagado(mov.vendido, sin_pagar)

        # ------------------------------------------------------------ las filas
        # Se van creando en el orden en que tienen que salir en pantalla: primero los
        # miembros del grupo (la raíz y después sus subproductos, en el orden del
        # catálogo), después las mermas y por último los residuos.
        orden: list[str] = []
        acumulado: dict[str, dict[str, Any]] = {}

        def renglon(clave_fila: str, producto: Any, papel: str = "vendido") -> dict:
            if clave_fila not in acumulado:
                orden.append(clave_fila)
                acumulado[clave_fila] = {
                    "producto": producto,
                    "papel": papel,
                    "cantidad": CERO,
                    "vendida": CERO,
                    "ingreso": CERO,
                    "gastos": CERO,
                    "costo": CERO,
                    # De qué pozos salió su costo. Con uno solo, la columna "costo por
                    # kilo" es la de ese pozo (que es la que el dueño cruza con el
                    # recibo del productor); con dos, la única cifra honesta es la de
                    # la propia fila.
                    "unitarios": [],
                }
            return acumulado[clave_fila]

        for producto in grupo.miembros:
            mov = movimientos.get(producto.clave, vacio)
            fila = renglon(_clave_de_fila(producto.clave), producto)
            fila["vendida"] += mov.vendido
            fila["ingreso"] += mov.vendido_plata
            fila["gastos"] += mov.gastos

        # ------------------------------------------------------------- los pozos
        for producto in grupo.miembros:
            mov = movimientos.get(producto.clave, vacio)
            es_raiz = producto.clave == grupo.raiz.clave
            # El pozo de la RAÍZ existe siempre —es donde cae el residuo del grupo y
            # es la fila que el dueño ya conoce—; el de un subproducto solo si de
            # verdad se le compró algo.
            if not es_raiz and not (mov.comprado or mov.comprado_plata):
                continue

            # (clave de la fila, cantidad que se lleva de este pozo)
            destinos: list[tuple[str, Decimal]] = []
            # Lo vendido DE ESTE producto sale de SUS compras, descontando lo que no
            # se pagó (ver `consumo_de_lo_vendido`).
            destinos.append(
                (_clave_de_fila(producto.clave), consumo_de_lo_vendido(producto, mov))
            )

            # Lo que salió de él por ajustes: a un subproducto suyo o a la merma.
            for destino, cantidad in sorted(
                ajustes.get(producto.clave, {}).items(),
                key=lambda par: (par[0] is None, par[0] or ""),
            ):
                if destino is None:
                    clave_merma = _clave_de_merma(producto.clave)
                    renglon(clave_merma, producto, papel="merma")
                    destinos.append((clave_merma, cantidad))
                elif destino in grupo.claves:
                    destinos.append((_clave_de_fila(destino), cantidad))
                # UN DESTINO QUE YA NO ES SUBPRODUCTO DE ESTE (solo puede quedar así
                # tocando la base a mano: el catálogo no deja re-colgar un producto que
                # tenga ajustes) se deja SIN consumir el pozo, así que esos kilos se
                # quedan en el residuo. Es lo más pequeño que se puede decir sin
                # mentir: la plata no se pierde y no se le acredita a nadie que no sea.

            # LA FILA DE LA MERMA SALE SIEMPRE en el grupo del producto de siempre,
            # aunque el período no tenga merma. Es un renglón que el dueño busca con el
            # dedo —"¿cuánto se perdió este mes?"— y un cero explícito es una
            # respuesta; que la fila desaparezca lo deja sin saber si no hubo merma o
            # si el sistema no la contó.
            if imprime_siempre and es_raiz:
                clave_merma = _clave_de_merma(producto.clave)
                if clave_merma not in acumulado:
                    renglon(clave_merma, producto, papel="merma")
                    destinos.append((clave_merma, CERO))

            # EL RESIDUO ES LO QUE FALTA PARA QUE LOS DESTINOS SUMEN LO COMPRADO DE
            # ESTE PRODUCTO, así que las cantidades cierran exacto por definición y el
            # reparto del pozo también.
            residuo = mov.comprado - sum((c for _, c in destinos), CERO)
            clave_sobra, clave_falta = _claves_de_residuo(producto.clave)
            papel_residuo = "pendiente" if residuo >= CERO else "anterior"
            clave_residuo = clave_sobra if residuo >= CERO else clave_falta
            renglon(clave_residuo, producto, papel=papel_residuo)
            # El papel se vuelve a fijar: la MISMA fila puede haberse creado como
            # 'pendiente' en una vuelta anterior y ahora ser 'anterior' (no puede
            # pasar hoy —cada pozo tiene su propia clave de residuo— pero si algún día
            # dos pozos compartieran una, el rótulo tiene que decir lo que la fila es).
            acumulado[clave_residuo]["papel"] = papel_residuo
            destinos.append((clave_residuo, residuo))

            # El costo de cada destino: su parte del pozo, repartida al resto mayor
            # sobre los valores EXACTOS (sin redondear antes de multiplicar, que es lo
            # que desviaba la columna unos pesos de la cifra grande).
            exactos = [
                (
                    indice,
                    (cantidad * mov.comprado_plata / mov.comprado)
                    if mov.comprado
                    else CERO,
                )
                for indice, (_, cantidad) in enumerate(destinos)
            ]
            costos = repartir_al_resto_mayor(exactos, mov.comprado_plata)
            # El costo unitario del pozo, para la columna que el dueño cruza a mano con
            # lo que le pagó al productor. Es el de ESTE producto y no el del período
            # entero: meterle la plata de otro daría un precio por kilo que no está en
            # ningún recibo.
            unitario = (
                (mov.comprado_plata / mov.comprado).quantize(DOS_DECIMALES)
                if mov.comprado
                else CERO
            )
            for indice, (clave_fila, cantidad) in enumerate(destinos):
                fila = acumulado[clave_fila]
                fila["cantidad"] += cantidad
                fila["costo"] += costos[indice]
                if unitario not in fila["unitarios"]:
                    fila["unitarios"].append(unitario)

        # El costo por unidad del pozo de la raíz es el que contesta por las filas que
        # no consumieron de ningún pozo (un subproducto que solo vendió lo que le llegó
        # gratis): es lo que este renglón devolvía antes y lo que el dueño ya conoce.
        unitario_de_la_raiz = (
            acumulado.get(_clave_de_fila(grupo.raiz.clave), {}).get("unitarios") or [CERO]
        )[0]

        # ------------------------------------------------------- armar la respuesta
        salida: list[GananciaProducto] = []
        for clave_fila in orden:
            fila = acumulado[clave_fila]
            papel = fila["papel"]
            es_residuo = papel in ("pendiente", "anterior")
            producto = fila["producto"]
            nombre = producto.nombre if producto is not None else ""
            etiqueta, nota = _etiqueta_y_nota(
                clave_fila, nombre, papel=papel, se_pesa=en_kilos
            )
            unitarios = fila["unitarios"]
            if not unitarios:
                costo_unitario = unitario_de_la_raiz
            elif len(unitarios) == 1:
                costo_unitario = unitarios[0]
            else:
                # La fila se sirvió de dos pozos con precios distintos (lo convertido
                # del padre y lo que se le compró a él directo). Ningún precio de
                # recibo explica esa mezcla: lo único cierto es lo que costó ESTA fila.
                costo_unitario = (
                    (fila["costo"] / fila["cantidad"]).quantize(DOS_DECIMALES)
                    if fila["cantidad"]
                    else CERO
                )
            cantidad = abs(fila["cantidad"]) if es_residuo else fila["cantidad"]
            if en_kilos:
                salida.append(
                    self._fila_producto(
                        clave_fila,
                        cantidad,
                        fila["ingreso"],
                        fila["costo"],
                        fila["gastos"],
                        costo_unitario,
                        # EN LAS FILAS QUE NO SE VENDEN (la merma y el residuo) los
                        # "vendidos" quedan iguales a la cantidad, que es lo que ya
                        # devolvía el resumen. No es un descuido heredado: la pantalla
                        # muestra la nota "vendidos: X" SOLO cuando difiere de la
                        # cantidad, así que dejarlos en cero le estrenaría al dueño una
                        # nota que dice "vendidos: 0" en dos filas que por definición
                        # no se vendieron. Igualarlos es lo que la mantiene callada.
                        kilos_vendidos=(
                            None if es_residuo or papel == "merma" else fila["vendida"]
                        ),
                        etiqueta=etiqueta,
                        nota=nota,
                        # Cantidad sin costo = la compra cayó fuera del período, y hay
                        # que decirlo en vez de mostrar $0 de pérdida. Aplica al
                        # residuo que sobró y a la merma, que es de donde salían los
                        # dos casos reales.
                        sin_costo_es_de_antes=(
                            papel == "merma" or (es_residuo and papel == "pendiente")
                        ),
                    )
                )
            else:
                salida.append(
                    self._fila_barras(
                        clave_fila,
                        cantidad,
                        fila["ingreso"],
                        fila["costo"],
                        fila["gastos"],
                        costo_unitario,
                        # El residuo no se vendió: sin unidades vendidas no hay precio
                        # de venta que mostrar (si no, saldría $0 "por unidad" como si
                        # se hubiera regalado).
                        barras_vendidas=CERO if es_residuo else fila["vendida"],
                        etiqueta=etiqueta,
                        nota=nota,
                    )
                )
        return salida
    @classmethod
    def _cuadrar_desglose(
        cls,
        filas: list[GananciaProducto],
        *,
        total_compras: Decimal,
        total_ventas: Decimal,
        total_gastos: Decimal,
    ) -> list[GananciaProducto]:
        """LA REGLA DE ORO, EXIGIDA AQUÍ Y NO CONFIADA A LAS RAMAS DE ARRIBA: la
        suma de las filas del desglose es EXACTAMENTE el encabezado.

        Las tres cifras que entran son las del encabezado, las mismas que el dueño
        ve en las tarjetas. Si lo que quedó repartido en las filas no da esa cifra,
        la diferencia sale en SU PROPIA FILA en vez de desaparecer.

        POR QUÉ HACE FALTA UNA RED SI ARRIBA YA CUADRA POR CONSTRUCCIÓN. Porque ya
        no cuadró una vez, y por eso mismo: las filas de kilos suman su plata porque
        el residuo se lleva la diferencia, y las de barras porque el residuo de
        barras hace lo mismo... pero las de barras solo se IMPRIMEN si hubo barras.
        El día que una compra quedó clasificada "de unidades" con cero barras (por
        la fuga entre empresas, o editándola por PUT), su plata se sumó al total de
        la mozzarella y no cayó en ninguna fila: el encabezado decía $200.000 y el
        desglose sumaba $0. El dueño suma esa columna a mano con calculadora, así que
        eso no es un detalle de presentación: es la cifra en la que él confía.

        Hoy esa fuga está cerrada de dos maneras (la clasificación filtra por empresa
        y exige que la fila traiga unidades, ver `se_mide_en_unidades`), así que esta
        fila NO PUEDE APARECER con los datos que el sistema sabe escribir. Se queda
        igual, porque "no puede aparecer" es exactamente lo que se creía la vez
        pasada: mientras esto esté aquí, un desglose que no suma el encabezado es
        IMPOSIBLE y no solo improbable, venga la plata de donde venga.

        La fila va SIN cantidad (ni kilos ni barras): su asunto son los pesos, y
        ponerle una cantidad inventada sería el error que se está tapando. Va de
        última en la lista, que es donde la pantalla la muestra.
        """
        costo = total_compras - sum((f.costo for f in filas), CERO)
        ingreso = total_ventas - sum((f.ingreso for f in filas), CERO)
        gastos = total_gastos - sum((f.gastos for f in filas), CERO)
        if costo or ingreso or gastos:
            filas = [
                *filas,
                cls._fila_producto("sin_producto", CERO, ingreso, costo, gastos, CERO),
            ]
        return filas

    def _filas_por_productor(
        self,
        desde: date,
        hasta: date,
        *,
        grupos: list[GrupoDeCosteo],
        kilos: dict[str, _MovimientoDeProducto],
        unidades: dict[str, _MovimientoDeProducto],
        valor_realizado_kilo: Decimal,
        valor_realizado_barra: Decimal = CERO,
        clave_de_las_unidades: str | None = None,
    ) -> list[GananciaProductor]:
        """Ganancia ESTIMADA por productor (la UI debe decir que es estimación).

        LA CUENTA, Y EL PORQUÉ DE QUE SEA POR GRUPO. A cada productor se le acredita
        la parte del valor neto que dejaron las ventas —ventas menos gastos— que le
        corresponde a las cantidades que él vendió, y se le descuenta lo que se le
        pagó. Quien vendió más barato dejó más margen. El divisor son las cantidades
        COMPRADAS y no las vendidas: así la suma de la columna cuadra con la ganancia
        neta del período y el ranking no puede contradecir la tarjeta de arriba.

        EL REPARTO SE HACE UNA VEZ POR GRUPO DE COSTEO, y es la única forma de que
        cuadre. El neto que dejaron las ventas de un grupo se reparte entre las
        cantidades compradas DE ESE GRUPO, en SU unidad. Si se repartiera todo el neto
        entre los kilos, a un productor que solo vendió unidades le saldría ganancia
        cero y su plata se les acreditaría a los de kilos: el ranking diría que el
        mejor negocio lo hizo alguien que no vendió una sola barra. Las partes se SUMAN
        en `ganancia_estimada` porque son pesos, y la columna sigue sumando la
        ganancia del período:

            Σ_grupos (neto del grupo − comprado del grupo)
            = total_ventas − total_gastos − total_compras = ganancia_estimada

        Antes esto se hacía dos veces —una para los kilos y otra para las barras— con
        las dos unidades escritas a mano. Con el reparto por grupo, un producto nuevo
        entra al ranking solo, sin que nadie tenga que agregarle su vuelta.

        LOS CENTAVOS VAN AL RESTO MAYOR y no a la última fila de la lista. Antes el
        residuo del redondeo se le sumaba a "la última fila que tuviera de esa unidad",
        que es un puesto que no significa nada —depende de cómo la base devolvió las
        filas— y le desviaba a ese productor su cifra de su propia multiplicación. El
        dueño revisa el ranking fila por fila. Con el resto mayor cada fila queda a lo
        sumo un centavo de su cuenta y la columna sigue sumando exacto.

        Las filas salen del conjunto HISTÓRICO de productores a los que se les
        debe, no solo de los que tuvieron compras EN EL PERÍODO: a quien se le
        compró en mayo y no se le ha pagado se le sigue debiendo en julio, y sin
        su fila la columna `por_pagar` no sumaba la tarjeta "Por pagar a
        productores", que sí es histórica. Esas filas van con cantidades 0 y comprado
        0 —no tuvieron compras en el período, así que no inventan plata en el
        desglose— pero con TODA su deuda: la de las compras del sistema más la
        del libro anterior.

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
        reparto, así que no puede desincronizarse del cálculo.
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

        # Lo comprado en el período, por productor y por producto. El nombre se
        # conserva tal como está escrito en las compras (es el que el dueño ve en el
        # resto del módulo) y se agrupa por su clave canónica, para que dos
        # escrituras del mismo señor no salgan en dos filas.
        nombres: dict[str, str] = {}
        # (clave del tercero, clave del grupo) -> (cantidad, plata)
        comprado: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
        conteo: dict[str, int] = {}
        orden_de_aparicion: list[str] = []
        for productor, tipo, cuantas, kilos_netos, unidades_compradas, plata in (
            self.compras.por_productor_y_tipo(desde, hasta)
        ):
            clave_tercero = _clave_tercero(productor)
            if clave_tercero not in nombres:
                nombres[clave_tercero] = productor
                orden_de_aparicion.append(clave_tercero)
            clave_grupo = self.catalogo.raiz_de(tipo)
            cantidad = kilos_netos if self.catalogo.se_pesa(tipo) else unidades_compradas
            antes = comprado.get((clave_tercero, clave_grupo), (CERO, CERO))
            comprado[(clave_tercero, clave_grupo)] = (
                antes[0] + cantidad,
                antes[1] + plata,
            )
            conteo[clave_tercero] = conteo.get(clave_tercero, 0) + cuantas

        # EL REPARTO, GRUPO POR GRUPO. `realizado[(tercero, grupo)]` es la parte del
        # neto de ese grupo que le corresponde a ese productor, y la suma de cada
        # grupo da EXACTAMENTE su neto.
        realizado: dict[tuple[str, str], Decimal] = {}
        for grupo in grupos:
            # EL NETO DEL GRUPO SE TOMA DE LAS DOS CLASES DE MOVIMIENTO —lo que se pesó
            # y lo que se contó— aunque un grupo normal solo tenga una. Es lo que
            # garantiza que la columna sume la tarjeta pase lo que pase: si una fila
            # vieja de un producto por unidad trajera kilos (una mestiza, que hoy ya no
            # se puede escribir pero pudo quedar guardada), su plata estaría en la otra
            # clase y se quedaría sin repartir. El neto son PESOS, así que sumarlos es
            # legal; lo que nunca se mezcla son las CANTIDADES, y esas siguen saliendo
            # cada una de la columna de su unidad.
            mios = [
                mapa.get(clave, _MovimientoDeProducto())
                for mapa in (kilos, unidades)
                for clave in grupo.claves
            ]
            neto = sum((m.vendido_plata - m.gastos for m in mios), CERO)
            total_cantidad = sum(
                (c for (t, g), (c, _) in comprado.items() if g == grupo.clave), CERO
            )
            if not total_cantidad:
                # Sin compras de este grupo en el período no hay a quién repartirle:
                # esa plata salió de inventario viejo y lo dice la fila del residuo.
                continue
            exactos = [
                (tercero, cantidad * neto / total_cantidad)
                for (tercero, g), (cantidad, _) in sorted(comprado.items())
                if g == grupo.clave and cantidad > CERO
            ]
            for tercero, parte in repartir_al_resto_mayor(exactos, neto).items():
                realizado[(tercero, grupo.clave)] = parte

        claves_kg = {g.clave for g in grupos if g.raiz.se_pesa}
        # El orden en que se arman las filas: del que más plata trajo al que menos, y
        # la clave del tercero como desempate. Es el mismo criterio de siempre, pero
        # escrito acá y no dejado al `ORDER BY` de la base: dos productores que hayan
        # traído lo mismo tienen que salir en el mismo puesto en cada consulta, o el
        # ranking se ve distinto cada vez que se abre la pantalla.
        orden_de_aparicion.sort(
            key=lambda t: (
                -sum((v[1] for (x, _), v in comprado.items() if x == t), CERO),
                t,
            )
        )
        filas: list[GananciaProductor] = []
        for clave_tercero in orden_de_aparicion:
            mios = {g: v for (t, g), v in comprado.items() if t == clave_tercero}
            # Las cantidades de kilos se suman entre grupos que se pesan (los kilos son
            # kilos); las de unidades NO se suman entre productos distintos, así que la
            # columna `barras` habla del producto por unidad de siempre y el resto sale
            # en el desglose por producto. Ver el docstring de `ResumenReventa`.
            cantidad_kilos = sum(
                (v[0] for g, v in mios.items() if g in claves_kg), CERO
            )
            plata_kilos = sum((v[1] for g, v in mios.items() if g in claves_kg), CERO)
            cantidad_unidades, plata_unidades = mios.get(
                clave_de_las_unidades or "", (CERO, CERO)
            )
            precio_promedio = (
                (plata_kilos / cantidad_kilos).quantize(DOS_DECIMALES)
                if cantidad_kilos
                else CERO
            )
            precio_promedio_barra = (
                (plata_unidades / cantidad_unidades).quantize(DOS_DECIMALES)
                if cantidad_unidades
                else CERO
            )
            # LA GANANCIA ES LA SUMA DE SUS GRUPOS: lo que cada unidad realizó menos lo
            # que esa unidad costó. Se suman porque son pesos.
            ganancia = sum(
                (
                    realizado.get((clave_tercero, g), CERO) - v[1]
                    for g, v in mios.items()
                ),
                CERO,
            )
            _, del_sistema = pendiente_sistema.pop(clave_tercero, ("", CERO))
            _, del_libro = pendiente_libro.pop(clave_tercero, ("", CERO))
            filas.append(
                GananciaProductor(
                    productor=nombres[clave_tercero],
                    compras=conteo.get(clave_tercero, 0),
                    kilos=cantidad_kilos,
                    barras=cantidad_unidades,
                    total_comprado=sum((v[1] for v in mios.values()), CERO),
                    total_comprado_barras=plata_unidades,
                    precio_promedio=precio_promedio,
                    precio_promedio_barra=precio_promedio_barra,
                    # La columna `por_pagar` INCLUYE el saldo del libro anterior,
                    # porque la tarjeta "Por pagar a productores" también lo incluye:
                    # si solo lo sumara la tarjeta, la columna dejaría de cuadrar con
                    # ella. Los kilos, el costo y la ganancia NO se tocan: los saldos
                    # anteriores no son compras de este sistema.
                    por_pagar=del_sistema + del_libro,
                    margen_por_kilo=(
                        valor_realizado_kilo - precio_promedio if cantidad_kilos else CERO
                    ),
                    margen_por_barra=(
                        valor_realizado_barra - precio_promedio_barra
                        if cantidad_unidades
                        else CERO
                    ),
                    ganancia_estimada=ganancia,
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
        """El resumen del período, ARMADO PRODUCTO POR PRODUCTO.

        DE DÓNDE SALE CADA CIFRA, Y ES UN SOLO SITIO. Todo lo que hay aquí abajo se
        arma de `_movimientos_del_periodo`, que abre las compras y las ventas del
        período por producto y por unidad. Antes cada cifra salía de su propia
        consulta con el nombre del producto escrito adentro —"las ventas de tipo
        'queso'", "las ventas que no son de tipo 'queso' son la borona"—, y de ahí
        venía el defecto más caro: la venta de un producto que se pesara y no se
        llamara 'queso' se contaba como BORONA, que es subproducto sin costo, así que
        el resumen mostraba "borona vendida" que el dueño no tiene y la ganancia
        inflada.

        LAS DOS REGLAS QUE MANDAN SOBRE TODO LO DEMÁS:

        1. LOS KILOS Y LAS UNIDADES NUNCA SE SUMAN EN UNA MISMA CIFRA ("20 kg + 8
           barras" no es un número). Los movimientos vienen en dos diccionarios
           separados y las cantidades se llevan en variables con la unidad en el
           nombre. LA PLATA SÍ SE SUMA: los pesos son pesos, vengan de kilos o de
           unidades, y esas sumas están escritas de frente para que se vea que son a
           propósito.
        2. LA BORONA ES BORONA POR SU `subproducto_de_id`, NO POR SU NOMBRE. Lo que el
           resumen llama "vendido" son los productos que se compran, y "borona" los
           que llegan gratis con otro. Un producto propio del dueño se cuenta como lo
           primero, que es lo que es.

        QUÉ SIGNIFICAN LOS CAMPOS DE UNIDADES DEL ENCABEZADO (`barras_*`,
        `*_mozzarella`): el producto por unidad DE SIEMPRE, o sea el primero del
        catálogo que se cuenta. No son la suma de todos los productos por unidad, y
        eso es a propósito: sumar panelas con barras de mozzarella daría una cantidad
        que no significa nada y un "precio promedio por barra" que promedia $3.000 con
        $12.000. La plata de los demás productos por unidad SÍ entra en
        `total_compras`, `total_ventas` y `total_gastos` —son pesos— y cada uno tiene
        su propia fila en `por_producto`, que es el desglose que suma el encabezado.
        """
        # --------------------------------------------- los movimientos, por producto
        kilos, unidades = self._movimientos_del_periodo(desde, hasta)
        # Los ajustes del período, abiertos por (de qué producto salieron, a cuál
        # entraron). Es lo que deja que cada grupo cuente los suyos sin adivinar.
        ajustes_por_producto = self.conversiones.totales_periodo_por_producto(desde, hasta)
        # Los kilos que llegaron GRATIS en el período, a nombre del producto QUE CADA
        # COMPRA NOMBRÓ. Se lee UNA vez y de aquí sale para las dos cosas que lo
        # necesitan: armar los grupos (un producto que solo recibió kilos gratis
        # también tiene que tener su fila) y repartir el costo en el desglose.
        gratis_por_producto = dict(
            self.compras.gratis_periodo_por_subproducto(desde, hasta)
        )
        grupos = self.catalogo.grupos(
            [
                *kilos,
                *unidades,
                *(origen for origen, _, _ in ajustes_por_producto),
                *gratis_por_producto,
            ]
        )
        # LOS TRES PRODUCTOS DE SIEMPRE, POR SU CLAVE Y NO POR SU PUESTO EN LA LISTA.
        # Los campos `kilos_disponibles`, `borona_disponible`, `barras_*` y
        # `*_mozzarella` del encabezado son los de esos tres productos: así se llaman
        # y eso es lo que el dueño lee en sus tarjetas desde hace meses.
        #
        # ANTES EL DE UNIDADES SE ESCOGÍA POR ORDEN —"el primero del catálogo que se
        # cuenta"— y esa era una decisión de plata tomada con un campo de presentación:
        # crear una panela por unidad y ponerla de primera le cambiaba a la tarjeta de
        # la mozzarella las barras compradas, las vendidas y su ganancia, sin que se
        # hubiera movido un solo documento. La plata de los demás productos por unidad
        # NO se pierde: entra en `total_compras` / `total_ventas` / `total_gastos` y
        # cada uno tiene su propia fila en `por_producto` y su propia existencia.
        clave_subproducto = TIPO_BORONA
        clave_unidades = TIPO_MOZZARELLA

        def suma(movimientos, campo, *, solo=None, excepto=None) -> Decimal:
            return sum(
                (
                    getattr(mov, campo)
                    for clave, mov in movimientos.items()
                    if (solo is None or clave in solo) and (excepto is None or clave not in excepto)
                ),
                CERO,
            )

        # --------------------------------------------------- lo que se mide en kilos
        kilos_comprados = suma(kilos, "comprado")
        compras_kilos = suma(kilos, "comprado_plata")
        # LO "VENDIDO" son los productos que se compran y "la borona" los que llegan
        # gratis con otro: la diferencia la hace `subproducto_de_id` y no el nombre.
        subproductos = {
            p.clave for p in self.catalogo.por_clave.values() if p.es_subproducto
        }
        kilos_queso = suma(kilos, "vendido", excepto=subproductos)
        ventas_queso = suma(kilos, "vendido_plata", excepto=subproductos)
        gastos_queso = suma(kilos, "gastos", excepto=subproductos)
        kilos_borona = suma(kilos, "vendido", solo=subproductos)
        ventas_borona = suma(kilos, "vendido_plata", solo=subproductos)
        gastos_borona = suma(kilos, "gastos", solo=subproductos)
        # Los totales de kilos son la SUMA de sus partes y no una consulta aparte: así
        # queso + borona da exacto el total, sin restas que le acrediten a uno lo del
        # otro (que es justo de donde salía el defecto).
        ventas_kilos = ventas_queso + ventas_borona
        gastos_kilos = gastos_queso + gastos_borona
        # Ajustes del período: lo que se pasó a subproducto y LA MERMA REAL.
        kilos_a_borona, kilos_merma = self.conversiones.totales_periodo(desde, hasta)

        # ------------------------------------------ lo que se cuenta, en unidades
        # Su propio renglón de punta a punta. Las cifras del encabezado son las del
        # producto por unidad de siempre; la plata de todos entra en los totales.
        del_de_siempre = {clave_unidades} if clave_unidades else set()
        barras_compradas = suma(unidades, "comprado", solo=del_de_siempre)
        compras_mozzarella = suma(unidades, "comprado_plata", solo=del_de_siempre)
        barras_vendidas = suma(unidades, "vendido", solo=del_de_siempre)
        ventas_mozzarella = suma(unidades, "vendido_plata", solo=del_de_siempre)
        gastos_mozzarella = suma(unidades, "gastos", solo=del_de_siempre)
        compras_unidades = suma(unidades, "comprado_plata")
        ventas_unidades = suma(unidades, "vendido_plata")
        gastos_unidades = suma(unidades, "gastos")

        # ---------------------------------------------- la plata, que SÍ se suma
        total_compras = compras_kilos + compras_unidades
        total_ventas = ventas_kilos + ventas_unidades
        total_gastos = gastos_kilos + gastos_unidades

        # ------------------------------------------------------------- el inventario
        existencias = ExistenciasReventa(self.db, self.ctx, catalogo=self.catalogo)
        _, _, por_pagar = self.compras.acumulados()
        _, _, por_cobrar = self.ventas.acumulados()
        # Cartera heredada del sistema anterior. Solo entra en lo que se debe
        # cobrar y pagar: NO tiene kilos, no se compró ni se vendió aquí, así que
        # no toca el inventario, ni los totales del período, ni la ganancia.
        por_cobrar_libro = self.saldos.pendiente(TIPO_SALDO_COBRAR)
        por_pagar_libro = self.saldos.pendiente(TIPO_SALDO_PAGAR)

        # El promedio POR KILO divide la plata DE LOS KILOS entre los kilos. Ojo con
        # no ponerle `total_compras` (que ya incluye las unidades): saldría un "precio
        # por kilo" inflado con pesos que no salieron de ningún kilo, y el dueño lo
        # cruza a mano con lo que le pagó al productor. El promedio de CADA producto
        # sale en su fila del desglose (`costo_kilo`), que es el que de verdad se puede
        # cruzar con un recibo.
        precio_prom_compra = (
            (compras_kilos / kilos_comprados).quantize(DOS_DECIMALES) if kilos_comprados else CERO
        )
        precio_prom_venta = (
            (ventas_queso / kilos_queso).quantize(DOS_DECIMALES) if kilos_queso else CERO
        )
        # Los mismos dos promedios en la otra unidad: plata del producto por unidad de
        # siempre entre sus unidades. Nunca se cruzan con los de arriba.
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
        # Kilos que de verdad se vendieron: lo propio más los subproductos. Es la base
        # de los promedios por kilo vendido (antes solo contaba el queso, y daba 0
        # cuando el período solo tuvo ventas de borona).
        kilos_vendidos_total = kilos_queso + kilos_borona
        # ------------------------------------------------------------- el desglose
        # SE ARMA PRIMERO Y EL ENCABEZADO SE LEE DE ÉL en las cifras que son un
        # residuo. Es al revés de como estaba: antes el encabezado calculaba
        # `kilos_pendientes` con su propia resta y el desglose con la suya, y dos
        # cuentas del mismo hecho terminan contradiciéndose. Ahora el residuo se
        # calcula UNA vez, dentro del grupo que lo produce.
        filas = self._filas_por_producto(
            kilos=kilos,
            unidades=unidades,
            ajustes=ajustes_por_producto,
            gratis=gratis_por_producto,
        )
        # Residuo CON SIGNO del período: lo comprado que no se vendió, no se pasó a
        # subproducto y no se perdió. Sale de las filas del residuo del desglose, que
        # traen el valor absoluto y el signo en el nombre de la fila ('pendiente' si
        # sobró, 'anterior' si se movió inventario de antes del período).
        #
        # Los kilos SÍ se suman entre grupos que se pesan (los kilos son kilos); las
        # unidades hablan del producto por unidad de siempre, por lo mismo que los
        # demás campos `barras_*`.
        def residuo_de(claves_de_grupos, en_kilos: bool) -> Decimal:
            unidad_buscada = UNIDAD_KILO if en_kilos else UNIDAD_BARRA
            # SE RECORREN LOS MIEMBROS DEL GRUPO Y NO SOLO SU RAÍZ: desde que un
            # subproducto que se compró directamente tiene su propio pozo, también
            # tiene su propia fila de residuo, y esos kilos son inventario pendiente
            # igual que los del padre. Sin esto, la borona comprada y no vendida
            # quedaría fuera de la cifra grande mientras sale en su fila del desglose.
            total = CERO
            for grupo in grupos:
                if grupo.raiz.se_pesa != en_kilos or grupo.clave not in claves_de_grupos:
                    continue
                for miembro in grupo.miembros:
                    sobra, falta = _claves_de_residuo(miembro.clave)
                    for fila in filas:
                        if fila.unidad != unidad_buscada:
                            continue
                        if fila.producto == sobra:
                            total += fila.kilos if en_kilos else fila.barras
                        elif fila.producto == falta:
                            total -= fila.kilos if en_kilos else fila.barras
            return total

        kilos_pendientes = residuo_de({g.clave for g in grupos if g.raiz.se_pesa}, True)
        barras_pendientes = residuo_de(del_de_siempre, False)

        # Ganancia neta EXACTA del período = lo que se vendió − lo que se compró
        # − los gastos de venta. Al restar TODA la compra (no solo el costo de lo
        # vendido) queda contado lo que no se alcanzó a vender y la merma real.
        # Suma las dos unidades porque son PESOS: es la cifra que el dueño espera
        # ver como "lo que dejó el negocio", no "lo que dejó el queso".
        ganancia = (total_ventas - total_compras - total_gastos).quantize(DOS_DECIMALES)
        # El margen POR KILO solo mira la plata de los kilos, y esto es lo delicado:
        # dividir la ganancia TOTAL entre los kilos vendidos daría un "peso por
        # kilo" que lleva adentro lo que dejaron las unidades. Con 20 kg y 500 barras
        # esa cifra no diría nada del queso.
        ganancia_kilos = (ventas_kilos - compras_kilos - gastos_kilos).quantize(DOS_DECIMALES)
        margen = (
            (ganancia_kilos / kilos_vendidos_total).quantize(DOS_DECIMALES)
            if kilos_vendidos_total
            else CERO
        )
        # Lo mismo por UNIDAD VENDIDA, con la plata de ese producto y nada más.
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
        # El numerador es el neto DE LOS KILOS: si trajera la plata de las unidades,
        # repartirlo entre los kilos les acreditaría a los productores de queso una
        # ganancia que salió de otro producto.
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
            # El desglose sale por la red que exige la regla de oro: lo que las
            # filas no alcancen a explicar del encabezado sale en su propia fila
            # (ver `_cuadrar_desglose`). Las tres cifras que se le pasan son las
            # MISMAS variables que viajan en el encabezado de esta respuesta, no un
            # recálculo: así no pueden desincronizarse de lo que el dueño ve arriba.
            por_producto=self._cuadrar_desglose(
                filas,
                total_compras=total_compras,
                total_ventas=total_ventas,
                total_gastos=total_gastos,
            ),
            por_productor=self._filas_por_productor(
                desde,
                hasta,
                grupos=grupos,
                kilos=kilos,
                unidades=unidades,
                valor_realizado_kilo=valor_realizado_kilo,
                valor_realizado_barra=valor_realizado_barra,
                clave_de_las_unidades=clave_unidades,
            ),
            # Los tres inventarios de siempre, cada uno el de SU producto y ya no una
            # canasta compartida. `existencias` los trae todos, uno por producto, y es
            # de donde la pantalla nueva los tiene que leer.
            kilos_disponibles=existencias.disponible(TIPO_QUESO),
            borona_disponible=existencias.disponible(clave_subproducto),
            barras_disponibles=existencias.disponible(clave_unidades),
            existencias=[
                ExistenciaProducto(
                    producto=clave,
                    etiqueta=self.catalogo.de(clave).nombre,
                    unidad=self.catalogo.unidad_de(clave),
                    disponible=existencias.disponible(clave),
                )
                for clave in self._claves_para_existencias(existencias)
            ],
            # Las dos tarjetas de cartera suman el sistema MÁS el libro anterior
            # (es la plata que de verdad se debe hoy), y enseguida va ese pedazo
            # por separado para poder mostrar el desglose.
            por_pagar_productores=por_pagar + por_pagar_libro,
            por_cobrar_clientes=por_cobrar + por_cobrar_libro,
            por_cobrar_libro_anterior=por_cobrar_libro,
            por_pagar_libro_anterior=por_pagar_libro,
        )

    def _claves_para_existencias(self, existencias: ExistenciasReventa) -> list[str]:
        """Qué productos salen en la lista de existencias, y en qué orden.

        Salen los del catálogo SIEMPRE —aunque estén en cero, porque "no hay" es una
        respuesta que el dueño necesita ver— y además cualquier clave que aparezca en
        los movimientos sin estar en el catálogo, que si no quedaría con mercancía que
        ninguna pantalla muestra. El orden es el del catálogo (el que el dueño puso) y
        los sueltos al final.
        """
        del_catalogo = [
            p.clave
            for p in sorted(
                self.catalogo.por_clave.values(), key=lambda x: (x.orden, x.clave)
            )
        ]
        sueltos = sorted(c for c in existencias.claves() if c and c not in del_catalogo)
        return del_catalogo + sueltos
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
                    producto=_nombre_para_el_cliente(self.catalogo, tipo),
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

        CADA EVENTO VIAJA CON LA CLAVE DE SU PRODUCTO, y el reparto le lleva una cola
        de inventario a cada uno. Antes había UNA cola para todo lo que se pesa, así
        que la venta de un producto consumía las compras de otro y se le cargaba el
        costo del producto equivocado (ver el comentario largo en `lotes.py`). Quién es
        subproducto de quién lo dice el catálogo con `subproducto_de_id`, no el nombre
        de la fila.

        Y LOS PRODUCTOS DE CADA MOVIMIENTO SALEN DE SU PROPIA FILA, no del catálogo:
        la compra dice a quién le entregó lo que llegó gratis y el ajuste dice de qué
        producto salió y a cuál entró. Antes los dos se adivinaban con el ORDEN del
        catálogo, así que reordenar la lista de productos volvía a repartir toda la
        historia con otras colas y cambiaba el costo de ventas ya impresas.
        """
        catalogo = CatalogoReventa(self.db, self.ctx.empresa_id)
        compras = [
            CompraEvento(
                fecha=fila[0], orden=indice, productor=fila[2],
                kilos=Decimal(fila[3] or 0), borona_kilos=Decimal(fila[4] or 0),
                precio_kilo=Decimal(fila[7] or 0),
                valor_total=Decimal(fila[5] or 0), saldo=Decimal(fila[6] or 0),
                producto=fila[8] or TIPO_QUESO,
                subproducto=fila[9],
            )
            for indice, fila in enumerate(self.compras.eventos_para_lotes())
        ]
        ventas = [
            VentaEvento(
                fecha=fila[0], orden=indice, cliente=fila[6], tipo=fila[2],
                kilos=Decimal(fila[3] or 0), precio_kilo=Decimal(fila[7] or 0),
                valor_total=Decimal(fila[4] or 0), gasto_monto=Decimal(fila[5] or 0),
                es_subproducto=catalogo.es_subproducto(fila[2]),
            )
            for indice, fila in enumerate(self.ventas.eventos_para_lotes())
        ]
        ajustes = [
            AjusteEvento(
                fecha=fila[0], orden=indice, kilos=Decimal(fila[2] or 0),
                destino=fila[3],
                producto=fila[4] or TIPO_QUESO,
                subproducto=fila[5],
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
