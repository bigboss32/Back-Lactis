"""LA CARRERA DE VERDAD: dos peticiones sobre la misma quincena, contra un Postgres de verdad.

POR QUÉ EXISTE ESTE ARCHIVO. Todo el candado que evita que dos usuarios se pisen la plata es
un `SELECT ... FOR UPDATE` (`_bloquear` en `liquidaciones/service.py`), y SQLite lo descarta
en silencio: en la suite, esa línea es decorativa. Las pruebas de `tests/` fijan que el
candado se pida y en qué orden, y que la petición relea lo que otro cambió por debajo, pero
solo aquí se ve que EL SEGUNDO DE VERDAD ESPERA y que la quincena queda cuadrada.

QUÉ HACE. Levanta su propio Postgres desechable (no toca ningún otro contenedor), y por cada
escenario arma una quincena real por la API, DETIENE a la petición A justo después de que
leyó lo que iba a usar, deja que la petición B intente pasar, y suelta a A. Después revisa
la base con las igualdades que el dueño verifica a mano:

  · regla de oro: saldo = valor_total − anticipos − saldo_anterior − pagado;
  · la liquidación es la suma de sus recepciones y de sus renglones;
  · una aprobada conserva sus recepciones; una anulada no las conserva;
  · un anticipo se descuenta UNA sola vez: lo que restaron las quincenas del proveedor es lo
    que se le entregó (y lo que quedó marcado como aplicado).

Se corre contra DOS códigos y se comparan: el de un commit viejo (`--antes`, por omisión
HEAD) y el del árbol de trabajo. En el código sin candado B pasa por encima de A y la
quincena queda descuadrada; con candado B espera y las igualdades se cumplen.

    .venv/Scripts/python.exe herramientas/carrera_de_candados.py
    .venv/Scripts/python.exe herramientas/carrera_de_candados.py --antes 0ba84ae

Con Docker prendido. Al terminar baja y borra el contenedor y la carpeta temporal.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONTENEDOR = "lactis-carrera-candado"
PUERTO = 15433
BASE = "lactis_carrera"
IMAGEN = "postgres:16"
URL = f"postgresql+psycopg2://postgres:carrera@localhost:{PUERTO}/{BASE}"
PY = str(RAIZ / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():  # linux/mac
    PY = str(RAIZ / ".venv" / "bin" / "python")

# Cuánto espera el orquestador a B con A detenida: con candado B no puede avanzar; sin él
# termina en menos de un segundo. Y cuánto espera A a que se la suelte, como tope.
ESPERA_DE_B = 3.0
TOPE_DE_A = 20.0
CLAVE = "Carrera1234*"


# ====================================================================== orquestador
def correr(cmd, *, env=None, cwd=None):
    return subprocess.run(
        cmd, cwd=cwd or RAIZ, env={**os.environ, **(env or {})},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def docker(*args):
    return correr(["docker", *args])


def levantar_postgres():
    print(f"==> Postgres desechable '{CONTENEDOR}' en el puerto {PUERTO} (no toca ningún otro)")
    docker("rm", "-f", "-v", CONTENEDOR)
    r = docker(
        "run", "-d", "--name", CONTENEDOR,
        "-e", "POSTGRES_PASSWORD=carrera", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
        # SOLO en loopback: la base entra sin contraseña (`trust`) y no debe verse desde la red.
        "-e", f"POSTGRES_DB={BASE}", "-p", f"127.0.0.1:{PUERTO}:5432", IMAGEN,
    )
    if r.returncode != 0:
        sys.exit(f"no se pudo levantar Postgres: {r.stderr[:400]}")
    for _ in range(120):
        if docker("exec", CONTENEDOR, "pg_isready", "-U", "postgres", "-d", BASE).returncode == 0:
            time.sleep(1.5)  # el primer pg_isready sale antes de que acepte conexiones de afuera
            print("    listo")
            return
        time.sleep(1)
    sys.exit("Postgres no arrancó")


def extraer_codigo(commit: str, destino: Path) -> Path:
    """El `app/` de un commit, sin tocar el repositorio (git archive no deja rastro)."""
    destino.mkdir(parents=True, exist_ok=True)
    git = subprocess.Popen(["git", "archive", commit, "app"], cwd=RAIZ, stdout=subprocess.PIPE)
    tar = subprocess.run(["tar", "-x", "-C", str(destino)], stdin=git.stdout)
    git.stdout.close()
    if git.wait() != 0 or tar.returncode != 0:
        sys.exit(f"no se pudo extraer {commit}")
    return destino


def correr_hijo(raiz: Path) -> list[dict]:
    r = correr(
        [PY, str(Path(__file__).resolve()), "--hijo", "--raiz", str(raiz)],
        env={"DATABASE_URL": URL, "PYTHONIOENCODING": "utf-8"}, cwd=raiz,
    )
    marca = "@@RESULTADOS@@"
    if marca not in r.stdout:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        sys.exit(f"el hijo no devolvió resultados ({raiz})")
    return json.loads(r.stdout.split(marca, 1)[1])


def imprimir(titulo: str, resultados: list[dict]) -> bool:
    print(f"\n=== {titulo}")
    todo_bien = True
    for r in resultados:
        bien = not r["violaciones"]
        todo_bien &= bien
        print(f"\n  [{'OK ' if bien else 'MAL'}] {r['nombre']}")
        print(f"       A: {r['a']}")
        print(f"       B: {r['b']}")
        print(f"       B pudo pasar con A detenida: {'SÍ' if r['b_paso_con_a_detenida'] else 'no, esperó el candado'}")
        print(f"       al final: {r['final']}")
        for v in r["violaciones"]:
            print(f"       ✗ {v}")
    return todo_bien


def principal():
    # La consola de Windows (cp1252) no sabe escribir Σ ni ≠ ni ✗.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--antes", default="HEAD", help="commit del código SIN el candado (por omisión HEAD)")
    ap.add_argument("--solo-ahora", action="store_true", help="no corre el código anterior")
    ap.add_argument("--hijo", action="store_true")
    ap.add_argument("--raiz")
    args = ap.parse_args()
    if args.hijo:
        return hijo(Path(args.raiz))

    carpeta = Path(tempfile.mkdtemp(prefix="carrera-candados-"))
    try:
        levantar_postgres()
        if not args.solo_ahora:
            if args.antes == "HEAD" and correr(["git", "diff", "--quiet", "HEAD", "--", "app"]).returncode == 0:
                print("\n  OJO: el árbol de trabajo no tiene cambios en app/ respecto a HEAD, así que las dos corridas")
                print("  usan el MISMO código y las dos darán lo mismo. Pase --antes <commit sin el candado>.")
            anterior = extraer_codigo(args.antes, carpeta / "antes")
            imprimir(f"CÓDIGO ANTERIOR ({args.antes})", correr_hijo(anterior))
        bien = imprimir("CÓDIGO DEL ÁRBOL DE TRABAJO", correr_hijo(RAIZ))
    finally:
        # `-v` borra también el volumen anónimo que Postgres crea: sin él cada corrida dejaba uno.
        docker("rm", "-f", "-v", CONTENEDOR)
        shutil.rmtree(carpeta, ignore_errors=True)
    print("\nEl código del árbol de trabajo " + ("CUMPLE todas las igualdades." if bien else "NO cumple: ver arriba."))
    sys.exit(0 if bien else 1)


# ============================================================================= hijo
def hijo(raiz: Path):
    os.chdir(raiz)
    sys.path.insert(0, str(raiz))
    from decimal import Decimal

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    import app.models_registry  # noqa: F401
    from app.core.database import Base, SessionLocal, engine
    from app.core.security import hash_password
    from app.main import app as aplicacion
    from app.modules.empresas.models import Empresa
    from app.modules.liquidaciones import service as servicio
    from app.modules.liquidaciones.repository import AnticipoRepository
    from app.modules.usuarios.models import Usuario
    from app.seeds.seed import ensure_catalogos_empresas, seed_permisos, seed_roles

    # `drop_all` borra TODO: solo contra la base desechable de esta herramienta, nunca contra otra.
    destino = engine.url
    if (destino.host, destino.port, destino.database) != ("localhost", PUERTO, BASE):
        sys.exit(f"me niego a borrar la base {destino!r}: esta herramienta solo trabaja en localhost:{PUERTO}/{BASE}")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        permisos = seed_permisos(db)
        roles = seed_roles(db, permisos)
        empresa = Empresa(nombre="Quesera Carrera", nit="900CARRERA")
        db.add(empresa)
        db.flush()
        ensure_catalogos_empresas(db)
        usuario = Usuario(
            nombre="Admin", apellido="Carrera", correo="admin@carrera.local", username="admin.carrera",
            hashed_password=hash_password(CLAVE), empresa_id=empresa.id,
        )
        usuario.roles = [roles["Administrador Empresa"]]
        db.add(usuario)
        db.commit()

    def D(v):
        return Decimal(str(v))

    def consulta(sql, **p):
        with engine.connect() as c:
            return [dict(f._mapping) for f in c.execute(text(sql), p)]

    resultados: list[dict] = []
    contador = {"n": 0}

    with TestClient(aplicacion) as cliente:
        r = cliente.post("/api/v1/auth/login", data={"username": "admin.carrera", "password": CLAVE})
        assert r.status_code == 200, r.text
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        API = "/api/v1/liquidaciones"

        def quincena(con_anticipo=False):
            contador["n"] += 1
            nombre = f"Proveedor {contador['n']}"
            r = cliente.post("/api/v1/proveedores", json={"nombre": nombre, "vereda": "X", "precio_litro": "1900"}, headers=h)
            assert r.status_code == 201, r.text
            prov = r.json()["id"]
            for i, litros in enumerate(("100", "150", "200")):
                r = cliente.post("/api/v1/recepciones", json={"fecha": f"2026-07-{16 + i}", "proveedor_id": prov, "cantidad_litros": litros}, headers=h)
                assert r.status_code == 201, r.text
            r = cliente.post(f"{API}/generar", json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31", "tipo": "proveedor"}, headers=h)
            assert r.status_code == 200, r.text
            liq = next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)
            if con_anticipo:
                r = cliente.post("/api/v1/anticipos", json={"tipo": "proveedor", "proveedor_id": prov, "fecha": "2026-07-20", "valor": "500000"}, headers=h)
                assert r.status_code == 201, r.text
            return liq["id"], liq["detalles"][0]["id"], prov

        def revisar(liq_id, aprobados=()):
            """Las igualdades, leídas de la BASE. Devuelve (violaciones, resumen).

            `aprobados` son los valor_total que las respuestas "aprobada" le dijeron al usuario:
            lo que se aprobó tiene que ser lo que quedó guardado."""
            f = consulta("select estado, valor_total, anticipos, saldo_anterior, pagado, saldo from liquidaciones where id=:i", i=liq_id)[0]
            rec = consulta(
                "select count(*) n, coalesce(sum(valor_bruto + bonificaciones - descuentos),0) total "
                "from recepciones_leche where liquidacion_id=:i and estado='activo' and deleted_at is null", i=liq_id)[0]
            det = consulta("select count(*) n, coalesce(sum(valor),0) total from liquidacion_detalles where liquidacion_id=:i and deleted_at is null", i=liq_id)[0]
            v = []
            esperado = D(f["valor_total"]) - D(f["anticipos"]) - D(f["saldo_anterior"] or 0) - D(f["pagado"])
            if D(f["saldo"]) != esperado:
                v.append(f"REGLA DE ORO: saldo {D(f['saldo'])} ≠ valor_total {D(f['valor_total'])} − anticipos {D(f['anticipos'])} − pagado {D(f['pagado'])} = {esperado}")
            if f["estado"] != "anulada" and D(f["valor_total"]) != D(rec["total"]):
                v.append(f"LA LIQUIDACIÓN NO SUMA SUS RECEPCIONES: valor_total {D(f['valor_total'])} ≠ recepciones {D(rec['total'])}")
            if f["estado"] != "anulada" and D(det["total"]) != D(f["valor_total"]):
                v.append(f"LOS RENGLONES NO SUMAN LA CIFRA GRANDE: renglones {D(det['total'])} ≠ valor_total {D(f['valor_total'])}")
            if f["estado"] == "aprobada" and rec["n"] != 3:
                v.append(f"APROBADA SIN SUS DÍAS: tiene {rec['n']} de 3 recepciones apartadas")
            if f["estado"] == "anulada" and rec["n"] != 0:
                v.append(f"ANULADA CON {rec['n']} RECEPCIONES TODAVÍA APARTADAS")
            for visto in aprobados:
                if D(visto) != D(f["valor_total"]):
                    v.append(f"SE APROBÓ UNA CIFRA Y QUEDÓ OTRA: la respuesta dijo valor_total {D(visto)} y en la base quedó {D(f['valor_total'])}")
            resumen = f"estado={f['estado']} valor_total={D(f['valor_total'])} anticipos={D(f['anticipos'])} saldo={D(f['saldo'])} recepciones={rec['n']} Σrecepciones={D(rec['total'])}"
            return v, resumen

        def dos_quincenas(mes, deudora=False):
            """Un proveedor con la quincena del 16 al 31 de julio ya GENERADA (borrador) y con
            recepciones del 1 al 3 de `mes` todavía sin generar; DESPUÉS se le registra un anticipo
            de $500.000 del 20 de julio: está suelto y le sirve a las dos quincenas (la de julio lo
            barre al aprobar, la siguiente al generar). Devuelve (liquidación de julio, datos, None).

            Con `deudora`, la de julio ya NACE debiendo (un anticipo de $900.000 antes de generar la
            deja en −$45.000: una deuda que la siguiente quincena va a cobrar, y por eso la va a
            BLOQUEAR) y el anticipo suelto de después es de $200.000."""
            contador["n"] += 1
            r = cliente.post("/api/v1/proveedores", json={"nombre": f"Proveedor {contador['n']}", "vereda": "X", "precio_litro": "1900"}, headers=h)
            assert r.status_code == 201, r.text
            prov = r.json()["id"]
            for i, litros in enumerate(("100", "150", "200")):
                r = cliente.post("/api/v1/recepciones", json={"fecha": f"2026-07-{16 + i}", "proveedor_id": prov, "cantidad_litros": litros}, headers=h)
                assert r.status_code == 201, r.text
            if deudora:
                r = cliente.post("/api/v1/anticipos", json={"tipo": "proveedor", "proveedor_id": prov, "fecha": "2026-07-17", "valor": "900000"}, headers=h)
                assert r.status_code == 201, r.text
            r = cliente.post(f"{API}/generar", json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31", "tipo": "proveedor"}, headers=h)
            assert r.status_code == 200, r.text
            liq = next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov)
            if deudora:
                assert D(liq["saldo"]) < 0, f"la quincena de julio tenía que nacer debiendo: {liq['saldo']}"
            for i, litros in enumerate(("120", "130", "140")):
                r = cliente.post("/api/v1/recepciones", json={"fecha": f"2026-{mes}-{1 + i:02d}", "proveedor_id": prov, "cantidad_litros": litros}, headers=h)
                assert r.status_code == 201, r.text
            suelto = "200000" if deudora else "500000"
            r = cliente.post("/api/v1/anticipos", json={"tipo": "proveedor", "proveedor_id": prov, "fecha": "2026-07-20", "valor": suelto}, headers=h)
            assert r.status_code == 201, r.text
            datos = {"prov": prov, "mes": mes, "anticipo": "1100000" if deudora else "500000"}
            return liq["id"], datos, datos

        def revisar_anticipo_unico(datos):
            """Un anticipo se descuenta UNA vez: lo que restaron las quincenas vivas del proveedor es
            lo que se le entregó y lo que quedó marcado, y cada quincena cumple la regla de oro."""
            prov = datos["prov"]
            restado = D(consulta("select coalesce(sum(anticipos),0) s from liquidaciones where proveedor_id=:p and estado<>'anulada' and deleted_at is null", p=prov)[0]["s"])
            marcado = D(consulta("select coalesce(sum(valor),0) s from anticipos where proveedor_id=:p and liquidacion_id is not null and deleted_at is null", p=prov)[0]["s"])
            entregado = D(datos["anticipo"])
            v = []
            if restado != entregado:
                v.append(f"UN ANTICIPO SE DESCONTÓ {'DOS VECES' if restado > entregado else 'MENOS DE LO QUE SE ENTREGÓ'}: las quincenas restaron ${restado} en total y se le entregaron ${entregado}")
            if marcado != entregado:
                v.append(f"EL ANTICIPO QUEDÓ MAL MARCADO: aplicado ${marcado} de ${entregado} entregados")
            partes = []
            for fila in consulta("select id, periodo_inicio, anticipos, estado from liquidaciones where proveedor_id=:p and estado<>'anulada' and deleted_at is null order by periodo_inicio", p=prov):
                vv, _ = revisar(str(fila["id"]))
                v.extend(f"[quincena {fila['periodo_inicio']}] {x}" for x in vv)
                partes.append(f"{fila['periodo_inicio']}:{fila['estado']} anticipos={D(fila['anticipos'])}")
            return v, "quincenas: " + " | ".join(partes)

        def carrera(nombre, con_anticipo, donde_se_detiene, accion_a, accion_b, antes=None,
                    preparar=None, revisar_extra=None, despues_de_leer=False):
            """A se detiene en `donde_se_detiene` (antes de ejecutarlo, o DESPUÉS de leer si
            `despues_de_leer`); B intenta; se suelta a A."""
            if preparar is not None:
                liq, dia, extra = preparar()
            else:
                liq, dia, _ = quincena(con_anticipo)
                extra = None
            if antes is not None:
                antes(liq, dia)
            llego, seguir, usada = threading.Event(), threading.Event(), [False]
            modulo_o_clase, atributo, es_metodo = donde_se_detiene
            original = getattr(modulo_o_clase, atributo)

            def envuelto(*a, **k):
                if not usada[0]:
                    usada[0] = True
                    if despues_de_leer:
                        # A ya leyó (y con candado, ya lo tiene puesto) y se queda esperando.
                        resultado = original(*a, **k)
                        llego.set()
                        seguir.wait(TOPE_DE_A)
                        return resultado
                    llego.set()
                    seguir.wait(TOPE_DE_A)
                return original(*a, **k)

            setattr(modulo_o_clase, atributo, envuelto)
            salida: dict = {}
            aprobados: list = []

            def pedir(clave, accion):
                try:
                    r = accion(liq, dia)
                    if r.status_code < 400 and r.json().get("estado") == "aprobada":
                        aprobados.append(r.json()["valor_total"])
                    detalle = ""
                    if r.status_code >= 400:
                        try:
                            detalle = r.json()["error"]["detail"][:90]
                        except Exception:
                            detalle = r.text[:90]
                    if r.status_code >= 400 and "concurrency_conflict" in r.text:
                        detalle = "concurrency_conflict · " + detalle
                    salida[clave] = f"HTTP {r.status_code}" + (f" · {detalle}" if detalle else "")
                except Exception as e:  # noqa: BLE001
                    salida[clave] = f"EXCEPCIÓN {type(e).__name__}: {str(e)[:90]}"

            try:
                ha = threading.Thread(target=pedir, args=("a", accion_a))
                ha.start()
                if not llego.wait(15):
                    raise RuntimeError("A no llegó al punto donde se detiene")
                hb = threading.Thread(target=pedir, args=("b", accion_b))
                hb.start()
                hb.join(ESPERA_DE_B)
                b_paso = not hb.is_alive()
                seguir.set()
                ha.join(60)
                hb.join(60)
            finally:
                setattr(modulo_o_clase, atributo, original)
                seguir.set()
            violaciones, final = revisar(liq, aprobados)
            if revisar_extra is not None:
                v_extra, f_extra = revisar_extra(extra)
                violaciones += v_extra
                final += " || " + f_extra
            for quien, texto in salida.items():
                if texto.startswith("EXCEPCIÓN") or " 5" in texto[:8]:
                    violaciones.append(f"{quien.upper()} TERMINÓ CON UN ERROR: {texto}")
                if "concurrency_conflict" in texto or "Otra persona estaba cambiando" in texto:
                    violaciones.append(f"{quien.upper()} SE ABRAZÓ CON LA OTRA (deadlock): {texto}")
            if ha.is_alive() or hb.is_alive():
                violaciones.append("UNA PETICIÓN QUEDÓ COLGADA después de soltar a A")
            resultados.append({
                "nombre": nombre, "a": salida.get("a"), "b": salida.get("b"),
                "b_paso_con_a_detenida": b_paso, "final": final, "violaciones": violaciones,
            })

        precio = lambda liq, dia: cliente.put(f"{API}/{liq}/detalles/{dia}", json={"precio_litro": "2000"}, headers=h)  # noqa: E731
        masivo = lambda liq, dia: cliente.post(f"{API}/{liq}/precios", json={"precio_litro": "2000"}, headers=h)  # noqa: E731
        aprobar = lambda liq, dia: cliente.post(f"{API}/{liq}/aprobar", headers=h)  # noqa: E731
        recalcular = lambda liq, dia: cliente.post(f"{API}/{liq}/recalcular", headers=h)  # noqa: E731
        anular = lambda liq, dia: cliente.post(f"{API}/{liq}/anular", headers=h)  # noqa: E731

        def desactualizada(liq, dia):
            """Alguien corrigió el precio de una recepción POR FUERA del comprobante (otra pantalla),
            así que el papel ya no suma lo que suman sus recepciones: justo cuando se oprime Recalcular."""
            with engine.begin() as c:
                c.execute(text(
                    "update recepciones_leche set precio_litro=2000, valor_bruto=cantidad_litros*2000, "
                    "valor_neto=cantidad_litros*2000 where liquidacion_id=:i and fecha=("
                    "select min(fecha) from recepciones_leche where liquidacion_id=:i)"), {"i": liq})

        S = servicio.LiquidacionService
        carrera("Aprobar (con un anticipo por descontar) mientras otro cambia el precio de UN día",
                True, (S, "_aplicar_anticipos_pendientes", True), aprobar, precio)
        carrera("Aprobar (con un anticipo por descontar) mientras otro cambia el precio de TODOS los días",
                True, (S, "_aplicar_anticipos_pendientes", True), aprobar, masivo)
        carrera("Recalcular una quincena DESACTUALIZADA mientras otro la APRUEBA",
                False, (servicio, "_refrescar_saldo", False), recalcular, aprobar, antes=desactualizada)
        carrera("Aprobar mientras otro ANULA la quincena",
                False, (S, "_transicionar", True), aprobar, anular)
        carrera("Cambiar el precio de UN día mientras otro APRUEBA (la aprobación entra primero)",
                False, (S, "_recalcular_desde_recepciones", True), precio, aprobar)

        # UN ANTICIPO NO SE DESCUENTA DOS VECES: Aprobar la quincena de julio y Generar la siguiente
        # leen el MISMO anticipo suelto (`pendientes_de`) y los dos lo restan; la marca queda
        # apuntando a la última que escribió, así que después no hay ni rastro de la otra.
        generar_siguiente = lambda mes: (lambda liq, dia: cliente.post(  # noqa: E731
            f"{API}/generar", json={"periodo_inicio": f"2026-{mes}-01", "periodo_fin": f"2026-{mes}-15", "tipo": "proveedor"}, headers=h))
        carrera("Aprobar julio (barre un anticipo suelto) mientras otro GENERA la quincena siguiente",
                False, (AnticipoRepository, "pendientes_de", True), aprobar, generar_siguiente("08"),
                preparar=lambda: dos_quincenas("08"), revisar_extra=revisar_anticipo_unico, despues_de_leer=True)
        carrera("Generar la quincena siguiente (toma un anticipo suelto) mientras otro APRUEBA julio",
                False, (AnticipoRepository, "pendientes_de", True), generar_siguiente("09"), aprobar,
                preparar=lambda: dos_quincenas("09"), revisar_extra=revisar_anticipo_unico, despues_de_leer=True)

        # EL ORDEN DE LOS CANDADOS: Aprobar toma primero la liquidación y después los anticipos;
        # Generar tomaba primero los anticipos y después la liquidación que le deja la deuda. Con
        # una quincena de julio que ya DEBE, las dos se esperaban entre sí (abrazo mortal, 40P01).
        carrera("Aprobar una quincena que ya DEBE (barre un anticipo suelto) mientras otro GENERA la siguiente (que le cobra la deuda)",
                False, (S, "_aplicar_anticipos_pendientes", True), aprobar, generar_siguiente("10"),
                preparar=lambda: dos_quincenas("10", deudora=True), revisar_extra=revisar_anticipo_unico)

        # CONTROL: sin carrera, una detrás de la otra, las igualdades se cumplen. Si esto
        # fallara, el problema sería del revisor y no del candado.
        liq, dia, _ = quincena(True)
        assert aprobar(liq, dia).status_code == 200
        violaciones, final = revisar(liq)
        resultados.append({"nombre": "CONTROL sin carrera: aprobar con anticipo, una sola petición", "a": "HTTP 200", "b": "—",
                           "b_paso_con_a_detenida": True, "final": final, "violaciones": violaciones})

    print("@@RESULTADOS@@" + json.dumps(resultados))


if __name__ == "__main__":
    principal()
