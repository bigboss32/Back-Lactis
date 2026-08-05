"""LA CORRIDA DE LA QUINCENA NO SE TUMBA NUNCA, LA PLATA CUADRA, Y EL SALTO NO DEJA BASURA.

VERIFICACIÓN ADVERSARIAL del sobre que devuelve `POST /liquidaciones/generar`
—{"generadas": [...], "omitidas": [...]}— y del salto que lo reemplazó al rebote. Tres
mitades, y el orden es el de lo que más cuesta:

  A. QUE LA CORRIDA NO SE TUMBE NUNCA. Es lo primero porque una corrida que rebota deja al
     dueño SIN PODER PAGARLE A NADIE un día de pago: "Generar quincena" es un botón de
     barrida sobre todos los terceros del período y la pantalla no tiene el filtro de a uno
     (manda {periodo_inicio, periodo_fin, tipo} y nunca `proveedor_id`). Se atacan todos los
     caminos por donde un tercero se puede quedar por fuera y en cada uno se exigen LAS DOS
     COSAS: que los sanos SALGAN LIQUIDADOS y que los omitidos SALGAN REPORTADOS. Un
     tercero saltado en silencio es peor que el error, porque el dueño cierra la pantalla
     creyendo que ya liquidó a todos.
  B. QUE LA PLATA CUADRE. La cuenta del dueño, medida con `_cuadra` (la misma regla de los
     45 escenarios anteriores): anticipos entregados + plata pagada == leche liquidada
     − lo que la quesera todavía debe + lo que el tercero quedó debiendo. Y que el papel
     oficial, el papel preliminar y la pantalla digan LO MISMO.
  C. QUE EL SALTO NO DEJE BASURA. Un tercero omitido no puede quedar A MEDIO ESCRIBIR: ni
     una liquidación sin renglones, ni un día marcado con una liquidación que no existe, ni
     un anticipo apartado para una liquidación que no se creó, ni una foto de flete movida.

El instrumental sale de tests/test_liquidacion_deuda_arrastrada_plata.py para medir con la
MISMA regla. El comportamiento del guardia está en `_omitido_por_periodo_cruzado`,
`_omitido_por_flete_sin_tarifa` y `LiquidacionRepository.solapada_para_periodo`.
"""
from tests.conftest import auth_headers
from tests.test_liquidacion_deuda_arrastrada_plata import (
    API,
    CERO,
    D,
    Q1,
    Q2,
    Q3,
    Q4,
    Q5,
    _anticipo,
    _anticipos,
    _anular,
    _aprobar,
    _cuadra,
    _detalle,
    _leer,
    _libro,
    _pagar,
    _pdf,
    _proveedor,
    _recepcion,
    _recepciones,
    _ruta,
    _todas,
    _transportador,
    renglon,
    texto_pdf,
)

TRANS = "/api/v1/transportadores"


# ---------------------------------------------------------------- instrumental
def _pedir(client, h, inicio, fin, tipo="proveedor", proveedor_id=None):
    cuerpo = {"periodo_inicio": inicio, "periodo_fin": fin, "tipo": tipo}
    if proveedor_id:
        cuerpo["proveedor_id"] = proveedor_id
    return client.post(f"{API}/generar", json=cuerpo, headers=h)


def _corrida(client, h, inicio, fin, tipo="proveedor", proveedor_id=None):
    """La corrida COMPLETA, exigiendo que NO SE HAYA TUMBADO.

    Este assert es la mitad A entera: cualquier cosa distinta de 200 es el dueño parado
    frente a la pantalla el día de pago sin poder liquidarle a nadie.
    """
    r = _pedir(client, h, inicio, fin, tipo, proveedor_id)
    assert r.status_code == 200, (
        f"CRÍTICO — LA CORRIDA DE LA QUINCENA SE TUMBÓ ({inicio} al {fin}, tipo={tipo}): "
        f"{r.status_code} — {_detalle(r)}"
    )
    return r.json()


def _ok(client, h, inicio, fin, tipo="proveedor", proveedor_id=None):
    """Las generadas, exigiendo además que no se quedara NADIE por fuera."""
    corrida = _corrida(client, h, inicio, fin, tipo, proveedor_id)
    assert corrida["omitidas"] == [], (
        f"la corrida ({inicio} al {fin}, tipo={tipo}) dejó terceros SIN COMPROBANTE: "
        + "; ".join(
            f"{o['tercero_nombre']} ({o['cuenta']}, {o['motivo_codigo']})"
            for o in corrida["omitidas"]
        )
    )
    return corrida["generadas"]


def _un_omitido(corrida):
    assert len(corrida["omitidas"]) == 1, corrida["omitidas"]
    return corrida["omitidas"][0]


def _bien_reportado(omitida, *, tercero, cuenta, codigo):
    """Un omitido está BIEN REPORTADO cuando la pantalla puede hacer algo con él.

    O sea: dice a QUIÉN (nombre para leer, id para llevarlo allá o regenerarle solo a él),
    de qué CUENTA es en las palabras del dueño ("leche"/"flete"), el motivo en CÓDIGO —para
    agrupar y ponerle su botón sin buscar palabras dentro de una frase en español— y el
    motivo REDACTADO, que no puede venir vacío ni ser un "no se puede" pelado.
    """
    assert omitida["tercero_id"] == tercero["id"], omitida
    assert omitida["tercero_nombre"] == tercero["nombre"], omitida
    assert omitida["cuenta"] == cuenta, omitida
    assert omitida["motivo_codigo"] == codigo, omitida
    assert omitida["tercero_nombre"] in omitida["motivo"], omitida["motivo"]
    assert len(omitida["motivo"]) > 60, omitida["motivo"]


def _leche_sin_liquidar(client, h, desde=None, hasta=None):
    """La plata de los días que ninguna liquidación de leche recogió todavía.

    Con `desde`/`hasta` se mide SOLO dentro de un período, que es como hay que medirla en
    medio de una cadena de quincenas: los días de las quincenas que todavía no se han
    corrido también están sin liquidar y no son el hueco que se está buscando.
    """
    return sum(
        (
            D(r["valor_neto"])
            for r in _recepciones(client, h)
            if r["liquidacion_id"] is None
            and (desde is None or r["fecha"] >= desde)
            and (hasta is None or r["fecha"] <= hasta)
        ),
        CERO,
    )


# ===========================================================================
# MITAD A — QUE LA CORRIDA NO SE TUMBE NUNCA
# ===========================================================================
def test_1_un_cruce_entre_ocho_sanos_no_tumba_la_corrida_ni_le_estorba_al_flete(
    client, base_datos
):
    """UN tercero con período cruzado entre OCHO sanos, en la corrida tipo="ambos".

    Es el caso que más costaría, y el que la pantalla manda: nueve proveedores, uno con una
    quincena que se cruza. Los ocho sanos salen con su comprobante, el noveno sale
    reportado, Y EL FLETE TAMBIÉN SALE: el cruce de la leche de uno no puede estorbarle la
    liquidación del transportador, que es otra cuenta.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 1. UN CRUCE ENTRE OCHO SANOS (tipo=ambos) =====")
    ruta = _ruta(client, h, "Napoles")
    t = _transportador(client, h, "Don Chucho", [(ruta, "200")])
    provs = [_proveedor(client, h, f"Prov {i}") for i in range(9)]
    for p in provs:
        _recepcion(client, h, p, "2026-06-02", "100", t=t, ruta=ruta)

    # Al noveno se le genera SOLO la leche de la quincena 01-15: eso es lo que se va a
    # cruzar con el rango 10-20 de abajo.
    culpable = provs[-1]
    q1 = _ok(client, h, *Q1, proveedor_id=culpable["id"])[0]
    assert D(q1["valor_total"]) == D("180000")

    # Un día nuevo para los nueve, dentro de un rango que se monta con esa quincena.
    for p in provs:
        _recepcion(client, h, p, "2026-06-12", "50", precio="2000", t=t, ruta=ruta)
    corrida = _corrida(client, h, "2026-06-10", "2026-06-20", tipo="ambos")
    omitida = _un_omitido(corrida)
    de_leche = [liq for liq in corrida["generadas"] if liq["tipo"] == "proveedor"]
    de_flete = [liq for liq in corrida["generadas"] if liq["tipo"] == "transportador"]
    print(f"      corrida 10-20 'ambos' -> {len(de_leche)} de leche, {len(de_flete)} de "
          f"flete, omitida: {omitida['tercero_nombre']} ({omitida['cuenta']})")

    # LOS OCHO SANOS, cada uno con sus $100.000.
    assert len(de_leche) == 8
    assert {liq["proveedor_id"] for liq in de_leche} == {p["id"] for p in provs[:-1]}
    assert all(D(liq["valor_total"]) == D("100000") for liq in de_leche)
    # EL FLETE SALIÓ: los 450 L del período a $200.
    assert len(de_flete) == 1
    assert D(de_flete[0]["valor_total"]) == D("90000")
    # Y EL DEL CRUCE, REPORTADO.
    _bien_reportado(omitida, tercero=culpable, cuenta="leche", codigo="periodo_cruzado")

    libro = _libro(client, h, "ocho sanos, uno reportado, y el flete")
    _cuadra(libro, "1")
    assert libro["leche"] == D("180000") + D("800000") + D("90000")


def test_2_varios_cruces_a_la_vez_y_cada_uno_sale_con_su_propio_motivo(client, base_datos):
    """TRES cruces en la misma corrida de diez: los siete sanos salen y los TRES se
    reportan, cada uno con SU nombre —no uno solo, no "hubo problemas"—.

    Importa que sean los tres: si la corrida reportara solo el primero que encuentra, el
    dueño arreglaría ese, volvería a oprimir Generar y se encontraría con otro, y con otro.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 2. VARIOS CRUCES A LA VEZ =====")
    provs = [_proveedor(client, h, f"Prov {i}") for i in range(10)]
    for p in provs:
        _recepcion(client, h, p, "2026-06-02", "100")
    cruzados = provs[:3]
    for p in cruzados:
        assert len(_ok(client, h, *Q1, proveedor_id=p["id"])) == 1
    for p in provs:
        _recepcion(client, h, p, "2026-06-12", "50", precio="2000")

    corrida = _corrida(client, h, "2026-06-10", "2026-06-20")
    print(f"      corrida 10-20 para diez -> generadas={len(corrida['generadas'])}, "
          f"omitidas={[o['tercero_nombre'] for o in corrida['omitidas']]}")
    assert len(corrida["generadas"]) == 7
    assert {liq["proveedor_id"] for liq in corrida["generadas"]} == {
        p["id"] for p in provs[3:]
    }
    # LOS TRES, cada uno con su nombre y su motivo redactado.
    assert len(corrida["omitidas"]) == 3
    por_id = {o["tercero_id"]: o for o in corrida["omitidas"]}
    assert set(por_id) == {p["id"] for p in cruzados}
    for p in cruzados:
        _bien_reportado(por_id[p["id"]], tercero=p, cuenta="leche", codigo="periodo_cruzado")

    libro = _libro(client, h, "siete salieron, tres reportados")
    _cuadra(libro, "2")
    assert libro["leche"] == D("540000") + D("700000")


def test_3_el_cruce_de_un_transportador_no_tumba_ni_al_otro_transportador(
    client, base_datos
):
    """EL CRUCE DEL FLETE, que era el hallazgo de mayor alcance: un transportador recoge la
    leche de MUCHOS proveedores y el flete va PRIMERO en la corrida, así que su cruce
    rebotaba antes de que a la leche le tocara turno.

    Acá se aprieta un paso más: DOS transportadores, uno cruzado y uno sano. Tienen que
    salir las dos mitades —el flete del sano Y la leche de los dos proveedores— y el
    cruzado tiene que salir reportado.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 3. DOS TRANSPORTADORES, UNO CRUZADO =====")
    ruta_a = _ruta(client, h, "Napoles")
    chucho = _transportador(client, h, "Don Chucho", [(ruta_a, "200")])
    prov_a = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov_a, "2026-06-02", "100", t=chucho, ruta=ruta_a)
    # Solo el flete de Don Chucho: es lo que se va a cruzar.
    flete_viejo = _ok(client, h, *Q1, tipo="transportador")[0]
    assert D(flete_viejo["valor_total"]) == D("20000")

    ruta_b = _ruta(client, h, "La Y")
    pedro = _transportador(client, h, "Don Pedro", [(ruta_b, "300")])
    prov_b = _proveedor(client, h, "Marleny")
    _recepcion(client, h, prov_b, "2026-06-05", "100", t=pedro, ruta=ruta_b)
    # Un día nuevo de Don Chucho: ahora tiene flete pendiente Y una liquidación de flete
    # del mismo período, o sea el cruce.
    _recepcion(client, h, prov_a, "2026-06-07", "100", t=chucho, ruta=ruta_a)

    corrida = _corrida(client, h, *Q1, tipo="ambos")
    omitida = _un_omitido(corrida)
    de_leche = [liq for liq in corrida["generadas"] if liq["tipo"] == "proveedor"]
    de_flete = [liq for liq in corrida["generadas"] if liq["tipo"] == "transportador"]
    print(f"      corrida 'ambos' -> {len(de_leche)} de leche, {len(de_flete)} de flete "
          f"({[liq['transportador_nombre'] for liq in de_flete]}), "
          f"omitida: {omitida['tercero_nombre']} ({omitida['cuenta']})")

    # EL FLETE DEL SANO SALIÓ: los 100 L de Don Pedro a $300.
    assert len(de_flete) == 1
    assert de_flete[0]["transportador_id"] == pedro["id"]
    assert D(de_flete[0]["valor_total"]) == D("30000")
    # Y LA LECHE DE LOS DOS: Henri con sus dos días ($360.000) y Marleny con el suyo.
    assert len(de_leche) == 2
    por_prov = {liq["proveedor_id"]: liq for liq in de_leche}
    assert D(por_prov[prov_a["id"]]["valor_total"]) == D("360000")
    assert D(por_prov[prov_b["id"]]["valor_total"]) == D("180000")
    # EL CRUZADO, reportado como flete.
    _bien_reportado(omitida, tercero=chucho, cuenta="flete", codigo="periodo_cruzado")

    libro = _libro(client, h, "el flete del sano salió, el cruzado se reportó")
    _cuadra(libro, "3")
    assert libro["leche"] == D("20000") + D("30000") + D("360000") + D("180000")


def test_4_el_transportador_sin_tarifa_sale_reportado_y_su_flete_entra_al_arreglarla(
    client, base_datos
):
    """EL TRANSPORTADOR SIN TARIFA: el otro camino por donde un tercero se queda sin
    comprobante, y el más común de los dos —la tarifa por omisión es CERO, así que el
    transportador recién creado al que nadie le llenó la tarifa cae justo acá—.

    No tumba la corrida (eso ya era así), pero además YA NO SE SALTA EN SILENCIO: sale
    reportado diciendo los LITROS que están esperando, que es lo que hace que el dueño
    entienda que esto no es "a este no le tocaba nada". Y su flete no se pierde: se le
    arregla la tarifa, se genera otra vez y entra COMPLETO.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 4. EL TRANSPORTADOR SIN TARIFA =====")
    ruta_a = _ruta(client, h, "Napoles")
    chucho = _transportador(client, h, "Don Chucho", [(ruta_a, "200")])
    ruta_b = _ruta(client, h, "La Y")
    # Sin rutas y con la general en cero: el caso normal de quien no llenó la tarifa.
    pelado = _transportador(client, h, "Don Pelado", [])
    prov_a = _proveedor(client, h, "Henri C")
    prov_b = _proveedor(client, h, "Marleny")
    _recepcion(client, h, prov_a, "2026-06-02", "100", t=chucho, ruta=ruta_a)
    dia_sin_tarifa = _recepcion(client, h, prov_b, "2026-06-03", "100", t=pelado, ruta=ruta_b)
    # La foto del flete de ese día nació en cero, porque no hay tarifa de dónde sacarla.
    assert D(dia_sin_tarifa["valor_transporte"]) == CERO

    corrida = _corrida(client, h, *Q1, tipo="ambos")
    omitida = _un_omitido(corrida)
    de_flete = [liq for liq in corrida["generadas"] if liq["tipo"] == "transportador"]
    print(f"      corrida 'ambos' -> {len(corrida['generadas'])} generadas, "
          f"omitida: {omitida['motivo'][:150]}")
    # Los sanos salieron: la leche de los dos y el flete de Don Chucho.
    assert len(corrida["generadas"]) == 3
    assert len(de_flete) == 1 and de_flete[0]["transportador_id"] == chucho["id"]
    # Y el pelado, reportado con los litros que están esperando.
    _bien_reportado(omitida, tercero=pelado, cuenta="flete", codigo="flete_sin_tarifa")
    assert "no tiene tarifa de flete" in omitida["motivo"]
    assert "100 L" in omitida["motivo"], omitida["motivo"]
    libro = _libro(client, h, "el sin tarifa se reportó")
    _cuadra(libro, "4")
    assert libro["leche"] == D("360000") + D("20000")

    # SE LE ARREGLA LA TARIFA Y SU FLETE ENTRA COMPLETO: 100 L a $350.
    assert client.put(
        f"{TRANS}/{pelado['id']}",
        json={"rutas": [{"ruta_id": ruta_b["id"], "valor_transporte": "350"}]},
        headers=h,
    ).status_code == 200
    rescatado = _ok(client, h, *Q1, tipo="transportador")
    print(f"      con la tarifa puesta -> total={rescatado[0]['valor_total']}")
    assert len(rescatado) == 1
    assert rescatado[0]["transportador_id"] == pelado["id"]
    assert D(rescatado[0]["valor_total"]) == D("35000")
    libro = _libro(client, h, "el flete del pelado entró completo")
    _cuadra(libro, "4 final")
    assert libro["leche"] == D("360000") + D("20000") + D("35000")


def test_5_el_que_no_entrego_leche_no_sale_ni_generado_ni_omitido(client, base_datos):
    """EL QUE NO ENTREGÓ NADA NO ES UN OMITIDO, y la diferencia es todo el punto de
    `omitidas`: reportarlo mandaría al dueño a arreglar algo que no está roto, y un aviso
    que sale siempre es un aviso que nadie lee. `omitidas` es "esto le falta", no "esto no
    le tocaba".

    Se mide con el proveedor quieto (tiene un anticipo entregado y ni un litro), con el
    transportador sin recepciones y con un período entero vacío, dos veces seguidas.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 5. EL QUE NO ENTREGÓ NADA =====")
    ruta = _ruta(client, h, "Napoles")
    _transportador(client, h, "Don Quieto", [(ruta, "200")])   # sin un solo día
    henri = _proveedor(client, h, "Henri C")
    quieto = _proveedor(client, h, "El Quieto")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "50000", proveedor=quieto)

    corrida = _corrida(client, h, *Q1, tipo="ambos")
    print(f"      Q1 'ambos' -> generadas={len(corrida['generadas'])}, "
          f"omitidas={len(corrida['omitidas'])}")
    assert len(corrida["generadas"]) == 1
    assert corrida["generadas"][0]["proveedor_id"] == henri["id"]
    assert corrida["omitidas"] == [], (
        "se reportó a alguien a quien no le tocaba nada: el dueño saldría a arreglar lo "
        "que no está roto"
    )
    # El quieto pedido de a uno: nada, y sin reportarlo.
    corrida = _corrida(client, h, *Q1, proveedor_id=quieto["id"])
    assert corrida["generadas"] == [] and corrida["omitidas"] == []
    # Un período entero sin nada, dos veces seguidas.
    for _ in range(2):
        corrida = _corrida(client, h, "2026-09-01", "2026-09-15", tipo="ambos")
        assert corrida["generadas"] == [] and corrida["omitidas"] == []
    print("      el quieto y el período vacío -> ni generados ni reportados")
    libro = _libro(client, h, "el anticipo del quieto sigue suelto")
    _cuadra(libro, "5")
    assert libro["sueltos"] == D("50000")


def test_6_de_a_uno_con_proveedor_id_ni_rebota_ni_lo_arrastra_el_cruce_ajeno(
    client, base_datos
):
    """DE A UNO CON `proveedor_id`: el camino que la pantalla no tiene pero la API sí, y por
    el que el dueño va a entrar el día que se le ponga el botón.

    Las dos exigencias: al pedir el del cruce no rebota —se reporta— y al pedir a los otros
    dos el cruce ajeno no los toca.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 6. DE A UNO CON proveedor_id =====")
    henri = _proveedor(client, h, "Henri C")
    marleny = _proveedor(client, h, "Marleny")
    aleida = _proveedor(client, h, "Aleida")
    for p in (henri, marleny, aleida):
        _recepcion(client, h, p, "2026-06-02", "100")
    assert len(_ok(client, h, *Q1, proveedor_id=henri["id"])) == 1
    # El día olvidado de Henri: ahora él se cruza consigo mismo.
    _recepcion(client, h, henri, "2026-06-05", "30")

    corrida = _corrida(client, h, *Q1, proveedor_id=henri["id"])
    omitida = _un_omitido(corrida)
    print(f"      de a uno Henri (cruzado) -> generadas={len(corrida['generadas'])}, "
          f"omitida: {omitida['motivo'][:110]}")
    assert corrida["generadas"] == []
    _bien_reportado(omitida, tercero=henri, cuenta="leche", codigo="periodo_cruzado")
    # Y los otros dos, de a uno, salen enteros: el cruce de Henri no es de ellos.
    for p in (marleny, aleida):
        salieron = _ok(client, h, *Q1, proveedor_id=p["id"])
        print(f"      de a uno {p['nombre']} -> total={salieron[0]['valor_total']}")
        assert len(salieron) == 1
        assert D(salieron[0]["valor_total"]) == D("180000")
    libro = _libro(client, h, "de a uno, con un cruce ajeno")
    _cuadra(libro, "6")
    assert libro["leche"] == D("540000")


def test_7_los_dos_motivos_en_la_misma_corrida_y_los_dos_se_reportan(client, base_datos):
    """LOS DOS MOTIVOS A LA VEZ. Se omiten por caminos distintos y en momentos distintos de
    `generar` —el del flete sin tarifa dentro del recorrido del transportador, el del
    período cruzado antes de escribir— así que hay que medir que los dos llegan juntos a la
    misma respuesta y que ninguno se come al otro.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 7. LOS DOS MOTIVOS EN LA MISMA CORRIDA =====")
    ruta_a = _ruta(client, h, "Napoles")
    chucho = _transportador(client, h, "Don Chucho", [(ruta_a, "200")])
    ruta_b = _ruta(client, h, "La Y")
    pelado = _transportador(client, h, "Don Pelado", [])
    prov_a = _proveedor(client, h, "Henri C")
    prov_b = _proveedor(client, h, "Marleny")
    prov_c = _proveedor(client, h, "Aleida")
    _recepcion(client, h, prov_a, "2026-06-02", "100", t=chucho, ruta=ruta_a)
    _recepcion(client, h, prov_b, "2026-06-03", "100", t=pelado, ruta=ruta_b)
    _recepcion(client, h, prov_c, "2026-06-04", "100")
    # El cruce: a Aleida se le genera la quincena y después le entra un día olvidado.
    assert len(_ok(client, h, *Q1, proveedor_id=prov_c["id"])) == 1
    _recepcion(client, h, prov_c, "2026-06-06", "50")

    corrida = _corrida(client, h, *Q1, tipo="ambos")
    print(f"      corrida 'ambos' -> generadas={len(corrida['generadas'])}, omitidas="
          f"{[(o['tercero_nombre'], o['motivo_codigo']) for o in corrida['omitidas']]}")
    # Los sanos: la leche de Henri y de Marleny, y el flete de Don Chucho.
    assert len(corrida["generadas"]) == 3
    # Y LOS DOS MOTIVOS, cada uno con su tercero.
    assert len(corrida["omitidas"]) == 2
    por_codigo = {o["motivo_codigo"]: o for o in corrida["omitidas"]}
    assert set(por_codigo) == {"flete_sin_tarifa", "periodo_cruzado"}
    _bien_reportado(
        por_codigo["flete_sin_tarifa"], tercero=pelado, cuenta="flete",
        codigo="flete_sin_tarifa",
    )
    _bien_reportado(
        por_codigo["periodo_cruzado"], tercero=prov_c, cuenta="leche",
        codigo="periodo_cruzado",
    )
    libro = _libro(client, h, "los dos motivos reportados")
    _cuadra(libro, "7")
    # Aleida $180.000 + Henri $180.000 + Marleny $180.000 + el flete de Chucho $20.000.
    assert libro["leche"] == D("560000")


# ===========================================================================
# MITAD B — QUE LA PLATA CUADRE
# ===========================================================================
def test_8_la_cadena_de_cinco_quincenas_con_un_cruce_en_el_medio_cuadra_al_peso(
    client, base_datos
):
    """CINCO QUINCENAS SEGUIDAS PARA DOS PROVEEDORES, con un cruce en la del medio, medida
    con el libro en CADA paso.

    Lo que se ataca: que el salto de un tercero en Q3 no le desacomode la cadena de la
    deuda ni a él ni a la otra, ni deje una cifra a medio camino en las dos quincenas que
    vienen después. Al final se arregla el cruce y se paga todo lo pagable: de la caja
    tiene que salir EXACTAMENTE la leche liquidada, ni un peso más.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 8. CINCO QUINCENAS Y UN CRUCE EN EL MEDIO =====")
    henri = _proveedor(client, h, "Henri C")
    marleny = _proveedor(client, h, "Marleny")
    plan = [
        (Q1, "2026-06-02", "100", "1800"),   # 180.000 cada uno
        (Q2, "2026-06-20", "100", "2000"),   # 200.000
        (Q3, "2026-07-02", "120", "1900"),   # 228.000
        (Q4, "2026-07-20", "80", "2100"),    # 168.000
        (Q5, "2026-08-02", "150", "1700"),   # 255.000
    ]
    for quincena, dia, litros, precio in plan:
        for p in (henri, marleny):
            _recepcion(client, h, p, dia, litros, precio=precio)
    # Un anticipo grande a Henri en la primera, para que la deuda tenga que viajar.
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)

    # Q1 y Q2 normales.
    for quincena in (Q1, Q2):
        salieron = _ok(client, h, *quincena)
        assert len(salieron) == 2
        print(f"      {quincena[0]}: {[(liq['proveedor_nombre'], liq['saldo']) for liq in salieron]}")
        _cuadra(_libro(client, h, f"tras {quincena[0]}"), f"8 {quincena[0]}")
    # Q1 de Henri quedó debiendo $120.000 y Q2 se los cobró.
    henri_q1 = next(
        liq for liq in _todas(client, h)
        if liq.get("proveedor_id") == henri["id"] and liq["periodo_inicio"] == Q1[0]
    )
    assert D(henri_q1["le_queda_debiendo"]) == D("120000")

    # Q3: EL CRUCE. Se le genera de a uno a Henri, le entra un día olvidado y la corrida
    # completa lo salta reportándolo — Marleny sale igual.
    assert len(_ok(client, h, *Q3, proveedor_id=henri["id"])) == 1
    _recepcion(client, h, henri, "2026-07-05", "20", precio="1900")     # 38.000
    corrida = _corrida(client, h, *Q3)
    omitida = _un_omitido(corrida)
    print(f"      {Q3[0]} con el cruce -> generadas="
          f"{[liq['proveedor_nombre'] for liq in corrida['generadas']]}, "
          f"omitida: {omitida['tercero_nombre']}")
    assert [liq["proveedor_id"] for liq in corrida["generadas"]] == [marleny["id"]]
    _bien_reportado(omitida, tercero=henri, cuenta="leche", codigo="periodo_cruzado")
    _cuadra(_libro(client, h, "tras Q3 con el omitido"), "8 Q3")
    # LA LECHE DEL OMITIDO ESTÁ SIN LIQUIDAR, y es medible: dentro de Q3 lo único que
    # ninguna liquidación recogió son los $38.000 del día olvidado de Henri.
    assert _leche_sin_liquidar(client, h, *Q3) == D("38000")

    # Q4 y Q5 siguen normales para los dos: el salto de Q3 no desacomodó la cadena.
    for quincena in (Q4, Q5):
        salieron = _ok(client, h, *quincena)
        assert len(salieron) == 2
        print(f"      {quincena[0]}: "
              f"{[(liq['proveedor_nombre'], liq['saldo']) for liq in salieron]}")
        _cuadra(_libro(client, h, f"tras {quincena[0]}"), f"8 {quincena[0]}")

    # SE ARREGLA EL CRUCE: se anula la Q3 de Henri y se vuelve a correr la quincena. Entra
    # completa, con el día olvidado adentro: $228.000 + $38.000.
    henri_q3 = next(
        liq for liq in _todas(client, h)
        if liq.get("proveedor_id") == henri["id"]
        and liq["periodo_inicio"] == Q3[0]
        and liq["estado"] != "anulada"
    )
    assert _anular(client, h, henri_q3["id"]).status_code == 200
    rehecha = _ok(client, h, *Q3)
    print(f"      Q3 rehecha -> total={rehecha[0]['valor_total']}")
    assert len(rehecha) == 1
    assert D(rehecha[0]["valor_total"]) == D("266000")
    assert _leche_sin_liquidar(client, h) == CERO, "quedó leche sin comprobante"
    # Ni un litro de las cinco quincenas quedó afuera.

    # Y SE PAGA TODO LO PAGABLE: de la caja sale la leche y nada más.
    libro = _libro(client, h, "la cadena completa")
    _cuadra(libro, "8 cadena")
    for liq in list(libro["vivas"]):
        if D(liq["saldo"]) > CERO:
            assert _aprobar(client, h, liq["id"]).status_code == 200
            assert _pagar(client, h, liq["id"]).status_code == 200
    final = _libro(client, h, "la cadena, pagada")
    _cuadra(final, "8 final")
    print(f"      SALIÓ DE LA CAJA {final['anticipos'] + final['pagado']} "
          f"POR {final['leche']} DE LECHE")
    # Henri: 180 + 200 + 266 + 168 + 255 = 1.069.000
    # Marleny: 180 + 200 + 228 + 168 + 255 = 1.031.000  ->  2.100.000 de leche
    assert final["leche"] == D("2100000")
    assert final["anticipos"] == D("300000")
    assert final["pagado"] == D("1800000")
    # LA CUENTA DEL DUEÑO, al peso: lo que salió de la caja es la leche liquidada.
    assert final["anticipos"] + final["pagado"] == final["leche"] == D("2100000")
    assert final["debiendo"] == CERO
    assert final["por_pagar"] == CERO


def test_9_anular_y_regenerar_por_la_corrida_completa_en_los_dos_ordenes(
    client, base_datos
):
    """ANULAR Y REGENERAR POR LA CORRIDA COMPLETA (sin `proveedor_id`, que es lo único que
    la pantalla sabe hacer), EN LOS DOS ÓRDENES, con dos proveedores a la vez.

    · AL DERECHO (primero la quincena vieja): la deuda vuelve a su sitio y de la caja sale
      exactamente la leche.
    · AL REVÉS (primero la nueva): el anticipo viejo se lo lleva la quincena NUEVA
      —`pendientes_de` mira solo `fecha <= periodo_fin`— y la vieja queda sin nada que
      descontar, así que se le paga completa. LA PLATA NO SE PIERDE (queda anotada como
      deuda) pero SALE: es el hueco de ORDEN que ya está medido en la prueba 3c de
      tests/test_liquidacion_deuda_arrastrada_plata.py, y acá se mide que por el camino de
      la corrida completa las cifras son las mismas y el libro cuadra igual.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 9. ANULAR Y REGENERAR POR LA CORRIDA, EN LOS DOS ÓRDENES =====")
    provs = [_proveedor(client, h, n) for n in ("Henri C", "Marleny")]
    for p in provs:
        _recepcion(client, h, p, "2026-06-02", "100")               # 180.000
        _anticipo(client, h, "2026-06-01", "300000", proveedor=p)
        _recepcion(client, h, p, "2026-06-20", "100", precio="2500")  # 250.000

    def _vivas_de(periodo):
        return [
            liq for liq in _todas(client, h)
            if liq["estado"] != "anulada" and liq["periodo_inicio"] == periodo[0]
        ]

    def _tumbar_las_dos(ids):
        """Anular las dos quincenas de esos proveedores, en el único orden posible."""
        # Q1 está congelada mientras Q2 le cobre la deuda: hay que anular Q2 primero, y
        # eso lo dice el propio mensaje de error, así que se comprueba que rebota.
        for liq in _vivas_de(Q1):
            if liq.get("proveedor_id") in ids:
                assert _anular(client, h, liq["id"]).status_code == 422
        for periodo in (Q2, Q1):
            for liq in _vivas_de(periodo):
                if liq.get("proveedor_id") in ids:
                    assert _anular(client, h, liq["id"]).status_code == 200
        _cuadra(_libro(client, h, "las dos quincenas anuladas"), "9 anuladas")

    # --- AL DERECHO: se generan, se tumban las dos y se REGENERAN Q1 y después Q2.
    for periodo in (Q1, Q2):
        assert len(_ok(client, h, *periodo)) == 2
    _tumbar_las_dos({p["id"] for p in provs})
    for periodo in (Q1, Q2):
        assert len(_ok(client, h, *periodo)) == 2
        _cuadra(_libro(client, h, f"al derecho: {periodo[0]}"), f"9 derecho {periodo[0]}")
    derecho = _libro(client, h, "al derecho, regeneradas")
    for liq in _vivas_de(Q2):
        assert D(liq["saldo_anterior"]) == D("120000")
        assert D(liq["saldo"]) == D("130000")
    for liq in list(derecho["vivas"]):
        if D(liq["saldo"]) > CERO:
            assert _aprobar(client, h, liq["id"]).status_code == 200
            assert _pagar(client, h, liq["id"]).status_code == 200
    derecho = _libro(client, h, "al derecho, pagado")
    _cuadra(derecho, "9 derecho final")
    salio_derecho = derecho["anticipos"] + derecho["pagado"]
    print(f"      AL DERECHO salió {salio_derecho} por {derecho['leche']} de leche, "
          f"debiendo {derecho['debiendo']}")
    # $430.000 de leche por cabeza: $600.000 de anticipos + $260.000 pagados.
    assert derecho["leche"] == D("860000")
    assert salio_derecho == D("860000")
    assert derecho["debiendo"] == CERO

    # --- AL REVÉS: Q2 y después Q1. Hay que soltar las cuatro primero, y las pagadas no
    #     se anulan, así que se hace sobre un par nuevo de proveedores.
    print("\n      --- ahora al revés, con otro par de proveedores ---")
    provs = [_proveedor(client, h, n) for n in ("Aleida", "Rosalba")]
    for p in provs:
        _recepcion(client, h, p, "2026-06-02", "100")
        _anticipo(client, h, "2026-06-01", "300000", proveedor=p)
        _recepcion(client, h, p, "2026-06-20", "100", precio="2500")
    # Se generan al derecho, se tumban las dos quincenas de las nuevas y se rehacen al
    # revés. Las de Henri y Marleny están pagadas: la corrida no las vuelve a tocar.
    for periodo in (Q1, Q2):
        assert len(_ok(client, h, *periodo)) == 2
    nuevas_ids = {p["id"] for p in provs}
    _tumbar_las_dos(nuevas_ids)
    for periodo in (Q2, Q1):
        salieron = _ok(client, h, *periodo)
        assert len(salieron) == 2
        _cuadra(_libro(client, h, f"al revés: {periodo[0]}"), f"9 reves {periodo[0]}")

    # LAS CIFRAS DEL HUECO DE ORDEN: el anticipo se fue a la quincena NUEVA.
    for liq in _vivas_de(Q2):
        if liq.get("proveedor_id") in nuevas_ids:
            assert D(liq["anticipos"]) == D("300000")
            assert D(liq["le_queda_debiendo"]) == D("50000")
    for liq in _vivas_de(Q1):
        if liq.get("proveedor_id") in nuevas_ids:
            assert D(liq["anticipos"]) == CERO
            assert D(liq["saldo"]) == D("180000")
    libro = _libro(client, h, "al revés, generadas")
    _cuadra(libro, "9 reves")
    for liq in list(libro["vivas"]):
        if D(liq["saldo"]) > CERO:
            assert _aprobar(client, h, liq["id"]).status_code == 200
            assert _pagar(client, h, liq["id"]).status_code == 200
    final = _libro(client, h, "al revés, pagado")
    _cuadra(final, "9 final")
    salio = final["anticipos"] + final["pagado"]
    print(f"      TOTAL salió {salio} por {final['leche']} de leche, el tercero quedó "
          f"debiendo {final['debiendo']}")
    # Los cuatro dan $430.000 de leche: $1.720.000. Del par al revés salieron $100.000 de
    # más ($50.000 por cabeza) y quedaron anotados como deuda, no perdidos.
    assert final["leche"] == D("1720000")
    assert final["debiendo"] == D("100000")
    assert salio == D("1820000")
    assert salio - final["leche"] == final["debiendo"]


def test_10_la_leche_del_omitido_queda_sin_liquidar_y_entra_completa_al_arreglar_el_cruce(
    client, base_datos
):
    """LA CUENTA DEL OMITIDO, medida en las dos fotos: MIENTRAS ESTÁ OMITIDO SU LECHE NO
    ESTÁ LIQUIDADA —eso es el precio del salto y hay que decirlo— Y AL ARREGLAR EL CRUCE
    ENTRA COMPLETA, hasta el último peso.

    Es la exigencia que de verdad justifica reportar en vez de rebotar: si la leche del
    omitido se perdiera, o entrara incompleta, el salto sería peor que el error.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 10. LA LECHE DEL OMITIDO ENTRA COMPLETA =====")
    henri = _proveedor(client, h, "Henri C")
    marleny = _proveedor(client, h, "Marleny")
    _recepcion(client, h, henri, "2026-06-02", "100")            # 180.000
    q1 = _ok(client, h, *Q1)[0]
    assert D(q1["valor_total"]) == D("180000")

    # El día olvidado de Henri (que ya no cabe en su Q1) y el día de Marleny.
    _recepcion(client, h, henri, "2026-06-05", "30")             # 54.000
    _recepcion(client, h, marleny, "2026-06-03", "100")          # 180.000
    corrida = _corrida(client, h, *Q1)
    omitida = _un_omitido(corrida)
    print(f"      corrida Q1 -> generadas={len(corrida['generadas'])}, "
          f"omitida: {omitida['tercero_nombre']}")
    assert [liq["proveedor_id"] for liq in corrida["generadas"]] == [marleny["id"]]
    _bien_reportado(omitida, tercero=henri, cuenta="leche", codigo="periodo_cruzado")

    # FOTO 1: la leche de Marleny sí está liquidada; los $54.000 de Henri NO.
    con_omitido = _libro(client, h, "con Henri omitido")
    _cuadra(con_omitido, "10 omitido")
    assert con_omitido["leche"] == D("360000")
    sin_liquidar = _leche_sin_liquidar(client, h)
    print(f"      LECHE SIN LIQUIDAR mientras Henri está omitido: {sin_liquidar}")
    assert sin_liquidar == D("54000")

    # SE ARREGLA EL CRUCE por el flujo de corrección del sistema (anular y regenerar) y la
    # corrida completa vuelve a salir, ahora sin omitidos.
    assert _anular(client, h, q1["id"]).status_code == 200
    rehecha = _ok(client, h, *Q1)
    print(f"      Q1 rehecha -> {[(liq['proveedor_nombre'], liq['valor_total']) for liq in rehecha]}")
    assert len(rehecha) == 1
    assert rehecha[0]["proveedor_id"] == henri["id"]
    # FOTO 2: ENTRÓ COMPLETA, los dos días. Y no quedó un litro sin comprobante.
    assert D(rehecha[0]["valor_total"]) == D("234000")            # 180.000 + 54.000
    assert _leche_sin_liquidar(client, h) == CERO
    libro = _libro(client, h, "la leche del omitido entró completa")
    _cuadra(libro, "10 rehecha")
    # LA CIFRA QUE CIERRA: la leche de antes MÁS los $54.000 que estaban afuera.
    assert libro["leche"] == con_omitido["leche"] + sin_liquidar == D("414000")

    # Y pagándola, de la caja sale exactamente esa leche.
    for liq in list(libro["vivas"]):
        if D(liq["saldo"]) > CERO:
            assert _aprobar(client, h, liq["id"]).status_code == 200
            assert _pagar(client, h, liq["id"]).status_code == 200
    final = _libro(client, h, "todo pagado")
    _cuadra(final, "10 final")
    print(f"      SALIÓ DE LA CAJA {final['anticipos'] + final['pagado']} "
          f"POR {final['leche']} DE LECHE")
    assert final["anticipos"] + final["pagado"] == final["leche"] == D("414000")


def test_11_la_pagada_vieja_con_saldo_negativo_no_tumba_la_corrida_de_los_demas(
    client, base_datos, db_session
):
    """LA FILA VIEJA DE LA BASE DEL CLIENTE, dentro de una corrida de varios.

    La migración `e5c2b9a1f7d3` dice que en producción hay liquidaciones que el botón
    "Pagar" de antes dejó en 'pagada' con saldo negativo, y son orígenes de deuda válidos:
    por eso `solapada_para_periodo` las hace reservar sus fechas. Lo que se mide acá es que
    esa reserva NO se lleve por delante a los demás terceros de la corrida —el hueco de
    plata está tapado y el de la corrida también— y que el dueño de esa fila quede intacto.
    """
    from app.modules.liquidaciones.models import Liquidacion

    h = auth_headers(client, "admin.a")
    print("\n===== 11. LA 'PAGADA' VIEJA DENTRO DE LA CORRIDA DE VARIOS =====")
    henri = _proveedor(client, h, "Henri C")
    marleny = _proveedor(client, h, "Marleny")
    aleida = _proveedor(client, h, "Aleida")
    for p in (henri, marleny, aleida):
        _recepcion(client, h, p, "2026-06-02", "100")             # 180.000 cada uno
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    q1 = _ok(client, h, *Q1, proveedor_id=henri["id"])[0]
    assert D(q1["le_queda_debiendo"]) == D("120000")
    # Por la API no se llega a este estado: queda constancia.
    assert _aprobar(client, h, q1["id"]).status_code == 200
    assert _pagar(client, h, q1["id"]).status_code == 422
    fila = db_session.get(Liquidacion, __import__("uuid").UUID(q1["id"]))
    fila.estado = "pagada"
    db_session.flush()
    db_session.commit()
    print(f"      fila vieja simulada: estado={fila.estado} saldo={fila.saldo}")

    # Un día nuevo para los tres, en un rango que se monta con la fila vieja de Henri.
    for p in (henri, marleny, aleida):
        _recepcion(client, h, p, "2026-06-12", "50", precio="2000")   # 100.000
    corrida = _corrida(client, h, "2026-06-10", "2026-06-20")
    omitida = _un_omitido(corrida)
    print(f"      corrida 10-20 -> generadas={len(corrida['generadas'])}, "
          f"omitida: {omitida['tercero_nombre']}")
    # LOS DOS SANOS SALIERON; el de la fila vieja se reportó.
    assert {liq["proveedor_id"] for liq in corrida["generadas"]} == {
        marleny["id"], aleida["id"]
    }
    assert all(D(liq["valor_total"]) == D("100000") for liq in corrida["generadas"])
    _bien_reportado(omitida, tercero=henri, cuenta="leche", codigo="periodo_cruzado")

    # Y HENRI QUEDÓ INTACTO: su día del 12 sin liquidar, su anticipo donde estaba y su
    # deuda sin cobrar. Ni un peso salió por ese período.
    del_henri = [liq for liq in _todas(client, h) if liq.get("proveedor_id") == henri["id"]]
    assert [liq["id"] for liq in del_henri] == [q1["id"]]
    q1_ahora = _leer(client, h, q1["id"])
    assert D(q1_ahora["anticipos"]) == D("300000")
    assert D(q1_ahora["le_queda_debiendo"]) == D("120000")
    assert q1_ahora["deuda_trasladada_a_id"] is None
    libro = _libro(client, h, "los dos sanos salieron, la fila vieja quieta")
    _cuadra(libro, "11")
    assert libro["leche"] == D("180000") + D("200000")
    assert libro["anticipos"] + libro["pagado"] == D("300000")
    assert libro["debiendo"] == D("120000")

    # Y AHORA LA OTRA CARA, QUE EL GUARDIA NO HACE FAVORITISMOS: a las dos sanas les
    # quedó su propia liquidación del 10 al 20, así que si se pide la quincena 01-15 —que
    # se monta con ella— ELLAS TAMBIÉN se reportan, cada una por su propio cruce; y Henri
    # otra vez, porque su día del 12 sí cae dentro del 01-15. Los TRES reportados y la
    # corrida en pie: eso es lo que no se puede perder.
    corrida = _corrida(client, h, *Q1)
    print(f"      corrida Q1 -> generadas={len(corrida['generadas'])}, omitidas="
          f"{sorted(o['tercero_nombre'] for o in corrida['omitidas'])}")
    assert corrida["generadas"] == []
    assert {o["tercero_id"] for o in corrida["omitidas"]} == {
        henri["id"], marleny["id"], aleida["id"]
    }
    assert all(o["motivo_codigo"] == "periodo_cruzado" for o in corrida["omitidas"])

    # LA SALIDA, y por eso ninguna leche queda presa: el rango QUE NO SE MONTA con nada
    # (del 01 al 09) sí sale, y se lleva los días del 02 de las dos.
    salieron = _ok(client, h, "2026-06-01", "2026-06-09")
    print(f"      corrida 01-09 -> {[(liq['proveedor_nombre'], liq['valor_total']) for liq in salieron]}")
    assert len(salieron) == 2
    assert {liq["proveedor_id"] for liq in salieron} == {marleny["id"], aleida["id"]}
    assert all(D(liq["valor_total"]) == D("180000") for liq in salieron)
    final = _libro(client, h, "la leche de las otras dos, completa")
    _cuadra(final, "11 final")
    assert final["leche"] == D("380000") + D("360000")
    # Y no quedó leche sin comprobante más que la de Henri, que sigue esperando que se le
    # cobre la deuda de la fila vieja (su día del 12, $100.000).
    assert _leche_sin_liquidar(client, h) == D("100000")


def test_12_el_papel_el_preliminar_y_la_pantalla_dicen_la_misma_cifra(client, base_datos):
    """EL PAPEL OFICIAL, EL PAPEL PRELIMINAR Y LA PANTALLA: los tres suman exacto de arriba
    abajo Y los tres dicen la MISMA cifra de lo que va a salir de la caja.

    El dueño manda el papel MIRANDO LA PANTALLA, así que dos cifras para el mismo hecho es
    el defecto: el avance decía "saldo $250.000" y el papel del mismo avance decía que van a
    salir $130.000. Hoy el avance trae `deuda_pendiente` y las tres fuentes cuadran.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 12. EL PAPEL, EL PRELIMINAR Y LA PANTALLA =====")
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100")                 # 180.000
    _anticipo(client, h, "2026-06-01", "300000", proveedor=prov)
    q1 = _ok(client, h, *Q1)[0]
    assert D(q1["le_queda_debiendo"]) == D("120000")
    _recepcion(client, h, prov, "2026-06-20", "100", precio="2500")  # 250.000

    cuerpo = {
        "periodo_inicio": Q2[0], "periodo_fin": Q2[1],
        "tipo": "proveedor", "tercero_id": prov["id"],
    }
    # 1) LA PANTALLA (el JSON del avance).
    r = client.post(f"{API}/previsualizar", json=cuerpo, headers=h)
    assert r.status_code == 200, r.text
    avance = r.json()[0]
    print(f"      pantalla: total={avance['valor_total']} anticipos={avance['anticipos']} "
          f"saldo={avance['saldo']} deuda_pendiente={avance['deuda_pendiente']}")
    assert D(avance["valor_total"]) == D("250000")
    assert D(avance["anticipos"]) == CERO
    assert D(avance["saldo"]) == D("250000")
    assert D(avance["deuda_pendiente"]) == D("120000")
    va_a_salir = D(avance["saldo"]) - D(avance["deuda_pendiente"])
    assert va_a_salir == D("130000")

    # 2) EL PAPEL PRELIMINAR: suma exacto de arriba abajo y avisa la deuda con la MISMA
    #    cifra de la pantalla, y con la MISMA conclusión.
    r = client.post(f"{API}/previsualizar/pdf", json=cuerpo, headers=h)
    assert r.status_code == 200, r.text
    preliminar = texto_pdf(r.content)
    pre_total = renglon(preliminar, "VALOR TOTAL")
    pre_ant = renglon(preliminar, "Anticipos aplicados")
    pre_saldo = renglon(preliminar, "SALDO ESTIMADO")
    print(f"      preliminar: VALOR TOTAL={pre_total} anticipos={pre_ant} "
          f"SALDO ESTIMADO={pre_saldo}")
    assert pre_total - abs(pre_ant) == pre_saldo, f"el preliminar no suma:\n{preliminar}"
    assert (pre_total, pre_saldo) == (D("250000"), D("250000"))
    # Y dice la misma cifra que la pantalla, con las dos puntas: la deuda y lo que queda.
    assert "TODAVÍA NO DESCUENTA" in preliminar
    assert "$120.000" in preliminar, preliminar
    assert "va a quedar en $130.000" in preliminar, preliminar

    # 3) EL PAPEL OFICIAL: suma exacto y cierra en la misma cifra.
    q2 = _ok(client, h, *Q2)[0]
    papel = _pdf(client, h, q2["id"])
    total = renglon(papel, "VALOR TOTAL")
    anticipos = renglon(papel, "Anticipos aplicados")
    vieja = renglon(papel, "quedó debiendo de la quincena pasada")
    saldo = renglon(papel, "SALDO A PAGAR")
    print(f"      oficial: VALOR TOTAL={total} anticipos={anticipos} deuda_vieja={vieja} "
          f"SALDO A PAGAR={saldo}")
    assert total - abs(anticipos) - abs(vieja) == saldo, f"el papel no suma:\n{papel}"
    assert (total, abs(vieja), saldo) == (D("250000"), D("120000"), D("130000"))
    # El papel dice DE DÓNDE viene la deuda, con período y cifra.
    assert "01/06/2026 al 15/06/2026" in papel and "quedó debiendo $120.000" in papel

    # LAS TRES FUENTES, LA MISMA CIFRA para lo que sale de la caja.
    assert va_a_salir == saldo == D(q2["saldo"]) == D("130000"), (
        f"pantalla {va_a_salir}, papel {saldo}, liquidación {q2['saldo']}"
    )
    # Y el papel del ORIGEN también suma: $180.000 contra $300.000 de anticipo.
    papel_q1 = _pdf(client, h, q1["id"])
    t1 = renglon(papel_q1, "VALOR TOTAL")
    a1 = renglon(papel_q1, "Anticipos aplicados")
    debe1 = renglon(papel_q1, "QUEDA DEBIENDO")
    print(f"      oficial de Q1: VALOR TOTAL={t1} anticipos={a1} QUEDA DEBIENDO={debe1}")
    assert (t1, abs(a1), debe1) == (D("180000"), D("300000"), D("120000"))
    assert abs(a1) - t1 == debe1, f"el papel del origen no suma:\n{papel_q1}"
    _cuadra(_libro(client, h, "los tres papeles"), "12")


# ===========================================================================
# MITAD C — QUE EL SALTO NO DEJE BASURA
# ===========================================================================
def _foto_de_la_base(client, h):
    """La base por los cuatro lados que el salto podría dejar sucios."""
    return {
        "liquidaciones": {
            liq["id"]: (liq["estado"], liq["valor_total"], liq["anticipos"],
                        liq["saldo_anterior"], liq.get("deuda_trasladada_a_id"))
            for liq in _todas(client, h)
        },
        "recepciones": {
            r["id"]: (r["liquidacion_id"], r["liquidacion_transporte_id"],
                      D(r["valor_transporte"]))
            for r in _recepciones(client, h)
        },
        "anticipos": {a["id"]: a["liquidacion_id"] for a in _anticipos(client, h)},
    }


def _sin_punteros_al_vacio(client, h, donde):
    """NADA APUNTANDO A UNA LIQUIDACIÓN QUE NO EXISTE, ni entre los días ni entre los
    anticipos. Un día marcado con una liquidación que no está es leche que no aparece en
    ningún comprobante y que Generar tampoco vuelve a recoger: desaparece."""
    existen = {liq["id"] for liq in _todas(client, h)}
    for r in _recepciones(client, h):
        for campo in ("liquidacion_id", "liquidacion_transporte_id"):
            if r[campo] is not None:
                assert r[campo] in existen, (
                    f"[{donde}] el día {r['fecha']} está marcado en {campo}={r[campo]}, "
                    "que no existe: esa leche no está en ningún comprobante y Generar no "
                    "la vuelve a recoger"
                )
    for a in _anticipos(client, h):
        if a["liquidacion_id"] is not None:
            assert a["liquidacion_id"] in existen, (
                f"[{donde}] el anticipo del {a['fecha']} quedó apartado para la "
                f"liquidación {a['liquidacion_id']}, que no existe: nadie lo va a "
                "descontar nunca"
            )


def test_13_la_corrida_con_omitidos_no_deja_nada_a_medio_escribir(client, base_datos):
    """DESPUÉS DE UNA CORRIDA CON OMITIDOS, LA BASE TIENE QUE ESTAR LIMPIA.

    Se fotografía la base ANTES, se corre una quincena con los dos motivos de omisión a la
    vez y se compara renglón por renglón. Del lado de los omitidos NADA puede haber
    cambiado, y en toda la base no puede quedar:
      · una liquidación sin renglones (a medio escribir),
      · un día marcado con una liquidación que no existe,
      · un anticipo apartado para una liquidación que no se creó,
      · ni una foto de flete movida.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 13. LA CORRIDA CON OMITIDOS NO DEJA BASURA =====")
    ruta_a = _ruta(client, h, "Napoles")
    chucho = _transportador(client, h, "Don Chucho", [(ruta_a, "200")])
    ruta_b = _ruta(client, h, "La Y")
    pelado = _transportador(client, h, "Don Pelado", [])
    sanos = [_proveedor(client, h, f"Sano {i}") for i in range(4)]
    cruzado = _proveedor(client, h, "El Cruzado")
    del_pelado = _proveedor(client, h, "La del Pelado")
    for p in sanos:
        _recepcion(client, h, p, "2026-06-02", "100", t=chucho, ruta=ruta_a)
        _anticipo(client, h, "2026-06-01", "50000", proveedor=p)
    _recepcion(client, h, del_pelado, "2026-06-03", "100", t=pelado, ruta=ruta_b)
    # El cruzado: se le genera la quincena, le entra un día olvidado y además tiene un
    # anticipo esperando —que es lo que NO puede quedar apartado por una liquidación que
    # no se creó—.
    _recepcion(client, h, cruzado, "2026-06-04", "100")
    assert len(_ok(client, h, *Q1, proveedor_id=cruzado["id"])) == 1
    _recepcion(client, h, cruzado, "2026-06-06", "50")
    _anticipo(client, h, "2026-06-06", "400000", proveedor=cruzado)

    antes = _foto_de_la_base(client, h)
    libro_antes = _libro(client, h, "antes de la corrida")
    _cuadra(libro_antes, "13 antes")
    _sin_punteros_al_vacio(client, h, "13 antes")

    corrida = _corrida(client, h, *Q1, tipo="ambos")
    print(f"      corrida 'ambos' -> generadas={len(corrida['generadas'])}, omitidas="
          f"{[(o['tercero_nombre'], o['motivo_codigo']) for o in corrida['omitidas']]}")
    assert len(corrida["omitidas"]) == 2
    assert {o["motivo_codigo"] for o in corrida["omitidas"]} == {
        "periodo_cruzado", "flete_sin_tarifa"
    }
    despues = _foto_de_la_base(client, h)

    # 1) NINGUNA LIQUIDACIÓN A MEDIO ESCRIBIR: todas con renglones y con su tercero.
    for liq in _todas(client, h):
        completa = _leer(client, h, liq["id"])
        assert completa["detalles"], (
            f"la liquidación {liq['id']} quedó SIN RENGLONES (a medio escribir)"
        )
        assert completa["proveedor_id"] or completa["transportador_id"]
        suma = sum((D(d["valor"]) for d in completa["detalles"]), CERO)
        assert suma == D(completa["valor_total"]), (
            f"la liquidación {liq['id']} dice {completa['valor_total']} y sus renglones "
            f"suman {suma}"
        )

    # 2) NI UN PUNTERO AL VACÍO, ni entre los días ni entre los anticipos.
    _sin_punteros_al_vacio(client, h, "13 despues")

    # 3) LO DEL CRUZADO, INTACTO PIEZA POR PIEZA: sus liquidaciones como estaban, su día
    #    olvidado sin marcar y su anticipo de $400.000 sin apartar.
    ids_viejos = set(antes["liquidaciones"])
    nuevas = set(despues["liquidaciones"]) - ids_viejos
    assert antes["liquidaciones"] == {
        k: v for k, v in despues["liquidaciones"].items() if k in ids_viejos
    }, "una liquidación que ya existía cambió durante la corrida"
    del_cruzado = [
        r for r in _recepciones(client, h) if r["proveedor_id"] == cruzado["id"]
    ]
    olvidado = next(r for r in del_cruzado if r["fecha"] == "2026-06-06")
    print(f"      el día olvidado del cruzado: liquidacion_id={olvidado['liquidacion_id']}")
    assert olvidado["liquidacion_id"] is None
    del_cruzado_ant = [
        a for a in _anticipos(client, h) if a.get("proveedor_id") == cruzado["id"]
    ]
    apartado = next(a for a in del_cruzado_ant if D(a["valor"]) == D("400000"))
    print(f"      el anticipo de 400.000: liquidacion_id={apartado['liquidacion_id']}")
    assert apartado["liquidacion_id"] is None, (
        "el anticipo del omitido quedó apartado para una liquidación que no se creó"
    )

    # 4) NINGUNA FOTO DE FLETE MOVIDA ni apartada en los días del omitido por falta de
    #    tarifa: sus fotos siguen igual porque `generar` ni las mira cuando no va a salir
    #    comprobante. Se mira SOLO el lado del flete a propósito: la LECHE de ese mismo
    #    día sí salió (el omitido es el transportador, no el proveedor), así que su
    #    `liquidacion_id` cambió y tenía que cambiar.
    for r in _recepciones(client, h):
        if r["transportador_id"] == pelado["id"]:
            _, flete_antes, foto_antes = antes["recepciones"][r["id"]]
            _, flete_despues, foto_despues = despues["recepciones"][r["id"]]
            assert (flete_despues, foto_despues) == (flete_antes, foto_antes), (
                f"le movieron el flete al día {r['fecha']} del que no tiene tarifa, y no "
                "salió comprobante que lo explique"
            )
            assert flete_despues is None

    # Y LA CORRIDA SÍ ESCRIBIÓ LO DE LOS SANOS, que es la otra mitad: cinco de leche
    # —los cuatro sanos y la del transportador sin tarifa, cuya LECHE sí sale— y el flete
    # de Don Chucho.
    print(f"      liquidaciones nuevas: {len(nuevas)}")
    assert len(nuevas) == 6
    libro = _libro(client, h, "despues de la corrida")
    _cuadra(libro, "13 despues")


def test_14_al_transportador_omitido_no_le_mueven_la_foto_del_flete_ni_con_otra_tarifa(
    client, base_datos
):
    """LA FOTO DEL FLETE DEL TRANSPORTADOR OMITIDO NO SE MUEVE, y la prueba es con la
    TARIFA CAMBIADA, que es donde se vería.

    Generar re-deriva el flete con la tarifa de HOY (`_rederivar_el_flete`) y eso reescribe
    la columna `valor_transporte` del día. Si el guardia del cruce se mirara DESPUÉS de esa
    línea, al transportador omitido le quedarían las fotos reescritas con una tarifa nueva
    SIN NINGÚN COMPROBANTE que las explique: el día diría $50.000 de flete y no habría papel
    donde aparezca esa cifra. Acá se le cambia la tarifa de $200 a $500 entre las dos
    corridas y se exige que la foto siga en los $20.000 con que nació.

    Y al arreglar el cruce, el flete entra con la tarifa de hoy —eso sí es a propósito: por
    ese flete no ha salido un peso, así que manda la tarifa viva—.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 14. LA FOTO DEL FLETE DEL OMITIDO, CON LA TARIFA CAMBIADA =====")
    ruta = _ruta(client, h, "Napoles")
    chucho = _transportador(client, h, "Don Chucho", [(ruta, "200")])
    prov = _proveedor(client, h, "Henri C")
    _recepcion(client, h, prov, "2026-06-02", "100", t=chucho, ruta=ruta)
    flete_viejo = _ok(client, h, *Q1, tipo="transportador")[0]
    assert D(flete_viejo["valor_total"]) == D("20000")

    # Un día nuevo, con la tarifa de $200 todavía: su foto nace en $20.000.
    nuevo = _recepcion(client, h, prov, "2026-06-07", "100", t=chucho, ruta=ruta)
    assert D(nuevo["valor_transporte"]) == D("20000")
    # Y AHORA LE CAMBIAN LA TARIFA a $500.
    assert client.put(
        f"{TRANS}/{chucho['id']}",
        json={"rutas": [{"ruta_id": ruta["id"], "valor_transporte": "500"}]},
        headers=h,
    ).status_code == 200

    fotos_antes = {
        r["id"]: D(r["valor_transporte"]) for r in _recepciones(client, h)
    }
    corrida = _corrida(client, h, *Q1, tipo="transportador")
    omitida = _un_omitido(corrida)
    print(f"      corrida de flete -> generadas={len(corrida['generadas'])}, "
          f"omitida: {omitida['tercero_nombre']} ({omitida['motivo_codigo']})")
    assert corrida["generadas"] == []
    _bien_reportado(omitida, tercero=chucho, cuenta="flete", codigo="periodo_cruzado")

    # NI UNA FOTO MOVIDA: la del día nuevo sigue en $20.000 y no en los $50.000 de la
    # tarifa nueva, porque no salió comprobante que los explicara.
    fotos_despues = {r["id"]: D(r["valor_transporte"]) for r in _recepciones(client, h)}
    print(f"      foto del día nuevo: antes {fotos_antes[nuevo['id']]} -> "
          f"después {fotos_despues[nuevo['id']]}")
    assert fotos_despues == fotos_antes, "le movieron las fotos del flete al omitido"
    assert fotos_despues[nuevo["id"]] == D("20000")
    _sin_punteros_al_vacio(client, h, "14")
    _cuadra(_libro(client, h, "el flete omitido, sin tocar"), "14")

    # SE ARREGLA EL CRUCE: se anula el flete viejo y entra completo CON LA TARIFA DE HOY,
    # los 200 L a $500. Ahora sí hay un comprobante que explica cada foto.
    assert _anular(client, h, flete_viejo["id"]).status_code == 200
    rehecho = _ok(client, h, *Q1, tipo="transportador")[0]
    print(f"      flete rehecho con la tarifa de hoy -> total={rehecho['valor_total']}")
    assert D(rehecho["valor_total"]) == D("100000")               # 200 L a $500
    detalle = _leer(client, h, rehecho["id"])
    suma_fotos = sum(
        (
            D(r["valor_transporte"])
            for r in _recepciones(client, h)
            if r["liquidacion_transporte_id"] == rehecho["id"]
        ),
        CERO,
    )
    # LAS FOTOS SUMAN EL RENGLÓN DEL COMPROBANTE: es lo que hace que el conductor pueda
    # reproducir su propia columna.
    assert suma_fotos == D(detalle["valor_total"]) == D("100000")
    _sin_punteros_al_vacio(client, h, "14 final")
    _cuadra(_libro(client, h, "el flete rehecho"), "14 final")


def test_15_la_corrida_repetida_con_el_mismo_omitido_no_acumula_basura(client, base_datos):
    """EL DUEÑO OPRIME GENERAR VARIAS VECES —es lo que hace cuando algo no le cuadra— y el
    omitido sigue omitido. Cinco corridas seguidas no pueden dejar cinco liquidaciones a
    medio hacer, ni mover un peso, ni cambiar el aviso: la misma corrida tiene que culpar
    siempre a la misma liquidación.
    """
    h = auth_headers(client, "admin.a")
    print("\n===== 15. GENERAR CINCO VECES CON EL MISMO OMITIDO =====")
    henri = _proveedor(client, h, "Henri C")
    marleny = _proveedor(client, h, "Marleny")
    _recepcion(client, h, henri, "2026-06-02", "100")
    _anticipo(client, h, "2026-06-01", "300000", proveedor=henri)
    _ok(client, h, *Q1, proveedor_id=henri["id"])
    _recepcion(client, h, henri, "2026-06-05", "30")
    _recepcion(client, h, marleny, "2026-06-03", "100")

    primera = _corrida(client, h, *Q1)
    assert len(primera["generadas"]) == 1 and len(primera["omitidas"]) == 1
    antes = _foto_de_la_base(client, h)
    libro_antes = _libro(client, h, "tras la primera corrida")
    _cuadra(libro_antes, "15 primera")

    motivos = set()
    for vuelta in range(5):
        corrida = _corrida(client, h, *Q1)
        # Marleny ya está liquidada, así que ahora no sale nada; Henri sigue reportado.
        assert corrida["generadas"] == [], f"vuelta {vuelta + 1}: salió algo de más"
        omitida = _un_omitido(corrida)
        motivos.add(omitida["motivo"])
        assert omitida["tercero_id"] == henri["id"]
        _sin_punteros_al_vacio(client, h, f"15 vuelta {vuelta + 1}")
    print(f"      cinco vueltas -> {len(motivos)} mensaje(s) distinto(s)")
    # EL MISMO AVISO SIEMPRE: si cambiara de vuelta en vuelta, el dueño creería que cada
    # Generar encontró un problema nuevo.
    assert len(motivos) == 1, motivos

    despues = _foto_de_la_base(client, h)
    assert despues == antes, "las corridas repetidas movieron algo en la base"
    final = _libro(client, h, "tras las cinco vueltas")
    _cuadra(final, "15 final")
    assert final["leche"] == libro_antes["leche"] == D("360000")
    assert final["anticipos"] + final["pagado"] == D("300000")
