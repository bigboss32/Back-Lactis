"""EL MISMO PRECIO PARA VARIOS DÍAS DE LA QUINCENA, DE UNA SOLA VEZ.

Lo pidió el dueño: el precio por litro casi siempre es el mismo en toda la quincena
($2.000 en lugar de $1.900) y una quincena trae hasta 16 días; corregirlos con el lápiz,
uno por uno, son 16 veces abrir, teclear y guardar. `POST /liquidaciones/{id}/precios`
con `{"precio_litro": ..., "detalle_ids": [...] | null}` lo hace de una vez.

Lo que se fija aquí es lo que el dueño verifica con la calculadora:

  (1) los 16 días a $2.000: cada día vale litros × 2.000 y el VALOR TOTAL es la suma de
      los días, al centavo. Una sola recalculada, y el libro dice exactamente qué se
      tocó: una fila de la liquidación y una por cada recepción que cambió;
  (2) "del día 5 hacia abajo": los primeros cuatro quedan como estaban;
  (3) TODO O NADA: si un día deja el neto en rojo, el 422 lo nombra y NO se movió ningún
      otro día, ni la liquidación, ni la bitácora. Igual con un id que no es del
      comprobante (404);
  (4) volver a aplicar el mismo precio no escribe nada; si solo algunos ya estaban, solo
      cambian y se auditan los otros;
  (5) los guardias son los del lápiz de un día, con las mismas frases y los mismos
      códigos, y el permiso es el mismo ('editar');
  (6) lista vacía (422 con mensaje), repetidos (cuentan una vez) y precios inválidos;
  (7) el resultado es el mismo que el de N cambios de un día seguidos;
  (8) el guardia pregunta con el candado ya puesto.

Las cifras están calculadas a mano: 16 días de la segunda quincena de julio con
4.041,75 L en total. A $1.900 son $7.679.325; a $2.000 son $8.083.500.
"""
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import select, update

from app.modules.auditoria.models import Auditoria
from app.modules.liquidaciones import service as servicio
from app.modules.liquidaciones.models import Liquidacion
from app.modules.liquidaciones.service import LiquidacionService
from tests.conftest import auth_headers
from tests.test_liquidacion_corregir_permisos import crear_usuario_con_rol
from tests.test_liquidacion_precio_dia import _proveedor_con_recepciones

V = "/api/v1"
API = f"{V}/liquidaciones"
REC = f"{V}/recepciones"
PERIODO = ("2026-07-16", "2026-07-31")
PRIMER_DIA = date(2026, 7, 16)

# Los 16 días de la quincena, con decimales a propósito: 4.041,75 L en total.
LITROS = [
    "250", "180.5", "312.25", "275", "198.75", "260", "241.5", "289",
    "305.25", "220", "267.5", "233", "290.75", "214", "258.25", "246",
]
TOTAL_LITROS = Decimal("4041.75")
A_1900 = Decimal("7679325.00")
A_2000 = Decimal("8083500.00")


def D(v):
    return Decimal(str(v))


def _detalle(r):
    return r.json()["error"]["detail"]


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="1900"):
    r = client.post(
        f"{V}/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _dias(client, h, prov, *, descuentos=None, litros=LITROS):
    """Un día por cada elemento de `litros`, desde el 16/07; `descuentos` es
    {posición del día: valor} para los que traen un descuento."""
    for i, l in enumerate(litros):
        cuerpo = {
            "fecha": (PRIMER_DIA + timedelta(days=i)).isoformat(),
            "proveedor_id": prov["id"],
            "cantidad_litros": l,
        }
        if descuentos and i in descuentos:
            cuerpo["descuentos"] = str(descuentos[i])
        r = client.post(REC, json=cuerpo, headers=h)
        assert r.status_code == 201, r.text


def _generar(client, h, prov):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": PERIODO[0], "periodo_fin": PERIODO[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["generadas"] if x["proveedor_id"] == prov["id"])


def _quincena(client, h, nombre="Henri C", precio="1900", **kw):
    prov = _proveedor(client, h, nombre, precio)
    _dias(client, h, prov, **kw)
    return prov, _generar(client, h, prov)


def _numeros(valor):
    """Las cifras como números y no como texto. La sesión de las pruebas es una sola y la
    comparten el arnés y el servicio: según qué objetos conserve en memoria, la misma cifra
    sale como "0" (la que se asignó) o como "0.00" (la que se relee de la base), sin que
    ningún valor haya cambiado. Comparar los textos haría la prueba depender de eso."""
    if isinstance(valor, dict):
        return {k: _numeros(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_numeros(v) for v in valor]
    if isinstance(valor, str):
        try:
            return Decimal(valor)
        except ArithmeticError:
            return valor
    return valor


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return _numeros(r.json())


def _recepciones(client, h, prov):
    """Las recepciones de ese proveedor, por fecha."""
    r = client.get(REC, params={"page": 1, "page_size": 200}, headers=h)
    assert r.status_code == 200, r.text
    filas = [x for x in r.json()["items"] if x["proveedor_id"] == prov["id"]]
    return sorted((_numeros(f) for f in filas), key=lambda x: x["fecha"])


def _precios(client, h, liq_id, precio, detalle_ids="__ausente__"):
    cuerpo = {"precio_litro": str(precio)}
    if detalle_ids != "__ausente__":
        cuerpo["detalle_ids"] = detalle_ids
    return client.post(f"{API}/{liq_id}/precios", json=cuerpo, headers=h)


def _ids_de(liq, desde=0, hasta=None):
    dias = sorted(liq["detalles"], key=lambda d: d["fecha"])
    return [d["id"] for d in dias[desde:hasta]]


def _bitacora(db_session):
    return list(db_session.scalars(select(Auditoria)).all())


def _nuevas(db_session, antes):
    ya = {a.id for a in antes}
    return [a for a in _bitacora(db_session) if a.id not in ya]


def _foto(client, h, db_session, liq, prov):
    """TODO lo que un cambio de precio podría tocar, para comparar antes y después."""
    return {
        "liquidacion": _leer(client, h, liq["id"]),
        "recepciones": [
            (r["fecha"], r["precio_litro"], r["valor_bruto"], r["valor_neto"], r["updated_at"])
            for r in _recepciones(client, h, prov)
        ],
        "bitacora": len(_bitacora(db_session)),
    }


def _cuenta_recalculos(monkeypatch):
    """Cuántas veces se recalcula la liquidación desde sus recepciones."""
    llamadas = []
    original = LiquidacionService._recalcular_desde_recepciones

    def con_cuenta(self, liquidacion):
        llamadas.append(liquidacion.id)
        return original(self, liquidacion)

    monkeypatch.setattr(LiquidacionService, "_recalcular_desde_recepciones", con_cuenta)
    return llamadas


def _cuadra(liq, ajustes=None):
    """La regla de oro de la casa, sobre lo que el dueño ve en el comprobante.

    `ajustes` es {fecha: bonificación − descuento} de los días que traen alguno: el valor
    del renglón es litros × precio MÁS ese ajuste."""
    ajustes = ajustes or {}
    suma_dias = sum(D(d["valor"]) for d in liq["detalles"])
    assert suma_dias == D(liq["valor_total"]), "la columna Valor no suma el total"
    assert (
        D(liq["valor_bruto"]) + D(liq["bonificaciones"]) - D(liq["descuentos"])
        == D(liq["valor_total"])
    )
    assert D(liq["saldo"]) == D(liq["valor_total"]) - D(liq["anticipos"]) - D(
        liq["saldo_anterior"]
    ) - D(liq["pagado"])
    for d in liq["detalles"]:
        # El medio centavo sube (180,5 L × $1.999,99 = $360.998,195 → $360.998,20).
        esperado = (D(d["litros"]) * D(d["precio_litro"])).quantize(D("0.01"), ROUND_HALF_UP)
        assert D(d["valor"]) == esperado + ajustes.get(d["fecha"], D(0)), d["fecha"]


# ===========================================================================
# (1) LA QUINCENA COMPLETA, DE $1.900 A $2.000
# ===========================================================================
def test_toda_la_quincena_a_2000_cuadra_al_centavo_y_se_recalcula_una_vez(
    client, base_datos, db_session, monkeypatch
):
    """Las cifras, hechas a mano:

        16 días · 4.041,75 L
        a $1.900 el litro        $7.679.325     (como salió el comprobante)
        a $2.000 el litro        $8.083.500     (4.041,75 × 2.000)

    Cada día pasa a litros × $2.000 —el del 17/07, 180,5 L, a $361.000— y el VALOR TOTAL
    es la suma de esos 16 renglones, no otra cuenta. Se recalcula UNA sola vez y la
    bitácora trae 1 fila de la liquidación + 16 de recepciones (una por día que cambió).
    """
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    assert D(liq["total_litros"]) == TOTAL_LITROS and D(liq["valor_total"]) == A_1900
    assert len(liq["detalles"]) == 16
    bitacora = _bitacora(db_session)
    recalculos = _cuenta_recalculos(monkeypatch)

    r = _precios(client, h, liq["id"], "2000")
    assert r.status_code == 200, r.text
    nueva = r.json()
    print(f"\n  16 días a $2.000: valor total {nueva['valor_total']} · saldo {nueva['saldo']}")

    assert len(recalculos) == 1, "se recalcula una sola vez, no una por día"
    assert D(nueva["valor_total"]) == A_2000
    assert D(nueva["valor_bruto"]) == A_2000 and D(nueva["saldo"]) == A_2000
    assert D(nueva["precio_promedio"]) == D("2000")
    assert len(nueva["detalles"]) == 16
    for d in nueva["detalles"]:
        assert D(d["precio_litro"]) == D("2000")
    # El valor total es la suma de lo que dice cada renglón, calculado aparte aquí.
    assert sum((D(l) * D("2000") for l in LITROS), D(0)) == A_2000
    por_fecha = {d["fecha"]: d for d in nueva["detalles"]}
    assert D(por_fecha["2026-07-17"]["valor"]) == D("361000")
    _cuadra(nueva)
    assert _leer(client, h, liq["id"])["valor_total"] == D(nueva["valor_total"])

    # Las recepciones —que son de donde sale el precio— quedaron a $2.000, con su valor.
    recepciones = _recepciones(client, h, prov)
    assert len(recepciones) == 16
    for rec in recepciones:
        assert D(rec["precio_litro"]) == D("2000")
        assert D(rec["valor_bruto"]) == D(rec["cantidad_litros"]) * D("2000")
        assert D(rec["valor_neto"]) == D(rec["valor_bruto"])

    # La bitácora: 1 de la liquidación + 1 por cada recepción que cambió, y nada más.
    nuevas = _nuevas(db_session, bitacora)
    de_la_liq = [a for a in nuevas if a.entidad == "Liquidacion"]
    de_recepcion = [a for a in nuevas if a.entidad == "RecepcionLeche"]
    assert len(nuevas) == 1 + 16 and len(de_la_liq) == 1 and len(de_recepcion) == 16
    assert str(de_la_liq[0].entidad_id) == liq["id"]
    assert (de_la_liq[0].modulo, de_la_liq[0].accion) == ("liquidaciones", "editar")
    assert float(de_la_liq[0].antes["valor_total"]) == float(A_1900)
    assert float(de_la_liq[0].despues["valor_total"]) == float(A_2000)
    assert {a.entidad_id for a in de_recepcion} == {
        uuid.UUID(x["id"]) for x in recepciones
    }
    for a in de_recepcion:
        assert (a.modulo, a.accion) == ("recepcion", "editar")
        assert float(a.antes["precio_litro"]) == 1900 and float(a.despues["precio_litro"]) == 2000


def test_con_detalle_ids_en_null_es_lo_mismo_que_sin_mandarlo(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    r = _precios(client, h, liq["id"], "2000", None)
    assert r.status_code == 200, r.text
    assert D(r.json()["valor_total"]) == A_2000


# ===========================================================================
# (2) "DESDE EL 5 HACIA ABAJO"
# ===========================================================================
def test_solo_los_dias_elegidos_cambian_y_los_primeros_cuatro_quedan_como_estaban(
    client, base_datos, db_session
):
    """Días 5 a 16 a $2.000 y los cuatro primeros (16 a 19 de julio: 250 + 180,5 + 312,25
    + 275 = 1.017,75 L) quedan a $1.900:

        1.017,75 L × $1.900 =    $1.933.725
        3.024,00 L × $2.000 =    $6.048.000
                                 $7.981.725

    Y 12 recepciones auditadas más la de la liquidación, no 16."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    bitacora = _bitacora(db_session)
    antes = _recepciones(client, h, prov)

    r = _precios(client, h, liq["id"], "2000", _ids_de(liq, desde=4))
    assert r.status_code == 200, r.text
    nueva = r.json()
    assert D(nueva["valor_total"]) == D("7981725.00")
    _cuadra(nueva)
    dias = sorted(nueva["detalles"], key=lambda d: d["fecha"])
    for d in dias[:4]:
        assert D(d["precio_litro"]) == D("1900")
    for d in dias[4:]:
        assert D(d["precio_litro"]) == D("2000")
    assert sum((D(d["litros"]) for d in dias[:4]), D(0)) == D("1017.75")

    despues = _recepciones(client, h, prov)
    assert despues[:4] == antes[:4], "los primeros cuatro días no se tocaron"
    assert all(D(x["precio_litro"]) == D("2000") for x in despues[4:])
    nuevas = _nuevas(db_session, bitacora)
    assert len(nuevas) == 1 + 12
    assert len([a for a in nuevas if a.entidad == "RecepcionLeche"]) == 12


def test_un_dia_suelto_funciona_como_el_lapiz(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    r = _precios(client, h, liq["id"], "2000", _ids_de(liq, 0, 1))
    assert r.status_code == 200, r.text
    assert D(r.json()["valor_total"]) == A_1900 + D("250") * D("100")
    _cuadra(r.json())


# ===========================================================================
# (3) TODO O NADA
# ===========================================================================
def _con_dia_que_se_pone_en_rojo(client, h, *, posiciones=(8,)):
    """16 días a $2.000; los de `posiciones` con 100 L y $160.000 de descuento: a $2.000
    valen $200.000 − $160.000 = $40.000, pero a $1.500 son $150.000 − $160.000 =
    −$10.000. La del día 9 es la del 24/07."""
    litros = list(LITROS)
    for p in posiciones:
        litros[p] = "100"
    return _quincena(
        client, h, precio="2000", litros=litros, descuentos={p: 160000 for p in posiciones}
    )


def test_un_dia_en_rojo_rebota_nombrandolo_y_no_se_mueve_nada(
    client, base_datos, db_session, monkeypatch
):
    h = auth_headers(client, "admin.a")
    prov, liq = _con_dia_que_se_pone_en_rojo(client, h)
    antes = _foto(client, h, db_session, liq, prov)
    recalculos = _cuenta_recalculos(monkeypatch)
    # Si se escribiera algo antes de validar, esta cuenta no sería cero: la sesión del
    # arnés comparte la transacción y un rollback posterior taparía el cambio a medias.
    bajadas = []
    original = db_session.flush
    monkeypatch.setattr(db_session, "flush", lambda *a, **k: (bajadas.append(1), original(*a, **k))[1])

    r = _precios(client, h, liq["id"], "1500")
    print(f"\n  a $1.500: {r.status_code} · {_detalle(r)}")
    assert r.status_code == 422
    assert _detalle(r) == (
        "Con ese precio el valor del día 24/07/2026 queda negativo: revise los descuentos"
    )
    assert not recalculos and not bajadas, "se escribió algo antes de validar"
    monkeypatch.undo()
    assert _foto(client, h, db_session, liq, prov) == antes


def test_el_mismo_rebote_con_el_dia_en_rojo_dentro_de_la_lista(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, liq = _con_dia_que_se_pone_en_rojo(client, h)
    antes = _foto(client, h, db_session, liq, prov)

    r = _precios(client, h, liq["id"], "1500", _ids_de(liq, 5, 12))
    assert r.status_code == 422 and "24/07/2026" in _detalle(r)
    assert _foto(client, h, db_session, liq, prov) == antes

    # Dejando ese día fuera la misma petición pasa: el rebote era de ese día y de ningún otro.
    r = _precios(client, h, liq["id"], "1500", _ids_de(liq, 9))
    assert r.status_code == 200, r.text
    nueva = r.json()
    _cuadra(nueva, {"2026-07-24": D(-160000)})
    dias = sorted(nueva["detalles"], key=lambda d: d["fecha"])
    assert all(D(d["precio_litro"]) == D("2000") for d in dias[:9])
    assert all(D(d["precio_litro"]) == D("1500") for d in dias[9:])


def test_varios_dias_en_rojo_se_nombran_todos(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, liq = _con_dia_que_se_pone_en_rojo(client, h, posiciones=(8, 10))
    r = _precios(client, h, liq["id"], "1500")
    assert r.status_code == 422
    assert _detalle(r) == (
        "Con ese precio el valor de los días 24/07/2026 y 26/07/2026 queda negativo: "
        "revise los descuentos"
    )
    # Muchos: se nombran los primeros cinco y se cuenta el resto, sin una lista de 16.
    prov, liq = _con_dia_que_se_pone_en_rojo(client, h, posiciones=range(7))
    r = _precios(client, h, liq["id"], "1500")
    assert _detalle(r) == (
        "Con ese precio el valor de los días 16/07/2026, 17/07/2026, 18/07/2026, "
        "19/07/2026, 20/07/2026 y 2 más queda negativo: revise los descuentos"
    )


def test_un_id_que_no_es_de_la_liquidacion_rebota_y_no_se_mueve_nada(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    # El día de OTRA quincena (otro proveedor, con sus propios días en agosto).
    otro = _proveedor(client, h, "Otro proveedor")
    client.post(REC, json={"fecha": "2026-08-02", "proveedor_id": otro["id"],
                           "cantidad_litros": "50"}, headers=h)
    liq_otro = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-08-01", "periodo_fin": "2026-08-15", "tipo": "proveedor"},
        headers=h,
    ).json()["generadas"][0]
    antes = _foto(client, h, db_session, liq, prov)

    ajenos = [
        "00000000-0000-4000-8000-000000000001",
        liq_otro["detalles"][0]["id"],
    ]
    for malo in ajenos:
        r = _precios(client, h, liq["id"], "2000", _ids_de(liq, 0, 5) + [malo])
        print(f"\n  con el id {malo[:8]}…: {r.status_code} · {_detalle(r)}")
        assert r.status_code == 404
        assert _detalle(r) == "Ese día no pertenece a la liquidación"
        assert _foto(client, h, db_session, liq, prov) == antes


# ===========================================================================
# (4) IDEMPOTENCIA
# ===========================================================================
def test_el_mismo_precio_otra_vez_no_escribe_nada(client, base_datos, db_session, monkeypatch):
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    assert _precios(client, h, liq["id"], "2000").status_code == 200
    antes = _foto(client, h, db_session, liq, prov)
    recalculos = _cuenta_recalculos(monkeypatch)

    r = _precios(client, h, liq["id"], "2000")
    assert r.status_code == 200, r.text
    assert D(r.json()["valor_total"]) == A_2000
    assert not recalculos
    assert _foto(client, h, db_session, liq, prov) == antes, "ni una fila nueva en la bitácora"
    # Lo mismo con "2000.00" y con una lista: es el mismo precio.
    assert _precios(client, h, liq["id"], "2000.00", _ids_de(liq, 3, 9)).status_code == 200
    assert _foto(client, h, db_session, liq, prov) == antes


def test_mezclados_solo_cambian_y_se_auditan_los_que_no_estaban(
    client, base_datos, db_session, monkeypatch
):
    """Ocho días ya a $2.000 y ocho a $1.900: al pedir toda la quincena a $2.000 solo se
    tocan los ocho de $1.900 —las 8 recepciones y la liquidación en la bitácora, 9 filas—
    y queda a $8.083.500 igual que si todos hubieran cambiado."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    assert _precios(client, h, liq["id"], "2000", _ids_de(liq, 0, 8)).status_code == 200
    bitacora = _bitacora(db_session)
    antes = _recepciones(client, h, prov)
    recalculos = _cuenta_recalculos(monkeypatch)

    r = _precios(client, h, liq["id"], "2000")
    assert r.status_code == 200, r.text
    assert D(r.json()["valor_total"]) == A_2000
    _cuadra(r.json())
    assert len(recalculos) == 1
    nuevas = _nuevas(db_session, bitacora)
    assert len(nuevas) == 1 + 8
    tocadas = {a.entidad_id for a in nuevas if a.entidad == "RecepcionLeche"}
    assert {uuid.UUID(x["id"]) for x in antes[8:]} == tocadas
    despues = _recepciones(client, h, prov)
    assert despues[:8] == antes[:8], "las que ya estaban a $2.000 no se escribieron"


# ===========================================================================
# (5) LOS GUARDIAS, LOS DEL LÁPIZ DE UN DÍA
# ===========================================================================
def _lapiz(client, h, liq, precio="2000"):
    return client.put(
        f"{API}/{liq['id']}/detalles/{liq['detalles'][0]['id']}",
        json={"precio_litro": precio},
        headers=h,
    )


def test_aprobada_pagada_y_anulada_rebotan_con_la_frase_del_lapiz(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, liq = _proveedor_con_recepciones(client, h, [("2026-06-01", "100"), ("2026-06-02", "50")])
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    bitacora = _bitacora(db_session)

    r = _precios(client, h, liq["id"], "2000")
    assert r.status_code == 422
    assert _detalle(r) == _detalle(_lapiz(client, h, liq))
    assert "borrador" in _detalle(r) and "'aprobada'" in _detalle(r)
    assert D(_leer(client, h, liq["id"])["valor_total"]) == D("270000")

    assert client.post(f"{API}/{liq['id']}/pagar", headers=h).status_code == 200
    r = _precios(client, h, liq["id"], "2000")
    assert r.status_code == 422 and _detalle(r) == _detalle(_lapiz(client, h, liq))
    assert "'pagada'" in _detalle(r)
    assert not [a for a in _nuevas(db_session, bitacora) if a.entidad == "RecepcionLeche"]

    _, otra = _proveedor_con_recepciones(client, h, [("2026-06-03", "80")], nombre="Anulada")
    assert client.post(f"{API}/{otra['id']}/anular", headers=h).status_code == 200
    r = _precios(client, h, otra["id"], "2000")
    assert r.status_code == 422 and _detalle(r) == _detalle(_lapiz(client, h, otra))
    assert "'anulada'" in _detalle(r)


def test_la_de_transportador_rebota_con_la_frase_del_lapiz(client, base_datos):
    h = auth_headers(client, "admin.a")
    ruta = client.post(f"{V}/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"},
                       headers=h).json()
    transportador = client.post(
        f"{V}/transportadores",
        json={"nombre": "Stella", "valor_transporte": "100",
              "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "100"}]},
        headers=h,
    ).json()
    prov = _proveedor(client, h, "Libardo", "1800")
    client.post(REC, json={"fecha": "2026-06-01", "proveedor_id": prov["id"],
                           "transportador_id": transportador["id"],
                           "cantidad_litros": "100"}, headers=h)
    liqs = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "ambos"},
        headers=h,
    ).json()["generadas"]
    liq_t = {x["tipo"]: x for x in liqs}["transportador"]

    r = _precios(client, h, liq_t["id"], "150")
    assert r.status_code == 422
    assert _detalle(r) == _detalle(_lapiz(client, h, liq_t, "150"))
    assert "proveedor" in _detalle(r)
    assert D(_leer(client, h, liq_t["id"])["valor_total"]) == D(liq_t["valor_total"])


def test_la_deuda_ya_cobrada_en_otra_rebota_con_la_frase_del_lapiz(
    client, base_datos, db_session
):
    """Beto: 100 L × $1.800 = $180.000 contra $300.000 de adelanto, debe $120.000; la del
    16/06 se los cobra. Cambiarle el precio a la primera descuadraría las dos."""
    from tests.test_liquidacion_consejo_de_la_deuda_cobrada import _beto

    h = auth_headers(client, "admin.a")
    _, _, q1, q2 = _beto(client, h)
    q1 = _leer(client, h, q1["id"])
    assert q1["estado"] == "borrador"
    antes = _leer(client, h, q1["id"])

    r = _precios(client, h, q1["id"], "2000")
    assert r.status_code == 422
    print(f"\n  {_detalle(r)}")
    assert _detalle(r) == _detalle(_lapiz(client, h, q1))
    assert "ya se le cobró en la liquidación del 16/06/2026 al 30/06/2026" in _detalle(r)
    assert q1["avisos_deuda_cobrada"]["precio"] == _detalle(r)
    assert _leer(client, h, q1["id"]) == antes


def test_la_de_otra_empresa_es_404_y_no_se_toca(client, base_datos, db_session):
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    prov, liq = _quincena(client, h_a)
    antes = _foto(client, h_a, db_session, liq, prov)
    for ids in ("__ausente__", _ids_de(liq)):
        r = _precios(client, h_b, liq["id"], "2000", ids)
        assert r.status_code == 404, r.text
    assert _foto(client, h_a, db_session, liq, prov) == antes


def test_el_permiso_es_editar_consulta_no_puede_y_compras_si(client, base_datos, db_session):
    """El mismo permiso que el lápiz de un día: 'liquidaciones:editar'. Consulta solo mira
    (403, y nada se mueve); Compras sí lo tiene (200)."""
    empresa = base_datos["empresa_a"]
    crear_usuario_con_rol(db_session, empresa, "Consulta", "rol.consulta")
    crear_usuario_con_rol(db_session, empresa, "Compras", "rol.compras")
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    antes = _foto(client, h, db_session, liq, prov)
    bitacora = _bitacora(db_session)

    r = _precios(client, auth_headers(client, "rol.consulta"), liq["id"], "2000")
    assert r.status_code == 403, r.text
    assert _foto(client, h, db_session, liq, prov) == antes

    r = _precios(client, auth_headers(client, "rol.compras"), liq["id"], "2000")
    assert r.status_code == 200, r.text
    assert D(r.json()["valor_total"]) == A_2000
    # El libro dice quién fue: las 17 filas de este cambio son de Compras, no del admin.
    quienes = {a.usuario_id for a in _nuevas(db_session, bitacora)}
    assert len(_nuevas(db_session, bitacora)) == 1 + 16
    assert len(quienes) == 1


# ===========================================================================
# (6) LISTA VACÍA, REPETIDOS Y PRECIOS INVÁLIDOS
# ===========================================================================
def test_la_lista_vacia_rebota_con_un_mensaje_y_no_es_todos(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    antes = _foto(client, h, db_session, liq, prov)
    r = _precios(client, h, liq["id"], "2000", [])
    print(f"\n  []: {r.status_code} · {_detalle(r)}")
    assert r.status_code == 422
    assert _detalle(r).startswith("Elija al menos un día")
    assert _foto(client, h, db_session, liq, prov) == antes


def test_los_repetidos_cuentan_una_sola_vez(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    bitacora = _bitacora(db_session)
    primero = _ids_de(liq, 0, 1)[0]
    r = _precios(client, h, liq["id"], "2000", [primero, primero, primero])
    assert r.status_code == 200, r.text
    # 250 L subieron de $1.900 a $2.000: $25.000 más, una sola vez.
    assert D(r.json()["valor_total"]) == A_1900 + D("25000")
    assert len(_nuevas(db_session, bitacora)) == 1 + 1


@pytest.mark.parametrize("precio", ["0", "-500", "1800000.01"])
def test_los_precios_invalidos_rebotan_igual_que_en_el_lapiz(client, base_datos, db_session, precio):
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    antes = _foto(client, h, db_session, liq, prov)
    suelto = _lapiz(client, h, liq, precio)
    en_bloque = _precios(client, h, liq["id"], precio)
    assert suelto.status_code == en_bloque.status_code == 422
    assert suelto.json()["error"]["detail"] == en_bloque.json()["error"]["detail"]
    assert _foto(client, h, db_session, liq, prov) == antes


def test_sin_precio_o_con_un_id_que_no_es_uuid_es_422(client, base_datos):
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    assert client.post(f"{API}/{liq['id']}/precios", json={}, headers=h).status_code == 422
    assert client.post(f"{API}/{liq['id']}/precios",
                       json={"precio_litro": "2000", "detalle_ids": ["no-es-un-id"]},
                       headers=h).status_code == 422
    assert client.post(f"{API}/{liq['id']}/precios",
                       json={"precio_litro": "2000", "detalle_ids": "todos"},
                       headers=h).status_code == 422
    # El tope del precio es inclusivo: 1.000.000 pasa (y el comprobante lo dice).
    assert _precios(client, h, liq["id"], "1000000").status_code == 200


def test_un_precio_con_milesimas_se_redondea_como_en_el_lapiz(client, base_datos):
    """$1.999,995 por litro: el medio centavo sube a $2.000,00, igual que en el lápiz de un
    día, y cada día vale litros × $2.000,00."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    r = _precios(client, h, liq["id"], "1999.995")
    assert r.status_code == 200, r.text
    assert all(D(d["precio_litro"]) == D("2000.00") for d in r.json()["detalles"])
    assert D(r.json()["valor_total"]) == A_2000


# ===========================================================================
# (7) LO MISMO QUE N CAMBIOS DE UN DÍA SEGUIDOS
# ===========================================================================
def test_coincide_con_cambiar_los_dias_de_a_uno(client, base_datos, db_session):
    """Dos proveedores con la MISMA quincena (incluidos un descuento y una bonificación):
    a uno se le cambia el precio en bloque y al otro día por día con el lápiz. Las cifras
    finales —del comprobante, de cada renglón y de cada recepción— son iguales. Precio
    con centavos a propósito: $1.999,99 deja centavos en cada día."""
    h = auth_headers(client, "admin.a")
    en_bloque = _proveedor(client, h, "En bloque")
    de_a_uno = _proveedor(client, h, "De a uno")
    for prov in (en_bloque, de_a_uno):
        for i, l in enumerate(LITROS):
            cuerpo = {
                "fecha": (PRIMER_DIA + timedelta(days=i)).isoformat(),
                "proveedor_id": prov["id"],
                "cantidad_litros": l,
            }
            if i == 3:
                cuerpo["descuentos"] = "12345.67"
            if i == 7:
                cuerpo["bonificaciones"] = "8000"
            assert client.post(REC, json=cuerpo, headers=h).status_code == 201
    generadas = client.post(
        f"{API}/generar",
        json={"periodo_inicio": PERIODO[0], "periodo_fin": PERIODO[1], "tipo": "proveedor"},
        headers=h,
    ).json()["generadas"]
    liq_bloque = next(x for x in generadas if x["proveedor_id"] == en_bloque["id"])
    liq_uno = next(x for x in generadas if x["proveedor_id"] == de_a_uno["id"])
    assert D(liq_bloque["valor_total"]) == D(liq_uno["valor_total"])

    r = _precios(client, h, liq_bloque["id"], "1999.99")
    assert r.status_code == 200, r.text
    bloque = _numeros(r.json())
    for detalle_id in _ids_de(liq_uno):
        assert _lapiz_a(client, h, liq_uno, detalle_id, "1999.99").status_code == 200
    uno = _leer(client, h, liq_uno["id"])

    print(f"\n  en bloque: {bloque['valor_total']} · de a uno: {uno['valor_total']}")
    ajustes = {"2026-07-19": D("-12345.67"), "2026-07-23": D("8000")}
    _cuadra(bloque, ajustes)
    _cuadra(uno, ajustes)
    campos = (
        "total_litros", "valor_bruto", "bonificaciones", "descuentos", "valor_total",
        "precio_promedio", "anticipos", "saldo_anterior", "saldo", "pagado", "valor_transporte",
        "estado",
    )
    assert {c: bloque[c] for c in campos} == {c: uno[c] for c in campos}
    renglones = lambda liq: [  # noqa: E731
        (d["fecha"], d["litros"], d["precio_litro"], d["valor"])
        for d in sorted(liq["detalles"], key=lambda d: d["fecha"])
    ]
    assert renglones(bloque) == renglones(uno)
    campos_rec = ("fecha", "cantidad_litros", "precio_litro", "valor_bruto", "valor_neto",
                  "bonificaciones", "descuentos")
    assert [{c: x[c] for c in campos_rec} for x in _recepciones(client, h, en_bloque)] == [
        {c: x[c] for c in campos_rec} for x in _recepciones(client, h, de_a_uno)
    ]


def _lapiz_a(client, h, liq, detalle_id, precio):
    return client.put(
        f"{API}/{liq['id']}/detalles/{detalle_id}", json={"precio_litro": precio}, headers=h
    )


# ===========================================================================
# (8) EL GUARDIA PREGUNTA CON EL CANDADO PUESTO
# ===========================================================================
def test_el_candado_va_antes_que_el_guardia_y_el_recalculo_despues_de_los_dos(
    client, base_datos, monkeypatch
):
    """EVIDENCIA DEL ORDEN, porque SQLite descarta el FOR UPDATE en silencio y la suite no
    puede provocar la carrera de verdad (ver `_bloquear`): se registra el orden en que el
    servicio llama al candado, al guardia y al recálculo. Lo que protege la plata es ese
    orden —el guardia lee `estado` y la deuda cobrada DESPUÉS de esperar el turno— y en
    Postgres lo hace cumplir el FOR UPDATE; aquí se fija que el orden no se invierta."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)
    orden = []
    bloquear, guardia = servicio._bloquear, servicio._razon_para_no_cambiar_el_precio
    recalcular = LiquidacionService._recalcular_desde_recepciones

    def _bloquear(db, liquidacion):
        orden.append("candado")
        return bloquear(db, liquidacion)

    def _guardia(liquidacion, ctx=None):
        orden.append("guardia")
        return guardia(liquidacion, ctx)

    def _recalcular(self, liquidacion):
        orden.append("recalculo")
        return recalcular(self, liquidacion)

    monkeypatch.setattr(servicio, "_bloquear", _bloquear)
    monkeypatch.setattr(servicio, "_razon_para_no_cambiar_el_precio", _guardia)
    monkeypatch.setattr(LiquidacionService, "_recalcular_desde_recepciones", _recalcular)

    assert _precios(client, h, liq["id"], "2000").status_code == 200
    assert orden == ["candado", "guardia", "recalculo"]

    # Y cuando el guardia rebota, el candado ya estaba puesto y no hubo recálculo.
    orden.clear()
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    orden.clear()
    assert _precios(client, h, liq["id"], "1950").status_code == 422
    assert orden == ["candado", "guardia"]


def test_una_aprobacion_que_entra_entre_la_lectura_y_el_candado_se_ve_y_rebota(
    client, base_datos, db_session, monkeypatch
):
    """LA CARRERA, SIMULADA: otro usuario aprueba la quincena justo después de que esta
    petición la leyó y antes de que tenga el candado. Como el guardia pregunta DESPUÉS del
    candado —que relee la fila con `populate_existing`—, ve 'aprobada' y rebota; si
    preguntara con lo que leyó primero, vería 'borrador' y le reescribiría el precio a un
    comprobante que ya salió.

    La aprobación se escribe con un UPDATE directo a la tabla (sin pasar por el mapeo de
    objetos), para que la fila en memoria de esta petición quede VIEJA, igual que en la
    carrera real entre dos conexiones."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    bloquear = servicio._bloquear

    def _bloquear_con_aprobacion_en_el_medio(db, liquidacion):
        tabla = Liquidacion.__table__
        db.execute(update(tabla).where(tabla.c.id == liquidacion.id).values(estado="aprobada"))
        return bloquear(db, liquidacion)

    monkeypatch.setattr(servicio, "_bloquear", _bloquear_con_aprobacion_en_el_medio)
    r = _precios(client, h, liq["id"], "2000")
    monkeypatch.undo()
    print(f"\n  con la aprobación en el medio: {r.status_code} · {_detalle(r)}")
    assert r.status_code == 422
    assert "'aprobada'" in _detalle(r) and "borrador" in _detalle(r)
    assert all(D(x["precio_litro"]) == D("1900") for x in _recepciones(client, h, prov))
    assert D(_leer(client, h, liq["id"])["valor_total"]) == A_1900



# =================================================================== fronteras y huecos
# Salieron de la revisión adversaria: tres mutantes sobrevivían a la suite de arriba (el cero
# exacto, el corte de los cinco días y quién cambió el precio) y la atomicidad solo se probaba
# por el rollback del arnés, no por lo que el servicio deja escrito.
def test_un_dia_que_queda_en_cero_exacto_se_deja_pasar(client, base_datos, db_session):
    """100 L con $160.000 de descuento: a $1.600 el bruto es $160.000 y el neto queda en $0
    exacto. Cero no es negativo: pasa (200), igual que en el lápiz de un día."""
    h = auth_headers(client, "admin.a")
    _, liq = _con_dia_que_se_pone_en_rojo(client, h)

    r = _precios(client, h, liq["id"], "1600")

    assert r.status_code == 200, r.text
    dia = next(d for d in r.json()["detalles"] if d["fecha"] == "2026-07-24")
    assert D(dia["valor"]) == D("0")


def test_cinco_dias_en_rojo_se_nombran_sin_el_y_mas(client, base_datos, db_session):
    h = auth_headers(client, "admin.a")
    _, liq = _con_dia_que_se_pone_en_rojo(client, h, posiciones=range(5))

    r = _precios(client, h, liq["id"], "1500")

    assert r.status_code == 422
    assert _detalle(r) == (
        "Con ese precio el valor de los días 16/07/2026, 17/07/2026, 18/07/2026, "
        "19/07/2026 y 20/07/2026 queda negativo: revise los descuentos"
    )


def test_la_recepcion_queda_a_nombre_de_quien_cambio_el_precio(client, base_datos, db_session):
    from app.modules.recepcion.models import RecepcionLeche

    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    compras = crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Compras", "rol.compras")
    hc = auth_headers(client, "rol.compras")

    assert _precios(client, hc, liq["id"], "2000").status_code == 200

    db_session.expire_all()
    recepciones = db_session.scalars(
        select(RecepcionLeche).where(RecepcionLeche.proveedor_id == uuid.UUID(prov["id"]))
    ).all()
    assert len(recepciones) == 16 and all(r.updated_by == compras.id for r in recepciones)


def test_si_el_servicio_rebota_y_quien_llama_hace_commit_no_queda_nada_escrito(
    client, base_datos, db_session
):
    """Atomicidad A NIVEL DE SERVICIO, sin apoyarse en el rollback de `get_db`: si el servicio
    lanza BusinessError y quien llama traga la excepción y confirma (un lote futuro, un
    script), no puede haber quedado ni un precio, ni un renglón, ni una fila de auditoría."""
    from app.core.context import system_context
    from app.core.exceptions import BusinessError
    from app.modules.recepcion.models import RecepcionLeche

    def estado():
        db_session.expire_all()
        recepciones = db_session.execute(
            select(
                RecepcionLeche.fecha,
                RecepcionLeche.precio_litro,
                RecepcionLeche.valor_bruto,
                RecepcionLeche.valor_neto,
            ).order_by(RecepcionLeche.fecha)
        ).all()
        return (
            [(str(f), D(a), D(b), D(c)) for f, a, b, c in recepciones],
            len(_bitacora(db_session)),
        )

    h = auth_headers(client, "admin.a")
    _, liq = _con_dia_que_se_pone_en_rojo(client, h)
    antes = estado()
    servicio_ = LiquidacionService(db_session, system_context(base_datos["empresa_a"].id))

    with pytest.raises(BusinessError):
        servicio_.actualizar_precio_varios_dias(uuid.UUID(liq["id"]), Decimal("1500"), None)
    db_session.flush()
    db_session.commit()

    assert estado() == antes, "quedó algo escrito aunque el servicio rebotó"
    assert D(_leer(client, h, liq["id"])["valor_total"]) == D(liq["valor_total"])


def test_bajar_el_precio_y_quedar_debiendo_da_lo_mismo_que_cambiar_los_dias_de_a_uno(
    client, base_datos, db_session
):
    """Con un adelanto de $300.000 el precio baja y la quincena queda por debajo de cero: el
    tercero debe. Por el camino masivo y por el de un día los dos comprobantes tienen que
    terminar con las mismas cifras, y el saldo con la cuenta del dueño."""
    from tests import test_liquidacion_consejo_de_la_deuda_cobrada as base

    h = auth_headers(client, "admin.a")
    gemelos = []
    for nombre in ("Gemelo uno", "Gemelo dos"):
        prov = base._prov(client, h, nombre)
        for i, litros in enumerate(["100", "50.5", "75.25"]):
            base._dia(client, h, prov, f"2026-06-{2 + i:02d}", litros)
        base._adelanto(client, h, prov, "300000")
        gemelos.append(base._gen(client, h, base.Q1, prov))
    masivo, de_a_uno = gemelos

    r = _precios(client, h, masivo["id"], "1000")
    assert r.status_code == 200, r.text
    por_el_masivo = _numeros(r.json())
    for dia in _ids_de(de_a_uno):
        assert _lapiz_a(client, h, de_a_uno, dia, "1000").status_code == 200
    por_uno = _leer(client, h, de_a_uno["id"])

    ignorar = {"id", "created_at", "updated_at", "proveedor_id", "proveedor_nombre", "detalles", "version"}
    distintas = {
        k: (por_el_masivo[k], por_uno[k])
        for k in por_el_masivo
        if k not in ignorar and por_el_masivo[k] != por_uno.get(k)
    }
    assert not distintas, distintas
    assert D(por_el_masivo["saldo"]) == D(por_el_masivo["valor_total"]) - D(por_el_masivo["anticipos"])
    assert D(por_el_masivo["le_queda_debiendo"]) > 0


# ============================================================ un precio que redondea a cero
@pytest.mark.parametrize("precio", ["0.004", "0.001", "0.0049"])
def test_un_precio_que_redondea_a_cero_rebota_en_las_dos_puertas_y_no_mueve_nada(
    client, base_datos, db_session, precio
):
    """`gt=0` mira el número tal como llegó y 0,004 pasa, pero se guarda a centavos: $0,00 el
    litro. Por la puerta masiva eso era toda la quincena en cero con una sola llamada."""
    h = auth_headers(client, "admin.a")
    prov, liq = _quincena(client, h)
    antes = _leer(client, h, liq["id"])
    bitacora_antes = _bitacora(db_session)

    masivo = _precios(client, h, liq["id"], precio)
    uno = client.put(
        f"{API}/{liq['id']}/detalles/{liq['detalles'][0]['id']}",
        json={"precio_litro": precio},
        headers=h,
    )

    for r in (masivo, uno):
        assert r.status_code == 422, r.text
        assert "al menos un centavo" in r.text
    assert _leer(client, h, liq["id"]) == antes
    assert _nuevas(db_session, bitacora_antes) == []


def test_medio_centavo_redondea_a_un_centavo_y_pasa(client, base_datos, db_session):
    """El corte está en lo que se guarda: 0,005 queda en $0,01 (se redondea hacia arriba) y
    es un precio, absurdo pero válido; 0,004 queda en cero y no lo es."""
    h = auth_headers(client, "admin.a")
    _, liq = _quincena(client, h)

    r = _precios(client, h, liq["id"], "0.005")

    assert r.status_code == 200, r.text
    assert all(D(d["precio_litro"]) == D("0.01") for d in r.json()["detalles"])


# ============================================================ la recepción que falta
def _sin_la_recepcion_del_20(client, db_session, h):
    from app.modules.recepcion.models import RecepcionLeche

    prov, liq = _quincena(client, h)
    db_session.execute(
        update(RecepcionLeche)
        .where(
            RecepcionLeche.proveedor_id == uuid.UUID(prov["id"]),
            RecepcionLeche.fecha == date(2026, 7, 20),
        )
        .values(deleted_at=servicio.datetime.now(servicio.timezone.utc))
    )
    db_session.commit()
    return liq


def test_la_recepcion_que_falta_se_nombra_y_al_administrador_se_le_dice_que_anule(
    client, base_datos, db_session
):
    h = auth_headers(client, "admin.a")
    liq = _sin_la_recepcion_del_20(client, db_session, h)
    antes = _leer(client, h, liq["id"])

    r = _precios(client, h, liq["id"], "2000")

    assert r.status_code == 422
    assert _detalle(r) == (
        "No se encontró la recepción del día 20/07/2026; "
        "anule la liquidación y vuelva a generarla"
    )
    assert _leer(client, h, liq["id"]) == antes


def test_la_recepcion_que_falta_a_Compras_no_se_le_manda_a_anular(client, base_datos, db_session):
    """Anular pide 'administrar' y Compras solo tiene 'editar': el consejo le dice a quién
    pedírselo en vez de mandarlo a un botón que el servidor le rebota con 403."""
    h = auth_headers(client, "admin.a")
    liq = _sin_la_recepcion_del_20(client, db_session, h)
    crear_usuario_con_rol(db_session, base_datos["empresa_a"], "Compras", "rol.compras")
    hc = auth_headers(client, "rol.compras")

    r = _precios(client, hc, liq["id"], "2000")

    assert r.status_code == 422
    assert _detalle(r) == (
        "No se encontró la recepción del día 20/07/2026; "
        "pídale a un Administrador de la empresa que la anule y la vuelva a generar"
    )
