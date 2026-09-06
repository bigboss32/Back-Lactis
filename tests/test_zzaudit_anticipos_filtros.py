"""AUDITORÍA — ANTICIPOS: filtros por fecha, búsqueda por beneficiario y la
SUMA FILTRADA contra lo que la pantalla lista. Cifras feas a propósito."""
import pytest

from decimal import Decimal

from tests.conftest import auth_headers

ANT = "/api/v1/anticipos"


def proveedor(client, h, nombre, precio="1833.33"):
    r = client.post("/api/v1/proveedores",
                    json={"nombre": nombre, "vereda": "Porvenir", "precio_litro": precio},
                    headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def transportador(client, h, nombre):
    r = client.post("/api/v1/transportadores", json={"nombre": nombre}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def empleado(client, h, nombre, apellido):
    r = client.post("/api/v1/empleados",
                    json={"nombre": nombre, "apellido": apellido, "valor_dia": "41833.33"},
                    headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def anticipo(client, h, **campos):
    r = client.post(ANT, json=campos, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def listar_todo(client, h, query=""):
    """Recorre TODAS las páginas del listado y devuelve los valores."""
    valores, page = [], 1
    while True:
        sep = "&" if query else "?"
        r = client.get(f"{ANT}{'?' + query if query else ''}{sep if query else '?'}page={page}&page_size=5",
                       headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        valores += [Decimal(i["valor"]) for i in data["items"]]
        if len(valores) >= data["total"] or not data["items"]:
            return valores, data["total"]
        page += 1


def suma(client, h, query=""):
    r = client.get(f"{ANT}/totales/suma{'?' + query if query else ''}", headers=h)
    assert r.status_code == 200, r.text
    return Decimal(str(r.json()))


def _sembrar(client, h):
    p1 = proveedor(client, h, "Henri C")
    p2 = proveedor(client, h, "Yubigildo")
    t1 = transportador(client, h, "Stella")
    e1 = empleado(client, h, "Aurelio", "Ricaute")
    datos = [
        dict(tipo="proveedor", proveedor_id=p1["id"], fecha="2026-06-30", valor="242.76"),
        dict(tipo="proveedor", proveedor_id=p1["id"], fecha="2026-07-01", valor="1833.33"),
        dict(tipo="proveedor", proveedor_id=p2["id"], fecha="2026-07-08", valor="137450.45"),
        dict(tipo="transportador", transportador_id=t1["id"], fecha="2026-07-15", valor="94030.07"),
        dict(tipo="empleado", empleado_id=e1["id"], fecha="2026-07-16", valor="242760.99"),
        dict(tipo="proveedor", proveedor_id=p1["id"], fecha="2026-07-31", valor="0.01",
             observaciones="Sobrante de Stella"),
    ]
    for d in datos:
        anticipo(client, h, **d)
    return p1, p2, t1, e1


COMBOS = [
    "",
    "desde=2026-07-01",
    "hasta=2026-07-15",
    "desde=2026-07-01&hasta=2026-07-15",
    "desde=2026-07-31&hasta=2026-07-31",
    "desde=2026-08-01",
    "search=Henri",
    "search=henri",
    "search=Stella",
    "search=Ricaute",
    "search=Aurelio",
    "search=Yubigildo",
    "search=Stella&desde=2026-07-01&hasta=2026-07-20",
    "estado=activo",
    "estado=activo&desde=2026-07-01",
]


def test_la_suma_filtrada_cuadra_con_lo_que_lista_la_pantalla(client, base_datos):
    h = auth_headers(client, "admin.a")
    _sembrar(client, h)
    descuadres = []
    for q in COMBOS:
        valores, total = listar_todo(client, h, q)
        s = suma(client, h, q)
        if sum(valores) != s or len(valores) != total:
            descuadres.append((q, str(sum(valores)), str(s), len(valores), total))
    assert not descuadres, "SUMA FILTRADA != RENGLONES LISTADOS:\n" + "\n".join(map(str, descuadres))


def test_la_busqueda_por_beneficiario_encuentra_a_los_tres_tipos(client, base_datos):
    h = auth_headers(client, "admin.a")
    _sembrar(client, h)
    esperado = {
        "Henri": Decimal("242.76") + Decimal("1833.33") + Decimal("0.01"),
        "Yubigildo": Decimal("137450.45"),
        "Ricaute": Decimal("242760.99"),   # apellido del empleado
        "Aurelio": Decimal("242760.99"),   # nombre del empleado
    }
    malos = []
    for texto, valor in esperado.items():
        s = suma(client, h, f"search={texto}")
        if s != valor:
            malos.append((texto, str(s), str(valor)))
    assert not malos, malos


@pytest.mark.xfail(strict=True, reason="DEUDA CONOCIDA de la auditoria: este defecto sigue ABIERTO. Cuando se cierre, esta prueba falla por pasar "
                   "(XPASS) y hay que quitarle el marcador y dejarla exigiendo lo correcto.")
def test_la_busqueda_por_beneficiario_no_arrastra_las_observaciones(client, base_datos):
    """Buscar 'Stella' debe traer el flete de Stella. Trae ADEMÁS el anticipo de
    Henri C cuya observación dice 'Sobrante de Stella': la búsqueda dice ser por
    beneficiario y también mira observaciones."""
    h = auth_headers(client, "admin.a")
    _sembrar(client, h)
    s = suma(client, h, "search=Stella")
    assert s == Decimal("94030.07"), (
        f"la búsqueda por beneficiario 'Stella' suma ${s} en vez de $94.030,07"
    )


def test_los_anticipos_no_cruzan_de_empresa(client, base_datos):
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    _sembrar(client, ha)
    _, total_b = listar_todo(client, hb)
    assert total_b == 0
    assert suma(client, hb) == Decimal("0")
    assert suma(client, hb, "search=Henri") == Decimal("0")


def test_el_borrado_saca_el_anticipo_de_la_suma(client, base_datos):
    h = auth_headers(client, "admin.a")
    p1, *_ = _sembrar(client, h)
    antes = suma(client, h)
    uno = client.get(f"{ANT}?search=Yubigildo", headers=h).json()["items"][0]
    assert client.delete(f"{ANT}/{uno['id']}", headers=h).status_code == 204
    despues = suma(client, h)
    assert antes - despues == Decimal("137450.45"), (str(antes), str(despues))
    valores, total = listar_todo(client, h)
    assert sum(valores) == despues and total == 5
