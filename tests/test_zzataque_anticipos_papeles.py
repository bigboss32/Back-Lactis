"""ATAQUE 5 — EL PAPEL Y EL RASTRO CUANDO SE MUEVE UN ADELANTO.

Corregirle los ANTICIPOS a una quincena ya pagada toca la plata más delicada del
sistema: un anticipo es efectivo QUE YA SE LE ENTREGÓ EN LA MANO al productor, y el
comprobante se lo RESTA. Mover uno cambia, en un papel que el productor ya tiene
guardado, la cifra grande con la que él cuadra lo que recibió.

    neto_a_pagar = valor_total − anticipos − saldo_anterior
    saldo        = neto_a_pagar − pagado

LO QUE SE ATACA ACÁ, en este orden:

  1. QUE LA COLUMNA DEL PAPEL SIGA SUMANDO DE ARRIBA ABAJO después de mover un
     adelanto, en los tres casos: entra uno, sale uno, cambia de valor. Se lee el PDF
     renglón por renglón —no la API— porque lo que el dueño suma con calculadora son
     los caracteres que salieron impresos. El molde es
     tests/test_liquidacion_saldo_anterior.py.
  2. QUE LA TABLA «Anticipos aplicados» DEL PAPEL SUME EXACTO el renglón «Anticipos
     aplicados» del resumen. Es la regla de la casa: todo desglose suma la cifra
     grande. Si el renglón dice −$250.000 y la tabla de abajo solo muestra $100.000,
     el productor ve $150.000 descontados que no puede reconocer.
  3. QUE EL PAPEL NOMBRE CADA ADELANTO MOVIDO, con su fecha y su cifra. Es lo ÚNICO
     que le permite al productor emparejar la hoja vieja con la nueva: sin la fecha,
     "se le descontó un adelanto" es una acusación que él no puede verificar.
  4. QUE EL RÓTULO DE LA CIFRA GRANDE DIGA LA VERDAD. Cuando el saldo queda negativo,
     el papel tiene que decir de dónde salió el negativo: LE QUEDA DEBIENDO cuando lo
     pusieron los adelantos (no salió efectivo por este comprobante), y SE LE PAGÓ DE
     MÁS cuando el efectivo que salió de la caja fue mayor que el neto.
  5. QUE EL RENGLÓN DE CORRECCIÓN GUARDE anticipos_antes / anticipos_despues, también
     cuando la corrección NO toca adelantos.
  6. QUE QUEDE EN LA BITÁCORA, y que en el libro se pueda ver que se movió un adelanto.
  7. Y lo que sale de ahí: que la hoja vieja se pida de vuelta cuando de verdad salió,
     que dos correcciones seguidas ENCADENEN las cifras de adelantos hoja contra hoja,
     que un adelanto que entra con el valor corregido se imprima con la cifra nueva en
     los tres sitios donde sale, que la columna siga cuadrando con CUATRO renglones que
     restar (cuando además arrastra deuda de la quincena pasada), que cambiar un
     adelanto por otro del mismo valor deje rastro aunque la cifra no se mueva, y que lo
     que no se puede corregir no saque papel nuevo ni suba la versión.

EL MONTAJE BASE ES SIEMPRE EL MISMO, con cifras que se suman de memoria:

    250 L × $2.000 = $500.000 de leche el 02/06      -> VALOR TOTAL   $500.000
    − $100.000 de adelanto entregado el 03/06        -> anticipos     $100.000
    = $400.000 de neto, que se pagan completos       -> pagado        $400.000
                                                        saldo             $0, 'pagada'

Y el adelanto olvidado: $150.000 entregados el 10/06, que nadie descontó.

TODAS LAS CIFRAS DE ESTE ARCHIVO ESTÁN CALCULADAS A MANO en el docstring de cada
prueba, nunca con el mismo código que se está probando.
"""
import io
import re
import uuid
from decimal import Decimal

import pytest
from pypdf import PdfReader

from tests.conftest import auth_headers

API = "/api/v1/liquidaciones"
REC = "/api/v1/recepciones"
ANT = "/api/v1/anticipos"

Q1 = ("2026-06-01", "2026-06-15")


def D(v):
    return Decimal(str(v))


CERO = D(0)


# --------------------------------------------------------------------- montaje
def _proveedor(client, h, nombre, precio="2000"):
    r = client.post(
        "/api/v1/proveedores",
        json={"nombre": nombre, "vereda": "El Roble", "precio_litro": precio},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _recepcion(client, h, prov, fecha, litros):
    r = client.post(
        REC,
        json={"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, prov, fecha, valor, observaciones="para la droga"):
    r = client.post(
        ANT,
        json={
            "tipo": "proveedor",
            "proveedor_id": prov["id"],
            "fecha": fecha,
            "valor": str(valor),
            "observaciones": observaciones,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _quincena_pagada(client, h, nombre="Libardo", litros="250", anticipo="100000"):
    """250 L × $2.000 = $500.000 − $100.000 de adelanto = $400.000, pagados completos."""
    prov = _proveedor(client, h, nombre)
    _recepcion(client, h, prov, "2026-06-02", litros)
    ant = _anticipo(client, h, prov, "2026-06-03", anticipo)
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": Q1[0], "periodo_fin": Q1[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    liq = next(
        x for x in r.json()["generadas"] if x["proveedor_id"] == prov["id"]
    )
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    return prov, ant, pagada.json()


def _previsualizar(client, h, liq_id, **cuerpo):
    cuerpo.setdefault("motivo", "revisando los adelantos")
    r = client.post(f"{API}/{liq_id}/corregir/previsualizar", json=cuerpo, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _corregir(client, h, liq_id, **cuerpo):
    return client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)


def _correcciones(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/correcciones", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _auditorias(db_session, liq_id, accion=None):
    from sqlalchemy import select

    from app.modules.auditoria.models import Auditoria

    condiciones = [Auditoria.entidad_id == uuid.UUID(liq_id)]
    if accion is not None:
        condiciones.append(Auditoria.accion == accion)
    return list(db_session.scalars(select(Auditoria).where(*condiciones)).all())


# ------------------------------------------------------------------ el papel
def _texto_pdf(contenido):
    crudo = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    return " ".join(crudo.split())


def _pdf(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    return _texto_pdf(r.content)


# El renglón del resumen del comprobante, CON SU SIGNO: "- $150.000" vale -150000.
# Se lee del papel impreso y no de la API a propósito: lo que el dueño suma a mano son
# los caracteres que salieron en la hoja.
_CIFRA = re.compile(r"(-?)\s*\$\s*(-?)([\d.]+(?:,\d{2})?)")


def renglon(papel, rotulo):
    inicio = papel.find(rotulo)
    assert inicio >= 0, f"el comprobante no trae el renglón «{rotulo}»:\n{papel}"
    resto = papel[inicio + len(rotulo):]
    encontrado = _CIFRA.search(resto)
    assert encontrado, f"el renglón «{rotulo}» salió sin cifra:\n{resto[:120]}"
    signo, signo_interno, cifra = encontrado.groups()
    valor = D(cifra.replace(".", "").replace(",", "."))
    return -valor if (signo == "-" or signo_interno == "-") else valor


_FILA_ADELANTO = re.compile(r"(\d{2}/\d{2}/\d{4})\s*\$\s*([\d.]+(?:,\d{2})?)")
_ENCABEZADO_TABLA = "Fecha Valor Observaciones"
_CORTES = ("Pagos y giros realizados", "Entregué conforme")


def tabla_de_adelantos(papel):
    """Las filas de la tabla «Anticipos descontados» del papel: [(fecha, valor)].

    El rótulo «Anticipos aplicados» sale DOS veces cuando hay tabla —una en el renglón
    del resumen y otra como título de la tabla— y una sola cuando no queda ningún
    adelanto descontado. Esa cuenta es justamente lo que distingue "la tabla salió
    vacía" de "la tabla no salió", y es lo que se quiere medir: el desglose tiene que
    desaparecer cuando el renglón queda en $0, no quedarse contradiciéndolo.
    """
    if papel.count("Anticipos aplicados") < 2:
        return []
    cuerpo = papel[papel.rindex("Anticipos aplicados"):]
    # La palabra "Observaciones" es también el título de la TERCERA columna, así que
    # cortar por ella se comía la tabla entera. Se arranca después del encabezado.
    assert _ENCABEZADO_TABLA in cuerpo, cuerpo[:200]
    cuerpo = cuerpo[cuerpo.index(_ENCABEZADO_TABLA) + len(_ENCABEZADO_TABLA):]
    for corte in _CORTES:
        if corte in cuerpo:
            cuerpo = cuerpo[: cuerpo.index(corte)]
    return [
        (fecha, D(valor.replace(".", "").replace(",", ".")))
        for fecha, valor in _FILA_ADELANTO.findall(cuerpo)
    ]


def _cuadre_del_resumen(papel):
    """Las siete cifras del resumen de un comprobante de proveedor, como salen impresas."""
    datos = {
        "bruto": renglon(papel, "Valor bruto"),
        "bonif": renglon(papel, "Bonificaciones"),
        "desc": renglon(papel, "Descuentos"),
        "total": renglon(papel, "VALOR TOTAL"),
        "anticipos": renglon(papel, "Anticipos aplicados"),
        "pagado": renglon(papel, "Pagado") if "Pagado" in papel else CERO,
    }
    for rotulo in ("SALDO A PAGAR", "SE LE PAGÓ DE MÁS", "LE QUEDA DEBIENDO"):
        if rotulo in papel:
            datos["rotulo"] = rotulo
            datos["cifra_grande"] = renglon(papel, rotulo)
            break
    else:  # pragma: no cover - si esto pasa, el papel salió sin cifra grande
        raise AssertionError(f"el comprobante salió sin renglón de saldo:\n{papel}")
    return datos


def _imprimir_cuadre(titulo, c):
    print(f"\n===== {titulo} =====")
    print(f"    Valor bruto           {c['bruto']}")
    print(f"    Bonificaciones        {c['bonif']}")
    print(f"    Descuentos            {c['desc']}")
    print(f"    VALOR TOTAL           {c['total']}")
    print(f"    Anticipos aplicados   {c['anticipos']}")
    print(f"    Pagado                {c['pagado']}")
    print(f"    {c['rotulo']:<21} {c['cifra_grande']}")


# ===========================================================================
# 1. LA COLUMNA DEL PAPEL SIGUE SUMANDO DESPUÉS DE MOVER UN ADELANTO
# ===========================================================================
def test_entra_un_adelanto_de_150000_y_el_papel_sigue_sumando_de_arriba_abajo(
    client, base_datos
):
    """El adelanto olvidado del 10/06 entra, y la columna impresa cuadra al peso.

    A MANO:
        Valor bruto                                    $500.000
        + Bonificaciones                                     $0
        − Descuentos                                         $0
        = VALOR TOTAL                                  $500.000
        − Anticipos aplicados ($100.000 + $150.000)    $250.000
        − Pagado                                       $400.000
        = −$150.000  ->  se le entregó plata de más por $150.000

    Y la tabla de adelantos del papel tiene que traer LOS DOS: $100.000 + $150.000,
    que es el desglose de ese renglón de $250.000.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    assert D(pagada["valor_total"]) == D("500000.00")
    assert D(pagada["pagado"]) == D("400000.00")

    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    prev = _previsualizar(client, h, pagada["id"])
    suelto = prev["anticipos_sueltos"][0]["anticipo_id"]

    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="faltaba el adelanto del 10",
        anticipos_a_incluir=[suelto],
    )
    assert r.status_code == 200, r.text

    papel = _pdf(client, h, pagada["id"])
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("1. ENTRA UN ADELANTO DE $150.000", c)

    assert c["bruto"] + c["bonif"] + c["desc"] == c["total"], (
        f"los renglones de arriba no dan VALOR TOTAL: {c['bruto']} + {c['bonif']} + "
        f"{c['desc']} contra {c['total']}"
    )
    # La cifra grande va en POSITIVO con el rótulo volteado cuando el saldo es
    # negativo, así que la resta de arriba abajo tiene que dar su NEGATIVO.
    assert c["total"] + c["anticipos"] + c["pagado"] == -c["cifra_grande"], (
        f"de VALOR TOTAL para abajo no se llega a la cifra grande: {c['total']} + "
        f"{c['anticipos']} + {c['pagado']} = "
        f"{c['total'] + c['anticipos'] + c['pagado']} contra -{c['cifra_grande']}"
    )
    # Y las cifras del papel son EXACTAMENTE las de arriba, escritas a mano.
    assert c["total"] == D("500000")
    assert c["anticipos"] == D("-250000")
    assert c["pagado"] == D("-400000")
    assert c["cifra_grande"] == D("150000")

    filas = tabla_de_adelantos(papel)
    print(f"    tabla de adelantos: {filas}")
    assert [f for f, _ in filas] == ["03/06/2026", "10/06/2026"], filas
    assert sum((v for _, v in filas), CERO) == -c["anticipos"] == D("250000"), (
        f"la tabla de adelantos suma {sum((v for _, v in filas), CERO)} y el renglón "
        f"del resumen dice {-c['anticipos']}"
    )


def test_sale_un_adelanto_de_100000_y_el_papel_sigue_sumando_de_arriba_abajo(
    client, base_datos
):
    """El adelanto que no era de esta quincena sale, y hay que entregarle $100.000.

    A MANO:
        VALOR TOTAL                                    $500.000
        − Anticipos aplicados (ya no queda ninguno)          $0
        − Pagado                                       $400.000
        = SALDO A PAGAR                                $100.000

    Esa plata se le entregó igual: el adelanto no se borra, queda suelto y se lo
    descuenta la quincena SIGUIENTE. Lo que este papel dice es que en ESTA no iba.
    Y la tabla de adelantos tiene que desaparecer: un renglón de $0 con una tabla
    debajo mostrando $100.000 sería el desglose contradiciendo la cifra grande.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)

    aplicado = _previsualizar(client, h, pagada["id"])["anticipos_aplicados"][0]
    assert D(aplicado["valor"]) == D("100000.00")

    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="ese adelanto era de la quincena pasada",
        anticipos_a_soltar=[aplicado["anticipo_id"]],
    )
    assert r.status_code == 200, r.text

    papel = _pdf(client, h, pagada["id"])
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("2. SALE EL ADELANTO DE $100.000", c)

    assert c["bruto"] + c["bonif"] + c["desc"] == c["total"] == D("500000")
    assert c["anticipos"] == CERO
    assert c["total"] + c["anticipos"] + c["pagado"] == c["cifra_grande"], (
        f"{c['total']} + {c['anticipos']} + {c['pagado']} contra {c['cifra_grande']}"
    )
    assert c["rotulo"] == "SALDO A PAGAR", c["rotulo"]
    assert c["cifra_grande"] == D("100000")

    filas = tabla_de_adelantos(papel)
    print(f"    tabla de adelantos: {filas}")
    assert filas == [], f"el adelanto soltado sigue impreso en la tabla: {filas}"


def test_el_adelanto_pasa_de_100000_a_80000_y_el_papel_sigue_sumando(client, base_datos):
    """El adelanto se digitó mal: eran $80.000 y se escribieron $100.000.

    A MANO:
        VALOR TOTAL                                    $500.000
        − Anticipos aplicados                           $80.000
        − Pagado                                       $400.000
        = SALDO A PAGAR                                 $20.000

    Le quedan debiendo $20.000 porque se le descontaron $20.000 que nunca se le
    entregaron. La tabla de adelantos tiene que imprimir el valor NUEVO: si dejara el
    viejo, el desglose diría $100.000 al lado de un renglón de $80.000.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    aplicado = _previsualizar(client, h, pagada["id"])["anticipos_aplicados"][0]

    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="el adelanto eran 80 mil, no 100 mil",
        valores_de_anticipos=[{"anticipo_id": aplicado["anticipo_id"], "valor": "80000"}],
    )
    assert r.status_code == 200, r.text

    papel = _pdf(client, h, pagada["id"])
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("3. EL ADELANTO PASA DE $100.000 A $80.000", c)

    assert c["bruto"] + c["bonif"] + c["desc"] == c["total"] == D("500000")
    assert c["anticipos"] == D("-80000")
    assert c["total"] + c["anticipos"] + c["pagado"] == c["cifra_grande"] == D("20000"), (
        f"{c['total']} + {c['anticipos']} + {c['pagado']} contra {c['cifra_grande']}"
    )
    assert c["rotulo"] == "SALDO A PAGAR"

    filas = tabla_de_adelantos(papel)
    print(f"    tabla de adelantos: {filas}")
    assert filas == [("03/06/2026", D("80000"))], filas


def test_el_adelanto_con_centavos_no_descuadra_la_columna_del_papel(client, base_datos):
    """Los centavos son donde se rompen las columnas impresas, así que van medidos.

    El adelanto se corrige a $80.000,35:
        VALOR TOTAL                                    $500.000,00
        − Anticipos aplicados                           $80.000,35
        − Pagado                                       $400.000,00
        = SALDO A PAGAR                                 $19.999,65

    500.000 − 80.000,35 = 419.999,65 de neto; 419.999,65 − 400.000 = 19.999,65.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    aplicado = _previsualizar(client, h, pagada["id"])["anticipos_aplicados"][0]

    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="el adelanto traia centavos",
        valores_de_anticipos=[{"anticipo_id": aplicado["anticipo_id"], "valor": "80000.35"}],
    )
    assert r.status_code == 200, r.text
    assert D(r.json()["saldo"]) == D("19999.65"), r.json()["saldo"]

    papel = _pdf(client, h, pagada["id"])
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("4. EL ADELANTO CON CENTAVOS", c)
    assert c["anticipos"] == D("-80000.35")
    assert c["total"] + c["anticipos"] + c["pagado"] == c["cifra_grande"] == D("19999.65")
    filas = tabla_de_adelantos(papel)
    print(f"    tabla de adelantos: {filas}")
    assert sum((v for _, v in filas), CERO) == D("80000.35"), filas


# ===========================================================================
# 2. EL PAPEL NOMBRA CADA ADELANTO MOVIDO, CON SU FECHA Y SU CIFRA
# ===========================================================================
def test_el_papel_nombra_el_adelanto_que_entro_con_su_fecha_y_su_cifra(
    client, base_datos
):
    """Sin la fecha y la cifra, "se le descontó un adelanto" no se puede verificar.

    El productor tiene una hoja que decía que le pagaban $400.000 y recibe otra que le
    descuenta $150.000 más. Lo único que le permite reconocer ese descuento —y aceptar
    que sí, ese día le dieron esa plata— es que el papel diga 10/06/2026 y $150.000.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]

    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="faltaba el adelanto del 10",
            anticipos_a_incluir=[suelto["anticipo_id"]],
        ).status_code
        == 200
    )

    papel = _pdf(client, h, pagada["id"])
    nota = papel[papel.find("Corregido el"):][:400]
    print("\n===== 5. LA LETRA CHICA DEL ADELANTO QUE ENTRÓ =====")
    print(f"    {nota}")
    assert "10/06/2026" in papel, "el papel no dice de qué día es el adelanto"
    assert "$150.000" in papel, "el papel no dice cuánto se le descontó"
    assert "se le descontó el adelanto del 10/06/2026 ($150.000)" in papel, papel
    # Y la banda que distingue las dos hojas a un metro de distancia.
    assert "COMPROBANTE CORREGIDO" in papel
    assert "-v2" in papel, "el folio del papel corregido no lleva la versión"


def test_el_papel_dice_que_el_adelanto_que_salio_se_cobra_en_la_siguiente(
    client, base_datos
):
    """El que sale NO se borra: esa plata se entregó, y el papel tiene que decirlo.

    Si el comprobante solo dejara de descontar los $100.000 sin explicar nada, el
    productor leería que el adelanto se le perdonó. Lo que pasó es otra cosa: no iba en
    ESTA quincena, y la siguiente se lo descuenta.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    aplicado = _previsualizar(client, h, pagada["id"])["anticipos_aplicados"][0]

    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="ese adelanto era de la quincena pasada",
            anticipos_a_soltar=[aplicado["anticipo_id"]],
        ).status_code
        == 200
    )

    papel = _pdf(client, h, pagada["id"])
    nota = papel[papel.find("Corregido el"):][:400]
    print("\n===== 6. LA LETRA CHICA DEL ADELANTO QUE SALIÓ =====")
    print(f"    {nota}")
    assert "el adelanto del 03/06/2026 ($100.000)" in papel, papel
    assert "ya NO se descuenta en esta quincena" in papel
    assert "se le descuenta en la siguiente" in papel


def test_el_papel_nombra_las_dos_cifras_del_adelanto_que_cambio_de_valor(
    client, base_datos
):
    """De $100.000 a $80.000: las DOS cifras, o el productor no puede emparejar.

    En la hoja vieja el adelanto figuraba en $100.000. Si la nueva solo dijera
    $80.000, la diferencia de $20.000 aparecería sola en la cifra grande sin nada que
    la explique — y es justo la discusión que las dos hojas sobre la mesa tienen que
    poder cerrar.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    aplicado = _previsualizar(client, h, pagada["id"])["anticipos_aplicados"][0]

    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="el adelanto eran 80 mil, no 100 mil",
            valores_de_anticipos=[
                {"anticipo_id": aplicado["anticipo_id"], "valor": "80000"}
            ],
        ).status_code
        == 200
    )

    papel = _pdf(client, h, pagada["id"])
    nota = papel[papel.find("Corregido el"):][:400]
    print("\n===== 7. LA LETRA CHICA DEL ADELANTO CORREGIDO =====")
    print(f"    {nota}")
    assert "el adelanto del 03/06/2026 pasó de $100.000 a $80.000" in papel, papel
    # Y el motivo escrito, que es lo que impide que esto sirva para tapar plata.
    assert "el adelanto eran 80 mil, no 100 mil" in papel


# ===========================================================================
# 3. EL RÓTULO DE LA CIFRA GRANDE DICE DE DÓNDE SALIÓ EL NEGATIVO
# ===========================================================================
def test_le_queda_debiendo_cuando_el_negativo_lo_pusieron_los_adelantos(
    client, base_datos
):
    """No salió un peso en efectivo por este comprobante: el negativo son los adelantos.

    MONTAJE, a mano: 250 L × $2.000 = $500.000 de leche contra $500.000 de adelanto ya
    entregado el 03/06. El neto da $0, se cierra con `pagado = $0` —los adelantos lo
    cubrieron exacto— y la quincena queda 'pagada'.

    Entra después el adelanto olvidado del 10/06 por $150.000:
        VALOR TOTAL                                    $500.000
        − Anticipos aplicados                          $650.000
        = neto                                        −$150.000
        − Pagado                                             $0
        = LE QUEDA DEBIENDO                            $150.000

    Decirle acá "SE LE PAGÓ DE MÁS" mandaría al dueño a buscar un pago que NO EXISTE:
    de la caja no salió un peso por este papel. Lo que hubo fueron adelantos.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h, anticipo="500000")
    assert D(pagada["anticipos"]) == D("500000.00")
    assert D(pagada["pagado"]) == CERO, pagada["pagado"]
    assert D(pagada["neto_a_pagar"]) == CERO

    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]
    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="faltaba el adelanto del 10",
        anticipos_a_incluir=[suelto["anticipo_id"]],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert D(d["neto_a_pagar"]) == D("-150000.00"), d["neto_a_pagar"]
    assert D(d["pagado"]) == CERO
    assert D(d["le_queda_debiendo"]) == D("150000.00")

    papel = _pdf(client, h, pagada["id"])
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("8. EL NEGATIVO LO PUSIERON LOS ADELANTOS", c)
    assert c["rotulo"] == "LE QUEDA DEBIENDO", (
        f"el papel dice «{c['rotulo']}» sobre una quincena en la que no salió un peso "
        "en efectivo: manda a buscar un pago que no existe"
    )
    assert c["cifra_grande"] == D("150000")
    assert "SE LE PAGÓ DE MÁS" not in papel
    # Y sin pagos, el renglón "Pagado" no sale: la resta es total − anticipos.
    assert c["total"] + c["anticipos"] == -c["cifra_grande"]


def test_se_le_pago_de_mas_cuando_el_negativo_lo_puso_el_efectivo_entregado(
    client, base_datos
):
    """La pregunta con la plata en la mano: ¿salió efectivo de más, o no salió?

    MONTAJE: $500.000 de leche − $100.000 de adelanto = $400.000 de neto, pagados EN
    EFECTIVO completos. Después entra el adelanto olvidado del 10/06 por $150.000:

        VALOR TOTAL                                    $500.000
        − Anticipos aplicados                          $250.000
        = neto                                         $250.000   (positivo)
        − Pagado (efectivo que SÍ salió de la caja)    $400.000
        = −$150.000

    LA CUENTA CON LA PLATA EN LA MANO: al productor le entregaron $400.000 en efectivo
    más $250.000 en adelantos = $650.000, por $500.000 de leche. El neto de la quincena
    era $250.000 y se le giraron $400.000: SE LE PAGÓ DE MÁS, $150.000, y esa plata sí
    salió de la caja y se puede ir a buscar. «LE QUEDA DEBIENDO» también sería cierto en
    el sentido contable, pero no le diría al dueño de dónde salió el hueco.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]

    prev = _previsualizar(
        client,
        h,
        pagada["id"],
        motivo="faltaba el adelanto del 10",
        anticipos_a_incluir=[suelto["anticipo_id"]],
    )
    print("\n===== 9. EL DIÁLOGO ANTES DE CONFIRMAR =====")
    print(f"    anticipos {prev['anticipos_antes']} -> {prev['anticipos_despues']}")
    print(f"    neto      {prev['neto_antes']} -> {prev['neto_despues']}")
    print(f"    saldo     {prev['saldo_antes']} -> {prev['saldo_despues']}")
    print(f"    se_le_pago_de_mas {prev['se_le_pago_de_mas']} · "
          f"queda_por_entregar {prev['queda_por_entregar']}")
    # La pantalla ya dice la misma frase que el papel: plata entregada de más.
    assert D(prev["se_le_pago_de_mas"]) == D("150000.00")
    assert D(prev["queda_por_entregar"]) == CERO
    assert D(prev["neto_despues"]) == D("250000.00")

    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="faltaba el adelanto del 10",
            anticipos_a_incluir=[suelto["anticipo_id"]],
        ).status_code
        == 200
    )

    papel = _pdf(client, h, pagada["id"])
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("9. EL NEGATIVO LO PUSO EL EFECTIVO ENTREGADO", c)
    assert c["rotulo"] == "SE LE PAGÓ DE MÁS", (
        f"el papel dice «{c['rotulo']}» sobre una quincena en la que se le giraron "
        "$400.000 contra un neto de $250.000: esa plata salió de la caja y hay que "
        "poder ir a buscarla"
    )
    assert c["cifra_grande"] == D("150000")
    assert "LE QUEDA DEBIENDO" not in papel
    assert c["pagado"] == D("-400000"), "el papel dejó de imprimir lo que ya se giró"


# ===========================================================================
# 4. EL RENGLÓN DE CORRECCIÓN: LAS DOS CIFRAS DE ADELANTOS
# ===========================================================================
def test_el_renglon_de_correccion_guarda_los_adelantos_antes_y_despues(
    client, base_datos
):
    """El rastro tiene que cuadrar con el papel, o no sirve de rastro.

    A mano: adelantos $100.000 -> $250.000 (entró el del 10/06 por $150.000), y esa
    diferencia de $150.000 es EXACTAMENTE lo que bajó el neto: $400.000 -> $250.000.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]
    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="faltaba el adelanto del 10",
            anticipos_a_incluir=[suelto["anticipo_id"]],
        ).status_code
        == 200
    )

    c = _correcciones(client, h, pagada["id"])[0]
    print("\n===== 10. EL RENGLÓN DE LA CORRECCIÓN =====")
    print(f"    anticipos {c['anticipos_antes']} -> {c['anticipos_despues']}")
    print(f"    neto      {c['neto_antes']} -> {c['neto_despues']}")
    print(f"    cambiados {c['anticipos_cambiados']}")

    assert D(c["anticipos_antes"]) == D("100000.00")
    assert D(c["anticipos_despues"]) == D("250000.00")
    # EL DESGLOSE SUMA EXACTO LA CIFRA GRANDE: lo que dice el JSON que se movió tiene
    # que ser lo mismo que la diferencia de las dos columnas.
    movido = sum(
        (D(x["valor"]) for x in c["anticipos_cambiados"] if x["accion"] == "entro"),
        CERO,
    )
    assert movido == D(c["anticipos_despues"]) - D(c["anticipos_antes"]) == D("150000.00")
    # Y el valor total NO se movió: esta corrección no tocó un solo día de leche.
    assert D(c["valor_total_antes"]) == D(c["valor_total_despues"]) == D("500000.00")
    # El neto bajó exactamente lo que subieron los adelantos.
    assert D(c["neto_antes"]) - D(c["neto_despues"]) == D("150000.00")
    assert D(c["saldo_despues"]) == D(c["neto_despues"]) - D(c["pagado_al_momento"])
    cambio = c["anticipos_cambiados"][0]
    assert cambio["fecha"] == "2026-06-10" and cambio["valor"] == "150000.00", cambio


def test_una_correccion_que_no_toca_adelantos_deja_las_dos_cifras_coherentes(
    client, base_datos
):
    """Entra un DÍA, no un adelanto: las dos columnas tienen que decir lo mismo.

    A mano: entra el 12/06 con 90 L × $2.000 = $180.000.
        VALOR TOTAL   $500.000 -> $680.000
        adelantos     $100.000 -> $100.000   (no se tocó ninguno)
        neto          $400.000 -> $580.000
        pagado        $400.000
        saldo                $0 ->  $180.000, 'parcial'

    Un cero en `anticipos_despues` acá sería MENTIRA —afirmaría que esta quincena no
    tenía adelantos— y un nulo dejaría al dueño sin poder emparejar las dos hojas.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _recepcion(client, h, prov, "2026-06-12", "90")
    prev = _previsualizar(client, h, pagada["id"])
    rec_id = prev["dias_sueltos"][0]["recepcion_id"]

    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="se anoto tarde el dia 12",
        recepciones_a_incluir=[rec_id],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert D(d["valor_total"]) == D("680000.00")
    assert D(d["anticipos"]) == D("100000.00")
    assert D(d["saldo"]) == D("180000.00")

    c = _correcciones(client, h, pagada["id"])[0]
    print("\n===== 11. UNA CORRECCIÓN QUE NO TOCA ADELANTOS =====")
    print(f"    anticipos {c['anticipos_antes']} -> {c['anticipos_despues']} · "
          f"cambiados {c['anticipos_cambiados']}")
    assert c["anticipos_antes"] is not None, "la columna quedó en nulo y no hay con qué emparejar"
    assert D(c["anticipos_antes"]) == D(c["anticipos_despues"]) == D("100000.00")
    assert c["anticipos_cambiados"] == []
    # La regla de la casa, sobre el renglón del rastro:
    #   neto = valor_total − anticipos (no hay saldo anterior)
    assert D(c["neto_despues"]) == D(c["valor_total_despues"]) - D(c["anticipos_despues"])
    assert D(c["neto_antes"]) == D(c["valor_total_antes"]) - D(c["anticipos_antes"])

    # Y el papel sigue cuadrando de arriba abajo con los adelantos intactos.
    papel = _pdf(client, h, pagada["id"])
    cu = _cuadre_del_resumen(papel)
    _imprimir_cuadre("11. EL PAPEL CON LOS ADELANTOS INTACTOS", cu)
    assert cu["total"] + cu["anticipos"] + cu["pagado"] == cu["cifra_grande"] == D("180000")
    assert tabla_de_adelantos(papel) == [("03/06/2026", D("100000"))]


# ===========================================================================
# 5. LA BITÁCORA
# ===========================================================================
def test_la_bitacora_muestra_que_se_movio_un_adelanto(client, base_datos, db_session):
    """En el libro tiene que poder verse que lo que cambió fue un ADELANTO.

    La pregunta de auditoría es "¿a quién se le cambió un descuento de adelanto después
    de entregarle el comprobante?". La respuesta tiene que salir del libro: un renglón
    con verbo propio ('corregir') y el antes/después de la columna `anticipos`.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]
    assert _auditorias(db_session, pagada["id"], "corregir") == []

    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="faltaba el adelanto del 10",
            anticipos_a_incluir=[suelto["anticipo_id"]],
        ).status_code
        == 200
    )

    filas = _auditorias(db_session, pagada["id"], "corregir")
    assert len(filas) == 1, "un verbo propio, una fila"
    fila = filas[0]
    print("\n===== 12. LA BITÁCORA =====")
    print(f"    accion={fila.accion} entidad={fila.entidad}")
    print(f"    anticipos {fila.antes['anticipos']} -> {fila.despues['anticipos']}")
    print(f"    version   {fila.antes['version']} -> {fila.despues['version']}")
    assert fila.entidad == "Liquidacion"
    assert fila.usuario_id == base_datos["admin_a"].id
    assert fila.empresa_id == base_datos["empresa_a"].id
    assert D(str(fila.antes["anticipos"])) == D("100000"), fila.antes["anticipos"]
    assert D(str(fila.despues["anticipos"])) == D("250000"), fila.despues["anticipos"]
    # El valor total NO se movió: sin la columna de adelantos, desde el libro esta
    # corrección parecería no haber cambiado un peso.
    assert D(str(fila.antes["valor_total"])) == D(str(fila.despues["valor_total"]))
    assert str(fila.antes["version"]) == "1" and str(fila.despues["version"]) == "2"

    # El renglón del rastro nombra el adelanto con fecha y cifra, que es lo que la
    # bitácora por sí sola no alcanza a decir.
    c = _correcciones(client, h, pagada["id"])[0]
    assert c["anticipos_cambiados"][0]["fecha"] == "2026-06-10"
    assert c["motivo"] == "faltaba el adelanto del 10"
    assert c["corregido_por_nombre"], "el libro no dice quién corrigió"




# ===========================================================================
# 6. EL PAPEL VIEJO QUE HAY QUE RECOGER
# ===========================================================================
def test_el_papel_corregido_pide_devolver_la_hoja_que_de_verdad_salio(
    client, base_datos
):
    """El flujo real: la hoja se imprimió y se entregó ANTES de corregir.

    Ahí el papel nuevo tiene que hacer tres cosas: nombrar la hoja vieja, decir cuándo
    salió, y pedir que la devuelvan. Dos hojas de la misma quincena en manos de la
    misma persona es un problema del mundo; lo único que el sistema puede hacer es
    escribir cuál reemplaza a cuál.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)

    # LA HOJA VIEJA, la que el productor se lleva: $400.000 de neto, ya pagados.
    vieja = _pdf(client, h, pagada["id"])
    assert "COMPROBANTE CORREGIDO" not in vieja
    assert "Por favor devuelva el papel anterior" not in vieja
    assert renglon(vieja, "Anticipos aplicados") == D("-100000")

    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]
    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="faltaba el adelanto del 10",
            anticipos_a_incluir=[suelto["anticipo_id"]],
        ).status_code
        == 200
    )

    nueva = _pdf(client, h, pagada["id"])
    print("\n===== 13. LA HOJA NUEVA CONTRA LA VIEJA =====")
    print(f"    vieja: anticipos {renglon(vieja, 'Anticipos aplicados')}")
    print(f"    nueva: anticipos {renglon(nueva, 'Anticipos aplicados')}")
    print(f"    {nueva[nueva.find('Este comprobante REEMPLAZA'):][:260]}")
    assert "Este comprobante REEMPLAZA" in nueva
    assert "emitido el" in nueva, "el papel nuevo no dice cuándo salió el viejo"
    assert "Por favor devuelva el papel anterior" in nueva
    # Y las dos hojas se llaman distinto, que es lo que permite saber cuál manda.
    folio = nueva[nueva.find("N.º ") + 4:][:11]
    assert folio.endswith("-v2"), folio
    assert folio[:8] in vieja and f"{folio[:8]}-v2" not in vieja


# ===========================================================================
# 7. DOS CORRECCIONES SEGUIDAS: LA CADENA DE ADELANTOS, HOJA CONTRA HOJA
# ===========================================================================
def test_dos_correcciones_seguidas_encadenan_los_adelantos_hoja_contra_hoja(
    client, base_datos
):
    """v2 mete un adelanto, v3 saca otro. El rastro tiene que encadenar.

    A MANO:
        v1  adelantos $100.000   neto $400.000   (pagados)
        v2  entra el del 10/06 por $150.000  ->  adelantos $250.000, neto $250.000
        v3  sale el del 03/06 por $100.000   ->  adelantos $150.000, neto $350.000

    LO QUE SE MIDE: que `anticipos_antes` de la v3 sea EXACTAMENTE
    `anticipos_despues` de la v2. Si la cadena se rompe, el dueño que pone las tres
    hojas en fila encuentra un hueco que ninguna corrección explica.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]
    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="faltaba el adelanto del 10",
            anticipos_a_incluir=[suelto["anticipo_id"]],
        ).status_code
        == 200
    )
    viejo = next(
        a
        for a in _previsualizar(client, h, pagada["id"])["anticipos_aplicados"]
        if a["fecha"] == "2026-06-03"
    )
    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="el del 3 era de la quincena pasada",
        anticipos_a_soltar=[viejo["anticipo_id"]],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert D(d["anticipos"]) == D("150000.00")
    assert D(d["neto_a_pagar"]) == D("350000.00")
    assert D(d["saldo"]) == D("-50000.00"), d["saldo"]

    c2, c3 = _correcciones(client, h, pagada["id"])
    print("\n===== 14. LA CADENA DE LAS TRES HOJAS =====")
    print(f"    v2: {c2['anticipos_antes']} -> {c2['anticipos_despues']}")
    print(f"    v3: {c3['anticipos_antes']} -> {c3['anticipos_despues']}")
    assert c2["version_nueva"] == 2 and c3["version_nueva"] == 3
    assert D(c2["anticipos_antes"]) == D("100000.00")
    assert D(c2["anticipos_despues"]) == D("250000.00")
    assert D(c3["anticipos_antes"]) == D(c2["anticipos_despues"]), (
        "la cadena se rompió: la v3 arranca de una cifra de adelantos que la v2 nunca "
        "dejó escrita"
    )
    assert D(c3["anticipos_despues"]) == D("150000.00")

    # Y EL PAPEL DE LA v3 TRAE LAS DOS CORRECCIONES, no solo la última: el productor
    # puede tener DOS hojas viejas y las dos tienen que poder emparejarse.
    papel = _pdf(client, h, pagada["id"])
    assert "(v2)" in papel and "(v3)" in papel, papel
    assert "se le descontó el adelanto del 10/06/2026 ($150.000)" in papel
    assert "el adelanto del 03/06/2026 ($100.000) ya NO se descuenta" in papel
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("14. EL PAPEL DE LA v3", c)
    assert c["total"] + c["anticipos"] + c["pagado"] == -c["cifra_grande"] == D("-50000")
    assert tabla_de_adelantos(papel) == [("10/06/2026", D("150000"))]


# ===========================================================================
# 8. ENTRAR Y CORREGIRLE EL VALOR AL MISMO ADELANTO, EN UNA SOLA HOJA
# ===========================================================================
def test_el_adelanto_que_entra_con_el_valor_corregido_se_imprime_con_la_cifra_nueva(
    client, base_datos
):
    """Entró el del 10/06, pero eran $120.000 y no $150.000.

    A MANO:
        VALOR TOTAL                                    $500.000
        − Anticipos ($100.000 + $120.000)              $220.000
        = neto                                         $280.000
        − Pagado                                       $400.000
        = se le pagó de más                            $120.000

    LO QUE SE MIDE: que el papel imprima $120.000 y NO $150.000 en los tres sitios
    donde esa cifra sale —el renglón del resumen, la tabla de adelantos y la letra
    chica—, porque un solo sitio con la cifra vieja deja al productor cuadrando contra
    una plata que nunca se le entregó.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]

    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="el del mercado eran 120 mil",
        anticipos_a_incluir=[suelto["anticipo_id"]],
        valores_de_anticipos=[
            {"anticipo_id": suelto["anticipo_id"], "valor": "120000"}
        ],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert D(d["anticipos"]) == D("220000.00"), d["anticipos"]
    assert D(d["neto_a_pagar"]) == D("280000.00")
    assert D(d["le_queda_debiendo"]) == D("120000.00")

    papel = _pdf(client, h, pagada["id"])
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("15. ENTRA CON EL VALOR CORREGIDO", c)
    assert c["anticipos"] == D("-220000")
    assert c["total"] + c["anticipos"] + c["pagado"] == -c["cifra_grande"] == D("-120000")
    filas = tabla_de_adelantos(papel)
    print(f"    tabla de adelantos: {filas}")
    assert filas == [("03/06/2026", D("100000")), ("10/06/2026", D("120000"))], filas
    assert sum((v for _, v in filas), CERO) == D("220000")
    assert "se le descontó el adelanto del 10/06/2026 ($120.000)" in papel, papel
    # Y EN NINGUNA PARTE LA CIFRA VIEJA: al productor nunca se le entregaron $150.000,
    # así que un papel que la nombre lo manda a cuadrar contra una plata que no existió.
    assert "150.000" not in papel, papel

    # EL DESGLOSE DEL RASTRO SUMA EXACTO LO QUE SE MOVIÓ: $220.000 − $100.000. El que
    # entra se anota UNA SOLA VEZ y con la cifra final; anotarlo dos veces (una como
    # "entró" y otra como "valor") haría que el desglose sumara $270.000 contra una
    # diferencia real de $120.000.
    corr = _correcciones(client, h, pagada["id"])[0]
    print(f"    cambiados {corr['anticipos_cambiados']}")
    entro = [x for x in corr["anticipos_cambiados"] if x["accion"] == "entro"]
    assert len(corr["anticipos_cambiados"]) == 1, corr["anticipos_cambiados"]
    assert len(entro) == 1 and D(entro[0]["valor"]) == D("120000.00"), corr[
        "anticipos_cambiados"
    ]
    assert D(entro[0]["valor"]) == D(corr["anticipos_despues"]) - D(
        corr["anticipos_antes"]
    )


# ===========================================================================
# 9. UNA QUINCENA QUE ADEMÁS ARRASTRA DEUDA: CUATRO RENGLONES QUE RESTAR
# ===========================================================================
def test_con_deuda_arrastrada_el_papel_sigue_sumando_despues_de_meter_un_adelanto(
    client, base_datos
):
    """El resumen más largo que existe, y el que más fácil descuadra.

    MONTAJE, a mano:
      Q1 (01-15/06): 100 L x $2.000 = $200.000 contra $300.000 de adelanto entregado
                     -> neto -$100.000: HENRI LE QUEDÓ DEBIENDO $100.000.
      Q2 (16-30/06): 100 L x $2.000 = $200.000, sin adelantos, y se le cobra la deuda:
                     neto = 200.000 - 0 - 100.000 = $100.000, que se pagan completos.

    Se corrige la Q2 metiéndole el adelanto olvidado del 25/06 por $40.000:
        VALOR TOTAL                                    $200.000
        - Anticipos aplicados                           $40.000
        - Lo que quedó debiendo de la quincena pasada   $100.000
        = neto                                          $60.000
        - Pagado                                       $100.000
        = SE LE PAGÓ DE MÁS                             $40.000

    CUATRO renglones que restar de la cifra grande, y el de la deuda NO se puede mover
    desde acá. Si moverle un adelanto le tocara `saldo_anterior`, el papel dejaría de
    cuadrar y el error se lo comería la deuda de otra quincena.
    """
    h = auth_headers(client, "admin.a")
    henri = _proveedor(client, h, "Henri")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, henri, "2026-06-01", "300000", "el adelanto grande")
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-01", "periodo_fin": "2026-06-15",
              "tipo": "proveedor"},
        headers=h,
    )
    q1 = next(x for x in r.json()["generadas"] if x["proveedor_id"] == henri["id"])
    assert D(q1["le_queda_debiendo"]) == D("100000.00"), q1["le_queda_debiendo"]
    assert client.post(f"{API}/{q1['id']}/aprobar", headers=h).status_code == 200

    _recepcion(client, h, henri, "2026-06-20", "100")
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": "2026-06-16", "periodo_fin": "2026-06-30",
              "tipo": "proveedor"},
        headers=h,
    )
    q2 = next(x for x in r.json()["generadas"] if x["proveedor_id"] == henri["id"])
    assert D(q2["saldo_anterior"]) == D("100000.00"), q2["saldo_anterior"]
    assert D(q2["neto_a_pagar"]) == D("100000.00")
    assert client.post(f"{API}/{q2['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{q2['id']}/pagar", headers=h).json()
    assert D(pagada["pagado"]) == D("100000.00")

    _anticipo(client, h, henri, "2026-06-25", "40000", "el del 25")
    suelto = _previsualizar(client, h, q2["id"])["anticipos_sueltos"][0]
    assert suelto["fecha"] == "2026-06-25", suelto
    r = _corregir(
        client,
        h,
        q2["id"],
        motivo="faltaba el adelanto del 25",
        anticipos_a_incluir=[suelto["anticipo_id"]],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert D(d["saldo_anterior"]) == D("100000.00"), (
        "moverle un adelanto le tocó la deuda que arrastraba de otra quincena"
    )
    assert D(d["neto_a_pagar"]) == D("60000.00")
    assert D(d["le_queda_debiendo"]) == D("40000.00")

    papel = _pdf(client, h, q2["id"])
    c = _cuadre_del_resumen(papel)
    deuda = renglon(papel, "Lo que quedó debiendo de la quincena pasada")
    _imprimir_cuadre("16. CON DEUDA ARRASTRADA", c)
    print(f"    Lo que quedó debiendo de la quincena pasada  {deuda}")
    assert c["total"] == D("200000")
    assert c["anticipos"] == D("-40000")
    assert deuda == D("-100000")
    assert c["pagado"] == D("-100000")
    assert c["total"] + c["anticipos"] + deuda + c["pagado"] == -c["cifra_grande"], (
        f"{c['total']} + {c['anticipos']} + {deuda} + {c['pagado']} = "
        f"{c['total'] + c['anticipos'] + deuda + c['pagado']} contra "
        f"-{c['cifra_grande']}"
    )
    assert c["rotulo"] == "SE LE PAGÓ DE MÁS" and c["cifra_grande"] == D("40000")
    # El orden de la resta es el que el dueño lee de arriba abajo.
    assert papel.index("VALOR TOTAL") < papel.index("Anticipos aplicados")
    assert papel.index("Anticipos aplicados") < papel.index("Lo que quedó debiendo")
    assert papel.index("Lo que quedó debiendo") < papel.index("Pagado")
    assert tabla_de_adelantos(papel) == [("25/06/2026", D("40000"))]


# ===========================================================================
# 10. CUANDO LA CIFRA NO SE MUEVE PERO EL ADELANTO SÍ
# ===========================================================================
def test_cambiar_un_adelanto_por_otro_del_mismo_valor_deja_rastro_igual(
    client, base_datos
):
    """Sale uno de $100.000 y entra otro de $100.000: la cifra grande no se mueve.

    Es el caso que más fácil se pierde: `anticipos` sigue en $100.000, el neto sigue en
    $400.000 y el saldo sigue en $0. Desde las columnas de la liquidación NO PASÓ NADA
    —y sin embargo se le cambió al productor CUÁL adelanto se le está descontando, que
    es exactamente la discusión que él va a traer: "ese del 3 ya me lo habían
    descontado en la quincena pasada".

    Lo que se mide: que el rastro y el papel lo digan aunque la plata no se mueva.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "100000", "el del mercado")
    prev = _previsualizar(client, h, pagada["id"])
    viejo = prev["anticipos_aplicados"][0]
    nuevo = prev["anticipos_sueltos"][0]

    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="el del 3 era de la quincena pasada; va el del 10",
        anticipos_a_soltar=[viejo["anticipo_id"]],
        anticipos_a_incluir=[nuevo["anticipo_id"]],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert D(d["anticipos"]) == D("100000.00")
    assert D(d["neto_a_pagar"]) == D("400000.00")
    assert D(d["saldo"]) == CERO

    corr = _correcciones(client, h, pagada["id"])[0]
    acciones = {x["accion"]: x for x in corr["anticipos_cambiados"]}
    print("\n===== 17. LA PLATA NO SE MOVIÓ, EL ADELANTO SÍ =====")
    print(f"    anticipos {corr['anticipos_antes']} -> {corr['anticipos_despues']}")
    print(f"    cambiados {corr['anticipos_cambiados']}")
    assert D(corr["anticipos_antes"]) == D(corr["anticipos_despues"]) == D("100000.00")
    assert set(acciones) == {"salio", "entro"}, corr["anticipos_cambiados"]
    assert acciones["salio"]["fecha"] == "2026-06-03"
    assert acciones["entro"]["fecha"] == "2026-06-10"
    # Y EL DESGLOSE SIGUE CUADRANDO: lo que entró menos lo que salió es cero, que es
    # exactamente lo que se movió la columna.
    movido = D(acciones["entro"]["valor"]) - D(acciones["salio"]["valor"])
    assert movido == D(corr["anticipos_despues"]) - D(corr["anticipos_antes"]) == CERO

    papel = _pdf(client, h, pagada["id"])
    print(f"    {papel[papel.find('Corregido el'):][:320]}")
    assert "el adelanto del 03/06/2026 ($100.000) ya NO se descuenta" in papel
    assert "se le descontó el adelanto del 10/06/2026 ($100.000)" in papel
    assert tabla_de_adelantos(papel) == [("10/06/2026", D("100000"))], (
        "la tabla del papel sigue mostrando el adelanto que salió"
    )
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("17. EL PAPEL DEL CAMBIO DE ADELANTO", c)
    assert c["total"] + c["anticipos"] + c["pagado"] == c["cifra_grande"] == CERO


# ===========================================================================
# 11. LO QUE NO SE PUEDE CORREGIR NO ESCRIBE NI UNA LETRA
# ===========================================================================
def test_corregirle_el_valor_a_un_adelanto_suelto_no_saca_papel_nuevo(
    client, base_datos
):
    """Un adelanto suelto no es de esta quincena: no se le corrige el valor desde acá.

    Y lo que importa para el papel: el diálogo y el botón tienen que contestar LO
    MISMO. Si la previsualización dejara pasar y el botón rebotara, el dueño vería una
    cifra aprobada que nunca se escribió; si fuera al revés, se le corregiría plata de
    un documento que este comprobante no explica. Se comprueba además que no subió la
    versión: una versión fantasma le cambia el nombre al papel sin que haya un peso de
    diferencia que lo explique.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]
    cuerpo = {
        "motivo": "el del mercado eran 120 mil",
        "valores_de_anticipos": [
            {"anticipo_id": suelto["anticipo_id"], "valor": "120000"}
        ],
    }

    previo = client.post(
        f"{API}/{pagada['id']}/corregir/previsualizar", json=cuerpo, headers=h
    )
    boton = client.post(f"{API}/{pagada['id']}/corregir", json=cuerpo, headers=h)
    print("\n===== 18. EL DIÁLOGO Y EL BOTÓN CONTESTAN LO MISMO =====")
    print(f"    previsualizar {previo.status_code} · corregir {boton.status_code}")
    print(f"    {boton.text[:200]}")
    assert previo.status_code == 422, previo.text
    assert boton.status_code == previo.status_code, (
        "el diálogo y el botón no contestan lo mismo"
    )

    quedo = client.get(f"{API}/{pagada['id']}", headers=h).json()
    assert quedo["version"] == 1, "subió la versión sin corregir un peso"
    assert D(quedo["anticipos"]) == D("100000.00")
    assert _correcciones(client, h, pagada["id"]) == []
    papel = _pdf(client, h, pagada["id"])
    assert "COMPROBANTE CORREGIDO" not in papel
    assert tabla_de_adelantos(papel) == [("03/06/2026", D("100000"))]


# ===========================================================================
# 12. LA CIFRA VIEJA DEL ADELANTO QUE ENTRA CORREGIDO SIGUE SIENDO RECUPERABLE
# ===========================================================================
def test_la_cifra_vieja_del_adelanto_que_entro_corregido_se_puede_recuperar(
    client, base_datos, db_session
):
    """El adelanto estaba anotado en $150.000 y entra a la quincena por $120.000.

    ESA REESCRITURA ES PLATA: el que anotó el adelanto escribió $150.000 y el papel del
    productor va a decir $120.000. El renglón de la corrección anota el que ENTRA una
    sola vez y con la cifra FINAL —anotarlo dos veces haría que el desglose sumara
    $270.000 contra una diferencia real de $120.000—, así que la cifra vieja NO está en
    `anticipos_cambiados`. Lo que se mide aquí es que no se haya perdido: el libro
    guarda el 'crear' del adelanto con sus $150.000, y con esas dos fuentes se puede
    contestar "¿quién le bajó $30.000 a ese adelanto y por qué?".
    """
    import json

    from sqlalchemy import select

    from app.modules.auditoria.models import Auditoria
    from app.modules.liquidaciones.models import Anticipo

    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    nuevo = _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]
    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="el del mercado eran 120 mil",
            anticipos_a_incluir=[suelto["anticipo_id"]],
            valores_de_anticipos=[
                {"anticipo_id": suelto["anticipo_id"], "valor": "120000"}
            ],
        ).status_code
        == 200
    )

    vivo = db_session.scalars(
        select(Anticipo).where(Anticipo.id == uuid.UUID(nuevo["id"]))
    ).one()
    corr = _correcciones(client, h, pagada["id"])[0]
    creado = [
        a
        for a in db_session.scalars(select(Auditoria)).all()
        if a.entidad == "Anticipo"
        and a.accion == "crear"
        and a.despues
        and str(a.despues.get("fecha")) == "2026-06-10"
    ]
    print("\n===== 19. LA CIFRA VIEJA DEL ADELANTO =====")
    print(f"    el adelanto hoy vale        {vivo.valor}")
    print(f"    el renglón dice que entró a {corr['anticipos_cambiados']}")
    print(f"    el libro lo creó en         {[a.despues['valor'] for a in creado]}")

    assert D(vivo.valor) == D("120000.00")
    assert corr["anticipos_cambiados"][0]["valor"] == "120000.00"
    # La cifra vieja NO viaja en el renglón del que entra (sí en la acción 'valor' de
    # los que ya estaban): se recupera del libro.
    assert "valor_antes" not in corr["anticipos_cambiados"][0]
    assert len(creado) == 1, "el libro no guardó el nacimiento del adelanto"
    assert D(str(creado[0].despues["valor"])) == D("150000"), creado[0].despues
    # Y la diferencia se puede sacar con las dos fuentes: $150.000 − $120.000.
    assert D(str(creado[0].despues["valor"])) - D(
        corr["anticipos_cambiados"][0]["valor"]
    ) == D("30000.00")
    texto = json.dumps(
        [
            [a.antes, a.despues]
            for a in db_session.scalars(select(Auditoria)).all()
        ],
        ensure_ascii=False,
        default=str,
    )
    assert "150000" in texto, "la cifra vieja del adelanto se perdió del libro"


# ===========================================================================
# 13. EL ADELANTO VIEJO, EL QUE VIENE DE ANTES DEL PERÍODO
# ===========================================================================
def test_el_papel_nombra_con_su_fecha_el_adelanto_viejo_que_se_metio(
    client, base_datos
):
    """Un adelanto de enero que nunca se le descontó a nadie, metido en la quincena de junio.

    `pendientes_de` NO tiene cota por abajo, así que ese adelanto sale como candidato
    meses después. Meterlo puede ser exactamente lo que el dueño quiere —es plata que
    entregó y no ha recuperado— pero el papel tiene que decir DE QUÉ FECHA es, o el
    productor ve un descuento de $200.000 sin nada con qué reconocerlo.

    A MANO:
        VALOR TOTAL                                    $500.000
        − Anticipos ($100.000 del 03/06 + $200.000 del 20/01)  $300.000
        = neto                                         $200.000
        − Pagado                                       $400.000
        = SE LE PAGÓ DE MÁS                            $200.000
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-01-20", "200000", "el de enero")

    prev = _previsualizar(client, h, pagada["id"])
    viejo = next(a for a in prev["anticipos_sueltos"] if a["fecha"] == "2026-01-20")
    print("\n===== 20. EL ADELANTO DE ENERO =====")
    print(f"    aviso: {viejo['aviso']}")
    assert viejo["aviso"], "el diálogo no señala el adelanto anterior al período"
    assert "20/01/2026" in viejo["aviso"]
    assert "ANTES de esta quincena" in viejo["aviso"]

    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="el de enero nunca se le descontó",
            anticipos_a_incluir=[viejo["anticipo_id"]],
        ).status_code
        == 200
    )

    papel = _pdf(client, h, pagada["id"])
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("20. EL PAPEL CON EL ADELANTO DE ENERO", c)
    assert c["anticipos"] == D("-300000")
    assert c["total"] + c["anticipos"] + c["pagado"] == -c["cifra_grande"] == D("-200000")
    assert c["rotulo"] == "SE LE PAGÓ DE MÁS"
    assert "se le descontó el adelanto del 20/01/2026 ($200.000)" in papel, papel
    # La tabla va por fecha, así que el de enero encabeza: es el orden en que el
    # productor los busca en su cuaderno.
    filas = tabla_de_adelantos(papel)
    print(f"    tabla de adelantos: {filas}")
    assert filas == [("20/01/2026", D("200000")), ("03/06/2026", D("100000"))], filas
    assert sum((v for _, v in filas), CERO) == -c["anticipos"] == D("300000")


# ===========================================================================
# 14. EL BORDE DEL RÓTULO: CUANDO EL NEGATIVO LO PONEN LAS DOS COSAS
# ===========================================================================
def test_el_rotulo_en_el_borde_donde_el_negativo_lo_ponen_el_efectivo_y_los_adelantos(
    client, base_datos
):
    """El caso mezclado, que es donde el rótulo se juega la verdad.

    A MANO: $500.000 de leche, $100.000 de adelanto, $400.000 girados en efectivo
    (saldo $0). Entra después un adelanto olvidado de $450.000:

        VALOR TOTAL                                    $500.000
        − Anticipos ($100.000 + $450.000)              $550.000
        = neto                                        −$50.000   (NEGATIVO)
        − Pagado                                       $400.000
        = le queda debiendo                            $450.000

    DE ESOS $450.000, $400.000 SON EFECTIVO QUE SÍ SALIÓ DE LA CAJA y $50.000 son
    adelantos que la quincena no alcanzó a cubrir. El papel dice LE QUEDA DEBIENDO
    —que es cierto: el productor le debe $450.000 al negocio— y la parte en efectivo no
    se pierde, porque el renglón «Pagado − $400.000» está impreso justo encima. Esta
    prueba fija ese borde: si el rótulo cambiara a «SE LE PAGÓ DE MÁS», estaría
    diciendo que los $450.000 salieron todos en efectivo, y $50.000 de esos nunca lo
    hicieron.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "450000", "el grande del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]

    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="faltaba el adelanto grande del 10",
        anticipos_a_incluir=[suelto["anticipo_id"]],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert D(d["anticipos"]) == D("550000.00")
    assert D(d["neto_a_pagar"]) == D("-50000.00")
    assert D(d["pagado"]) == D("400000.00")
    assert D(d["le_queda_debiendo"]) == D("450000.00")

    papel = _pdf(client, h, pagada["id"])
    c = _cuadre_del_resumen(papel)
    _imprimir_cuadre("21. EL BORDE DEL RÓTULO", c)
    assert c["rotulo"] == "LE QUEDA DEBIENDO", c["rotulo"]
    assert c["cifra_grande"] == D("450000")
    # La columna sigue cuadrando de arriba abajo, que es lo que el dueño verifica:
    assert c["total"] + c["anticipos"] + c["pagado"] == -c["cifra_grande"]
    # Y el efectivo que sí salió sigue impreso, para que esos $400.000 no se confundan
    # con los $50.000 de adelanto que la quincena no alcanzó a cubrir.
    assert c["pagado"] == D("-400000"), "el papel dejó de decir cuánto se giró"
    assert "SE LE PAGÓ DE MÁS" not in papel


# ===========================================================================
# 15. DEFECTO: LA LETRA CHICA NOMBRA LA CIFRA QUE **NO** SE MOVIÓ
# ===========================================================================
def test_la_letra_chica_nombra_la_cifra_que_se_movio_y_no_la_que_se_quedo_quieta(
    client, base_datos
):
    """Cuando solo se mueve un adelanto, VALOR TOTAL NO CAMBIA. La plata sí.

    A MANO: $500.000 de leche, $100.000 de adelanto, $400.000 girados. Entra el
    adelanto olvidado del 10/06 por $150.000:

        VALOR TOTAL   $500.000 -> $500.000   (NO se movió: no entró ni un litro)
        neto          $400.000 -> $250.000   (se movió $150.000, que es lo que él cobra)

    El renglón del rastro sí guarda las dos cifras del neto, y el desglose nombra el
    adelanto con su fecha y su valor —eso funciona—. Lo que falla es la frase de
    cierre, que es la que el productor lee de último y con la que se queda: le dice que
    el VALOR TOTAL pasó de $500.000 a $500.000, sobre una hoja que le descuenta
    $150.000 más que la anterior. La hoja vieja y la nueva dicen el MISMO VALOR TOTAL,
    así que esa frase tampoco sirve para emparejarlas.

    LO QUE DEBERÍA DECIR: la cifra que se movió. El neto (o el saldo) de $400.000 a
    $250.000, que es la plata que el productor fue a recibir.
    """
    h = auth_headers(client, "admin.a")
    prov, ant, pagada = _quincena_pagada(client, h)
    _anticipo(client, h, prov, "2026-06-10", "150000", "el del mercado")
    suelto = _previsualizar(client, h, pagada["id"])["anticipos_sueltos"][0]
    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="faltaba el adelanto del 10",
            anticipos_a_incluir=[suelto["anticipo_id"]],
        ).status_code
        == 200
    )

    papel = _pdf(client, h, pagada["id"])
    inicio = papel.index("Corregido el")
    nota = papel[inicio: papel.index("Anticipos aplicados", inicio)]
    print("\n===== 22. LA LETRA CHICA DE UNA CORRECCIÓN DE PURO ADELANTO =====")
    print(f"    {nota}")

    # Esto sí sale hoy, y es lo que salva la hoja: el adelanto queda nombrado.
    assert "se le descontó el adelanto del 10/06/2026 ($150.000)" in nota

    # LO QUE FALLA:
    assert "pasó de $500.000 a $500.000" not in nota, (
        "la letra chica cierra diciendo que la cifra que nombra no cambió"
    )
    assert "$400.000" in nota and "$250.000" in nota, (
        "la letra chica no nombra la cifra que sí se movió: lo que se le entrega pasó "
        "de $400.000 a $250.000"
    )
