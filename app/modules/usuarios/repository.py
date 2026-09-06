import uuid

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
    # El superadmin SIN X-Empresa-Id ve el catálogo completo: lo necesita para
    # armar las membresías de un usuario en varias empresas. Por eso no se exige
    # contexto de empresa aunque el modelo tenga empresa_id.
    tenant_required = False

    def base_query(self) -> Select:
        """Una quesera ve SUS roles y las PLANTILLAS de sistema (empresa_id NULL).

        Las plantillas se ven a propósito: son el catálogo compartido que la
        siembra mantiene y del que todo el mundo cuelga a sus usuarios. Lo que
        NO se ve nunca es un rol de la otra quesera — ni en el listado, ni al
        pedirlo por id, ni al comprobar si un nombre está libre (todo pasa por
        este base_query).
        """
        stmt = select(Rol).where(Rol.deleted_at.is_(None))
        if self.empresa_id is not None:
            stmt = stmt.where(or_(Rol.empresa_id == self.empresa_id, Rol.empresa_id.is_(None)))
        return stmt

    def get_by_nombre(self, nombre: str) -> Rol | None:
        """Rol por nombre DENTRO del alcance de la empresa (propios + plantillas)."""
        return self.db.scalars(self.base_query().where(Rol.nombre == nombre)).first()

    def nombre_ocupado(self, nombre: str, *, exclude_id: uuid.UUID | None = None) -> Rol | None:
        """Devuelve el rol que ya tiene ese nombre, INCLUIDOS los borrados en suave.

        Hace falta aparte de `exists_where` porque los índices únicos no filtran
        `deleted_at`: un rol que el dueño borró sigue ocupando su nombre y volver
        a usarlo reventaría el INSERT contra la base. Mirarlo aquí convierte un
        error 500 en un mensaje que se entiende.
        """
        stmt = select(Rol).where(Rol.nombre == nombre)
        if self.empresa_id is not None:
            stmt = stmt.where(or_(Rol.empresa_id == self.empresa_id, Rol.empresa_id.is_(None)))
        if exclude_id is not None:
            stmt = stmt.where(Rol.id != exclude_id)
        return self.db.scalars(stmt.limit(1)).first()


class PermisoRepository(BaseRepository[Permiso]):
    model = Permiso
    search_fields = ("modulo", "accion")


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    def get_by_jti(self, jti: str) -> RefreshToken | None:
        return self.db.scalars(select(RefreshToken).where(RefreshToken.jti == jti)).first()


class LoginAuditRepository(BaseRepository[LoginAudit]):
    """Registro de ingresos y de intentos fallidos.

    LA TABLA NO GUARDA LA EMPRESA, Y NO SE LE INVENTA UNA. No podría: al iniciar
    sesión todavía no hay empresa elegida —la cuenta es una sola y la quesera se
    escoge después, con el header X-Empresa-Id—, así que rellenar una columna con
    la empresa principal del usuario sería anotar un dato que nunca ocurrió, y
    para los usuarios que trabajan en las dos queseras sería directamente falso.

    Se filtra por MEMBRESÍA, que es un dato que sí existe: una quesera ve los
    ingresos de la gente que tiene un rol en ella. Consecuencias, dichas de
    frente porque el dueño las va a notar:

    · quien es miembro de las DOS queseras aparece en las listas de las dos. Es
      verdad: esa persona entró, y las dos la tienen contratada.
    · los intentos contra un usuario que NO EXISTE (usuario_id NULL: alguien
      tecleando nombres a ver si pega) no le pertenecen a ninguna quesera y solo
      los ve el Administrador General sin header. Se avisa porque es la única
      cifra que el dueño puede echar de menos.
    · si a alguien se le quitan todas sus membresías, sus ingresos dejan de
      verse. La alternativa —congelar la empresa en la fila— es justamente el
      dato inventado que no se quiso poner.
    """

    model = LoginAudit
    # El superadmin sin X-Empresa-Id revisa toda la instalación.
    tenant_required = False

    def base_query(self) -> Select:
        stmt = select(LoginAudit).where(LoginAudit.deleted_at.is_(None))
        if self.empresa_id is not None:
            stmt = stmt.where(
                select(UsuarioRol.id)
                .where(
                    UsuarioRol.usuario_id == LoginAudit.usuario_id,
                    UsuarioRol.empresa_id == self.empresa_id,
                )
                .exists()
            )
        return stmt
