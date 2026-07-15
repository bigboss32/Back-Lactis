"""Seed idempotente: catálogo de permisos, roles del sistema, superadmin
y (opcional) empresa demo 'Queso La Marginal de la Selva' con los datos
reales del negocio (rutas, veredas, proveedores y precios del Excel origen).

Ejecutar:  python -m app.seeds.seed
"""
from decimal import Decimal

from sqlalchemy import select
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
from app.modules.transportadores.models import Transportador
from app.modules.usuarios.models import Permiso, Rol, Usuario

logger = get_logger("seed")

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
    },
    "Auxiliar": {
        ("recepcion", "crear"), ("recepcion", "consultar"), ("proveedores", "consultar"),
        ("transportadores", "consultar"), ("rutas", "consultar"), ("inventario", "consultar"),
        ("inventario", "crear"), ("notificaciones", "consultar"),
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
    roles = {r.nombre: r for r in db.scalars(select(Rol)).all()}
    for nombre in ROLES_SISTEMA:
        if nombre not in roles:
            rol = Rol(nombre=nombre, descripcion=f"Rol de sistema: {nombre}", es_sistema=True)
            db.add(rol)
            roles[nombre] = rol
    db.flush()
    # Sincronización por unión: si aparecen módulos nuevos en el catálogo,
    # los roles de sistema existentes reciben sus permisos en el siguiente seed
    for nombre, claves in ROLES_PERMISOS.items():
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
        admin.roles = [roles[ROL_SUPERADMIN]]
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
        db.add(
            Transportador(
                empresa_id=empresa.id, nombre=nombre,
                ruta_id=rutas[ruta_nombre].id, valor_transporte=tarifa,
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
    admin_empresa.roles = [roles["Administrador Empresa"]]
    db.add(admin_empresa)
    db.flush()
    logger.info("Empresa demo creada: %s", empresa.nombre)


def run() -> None:
    db = SessionLocal()
    try:
        permisos = seed_permisos(db)
        roles = seed_roles(db, permisos)
        seed_superadmin(db, roles)
        if settings.SEED_DEMO_DATA:
            seed_empresa_demo(db, roles)
        db.commit()
        logger.info("Seed completado")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
