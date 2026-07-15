from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.common.repository import BaseRepository
from app.modules.usuarios.models import LoginAudit, Permiso, RefreshToken, Rol, Usuario


class UsuarioRepository(BaseRepository[Usuario]):
    model = Usuario
    search_fields = ("nombre", "apellido", "correo", "username", "documento")
    # El superadmin administra usuarios de todas las empresas sin header
    tenant_required = False

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
