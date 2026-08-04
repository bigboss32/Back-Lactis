"""Seed idempotente: catálogo de permisos, roles del sistema, superadmin
y (opcional) empresa demo 'Queso La Marginal de la Selva' con los datos
reales del negocio (rutas, veredas, proveedores y precios del Excel origen).

Ejecutar:  python -m app.seeds.seed
"""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models_registry  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging_config import get_logger
from app.core.permissions import ACCIONES, MODULOS, ROL_SUPERADMIN, ROLES_SISTEMA
from app.core.security import hash_password
from app.modules.empresas.models import Empresa
from app.modules.gastos.models import CATEGORIAS_DEFECTO, CategoriaGasto
from app.modules.inventario.models import Producto
from app.modules.produccion.models import TipoQueso
from app.modules.proveedores.models import Proveedor
from app.modules.rutas.models import Ruta
from app.modules.sucursales.models import Sucursal
from app.modules.transportadores.models import Transportador, TransportadorRuta
from app.modules.usuarios.models import Permiso, Rol, Usuario

logger = get_logger("seed")

# Roles de un solo módulo: se declaran aquí y no en ROLES_SISTEMA (app/core/
# permissions.py) porque no forman parte del esquema RBAC general del ERP; son
# roles para clientes que contratan un módulo suelto. Se siembran igual que los
# de sistema (es_sistema=True, para que no se puedan renombrar ni borrar).
ROLES_POR_MODULO: tuple[str, ...] = ("Reventa",)

TODOS_LOS_ROLES: tuple[str, ...] = (*ROLES_SISTEMA, *ROLES_POR_MODULO)

# Permisos por rol de sistema: {rol: {(modulo, accion), ...}} — el superadmin no
# necesita filas porque el chequeo lo aprueba de forma implícita.
CONSULTA_TODOS = {(m, "consultar") for m in MODULOS}

ROLES_PERMISOS: dict[str, set[tuple[str, str]]] = {
    "Administrador Empresa": {
        (m, a) for m in MODULOS for a in ACCIONES if not (m == "empresas" and a in ("crear", "eliminar"))
    },
    "Contador": CONSULTA_TODOS
    | {
        (m, a)
        for m in ("contabilidad", "gastos", "caja", "bancos", "reportes", "auditoria")
        for a in ("consultar", "exportar", "imprimir")
    }
    | {("gastos", "crear"), ("gastos", "editar"), ("caja", "crear"), ("caja", "administrar"),
       ("bancos", "crear"), ("bancos", "administrar")},
    "Supervisor": CONSULTA_TODOS
    | {
        ("recepcion", "crear"), ("recepcion", "editar"), ("produccion", "crear"),
        ("produccion", "editar"), ("liquidaciones", "crear"), ("liquidaciones", "imprimir"),
        ("reportes", "exportar"), ("inventario", "crear"), ("notificaciones", "administrar"),
        ("transporte", "crear"), ("transporte", "editar"),
    },
    "Auxiliar": {
        ("recepcion", "crear"), ("recepcion", "consultar"), ("proveedores", "consultar"),
        ("transportadores", "consultar"), ("rutas", "consultar"), ("inventario", "consultar"),
        ("inventario", "crear"), ("notificaciones", "consultar"),
        ("transporte", "crear"), ("transporte", "consultar"),
    },
    "Producción": {
        ("produccion", "crear"), ("produccion", "editar"), ("produccion", "consultar"),
        ("inventario", "crear"), ("inventario", "consultar"), ("notificaciones", "consultar"),
    },
    "Compras": {
        (m, a)
        for m in ("proveedores", "transportadores", "rutas", "recepcion", "liquidaciones", "reventa")
        for a in ("crear", "editar", "consultar", "exportar", "imprimir")
    }
    | {("gastos", "crear"), ("gastos", "consultar"), ("notificaciones", "consultar")},
    "Ventas": {
        (m, a)
        for m in ("clientes", "ventas", "reventa")
        for a in ("crear", "editar", "consultar", "exportar", "imprimir")
    }
    | {("caja", "crear"), ("caja", "consultar"), ("inventario", "consultar"),
       ("notificaciones", "consultar")},
    "Consulta": CONSULTA_TODOS,
    # Para el cliente que solo compra y revende queso y no usa el resto del ERP.
    # Lleva TODAS las acciones de 'reventa', incluidas 'eliminar' y 'administrar'
    # (la que anula compras y ventas): es el dueño de su propio negocio, nadie más
    # va a corregirle los registros y el daño posible no sale de su módulo y su
    # empresa.
    #
    # Y 'suscripcion' completo, que es lo ÚNICO que se lleva de Administración:
    # este cliente es quien paga la mensualidad. Sin esto el menú no le mostraba
    # por dónde pagar y, cuando la suscripción se venciera, quedaría bloqueado
    # sin manera de arreglarlo él mismo — dependiendo de que alguien con más
    # permisos entrara a pagarle. Van las tres acciones porque las tres son de
    # pagar: 'consultar' para ver cómo va, 'crear' para pagar (tarjeta o PSE) y
    # 'administrar' para guardar la tarjeta del cobro automático; sin esa última
    # tendría que pagar a mano todos los meses.
    #
    # Lo demás de Administración NO va: nada de usuarios, roles, empresas,
    # empleados, sucursales ni auditoría. Tampoco contabilidad ni reportes (de
    # este último cuelga la pantalla de Estadísticas del ERP). Fuera de eso,
    # solo sus notificaciones, como los demás roles operativos.
    "Reventa": {("reventa", a) for a in ACCIONES}
    | {("suscripcion", a) for a in ("consultar", "crear", "administrar")}
    | {("notificaciones", "consultar")},
}

# Datos reales tomados de la hoja 'LITROS Y TRANSPORTE' de la 1ª quincena de junio
RUTAS_DEMO = [
    ("Ruta Granada Stella 1", "Granada"),
    ("Ruta Porvenir Yoiner", "Porvenir"),
    ("Ruta La Granada Eduin", "La Granada"),
    ("Ruta Guacamayas", "Guacamayas"),
]

TRANSPORTADORES_DEMO = [
    ("Stella", "Ruta Granada Stella 1", Decimal("124.93")),
    ("Yoiner", "Ruta Porvenir Yoiner", Decimal("94.03")),
    ("Eduin", "Ruta La Granada Eduin", Decimal("130")),
]

PROVEEDORES_DEMO = [
    # (nombre, vereda, precio_litro, ruta)
    ("Moisés", "Porvenir", Decimal("1500"), "Ruta Porvenir Yoiner"),
    ("Marlion", "Porvenir", Decimal("1700"), "Ruta Porvenir Yoiner"),
    ("Henri C", "Granada", Decimal("1700"), "Ruta Granada Stella 1"),
    ("Irene", "Granada", Decimal("1600"), "Ruta Granada Stella 1"),
    ("Libardo", "Granada", Decimal("1800"), "Ruta Granada Stella 1"),
    ("Yubigildo", "Veracruz", Decimal("1700"), "Ruta Granada Stella 1"),
    ("Jaime", "Veracruz", Decimal("1500"), "Ruta Granada Stella 1"),
    ("Estella", "Veracruz", Decimal("1600"), "Ruta Granada Stella 1"),
    ("Arturo V", "Veracruz", Decimal("1600"), "Ruta Granada Stella 1"),
    ("Yicela", "Veracruz", Decimal("1300"), "Ruta Granada Stella 1"),
    ("Serafín", "Guacamayas", Decimal("1500"), "Ruta Guacamayas"),
    ("Arturo P", "Guacamayas", Decimal("1900"), "Ruta Guacamayas"),
    ("Pedro", "Guacamayas", Decimal("1700"), "Ruta Guacamayas"),
    ("Fidel", "Guacamayas", Decimal("1700"), "Ruta Guacamayas"),
    ("Alexander", "Granada", Decimal("1900"), "Ruta La Granada Eduin"),
    ("Mojino", "Granada", Decimal("1600"), "Ruta La Granada Eduin"),
]

PRODUCTOS_DEMO = [
    ("Leche cruda", "leche", "litro", Decimal("100")),
    ("Sal", "insumo", "kg", Decimal("25")),
    ("Cuajo", "insumo", "unidad", Decimal("5")),
    ("Bolsas", "empaque", "unidad", Decimal("100")),
    ("Etiquetas", "empaque", "unidad", Decimal("100")),
    ("Queso Costeño", "producto_terminado", "kg", Decimal("50")),
    ("Queso Criollo", "producto_terminado", "kg", Decimal("50")),
]


def seed_permisos(db: Session) -> dict[tuple[str, str], Permiso]:
    existentes = {(p.modulo, p.accion): p for p in db.scalars(select(Permiso)).all()}
    for modulo in MODULOS:
        for accion in ACCIONES:
            if (modulo, accion) not in existentes:
                permiso = Permiso(
                    modulo=modulo, accion=accion, descripcion=f"Puede {accion} en {modulo}"
                )
                db.add(permiso)
                existentes[(modulo, accion)] = permiso
    db.flush()
    return existentes


def seed_roles(db: Session, permisos: dict[tuple[str, str], Permiso]) -> dict[str, Rol]:
    """Crea los roles de sistema que falten y les sincroniza sus permisos.

    NUNCA toca un rol que no sea de sistema, aunque se llame igual que uno de la
    lista de siembra. Los roles son GLOBALES (Rol no tiene empresa_id) y
    Rol.nombre es UNIQUE en toda la base, así que un nombre que aquí se siembra
    puede chocar con uno que un administrador de cualquier empresa ya creó a
    mano —"Reventa" es el ejemplo evidente—. Sincronizarle los permisos de la
    lista sería una ESCALADA DE PRIVILEGIOS silenciosa: un rol de solo lectura
    pasaría, en un despliegue cualquiera, a poder anular y eliminar, y con él
    todos los usuarios que ya lo tuvieran asignado. Cuando pasa, el rol del
    cliente se deja EXACTAMENTE como está y se avisa por el log.

    Devuelve {nombre: rol} con los roles utilizables (existentes y vivos). Puede
    faltar algún nombre si el rol está borrado lógicamente, así que quien lo
    consuma debe usar .get() y no indexar a ciegas.
    """
    # Se cargan TODOS los roles, incluidos los borrados lógicamente: el borrado
    # de roles es SOFT (BaseRepository.soft_delete pone deleted_at y estado
    # 'inactivo'; la fila se queda) y el UNIQUE de roles.nombre es de columna, no
    # filtra por deleted_at. O sea: una fila borrada SIGUE ocupando el nombre y
    # crear otro rol con él reventaría el INSERT. Por eso hay que verlas aquí,
    # aunque a efectos de sincronizar permisos no cuenten como existentes.
    existentes: dict[str, Rol] = {r.nombre: r for r in db.scalars(select(Rol)).all()}

    roles: dict[str, Rol] = {}
    # Solo los roles de sistema vivos reciben la sincronización de permisos.
    a_sincronizar: list[str] = []

    for nombre in TODOS_LOS_ROLES:
        rol = existentes.get(nombre)

        if rol is None:
            rol = Rol(nombre=nombre, descripcion=f"Rol de sistema: {nombre}", es_sistema=True)
            db.add(rol)
            roles[nombre] = rol
            a_sincronizar.append(nombre)
            continue

        if not rol.es_sistema:
            # Se devuelve igual (existe y está vivo: quien pregunte por ese
            # nombre debe encontrarlo), pero no se le toca ni un permiso.
            roles[nombre] = rol
            logger.warning(
                "SIEMBRA OMITIDA — rol '%s': ya existe un rol de USUARIO con ese nombre "
                "(id=%s, es_sistema=False, %d permiso(s)). NO se sembró el rol de sistema y NO "
                "se le añadió ningún permiso al rol existente, para no ampliar en silencio lo "
                "que pueden hacer los usuarios que ya lo tienen asignado. Si hace falta el rol "
                "de sistema, renombre primero el rol de usuario y vuelva a ejecutar la siembra.",
                nombre,
                rol.id,
                len(rol.permisos),
            )
            continue

        if rol.deleted_at is not None:
            # No se recrea: chocaría con el UNIQUE del nombre. No se resucita
            # tampoco: borrarlo fue una decisión de alguien, no de la siembra.
            logger.warning(
                "SIEMBRA OMITIDA — rol de sistema '%s': está borrado lógicamente (id=%s, "
                "deleted_at=%s). No se le sincronizan permisos ni se vuelve a crear, porque el "
                "UNIQUE de roles.nombre no distingue filas borradas. Restáurelo a mano "
                "(deleted_at=NULL) si lo necesita.",
                nombre,
                rol.id,
                rol.deleted_at,
            )
            continue

        roles[nombre] = rol
        a_sincronizar.append(nombre)

    db.flush()
    # Sincronización por unión: si aparecen módulos nuevos en el catálogo,
    # los roles de sistema existentes reciben sus permisos en el siguiente seed
    for nombre in a_sincronizar:
        claves = ROLES_PERMISOS.get(nombre)
        if not claves:
            continue  # el superadmin no lleva filas: su chequeo es implícito
        rol = roles[nombre]
        actuales = {(p.modulo, p.accion) for p in rol.permisos}
        faltantes = [permisos[clave] for clave in claves if clave in permisos and clave not in actuales]
        if faltantes:
            rol.permisos = list(rol.permisos) + faltantes
    db.flush()
    return roles


def seed_superadmin(db: Session, roles: dict[str, Rol]) -> Usuario:
    admin = db.scalars(
        select(Usuario).where(Usuario.username == settings.FIRST_ADMIN_USERNAME)
    ).first()
    if admin is None:
        admin = Usuario(
            nombre="Administrador",
            apellido="General",
            correo=settings.FIRST_ADMIN_EMAIL,
            username=settings.FIRST_ADMIN_USERNAME,
            hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
        )
        # .get(): seed_roles puede no devolver un nombre si el rol está borrado
        # lógicamente. Mejor un superadmin sin rol y un aviso en el log que una
        # siembra que revienta con KeyError a mitad de un despliegue.
        rol_superadmin = roles.get(ROL_SUPERADMIN)
        if rol_superadmin is None:
            logger.warning(
                "El rol '%s' no está disponible: el superadmin se crea SIN rol y hay que "
                "asignárselo a mano.",
                ROL_SUPERADMIN,
            )
        admin.roles = [rol_superadmin] if rol_superadmin is not None else []
        db.add(admin)
        db.flush()
        logger.info("Superadmin creado: %s", admin.username)
    return admin


def seed_empresa_demo(db: Session, roles: dict[str, Rol]) -> None:
    empresa = db.scalars(select(Empresa).where(Empresa.nit == "900000000-1")).first()
    if empresa is not None:
        return
    empresa = Empresa(
        nombre="Queso La Marginal de la Selva",
        nit="900000000-1",
        ciudad="San José del Guaviare",
        departamento="Guaviare",
        pais="Colombia",
    )
    db.add(empresa)
    db.flush()

    db.add(Sucursal(empresa_id=empresa.id, nombre="Planta Principal", tipo="planta"))
    for nombre in ("Centro Acopio Granada", "Centro Acopio San Luis", "Centro Acopio Guacamayas"):
        db.add(Sucursal(empresa_id=empresa.id, nombre=nombre, tipo="centro_acopio"))

    rutas: dict[str, Ruta] = {}
    for nombre, municipio in RUTAS_DEMO:
        ruta = Ruta(empresa_id=empresa.id, nombre=nombre, municipio=municipio)
        db.add(ruta)
        rutas[nombre] = ruta
    db.flush()

    for nombre, ruta_nombre, tarifa in TRANSPORTADORES_DEMO:
        # La tarifa va en la ruta Y en la general: en la ruta porque es donde el
        # código la busca ahora, y en la general para que el demo siga dando la
        # misma cifra si a un día se le quita la ruta.
        db.add(
            Transportador(
                empresa_id=empresa.id, nombre=nombre, valor_transporte=tarifa,
                rutas=[
                    TransportadorRuta(ruta_id=rutas[ruta_nombre].id, valor_transporte=tarifa)
                ],
            )
        )
    for nombre, vereda, precio, ruta_nombre in PROVEEDORES_DEMO:
        db.add(
            Proveedor(
                empresa_id=empresa.id, nombre=nombre, vereda=vereda,
                precio_litro=precio, ruta_id=rutas[ruta_nombre].id,
            )
        )
    for nombre in ("Queso Costeño", "Queso Criollo"):
        db.add(TipoQueso(empresa_id=empresa.id, nombre=nombre))
    for nombre, categoria, unidad, minimo in PRODUCTOS_DEMO:
        db.add(
            Producto(
                empresa_id=empresa.id, nombre=nombre, categoria=categoria,
                unidad=unidad, stock_minimo=minimo,
            )
        )
    for nombre in CATEGORIAS_DEFECTO:
        db.add(CategoriaGasto(empresa_id=empresa.id, nombre=nombre))

    admin_empresa = Usuario(
        nombre="Admin",
        apellido="Quesera",
        correo="admin@lamarginal.local",
        username="admin.quesera",
        hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
        empresa_id=empresa.id,
    )
    # .get() por lo mismo que en seed_superadmin: el nombre puede faltar.
    rol_admin_empresa = roles.get("Administrador Empresa")
    if rol_admin_empresa is None:
        logger.warning(
            "El rol 'Administrador Empresa' no está disponible: el usuario de la empresa demo "
            "se crea SIN rol."
        )
    admin_empresa.roles = [rol_admin_empresa] if rol_admin_empresa is not None else []
    db.add(admin_empresa)
    db.flush()
    logger.info("Empresa demo creada: %s", empresa.nombre)


# Catálogos por defecto que recibe TODA empresa (editables/eliminables después).
TIPOS_QUESO_DEFECTO = ["Queso Costeño", "Queso Criollo", "Queso Doble Crema", "Queso Campesino"]


def ensure_catalogos_empresas(db: Session) -> None:
    """Garantiza que cada empresa tenga catálogos mínimos (tipos de queso y
    categorías de gasto) para que los formularios no queden con selects vacíos.
    Idempotente: solo agrega si la empresa no tiene ninguno activo.
    """
    empresas = db.scalars(select(Empresa).where(Empresa.deleted_at.is_(None))).all()
    for empresa in empresas:
        tiene_tipos = db.scalar(
            select(func.count())
            .select_from(TipoQueso)
            .where(TipoQueso.empresa_id == empresa.id, TipoQueso.deleted_at.is_(None))
        )
        if not tiene_tipos:
            for nombre in TIPOS_QUESO_DEFECTO:
                db.add(TipoQueso(empresa_id=empresa.id, nombre=nombre))

        tiene_categorias = db.scalar(
            select(func.count())
            .select_from(CategoriaGasto)
            .where(CategoriaGasto.empresa_id == empresa.id, CategoriaGasto.deleted_at.is_(None))
        )
        if not tiene_categorias:
            for nombre in CATEGORIAS_DEFECTO:
                db.add(CategoriaGasto(empresa_id=empresa.id, nombre=nombre))
    db.flush()


def run() -> None:
    db = SessionLocal()
    try:
        permisos = seed_permisos(db)
        roles = seed_roles(db, permisos)
        seed_superadmin(db, roles)
        if settings.SEED_DEMO_DATA:
            seed_empresa_demo(db, roles)
        ensure_catalogos_empresas(db)
        db.commit()
        logger.info("Seed completado")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
