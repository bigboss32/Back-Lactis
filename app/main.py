from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import app.models_registry  # noqa: F401  (asegura el registro de todos los modelos)
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import get_logger, setup_logging
from app.modules.auditoria.router import router as auditoria_router
from app.modules.auth.router import router as auth_router
from app.modules.bancos.router import movimientos_router as mov_bancarios_router
from app.modules.bancos.router import router as bancos_router
from app.modules.caja.router import router as caja_router
from app.modules.clientes.router import router as clientes_router
from app.modules.contabilidad.router import router as contabilidad_router
from app.modules.empleados.router import pagos_router as nomina_router
from app.modules.empleados.router import router as empleados_router
from app.modules.empresas.router import router as empresas_router
from app.modules.gastos.router import categorias_router as categorias_gasto_router
from app.modules.gastos.router import router as gastos_router
from app.modules.inventario.router import movimientos_router as mov_inventario_router
from app.modules.inventario.router import router as inventario_router
from app.modules.liquidaciones.router import anticipos_router
from app.modules.liquidaciones.router import router as liquidaciones_router
from app.modules.notificaciones.router import router as notificaciones_router
from app.modules.produccion.router import router as produccion_router
from app.modules.produccion.router import tipos_queso_router
from app.modules.proveedores.router import router as proveedores_router
from app.modules.recepcion.router import router as recepcion_router
from app.modules.reportes.router import router as reportes_router
from app.modules.reventa.router import router as reventa_router
from app.modules.rutas.router import router as rutas_router
from app.modules.sucursales.router import router as sucursales_router
from app.modules.transportadores.router import router as transportadores_router
from app.modules.usuarios.router import permisos_router, roles_router
from app.modules.usuarios.router import router as usuarios_router
from app.modules.ventas.router import pagos_router
from app.modules.ventas.router import router as ventas_router

setup_logging()
logger = get_logger("main")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=(
            "ERP multiempresa para la administración de queseras: recepción de leche, "
            "liquidaciones, producción, inventario, ventas, gastos, caja, bancos, "
            "contabilidad, reportes, notificaciones y auditoría."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        # Permite el frontend desplegado en Cloudflare: el dominio propio
        # lactis.maroa.co, los subdominios *.maroa.co, los *.pages.dev y los
        # *.workers.dev (previews de Cloudflare Workers/Pages).
        allow_origin_regex=(
            r"https://([a-z0-9-]+\.)*maroa\.co"
            r"|https://([a-z0-9-]+\.)+pages\.dev"
            r"|https://([a-z0-9-]+\.)+workers\.dev"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    prefix = settings.API_V1_PREFIX
    app.include_router(auth_router, prefix=f"{prefix}/auth")
    app.include_router(empresas_router, prefix=f"{prefix}/empresas")
    app.include_router(sucursales_router, prefix=f"{prefix}/sucursales")
    app.include_router(usuarios_router, prefix=f"{prefix}/usuarios")
    app.include_router(roles_router, prefix=f"{prefix}/roles")
    app.include_router(permisos_router, prefix=f"{prefix}/permisos")
    app.include_router(proveedores_router, prefix=f"{prefix}/proveedores")
    app.include_router(transportadores_router, prefix=f"{prefix}/transportadores")
    app.include_router(rutas_router, prefix=f"{prefix}/rutas")
    app.include_router(recepcion_router, prefix=f"{prefix}/recepciones")
    app.include_router(reventa_router, prefix=f"{prefix}/reventa")
    app.include_router(liquidaciones_router, prefix=f"{prefix}/liquidaciones")
    app.include_router(anticipos_router, prefix=f"{prefix}/anticipos")
    app.include_router(produccion_router, prefix=f"{prefix}/produccion")
    app.include_router(tipos_queso_router, prefix=f"{prefix}/tipos-queso")
    app.include_router(inventario_router, prefix=f"{prefix}/inventario/productos")
    app.include_router(mov_inventario_router, prefix=f"{prefix}/inventario/movimientos")
    app.include_router(clientes_router, prefix=f"{prefix}/clientes")
    app.include_router(empleados_router, prefix=f"{prefix}/empleados")
    app.include_router(nomina_router, prefix=f"{prefix}/nomina")
    app.include_router(ventas_router, prefix=f"{prefix}/ventas")
    app.include_router(pagos_router, prefix=f"{prefix}/pagos")
    app.include_router(gastos_router, prefix=f"{prefix}/gastos")
    app.include_router(categorias_gasto_router, prefix=f"{prefix}/categorias-gasto")
    app.include_router(caja_router, prefix=f"{prefix}/caja")
    app.include_router(bancos_router, prefix=f"{prefix}/bancos/cuentas")
    app.include_router(mov_bancarios_router, prefix=f"{prefix}/bancos/movimientos")
    app.include_router(contabilidad_router, prefix=f"{prefix}/contabilidad")
    app.include_router(reportes_router, prefix=f"{prefix}/reportes")
    app.include_router(notificaciones_router, prefix=f"{prefix}/notificaciones")
    app.include_router(auditoria_router, prefix=f"{prefix}/auditoria")

    uploads_dir = Path(settings.UPLOADS_DIR)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

    @app.get("/health", tags=["Salud"], summary="Verificación de salud del servicio")
    def health() -> dict:
        return {"status": "ok", "service": settings.PROJECT_NAME, "version": settings.VERSION}

    logger.info("Aplicación inicializada (%s)", settings.ENVIRONMENT)
    return app


app = create_app()
