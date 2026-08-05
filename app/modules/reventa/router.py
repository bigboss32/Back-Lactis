import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response, UploadFile, status

from app.core.context import RequestContext
from app.core.deps import DbSession, require_any_permission, require_permission
from app.core.exceptions import BusinessError
from app.core.pagination import Page, PageParams, page_params
from app.modules.reventa.schemas import (
    AbonoCreate,
    AdjuntosLista,
    EnlaceCompartido,
    GananciaPorDia,
    LotesPanel,
    CompraQuesoCreate,
    CompraQuesoRead,
    CompraQuesoUpdate,
    ConversionCreate,
    ConversionRead,
    DocumentoReventaCreate,
    DocumentoReventaRead,
    DocumentoReventaUpdate,
    EstadoCuentaCliente,
    EstadoCuentaProductor,
    ProductoReventaCreate,
    ProductoReventaRead,
    ProductoReventaUpdate,
    ResumenReventa,
    SaldoAnteriorCreate,
    SaldoAnteriorRead,
    SaldoAnteriorUpdate,
    SugerenciasReventa,
    TemporadaCerrar,
    TemporadaCreate,
    TemporadaRead,
    TemporadasPanel,
    TemporadaUpdate,
    VentaQuesoCreate,
    VentaQuesoRead,
    VentaQuesoUpdate,
)
from app.modules.reventa.service import (
    AdjuntoReventaService,
    CompraQuesoService,
    DocumentoReventaService,
    LoteService,
    ConversionBoronaService,
    ProductoReventaService,
    ReventaResumenService,
    SaldoAnteriorService,
    TemporadaService,
    VentaQuesoService,
)

router = APIRouter(tags=["Compra y venta de queso"])


@router.get("/resumen", response_model=ResumenReventa, summary="Resumen del negocio de reventa")
def resumen(
    db: DbSession,
    desde: date = Query(...),
    hasta: date = Query(...),
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> ResumenReventa:
    return ReventaResumenService(db, ctx).resumen(desde, hasta)


@router.get(
    "/estado-cuenta",
    response_model=EstadoCuentaCliente,
    summary="Estado de cuenta de un cliente (compras, pagos y saldo)",
)
def estado_cuenta(
    db: DbSession,
    # max_length igual al de la columna (String(150)): un nombre más largo no
    # puede existir en la base, así que se rechaza antes de tocar la consulta.
    cliente: str = Query(..., min_length=2, max_length=150),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> EstadoCuentaCliente:
    """Sin desde/hasta cubre todo el histórico del cliente: el saldo real que debe."""
    return ReventaResumenService(db, ctx).estado_cuenta(cliente, desde, hasta)


@router.get(
    "/estado-cuenta/pdf",
    summary="Descargar el estado de cuenta del cliente en PDF (para enviárselo)",
)
def estado_cuenta_pdf(
    db: DbSession,
    # Mismo tope que la columna String(150), igual que en la ruta del JSON.
    cliente: str = Query(..., min_length=2, max_length=150),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    ctx: RequestContext = Depends(require_permission("reventa", "imprimir")),
) -> Response:
    contenido, filename = ReventaResumenService(db, ctx).estado_cuenta_pdf(
        cliente, desde, hasta
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/estado-cuenta-productor",
    response_model=EstadoCuentaProductor,
    summary="Estado de cuenta de un productor (compras, pagos y saldo a su favor)",
)
def estado_cuenta_productor(
    db: DbSession,
    # max_length igual al de la columna (String(150)), como en el del cliente: un
    # nombre más largo no puede existir en la base, así que se rechaza antes de
    # tocar la consulta.
    productor: str = Query(..., min_length=2, max_length=150),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> EstadoCuentaProductor:
    """Sin desde/hasta cubre todo el histórico del productor, que es el caso
    normal: el saldo real que se le debe.

    OJO CON EL SIGNO, que va al contrario del estado de cuenta del cliente: aquí
    un saldo positivo significa que LA QUESERA LE DEBE A ÉL.
    """
    return ReventaResumenService(db, ctx).estado_cuenta_productor(productor, desde, hasta)


@router.get(
    "/estado-cuenta-productor/pdf",
    summary="Descargar el estado de cuenta del productor en PDF (para entregárselo)",
)
def estado_cuenta_productor_pdf(
    db: DbSession,
    # Mismo tope que la columna String(150), igual que en la ruta del JSON.
    productor: str = Query(..., min_length=2, max_length=150),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    ctx: RequestContext = Depends(require_permission("reventa", "imprimir")),
) -> Response:
    contenido, filename = ReventaResumenService(db, ctx).estado_cuenta_productor_pdf(
        productor, desde, hasta
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/sugerencias",
    response_model=SugerenciasReventa,
    summary="Nombres ya usados de productores y clientes (autocompletar)",
)
def sugerencias(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> SugerenciasReventa:
    return ReventaResumenService(db, ctx).sugerencias()


# ----------------------------------------- catálogo de productos de reventa
# QUÉ SE COMPRA Y SE REVENDE, como dato y no como una lista escrita en el código.
# El dueño pidió poder "comprar y vender algo que quiera el cliente": estas cinco
# rutas son ese "algo".
#
# EN ESTE LOTE EL CATÁLOGO ES DE SOLO LECTURA PARA LOS CAMINOS DE PLATA: ninguna
# consulta de compras, ventas, resumen, temporadas, lotes ni FIFO lo mira todavía,
# así que no puede mover una cifra. Lo que sí hay ya es el CRUD completo, para que
# el dueño arme su lista antes de que las pantallas de registro la usen.
#
# LOS PERMISOS SON LOS DE SIEMPRE ('reventa', consultar/crear/editar/eliminar), los
# mismos con los que ya se registra una compra. No lleva 'administrar': eso está
# reservado para anular plata, y acá no hay plata.
#
# Van montadas a mano y no con `build_crud_router` porque ese registra su listado
# en la raíz del router ("") y este router ya tiene la raíz ocupada por el resto
# del módulo, que va colgado de /reventa.
@router.get(
    "/productos",
    response_model=Page[ProductoReventaRead],
    summary="Listar los productos que se compran y se revenden",
)
def listar_productos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None, description="Busca por nombre o por clave"),
    estado: str | None = Query(None, description="'activo' o 'inactivo'"),
) -> Page[ProductoReventaRead]:
    """En el orden en que el dueño los puso, que es el de la lista de selección."""
    items, total = ProductoReventaService(db, ctx).listar(
        params, search=search, estado=estado
    )
    return Page.build(items, total, params)


@router.post(
    "/productos",
    response_model=ProductoReventaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Agregar un producto al catálogo (por ahora solo por kilo)",
)
def crear_producto(
    payload: ProductoReventaCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> ProductoReventaRead:
    """Se pide el NOMBRE y si se pesa o se cuenta; lo demás se deduce.

    La clave —el identificador con el que las compras y las ventas nombran al
    producto— sale del nombre y no vuelve a cambiar. Los decimales y si admite
    ajustes salen de la unidad.

    EN ESTE CORTE SOLO PASA 'kg'. Un producto por unidad exige tumbar primero los
    CheckConstraints de `compras_queso` y `ventas_queso`, y se rechaza con un
    mensaje que lo explica en vez de guardarse a medias.

    Si ese producto YA EXISTIÓ y se había quitado, se reactiva la misma fila con su
    mismo id y su misma clave, y se le pone el nombre nuevo.
    """
    return ProductoReventaService(db, ctx).crear(payload)


@router.get(
    "/productos/{entity_id}",
    response_model=ProductoReventaRead,
    summary="Un producto del catálogo",
)
def obtener_producto(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> ProductoReventaRead:
    return ProductoReventaService(db, ctx).obtener(entity_id)


@router.put(
    "/productos/{entity_id}",
    response_model=ProductoReventaRead,
    summary="Renombrar un producto, reordenarlo o desactivarlo",
)
def editar_producto(
    entity_id: uuid.UUID,
    payload: ProductoReventaUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> ProductoReventaRead:
    """RENOMBRAR SE PUEDE SIEMPRE Y NO TIENE RIESGO: la clave y el id no se mueven,
    así que ninguna compra ni venta ya registrada se entera. Es a propósito, porque
    el dueño va a querer que "Queso" diga "Queso costeño".

    La clave y la unidad no se pueden cambiar (no están en el esquema): la clave es
    la identidad con la que las filas nombran al producto, y la unidad decide la
    forma de la cantidad. De quién es subproducto solo se puede mover mientras no
    tenga movimientos, porque de ahí hereda el costo lo que se venda de él.
    """
    return ProductoReventaService(db, ctx).actualizar(entity_id, payload)


@router.delete(
    "/productos/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Quitar un producto del catálogo (solo si no tiene movimientos)",
)
def eliminar_producto(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    """Solo se puede quitar un producto que no tenga movimientos, igual que en el
    resto del ERP. Si ya se compró o se vendió, lo que se hace es DESACTIVARLO: deja
    de ofrecerse al registrar y su historia se queda completa."""
    ProductoReventaService(db, ctx).eliminar(entity_id)


# ------------------------------- documentos (facturas de varios productos)
# UNA FACTURA ES UNA CABECERA Y N RENGLONES, y los renglones son las MISMAS filas
# de /compras y /ventas: un producto, su cantidad, su precio, su plata y sus
# abonos. La cabecera no guarda ni una cifra de plata; el total es la suma de los
# renglones, calculada al leer.
#
# LOS PERMISOS SON LOS DE SIEMPRE ('reventa', crear/consultar/editar/...): registrar
# una venta de tres productos es registrar una venta, y quien podía hacerlo de a
# uno puede hacerlo de a tres. Lo único distinto es 'administrar' para anular, que
# es lo que ya exigen /compras/{id}/anular y /ventas/{id}/anular.
@router.get(
    "/documentos",
    response_model=Page[DocumentoReventaRead],
    summary="Listar facturas de reventa (compras y ventas de varios productos)",
)
def listar_documentos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
    tipo: str | None = Query(None, description="'compra' o 'venta'"),
    search: str | None = Query(None, description="Busca por el nombre del tercero"),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[DocumentoReventaRead]:
    """Cada factura viene con sus renglones y con su total CALCULADO (no guardado).

    De la más reciente a la más vieja. El estado de pago es derivado: sale de los
    estados de los renglones, no de una columna.
    """
    items, total = DocumentoReventaService(db, ctx).listar_filtrado(
        params, tipo=tipo, search=search, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@router.post(
    "/documentos",
    response_model=DocumentoReventaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar una compra o una venta de VARIOS productos",
)
def crear_documento(
    payload: DocumentoReventaCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> DocumentoReventaRead:
    """La cabecera (tipo, fecha, tercero, nota) y sus renglones, EN UNA TRANSACCIÓN.

    `tipo` decide la forma de los renglones: 'compra' los recibe con
    kilos_brutos/precio_kilo o barras/precio_barra, y 'venta' con
    kilos/precio_kilo o barras/precio_barra más el gasto de venta.

    Las existencias se validan sumando los renglones DEL MISMO producto y sobre la
    factura COMPLETA antes de escribir la primera fila: con 400 kg en bodega, dos
    renglones de 300 kg no pasan.
    """
    servicio = DocumentoReventaService(db, ctx)
    return servicio.leer(servicio.crear(payload).id)


@router.get(
    "/documentos/{entity_id}",
    response_model=DocumentoReventaRead,
    summary="Una factura con sus renglones y su total calculado",
)
def obtener_documento(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> DocumentoReventaRead:
    return DocumentoReventaService(db, ctx).leer(entity_id)


@router.put(
    "/documentos/{entity_id}",
    response_model=DocumentoReventaRead,
    summary="Editar una factura (los productos, solo si no tiene abonos)",
)
def editar_documento(
    entity_id: uuid.UUID,
    payload: DocumentoReventaUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> DocumentoReventaRead:
    """La fecha, el tercero y la nota se pueden corregir siempre, y se les copian a
    todos los renglones (que es de donde el resumen y la cartera leen esos datos).

    Mandar `renglones` significa REHACERLOS, y eso solo se permite si la factura no
    tiene abonos: los abonos cuelgan de los renglones, así que rehacerlos con plata
    encima sería mover los pagos a productos distintos de los que el dueño vio
    cuando la recibió. Con abonos hay que anular la factura y volverla a registrar.
    """
    servicio = DocumentoReventaService(db, ctx)
    return servicio.leer(servicio.actualizar(entity_id, payload).id)


@router.post(
    "/documentos/{entity_id}/abonos",
    response_model=DocumentoReventaRead,
    summary="Abonar a la factura entera (el abono se derrama sobre los renglones)",
)
def abonar_documento(
    entity_id: uuid.UUID,
    payload: AbonoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> DocumentoReventaRead:
    """El abono NO SE DIVIDE, SE DERRAMA: se le aplica a los renglones en su orden,
    `min(lo que queda, el saldo del renglón)` a cada uno. Sin división no hay
    redondeo, así que la suma de los abonos da el abono exacto, y cada abono queda
    siendo una cifra entera que el dueño puede señalar.

    El abono por renglón (`/compras/{id}/abonos`, `/ventas/{id}/abonos`) sigue
    funcionando igual que siempre, para cuando el pago es de un producto.
    """
    servicio = DocumentoReventaService(db, ctx)
    return servicio.leer(servicio.registrar_abono(entity_id, payload).id)


@router.post(
    "/documentos/{entity_id}/anular",
    response_model=DocumentoReventaRead,
    summary="Anular una factura (anula todos sus renglones)",
)
def anular_documento(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "administrar")),
) -> DocumentoReventaRead:
    """Cada renglón se anula por el camino de siempre, así que la factura de una
    compra solo se anula si el queso de TODOS sus renglones sigue en la bodega."""
    servicio = DocumentoReventaService(db, ctx)
    return servicio.leer(servicio.anular(entity_id).id)


@router.delete(
    "/documentos/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una factura y sus renglones",
)
def eliminar_documento(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    """No se puede si alguno de sus renglones tiene abonos: primero se eliminan los
    abonos, o se anula la factura."""
    DocumentoReventaService(db, ctx).eliminar(entity_id)


# ------------------------------------------------------------------- compras
@router.get("/compras", response_model=Page[CompraQuesoRead], summary="Listar compras de queso")
def listar_compras(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None),
    estado: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[CompraQuesoRead]:
    items, total = CompraQuesoService(db, ctx).listar_filtrado(
        params, search=search, estado=estado, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@router.post("/compras", response_model=CompraQuesoRead, status_code=status.HTTP_201_CREATED, summary="Registrar compra de queso")
def crear_compra(
    payload: CompraQuesoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).crear(payload)


@router.put("/compras/{entity_id}", response_model=CompraQuesoRead, summary="Editar compra (recalcula el estado con los abonos ya hechos)")
def editar_compra(
    entity_id: uuid.UUID,
    payload: CompraQuesoUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).actualizar(entity_id, payload)


@router.delete("/compras/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar compra")
def eliminar_compra(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    CompraQuesoService(db, ctx).eliminar(entity_id)


@router.post("/compras/{entity_id}/abonos", response_model=CompraQuesoRead, summary="Abonar a un productor")
def abonar_compra(
    entity_id: uuid.UUID,
    payload: AbonoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).registrar_abono(entity_id, payload)


@router.delete(
    "/compras/{entity_id}/abonos/{abono_id}",
    response_model=CompraQuesoRead,
    summary="Eliminar un abono mal registrado de la compra",
)
def eliminar_abono_compra(
    entity_id: uuid.UUID,
    abono_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).eliminar_abono(entity_id, abono_id)


@router.post("/compras/{entity_id}/anular", response_model=CompraQuesoRead, summary="Anular compra")
def anular_compra(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "administrar")),
) -> CompraQuesoRead:
    return CompraQuesoService(db, ctx).anular(entity_id)


# -------------------------------------------------------------- conversiones
@router.get("/conversiones", response_model=Page[ConversionRead], summary="Queso pasado a borona")
def listar_conversiones(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
) -> Page[ConversionRead]:
    service = ConversionBoronaService(db, ctx)
    items, total = service.listar(params)
    return Page.build(items, total, params)


@router.post(
    "/conversiones",
    response_model=ConversionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Pasar queso a borona",
)
def crear_conversion(
    payload: ConversionCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> ConversionRead:
    return ConversionBoronaService(db, ctx).crear(payload)


@router.delete(
    "/conversiones/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar conversión",
)
def eliminar_conversion(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    ConversionBoronaService(db, ctx).eliminar(entity_id)


# -------------------------------------------------------------------- ventas
@router.get("/ventas", response_model=Page[VentaQuesoRead], summary="Listar ventas de queso")
def listar_ventas(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
    search: str | None = Query(None),
    estado: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[VentaQuesoRead]:
    items, total = VentaQuesoService(db, ctx).listar_filtrado(
        params, search=search, estado=estado, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@router.post("/ventas", response_model=VentaQuesoRead, status_code=status.HTTP_201_CREATED, summary="Registrar venta de queso")
def crear_venta(
    payload: VentaQuesoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).crear(payload)


@router.put("/ventas/{entity_id}", response_model=VentaQuesoRead, summary="Editar venta (recalcula el estado con los abonos ya hechos)")
def editar_venta(
    entity_id: uuid.UUID,
    payload: VentaQuesoUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).actualizar(entity_id, payload)


@router.delete("/ventas/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar venta")
def eliminar_venta(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    VentaQuesoService(db, ctx).eliminar(entity_id)


@router.post("/ventas/{entity_id}/abonos", response_model=VentaQuesoRead, summary="Registrar abono del cliente")
def abonar_venta(
    entity_id: uuid.UUID,
    payload: AbonoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).registrar_abono(entity_id, payload)


@router.delete(
    "/ventas/{entity_id}/abonos/{abono_id}",
    response_model=VentaQuesoRead,
    summary="Eliminar un abono mal registrado de la venta",
)
def eliminar_abono_venta(
    entity_id: uuid.UUID,
    abono_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).eliminar_abono(entity_id, abono_id)


@router.post("/ventas/{entity_id}/anular", response_model=VentaQuesoRead, summary="Anular venta")
def anular_venta(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "administrar")),
) -> VentaQuesoRead:
    return VentaQuesoService(db, ctx).anular(entity_id)


# ----------------------------------- adjuntos (soportes de transferencia)
# Cuatro rutas y dos permisos distintos, y la diferencia importa:
#
# - SUBIR con 'crear' O 'editar' (require_any_permission): adjuntarle el soporte
#   a una compra es parte de registrarla para quien la acaba de hacer, y una
#   edición para quien le agrega la foto después.
# - VER con 'consultar', que es lo mismo que ver la compra.
# - COMPARTIR con 'exportar': el enlace largo saca información de pago DEL
#   SISTEMA hacia afuera (se manda por WhatsApp y se puede reenviar). Es la
#   misma acción que ya exige sacar datos en los demás módulos, y deja fuera a
#   los roles de solo consulta, que pueden mirar el soporte en pantalla pero no
#   repartirlo.
# - BORRAR con 'eliminar', A SECAS. No con 'crear'. Ya pasó en este proyecto que
#   un borrado quedó pidiendo 'crear' (los abonos) y le dio a media empresa la
#   posibilidad de borrar lo que otro registró.
@router.post(
    "/compras/{entity_id}/adjuntos",
    response_model=AdjuntosLista,
    status_code=status.HTTP_201_CREATED,
    summary="Adjuntar soportes de pago a una compra (varias imágenes o PDF)",
)
def subir_adjuntos_compra(
    entity_id: uuid.UUID,
    files: list[UploadFile],
    db: DbSession,
    ctx: RequestContext = Depends(require_any_permission("reventa", "crear", "editar")),
) -> AdjuntosLista:
    """Devuelve la lista completa ya actualizada, con enlaces frescos, para que
    la pantalla no tenga que pedirla otra vez después de subir."""
    return AdjuntoReventaService(db, ctx).subir(files, compra_id=entity_id)


@router.get(
    "/compras/{entity_id}/adjuntos",
    response_model=AdjuntosLista,
    summary="Soportes de la compra, con enlaces firmados de corta duración",
)
def listar_adjuntos_compra(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> AdjuntosLista:
    return AdjuntoReventaService(db, ctx).listar(compra_id=entity_id)


@router.post(
    "/ventas/{entity_id}/adjuntos",
    response_model=AdjuntosLista,
    status_code=status.HTTP_201_CREATED,
    summary="Adjuntar soportes de pago a una venta (varias imágenes o PDF)",
)
def subir_adjuntos_venta(
    entity_id: uuid.UUID,
    files: list[UploadFile],
    db: DbSession,
    ctx: RequestContext = Depends(require_any_permission("reventa", "crear", "editar")),
) -> AdjuntosLista:
    return AdjuntoReventaService(db, ctx).subir(files, venta_id=entity_id)


@router.get(
    "/ventas/{entity_id}/adjuntos",
    response_model=AdjuntosLista,
    summary="Soportes de la venta, con enlaces firmados de corta duración",
)
def listar_adjuntos_venta(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> AdjuntosLista:
    return AdjuntoReventaService(db, ctx).listar(venta_id=entity_id)


@router.post(
    "/adjuntos/{adjunto_id}/compartir",
    response_model=EnlaceCompartido,
    summary="Enlace de más duración para mandar UNA imagen por WhatsApp",
)
def compartir_adjunto(
    adjunto_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "exportar")),
) -> EnlaceCompartido:
    """El enlace trae escrito hasta cuándo sirve, en hora de Colombia, porque
    quien lo reparte tiene que saber qué está repartiendo. Queda en la auditoría."""
    return AdjuntoReventaService(db, ctx).compartir(adjunto_id)


@router.delete(
    "/adjuntos/{adjunto_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar un soporte (borra también el archivo del almacenamiento)",
)
def eliminar_adjunto(
    adjunto_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    AdjuntoReventaService(db, ctx).eliminar_adjunto(adjunto_id)


# ---------------------------------------------- saldos de la cuenta anterior
@router.get(
    "/saldos-anteriores",
    response_model=Page[SaldoAnteriorRead],
    summary="Listar saldos traídos del sistema anterior",
)
def listar_saldos_anteriores(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
    params: PageParams = Depends(page_params),
    tipo: str | None = Query(None, description="'cobrar' (clientes) o 'pagar' (productores)"),
    search: str | None = Query(None),
    estado: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> Page[SaldoAnteriorRead]:
    items, total = SaldoAnteriorService(db, ctx).listar_filtrado(
        params, tipo=tipo, search=search, estado=estado, desde=desde, hasta=hasta
    )
    return Page.build(items, total, params)


@router.post(
    "/saldos-anteriores",
    response_model=SaldoAnteriorRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cargar un saldo del sistema anterior (no toca inventario ni ganancia)",
)
def crear_saldo_anterior(
    payload: SaldoAnteriorCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).crear(payload)


@router.put(
    "/saldos-anteriores/{entity_id}",
    response_model=SaldoAnteriorRead,
    summary="Editar saldo anterior (recalcula el estado con los abonos ya hechos)",
)
def editar_saldo_anterior(
    entity_id: uuid.UUID,
    payload: SaldoAnteriorUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).actualizar(entity_id, payload)


@router.delete(
    "/saldos-anteriores/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar saldo anterior",
)
def eliminar_saldo_anterior(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    SaldoAnteriorService(db, ctx).eliminar(entity_id)


@router.post(
    "/saldos-anteriores/{entity_id}/abonos",
    response_model=SaldoAnteriorRead,
    summary="Registrar un abono sobre un saldo del sistema anterior",
)
def abonar_saldo_anterior(
    entity_id: uuid.UUID,
    payload: AbonoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).registrar_abono(entity_id, payload)


@router.delete(
    "/saldos-anteriores/{entity_id}/abonos/{abono_id}",
    response_model=SaldoAnteriorRead,
    summary="Eliminar un abono mal registrado del saldo anterior",
)
def eliminar_abono_saldo_anterior(
    entity_id: uuid.UUID,
    abono_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).eliminar_abono(entity_id, abono_id)


@router.post(
    "/saldos-anteriores/{entity_id}/anular",
    response_model=SaldoAnteriorRead,
    summary="Anular saldo anterior",
)
def anular_saldo_anterior(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "administrar")),
) -> SaldoAnteriorRead:
    return SaldoAnteriorService(db, ctx).anular(entity_id)


# ----------------------------------------------------------------- temporadas
@router.get(
    "/temporadas",
    response_model=TemporadasPanel,
    summary="Temporadas con la ganancia de cada una (calculada, no guardada)",
)
def panel_temporadas(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> TemporadasPanel:
    """La lista de temporadas con sus cifras, de la más reciente a la más vieja.

    Las cifras se calculan con el mismo motor del resumen sobre las fechas de
    cada temporada, así que la ganancia de una temporada es EXACTAMENTE la que
    muestra el Resumen si se filtra a esas fechas. No hay ninguna cifra guardada.
    """
    return TemporadaService(db, ctx).panel()


@router.post(
    "/temporadas",
    response_model=TemporadaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir o registrar una temporada (sin fecha_fin queda abierta)",
)
def crear_temporada(
    payload: TemporadaCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "crear")),
) -> TemporadaRead:
    """Se puede registrar una temporada YA PASADA con sus dos fechas: las cifras
    salen de los movimientos que ya están cargados, así que aparecen de una."""
    return TemporadaService(db, ctx).crear(payload)


@router.put(
    "/temporadas/{entity_id}",
    response_model=TemporadaRead,
    summary="Editar una temporada (nombre, fechas o notas)",
)
def editar_temporada(
    entity_id: uuid.UUID,
    payload: TemporadaUpdate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> TemporadaRead:
    return TemporadaService(db, ctx).actualizar(entity_id, payload)


@router.delete(
    "/temporadas/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una temporada (no borra compras ni ventas)",
)
def eliminar_temporada(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "eliminar")),
) -> None:
    """Borra SOLO la temporada, que es un rango de fechas con nombre. Las compras
    y las ventas de esas fechas se quedan tal cual: la temporada no las contiene,
    solo las agrupa para mirarlas."""
    TemporadaService(db, ctx).eliminar(entity_id)


@router.post(
    "/temporadas/{entity_id}/cerrar",
    response_model=TemporadaRead,
    summary="Cerrar la temporada (le pone fecha de fin; no congela las cifras)",
)
def cerrar_temporada(
    entity_id: uuid.UUID,
    payload: TemporadaCerrar,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> TemporadaRead:
    return TemporadaService(db, ctx).cerrar(entity_id, payload.fecha_fin)


@router.post(
    "/temporadas/{entity_id}/reabrir",
    response_model=TemporadaRead,
    summary="Reabrir una temporada cerrada por equivocación",
)
def reabrir_temporada(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("reventa", "editar")),
) -> TemporadaRead:
    return TemporadaService(db, ctx).reabrir(entity_id)


# ---------------------------------------------------------------------- lotes
@router.get(
    "/ganancia-por-dia",
    response_model=GananciaPorDia,
    summary="Cuánto se ganó de verdad entre dos fechas, día por día",
)
def ganancia_por_dia(
    db: DbSession,
    desde: date = Query(..., description="Primer día que se cuenta"),
    hasta: date = Query(..., description="Último día que se cuenta"),
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> GananciaPorDia:
    """De cada venta de esos días: lo que entró, menos lo que había costado ESE
    queso (el reparto FIFO lo sabe exacto, no es un promedio), menos el flete.

    No es lo mismo que la ganancia del resumen, que resta las compras del
    período: esa sale negativa cuando se compra mucho y se vende poco, aunque no
    se haya perdido nada — el queso está en la bodega.
    """
    if hasta < desde:
        raise BusinessError("La fecha final no puede ser anterior a la inicial")
    return LoteService(db, ctx).ganancia_por_dia(desde, hasta)


@router.get(
    "/lotes",
    response_model=LotesPanel,
    summary="Ganancia por lote de compra (las compras de una misma fecha)",
)
def panel_lotes(
    db: DbSession,
    desde: date | None = Query(None, description="Filtra qué lotes se muestran"),
    hasta: date | None = Query(None, description="Filtra qué lotes se muestran"),
    ctx: RequestContext = Depends(require_permission("reventa", "consultar")),
) -> LotesPanel:
    """Qué dejó cada tanda de queso que se compró.

    Un lote son todas las compras de queso de una misma fecha. Como las ventas no
    dicen de qué lote salió el queso, se reparten FIFO: se vende del lote más
    viejo primero, que es lo que pasa en la bodega porque el queso es perecedero.

    `desde`/`hasta` recortan qué lotes se MUESTRAN, no el cálculo: el reparto se
    hace siempre sobre toda la historia, porque para saber qué había en inventario
    en una fecha hay que haber procesado lo de antes.
    """
    return LoteService(db, ctx).panel(desde, hasta)
