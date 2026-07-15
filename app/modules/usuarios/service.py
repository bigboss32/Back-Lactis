import uuid
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, ConflictError, ForbiddenError, NotFoundError
from app.core.permissions import ROL_SUPERADMIN
from app.core.security import hash_password
from app.modules.usuarios.models import Permiso, Rol, Usuario
from app.modules.usuarios.repository import PermisoRepository, RolRepository, UsuarioRepository
from app.utils.files import save_upload


class UsuarioService(BaseService[Usuario]):
    repository_cls = UsuarioRepository
    modulo = "usuarios"

    # Los usuarios se filtran por la empresa del contexto (salvo superadmin,
    # que ve todos). BaseRepository ya aplica empresa_id automáticamente.

    def _validar_unicos(self, data: dict[str, Any], exclude_id: uuid.UUID | None = None) -> None:
        repo = UsuarioRepository(self.db)  # sin filtro tenant: unicidad global
        if data.get("username") and repo.exists_where(
            Usuario.username == data["username"], exclude_id=exclude_id
        ):
            raise ConflictError(f"El username '{data['username']}' ya está en uso")
        if data.get("correo") and repo.exists_where(
            Usuario.correo == data["correo"], exclude_id=exclude_id
        ):
            raise ConflictError(f"El correo '{data['correo']}' ya está registrado")

    def _cargar_roles(self, rol_ids: list[uuid.UUID]) -> list[Rol]:
        if not rol_ids:
            return []
        roles = list(self.db.scalars(select(Rol).where(Rol.id.in_(rol_ids), Rol.deleted_at.is_(None))))
        if len(roles) != len(set(rol_ids)):
            raise NotFoundError("Uno o más roles no existen")
        if any(r.nombre == ROL_SUPERADMIN for r in roles) and not self.ctx.is_superadmin:
            raise ForbiddenError("Solo un Administrador General puede asignar ese rol")
        return roles

    def crear(self, payload: Any) -> Usuario:
        data = payload.model_dump(exclude_unset=True)
        rol_ids = data.pop("rol_ids", [])
        password = data.pop("password")
        self._validar_unicos(data)
        data["hashed_password"] = hash_password(password)
        # Un admin de empresa solo crea usuarios dentro de su propia empresa
        if not self.ctx.is_superadmin:
            data["empresa_id"] = self.ctx.empresa_id
        elif not data.get("empresa_id"):
            data["empresa_id"] = self.ctx.empresa_id
        data["created_by"] = self.ctx.user_id
        data["updated_by"] = self.ctx.user_id
        usuario = Usuario(**data)
        usuario.roles = self._cargar_roles(rol_ids)
        self.repo.add(usuario)
        despues = serialize_entity(usuario)
        despues.pop("hashed_password", None)
        self._audit("crear", usuario.id, None, despues)
        return usuario

    def actualizar(self, entity_id: uuid.UUID, payload: Any) -> Usuario:
        data = payload.model_dump(exclude_unset=True) if not isinstance(payload, dict) else dict(payload)
        self._validar_unicos(data, exclude_id=entity_id)
        obj = self.repo.get_or_fail(entity_id)
        antes = serialize_entity(obj)
        antes.pop("hashed_password", None)
        data["updated_by"] = self.ctx.user_id
        obj = self.repo.update(obj, data)
        despues = serialize_entity(obj)
        despues.pop("hashed_password", None)
        self._audit("editar", obj.id, antes, despues)
        return obj

    def asignar_roles(self, entity_id: uuid.UUID, rol_ids: list[uuid.UUID]) -> Usuario:
        usuario = self.repo.get_or_fail(entity_id)
        antes = [r.nombre for r in usuario.roles]
        usuario.roles = self._cargar_roles(rol_ids)
        self.db.flush()
        self._audit(
            "editar", usuario.id, {"roles": antes}, {"roles": [r.nombre for r in usuario.roles]}
        )
        return usuario

    def bloquear(self, entity_id: uuid.UUID, bloquear: bool) -> Usuario:
        usuario = self.repo.get_or_fail(entity_id)
        if usuario.id == self.ctx.user_id:
            raise BusinessError("No puede bloquearse a sí mismo")
        usuario.bloqueado = bloquear
        if not bloquear:
            usuario.intentos_fallidos = 0
        self.db.flush()
        self._audit("editar", usuario.id, {"bloqueado": not bloquear}, {"bloqueado": bloquear})
        return usuario

    def restablecer_password(self, entity_id: uuid.UUID, password: str) -> Usuario:
        usuario = self.repo.get_or_fail(entity_id)
        usuario.hashed_password = hash_password(password)
        usuario.intentos_fallidos = 0
        usuario.bloqueado = False
        self.db.flush()
        self._audit("editar", usuario.id, None, {"password": "restablecida"})
        return usuario

    def subir_foto(self, entity_id: uuid.UUID, file: UploadFile) -> Usuario:
        usuario = self.repo.get_or_fail(entity_id)
        usuario.foto_url = save_upload(file, empresa_id=usuario.empresa_id, subdir="fotos")
        self.db.flush()
        return usuario


class RolService(BaseService[Rol]):
    repository_cls = RolRepository
    modulo = "roles"

    def _cargar_permisos(self, permiso_ids: list[uuid.UUID]) -> list[Permiso]:
        if not permiso_ids:
            return []
        permisos = list(
            self.db.scalars(select(Permiso).where(Permiso.id.in_(permiso_ids), Permiso.deleted_at.is_(None)))
        )
        if len(permisos) != len(set(permiso_ids)):
            raise NotFoundError("Uno o más permisos no existen")
        return permisos

    def crear(self, payload: Any) -> Rol:
        data = payload.model_dump(exclude_unset=True)
        permiso_ids = data.pop("permiso_ids", [])
        if self.repo.exists_where(Rol.nombre == data["nombre"]):
            raise ConflictError(f"Ya existe un rol '{data['nombre']}'")
        rol = Rol(**data, created_by=self.ctx.user_id, updated_by=self.ctx.user_id)
        rol.permisos = self._cargar_permisos(permiso_ids)
        self.repo.add(rol)
        self._audit("crear", rol.id, None, {"nombre": rol.nombre, "permisos": len(rol.permisos)})
        return rol

    def validar_actualizar(self, obj: Rol, data: dict[str, Any]) -> None:
        if obj.es_sistema and data.get("nombre") and data["nombre"] != obj.nombre:
            raise BusinessError("No se puede renombrar un rol de sistema")

    def validar_eliminar(self, obj: Rol) -> None:
        if obj.es_sistema:
            raise BusinessError("No se puede eliminar un rol de sistema")

    def asignar_permisos(self, entity_id: uuid.UUID, permiso_ids: list[uuid.UUID]) -> Rol:
        rol = self.repo.get_or_fail(entity_id)
        if rol.nombre == ROL_SUPERADMIN:
            raise BusinessError("El Administrador General tiene todos los permisos implícitos")
        antes = len(rol.permisos)
        rol.permisos = self._cargar_permisos(permiso_ids)
        self.db.flush()
        self._audit("editar", rol.id, {"permisos": antes}, {"permisos": len(rol.permisos)})
        return rol


class PermisoService(BaseService[Permiso]):
    repository_cls = PermisoRepository
    modulo = "roles"
