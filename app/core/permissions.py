"""Catálogo central de módulos y acciones para el sistema RBAC.

El seed crea un Permiso por cada combinación módulo × acción.
"""

ACCIONES: tuple[str, ...] = (
    "crear",
    "editar",
    "eliminar",
    "consultar",
    "exportar",
    "imprimir",
    "administrar",
)

MODULOS: tuple[str, ...] = (
    "empresas",
    "sucursales",
    "usuarios",
    "roles",
    "proveedores",
    "transportadores",
    "rutas",
    "recepcion",
    "liquidaciones",
    "reventa",
    "produccion",
    "inventario",
    "clientes",
    "empleados",
    "ventas",
    "gastos",
    "caja",
    "bancos",
    "contabilidad",
    "reportes",
    "notificaciones",
    "auditoria",
)

ROL_SUPERADMIN = "Administrador General"

ROLES_SISTEMA: tuple[str, ...] = (
    ROL_SUPERADMIN,
    "Administrador Empresa",
    "Contador",
    "Supervisor",
    "Auxiliar",
    "Producción",
    "Compras",
    "Ventas",
    "Consulta",
)
