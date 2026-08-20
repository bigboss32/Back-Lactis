"""EL FLETE DE DÍA FIJO NI SE MULTIPLICA NI SE PIERDE: dieciocho formas de intentarlo.

Cada prueba de acá agarra el fijo de $150.000 e intenta, por un camino distinto, que se
cobre más de una vez, que se evapore, o que el desglose deje de sumar la cifra grande.
Ninguna lo logra, y esa es la prueba: cada una exige EL COMPORTAMIENTO BUENO, con la
cifra escrita a mano para poder verificarla con calculadora.

LO QUE SOSTIENE LAS DIECIOCHO es una sola regla, la que da el dueño y que está escrita
en `tarifas.valor_del_grupo`: EN UN DÍA FIJO EL RENGLÓN VALE LA TARIFA, nunca la suma
de las fotos; las fotos son solo el reparto de esa cifra y su única obligación es
sumarla exacto. Invertida la dirección de la cuenta, ninguno de estos ataques puede
existir. Antes sí existían, y con estas cifras: corregirle los litros a un proveedor
del día dejaba el comprobante en $261.045,13; corrigiéndole a los cinco, en
$554.826,77; y esa cifra se aprobaba y se pagaba —$93.950,79 de más al conductor—.

La prueba que fija la regla de frente —hacerle TODO a un mismo día y exigir después de
cada operación que valga $150.000— está en `test_transporte_dia_fijo_la_regla.py`.

El escenario es el mismo del archivo hermano (`test_transporte_dia_fijo.py`) y se
reutiliza tal cual: Alex Agudelo con la ruta "A fabrica" a $150.000 POR DÍA y
"Napoles" a $242,76 POR LITRO.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.modules.liquidaciones.models import Liquidacion, LiquidacionDetalle
from app.modules.recepcion.models import RecepcionLeche
from tests.conftest import auth_headers
from tests.test_transporte_dia_fijo import (
    EL_DIA,
    FIJO,
    LIQUIDACIONES,
    PROVEEDORES,
    RECEPCIONES,
    D,
    _crear,
    _escenario,
    _liquidar_flete,
    _recibir,
    _renglones,
    _revisar_invariante,
    centavos,
)


def _fotos_del_dia(db_session, dia=EL_DIA, solo_activas=True):
    """Las fotos del flete de TODAS las recepciones de ese día (activas o todas)."""
    db_session.expire_all()
    y, m, d = (int(x) for x in dia.split("-"))
    filtros = [
        RecepcionLeche.fecha == date(y, m, d),
        RecepcionLeche.deleted_at.is_(None),
    ]
    if solo_activas:
        filtros.append(RecepcionLeche.estado == "activo")
    filas = db_session.scalars(select(RecepcionLeche).where(*filtros)).all()
    return {
        (f.cantidad_litros, str(f.id)[:8]): D(f.valor_transporte) for f in filas
    }


def _suma(fotos):
    return sum(fotos.values(), D(0))


def _ver(liq):
    """Imprime el comprobante renglón por renglón y devuelve la suma."""
    total = D(0)
    for r in _renglones(liq):
        print(
            f"    {r['fecha']}  {(r['ruta_nombre'] or '-'):<11}"
            f"{D(r['litros']):>9} L  [{r['modo_transporte']:<8}] "
            f"precio={D(r['precio_litro']):>9}  valor=${D(r['valor'])}"
        )
        total += D(r["valor"])
    print(f"    ------ suma de renglones = ${total} / "
          f"valor_transporte = ${D(liq['valor_transporte'])}")
    return total


# ===========================================================================
# ATAQUE 1 — corregirle los litros a una recepción de un día fijo QUE YA ESTÁ
#            EN UN COMPROBANTE EN BORRADOR
# ===========================================================================
def test_ataque_corregir_litros_de_un_dia_fijo_ya_liquidado_en_borrador(
    client, base_datos, db_session
):
    """El día vale $150.000 antes y después de corregirle los litros a un proveedor.

    EL CAMINO: se registran tres recepciones del 16/07 en la ruta fija, se GENERA el
    comprobante (queda en BORRADOR, sin aprobar ni pagar), y entonces alguien corrige
    los litros de una de las tres —que es lo más normal del mundo: se equivocó
    tecleando—.

    A MANO, lo que el comprobante tiene que decir en los dos momentos:

        antes:   82,00 + 137,45 + 96,30 = 315,75 L  →  $150.000,00
        después: 200,00 + 137,45 + 96,30 = 433,75 L →  $150.000,00

    El fijo NO depende de los litros. Si el comprobante dice otra cosa, el fijo se
    multiplicó.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    uno = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")

    liq_id = _liquidar_flete(client, h)["id"]
    antes = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print("\n===== ATAQUE 1: corregir litros de un dia fijo YA LIQUIDADO (borrador) =====")
    print(f"  estado del comprobante: {antes['estado']}")
    print("  el comprobante ANTES de corregir:")
    _ver(antes)
    print(f"  fotos del dia: {dict(sorted(_fotos_del_dia(db_session).items()))}")
    assert D(antes["valor_transporte"]) == FIJO
    assert _suma(_fotos_del_dia(db_session)) == FIJO

    # LA CORRECCIÓN: a Aurelio se le habían anotado 82,00 L y eran 200,00 L.
    r = client.put(f"{RECEPCIONES}/{uno['id']}", json={"cantidad_litros": "200"}, headers=h)
    assert r.status_code == 200, r.text

    despues = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print("\n  el comprobante DESPUES de corregir:")
    suma_renglones = _ver(despues)
    fotos = _fotos_del_dia(db_session)
    print(f"  fotos del dia: {dict(sorted(fotos.items()))}")
    print(f"  suma de las fotos = ${_suma(fotos)}")
    print(f"  el dia tenia que seguir valiendo ${FIJO}")

    assert D(despues["valor_transporte"]) == FIJO, (
        f"EL FIJO SE MULTIPLICO: corregirle los litros a un proveedor dejo el dia en "
        f"${D(despues['valor_transporte'])} cuando el dia completo vale ${FIJO}"
    )
    assert suma_renglones == D(despues["valor_transporte"])
    assert _suma(fotos) == FIJO, (
        f"las fotos del dia suman ${_suma(fotos)} y el dia vale ${FIJO}"
    )
    _revisar_invariante(despues, _suma(fotos))


# ===========================================================================
# ATAQUE 2 — apagar y borrar una recepción de un día fijo YA LIQUIDADO
# ===========================================================================
def test_ataque_apagar_una_recepcion_de_un_dia_fijo_ya_liquidado(
    client, base_datos, db_session
):
    """Apagar un día del comprobante en borrador no puede inflar ni evaporar el fijo.

    Tres recepciones del 16/07 fijas, comprobante generado (borrador), se apaga una.
    El día lo pagan las dos que quedan y sigue valiendo $150.000.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    dos = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    liq_id = _liquidar_flete(client, h)["id"]

    print("\n===== ATAQUE 2: apagar una recepcion de un dia fijo ya liquidado =====")
    r = client.put(f"{RECEPCIONES}/{dos['id']}", json={"estado": "inactivo"}, headers=h)
    print(f"  PUT estado=inactivo -> {r.status_code}")
    assert r.status_code == 200, r.text

    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print("  el comprobante:")
    _ver(liq)
    activas = _fotos_del_dia(db_session)
    todas = _fotos_del_dia(db_session, solo_activas=False)
    print(f"  fotos ACTIVAS: {dict(sorted(activas.items()))}  suma=${_suma(activas)}")
    print(f"  fotos TODAS  : {dict(sorted(todas.items()))}  suma=${_suma(todas)}")

    assert D(liq["valor_transporte"]) == FIJO, (
        f"apagar un dia dejo el comprobante en ${D(liq['valor_transporte'])}"
    )
    assert _suma(activas) == FIJO, (
        f"las fotos activas suman ${_suma(activas)} y el dia vale ${FIJO}"
    )
    _revisar_invariante(liq, _suma(activas))
    # La foto de la recepción APAGADA: no se le paga a nadie, así que no puede quedar
    # cargando un día completo fantasma.
    apagada = {k: v for k, v in todas.items() if k not in activas}
    print(f"  la foto de la recepcion apagada: {apagada}")
    assert _suma(apagada) <= FIJO, (
        f"la recepcion apagada quedo cargando ${_suma(apagada)} de flete fantasma"
    )


def test_ataque_borrar_una_recepcion_de_un_dia_fijo_ya_liquidado(
    client, base_datos, db_session
):
    """Borrar una recepción del comprobante en borrador: el día sigue en $150.000."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    uno = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    liq_id = _liquidar_flete(client, h)["id"]

    print("\n===== ATAQUE 3: borrar una recepcion de un dia fijo ya liquidado =====")
    r = client.delete(f"{RECEPCIONES}/{uno['id']}", headers=h)
    print(f"  DELETE -> {r.status_code}")
    assert r.status_code in (200, 204), r.text

    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print("  el comprobante:")
    _ver(liq)
    fotos = _fotos_del_dia(db_session)
    print(f"  fotos: {dict(sorted(fotos.items()))}  suma=${_suma(fotos)}")
    assert D(liq["valor_transporte"]) == FIJO, (
        f"borrar una recepcion dejo el dia en ${D(liq['valor_transporte'])}"
    )
    assert _suma(fotos) == FIJO
    _revisar_invariante(liq, _suma(fotos))


# ===========================================================================
# ATAQUE 4 — uno, dos, cinco y QUINCE proveedores el mismo día
# ===========================================================================
def test_ataque_uno_dos_cinco_y_quince_proveedores_el_mismo_dia(
    client, base_datos, db_session
):
    """En los cuatro casos el comprobante dice $150.000 y las fotos suman $150.000.

    QUINCE proveedores con litros distintos es el caso que más centavos mueve: quince
    partes de $150.000 ÷ 1.000 L exactos, con los pisos y el resto mayor.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    # Quince proveedores, todos de la ruta FIJA. Litros distintos y feos a propósito,
    # para que el reparto tenga fracciones de centavo que acomodar.
    litros = [D("13.37") + D(i) * D("7.11") for i in range(15)]
    for i in range(15):
        nombre = f"P{i:02d}"
        esc["proveedores"][nombre] = _crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "La Vega", "precio_litro": "1800",
            "ruta_id": esc["fabrica"]["id"]})

    # UN DÍA POR CASO: cada (día, ruta) es su propio grupo y su propio fijo.
    casos = {"2026-07-16": 1, "2026-07-17": 2, "2026-07-18": 5, "2026-07-19": 15}
    for dia, cuantos in casos.items():
        for i in range(cuantos):
            _recibir(client, h, esc, dia, f"P{i:02d}", str(litros[i]))

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    print("\n===== ATAQUE 4: uno, dos, cinco y QUINCE proveedores el mismo dia =====")
    suma = _ver(liq)
    renglones = _renglones(liq)
    assert len(renglones) == 4, f"un renglon por dia; salieron {len(renglones)}"

    for renglon, (dia, cuantos) in zip(renglones, casos.items()):
        fotos = _fotos_del_dia(db_session, dia)
        print(f"\n  --- {dia}: {cuantos} proveedor(es) ---")
        print(f"  litros: {[str(x) for x in litros[:cuantos]]}  "
              f"total={sum(litros[:cuantos], D(0))} L")
        print(f"  fotos ({len(fotos)}): "
              + " + ".join(f"${v}" for v in sorted(fotos.values()))
              + f" = ${_suma(fotos)}")
        assert renglon["fecha"] == dia
        assert renglon["modo_transporte"] == "dia_fijo"
        assert D(renglon["valor"]) == FIJO, (
            f"con {cuantos} proveedores el dia {dia} salio en ${D(renglon['valor'])} "
            f"(el error clasico seria ${FIJO * cuantos})"
        )
        assert len(fotos) == cuantos
        assert _suma(fotos) == FIJO, (
            f"las {cuantos} fotos del {dia} suman ${_suma(fotos)} y el dia vale ${FIJO}"
        )
        # Y ninguna parte absurda: nadie negativo, nadie por encima del fijo.
        for k, v in fotos.items():
            assert D(0) <= v <= FIJO, f"parte absurda {k} -> ${v}"

    print(f"\n  cuatro dias fijos = 4 x $150.000 = ${FIJO * 4}")
    assert suma == FIJO * 4
    assert D(liq["valor_transporte"]) == FIJO * 4
    _revisar_invariante(liq)


# ===========================================================================
# ATAQUE 5 — $150.000 entre SIETE: el reparto que no da redondo
# ===========================================================================
def test_ataque_ciento_cincuenta_mil_entre_siete_proveedores_iguales(
    client, base_datos, db_session
):
    """Siete proveedores con los MISMOS litros: $150.000 ÷ 7 = $21.428,5714285714...

    A MANO:
        21.428,571428... → piso $21.428,57 cada uno
        $21.428,57 × 7   = $149.999,99
        falta            = $0,01

    O sea que UNO de los siete se lleva un centavo más ($21.428,58) y los otros seis
    quedan en $21.428,57. Las siete partes suman EXACTO $150.000,00 y ninguna se
    desvía más de un centavo de su propia cuenta.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    for i in range(7):
        nombre = f"Siete{i}"
        esc["proveedores"][nombre] = _crear(client, h, PROVEEDORES, {
            "nombre": nombre, "vereda": "La Vega", "precio_litro": "1800",
            "ruta_id": esc["fabrica"]["id"]})
        _recibir(client, h, esc, EL_DIA, nombre, "100.00")

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    fotos = _fotos_del_dia(db_session)
    print("\n===== ATAQUE 5: $150.000 entre SIETE proveedores de 100 L =====")
    _ver(liq)
    partes = sorted(fotos.values())
    print(f"  las siete partes: {[str(p) for p in partes]}")
    print(f"  suman ${_suma(fotos)}   (a mano: 6 x 21.428,57 + 1 x 21.428,58)")
    assert _suma(fotos) == FIJO
    assert D(liq["valor_transporte"]) == FIJO
    assert partes.count(D("21428.57")) == 6, f"partes: {partes}"
    assert partes.count(D("21428.58")) == 1, f"partes: {partes}"
    _revisar_invariante(liq, _suma(fotos))


# ===========================================================================
# ATAQUE 6 — recalcular DOS y TRES veces seguidas: el papel no se mueve
# ===========================================================================
def test_ataque_recalcular_dos_y_tres_veces_no_mueve_el_papel(
    client, base_datos, db_session
):
    """Un día fijo mezclado con uno por litro, recalculado tres veces. Cifra idéntica.

    A MANO:
        16/07 A fabrica  (fijo)      82,00 + 137,45 + 96,30 = 315,75 L → $150.000,00
        17/07 Napoles    (x litro)   219,45 L × $242,76            = $ 53.273,68
                                                             total  = $203.273,68
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _recibir(client, h, esc, "2026-07-17", "Henri", "219.45")

    a_mano = FIJO + centavos(D("219.45") * D("242.76"))
    liq_id = _liquidar_flete(client, h)["id"]
    print("\n===== ATAQUE 6: recalcular dos y tres veces =====")
    print(f"  a mano: $150.000,00 (dia fijo) + $53.273,68 (219,45 x 242,76) = ${a_mano}")

    huellas = []
    for vuelta in range(4):
        if vuelta:
            r = client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)
            assert r.status_code == 200, r.text
        liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
        fotos = _fotos_del_dia(db_session)
        print(f"\n  --- vuelta {vuelta} (recalculos: {vuelta}) ---")
        _ver(liq)
        huella = (
            D(liq["valor_transporte"]),
            D(liq["total_litros"]),
            D(liq["precio_promedio"]),
            tuple(
                (r["fecha"], r["ruta_nombre"], D(r["litros"]),
                 D(r["precio_litro"]), D(r["valor"]), r["modo_transporte"])
                for r in _renglones(liq)
            ),
            tuple(sorted(str(v) for v in fotos.values())),
        )
        huellas.append(huella)
        assert D(liq["valor_transporte"]) == a_mano, (
            f"vuelta {vuelta}: el comprobante dice ${D(liq['valor_transporte'])} "
            f"y a mano da ${a_mano}"
        )
        _revisar_invariante(liq)

    for i, huella in enumerate(huellas[1:], start=1):
        assert huella == huellas[0], (
            f"el papel se movio en el recalculo {i}:\n  antes={huellas[0]}\n  ahora={huella}"
        )
    print("\n  las cuatro huellas son identicas: el papel no se movio")


# ===========================================================================
# ATAQUE 7 — dos rutas FIJAS el mismo día: son DOS fijos, uno por ruta
# ===========================================================================
def test_ataque_dos_rutas_fijas_el_mismo_dia_son_dos_fijos(client, base_datos, db_session):
    """Dos rutas con fijo distinto el mismo día: $150.000 + $90.000 = $240.000.

    Nada de cobrar un solo fijo (perder $90.000) ni de cobrar cuatro (una por
    recepción). Son DOS renglones de día completo, uno por ruta.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    # Se le cambia Napoles a DÍA FIJO de $90.000, dejando A fabrica en $150.000.
    otro_fijo = D("90000")
    r = client.put(
        f"/api/v1/transportadores/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(FIJO),
             "modo_transporte": "dia_fijo"},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": str(otro_fijo),
             "modo_transporte": "dia_fijo"},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    # Un segundo proveedor de Nápoles, para que esa ruta también tenga dos recepciones.
    esc["proveedores"]["Henri2"] = _crear(client, h, PROVEEDORES, {
        "nombre": "Henri2", "vereda": "Napoles", "precio_litro": "1800",
        "ruta_id": esc["napoles"]["id"]})

    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Henri", "50.00")
    _recibir(client, h, esc, EL_DIA, "Henri2", "70.00")

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    print("\n===== ATAQUE 7: dos rutas FIJAS el mismo dia =====")
    suma = _ver(liq)
    fotos = _fotos_del_dia(db_session)
    print(f"  fotos: {dict(sorted(fotos.items()))}  suma=${_suma(fotos)}")
    print(f"  a mano: $150.000 (A fabrica) + $90.000 (Napoles) = ${FIJO + otro_fijo}")

    renglones = _renglones(liq)
    assert len(renglones) == 2, f"tenian que ser DOS renglones y salieron {len(renglones)}"
    assert all(r["modo_transporte"] == "dia_fijo" for r in renglones)
    valores = sorted(D(r["valor"]) for r in renglones)
    assert valores == [otro_fijo, FIJO], f"los dos fijos salieron {valores}"
    assert D(liq["valor_transporte"]) == FIJO + otro_fijo
    assert suma == D(liq["valor_transporte"])
    assert _suma(fotos) == FIJO + otro_fijo
    _revisar_invariante(liq, _suma(fotos))


# ===========================================================================
# ATAQUE 8 — un fijo de $0 mezclado con un día por litro
# ===========================================================================
def test_ataque_un_fijo_de_cero_no_se_puede_leer_como_ya_cobrado(
    client, base_datos, db_session
):
    """Una ruta con fijo de $0,00 (el dueño decidió no cobrar ese viaje).

    El renglón sale en $0,00, que es correcto. LO QUE SE MIRA ACÁ es lo que el papel
    y la pantalla DICEN de ese cero: un fijo de $0 no es un día "ya cobrado en otro
    comprobante", y decirlo sería afirmar en el papel del conductor un hecho que no
    ocurrió.

    A MANO:
        16/07 A fabrica (fijo $0)   315,75 L  →  $0,00
        17/07 Napoles   (x litro)   219,45 L × $242,76 = $53.273,68
                                                total  = $53.273,68
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    r = client.put(
        f"/api/v1/transportadores/{esc['alex']['id']}",
        json={"rutas": [
            {"ruta_id": esc["fabrica"]["id"], "valor_transporte": "0",
             "modo_transporte": "dia_fijo"},
            {"ruta_id": esc["napoles"]["id"], "valor_transporte": "242.76",
             "modo_transporte": "litro"},
        ]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    _recibir(client, h, esc, "2026-07-17", "Henri", "219.45")

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    print("\n===== ATAQUE 8: un fijo de $0,00 =====")
    suma = _ver(liq)
    fotos = _fotos_del_dia(db_session)
    print(f"  fotos del 16/07: {dict(sorted(fotos.items()))}  suma=${_suma(fotos)}")
    a_mano = centavos(D("219.45") * D("242.76"))
    assert D(liq["valor_transporte"]) == a_mano
    assert suma == a_mano
    assert _suma(fotos) == D(0), "un fijo de $0 no le puede poner plata a ninguna foto"

    # EL PAPEL. Se busca la palabra "Ya cobrado", que solo puede aparecer cuando el día
    # de verdad se cobró en OTRO comprobante.
    pdf = client.get(f"{LIQUIDACIONES}/{liq['id']}/pdf", headers=h)
    assert pdf.status_code == 200, pdf.text
    from tests.test_transporte_dia_fijo import texto_pdf
    texto = texto_pdf(pdf.content)
    print(f"  ¿el PDF dice 'Ya cobrado'? {'Ya cobrado' in texto}")
    print(f"  ¿el PDF dice 'ya se le pago en otro comprobante'? "
          f"{'ya se le pagó en otro comprobante' in texto}")
    assert "Ya cobrado" not in texto, (
        "el papel le dice al conductor que ese dia YA SE LE PAGO en otro comprobante, "
        "y no es cierto: la tarifa fija de esa ruta es $0,00 y nunca se cobro"
    )


# ===========================================================================
# ATAQUE 9 — anular la liquidación y regenerar
# ===========================================================================
def test_ataque_anular_y_regenerar_un_dia_fijo(client, base_datos, db_session):
    """Anular el comprobante y volver a generar da EXACTAMENTE el mismo papel.

    Y con el agravante: entre el generar y el anular se anota tarde una cuarta
    recepción de ese mismo día (que entró en $0,00 porque el día ya estaba cobrado).
    Al anular, el día vuelve a estar por cobrar y las CUATRO se tienen que rearmar en
    un solo fijo de $150.000, sin perder a la que entró en cero.
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    primero = _liquidar_flete(client, h)
    print("\n===== ATAQUE 9: anular y regenerar =====")
    print(f"  primer comprobante: ${D(primero['valor_transporte'])}")
    assert D(primero["valor_transporte"]) == FIJO

    tarde = _recibir(client, h, esc, EL_DIA, "Ramiro", "60.00")
    print(f"  se anota tarde a Ramiro (60 L): su foto entra en "
          f"${D(tarde['valor_transporte'])}")

    r = client.post(f"{LIQUIDACIONES}/{primero['id']}/anular", headers=h)
    print(f"  anular -> {r.status_code}")
    assert r.status_code == 200, r.text

    fotos_tras_anular = _fotos_del_dia(db_session)
    print(f"  fotos tras anular: {dict(sorted(fotos_tras_anular.items()))}  "
          f"suma=${_suma(fotos_tras_anular)}")

    segundo = _liquidar_flete(client, h)
    liq = client.get(f"{LIQUIDACIONES}/{segundo['id']}", headers=h).json()
    print("  el comprobante regenerado:")
    _ver(liq)
    fotos = _fotos_del_dia(db_session)
    print(f"  fotos: {dict(sorted(fotos.items()))}  suma=${_suma(fotos)}")
    print(f"  a mano: el dia completo con 4 proveedores sigue valiendo ${FIJO}")

    assert len(_renglones(liq)) == 1
    assert D(liq["valor_transporte"]) == FIJO, (
        f"tras anular y regenerar el dia quedo en ${D(liq['valor_transporte'])}"
    )
    assert len(fotos) == 4, f"se perdio una recepcion del reparto: {fotos}"
    assert _suma(fotos) == FIJO
    # Y a Ramiro ya no le puede quedar la foto en cero: el día se volvió a cobrar entero.
    assert all(v > D(0) for v in fotos.values()), (
        f"una recepcion quedo con flete $0 despues de regenerar: {fotos}"
    )
    _revisar_invariante(liq, _suma(fotos))


# ===========================================================================
# ATAQUE 10 — cambiar el fijo a mitad de quincena, y fijo <-> por litro
# ===========================================================================
def test_ataque_cambiar_el_fijo_y_el_modo_a_mitad_de_quincena(
    client, base_datos, db_session
):
    """Subir el fijo, y pasar la ruta de fijo a por litro y al revés. Recalculando.

    A MANO, las tres fotos del papel:
        1) fijo $150.000                      →  $150.000,00
        2) fijo subido a $180.000, RECALCULAR →  $180.000,00
        3) la ruta pasa a POR LITRO $242,76, RECALCULAR
           315,75 L × $242,76 = $76.641,47    →  $ 76.641,47
        4) vuelve a DIA FIJO $180.000, RECALCULAR → $180.000,00
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    litros_dia = D("82.00") + D("137.45") + D("96.30")
    liq_id = _liquidar_flete(client, h)["id"]
    print("\n===== ATAQUE 10: cambiar el fijo y el modo a mitad de quincena =====")

    def poner(valor, modo):
        r = client.put(
            f"/api/v1/transportadores/{esc['alex']['id']}",
            json={"rutas": [
                {"ruta_id": esc["fabrica"]["id"], "valor_transporte": str(valor),
                 "modo_transporte": modo},
                {"ruta_id": esc["napoles"]["id"], "valor_transporte": "242.76",
                 "modo_transporte": "litro"},
            ]},
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert client.post(
            f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h
        ).status_code == 200
        return client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()

    casos = [
        (D("180000"), "dia_fijo", D("180000"), "dia_fijo"),
        (D("242.76"), "litro", centavos(litros_dia * D("242.76")), "litro"),
        (D("180000"), "dia_fijo", D("180000"), "dia_fijo"),
    ]
    for valor, modo, esperado, modo_esperado in casos:
        liq = poner(valor, modo)
        print(f"\n  tarifa {valor} [{modo}]  ->  a mano ${esperado}")
        _ver(liq)
        fotos = _fotos_del_dia(db_session)
        print(f"  fotos: {dict(sorted(fotos.items()))}  suma=${_suma(fotos)}")
        assert D(liq["valor_transporte"]) == esperado, (
            f"con tarifa {valor} [{modo}] el comprobante dice "
            f"${D(liq['valor_transporte'])} y a mano da ${esperado}"
        )
        assert all(r["modo_transporte"] == modo_esperado for r in _renglones(liq))
        assert _suma(fotos) == esperado
        _revisar_invariante(liq, _suma(fotos))


# ===========================================================================
# ATAQUE 11 — un comprobante PAGADO no se mueve por ninguno de esos caminos
# ===========================================================================
def test_ataque_un_comprobante_pagado_no_se_mueve_por_ningun_camino(
    client, base_datos, db_session
):
    """Cinco caminos contra un comprobante de día fijo YA PAGADO. Ninguno lo mueve."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    uno = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    dos = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h).status_code == 200

    antes = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    fotos_antes = _fotos_del_dia(db_session)
    print("\n===== ATAQUE 11: contra un comprobante PAGADO =====")
    print(f"  pagado en ${D(antes['valor_transporte'])} ({antes['estado']})")
    print(f"  fotos: {dict(sorted(fotos_antes.items()))}")

    ataques = [
        ("corregir los litros",
         lambda: client.put(f"{RECEPCIONES}/{uno['id']}",
                            json={"cantidad_litros": "999"}, headers=h)),
        ("apagar un dia",
         lambda: client.put(f"{RECEPCIONES}/{dos['id']}",
                            json={"estado": "inactivo"}, headers=h)),
        ("borrar un dia",
         lambda: client.delete(f"{RECEPCIONES}/{uno['id']}", headers=h)),
        ("recalcular",
         lambda: client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)),
        ("anular",
         lambda: client.post(f"{LIQUIDACIONES}/{liq_id}/anular", headers=h)),
    ]
    for nombre, disparo in ataques:
        r = disparo()
        print(f"  {nombre:<22} -> {r.status_code}")
        assert r.status_code >= 400, f"'{nombre}' paso con {r.status_code} sobre una PAGADA"

    # Se cambia también la tarifa y el modo, y se vuelve a mirar.
    client.put(
        f"/api/v1/transportadores/{esc['alex']['id']}",
        json={"rutas": [{"ruta_id": esc["fabrica"]["id"],
                         "valor_transporte": "999999", "modo_transporte": "litro"}]},
        headers=h,
    )
    despues = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    fotos_despues = _fotos_del_dia(db_session)
    print(f"  despues de todo: ${D(despues['valor_transporte'])} ({despues['estado']})")
    print(f"  fotos: {dict(sorted(fotos_despues.items()))}")
    assert D(despues["valor_transporte"]) == D(antes["valor_transporte"]) == FIJO
    assert despues["estado"] == antes["estado"] == "pagada"
    assert fotos_despues == fotos_antes, "las fotos de un flete pagado se movieron"
    assert [dict(d, id=None) for d in _renglones(despues)] == \
           [dict(d, id=None) for d in _renglones(antes)]


# ===========================================================================
# ATAQUE 12 — un día fijo con LITROS EN CERO en la base
# ===========================================================================
def test_ataque_un_dia_fijo_con_litros_en_cero_no_pierde_el_fijo(
    client, base_datos, db_session
):
    """El camión hizo el viaje y no trajo nada: el día sigue valiendo $150.000.

    La API no deja registrar 0 L (`cantidad_litros > 0`), así que el cero se planta en
    la base —que es como llegaría: una corrección hecha por SQL, una importación—, y
    después se genera el comprobante.

    A MANO: dos recepciones de 0,00 L en la ruta fija →  $150.000 ÷ 2 = $75.000 cada
    una (partes iguales, porque sin litros ninguna puede reclamar más que otra).
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    a = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    b = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")

    db_session.expire_all()
    for fila in db_session.scalars(
        select(RecepcionLeche).where(
            RecepcionLeche.id.in_([uuid.UUID(a["id"]), uuid.UUID(b["id"])])
        )
    ).all():
        fila.cantidad_litros = Decimal("0")
    db_session.flush()
    db_session.commit()

    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    print("\n===== ATAQUE 12: un dia fijo con los litros en CERO =====")
    suma = _ver(liq)
    fotos = _fotos_del_dia(db_session)
    print(f"  fotos: {dict(sorted(fotos.items()))}  suma=${_suma(fotos)}")
    print(f"  a mano: $150.000 / 2 = $75.000 cada una")
    assert D(liq["valor_transporte"]) == FIJO, (
        f"el dia fijo con 0 litros salio en ${D(liq['valor_transporte'])}: el camion "
        f"hizo el viaje y el fijo se cobra por haber ido"
    )
    assert suma == FIJO
    assert _suma(fotos) == FIJO
    assert sorted(fotos.values()) == [D("75000.00"), D("75000.00")], f"{fotos}"
    _revisar_invariante(liq, _suma(fotos))


# ===========================================================================
# ATAQUE 13 — el mismo día con una ruta FIJA y otra POR LITRO, editando
# ===========================================================================
def test_ataque_mezcla_fija_y_por_litro_editando_la_del_litro(
    client, base_datos, db_session
):
    """Corregir los litros del día POR LITRO no le puede tocar el fijo del otro.

    A MANO:
        16/07 A fabrica (fijo)     82,00 + 137,45 = 219,45 L → $150.000,00
        16/07 Napoles   (x litro)  50,00 L × $242,76         → $ 12.138,00
                                                       total = $162.138,00
      tras corregir Nápoles a 123,45 L:
        16/07 Napoles   123,45 × 242,76 = $29.968,72
                                                       total = $179.968,72
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    henri = _recibir(client, h, esc, EL_DIA, "Henri", "50.00")
    liq_id = _liquidar_flete(client, h)["id"]

    print("\n===== ATAQUE 13: mezcla fija + por litro, corrigiendo la del litro =====")
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    _ver(liq)
    assert D(liq["valor_transporte"]) == FIJO + centavos(D("50") * D("242.76"))

    r = client.put(f"{RECEPCIONES}/{henri['id']}",
                   json={"cantidad_litros": "123.45"}, headers=h)
    assert r.status_code == 200, r.text
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print("  tras corregir los litros de Napoles:")
    suma = _ver(liq)
    fotos = _fotos_del_dia(db_session)
    print(f"  fotos: {dict(sorted(fotos.items()))}  suma=${_suma(fotos)}")
    a_mano = FIJO + centavos(D("123.45") * D("242.76"))
    print(f"  a mano: $150.000,00 + $29.968,72 = ${a_mano}")

    fijos = [r for r in _renglones(liq) if r["modo_transporte"] == "dia_fijo"]
    assert len(fijos) == 1
    assert D(fijos[0]["valor"]) == FIJO, (
        f"corregir el dia POR LITRO le movio el dia FIJO a ${D(fijos[0]['valor'])}"
    )
    assert D(liq["valor_transporte"]) == a_mano
    assert suma == a_mano
    assert _suma(fotos) == a_mano
    _revisar_invariante(liq, _suma(fotos))


# ===========================================================================
# ATAQUE 14 — el promedio del encabezado no puede afirmar una tarifa que no existe
# ===========================================================================
def test_ataque_el_promedio_no_miente_con_dias_fijos(client, base_datos, db_session):
    """Con días fijos mezclados el promedio va en cero y la bandera lo explica."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, "2026-07-17", "Henri", "219.45")
    liq = client.get(f"{LIQUIDACIONES}/{_liquidar_flete(client, h)['id']}", headers=h).json()
    print("\n===== ATAQUE 14: el promedio con dias fijos mezclados =====")
    _ver(liq)
    print(f"  precio_promedio={D(liq['precio_promedio'])}  "
          f"tiene_dias_fijos={liq.get('tiene_dias_fijos')}")
    assert liq.get("tiene_dias_fijos") is True
    assert D(liq["precio_promedio"]) == D(0)
    _revisar_invariante(liq)


# ===========================================================================
# ATAQUE 15 — corregirle los litros a LAS CINCO: el fijo x 5
# ===========================================================================
def test_ataque_corregir_las_cinco_recepciones_de_un_dia_fijo_liquidado(
    client, base_datos, db_session
):
    """El error exacto que el diseño dice hacer imposible: $150.000 x 5 = $750.000.

    Cinco proveedores el 16/07 en la ruta fija, comprobante GENERADO (borrador), y
    después se le corrige un litro a cada uno —cinco guardados normales, uno por
    proveedor—. El día tiene que seguir valiendo $150.000.

    Y de paso: ¿lo arregla el botón RECALCULAR, que sí re-precifica?
    """
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    ids = []
    for nombre, litros_, _ in __import__(
        "tests.test_transporte_dia_fijo", fromlist=["CINCO"]
    ).CINCO:
        ids.append(_recibir(client, h, esc, EL_DIA, nombre, str(litros_))["id"])
    liq_id = _liquidar_flete(client, h)["id"]

    print("\n===== ATAQUE 15: corregirle los litros a LAS CINCO =====")
    print(f"  antes: ${D(client.get(f'{LIQUIDACIONES}/{liq_id}', headers=h).json()['valor_transporte'])}")
    for i, rid in enumerate(ids):
        r = client.put(f"{RECEPCIONES}/{rid}",
                       json={"cantidad_litros": str(D("100") + D(i))}, headers=h)
        assert r.status_code == 200, r.text
        liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
        print(f"  tras corregir la {i + 1}a: ${D(liq['valor_transporte'])}")

    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    _ver(liq)
    fotos = _fotos_del_dia(db_session)
    print(f"  fotos: suma=${_suma(fotos)}")
    print(f"  el error que el diseño dice hacer imposible: ${FIJO * 5}")

    # ¿Lo arregla RECALCULAR?
    rec = client.post(f"{LIQUIDACIONES}/{liq_id}/recalcular", headers=h)
    tras = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  RECALCULAR -> {rec.status_code}; queda en ${D(tras['valor_transporte'])}")

    assert D(liq["valor_transporte"]) == FIJO, (
        f"EL FIJO SE MULTIPLICO: cinco correcciones dejaron el dia en "
        f"${D(liq['valor_transporte'])} y el dia completo vale ${FIJO}"
    )


# ===========================================================================
# ATAQUE 16 — la foto fantasma del día apagado, al volver a prenderlo
# ===========================================================================
def test_ataque_prender_otra_vez_un_dia_apagado_de_un_fijo_liquidado(
    client, base_datos, db_session
):
    """Apagar y volver a prender un día de un fijo liquidado deja el papel donde estaba."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    dos = _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    _recibir(client, h, esc, EL_DIA, "Gilberto", "96.30")
    liq_id = _liquidar_flete(client, h)["id"]

    print("\n===== ATAQUE 16: apagar y volver a prender =====")
    for estado in ("inactivo", "activo"):
        r = client.put(f"{RECEPCIONES}/{dos['id']}", json={"estado": estado}, headers=h)
        assert r.status_code == 200, r.text
        liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
        todas = _fotos_del_dia(db_session, solo_activas=False)
        print(f"  estado={estado:<9} comprobante=${D(liq['valor_transporte'])}  "
              f"fotos(todas)=${_suma(todas)}")
        print(f"    {dict(sorted(todas.items()))}")

    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    assert D(liq["valor_transporte"]) == FIJO, (
        f"apagar y volver a prender dejo el dia en ${D(liq['valor_transporte'])}"
    )


def test_ataque_corregir_litros_de_un_dia_fijo_APROBADO(client, base_datos, db_session):
    """Lo mismo sobre un comprobante ya APROBADO: el visto bueno se cae y la cifra sube."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    uno = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    assert client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h).status_code == 200
    antes = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print("\n===== ATAQUE 17: sobre un comprobante APROBADO =====")
    print(f"  aprobado en ${D(antes['valor_transporte'])} ({antes['estado']})")
    r = client.put(f"{RECEPCIONES}/{uno['id']}", json={"cantidad_litros": "200"}, headers=h)
    print(f"  PUT litros=200 -> {r.status_code}")
    assert r.status_code == 200, r.text
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  queda en ${D(liq['valor_transporte'])} ({liq['estado']})")
    _ver(liq)
    assert D(liq["valor_transporte"]) == FIJO, (
        f"el comprobante APROBADO paso de ${FIJO} a ${D(liq['valor_transporte'])}"
    )


def test_ataque_la_cifra_inflada_se_puede_aprobar_y_pagar(client, base_datos, db_session):
    """La consecuencia final: al conductor se le paga de verdad la cifra inflada."""
    h = auth_headers(client, "admin.a")
    esc = _escenario(client, h)
    uno = _recibir(client, h, esc, EL_DIA, "Aurelio", "82.00")
    _recibir(client, h, esc, EL_DIA, "Marleny", "137.45")
    liq_id = _liquidar_flete(client, h)["id"]
    client.put(f"{RECEPCIONES}/{uno['id']}", json={"cantidad_litros": "200"}, headers=h)
    print("\n===== ATAQUE 18: aprobar y pagar la cifra inflada =====")
    a = client.post(f"{LIQUIDACIONES}/{liq_id}/aprobar", headers=h)
    p = client.post(f"{LIQUIDACIONES}/{liq_id}/pagar", headers=h)
    liq = client.get(f"{LIQUIDACIONES}/{liq_id}", headers=h).json()
    print(f"  aprobar -> {a.status_code}   pagar -> {p.status_code}")
    print(f"  se le pago al conductor: ${D(liq['valor_total'])}  ({liq['estado']})")
    print(f"  el dia completo vale:    ${FIJO}")
    print(f"  DE MAS:                  ${D(liq['valor_total']) - FIJO}")
    assert D(liq["valor_total"]) == FIJO, (
        f"se le pagaron ${D(liq['valor_total'])} por un dia que vale ${FIJO}"
    )
