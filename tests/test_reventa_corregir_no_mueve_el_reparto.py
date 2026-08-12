"""¿CORREGIR UNA FACTURA LE CAMBIA EL PUESTO EN EL REPARTO FIFO?

La llave de orden del reparto es (fecha, hora de registro, renglón, ...). La hora de
registro ahora la escribe la aplicación con microsegundos, así que es de verdad "cuál
se registró primero" — y eso arregló el temblor del UUID.

Pero hay una puerta que REHACE los renglones en vez de actualizarlos: mandar
`renglones` en el PUT de una factura ("Mandar `renglones` significa REHACERLOS", lo
dice el propio endpoint). Los renglones nuevos nacen con una hora de registro NUEVA,
o sea la de hoy, así que la factura se va al FINAL del orden de su día.

Y en el reparto FIFO el orden del día decide A QUÉ PRODUCTOR se le consumen los
kilos de una venta, con su costo y su ganancia. La pregunta que estas pruebas
contestan con cifras es: corregirle un dato a la factura de la mañana, ¿le mueve la
ganancia de un productor a otro?
"""
from decimal import Decimal

import pytest

from tests.conftest import auth_headers

API = "/api/v1/reventa"
CERO = Decimal("0")


def D(v):
    return Decimal(str(v))


@pytest.fixture()
def h(client, base_datos):
    return auth_headers(client, "admin.a")


def factura_compra(client, h, fecha, tercero, renglones):
    r = client.post(
        f"{API}/documentos",
        json={"tipo": "compra", "fecha": fecha, "tercero": tercero,
              "renglones": renglones},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def ganancias_por_productor(client, h):
    """Lo que le quedó a cada productor según el panel de lotes."""
    r = client.get(f"{API}/lotes", headers=h)
    assert r.status_code == 200, r.text
    salida = {}
    for lote in r.json()["lotes"]:
        for c in lote["detalle_compras"]:
            clave = f"{lote['fecha']} {c['productor']}"
            salida[clave] = {
                "kilos_vendidos": D(c["kilos_vendidos"]),
                "kilos_sin_vender": D(c["kilos_sin_vender"]),
                "costo_realizado": D(c["costo_realizado"]),
                "ingresos": D(c["ingresos"]),
                "ganancia": D(c["ganancia"]),
            }
    return salida


def pintar(titulo, cifras):
    print(f"\n===== {titulo} =====")
    for clave, v in sorted(cifras.items()):
        print(f"   {clave:26} vendidos={v['kilos_vendidos']:>8} "
              f"sin_vender={v['kilos_sin_vender']:>8} "
              f"costo={v['costo_realizado']:>12} ingresos={v['ingresos']:>12} "
              f"ganancia={v['ganancia']:>12}")


def montar(client, h):
    """Dos compras del MISMO DÍA de dos productores a precios muy distintos, y una
    venta que se lleva más de lo que trajo la primera: así el orden del día decide
    de quién sale el resto."""
    factura = factura_compra(client, h, "2026-03-01", "Patricia",
                             [{"kilos_brutos": 100, "precio_kilo": 1000}])
    factura_compra(client, h, "2026-03-01", "Sebastian",
                   [{"kilos_brutos": 100, "precio_kilo": 5000}])
    r = client.post(
        f"{API}/ventas",
        json={"fecha": "2026-03-05", "cliente": "Don Jose", "tipo": "queso",
              "kilos": 150, "precio_kilo": 8000, "gasto_por_kilo": 100},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return factura


def test_corregir_solo_la_nota_no_mueve_el_reparto(client, h):
    """Corregir la NOTA (sin mandar renglones) actualiza la fila y le deja su hora
    de registro. Nada se puede mover, y esta prueba lo confirma."""
    factura = montar(client, h)
    antes = ganancias_por_productor(client, h)
    pintar("antes de corregir la nota", antes)

    r = client.put(f"{API}/documentos/{factura['id']}",
                   json={"tipo": "compra",
                         "observaciones": "Le faltaba el número de la remisión"},
                   headers=h)
    assert r.status_code == 200, r.text
    despues = ganancias_por_productor(client, h)
    pintar("después de corregir la nota", despues)
    assert antes == despues, (
        "corregir la nota de la factura movió el reparto entre productores"
    )


def test_rehacer_los_renglones_con_los_mismos_datos_no_mueve_la_ganancia(client, h):
    """CORREGIRLE UN DATO A LA FACTURA NO LE CAMBIA EL DUEÑO A LA PLATA.

    Se manda el PUT con los renglones EXACTAMENTE IGUALES (mismos kilos, mismo
    precio). Rehacerlos BORRA las filas y las CREA de nuevo, así que antes nacían con
    la hora de hoy y la factura se iba al final del orden de su día: la venta de 150 kg
    dejaba de tomar los 100 kg baratos de Patricia primero y empezaba por los 100 kg
    caros de Sebastián. La ganancia cambiaba de productor, y los 50 kg que quedan en
    bodega pasaban de ser de uno a ser del otro —o sea que el costo del inventario y
    los dos estados de cuenta cambiaban también— sin que ninguna cifra del negocio se
    hubiera movido.

    Lo que lo cierra: el puesto en el reparto es DE LA FACTURA, así que los renglones
    rehechos heredan su hora de registro (ver `_hora_de_la_factura`). En Postgres esto
    era seguro que pasara, porque `now()` es la hora de la transacción y la fila
    rehecha quedaba de última siempre.
    """
    factura = montar(client, h)
    antes = ganancias_por_productor(client, h)
    pintar("antes de rehacer los renglones", antes)

    r = client.put(
        f"{API}/documentos/{factura['id']}",
        json={"tipo": "compra",
              "renglones": [{"kilos_brutos": 100, "precio_kilo": 1000}]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    despues = ganancias_por_productor(client, h)
    pintar("después de rehacer los renglones CON LOS MISMOS DATOS", despues)

    movidos = {k: (antes[k], despues[k]) for k in antes if antes.get(k) != despues.get(k)}
    for clave, (a, d) in movidos.items():
        print(f"\n   {clave} se movió:")
        for campo in a:
            if a[campo] != d[campo]:
                print(f"      {campo:18} {a[campo]} -> {d[campo]}")
    assert not movidos, (
        "rehacer los renglones de una factura con LOS MISMOS DATOS movió el reparto: "
        + "; ".join(sorted(movidos))
    )
