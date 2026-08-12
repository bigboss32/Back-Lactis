"""UN INVENTARIO POR PRODUCTO, no tres canastas.

QUÉ HABÍA ANTES Y QUÉ COSTÓ. Había exactamente tres inventarios, con tres nombres
escritos en el código: el del 'queso', el de la 'borona' y el de la 'mozzarella'. Todo
producto que el dueño agregara al catálogo caía en uno de esos tres según el nombre de
la fila, y de ahí salieron dos huecos por los que se despachó mercancía que no existe:

  · UN PRODUCTO QUE SE PESA Y NO SE LLAMA 'queso' NO TENÍA QUIÉN LE CONTARA. El
    disponible del queso se calculaba como "kilos comprados (de todo) − kilos vendidos
    DE TIPO 'queso' − convertidos": una venta de panela no entraba en el sustraendo, así
    que el disponible NO BAJABA NUNCA. Se compraron 100 kg y se registraron SEIS ventas
    de 100 kg: las seis pasaron. 600 kg despachados de 100, con la ganancia de una
    mercancía que no existe.
  · TODOS LOS PRODUCTOS QUE SE CUENTAN COMPARTÍAN LA CANASTA DE LA MOZZARELLA. Vender
    panelas sin haberlas comprado pasaba, restaba de las barras de mozzarella y la
    dejaba en NEGATIVO, y desde ahí las ventas legítimas de mozzarella quedaban
    bloqueadas: el dueño se queda sin poder trabajar sin entender por qué.

LA REGLA DE AHORA, UNA SOLA: cada producto tiene su inventario, y una venta se compara
contra el disponible DE SU PRODUCTO. Tener 500 kg de queso no autoriza a despachar una
panela, y al contrario.

CÓMO SE CUENTA EL DISPONIBLE DE UN PRODUCTO
-------------------------------------------
En la unidad de SU producto (kilos si se pesa, unidades si se cuenta):

    entra:  lo que se le compró
          + lo que llegó GRATIS a nombre suyo (la columna `borona_kilos` de las
            compras que LO NOMBRARON en `subproducto_tipo`)
          + lo que se convirtió hacia él (los ajustes que lo nombran de destino)
    sale:   lo que se le vendió
          + lo que salió de él por ajustes (los que lo nombran de origen: lo que se
            pasó a subproducto MÁS la merma)

Para el queso, la borona y la mozzarella del cliente esto da EXACTAMENTE las mismas
tres cifras que daban los tres cálculos de antes, y no por casualidad: los kilos de una
compra que se cuenta valen cero y las unidades de una que se pesa también, así que
"comprado de todo" y "comprado de este producto" eran la misma suma cuando los únicos
productos eran esos tres. Lo fija tests/test_reventa_no_movimiento.py.

NINGUNA DE ESAS CUATRO SUMAS MIRA EL CATÁLOGO, y eso es lo que cambió. Hasta hace poco,
"lo que llega gratis" y "lo que se convirtió" se le acreditaban al subproducto que el
catálogo tuviera de PRIMERO en su orden de presentación: crear un producto con
`orden = 0` le vaciaba a la borona meses de mercancía y desde ahí sus ventas legítimas
rebotaban con "solo hay 0 kg". Ahora cada compra y cada ajuste NOMBRAN a su producto en
su propia fila (ver `CompraQueso.subproducto_tipo` y `ConversionBorona`), así que estas
cuatro sumas son agrupaciones y no interpretaciones. Un producto que el dueño agregue
tiene dónde recibir lo suyo, y ningún cambio del catálogo mueve lo ya registrado.
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.reventa.catalogo import CatalogoReventa
from app.modules.reventa.repository import (
    CompraQuesoRepository,
    ConversionBoronaRepository,
    VentaQuesoRepository,
)

CERO = Decimal("0")


class ExistenciasReventa:
    """El disponible de CADA producto, calculado UNA vez por petición.

    LO DE "UNA VEZ" ES PLATA Y NO VELOCIDAD. El guardia de una factura de tres
    renglones pregunta por el disponible de cada uno; si cada pregunta volviera a
    sumar la base, dos renglones del mismo producto se compararían cada uno contra el
    disponible COMPLETO y la factura despacharía el doble de lo que hay. Con las
    cifras cargadas una vez, quien valida un conjunto puede sumar primero lo pedido y
    comparar después, que es la única forma de que la suma no se cuele.
    """

    def __init__(self, db, ctx, *, catalogo: CatalogoReventa | None = None):
        self.db = db
        self.ctx = ctx
        self.catalogo = catalogo or CatalogoReventa(db, ctx.empresa_id)
        self._compras = CompraQuesoRepository(db, ctx.empresa_id)
        self._ventas = VentaQuesoRepository(db, ctx.empresa_id)
        self._conversiones = ConversionBoronaRepository(db, ctx.empresa_id)
        self._disponibles: dict[str, Decimal] | None = None
        self._claves: list[str] | None = None

    # ------------------------------------------------------------------ el cálculo
    def _cargar(self) -> dict[str, Decimal]:
        # Las dos consultas se hacen UNA vez y de ellas sale todo, incluida la lista de
        # claves: preguntarlas otra vez para armar la lista serían cuatro consultas
        # más por cada validación de una factura.
        compradas = {f[0]: f for f in self._compras.acumulados_por_tipo()}
        vendidas = {f[0]: f for f in self._ventas.acumulados_por_tipo()}

        # LO QUE LLEGA GRATIS CON UNA COMPRA (`borona_kilos`), acreditado al producto
        # QUE LA COMPRA NOMBRÓ. Una compra puede traer kilos gratis encima de
        # cualquier producto que se pese —el dueño anota lo que le llegó de más—, y
        # por eso esto no filtra por quién es el padre de quién: lo que manda es a
        # quién dijo la fila que le entraban.
        gratis_hacia: dict[str, Decimal] = {}
        for clave, kilos in self._compras.gratis_por_subproducto():
            gratis_hacia[clave] = gratis_hacia.get(clave, CERO) + kilos

        # LOS AJUSTES, ABIERTOS POR PRODUCTO: de cuál salieron y a cuál entraron. El
        # destino en nulo es la merma, que sale de un producto y no le entra a nadie.
        sale_por_ajustes: dict[str, Decimal] = {}
        entra_por_ajustes: dict[str, Decimal] = {}
        for origen, destino, kilos in self._conversiones.acumulados_por_producto():
            sale_por_ajustes[origen] = sale_por_ajustes.get(origen, CERO) + kilos
            if destino:
                entra_por_ajustes[destino] = entra_por_ajustes.get(destino, CERO) + kilos

        self._claves = self._claves_de(
            compradas, vendidas, gratis_hacia, sale_por_ajustes, entra_por_ajustes
        )

        disponibles: dict[str, Decimal] = {}
        for clave in self._claves:
            producto = self.catalogo.de(clave)
            compra = compradas.get(clave, ("", CERO, CERO, CERO))
            venta = vendidas.get(clave, ("", CERO, CERO))
            if producto.se_pesa:
                entra = (
                    compra[1]
                    + gratis_hacia.get(clave, CERO)
                    + entra_por_ajustes.get(clave, CERO)
                )
                sale = venta[1] + sale_por_ajustes.get(clave, CERO)
            else:
                # Lo que se cuenta: entra como pieza y sale como pieza. No participa
                # en los ajustes —una barra no se desmenuza ni pierde peso, porque no
                # se está pesando— y por eso esta cuenta es la más corta.
                entra = compra[3]
                sale = venta[2]
            disponibles[clave] = entra - sale
        return disponibles

    @property
    def disponibles(self) -> dict[str, Decimal]:
        if self._disponibles is None:
            self._disponibles = self._cargar()
        return self._disponibles

    def _claves_de(self, *mapas: dict) -> list[str]:
        """Todas las claves que hay que contar: las del catálogo MÁS las que
        aparezcan en cualquier movimiento.

        Las de los movimientos hacen falta porque una fila puede hablar de un producto
        que no está en el catálogo (una vieja, una con el tipo en blanco, una con un
        tipo escrito mal). Sin contarla, esa mercancía no tendría inventario y se
        podría despachar sin límite, que es el mismo hueco de antes con otro nombre.

        ENTRAN LOS CINCO MAPAS y no solo compras y ventas: un ajuste que nombre un
        producto que ya no está en el catálogo también movió kilos, y si su clave no
        entrara aquí, esos kilos no se le restarían a nadie.
        """
        claves = list(self.catalogo.por_clave)
        for mapa in mapas:
            for clave in mapa:
                if clave not in claves:
                    claves.append(clave)
        return claves

    def claves(self) -> list[str]:
        """Las claves con inventario, en el orden del catálogo y los sueltos al final."""
        if self._claves is None:
            self._cargar_si_hace_falta()
        return list(self._claves or ())

    def _cargar_si_hace_falta(self) -> None:
        if self._disponibles is None:
            self._disponibles = self._cargar()

    # ------------------------------------------------------------------ preguntas
    def disponible(self, clave: str | None) -> Decimal:
        """Lo que hay en bodega de ese producto, en SU unidad. Cero si nunca se movió."""
        return self.disponibles.get(clave or "", CERO)

    def unidad(self, clave: str | None) -> str:
        return self.catalogo.unidad_de(clave)

    def nombre(self, clave: str | None) -> str:
        """Cómo llamarlo en el mensaje de error: el nombre que el dueño le puso.

        NO es un detalle de cortesía. El mensaje decía "Solo hay 10 kg de queso
        disponibles" cuando el dueño estaba vendiendo costeño: le está hablando de un
        producto que no es el que tiene en la mano, y lo manda a revisar el inventario
        equivocado.
        """
        return self.catalogo.de(clave).nombre

    def rotulo_de_unidad(self, clave: str | None) -> str:
        """Cómo se dice la cantidad en el mensaje: 'kg' o 'unidades'."""
        return "kg" if self.catalogo.se_pesa(clave) else "unidades"
