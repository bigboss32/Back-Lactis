"""Importa todos los modelos para que Base.metadata esté completa.

Usado por Alembic (autogenerate), los seeds y las pruebas (create_all).
"""
from app.core.database import Base  # noqa: F401
from app.modules.auditoria.models import Auditoria  # noqa: F401
from app.modules.bancos.models import CuentaBancaria, MovimientoBancario  # noqa: F401
from app.modules.caja.models import CajaDiaria, MovimientoCaja  # noqa: F401
from app.modules.clientes.models import Cliente  # noqa: F401
from app.modules.empleados.models import Empleado, PagoEmpleado  # noqa: F401
from app.modules.empresas.models import Empresa  # noqa: F401
from app.modules.gastos.models import CategoriaGasto, Gasto  # noqa: F401
from app.modules.inventario.models import MovimientoInventario, Producto  # noqa: F401
from app.modules.liquidaciones.models import (  # noqa: F401
    Anticipo,
    Liquidacion,
    LiquidacionDetalle,
    PagoLiquidacion,
)
from app.modules.notificaciones.models import Notificacion  # noqa: F401
from app.modules.produccion.models import (  # noqa: F401
    CicloDespacho,
    CicloDespachoLote,
    Produccion,
    TipoQueso,
)
from app.modules.proveedores.models import Proveedor  # noqa: F401
from app.modules.recepcion.models import RecepcionLeche  # noqa: F401
from app.modules.reventa.models import (  # noqa: F401
    AbonoCompraQueso,
    AbonoSaldoAnterior,
    AbonoVentaQueso,
    AdjuntoReventa,
    CompraQueso,
    ConversionBorona,
    SaldoAnterior,
    Temporada,
    VentaQueso,
)
from app.modules.rutas.models import Ruta  # noqa: F401
from app.modules.sucursales.models import Sucursal  # noqa: F401
from app.modules.suscripcion.models import (  # noqa: F401
    FuentePagoSuscripcion,
    PagoSuscripcion,
)
from app.modules.transportadores.models import (  # noqa: F401
    Transportador,
    TransportadorRuta,
)
from app.modules.transporte.models import (  # noqa: F401
    AbonoFlete,
    Vehiculo,
    VehiculoDocumento,
    VehiculoGasto,
    VehiculoMantenimiento,
    Viaje,
    ViajeServicio,
)
from app.modules.usuarios.models import (  # noqa: F401
    LoginAudit,
    PasswordResetToken,
    Permiso,
    RefreshToken,
    Rol,
    Usuario,
    UsuarioRol,
)
from app.modules.ventas.models import (  # noqa: F401
    Pago,
    PagoConductor,
    Venta,
    VentaDetalle,
    VentaTramoFlete,
)
