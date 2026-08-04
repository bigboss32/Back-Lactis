import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin, TenantMixin
from app.core.database import Base


class RecepcionLeche(TenantMixin, AuditMixin, Base):
    __tablename__ = "recepciones_leche"
    __table_args__ = (
        Index("ix_recepcion_fecha_proveedor", "empresa_id", "fecha", "proveedor_id"),
    )

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    proveedor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proveedores.id"), nullable=False)
    transportador_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transportadores.id"))
    ruta_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rutas.id"))
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))

    cantidad_litros: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_litro: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bonificaciones: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    descuentos: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    observaciones: Mapped[str | None] = mapped_column(String(500))

    # Calculados por el servicio en cada escritura
    valor_bruto: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    valor_transporte: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    valor_neto: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))

    # Marcas de liquidación: una recepción se liquida al proveedor (leche)
    # y al transportador (flete) por separado; cada marca evita duplicados
    liquidacion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("liquidaciones.id"), index=True
    )
    liquidacion_transporte_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("liquidaciones.id"), index=True
    )

    proveedor = relationship("Proveedor", lazy="joined")
    transportador = relationship("Transportador", lazy="joined")
    ruta = relationship("Ruta", lazy="joined")

    # NADA de lo que sigue es una columna: lo llena
    # RecepcionService._marcar_estado_liquidacion. Se deja declarado aquí, con
    # valor por omisión, para que RecepcionRead siempre encuentre el atributo
    # aunque la lectura venga por un camino que no lo llene.

    # El estado de la liquidación que MANDA sobre este día (la más trabada de las
    # dos marcas de arriba). Es la seña de siempre: sirve para avisar que al tocar
    # el día se mueve una liquidación ya generada.
    liquidacion_estado = None

    # Las dos platas por separado, que es lo que hacía falta para que el candado
    # sea por CAMPO y no por fila: al proveedor se le puede haber pagado la leche
    # sin que el flete se haya liquidado siquiera, y en ese caso el transportador
    # SÍ se puede corregir. Ver el bloque de `_CAMPOS_DE_LA_LECHE` en el servicio.
    liquidacion_estado_leche = None
    liquidacion_estado_flete = None
    leche_pagada = False
    flete_pagado = False

    # El candado ya resuelto, para que la pantalla apague exactamente los campos
    # que el backend va a rebotar y le explique al usuario por qué. Los nombres
    # son los de los campos de la recepción ('cantidad_litros', 'precio_litro'…).
    #
    # Van SIN anotación de tipo y como tuplas a propósito: una anotación que no sea
    # Mapped[...] hace que el declarativo de SQLAlchemy 2.0 intente mapear el
    # atributo y reviente al cargar la clase, y un [] de clase sería un default
    # mutable compartido por todas las instancias.
    campos_bloqueados = ()
    campos_editables = ()
    candado_aviso = None
