import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AuditMixin
from app.core.database import Base

usuario_roles = Table(
    "usuario_roles",
    Base.metadata,
    Column("usuario_id", Uuid, ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True),
    Column("rol_id", Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

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
    # Nullable: el Administrador General no pertenece a una empresa específica
    empresa_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("empresas.id"), index=True)
    sucursal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sucursales.id"))
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bloqueado: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    roles: Mapped[list[Rol]] = relationship(secondary=usuario_roles, lazy="selectin")

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombre} {self.apellido}".strip()


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
