import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, HoraDeRegistroMixin, TenantMixin
from app.core.database import Base

# Categorías sembradas por defecto para cada empresa.
# NO se incluyen "Compra de leche" ni el transporte de recepción de leche: esos
# costos ya se contabilizan vía recepciones/liquidaciones y duplicarlos aquí
# inflaría el estado de resultados. "Fletes" SÍ va: es el despacho del queso
# vendido (se cobra por kilo), un costo distinto que no se registra en otro lado.
CATEGORIAS_DEFECTO = (
    "Combustible",
    "Servicios",
    "Nómina",
    "Mantenimiento",
    "Papelería",
    "Insumos",
    "Fletes",
    "Otros",
)


class CategoriaGasto(TenantMixin, AuditMixin, Base):
    __tablename__ = "categorias_gasto"

    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(300))


class Gasto(TenantMixin, AuditMixin, Base):
    __tablename__ = "gastos"

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    categoria_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categorias_gasto.id"), index=True)
    concepto: Mapped[str] = mapped_column(String(200), nullable=False)
    proveedor: Mapped[str | None] = mapped_column(String(150))
    # Opcional: gastos cobrados por unidad (ej. flete por kilo). Si vienen ambos,
    # valor = cantidad * precio_unitario. Si no, el valor se ingresa directo.
    cantidad: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    precio_unitario: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    numero_factura: Mapped[str | None] = mapped_column(String(50))
    observaciones: Mapped[str | None] = mapped_column(String(500))
    # LA FACTURA VIEJA, la que se subía antes a la carpeta `uploads/` del propio
    # servidor. NO SE ESCRIBE MÁS: las facturas nuevas van a `AdjuntoGasto`, en el
    # bucket privado. La columna se queda —y la pantalla sigue mostrando el enlace
    # cuando trae algo— porque borrarla escondería las que el dueño ya subió, y
    # eso sería perderle una factura. Ver `AdjuntoGasto` para el porqué del cambio.
    adjunto_url: Mapped[str | None] = mapped_column(String(300))
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))

    categoria = relationship("CategoriaGasto", lazy="joined")

    # Las facturas vivas del gasto. Mismo criterio que en los soportes de pago de
    # las liquidaciones: el filtro por `deleted_at` va DENTRO del join —si no, la
    # lista seguiría contando las borradas— y la relación es de SOLO LECTURA, para
    # que el ORM no borre filas por su cuenta: quitar una factura tiene que pasar
    # por el servicio, que además borra el archivo del bucket.
    #
    # `selectin` y no `joined`: el listado de gastos trae una página entera y una
    # carga diferida dispararía una consulta POR GASTO nada más que para poder
    # decir cuántas facturas tiene cada uno.
    adjuntos: Mapped[list["AdjuntoGasto"]] = relationship(
        primaryjoin=(
            "and_(Gasto.id == AdjuntoGasto.gasto_id, AdjuntoGasto.deleted_at.is_(None))"
        ),
        viewonly=True,
        lazy="selectin",
        order_by="AdjuntoGasto.created_at",
    )

    @property
    def adjuntos_count(self) -> int:
        """Cuántas facturas tiene colgadas.

        Va en la respuesta del listado para que el clip de la grilla salga marcado
        con el número: el dueño revisa la lista buscando justamente los gastos a
        los que les falta la factura, y para eso no puede tener que abrir uno por
        uno.
        """
        return len(self.adjuntos)


class AdjuntoGasto(HoraDeRegistroMixin, TenantMixin, AuditMixin, Base):
    """La factura de un gasto, guardada en el bucket privado.

    POR QUÉ EXISTE ESTA TABLA SI `Gasto.adjunto_url` YA GUARDABA UNA FACTURA. La
    de antes se subía a la carpeta `uploads/` del servidor, que está publicada
    como archivos estáticos en `/uploads` SIN NINGUNA CLAVE: la dirección de la
    factura de una quesera se podía abrir desde cualquier navegador del mundo, sin
    entrar al sistema, y quien tuviera una dirección podía tantear las de al lado.
    Una factura trae el NIT del proveedor, el valor y a veces la cuenta a la que se
    pagó. Además solo cabía UNA por gasto —la segunda página de una factura pisaba
    la primera— y el archivo se guardaba tal como salía del celular, de varios
    megas cada uno.

    Ahora es el MISMO mecanismo que ya usan los soportes de pago de las
    liquidaciones y los de reventa, y por las mismas tres razones:

    NO SE GUARDA NINGUNA URL. Solo `object_key`, la llave del objeto dentro del
    bucket privado. El enlace para verla se firma en el momento en que alguien la
    pide y caduca solo (ver app/core/storage.py). Una URL guardada en una columna
    es un permiso permanente: quien la viera —en un backup, en un log, en un
    export— vería la factura para siempre.

    LA LLAVE LLEVA EL empresa_id ADENTRO:
    `{empresa_id}/gastos/{gasto_id}/{uuid}.jpg`. Además del filtro por empresa en
    cada consulta, la llave queda amarrada a la empresa dueña; y como lleva un
    uuid aleatorio, no se puede adivinar la de nadie.

    Y LA TABLA LLEVA `empresa_id` PROPIO —aunque el gasto ya lo tenga— porque a la
    factura se entra DIRECTO POR SU PROPIO id para compartirla y para borrarla
    (`/gastos/adjuntos/{adjunto_id}`), sin el gasto en la ruta. Sin la columna, el
    aislamiento entre queseras dependería de acordarse de escribir un JOIN hasta el
    gasto en cada consulta, y un filtro que hay que acordarse de escribir es el
    filtro que un día no se escribe.

    LA HORA LA ESCRIBE LA APLICACIÓN, CON MICROSEGUNDOS (`HoraDeRegistroMixin`),
    porque las facturas se listan EN ORDEN —primero la que se mandó primero— y la
    pantalla las sube TODAS EN UNA SOLA PETICIÓN. En Postgres `now()` es la hora de
    la TRANSACCIÓN: las tres páginas de una misma factura quedarían con EL MISMO
    instante y el orden lo decidiría el motor. Con una factura de varias páginas
    eso no es un detalle: se vería la página 3 antes que la 1.
    """

    __tablename__ = "adjuntos_gasto"
    __table_args__ = (
        # Con nombre explícito y no con `unique=True` en la columna: así el nombre
        # es el MISMO que el de la migración y un `alembic revision --autogenerate`
        # futuro no propone borrarla y volverla a crear.
        UniqueConstraint("object_key", name="uq_adjuntos_gasto_object_key"),
    )

    # CASCADE es la red de abajo, no el mecanismo: el servicio borra las filas Y
    # los archivos del bucket antes de borrar el gasto, porque una fila que se va
    # sin su archivo deja el archivo cobrando almacenamiento sin que nadie pueda
    # verlo ni borrarlo.
    gasto_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gastos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # Nombre con el que llegó el archivo, para mostrarlo y para nombrar la
    # descarga. NO se usa para armar la llave: el nombre lo escribe quien sube.
    nombre_archivo: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # El peso de lo que QUEDÓ en el bucket, ya comprimido, no el de la foto que
    # salió del celular: es lo que se está pagando.
    tamano_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Quién la subió: el id va en `created_by` (AuditMixin), que es la única fuente
    # de ese dato. Aquí se guarda solo el NOMBRE tal como estaba al subir, que es
    # un hecho distinto: si mañana el usuario se borra o le cambian el nombre, la
    # factura tiene que seguir diciendo quién la aportó.
    subido_por_nombre: Mapped[str | None] = mapped_column(String(150), default=None)

    @property
    def es_imagen(self) -> bool:
        return (self.content_type or "").startswith("image/")
