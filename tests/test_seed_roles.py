"""Pruebas de la siembra de roles (app/seeds/seed.py).

Lo que se protege aquí es una ESCALADA DE PRIVILEGIOS, no una comodidad: los
roles son GLOBALES (Rol no tiene empresa_id) y Rol.nombre es UNIQUE en toda la
base, así que un rol que un cliente creó a mano puede llamarse exactamente igual
que uno de la lista de siembra. Si la siembra lo tomara por suyo y le
sincronizara los permisos, en el siguiente despliegue TODOS los usuarios que lo
tuvieran asignado ganarían permisos en silencio.

El caso real: un cliente al que solo se le deja mirar tiene un rol "Reventa"
propio con un único permiso, reventa:consultar. Sembrar encima le daría las 7
acciones del módulo —incluidas 'eliminar' y 'administrar', la que anula compras
y ventas— sin que nadie lo pidiera.
"""
import logging
from contextlib import contextmanager

from sqlalchemy import select

from app.core.permissions import ACCIONES
from app.modules.usuarios.models import Rol
from app.seeds.seed import seed_permisos, seed_roles

NOMBRE_EN_CONFLICTO = "Reventa"

# Las 7 acciones de 'reventa', las 3 de 'suscripcion' (este cliente es quien
# paga la mensualidad) y notificaciones:consultar. Nada más.
PERMISOS_ESPERADOS_ROL_SISTEMA = (
    {("reventa", a) for a in ACCIONES}
    | {("suscripcion", a) for a in ("consultar", "crear", "administrar")}
    | {("notificaciones", "consultar")}
)


class _CapturaAvisos(logging.Handler):
    """Recoge los mensajes de warning ya formateados."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.mensajes: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.mensajes.append(record.getMessage())


@contextmanager
def capturar_avisos_de_siembra():
    """Engancha un handler al logger de la siembra.

    No sirve el fixture `caplog`: el logger 'quesera' se configura con
    propagate=False (app/core/logging_config.py), así que sus registros nunca
    llegan al root, que es donde pytest pone su handler.
    """
    logger = logging.getLogger("quesera.seed")
    handler = _CapturaAvisos()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _claves(rol: Rol) -> set[tuple[str, str]]:
    return {(p.modulo, p.accion) for p in rol.permisos}


def test_siembra_no_escala_privilegios_de_un_rol_de_usuario_homonimo(db_session):
    """Un rol de USUARIO llamado 'Reventa' no debe ganar permisos al sembrar.

    Es la prueba de la escalada de privilegios: si esto falla, un despliegue
    convierte un rol de solo consulta en uno que puede anular y eliminar, y con
    él a todos los usuarios que lo tengan asignado.
    """
    permisos = seed_permisos(db_session)

    rol_cliente = Rol(
        nombre=NOMBRE_EN_CONFLICTO,
        descripcion="Rol del cliente: solo mirar",
        es_sistema=False,
    )
    rol_cliente.permisos = [permisos[("reventa", "consultar")]]
    db_session.add(rol_cliente)
    db_session.flush()
    rol_id = rol_cliente.id

    with capturar_avisos_de_siembra() as avisos:
        roles = seed_roles(db_session, permisos)

    # 1. Sus permisos siguen siendo EXACTAMENTE los que tenía.
    rol_despues = db_session.get(Rol, rol_id)
    assert _claves(rol_despues) == {("reventa", "consultar")}, (
        "la siembra amplió los permisos de un rol de usuario: escalada de privilegios"
    )

    # 2. Y sigue siendo un rol de usuario: la siembra tampoco lo convierte en
    #    rol de sistema (eso lo dejaría sin poder renombrar ni borrar).
    assert rol_despues.es_sistema is False

    # 3. No se creó un segundo rol con ese nombre (el UNIQUE tampoco lo dejaría).
    con_ese_nombre = db_session.scalars(
        select(Rol).where(Rol.nombre == NOMBRE_EN_CONFLICTO)
    ).all()
    assert len(con_ese_nombre) == 1

    # 4. Y quedó constancia BIEN VISIBLE en el log del despliegue.
    assert any(NOMBRE_EN_CONFLICTO in m for m in avisos.mensajes), (
        f"no se avisó del conflicto de nombres en el log: {avisos.mensajes}"
    )

    # 5. El diccionario que devuelve sigue siendo utilizable: los demás roles de
    #    sistema (los que piden seed_superadmin y la empresa demo) están ahí.
    assert roles["Administrador General"].es_sistema is True
    assert roles["Administrador Empresa"].es_sistema is True


def test_siembra_crea_el_rol_de_sistema_reventa_si_nadie_ocupa_el_nombre(db_session):
    """Sin conflicto, la siembra sí crea el rol de sistema con sus permisos."""
    permisos = seed_permisos(db_session)

    roles = seed_roles(db_session, permisos)

    rol = roles[NOMBRE_EN_CONFLICTO]
    assert rol.es_sistema is True
    assert _claves(rol) == PERMISOS_ESPERADOS_ROL_SISTEMA
    assert len(rol.permisos) == len(PERMISOS_ESPERADOS_ROL_SISTEMA)


def test_el_rol_reventa_puede_pagar_pero_no_administrar_la_empresa(db_session):
    """Lo que este rol tiene que poder y lo que NO, dicho campo por campo.

    El cliente de reventa no usa el ERP: solo compra y revende queso. Pero es
    quien paga la mensualidad, así que necesita el módulo de suscripción — y
    SOLO ese de todo Administración. Si se le colara 'usuarios' o 'roles' podría
    darse a sí mismo el resto del sistema, y si se le colara 'empresas' vería
    datos de la quesera que no le tocan.

    Sin la suscripción el problema es el contrario y también real: al vencerse
    quedaría bloqueado sin manera de pagar, esperando a que alguien con más
    permisos entrara a hacerlo por él.
    """
    permisos = seed_permisos(db_session)
    rol = seed_roles(db_session, permisos)[NOMBRE_EN_CONFLICTO]
    claves = _claves(rol)
    modulos = sorted({m for m, _ in claves})

    print("\n===== QUÉ VE EL ROL 'REVENTA' =====")
    for modulo in modulos:
        acciones = sorted(a for m, a in claves if m == modulo)
        print(f"  {modulo:16} {', '.join(acciones)}")

    # Puede pagar, de las tres formas que hacen falta
    assert ("suscripcion", "consultar") in claves      # ver cómo va
    assert ("suscripcion", "crear") in claves          # pagar (tarjeta o PSE)
    assert ("suscripcion", "administrar") in claves    # guardar la tarjeta

    # Y NO puede nada más de Administración
    prohibidos = ("usuarios", "roles", "empresas", "empleados", "sucursales", "auditoria")
    colados = [m for m in prohibidos if any(mod == m for mod, _ in claves)]
    print(f"  módulos prohibidos que se colaron: {colados or 'ninguno'}")
    assert colados == []

    # Ni contabilidad, ni reportes (de ahí cuelga Estadísticas del ERP)
    assert not any(mod in ("contabilidad", "reportes") for mod, _ in claves)
    assert modulos == ["notificaciones", "reventa", "suscripcion"]


def test_siembra_repetida_no_duplica_ni_cambia_permisos(db_session):
    """Idempotencia: sembrar dos veces deja el rol de sistema igual."""
    permisos = seed_permisos(db_session)
    seed_roles(db_session, permisos)

    roles = seed_roles(db_session, permisos)

    assert _claves(roles[NOMBRE_EN_CONFLICTO]) == PERMISOS_ESPERADOS_ROL_SISTEMA
    con_ese_nombre = db_session.scalars(
        select(Rol).where(Rol.nombre == NOMBRE_EN_CONFLICTO)
    ).all()
    assert len(con_ese_nombre) == 1
