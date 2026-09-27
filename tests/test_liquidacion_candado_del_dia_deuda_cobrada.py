"""RECEPCIÓN DIARIA: el candado que pinta la pantalla es el MISMO que rebota el PUT.

La grilla decidía el candado de la celda con `pagada = liquidacion_estado in (pagada,
parcial)`, o sea con el estado GUARDADO. Pero el candado de verdad (`_traba_el_dia`)
es "ya salió papel o plata, O lo que quedó debiendo ya se cobró en otra". El caso de
Beto: 100 L a $1.800 = $180.000 contra $300.000 de adelanto, debe $120.000, aprobada;
la quincena siguiente se cobra esos $120.000. Su quincena sigue guardada 'aprobada', así
que la grilla la pintaba sin candado y con "si lo cambia, vuelve a borrador", y el PUT
daba 422.

Ahora `pagada` de la celda es `leche_pagada or flete_pagado` —que salen de
`_traba_el_dia`— y la celda trae también el `candado_aviso` que ya traía la lista.
La lista ya mandaba bien esos campos: aquí se amarra que la lista, la grilla y el PUT
digan lo mismo para cada día.
"""
from decimal import Decimal

from tests.conftest import auth_headers

V = "/api/v1"
API = f"{V}/liquidaciones"
ANT = f"{V}/anticipos"
REC = f"{V}/recepciones"
Q1 = ("2026-06-01", "2026-06-15")
Q2 = ("2026-06-16", "2026-06-30")
DIA = "2026-06-02"
# Lo que la pantalla lee de cada día para decidir el candado y explicarlo.
CANDADO = ("liquidacion_estado", "leche_pagada", "flete_pagado", "campos_bloqueados",
           "candado_aviso")


def D(v):
    return Decimal(str(v))


def _proveedor(client, h, nombre):
    r = client.post(f"{V}/proveedores", json={
        "nombre": nombre, "vereda": "El Roble", "precio_litro": "1800"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros, precio=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": litros}
    if precio:
        cuerpo["precio_litro"] = precio
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, prov, valor):
    r = client.post(ANT, json={"tipo": "proveedor", "proveedor_id": prov["id"],
                               "fecha": "2026-06-01", "valor": valor}, headers=h)
    assert r.status_code == 201, r.text


def _generar(client, h, periodo):
    r = client.post(f"{API}/generar", json={
        "periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h)
    assert r.status_code == 200, r.text
    return {x["proveedor_id"]: x for x in r.json()["generadas"]}


def _escenario(client, h):
    """Cuatro días del 02/06, uno por forma de quincena:

      B  Beto: aprobada, debe $120.000, deuda YA COBRADA en la segunda de junio.
      A  Ana: aprobada, debe $120.000, deuda SIN cobrar (sus días se corrigen).
      N  Nora: $180.000 − $50.000 de adelanto, pagada ($130.000 entregados).
      R  Rosa: $180.000 en borrador.
    """
    prov = {k: _proveedor(client, h, n) for k, n in
            (("B", "Beto Cobrada"), ("A", "Ana SinCobrar"), ("N", "Nora Pagada"),
             ("R", "Rosa Borrador"))}
    dia = {k: _recepcion(client, h, p, DIA, "100") for k, p in prov.items()}
    for k, valor in (("B", "300000"), ("A", "300000"), ("N", "50000")):
        _anticipo(client, h, prov[k], valor)
    q1 = _generar(client, h, Q1)
    liq = {k: q1[p["id"]] for k, p in prov.items()}
    for k in ("B", "A", "N"):
        assert client.post(f"{API}/{liq[k]['id']}/aprobar", headers=h).status_code == 200
    assert client.post(f"{API}/{liq['N']['id']}/pagar", headers=h).status_code == 200
    # Solo Beto entrega en la segunda de junio: esa quincena le cobra los $120.000.
    _recepcion(client, h, prov["B"], "2026-06-20", "100", precio="2500")
    q2 = _generar(client, h, Q2)[prov["B"]["id"]]
    assert D(q2["saldo_anterior"]) == D("120000")
    b = client.get(f"{API}/{liq['B']['id']}", headers=h).json()
    assert b["estado"] == "aprobada" and b["deuda_trasladada_a_id"] == q2["id"]
    return prov, dia


def _celdas(client, h, prov):
    r = client.get(f"{REC}/grilla/quincena", params={"desde": Q1[0], "hasta": Q1[1]},
                   headers=h)
    assert r.status_code == 200, r.text
    por_proveedor = {f["proveedor_id"]: f for f in r.json()["filas"]}
    return {k: por_proveedor[p["id"]]["celdas"][DIA] for k, p in prov.items()}


def _filas_de_la_lista(client, h, dia):
    """Las dos listas que usa la pantalla: la paginada y la del filtro avanzado."""
    filas = {}
    for ruta in (REC, f"{REC}/filtrar/avanzado"):
        r = client.get(ruta, params={"page": 1, "page_size": 200}, headers=h)
        assert r.status_code == 200, r.text
        por_id = {x["id"]: x for x in r.json()["items"]}
        filas[ruta] = {k: {c: por_id[d["id"]][c] for c in CANDADO} for k, d in dia.items()}
    assert filas[REC] == filas[f"{REC}/filtrar/avanzado"]
    return filas[REC]


def test_la_celda_del_dia_con_la_deuda_ya_cobrada_lleva_el_candado(client, base_datos):
    h = auth_headers(client, "admin.a")
    prov, dia = _escenario(client, h)
    celda = _celdas(client, h, prov)["B"]
    print(f"\n  celda de Beto: {celda}")
    assert celda["liquidacion_estado"] == "aprobada"
    assert celda["leche_pagada"] is True
    assert celda["pagada"] is True, "la grilla tiene que poner el candado"
    assert "ya se le cobró en la del 16/06/2026 al 30/06/2026" in celda["candado_aviso"]

    r = client.put(f"{REC}/{dia['B']['id']}", json={"cantidad_litros": "90"}, headers=h)
    assert r.status_code == 422, r.text


def test_la_lista_y_la_grilla_dicen_lo_mismo_y_el_put_les_da_la_razon(client, base_datos):
    """Para cada forma: el candado de la grilla es el de la lista, el aviso es el mismo
    texto, y el PUT de los litros rebota exactamente donde hay candado."""
    h = auth_headers(client, "admin.a")
    prov, dia = _escenario(client, h)
    celdas = _celdas(client, h, prov)
    filas = _filas_de_la_lista(client, h, dia)

    esperado = {"B": True, "A": False, "N": True, "R": False}
    for k in esperado:
        celda, fila = celdas[k], filas[k]
        print(f"\n  {k}: grilla pagada={celda['pagada']} · lista leche_pagada="
              f"{fila['leche_pagada']} bloqueados={fila['campos_bloqueados']}")
        assert celda["pagada"] is esperado[k], k
        assert celda["pagada"] == (fila["leche_pagada"] or fila["flete_pagado"]), k
        assert celda["candado_aviso"] == fila["candado_aviso"], k
        assert ("cantidad_litros" in fila["campos_bloqueados"]) is esperado[k], k
        assert celda["liquidada"] is True, k

    # Y el servidor les da la razón a las dos.
    for k in esperado:
        r = client.put(f"{REC}/{dia[k]['id']}", json={"cantidad_litros": "90"}, headers=h)
        assert (r.status_code == 422) is esperado[k], (k, r.status_code, r.text)
        if not esperado[k]:
            assert r.status_code == 200, (k, r.text)


def test_el_dia_de_la_deuda_sin_cobrar_sigue_corrigiendose(client, base_datos):
    """Lo que no cambió: Ana debe, pero nadie se lo ha cobrado. Sin candado, y al
    corregirle el día la quincena vuelve a borrador (lo que promete la pantalla)."""
    h = auth_headers(client, "admin.a")
    prov, dia = _escenario(client, h)
    celda = _celdas(client, h, prov)["A"]
    assert celda["pagada"] is False and celda["candado_aviso"] is None
    assert celda["liquidacion_estado"] == "aprobada"

    r = client.put(f"{REC}/{dia['A']['id']}", json={"cantidad_litros": "90"}, headers=h)
    assert r.status_code == 200, r.text
    lista = client.get(API, params={"page_size": 200}, headers=h).json()["items"]
    ana = next(x for x in lista if x["proveedor_id"] == prov["A"]["id"])
    assert ana["estado"] == "borrador"
    assert D(ana["valor_total"]) == D("162000")    # 90 L × $1.800
    assert D(ana["saldo"]) == D("-138000")         # 162.000 − 300.000


# ---------------------------------------------------------------------------------------
# UNA SOLA DEFINICIÓN: el candado del día ES `cifras_congeladas`, importada, no copiada.
# ---------------------------------------------------------------------------------------
def test_el_candado_del_dia_es_la_misma_funcion_que_traba_los_anticipos(monkeypatch):
    """`_traba_el_dia` era una copia de `cifras_congeladas` (las dos decían "papel o plata,
    o deuda ya cobrada"). Una copia es la que se queda atrás el día que se le sume una
    razón a la otra. Se amarra que el día pregunte EXACTAMENTE a esa función: si se le
    cambia la respuesta, el día cambia con ella."""
    import uuid as _uuid

    import app.modules.liquidaciones.service as liquidaciones
    from app.modules.liquidaciones.models import Liquidacion
    from app.modules.recepcion.service import _traba_el_dia

    formas = [
        Liquidacion(estado="aprobada", pagado=D("0"), version=1),                  # abierta
        Liquidacion(estado="aprobada", pagado=D("0"), version=1,
                    deuda_trasladada_a_id=_uuid.uuid4()),                           # cobrada
        Liquidacion(estado="parcial", pagado=D("50000"), version=1),               # abono
        Liquidacion(estado="pagada", pagado=D("0"), version=1),                    # anticipos
        Liquidacion(estado="parcial", pagado=D("0"), version=2),                   # corregida
        Liquidacion(estado="borrador", pagado=D("0"), version=1),
    ]
    for liq in formas:
        assert _traba_el_dia(liq) == liquidaciones.cifras_congeladas(liq)
    assert [_traba_el_dia(liq) for liq in formas] == [False, True, True, True, True, False]

    preguntadas = []

    def espia(liq):
        preguntadas.append(liq)
        return "la respuesta de cifras_congeladas"

    monkeypatch.setattr(liquidaciones, "cifras_congeladas", espia)
    assert _traba_el_dia(formas[0]) == "la respuesta de cifras_congeladas"
    assert preguntadas == [formas[0]]
