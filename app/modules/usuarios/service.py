import uuid
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select

from app.common.service import BaseService, serialize_entity
from app.core.exceptions import BusinessError, ConflictError, ForbiddenError, NotFoundError
from app.core.pagination import PageParams
from app.core.permissions import ROL_SUPERADMIN
from app.core.security import hash_password
from app.modules.empresas.models import Empresa
from app.modules.usuarios.models import Permiso, Rol, Usuario, UsuarioRol
from app.modules.usuarios.repository import PermisoRepository, RolRepository, UsuarioRepository
from app.utils.files import save_upload


class UsuarioService(BaseService[Usuario]):
    repository_cls = UsuarioRepository
    modulo = "usuarios"

    # Los usuarios se filtran por MEMBRESÍA en la empresa del contexto (salvo
    # superadmin sin header, que ve todos): ver UsuarioRepository.base_query.
    #
    # OJO: bloquear, restablecer la contraseña o editar los datos de un usuario
    # desde UNA empresa afecta su cuenta GLOBAL en todas sus empresas
    # (consecuencia del modelo de cuenta única).

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
        # El rol de superadmin (solo asignable por otro superadmin) es GLOBAL
        # (fila sin empresa); el resto queda anclado a la empresa resuelta
        usuario.asignaciones = [
            UsuarioRol(
                rol=rol,
                empresa_id=None if rol.nombre == ROL_SUPERADMIN else usuario.empresa_id,
            )
            for rol in self._cargar_roles(rol_ids)
        ]
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
        """Reemplaza los roles del usuario EN la empresa del contexto.

        Las filas de otras empresas no se tocan. El rol global de superadmin lo
        puede materializar/quitar un superadmin desde aquí, con guard para que
        no se lo quite a sí mismo.
        """
        if self.ctx.empresa_id is None:
            raise BusinessError(
                "Asignar roles requiere contexto de empresa: envíe el header X-Empresa-Id"
            )
        # El superadmin gestiona los roles de cualquier usuario, incluso de otro
        # superadmin (que solo tiene la fila global y no es miembro de ninguna
        # empresa, así que el repo scoped por membresía no lo encontraría); los
        # demás solo alcanzan a los miembros de su empresa activa
        repo = UsuarioRepository(self.db) if self.ctx.is_superadmin else self.repo
        usuario = repo.get_or_fail(entity_id)
        roles = self._cargar_roles(rol_ids)
        antes = [r.nombre for r in usuario.roles_en(self.ctx.empresa_id)]

        con_superadmin = any(r.nombre == ROL_SUPERADMIN for r in roles)
        tiene_superadmin = any(
            a.empresa_id is None and a.rol is not None and a.rol.nombre == ROL_SUPERADMIN
            for a in usuario.asignaciones
        )
        quita_superadmin = self.ctx.is_superadmin and tiene_superadmin and not con_superadmin
        if quita_superadmin and usuario.id == self.ctx.user_id:
            raise BusinessError("No puede quitarse a sí mismo el rol de Administrador General")

        # Diff sobre las filas de la empresa activa: se conservan las que siguen,
        # se quitan las que sobran y se agregan solo las nuevas (así los índices
        # únicos no ven duplicados transitorios dentro del mismo flush)
        deseados = {r.id: r for r in roles if r.nombre != ROL_SUPERADMIN}
        for asignacion in list(usuario.asignaciones):
            if asignacion.empresa_id == self.ctx.empresa_id:
                if asignacion.rol_id in deseados:
                    deseados.pop(asignacion.rol_id)
                else:
                    usuario.asignaciones.remove(asignacion)
            elif quita_superadmin and asignacion.empresa_id is None and (
                asignacion.rol is not None and asignacion.rol.nombre == ROL_SUPERADMIN
            ):
                usuario.asignaciones.remove(asignacion)
        for rol in deseados.values():
            usuario.asignaciones.append(UsuarioRol(rol=rol, empresa_id=self.ctx.empresa_id))
        if con_superadmin and not tiene_superadmin:
            rol_super = next(r for r in roles if r.nombre == ROL_SUPERADMIN)
            usuario.asignaciones.append(UsuarioRol(rol=rol_super, empresa_id=None))

        self.db.flush()
        self._audit(
            "editar",
            usuario.id,
            {"roles": antes},
            {"roles": [r.nombre for r in usuario.roles_en(self.ctx.empresa_id)]},
        )
        usuario.roles_ctx = usuario.roles_en(self.ctx.empresa_id)
        return usuario

    # ------------------------------------------------------------- membresías
    def _membresias_de(self, usuario: Usuario) -> list[dict[str, Any]]:
        """Agrupa las asignaciones con empresa en filas {empresa, roles}."""
        por_empresa: dict[uuid.UUID, dict[str, Any]] = {}
        for asignacion in usuario.asignaciones:
            if asignacion.empresa_id is None or asignacion.empresa is None:
                continue
            membresia = por_empresa.setdefault(
                asignacion.empresa_id,
                {
                    "empresa_id": asignacion.empresa_id,
                    "empresa_nombre": asignacion.empresa.nombre,
                    "roles": [],
                },
            )
            rol = asignacion.rol
            if rol is not None and all(r.id != rol.id for r in membresia["roles"]):
                membresia["roles"].append(rol)
        return sorted(por_empresa.values(), key=lambda m: m["empresa_nombre"])

    @staticmethod
    def _membresias_para_auditoria(membresias: list[dict[str, Any]]) -> dict[str, list[str]]:
        return {m["empresa_nombre"]: sorted(r.nombre for r in m["roles"]) for m in membresias}

    def listar_membresias(self, entity_id: uuid.UUID) -> list[dict[str, Any]]:
        if not self.ctx.is_superadmin:
            raise ForbiddenError(
                "Solo el Administrador General puede consultar las empresas de un usuario"
            )
        # Sin filtro tenant: el superadmin consulta al usuario esté donde esté
        usuario = UsuarioRepository(self.db).get_or_fail(entity_id)
        return self._membresias_de(usuario)

    def asignar_membresias(self, entity_id: uuid.UUID, payload: Any) -> list[dict[str, Any]]:
        """Reemplaza las membresías (roles POR empresa) de un usuario.

        Solo superadmin. Se reemplazan únicamente las filas con empresa: las
        globales (el rol de superadmin) se preservan y se gestionan por
        asignar_roles. La empresa principal se puede fijar con
        empresa_principal_id o se reasigna a la primera de la lista si la
        actual dejó de ser miembro.
        """
        if not self.ctx.is_superadmin:
            raise ForbiddenError(
                "Solo el Administrador General puede asignar empresas a un usuario"
            )
        # Sin filtro tenant: el superadmin gestiona al usuario esté donde esté
        usuario = UsuarioRepository(self.db).get_or_fail(entity_id)
        if any(
            a.empresa_id is None and a.rol is not None and a.rol.nombre == ROL_SUPERADMIN
            for a in usuario.asignaciones
        ):
            raise BusinessError(
                "El Administrador General es global y no lleva membresías por empresa"
            )

        membresias = payload.membresias
        if not membresias:
            raise BusinessError("Debe indicar al menos una empresa")
        empresa_ids = [m.empresa_id for m in membresias]
        if len(empresa_ids) != len(set(empresa_ids)):
            raise BusinessError("Hay empresas repetidas en la lista")
        if any(not m.rol_ids for m in membresias):
            raise BusinessError("Cada empresa debe tener al menos un rol")

        empresas_existentes = set(
            self.db.scalars(
                select(Empresa.id).where(Empresa.id.in_(empresa_ids), Empresa.deleted_at.is_(None))
            )
        )
        if len(empresas_existentes) != len(set(empresa_ids)):
            raise NotFoundError("Una o más empresas no existen")
        todos_rol_ids = {rol_id for m in membresias for rol_id in m.rol_ids}
        roles = {
            r.id: r
            for r in self.db.scalars(
                select(Rol).where(Rol.id.in_(todos_rol_ids), Rol.deleted_at.is_(None))
            )
        }
        if len(roles) != len(todos_rol_ids):
            raise NotFoundError("Uno o más roles no existen")
        if any(r.nombre == ROL_SUPERADMIN for r in roles.values()):
            raise BusinessError(
                "El rol Administrador General es global y no se asigna por empresa"
            )
        if payload.empresa_principal_id is not None and payload.empresa_principal_id not in set(
            empresa_ids
        ):
            raise BusinessError("La empresa principal debe estar entre las empresas asignadas")

        antes = self._membresias_de(usuario)
        principal_antes = str(usuario.empresa_id) if usuario.empresa_id else None

        # Diff: se conservan las filas que siguen, se quitan las que sobran y se
        # agregan solo las nuevas (evita duplicados transitorios en los índices
        # únicos dentro del mismo flush). Las globales no se tocan.
        deseadas = {(m.empresa_id, rol_id) for m in membresias for rol_id in m.rol_ids}
        for asignacion in list(usuario.asignaciones):
            if asignacion.empresa_id is None:
                continue
            clave = (asignacion.empresa_id, asignacion.rol_id)
            if clave in deseadas:
                deseadas.discard(clave)
            else:
                usuario.asignaciones.remove(asignacion)
        for empresa_id, rol_id in deseadas:
            usuario.asignaciones.append(UsuarioRol(rol=roles[rol_id], empresa_id=empresa_id))

        if payload.empresa_principal_id is not None:
            usuario.empresa_id = payload.empresa_principal_id
        elif usuario.empresa_id not in set(empresa_ids):
            # La principal dejó de ser miembro: pasa a la primera de la lista
            usuario.empresa_id = membresias[0].empresa_id
        usuario.updated_by = self.ctx.user_id
        self.db.flush()

        despues = self._membresias_de(usuario)
        self._audit(
            "editar",
            usuario.id,
            {"membresias": self._membresias_para_auditoria(antes), "empresa_principal": principal_antes},
            {
                "membresias": self._membresias_para_auditoria(despues),
                "empresa_principal": str(usuario.empresa_id) if usuario.empresa_id else None,
            },
        )
        return despues

    # ------------------------------------------------------ lecturas con contexto
    def _adjuntar_roles_ctx(self, usuario: Usuario) -> None:
        """roles_ctx: atributo transitorio que UsuarioRead lee con prioridad para
        mostrar SOLO los roles de la empresa activa (superadmin sin header ve
        todos). Se asigna SIEMPRE, incluso sin empresa en el contexto: la
        instancia puede venir de la identity map de una sesión compartida con un
        roles_ctx viejo de otra petición, y el fallback del schema a la property
        roles no borra ese residuo."""
        if self.ctx.empresa_id is not None:
            usuario.roles_ctx = usuario.roles_en(self.ctx.empresa_id)
        else:
            usuario.roles_ctx = usuario.roles

    def listar(self, params: PageParams, **kwargs: Any) -> tuple[list[Usuario], int]:
        items, total = super().listar(params, **kwargs)
        for usuario in items:
            self._adjuntar_roles_ctx(usuario)
        return items, total

    def obtener(self, entity_id: uuid.UUID) -> Usuario:
        usuario = super().obtener(entity_id)
        self._adjuntar_roles_ctx(usuario)
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
