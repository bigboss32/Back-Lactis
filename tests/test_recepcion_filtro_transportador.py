"""Filtro por TRANSPORTADOR en la grilla de quincena de Recepción diaria.

El dueño quiere saber qué recogió cada transportador. El transportador se
guarda en CADA recepción (columna transportador_id de recepciones_leche), no se
hereda de la ruta: por eso el mismo proveedor puede tener el lunes a un
transportador y el martes a otro, y el filtro tiene que trabajar A NIVEL DE
CELDA (día), no de fila (proveedor).

Lo que se prueba aquí:
  1. filtrar por un transportador devuelve solo SUS recepciones, y los totales
     de la pantalla cuadran exacto con las celdas que quedaron;
  2. el filtro se combina con el de ruta y con la búsqueda por proveedor, sin
     pisarse;
  3. no se cruzan empresas: el transportador de la Quesera A no ve —ni deja
     ver— nada de la Quesera B.

Hay un cliente real usando esto: los totales que se imprimen aquí son los que
el dueño revisa a mano.
"""
from tests.conftest import auth_headers

GRILLA = "/api/v1/recepciones/grilla/quincena?desde=2026-06-01&hasta=2026-06-15"


def _crear(client, headers, url, payload):
    r = client.post(url, json=payload, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _escenario(client, headers):
    """Dos rutas, dos transportadores y tres proveedores.

    Reparto pensado para que el filtro por transportador NO sea lo mismo que el
    filtro por ruta:
      - Stella (Ruta Norte) recoge a Alberto y a Bernardo;
      - Efraín (Ruta Sur) recoge a Carmen Y TAMBIÉN un día a Alberto.
    Así Alberto tiene días de los dos, y Stella cubre proveedores de una sola
    ruta mientras Efraín cruza rutas.
    """
    norte = _crear(client, headers, "/api/v1/rutas", {"nombre": "Ruta Norte", "municipio": "Norte"})
    sur = _crear(client, headers, "/api/v1/rutas", {"nombre": "Ruta Sur", "municipio": "Sur"})

    stella = _crear(
        client, headers, "/api/v1/transportadores",
        {"nombre": "Stella", "valor_transporte": "100",
         "rutas": [{"ruta_id": norte["id"], "valor_transporte": "100"}]},
    )
    efrain = _crear(
        client, headers, "/api/v1/transportadores",
        {"nombre": "Efraín", "valor_transporte": "130",
         "rutas": [{"ruta_id": sur["id"], "valor_transporte": "130"}]},
    )

    alberto = _crear(
        client, headers, "/api/v1/proveedores",
        {"nombre": "Alberto", "vereda": "Norte", "precio_litro": "1800", "ruta_id": norte["id"]},
    )
    bernardo = _crear(
        client, headers, "/api/v1/proveedores",
        {"nombre": "Bernardo", "vereda": "Norte", "precio_litro": "1800", "ruta_id": norte["id"]},
    )
    carmen = _crear(
        client, headers, "/api/v1/proveedores",
        {"nombre": "Carmen", "vereda": "Sur", "precio_litro": "1800", "ruta_id": sur["id"]},
    )

    # (fecha, proveedor, transportador, litros)
    recepciones = [
        ("2026-06-01", alberto, stella, "100"),
        ("2026-06-02", alberto, stella, "120"),
        ("2026-06-03", alberto, efrain, "90"),   # el mismo proveedor, otro camión
        ("2026-06-01", bernardo, stella, "80"),
        ("2026-06-01", carmen, efrain, "200"),
    ]
    for fecha, proveedor, transportador, litros in recepciones:
        _crear(
            client, headers, "/api/v1/recepciones",
            {
                "fecha": fecha,
                "proveedor_id": proveedor["id"],
                "transportador_id": transportador["id"],
                "cantidad_litros": litros,
            },
        )
    return {
        "norte": norte, "sur": sur,
        "stella": stella, "efrain": efrain,
        "alberto": alberto, "bernardo": bernardo, "carmen": carmen,
    }


def _resumen(grilla):
    """Filas legibles: {nombre: [días con leche]} + los totales del pie."""
    return {
        f["proveedor_nombre"]: sorted(f["celdas"].keys()) for f in grilla["filas"]
    }


def test_filtro_por_transportador_devuelve_solo_sus_recepciones(client, base_datos):
    """Pedir "lo de Stella" tiene que dejar los días de Stella y NADA más.

    Es filtro de celda: Alberto se queda en la grilla pero pierde el 3 de junio,
    que se lo recogió Efraín. Y los totales se recalculan sobre lo que queda:
    si la pantalla muestra 300 L, el pie tiene que decir 300 L, no 590.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario(client, h)

    completa = client.get(GRILLA, headers=h).json()
    solo_stella = client.get(f"{GRILLA}&transportador_id={d['stella']['id']}", headers=h).json()

    print("\n===== FILTRO POR TRANSPORTADOR =====")
    print(f"  sin filtro:      {_resumen(completa)}")
    print(f"  total litros:    {float(completa['total_litros'])}")
    print(f"  solo Stella:     {_resumen(solo_stella)}")
    print(f"  total litros:    {float(solo_stella['total_litros'])}")

    # Sin filtro está todo: 100 + 120 + 90 + 80 + 200 = 590
    assert float(completa["total_litros"]) == 590

    # Con Stella: Alberto (100 + 120) y Bernardo (80) = 300. Carmen no aparece.
    assert _resumen(solo_stella) == {
        "Alberto": ["2026-06-01", "2026-06-02"],
        "Bernardo": ["2026-06-01"],
    }
    assert float(solo_stella["total_litros"]) == 300

    # El día que recogió Efraín desaparece de la fila de Alberto
    fila_alberto = next(f for f in solo_stella["filas"] if f["proveedor_nombre"] == "Alberto")
    assert "2026-06-03" not in fila_alberto["celdas"]
    assert float(fila_alberto["total_litros"]) == 220

    # LOS DESGLOSES CUADRAN: la suma de las filas es el total del pie, y la suma
    # de los totales por día también. Es lo que el dueño revisa a mano.
    suma_filas = sum(float(f["total_litros"]) for f in solo_stella["filas"])
    suma_dias = sum(float(v) for v in solo_stella["totales_dia"].values())
    print(f"  suma de filas:   {suma_filas}")
    print(f"  suma por día:    {suma_dias}")
    assert suma_filas == 300
    assert suma_dias == 300

    # Y la plata igual: 300 L × $1.800 de leche, 300 L × $100 de flete de Stella
    print(f"  valor neto:      {float(solo_stella['total_valor_neto'])}")
    print(f"  transporte:      {float(solo_stella['total_transporte'])}")
    assert float(solo_stella["total_valor_neto"]) == 300 * 1800
    assert float(solo_stella["total_transporte"]) == 300 * 100

    # Efraín: Alberto el 3 (90 L) y Carmen el 1 (200 L) = 290 L, a $130 de flete
    solo_efrain = client.get(f"{GRILLA}&transportador_id={d['efrain']['id']}", headers=h).json()
    print(f"  solo Efraín:     {_resumen(solo_efrain)}")
    print(f"  total litros:    {float(solo_efrain['total_litros'])}")
    assert _resumen(solo_efrain) == {"Alberto": ["2026-06-03"], "Carmen": ["2026-06-01"]}
    assert float(solo_efrain["total_litros"]) == 290
    assert float(solo_efrain["total_transporte"]) == 290 * 130

    # Las dos partes suman el todo: nada se perdió ni se contó dos veces
    assert float(solo_stella["total_litros"]) + float(solo_efrain["total_litros"]) == 590


def test_transportador_se_combina_con_ruta_y_con_buscar_proveedor(client, base_datos):
    """Los tres filtros se APILAN, no se pisan.

    El caso que lo demuestra es Efraín, que cruza rutas: él solo trae Alberto
    (Norte) y Carmen (Sur); sumándole la ruta Norte tiene que quedar únicamente
    Alberto; y sumándole además la búsqueda "carm" no puede quedar nada, porque
    Carmen no es de la ruta Norte.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario(client, h)
    efrain = f"transportador_id={d['efrain']['id']}"
    norte = f"ruta_id={d['norte']['id']}"

    print("\n===== FILTROS COMBINADOS =====")

    solo_efrain = client.get(f"{GRILLA}&{efrain}", headers=h).json()
    print(f"  Efraín:                  {_resumen(solo_efrain)} · "
          f"{float(solo_efrain['total_litros'])} L")
    assert sorted(_resumen(solo_efrain)) == ["Alberto", "Carmen"]

    efrain_norte = client.get(f"{GRILLA}&{efrain}&{norte}", headers=h).json()
    print(f"  Efraín + Ruta Norte:     {_resumen(efrain_norte)} · "
          f"{float(efrain_norte['total_litros'])} L")
    assert _resumen(efrain_norte) == {"Alberto": ["2026-06-03"]}
    assert float(efrain_norte["total_litros"]) == 90

    # Stella + Ruta Norte: sus dos proveedores son de Norte, así que no cambia
    stella_norte = client.get(
        f"{GRILLA}&transportador_id={d['stella']['id']}&{norte}", headers=h
    ).json()
    print(f"  Stella + Ruta Norte:     {_resumen(stella_norte)} · "
          f"{float(stella_norte['total_litros'])} L")
    assert float(stella_norte["total_litros"]) == 300

    # Stella + Ruta Sur: Stella no recoge en el Sur -> ninguna fila (y el
    # frontend muestra el estado vacío explicando que fue por los filtros)
    stella_sur = client.get(
        f"{GRILLA}&transportador_id={d['stella']['id']}&ruta_id={d['sur']['id']}", headers=h
    ).json()
    print(f"  Stella + Ruta Sur:       {_resumen(stella_sur)} · "
          f"{float(stella_sur['total_litros'])} L")
    assert stella_sur["filas"] == []
    assert float(stella_sur["total_litros"]) == 0

    # Efraín + buscar "alb" -> solo el día de Alberto
    efrain_alberto = client.get(f"{GRILLA}&{efrain}&search=alb", headers=h).json()
    print(f"  Efraín + buscar 'alb':   {_resumen(efrain_alberto)} · "
          f"{float(efrain_alberto['total_litros'])} L")
    assert _resumen(efrain_alberto) == {"Alberto": ["2026-06-03"]}
    assert float(efrain_alberto["total_litros"]) == 90

    # Los tres a la vez, contradictorios: Carmen no es de la ruta Norte
    vacio = client.get(f"{GRILLA}&{efrain}&{norte}&search=carm", headers=h).json()
    print(f"  Efraín + Norte + 'carm': {_resumen(vacio)} · {float(vacio['total_litros'])} L")
    assert vacio["filas"] == []
    assert float(vacio["total_litros"]) == 0


def test_el_selector_ofrece_tambien_los_transportadores_inactivos(client, base_datos):
    """Un transportador retirado no puede volverse invisible.

    El selector de la grilla es un filtro de CONSULTA: pide /transportadores sin
    estado, a propósito. Si pidiera solo los activos, al apartar a un
    transportador su historia dejaría de poderse mirar en la pantalla —justo lo
    contrario de lo que se busca al apartarlo—. Y su grilla vieja tiene que
    seguir respondiendo con las cifras de siempre.
    """
    h = auth_headers(client, "admin.a")
    d = _escenario(client, h)

    # Se aparta a Stella: estado inactivo, que es "ya no trabaja con nosotros".
    # (No se usa DELETE porque ese es un borrado lógico —pone deleted_at— y esos
    # registros no los devuelve ninguna consulta del sistema, ni esta.)
    r = client.put(
        f"/api/v1/transportadores/{d['stella']['id']}", json={"estado": "inactivo"}, headers=h
    )
    assert r.status_code == 200, r.text

    activos = client.get("/api/v1/transportadores?page_size=100&estado=activo", headers=h).json()
    todos = client.get("/api/v1/transportadores?page_size=100", headers=h).json()
    nombres_activos = sorted(t["nombre"] for t in activos["items"])
    nombres_todos = sorted(t["nombre"] for t in todos["items"])

    print("\n===== TRANSPORTADOR RETIRADO =====")
    print(f"  con estado=activo (formulario): {nombres_activos}")
    print(f"  sin estado (filtro de la grilla): {nombres_todos}")
    assert nombres_activos == ["Efraín"]
    assert nombres_todos == ["Efraín", "Stella"], "el retirado desapareció del filtro"

    # Y su quincena sigue consultándose igual
    solo_stella = client.get(f"{GRILLA}&transportador_id={d['stella']['id']}", headers=h).json()
    print(f"  grilla de Stella retirada:      {_resumen(solo_stella)} · "
          f"{float(solo_stella['total_litros'])} L")
    assert float(solo_stella["total_litros"]) == 300


def test_el_filtro_no_cruza_empresas(client, base_datos):
    """Cada quesera ve lo suyo y nada más.

    Se monta el mismo escenario en las dos empresas y se pide la grilla de la
    Quesera A con el id de un transportador de la Quesera B. No puede devolver
    ni una gota: el filtro va sobre la consulta, que ya viene acotada por
    empresa_id y deleted_at IS NULL.
    """
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    a = _escenario(client, h_a)
    b = _escenario(client, h_b)

    print("\n===== AISLAMIENTO ENTRE EMPRESAS =====")
    assert a["stella"]["id"] != b["stella"]["id"]

    # A con SU Stella: sus 300 L
    propia = client.get(f"{GRILLA}&transportador_id={a['stella']['id']}", headers=h_a).json()
    print(f"  A con la Stella de A: {_resumen(propia)} · {float(propia['total_litros'])} L")
    assert float(propia["total_litros"]) == 300

    # A con la Stella de B: nada. Ni filas ni litros de la otra quesera.
    ajena = client.get(f"{GRILLA}&transportador_id={b['stella']['id']}", headers=h_a).json()
    print(f"  A con la Stella de B: {_resumen(ajena)} · {float(ajena['total_litros'])} L")
    assert ajena["filas"] == []
    assert float(ajena["total_litros"]) == 0
    assert float(ajena["total_transporte"]) == 0

    # Y al revés, para que no sea casualidad del orden en que se crearon
    ajena_b = client.get(f"{GRILLA}&transportador_id={a['efrain']['id']}", headers=h_b).json()
    print(f"  B con el Efraín de A: {_resumen(ajena_b)} · {float(ajena_b['total_litros'])} L")
    assert ajena_b["filas"] == []
    assert float(ajena_b["total_litros"]) == 0

    # B con SU Efraín sí ve lo suyo (la prueba anterior no pasó por estar vacía)
    propia_b = client.get(f"{GRILLA}&transportador_id={b['efrain']['id']}", headers=h_b).json()
    print(f"  B con el Efraín de B: {_resumen(propia_b)} · {float(propia_b['total_litros'])} L")
    assert float(propia_b["total_litros"]) == 290
