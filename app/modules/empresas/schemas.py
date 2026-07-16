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
