"""AUDITORIA (no arregla nada): las migraciones contra POSTGRES DE VERDAD.

La suite del proyecto monta el esquema con `create_all` sobre SQLite, asi que verde
NO dice nada sobre si `alembic upgrade head` corre en la base del cliente. Cada push a
Render corre `alembic upgrade head` sobre Postgres con la plata adentro.

Estas pruebas levantan un PostgreSQL 16 de verdad (paquete `pgserver`, binarios propios,
en el scratchpad, NUNCA la base del cliente) y miden. Si `pgserver` no esta instalado se
saltan enteras.
"""
import os
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACK = Path(__file__).resolve().parents[1]

pgserver = pytest.importorskip("pgserver", reason="hace falta `pip install pgserver`")
psycopg2 = pytest.importorskip("psycopg2")

PGDATA = Path(
    os.getenv(
        "AUDIT_PGDATA",
        r"C:\Users\MIGUEL~1\AppData\Local\Temp\claude"
        r"\D--maroa-hike-connect-back\3270a96e-c42e-4673-b958-dbcf53cfefeb"
        r"\scratchpad\pgdata",
    )
)


# ---------------------------------------------------------------------------
# Infraestructura: un Postgres de verdad, bases desechables
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def servidor():
    PGDATA.parent.mkdir(parents=True, exist_ok=True)
    return pgserver.get_server(PGDATA, cleanup_mode=None)


def _uri(servidor, db):
    return servidor.get_uri().rsplit("/", 1)[0] + "/" + db


def _sa_uri(servidor, db):
    return "postgresql+psycopg2://" + _uri(servidor, db).split("://", 1)[1]


def _base_nueva(servidor, nombre):
    con = psycopg2.connect(servidor.get_uri())
    con.autocommit = True
    cur = con.cursor()
    cur.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % nombre)
    cur.execute('CREATE DATABASE "%s"' % nombre)
    con.close()
    return _sa_uri(servidor, nombre)


def _alembic(url, *args):
    entorno = dict(os.environ, DATABASE_URL=url)
    return subprocess.run(
        ["alembic", *args],
        cwd=str(BACK),
        env=entorno,
        capture_output=True,
        text=True,
        errors="replace",
    )


# ---------------------------------------------------------------------------
# 1. LA CADENA: una sola cabeza, ninguna huerfana
# ---------------------------------------------------------------------------
def test_zzaudit_la_cadena_tiene_una_sola_cabeza_y_ninguna_huerfana():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    sd = ScriptDirectory.from_config(Config(str(BACK / "alembic.ini")))
    cabezas = sd.get_heads()
    assert len(cabezas) == 1, "la cadena tiene %d cabezas: %s" % (len(cabezas), cabezas)

    alcanzables = {r.revision: r for r in sd.walk_revisions("base", "heads")}

    # TODOS los archivos de revision que hay en disco, los alcance la cadena o no
    en_disco = {}
    for archivo in sorted((BACK / "alembic" / "versions").glob("*.py")):
        texto = archivo.read_text(encoding="utf-8", errors="replace")
        rev = None
        down = None
        for linea in texto.splitlines():
            if linea.startswith("revision:") or linea.startswith("revision "):
                rev = linea.split("=", 1)[1].strip().strip("'\"")
            elif linea.startswith("down_revision"):
                crudo = linea.split("=", 1)[1].strip()
                down = None if "None" in crudo else crudo.strip("'\"")
        if rev:
            en_disco[rev] = (archivo.name, down)

    sueltas = sorted(set(en_disco) - set(alcanzables))
    assert sueltas == [], "revisiones en disco que la cadena NO alcanza: %s" % (
        [(r, en_disco[r][0]) for r in sueltas],
    )
    huerfanas = [
        (rev, down)
        for rev, (_, down) in en_disco.items()
        if down is not None and down not in en_disco
    ]
    assert huerfanas == [], "revisiones con padre inexistente: %s" % (huerfanas,)
    print(
        "\nCADENA: %d revisiones en disco, %d alcanzables, 1 cabeza (%s), 0 huerfanas"
        % (len(en_disco), len(alcanzables), cabezas[0])
    )


# ---------------------------------------------------------------------------
# 2. `alembic upgrade head` corre entero contra Postgres
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def base_migrada(servidor):
    url = _base_nueva(servidor, "zzaudit_mig")
    r = _alembic(url, "upgrade", "head")
    assert r.returncode == 0, "`alembic upgrade head` fallo:\n" + r.stderr[-4000:]
    return url


@pytest.fixture(scope="session")
def base_modelos(servidor):
    url = _base_nueva(servidor, "zzaudit_mod")
    import app.models_registry  # noqa: F401
    from app.core.database import Base

    Base.metadata.create_all(create_engine(url))
    return url


def test_zzaudit_upgrade_head_corre_entero_en_postgres(base_migrada):
    with create_engine(base_migrada).connect() as con:
        rev = con.execute(text("select version_num from alembic_version")).scalar_one()
        tablas = con.execute(
            text(
                "select count(*) from information_schema.tables "
                "where table_schema='public' and table_name<>'alembic_version'"
            )
        ).scalar_one()
    print("\nUPGRADE HEAD en Postgres: revision %s, %d tablas" % (rev, tablas))
    assert tablas > 50


# ---------------------------------------------------------------------------
# 3. La forma que dejan las MIGRACIONES vs la que deja CREATE_ALL
# ---------------------------------------------------------------------------
_Q_COLS = """
select table_name, column_name, data_type, is_nullable,
       character_maximum_length, numeric_precision, numeric_scale
from information_schema.columns
where table_schema='public' and table_name <> 'alembic_version'
"""
_Q_TABLAS = (
    "select table_name from information_schema.tables "
    "where table_schema='public' and table_name<>'alembic_version'"
)
_Q_IDX = (
    "select tablename, indexdef from pg_indexes "
    "where schemaname='public' and tablename<>'alembic_version'"
)
_Q_CON = """
select rel.relname, pg_get_constraintdef(con.oid)
from pg_constraint con join pg_class rel on rel.oid=con.conrelid
join pg_namespace ns on ns.oid=rel.relnamespace
where ns.nspname='public' and rel.relname<>'alembic_version'
"""


def _foto(url):
    with create_engine(url).connect() as con:
        cols = {
            (f.table_name, f.column_name): tuple(f)[2:] for f in con.execute(text(_Q_COLS))
        }
        tablas = {f[0] for f in con.execute(text(_Q_TABLAS))}
        idx = sorted(tuple(f) for f in con.execute(text(_Q_IDX)))
        cons = sorted(tuple(f) for f in con.execute(text(_Q_CON)))
    return tablas, cols, idx, cons


def test_zzaudit_migraciones_y_createall_dejan_la_misma_forma(base_migrada, base_modelos):
    t_mig, c_mig, i_mig, k_mig = _foto(base_migrada)
    t_mod, c_mod, i_mod, k_mod = _foto(base_modelos)

    assert t_mig == t_mod, "tablas solo mig: %s ; solo mod: %s" % (
        sorted(t_mig - t_mod),
        sorted(t_mod - t_mig),
    )
    assert set(c_mig) == set(c_mod), "columnas solo mig: %s ; solo mod: %s" % (
        sorted(set(c_mig) - set(c_mod)),
        sorted(set(c_mod) - set(c_mig)),
    )
    distintas = {k: (c_mig[k], c_mod[k]) for k in c_mig if c_mig[k] != c_mod[k]}
    assert distintas == {}, (
        "columnas con tipo/nulabilidad/longitud/precision distinta (MIG, MOD): %s"
        % (distintas,)
    )
    # indices y constraints, comparados POR DEFINICION (el nombre puede diferir)
    assert i_mig == i_mod, "indices solo mig: %s ; solo mod: %s" % (
        sorted(set(i_mig) - set(i_mod)),
        sorted(set(i_mod) - set(i_mig)),
    )
    assert k_mig == k_mod, "constraints solo mig: %s ; solo mod: %s" % (
        sorted(set(k_mig) - set(k_mod)),
        sorted(set(k_mod) - set(k_mig)),
    )
    print(
        "\nFORMA IGUAL: %d tablas, %d columnas, %d indices, %d constraints"
        % (len(t_mig), len(c_mig), len(i_mig), len(k_mig))
    )


def test_zzaudit_defaults_de_servidor_que_solo_existen_en_las_migraciones(
    base_migrada, base_modelos
):
    """Documenta (y exige que no se contradigan) los server_default de cada lado."""
    q = text(
        "select table_name, column_name, column_default from information_schema.columns "
        "where table_schema='public' and column_default is not null "
        "and column_default not like 'nextval%'"
    )
    with create_engine(base_migrada).connect() as con:
        mig = {(f[0], f[1]): f[2] for f in con.execute(q)}
    with create_engine(base_modelos).connect() as con:
        mod = {(f[0], f[1]): f[2] for f in con.execute(q)}
    solo_mig = {k: mig[k] for k in mig if k not in mod}
    print("\nDEFAULTS solo en MIGRACIONES (el modelo no los declara):")
    for k, v in sorted(solo_mig.items()):
        print("   %s.%s = %s" % (k[0], k[1], v))
    print("DEFAULTS solo en MODELOS:", {k: mod[k] for k in mod if k not in mig})
    contradictorios = {k: (mig[k], mod[k]) for k in mig if k in mod and mig[k] != mod[k]}
    assert contradictorios == {}, (
        "la migracion y el modelo declaran defaults DISTINTOS: %s" % (contradictorios,)
    )


# ---------------------------------------------------------------------------
# 4. El SQL con dialecto Postgres: `alembic upgrade <rev>:head --sql`
# ---------------------------------------------------------------------------
URL_DE_MENTIRAS = "postgresql+psycopg2://u:p@127.0.0.1:1/nohay"


def _revisiones():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    sd = ScriptDirectory.from_config(Config(str(BACK / "alembic.ini")))
    return list(sd.walk_revisions("base", "heads"))[::-1]


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_el_sql_offline_de_la_cadena_completa_se_puede_generar():
    """`alembic upgrade base:head --sql` tiene que producir el script entero."""
    r = _alembic(URL_DE_MENTIRAS, "upgrade", "base:head", "--sql")
    rotas = []
    if r.returncode != 0:
        anterior = "base"
        for rev in _revisiones():
            p = _alembic(URL_DE_MENTIRAS, "upgrade", "%s:%s" % (anterior, rev.revision))
            anterior = rev.revision
        anterior = "base"
        for rev in _revisiones():
            p = _alembic(
                URL_DE_MENTIRAS, "upgrade", "%s:%s" % (anterior, rev.revision), "--sql"
            )
            if p.returncode != 0:
                ultima = p.stderr.strip().splitlines()[-1]
                rotas.append((rev.revision, (rev.doc or "")[:55], ultima))
            anterior = rev.revision
    for rev, doc, err in rotas:
        print("\nOFFLINE ROTA %s  %s\n      %s" % (rev, doc, err))
    assert r.returncode == 0, (
        "`alembic upgrade base:head --sql` NO se puede generar; %d de %d revisiones "
        "revientan en modo offline: %s"
        % (len(rotas), len(_revisiones()), ", ".join(x[0] for x in rotas))
    )


def test_zzaudit_el_sql_offline_no_hace_backfill_despues_de_botar_la_columna():
    """En el SQL generado, ningun UPDATE/INSERT lee una columna ya botada."""
    anterior = "base"
    problemas = []
    revisadas = 0
    for rev in _revisiones():
        p = _alembic(
            URL_DE_MENTIRAS, "upgrade", "%s:%s" % (anterior, rev.revision), "--sql"
        )
        anterior = rev.revision
        if p.returncode != 0:
            continue
        revisadas += 1
        sentencias = [s.strip() for s in p.stdout.split(";") if s.strip()]
        botadas = []
        for s in sentencias:
            plano = " ".join(s.split())
            bajo = plano.lower()
            if bajo.startswith("alter table") and " drop column " in bajo:
                tabla = plano.split()[2].strip('"').lower()
                col = bajo.split(" drop column ")[1].split()[0].strip('"')
                botadas.append((tabla, col))
            elif bajo.startswith("update ") or bajo.startswith("insert into "):
                for tabla, col in botadas:
                    if tabla in bajo and col in bajo:
                        problemas.append((rev.revision, tabla, col, plano[:120]))
    print("\nrevisiones cuyo SQL se pudo revisar: %d" % revisadas)
    for p_ in problemas:
        print("BACKFILL DESPUES DEL DROP:", p_)
    assert problemas == [], problemas


# ---------------------------------------------------------------------------
# 5. EL BACKFILL DEL FLETE, con plata y con DOS empresas
# ---------------------------------------------------------------------------
ANTES_DEL_DIA_FIJO = "a5c6d7e8f9a0"


def _sembrar_flete(con):
    """Dos queseras en la MISMA instalacion, cada una con su flete. Cifras feas."""
    ids = {}

    def nid(k):
        ids[k] = uuid.uuid4()
        return ids[k]

    for emp in ("A", "B"):
        con.execute(
            text(
                "insert into empresas (id, nombre, nit, created_at, updated_at) "
                "values (:i, :n, :nit, now(), now())"
            ),
            {"i": nid("emp" + emp), "n": "Quesera " + emp, "nit": "9001234" + emp},
        )
        con.execute(
            text(
                "insert into transportadores (id, empresa_id, nombre, valor_transporte, "
                "created_at, updated_at) values (:i,:e,:n,:v,now(),now())"
            ),
            {
                "i": nid("tra" + emp),
                "e": ids["emp" + emp],
                "n": "Alex " + emp,
                "v": Decimal("242.76"),
            },
        )
        for rt in ("fabrica", "napoles"):
            con.execute(
                text(
                    "insert into rutas (id, empresa_id, nombre, created_at, "
                    "updated_at) values (:i,:e,:n,now(),now())"
                ),
                {"i": nid("ruta" + emp + rt), "e": ids["emp" + emp], "n": rt},
            )
            con.execute(
                text(
                    "insert into transportador_rutas (id, transportador_id, ruta_id, "
                    "valor_transporte) values (:i,:t,:r,:v)"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": ids["tra" + emp],
                    "r": ids["ruta" + emp + rt],
                    "v": Decimal("317.50") if rt == "napoles" else Decimal("242.76"),
                },
            )
    return ids


def _liquidacion_flete(con, ids, emp, renglones, marca):
    """Un comprobante de transportador con sus renglones.

    `renglones` = lista de (ruta_key | None, litros, precio_litro, valor).
    """
    liq = uuid.uuid4()
    total_litros = sum(r[1] for r in renglones)
    total = sum(r[3] for r in renglones)
    con.execute(
        text(
            "insert into liquidaciones (id, empresa_id, tipo, transportador_id, "
            "periodo_inicio, periodo_fin, total_litros, precio_promedio, valor_bruto, "
            "bonificaciones, descuentos, valor_transporte, anticipos, valor_total, "
            "saldo, estado, created_at, updated_at) values "
            "(:i,:e,'transportador',:t,'2026-07-01','2026-07-15',:tl,0,0,0,0,:vt,0,:vt,"
            ":vt,'aprobada',now(),now())"
        ),
        {"i": liq, "e": ids["emp" + emp], "t": ids["tra" + emp], "tl": total_litros,
         "vt": total},
    )
    for n, (rt, litros, precio, valor) in enumerate(renglones):
        con.execute(
            text(
                "insert into liquidacion_detalles (id, liquidacion_id, fecha, ruta_id, "
                "litros, precio_litro, valor, estado, created_at, updated_at) values "
                "(:i,:l,:f,:r,:li,:p,:v,'aprobada',now(),now())"
            ),
            {
                "i": uuid.uuid4(),
                "l": liq,
                "f": "2026-07-%02d" % (n + 1),
                "r": None if rt is None else ids["ruta" + emp + rt],
                "li": litros,
                "p": precio,
                "v": valor,
            },
        )
    ids[marca] = liq
    return liq


COLUMNAS_DE_PLATA = {
    "transportadores": ("valor_transporte",),
    "transportador_rutas": ("valor_transporte",),
    "liquidacion_detalles": ("litros", "precio_litro", "valor"),
    "liquidaciones": ("total_litros", "valor_transporte", "valor_total", "saldo"),
}


def _medir_plata(con):
    medido = {}
    for tabla, columnas in COLUMNAS_DE_PLATA.items():
        sel = ", ".join("coalesce(sum(%s),0) as %s" % (c, c) for c in columnas)
        fila = con.execute(text("select count(*) as filas, %s from %s" % (sel, tabla))).one()
        medido[tabla] = {"filas": fila.filas}
        for c in columnas:
            medido[tabla][c] = Decimal(str(getattr(fila, c)))
    return medido


@pytest.fixture()
def base_con_flete(servidor):
    """Base migrada hasta JUSTO ANTES del dia fijo, con dos queseras y su flete."""
    url = _base_nueva(servidor, "zzaudit_flete")
    r = _alembic(url, "upgrade", ANTES_DEL_DIA_FIJO)
    assert r.returncode == 0, r.stderr[-3000:]
    eng = create_engine(url)
    with eng.begin() as con:
        ids = _sembrar_flete(con)
        # Quesera A: comprobante POR LITRO, dos rutas y un dia sin ruta. Cifras feas.
        _liquidacion_flete(
            con,
            ids,
            "A",
            [
                ("fabrica", Decimal("137.45"), Decimal("242.76"), Decimal("33367.36")),
                ("napoles", Decimal("82.00"), Decimal("317.50"), Decimal("26035.00")),
                (None, Decimal("44.23"), Decimal("242.76"), Decimal("10737.28")),
            ],
            "liqA",
        )
        # Quesera B: OTRA empresa en la misma instalacion, otra cifra
        _liquidacion_flete(
            con,
            ids,
            "B",
            [("fabrica", Decimal("233.75"), Decimal("1833.33"), Decimal("428540.89"))],
            "liqB",
        )
        # un comprobante de PROVEEDOR: no debe recibir memoria de rutas
        con.execute(
            text(
                "insert into liquidaciones (id, empresa_id, tipo, periodo_inicio, "
                "periodo_fin, total_litros, precio_promedio, valor_bruto, bonificaciones,"
                " descuentos, valor_transporte, anticipos, valor_total, saldo, estado, "
                "created_at, updated_at) values (:i,:e,'proveedor','2026-07-01',"
                "'2026-07-15',0,0,0,0,0,0,0,0,0,'aprobada',now(),now())"
            ),
            {"i": uuid.uuid4(), "e": ids["empA"]},
        )
    return url, ids, eng


def test_zzaudit_el_backfill_del_flete_no_mueve_un_peso_y_escribe_la_memoria(
    base_con_flete,
):
    url, ids, eng = base_con_flete
    with eng.connect() as con:
        antes = _medir_plata(con)

    r = _alembic(url, "upgrade", "head")
    assert r.returncode == 0, "el upgrade con datos de flete REVENTO:\n" + r.stderr[-4000:]

    with eng.connect() as con:
        despues = _medir_plata(con)
        memoria = con.execute(
            text(
                "select lr.liquidacion_id, lr.ruta_id, lr.modo_transporte, "
                "lr.precio_litro, lr.valor_dia_fijo, l.empresa_id "
                "from liquidacion_rutas lr join liquidaciones l on l.id=lr.liquidacion_id"
            )
        ).all()

    print("\nPLATA ANTES  :", antes)
    print("PLATA DESPUES:", despues)
    assert antes == despues, "el backfill movio plata"

    # cuatro filas: A x (fabrica, napoles, sin ruta) + B x fabrica. Proveedor: ninguna.
    assert len(memoria) == 4, [tuple(m) for m in memoria]
    por_empresa = {}
    for m in memoria:
        por_empresa.setdefault(m.empresa_id, []).append(m)
    assert set(por_empresa) == {ids["empA"], ids["empB"]}
    assert len(por_empresa[ids["empA"]]) == 3
    assert len(por_empresa[ids["empB"]]) == 1

    tarifas = {
        (str(m.liquidacion_id), str(m.ruta_id)): (m.modo_transporte, m.precio_litro)
        for m in memoria
    }
    assert tarifas[(str(ids["liqA"]), str(ids["rutaAfabrica"]))] == (
        "litro",
        Decimal("242.76"),
    )
    assert tarifas[(str(ids["liqA"]), str(ids["rutaAnapoles"]))] == (
        "litro",
        Decimal("317.50"),
    )
    assert tarifas[(str(ids["liqA"]), "None")] == ("litro", Decimal("242.76"))
    assert tarifas[(str(ids["liqB"]), str(ids["rutaBfabrica"]))] == (
        "litro",
        Decimal("1833.33"),
    )
    print("MEMORIA ESCRITA:", tarifas)


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_bajar_y_volver_a_subir_la_migracion_del_dia_fijo(base_con_flete):
    """El DOWNGRADE: de verdad devuelve la base, o solo lo aparenta?

    Se sube a head, se pone una ruta en DIA FIJO a $150.000 (que es lo que el dueno
    hace desde la pantalla), se baja hasta antes del dia fijo y se vuelve a subir.
    """
    url, ids, eng = base_con_flete
    assert _alembic(url, "upgrade", "head").returncode == 0

    liq = uuid.uuid4()
    with eng.begin() as con:
        # el dueno renegocia "a fabrica" a dia completo $150.000
        con.execute(
            text(
                "update transportador_rutas set modo_transporte='dia_fijo', "
                "valor_transporte=:v where ruta_id=:r"
            ),
            {"v": Decimal("150000.00"), "r": ids["rutaAfabrica"]},
        )
        # y emite un comprobante nuevo cobrando ese dia fijo
        con.execute(
            text(
                "insert into liquidaciones (id, empresa_id, tipo, transportador_id, "
                "periodo_inicio, periodo_fin, total_litros, precio_promedio, valor_bruto,"
                " bonificaciones, descuentos, valor_transporte, anticipos, valor_total, "
                "saldo, estado, created_at, updated_at) values (:i,:e,'transportador',:t,"
                "'2026-07-16','2026-07-31',:tl,0,0,0,0,:v,0,:v,:v,'aprobada',now(),now())"
            ),
            {
                "i": liq,
                "e": ids["empA"],
                "t": ids["traA"],
                "tl": Decimal("233.75"),
                "v": Decimal("150000.00"),
            },
        )
        con.execute(
            text(
                "insert into liquidacion_detalles (id, liquidacion_id, fecha, ruta_id, "
                "litros, precio_litro, valor, modo_transporte, dia_fijo_ya_cobrado, "
                "estado, created_at, updated_at) values (:i,:l,'2026-07-17',:r,:li,0,:v,"
                "'dia_fijo',false,'aprobada',now(),now())"
            ),
            {
                "i": uuid.uuid4(),
                "l": liq,
                "r": ids["rutaAfabrica"],
                "li": Decimal("233.75"),
                "v": Decimal("150000.00"),
            },
        )
        con.execute(
            text(
                "insert into liquidacion_rutas (id, liquidacion_id, ruta_id, "
                "modo_transporte, precio_litro, valor_dia_fijo, created_at, updated_at) "
                "values (:i,:l,:r,'dia_fijo',null,:v,now(),now())"
            ),
            {
                "i": uuid.uuid4(),
                "l": liq,
                "r": ids["rutaAfabrica"],
                "v": Decimal("150000.00"),
            },
        )

    with eng.connect() as con:
        antes = _medir_plata(con)
        tarifa_antes = con.execute(
            text(
                "select modo_transporte, valor_transporte from transportador_rutas "
                "where ruta_id=:r"
            ),
            {"r": ids["rutaAfabrica"]},
        ).one()
        renglon_antes = con.execute(
            text(
                "select modo_transporte, valor from liquidacion_detalles "
                "where liquidacion_id=:l"
            ),
            {"l": liq},
        ).one()

    b = _alembic(url, "downgrade", ANTES_DEL_DIA_FIJO)
    assert b.returncode == 0, "el downgrade revento:\n" + b.stderr[-4000:]
    s = _alembic(url, "upgrade", "head")
    assert s.returncode == 0, "el re-upgrade revento:\n" + s.stderr[-4000:]

    with eng.connect() as con:
        despues = _medir_plata(con)
        tarifa_despues = con.execute(
            text(
                "select modo_transporte, valor_transporte from transportador_rutas "
                "where ruta_id=:r"
            ),
            {"r": ids["rutaAfabrica"]},
        ).one()
        renglon_despues = con.execute(
            text(
                "select modo_transporte, valor from liquidacion_detalles "
                "where liquidacion_id=:l"
            ),
            {"l": liq},
        ).one()
        memoria_despues = con.execute(
            text(
                "select modo_transporte, precio_litro, valor_dia_fijo "
                "from liquidacion_rutas where liquidacion_id=:l"
            ),
            {"l": liq},
        ).all()

    print("\nTARIFA de la ruta ANTES  :", tuple(tarifa_antes))
    print("TARIFA de la ruta DESPUES:", tuple(tarifa_despues))
    print("RENGLON del comprobante ANTES  :", tuple(renglon_antes))
    print("RENGLON del comprobante DESPUES:", tuple(renglon_despues))
    print("MEMORIA del comprobante DESPUES:", [tuple(m) for m in memoria_despues])
    print("SUMAS ANTES  :", antes)
    print("SUMAS DESPUES:", despues)

    movidas = [
        (t, c, antes[t][c], despues[t][c])
        for t in antes
        for c in antes[t]
        if antes[t][c] != despues[t][c]
    ]
    assert movidas == [], "bajar y volver a subir MOVIO PLATA: %s" % (movidas,)
    assert (tarifa_antes.modo_transporte, tarifa_antes.valor_transporte) == (
        tarifa_despues.modo_transporte,
        tarifa_despues.valor_transporte,
    ), "la tarifa de la ruta no volvio como estaba"
    assert renglon_antes.modo_transporte == renglon_despues.modo_transporte, (
        "el renglon del comprobante cambio de modo"
    )
    assert [tuple(m) for m in memoria_despues] == [
        ("dia_fijo", None, Decimal("150000.00"))
    ], "la memoria del comprobante no volvio como estaba"


# ---------------------------------------------------------------------------
# 6. QUE PASA SI EL DEPLOY SE CORTA A MITAD DE LA CADENA
# ---------------------------------------------------------------------------
def test_zzaudit_si_una_migracion_revienta_no_queda_media_cadena_aplicada(servidor):
    """`start.sh` corre `alembic upgrade head` de una. Si la ULTIMA revienta,
    la anterior no puede quedar aplicada: la base del cliente quedaria en una forma
    que ningun codigo espera."""
    url = _base_nueva(servidor, "zzaudit_corte")
    assert _alembic(url, "upgrade", ANTES_DEL_DIA_FIJO).returncode == 0

    eng = create_engine(url)
    # se planta un obstaculo para que la ULTIMA migracion (c3f8a1d6b0e5) reviente
    with eng.begin() as con:
        con.execute(text("create table liquidacion_rutas (x int)"))

    r = _alembic(url, "upgrade", "head")
    assert r.returncode != 0, "el upgrade tenia que reventar y no revento"

    with eng.connect() as con:
        rev = con.execute(text("select version_num from alembic_version")).scalar_one()
        columna_de_la_anterior = con.execute(
            text(
                "select count(*) from information_schema.columns where table_schema="
                "'public' and table_name='liquidacion_detalles' and "
                "column_name='modo_transporte'"
            )
        ).scalar_one()
    print("\nTRAS EL CORTE: alembic_version=%s ; "
          "liquidacion_detalles.modo_transporte existe=%s" % (rev, bool(columna_de_la_anterior)))
    assert rev == ANTES_DEL_DIA_FIJO, (
        "alembic_version quedo en %s: la cadena avanzo a pesar del error" % rev
    )
    assert columna_de_la_anterior == 0, (
        "la migracion ANTERIOR quedo aplicada aunque la ultima revento: la base quedo "
        "a medio camino"
    )


# ---------------------------------------------------------------------------
# 7. varchar: lo que SQLite deja pasar y Postgres rechaza con un 500
# ---------------------------------------------------------------------------
@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_la_clave_del_producto_puede_no_caber_en_su_propia_columna(base_migrada):
    """`clave_de_producto` recorta a 80 y DESPUES se le puede pegar un sufijo."""
    from app.modules.reventa.service import (
        SUFIJO_PARA_NO_CHOCAR,
        clave_de_producto,
        clave_sin_chocar_con_el_desglose,
    )
    from app.modules.reventa.schemas import ProductoReventaCreate

    # un nombre de 80 caracteres, que es EXACTAMENTE lo que el esquema admite
    nombre = ("Queso costeno artesanal del Guaviare curado en hoja de platano " "y merma")
    nombre = nombre + "x" * (80 - len(nombre) - len(" merma")) + " merma"
    assert len(nombre) == 80, len(nombre)
    ProductoReventaCreate(nombre=nombre)  # el esquema lo acepta: max_length=80

    clave = clave_sin_chocar_con_el_desglose(clave_de_producto(nombre))
    print("\nNOMBRE (%d chars): %s" % (len(nombre), nombre))
    print("CLAVE  (%d chars): %s" % (len(clave), clave))
    print("sufijo que la empuja:", SUFIJO_PARA_NO_CHOCAR)

    import sqlalchemy.exc as saexc

    eng = create_engine(base_migrada)
    emp = uuid.uuid4()
    with eng.begin() as con:
        con.execute(
            text(
                "insert into empresas (id, nombre, nit, created_at, updated_at) "
                "values (:i,'Quesera del corte','900zz',now(),now())"
            ),
            {"i": emp},
        )
    error = None
    try:
        with eng.begin() as con:
            con.execute(
                text(
                    "insert into productos_reventa (id, empresa_id, nombre, clave, "
                    "unidad, decimales, admite_ajustes, orden, created_at, updated_at) "
                    "values (:i,:e,:n,:c,'kg',2,true,1,now(),now())"
                ),
                {"i": uuid.uuid4(), "e": emp, "n": nombre, "c": clave},
            )
    except saexc.DBAPIError as exc:  # pragma: no cover - es justo lo que se mide
        error = str(exc.orig).strip().splitlines()[0]
    print("POSTGRES DICE:", error)
    assert error is None, (
        "la clave calculada (%d caracteres) NO cabe en productos_reventa.clave "
        "varchar(80): Postgres responde %r. En SQLite entra sin chistar."
        % (len(clave), error)
    )


# ---------------------------------------------------------------------------
# 8. El downgrade completo CON DATOS adentro
# ---------------------------------------------------------------------------
def test_zzaudit_downgrade_base_con_datos_adentro(base_con_flete):
    """Bajar toda la cadena con plata en la base: tiene que dejarla vacia de verdad."""
    url, ids, eng = base_con_flete
    assert _alembic(url, "upgrade", "head").returncode == 0

    r = _alembic(url, "downgrade", "base")
    assert r.returncode == 0, "downgrade base con datos revento:\n" + r.stderr[-4000:]

    with eng.connect() as con:
        sobran = [
            f[0]
            for f in con.execute(
                text(
                    "select table_name from information_schema.tables "
                    "where table_schema='public' and table_name<>'alembic_version'"
                )
            )
        ]
        secuencias = [
            f[0]
            for f in con.execute(
                text(
                    "select sequence_name from information_schema.sequences "
                    "where sequence_schema='public'"
                )
            )
        ]
        tipos = [
            f[0]
            for f in con.execute(
                text(
                    "select t.typname from pg_type t join pg_namespace n "
                    "on n.oid=t.typnamespace where n.nspname='public' and t.typtype='e'"
                )
            )
        ]
    print("\nTRAS DOWNGRADE BASE con datos: tablas=%s secuencias=%s enums=%s"
          % (sobran, secuencias, tipos))
    assert sobran == [], "quedaron tablas: %s" % (sobran,)
    assert secuencias == [] and tipos == []


# ---------------------------------------------------------------------------
# 9. Que tan fuerte es el post-vuelo de la migracion cabeza
# ---------------------------------------------------------------------------
@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_el_postvuelo_de_la_memoria_no_es_una_segunda_lectura_de_la_regla():
    """El post-vuelo dice que compara "lo escrito contra los renglones".

    Compara la tabla contra una re-derivacion hecha con LAS MISMAS expresiones SQL
    (`_columnas_de_la_memoria()`), asi que puede detectar una fila que no se escribio
    o que se guardo con otra precision, pero NO puede detectar que la regla este mal:
    los dos lados se equivocarian igual. Esto lo mide comparando el SQL de las dos.
    """
    import importlib.util

    ruta = BACK / "alembic" / "versions" / "c3f8a1d6b0e5_el_comprobante_guarda_como_cobro_cada_ruta.py"
    spec = importlib.util.spec_from_file_location("mig_memoria", ruta)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    from sqlalchemy.dialects import postgresql

    dial = postgresql.dialect()

    def sql(stmt):
        return str(stmt.compile(dialect=dial, compile_kwargs={"literal_binds": True}))

    grupos = sql(mig._grupos_de_la_memoria())
    insert = sql(mig.sentencia_backfill_de_la_memoria())

    def cuerpo(texto):
        # el pedazo que calcula modo / precio / fijo, sin el envoltorio del INSERT
        return " ".join(texto.split()).split("FROM liquidacion_detalles")[0]

    a = cuerpo(grupos)
    b = cuerpo(insert)
    # el INSERT ademas trae id, created_at y updated_at; se recortan para comparar
    for extra in ("CAST(min(CAST(liquidacion_detalles.id AS TEXT)) AS UUID) AS id,",
                  ", now() AS created_at, now() AS updated_at"):
        b = b.replace(extra, "")
    a = a.replace("SELECT ", "").strip()
    b = b.replace("INSERT INTO liquidacion_rutas (id, liquidacion_id, ruta_id, "
                  "modo_transporte, precio_litro, valor_dia_fijo, created_at, "
                  "updated_at) SELECT ", "").strip()
    print("\nEXPRESION del POST-VUELO :", a[:220])
    print("EXPRESION del BACKFILL   :", b[:220])
    assert a != b, (
        "el post-vuelo y el backfill usan LA MISMA expresion SQL, palabra por palabra: "
        "no es una segunda lectura de la regla y no puede delatar una regla equivocada"
    )


# ---------------------------------------------------------------------------
# 10. c6b1e4a8d3f7: el backfill que descarta filas y DESPUES bota la columna
# ---------------------------------------------------------------------------
ANTES_DE_LAS_RUTAS = "a2f7c1d4b8e6"
LAS_RUTAS = "c6b1e4a8d3f7"


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_zzaudit_la_tarifa_de_una_ruta_de_otra_empresa_se_pierde_sin_avisar(servidor):
    """`c6b1e4a8d3f7` salva `transportadores.ruta_id` en `transportador_rutas` y
    DESPUES bota la columna. El INSERT ... SELECT junta con
    `rutas.empresa_id = transportadores.empresa_id`: la fila cuya ruta era de la OTRA
    quesera no entra, y un renglon despues la columna desaparece. No hay pre-vuelo,
    no hay post-vuelo y no se cuenta nada: la tarifa se va en silencio.
    """
    url = _base_nueva(servidor, "zzaudit_rutas")
    assert _alembic(url, "upgrade", ANTES_DE_LAS_RUTAS).returncode == 0
    eng = create_engine(url)

    ids = {}
    with eng.begin() as con:
        for emp in ("A", "B"):
            ids["emp" + emp] = uuid.uuid4()
            con.execute(
                text(
                    "insert into empresas (id, nombre, nit, created_at, updated_at) "
                    "values (:i,:n,:nit,now(),now())"
                ),
                {"i": ids["emp" + emp], "n": "Quesera " + emp, "nit": "9001234" + emp},
            )
            ids["ruta" + emp] = uuid.uuid4()
            con.execute(
                text(
                    "insert into rutas (id, empresa_id, nombre, created_at, updated_at) "
                    "values (:i,:e,:n,now(),now())"
                ),
                {"i": ids["ruta" + emp], "e": ids["emp" + emp], "n": "fabrica " + emp},
            )
        # el transportador de A quedo apuntando a una ruta de B (la base lo admite:
        # la FK es contra rutas(id) y no mira empresa_id)
        ids["sano"] = uuid.uuid4()
        ids["cruzado"] = uuid.uuid4()
        con.execute(
            text(
                "insert into transportadores (id, empresa_id, nombre, valor_transporte,"
                " ruta_id, created_at, updated_at) values "
                "(:i,:e,'Alex sano',:v,:r,now(),now())"
            ),
            {"i": ids["sano"], "e": ids["empA"], "v": Decimal("242.76"),
             "r": ids["rutaA"]},
        )
        con.execute(
            text(
                "insert into transportadores (id, empresa_id, nombre, valor_transporte,"
                " ruta_id, created_at, updated_at) values "
                "(:i,:e,'Alex cruzado',:v,:r,now(),now())"
            ),
            {"i": ids["cruzado"], "e": ids["empA"], "v": Decimal("1833.33"),
             "r": ids["rutaB"]},
        )

    r = _alembic(url, "upgrade", LAS_RUTAS)
    assert r.returncode == 0, r.stderr[-3000:]

    with eng.connect() as con:
        salvadas = {
            str(f[0]): (str(f[1]), f[2])
            for f in con.execute(
                text("select transportador_id, ruta_id, valor_transporte "
                     "from transportador_rutas")
            )
        }
        columna = con.execute(
            text(
                "select count(*) from information_schema.columns where table_schema="
                "'public' and table_name='transportadores' and column_name='ruta_id'"
            )
        ).scalar_one()
    print("\nTARIFAS SALVADAS:", salvadas)
    print("la columna transportadores.ruta_id sigue existiendo:", bool(columna))
    print("SALIDA de alembic (avisos):", (r.stdout + r.stderr).count("cruzado"),
          "menciones del transportador que se perdio")

    assert str(ids["sano"]) in salvadas
    assert str(ids["cruzado"]) in salvadas, (
        "la tarifa de $1.833,33 del transportador cuya ruta era de la otra quesera NO "
        "quedo en transportador_rutas, y la columna transportadores.ruta_id ya se boto "
        "(existe=%s): el dato se perdio y la migracion no dijo nada." % bool(columna)
    )
