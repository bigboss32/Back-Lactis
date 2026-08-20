"""EL FLETE COBRADO POR DÍA FIJO: "el transporte de leche a fábrica vale 150k
independientemente de los litros".

Lo pidió el dueño así, textual: "en el transporte hay un nuevo requerimiento: que sea
por litro o que sea por día fijo, es decir, el transporte de leche a fábrica vale 150k
independientemente de los litros".

LO QUE ESTE ARCHIVO PROTEGE, y es UNA sola cosa por encima de todas las demás: que el
fijo sea POR DÍA Y POR RUTA. Si ese día recogió leche de CINCO proveedores en la ruta a
fábrica, el flete del día son $150.000, NO $150.000 × 5 = $750.000. Ese es el error que
hay que hacer imposible, y la primera prueba de abajo es la que lo hace imposible.

LAS CIFRAS DEL CUADRE, escritas a mano acá y no calculadas por el código que se está
probando. Alex Agudelo, quincena del 16 al 31 de julio de 2026, con DOS rutas y DOS
modos —que es justo lo que el dueño pidió poder tener a la vez—:

    ruta "A fábrica"  →  $150.000 POR DÍA (fijo)
    ruta "Napoles"    →  $242,76 POR LITRO
    tarifa general    →  $200 por litro (solo aplica donde no hay ruta; no tiene que
                         aparecer en ninguna parte de estas pruebas)

EL DÍA DE LOS CINCO PROVEEDORES — 16/07/2026, ruta A fábrica:

    Aurelio      82,00 L
    Marleny     137,45 L
    Gilberto     96,30 L
    Ramiro       60,00 L
    Rosa        124,20 L
                --------
                499,95 L      →   EL DÍA VALE $150.000,00   (no $750.000)

  y esos $150.000 se reparten entre las cinco fotos EN PROPORCIÓN A LOS LITROS, porque
  la foto de cada recepción es "cuánto costó recoger la leche de ese productor ese día" y
  la leen la contabilidad, la grilla de la quincena y el costeo:

    $150.000 ÷ 499,95 L = $300,03000300030003... el litro (que NO es una tarifa: es solo
    el factor del reparto, y por eso no se imprime en ninguna parte)

    Aurelio    82,00 × 300,030003... = 24.602,4602...  →  piso 24.602,46   (fracción 0,46)
    Marleny   137,45 × 300,030003... = 41.239,1239...  →  piso 41.239,12   (fracción 0,12)
    Gilberto   96,30 × 300,030003... = 28.892,8892...  →  piso 28.892,88   (fracción 0,88)
    Ramiro     60,00 × 300,030003... = 18.001,8001...  →  piso 18.001,80   (fracción 0,80)
    Rosa      124,20 × 300,030003... = 37.263,7263...  →  piso 37.263,72   (fracción 0,72)
                                                          -----------
                                       suma de los pisos  149.999,98
                                       faltan                    0,02

  Los dos centavos que faltan se entregan por RESTO MAYOR, o sea a las dos fracciones de
  centavo más grandes: la de Gilberto (0,8892) y la de Rosa (0,7263). Quedan:

    Aurelio    82,00 L  →  $ 24.602,46
    Marleny   137,45 L  →  $ 41.239,12
    Gilberto   96,30 L  →  $ 28.892,89   (+1 centavo)
    Ramiro     60,00 L  →  $ 18.001,80
    Rosa      124,20 L  →  $ 37.263,73   (+1 centavo)
                           -----------
                           $150.000,00   == EXACTO el renglón del comprobante

EL RENGLÓN DEL COMPROBANTE de ese día NO puede decir "litros × precio = valor", porque
esa multiplicación no reproduce los $150.000. Dice que es el día completo:

    16/07/2026 | A fabrica | 499,95 L | Día completo | $150.000,00

y se verifica trivialmente: el día vale $150.000. Un día por litro se sigue verificando
multiplicando. La regla de oro no se mueve: los renglones suman EXACTO el total del
comprobante.
"""
import io
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader
from sqlalchemy import select

from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers

RUTAS = "/api/v1/rutas"
PROVEEDORES = "/api/v1/proveedores"
TRANSPORTADORES = "/api/v1/transportadores"
RECEPCIONES = "/api/v1/recepciones"
LIQUIDACIONES = "/api/v1/liquidaciones"

FIJO = Decimal("150000")
NAPOLES = Decimal("242.76")
GENERAL = Decimal("200")

# El día de los cinco proveedores, tal como está escrito en el encabezado.
EL_DIA = "2026-07-16"
CINCO = (
    ("Aurelio", Decimal("82.00"), Decimal("24602.46")),
    ("Marleny", Decimal("137.45"), Decimal("41239.12")),
    ("Gilberto", Decimal("96.30"), Decimal("28892.89")),
    ("Ramiro", Decimal("60.00"), Decimal("18001.80")),
    ("Rosa", Decimal("124.20"), Decimal("37263.73")),
)
LITROS_DEL_DIA = Decimal("499.95")


def D(v):
    return Decimal(str(v))


def centavos(valor):
    """La misma regla del backend: al centavo, con el medio centavo para arriba."""
    return D(valor).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _crear(client, headers, url, payload):
    r = client.post(url, json=payload, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _escenario(client, h, *, modo_napoles="litro"):
    """Alex con la ruta A fábrica en DÍA FIJO y Nápoles POR LITRO, y seis proveedores.

    La tarifa GENERAL queda en $200 POR LITRO, distinta de las dos: si el código se
    equivocara y leyera la general donde no debe, las cifras saldrían disparatadas en vez
    de parecidas y la prueba lo grita.
    """
    fabrica = _crear(client, h, RUTAS, {"nombre": "A fabrica", "municipio": "Granada"})
    napoles = _crear(client, h, RUTAS, {"nombre": "Napoles", "municipio": "Granada"})
    alex = _crear(client, h, TRANSPORTADORES, {
        "nombre": "Alex Agudelo",
        "valor_transporte": str(GENERAL),
        "modo_transporte": "litro",
        "rutas": [
            {"ruta_id": fabrica["id"], "valor_transporte": str(FIJO),
             "modo_transporte": "dia_fijo"},
            {"ruta_id": napoles["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": modo_napoles},
        ],
    })
    proveedores = {}
    for nombre, _, _ in CINCO:
        proveedores[nombre] = _crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "La Vega", "precio_litro": "1800",
            "ruta_id": fabrica["id"]})
    # Uno de Nápoles, para el día que mezcla los dos modos.
    proveedores["Henri"] = _crear(client, h, PROVEEDORES, {
        "nombre": "Henri", "vereda": "Napoles", "precio_litro": "1800",
        "ruta_id": napoles["id"]})
    return {"fabrica": fabrica, "napoles": napoles, "alex": alex, "proveedores": proveedores}


def _recibir(client, h, esc, fecha, proveedor, litros_, **extra):
    cuerpo = {
        "fecha": fecha,
        "proveedor_id": esc["proveedores"][proveedor]["id"],
        "transportador_id": esc["alex"]["id"],
        "cantidad_litros": str(litros_),
    }
    cuerpo.update(extra)
    return _crear(client, h, RECEPCIONES, cuerpo)


def _liquidar_flete(client, h, inicio="2026-07-16", fin="2026-07-31"):
    r = client.post(
        f"{LIQUIDACIONES}/generar",
        json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": "transportador"},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["generadas"], f"no se generó comprobante de flete: {r.json()}"
    return r.json()["generadas"][0]


def _renglones(liq):
    """Los renglones en el orden en que se leen: por fecha y por nombre de ruta."""
    return sorted(liq["detalles"], key=lambda d: (d["fecha"], d["ruta_nombre"] or ""))


def _fotos_de(db_session, liq_id):
    """Las fotos del flete guardadas en las recepciones de ese comprobante."""
    db_session.expire_all()
    filas = db_session.scalars(
        select(RecepcionLeche).where(
            RecepcionLeche.liquidacion_transporte_id == uuid.UUID(liq_id),
            RecepcionLeche.deleted_at.is_(None),
        )
    ).all()
    return {D(f.cantidad_litros): D(f.valor_transporte) for f in filas}, sum(
        (D(f.valor_transporte) for f in filas), D(0)
    )


def _revisar_invariante(liq, fotos_esperadas=None):
    """LA REGLA DE ORO, con las dos formas de verificar un renglón.

      · un renglón POR LITRO se verifica multiplicando: litros × precio == valor;
      · uno de DÍA FIJO se verifica leyéndolo —el día vale lo que dice— y lo único que
        se le exige a las cifras es que NO tenga tarifa por litro (iría en cero, porque
        no existe ninguna que reproduzca el valor);
      · y los dos tipos juntos suman EXACTO el total del comprobante.

    Devuelve la suma de los renglones.
    """
    suma = D(0)
    for renglon in _renglones(liq):
        litros_ = D(renglon["litros"])
        precio = D(renglon["precio_litro"])
        valor = D(renglon["valor"])
        if renglon["modo_transporte"] == "dia_fijo":
            assert precio == D(0), (
                "un renglón de día fijo no puede traer tarifa por litro: no existe "
                f"ninguna que reproduzca ${valor} — trae ${precio}"
            )
        else:
            assert centavos(litros_ * precio) == valor, (
                f"el renglón {renglon['fecha']} / {renglon['ruta_nombre']} no cuadra: "
                f"{litros_} × {precio} = {centavos(litros_ * precio)} y dice {valor}"
            )
        suma += valor
    assert suma == D(liq["valor_transporte"]), "los renglones no suman el valor del flete"
    assert D(liq["valor_total"]) == D(liq["valor_transporte"])
    if fotos_esperadas is not None:
        assert suma == fotos_esperadas, "el total no es la suma de las fotos guardadas"
    return suma


def texto_pdf(contenido: bytes) -> str:
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


# ---------------------------------------------------------------------------
# 1. LA PRUEBA QUE IMPORTA: cinco proveedores el mismo día son UN fijo
# ---------------------------------------------------------------------------
def test_cinco_proveedores_el_mismo_dia_valen_un_solo_fijo(client, base_datos, db_session):
    """Las cifras del encabezado, recepción por recepción y renglón por renglón.

        Aurelio    82,00 L  →  $ 24.602,46
        Marleny   137,45 L  →  $ 41.239,12
        Gilberto   96,30 L  →  $ 28.892,89
        Ramiro     60,00 L  →  $ 18.001,80
        Rosa      124,20 L  →  $ 37.263,73
                  --------     -----------
                  499,95 L     $150.000,00     y el comprobante dice $150.000,00

    Lo que NO puede pasar, y es el error que esta prueba hace imposible: que el
    comprobante diga $750.000 —el fijo cobrado una vez por proveedor— ni que las cinco
    fotos sumen algo distinto de $150.000.

    Se revisa DESPUÉS DE CADA RECEPCIÓN y no solo al final, porque ahí está la parte
    delicada del diseño: la foto se escribe al registrar CADA recepción, pero el fijo del
    día solo se conoce cuando se sabe cuáles son todas. Registrar la segunda le cambia lo
    que le tocaba a la primera.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)

    print("\n===== 1. CINCO PROVEEDORES, UN SOLO FIJO DE $150.000 =====")
    print("  se registran de a uno, y despues de cada uno las fotos del dia suman el fijo:")
    for cuantos in range(1, 6):
        nombre, litros_, _ = CINCO[cuantos - 1]
        _recibir(client, h, esc, EL_DIA, nombre, litros_)
        # Las fotos del día tal como quedaron guardadas después de esta recepción.
        db_session.expire_all()
        delas = db_session.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.fecha == date(2026, 7, 16),
                RecepcionLeche.deleted_at.is_(None),
            )
        ).all()
        suma = sum((D(r.valor_transporte) for r in delas), D(0))
        print(f"    con {cuantos} recepcion(es): "
              + " + ".join(f"${D(r.valor_transporte)}" for r in
                           sorted(delas, key=lambda r: r.cantidad_litros))
              + f" = ${suma}")
        assert suma == FIJO, (
            f"con {cuantos} recepciones el dia fijo tenia que seguir valiendo ${FIJO} "
            f"y las fotos suman ${suma}"
        )

    # LAS CINCO FOTOS FINALES, una por una, contra las cifras escritas a mano arriba.
    db_session.expire_all()
    por_litros = {
        D(r.cantidad_litros): D(r.valor_transporte)
        for r in db_session.scalars(
            select(RecepcionLeche).where(RecepcionLeche.deleted_at.is_(None))
        ).all()
    }
    print("\n  las cinco fotos, contra la cuenta a mano:")
    for nombre, litros_, esperada in CINCO:
        print(f"    {nombre:<9}{litros_:>8} L  ->  ${por_litros[litros_]:>10}   "
              f"a mano ${esperada}")
        assert por_litros[litros_] == esperada, (
            f"la foto de {nombre} tenia que ser ${esperada} y es ${por_litros[litros_]}"
        )
    assert sum(por_litros[l] for _, l, _ in CINCO) == FIJO

    # EL COMPROBANTE: UN renglón, del día completo, por $150.000.
    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    renglones = _renglones(liq)
    print("\n  el comprobante:")
    for r in renglones:
        print(f"    {r['fecha']}  {r['ruta_nombre']:<11}{D(r['litros']):>9} L  "
              f"[{r['modo_transporte']}]  ${D(r['valor'])}")
    assert len(renglones) == 1, (
        f"el dia tenia que ser UN renglon de dia completo y salieron {len(renglones)}: "
        f"{renglones}"
    )
    unico = renglones[0]
    assert unico["fecha"] == EL_DIA
    assert unico["ruta_nombre"] == "A fabrica"
    assert unico["modo_transporte"] == "dia_fijo"
    assert D(unico["litros"]) == LITROS_DEL_DIA, "los litros del dia van al lado, completos"
    assert D(unico["precio_litro"]) == D(0), "un dia fijo no tiene tarifa por litro"
    assert D(unico["valor"]) == FIJO

    # Y EL TOTAL: $150.000, no $750.000.
    print(f"\n  valor_transporte del comprobante: ${D(liq['valor_transporte'])}")
    print(f"  el error que esto hace imposible:  ${FIJO * 5} (el fijo por proveedor)")
    assert D(liq["valor_transporte"]) == FIJO
    assert D(liq["valor_transporte"]) != FIJO * 5
    assert D(liq["total_litros"]) == LITROS_DEL_DIA

    _, fotos = _fotos_de(db_session, liq["id"])
    assert _revisar_invariante(liq, fotos) == FIJO


# ---------------------------------------------------------------------------
# 2. El mismo día con DOS rutas, una fija y otra por litro
# ---------------------------------------------------------------------------
def test_el_mismo_dia_con_una_ruta_fija_y_otra_por_litro(client, base_datos, db_session):
    """Dos renglones del MISMO día, cada uno verificable a su manera, sumando el total.

    Es lo que el dueño pidió poder tener: el mismo transportador con Nápoles a $242,76 el
    litro y "a fábrica" a $150.000 el día, y el mismo día puede hacer las dos.

        16/07  A fabrica   219,45 L   Día completo         $150.000,00
        16/07  Napoles     137,45 L × $242,76 = $ 33.367,36
                                                -----------
                                                $183.367,36

      (137,45 × 242,76 = 33.367,362 → $33.367,36, redondeado UNA vez)

    El de A fábrica se verifica leyéndolo: el día vale $150.000, sin importar que hayan
    sido dos proveedores ni 219,45 litros. El de Nápoles se verifica multiplicando.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)

    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    henri = _recibir(client, h, esc, EL_DIA, "Henri", "137.45")
    assert D(henri["valor_transporte"]) == D("33367.36"), (
        "el dia por litro no cambia: 137,45 x 242,76"
    )

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    renglones = _renglones(liq)

    print("\n===== 2. UN DIA CON LAS DOS FORMAS DE COBRAR =====")
    for r in renglones:
        como = ("Dia completo" if r["modo_transporte"] == "dia_fijo"
                else f"x ${D(r['precio_litro'])}")
        print(f"  {r['fecha']}  {r['ruta_nombre']:<11}{D(r['litros']):>9} L  "
              f"{como:<14}${D(r['valor'])}")

    assert len(renglones) == 2, f"tenian que ser DOS renglones: {renglones}"
    fijo = [r for r in renglones if r["ruta_nombre"] == "A fabrica"][0]
    litro = [r for r in renglones if r["ruta_nombre"] == "Napoles"][0]

    # El fijo: el día completo, 219,45 L al lado, $150.000 y sin tarifa por litro.
    assert fijo["modo_transporte"] == "dia_fijo"
    assert D(fijo["litros"]) == D("219.45")
    assert D(fijo["precio_litro"]) == D(0)
    assert D(fijo["valor"]) == FIJO

    # El de por litro: como siempre, y se reproduce con calculadora.
    assert litro["modo_transporte"] == "litro"
    assert D(litro["litros"]) == D("137.45")
    assert D(litro["precio_litro"]) == NAPOLES
    assert D(litro["valor"]) == centavos(D("137.45") * NAPOLES) == D("33367.36")

    total = FIJO + D("33367.36")
    print(f"  suma de los dos renglones: ${total}  ·  comprobante: "
          f"${D(liq['valor_transporte'])}")
    _, fotos = _fotos_de(db_session, liq["id"])
    assert _revisar_invariante(liq, fotos) == total == D("183367.36")

    # EL PROMEDIO NO MIENTE: con días fijos mezclados no puede afirmar una tarifa por
    # litro. Va en cero y la bandera le dice a la pantalla que escriba "—".
    print(f"  precio_promedio: ${D(liq['precio_promedio'])} · "
          f"tiene_dias_fijos: {liq['tiene_dias_fijos']}")
    assert liq["tiene_dias_fijos"] is True
    assert D(liq["precio_promedio"]) == D(0), (
        "con un dia fijo mezclado, el promedio por litro no reproduce nada: no se afirma"
    )


# ---------------------------------------------------------------------------
# 3. Un fijo con UN solo proveedor, y un fijo sin litros
# ---------------------------------------------------------------------------
def test_un_fijo_con_un_solo_proveedor_vale_el_dia_completo(client, base_datos, db_session):
    """Un solo proveedor ese día: la foto es el fijo completo y el renglón también.

        16/07  A fabrica   82,00 L   Día completo   $150.000,00

    Es el caso que hace ver que el fijo NO es "el fijo dividido entre los proveedores":
    con uno solo no se divide entre nada, vale el día.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    solo = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")

    print("\n===== 3. UN FIJO CON UN SOLO PROVEEDOR =====")
    print(f"  la foto de la unica recepcion: ${D(solo['valor_transporte'])}")
    assert D(solo["valor_transporte"]) == FIJO

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    renglones = _renglones(liq)
    assert len(renglones) == 1
    assert D(renglones[0]["valor"]) == FIJO
    assert renglones[0]["modo_transporte"] == "dia_fijo"
    _, fotos = _fotos_de(db_session, liq["id"])
    assert _revisar_invariante(liq, fotos) == FIJO


def test_un_fijo_sin_litros_se_reparte_en_partes_iguales():
    """CON LOS LITROS EN CERO el fijo sigue valiendo $150.000, y se parte en iguales.

    El camión hizo el viaje: el fijo se cobra por haber ido, no por lo que trajo. Y sin
    litros no hay proporción posible, así que ninguna recepción puede reclamar más que
    otra:

        3 recepciones de 0 L, fijo $150.000  →  $50.000,00 cada una   (suma $150.000,00)
        3 recepciones de 0 L, fijo $100      →  $33,34 + $33,33 + $33,33 = $100,00

    (El segundo caso es el que prueba que los centavos también se cierran: 100 ÷ 3 no es
    exacto y el que sobra se entrega por resto mayor.)

    Se prueba sobre la función y no por el API porque el API no deja registrar una
    recepción de cero litros (`cantidad_litros` exige `gt=0`), y está bien que no deje:
    lo que se protege acá es que si un cero llega por otro camino —una fila vieja, una
    corrección en la base— la cuenta no se caiga ni deje de sumar.
    """
    from app.modules.transportadores.tarifas import (
        MODO_DIA_FIJO,
        Tarifa,
        reparto_entre_las_fotos,
        valor_del_grupo,
    )

    print("\n===== 3b. UN FIJO CON LITROS EN CERO =====")
    for fijo, esperado in ((FIJO, [D("50000.00")] * 3), (D("100"), [D("33.34"), D("33.33"), D("33.33")])):
        tarifa = Tarifa(MODO_DIA_FIJO, fijo)
        assert valor_del_grupo(tarifa, D(0)) == centavos(fijo), (
            "sin litros el dia fijo sigue valiendo lo que vale"
        )
        partes = [("a", D(0)), ("b", D(0)), ("c", D(0))]
        reparto = reparto_entre_las_fotos(tarifa, partes, centavos(fijo))
        valores = sorted(reparto.values(), reverse=True)
        print(f"  fijo ${fijo} entre 3 recepciones de 0 L: "
              + " + ".join(f"${v}" for v in valores)
              + f" = ${sum(valores)}")
        assert valores == esperado
        assert sum(valores) == centavos(fijo), "las partes tienen que sumar el fijo"


# ---------------------------------------------------------------------------
# 4. El fijo cambiado a mitad de quincena
# ---------------------------------------------------------------------------
def test_el_fijo_cambiado_a_mitad_de_quincena_manda_el_de_hoy(client, base_datos, db_session):
    """Se recibe leche a $150.000 el día, se corrige el fijo a $180.000, y se liquida.

    Manda el fijo VIVO —el único que el dueño tiene en pantalla— igual que ya pasaba con
    la tarifa por litro. Los días que todavía no están en ningún comprobante se vuelven a
    derivar con él:

        16/07  (recibido con el fijo en $150.000)  →  al liquidar, $180.000
        17/07  (recibido con el fijo en $180.000)  →              $180.000
                                                                 ---------
                                                                 $360.000

    Lo que se protege es que las dos cosas pasen a la vez: que el comprobante salga con el
    fijo de hoy, y que las fotos de cada día sigan sumando el renglón de SU día.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)

    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    a = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    print("\n===== 4. EL FIJO CAMBIADO A MITAD DE QUINCENA =====")
    print(f"  con el fijo en $150.000 las dos fotos del 16/07 suman "
          f"${D(a['valor_transporte']) + D('56049.21')}")

    r = client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": "180000",
             "modo_transporte": "dia_fijo"},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES),
             "modo_transporte": "litro"},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    _recibir(client, h, esc, "2026-07-17", "Gilberto", "96.30")

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    renglones = _renglones(liq)
    for renglon in renglones:
        print(f"  {renglon['fecha']}  {renglon['ruta_nombre']:<11}"
              f"{D(renglon['litros']):>9} L  Dia completo  ${D(renglon['valor'])}")

    assert len(renglones) == 2, f"un renglon por dia: {renglones}"
    assert all(r["modo_transporte"] == "dia_fijo" for r in renglones)
    assert all(D(r["valor"]) == D("180000") for r in renglones), (
        "manda el fijo VIVO: los dos dias valen $180.000"
    )
    _, fotos = _fotos_de(db_session, liq["id"])
    assert _revisar_invariante(liq, fotos) == D("360000.00")

    # Y CADA DÍA POR SEPARADO sigue sumando SU renglón: el 16/07 son dos fotos que dan
    # $180.000 y el 17/07 una sola que da $180.000. Si el fijo se hubiera repartido entre
    # los TRES días juntos, ninguno de los dos renglones cuadraría.
    db_session.expire_all()
    del_16 = sum(
        (D(r.valor_transporte) for r in db_session.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.fecha == date(2026, 7, 16),
                RecepcionLeche.deleted_at.is_(None))).all()), D(0))
    print(f"  las fotos del 16/07 suman ${del_16} (su propio renglon)")
    assert del_16 == D("180000.00")


# ---------------------------------------------------------------------------
# 5. Pasar una ruta de por litro a fijo, y al revés
# ---------------------------------------------------------------------------
def test_pasar_una_ruta_de_por_litro_a_fijo_y_al_reves(client, base_datos, db_session):
    """La misma ruta y el mismo día, cambiando SOLO el modo. Ida y vuelta.

        Nápoles, 137,45 L el 16/07:
          por litro  →  137,45 × $242,76 = $ 33.367,36
          día fijo   →                     $242,76        (el día completo vale la cifra)
          por litro  →  137,45 × $242,76 = $ 33.367,36    (vuelve a lo de antes)

    LA MISMA CIFRA EN LA COLUMNA Y DOS PLATAS DISTINTAS: eso es exactamente por qué el
    modo tiene que viajar pegado a la cifra y por qué queda en la bitácora. Y por qué el
    valor por omisión es 'litro': una pantalla que no mande el modo no puede convertirle
    una tarifa por litro en un día fijo sin decirlo.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    recepcion = _recibir(client, h, esc, EL_DIA, "Henri", "137.45")

    def poner_modo(modo):
        r = client.put(
            f"{TRANSPORTADORES}/{esc['alex']['id']}",
            json={"rutas": [
                {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES),
                 "modo_transporte": modo},
            ]},
            headers=h,
        )
        assert r.status_code == 200, r.text
        # El PUT de la recepción sin cambiar nada re-deriva la foto: el día no está en
        # ningún comprobante, y ese es el caso 2 de la regla ("se re-deriva siempre").
        v = client.put(
            f"{RECEPCIONES}/{recepcion['id']}", json={"cantidad_litros": "137.45"}, headers=h
        )
        assert v.status_code == 200, v.text
        return D(v.json()["valor_transporte"])

    print("\n===== 5. LA MISMA RUTA, CAMBIANDO SOLO EL MODO =====")
    print(f"  al recibir (por litro):     ${D(recepcion['valor_transporte'])}")
    assert D(recepcion["valor_transporte"]) == D("33367.36")
    como_fijo = poner_modo("dia_fijo")
    print(f"  con la ruta en dia fijo:    ${como_fijo}  (el dia completo vale $242,76)")
    assert como_fijo == NAPOLES
    de_vuelta = poner_modo("litro")
    print(f"  de vuelta a por litro:      ${de_vuelta}")
    assert de_vuelta == D("33367.36")

    # Y la lectura del transportador dice el modo de cada ruta, que es lo que la pantalla
    # necesita para escribir "$242,76 / L" o "$150.000 el día" sin adivinar.
    alex = client.get(f"{TRANSPORTADORES}/{esc['alex']['id']}", headers=h).json()
    print(f"  la lectura dice el modo: "
          f"{[(x['nombre'], x['modo_transporte']) for x in alex['rutas']]}")
    assert {x["nombre"]: x["modo_transporte"] for x in alex["rutas"]} == {"Napoles": "litro"}
    assert alex["modo_transporte"] == "litro"


# ---------------------------------------------------------------------------
# 6. Agregar, corregir, apagar y borrar una recepción de un día fijo
# ---------------------------------------------------------------------------
def test_agregar_corregir_apagar_y_borrar_una_recepcion_de_un_dia_fijo(
    client, base_datos, db_session
):
    """El fijo del día sigue siendo $150.000 después de cada una de las cuatro cosas.

    Es la prueba de que la cuenta del fijo no está clavada en el momento de registrar:
    cada escritura vuelve a mirar el grupo completo y reparte otra vez.

      · AGREGAR   → tres recepciones, las tres fotos suman $150.000;
      · CORREGIR  → a una se le cambian los litros, siguen sumando $150.000 (y el reparto
                    cambia, porque es proporcional a los litros);
      · APAGAR    → una se apaga; el día lo pagan las dos que quedan y suman $150.000;
      · BORRAR    → una se borra; la que queda carga el día completo, $150.000.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)

    def fotos_activas():
        db_session.expire_all()
        filas = db_session.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.fecha == date(2026, 7, 16),
                RecepcionLeche.estado == "activo",
                RecepcionLeche.deleted_at.is_(None),
            )
        ).all()
        return {D(f.cantidad_litros): D(f.valor_transporte) for f in filas}

    print("\n===== 6. AGREGAR, CORREGIR, APAGAR Y BORRAR =====")
    uno = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    dos = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    print(f"  agregar 3:  {dict(sorted(fotos_activas().items()))}")
    assert sum(fotos_activas().values()) == FIJO

    r = client.put(f"{RECEPCIONES}/{uno['id']}", json={"cantidad_litros": "200"}, headers=h)
    assert r.status_code == 200, r.text
    print(f"  corregir:   {dict(sorted(fotos_activas().items()))}")
    assert sum(fotos_activas().values()) == FIJO, "corregir los litros no puede descuadrar"
    assert D(200) in fotos_activas(), "y la correccion si entro"

    r = client.put(f"{RECEPCIONES}/{dos['id']}", json={"estado": "inactivo"}, headers=h)
    assert r.status_code == 200, r.text
    apagado = fotos_activas()
    print(f"  apagar:     {dict(sorted(apagado.items()))}")
    assert D("137.45") not in apagado, "el dia apagado sale del reparto"
    assert sum(apagado.values()) == FIJO, "el dia sigue valiendo el fijo entre los que quedan"

    r = client.delete(f"{RECEPCIONES}/{uno['id']}", headers=h)
    assert r.status_code in (200, 204), r.text
    borrado = fotos_activas()
    print(f"  borrar:     {dict(sorted(borrado.items()))}")
    assert list(borrado) == [D("96.30")], "queda una sola recepcion activa"
    assert sum(borrado.values()) == FIJO, "y esa una carga el dia completo"

    # Y el comprobante que se genere después dice lo mismo: el día completo, $150.000.
    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    _, fotos = _fotos_de(db_session, liq["id"])
    print(f"  el comprobante: ${D(liq['valor_transporte'])} en "
          f"{len(liq['detalles'])} renglon(es)")
    assert _revisar_invariante(liq, fotos) == FIJO


def test_agregar_una_recepcion_a_un_dia_fijo_ya_liquidado_no_lo_cobra_dos_veces(
    client, base_datos, db_session
):
    """La leche anotada TARDE de un día fijo ya cobrado entra con flete $0,00.

    EL CASO, y es el que hacía cobrar dos veces: el 16/07 Alex recoge a dos proveedores
    en la ruta a fábrica, se le liquida y SE LE PAGA ($150.000). Después alguien anota
    tarde la leche de un tercer proveedor de ESE MISMO día. Un comprobante pagado no
    reserva sus fechas (a propósito: si las reservara, el día anotado tarde no tendría por
    dónde entrar nunca), así que el dueño puede volver a correr la quincena.

    Sin esto le saldría un SEGUNDO renglón "Día completo (a fábrica) $150.000" del mismo
    16 de julio: $150.000 de más por una tarifa que dice "el día vale 150k". Con esto la
    recepción nueva entra en $0,00 —el día ya costó $150.000 y ya se pagó, y recoger un
    proveedor más ese día no cuesta más— y las fotos del día siguen sumando $150.000.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h).status_code == 200

    print("\n===== 6b. LECHE ANOTADA TARDE DE UN DIA FIJO YA PAGADO =====")
    tarde = _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    print(f"  la recepcion anotada tarde entra con flete ${D(tarde['valor_transporte'])}")
    assert D(tarde["valor_transporte"]) == D(0), (
        "ese dia ya se pago completo: recoger un proveedor mas no cuesta mas"
    )

    # El comprobante pagado NO SE MOVIÓ ni un peso.
    pagado = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  el comprobante pagado sigue en ${D(pagado['valor_transporte'])} "
          f"({pagado['estado']})")
    assert D(pagado["valor_transporte"]) == FIJO
    assert len(pagado["detalles"]) == 1

    # Y las fotos del día siguen sumando $150.000, no $300.000.
    db_session.expire_all()
    del_dia = sum(
        (D(r.valor_transporte) for r in db_session.scalars(
            select(RecepcionLeche).where(
                RecepcionLeche.fecha == date(2026, 7, 16),
                RecepcionLeche.estado == "activo",
                RecepcionLeche.deleted_at.is_(None))).all()), D(0))
    print(f"  las fotos del dia suman ${del_dia} (el error seria ${FIJO * 2})")
    assert del_dia == FIJO

    # Y si el dueño vuelve a correr la quincena, no le sale un segundo $150.000: el
    # transportador se OMITE con un motivo que dice la verdad (no le falta ninguna tarifa).
    otra = client.post(
        f"{LIQUIDACIONES}/generar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador"},
        headers=h,
    ).json()
    print(f"  volver a generar: generadas={len(otra['generadas'])} "
          f"omitidas={len(otra['omitidas'])}")
    if otra["omitidas"]:
        print(f"    motivo: {otra['omitidas'][0]['motivo'][:120]}")
        assert "ya se le pagaron completos" in otra["omitidas"][0]["motivo"]
    nuevo_total = sum((D(x["valor_transporte"]) for x in otra["generadas"]), D(0))
    assert nuevo_total == D(0), (
        f"no se puede volver a cobrar el dia fijo: salieron ${nuevo_total} de mas"
    )


# ---------------------------------------------------------------------------
# 7. El candado: un comprobante pagado no se mueve
# ---------------------------------------------------------------------------
def test_un_comprobante_pagado_no_se_mueve_al_cambiar_el_modo_ni_la_tarifa(
    client, base_datos, db_session
):
    """Cambiar el modo (o el fijo) HOY no le toca la plata a un comprobante ya pagado.

    Es la misma regla de siempre y no se afloja ni un centavo: el papel que el conductor
    tiene en la mano dice $150.000, se le entregaron $150.000, y ni pasar la ruta a
    $900.000 el día ni pasarla a por litro le mueven esa cifra, ni la de sus renglones, ni
    las fotos de sus recepciones.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    recepcion = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h).status_code == 200
    antes = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    fotos_antes, suma_antes = _fotos_de(db_session, liq_id)

    print("\n===== 7. EL COMPROBANTE PAGADO NO SE MUEVE =====")
    print(f"  pagado en ${D(antes['valor_transporte'])} · fotos ${suma_antes}")

    for cambio in (
        {"valor_transporte": "900000", "modo_transporte": "dia_fijo"},
        {"valor_transporte": "900000", "modo_transporte": "litro"},
    ):
        r = client.put(
            f"{TRANSPORTADORES}/{esc['alex']['id']}",
            json={"rutas": [dict(cambio, ruta_id=esc["fabrica"]["id"])]},
            headers=h,
        )
        assert r.status_code == 200, r.text
        despues = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
        fotos_despues, suma_despues = _fotos_de(db_session, liq_id)
        print(f"  tras poner la ruta en {cambio['modo_transporte']} a "
              f"${cambio['valor_transporte']}: comprobante "
              f"${D(despues['valor_transporte'])} · fotos ${suma_despues}")
        assert D(despues["valor_transporte"]) == D(antes["valor_transporte"])
        assert [(d["fecha"], d["valor"], d["modo_transporte"]) for d in _renglones(despues)] == [
            (d["fecha"], d["valor"], d["modo_transporte"]) for d in _renglones(antes)
        ]
        assert fotos_despues == fotos_antes, "una foto de un flete pagado no se toca"

    # Y corregir los litros de ese día rebota, como siempre.
    r = client.put(f"{RECEPCIONES}/{recepcion['id']}", json={"cantidad_litros": "90"}, headers=h)
    print(f"  corregir los litros de un dia pagado: {r.status_code}")
    assert r.status_code == 422, r.text
    _revisar_invariante(client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json(), suma_antes)


# ---------------------------------------------------------------------------
# 8. Recalcular re-deriva con el modo de hoy; el recuadre NO
# ---------------------------------------------------------------------------
def test_recalcular_re_deriva_con_el_modo_de_hoy_y_el_recuadre_no(client, base_datos, db_session):
    """La distinción que no se puede perder, ahora con el modo de por medio.

      · RECALCULAR (el botón del dueño) re-deriva con la tarifa Y EL MODO de hoy: el
        comprobante pasa de $150.000 (día fijo) a 219,45 L × $242,76 = $53.273,68.
      · EL RECUADRE automático —la cascada que se dispara al escribir una observación en
        un día— NO re-precifica: el comprobante aprobado sigue diciendo $150.000 aunque
        alguien le haya cambiado el modo a la ruta entre tanto.

    Sin esa segunda mitad, escribir una nota en un día le cambiaría la cifra a un
    comprobante ya aprobado y le quitaría el visto bueno: $96.726,32 de diferencia por un
    campo que no tiene nada que ver con la plata.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    uno = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200

    def papel():
        j = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
        return j["estado"], D(j["valor_transporte"]), [
            (d["modo_transporte"], D(d["valor"])) for d in _renglones(j)
        ]

    print("\n===== 8. RECALCULAR SI, EL RECUADRE NO =====")
    print(f"  aprobado: {papel()}")
    assert papel()[1] == FIJO

    # LA RUTA PASA A POR LITRO. El comprobante aprobado no se puede mover por eso.
    assert client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [{"ruta_id": esc["fabrica"]["id"],
                         "valor_transporte": str(NAPOLES), "modo_transporte": "litro"}]},
        headers=h,
    ).status_code == 200

    # EL RECUADRE: se escribe una observación, que no traba nada y no le mueve la cuenta
    # a nadie. La liquidación vuelve a borrador (eso ya pasaba) pero NO se re-precifica.
    assert client.put(
        f"{RECEPCIONES}/{uno['id']}", json={"observaciones": "el tarro venia mal tapado"},
        headers=h,
    ).status_code == 200
    estado, total, renglones = papel()
    print(f"  tras una observacion (recuadre): {estado} ${total} {renglones}")
    assert total == FIJO, (
        "el recuadre NO re-precifica: el comprobante tenia que seguir en $150.000"
    )
    assert renglones == [("dia_fijo", FIJO)], "y el renglon sigue siendo el dia completo"
    _, fotos = _fotos_de(db_session, liq_id)
    assert fotos == FIJO

    # RECALCULAR: acá sí, y con el modo de hoy.
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h).status_code == 200
    estado, total, renglones = papel()
    print(f"  tras Recalcular:                {estado} ${total} {renglones}")
    esperado = centavos(D("219.45") * NAPOLES)
    assert esperado == D("53273.68")
    assert total == esperado, "Recalcular re-deriva con el modo de hoy (por litro)"
    assert [m for m, _ in renglones] == ["litro"]
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    _, fotos = _fotos_de(db_session, liq_id)
    assert _revisar_invariante(liq, fotos) == esperado
    # Y el promedio vuelve a poder afirmarse, porque ya no hay días fijos.
    print(f"  precio_promedio: ${D(liq['precio_promedio'])} · "
          f"tiene_dias_fijos: {liq['tiene_dias_fijos']}")
    assert liq["tiene_dias_fijos"] is False
    assert D(liq["precio_promedio"]) == NAPOLES


# ---------------------------------------------------------------------------
# 9. El papel y la pantalla: los dos suman exacto, con los dos modos mezclados
# ---------------------------------------------------------------------------
def test_el_desglose_del_pdf_y_de_la_pantalla_suman_exacto(client, base_datos, db_session):
    """El PDF que se le entrega al conductor, con días fijos y por litro mezclados.

    Se revisa lo que él ve: la columna Precio/L dice "Día completo" donde no hay tarifa
    por litro, las cifras impresas son las de los renglones, y la nota al pie le explica
    cómo verificar esa línea. Y que las tres cifras del papel cuadren entre ellas:

        16/07  A fabrica   219,45 L  Día completo         $150.000,00
        16/07  Napoles     137,45 L × $242,76 = $ 33.367,36
        17/07  A fabrica    96,30 L  Día completo         $150.000,00
                                                          -----------
                                        VALOR TOTAL       $333.367,36
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Henri", "137.45")
    _recibir(client, h, esc, "2026-07-17", "Gilberto", "96.30")
    liq_id = _liquidar_flete(client, h)["id"]
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()

    total = FIJO + D("33367.36") + FIJO
    _, fotos = _fotos_de(db_session, liq_id)
    assert _revisar_invariante(liq, fotos) == total == D("333367.36")

    r = client.get(f"{LIQUIDACIONES}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")
    impreso = texto_pdf(r.content)
    tabla = impreso.split("Detalle diario", 1)[1].split("Resumen de liquidación", 1)[0]

    print("\n===== 9. EL PDF CON LOS DOS MODOS MEZCLADOS =====")
    print(f"  la tabla: {tabla.strip()[:220]}")
    for esperado in [
        "Fecha", "Ruta", "Litros", "Precio/L", "Valor",
        "A fabrica", "Napoles",
        "Día completo",          # la palabra que reemplaza a la tarifa que no existe
        # Los formateadores colombianos recortan los decimales que no aportan: $150.000
        # (y no $150.000,00), pero $33.367,36 sí los lleva.
        "$150.000", "$33.367,36", "$333.367,36",
        "242,76",
    ]:
        assert esperado in impreso, f"el comprobante no imprime {esperado!r}"
        print(f"  imprime {esperado!r}")

    # La columna Precio/L de un día fijo NO trae una tarifa; la del día por litro sí.
    assert tabla.count("Día completo") == 2, "los dos dias fijos van rotulados"
    assert "$0,00" not in tabla, (
        "un dia fijo no imprime $0,00 en Precio/L: imprime 'Dia completo'"
    )
    # Y la nota al pie le dice al conductor cómo se verifica esa línea.
    assert "se cobran POR DÍA y no por litro" in impreso
    print("  la nota al pie explica como verificar la linea")

    # LA PANTALLA suma lo mismo que el papel, renglón por renglón.
    pantalla = sum((D(d["valor"]) for d in liq["detalles"]), D(0))
    print(f"  la pantalla suma ${pantalla} · el papel imprime "
          f"${D(liq['valor_transporte'])}")
    assert pantalla == D(liq["valor_transporte"]) == total

    # Y EL AVANCE ("¿cómo voy?") agrupa igual: mismos renglones, mismos modos.
    esc2 = esc
    otro = _recibir(client, h, esc2, "2026-07-20", "Rosa", "124.20")
    assert D(otro["valor_transporte"]) == FIJO
    pre = client.post(
        f"{LIQUIDACIONES}/previsualizar",
        json={"periodo_inicio": "2026-07-16", "periodo_fin": "2026-07-31",
              "tipo": "transportador", "tercero_id": esc["alex"]["id"]},
        headers=h,
    ).json()[0]
    print(f"  el avance: {[(d['fecha'], d['modo_transporte'], d['valor']) for d in pre['detalles']]}")
    assert [d["modo_transporte"] for d in pre["detalles"]] == ["dia_fijo"]
    assert D(pre["valor_transporte"]) == FIJO
    assert pre["tiene_dias_fijos"] is True
    assert D(pre["precio_promedio"]) == D(0)
    assert sum((D(d["valor"]) for d in pre["detalles"]), D(0)) == D(pre["valor_transporte"])


# ---------------------------------------------------------------------------
# 10. El modo que no viene NO se toca: $150.000 el día no se vuelve $150.000 el litro
# ---------------------------------------------------------------------------
def test_un_put_sin_el_modo_no_convierte_el_fijo_en_una_tarifa_por_litro(
    client, base_datos, db_session
):
    """UN PUT QUE NO HABLA DEL MODO DEJA EL MODO COMO ESTABA. Es plata, y mucha.

    La lista de rutas se REEMPLAZA COMPLETA en cada PUT, así que un cliente que mande la
    fila sin `modo_transporte` —una pantalla vieja, un payload armado a mano, un script—
    estaría cambiándole el modo a la ruta SIN cambiarle la cifra. Y ahí está el desastre,
    con la cuenta hecha:

        "a fábrica" a $150.000 POR DÍA   →   un día de 300 L cuesta $   150.000
        "a fábrica" a $150.000 POR LITRO →   el mismo día cuesta   $45.000.000

    La misma cifra en la columna, la misma pantalla, y $44.850.000 de diferencia. Lo único
    que cambió es una palabra que ese cliente no sabe que existe. Por eso el campo ausente
    significa "no me toque el modo", igual que la lista de rutas ausente significa "no me
    toque las rutas".
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)

    print("\n===== 10. UN PUT SIN EL MODO =====")
    # El PUT de una pantalla que no sabe del modo: manda la ruta y la cifra, nada más.
    r = client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"telefono": "3001234567", "rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(FIJO)},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(NAPOLES)},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    modos = {x["nombre"]: x["modo_transporte"] for x in r.json()["rutas"]}
    print(f"  despues del PUT sin modo: {modos}")
    assert modos["A fabrica"] == "dia_fijo", (
        "el PUT sin modo NO puede convertir el dia fijo en una tarifa por litro"
    )
    assert modos["Napoles"] == "litro", "y la que era por litro sigue por litro"

    # Y la plata lo confirma: el día sigue costando $150.000 y no 300 × $150.000.
    recepcion = _recibir(client, h, esc, EL_DIA, "Aurelio", "300")
    print(f"  un dia de 300 L cuesta ${D(recepcion['valor_transporte'])} "
          f"(el desastre serian ${D(300) * FIJO})")
    assert D(recepcion["valor_transporte"]) == FIJO
    assert D(recepcion["valor_transporte"]) != D(300) * FIJO

    # Cambiar el modo A PROPÓSITO sí se puede: se manda, y entonces sí cambia.
    r = client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [{"ruta_id": esc["fabrica"]["id"],
                         "valor_transporte": "1000", "modo_transporte": "litro"}]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    print(f"  mandando el modo a proposito: "
          f"{[(x['nombre'], x['modo_transporte'], x['valor_transporte']) for x in r.json()['rutas']]}")
    assert r.json()["rutas"][0]["modo_transporte"] == "litro"

    # Y un modo que no existe rebota con un 422 en vez de guardarse: un modo mal escrito
    # se leería como 'litro' y volvería a ser el mismo desastre, pero en silencio.
    malo = client.put(
        f"{TRANSPORTADORES}/{esc['alex']['id']}",
        json={"rutas": [{"ruta_id": esc["fabrica"]["id"],
                         "valor_transporte": "150000", "modo_transporte": "FIJO"}]},
        headers=h,
    )
    print(f"  un modo que no existe ('FIJO'): {malo.status_code}")
    assert malo.status_code == 422, malo.text
