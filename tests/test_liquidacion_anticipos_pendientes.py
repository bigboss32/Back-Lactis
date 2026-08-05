"""El anticipo que se registra DESPUÉS de generar la liquidación se quedaba por fuera.

Sale de un caso real, con plata de verdad: el dueño tenía a Henri con 644 L en la
quincena que cierra el 31/07, un anticipo de $500.000 con fecha 31/07 en estado
PENDIENTE, y la liquidación en BORRADOR mostrando "Anticipos aplicados $0" y un
saldo a pagar de $1.159.200 completo. Al proveedor de arriba, con un anticipo del
MISMO día, sí se le había aplicado.

Lo que pasaba de verdad (y lo que se descarta aquí, hipótesis por hipótesis):

  · NO era el rango de fechas: `AnticipoRepository.pendientes_de` compara con
    `fecha <= hasta`, así que un anticipo del último día del período sí entra.
    Se deja probado abajo para que nadie lo vuelva a cambiar a `<`.
  · NO era el nombre: el anticipo apunta al proveedor por id (llave foránea),
    así que tener un "Henri C" además de "Henri" no confunde a nadie.
  · ERA EL ORDEN DE LOS HECHOS: los anticipos se aplicaban UNA sola vez, en el
    instante de generar la liquidación. Si el anticipo se registraba después, el
    borrador quedaba en $0 para siempre: volver a darle a "Generar" no hacía nada
    (las recepciones ya tenían liquidación) y no había forma de recogerlo.

El arreglo: un borrador se puede RECALCULAR, y al APROBARLO recoge los anticipos
pendientes que le corresponden. Lo que ya está aprobado o pagado no se toca, y un
anticipo no se puede contar dos veces.
"""
from decimal import Decimal

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"

# El período tal cual el del caso: la quincena/mes que cierra el 31 de julio
INICIO = "2026-07-16"
FIN = "2026-07-31"


def D(v):
    return Decimal(str(v))


def _proveedor(client, h, nombre, precio="1800"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, proveedor, fecha, litros):
    r = client.post(
        "/api/v1/recepciones",
        json={"fecha": fecha, "proveedor_id": proveedor["id"], "cantidad_litros": str(litros)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, proveedor, fecha, valor):
    r = client.post(
        "/api/v1/anticipos",
        json={
            "tipo": "proveedor",
            "proveedor_id": proveedor["id"],
            "fecha": fecha,
            "valor": str(valor),
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, inicio=INICIO, fin=FIN, tipo="proveedor"):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _henri_con_sus_644_litros(client, h, nombre="Henri"):
    """Henri, con las recepciones del período: 200 + 200 + 244 = 644 L a $1.800."""
    henri = _proveedor(client, h, nombre)
    for fecha, litros in (("2026-07-16", "200"), ("2026-07-24", "200"), ("2026-07-31", "244")):
        _recepcion(client, h, henri, fecha, litros)
    return henri


# ---------------------------------------------------------------------------
# Hipótesis 1: ¿el filtro se come el último día del período? NO.
# ---------------------------------------------------------------------------
def test_el_anticipo_del_ultimo_dia_del_periodo_si_entra_al_generar(client, base_datos):
    """Un anticipo fechado el 31/07 tiene que entrar en la liquidación que va
    HASTA el 31/07. Si alguien cambia el `<=` por `<` en el repositorio, esta
    prueba lo agarra: el dueño perdería el anticipo del día de cierre, que es
    justo cuando más se registran.
    """
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)
    _anticipo(client, h, henri, FIN, "500000")  # mismo día que cierra el período

    liq = _generar(client, h)[0]

    print("\n===== HIPÓTESIS 1: EL ANTICIPO DEL DÍA DE CIERRE =====")
    print(f"  período            : {INICIO} al {FIN}")
    print(f"  anticipo fechado   : {FIN}")
    print(f"  anticipos aplicados: {liq['anticipos']}")
    assert D(liq["anticipos"]) == D("500000"), "el filtro de fechas se comió el día de cierre"
    assert D(liq["saldo"]) == D("1159200") - D("500000")


# ---------------------------------------------------------------------------
# Hipótesis 3: ¿el vínculo es por nombre? NO, es por id.
# ---------------------------------------------------------------------------
def test_el_anticipo_de_henri_c_no_se_mezcla_con_el_de_henri(client, base_datos):
    """En los datos del cliente hay un "Henri C" además de "Henri". Si el anticipo
    se vinculara por texto, el de uno le caería al otro. Va por llave foránea, y
    esta prueba lo deja fijo.
    """
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h, nombre="Henri")
    henri_c = _proveedor(client, h, "Henri C")
    _recepcion(client, h, henri_c, "2026-07-20", "100")
    _anticipo(client, h, henri_c, FIN, "300000")  # el anticipo es de Henri C, NO de Henri

    liqs = {liq["proveedor_id"]: liq for liq in _generar(client, h)}
    liq_henri = liqs[henri["id"]]
    liq_henri_c = liqs[henri_c["id"]]

    print("\n===== HIPÓTESIS 3: 'HENRI' CONTRA 'HENRI C' =====")
    print(f"  Henri   · anticipos: {liq_henri['anticipos']}")
    print(f"  Henri C · anticipos: {liq_henri_c['anticipos']}")
    assert D(liq_henri["anticipos"]) == D("0")
    assert D(liq_henri_c["anticipos"]) == D("300000")


# ---------------------------------------------------------------------------
# LA CAUSA: el anticipo se registró DESPUÉS de generar la liquidación
# ---------------------------------------------------------------------------
def test_caso_henri_el_anticipo_registrado_despues_se_recoge_al_recalcular(client, base_datos):
    """El caso de Henri, tal cual: primero se generó la liquidación y después se
    registró el anticipo. El borrador quedaba en $0 y no había forma de recogerlo:
    volver a "Generar" no hace nada porque las recepciones ya están apartadas.

    Un borrador es un borrador: tiene que poder recalcularse y recoger lo que el
    proveedor debe. Las cifras del resumen tienen que quedar cuadrando exacto:
    valor total - anticipos = saldo a pagar.
    """
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)

    # 1) Se genera la liquidación ANTES de que exista el anticipo
    liq = _generar(client, h)[0]
    print("\n===== LA CAUSA: EL ANTICIPO LLEGÓ DESPUÉS =====")
    print(f"  al generar · litros: {liq['total_litros']} · bruto: {liq['valor_bruto']} · "
          f"anticipos: {liq['anticipos']} · saldo: {liq['saldo']}")
    assert liq["estado"] == "borrador"
    assert D(liq["total_litros"]) == D("644")
    assert D(liq["valor_bruto"]) == D("1159200")
    assert D(liq["anticipos"]) == D("0")

    # 2) Ahora sí se registra el anticipo del 31/07 por $500.000
    ant = _anticipo(client, h, henri, FIN, "500000")
    print(f"  anticipo registrado · fecha: {ant['fecha']} · valor: {ant['valor']} · "
          f"aplicado: {ant['aplicado']}")
    assert ant["aplicado"] is False

    # 3) Darle otra vez a "Generar" no arregla nada: las recepciones ya están apartadas
    assert _generar(client, h) == []
    sigue_en_cero = client.get(f"{API}/{liq['id']}", headers=h).json()
    print(f"  volver a generar   · anticipos: {sigue_en_cero['anticipos']} (no se mueve)")
    assert D(sigue_en_cero["anticipos"]) == D("0")

    # 4) Recalcular el borrador SÍ tiene que recogerlo
    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    assert r.status_code == 200, r.text
    recalculada = r.json()
    print(f"  al recalcular      · anticipos: {recalculada['anticipos']} · "
          f"saldo: {recalculada['saldo']}")

    assert D(recalculada["anticipos"]) == D("500000")
    assert D(recalculada["valor_total"]) == D("1159200")
    # El cuadre que el dueño hace a mano contra el cuaderno
    assert D(recalculada["saldo"]) == D(recalculada["valor_total"]) - D(recalculada["anticipos"])
    assert D(recalculada["saldo"]) == D("659200")
    # Y la suma de los días sigue dando el valor total
    assert sum(D(d["valor"]) for d in recalculada["detalles"]) == D(recalculada["valor_total"])

    # 5) Quedó guardado, no en memoria, y el anticipo ya figura como aplicado
    guardada = client.get(f"{API}/{liq['id']}", headers=h).json()
    assert D(guardada["anticipos"]) == D("500000")
    anticipo = client.get(f"/api/v1/anticipos/{ant['id']}", headers=h).json()
    print(f"  el anticipo quedó  · aplicado: {anticipo['aplicado']}")
    assert anticipo["aplicado"] is True
    assert anticipo["liquidacion_id"] == liq["id"]


def test_aprobar_un_borrador_recoge_el_anticipo_que_habia_quedado_pendiente(client, base_datos):
    """Aprobar es el último momento antes de pagar. Si el dueño no se dio cuenta
    del anticipo pendiente, aprobar no puede dejarlo pasar: se le pagaría al
    proveedor plata que ya se le había adelantado.
    """
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)
    liq = _generar(client, h)[0]
    _anticipo(client, h, henri, FIN, "500000")

    r = client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    assert r.status_code == 200, r.text
    aprobada = r.json()
    print("\n===== APROBAR RECOGE EL ANTICIPO PENDIENTE =====")
    print(f"  aprobada · anticipos: {aprobada['anticipos']} · saldo: {aprobada['saldo']}")
    assert aprobada["estado"] == "aprobada"
    assert D(aprobada["anticipos"]) == D("500000")
    assert D(aprobada["saldo"]) == D("659200")


# ---------------------------------------------------------------------------
# Un anticipo no se puede descontar dos veces
# ---------------------------------------------------------------------------
def test_un_anticipo_ya_aplicado_en_otra_liquidacion_no_se_cuenta_dos_veces(client, base_datos):
    """Lo más caro que podría hacer este arreglo: recoger en la quincena de julio
    un anticipo que ya se le había descontado en la de junio. Al proveedor le
    quitarían dos veces la misma plata.

    ESTA PRUEBA CAMBIÓ UNA CIFRA cuando la deuda empezó a cobrarse en la quincena
    siguiente (ver tests/test_liquidacion_saldo_anterior.py). Las cuentas, a mano:

        JUNIO: 100 L a $1.800 = $180.000 con un anticipo de $200.000 ya entregado
            -> saldo -$20.000: Henri le quedó DEBIENDO $20.000 a la quesera
        JULIO: 644 L a $1.800 = $1.159.200, sin anticipos nuevos
            - lo que quedó debiendo de junio                  -$20.000
            saldo a pagar                                  $1.139.200

    Antes esta prueba esperaba $1.159.200 —los $20.000 de junio se quedaban escritos en
    un papel viejo y nadie los cobraba—. Y la cuenta grande cuadra exacto: $180.000 +
    $1.159.200 = $1.339.200 de leche; $200.000 de anticipo + $1.139.200 de julio =
    $1.339.200 entregados. Ni un peso de más ni de menos.

    LO QUE LA PRUEBA SIGUE VIGILANDO, que es lo suyo: el ANTICIPO de junio no vuelve a
    aparecer en julio (`anticipos` en cero). Lo que viaja es la DEUDA que dejó, que son
    $20.000 y no $200.000.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri")

    # Junio: 100 L y un anticipo de $200.000 que SÍ se aplica al generar
    _recepcion(client, h, henri, "2026-06-10", "100")
    _anticipo(client, h, henri, "2026-06-10", "200000")
    liq_junio = _generar(client, h, "2026-06-01", "2026-06-30")[0]
    assert D(liq_junio["anticipos"]) == D("200000")
    assert D(liq_junio["le_queda_debiendo"]) == D("20000")

    # Julio: sus 644 L, sin anticipo nuevo
    for fecha, litros in (("2026-07-16", "200"), ("2026-07-24", "200"), ("2026-07-31", "244")):
        _recepcion(client, h, henri, fecha, litros)
    liq_julio = _generar(client, h)[0]

    recalculada = client.post(f"{API}/{liq_julio['id']}/recalcular", headers=h).json()
    print("\n===== EL ANTICIPO DE JUNIO NO VUELVE A APARECER EN JULIO =====")
    print(f"  junio · anticipos: {liq_junio['anticipos']} · le queda debiendo: "
          f"{liq_junio['le_queda_debiendo']}")
    print(f"  julio · anticipos: {recalculada['anticipos']} (tiene que ser 0) · "
          f"deuda de junio cobrada: {recalculada['saldo_anterior']}")
    assert D(recalculada["anticipos"]) == D("0"), "el anticipo de junio se descontó dos veces"
    assert D(recalculada["saldo_anterior"]) == D("20000"), (
        "la deuda que Henri dejó en junio no se le cobró en julio: esos $20.000 son un "
        "anticipo que ya salió de la caja"
    )
    assert D(recalculada["saldo"]) == D("1139200")

    # Y la de junio quedó intacta (su anticipo sigue siendo el suyo)
    junio = client.get(f"{API}/{liq_junio['id']}", headers=h).json()
    assert D(junio["anticipos"]) == D("200000")
    # La cuenta grande: leche entregada = plata entregada.
    print(f"  leche = $180.000 + $1.159.200 = $1.339.200 · entregado = $200.000 de "
          f"anticipo + {recalculada['saldo']} de julio")
    assert D("180000") + D("1159200") == D("200000") + D(recalculada["saldo"])


def test_recalcular_dos_veces_seguidas_no_duplica_el_anticipo(client, base_datos):
    """El botón se puede oprimir dos veces (o el navegador reintenta). Recalcular
    tiene que dar siempre el mismo resultado: el total se vuelve a sumar desde los
    anticipos que hoy apuntan a esta liquidación, no se le suma encima al guardado.
    """
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)
    liq = _generar(client, h)[0]
    _anticipo(client, h, henri, FIN, "500000")

    primera = client.post(f"{API}/{liq['id']}/recalcular", headers=h).json()
    segunda = client.post(f"{API}/{liq['id']}/recalcular", headers=h).json()
    tercera = client.post(f"{API}/{liq['id']}/recalcular", headers=h).json()

    print("\n===== RECALCULAR ES IDEMPOTENTE =====")
    for i, liq_i in enumerate((primera, segunda, tercera), start=1):
        print(f"  vez {i} · anticipos: {liq_i['anticipos']} · saldo: {liq_i['saldo']}")
    assert D(primera["anticipos"]) == D(segunda["anticipos"]) == D(tercera["anticipos"]) == D("500000")
    assert D(tercera["saldo"]) == D("659200")


def test_aprobar_despues_de_recalcular_no_vuelve_a_descontar(client, base_datos):
    """Recalcular y luego aprobar es la secuencia normal del dueño. El anticipo ya
    quedó marcado contra esta liquidación en el recálculo; aprobar no puede
    sumárselo otra vez.
    """
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)
    liq = _generar(client, h)[0]
    _anticipo(client, h, henri, FIN, "500000")

    client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    aprobada = client.post(f"{API}/{liq['id']}/aprobar", headers=h).json()
    print("\n===== RECALCULAR Y DESPUÉS APROBAR =====")
    print(f"  aprobada · anticipos: {aprobada['anticipos']} · saldo: {aprobada['saldo']}")
    assert D(aprobada["anticipos"]) == D("500000")
    assert D(aprobada["saldo"]) == D("659200")


# ---------------------------------------------------------------------------
# Lo que ya se pagó no se toca
# ---------------------------------------------------------------------------
def test_una_liquidacion_aprobada_o_pagada_no_se_recalcula(client, base_datos):
    """Aprobada quiere decir que esa cifra ya se le dio a alguien (o está por
    darse). Recalcularla cambiaría un pago que ya salió: el guardia va en el
    backend, no en la pantalla.
    """
    h = auth_headers(client, "admin.a")
    henri = _henri_con_sus_644_litros(client, h)
    liq = _generar(client, h)[0]
    client.post(f"{API}/{liq['id']}/aprobar", headers=h)
    # El anticipo llega cuando la liquidación ya está aprobada
    _anticipo(client, h, henri, FIN, "500000")

    print("\n===== APROBADA Y PAGADA NO SE RECALCULAN =====")
    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    print(f"  aprobada · recalcular: {r.status_code} · "
          f"{r.json().get('error', {}).get('detail', '')}")
    assert r.status_code == 422
    assert "borrador" in r.json()["error"]["detail"]

    client.post(f"{API}/{liq['id']}/pagar", headers=h)
    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h)
    print(f"  pagada   · recalcular: {r.status_code}")
    assert r.status_code == 422

    intacta = client.get(f"{API}/{liq['id']}", headers=h).json()
    print(f"  la liquidación sigue en · anticipos: {intacta['anticipos']} · "
          f"neto a pagar: {intacta['neto_a_pagar']} · pagado: {intacta['pagado']} · "
          f"saldo: {intacta['saldo']}")
    assert D(intacta["anticipos"]) == D("0"), "el anticipo tardío NO se le podía aplicar"
    # Con los pagos parciales, `saldo` es lo que TODAVÍA se debe: al pagarla
    # completa queda en cero y lo que vale $1.159.200 es el neto a pagar, que ya
    # está entregado. Antes esta prueba miraba `saldo` porque era la única cifra
    # que había y no se movía con el pago.
    assert D(intacta["neto_a_pagar"]) == D("1159200")
    assert D(intacta["pagado"]) == D("1159200")
    assert D(intacta["saldo"]) == D("0")


def test_recalcular_no_cruza_empresas(client, base_datos):
    """Multiempresa por fila: el admin de la Quesera B no recalcula (ni ve) una
    liquidación de la Quesera A ni teniendo el id a la mano."""
    h_a = auth_headers(client, "admin.a")
    h_b = auth_headers(client, "admin.b")
    henri = _henri_con_sus_644_litros(client, h_a)
    liq = _generar(client, h_a)[0]
    _anticipo(client, h_a, henri, FIN, "500000")

    r = client.post(f"{API}/{liq['id']}/recalcular", headers=h_b)
    print("\n===== OTRA EMPRESA =====")
    print(f"  admin.b contra la liquidación de la empresa A: {r.status_code}")
    assert r.status_code == 404

    intacta = client.get(f"{API}/{liq['id']}", headers=h_a).json()
    assert D(intacta["anticipos"]) == D("0")


# ---------------------------------------------------------------------------
# El transportador va por el mismo camino
# ---------------------------------------------------------------------------
def test_el_borrador_del_transportador_tambien_recoge_su_anticipo(client, base_datos):
    """Al transportador le pasa lo mismo: se le adelanta la gasolina y el anticipo
    se registra después de generar la liquidación del flete."""
    h = auth_headers(client, "admin.a")
    ruta = client.post(
        "/api/v1/rutas", json={"nombre": "Ruta Granada", "municipio": "Granada"}, headers=h
    ).json()
    transportador = client.post(
        "/api/v1/transportadores",
        json={"nombre": "Stella", "valor_transporte": "100",
              "rutas": [{"ruta_id": ruta["id"], "valor_transporte": "100"}]},
        headers=h,
    ).json()
    proveedor = _proveedor(client, h, "Libardo")
    client.post(
        "/api/v1/recepciones",
        json={
            "fecha": "2026-07-20", "proveedor_id": proveedor["id"],
            "transportador_id": transportador["id"], "cantidad_litros": "500",
        },
        headers=h,
    )
    liqs = _generar(client, h, tipo="ambos")
    liq_t = {x["tipo"]: x for x in liqs}["transportador"]
    assert D(liq_t["valor_total"]) == D("50000")
    assert D(liq_t["anticipos"]) == D("0")

    client.post(
        "/api/v1/anticipos",
        json={
            "tipo": "transportador", "transportador_id": transportador["id"],
            "fecha": FIN, "valor": "20000",
        },
        headers=h,
    )
    recalculada = client.post(f"{API}/{liq_t['id']}/recalcular", headers=h).json()
    print("\n===== TRANSPORTADOR =====")
    print(f"  flete: {recalculada['valor_total']} · anticipos: {recalculada['anticipos']} · "
          f"saldo: {recalculada['saldo']}")
    assert D(recalculada["anticipos"]) == D("20000")
    assert D(recalculada["saldo"]) == D("30000")
    # El detalle del flete no se movió al recalcular
    assert D(recalculada["total_litros"]) == D("500")
    assert D(recalculada["valor_total"]) == D("50000")

    # Y el comprobante sale: antes, el del transportador no traía el renglón de
    # anticipos y mostraba VALOR TOTAL $50.000 con SALDO A PAGAR $30.000 sin
    # explicar de dónde salían los $20.000 de diferencia.
    pdf = client.get(f"{API}/{liq_t['id']}/pdf", headers=h)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
