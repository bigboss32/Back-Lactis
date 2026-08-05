"""ATAQUE ADVERSARIAL a la tabla puente transportador+ruta+tarifa.

No prueba que la función sirva: prueba que NO se puede romper. Dos cosas se
atacan, porque son las dos que le cuestan plata al cliente real:

  · EL AISLAMIENTO ENTRE EMPRESAS — la tarifa por ruta es plata, y una ruta de
    otra quesera metida acá significa que la cuenta de una depende de un dato que
    la otra puede cambiar;
  · LA INTEGRIDAD DE LA TABLA PUENTE — tarifas repetidas, en cero callado, con
    tres decimales, o apuntando a filas borradas.

Cada prueba imprime lo que obtuvo. Las que pasan cierran un hueco; las que
FALLAN son hallazgos, y están marcadas con el comentario "HALLAZGO".
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import insert, select, text

from app.modules.rutas.models import Ruta
from app.modules.transportadores.models import Transportador, TransportadorRuta
from tests.conftest import auth_headers

TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
LIQUIDACIONES = "/api/v1/liquidaciones"


def D(v):
    return Decimal(str(v))


def centavos(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _crear(client, headers, url, payload):
    r = client.post(url, json=payload, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _detalle(r):
    try:
        return r.json().get("error", {}).get("detail", "")
    except Exception:
        return r.text[:200]


# ===========================================================================
# ATAQUE 1 · UNA RUTA DE LA EMPRESA B EN UN TRANSPORTADOR DE LA EMPRESA A
# ===========================================================================
def test_ataque_ruta_ajena_por_todas_las_puertas(client, base_datos, db_session):
    """Se intenta por CREAR, por EDITAR, con el superadmin, y mirando la basura.

    Lo que ya está probado en test_transportador_rutas es crear y editar con el
    admin de la A. Acá se agregan las puertas que quedaban:
      · el SUPERADMIN, que no tiene empresa propia y actúa con el header
        X-Empresa-Id: si el aislamiento saliera del token y no del header, esta
        sería la puerta abierta;
      · el admin de la B intentando colgarle una ruta suya al transportador de la A;
      · y después de cada intento, se cuenta la tabla puente EN LA BASE, no en la
        respuesta: un rechazo que igual dejó la fila insertada es peor que un sí.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    hs = auth_headers(client, "superadmin")
    empresa_a = base_datos["empresa_a"]
    empresa_b = base_datos["empresa_b"]

    ajena = _crear(client, hb, RUTAS, {"nombre": "Ruta de la B", "municipio": "Otra"})
    propia = _crear(client, ha, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = _crear(client, ha, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [{"ruta_id": propia["id"], "valor_transporte": "242.76"}],
    })

    print("\n===== ATAQUE 1: RUTA DE OTRA EMPRESA =====")
    intentos = [
        ("superadmin con X-Empresa-Id de la A, al crear",
         lambda: client.post(TRANSPORTADORES, headers={**hs, "X-Empresa-Id": str(empresa_a.id)},
                             json={"nombre": "Colado", "valor_transporte": "100",
                                   "rutas": [{"ruta_id": ajena["id"], "valor_transporte": "999"}]})),
        ("superadmin con X-Empresa-Id de la A, al editar",
         lambda: client.put(f"{TRANSPORTADORES}/{alex['id']}",
                            headers={**hs, "X-Empresa-Id": str(empresa_a.id)},
                            json={"rutas": [{"ruta_id": ajena["id"], "valor_transporte": "999"}]})),
        ("superadmin con X-Empresa-Id de la B sobre el transportador de la A",
         lambda: client.put(f"{TRANSPORTADORES}/{alex['id']}",
                            headers={**hs, "X-Empresa-Id": str(empresa_b.id)},
                            json={"rutas": [{"ruta_id": ajena["id"], "valor_transporte": "999"}]})),
        ("el admin de la B editando el transportador de la A",
         lambda: client.put(f"{TRANSPORTADORES}/{alex['id']}", headers=hb,
                            json={"rutas": [{"ruta_id": ajena["id"], "valor_transporte": "999"}]})),
        ("el admin de la A con la ruta de la B (otra vez, por si acaso)",
         lambda: client.put(f"{TRANSPORTADORES}/{alex['id']}", headers=ha,
                            json={"rutas": [{"ruta_id": ajena["id"], "valor_transporte": "999"}]})),
    ]
    for etiqueta, hacer in intentos:
        r = hacer()
        print(f"  {etiqueta:<58} → {r.status_code} · {_detalle(r)[:60]}")
        assert r.status_code != 500, f"{etiqueta}: reventó con 500"
        assert r.status_code not in (200, 201), f"{etiqueta}: ENTRÓ LA RUTA AJENA"

    # Y en la base no quedó NI UNA fila apuntando a la ruta de la otra empresa.
    db_session.expire_all()
    colados = db_session.scalars(
        select(TransportadorRuta).where(TransportadorRuta.ruta_id == uuid.UUID(ajena["id"]))
    ).all()
    print(f"  filas de la tabla puente con la ruta de la B: {len(colados)}")
    assert colados == [], "quedó basura cruzada en transportador_rutas"

    # La tarifa buena que ya tenía sigue intacta después de los cinco intentos.
    sigue = client.get(f"{TRANSPORTADORES}/{alex['id']}", headers=ha).json()
    print(f"  las rutas de Alex siguen siendo: "
          f"{[(f['nombre'], f['valor_transporte']) for f in sigue['rutas']]}")
    assert len(sigue["rutas"]) == 1
    assert D(sigue["rutas"][0]["valor_transporte"]) == D("242.76")


def test_ataque_lectura_no_muestra_rutas_de_otra_empresa(client, base_datos):
    """La B no puede leer ni la lista ni el detalle del transportador de la A."""
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    napoles = _crear(client, ha, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = _crear(client, ha, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "242.76"}],
    })

    print("\n===== ATAQUE 1b: LECTURA CRUZADA =====")
    detalle = client.get(f"{TRANSPORTADORES}/{alex['id']}", headers=hb)
    print(f"  la B pidiendo el transportador de la A → {detalle.status_code}")
    assert detalle.status_code == 404, detalle.text

    lista = client.get(TRANSPORTADORES, headers=hb).json()
    print(f"  transportadores que ve la B: {[i['nombre'] for i in lista['items']]}")
    assert lista["items"] == []

    # Y al revés: la A no ve ninguna tarifa que no sea suya.
    mia = client.get(f"{TRANSPORTADORES}/{alex['id']}", headers=ha).json()
    assert [f["nombre"] for f in mia["rutas"]] == ["Napoles"]


def test_ataque_una_fila_cruzada_en_la_base_se_muestra_como_propia(
    client, base_datos, db_session
):
    """HALLAZGO · La LECTURA de las rutas no filtra por empresa.

    `TransportadorRuta.ruta` es un relationship pelado (`lazy="selectin"`), sin
    `empresa_id` ni `deleted_at IS NULL`: carga la ruta por su llave y ya. O sea que
    el aislamiento de la lectura descansa ENTERO en que la escritura nunca deje
    entrar una ruta ajena, sin ninguna segunda barrera.

    Esta prueba planta a mano UNA fila cruzada —transportador de la A, ruta de la
    B— que es EXACTAMENTE la fila que produce el backfill de la migración
    c6b1e4a8d3f7 si el cliente tiene un `transportadores.ruta_id` cruzado (el
    endpoint viejo no validaba empresa: era el BaseService genérico, ver
    `git show HEAD:app/modules/transportadores/service.py`), y comprueba qué hace
    la lectura con ella.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")
    ajena = _crear(client, hb, RUTAS, {"nombre": "SECRETO DE LA B", "municipio": "Otra"})
    alex = _crear(client, ha, TRANSPORTADORES, {"nombre": "Alex", "valor_transporte": "130"})

    # La fila que dejaría el backfill de un dato viejo cruzado.
    db_session.execute(insert(TransportadorRuta).values(
        id=uuid.uuid4(), transportador_id=uuid.UUID(alex["id"]),
        ruta_id=uuid.UUID(ajena["id"]), valor_transporte=D("999.99"),
    ))
    db_session.commit()

    leido = client.get(f"{TRANSPORTADORES}/{alex['id']}", headers=ha)
    print("\n===== ATAQUE 1c: FILA CRUZADA PLANTADA =====")
    print(f"  la A lee su transportador → {leido.status_code}")
    print(f"  rutas que le salen: "
          f"{[(f['nombre'], f['valor_transporte']) for f in leido.json()['rutas']]}")
    nombres = [f["nombre"] for f in leido.json()["rutas"]]
    assert "SECRETO DE LA B" not in nombres, (
        "HALLAZGO: la lectura de un transportador de la empresa A muestra el NOMBRE "
        "de una ruta de la empresa B. La lectura no tiene ningún filtro de empresa."
    )


# ===========================================================================
# ATAQUE 2 · LA MISMA RUTA DOS VECES, POR CAMINOS TORCIDOS
# ===========================================================================
def test_ataque_misma_ruta_repetida_en_todas_sus_formas(client, base_datos, db_session):
    """Repetida con mayúsculas, tres veces, y colada por dos PUT seguidos.

    Lo ya probado es la repetición literal. Acá se prueba que el mismo UUID escrito
    distinto (mayúsculas, con guiones y sin guiones) también se detecta —si la
    comparación fuera de textos y no de UUID, `242,76` y `300` entrarían las dos y
    la tarifa que cobra el señor la escogería el orden del SELECT—.
    """
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    crudo = napoles["id"]
    variantes = [crudo, crudo.upper(), crudo.replace("-", ""), crudo.replace("-", "").upper()]

    print("\n===== ATAQUE 2: LA MISMA RUTA REPETIDA =====")
    for i, otra_forma in enumerate(variantes[1:], start=1):
        r = client.post(TRANSPORTADORES, headers=h, json={
            "nombre": f"Alex {i}", "valor_transporte": "130",
            "rutas": [
                {"ruta_id": crudo, "valor_transporte": "242.76"},
                {"ruta_id": otra_forma, "valor_transporte": "300"},
            ],
        })
        print(f"  el id repetido como '{otra_forma[:12]}...' → {r.status_code}")
        assert r.status_code == 422, (
            f"HALLAZGO: la misma ruta pasó dos veces escribiendo el id como "
            f"{otra_forma}: {r.text[:200]}"
        )

    # Tres veces, con tres tarifas
    r = client.post(TRANSPORTADORES, headers=h, json={
        "nombre": "Alex 9", "valor_transporte": "130",
        "rutas": [{"ruta_id": crudo, "valor_transporte": v} for v in ("100", "200", "300")],
    })
    print(f"  la misma ruta TRES veces → {r.status_code}")
    assert r.status_code == 422

    # Y en la base no quedó ninguna fila de los intentos fallidos
    db_session.expire_all()
    total = db_session.scalar(select(text("count(*)")).select_from(TransportadorRuta.__table__))
    print(f"  filas en transportador_rutas después de los intentos: {total}")
    assert total == 0


def test_ataque_dos_filas_iguales_no_caben_ni_a_la_fuerza(client, base_datos, db_session):
    """El único de (transportador_id, ruta_id) existe DE VERDAD en la base.

    La validación del servicio se puede saltar (un script, un seed, una migración
    futura). Si el único no estuviera, la fila duplicada entraría y la tarifa que
    se cobra la escogería el orden del SELECT: el helper devuelve LA PRIMERA que
    encuentra en `transportador.rutas`.
    """
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex", "valor_transporte": "130",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "242.76"}],
    })
    print("\n===== ATAQUE 2b: EL ÚNICO EN LA BASE =====")
    with pytest.raises(Exception) as excinfo:
        db_session.execute(insert(TransportadorRuta).values(
            id=uuid.uuid4(), transportador_id=uuid.UUID(alex["id"]),
            ruta_id=uuid.UUID(napoles["id"]), valor_transporte=D("300"),
        ))
        db_session.flush()
    print(f"  la base rechazó la fila repetida: {type(excinfo.value).__name__}")
    db_session.rollback()


# ===========================================================================
# ATAQUE 3 · BORRAR LA RUTA O EL TRANSPORTADOR Y DEJAR BASURA
# ===========================================================================
def test_ataque_borrar_la_ruta_deja_al_transportador_sin_poderse_editar(
    client, base_datos
):
    """HALLAZGO · Con una ruta borrada, el transportador queda TRABADO.

    La secuencia es la que hace cualquier pantalla: LEER, cambiar un campo, GUARDAR
    lo leído. La lectura devuelve la ruta borrada (no filtra `deleted_at`), y la
    escritura la RECHAZA por borrada. O sea que después de botar una ruta, el
    diálogo del transportador no puede guardar ni el teléfono: manda de vuelta lo
    que el propio API le acabó de dar y le responden 422.
    """
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    mira = _crear(client, h, RUTAS, {"nombre": "Mira Valle", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [
            {"ruta_id": napoles["id"], "valor_transporte": "242.76"},
            {"ruta_id": mira["id"], "valor_transporte": "300"},
        ],
    })
    assert client.delete(f"{RUTAS}/{napoles['id']}", headers=h).status_code == 204

    print("\n===== ATAQUE 3: LA RUTA BORRADA =====")
    leido = client.get(f"{TRANSPORTADORES}/{alex['id']}", headers=h)
    assert leido.status_code == 200, "la lectura no puede reventar por una ruta borrada"
    rutas_leidas = leido.json()["rutas"]
    print(f"  después de botar Napoles, la lectura devuelve: "
          f"{[(f['nombre'], f['valor_transporte']) for f in rutas_leidas]}")

    # Lo que hace la pantalla: devolver lo leído, cambiando solo el teléfono.
    devolver = [
        {"ruta_id": f["ruta_id"], "valor_transporte": f["valor_transporte"]}
        for f in rutas_leidas
    ]
    r = client.put(f"{TRANSPORTADORES}/{alex['id']}", headers=h,
                   json={"telefono": "3001234567", "rutas": devolver})
    print(f"  guardar el teléfono devolviendo esas mismas rutas → {r.status_code} · "
          f"{_detalle(r)[:70]}")
    assert r.status_code == 200, (
        "HALLAZGO: la lectura devuelve una ruta borrada que la escritura rechaza; "
        "leer-modificar-guardar (lo que hace toda pantalla) queda imposible"
    )


def test_ataque_ruta_borrada_no_le_mueve_la_plata_ni_revienta_el_comprobante(
    client, base_datos
):
    """Botar la ruta después de recibir la leche no puede mover el flete ya guardado."""
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex", "valor_transporte": "1",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "242.76"}],
    })
    libardo = _crear(client, h, PROVEEDORES, {
        "nombre": "Libardo", "vereda": "Napoles", "precio_litro": "1800",
        "ruta_id": napoles["id"],
    })
    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": libardo["id"],
        "transportador_id": alex["id"], "cantidad_litros": "82",
    })
    assert D(recepcion["valor_transporte"]) == D("19906.32")

    # Se bota la ruta DESPUÉS de recibir la leche.
    assert client.delete(f"{RUTAS}/{napoles['id']}", headers=h).status_code == 204

    print("\n===== ATAQUE 3b: BOTAR LA RUTA DESPUÉS DE LA LECHE =====")
    r = client.post(f"{LIQUIDACIONES}/generar", headers=h, json={
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "transportador",
    })
    print(f"  generar el comprobante → {r.status_code}")
    assert r.status_code in (200, 201), r.text
    liq = [x for x in r.json()["generadas"] if x["tipo"] == "transportador"][0]
    detalles = liq["detalles"]
    for d in detalles:
        print(f"    {d['fecha']}  ruta={d['ruta_nombre']!r}  "
              f"{D(d['litros'])} L × ${D(d['precio_litro'])} = ${D(d['valor'])}")
        assert centavos(D(d["litros"]) * D(d["precio_litro"])) == D(d["valor"]), (
            "el renglón no cuadra con la ruta borrada"
        )
    suma = sum((D(d["valor"]) for d in detalles), D(0))
    print(f"  suma de renglones ${suma} · total del comprobante "
          f"${D(liq['valor_transporte'])} · foto guardada $19.906,32")
    assert suma == D(liq["valor_transporte"]) == D("19906.32")

    # Y el PDF sale (no revienta por una ruta borrada)
    pdf = client.get(f"{LIQUIDACIONES}/{liq['id']}/pdf", headers=h)
    print(f"  el PDF del comprobante → {pdf.status_code}")
    assert pdf.status_code == 200


def test_ataque_borrar_el_transportador_no_revienta_ni_pierde_la_tarifa(
    client, base_datos, db_session
):
    """El soft delete del transportador deja sus tarifas, y no rompe ninguna lectura."""
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex", "valor_transporte": "130",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "242.76"}],
    })
    assert client.delete(f"{TRANSPORTADORES}/{alex['id']}", headers=h).status_code == 204

    print("\n===== ATAQUE 3c: BORRAR EL TRANSPORTADOR =====")
    db_session.expire_all()
    filas = db_session.scalars(
        select(TransportadorRuta).where(
            TransportadorRuta.transportador_id == uuid.UUID(alex["id"])
        )
    ).all()
    print(f"  tarifas que le quedaron: {[(str(f.ruta_id)[:8], str(f.valor_transporte)) for f in filas]}")
    assert len(filas) == 1, "las tarifas del transportador borrado se perdieron"
    assert D(filas[0].valor_transporte) == D("242.76")

    lista = client.get(TRANSPORTADORES, headers=h)
    print(f"  la lista de transportadores → {lista.status_code} · "
          f"{len(lista.json()['items'])} activos")
    assert lista.status_code == 200
    assert lista.json()["items"] == []


# ===========================================================================
# ATAQUE 4 · TARIFAS RARAS: CERO, NEGATIVA, TRES DECIMALES, GIGANTE, NaN
# ===========================================================================
@pytest.mark.parametrize("tarifa,debe_entrar,por_que", [
    ("0", True, "cero es válido: hay rutas en que no se cobra flete"),
    ("-0.01", False, "nadie le paga negativo al transportador"),
    ("-242.76", False, "negativa"),
    ("NaN", False, "NaN se multiplicaría por los litros y el total quedaría en NaN"),
    ("Infinity", False, "infinito"),
    ("242.765", False, "TRES decimales en una columna Numeric(12,2)"),
    ("0.001", False, "una milésima de peso no existe"),
    ("99999999999999.99", False, "no cabe en Numeric(12,2): en Postgres es un 22003"),
    ("1E+20", False, "notación científica gigante"),
])
def test_ataque_tarifas_raras(client, base_datos, tarifa, debe_entrar, por_que):
    """Cada valor raro que se le puede escribir a una tarifa por litro.

    Los que importan de verdad son los que la columna NO puede guardar: la columna
    es Numeric(12,2), o sea dos decimales y doce dígitos. Postgres redondea el
    tercer decimal en silencio al guardar y revienta con un 22003 si no cabe; el
    schema no valida ni lo uno ni lo otro (`Field(ge=0)` a secas, cuando en
    ventas/schemas.py el mismo tipo de campo va con `decimal_places=2`).
    """
    h = auth_headers(client, "admin.a")
    ruta = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    r = client.post(TRANSPORTADORES, headers=h, json={
        "nombre": "Alex Agudelo", "valor_transporte": "130",
        "rutas": [{"ruta_id": ruta["id"], "valor_transporte": tarifa}],
    })
    entro = r.status_code in (200, 201)
    guardado = r.json()["rutas"][0]["valor_transporte"] if entro else None
    print(f"\n  tarifa {tarifa!r:<22} → {r.status_code}"
          f"{f' · quedó guardada como {guardado!r}' if entro else ''}   ({por_que})")
    assert r.status_code != 500, f"la tarifa {tarifa} reventó con 500"
    assert entro is debe_entrar, (
        f"HALLAZGO: la tarifa {tarifa!r} {'no entró' if debe_entrar else 'ENTRÓ'} "
        f"y debía ser lo contrario ({por_que}). Respuesta: {r.text[:160]}"
    )


def test_ataque_tarifa_que_no_cabe_en_la_columna(client, base_datos):
    """La tarifa que no cabe en Numeric(12,2) SE RECHAZA, y el techo cuadra.

    CORREGIDA: esta prueba nació dando por hecho que 1E+20 entraba (usaba `_crear`,
    que exige 200/201) y comprobaba después la magnitud del estropicio. Era la
    expectativa equivocada: lo que había que exigir no es que el desastre sea
    pequeño, es que la tarifa no entre. El schema ahora la limita a la forma exacta
    de la columna (`max_digits=12, decimal_places=2`), así que 1E+20 rebota con un
    422 y un mensaje, no con un 22003 de Postgres convertido en 500.

    Y se comprueba lo que hacía falta comprobar y no se comprobaba: QUE EL TECHO DE
    LA TARIFA SEA COHERENTE CON LA COLUMNA DEL FLETE. Con la tarifa más alta que
    cabe, el flete de un día grande todavía tiene que caber en el Numeric(14,2) de
    `recepciones_leche.valor_transporte`. Si no cupiera, el 22003 solo se habría
    corrido de sitio.
    """
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    tope_tarifa = D("9999999999.99")     # Numeric(12,2) de la tarifa
    tope_valor = D("999999999999.99")    # Numeric(14,2) del flete guardado

    print("\n===== ATAQUE 4c: LA TARIFA QUE NO CABE =====")
    r = client.post(TRANSPORTADORES, headers=h, json={
        "nombre": "Alex", "valor_transporte": "130",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "1E+20"}],
    })
    print(f"  tarifa 1E+20             → {r.status_code} · {_detalle(r)[:70]}")
    assert r.status_code == 422, (
        f"HALLAZGO: entró una tarifa que no cabe en Numeric(12,2); en Postgres el "
        f"INSERT revienta con 22003 y sale un 500. Respuesta: {r.text[:160]}"
    )

    # La más alta que SÍ cabe entra, y el flete que produce sigue cabiendo.
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex", "valor_transporte": "130",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": str(tope_tarifa)}],
    })
    libardo = _crear(client, h, PROVEEDORES, {
        "nombre": "Libardo", "vereda": "Napoles", "precio_litro": "1800",
        "ruta_id": napoles["id"],
    })
    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": libardo["id"],
        "transportador_id": alex["id"], "cantidad_litros": "82",
    })
    flete = D(recepcion["valor_transporte"])
    print(f"  tope de Numeric(12,2)    : ${tope_tarifa:,.2f}  (se acepta)")
    print(f"  flete de 82 L que guardó : ${flete:,.2f}")
    print(f"  tope de Numeric(14,2)    : ${tope_valor:,.2f}")
    assert D(alex["rutas"][0]["valor_transporte"]) == tope_tarifa
    assert flete == centavos(D("82") * tope_tarifa), "el flete del tope no cuadra"
    assert flete <= tope_valor, (
        "el techo de la tarifa no cuadra con la columna del flete: con la tarifa más "
        "alta que se acepta, el flete de un día ya no cabe en Numeric(14,2)"
    )


def test_ataque_lo_que_responde_el_post_es_lo_que_se_va_a_cobrar(client, base_datos):
    """La tarifa que devuelve el POST tiene que ser la que quedó GUARDADA.

    CORREGIDA, y vale la pena decir en qué. La prueba nació metiendo $242,765 con
    `_crear` (que exige 200/201) para demostrar que el POST respondía $242,765
    mientras la columna Numeric(12,2) guardaba otra cosa. La expectativa de fondo
    era la correcta —lo que responde el API es lo que se va a cobrar— pero el camino
    para llegar a ella ya no existe: el schema limita la tarifa a la forma exacta de
    la columna, así que los tres decimales rebotan con un 422 y nunca hay un
    Decimal en memoria distinto del de la base.

    Así que se prueban LAS DOS MITADES de la regla:

      1. la tarifa de tres decimales NO entra, y no deja ninguna fila a medias
         —era el camino por el que el dato falso llegaba a la pantalla, y de paso el
         que hacía que el mismo dato diera $242,76 en SQLite y $242,77 en Postgres,
         o sea una tarifa distinta según el motor—;
      2. con una tarifa que sí cabe, el eco del POST es EXACTAMENTE lo que lee una
         SESIÓN NUEVA, que es la que va a calcular el flete mañana (en producción
         cada petición trae su propia sesión).
    """
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})

    print("\n===== ATAQUE 4b: TARIFA DE TRES DECIMALES =====")
    r = client.post(TRANSPORTADORES, headers=h, json={
        "nombre": "Alex Agudelo", "valor_transporte": "1",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "242.765"}],
    })
    print(f"  tarifa $242,765 (tres decimales) → {r.status_code} · {_detalle(r)[:70]}")
    assert r.status_code == 422, (
        f"HALLAZGO: entró una tarifa de tres decimales en una columna de dos. El API "
        f"va a responder una tarifa por litro que no es la que se le paga al "
        f"transportador. Respuesta: {r.text[:160]}"
    )
    from tests.conftest import TestingSessionLocal
    otra = TestingSessionLocal()
    try:
        colados = otra.execute(text("select count(*) from transportador_rutas")).scalar()
    finally:
        otra.close()
    print(f"  filas que dejó el intento fallido: {colados}")
    assert colados == 0, "el intento rechazado dejó una fila escrita"

    # Y con una tarifa que sí cabe, el eco del POST es lo que se va a cobrar.
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "1",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "242.76"}],
    })
    eco_del_post = D(alex["rutas"][0]["valor_transporte"])

    otra = TestingSessionLocal()
    try:
        de_la_base = D(
            otra.get(Transportador, uuid.UUID(alex["id"])).rutas[0].valor_transporte
        )
        crudo = otra.execute(text("select valor_transporte from transportador_rutas")).all()
    finally:
        otra.close()

    print(f"  lo que responde el POST          : ${eco_del_post}")
    print(f"  lo que hay en la columna (SQL)   : {crudo}")
    print(f"  lo que lee el ORM en sesión nueva: ${de_la_base}  <-- con esta se cobra")
    print(f"  o sea: 82 L le dan ${centavos(D('82') * eco_del_post)} con la del POST "
          f"y ${centavos(D('82') * de_la_base)} con la de la base")

    assert eco_del_post == de_la_base, (
        f"HALLAZGO: el POST respondió ${eco_del_post} y con lo que quedó guardado se "
        f"cobra ${de_la_base}. La pantalla muestra una tarifa por litro que no es la "
        f"que se le va a pagar al transportador."
    )


# ===========================================================================
# ATAQUE 5 · EL HELPER DE TARIFA: QUE NO SE CRUCE NI CAIGA EN CERO CALLADO
# ===========================================================================
def test_ataque_el_helper_no_cae_en_la_tarifa_de_otro_transportador(
    client, base_datos, db_session
):
    """Dos transportadores, LA MISMA ruta, tarifas distintas: cada uno con la suya.

    Si el helper buscara la fila por `ruta_id` sin amarrarla a SU transportador,
    Alex cobraría la tarifa de Stella. Se prueba con las dos tarifas cruzadas a
    propósito y en los dos sentidos.
    """
    from app.modules.transportadores.tarifas import tarifa_por_litro

    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    otra = _crear(client, h, RUTAS, {"nombre": "La Esperanza", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex", "valor_transporte": "130",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "242.76"}],
    })
    stella = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Stella", "valor_transporte": "500",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "999.99"}],
    })
    db_session.expire_all()
    obj_alex = db_session.get(Transportador, uuid.UUID(alex["id"]))
    obj_stella = db_session.get(Transportador, uuid.UUID(stella["id"]))

    print("\n===== ATAQUE 5: EL HELPER NO SE CRUZA =====")
    casos = [
        ("Alex en Napoles", obj_alex, uuid.UUID(napoles["id"]), D("242.76")),
        ("Stella en Napoles", obj_stella, uuid.UUID(napoles["id"]), D("999.99")),
        ("Alex en una ruta sin tarifa propia", obj_alex, uuid.UUID(otra["id"]), D("130")),
        ("Stella en una ruta sin tarifa propia", obj_stella, uuid.UUID(otra["id"]), D("500")),
        ("Alex sin ruta", obj_alex, None, D("130")),
    ]
    for etiqueta, obj, ruta_id, esperado in casos:
        obtenido = tarifa_por_litro(obj, ruta_id)
        print(f"  {etiqueta:<38} → ${obtenido}   (esperado ${esperado})")
        assert obtenido == esperado, f"{etiqueta}: se cruzó la tarifa"

    # Una ruta de OTRA empresa nunca puede darle una tarifa: cae en la general.
    hb = auth_headers(client, "admin.b")
    ajena = _crear(client, hb, RUTAS, {"nombre": "De la B", "municipio": "Otra"})
    de_ruta_ajena = tarifa_por_litro(obj_alex, uuid.UUID(ajena["id"]))
    print(f"  {'Alex en una ruta de la empresa B':<38} → ${de_ruta_ajena}   "
          f"(la general, $130)")
    assert de_ruta_ajena == D("130")


def test_ataque_el_helper_nunca_devuelve_un_cero_callado(client, base_datos, db_session):
    """Sin tarifa de ruta y con general en cero, el flete es cero: tiene que verse.

    Es el caso que más plata puede esconder al revés —el transportador trabajando
    gratis sin que nadie se dé cuenta hasta cuadrar la quincena—. No es un bug del
    helper (cero es cero), pero sí hay que comprobar que la recepción lo devuelva
    en cero VISIBLE y no que el comprobante se genere igual con un total en cero.
    """
    h = auth_headers(client, "admin.a")
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex", "valor_transporte": "0",
        "rutas": [{"ruta_id": napoles["id"], "valor_transporte": "0"}],
    })
    libardo = _crear(client, h, PROVEEDORES, {
        "nombre": "Libardo", "vereda": "Napoles", "precio_litro": "1800",
        "ruta_id": napoles["id"],
    })
    recepcion = _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": libardo["id"],
        "transportador_id": alex["id"], "cantidad_litros": "82",
    })
    print("\n===== ATAQUE 5b: EL CERO CALLADO =====")
    print(f"  flete de 82 L con todo en cero: ${D(recepcion['valor_transporte'])}")
    assert D(recepcion["valor_transporte"]) == D("0")

    r = client.post(f"{LIQUIDACIONES}/generar", headers=h, json={
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "transportador",
    })
    corrida = r.json()
    generadas = [x for x in corrida["generadas"] if x["tipo"] == "transportador"]
    print(f"  comprobantes de transportador generados: {len(generadas)} "
          f"(no se genera uno en cero, se salta)")
    assert generadas == [], "un comprobante de flete en cero no debería generarse"

    # Y EL SALTO SE AVISA, que es la otra mitad del "cero callado": antes Alex
    # simplemente no salía en la respuesta —igual que quien no trajo leche—, y el dueño
    # no tenía cómo distinguir "a este no le tocaba nada" de "a este le faltó la tarifa
    # y sus 82 L se quedaron sin comprobante". Ahora sale en `omitidas` con el motivo.
    omitida = corrida["omitidas"][0]
    print(f"  omitidas: {len(corrida['omitidas'])} -> {omitida['motivo']}")
    assert len(corrida["omitidas"]) == 1
    assert omitida["motivo_codigo"] == "flete_sin_tarifa"
    assert (omitida["tipo"], omitida["cuenta"]) == ("transportador", "flete")
    assert omitida["tercero_id"] == alex["id"]
    assert omitida["tercero_nombre"] == "Alex"
    # Dice los litros que quedaron esperando y qué hacer.
    assert "82 L" in omitida["motivo"] and "tarifa" in omitida["motivo"]


# ===========================================================================
# ATAQUE 6 · LA INVARIANTE DEL COMPROBANTE, CON RUTAS Y TARIFAS PELEADAS
# ===========================================================================
def test_ataque_invariante_con_tres_rutas_y_tarifas_cambiadas_a_media_quincena(
    client, base_datos
):
    """El caso más sucio que se puede armar sin tocar la base a mano.

    Tres rutas, tarifas con centavos, el mismo día repartido en las tres, y la
    tarifa de una CAMBIADA a mitad de quincena. Se comprueban las tres patas de la
    invariante: cada renglón cuadra, los renglones suman el total, y el total es la
    suma de las fotos guardadas.
    """
    h = auth_headers(client, "admin.a")
    rutas = {}
    proveedores = {}
    tarifas = {"Napoles": "242.76", "Mira Valle": "317.53", "La Esperanza": "88.09"}
    for nombre in tarifas:
        rutas[nombre] = _crear(client, h, RUTAS, {"nombre": nombre, "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo", "valor_transporte": "150",
        "rutas": [{"ruta_id": rutas[n]["id"], "valor_transporte": t}
                  for n, t in tarifas.items()],
    })
    for nombre in tarifas:
        proveedores[nombre] = _crear(client, h, PROVEEDORES, {
            "nombre": f"Productor {nombre}", "vereda": nombre, "precio_litro": "1800",
            "ruta_id": rutas[nombre]["id"],
        })
    # Un segundo productor en Napoles: es el que permite que el MISMO día y la
    # MISMA ruta tengan dos fotos a tarifas distintas (una recepción por proveedor
    # y fecha es lo único que la base deja repetir).
    otro_de_napoles = _crear(client, h, PROVEEDORES, {
        "nombre": "Segundo de Napoles", "vereda": "Napoles", "precio_litro": "1800",
        "ruta_id": rutas["Napoles"]["id"],
    })

    litros_por_dia = {
        "2026-06-01": {"Napoles": "82", "Mira Valle": "95.37", "La Esperanza": "13.5"},
        "2026-06-02": {"Napoles": "77.19", "Mira Valle": "101"},
    }
    for fecha, reparto in litros_por_dia.items():
        for nombre, litros in reparto.items():
            _crear(client, h, RECEPCIONES, {
                "fecha": fecha, "proveedor_id": proveedores[nombre]["id"],
                "transportador_id": alex["id"], "cantidad_litros": litros,
            })

    # A MITAD DE QUINCENA le cambian la tarifa de Napoles, y sigue recibiendo leche
    r = client.put(f"{TRANSPORTADORES}/{alex['id']}", headers=h, json={
        "rutas": [{"ruta_id": rutas[n]["id"],
                   "valor_transporte": "301.11" if n == "Napoles" else t}
                  for n, t in tarifas.items()],
    })
    assert r.status_code == 200, r.text
    _crear(client, h, RECEPCIONES, {
        "fecha": "2026-06-01", "proveedor_id": otro_de_napoles["id"],
        "transportador_id": alex["id"], "cantidad_litros": "40.25",
    })

    r = client.post(f"{LIQUIDACIONES}/generar", headers=h, json={
        "periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15", "tipo": "transportador",
    })
    assert r.status_code in (200, 201), r.text
    liq = [x for x in r.json()["generadas"] if x["tipo"] == "transportador"][0]

    # Las fotos se leen DESPUÉS de generar, y no es un detalle de la prueba: al
    # liquidar un flete que todavía no ha movido plata, el comprobante vuelve a derivar
    # la cifra de cada día con la TARIFA VIVA, así que los DOS días de Napoles quedan a
    # $301,11 y no uno a $242,76 y otro a $301,11. Es lo que le permite al dueño
    # corregir una tarifa mal tecleada; leerlas antes sería comparar el papel contra
    # unas cifras que el papel ya no dice.
    fotos = client.get(f"{RECEPCIONES}?page_size=100", headers=h).json()["items"]
    suma_fotos = sum((D(x["valor_transporte"]) for x in fotos), D(0))
    # La cuenta del dueño para el día revuelto: 82 + 40,25 = 122,25 L en Napoles a la
    # tarifa que él ve hoy en la pantalla.
    del_dia = [d for d in liq["detalles"]
               if d["fecha"] == "2026-06-01" and d["ruta_nombre"] == "Napoles"]
    assert len(del_dia) == 1, f"Napoles del 01/06 tenía que ser UN renglón: {del_dia}"
    assert D(del_dia[0]["litros"]) == D("122.25")
    assert D(del_dia[0]["precio_litro"]) == D("301.11")

    print("\n===== ATAQUE 6: TRES RUTAS Y UNA TARIFA CAMBIADA A MEDIA QUINCENA =====")
    suma = D(0)
    for d in liq["detalles"]:
        cuadra = centavos(D(d["litros"]) * D(d["precio_litro"])) == D(d["valor"])
        print(f"  {d['fecha']}  {str(d['ruta_nombre'] or '-'):<14} "
              f"{D(d['litros']):>9,.2f} L × ${D(d['precio_litro']):>9,.2f} "
              f"= ${D(d['valor']):>12,.2f}  {'OK' if cuadra else '<<< NO CUADRA'}")
        assert cuadra, f"el renglón {d} no cuadra"
        suma += D(d["valor"])
    print(f"  {'suma de los renglones':<48} = ${suma:>12,.2f}")
    print(f"  {'valor_transporte del comprobante':<48} = ${D(liq['valor_transporte']):>12,.2f}")
    print(f"  {'suma de las fotos de las recepciones':<48} = ${suma_fotos:>12,.2f}")
    assert suma == D(liq["valor_transporte"]), "los renglones no suman el total"
    assert D(liq["valor_transporte"]) == suma_fotos, "el total no es la suma de las fotos"

    # Y recalcular no puede cambiar el papel
    rec = client.post(f"{LIQUIDACIONES}/{liq['id']}/recalcular", headers=h)
    if rec.status_code in (200, 201):
        de_nuevo = rec.json()
        suma2 = sum((D(d["valor"]) for d in de_nuevo["detalles"]), D(0))
        print(f"  después de recalcular: ${suma2:,.2f} (antes ${suma:,.2f})")
        assert suma2 == suma == D(de_nuevo["valor_transporte"])


# ===========================================================================
# ATAQUE 7 · POR DÓNDE ENTRA UNA FILA CRUZADA: EL BACKFILL DE LA MIGRACIÓN
# ===========================================================================
def test_ataque_el_backfill_copia_una_ruta_de_otra_empresa(tmp_path):
    """HALLAZGO · El backfill de la migración no comprueba la empresa.

    Es la reachability del ATAQUE 1c: el endpoint NUEVO cierra bien la puerta, pero
    el dato viejo del cliente entra por la migración sin pasar por esa puerta.

    `transportadores.ruta_id` la escribía el endpoint VIEJO, que era el BaseService
    genérico SIN NINGUNA validación de ruta (ver
    `git show HEAD:app/modules/transportadores/service.py`): aceptaba cualquier UUID
    de ruta, de cualquier empresa. Si en la base del cliente hay una sola fila así,
    `backfill_rutas_de_transportadores` la copia tal cual a la tabla puente —su
    SELECT es `where ruta_id is not null` y nada más— y desde ahí la lectura la
    muestra como propia, porque la lectura tampoco filtra.
    """
    import importlib.util
    from datetime import datetime, timezone
    from pathlib import Path

    from sqlalchemy import create_engine, insert, select as sa_select, text as sa_text
    from sqlalchemy.pool import StaticPool

    from app.core.database import Base as _Base
    from app.modules.empresas.models import Empresa

    ruta_mig = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "c6b1e4a8d3f7_tarifa_por_ruta_del_transportador.py"
    )
    spec = importlib.util.spec_from_file_location("mig_ataque", ruta_mig)
    migracion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migracion)

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _Base.metadata.create_all(bind=engine)
    print("\n===== ATAQUE 7: EL BACKFILL Y LA RUTA CRUZADA =====")
    try:
        with engine.begin() as conn:
            conn.execute(sa_text("ALTER TABLE transportadores ADD COLUMN ruta_id CHAR(32)"))
            id_a, id_b = uuid.uuid4(), uuid.uuid4()
            for eid, nombre, nit in ((id_a, "Quesera A", "900A"), (id_b, "Quesera B", "900B")):
                conn.execute(insert(Empresa.__table__).values(
                    id=eid, nombre=nombre, nit=nit,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                ))
            ruta_de_b = uuid.uuid4()
            conn.execute(insert(Ruta.__table__).values(
                id=ruta_de_b, empresa_id=id_b, nombre="SECRETO DE LA B", municipio="Otra",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            # El transportador de la A apuntando a la ruta de la B: exactamente lo
            # que el endpoint viejo dejaba guardar.
            trans_de_a = uuid.uuid4()
            conn.execute(insert(Transportador.__table__).values(
                id=trans_de_a, empresa_id=id_a, nombre="Alex Agudelo",
                valor_transporte=D("242.76"), estado="activo",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            conn.execute(sa_text(
                "UPDATE transportadores SET ruta_id = :r WHERE id = :i"
            ).bindparams(r=ruta_de_b.hex, i=trans_de_a.hex))

        with engine.begin() as conn:
            copiadas = migracion.backfill_rutas_de_transportadores(conn)
            print(f"  filas que copió el backfill: {copiadas}")
            cruzadas = conn.execute(sa_select(
                TransportadorRuta.__table__.c.transportador_id,
                TransportadorRuta.__table__.c.ruta_id,
                TransportadorRuta.__table__.c.valor_transporte,
            )).all()
            print(f"  el transportador de la A quedó con la ruta de la B: "
                  f"{cruzadas[0][1].hex == ruta_de_b.hex if cruzadas else 'no hay filas'}")
            assert not cruzadas or cruzadas[0][1].hex != ruta_de_b.hex, (
                "HALLAZGO: el backfill copió a transportador_rutas una ruta de la "
                "empresa B colgada de un transportador de la empresa A. La migración "
                "no valida empresa y la lectura tampoco, así que esa tarifa queda "
                "visible como propia en la pantalla de la Quesera A."
            )
    finally:
        _Base.metadata.drop_all(bind=engine)
