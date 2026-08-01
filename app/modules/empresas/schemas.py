from datetime import date
from decimal import Decimal

from pydantic import EmailStr, Field

from app.common.schemas import AuditRead, BaseSchema


class EmpresaCreate(BaseSchema):
    nombre: str = Field(min_length=2, max_length=150)
    nit: str = Field(min_length=3, max_length=30)
    direccion: str | None = None
    ciudad: str | None = None
    departamento: str | None = None
    pais: str = "Colombia"
    telefono: str | None = None
    correo: EmailStr | None = None


class EmpresaUpdate(BaseSchema):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    nit: str | None = Field(default=None, min_length=3, max_length=30)
    direccion: str | None = None
    ciudad: str | None = None
    departamento: str | None = None
    pais: str | None = None
    telefono: str | None = None
    correo: EmailStr | None = None
    estado: str | None = None


class ReinicioEmpresa(BaseSchema):
    """Confirmación para reiniciar (borrar) los datos de una empresa."""

    confirmacion: str = Field(min_length=1, description="Debe coincidir con el nombre de la empresa")


class SuscripcionEmpresaUpdate(BaseSchema):
    """Ajuste de la suscripción de una empresa — solo superadmin.

    Los tres campos son opcionales y los null EXPLÍCITOS valen: tarifa_mensual
    null vuelve a la tarifa global y pagada_hasta null regresa la empresa al
    esquema de prueba (contado desde su creación).
    """

    tarifa_mensual: Decimal | None = Field(default=None, ge=0)
    exenta: bool | None = None
    # Adelantarla "regala" días; atrasarla fuerza el cobro/bloqueo
    pagada_hasta: date | None = None


class EmpresaRead(AuditRead):
    nombre: str
    nit: str
    direccion: str | None
    ciudad: str | None
    departamento: str | None
    pais: str
    telefono: str | None
    correo: str | None
    logo_url: str | None
    # Suscripción (los gestiona el superadmin por PUT /empresas/{id}/suscripcion)
    tarifa_mensual: Decimal | None
    exenta: bool
    pagada_hasta: date | None
