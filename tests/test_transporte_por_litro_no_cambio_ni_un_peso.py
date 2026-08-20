"""DIFERENCIAL POR LITRO: la misma historia corrida en los dos árboles de código.

Este archivo no exige cifras: LAS ESCRIBE. Corre una historia larga de flete POR
LITRO —dos empresas, dos rutas, tres quincenas, correcciones, días apagados y
borrados, tarifa cambiada a media quincena, anular y regenerar, aprobar y pagar— y
vuelca a un JSON TODO lo que el dueño mira:

  · las fotos del flete de cada recepción, una por una;
  · los comprobantes de proveedor y de transportador completos, renglón por renglón;
  · el TEXTO EXTRAÍDO DE LOS PDF de esos comprobantes;
  · la grilla de la quincena, el resumen del período y el tablero;
  · el libro diario, el estado de resultados y el balance.

El volcado se compara renglón por renglón contra el MISMO volcado producido por una
copia del código anterior. Cero diferencias es la única respuesta aceptable: el día
fijo no puede haberle movido un peso a lo que se cobra por litro.

No usa NADA que solo exista en el árbol nuevo (ni `modo_transporte`, ni
`tiene_dias_fijos`), porque tiene que correr igual en los dos.
"""
import io
import json
import os

from pypdf import PdfReader

from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQUIDACIONES = "/api/v1/liquidaciones"
CONTABILIDAD = "/api/v1/contabilidad"
REPORTES = "/api/v1/reportes"

SALIDA = os.environ.get("DIFERENCIAL_SALIDA", "diferencial_por_litro.json")


def _post(client, h, url, cuerpo):
    r = client.post(url, json=cuerpo, headers=h)
    assert r.status_code in (200, 201), f"{url}: {r.status_code} {r.text}"
    return r.json()


def _get(client, h, url, **params):
    r = client.get(url, headers=h, params=params or None)
    assert r.status_code == 200, f"{url}: {r.status_code} {r.text}"
    return r.json()


def _texto_pdf(contenido: bytes) -> str:
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def _limpiar(valor):
    """Saca lo que cambia entre dos corridas y no es plata: ids, sellos de tiempo."""
    if isinstance(valor, dict):
        return {
            k: _limpiar(v)
            for k, v in sorted(valor.items())
            if not (
                k.endswith("_id")
                or k in ("id", "created_at", "updated_at", "deleted_at",
                         "created_by", "updated_by", "consecutivo")
            )
        }
    if isinstance(valor, list):
        return [_limpiar(v) for v in valor]
    return valor


def _historia(client, h, sufijo):
    """La historia POR LITRO de una quesera. Devuelve el volcado de esa empresa."""
    napoles = _post(client, h, RUTAS, {"nombre": f"Napoles{sufijo}", "municipio": "Granada"})
    fabrica = _post(client, h, RUTAS, {"nombre": f"Fabrica{sufijo}", "municipio": "Granada"})
    alex = _post(client, h, TRANSPORTADORES, {
        "nombre": f"Alex{sufijo}",
        "valor_transporte": "200",
        "rutas": [
            {"ruta_id": napoles["id"], "valor_transporte": "242.76"},
            {"ruta_id": fabrica["id"], "valor_transporte": "185.40"},
        ],
    })
    beto = _post(client, h, TRANSPORTADORES, {
        "nombre": f"Beto{sufijo}", "valor_transporte": "310.15",
    })

    provs = {}
    for nombre, ruta in (
        ("Aurelio", napoles), ("Marleny", napoles), ("Gilberto", fabrica),
        ("Ramiro", fabrica), ("Rosa", napoles), ("Henri", fabrica),
    ):
        provs[nombre] = _post(client, h, PROVEEDORES, {
            "nombre": f"{nombre}{sufijo}", "vereda": "La Vega",
            "precio_litro": "1800", "ruta_id": ruta["id"]})

    def recibir(fecha, quien, litros, transportador=alex, **extra):
        cuerpo = {
            "fecha": fecha, "proveedor_id": provs[quien]["id"],
            "transportador_id": transportador["id"], "cantidad_litros": str(litros),
        }
        cuerpo.update(extra)
        return _post(client, h, RECEPCIONES, cuerpo)

    # ---- QUINCENA 1: 16-31 de julio ----------------------------------------
    dias = {}
    litros_por_dia = {
        "2026-07-16": [("Aurelio", "44.23"), ("Marleny", "82.48"), ("Gilberto", "227.55")],
        "2026-07-17": [("Aurelio", "51.10"), ("Rosa", "124.20"), ("Ramiro", "60.00")],
        "2026-07-18": [("Marleny", "137.45"), ("Henri", "219.45"), ("Gilberto", "96.30")],
        "2026-07-20": [("Aurelio", "38.75"), ("Rosa", "111.11"), ("Ramiro", "77.05")],
    }
    for fecha, filas in litros_por_dia.items():
        for quien, litros in filas:
            dias[(fecha, quien)] = recibir(fecha, quien, litros)
    # Un día con OTRO transportador, con su tarifa general.
    dias[("2026-07-21", "Henri")] = recibir("2026-07-21", "Henri", "63.40", beto)

    # Correcciones antes de liquidar.
    client.put(f"{RECEPCIONES}/{dias[('2026-07-16', 'Aurelio')]['id']}",
               json={"cantidad_litros": "47.90"}, headers=h)
    client.put(f"{RECEPCIONES}/{dias[('2026-07-17', 'Ramiro')]['id']}",
               json={"estado": "inactivo"}, headers=h)
    client.delete(f"{RECEPCIONES}/{dias[('2026-07-20', 'Ramiro')]['id']}", headers=h)
    # Un día que se cambia de ruta: la tarifa que le aplica cambia de $242,76 a $185,40.
    client.put(f"{RECEPCIONES}/{dias[('2026-07-20', 'Rosa')]['id']}",
               json={"ruta_id": fabrica["id"]}, headers=h)

    generadas_1 = _post(client, h, f"{LIQUIDACIONES}/generar", {
        "periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31", "tipo": "ambos"})

    # Correcciones DESPUÉS de liquidar (recuadre en cascada).
    client.put(f"{RECEPCIONES}/{dias[('2026-07-18', 'Henri')]['id']}",
               json={"cantidad_litros": "225.00"}, headers=h)
    client.put(f"{RECEPCIONES}/{dias[('2026-07-17', 'Rosa')]['id']}",
               json={"estado": "inactivo"}, headers=h)
    client.put(f"{RECEPCIONES}/{dias[('2026-07-17', 'Rosa')]['id']}",
               json={"estado": "activo"}, headers=h)

    # LA TARIFA CAMBIA A MEDIA QUINCENA: el comprobante viejo no se re-precifica
    # solo; solo lo hace RECALCULAR.
    client.put(f"{TRANSPORTADORES}/{alex['id']}", json={
        "valor_transporte": "200",
        "rutas": [
            {"ruta_id": napoles["id"], "valor_transporte": "255.00"},
            {"ruta_id": fabrica["id"], "valor_transporte": "185.40"},
        ]}, headers=h)
    for liq in generadas_1["generadas"]:
        client.post(f"{LIQUIDACIONES}/{liq['id']}/recalcular", headers=h)

    # Se aprueba y se paga el comprobante del flete de Alex; los demás se dejan
    # en borrador, y uno de proveedor se aprueba nada más.
    for liq in generadas_1["generadas"]:
        if liq["tipo"] == "transportador":
            client.post(f"{LIQUIDACIONES}/{liq['id']}/aprobar", headers=h)
            client.post(f"{LIQUIDACIONES}/{liq['id']}/pagar", headers=h)
    # SE APRUEBA POR NOMBRE Y NO "la primera": el orden en que salen las generadas
    # es un detalle interno, y colgar de él haría que el diferencial acusara una
    # diferencia donde no se movió ningún peso.
    for liq in generadas_1["generadas"]:
        if liq["tipo"] == "proveedor" and liq.get("tercero_nombre") == f"Aurelio{sufijo}":
            client.post(f"{LIQUIDACIONES}/{liq['id']}/aprobar", headers=h)

    # ---- QUINCENA 2: 1-15 de agosto, con anular y regenerar ----------------
    for fecha, filas in {
        "2026-08-03": [("Aurelio", "55.05"), ("Gilberto", "180.20")],
        "2026-08-05": [("Marleny", "91.30"), ("Rosa", "142.90"), ("Henri", "70.15")],
        "2026-08-09": [("Ramiro", "88.75"), ("Aurelio", "39.60")],
    }.items():
        for quien, litros in filas:
            dias[(fecha, quien)] = recibir(fecha, quien, litros)

    generadas_2 = _post(client, h, f"{LIQUIDACIONES}/generar", {
        "periodo_inicio": "2026-08-01", "periodo_fin": "2026-08-15", "tipo": "ambos"})
    for liq in generadas_2["generadas"]:
        if liq["tipo"] == "transportador":
            client.post(f"{LIQUIDACIONES}/{liq['id']}/anular", headers=h)
    # Leche anotada tarde de un día ya liquidado (por litro SÍ suma flete nuevo).
    dias[("2026-08-05", "Gilberto")] = recibir("2026-08-05", "Gilberto", "64.80")
    generadas_2b = _post(client, h, f"{LIQUIDACIONES}/generar", {
        "periodo_inicio": "2026-08-01", "periodo_fin": "2026-08-15", "tipo": "transportador"})

    # ---- EL VOLCADO --------------------------------------------------------
    volcado = {}
    recepciones = _get(client, h, f"{RECEPCIONES}/filtrar/avanzado", size=200)
    volcado["recepciones"] = sorted(
        (
            {
                "fecha": r["fecha"], "proveedor": r.get("proveedor_nombre"),
                "litros": r["cantidad_litros"], "bruto": r["valor_bruto"],
                "neto": r["valor_neto"], "flete": r["valor_transporte"],
                "estado": r["estado"],
            }
            for r in recepciones["items"]
        ),
        key=lambda x: (x["fecha"], str(x["proveedor"]), x["litros"]),
    )

    campos_liq = (
        "tipo", "periodo_inicio", "periodo_fin", "total_litros", "precio_promedio",
        "valor_bruto", "bonificaciones", "descuentos", "valor_transporte",
        "anticipos", "valor_total", "neto_a_pagar", "pagado", "saldo", "estado",
        "le_queda_debiendo", "saldo_anterior",
    )
    campos_det = ("fecha", "litros", "precio_litro", "valor", "ruta_nombre")
    todas = _get(client, h, LIQUIDACIONES, size=200)
    liquidaciones = []
    for cabecera in todas["items"]:
        liq = _get(client, h, f"{LIQUIDACIONES}/{cabecera['id']}")
        fila = {c: liq.get(c) for c in campos_liq}
        fila["tercero"] = liq.get("tercero_nombre")
        fila["detalles"] = sorted(
            ({c: d.get(c) for c in campos_det} for d in liq["detalles"]),
            key=lambda x: (x["fecha"], str(x["ruta_nombre"]), str(x["valor"])),
        )
        fila["pagos"] = sorted(
            ({"fecha": p["fecha"], "valor": p["valor"]} for p in liq.get("pagos", [])),
            key=lambda x: (x["fecha"], x["valor"]),
        )
        pdf = client.get(f"{LIQUIDACIONES}/{cabecera['id']}/pdf", headers=h)
        fila["pdf"] = _texto_pdf(pdf.content) if pdf.status_code == 200 else pdf.status_code
        liquidaciones.append(fila)
    volcado["liquidaciones"] = sorted(
        liquidaciones,
        key=lambda x: (x["tipo"], str(x["tercero"]), x["periodo_inicio"], str(x["valor_total"])),
    )

    volcado["grilla_julio"] = _get(client, h, f"{RECEPCIONES}/grilla/quincena",
                                   desde="2026-07-16", hasta="2026-07-31")
    volcado["grilla_agosto"] = _get(client, h, f"{RECEPCIONES}/grilla/quincena",
                                    desde="2026-08-01", hasta="2026-08-15")
    volcado["resumen"] = _get(client, h, f"{RECEPCIONES}/resumen/periodo",
                              desde="2026-07-01", hasta="2026-08-31")
    volcado["tablero"] = _get(client, h, f"{REPORTES}/dashboard")
    volcado["libro_diario"] = _get(client, h, f"{CONTABILIDAD}/libro-diario",
                                   desde="2026-07-01", hasta="2026-08-31")
    volcado["estado_resultados"] = _get(client, h, f"{CONTABILIDAD}/estado-resultados",
                                        desde="2026-07-01", hasta="2026-08-31")
    volcado["balance"] = _get(client, h, f"{CONTABILIDAD}/balance")
    # EL ORDEN EN QUE SALEN LAS GENERADAS, apuntado aparte: es lo único que puede
    # diferir sin que se mueva plata, y conviene verlo en vez de que contamine el resto.
    volcado["orden_generadas"] = [
        [(g["tipo"], g.get("tercero_nombre")) for g in bloque["generadas"]]
        for bloque in (generadas_1, generadas_2, generadas_2b)
    ]
    volcado["omitidas"] = [
        sorted((o.get("motivo_codigo"), o.get("cuenta")) for o in bloque["omitidas"])
        for bloque in (generadas_1, generadas_2, generadas_2b)
    ]
    return _limpiar(volcado)


def test_volcado_por_litro(client, base_datos):
    """Corre la historia en LAS DOS queseras y deja el volcado en un JSON."""
    salida = {}
    for usuario, sufijo in (("admin.a", " A"), ("admin.b", " B")):
        h = auth_headers(client, usuario)
        salida[usuario] = _historia(client, h, sufijo)
    with io.open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(salida, f, indent=1, ensure_ascii=False, sort_keys=True, default=str)
    print(f"\nvolcado escrito en {os.path.abspath(SALIDA)}")
    # Una comprobación mínima para que el volcado no sea vacío por accidente.
    assert salida["admin.a"]["liquidaciones"], "no se genero ninguna liquidacion"
    assert salida["admin.a"]["recepciones"], "no se registro ninguna recepcion"
