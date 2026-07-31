from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.common.repository import BaseRepository
from app.modules.usuarios.models import (
    LoginAudit,
    Permiso,
    RefreshToken,
    Rol,
    Usuario,
    UsuarioRol,
)


class UsuarioRepository(BaseRepository[Usuario]):
    model = Usuario
    search_fields = ("nombre", "apellido", "correo", "username", "documento")
    # El superadmin administra usuarios de todas las empresas sin header
    tenant_required = False

    def base_query(self) -> Select:
        """Scoping por MEMBRESÍA y no por Usuario.empresa_id: el admin de una
        empresa ve/edita a todo el que tenga un rol en ella, aunque su empresa
        principal sea otra. Sin este override, el admin de A no vería a un
        miembro cuya principal es B.
        """
        stmt = select(Usuario).where(Usuario.deleted_at.is_(None))
        if self.empresa_id is not None:
            stmt = stmt.where(
                select(UsuarioRol.id)
                .where(
                    UsuarioRol.usuario_id == Usuario.id,
                    UsuarioRol.empresa_id == self.empresa_id,
                )
                .exists()
            )
        return stmt

    def get_by_username_or_email(self, identificador: str) -> Usuario | None:
        stmt = select(Usuario).where(
            Usuario.deleted_at.is_(None),
            or_(Usuario.username == identificador, Usuario.correo == identificador),
        )
        return self.db.scalars(stmt).first()


class RolRepository(BaseRepository[Rol]):
    model = Rol
    search_fields = ("nombre", "descripcion")

    def get_by_nombre(self, nombre: str) -> Rol | None:
        return self.db.scalars(
            select(Rol).where(Rol.nombre == nombre, Rol.deleted_at.is_(None))
        ).first()


class PermisoRepository(BaseRepository[Permiso]):
    model = Permiso
    search_fields = ("modulo", "accion")


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    def get_by_jti(self, jti: str) -> RefreshToken | None:
        return self.db.scalars(select(RefreshToken).where(RefreshToken.jti == jti)).first()


class LoginAuditRepository(BaseRepository[LoginAudit]):
    model = LoginAudit
