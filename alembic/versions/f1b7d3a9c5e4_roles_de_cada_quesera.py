"""roles de cada quesera

Los roles eran de TODA LA INSTALACIÓN: `roles` no tenía empresa_id y su nombre
era UNIQUE en toda la base, así que las dos queseras compartían LA MISMA FILA. El
administrador de una le cambiaba los permisos al rol 'Reventa' y con eso le
apagaba o le encendía pantallas a los usuarios de la otra.

Esta migración separa:

  · rol de SISTEMA (es_sistema) -> empresa_id NULL. Se queda COMPARTIDO porque
    es la plantilla que la siembra mantiene en cada despliegue, pero deja de
    poder editarlo el administrador de una empresa (eso lo cierra RolService).
  · rol de una empresa -> empresa_id con valor. Solo esa empresa lo ve y lo
    edita. Si un rol lo usaban usuarios de DOS empresas, se DUPLICA y se
    reparte: cada quesera se queda con su copia y sus usuarios apuntan a ella.
  · el nombre pasa a ser único POR EMPRESA (dos índices únicos parciales, uno
    para los roles de empresa y otro para las plantillas).

LO QUE NO PUEDE PASAR: que alguien quede con un permiso de más o de menos. Los
roles deciden quién entra a qué, y un error acá deja gente por fuera del sistema
en producción, sin nadie adentro que pueda arreglarlo. Por eso la migración se
toma una FOTO de los permisos efectivos de CADA USUARIO EN CADA EMPRESA antes de
tocar nada, y al final vuelve a tomarla y las compara una por una. Si a alguien
le cambió aunque sea un permiso, REVIENTA — y como el DDL de Postgres es
transaccional, el despliegue se queda con la base intacta y sin migrar.

La foto se calcula igual que lo hace la aplicación en vivo (Usuario.roles_en):
los permisos de una persona EN una empresa son los de sus roles de esa empresa
MÁS los de sus filas globales. Incluye a propósito los roles borrados en suave,
porque hoy siguen otorgando permisos (`roles_en` no mira deleted_at) y lo que se
conserva es lo que HAY, no lo que debería haber.

Verificación manual en Postgres después de migrar:

    -- 1) TIENE QUE DAR 0. Ninguna asignación puede apuntar a un rol de OTRA
    --    empresa; si da algo, el reparto quedó mal y hay que mirarlo.
    SELECT count(*) FROM usuario_roles ur JOIN roles r ON r.id = ur.rol_id
    WHERE r.empresa_id IS NOT NULL AND r.empresa_id IS DISTINCT FROM ur.empresa_id;

    -- 2) LISTA DE PENDIENTES, no un error: los roles que se quedaron
    --    compartidos sin ser plantillas de sistema. Salen aquí los que la
    --    migración avisó por el log porque tenían asignaciones GLOBALES y no se
    --    pudo saber de qué quesera son. Nadie perdió permisos por eso, pero
    --    ningún administrador de empresa los puede editar, y la siembra los
    --    deja quietos. Para cerrar cada uno: averigüe qué quesera lo usa y
    --    hágale UPDATE roles SET empresa_id = '<la empresa>' WHERE id = '<el rol>'.
    --    Si la lista sale vacía, mejor: no quedó nada por revisar.
    SELECT id, nombre FROM roles WHERE empresa_id IS NULL AND es_sistema = false;

Revision ID: f1b7d3a9c5e4
Revises: c3f8a1d6b0e5
Create Date: 2026-08-26 00:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1b7d3a9c5e4'
down_revision: Union[str, None] = 'c3f8a1d6b0e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --------------------------------------------------------------------------
# La foto: permisos efectivos de cada usuario en cada empresa
# --------------------------------------------------------------------------
def _permisos_por_rol(bind) -> dict:
    """{rol_id: frozenset(permiso_id)} — todos los roles, borrados incluidos."""
    acumulado: dict = {}
    for fila in bind.execute(sa.text("SELECT rol_id, permiso_id FROM rol_permisos")).mappings():
        acumulado.setdefault(fila["rol_id"], set()).add(fila["permiso_id"])
    return {rol_id: frozenset(permisos) for rol_id, permisos in acumulado.items()}


def _foto_de_permisos(bind) -> dict:
    """{(usuario_id, empresa_id_o_None): frozenset(permiso_id)}.

    Un renglón por cada empresa en la que la persona puede pararse: las de sus
    asignaciones, su empresa principal y —siempre— el contexto sin empresa, que
    es el del Administrador General. En cada uno, la unión de los permisos de
    sus roles de esa empresa y de sus roles globales, exactamente como los arma
    `Usuario.roles_en` cuando alguien inicia sesión.
    """
    permisos_de = _permisos_por_rol(bind)

    asignaciones: dict = {}
    for fila in bind.execute(
        sa.text("SELECT usuario_id, rol_id, empresa_id FROM usuario_roles")
    ).mappings():
        asignaciones.setdefault(fila["usuario_id"], []).append(
            (fila["rol_id"], fila["empresa_id"])
        )

    principales: dict = {
        fila["id"]: fila["empresa_id"]
        for fila in bind.execute(sa.text("SELECT id, empresa_id FROM usuarios")).mappings()
    }

    foto: dict = {}
    for usuario_id in set(principales) | set(asignaciones):
        filas = asignaciones.get(usuario_id, [])
        contextos = {empresa_id for _, empresa_id in filas if empresa_id is not None}
        principal = principales.get(usuario_id)
        if principal is not None:
            contextos.add(principal)
        contextos.add(None)  # el superadmin trabaja sin empresa activa
        for contexto in contextos:
            efectivos: set = set()
            for rol_id, empresa_id in filas:
                if empresa_id is None or empresa_id == contexto:
                    efectivos |= permisos_de.get(rol_id, frozenset())
            foto[(usuario_id, contexto)] = frozenset(efectivos)
    return foto


def _comparar(antes: dict, despues: dict) -> None:
    """Revienta si a alguien le cambió el conjunto de permisos, y dice a quién."""
    diferencias = []
    for clave in sorted(set(antes) | set(despues), key=str):
        previo = antes.get(clave, frozenset())
        actual = despues.get(clave, frozenset())
        if previo != actual:
            diferencias.append(
                "  usuario=%s empresa=%s  perdió=%d ganó=%d"
                % (clave[0], clave[1], len(previo - actual), len(actual - previo))
            )
    if diferencias:
        raise RuntimeError(
            "MIGRACIÓN ABORTADA: separar los roles por empresa le habría cambiado los "
            "permisos a %d usuario(s)/empresa(s). No se migró nada.\n%s"
            % (len(diferencias), "\n".join(diferencias))
        )
    print("  post-vuelo: %d usuario(s)/empresa(s) con los MISMOS permisos que antes"
          % len(despues))


# --------------------------------------------------------------------------
# Quitar el UNIQUE global de roles.nombre
# --------------------------------------------------------------------------
def _exigir_postgres(bind) -> None:
    """Esta revisión es de Postgres, como toda la cadena.

    No es una decisión de esta migración: la cadena ya no corre en SQLite desde
    'd4e7b1a0f9c3' (usa ALTER COLUMN ... DROP NOT NULL, que SQLite no tiene), así
    que nunca se llega hasta acá con otro motor. Se dice de frente en vez de
    dejar un camino alternativo que nadie ejecuta jamás y que por eso nadie sabe
    si funciona. La suite de pruebas no pasa por aquí: monta el esquema con
    `create_all` desde los modelos.
    """
    if bind.dialect.name != "postgresql":
        raise RuntimeError(
            "La cadena de migraciones de Lactis es de PostgreSQL (producción). "
            "Motor recibido: %s. Para pruebas con SQLite se usa create_all desde "
            "los modelos, no alembic." % bind.dialect.name
        )


def _nombre_del_unique_de_nombre(bind) -> str | None:
    """Cómo se llama en Postgres el UNIQUE sobre roles(nombre).

    Se busca en el catálogo en vez de escribir 'roles_nombre_key' a mano: ese es
    el nombre que genera Postgres, pero si la base del cliente se creó de otra
    forma podría llamarse distinto y el DROP fallaría a mitad del despliegue.
    """
    return bind.execute(
        sa.text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
            "WHERE rel.relname = 'roles' AND con.contype = 'u' "
            "AND con.conkey = ARRAY[(SELECT attnum FROM pg_attribute "
            "  WHERE attrelid = rel.oid AND attname = 'nombre')]::smallint[] "
            "LIMIT 1"
        )
    ).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    _exigir_postgres(bind)
    antes = _foto_de_permisos(bind)
    print("\n  pre-vuelo: %d usuario(s)/empresa(s) fotografiados" % len(antes))

    op.add_column('roles', sa.Column('empresa_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('roles_empresa_id_fkey', 'roles', 'empresas', ['empresa_id'], ['id'])
    constraint = _nombre_del_unique_de_nombre(bind)
    if constraint:
        op.drop_constraint(constraint, 'roles', type_='unique')
    op.create_index(op.f('ix_roles_empresa_id'), 'roles', ['empresa_id'], unique=False)

    _repartir_roles(bind)

    # Los índices se crean DESPUÉS del reparto: si algo quedó mal repartido, es
    # aquí donde revienta y no en el primer INSERT que haga el cliente.
    op.create_index(
        'uq_rol_nombre_empresa', 'roles', ['empresa_id', 'nombre'], unique=True,
        postgresql_where=sa.text('empresa_id IS NOT NULL'),
        sqlite_where=sa.text('empresa_id IS NOT NULL'),
    )
    op.create_index(
        'uq_rol_nombre_global', 'roles', ['nombre'], unique=True,
        postgresql_where=sa.text('empresa_id IS NULL'),
        sqlite_where=sa.text('empresa_id IS NULL'),
    )

    _comparar(antes, _foto_de_permisos(bind))


def _repartir_roles(bind) -> None:
    """Le pone dueño a cada rol, duplicando los que usan dos queseras."""
    roles = bind.execute(
        sa.text(
            "SELECT id, nombre, descripcion, es_sistema, estado, deleted_at, "
            "created_by, updated_by FROM roles ORDER BY created_at, id"
        )
    ).mappings().all()

    # Qué empresas usan cada rol, según a quién se lo asignaron.
    usos: dict = {}
    globales: set = set()
    for fila in bind.execute(
        sa.text("SELECT DISTINCT rol_id, empresa_id FROM usuario_roles")
    ).mappings():
        if fila["empresa_id"] is None:
            globales.add(fila["rol_id"])
        else:
            usos.setdefault(fila["rol_id"], []).append(fila["empresa_id"])

    empresas_vivas = [
        fila["id"]
        for fila in bind.execute(
            sa.text(
                "SELECT id FROM empresas WHERE deleted_at IS NULL ORDER BY created_at, id"
            )
        ).mappings()
    ]
    # Empresa de quien creó el rol: la usamos para atribuir los roles que nadie
    # tiene asignado todavía (un rol recién hecho, sin usuarios).
    empresa_del_usuario = {
        fila["id"]: fila["empresa_id"]
        for fila in bind.execute(
            sa.text("SELECT id, empresa_id FROM usuarios WHERE empresa_id IS NOT NULL")
        ).mappings()
    }

    for rol in roles:
        if rol["es_sistema"]:
            # Plantilla: se queda compartida, sin empresa. Es lo que la siembra
            # mantiene y lo que las dos queseras siguen asignando.
            continue

        if rol["id"] in globales:
            # Rol de usuario asignado SIN empresa (fila global). Repartirlo le
            # cambiaría los permisos a quien la tiene, así que se queda como
            # plantilla y se avisa: alguien tendrá que mirarlo a mano.
            print(
                "  AVISO — el rol '%s' (%s) tiene asignaciones GLOBALES y se queda "
                "compartido. Revíselo: ningún administrador de empresa podrá editarlo."
                % (rol["nombre"], rol["id"])
            )
            continue

        empresas = list(dict.fromkeys(usos.get(rol["id"], [])))
        empresas = [e for e in empresas if e is not None]
        lo_usaban_varias = len(empresas) > 1

        if not empresas:
            # Nadie lo tiene puesto: no hay permisos que mover pase lo que pase.
            duenio = empresa_del_usuario.get(rol["created_by"])
            if duenio is not None:
                empresas = [duenio]
                print(
                    "  el rol '%s' no lo tiene ningún usuario: queda en la empresa de "
                    "quien lo creó (%s)" % (rol["nombre"], duenio)
                )
            elif empresas_vivas:
                # No se puede saber de quién es. Antes lo veían las dos queseras,
                # así que se le deja una copia a cada una: nadie pierde un rol
                # que ya tenía a la vista y ninguna edita el de la otra.
                empresas = list(empresas_vivas)
                print(
                    "  AVISO — no se pudo saber de qué quesera es el rol '%s' (nadie lo "
                    "tiene asignado y no se sabe quién lo creó): se le deja una copia a "
                    "cada una de las %d empresas." % (rol["nombre"], len(empresas))
                )
            else:
                print(
                    "  AVISO — el rol '%s' se queda sin empresa: no hay ninguna empresa "
                    "viva a la que asignárselo." % rol["nombre"]
                )
                continue

        # La primera empresa se queda con la fila original; las demás reciben una
        # copia idéntica y sus usuarios pasan a apuntarle a ella.
        bind.execute(
            sa.text("UPDATE roles SET empresa_id = :empresa WHERE id = :rol"),
            {"empresa": empresas[0], "rol": rol["id"]},
        )
        for empresa in empresas[1:]:
            copia = uuid.uuid4()
            bind.execute(
                sa.text(
                    "INSERT INTO roles (id, nombre, descripcion, es_sistema, empresa_id, "
                    "estado, deleted_at, created_by, updated_by, created_at, updated_at) "
                    "SELECT :copia, nombre, descripcion, es_sistema, :empresa, estado, "
                    "deleted_at, created_by, updated_by, created_at, updated_at "
                    "FROM roles WHERE id = :rol"
                ),
                {"copia": copia, "empresa": empresa, "rol": rol["id"]},
            )
            bind.execute(
                sa.text(
                    "INSERT INTO rol_permisos (rol_id, permiso_id) "
                    "SELECT :copia, permiso_id FROM rol_permisos WHERE rol_id = :rol"
                ),
                {"copia": copia, "rol": rol["id"]},
            )
            bind.execute(
                sa.text(
                    "UPDATE usuario_roles SET rol_id = :copia "
                    "WHERE rol_id = :rol AND empresa_id = :empresa"
                ),
                {"copia": copia, "rol": rol["id"], "empresa": empresa},
            )
            print(
                "  %s: copia %s para la empresa %s"
                % (
                    ("el rol '%s' lo usaban varias queseras" % rol["nombre"])
                    if lo_usaban_varias
                    else ("el rol '%s' se replica" % rol["nombre"]),
                    copia,
                    empresa,
                )
            )


def downgrade() -> None:
    """Vuelve al rol único de toda la instalación.

    Al volver, `roles.nombre` es UNIQUE en toda la base otra vez, y las copias
    que dejó el reparto se llaman igual. NO SE FUSIONAN NI SE BORRAN: fusionarlas
    obligaría a escoger un juego de permisos y alguien terminaría con uno de más
    (escalada) o de menos (se queda por fuera de una pantalla). Se RENOMBRAN, que
    es lo único que no le cambia los permisos a nadie: el dueño verá un rol con
    un nombre feo —'Ventas (Quesera B)'— y podrá arreglarlo a mano.

    Se comprueba igual que en el upgrade: si el downgrade le cambia los permisos
    a alguien, revienta y no deshace nada.
    """
    bind = op.get_bind()
    _exigir_postgres(bind)
    antes = _foto_de_permisos(bind)

    nombres_de_empresa = {
        fila["id"]: fila["nombre"]
        for fila in bind.execute(sa.text("SELECT id, nombre FROM empresas")).mappings()
    }
    # Las PLANTILLAS (empresa_id NULL) van primero para que sean ellas las que se
    # queden con el nombre bueno y se renombre lo que venga después. NULLS FIRST
    # es explícito: en Postgres los NULL ordenan al final si no se dice nada.
    ocupados = set()
    for fila in bind.execute(
        sa.text(
            "SELECT id, nombre, empresa_id FROM roles "
            "ORDER BY empresa_id NULLS FIRST, created_at, id"
        )
    ).mappings():
        if fila["nombre"] not in ocupados:
            ocupados.add(fila["nombre"])
            continue
        etiqueta = nombres_de_empresa.get(fila["empresa_id"], "otra empresa")
        candidato = f"{fila['nombre']} ({etiqueta})"[:80]
        sufijo = 2
        while candidato in ocupados:
            candidato = f"{fila['nombre']} ({etiqueta} {sufijo})"[:80]
            sufijo += 1
        ocupados.add(candidato)
        bind.execute(
            sa.text("UPDATE roles SET nombre = :nuevo WHERE id = :rol"),
            {"nuevo": candidato, "rol": fila["id"]},
        )
        print("  downgrade: '%s' se renombra a '%s' para que quepa el UNIQUE global"
              % (fila["nombre"], candidato))

    op.drop_index('uq_rol_nombre_global', table_name='roles')
    op.drop_index('uq_rol_nombre_empresa', table_name='roles')
    op.drop_index(op.f('ix_roles_empresa_id'), table_name='roles')

    op.drop_constraint('roles_empresa_id_fkey', 'roles', type_='foreignkey')
    op.drop_column('roles', 'empresa_id')
    op.create_unique_constraint('roles_nombre_key', 'roles', ['nombre'])

    _comparar(antes, _foto_de_permisos(bind))
