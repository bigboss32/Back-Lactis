"""¿Cuadra al peso? La prueba que hace el dueño con el cuaderno al lado.

No busca defectos de código: comprueba la propiedad que el cliente verifica de
verdad —que la suma de las partes dé EXACTO la cifra grande— y la comprueba con
números feos a propósito: precios que no dividen redondo, una venta que se come
dos lotes, una compra anulada por el medio y borona.

Los números redondos esconden los errores de redondeo. Estos no.

OJO CON LOS KILOS: en la base son Numeric(12,2), o sea DOS decimales. Escribir
esta prueba destapó un defecto de dinero — el total se calculaba con los kilos
CRUDOS mientras la columna guardaba los redondeados, así que la fila se
contradecía sola en Postgres y SQLite no lo delataba. Ahora los kilos se
redondean en la entrada; las dos últimas pruebas de este archivo lo fijan.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"
CENTAVO = Decimal("0.01")


def D(v):
    """Los Decimal viajan como string en JSON."""
    return Decimal(str(v))


@pytest.fixture()
def h(client, base_datos):
    # Depende de base_datos a propósito: sin él las fixtures corren en el orden
    # equivocado y el login falla porque el usuario todavía no existe.
    return auth_headers(client, "admin.a")


def comprar(client, h, *, dias_atras, productor, kilos, precio, borona="0"):
    r = client.post(
        f"{API}/compras",
        json={
            "fecha": str(date.today() - timedelta(days=dias_atras)),
            "productor": productor,
            "kilos_brutos": str(kilos),
            "borona_kilos": str(borona),
            "precio_kilo": str(precio),
        },
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def vender(client, h, *, dias_atras, cliente, kilos, precio, tipo="queso", contado=False):
    r = client.post(
        f"{API}/ventas",
        json={
            "fecha": str(date.today() - timedelta(days=dias_atras)),
            "cliente": cliente,
            "tipo": tipo,
            "kilos": str(kilos),
            "precio_kilo": str(precio),
            "pagada_de_contado": contado,
        },
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def resumen(client, h, dias=90):
    r = client.get(
        f"{API}/resumen",
        params={
            "desde": str(date.today() - timedelta(days=dias)),
            "hasta": str(date.today()),
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1. El total de una compra es kilos x precio, al peso
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kilos,precio",
    [
        ("33.33", "13333"),       # nada divide redondo
        ("0.01", "999999"),       # el mínimo, precio enorme
        ("7.77", "1"),            # el peso por kilo que usa el cliente de verdad
        ("1234.56", "8901"),      # cifras grandes
        ("99.99", "10333"),       # el clásico que descuadra por medio centavo
    ],
)
def test_el_total_de_la_compra_es_kilos_por_precio(client, h, kilos, precio):
    c = comprar(client, h, dias_atras=1, productor="Yeferson", kilos=kilos, precio=precio)
    esperado = (D(kilos) * D(precio)).quantize(CENTAVO)
    print(f"\n  {kilos} kg x ${precio} = {c['valor_total']}   (a mano: {esperado})")
    assert D(c["valor_total"]) == esperado
    # Y el saldo de una compra recién hecha es todo el valor: no se ha abonado nada
    assert D(c["saldo"]) == esperado
    assert D(c["abonado"]) == 0


# ---------------------------------------------------------------------------
# 2. El resumen contra las filas, sumadas a mano
# ---------------------------------------------------------------------------
def test_el_resumen_cuadra_con_numeros_feos(client, h):
    compras = [
        comprar(client, h, dias_atras=20, productor="Yeferson", kilos="123.45", precio="9877"),
        comprar(client, h, dias_atras=15, productor="Marlion", kilos="77.77", precio="10333"),
        comprar(client, h, dias_atras=10, productor="Yeferson", kilos="0.99", precio="12345"),
    ]
    ventas = [
        vender(client, h, dias_atras=5, cliente="Tienda La 33", kilos="99.11", precio="15777"),
        vender(client, h, dias_atras=2, cliente="Doña Rosa", kilos="50.5", precio="16333", contado=True),
    ]

    res = resumen(client, h)
    total_compras = sum((D(c["valor_total"]) for c in compras), D(0))
    total_ventas = sum((D(v["valor_total"]) for v in ventas), D(0))
    kilos_comprados = sum((D(c["kilos_netos"]) for c in compras), D(0))
    kilos_vendidos = sum((D(v["kilos"]) for v in ventas), D(0))

    print("\n===== EL RESUMEN CONTRA LAS FILAS =====")
    print(f"  compras:  filas {total_compras:>14}   resumen {res['total_compras']:>14}")
    print(f"  ventas:   filas {total_ventas:>14}   resumen {res['total_ventas']:>14}")
    print(f"  kg compr: filas {kilos_comprados:>14}   resumen {res['kilos_comprados']:>14}")
    print(f"  kg vend:  filas {kilos_vendidos:>14}   resumen {res['kilos_vendidos']:>14}")
    assert D(res["total_compras"]) == total_compras
    assert D(res["total_ventas"]) == total_ventas
    assert D(res["kilos_comprados"]) == kilos_comprados
    assert D(res["kilos_vendidos"]) == kilos_vendidos

    # La ganancia estimada es ventas - compras - gastos, ni un peso más
    esperada = total_ventas - total_compras - D(res["total_gastos"])
    print(f"  ganancia: a mano {esperada:>14}   resumen {res['ganancia_estimada']:>14}")
    assert D(res["ganancia_estimada"]) == esperada


# ---------------------------------------------------------------------------
# 3. Los desgloses suman la cifra grande (esto es lo que el dueño revisa)
# ---------------------------------------------------------------------------
def test_los_desgloses_suman_la_cifra_grande(client, h):
    comprar(client, h, dias_atras=20, productor="Yeferson", kilos="123.45", precio="9877")
    comprar(client, h, dias_atras=18, productor="Marlion", kilos="77.77", precio="10333")
    comprar(client, h, dias_atras=16, productor="Yubigildo", kilos="45.33", precio="11111")
    vender(client, h, dias_atras=5, cliente="Tienda La 33", kilos="99.11", precio="15777")
    vender(client, h, dias_atras=4, cliente="Doña Rosa", kilos="50.5", precio="16333")
    vender(client, h, dias_atras=3, cliente="Tienda La 33", kilos="30.25", precio="15900")

    res = resumen(client, h)
    print("\n===== LOS DESGLOSES =====")
    for p in res["por_productor"]:
        print(f"  {p.get('productor', '?'):12} " +
              " ".join(f"{k}={v}" for k, v in p.items() if k != "productor"))

    # Los kilos de los productores tienen que dar los kilos comprados
    kilos_desglose = sum((D(p["kilos"]) for p in res["por_productor"] if "kilos" in p), D(0))
    if kilos_desglose:
        print(f"  kilos: desglose {kilos_desglose}  ·  resumen {res['kilos_comprados']}")
        assert kilos_desglose == D(res["kilos_comprados"]), (
            "los kilos por productor no suman los kilos comprados"
        )


# ---------------------------------------------------------------------------
# 4. Una compra anulada no puede seguir sumando en ningún lado
# ---------------------------------------------------------------------------
def test_una_compra_anulada_desaparece_de_todas_las_cifras(client, h):
    buena = comprar(client, h, dias_atras=10, productor="Yeferson", kilos="50.5", precio="10000")
    mala = comprar(client, h, dias_atras=9, productor="Marlion", kilos="33.33", precio="9999")

    antes = resumen(client, h)
    r = client.post(f"{API}/compras/{mala['id']}/anular", headers=h)
    assert r.status_code == 200, r.text
    despues = resumen(client, h)

    print("\n===== UNA COMPRA ANULADA =====")
    print(f"  compras antes:   {antes['total_compras']}")
    print(f"  la anulada:      {mala['valor_total']}")
    print(f"  compras después: {despues['total_compras']}   (debe ser {buena['valor_total']})")
    assert D(despues["total_compras"]) == D(buena["valor_total"])
    assert D(despues["kilos_comprados"]) == D(buena["kilos_netos"])
    # Y no puede seguir en la cartera por pagar
    print(f"  por pagar: {antes['por_pagar_productores']} -> {despues['por_pagar_productores']}")
    assert D(despues["por_pagar_productores"]) == D(buena["valor_total"])


# ---------------------------------------------------------------------------
# 5. Los lotes suman lo mismo que el resumen
# ---------------------------------------------------------------------------
def test_la_suma_de_los_lotes_da_el_total(client, h):
    """Si esto no cuadra el dueño lo ve: abre "Ganancia por lote", suma la
    columna y la compara con el resumen."""
    comprar(client, h, dias_atras=25, productor="Yeferson", kilos="60.5", precio="9800")
    comprar(client, h, dias_atras=18, productor="Marlion", kilos="40.25", precio="10250")
    # Una venta que se come el primer lote entero y parte del segundo
    vender(client, h, dias_atras=5, cliente="Tienda La 33", kilos="75.12", precio="15500")

    r = client.get(f"{API}/lotes", headers=h)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    lotes = cuerpo if isinstance(cuerpo, list) else cuerpo.get("items", cuerpo.get("lotes", []))
    res = resumen(client, h)

    print("\n===== LOS LOTES CONTRA EL RESUMEN =====")
    suma_costo = D(0)
    suma_kilos = D(0)
    for lote in lotes:
        print(f"  {({k: v for k, v in lote.items() if 'kilos' in k or 'costo' in k})}")
        suma_costo += D(lote["costo_total"])
        suma_kilos += D(lote["kilos_comprados"])
    print(f"  costo:  lotes {suma_costo}  ·  resumen {res['total_compras']}")
    print(f"  kilos:  lotes {suma_kilos}  ·  resumen {res['kilos_comprados']}")
    assert suma_costo == D(res["total_compras"])
    assert suma_kilos == D(res["kilos_comprados"])

    # Y dentro de cada lote, los kilos tienen que cerrar: lo comprado es lo
    # vendido + lo que pasó a borona + la merma + lo que sigue en bodega.
    for lote in lotes:
        cierre = (
            D(lote["kilos_vendidos"]) + D(lote["kilos_a_borona"])
            + D(lote["kilos_merma"]) + D(lote["kilos_sin_vender"])
        )
        print(f"  cierre del lote: {cierre} contra {lote['kilos_comprados']}")
        assert cierre == D(lote["kilos_comprados"]), "los kilos del lote no cierran"


# ---------------------------------------------------------------------------
# 6. La cartera: lo que se debe es lo que dicen las filas
# ---------------------------------------------------------------------------
def test_la_cartera_cuadra_con_los_abonos(client, h):
    c = comprar(client, h, dias_atras=10, productor="Yeferson", kilos="100.5", precio="10000")
    valor = D(c["valor_total"])

    # Dos abonos parciales con cifras feas. El endpoint de abonar devuelve la
    # compra entera ya recalculada (no hay GET de una compra suelta).
    fila = None
    for monto in ("333333.33", "166666.67"):
        r = client.post(
            f"{API}/compras/{c['id']}/abonos",
            json={"fecha": str(date.today()), "valor": monto},
            headers=h,
        )
        assert r.status_code in (200, 201), r.text
        fila = r.json()
    res = resumen(client, h)
    print("\n===== CARTERA =====")
    print(f"  valor {valor} - abonado {fila['abonado']} = saldo {fila['saldo']}")
    print(f"  por pagar en el resumen: {res['por_pagar_productores']}")
    assert D(fila["abonado"]) == D("500000.00")
    assert D(fila["saldo"]) == valor - D("500000.00")
    assert D(res["por_pagar_productores"]) == D(fila["saldo"])


# ---------------------------------------------------------------------------
# 7. Los kilos con TRES decimales: la base solo guarda dos
# ---------------------------------------------------------------------------
def test_los_kilos_se_redondean_a_dos_decimales_en_la_entrada(client, h):
    """Las columnas de kilos son Numeric(12,2) y ahí estaba el defecto.

    Antes: llegaban 10,005 kg, el servicio calculaba el total con el número CRUDO
    (10.005 x 1000 = $10.005) y Postgres guardaba los kilos redondeados (10,01).
    La fila quedaba diciendo "10,01 kg a $1.000 = $10.005", que no da. El dueño
    multiplica a mano y lo ve. SQLite no lo delataba porque se guarda los tres
    decimales tan tranquilo, así que la suite pasaba con el defecto puesto.

    Ahora se redondea EN LA ENTRADA: lo validado, lo guardado y lo calculado son
    el mismo número, en los dos motores.
    """
    c = comprar(client, h, dias_atras=1, productor="Yeferson", kilos="10.005", precio="1000")
    print("\n===== TRES DECIMALES =====")
    print(f"  se mandaron 10.005 kg, quedaron: {c['kilos_brutos']}")
    print(f"  valor_total: {c['valor_total']}")
    assert D(c["kilos_brutos"]) == D("10.01"), "los kilos tienen que caber en la columna"
    # Y la fila no se contradice: kilos x precio da el total que muestra
    assert D(c["valor_total"]) == (D(c["kilos_netos"]) * D("1000")).quantize(CENTAVO)


def test_la_venta_tampoco_se_contradice_con_tres_decimales(client, h):
    """Mismo defecto, mismo arreglo, en el otro lado del negocio."""
    comprar(client, h, dias_atras=5, productor="Yeferson", kilos="100", precio="9000")
    v = vender(client, h, dias_atras=1, cliente="Tienda", kilos="33.335", precio="15000")
    print("\n===== TRES DECIMALES AL VENDER =====")
    print(f"  se mandaron 33.335 kg, quedaron: {v['kilos']}  ·  total {v['valor_total']}")
    assert D(v["kilos"]) == D("33.34")
    assert D(v["valor_total"]) == (D(v["kilos"]) * D("15000")).quantize(CENTAVO)
