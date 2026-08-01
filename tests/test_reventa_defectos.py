"""Un defecto por prueba, de la revisión a fondo del módulo de reventa.

Hay un cliente real usando esto con dinero de verdad. Todos los de aquí se
reprodujeron contra la API antes de arreglarlos.
"""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from tests.conftest import auth_headers

API = "/api/v1/reventa"


def D(v):
    return Decimal(str(v))


def comprar(client, h, kilos, precio, productor="Yeferson", dias=10):
    r = client.post(
        f"{API}/compras",
        json={
            "fecha": str(date.today() - timedelta(days=dias)),
            "productor": productor,
            "kilos_brutos": str(kilos),
            "borona_kilos": "0",
            "precio_kilo": str(precio),
        },
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def vender(client, h, kilos, precio, cliente="Tienda La 33", dias=2, tipo="queso"):
    r = client.post(
        f"{API}/ventas",
        json={
            "fecha": str(date.today() - timedelta(days=dias)),
            "cliente": cliente,
            "tipo": tipo,
            "kilos": str(kilos),
            "precio_kilo": str(precio),
        },
        headers=h,
    )
    return r


# ---------------------------------------------------------------------------
# 1. ALTO: editar una venta se saltaba el control de existencias
# ---------------------------------------------------------------------------
def test_editar_una_venta_no_puede_inventar_kilos(client, base_datos):
    """El guardia estaba SOLO en crear. Se creaba una venta de 1 kg y se editaba
    a 500, y pasaba: el resumen quedaba con kilos negativos, con una ganancia
    que no era la real, y el desglose por lote decía otra cosa que el resumen.
    Justo lo que el dueño ve al cuadrar a mano.
    """
    h = auth_headers(client, "admin.a")
    comprar(client, h, "100", "18000")
    v = vender(client, h, "100", "21000").json()

    # Crear una más ya no se puede: no queda queso. Ese guardia sí estaba.
    extra = vender(client, h, "1", "21000")
    print("\n===== 1. EDITAR UNA VENTA =====")
    print(f"  crear otra venta de 1 kg: {extra.status_code} · "
          f"{extra.json().get('error', {}).get('detail', '')}")
    assert extra.status_code == 422

    # Y editar la que hay para inflarla, tampoco
    r = client.put(f"{API}/ventas/{v['id']}", json={"kilos": "500"}, headers=h)
    print(f"  editarla a 500 kg:        {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422, "se pudo vender queso que nunca se compró"
    assert "disponible" in r.json()["error"]["detail"]


def test_editar_una_venta_sin_pasarse_si_se_puede(client, base_datos):
    """El arreglo no puede volverse un estorbo: al editar hay que devolverle al
    inventario los kilos que esa misma venta ya tenía apartados, o si no dejar
    100 kg en 100 kg fallaría."""
    h = auth_headers(client, "admin.a")
    comprar(client, h, "100", "18000")
    v = vender(client, h, "100", "21000").json()

    print("\n===== 2. EDITAR SIN PASARSE =====")
    igual = client.put(f"{API}/ventas/{v['id']}", json={"kilos": "100"}, headers=h)
    print(f"  dejarla igual (100 kg):   {igual.status_code}")
    assert igual.status_code == 200, igual.text

    menos = client.put(f"{API}/ventas/{v['id']}", json={"kilos": "60"}, headers=h)
    print(f"  bajarla a 60 kg:          {menos.status_code}")
    assert menos.status_code == 200, menos.text

    # Y ahora que sobran 40, subirla a 100 otra vez tiene que poder
    otra_vez = client.put(f"{API}/ventas/{v['id']}", json={"kilos": "100"}, headers=h)
    print(f"  volver a 100 kg:          {otra_vez.status_code}")
    assert otra_vez.status_code == 200, otra_vez.text


# ---------------------------------------------------------------------------
# 2. ALTO: borrar un abono pedía 'crear' en vez de 'eliminar'
# ---------------------------------------------------------------------------
def test_borrar_un_pago_exige_el_permiso_de_eliminar(client, db_session, base_datos):
    """Borrar un abono es la operación más destructiva de plata del módulo: baja
    lo abonado, sube el saldo y hace desaparecer el pago del PDF que se le
    entrega al productor. Y es un borrado DURO, la fila no queda ni en papelera.

    Pedía 'crear', el mismo permiso que registrar un pago. O sea que a quien se
    le dio permiso de anotar pagos —y a quien se le negó a propósito el de
    borrar compras— podía borrarlos.
    """
    from app.modules.usuarios.models import Permiso, Rol, Usuario, UsuarioRol

    h = auth_headers(client, "admin.a")
    c = comprar(client, h, "100", "50000")
    r = client.post(
        f"{API}/compras/{c['id']}/abonos",
        json={"fecha": str(date.today()), "valor": c["valor_total"]},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    abono_id = r.json()["abonos"][0]["id"]

    # Un usuario que puede anotar pagos pero NO eliminar
    empresa = base_datos["empresa_a"]
    permisos = db_session.scalars(
        select(Permiso).where(
            Permiso.modulo == "reventa",
            Permiso.accion.in_(("consultar", "crear", "editar")),
        )
    ).all()
    rol = Rol(nombre="Anotador de pagos", descripcion="prueba")
    rol.permisos = list(permisos)
    db_session.add(rol)
    db_session.flush()
    from app.core.security import hash_password

    from tests.conftest import PASSWORD

    u = Usuario(
        nombre="Ana", apellido="Pagos", correo="ana.pagos@test.local",
        username="ana.pagos", hashed_password=hash_password(PASSWORD),
        empresa_id=empresa.id,
    )
    db_session.add(u)
    db_session.flush()
    db_session.add(UsuarioRol(usuario_id=u.id, rol_id=rol.id, empresa_id=empresa.id))
    db_session.commit()

    ha = auth_headers(client, "ana.pagos")
    print("\n===== 3. BORRAR UN PAGO =====")
    # Puede anotar pagos: para eso se le dio 'crear'
    otro = client.post(
        f"{API}/compras/{c['id']}/abonos",
        json={"fecha": str(date.today()), "valor": "1"},
        headers=ha,
    )
    print(f"  anotar un pago:  {otro.status_code}")

    borrar = client.delete(f"{API}/compras/{c['id']}/abonos/{abono_id}", headers=ha)
    print(f"  borrar el pago:  {borrar.status_code}  (tiene que ser 403)")
    assert borrar.status_code == 403, "pudo borrar un pago sin permiso de eliminar"

    # Y quien sí tiene 'eliminar' puede
    con_permiso = client.delete(f"{API}/compras/{c['id']}/abonos/{abono_id}", headers=h)
    print(f"  con permiso:     {con_permiso.status_code}")
    assert con_permiso.status_code in (200, 204)


# ---------------------------------------------------------------------------
# 3. Los abonos se leen bajo bloqueo (no se puede probar en SQLite, se deja fijo
#    el que la ruta siga funcionando después de meter el FOR UPDATE)
# ---------------------------------------------------------------------------
def test_los_abonos_siguen_funcionando_con_el_bloqueo_puesto(client, base_datos):
    """SQLite descarta el FOR UPDATE en silencio, así que esta prueba NO
    demuestra que el bloqueo sirva: eso solo se ve en Postgres. Lo que fija es
    que la relectura con populate_existing no rompió el camino normal, que es
    donde suelen aparecer las sorpresas (objetos rancios, abonos que no salen)."""
    h = auth_headers(client, "admin.a")
    c = comprar(client, h, "10", "10000")

    for valor in ("30000", "20000"):
        r = client.post(
            f"{API}/compras/{c['id']}/abonos",
            json={"fecha": str(date.today()), "valor": valor},
            headers=h,
        )
        assert r.status_code in (200, 201), r.text
        fila = r.json()

    print("\n===== 4. ABONOS CON BLOQUEO =====")
    print(f"  valor {fila['valor_total']} · abonado {fila['abonado']} · saldo {fila['saldo']}")
    assert D(fila["abonado"]) == D("50000")
    assert D(fila["saldo"]) == D("50000")

    # Pasarse del saldo sigue estando prohibido
    r = client.post(
        f"{API}/compras/{c['id']}/abonos",
        json={"fecha": str(date.today()), "valor": "50001"},
        headers=h,
    )
    print(f"  abonar de más: {r.status_code}")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 4. ALTO: bajar los kilos de una compra ya vendida dejaba el inventario negativo
# ---------------------------------------------------------------------------
def test_no_se_pueden_quitar_kilos_de_una_compra_ya_vendida(client, base_datos):
    """Bajar una compra de 100 kg a 10 cuando ya se vendieron 80 dejaba el
    inventario en -70. A partir de ahí NINGUNA venta pasa el control de
    existencias: el dueño se queda sin poder trabajar sin entender por qué."""
    h = auth_headers(client, "admin.a")
    c = comprar(client, h, "100", "10000")
    vender(client, h, "80", "15000")

    print("\n===== 5. QUITARLE KILOS A UNA COMPRA VENDIDA =====")
    r = client.put(f"{API}/compras/{c['id']}", json={"kilos_brutos": "10"}, headers=h)
    print(f"  bajarla de 100 a 10 kg: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422

    # Bajar hasta lo vendido sí se puede: quedan 20 kg sin vender
    ok = client.put(f"{API}/compras/{c['id']}", json={"kilos_brutos": "80"}, headers=h)
    print(f"  bajarla a 80 kg:        {ok.status_code}")
    assert ok.status_code == 200, ok.text

    # Y subirla siempre se puede
    mas = client.put(f"{API}/compras/{c['id']}", json={"kilos_brutos": "150"}, headers=h)
    print(f"  subirla a 150 kg:       {mas.status_code}")
    assert mas.status_code == 200, mas.text


# ---------------------------------------------------------------------------
# 5. Decisión del dueño: si el queso ya se vendió, la compra NO se anula
# ---------------------------------------------------------------------------
def test_no_se_anula_una_compra_cuyo_queso_ya_se_vendio(client, base_datos):
    """Anularla borraría de la cuenta un queso que salió de verdad: el
    inventario se iría a negativo y, con el inventario negativo, ninguna venta
    vuelve a pasar el control. El dueño se queda sin poder trabajar."""
    h = auth_headers(client, "admin.a")
    c = comprar(client, h, "100", "10000")
    vender(client, h, "80", "15000")

    r = client.post(f"{API}/compras/{c['id']}/anular", headers=h)
    print("\n===== 6. ANULAR UNA COMPRA YA VENDIDA =====")
    print(f"  {r.status_code} · {r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "ya se vendió" in r.json()["error"]["detail"]


def test_una_compra_sin_vender_si_se_anula(client, base_datos):
    """El arreglo no puede volverse un estorbo: lo que no ha salido se anula."""
    h = auth_headers(client, "admin.a")
    c = comprar(client, h, "100", "10000")
    r = client.post(f"{API}/compras/{c['id']}/anular", headers=h)
    print("\n===== 7. ANULAR UNA SIN VENDER =====")
    print(f"  {r.status_code}")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 6. Cuánto se ganó de verdad en unos días concretos
# ---------------------------------------------------------------------------
def test_la_ganancia_por_dia_es_exacta_y_los_dias_suman(client, base_datos):
    """Lo que pidió el dueño: "cuánto gané en determinados días".

    De cada venta de esos días se toma lo que entró menos lo que había costado
    ESE queso (FIFO exacto, no un promedio) menos el flete.
    """
    h = auth_headers(client, "admin.a")
    # Dos lotes a precios distintos, para que el FIFO tenga algo que decir
    comprar(client, h, "100", "10000", dias=30)   # $10.000/kg
    comprar(client, h, "100", "12000", dias=20)   # $12.000/kg

    # Día A: 60 kg, todos del lote barato
    vender(client, h, "60", "15000", dias=10)
    # Día B: 60 kg, 40 del barato y 20 del caro
    vender(client, h, "60", "16000", dias=9)

    desde = str(date.today() - timedelta(days=12))
    hasta = str(date.today() - timedelta(days=8))
    r = client.get(f"{API}/ganancia-por-dia",
                   params={"desde": desde, "hasta": hasta}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()

    print("\n===== 8. GANANCIA POR DÍA =====")
    for dia in d["dias"]:
        print(f"  {dia['fecha']}  {dia['kilos']:>7} kg   entró {dia['ingresos']:>12}"
              f"   costó {dia['costo']:>12}   ganó {dia['ganancia']:>12}")
    print(f"  TOTAL                  entró {d['ingresos']:>12}"
          f"   costó {d['costo']:>12}   ganó {d['ganancia']:>12}")

    # Día A: 60 x 15.000 = 900.000, costó 60 x 10.000 = 600.000 -> ganó 300.000
    dia_a = next(x for x in d["dias"] if D(x["kilos"]) == 60)
    assert D(dia_a["ingresos"]) == D("900000")
    assert D(dia_a["costo"]) == D("600000")
    assert D(dia_a["ganancia"]) == D("300000")

    # Día B: 60 x 16.000 = 960.000; costó 40x10.000 + 20x12.000 = 640.000
    dia_b = next(x for x in d["dias"] if x is not dia_a)
    assert D(dia_b["ingresos"]) == D("960000")
    assert D(dia_b["costo"]) == D("640000")
    assert D(dia_b["ganancia"]) == D("320000")

    # Y los días SUMAN el total, sin sobrar ni faltar un peso
    assert sum((D(x["ganancia"]) for x in d["dias"]), D(0)) == D(d["ganancia"])
    assert sum((D(x["ingresos"]) for x in d["dias"]), D(0)) == D(d["ingresos"])
    assert D(d["ganancia"]) == D("620000")


def test_la_ganancia_por_dia_deja_fuera_lo_que_no_esta_en_el_rango(client, base_datos):
    """Y solo cuenta lo de esos días: una venta de antes no se cuela."""
    h = auth_headers(client, "admin.a")
    comprar(client, h, "100", "10000", dias=30)
    vender(client, h, "50", "15000", dias=25)   # fuera del rango
    vender(client, h, "50", "15000", dias=5)    # dentro

    r = client.get(f"{API}/ganancia-por-dia", params={
        "desde": str(date.today() - timedelta(days=7)),
        "hasta": str(date.today()),
    }, headers=h)
    d = r.json()
    print("\n===== 9. SOLO LO DEL RANGO =====")
    print(f"  días contados: {len(d['dias'])}  ·  ganancia {d['ganancia']}")
    assert len(d["dias"]) == 1
    assert D(d["ganancia"]) == D("250000")
