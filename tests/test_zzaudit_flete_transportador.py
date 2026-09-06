"""AUDITORÍA DEL FLETE DEL TRANSPORTADOR — sondas medidas contra la API.

No arregla nada: mide. Cifras feas a propósito para que ningún cuadre salga por
casualidad de un número redondo:

    ruta "A fabrica"  →  DÍA FIJO  $183.333,33
    ruta "Napoles"    →  POR LITRO $242,76
    tarifa general    →  POR LITRO $1.833,33   (no debe aparecer en ningún renglón)

    Aurelio   44,23 L
    Marleny  137,45 L
    Gilberto  82,48 L
    Ramiro    96,31 L
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQ = "/api/v1/liquidaciones"

FIJO = Decimal("183333.33")
NAPOLES = Decimal("242.76")
GENERAL = Decimal("1833.33")

DIA_1 = "2026-07-16"
DIA_2 = "2026-07-17"
DIA_3 = "2026-07-18"
INICIO, FIN = "2026-07-16", "2026-07-31"

PROVS = (
    ("Aurelio", Decimal("44.23")),
    ("Marleny", Decimal("137.45")),
    ("Gilberto", Decimal("82.48")),
    ("Ramiro", Decimal("96.31")),
    ("Henri", Decimal("61.07")),
)


def D(v):
    return Decimal(str(v))


def cent(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _post(client, h, url, payload, ok=(200, 201)):
    r = client.post(url, json=payload, headers=h)
    assert r.status_code in ok, r.text
    return r.json()


def escenario(client, h, *, modo_fabrica="dia_fijo", modo_napoles="litro"):
    fabrica = _post(client, h, RUTAS, {"nombre": "A fabrica", "municipio": "Granada"})
    napoles = _post(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = _post(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo",
        "valor_transporte": str(GENERAL),
        "modo_transporte": "litro",
        "rutas": [
            {"ruta_id": fabrica["id"], "valor_transporte": str(FIJO),
             "modo_transporte": modo_fabrica},
            {"ruta_id": napoles["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": modo_napoles},
        ],
    })
    beto = _post(client, h, TRANSPORTADORES, {
        "nombre": "Beto Ruiz",
        "valor_transporte": "310.15",
        "modo_transporte": "litro",
        "rutas": [],
    })
    provs = {}
    for nombre, _ in PROVS:
        provs[nombre] = _post(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "La Vega", "precio_litro": "1800",
            "ruta_id": fabrica["id"]})
    return {"fabrica": fabrica, "napoles": napoles, "alex": alex, "beto": beto,
            "provs": provs}


def recibir(client, h, esc, fecha, nombre, litros, **extra):
    cuerpo = {"fecha": fecha,
              "proveedor_id": esc["provs"][nombre]["id"],
              "transportador_id": esc["alex"]["id"],
              "cantidad_litros": str(litros)}
    cuerpo.update(extra)
    return _post(client, h, RECEPCIONES, cuerpo)


def put_recepcion(client, h, rid, body, ok=(200,)):
    r = client.put(f"{RECEPCIONES}/{rid}", json=body, headers=h)
    assert r.status_code in ok, f"esperaba {ok}, dio {r.status_code}: {r.text}"
    return r


def generar(client, h, inicio=INICIO, fin=FIN):
    return _post(client, h, f"{LIQ}/generar",
                 {"periodo_inicio": inicio, "periodo_fin": fin,
                  "tipo": "transportador"})


def leer(client, h, liq_id):
    r = client.get(f"{LIQ}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def renglones(liq):
    return sorted(liq["detalles"],
                  key=lambda d: (d["fecha"], d["ruta_nombre"] or "",
                                 str(d["precio_litro"]), str(d["valor"])))


def fotos_del_comprobante(db, liq_id):
    db.expire_all()
    filas = db.scalars(select(RecepcionLeche).where(
        RecepcionLeche.liquidacion_transporte_id == uuid.UUID(liq_id),
        RecepcionLeche.deleted_at.is_(None),
        RecepcionLeche.estado == "activo",
    )).all()
    return filas, sum((D(f.valor_transporte) for f in filas), D(0))


def foto(db, rid):
    db.expire_all()
    fila = db.get(RecepcionLeche, uuid.UUID(rid))
    return D(fila.valor_transporte)


def pinta(liq, titulo=""):
    print(f"\n  --- {titulo} [{liq['estado']}] ---")
    for r in renglones(liq):
        modo = r["modo_transporte"]
        como = "Dia completo" if modo == "dia_fijo" else f"x ${D(r['precio_litro'])}"
        marca = " (YA COBRADO)" if r.get("dia_fijo_ya_cobrado") else ""
        print(f"    {r['fecha']}  {(r['ruta_nombre'] or '-'):<11}"
              f"{D(r['litros']):>9} L  {como:<14}${D(r['valor'])}{marca}")
    print(f"    TOTAL flete ${D(liq['valor_transporte'])}  "
          f"total ${D(liq['valor_total'])}  saldo ${D(liq['saldo'])}")


def cuadre(liq, fotos=None, ctx=""):
    """LA REGLA DE ORO: cada renglón se verifica, y los renglones suman el total."""
    suma = D(0)
    for r in renglones(liq):
        val = D(r["valor"])
        if r["modo_transporte"] != "dia_fijo":
            assert cent(D(r["litros"]) * D(r["precio_litro"])) == val, (
                f"{ctx}: renglón por litro no cuadra: {r['litros']} x "
                f"{r['precio_litro']} = {cent(D(r['litros']) * D(r['precio_litro']))} "
                f"pero dice {val}")
        else:
            assert D(r["precio_litro"]) == 0, (
                f"{ctx}: un renglón de día fijo no puede traer tarifa por litro")
        suma += val
    assert suma == D(liq["valor_transporte"]), (
        f"{ctx}: los renglones suman ${suma} y el comprobante dice "
        f"${D(liq['valor_transporte'])}")
    if fotos is not None:
        assert suma == fotos, (
            f"{ctx}: los renglones suman ${suma} y las fotos de las recepciones "
            f"suman ${fotos}")
    return suma


def cuadre_por_renglon(db, liq, ctx=""):
    """LA REGLA FINA: las fotos de CADA (día, ruta) suman EXACTO su renglón.

    El total puede cuadrar y el desglose por día estar torcido; el costeo del queso
    y la grilla de la quincena leen la foto de cada recepción, no el total.
    """
    filas, _ = fotos_del_comprobante(db, liq["id"])
    por_grupo = {}
    for f in filas:
        clave = (f.fecha.isoformat(), str(f.ruta_id) if f.ruta_id else None)
        por_grupo[clave] = por_grupo.get(clave, D(0)) + D(f.valor_transporte)
    de_renglones = {}
    for r in liq["detalles"]:
        clave = (r["fecha"], r["ruta_id"])
        de_renglones[clave] = de_renglones.get(clave, D(0)) + D(r["valor"])
    print(f"    cuadre por (dia, ruta) [{ctx}]:")
    for clave in sorted(set(por_grupo) | set(de_renglones)):
        print(f"      {clave[0]}  fotos ${por_grupo.get(clave, D(0))}  "
              f"renglon ${de_renglones.get(clave, D(0))}")
    assert por_grupo == de_renglones, (
        f"{ctx}: el desglose por (dia, ruta) no cuadra: fotos {por_grupo} vs "
        f"renglones {de_renglones}")
