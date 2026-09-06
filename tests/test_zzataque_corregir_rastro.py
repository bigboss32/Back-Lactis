"""ATAQUE 4 — EL RASTRO DE LA CORRECCIÓN, que es lo que impide que esto sirva para tapar plata.

Corregir una quincena YA PAGADA es, mirado de frente, la operación más peligrosa del
sistema: la cifra grande de un comprobante que el productor ya tiene EN LA MANO cambia
después de entregada. Lo único que separa eso de "tapar plata" es el rastro: un renglón
en `liquidaciones_correcciones` que diga quién, cuándo, por qué, y CONTRA QUÉ CIFRA; un
renglón de bitácora con verbo propio; y una versión que suba, para que el papel nuevo se
llame distinto al viejo.

LO QUE SE ATACA ACÁ, en este orden:

  1. Que el renglón quede escrito con todo: motivo, nombre congelado de quien corrigió,
     hora, y las cifras antes/después EN COLUMNAS (no solo adentro del JSON).
  2. Que esas cifras CUADREN CON EL DOCUMENTO. Un rastro que no cuadra con el papel no
     sirve de rastro: si el renglón dice $680.000 y la liquidación dice otra cosa, el
     dueño no puede emparejar la hoja vieja con la nueva y el renglón es decoración.
  3. Que el desglose `dias_agregados` sume EXACTO la diferencia entre la cifra grande
     vieja y la nueva. Ese es literalmente el emparejamiento que el dueño hace con
     calculadora entre las dos hojas: "¿de dónde salieron estos $180.000 de más?".
  4. Que la bitácora diga 'corregir' y no el 'editar' genérico: en el libro esto tiene
     que poder buscarse como lo que es.
  5. Que NO se pueda corregir sin dejar rastro: motivo vacío, de dos letras o de puros
     espacios tiene que rebotar SIN ESCRIBIR NADA, y corregir sin escoger ningún día ni
     ningún precio tampoco puede subir la versión —una versión fantasma le cambiaría el
     folio al papel sin que haya un peso de diferencia que la explique—.

TODAS LAS CIFRAS DE ESTE ARCHIVO ESTÁN CALCULADAS A MANO en el docstring de cada prueba.
El montaje base es siempre el mismo, para que se puedan sumar de memoria:

    250 L × $2.000 = $500.000 de leche el 02/06
    − $100.000 de anticipo entregado en la mano
    = $400.000 de neto, que se pagan completos y dejan la quincena en 'pagada'.

Y el día olvidado del 12/06: 90 L × $2.000 = $180.000.
"""
import uuid
from decimal import Decimal

import pytest

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


def _recepcion(client, h, prov, fecha, litros, *, precio=None, bonif=None, desc=None):
    cuerpo = {"fecha": fecha, "proveedor_id": prov["id"], "cantidad_litros": str(litros)}
    if precio is not None:
        cuerpo["precio_litro"] = str(precio)
    if bonif is not None:
        cuerpo["bonificaciones"] = str(bonif)
    if desc is not None:
        cuerpo["descuentos"] = str(desc)
    r = client.post(REC, json=cuerpo, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _anticipo(client, h, prov, fecha, valor):
    r = client.post(
        ANT,
        json={
            "fecha": fecha,
            "valor": str(valor),
            "tipo": "proveedor",
            "proveedor_id": prov["id"],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _generar(client, h, periodo=Q1):
    r = client.post(
        f"{API}/generar",
        json={"periodo_inicio": periodo[0], "periodo_fin": periodo[1], "tipo": "proveedor"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["generadas"]


def _leer(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _correcciones(client, h, liq_id):
    r = client.get(f"{API}/{liq_id}/correcciones", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _previsualizar(client, h, liq_id, **cuerpo):
    cuerpo.setdefault("motivo", "para mirar")
    return client.post(f"{API}/{liq_id}/corregir/previsualizar", json=cuerpo, headers=h)


def _corregir(client, h, liq_id, **cuerpo):
    return client.post(f"{API}/{liq_id}/corregir", json=cuerpo, headers=h)


def _quincena_pagada(client, h, nombre, *, con_anticipo=True):
    """La quincena del dueño, ya pagada: $500.000 de leche − $100.000 de anticipo.

    Devuelve (proveedor, liquidación pagada). Con anticipo el neto queda en $400.000 y
    eso es a propósito: si `neto` fuera igual a `valor_total`, el cuadre
    `neto = valor_total − anticipos − saldo_anterior` pasaría sin haberse verificado.
    """
    prov = _proveedor(client, h, nombre)
    _recepcion(client, h, prov, "2026-06-02", "250")
    if con_anticipo:
        _anticipo(client, h, prov, "2026-06-03", "100000")
    generadas = _generar(client, h)
    liq = next(x for x in generadas if x["proveedor_id"] == prov["id"])
    assert client.post(f"{API}/{liq['id']}/aprobar", headers=h).status_code == 200
    pagada = client.post(f"{API}/{liq['id']}/pagar", headers=h)
    assert pagada.status_code == 200, pagada.text
    pagada = pagada.json()
    assert pagada["estado"] == "pagada", pagada
    assert D(pagada["valor_total"]) == D("500000.00"), pagada["valor_total"]
    assert D(pagada["version"]) == 1
    return prov, pagada


def _dia_suelto(client, h, prov, fecha, litros):
    """Un día del período que se anotó DESPUÉS de que la quincena ya estaba pagada."""
    return _recepcion(client, h, prov, fecha, litros)


def _auditorias(db_session, liq_id, accion=None):
    from app.modules.auditoria.models import Auditoria

    stmt = [Auditoria.entidad_id == uuid.UUID(liq_id)]
    if accion is not None:
        stmt.append(Auditoria.accion == accion)
    from sqlalchemy import select

    return list(db_session.scalars(select(Auditoria).where(*stmt)).all())


def _filas_de_correccion(db_session, liq_id=None):
    from sqlalchemy import select

    from app.modules.liquidaciones.models import CorreccionLiquidacion

    stmt = select(CorreccionLiquidacion)
    if liq_id is not None:
        stmt = stmt.where(CorreccionLiquidacion.liquidacion_id == uuid.UUID(liq_id))
    return list(db_session.scalars(stmt).all())


# ------------------------------------------------- 1. el renglón cuadra con el papel
def test_el_renglon_de_correccion_cuadra_peso_por_peso_con_el_comprobante_nuevo(
    client, base_datos, db_session
):
    """Las cifras del rastro tienen que ser LAS MISMAS del documento corregido.

    LA CUENTA, a mano:
        antes    500.000 de leche − 100.000 de anticipo = 400.000 de neto, pagados
                 completos -> saldo 0, estado 'pagada'.
        entra    el 12/06: 90 L × $2.000 = 180.000.
        después  680.000 − 100.000 = 580.000 de neto, pagado sigue en 400.000
                 -> saldo 180.000, estado 'parcial'.

    POR QUÉ IMPORTA CADA CUADRE: el productor tiene en la mano una hoja que dice
    $400.000 y la pantalla dirá $580.000. Lo único que empareja las dos hojas es este
    renglón. Si `valor_total_despues` no fuera exactamente el `valor_total` que quedó en
    la liquidación, el renglón estaría contando una tercera historia que no existe en
    ningún papel; y si `neto_antes` no fuera `valor_total_antes − anticipos −
    saldo_anterior`, el rastro no se podría releer solo —haría falta ir a buscar los
    anticipos de esa quincena, que para entonces ya pueden haberse movido—.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")
    assert D(pagada["neto_a_pagar"]) == D("400000.00")
    assert D(pagada["pagado"]) == D("400000.00")
    assert D(pagada["saldo"]) == CERO

    _dia_suelto(client, h, prov, "2026-06-12", "90")
    prev = _previsualizar(client, h, pagada["id"]).json()
    rec_id = next(d["recepcion_id"] for d in prev["dias_sueltos"] if d["fecha"] == "2026-06-12")

    hecho = _corregir(
        client,
        h,
        pagada["id"],
        motivo="Se anoto tarde la leche del 12 de junio",
        recepciones_a_incluir=[rec_id],
    )
    assert hecho.status_code == 200, hecho.text
    liq = hecho.json()

    # La cifra grande nueva, calculada a mano: 500.000 + 180.000.
    assert D(liq["valor_total"]) == D("680000.00"), liq["valor_total"]
    assert D(liq["neto_a_pagar"]) == D("580000.00"), liq["neto_a_pagar"]
    assert D(liq["pagado"]) == D("400000.00"), "corregir NO puede mover un solo pago"
    assert D(liq["saldo"]) == D("180000.00"), liq["saldo"]
    # LA REGLA DE LA CASA.
    assert D(liq["neto_a_pagar"]) == D(liq["pagado"]) + D(liq["saldo"])

    correcciones = _correcciones(client, h, pagada["id"])
    assert len(correcciones) == 1, "una corrección, un renglón"
    c = correcciones[0]

    # QUIÉN, CUÁNDO Y POR QUÉ. El nombre va congelado en el renglón —y no solo el id en
    # `created_by`— porque si mañana borran o renombran al usuario, el papel tiene que
    # seguir diciendo quién le cambió la cifra al comprobante del productor.
    assert c["motivo"] == "Se anoto tarde la leche del 12 de junio"
    assert c["corregido_por_nombre"] == "Admin.A Prueba", c["corregido_por_nombre"]
    assert c["created_at"], "sin hora el renglón no se puede ordenar ni fechar"
    assert c["version_nueva"] == 2

    # LAS CIFRAS, EN COLUMNAS. Estas son las que el dueño suma con calculadora.
    assert D(c["valor_total_antes"]) == D("500000.00")
    assert D(c["valor_total_despues"]) == D("680000.00")
    assert D(c["neto_antes"]) == D("400000.00")
    assert D(c["neto_despues"]) == D("580000.00")
    assert D(c["pagado_al_momento"]) == D("400000.00")
    assert D(c["saldo_antes"]) == CERO
    assert D(c["saldo_despues"]) == D("180000.00")
    assert c["estado_antes"] == "pagada"
    assert c["estado_despues"] == "parcial"

    # Y AHORA EL CUADRE CONTRA EL DOCUMENTO, que es lo que hace que el rastro sirva.
    anticipos = D(liq["anticipos"])
    saldo_anterior = D(liq["saldo_anterior"])
    assert D(c["valor_total_despues"]) == D(liq["valor_total"])
    assert D(c["neto_despues"]) == D(liq["neto_a_pagar"])
    assert D(c["saldo_despues"]) == D(liq["saldo"])
    assert D(c["pagado_al_momento"]) == D(liq["pagado"])
    assert c["estado_despues"] == liq["estado"]
    # El renglón se relee solo: neto = valor_total − anticipos − saldo_anterior, en las
    # DOS puntas, con las columnas que la corrección no toca.
    assert D(c["neto_antes"]) == D(c["valor_total_antes"]) - anticipos - saldo_anterior
    assert D(c["neto_despues"]) == D(c["valor_total_despues"]) - anticipos - saldo_anterior
    assert D(c["saldo_antes"]) == D(c["neto_antes"]) - D(c["pagado_al_momento"])
    assert D(c["saldo_despues"]) == D(c["neto_despues"]) - D(c["pagado_al_momento"])

    # Y el renglón queda amarrado a la empresa y al usuario en la base, no solo en la
    # pantalla: una fila de rastro sin tenant es una fila que otra quesera puede leer.
    filas = _filas_de_correccion(db_session, pagada["id"])
    assert len(filas) == 1
    fila = filas[0]
    assert fila.empresa_id == base_datos["empresa_a"].id
    assert fila.created_by == base_datos["admin_a"].id
    assert fila.motivo == "Se anoto tarde la leche del 12 de junio"


# ------------------------------------------- 2. el desglose suma la diferencia exacta
def test_los_dias_agregados_suman_exacto_la_diferencia_entre_la_hoja_vieja_y_la_nueva(
    client, base_datos
):
    """"¿De dónde salieron estos $240.000 de más?" — el desglose tiene que contestarlo.

    LA CUENTA, a mano:
        antes    500.000.
        entran   el 12/06: 90 L × $2.000 = 180.000
                 el 14/06: 30 L × $2.000 =  60.000  (con $5.000 de bonificación y
                                                     $2.000 de descuento)
        La bonificación y el descuento están puestos a propósito: el valor del día que
        mueve la cifra grande es bruto + bonificaciones − descuentos, no el bruto pelado.
        60.000 + 5.000 − 2.000 = 63.000.
        después  500.000 + 180.000 + 63.000 = 743.000.
        diferencia = 243.000, que es exactamente lo que tienen que sumar los dos
        renglones del desglose.

    Si el desglose sumara otra cosa, el dueño se quedaría con una diferencia sin
    explicación entre las dos hojas, que es la forma exacta en que se pierde plata sin
    que nadie lo note.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")

    _dia_suelto(client, h, prov, "2026-06-12", "90")
    _recepcion(client, h, prov, "2026-06-14", "30", bonif="5000", desc="2000")

    prev = _previsualizar(client, h, pagada["id"]).json()
    sueltos = {d["fecha"]: d for d in prev["dias_sueltos"]}
    assert set(sueltos) == {"2026-06-12", "2026-06-14"}
    assert D(sueltos["2026-06-14"]["valor"]) == D("63000.00"), sueltos["2026-06-14"]

    hecho = _corregir(
        client,
        h,
        pagada["id"],
        motivo="Dos dias que quedaron sin anotar",
        recepciones_a_incluir=[s["recepcion_id"] for s in sueltos.values()],
    )
    assert hecho.status_code == 200, hecho.text
    liq = hecho.json()
    assert D(liq["valor_total"]) == D("743000.00"), liq["valor_total"]

    c = _correcciones(client, h, pagada["id"])[0]
    diferencia = D(c["valor_total_despues"]) - D(c["valor_total_antes"])
    assert diferencia == D("243000.00"), diferencia

    suma_del_desglose = sum((D(d["valor"]) for d in c["dias_agregados"]), CERO)
    assert suma_del_desglose == diferencia, (
        f"el desglose suma {suma_del_desglose} y la cifra grande subió {diferencia}: "
        "el dueño no puede emparejar las dos hojas"
    )
    assert len(c["dias_agregados"]) == 2
    assert c["precios_corregidos"] == []
    # Y cada renglón del desglose se verifica multiplicando, como el dueño lo hace.
    por_fecha = {d["fecha"]: d for d in c["dias_agregados"]}
    assert D(por_fecha["2026-06-12"]["litros"]) * D(por_fecha["2026-06-12"]["precio_litro"]) == D(
        "180000.00"
    )
    assert D(por_fecha["2026-06-12"]["valor"]) == D("180000.00")
    assert D(por_fecha["2026-06-14"]["valor"]) == D("63000.00")


def test_el_desglose_de_precios_explica_exacto_la_rebaja_cuando_la_cifra_grande_baja(
    client, base_datos
):
    """Cuando el total BAJA, el rastro tiene que explicar la rebaja peso por peso.

    LA CUENTA, a mano, sin anticipo para que el neto sea la cifra grande:
        antes    250 L × $2.000 = 500.000, pagados completos.
        el precio estaba mal: eran $1.600 -> 250 L × $1.600 = 400.000.
        después  400.000 contra 500.000 ya entregados: saldo −100.000, le queda
                 debiendo 100.000, y el estado sigue en 'pagada' porque salió toda la
                 plata (y $100.000 de más).

    ESTA ES LA MITAD PELIGROSA DE LA OPERACIÓN: bajarle la cifra a una quincena ya
    pagada crea una deuda del productor contra la quesera. Si el desglose no dice
    exactamente de qué día salió esa rebaja, la deuda aparece sin origen.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri", con_anticipo=False)
    assert D(pagada["pagado"]) == D("500000.00")

    detalle = next(d for d in pagada["detalles"] if d["fecha"] == "2026-06-02")
    hecho = _corregir(
        client,
        h,
        pagada["id"],
        motivo="El precio del 02 estaba en 2000 y era 1600",
        precios=[{"detalle_id": detalle["id"], "precio_litro": "1600"}],
    )
    assert hecho.status_code == 200, hecho.text
    liq = hecho.json()
    assert D(liq["valor_total"]) == D("400000.00"), liq["valor_total"]
    assert D(liq["saldo"]) == D("-100000.00"), liq["saldo"]
    assert D(liq["le_queda_debiendo"]) == D("100000.00")
    assert liq["estado"] == "pagada"
    assert D(liq["neto_a_pagar"]) == D(liq["pagado"]) + D(liq["saldo"])

    c = _correcciones(client, h, pagada["id"])[0]
    diferencia = D(c["valor_total_despues"]) - D(c["valor_total_antes"])
    assert diferencia == D("-100000.00"), diferencia
    assert c["dias_agregados"] == []
    assert len(c["precios_corregidos"]) == 1
    p = c["precios_corregidos"][0]
    movido = D(p["valor_despues"]) - D(p["valor_antes"])
    assert movido == diferencia, (
        f"el desglose de precios explica {movido} y la cifra grande se movió {diferencia}"
    )
    assert D(p["precio_antes"]) == D("2000")
    assert D(p["precio_despues"]) == D("1600")
    assert D(p["litros"]) * D(p["precio_despues"]) == D(p["valor_despues"])
    assert D(c["saldo_despues"]) == D("-100000.00")
    assert D(c["saldo_despues"]) == D(c["neto_despues"]) - D(c["pagado_al_momento"])


# ------------------------------------------------------------ 3. la bitácora
def test_la_bitacora_dice_corregir_y_no_el_editar_generico(client, base_datos, db_session):
    """En el libro esto tiene que poder buscarse como lo que es.

    Una corrección de una quincena PAGADA no es una edición cualquiera: es la única
    operación que le cambia la cifra a un papel que ya salió de la oficina. Si quedara
    anotada como 'editar', se perdería entre las decenas de ediciones de borradores y
    nadie podría contestar "¿cuántas veces se les cambió la cifra a comprobantes ya
    entregados este mes?", que es la pregunta de auditoría que justifica todo esto.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")
    _dia_suelto(client, h, prov, "2026-06-12", "90")
    prev = _previsualizar(client, h, pagada["id"]).json()
    rec_id = prev["dias_sueltos"][0]["recepcion_id"]

    corregir_registros_antes = _auditorias(db_session, pagada["id"], "corregir")
    assert corregir_registros_antes == []

    assert (
        _corregir(
            client,
            h,
            pagada["id"],
            motivo="Se anoto tarde el 12",
            recepciones_a_incluir=[rec_id],
        ).status_code
        == 200
    )

    filas = _auditorias(db_session, pagada["id"], "corregir")
    assert len(filas) == 1, "un verbo propio, una fila"
    fila = filas[0]
    assert fila.entidad == "Liquidacion"
    assert fila.usuario_id == base_datos["admin_a"].id
    assert fila.empresa_id == base_datos["empresa_a"].id
    # El antes y el después de la bitácora tienen que contar la misma historia que el
    # renglón de corrección: si la bitácora dijera version 1 -> 1, no habría cómo saber
    # desde el libro que salió un papel nuevo.
    assert str(fila.antes["version"]) == "1", fila.antes["version"]
    assert str(fila.despues["version"]) == "2", fila.despues["version"]
    assert D(str(fila.antes["valor_total"])) == D("500000.00")
    assert D(str(fila.despues["valor_total"])) == D("680000.00")


# ------------------------------------------------------------ 4. la versión sube
def test_la_version_sube_una_por_una_y_los_renglones_se_encadenan_hoja_contra_hoja(
    client, base_datos
):
    """1 -> 2 -> 3, y la hoja nueva empieza donde terminó la anterior.

    LA CUENTA, a mano:
        v1   500.000 (pagada con 400.000 tras 100.000 de anticipo)
        v2   entra el 12/06 (90 L × 2.000 = 180.000) -> 680.000
        v3   entra el 14/06 (30 L × 2.000 =  60.000) -> 740.000

    EL ENCADENAMIENTO ES EL PUNTO: `valor_total_antes` de la v3 tiene que ser
    exactamente el `valor_total_despues` de la v2. Si hubiera un hueco entre las dos, en
    ese hueco cabe plata que se movió sin renglón que la explique — que es exactamente
    lo que este rastro existe para impedir.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")
    _dia_suelto(client, h, prov, "2026-06-12", "90")
    _dia_suelto(client, h, prov, "2026-06-14", "30")

    prev = _previsualizar(client, h, pagada["id"]).json()
    sueltos = {d["fecha"]: d["recepcion_id"] for d in prev["dias_sueltos"]}

    v2 = _corregir(
        client,
        h,
        pagada["id"],
        motivo="Primer dia olvidado",
        recepciones_a_incluir=[sueltos["2026-06-12"]],
    )
    assert v2.status_code == 200, v2.text
    assert v2.json()["version"] == 2

    v3 = _corregir(
        client,
        h,
        pagada["id"],
        motivo="Segundo dia olvidado",
        recepciones_a_incluir=[sueltos["2026-06-14"]],
    )
    assert v3.status_code == 200, v3.text
    liq = v3.json()
    assert liq["version"] == 3
    assert D(liq["valor_total"]) == D("740000.00"), liq["valor_total"]

    correcciones = _correcciones(client, h, pagada["id"])
    assert [c["version_nueva"] for c in correcciones] == [2, 3], "de la más vieja a la más nueva"
    assert [c["motivo"] for c in correcciones] == ["Primer dia olvidado", "Segundo dia olvidado"]

    a, b = correcciones
    assert D(a["valor_total_antes"]) == D("500000.00")
    assert D(a["valor_total_despues"]) == D("680000.00")
    assert D(b["valor_total_antes"]) == D(a["valor_total_despues"]), (
        "entre la v2 y la v3 hay un hueco: plata que se movió sin renglón que la explique"
    )
    assert D(b["valor_total_despues"]) == D("740000.00")
    assert D(b["valor_total_despues"]) == D(liq["valor_total"])
    # Y el segundo renglón también cuadra con lo que quedó pagado: `pagado` no se movió
    # en ninguna de las dos correcciones.
    assert D(a["pagado_al_momento"]) == D("400000.00")
    assert D(b["pagado_al_momento"]) == D("400000.00")
    assert D(b["saldo_despues"]) == D(liq["saldo"]) == D("240000.00")


# ------------------------------------------- 5. no se puede corregir sin dejar rastro
@pytest.mark.parametrize(
    "motivo, como_se_llama",
    [
        ("", "vacío"),
        ("ab", "de dos letras"),
        ("   ", "de puros espacios"),
        ("\t\n  \t", "de espacios y tabuladores"),
    ],
)
def test_un_motivo_que_no_dice_nada_rebota_y_no_deja_ni_media_correccion_escrita(
    client, base_datos, db_session, motivo, como_se_llama
):
    """Sin motivo escrito, una corrección no se distingue de un error — ni de un fraude.

    El motivo es lo único que después le explica a alguien por qué la hoja de $400.000
    que el productor guardó y la pantalla que dice $580.000 hablan de la misma quincena.
    Un motivo {como_se_llama} es lo mismo que no tener motivo.

    Y no basta con que rebote: tiene que rebotar SIN ESCRIBIR NADA. Si el día olvidado
    quedara marcado contra la liquidación o la versión subiera, la quincena quedaría
    corregida a medias, con la plata movida y sin renglón que lo explique.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")
    _dia_suelto(client, h, prov, "2026-06-12", "90")
    prev = _previsualizar(client, h, pagada["id"]).json()
    rec_id = prev["dias_sueltos"][0]["recepcion_id"]

    r = _corregir(client, h, pagada["id"], motivo=motivo, recepciones_a_incluir=[rec_id])
    assert r.status_code == 422, f"un motivo {como_se_llama} tiene que rebotar: {r.text}"

    despues = _leer(client, h, pagada["id"])
    assert despues["version"] == 1, "versión fantasma: el folio cambió sin corrección"
    assert D(despues["valor_total"]) == D("500000.00"), "la plata se movió sin rastro"
    assert D(despues["pagado"]) == D("400000.00")
    assert _correcciones(client, h, pagada["id"]) == []
    assert _filas_de_correccion(db_session, pagada["id"]) == []
    assert _auditorias(db_session, pagada["id"], "corregir") == []
    # Y el día olvidado sigue suelto, listo para entrar cuando alguien escriba el motivo.
    prev2 = _previsualizar(client, h, pagada["id"]).json()
    assert [d["recepcion_id"] for d in prev2["dias_sueltos"]] == [rec_id]


def test_corregir_sin_escoger_ni_un_dia_ni_un_precio_no_puede_subir_la_version(
    client, base_datos, db_session
):
    """Una versión fantasma le cambiaría el folio al papel sin un peso que lo explique.

    El productor recibiría una "v2" idéntica en cifras a la v1 y la única lectura
    posible sería que algo se movió y no quedó anotado. Peor: la previsualización avisa
    "esta quincena ya se corrigió N veces, recójale las anteriores" contando versiones,
    así que una versión fantasma manda al dueño a buscar una hoja que nunca se imprimió.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")
    _dia_suelto(client, h, prov, "2026-06-12", "90")

    r = _corregir(client, h, pagada["id"], motivo="Se me olvido decir cual dia")
    assert r.status_code == 422, r.text
    assert "no hay nada que corregir" in r.json()["error"]["detail"].lower(), r.text

    despues = _leer(client, h, pagada["id"])
    assert despues["version"] == 1, "subió la versión sin corregir un peso"
    assert D(despues["valor_total"]) == D("500000.00")
    assert despues["estado"] == "pagada"
    assert _correcciones(client, h, pagada["id"]) == []
    assert _filas_de_correccion(db_session, pagada["id"]) == []
    assert _auditorias(db_session, pagada["id"], "corregir") == []


def test_un_precio_de_otra_liquidacion_rebota_y_no_deja_el_comprobante_a_medias(
    client, base_datos, db_session
):
    """Se manda un día bueno y un precio malo en la MISMA petición: no puede entrar medio.

    Las dos formas de corregir viajan juntas a propósito, y por eso mismo la validación
    de las dos tiene que pasar ANTES de escribir el primer peso. Si el día olvidado
    quedara marcado y el precio rebotara, la quincena quedaría con $680.000 escritos, sin
    renglón de corrección, sin versión nueva y sin nadie que sepa por qué.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")
    # Un detalle que existe... pero en el comprobante de OTRO proveedor.
    _prov_b = _proveedor(client, h, "Marina")
    _recepcion(client, h, _prov_b, "2026-06-04", "100")
    otras = _generar(client, h)
    ajena = next(x for x in otras if x["proveedor_id"] == _prov_b["id"])
    detalle_ajeno = ajena["detalles"][0]["id"]

    _dia_suelto(client, h, prov, "2026-06-12", "90")
    prev = _previsualizar(client, h, pagada["id"]).json()
    rec_id = prev["dias_sueltos"][0]["recepcion_id"]

    r = _corregir(
        client,
        h,
        pagada["id"],
        motivo="Un dia bueno y un precio de otra hoja",
        recepciones_a_incluir=[rec_id],
        precios=[{"detalle_id": detalle_ajeno, "precio_litro": "1600"}],
    )
    assert r.status_code == 404, r.text

    despues = _leer(client, h, pagada["id"])
    assert despues["version"] == 1
    assert D(despues["valor_total"]) == D("500000.00"), "el día entró a medias"
    assert _filas_de_correccion(db_session, pagada["id"]) == []
    assert _auditorias(db_session, pagada["id"], "corregir") == []
    prev2 = _previsualizar(client, h, pagada["id"]).json()
    assert [d["recepcion_id"] for d in prev2["dias_sueltos"]] == [rec_id], "el día quedó marcado"


def test_un_motivo_relleno_de_espacios_no_se_cuela_por_debajo_del_minimo(
    client, base_datos
):
    """"  ab  " tiene 6 caracteres, pero el papel solo va a leer dos letras.

    El mínimo de 3 está puesto para que el motivo diga algo, y aquí hay una rendija
    obvia por donde intentar colarse: quien no quiera escribir por qué le cambió la
    cifra a un comprobante ya entregado teclea dos letras y dos espacios. NO SE CUELA:
    `BaseSchema` recorta los espacios ANTES de medir el largo, así que el esquema mide
    lo mismo que va a quedar impreso. Esta prueba es lo que amarra esas dos cosas: si
    algún día se le quita el `str_strip_whitespace` a `BaseSchema`, el rastro se
    empezaría a llenar de motivos de dos letras y nadie se enteraría.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")
    _dia_suelto(client, h, prov, "2026-06-12", "90")
    prev = _previsualizar(client, h, pagada["id"]).json()
    rec_id = prev["dias_sueltos"][0]["recepcion_id"]

    r = _corregir(client, h, pagada["id"], motivo="  ab  ", recepciones_a_incluir=[rec_id])
    assert r.status_code == 422, (
        f"se coló un motivo de dos letras rellenado con espacios: {r.text}"
    )
    assert _leer(client, h, pagada["id"])["version"] == 1
    assert _correcciones(client, h, pagada["id"]) == []


# ---------------------------------------- 6. el ataque: el mismo día escogido dos veces
#
# DEFECTO QUE ESTUVO ABIERTO Y SE CERRÓ. Las dos pruebas de abajo demuestran el mismo
# hueco por sus dos puntas: con el día repetido, la previsualización prometía $860.000 y
# el botón escribía $680.000, y el desglose de la corrección sumaba $360.000 al lado de
# una cifra grande que subió $180.000. Se arregló con `dict.fromkeys()` sobre
# `recepciones_a_incluir` dentro de `_simular_correccion` —el único sitio por donde pasan
# los dos caminos—, así que estas dos se quedan aquí en verde, custodiando el arreglo.
def test_escoger_el_mismo_dia_dos_veces_no_puede_desajustar_el_desglose(client, base_datos):
    """El día olvidado marcado dos veces: el rastro tiene que seguir cuadrando.

    CÓMO PASA EN LA VIDA REAL: la pantalla manda la lista de casillas marcadas. Un doble
    envío del formulario, una casilla duplicada por un re-render, o alguien llamando la
    API con el mismo id dos veces, y llega `recepciones_a_incluir=[X, X]`.

    LA CUENTA, a mano: el 12/06 vale 90 L × $2.000 = $180.000. La quincena pasa de
    $500.000 a $680.000 —SUBE $180.000 y una sola vez— porque la recepción es UNA fila y
    `_recalcular_desde_recepciones` la lee una vez. Eso sale bien: la plata del
    comprobante queda correcta.

    LO QUE NO SALE BIEN ES EL RASTRO, que es lo que esta prueba mide: `dias_agregados`
    trae el 12/06 DOS VECES y suma $360.000. Y ese desglose es justo la respuesta a la
    única pregunta que el dueño le hace al renglón —"¿de dónde salieron estos $180.000
    de más?"—: le contesta con $360.000. Un rastro que no cuadra con el documento no
    sirve de rastro.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")
    _dia_suelto(client, h, prov, "2026-06-12", "90")
    prev = _previsualizar(client, h, pagada["id"]).json()
    rec_id = prev["dias_sueltos"][0]["recepcion_id"]

    hecho = _corregir(
        client,
        h,
        pagada["id"],
        motivo="El 12 de junio, marcado dos veces por error",
        recepciones_a_incluir=[rec_id, rec_id],
    )
    assert hecho.status_code == 200, hecho.text
    liq = hecho.json()
    # La plata del comprobante SÍ queda bien: el recálculo lee la fila una vez.
    assert D(liq["valor_total"]) == D("680000.00"), liq["valor_total"]
    assert D(liq["saldo"]) == D("180000.00")
    assert D(liq["neto_a_pagar"]) == D(liq["pagado"]) + D(liq["saldo"])

    c = _correcciones(client, h, pagada["id"])[0]
    diferencia = D(c["valor_total_despues"]) - D(c["valor_total_antes"])
    assert diferencia == D("180000.00"), diferencia
    suma_del_desglose = sum((D(d["valor"]) for d in c["dias_agregados"]), CERO)
    assert len(c["dias_agregados"]) == 1, c["dias_agregados"]
    assert suma_del_desglose == diferencia, (
        f"el desglose suma {suma_del_desglose} y la cifra grande solo subió {diferencia}"
    )


def test_la_calculadora_de_la_pantalla_no_puede_prometer_una_cifra_y_el_boton_escribir_otra(
    client, base_datos
):
    """La previsualización existe para que el dueño confirme mirando la cifra final.

    Es "la mitad de la seguridad de la operación", según el propio código: el dueño pone
    la hoja vieja al lado de la pantalla y compara ANTES de confirmar. Si la pantalla
    dice $860.000 y lo que se guarda son $680.000, ese paso deja de proteger nada — el
    dueño aprobó una cifra que nunca existió.

    LA CUENTA, a mano: el 12/06 son 90 L × $2.000 = $180.000, una sola vez.
    Previsualizado con el día repetido: $500.000 + $180.000 + $180.000 = $860.000, que
    es una quincena que no existe en ninguna parte.
    """
    h = auth_headers(client, "admin.a")
    prov, pagada = _quincena_pagada(client, h, "Henri")
    _dia_suelto(client, h, prov, "2026-06-12", "90")
    prev = _previsualizar(client, h, pagada["id"]).json()
    rec_id = prev["dias_sueltos"][0]["recepcion_id"]

    prometido = _previsualizar(
        client, h, pagada["id"], recepciones_a_incluir=[rec_id, rec_id]
    ).json()
    hecho = _corregir(
        client,
        h,
        pagada["id"],
        motivo="El 12 de junio, marcado dos veces por error",
        recepciones_a_incluir=[rec_id, rec_id],
    )
    assert hecho.status_code == 200, hecho.text
    liq = hecho.json()

    assert D(prometido["valor_total_despues"]) == D(liq["valor_total"]), (
        f"la pantalla prometió {prometido['valor_total_despues']} y se escribieron "
        f"{liq['valor_total']}"
    )
    assert D(prometido["queda_por_entregar"]) == D(liq["saldo"]), (
        f"la pantalla prometió entregarle {prometido['queda_por_entregar']} y el "
        f"comprobante quedó debiéndole {liq['saldo']}"
    )
