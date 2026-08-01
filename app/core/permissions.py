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
    "transporte",
    "caja",
    "bancos",
    "contabilidad",
    "reportes",
    "notificaciones",
    "auditoria",
    # Suscripción de la plataforma: el Administrador Empresa gestiona SU tarjeta
    # y SUS pagos; la tarifa y la exención viven bajo empresas + superadmin.
    "suscripcion",
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
