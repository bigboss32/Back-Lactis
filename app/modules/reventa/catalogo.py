"""QUIÉN MANDA SOBRE UN RENGLÓN DE PLATA: EL PRODUCTO DEL CATÁLOGO.

POR QUÉ EXISTE ESTE ARCHIVO. El módulo nació con tres productos escritos a mano en
el código —'queso', 'borona' y 'mozzarella'— y cada camino de plata preguntaba por
esas tres cadenas: en qué unidad va la cantidad, contra qué inventario se compara una
venta, de dónde sale el costo, en qué fila del desglose cae la plata. Después se abrió
el catálogo para que el dueño creara sus propios productos, y ahí quedó el hueco: se
abrió la puerta sin conectar la tubería. Un producto nuevo se podía crear, comprar y
vender, y cada una de esas cuatro preguntas le contestaba con la respuesta del queso o
de la mozzarella. Plata mal contada en producción.

Este archivo es la tubería. Es la ÚNICA pieza que traduce "esta fila dice tipo = X" a
las cuatro cosas que hay que saber de X:

  1. SU UNIDAD ('kg' o 'unidad'), que decide en qué columnas va la cantidad y el
     precio al escribir, y en qué canasta se cuenta al leer.
  2. SI ES SUBPRODUCTO DE OTRO, que es lo que hace que la borona sea borona: llega
     gratis con su padre y por eso no tiene costo propio. La borona no es un caso
     especial del código, es una fila del catálogo con `subproducto_de_id`.
  3. SU GRUPO DE COSTEO: un producto raíz con sus subproductos. La plata de las
     compras del grupo es el pozo que se reparte entre los destinos del grupo, y por
     eso el costo de un producto NO se puede pagar con las compras de otro.
  4. SU NOMBRE, para el rótulo de su fila en el desglose.

LO QUE ESTA CLASE NO HACE: sumar plata. No tiene ninguna consulta de movimientos. Lee
el catálogo una vez y contesta preguntas sobre productos. Quien suma es el resumen, y
lo hace agrupando por lo que esto le dice.

DOS REGLAS DE LA CASA, LAS DOS EXIGIDAS AQUÍ
--------------------------------------------
- MULTIEMPRESA: el catálogo se carga con `empresa_id` y `deleted_at IS NULL`, siempre.
  El dueño maneja dos queseras en la misma instalación y cada una tiene su 'queso'.
  Preguntarle la unidad de un producto al catálogo del vecino ya movió plata una vez
  (ver `_claves_que_se_cuentan` en el repositorio).
- UNA CLAVE QUE NO ESTÁ EN EL CATÁLOGO SE PESA Y ES SU PROPIO PRODUCTO. No se
  inventa, no se pega a otro y sobre todo NO SE PIERDE: es exactamente lo que hace la
  clasificación de lectura (`se_mide_en_kilos` cuenta como kilos todo lo que no
  reconoce), y así una fila vieja, una con el tipo en blanco o una con un tipo que
  alguien escribió mal aparece en su propia fila del desglose con su plata a la
  vista, en vez de que sus pesos se le acrediten a la borona.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.modules.reventa.models import (
    TIPO_BORONA,
    UNIDAD_KILO,
    UNIDAD_UNIDAD,
    ProductoReventa,
)
from app.modules.reventa.repository import ProductoReventaRepository


@dataclass(frozen=True)
class ProductoDelCatalogo:
    """Un producto, con lo poco que hace falta saber de él para contar su plata.

    Es una copia inmutable y no la fila del ORM a propósito: estas respuestas viajan
    por todo el resumen, y una entidad viva podría recargarse en medio de un cálculo
    y contestar distinto en la segunda pregunta.
    """

    clave: str
    nombre: str
    unidad: str
    orden: int
    # La clave del producto del que este es subproducto (la borona lo es del queso).
    # En nulo = es un producto raíz: se compra y se paga.
    subproducto_de: str | None
    # Si está en el catálogo de la empresa. En `False` cuando la clave viene de una
    # fila de movimiento y no de la tabla: ver la regla del final del docstring del
    # módulo.
    del_catalogo: bool = True

    @property
    def se_pesa(self) -> bool:
        return self.unidad == UNIDAD_KILO

    @property
    def se_cuenta(self) -> bool:
        return self.unidad == UNIDAD_UNIDAD

    @property
    def es_subproducto(self) -> bool:
        return self.subproducto_de is not None


@dataclass(frozen=True)
class GrupoDeCosteo:
    """UN PRODUCTO RAÍZ CON SUS SUBPRODUCTOS, y es la pieza que hace que el costo no
    se cruce entre productos.

    QUÉ ES UN GRUPO Y POR QUÉ NO ES SIMPLEMENTE UN PRODUCTO. Lo que se paga son las
    compras del producto raíz. Sus subproductos llegan gratis con él (la borona que
    viene en el lote) o salen de él (el queso que se desmenuza), así que no tienen
    compras propias que costear: su costo sale del mismo pozo. Por eso el pozo es del
    GRUPO y no del producto.

    Y por eso mismo el reparto del costo no se puede hacer producto por producto
    contra el total de las compras en kilos, que es lo que se hacía antes: con dos
    productos que se pesan —el queso del cliente y la panela que el dueño agregue— el
    costo de la panela vendida salía del pozo del queso, y el queso quedaba con un
    costo de inventario que no era el suyo.

    `miembros` va con la raíz PRIMERO y después sus subproductos en el orden del
    catálogo: es el orden en que salen las filas del desglose, y es el que el dueño
    ya conoce (primero "Vendido como queso", después "Vendido como borona").
    """

    raiz: ProductoDelCatalogo
    subproductos: tuple[ProductoDelCatalogo, ...]

    @property
    def unidad(self) -> str:
        """La del producto raíz: es en la que se compra, o sea en la que está el pozo."""
        return self.raiz.unidad

    @property
    def miembros(self) -> tuple[ProductoDelCatalogo, ...]:
        return (self.raiz, *self.subproductos)

    @property
    def claves(self) -> tuple[str, ...]:
        return tuple(p.clave for p in self.miembros)

    @property
    def clave(self) -> str:
        """La del grupo = la de su raíz. Sirve de identidad y de desempate de orden."""
        return self.raiz.clave


# CÓMO SE LLAMA LA FILA CUYO `tipo` ESTÁ EN BLANCO. Una fila así no la puede escribir
# el sistema (al renglón sin tipo se le pone el producto de siempre), pero la base la
# admite y podría llegar por un SQL suelto o por una importación. Su plata tiene que
# aparecer en el desglose con un rótulo que se entienda, y no puede quedarse sin clave
# de fila: el campo `producto` de la respuesta es con lo que la pantalla decide cómo
# pintar el renglón, y una cadena vacía ahí no le dice nada a nadie.
CLAVE_SIN_IDENTIFICAR = "sin_identificar"
NOMBRE_SIN_IDENTIFICAR = "Producto sin identificar"


def producto_suelto(clave: str) -> ProductoDelCatalogo:
    """El producto de una clave que NO está en el catálogo: se pesa y es su propia
    raíz. Ver la regla del final del docstring del módulo."""
    return ProductoDelCatalogo(
        clave=clave or "",
        # El rótulo es la clave misma: es lo único que se sabe de él, y decir otra
        # cosa sería inventarle un nombre al dueño.
        nombre=clave or NOMBRE_SIN_IDENTIFICAR,
        unidad=UNIDAD_KILO,
        # De último en la lista: los productos del catálogo van primero, que es el
        # orden que el dueño puso.
        orden=10_000,
        subproducto_de=None,
        del_catalogo=False,
    )


class CatalogoReventa:
    """El catálogo de UNA empresa, leído UNA vez, listo para preguntarle.

    SE LEE UNA SOLA VEZ Y ESO ES PARTE DEL DISEÑO: el resumen le pregunta por cada
    clave que aparece en las compras, en las ventas, en los productores y en el
    desglose. Con una consulta por pregunta, un resumen de un catálogo de diez
    productos serían decenas de consultas, y peor: dos preguntas de la misma petición
    podrían caer a los dos lados de un cambio del catálogo y contestar distinto.
    """

    def __init__(self, db, empresa_id):
        self._db = db
        self._empresa_id = empresa_id
        self._por_clave: dict[str, ProductoDelCatalogo] | None = None

    # -------------------------------------------------------------- la carga
    @property
    def por_clave(self) -> dict[str, ProductoDelCatalogo]:
        if self._por_clave is None:
            self._por_clave = self._cargar()
        return self._por_clave

    def _cargar(self) -> dict[str, ProductoDelCatalogo]:
        filas: list[ProductoReventa] = ProductoReventaRepository(
            self._db, self._empresa_id
        ).catalogo()
        # La relación se guarda por CLAVE y no por id: la clave es la que traen las
        # filas de compra y de venta (ver el docstring de `ProductoReventa`), así que
        # todo el resto del módulo puede hablar en claves y no tener que cargar ids.
        clave_de_id = {fila.id: fila.clave for fila in filas}
        unidad_de_id = {fila.id: fila.unidad for fila in filas}
        return {
            fila.clave: ProductoDelCatalogo(
                clave=fila.clave,
                nombre=fila.nombre,
                unidad=fila.unidad,
                orden=int(fila.orden or 0),
                subproducto_de=(
                    clave_de_id.get(fila.subproducto_de_id)
                    # UN SUBPRODUCTO SOLO CUELGA DE UN PADRE DE SU MISMA UNIDAD, y si
                    # no, acá se lee como producto raíz. Un grupo de costeo tiene UN
                    # pozo y ese pozo está en la unidad de su raíz (ver `GrupoDeCosteo`):
                    # una borona que se pesa colgada de una mozzarella que se cuenta no
                    # tiene de dónde heredar costo —no se reparten barras entre kilos— y
                    # el grupo salía impreso DOS VECES, una en la vuelta de los kilos y
                    # otra en la de las unidades: tres claves REPETIDAS en el mismo
                    # desglose y $30.000 de neto acreditados al productor equivocado.
                    #
                    # La puerta de escribir ya no deja armar esa pareja (ver
                    # `ProductoReventaService._padre`), pero esto se queda: una fila así
                    # pudo quedar guardada antes de ese candado, y una lista de
                    # productos mal armada no puede sacar un desglose que no cuadre.
                    if unidad_de_id.get(fila.subproducto_de_id) == fila.unidad
                    else None
                ),
            )
            for fila in filas
        }

    # ---------------------------------------------------------- preguntas simples
    def de(self, clave: str | None) -> ProductoDelCatalogo:
        """El producto de esa clave. Si no está en el catálogo, uno suelto que se
        pesa (nunca `None`: quien pregunta no tiene que acordarse del caso raro)."""
        producto = self.por_clave.get(clave or "")
        return producto if producto is not None else producto_suelto(clave or "")

    def unidad_de(self, clave: str | None) -> str:
        """'kg' o 'unidad'. ES LA PREGUNTA QUE DECIDE LA PLATA AL ESCRIBIR: en qué
        columnas va la cantidad y el precio de una compra o de una venta."""
        return self.de(clave).unidad

    def se_pesa(self, clave: str | None) -> bool:
        return self.de(clave).se_pesa

    def es_subproducto(self, clave: str | None) -> bool:
        """Si llega gratis con otro producto. Es lo que hace que la borona sea
        borona, y lo que impide que un producto propio del dueño se cuente como
        subproducto sin costo solo porque se pesa."""
        return self.de(clave).es_subproducto

    def subproductos_de(self, clave: str) -> tuple[ProductoDelCatalogo, ...]:
        return tuple(
            p
            for p in sorted(self.por_clave.values(), key=lambda x: (x.orden, x.clave))
            if p.subproducto_de == clave
        )

    def raiz_de(self, clave: str | None) -> str:
        """La clave del grupo de costeo al que pertenece esta: la del padre si es
        subproducto, y la propia si no."""
        producto = self.de(clave)
        if producto.subproducto_de and producto.subproducto_de in self.por_clave:
            return producto.subproducto_de
        return producto.clave

    # ------------------------------------ a quién le entran los kilos, AL ESCRIBIR
    #
    # LAS DOS PREGUNTAS DE ABAJO SE CONTESTAN AL ESCRIBIR Y NUNCA AL LEER, y esa es
    # toda la diferencia con lo que había. Antes se resolvían CADA VEZ que alguien
    # pedía el resumen o el inventario, mirando el ORDEN del catálogo —un campo de
    # presentación que la API deja cambiar con un PUT—, así que reordenar la lista le
    # movía a la borona meses de plata ya registrada (el detalle, con cifras, está en
    # el docstring de `ConversionBorona`). Ahora se resuelven UNA vez, cuando se
    # registra el movimiento, y la respuesta queda GUARDADA en su fila: a partir de
    # ahí ningún cambio del catálogo la puede mover.
    #
    # Y NINGUNA DE LAS RAMAS MIRA EL ORDEN. Cuando hay que desempatar se usa el
    # producto de siempre —la clave 'borona', que es una CONSTANTE del código y de la
    # siembra, no "el primero de la lista"—, exactamente por la misma razón por la que
    # las filas del desglose conservan sus nombres de siempre: es lo que estos
    # movimientos han significado desde que existen, y no se puede mover porque
    # alguien agregue un producto.
    def _quien_recibe(
        self, clave_del_padre: str | None, propuesto: str | None
    ) -> str | None:
        """LA REGLA, UNA SOLA PARA LAS DOS PUERTAS: a qué producto le entran unos
        kilos que salen de otro.

        REGISTRAR SIEMPRE GANA, y esa es la regla de la casa: un problema de la LISTA
        DE PRODUCTOS no puede dejar al dueño sin poder anotar lo que ya hizo. La lista
        se arregla después; la compra, el ajuste y la venta de hoy son plata que ya se
        movió en la vida real y tienen que poder quedar escritas.

        En orden:

        1. lo que diga quien registra (`propuesto`), si de verdad puede recibir kilos;
        2. si de quien salen tiene UN SOLO subproducto que se pese, ese: no hay a quién
           más darle, y es lo que llegó (o salió) con su padre;
        3. el de siempre ('borona'), que es a quien estos kilos le han entrado desde
           que el módulo existe, esté o no esté en el catálogo.

        Y DEVUELVE `None` SOLO EN LOS DOS CASOS QUE DE VERDAD NECESITAN QUE UNA PERSONA
        DECIDA, que quien llama convierte en un rechazo con la salida escrita:

        · nombraron un producto que se CUENTA POR UNIDADES: una barra no recibe kilos,
          y meterle kilos a su inventario sería mercancía inventada;
        · nombraron un nombre que nadie conoce —ni está en el catálogo ni es la clave
          de siempre—: eso es un error de dedo, y anotarle la plata a un producto
          fantasma que el dueño no puso es peor que pedirle que revise el nombre.

        POR QUÉ EL PASO 3 ACEPTA UNA CLAVE QUE NO ESTÁ EN EL CATÁLOGO. Porque el dueño
        acaba de escribir una cifra en el campo de la borona: la fila la nombra a ELLA,
        la constante del código y de la siembra, no "el primero de la lista". Y una
        clave que no está en el catálogo NO SE PIERDE: se pesa, es su propio producto,
        sale con su fila en el desglose y con su renglón en las existencias (ver la
        regla del final del docstring del módulo), así que el día que el dueño la
        agregue a la lista esos kilos ya están a nombre suyo.

        NINGUNA RAMA MIRA EL ORDEN DEL CATÁLOGO. Cuando hay que desempatar se usa la
        clave de siempre, que es una constante: el orden es un campo de presentación
        que un PUT cambia, y decidir plata con él fue exactamente el defecto que este
        archivo vino a cerrar.
        """
        if propuesto:
            elegido = self.por_clave.get(propuesto)
            if elegido is not None:
                # Del catálogo: sirve si se pesa. Uno que se cuenta por unidades no
                # puede recibir kilos, y eso sí lo tiene que resolver una persona.
                return elegido.clave if elegido.se_pesa else None
            # Fuera del catálogo solo pasa la clave DE SIEMPRE: lo que se acepta sin
            # estar en la lista es el producto de toda la vida, no el que alguien
            # tecleó mal.
            return TIPO_BORONA if propuesto == TIPO_BORONA else None

        padre = self.por_clave.get(clave_del_padre or "")
        candidatos = (
            [p for p in self.subproductos_de(padre.clave) if p.se_pesa]
            if padre is not None
            else []
        )
        if len(candidatos) == 1:
            return candidatos[0].clave
        de_siempre = self.por_clave.get(TIPO_BORONA)
        if de_siempre is None:
            # No está en el catálogo: la fila lo nombra igual. Registrar gana.
            return TIPO_BORONA
        return de_siempre.clave if de_siempre.se_pesa else None

    def subproducto_que_recibe(
        self, clave_del_padre: str | None, propuesto: str | None = None
    ) -> str | None:
        """El producto al que le entran los kilos que salen de otro por un AJUSTE.

        ANTES ERA LA ESTRICTA —el destino tenía que ser subproducto del origen— Y ESO
        DEJABA AL DUEÑO SIN PODER TRABAJAR. Está medido contra el código desplegado:
        con una empresa recién creada (su catálogo no se siembra hasta el despliegue
        siguiente), con la borona fuera de la lista o con la borona descolgada de su
        padre, el ajuste de todos los días rebotaba con un 422 donde antes respondía
        201, y nombrar el origen y el destino a mano tampoco lo salvaba.

        Un ajuste es el dueño anotando que unos kilos dejaron de ser una cosa y pasaron
        a ser otra: eso ya pasó en la bodega, y la lista de productos no puede decirle
        que no. Si el destino no es subproducto del origen, la plata NO se cruza de
        grupo —el desglose no le deja consumir el pozo del origen y esos kilos se
        quedan en su residuo (ver `_filas_de_un_grupo`)—, así que aceptarlo no le mueve
        un peso a nadie: solo mueve los kilos, que es lo que el dueño dijo que pasó.
        """
        return self._quien_recibe(clave_del_padre, propuesto)

    def quien_recibe_lo_gratis(
        self, clave_comprada: str | None, propuesto: str | None = None
    ) -> str | None:
        """El producto al que le entran los kilos que LLEGARON GRATIS con una compra.

        Es la misma regla del ajuste (ver `_quien_recibe`), y tiene que serlo: la
        columna `borona_kilos` significa "borona que llegó con este lote", y el dueño
        se la anota encima de CUALQUIER compra que se pese, no solo de las del queso.
        Comprando panela le pueden llegar unos kilos de borona encima, y esos kilos
        están en la bodega. Exigir que el destinatario fuera subproducto de lo comprado
        los dejaría sin dónde entrar, y desde ahí las ventas legítimas de borona
        rebotarían por falta de existencias.
        """
        return self._quien_recibe(clave_comprada, propuesto)

    # ---------------------------------------------------------------- los grupos
    def grupos(self, claves: Iterable[str] | None = None) -> list[GrupoDeCosteo]:
        """Los grupos de costeo, en el orden en que salen sus filas en el desglose.

        `claves` son las que aparecieron en los movimientos del período; sirven para
        que un producto que no está en el catálogo (una fila vieja, un tipo escrito
        mal) también tenga su grupo y su fila, y su plata no caiga en la de otro.

        EL ORDEN ES EL DE LA PANTALLA y no es decoración: primero los grupos que se
        pesan y después los que se cuentan (nunca mezclados, porque "20 kg + 8
        barras" no es un número), y dentro de cada clase en el orden del catálogo,
        que es el que el dueño puso. Así el desglose de un período de puro queso sale
        con las filas exactamente donde siempre estuvieron.
        """
        productos: dict[str, ProductoDelCatalogo] = dict(self.por_clave)
        for clave in claves or ():
            # OJO, LA CLAVE EN BLANCO TAMBIÉN ENTRA (por eso no hay un `if clave`): una
            # fila con el `tipo` vacío es plata registrada, y si no tuviera grupo su
            # plata no caería en ninguna fila del desglose y tendría que salir por la
            # red de seguridad, que es el aviso de "algo se rompió" y no el sitio donde
            # va la plata de todos los días.
            if (clave or "") not in productos:
                productos[clave or ""] = producto_suelto(clave or "")

        # Cada raíz con sus subproductos. Un subproducto cuyo padre no esté en el
        # catálogo se trata como raíz: su plata tiene que caer en alguna fila, y
        # pegarlo a un grupo que no existe la haría desaparecer.
        subproductos: dict[str, list[ProductoDelCatalogo]] = {}
        raices: list[ProductoDelCatalogo] = []
        for producto in sorted(productos.values(), key=lambda p: (p.orden, p.clave)):
            padre = producto.subproducto_de
            if padre and padre in productos:
                subproductos.setdefault(padre, []).append(producto)
            else:
                raices.append(producto)

        grupos = [
            GrupoDeCosteo(raiz=raiz, subproductos=tuple(subproductos.get(raiz.clave, ())))
            for raiz in raices
        ]
        # Los que se pesan primero, los que se cuentan después, y cada clase en el
        # orden del catálogo.
        grupos.sort(key=lambda g: (0 if g.raiz.se_pesa else 1, g.raiz.orden, g.raiz.clave))
        return grupos
