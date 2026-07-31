import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin
from app.core.database import Base
from app.modules.empresas.models import Empresa

rol_permisos = Table(
    "rol_permisos",
    Base.metadata,
    Column("rol_id", Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permiso_id", Uuid, ForeignKey("permisos.id", ondelete="CASCADE"), primary_key=True),
)


class Permiso(AuditMixin, Base):
    __tablename__ = "permisos"
    __table_args__ = (UniqueConstraint("modulo", "accion", name="uq_permiso_modulo_accion"),)

    modulo: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    accion: Mapped[str] = mapped_column(String(30), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(200))


class Rol(AuditMixin, Base):
    __tablename__ = "roles"

    nombre: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(200))
    es_sistema: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    permisos: Mapped[list[Permiso]] = relationship(secondary=rol_permisos, lazy="selectin")


class Usuario(AuditMixin, Base):
    __tablename__ = "usuarios"

    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    apellido: Mapped[str] = mapped_column(String(100), nullable=False)
    documento: Mapped[str | None] = mapped_column(String(30), index=True)
    correo: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    telefono: Mapped[str | None] = mapped_column(String(30))
    username: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    foto_url: Mapped[str | None] = mapped_column(String(300))
    # Nullable: el Administrador General no pertenece a una empresa específica.
    # Para los demás usuarios es la empresa PRINCIPAL (a la que entran sin
    # header X-Empresa-Id); la membresía completa vive en `asignaciones`.
    empresa_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("empresas.id"), index=True)
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bloqueado: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    asignaciones: Mapped[list["UsuarioRol"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def roles(self) -> list[Rol]:
        """Roles del usuario en TODAS sus empresas, sin duplicados.

        Property de COMPATIBILIDAD con el modelo anterior (roles globales por
        usuario). El setter reemplaza todas las asignaciones creando una fila
        `UsuarioRol` por rol anclada a `self.empresa_id`, así que la empresa
        PRINCIPAL debe estar asignada ANTES de asignar los roles (si es None,
        las filas quedan globales — el caso del superadmin del seed). Todos los
        call sites actuales (seeds, conftest y tests) cumplen ese orden.
        """
        vistos: set[uuid.UUID] = set()
        resultado: list[Rol] = []
        for asignacion in self.asignaciones:
            rol = asignacion.rol
            if rol is None or rol.id in vistos:
                continue
            vistos.add(rol.id)
            resultado.append(rol)
        return resultado

    @roles.setter
    def roles(self, roles: list[Rol]) -> None:
        self.asignaciones = [UsuarioRol(rol=rol, empresa_id=self.empresa_id) for rol in roles]

    def roles_en(self, empresa_id: uuid.UUID | None) -> list[Rol]:
        """Roles vigentes EN una empresa: sus filas de esa empresa + las globales.

        Con empresa_id None solo devuelve las globales (el superadmin sin header).
        """
        vistos: set[uuid.UUID] = set()
        resultado: list[Rol] = []
        for asignacion in self.asignaciones:
            if asignacion.empresa_id is not None and asignacion.empresa_id != empresa_id:
                continue
            rol = asignacion.rol
            if rol is None or rol.id in vistos:
                continue
            vistos.add(rol.id)
            resultado.append(rol)
        return resultado

    @property
    def empresas_ids(self) -> frozenset[uuid.UUID]:
        """Empresas de las que el usuario es miembro (derivadas de sus roles)."""
        return frozenset(a.empresa_id for a in self.asignaciones if a.empresa_id is not None)

    @property
    def empresas_nombres(self) -> list[str]:
        """Nombres de las empresas del usuario, ordenados alfabéticamente."""
        return sorted({a.empresa.nombre for a in self.asignaciones if a.empresa is not None})

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombre} {self.apellido}".strip()


class UsuarioRol(Base):
    """Asignación de un rol a un usuario EN una empresa (association object).

    Una fila = "este rol en esta empresa". empresa_id NULL = rol global (solo
    el Administrador General). La unicidad se garantiza con dos índices únicos
    parciales (postgresql_where + sqlite_where, válidos en ambos motores):
    uno para las filas con empresa y otro para las globales.
    """

    __tablename__ = "usuario_roles"
    __table_args__ = (
        Index(
            "uq_usuario_rol_empresa",
            "usuario_id",
            "rol_id",
            "empresa_id",
            unique=True,
            postgresql_where=text("empresa_id IS NOT NULL"),
            sqlite_where=text("empresa_id IS NOT NULL"),
        ),
        Index(
            "uq_usuario_rol_global",
            "usuario_id",
            "rol_id",
            unique=True,
            postgresql_where=text("empresa_id IS NULL"),
            sqlite_where=text("empresa_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rol_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    empresa_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("empresas.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    usuario: Mapped[Usuario] = relationship(back_populates="asignaciones")
    rol: Mapped[Rol] = relationship(lazy="joined")
    empresa: Mapped[Empresa | None] = relationship(lazy="joined")


class RefreshToken(AuditMixin, Base):
    __tablename__ = "refresh_tokens"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetToken(AuditMixin, Base):
    __tablename__ = "password_reset_tokens"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginAudit(AuditMixin, Base):
    __tablename__ = "login_audits"

    usuario_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    username_intentado: Mapped[str | None] = mapped_column(String(150))
    exito: Mapped[bool] = mapped_column(Boolean, default=False)
    motivo: Mapped[str | None] = mapped_column(String(200))
    ip: Mapped[str | None] = mapped_column(String(60))
    user_agent: Mapped[str | None] = mapped_column(String(300))
