"""NÓMINA: el adelanto que se descuenta en un pago no se queda preso si el pago
se cae.

EL DEFECTO QUE CIERRAN ESTAS PRUEBAS. `PagoEmpleadoService.crear` descuenta los
adelantos pendientes del empleado y los marca con `pago_empleado_id = pago.id`.
Cuando ese pago se borraba, el adelanto se quedaba apuntando a un pago que las
consultas ya no devuelven: dejaba de estar pendiente —`pendientes_empleado` solo
recoge los que tienen `pago_empleado_id` en nulo— y no se le podía descontar nunca
más. De ahí salían las dos puntas del mismo hueco, y las dos con plata de verdad:

  · o se rehacía el pago y salía COMPLETO, sin descontar el adelanto que el
    empleado ya se había llevado en efectivo (plata de menos en la caja);
  · o el dueño, viendo que el adelanto no aparecía descontado, le registraba otro
    igual y se lo cobraba DOS VECES al empleado.

La regla que fijan estas pruebas es una sola: LO QUE SE LE ENTREGÓ AL EMPLEADO EN
ADELANTOS MÁS LO QUE SE LE PAGÓ EN NÓMINA TIENE QUE SER LO QUE SE LE DEBÍA (los
jornales trabajados), pase lo que pase con los pagos por el camino.
"""
import io
import re

from pypdf import PdfReader

from tests.conftest import auth_headers

NOM = "/api/v1/nomina"
ANT = "/api/v1/anticipos"

# El jornal del dueño. 41.833,33 × 12,5 = 522.916,625 EXACTOS: es el número con el
# que se ve si la nómina redondea con la regla de la casa (0,005 SUBE).
JORNAL = "41833.33"


def crear_empleado(client, h, valor_dia=JORNAL, nombre="Aurelio", apellido="Marin"):
    r = client.post(
        "/api/v1/empleados",
        json={"nombre": nombre, "apellido": apellido, "cargo": "Quesero", "valor_dia": valor_dia},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def crear_anticipo(client, h, empleado_id, fecha, valor, obs=None):
    r = client.post(
        ANT,
        json={
            "tipo": "empleado", "empleado_id": empleado_id,
            "fecha": fecha, "valor": valor, "observaciones": obs,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def pagar(client, h, empleado_id, fecha, dias):
    r = client.post(
        NOM,
        json={"empleado_id": empleado_id, "fecha": fecha, "dias_trabajados": dias},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def anticipo(client, h, anticipo_id):
    r = client.get(f"{ANT}/{anticipo_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def a_numero(texto: str) -> float:
    """'$522.916,63' -> 522916.63"""
    return float(texto.replace("$", "").replace(".", "").replace(",", ".").strip())


# ---------------------------------------------------------------- pagar, borrar, repagar
def test_pagar_borrar_y_volver_a_pagar_descuenta_el_adelanto_una_sola_vez(client, base_datos):
    """El mismo adelanto no se le puede descontar dos veces al empleado.

    LAS CIFRAS, a mano. Jornal $41.833,33, adelanto de $242.760,00 el 3 de julio,
    quincena de 12,5 jornales:

        12,5 × $41.833,33 = $522.916,63   bruto
                          - $242.760,00   adelanto
                          ---------------
                            $280.156,63   se le entrega

    Se borra el pago y se vuelve a hacer IGUAL. El segundo pago tiene que salir por
    los mismos $280.156,63 —ni $522.916,63 (el adelanto se habría perdido) ni
    $37.396,63 (se lo habrían descontado dos veces)—. Y el adelanto tiene que
    aparecer descontado en UN solo pago: el que quedó vivo.
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h)
    ant = crear_anticipo(client, h, emp["id"], "2026-07-03", "242760.00", "Adelanto tienda")

    primero = pagar(client, h, emp["id"], "2026-07-15", "12.5")
    assert float(primero["anticipos"]) == 242760.00
    assert float(primero["total"]) == 280156.63
    assert anticipo(client, h, ant["id"])["pago_empleado_id"] == primero["id"]

    assert client.delete(f"{NOM}/{primero['id']}", headers=h).status_code == 204

    # Suelto otra vez: vuelve a estar disponible y se puede corregir
    suelto = anticipo(client, h, ant["id"])
    assert suelto["pago_empleado_id"] is None
    assert suelto["aplicado"] is False
    assert suelto["bloqueado"] is False

    segundo = pagar(client, h, emp["id"], "2026-07-15", "12.5")
    assert float(segundo["anticipos"]) == 242760.00, "el adelanto se perdió al borrar el pago"
    assert float(segundo["total"]) == 280156.63

    # Y una tercera quincena YA NO lo descuenta: solo se descuenta una vez
    tercero = pagar(client, h, emp["id"], "2026-07-31", "12.5")
    assert float(tercero["anticipos"]) == 0.00, "el mismo adelanto se descontó dos veces"
    assert float(tercero["total"]) == 522916.63

    assert anticipo(client, h, ant["id"])["pago_empleado_id"] == segundo["id"]


# ---------------------------------------------------------------- dos adelantos, uno cabe
def test_dos_adelantos_y_solo_uno_cabe_en_la_quincena(client, base_datos):
    """Se descuenta el MÁS VIEJO primero, y el que no cabe queda para la siguiente.

    LAS CIFRAS. Jornal $41.833,33, quincena de 5 jornales:

        5 × $41.833,33 = $209.166,65   bruto

    Dos adelantos vivos: $180.000,00 del 3 de julio y $150.000,00 del 8 de julio.
    Juntos son $330.000,00 y no caben en $209.166,65. Entra el del 3 (el más
    viejo) y queda $209.166,65 - $180.000,00 = $29.166,65 para el empleado; el del
    8 se lo descuenta la quincena siguiente.

    EL ORDEN NO ES UN DETALLE: los adelantos se registran acá A PROPÓSITO al revés
    (primero el del 8, después el del 3), porque `pendientes_empleado` no trae
    ORDER BY y el orden que ponga la base no es el mismo en SQLite que en Postgres.
    Si la nómina se fiara de ese orden, en producción podría descontar el del 8 y
    dejar colgado el del 3, y el empleado recibiría $59.166,65 en vez de
    $29.166,65: la misma quincena pagaría distinto según la base.
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h)
    nuevo = crear_anticipo(client, h, emp["id"], "2026-07-08", "150000.00", "El más nuevo")
    viejo = crear_anticipo(client, h, emp["id"], "2026-07-03", "180000.00", "El más viejo")

    quincena_1 = pagar(client, h, emp["id"], "2026-07-15", "5")
    assert float(quincena_1["anticipos"]) == 180000.00, "no se descontó el adelanto más viejo"
    assert float(quincena_1["total"]) == 29166.65  # 209.166,65 - 180.000,00
    assert anticipo(client, h, viejo["id"])["pago_empleado_id"] == quincena_1["id"]
    assert anticipo(client, h, nuevo["id"])["pago_empleado_id"] is None

    quincena_2 = pagar(client, h, emp["id"], "2026-07-31", "5")
    assert float(quincena_2["anticipos"]) == 150000.00
    assert float(quincena_2["total"]) == 59166.65  # 209.166,65 - 150.000,00

    # Y al borrar la primera, el que vuelve a quedar suelto es SOLO el suyo
    assert client.delete(f"{NOM}/{quincena_1['id']}", headers=h).status_code == 204
    assert anticipo(client, h, viejo["id"])["pago_empleado_id"] is None
    assert anticipo(client, h, nuevo["id"])["pago_empleado_id"] == quincena_2["id"]


# ------------------------------------------------------------- borrar el adelanto aplicado
def test_borrar_el_adelanto_ya_descontado_no_descuadra_el_pago(client, base_datos):
    """El camino inverso: mientras el adelanto esté descontado en un pago, no se toca.

    LAS CIFRAS. Jornal $50.000, 5 jornales = $250.000; adelanto de $80.000, así que
    al empleado se le entregaron $170.000. Si se pudiera borrar (o corregir) ese
    adelanto sin más, el pago se quedaría diciendo "anticipos $80.000" contra un
    adelanto que ya no existe y su desglose dejaría de sumar: $250.000 - $0 no da
    los $170.000 que están impresos en el recibo que firmó el empleado.

    La salida es la misma que en liquidaciones: primero se borra el pago —lo que
    SUELTA el adelanto— y ahí sí se corrige o se borra el adelanto.
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="50000")
    ant = crear_anticipo(client, h, emp["id"], "2026-07-01", "80000.00")
    pago = pagar(client, h, emp["id"], "2026-07-15", "5")
    assert float(pago["anticipos"]) == 80000.00
    assert float(pago["total"]) == 170000.00

    trabado = anticipo(client, h, ant["id"])
    assert trabado["bloqueado"] is True

    borrar = client.delete(f"{ANT}/{ant['id']}", headers=h)
    assert borrar.status_code == 422, borrar.text
    assert "nómina" in borrar.json()["error"]["detail"]
    corregir = client.put(f"{ANT}/{ant['id']}", json={"valor": "30000.00"}, headers=h)
    assert corregir.status_code == 422, corregir.text

    # El pago quedó intacto y su desglose sigue sumando exacto
    vivo = client.get(f"{NOM}?empleado_id={emp['id']}", headers=h).json()["items"][0]
    assert float(vivo["dias_trabajados"]) * float(vivo["valor_dia"]) - float(vivo["anticipos"]) == (
        float(vivo["total"])
    )
    assert float(vivo["total"]) == 170000.00

    # Borrado el pago, el adelanto se suelta y ahora sí se deja borrar
    assert client.delete(f"{NOM}/{pago['id']}", headers=h).status_code == 204
    assert anticipo(client, h, ant["id"])["bloqueado"] is False
    assert client.delete(f"{ANT}/{ant['id']}", headers=h).status_code == 204

    # Y el pago que se rehaga ya no descuenta nada: ese adelanto no existió
    rehecho = pagar(client, h, emp["id"], "2026-07-15", "5")
    assert float(rehecho["anticipos"]) == 0.00
    assert float(rehecho["total"]) == 250000.00


# ------------------------------------------------------------------------- la cuenta cierra
def test_la_cuenta_del_empleado_cierra_despues_de_borrar_y_rehacer(client, base_datos):
    """LO QUE SE LE ENTREGÓ EN ADELANTOS + LO QUE SE LE PAGÓ EN NÓMINA = LO QUE SE LE DEBÍA.

    Es la cuenta que el dueño hace a mano al final del mes, y tiene que cerrar aunque
    por el camino se haya borrado y rehecho un pago.

    LAS CIFRAS, jornal $41.833,33 y dos quincenas de 5 jornales:

        adelanto del 03/07 ........................  $180.000,00
        adelanto del 08/07 ........................  $150.000,00
        quincena 1 (5 jornales, menos el del 03) ..   $29.166,65
        quincena 2 (5 jornales, menos el del 08) ..   $59.166,65
        --------------------------------------------------------
        entregado al empleado .....................  $418.333,30

        10 jornales × $41.833,33 ..................  $418.333,30   IGUAL

    En el medio la quincena 1 se borra por equivocada y se vuelve a hacer idéntica.
    Si el adelanto del 03/07 se quedara preso, la quincena rehecha saldría por
    $209.166,65 y el entregado subiría a $598.333,30: $180.000,00 de más, que es
    exactamente el adelanto que el empleado ya se había llevado en efectivo.
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h)
    crear_anticipo(client, h, emp["id"], "2026-07-03", "180000.00")
    crear_anticipo(client, h, emp["id"], "2026-07-08", "150000.00")

    quincena_1 = pagar(client, h, emp["id"], "2026-07-15", "5")
    assert client.delete(f"{NOM}/{quincena_1['id']}", headers=h).status_code == 204
    quincena_1 = pagar(client, h, emp["id"], "2026-07-15", "5")
    quincena_2 = pagar(client, h, emp["id"], "2026-07-31", "5")

    en_adelantos = 180000.00 + 150000.00
    en_nomina = float(quincena_1["total"]) + float(quincena_2["total"])
    se_le_debia = round(10 * 41833.33, 2)

    assert en_adelantos + en_nomina == se_le_debia, (
        f"LA CUENTA NO CIERRA: adelantos ${en_adelantos:,.2f} + nómina "
        f"${en_nomina:,.2f} = ${en_adelantos + en_nomina:,.2f}, y se le debían "
        f"${se_le_debia:,.2f}"
    )
    assert se_le_debia == 418333.30

    # Cada adelanto quedó descontado en UNA sola quincena, y en la que le tocaba
    descontado = float(quincena_1["anticipos"]) + float(quincena_2["anticipos"])
    assert descontado == en_adelantos
    assert float(quincena_1["anticipos"]) == 180000.00
    assert float(quincena_2["anticipos"]) == 150000.00


# ------------------------------------------------------------------ el papel del pago rehecho
def test_el_recibo_del_pago_rehecho_sigue_cuadrando(client, base_datos):
    """El recibo del pago rehecho trae el adelanto UNA vez y su desglose suma exacto.

    Cifras: 12,5 jornales de $41.833,33 = $522.916,63, menos el adelanto de
    $242.760,00 del 3 de julio = $280.156,63. Es el papel que firma el empleado, y
    el dueño le hace la resta a mano.
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h)
    crear_anticipo(client, h, emp["id"], "2026-07-03", "242760.00", "Adelanto tienda")

    primero = pagar(client, h, emp["id"], "2026-07-15", "12.5")
    assert client.delete(f"{NOM}/{primero['id']}", headers=h).status_code == 204
    rehecho = pagar(client, h, emp["id"], "2026-07-15", "12.5")

    res = client.get(f"{NOM}/{rehecho['id']}/pdf", headers=h)
    assert res.status_code == 200
    texto = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(res.content)).pages)

    bruto = a_numero(re.search(r"Subtotal devengado\s*(\$[\d\.,]+)", texto).group(1))
    desc = a_numero(re.search(r"Descuento anticipos\s*-\s*(\$[\d\.,]+)", texto).group(1))
    total = a_numero(re.search(r"TOTAL PAGADO\s*(\$[\d\.,]+)", texto).group(1))
    assert (bruto, desc, total) == (522916.63, 242760.00, 280156.63)
    assert round(bruto - desc, 2) == total, "el recibo del pago rehecho no cuadra"

    # Y el adelanto aparece UNA sola vez en el desglose del recibo
    filas = [a_numero(m) for m in re.findall(r"03/07/2026\s*(\$[\d\.,]+)", texto)]
    assert filas == [242760.00], filas


# ------------------------------------------------------------------- borrar el empleado
def test_borrar_el_empleado_no_deja_adelantos_presos(client, base_datos):
    """Borrar al empleado NO hace desaparecer sus pagos, así que nada queda preso.

    `PagoEmpleadoRepository` filtra por el `deleted_at` del PAGO, no por el del
    empleado, y la relación carga al empleado aunque esté borrado: el pago se sigue
    listando con su nombre y su recibo se sigue bajando. O sea que el adelanto sigue
    descontado en un pago que SÍ existe.

    Y si después se borra ese pago, el adelanto se suelta igual —el camino de
    soltarlo no depende del empleado—. Cifras: jornal $50.000, 5 jornales
    $250.000, adelanto $80.000, entregado $170.000.
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="50000")
    aplicado = crear_anticipo(client, h, emp["id"], "2026-07-01", "80000.00")
    pendiente = crear_anticipo(client, h, emp["id"], "2026-07-20", "500000.00")
    pago = pagar(client, h, emp["id"], "2026-07-15", "5")
    assert float(pago["total"]) == 170000.00

    assert client.delete(f"/api/v1/empleados/{emp['id']}", headers=h).status_code == 204

    # El pago sobrevive al empleado, con su nombre y su recibo
    lista = client.get(f"{NOM}?empleado_id={emp['id']}", headers=h).json()
    assert lista["total"] == 1
    assert lista["items"][0]["empleado_nombre"] == "Aurelio Marin"
    assert client.get(f"{NOM}/{pago['id']}/pdf", headers=h).status_code == 200

    # El aplicado sigue aplicado a un pago vivo; el pendiente sigue suelto y
    # visible (no se pierde de vista una plata que ya salió de la caja)
    assert anticipo(client, h, aplicado["id"])["pago_empleado_id"] == pago["id"]
    libre = anticipo(client, h, pendiente["id"])
    assert libre["pago_empleado_id"] is None
    assert libre["bloqueado"] is False
    assert client.delete(f"{ANT}/{pendiente['id']}", headers=h).status_code == 204

    # Y borrar el pago del empleado borrado suelta su adelanto igual
    assert client.delete(f"{NOM}/{pago['id']}", headers=h).status_code == 204
    assert anticipo(client, h, aplicado["id"])["pago_empleado_id"] is None
    assert client.delete(f"{ANT}/{aplicado['id']}", headers=h).status_code == 204


# --------------------------------------------------------------------- las puertas
def test_no_hay_forma_de_anular_ni_de_editar_un_pago_de_nomina(client, base_datos):
    """FIJA QUE NO EXISTEN esos caminos, para que el día que se abran no pasen callados.

    Un pago de nómina no tiene estados (no hay borrador ni aprobado: existe = ya se
    le pagó al empleado), así que hoy la ÚNICA forma de dar de baja un pago es
    borrarlo, y ese camino ya suelta los adelantos.

    Si mañana alguien agrega un PUT (para corregir los días) o un `anular`, esta
    prueba falla y dice con nombre y apellido lo que hay que hacer: por ese camino
    también hay que soltar los adelantos —y, si se corrigen los días, volver a
    cuadrar `anticipos` y `total`, o el desglose del recibo deja de sumar—. Es la
    misma decisión que en liquidaciones, donde el 405 del DELETE está fijado en
    tests/test_liquidacion_deuda_arrastrada_puertas.py.
    """
    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="50000")
    pago = pagar(client, h, emp["id"], "2026-07-15", "5")

    assert client.put(f"{NOM}/{pago['id']}", json={"dias_trabajados": "3"},
                      headers=h).status_code == 405
    assert client.patch(f"{NOM}/{pago['id']}", json={"dias_trabajados": "3"},
                        headers=h).status_code == 405
    assert client.post(f"{NOM}/{pago['id']}/anular", headers=h).status_code == 404


def test_soltar_los_adelantos_queda_escrito_en_la_bitacora(client, base_datos, db_session):
    """La bitácora dice cuáles adelantos se soltaron y por cuánta plata.

    El dueño abre la bitácora cuando una cifra no le cuadra, y "el pago se borró"
    a secas no explica por qué el adelanto de $80.000 volvió a aparecer pendiente.
    El renglón va contra el PAGO (no contra cada anticipo) porque `_audit` escribe
    en `entidad` el modelo de este servicio: auditar uno por uno dejaría filas que
    dicen "PagoEmpleado" con el id de un anticipo.
    """
    import uuid as _uuid

    from app.modules.auditoria.models import Auditoria

    h = auth_headers(client, "admin.a")
    emp = crear_empleado(client, h, valor_dia="50000")
    ant = crear_anticipo(client, h, emp["id"], "2026-07-01", "80000.00")
    pago = pagar(client, h, emp["id"], "2026-07-15", "5")
    assert client.delete(f"{NOM}/{pago['id']}", headers=h).status_code == 204

    filas = [
        a
        for a in db_session.query(Auditoria)
        .filter(Auditoria.entidad_id == _uuid.UUID(pago["id"]))
        .all()
        if (a.despues or {}).get("anticipos_soltados")
    ]
    assert len(filas) == 1, "no quedó el renglón que explica el adelanto soltado"
    fila = filas[0]
    assert fila.entidad == "PagoEmpleado"
    assert fila.despues["total_soltado"] == 80000.00
    assert [x["id"] for x in fila.despues["anticipos_soltados"]] == [ant["id"]]
    assert "vuelve a quedar pendiente" in fila.despues["motivo"]


def test_borrar_un_pago_de_una_quesera_no_suelta_los_adelantos_de_la_otra(client, base_datos):
    """Multiempresa: soltar los adelantos también filtra por empresa.

    Las dos queseras viven en la misma instalación y las dos tienen un Aurelio con
    un adelanto de $80.000 descontado en su quincena. Borrar el pago de la Quesera A
    no puede tocar el adelanto de la Quesera B: si lo soltara, a ese empleado se le
    volvería a descontar en su próxima quincena y le cobrarían $80.000 dos veces.
    """
    ha = auth_headers(client, "admin.a")
    hb = auth_headers(client, "admin.b")

    emp_a = crear_empleado(client, ha, valor_dia="50000")
    ant_a = crear_anticipo(client, ha, emp_a["id"], "2026-07-01", "80000.00")
    pago_a = pagar(client, ha, emp_a["id"], "2026-07-15", "5")

    emp_b = crear_empleado(client, hb, valor_dia="50000")
    ant_b = crear_anticipo(client, hb, emp_b["id"], "2026-07-01", "80000.00")
    pago_b = pagar(client, hb, emp_b["id"], "2026-07-15", "5")

    # La Quesera B no puede borrar el pago de la A
    assert client.delete(f"{NOM}/{pago_a['id']}", headers=hb).status_code == 404

    assert client.delete(f"{NOM}/{pago_a['id']}", headers=ha).status_code == 204
    assert anticipo(client, ha, ant_a["id"])["pago_empleado_id"] is None
    assert anticipo(client, hb, ant_b["id"])["pago_empleado_id"] == pago_b["id"]

    # Y la quincena de la B sigue entera: $250.000 - $80.000 = $170.000
    viva = client.get(f"{NOM}?empleado_id={emp_b['id']}", headers=hb).json()["items"][0]
    assert float(viva["anticipos"]) == 80000.00
    assert float(viva["total"]) == 170000.00
