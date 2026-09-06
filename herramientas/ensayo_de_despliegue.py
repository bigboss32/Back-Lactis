"""EL ENSAYO DEL DESPLIEGUE: correr las migraciones contra un Postgres de verdad.

POR QUÉ EXISTE ESTE ARCHIVO. La suite monta el esquema con `Base.metadata.create_all`,
no con alembic. Eso quiere decir que **una suite verde no dice absolutamente nada sobre
si las migraciones corren**, y durante doce migraciones seguidas no hubo forma de
saberlo: se subían a la base de un cliente real sin haberlas ejecutado nunca. Este
script es esa forma.

Y hay una segunda cosa que solo se puede probar aquí: SQLite IGNORA EN SILENCIO el
`SELECT ... FOR UPDATE`. Todo el candado que evita que dos peticiones simultáneas se
pisen la plata es, en la suite, una línea decorativa. En Postgres además falla con 0A000
si la consulta lleva un LEFT JOIN de por medio — este proyecto ya se quemó con eso, y es
la razón de que varias relaciones estén en lazy="select".

QUÉ HACE, en el mismo orden en que ocurre en Render (ver start.sh):

  1. levanta un Postgres 16 desechable, aparte de todo lo demás;
  2. lo deja EN EL ESTADO EN QUE ESTÁ LA BASE DEL CLIENTE: migra hasta la revisión que
     hay desplegada, usando EL CÓDIGO DE ESE COMMIT (un worktree de git), porque el
     código de hoy no puede leer el esquema de ayer;
  3. le mete plata de verdad con ese mismo código: un proveedor, una recepción, una
     quincena generada, aprobada y PAGADA, y un gasto;
  4. corre `alembic upgrade head` encima, que es el despliegue;
  5. comprueba que ni una cifra se movió, que las columnas nuevas nacieron con el valor
     que debían sobre las filas viejas, y que `alembic check` no encuentra diferencias
     entre el esquema migrado y los modelos;
  6. baja y vuelve a subir, para saber qué se pierde si hay que revertir.

CÓMO SE CORRE (desde Back-Lactis, con Docker prendido):

    .venv/Scripts/python.exe herramientas/ensayo_de_despliegue.py <revision-desplegada>

Por ejemplo, si en Render está corriendo la revisión b3d9f6c2a8e1:

    .venv/Scripts/python.exe herramientas/ensayo_de_despliegue.py b3d9f6c2a8e1

Si no se le pasa nada, usa la revisión que la base de `docker-compose` tenga puesta.
Al terminar borra el contenedor y el worktree. NO toca ningún otro contenedor.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONTENEDOR = "lactis-ensayo-despliegue"
PUERTO = 15432
BASE = "lactis_ensayo"
URL = f"postgresql+psycopg2://postgres:ensayo@localhost:{PUERTO}/{BASE}"
WORKTREE = RAIZ.parent / ".ensayo-desplegado"
PY = str(RAIZ / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():  # linux/mac
    PY = str(RAIZ / ".venv" / "bin" / "python")


def correr(cmd, *, cwd=None, env=None, callar=False):
    entorno = {**os.environ, **(env or {})}
    r = subprocess.run(
        cmd, cwd=cwd or RAIZ, env=entorno, shell=isinstance(cmd, str),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if not callar and r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
    return r


def docker(*args, callar=True):
    return correr(["docker", *args], callar=callar)


def levantar_postgres():
    print("==> Postgres 16 desechable (no toca ningún otro contenedor)")
    docker("rm", "-f", CONTENEDOR)
    r = docker(
        "run", "-d", "--name", CONTENEDOR,
        "-e", "POSTGRES_PASSWORD=ensayo",
        # `trust` porque es una base de usar y tirar en localhost; y porque al
        # reiniciarse el contenedor la IP cambia y pg_hba rechaza la conexión.
        "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
        "-e", f"POSTGRES_DB={BASE}",
        "-p", f"{PUERTO}:5432", "postgres:16-alpine",
        callar=False,
    )
    if r.returncode != 0:
        sys.exit(f"no se pudo levantar Postgres: {r.stderr[:400]}")
    for _ in range(120):
        if docker("exec", CONTENEDOR, "pg_isready", "-U", "postgres", "-d", BASE).returncode == 0:
            print("    listo")
            return
        time.sleep(1)
    sys.exit("Postgres no arrancó")


def sql(consulta: str) -> str:
    r = docker("exec", CONTENEDOR, "psql", "-U", "postgres", "-d", BASE, "-tc", consulta)
    return (r.stdout or "").strip()


def alembic(*args, cwd=None, pythonpath=None):
    entorno = {"DATABASE_URL": URL}
    if pythonpath:
        entorno["PYTHONPATH"] = str(pythonpath)
    return correr([PY, "-m", "alembic", *args], cwd=cwd, env=entorno)


def worktree_del_commit(revision_desplegada: str) -> Path | None:
    """El código del commit que trajo esa revisión de alembic. None si es la de hoy.

    Hace falta porque el código de HOY no puede escribir en el esquema de AYER: los
    modelos ya tienen las columnas nuevas y cualquier consulta revienta con
    `UndefinedColumn`. Para que el ensayo sea fiel, los datos los tiene que escribir el
    código que está corriendo en producción.
    """
    # El archivo que DEFINE esa revisión, y el commit que lo AÑADIÓ. No vale buscar la
    # cadena con `git log -S`: la revisión aparece también como `down_revision` en la
    # migración SIGUIENTE, así que esa búsqueda devuelve el commit equivocado —el de
    # después— y el ensayo terminaría montando el esquema de mañana creyendo que es el
    # de hoy.
    archivo = None
    for candidato in (RAIZ / "alembic" / "versions").glob("*.py"):
        texto = candidato.read_text(encoding="utf-8", errors="replace")
        if f"revision: str = '{revision_desplegada}'" in texto or \
           f'revision: str = "{revision_desplegada}"' in texto:
            archivo = candidato
            break
    if archivo is None:
        print(f"    !! no encontré la migración {revision_desplegada}")
        return None
    r = correr(["git", "log", "--diff-filter=A", "--format=%H", "--",
                f"alembic/versions/{archivo.name}"])
    commits = [c for c in (r.stdout or "").split() if c]
    if not commits:
        return None
    commit = commits[0]
    correr(["git", "worktree", "remove", "--force", str(WORKTREE)], callar=True)
    r = correr(["git", "worktree", "add", "--detach", str(WORKTREE), commit], callar=False)
    if r.returncode != 0:
        return None
    print(f"    código del commit {commit[:8]} en {WORKTREE.name}")
    return WORKTREE


def limpiar():
    docker("rm", "-f", CONTENEDOR)
    correr(["git", "worktree", "remove", "--force", str(WORKTREE)], callar=True)


# Lo que siembra la plata, escrito para correrse CON EL CÓDIGO VIEJO. Va como texto y no
# como función porque lo ejecuta OTRO intérprete —el del worktree del commit desplegado—:
# el de hoy no puede ni importar los modelos contra el esquema de ayer.
#
# Y siembra una quincena PAGADA a propósito. Una base vacía haría que el ensayo compare
# cero contra cero y diga "la plata no se movió", que es la clase de prueba que pasa por
# la razón equivocada. Si el paso 1 no deja liquidaciones, el ensayo aborta.
SEMBRAR_PLATA = '''
import json, sys
from fastapi.testclient import TestClient
from app.main import create_app

API = "/api/v1"
c = TestClient(create_app())
r = c.post(f"{API}/auth/login", data={"username": "admin", "password": "Admin123*"})
assert r.status_code == 200, r.text
h = {"Authorization": "Bearer " + r.json()["access_token"]}
emp = c.get(f"{API}/empresas", headers=h).json()["items"][0]["id"]
h["X-Empresa-Id"] = emp

prov = c.post(f"{API}/proveedores", json={
    "nombre": "Ensayo Productor", "vereda": "El Roble", "precio_litro": "1800"},
    headers=h).json()
c.post(f"{API}/recepciones", json={
    "fecha": "2026-06-02", "proveedor_id": prov["id"], "cantidad_litros": "277.78"}, headers=h)
gen = c.post(f"{API}/liquidaciones/generar", json={
    "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "proveedor"},
    headers=h).json()["generadas"]
liq = next(x for x in gen if x["proveedor_id"] == prov["id"])
c.post(f"{API}/liquidaciones/{liq['id']}/aprobar", headers=h)
pagada = c.post(f"{API}/liquidaciones/{liq['id']}/pagar", headers=h)
assert pagada.status_code == 200, pagada.text

cats = c.get(f"{API}/categorias-gasto?page_size=100", headers=h).json()["items"]
cat = next(x for x in cats if x["nombre"] == "Combustible")
c.post(f"{API}/gastos", json={
    "fecha": "2026-07-03", "categoria_id": cat["id"], "concepto": "ACPM",
    "valor": "242760.75"}, headers=h)
print("SEMBRADO", json.dumps({"liquidacion": liq["id"], "empresa": emp}))
'''


def main() -> int:
    revision = sys.argv[1] if len(sys.argv) > 1 else "head"
    print(f"ENSAYO DEL DESPLIEGUE — la base del cliente está en: {revision}\n")
    levantar_postgres()

    codigo_viejo = worktree_del_commit(revision) if revision != "head" else None
    cwd = codigo_viejo or RAIZ
    pythonpath = codigo_viejo or RAIZ

    print(f"\n==> 1. La base como está hoy en producción ({revision})")
    r = alembic("upgrade", revision, cwd=cwd, pythonpath=pythonpath)
    if r.returncode != 0:
        limpiar()
        return 1
    correr([PY, "-m", "app.seeds.seed"], cwd=cwd,
           env={"DATABASE_URL": URL, "PYTHONPATH": str(pythonpath)})
    print(f"    revisión: {sql('select version_num from alembic_version;')}")

    print("    sembrando plata con ESE código (una quincena pagada y un gasto)")
    r = correr([PY, "-c", SEMBRAR_PLATA], cwd=cwd,
               env={"DATABASE_URL": URL, "PYTHONPATH": str(pythonpath)})
    if r.returncode != 0 or "SEMBRADO" not in (r.stdout or ""):
        print("!! no se pudo sembrar plata con el código desplegado; sin datos el ensayo")
        print("   compararía cero contra cero y diría que todo está bien.")
        print((r.stdout or "")[-1500:])
        print((r.stderr or "")[-1500:])
        limpiar()
        return 1

    antes = {
        "liquidaciones": sql("select count(*) from liquidaciones;"),
        "plata": sql("select coalesce(sum(valor_total),0)||' / '||coalesce(sum(pagado),0) "
                     "from liquidaciones where deleted_at is null;"),
        "gastos": sql("select coalesce(sum(valor),0) from gastos where deleted_at is null;"),
    }
    print(f"    liquidaciones: {antes['liquidaciones']} · total/pagado: {antes['plata']}"
          f" · gastos: {antes['gastos']}")
    # SIN DATOS EL ENSAYO NO PRUEBA NADA: comparar 0 contra 0 siempre da "no se movió".
    if antes["liquidaciones"].strip() in ("", "0"):
        print("!! el paso 1 no dejó ninguna liquidación: el ensayo no probaría nada")
        limpiar()
        return 1

    print("\n==> 2. EL DESPLIEGUE: alembic upgrade head, con datos encima")
    r = alembic("upgrade", "head")
    if r.returncode != 0:
        print("!! LA MIGRACIÓN FALLÓ. En Render, start.sh tiene `set -e`: el despliegue")
        print("   se cae y la app NO arranca contra un esquema a medias.")
        limpiar()
        return 1
    print(f"    revisión: {sql('select version_num from alembic_version;')}")

    despues = {
        "liquidaciones": sql("select count(*) from liquidaciones;"),
        "plata": sql("select coalesce(sum(valor_total),0)||' / '||coalesce(sum(pagado),0) "
                     "from liquidaciones where deleted_at is null;"),
        "gastos": sql("select coalesce(sum(valor),0) from gastos where deleted_at is null;"),
    }
    print(f"    liquidaciones: {despues['liquidaciones']} · total/pagado: {despues['plata']}"
          f" · gastos: {despues['gastos']}")

    fallos = []
    if antes != despues:
        fallos.append(f"LA PLATA SE MOVIÓ: {antes} -> {despues}")

    print("\n==> 3. ¿El esquema migrado es el mismo que el de los modelos?")
    r = alembic("check")
    salida = (r.stdout or "") + (r.stderr or "")
    if "No new upgrade operations detected" in salida:
        print("    sí: no hay diferencias")
    else:
        fallos.append("EL ESQUEMA MIGRADO NO COINCIDE CON LOS MODELOS")
        print("    " + salida.strip()[-1500:])

    print(f"\n==> 4. Bajar a {revision} y volver a subir")
    if alembic("downgrade", revision).returncode != 0:
        fallos.append(f"LA BAJADA A {revision} FALLA (no se puede revertir el despliegue)")
    elif alembic("upgrade", "head").returncode != 0:
        fallos.append("NO SE PUEDE VOLVER A SUBIR DESPUÉS DE BAJAR")
    else:
        r = alembic("check")
        if "No new upgrade operations" in ((r.stdout or "") + (r.stderr or "")):
            print("    el ciclo cierra y el esquema sigue coincidiendo")
        else:
            fallos.append("TRAS BAJAR Y SUBIR, EL ESQUEMA YA NO COINCIDE")

    print("\n" + "=" * 62)
    if fallos:
        print("EL ENSAYO ENCONTRÓ PROBLEMAS — NO SUBIR:")
        for f in fallos:
            print("  !! " + f)
        limpiar()
        return 1
    print("ENSAYO LIMPIO: las migraciones corren sobre datos y no mueven una cifra.")
    print("OJO CON LA BAJADA: revertir borra lo que las tablas nuevas hubieran guardado.")
    limpiar()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        limpiar()
        sys.exit(130)
